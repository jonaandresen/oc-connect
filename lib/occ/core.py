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

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

VERSION = "2.0.0"  # keep equal to .claude-plugin/plugin.json (a test checks it)

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
                data = fh.read()
            stat["plus"] += data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        except OSError:
            pass
    return stat


# --------------------------------------------------------------------------- OpenCode HTTP

def model_ref(model: str) -> Dict[str, str]:
    """``provider/model`` -> OpenCode 2's model reference."""
    prov, _, mid = model.partition("/")
    return {"providerID": prov, "id": mid}


class Worker:
    """Thin client for one OpenCode 2 server process (one job).

    OpenCode 2 serves its API under ``/api`` behind HTTP basic auth: user
    ``opencode``, password from the server's ``OPENCODE_SERVER_PASSWORD``. Every
    job runs its own server in its worktree, so all routes are already scoped
    to the job's directory.
    """

    def __init__(self, port: int, directory: str, password: str = "") -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.directory = directory
        self.auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()

    def request(self, method: str, path: str, body: Any = None, timeout: float = 3) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            body = exc.read()
            exc.close()
            raise OcError(f"{method} {path} returned HTTP {exc.code}: {body[:300]!r}") from None

    def get(self, path: str, timeout: float = 3) -> Any:
        """GET that never raises; ``None`` on any failure."""
        try:
            return self.request("GET", path, timeout=timeout)
        except Exception:
            return None

    def data(self, path: str, timeout: float = 3) -> Any:
        """The ``data`` member of a GET answer, or None."""
        res = self.get(path, timeout=timeout)
        return res.get("data") if isinstance(res, dict) else None

    def alive(self, timeout: float = 1) -> bool:
        try:
            self.request("GET", "/api/info", timeout=timeout)
            return True
        except Exception:
            return False

    def _post(self, path: str, body: Any, timeout: float = 10) -> Any:
        return self.request("POST", path, body, timeout=timeout)

    def agents(self) -> List[str]:
        return [a.get("id") for a in (self.data("/api/agent", timeout=10) or []) if a.get("id")]

    def create_session(self, title: str, agent: Optional[str] = None, model: Optional[str] = None,
                       permissions: Optional[List[Dict[str, str]]] = None) -> str:
        body: Dict[str, Any] = {"title": title}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model_ref(model)
        if permissions:
            body["permissions"] = permissions
        return self._post("/api/session", body)["data"]["id"]

    def prompt(self, session: str, text: str, agent: Optional[str] = None, model: Optional[str] = None) -> None:
        """Queue a message; OpenCode 2 answers at once and works in the background."""
        if agent:
            self._post(f"/api/session/{session}/agent", {"agent": agent})
        if model:  # "provider/model"; overrides the agent's model
            self._post(f"/api/session/{session}/model", {"model": model_ref(model)})
        self._post(f"/api/session/{session}/prompt", {"text": text})

    def busy(self, session: str) -> Optional[bool]:
        active = self.data("/api/session/active")
        return None if active is None else session in active

    def pending(self, session: str) -> List[Dict[str, Any]]:
        """Open questions (forms) and permission requests of one session.

        Both keep the session active although it waits for us; without this a
        waiter would run into its timeout. One question per form field: the
        question tool turns each question into a field ``q0``, ``q1``, ...
        """
        out = []
        for form in self.data(f"/api/session/{session}/form") or []:
            for field in form.get("fields") or []:
                out.append({"kind": "question", "id": form["id"], "key": field.get("key"),
                            "text": field.get("description") or field.get("title", ""),
                            "header": field.get("title", ""),
                            "options": [o.get("label", "") for o in field.get("options") or []],
                            "multiple": field.get("type") == "multiselect"})
        for req in self.data(f"/api/session/{session}/permission") or []:
            # OpenCode 2 splits a compound command into its parts (measured: `a && b` -> ["a", "b"]).
            res = req.get("resources") or []
            out.append({"kind": "permission", "id": req["id"], "patterns": res,
                        "text": f"{req.get('action', '')}: {' | '.join(res)}" if res else req.get("action", "")})
        return out

    def messages(self, session: str, limit: int = 200, timeout: float = 5) -> List[Dict[str, Any]]:
        """The newest ``limit`` messages, oldest first (the API sends newest first)."""
        return list(reversed(self.data(f"/api/session/{session}/message?limit={limit}", timeout=timeout) or []))

    def info(self, session: str) -> Dict[str, Any]:
        """Session header: cost, model, outcome and ``time.updated`` - cheap."""
        return self.data(f"/api/session/{session}") or {}


def worker_of(job: Dict[str, Any]) -> Worker:
    return Worker(int(job.get("port") or 4096), job["dir"], job.get("password") or "")


# --------------------------------------------------------------------------- processes

def pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def claude_pid() -> Optional[int]:
    """PID of the Claude Code process this process runs under, or None.

    Used as the OWNER of a job: the MCP server, the monitor and Bash commands
    of one Claude session all share this ancestor. Two sessions therefore do
    not both react to the same worker question.
    """
    pid = os.getpid()
    for _ in range(10):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=1).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        if not out:
            return None
        ppid_s, _, args = out.partition(" ")
        prog = os.path.basename(args.split(" ", 1)[0]) if args else ""
        if prog == "claude" or "/claude-code/" in args or args.startswith("claude "):
            return pid
        try:
            pid = int(ppid_s)
        except ValueError:
            return None
        if pid <= 1:
            return None
    return None


# --------------------------------------------------------------------------- job status

CACHE_DIR = os.path.join(STATE_DIR, "cache")


def _tools(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in msg.get("content") or [] if c.get("type") == "tool"]


def last_activity(msgs: List[Dict[str, Any]], directory: str) -> str:
    """Most recent meaningful tool call, e.g. ``edit: src/app.js``."""
    for msg in reversed(msgs):
        if msg.get("type") != "assistant":
            continue
        for tool in reversed(_tools(msg)):
            inp = (tool.get("state") or {}).get("input") or {}
            target = inp.get("command") or inp.get("path") or inp.get("filePath") or inp.get("pattern") \
                or inp.get("description") or ""
            if target:
                return f"{tool.get('name')}: {str(target).replace(directory + '/', '')}"[:90]
    return ""


def final_report(msgs: List[Dict[str, Any]], info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Text and error of the last turn; cost and model from the session header.

    A turn ends with an ``idle`` message whose ``outcome`` is succeeded, failed
    or interrupted (measured on 2.1.4). The error sits on that message and on
    the assistant message that failed.
    """
    info = info or {}
    cost = float(info.get("cost") or 0)
    model = (info.get("model") or {}).get("id")
    assistant = [m for m in msgs if m.get("type") == "assistant"]
    idle = [m for m in msgs if m.get("type") == "idle"]
    text = ""
    if assistant:
        text = "\n".join(c.get("text", "") for c in assistant[-1].get("content") or [] if c.get("type") == "text")
        model = model or (assistant[-1].get("model") or {}).get("id")
    error = None
    last_idle = idle[-1] if idle else None
    if last_idle and (not assistant or last_idle["time"]["created"] >= assistant[-1]["time"]["created"]):
        if last_idle.get("outcome") == "failed":
            error = last_idle.get("error") or (assistant[-1].get("error") if assistant else None) or "failed"
        elif last_idle.get("outcome") == "interrupted":
            error = {"type": "interrupted", "message": "the turn was interrupted"}
    return {"text": text, "error": error, "cost": cost, "model": model}


def error_text(err: Any) -> str:
    """Short, readable form of an OpenCode error (``{type, message}``)."""
    if not err:
        return ""
    if isinstance(err, dict):
        msg = err.get("message")
        return f"{err.get('type', 'error')}: {msg}"[:300] if msg else str(err.get("type", "error"))
    return str(err)[:200]


def session_summary(w: Worker, sid: str, directory: str) -> Dict[str, Any]:
    """Cost, model, last error and activity of a session - cached.

    The message list of a long job is large, and status lines ask every few
    seconds. The session header is cheap and carries ``time.updated``; the
    messages are only fetched again when that changed.
    """
    info = w.info(sid)
    updated = (info.get("time") or {}).get("updated")
    path = os.path.join(CACHE_DIR, sid + ".json")
    if updated:
        try:
            with open(path) as fh:
                cached = json.load(fh)
            if cached.get("updated") == updated:
                return cached
        except (OSError, ValueError):
            pass
    msgs = w.messages(sid, timeout=10)
    rep = final_report(msgs, info)
    stamps = [(m.get("time") or {}) for m in msgs]
    last_ms = max([t.get("completed") or t.get("created") or 0 for t in stamps] or [0])
    summary = {"updated": updated or last_ms, "cost": rep["cost"], "model": rep["model"],
               "error": error_text(rep["error"]), "activity": last_activity(msgs, directory),
               "text_tail": rep["text"][-1500:], "messages": len(msgs)}
    if updated:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path + ".tmp", "w") as fh:
                json.dump(summary, fh)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
    return summary


def job_status(job: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
    """Current state of a job.

    ``state`` is one of:
      * ``starting`` - worker or session not up yet (dependencies may install)
      * ``running``  - the worker is busy
      * ``waiting``  - a question or permission request needs an answer
      * ``done``     - the session is idle after having worked
      * ``failed``   - idle, but the last turn ended with an error (rate limit,
                       provider down, aborted) or the start failed
      * ``stopped``  - the worker process is gone (tab closed)
    """
    w = worker_of(job)
    owner = job.get("owner")
    st: Dict[str, Any] = {"slug": job["slug"], "title": job.get("title", ""), "port": job.get("port"),
                          "agent": job.get("agent"), "dir": job.get("dir"), "branch": job.get("branch"),
                          "pr": job.get("pr"), "pending": [], "created": job.get("created"),
                          "owner": owner, "owner_alive": pid_alive(owner) if owner else False,
                          "reviewed": bool(job.get("review")), "closed": job.get("closed_reason")}
    if job.get("start_error"):
        st["state"], st["error"] = "failed", job["start_error"]
        return st
    if not w.alive(timeout=0.6):
        # Before the task went out the tab may still install dependencies.
        st["state"] = "starting" if not job.get("sent_at") and time.time() - job.get("created", 0) < 1800 \
            else "stopped"
        return st
    sid = job.get("session")
    if not sid:
        st["state"] = "starting"
        return st
    st["pending"] = w.pending(sid)
    busy = w.busy(sid)
    idle = not st["pending"] and not busy
    if busy and not detail:
        # Cheap: only the session header, for stall detection and cost.
        info = w.info(sid)
        st["updated"] = (info.get("time") or {}).get("updated")
        st["cost"] = float(info.get("cost") or 0)
    if detail or idle:
        summ = session_summary(w, sid, job["dir"])
        st.update({"cost": summ["cost"], "model": summ["model"], "activity": summ["activity"],
                   "updated": summ["updated"], "error": summ["error"]})
    if st["pending"]:
        st["state"] = "waiting"
    elif busy:
        st["state"] = "running"
    else:
        st["state"] = "failed" if st.get("error") else "done"
    if detail:
        st["diff"] = diffstat(job["dir"])
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
    plugins = config_plugins(cfg)
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
    for name, val in (cfg.get("agents") or cfg.get("agent") or {}).items():
        if isinstance(val, dict) and not val.get("disable"):
            agents.setdefault(name, val.get("model", "default"))
    omo_installed = any("oh-my-opencode" in p for p in plugins)
    from . import profile  # late: profile imports core
    if profile.mode() == "isolated":
        agents = {name: model or "OpenCode default model" for name, model in profile.agent_models().items()}
    return {
        "profile": profile.mode(),
        "opencode": _which(opencode_bin()),
        "version": _opencode_version(),
        "plugins": plugins,
        "oh_my_opencode": omo_installed,
        "superpowers": any("superpowers" in p for p in plugins),
        "agents": agents,
        "default_agent": default_agent(omo_installed),
    }


AUTONOMY_MODES = ("off", "ask", "mechanical", "auto")
AUTONOMY_FILE = os.path.join(STATE_DIR, "autonomy")


def autonomy() -> str:
    """When Claude may delegate: ``off``, ``ask`` (default), ``mechanical`` or ``auto``.

    ``oc mode`` (``/oc:mode``) writes an override that wins over the plugin
    option, so the user can switch without the settings dialog. Hooks get the
    option as ``CLAUDE_PLUGIN_OPTION_AUTONOMY``, the MCP server as ``OC_AUTONOMY``.
    """
    try:
        with open(AUTONOMY_FILE) as fh:
            mode = fh.read().strip().lower()
        if mode in AUTONOMY_MODES:
            return mode
    except OSError:
        pass
    for var in ("CLAUDE_PLUGIN_OPTION_AUTONOMY", "OC_AUTONOMY"):
        mode = (os.environ.get(var) or "").strip().lower()
        if mode in AUTONOMY_MODES:
            return mode
    return "ask"


def set_autonomy(mode: Optional[str]) -> str:
    """Set the override; ``None`` or ``default`` removes it (the plugin option applies again)."""
    if mode in (None, "", "default"):
        try:
            os.remove(AUTONOMY_FILE)
        except OSError:
            pass
        return autonomy()
    mode = mode.strip().lower()
    if mode not in AUTONOMY_MODES:
        raise OcError(f"unknown mode {mode!r} - use one of {', '.join(AUTONOMY_MODES)} or default")
    _ensure_dirs()
    with open(AUTONOMY_FILE, "w") as fh:
        fh.write(mode + "\n")
    return mode


def default_agent(omo_installed: Optional[bool] = None) -> str:
    if os.environ.get("OC_AGENT"):
        return os.environ["OC_AGENT"]
    from . import profile  # late: profile imports core
    if profile.mode() == "isolated":
        return "oc-fast"
    if omo_installed is None:
        omo_installed = any("oh-my-opencode" in p for p in opencode_setup_plugins())
    return "orchestrator" if omo_installed else "build"


def opencode_setup_plugins() -> List[str]:
    cfg = _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.jsonc")) \
        or _load_jsonc(os.path.join(OPENCODE_CONFIG_DIR, "opencode.json")) or {}
    return config_plugins(cfg)


def config_plugins(cfg: Dict[str, Any]) -> List[str]:
    """Plugins of an OpenCode config; OpenCode 2 reads ``plugins`` and still the 1.x key ``plugin``."""
    return [p if isinstance(p, str) else str(p) for p in (cfg.get("plugins") or cfg.get("plugin") or [])]


def opencode_bin() -> str:
    """The OpenCode program: ``OC_OPENCODE`` (a path, for a second install next to
    another major version) or ``opencode`` from PATH."""
    return os.environ.get("OC_OPENCODE") or "opencode"


def _which(cmd: str) -> Optional[str]:
    if os.sep in cmd:
        return cmd if os.path.isfile(cmd) and os.access(cmd, os.X_OK) else None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, cmd)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def opencode_major(version: Optional[str]) -> Optional[int]:
    """``1.19.4`` -> 1, ``opencode v2.0.29`` -> 2, unknown -> None."""
    m = re.search(r"(\d+)\.\d+", version or "")
    return int(m.group(1)) if m else None


OPENCODE_1_UNSUPPORTED = ("This OpenCode ({version}) is OpenCode 1 - oc-connect 2.x needs OpenCode 2 "
                          "(https://opencode.ai/v2/install). For OpenCode 1 install oc-connect 1.x: "
                          "claude plugin marketplace add jonaandresen/oc-connect#v1")


def require_opencode_2() -> None:
    """Refuse OpenCode 1 with a clear message instead of an HTTP error.

    OpenCode 1 serves an older API without ``/api`` and without a password;
    oc-connect 1.x (branch ``v1``) speaks it. An unknown version passes - the
    first API call tells.
    """
    version = _opencode_version()
    major = opencode_major(version)
    if major is not None and major < 2:
        raise OcError(OPENCODE_1_UNSUPPORTED.format(version=version))


def _opencode_version() -> Optional[str]:
    try:
        return subprocess.run([opencode_bin(), "--version"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
