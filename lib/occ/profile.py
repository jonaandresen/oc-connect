"""The worker profile: an OpenCode configuration of our own for workers.

Why: the user's global OpenCode setup is built for interactive use. Plugins such
as oh-my-opencode (an orchestrator that never delegated in our jobs, and helper
agents that are *subagents*, so the TUI cannot select them) and superpowers
(brainstorming that stops to ask for approval) work against a worker that got
a finished plan from Claude.

How, measured on OpenCode 1.18:
  * ``OPENCODE_CONFIG`` / ``OPENCODE_CONFIG_CONTENT`` are MERGED into the global
    config - an empty ``plugin`` list does not unload oh-my-opencode.
  * ``OPENCODE_CONFIG_DIR`` adds a directory, it does not replace the global one.
  * ``--pure`` drops ALL external plugins, including the Vibe Island / Open
    Island plugin files - session trackers would lose the workers.
  * ``XDG_CONFIG_HOME`` pointing to a directory of our own replaces the global
    config. Plugin FILES placed in its ``opencode/plugins`` still load, and
    logins survive because they live in the data dir, not the config dir.

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
from typing import Any, Dict, Optional

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
# Settings of the user's global config that a worker needs as well.
CARRY = ("provider", "model", "small_model", "mcp", "lsp", "formatter", "disabled_providers",
         "enabled_providers")


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


def build_config() -> Dict[str, Any]:
    user = _user_config()
    cfg: Dict[str, Any] = {"$schema": "https://opencode.ai/config.json", "autoupdate": False,
                           "default_agent": "oc-fast"}
    for key in CARRY:
        if key in user:
            cfg[key] = user[key]
    # The question tool exists but is off unless permitted (measured: without
    # this the worker said "there is no question tool" and stopped). Push,
    # docker & co. are handled by the guard (OPENCODE_CONFIG) on top.
    cfg["permission"] = {"question": "allow"}
    prompt = "{file:" + os.path.join(PLUGIN_ROOT, "templates", "worker-prompt.md") + "}"
    cfg["agent"] = {}
    for name, model in agent_models().items():
        agent: Dict[str, Any] = {"mode": "primary", "description": AGENTS[name]["description"], "prompt": prompt}
        if model:
            agent["model"] = model
        cfg["agent"][name] = agent
    return cfg


def _replace_link(link: str, target: str) -> None:
    if os.path.islink(link) and os.readlink(link) == target:
        return
    if os.path.islink(link) or os.path.isfile(link):
        os.remove(link)
    elif os.path.isdir(link):
        shutil.rmtree(link)
    os.symlink(target, link)


def ensure_home() -> str:
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
        # Plugin FILES are session trackers and the like - keep them.
        for name in os.listdir(src_plugins):
            if name.endswith((".js", ".ts", ".mjs")):
                present.add(name)
                _replace_link(os.path.join(plugins, name), os.path.join(src_plugins, name))
    for name in os.listdir(plugins):
        if name not in present:
            os.remove(os.path.join(plugins, name))
    tmp = os.path.join(oc_dir, "opencode.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(build_config(), fh, indent=2)
    os.replace(tmp, os.path.join(oc_dir, "opencode.json"))
    return HOME
