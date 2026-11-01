from copy import deepcopy
import unittest
from unittest.mock import patch

from pattyline.common import Blocked
from pattyline.config import DEFAULTS
from pattyline.github import GitHub


class GitHubTests(unittest.TestCase):
    def test_missing_cli_blocks_without_side_effects(self):
        values = deepcopy(DEFAULTS)
        values["repository"] = "example/project"
        service = GitHub(".", values)
        with patch("shutil.which", return_value=None):
            self.assertIn("Install GitHub CLI", service.readiness()[0])

    def test_remote_identity_drift_blocks_before_auth_or_push(self):
        values = deepcopy(DEFAULTS)
        values["repository"] = "expected/project"
        service = GitHub(".", values)
        with patch("shutil.which", return_value="gh"), patch("pattyline.github.git", return_value="https://github.com/other/project.git"), patch.object(service, "gh") as command:
            self.assertIn("identity", service.readiness()[0])
            command.assert_not_called()

    def test_strict_rules_are_not_falsely_reported_as_race_proof(self):
        values = deepcopy(DEFAULTS)
        values["repository"] = "expected/project"
        service = GitHub(".", values)
        with patch("shutil.which", return_value="gh"), patch("pattyline.github.git", return_value="https://github.com/expected/project.git"), patch.object(service, "gh"), patch.object(service, "api", side_effect=[{"allow_merge_commit": True}, {"required_status_checks": {"strict": True, "contexts": ["test"]}}]):
            self.assertIn("not been demonstrated", service.readiness()[0])

    def test_failed_freshness_gate_prevents_all_mutations(self):
        service = GitHub(".", DEFAULTS)
        with patch.object(service, "readiness", return_value=["Freshness unavailable"]), patch.object(service, "api") as api:
            with self.assertRaisesRegex(Blocked, "Freshness"):
                service.integrate(None, ".", "branch", {}, "base", None, None, lambda: False, 0)
            api.assert_not_called()
