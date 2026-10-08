"""Same-payload proof cost, outside GUI; no GC or runtime threshold changes."""
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
import types

from puyo_env import nextgen_scheduler as current
from tests.test_nextgen_reader_decode import ReaderDecodeTests

ROOT = Path(__file__).parent
SHA = '0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222'
source = subprocess.check_output(['git', 'show', f'{SHA}:puyo_env/nextgen_scheduler.py'], text=True)
before = types.ModuleType('scheduler_before')
exec(compile(source, 'scheduler_before', 'exec'), vars(before))
_, _, payload = ReaderDecodeTests().prepare()
result = {'reference': SHA, 'reference_sha256': hashlib.sha256(source.encode()).hexdigest(), 'runs': {}}
for name, module in [('before', before), ('after', current)]:
    decoded = module.decode_nextgen_payload(payload)
    times = {}
    for label, call in [('decode_ms', lambda: module.decode_nextgen_payload(payload)),
                        ('proof_ms', decoded.decoded)]:
        samples = []
        for _ in range(100):
            start = time.perf_counter_ns()
            call()
            samples.append((time.perf_counter_ns() - start) / 1e6)
        times[label] = {'median': statistics.median(samples), 'raw': samples}
    times['canonical_sha256'] = hashlib.sha256(decoded.decoded().to_json().encode()).hexdigest()
    result['runs'][name] = times
assert result['runs']['before']['canonical_sha256'] == result['runs']['after']['canonical_sha256']
(ROOT / 'microbenchmark.json').write_text(json.dumps(result, indent=2) + '\n')
print({name: {k: v['median'] for k, v in row.items() if isinstance(v, dict)} for name, row in result['runs'].items()})
