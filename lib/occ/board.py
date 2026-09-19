"""Live board of all jobs, for a terminal tab.

Read-only on purpose: answers and corrections go through Claude
(the MCP tools ``answer`` and ``send``) so that Claude knows what was decided. Keys:
``q`` quits, ``1``-``9`` opens that job's tab.
"""
from __future__ import annotations

import os
import select
import signal
import sys
import termios
import time
import tty

from . import core, ops, terminal
from .statusline import C, bar

ICON = {"running": ("▶", C["cy"]), "waiting": ("?", C["ye"]), "done": ("✓", C["gr"]), "failed": ("✗", C["red"]),
        "stopped": ("■", C["dim"]), "starting": ("…", C["cy"])}


def draw() -> list:
    rows = ops.status_all(detail=True)
    out = [f"{C['b']}oc-connect{C['r']}  {C['dim']}OpenCode jobs · {time.strftime('%H:%M:%S')}{C['r']}", ""]
    for i, st in enumerate(rows, 1):
        icon, color = ICON.get(st["state"], ("·", C["dim"]))
        age = core.minutes_since(st.get("created"))
        d = st.get("diff") or {}
        diff = f"{d.get('files', 0)} files +{d.get('plus', 0)} -{d.get('minus', 0)}" if d.get("files") else ""
        cost = f"${st.get('cost', 0):.2f}" if st.get("cost") else ""
        pr = f"PR {st['pr']}" if st.get("pr") else ""
        out.append(f" {C['b']}{i}{C['r']} {color}{icon} {st['state']:<8}{C['r']} {st['slug'][:18]:<18} "
                   f"{(st.get('title') or '')[:44]:<44} {C['dim']}{(f'{age:.0f}m' if age is not None else ''):>5}{C['r']}")
        details = "  ".join(x for x in (bar(st.get("todo_done", 0), st.get("todo_total", 0)), diff,
                                         str(st.get("agent") or ""), cost, pr) if x)
        if details:
            out.append(f"      {details}")
        for p in st.get("pending", []):
            out.append(f"      {C['ye']}{p['kind']}: {p['text'][:90]}{C['r']}")
        if st["state"] == "failed" and st.get("error"):
            out.append(f"      {C['red']}{st['error'][:100]}{C['r']}")
        if st.get("activity") and st["state"] == "running":
            out.append(f"      {C['dim']}{st['activity']}{C['r']}")
    if not rows:
        out.append(f" {C['dim']}no jobs{C['r']}")
    out += ["", f"{C['dim']}q quit · 1-9 open job tab · answers and corrections go through Claude{C['r']}"]
    sys.stdout.write("\033[H\033[2J" + "\n".join(out) + "\n")
    sys.stdout.flush()
    return rows


def main() -> None:
    sys.stdout.write("\033]0;OC Board\007")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            rows = draw()
            ready, _, _ = select.select([sys.stdin], [], [], 3)
            if not ready:
                continue
            key = sys.stdin.read(1)
            if key in ("q", "Q"):
                break
            if key.isdigit() and 1 <= int(key) <= len(rows):
                try:
                    ops.open_(rows[int(key) - 1]["slug"])
                except core.OcError:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")


def open_tab() -> str:
    oc = os.path.join(ops.PLUGIN_ROOT, "bin", "oc")
    return terminal.open_tab("OC Board", f"exec {oc} board")
