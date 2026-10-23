"""Command line interface: ``oc <command>``.

The same operations are exposed to Claude through the MCP server (``oc mcp``);
the CLI exists for humans, for sessions without the MCP server, and for scripting.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import core, ops
from .core import OcError

STATE_ICON = {"running": "▶", "waiting": "?", "done": "✓", "failed": "✗", "stopped": "■", "starting": "…"}


def _read_task(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    with open(arg) as fh:
        return fh.read()


def cmd_status(args: argparse.Namespace) -> int:
    rows = ops.status_all(detail=args.json or args.long)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no jobs")
        return 0
    for st in rows:
        prog = f"{st.get('todo_done')}/{st.get('todo_total')}" if st.get("todo_total") else ""
        pr = f"PR {st['pr']}" if st.get("pr") else ""
        print(f"{STATE_ICON.get(st['state'], ' ')} {st['slug']:<20} {st['state']:<8} {prog:>6} "
              f"{str(st.get('agent') or ''):<12} {st['title'][:50]} {pr}".rstrip())
        if args.long:
            if st.get("activity"):
                print(f"    last: {st['activity']}")
            d = st.get("diff") or {}
            if d.get("files"):
                print(f"    diff: {d['files']} files +{d['plus']} -{d['minus']}   cost ${st.get('cost', 0):.2f}")
        for p in st.get("pending", []):
            print(f"    {p['kind'].upper()} [{p['id']}]: {p['text'][:100]}")
        if st.get("error") and st["state"] == "failed":
            print(f"    ERROR: {st['error']}")
    return 0


def cmd_setup(_: argparse.Namespace) -> int:
    s = core.opencode_setup()
    print(f"opencode: {s['opencode'] or 'NOT FOUND'} {s['version'] or ''}".rstrip())
    if (core.opencode_major(s["version"]) or 0) >= 2:
        print("WARNING: " + core.OPENCODE_2_UNSUPPORTED.format(version=s["version"]))
    print(f"plugins: {', '.join(s['plugins']) or 'none'}")
    print(f"oh-my-opencode: {'yes' if s['oh_my_opencode'] else 'no (optional)'}"
          f"   superpowers: {'yes' if s['superpowers'] else 'no'}")
    if s["agents"]:
        print("agents:")
        for name, model in s["agents"].items():
            print(f"  {name:<14} {model}")
    print(f"worker profile: {s['profile']}" + ("  (own config, OC_PROFILE=inherit uses yours)"
                                              if s["profile"] == "isolated" else "  (your OpenCode setup)"))
    print(f"default agent for jobs: {s['default_agent']}")
    print(f"guard config: {ops.guard_config()}")
    moved = core.migrate_legacy()
    if moved:
        print(f"migrated legacy jobs: {', '.join(moved)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oc", description="Delegate coding tasks to OpenCode workers.")
    p.add_argument("--version", action="version", version=f"oc-connect {core.VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="worktree + worker tab + session + task")
    s.add_argument("slug")
    s.add_argument("task_file", help="file with the task, or - for stdin")
    s.add_argument("--title")
    s.add_argument("--branch")
    s.add_argument("--agent")
    s.add_argument("--model", help="provider/model, overrides the agent's model")
    s.add_argument("--base", help="git ref to branch from (default: origin/HEAD)")

    s = sub.add_parser("send", help="follow-up message into the job's session")
    s.add_argument("slug")
    s.add_argument("file", help="file with the message, or - for stdin")

    s = sub.add_parser("wait", help="block until done/failed/waiting/stopped (exit 0/1/2/3, 4 = still running)")
    s.add_argument("slug")
    s.add_argument("minutes", nargs="?", type=float, default=9.5)

    s = sub.add_parser("status", help="all jobs")
    s.add_argument("--json", action="store_true")
    s.add_argument("-l", "--long", action="store_true")

    s = sub.add_parser("report", help="final message, cost and diff of a job")
    s.add_argument("slug")

    s = sub.add_parser("answer", help="answer an open question (one answer per sub-question)")
    s.add_argument("slug")
    s.add_argument("answers", nargs="+")
    s.add_argument("--id", dest="question_id", help="question id from status/monitor")

    s = sub.add_parser("permit", help="answer an open permission request")
    s.add_argument("slug")
    s.add_argument("reply", choices=["once", "always", "reject"])

    for name, hlp in (("open", "open the job's tab (restarts the worker if needed)"),
                      ("close", "stop the worker; its tab closes")):
        sub.add_parser(name, help=hlp).add_argument("slug")
    s = sub.add_parser("done", help="stop the worker and remove the worktree (branch stays)")
    s.add_argument("slug")
    s.add_argument("--force", action="store_true", help="also when uncommitted or unpushed work exists")
    s = sub.add_parser("review", help="record the review of a job (unblocks commits of exactly this state)")
    s.add_argument("slug")
    s.add_argument("--checks", required=True)
    s.add_argument("--defeat-test", required=True)
    s.add_argument("--gaps", required=True)

    s = sub.add_parser("snapshots", help="list the automatic snapshots of a job's worktree")
    s.add_argument("slug")
    s = sub.add_parser("restore", help="put a snapshot back into the job's worktree")
    s.add_argument("slug")
    s.add_argument("commit", nargs="?", help="snapshot to restore (default: the newest)")

    s = sub.add_parser("pr", help="remember the PR of a job (shown in status and board)")
    s.add_argument("slug")
    s.add_argument("pr")

    sub.add_parser("setup", help="show the OpenCode setup and default agent; migrate old jobs")
    s = sub.add_parser("selftest", help="prove the guard blocks git push and docker")
    s.add_argument("--model", help="provider/model for the test, e.g. opencode/grok-code")

    sub.add_parser("board", help="live board (run it in a terminal)")
    sub.add_parser("board-tab", help="open the live board in a new tab")
    sub.add_parser("mcp", help="run the MCP server on stdio (used by Claude Code)")
    sub.add_parser("monitor", help="print one line per job event (used by Claude Code)")
    s = sub.add_parser("reap", help="stop a worker once Claude no longer uses it (runs in its tab)")
    s.add_argument("slug")
    s.add_argument("pid", type=int)
    s.add_argument("idle_seconds", type=float, help="0 = only close when the Claude session ended")
    s = sub.add_parser("statusline", help="status line rows for jobs")
    s.add_argument("--base", help="command whose output goes above the job rows")
    s = sub.add_parser("install-statusline", help="add job rows under your existing Claude Code status line")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("hook", help="Claude Code hook entry point")
    s.add_argument("event", choices=["session-start", "prompt-submit", "pre-bash", "pre-delegate"])
    s = sub.add_parser("mode", help="show or set when Claude may delegate: off, ask, mechanical, auto, default")
    s.add_argument("value", nargs="?")
    return p


MODE_TEXT = {
    "off": "Claude never delegates on its own; when you ask for OpenCode, you confirm in a dialog.",
    "ask": "Claude may propose a delegation; every one needs your confirmation in a dialog.",
    "mechanical": "Claude delegates bounded work (tests, docs, small UI) without asking; larger work after one "
                  "question. No dialog.",
    "auto": "Claude delegates on its own judgement. No dialog.",
}


def cmd_mode(value: Optional[str]) -> str:
    mode = core.set_autonomy(value) if value else core.autonomy()
    src = "set by `oc mode`" if os.path.exists(core.AUTONOMY_FILE) else "plugin option or default"
    return f"delegation mode: {mode} ({src}) - {MODE_TEXT[mode]}"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "start":
            print(ops.start(args.slug, _read_task(args.task_file), title=args.title, branch=args.branch,
                            agent=args.agent, base=args.base, model=args.model))
        elif args.cmd == "send":
            print(ops.send(args.slug, _read_task(args.file)))
        elif args.cmd == "wait":
            code, text = ops.wait(args.slug, args.minutes)
            print(text)
            return code
        elif args.cmd == "status":
            return cmd_status(args)
        elif args.cmd == "report":
            print(ops.report(args.slug))
        elif args.cmd == "answer":
            print(ops.answer(args.slug, args.answers, args.question_id))
        elif args.cmd == "permit":
            print(ops.permit(args.slug, args.reply))
        elif args.cmd == "open":
            print(ops.open_(args.slug))
        elif args.cmd == "close":
            print(ops.close(args.slug))
        elif args.cmd == "done":
            print(ops.done(args.slug, args.force))
        elif args.cmd == "review":
            print(ops.review(args.slug, args.checks, args.defeat_test, args.gaps))
        elif args.cmd == "snapshots":
            print(ops.snapshots(args.slug))
        elif args.cmd == "restore":
            print(ops.restore(args.slug, args.commit))
        elif args.cmd == "pr":
            print(ops.set_pr(args.slug, args.pr))
        elif args.cmd == "setup":
            return cmd_setup(args)
        elif args.cmd == "selftest":
            print(ops.selftest(args.model))
        elif args.cmd == "board":
            from . import board
            board.main()
        elif args.cmd == "board-tab":
            from . import board
            print(board.open_tab())
        elif args.cmd == "mcp":
            from . import mcp
            mcp.main()
        elif args.cmd == "monitor":
            from . import monitor
            monitor.main()
        elif args.cmd == "reap":
            from . import reaper
            reaper.main(args.slug, args.pid, args.idle_seconds)
        elif args.cmd == "statusline":
            from . import statusline
            statusline.main(args.base)
        elif args.cmd == "install-statusline":
            from . import hooks
            print(hooks.install_statusline(args.dry_run))
        elif args.cmd == "hook":
            from . import hooks
            {"session-start": hooks.session_start, "prompt-submit": hooks.prompt_submit,
             "pre-bash": hooks.pre_bash, "pre-delegate": hooks.pre_delegate}[args.event]()
        elif args.cmd == "mode":
            print(cmd_mode(args.value))
    except OcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
