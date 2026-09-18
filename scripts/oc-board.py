#!/usr/bin/env python3
"""oc-board — Live-Übersicht aller OpenCode-Aufträge des Plugins opencode-bauleitung.

Zeigt je Auftrag: läuft / wartet auf Antwort / fertig, seit wann, was der
Orchestrator gerade tut (letzter Werkzeugaufruf), offene Rückfragen. Aktualisiert
sich alle 3 Sekunden. `q` beendet, Zifferntaste 1–9 öffnet den Auftrag als Tab.

Bewusst NICHT hier: irgendetwas an den Sitzungen ändern. Das Board liest nur —
Antworten und Korrekturen laufen über Claude (`oc answer`, `oc send`), damit
Claude weiß, was entschieden wurde.

Falle: Der Server beantwortet /session/status nur je Verzeichnis. Jeder
Auftrag liegt in einem eigenen worktree, also eine Abfrage je Auftrag.
"""
import json
import os
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import urllib.parse
import urllib.request

PORT = os.environ.get("OC_PORT", "4096")
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.environ.get("OC_STATE", os.path.expanduser("~/.local/state/oc-auftrag"))
OC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oc")

DIM, BOLD, RED, GRN, YEL, CYA, RST = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"


def get(path):
    """GET gegen den OpenCode-Server; None, wenn er nicht antwortet."""
    try:
        with urllib.request.urlopen(BASE + path, timeout=3) as r:
            return json.loads(r.read() or b"null")
    except Exception:
        return None


def auftraege():
    """Alle Aufträge aus dem Zustandsverzeichnis, älteste zuerst."""
    out = []
    for f in sorted(os.listdir(STATE)) if os.path.isdir(STATE) else []:
        if not f.endswith(".env"):
            continue
        d = {}
        for line in open(os.path.join(STATE, f)):
            k, _, v = line.rstrip("\n").partition("=")
            d[k] = v
        d["_mtime"] = os.path.getmtime(os.path.join(STATE, f))
        out.append(d)
    return sorted(out, key=lambda d: d["_mtime"])


def zustand(a, offen):
    """Liefert (Symbol, Farbe, Text, letzte Aktivität, Alter in Minuten)."""
    q = urllib.parse.quote(a["dir"])
    st = get(f"/session/status?directory={q}") or {}
    busy = a["session"] in st
    msgs = get(f"/session/{a['session']}/message?directory={q}") or []
    letzte, alter = "", None
    for m in reversed(msgs):
        if m["info"]["role"] != "assistant":
            continue
        t = m["info"].get("time", {})
        ts = t.get("completed") or t.get("created")
        if ts and alter is None:
            alter = (time.time() * 1000 - ts) / 60000
        for p in reversed(m.get("parts", [])):
            if p.get("type") == "tool":
                inp = p.get("state", {}).get("input", {})
                ziel = inp.get("command") or inp.get("filePath") or inp.get("description") or inp.get("pattern") or ""
                if not ziel:  # todowrite & Co. ohne Ziel sagen nichts — weiter zurück
                    continue
                letzte = f"{p.get('tool')}: {str(ziel).replace(a['dir'] + '/', '')[:70]}"
                break
        if letzte:
            break
    if a["session"] in offen:
        return "?", YEL, "WARTET AUF ANTWORT", offen[a["session"]], alter
    if busy:
        return "▶", CYA, "läuft", letzte, alter
    return "✓", GRN, "fertig", letzte, alter


def offene_fragen():
    """sessionID → Kurztext der offenen Rückfrage/Rechte-Anfrage."""
    res = {}
    for a in auftraege():
        q = urllib.parse.quote(a["dir"])
        for it in (get(f"/question?directory={q}") or []):
            fr = (it.get("questions") or [{}])[0].get("question", "")
            res[it["sessionID"]] = "Frage: " + fr[:70]
        for it in (get(f"/permission?directory={q}") or []):
            res[it["sessionID"]] = f"Recht: {it.get('permission')} {' '.join(it.get('patterns', []))}"[:80]
    return res


def zeichnen():
    """Ein Bild der Übersicht; gibt die Aufträge in Anzeige-Reihenfolge zurück."""
    liste = auftraege()
    server = get("/doc") is not None
    zeilen = [f"{BOLD}OpenCode-Bauleitung{RST}  {DIM}{BASE}  ·  {time.strftime('%H:%M:%S')}{RST}"]
    if not server:
        zeilen.append(f"{RED}Server läuft nicht.{RST} Start mit einem Auftrag oder: oc status")
    offen = offene_fragen() if server else {}
    zeilen.append("")
    for i, a in enumerate(liste, 1):
        sym, farbe, text, letzte, alter = zustand(a, offen) if server else ("·", DIM, "?", "", None)
        alt = f"{alter:4.0f} min" if alter is not None else "      "
        zeilen.append(f" {BOLD}{i}{RST} {farbe}{sym} {text:<18}{RST} {a.get('title','')[:48]:<48} {DIM}{alt}{RST}")
        if letzte:
            zeilen.append(f"      {DIM}{letzte}{RST}")
    if not liste:
        zeilen.append(f" {DIM}keine Aufträge{RST}")
    zeilen += ["", f"{DIM}q beenden · 1–9 Auftrag als Tab öffnen · Antworten/Korrekturen über Claude{RST}"]
    sys.stdout.write("\033[H\033[2J" + "\n".join(zeilen) + "\n")
    sys.stdout.flush()
    return liste


def main():
    """Hauptschleife: zeichnen, 3 s auf Taste warten, wiederholen."""
    sys.stdout.write("\033]0;OC Board\a")
    # `oc board-zu` schickt SIGTERM: sauber beenden, damit Terminal den Tab schließt.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    fd = sys.stdin.fileno()
    alt = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            liste = zeichnen()
            r, _, _ = select.select([sys.stdin], [], [], 3)
            if not r:
                continue
            k = sys.stdin.read(1)
            if k in ("q", "Q"):
                break
            if k.isdigit() and 1 <= int(k) <= len(liste):
                subprocess.run([OC, "open", liste[int(k) - 1]["slug"]], capture_output=True)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, alt)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
