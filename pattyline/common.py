"""Small shared data and filesystem operations; no subprocess authority."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import re


class Blocked(Exception):
    """An actionable prerequisite, never a successful partial operation."""


class CapabilityMismatch(Blocked):
    """Demonstrated model tool/result failure, eligible for approved escalation."""


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pattyline-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def within(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise Blocked(f"Path escapes project: {relative}")
    return path


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def redact(value):
    """Remove known orchestrator secrets from persisted worker/provider output."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for key, secret in os.environ.items():
        if len(secret) >= 6 and re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", key, re.I):
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,})\b", "[REDACTED]", value)
    return value
