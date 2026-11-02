import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pattyline.cli import main
from pattyline.onboarding import codex_auth_status


def inventory(missing=()):
    tools = {}
    for name in ("git", "gh", "codex"):
        installed = name not in missing
        tools[name] = {"path": f"fake-{name}" if installed else None,
                       "version": "1.0.0" if installed else None,
                       "latest_version": "1.0.0", "source": "official",
                       "presence": "installed" if installed else "missing",
                       "update": "current" if installed else "not-installed"}
    return {"platform": "test", "tools": tools}


class CodexAuthenticationTests(unittest.TestCase):
    def run_setup(self, statuses, login=None, inventories=None, interactive=False, answer="yes"):
        with tempfile.TemporaryDirectory() as directory:
            class Output(io.StringIO):
                def isatty(self):
                    return interactive

            output = Output()
            env = {"APPDATA": os.path.join(directory, "appdata"),
                   "XDG_CONFIG_HOME": os.path.join(directory, "xdg"),
                   "HOME": directory, "USERPROFILE": directory,
                   "PATTYLINE_WORKER": "", "CODEX_THREAD_ID": ""}
            with patch.dict(os.environ, env, clear=True), \
                    patch("pattyline.onboarding.tool_inventory", side_effect=inventories or [inventory(), inventory()]), \
                    patch("pattyline.onboarding.codex_auth_status", side_effect=statuses) as auth_status, \
                    patch("pattyline.onboarding.codex_login", return_value=login or {"status": "failed"}) as codex_login, \
                    patch("sys.stdin.isatty", return_value=interactive), \
                    patch("builtins.input", return_value=answer), \
                    contextlib.redirect_stdout(output):
                code = main(["setup"])
            result = json.loads(output.getvalue())
            return code, result, auth_status, codex_login

    def test_existing_chatgpt_auth_is_reused_and_readiness_is_true(self):
        code, result, _, login = self.run_setup([{"status": "authenticated"}])
        self.assertEqual(code, 0)
        self.assertEqual(result["authentication"]["codex_chatgpt"], "authenticated")
        self.assertEqual(result["readiness"], {"ready": True, "status": "ready"})
        login.assert_not_called()

    def test_successful_native_login_is_verified_before_readiness(self):
        code, result, auth, login = self.run_setup(
            [{"status": "not-authenticated"}, {"status": "authenticated"}],
            login={"status": "completed"}, interactive=True)
        self.assertEqual(code, 0)
        login.assert_called_once()
        self.assertEqual(auth.call_count, 2)
        self.assertEqual(result["authentication"], {"codex_chatgpt": "authenticated", "login": "completed"})
        self.assertTrue(result["readiness"]["ready"])

    def test_failed_login_does_not_claim_readiness(self):
        code, result, _, login = self.run_setup(
            [{"status": "not-authenticated"}, {"status": "not-authenticated"}],
            login={"status": "failed"}, interactive=True)
        self.assertEqual(code, 0)
        login.assert_called_once()
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(result["authentication"]["codex_chatgpt"], "not-authenticated")

    def test_declined_login_is_reported_incomplete(self):
        code, result, _, login = self.run_setup(
            [{"status": "not-authenticated"}], interactive=True, answer="no")
        self.assertEqual(code, 0)
        login.assert_not_called()
        self.assertEqual(result["authentication"]["login"], "declined")
        self.assertFalse(result["readiness"]["ready"])

    def test_interrupted_login_is_reported_incomplete(self):
        code, result, _, login = self.run_setup(
            [{"status": "not-authenticated"}, {"status": "not-authenticated"}],
            login={"status": "interrupted"}, interactive=True)
        self.assertEqual(code, 0)
        login.assert_called_once()
        self.assertEqual(result["authentication"]["login"], "interrupted")
        self.assertFalse(result["readiness"]["ready"])

    def test_missing_required_tool_keeps_final_readiness_incomplete(self):
        code, result, _, login = self.run_setup(
            [{"status": "authenticated"}],
            inventories=[inventory(), inventory(missing=("gh",))])
        self.assertEqual(code, 0)
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(result["readiness"]["status"], "incomplete")
        login.assert_not_called()

    def test_auth_status_discards_raw_output_and_accepts_only_chatgpt(self):
        class Result:
            returncode = 0
            stdout = ""
            stderr = "Logged in using ChatGPT; private-token-must-not-escape"

        with patch("pattyline.onboarding.executable", return_value="fake-codex"), \
                patch("pattyline.onboarding.subprocess.run", return_value=Result()) as run:
            result = codex_auth_status({"runtime": {"codex": "codex"}})
        self.assertEqual(result, {"status": "authenticated"})
        self.assertNotIn("private-token", json.dumps(result))
        run.assert_called_once_with(["fake-codex", "login", "status"], capture_output=True, text=True, timeout=15)


if __name__ == "__main__":
    unittest.main()
