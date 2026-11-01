from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pattyline import config
from pattyline.common import Blocked


class ConfigTests(unittest.TestCase):
    def test_init_snapshots_and_refreshes_selected_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            global_path = root / "global.toml"
            global_path.write_text(config.toml_text({"runtime": {"cloud_model": "first"}}))
            with patch("pattyline.config.global_path", return_value=global_path):
                first = config.initialize(root, True)
                self.assertEqual(first["runtime"]["cloud_model"], "first")
                global_path.write_text(config.toml_text({"runtime": {"cloud_model": "second"}}))
                self.assertEqual(config.initialize(root, True)["runtime"]["cloud_model"], "first")
                self.assertIn("runtime", config.refresh(root))
                config.refresh(root, ["runtime"])
                self.assertEqual(config.load(root)["runtime"]["cloud_model"], "second")

    def test_roundtrip_and_platform_commands(self):
        self.assertEqual(__import__("tomllib").loads(config.toml_text(config.DEFAULTS)), config.DEFAULTS)
        with patch("platform.system", return_value="Windows"):
            command = config.command_for({"windows": {"argv": ["py", "-3.12", "-m", "unittest"], "timeout": 3}})
            self.assertEqual(command["argv"][0], "py")
        with self.assertRaises(Blocked):
            config.command_for({"argv": "echo success", "timeout": 3})
        with self.assertRaises(Blocked):
            config.command_for({"argv": ["test"], "timeout": 3, "cwd": "../escape"})
        command = config.command_for({"linux": {"argv": ["python3", "-m", "unittest"], "timeout": 10}, "windows": {"argv": ["py", "-m", "unittest"], "timeout": 10}}, execution_platform="linux")
        self.assertEqual(command["argv"][0], "python3")

    def test_limits_cannot_reset_or_allow_unbounded_repairs(self):
        values = deepcopy(config.DEFAULTS)
        values["limits"]["repairs"] = 3
        with self.assertRaises(Blocked):
            config.validate(values)
