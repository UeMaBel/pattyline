import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pattyline.common import Blocked
from pattyline.process import process_identity, reconcile_process, run, sanitized_env


class ProcessTests(unittest.TestCase):
    def test_environment_excludes_credentials_and_user_configuration(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "OPENAI_API_KEY": "secret", "CUSTOM_SECRET": "secret", "PYTHONPATH": "host"}):
            env = sanitized_env("isolated")
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("CUSTOM_SECRET", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual(env["PATTYLINE_WORKER"], "1")

    def test_actual_process_timeout_stops_spawned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            child_file = Path(directory) / "child.json"
            script = ("import subprocess,sys,time,json; "
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                      "open(sys.argv[1],'w').write(json.dumps({'pid':p.pid})); time.sleep(60)")
            records = []
            result = run([sys.executable, "-c", script, str(child_file)], directory, timeout=1, grace=1,
                         on_start=lambda record: records.append(record))
            self.assertEqual(result["outcome"], "timeout")
            self.assertEqual(len(records), 1)
            self.assertIsNone(process_identity(records[0]["pid"]))
            child_pid = json.loads(child_file.read_text())["pid"]
            if os.name == "nt":
                self.assertIsNone(process_identity(child_pid))
            else:
                stat = Path(f"/proc/{child_pid}/stat")
                self.assertTrue(not stat.exists() or stat.read_text().split(")", 1)[1].split()[0] == "Z")

    def test_cancel_is_bounded_and_ownership_events_are_emitted(self):
        with tempfile.TemporaryDirectory() as directory:
            start = time.monotonic()
            stopped = []
            result = run([sys.executable, "-c", "import time; time.sleep(60)"], directory, timeout=10,
                         cancelled=lambda: time.monotonic() - start > 0.3, grace=1,
                         on_stop=lambda record: stopped.append(record))
            self.assertEqual(result["outcome"], "cancelled")
            self.assertLess(time.monotonic() - start, 5)
            self.assertEqual(len(stopped), 1)

    def test_pid_reuse_or_missing_parent_never_proves_descendant_termination(self):
        with patch("pattyline.process.process_identity", return_value="different-start"):
            self.assertIn("uncertain", reconcile_process({"pid": 123, "identity": "original"}))
        with patch("pattyline.process.process_identity", return_value=None):
            self.assertIn("uncertain", reconcile_process({"pid": 123, "identity": "original"}))

    def test_real_process_identity_is_stable(self):
        first = process_identity(os.getpid())
        self.assertTrue(first)
        self.assertEqual(first, process_identity(os.getpid()))
