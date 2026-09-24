# PUYO-251 rule selector と scheduler 接続

`selfplay.policies.make_policy("nextgen_tactic_manager")` は `NextgenTacticManagerPolicy` を返す．正式なモデルバージョンは未採番．初期 profile は接続検証用 `nextgen_smoke`（shared/template/response = 256/128/256）で，Python backend を使う．勝率，品質 gate，GUI の G1 合格を主張する設定ではない．

## 選択と探索

`agents/nextgen_tactic_manager.py` の `RuleSelectorConfig` が 6 戦術の優先順と teacher 閾値を持つ．初期順は即時脅威の期限内完全相殺，counter，公開盤面の占有数と送信量による短期攻撃の推定，active template，安全性 evidence のある飽和本線，大連鎖構築．完全相殺が見込めない場合も，相殺/counter の生存・相殺量・送信量を比較する．逼迫だけでは template を無効化しない．短期攻撃の勝利保証はなく，teacher の閾値は構造的 mask を変更しない．

`template_phase → shared_batch → select_tactic` の `DecisionFlow` を使う．開始 template の必須選択と対応後の再選択は `TemplatePhaseController` の責務で，selector の出力ではない．matcher は 1 回だけ実行する．`PreparedTemplateSearch` は公開 snapshot，identity，execution，catalog，profile，phase を含む request digest と選択 shape key に束縛される．batch builder は backend 実行前に検証し，matcher の node・feature・時間を同じ quota/counter に含める．template 候補は選択済み variant/transform/binding と一致するものに限定する．

selector を注入でき，rule と将来の RL は同一 `CandidateBatch` と `PolicyFeatures` を使う．RL actor へ渡す値は `PolicyFeatures.actor_vector` と mask のみとし，request/盤面/timing sidecar を actor 入力へ流さない．`Selection.validate_batch()` が mask，batch digest，固定順位の best candidate を検査する．選択後の再探索はない．合法な最小 fallback 候補も `build_main` 内にあり，第 7 戦術はない．

## Scheduler と receipt

`RealtimePolicyController.nextgen_scheduler` が対局単位の phase を所有する．worker process/thread には公開値だけの envelope と phase のコピーを渡す．既存の simulator，hidden row，実 queue，環境 RNG を含む `info` は worker に渡さない．policy を直接呼ぶ場合も `nextgen` envelope が必要で，旧 turn-based info へ暗黙に変換しない．

初期選択・照合は検証済み worker 結果から取り込み，timeout/retry で選び直さない．定型手数は採用 receipt だけで増やし，同じ実 piece の replan では最大 1 消費．公開 placement event 数が piece identity，request ごとの連番が request/diagnostic decision identity になる．対局 reset は episode ID を更新する．run の外部 episode ID を使う caller は最初の request 前に `nextgen_scheduler.episode_id` を設定できる．

採用直前に公開 snapshot digest と authoritative board の配置合法性・現在位置からの到達可能性を再検査し，低レベルの root plan を作り直す．hidden row のみの変更でも合法性検査は省略しない．不整合，timeout，stale，非法/到達不能は通常選択と区別して既存合法 fallback へ進む．未完成の async request では候補/selection を捏造せず，scheduler の timeout record と `nextgen_scheduler.errors` に残す．型契約のある ledger は検証済み selection が得られた request を保持する．

receipt の `executed_action` は scheduler が採用した現在組の root action を意味し，探索した複数手 plan の実現や勝利を保証しない．counter plan の後続手をキューへ追加しない．最初の実落下後，次の操作可能盤面で新しい snapshot/request/batch を作り直す．公開 packet/resolution history が phase の対応完了判定を駆動する．

operation cadence は `[1,120]` tick の明示的な smoke 見積りで，公開情報の `public_estimate` として記録する．実際の lock/landing 時刻ではない．latency mode，inference latency，timeout は controller 設定から生成する．timeout 未指定は wire 上の有限 sentinel `2**31 - 1` tick を用いる．この見積りの校正と measured latency の昇格評価は後続 gate の範囲．

## GUI / replay / ledger の読取境界

| 値 | schema と更新タイミング |
| --- | --- |
| `controller.diagnostics.last_decision.nextgen_diagnostics` | `puyo.nextgen.diagnostics.v1`．採用直前検証後または stale 拒否後に設定する．request/batch/features/selection/receipt を同梱する．scheduled 中，または検証済み selection がない error では `None`． |
| `last_decision.requested_action / executed_action` | 要求 root と採用 root を分離．stale 拒否で採用しなければ executed は `None`．`outcome` は nextgen receipt の activated/stale/timeout/fallback と一致する． |
| `controller.latest_policy_diagnostics["nextgen"]` | receipt 確定後は上記 diagnostics と同じ値．worker 単体の `policy.tactical_diagnostics` は receipt 前の結果なので，GUI は controller 側を優先する． |
| `latest_policy_diagnostics["template_phase"]` | `TemplatePhaseController.diagnostics()`．phase ID，exit reason，consumed/limit，pending resolution，reselect ready，request/piece count．receipt 確定時の scheduler phase． |
| `latest_policy_diagnostics["template_selection"]` | `TemplateSelection` の dataclass JSON．candidate に template/variant/transform/binding，score，fit status，reason，witness，prefix，cutoff を保持．probability/RNG position は template selector 由来． |
| `latest_policy_diagnostics["decision_trace"]` | `puyo.decision_trace.v1`．template/共有探索/戦術選択の順序と時間． |
| `nextgen_scheduler.ledger` | 同じ receipt を持つ typed `Diagnostics` の request 順リスト．`decision_record(...)` は caller が観測した reward，elapsed tick，終端情報と結合して PUYO-254 の `DecisionRecord` にする． |

既存 replay は controller record の `to_json()` と `policy_diagnostics` を保存するため，同じ diagnostics/receipt を読み戻せる．GUI は表示用に requested を executed で上書きしない．ledger の reward/終端を探索予測から補完しない．phase 履歴や GUI controls の追加は PUYO-253，評価 gate は PUYO-255 が担当する．

## 検証

`tests/test_nextgen_tactic_manager.py` は teacher/mask 分離，同一 batch の rule/RL 選択，matcher の単一実行と不一致拒否，同一 piece の再計数防止，公開未来不変性，spawn worker，timeout/stale/到達不能，実 hidden row 変更，実落下後の counter 再探索，live receipt の trajectory 保存・actor loader 読込を確認する．既存 phase/contract/response/shared-search/realtime/trajectory テストも実行する．native backend の再 build と GUI 操作は本変更では行わない．
