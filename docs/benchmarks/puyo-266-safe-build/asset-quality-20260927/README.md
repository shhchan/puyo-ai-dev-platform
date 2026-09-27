# PUYO-266 完成資産の利用診断と native G2 cohort

2026-09-27 の人間 GUI 観測で，GTR のセカンド 10 連鎖が逆発火になり，構築部が残った．元 run の seed／raw／receipt は保存されていない．この資料の seed 55／123／124 を元 run と同一視しない．同日の人間 GUI では理由のない土台破壊と発火不能窒息は非観測だったが，過去の機械失敗を上書きしない．

## 採否と所有境界

今回追加したのは公開盤面と実採用 receipt に基づく資産診断，および native の固定 G2 cohort runner である．policy の重み，native ABI，構築中の PUYO-268 prefix 順位は変更していない．未使用土台を不良とみなして発火を罰するだけでは，有効な逆発火や次の土台の温存を妨げる．新しい再現 run でその害を確認できていないため，順位変更は採用しない．

`eval.nextgen_asset_diagnostic` は，`completed` が公開盤面で確認された定型の必須セルに由来 ID を付ける．実採用 root の消去・重力で ID を追い，同色の落下セルが元の位置を埋めた場合も元セルが残ったと誤認しない．GTR 必須セルは 8 個であり，盤面全体や周囲の連鎖部をすべて GTR と数えるものではない．

発火時には，全到達可能 root と batch 内の公開 current／NEXT／NEXT2 plan を別枠の最大 2048 transition で比較する．元の候補 ID，戦術別順位，shared root evidence，selection，receipt，quota，latency を保存する．この offline 予算を policy の固定 quota へ混ぜない．公開 plan にない代替本線や sampled future は「未知」であり，不在の証明にしない．hidden 行が未知の場合は代替評価も `public_estimate` のまま残す．

`unused` は公開モデル上で由来セルが消えなかったという予測だけを表す．receipt の activation と実連鎖数の一致だけでは，実配置や個別セルの消去を証明できない．実 root の一致を別途照合し，hidden 行や後続盤面の不連続があれば実セルの利用は未知として扱う．有効な逆発火を一律禁止する flag／penalty はない．今後の順位変更には，同じ公開入力の到達可能候補で，安全性，実火力，残存形，次の本線見通しを比較できる反例が必要である．相手の攻撃への cancel／counter は独立した正当化であり，本診断の無脅威 solo 条件から否定しない．

## 新しい 80 手再現

起点 `156a7618027151134783197a969ba7ed50b3015f`，native binary SHA-256 `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．host／source は `declaration.json`，各判断の profile／config／native provenance は raw 内に記録した．停止相手・攻撃抑止・configured latency 0・GTR・argmax・N=14・`nextgen_safe_build` の新規診断であり，人間 GUI の再現 run や正式 G2 の repeat ではない．

| seed | 実発火（手数:連鎖数） | premature／窒息 | 公開モデルの必須セル消去予測 | 80 手の経過時間 |
| --- | --- | --- | --- | --- |
| 55 | 32:10，53:10 | 0／0 | 8／8 | 58.188 s |
| 123 | 31:10，47:10，78:10 | 0／0 | 8／8 | 62.072 s |
| 124 | 31:11，54:10，76:10 | 0／0 | 8／8 | 65.509 s |

3 run の初回は公開モデル上で完成 GTR 8 セルの消去を予測した．seed 123／124 では発火後の公開盤面との不連続も記録しており，この値を実セル追跡の確定値とはしない．第 2 発火以降は `free_build_no_proven_fit` で，定型の完成記録がない．元 GUI の「セカンドの完成 GTR を残す逆発火」は未再現である．旧 PUYO-271 after の seed 55 窒息・初回 GTR 2／3 と，PUYO-268 新 head の 3 seed 最大 10／10／11・窒息 0・初回 GTR 3／3 は，それぞれの既存 source／raw に帰属する．

## 正式 cohort の固定宣言

`eval.nextgen_safe_build_gate` は既存の Python smoke runner と別の native runner である．`init` は source と tests の未 commit 変更を拒否し，source SHA，native binary／capabilities SHA，Python／依存ライブラリ，host，thread 環境，profile，catalog，seed stream，quota を manifest へ固定する．各 worker の前後で照合し，異なる manifest の混入，既存 run の上書き，破損した trajectory を拒否する．

条件は seed 123〜152 × repeat 1／2 × 40 placements，公開 current／NEXT／NEXT2，configured latency 0，停止相手，攻撃抑止，1 worker，各 identity が新規 process，native `scenario-6` である．shared depth 16／width 250／6 scenario／quota 600000，template quota 128，response quota 256，target 10 を使用する．未知の将来ツモは既存の `legacy-fixed-six` 補完であり，runtime へ真の隠れツモを渡さない．

`reference_profile_calibrated=true` は，PUYO-266 の既存校正に加え，runner が `deep_chain_builder` reference と depth／width／scenario／shared quota の一致および target 10 を実行時に検証したことを表す．以前の smoke 固定 profile を true へ書き換えたものではない．reference 比較とは seed の導出方法，定型 phase，ghost 行の観測に差がある．latency の閾値充足，前段 gate，対戦品質をこの flag から推定しない．

全 60 identity の最大実連鎖・premature・窒息・repeat digest・decision latency を判定する．平均連鎖の分母は repeat 1 の 30 seed，premature／窒息は全 repeat とする．repeat digest には実入力／実結果／receipt に加え batch digest も含める．G0／G1／必須脅威 fixture の証拠は未確定として宣言しており，全 60 run が良好でも自動的に G2 PASS にはならない．人間 GUI の口頭観測から `gui_ledger=true` を捏造しない．PUYO-256〜258 の学習は開始しない．

```bash
env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=6 \
  python -m eval.nextgen_safe_build_gate --output /tmp/new-native-g2 init
env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=6 \
  python -m eval.nextgen_safe_build_gate --output /tmp/new-native-g2 run-all
python -m eval.nextgen_safe_build_gate --output /tmp/new-native-g2 finalize
python -m eval.nextgen_asset_diagnostic /tmp/new-native-g2/seed-123-repeat-1.json.gz \
  --output /tmp/new-asset-audit
```

## 回帰と残課題

### 60 run の結果

測定 source は `1a25af1`．全 60 identity を保存し，欠損／未完了 worker は 0，30 seed の repeat digest はすべて一致した．窒息による途中終了も結果として数え，成功した run だけに分母を縮めていない．

| 指標 | 結果 |
| --- | --- |
| 最大実連鎖の平均（repeat 1，30 seed） | 8.4667 |
| 10 連鎖以上の seed（repeat 1） | 23／30 |
| premature（全 repeat） | 10 |
| 窒息（全 repeat） | 12 |
| 配置／判断の観測数 | 2354／2356 |
| 判断 p50／p95 | 0.6449／0.8150 s |
| repeat digest 一致 | 30／30 |
| observed quality／G2 | FAIL／BLOCKED |

| 品質未達 seed | 最大実連鎖 | premature／run | 窒息 | repeat |
| --- | --- | --- | --- | --- |
| 126，128，132 | 0 | 0 | あり | 両方で再現 |
| 135 | 1 | 2 | あり | 両方で再現 |
| 137，144 | 1 | 1 | あり | 両方で再現 |
| 148 | 8 | 1 | なし | 両方で再現 |

平均 10 以上・premature 0・窒息 0 の条件は実測で不合格である．これに加え，G0／G1／必須脅威 fixture／公開既知解 gap の宣言上の証拠も未確認のため G2 は BLOCKED のままとする．p95 が 1 秒以下だったことは品質の失敗を取り消さない．全 raw，固定 manifest，集計と実行 log は `native-g2/` に保存した．

`python docs/benchmarks/puyo-266-safe-build/asset-quality-20260927/verify.py` は SHA と全 run の集計／gate を再検証する．policy 探索や学習は実行しない．

資産追跡，安全／契約／shared／戦術／survival，native gate，保存入力 replay を含む最終 83 tests が成功した（`final-tests.log`）．同色置換，永久 hidden 行，未使用資産を残す有効な発火，公開 3 手境界，実 receipt と chain の一致，manifest 改竄／上書き／repeat batch 差／60 run から他 gate を捏造しないことを確認した．合成の資産保持例は元 GUI の逆発火を再現した証拠ではない．

元 GUI run の再現，望ましくない未使用 GTR の確定反例，新 head での人間 GUI 再 QA，G0／G1 と必須脅威・公開既知解 gap の一式の証跡が残る．この PR をもって品質 PASS／Jira COMPLETE とはしない．

## 実採用から lock までの境界

`eval.nextgen_adoption_replay` は保存した入力を policy 探索なしで tick ごとに再生し，選択 root，activation receipt，実際の軸／回転，lock，resolve を同じ raw で照合する．30 seed の repeat 1 はすべて最終 state hash が元 raw と一致した．offline の完全盤面は原因分離にだけ使い，runtime 入力や公開既知解 gate の証拠にはしない．

| seed／decision | 選択した配置 | 実 lock | 連鎖予測 → 実結果 | 境界 |
| --- | --- | --- | --- | --- |
| 134／16 | x0 RIGHT | x1 UP | 0 → 0 | 連鎖数だけでは配置差を検出できない |
| 137／34 | x3 RIGHT | x2 RIGHT | 12 → 1，窒息 | tick 1800 の自然落下後，1802 の回転で左へ蹴られる |
| 148／21 | x4 LEFT | x3 LEFT | 0 → 8 | tick 1200 の DOWN と自然落下で y11 → y9 |
| 148／25 | x1 RIGHT | x2 UP | 0 → 0 | 選択 root と異なる lock |

seed 137／148 の当該連鎖予測は公開盤面と offline 完全盤面で同じであり，hidden 行では配置差を説明できない．activation は入力計画の開始を表し，その root の実 lock を保証していない．幾何探索と実 tick／gravity の境界を PUYO-273 へ渡した．この PR は core／planner を編集しない．保存した旧入力を使う回帰は歴史的失敗を再現するもので，planner 修正後の新しい入力計画を評価するものではない．

別の原因も混同しない．seed 142／decision 29 は root が一致し，公開予測 9／完全盤面予測 10／実結果 10 だったため hidden 行の差である．seed 151 は 41 判断に対して 40 配置で，未 lock の採用後に再計画するため，資産診断は行対応の曖昧さを拒否する．seed 135 の単発は `build_template` 中で quiet root が存在し，完成後の本件と異なる PUYO-268 の構築順位境界である．seed 144 の単発時には到達可能 root 7／9 の両方が発火し，quiet root はなかった．元 GUI の有効な逆 10 連鎖とは別条件である．

```bash
python -m eval.nextgen_adoption_replay \
  docs/benchmarks/puyo-266-safe-build/asset-quality-20260927/native-g2/seed-137-repeat-1.json.gz \
  --output /tmp/replayed-adoptions
```

`analysis/index.json` は全 repeat 1 の照合結果と資産診断の停止理由を集約する．137／142／148 の連鎖不一致，151 の行対応不一致を成功した資産利用へ読み替えない．そのほかの結果も公開モデルの予測であり，盤面不連続は `board_gaps` に残す．測定後に加えた replay と trajectory 整合性検査は offline 診断のみで，source `1a25af1` の実測を新しい source の実測へ付け替えない．PUYO-273 修正後の G2 は別の測定が必要である．

## 保存済み 3 seed と reference の比較

`comparison.json` では，PUYO-268 の保存済み 55／123／124 の 40 手と今回 80 手の先頭 40 手について，公開入力，selection／phase／action，receipt，連鎖，profile／search config がすべて一致した．source／実行時間は各宣言のまま保持する．

同じ native binary，host，shared quota，無脅威環境で新たに測定した `deep_chain_builder` reference の 40 手は次のとおりである．

| seed | 最大実連鎖 | premature | 窒息 |
| --- | --- | --- | --- |
| 55 | 1 | 1 | あり |
| 123 | 10 | 0 | なし |
| 124 | 10 | 0 | なし |

reference 113 判断の p50／p95 は 0.5628／0.7124 s．raw と宣言は `reference/` に保存した．reference adapter は自盤面の ghost 行を観測し，公開 scenario の seed 導出と定型 phase も異なる．したがって，これは環境と探索予算を合わせた診断比較であり，厳密に同一の公開入力に対する selector 優劣や G2 の公開既知解 gap 0 を証明しない．
