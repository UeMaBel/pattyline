"""Dependency inventory and consented installation plans; never install on import."""
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import re

from .common import Blocked, now, write_json
from .process import run
from .runtime import inspect

SOURCES = {
    "git": "https://git-scm.com/downloads",
    "gh": "https://cli.github.com/",
    "codex": "https://developers.openai.com/codex/cli/",
    "ollama": "https://docs.ollama.com/download",
    "docker": "https://docs.docker.com/get-started/get-docker/",
}
WINGET = {"git": "Git.Git", "gh": "GitHub.cli", "ollama": "Ollama.Ollama"}


def executable(name, configured=None):
    found = shutil.which(configured or name)
    if found:
        return found
    if os.name == "nt" and name == "ollama":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def inventory(config):
    tools = {}
    for name in SOURCES:
        configured = config["runtime"].get(name)
        path = executable(name, configured)
        version = None
        if path:
            try:
                completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                version = (completed.stdout or completed.stderr).strip()[:300]
            except (OSError, subprocess.TimeoutExpired):
                version = "version query unavailable"
        tools[name] = {"path": path, "version": version, "source": SOURCES[name],
                       "presence": "installed" if path else "missing",
                       "authentication": "not checked", "capability": "not demonstrated"}
    return {"platform": platform.platform(), "tools": tools, "python": platform.python_version(),
            "disk_free_bytes": shutil.disk_usage(Path.cwd()).free,
            "hardware": hardware()}


def hardware():
    memory = None
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                        ("total_pagefile", ctypes.c_ulonglong), ("available_pagefile", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory = {"total_bytes": status.total_physical, "available_bytes": status.available_physical}
    else:
        try:
            memory = {"total_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"),
                      "available_bytes": os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")}
        except (ValueError, OSError):
            pass
    gpu = "unknown; GPU memory/driver availability requires inspection"
    if shutil.which("nvidia-smi"):
        try:
            completed = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            if completed.returncode == 0:
                gpu = {"reported_name_total_free_MiB": completed.stdout.strip()}
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"ram": memory or "unknown", "gpu": gpu, "model_fit": "not demonstrated; confirm publisher weights, context/KV cache requirements and actual tool probe before recommending/downloading"}


def installation_plan(component, config, version=None):
    if component not in SOURCES:
        raise Blocked(f"Unknown component {component}")
    installed = executable(component, config["runtime"].get(component))
    if installed:
        raise Blocked(f"{component} already installed at {installed}; no automatic replacement/upgrades")
    version = version or (config["runtime"]["version"] if component == "codex" else None)
    command = None
    privilege = "manual install; source documents privileges"
    destination = "installer-managed; inspect installer before consenting"
    if os.name == "nt" and component in WINGET and version and shutil.which("winget"):
        command = ["winget", "install", "--id", WINGET[component], "--exact", "--version", version, "--source", "winget"]
        privilege = "installer may require elevation; Pattyline does not elevate automatically"
    elif os.name != "nt" and component in {"git", "gh", "docker"} and version and shutil.which("apt-get") and shutil.which("apt-cache"):
        package = "docker.io" if component == "docker" else component
        policy = subprocess.run(["apt-cache", "policy", package], capture_output=True, text=True, timeout=10)
        if policy.returncode == 0 and re.search(r"^\s*(?:\*\*\*\s+)?" + re.escape(version) + r"\s+\d+\s*$", policy.stdout, re.M):
            command = ["apt-get", "install", "--yes", f"{package}={version}"]
            if os.geteuid() != 0:
                if shutil.which("sudo"):
                    command = ["sudo", "--non-interactive", *command]
                else:
                    command = None
            privilege = "root required for this step only; sudo must already be authorized or the human must run the plan manually"
            destination = "system package-manager paths"
            source_urls = re.findall(r"^\s+\d+\s+(https?://\S+)", policy.stdout, re.M)
            safe_urls = []
            for url in source_urls:
                parsed = urllib.parse.urlsplit(url)
                safe_urls.append(urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", "")))
            source = ", ".join(safe_urls) or "configured local apt package index; inspect apt-cache policy before consent"
        else:
            source = SOURCES[component]
    else:
        source = SOURCES[component]
    return {"component": component, "version": version or "unspecified: choose compatible pinned version",
            "source": source if os.name != "nt" else SOURCES[component], "destination": destination, "privileges": privilege,
            "argv": command, "status": "consent required"}


def install(root, plan, consent=False):
    if not consent:
        raise Blocked("Installation requires explicit consent after reviewing its plan")
    if not plan["argv"]:
        raise Blocked(f"Manual installation needed: {plan['source']}; no verified unattended installer for this component/platform/version")
    record = {"time": now(), "plan": plan, "state": "installing"}
    path = Path(root) / ".pattyline/evidence" / f"install-{plan['component']}.json"
    write_json(path, record)
    try:
        result = run(plan["argv"], root, 900)
        record.update({"state": "installed-needs-validation" if result["exit_code"] == 0 else "interrupted-or-failed",
                       "exit_code": result["exit_code"], "outcome": result["outcome"]})
        write_json(path, record)
        if result["exit_code"]:
            raise Blocked("Installation failed/interrupted; inspect installation and rerun setup inventory")
    except BaseException:
        record["state"] = "interrupted-or-failed"
        write_json(path, record)
        raise
    return record


def ollama_api(config, endpoint, data=None, timeout=10):
    base = config["runtime"]["ollama_url"]
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise Blocked("Local Ollama requires an HTTP loopback endpoint without credentials/path; remote inference is not a local route")
    request = urllib.request.Request(base.rstrip("/") + endpoint,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
    # Loopback probes bypass environment proxies so 'localhost' cannot route externally.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError) as error:
        raise Blocked(f"Ollama service unavailable at loopback endpoint: {type(error).__name__}; start Ollama and retry") from error


def local_inventory(config):
    tags = ollama_api(config, "/api/tags")
    model = config["runtime"]["local_model"]
    found = next((item for item in tags.get("models", []) if item.get("name") == model), None)
    result = {"models": tags.get("models", []), "selected": model, "selected_installed": bool(found),
              "hardware_fit": "not demonstrated", "local_inference": "not demonstrated"}
    if found:
        details = ollama_api(config, "/api/show", {"model": model})
        if details.get("remote_host") or details.get("remote_model") or "cloud" in model.lower():
            raise Blocked("Selected model may delegate to a remote service; local routing is blocked")
        result["details"] = details
        result["storage_bytes"] = found.get("size", "unknown")
        result["context"] = {k: v for k, v in details.get("model_info", {}).items() if k.endswith("context_length")}
    return result


def model_plan(config, name):
    if not name or "cloud" in name.lower() or "/" in name or any(c.isspace() for c in name):
        raise Blocked("Select an explicit local Ollama model name")
    return {"model": name, "provider": "loopback Ollama", "download_bytes": "unknown; registry metadata unavailable",
            "storage_required": "unknown; inspect model publisher before download", "hardware_fit": "unknown",
            "source": "Ollama model registry through the local Ollama service", "consent_required": True}


def download_model(root, config, name, consent=False):
    plan = model_plan(config, name)
    if not consent:
        raise Blocked("Model download requires explicit consent including unknown size/hardware fit")
    path = Path(root) / ".pattyline/evidence/model-download.json"
    write_json(path, {"time": now(), "plan": plan, "state": "downloading"})
    try:
        result = ollama_api(config, "/api/pull", {"model": name, "stream": False}, timeout=1800)
        if result.get("status") != "success":
            raise Blocked("Ollama did not confirm completed model download")
    except BaseException:
        write_json(path, {"time": now(), "plan": plan, "state": "interrupted-or-failed"})
        raise
    write_json(path, {"time": now(), "plan": plan, "state": "downloaded-not-yet-ready"})
    return {"status": "downloaded; configure selected model and run capability probes before execution"}
