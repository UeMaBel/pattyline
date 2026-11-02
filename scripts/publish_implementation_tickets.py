"""Publish the human-authorized replacement graph, preserving old draft identities.

This development-session operation is not a product approval bypass. It records
the explicit 2026-11-01 human instruction, not an agent's inferred authorization.
"""
from copy import deepcopy
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pattyline import approval, config
from pattyline.common import atomic_write, now
from pattyline.tickets import parse, order

CHECKS = {
    "foundation": ["tests.test_config", "tests.test_cli", "tests.test_package"],
    "tracker": ["tests.test_tickets"], "approval": ["tests.test_tickets"], "adoption": ["tests.test_tickets"],
    "cloud-onboarding": ["tests.test_onboarding"], "local-onboarding": ["tests.test_onboarding"],
    "preparation": ["tests.test_initialize"], "workbench": ["tests.test_state", "tests.test_engine"],
    "workers": ["tests.test_broker"], "review": ["tests.test_engine"],
    "local-worker": ["tests.test_broker"], "escalation": ["tests.test_engine"],
    "github": ["tests.test_github"], "scheduler": ["tests.test_engine"],
    "process": ["tests.test_process"], "recovery": ["tests.test_state", "tests.test_engine"],
    "reports": ["tests.test_cleanup"], "package": ["tests.test_package"],
}
PER_SLICE = ["foundation", "tracker", "approval", "adoption", "cloud-onboarding", "local-onboarding",
             "preparation", "preparation", "preparation", "workbench", "workers", "review", "local-worker",
             "escalation", "github", "scheduler", "process", "recovery", "reports", "package", "release-live", "release-live"]
OLD = [[1, 3, 18], [6], [8], [7], [2, 4], [2, 4], [5, 9], [5, 9], [5, 9], [10, 11], [2, 12],
       [13], [2, 12, 13], [13], [14], [15], [16], [7, 16], [17], [18, 19], [20], [21]]
IMPLEMENTATION = {
    1: "Implemented; Windows offline wheel/config/CLI checks passed; Linux installation evidence pending.",
    2: "Implemented parser/planner; deterministic graph and diagnostic tests.",
    3: "Implemented semantic approvals/drift detection; actual isolation enforced separately.",
    4: "Implemented idempotent draft adoption/history preservation; real consumer consent/access pending.",
    5: "Inventory/plans implemented; manual Codex/Linux installer routes remain; real install matrix pending.",
    6: "Loopback model inventory/consented download implemented; hardware-fit recommendation and live model evidence pending.",
    7: "Durable interview/session driver and bundled resources implemented; live cloud preparation gated.",
    8: "Fresh-session handoff/specification driver implemented; live cloud session evidence pending.",
    9: "Proposal/review-choice/publication/approval driver implemented; live preparation/repair evidence pending.",
    10: "Deterministic workbench implemented, including real Git worktrees/SQLite processes; native Linux evidence pending.",
    11: "Broker/Responses/container boundary implemented after failed native Codex network probe; live cloud/tool image evidence pending.",
    12: "Independent cloud review and bounded repair contract implemented; live review isolation/evidence pending.",
    13: "Ollama broker implemented; selected tool-capable model/hardware/context/local-inference evidence unavailable.",
    14: "Bounded approved escalation implemented and deterministically exercised; real local/cloud handoff pending.",
    15: "GitHub integration candidate implemented; strict freshness race proof and disposable authenticated repository unavailable; gate closed.",
    16: "Frozen dependency scheduler implemented; ordinary tests prove chain execution/blocked continuation, not live overnight acceptance.",
    17: "Deadlines/cancel/process identity/container termination implemented; narrow Windows child-process evidence passed; full cross-platform crash ownership pending.",
    18: "Conservative recovery/intent reconciliation implemented; uncertain ownership blocks; complete recovery scenarios pending.",
    19: "Durable reports and safe cleanup implemented and deterministically tested.",
    20: "Offline distribution/resource/entry-point checks implemented; complete real installed workflow blocked on runtime/integration gates.",
    21: "Blocked: complete real Windows/Linux local/cloud/GitHub release evidence unavailable.",
    22: "Blocked: actual website run consent/checkout and second consumer unavailable; snapshot is import-only fixture.",
}


def main():
    values = deepcopy(config.DEFAULTS)
    values["tickets"] = ["docs/pattyline/implementation-v1/issues/*.md"]
    values["instructions"] = ["GLOSSARY.md"] + [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "docs/adr").glob("*.md"))]
    for name, modules in CHECKS.items():
        values["checks"][name] = {"windows": {"argv": ["py", "-3.12", "-m", "unittest", *modules], "cwd": ".", "timeout": 180},
                                  "linux": {"argv": ["python3", "-m", "unittest", *modules], "cwd": ".", "timeout": 180}}
    values["checks"]["release-live"] = {"windows": {"argv": ["py", "-3.12", "scripts/check.py", "release-live"], "cwd": ".", "timeout": 30},
                                           "linux": {"argv": ["python3", "scripts/check.py", "release-live"], "cwd": ".", "timeout": 30}}
    config.save(ROOT, values)
    proposal = (ROOT / "docs/pattyline/v1/proposed-breakdown.md").read_text(encoding="utf-8")
    sections = list(re.finditer(r"^(\d+)\. \*\*(.+?)\*\*\s*\n(.*?)(?=^\d+\. \*\*|^## Check policy)", proposal, re.M | re.S))
    if len(sections) != 22:
        raise RuntimeError(f"Expected 22 approved slices, found {len(sections)}")
    destination = ROOT / "docs/pattyline/implementation-v1"
    index = ["# Approved Pattyline implementation queue", "", "Human authorized direct implementation on 2026-11-01. These 22 identities replace the original 21 drafts; no copied website work is dispatched.", "", "All checks introduced by a slice are not-yet-applicable on its base, never reported as passed. Existing failing baselines block. Post-implementation checks must pass.", "", "Status below is development progress, not verified GitHub integration or product release acceptance.", "", "| Slice | Dependencies | Superseded draft coverage | Progress / blockers |", "| --- | --- | --- | --- |"]
    tickets, seen_checks = {}, set()
    for section in sections:
        number, title, content = int(section[1]), section[2], section[3]
        dependencies_text = re.search(r"\*\*Blocked by:\*\* (.+)", content)[1]
        dependencies = [f"implementation-v1/{int(n):02}" for n in re.findall(r"\d+", dependencies_text)]
        outcome = re.search(r"\*\*What it delivers:\*\* (.+)", content)[1]
        criteria = re.findall(r"^   - (.+)$", content, re.M)
        check = PER_SLICE[number - 1]
        phase = "baseline" if check in seen_checks else "introduced"
        seen_checks.add(check)
        data = {"schema_version": 1, "id": f"implementation-v1/{number:02}", "type": "task", "status": "ready-for-agent",
                "llm_target": "cloud", "specification": "../../v1/spec.md", "dependencies": dependencies,
                "scope": ["pattyline/*", "tests/*", "scripts/*", "docs/pattyline/*", "README.md", "pyproject.toml", "build_backend.py", "LICENSE", ".gitignore"],
                "verification": [{"name": check, "phase": phase}], "cloud_escalation_allowed": False}
        path = destination / "issues" / f"{number:02}-slice.md"
        text = f"# {number:02}: {title}\n\n```toml\n{config.toml_text(data)}```\n\n## Scope\n\n{outcome}\n\n## Acceptance criteria\n\n"
        text += "\n".join(f"- [ ] {criterion}" for criterion in criteria)
        text += f"\n\n## Required evidence\n\nNamed check: `{check}` ({phase}). Real-platform/live requirements in acceptance remain mandatory; deterministic tests cannot replace them.\n\n## Comments\n\nHuman session authorization: approved revised 22-slice breakdown and direct implementation, 2026-11-01. Independent development review skipped for this session only. Product implementation review remains mandatory.\n\nSupersedes draft coverage: {', '.join(f'v1/{n:02}' for n in OLD[number-1])}. Old identities remain historical drafts.\n\nDevelopment progress: {IMPLEMENTATION[number]}\n"
        atomic_write(path, text)
        ticket = parse(path, ROOT, values)
        ticket.data["approval"] = {"schema": 1, "actor": "human: explicit development-session instruction 2026-11-01",
                                   "action": "human-approve", "timestamp": now(), "timestamp_kind": "recording time",
                                   "authorization_date": "2026-11-01", "fingerprint": approval.fingerprint(ROOT, ticket, values)}
        ticket.write()
        tickets[ticket.id] = ticket
        index.append(f"| [{number:02}: {title}](issues/{number:02}-slice.md) | {', '.join(dependencies) or 'None'} | {', '.join(f'v1/{n:02}' for n in OLD[number-1])} | {IMPLEMENTATION[number]} |")
    order(tickets)
    atomic_write(destination / "tickets.md", "\n".join(index) + "\n")
    print("Published 22 authorized replacement tickets; superseded drafts untouched. Production readiness remains blocked.")


if __name__ == "__main__":
    main()
