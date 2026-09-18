---
description: Stand aller OpenCode-Aufträge im Chat anzeigen (läuft / wartet / fertig)
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/oc:*)
---
Führe aus: `${CLAUDE_PLUGIN_ROOT}/scripts/oc status`

Gib das Ergebnis als kurze Tabelle wieder (Auftrag, Zustand, Titel). Steht ein Auftrag auf „busy“ länger als erwartet oder wartet einer auf Antwort, prüfe mit `${CLAUDE_PLUGIN_ROOT}/scripts/oc wait <slug> 0` bzw. der offenen Frage und sag, was zu tun ist. Nichts beantworten oder schließen ohne Rückfrage.
