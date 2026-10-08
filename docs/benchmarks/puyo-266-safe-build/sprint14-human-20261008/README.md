# PUYO-266: seed 127 の全消し攻撃と生存 witness

**品質 FAIL/G2 BLOCKED を維持する．元の人間対局の再現ではない．**

報告条件は 1P nextgen，2P human，速度 x1.0，match seed 127，1P policy seed 58，daa，selection softmax，temperature 1.0 である．softmax/1.0 は既定 argmax/0.2 と異なる．元 replay と正確な human 入力列，元 source/config/native SHA は未取得であり，新しい固定入力 fixture と区別する．

## 新しい再現機能と入力

診断 CLI に selection mode/temperature/speed と `--human-inputs` を追加した．normal は指定速度の最大 tick rate で進め，step は worker 完了待ちの別条件と明記する．fixture の schema/seed/重複 tick/不正 edge を検証し，原文と SHA-256 を report に保存する．replay は実際に投入した両 player の入力を保持する．

`human-inputs.json` は seed 127 の公開初期ツモ `(1,1),(1,1),(3,2)` から作った合成入力である．`make_fixture.py` の greedy は入力作成時だけ使い，測定中の 2P には固定済み tick edges を投入する．生成器の simulator は nextgen policy へ渡さない．全消しは 2 手目，ボーナス消費は 12 手目となる．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
.venv/bin/python -m eval.nextgen_realtime_diagnostic \
  --mode normal --seed 127 --seed-a 58 --templates daa \
  --selection-mode softmax --temperature 1.0 --speed 1.0 --opponent human \
  --human-inputs docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/human-inputs.json \
  --placements 60 --max-ticks 3500 --profile nextgen_safe_build \
  --output /tmp/puyo266-human127-new
```

## 変更前の新規測定

1565 tick，32.794 s，23 採用，5 stale，fallback 0．1P が窒息した．全 tick hash/攻撃/全消しと最終 hash の再生一致を確認し，23 実 lock の root 不一致は 0．source の測定中変更なし．`before/report.json.gz` に source 各 file SHA/config，ledger に候補固定順位/selection/receipt，audit に実 lock/clear を保存した．`before/replay.json.gz` は反復 UI/controller diagnostics を除いた入力 replay で，除外 key/元 SHA/サイズは `originals.json` に記録した．元 `/tmp/puyo266-human127-normal` は保持している．

| 公開/実 event | tick | 結果 |
| --- | ---: | --- |
| 2P 2 手目の全消し | 143 | bonus pending，送信 0 |
| 2P 次回消去で bonus 消費 | 690 | 2100 score bonus，生成/送信 32 |
| 1P おじゃま落下 | 724 / 806 | 30 + 2 |
| 1P 最後の判断 | 1532 activation | 即時消去なし，後に窒息 |

最後の判断 28 に公開/実盤面いずれも即時合法消去はない．これを「最後の小消しを無視」と表現しない．一方，判断 26 では公開/実盤面とも root 7/8/10 の到達可能な 4 連鎖があり，即時解決では非 fatal だった．選択は root 3 の非発火 `survival_safe_nonfire` だった．

判断 26 の witness `[3,0,11]` は，root 3 後の列 1 が満杯となり，次 root 0 に公開/実盤面とも幾何到達できない．判断 27 は missing hidden 4 セルが原因で，公開投影だけが次 root 0 を可到達とした．offline 完全盤面を同じ有限 probe に渡しても，26 は別の `[3,11,15]` を返す．この 3 手目 15 も，直前の高さ `[12,14,11,13,12,12]` では fresh-spawn geometry が返す `[7,9]` に含まれない．hidden の欠落だけを直しても，後続操作の不足は残る．完全盤面は offline 診断だけで使用した．

## 採用した狭い修正と固定回帰

完全に埋まった 14 段列を横断する後続配置だけを probe から除外した．root mask と既存 quota は維持する．正常 hidden 継続の一律抑制はない．別 sidecar として，公開 current pair と実 lock action の履歴も追加した．[契約と残設計](../../../development/puyo-266-public-placement-history.md)を参照．

`compare_probes.py` は旧保存物の公開入力を固定して，従来 probe と修正 probe を比較する．383 判断で survival 128/response 256 を超えない．これは対局全体の新しい品質測定ではない．

| identity | 判断数 | 保存された採用 root の証拠の変化 |
| --- | ---: | --- |
| GTR 55 / 123 / 124 | 各 40 | なし．123/28 の `[1,3,8]` を維持 |
| GTR 126 | 36 | 35 判断目 `[3,0,11]` → `[3,12,11]`，witness のまま |
| GTR 128 | 39 | 37 判断目 `[3,0,11]` → `[3,8,0]`，witness のまま |
| GTR 132 / 135 / 144 | 33 / 37 / 38 | なし |
| daa / persian 55 | 各 40 | なし |

127/26 は `[3,0,11]` → `[3,11,15]` となり，非発火 root 3 の witness が残る．この修正だけで必要な小消しを選ぶことは立証していない．

## 採用しなかった control 探索

全後続 root に公開 geometry BFS を追加し，control-state 展開を placement/drop と同じ 128 枠へ課金した試作は，正常 GTR 123/28 で cutoff を起こした．14 テスト中 5 件が失敗したため，runtime へは採用していない．[棄却 patch（gzip）](rejected-control-prototype.patch.gz)/JSON/失敗ログを保存し，正常ケースの期待値は変更していない．

| 入力 | placement / control | 結果 |
| --- | --- | --- |
| 127/26 | 25 / 103 | root 3 と消去 7/8/10 が cutoff，別 root 11 は witness |
| 123/28 | 23 / 105 | 保護対象 root 1 も cutoff，正当な継続を失う |

## 再検証

```bash
python -m unittest tests.test_nextgen_public_placement_history \
  tests.test_nextgen_public_snapshot tests.test_nextgen_survival \
  tests.test_nextgen_survival_walls tests.test_nextgen_realtime_diagnostic_inputs \
  tests.test_nextgen_adoption_replay tests.test_nextgen_safe_build
PYTHONPATH=. python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/compare_probes.py
python -m eval.nextgen_realtime_audit \
  --report docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/before/report.json.gz \
  --replay docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/before/replay.json.gz \
  --output /tmp/puyo266-human-audit.json
```

正式 G2，修正済みの品質宣言，元 human run の同定，人間 GUI QA は未完了である．

現在の関連単体・receipt・safe-build 回帰は 43 件成功，Ruff と diff check も成功した．既存 response fixture 8 件は candidate gap 0 だった．その専用 quota は 2000/4000 であり，通常 profile の response 256 や正式 G2 の全条件の代用にしない．

## 最終 runtime の追加測定

runtime head `b287a3c`（コード変更の最終 commit は `0e7a707`）で，同じ設定の normal127 を再測定した．1800 tick，37.389 s，25 採用，4 stale，fallback 0，1P 窒息だった．source の測定中変更なし．全 tick/最終 hash が一致し，25 実 lock の不一致・未対応 lock は 0．公開 placement sidecar の 25 record も実 lock と一致した．

2P は同じ tick 143 に全消し，690 に bonus を消費して 32 個を送った．1P は tick 810 の 1 連鎖で 2 個を相殺し，残る 30 個を同 tick に受けた．採用 9 件目の時点で before/after の相手公開状態と判断時刻が異なり（request 746 → 704），戦術も build_main → cancel となった．normal の wall-clock 依存を含むため，25 対 23 配置を修正の改善量とは扱わない．

変更後の判断 27/28 でも，即時非 fatal の到達可能な消去候補を持ちながら非発火を選び，それぞれ hidden 2/3 セルの欠落により後続 root 0 を公開投影だけが到達可能と判定した．最後の判断 29 には即時消去がない．`after/audit.json.gz` と `after/witnesses.json.gz` が，この残る反例を候補→順位→selection→receipt→実 lock/clear に分けて保持する．

同一公開入力の probe 比較では，383 判断の全 root で順位に使う status/root_chain/witness depth の evidence が変わらず，変更は到達不能 witness の経路差し替えだった．保存済み 127/26 も root 3 は witness のままである．この部分成果を窒息行動の改善とは呼ばない．

固定 40 配置の最小セットを fresh process で逐次測定し，各 run の入力を再生した．5 run とも source 変更なし，実 lock 不一致 0，全最終 hash の再生一致を確認した．旧保存物と**入力列・最終 hash が完全一致**した．latency の同一条件 A/B 比較や正式 G2 の代わりではない．

| GTR seed | 最大実連鎖 | 小発火 | 窒息 | 配置 |
| --- | ---: | ---: | --- | ---: |
| 55 | 10 | 0 | なし | 40 |
| 123 | 10 | 0 | なし | 40 |
| 124 | 11 | 0 | なし | 40 |
| 126 | 0 | 0 | あり | 36 |
| 128 | 0 | 0 | あり | 39 |

`fixed-after/` と `fixed-summary.json` に保存した．132/135/144 と daa/persian の新しい全対局は未実施であり，前述の固定公開 probe 回帰だけを実施した．正常 3 seed を保つ最小回帰は成功したが，127/126/128 の窒息残差，正式 G2，人間 GUI QA は未解決である．

## 順位順 control 証明の追加 feasibility

`control-feasibility.py/json/txt` は runtime head `c735ea7` の保存済み 24 判断を使った読取解析である．元 `/tmp/puyo266-feasibility.{py,json,txt}` は保持し，script の保存版だけに出力先指定と整形を加えた．完全盤面は **offline 診断専用であり，runtime/policy 入力ではない**．公開 probe は公開 request だけを受け，完全盤面は保存 replay/lock の原因分類にのみ使う．新規 native/GUI/全対局測定は行わない．

順位は保存された `build_main` の候補列を使い，現在の公開 probe の witness を root ごとに確認する．cache は同じ占有形状の探索を再利用する．control は fresh spawn/補間 0 の幾何到達候補で，live 実行保証ではない．JSON の `proven` はこの限定された幾何確認を表す．既存 `_geometric_paths` との判定一致も assert する．以下の費用は無制限の offline 診断で必要量を調べたものであり，production の survival 128/response 256 を増やした値ではない．

| 判断 | 既存 probe | control | 配置再検証 | 合計 | 結果 |
| --- | ---: | ---: | ---: | ---: | --- |
| GTR 123/28 | 53 | 21 | 3 | 77 | 正常 `[1,3,8]` を保持できる局所例 |
| human fixture 127/26，root 11 まで | 44 | 24 | 5 | 73 | 予算内でも hidden 欠落による誤候補が残る |
| GTR 135/34 | 60 | 105 | 3 | 168 | 正当な先頭 witness の確認だけで 128 超過 |
| GTR 132/30 | 62 | 264 | 3 | 329 | 同上 |

GTR 126/34–35，128/36–38，132/32，135/35–36，144/35–37 は，row 12/13 の自配置占有が公開モデルから欠落し，選択 witness が公開推定では到達可能，offline 実盤面では到達不能となる．135/34 は hidden 欠落なしで `[3,0,7]` が幾何到達可能だが，即時非 fatal の 10 連鎖 root 19/21 を選ばない．次判断 35/36 で上部の青 1/2 セルが欠落し，NEXT root 0 の予測が崩れる．35/36 と最終 37 には即時非 fatal 消去がない．

127/26 の `[3,11,15]` を除いても次順位 root 11 の `[11,0,3]` が公開推定を通過し，offline 実盤面では NEXT 0 が不可能である．到達可能な即時 4 連鎖 7/8/10 の選択には至らない．control cache 単独による修正を採用する根拠はなく，runtime 変更は追加しない．元人間 run と同定しない条件も維持する．

再実行は repository root で行う．JSON と標準出力を別の `/tmp` ファイルに保存し，保存証拠と全体一致を確認する．これは軽量な保存入力の診断である．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. \
.venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/control-feasibility.py \
  --output /tmp/puyo266-feasibility-recheck.json \
  > /tmp/puyo266-feasibility-recheck.txt
cmp docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/control-feasibility.json /tmp/puyo266-feasibility-recheck.json
cmp docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/control-feasibility.txt /tmp/puyo266-feasibility-recheck.txt
```

次の独立検証は，公開 placement 履歴と連続する可視 snapshot から自配置の hidden 占有を推定する契約である．確定可能／推定／unknown，visible 整合，episode/reset，midgame，stale/未 lock，clear/おじゃま着弾による失効を区別する．123/28，127/26，135/34→35→36 と上記 4 seed を最小 fixture にする．その後に順位順証明の共有課金，cache の provenance/失効，cutoff を fatal にしない選択規則を検証する．132/30 と 135/34 は一般 BFS の単純追加では収まらない費用反例として保持する．共有 worker/scheduler 契約変更，43 回帰/383 判断全体の非回帰，正式 G2，人間 GUI QA は別途必要である．

## 棄却 patch の可逆保存

`rejected-control-prototype.patch.gz` は元 patch bytes を変更せず gzip 化したもの．patch の context 行末空白を資料側で整形せず，repository の差分 whitespace 検査と適用再現性を両立する．`rejected-control-prototype-manifest.json` に圧縮前後の bytes/SHA-256 と適用元 commit，元/適用後ソースの SHA-256 を記録した．別の一時ディレクトリで `git apply --check`，適用，逆適用による元ソース復元を確認した．製品コードと測定 raw は変更していない．

元 patch は以下で復元できる．これは棄却した実験の証拠であり，作業中の製品コードへ適用しない．適用再現は manifest の base revision から取り出した `agents/nextgen_survival.py` を一時ディレクトリに置いて行う．

```bash
gzip -dc docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/rejected-control-prototype.patch.gz > /tmp/puyo266-rejected-control-restored.patch
sha256sum /tmp/puyo266-rejected-control-restored.patch
```

## 公開推定 observer の固定入力監査

`public-inference-audit.py/json` は新しい公開推定 observer を保存済み GTR 123/132/135 と新規 human fixture で監査する．推定器は公開 snapshot/実 lock/lifecycle だけを受け，完全盤面は driver の offline 期待結果比較専用である．133 判断すべてで known，hidden 2 行の不一致 0．127/26 の (0,12)/(0,13)，135/35–36 の (1,12)/(3,12) を復元し，123/28 の空きも保持した．新規 policy/native/全対局測定ではなく，worker 接続や窒息修正の成功を示すものではない．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. \
.venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/public-inference-audit.py \
  --output /tmp/puyo266-public-inference-audit.json
cmp docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/public-inference-audit.json /tmp/puyo266-public-inference-audit.json
```

関連既存 43 件と起点/欠測/clear/drop/private 非干渉の追加 7 件，計 50 テストが成功した．G2 と人間 QA は未達のままである．

## 公開推定と control/terminal の runtime 接続

known sidecar を request.v2 に bind し，survival に限って利用する．旧 request.v1 は旧 digest を保持して読める．native 入力・actor feature へ hidden セルを追加しない．欠測/不整合/unknown は従来経路へ戻す．未着弾攻撃がある場合も分岐を証明できないため従来経路を使う．

保存済み公開履歴から得た推定と候補順位に対し，実装済み `refine_inferred` と `apply_envelope` を実行した結果を [inferred-survival-audit.json](inferred-survival-audit.json) に保存した．offline 完全盤面は診断専用・runtime 入力ではない．この監査は新規 native policy/対局を実行していない．

| 判断 | 選択 root | 合計 nodes | control/terminal |
|---|---:|---:|---|
| 123/28 | 1 | 62 | certified，正常維持 |
| 132/30 | 11 | 100 | certified，正常維持 |
| 135/34 | 3 | 91 | certified，有効構築を維持 |
| 135/35 | 15 | 87 | 上位 14 の witness は unknown |
| 135/36 | 従来有限 horizon へ fallback | 18 | unknown，回避達成ではない |
| 新規 human 127/26 | 8 | 117 | 合法 4 連鎖 root，元手動対局とは非同一 |

全件で survival 128/response 256 を維持する．127 の 100 node 強制打切りは unknown 診断と元 root 3 の有限 horizon fallback になり，fatal とみなさない．terminal は未知色に依存しない追加 1 配置の十分条件であり，長期安全ではない．cache は request 内のみで，transition の再利用は全 immutable state・pair・action を照合する．cache miss と control 展開も処理前に同じ予算へ課金する．

```bash
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/inferred-survival-audit.py --output /tmp/puyo266-inferred-survival-audit.json
.venv/bin/python -m unittest tests.test_nextgen_inferred_survival tests.test_nextgen_inference_wire tests.test_nextgen_public_inference -q
```

関連 135 tests（既存 43 を含む），Ruff，diff check が成功した．legacy 383 判断の probe/evidence は既存 `probe-comparison.json` と完全一致した．新規対局/実 lock と正常品質の回帰は次の計測段階で確認する．G2 と人間 QA は未達のままである．

## 推定接続後の固定 5 seed と normal127（source ce44f6e）

製品 source を `ce44f6ef4d47dafcb21613c907d285971f84000a` に固定して逐次測定した．各 run の source SHA と native build は raw に保存し，測定中の source 変更はない．[固定結果](inference-v1-fixed/summary.json)と [human 結果](inference-v1-human/inference-summary.json)を別に扱う．正常構築は維持したが，窒息全般の修正完了ではない．

| 固定 GTR seed | 最大実連鎖 | 小発火 | 窒息 | 配置 | decision p50/p95（秒） |
|---|---:|---:|---|---:|---|
| 55 | 10 | 0 | なし | 40 | 0.377/0.430 |
| 123 | 10 | 0 | なし | 40 | 0.371/0.449 |
| 124 | 11 | 0 | なし | 40 | 0.370/0.431 |
| 126 | 1 | 2 | なし | 40 | 0.335/0.444 |
| 128 | 0 | 0 | あり | 39 | raw の集計を参照 |

55/123/124 は旧保存物と入力列・最終 hash が完全一致した．126 は旧 36 配置窒息から 40 配置生存へ変化したが，1 連鎖 2 件を含み，構築品質 PASS とはしない．最初の差は判断 33 で同じ公開入力に対する root 6→1．root 6 の terminal 十分条件が証明できず root 1 を採用した．128 は判断 38 で probe 110 + control 18 = 128 に達し，root 11 の witness は control 不成立，次 root 12 の検査で cutoff．unknown として従来の有限 horizon へ fallback し，窒息が残った．全 199 判断で推定 hidden と offline 実盤面の不一致 0，survival/response 上限違反 0，全実 lock 不一致 0，全最終 hash の replay 一致を確認した．

新規 human127 は同じ seed127/policy58/daa/softmax1.0/x1.0 と固定 2P 入力で 2038 tick/40.364 秒，30 採用/4 stale/fallback0，1P 窒息だった．tick143 の全消し時に送信0，tick690 に bonus 消費で32個送信，1P は tick806 に2個相殺し30個を受けた．実消去は判断13/31の各1連鎖．decision p50/p95 は0.201/0.393秒．全tick/最終hash，30実lockの一致と全34 requestの推定hidden不一致0を確認した．元手動対局とは非同一であり，normal の途中公開入力も以前の保存runと異なるため，30対25/23という配置数を改善量とは扱わない．

新規runの判断31では到達可能な即時非fatal消去はroot9だけで，実際に採用した．判断32の非発火候補3/15は既知prefixの有限witnessを持つが terminal が unknown，33/34には証明済みwitnessがない．判断31以降は予算不足ではなく terminal/継続能力の残差であり，保存済み旧127/26のroot8選択結果とは区別する．

```bash
# 5 件は fresh process で逐次実行する．未使用の出力先を指定する．
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
.venv/bin/python docs/benchmarks/puyo-266-safe-build/desktop-survival-20260928/measure.py --seed 128 --output /tmp/puyo266-inference-repeat
# normal の実行条件は冒頭と同じ．--output だけ未使用の場所へ変更する．
.venv/bin/python -m eval.nextgen_realtime_audit --report /tmp/puyo266-inference-human127/report.json --replay /tmp/puyo266-inference-human127/replay.json --output /tmp/puyo266-inference-human127/audit.json
```

`inference-v1-fixed/*.locks.json.gz` と `inference-v1-human/audit.json.gz` に含む完全盤面は offline 診断専用・runtime 入力ではない．次は 128/38 の同一 transition 再評価と順位順予算を調べる．132/135/144 の新規全対局，正式 60 run と残る G2 条件，人間 GUI QA は未達のまま保持する．

## 追加の予算配分案は不採用

[3 案の数値比較](rejected-stage-summary.json)と小規模な実行 script/JSON gzip を保存した．runtime は ce44f6e のままである．97/106 保存判断を，公開推定と保存 batch の候補順位だけで再評価した読取試作であり，新規 native/対局測定ではない．各案とも 128/256 を増やさず cutoff を unknown とした．offline 完全盤面は診断専用・runtime 入力ではない．

| 判断 | 現 runtime | cache 共有のみ | known 解先行＋terminal 前置き | quiet 優先の段階化＋即時 clear 予約 |
|---|---|---|---|---|
| 正常 123/28 | root1，62 | root1，47 | root1，47 | root1，60 |
| 正常 123/30 | root12，128 fallback | **root8 の1連鎖**，92 | root12，77 | root12，118 |
| 旧 127/26 | root8，117 | root8，117 | root8，90 | root8，90 |
| 128/38 | root11，128 cutoff | root11，128 cutoff | root11，128 cutoff | root11，128 cutoff |
| 132/30 | root11，100 | root11，96 | root11，96 | **root4へ変更**，128 cutoff |
| 135/35 | root15，87 | root15，87 | **root14へfallback**，128 cutoff | root15，87 |
| 新127/31–34 | 9/3/11/7 | 未比較 | 9/3/11/7 | 9/3/11/7 |

単純な transition cache lookup では，generator の予約済み重複計算に既に課金してしまう．課金を行う round robin 側で同じ immutable state/pair/action を共有すると，128/38 の probe は110→70になる．それでも control/terminal の残り58で打ち切られ，窒息候補は変わらなかった．正常123/30のquiet root12を1連鎖root8へ変更する危険もあるため採用しない．

正常123/30には，既存 native が公開既知 prefix の10連鎖plan `[12,16]` を持つ．先頭の幾何witness `[12,12,15]` が不成立でも，root自体が不適切とは限らない．既存fire_main相当の公開known plan 1本を再検証すればroot12を保持できるが，常に先行させると135/35の `[14,20]`/`[15,20]` のcontrol検査が予算を使い切る．任意の小発火planまで探索する案も採用しない．

最後の段階化は，選択上のquiet優先を維持しつつ，即時clearの1候補の証明を先に確保し，quietが得られない時だけ既存known大連鎖解を試す．123/30と135/35は保持できたが，128/38の予約対象root8自身のwitness `[8,1,11]` がterminal unknownで予約できない．さらに132/30ではclear証明への先行課金で正当なquiet root11がcutoffとなり，root4へ変わった．有効な構築を維持する条件を満たさず，全3案を不採用とする．

旧132/32は現推定ロジックでroot5→0，旧144/34はroot15→7という固定候補の変化を得たが，正式な全対局の改善証明ではない．旧144/37と新127/31–34は十分なterminal証明が得られず，予算再配分だけでは解決しない．次に必要なのは，最初の幾何witnessに依存しない公開known-prefixの代替経路を，正常quiet候補への証明予算を奪わず提示する契約である．新127の長い継続能力は別の課題であり，有限horizonを長期安全へ昇格して代用しない．

```bash
# repository root，保存時の runtime source ce44f6e と同じ実装で実行する．
# 元 /tmp ファイルは保持し，別名の /tmp/puyo266-rejected-*-result.json に出力する．
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/rejected-probe-cache.py
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/rejected-alternate-prefix.py
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/rejected-staged-prefix.py
```

保存scriptの再実行結果は各JSON gzipと完全一致する．source置換は診断 interpreter の関数コピー内だけで行い，製品ファイルを編集しない．`rejected-stage-manifest.json` に保存物のhashを記録した．追加runtimeなし，正式G2/人間QA未達，In Progress/draftを維持する．

最終 source の関連 136 tests はすべて成功した（`runtime-final-tests.txt`）．3 案の保存 script の再実行 JSON は gzip 原本と bytes 一致，Ruff と diff check も成功した．

## 着弾済みおじゃまの回復順位

`ce44f6e` の新規 human127 では，受け取った 30 個が残っているのに，incoming packet が消えた後の有限 witness が静かな構築を優先していた．公開推定が known，未着弾 packet なし，到達可能な fatal root が存在し，すでに課金済みの即時・非 fatal 消去が実際におじゃまを減らす場合だけ，回復候補の control/terminal 証明を先に行う．減少数，連鎖数，元順位の順に比較し，認証できた 1 root を `build_main` の先頭へ置く．他 tactic と untested な静的 witness は維持する．128/response256 は増やさず，cutoff/unknown は従来経路へ戻す．有限 prefix と追加 1 配置の証明であり，長期生存の保証ではない．

| 保存判断 | 旧選択 → 回復選択 | 即時連鎖 | おじゃま減少 | 合計 nodes |
| --- | --- | --- | --- | --- |
| 新規 127/17 | 18 → 12 | 1 | 6 | 82 |
| 新規 127/21 | 19 → 5 | 2 | 9 | 87 |
| 新規 127/23 | 19 → 21 | 3 | 14 | 78 |
| 新規 127/27 | 3 → 17 | 4 | 14 | 73 |
| 新規 127/28 | 19 → 17 | 4 | 14 | 68 |
| 新規 127/29 | 15 → 17 | 3 | 14 | 38 |

保存済み 228 判断の実 `apply_envelope` 比較で変化は上記 6 件だけだった．おじゃま 0 の 207 判断は証明・選択・node 数まで一致し，現固定 55/123/124/126/128 の全 199 判断を含む．pending 1 判断は推定不適用の旧経路である．旧 127/26 は root8 の 4 連鎖を維持し，117 → 77 nodes．正常 123/28，132/30，135/34–36 は既存の証明結果を維持した．元 G2 corpus 383 判断はすべておじゃま 0，legacy probe/evidence 比較も既存原本と完全一致した．128/38 などのおじゃま 0 の残差をこの変更で修正したとは扱わない．

```bash
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-audit.py
.venv/bin/python -m unittest tests.test_nextgen_landed_recovery tests.test_nextgen_inferred_survival -q
```

`landed-recovery-audit.py` と `landed-recovery-audit.json.gz` は公開 request と保存 candidate を使用する比較である．baseline の survival 関数だけを `ce44f6e` から独立 namespace へ読み，native/対局は実行しない．offline 完全盤面は診断専用・runtime 入力ではない．この比較では完全盤面も使用しない．関連 140 tests，Ruff，diff check が成功した．新規対局の窒息回避はこの固定判断比較だけでは未検証である．G2 と人間 QA は未達のまま維持する．

### 回復順位を含む新規 normal 3500 tick 測定

source `0633af0` を clean に固定し，同じ seed127/policy58/daa/softmax1.0/x1.0，固定 human 入力，目標 60 配置/max_ticks3500 で単独測定した．3500 tick/68.992 秒，44 採用/5 stale/fallback0，43 実 lock，両者 game_over=false だった．終了直前の判断49は tick 上限による未 lock であり，60 配置の完走とは扱わない．decision p50/p95 は 0.244/0.403 秒．全 tick/最終 hash と 43 実 lock が一致し，49 request の推定 hidden と offline 実盤面の不一致0，quota 超過0だった．

| 判断 | 回復 root | 合計 nodes | 実連鎖 | 予測/実おじゃま減少 |
| --- | --- | --- | --- | --- |
| 19 | 12 | 75 | 1 | 5/5 |
| 28 | 10 | 73 | 1 | 2/2 |
| 30 | 12 | 71 | 3 | 4/4 |
| 39 | 20 | 94 | 5 | 13/13 |

2P は tick143 の全消し時送信0，tick690 に bonus を使い32個送信．今回は1Pが相殺せず，tick722に30個，803に2個を受けた．4回の回復で計24個を消し，終了時8個が残った．前回 ce44f6e との差は途中の公開入力・着弾・判断時刻にも及ぶため，43対30を改善量とは扱わない．raw の input prefix は tick100 で初めて相違し，採用順9件目で候補順位・10件目で選択 action が異なる．各run自身のreplayは全hash一致だが，run間の同一prefixではない．元手動対局とも同定しない．

保存先は `landed-recovery-human/`．report/ledger/replay/audit gzip と原本 SHA，`recovery-summary.json` を含む．完全盤面は offline 監査だけに使う．再監査は次のとおり．

```bash
.venv/bin/python -m eval.nextgen_realtime_audit --report docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-human/report.json.gz --replay docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-human/replay.json.gz --output /tmp/puyo266-recovery-audit.json
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/recovery-run-audit.py docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-human docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/inference-v1-human /tmp/puyo266-recovery-summary.json
```

60配置までの新規延長条件は別runで確認する．本結果を一般的な窒息解消，正式G2 PASS，人間GUI QA成功とは表現しない．

### 60 配置までの fresh 延長 run

上記 3500 tick run を保存後，max_ticks だけを 7000 へ増やした fresh normal run を単独で測定した．Git head は証拠保存後の `72efe8a`，製品コードは `0633af0` と同じで，3500 run と全 170 source/config ファイルの SHA が一致した．実行中変更なし．config の差は max_ticks と保存先だけである．入力・snapshot hash は tick101 で分岐するため，3500 run そのものの継続や同一 prefix と表現しない．共通する採用順の先頭44 actionは一致した．元手動対局との非同一性も維持する．

5090 tick/100.883 秒で目標 60 実 lock に到達した．60 採用/6 stale/fallback0，両者 game_over=false，未 lock/実 root 不一致/無帰属 lock はすべて0．全 tick/最終 hash 一致，66 request の公開推定 hidden と offline 盤面の不一致0，survival128/response256 を含む quota 超過0だった．decision p50/p95 は0.256/0.406秒．実連鎖は判断19/28/30/39/50/63の1/1/3/5/2/9．6回とも回復 root と実 lock が一致し，おじゃま減少の予測/実測は5/2/4/13/1/4で全一致（計29個，32→3個）．追加2件の証明費用は90/54 nodesだった．今回の固定 human 条件では60配置まで窒息を観測しなかった．この事実を G2 の無脅威失敗や人間 GUI 全般の解消へ広げない．

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1 .venv/bin/python -m eval.nextgen_realtime_diagnostic --mode normal --seed 127 --seed-a 58 --templates daa --selection-mode softmax --temperature 1.0 --speed 1.0 --opponent human --human-inputs docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/human-inputs.json --placements 60 --max-ticks 7000 --profile nextgen_safe_build --output /tmp/puyo266-landed-recovery-human127-extended
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/recovery-run-audit.py docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-extended docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/landed-recovery-human /tmp/puyo266-recovery-extended-summary.json
```

`landed-recovery-extended/` に report/ledger/replay/audit gzip と集計を保存した．元 `/tmp/puyo266-landed-recovery-human127-extended/replay.json` は2690118374 bytesで保持し，SHA-256は`34e0ac511064b2e1c6608ef9ae607f31941d90bf58772b69b6f44658315339b7`．`archive-stream.py` は元JSON全体をメモリへ読み込まず，既存 archive と同じ3種類の重複UI診断キーだけをtickごとに除き，input/hash/攻撃/最終状態を保持する．複数chunkにまたがる小fixtureの展開JSON一致と，保存版5090tickの全hash replayを確認した．原本bytes/SHAを`originals.json`，圧縮保存物のSHAを`landed-recovery-manifest.json`へ記録した．

```bash
.venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/archive-stream.py /tmp/puyo266-landed-recovery-human127-extended/replay.json /tmp/puyo266-replay-light-copy.json.gz /tmp/puyo266-replay-original-copy.json
```

未達は，おじゃま0の128/38でprobe110+control18=128となるcutoff，126の小発火，132/135/144を含む正式G2の品質条件，人間GUI QAである．単純cache/既存known解先行/clear予約は保存済み正常123/30または132/30・135/35を退行させるため，追加しない．次の独立設計は最初の幾何witness以外の公開known経路を提示する契約と，正常quietの証明予算を奪わない共有課金であり，今回の着弾後回復とは適用条件を分けて検証する必要がある．個別FAILが明確なため正式60runは再測定せず，品質FAIL/G2 BLOCKED，Jira In Progress，draftを維持する．

## 126 の発火適格性と 128 の代替 prefix の分離

固定 126/33 には到達可能で即時非fatalの10連鎖root12/17/21があり，既存fatal_rate=0を満たしていた．しかしfire_main先頭は3手の10連鎖候補でfatal_rate未評価だったため，ruleがfire_main全体を不適格としていた．34でも同様，35では右側への到達が閉じて小発火しか選べなくなる．小発火の禁止では解決しない．

候補・evidence・selector条件を変えず，fire_main内で既存必要条件fatal_rate=0を満たす候補を未評価候補より先へ置く．各群内の既存順位を保ち，survival順位は引き続き外側で優先する．保存199判断の読取比較では126/31・33・34が即時10連鎖へ変わる一方，正常123/29と124/25–30の一部も10/11連鎖へ早まる．同一入力維持とは扱わず，正常3seedの実品質測定を採用条件にする．実Python共有探索2000nodesを使う126/33の統合fixtureで，未評価future候補を残したまま実selector/envelopeがfire_main/root12を選ぶことを確認した．関連141tests成功．

128/38は別原因である．既課金cache70辺に含まれるroot11/12/8/14の各3手経路には，現在のcontrol/terminal条件を満たすものがなかった．公開known3手を診断専用に全列挙すると，root12の[12,19,14]はcontrol/terminal23nodes，root8/14の[8,6,12]/[14,6,12]は9nodesで確認できる．ただし探索発見コストは別であり，それぞれ14/138/138配置展開を要した．既存probe110に証明23だけでも133となるため，経路の存在だけをproduction128内の修正とは扱わない．cacheだけの追加や無制限列挙をruntimeへ入れず，代替prefix提示・探索と証明の共有課金を次の契約課題として残す．private field/未公開futureは一切使用していない．

```bash
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/fire-eligibility-audit.py
PYTHONPATH=. .venv/bin/python docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/residual-prefix-audit.py
.venv/bin/python -m unittest tests.test_nextgen_fire_eligibility -q
```

JSONは保存公開request/batchの読取解析であり，native再対局や正式G2の代用ではない．残る128の個別FAILを理由に，正式60runは実施しない．
