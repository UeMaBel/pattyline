import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pattyline import approval
from pattyline.common import Blocked
from pattyline.tickets import discover, order, parse, legacy_preview, adopt
from tests.support import Project


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.addCleanup(self.project.close)
        self.root, self.config = self.project.root, self.project.config

    def test_prerequisite_closure_excludes_reverse_dependents(self):
        self.project.add("02", ["feature/01"])
        tickets = discover(self.root, self.config)
        self.assertEqual(order(tickets, ["feature/01"]), ["feature/01"])
        self.assertEqual(order(tickets, ["feature/02"]), ["feature/01", "feature/02"])

    def test_relative_dependencies_normalize_and_readable_contradictions_fail(self):
        second = self.project.add("02", ["01-task.md"])
        tickets = discover(self.root, self.config)
        self.assertEqual(tickets["feature/02"].dependencies, ["feature/01"])
        second.path.write_text(second.text.replace("## Acceptance criteria", "**Blocked by:** None\n\n## Acceptance criteria"))
        with self.assertRaisesRegex(Blocked, "contradicts"):
            discover(self.root, self.config)

    def test_cycles_duplicate_identity_and_unknown_checks_are_errors(self):
        self.project.add("01", ["feature/02"])
        self.project.add("02", ["feature/01"])
        with self.assertRaisesRegex(Blocked, "cycle"):
            discover(self.root, self.config)
        self.project.add("01")
        self.project.add("02")
        first = self.root / "docs/feature/issues/01-task.md"
        (first.parent / "duplicate.md").write_text(first.read_text())
        with self.assertRaisesRegex(Blocked, "Duplicate"):
            discover(self.root, self.config)

    def test_approval_semantics_and_filename_rename(self):
        ticket = self.project.add("01")
        with patch.dict(os.environ, {}, clear=True):
            approval.approve(self.root, [ticket], self.config, "human")
        self.assertIsNone(approval.reason(self.root, ticket, self.config))
        ticket.path.write_text(ticket.text + "\nnew operational comment\n")
        renamed = ticket.path.with_name("01-renamed.md")
        ticket.path.rename(renamed)
        parsed = parse(renamed, self.root, self.config)
        self.assertIsNone(approval.reason(self.root, parsed, self.config))
        spec = self.root / "docs/feature/spec.md"
        spec.write_text(spec.read_text() + "\nAdditional requirement")
        self.assertIn("drift", approval.reason(self.root, parsed, self.config))

    def test_worker_cannot_approve(self):
        with patch.dict(os.environ, {"PATTYLINE_WORKER": "1"}):
            with self.assertRaisesRegex(Blocked, "Worker"):
                approval.approve(self.root, [self.project.add("01")], self.config)

    def test_applicable_agent_instructions_are_approval_inputs(self):
        ticket = self.project.add("01")
        with patch.dict(os.environ, {}, clear=True):
            approval.approve(self.root, [ticket], self.config, "human")
        (self.root / "AGENTS.md").write_text("Use explicit verification evidence.")
        self.assertIn("drift", approval.reason(self.root, ticket, self.config))

    def test_phase_is_explicit(self):
        ticket = self.project.add("01")
        ticket.data["verification"] = ["unit"]
        ticket.write()
        with self.assertRaisesRegex(Blocked, "phase"):
            parse(ticket.path, self.root, self.config)

    def test_legacy_import_retains_history_and_needs_reconciliation(self):
        path = self.root / "legacy/issues/67-example.md"
        path.parent.mkdir(parents=True)
        original = "# Example\n\n**Status:** resolved\n\n**Type:** task\n\n**llm_target:** local\n\n**Blocked by:** None\n\n## Acceptance criteria\n\n- [x] History retained.\n\n## Comments\n\nHistorical commit abc123\n"
        path.write_text(original)
        preview = legacy_preview(self.root, [path])
        with self.assertRaisesRegex(Blocked, "reconciliation"):
            adopt(self.root, preview, "docs/feature/spec.md", [{"name": "unit", "phase": "baseline"}], ["app/*"])
        adopt(self.root, preview, "docs/feature/spec.md", [{"name": "unit", "phase": "baseline"}], ["app/*"], True)
        self.assertIn(original, path.read_text())
        ticket = parse(path, self.root, self.config)
        self.assertIsNotNone(approval.reason(self.root, ticket, self.config))
        first = path.read_text()
        self.assertIn("already native", adopt(self.root, preview, "docs/feature/spec.md", [], [], True)[0]["result"])
        self.assertEqual(path.read_text(), first)
