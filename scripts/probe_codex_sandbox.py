"""Narrow opt-in host probe: dummy files and a local socket, never inference.

Uses only an already-provisioned sandbox. Does not install/elevate or change
personal configuration. A failed denial keeps the production gate closed.
"""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pattyline.common import now, write_json


def main():
    executable = shutil.which("codex")
    if not executable:
        raise SystemExit("Codex unavailable")
    doctor = json.loads(subprocess.run([executable, "doctor", "--json"], capture_output=True, text=True, check=True, timeout=30).stdout)
    details = doctor["checks"].get("sandbox.helpers", {}).get("details", {})
    evidence = {"timestamp": now(), "tool_version": doctor.get("codexVersion"), "sandbox": details,
                "origin": "live-host-probe", "release_ready": False}
    if sys.platform == "win32" and details.get("sandbox provisioning") != "complete":
        evidence["outcome"] = "blocked: sandbox provisioning requires separate human setup consent"
        write_json(ROOT / ".pattyline/evidence/codex-sandbox-probe.json", evidence)
        print(json.dumps(evidence, indent=2))
        return 2
    with tempfile.TemporaryDirectory(prefix="pattyline-probe-") as directory, socket.socket() as server:
        base = Path(directory)
        workspace = base / "workspace"
        workspace.mkdir()
        secret = base / "dummy-secret.txt"
        secret.write_text("dummy; no real credential")
        protected = workspace / "approved.txt"
        protected.write_text("approved-input")
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        program = '''import json, pathlib, socket, sys
result = {}
try:
    pathlib.Path('allowed.txt').write_text('allowed')
    result['workspace_write'] = True
except OSError:
    result['workspace_write'] = False
try:
    pathlib.Path('approved.txt').write_text('tampered')
    result['protected_write_denied'] = False
except OSError:
    result['protected_write_denied'] = True
try:
    pathlib.Path(sys.argv[1]).read_text()
    result['credential_read_denied'] = False
except OSError:
    result['credential_read_denied'] = True
try:
    connection = socket.create_connection(('127.0.0.1', int(sys.argv[2])), timeout=2)
    connection.close()
    result['network_denied'] = False
except OSError:
    result['network_denied'] = True
print(json.dumps(result))
'''
        profile = {"extends": ":workspace", "filesystem": {":minimal": "read",
                   ":workspace_roots": {".": "write", "approved.txt": "read"},
                   str(secret): "deny", str(Path(sys.executable).parent): "read"}, "network": {"enabled": False}}
        from pattyline.config import toml_text
        # Inline TOML profile prevents a persistent change to a personal config file.
        inline = toml_text({"profile": profile}).split(" = ", 1)[1].strip()
        argv = [executable, "sandbox", "-P", "pattyline-probe", "-C", str(workspace),
                "-c", "permissions.pattyline-probe=" + inline,
                "--", sys.executable, "-I", "-c", program, str(secret), str(port)]
        try:
            response = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30)
            evidence.update({"exit_status": response.returncode, "stdout": response.stdout[-5000:], "stderr": response.stderr[-5000:],
                             "command": [*argv[:8], "<inline dummy-only profile>", "<dummy denial probe>"]})
            try:
                results = json.loads(response.stdout)
            except ValueError:
                results = {}
            evidence["results"] = results
            required = ("workspace_write", "protected_write_denied", "credential_read_denied", "network_denied")
            evidence["outcome"] = "narrow-denials-passed" if response.returncode == 0 and all(results.get(key) is True for key in required) else "blocked: required denial not demonstrated"
        except subprocess.TimeoutExpired:
            evidence["outcome"] = "blocked: sandbox probe timeout; inspect helper processes before retry"
        evidence["limitations"] = ["Dummy-file/socket probe only; no model execution, process crash recovery or full release scenario"]
    write_json(ROOT / ".pattyline/evidence/codex-sandbox-probe.json", evidence)
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["outcome"] == "narrow-denials-passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
