import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pattyline.cli import main
from tests.support import Project


class CliTests(unittest.TestCase):
    def test_public_init_is_idempotent_and_noninteractive_commands_are_actionable(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in {"CODEX_THREAD_ID", "PATTYLINE_WORKER"}}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--project", directory, "init", "--defaults", "independent"]), 0)
                first = (Path(directory) / "pattyline.toml").read_text()
                self.assertEqual(main(["--project", directory, "init"]), 0)
                self.assertEqual(first, (Path(directory) / "pattyline.toml").read_text())
                self.assertEqual(main(["--project", directory, "init", "--set", 'runtime={"cloud_model":"chosen-model"}']), 0)
                from pattyline.config import load
                self.assertEqual(load(directory)["runtime"]["cloud_model"], "chosen-model")
                self.assertEqual(load(directory)["runtime"]["adapter"], "broker")
                self.assertEqual(main(["--project", directory, "validate", "--scope", "config"]), 0)
                self.assertEqual(main(["--project", directory, "prepare"]), 2)
            self.assertIn("feature slug", output.getvalue())

    def test_yolo_does_not_bypass_readiness_or_approval(self):
        project = Project()
        self.addCleanup(project.close)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--project", str(project.root), "start", "feature/01", "--yolo"]), 2)
        self.assertIn("not ready-for-agent", output.getvalue())
        self.assertFalse((project.root / ".pattyline/reports").exists())

    def test_real_cli_subprocess_help_has_all_required_commands(self):
        result = subprocess.run([sys.executable, "-m", "pattyline", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        for command in ("setup", "init", "prepare", "approve", "config", "validate", "plan", "start", "status", "resume", "cancel", "clean"):
            self.assertIn(command, result.stdout)
