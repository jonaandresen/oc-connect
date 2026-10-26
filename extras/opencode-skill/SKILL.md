---
name: opencode
description: Hand implementation work to an OpenCode worker (other models, free by default) running in a sandbox; Claude reviews and commits. Use when the user names OpenCode or asks to delegate/offload a task, or when an implementation task is substantial and clearly specified.
---

# OpenCode worker

The worker implements; you own the task text, the review and the commit. It runs `opencode run` inside Anthropic's sandbox runtime: writes only in this repository (git hooks, git config and `.claude/` excluded), network only to the model provider. The wrapper handles timeouts and connection errors and prints a short summary.

`oc-run` below means `~/.claude/skills/opencode/scripts/oc-run`. Model: `OC_MODEL` (default `opencode/deepseek-v4.1-flash-free`; `opencode models` lists more). Needs OpenCode 2, `jq`, `npx`.

## When to delegate

Measured on three small tasks (oc-connect `docs/measured.md`, 2026-10-26): doing a small change yourself cost less Claude usage than delegating it, even to a free worker — the hand-over plus review exceeded the implementation. Delegate when the implementation outweighs the review: multi-file features, broad mechanical changes, or several independent tasks in parallel. For a small fix, do it yourself and say so in one line. When the user explicitly asks for OpenCode, delegate.

## Steps

1. **Task file.** Write `$(git rev-parse --git-dir)/oc/<slug>.task.md` (outside the working tree). Default is a short hand-over: the user's request nearly verbatim, the check commands, and "Do not commit, do not push." — the worker researches the code itself, which was the cheapest variant measured. Add file paths, line numbers and numbered required behaviour only when you already know them or the task is ambiguous; settle every open decision in the text, because the worker cannot ask you.
2. **Start** with Bash `run_in_background: true` (an attempt may run 15 min, longer than a foreground call): `oc-run -t <slug> <task file>`. You are notified when it exits; the output is the summary (outcome, session id, final message, changed files). Tell the user they can watch with `oc-run watch <slug>` (live log in a terminal window) or, once done, `oc-run view <session>` (OpenCode TUI).
3. **Review** — never delegated. Read the full diff (`git diff`, plus untracked files) first, before running anything: checks, tests and package scripts run outside the sandbox. Then run every check yourself, do your own defeat test (break the change, see a test go red), and look for untested cases and scope creep. Done when every changed line is read and every check ran green as its own command.
4. **Corrections.** Small things: fix them yourself. Larger: write the finding plus what to do into a new task file and continue the same session: `oc-run -s <session> -t <slug>-2 <file>` (background again).
5. **Commit** yourself after the review. The sandbox lets the worker create local commits, so check `git log` for any it made and fold them into yours.

## Parallel tasks

One `git worktree add ../<repo>-<slug> -b <slug>` per task, `oc-run` started from inside each worktree, one background call each. Review and merge each branch as above.

## When it fails

- `outcome: error` with `ERR_PROXY_TUNNEL ... 403`: the model's provider is not in the network allowlist (`DOMAINS` in the script). Ask the user before adding a domain.
- `outcome: timeout` after the retries, or repeated connection errors: free endpoints are flaky. Retry once later, try another model, or do the task yourself.
- `Operation not permitted` in the worker's output: the sandbox blocked a write or a host. Expected for anything outside the repository.
