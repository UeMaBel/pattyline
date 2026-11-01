"""Dependency-free PEP 517 wheel/sdist backend for this pure Python package."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import tarfile
import zipfile

NAME = "pattyline-0.1.0"
INFO = f"{NAME}.dist-info"
ROOT = Path(__file__).parent


def metadata():
    return ("Metadata-Version: 2.1\nName: pattyline\nVersion: 0.1.0\n"
            "Summary: Human-approved local and cloud ticket orchestration\n"
            "Requires-Python: >=3.11\nLicense: MIT\n\n" +
            (ROOT / "README.md").read_text(encoding="utf-8")).encode()


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    target = Path(wheel_directory) / f"{NAME}-py3-none-any.whl"
    files = {p.relative_to(ROOT).as_posix(): p.read_bytes()
             for p in (ROOT / "pattyline").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts}
    files["pattyline/resources/operator-guide.md"] = (ROOT / "docs/pattyline/v1/operator-guide.md").read_bytes()
    files.update({f"{INFO}/METADATA": metadata(),
                  f"{INFO}/WHEEL": b"Wheel-Version: 1.0\nGenerator: pattyline\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                  f"{INFO}/entry_points.txt": b"[console_scripts]\npattyline = pattyline.cli:main\n",
                  f"{INFO}/licenses/LICENSE": (ROOT / "LICENSE").read_bytes()})
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as wheel:
        for name, data in sorted(files.items()):
            wheel.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            writer.writerow([name, f"sha256={digest}", len(data)])
        writer.writerow([f"{INFO}/RECORD", "", ""])
        wheel.writestr(f"{INFO}/RECORD", record.getvalue())
    return target.name


def build_sdist(sdist_directory, config_settings=None):
    target = Path(sdist_directory) / f"{NAME}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for pattern in ("pattyline/**/*", "tests/**/*.py", "pyproject.toml", "build_backend.py", "README.md", "LICENSE", "docs/pattyline/v1/operator-guide.md"):
            for path in ROOT.glob(pattern):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.add(path, arcname=f"{NAME}/{path.relative_to(ROOT).as_posix()}")
    return target.name
