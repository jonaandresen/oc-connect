"""MCP server (stdio) that exposes oc-connect to Claude Code.

Why an MCP server and not only a skill: a skill is loaded when its description
matches, so Claude may never think of OpenCode. The server's tools and its
``instructions`` are present in every session, which is what lets Claude decide
on its own when a task is worth delegating.

Minimal JSON-RPC 2.0 over newline-delimited stdio, standard library only.
Nothing may be printed to stdout except protocol messages.
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout
from typing import Any, Callable, Dict

from . import core, ops
from .core import OcError

_AUTONOMY_RULES = {
    "off": "Never delegate on your own. Only when the user explicitly asks for OpenCode, call `delegate`; "
           "any other call is refused.",
    "ask": "When a task fits, call `delegate` directly - do NOT ask in the chat first: Claude Code shows the "
           "user a confirmation dialog with the job, and the user decides there. If the user declines, do "
           "the work yourself or ask how to proceed; do not retry the same delegation.",
    "mechanical": "Delegate WITHOUT asking when the task is well-bounded implementation work: writing or "
                  "extending tests, mechanical refactors and renames, small UI/frontend changes, docs, "
                  "follow-up fixes with a clear spec. Say in one line that you delegated and to which agent. "
                  "For larger or cross-cutting work, ask first in one short question with your recommendation.",
    "auto": "Delegate on your own judgement whenever a well-specified implementation task would otherwise "
            "cost you significant effort. Say in one line that you delegated and to which agent.",
}


def instructions() -> str:
    setup = core.opencode_setup()
    agents = setup["agents"]
    if setup["profile"] == "isolated":
        agent_line = ("Workers run with oc-connect's own OpenCode profile (no oh-my-opencode, no superpowers; "
                      "session tracker plugins stay). Agents: `oc-fast` for bounded mechanical work, `oc-strong` "
                      "for larger multi-file work, `oc-ui` for interface work. Models: "
                      + ", ".join(f"{k}={v}" for k, v in agents.items()) + ".")
    elif setup["oh_my_opencode"]:
        agent_line = ("Agents (oh-my-opencode): pick per job. `fixer` for small mechanical work (cheapest), "
                      "`designer` for UI, `orchestrator` for larger multi-file work. "
                      "You are the orchestrator: your task already contains the plan, so prefer `fixer` "
                      "unless the work is large. Models: "
                      + ", ".join(f"{k}={v}" for k, v in agents.items()) + ".")
    else:
        agent_line = "Agent: OpenCode's built-in `build` (oh-my-opencode not installed; that is fine)."
    mode = core.autonomy()
    return f"""oc-connect lets you hand implementation tasks to OpenCode workers running on cheaper models.
Each job gets its own git worktree and its own OpenCode process, running in the background (or in a
terminal tab, if the user chose that); `open` shows a worker to the user in a terminal window. Workers cannot commit, push, run docker or ssh.

WHEN TO DELEGATE ({mode}): {_AUTONOMY_RULES[mode]}
The user controls this: a confirmation dialog in modes off and ask, `/oc:mode` switches the mode.
Good fits: bounded implementation with a clear spec you can write down. Bad fits: exploration or
questions (a Claude subagent is cheaper, since you would review the worker's output anyway), anything
touching money, payments, auth, security or data deletion, and tasks with an open product decision.
Treat OpenCode as an alternative to spawning an implementation subagent, not to research subagents.
If the user says "do this with OpenCode" (or similar), always delegate that task.

HOW: 1) Research first and write a precise task (files with lines, required behaviour, tests, what must
not change, the project's check commands). The skill `oc:delegate` has the full template - load it
before your first delegation in a session. 2) `delegate` - it returns at once; the worker
starts in the background. 3) The oc-connect monitor notifies you: running, questions (with an id),
finished, failed, stalled, over budget, stopped. Do not poll. Only jobs owned by THIS session are
reported; leave jobs of other live Claude sessions alone unless the user says otherwise.
4) Answer questions yourself when code or task settle them (`answer` with the question id). Product,
legal or money questions go to the user - send a push notification if they may be away.
5) When finished: `report`, then REVIEW yourself - read the full diff, run all checks, defeat test,
hunt for untested cases. Corrections go back with `send`. 6) Record the review with `review` (checks
run, defeat test, known gaps). A commit hook blocks commits in a job worktree until the review for
exactly that state is recorded. 7) Commit, push, open the PR; then `set_pr` and `close`. After merge:
`done` (it refuses while unpushed work exists).
The worker's result lies in the worktree uncommitted, so `report`, `send`, `review` and `done` each
snapshot it first. If a review step, a correction or a wrong git command destroys the result, get it
back with `restore` (`snapshots` lists the states) instead of asking the worker to do the work again.
{agent_line}"""


def _slug_schema(desc: str = "job slug, e.g. issue number or short name") -> Dict[str, Any]:
    return {"type": "string", "description": desc}


TOOLS: Dict[str, Dict[str, Any]] = {
    "delegate": {
        "description": "Start an OpenCode worker on a task: new git worktree + branch, own OpenCode process, "
                       "OpenCode session, task sent. Returns immediately; the monitor reports progress.",
        "inputSchema": {"type": "object", "required": ["slug", "task"], "properties": {
            "slug": _slug_schema("short id used for worktree and branch, e.g. '276' or 'tests-cart'"),
            "task": {"type": "string", "description": "complete task text for the worker (markdown)"},
            "title": {"type": "string", "description": "session title, e.g. '#276 Show orders only after payment'"},
            "agent": {"type": "string", "description": "worker agent: oc-fast (default), oc-strong, oc-ui - "
                                                       "see the setup tool"},
            "model": {"type": "string", "description": "provider/model override; normally leave empty"},
            "branch": {"type": "string", "description": "branch name (default oc/<slug>)"},
            "base": {"type": "string", "description": "git ref to start from (default origin/HEAD)"},
            "repo": {"type": "string", "description": "repository path (default: current project)"},
        }},
    },
    "status": {
        "description": "State of all OpenCode jobs (running / waiting / done / stopped), with "
                       "last activity, diff size, cost and open questions.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    "report": {
        "description": "Final message, cost and git status of a job. Read this, then review the diff yourself.",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {"slug": _slug_schema()}},
        "annotations": {"readOnlyHint": True},
    },
    "send": {
        "description": "Send a follow-up message (e.g. review findings to fix) into the job's session. "
                       "A stopped worker (closed by hand or when idle) is reopened in the same session first.",
        "inputSchema": {"type": "object", "required": ["slug", "message"], "properties": {
            "slug": _slug_schema(), "message": {"type": "string"}}},
    },
    "answer": {
        "description": "Answer a worker's open question. Pass the question id from the monitor or `status`; "
                       "one answer per sub-question, in order; use an option label or free text.",
        "inputSchema": {"type": "object", "required": ["slug", "question_id", "answers"], "properties": {
            "slug": _slug_schema(), "question_id": {"type": "string"},
            "answers": {"type": "array", "items": {"type": "string"}}}},
    },
    "permit": {
        "description": "Answer a worker's permission request: once, always or reject. Reject anything "
                       "the guard would forbid in spirit.",
        "inputSchema": {"type": "object", "required": ["slug", "reply"], "properties": {
            "slug": _slug_schema(), "reply": {"type": "string", "enum": ["once", "always", "reject"]},
            "request_id": {"type": "string", "description": "id from the monitor or status"}}},
    },
    "open": {
        "description": "Show the job's worker to the user in a terminal window (restarts a stopped worker, same session).",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {"slug": _slug_schema()}},
    },
    "close": {
        "description": "Stop a job's worker (its window closes). Worktree and session stay. "
                       "Do this once the PR exists and no correction is pending.",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {"slug": _slug_schema()}},
    },
    "done": {
        "description": "Stop the worker and remove the job's worktree (branch stays). Use after merge. "
                       "Refuses while uncommitted or unpushed work exists unless force is true.",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {
            "slug": _slug_schema(), "force": {"type": "boolean", "description": "discard unsaved work"}}},
        "annotations": {"destructiveHint": True},
    },
    "review": {
        "description": "Record your review of a finished job before committing it: which checks you ran "
                       "with what result, your defeat test, and the gaps you know of. Tied to the exact "
                       "worktree state; a commit hook blocks commits until it is recorded.",
        "inputSchema": {"type": "object", "required": ["slug", "checks", "defeat_test", "gaps"], "properties": {
            "slug": _slug_schema(),
            "checks": {"type": "string", "description": "commands run and their results"},
            "defeat_test": {"type": "string", "description": "what you broke and which test turned red"},
            "gaps": {"type": "string", "description": "what is not covered or not verified"}}},
    },
    "snapshots": {
        "description": "List the automatic snapshots of a job's worktree (taken by report, send, review "
                       "and done). Use before `restore` to pick a state.",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {"slug": _slug_schema()}},
        "annotations": {"readOnlyHint": True},
    },
    "restore": {
        "description": "Put a snapshot of the worker's result back into the job's worktree - after a bad "
                       "correction round, a wrong `git checkout` or any other loss. The current state is "
                       "snapshotted first, so this is reversible; the recorded review is dropped.",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {
            "slug": _slug_schema(),
            "commit": {"type": "string", "description": "snapshot from `snapshots` (default: the newest)"}}},
    },
    "set_pr": {
        "description": "Record the pull request of a job so status views show it.",
        "inputSchema": {"type": "object", "required": ["slug", "pr"], "properties": {
            "slug": _slug_schema(), "pr": {"type": "string", "description": "e.g. '#303'"}}},
    },
    "setup": {
        "description": "Show the local OpenCode setup: version, plugins, agents with models, default agent.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
}


def _project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _job_lines(r: Dict[str, Any], me: Any) -> str:
    """One job as compact text.

    Deliberately not JSON: the same facts cost a fraction of the tokens, and
    every status call goes into Claude's context. Everything Claude needs to
    decide the next step is here - state, progress, where the code is, what is
    open - the rest is one `report` away.
    """
    owner = r.get("owner")
    who = ("" if owner == me else
           "  OWNED BY ANOTHER LIVE CLAUDE SESSION - leave it alone" if r.get("owner_alive") else "  (no owner)")
    bits = []
    if r.get("todo_total"):
        bits.append(f"{r.get('todo_done', 0)}/{r['todo_total']} todos")
    d = r.get("diff") or {}
    if d.get("files"):
        bits.append(f"{d['files']} files +{d['plus']} -{d['minus']}")
    if r.get("cost"):
        bits.append(f"${float(r['cost']):.2f}")
    age = core.minutes_since(r.get("created"))
    if age is not None:
        bits.append(f"{age:.0f}m old")
    if r.get("reviewed"):
        bits.append("reviewed")
    if r.get("pr"):
        bits.append(f"PR {r['pr']}")
    lines = [f"{r['slug']} [{r['state']}] {r.get('agent') or ''} {r.get('title') or ''}".rstrip() + who,
             f"  {r.get('dir')} ({r.get('branch')})" + (("  " + ", ".join(bits)) if bits else "")]
    if r.get("activity") and r["state"] in ("running", "waiting"):
        lines.append(f"  last: {r['activity']}")
    if r.get("error"):
        lines.append(f"  ERROR: {r['error']}")
    if r.get("closed") and r["state"] == "stopped":
        lines.append(f"  closed automatically: {r['closed']} (`send` or `open` restarts it)")
    for p in r.get("pending", []):
        opts = f" [options: {' | '.join(p.get('options') or [])}]" if p.get("options") else ""
        lines.append(f"  {p['kind'].upper()} [{p['id']}]: {p['text']}{opts}")
    return "\n".join(lines)


def _status_text(_: Dict[str, Any]) -> str:
    rows = ops.status_all(detail=True)
    if not rows:
        return "no jobs"
    me = core.claude_pid()
    return "\n".join(_job_lines(r, me) for r in rows)


def _setup_text(_: Dict[str, Any]) -> str:
    return json.dumps(core.opencode_setup(), indent=1)


HANDLERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "delegate": lambda a: ops.start(a["slug"], a["task"], title=a.get("title"), branch=a.get("branch"),
                                       agent=a.get("agent"), repo=a.get("repo"), base=a.get("base"),
                                       cwd=_project_dir(), model=a.get("model"), background=True),
    "status": _status_text,
    "report": lambda a: ops.report(a["slug"]),
    "send": lambda a: ops.send(a["slug"], a["message"]),
    "answer": lambda a: ops.answer(a["slug"], a["answers"], a.get("question_id")),
    "permit": lambda a: ops.permit(a["slug"], a["reply"], a.get("request_id")),
    "open": lambda a: ops.open_(a["slug"]),
    "close": lambda a: ops.close(a["slug"]),
    "done": lambda a: ops.done(a["slug"], bool(a.get("force"))),
    "review": lambda a: ops.review(a["slug"], a["checks"], a["defeat_test"], a["gaps"]),
    "snapshots": lambda a: ops.snapshots(a["slug"]),
    "restore": lambda a: ops.restore(a["slug"], a.get("commit")),
    "set_pr": lambda a: ops.set_pr(a["slug"], a["pr"]),
    "setup": _setup_text,
}


def handle(msg: Dict[str, Any]) -> Any:
    """Return the JSON-RPC result for a request, or raise."""
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "opencode", "version": core.VERSION},
                "instructions": instructions()}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [dict(name=n, **spec) for n, spec in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in HANDLERS:
            return {"isError": True, "content": [{"type": "text", "text": f"unknown tool {name}"}]}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):  # never let a stray print corrupt the protocol
                text = HANDLERS[name](params.get("arguments") or {})
            return {"content": [{"type": "text", "text": text}]}
        except OcError as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"ERROR: {exc}"}]}
        except KeyError as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"missing argument {exc}"}]}
        except Exception as exc:  # report, do not crash the server
            traceback.print_exc(file=sys.stderr)
            return {"isError": True, "content": [{"type": "text", "text": f"ERROR: {type(exc).__name__}: {exc}"}]}
    raise LookupError(method)


def main() -> None:
    core.migrate_legacy()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:        # notification, e.g. notifications/initialized
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "method not found"}}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(exc)}}
        out.write(json.dumps(reply) + "\n")
        out.flush()
