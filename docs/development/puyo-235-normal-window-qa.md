# PUYO-235 通常画面の plan / ghost / 操作 QA

通常画面の人間 QA と、その run の自動 replay 検証を独立して記録する。
正本は [証跡ディレクトリ](../benchmarks/puyo-235-normal-window-qa/README.md)。
自動テスト・dummy 実行・通常画面の起動成功だけで、人間の確認項目を PASS にしない。

2026-09-09 にユーザーから残る可読性・O往復・再開後応答の確認結果を受領し、
通常画面の全確認項目と GUI 全体を PASS として記録した。

## 対象候補

- 統合ブランチ: `integration/puyo-113-v1-7-2`
- 実行 commit: `c1267a9d70c09c8257f4fe2546d3daee3b4424eb`
- 取り込み済み: PUYO-230 / PR #123、PUYO-232 / #127、PUYO-237 / #125、PUYO-238 / #126
- reference / native / target 10、depth 16 / width 250 / scenarios 6 / 600,000 nodes
- seed 187、相手 random、通常画面 speed 1、開始時 pause、overlay ON
- native: CPython 3.12 / release / scenario-6、fallback 許可なし
- wheel SHA-256: `9887b2e34b3ad8ae817df585003985a3686e3d13de61c77ad5ec9c21e43be146`

実行は `/tmp/puyo-235-build` の clean worktree から行った。
元の作業ディレクトリにある2件の未追跡ユーザー資料は build に含めていない。
今回変更するのは証跡検証器と記録だけで、候補の policy / search / GUI は変更しない。
`execution.json` に検証器 commit、実行コマンド、runtime/config の checksum を記録する。

## 通常画面の再現

リポジトリ直下で実行する。既に `/tmp/puyo-235-build` が存在する場合は、その commit と
native build が上記候補に一致することを確認して利用できる。新しい観察は出力名を変更し、
既存 run を上書きしない。

```bash
puyo235_python="$(pwd)/.venv/bin/python"
git worktree add --detach /tmp/puyo-235-build c1267a9d70c09c8257f4fe2546d3daee3b4424eb
PUYO_NATIVE_PYTHON="$puyo235_python" /tmp/puyo-235-build/scripts/build_deep_chain_native.sh
(
  cd /tmp/puyo-235-build
  env -u SDL_VIDEODRIVER -u SDL_AUDIODRIVER "$puyo235_python" -m eval.realtime_versus_ui \
    --policy-a deep_chain_builder --policy-b random \
    --deep-chain-profile reference --deep-chain-backend native \
    --deep-chain-target-chain 10 --seed 187 --speed 1 --start-paused \
    --result-json /tmp/puyo-235-gui-normal-result.json \
    --replay /tmp/puyo-235-gui-normal-replay.json
)
```

1. `P` で開始し、左側 AI の配置と plan 更新を3 decision 以上観察する。
2. 先頭 ghost と選択・実行 action が一致し、次 decision で古い ghost が置換されるか確認する。
3. 選択理由、backend、target、node counter、flow timing の文字が読めるか確認する。
4. `P` で停止した同一 decision の状態で `O` を OFF / ON と往復し、同じ ghost / plan が戻るか確認する。
5. `P` で再開し、描画と操作応答が続くか確認する。`Esc` で終了すると result / replay が保存される。
6. 実施者・観察日時・画面条件・各項目の passed / failed / pending と根拠を報告する。
   画面で観察した run と保存 replay の対応も記録する。

`R` は試合をリセットするので、この run の途中では使用しない。
`N` は realtime では1 tickの進行であり、1 decisionの進行ではない。
通常画面の終端で overlay が OFF であることだけでは、途中の往復操作の成否は判定できない。

## 自動検証と target 契約

PUYO-230 の GUI 検証は既定 target 6 を維持する。新しい
`record-gui-qa --expected-target-chain 10 --ticket PUYO-235` は target を明示して固定し、
result のモデル設定と採用された全 decision の target を照合する。途中だけ target 6 の
履歴も拒否する。新契約を PUYO-230 の既定ディレクトリへ保存することは禁止する。

`gui_qa.v3` の追加フィールド `expected_target_chain_count` を保存し、
`verify-gui-qa` は同じ条件で checksum と判定を再検証する。
このフィールドがない旧証跡は target 6 として扱う。

```bash
.venv/bin/python -m unittest \
  tests.test_deep_chain_gui_qa tests.test_deep_chain_builder_benchmark \
  tests.test_realtime_ai tests.test_realtime_versus_ui tests.test_launcher

.venv/bin/python -m eval.deep_chain_builder_benchmark record-gui-qa \
  --output-dir docs/benchmarks/puyo-235-normal-window-qa \
  --ticket PUYO-235 --expected-target-chain 10 \
  --automated-passed \
  --automated-command ".venv/bin/python -m unittest tests.test_deep_chain_gui_qa tests.test_deep_chain_builder_benchmark tests.test_realtime_ai tests.test_realtime_versus_ui tests.test_launcher" \
  --dummy-result docs/benchmarks/puyo-235-normal-window-qa/gui_dummy_result.json.gz \
  --dummy-replay docs/benchmarks/puyo-235-normal-window-qa/gui_dummy_replay.json.gz \
  --manual-status passed --reviewer "ユーザー（本会話の報告者）" \
  --notes "2026-09-09に全確認項目の観察結果を受領。human_review.json を参照"

.venv/bin/python -m eval.deep_chain_builder_benchmark verify-gui-qa \
  --output-dir docs/benchmarks/puyo-235-normal-window-qa
```

通常画面の replay 自動検証は `normal_replay_qa.json` に分ける。
これは人間による可読性・ghost・操作感の判定を代替しない。
上記 record コマンドは今回の受領済み観察結果に対応する記録例で、結果を置換する。
別の run の人間 QA が未実施の場合は必ず pending とし、今回の PASS を転用しない。

## 人間の結果と後続評価

`human_review.json` に実施者、日時、各確認項目の状態と根拠、元の回答、
normal result/replay の checksum を記録する。回答前は全項目 pending のままにする。
failed 項目は原因・再現手順を独立した Jira Task にし、PUYO-236 への Blocks を追加する。
通常画面の全項目と対応する証跡が揃ってから PUYO-235 を Complete にする。

PUYO-236 ではこの候補の commit / build / config を最終 benchmark 候補と照合する。
PUYO-233 から採用する改良などで search / plan / GUI が変わる場合は、その影響を確認し、
影響する通常画面の項目を再実施する。この GUI QA だけで品質・性能 baseline は採用しない。

## References

- [PUYO-235](https://shhchan.atlassian.net/browse/PUYO-235)
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [PUYO-230 policy 再計画の契約](puyo-230-policy-replan-qa.md)
- [PUYO-232 target 10 の契約](puyo-232-safe-build-target-contract.md)
