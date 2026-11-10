"""Run only this experiment's tests, with UTF-8 logs and machine-readable status."""
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="tests_final")
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        parser.error("name must be a basename")
    directory = ROOT / "evidence"
    directory.mkdir(exist_ok=True)
    started = time.perf_counter()
    with (directory / (args.name + ".log")).open("w", encoding="utf-8") as stream:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test*.py")
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    payload = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "seconds": time.perf_counter() - started,
               "success": result.wasSuccessful(), "device": "cpu", "inputs": "synthetic fixtures; no GPU smoke claim",
               "log": str((directory / (args.name + ".log")).relative_to(ROOT))}
    (directory / (args.name + ".json")).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
