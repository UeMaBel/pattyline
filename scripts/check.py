"""Offline check catalog. Exit 2 when required real evidence is unavailable."""
import argparse
import compileall
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=["all", "static", "release-live"])
    args = parser.parse_args()
    if args.profile == "release-live":
        from pattyline.release import release_status
        import json
        print(json.dumps(release_status(ROOT), indent=2))
        return 2
    if args.profile == "static":
        success = compileall.compile_dir(ROOT / "pattyline", quiet=1) and compileall.compile_dir(ROOT / "tests", quiet=1)
        bad = []
        for directory in (ROOT / "pattyline", ROOT / "tests", ROOT / "scripts"):
            for path in directory.rglob("*.py"):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.rstrip() != line or "\t" in line:
                        bad.append(f"{path.relative_to(ROOT)}:{number}: trailing whitespace or tabs")
        print("\n".join(bad) if bad else "Compilation and whitespace checks passed")
        return 0 if success and not bad else 1
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    if suite.countTestCases() == 0:
        raise RuntimeError("Empty test suites cannot satisfy a check")
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
