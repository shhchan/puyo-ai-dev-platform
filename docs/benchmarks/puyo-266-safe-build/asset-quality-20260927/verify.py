"""Verify the archived cohort and recompute G2 without running policy search."""

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from eval.nextgen_gates import evaluate
from eval.nextgen_safe_build_gate import cohort_row, load_manifest, read


def main():
    for line in (HERE / "checksums.sha256").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        path = HERE / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"checksum mismatch: {relative}")
    cohort = HERE / "native-g2"
    manifest = load_manifest(cohort)
    saved = read(cohort / "report.json")
    rows = []
    for filename, expected in saved["artifacts"].items():
        path = cohort / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"cohort artifact mismatch: {filename}")
        rows.append(cohort_row(read(path), manifest))
    actual = evaluate(rows=rows, contract=manifest["config"])
    for key in ("safe_build", "gates", "observed_quality", "safe_build_performance", "long_training_allowed"):
        if actual[key] != saved[key]:
            raise ValueError(f"report mismatch: {key}")
    print(f"Verified {len(rows)} identities; G2={actual['gates']['G2']['status']}; quality={actual['observed_quality']['status']}")


if __name__ == "__main__":
    main()
