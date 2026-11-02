import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pattyline import config
from pattyline.cli import main


class GlobalSetupTests(unittest.TestCase):
    def test_global_settings_path_resolves_windows_and_linux_user_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            appdata = root / "windows-roaming"
            xdg = root / "linux-config"

            windows_path = config._global_path("nt", {"APPDATA": str(appdata)}, root)
            linux_path = config._global_path("posix", {"XDG_CONFIG_HOME": str(xdg)}, root)

            self.assertEqual(windows_path, appdata / "pattyline/config.toml")
            self.assertEqual(linux_path, xdg / "pattyline/config.toml")

    def test_cli_initializes_user_settings_without_reading_or_changing_project_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "outside-project"
            project.mkdir()
            project_config = project / "pattyline.toml"
            project_config.write_text("not valid TOML =", encoding="utf-8")
            project_state = project / ".pattyline"
            project_state.mkdir()
            sentinel = project_state / "keep.txt"
            sentinel.write_text("leave me alone", encoding="utf-8")
            appdata = root / "appdata"
            xdg = root / "xdg"
            environment = {"APPDATA": str(appdata), "XDG_CONFIG_HOME": str(xdg)}
            output = io.StringIO()
            inventory = {"platform": "test", "tools": {
                name: {"path": f"fake-{name}", "version": "1.0.0", "latest_version": "1.0.0",
                       "source": "official", "presence": "installed", "update": "current"}
                for name in ("git", "gh", "codex")}}
            with patch.dict(os.environ, environment), \
                    patch("pattyline.onboarding.tool_inventory", return_value=inventory) as tool_inventory, \
                    patch("pattyline.onboarding.codex_auth_status", return_value={"status": "authenticated"}), \
                    patch("pattyline.onboarding.local_inventory", side_effect=AssertionError("local setup is deferred")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(["--project", str(project), "setup"]), 0)

            settings_path = appdata / "pattyline/config.toml" if os.name == "nt" else xdg / "pattyline/config.toml"
            settings = settings_path.read_text(encoding="utf-8")
            self.assertEqual(project_config.read_text(encoding="utf-8"), "not valid TOML =")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "leave me alone")
            self.assertEqual(sorted(path.name for path in project.iterdir()), [".pattyline", "pattyline.toml"])
            self.assertIn('"cloud_model" = "gpt-6.1-sol"', settings)
            self.assertIn('"adapter" = "broker"', settings)
            self.assertEqual(tool_inventory.call_count, 2)
            self.assertEqual(tool_inventory.call_args.args[0]["runtime"]["cloud_model"], "gpt-6.1-sol")
            result = json.loads(output.getvalue())
            self.assertEqual(result["global_settings"], {"path": str(settings_path), "status": "initialized"})
            self.assertEqual(result["readiness"], {"ready": True, "status": "ready"})

    def test_repeated_setup_preserves_edited_global_settings_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            appdata = root / "appdata"
            xdg = root / "xdg"
            settings_path = appdata / "pattyline/config.toml" if os.name == "nt" else xdg / "pattyline/config.toml"
            settings_path.parent.mkdir(parents=True)
            original = '"runtime" = { "cloud_model" = "my-model", "adapter" = "broker" }\n# user note\n'
            settings_path.write_text(original, encoding="utf-8")
            output = io.StringIO()
            inventory = {"platform": "test", "tools": {
                name: {"path": f"fake-{name}", "version": "1.0.0", "latest_version": "1.0.0",
                       "source": "official", "presence": "installed", "update": "current"}
                for name in ("git", "gh", "codex")}}
            with patch.dict(os.environ, {"APPDATA": str(appdata), "XDG_CONFIG_HOME": str(xdg)}), \
                    patch("pattyline.onboarding.tool_inventory", return_value=inventory), \
                    patch("pattyline.onboarding.codex_auth_status", return_value={"status": "authenticated"}), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(["--project", str(root / "not-a-project"), "setup"]), 0)

            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(output.getvalue())["global_settings"]["status"], "preserved")


if __name__ == "__main__":
    unittest.main()
