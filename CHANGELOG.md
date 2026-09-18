# Changelog

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
