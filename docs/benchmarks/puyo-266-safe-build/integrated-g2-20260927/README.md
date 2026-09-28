# PUYO-266 合成 source の native G2 再測定

## 判定

**observed quality FAIL／G2 BLOCKED**．PUYO-273 の live-clock planner を含む合成 source `2d78cad9b26fb129d4b33dd75add90ff49456491` で，seed 123〜152 × repeat 1／2 × 40 placements の全 60 run を新しく測定した．旧 source `1a25af1` の [FAIL 証跡](../asset-quality-20260927/README.md) は変更していない．この追加 commit は証跡のみであり，policy／重み／core／planner／GUI を変更しない．

| 指標 | 旧 source `1a25af1` | 合成 source `2d78cad` |
| --- | ---: | ---: |
| 完了 identity | 60／60 | 60／60 |
| 平均最大実連鎖（30 seed） | 8.4667 | 8.8667 |
| 最大 10 連鎖以上（30 seed） | 23／30 | 25／30 |
| premature（全 repeat） | 10 | 6 |
| 窒息（全 repeat） | 12 | 10 |
| 配置／判断数 | 2354／2356 | 2366／2366 |
| repeat digest 一致 | 30／30 | 30／30 |
| decision p50／p95 | 0.6449／0.8150 s | 0.6635／0.8349 s |
| worker／scheduler error | 0／0 | 0／0 |

改善した seed だけを取り出して平均を計算していない．平均 10 以上，premature 0，窒息 0 は未達で，G0／G1／必須脅威 fixture／公開既知解 gap 0 もこの cohort では確認していない．native budget の `reference_profile_calibrated=true` は旧 cohort と同じ depth／width／scenario／shared quota の校正を表し，これらの未確認条件を満たす flag ではない．人間 GUI 再 QA と学習開始の許可をこの測定から導かない．

## 固定条件と証跡

- 新規 output `/tmp/puyo266-native-g2-integrated-20260927` を作成し，preflight で clean source，strict native import，旧 manifest との config／build／host 完全一致を確認した．全 worker の前後で同じ照合を行い，測定中の source 変更は false だった．
- native binary SHA-256：`79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．Python 3.12.3，WSL2 host `DESKTOP-G4HAPVQ`，OMP／OPENBLAS／MKL=1，RAYON=6．
- config SHA-256：`af704ba11b0876f32952ede2d9c2086318a26655b04f82e93a90269441bbd1bf`．manifest SHA-256：`56c70f79878d34ec98eac0b2cf3afcd5d335a7a97ad8dcc4915caeffa8e3c659`．
- `nextgen_safe_build`，native depth 16／width 250／6 scenarios／shared quota 600000，template 128／response 256，target 10，GTR／argmax／N=14．公開 current／NEXT／NEXT2，configured inference 0 tick，停止相手，攻撃抑止，最大 40 placements．未知 future は従来の補完で，真の隠れツモを runtime へ渡さない．
- CPU 枠を単独使用し，全 identity を fresh process で直列測定した．60 run が終わってから入力 replay，その後に追加 seed 55 を直列実行した．
- `native-g2/manifest.json` は全 source file SHA，native capabilities，host／packages／profile／予算を保持する．各 raw は候補 batch，順位，公開 request，selection，receipt，入力列，実連鎖，生存，latency，semantic digest を含む．`paired/` は実 lock と resolve を再現した sidecar と旧新対照を含む．

## root の実現と残った品質失敗

全 60 run について保存入力を policy 探索なしで再生し，最終 state hash が元 raw と一致した．**選択 root と実 lock の不一致 0，完全盤面での即時予測と実連鎖の不一致 0，未 lock 判断 0**．旧 cohort の repeat 1 replay を参照する際は，各旧 repeat の入力 semantic digest と一致することを確認した．

全 identity で旧新の初期公開状態が一致し，共通に観測できた lock 順の範囲で公開 current／NEXT／NEXT2 の窓も一致した．比較した窓数を identity ごとに保存し，窒息後の未観測入力は一致の件数へ含めない．途中の盤面は異なる行動経路で変化するため，旧新の全判断を同一盤面の selector 比較とは扱わない．公開状態，順位と receipt は元 raw，実 root／連鎖／生存は `paired/*roots.json.gz`，旧新の digest／latency／差分一覧は `paired/comparison.json` を参照する．offline 完全盤面は原因分離専用であり，公開既知解の証拠ではない．

| seed | 旧 → 新 最大実連鎖 | その他 |
| --- | --- | --- |
| 134 | 11 → 10 | 旧 decision 16 の配置差を解消．最大連鎖は 1 減少し，成功閾値内でも減少を隠さない |
| 137 | 1 → 12 | 旧 decision 34 の配置差を解消．premature 1 → 0，窒息あり → なし |
| 148 | 8 → 10 | 旧 decision 21／25 の配置差を解消．premature 1 → 0 |
| 126／128／132 | 0 → 0 | 両 repeat で無発火窒息が残る |
| 135 | 1 → 1 | 両 repeat で premature 2／窒息が残る |
| 144 | 1 → 1 | 両 repeat で premature 1／窒息が残る |

上記以外の最大実連鎖は旧新で同じだった．seed 142／decision 29 は実 root が一致し，公開予測 9／完全盤面予測 10／実結果 10 の差だけが両 repeat に残る．hidden 行による差を planner の不一致へ数えない．seed 151 の旧未 lock 再計画も新測定では 40 判断／40 配置となった．

seed 135 は，quiet roots が固定定型に違反し，定型に適合する全 14 候補が単発，採用 root は rank 0／compatible=true だった既存の読取結論を保持する．固定定型を継続するか明示解除するかの phase／発火方針は PUYO-266 の残課題であり，PUYO-268 の順位バグとは確認していない．今回の再測定では quiet 優先などの修正を行っていない．

## 固定 3 seed の独立診断

正式 cohort と別の `fixed3/declaration.json` に seed 55／123／124 の 40 手比較を宣言した．55 は全 60 run と replay の後に新規測定し，123／124 は正式 repeat 1 の raw をそのまま参照・複製した．後二つを独立した追加 repeat と数えない．source／native／host／profile／予算は上記と同一である．

| seed | PUYO-271 after | PUYO-268 公開 prefix 後 | 今回の合成 source |
| --- | --- | --- | --- |
| 55 | 最大 0，35 手で窒息，初回 GTR 12 手 | 最大 10，40 手生存，初回 10 手 | 最大 10，40 手生存，初回 10 手 |
| 123 | 最大 10，初回 14 手 limit | 最大 10，初回 9 手 | 最大 10，初回 9 手 |
| 124 | 最大 11，初回 9 手 | 最大 11，初回 9 手 | 最大 11，初回 9 手 |

今回の premature／窒息は 3 seed とも 0，初回 GTR 完成 3／3，実 lock 不一致 0 だった．保存済み PUYO-268 との全 40 手の可視盤面・公開ツモ・action・phase・連鎖列は一致する．public snapshot 全体は event tick と `score_carry` が異なるため完全一致とは記載しない．seed 123 の 9 手後の列高は PUYO-271 after `[2,3,6,6,1,0]` に対して今回 `[5,3,5,4,1,0]` で，PUYO-268 の改善形状を保持する．比較用の 6／10／15 判断目の公開盤面も `fixed3/comparison.json` に保存した．

旧 PUYO-271 after の source `2583976`，PUYO-268 保存 raw の source `9ce0145` の宣言は各既存資料を参照し，今回へ付け替えない．過去は thread 環境や process 分離が異なるため，歴史的 latency を厳密な同条件速度差へ換算しない．公開初期状態と policy の profile／search config は 3 条件で照合済みである．

40 手で構築形状の比較が一致したため新しい 80 手測定は追加していない．[以前の新規 80 手診断](../asset-quality-20260927/README.md) は別 source のまま保持する．元の人間 GUI セカンド逆発火の seed／raw は不明で，今回も同一 run の再現とは扱わない．asset sidecar の使用セル数は公開モデル予測であり，hidden 行を含む実セル identity の確定観測ではない．

## 検証と再確認

合成 source の境界回帰 9 modules／99 tests は親セッションが 47.7 s で成功確認した．本追加は docs 内の証跡のみで，その回帰を再実行して新しい回帰件数へ重複計上していない．全 60 identity の repeat／集計／SHA と保存入力の replay，固定 3 seed の診断を検証した．

```bash
python docs/benchmarks/puyo-266-safe-build/integrated-g2-20260927/verify.py
python -m eval.nextgen_adoption_replay \
  docs/benchmarks/puyo-266-safe-build/integrated-g2-20260927/native-g2/seed-137-repeat-1.json.gz \
  --output /tmp/integrated-replay-check
```

`verify.py` は旧 cohort と同じ検証器で，新しい artifacts の SHA と全 60 run の集計／gate を再計算する．policy 探索や学習は実行しない．Jira は In Progress，PR は draft を維持し，学習 PUYO-256〜258 は開始しない．
