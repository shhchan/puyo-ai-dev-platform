"""Run from the baseline or changed checkout; write a separate frozen cohort.

The driver itself is shared, while cwd selects the policy source. Each process
measures one identity with actual scheduler inputs/receipts and replays its
locks. No training or formal G2 qualification is performed.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))

from eval import nextgen_safe_build_diagnostic as diagnostic  # noqa: E402
from eval.nextgen_adoption_replay import replay_adoptions  # noqa: E402
from eval.nextgen_safe_build_gate import build_identity  # noqa: E402
from eval.nextgen_realtime_diagnostic import source_identity  # noqa: E402
from src.ui.launcher_settings import resolve_nextgen_catalog  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--template', choices=('gtr', 'daa', 'persian'), default='gtr')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raw_path = args.output / f'{args.template}-{args.seed}.json.gz'
    if raw_path.exists():
        raise ValueError('refusing to overwrite an existing identity')
    source, build = source_identity(), build_identity()
    original = diagnostic.make_policy

    def make_policy(kind, seed, profile):
        policy = original(kind, seed, profile)
        policy.catalog, _ = resolve_nextgen_catalog(
            catalog_path='train/config/nextgen_templates.yaml', templates=args.template,
            mode='argmax', temperature=.1, commit_turns=14, repo_root=Path.cwd(),
        )
        return policy

    diagnostic.make_policy = make_policy
    result = diagnostic.measure('nextgen', args.seed, 'nextgen_safe_build', placements=40)
    replay = replay_adoptions(result)
    result.update(
        template=args.template, source=source, build=build,
        source_changed=source != source_identity(),
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        quality_status='diagnostic_only; observed quality FAIL / formal G2 BLOCKED retained',
    )
    assert not result['source_changed']
    raw_path.write_bytes(gzip.compress(json.dumps(result, allow_nan=False).encode(), mtime=0))
    (args.output / f'{args.template}-{args.seed}.locks.json.gz').write_bytes(
        gzip.compress(json.dumps(replay, allow_nan=False).encode(), mtime=0))
    print('RESULT', args.template, args.seed, result['max_chain'], result['premature'],
          result['game_over'], result['placements'], flush=True)


if __name__ == '__main__':
    main()
