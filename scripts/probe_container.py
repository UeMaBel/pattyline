"""Explicit local-image-only container denial probe; never pulls/runs app entrypoints."""
from copy import deepcopy
import json
from pathlib import Path
import platform
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pattyline.common import Blocked, now, write_json
from pattyline.config import DEFAULTS
from pattyline.sandbox import Container


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_container.py sha256:<approved locally present tool image ID>")
    values = deepcopy(DEFAULTS)
    values["runtime"]["container_image"] = sys.argv[1]
    boundary = Container(values)
    with tempfile.TemporaryDirectory(prefix="pattyline-container-") as directory:
        base = Path(directory)
        workspace, primary = base / "workspace", base / "primary"
        workspace.mkdir()
        primary.mkdir()
        (workspace / "approved.txt").write_text("immutable")
        (primary / "dummy-secret.txt").write_text("dummy credential")
        program = '''import pathlib,socket,os,json
r={}
pathlib.Path('output.txt').write_text('container proof')
r['workspace_write']=True
try:
 pathlib.Path('approved.txt').write_text('bad');r['protected_write_denied']=False
except OSError:r['protected_write_denied']=True
try:
 socket.create_connection(('1.1.1.1',443),timeout=1);r['external_network_denied']=False
except OSError:r['external_network_denied']=True
try:
 socket.create_connection(('host.docker.internal',11434),timeout=1);r['host_network_denied']=False
except OSError:r['host_network_denied']=True
r['orchestrator_secrets_absent']=not any(k in os.environ for k in ('OPENAI_API_KEY','GH_TOKEN','GITHUB_TOKEN'))
r['host_primary_not_mounted']=not pathlib.Path('/primary/dummy-secret.txt').exists()
print(json.dumps(r))
'''
        states = []
        evidence = boundary.execute({"argv": ["python", "-I", "-c", program], "cwd": ".", "timeout": 20}, workspace, primary,
                                    ["approved.txt"], on_start=lambda record: states.append(dict(record)), on_stop=lambda record: states.append(dict(record)))
        result = json.loads(evidence["stdout"]) if evidence["exit_code"] == 0 else {}
        passed = bool(result) and all(value is True for value in result.values()) and (workspace / "approved.txt").read_text() == "immutable"
        value = {"time": now(), "os": platform.platform(), "image": sys.argv[1], "origin": "live-container-probe", "results": result,
                 "evidence": evidence, "ownership": states, "outcome": "narrow-denials-passed" if passed else "blocked: required denial not demonstrated",
                 "release_ready": False, "limitations": ["Windows-host Linux container only; no native Linux, live models, full crash/recovery or GitHub release scenario"]}
        write_json(ROOT / ".pattyline/evidence/container-probe.json", value)
        print(json.dumps({k: v for k, v in value.items() if k not in {"evidence", "ownership"}}, indent=2))
        return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
