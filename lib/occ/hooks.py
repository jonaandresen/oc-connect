"""Claude Code hooks and the status line installer.

``session_start``: refreshes the stable shim (see ``install_shim``) and tells a
new session about jobs that are still open - otherwise it would work blind
next to running workers.

``pre_bash``: blocks ``git commit`` inside a job's worktree until Claude has
recorded its review for exactly that state (``ops.review``). The review is the
point of the whole design; this makes skipping it impossible by accident.

``prompt_submit``: when the user says "do this with OpenCode" (German or
English), adds a note so Claude delegates instead of implementing. Pure regex,
no network: it runs on every prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import core, ops

SHIM_DIR = os.path.join(core.STATE_DIR, "bin")
SHIM = os.path.join(SHIM_DIR, "oc")
SETTINGS = os.path.expanduser("~/.claude/settings.json")

# "mach das mit opencode", "gib das an OpenCode", "lass opencode das machen",
# "do this with opencode", "delegate it to opencode", "use opencode for this".
# "an opencode connect/plugin" is about the tool itself, not a request.
_TOOL = r"open\s?code(?![\s-]*(connect|plugin|config|server|setup|slim|\.json))"
DELEGATE_PATTERNS = [
    rf"\b(mit|über|ueber|via|per|an|durch)\s+(dem\s+)?{_TOOL}",
    rf"\blass\w*\s+(das\s+|es\s+|den\s+rest\s+)?{_TOOL}",
    rf"\b{_TOOL}\s+(soll|sollte|kann|darf|mach|übernimm|uebernimm)",
    rf"\b(with|via|using|to|through)\s+{_TOOL}\b",
    rf"\b(use|let|have)\s+{_TOOL}\b",
]
_DELEGATE_RE = re.compile("|".join(DELEGATE_PATTERNS), re.I)


def wants_delegation(prompt: str) -> bool:
    return bool(_DELEGATE_RE.search(prompt or ""))


def install_shim() -> str:
    """Stable path to the current plugin version's ``oc``.

    Claude Code installs each plugin version into its own cache directory, so a
    path written into settings.json would break with the next update. The
    statusLine points to this shim; every SessionStart rewrites it.
    """
    os.makedirs(SHIM_DIR, exist_ok=True)
    target = os.path.join(ops.PLUGIN_ROOT, "bin", "oc")
    body = f'#!/bin/sh\nexec "{target}" "$@"\n'
    try:
        with open(SHIM) as fh:
            if fh.read() == body:
                return SHIM
    except OSError:
        pass
    tmp = SHIM + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(body)
    os.chmod(tmp, 0o755)
    os.replace(tmp, SHIM)
    return SHIM


def install_statusline(dry_run: bool = False) -> str:
    """Put the job rows under the user's existing status line.

    Plugins cannot set the main ``statusLine`` themselves, so this edits
    ``~/.claude/settings.json`` once (backup next to it). An existing command
    is kept and runs above the job rows.
    """
    shim = install_shim()
    try:
        with open(SETTINGS) as fh:
            settings = json.load(fh)
    except FileNotFoundError:
        settings = {}
    current = (settings.get("statusLine") or {}).get("command", "")
    if "statusline" in current and (shim in current or "oc-connect" in current):
        return f"already installed: {current}"
    # Unwrap the wrapper of the old bash version, keep its base command.
    m = re.match(r"OC_STATUSLINE_BASE=(\S+)\s+\S*oc-statusline-wrap", current)
    base = m.group(1) if m else current
    cmd = f"{shim} statusline" + (f" --base {json.dumps(base)}" if base else "")
    if dry_run:
        return f"would set statusLine.command to: {cmd}"
    if os.path.exists(SETTINGS):
        with open(SETTINGS) as src, open(SETTINGS + ".before-oc-connect", "w") as dst:
            dst.write(src.read())
    line = dict(settings.get("statusLine") or {"type": "command"})
    line["command"] = cmd
    line.setdefault("refreshInterval", 5)
    settings["statusLine"] = line
    with open(SETTINGS + ".tmp", "w") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    os.replace(SETTINGS + ".tmp", SETTINGS)
    return f"statusLine.command = {cmd}\n(backup: {SETTINGS}.before-oc-connect)"


def _emit(event: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}))


_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+commit\b")
_CD_RE = re.compile(r"(?:^|[;&|]\s*)cd\s+(\"[^\"]+\"|'[^']+'|\S+)")
_GIT_C_RE = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)")


def commit_dirs(command: str, cwd: str) -> list:
    """Directories a Bash command would commit in (empty: no commit)."""
    if not _COMMIT_RE.search(command or ""):
        return []
    dirs = [m.strip("'\"") for m in _GIT_C_RE.findall(command) + _CD_RE.findall(command)]
    dirs = [os.path.expanduser(d) if os.path.isabs(os.path.expanduser(d)) else os.path.join(cwd, d) for d in dirs]
    return dirs or [cwd]


def pre_bash() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if "commit" not in command:
        return
    for d in commit_dirs(command, data.get("cwd") or os.getcwd()):
        try:
            reason = ops.review_block_reason(d)
        except Exception:
            reason = None
        if reason:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "deny",
                                                     "permissionDecisionReason": reason}}))
            return


def session_start() -> None:
    try:
        install_shim()
        core.migrate_legacy()
        rows = ops.status_all()
    except Exception:
        return
    me = core.claude_pid()
    active = [r for r in rows if r["state"] != "stopped" or not r.get("pr")]
    if not active:
        return
    lines = []
    for st in active:
        extra = "; ".join(p["text"][:80] for p in st.get("pending", []))
        other = " [owned by another live Claude session - leave it alone]" \
            if st.get("owner_alive") and st.get("owner") != me else ""
        lines.append(f"- {st['slug']}: {st['state']} - {st['title']}" + (f" ({extra})" if extra else "")
                     + (f" PR {st['pr']}" if st.get("pr") else "") + other)
    _emit("SessionStart", "Open OpenCode jobs (oc-connect):\n" + "\n".join(lines)
          + "\nReview every finished job and record it with the oc-connect `review` tool before committing.")


def prompt_submit() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not wants_delegation(data.get("prompt", "")):
        return
    _emit("UserPromptSubmit",
          "oc-connect: The user appears to ask for this task to be done WITH OPENCODE. If so, do not "
          "implement it yourself: research as needed, write a precise task and use the oc-connect "
          "`delegate` tool (skill oc:delegate has the template), then review the result. If the message "
          "is only ABOUT OpenCode (e.g. discussing the tool), ignore this note.")
