# PUYO-235 GUI QA 証跡

実行候補は `c1267a9d70c09c8257f4fe2546d3daee3b4424eb`、
reference/native/target10、seed187。
[実行手順と確認項目](../../development/puyo-235-normal-window-qa.md)を参照する。

| 種別 | 結果 | 正本 |
| --- | --- | --- |
| release build | PASS: clean commit / release / ABI / scenario-6 / wheel を検証 | `build_provenance.json`, `native_build.log` |
| 自動テスト | PASS: 92 tests | `automated_tests.log` |
| dummy replay | PASS: 10 decision、計画更新9回、retry 0 | `gui_qa.json`, `gui_dummy_{result,replay}.json.gz` |
| 通常画面 replay | 19 decision、計画更新18回、retry 0、action/plan整合 | `normal_replay_qa.json`, `gui_normal_{result,replay}.json.gz` |
| 人間の目視 | 一部確認済み: ghost/配置一致・再計画後の配置。文字・O往復・再開後応答は pending | `human_review.json` |
| GUI 全体 | pending（`passed=false`） | `gui_qa.json` |

`execution.json` は build/config/commit、取り込んだ PR、実行コマンド、表示条件を保持する。
`artifact_manifest.json` は記録時点の全証跡の checksum を保持する。
全 tick を JSON.gz へ圧縮して保存し、間引きは行っていない。
通常画面は X11/Weston、1120×780 のウィンドウとして起動した。
最終 overlay は OFF。これは最終表示状態の記録であり、同一 decision の O 往復が
成功したかどうかは人間の回答と自動 toggle テストで別々に確認する。

通常画面の replay 検証で、最終 overlay ON は必須条件にしない。
dummy 検証は操作を行わない起動時 ON の維持も確認するため、従来どおり必須とする。
他の7項目（schema、profile/backend、target、fallback、初手一致、採用履歴）は同じ条件で検証する。

```bash
.venv/bin/python -m eval.deep_chain_builder_benchmark verify-gui-qa \
  --output-dir docs/benchmarks/puyo-235-normal-window-qa
```

このコマンドの成功は dummy の checksum と証跡の再検証成功であり、
pending の人間 QA を合格へ変更しない。

通常画面の採用履歴は以下で再検証できる。

```bash
.venv/bin/python - <<'PY'
import gzip
import json
from pathlib import Path
from eval.deep_chain_builder_benchmark import _dummy_gui_summary
from train.artifacts import file_sha256

root = Path('docs/benchmarks/puyo-235-normal-window-qa')
manifest = json.loads((root / 'artifact_manifest.json').read_text())
for artifact in manifest['artifacts']:
    assert file_sha256(root / artifact['path']) == artifact['sha256'], artifact['path']
qa = _dummy_gui_summary(root / 'gui_normal_result.json.gz',
                        root / 'gui_normal_replay.json.gz',
                        expected_target_chain_count=10)
required = [check for check in qa['checks'] if check['name'] != 'plan_overlay_enabled']
assert all(check['passed'] for check in required), required
stored = json.loads((root / 'normal_replay_qa.json').read_text())
assert stored['checks'] == required
print('Artifact checksums and normal-window replay contract passed; human QA is separate.')
PY
```

2026-09-08、ユーザーから「N 手先のゴースト表示に対して配置できており、
再計画後のところにきちんと設置できているように見えた」と観察報告を受領した。
この自然文の回答を `human_review.json` に保持し、確認できた項目を manual へ反映した。
診断文字の可読性・O往復・再開後応答は追加回答待ち。

別件として報告された「窒息時に相手の連鎖を打ち切って終了する」は
[PUYO-239](https://shhchan.atlassian.net/browse/PUYO-239)へ起票し、
ユーザー指定でBacklogへ置いた。今回の保存replayの終端は0連鎖のanimationだったため、
連鎖中の症状は別の最小fixtureで再現・診断し、詳細を同Bugに記録している。

追加回答の受領後に `human_review.json` と `gui_qa.json` の manual を更新する。
未確認・failed を自動 PASS にせず、後続の PUYO-236 へ状態を引き継ぐ。
