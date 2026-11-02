"""Offline Linux-container checks against a locally present immutable image."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pattyline.common import now, write_json


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("sha256:"):
        raise SystemExit("Supply a locally present immutable Python tool image ID; no images are pulled")
    modules = ["tests.test_config", "tests.test_broker", "tests.test_process", "tests.test_initialize", "tests.test_release", "tests.test_package",
               "tests.test_cli.CliTests.test_real_cli_subprocess_help_has_all_required_commands"]
    argv = ["docker", "run", "--rm", "--pull", "never", "--init", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g", "--user", "1000:1000",
            "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=512m", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,source={ROOT},target=/source,readonly", "--workdir", "/source",
            "--entrypoint", "python", sys.argv[1], "-m", "unittest", *modules, "-v"]
    response = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    evidence = {"time": now(), "origin": "real-linux-container", "image": sys.argv[1], "argv": argv,
                "exit_status": response.returncode, "stdout": response.stdout, "stderr": response.stderr,
                "release_ready": False, "limitations": ["Image has no Git; Git/SQLite/scheduler CLI fixture scenarios require a suitable approved tool image or native Linux host", "No live model or GitHub evidence", "Windows-host Linux container, not full native-host release matrix"]}
    write_json(ROOT / ".pattyline/evidence/linux-offline.json", evidence)
    print(response.stderr[-12000:])
    print("Linux-container evidence recorded; complete release remains blocked.")
    return response.returncode


if __name__ == "__main__":
    raise SystemExit(main())
