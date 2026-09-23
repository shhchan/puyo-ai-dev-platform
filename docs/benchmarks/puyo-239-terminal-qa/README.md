# PUYO-239 終端連鎖の通常画面 QA

2026-09-23 に，固定した窒息境界を通常 X11 window で再生した．`eval/realtime_terminal_qa.py` が実際の controller / env / renderer を使用し，表示 surface を PNG として保存している．dummy driver では capture を拒否する．自動操作による実行と Codex 子・親の画像確認の証跡であり，ユーザーによる操作・目視確認としては扱わない．

## 修正前後

| 対象 | ソース | 終了 tick 数 | 左の score / chain / sent | 右の pending / received |
| --- | --- | --- | --- | --- |
| before | `f721c9af3fd7e70a4bca8b83c3b25ef6bd18a90c` | 1 | 0 / 0 / 0 | 0 / 0 |
| after | `e4190ddc6a21434bf6166d831e3824dd323425e2` | 75 | 360 / 2 / 6 | 6 / 0 |

両方とも seed 239 で生成した初期状態に，helper の `prepare_boundary` を適用する．左は赤 4 個→青 4 個の 2 連鎖，得点端数 69，右は窒息済みの交互色の列である．seed だけで自然発生する試合や PUYO-235 のユーザー報告そのものの再収録ではない．

- [before/final.png](before/final.png): tick 1 で左の消去が停止し，score 0・右の予告 0 のまま勝敗を表示する．
- [after/first-tick.png](after/first-tick.png): 窒息は authoritative state に記録済みだが，勝敗・GAME OVER は表示せず連鎖を続ける．
- [after/chain-continues.png](after/chain-continues.png): tick 31，1 連鎖・score 40 で，青の落下が続く．
- [after/authoritative-final.png](after/authoritative-final.png): tick 75，2 CHAIN +360・sent 6・右の pending 6 を表示する．右の盤面は変化せず，次の操作組も生成しない．
- [after/final.png](after/final.png): 残っていた結果演出を消化した後，同じ tick 75 と得点・予告を保持したまま勝敗・GAME OVER を表示する．

子 Codex は上記の全段階を画像ツールで確認し，親 Codex も before 最終・after 途中・after 最終を独立に確認した．commit 後の再採取でも同じ結果を確認した．各 `provenance.json` に SDL driver，ソース revision，読み込んだ 8 モジュールと helper の SHA256 を保存した．8 モジュールすべてが指定 commit の `git show` と一致することを確認した．

## 終端・互換契約

- `ending` はいずれかの窒息，`resolution_pending` は生存側の既存の連鎖，`finished` はその確定後を表す．状態は既存の game state から導出し，replay 用の隠れた latch は増やさない．落下後に初回消去が成立する配置も待つ．消去を生じない配置・落下演出だけでは終了を遅らせない．
- 両者の同 tick の解決・相殺・着弾を確定してから，対戦継続時に限り新しい組を生成する．窒息検知後の連鎖解決では入力・探索・新規 spawn・追加のおじゃま落下を行わない．未消費のおじゃまは予告に残る．
- 同時窒息は既存どおり得点で勝敗を決め，同点は draw とする．明示的な tick limit は truncation，途中の手動終了は interrupted のままとし，連鎖完了とは記録しない．
- GUI の補助演出は env 終了後も消化でき，pause 中の N と通常の update の双方で進む．GAME OVER・勝敗 banner・自動終了のカウントは演出消化後に始まる．演出消化だけでは simulation tick / replay / reward を増やさない．
- 学習用の score reward は各 tick の実際の score delta を使用する．従来は 2 連鎖 360 点に対して，中間の 40 点と最後の連鎖合計 360 点を計上し，reward 用の合計が 400 点になっていた．修正後は 360 点だけを計上する．attack は従来どおり連鎖終了時の累積得点・端数・全消し bonus を使用する．terminal reward は env 終了時の一度だけである．
- replay schema と通常 tick の hash 形式は変更していない．`historical-replay.json` のとおり，既存 PUYO-235 normal replay は全 2175 tick の hash が一致した．バグに該当する「窒息で進行中連鎖を打ち切った」古い replay では，修正前後の終端時刻・hash が変わることがある．before 証跡は before ソースで検証する．

## 再実行

依存を導入した Python を使い，リポジトリのルートで実行する．表示先は通常 window を使える環境に合わせる．

```bash
DISPLAY=:0 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy \
  python -m eval.realtime_terminal_qa --output /tmp/puyo-239-after
python -m eval.realtime_terminal_qa \
  --replay docs/benchmarks/puyo-239-terminal-qa/after/boundary-replay.json
```

左右を入れ替える場合は `--survivor player_1`，1 連鎖は `--chains 1` を付ける．helper は通常 controller の 1 tick step を 60 FPS で実行し，終端後も描画して自動終了する．`run.json` に tick ごとの score・chain・予告・着弾・active pair・最終 diagnostics，`boundary-replay.json` に初期 fixture の指定と入力・攻撃・hash を保存する．fixture replay は初期盤面を復元してから既存の `replay_realtime_match` で全 tick の hash と攻撃 diagnostics を検証する．

before は対象 commit の `git archive` を別の一時ディレクトリへ展開し，そのルートだけを `PYTHONPATH` に指定して，この commit の helper を絶対パスで実行する．helper は比較対象ソースを改変しない．例:

```bash
mkdir -p /tmp/puyo-239-before-source
git archive f721c9af3fd7e70a4bca8b83c3b25ef6bd18a90c | tar -x -C /tmp/puyo-239-before-source
DISPLAY=:0 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy \
  PYTHONPATH=/tmp/puyo-239-before-source \
  python "$PWD/eval/realtime_terminal_qa.py" \
  --source-revision f721c9af3fd7e70a4bca8b83c3b25ef6bd18a90c \
  --output /tmp/puyo-239-before
```

## 自動検証

[test-results.txt](test-results.txt): 関連 12 ファイルの **137 tests +55 subtests 成功**．その後，実着弾による窒息中の連鎖・初回消去前の落下の境界テストを追加し，境界 suite 全体を再実行して **19 tests +18 subtests 成功**．両実行は重複を含む．pygame の既存 `pkg_resources` deprecation warning が 1 件ある．

```bash
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python -m pytest -q \
  tests/test_realtime_terminal.py tests/test_realtime_versus.py \
  tests/test_realtime_ai.py tests/test_realtime_replay.py \
  tests/test_realtime_versus_ui.py tests/test_realtime_arena.py \
  tests/test_realtime_headless.py tests/test_realtime_gui_qa.py \
  tests/test_ojama_scoring.py tests/test_scoring.py \
  tests/test_headless_simulator.py tests/test_versus_ui.py
```

左右，1/2 連鎖，連鎖なし，落下だけ，既存予告の全量/部分相殺，全消し bonus と soft-drop 得点，実着弾による窒息，同時窒息と同点，clone 再開，env/arena/replay の一致，reward の一度だけの加算，探索抑止，pause/N/speed，途中終了を検証する．helper と新テストの `ruff check`，`git diff --check` も成功した．
