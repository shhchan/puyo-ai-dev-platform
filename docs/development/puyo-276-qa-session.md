# PUYO-276 QA session 保存契約

`eval.qa_session.save_qa_session(root, replay=..., result=..., config=..., source=..., native=..., tsumo=...)` は，既存の `puyo-realtime-match-v1` replay と `puyo.gui_qa.v1` result を 1 対局ずつ保存する．`source`，`native`，`tsumo` は，呼出側で確認した値だけを渡す．不明な identity は `null` で残す．`config` は起動時の確定設定を渡す．

launcher の「対戦」では「QA replay 自動保存」が既定 ON であり，設定画面で OFF に変更できる．「QA 保存先」で root を選ぶ．起動時に予定 session dir が launcher と対局画面へ表示され，対局を閉じるとそこへ保存する．launcher では `Ctrl+C` で表示された path を clipboard へコピーできる．CLI からは `--qa-auto-save --qa-save-root runs/gui-qa-sessions` を指定する．旧 `--replay` はそのまま使える．

保存先は `<root>/<32 桁の session ID>/` で，`replay.json`，`result.json`，`manifest.json` を含む．result の `artifacts` に 3 ファイルと session dir の絶対パスを記録するため，報告時は session dir または manifest path を共有できる．manifest は seed，policy seed，速度，tick 数，最終 hash，設定 digest，source/native/配ぷよ identity，各ファイルの SHA256 と byte 数，保存成否，中断状態を記録する．配ぷよ設定の正式名称は先行する PUYO-275 の確定契約に合わせる．

保存前に `replay_realtime_match` で入力と tick ごとの hash，最終 hash を検証する．同一 root 内の隠し `.pending` directory に各ファイルを一時名で書き，`fsync` 後に session dir 全体を公開する．公開時は root directory を lock し，既存 session の上書きを防ぐ．保存中に失敗した場合は `QASessionSaveError` が session ID と回収用 path を持ち，公開済みの不完全な session は作らない．未公開の `.pending` directory は手動回収用に保持する．

保存済みファイルは `eval.qa_session.validate_qa_session(session_dir)` で checksum，設定 digest，seed，tick，最終 hash と replay の決定性を再検証する．返り値が空リストなら検証成功である．通常終了と途中終了の両方を同じ validator で扱う．result の絶対パスは元の保存先を示す記録であり，session dir 全体を別の場所へコピーしても validator は同梱ファイルを検証する．

```bash
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session runs/gui-qa-sessions/<session-id>
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session /copied/<session-id> --tsumo-source /path/to/verified/haipuyo.txt
```

`esports_tsu` の原本データは session dir に再配布しない．別の PC では同じ SHA-256 の原本を入手して `--tsumo-source` で指定する．validator は source identity と color mapping，入力，各 tick hash，最終 hash を照合する．`result.json` の `runtime` は対局中の frame cadence，終了後に返される `qa_save_elapsed_seconds` は保存処理時間として区別する．

## 人間 QA 手順

リポジトリ root で `/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python main.py` を実行し，「対戦」を選ぶ．設定で 1P を `nextgen_tactic_manager`，2P を `human`，速度を `1.0`，配ぷよ mode を `random` または検証対象の `esports_tsu` にする．「QA replay 自動保存」が ON であることと「QA 保存先」を確認し，開始する．起動前の launcher と対局画面には具体的な session dir が表示される．2P の既定キーは `A`／`D` または左右矢印で左右移動，`Q`／`E` または上／下矢印で回転，`W`／`S`／`Enter`／`Space` で soft drop．`P` は一時停止，`O` は両側の将来配置 plan overlay の表示／非表示を切り替える．`O` を 2 回押して overlay が消え，再表示されることを確かめる．現在組の落下位置 ghost と将来 plan を区別する．キー割当を変更済みなら `F1` の設定画面で実際の割当を確認する．

人間が 2P を操作して通常終了させ，launcher の「保存先」表示を `Ctrl+C` でコピーする．session dir に 3 ファイルがあり，次の validator が終了コード 0 と `QA session valid:` を返すことを確認する．対局前に OFF へ切り替えた場合は session dir が作られないことを確認する．

途中終了も同じ設定で開始して対局画面を `Esc` で閉じる．保存された `result.json` の `result.interrupted` が `true` であり，validator が成功することを確認する．端末から `Ctrl+C` または `SIGTERM` で中断した場合も session を回収する．保存失敗時は launcher／CLI に失敗 path が表示され，root 配下の `.<session-id>.pending/save_failure.json` を調べる．

CLI から再現する場合の実コマンド例は次のとおり．`--max-ticks 120` は短時間の正常終了，`--max-ticks 10000` と画面での `Esc` は途中終了の確認に使う．`esports_tsu` では mode・原本・pattern ID をすべて指定する．この host の Python は既存 native 検証済み venv を read-only で使う．

```bash
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 --seed 127 --max-ticks 120 --qa-auto-save --qa-save-root runs/gui-qa-sessions
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 --seed 127 --max-ticks 10000 --qa-auto-save --qa-save-root runs/gui-qa-sessions
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 --seed 127 --max-ticks 120 --tsumo-mode esports_tsu --tsumo-source /path/to/haipuyo.txt --tsumo-pattern-id 34066 --qa-auto-save --qa-save-root runs/gui-qa-sessions
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session runs/gui-qa-sessions/<session-id>
mkdir -p /tmp/qa-review
cp -a runs/gui-qa-sessions/<session-id> /tmp/qa-review/<session-id>
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session /tmp/qa-review/<session-id> --tsumo-source /path/to/verified/haipuyo.txt
```

保存済み `replay.json` の `ticks[*].inputs`，`snapshot_hash`，`outcome.final_snapshot_hash`，`match_rules.tsumo` と，`manifest.json` の source/native/seed/速度/設定 digest・各 SHA-256 を照合する．`esports_tsu` の移送先では原本が別 path でも checksum が一致すれば `--tsumo-source` で再検証できる．checksum が異なる原本，または同梱ファイルの改変では終了コード 1 を期待する．

## 短時間の保存負荷測定

dummy video driver，random 配ぷよ，nextgen policy と scripted human 入力，各 120 frame の機能測定では，OFF は 143 tick，対局 2.087 秒，frame p95/p99 は 22/65 ms，ON は 145 tick，対局 2.11 秒，frame p95/p99 は 21/84 ms だった．ON の保存は 0.137 秒，replay 2.4 MiB，result 584 KiB だった．tick 数と実行条件が揃った因果比較ではなく，既存 S14 SLA の達成判定にも使わない．長時間の対局での cadence・保存量は別途実機で確認する．`qa_save_elapsed_seconds` は保存後に返す値であり，保存された `result.json` の対局中 `runtime` には含めない．
