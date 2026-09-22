# Changelog

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
