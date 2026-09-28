# PUYO-266 desktop 生存調査と棄却回帰

**窒息修正は未採用．observed quality FAIL / G2 BLOCKED，Jira In Progress を維持する．**
起点 `732ed3d` の新規 desktop 診断，公開モデルと実盤面の反例，通常構築を壊した修正案の棄却証跡を保存した．policy／selector／scheduler／native の最終差分はない．本学習，正式 G2 の再実行，人間 GUI QA，merge／release は実施していない．

## 新規 daa/random 診断

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
.venv/bin/python -m eval.nextgen_realtime_diagnostic \
  --mode step --seed 59 --seed-a 55 --seed-b 59 --templates daa \
  --placements 120 --max-ticks 16000 --profile nextgen_safe_build \
  --output /tmp/puyo266-desktop-daa-new
```

GUI と同じ `RealtimeVersusMatchController`／process worker を使用し，表示は開かず，各 tick の間で worker 完了を待つ `step`／`measured` 条件である．通常速度や configured 0 tick の対戦と同一視しない．元の人間 run の replay／速度／source／config／native SHA は未提供のため，同一 run の再現ではない．診断器の template seed は共通 seed の 59，policy/search seed は 55 である．`argmax` は template RNG を使用しない．全設定は保存 report に含む．

- 28 配置／1567 tick，実時間 28.225 s，採用 28，fallback 0，source の測定中変更なし．最後に player 0 が窒息した．
- 23／24 判断目には公開モデル上の即時小消しがあるが，全 root に有限 horizon の witness があり，その時点で発火が必要だった証明は得ていない．25／26 は `survival_safe_nonfire`，27／28 は probe の 128 nodes cutoff で witness がない．
- **終盤 25〜28 の公開・到達可能 root に即時消去はない．** この新規 run を「有効な小消しを捨てた最終手」の再現とは扱わない．公開おじゃま着弾と cutoff を含む未解決の診断として保持する．
- 全 tick の hash／攻撃／全消し diagnostics と最終 hash を保存入力から再検証した．28 実 lock の root 不一致は 0．候補／順位／selection／receipt／到達可能 mask は report／ledger，実 lock／resolve は [audit](daa-step-audit.json) にある．

`daa-step-before/` は report／ledger の lossless gzip と，全入力・hash・攻撃・全消し・公開 event を保持した replay を保存する．697 MiB の元 replay に反復する controller/UI diagnostics は，report／ledger と重複するため投影から除いた．除外 key，元ファイルの SHA-256 とサイズは `originals.json` に記録した．元 `/tmp/puyo266-desktop-daa-before/` は削除していない．この投影 replay 自体の再生一致を確認した．

## 固定 seed と棄却案

同じ desktop／native／profile／quota で，各 identity を fresh process で直列測定した．最大 40 配置，configured 0 tick，停止相手／攻撃抑止の既存 `SafeNoThreatMatch` 条件であり，上記 random 対戦とは別である．native depth 16／width 250／6 scenarios／shared quota 600000，template 128／response 256，target 10 を保持した．host，native binary SHA，全 source file SHA，thread 環境，設定は [declaration](declaration.json) と各 raw に含む．

`d1060ba` の案は，消去・落下後に未知 hidden 行へセルが残る継続を `unknown` にして，既存の fatal + witness 危機条件で再選択させた．単体対例 15 件は通ったが，正常な GTR seed 123 の大連鎖を損ねたため，`18c1051` で通常 revert した．共有済み履歴を書き換えず，最終 runtime は起点と同じである．棄却案を新しい生存契約として採用していない．

| 定型／seed | baseline 最大実連鎖／小発火／窒息 | 棄却案 最大実連鎖／小発火／窒息 |
| --- | --- | --- |
| GTR 55 | 10／0／なし | 10／0／なし |
| GTR 123 | 10／0／なし | **3／2／なし** |
| GTR 124 | 11／0／なし | 13／0／なし |
| GTR 126 | 0／0／あり | 10／0／なし |
| GTR 128 | 0／0／あり | 1／3／なし |
| GTR 132 | 0／0／あり | 11／0／なし |
| GTR 135 | 1／2／あり | 1／2／あり |
| GTR 144 | 1／1／あり | 未実施 |
| daa 55 | 10／0／なし | 未実施 |
| persian 55 | 10／0／なし | 未実施 |

小発火は既存集計の `0 < chain < 10` 件数であり，必要な生存発火まで含む．改善行だけを採用理由にしていない．baseline 10 run，棄却案 7 run の完了 raw を保持する．123 の回帰により親が測定停止を指示し，所有 PGID 22276 だけを SIGTERM，残存 process なしを確認した．棄却案の 144／daa／persian と daa/random の変更後 run は未実施である．

全 17 run は保存した入力を再生し，最終 hash が一致，実 root と採用 root の不一致 0．7 paired identity の初期公開盤面／設定／native／host は一致し，共通 lock 順の公開ツモ窓も一致した．分岐後の盤面まで同一入力と呼ばない．[comparison](comparison.json) に初回分岐の同一公開入力／mask 照合，実発火 receipt／resolve，decision latency を保持する．GTR 123 の初回分岐は 28 判断目の root 1 → 15，最大実連鎖は 10 → 3 となった．これが通常構築を保つ受け入れ条件への反例である．

## 公開推定と実盤面の切り分け

既存 planner の `_geometric_paths` を読取利用し，公開投影と offline 完全盤面に，同じ既知組・fresh spawn・ゼロの操作 counter を与えた．live clock／自然落下／実時間到達保証ではない．各照合は最大 2000 control states の診断であり，runtime の probe 予算へ追加していない．結果は [witness-comparison](witness-comparison.json) にある．到達不能な継続より後の行は，実現した盤面ではなく反実仮想として prefix の不成立を明示する．

| seed／判断 | 公開モデルが欠く hidden セル | 次の代表 action 0 |
| --- | ---: | --- |
| 126／34 | 1 | 公開投影で到達可，完全盤面で不可 |
| 128／38 | 4 | 公開投影で到達可，完全盤面で不可 |
| 132／32 | 1 | 公開投影で到達可，完全盤面で不可 |
| 144／37 | 2 | 公開投影で到達可，完全盤面で不可 |

126／35 は公開投影だけでも代表 NEXT action 0 が到達不能だった．一方，正常な 123／28 は欠落 hidden セルがなく，継続 `[1, 3, 8]` は公開／完全盤面とも幾何到達可能である．hidden 配置を一律に未証明扱いする修正は，この正当な継続まで抑えた．

root は authoritative mask で確認され，実 lock も一致した．後続の証拠では，前の自配置で hidden に残ったセルを新しい公開モデルが保持していないことが問題になる．公開 adapter は hidden を意図的に unknown とし，placement event も `action=None, cells=()` である．したがって，今ある公開履歴だけからこれらのセルを確定復元できない．完全盤面を runtime へ渡す解決や，BFS を無断に追加して予算を増やす解決は採用しない．

## PUYO-266 に残す次の独立作業単位

- **What:** 採用済み自配置の公開履歴と hidden 推定の契約を定め，その推定を既存 PUYO-270 の有限生存 probe へ接続する．必要な selector／receipt／scheduler の境界を別レビュー単位にする．新規 Jira へ自動拡張しない．
- **Why:** 公開 compact 盤面は未知 hidden を空と推定するため，既に塞がった逃げ道を後続 witness にできる．一方，hidden への合法な配置は大連鎖にも必要で，一律抑制は回帰する．
- **How:** public input，採用 intent，実行 outcome，観測による確定／推定／不明を分離する．自配置履歴から推定できる範囲と，stale／fallback／timeout／未 lock／おじゃま／途中参加で不明へ戻す規則を定義する．reset と episode 境界で情報を混ぜず，次 snapshot の可視盤面と整合させる．engine の真の hidden board／未公開ツモ／私的 garbage RNG を oracle として渡さない．後続幾何探索を加える場合は placement と control-state counters を分け，既存 quota 内で課金する設計を先にレビューする．cutoff を死亡や生存と呼ばない．
- **A/C:** 本保存物の 126／128／132／144 で候補→固定順位→selection→receipt→実 lock／clear の境界を確認し，必要な小消しと安全非発火を区別する．正常 123／28 の hidden 継続と 55／123／124 の通常土台・有効逆発火を維持する．3 定型と失敗 135，midgame／unknown／reset／stale／fallback／timeout／予算切れ／公開入力境界を回帰する．新規 daa/random を速度付きで再測定し，元 run の欠損と非再現を保持する．G2 の規定条件と人間 QA を満たすまで PASS／COMPLETE にしない．

## 再確認

最終回帰は 14 件中 13 件成功，1 件は起点でも再現する既存失敗である．`tests.test_nextgen_realtime_diagnostic.GtrCapabilityTests.test_fixed_gtr_fixture_completes_then_rule_switches_to_build_main` の line 54 が，legacy の witness を空と期待するが `(0,)` を返す．親の未変更起点 `732ed3d` でも単独実行で同じ失敗を確認し，期待値を書き換えていない．[最終ログ](tests-final.txt) と [起点ログ](tests-baseline.txt) を分けて保存した．CLI の従来既定 `55/10055/gtr` と独立指定 `55/59/daa` の config 生成，Ruff，diff check は成功した．

```bash
python docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/analyze.py
python docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/inspect_witnesses.py
python docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/audit_daa.py \
  docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/daa-step-before \
  --output /tmp/puyo266-daa-audit.json
python -m unittest tests.test_nextgen_survival tests.test_nextgen_realtime_diagnostic tests.test_nextgen_adoption_replay
```

新しい native build は親が同じ desktop で作成したものを使用した．ノート PC の歴史的 latency との厳密な A/B 比較ではない．正式 G2 の既存 30 seed × 2 repeat の FAIL raw と gate は変更していない．PUYO-270 の生存契約を再実装せず，PUYO-266 の品質修正未達を明示する．
