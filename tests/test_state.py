import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest

from pattyline.common import Blocked
from pattyline.state import State


def claimant(root, run, ticket, queue):
    state = State(root)
    try:
        queue.put(("claimed", state.claim(run, ticket)))
    except Blocked:
        queue.put(("blocked", None))
    finally:
        state.close()


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(self.temp.name)
        self.addCleanup(self.state.close)
        self.snapshot = {"order": ["f/01", "f/02"], "fingerprints": {"f/01": "first", "f/02": "second"}, "config": {}}
        self.run = self.state.create_run(self.snapshot, time.time() + 100)

    def test_real_process_claims_share_project_capacity(self):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        children = [context.Process(target=claimant, args=(self.temp.name, self.run, ticket, queue)) for ticket in self.snapshot["order"]]
        for child in children:
            child.start()
        results = [queue.get(timeout=15) for _ in children]
        for child in children:
            child.join(15)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(sorted(item[0] for item in results), ["blocked", "claimed"])
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM claims").fetchone()[0], 1)

    def test_budget_survives_successor_and_uncertain_process_retains_claim(self):
        owner = self.state.claim(self.run, "f/01")
        self.state.attempt("f/01", "local", {"repairs": 2, "local_repairs": 1})
        self.state.update("f/01", process={"pid": 1, "identity": "uncertain"})
        with self.assertRaisesRegex(Blocked, "termination"):
            self.state.release("f/01", owner)
        self.state.update("f/01", process=None)
        self.state.release("f/01", owner)
        self.state.finish(self.run, "blocked")
        successor = self.state.create_run({**self.snapshot, "fingerprints": {"f/01": "changed", "f/02": "second"}}, time.time() + 100)
        self.assertEqual(self.state.get_work("f/01")["attempts"], 1)
        self.assertNotEqual(successor, self.run)
        self.assertEqual(self.state.get_work("f/01")["fingerprint"], "changed")

    def test_uncertain_external_operation_is_not_discarded(self):
        first = self.state.intent(self.run, "f/01", "pr", {"branch": "one"})
        second = self.state.intent(self.run, "f/01", "pr", {"branch": "one"})
        self.assertEqual(first["id"], second["id"])
        self.state.complete_operation(first["id"], {"number": 42})
        self.assertEqual(json.loads(self.state.intent(self.run, "f/01", "pr", {})["result"])["number"], 42)

    def test_successor_reports_preserve_prior_state_and_secret_values_are_redacted(self):
        from unittest.mock import patch
        self.state.update("f/01", state="blocked", reason="old requirement")
        self.state.finish(self.run, "blocked")
        successor = self.state.create_run({**self.snapshot, "fingerprints": {"f/01": "changed", "f/02": "second"}}, time.time() + 100)
        self.state.update("f/01", state="implementing", reason="new requirement")
        old = self.state.report(self.run)
        self.assertEqual(old["work"][0]["state"], "blocked")
        self.assertEqual(old["work"][0]["fingerprint"], "first")
        with patch.dict("os.environ", {"GITHUB_TOKEN": "sensitive-test-token"}):
            self.state.event(successor, "f/01", "output", {"stdout": "sensitive-test-token"})
        current = self.state.report(successor)
        self.assertNotIn("sensitive-test-token", json.dumps(current))

    def test_cancel_prevents_claim_and_report_marks_unknown_usage(self):
        self.state.cancel(self.run)
        with self.assertRaisesRegex(Blocked, "cancellation"):
            self.state.claim(self.run, "f/01")
        report = self.state.report(self.run)
        self.assertEqual(report["usage"]["cloud"], "unknown")
        self.assertTrue(Path(report["report"]).is_file())
