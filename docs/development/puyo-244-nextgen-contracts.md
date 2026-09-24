# PUYO-244 次世代公開契約 v1

[設計書](puyo-234-next-generation-model-design.md)の §11，13，15 に対応する型・検証・fixture を `agents/nextgen_contracts.py` に定義する．探索器，selector，学習器の実装・能力評価は含まない．

## 型と読み込み

| Schema suffix | 型 | 境界 |
| --- | --- | --- |
| `request.v1` | `NextgenRequest` | `DecisionIdentity`，`PublicSnapshot`，`ExecutionContext`，`ControlContext` |
| `candidate_batch.v1` | `CandidateBatch` | `Candidate`，固定順の `TacticSummary`，`SearchCounters` |
| `features.v1` | `PolicyFeatures` | 正規化済み `values`，`missing`，`action_mask` と registry hash |
| `selection.v1` | `Selection` | 戦術，固定順位の候補，batch digest，rule/RL の出力 |
| `diagnostics.v1` | `Diagnostics` | 上記全体と任意の `ExecutionReceipt` の結合検証 |

各 schema の prefix は `puyo.nextgen.`．全型は frozen dataclass で，コンストラクタと `from_dict` の両方が型・有限数・局所条件を検査する．配列は tuple にコピーする．`from_dict` は省略 field・未知 field・未知 schema を拒否するため，nullable field も明示する．`from_json` は重複 key と非有限 JSON 数値も拒否する．`to_dict` は独立した JSON 値を返す．

`from_dict`/`from_json` の dispatcher は上記 5 schema のみを受け付ける．旧 `WorkerProposal` v2，旧 feature，旧 8 戦術の自動変換はない．将来，既存探索結果を再利用する場合は，公開 request を受け取り，旧値の出所・missingness を明示する専用 adapter を作る．旧 tactic ID の機械的な読み替えは行わない．今回，既存 runtime を変更する adapter は必要ない．

## 公開 snapshot と後続 adapter

`PublicSnapshot(own, opponent, events, information_mode="public_only")` は許可 field だけを運ぶ．`PublicPlayerState` は可視盤面，公開 current/NEXT/NEXT2 の最大 3 組，phase，公開 attack packet，score carry，全消し状態を持つ．producer は simulator を渡さず，公開情報をコピーする．

- `visible_board` は上から下への 6 列行．`None` は未観測，`0` は空，`PUBLIC_COLOR_IDS` は `NORMAL_PUYO_COLORS` 順の `1..N`，`PUBLIC_GARBAGE_ID=N+1` はおじゃま．`PUBLIC_CELL_TO_COLOR` で engine 色へ変換できる．`PuyoColor.value` とは異なる wire 表現なので，adapter は enum を明示変換する．hidden/ghost 行を含める場合は，公開履歴から復元できない cell を `None` にする責任を producer が持つ．
- `known_pieces` は同じ normal color ID の pair．未公開未来を追加しない．将来の `public_reconstructed` は現在拒否する．
- `score_carry` は非負整数．上限は runtime の設定可能な `target_score_per_ojama` に依存するため，adapter が対応する設定で検査する．
- `PublicAttackPacket` の `arrival_tick` と `landed_tick` は別 field．未確定値は `None`．`PublicEvent` は公開 placement/clear/attack/arrival/drop/phase の追跡値で，RL 入力に含めない．
- `ExecutionContext` の到達可能 mask は既存 `NUM_ACTIONS` 順．request/timeout tick，timing schema/digest，configured/measured を保存する．`ControlContext` は phase，固定 quota，template hash，公開 scenario provenance を持つ．環境 seed は持たない．

PUYO-248 はこの輸送型に公開 snapshot を変換し，timing の派生要約だけを `build_features` へ渡す．型検査だけで観測の公平性を証明したとは扱わない．private queue/seed の対照テストは adapter の責務である．

## 候補と結合

戦術順は `build_main, build_template, fire_main, cancel, counter, decisive_short_attack`．`TACTIC_REGISTRY_HASH` は順序を含む．fallback は `build_main` の候補で，第 7 戦術を作らない．

候補 ID は `candidate_id(identity, plan, assumptions)` で生成する．episode/player/decision，action/piece/provenance 列，snapshot/scenario/timing/profile/template hash を含める．request retry ID，経過時間，予測 score，順位は含めない．候補 ID と異なり，batch digest は予測 evidence・mask・counter を含み，経過時間だけを除く．

`CandidateBatch` は全候補の同一 decision，ID の一意性，rank の一意性，公開 prefix と補完の順序，各戦術の候補 ID・best ID・合法到達可能性を検証する．`not_found_within_budget` は構造的不可能と区別する．必要火力不足や短期攻撃の勝率は mask 検証へ持ち込まない．構造 witness の探索・評価は後続 provider の責務である．

`validate_request_batch(request, batch)` は snapshot/予測前提，公開 piece，reachable mask，固定 quota を照合し，到達可能 root がある場合に `build_main` 候補を要求する．`Selection.validate_batch(batch)` は同じ batch 内の mask 有効な戦術の best 候補のみを返す．`Diagnostics` はこれらに加え feature mask・receipt の要求候補・timing・activated snapshot を検証する．stale/timeout/fallback は `receipt.actor_trainable=False`．実行結果で選択結果を書き換えない．

## RL feature と checkpoint

`FEATURE_REGISTRY` は 93 個の名前・型・scale・clip・変換・missingness・生成元・version を固定順に持ち，`FEATURE_REGISTRY_HASH` で検査する．`build_features(named_summaries, action_mask)` には登録名の要約だけを渡す．未知名の盤面，tick，action 列，seed，scenario ID，future，path，digest，template ID は拒否する．

未評価・unsupported の `NumericEvidence` は `value=None`，評価済み・partial は有限数を要求する．feature 欠損は `0` と `missing=True`，partial の有限値は `missing=False`．evidence の status/source は batch sidecar に残す．`PolicyFeatures.actor_vector` は 93 個の正規化値＋93 個の missing bit，mask は別の bool[6]．入出力の実 tensor 化は後続 actor が行う．trajectory は実際に渡した `PolicyFeatures.to_dict()` を保存する．

`checkpoint_contract()` と `validate_checkpoint_contract(metadata)` は将来の weight load 前の必須境界である．policy ID，feature schema/hash，tactic hash/order，186 入力・6 出力，tactic-only を厳密照合し，旧 checkpoint の padding や暗黙読み込みを許可しない．ここには weight loader 自体はない．

## fixture と確認

`tests/fixtures/nextgen/` は各戦術・partial・fallback・unavailable の診断 JSON と feature registry snapshot．全候補は人工データで，実際の相殺・counter・定型成立能力の証拠にはしない．`tests.test_nextgen_contracts.make_diagnostics` が再現可能な fixture producer である．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m unittest tests.test_nextgen_contracts -v
```

roundtrip，未知 schema/field，NaN/Inf，型 coercion，不正 ID，参照/mask 不整合，公開値のコピー，feature の情報境界，旧 checkpoint 拒否を確認する．
