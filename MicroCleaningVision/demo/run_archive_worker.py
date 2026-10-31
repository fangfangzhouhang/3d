"""File-only archive subprocess; the parent holds the source task lock.

P1 owns snapshot copying, hashes and atomic publication. A user's retention
receipt is stored separately; neither source nor immutable archive is edited.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


class PublishedArchiveNotVerified(RuntimeError):
    def __init__(self, path, cause):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.archive_path = path


def export_and_verify(folder, data_root, selection=None):
    from microcleaning.data_learning.run_archive import RunArchiveExporter, verify_archive
    if selection is not None:
        if not isinstance(selection, dict) or not all(
            isinstance(selection.get(k), str) and selection[k].strip() for k in ("purpose", "reason")
        ):
            raise ValueError("RETENTION_PURPOSE_AND_REASON_REQUIRED")
    result = RunArchiveExporter(data_root).export_run(folder)
    try:
        verify_archive(result["path"])
        if selection is not None:
            root = Path(data_root).resolve()
            receipts = root / "selection_records" / result["archive_id"]
            if not receipts.resolve().is_relative_to(root):
                raise ValueError("RETENTION_RECEIPT_OUTSIDE_ROOT")
            receipts.mkdir(parents=True, exist_ok=True)
            name = uuid4().hex
            pending = receipts / (name + ".tmp")
            final = receipts / (name + ".json")
            record = {"schema": "microcleaning-retention-v1", "archive_id": result["archive_id"],
                "source_folder": str(Path(folder).resolve()), "training_eligibility_override": False,
                **{k: selection.get(k) for k in ("purpose", "reason", "destination_kind", "operator", "decided_at")}}
            with pending.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(pending, final)
            result["selection_ref"] = str(final)
    except BaseException as exc:
        raise PublishedArchiveNotVerified(result["path"], exc) from exc
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--selection", help="Explicit retention decision, as a JSON object")
    args = parser.parse_args(argv)
    try:
        result = export_and_verify(args.run, args.data_root,
            json.loads(args.selection) if args.selection is not None else None)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True), flush=True)
        return 0
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
            "path": getattr(exc, "archive_path", None)}, ensure_ascii=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
