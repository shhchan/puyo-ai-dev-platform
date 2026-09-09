"""Run both config conditions sequentially against the immutable baseline."""

import argparse
import os
import subprocess
from pathlib import Path

import puyo241_risk_weight_trial as trial
from eval import deep_chain_builder_benchmark as baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    cases, checksums = [], {}
    for seed, turn in trial.FIXED_BOARDS:
        source = args.diagnostics / f"seed-{seed}-all-roots.json.gz"
        data = baseline._read_json(source)
        assert data["build_provenance"]["evaluated_commit"] == trial.SOURCE_COMMIT
        cases.append({"seed": seed, "turn": turn, "request_hex": data["cases"][turn]["request_hex"]})
        checksums[source.name] = baseline.file_sha256(source)
    path = args.output / "fixed-inputs.json"
    if path.exists():
        raise ValueError("output must be new")
    baseline._write_json(path, {"cases": cases, "source_sha256": checksums})

    def invoke(command, condition, *extra):
        subprocess.run([
            str(args.baseline / ".venv/bin/python"), str(Path(trial.__file__).resolve()),
            command, str(args.output), condition, *map(str, extra),
        ], cwd=args.baseline, env={**os.environ, "PYTHONPATH": str(args.baseline)}, check=True)

    for condition in trial.CONDITIONS:
        invoke("init", condition)
    for seed in trial.SEEDS:
        for repeat in (1, 2):
            for condition in trial.CONDITIONS:
                invoke("worker", condition, "--seed", seed, "--repeat", repeat)
    for repeat in (1, 2, 3):
        for condition in trial.CONDITIONS:
            invoke("fixed", condition, "--repeat", repeat)


if __name__ == "__main__":
    main()
