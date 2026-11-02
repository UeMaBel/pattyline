from copy import deepcopy
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pattyline.common import Blocked
from pattyline.config import DEFAULTS
from pattyline.onboarding import installation_plan, install, model_plan, download_model, local_inventory, ollama_api, tool_inventory
from pattyline.cli import main


class OnboardingTests(unittest.TestCase):
    def test_installation_plan_requires_pinned_version_and_consent(self):
        with patch("pattyline.onboarding.executable", return_value=None), \
             patch("pattyline.onboarding.latest_version", return_value=None), \
             patch("shutil.which", return_value=None):
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

    def test_shared_inventory_reports_versions_and_excludes_local_runtime_tools(self):
        available = {"git": "2.50.0", "gh": "2.70.0", "codex": "0.166.0"}
        paths = {"git": "fake-git", "gh": "fake-gh", "codex": None}
        output = {"fake-git": "git version 2.50.0.windows.1", "fake-gh": "gh version 2.65.0"}

        class Result:
            def __init__(self, stdout):
                self.stdout, self.stderr = stdout, ""

        with patch("pattyline.onboarding.executable", side_effect=lambda name, configured=None: paths[name]), \
             patch("pattyline.onboarding.latest_version", side_effect=lambda name: available[name]), \
             patch("pattyline.onboarding.subprocess.run", side_effect=lambda argv, **kwargs: Result(output[argv[0]])):
            result = tool_inventory(DEFAULTS)
        self.assertEqual(set(result["tools"]), {"git", "gh", "codex"})
        self.assertEqual(result["tools"]["git"]["update"], "current")
        self.assertEqual(result["tools"]["gh"]["update"], "outdated")
        self.assertEqual(result["tools"]["codex"]["presence"], "missing")

    def test_setup_yes_only_installs_the_named_component(self):
        inventory = {"platform": "fake", "tools": {
            name: {"path": None, "version": None, "latest_version": "2.0.0", "source": "https://official.example",
                   "presence": "missing", "update": "not-installed"} for name in ("git", "gh", "codex")}}
        plan = {"component": "gh", "action": "install", "version": "2.0.0", "source": "https://official.example",
                "argv": ["fake-installer"], "status": "consent required"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict("os.environ", {"CODEX_THREAD_ID": "", "PATTYLINE_WORKER": ""}), \
             patch("pattyline.cli.onboarding.tool_inventory", return_value=inventory), \
             patch("pattyline.cli.onboarding.installation_plan", return_value=plan) as make_plan, \
             patch("pattyline.cli.onboarding.install", return_value={"component": "gh", "state": "installed"}) as do_install, \
             contextlib.redirect_stdout(io.StringIO()):
            result = main(["--project", directory, "setup", "--install", "gh", "--yes"])
        self.assertEqual(result, 0)
        self.assertEqual(make_plan.call_count, 1)
        make_plan.assert_called_once_with("gh", unittest.mock.ANY, "2.0.0", upgrade=False)
        do_install.assert_called_once()

    def test_declining_a_tool_leaves_it_unchanged_and_reports_next_step(self):
        inventory = {"platform": "fake", "tools": {
            name: {"path": None, "version": None, "latest_version": "2.0.0", "source": "https://official.example",
                   "presence": "missing", "update": "not-installed"} for name in ("git", "gh", "codex")}}
        plan = {"component": "git", "action": "install", "version": "2.0.0", "source": "https://official.example",
                "argv": ["fake-installer"], "status": "consent required"}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict("os.environ", {"CODEX_THREAD_ID": "", "PATTYLINE_WORKER": ""}), \
             patch("pattyline.cli.onboarding.tool_inventory", return_value=inventory), \
             patch("pattyline.cli.onboarding.installation_plan", return_value=plan), \
             patch("pattyline.cli.onboarding.install") as do_install, \
             patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"), \
             contextlib.redirect_stdout(output):
            result = main(["--project", directory, "setup", "--install", "git"])
        self.assertEqual(result, 0)
        self.assertIn('"status": "declined"', output.getvalue())
        self.assertIn("rerun setup with --install git", output.getvalue())
        do_install.assert_not_called()

    def test_manual_install_plan_does_not_guess_a_command(self):
        with patch("pattyline.onboarding.executable", return_value=None), \
             patch("pattyline.onboarding.latest_version", return_value="2.56.0"), \
             patch("shutil.which", return_value=None):
            plan = installation_plan("git", DEFAULTS)
        self.assertIsNone(plan["argv"])
        self.assertEqual(plan["version"], "2.56.0")
        self.assertEqual(plan["source"], "https://git-scm.com/downloads")

    def test_failed_installation_is_incomplete_and_writes_no_project_evidence(self):
        plan = {"component": "codex", "action": "install", "argv": ["fake-installer"], "source": "official"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("pattyline.onboarding.run", return_value={"exit_code": 5, "outcome": "exited"}):
            with self.assertRaisesRegex(Blocked, "Installation incomplete"):
                install(directory, plan, consent=True)
            self.assertFalse((Path(directory) / ".pattyline").exists())

    def test_successful_installer_requires_matching_version_before_reporting_installed(self):
        plan = {"component": "codex", "action": "install", "version": "1.2.3", "argv": ["fake-installer"],
                "source": "official"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("pattyline.onboarding.run", return_value={"exit_code": 0, "outcome": "exited"}), \
             patch("pattyline.onboarding.executable", return_value="fake-codex"), \
             patch("pattyline.onboarding._installed_version", return_value="codex-cli 1.2.3"):
            result = install(directory, plan, consent=True)
        self.assertEqual(result["state"], "installed")
        self.assertEqual(result["version"], "codex-cli 1.2.3")

        with tempfile.TemporaryDirectory() as directory, \
             patch("pattyline.onboarding.run", return_value={"exit_code": 0, "outcome": "exited"}), \
             patch("pattyline.onboarding.executable", return_value="fake-codex"), \
             patch("pattyline.onboarding._installed_version", return_value="codex-cli 1.2.2"):
            result = install(directory, plan, consent=True)
        self.assertEqual(result["state"], "installed-needs-validation")

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
