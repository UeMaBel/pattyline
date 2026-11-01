"""Release evidence inventory; unit fixtures cannot satisfy live/platform gates."""
from importlib.resources import files
import json
from pathlib import Path

from .common import load_json


def release_status(root):
    matrix = json.loads((files("pattyline") / "resources/release-matrix.json").read_text(encoding="utf-8"))
    evidence = [load_json(path) for path in sorted((Path(root) / ".pattyline/release").glob("*.json"))]
    profiles = {}
    for profile in matrix["profiles"]:
        records = [record for record in evidence if record.get("profile") == profile]
        complete = [record for record in records if all(key in record for key in matrix["required_fields"])
                    and record["outcome"] == "passed" and record.get("origin") == "live"
                    and record["evidence_paths"] and not record["limitations"]
                    and all(Path(root, path).is_file() for path in record["evidence_paths"])]
        profiles[profile] = "evidence-present-needs-release-validation" if complete else "blocked: required real evidence unavailable"
    # Presence is not semantic validation. No imported JSON can assert its own success.
    return {"release_ready": False, "profiles": profiles,
            "reason": "Real denial/race/platform evidence and production enforcement must be validated before enabling execution"}
