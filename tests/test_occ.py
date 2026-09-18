"""Tests for oc-connect. Standard library only: ``python3 -m unittest discover tests``.

A fake OpenCode server stands in for the real one, so the state machine
(running / waiting / done / stopped) is tested without a model or a terminal.
The end-to-end path against real OpenCode is covered by ``oc selftest`` and the
manual check in CONTRIBUTING.md.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
TMP_STATE = tempfile.mkdtemp(prefix="oc-test-state-")
os.environ["OC_STATE"] = TMP_STATE

from occ import core, hooks, mcp, ops, statusline  # noqa: E402


class FakeOpenCode(BaseHTTPRequestHandler):
    """Answers the handful of endpoints oc-connect reads."""
    status = {}
    questions = []
    permissions = []
    todos = []

    def log_message(self, *args):
        pass

    def _send(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/doc":
            return self._send({})
        if path == "/session/status":
            return self._send(FakeOpenCode.status)
        if path == "/question":
            return self._send(FakeOpenCode.questions)
        if path == "/permission":
            return self._send(FakeOpenCode.permissions)
        if path.endswith("/todo"):
            return self._send(FakeOpenCode.todos)
        if path.endswith("/message"):
            return self._send([{"info": {"role": "assistant", "cost": 0.5, "modelID": "m"},
                                "parts": [{"type": "text", "text": "all done"}]}])
        self._send(None)


def start_fake():
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenCode)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class StateMachine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = start_fake()
        cls.dir = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", cls.dir], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        FakeOpenCode.status, FakeOpenCode.questions, FakeOpenCode.permissions, FakeOpenCode.todos = {}, [], [], []
        core.save_job({"slug": "t1", "title": "#1 Test job", "dir": self.dir, "port": self.srv.server_port,
                       "session": "ses_1", "agent": "fixer", "created": time.time(), "sent_at": 0})

    def tearDown(self):
        core.delete_job("t1")

    def test_busy_session_is_running(self):
        FakeOpenCode.status = {"ses_1": {"type": "busy"}}
        FakeOpenCode.todos = [{"status": "completed"}, {"status": "pending"}]
        st = ops.status("t1")
        self.assertEqual(st["state"], "running")
        self.assertEqual((st["todo_done"], st["todo_total"]), (1, 2))

    def test_question_wins_over_busy(self):
        # An open question keeps the session busy; it must still show as waiting.
        FakeOpenCode.status = {"ses_1": {"type": "busy"}}
        FakeOpenCode.questions = [{"id": "q1", "sessionID": "ses_1",
                                   "questions": [{"question": "Which?", "options": [{"label": "a"}]}]}]
        st = ops.status("t1")
        self.assertEqual(st["state"], "waiting")
        self.assertEqual(st["pending"][0]["options"], ["a"])

    def test_question_of_other_session_is_ignored(self):
        FakeOpenCode.questions = [{"id": "q1", "sessionID": "ses_other", "questions": [{"question": "x"}]}]
        self.assertEqual(ops.status("t1")["state"], "done")

    def test_idle_session_is_done_after_grace(self):
        self.assertEqual(ops.status("t1")["state"], "done")

    def test_fresh_prompt_counts_as_running(self):
        # Right after prompt_async the session may not be busy yet.
        job = core.load_job("t1")
        job["sent_at"] = time.time()
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "running")

    def test_idle_status_entry_is_not_busy(self):
        FakeOpenCode.status = {"ses_1": {"type": "idle"}}
        self.assertEqual(ops.status("t1")["state"], "done")

    def test_dead_worker_is_stopped(self):
        job = core.load_job("t1")
        job["port"] = 1  # nothing listens there
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "stopped")

    def test_wait_returns_code_2_for_question(self):
        FakeOpenCode.permissions = [{"id": "p1", "sessionID": "ses_1", "permission": "bash", "patterns": ["rm -rf x"]}]
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 2)
        self.assertIn("PERMISSION: bash rm -rf x", text)

    def test_wait_returns_report_when_done(self):
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 0)
        self.assertIn("all done", text)
        self.assertIn("$0.50", text)

    def test_job_with_removed_worktree_disappears(self):
        core.save_job({"slug": "gone", "dir": "/nonexistent/oc-test", "port": 1})
        self.assertNotIn("gone", [j["slug"] for j in core.load_jobs()])
        self.assertFalse(os.path.exists(core.job_path("gone")))


class DelegationPhrases(unittest.TestCase):
    def test_requests_are_detected(self):
        for text in ("das machst du jz mit opencode", "gib das an OpenCode", "lass opencode das machen",
                     "OpenCode soll die Tests schreiben", "do this with opencode", "delegate it to OpenCode",
                     "use opencode for the tests"):
            self.assertTrue(hooks.wants_delegation(text), text)

    def test_talk_about_the_tool_is_not(self):
        for text in ("wir haben eben am opencode connect gearbeitet", "arbeiten an opencode connect",
                     "opencode findet diesen agent nicht", "ich hab oh my opencode installiert",
                     "update the opencode.json", "what is opencode?"):
            self.assertFalse(hooks.wants_delegation(text), text)


class Slugs(unittest.TestCase):
    def test_rejects_shell_and_path_characters(self):
        for bad in ("", "../x", "a b", "a;rm", "-x", "a" * 41):
            with self.assertRaises(core.OcError):
                core.valid_slug(bad)
        self.assertEqual(core.valid_slug("276"), "276")
        self.assertEqual(core.valid_slug("tests-cart.v2"), "tests-cart.v2")


class EnvFiles(unittest.TestCase):
    def test_finds_ignored_env_files_not_templates(self):
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", repo], check=True)
        os.makedirs(os.path.join(repo, "backend"))
        for rel, text in ((".gitignore", ".env\n.env.local\nnode_modules/\n"), (".env", "A=1"),
                          ("backend/.env", "B=1"), (".env.example", "A="), (".env.local", "C=1")):
            with open(os.path.join(repo, rel), "w") as fh:
                fh.write(text)
        os.environ.pop("OC_COPY", None)
        self.assertEqual(sorted(core.env_files(repo)), [".env", ".env.local", "backend/.env"])


class StatusRows(unittest.TestCase):
    def st(self, **kw):
        base = {"slug": "276", "title": "#276 Orders after payment", "state": "running", "pending": [],
                "todo_done": 3, "todo_total": 6, "agent": "fixer", "created": time.time() - 600,
                "diff": {"files": 2, "plus": 10, "minus": 1}}
        base.update(kw)
        return base

    def plain(self, text):
        return statusline.ANSI.sub("", text or "")

    def test_running_row(self):
        row = self.plain(statusline.row(self.st()))
        self.assertIn("▶ 276", row)
        self.assertIn("Orders after payment", row)
        self.assertIn("3/6", row)
        self.assertIn("2 files +10 -1", row)

    def test_waiting_row_shows_question(self):
        row = self.plain(statusline.row(self.st(state="waiting", pending=[
            {"kind": "question", "text": "Refunds too?", "options": []}])))
        self.assertIn("asks: Refunds too?", row)

    def test_done_row_until_pr_exists(self):
        self.assertIn("ready for review", self.plain(statusline.row(self.st(state="done"))))
        self.assertIsNone(statusline.row(self.st(state="done", pr="#303")))
        self.assertIsNone(statusline.row(self.st(state="stopped")))

    def test_subagent_rows_only_for_worker_agents(self):
        data = {"columns": 120, "tasks": [
            {"id": "a", "type": "oc:opencode-worker", "description": "oc 276"},
            {"id": "b", "type": "general-purpose", "description": "oc 276"},
            {"id": "c", "type": "oc:opencode-worker", "description": "oc unknown"}]}
        out = io.StringIO()
        with mock.patch.object(statusline, "_cached_status", return_value=[self.st()]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(data))), mock.patch.object(sys, "stdout", out):
            statusline.subagent_main()
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in rows], ["a"])
        self.assertIn("276", self.plain(rows[0]["content"]))


class McpProtocol(unittest.TestCase):
    def test_initialize_lists_instructions_and_tools(self):
        init = mcp.handle({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("WHEN TO DELEGATE", init["instructions"])
        names = [t["name"] for t in mcp.handle({"id": 2, "method": "tools/list"})["tools"]]
        self.assertIn("delegate", names)
        self.assertEqual(set(names), set(mcp.HANDLERS))

    def test_tool_errors_are_results_not_crashes(self):
        res = mcp.handle({"id": 3, "method": "tools/call", "params": {"name": "report", "arguments": {"slug": "nope"}}})
        self.assertTrue(res["isError"])
        self.assertIn("unknown job", res["content"][0]["text"])

    def test_unknown_method(self):
        with self.assertRaises(LookupError):
            mcp.handle({"id": 4, "method": "resources/list"})


if __name__ == "__main__":
    unittest.main()
