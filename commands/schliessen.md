---
description: OpenCode-Worker eines Auftrags beenden — sein Tab schließt sich (worktree und Sitzung bleiben)
argument-hint: <auftrag>
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/oc:*)
---
Ohne Argument: `${CLAUDE_PLUGIN_ROOT}/scripts/oc status` zeigen und fragen, welcher Auftrag.

Mit Argument: Prüfe zuerst mit `oc status`, ob der Auftrag noch „busy“ ist. Wenn ja, NICHT beenden, sondern nachfragen — ein laufender Worker verliert seinen Arbeitsstand. Sonst: `${CLAUDE_PLUGIN_ROOT}/scripts/oc close $ARGUMENTS` und das Ergebnis melden.
