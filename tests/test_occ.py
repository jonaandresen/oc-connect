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

from occ import core, hooks, mcp, monitor, ops, profile, statusline  # noqa: E402


class FakeOpenCode(BaseHTTPRequestHandler):
    """Answers the handful of endpoints oc-connect reads."""
    status = {}
    questions = []
    permissions = []
    todos = []
    error = None
    updated = 1000
    replies = []

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
            return self._send([{"info": {"role": "assistant", "cost": 0.5, "modelID": "m",
                                         "error": FakeOpenCode.error},
                                "parts": [{"type": "text", "text": "all done"}]}])
        if path.startswith("/session/"):
            return self._send({"id": path.split("/")[2], "time": {"updated": FakeOpenCode.updated}})
        self._send(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        FakeOpenCode.replies.append((self.path.split("?")[0], json.loads(self.rfile.read(length) or b"null")))
        self._send(True)


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
        FakeOpenCode.error, FakeOpenCode.replies = None, []
        FakeOpenCode.updated += 1  # fresh summary cache per test
        core.save_job({"slug": "t1", "title": "#1 Test job", "dir": self.dir, "port": self.srv.server_port,
                       "session": "ses_1", "agent": "fixer", "created": time.time(), "sent_at": time.time() - 3600})

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

    def test_worker_not_up_before_task_sent_is_starting(self):
        # The tab installs dependencies before OpenCode listens.
        job = core.load_job("t1")
        job.update(port=1, sent_at=None)
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "starting")

    def test_idle_session_before_task_sent_is_not_finished(self):
        # Between "tab is up" and "task sent" the session is idle - that is no finish.
        job = core.load_job("t1")
        job["sent_at"] = None
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "starting")

    def test_dead_worker_is_stopped(self):
        job = core.load_job("t1")
        job["port"] = 1  # nothing listens there
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "stopped")

    def test_wait_returns_code_2_for_question(self):
        FakeOpenCode.permissions = [{"id": "p1", "sessionID": "ses_1", "permission": "bash", "patterns": ["rm -rf x"]}]
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 2)
        self.assertIn("PERMISSION [p1]: bash rm -rf x", text)

    def test_wait_returns_report_when_done(self):
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 0)
        self.assertIn("all done", text)
        self.assertIn("$0.50", text)

    def test_job_with_removed_worktree_disappears(self):
        core.save_job({"slug": "gone", "dir": "/nonexistent/oc-test", "port": 1})
        self.assertNotIn("gone", [j["slug"] for j in core.load_jobs()])
        self.assertFalse(os.path.exists(core.job_path("gone")))

    def test_error_in_last_turn_is_failed_not_done(self):
        FakeOpenCode.error = {"name": "APIError", "data": {"message": "rate limited"}}
        st = ops.status("t1")
        self.assertEqual(st["state"], "failed")
        self.assertIn("rate limited", st["error"])
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 1)

    def test_start_error_is_failed(self):
        job = core.load_job("t1")
        job["start_error"] = "OpenCode has no agent 'fixr'"
        core.save_job(job)
        st = ops.status("t1")
        self.assertEqual((st["state"], st["error"]), ("failed", "OpenCode has no agent 'fixr'"))

    def _question(self, qid="q1", parts=1):
        FakeOpenCode.status = {"ses_1": {"type": "busy"}}
        FakeOpenCode.questions = [{"id": qid, "sessionID": "ses_1",
                                   "questions": [{"question": f"Q{i}", "options": []} for i in range(parts)]}]

    def test_answer_goes_to_the_named_question(self):
        self._question("q7")
        ops.answer("t1", ["yes"], "q7")
        self.assertEqual(FakeOpenCode.replies[-1], ("/question/q7/reply", {"answers": [["yes"]]}))
        self.assertIn("q7", core.load_job("t1")["answered"])

    def test_answer_to_a_question_that_is_gone_is_refused(self):
        # A second Claude session must not answer the NEXT question by accident.
        self._question("q8")
        with self.assertRaises(core.OcError) as ctx:
            ops.answer("t1", ["yes"], "q7")
        self.assertIn("no longer open", str(ctx.exception))
        self.assertEqual(FakeOpenCode.replies, [])

    def test_answer_count_must_match_sub_questions(self):
        self._question("q9", parts=2)
        with self.assertRaises(core.OcError):
            ops.answer("t1", ["only one"], "q9")


class MonitorTransitions(unittest.TestCase):
    job = {"slug": "m1", "port": 1, "dir": "/tmp"}

    def st(self, state, **kw):
        base = {"slug": "m1", "title": "t", "state": state, "pending": [], "agent": "fixer"}
        base.update(kw)
        return base

    def run_seq(self, states, job=None, first=True):
        w, lines = monitor.JobWatch(), []
        for i, st in enumerate(states):
            with mock.patch.object(core, "diffstat", return_value={"files": 1, "plus": 2, "minus": 0}):
                lines.append(monitor.transition(w, st, job or self.job, first=first and i == 0))
        return lines

    def test_silent_start_then_finish(self):
        out = self.run_seq([self.st("running"), self.st("running"), self.st("done")])
        self.assertEqual(out[0], [])
        self.assertEqual(out[1], [])
        self.assertIn("finished", out[2][0])

    def test_waiting_job_is_reported_even_at_start(self):
        q = [{"kind": "question", "id": "q1", "text": "Which?", "options": ["a"]}]
        out = self.run_seq([self.st("waiting", pending=q)])
        self.assertIn("asks [q1]: Which?", out[0][0])

    def test_new_question_while_waiting_is_reported(self):
        q1 = [{"kind": "question", "id": "q1", "text": "A?", "options": []}]
        q2 = [{"kind": "question", "id": "q2", "text": "B?", "options": []}]
        out = self.run_seq([self.st("running"), self.st("waiting", pending=q1), self.st("waiting", pending=q1),
                            self.st("waiting", pending=q2)])
        self.assertEqual([len(x) for x in out], [0, 1, 0, 1])

    def test_answer_outside_claude_is_reported(self):
        q = [{"kind": "question", "id": "q1", "text": "A?", "options": []}]
        out = self.run_seq([self.st("running"), self.st("waiting", pending=q), self.st("running")],
                           job=dict(self.job, answered=[]))
        self.assertIn("answered directly in OpenCode", out[2][0])
        out = self.run_seq([self.st("running"), self.st("waiting", pending=q), self.st("running")],
                           job=dict(self.job, answered=["q1"]))
        self.assertEqual(out[2], [])

    def test_failed_and_stalled_and_budget(self):
        out = self.run_seq([self.st("running"), self.st("failed", error="APIError: down")])
        self.assertIn("FAILED: APIError: down", out[1][0])
        old = (time.time() - 3600) * 1000
        out = self.run_seq([self.st("running"), self.st("running", updated=old), self.st("running", updated=old)])
        self.assertIn("stalled", out[1][0])
        self.assertEqual(out[2], [])  # reported once
        out = self.run_seq([self.st("running", cost=0.5), self.st("running", cost=2.5),
                            self.st("running", cost=3.0), self.st("running", cost=4.1)])
        self.assertEqual([len(x) for x in out], [0, 1, 0, 1])

    def test_start_reported_with_browser_link(self):
        out = self.run_seq([self.st("starting"), self.st("running")], first=False)
        self.assertIn("is running", out[1][0])


def _git_repo_with_origin():
    tmp = tempfile.mkdtemp()
    origin, repo = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
    subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
    subprocess.run(["git", "clone", "-q", origin, repo], check=True, capture_output=True)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("a\n")
    for args in (["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                 ["push", "-q", "origin", "HEAD:main"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return origin, repo


class GuardEnvironment(unittest.TestCase):
    """The second guard layer, against real git - no model involved."""

    def test_every_push_form_is_blocked_and_fetch_works(self):
        origin, repo = _git_repo_with_origin()
        env = dict(os.environ, **ops.worker_env())
        for cmd in (["git", "push", "origin", "HEAD:refs/heads/x1"],
                    ["bash", "-c", "git push origin HEAD:refs/heads/x2"],
                    ["git", "push", origin, "HEAD:refs/heads/x3"],
                    ["git", "push", "file://" + origin, "HEAD:refs/heads/x4"]):
            res = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, cmd)
        self.assertEqual(subprocess.run(["git", "fetch", "origin"], cwd=repo, env=env).returncode, 0)
        branches = subprocess.run(["git", "branch"], cwd=origin, capture_output=True, text=True).stdout
        self.assertNotIn("x", branches.replace("main", ""))

    def test_user_git_config_entries_are_kept(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1"}):
            env = ops.worker_env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "remote.origin.pushurl")


class ReviewGate(unittest.TestCase):
    def setUp(self):
        self.origin, self.repo = _git_repo_with_origin()
        core.save_job({"slug": "rv", "dir": self.repo, "port": 1, "created": time.time()})

    def tearDown(self):
        core.delete_job("rv")

    def test_commit_blocked_until_review_then_again_after_change(self):
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("changed\n")
        self.assertIn("has not been reviewed", ops.review_block_reason(self.repo))
        with self.assertRaises(core.OcError):
            ops.review("rv", "ok", "ok", "ok")  # too thin to count
        ops.review("rv", "unit tests 21/21 green", "broke parser, test_x went red", "no e2e run")
        self.assertIsNone(ops.review_block_reason(self.repo))
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)  # staging does not matter
        self.assertIsNone(ops.review_block_reason(self.repo))
        with open(os.path.join(self.repo, "b.txt"), "w") as fh:
            fh.write("new after review\n")
        self.assertIn("changed after the recorded review", ops.review_block_reason(self.repo))

    def test_other_repositories_are_not_affected(self):
        _, other = _git_repo_with_origin()
        self.assertIsNone(ops.review_block_reason(other))

    def test_commit_dirs_parsing(self):
        self.assertEqual(hooks.commit_dirs("npm test", "/w"), [])
        self.assertEqual(hooks.commit_dirs('git commit -m "x"', "/w"), ["/w"])
        self.assertEqual(hooks.commit_dirs("cd /a/b && git add -A && git commit -m x", "/w"), ["/a/b"])
        self.assertEqual(hooks.commit_dirs("git -C /a/c commit -m x", "/w"), ["/a/c"])
        self.assertEqual(hooks.commit_dirs("cd sub && git commit -m x", "/w"), ["/w/sub"])
        self.assertEqual(hooks.commit_dirs("git -c user.name=x commit -m y", "/w"), ["/w"])

    def test_hook_denies_with_reason(self):
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("changed\n")
        out = io.StringIO()
        data = {"tool_input": {"command": f"cd {self.repo} && git commit -am x"}, "cwd": "/"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(data))), mock.patch.object(sys, "stdout", out):
            hooks.pre_bash()
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")


class DoneSafety(unittest.TestCase):
    def test_refuses_with_unsaved_work(self):
        origin, repo = _git_repo_with_origin()
        self.assertEqual(ops.unsaved_work(repo), [])
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("dirty\n")
        self.assertIn("uncommitted", ops.unsaved_work(repo)[0])
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "x"], cwd=repo, check=True)
        self.assertIn("not pushed", ops.unsaved_work(repo)[0])


class Snapshots(unittest.TestCase):
    """The worker's result must survive a mistake during the review."""

    def setUp(self):
        _, self.repo = _git_repo_with_origin()
        self.wt = os.path.join(core.worktree_root(self.repo), "snap")
        subprocess.run(["git", "worktree", "add", "-q", "-b", "oc/snap", self.wt], cwd=self.repo,
                       check=True, capture_output=True)
        core.save_job({"slug": "snap", "dir": self.wt, "repo": self.repo, "port": 1,
                       "created": time.time()})

    def tearDown(self):
        core.delete_job("snap")

    def _write(self, name, text):
        with open(os.path.join(self.wt, name), "w") as fh:
            fh.write(text)

    def test_restore_brings_back_a_wiped_result(self):
        self._write("a.txt", "worker result\n")       # tracked file changed
        self._write("new.py", "print('new')\n")       # untracked file added
        first = ops.snapshot("snap", "worker result")
        self.assertTrue(first)
        subprocess.run(["git", "checkout", "--", "a.txt"], cwd=self.wt, check=True)
        os.remove(os.path.join(self.wt, "new.py"))
        ops.restore("snap")
        with open(os.path.join(self.wt, "a.txt")) as fh:
            self.assertEqual(fh.read(), "worker result\n")
        self.assertTrue(os.path.exists(os.path.join(self.wt, "new.py")))

    def test_state_before_a_restore_is_kept_and_review_is_dropped(self):
        self._write("a.txt", "first\n")
        ops.snapshot("snap")
        self._write("a.txt", "second\n")
        ops.review("snap", "unit tests green 12/12", "broke the guard, test_x went red", "no e2e run yet")
        self.assertIsNotNone(core.load_job("snap").get("review"))
        ops.restore("snap", ops.snapshot_ref("snap") + "~1")
        with open(os.path.join(self.wt, "a.txt")) as fh:
            self.assertEqual(fh.read(), "first\n")
        self.assertIsNone(core.load_job("snap").get("review"))
        self.assertIn("second", subprocess.run(["git", "show", ops.snapshot_ref("snap") + ":a.txt"],
                                               cwd=self.repo, capture_output=True, text=True).stdout)

    def test_unchanged_content_makes_no_second_snapshot(self):
        self._write("a.txt", "once\n")
        first = ops.snapshot("snap")
        self.assertEqual(ops.snapshot("snap"), first)

    def test_snapshot_survives_done(self):
        self._write("a.txt", "result to keep\n")
        out = ops.done("snap", force=True)
        self.assertIn(ops.snapshot_ref("snap"), out)
        self.assertIn("result to keep", subprocess.run(
            ["git", "show", ops.snapshot_ref("snap") + ":a.txt"], cwd=self.repo,
            capture_output=True, text=True).stdout)


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

    def test_long_names_are_marked_as_cut(self):
        row = self.plain(statusline.row(self.st(slug="pricing-validation-extra", title="x")))
        self.assertIn("pricing-validati…", row)
        self.assertNotIn("pricing-validation ", row)  # a cut name must not read like the real one

    def test_done_row_until_pr_exists(self):
        self.assertIn("ready for review", self.plain(statusline.row(self.st(state="done"))))
        self.assertIsNone(statusline.row(self.st(state="done", pr="#303")))
        self.assertIsNone(statusline.row(self.st(state="stopped")))


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

    def test_status_is_compact_text_with_everything_needed(self):
        text = mcp._job_lines({"slug": "276", "state": "waiting", "agent": "oc-fast", "title": "#276 Orders",
                               "dir": "/w/276", "branch": "oc/276", "todo_done": 2, "todo_total": 5,
                               "diff": {"files": 3, "plus": 40, "minus": 2}, "cost": 0.12,
                               "created": time.time() - 300, "owner": 42, "reviewed": False,
                               "pending": [{"kind": "question", "id": "que_1", "text": "Which mode?",
                                            "options": ["a", "b"]}]}, 42)
        for needed in ("276 [waiting]", "oc-fast", "/w/276", "oc/276", "2/5 todos", "3 files +40 -2",
                       "$0.12", "QUESTION [que_1]: Which mode?", "[options: a | b]"):
            self.assertIn(needed, text)
        self.assertNotIn("{", text)          # JSON would cost several times the tokens
        self.assertLessEqual(len(text), 260)

    def test_job_of_another_session_is_marked(self):
        text = mcp._job_lines({"slug": "x", "state": "running", "dir": "/w/x", "branch": "b",
                               "owner": 7, "owner_alive": True, "pending": []}, 42)
        self.assertIn("ANOTHER LIVE CLAUDE SESSION", text)

    def test_unknown_method(self):
        with self.assertRaises(LookupError):
            mcp.handle({"id": 4, "method": "resources/list"})


class WorkerProfile(unittest.TestCase):
    """The workers' own OpenCode config: no user plugins, trackers and tools kept."""

    def setUp(self):
        self.user = tempfile.mkdtemp()           # stands in for ~/.config
        self.oc = os.path.join(self.user, "opencode")
        os.makedirs(os.path.join(self.oc, "plugins"))
        os.makedirs(os.path.join(self.user, "gh"))
        with open(os.path.join(self.oc, "opencode.jsonc"), "w") as fh:
            fh.write('// comment\n{"plugin": ["oh-my-opencode-slim"], "mcp": {"pw": {"type": "local"}}, '
                     '"permission": {"bash": "allow"}}')
        with open(os.path.join(self.oc, "oh-my-opencode-slim.json"), "w") as fh:
            json.dump({"preset": "p", "presets": {"p": {"fixer": {"model": "go/flash"},
                                                          "designer": {"model": "go/kimi"}}}}, fh)
        for name in ("vibe-island.js", "package.json"):
            open(os.path.join(self.oc, "plugins", name), "w").close()
        self.home = os.path.join(tempfile.mkdtemp(), "worker-home")
        self.patches = [mock.patch.object(core, "OPENCODE_CONFIG_DIR", self.oc),
                        mock.patch.object(profile, "USER_CONFIG", self.user),
                        mock.patch.object(profile, "HOME", self.home)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_config_has_primary_agents_question_tool_and_no_plugins(self):
        cfg = profile.build_config()
        self.assertNotIn("plugin", cfg)
        self.assertEqual(cfg["permission"], {"question": "allow"})
        self.assertEqual(cfg["mcp"], {"pw": {"type": "local"}})
        self.assertEqual({a["mode"] for a in cfg["agent"].values()}, {"primary"})
        self.assertEqual(cfg["agent"]["oc-fast"]["model"], "go/flash")
        self.assertEqual(cfg["agent"]["oc-ui"]["model"], "go/kimi")
        self.assertNotIn("model", cfg["agent"]["oc-strong"])  # no orchestrator role: OpenCode default
        self.assertIn("worker-prompt.md", cfg["agent"]["oc-fast"]["prompt"])

    def test_home_mirrors_other_tools_and_keeps_only_plugin_files(self):
        home = profile.ensure_home()
        self.assertEqual(os.readlink(os.path.join(home, "gh")), os.path.join(self.user, "gh"))
        self.assertFalse(os.path.islink(os.path.join(home, "opencode")))
        self.assertEqual(os.listdir(os.path.join(home, "opencode", "plugins")), ["vibe-island.js"])
        with open(os.path.join(home, "opencode", "opencode.json")) as fh:
            self.assertIn("oc-fast", json.load(fh)["agent"])
        os.rmdir(os.path.join(self.user, "gh"))  # removed from ~/.config -> link goes
        profile.ensure_home()
        self.assertFalse(os.path.lexists(os.path.join(home, "gh")))

    def test_worker_env_and_command_use_the_profile(self):
        env = ops.worker_env()
        self.assertEqual(env["XDG_CONFIG_HOME"], self.home)
        cmd = ops._worker_command({"dir": "/w", "port": 4101, "agent": "oc-fast"}, "ses_1", False)
        self.assertIn("--agent oc-fast", cmd)
        cmd = ops._worker_command({"dir": "/w", "port": 4101, "agent": "fixer"}, "ses_1", False)
        self.assertNotIn("--agent", cmd)  # subagents cannot be TUI agents

    def test_inherit_mode_leaves_the_user_config_alone(self):
        with mock.patch.dict(os.environ, {"OC_PROFILE": "inherit"}):
            self.assertNotIn("XDG_CONFIG_HOME", ops.worker_env())
            self.assertNotEqual(core.default_agent(False), "oc-fast")


if __name__ == "__main__":
    unittest.main()
