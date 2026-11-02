"""GitHub CLI integration; normal merges require server-enforced freshness."""
import json
import shutil
import subprocess
import time

from .common import Blocked
from .git import assert_pin, fetched_base, git


class GitHub:
    def __init__(self, root, config):
        self.root, self.config = root, config

    def gh(self, *args, check=True):
        if not shutil.which("gh"):
            raise Blocked("Install GitHub CLI (gh), then gh auth login")
        result = subprocess.run(["gh", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if check and result.returncode:
            raise Blocked("GitHub operation unavailable; check gh authentication/repository permissions (sensitive output withheld)")
        return result

    def api(self, endpoint, *args):
        return json.loads(self.gh("api", endpoint, *args).stdout)

    def readiness(self):
        if not self.config["repository"]:
            return ["Configure repository as owner/name"]
        if not shutil.which("gh"):
            return ["Install GitHub CLI and authenticate with gh auth login"]
        try:
            remote = git(self.root, "remote", "get-url", self.config["remote"])
            expected = self.config["repository"].lower().removesuffix(".git")
            actual = remote.removeprefix("https://github.com/").removeprefix("git@github.com:").removesuffix(".git").lower()
            if actual != expected:
                return ["Configured Git remote destination does not match approved GitHub repository identity"]
            self.gh("auth", "status")
            repo = self.api(f"repos/{self.config['repository']}")
            if not repo.get("allow_merge_commit"):
                return ["Repository must permit normal merge commits; squash-only integration is unsupported"]
            # Protection API describes a candidate strict mechanism. It does not prove all
            # ruleset/race combinations; readiness remains closed until a live race probe.
            protection = self.api(f"repos/{self.config['repository']}/branches/{self.config['base']}/protection")
            required = protection.get("required_status_checks") or {}
            if not required.get("strict") or not (required.get("contexts") or required.get("checks")):
                return ["Base requires strict nonempty status checks to enforce freshness atomically; preview policy changes with an administrator"]
            return ["GitHub strict-rule freshness race capability has not been demonstrated in a consented disposable repository; integration remains gated"]
        except (Blocked, ValueError) as error:
            return [str(error)]

    def reconcile(self, branch):
        response = self.api(f"repos/{self.config['repository']}/pulls?state=all&head={self.config['repository'].split('/')[0]}:{branch}")
        matching = [pr for pr in response if pr["base"]["ref"] == self.config["base"]]
        if len(matching) > 1:
            raise Blocked("Ambiguous existing PRs; reconcile before retrying integration")
        return matching[0] if matching else None

    def integrate(self, ticket, worktree, branch, pin, base, operation, complete, cancelled, deadline):
        blockers = self.readiness()
        if blockers:
            raise Blocked("; ".join(blockers))
        assert_pin(worktree, pin)
        current = fetched_base(self.root, self.config)
        if current != base:
            return {"outcome": "base-advanced", "base": current}
        intent = operation("push", {"branch": branch, "head": pin["head"]})
        remote = git(worktree, "ls-remote", self.config["remote"], f"refs/heads/{branch}").split()
        if remote and remote[0] != pin["head"]:
            raise Blocked("Remote branch differs from reviewed commit; no force push")
        if not remote:
            git(worktree, "push", self.config["remote"], f"{pin['head']}:refs/heads/{branch}")
        complete(intent["id"], {"head": pin["head"]})
        intent = operation("pr", {"branch": branch, "head": pin["head"]})
        pr = self.reconcile(branch)
        if not pr:
            pr = self.api(f"repos/{self.config['repository']}/pulls", "-f", f"head={branch}", "-f", f"base={self.config['base']}",
                          "-f", f"title={ticket.id}: approved implementation", "-f", f"body=Pattyline reviewed commit {pin['head']}; local verification evidence retained in run state.")
        if pr["head"]["sha"] != pin["head"]:
            raise Blocked("PR head differs from reviewed commit")
        complete(intent["id"], {"number": pr["number"], "url": pr["html_url"]})
        while not pr.get("merged_at"):
            if cancelled() or time.time() >= deadline:
                raise Blocked("Integration cancelled/deadline; PR preserved for reconciliation")
            check = self.gh("pr", "checks", str(pr["number"]), "--repo", self.config["repository"], "--required", "--json", "name,state,bucket", check=False)
            if check.returncode not in {0, 8}:
                raise Blocked("Required GitHub checks failed or cannot be established")
            if check.returncode == 0:
                break
            time.sleep(1)
            pr = self.api(f"repos/{self.config['repository']}/pulls/{pr['number']}")
        assert_pin(worktree, pin)
        if fetched_base(self.root, self.config) != base:
            return {"outcome": "base-advanced", "base": fetched_base(self.root, self.config)}
        intent = operation("merge", {"number": pr["number"], "head": pin["head"], "base": base})
        # The head SHA guard alone does not guard base freshness: readiness must also
        # establish an atomic strict server rule before this path is reachable.
        pr = self.api(f"repos/{self.config['repository']}/pulls/{pr['number']}")
        if not pr.get("merged_at"):
            self.api(f"repos/{self.config['repository']}/pulls/{pr['number']}/merge", "--method", "PUT", "-f", "merge_method=merge", "-f", f"sha={pin['head']}")
        merged = self.api(f"repos/{self.config['repository']}/pulls/{pr['number']}")
        final_base = fetched_base(self.root, self.config)
        ancestry = git(self.root, "merge-base", "--is-ancestor", pin["head"], final_base, check=False)
        if not merged.get("merged_at") or ancestry.returncode:
            raise Blocked("Merge response/ancestry does not establish reviewed-commit integration")
        evidence = {"outcome": "integrated", "pr": pr["number"], "url": pr["html_url"], "reviewed": pin["head"],
                    "merge": merged["merge_commit_sha"], "base": final_base}
        complete(intent["id"], evidence)
        return evidence
