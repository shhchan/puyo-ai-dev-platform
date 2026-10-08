"""Retain replay inputs/hash/attack evidence without duplicate UI diagnostics."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def archive(source, target):
    target.mkdir(parents=True, exist_ok=False)
    originals = {}
    for name in ('report', 'ledger', 'replay', 'audit', 'witnesses'):
        path = source / (name + '.json')
        if not path.exists():
            continue
        raw = path.read_bytes()
        originals[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        value = json.loads(raw)
        if name == 'replay':
            omitted = ('policy_diagnostics', 'controller_diagnostics', 'controller_status')
            value['ticks'] = [{k: v for k, v in row.items() if k not in omitted}
                              for row in value['ticks']]
            originals[name]['omitted_tick_keys'] = omitted
        packed = gzip.compress(json.dumps(value, separators=(',', ':')).encode(), mtime=0)
        target.joinpath(name + '.json.gz').write_bytes(packed)
    target.joinpath('originals.json').write_text(json.dumps(originals, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    archive(args.source, args.target)
