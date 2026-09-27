"""Recompute the compact PUYO-273 evidence from checksummed traces."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def stats(values):
    values = sorted(values)
    def percentile(q):
        if not values:
            return None
        p = (len(values)-1)*q
        low = int(p)
        return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(p-low)
    return dict(n=len(values), p50=percentile(.5), p95=percentile(.95),
                p99=percentile(.99), max=max(values) if values else None)


def summarize(d):
    samples = {k:stats(v) for k,v in d['raw_samples'].items()}
    assert samples == d['samples']
    active = d['active_samples']
    processes = [p for row in d['process_samples'] for p in row['processes']]
    by_pid = {}
    for p in processes:
        by_pid.setdefault(p['pid'], []).append(p)
    resources = {pid:{'cpu_seconds':(ps[-1]['cpu_ticks']-ps[0]['cpu_ticks'])/d['clock_ticks'],
                      'max_rss_kb':max(p['rss_kb'] for p in ps),
                      'max_threads':max(p['threads'] for p in ps)} for pid,ps in by_pid.items()}
    events = d['input_events']
    handled = {r['id'] for r in events}
    # Ignore terminal calls which return without advancing a simulation tick.
    seen = set()
    ticks = []
    for t in d['human_ticks']:
        if t['tick'] not in seen:
            ticks.append(t)
            seen.add(t['tick'])
    integrity = {'posted':len(d['posted_events']), 'handled':len(events),
                 'duplicate_event_ids':len(events)-len(handled),
                 'pending_at_stop':[e['id'] for e in d['posted_events'] if e['id'] not in handled],
                 'unique_human_ticks':len(ticks),
                 'held_mismatch_ticks':sum(bool(set(t['held'])-set(t['ui_held'])) for t in ticks),
                 'same_tick_opposite_presses':sum('LEFT' in t['press'] and 'RIGHT' in t['press'] for t in ticks),
                 'actions':{a:{'emitted_press':sum(t['press'].count(a) for t in ticks),
                               'fired':sum(t['fired'].count(a) for t in ticks),
                               'fired_after_ui_release':sum(a in t['fired'] and a not in t['ui_held'] for t in ticks),
                               'repeat_after_ui_release':sum(a in t['fired'] and a not in t['ui_held'] and a not in t['press'] for t in ticks)}
                            for a in ['LEFT','RIGHT','DOWN','ROTATE_LEFT','ROTATE_RIGHT']}}
    counts = {agent:{k:v for k,v in row.items() if not isinstance(v,dict)} for agent,row in d['diagnostics'].items()}
    gate = {k:active[k]['p95'] is not None and active[k]['p95']<=25 and active[k]['p99']<=50
            for k in ['frame_interval_ms','input_schedule_ms','input_to_render_ms']}
    cache = {str(hit):stats([r['worker_seconds']*1000 for r in d['cache_samples']
                           if r['hit'] is hit and r['worker_seconds'] is not None]) for hit in [True,False,None]}
    return dict(settings=d['settings'], source_sha=d['source_sha'], reference_mask=d['reference_mask'],
                native=d['native'], frames=d['frames'], active_frames=sum(d['active_frames']),
                elapsed_seconds=d['elapsed_seconds'],ticks=d['ticks'],samples=samples,active_samples=active,
                functions_ms=d['functions_ms'],functions_total_ms={k:sum(r['ms'] for r in v) for k,v in d['functions_raw'].items()},
                resources=resources,worker_cleanup=d['worker_cleanup'],worker_cache_ms=cache,
                diagnostics=counts,scheduler_errors=d['scheduler_errors'],input_integrity=integrity,gate=gate)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--write',action='store_true');args=ap.parse_args()
    manifest=json.loads((ROOT/'manifest.json').read_text())
    summary={}
    for name,meta in manifest['traces'].items():
        compressed=(ROOT/meta['path']).read_bytes()
        assert hashlib.sha256(compressed).hexdigest()==meta['gzip_sha256']
        raw=gzip.decompress(compressed)
        assert hashlib.sha256(raw).hexdigest()==meta['raw_sha256']
        summary[name]=summarize(json.loads(raw))
    # Normalize integer process ids to JSON object keys before comparison.
    summary=json.loads(json.dumps(summary))
    if args.write:
        (ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    else:
        assert summary==json.loads((ROOT/'summary.json').read_text())
    print(f"Verified {len(summary)} checksummed traces and summaries")

if __name__=='__main__':
    main()
