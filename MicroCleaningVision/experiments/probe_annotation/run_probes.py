"""Run every configured offline probe while retaining blocked/failed records."""

import argparse

from probes.runner import run_config
from probes.runner import ROOT
from probes.comparison import write_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stage2.json", help="JSON config relative to the experiment directory")
    parser.add_argument("--output", help="New output directory relative to the experiment directory; never overwritten")
    args = parser.parse_args()
    summary = run_config(args.config, output_override=args.output)
    write_comparison(ROOT, ROOT / summary['output_root'], summary)
    counts = summary["status_counts"]
    print(f"Completed batch: {counts}; output={summary['output_root']}")
    # Blocked prerequisites are a recorded result. Actual failures remain visible
    # to automation, but only after every configured job has been attempted.
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
