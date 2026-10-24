# oc-connect

**Let Claude Code hand coding tasks to [OpenCode](https://opencode.ai) - and review every line before it is committed.**

Claude does the thinking: it plans, writes a precise task and reviews the result. OpenCode does the typing, on a cheaper or free model, in its own git worktree and its own terminal window you can watch.

[![Release](https://img.shields.io/github/v/release/jonaandresen/oc-connect?sort=semver)](https://github.com/jonaandresen/oc-connect/releases)
[![OpenCode](https://img.shields.io/badge/OpenCode-1.x-orange)](https://opencode.ai)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<p align="center"><img src="docs/demo.gif" alt="Claude Code delegates a task to an OpenCode worker, the worker implements it in its own window, Claude reviews the diff, breaks the code on purpose to see the tests fail, and lists the gaps it found" width="900"></p>
<p align="center"><sub>A real run at 2× speed: Claude delegates, you confirm, an OpenCode worker on a free model implements it, Claude reviews - including its own defeat test - and names the gaps.</sub></p>

## Install

> **This is oc-connect 1.x for OpenCode 1.** For OpenCode 2 use the [main branch](https://github.com/jonaandresen/oc-connect).

You need [Claude Code](https://claude.com/claude-code), [OpenCode 1](https://opencode.ai) (1.18 or newer) with a model set up, `git` and `python3` (3.9+, ships with macOS).

Inside Claude Code, run:

```
/plugin marketplace add jonaandresen/oc-connect#v1
/plugin install oc@oc-connect
```

Restart Claude Code, then run **`/oc:setup`**. It checks OpenCode, proves that workers cannot push, and offers to show the job rows under your status line.

<details>
<summary><strong>Prefer the terminal?</strong></summary>

```bash
claude plugin marketplace add jonaandresen/oc-connect#v1
claude plugin install oc@oc-connect
```

</details>

<details>
<summary><strong>OpenCode 1 and 2 both installed?</strong></summary>

Point `OC_OPENCODE` to the OpenCode 1 binary - see [configuration](docs/configuration.md).

</details>

**Update:** `claude plugin marketplace update oc-connect && claude plugin update oc@oc-connect`, then restart Claude Code.

## Use

Just ask:

> Do this with OpenCode: add input validation to the signup form, with tests.

Claude writes the task, you confirm in a dialog, a worker opens in its own window. When it finishes, Claude reads the full diff, runs the checks, breaks the code on purpose to see the tests catch it, and tells you what is still missing. Nothing is committed before that.

| Command | |
|---|---|
| `/oc:status` | All jobs |
| `/oc:show <job>` | Bring a job's window to the front |
| `/oc:stop <job>` | Stop a worker (its work stays) |
| `/oc:board` | Live board in a terminal |
| `/oc:mode` | When Claude may delegate: `ask` (default), `off`, `mechanical`, `auto` |
| `/oc:setup` | Check the setup again |

## What it does

- **You stay in charge.** Every delegation needs your OK by default - enforced by a hook, not by the model's good will.
- **One job, one window.** Each job gets its own git worktree, terminal window and OpenCode process. Run several at once.
- **Live status.** Rows under your prompt show each job's progress, diff and cost; Claude is notified the moment a worker asks, finishes or fails.
- **Questions handled.** Claude answers what the code settles and only passes the real decisions on to you.
- **Workers cannot ship.** They cannot commit, push, merge or run docker - two layers, proven by `oc selftest`.
- **Review is enforced.** A commit in a job's worktree is blocked until Claude has recorded its review of exactly those files.
- **Nothing gets lost.** Every result is snapshotted; one command puts it back.
- **Clean workers.** Workers run with their own OpenCode setup, without your interactive plugins; your OpenCode stays untouched.

## How it works

```
you ──► Claude Code (lead) ──task──► OpenCode worker  (own window, own worktree)
            ▲  reviews diff, runs checks,          │
            │  defeat test, finds gaps             │ question? done? failed?
            └──────── notification ◄───────────────┘
```

More: [how it works in detail](docs/how-it-works.md) · [configuration](docs/configuration.md) · [changelog](CHANGELOG.md)

## Development

```bash
git clone https://github.com/jonaandresen/oc-connect && cd oc-connect
python3 -m unittest discover tests      # fake OpenCode server, no model needed
claude --plugin-dir .                   # try the working copy
```

Notes for contributors - what was measured and what is still open - are in [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE) © Jona Andresen
