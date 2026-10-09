"""Recompute evidence without launching a GUI. Reference root is optional."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def percentile(values, fraction):
    values = sorted(values)
    index = (len(values)-1)*fraction
    return values[int(index)]*(1-index % 1)+values[math.ceil(index)]*(index % 1)


def correlation(left, right):
    lm, rm = statistics.mean(left), statistics.mean(right)
    denominator = sum((v-lm)**2 for v in left)*sum((v-rm)**2 for v in right)
    return sum((a-lm)*(b-rm) for a, b in zip(left, right))/math.sqrt(denominator) if denominator else 0


def reference_summary(path):
    raw = read(path)
    samples = raw['raw_samples']
    frames, update, render = (samples[k] for k in ('frame_interval_ms', 'update_ms', 'render_ms'))
    work = [a+b+c for a,b,c in zip(update, render, samples['event_ms'])]
    wait = [a-b for a,b in zip(frames, work)]
    high = [i for i,v in enumerate(frames) if v > 25]
    return {
        'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'source_sha': raw['source_sha'], 'frames': len(frames), 'ticks': raw['ticks'],
        'frame_p95_ms': percentile(frames, .95), 'frame_p99_ms': percentile(frames, .99),
        'update_p95_ms': percentile(update, .95), 'render_p95_ms': percentile(render, .95),
        'frame_update_correlation': correlation(frames, update),
        'above_25_frames': high, 'above_25_mean_update_ms': statistics.mean(update[i] for i in high),
        'above_25_mean_render_ms': statistics.mean(render[i] for i in high),
        'above_25_mean_wait_ms': statistics.mean(wait[i] for i in high),
        'wait_above_20_frames': [i for i,v in enumerate(wait) if v > 20],
        'gc_over_10_ms': [r for r in raw['functions_raw'].get('gc_collect', []) if r['ms'] > 10],
    }


def diagnostic_summary():
    raw_path, intervals_path = HERE/'diagnostic-raw.json.gz', HERE/'diagnostic-intervals.json.gz'
    raw, diagnostic = read(raw_path), read(intervals_path)
    assert hashlib.sha256(gzip.decompress(raw_path.read_bytes())).hexdigest() == diagnostic['raw_sha256']
    probe = HERE.parents[2]/'eval/puyo_269_frame_diagnostic.py'
    assert hashlib.sha256(probe.read_bytes()).hexdigest() == diagnostic['probe_sha256']
    assert diagnostic['source_sha'] == raw['source_sha']
    assert raw['minimal'] and raw['frames'] == 360 and all(raw['active_frames'])
    assert all(raw['worker_cleanup'].values()) and not any(raw['scheduler_errors'].values())
    assert all(row['matches'] is not False for row in raw['lock_receipts'])
    assert all(d['timeouts'] == d['fallback_actions'] == 0 for d in raw['diagnostics'].values())
    grouped = defaultdict(list)
    for row in diagnostic['intervals']:
        assert row['ended_ns'] >= row['started_ns'] and row['thread_cpu_ms'] >= 0
        grouped[(row['label'], row.get('parent'))].append(row)
    assert len(grouped['clock_tick', None]) == len(grouped['update', None]) == len(grouped['render', None]) == 360
    groups = []
    for (label, parent), rows in sorted(grouped.items(), key=lambda item: str(item[0])):
        groups.append({'label': label, 'parent': parent, 'count': len(rows),
                       'wall_total_ms': sum(r['wall_ms'] for r in rows),
                       'thread_cpu_total_ms': sum(r['thread_cpu_ms'] for r in rows),
                       'wall_median_ms': statistics.median(r['wall_ms'] for r in rows),
                       'wall_max_ms': max(r['wall_ms'] for r in rows)})
    return {
        'purpose': diagnostic['purpose'], 'source_sha': raw['source_sha'],
        'raw_sha256': hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        'intervals_sha256': hashlib.sha256(intervals_path.read_bytes()).hexdigest(),
        'native_sha256': raw['native']['sha256'], 'host': raw['host'],
        'frame': raw['samples']['frame_interval_ms'], 'input_schedule': raw['samples']['input_schedule_ms'],
        'groups': groups, 'gc_over_10_ms': [r for r in diagnostic['intervals'] if r['label'] == 'gc_collect' and r['wall_ms'] > 10],
        'slow_frames': [{'frame': i, 'frame_ms': v, 'wait_residual_ms': diagnostic['wait_residual_ms'][i],
                         'intervals': [r for r in diagnostic['intervals'] if r['frame_start'] == i and r['wall_ms'] > 2]}
                        for i,v in enumerate(raw['raw_samples']['frame_interval_ms']) if v > 25],
        'lock_receipts': len(raw['lock_receipts']), 'worker_cleanup': raw['worker_cleanup'],
        'stale_decisions': {a:d['stale_decisions'] for a,d in raw['diagnostics'].items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-root', type=Path)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    result = {'diagnostic': diagnostic_summary()}
    if args.reference_root:
        paths = sorted(p for name in ('puyo-274-sprint14-bounded-20261009', 'puyo-274-sprint14-bounded-repeat-20261009')
                       for p in (args.reference_root/name).rglob('*.json.gz'))
        assert len(paths) == 18
        result['references'] = {str(p.relative_to(args.reference_root)): reference_summary(p) for p in paths}
        raw = read(HERE/'diagnostic-raw.json.gz')
        reference = read(args.reference_root/'puyo-274-sprint14-bounded-20261009/raw/minimal-two.json.gz')
        assert raw['native']['sha256'] == reference['native']['sha256'] and raw['host'] == reference['host']
        assert raw['settings'] == reference['settings']
        assert all(raw['source_files_sha256'][p] == h for p,h in reference['source_files_sha256'].items())
        result['reference_source_fingerprints_match'] = True
    if args.write:
        (HERE/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    else:
        expected = json.loads((HERE/'summary.json').read_text())
        assert result['diagnostic'] == expected['diagnostic']
        if args.reference_root:
            assert result == expected
        print('PASS: clocks, checksum, source, cleanup, locks and stored analysis agree; no gate verdict.')


if __name__ == '__main__':
    main()
