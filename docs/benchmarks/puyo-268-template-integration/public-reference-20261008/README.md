# PUYO-268：公開入力を揃えた reference 比較

`deep_chain_builder` reference の既存 adapter は自分の hidden 2 行を参照し，盤面 digest から future seed を導出する．従来の実対局比較では nextgen と入力条件が異なっていた．[比較器](../../../../eval/puyo_268_public_reference.py) は評価専用の入力 adapter でこの差を取り除く．production の探索・順位・phase・native ABI は変更しない．

## 比較境界

- reference の `DeepChainBuildFlow`（集約・配置選択を含む）をそのまま実行する．評価 process 内だけで盤面変換と scenario seed を差し替え，呼出し終了時に元へ戻す．この比較器は単一 process で逐次実行する．
- 双方は下 12 行と公開 current/NEXT/NEXT2 のみを利用する．上 2 行は公開情報として未知のまま残し，探索のための空推定を共用する．`public_board_complete=false` と `source=public_estimate` を保存する．未知を空の証明や完成保証へ変更しない．
- nextgen の各判断をすべて reference にも渡す．公開推定盤面の bytes SHA，既知組，到達 mask，未知補完を含む scenario 実列の digest，共有予算を照合し，不一致は例外で停止する．`exact-input-*.json.gz` はその入力・両候補 action・nextgen 探索 trace・receipt と reference 探索証跡を保存する．
- reference の shadow action は実行しない．実連鎖・premature・窒息は別々の新規 `SafeNoThreatMatch(seed)` を 40 設置まで実行して比較する．初期条件と公開組生成器は同じだが，異なる行動を採用した後の盤面軌跡は異なる．shadow の予測を reference の実績に数えない．
- depth 16／width 250／6 scenarios／shared 600000 nodes／target 10 は共通である．nextgen の template 128／response 256 は独立 quota として残る．総 CPU 時間や総 node 数まで同一と呼ばない．
- reference は定型を選択しないため，nextgen の初回選択 binding を受け渡した受動観測器で，双方の公開盤面が 14 設置以内に同じ required/guard を満たすかを測る．reference の phase 完了を捏造しない．nextgen の実 phase／実採用は raw の別列に残す．

## 測定結果

測定 source は `f7cdf7fe3d161b9738a6c0e6f413ce862df2f28e`．source hash の前後変化はない．native binary／host／affinity／thread／profile は `declaration.json`，各 raw の SHA は `summary.json` に保存した．次の実対局は各 1 回で，shadow 120 判断を追加の実対局や repeat と数えない．

| seed | policy | 実設置／40 | 初期 binding 成立（14 手以内） | 最大実連鎖 | premature | 終了 | decision p50／p95（秒） |
| --- | --- | ---: | --- | ---: | ---: | --- | --- |
| 55 | nextgen | 40 | 10 手 | 10 | 0 | 生存 | 0.4276／0.5088 |
| 55 | public reference | 40 | 未成立 | 10 | 0 | 生存 | 0.3458／0.4055 |
| 123 | nextgen | 40 | 9 手 | 10 | 0 | 生存 | 0.4099／0.5781 |
| 123 | public reference | **30** | 未成立 | **0** | 0 | **到達不能 root の拒否で未完了** | 0.3332／0.4070 |
| 124 | nextgen | 40 | 9 手 | 11 | 0 | 生存 | 0.4111／0.5327 |
| 124 | public reference | 40 | 未成立 | 11 | 0 | 生存 | 0.3609／0.4218 |

nextgen の初回 GTR は 3/3，参照用の同一初期 binding の受動成立は reference 0/3．reference の定型戦術を評価した数字ではない．nextgen は全 120 設置で premature／窒息／scheduler error が 0．reference は 2/3 run だけが 40 設置へ到達した．seed 123 は tick 1618，30 設置後の判断で action 1 が到達 mask 外となり，既存 validator が拒否した．窒息イベントは未観測だが，この run を生存成功や 40 手完走に数えない．latency は拒否した判断も含む 31 件である．残り 10 設置の連鎖・生存結果は未知である．

全 **120/120** nextgen 判断で公開推定盤面／既知組／mask／scenario 実列／shared 600000 nodes の一致を確認した．shadow reference の拒否は **1/120**（seed 123 の 30 番目判断，action 1）で，raw から削除していない．これは上記の独立した reference 実対局の拒否とは別の盤面・判断である．legacy reference の選択 flow は幾何合法 root を順位付けし，到達 mask は最後の validator で確認するため，mask 外の最高順位 root が拒否される差が顕在化した．本比較は reference の選択を修正せず，未完了も結果として報告する．

3 seed の nextgen の観測は良好だが，一般品質・正式 G2 の合格を示さない．旧 reference の ghost 行あり・別 seed 導出での最大連鎖 1/10/10 とも実験条件が異なるため，今回の 10/0/11 を単純な性能改善率にはしない．

探索を途中で打ち切った開発時 run は最終比較に混ぜない．`interrupted/manifest.json` に source と残した一時 raw の SHA を，対応 log に停止理由を保存した．最初の NumPy 保存エラー，次の shadow 拒否，最後の live 拒否を修正して記録可能にし，同じ 3 seed・40 設置上限・quota で全条件を新規再実行した．

## A/C 対照

| A/C | 実装・証拠 |
| --- | --- |
| ペルシャ横 2／L 字と補充による 4 消し，色置換・mirror・合法尾 | `tests.test_template_preserving_integration.CatalogCompilerTests`，凍結した [PUYO-271 fixture](../../../../tests/fixtures/puyo_271_regression_cases.json) と [旧新比較](../../puyo-271-regression/after/README.md)．L 字一般や必要単発の禁止ではない． |
| 固定 variant/transform/binding，矛盾・未知・quota cutoff の区別 | 同 suite の compiler/shared tests と [接続仕様](../../../development/puyo-268-template-integration.md)．sampled 完成を既知保証にしない． |
| 公開完成手順と実採用，3 定型・完成離脱・14/15 手・生存例外 | 同 suite の `SharedConstraintTests`／`TemplateReceiptTests`，`tests.test_template_phase`．今回の保存 raw は候補／公開入力／receipt を含む． |
| 同じ公開入力と固定予算の reference 比較 | 本比較の全 nextgen 判断に対する shadow 照合と，固定 3 seed の実対局指標を分けて保存する． |
| seed 123 の同進捗 root と公開 prefix，55/123/124 の前後比較 | [2026-09-27 証跡](../public-prefix-compact-20260927/README.md) の exact request 6：旧 root 1／新 root 0，同じ現在 +2 に対し公開 prefix +2／+3．旧失敗 seed 55 と初回 2/3 は保持する． |
| 人間 GUI | [Jira コメント 10768](https://shhchan.atlassian.net/browse/PUYO-268?focusedCommentId=10768) の 2026-09-28 依頼者 OK．今回は GUI を再実行していない．元 seed 123 GUI raw は未保存で，同一 run と主張しない． |

### 3 定型と完成可能性の詳細

以下の fixture は今回の 40 tests に含む `tests.test_template_preserving_integration` で再実行した．将来 witness は公開 current/NEXT/NEXT2 内の存在証拠であり，任意の未知未来に対する完成保証ではない．

| 定型／境界 | 選択土台・色条件 | 検証した完成可能性・保持 | 判定 |
| --- | --- | --- | --- |
| GTR | A≠B，B≠C，A=C を許し alias binding を compile．左の A 3 連結への A 接続を guard． | `test_three_catalog_rollouts_complete_with_backend_ranked_roots` の公開 2 手 fixture が無消去で完成．`test_public_prefix_progress_preserves_fixed_shape_within_quota` は seed 123 の同進捗 root と +3 witness を色置換・quota 0/22/128 で照合． | 個別契約を確認 |
| だぁ積み | A≠B，A/B の L 字 3 連結は必要な形．上から同色 4 個目を足す guard を維持． | 同じ 3 定型 rollout test が選択 binding を保つ中間配置から無消去で完成．他色支持，mirror，色置換を `test_all_shapes_allow_other_color_support_and_unrelated_tail` で確認． | 個別契約を確認 |
| ペルシャ | A/B/C 相異．底面の A 横 3 と C 横 3 を保持し，報告された左 A の L 字を guard で識別． | 横 2→補充の整合 root を Python/native で比較し，破壊 root を除外．同 3 定型 rollout の公開完成 root・現在 1 手完成の終端特例を確認． | 個別契約を確認 |
| 共通の中間配置 | 選択 variant/transform/binding のまま，他色支持・合法尾を許可．`.` 全体を empty に置換しない． | `test_all_shapes_allow_other_color_support_and_unrelated_tail`，固定矛盾時の phase 終了，quota 0 で固定 binding を維持する tests． | 個別契約を確認 |
| 共通の完成離脱 | witness の存在だけでは完成扱いにしない．実採用・次公開盤面で完成を確認した後に制約を全解除． | 3 定型の `test_completion_and_limit_release_backend_constraint`，GTR の実 scheduler receipt，ペルシャの採用／timeout trace． | 個別契約を確認 |
| 14/15 手・生存 | 14 回目の採用を許し，15 回目で定型を無効化．stale/timeout/fallback は消費しない．必要な生存単発は定型違反より優先． | `tests.test_template_phase` と `test_required_survival_clear_overrides_all_template_violations`． | 個別契約を確認 |
| public estimate | 未観測 guard で完成を閉じない．sampled 完成・unknown/cutoff を公開完成と区別． | `test_unknown_guard_cannot_close_an_otherwise_complete_phase`，`test_unknown_cutoff_and_public_completion_are_not_conflated`，新 adapter の poisoning／scenario tests． | 個別契約を確認 |

初回だぁ積みの L 字の細部は依頼者が 2026-09-27 に範囲外とした catalog 仕様である．L 字一般を禁止する変更も，この範囲外の見た目を「修正済み」とする扱いも行わない．ペルシャの報告 L 字と区別する．元 GUI の seed 不明のセカンド逆発火は PUYO-266 のまま残す．

正式 G2 と完成後の生存・発火品質は PUYO-266 が所有する．既存の [60 run](../../puyo-266-safe-build/integrated-g2-20260927/README.md) は **observed quality FAIL／G2 BLOCKED**（平均 8.8667，premature 6，窒息 10）を保持する．本比較や人間 GUI OK は G2 PASS，学習開始，release の許可ではない．

## 再現

既存 raw の上書きを防ぐため，出力は存在しない directory を指定する．native を含む既存環境を読み取り専用で使用し，共有環境へ install/build しない．

```bash
env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=6 \
  /home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  -m eval.puyo_268_public_reference --output /tmp/puyo268-new-public-reference \
  --seeds 55 123 124 --placements 40
```

境界テストは hidden/private の poisoning，公開セル・既知組・mask の保持，scenario/予算一致と予算不一致時の停止を確認する．到達 mask 外の shadow action は validator の拒否を記録し，代替 action を選ばず，実行もしない．全 shadow 判断の分母に含める．reference の実対局では従来どおり fail-closed にし，停止までの実設置・連鎖・入力と拒否した判断を未完了 run として残す．途中停止を 40 手生存や窒息 0 の成功 run と数えない．この拒否は reference 側の制約遵守の差として保存する．

定型回帰と合わせた 40 tests が成功した．追加で `tests.test_puyo_271_regression` と `tests.test_nextgen_template_catalog` の 3 tests も成功し，合計 43 件を確認した（`tests.log`／`fixtures.log`）．最初に root の別 venv を使った回帰は native module 未導入で 3 errors となったため，既存 native 環境へ切り替えて全件を再実行した．

```bash
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python -m unittest \
  tests.test_puyo_268_public_reference tests.test_template_preserving_integration \
  tests.test_template_phase
```

保存証跡は，探索をせず次のコマンドで raw の SHA，指標，公開盤面・既知組・mask・scenario 列と予算を再検証できる．

```bash
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python \
  docs/benchmarks/puyo-268-template-integration/public-reference-20261008/verify.py
```
