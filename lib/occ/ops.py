"""Job operations shared by the CLI and the MCP server.

Every function returns a human-readable report (str) or raises ``OcError``.
Nothing here commits, pushes or opens pull requests: integration is Claude's
job after reviewing the diff.

Workers are kept from integrating in two layers (see ``worker_env``): the
OpenCode permission guard, and git/docker configuration in the worker's
environment. Neither is a sandbox - a model that sets out to escape can - but
together they stop the realistic case: a cheap model following a skill that
says "commit and push when done". ``oc selftest`` proves both layers.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import core, profile, terminal
from .core import OcError

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUARD_FILE = os.path.join(core.STATE_DIR, "guard.json")
START_GRACE_S = 20       # a fresh prompt may not show as busy yet
START_TIMEOUT_S = 1200   # dependency install runs in the tab before OpenCode starts
PUSH_DISABLED = "/oc-connect/push-disabled"
LOCKFILES = [
    ("package-lock.json", "npm ci --no-audit --no-fund"),
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn install --frozen-lockfile"),
    ("bun.lock", "bun install --frozen-lockfile"),
    ("bun.lockb", "bun install --frozen-lockfile"),
]


# --------------------------------------------------------------------------- guard

def guard_config() -> str:
    """Path of the OpenCode permission guard every worker runs with.

    Lives in the state dir, not in the user's repository. Created from the
    template on first use; edit it to change the rules for all repos. A
    repo-local, untracked ``opencode.json`` is copied into each worktree on top.
    """
    if not os.path.exists(GUARD_FILE):
        os.makedirs(core.STATE_DIR, exist_ok=True)
        shutil.copy(os.path.join(PLUGIN_ROOT, "templates", "guard.json"), GUARD_FILE)
    return GUARD_FILE


def worker_env() -> Dict[str, str]:
    """Environment of every worker - the second guard layer.

    The permission guard matches command strings, so ``bash -c "git push"``,
    ``command git push`` and ``/usr/bin/git push`` pass it (measured). These
    settings act on the programs themselves, in every child process:

    * ``remote.origin.pushurl`` and a catch-all ``pushInsteadOf`` rewrite every
      push URL, explicit ones included, to a path that does not exist. Fetch
      keeps working.
    * ``DOCKER_HOST`` points to a socket that does not exist.
    * ``GIT_SSH_COMMAND=false`` stops git over ssh.
    """
    env = {"OPENCODE_CONFIG": guard_config()}
    if profile.mode() == "isolated":
        env["XDG_CONFIG_HOME"] = profile.ensure_home()
    base = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)  # keep the user's own entries
    pairs = [("remote.origin.pushurl", PUSH_DISABLED), (f"url.{PUSH_DISABLED}/.pushInsteadOf", "")]
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{base + i}"] = key
        env[f"GIT_CONFIG_VALUE_{base + i}"] = value
    env["GIT_CONFIG_COUNT"] = str(base + len(pairs))
    env["DOCKER_HOST"] = "unix:///oc-connect/docker-disabled.sock"
    env["GIT_SSH_COMMAND"] = "false"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


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


def _install_commands(wt: str) -> List[Tuple[str, str]]:
    """(relative dir, command) for every lockfile up to two levels deep.

    Always the frozen/ci variant: a plain ``npm install`` rewrites the lockfile.
    """
    if os.environ.get("OC_INSTALL", "1") == "0":
        return []
    found = []
    for root, dirs, files in os.walk(wt):
        depth = root[len(wt):].count(os.sep)
        dirs[:] = sorted(d for d in dirs if d not in ("node_modules", ".git") and depth < 2)
        for lock, cmd in LOCKFILES:
            if lock in files:
                found.append((os.path.relpath(root, wt), cmd))
                break
    return found


def _worker_command(job: Dict[str, Any], session: Optional[str], install: bool) -> str:
    """Shell command for the job's tab: install dependencies, then run OpenCode.

    Installing in the tab keeps ``delegate`` fast and lets the human watch it.
    ``exec`` so that closing the tab ends the worker and ending the worker
    closes the tab. ``--port`` makes the TUI process itself the HTTP server.

    ``--agent`` only for the worker profile's own agents: the TUI accepts only
    PRIMARY agents, and oh-my-opencode's ``fixer`` is a subagent -
    ``opencode --agent fixer`` fails. The HTTP API accepts both, so the agent
    also goes with every prompt.
    """
    steps = []
    if install:
        for rel, cmd in _install_commands(job["dir"]):
            where = shlex.quote(os.path.join(job["dir"], rel))
            steps.append(f"echo '== oc-connect: {cmd} in {rel}'; (cd {where} && {cmd}) "
                         f"|| echo '== oc-connect: INSTALL FAILED in {rel}'")
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in worker_env().items())
    run = f"exec env {env} opencode --port {job['port']}" + (f" -s {shlex.quote(session)}" if session else "")
    if profile.mode() == "isolated" and job.get("agent") in profile.AGENTS:
        run += f" --agent {shlex.quote(job['agent'])}"  # primary agents: the tab shows the right one
    return "; ".join(steps + [run])


def _wait_alive(w: core.Worker, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if w.alive(timeout=0.5):
            return True
        time.sleep(1)
    return False


def browser_url(job: Dict[str, Any]) -> str:
    import base64
    base = f"http://127.0.0.1:{job['port']}"
    return f"{base}/server/{base64.b64encode(base.encode()).decode()}/session/{job.get('session', '')}"


def _untracked(repo: str, rel: str) -> bool:
    if not os.path.isfile(os.path.join(repo, rel)):
        return False
    return subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=repo,
                          capture_output=True).returncode != 0


# --------------------------------------------------------------------------- start / send

def start(slug: str, task: str, title: Optional[str] = None, branch: Optional[str] = None,
          agent: Optional[str] = None, repo: Optional[str] = None, base: Optional[str] = None,
          cwd: Optional[str] = None, model: Optional[str] = None, background: bool = False) -> str:
    """Create worktree, open the worker tab, then create the session and send the task.

    With ``background=True`` (MCP server) the second half runs in a thread and
    the call returns right after the tab opened; the monitor reports when the
    worker is running or why it failed (``start_error``).
    """
    core.valid_slug(slug)
    if not task.strip():
        raise OcError("empty task")
    if not shutil.which("opencode"):
        raise OcError("`opencode` not found on PATH - install it from https://opencode.ai")
    if os.path.exists(core.job_path(slug)):
        raise OcError(f"job {slug} exists already - pick another slug or finish it with `done`")
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
    base_sha = core.git("rev-parse", "--short", base, cwd=repo, check=False, timeout=5)
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    core.git("worktree", "add", "-q", "-b", branch, wt, base, cwd=repo)
    report.append(f"worktree {wt}\nbranch {branch} from {base} ({base_sha}) - local unpushed commits are NOT included")

    copied = []
    for rel in core.env_files(repo) + (["opencode.json"] if _untracked(repo, "opencode.json") else []):
        dst = os.path.join(wt, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(repo, rel), dst)
        copied.append(rel)
    if copied:
        report.append("copied: " + ", ".join(copied))
    installs = _install_commands(wt)
    if installs:
        report.append("installing in the tab: " + ", ".join(f"{c} ({r})" for r, c in installs))

    job = {"slug": slug, "title": title or slug, "branch": branch, "dir": wt, "repo": repo,
           "port": free_port(), "agent": agent, "model": model, "created": time.time(), "base": base,
           "owner": core.claude_pid()}
    core.save_job(job)

    if background:
        threading.Thread(target=_launch_safely, args=(slug, task), daemon=True).start()
        report.append(f"opening the worker tab (port {job['port']}, agent {agent}). The monitor reports when it "
                      "runs (with the browser link) or why it failed.")
        return "\n".join(report)
    report.append(_launch(slug, task))
    return "\n".join(report)


def _precreate_session(job: Dict[str, Any]) -> str:
    """Create the job's session BEFORE its tab opens, via a short-lived server.

    The tab then starts with ``-s <session>`` and shows the job right away.
    Switching an already running TUI (``/tui/select-session``) is unreliable:
    the port answers before the TUI listens, and the tab stayed on the start
    screen. Sessions live on disk, so the tab's own server finds this one.
    Also checks the agent name, so a typo fails before any tab opens.
    """
    port = free_port()
    while port == job["port"]:
        port += 1
    proc = subprocess.Popen(["opencode", "serve", "--port", str(port)], cwd=job["dir"],
                            env=dict(os.environ, **worker_env()),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        w = core.Worker(port, job["dir"])
        if not _wait_alive(w, 60):
            raise OcError("could not start OpenCode to create the session")
        names = [a.get("name") for a in (w.get("/agent", timeout=10) or [])]
        if names and job["agent"] not in names:
            raise OcError(f"OpenCode has no agent {job['agent']!r}. Available: {', '.join(n for n in names if n)}")
        return w.create_session(job["title"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _launch(slug: str, task: str) -> str:
    """Second half of ``start``: session, tab, wait for the worker, send the task."""
    job = core.load_job(slug)
    job["session"] = _precreate_session(job)
    core.save_job(job)
    job["tab"] = terminal.open_tab(f"{terminal.TITLE_PREFIX} {slug}", _worker_command(job, job["session"], True),
                                   cwd=job["dir"])
    core.save_job(job)
    w = core.worker_of(job)
    if not _wait_alive(w, START_TIMEOUT_S):
        raise OcError(f"worker on port {job['port']} did not come up - look at its tab")
    w.prompt(job["session"], task, job["agent"], job.get("model"))
    job["sent_at"] = time.time()
    core.save_job(job)
    return (f"{job['tab']}\nsession {job['session']} on port {job['port']}, agent {job['agent']}"
            + (f", model {job['model']}" if job.get("model") else "") + f"\nbrowser: {browser_url(job)}")


def _launch_safely(slug: str, task: str) -> None:
    try:
        _launch(slug, task)
    except Exception as exc:  # recorded for the monitor, never lost in a thread
        _set(slug, start_error=str(exc))


def send(slug: str, text: str) -> str:
    """Follow-up message (a correction) into the same session."""
    job = core.load_job(slug)
    w = core.worker_of(job)
    if not w.alive():
        raise OcError(f"worker {slug} is not running - open it first (`open`)")
    snapshot(slug, f"oc snapshot {slug}: before a follow-up message")  # a correction can make it worse
    w.prompt(job["session"], text, job.get("agent"), job.get("model"))
    job["sent_at"] = time.time()
    job.pop("review", None)  # new work, new review
    core.save_job(job)
    return f"sent to {slug}"


# --------------------------------------------------------------------------- status / wait

def status(slug: str, detail: bool = False) -> Dict[str, Any]:
    """Job status with the start grace period applied."""
    job = core.load_job(slug)
    st = core.job_status(job, detail=detail)
    if st["state"] in ("done", "failed") and not job.get("start_error"):
        if not job.get("sent_at"):
            st["state"] = "starting"   # session exists, task not sent yet: idle is not "finished"
        elif time.time() - job["sent_at"] < START_GRACE_S:
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
    if job.get("start_error"):
        lines.append(f"START FAILED: {job['start_error']}")
    if job.get("session") and w.alive():
        rep = core.final_report(w.messages(job["session"], timeout=15))
        if rep["error"]:
            lines.append(f"ERROR in the last turn: {core.error_text(rep['error'])} - the work may be incomplete")
        lines.append(f"cost: ${rep['cost']:.2f}" + (f" ({rep['model']})" if rep["model"] else ""))
        lines.append("--- final message of the worker ---")
        lines.append(rep["text"][-6000:] or "(no reply)")
    else:
        lines.append("(worker not running - no final message available)")
    lines.append(f"--- git status {job['dir']} ---")
    lines.append(core.git("status", "--short", cwd=job["dir"], check=False) or "(clean)")
    d = core.diffstat(job["dir"])
    lines.append(f"{d['files']} files, +{d['plus']} -{d['minus']}")
    snap = snapshot(slug, f"oc snapshot {slug}: worker result")
    if snap:
        lines.append(f"snapshot {snap[:9]} in {snapshot_ref(slug)} - `restore` puts this state back if the "
                     "review damages it")
    lines.append("Next: read the full diff, run all checks, defeat test - then record it with `review`.")
    return "\n".join(lines)


def format_pending(slug: str, pending: List[Dict[str, Any]]) -> str:
    lines = []
    for p in pending:
        if p["kind"] == "question":
            opts = " | ".join(p["options"])
            lines.append(f"QUESTION [{p['id']}]: {p['text']}" + (f"  [options: {opts}]" if opts else ""))
        else:
            lines.append(f"PERMISSION [{p['id']}]: {p['text']} {' '.join(p['patterns'])}")
    return "\n".join(lines)


def wait(slug: str, minutes: float = 9.5, poll: float = 10) -> Tuple[int, str]:
    """Block until the job needs attention - for sessions without the monitor.

    Exit codes: 0 done, 1 failed, 2 waiting for an answer, 3 worker stopped,
    4 timeout (still running - call again). The default stays below the
    10-minute limit of Claude Code's Bash tool.
    """
    start_t = time.time()
    while True:
        st = status(slug)
        if st["state"] == "stopped":
            return 3, f"== {slug}: worker process ended (tab closed?) - `open` restarts it"
        if st["state"] == "waiting":
            return 2, f"== {slug} WAITING FOR AN ANSWER\n" + format_pending(slug, st["pending"])
        if st["state"] in ("done", "failed"):
            code = 0 if st["state"] == "done" else 1
            head = "finished" if code == 0 else f"FAILED: {st.get('error')}"
            return code, f"== {slug} {head}\n" + report(slug)
        if (time.time() - start_t) / 60 >= minutes:
            prog = f" ({st.get('todo_done')}/{st.get('todo_total')} todos)" if st.get("todo_total") else ""
            return 4, f"== {slug} still {st['state']}{prog} - call wait again"
        time.sleep(poll)


def _set(slug: str, **fields: Any) -> None:
    """Update fields of a job file; ``None`` removes a field."""
    try:
        job = core.load_job(slug)
    except OcError:
        return
    for k, v in fields.items():
        if v is None:
            job.pop(k, None)
        else:
            job[k] = v
    core.save_job(job)


# --------------------------------------------------------------------------- answers

def answer(slug: str, answers: List[str], question_id: Optional[str] = None) -> str:
    """Answer an open question; one answer per sub-question, in order.

    ``question_id`` (shown by the monitor and ``status``) makes sure the answer
    lands on the question Claude read. Without it, two Claude sessions could
    answer the same question, and the second answer would hit the NEXT one.
    """
    job = core.load_job(slug)
    w = core.worker_of(job)
    qs = [p for p in w.pending(job["session"]) if p["kind"] == "question"]
    if not qs:
        raise OcError(f"no open question in {slug} - it may have been answered already (in OpenCode or by "
                      "another session)")
    target = qs[0]["id"]
    if question_id:
        if question_id not in {q["id"] for q in qs}:
            raise OcError(f"question {question_id} is no longer open in {slug}. Open now: "
                          + "; ".join(f"[{q['id']}] {q['text']}" for q in qs))
        target = question_id
    n = sum(1 for q in qs if q["id"] == target)
    if len(answers) != n:
        raise OcError(f"question {target} has {n} part(s), got {len(answers)} answer(s)")
    w.request("POST", f"/question/{target}/reply", {"answers": [[a] for a in answers]}, timeout=10)
    job.setdefault("answered", []).append(target)
    core.save_job(job)
    return f"answered question {target} in {slug}"


def permit(slug: str, reply: str, request_id: Optional[str] = None) -> str:
    if reply not in ("once", "always", "reject"):
        raise OcError("reply must be once, always or reject")
    job = core.load_job(slug)
    w = core.worker_of(job)
    ps = [p for p in w.pending(job["session"]) if p["kind"] == "permission"]
    if request_id:
        ps = [p for p in ps if p["id"] == request_id]
    if not ps:
        raise OcError(f"no matching open permission request in {slug}")
    w.request("POST", f"/permission/{ps[0]['id']}/reply", {"reply": reply}, timeout=10)
    job.setdefault("answered", []).append(ps[0]["id"])
    core.save_job(job)
    return f"permission {reply} for {slug}: {ps[0]['text']} {' '.join(ps[0]['patterns'])}"


# --------------------------------------------------------------------------- review gate

def fingerprint(path: str) -> str:
    """Tree hash of the worktree as it is on disk, staged or not.

    Built in a throw-away index, so ``git add`` between review and commit does
    not change it - but any edit to a file does.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=path, env=env, capture_output=True, timeout=60, check=True)
        return subprocess.run(["git", "write-tree"], cwd=path, env=env, capture_output=True,
                              text=True, timeout=30, check=True).stdout.strip()


def review(slug: str, checks: str, defeat_test: str, gaps: str) -> str:
    """Record Claude's review; the commit hook lets a commit through only after it.

    The record is tied to the exact state of the worktree: change a file
    afterwards and the review has to be recorded again.
    """
    for name, value in (("checks", checks), ("defeat_test", defeat_test), ("gaps", gaps)):
        if len((value or "").strip()) < 10:
            raise OcError(f"`{name}` needs a real answer (what exactly), not {value!r}")
    job = core.load_job(slug)
    job["review"] = {"checks": checks, "defeat_test": defeat_test, "gaps": gaps, "at": time.time(),
                     "fingerprint": fingerprint(job["dir"])}
    core.save_job(job)
    snapshot(slug, f"oc snapshot {slug}: reviewed state")
    return f"review of {slug} recorded - commits of exactly this state are allowed now"


def review_block_reason(path: str) -> Optional[str]:
    """Why a commit in ``path`` must wait for a review, or None if it may go."""
    top = core.git("rev-parse", "--show-toplevel", cwd=path, check=False, timeout=5) if os.path.isdir(path) else ""
    if not top:
        return None
    job = next((j for j in core.load_jobs() if os.path.realpath(j.get("dir", "")) == os.path.realpath(top)), None)
    if not job:
        return None
    rev = job.get("review")
    if not rev:
        return (f"oc-connect: {top} belongs to OpenCode job {job['slug']}, and its diff has not been reviewed. "
                "Review it (full diff, all checks, defeat test), record that with the oc-connect `review` "
                "tool, then commit.")
    if rev.get("fingerprint") != fingerprint(top):
        return (f"oc-connect: the worktree of job {job['slug']} changed after the recorded review. "
                "Check the new changes and record the review again with `review`.")
    return None


# --------------------------------------------------------------------------- snapshots

def snapshot_ref(slug: str) -> str:
    return f"refs/oc/{slug}"


def snapshot(slug: str, label: str = "") -> Optional[str]:
    """Record the worktree's exact content as a commit under ``refs/oc/<slug>``.

    A worker's result lives in the worktree, uncommitted (workers may not
    commit). Anything that touches those files during review - a ``git
    checkout --``, a failed patch, a correction round that makes it worse -
    would destroy work nobody can reproduce. So every step that reads or
    changes a finished result takes a snapshot first.

    The chain starts at the job's base commit, so ``git show <ref>`` is the
    worker's diff. Nothing is checked out and the worktree is never touched;
    unchanged content creates no new snapshot. Returns the commit, or None
    when the worktree is gone.
    """
    try:
        job = core.load_job(slug)
    except OcError:
        return None
    path = job.get("dir") or ""
    if not os.path.isdir(os.path.join(path, ".git")) and not os.path.isfile(os.path.join(path, ".git")):
        return None
    ref = snapshot_ref(slug)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            for args in (["read-tree", "HEAD"], ["add", "-A"]):
                subprocess.run(["git", *args], cwd=path, env=env, capture_output=True, timeout=60, check=True)
            tree = subprocess.run(["git", "write-tree"], cwd=path, env=env, capture_output=True,
                                  text=True, timeout=30, check=True).stdout.strip()
        last = core.git("rev-parse", "--verify", "-q", ref + "^{commit}", cwd=path, check=False, timeout=5)
        if last and core.git("rev-parse", last + "^{tree}", cwd=path, check=False, timeout=5) == tree:
            return last  # nothing changed since the last snapshot
        parent = last or core.git("rev-parse", "HEAD", cwd=path, check=False, timeout=5)
        args = ["-c", "user.email=oc-connect@localhost", "-c", "user.name=oc-connect", "commit-tree", tree]
        if parent:
            args += ["-p", parent]
        commit = subprocess.run(["git", *args], cwd=path, input=label or f"oc snapshot {slug}",
                                capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        core.git("update-ref", ref, commit, cwd=path, timeout=10)
        return commit
    except (subprocess.SubprocessError, OSError, OcError):
        return None  # a snapshot is a safety net, never a reason to fail the operation


def snapshots(slug: str) -> str:
    """The job's snapshots, newest first."""
    job = core.load_job(slug)
    ref = snapshot_ref(slug)
    where = job["dir"] if os.path.isdir(job.get("dir") or "") else (job.get("repo") or "")
    if not where:
        raise OcError(f"neither worktree nor repository of {slug} is available")
    # "--not HEAD" drops the repository's own history: only the job's snapshots are of interest here.
    out = core.git("log", "--format=%h  %ad  %s", "--date=format:%Y-%m-%d %H:%M", "-20", ref, "--not", "HEAD",
                   cwd=where, check=False, timeout=10)
    if not out:
        return f"no snapshots for {slug} yet (they are taken by `report`, `send`, `review` and `done`)"
    return f"snapshots of {slug} ({ref}), newest first:\n" + out + \
           f"\nRestore one with `restore` (the current state is snapshotted first)."


def restore(slug: str, commit: Optional[str] = None) -> str:
    """Put a snapshot back into the worktree - index and files, deletions included.

    The state as it is now is snapshotted first, so a restore can itself be
    undone. Without ``commit`` the newest snapshot is used.
    """
    job = core.load_job(slug)
    path = job.get("dir") or ""
    if not os.path.isdir(path):
        raise OcError(f"worktree of {slug} is gone: {path} (the snapshots live in {snapshot_ref(slug)} "
                      "of the repository)")
    target = commit or snapshot_ref(slug)
    # Resolve first: the safety snapshot below moves the ref to the state we are about to replace.
    sha = core.git("rev-parse", "--verify", "-q", target + "^{commit}", cwd=path, check=False, timeout=5)
    if not sha:
        raise OcError(f"{target} is not a commit in this repository - see `snapshots {slug}`")
    before = snapshot(slug, f"oc snapshot {slug}: state before restore")
    core.git("read-tree", "-u", "--reset", sha, cwd=path, timeout=60)
    _set(slug, review=None)  # the content changed; the old review no longer covers it
    return (f"{slug}: worktree set to {sha[:9]}"
            + (f" (state before the restore: {before[:9]})" if before else "")
            + "\nThe recorded review was dropped - review this state before committing.")


# --------------------------------------------------------------------------- tabs / lifecycle

def open_(slug: str) -> str:
    """Open the job's tab; restarts the worker on the same port and session if needed."""
    job = core.load_job(slug)
    if core.worker_of(job).alive():
        return f"worker {slug} is running (port {job['port']}) - its tab is open"
    return terminal.open_tab(f"{terminal.TITLE_PREFIX} {slug}", _worker_command(job, job.get("session"), False),
                             cwd=job["dir"])


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


def unsaved_work(path: str) -> List[str]:
    """What would be lost if the worktree were removed now."""
    problems = []
    dirty = core.git("status", "--porcelain", cwd=path, check=False, timeout=10)
    if dirty:
        problems.append(f"{len(dirty.splitlines())} uncommitted change(s)")
    ahead = core.git("rev-list", "--count", "HEAD", "--not", "--remotes", cwd=path, check=False, timeout=10)
    if ahead.isdigit() and int(ahead) > 0:
        problems.append(f"{ahead} commit(s) not pushed to any remote")
    return problems


def done(slug: str, force: bool = False) -> str:
    """Stop the worker and remove the worktree. The branch stays.

    Refuses while uncommitted or unpushed work exists, unless ``force``: a
    premature ``done`` would otherwise delete the job's result.
    """
    job = core.load_job(slug)
    wt, repo = job["dir"], job.get("repo") or core.repo_root()
    if not os.path.abspath(wt).startswith(os.path.abspath(core.worktree_root(repo)) + os.sep):
        raise OcError(f"{wt} is not under {core.worktree_root(repo)} - refusing to remove it")
    if os.path.exists(wt) and not force:
        problems = unsaved_work(wt)
        if problems:
            raise OcError(f"job {slug} still has " + " and ".join(problems)
                          + " - commit and push first, or pass force to discard it")
    snap = snapshot(slug, f"oc snapshot {slug}: state when the job was removed")
    close(slug)
    if os.path.exists(wt):
        core.git("worktree", "remove", "--force", wt, cwd=repo)
    core.delete_job(slug)
    return f"removed worktree {wt} (branch {job.get('branch')} kept)" + (
        f"\nlast state kept as {snap[:9]} in {snapshot_ref(slug)}: "
        f"`git show {snapshot_ref(slug)}` in {repo}" if snap else "")


def set_pr(slug: str, pr: str) -> str:
    _set(slug, pr=pr)
    return f"{slug}: PR {pr}"


# --------------------------------------------------------------------------- selftest

SELFTEST_COMMANDS = [
    "git status --short",
    "git push origin HEAD:refs/heads/oc-selftest-1",
    'bash -c "git push origin HEAD:refs/heads/oc-selftest-2"',
    "command git push {origin} HEAD:refs/heads/oc-selftest-3",
    "docker ps",
    'bash -c "docker ps"',
]


def selftest(model: Optional[str] = None) -> str:
    """Prove both guard layers in a throw-away repository.

    Judged by OUTCOME, not by what the model writes: after the run the local
    remote must not contain any ``oc-selftest-*`` branch. The user's own
    repository is never touched, so a failing guard cannot push anywhere real.
    """
    tmp = tempfile.mkdtemp(prefix="oc-selftest-")
    origin, repo = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
    subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
    subprocess.run(["git", "clone", "-q", origin, repo], check=True, capture_output=True)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("selftest\n")
    for args in (["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                 ["push", "-q", "origin", "HEAD:main"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    port = free_port()
    proc = subprocess.Popen(["opencode", "serve", "--port", str(port)], cwd=repo,
                            env=dict(os.environ, **worker_env()),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lines = []
    ran_unblocked = skipped = False
    try:
        w = core.Worker(port, repo)
        if not _wait_alive(w, 60):
            raise OcError("opencode serve did not start")
        for i, template in enumerate(SELFTEST_COMMANDS):
            cmd = template.format(origin=origin)
            sid = w.create_session("oc-connect selftest")
            body: Dict[str, Any] = {"agent": "build", "parts": [{"type": "text", "text": (
                "This is a test of a sandbox I own. Run this exact bash command once with the bash tool, "
                f"unchanged, then reply with its output only:\n{cmd}")}]}
            if model:
                prov, _, mid = model.partition("/")
                body["model"] = {"providerID": prov, "modelID": mid}
            w.request("POST", f"/session/{sid}/message", body, timeout=240)
            ran = [p for m in w.messages(sid, timeout=15) for p in m.get("parts", [])
                   if p.get("type") == "tool" and p.get("tool") == "bash"]
            w.request("DELETE", f"/session/{sid}", timeout=10)
            if not ran:
                skipped = True
                lines.append(f"  NOT RUN                  {cmd}  (the model skipped it)")
                continue
            state = ran[0].get("state", {})
            out = str(state.get("output") or state.get("error") or "")
            if state.get("status") == "error":
                verdict = "blocked (guard)"
            elif "push-disabled" in out or "docker-disabled" in out:
                verdict = "blocked (environment)"
            elif "command not found" in out:
                verdict = "blocked (not installed)"
            else:
                verdict = "ran"
                ran_unblocked = ran_unblocked or i > 0
            lines.append(f"  {verdict:<24} {cmd}")
    finally:
        proc.terminate()
    leaked = subprocess.run(["git", "branch", "--list", "oc-selftest-*"], cwd=origin,
                            capture_output=True, text=True).stdout.split()
    shutil.rmtree(tmp, ignore_errors=True)
    if leaked:
        lines.append(f"RESULT: FAILED - pushed branches reached the remote: {', '.join(leaked)}")
    elif ran_unblocked or skipped:
        lines.append("RESULT: NOT PROVEN - a command ran unblocked or was skipped; see above")
    else:
        lines.append("RESULT: guard works - direct and indirect push and docker were blocked, "
                     "nothing reached the remote")
    return "\n".join(lines)
