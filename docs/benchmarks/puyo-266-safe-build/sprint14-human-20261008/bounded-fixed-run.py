"""One fresh-process native 40-placement diagnostic; never formal G2."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from eval.nextgen_adoption_replay import replay_adoptions
from eval.nextgen_realtime_diagnostic import source_identity
from eval.nextgen_safe_build_diagnostic import measure
from eval.nextgen_safe_build_gate import build_identity


def run(seed, output):
    output.mkdir(parents=True, exist_ok=True)
    source, build = source_identity(), build_identity()
    raw = measure('nextgen', seed, 'nextgen_safe_build', placements=40)
    raw.update(template='gtr', source=source, build=build,
               source_changed=source_identity() != source,
               driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               quality_status='diagnostic_only; observed quality FAIL / formal G2 BLOCKED retained')
    assert not raw['source_changed']
    assert build_identity() == build
    for suffix, value in (('.json.gz', raw), ('.locks.json.gz', replay_adoptions(raw))):
        with (output / f'gtr-{seed}{suffix}').open('xb') as handle:
            handle.write(gzip.compress(json.dumps(value, allow_nan=False).encode(), mtime=0))
    print({k: raw[k] for k in ('seed', 'max_chain', 'premature', 'game_over', 'placements', 'decision_seconds')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('seed', type=int, choices=(128, 55, 123, 124, 126))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.seed, args.output)
