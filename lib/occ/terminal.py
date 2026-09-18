"""Open and close terminal tabs for workers.

One job = one tab = one OpenCode process. That is what makes session
trackers such as Vibe Island and Open Island work: their OpenCode plugin runs
inside the server process and finds the terminal by walking up the process
tree to the first TTY. A shared background server without a TTY made every
job show up as one session with no tab to jump to.

Supported: tmux (anywhere), WezTerm, kitty, iTerm2, Terminal.app (macOS).
Fallback when nothing works: print the command for the human to run.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Optional

TITLE_PREFIX = "OC"


def _terminal() -> str:
    return os.environ.get("OC_TERMINAL") or os.environ.get("TERM_PROGRAM") or ""


def _osascript(script: str) -> bool:
    if not shutil.which("osascript"):
        return False
    res = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=20)
    return res.returncode == 0


def _as_string(text: str) -> str:
    """Quote for an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def close_dead_tabs() -> None:
    """Close finished "OC …" tabs in Terminal.app.

    Terminal keeps a tab after its process ends, and the tab keeps its device
    name (ttysNNN). The next tab can get the same name, and Vibe Island, which
    jumps by device name, then landed in the dead tab. Only tabs whose custom
    title starts with "OC" and that run nothing are closed.
    """
    if _terminal() != "Apple_Terminal":
        return
    _osascript(f'''
tell application "Terminal"
  repeat with w in (every window)
    try
      set t to selected tab of w
      if (custom title of t) starts with "{TITLE_PREFIX}" and not (busy of t) then close w
    end try
  end repeat
end tell''')


def open_tab(title: str, command: str, cwd: Optional[str] = None) -> str:
    """Run ``command`` in a new tab of the terminal Claude Code runs in.

    Returns a one-line description of what happened. ``command`` is a shell
    command line; it is run by a login shell so PATH matches the user's.
    """
    if cwd:
        command = f"cd {shlex.quote(cwd)} && {command}"
    # Set the tab title from inside, works in every terminal that honours OSC 0.
    full = f"printf '\\033]0;%s\\007' {shlex.quote(title)}; {command}"
    term = _terminal()
    close_dead_tabs()

    if os.environ.get("TMUX") and shutil.which("tmux"):
        if subprocess.run(["tmux", "new-window", "-n", title, full]).returncode == 0:
            return f"tmux window: {title}"
    if term == "WezTerm" and shutil.which("wezterm"):
        if subprocess.run(["wezterm", "cli", "spawn", "--", "bash", "-lc", full],
                          capture_output=True).returncode == 0:
            return f"WezTerm tab: {title}"
    if term in ("kitty", "xterm-kitty") and shutil.which("kitty"):
        if subprocess.run(["kitty", "@", "launch", "--type=tab", "--tab-title", title, "bash", "-lc", full],
                          capture_output=True).returncode == 0:
            return f"kitty tab: {title}"
    if term == "iTerm.app":
        if _osascript(f'''
tell application "iTerm2"
  tell current window
    set t to (create tab with default profile)
    tell current session of t to write text {_as_string(full)}
  end tell
end tell'''):
            return f"iTerm tab: {title}"
    if term in ("Apple_Terminal", ""):
        # Terminal.app has no scripting verb for tabs; Cmd-T via System Events
        # needs the Accessibility permission for Terminal.
        if _osascript(f'''
tell application "Terminal" to activate
delay 0.3
tell application "System Events" to keystroke "t" using command down
delay 0.6
tell application "Terminal" to do script {_as_string(full)} in selected tab of front window'''):
            return f"Terminal tab: {title}"
        if _osascript(f'tell application "Terminal" to do script {_as_string(full)}'):
            return (f"Terminal WINDOW: {title} (tabs need System Settings > Privacy & Security > "
                    "Accessibility > Terminal)")
    return f"no terminal opened; run it yourself:\n  {full}"
