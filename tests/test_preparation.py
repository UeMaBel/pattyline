from importlib.resources import files
from pathlib import Path
import tempfile
import unittest
import uuid

from pattyline.common import Blocked
from pattyline.config import DEFAULTS
from copy import deepcopy
from pattyline.preparation import Preparation, bundle


class PreparationRuntime:
    def __init__(self):
        self.prompts = []

    def readiness(self, route, stage):
        return []

    def execute(self, stage, route, directory, prompt, **options):
        self.prompts.append(prompt)
        artifact = {"interview": "interview.md", "specification": "spec.md", "breakdown": "ticket-proposal.md", "preparation-review": "preparation-review.md"}[prompt["stage"]]
        return {"session": uuid.uuid4().hex, "decisions": ["Build a CLI"], "open_questions": [],
                "terms": {"Ticket": "Approved implementation unit"}, "summary": "Draft prepared",
                "artifacts": {artifact: "# Fixture\n\nConfirmed scope.\n"}, "verdict": "approved"}


class PreparationTests(unittest.TestCase):
    def test_slug_normalizes_idea_as_ascii(self):
        self.assertEqual(Preparation.slug("a moving burger cli animation"), "a-moving-burger-cli-animation")
        self.assertEqual(Preparation.slug("Crème brûlée"), "creme-brulee")

    def test_fresh_stage_handoffs_survive_resume_and_review_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = PreparationRuntime()
            preparation = Preparation(directory, DEFAULTS, "feature", runtime)
            preparation.answer("I want a CLI")
            preparation.advance()
            self.assertEqual(preparation.value["stage"], "interview")
            preparation.advance(confirm=True)
            preparation = Preparation(directory, DEFAULTS, "feature", runtime)
            preparation.advance()
            self.assertEqual(preparation.value["stage"], "breakdown")
            preparation.advance()
            with self.assertRaisesRegex(Blocked, "Choose whether"):
                preparation.advance(approve_breakdown=True)
            preparation.choose_review("yes")
            preparation.advance(approve_breakdown=True)
            self.assertEqual(preparation.value["stage"], "approval")
            sessions = [session["session"] for session in preparation.value["sessions"]]
            self.assertEqual(len(sessions), len(set(sessions)))
            self.assertEqual(len(sessions), 3)
            self.assertTrue((Path(directory) / "docs/pattyline/feature/handoff.md").is_file())
            self.assertIn("to-tickets", runtime.prompts[2]["skills"])
            self.assertEqual(runtime.prompts[2]["state"]["decisions"], ["Build a CLI"])

    def test_open_questions_cannot_be_summarized_away_by_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            preparation = Preparation(directory, DEFAULTS, "feature", PreparationRuntime())
            preparation.value["decisions"] = ["Build a CLI"]
            preparation.value["open_questions"] = ["Which platform?"]
            preparation.save()
            with self.assertRaisesRegex(Blocked, "Resolve open"):
                preparation.advance(confirm=True)
            restored = Preparation(directory, DEFAULTS, "feature")
            self.assertEqual(restored.value["open_questions"], ["Which platform?"])

    def test_production_preparation_preserves_human_input_when_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            preparation = Preparation(directory, DEFAULTS, "feature")
            preparation.answer("Important decision")
            with self.assertRaisesRegex(Blocked, "Preparation saved"):
                preparation.advance()
            restored = Preparation(directory, DEFAULTS, "feature")
            self.assertEqual(restored.value["messages"][0]["text"], "Important decision")

    def test_complete_owned_resources_and_license_are_bundled(self):
        base = files("pattyline") / "resources"
        self.assertIn("MIT License", (base / "LICENSE.txt").read_text())
        self.assertIn("No installed personal skills", (base / "NOTICE.md").read_text())
        for stage in ("interview", "specification", "breakdown", "preparation-review"):
            resources = bundle(stage)
            for name, text in resources.items():
                self.assertTrue(text.strip(), name)
                if name != "templates":
                    self.assertIn("name:", text)

    def test_approved_breakdown_publishes_valid_unapproved_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            values = deepcopy(DEFAULTS)
            values["checks"] = {"unit": {"argv": ["test-command"], "cwd": ".", "timeout": 10}}
            preparation = Preparation(directory, values, "feature", PreparationRuntime())
            preparation.value.update({"stage": "approval", "breakdown_approved": True, "review_choice": "no", "proposed_tickets": [{"number": "01", "title": "Observable outcome", "outcome": "Write output", "route": "local", "dependencies": [], "scope": ["app/*"], "verification": [{"name": "unit", "phase": "introduced"}], "acceptance": ["Output exists."], "cloud_escalation_allowed": False}]})
            (preparation.directory / "spec.md").write_text("# Specification\n\nBuild the output.")
            result = preparation.publish()
            from pattyline.tickets import discover
            from pattyline.approval import reason
            tickets = discover(directory, values)
            self.assertEqual(len(tickets), 1)
            self.assertIsNotNone(reason(directory, tickets["feature/01"], values))
            self.assertEqual(len(result["published"]), 1)
            ticket_path = next(iter(tickets.values())).path
            original = ticket_path.read_text()
            self.assertEqual(preparation.publish()["published"], result["published"])
            self.assertEqual(ticket_path.read_text(), original)
