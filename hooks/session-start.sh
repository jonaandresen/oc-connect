#!/bin/bash
# SessionStart: Gibt es offene OpenCode-Aufträge, erfährt Claude beim Start
# davon — sonst arbeitet eine neue Sitzung ahnungslos neben laufenden Workern.
# Ohne Aufträge keine Ausgabe (kein Rauschen im Kontext).
STATE="${OC_STATE:-$HOME/.local/state/oc-auftrag}"
ls "$STATE"/*.env >/dev/null 2>&1 || exit 0
out=$("$(dirname "$0")/../scripts/oc" status 2>/dev/null) || exit 0
[ -n "$out" ] || exit 0
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":
 "Offene OpenCode-Aufträge (Plugin oc, Skill oc:auftrag):\n"+sys.argv[1]+
 "\nVor dem Committen jeden fertigen Auftrag prüfen (Skill-Schritt 5)."}}))' "$out"
