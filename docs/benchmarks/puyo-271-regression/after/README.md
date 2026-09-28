# PUYO-271：統合後の同条件比較

## 判定

PUYO-271 の公開反例 corpus／比較器は完成した．**モデル品質は未達**である．`nextgen_safe_build` の seed 55 は，変更前の 12 実連鎖・40 配置生存から，変更後は最大実連鎖 0・35 配置で窒息した．GUI も片側／両側とも frame と input の p95 ≤ 25 ms／p99 ≤ 50 ms を満たさない．[paired_comparison.json](paired_comparison.json) は全 decision の候補・選択・receipt・公開生存 probe と，実入力を再生して検証した score を含む．[evidence_manifest.json](evidence_manifest.json) は最終 artifact の SHA-256 を固定する．

これは seed 55/123/124 ×各 1 回の診断 sample であり，正式 G2 30 seed×2 repeat の実施／PASS ではない．元のユーザー GUI seed／replay は未提供なので，合成 fixture・この seed 対局・GUI F12 注入を元事象の完全再現とは扱わない．Ama は同条件未測定であり，既存の静的値を対局や GUI の実測列に入れない．

## 比較条件と provenance

変更前は [before/](../before/) の Python source `c0c77d944ff3ed276a4b44a40a779cd72bc0977a` と native wheel SHA `4505762d3e17672edec97b653248daa300857c80301c12324e567114a9aa0e9f`．変更後は Python source `258397624245407e402ebf81c5ca20bdc007a475`，native release binary は source `9b64186087ea8b6e1219861ddbb534b39233966c`，SHA `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．native は PUYO-272 の binary を `/tmp/puyo271-final-venv` にコピーした．Python と native の source SHA は意図的に異なり，別々に [regression_manifest.json](regression_manifest.json) に記録する．測定中の Python source 変更は false．`before/` は一切上書きしていない．

同じ Intel Core Ultra 7 258V／WSL2 host，CPython 3.12.3，affinity CPU 0–7，OMP／RAYON／OPENBLAS／MKL thread 環境変数は未設定．native は release/cp312/scenario-6・6 workers．config のうち `nextgen_templates.yaml` だけ SHA が変更され，deep-chain config と nextgen quota profile は同一である．各 run は新しい policy instance を使い，同じ 1 process 内で nextgen 55/123/124 → deep_chain 55/123/124 の順に直列測定した．最初の意思決定を除外せず，cache の hit/miss と全 raw を保存した．affinity／thread は runner が各 run 中に埋め込んでいないため，同一 host の終了後解析時に採取した値であり，run 中の変化までは証明できない．

対局は `SafeNoThreatMatch`，相手非活動，公開 current／NEXT／NEXT2，configured 0 inference ticks，最大 40 実設置．reference は `deep_chain_builder` の target 10／quality floor 10，depth 16，width 250，6 scenario，最大 600000 expanded nodes．nextgen は `nextgen_safe_build` の shared 600000／template 128／response 256 の独立 quota，GTR → 自由構築．両 policy は同じ seed と公開組生成器を使うが，行動が異なれば盤面分布と途中の公開 state は変わる．nextgen は ghost 行を除き，reference の既存 adapter は own ghost 行を参照する．公開 future の seed derivation も異なるため，差を単純な policy 単体効果と読まない．GUI は別の measured latency mode であり，この headless decision 秒と合算しない．

## 合成反例と backend の接続

同じ [fixture SHA](../synthetic_results.json) の公開ペルシャ横 3／L 字では，旧 static fit は両方 satisfied=2／conflicts=0／fit．統合後の guard は横 3 を compatible／conflicts=0／fit，土台 A が補充で 1 連鎖消える L 字を incompatible／conflicts=1／no_fit と分けた．元 action の chain は横 3 が 0，L 字が 1 のままであり，合成盤面を対局での遭遇率とは解釈しない．必要な生存単発は safe action が chain 1／生存，fatal action が chain 0／game over．GTR／だぁ積み／ペルシャ式の各 2 手 witness は完成し，known／unknown／quota 0 の区別も維持した．旧 static と新 guard の結果を [paired_comparison.json](paired_comparison.json) の `synthetic.persian_static_vs_guard` の別列に保存した．

PUYO-272 の [Python/native parity 証跡](../../puyo-272-template-search/README.md) は，optional selected-template 制約あり／なしの公開 root 証拠・plan・semantic digest が共通 quota の 8 組ですべて一致する．今回の対局はその native binary を使い，PUYO-268 の [統合 fixture](../../puyo-268-template-integration/README.md) と同じ guard を通す．制約付き合成 L 字で build_template が全 root violated となり build_main fallback に進む例は，土台保持成功数へ算入しない．PUYO-270 の [必要な生存単発](../../puyo-270-survival/README.md) は別の合成反例で確認済みである．

## 全 seed の品質と速度

score は保存された全 tick input を同じ simulator に再投入し，最終 `state_hash` が raw と一致した場合だけ採用した実 score．時間は各 decision の wall seconds の線形補間 p50/p95．未完了理由は raw と比較 JSON に残した．

| policy／seed | 変更前 配置・最大実連鎖・score・死亡 | 変更後 配置・最大実連鎖・score・死亡 | 初回 GTR phase | 変更後の完成後最大連鎖 |
| --- | --- | --- | --- | ---: |
| nextgen 55 | 40・12・64656・なし | **35・0・241・窒息** | 12 設置で完成，13 request で解除 | 0 |
| nextgen 123 | 40・10・41002・なし | 40・10・44763・なし | 14 設置で未完成／limit，後の phase が 38 設置で完成 | 0 |
| nextgen 124 | 40・10・53350・なし | 40・11・51610・なし | 9 設置で完成，10 request で解除 | 11 |
| reference 55 | 33・1・279・窒息 | 33・1・279・窒息 | 対象外 | 対象外 |
| reference 123 | 40・10・39717・なし | 40・10・39717・なし | 対象外 | 対象外 |
| reference 124 | 40・10・46901・なし | 40・10・46901・なし | 対象外 | 対象外 |

初回 14 設置内の GTR 成立は変更後 2/3．seed 123 の 39 request での `completed` を初回成立へ数えない．nextgen の premature（1〜9 実連鎖）は変更前後とも 0，reference は seed 55 の 1 件のみ．nextgen の decision p50/p95 は変更前 0.706/0.885 s（120 件）→変更後 0.544/0.715 s（115 件），reference は 0.544/0.642 s（113 件）→0.497/0.580 s（113 件）．1 回の非同一盤面軌跡なので有意な高速化とは扱わない．

候補・選択・実行は列を分けた．変更後の公開生存 probe が証明した witness root の候補欠落，witness がある中での fatal root 選択／実行，receipt 非採用，requested／executed 不一致は，3 seed でいずれも 0．headless timeout／stale も 0．shared-search 先頭以外の採用は変更前 44/120 →変更後 17/115 だが，戦術別の順位と shared 順位は用途が異なる．最適 root の固定 oracle がないため `rank_error` は null と理由を保存し，順位誤りゼロとは宣言しない．変更前 raw は生存 root probe を内蔵しないため，その witness gap を 0 と補完しない．PUYO-270 の同一公開入力へ適用した別の有限 probe（quota 128）の変更前 gap 0 は参照値としてのみ扱う．

seed 55 は phase 完了後に build_main で 13〜35 手を進め，chain 0 のまま盤面が高くなった．26〜34 手の native run 内蔵生存 probe は response quota 256 内の 16〜56 node で bounded witness を返し，選択 root は witness，receipt はすべて activated．31〜34 手には chain 10 の **将来** fire plan（深さ 3／2／3／2）があるが，現在組で直ちに 10 連鎖を発火する root ではない．`fatal_rate=null` のため `fire_main` selector gate を通らないこと自体は before の深さ 3 plan と同じ契約である．35 手目は到達可能 root 7／9 が公開 horizon でともに fatal，probe status は unknown，選択 root 7 は実行後に死亡した．直前の公開盤面・既知 3 組と全 root status は `paired[].after.decisions[34]` に保存した．この有限公開 probe だけでは，より前の行動で回避できたかは確定しない．「必要な単発を拒否した」270 回帰ではなく，完成後に発火可能 root へ育てられなかった PUYO-266 品質残差として扱う．

## 実 GUI cadence

[GUI raw](gui/) は PUYO-269 の計測器と同じ `DISPLAY=:0`，1120×780，seed 55，60 FPS 上限，速度 1.0，overlay off，最大 2400 tick，replay 保存なし，native safe-build，片側 nextgen 対 random 600 frame／両側 nextgen 360 frame．各 run は別 process で開始し，直列に測った．50 ms 間隔で別 thread から無作用 F12 を投入し，予定投入時刻から GUI input 処理までの遅延を測った．今回は frame ごとの interval／event／update／render／tick と input event の個票を gzip に加えた．[GUI summary](gui/summary.json) はその個票から再計算・照合でき，[GUI manifest](gui/manifest.json) が probe／raw SHA を固定する．終了後に worker 残存なしを process listing で確認した．

| 条件 | frame p50／p95／p99 ms | input p50／p95／p99 ms | request／activated／stale | gate |
| --- | --- | --- | --- | --- |
| 片側 600 frame | 16.3／62.5／114.6 | 11.7／67.4／87.2 | 14／6／7 | p95／p99 とも未達 |
| 両側 360 frame | 18.6／151.2／295.3 | 22.9／208.9／284.3 | 各側 8／8／0 | p95／p99 とも未達 |

GUI timeout／deadline miss／fallback は両 run 0．片側の stale 7 は headless configured 0 tick 対局の stale 0 と異なる実行条件である．PUYO-269 の既存保存値は変更後 source `0be88b9` の片側 frame p95/p99 58.8/121.2 ms，両側 190.4/370.3 ms であり，元 raw は集計値で per-frame 個票を持たない．今回 source `2583976` では方策・盤面分布も変わるので，二つの数字を直接の GUI 改善率とは扱わない．F12 注入は物理 keyboard／人間の見た目の確認ではない．

## 残 A/C と実行順

1. **PUYO-268（draft／In Progress）**：合成 guard と receipt 接続は確認したが，一般比較では seed 55 の連鎖 12→0・窒息，初回 GTR 成立 2/3．一般品質 A/C は未達として再検討する．
2. **PUYO-266（In Progress）**：完成即自由構築の phase 遷移は確認したが，seed 55 の完成後 23 手が chain 0．理由なき早消しの fixture と 10 連鎖級／窒息を修正後同じ runner で再測定し，正式 G2 30 seed×2 repeat は同チケットが実施・判定する．今回の 3×1 を代用しない．
3. **PUYO-273 → PUYO-269（In Progress）**：UI thread の scheduler／到達性処理残差を減らし，同じ DISPLAY／frame／F12 条件で p95 25 ms・p99 50 ms gate を再測定する．人間の見た目と実 keyboard の QA も別途実施する．
4. **PUYO-264（In Progress）**：通常速度と `n` 低速ステップで同じ公開入力・seed を固定し，3 列目縦積み，phase，候補順位，receipt，timeout／stale を対比する．今回の F12 cadence や headless 対局をその代わりにしない．

その後に PUYO-266 の正式 G2 と人間 GUI QA を見て，PUYO-256〜258 の学習開始可否を判断する．本作業では学習しない．

## 再集計・人間 QA

専用 release-native venv を使い，既存 raw を上書きしない．`verify` は raw checksum，headless 集計，score を再生した paired 比較，GUI 個票集計，最終 manifest を照合する．

```bash
/tmp/puyo271-final-venv/bin/python -m eval.puyo_271_regression verify --run-dir docs/benchmarks/puyo-271-regression/after
/tmp/puyo271-final-venv/bin/python -m unittest tests.test_puyo_271_regression tests.test_puyo_271_integrated
```

測定を取り直す場合は新しい出力 directory を使う．GUI は `DISPLAY=:0` で `python -m eval.puyo_271_gui_probe --policy nextgen_tactic_manager --frames 600 --output <new-json>`，両側は `--both --frames 360` を付ける．人間 GUI QA は以下を片側で起動し，同じ `--policy-b nextgen_tactic_manager` の両側も目視・操作する．画面・入力応答，定型，早消し，窒息，実 receipt を記録する．

```bash
python -m eval.realtime_versus_ui --seed 55 \
  --policy-a nextgen_tactic_manager --policy-b random \
  --nextgen-templates gtr --nextgen-selection-mode argmax \
  --nextgen-seed 55 --nextgen-commit-turns 14 \
  --nextgen-profile nextgen_safe_build --nextgen-backend native
```

参照：[PUYO-271](https://shhchan.atlassian.net/browse/PUYO-271)，[PUYO-268](https://shhchan.atlassian.net/browse/PUYO-268)，[PUYO-264](https://shhchan.atlassian.net/browse/PUYO-264)，[PUYO-266](https://shhchan.atlassian.net/browse/PUYO-266)．
