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


def _terminal_app_tab(command: str) -> str:
    """New tab in Terminal.app, falling back to a new window. Returns tab/window/"".

    Two facts, both measured, shape this:

    * Terminal's tabs are native macOS window tabs, so AppleScript lists EVERY
      TAB AS ITS OWN WINDOW with one tab. A new tab shows up as a new window id.
    * Opening the tab can fail silently (keystroke dropped, other app in
      front). The old code then wrote into "the selected tab of the front
      window" - an EXISTING tab; once that was the tab running Claude Code, and
      the worker command landed in Claude's prompt.

    So: remember all window ids, open a tab via the Shell menu (menu 3, item 2,
    first profile - by position, so it works in every UI language), and write
    only into a window id that did not exist before and runs nothing.
    Otherwise ``do script`` without a target, which always creates a window.
    """
    cmd = _as_string(command)
    if not shutil.which("osascript"):
        return ""
    out = subprocess.run(["osascript", "-"], capture_output=True, text=True, timeout=30, input=f'''
tell application "Terminal"
  activate
  set known to id of every window
end tell
try
  tell application "System Events" to tell process "Terminal"
    set frontmost to true
    click menu item 1 of menu 1 of menu item 2 of menu 3 of menu bar 1
  end tell
end try
repeat 20 times
  delay 0.1
  tell application "Terminal"
    repeat with w in windows
      if known does not contain (id of w) then
        set t to selected tab of w
        if not (busy of t) then
          do script {cmd} in t
          return "tab"
        end if
      end if
    end repeat
  end tell
end repeat
tell application "Terminal" to do script {cmd}
return "window"''')
    return out.stdout.strip() if out.returncode == 0 else ""


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
        res = _terminal_app_tab(full)
        if res == "tab":
            return f"Terminal tab: {title}"
        if res == "window":
            return (f"Terminal WINDOW: {title} (a new tab could not be verified; tabs need System Settings > "
                    "Privacy & Security > Accessibility > Terminal)")
    return f"no terminal opened; run it yourself:\n  {full}"
