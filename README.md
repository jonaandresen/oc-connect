# oc-connect

**Claude Code as the lead, OpenCode as the workers.**

Claude Code plans, writes a precise task and reviews. [OpenCode](https://opencode.ai) implements it on a cheaper model, in its own git worktree and its own terminal tab you can watch live. Claude answers the worker's questions (and passes on only the ones that are really yours), then reads the full diff, runs the checks and does a defeat test before anything is committed.

```
you ──► Claude Code (lead) ──task──► OpenCode worker (tab "OC 276", port 4101, worktree ../repo-worktrees/276)
            ▲  reviews diff, runs checks,          │
            │  defeat test, finds gaps             │ question? done? died?
            └──────── monitor notification ◄───────┘
```

## What you get

| | |
|---|---|
| **Claude always knows** | An MCP server gives every session the tools (`delegate`, `status`, `answer`, …) and the rules for when delegating is worth it. No need to remember a command. |
| **You decide** | Default: every delegation needs your OK in Claude Code's confirmation dialog, which shows title, agent and task. A hook enforces it, not the model's good behaviour - it also works under `--dangerously-skip-permissions`. `/oc:mode` switches between `off`, `ask`, `mechanical` and `auto` at any time. Money, auth, security and open product decisions are never delegated. |
| **Live in Claude Code** | Rows under your status line: `▶ 276  Orders after payment  ███████░░░ 14/18  15 files +169 -65  oc-fast  26m`, `? 301 asks: …`, `✗ 288 APIError: …`. |
| **Push, not poll** | A background monitor tells Claude the moment a worker runs, asks something (with a question id), finishes, fails, stalls, gets expensive or dies. Each job belongs to the Claude session that started it, so two open sessions never answer the same question. |
| **One job = one worktree = one tab = one process** | Session trackers such as **Vibe Island** and **Open Island** show every job separately, and a click jumps to its tab (verified with Vibe Island). Closing the tab stops the worker, and the tab closes itself once Claude no longer uses the job (idle, or its Claude session ended). |
| **Two guard layers** | OpenCode's permission guard blocks `git commit/push/merge/stash`, `docker`, `ssh`, `gh pr merge`, `npm install` and more. Because it matches command strings (`bash -c "git push"` slips through - measured), the worker's environment also rewrites every git push URL to nowhere and points docker at a dead socket. `oc selftest` proves both in a throw-away repo, judged by what reached the remote. This stops accidents, it is not a sandbox. |
| **Nothing gets lost** | The worker's result lies in the worktree uncommitted. Before `report`, `send`, `review` and `done`, oc-connect snapshots it into `refs/oc/<slug>`. A correction that makes things worse, a wrong `git checkout`, even `done --force` - `restore` puts the state back, and the state before the restore is snapshotted too. |
| **Review is enforced** | Claude records its review (checks run, defeat test, known gaps) with `review`. A hook blocks `git commit` in a job's worktree until that review matches exactly the current files. |
| **Own worker profile** | Workers run with an OpenCode config of their own: none of your OpenCode plugins (oh-my-opencode, superpowers, …), but your logins, MCP servers and session-tracker plugins. Three agents - `oc-fast`, `oc-strong`, `oc-ui` - take their models from your oh-my-opencode roles if you have it, and share a worker prompt written for this workflow. Your interactive OpenCode stays untouched. |

## Requirements

- Claude Code ≥ 2.1.271
- [OpenCode](https://opencode.ai) ≥ 1.18 with a configured provider
- `python3` ≥ 3.9 (ships with macOS), `git`
- A terminal that can open tabs: tmux (anywhere), WezTerm, kitty, iTerm2 or Terminal.app. **Terminal.app** needs *System Settings → Privacy & Security → Accessibility → Terminal*; without it workers open in windows.
- Optional: [oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim), [superpowers](https://github.com/obra/superpowers)

## Install

```bash
claude plugin marketplace add jonaandresen/oc-connect
claude plugin install oc@oc-connect
```

Restart Claude Code, then run **`/oc:setup`** once. It checks OpenCode, proves the guard, and offers to put the job rows under your existing status line (plugins cannot set the main status line themselves; your current one is kept and runs above the rows).

## Use

Just talk:

- "Do #276 with OpenCode." / "Gib das an OpenCode."
- "Write the missing tests for the cart" - Claude may propose OpenCode for this; you confirm or decline in the dialog.

Commands:

| | |
|---|---|
| `/oc:delegate <#issue or task>` | Delegate explicitly |
| `/oc:status` | All jobs in the chat |
| `/oc:board` | Live board in a new tab (`1`-`9` opens a job, `q` quits) |
| `/oc:show <job>` | Open a job's tab (restarts a stopped worker in the same session) |
| `/oc:stop <job>` | Stop a worker; worktree and session stay |
| `/oc:mode [off\|ask\|mechanical\|auto\|default]` | Show or switch when Claude may delegate |
| `/oc:setup` | Check, selftest, status line |

The CLI does the same from any shell: `oc --help` (inside Claude Code `oc` is on the Bash PATH).

### The lifecycle of a job

1. Claude researches and writes the task (files with lines, required behaviour, tests, what must not change, check commands).
2. `delegate`: worktree from `origin/HEAD` on branch `oc/<slug>`, git-ignored `.env` files copied, and back to you right away. In the background: session created, a new tab opens **directly in that session**, installs dependencies from lockfiles (`npm ci`, `pnpm`/`yarn`/`bun` frozen), starts the worker, task sent.
3. The monitor reports: running (with a browser link), questions, finished, failed, stalled, over budget, stopped. Claude answers what code and task settle and asks you the rest.
4. Finished: Claude reads the report and the full diff, runs all checks, does a defeat test, hunts for untested cases, sends corrections into the same session. Every one of these steps snapshots the worktree first; `oc snapshots <job>` lists them, `oc restore <job> [commit]` puts one back.
5. Claude records the review, commits, opens the PR, records it (`set_pr`) and closes the worker. After merge, `done` removes the worktree - it refuses while unpushed work exists.

## Configuration

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

## Good to know

- **How the worker profile works.** OpenCode merges `OPENCODE_CONFIG` into your global config, so plugins cannot be removed that way; `--pure` would also drop session-tracker plugins. oc-connect points the worker's `XDG_CONFIG_HOME` to its own directory (`~/.local/state/oc-connect/worker-home`) with a generated `opencode.json`, your plugin *files* and a symlink for every other entry of `~/.config` (so `gh`, `git` and friends keep their config). Logins live in OpenCode's data dir and are unaffected.
- **Cost on flat-rate providers.** OpenCode Go reports a per-message cost (measured: $0.01 for a small job on deepseek-v4.1-flash), so `OC_BUDGET` fires there as well - but that number is not what a subscription actually charges you. The limit that matters then is the provider's usage quota, which oc-connect cannot read.
- **Typing in a worker tab** goes to the agent shown at the bottom of that tab. With the worker profile that is the job's agent; with `OC_PROFILE=inherit` it is your default agent (e.g. oh-my-opencode's orchestrator), not the job's subagent.
- **Subagents as job agents (inherit mode).** oh-my-opencode's `fixer` and `designer` are OpenCode *subagents*. `opencode --agent fixer` fails for them, but the HTTP API accepts them, so the job's agent goes with every prompt.
- **Why a tab per job.** Trackers like Vibe Island find a session's terminal by walking up from the OpenCode process to the first TTY. A shared background server made all jobs look like one session with no tab to jump to.
- **Tabs close themselves.** A reaper beside each worker stops it once the job has been idle for `OC_IDLE_CLOSE_MIN` minutes, or two minutes after its Claude session ended without another session taking the job over (a restart of Claude Code is fast enough). A busy or asking worker is never stopped. Worktree and session stay; `send` or `open` bring the worker back in the same session. Reading a finished job in its tab for longer than the limit closes it - raise the limit if you like to linger.
- **Terminal.app keeps dead tabs** with their device name, and the next tab can reuse it; trackers then jumped into the dead tab. oc-connect records the window id of every tab it opens and closes those tabs once nothing runs in them. (Matching by title does not work: OpenCode sets its own title and resets it to "Terminal" on exit.)
- **Terminal.app lists every tab as its own window** in AppleScript. oc-connect therefore opens a tab via the Shell menu and writes the worker command only into a window id that did not exist before. An earlier version wrote into "the selected tab of the front window" - when opening the tab failed, that was the tab running Claude Code.
- **The tab opens straight into the job's session.** The session is created first by a short-lived server; switching a running TUI to a session afterwards was unreliable and left the tab on the start screen.
- **No agent-panel row.** Only subagents get a custom row in Claude Code's agent panel, and subagents are model calls. A Haiku watcher was tried for it and misreported the job state (it summarised although told to pass the output through), and the panel setting from the plugin did not run. Status rows and the monitor need no model.
- **Monitors are experimental** in Claude Code and run only in interactive sessions. Without them Claude uses `oc wait` in the background.
- **Review stays Claude's job.** In real use the workers were fast and formally clean, but checked "nothing else broke" only with existing tests. The gaps were found in Claude's review.

## Development

```bash
git clone https://github.com/jonaandresen/oc-connect && cd oc-connect
python3 -m unittest discover tests      # fake OpenCode server, no model needed
claude plugin validate --strict .
ln -s "$PWD" ~/.claude/skills/oc        # load the working copy as oc@skills-dir
```

## License

MIT
