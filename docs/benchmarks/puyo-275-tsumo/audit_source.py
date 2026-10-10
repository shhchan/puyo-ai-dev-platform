"""Recheck the external corpus against two other authors' published tools."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


SOURCE_SHA256 = "568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb"
WEAKFLOUR_URL = "https://puyo.weakflour.net/haipuyo-search/dist/assets/data/haipuyo-min.js"
SAMPLE_IDS = (0, 34066, 65535)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def audit(path):
    raw = Path(path).read_bytes()
    if digest(raw) != SOURCE_SHA256:
        raise ValueError("source checksum differs from the audited version")
    rows = raw.splitlines()
    if len(rows) != 65536:
        raise ValueError("source row count differs")

    with urlopen(WEAKFLOUR_URL, timeout=20) as response:
        weakflour = response.read()
    prefixes = ast.literal_eval(weakflour.decode().strip().lstrip(";").rstrip(";"))
    matching_prefixes = sum(
        prefix.encode() == rows[index][:len(prefix)]
        for index, prefix in enumerate(prefixes)
    )
    if len(prefixes) != 65536 or matching_prefixes != 65536:
        raise AssertionError("weakflour prefix comparison differs")
    result = {
        "source_sha256": digest(raw),
        "weakflour_url": WEAKFLOUR_URL,
        "weakflour_response_sha256": digest(weakflour),
        "weakflour_prefix_characters": len(prefixes[0]),
        "weakflour_matching_prefixes": matching_prefixes,
        "puyop_samples": [],
    }
    for pattern_id in SAMPLE_IDS:
        url = f"https://www.puyop.com/Sim/get-haipuyo?pattern={pattern_id + 1}"
        with urlopen(url, timeout=20) as response:
            data = response.read()
        label, sequence = data.decode().split(":", 1)
        full_match = label == f"{pattern_id + 1:05d}" and sequence.encode() == rows[pattern_id]
        if not full_match:
            raise AssertionError(f"puyop comparison differs for pattern {pattern_id}")
        result["puyop_samples"].append({
            "pattern_id": pattern_id,
            "url": url,
            "response_sha256": digest(data),
            "compared_characters": len(sequence),
            "matched": full_match,
        })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="External original haipuyo.txt")
    args = parser.parse_args()
    print(json.dumps(audit(args.source), ensure_ascii=False, indent=2))
