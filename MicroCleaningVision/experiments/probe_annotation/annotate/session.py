"""Persistent wall timer: start before choosing points, record revisions, finish on export."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .export import digest, write_json


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def start(path, image, actor):
    if path.exists():
        raise FileExistsError(path)
    value = {"schema": "point-annotation-session-v1", "actor": actor,
             "image_sha256": digest(image), "started_utc": utc_now(),
             "started_monotonic_ns": time.monotonic_ns(), "status": "selecting_points",
             "timing_scope": "elapsed wall time from start before point selection through final export; includes review, revisions, process/model loading and waiting; not human active click time",
             "events": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
    return value


def record(path, config):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["status"] == "exported":
        raise ValueError("completed session cannot accept revisions")
    data = json.loads(config.read_text(encoding="utf-8"))
    event = {"utc": utc_now(), "elapsed_seconds": elapsed(value),
             "config_sha256": digest(config), "config": data}
    value["events"].append(event)
    write_json(path, value)
    return value


def elapsed(value):
    seconds = (time.monotonic_ns() - value["started_monotonic_ns"]) / 1e9
    # Reboot/clock-origin changes must not create bogus durations.
    wall = (datetime.now(timezone.utc) - datetime.fromisoformat(value["started_utc"])).total_seconds()
    if seconds < 0 or abs(wall - seconds) > 5:
        raise ValueError("timer clock discontinuity; do not report this session as comparable")
    return seconds


def resume(path, reason, archived_output):
    value = json.loads(path.read_text(encoding="utf-8"))
    archived = json.loads((archived_output/"session.json").read_text(encoding="utf-8"))
    if value["status"] != "exported" or not reason.strip() or archived != value:
        raise ValueError("resume requires completed session, a reason, and exact archived prior export")
    value.setdefault("export_history",[]).append({"output":value["output"],
        "archived_output":str(archived_output),"exported_utc":value["exported_utc"],
        "elapsed_seconds":value["elapsed_seconds"],"resume_reason":reason,"resumed_utc":utc_now()})
    value["status"] = "selecting_points"
    for key in ("exported_utc","elapsed_seconds","output"):
        value.pop(key,None)
    write_json(path,value)
    return value


def finish(path, config, image, output):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["status"] == "exported" or value["image_sha256"] != digest(image):
        raise ValueError("completed session or different image")
    if not value["events"] or value["events"][-1]["config_sha256"] != digest(config):
        raise ValueError("record the current point revision before final export")
    value.update(status="exported", exported_utc=utc_now(),
                 elapsed_seconds=elapsed(value), output=str(output))
    write_json(path, value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("start")
    a.add_argument("--session", type=Path, required=True)
    a.add_argument("--image", type=Path, required=True)
    a.add_argument("--actor", required=True)
    a = sub.add_parser("record")
    a.add_argument("--session", type=Path, required=True)
    a.add_argument("--config", type=Path, required=True)
    a = sub.add_parser("resume")
    a.add_argument("--session", type=Path, required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("--archived-output",type=Path,required=True)
    args = p.parse_args()
    if args.command == "start":
        value = start(args.session,args.image,args.actor)
    elif args.command == "record":
        value = record(args.session,args.config)
    else:
        value = resume(args.session,args.reason,args.archived_output)
    print(json.dumps({k: v for k, v in value.items() if k != "events"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
