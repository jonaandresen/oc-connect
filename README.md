# oc-connect

**Let Claude Code hand coding tasks to [OpenCode](https://opencode.ai) - and review every line before it is committed.**

Claude does the thinking: it plans, writes a precise task and reviews the result. OpenCode does the typing, on a cheaper or free model, in its own git worktree, in the background - open a window on it whenever you want to watch.

[![Release](https://img.shields.io/github/v/release/jonaandresen/oc-connect?sort=semver)](https://github.com/jonaandresen/oc-connect/releases)
[![OpenCode](https://img.shields.io/badge/OpenCode-2.x-orange)](https://opencode.ai)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Built with AI assistance](https://img.shields.io/badge/built%20with-AI%20assistance-8A2BE2)](#ai-assisted-development)

<p align="center"><img src="docs/demo.gif" alt="Claude Code delegates a task to an OpenCode worker, the worker implements it in its own window, Claude reviews the diff, breaks the code on purpose to see the tests fail, and lists the gaps it found" width="900"></p>
<p align="center"><sub>A real run at 2× speed: Claude delegates, you confirm, an OpenCode worker on a free model implements it, Claude reviews - including its own defeat test - and names the gaps.</sub></p>

## Install

You need [Claude Code](https://claude.com/claude-code), [OpenCode 2](https://opencode.ai) with a model set up, `git` and `python3` (3.9+, ships with macOS).

Inside Claude Code, run:

```
/plugin marketplace add jonaandresen/oc-connect
/plugin install oc@oc-connect
```

Restart Claude Code, then run **`/oc:setup`**. It checks OpenCode, proves that workers cannot push, and offers to show the job rows under your status line.

<details>
<summary><strong>Prefer the terminal?</strong></summary>

```bash
claude plugin marketplace add jonaandresen/oc-connect
claude plugin install oc@oc-connect
```

</details>

<details>
<summary><strong>Still on OpenCode 1?</strong></summary>

Use oc-connect 1.x - the major version follows OpenCode's:

```bash
claude plugin marketplace add jonaandresen/oc-connect#v1
claude plugin install oc@oc-connect
```

</details>

**Update:** `claude plugin marketplace update oc-connect && claude plugin update oc@oc-connect`, then restart Claude Code.

**Uninstall:** `claude plugin uninstall oc@oc-connect` - finish open jobs first; removing the state and the status line entry is described in [configuration](docs/configuration.md#uninstall).

## Use

Just ask:

> Do this with OpenCode: add input validation to the signup form, with tests.

Claude writes the task, you confirm in a dialog, a worker starts in the background (`/oc:show` lets you watch it). When it finishes, Claude reads the full diff, runs the checks, breaks the code on purpose to see the tests catch it, and tells you what is still missing. Nothing is committed before that.

| Command | |
|---|---|
| `/oc:status` | All jobs |
| `/oc:show <job>` | Open a window on a job's worker |
| `/oc:stop <job>` | Stop a worker (its work stays) |
| `/oc:board` | Live board in a terminal |
| `/oc:mode` | When Claude may delegate: `ask` (default), `off`, `mechanical`, `auto` |
| `/oc:setup` | Check the setup again |

## What it does

- **You stay in charge.** Every delegation needs your OK by default - enforced by a hook, not by the model's good will.
- **One job, one worktree.** Each job gets its own git worktree and OpenCode process, in the background or in its own window. Run several at once.
- **Live status.** Rows under your prompt show each job's progress, diff and cost; Claude is notified the moment a worker asks, finishes or fails.
- **Questions handled.** Claude answers what the code settles and only passes the real decisions on to you.
- **Workers cannot ship.** They cannot commit, push, merge or run docker - two layers, proven by `oc selftest`.
- **Review is enforced.** A commit in a job's worktree is blocked until Claude has recorded its review of exactly those files.
- **Nothing gets lost.** Every result is snapshotted; one command puts it back.
- **Clean workers.** Workers run with their own OpenCode setup, without your interactive plugins; your OpenCode stays untouched.

## How it works

```
you ──► Claude Code (lead) ──task──► OpenCode worker  (own worktree, background)
            ▲  reviews diff, runs checks,          │
            │  defeat test, finds gaps             │ question? done? failed?
            └──────── notification ◄───────────────┘
```

More: [how it works in detail](docs/how-it-works.md) · [configuration](docs/configuration.md) · [changelog](CHANGELOG.md)

## Privacy

oc-connect sends nothing anywhere itself and has no telemetry. It talks only to the OpenCode server it starts on your machine. The worker sends the task and the code it reads to the model provider you set up in OpenCode - the same as using OpenCode yourself. Git-ignored `.env` files are copied into the job's worktree so the code can run, which means the worker can read them.

It writes job state to `~/.local/state/oc-connect`, a worktree, branch and snapshot ref per job to your repository, and `~/.claude/settings.json` only if you accept the status line in `/oc:setup`. Your OpenCode and git config stay untouched. The full list: [what oc-connect writes](docs/configuration.md#what-oc-connect-writes).

## Development

```bash
git clone https://github.com/jonaandresen/oc-connect && cd oc-connect
scripts/check.sh            # unit tests (fake OpenCode server, no model) + plugin validation -> RESULT: GREEN
claude --plugin-dir .       # try the working copy
```

Project layout, the live selftest, rules for changes and releasing: [docs/development.md](docs/development.md). What was measured about OpenCode and Claude Code, and what is still open: [docs/measured.md](docs/measured.md).

## AI-assisted development

oc-connect is built with Claude Code: code, tests and docs are written in collaboration with AI. Every change passes `scripts/check.sh`, every claim about OpenCode or Claude Code was measured before it went in ([docs/measured.md](docs/measured.md)), and releases are checked end to end with real OpenCode workers. Issues and pull requests are welcome.

## License

[MIT](LICENSE) © Jona Andresen
