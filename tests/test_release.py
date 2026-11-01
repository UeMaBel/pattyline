import json
from pathlib import Path
import tempfile
import unittest

from pattyline.release import release_status
from pattyline.common import write_json


class ReleaseTests(unittest.TestCase):
    def test_missing_live_evidence_is_a_release_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            status = release_status(directory)
            self.assertFalse(status["release_ready"])
            self.assertTrue(all(v.startswith("blocked") for v in status["profiles"].values()))

    def test_fixtures_or_imported_pass_claims_cannot_enable_production(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(Path(directory) / ".pattyline/release/fake.json", {"profile": "windows-cloud-implementation", "outcome": "passed", "origin": "fixture"})
            self.assertFalse(release_status(directory)["release_ready"])
