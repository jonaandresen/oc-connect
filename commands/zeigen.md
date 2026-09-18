---
description: Den Terminal-Tab eines OpenCode-Auftrags öffnen (startet den Worker neu, falls der Tab geschlossen war)
argument-hint: <auftrag>
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/oc:*)
---
Führe aus: `${CLAUDE_PLUGIN_ROOT}/scripts/oc open $ARGUMENTS`. Ohne Argument erst `oc status` zeigen und fragen, welcher Auftrag.
