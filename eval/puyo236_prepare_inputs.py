"""Derive independent fixed requests from immutable prior experimental evidence."""

import argparse
from pathlib import Path

from eval import deep_chain_builder_benchmark as baseline


def prepare(previous, output):
    path = output / "fixed-inputs.json"
    if path.exists():
        raise ValueError(f"Refusing to overwrite {path}")
    selected = ((123, 29), (126, 22), (130, 7), (130, 24), (133, 38), (135, 10),
                (135, 11), (135, 33), (138, 30), (147, 7), (147, 27), (151, 31))
    cases = []
    for seed, turn in selected:
        source = previous / "puyo-240-evidence" / f"seed-{seed}-all-roots.json.gz"
        original = baseline._read_json(source)
        case = original["cases"][turn]
        assert original["seed"] == seed and case["turn"] == turn
        cases.append({"seed": seed, "turn": turn, "request_hex": case["request_hex"],
                      "source": str(source), "source_sha256": baseline.file_sha256(source)})
    baseline._write_json(path, {"ticket": "PUYO-236", "turn_index": "zero_based", "cases": cases})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.previous, args.output)
