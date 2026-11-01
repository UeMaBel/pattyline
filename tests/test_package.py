from pathlib import Path
import subprocess
import tempfile
import unittest
import venv
import zipfile

import build_backend


class PackageTests(unittest.TestCase):
    def test_wheel_installs_offline_in_isolated_environment_with_all_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / build_backend.build_wheel(directory)
            with zipfile.ZipFile(wheel) as archive:
                paths = set(archive.namelist())
                for name in ("grilling", "domain-modeling", "to-spec", "to-tickets", "triage", "handoff", "preparation-review"):
                    self.assertIn(f"pattyline/resources/workflow/{name}/SKILL.md", paths)
                self.assertIn("pattyline/resources/workflow/templates.md", paths)
                self.assertIn("pattyline/resources/LICENSE.txt", paths)
                self.assertIn("pattyline/resources/NOTICE.md", paths)
                self.assertIn("pattyline/resources/release-matrix.json", paths)
                self.assertIn("pattyline/resources/operator-guide.md", paths)
                self.assertFalse(any("__pycache__" in name for name in paths))
            environment = root / "environment"
            venv.create(environment, with_pip=True)
            executable = environment / ("Scripts/python.exe" if __import__('os').name == "nt" else "bin/python")
            installed = subprocess.run([str(executable), "-m", "pip", "install", "--no-index", str(wheel)], capture_output=True, text=True, cwd=root, timeout=60)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            result = subprocess.run([str(executable), "-I", "-m", "pattyline", "--help"], capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("prepare", result.stdout)
            script = environment / ("Scripts/pattyline.exe" if __import__('os').name == "nt" else "bin/pattyline")
            result = subprocess.run([str(script), "--version"], capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(result.stdout.strip(), "0.1.0")
            result = subprocess.run([str(executable), "-I", "-c", "from pattyline.preparation import bundle; assert 'grilling' in bundle('interview')"], capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
