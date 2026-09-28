"""Serial, reproducible backend-only cost comparison; never a model/G2 gate."""

import argparse
import ast
import importlib
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from agents.chain_structure import load_chain_structure_config
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    NativeDecisionRequest,
    NativeDeepChainBackend,
    request_sha256,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.long_horizon_search import (
    LongHorizonSearchConfig,
    run_compact_long_horizon_search,
)
from agents.selected_template import SelectedTemplate
from src.core.constants import PuyoColor


def inputs(case):
    corpus = json.loads(
        (ROOT / "tests/fixtures/puyo_271_regression_cases.json").read_text()
    )
    rows = (
        corpus["persian_counterexamples"][int(case == "l")]["rows_bottom_up"]
        if case in ("flat", "l")
        else ["110000"]
    )
    planes = [0] * 6
    for y, row in enumerate(rows):
        for x, color in enumerate(row):
            if int(color):
                planes[int(color) - 1] |= 1 << (6 * y + x)
    required = ((0, 0, 1), (1, 0, 1), (2, 0, 1))
    if case == "all_failed":
        required = ((0, 0, 3),)
    if case == "unproven":
        required = ((0, 11, 3),)
    selected = SelectedTemplate(
        "synthetic-persian", case, "identity", (("A", 1), ("B", 2), ("C", 3)), required
    )
    return CompactSearchState(tuple(planes)), selected


def worker(args):
    board, selected = inputs(args.case)
    if not args.constrained:
        selected = None
    cfg = LongHorizonSearchConfig(
        depth=16,
        width=250,
        scenarios=6,
        minimum_chain_count=10,
        max_expanded_nodes=args.quota,
        decision_seed=123,
        future_sampling_mode="legacy-fixed-six",
    )
    req = NativeDecisionRequest(
        board,
        (
            (PuyoColor.RED, PuyoColor.BLUE),
            (PuyoColor.GREEN, PuyoColor.YELLOW),
            (PuyoColor.BLUE, PuyoColor.GREEN),
        ),
        cfg,
        load_chain_structure_config(),
        hashlib.sha256(json.dumps(asdict(cfg), sort_keys=True).encode()).hexdigest(),
        "puyo272-cost",
        "1",
        "1",
        selected_template=selected,
        execution_mode=args.mode,
    )
    client = NativeDeepChainBackend() if args.backend == "native" else None

    def run():
        started = time.perf_counter_ns()
        if client:
            raw = client.decide(req)
            result = materialize_native_long_horizon_result(raw, req)
            telemetry = dict(raw.telemetry)
        else:
            result = run_compact_long_horizon_search(
                board, req.known_pairs, cfg, selected_template=selected
            )
            telemetry = {}
        elapsed = time.perf_counter_ns() - started
        return result, elapsed, telemetry

    run()  # one warmup, excluded from decision percentiles; RSS includes warmup
    records = []
    for repeat in range(args.repeats):
        result, elapsed, telemetry = run()
        records.append(
            dict(
                repeat=repeat,
                elapsed_ns=elapsed,
                template_check_ns=result.template_check_ns,
                peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                counters=result.counters.to_dict(),
                native_telemetry=telemetry,
                result_digest=result.deterministic_digest,
                ranked_roots=[v.root_action for v in result.ranked_roots],
                compatible_roots=[
                    v.root_action for v in result.compatible_ranked_roots
                ],
                root_quality=[
                    dict(
                        action=v.root_action,
                        expected_chain=v.chain_count_mean,
                        max_chain=v.max_chain_count,
                        fire_class=v.fire_class,
                    )
                    for v in result.root_evidence
                ],
                representatives={
                    a: dict(path=n.path, scenario=n.scenario_id)
                    for a, n in result.representatives.items()
                },
                selected_template=result.selected_template,
            )
        )
    print(
        json.dumps(
            dict(
                case=args.case,
                worker_pid=os.getpid(),
                state_hex=board.to_bytes().hex(),
                backend=args.backend,
                mode=args.mode,
                constrained=args.constrained,
                config=asdict(cfg),
                known_pairs=[[c.name for c in p] for p in req.known_pairs],
                request_sha256=request_sha256(req),
                selected_template_hex=None
                if selected is None
                else selected.to_bytes().hex(),
                records=records,
            )
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--backend", choices=["python", "native"])
    parser.add_argument("--case")
    parser.add_argument("--constrained", action="store_true")
    parser.add_argument("--quota", type=int, default=512)
    parser.add_argument("--mode", default="oracle-1")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-native", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    source_paths = [
        "agents/selected_template.py",
        "agents/long_horizon_search.py",
        "agents/deep_chain_native.py",
        "agents/deep_chain_native_search.py",
        "native/deep_chain_native/src/selected_template.rs",
        "native/deep_chain_native/src/long_horizon.rs",
        "native/deep_chain_native/src/lib.rs",
    ]
    native = importlib.import_module("_puyo_deep_chain_native")
    manifest = dict(
        code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        measurement_script_sha256=hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        native_capabilities=NativeDeepChainBackend().capabilities.to_dict(),
        native_binary={
            "path": native.__file__,
            "sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        },
        python_semantic_ast_sha256={
            p: hashlib.sha256(
                ast.dump(
                    ast.parse((ROOT / p).read_text()), include_attributes=False
                ).encode()
            ).hexdigest()
            for p in source_paths
            if p.endswith(".py")
        },
        schema="puyo.272.template_cost.v1",
        host=platform.platform(),
        machine=platform.machine(),
        python=sys.version,
        cpu=next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "",
        ),
        cpu_affinity=sorted(os.sched_getaffinity(0)),
        parent_pid=os.getpid(),
        logical_seed=123,
        sampling_mode="legacy-fixed-six",
        source_sha256={
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths
        },
        corpus_sha256=hashlib.sha256(
            (ROOT / "tests/fixtures/puyo_271_regression_cases.json").read_bytes()
        ).hexdigest(),
        series="native-production-quota"
        if args.full_native
        else "common-bounded-quota",
        notes="Geometric backend decisions only. Three warm repetitions; percentiles are exploratory. Not actual game quality or G2. RSS is process high water including warmup. Constraint time measures validation only, including timer overhead. Native speculative scenario work is reported separately in telemetry.",
        runs=[],
    )
    for case in ("flat", "l", "all_failed", "unproven"):
        for backend in ("native",) if args.full_native else ("python", "native"):
            for constrained in (False, True):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--backend",
                    backend,
                    "--case",
                    case,
                    "--quota",
                    str(600000 if args.full_native else args.quota),
                    "--mode",
                    "scenario-6" if args.full_native else "oracle-1",
                    "--repeats",
                    str(args.repeats),
                ]
                if constrained:
                    command.append("--constrained")
                print(" ".join(command), file=sys.stderr, flush=True)
                proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                if proc.returncode:
                    manifest["runs"].append(
                        dict(
                            command=command,
                            error=proc.stderr,
                            returncode=proc.returncode,
                        )
                    )
                    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
                    raise RuntimeError(proc.stderr)
                run = json.loads(proc.stdout)
                run["command"] = command
                manifest["runs"].append(run)
                args.output.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
