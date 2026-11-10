"""Verify protected bytes, original untracked files and strict Git change boundary."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
PREFIX = ROOT.relative_to(REPO).as_posix() + "/"


def main():
    before = json.loads((ROOT / "evidence/protected_before.json").read_text(encoding="utf-8"))
    changed = [name for name, digest in before["sha256"].items()
               if not (REPO / name).is_file() or hashlib.sha256((REPO / name).read_bytes()).hexdigest() != digest]
    now = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=REPO).decode("utf-8").split("\0")
    outside = sorted(name for name in now if name and not name.startswith(PREFIX))
    git_changed = subprocess.check_output(["git", "diff", "--name-only", before["base_commit"], "--"], cwd=REPO).decode("utf-8").splitlines()
    git_outside = [name for name in git_changed if not name.startswith(PREFIX)]
    result = {"base_commit": before["base_commit"], "protected_file_count": len(before["sha256"]),
              "changed_protected_files": changed, "tracked_changes_outside_new_directory": git_outside,
              "existing_untracked_count": len(before["existing_untracked"]),
              "original_untracked_set_unchanged": outside == sorted(before["existing_untracked"]),
              "success": not changed and not git_outside and outside == sorted(before["existing_untracked"])}
    (ROOT / "evidence/scope_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
