# oc-connect - working on the plugin

Claude Code plugin: Claude is the lead, OpenCode workers implement. What it does for users is in `README.md`, the version history in `CHANGELOG.md`. This file is for whoever changes the plugin: how to work on it, what was measured, what is verified and what is still open.

## Develop, test, ship

```bash
python3 -m unittest discover tests          # fake OpenCode server, no model; must pass on /usr/bin/python3 (3.9) too
/usr/bin/python3 -m unittest discover tests
claude plugin validate --strict .
bin/oc selftest --model opencode/deepseek-v4.1-flash-free   # real OpenCode 2: both guard layers, throw-away repo
```

Ship: commit (no Claude/AI signature lines), `git push`, then
`claude plugin marketplace update oc-connect && claude plugin update oc@oc-connect` and restart Claude Code. The installed copy lives in `~/.claude/plugins/cache/oc-connect/oc/<version>/`; never edit it except for a throw-away experiment, and restore it afterwards.

Rules for changes:
- Standard library only, Python 3.9 compatible (`from __future__ import annotations`, no `match`, no `X | Y` at runtime).
- Every fix gets a test, and every test gets a defeat test: break the fix, see the test turn red.
- A claim about OpenCode or Claude Code behaviour goes into code or docs only after it was measured. Several "obvious" assumptions below turned out wrong.
- End-to-end probes run in a scratch repo with a local bare `origin`, never in a real project. Clean up with `oc done <slug> --force`, and close only the terminal tabs you opened yourself.

## Measured facts (OpenCode 1.18.31, Claude Code 2.1.277, macOS Terminal.app)

**OpenCode**
- oh-my-opencode's `fixer`/`designer` are **subagents**: `opencode --agent fixer` fails in the TUI, the HTTP API (`agent` in the prompt body) accepts them.
- The permission guard matches command strings: `bash -c "git push"`, `command git push`, `/usr/bin/git push`, `env git push` all passed it. Hence the second layer in `ops.worker_env()`: `remote.origin.pushurl` plus a catch-all `url.<nowhere>.pushInsteadOf ""` block every push URL, explicit ones included, while fetch keeps working; `DOCKER_HOST` points to a dead socket. Not a sandbox - `env -u GIT_CONFIG_COUNT` gets around it.
- `POST /session/<id>/message` returns only the LAST step of a turn; tool calls sit in earlier messages. Read `/session/<id>/message` afterwards.
- `OPENCODE_CONFIG` and `OPENCODE_CONFIG_CONTENT` are merged into the global config; an empty `plugin` list does NOT unload oh-my-opencode. `OPENCODE_CONFIG_DIR` adds, it does not replace. `--pure` drops all external plugins, including the Vibe Island / Open Island plugin files. Only `XDG_CONFIG_HOME` pointing to our own directory replaces the global config; plugin files in its `opencode/plugins` still load, logins survive (data dir).
- In a config without the user's `permission` block the `question` tool is off - the worker said it had none. The profile allows it explicitly.
- A session created by one server process is visible to another on the same machine (sessions are on disk). That is how the tab can start with `-s <session>`.
- `/tui/select-session` right after the port answers is unreliable - the TUI is not listening yet and the tab stayed on the start screen.
- Models do not always run the command they are asked for (1.19.4, `deepseek-v4.1-flash-free`): once `git remote -v; git status -sb` instead of `bash -c "git push ..."`, once a direct `git push` that passed the guard although `git push origin HEAD:refs/...` alone was blocked by it (measured twice, with and without `/`). The selftest therefore judges the command in the tool part's `state.input.command`.
- A provider error ends the turn without parts: `POST /session/<id>/message` returns `info.error = {name: "APIError", data: {message, statusCode}}` (measured 1.19.4, expired key). `ops.turn_error()` reads it.
- A flat-rate provider (OpenCode Go) DOES report a cost per message: $0.01 for a small job on `deepseek-v4.1-flash` (measured 2026-09-20). The earlier claim of cost 0 was wrong. What the number is worth is another question - a subscription does not charge per message.

**OpenCode 2 (measured on 2.1.4, 2026-10-24) - what 2.x is built on**
- `opencode.ai/install` still installs 1.x (1.19.4 on 2026-10-24); 2.x comes from `opencode.ai/v2/install` / npm `@opencode/cli-<platform>`. Branch `v1` (oc-connect 1.x) keeps OpenCode 1.
- `opencode serve --port P` prints a password unless `OPENCODE_SERVER_PASSWORD` sets one; the API needs HTTP basic auth (user `opencode`), routes live under `/api`, the OpenAPI spec is `/openapi.json` ("Experimental HttpApi" 0.0.1). `/doc` and every unknown path serve the web UI. The v1 path `POST /session` answers 405.
- The TUI has no `--port`: it attaches with `--server URL` (the password from `OPENCODE_SERVER_PASSWORD` in its env works) and `--session ID` opens that session directly. Hence `oc tab`: server + TUI as children of one process in the tab.
- `POST /api/session` takes `title`, `agent`, `model` (`{providerID, id}`) and `permissions` (a rule list) - the guard sits on the session. Session rules beat a config `shell * allow` (a session deny alone blocked).
- Permission rules: a list of `{action, resource, effect}`; the LAST matching rule wins (allow-then-deny blocked `echo`, deny-then-allow ran it). Actions: `shell` (1.x `bash`), `subagent` (1.x `task`), `question`, `external_directory`, ... OpenCode 2 reads a 1.x config and translates it (`permission` -> `permissions`, `agent` -> `agents`, `plugin` -> `plugins`, `{file:...}` prompts -> `system` text).
- `OPENCODE_CONFIG` is still merged (after the user's config); `XDG_CONFIG_HOME` still replaces the global config (only the worker profile loaded).
- An agent whose config names a model is dropped silently - `"provider/model"`, `{providerID, id}`, `{providerID, modelID}` all tried; without `model` it loads. The model goes onto the session instead.
- `POST /api/session/<id>/prompt {text}` returns at once (the message is queued); `GET /api/session/active` lists running sessions; `POST /api/experimental/session/<id>/wait` blocks until idle. A turn ends with an `idle` message: `outcome` succeeded / failed / interrupted, `error {type, message}` on failure.
- Messages come newest first (`?limit=`); types user, assistant (`content`: text, reasoning, tool), idle, system. A tool is `{type: tool, name, state: {status, input, content: [{type: text, text}], metadata, error}}`. Shell output sits in `state.content`, the exit code in `state.metadata.exit`. A guard block is `status: error, error.type: permission.rejected`.
- The session header (`GET /api/session/<id>`) carries `cost`, `model`, `outcome`, `time.updated` - cheap, read while running.
- The question tool creates a form (`/api/session/<id>/form`): one field per question (`key` q0, q1, ...), `options` with label/value, `custom: true`. Reply `{"answer": {"q0": "HALF_EVEN"}}`; the model got the answer and used it. The session stays in `active` while a form is open.
- Permission requests (`/api/session/<id>/permission`): `action`, `resources` - a compound command is split into its parts (`a && b` -> ["a", "b"]), each checked; reply `{"decision": once|always|reject}`.
- There is no todo tool and no todo route any more.
- Code mode: models get an `execute` tool (JavaScript against tools). The shell is not in its catalog (a model searched for it and tried Playwright's `browser_run_code_unsafe` - failed). OpenCode's own `opencode.worktree_list` ran through `execute` although a session rule denied that action; whether the action name was wrong or `execute` skips the rule is open.
- Shell commands inherit the server process's environment - the second guard layer (git push URL, DOCKER_HOST) works as on 1.x (`oc selftest`).
- Free models (`opencode/*-free`) need either no OpenCode account at all or a workspace that allows free endpoints (error `provider.invalid-request: ... Allow free endpoints in your workspace's Privacy settings`). For tests: `XDG_DATA_HOME` and `XDG_STATE_HOME` pointing to an empty directory = anonymous. In tmux, set them with `set-environment -g`: new windows do not inherit a pane's variables.

**Terminal.app**
- AppleScript lists every tab as its own window with one tab. A new tab is a new window id; "count of tabs" never grows.
- Cmd-T via System Events was silently dropped in some runs; clicking Shell menu → New Tab (by position: menu 3, item 2, first entry) worked. Writing into "the selected tab of the front window" after a failed attempt put the worker command into the tab running Claude Code - never do that; only write into a window id that did not exist before and runs nothing.
- `before` is a reserved word in AppleScript; a variable of that name makes the whole script fail.
- Finished tabs stay open and keep their tty name; the next tab can reuse it, and trackers jumped into the dead one. `close_dead_tabs()` closes them.
- OpenCode sets its own tab title ("OC | <session title>") and resets it to "Terminal" when it exits (measured 2026-09-22). Finding dead tabs by the "OC" title therefore failed; they are tracked by window id (`terminal-windows` in the state dir), and a tab is closed only when `count of processes` is 0 (an idle shell has processes, so user tabs are safe).
- OpenCode needs about 1.2 s to exit after SIGTERM; until then the tab still holds it and cannot be closed.
- The tab's login shell does NOT inherit Claude Code's environment. Anything the tab needs from it (e.g. `OC_IDLE_CLOSE_MIN`) goes into the command line.
- While Claude's window is in full screen, macOS turns a new Terminal window (`do script`) into a tab of it: the system default "Prefer tabs when opening documents: In Full Screen" (`AppleWindowTabbingMode` unset). Seen as identical bounds and a tab bar appearing on Claude's window.
- A background process started in the tab keeps the tab busy unless it leaves the tty session: the reaper calls `setsid()` (its tty shows as `??`).

**Claude Code**
- Plugin `monitors` run for the session and deliver each stdout line as a notification (verified live). A process started with `( … &)` is re-parented to PID 1, so `claude_pid()` finds no owner - test the monitor with `run_in_background`, not with a detached subshell.
- The plugin `settings.json` key `subagentStatusLine` never ran (logged: zero calls while an agent ran). Reason not found.
- A Haiku watcher agent told to return tool output unchanged summarised it anyway and reported a stale git state. Do not put a model between a job and the lead.
- Plugin `bin/` is on the Bash PATH; `${CLAUDE_PLUGIN_ROOT}` changes with every version, so settings that must survive updates point to the shim `~/.local/state/oc-connect/bin/oc`, rewritten at every SessionStart.
- A PreToolUse hook returning `permissionDecision: "ask"` brings up the confirmation dialog even in a session started with `--dangerously-skip-permissions` (measured 2.1.280 with a hook on an MCP tool; the user saw and confirmed the dialog). Hooks in `.claude/settings.local.json` took effect without a restart. This is what makes the delegation gate hard.
- Plugin `userConfig` reaches hooks as `CLAUDE_PLUGIN_OPTION_<KEY>` and the MCP server via `${user_config.KEY}` in `plugin.json` env (the latter measured; the hook variable is from the docs and not yet seen live). Shell-form hook commands reject `${user_config.*}`.
- A plugin's `.mcp.json` is ALSO read as project config when you work in the plugin's own checkout, and `${CLAUDE_PLUGIN_ROOT}` is empty there: `ENOENT ... /bin/oc`, a second server named `opencode` stuck on "Pending approval". Since 0.6.0 the config lives in `plugin.json` (`mcpServers`, inline object - a path or `.mcp.json` also work).

## Verification status of 2.0.0 (2026-10-24, OpenCode 2.1.4, Claude Code 2.1.310, tmux)

Live, `opencode/deepseek-v4.1-flash-free`, anonymous (empty XDG data/state dir):
- `oc selftest`: direct push and `docker ps` blocked by the guard, indirect forms by the environment, nothing reached the remote. One earlier run: the model ran `git remote -v` instead of `bash -c "git push"` - reported as NOT RUN, not as blocked.
- CLI cycle in a scratch repo: `start` (tab with `oc tab`: server + TUI on the job's session, agent `oc-fast`, model on the session), two permission requests shown with every command part and answered with `permit`, worker report in the worker prompt's format, `report` snapshot, `close` (tab, TUI and server gone), `send` reopened the tab in the SAME session, a question form answered with `answer` - the worker implemented the answered option -, `review` unblocked the commit gate, `done` left no process behind.
- A provider error (free endpoints not allowed for the logged-in workspace) showed as `failed` with the provider's message in `status` and in the selftest.
- Claude Code with the plugin (`--plugin-dir`, docs/demo.gif): delegate skill, confirmation dialog, worker window, monitor events, Claude's review with its own defeat test, job row "ready for review".
- Not re-checked on 2.x: Vibe Island / Open Island tracking of the job's tab (the TUI and the server both run in the tab's terminal, as trackers need), the reaper closing an idle worker live (its `close` path is the one `done` used; its decisions are unit-tested), Terminal.app windows (tmux only).

## Verification status of 1.x (2026-09-22, version 0.8.0; 1.0.0 changes checked 2026-10-24)

Verified end to end, in a real Claude Code session with the installed plugin:
- MCP tools and instructions load; `answer` via MCP reached the worker; a wrong question id is refused.
- Monitor notifications: running (with browser link), question with id, finished.
- Commit gate: `git commit` in a job worktree was denied without `review`, allowed after it.
- `done` refused while a commit was not pushed.
- Tab opens directly in the job's session (checked on a screenshot); the session appears in Vibe Island with model and terminal.
- Worker profile: `oc-fast` ran on deepseek-v4.1-flash without oh-my-opencode and superpowers, asked via the question tool, wrote the file, gave the report in the prompt's format.
- `oc selftest`: direct push and docker blocked by the guard, indirect forms by the environment, nothing reached the remote; with a permissive guard it reports NOT PROVEN (defeat test).

Three real jobs in a scratch repo (2026-09-20, `oc-fast` on `opencode-go/deepseek-v4.1-flash`), each the full cycle:
- Input validation plus tests: 11 tests, defeat test by the worker and by Claude, diff clean, review recorded, commit denied before the review and allowed after it, push, `done`.
- Rounding mode: the worker used its `question` tool for the open decision, waited, and implemented exactly the answered mode (`ROUND_HALF_UP`). Answer via `answer` with the question id.
- Snapshot probe: `report` snapshotted the result, `git checkout --` destroyed it, `restore` brought it back; after `done --force` the state was still readable from `refs/oc/snaptest`.
Timings: `delegate` to task sent 17 s (about 1.5 s of it the short-lived server for the session), small job finished after 65 s, job with a question about 100 s. `oc status` 6-26 ms, status line 50-90 ms per redraw - the poll loop is not a cost factor.

Reaper (2026-09-22, scratch repo, `oc-fast` on `opencode-go/deepseek-v4.1-flash`): with `OC_IDLE_CLOSE_MIN=1` an idle worker was stopped after ~75 s and its tab closed; the reason showed in `status`. `send` to the stopped worker reopened the tab in the same session and the worker answered. The owner-ended path is covered by unit tests only - while a session runs, its monitor adopts orphaned jobs, so it cannot be provoked live from inside one.
Vibe Island: clicking the job's session brought its OpenCode terminal to the front (confirmed by the user, 2026-09-22). In that run the worker opened as its own window, not as a tab of Claude's window.

Not yet seen live with the installed plugin (needs a restart): the delegate gate from the plugin's own `hooks.json`, `CLAUDE_PLUGIN_OPTION_AUTONOMY` in the hook, `/oc:mode`.

0.8.1 (2026-10-24): install from the marketplace into an empty Claude Code config (2.1.307) loaded 7 skills, 3 hooks and the MCP server; MCP `initialize`/`tools/list` answered; `oc setup` on 2.0.29 warns; `selftest` on 1.19.4 showed the provider error (expired key); with `opencode/deepseek-v4.1-flash-free` (no account) all six commands ran exactly as given and were blocked - direct push and `docker ps` by the guard, the indirect forms by the environment - and nothing reached the remote.

README demo (docs/demo.gif, 2026-10-24): recorded with asciinema in a tmux session (`claude --plugin-dir . --setting-sources project,local`, `OC_OPENCODE` = OpenCode 1.19.4, `OC_MODEL=opencode/deepseek-v4.1-flash-free`), rendered with `agg --speed 2`. Full cycle in one take: confirmation dialog, worker window, a permission request answered by Claude, monitor events, review with Claude's own defeat test.

Unit tests (2.0.0): 86, green on Python 3.14 and 3.9.

## Open

1. **Tab or window.** Default is now `window` (option `worker_terminal`). In full screen macOS still makes it a tab (see Terminal.app facts); whether the Shell-menu click of `tab` mode works on this machine is not re-checked.
2. **Status line rows** confirmed on screen in the README demo (tmux, `oc statusline` as the only status line). Under another status line (Open Island, claude-hud) not yet seen.
3. **Quality of the worker profile.** Three real tasks were clean (see above): test-first, honest defeat test, no scope creep, an open decision asked instead of guessed. Still open: a task in a real project with real history, and a comparison with oh-my-opencode's orchestrator.
4. **Terminals other than Terminal.app**: tmux verified (README demo, 2026-10-24: the worker opened as a tmux window, the job row showed under Claude's prompt). iTerm2, WezTerm and kitty are implemented but untested.
5. **Agent panel row.** Would need a subagent (a model call) and the plugin's `subagentStatusLine` did not run. Parked unless Claude Code offers rows for non-model tasks.
6. **Two lines.** 2.x (`main`, `v2`) for OpenCode 2, 1.x (`v1`) for OpenCode 1 - fixes only. A fix that concerns both goes into both branches.
7. **Budget on flat-rate providers:** the cost per message is reported (see above), but it does not describe a subscription's real limit, and the provider quota is not readable.
8. **OpenCode 2's API is "experimental"** (its own words, version 0.0.1). Re-run the selftest and a CLI cycle after every OpenCode 2 update; the facts above say which shapes oc-connect relies on.
9. **Code mode:** whether permission rules apply to tools called inside `execute` is open (see the OpenCode 2 facts).
10. **Official SDKs.** The MCP protocol and the OpenCode API are hand-rolled (standard library only). Worth revisiting if OpenCode 2's API keeps moving.
