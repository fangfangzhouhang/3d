"""Download only the official tiny checkpoint; never replace an existing bad file."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parent


def main():
    source = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    target = ROOT / "weights" / source["checkpoint_file"]
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".download")
        with urllib.request.urlopen(source["checkpoint_url"], timeout=60) as response:
            with temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        with temporary.open("rb") as stream:
            downloaded = hashlib.file_digest(stream, "sha256").hexdigest()
        if source.get("checkpoint_sha256") and downloaded != source["checkpoint_sha256"]:
            raise SystemExit(f"Downloaded checkpoint hash mismatch: {downloaded}; temporary preserved")
        temporary.replace(target)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = source.get("checkpoint_sha256")
    if expected and digest != expected:
        raise SystemExit(f"Checkpoint hash mismatch: {digest}; existing file preserved")
    record = {"source": source["checkpoint_url"], "file": target.name,
              "bytes": target.stat().st_size, "sha256": digest}
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "checkpoint.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
