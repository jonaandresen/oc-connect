# Changelog

## 2.1.0

- **Workers run in the background by default.** No terminal opens at `delegate`; `/oc:show <job>` (the `open` tool) opens a window with OpenCode's TUI attached to the running worker (`oc attach`), and typing there goes into the job's session. `close`, the reaper and `done` end those windows with the worker. The option `worker_terminal` (`OC_OPEN`) keeps `window` and `tab` for one terminal per job from the start. Session trackers such as Vibe Island see a background worker only while its window is open.
- A worker started from a login shell found another OpenCode than Claude's (measured: `bash -l` found 1.18 in `/opt/homebrew/bin`, Claude's PATH 2.1.4 in `~/.opencode/bin`). The resolved path now travels with the command, in tabs too.
- `docs/measured.md`: plugin vs. plain `opencode run` vs. Claude alone on three real tasks - at that task size delegating cost more Claude usage than doing it, and the plugin cost more than the plain CLI. `extras/opencode-skill/` is the lightweight alternative that came out of it: one skill plus a wrapper that runs `opencode run` in Anthropic's sandbox runtime.

## 2.0.0

For OpenCode 2 (tested with 2.1.4). OpenCode 1 users stay on oc-connect 1.x (branch `v1`); this version refuses OpenCode 1 with a pointer there.

OpenCode 2 is a new program behind the same name: its server API moved to `/api` behind a password, questions became forms, the TUI attaches to a server instead of being one. Everything that talks to OpenCode was rewritten and re-measured; the Claude side (MCP tools, monitor, status rows, hooks, review gate, snapshots) is unchanged.

- **One server per job, in the job's tab.** `oc tab` (started by the tab) runs `opencode serve` on the job's port with a password of the job's own and the worker's environment, creates the session, and attaches the TUI with `--server`. Both stay in the tab's terminal, so session trackers still find the job's tab; closing the tab, the reaper, `close` and `done` stop server and TUI together.
- **The guard goes onto every session** (`permissions` at creation) as well as into the worker's config and `OPENCODE_CONFIG`. Rules are OpenCode 2's list - the last matching rule wins (measured). The guard file in the state dir may still be in the 1.x format; it is converted on the fly.
- **Second guard layer re-proven:** the server's environment reaches every shell command, so the push and docker block works as before. `oc selftest` on 2.1.4: direct push and docker blocked by the guard, indirect forms by the environment, nothing reached the remote.
- OpenCode 2 splits compound commands (`a && b`) and checks each part - a push hidden behind `cd x &&` now meets the guard too. Permission requests show every part.
- Questions are OpenCode 2 forms; `answer` maps one answer to each field, a multiselect answer becomes a list.
- The worker profile's agents carry no model: OpenCode 2 silently drops an agent whose config names one (every notation tried). The model goes onto the job's session instead. The TUI keeps the user's `cli.json` plugins (session trackers), without oh-my-opencode.
- No todo progress: OpenCode 2 has no todo tool. Status rows show the last activity instead.
- Cost comes from the session header, which is cheap to read while the worker runs.

## 1.0.0

First public release, for OpenCode 1.x. From here on the major version follows OpenCode's: oc-connect 1.x for OpenCode 1 (branch `v1`), 2.x for OpenCode 2 (`main`).

- `OC_OPENCODE` points to the OpenCode binary, for a 1.x install next to OpenCode 2. Found while recording the README demo: the MCP server took OpenCode 2 from the login shell's PATH.
- OpenCode 2 is detected and refused with a clear message (`setup` warns, `delegate`, `oc start` and `selftest` stop) instead of failing with `HTTP Error 405`. OpenCode 2 moved its server API to `/api/...` behind a password (measured on 2.0.29). oc-connect 2.x supports it.
- `selftest` names the provider error (for example an expired API key) instead of reporting that the model skipped the command.
- `selftest` checks which command the model really ran. A model asked for `bash -c "git push ..."` ran `git remote -v` instead; its output showed the disabled push URL and the selftest counted a push that never happened as blocked. Such a run now counts as NOT RUN, and a wrapped command is shown as `(ran as: ...)`.
- `oc --version` and the MCP server reported 0.6.0; they now report the plugin version, and a test keeps both in sync.

## 0.8.0

- You decide whether work goes to OpenCode. New default mode `ask`: every delegation brings up Claude Code's confirmation dialog with title, agent and task. `off` refuses unless your latest message asked for OpenCode, and then asks too. The gate is a PreToolUse hook on `delegate` and on `oc start` in Bash, so it does not depend on the model following instructions; a hook's "ask" shows the dialog even under `--dangerously-skip-permissions` (measured).
- `/oc:mode` (CLI `oc mode`) shows or switches the mode at any time; it wins over the plugin option until `/oc:mode default`.
- Workers open in their own window by default, so they can sit next to Claude; option `worker_terminal` / `OC_OPEN=tab` keeps tabs. WezTerm, kitty and iTerm2 get the same choice (untested).

## 0.7.0

- Worker tabs close once Claude no longer uses them. Every tab starts a small reaper beside OpenCode. It stops the worker when the job has been idle (finished or failed, nothing pending) for `OC_IDLE_CLOSE_MIN` minutes (default 20, `0` = off), or when the Claude session that owns the job has ended and no other session adopted it within two minutes. A busy worker or one waiting for an answer is never stopped. Worktree and session stay; `status` shows why a worker was closed.
- `send` to a stopped worker reopens its tab in the same session instead of failing.
- Dead worker tabs in Terminal.app now actually close. They were found by their "OC" title, but OpenCode sets its own title and resets it to "Terminal" when it exits (measured), so finished tabs piled up. oc-connect now records the window id of every tab it opens and closes only those, and only once no process runs in them - a user's idle shell tab is never touched.
- `close` waits until OpenCode has exited (about 1.2 s after SIGTERM, measured) before closing the tab; the fixed 1 s wait lost that race and left the tab open.

## 0.6.0

- Snapshots: the worker's result is recorded as a commit under `refs/oc/<slug>` before `report`, `send`, `review` and `done`. `restore` puts a snapshot back (the state before the restore is snapshotted first, the recorded review is dropped), `snapshots` lists them. Found in real use: a single `git checkout --` during a review destroyed a finished result that nothing could reproduce. Snapshots survive `done --force`, so a removed worktree can still be recovered from the repository.
- `status` (MCP) answers in compact text instead of JSON - same facts, a fraction of the tokens in Claude's context.
- The MCP server configuration moved from `.mcp.json` into `plugin.json`. Working *on* the plugin no longer started a second, broken `opencode` server: in a project checkout Claude Code read the plugin's `.mcp.json` as project config, where `${CLAUDE_PLUGIN_ROOT}` is not set (`ENOENT ... /bin/oc`).
- Status line: a name or title that does not fit is marked with `…` instead of being cut silently.
- Measured and corrected: with a flat-rate provider (OpenCode Go) OpenCode *does* report a cost per message, so `OC_BUDGET` works there too.

## 0.5.0

- Own worker profile (default): workers get an OpenCode config of their own via `XDG_CONFIG_HOME` - none of the user's OpenCode plugins, logins, MCP servers and session-tracker plugin files kept, other tools' config mirrored. Agents `oc-fast`, `oc-strong`, `oc-ui` are primary (the tab shows the right one), take their models from oh-my-opencode roles, and share a worker prompt for this workflow. `OC_PROFILE=inherit` / option `worker_profile` keeps the old behaviour.
- The question tool is permitted explicitly in the profile (without it the worker reported that it had none).
- Removed the Haiku watcher agent and the agent-panel status line: the watcher summarised although told not to and reported a stale state; the plugin's `subagentStatusLine` never ran. The monitor covers every event without a model.

## 0.4.0

- Guard, second layer: the worker environment rewrites every git push URL (explicit ones included) and disables docker and git over ssh. The OpenCode guard alone let `bash -c "git push"`, `command git push`, `/usr/bin/git push` and `env git push` through (measured). `oc selftest` now runs in a throw-away repo, tries indirect forms too, and judges by what reached the remote.
- Jobs belong to the Claude session that started them; only its monitor reports them, orphans are adopted. `answer` takes the question id and refuses when that question is gone.
- Monitor reports failed turns (API errors, rate limits), stalls (`OC_STALL_MIN`), cost steps (`OC_BUDGET`), questions answered directly in OpenCode, and the start of a job.
- Review gate: `review` records checks, defeat test and gaps, tied to the worktree's exact content; a PreToolUse hook blocks `git commit` in a job worktree without it.
- `done` refuses while uncommitted or unpushed work exists (`force` to discard).
- `delegate` returns right away; the tab opens directly in the job's session (pre-created) and installs dependencies itself.
- Terminal.app: new tabs are detected by window id (every tab is its own AppleScript window). Fixes a bug where the worker command could land in the tab running Claude Code.
- Status lines read the session's message list only when it changed.
- Worker agent keeps waiting through questions; the monitor does not repeat its finish.

## 0.3.0

Rewrite as a real Claude Code plugin.

- Core in Python (standard library, 3.9+) instead of bash; one library for CLI, MCP server, monitor and status lines.
- MCP server `opencode` with tools and instructions, so every session knows when delegating is worth it (`autonomy` option).
- Monitor: Claude is notified when a job asks, finishes or dies.
- Agent `opencode-worker` plus `subagentStatusLine`: jobs show live in the Claude Code agent panel.
- Prompt hook: "do this with OpenCode" is recognised in German and English.
- Subagents such as oh-my-opencode's `fixer` work as job agents (sent per prompt via the API; `--agent` on the TUI rejects them).
- Guard config lives in the state dir instead of the repository; untracked repo `opencode.json` is still applied.
- `.env` files found via git instead of a fixed list; pnpm, yarn and bun lockfiles supported.
- `oc selftest` reads all messages of the test session (the reply alone holds only the last step).
- English UI and docs. Jobs of the old bash version are imported automatically.

## 0.2.0

German bash version: one tab and one OpenCode process per job, status line, board.
