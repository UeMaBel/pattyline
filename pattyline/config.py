"""Versioned project snapshots; global configuration is read only on explicit import."""
from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import tomllib
import re

from .common import Blocked, atomic_write, digest, within

DEFAULTS = {
    "schema": 1, "workflow": "1", "tickets": ["docs/pattyline/*/issues/*.md"],
    "instructions": [], "base": "main", "remote": "origin", "repository": "",
    "worktrees": "../pattyline-worktrees", "checks": {}, "setup": [],
    "runtime": {"adapter": "broker", "codex": "codex", "version": "0.166.0", "cloud_model": "gpt-6.1-sol",
                "local_model": "", "ollama": "ollama", "ollama_url": "http://127.0.0.1:11434",
                "container_image": "", "container_python": "python3", "api_key_env": "OPENAI_API_KEY",
                "max_tool_calls": 30, "max_output_tokens": 4096, "context_tokens": 65536,
                "max_context_bytes": 48000, "execution_platform": "linux"},
    "permissions": {"network": False, "hard_cost_cap": False, "command_policy": "container-unrestricted"},
    "limits": {"implementation_seconds": 1800, "review_seconds": 900,
               "integration_seconds": 1200, "run_seconds": 14400,
               "termination_grace_seconds": 10, "repairs": 2, "local_repairs": 1},
    "escalation": {"allowed": False},
    "integration": {"method": "merge", "freshness": "strict-required-checks"},
}
GLOBAL_KEYS = {"runtime", "permissions", "limits", "escalation"}


def global_path():
    return _global_path(os.name, os.environ, Path.home())


def _global_path(os_name, environ, home):
    """Resolve the per-user settings file for a supplied platform/environment."""
    if os_name == "nt":
        base = Path(environ.get("APPDATA", Path(home) / "AppData/Roaming"))
    else:
        base = Path(environ.get("XDG_CONFIG_HOME", Path(home) / ".config"))
    return base / "pattyline/config.toml"


def initialize_global():
    """Create first-run per-user defaults, leaving every existing setting untouched."""
    path = global_path()
    defaults = {key: deepcopy(DEFAULTS[key]) for key in GLOBAL_KEYS}
    if path.exists():
        existing = tomllib.loads(path.read_text(encoding="utf-8"))
        return merge(defaults, {key: value for key, value in existing.items() if key in GLOBAL_KEYS})
    atomic_write(path, toml_text(defaults))
    return defaults


def merge(left, right):
    result = deepcopy(left)
    for key, value in right.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


def toml_text(data):
    """Encode the deliberately small config value language without a dependency."""
    def scalar(value):
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (str, int, float)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            return "[" + ", ".join(scalar(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{ " + ", ".join(f"{json.dumps(k)} = {scalar(v)}" for k, v in value.items()) + " }"
        raise Blocked(f"Unsupported configuration value: {type(value).__name__}")
    return "\n".join(f"{json.dumps(key)} = {scalar(value)}" for key, value in data.items()) + "\n"


def save(root, data):
    validate(data)
    atomic_write(Path(root) / "pattyline.toml", toml_text(data))


def initialize(root, use_global=False):
    root = Path(root)
    path = root / "pattyline.toml"
    if path.exists():
        return load(root)
    values = deepcopy(DEFAULTS)
    if use_global and global_path().exists():
        global_values = tomllib.loads(global_path().read_text(encoding="utf-8"))
        values = merge(values, {k: v for k, v in global_values.items() if k in GLOBAL_KEYS})
    save(root, values)
    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if ".pattyline/" not in existing.splitlines():
        atomic_write(ignore, existing.rstrip() + "\n.pattyline/\n")
    return values


def load(root):
    path = Path(root) / "pattyline.toml"
    if not path.exists():
        raise Blocked("No pattyline.toml; run pattyline init first")
    values = merge(DEFAULTS, tomllib.loads(path.read_text(encoding="utf-8")))
    validate(values)
    return values


def validate(values):
    for name in ("runtime", "permissions", "limits", "escalation", "integration", "checks"):
        if not isinstance(values.get(name), dict):
            raise Blocked(f"{name}: expected a table")
    for name in ("instructions", "tickets"):
        if not isinstance(values.get(name), list) or any(not isinstance(item, str) or not item for item in values[name]):
            raise Blocked(f"{name}: expected an array of nonempty paths")
    if not isinstance(values.get("setup"), list):
        raise Blocked("setup: expected an array of structured commands")
    for name in ("base", "remote", "repository", "worktrees"):
        if not isinstance(values.get(name), str):
            raise Blocked(f"{name}: expected a string")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", values["remote"]):
        raise Blocked("remote: expected a Git remote name, not a URL or option")
    if not values["base"] or values["base"].startswith("-") or re.search(r"[\s~^:?*\[\\]", values["base"]) or ".." in values["base"] or "@{" in values["base"]:
        raise Blocked("base: invalid branch name")
    if values["repository"] and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", values["repository"]):
        raise Blocked("repository: expected owner/name")
    if values.get("schema") != 1 or values.get("workflow") != "1":
        raise Blocked("Unsupported configuration schema/workflow; no automatic migration")
    if values["integration"]["method"] != "merge":
        raise Blocked("V1 supports normal merge only")
    for key, value in values["limits"].items():
        if type(value) is not int or value < (0 if key in {"repairs", "local_repairs"} else 1):
            raise Blocked(f"limits.{key}: expected a positive integer (repair counts may be zero)")
    if values["limits"]["repairs"] > 2 or values["limits"]["local_repairs"] > values["limits"]["repairs"]:
        raise Blocked("At most two repairs; local repairs must fit total repair budget")
    for name, command in values["checks"].items():
        command_for(command, name, values["runtime"].get("execution_platform"))
    for command in values["setup"]:
        command_for(command, "setup", values["runtime"].get("execution_platform"))
    if any(k in values["runtime"] for k in ("token", "api_key", "password")):
        raise Blocked("Use authentication references, never inline secrets")
    if values["runtime"]["adapter"] not in {"broker", "codex"}:
        raise Blocked("runtime.adapter must be broker or codex; fixtures are test-only")
    if values["permissions"].get("network") is not False:
        raise Blocked("V1 broker tool network must be disabled; no unsupported policy weakening")
    for key in ("max_tool_calls", "max_output_tokens", "context_tokens", "max_context_bytes"):
        if type(values["runtime"][key]) is not int or values["runtime"][key] < 1:
            raise Blocked(f"runtime.{key}: positive integer required")
    if values["runtime"]["max_context_bytes"] + values["runtime"]["max_output_tokens"] + 4096 > values["runtime"]["context_tokens"]:
        raise Blocked("Conservative context byte budget plus output/protocol reserve exceeds configured context")


def command_for(value, label="check", execution_platform=None):
    if not isinstance(value, dict):
        raise Blocked(f"{label}: expected a structured command table")
    selected = execution_platform or platform.system().lower()
    command = value.get(selected, value)
    if not isinstance(command, dict):
        raise Blocked(f"{label}: platform command must be a table")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise Blocked(f"{label}: command requires a nonempty argv array for {selected}")
    if type(command.get("timeout")) is not int or command["timeout"] < 1:
        raise Blocked(f"{label}: positive timeout required")
    cwd = Path(command.get("cwd", "."))
    if cwd.is_absolute() or ".." in cwd.parts:
        raise Blocked(f"{label}: cwd must be project-relative")
    return command


def refresh(root, selected=()):
    current = load(root)
    source = tomllib.loads(global_path().read_text(encoding="utf-8")) if global_path().exists() else {}
    changes = {k: {"before": current.get(k), "after": source[k]}
               for k in sorted(GLOBAL_KEYS & source.keys()) if current.get(k) != source[k]}
    unknown = set(selected) - changes.keys()
    if unknown:
        raise Blocked(f"No global changes for: {', '.join(sorted(unknown))}")
    if selected:
        save(root, merge(current, {k: source[k] for k in selected}))
    return changes
