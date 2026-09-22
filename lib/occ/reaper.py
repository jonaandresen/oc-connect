"""Reaper: closes a worker's tab once Claude Code no longer uses it.

Every worker tab starts one reaper in the background (see
``ops._worker_command``). It polls the job and stops the worker when

  * the Claude session that owns the job has ended and no other session took
    the job over within ``OWNER_GRACE_S`` (a restart of Claude Code takes a few
    seconds, and the next session's monitor adopts the job), or
  * the job sat idle (finished or failed, nothing pending) for
    ``OC_IDLE_CLOSE_MIN`` minutes, although its session is still alive.

A worker that is busy or waits for an answer is never stopped: the worker
finishes its turn first. Stopping keeps worktree and session; ``open`` or
``send`` start the worker again in the same session.

The reaper leaves the tab's terminal session (``setsid``): otherwise Terminal.app
counts the tab as busy and ``close_dead_tabs`` would keep it open.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from . import core, ops

POLL_S = 15
OWNER_GRACE_S = 120
IDLE_STATES = ("done", "failed")


def idle_close_s() -> float:
    """Seconds of idleness before the worker is stopped; 0 disables it.

    Read where the tab command is built (Claude's environment) and passed to
    ``oc reap``: the tab's login shell does not see Claude's variables.
    """
    try:
        return max(0.0, float(os.environ.get("OC_IDLE_CLOSE_MIN", "20"))) * 60
    except ValueError:
        return 20 * 60


class Clock:
    """What the reaper remembers between polls."""

    def __init__(self) -> None:
        self.idle_since: Optional[float] = None
        self.orphan_since: Optional[float] = None


def decide(c: Clock, st: Dict[str, Any], now: float, idle_limit: float) -> Optional[str]:
    """Reason to stop the worker now, or None. Updates the clock."""
    idle = st.get("state") in IDLE_STATES
    c.idle_since = (c.idle_since or now) if idle else None
    orphan = bool(st.get("owner")) and not st.get("owner_alive")
    c.orphan_since = (c.orphan_since or now) if orphan else None
    if not idle:
        return None
    if orphan and now - c.orphan_since >= OWNER_GRACE_S:
        return "the Claude session that started it has ended"
    if idle_limit and now - c.idle_since >= idle_limit:
        return f"idle for {idle_limit / 60:.0f} min"
    return None


def main(slug: str, pid: int, limit: float) -> None:
    try:
        os.setsid()
    except OSError:
        pass
    c = Clock()
    while core.pid_alive(pid):
        time.sleep(POLL_S)
        try:
            st = ops.status(slug)
        except Exception:  # job removed (`done`) or state unreadable
            return
        if st.get("state") == "stopped":
            return
        reason = decide(c, st, time.time(), limit)
        if reason:
            ops._set(slug, closed_reason=reason)
            ops.close(slug)
            return
