# oc — Claude Code als Bauleitung für OpenCode-Worker

> Arbeitsname. Der endgültige Name steht noch aus.

Claude Code plant, schreibt den Auftrag und prüft. [OpenCode](https://opencode.ai) setzt um, bevorzugt der `orchestrator` aus [oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim) mit seinen Subagenten, auf günstigen Modellen. Du siehst jedem Worker live in seinem eigenen Terminal-Tab zu.

```
du ─► Claude Code (Bauleitung) ──Auftrag──► OpenCode-Orchestrator (Tab „OC 276“, Port 4101)
          ▲   prüft Diff, Tests,                  └─► fixer · explorer · oracle · …
          │   Defeat-Test, Lücken
          └── Rückfragen gehen erst an Claude, nur Unklares an dich
```

*English summary:* Claude Code acts as the lead: it writes a precise task, hands it to an OpenCode worker running in **its own git worktree and its own terminal tab**, answers the worker's questions (escalating only product/legal decisions to you), and **reviews every diff before anything is committed**. Workers are technically blocked from committing, pushing, running docker or ssh.

## Was es anders macht

| | |
|---|---|
| **Offizielle Schnittstelle** | Spricht OpenCodes eigene HTTP-API (`/session`, `prompt_async`, `/question`, `/permission`, `/tui/select-session`). Kein Mitschneiden von Terminal-Ausgabe. |
| **Ein Auftrag = ein worktree = ein Tab = ein Prozess** | Jeder Worker läuft in seinem eigenen Tab als eigener OpenCode-Prozess. Tools wie **Vibe Island** ordnen ihn deshalb dem richtigen Tab zu, und ein Klick springt dorthin. Tab schließen heißt Worker beenden. |
| **Harte Sperren statt Hoffnung** | `oc init` legt eine `opencode.json` mit Sperrliste ins Repo (`git push/commit/merge/stash`, `docker`, `ssh`, `gh pr merge` …) und trägt sie in `.git/info/exclude` ein. `oc selftest` **belegt**, dass die Sperren greifen. Gemessen wird der Status der Werkzeugaufrufe, nicht das, was das Modell behauptet. |
| **Rückfragen erst an Claude** | `oc wait` erkennt offene Fragen und Rechte-Anfragen, zum Beispiel aus superpowers' `brainstorming`. Claude beantwortet, was sich aus Code und Auftrag belegen lässt. Nur Produkt-, Rechts- und Geldfragen gehen an dich. |
| **Prüfpflicht für Claude** | Der Skill macht die Prüfung zur Pflicht: Diff lesen, alle Prüfbefehle laufen lassen, eigener Defeat-Test, gezielt nach Ungetestetem suchen (alt gegen neu, echte Daten, andere Zeitzonen). |
| **Übersicht** | `/oc:board` öffnet eine Live-Übersicht als Tab. Dazu eine Statuszeile `OC 2▶ 1? 3✓` in Claude Code und `/oc:status` im Chat. |
| **Kennt dein Setup** | `oc setup` liest OpenCode-Plugins, oh-my-opencode-slim-Agenten mit Modellen, superpowers- und lokale Skills. Claude schreibt den Auftrag passend dazu. |

## Voraussetzungen

- macOS (Terminal.app, iTerm2, WezTerm, kitty oder tmux), `bash`, `python3`, `curl`, `git`
- [OpenCode](https://opencode.ai) ≥ 1.18 mit einem eingerichteten Anbieter
- Empfohlen: oh-my-opencode-slim (Orchestrator) und superpowers
- **Terminal.app:** Neue Tabs brauchen die Freigabe *Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen → Terminal*. Ohne sie öffnet `oc` Fenster statt Tabs.

## Installation

```bash
# als Marketplace (nach Veröffentlichung)
claude plugin marketplace add <github-user>/<repo>
claude plugin install oc@oc-plugin

# lokal zum Entwickeln
git clone <repo> ~/Documents/oc-plugin
ln -s ~/Documents/oc-plugin ~/.claude/skills/oc     # lädt als oc@skills-dir
```

Einmal je Repo, in Claude Code: „richte oc für dieses Repo ein“. Claude führt dann `oc setup`, `oc init` und `oc selftest` aus.

### Statuszeile (optional)

Claude Code erlaubt nur einen `statusLine`-Befehl. Der Wrapper hängt die OC-Zeile an deine bestehende an:

```json
"statusLine": {
  "type": "command",
  "command": "OC_STATUSLINE_BASE=<dein-bisheriger-befehl> ~/.claude/skills/oc/scripts/oc-statusline-wrap"
}
```

## Benutzung

| In Claude Code | Wirkung |
|---|---|
| „gib #276 an OpenCode“ / `/oc:auftrag #276` | Claude recherchiert, schreibt den Auftrag, startet Worker und Tab, wartet im Hintergrund, prüft und legt den PR an |
| `/oc:status` | Stand aller Aufträge im Chat |
| `/oc:board` · `/oc:board-zu` | Live-Übersicht als Tab öffnen und schließen (`1–9` öffnet einen Auftrag) |
| `/oc:zeigen <auftrag>` | Tab eines Auftrags öffnen, startet den Worker neu, falls er beendet war |
| `/oc:schliessen <auftrag>` | Worker beenden, der Tab schließt sich. worktree und Sitzung bleiben |

Das Skript `scripts/oc` lässt sich auch direkt nutzen: `oc` ohne Argument zeigt alle Befehle.

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Wofür |
|---|---|---|
| `OC_REPO` | Haupt-worktree des aktuellen Repos | Welches Repo |
| `OC_WT_ROOT` | `<repo>-worktrees` | Wo die worktrees entstehen |
| `OC_BASE_REF` | `origin/HEAD` | Ausgangsstand neuer worktrees |
| `OC_COPY` | `.env backend/.env frontend/.env` | Unversionierte Dateien, die jeder worktree braucht |
| `OC_AGENT` | `orchestrator` mit oh-my-opencode-slim, sonst `build` | An wen Aufträge gehen |
| `OC_TEST_MODEL` | `opencode-go/deepseek-v4.1-flash` | Billiges Modell für `oc selftest` |
| `OC_TERMINAL` | `$TERM_PROGRAM` | Terminal erzwingen |

## Grenzen

- Oberfläche und Texte sind bisher deutsch.
- Tabs öffnen ist auf macOS-Terminals und tmux zugeschnitten. Linux funktioniert über tmux, WezTerm oder kitty.
- Abhängigkeiten installiert `oc` nur mit `npm ci`. pnpm und yarn fehlen noch.
- Die Prüfung in Schritt 5 ist **Claudes** Pflicht und nicht automatisiert. Genau dort wurden im Einsatz die Fehler gefunden, die die Tests der Worker übersehen hatten.

## Lizenz

MIT
