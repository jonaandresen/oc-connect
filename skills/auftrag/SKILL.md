---
name: auftrag
description: Delegiert eine abgegrenzte Coding-Aufgabe an einen OpenCode-Worker (bevorzugt den Orchestrator von oh-my-opencode-slim) — eigener git worktree, eigener Terminal-Tab mit eigenem OpenCode-Prozess (sichtbar in Vibe Island & Co.), Rückfragen erst an Claude, Prüfung durch Claude vor jedem Commit. Verwenden bei „gib das an OpenCode“, „lass den Orchestrator …“, „/oc:auftrag“, „verteil das an die Worker“, oder wenn Aufgaben parallel abgearbeitet werden sollen.
---

# OpenCode-Auftrag

Claude ist **Bauleitung**, der OpenCode-Worker ist **Vorarbeiter**. Claude schreibt den Auftrag, der Worker setzt um, der Mensch sieht live zu, Claude prüft und committet. Der Worker committet, pusht und deployt nie — die `opencode.json` im Repo sperrt das technisch (`$OC init`, Gegenprobe `$OC selftest`).

Werkzeug: `OC=${CLAUDE_PLUGIN_ROOT}/scripts/oc` (Hilfe: `$OC` ohne Argument).

## Vor dem ersten Auftrag in einem Repo

1. `$OC setup` — zeigt, wie OpenCode eingerichtet ist (Plugins, Agenten mit Modellen, superpowers-Skills, lokale Skills). Den Auftrag an DIESES Setup anpassen, nicht an Annahmen.
2. `$OC init` — legt die Sperr-Config `opencode.json` ins Repo und trägt sie in `.git/info/exclude` ein (wird nie committet).
3. `$OC selftest` — belegt mit einem billigen Modell, dass `git push` und `docker` wirklich abgelehnt werden. Ergebnis wird am STATUS der Werkzeugaufrufe gemessen, nicht am Text des Modells.
4. Die Projektregeln lesen (`CLAUDE.md`, `AGENTS.md`, Handbuch): Welche Pfade sind kritisch? Welche Tests teilen sich Ressourcen?

## Wenn oh-my-opencode-slim und superpowers installiert sind

- Aufträge gehen an den Agenten **`orchestrator`**; er verteilt selbst an `fixer`, `explorer`, `librarian`, `oracle`, `designer`, `observer`. Nicht einzelne Subagenten ansprechen.
- **superpowers greift aktiv ein — im Auftrag abfangen:**
  - `brainstorming` („MUST use before any creative work“) führt zu Rückfragen und Warten auf Freigabe. Im Auftrag: *Anforderungen sind abgestimmt und freigegeben; kein Brainstorming, kein Design-Dokument. Nur bei echter fachlicher Unklarheit eine konkrete Frage.*
  - `using-git-worktrees` bzw. omo `worktrees` würden einen weiteren worktree anlegen. Im Auftrag: *Du bist bereits in einem isolierten worktree; keinen weiteren anlegen.* (`git worktree*` ist zusätzlich gesperrt.)
  - `finishing-a-development-branch` will mergen/committen. Im Auftrag: *Integration macht der Auftraggeber; diesen Schritt auslassen.*
  - `test-driven-development`, `verification-before-completion`, `systematic-debugging` passen — ausdrücklich erlauben.
  - `deepwork` (omo) ist teuer — nur für große, mehrphasige Vorhaben.

## Wann nicht delegieren

- Kritische Pfade (Geld, Zahlungen, Rechnungen, Sicherheit, Datenlöschung) — selbst machen oder nur mit sehr engem Auftrag.
- Aufgaben mit offener Produkt- oder Rechtsentscheidung — erst den Menschen fragen.
- Mehrere Aufträge, deren Tests dieselbe Ressource brauchen (z. B. eine gemeinsame Test-Datenbank) — nacheinander, nicht parallel. Parallel gehen Aufgaben ohne geteilte Ressourcen.
- Aufgaben, deren Tests Dienste brauchen, die der Worker nicht starten darf (`docker` ist gesperrt): Claude startet sie vorher.

## Ablauf

1. **Vorarbeit:** Issue lesen, Code finden, Soll klären. Der Auftrag nennt Dateien mit Zeilen und Belege.
2. **Auftrag** nach der Vorlage unten in eine Datei (Scratchpad) schreiben.
3. **Starten:** `$OC start <slug> <branch> "<#nr Titel>" <auftrag.md>` — worktree vom Standard-Branch, `.env`-Dateien kopiert (`OC_COPY`), `npm ci`, **eigener Tab mit eigenem OpenCode-Prozess** (Port 4101…), Sitzung, Auftrag. Dem Menschen den Browser-Link nennen. Titel beginnt mit der Issue-Nummer.
4. **Warten:** `$OC wait <slug>` mit `run_in_background: true`.
   - Exit 3 = Worker beendet (Tab geschlossen) → `$OC open <slug>` startet ihn mit derselben Sitzung neu.
   - **Exit 2 = Rückfrage oder Rechte-Anfrage offen. Fragen gehen ERST an Claude, NUR wenn Claude sie nicht belastbar beantworten kann, an den Menschen:**
     1. Selbst beantworten, wenn die Antwort im Auftrag, im Code, in den Projektregeln oder im Issue steht oder rein technisch ist: `$OC answer <slug> "<Antwort mit kurzem Beleg>"`. Rechte-Anfragen: `$OC permit <slug> once|reject` — `reject` bei allem, was die Sperrliste sinngemäß verbietet.
     2. An den Menschen, wenn es eine Produkt-, Rechts- oder Geldentscheidung ist oder die Belege sich widersprechen: `PushNotification` (Titel „OC <slug> fragt“) UND im Chat per `AskUserQuestion`, mit Empfehlung als erster Option. Die Antwort dann mit `$OC answer` weitergeben.
     3. Nie raten, nur damit der Worker weiterläuft. Der Mensch sieht Fragen auch in Vibe Island & Co.; er antwortet dort nur, wenn Claude sie an ihn weitergereicht hat — sonst entstehen zwei Antworten.
     Danach wieder `$OC wait <slug>` im Hintergrund.
5. **Prüfen (Pflicht, nicht delegierbar):**
   - Diff vollständig lesen, nicht nur die Abschlussmeldung.
   - Die Prüfbefehle des Projekts selbst laufen lassen — alle, auch die, die der Auftrag nicht nannte (Typprüfung!).
   - Eigener Defeat-Test an der Kernstelle.
   - **Gezielt suchen, was NICHT getestet ist:** Verhalten für alle anderen Fälle unverändert? Geänderte Hilfsfunktion alt (`git show <basis>:<datei>`) gegen neu über viele Eingaben vergleichen. Echte Daten ansehen, ob betroffene Konstellationen existieren. Tests unter anderen Rahmenbedingungen laufen lassen (z. B. `TZ=…`). Kommentare und Doku auf Richtigkeit, nicht nur auf Vorhandensein.
6. **Korrektur:** `$OC send <slug> <korrektur.md>` in dieselbe Sitzung — Befund mit Beleg, was zu tun ist, Defeat-Test verlangen. Dann wieder `$OC wait`. Kleinigkeiten (Kommentar, eine Zeile) schneller selbst beheben.
7. **Abschluss:** commit nach den Konventionen des Projekts, push, PR mit Testanleitung und ehrlich markierten Lücken. `opencode.json` NIE committen. Danach `$OC close <slug>` (Worker beendet, Tab schließt sich).
8. **Aufräumen** nach dem Merge: `$OC done <slug>` (worktree weg, Branch bleibt).

Übersicht: `/oc:status` im Chat, `/oc:board` als Live-Tab (`/oc:board-zu` schließt), Statuszeile `OC 2▶ 1? 3✓` (siehe README).

## Vorlage Auftrag

```markdown
Auftrag: <Issue #nr „Titel“ umsetzen | Tests für …>.

Du arbeitest im git worktree <pfad> (Branch <branch>) — bereits isoliert, keinen weiteren worktree anlegen. Lies zuerst <CLAUDE.md / AGENTS.md / Handbuch-Kapitel>; die Konventionen dort sind verbindlich.

Die Anforderungen unten sind abgestimmt und freigegeben: kein Brainstorming, kein Design-Dokument, kein finishing-a-development-branch. TDD und verification-before-completion ausdrücklich erwünscht. Nur bei echter fachlicher Unklarheit eine konkrete Frage (question-Werkzeug).

## Das Problem / Issue
## Was heute passiert (von mir nachgesehen) — Dateien:Zeilen, Belege
## Soll-Verhalten / Anforderungen — nummeriert, prüfbar; was UNVERÄNDERT bleiben muss
## Tests — Pflichtfälle; Bestand nicht passend biegen; angepasste Zusicherungen einzeln begründen; Defeat-Test von Hand, ohne git stash
## Verboten (technisch gesperrt)
Kein git commit/push/stash, kein npm install, kein docker, kein ssh, keine Kommentare auf GitHub, keine DB-Resets.
## Prüfen vor der Fertigmeldung — ALLE Prüfbefehle des Projekts (inkl. Typprüfung)
## Abschlussmeldung
Geänderte Dateien, gewählter Weg mit Begründung, ALLE geprüften Stellen (auch ohne Änderung), Prüfergebnisse, Defeat-Tests, bewusst nicht Gemachtes, Unsicherheiten.
```

## Erfahrungen aus dem Einsatz

- Worker sind schnell und formal sauber (Kommentare, Tests, Doku). Ihre Schwäche: „nichts anderes kaputt“ prüfen sie nur mit den vorhandenen Tests. Lücken fielen erst durch Schritt 5 auf — eine gestrichene Anzeige bei 14 echten Datensätzen, Tests, die nur in zwei von vier Zeitzonen grün waren, eine nicht gelaufene Typprüfung.
- Wo ein Worker einen echten Fehler fand, hat er ihn korrekt als `it.fails` stehen lassen statt den Test zu verbiegen — die Regel im Auftrag wirkt.
- Ein gemeinsamer Hintergrund-Server (ohne Terminal) ließ in Vibe Island alle Aufträge als eine Sitzung erscheinen — deshalb ein Prozess je Tab.
- Terminal.app lässt beendete Tabs mit ihrer Geräte-Kennung stehen; Vibe Island sprang dann in den toten Tab. `oc` schließt tote „OC…“-Tabs selbst.
