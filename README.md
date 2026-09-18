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
| **Claude decides, or you do** | Default: bounded work (tests, docs, small UI, mechanical refactors) is delegated without asking; larger work after one question; money, auth, security and open product decisions never. Say "do this with OpenCode" and it is delegated, full stop. Configurable. |
| **Live in Claude Code** | Each job can show up in Claude Code's **agent panel** with progress, diff size and state, and as rows under your status line: `▶ 276  Orders after payment  ███████░░░ 14/18  15 files +169 -65  fixer  26m`. |
| **Push, not poll** | A background monitor tells Claude the moment a worker asks something, finishes or dies. |
| **One job = one worktree = one tab = one process** | Session trackers such as **Vibe Island** and **Open Island** show every job separately, and a click jumps to its tab. Closing the tab stops the worker. |
| **Hard guard** | Workers run with a permission config that blocks `git commit/push/merge/stash`, `docker`, `ssh`, `gh pr merge`, `npm install` and more. `oc selftest` *proves* it by the status of the tool calls, not by what the model says. |
| **oh-my-opencode optional** | With it, Claude picks `fixer` / `designer` / `orchestrator` per job. Without it, jobs go to OpenCode's `build` agent. |

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
- "Write the missing tests for the cart" - Claude may delegate this on its own and says so in one line.

Commands:

| | |
|---|---|
| `/oc:delegate <#issue or task>` | Delegate explicitly |
| `/oc:status` | All jobs in the chat |
| `/oc:board` | Live board in a new tab (`1`-`9` opens a job, `q` quits) |
| `/oc:show <job>` | Open a job's tab (restarts a stopped worker in the same session) |
| `/oc:stop <job>` | Stop a worker; worktree and session stay |
| `/oc:setup` | Check, selftest, status line |

The CLI does the same from any shell: `oc --help` (inside Claude Code `oc` is on the Bash PATH).

### The lifecycle of a job

1. Claude researches and writes the task (files with lines, required behaviour, tests, what must not change, check commands).
2. `delegate`: worktree from `origin/HEAD` on branch `oc/<slug>`, git-ignored `.env` files copied, dependencies installed from lockfiles (`npm ci`, `pnpm`/`yarn`/`bun` frozen), new tab with the worker, session created, task sent.
3. Claude optionally starts the `oc:opencode-worker` agent (Haiku, only waits) so the job appears in the agent panel.
4. The monitor reports questions. Claude answers what code and task settle and asks you the rest.
5. Done → Claude reads the report and the full diff, runs all checks, does a defeat test, looks for untested cases, sends corrections into the same session.
6. Claude commits and opens the PR, records it (`set_pr`) and closes the worker. After merge, `done` removes the worktree.

## Configuration

**Autonomy** (asked when the plugin is enabled, change via `/plugin` → oc → configure):

| Value | Behaviour |
|---|---|
| `mechanical` (default) | Bounded work without asking, larger work after one question |
| `ask` | Always ask first |
| `auto` | Claude decides |
| `off` | Only when you say so |

**Environment variables** (optional):

| Variable | Default | Purpose |
|---|---|---|
| `OC_AGENT` | `orchestrator` with oh-my-opencode, else `build` | Default agent |
| `OC_MODEL` | agent's own model | `provider/model` override |
| `OC_WT_ROOT` | `<repo>-worktrees` | Where worktrees go |
| `OC_BASE_REF` | `origin/HEAD` | Where jobs branch from |
| `OC_COPY` | git-ignored `.env`, `.env.local`, … | Files copied into each worktree (space separated) |
| `OC_INSTALL` | `1` | `0` skips dependency installation |
| `OC_TERMINAL` | `$TERM_PROGRAM` | Force a terminal |
| `OC_STATE` | `~/.local/state/oc-connect` | Job state and the guard |
| `OC_STATUSLINE_ALIGN` | `left` | `right` aligns the job rows right |

**The guard** lives in `~/.local/state/oc-connect/guard.json` (created from `templates/guard.json`). Edit it to tighten or loosen the rules for all repos. A repo's own untracked `opencode.json` is copied into each worktree on top.

## Good to know

- **Subagents as job agents.** oh-my-opencode's `fixer` and `designer` are OpenCode *subagents*. `opencode --agent fixer` fails for them, but the HTTP API accepts them. oc-connect therefore starts the tab without `--agent` and sends the job's agent with every prompt.
- **Why a tab per job.** Trackers like Vibe Island find a session's terminal by walking up from the OpenCode process to the first TTY. A shared background server made all jobs look like one session with no tab to jump to.
- **Terminal.app keeps dead tabs** with their device name, and the next tab can reuse it; trackers then jumped into the dead tab. oc-connect closes finished "OC …" tabs itself.
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
