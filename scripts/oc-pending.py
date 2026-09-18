#!/usr/bin/env python3
"""oc-pending — offene Rückfragen und Rechte-Anfragen EINER Sitzung ausgeben.

Aufruf: oc-pending.py <base-url> <directory> <sessionID>
Ausgabe je Zeile: `FRAGE <id>: <text>  [Optionen: …]` oder `RECHT <id>: <art> <muster>`.
Leer = nichts offen.

Eigene Datei statt Einbettung in `oc`: Ein Python-Schnipsel in einem
einfach-gequoteten Bash-String kann keine Anführungszeichen enthalten — die
erste Fassung scheiterte daran bei JEDEM Aufruf still, und Rückfragen blieben
unbemerkt.
"""
import json
import sys
import urllib.parse
import urllib.request

base, directory, sid = sys.argv[1], sys.argv[2], sys.argv[3]
q = urllib.parse.quote(directory)


def get(path):
    """GET; leere Liste bei jedem Fehler."""
    try:
        with urllib.request.urlopen(f"{base}{path}?directory={q}", timeout=3) as r:
            return json.loads(r.read() or b"[]") or []
    except Exception:
        return []


for it in get("/question"):
    if it.get("sessionID") != sid:
        continue
    for frage in it.get("questions", []):
        optionen = " | ".join(o.get("label", "") for o in frage.get("options", []))
        print(f"FRAGE {it['id']}: {frage.get('question', '')}  [Optionen: {optionen}]")
for it in get("/permission"):
    if it.get("sessionID") == sid:
        print(f"RECHT {it['id']}: {it.get('permission')} {' '.join(it.get('patterns', []))}")
