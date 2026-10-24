# Configuring oc-connect

Everything here is optional - the defaults work. Background: [how-it-works.md](how-it-works.md).

**Delegation mode** (asked when the plugin is enabled, change via `/plugin` → oc → configure, or at any time with `/oc:mode`, which wins over the option until `/oc:mode default`):

| Value | Behaviour |
|---|---|
| `ask` (default) | Claude calls `delegate` when a task fits; every delegation shows a confirmation dialog |
| `off` | Refused unless your latest message asked for OpenCode - and then you still confirm in the dialog |
| `mechanical` | Bounded work without asking, larger work after one question; no dialog |
| `auto` | Claude decides; no dialog |

The gate covers the `delegate` tool and `oc start` run through Bash.

**Worker terminal** (`window` or `tab`, same place): `window` (default) opens each worker in its own window that you can place next to Claude; `tab` adds a tab to Claude's window (Terminal.app needs Accessibility permission for that). macOS turns new windows into tabs anyway while the front window is in full screen, when *System Settings → Desktop & Dock → Prefer tabs when opening documents* is "In Full Screen" (the default) - set it to "Never" or leave full screen to get real windows.

**Environment variables** (optional):

| Variable | Default | Purpose |
|---|---|---|
| `OC_AGENT` | `oc-fast` | Default agent |
| `OC_PROFILE` | `isolated` | `inherit` runs workers with your own OpenCode setup instead of the worker profile |
| `OC_OPENCODE` | `opencode` on PATH | Path of the OpenCode binary, e.g. when OpenCode 1 and 2 are both installed |
| `OC_MODEL` | agent's own model | `provider/model` override |
| `OC_WT_ROOT` | `<repo>-worktrees` | Where worktrees go |
| `OC_BASE_REF` | `origin/HEAD` | Where jobs branch from |
| `OC_COPY` | git-ignored `.env`, `.env.local`, … | Files copied into each worktree (space separated) |
| `OC_INSTALL` | `1` | `0` skips dependency installation |
| `OC_TERMINAL` | `$TERM_PROGRAM` | Force a terminal |
| `OC_OPEN` | `window` | `tab` opens workers as tabs (set by the plugin option for the MCP server) |
| `OC_STATE` | `~/.local/state/oc-connect` | Job state and the guard |
| `OC_STATUSLINE_ALIGN` | `left` | `right` aligns the job rows right |
| `OC_STALL_MIN` | `15` | Minutes without activity before the monitor reports a stall |
| `OC_BUDGET` | `2` | Dollar steps at which the monitor warns about a job's cost |
| `OC_IDLE_CLOSE_MIN` | `20` | Minutes a finished job's worker stays open before its tab closes (`0`: only when the Claude session ended) |

**The guard** lives in `~/.local/state/oc-connect/guard.json` (created from `templates/guard.json`). Edit it to tighten or loosen the rules for all repos. A repo's own untracked `opencode.json` is copied into each worktree on top.

**Terminals.** Workers open in tmux (anywhere), WezTerm, kitty, iTerm2 or Terminal.app. Terminal.app needs *System Settings → Privacy & Security → Accessibility → Terminal* for tabs; without it workers open in windows. Tested: Terminal.app and tmux.

**Optional companions.** [oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim): its roles give the worker agents their models. [superpowers](https://github.com/obra/superpowers): stays in your interactive OpenCode; workers run without it.
