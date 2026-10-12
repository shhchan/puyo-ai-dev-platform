"""PUYO-266 exploratory paired fire-goal comparison; distinct from formal G2.

The declared cohort is the formal 30 esports patterns twice per goal. A run
stops at its first actual 10+ chain, game over, or 60 resolved placements.
Frozen source/build/provider identities and tick replay make runs auditable.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from agents.nextgen_profiles import nextgen_search_settings
from eval.nextgen_gates import digest, distribution
from eval.nextgen_safe_build_gate import read, write_new
from eval.nextgen_single_quality_gate import (
    PATTERN_IDS, REPEATS, build_identity, current_source, require_clean,
)
from eval.nextgen_single_quality_runtime import (
    POLICY_SEED, SingleQualityMatch, make_policy, measure, replay_audit, semantic_payload,
)
from src.core.tsumo import EsportsTsuSource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "puyo.nextgen.fire_goal_comparison.v1"
GOALS = {"chain10": None, "score95000": 95_000}
PLACEMENTS = 60
STOP_CHAIN = 10


def name(goal, pattern_id, repeat):
    return f"{goal}-pattern-{pattern_id}-repeat-{repeat}.json.gz"


def new_match(config, pattern_id):
    return SingleQualityMatch(seed=config["environment_seed"], tsumo_mode="esports_tsu",
                              tsumo_source=config["provider"]["source_path"],
                              tsumo_pattern_id=pattern_id)


def declared_config(source_path):
    source = EsportsTsuSource(source_path)
    policies = {goal: make_policy("nextgen_tactic_manager", fire_goal_score=score)
                for goal, score in GOALS.items()}
    configs = {goal: {"profile": policy.profile.to_dict(),
                      "search": asdict(policy.search_config),
                      "selector": asdict(policy.selector.config),
                      "catalog_sha256": policy.catalog.semantic_digest}
               for goal, policy in policies.items()}
    for goal, score in GOALS.items():
        profile, search = nextgen_search_settings("nextgen_safe_build", seed=POLICY_SEED,
                                                   fire_goal_score=score)
        if (configs[goal]["profile"] != profile.to_dict() or
                configs[goal]["search"] != asdict(search)):
            raise ValueError("policy search differs from declaration")
    return {"pattern_ids": list(PATTERN_IDS), "repeats": list(REPEATS),
            "goals": GOALS, "policies": configs, "provider": {
                "mode": "esports_tsu", "source_path": str(Path(source_path).resolve()),
                "sha256": source.checksum, "version": source.version},
            "environment_seed": 55, "policy_seed": POLICY_SEED,
            "placements": PLACEMENTS, "stop_first_actual_chain": STOP_CHAIN,
            "max_ticks": 30_000, "backend": "native", "workers": 1,
            "environment": "solo frozen opponent, attacks suppressed, realtime reachable controls",
            "information_boundary": "public current/NEXT/NEXT2; provider pattern unavailable to policy",
            "status": "EXPLORATORY_ONLY; original 46-placement G2 thresholds unchanged"}


def initialize(output, source_path):
    require_clean()
    config = declared_config(source_path)
    manifest = {"schema": SCHEMA, "config": config, "config_sha256": digest(config),
                "source": current_source(), "build": build_identity(),
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    manifest["sha256"] = digest(manifest)
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", manifest)
    return manifest


def load_manifest(output, *, execution=False):
    manifest = read(output / "manifest.json")
    config = manifest["config"]
    if (manifest["schema"] != SCHEMA or
            digest({k: v for k, v in manifest.items() if k != "sha256"}) != manifest["sha256"] or
            digest(config) != manifest["config_sha256"] or
            config["pattern_ids"] != list(PATTERN_IDS) or
            config["repeats"] != list(REPEATS) or config["goals"] != GOALS or
            config["placements"] != PLACEMENTS or
            config["stop_first_actual_chain"] != STOP_CHAIN):
        raise ValueError("comparison declaration mismatch")
    if execution:
        require_clean()
        if (current_source() != manifest["source"] or
                build_identity() != manifest["build"] or
                declared_config(config["provider"]["source_path"]) != config):
            raise ValueError("source/build/provider changed after declaration")
    return manifest


def validate(raw, manifest):
    config = manifest["config"]
    goal, pattern_id, repeat = raw["goal"], raw["pattern_id"], raw["repeat"]
    if (goal not in GOALS or pattern_id not in PATTERN_IDS or repeat not in REPEATS or
            raw["schema"] != SCHEMA or raw["manifest_sha256"] != manifest["sha256"] or
            raw["semantic_digest"] != digest(semantic_payload(raw))):
        raise ValueError("artifact identity or semantic checksum mismatch")
    tsumo = raw["match_rules"].get("tsumo", {})
    if (tsumo.get("mode") != "esports_tsu" or tsumo.get("pattern_id") != pattern_id or
            tsumo.get("source_sha256") != config["provider"]["sha256"]):
        raise ValueError("artifact provider differs from declaration")
    chains = [e["chain_count"] for e in raw["events"] if e["type"] == "resolution_complete"]
    if chains != raw["chains"] or len(chains) != raw["final"]["placements"] or len(chains) > PLACEMENTS:
        raise ValueError("actual resolution count mismatch")
    first = next((i for i, count in enumerate(chains) if count >= STOP_CHAIN), None)
    if first is not None and first != len(chains) - 1:
        raise ValueError("run continued after first target chain")
    expected = ("game_over" if raw["game_over"] else "first_target_chain" if first is not None
                else "placements" if len(chains) == PLACEMENTS else "tick_limit")
    if raw["termination"] != expected:
        raise ValueError("termination reason mismatch")
    if raw["audit"]["lock_mismatches"] or raw["audit"]["unlocked_requests"] or raw["errors"]:
        raise ValueError("lock or scheduler integrity failure")
    if raw["controller"]["fallback_actions"] or raw["controller"]["stale_decisions"]:
        raise ValueError("controller fallback or stale decisions")
    if any(row["inference"]["status"] != "known" for row in raw["rows"]):
        raise ValueError("public inference unavailable")
    if any(row["counters"]["shared_nodes"] > 600_000 or
           row["counters"]["template_nodes"] > 128 or
           row["counters"]["response_nodes"] > 256 for row in raw["rows"]):
        raise ValueError("configured quota exceeded")
    return raw


def worker(output, goal, pattern_id, repeat):
    manifest = load_manifest(output, execution=True)
    if goal not in GOALS or pattern_id not in PATTERN_IDS or repeat not in REPEATS:
        raise ValueError("unexpected cohort identity")
    path = output / name(goal, pattern_id, repeat)
    progress = output / (path.name.removesuffix(".json.gz") + ".progress.json.gz")
    failure = output / (path.name.removesuffix(".json.gz") + ".failure.json")
    if any(p.exists() for p in (path, progress, failure)):
        raise ValueError("record already exists; refusing overwrite")

    def save_partial(value):
        staging = progress.with_suffix(".tmp")
        staging.write_bytes(gzip.compress(json.dumps(value, allow_nan=False).encode(), mtime=0))
        staging.replace(progress)

    try:
        policy = make_policy("nextgen_tactic_manager", fire_goal_score=GOALS[goal])
        raw = measure(new_match(manifest["config"], pattern_id), policy,
                      "nextgen_tactic_manager", placements=PLACEMENTS,
                      stop_first_chain=STOP_CHAIN, progress=save_partial)
        raw.update(schema=SCHEMA, goal=goal, pattern_id=pattern_id, repeat=repeat,
                   manifest_sha256=manifest["sha256"])
        raw["audit"] = replay_audit(raw, new_match(manifest["config"], pattern_id))
        raw["semantic_digest"] = digest(semantic_payload(raw))
        validate(raw, manifest)
        load_manifest(output, execution=True)
        write_new(path, raw)
        progress.unlink()
        print(json.dumps({"goal": goal, "pattern_id": pattern_id, "repeat": repeat,
                          "placements": len(raw["chains"]), "max_chain": raw["final"]["max_chain"],
                          "termination": raw["termination"]}), flush=True)
    except Exception as error:
        write_new(failure, {"goal": goal, "pattern_id": pattern_id, "repeat": repeat,
                            "manifest_sha256": manifest["sha256"],
                            "error_type": type(error).__name__, "error": str(error)})
        raise


def run_all(output):
    load_manifest(output, execution=True)
    for pattern_id in PATTERN_IDS:
        for goal in GOALS:
            for repeat in REPEATS:
                path = output / name(goal, pattern_id, repeat)
                if path.exists():
                    validate(read(path), load_manifest(output))
                    print("RESUME_SKIP_COMPLETE", goal, pattern_id, repeat, flush=True)
                    continue
                if (output / (path.name.removesuffix(".json.gz") + ".failure.json")).exists():
                    print("RESUME_SKIP_FAILED", goal, pattern_id, repeat, flush=True)
                    continue
                command = [sys.executable, "-m", "eval.nextgen_fire_goal_comparison",
                           "worker", "--output", str(output), "--goal", goal,
                           "--pattern-id", str(pattern_id), "--repeat", str(repeat)]
                subprocess.run(command, cwd=ROOT, check=False)


def summarize(output, *, verify=False):
    manifest = load_manifest(output)
    rows, hashes = [], {}
    for path in sorted(output.glob("*-pattern-*-repeat-*.json.gz")):
        if path.name.endswith((".partial.json.gz", ".progress.json.gz")):
            continue
        raw = validate(read(path), manifest)
        if path.name != name(raw["goal"], raw["pattern_id"], raw["repeat"]):
            raise ValueError("filename/identity mismatch")
        if verify and replay_audit(raw, new_match(manifest["config"], raw["pattern_id"])) != raw["audit"]:
            raise ValueError("tick replay mismatch")
        fires = [e for e in raw["events"] if e["type"] == "resolution_complete" and e["chain_count"] > 0]
        target = next((e for e in fires if e["chain_count"] >= STOP_CHAIN), None)
        rows.append({"goal": raw["goal"], "pattern_id": raw["pattern_id"], "repeat": raw["repeat"],
                     "placements": len(raw["chains"]), "max_chain": raw["final"]["max_chain"],
                     "target_chain": target["chain_count"] if target else None,
                     "target_score": target["score_delta"] if target else None,
                     "max_fired_score": max((e["score_delta"] for e in fires), default=0),
                     "small_clear_count": sum(0 < v < STOP_CHAIN for v in raw["chains"]),
                     "game_over": raw["game_over"], "termination": raw["termination"],
                     "semantic_digest": raw["semantic_digest"],
                     "decision_seconds": [r["seconds"] for r in raw["rows"]]})
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    failures = [read(p) for p in sorted(output.glob("*.failure.json"))]
    by_goal = {}
    for goal in GOALS:
        values = [r for r in rows if r["goal"] == goal]
        primary = [r for r in values if r["repeat"] == 1]
        by_id = {(r["pattern_id"], r["repeat"]): r for r in values}
        missing = [(p, r) for p in PATTERN_IDS for r in REPEATS if (p, r) not in by_id]
        mismatch = [p for p in PATTERN_IDS if (p, 1) in by_id and (p, 2) in by_id and
                    by_id[p, 1]["semantic_digest"] != by_id[p, 2]["semantic_digest"]]
        by_goal[goal] = {"runs": len(values), "missing": missing, "repeat_mismatch": mismatch,
                         "first_10_plus": sum(r["target_chain"] is not None for r in primary),
                         "score_95000_plus": sum(r["max_fired_score"] >= 95_000 for r in primary),
                         "mean_max_chain": (sum(r["max_chain"] for r in primary) / 30
                                            if len(primary) == 30 else None),
                         "mean_placements": (sum(r["placements"] for r in primary) / 30
                                             if len(primary) == 30 else None),
                         "game_overs": sum(r["game_over"] for r in primary),
                         "small_clears": sum(r["small_clear_count"] for r in primary),
                         "decision_seconds": distribution([t for r in values for t in r["decision_seconds"]])}
    return {"schema": SCHEMA, "manifest_sha256": manifest["sha256"],
            "status": "COMPLETE_EXPLORATORY" if len(rows) == 120 and not failures and all(
                not v["missing"] and not v["repeat_mismatch"] for v in by_goal.values())
            else "INCOMPLETE", "goals": by_goal, "rows": rows, "failed_workers": failures,
            "artifacts": hashes, "source_changed_since_declaration": current_source() != manifest["source"]}


def render_html(report):
    """Self-contained visual report; numbers are taken only from saved artifacts."""
    esc = html.escape
    one = {goal: {r["pattern_id"]: r for r in report["rows"]
                  if r["goal"] == goal and r["repeat"] == 1} for goal in GOALS}
    cards = []
    for goal, label in (("chain10", "10 連鎖目標"), ("score95000", "95,000 点目標")):
        value = report["goals"][goal]
        mean = "未完了" if value["mean_max_chain"] is None else f'{value["mean_max_chain"]:.2f}'
        cards.append(f'<article class="card"><h3>{label}</h3><strong>{value["first_10_plus"]}/30</strong>'
                     f'<p>60 手以内の実 10 連鎖以上</p><div class="metric">平均最大実連鎖 {mean}</div>'
                     f'<div class="metric">実 95,000 点以上 {value["score_95000_plus"]}/30</div>'
                     f'<div class="metric">窒息 {value["game_overs"]} 件 · 小消し {value["small_clears"]} 回</div>'
                     f'<div class="metric">取得済み {value["runs"]}/60 run</div></article>')
    pattern_rows = []
    for pattern in PATTERN_IDS:
        cells = []
        for goal in GOALS:
            row = one[goal].get(pattern)
            if row is None:
                cells.extend(("—", "—", "—", "—"))
            else:
                cells.extend((str(row["placements"]), str(row["max_chain"]),
                              "—" if row["target_score"] is None else f'{row["target_score"]:,}',
                              esc(row["termination"])))
        diff = (one["score95000"].get(pattern, {}).get("max_chain", 0)
                - one["chain10"].get(pattern, {}).get("max_chain", 0))
        klass = ' class="low"' if any(one[g].get(pattern, {}).get("max_chain", 10) < 10
                                      for g in GOALS) else ""
        pattern_rows.append(f'<tr{klass}><th>{pattern}</th>' + ''.join(f'<td>{v}</td>' for v in cells)
                            + f'<td>{diff:+d}</td></tr>')
    bars = []
    for goal, label in (("chain10", "10 連鎖目標"), ("score95000", "95,000 点目標")):
        dist = {i: sum(r["max_chain"] == i for r in one[goal].values()) for i in range(1, 20)}
        bars.append(f'<div class="hist"><h3>{label}</h3><div class="bars">' + ''.join(
            f'<div class="barcol" title="{i} 連鎖: {dist[i]} 件"><div class="bar" '
            f'style="height:{max(2, dist[i] * 9)}px"></div><small>{i}</small></div>'
            for i in range(1, 20)) + '</div><p>各 pattern の 1 回目，最大実連鎖の分布</p></div>')
    status = esc(report["status"])
    source = "変更あり" if report["source_changed_since_declaration"] else "一致"
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PUYO-266 発火目標と 60 手の比較</title><style>
:root {{font-family:system-ui,"Noto Sans JP",sans-serif;color:#223047;background:#f4f7fb}}
body {{max-width:1240px;margin:auto;padding:24px}} h1 {{font-size:2rem}} h2 {{margin-top:2.5rem}}
.lead {{font-size:1.1rem;line-height:1.8;max-width:78ch}} .cards,.charts {{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.card,.hist,.note {{background:white;border-radius:16px;padding:22px;box-shadow:0 3px 16px #18305012}}
.card strong {{font-size:3rem;color:#1767b8}} .card p {{margin:0 0 14px}} .metric {{padding:7px 0;border-top:1px solid #e4eaf2}}
.bars {{height:185px;display:flex;align-items:end;gap:4px;border-bottom:1px solid #8a98aa}}
.barcol {{height:100%;flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center}}
.bar {{width:100%;background:#4489e9;border-radius:4px 4px 0 0}} .barcol small {{font-size:10px;margin-bottom:-18px}}
.hist p {{margin-top:30px;color:#52647e}} .scroll {{overflow-x:auto}} table {{border-collapse:collapse;background:white;width:100%;font-size:.9rem}}
th,td {{padding:8px;border-bottom:1px solid #e1e7ef;text-align:right;white-space:nowrap}} thead th {{background:#e8f0fb;position:sticky;top:0}}
tbody th {{font-weight:600}} tr.low {{background:#fff2e3}} .note {{line-height:1.8}} code {{word-break:break-all}}
</style></head><body><header><p>PUYO-266 · 追加比較 · {status}</p><h1>発火目標を変え，60 手まで見た結果</h1>
<p class="lead">同じ eスポーツ通の 30 pattern × 2 回で，10 連鎖目標と 95,000 点目標を比較する．どちらも最初の<b>実 10 連鎖以上</b>，窒息，または 60 手で停止する．目標は探索の勝利スコア・発火候補・rule 選択へ渡した．</p></header>
<section class="cards">{''.join(cards)}</section><h2>最大実連鎖の分布</h2><section class="charts">{''.join(bars)}</section>
<h2>Pattern ごとの軌跡</h2><p>橙色はどちらかの設定で 10 連鎖未満だった pattern．発火点は最初の実 10 連鎖以上の <code>score_delta</code>．差は 95,000 点目標 − 10 連鎖目標．</p>
<div class="scroll"><table><thead><tr><th rowspan="2">pattern ID</th><th colspan="4">10 連鎖目標</th><th colspan="4">95,000 点目標</th><th rowspan="2">最大連鎖差</th></tr>
<tr><th>手数</th><th>最大連鎖</th><th>発火点</th><th>終了</th><th>手数</th><th>最大連鎖</th><th>発火点</th><th>終了</th></tr></thead><tbody>{''.join(pattern_rows)}</tbody></table></div>
<h2>読み方と限界</h2><div class="note"><p>95,000 点設定では <code>winning_score_threshold</code> を探索に渡し，<code>fire_main</code> の候補生成と rule selector も 95,000 点を要求する．探索の既存の 10 連鎖分類は残るため，内部順位の全項目をスコア専用に置き換えた実験ではない．また最初の実 10 連鎖で停止するため，そこで 95,000 点未満なら，その先のスコア達成可否は測れない．</p>
<p>小消し回数は 1〜9 連鎖の実発火数．「不当」かどうかの因果判定ではない．平均は各 pattern の 1 回目のみ，repeat 2 は同一軌跡の再現確認用．旧 46 手正式 gate の受入基準は維持し，本結果をその PASS に読み替えない．</p>
<p>証拠: <code>manifest SHA-256 {esc(report['manifest_sha256'])}</code>．source 身元 {source}．failed worker {len(report['failed_workers'])} 件．raw と report.json は同じディレクトリに保存．</p></div></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "worker", "run-all", "finalize", "verify", "html"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--goal", choices=tuple(GOALS))
    parser.add_argument("--pattern-id", type=int, choices=PATTERN_IDS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    args = parser.parse_args()
    if args.command == "init":
        if args.source is None:
            parser.error("init needs --source")
        result = initialize(args.output, args.source)
    elif args.command == "worker":
        if args.goal is None or args.pattern_id is None or args.repeat is None:
            parser.error("worker needs --goal, --pattern-id and --repeat")
        worker(args.output, args.goal, args.pattern_id, args.repeat)
        return
    elif args.command == "run-all":
        run_all(args.output)
        return
    else:
        result = summarize(args.output, verify=args.command == "verify")
        if args.command == "finalize":
            write_new(args.output / "report.json", result)
        if args.command in ("finalize", "html"):
            (args.output / "report.html").write_text(render_html(result), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("sha256", "status", "goals") if k in result}, indent=2))


if __name__ == "__main__":
    main()
