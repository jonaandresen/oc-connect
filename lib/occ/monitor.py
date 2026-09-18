"""Monitor: one stdout line per job event, delivered to Claude as a notification.

Claude Code starts this for the lifetime of a session (``monitors/monitors.json``)
and hands every line to Claude. That removes polling: Claude hears about a
question, a finished job or a dead worker the moment it happens.

Only TRANSITIONS are printed. The first scan after start records the current
state silently, except for jobs that already wait for an answer - those are
reported once, because a new session would otherwise never learn about them.
Every Claude session runs its own monitor, so two open sessions both hear it.
"""
from __future__ import annotations

import sys
import time
from typing import Dict, Tuple

from . import core, ops

POLL_S = 5


def _key(st: Dict) -> Tuple:
    pend = tuple(p["id"] for p in st.get("pending", []))
    return (st["state"], pend)


def _line(st: Dict, prev_state: str) -> str:
    slug, state = st["slug"], st["state"]
    title = st.get("title") or slug
    if state == "waiting":
        items = []
        for p in st["pending"]:
            if p["kind"] == "question":
                opts = f" [options: {' | '.join(p['options'])}]" if p["options"] else ""
                items.append(f"asks: {p['text']}{opts}")
            else:
                items.append(f"requests permission: {p['text']} {' '.join(p['patterns'])}")
        return (f"oc job {slug} ({title}) {'; '.join(items)} -> answer with the oc-connect answer/permit tool if code or "
                f"task settle it, otherwise ask the user")
    if state == "done":
        d = core.diffstat(st["dir"]) if st.get("dir") else {"files": 0, "plus": 0, "minus": 0}
        return (f"oc job {slug} ({title}) finished: {d['files']} files +{d['plus']} -{d['minus']} "
                f"-> read the oc-connect report, then review the diff before anything is committed")
    if state == "stopped" and prev_state in ("running", "waiting", "starting"):
        return f"oc job {slug} ({title}) worker process ended while {prev_state} -> the oc-connect open tool restarts it"
    if state == "running" and prev_state == "waiting":
        return f"oc job {slug} ({title}) continues after the answer"
    return ""


def main() -> None:
    core.migrate_legacy()
    seen: Dict[str, Tuple] = {}
    first = True
    while True:
        try:
            current = {st["slug"]: st for st in ops.status_all()}
        except Exception:
            current = {}
        for slug, st in current.items():
            key = _key(st)
            prev = seen.get(slug)
            if prev == key:
                continue
            if first or prev is None:
                # New to us: announce only what needs action now.
                # A job started by this session is known to Claude already.
                line = _line(st, "") if st["state"] == "waiting" else ""
            else:
                line = _line(st, prev[0])
            if line:
                print(line, flush=True)
            seen[slug] = key
        for slug in list(seen):
            if slug not in current:
                del seen[slug]
        first = False
        try:
            time.sleep(POLL_S)
        except KeyboardInterrupt:
            sys.exit(0)
