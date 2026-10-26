---
description: Stop an OpenCode job's worker - its window closes, worktree and session stay
argument-hint: <job>
---
Without an argument: call the `status` tool and ask which job. With an argument: check the job's state first. If it is `running`, do NOT stop it - ask first, a running worker loses its current step. Otherwise call the oc-connect `close` tool for `$ARGUMENTS` and report the result.
