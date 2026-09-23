# PUYO-230 policy 再計画と controller retry の GUI QA 契約

通常の placement 後に policy が新しい plan を返したことを、replay の採用済み
decision 履歴から判定する。`controller.replans` は入力実行を中断した際の回復回数であり、
正常実行で 0 回でもよい。PUYO-204 の `replans >= 1` 判定は新契約では使用しない。

## 観測・decision・plan の対応

| 証跡 | 内容 |
| --- | --- |
| `policy.decision_input` | `puyo.deep_chain_builder.decision_input.v1`。公開入力の SHA-256 digest と root state fingerprint |
| `controller.last_decision.decision_input` | policy 呼び出し前に controller が独立して取得した同じ入力の identity |
| `policy.decision_trace.decision_id` / `controller.last_decision.policy_decision_id` | policy の問い合わせと controller が採用した結果の対応 |
| `controller.last_decision.request_placement_count` | 問い合わせ開始時の controller placement 完了数 |
| `request_tick` / `activation_tick` | 問い合わせと採用の時点。replay tick は入力処理を実行した tick で、採用時は `activation_tick` と等しい |
| `plan_id` / `replan_reason` / `selected_action` / `plan.steps[0]` | plan 更新、理由、選択した action と ghost の先頭手 |

観測 digest は既存 `VisibleRuntimeInput` の全フィールドを正規化して計算する。
第14行を含む公開盤面、可視ツモ、合法手、公開スカラー・score・tick 等を含み、
simulator オブジェクトと private future は取り込まない。root state fingerprint は
既存 compact state の fingerprint と同じ契約を用い、plan と step 1 の root に照合する。
不完全な入力を受ける injected flow / Python fallback の診断では root が `null` に
なる場合がある。この場合も診断は読めるが、GUI QA は PASS にしない。

replay 本体の `puyo-realtime-match-v1` は維持し、追加の
`policy_decision_schema_version=puyo.realtime_policy_decisions.v1` で新しい証跡を識別する。
既存の sparse な policy diagnostics を引き継ぎながら、`decisions_activated` が増えた
tick だけを採用履歴として数える。計算開始・待機・同じ診断の再掲は採用回数に含めない。

## 判定

- reference / native / target 6、policy と controller の fallback が false。
- 3 decision 以上を採用し、配置が進んだ異なる root の観測で 2 回以上 plan を更新する。
- 各採用時点で decision ID、観測 identity、plan root、step 1、controller action が一致する。
- 配置後に以前の plan ID を使い回す、観測対応がない、途中の action が違う場合は FAIL。
- 同じ観測への重複問い合わせは `plan_unchanged` として区別し、plan ID / action を維持する。
  既存の再問い合わせ動作は維持し、配置後の plan 更新回数には加算しない。
- result の最終診断と replay の最終診断・counter が対応する。

controller retry / stale decision は独立した診断値として出力する。retry を発生させる
ために正常系の入力を壊す必要はない。既存の controller 異常回復テストは継続する。

`O` の OFF / ON は実際の controller に対する自動テストで確認する。3 decision にわたり
ghost が新しい plan に置換され、各 toggle の前後で policy の全診断・action・plan と
controller counter が変化しないことを検証する。色・可読性・体感上の応答性は
通常画面での人間 QA（PUYO-235）で確認する。

## 自動検証

```bash
.venv/bin/python -m unittest \
  tests.test_deep_chain_gui_qa \
  tests.test_deep_chain_builder_benchmark \
  tests.test_deep_chain_builder \
  tests.test_deep_chain_builder_smoke \
  tests.test_deep_chain_search_backend \
  tests.test_deep_chain_native_search \
  tests.test_realtime_ai \
  tests.test_realtime_versus_ui \
  tests.test_realtime_arena \
  tests.test_launcher
```

## dummy 実行と記録

native extension を導入した環境で以下を実行する。今回の native source は PUYO-229 の
release build と同一であり、Rust の再ビルドは不要だった。新規環境では
`./scripts/build_deep_chain_native.sh` で導入できる。

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  .venv/bin/python -m eval.realtime_versus_ui \
  --policy-a deep_chain_builder --policy-b random \
  --deep-chain-profile reference --deep-chain-backend native \
  --deep-chain-target-chain 6 --seed 187 --speed 4 --max-frames 240 \
  --result-json /tmp/puyo-230-gui-dummy-result.json \
  --replay /tmp/puyo-230-gui-dummy-replay.json
```

全 tick を保持して gzip 圧縮する。validator は JSON / JSON.gz の両方を読み取れる。

```bash
.venv/bin/python - <<'PY'
import gzip
from pathlib import Path

target = Path("docs/benchmarks/puyo-230-policy-replan-qa")
target.mkdir(parents=True, exist_ok=True)
for kind in ("result", "replay"):
    raw = Path(f"/tmp/puyo-230-gui-dummy-{kind}.json").read_bytes()
    (target / f"gui_dummy_{kind}.json.gz").write_bytes(gzip.compress(raw, mtime=0))
PY

.venv/bin/python -m eval.deep_chain_builder_benchmark record-gui-qa \
  --automated-passed --automated-command "<実行した unittest コマンド>" \
  --dummy-result docs/benchmarks/puyo-230-policy-replan-qa/gui_dummy_result.json.gz \
  --dummy-replay docs/benchmarks/puyo-230-policy-replan-qa/gui_dummy_replay.json.gz \
  --manual-status pending --notes "通常画面の人間 QA は PUYO-235 で実施"

.venv/bin/python -m eval.deep_chain_builder_benchmark verify-gui-qa
```

`record-gui-qa` / `verify-gui-qa` の既定保存先は
`docs/benchmarks/puyo-230-policy-replan-qa/`。出力は
`puyo.deep_chain_builder.gui_qa.v3` で、automated / dummy_replay / manual を別々に保持する。
目視未実施は `manual.status=pending`、全体の `passed=false` になる。
`verify-gui-qa` の成功は証跡の再検証成功を意味し、人間 QA の完了を意味しない。
このコマンドは保存済みの入力 checksum と判定を照合し、ファイルを書き換えない。

旧 PUYO-204 の replay / result / QA は履歴のまま保持する。新しい validator は
旧 replay の観測対応情報が不足していることを検出し、結果を後付けで PASS にしない。
PUYO-236 の 60-run 再評価と experimental baseline の再判定は別タスクで実施する。

## 今回の結果

実装 revision `d19dc6b` の reference/native dummy 実行は PASS。
採用 decision 10 件、配置後の plan 更新 9 回、controller retry 0 回、stale decision 0 回で、
各 decision の観測・plan・採用 action は一致した。関連テストは 129 件 PASS、
保存済み QA の再検証と旧保存先の保護を確認する追加 2 件も PASS。

正本は `docs/benchmarks/puyo-230-policy-replan-qa/gui_qa.json`。
`execution.json` に実行コマンド、implementation revision、変更ソースの SHA-256 と
使用した native build 情報を保存する。raw result / replay はそれぞれ JSON.gz として
全内容を保存し、`gui_qa.json` に圧縮ファイルの checksum を記録する。

自動・dummy QA は PASS、通常画面の人間 QA は pending で、全体の GUI QA は未完了。
通常画面を確認する人は同じ dummy コマンドから SDL の2つの環境変数を外して起動し、
3 decision 以上の ghost 置換、先頭手と選択 action、診断の可読性、`O` の往復、
描画・操作の応答性を確認する。PUYO-235 で確認結果と reviewer を記録する。

## References

- [PUYO-230](https://shhchan.atlassian.net/browse/PUYO-230)
- [PUYO-204](https://shhchan.atlassian.net/browse/PUYO-204)
- [PUYO-235](https://shhchan.atlassian.net/browse/PUYO-235)
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [旧 baseline 評価](puyo-204-deep-chain-native-baseline.md)
