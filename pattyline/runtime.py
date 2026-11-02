"""Codex candidate contract and capability ledger. No fake production route."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import re
import threading
import uuid

from .common import Blocked, CapabilityMismatch, digest, load_json, now, write_json
from .process import run
from .terminal import Spinner

REQUIRED = {"fresh-session", "structured-result", "readonly-review", "protected-inputs",
            "command-isolation", "credential-isolation", "network-isolation", "tree-termination"}


def inspect(config):
    executable = shutil.which(config["runtime"]["codex"])
    if not executable:
        return {"available": False, "blockers": ["Install Codex CLI; configure runtime.codex"]}
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
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
        # Preparation only returns drafts and has no tool authority. Codex runs
        # with its own native sign-in; execution/review remain behind this gate.
        if stage != "preparation":
            blockers.append("Codex production adapter capability gate blocked: no demonstrated outer boundary protecting credentials, approved inputs, command/network policy and process descendants; CLI sandbox flags alone are insufficient")
        if self.config["permissions"].get("hard_cost_cap"):
            blockers.append("No demonstrated hard monetary-cap enforcement for this runtime")
        return blockers

    def execute(self, stage, route, worktree, prompt, **options):
        blockers = self.readiness(route, stage)
        if blockers:
            raise Blocked("; ".join(blockers))
        if stage != "preparation":
            raise Blocked("Production runtime dispatch remains unavailable until the capability gate is implemented and demonstrated")
        executable = shutil.which(self.config["runtime"]["codex"])
        if not executable:
            raise Blocked("Install Codex CLI; configure runtime.codex")
        from .broker import schema
        with tempfile.TemporaryDirectory(prefix="pattyline-codex-prepare-") as temporary:
            output_path = Path(temporary) / "result.json"
            schema_file = Path(temporary) / "schema.json"
            output_schema = schema("preparation")
            # Codex's structured-output API requires a properties object for
            # every object schema; represent dynamic maps as key/value pairs.
            fields = output_schema["properties"]
            fields["terms"] = {"type": "array", "items": {"type": "object", "properties": {
                "term": {"type": "string"}, "definition": {"type": "string"}},
                "required": ["term", "definition"], "additionalProperties": False}}
            fields["artifacts"] = {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"], "additionalProperties": False}}
            fields["tickets"] = {"type": "array", "items": {"type": "object", "properties": {
                "number": {"type": "string"}, "title": {"type": "string"}, "outcome": {"type": "string"},
                "route": {"type": "string"}, "dependencies": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "phase": {"type": "string"}}, "required": ["name", "phase"], "additionalProperties": False}},
                "acceptance": {"type": "array", "items": {"type": "string"}},
                "cloud_escalation_allowed": {"type": "boolean"}},
                "required": ["number", "title", "outcome", "route", "dependencies", "scope", "verification", "acceptance", "cloud_escalation_allowed"],
                "additionalProperties": False}}
            schema_file.write_text(json.dumps(output_schema), encoding="utf-8")
            request = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
            model = self.config["runtime"].get("cloud_model") or "Codex default model"
            argv = [executable, "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--color", "never",
                    "--model", model,
                    "--output-schema", str(schema_file), "--output-last-message", str(output_path), "-"]
            timeout = options.get("timeout", 1800)
            proc = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    bufsize=1)
            lines = []
            outcome = {}
            detail = "model: " + model

            def feed():
                try:
                    proc.stdin.write(request)
                    proc.stdin.close()
                    for line in proc.stdout:
                        lines.append(line.rstrip())
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        payload = event.get("payload", {}) if isinstance(event, dict) else {}
                        event_type = event.get("type", "") if isinstance(event, dict) else ""
                        if event_type in {"turn.started", "item.started", "item.completed", "turn.completed", "reasoning.summary_text.delta", "reasoning.summary_text.done"}:
                            label = (payload.get("delta") or payload.get("text") or payload.get("title")
                                     or payload.get("name") or payload.get("type") or event_type)
                            with waiting_lock:
                                prefix = "reasoning: " if "reasoning" in event_type else "status: "
                                status_detail[0] = f"model: {model}; {prefix}{str(label)[:100]}"
                        # Codex may include account/rate-limit usage in future
                        # events. Surface it only when explicitly reported.
                        if isinstance(payload, dict):
                            usage = payload.get("rate_limits") or payload.get("account_usage")
                            if usage:
                                with waiting_lock:
                                    status_detail[0] = f"model: {model}; usage: {json.dumps(usage, ensure_ascii=False)[:120]}"
                    outcome["code"] = proc.wait()
                except BaseException as error:
                    outcome["error"] = error

            waiting_lock = threading.Lock()
            status_detail = [detail]
            try:
                with Spinner("Waiting for Codex", detail="model: " + model) as waiting:
                    reader = threading.Thread(target=feed, daemon=True)
                    reader.start()
                    elapsed = 0
                    while reader.is_alive() and elapsed < timeout:
                        reader.join(0.5)
                        elapsed += 0.5
                        with waiting_lock:
                            waiting.detail = status_detail[0]
                    if reader.is_alive():
                        proc.kill()
                        reader.join(5)
                        raise Blocked(f"Codex preparation timed out after {timeout}s; model: {model}")
            finally:
                if proc.poll() is None:
                    proc.kill()
            if "error" in outcome:
                raise Blocked("Codex preparation process failed") from outcome["error"]
            if outcome.get("code"):
                detail = "\n".join(lines[-20:]).strip()[-2000:]
                raise Blocked("Codex preparation failed" + (": " + detail if detail else ""))
            try:
                value = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise Blocked("Codex preparation did not return valid structured output") from None
            from .broker import validate_payload
            value["terms"] = _pairs_to_map(value.get("terms", []), "term", "definition")
            value["artifacts"] = _pairs_to_map(value.get("artifacts", []), "path", "content")
            validate_payload(value, schema("preparation"))
            value["session"] = uuid.uuid4().hex
            value["usage"] = "Codex native session; provider usage not exposed"
            return value


def _pairs_to_map(items, key, value):
    if not isinstance(items, list):
        raise Blocked("Codex preparation returned malformed structured key/value data")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str) or not isinstance(item.get(value), str):
            raise Blocked("Codex preparation returned malformed structured key/value data")
        if item[key] in result:
            raise Blocked("Codex preparation returned duplicate structured keys")
        result[item[key]] = item[value]
    return result


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
