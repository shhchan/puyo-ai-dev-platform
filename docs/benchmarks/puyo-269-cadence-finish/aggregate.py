"""Recompute raw percentiles, gates and integrity of the fixed A/B matrix."""
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('desktop_aggregate', ROOT.parent / 'puyo-269-desktop/aggregate.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
previous.ROOT = ROOT
previous.aggregate()
summary = json.loads((ROOT / 'summary.json').read_text())
for name, row in summary.items():
    raw = json.loads(gzip.decompress((ROOT / 'raw' / f'{name}.json.gz').read_bytes()))
    row['wire_reference'] = raw.get('wire_reference')
    row['frames_over_25ms'] = [
        {'frame': frame, 'frame_ms': value,
         'update_ms': raw['raw_samples']['update_ms'][frame],
         'render_ms': raw['raw_samples']['render_ms'][frame],
         'catchup': raw['raw_samples']['tick_catchup'][frame],
         'spans_tagged_at_completion_not_additive': {
             key: [span for span in values if span['frame'] == frame]
             for key, values in raw['functions_raw'].items()
             if key in ('authoritative_mask', 'scheduler_prepare', 'scheduler_accept',
                        'scheduler_finish', 'reader_decode', 'gc_collect', 'ipc_deserialize')
             and any(span['frame'] == frame for span in values)}}
        for frame, value in enumerate(raw['raw_samples']['frame_interval_ms']) if value > 25]
    row['input_tail_spans'] = []
    for event in raw['input_events']:
        start, end = event['expected_ns'], event['handled_ns']
        if (end - start) / 1e6 <= 25:
            continue
        spans = []
        for function in ('authoritative_mask', 'scheduler_accept', 'scheduler_finish', 'reader_decode', 'gc_collect'):
            for span in raw['functions_raw'].get(function, []):
                a = span['started_ns']
                b = a + span['ms'] * 1e6
                if b > start and a < end:
                    spans.append({'function': function, **span, 'overlap_ms': (min(b, end) - max(a, start)) / 1e6})
        row['input_tail_spans'].append({'id': event['id'], 'schedule_ms': (end - start) / 1e6,
                                       'overlapping_wall_spans_not_additive': spans})
for scenario in ('one', 'two', 'human', 'light'):
    a, b = [summary[f'{stage}-{scenario}'] for stage in ('before', 'after')]
    for key in ('source_fingerprint', 'native_sha256', 'settings', 'host', 'frames'):
        assert a[key] == b[key], (scenario, key)
(ROOT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
for name, row in summary.items():
    print(name, row['gate'], {key: [round(row['timing_ms'][key][p], 2) for p in ('p95', 'p99')]
                             for key in ('frame_interval_ms', 'input_schedule_ms')})
