"""Clean only registered, inactive, verified integrated worktrees."""
from pathlib import Path

from .common import Blocked
from .git import git
from .state import State


def clean(root, config, apply=False):
    state = State(root)
    candidates = []
    allowed = (Path(root) / config["worktrees"]).resolve()
    registered = git(root, "worktree", "list", "--porcelain")
    registered_paths = {str(Path(line[9:]).resolve()) for line in registered.splitlines() if line.startswith("worktree ")}
    for row in state.db.execute("SELECT * FROM work WHERE worktree IS NOT NULL"):
        path = Path(row["worktree"]).resolve()
        reason = None
        if not path.is_relative_to(allowed) or path == allowed or path == Path(root).resolve():
            reason = "outside configured worktree directory"
        elif row["owner"] or row["process"]:
            reason = "active or uncertain ownership"
        elif row["state"] != "integrated" or not row["integration"]:
            reason = "not verified integrated; unfinished work preserved"
        elif str(path) not in registered_paths:
            reason = "not a registered worktree; no filesystem deletion"
        elif git(path, "status", "--porcelain"):
            reason = "uncommitted or untracked changes"
        candidates.append({"ticket": row["ticket"], "path": str(path), "action": "preserve" if reason else "remove worktree", "reason": reason})
    if apply:
        for item in candidates:
            if item["action"] == "remove worktree":
                # Git performs a second dirty check. Never use --force or recursive deletion.
                git(root, "worktree", "remove", item["path"])
    state.close()
    return candidates
