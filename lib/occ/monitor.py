"""Monitor: one stdout line per job event, delivered to Claude as a notification.

Claude Code starts this for the lifetime of a session (``monitors/monitors.json``)
and hands every line to Claude, so Claude never has to poll.

Ownership: every job belongs to the Claude session that started it (the PID of
its ``claude`` process). Each session runs its own monitor, and only the owner's
monitor speaks - otherwise two sessions would answer the same worker question.
A job whose owner is gone is adopted by the next monitor that sees it.

Events:
  * started / start failed          (``delegate`` returns before the worker runs)
  * question or permission request  (with the id to pass to ``answer``)
  * answered outside Claude         (the human answered in OpenCode directly)
  * finished / failed               (failed = last turn ended with an error)
  * stalled                         (busy, but no activity for OC_STALL_MIN minutes)
  * cost above OC_BUDGET            (and again at every further multiple)
  * worker process ended while working
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

from . import core, ops

POLL_S = 5
STALL_MIN = float(os.environ.get("OC_STALL_MIN", "15"))
BUDGET = float(os.environ.get("OC_BUDGET", "2"))
COST_EVERY_S = 60


def _name(st: Dict[str, Any]) -> str:
    return f"oc job {st['slug']} ({st.get('title') or st['slug']})"


def _pending_text(st: Dict[str, Any]) -> str:
    parts = []
    for p in st["pending"]:
        if p["kind"] == "question":
            opts = f" [options: {' | '.join(p['options'])}]" if p["options"] else ""
            parts.append(f"asks [{p['id']}]: {p['text']}{opts}")
        else:
            parts.append(f"requests permission [{p['id']}]: {p['text']}")
    return "; ".join(parts)


ASK = ("-> answer with `answer`/`permit` (pass the id) if code or task settle it, otherwise ask the user "
       "(push notification if they may be away)")


class JobWatch:
    """What the monitor remembers about one job between polls."""

    def __init__(self) -> None:
        self.state: Optional[str] = None
        self.pending_ids: tuple = ()
        self.stall_reported = False
        self.budget_level = 0
        self.cost_checked = 0.0


def transition(w: JobWatch, st: Dict[str, Any], job: Dict[str, Any], first: bool) -> List[str]:
    """Lines to print for this poll, given the remembered and the new state."""
    out = []
    state, prev = st["state"], w.state
    pend = tuple(p["id"] for p in st.get("pending", []))

    if first:
        # Silent start - except for what needs action now.
        if state == "waiting":
            out.append(f"{_name(st)} {_pending_text(st)} {ASK}")
        elif state == "failed":
            out.append(f"{_name(st)} FAILED: {st.get('error')} -> `report`, then decide: `send` to retry or stop")
    elif state == "waiting" and pend != w.pending_ids:
        out.append(f"{_name(st)} {_pending_text(st)} {ASK}")
    elif state != prev:
        if prev == "waiting" and w.pending_ids and not set(w.pending_ids) & set(job.get("answered", [])):
            out.append(f"{_name(st)}: the question was answered directly in OpenCode, not by Claude - "
                       "read the session before assuming what was decided")
        if state == "running" and prev == "starting":
            out.append(f"{_name(st)} is running (agent {st.get('agent')}) - browser: {ops.browser_url(job)}")
        elif state == "done":
            d = core.diffstat(st["dir"]) if st.get("dir") else {"files": 0, "plus": 0, "minus": 0}
            out.append(f"{_name(st)} finished: {d['files']} files +{d['plus']} -{d['minus']} -> `report`, "
                       "then review the diff; record it with `review` before committing")
        elif state == "failed":
            out.append(f"{_name(st)} FAILED: {st.get('error')} -> `report`, then decide: `send` to retry or stop")
        elif state == "stopped" and prev in ("running", "waiting", "starting"):
            out.append(f"{_name(st)}: worker process ended while {prev} -> `open` restarts it in the same session")

    if state == "running":
        mins = core.minutes_since(st.get("updated"))
        if mins is not None and mins >= STALL_MIN and not w.stall_reported:
            out.append(f"{_name(st)} looks stalled: busy but no activity for {mins:.0f} min -> `report` or `open` to look; "
                       "`send` a nudge or stop it")
            w.stall_reported = True
        elif mins is not None and mins < STALL_MIN:
            w.stall_reported = False
    if st.get("cost") is not None and BUDGET > 0:
        level = int(float(st["cost"]) // BUDGET)
        if level > w.budget_level and not first:
            out.append(f"{_name(st)} has cost ${float(st['cost']):.2f} (budget step ${BUDGET:.2f}) - "
                       "check whether it is going in circles")
        w.budget_level = max(w.budget_level, level)

    w.state, w.pending_ids = state, pend
    return out


def main() -> None:
    core.migrate_legacy()
    me = core.claude_pid()
    watches: Dict[str, JobWatch] = {}
    first_round = True
    while True:
        try:
            jobs = {j["slug"]: j for j in core.load_jobs()}
        except Exception:
            jobs = {}
        for slug, job in jobs.items():
            owner = job.get("owner")
            if owner and owner != me and core.pid_alive(owner):
                continue  # another live Claude session owns it
            if me and owner != me:
                ops._set(slug, owner=me)  # adopt an orphan or an old job without owner
            new = slug not in watches
            w = watches.setdefault(slug, JobWatch())
            try:
                want_cost = time.time() - w.cost_checked > COST_EVERY_S
                st = ops.status(slug, detail=want_cost)
                if want_cost:
                    w.cost_checked = time.time()
            except Exception:
                continue
            # A job that appears while we run was just started: follow it from here.
            for line in transition(w, st, job, first=new and first_round):
                print(line, flush=True)
        for slug in list(watches):
            if slug not in jobs:
                del watches[slug]
        first_round = False
        try:
            time.sleep(POLL_S)
        except KeyboardInterrupt:
            sys.exit(0)
