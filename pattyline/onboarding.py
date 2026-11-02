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
SHARED_COMPONENTS = ("git", "gh", "codex")
VERSION_SOURCES = {
    "git": "https://git-scm.com/downloads",
    "gh": "https://api.github.com/repos/cli/cli/releases/latest",
    "codex": "https://registry.npmjs.org/@openai%2fcodex/latest",
}


def executable(name, configured=None):
    found = shutil.which(configured or name)
    if found:
        return found
    if os.name == "nt" and name == "ollama":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def latest_version(component):
    """Read a version from the tool publisher's public release metadata."""
    if component not in VERSION_SOURCES:
        return None
    request = urllib.request.Request(VERSION_SOURCES[component], headers={"User-Agent": "Pattyline setup"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            content = response.read(256 * 1024).decode("utf-8", errors="replace")
        if component == "gh":
            version = json.loads(content).get("tag_name", "")
        elif component == "codex":
            version = json.loads(content).get("version", "")
        else:
            match = re.search(r"Latest version:\s*([0-9]+(?:\.[0-9]+)+)", content, re.I)
            version = match.group(1) if match else ""
        return normalized_version(version)
    except (OSError, TimeoutError, ValueError, urllib.error.URLError):
        return None


def normalized_version(value):
    match = re.search(r"(?<![0-9])v?([0-9]+(?:\.[0-9]+)+)", str(value or ""), re.I)
    return match.group(1) if match else None


def version_key(value):
    version = normalized_version(value)
    return tuple(int(part) for part in version.split(".")) + (0,) * (4 - len(version.split("."))) if version else None


def tool_inventory(config, components=SHARED_COMPONENTS):
    tools = {}
    for name in components:
        configured = config["runtime"].get(name)
        path = executable(name, configured)
        version = None
        if path:
            try:
                completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                version = (completed.stdout or completed.stderr).strip()[:300]
            except (OSError, subprocess.TimeoutExpired):
                version = "version query unavailable"
        available = latest_version(name)
        current_key, available_key = version_key(version), version_key(available)
        outdated = bool(path and current_key and available_key and current_key < available_key)
        tools[name] = {"path": path, "version": version, "latest_version": available, "source": SOURCES[name],
                       "presence": "installed" if path else "missing",
                       "update": "outdated" if outdated else "current" if path and available else "unknown" if path else "not-installed",
                       "authentication": "not checked", "capability": "not demonstrated"}
    return {"platform": platform.platform(), "tools": tools}


def codex_auth_status(config):
    """Ask Codex whether its own credential store has a ChatGPT session.

    Never read Codex's credential/config files; their contents remain owned by Codex.
    """
    path = executable("codex", config["runtime"].get("codex"))
    if not path:
        return {"status": "codex-missing"}
    try:
        completed = subprocess.run([path, "login", "status"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unknown"}
    # Codex currently reports this status on stderr. Do not return or persist raw
    # output; it is only used to distinguish ChatGPT from other login methods.
    message = (completed.stdout + "\n" + completed.stderr).casefold()
    if completed.returncode == 0 and "logged in using chatgpt" in message:
        return {"status": "authenticated"}
    if "not logged in" in message:
        return {"status": "not-authenticated"}
    if completed.returncode == 0 and "logged in using" in message:
        return {"status": "other-authentication"}
    return {"status": "unknown"}


def codex_login(config, cwd):
    """Start Codex's native interactive login and return only its outcome."""
    path = executable("codex", config["runtime"].get("codex"))
    if not path:
        return {"status": "codex-missing"}
    try:
        completed = subprocess.run([path, "login"], cwd=cwd, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "incomplete"}
    except KeyboardInterrupt:
        return {"status": "interrupted"}
    except OSError:
        return {"status": "failed"}
    return {"status": "completed" if completed.returncode == 0 else "failed"}


def inventory(config):
    """Full runtime inventory retained for worker probes; setup uses tool_inventory."""
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


def _installed_version(path):
    if not path:
        return None
    try:
        completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        return (completed.stdout or completed.stderr).strip()[:300]
    except (OSError, subprocess.TimeoutExpired):
        return "version query unavailable"


def installation_plan(component, config, version=None, upgrade=False):
    if component not in SHARED_COMPONENTS:
        raise Blocked(f"Unsupported shared tool {component}; choose git, gh, or codex")
    installed = executable(component, config["runtime"].get(component))
    if installed and not upgrade:
        raise Blocked(f"{component} already installed at {installed}; compatible tools are reused")
    version = normalized_version(version) or latest_version(component)
    if not version and component == "codex":
        version = normalized_version(config["runtime"].get("version"))
    command = None
    privilege = "manual installation; consult the official instructions for platform-specific privileges"
    if component == "codex" and version and shutil.which("npm"):
        command = [shutil.which("npm"), "install", "--global", f"@openai/codex@{version}"]
        privilege = "npm may require write access to its global package directory"
    elif component == "gh" and os.name == "nt" and version and shutil.which("winget"):
        verb = "upgrade" if installed else "install"
        command = ["winget", verb, "--id", WINGET[component], "--exact", "--version", version, "--source", "winget"]
        privilege = "winget may request elevation; Pattyline does not elevate automatically"
    return {"component": component, "action": "upgrade" if installed else "install",
            "installed_version": _installed_version(installed),
            "version": version or "unavailable; select a version from the official source",
            "source": SOURCES[component], "destination": "managed by the selected official installer",
            "privileges": privilege, "argv": command,
            "status": "consent required" if command else "manual installation required"}


def install(root, plan, consent=False):
    if not consent:
        raise Blocked("Installation requires explicit consent after reviewing its plan")
    if not plan["argv"]:
        raise Blocked(f"Manual installation needed: {plan['source']}; no verified unattended installer for this component/platform/version")
    record = {"time": now(), "plan": plan, "state": "installing"}
    try:
        result = run(plan["argv"], root, 900)
        record.update({"state": "installed-needs-validation" if result["exit_code"] == 0 else "interrupted-or-failed",
                       "exit_code": result["exit_code"], "outcome": result["outcome"]})
        if result["exit_code"]:
            raise Blocked("Installation incomplete (failed or interrupted); inspect the tool and rerun setup inventory")
        found = executable(plan["component"])
        if not found:
            record["state"] = "incomplete"
            raise Blocked("Installer exited successfully, but the tool is not available on PATH; setup remains incomplete")
        installed_version = _installed_version(found)
        target_version = normalized_version(plan.get("version"))
        actual_version = normalized_version(installed_version)
        if not target_version or not actual_version or actual_version != target_version:
            record.update({"state": "installed-needs-validation", "path": found, "version": installed_version})
            return record
        record.update({"state": "installed", "path": found, "version": installed_version})
    except BaseException:
        record["state"] = "interrupted-or-failed"
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
