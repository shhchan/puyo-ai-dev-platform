"""実際に選択が変わったseed130・8手目を固定して比較する。"""

import argparse
import resource
import time
from pathlib import Path

from agents.deep_chain_native import NativeDeepChainBackend, decode_request
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from eval import deep_chain_builder_benchmark as baseline
import trial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
parser.add_argument("--repeat", type=int, required=True)
args = parser.parse_args()
manifest = trial.initialize(args.root)
path = args.root / f"guard-fixed-{args.repeat:02d}.json"
if path.exists():
    raise ValueError("既存の固定request計測を上書きしません")
source_path = Path(__file__).parent / "seed-130-all-roots.json.gz"
source = baseline._read_json(source_path)
request = decode_request(bytes.fromhex(source["cases"][7]["request_hex"]))
backend = NativeDeepChainBackend(canonical=True)
samples = []
for label in ("cold", "warm"):
    start = time.perf_counter()
    native = backend.decide(request)
    materialization_start = time.perf_counter()
    result = materialize_native_long_horizon_result(native, request)
    end = time.perf_counter()
    samples.append({
        "label": label, "elapsed_seconds": end - start,
        "materialization_seconds": end - materialization_start,
        "native_telemetry": dict(native.telemetry),
        "counters": result.counters.to_dict(),
        "action": native.selected_action, "native_ranking": list(native.ranked_root_actions),
        "digest": result.deterministic_digest,
        "strict_all_root_parity_passed": True,
        "guard_applied": any(e.ranking_rule_version.endswith(".v3") for e in result.root_evidence),
    })
assert samples[0]["digest"] == samples[1]["digest"]
trajectory = baseline._read_json(args.root / "target-10/seed-130-repeat-01.json.gz")
assert samples[0]["action"] == trajectory["records"][7]["action"]
payload = {"manifest_sha256": manifest["manifest_sha256"],
           "script_sha256": baseline.file_sha256(Path(__file__)),
           "source_sha256": baseline.file_sha256(source_path),
           "seed": 130, "turn": 7, "samples": samples,
           "roots": [e.to_dict() for e in result.ranked_roots],
           "representative_paths": {str(action): list(node.path) for action, node in result.representatives.items()},
           "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
baseline._write_json(path, payload)
print({"condition": args.root.name, "repeat": args.repeat,
       "action": samples[0]["action"], "guard": samples[0]["guard_applied"]}, flush=True)
