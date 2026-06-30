"""Standalone replay diagnostics and model lineage viewer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.model_viewer import build_model_viewer_data, run_model_viewer


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="View replay diagnostics and model lineage.")
    parser.add_argument("--replay", type=Path, help="Realtime replay JSON to inspect.")
    parser.add_argument(
        "--lineage-root",
        action="append",
        default=[],
        help="Run root or manifest path to include in the lineage registry.",
    )
    parser.add_argument("--report-json", type=Path, help="Write a headless viewer report as JSON.")
    parser.add_argument("--report-markdown", type=Path, help="Write a headless viewer report as Markdown.")
    parser.add_argument(
        "--model-registry",
        type=Path,
        default=Path("runs/model_registry.json"),
        help="Model role registry containing champion/challenger/previous stable status.",
    )
    parser.add_argument("--max-frames", type=int, help="Stop after this many rendered frames.")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    data = build_model_viewer_data(
        replay_path=args.replay,
        lineage_roots=tuple(args.lineage_root),
        model_registry_path=args.model_registry,
    )
    report = run_model_viewer(
        data,
        max_frames=args.max_frames,
        report_json=args.report_json,
        report_markdown=args.report_markdown,
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
