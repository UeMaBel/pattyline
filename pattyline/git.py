"""Orchestrator-owned Git operations. Never enable project hooks or force pushes."""
from pathlib import Path
import fnmatch
import os
import subprocess

from .common import Blocked, within


class TreeChanged(Blocked):
    pass


def git(root, *arguments, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                             "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=always", *map(str, arguments)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise Blocked(f"Git {arguments[0]} failed: {result.stderr.strip()[:800]}")
    return result.stdout.strip() if check else result


def primary(path):
    path = Path(path).resolve()
    result = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    if result.returncode:
        return path
    common = Path(result.stdout.strip()).resolve()
    return common.parent


def committed_inputs(root, tickets, config):
    from .approval import instruction_paths
    paths = {"pattyline.toml", *config["instructions"]}
    for ticket in tickets:
        paths.update(instruction_paths(root, ticket, config))
        paths.add(ticket.path.relative_to(root).as_posix())
        paths.add(within(root, ticket.path.parent / ticket.data["specification"]).relative_to(root).as_posix())
    git(root, "rev-parse", "HEAD")
    for path in paths:
        result = git(root, "ls-files", "--error-unmatch", "--", path, check=False)
        if result.returncode or git(root, "status", "--porcelain", "--", path):
            raise Blocked(f"Commit approved input {path} before starting; Pattyline will not stage primary-checkout files")


def fetched_base(root, config):
    git(root, "fetch", "--no-tags", config["remote"], config["base"])
    return git(root, "rev-parse", f"refs/remotes/{config['remote']}/{config['base']}")


def worktree(root, config, branch, base):
    directory = (Path(root) / config["worktrees"]).resolve()
    if directory == Path(root).resolve() or directory.is_relative_to(Path(root).resolve()):
        raise Blocked("Worktrees must be outside primary checkout")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / branch.replace("/", "-")
    git(root, "worktree", "add", "-b", branch, str(target), base)
    return target


def head_tree(root):
    return {"head": git(root, "rev-parse", "HEAD"), "tree": git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": git(root, "status", "--porcelain", "--untracked-files=no")}


def assert_pin(root, pin):
    if head_tree(root) != pin or pin["dirty"]:
        raise TreeChanged("HEAD/tracked tree changed after verification; commit, verify and review again")


def scoped_commit(root, ticket, protected=(), reported=None):
    # -z avoids ambiguous filenames, embedded newlines and Git's display quoting.
    filters = git(root, "config", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$", check=False)
    if filters.stdout.strip():
        raise Blocked("Host Git content filters are unsupported; isolate/reconcile filters before scoped staging")
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                            cwd=root, capture_output=True, check=True)
    entries = result.stdout.decode("utf-8").split("\0")
    paths = []
    for entry in entries:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            raise Blocked("Renames/copies require explicit reconciliation; use add/delete within scope")
        within(root, path)
        if Path(path).is_symlink():
            raise Blocked(f"Symlink change requires reconciliation: {path}")
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in ticket.data["scope"]):
            raise Blocked(f"Out-of-scope change: {path}")
        if path == "pattyline.toml" or path.startswith(".pattyline/") or path == ".git":
            raise Blocked(f"Protected file changed: {path}")
        if any(path == p or path.startswith(p.rstrip("/") + "/") for p in protected):
            raise Blocked(f"Approved requirement/instruction changed: {path}")
        paths.append(path)
    if not paths:
        raise Blocked("Worker produced no implementation changes")
    if reported is not None and (len(set(reported)) != len(reported) or set(reported) != set(paths)):
        raise Blocked("Structured changed-file list differs from actual scoped diff; reconcile before staging")
    git(root, "add", "--", *paths)
    git(root, "commit", "-m", f"{ticket.id}: implement approved scope")
    return head_tree(root)
