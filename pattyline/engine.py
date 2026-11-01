"""Sequential frozen-run scheduling, independent review and bounded repair."""
import json
from pathlib import Path
import re
import time
import uuid

from . import approval
from .common import Blocked, CapabilityMismatch, canonical, digest, within
from . import git
from .github import GitHub
from .runtime import adapter, validate_result
from .sandbox import Container
from .state import State
from .tickets import discover, order
from .verification import CheckFailed, checks, setup


class RepairRequired(Blocked):
    pass


def plan(root, config, selection=None):
    tickets = discover(root, config)
    identities = order(tickets, selection)
    state = State(root) if (Path(root) / ".pattyline/state.sqlite3").exists() else None
    states = {identity: state.get_work(identity) for identity in tickets} if state else {}
    if state:
        state.close()
    return {"order": identities, "tickets": [{"id": identity, "route": tickets[identity].data["llm_target"],
             "dependencies": tickets[identity].dependencies,
             "execution_state": states[identity]["state"] if states.get(identity) else "unstarted",
             "scheduling_blockers": [f"Wait for verified integration of {dep}" for dep in tickets[identity].dependencies if not states.get(dep) or states[dep]["state"] != "integrated"],
             "blocker": approval.reason(root, tickets[identity], config)} for identity in identities]}


class Engine:
    def __init__(self, root, config, runtime=None, sandbox=None, github=None, clock=time.time):
        self.root, self.config = Path(root).resolve(), config
        self.state = State(self.root)
        self.runtime = runtime if runtime is not None else adapter(self.root, config)
        self.sandbox = sandbox if sandbox is not None else Container(config)
        self.github = github if github is not None else GitHub(self.root, config)
        self.clock = clock

    def readiness(self, identities):
        tickets = discover(self.root, self.config)
        blockers = list(self.sandbox.readiness())
        for identity in identities:
            ticket = tickets[identity]
            reason = approval.reason(self.root, ticket, self.config)
            if reason:
                blockers.append(f"{identity}: {reason}")
            runtime_blockers = self.runtime.readiness(ticket.data["llm_target"])
            if runtime_blockers and ticket.data["llm_target"] == "local" and ticket.data["cloud_escalation_allowed"] and self.config["escalation"]["allowed"]:
                runtime_blockers = self.runtime.readiness("cloud")
            blockers.extend(runtime_blockers)
        blockers.extend(self.runtime.readiness("cloud", "review"))
        blockers.extend(self.github.readiness())
        return sorted(set(blockers))

    def start(self, selection=None, run_seconds=None):
        tickets = discover(self.root, self.config)
        identities = order(tickets, selection)
        if not identities:
            raise Blocked("No selected tickets")
        blockers = self.readiness(identities)
        if blockers:
            raise Blocked("Readiness blocked:\n" + "\n".join(blockers))
        git.committed_inputs(self.root, [tickets[i] for i in identities], self.config)
        maximum = self.config["limits"]["run_seconds"]
        if run_seconds is not None and not 0 < run_seconds <= maximum:
            raise Blocked("Invocation deadline may only tighten the approved run limit")
        snapshot = {"order": identities, "config": self.config,
                    "fingerprints": {i: approval.fingerprint(self.root, tickets[i], self.config) for i in identities},
                    "sources": {i: str(tickets[i].path.relative_to(self.root)) for i in identities}}
        identity = self.state.create_run(snapshot, self.clock() + (run_seconds or maximum))
        return self.execute(identity)

    def revalidate(self, run, ticket):
        # Reload configuration and all source inputs before every dispatch/integration.
        from .config import load
        current = load(self.root)
        fresh = discover(self.root, current).get(ticket.id)
        if not fresh:
            raise Blocked("Frozen ticket removed; preserve work and prepare a successor")
        reason = approval.reason(self.root, fresh, current)
        if reason or approval.fingerprint(self.root, fresh, current) != run["snapshot"]["fingerprints"][ticket.id]:
            raise Blocked(reason or "Frozen requirements differ; start an approved successor instead of mutating this run")
        git.committed_inputs(self.root, [fresh], current)

    def cancelled(self, run):
        current = self.state.run(run["id"])
        return bool(current["stop"]) or self.clock() >= current["deadline"]

    def options(self, run, ticket):
        return {"cancelled": lambda: self.cancelled(run),
                "grace": self.config["limits"]["termination_grace_seconds"],
                "on_start": lambda process: self.state.update(ticket.id, process=process),
                "on_stop": lambda process: self.process_stopped(run, ticket, process)}

    def process_stopped(self, run, ticket, process):
        self.state.event(run["id"], ticket.id, "process-stopped", process)
        self.state.update(ticket.id, process=None)

    def execute(self, identity):
        run = self.state.run(identity)
        tickets = discover(self.root, self.config)
        for selected in run["snapshot"]["order"]:
            if self.cancelled(run):
                return self.state.finish(identity, "cancelled" if self.state.run(identity)["stop"] else "deadline")
            ticket = tickets.get(selected)
            row = self.state.get_work(selected)
            if row["state"] == "integrated":
                continue
            if not ticket:
                self.state.update(selected, state="blocked", reason="Frozen ticket missing; restore or approve successor")
                continue
            dependencies = [dep for dep in ticket.dependencies if not self.state.get_work(dep) or self.state.get_work(dep)["state"] != "integrated"]
            if dependencies:
                self.state.update(selected, state="blocked", reason="Dependencies not integrated: " + ", ".join(dependencies))
                continue
            owner = self.state.claim(identity, selected)
            try:
                self.revalidate(run, ticket)
                self.execute_ticket(run, ticket)
            except Blocked as error:
                self.state.update(selected, state="cancelled" if self.cancelled(run) else "blocked", reason=str(error))
                self.state.event(identity, selected, "blocked", {"reason": str(error)})
            except BaseException:
                self.state.update(selected, state="blocked", reason="Orchestrator interrupted; reconcile persisted stage/process before resuming")
                self.state.finish(identity, "interrupted")
                raise
            finally:
                if not self.state.get_work(selected)["process"]:
                    self.state.release(selected, owner)
                self.state.report(identity)
            if self.state.get_work(selected)["process"]:
                return self.state.finish(identity, "blocked-ownership")
        rows = [self.state.get_work(i) for i in run["snapshot"]["order"]]
        return self.state.finish(identity, "completed" if all(r["state"] == "integrated" for r in rows) else "blocked")

    def execute_ticket(self, run, ticket):
        row = self.state.get_work(ticket.id)
        event = lambda kind, evidence: self.state.event(run["id"], ticket.id, kind, evidence)
        options = self.options(run, ticket)
        if not row["worktree"]:
            self.state.update(ticket.id, state="preparing", stage="worktree")
            base = git.fetched_base(self.root, self.config)
            branch = f"pattyline/{ticket.id.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
            # Persist worktree intent before the external Git operation.
            path = (self.root / self.config["worktrees"]).resolve() / branch.replace("/", "-")
            self.state.update(ticket.id, branch=branch, worktree=str(path), base=base)
            git.worktree(self.root, self.config, branch, base)
            event("worktree", {"path": str(path), "branch": branch, "base": base})
        row = self.state.get_work(ticket.id)
        worktree = Path(row["worktree"])
        if not worktree.exists():
            # Safe reconciliation of persisted intent, only if branch is absent as well.
            if git.git(self.root, "show-ref", "--verify", f"refs/heads/{row['branch']}", check=False).returncode == 0:
                raise Blocked("Worktree creation uncertain: branch exists without expected worktree; reconcile manually")
            git.worktree(self.root, self.config, row["branch"], row["base"])
        if git.git(worktree, "branch", "--show-current") != row["branch"]:
            raise Blocked("Preserved worktree branch identity differs")
        if row["review"] and row["pin"]:
            pin = json.loads(row["pin"])
            git.assert_pin(worktree, pin)
            try:
                return self.integrate(run, ticket, worktree, pin, event, options)
            except RepairRequired:
                row = self.state.get_work(ticket.id)
        if row["pin"] and row["stage"] in {"checks", "review", "external-verification", "base-update"}:
            try:
                return self.finish_implementation(run, ticket, worktree, json.loads(row["pin"]), row["reason"] or "", event, options)
            except RepairRequired:
                row = self.state.get_work(ticket.id)
        if row["attempts"] == 0:
            setup(self.root, worktree, ticket, self.config, self.sandbox, event, **options)
            checks(self.root, worktree, ticket, self.config, self.sandbox, "baseline", event, **options)
        findings = row["reason"] or ""
        route = ticket.data["llm_target"]
        while True:
            if self.cancelled(run):
                raise Blocked("Cancellation/run deadline; preserved work")
            self.revalidate(run, ticket)
            row = self.state.get_work(ticket.id)
            limits = self.config["limits"]
            if route == "local" and row["local_attempts"] >= 1 + limits["local_repairs"]:
                if not ticket.data["cloud_escalation_allowed"] or not self.config["escalation"]["allowed"]:
                    raise Blocked("Local repair allocation exhausted; cloud escalation not approved")
                route = "cloud"
                event("escalation", {"preserved_branch": row["branch"], "consumed_attempts": row["attempts"], "remaining": 1 + limits["repairs"] - row["attempts"], "findings": findings})
            blockers = self.runtime.readiness(route)
            if blockers:
                if route == "local" and ticket.data["cloud_escalation_allowed"] and self.config["escalation"]["allowed"]:
                    route = "cloud"
                    event("escalation", {"reason": blockers, "consumed_attempts": row["attempts"], "remaining": 1 + limits["repairs"] - row["attempts"]})
                    continue
                raise Blocked("; ".join(blockers))
            attempt = self.state.attempt(ticket.id, route, limits)
            self.state.update(ticket.id, state="implementing" if attempt == 1 else "repairing", stage="implementation", review=None)
            prompt = self.context(run, ticket, worktree, findings)
            try:
                implementation = validate_result(self.runtime.execute("implementation", route, worktree, prompt,
                              timeout=min(limits["implementation_seconds"], max(1, int(run["deadline"] - self.clock()))), **options), "implementation")
            except CapabilityMismatch as error:
                event("capability-mismatch", {"route": route, "attempt": attempt, "reason": str(error)})
                if route == "local" and ticket.data["cloud_escalation_allowed"] and self.config["escalation"]["allowed"] and attempt < 1 + limits["repairs"] and not self.state.get_work(ticket.id)["process"]:
                    findings = str(error)
                    route = "cloud"
                    event("escalation", {"reason": findings, "consumed_attempts": attempt, "remaining": 1 + limits["repairs"] - attempt, "preserved_branch": row["branch"]})
                    continue
                raise
            if self.state.get_work(ticket.id)["process"]:
                raise Blocked("Implementer termination is uncertain; verification forbidden")
            implementation.update({"route": route, "attempt": attempt})
            event("implementation", implementation)
            from .sandbox import protected_paths
            pin = git.scoped_commit(worktree, ticket, protected_paths(self.root, ticket, self.config), implementation["files"])
            self.state.update(ticket.id, state="checking", stage="checks", pin=pin)
            try:
                return self.finish_implementation(run, ticket, worktree, pin, findings, event, options)
            except RepairRequired as error:
                findings = str(error)
                continue

    def finish_implementation(self, run, ticket, worktree, pin, findings, event, options):
        if self.cancelled(run):
            raise Blocked("Cancellation/deadline before verification or review")
        self.revalidate(run, ticket)
        self.state.update(ticket.id, state="checking", stage="checks")
        try:
            evidence = checks(self.root, worktree, ticket, self.config, self.sandbox, "post-implementation", event, **options)
        except (CheckFailed, git.TreeChanged) as error:
            self.state.update(ticket.id, stage="repair-needed", reason=str(error), review=None)
            raise RepairRequired(str(error)) from error
        self.revalidate(run, ticket)
        if self.cancelled(run):
            raise Blocked("Cancellation/deadline before independent review")
        self.state.update(ticket.id, stage="external-verification")
        external = approval.external_evidence(self.root, ticket, self.config, self.state, pin)
        self.state.update(ticket.id, state="reviewing", stage="review")
        git.assert_pin(worktree, pin)
        row = self.state.get_work(ticket.id)
        review_prompt = self.context(run, ticket, worktree, findings)
        review_prompt.update({"commit": pin, "checks": evidence, "external_evidence": external,
                              "diff": git.git(worktree, "diff", "--no-ext-diff", "--no-textconv", row["base"], pin["head"], "--")})
        review = validate_result(self.runtime.execute("review", "cloud", worktree, review_prompt,
                   timeout=min(self.config["limits"]["review_seconds"], max(1, int(run["deadline"] - self.clock()))), **options), "review")
        git.assert_pin(worktree, pin)
        if self.state.get_work(ticket.id)["process"]:
            raise Blocked("Reviewer termination uncertain")
        implementation_sessions = {json.loads(row[0]).get("session") for row in self.state.db.execute("SELECT evidence FROM events WHERE ticket=? AND kind='implementation'", (ticket.id,))}
        if review["session"] in implementation_sessions:
            raise Blocked("Implementation review must use an independent fresh cloud session")
        event("review", review)
        if review["verdict"] == "blocked":
            raise Blocked("Independent review blocked: " + canonical(review["findings"]))
        if review["verdict"] == "changes-required":
            if not review["findings"]:
                raise Blocked("Reviewer changes-required lacks concrete findings")
            self.state.update(ticket.id, stage="repair-needed", reason=canonical(review["findings"]), review=None)
            raise RepairRequired(canonical(review["findings"]))
        criteria = acceptance(ticket)
        if any(not review["acceptance_evidence"].get(criterion) for criterion in criteria):
            raise Blocked("Independent review lacks evidence for every acceptance criterion")
        review.update({"pin": pin, "fingerprint": run["snapshot"]["fingerprints"][ticket.id]})
        self.state.update(ticket.id, state="awaiting-integration", stage="integration", review=review)
        return self.integrate(run, ticket, worktree, pin, event, options)

    def context(self, run, ticket, worktree, findings):
        from .sandbox import protected_paths
        return {"ticket": approval.effective(self.root, ticket, self.config), "run": run["id"],
                "protected_paths": protected_paths(self.root, ticket, self.config),
                "branch": self.state.get_work(ticket.id)["branch"], "findings": findings,
                "attempts_consumed": self.state.get_work(ticket.id)["attempts"],
                "dependencies": {d: self.state.get_work(d)["integration"] for d in ticket.dependencies},
                "worker_authority": "Implement approved scope only; orchestrator owns commits/checks; fresh cloud review is mandatory"}

    def integrate(self, run, ticket, worktree, pin, event, options):
        self.revalidate(run, ticket)
        approval.external_evidence(self.root, ticket, self.config, self.state, pin)
        if self.cancelled(run):
            raise Blocked("Cancellation/deadline before integration")
        row = self.state.get_work(ticket.id)
        self.state.update(ticket.id, state="integrating", stage="integration")
        result = self.github.integrate(ticket, worktree, row["branch"], pin, row["base"],
                lambda kind, value: self.state.intent(run["id"], ticket.id, kind + ":" + pin["head"], value),
                self.state.complete_operation, lambda: self.cancelled(run),
                min(run["deadline"], self.clock() + self.config["limits"]["integration_seconds"]))
        event("integration", result)
        if result["outcome"] == "base-advanced":
            self.state.update(ticket.id, review=None, state="preparing", stage="base-update")
            merge = git.git(worktree, "merge", "--no-edit", result["base"], check=False)
            updated = git.head_tree(worktree)
            self.state.update(ticket.id, base=result["base"], pin=updated, reason="Base advanced: combined result requires fresh checks and independent review")
            if merge.returncode:
                self.state.update(ticket.id, stage="repair-needed", reason="Base merge conflict; repair consumes remaining budget")
                raise RepairRequired("Base merge conflict; repair consumes remaining budget")
            return self.finish_implementation(run, ticket, worktree, updated, "Base advanced; review the combined result", event, options)
        if result["outcome"] != "integrated":
            raise Blocked("Integration did not establish verified ancestry")
        self.state.update(ticket.id, state="integrated", stage="integrated", integration=result, reason=None)

    def resume(self, identity=None):
        run = self.state.run(identity)
        from .process import reconcile_process, process_identity
        for selected in run["snapshot"]["order"]:
            row = self.state.get_work(selected)
            if row["owner"]:
                claim = self.state.db.execute("SELECT controller FROM claims WHERE ticket=?", (selected,)).fetchone()
                controller = json.loads(claim[0]) if claim else {}
                if not controller.get("identity") or process_identity(controller["pid"]) == controller["identity"]:
                    raise Blocked(f"{selected}: original controller alive or unknown; preserve exclusive ownership")
                process = json.loads(row["process"]) if row["process"] else None
                status = reconcile_process(process)
                if status != "stopped":
                    raise Blocked(f"{selected}: {status}; ownership cannot be released from timestamp/PID alone")
                if process and process.get("mechanism") == "docker" and process.get("id"):
                    self.state.event(run["id"], selected, "recovery-container-stopped", process)
                    self.state.update(selected, process=None)
                    self.state.release(selected, row["owner"])
                    continue
                # No process record after an orchestrator crash could precede child
                # identity persistence. Only completed stop evidence is sufficient.
                raise Blocked(f"{selected}: interrupted claim requires proven executor/session termination; no automatic ownership reset")
            ticket = discover(self.root, self.config).get(selected)
            if ticket:
                self.revalidate(run, ticket)
        if self.clock() >= run["deadline"]:
            raise Blocked("Original run deadline exhausted; a new run may not reset ticket budgets")
        if run["stop"]:
            raise Blocked("Cancelled run cannot silently revoke stop request; use a successor start preserving budgets")
        if self.config != run["snapshot"]["config"]:
            raise Blocked("Frozen configuration changed; start approved successor")
        blockers = self.readiness(run["snapshot"]["order"])
        if blockers:
            raise Blocked("; ".join(blockers))
        self.state.finish(run["id"], "running")
        return self.execute(run["id"])


def acceptance(ticket):
    match = re.search(r"^## Acceptance criteria\s*\n(.*?)(?=\n## |\Z)", ticket.text, re.M | re.S)
    if not match:
        raise Blocked("No acceptance section")
    return [re.sub(r"^- \[[ xX]\]\s*", "", line).strip() for line in match[1].splitlines() if line.startswith("- [")]
