# PUYO-253 対戦 GUI の戦術表示と開始設定

対戦 launcher の「観戦」または「対戦」で `nextgen_tactic_manager` を選ぶと，確定した scheduler receipt に基づく戦術，土台／variant，残 decision，理由，要求 action と実行 action を表示する．計算中の worker 提案は戦術表示に使用しない．template の fit score，選択確率，RNG 位置は対戦画面にも履歴 overlay にも表示しない．

`H` で履歴 overlay を開閉し，`J`／`K` でそれぞれ 8 件古い／新しい履歴へスクロール，`L` で最新へ戻る．従来の `PageUp`／`PageDown`／`End` も使用できる．履歴は decision receipt と公開 resolution／lock／garbage event を tick 順に表示し，replay JSON の `tactic_history` に保存する．model viewer では `J`／`K` で前／次の履歴 event，`U`／`I` で前／次の lineage node に移動する．従来の `Shift+H`／`H` と `PageUp`／`PageDown` も使用できる．

開始設定は catalog path，有効 template ID 群，argmax／softmax，温度，専用 template seed，共通 N，固定探索 profile，selector，既存の latency mode，replay／decision 記録先を含む．catalog pattern は YAML を直接編集する．起動時には元 catalog を厳格に検証し，設定を適用した catalog も検証する．有効 template が 0 件，不正な温度／N／ID，未登録 profile，rule selector への checkpoint 指定は開始前に拒否する．RL selector は PUYO-256 の実装まで開始できない．

実行後は `*.nextgen_config.json` に解決済み catalog を保存する．`--nextgen-trajectory` は `puyo.nextgen.gui_ledger.v1` の確定 receipt と GUI event を保存する．この出力には実測 reward と学習 run の provenance がないため，`puyo.nextgen.trajectory.v1` の学習入力ではない．`nextgen_smoke` と `nextgen_diagnostic` は接続／表示確認用の固定 profile であり，品質校正済み profile ではない．

## 通常ウィンドウ QA（未完了）

1. 親セッションが表示環境を排他確保して launcher を開き，rule／template／温度／N／profile／timing／出力先を変更して preset 保存と再読込を確認する．不正値と RL selector は起動拒否を確認する．
2. softmax と複数 template を設定して対戦を進め，戦術切替，土台再選択，自由構築，理由と要求／実行 action が `nextgen_ledger` と一致することを確認する．画面と overlay に fit score と確率がないことを確認する．
3. `H` で履歴を開き，`J`／`K`／`L` でスクロールと最新への復帰を確認する．replay を model viewer で開き，`J`／`K` で同じ event／receipt tick，`U`／`I` で lineage node に移動する．`tactic_history` の欠落／重複と event 表示を確認する．

実施者，日時，実行 command，表示環境，replay／ledger path，観測した切替と結果を Jira PUYO-253 に記録する．SDL dummy の smoke はこの QA の代替にならない．

依頼者の GUI QA では GTR 構築の品質不良と Fn 依存キーの操作不能が報告された．本変更の Fn 不要キーは依頼者による再確認が未実施であり，戦術切替・履歴追従・lineage 表示の A/C は未達として扱う．品質原因は親チケット側で調査する．

## 自動 GUI smoke（2026-09-24 16:00〜16:03 JST）

`DISPLAY=:0`，`SDL_VIDEODRIVER=x11` で通常ウィンドウを起動し，H→PageUp→PageDown→H を自動送信した．`/tmp/puyo253-window-310.png` に履歴 overlay，`/tmp/puyo253-window-370.png` に sidebar を保存した．120 tick で activated receipt 1 件，履歴 7 件を `/tmp/puyo253-window-ledger.json` と `/tmp/puyo253-window-replay.json` に保存した．tick 46 の request-1 は `build_template`，要求／実行 action はともに 4 で，ledger と replay の履歴が一致した．同じ表示環境で model viewer に H を送信すると，`/tmp/puyo253-viewer-report.json` の選択 tick は 46，履歴は 7 件になった．これは自動 smoke であり，切替・自由構築・土台再選択を人間が通常ウィンドウで確認した記録ではない．

## Fn 不要キーの自動 GUI smoke（2026-09-24）

同じ通常ウィンドウ環境で `H`→`J`→`K`→`L`→`H` を送信した．`/tmp/puyo253-fn-keys-330.png` は `J` 後の古い履歴，`/tmp/puyo253-fn-keys-370.png` は `K`／`L` 後の最新履歴を示す．120 tick で receipt 1 件，履歴 7 件を `/tmp/puyo253-fn-keys-ledger.json` と `/tmp/puyo253-fn-keys-replay.json` に保存した．model viewer には `K`→`K`→`J`，`I`→`U`→`I` を送信し，`/tmp/puyo253-fn-viewer-report.json` で選択 tick 50，履歴 7 件，2 node の操作確認用 lineage fixture の選択 node `model:two` を確認した．この操作は自動 smoke であり，キー修正後の依頼者による人間再確認ではない．
