# PUYO-234 次世代モデル実装バックログ

2026-09-23起票。設計の正本は[設計書](puyo-234-next-generation-model-design.md)と[PR #135](https://github.com/shhchan/puyo-ai-dev-platform/pull/135)。全15件は独立Task、親[PUYO-228](https://shhchan.atlassian.net/browse/PUYO-228)、Medium、To Do、未割り当て、Sprint未設定。What / Why / How / A/C / References は各Jira descriptionに記入（Task typeに別の文脈custom fieldはない）。

## Taskと直接依存

表の前提Taskが当該TaskをBlocksする。全TaskをPUYO-234へRelatesし、完了済みの設計を実行待ちブロッカーにはしない。既存PUYO-233↔234 Relatesは維持し、236ともRelatesで能力証拠を接続する。

| Task | レビュー単位 | 直接の前提（Blocks） | 設計書 |
|---|---|---|---|
| [PUYO-244](https://shhchan.atlassian.net/browse/PUYO-244) | 公開契約・6戦術schemaと契約fixtureを実装する | なし | §11, 13, 15 |
| [PUYO-245](https://shhchan.atlassian.net/browse/PUYO-245) | 底面基準template config matcherと選択器を実装する | PUYO-244 | §12.1, 12.2 |
| [PUYO-246](https://shhchan.atlassian.net/browse/PUYO-246) | GTR・だぁ積み・ペルシャ式catalogと14手成立fixtureを追加する | PUYO-245 | §12, 17.1 |
| [PUYO-247](https://shhchan.atlassian.net/browse/PUYO-247) | 定型phaseの14decision上限と対応後再選択を実装する | PUYO-245 | §12.3, 13.4 |
| [PUYO-248](https://shhchan.atlassian.net/browse/PUYO-248) | 公開対戦snapshotとtiming要約adapterを実装する | PUYO-244 | §13.2, 13.4, 14 |
| [PUYO-249](https://shhchan.atlassian.net/browse/PUYO-249) | 固定総予算の共有探索batchと6戦術候補要約を実装する | PUYO-244, PUYO-245, PUYO-248 | §11, 13.1–13.3 |
| [PUYO-250](https://shhchan.atlassian.net/browse/PUYO-250) | 期限内相殺・落下後一手counter・短期攻撃候補を実装する | PUYO-248, PUYO-249 | §13.3, 14, 17 |
| [PUYO-251](https://shhchan.atlassian.net/browse/PUYO-251) | rule戦術selectorとscheduler実行経路を接続する | PUYO-247, PUYO-249, PUYO-250 | §12.3, 13, 14 |
| [PUYO-252](https://shhchan.atlassian.net/browse/PUYO-252) | decision trajectoryとartifact検証契約を実装する | PUYO-244, PUYO-248 | §15.1, 15.2 |
| [PUYO-253](https://shhchan.atlassian.net/browse/PUYO-253) | 戦術・土台・遷移履歴と開始設定をGUIへ追加する | PUYO-246, PUYO-251, PUYO-252 | §16, 17.1 |
| [PUYO-254](https://shhchan.atlassian.net/browse/PUYO-254) | 採用・不採用の節目と採用経路をlineage表示へ追加する | PUYO-252 | §15.3, 16 |
| [PUYO-255](https://shhchan.atlassian.net/browse/PUYO-255) | 候補能力・戦術選択・timing・latencyを分離する評価gateを実装する | PUYO-246, PUYO-251, PUYO-252 | §11, 17 |
| [PUYO-256](https://shhchan.atlassian.net/browse/PUYO-256) | 要約特徴6戦術bootstrap trainerとcheckpoint互換検査を実装する | PUYO-251, PUYO-252 | §13.4, 15.2, 17.3 |
| [PUYO-257](https://shhchan.atlassian.net/browse/PUYO-257) | realtime自己対戦PPOとcurriculum・報酬ledgerを実装する | PUYO-256, PUYO-255 | §15.2, 17 |
| [PUYO-258](https://shhchan.atlassian.net/browse/PUYO-258) | 段階学習・paired評価・GUI QAを実施し次世代モデル採否を記録する | PUYO-253, PUYO-254, PUYO-255, PUYO-257 | §17–19 |

## 着手順

1. PUYO-244で契約とfixtureを確定。
2. PUYO-245と248を進める。245後に246（形状pack）/247（phase）、248後に252（記録）を独立して進められる。
3. PUYO-249→250→251で探索・応答候補・ruleの実行経路を完成。252後の254（lineage）はfixtureで独立実装可能。
4. 246/251/252後に253（GUI）と255（評価）。251/252後に256（bootstrap配線）も可能だが、G2未達では本学習しない。
5. 255/256後に257（PPOコード）。253/254/255/257後に258（段階実験・採否）。長時間実行にはG2/G3と資源判断の記録が必要。

Blocksはコード/成果物の依存であり、チケット完了が能力ゲートPASSを意味しない。特に255はFAIL/BLOCKEDという正しい評価結果でもTask成果は完了し得る。258はgate artifactを検査し、失敗したら長時間実行を停止して原因別改良の計画へ戻す。

## 各Taskの成果・検証境界

### PUYO-244: 公開契約・6戦術schemaと契約fixtureを実装する

- 成果: nextgen request/candidate_batch/features/selection/diagnostics v1と6戦術IDを型・validator・fixtureとして固定する。
- 理由: 旧8戦術・parameter/ranker学習契約との混同を防ぎ、後続をmockで独立実装できるようにする。
- 実装方針: agents/nextgen_contracts.py（新規案）とtests/fixturesへschema、missingness、ordered feature registry/hash、候補ID、mask理由を追加。旧WorkerProposal v2との変換は明示adapterだけにする。
- A/C: 6戦術の順序が固定され、unknown schema/NaN/Inf/不正ID/候補参照不整合を拒否する。raw board/tick/seed/action列がRL特徴へ入らない。各戦術・partial・fallbackのserialize roundtripが成功し、既存checkpointはsilent loadされない。
- 既存の接続先: `agents/worker_proposals.py`、`agents/v1_7_strategy_manager.py`

### PUYO-245: 底面基準template config matcherと選択器を実装する

- 成果: puyo.template_catalog.v1のloader、色関係/構造照合、fit witness、argmax/softmax選択を実装する。
- 理由: 開始時の土台を探索器が選び、形状追加をconfigだけで行える共通基盤が必要。
- 実装方針: agents/chain_styles.pyのprovider拡張点と新matcherを接続。底面座標、same/different、wildcard/empty/occupied、mirror/底上げ、非単射の色割当、既知prefix内進捗を実装。選択専用RNGを記録。
- A/C: A=Cを許可し明示異色だけを制約にする。低fitでも開始時は有効候補から1つ選ぶ。空catalogは開始エラー。argmax tie-breakとsoftmax seed再現、再選択時fitなし自由構築、不正config拒否のfixtureが通る。
- 既存の接続先: `agents/chain_styles.py`、`tests/test_chain_styles.py`

### PUYO-246: GTR・だぁ積み・ペルシャ式catalogと14手成立fixtureを追加する

- 成果: 3定型の本番variant config、形状図、色関係、固定盤面・公開ツモfixtureを登録する。
- 理由: 記法の実装だけでは定型の正しさや14手以内の構築能力を証明できない。
- 実装方針: train/config/nextgen_templates.yaml（新規案）と対応図・出典・tests fixtureを追加。人間がvariantの形状と色条件を確認できる資料を用意し、人工schema例とは分離する。fixtureは結果を見る前に固定。
- A/C: 3定型を含み、各variantの成立/不成立と同色許容を照合できる。選択器と合法配置を使う脅威なしfixtureで14自decision以内に成立する。形状レビュー記録を残す。任意ツモ全体での保証とは主張しない。
- 既存の接続先: `train/config/v1_7_chain_styles.yaml`、`tests/test_chain_styles.py`

### PUYO-247: 定型phaseの14decision上限と対応後再選択を実装する

- 成果: template phase state machine、途中完成/中断/上限、応答・発火後の再選択を実装する。
- 理由: 同一組の再探索や脅威切替で手数がリセットされる不具合と、古い土台への無条件復帰を防ぐ。
- 実装方針: 新phase controllerにactivated decisionとauthoritative resolution eventを入力する。同一組のretry/replanは最大1消費。phase_idとexit_reasonを診断へ出す。mock eventで独立検証する。
- A/C: 14回目まで許可し15回目は自由構築。同組replanで枠を増やさない。相殺/counter/本線/短期攻撃完了後に現在盤面から1回再選択しfitなしは自由構築。脅威中に繰り返しphaseを開始しない。
- 既存の接続先: `agents/decision_flow.py`、`puyo_env/realtime_ai.py`

### PUYO-248: 公開対戦snapshotとtiming要約adapterを実装する

- 成果: PublicVersusSnapshotと脅威余裕・対応時間区間・先打ち時間損失の派生要約を実装する。
- 理由: 現deep_chain_builderは自盤面中心で、旧raw tick特徴やsimulatorを渡すと公平性と時間差の契約が崩れる。
- 実装方針: puyo_env/realtime_ai.py/realtime_versus.pyの公開fieldからallowlistコピー。hidden rowは公開履歴から復元可能な範囲だけ。score carry/bonus・到着/着地・発火開始/完了を分け、configured/measuredとschema hashを保存。
- A/C: private queue/seedだけの変更で公開snapshotと派生特徴が不変。連鎖中の相手独立進行、同tick相殺、carry/bonus一回消費、arrival≠dropのfixtureが通る。生tickとsimulatorはRL入力にない。
- 既存の接続先: `puyo_env/realtime_ai.py`、`puyo_env/realtime_versus.py`、`agents/state_analyzer.py`、`tests/test_realtime_ai.py`

### PUYO-249: 固定総予算の共有探索batchと6戦術候補要約を実装する

- 成果: deep_chain_builderの共有探索結果から戦術別候補・mask・summary・固定候補順位を返すbatchを実装する。
- 理由: 6戦術分の重複フル探索を避け、候補不足と戦術選択失敗を分離する。
- 実装方針: DecisionFlow/backendへ共有root/既知prefix/scenarioとB_shared/B_template/B_responseの固定quotaを接続。template進捗評価を候補生成へ組み込み、root/全scenario証拠・known/sampled・fatal・cutoffを保存。response providerはinterface/mockまで。
- A/C: 総node予算が各quota総和内であり自動再配分しない。選択後の二重探索なし。Python/native parity、全root順位・provenance・repeat digestが一致。support/quietを安全保証にしない。非終端build_mainの合法fallbackを提供する。
- 既存の接続先: `agents/deep_chain_builder.py`、`agents/deep_chain_search_backend.py`、`agents/long_horizon_search.py`、`native/deep_chain_native/`

### PUYO-250: 期限内相殺・落下後一手counter・短期攻撃候補を実装する

- 成果: 公開情報から相殺準備/発火、最初の落下後一手counter、既知prefix短期攻撃の候補と根拠を生成する。
- 理由: safe-build探索をRLに接続するだけでは期限と着地に応じた具体手が存在しない。
- 実装方針: 共有batchのresponse quotaでbounded search。期限の見積り区間、必要火力との差、発火点・残packet、未知落下列のconditional証拠を記録。短期攻撃horizon初期3公開組。
- A/C: incoming31/60超でも最初の最大30落下後一手を検証する。火力不足や勝利確率でmaskしない。構造不可と予算内未発見を分ける。counterの全量5段制限なし。必須脅威fixtureの候補coverageと計算費用を報告する。
- 既存の接続先: `agents/strategy_workers.py`、`agents/v1_7_planner.py`、`puyo_env/realtime_versus.py`

### PUYO-251: rule戦術selectorとscheduler実行経路を接続する

- 成果: 6戦術のrule baselineと新policy factoryを実装し、phase・候補batch・schedulerへ接続する。
- 理由: RL前に観測→候補→戦術→具体手→実結果の経路を検証し、教師軌跡の基準を作る。
- 実装方針: teacher初期優先順をconfig化し、脅威/短期機会/定型/飽和本線を選択。候補内部順位は探索器が固定選択。stale/timeout/illegalを実行器で再検証しrequestedとexecutedを分ける。
- A/C: 開始template選択をrule/RL出力にしない。逼迫だけでtemplateを強制解除しない。短期勝利判定をmaskに流用しない。ruleと将来RLが同一batchを利用でき、fallbackを第7戦術にしない。scheduler/replayと診断が一致。
- 既存の接続先: `selfplay/policies.py`、`agents/strategy_manager.py`、`puyo_env/realtime_ai.py`

### PUYO-252: decision trajectoryとartifact検証契約を実装する

- 成果: 実際のpolicy_input、候補根拠、実行receipt、reward、episode結果を結合できるJSONLとmanifestを実装する。
- 理由: 後から再計算した観測やfallbackを教師行動として誤学習せず、同じrunを監査・再現するため。
- 実装方針: train/artifacts.pyを再利用しextra.nextgen、episode/events/evidence sidecar、SHA/bytes、独立seed streams、native provenanceを追加。policy入力allowlistとoracle別保存を実装。fixture producerで独立検証。
- A/C: 各decisionの実入力・mask・選択・実actionが一意に結合できる。partial/incomplete/truncatedを区別。破損SHA/欠損参照/旧schemaを拒否。fallback actor除外を記録しseed/未来/oracleはtrain loaderに入らない。
- 既存の接続先: `train/artifacts.py`、`train/v1_7_bootstrap_dataset.py`、`tests/test_artifacts.py`

### PUYO-253: 戦術・土台・遷移履歴と開始設定をGUIへ追加する

- 成果: 対戦sidebar、履歴/replay、template/selector/profile/timing設定を既存pygame GUIへ追加する。
- 理由: 人間が採用された戦術・切替理由をリアルタイムに確認し、同じconfigで実験できる必要がある。
- 実装方針: versus_renderer/launcher_settings/model_viewerへactivated診断を接続。template有効群、argmax/softmax、温度、N、profile等を検証・保存。pattern編集はファイルのまま。
- A/C: 戦術・土台/自由構築・理由・履歴がledgerと一致。fit score/確率は対戦GUIに出さない。無効configは開始拒否し設定roundtripが通る。通常ウィンドウで切替/履歴追従の人間QAを記録、dummyだけをPASSにしない。
- 既存の接続先: `src/ui/versus_renderer.py`、`src/ui/launcher_settings.py`、`src/ui/model_viewer.py`

### PUYO-254: 採用・不採用の節目と採用経路をlineage表示へ追加する

- 成果: rule/template/bootstrap/RL/curriculum/評価のlineageと採否詳細・主グラフを実装する。
- 理由: 不採用を隠すと試行理由と採用根拠を辿れず、研究履歴とplayable登録も混同する。
- 実装方針: 既存node/edgeとmetadataを優先利用。evaluationに採否/scope/reason/gate SHAを記録し、pygame model_viewerで主経路・詳細panel・checkpoint折り畳みを実装。
- A/C: 採用と不採用が同じ主グラフに出る。明示採用記録からだけ経路を強調し、最新/高scoreで推定しない。欠損artifact警告・旧manifest読み込み・採用取り消し/保留のfixtureが通る。不採用をplayable registryへ登録しない。
- 既存の接続先: `train/lineage.py`、`src/ui/model_viewer.py`、`tests/test_lineage.py`、`tests/test_model_viewer.py`

### PUYO-255: 候補能力・戦術選択・timing・latencyを分離する評価gateを実装する

- 成果: G0〜G4のmachine-readable判定器、比較行列、候補gap/候補順位/戦術選択regret、能力・性能preflightを実装しG1/G2を測定する。
- 理由: PUYO-236 only240は採用済みだが品質未達であり、Jira完了や接続smokeを学習開始資格にできない。
- 実装方針: 既存30seed×2×40 safe-buildと脅威/定型fixture、同条件search-only/rule、paired-side比較、private境界、phase timing/RSSを計測。全root証拠とoracle sidecarを分離。閾値・seedを実行前固定。
- A/C: only240既存失敗をPASSにせずG2 BLOCKEDを正しく出す。candidate gapと2種類のselection失敗を別出力。全seed/未完了/分母・source/build/config SHAを保持。p50/p95と学習throughput見積りを報告。FAIL判定でも評価Task自体は成果完了可能。
- 既存の接続先: `eval/deep_chain_builder_benchmark.py`、`eval/deep_chain_safe_build_benchmark.py`、`eval/realtime_arena.py`

### PUYO-256: 要約特徴6戦術bootstrap trainerとcheckpoint互換検査を実装する

- 成果: 新MLP actorとmasked CE、dataset split、6戦術checkpoint、confusion reportを実装する。
- 理由: 旧board入力・8戦術・parameter headの重みを流用せず、rule軌跡から初期選好を学ぶ必要がある。
- 実装方針: 新trajectory loaderと既存checksum/restoreを組み合わせ、episode/配色系列でsplit。template継続のteacher/biasを初期化し、戦術class別recallを記録。
- A/C: 出力は戦術IDだけで候補rank/parameter headなし。mask外確率0、特徴順/hash不一致を拒否。2,000decision以内の配線smokeは品質合格と区別。G2未達では本学習を開始しないpreflightが働く。
- 既存の接続先: `train/train_v1_7_manager.py`、`agents/manager_ppo.py`、`train/restore.py`

### PUYO-257: realtime自己対戦PPOとcurriculum・報酬ledgerを実装する

- 成果: 6戦術MLP actor-critic/PPO、時間差discount、勝敗主報酬、凍結opponent poolと開始gateを実装する。
- 理由: 先打ちによる相手の構築猶予を反映し、小連鎖reward farmingや候補能力未達の長期学習を防ぐ。
- 実装方針: RealtimePuyoEnv共通scheduler、variable-dt GAE、masked old/new log-prob、fallback actor除外、潜在差reward ablation、episode境界curriculumを実装。短いsynthetic smokeでlossを検証。
- A/C: 戦術パラメータ/候補rankerを学習しない。終端/打切り/timeoutを区別。carry/bonusを二重報酬にしない。G2未達・stale artifact/schema mismatchでlong-runを拒否。resumeにoptimizer/RNG/pool/hashを復元できる。
- 既存の接続先: `agents/manager_ppo.py`、`puyo_env/realtime_ai.py`、`train/train_manager.py`

### PUYO-258: 段階学習・paired評価・GUI QAを実施し次世代モデル採否を記録する

- 成果: 資源確定後、bootstrap pilot→短期PPO→長期候補→G4を実行し、採用/不採用/保留とlineageを記録する。
- 理由: コード実装完了と強さ・品質・リアルタイム採用を分離し、best seedだけの選抜を避ける。
- 実装方針: G2/G3を実artifactで検査。pilot上限、5 training seeds、相手層別100 paired seeds、評価前の数値凍結、measured通常GUI QAを行う。CPU/thread/時間上限・version/releaseは人間判断を記録して進む。
- A/C: 全seed・失敗・CI・safe-build・脅威・template中断・先打ち・発火後生存・latencyを報告。不採用もlineageに残す。G4未達はplayable昇格しない。Task開始やコード完了をlong-run承認とせず、旧waiverを継承しない。
- 既存の接続先: `eval/realtime_arena.py`、`train/lineage.py`、`src/ui/model_viewer.py`

## 関連証拠と運用

- PUYO-249をPUYO-233、PUYO-255をPUYO-236へRelates。only240採用と品質未達を両方引き継ぐ。PUYO-240/241/242の全案を新計画のBlocksにしない。
- 親PUYO-228は現段階の設計・計画管理を維持。将来のモデル版・正式releaseの約束は追加しない。
- 実装ブランチは `integration/puyo-113-v1-7-2` 起点、PR baseも同じ。各Taskに着手するときだけIn Progressへ移す。
- schema/config更新、コード、実験結果はレビュー可能な意図単位でcommit。研究artifactには失敗・不採用も含める。
- まず人間が確認するものはPR #135の設計と依存表。次に246の形状図、255の測定結果、258前の資源・評価閾値を判断する。
