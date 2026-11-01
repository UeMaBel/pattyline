"""Codex candidate contract and capability ledger. No fake production route."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import uuid

from .common import Blocked, CapabilityMismatch, digest, load_json, now, write_json
from .process import run

REQUIRED = {"fresh-session", "structured-result", "readonly-review", "protected-inputs",
            "command-isolation", "credential-isolation", "network-isolation", "tree-termination"}


def inspect(config):
    executable = shutil.which(config["runtime"]["codex"])
    if not executable:
        return {"available": False, "blockers": ["Install Codex CLI; configure runtime.codex"]}
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    version = result.stdout.strip()
    return {"available": result.returncode == 0, "path": executable, "version": version,
            "platform": platform.platform(), "expected_version": config["runtime"]["version"],
            "blockers": [] if version.endswith(" " + config["runtime"]["version"]) else ["Codex version differs from pinned runtime.version"]}


class Codex:
    def __init__(self, root, config):
        self.root, self.config = Path(root), config

    def readiness(self, route="cloud", stage="implementation"):
        info = inspect(self.config)
        blockers = list(info["blockers"])
        if not self.config["runtime"].get(f"{route}_model"):
            blockers.append(f"Configure runtime.{route}_model and authenticate/prepare it")
        # There is intentionally no operator-supplied 'verified=true' escape hatch.
        # This candidate cannot enforce every required boundary on either host yet.
        blockers.append("Codex production adapter capability gate blocked: no demonstrated outer boundary protecting credentials, approved inputs, command/network policy and process descendants; CLI sandbox flags alone are insufficient")
        if self.config["permissions"].get("hard_cost_cap"):
            blockers.append("No demonstrated hard monetary-cap enforcement for this runtime")
        return blockers

    def execute(self, stage, route, worktree, prompt, **options):
        blockers = self.readiness(route, stage)
        if blockers:
            raise Blocked("; ".join(blockers))
        raise Blocked("Production runtime dispatch remains unavailable until the capability gate is implemented and demonstrated")


def validate_result(result, stage):
    if not isinstance(result, dict):
        raise CapabilityMismatch("Malformed runtime result: expected object")
    if not isinstance(result.get("session"), str) or not result["session"]:
        raise CapabilityMismatch("Runtime result lacks independent session identity")
    if stage == "review":
        if result.get("verdict") not in {"approved", "changes-required", "blocked"}:
            raise CapabilityMismatch("Malformed reviewer verdict")
        if not isinstance(result.get("findings"), list) or not isinstance(result.get("acceptance_evidence"), dict):
            raise CapabilityMismatch("Reviewer must identify findings and acceptance evidence")
    elif stage == "implementation":
        if not isinstance(result.get("summary"), str) or not isinstance(result.get("files"), list) or not result["files"]:
            raise CapabilityMismatch("Implementation result requires summary and changed files")
    from .broker import schema, validate_payload
    instrumentation = {"session", "provider_sessions", "usage", "tools", "transcript"}
    validate_payload({k: v for k, v in result.items() if k not in instrumentation}, schema(stage))
    return result


def record_inventory(root, config):
    """Read-only, no inference/download/paid operation. Never records a capability pass."""
    info = inspect(config)
    value = {"time": now(), "inventory": info, "capabilities": {name: "not demonstrated" for name in sorted(REQUIRED)},
             "local_tool_use": "not demonstrated", "usage": "unknown",
             "release_ready": False, "scope": "executable/version inventory only"}
    write_json(Path(root) / ".pattyline/evidence/runtime-inventory.json", value)
    return value


def adapter(root, config):
    if config["runtime"]["adapter"] == "broker":
        from .broker import Broker
        return Broker(root, config)
    return Codex(root, config)
