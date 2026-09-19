# oc-connect - working on the plugin

Claude Code plugin: Claude is the lead, OpenCode workers implement. What it does for users is in `README.md`, the version history in `CHANGELOG.md`. This file is for whoever changes the plugin: how to work on it, what was measured, what is verified and what is still open.

## Develop, test, ship

```bash
python3 -m unittest discover tests          # fake OpenCode server, no model; must pass on /usr/bin/python3 (3.9) too
/usr/bin/python3 -m unittest discover tests
claude plugin validate --strict .
bin/oc selftest --model opencode-go/deepseek-v4.1-flash   # real OpenCode: both guard layers, throw-away repo
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
- With a flat-rate provider (OpenCode Go) every message reports cost 0, so `OC_BUDGET` never fires there.

**Terminal.app**
- AppleScript lists every tab as its own window with one tab. A new tab is a new window id; "count of tabs" never grows.
- Cmd-T via System Events was silently dropped in some runs; clicking Shell menu → New Tab (by position: menu 3, item 2, first entry) worked. Writing into "the selected tab of the front window" after a failed attempt put the worker command into the tab running Claude Code - never do that; only write into a window id that did not exist before and runs nothing.
- `before` is a reserved word in AppleScript; a variable of that name makes the whole script fail.
- Finished tabs stay open and keep their tty name; the next tab can reuse it, and trackers jumped into the dead one. `close_dead_tabs()` closes finished "OC …" tabs.

**Claude Code**
- Plugin `monitors` run for the session and deliver each stdout line as a notification (verified live). A process started with `( … &)` is re-parented to PID 1, so `claude_pid()` finds no owner - test the monitor with `run_in_background`, not with a detached subshell.
- The plugin `settings.json` key `subagentStatusLine` never ran (logged: zero calls while an agent ran). Reason not found.
- A Haiku watcher agent told to return tool output unchanged summarised it anyway and reported a stale git state. Do not put a model between a job and the lead.
- Plugin `bin/` is on the Bash PATH; `${CLAUDE_PLUGIN_ROOT}` changes with every version, so settings that must survive updates point to the shim `~/.local/state/oc-connect/bin/oc`, rewritten at every SessionStart.

## Verification status (2026-09-19, version 0.5.0)

Verified end to end, in a real Claude Code session with the installed plugin:
- MCP tools and instructions load; `answer` via MCP reached the worker; a wrong question id is refused.
- Monitor notifications: running (with browser link), question with id, finished.
- Commit gate: `git commit` in a job worktree was denied without `review`, allowed after it.
- `done` refused while a commit was not pushed.
- Tab opens directly in the job's session (checked on a screenshot); the session appears in Vibe Island with model and terminal.
- Worker profile: `oc-fast` ran on deepseek-v4.1-flash without oh-my-opencode and superpowers, asked via the question tool, wrote the file, gave the report in the prompt's format.
- `oc selftest`: direct push and docker blocked by the guard, indirect forms by the environment, nothing reached the remote; with a permissive guard it reports NOT PROVEN (defeat test).

Unit tests: 44, green on Python 3.14 and 3.9.

## Open

1. **Vibe Island click → tab.** The session shows up; whether a click jumps into its tab is not confirmed.
2. **Status line rows** (`▶ slug …` under the Open Island line) have not been confirmed on screen in a live session.
3. **Quality of the worker profile.** Only probe jobs so far. Run a real, small task (e.g. missing tests in a project) and compare the review findings with earlier runs of oh-my-opencode's orchestrator.
4. **Terminals other than Terminal.app** (iTerm2, WezTerm, kitty, tmux) are implemented but untested.
5. **Agent panel row.** Would need a subagent (a model call) and the plugin's `subagentStatusLine` did not run. Parked unless Claude Code offers rows for non-model tasks.
6. **Before making the repo public:** switch the commit author e-mail to the GitHub noreply address; decide whether to port to TypeScript for the official MCP and OpenCode SDKs (the MCP protocol and the OpenCode API are hand-rolled here).
7. **Budget on flat-rate providers:** OpenCode reports no cost, and the provider quota is not readable - no cost warning there.
