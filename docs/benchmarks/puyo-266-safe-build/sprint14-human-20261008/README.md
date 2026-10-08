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
