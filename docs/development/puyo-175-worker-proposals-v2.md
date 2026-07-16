# PUYO-175 Worker Proposal / Ranker Contract v2

## Scope

PUYO-175 は PUYO-174 の six-scenario expected-chain root evidence を、runtime、
replay、benchmark、将来の learned ranker が同じ形で利用できる proposal contract へ接続する。
探索結果を consumer 側で盤面から再計算しない。

公開 schema は次のとおり。

- batch: `puyo.worker_proposal_batch.v2`
- candidate: `puyo.worker_proposal_candidate.v2`
- candidate evidence: `puyo.worker_candidate_evidence.v2`
- shared context: `puyo.worker_proposal_shared_context.v2`
- ranker input: `puyo.worker_candidate_ranker_input.v2`
- lossy compatibility projection: `puyo.worker_candidate_ranker_projection.v2-to-v1`

PUYO-169 の v1 stable candidate ID、固定 K、padding、legal root、rank-0 compatibility、
empty semantics は変更しない。batch/proposal ID は schema ごとに再計算されるが、同じ decision と
action sequence の candidate ID は v1/v2 間で一致する。

## Candidate-local / decision-shared boundary

候補間で異なる evidence と、1 decision に一つしかない探索コンテキストを分離する。

| namespace | ownership | contents |
|---|---|---|
| candidate identity | candidate-local | stable candidate ID、root action、action sequence、legal root |
| immediate preview | candidate-local | candidate value、chain/score/attack、BuildPotential、danger |
| `evidence.expected_chain` | candidate-local | scenario 別結果、sum/mean/worst/dispersion/maximum、support/coverage |
| `evidence.structural_chain` | candidate-local | potential、required keys、trigger height、connectivity/extension、danger、tear/waste |
| `evidence.trajectory` | candidate-local | max chain depth、terminal/premature/target fire |
| `evidence.scenario_vector` | candidate-local | canonical scenario slot ごとの値と mask |
| profile/search configuration | decision-shared | profile/version、depth、width、candidate/node/potential budget |
| scenario identity | decision-shared | known queue length、scenario ID、sequence digest、mask、set digest |
| aggregate search cost | decision-shared | total expanded/pruned/TT hit、elapsed、p50/p95 interval |
| worker deadline | decision-shared | budget、status、overrun、source |

v2 の candidate ranker row に `search_latency_ms` と `expanded_nodes` は含めない。
これらは `shared_context` で一度だけ渡す。モデルが shared context を使う場合も、candidate row へ
複写せず別入力として宣言する。

## Evidence and missingness

optional numeric は JSON number 単体ではなく、次の object として保存する。

```json
{
  "value": 0.0,
  "is_present": true,
  "evaluated": true,
  "status": "evaluated"
}
```

`status` は次の4種類である。

| status | value semantics | ranker candidate mask |
|---|---|---|
| `evaluated` | 評価が完了した。0 は有効な観測値 | enabled |
| `not_evaluated` | 評価を実行していない。value は null | disabled |
| `budget_exhausted` | bounded search が途中終了した。得られた有限値は保持 | enabled |
| `legacy_missing` | v1 artifact に evidence 自体が存在しない。推測で補完しない | disabled |

padding candidate、illegal candidate、fallback/missing evidence も learned ranker の
`candidate_mask` から除外する。compatibility selection は proposal の slot 0 を使い、v1 の
single-best action を維持する。

NaN と infinity は `MaskedNumeric`、candidate evidence、shared context、ranker input の
serialization boundary で拒否する。欠落値を暗黙の 0 として保存しない。ranker tensor の padding
値は 0 だが、必ず feature/candidate/scenario mask と組で解釈する。

## Scenario identity and shape

scenario slot は最大6件の固定長で、実 slot の後に padding を置く。

- shared context は `scenario_ids` の昇順に正規化する。
- 各 slot は scenario ID と sequence digest を持つ。
- ID/digest/mask の canonical digest を shared context と全 candidate evidence で共有する。
- candidate-local scenario values は ID で canonical slot へ割り当てる。
- padding scenario は null と false mask で表し、scenario ID 0 や評価値 0 と区別する。
- serialize 後の順序変更や digest 不一致は reader が拒否する。

ranker input は `K x 43` candidate features と feature mask に加え、
`K x 6 x 9` scenario features、scenario feature mask、candidate-scenario mask を返す。
feature 名、scale、signed/clamp、scenario shape は `candidate_ranker_schema_metadata()` の
schema hash に含まれる。feature の追加や順序変更を同じ hash で解釈してはならない。

## Lossless evidence namespaces

`CandidateEvidence` は ranker 用の正規化値だけでなく、次の source mapping を lossless に保持する。

- PUYO-174 `ExpectedChainRootEvidence.to_dict()` の全体
- representative `ChainStructureEvaluation.to_dict()` の全体
- terminal fire reason と premature/target flag を含む trajectory
- BuildPotential の evaluated/budget/truncation status
- scenario ごとの max chain count/score、depth、completion、terminal fire、探索統計

ranker feature row はこの保存済み evidence から構築する。worker、manager preview、benchmark writer
は `WorkerProposalBatch` の同じ serialized object を参照し、別の feature extractor を持たない。

## Version dispatch and migration

`WorkerProposalBatch.from_dict()` は v1/v2 を schema version で dispatch する。
`migrate_worker_proposal_payload()` の挙動は次のとおり。

| source | target | behavior |
|---|---|---|
| v0 | omitted / v1 | PUYO-169 の明示 v0→v1 migration |
| v1 | omitted | byte-shape を書き換えず v1 として読む |
| v1 | v2 | candidate ID/rank/mask/action を保持し、存在しない evidence を `legacy_missing` にする |
| v2 | omitted | lossless v2 read |
| v2 | v1 | 既定では error。`allow_lossy_projection=True` の場合だけ限定 projection |
| unknown | any | error |

v2→v1 projection は、v1 の13 feature の順序と normalization を固定し、各 slot の
`feature_mask`（true は present）と `missing_feature_mask`（true は absent）、drop する
namespace、source/target schema hash、deterministic digest を
artifact に保存する。drop 対象は expected-chain、structural-chain、trajectory status、scenario
vector、shared context である。

`ranker_input_for_model()` は model contract の schema version と schema hash を照合する。
v2 input を v1 model に silent に渡さない。v1 を使う場合は
`allow_compatibility_projection=True` と v1 schema/hash の両方が必要である。

## Checkpoint and production path

v1.7 strategy manager checkpoint metadata は
`puyo.v1_7_strategy_manager.checkpoint_metadata.v4`、migration record は
`puyo.v1_7_2_checkpoint_migration.v3` へ更新した。metadata は proposal v2 と、既存 manager が
明示利用する v1 ranker schema/hash、compatibility projection schema を記録する。loader は
expected metadata と一致しない checkpoint を拒否し、過去 checkpoint は migration path でのみ
更新する。

production の `build_main` worker は PUYO-174 の `runtime` long-horizon search profile を使う。
worker が作成した `WorkerProposalBatch` は `SearchProposal.worker_proposal_dict` に一度だけ serialize
され、manager preview と最終 worker result が同じ payload を保持する。named-style evaluator が
有効な PUYO-168 path は既存 namespace/backend を維持する。compact kernel が扱わない色を含む
current/NEXT2 fixture は `runtime-fallback-legacy` として明示的に simulator backend へ戻し、
evidence を `not_evaluated` に mask する。PUYO-166 の既存 budget sweep も再現用に legacy profile を
明示指定する。

## Benchmark

追跡 artifact は `docs/benchmarks/puyo-v1-7-2-worker-proposals-v2/` に置く。
固定 corpus は K=8、six scenarios、depth 1、132 expanded-node budget を使用し、各 seed を2回実行する。
corpus には evaluated zero、not evaluated、budget exhausted、legacy missing を必ず含める。

report は次を記録する。

- status 別 candidate 数
- candidate/scenario feature coverage
- serialized payload size
- serialize + v2 readback time
- v2→v1 projection time
- v1 で 0 と missingness が混同される件数
- serialized proposal、ranker input、projection の deterministic digest
- round-trip、K=8、scenario mask、candidate ID、rank-0 compatibility の checks

deterministic digest では elapsed/p50/p95 に加え、wall-clock から派生する deadline
status/overrun を neutralize する。deadline budget と source は保持するため、契約設定の変更は digest
差分として残る。

再現と checksum 検証:

```bash
python -m eval.v1_7_worker_proposal_v2_benchmark run
python -m eval.v1_7_worker_proposal_v2_benchmark verify
```

artifact:

- `benchmark_summary.json`
- `benchmark_records.json`
- `field_dictionary.json`
- `benchmark_report.md`
- `benchmark_manifest.json`
