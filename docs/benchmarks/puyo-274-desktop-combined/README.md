# PUYO-274 desktop 組合せ QA

preview #172 → survival 証跡 #173 → cadence #174 を履歴を保持する local merge で組み合わせた source `d6ca1b94baa9cd226f7ff79b7b6600e7c11db511`．native/build は統合起点と同一．最上段 #175 は本記録のみ．raw の source hash／設定／native SHA を保存．GitHub の PR merge／release はしていない．

## 検証

- 回帰 118 tests，46 subtests PASS（57.22 s）．`test_fixed_gtr_fixture_completes_then_rule_switches_to_build_main` 1 件は起点でも FAIL する既存の legacy starvation 期待として deselect．#173 の baseline/final log に残し，全テスト PASS としない．
- Ruff：変更対象成功．UI/probe の既存 E402 は除外（起点 UI でも同じ 18 E402 を確認）．diff check PASS．WSLg の `main.py --max-frames 3` PASS．
- 実 WSLg 1120×780，通常速度，seed 55，native safe-build，overlay ON，4 条件を独立 process／直列に測定．light/one/human 各 600 frame，two 360 frame．#174 の同 source A/B（overlay OFF）の代用ではなく，表示を含む組合せ QA．条件と分位点を変えて都合のよい PASS を選ばない．

| 条件 | frame p95/p99 ms | input schedule p95/p99 ms | event→state p95/p99 ms | event→draw p95/p99 ms |
| --- | --- | --- | --- | --- |
| light | 18.94/19.90 | 15.44/16.23 | 17.77/20.73 | 19.72/22.89 |
| one | 21.10/42.23 | 16.08/24.80 | 18.57/30.04 | 22.15/40.90 |
| two | 20.78/49.52 | 24.56/68.75 | 33.64/71.41 | 37.07/79.82 |
| human | 20.57/39.32 | 16.67/25.60 | 23.40/36.83 | 29.19/39.58 |

input schedule は予定投入→event handler，event→state/draw は実投入からの遅延．この違いを入力全体の PASS に読み替えない．固定 p95≤25 ms／p99≤50 ms に対し，two の schedule/state/draw と human の draw p95 が未達．cadence 単独 overlay OFF の two frame p99 51.39 ms の未達も保持．実人間が意図した位置に置けるかは未確認．

採用済み 3 手 preview は one 7 回／two 8 回／human 6 回．receipt mismatch 0．piece_finished，decision_changed，not_adopted_stale の表示抑止も記録．nextgen 実 lock は one 6/6，two 8/8，human 条件の AI 6/6 一致．全 worker cleanup 成功，scheduler errors なし．

`overlay-toggle-smoke.py` は別の 130 frame smoke で，同一 tick／action／receipt のまま o OFF→ON の plan 非表示／復元を assert．ON/OFF PNG と JSON を保存．画面保存の overhead があるため性能個票に混ぜない．初回補助 script の main guard 欠落は修正後に成功し，失敗した smoke を性能結果へ混ぜていない．人間 QA の代わりにはしない．

## 再実行

```bash
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe --overlay --output /tmp/combined-one.json
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe --overlay --opponent nextgen_tactic_manager --frames 360 --output /tmp/combined-two.json
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe --overlay --opponent human --output /tmp/combined-human.json
DISPLAY=:0 SDL_AUDIODRIVER=dummy .venv/bin/python -m eval.puyo_269_gui_probe --overlay --policy first --output /tmp/combined-light.json
DISPLAY=:0 SDL_AUDIODRIVER=dummy PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-274-desktop-combined/overlay-toggle-smoke.py --overlay --frames 130 --output /tmp/overlay-smoke.json
```

raw は gzip JSON．summary.json には通常/active 分位点，source/native SHA，設定，receipt/lock/cleanup を保存．SHA256SUMS は raw・summary・テスト・画像・補助 script の integrity 用．

## 残課題

PUYO-266：hidden 自配置の公開履歴契約，窒息の有効修正，正常/失敗 seed と 3 定型の固定条件回帰，正式 G2 と品質 A/C．棄却案を復活させない．PUYO-269/273：two 遅延の残る同期処理と人間操作 QA．PUYO-274：preview 人間確認と各所有先の完了追跡．全件 In Progress，品質 FAIL／G2 BLOCKED，本学習 256〜258 未開始．
