"""Credential-separated local/cloud model loop with container-only tool authority."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import sys

from .common import Blocked, CapabilityMismatch, canonical, digest, load_json, now, write_json, redact
from .onboarding import local_inventory, ollama_api, inventory
from .sandbox import Container
from .process import run, sanitized_env

TOOL = {"type": "function", "name": "run_command", "description": "Run argument-array command in the isolated /workspace container; no host files, credentials or network.",
        "strict": True, "parameters": {"type": "object", "additionalProperties": False,
        "properties": {"argv": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}},
        "required": ["argv", "cwd", "timeout"]}}
ENV_TOOL = {"type": "function", "name": "inspect_environment", "description": "Read actual installed tool versions, hardware and readiness facts. No installation, downloads, authentication or project-code execution.",
            "strict": True, "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}


def schema(stage):
    if stage == "init":
        stage = "preparation"
    string = {"type": "string"}
    array = {"type": "array", "items": string}
    mapping = {"type": "object", "additionalProperties": string}
    fields = {"implementation": {"summary": string, "files": array, "limitations": array},
              "review": {"verdict": {"type": "string", "enum": ["approved", "changes-required", "blocked"]}, "findings": array, "acceptance_evidence": mapping},
              "preparation": {"summary": string, "decisions": array, "terms": mapping, "open_questions": array,
                              "artifacts": mapping, "tickets": {"type": "array", "items": {"type": "object"}}, "verdict": string}}[stage]
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


class Transport:
    def __init__(self, config):
        self.config = config

    def request(self, url, body, headers, timeout, **options):
        started = options.pop("on_start", lambda process: None)
        stopped = options.pop("on_stop", lambda process: None)
        with tempfile.TemporaryDirectory(prefix="pattyline-http-") as home:
            result = run([sys.executable, "-I", "-S", str(Path(__file__).with_name("http_transport.py"))], home,
                         timeout, env=sanitized_env(home),
                         on_start=lambda process: started({**process, "mechanism": "provider-transport", "trusted_leaf": True}),
                         on_stop=lambda process: stopped({**process, "mechanism": "provider-transport", "trusted_leaf": True}),
                         input_text=canonical({"url": url, "body": body, "headers": headers, "timeout": timeout}), **options)
        if result["outcome"] != "exited":
            raise Blocked("Provider request cancelled/deadline; remote completion and usage unknown; no automatic replay")
        response = parse_content(result["stdout"])
        if result["exit_code"] or response.get("transport_error"):
            raise Blocked(response.get("transport_error", "Provider transport failed; usage unknown"))
        return response

    def complete(self, route, history, output_schema, timeout, **options):
        runtime = self.config["runtime"]
        preparation = "artifacts" in output_schema["properties"]
        if route == "local":
            selected_tool = ENV_TOOL if preparation else TOOL
            tools = [{"type": "function", "function": {key: value for key, value in selected_tool.items() if key not in {"type", "strict"}}}]
            response = self.request(runtime["ollama_url"].rstrip("/") + "/api/chat", {"model": runtime["local_model"], "messages": history,
                    "tools": tools, "format": output_schema, "stream": False,
                    "options": {"num_predict": runtime["max_output_tokens"], "num_ctx": runtime["context_tokens"]}}, {"Content-Type": "application/json"}, timeout, **options)
            if response.get("done") is not True or response.get("done_reason") not in {"stop", "tool_calls"}:
                raise Blocked("Local response incomplete/context/output limit reached; structured success unavailable")
            message = response.get("message", {})
            calls = [{"id": str(index), "name": item.get("function", {}).get("name"),
                      "arguments": item.get("function", {}).get("arguments")} for index, item in enumerate(message.get("tool_calls", []))]
            return {"calls": calls, "message": message, "result": None if calls else parse_content(message.get("content", "")),
                    "session": response.get("created_at", "stateless"),
                    "usage": {key: response[key] for key in ("prompt_eval_count", "eval_count", "total_duration") if key in response}}
        key = os.environ.get(runtime["api_key_env"])
        if not key:
            raise Blocked(f"Cloud broker requires credential reference {runtime['api_key_env']}; Codex login is not an API credential")
        body = {"model": runtime["cloud_model"], "input": history, "tools": [ENV_TOOL if preparation else TOOL], "store": False,
                "max_output_tokens": runtime["max_output_tokens"], "parallel_tool_calls": False,
                "text": {"format": {"type": "json_schema", "name": "pattyline_result", "schema": output_schema, "strict": False}}}
        response = self.request("https://api.openai.com/v1/responses", body,
                                {"Content-Type": "application/json", "Authorization": "Bearer " + key}, timeout, **options)
        if response.get("status") != "completed":
            raise Blocked("Cloud response incomplete/refused; no success evidence")
        output = response.get("output", [])
        calls = [{"id": item["call_id"], "name": item.get("name"), "arguments": parse_content(item.get("arguments", ""))}
                 for item in output if item.get("type") == "function_call"]
        content = "".join(c.get("text", "") for item in output if item.get("type") == "message" for c in item.get("content", []) if c.get("type") == "output_text")
        return {"calls": calls, "message": output, "result": None if calls else parse_content(content),
                "session": response.get("id", "unknown"), "usage": response.get("usage", "unknown")}


def parse_content(content):
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        raise CapabilityMismatch("Malformed structured runtime/tool result; no success attestation") from None


def validate_payload(value, rule, path="result"):
    expected = rule.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if expected and type(value) is not types[expected]:
        raise CapabilityMismatch(f"Malformed structured {path}: expected {expected}")
    if "enum" in rule and value not in rule["enum"]:
        raise CapabilityMismatch(f"Malformed structured {path}: invalid enum")
    if isinstance(value, dict):
        fields = rule.get("properties", {})
        for name in rule.get("required", []):
            if name not in value:
                raise CapabilityMismatch(f"Malformed structured {path}: missing {name}")
        for name, item in value.items():
            if name in fields:
                validate_payload(item, fields[name], path + "." + name)
            elif rule.get("additionalProperties") is False:
                raise CapabilityMismatch(f"Malformed structured {path}: unexpected {name}")
            elif isinstance(rule.get("additionalProperties"), dict):
                validate_payload(item, rule["additionalProperties"], path + "." + name)
    if isinstance(value, list) and "items" in rule:
        for index, item in enumerate(value):
            validate_payload(item, rule["items"], f"{path}[{index}]")
    return value


class Broker:
    def __init__(self, root, config, sandbox=None, transport=None):
        self.root, self.config = Path(root).resolve(), config
        self.sandbox = sandbox if sandbox is not None else Container(config)
        self.transport = transport if transport is not None else Transport(config)

    def binding(self, route, stage):
        return digest({"adapter": "broker-1", "platform": platform.platform(), "runtime": self.config["runtime"],
                       "permissions": self.config["permissions"], "route": route, "stage": stage})

    def prerequisites(self, route, stage="implementation"):
        is_init = stage in {"preparation", "init"}
        blockers = [] if is_init else list(self.sandbox.readiness())
        runtime = self.config["runtime"]
        if not runtime.get(f"{route}_model"):
            blockers.append(f"Configure runtime.{route}_model")
        if self.config["permissions"].get("hard_cost_cap"):
            blockers.append("Broker cannot enforce a hard monetary cap; choose a capable provider or reconcile that requirement")
        if route == "cloud" and not os.environ.get(runtime["api_key_env"]):
            blockers.append(f"Cloud broker requires {runtime['api_key_env']} in the human orchestrator environment; never in the project or container")
        if route == "local":
            try:
                details = local_inventory(self.config)
                if not details["selected_installed"]:
                    blockers.append("Selected local model not installed; consent to its download explicitly")
                elif not details.get("context") or max(details["context"].values()) < runtime["context_tokens"]:
                    blockers.append("Selected local model context capacity is unknown or below configured runtime.context_tokens")
            except Blocked as error:
                blockers.append(str(error))
        return blockers

    def readiness(self, route="cloud", stage="implementation"):
        blockers = self.prerequisites(route, stage)
        evidence = load_json(self.root / f".pattyline/evidence/broker-{route}-{stage}.json", {})
        signature = evidence.pop("signature", "")
        key_path = self.root / ".pattyline/probe-key"
        if not key_path.exists() or not signature or not hmac.compare_digest(signature, hmac.new(key_path.read_bytes(), canonical(evidence).encode(), hashlib.sha256).hexdigest()) or evidence.get("binding") != self.binding(route, stage):
            blockers.append(f"Missing trusted real {route}/{stage} capability evidence; run setup --probe-worker {route}:{stage} with explicit bounded-usage consent")
        return blockers

    def execute(self, stage, route, worktree, prompt, timeout=1800, _probe=False, **options):
        blockers = self.prerequisites(route, stage) if _probe else self.readiness(route, stage)
        if blockers:
            raise Blocked("; ".join(blockers))
        session = uuid.uuid4().hex
        expires = time.monotonic() + timeout
        cancelled = options.get("cancelled", lambda: False)
        protected = prompt.get("protected_paths", [])
        is_init = stage in {"preparation", "init"}
        authority = "Use inspect_environment for actual environment facts; return drafts as structured artifacts, with no arbitrary code/file authority." if is_init else "Use only run_command inside the isolated /workspace. Read-only review must independently assess exact evidence."
        history = [{"role": "system", "content": authority + " Return the specified structured result. Never approve tickets or perform Git/GitHub integration."},
                   {"role": "user", "content": canonical(prompt)}]
        transcript, usage, provider_sessions = [], [], []
        tool_calls_used = 0
        log_path = self.root / ".pattyline/transcripts" / f"{session}.json"
        log = {"session": session, "route": route, "stage": stage, "started": now(),
               "approved_context": prompt, "tools": transcript, "usage": usage, "provider_sessions": provider_sessions}
        def persist(status):
            write_json(log_path, redact({**log, "status": status, "updated": now()}))
        persist("prepared")
        for index in range(self.config["runtime"]["max_tool_calls"] + 1):
            if cancelled() or time.monotonic() >= expires:
                raise Blocked("Model stage cancelled/deadline; preserved tool evidence and work")
            context_bytes = len(canonical({"history": history, "tools": TOOL, "schema": schema(stage)}).encode())
            if context_bytes > self.config["runtime"]["max_context_bytes"]:
                persist("context-handoff-required")
                raise Blocked(f"Context byte budget reached before dispatch; full decisions/progress retained at {log_path}. Resume with a fresh bounded attempt or refine the ticket; no unresolved inputs were dropped")
            remaining = max(1, int(expires - time.monotonic()))
            persist("provider-request-intent")
            try:
                response = self.transport.complete(route, history, schema(stage), remaining, **options)
            except BaseException:
                persist("provider-interrupted-usage-unknown")
                raise
            provider_sessions.append(response["session"])
            usage.append(response["usage"])
            persist("provider-response")
            calls = response["calls"]
            if not calls:
                result = response["result"]
                validate_payload(result, schema(stage))
                result.update({"session": session, "provider_sessions": provider_sessions, "usage": usage, "tools": transcript,
                               "transcript": str(log_path.relative_to(self.root))})
                persist("completed")
                return result
            if index >= self.config["runtime"]["max_tool_calls"]:
                raise CapabilityMismatch("Approved tool-call budget exhausted")
            if route == "cloud":
                history.extend(response["message"])
            else:
                history.append(response["message"])
            for call in calls:
                tool_calls_used += 1
                if tool_calls_used > self.config["runtime"]["max_tool_calls"]:
                    raise CapabilityMismatch("Approved tool-call budget exhausted")
                arguments = call.get("arguments")
                if not isinstance(arguments, dict):
                    raise CapabilityMismatch("Unknown/malformed broker tool call")
                if stage in {"preparation", "init"}:
                    if call.get("name") != "inspect_environment" or arguments:
                        raise Blocked("Preparation permits only read-only environment facts and supplied source text; arbitrary code/file authority requires an isolated fact-finding checkout")
                    evidence = inventory(self.config)
                else:
                    if call.get("name") != "run_command":
                        raise CapabilityMismatch("Unknown/malformed broker tool call")
                    from .config import command_for
                    command = command_for(arguments, "worker tool")
                    command["timeout"] = min(command["timeout"], max(1, int(expires - time.monotonic())))
                    evidence = self.sandbox.execute(command, worktree, self.root, protected, readonly=stage == "review", **options)
                transcript.append(evidence)
                persist("tool-completed")
                if route == "cloud":
                    history.append({"type": "function_call_output", "call_id": call["id"], "output": canonical(evidence)})
                else:
                    history.append({"role": "tool", "tool_name": call["name"], "content": canonical(evidence)})
        raise Blocked("Runtime tool loop exhausted")

    def probe(self, route, stage):
        """Explicitly consented real inference and actual container denial probes."""
        if type(self.transport) is not Transport or type(self.sandbox) is not Container:
            raise Blocked("Test adapters cannot produce real capability evidence")
        blockers = self.prerequisites(route, stage)
        if blockers:
            raise Blocked("; ".join(blockers))
        with tempfile.TemporaryDirectory(prefix="pattyline-capability-") as directory:
            worktree = Path(directory)
            (worktree / "approved.txt").write_text("immutable dummy input")
            denial = """import pathlib,socket,json
r={}
try:
 pathlib.Path('approved.txt').write_text('bad');r['protected_write_denied']=False
except OSError:r['protected_write_denied']=True
try:
 socket.create_connection(('1.1.1.1',443),timeout=1);r['network_denied']=False
except OSError:r['network_denied']=True
r['credentials_absent']=not any(k in __import__('os').environ for k in ('OPENAI_API_KEY','GH_TOKEN','GITHUB_TOKEN'))
print(json.dumps(r))
"""
            command = {"argv": [self.config["runtime"]["container_python"], "-I", "-c", denial], "timeout": 15, "cwd": "."}
            if stage in {"preparation", "init"}:
                evidence = {"outcome": "not-applicable", "reason": "Preparation has only a trusted read-only environment inventory tool, no project-code or filesystem authority"}
            else:
                evidence = self.sandbox.execute(command, worktree, self.root, ["approved.txt"])
                results = parse_content(evidence["stdout"])
                if evidence["exit_code"] or not results or not all(value is True for value in results.values()):
                    raise Blocked("Actual container protected-input/network/credential denial probe failed")
            prompt = {"task": "Use run_command once to inspect approved.txt (read-only), then return a valid result. For implementation, create result.txt with 'capability proof' and list it. For review, approve only when immutable dummy evidence is present, with acceptance_evidence mapping 'Dummy input exists.' to your actual evidence. For preparation return one settled decision and an interview.md artifact.",
                      "protected_paths": ["approved.txt"], "acceptance": ["Dummy input exists."]}
            if stage in {"preparation", "init"}:
                prompt["task"] = "Invoke inspect_environment once to establish actual facts; then return one settled decision, no open questions, an interview.md artifact, empty tickets, and verdict approved from the supplied dummy evidence."
            result = self.execute(stage, route, worktree, prompt, timeout=60, _probe=True)
            from .runtime import validate_result
            if stage not in {"preparation", "init"}:
                validate_result(result, stage)
                if not result["tools"]:
                    raise Blocked("Capability probe did not demonstrate actual model tool use")
            elif not result["tools"]:
                raise Blocked("Preparation probe did not demonstrate actual environment fact finding")
            if stage == "implementation" and not (worktree / "result.txt").exists():
                raise Blocked("Model probe failed to produce actual requested edit")
            evidence = {"time": now(), "origin": "live-broker-probe", "binding": self.binding(route, stage),
                        "route": route, "stage": stage, "container_denials": evidence, "result": result,
                        "limitations": ["Narrow capability probe; full Windows/Linux release scenarios remain required"]}
            key_path = self.root / ".pattyline/probe-key"
            key_path.parent.mkdir(exist_ok=True)
            if not key_path.exists():
                key_path.write_bytes(os.urandom(32))
            evidence["signature"] = hmac.new(key_path.read_bytes(), canonical(evidence).encode(), hashlib.sha256).hexdigest()
            write_json(self.root / f".pattyline/evidence/broker-{route}-{stage}.json", evidence)
            return {"profile": f"{route}:{stage}", "outcome": "narrow-capability-demonstrated", "release_ready": False}
