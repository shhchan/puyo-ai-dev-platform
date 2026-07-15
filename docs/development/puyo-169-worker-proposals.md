# PUYO-169 K-best worker proposal contract

## Scope

PUYO-167 の `DiverseBeamCandidate` は探索内部の deterministic candidate generator であり、
PUYO-169 はその出力を runtime、replay、PUYO-130 の learned ranker、PUYO-131 の集計処理が
共通利用する固定長 artifact に変換する。

公開契約は次のとおり。

- batch schema: `puyo.worker_proposal_batch.v1`
- candidate schema: `puyo.worker_proposal_candidate.v1`
- ranker input schema: `puyo.worker_candidate_ranker_input.v1`
- selection telemetry schema: `puyo.worker_candidate_selection_telemetry.v1`
- candidate distribution schema: `puyo.worker_candidate_distribution.v1`
- compatibility entry point: `compatibility_action(batch)`
- learned-ranker entry point: `select_ranked_candidate(batch, logits)`

実装は `agents/worker_proposals.py` に置く。beam 探索の scalar score、diversity slot、
pruning、rank 0 は変更しない。

## Candidate construction

`build_worker_proposal_batch` は raw candidate を次の順で正規化する。

1. 現行 worker が選んだ action を先頭に置く。
2. raw rank、candidate value、root action、action sequence を deterministic tie-break に使う。
3. illegal root action を除外する。
4. 同じ root action または同じ action sequence を後勝ちさせず、最初の1件だけ残す。
5. `candidate_limit` 件まで保持する。
6. 選択済み action が raw set にない場合は、その合法 action の1手 preview を fallback として slot 0 に置く。

各 candidate は次を保存する。

- stable candidate ID、raw rank、root action、全 action sequence
- scalar candidate value と value breakdown
- predicted chain、score、attack generation/cancellation/outgoing
- BuildPotential v2 と ignition cost
- trigger recoverability、continuation flexibility、danger
- hidden-future scenario support/coverage
- decision search 全体の wall-clock latency と expanded nodes
- generation/retention/pruning reasons
- optional named-chain-style metadata

action sequence の runtime projection は cloned simulator だけを進める。元 simulator は変更しない。
hidden scenario に依存する raw `predicted_max_chain` は失わず、runtime projection より大きい場合も保持する。

## Stable identity

`decision_id` は盤面、current pair、visible next queue、all-clear pending state、worker profile、score carry、
incoming attack の canonical JSON digest から作る。
`candidate_id` は `decision_id + action_sequence + candidate schema` の digest であり、rank や wall-clock latency を
含めない。同じ局面と同じ手順は同じ ID になり、候補の順位が変わっても candidate identity は変わらない。

`proposal_id` は `decision_id + profile_id + candidate_limit + ordered candidate IDs` から作る。
deserialize 時に再計算し、不一致 artifact は拒否する。

## Mask, padding, and empty semantics

batch は常に `candidate_limit` 個の slot を持つ。

- 実 candidate の slot は `candidate_mask=true`。
- padding slot は JSON `null` かつ `candidate_mask=false`。
- policy softmax、log-probability、entropy、critic reduction は masked slot を必ず除外する。
- `legal_action_mask` は既存 22-action contract と同じ固定長で保存する。
- candidate の root action は legal mask が true でなければならない。
- legal action がない場合は全 slot が masked、`selected_index=null`、selection mode は `empty` になる。

beam 以外の `fire_main`、counter、survival 等も同じ batch schema を返す。これらは従来 action の
1件だけを deterministic fallback candidate にし、残りを padding する。このため consumer は worker 種別で
schema を分岐する必要がない。

## Compatibility selection

現行 manager と fixed worker は `compatibility_action(batch)` を利用する。非 empty batch では常に slot 0 を返し、
元の `SearchProposal.action` と異なる場合は runtime error にする。したがって PUYO-169 を有効にしても
従来 single-best action semantics は変わらない。

PUYO-130 は `select_ranked_candidate` または同じ mask contract を使う learned head に logits を渡せる。
helper は masked softmax、選択 candidate の log-probability、entropy を返し、同値 logit は小さい slot index で
deterministic に tie-break する。optimizer、rollout update、long-run training は PUYO-130 の範囲である。

## Ranker and critic input

`CandidateRankerInput` は環境を再探索せず、次の固定順 feature matrix を返す。

1. candidate value
2. predicted chain count
3. predicted score
4. generated attack
5. outgoing attack
6. predicted chain potential
7. normalized ignition cost
8. trigger recoverability
9. continuation flexibility
10. danger
11. scenario coverage
12. observational search latency
13. expanded nodes

matrix、candidate IDs、candidate mask、legal-action mask、selected index は同じ artifact に入る。
named chain style は optional metadata として candidate JSON に保存するが、generic ranker feature には入れない。
style を使う learned model は明示的な別 namespace/head で読む。

wall-clock latency は artifact と offline learning 用の観測値であり、candidate generation や rank-0 fallback の
ordering には使わない。`deterministic_dict` / `deterministic_digest` は latency fields だけを neutralize し、
同じ seed/config の contract equality を検証する。

## Rollout and migration

`SearchProposal.worker_proposal_dict` は完全な JSON-safe batch を返す。v1.7 strategy manager は、
各 tactic preview と最終 worker result の両方へ `proposal_batch` を保存する。realtime runtime は
`policy_diagnostics` を replay trajectory へ保存するため、この batch がそのまま rollout ledger になる。

`WorkerProposalBatch.from_dict` は schema を必須とする。PUYO-167 時点の raw envelope は
`puyo.worker_proposal_batch.v0` としてだけ明示 migration できる。v0 migration は action/value/
BuildPotential/reason を保持するが、当時存在しなかった score/attack runtime projection と latency は
`unavailable` / 0 とする。schema 不明、ID 不一致、mask/padding 不一致、duplicate root/plan は拒否する。

## Telemetry

`CandidateSelectionTelemetry` は PUYO-131 がそのまま集計できる次の値を公開する。

- `candidate_coverage = unique_root_actions / legal_action_count`
- `candidate_collapse_ratio = 1 - candidate_count / min(K, legal_action_count)`
- selected candidate value、best candidate value
- `selection_regret = max(0, best_value - selected_value)`
- unique root/action-sequence counts
- search latency、expanded nodes、fallback usage

compatibility mode は scalar rank 0 を選ぶため通常 regret 0 になる。learned ranker が別 candidate を選んだ場合は、
同じ batch に対して `batch.telemetry(selected_index)` を呼び、選択時点の regret を記録する。

## Benchmark

追跡 artifact は `docs/benchmarks/puyo-v1-7-2-worker-proposals/` に置く。4 seeds x 2 repetitions で、
`K=1/4/8` と deterministic preview node budget `48/96/192` を比較した。

| configuration | K | node budget | candidates | expanded nodes | latency p50 / p95 ms | peak memory p50 / p95 KiB |
|---|---:|---:|---:|---:|---:|---:|
| `k1-n96` | 1 | 96 | 1.00 | 96 | 157.52 / 160.12 | 919.42 / 999.92 |
| `k4-n96` | 4 | 96 | 4.00 | 96 | 325.11 / 357.54 | 1164.92 / 1254.81 |
| `k8-n96` | 8 | 96 | 8.00 | 96 | 565.21 / 614.80 | 1301.16 / 1363.58 |
| `k4-n48` | 4 | 48 | 4.00 | 48 | 297.86 / 327.25 | 871.83 / 881.01 |
| `k4-n192` | 4 | 192 | 4.00 | 192 | 380.41 / 402.62 | 1649.48 / 1947.74 |

latency は serialization を含む observational wall clock、memory は同じ区間の `tracemalloc` peak である。
全40 records で legal action、rank-0 compatibility、fixed shape、JSON round-trip、candidate legality、
latency-neutralized determinism が PASS した。

再現と検証:

```bash
python -m eval.v1_7_worker_proposal_benchmark run
python -m eval.v1_7_worker_proposal_benchmark verify
```

artifact:

- `benchmark_summary.json`
- `benchmark_records.json`
- `benchmark_report.md`
- `benchmark_manifest.json`
