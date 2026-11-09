"""Bounded range downloads of the official pinned Windows wheel, with official SHA256."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
URL = "https://download-r2.pytorch.org/whl/cu128/torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl"
FILENAME = "torch-2.7.1+cu128-cp312-cp312-win_amd64.whl"
SIZE = 3273024349
# Source: official https://download.pytorch.org/whl/cu128/torch/ link hash fragment.
SHA256 = "2bb8c05d48ba815b316879a18195d53a6472a03e297d971e916753f8e1053d30"
CHUNK = 16 * 1024**2


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def get_part(index):
    start = index * CHUNK
    end = min(SIZE, start + CHUNK) - 1
    target = CACHE / "torch_parts" / f"{index:04d}"
    if not target.exists() or target.stat().st_size != end - start + 1:
        subprocess.run(["curl.exe", "-sS", "-L", "--fail", "--retry", "2",
                        "--connect-timeout", "15", "--max-time", "180",
                        "--range", f"{start}-{end}", "-o", str(target), URL], check=True)
    if target.stat().st_size != end - start + 1:
        raise RuntimeError(f"Wrong HTTP range length for part {index}; file retained")
    print(f"part {index} ready", flush=True)
    return target


def main():
    target = CACHE / FILENAME
    (CACHE / "torch_parts").mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != SHA256:
            raise RuntimeError("Existing wheel hash mismatch; file preserved")
    else:
        with ThreadPoolExecutor(max_workers=16) as pool:
            paths = list(pool.map(get_part, range((SIZE + CHUNK - 1) // CHUNK)))
        temporary = target.with_suffix(".download")
        with temporary.open("wb") as output:
            for path in paths:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, output)
        if sha256(temporary) != SHA256:
            raise RuntimeError("Official wheel SHA256 mismatch; temporary retained")
        temporary.replace(target)
    print(f"OFFICIAL SHA256 VERIFIED: {SHA256}", flush=True)


if __name__ == "__main__":
    main()
