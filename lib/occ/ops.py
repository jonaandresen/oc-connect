"""Job operations shared by the CLI and the MCP server.

Every function returns a human-readable report (str) or raises ``OcError``.
Nothing here commits, pushes or opens pull requests: integration is Claude's
job after reviewing the diff. Workers are technically blocked from it by the
guard config (see ``guard_config``), and ``oc selftest`` proves the block.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from . import core, terminal
from .core import OcError

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUARD_FILE = os.path.join(core.STATE_DIR, "guard.json")
START_GRACE_S = 20      # a fresh prompt may not show as busy yet
LOCKFILES = [
    ("package-lock.json", ["npm", "ci", "--no-audit", "--no-fund"]),
    ("pnpm-lock.yaml", ["pnpm", "install", "--frozen-lockfile"]),
    ("yarn.lock", ["yarn", "install", "--frozen-lockfile"]),
    ("bun.lock", ["bun", "install", "--frozen-lockfile"]),
    ("bun.lockb", ["bun", "install", "--frozen-lockfile"]),
]


# --------------------------------------------------------------------------- guard

def guard_config() -> str:
    """Path of the permission guard every worker runs with.

    Lives in the state dir, not in the user's repository, so nothing needs to be
    committed or excluded. Created from the template on first use; edit it to
    tighten or loosen the rules for all repos. A repo-local, untracked
    ``opencode.json`` is copied into each worktree on top of it.
    """
    if not os.path.exists(GUARD_FILE):
        os.makedirs(core.STATE_DIR, exist_ok=True)
        shutil.copy(os.path.join(PLUGIN_ROOT, "templates", "guard.json"), GUARD_FILE)
    return GUARD_FILE


# --------------------------------------------------------------------------- helpers

def _port_free(port: int) -> bool:
    if core.Worker(port, "/").alive(timeout=0.3):
        return False
    res = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True)
    return res.returncode != 0


def free_port() -> int:
    used = {j.get("port") for j in core.load_jobs()}
    for port in core.PORT_RANGE:
        if port not in used and _port_free(port):
            return port
    raise OcError(f"no free port in {core.PORT_RANGE.start}-{core.PORT_RANGE.stop - 1}")


def _install_deps(wt: str) -> List[str]:
    """Install dependencies wherever a lockfile sits (max. two levels deep).

    Always the frozen/ci variant: a plain ``npm install`` rewrites the lockfile.
    """
    if os.environ.get("OC_INSTALL", "1") == "0":
        return ["dependency install skipped (OC_INSTALL=0)"]
    notes = []
    for root, dirs, files in os.walk(wt):
        depth = root[len(wt):].count(os.sep)
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git") and depth < 2]
        for lock, cmd in LOCKFILES:
            if lock in files:
                if not shutil.which(cmd[0]):
                    notes.append(f"WARNING: {lock} in {os.path.relpath(root, wt)} but `{cmd[0]}` not installed")
                    break
                res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=900)
                rel = os.path.relpath(root, wt)
                notes.append(f"{' '.join(cmd[:2])} in {rel}: " + ("ok" if res.returncode == 0 else
                             "FAILED - " + (res.stderr or res.stdout).strip().splitlines()[-1][:200]))
                break
    return notes


def _worker_command(job: Dict[str, Any], session: Optional[str]) -> str:
    """Shell command that runs the OpenCode TUI as the job's server.

    ``exec`` so that closing the tab ends the worker, and ending the worker
    closes the tab. ``--port`` makes the TUI process itself the HTTP server the
    plugin talks to.

    No ``--agent`` here: the TUI accepts only PRIMARY agents, and helpers such
    as oh-my-opencode's ``fixer`` are subagents - ``opencode --agent fixer``
    does not find them. The HTTP API does accept them, so the job's agent is
    sent with every prompt instead (verified: a prompt with agent ``fixer`` is
    answered by fixer's own model).
    """
    parts = ["exec", "env", f"OPENCODE_CONFIG={shlex.quote(guard_config())}", "opencode",
             "--port", str(job["port"])]
    if session:
        parts += ["-s", shlex.quote(session)]
    return " ".join(parts)


def _wait_alive(w: core.Worker, seconds: float = 30) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if w.alive(timeout=0.5):
            return True
        time.sleep(0.5)
    return False


def browser_url(job: Dict[str, Any]) -> str:
    import base64
    base = f"http://127.0.0.1:{job['port']}"
    return f"{base}/server/{base64.b64encode(base.encode()).decode()}/session/{job.get('session', '')}"


# --------------------------------------------------------------------------- start / send

def start(slug: str, task: str, title: Optional[str] = None, branch: Optional[str] = None,
          agent: Optional[str] = None, repo: Optional[str] = None, base: Optional[str] = None,
          cwd: Optional[str] = None, model: Optional[str] = None) -> str:
    """Create worktree, start the worker in a new tab, create the session, send the task."""
    core.valid_slug(slug)
    if not task.strip():
        raise OcError("empty task")
    if not shutil.which("opencode"):
        raise OcError("`opencode` not found on PATH - install it from https://opencode.ai")
    if os.path.exists(core.job_path(slug)):
        raise OcError(f"job {slug} exists already - pick another slug or `oc done {slug}`")
    repo = os.path.abspath(repo) if repo else core.repo_root(cwd)
    wt = os.path.join(core.worktree_root(repo), slug)
    if os.path.exists(wt):
        raise OcError(f"worktree exists already: {wt}")
    branch = branch or f"oc/{slug}"
    agent = agent or core.default_agent()
    model = model or os.environ.get("OC_MODEL") or None
    report = []

    core.git("fetch", "-q", "origin", cwd=repo, check=False, timeout=60)
    base = base or core.base_ref(repo)
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    core.git("worktree", "add", "-q", "-b", branch, wt, base, cwd=repo)
    report.append(f"worktree {wt} (branch {branch} from {base})")

    copied = []
    for rel in core.env_files(repo) + (["opencode.json"] if _untracked(repo, "opencode.json") else []):
        dst = os.path.join(wt, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(repo, rel), dst)
        copied.append(rel)
    if copied:
        report.append("copied: " + ", ".join(copied))
    report += _install_deps(wt)

    job = {"slug": slug, "title": title or slug, "branch": branch, "dir": wt, "repo": repo,
           "port": free_port(), "agent": agent, "model": model, "created": time.time(), "base": base}
    core.save_job(job)
    report.append(terminal.open_tab(f"{terminal.TITLE_PREFIX} {slug}", _worker_command(job, None), cwd=wt))

    w = core.worker_of(job)
    if not _wait_alive(w):
        raise OcError(f"worker on port {job['port']} did not come up - look at its tab")
    names = [a.get("name") for a in (w.get("/agent", timeout=10) or [])]
    if names and agent not in names:
        close(slug)
        done(slug)
        raise OcError(f"OpenCode has no agent {agent!r}. Available: {', '.join(n for n in names if n)}")
    job["session"] = w.create_session(job["title"])
    core.save_job(job)
    w.select_session(job["session"])
    w.prompt(job["session"], task, agent, model)
    job["sent_at"] = time.time()
    core.save_job(job)
    report.append(f"session {job['session']} on port {job['port']}, agent {agent}" + (f", model {model}" if model else ""))
    report.append(f"browser: {browser_url(job)}")
    return "\n".join(report)


def _untracked(repo: str, rel: str) -> bool:
    if not os.path.isfile(os.path.join(repo, rel)):
        return False
    return core.git("ls-files", "--error-unmatch", rel, cwd=repo, check=False, timeout=5) == ""


def send(slug: str, text: str) -> str:
    """Follow-up message (a correction) into the same session."""
    job = core.load_job(slug)
    w = core.worker_of(job)
    if not w.alive():
        raise OcError(f"worker {slug} is not running - `oc open {slug}` restarts it")
    w.prompt(job["session"], text, job.get("agent"), job.get("model"))
    job["sent_at"] = time.time()
    job.pop("finished_at", None)
    core.save_job(job)
    return f"sent to {slug}"


# --------------------------------------------------------------------------- status / wait

def status(slug: str, detail: bool = False) -> Dict[str, Any]:
    """Job status with the start grace period applied."""
    job = core.load_job(slug)
    st = core.job_status(job, detail=detail)
    if st["state"] == "done" and time.time() - job.get("sent_at", 0) < START_GRACE_S:
        st["state"] = "running"
    return st


def status_all(detail: bool = False) -> List[Dict[str, Any]]:
    out = []
    for job in core.load_jobs():
        try:
            out.append(status(job["slug"], detail=detail))
        except OcError:
            continue
    return out


def report(slug: str) -> str:
    """Final message, cost, and what changed in the worktree."""
    job = core.load_job(slug)
    w = core.worker_of(job)
    lines = []
    if job.get("session") and w.alive():
        rep = core.final_report(w.messages(job["session"], timeout=15))
        if rep["error"]:
            lines.append(f"ERROR: {json.dumps(rep['error'])[:500]}")
        lines.append(f"cost so far: ${rep['cost']:.2f}" + (f" ({rep['model']})" if rep["model"] else ""))
        lines.append("--- final message of the worker ---")
        lines.append(rep["text"][-6000:] or "(no reply)")
    else:
        lines.append("(worker not running - no final message available)")
    lines.append(f"--- git status {job['dir']} ---")
    lines.append(core.git("status", "--short", cwd=job["dir"], check=False) or "(clean)")
    d = core.diffstat(job["dir"])
    lines.append(f"{d['files']} files, +{d['plus']} -{d['minus']}")
    return "\n".join(lines)


def format_pending(slug: str, pending: List[Dict[str, Any]]) -> str:
    lines = []
    for p in pending:
        if p["kind"] == "question":
            opts = " | ".join(p["options"])
            lines.append(f"QUESTION: {p['text']}" + (f"  [options: {opts}]" if opts else ""))
        else:
            lines.append(f"PERMISSION: {p['text']} {' '.join(p['patterns'])}")
    lines.append(f"answer: oc answer {slug} \"<answer>\"  |  permission: oc permit {slug} once|reject")
    return "\n".join(lines)


def wait(slug: str, minutes: float = 9.5, poll: float = 10) -> Tuple[int, str]:
    """Block until the job needs attention.

    Exit codes: 0 done, 2 waiting for an answer, 3 worker stopped, 4 timeout
    (still running - just call again). The default stays below the 10 minute
    limit of Claude Code's Bash tool.
    """
    start_t = time.time()
    while True:
        st = status(slug)
        mins = (time.time() - start_t) / 60
        if st["state"] == "stopped":
            return 3, f"== {slug}: worker process ended (tab closed?) - `oc open {slug}` restarts it"
        if st["state"] == "waiting":
            return 2, f"== {slug} WAITING FOR AN ANSWER\n" + format_pending(slug, st["pending"])
        if st["state"] == "done":
            _mark_finished(slug)
            return 0, f"== {slug} finished\n" + report(slug)
        if mins >= minutes:
            prog = f" ({st.get('todo_done')}/{st.get('todo_total')} todos)" if st.get("todo_total") else ""
            return 4, f"== {slug} still running{prog} - call wait again"
        time.sleep(poll)


def _mark_finished(slug: str) -> None:
    job = core.load_job(slug)
    if not job.get("finished_at") or job["finished_at"] < job.get("sent_at", 0):
        job["finished_at"] = time.time()
        core.save_job(job)


# --------------------------------------------------------------------------- answers

def answer(slug: str, answers: List[str]) -> str:
    """Answer the oldest open question; one answer per sub-question, in order."""
    job = core.load_job(slug)
    w = core.worker_of(job)
    qs = [p for p in w.pending(job["session"]) if p["kind"] == "question"]
    if not qs:
        raise OcError(f"no open question in {slug}")
    qid = qs[0]["id"]
    w.request("POST", f"/question/{qid}/reply", {"answers": [[a] for a in answers]}, timeout=10)
    return f"answered question in {slug}"


def permit(slug: str, reply: str) -> str:
    if reply not in ("once", "always", "reject"):
        raise OcError("reply must be once, always or reject")
    job = core.load_job(slug)
    w = core.worker_of(job)
    ps = [p for p in w.pending(job["session"]) if p["kind"] == "permission"]
    if not ps:
        raise OcError(f"no open permission request in {slug}")
    w.request("POST", f"/permission/{ps[0]['id']}/reply", {"reply": reply}, timeout=10)
    return f"permission {reply} for {slug}: {ps[0]['text']} {' '.join(ps[0]['patterns'])}"


# --------------------------------------------------------------------------- tabs / lifecycle

def open_(slug: str) -> str:
    """Open the job's tab; restarts the worker on the same port and session if needed."""
    job = core.load_job(slug)
    if core.worker_of(job).alive():
        return f"worker {slug} is running (port {job['port']}) - its tab is open"
    return terminal.open_tab(f"{terminal.TITLE_PREFIX} {slug}", _worker_command(job, job.get("session")), cwd=job["dir"])


def _listener_pids(port: int) -> List[int]:
    res = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    return [int(p) for p in res.stdout.split() if p.isdigit()]


def close(slug: str) -> str:
    """Stop the worker. Its tab closes because the tab ``exec``-ed it."""
    job = core.load_job(slug)
    pids = _listener_pids(int(job["port"]))
    if not pids:
        return f"worker {slug} is not running"
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(1)
    terminal.close_dead_tabs()
    return f"worker {slug} stopped (port {job['port']})"


def done(slug: str) -> str:
    """Stop the worker and remove the worktree. The branch stays."""
    job = core.load_job(slug)
    wt, repo = job["dir"], job.get("repo") or core.repo_root()
    # Never remove the main repository - only folders under the worktree root.
    if not os.path.abspath(wt).startswith(os.path.abspath(core.worktree_root(repo)) + os.sep):
        raise OcError(f"{wt} is not under {core.worktree_root(repo)} - refusing to remove it")
    close(slug)
    if os.path.exists(wt):
        core.git("worktree", "remove", "--force", wt, cwd=repo)
    core.delete_job(slug)
    return f"removed worktree {wt} (branch {job.get('branch')} kept)"


def set_pr(slug: str, pr: str) -> str:
    job = core.load_job(slug)
    job["pr"] = pr
    core.save_job(job)
    return f"{slug}: PR {pr}"


# --------------------------------------------------------------------------- selftest

def selftest(model: Optional[str] = None, cwd: Optional[str] = None) -> str:
    """Prove the guard blocks ``git push`` and ``docker``.

    Judged by the STATUS of the tool calls, not by what the model writes - a
    model can claim "rejected" for a command that ran.
    """
    repo = core.repo_root(cwd)
    port = free_port()
    env = dict(os.environ, OPENCODE_CONFIG=guard_config())
    proc = subprocess.Popen(["opencode", "serve", "--port", str(port)], cwd=repo, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        w = core.Worker(port, repo)
        if not _wait_alive(w):
            raise OcError("opencode serve did not start")
        sid = w.create_session("oc-connect selftest")
        body: Dict[str, Any] = {"agent": "build", "parts": [{"type": "text", "text": (
            "Guard test. Run EXACTLY these bash commands one by one and report only RAN or BLOCKED for each: "
            "1) git status --short  2) git push --dry-run origin HEAD:refs/heads/oc-selftest  3) docker ps. "
            "No workarounds.")}]}
        if model:
            prov, _, mid = model.partition("/")
            body["model"] = {"providerID": prov, "modelID": mid}
        w.request("POST", f"/session/{sid}/message", body, timeout=240)
        # The reply holds only the LAST step; tool calls sit in earlier messages.
        msgs = w.messages(sid, timeout=15)
        w.request("DELETE", f"/session/{sid}", timeout=10)
    finally:
        proc.terminate()
    calls = []
    for msg in msgs:
        for part in msg.get("parts", []):
            if part.get("type") == "tool" and part.get("tool") == "bash":
                s = part.get("state", {})
                calls.append((s.get("input", {}).get("command", ""), s.get("status")))
    lines = [f"  {s:>9}  {c}" for c, s in calls]
    push = [s for c, s in calls if c.startswith("git push")]
    dock = [s for c, s in calls if c.startswith("docker")]
    ok = bool(push and dock and all(s == "error" for s in push + dock))
    lines.append("RESULT: guard works (git push and docker were blocked)" if ok else
                 "RESULT: NOT PROVEN - inspect the calls above; the model may have skipped them")
    return "\n".join(lines)
