"""Human approvals bind effective semantic inputs, never worker-reported success."""
import getpass
from pathlib import Path
import os

from .common import Blocked, digest, now, within

SCHEMA = 1


def instruction_paths(root, ticket, config):
    root = Path(root).resolve()
    result = set(config["instructions"])
    if (root / "AGENTS.md").exists():
        result.add("AGENTS.md")
    for pattern in ticket.data["scope"]:
        prefix = pattern
        for token in ("*", "?", "["):
            prefix = prefix.split(token, 1)[0]
        base = within(root, prefix if prefix.endswith("/") else str(Path(prefix).parent))
        current = base
        while current.is_relative_to(root):
            candidate = current / "AGENTS.md"
            if candidate.is_file():
                result.add(candidate.relative_to(root).as_posix())
            if current == root:
                break
            current = current.parent
        if any(token in pattern for token in ("*", "?", "[")) and base.is_dir():
            for directory, children, filenames in os.walk(base, followlinks=False):
                children[:] = [name for name in children if name not in {".git", ".pattyline", ".venv", "node_modules", "__pycache__"}]
                if "AGENTS.md" in filenames:
                    candidate = within(root, Path(directory) / "AGENTS.md")
                    result.add(candidate.relative_to(root).as_posix())
    return sorted(result)


def effective(root, ticket, config):
    spec = within(root, ticket.path.parent / ticket.data["specification"])
    instructions = {p: within(root, p).read_text(encoding="utf-8") for p in instruction_paths(root, ticket, config)}
    return {"schema": SCHEMA, "ticket": ticket.requirements(),
            "specification": spec.read_text(encoding="utf-8"), "instructions": instructions,
            "config": config}


def fingerprint(root, ticket, config):
    return digest(effective(root, ticket, config))


def reason(root, ticket, config):
    approval = ticket.data.get("approval", {})
    if ticket.data["status"] != "ready-for-agent":
        return "not ready-for-agent; refine and approve"
    if approval.get("schema") != SCHEMA or not approval.get("actor") or approval.get("action") != "human-approve" or not approval.get("timestamp"):
        return "missing human approval evidence"
    if approval.get("fingerprint") != fingerprint(root, ticket, config):
        return "approved requirements drifted; reconcile and approve a successor revision"
    return None


def approve(root, tickets, config, actor=None):
    if os.environ.get("PATTYLINE_WORKER") or os.environ.get("CODEX_THREAD_ID"):
        raise Blocked("Worker sessions have no approval authority; run approve from a human terminal")
    for ticket in tickets:
        ticket.data["status"] = "ready-for-agent"
        ticket.data["approval"] = {"schema": SCHEMA, "actor": actor or getpass.getuser(),
                                   "action": "human-approve", "timestamp": now(),
                                   "fingerprint": fingerprint(root, ticket, config)}
        ticket.write()
    return [ticket.id for ticket in tickets]


def attest(root, ticket, config, state, requirement, evidence, actor=None):
    if os.environ.get("PATTYLINE_WORKER") or os.environ.get("CODEX_THREAD_ID"):
        raise Blocked("Worker sessions cannot attest human/external acceptance evidence")
    if reason(root, ticket, config):
        raise Blocked("Current requirements must be human-approved before acceptance attestation")
    if requirement not in {item["name"] for item in ticket.data.get("external_verification", [])}:
        raise Blocked("Unknown external/human verification requirement")
    work = state.get_work(ticket.id)
    if not work or not work["pin"]:
        raise Blocked("Implement and verify a scoped commit before attesting acceptance")
    import json
    path = within(root, evidence)
    if not path.is_file():
        raise Blocked("External evidence file must exist inside this project")
    value = {"name": requirement, "actor": actor or getpass.getuser(), "action": "human-attest",
             "timestamp": now(), "commit": json.loads(work["pin"])["head"],
             "fingerprint": fingerprint(root, ticket, config), "path": path.relative_to(root).as_posix(),
             "evidence_hash": digest(path.read_bytes().hex())}
    state.event(work["run"], ticket.id, "human-evidence", value)
    return value


def external_evidence(root, ticket, config, state, pin):
    import json
    rows = [json.loads(row[0]) for row in state.db.execute("SELECT evidence FROM events WHERE ticket=? AND kind='human-evidence' ORDER BY id DESC", (ticket.id,))]
    result = {}
    for declared in ticket.data.get("external_verification", []):
        evidence = next((row for row in rows if row.get("name") == declared["name"] and row.get("commit") == pin["head"] and row.get("fingerprint") == fingerprint(root, ticket, config) and row.get("actor") and row.get("action") == "human-attest"), None)
        if not evidence:
            raise Blocked(f"Acceptance {declared['name']} requires human/external evidence for commit {pin['head']}; use approve --attest with an evidence file")
        path = within(root, evidence["path"])
        if not path.is_file() or digest(path.read_bytes().hex()) != evidence["evidence_hash"]:
            raise Blocked(f"Acceptance evidence {declared['name']} changed or disappeared; re-attest it")
        result[declared["name"]] = {**declared, **evidence}
    return result
