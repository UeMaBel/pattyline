"""Remove Pattyline project artifacts and only safe registered worktrees."""
from pathlib import Path
import shutil

from .common import Blocked, atomic_write, load_json
from .git import git
from .state import State


def clean(root, config, apply=False, all_artifacts=False):
    root = Path(root).resolve()
    state_dir = root / ".pattyline"
    if state_dir.is_symlink():
        raise Blocked("Refusing to clean through a .pattyline symlink")
    if state_dir.exists() and not state_dir.is_dir():
        raise Blocked("Refusing to clean: .pattyline exists but is not a directory")
    state = State(root) if state_dir.exists() else None
    candidates = []
    allowed = (root / config["worktrees"]).resolve()
    registered = git(root, "worktree", "list", "--porcelain") if state else ""
    registered_paths = {str(Path(line[9:]).resolve()) for line in registered.splitlines() if line.startswith("worktree ")}
    work_rows = state.db.execute("SELECT * FROM work WHERE worktree IS NOT NULL") if state else ()
    for row in work_rows:
        path = Path(row["worktree"]).resolve()
        reason = None
        if not path.is_relative_to(allowed) or path == allowed or path == root:
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
    artifacts = []
    project_config = root / "pattyline.toml"
    gitignore = root / ".gitignore"
    if state_dir.exists():
        artifacts.append({"path": str(state_dir), "action": "remove Pattyline state, reports, and evidence"})
    preparation_dirs = []
    if all_artifacts:
        # Only remove feature directories carrying Pattyline's own preparation
        # manifest. Other documentation and ticket directories may be user data.
        base = root / "docs/pattyline"
        if base.is_dir() and not base.is_symlink():
            for manifest in sorted(base.glob("*/preparation.json")):
                directory = manifest.parent
                if manifest.is_symlink() or directory.is_symlink():
                    continue
                value = load_json(manifest, {})
                if value.get("feature") != directory.name:
                    continue
                preparation_dirs.append(directory)
                artifacts.append({"path": str(directory), "action": "remove init preparation files, Markdown drafts, and issues"})
    if project_config.exists():
        artifacts.append({"path": str(project_config), "action": "remove Pattyline project configuration"})
    ignore_entry = False
    if gitignore.is_file():
        lines = gitignore.read_text(encoding="utf-8").splitlines(keepends=True)
        ignore_entry = any(line.strip() in {".pattyline/", ".pattyline"} for line in lines)
        if ignore_entry:
            artifacts.append({"path": str(gitignore), "action": "remove only the .pattyline ignore entry"})

    blockers = []
    if ignore_entry and gitignore.is_symlink():
        blockers.append(".gitignore is a symlink; refusing to replace it")
    if state and state.db.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
        blockers.append("an execution run is still marked running")
    unfinished = state.db.execute(
        "SELECT ticket,state FROM work WHERE state!='integrated' OR owner IS NOT NULL OR process IS NOT NULL"
    ).fetchall() if state else []
    if unfinished:
        blockers.append("unfinished or uncertain ticket work must be preserved: " + ", ".join(row["ticket"] for row in unfinished))

    if apply and artifacts and blockers:
        if state:
            state.close()
        raise Blocked("Cannot remove Pattyline project data: " + "; ".join(blockers))

    if apply:
        for item in candidates:
            if item["action"] == "remove worktree":
                # Git performs a second dirty check. Never use --force or recursive deletion.
                git(root, "worktree", "remove", item["path"])
        if state:
            state.close()
        if state_dir.exists():
            shutil.rmtree(state_dir)
        for directory in preparation_dirs:
            if directory.exists() and not directory.is_symlink():
                shutil.rmtree(directory)
        if project_config.exists() or project_config.is_symlink():
            project_config.unlink()
        if ignore_entry:
            lines = gitignore.read_text(encoding="utf-8").splitlines(keepends=True)
            kept = [line for line in lines if line.strip() not in {".pattyline/", ".pattyline"}]
            atomic_write(gitignore, "".join(kept))
        return {"worktrees": candidates, "artifacts": artifacts, "removed": True}
    if state:
        state.close()
    return {"worktrees": candidates, "artifacts": artifacts, "blocked": blockers}
