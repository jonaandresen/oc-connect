"""Tests for oc-connect. Standard library only: ``python3 -m unittest discover tests``.

A fake OpenCode server stands in for the real one, so the state machine
(running / waiting / done / stopped) is tested without a model or a terminal.
The end-to-end path against real OpenCode is covered by ``oc selftest`` and the
manual checks in docs/measured.md.
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

from occ import core, hooks, mcp, monitor, ops, profile, reaper, statusline, terminal  # noqa: E402


class FakeOpenCode(BaseHTTPRequestHandler):
    """Answers the OpenCode 2 endpoints oc-connect reads - shapes as measured on 2.1.4."""
    active = {}        # session id -> {"type": "running"}
    forms = []         # Form.Info
    permissions = []   # Permission.Request
    error = None       # error of the last turn: its idle message says "failed"
    cost = 0.5
    updated = 1000
    replies = []
    password = "pw"

    def log_message(self, *args):
        pass

    def _send(self, body, code=200):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code if raw or code != 200 else 204)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        import base64
        want = "Basic " + base64.b64encode(f"opencode:{FakeOpenCode.password}".encode()).decode()
        if self.headers.get("Authorization") == want:
            return True
        self._send({"error": "unauthorized"}, 401)
        return False

    def do_GET(self):
        if not self._authorized():
            return
        path = self.path.split("?")[0]
        parts = path.strip("/").split("/")
        if path == "/api/info":
            return self._send({"version": "2.1.4"})
        if path == "/api/session/active":
            return self._send({"data": FakeOpenCode.active})
        if path == "/api/agent":
            return self._send({"data": [{"id": "build"}, {"id": "oc-fast"}, {"id": "fixer"}]})
        if len(parts) == 4 and parts[3] == "form":
            return self._send({"data": [f for f in FakeOpenCode.forms if f["sessionID"] == parts[2]]})
        if len(parts) == 4 and parts[3] == "permission":
            return self._send({"data": [r for r in FakeOpenCode.permissions if r["sessionID"] == parts[2]]})
        if len(parts) == 4 and parts[3] == "message":
            err = FakeOpenCode.error
            msgs = [  # newest first, like the API
                {"id": "m3", "type": "idle", "time": {"created": 3}, "outcome": "failed" if err else "succeeded",
                 **({"error": err} if err else {})},
                {"id": "m2", "type": "assistant", "time": {"created": 2}, "model": {"id": "m", "providerID": "p"},
                 "content": [{"type": "tool", "name": "edit", "state": {"status": "completed",
                                                                       "input": {"path": "/w/src/app.js"}}},
                             {"type": "text", "text": "all done"}]},
                {"id": "m1", "type": "user", "time": {"created": 1}, "text": "task"}]
            return self._send({"data": msgs, "cursor": {}})
        if len(parts) == 3 and parts[1] == "session":
            return self._send({"data": {"id": parts[2], "cost": FakeOpenCode.cost,
                                        "model": {"id": "m", "providerID": "p"},
                                        "time": {"updated": FakeOpenCode.updated}}})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        path = self.path.split("?")[0]
        FakeOpenCode.replies.append((path, json.loads(self.rfile.read(length) or b"null")))
        if path == "/api/session":
            return self._send({"data": {"id": "ses_new"}})
        self._send(None)

    def do_DELETE(self):
        self.do_POST()


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
        FakeOpenCode.active, FakeOpenCode.forms, FakeOpenCode.permissions = {}, [], []
        FakeOpenCode.error, FakeOpenCode.replies = None, []
        FakeOpenCode.updated += 1  # fresh summary cache per test
        core.save_job({"slug": "t1", "title": "#1 Test job", "dir": self.dir, "port": self.srv.server_port,
                       "session": "ses_1", "agent": "fixer", "created": time.time(), "sent_at": time.time() - 3600,
                       "password": "pw"})

    def tearDown(self):
        core.delete_job("t1")

    def test_active_session_is_running(self):
        FakeOpenCode.active = {"ses_1": {"type": "running"}}
        st = ops.status("t1")
        self.assertEqual(st["state"], "running")
        self.assertEqual(st["cost"], 0.5)  # from the cheap session header

    def test_wrong_password_counts_as_no_worker(self):
        job = core.load_job("t1")
        job["password"] = "other"
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "stopped")

    def test_question_wins_over_busy(self):
        # An open question keeps the session busy; it must still show as waiting.
        FakeOpenCode.active = {"ses_1": {"type": "running"}}
        FakeOpenCode.forms = [{"id": "frm_1", "sessionID": "ses_1", "title": "Questions",
                               "fields": [{"key": "q0", "title": "Mode", "description": "Which?", "type": "string",
                                           "options": [{"value": "a", "label": "a"}], "custom": True}]}]
        st = ops.status("t1")
        self.assertEqual(st["state"], "waiting")
        self.assertEqual(st["pending"][0]["options"], ["a"])

    def test_question_of_other_session_is_ignored(self):
        FakeOpenCode.forms = [{"id": "frm_1", "sessionID": "ses_other", "title": "Questions",
                               "fields": [{"key": "q0", "type": "string"}]}]
        self.assertEqual(ops.status("t1")["state"], "done")

    def test_idle_session_is_done_after_grace(self):
        self.assertEqual(ops.status("t1")["state"], "done")

    def test_fresh_prompt_counts_as_running(self):
        # Right after prompt_async the session may not be busy yet.
        job = core.load_job("t1")
        job["sent_at"] = time.time()
        core.save_job(job)
        self.assertEqual(ops.status("t1")["state"], "running")

    def test_other_active_session_is_not_ours(self):
        FakeOpenCode.active = {"ses_other": {"type": "running"}}
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
        FakeOpenCode.permissions = [{"id": "per_1", "sessionID": "ses_1", "action": "shell", "resources": ["rm -rf x"]}]
        code, text = ops.wait("t1", minutes=0.01, poll=0.01)
        self.assertEqual(code, 2)
        self.assertIn("PERMISSION [per_1]: shell: rm -rf x", text)

    def test_permit_sends_the_decision(self):
        FakeOpenCode.permissions = [{"id": "per_1", "sessionID": "ses_1", "action": "shell", "resources": ["x"]}]
        ops.permit("t1", "once")
        self.assertEqual(FakeOpenCode.replies[-1], ("/api/session/ses_1/permission/per_1/reply", {"decision": "once"}))

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
        FakeOpenCode.error = {"type": "provider.rate-limit", "message": "rate limited"}
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

    def _question(self, qid="q1", parts=1, kind="string"):
        FakeOpenCode.active = {"ses_1": {"type": "running"}}
        FakeOpenCode.forms = [{"id": qid, "sessionID": "ses_1", "title": "Questions",
                               "fields": [{"key": f"q{i}", "title": f"Q{i}", "type": kind, "options": []}
                                          for i in range(parts)]}]

    def test_answer_goes_to_the_named_question(self):
        self._question("q7")
        ops.answer("t1", ["yes"], "q7")
        self.assertEqual(FakeOpenCode.replies[-1], ("/api/session/ses_1/form/q7/reply", {"answer": {"q0": "yes"}}))
        self.assertIn("q7", core.load_job("t1")["answered"])

    def test_answers_go_to_their_fields(self):
        self._question("q8", parts=2)
        ops.answer("t1", ["a", "b"], "q8")
        self.assertEqual(FakeOpenCode.replies[-1][1], {"answer": {"q0": "a", "q1": "b"}})

    def test_multiselect_answer_is_a_list(self):
        self._question("q9", kind="multiselect")
        ops.answer("t1", ["x"], "q9")
        self.assertEqual(FakeOpenCode.replies[-1][1], {"answer": {"q0": ["x"]}})

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
        os.makedirs(os.path.join(self.oc, "plugins", "vibe-island-tui"))       # a tracker's TUI part
        os.makedirs(os.path.join(self.oc, "plugins", "oh-my-opencode-slim"))
        with open(os.path.join(self.oc, "cli.json"), "w") as fh:
            json.dump({"theme": {"name": "orng"}, "plugins": ["oh-my-opencode-slim", "./tracker-tui"]}, fh)
        self.home = os.path.join(tempfile.mkdtemp(), "worker-home")
        self.patches = [mock.patch.object(core, "OPENCODE_CONFIG_DIR", self.oc),
                        mock.patch.object(profile, "USER_CONFIG", self.user),
                        mock.patch.object(profile, "HOME", self.home)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_config_has_primary_agents_question_tool_guard_and_no_plugins(self):
        guard = [{"action": "shell", "resource": "git push*", "effect": "deny"}]
        cfg = profile.build_config(guard)
        self.assertNotIn("plugin", cfg)
        self.assertNotIn("plugins", cfg)
        self.assertEqual(cfg["permissions"], [{"action": "question", "resource": "*", "effect": "allow"}] + guard)
        self.assertEqual(cfg["mcp"], {"pw": {"type": "local"}})
        self.assertEqual({a["mode"] for a in cfg["agents"].values()}, {"primary"})
        # OpenCode 2 drops agents that name a model: the model goes onto the session instead.
        self.assertFalse(any("model" in a for a in cfg["agents"].values()))
        self.assertEqual(profile.agent_models()["oc-fast"], "go/flash")
        self.assertEqual(profile.agent_models()["oc-ui"], "go/kimi")
        self.assertIsNone(profile.agent_models()["oc-strong"])  # no orchestrator role: OpenCode default
        self.assertIn("You are a builder", cfg["agents"]["oc-fast"]["system"])

    def test_home_mirrors_other_tools_and_keeps_only_local_plugins(self):
        home = profile.ensure_home()
        self.assertEqual(os.readlink(os.path.join(home, "gh")), os.path.join(self.user, "gh"))
        self.assertFalse(os.path.islink(os.path.join(home, "opencode")))
        self.assertEqual(sorted(os.listdir(os.path.join(home, "opencode", "plugins"))),
                         ["vibe-island-tui", "vibe-island.js"])
        with open(os.path.join(home, "opencode", "opencode.json")) as fh:
            self.assertIn("oc-fast", json.load(fh)["agents"])
        with open(os.path.join(home, "opencode", "cli.json")) as fh:
            cli = json.load(fh)
        self.assertEqual(cli["plugins"], ["./tracker-tui"])  # the TUI keeps its trackers, not oh-my-opencode
        self.assertEqual(cli["theme"], {"name": "orng"})
        os.rmdir(os.path.join(self.user, "gh"))  # removed from ~/.config -> link goes
        profile.ensure_home()
        self.assertFalse(os.path.lexists(os.path.join(home, "gh")))

    def test_worker_env_and_command_use_the_profile(self):
        env = ops.worker_env()
        self.assertEqual(env["XDG_CONFIG_HOME"], self.home)
        self.assertEqual(env["OPENCODE_CONFIG"], ops.guard_config())
        cmd = ops._worker_command({"slug": "s1", "dir": "/w", "port": 4101, "agent": "oc-fast"}, False)
        self.assertIn("OC_PROFILE=isolated", cmd)  # the tab's login shell does not see Claude's environment
        self.assertIn(f"OC_STATE={core.STATE_DIR}", cmd)
        self.assertTrue(cmd.endswith("/bin/oc tab s1"))

    def test_inherit_mode_leaves_the_user_config_alone(self):
        with mock.patch.dict(os.environ, {"OC_PROFILE": "inherit"}):
            self.assertNotIn("XDG_CONFIG_HOME", ops.worker_env())
            self.assertNotEqual(core.default_agent(False), "oc-fast")


class Reaper(unittest.TestCase):
    """The worker tab closes once Claude no longer uses it - never mid-work."""
    LIMIT = 20 * 60

    def run_polls(self, states, owner_alive, step=15):
        c, reason = reaper.Clock(), None
        for i, state in enumerate(states):
            reason = reaper.decide(c, {"state": state, "owner": 42, "owner_alive": owner_alive}, 1000 + i * step,
                                   self.LIMIT)
            if reason:
                return i, reason
        return None, None

    def test_idle_job_of_a_live_session_closes_after_the_limit(self):
        i, reason = self.run_polls(["done"] * 200, owner_alive=True)
        self.assertEqual(i, self.LIMIT // 15)
        self.assertIn("idle", reason)

    def test_activity_resets_the_idle_clock(self):
        polls = ["done"] * 70 + ["running"] + ["done"] * 70
        self.assertEqual(self.run_polls(polls, owner_alive=True), (None, None))

    def test_ended_session_closes_an_idle_job_after_the_grace(self):
        i, reason = self.run_polls(["done"] * 20, owner_alive=False)
        self.assertEqual(i, reaper.OWNER_GRACE_S // 15)
        self.assertIn("ended", reason)

    def test_busy_or_asking_worker_is_never_stopped(self):
        for state in ("running", "waiting", "starting"):
            self.assertEqual(self.run_polls([state] * 400, owner_alive=False), (None, None), state)

    def test_orphan_that_finishes_later_closes_after_the_grace(self):
        i, _ = self.run_polls(["running"] * 20 + ["done"] * 20, owner_alive=False)
        self.assertEqual(i, 20)  # orphaned long ago; closes as soon as it is idle

    def test_job_without_owner_only_uses_the_idle_limit(self):
        c = reaper.Clock()
        for t in range(0, 600, 15):
            self.assertIsNone(reaper.decide(c, {"state": "done", "owner": None, "owner_alive": False}, t, self.LIMIT))

    def test_idle_limit_zero_disables_it(self):
        c = reaper.Clock()
        for t in range(0, 100000, 600):
            self.assertIsNone(reaper.decide(c, {"state": "done", "owner": 42, "owner_alive": True}, t, 0))
        with mock.patch.dict(os.environ, {"OC_IDLE_CLOSE_MIN": "0"}):
            self.assertEqual(reaper.idle_close_s(), 0)

    def test_tab_starts_the_reaper_on_the_worker_pid(self):
        with mock.patch.dict(os.environ, {"OC_IDLE_CLOSE_MIN": "3"}):
            cmd = ops._worker_command({"slug": "s1", "dir": "/w", "port": 4101, "agent": "oc-fast"}, False)
        reap, _, run = cmd.rpartition("; ")
        self.assertIn("/bin/oc reap s1 $$ 180 ", reap)  # the limit travels with the command
        self.assertTrue(reap.endswith("&)"))  # in the background, before exec
        self.assertTrue(run.startswith("exec "))

    def test_send_reopens_a_closed_worker(self):
        srv = start_fake()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        core.save_job({"slug": "r1", "dir": d, "port": srv.server_port, "session": "ses_1", "agent": "fixer",
                       "created": time.time(), "closed_reason": "idle for 20 min", "password": "pw"})
        self.addCleanup(core.delete_job, "r1")
        real_popen = subprocess.Popen  # git still runs (snapshot); only the worker launch is faked
        for mode in ("", "window"):  # background by default, a tab when the option asks for one
            core.save_job(dict(core.load_job("r1"), closed_reason="idle for 20 min"))
            FakeOpenCode.replies = []
            with mock.patch.dict(os.environ, {"OC_OPEN": mode}), \
                    mock.patch.object(core.Worker, "alive", return_value=False), \
                    mock.patch.object(ops, "_wait_alive", return_value=True), \
                    mock.patch.object(ops.subprocess, "Popen", side_effect=self.fake_bash(real_popen)) as popen, \
                    mock.patch.object(ops.terminal, "open_tab", return_value="tab") as tab:
                out = ops.send("r1", "fix it")
            bash = [c for c in popen.call_args_list if c[0][0][1:2] == ["-lc"]]
            self.assertEqual((len(bash), tab.call_count), (1, 0) if mode == "" else (0, 1), mode)
            self.assertIn("restarted", out)
            self.assertNotIn("closed_reason", core.load_job("r1"))
            self.assertIn(("/api/session/ses_1/prompt", {"text": "fix it"}), FakeOpenCode.replies)

    @staticmethod
    def fake_bash(real):
        return lambda argv, **kw: mock.Mock() if argv[1:2] == ["-lc"] else real(argv, **kw)

    def test_background_worker_runs_without_terminal(self):
        core.save_job({"slug": "h1", "dir": "/w", "port": 4101, "agent": "oc-fast", "created": time.time()})
        self.addCleanup(core.delete_job, "h1")
        with mock.patch.dict(os.environ, {"OC_OPEN": ""}), \
                mock.patch.object(ops.subprocess, "Popen") as popen, \
                mock.patch.object(ops.terminal, "open_tab") as tab:
            out = ops._run_worker(core.load_job("h1"), False)
        tab.assert_not_called()
        argv, kw = popen.call_args
        self.assertEqual(argv[0][1], "-lc")  # the user's login shell, as in a tab
        self.assertTrue(argv[0][2].endswith("/bin/oc tab h1 --headless"), argv[0][2])
        self.assertTrue(kw["start_new_session"])  # no terminal, not killed with Claude's
        self.assertTrue(core.load_job("h1")["headless"])
        self.assertIn("/oc:show", out)

    def test_open_attaches_a_window_to_a_background_worker_only(self):
        core.save_job({"slug": "h2", "dir": "/w", "port": 4101, "created": time.time(), "headless": True})
        self.addCleanup(core.delete_job, "h2")
        with mock.patch.object(core.Worker, "alive", return_value=True), \
                mock.patch.object(ops.terminal, "open_tab", return_value="window") as tab:
            ops.open_("h2")
            self.assertTrue(tab.call_args[0][1].endswith("/bin/oc attach h2"), tab.call_args[0][1])
            self.assertIn("OC_STATE=", tab.call_args[0][1])  # the window's login shell lacks Claude's env
            tab.reset_mock()
            core.save_job(dict(core.load_job("h2"), headless=False))
            self.assertIn("its tab is open", ops.open_("h2"))
            tab.assert_not_called()

    def test_close_ends_attached_windows_too(self):
        core.save_job({"slug": "c2", "dir": "/w", "port": 4101, "created": time.time(), "viewers": [111, 222]})
        self.addCleanup(core.delete_job, "c2")
        ps = {111: "opencode --server http://127.0.0.1:4101 --session ses_1", 222: "vim notes"}  # 222: PID reused
        killed = []
        with mock.patch.object(ops, "_listener_pids", return_value=[]), \
                mock.patch.object(core, "pid_alive", side_effect=lambda p: p in ps and p not in killed), \
                mock.patch.object(ops.subprocess, "run", side_effect=lambda a, **k: subprocess.CompletedProcess(
                    a, 0, stdout=ps.get(int(a[-1]), ""))), \
                mock.patch.object(ops.os, "kill", side_effect=lambda p, s: killed.append(p)), \
                mock.patch.object(ops.time, "sleep"), \
                mock.patch.object(ops.terminal, "close_dead_tabs"):
            ops.close("c2")
        self.assertEqual(killed, [111])
        self.assertNotIn("viewers", core.load_job("c2"))

    def test_close_waits_for_the_worker_before_closing_its_tab(self):
        core.save_job({"slug": "c1", "dir": "/w", "port": 4101, "created": time.time()})
        self.addCleanup(core.delete_job, "c1")
        alive = [True] * 8 + [False] * 100  # OpenCode takes a while to exit
        seen = []
        with mock.patch.object(ops, "_listener_pids", return_value=[4242]), \
                mock.patch.object(ops.os, "kill"), \
                mock.patch.object(ops.time, "sleep"), \
                mock.patch.object(core, "pid_alive", side_effect=lambda p: alive.pop(0)), \
                mock.patch.object(ops.terminal, "close_dead_tabs",
                                  side_effect=lambda: seen.append(len(alive))) as cdt:
            ops.close("c1")
        cdt.assert_called_once()
        self.assertEqual(seen, [99])  # called only after the process was gone


class DeadTabs(unittest.TestCase):
    """Worker tabs are found by window id, and only tabs without a process close."""

    def setUp(self):
        self.addCleanup(lambda: os.path.exists(terminal.WINDOWS_FILE) and os.remove(terminal.WINDOWS_FILE))

    def test_opened_window_is_remembered(self):
        terminal.remember_window("tab 123")
        terminal.remember_window("window 456")
        terminal.remember_window("")  # nothing opened
        self.assertEqual(terminal._our_windows(), [123, 456])

    def test_script_closes_only_tabs_without_process(self):
        script = terminal.close_dead_script([123, 456])
        self.assertIn("set ours to {123, 456}", script)
        self.assertIn("(count of (processes of t)) is 0 and", script)

    def test_windows_that_are_gone_are_forgotten(self):
        terminal._save_windows([123, 456])
        done = subprocess.CompletedProcess([], 0, stdout="456 789 \n", stderr="")
        with mock.patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal", "OC_TERMINAL": ""}), \
                mock.patch.object(terminal.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(terminal.subprocess, "run", return_value=done):
            terminal.close_dead_tabs()
        self.assertEqual(terminal._our_windows(), [456])

    def test_background_is_the_default_window_and_tab_are_options(self):
        for mode, expect in ((None, "headless"), ("", "headless"), ("window", "window"), ("tab", "tab"),
                             ("nonsense", "headless")):
            with mock.patch.dict(os.environ, {"OC_OPEN": mode or ""}):
                if mode is None:
                    os.environ.pop("OC_OPEN", None)
                self.assertEqual(terminal.open_mode(), expect, mode)

    def test_a_terminal_opens_as_window_unless_tab_is_asked_for(self):
        env = {"TERM_PROGRAM": "Apple_Terminal", "OC_TERMINAL": "", "TMUX": ""}
        for mode, expect in ((None, "window"), ("window", "window"), ("tab", "tab"), ("nonsense", "window")):
            e = dict(env, OC_OPEN=mode) if mode else env
            with mock.patch.dict(os.environ, e), \
                    mock.patch.object(terminal, "close_dead_tabs"), \
                    mock.patch.object(terminal, "_terminal_app_window", return_value="window 7") as win, \
                    mock.patch.object(terminal, "_terminal_app_tab", return_value="tab 8") as tab:
                if mode is None:
                    os.environ.pop("OC_OPEN", None)
                out = terminal.open_tab("OC x", "true")
            self.assertEqual((win.called, tab.called), (expect == "window", expect == "tab"), mode)
            self.assertIn(f"Terminal {expect}:", out)


class DelegationControl(unittest.TestCase):
    """The user decides whether work goes to OpenCode - enforced by hooks, not by the model."""

    def setUp(self):
        core.set_autonomy(None)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_AUTONOMY": "", "OC_AUTONOMY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(core.set_autonomy, None)
        self.pid = mock.patch.object(core, "claude_pid", return_value=777)
        self.pid.start()
        self.addCleanup(self.pid.stop)

    def gate(self, mode, prompt):
        core.set_autonomy(mode)
        hooks.record_prompt(prompt)
        out = hooks.delegate_gate("x")
        return out and out["hookSpecificOutput"]["permissionDecision"]

    def test_default_mode_is_ask(self):
        self.assertEqual(core.autonomy(), "ask")

    def test_override_beats_plugin_option(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_AUTONOMY": "auto"}):
            self.assertEqual(core.autonomy(), "auto")
            core.set_autonomy("off")
            self.assertEqual(core.autonomy(), "off")
            core.set_autonomy("default")
            self.assertEqual(core.autonomy(), "auto")
        with self.assertRaises(core.OcError):
            core.set_autonomy("sometimes")

    def test_ask_always_shows_the_dialog(self):
        self.assertEqual(self.gate("ask", "write tests for the cart"), "ask")
        self.assertEqual(self.gate("ask", "mach das mit OpenCode"), "ask")

    def test_off_refuses_unless_the_user_asked_then_still_confirms(self):
        self.assertEqual(self.gate("off", "write tests for the cart"), "deny")
        self.assertEqual(self.gate("off", "gib das an OpenCode"), "ask")
        self.assertEqual(self.gate("off", "and now the docs"), "deny")  # only the LATEST prompt counts

    def test_request_belongs_to_its_own_session(self):
        core.set_autonomy("off")
        hooks.record_prompt("mach das mit OpenCode")
        with mock.patch.object(core, "claude_pid", return_value=778):
            self.assertEqual(hooks.delegate_gate("x")["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_mechanical_and_auto_pass(self):
        self.assertIsNone(self.gate("mechanical", "x"))
        self.assertIsNone(self.gate("auto", "x"))

    def test_dialog_shows_what_is_delegated(self):
        s = hooks._task_summary({"slug": "t1", "title": "#1 Tests", "agent": "oc-fast", "task": "a " * 400})
        self.assertIn("#1 Tests | agent oc-fast | task: a a", s)
        self.assertTrue(s.endswith("…"))

    def run_bash_hook(self, command):
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": command}}))), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as out:
            hooks.pre_bash()
        return out.getvalue()

    def test_cli_start_is_gated_too(self):
        core.set_autonomy("ask")
        hooks.record_prompt("x")
        for cmd in ("oc start t1 task.md", "cd /r && oc start t1 -", "~/.local/state/oc-connect/bin/oc start t1 x"):
            self.assertIn('"ask"', self.run_bash_hook(cmd), cmd)
        for cmd in ("oc status", "echo doc start", "cargo start"):
            self.assertEqual(self.run_bash_hook(cmd), "", cmd)

    def test_instructions_follow_the_mode(self):
        core.set_autonomy("off")
        with mock.patch.object(core, "opencode_setup",
                               return_value={"agents": {}, "profile": "isolated", "oh_my_opencode": False}):
            self.assertIn("WHEN TO DELEGATE (off)", mcp.instructions())


class Release(unittest.TestCase):
    def test_version_matches_plugin_manifest(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            self.assertEqual(core.VERSION, json.load(fh)["version"])

    def test_opencode_major(self):
        self.assertEqual(core.opencode_major("1.19.4"), 1)
        self.assertEqual(core.opencode_major("opencode v2.0.29"), 2)
        self.assertIsNone(core.opencode_major(None))

    def test_opencode_1_is_refused_with_a_clear_message(self):
        with mock.patch.object(core, "_opencode_version", return_value="1.19.4"):
            with self.assertRaises(core.OcError) as ctx:
                core.require_opencode_2()
        self.assertIn("needs OpenCode 2", str(ctx.exception))
        self.assertIn("oc-connect#v1", str(ctx.exception))  # where OpenCode 1 users go
        for v in ("opencode v2.1.4", None):
            with mock.patch.object(core, "_opencode_version", return_value=v):
                core.require_opencode_2()

    def test_oc_opencode_points_to_a_second_install(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "opencode 2")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\necho opencode v2.1.4\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"OC_OPENCODE": fake}):
                self.assertEqual(core._opencode_version(), "opencode v2.1.4")
                core.require_opencode_2()
                self.assertEqual(core.opencode_setup()["opencode"], fake)
                cmd = ops._worker_command({"slug": "s1", "dir": "/w", "port": 4101, "agent": "oc-fast"}, False)
                self.assertIn(f"OC_OPENCODE='{fake}'", cmd)  # travels to the tab
            with mock.patch.dict(os.environ, {"OC_OPENCODE": "", "PATH": d + os.pathsep + os.environ["PATH"]}):
                os.rename(fake, os.path.join(d, "opencode"))
                cmd = ops._worker_command({"slug": "s1", "dir": "/w", "port": 4101, "agent": "oc-fast"}, False)
                # resolved in Claude's environment: the tab's login shell may find another OpenCode first
                self.assertIn(f"OC_OPENCODE={os.path.join(d, 'opencode')} ", cmd)

    def test_selftest_names_the_provider_error(self):
        err = {"type": "provider.invalid-request", "message": "Allow free endpoints in your workspace"}
        msgs = [{"type": "user", "time": {"created": 1}, "text": "x"},
                {"type": "assistant", "time": {"created": 2}, "content": [], "finish": "error", "error": err},
                {"type": "idle", "time": {"created": 3}, "outcome": "failed", "error": err}]
        self.assertEqual(ops.turn_error(msgs), "provider.invalid-request: Allow free endpoints in your workspace")
        msgs[-1] = {"type": "idle", "time": {"created": 3}, "outcome": "succeeded"}
        self.assertIsNone(ops.turn_error(msgs))
        self.assertIsNone(ops.turn_error([]))

    def test_guard_reads_the_1x_format_in_rule_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "guard.json")
            with open(path, "w") as fh:
                json.dump({"permission": {"bash": {"*": "allow", "git push*": "deny"},
                                          "external_directory": "deny"}}, fh)
            self.assertEqual(ops.guard_rules(path), [
                {"action": "shell", "resource": "*", "effect": "allow"},
                {"action": "shell", "resource": "git push*", "effect": "deny"},   # last match wins
                {"action": "external_directory", "resource": "*", "effect": "deny"}])
        template = ops.guard_rules(os.path.join(ROOT, "templates", "guard.json"))
        self.assertEqual(template[0], {"action": "shell", "resource": "*", "effect": "allow"})
        self.assertIn({"action": "shell", "resource": "git push*", "effect": "deny"}, template)

    def test_selftest_shows_a_changed_command(self):
        part = {"type": "tool", "name": "shell", "state": {"status": "completed",
                                                          "input": {"command": "cd /tmp/r && git push origin x"}}}
        self.assertEqual(ops.ran_as(part, "git push origin x"), "  (ran as: cd /tmp/r && git push origin x)")
        part["state"]["input"]["command"] = "git push origin x"
        self.assertEqual(ops.ran_as(part, "git push origin x"), "")
        self.assertEqual(ops.ran_as({"state": {}}, "git push origin x"), "")

    def test_selftest_does_not_count_another_command_as_blocked(self):
        def part(actual):
            return {"state": {"input": {"command": actual}}}
        cmd = 'bash -c "git push origin x"'
        self.assertFalse(ops.ran_the_command(part("git remote -v; git status -sb"), cmd))
        self.assertTrue(ops.ran_the_command(part(cmd), cmd))
        self.assertTrue(ops.ran_the_command(part("cd /tmp/r && " + cmd), cmd))
        self.assertTrue(ops.ran_the_command({"state": {}}, cmd))


if __name__ == "__main__":
    unittest.main()
