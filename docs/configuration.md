# Configuring oc-connect

Everything here is optional - the defaults work. Background: [how-it-works.md](how-it-works.md). At the end: [what oc-connect writes](#what-oc-connect-writes) and how to [uninstall](#uninstall) it.

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
| `OC_OPENCODE` | `opencode` on PATH | Path of the OpenCode binary, e.g. a 1.x install next to OpenCode 2 |
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

## What oc-connect writes

On your machine, nowhere else:

| Where | What |
|---|---|
| `~/.local/state/oc-connect/` (`OC_STATE`) | Job state (`jobs/`), the guard (`guard.json`), the worker profile (`worker-home/`), a status cache (`cache/`), the delegation mode (`autonomy`), whether your latest prompt asked for OpenCode (`prompts/`, a `0` or `1` per Claude session - not the prompt), the window ids of worker tabs (`terminal-windows`) and the `oc` shim the status line calls (`bin/`) |
| `<repo>-worktrees/` (`OC_WT_ROOT`) | One git worktree per job; `done` removes it |
| Your repository | A branch `oc/<job>` per job (kept after `done`) and snapshots under `refs/oc/<job>` |
| `~/.claude/settings.json` | Only if you accept the status line in `/oc:setup`: `statusLine.command` runs oc-connect, your previous command is kept after `--base`, a backup is saved as `settings.json.before-oc-connect` |

Not changed: your OpenCode config (`~/.config/opencode` is read, and linked into the worker profile), your git config (the push block lives only in the worker's environment), your prompts (not stored).

Network: oc-connect itself talks only to the OpenCode server it starts on `127.0.0.1`. Starting a job runs `git fetch origin` and, if the worktree has a lockfile, a frozen dependency install (`npm ci`, `pnpm`/`yarn`/`bun install`). The worker sends the task and the code it reads to the model provider you set up in OpenCode, as OpenCode does when you use it yourself. Git-ignored `.env` files are copied into the worktree (`OC_COPY`), so the worker can read them - set `OC_COPY` to an empty value if it should not.

## Uninstall

1. Finish or remove open jobs, in Claude Code while the plugin is still installed: `/oc:status`, then `!oc done <job>` (`--force` discards unpushed work). A job left open keeps its worker running.
2. Remove the plugin:
   ```bash
   claude plugin uninstall oc@oc-connect
   claude plugin marketplace remove oc-connect
   ```
3. If you added the status line: in `~/.claude/settings.json`, set `statusLine.command` back to the command after `--base`, or remove `statusLine` if there was none.
4. Remove the state: `rm -rf ~/.local/state/oc-connect` (this deletes your edits to `guard.json` too).
5. Per repository, if you like: delete the snapshots with `git for-each-ref --format='delete %(refname)' refs/oc | git update-ref --stdin`, the branches `oc/*` you no longer need with `git branch -D`, and the empty `<repo>-worktrees/` folder.

OpenCode keeps the workers' sessions in its own data, like any other session.
