#!/usr/bin/env python3
"""Check TeX references, pinned Git sources, and worked numerical examples."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent


def main() -> None:
    text = "\n".join(p.read_text() for p in sorted(ROOT.glob("*.tex")))
    labels = re.findall(r"\\label\{([^}]+)\}", text)
    duplicates = [label for label, count in Counter(labels).items() if count > 1]
    refs = re.findall(r"\\(?:eqref|ref)\{([^}]+)\}", text)
    missing = sorted(set(refs) - set(labels))
    assert not duplicates and not missing, (duplicates, missing)
    cites = {key for group in re.findall(r"\\cite\{([^}]+)\}", text)
             for key in group.split(",")}
    assert cites <= set(re.findall(r"\\bibitem\{([^}]+)\}", text))
    equations = re.findall(r"\\begin\{(equation|align)\}(.*?)\\end\{\1\}", text, re.S)
    assert all(r"\label{" in body for _, body in equations), "Unlabelled numbered equation"
    assert not re.search(r"\\tag\{|\\setcounter\{equation\}", text), "Manual equation number"
    manifest = json.loads((ROOT / "source-map.json").read_text())
    sha = manifest["source_commit"]
    assert sha in text
    for name, expected in manifest["files"].items():
        data = subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == expected, name
    linked = set(re.findall(r"\\src(?:ja)?\{([^}]+)\}", text))
    assert linked <= set(manifest["files"]), linked - set(manifest["files"])
    # Independent arithmetic invariants used by the worked examples.
    score = 10 * 4 * 1 + 10 * 4 * 8
    assert score == 360 and divmod(score, 70) == (5, 10)
    generated, incoming = (10, 8), (4, 1)
    remaining = [g - min(g, q) for g, q in zip(generated, incoming)]
    assert tuple(r - min(remaining) for r in remaining) == (0, 1)
    gamma = 0.99
    assert math.isclose(gamma**0.5 * gamma**2, gamma**2.5)
    # Nonzero potentials cancel even when holding times differ.
    potentials = (0.4, -0.2, 0.0)
    discounts = (gamma**0.5, gamma**2)
    shaped = [discounts[i] * potentials[i + 1] - potentials[i] for i in range(2)]
    assert math.isclose(shaped[0] + discounts[0] * shaped[1], -potentials[0])
    mask = (True, False, False, True, False, False)
    probabilities = [int(value) / sum(mask) for value in mask]
    assert probabilities == [0.5, 0, 0, 0.5, 0, 0]
    assert math.isclose(math.log(probabilities[3]), -math.log(2))
    print(f"Verified {len(labels)} labels, {len(refs)} references, "
          f"{len(manifest['files'])} pinned files, and 5 arithmetic checks")
    print("Mathematical semantics and definition order still require human review.")


if __name__ == "__main__":
    main()
