from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pattyline.common import Blocked
from pattyline.config import DEFAULTS
from pattyline.onboarding import installation_plan, install, model_plan, download_model, local_inventory, ollama_api


class OnboardingTests(unittest.TestCase):
    def test_installation_plan_requires_pinned_version_and_consent(self):
        with patch("pattyline.onboarding.executable", return_value=None), patch("shutil.which", return_value=None):
            plan = installation_plan("codex", DEFAULTS)
        self.assertEqual(plan["version"], "0.166.0")
        self.assertIsNone(plan["argv"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(Blocked, "consent"):
                install(directory, plan)
            with self.assertRaisesRegex(Blocked, "Manual"):
                install(directory, plan, True)

    def test_existing_tools_are_never_silently_replaced(self):
        with patch("pattyline.onboarding.executable", return_value="/existing/tool"):
            with self.assertRaisesRegex(Blocked, "already installed"):
                installation_plan("codex", DEFAULTS)

    def test_unknown_download_size_is_explicit_and_no_download_without_consent(self):
        plan = model_plan(DEFAULTS, "local-model:8b")
        self.assertIn("unknown", plan["download_bytes"])
        with patch("pattyline.onboarding.ollama_api") as request:
            with self.assertRaisesRegex(Blocked, "consent"):
                download_model(".", DEFAULTS, "local-model:8b")
            request.assert_not_called()

    def test_cloud_model_and_nonloopback_endpoints_are_rejected(self):
        values = deepcopy(DEFAULTS)
        values["runtime"]["ollama_url"] = "https://provider.example"
        with self.assertRaisesRegex(Blocked, "loopback"):
            ollama_api(values, "/api/tags")
        with self.assertRaises(Blocked):
            model_plan(DEFAULTS, "model:cloud")

    def test_loopback_provider_name_does_not_establish_local_inference(self):
        values = deepcopy(DEFAULTS)
        values["runtime"]["local_model"] = "selected"
        with patch("pattyline.onboarding.ollama_api", side_effect=[{"models": [{"name": "selected"}]}, {"remote_host": "cloud"}]):
            with self.assertRaisesRegex(Blocked, "remote"):
                local_inventory(values)
