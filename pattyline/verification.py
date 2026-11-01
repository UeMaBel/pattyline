"""Execute ticket-defined checks in Pattyline, preserving honest baseline evidence."""
from pathlib import Path

from .common import Blocked, digest
from .config import command_for
from .git import assert_pin, head_tree
from .sandbox import protected_paths


class CheckFailed(Blocked):
    pass


def checks(root, worktree, ticket, config, sandbox, phase, event, **options):
    pin = head_tree(worktree)
    if pin["dirty"]:
        raise Blocked("Cannot verify a dirty tracked tree")
    results = []
    for declared in ticket.data["verification"]:
        name = declared["name"]
        command = command_for(config["checks"][name], name, config["runtime"].get("execution_platform"))
        identity = digest({"base": pin, "check": command, "setup": config["setup"], "phase": phase})
        if phase == "baseline" and declared["phase"] == "introduced":
            result = {"name": name, "phase": phase, "outcome": "not-yet-applicable", "identity": identity,
                      "reason": "Explicitly introduced by this ticket; must pass after implementation"}
        else:
            assert_pin(worktree, pin)
            evidence = sandbox.execute(command, worktree, root, protected_paths(root, ticket, config), **options)
            assert_pin(worktree, pin)
            result = {"name": name, "phase": phase, "identity": identity, **evidence}
            result["outcome"] = "passed" if evidence["exit_code"] == 0 and evidence["outcome"] == "exited" else "failed"
        event("check", result)
        results.append(result)
        if result["outcome"] == "failed":
            raise CheckFailed(f"{phase} check {name} failed; inspect persisted command/output evidence")
    assert_pin(worktree, pin)
    return results


def setup(root, worktree, ticket, config, sandbox, event, **options):
    before = head_tree(worktree)
    for step in config["setup"]:
        evidence = sandbox.execute(command_for(step, "setup", config["runtime"].get("execution_platform")), worktree, root,
                                   protected_paths(root, ticket, config), **options)
        event("setup", evidence)
        if evidence["exit_code"] or evidence["outcome"] != "exited":
            raise Blocked("Project setup failed; inspect evidence")
    assert_pin(worktree, before)
