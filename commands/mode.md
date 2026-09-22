---
description: Show or switch when Claude may hand work to OpenCode (off, ask, mechanical, auto)
argument-hint: "[off|ask|mechanical|auto|default]"
---
Run `oc mode $ARGUMENTS` with Bash and show the user its one-line answer. Without an argument it only shows the current mode. `default` goes back to the plugin option.

From now on follow the mode it reports: `off` - never delegate unless the user asks for OpenCode; `ask` - call `delegate` when a task fits, the user confirms in a dialog (do not ask in the chat first); `mechanical` - bounded work without asking, larger work after one question; `auto` - your judgement.
