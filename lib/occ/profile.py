"""The worker profile: an OpenCode configuration of our own for workers.

Why: the user's global OpenCode setup is built for interactive use. Plugins such
as oh-my-opencode (an orchestrator that never delegated in our jobs, and helper
agents that are *subagents*, so the TUI cannot select them) and superpowers
(brainstorming that stops to ask for approval) work against a worker that got
a finished plan from Claude.

How (measured on OpenCode 1.18, re-checked on 2.1.4):
  * ``OPENCODE_CONFIG`` is MERGED into the global config - it cannot unload a
    plugin. ``--pure`` would drop session trackers as well.
  * ``XDG_CONFIG_HOME`` pointing to a directory of our own replaces the global
    config. Plugins placed in its ``opencode/plugins`` still load, and logins
    survive because they live in the data dir, not the config dir.
  * OpenCode 2 drops an agent whose config names a model - silently, in every
    notation tried (``provider/model``, ``{providerID, id}``). So the agents here
    carry no model; the job's model is set on its session instead.
  * The TUI reads ``cli.json`` (its own plugins, e.g. a session tracker's TUI
    part); the worker home gets the user's, without oh-my-opencode.

``XDG_CONFIG_HOME`` also moves the config of every other tool the worker runs
(gh, git, ...). So the worker home mirrors every entry of ``~/.config`` as a
symlink, except ``opencode``.

``OC_PROFILE=inherit`` switches all of this off and runs workers with the
user's own setup.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

from . import core

HOME = os.path.join(core.STATE_DIR, "worker-home")
USER_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Our agents. PRIMARY, so the tab can start with --agent and shows the right one.
# Models come from the matching oh-my-opencode role when that is configured.
AGENTS = {
    "oc-fast": {"role": "fixer", "description": "Bounded, mechanical work with a precise plan: tests, "
                                                "small fixes, docs, renames."},
    "oc-strong": {"role": "orchestrator", "description": "Larger work across several files."},
    "oc-ui": {"role": "designer", "description": "User interface and styling."},
}
# Settings of the user's global config that a worker needs as well (1.x and 2.x key names:
# OpenCode 2 still reads a 1.x config).
CARRY = ("provider", "providers", "model", "small_model", "mcp", "lsp", "formatter", "disabled_providers",
         "enabled_providers")
SKIP_PLUGIN = ("oh-my-opencode", "node_modules", "package.json", "package-lock.json")


def mode() -> str:
    """``isolated`` (default) or ``inherit``."""
    return "inherit" if (os.environ.get("OC_PROFILE") or "").strip().lower() == "inherit" else "isolated"


def _omo_models() -> Dict[str, str]:
    for name in ("oh-my-opencode-slim.json", "oh-my-opencode-slim.jsonc", "oh-my-opencode.json"):
        cfg = core._load_jsonc(os.path.join(core.OPENCODE_CONFIG_DIR, name))
        if cfg:
            preset = (cfg.get("presets") or {}).get(cfg.get("preset")) or cfg.get("agents") or {}
            return {role: (v or {}).get("model") for role, v in preset.items() if isinstance(v, dict)}
    return {}


def _user_config() -> Dict[str, Any]:
    return core._load_jsonc(os.path.join(core.OPENCODE_CONFIG_DIR, "opencode.jsonc")) \
        or core._load_jsonc(os.path.join(core.OPENCODE_CONFIG_DIR, "opencode.json")) or {}


def agent_models() -> Dict[str, Optional[str]]:
    """Model per worker agent; None means OpenCode's default model."""
    omo = _omo_models()
    fallback = _user_config().get("model")
    return {name: omo.get(spec["role"]) or fallback for name, spec in AGENTS.items()}


def build_config(guard: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """The worker's ``opencode.json`` in OpenCode 2's format.

    ``guard`` are the permission rules every worker runs with (see
    ``ops.guard_rules``); they go here as well as onto each session, so a
    session opened by hand in the worker's tab is guarded too.
    """
    user = _user_config()
    cfg: Dict[str, Any] = {"$schema": "https://opencode.ai/config.json", "update": "disable",
                           "default_agent": "oc-fast"}
    for key in CARRY:
        if key in user:
            cfg[key] = user[key]
    # The question tool must be allowed explicitly (measured on 1.x: without it
    # the worker said "there is no question tool" and stopped).
    cfg["permissions"] = [{"action": "question", "resource": "*", "effect": "allow"}] + list(guard or [])
    with open(os.path.join(PLUGIN_ROOT, "templates", "worker-prompt.md")) as fh:
        prompt = fh.read()
    cfg["agents"] = {name: {"mode": "primary", "description": spec["description"], "system": prompt}
                     for name, spec in AGENTS.items()}
    return cfg


def _user_cli_config() -> Dict[str, Any]:
    cfg = core._load_jsonc(os.path.join(core.OPENCODE_CONFIG_DIR, "cli.json")) or {}
    if isinstance(cfg.get("plugins"), list):
        cfg["plugins"] = [p for p in cfg["plugins"] if "oh-my-opencode" not in str(p)]
    return cfg


def _replace_link(link: str, target: str) -> None:
    if os.path.islink(link) and os.readlink(link) == target:
        return
    if os.path.islink(link) or os.path.isfile(link):
        os.remove(link)
    elif os.path.isdir(link):
        shutil.rmtree(link)
    os.symlink(target, link)


def ensure_home(guard: Optional[List[Dict[str, str]]] = None) -> str:
    """Create or refresh the worker home; returns the value for XDG_CONFIG_HOME.

    Refreshed on every job start, so new tools in ``~/.config`` and changes to
    the user's OpenCode setup reach the next worker.
    """
    os.makedirs(HOME, exist_ok=True)
    wanted = set()
    if os.path.isdir(USER_CONFIG):
        for name in os.listdir(USER_CONFIG):
            if name == "opencode":
                continue
            wanted.add(name)
            _replace_link(os.path.join(HOME, name), os.path.join(USER_CONFIG, name))
    for name in os.listdir(HOME):  # entries the user removed from ~/.config
        path = os.path.join(HOME, name)
        if name != "opencode" and name not in wanted and os.path.islink(path):
            os.remove(path)
    oc_dir = os.path.join(HOME, "opencode")
    plugins = os.path.join(oc_dir, "plugins")
    os.makedirs(plugins, exist_ok=True)
    src_plugins = os.path.join(core.OPENCODE_CONFIG_DIR, "plugins")
    present = set()
    if os.path.isdir(src_plugins):
        # Local plugins are session trackers and the like - keep them, files and
        # plugin folders (a tracker's TUI part), but no oh-my-opencode.
        for name in os.listdir(src_plugins):
            src = os.path.join(src_plugins, name)
            if any(skip in name for skip in SKIP_PLUGIN) or name.startswith("."):
                continue
            if os.path.isdir(src) or name.endswith((".js", ".ts", ".mjs")):
                present.add(name)
                _replace_link(os.path.join(plugins, name), src)
    for name in os.listdir(plugins):
        if name not in present:
            path = os.path.join(plugins, name)
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path)
    for name, content in (("opencode.json", build_config(guard)), ("cli.json", _user_cli_config())):
        tmp = os.path.join(oc_dir, name + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(content, fh, indent=2)
        os.replace(tmp, os.path.join(oc_dir, name))
    return HOME
