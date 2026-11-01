"""Deterministic adapters are injectable test fixtures, never selectable by the CLI."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from pattyline import approval, config
from pattyline.common import atomic_write
from pattyline.git import git
from pattyline.tickets import parse


class Project:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.remote = Path(self.temp.name) / "remote.git"
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Fixture")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "remote", "add", "origin", str(self.remote))
        self.config = config.initialize(self.root)
        self.config["tickets"] = ["docs/feature/issues/*.md"]
        self.config["runtime"]["cloud_model"] = "test-cloud"
        self.config["runtime"]["local_model"] = "test-local"
        self.config["checks"] = {"unit": {"argv": ["test-fixture-check"], "cwd": ".", "timeout": 10}}
        config.save(self.root, self.config)
        atomic_write(self.root / "docs/feature/spec.md", "# Specification\n\nProduce the approved output.\n")
        self.add("01")

    def add(self, number, dependencies=(), route="cloud", phase="introduced"):
        data = {"schema_version": 1, "id": f"feature/{number}", "type": "task", "status": "draft",
                "llm_target": route, "specification": "../spec.md", "dependencies": list(dependencies),
                "verification": [{"name": "unit", "phase": phase}], "scope": [f"output-{number}.txt"],
                "cloud_escalation_allowed": False}
        path = self.root / f"docs/feature/issues/{number}-task.md"
        atomic_write(path, f"# {number}: Output\n\n```toml\n{config.toml_text(data)}```\n\n## Acceptance criteria\n\n- [ ] Approved output exists.\n\n## Comments\n\nFixture.\n")
        return parse(path, self.root, self.config)

    def approve_commit(self):
        from pattyline.tickets import discover
        tickets = discover(self.root, self.config)
        approval.approve(self.root, list(tickets.values()), self.config, "fixture human")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Approved fixture inputs")
        git(self.root, "push", "origin", "main")
        return tickets

    def close(self):
        self.temp.cleanup()


class FakeSandbox:
    def __init__(self, failures=0, mutate=None):
        self.failures, self.mutate, self.calls = failures, mutate, []

    def readiness(self):
        return []

    def execute(self, command, worktree, primary, protected=(), **options):
        self.calls.append({"command": command, "worktree": str(worktree), "protected": protected, "readonly": options.get("readonly", False)})
        if self.mutate:
            self.mutate(Path(worktree))
        failed = self.failures > 0
        self.failures -= int(failed)
        return {"argv": command["argv"], "cwd": str(worktree), "exit_code": int(failed),
                "stdout": "fixture check", "stderr": "", "outcome": "exited"}


class FakeRuntime:
    def __init__(self, verdicts=(), on_call=None):
        self.verdicts, self.calls, self.on_call = list(verdicts), [], on_call

    def readiness(self, route="cloud", stage="implementation"):
        return []

    def execute(self, stage, route, worktree, prompt, **options):
        self.calls.append((stage, route))
        if self.on_call:
            self.on_call(stage, route, worktree, prompt)
        session = uuid.uuid4().hex
        if stage == "implementation":
            metadata = prompt["ticket"]["ticket"]["metadata"]
            number = metadata["id"].split("/")[1]
            path = Path(worktree) / f"output-{number}.txt"
            path.write_text("approved output " + session, encoding="utf-8")
            return {"session": session, "summary": "Wrote approved output", "files": [path.name], "limitations": []}
        if stage == "review":
            verdict = self.verdicts.pop(0) if self.verdicts else "approved"
            return {"session": session, "verdict": verdict, "findings": ["Repair output"] if verdict != "approved" else [],
                    "acceptance_evidence": {"Approved output exists.": "fixture diff/check"}}
        raise AssertionError(stage)


class FakeGitHub:
    """Stateful local-remote integration, including uncertain result reconciliation."""
    def __init__(self, root, config, interrupt_once=False):
        self.root, self.config, self.calls = root, config, []
        self.interrupt_once = interrupt_once

    def readiness(self):
        return []

    def integrate(self, ticket, worktree, branch, pin, base, operation, complete, cancelled, deadline):
        self.calls.append(ticket.id)
        intent = operation("merge", {"head": pin["head"]})
        current = git(self.root, "ls-remote", "origin", "refs/heads/main").split()[0]
        if current != pin["head"]:
            git(worktree, "push", "origin", f"{pin['head']}:refs/heads/main")
        if self.interrupt_once:
            self.interrupt_once = False
            from pattyline.common import Blocked
            raise Blocked("Integration result uncertain; reconcile remote before retry")
        git(self.root, "fetch", "origin", "main")
        assert git(self.root, "merge-base", "--is-ancestor", pin["head"], "origin/main", check=False).returncode == 0
        result = {"outcome": "integrated", "reviewed": pin["head"], "merge": pin["head"], "pr": "fixture-only"}
        complete(intent["id"], result)
        return result
