"""Alternate fresh baseline/candidate processes; no concurrent heavy work."""

import argparse
import os
import subprocess
from pathlib import Path

import puyo242_known_prefix_trial as trial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    checkouts = {"baseline": args.baseline, "candidate": args.candidate}

    def invoke(command, condition, *extra):
        checkout = checkouts[condition]
        subprocess.run([
            str(checkout / ".venv/bin/python"), str(Path(trial.__file__).resolve()),
            command, str(args.output), condition, *map(str, extra),
        ], cwd=checkout, env={**os.environ, "PYTHONPATH": str(checkout)}, check=True)

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
