---
name: delegate
description: Hand a bounded coding task (issue number or description) to an OpenCode worker - own git worktree, own terminal tab, questions come to Claude first, Claude reviews the diff before anything is committed. Use for "do this with OpenCode", "give this to OpenCode", "/oc:delegate", "let the workers do it", or when Claude decides a well-specified implementation task is worth delegating.
argument-hint: "<#issue | task description>"
---

# Delegate to OpenCode

Claude is the **lead**, the OpenCode worker is the **builder**. Claude researches and writes the task, the worker implements, the human can watch live, Claude reviews and commits. The worker never commits, pushes or deploys: the guard config blocks it technically (`oc selftest` proves it).

Tools: the oc-connect MCP tools (`delegate`, `status`, `report`, `send`, `answer`, `permit`, `open`, `close`, `done`, `set_pr`, `setup`). CLI with the same functions: `oc` (`oc --help`).

Task for this invocation: $ARGUMENTS

## Before the first job in a repository

1. `setup` tool: what OpenCode offers here - agents and their models, plugins. Write the task for THIS setup.
2. Once per machine: `oc selftest` (Bash) proves that `git push` and `docker` are rejected. The result is judged by the status of the tool calls, not by the model's text.
3. Read the project rules (`CLAUDE.md`, `AGENTS.md`, contributor docs): critical paths, shared test resources, check commands.

## Should this be delegated?

- **Yes:** bounded implementation with a spec you can write down - tests, mechanical refactors, small UI work, docs, follow-up fixes.
- **No:** exploration and questions (a Claude subagent is cheaper: you would review the worker's output anyway). Money, payments, auth, security, data deletion. Open product or legal decisions (ask the human first). Two jobs whose tests share one resource (e.g. one test database) - run them one after the other.
- Tests that need services the worker may not start (`docker` is blocked): start them before delegating.

## Choosing the agent

Without oh-my-opencode, jobs go to OpenCode's `build` agent. With oh-my-opencode, pick per job and name the choice in one line:

| Work | Agent | Why |
|---|---|---|
| small, mechanical: tests, frontend details, docs | `fixer` | cheapest; your task already is the plan |
| UI and styling | `designer` | set up for it |
| larger, several files, cross-cutting | `orchestrator` | stronger model, may delegate further |

Measured in practice: a fully planned task leaves the `orchestrator` nothing to split, so it did all the work itself on its own model. You are the orchestrator here.

`fixer` and `designer` are OpenCode *subagents*. That is fine: oc-connect sends the agent with every prompt through the API. Only `opencode --agent fixer` on the command line would fail.

## Workflow

1. **Research:** read the issue, find the code, settle what must come out. The task names files with line numbers and evidence.
2. **Write the task** using the template below.
3. **Start:** `delegate` with `slug` (issue number or short name), `title` (starting with the issue number), `task`, and `agent`. This creates the worktree from the remote's default branch, copies git-ignored `.env` files, installs dependencies from lockfiles, opens a new terminal tab with the worker and sends the task. Give the human the browser link from the result.
4. **Panel (recommended):** start the agent `oc:opencode-worker` in the background with the description `oc <slug>` and the prompt `slug: <slug>`. The job then shows in the Claude Code agent panel with progress, diff size and state.
5. **Wait:** do nothing. The oc-connect monitor tells you when the job asks something, finishes or its worker stops. Without a monitor (non-interactive sessions): `oc wait <slug>` in Bash with `run_in_background`.
6. **Questions come to Claude first:**
   - Answer yourself when the task, the code, the project rules or the issue settle it, or when it is purely technical: `answer` with a short reason. Permission requests: `permit` with `once` or `reject` (reject anything the guard forbids in spirit).
   - Forward to the human when it is a product, legal or money decision or the evidence conflicts: ask with your recommendation first, then pass the answer on with `answer`.
   - Never guess just to keep the worker going. The human sees questions in Vibe Island too, but answers there only when you forwarded the question - otherwise two answers collide.
7. **Review (mandatory, never delegated):**
   - `report`, then read the FULL diff in the worktree, not just the worker's summary.
   - Run all of the project's check commands yourself, including the ones the task did not name (type checks!).
   - Your own defeat test at the core change: break the fix, see the test fail.
   - Hunt for what is NOT tested: is behaviour unchanged for all other cases? Compare a changed helper old (`git show <base>:<file>`) against new over many inputs. Look at real data for affected constellations. Run tests under other conditions (another `TZ`). Check that comments and docs are true, not only present.
8. **Corrections:** `send` into the same session - finding with evidence, what to do, ask for a defeat test. Small things (a comment, one line) are faster to fix yourself.
9. **Finish:** commit following the project's conventions, push, open the PR with test instructions and honestly marked gaps. Never commit the worker's `opencode.json`. Then `set_pr` and `close` - you decide when a session ends: once the PR exists and no correction is pending, close it without asking. Idle workers cost nothing but clutter the status line, Vibe Island and the tab bar.
10. **After merge:** `done` removes the worktree (the branch stays).

## Task template

```markdown
Task: <implement issue #nr "title" | tests for ...>.

You work in the git worktree <path> (branch <branch>), already isolated - do not create another worktree. Read <CLAUDE.md / AGENTS.md / docs> first; its conventions are binding.

The requirements below are agreed and approved: no brainstorming, no design document, no finishing-a-development-branch. TDD and verification-before-completion are welcome. Ask (question tool) only on a real functional ambiguity, with a concrete question.

## Problem
## What happens today (checked by me) - files:lines, evidence
## Required behaviour - numbered, testable; what must stay UNCHANGED
## Tests - required cases; do not bend existing tests to pass; justify every changed assertion; manual defeat test without git stash
## Forbidden (technically blocked)
No git commit/push/stash, no npm install, no docker, no ssh, no GitHub comments, no database resets.
## Check before reporting done - ALL of the project's check commands (incl. type checks)
## Final report
Changed files, chosen approach with reason, ALL places checked (also those without changes), check results, defeat tests, what was deliberately not done, uncertainties.
```

**If superpowers is installed in OpenCode**, the template's second paragraph matters: `brainstorming` would otherwise stop and wait for approval, `using-git-worktrees` would create another worktree, and `finishing-a-development-branch` would try to merge.

## Lessons from real jobs

- Workers are fast and formally clean (comments, tests, docs). Their weakness: they check "nothing else broke" only with the tests that already exist. Real gaps showed up only in step 7 - a filter on one of four status values that would have hidden refunded orders, tests that passed in only two of four time zones, a type check nobody ran.
- Where a worker found a real bug, it left it as an expected-failure test instead of bending the test - the rule in the template works.
