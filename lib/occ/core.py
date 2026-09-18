"""Core of oc-connect: job state, repository helpers and the OpenCode HTTP client.

A *job* is one delegated task: one git worktree, one OpenCode process on its own
port, one OpenCode session, one terminal tab. Jobs are stored as JSON files in
``$OC_STATE/jobs`` (default ``~/.local/state/oc-connect``) so that the CLI, the
MCP server, the monitor and both status lines all see the same picture.

Everything here must work on the Python 3.9 that ships with macOS and must never
hang: status lines and the monitor call into this module every few seconds.
Network calls therefore always carry short timeouts and return ``None`` instead
of raising.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

VERSION = "0.3.0"

STATE_DIR = os.environ.get("OC_STATE") or os.path.expanduser("~/.local/state/oc-connect")
JOBS_DIR = os.path.join(STATE_DIR, "jobs")
LEGACY_STATE = os.path.expanduser("~/.local/state/oc-auftrag")
OPENCODE_CONFIG_DIR = os.path.expanduser("~/.config/opencode")
PORT_RANGE = range(4101, 4200)


class OcError(Exception):
    """A user-facing failure; the message is printed as-is."""


# --------------------------------------------------------------------------- state

def _ensure_dirs() -> None:
    os.makedirs(JOBS_DIR, exist_ok=True)


def migrate_legacy() -> List[str]:
    """Import jobs written by the old bash version (``key=value`` files).

    The old files stay untouched: a Claude session still running the old
    script keeps reading them. Jobs whose worktree is gone are dropped from the
    new state by ``load_jobs``.
    """
    moved = []
    if not os.path.isdir(LEGACY_STATE):
        return moved
    _ensure_dirs()
    for name in sorted(os.listdir(LEGACY_STATE)):
        if not name.endswith(".env"):
            continue
        path = os.path.join(LEGACY_STATE, name)
        data: Dict[str, Any] = {}
        with open(path) as fh:
            for line in fh:
                key, _, value = line.rstrip("\n").partition("=")
                if key:
                    data[key] = value
        if not data.get("slug") or os.path.exists(job_path(data["slug"])):
            continue
        if data.get("port"):
            data["port"] = int(data["port"])
        data.setdefault("created", os.path.getmtime(path))
        save_job(data)
        moved.append(data["slug"])
    return moved


def job_path(slug: str) -> str:
    return os.path.join(JOBS_DIR, slug + ".json")


def save_job(job: Dict[str, Any]) -> None:
    """Write atomically: status lines read these files while we write them."""
    _ensure_dirs()
    tmp = job_path(job["slug"]) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(job, fh, indent=2)
    os.replace(tmp, job_path(job["slug"]))


def load_job(slug: str) -> Dict[str, Any]:
    try:
        with open(job_path(slug)) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise OcError(f"unknown job: {slug} (see `oc status`)")


def load_jobs() -> List[Dict[str, Any]]:
    """All jobs, oldest first. Unreadable files are skipped, never fatal."""
    if not os.path.isdir(JOBS_DIR):
        return []
    jobs = []
    for name in os.listdir(JOBS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(JOBS_DIR, name)) as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            continue
        # Worktree removed by hand or by the old script: the job is over.
        if job.get("dir") and not os.path.isdir(job["dir"]):
            delete_job(job.get("slug", name[:-5]))
            continue
        jobs.append(job)
    return sorted(jobs, key=lambda j: j.get("created", 0))


def delete_job(slug: str) -> None:
    try:
        os.remove(job_path(slug))
    except FileNotFoundError:
        pass


def valid_slug(slug: str) -> str:
    """Slugs end up in paths, branch names, tab titles and shell commands."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", slug or ""):
        raise OcError(f"invalid slug {slug!r}: use letters, digits, '.', '_' or '-' (max 40)")
    return slug


# --------------------------------------------------------------------------- git

def git(*args: str, cwd: Optional[str] = None, check: bool = True, timeout: float = 120) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise OcError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def repo_root(cwd: Optional[str] = None) -> str:
    """Main worktree of the repository we stand in.

    Deliberately NOT ``--show-toplevel``: from inside a job's worktree that would
    return the worktree, and the next job's worktree would nest inside it.
    """
    if os.environ.get("OC_REPO"):
        return os.path.abspath(os.environ["OC_REPO"])
    try:
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd, timeout=5)
    except (OcError, FileNotFoundError):
        raise OcError("not inside a git repository (or set OC_REPO)")
    return os.path.dirname(common)


def worktree_root(repo: str) -> str:
    return os.environ.get("OC_WT_ROOT") or repo + "-worktrees"


def base_ref(repo: str) -> str:
    """Default branch of the remote, so every job starts from the shared state."""
    if os.environ.get("OC_BASE_REF"):
        return os.environ["OC_BASE_REF"]
    ref = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo, check=False, timeout=5)
    if ref:
        return ref
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo, timeout=5)


def env_files(repo: str) -> List[str]:
    """Untracked, git-ignored ``.env*`` files a worktree needs to run.

    Found via git itself, so it adapts to any layout instead of assuming
    ``backend/.env``. ``OC_COPY`` overrides the list (space separated).
    Templates such as ``.env.example`` are tracked and therefore not listed.
    """
    if os.environ.get("OC_COPY") is not None:
        return [p for p in os.environ["OC_COPY"].split() if os.path.isfile(os.path.join(repo, p))]
    out = git("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", cwd=repo, check=False, timeout=10)
    found = []
    for rel in out.splitlines():
        base = os.path.basename(rel.rstrip("/"))
        if rel.endswith("/") or rel.count("/") > 2 or "node_modules" in rel:
            continue
        if base == ".env" or re.fullmatch(r"\.env\.(local|development|test)(\.local)?", base):
            found.append(rel)
    return found


def diffstat(path: str) -> Dict[str, int]:
    """Changed files and lines in a worktree, untracked files included."""
    stat = {"files": 0, "plus": 0, "minus": 0}
    try:
        out = git("diff", "--numstat", "HEAD", cwd=path, check=False, timeout=2)
        new = git("ls-files", "--others", "--exclude-standard", cwd=path, check=False, timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        return stat
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            stat["files"] += 1
            stat["plus"] += int(parts[0]) if parts[0].isdigit() else 0
            stat["minus"] += int(parts[1]) if parts[1].isdigit() else 0
    for rel in new.splitlines():
        stat["files"] += 1
        try:
            with open(os.path.join(path, rel), "rb") as fh:
                stat["plus"] += fh.read().count(b"\n")
        except OSError:
            pass
    return stat


# --------------------------------------------------------------------------- OpenCode HTTP

class Worker:
    """Thin client for one OpenCode server process (one job).

    Every call is scoped with ``?directory=``: OpenCode answers session status,
    questions and permissions only per project directory.
    """

    def __init__(self, port: int, directory: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.directory = directory

    def _url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self.base}{path}{sep}directory={urllib.parse.quote(self.directory)}"

    def request(self, method: str, path: str, body: Any = None, timeout: float = 3) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self._url(path), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            return json.loads(raw) if raw else None

    def get(self, path: str, timeout: float = 3) -> Any:
        """GET that never raises; ``None`` on any failure."""
        try:
            return self.request("GET", path, timeout=timeout)
        except Exception:
            return None

    def alive(self, timeout: float = 1) -> bool:
        try:
            urllib.request.urlopen(self.base + "/doc", timeout=timeout).close()
            return True
        except Exception:
            return False

    def create_session(self, title: str) -> str:
        return self.request("POST", "/session", {"title": title}, timeout=10)["id"]

    def prompt(self, session: str, text: str, agent: Optional[str], model: Optional[str] = None) -> None:
        body: Dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent
        if model:  # "provider/model"; overrides the agent's own model
            prov, _, mid = model.partition("/")
            body["model"] = {"providerID": prov, "modelID": mid}
        req = urllib.request.Request(self._url(f"/session/{session}/prompt_async"),
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                if res.status not in (200, 204):
                    raise OcError(f"prompt_async returned HTTP {res.status}")
        except urllib.error.HTTPError as exc:
            raise OcError(f"prompt_async returned HTTP {exc.code}: {exc.read()[:300]!r}")

    def select_session(self, session: str) -> None:
        """Switch this process's TUI to the session (affects only this tab)."""
        try:
            req = urllib.request.Request(self.base + "/tui/select-session",
                                         data=json.dumps({"sessionID": session}).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).close()
        except Exception:
            pass

    def busy(self, session: str) -> Optional[bool]:
        st = self.get("/session/status")
        if st is None:
            return None
        entry = st.get(session)
        return bool(entry) and (entry.get("type") != "idle" if isinstance(entry, dict) else True)

    def pending(self, session: str) -> List[Dict[str, Any]]:
        """Open questions and permission requests of one session.

        Both keep the session ``busy`` although it waits for us; without this a
        waiter would run into its timeout.
        """
        out = []
        for item in self.get("/question") or []:
            if item.get("sessionID") != session:
                continue
            for q in item.get("questions", []):
                out.append({"kind": "question", "id": item["id"], "text": q.get("question", ""),
                            "header": q.get("header", ""),
                            "options": [o.get("label", "") for o in q.get("options", [])],
                            "multiple": bool(q.get("multiple"))})
        for item in self.get("/permission") or []:
            if item.get("sessionID") == session:
                out.append({"kind": "permission", "id": item["id"], "text": item.get("permission", ""),
                            "patterns": item.get("patterns", [])})
        return out

    def messages(self, session: str, timeout: float = 5) -> List[Dict[str, Any]]:
        return self.get(f"/session/{session}/message", timeout=timeout) or []

    def todos(self, session: str) -> List[Dict[str, Any]]:
        return self.get(f"/session/{session}/todo") or []

    def children(self, session: str) -> List[Dict[str, Any]]:
        return self.get(f"/session/{session}/children") or []


def worker_of(job: Dict[str, Any]) -> Worker:
    return Worker(int(job.get("port") or os.environ.get("OC_PORT", 4096)), job["dir"])


# --------------------------------------------------------------------------- job status

def last_activity(msgs: List[Dict[str, Any]], directory: str) -> str:
    """Most recent meaningful tool call, e.g. ``edit: src/app.js``."""
    for msg in reversed(msgs):
        if msg.get("info", {}).get("role") != "assistant":
            continue
        for part in reversed(msg.get("parts", [])):
            if part.get("type") != "tool":
                continue
            inp = part.get("state", {}).get("input", {}) or {}
            target = inp.get("command") or inp.get("filePath") or inp.get("description") or inp.get("pattern") or ""
            if target:
                return f"{part.get('tool')}: {str(target).replace(directory + '/', '')}"[:90]
    return ""


def final_report(msgs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Text, error and cost of the last assistant turn, cost summed over the session."""
    assistant = [m for m in msgs if m.get("info", {}).get("role") == "assistant"]
    cost = sum(float(m["info"].get("cost") or 0) for m in assistant)
    if not assistant:
        return {"text": "", "error": None, "cost": cost, "model": None}
    last = assistant[-1]
    text = "\n".join(p.get("text", "") for p in last.get("parts", []) if p.get("type") == "text")
    info = last["info"]
    model = info.get("modelID") or (info.get("model") or {}).get("modelID")
    return {"text": text, "error": info.get("error"), "cost": cost, "model": model}


def job_status(job: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
    """Current state of a job.

    ``state`` is one of:
      * ``starting`` - no session yet
      * ``running``  - the worker is busy
      * ``waiting``  - a question or permission request needs an answer
      * ``done``     - the session is idle after having worked
      * ``stopped``  - the worker process is gone (tab closed)
    """
    w = worker_of(job)
    st: Dict[str, Any] = {"slug": job["slug"], "title": job.get("title", ""), "port": job.get("port"),
                          "agent": job.get("agent"), "dir": job.get("dir"), "branch": job.get("branch"),
                          "pr": job.get("pr"), "pending": [], "created": job.get("created")}
    if not w.alive(timeout=0.6):
        st["state"] = "stopped"
        return st
    sid = job.get("session")
    if not sid:
        st["state"] = "starting"
        return st
    st["pending"] = w.pending(sid)
    busy = w.busy(sid)
    if st["pending"]:
        st["state"] = "waiting"
    elif busy:
        st["state"] = "running"
    else:
        st["state"] = "done"
    todos = w.todos(sid)
    st["todo_done"] = sum(1 for t in todos if t.get("status") == "completed")
    st["todo_total"] = len(todos)
    if detail:
        msgs = w.messages(sid)
        st["activity"] = last_activity(msgs, job["dir"])
        st.update({k: v for k, v in final_report(msgs).items() if k in ("cost", "model")})
        st["diff"] = diffstat(job["dir"])
        times = [m.get("info", {}).get("time", {}) for m in msgs]
        stamps = [t.get("completed") or t.get("created") for t in times if t]
        st["last_ms"] = max([s for s in stamps if s] or [0])
    return st


def minutes_since(ts: Optional[float]) -> Optional[float]:
    """Minutes since a timestamp in seconds or milliseconds."""
    if not ts:
        return None
    if ts > 1e11:
        ts = ts / 1000
    return (time.time() - ts) / 60


# --------------------------------------------------------------------------- OpenCode setup

def _load_jsonc(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return None
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    try:
        return json.loads(text)
    except ValueError:
        return None


def opencode_setup() -> Dict[str, Any]:
    """What the local OpenCode installation offers: plugins, agents, skills.

    oh-my-opencode(-slim) is optional. Without it, jobs go to OpenCode's
    built-in ``build`` agent.
    """
    cfg = _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.jsonc")) \
        or _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.json")) or {}
    plugins = [p if isinstance(p, str) else str(p) for p in cfg.get("plugin", [])]
    omo = None
    for name in ("oh-my-opencode-slim.json", "oh-my-opencode-slim.jsonc", "oh-my-opencode.json", "oh-my-opencode.jsonc"):
        omo = _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, name))
        if omo is not None:
            break
    agents: Dict[str, str] = {}
    if omo:
        preset = omo.get("preset")
        for name, val in ((omo.get("presets") or {}).get(preset) or omo.get("agents") or {}).items():
            val = val or {}
            agents[name] = val.get("model", "?") if isinstance(val, dict) else str(val)
    for name, val in (cfg.get("agent") or {}).items():
        if isinstance(val, dict) and not val.get("disable"):
            agents.setdefault(name, val.get("model", "default"))
    omo_installed = any("oh-my-opencode" in p for p in plugins)
    return {
        "opencode": _which("opencode"),
        "version": _opencode_version(),
        "plugins": plugins,
        "oh_my_opencode": omo_installed,
        "superpowers": any("superpowers" in p for p in plugins),
        "agents": agents,
        "default_agent": default_agent(omo_installed),
    }


def default_agent(omo_installed: Optional[bool] = None) -> str:
    if os.environ.get("OC_AGENT"):
        return os.environ["OC_AGENT"]
    if omo_installed is None:
        omo_installed = any("oh-my-opencode" in p for p in opencode_setup_plugins())
    return "orchestrator" if omo_installed else "build"


def opencode_setup_plugins() -> List[str]:
    cfg = _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.jsonc")) \
        or _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.json")) or {}
    return [p if isinstance(p, str) else str(p) for p in cfg.get("plugin", [])]


def _which(cmd: str) -> Optional[str]:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, cmd)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _opencode_version() -> Optional[str]:
    try:
        return subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
