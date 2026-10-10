# PUYO-276 QA session 保存契約

`eval.qa_session.save_qa_session(root, replay=..., result=..., config=..., source=..., native=..., tsumo=...)` は，既存の `puyo-realtime-match-v1` replay と `puyo.gui_qa.v1` result を 1 対局ずつ保存する．`source`，`native`，`tsumo` は，呼出側で確認した値だけを渡す．不明な identity は `null` で残す．`config` は起動時の確定設定を渡す．

保存先は `<root>/<32 桁の session ID>/` で，`replay.json`，`result.json`，`manifest.json` を含む．result の `artifacts` に 3 ファイルと session dir の絶対パスを記録するため，報告時は session dir または manifest path を共有できる．manifest は seed，policy seed，速度，tick 数，最終 hash，設定 digest，source/native/配ぷよ identity，各ファイルの SHA256 と byte 数，保存成否，中断状態を記録する．配ぷよ設定の正式名称は先行する PUYO-275 の確定契約に合わせる．

保存前に `replay_realtime_match` で入力と tick ごとの hash，最終 hash を検証する．同一 root 内の隠し `.pending` directory に各ファイルを一時名で書き，`fsync` 後に session dir 全体を公開する．公開時は root directory を lock し，既存 session の上書きを防ぐ．保存中に失敗した場合は `QASessionSaveError` が session ID と回収用 path を持ち，公開済みの不完全な session は作らない．未公開の `.pending` directory は手動回収用に保持する．

保存済みファイルは `eval.qa_session.validate_qa_session(session_dir)` で checksum，設定 digest，seed，tick，最終 hash と replay の決定性を再検証する．返り値が空リストなら検証成功である．通常終了と途中終了の両方を同じ validator で扱う．result の絶対パスは元の保存先を示す記録であり，session dir 全体を別の場所へコピーしても validator は同梱ファイルを検証する．
