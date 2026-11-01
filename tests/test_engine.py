import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pattyline import approval, config
from pattyline.common import Blocked
from pattyline.engine import Engine
from pattyline.git import git
from tests.support import Project, FakeRuntime, FakeSandbox, FakeGitHub


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in {"PATTYLINE_WORKER", "CODEX_THREAD_ID"}}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.project = Project()
        self.addCleanup(self.project.close)
        self.root, self.config = self.project.root, self.project.config

    def engine(self, runtime=None, sandbox=None, github=None):
        engine = Engine(self.root, self.config, runtime or FakeRuntime(), sandbox or FakeSandbox(), github or FakeGitHub(self.root, self.config))
        self.addCleanup(engine.state.close)
        return engine

    def test_two_dependent_tickets_integrate_from_updated_base(self):
        self.project.add("02", ["feature/01"], "local")
        self.project.approve_commit()
        runtime = FakeRuntime()
        sandbox = FakeSandbox()
        engine = self.engine(runtime, sandbox)
        result = engine.start(["feature/02"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual([row["state"] for row in result["work"]], ["integrated", "integrated"])
        first, second = result["work"]
        self.assertEqual(second["base"], json.loads(first["integration"])["reviewed"])
        self.assertEqual(runtime.calls, [("implementation", "cloud"), ("review", "cloud"), ("implementation", "local"), ("review", "cloud")])
        baselines = [e["evidence"] for e in result["events"] if e["kind"] == "check" and e["evidence"]["phase"] == "baseline"]
        self.assertTrue(all(e["outcome"] == "not-yet-applicable" for e in baselines))
        self.assertEqual(len(sandbox.calls), 2)

    def test_existing_failing_baseline_blocks_before_worker(self):
        self.project.add("01", phase="baseline")
        self.project.approve_commit()
        runtime = FakeRuntime()
        result = self.engine(runtime, FakeSandbox(failures=1)).start(["feature/01"])
        self.assertEqual(runtime.calls, [])
        self.assertEqual(result["work"][0]["state"], "blocked")
        self.assertIn("baseline check", result["work"][0]["reason"])

    def test_failed_post_check_repairs_and_requires_fresh_review(self):
        self.project.approve_commit()
        runtime = FakeRuntime(verdicts=["changes-required", "approved"])
        result = self.engine(runtime, FakeSandbox(failures=1)).start(["feature/01"])
        self.assertEqual(result["work"][0]["attempts"], 3)
        self.assertEqual(result["work"][0]["state"], "integrated")
        self.assertEqual(len([c for c in runtime.calls if c[0] == "review"]), 2)

    def test_review_rejections_exhaust_persisted_budget(self):
        self.project.approve_commit()
        runtime = FakeRuntime(verdicts=["changes-required"] * 4)
        engine = self.engine(runtime)
        result = engine.start(["feature/01"])
        self.assertEqual(result["work"][0]["attempts"], 3)
        self.assertIn("budget exhausted", result["work"][0]["reason"])
        again = engine.resume(result["run"]["id"])
        self.assertEqual(again["work"][0]["attempts"], 3)
        self.assertEqual(len(runtime.calls), 6)

    def test_unrelated_work_continues_after_ticket_blocker(self):
        self.project.add("02")
        self.project.add("03", ["feature/01"])
        self.project.approve_commit()
        result = self.engine(FakeRuntime(verdicts=["blocked", "approved"])).start(["feature/03", "feature/02"])
        states = {r["ticket"]: r["state"] for r in result["work"]}
        self.assertEqual(states, {"feature/01": "blocked", "feature/02": "integrated", "feature/03": "blocked"})

    def test_verification_cannot_mutate_reviewed_tree(self):
        self.project.approve_commit()
        runtime = FakeRuntime()
        sandbox = FakeSandbox(mutate=lambda path: (path / "output-01.txt").write_text("mutated by check"))
        result = self.engine(runtime, sandbox).start(["feature/01"])
        self.assertEqual(result["work"][0]["state"], "blocked")
        self.assertEqual([c for c in runtime.calls if c[0] == "review"], [])

    def test_uncertain_merge_resume_reconciles_without_reimplementation(self):
        self.project.approve_commit()
        runtime = FakeRuntime()
        github = FakeGitHub(self.root, self.config, True)
        engine = self.engine(runtime, github=github)
        result = engine.start(["feature/01"])
        self.assertEqual(result["work"][0]["state"], "blocked")
        calls = list(runtime.calls)
        result = engine.resume(result["run"]["id"])
        self.assertEqual(result["work"][0]["state"], "integrated")
        self.assertEqual(runtime.calls, calls)

    def test_drift_on_resume_cannot_replace_frozen_requirements(self):
        self.project.approve_commit()
        github = FakeGitHub(self.root, self.config, True)
        engine = self.engine(github=github)
        result = engine.start(["feature/01"])
        spec = self.root / "docs/feature/spec.md"
        spec.write_text(spec.read_text() + "\nnew requirement")
        with self.assertRaisesRegex(Blocked, "drift"):
            engine.resume(result["run"]["id"])

    def test_local_escalation_preserves_three_attempt_total(self):
        ticket = self.project.add("01", route="local")
        ticket.data["cloud_escalation_allowed"] = True
        ticket.write()
        self.config["escalation"]["allowed"] = True
        config.save(self.root, self.config)
        self.project.approve_commit()
        runtime = FakeRuntime(verdicts=["changes-required", "changes-required", "approved"])
        result = self.engine(runtime).start(["feature/01"])
        self.assertEqual([route for stage, route in runtime.calls if stage == "implementation"], ["local", "local", "cloud"])
        self.assertEqual(result["work"][0]["attempts"], 3)
        self.assertEqual(result["work"][0]["state"], "integrated")

    def test_approved_early_local_capability_mismatch_can_escalate(self):
        ticket = self.project.add("01", route="local")
        ticket.data["cloud_escalation_allowed"] = True
        ticket.write()
        self.config["escalation"]["allowed"] = True
        config.save(self.root, self.config)
        self.project.approve_commit()
        class LocalUnavailable(FakeRuntime):
            def readiness(self, route="cloud", stage="implementation"):
                return ["Local capability unavailable"] if route == "local" else []
        runtime = LocalUnavailable()
        result = self.engine(runtime).start(["feature/01"])
        self.assertEqual(runtime.calls, [("implementation", "cloud"), ("review", "cloud")])
        self.assertEqual(result["work"][0]["attempts"], 1)
        self.assertEqual(result["work"][0]["local_attempts"], 0)

    def test_malformed_local_result_escalates_only_with_approval_and_keeps_budget(self):
        ticket = self.project.add("01", route="local")
        ticket.data["cloud_escalation_allowed"] = True
        ticket.write()
        self.config["escalation"]["allowed"] = True
        config.save(self.root, self.config)
        self.project.approve_commit()
        class BrokenLocal(FakeRuntime):
            def execute(self, stage, route, worktree, prompt, **options):
                if route == "local":
                    self.calls.append((stage, route))
                    return "malformed"
                return super().execute(stage, route, worktree, prompt, **options)
        runtime = BrokenLocal()
        result = self.engine(runtime).start(["feature/01"])
        self.assertEqual(runtime.calls, [("implementation", "local"), ("implementation", "cloud"), ("review", "cloud")])
        self.assertEqual(result["work"][0]["attempts"], 2)
        self.assertEqual(result["work"][0]["state"], "integrated")

    def test_no_production_fake_switch_or_implicit_permission_bypass(self):
        self.project.approve_commit()
        engine = Engine(self.root, self.config)
        self.addCleanup(engine.state.close)
        with self.assertRaisesRegex(Blocked, "Readiness blocked"):
            engine.start(["feature/01"])
        self.assertFalse(engine.state.db.execute("SELECT * FROM runs").fetchone())

    def test_review_infrastructure_interruption_resumes_without_reimplementation(self):
        self.project.approve_commit()
        interrupted = [False]
        def fail_once(stage, route, worktree, prompt):
            if stage == "review" and not interrupted[0]:
                interrupted[0] = True
                raise Blocked("Reviewer service unavailable")
        runtime = FakeRuntime(on_call=fail_once)
        engine = self.engine(runtime)
        result = engine.start(["feature/01"])
        self.assertEqual(result["work"][0]["stage"], "review")
        result = engine.resume(result["run"]["id"])
        self.assertEqual(result["work"][0]["state"], "integrated")
        self.assertEqual(result["work"][0]["attempts"], 1)
        self.assertEqual(len([call for call in runtime.calls if call[0] == "implementation"]), 1)

    def test_external_acceptance_requires_human_evidence_bound_to_commit(self):
        ticket = self.project.add("01")
        ticket.data["external_verification"] = [{"name": "human-ui", "kind": "human", "criterion": "Approved output exists."}]
        ticket.write()
        tickets = self.project.approve_commit()
        runtime = FakeRuntime()
        engine = self.engine(runtime)
        result = engine.start(["feature/01"])
        self.assertEqual(result["work"][0]["stage"], "external-verification")
        self.assertNotIn(("review", "cloud"), runtime.calls)
        evidence = self.root / "manual-evidence.txt"
        evidence.write_text("Human inspected exact output commit")
        approval.attest(self.root, tickets["feature/01"], self.config, engine.state, "human-ui", "manual-evidence.txt", "fixture human")
        result = engine.resume(result["run"]["id"])
        self.assertEqual(result["work"][0]["state"], "integrated")
        self.assertEqual(result["work"][0]["attempts"], 1)

    def test_clean_base_advance_reruns_checks_and_review_without_reimplementation(self):
        self.project.approve_commit()
        project = self.project
        class AdvancedGitHub(FakeGitHub):
            advanced = False
            def integrate(self, ticket, worktree, branch, pin, base, operation, complete, cancelled, deadline):
                if not self.advanced:
                    self.advanced = True
                    clone = Path(project.temp.name) / "base-advancer"
                    git(project.root, "clone", "--branch", "main", str(project.remote), str(clone))
                    git(clone, "config", "user.name", "Fixture")
                    git(clone, "config", "user.email", "fixture@example.invalid")
                    (clone / "unrelated.txt").write_text("base advance")
                    git(clone, "add", "unrelated.txt")
                    git(clone, "commit", "-m", "Advance base independently")
                    git(clone, "push", "origin", "main")
                    git(project.root, "fetch", "origin", "main")
                    return {"outcome": "base-advanced", "base": git(project.root, "rev-parse", "origin/main")}
                return super().integrate(ticket, worktree, branch, pin, base, operation, complete, cancelled, deadline)
        runtime = FakeRuntime()
        sandbox = FakeSandbox()
        result = self.engine(runtime, sandbox, AdvancedGitHub(self.root, self.config)).start(["feature/01"])
        self.assertEqual(result["work"][0]["state"], "integrated")
        self.assertEqual(result["work"][0]["attempts"], 1)
        self.assertEqual(len(sandbox.calls), 2)
        self.assertEqual(runtime.calls, [("implementation", "cloud"), ("review", "cloud"), ("review", "cloud")])
