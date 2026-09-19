"""Job rows for Claude Code's status line.

``main`` renders one row per active job. Claude Code allows only ONE
``statusLine`` command, so ``--base <cmd>`` runs the user's existing command
with the same stdin and puts the job rows below its output.

There is deliberately no agent-panel row: only subagents get one, a subagent
is a model call, and the Haiku watcher tried for it misreported the job state
(it summarised despite being told to pass output through). The monitor
reports every event without a model.

It runs on every redraw and must never hang or fail loudly: short timeouts, a
small cache, and no output at all on errors.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import core, ops

CACHE = os.path.join(core.STATE_DIR, ".statusline-cache.json")
FRESH_S = 4
C = {"cy": "\033[36m", "ye": "\033[33m", "gr": "\033[32m", "red": "\033[31m", "dim": "\033[2m",
     "b": "\033[1m", "r": "\033[0m"}
ANSI = re.compile(r"\033\[[0-9;]*m")


def _cached_status() -> List[Dict[str, Any]]:
    """Job status shared by both status lines, recomputed at most every FRESH_S."""
    try:
        if time.time() - os.path.getmtime(CACHE) < FRESH_S:
            with open(CACHE) as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    rows = ops.status_all(detail=True)
    try:
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(rows, fh, default=str)
        os.replace(tmp, CACHE)
    except OSError:
        pass
    return rows


def bar(done: int, total: int, width: int = 10) -> str:
    if not total:
        return ""
    full = round(width * done / total)
    return f"{C['cy']}{'█' * full}{C['dim']}{'░' * (width - full)}{C['r']} {done}/{total}"


def _diff(st: Dict[str, Any]) -> str:
    d = st.get("diff") or {}
    if not d.get("files"):
        return ""
    return f"{d['files']} files {C['gr']}+{d['plus']}{C['r']} {C['red']}-{d['minus']}{C['r']}"


def _age(st: Dict[str, Any]) -> str:
    m = core.minutes_since(st.get("created"))
    return f"{C['dim']}{m:.0f}m{C['r']}" if m is not None else ""


def _short_title(st: Dict[str, Any]) -> str:
    title = st.get("title") or st["slug"]
    parts = title.split(" ", 1)
    return parts[1] if parts[0].startswith("#") and len(parts) > 1 else title


def row(st: Dict[str, Any], name_width: int = 17, title_width: int = 32) -> Optional[str]:
    """One status row, or None for jobs that need no attention."""
    head = f"{st['slug'][:name_width]:<{name_width}} {_short_title(st)[:title_width]:<{title_width}}"
    state = st["state"]
    if state == "waiting" and st.get("pending"):
        p = st["pending"][0]
        what = ("asks: " + p["text"]) if p["kind"] == "question" else f"permission: {p['text']}"
        return f"{C['ye']}? {C['r']}{C['b']}{head}{C['r']} {C['ye']}{what[:70]}{C['r']}"
    if state in ("running", "starting"):
        parts = [bar(st.get("todo_done", 0), st.get("todo_total", 0)), _diff(st),
                 f"{C['dim']}{st.get('agent') or ''}{C['r']}", _age(st)]
        return f"{C['cy']}▶ {C['r']}{C['b']}{head}{C['r']} " + "  ".join(p for p in parts if p)
    if state == "failed":
        return f"{C['red']}✗ {C['r']}{C['b']}{head}{C['r']} {C['red']}{str(st.get('error') or 'failed')[:70]}{C['r']}"
    if state == "done" and not st.get("pr"):
        return f"{C['gr']}✓ {C['r']}{C['b']}{head}{C['r']} {C['gr']}ready for review{C['r']}  {_diff(st)}"
    return None


def _terminal_width() -> Optional[int]:
    """Columns of the terminal Claude Code runs in.

    Claude Code gives the main status line no width, and this process has no
    TTY. Walk up the parent chain to the first process with one and ask it.
    """
    pid = os.getppid()
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "tty=,ppid=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=0.5).stdout.split()
        except Exception:
            return None
        if len(out) < 2:
            return None
        tty, ppid = out[0], out[1]
        if tty not in ("??", "?"):
            try:
                size = subprocess.run(["stty", "-f", f"/dev/{tty}", "size"], capture_output=True,
                                      text=True, timeout=0.5).stdout.split()
                return int(size[1])
            except Exception:
                return None
        pid = int(ppid)
    return None


def _align(rows: List[str]) -> List[str]:
    if os.environ.get("OC_STATUSLINE_ALIGN", "left") != "right":
        return rows
    width = _terminal_width()
    if not width:
        return rows
    margin = int(os.environ.get("OC_STATUSLINE_MARGIN", "6"))
    return [" " * max(0, width - margin - len(ANSI.sub("", r))) + r for r in rows]


def main(base_cmd: Optional[str] = None) -> None:
    stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
    base_out = ""
    if base_cmd:
        # shell=True on purpose: base_cmd comes from the user's own settings.json,
        # where Claude Code runs statusLine commands through a shell anyway.
        try:
            base_out = subprocess.run(base_cmd, shell=True, input=stdin, capture_output=True,
                                      text=True, timeout=5).stdout.rstrip("\n")
        except Exception:
            base_out = ""
    rows: List[str] = []
    try:
        rows = _align([r for r in (row(st) for st in _cached_status()) if r])
    except Exception:
        rows = []
    sys.stdout.write("\n".join([x for x in [base_out] + rows if x]))
