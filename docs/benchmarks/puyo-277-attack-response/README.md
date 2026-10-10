# PUYO-277 固定対戦 gate の証跡

**未達**．公開盤面 collector の修正を既存 replay に適用した結果は，56 条件中 PASS 47，FAIL 5，回避不能として成功から除外 4 である．PUYO-277 を COMPLETE とする根拠にはならない．とこぷよ品質や G2 全体の合格とは独立した結果である．

## 実行 identity

- fixture 登録: `00a8fe1`，pattern ID `0，1，32768，65535` × 7 ケース × attack/no_attack．
- 正式実行: `7091afd`，[manifest](formal-v1/manifest.json) の semantic SHA-256 は `c047f98a23a8dfc91eb48a96cd82efe31177e6c78d8a74c2f94b46e9ec67cb63`．
- corpus SHA-256: `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．PUYO-275 の color mapping と実 current/NEXT/NEXT2 を照合した．
- native backend，thread 設定は各 1，1 process．shared/template/response quota `256/128/4000`，configured timeout/action deadline 各 120 tick．最大 3 resolution／1,800 tick．
- 56 条件を 210.16 秒で完走した．160 decision 全件 activated，quota 超過 0，実 lock 不一致 0，deadline 逸脱 0．全 56 replay の全 tick hash／event と最終 hash を検証した．
- collector 修正: `b64e28d`．既存 replay のみを再監査し，policy 呼出し 0，追加 tick 0．[再監査 summary](public-audit-v1/summary.json) に元 manifest と監査実装の checksum を保持する．

## 判定

| ケース | PASS | FAIL | 回避不能除外 |
| --- | ---: | ---: | ---: |
| 相殺境界 | 8 | 0 | 0 |
| 着弾後回復 | 7 | 1 | 0 |
| 独立副砲 | 8 | 0 | 0 |
| キー未設置の本線部分 | 8 | 0 | 0 |
| 副砲なし | 8 | 0 | 0 |
| 本線温存 | 4 | 4 | 0 |
| 回避不能 | 4 | 0 | 4 |

攻撃なし対照 28 条件はすべて PASS だった．短い固定盤面の正常配置回帰であり，長期の本線構築能力の保証ではない．search 全体の quota cutoff／partial と，必要な公開 root と実行を確認した肯定的証拠を分けて保存している．

## 残る 5 条件

| 条件 | 原因と証拠 |
| --- | --- |
| `preserve_mainline-0-attack` | 最終 tick 169 が arrival 240 より前で，3 resolution の観測窓が不足した．本線は保持されたが，攻撃を相殺・着弾まで観測できていない． |
| `preserve_mainline-1-attack` | 同上．最終 tick 175． |
| `preserve_mainline-32768-attack` | 同上．最終 tick 159． |
| `post_arrival_recovery-65535-attack` | 最初に副砲を発火し，1 個を相殺して tick 90 に 29 個が実着弾した．その後の 2 resolution は発火せず，着弾後発火という登録条件を満たさなかった．全 3 resolution は生存した．公開 counter 候補 `[0,1]` も記録されているが，その候補が実際に採用されたとは扱わない． |
| `preserve_mainline-65535-attack` | decision 3，request tick 108 で action 4 を選択し，3 連鎖で本線を消費した．同じ公開 request に reachable な action 3／5／6 があり，いずれも副砲の 1 連鎖で本線を保持する幾何学的候補だった．選択された手の実 lock と実 3 連鎖・残存盤面は一致した．過剰火力を選ぶ policy 順位の改善候補であり，eval 側で手を差し替えていない． |

追加観測窓は未実行である．実装対象外の agents を修正する場合は，親セッションが担当範囲と回帰条件を割り当てる．元の観測窓・script・pattern・予算を変更して既存結果を置き換えない．

## 保存物と検証上の訂正

[正式 v1 summary](formal-v1/summary.json) は旧 collector の PASS 40／FAIL 16 を保持している．着弾演出中の `None` 盤面を即座に不一致とした 8 条件と，上端飽和で一部しか落ちなかった回避不能 4 条件を，再監査で分類し直した．後者は全 22 合法 root の致死証明と実窒息を確認し，応答成功に加えず除外した．再監査によって新しい配置・探索・攻撃を実行したわけではない．

各 `formal-v1/<case_id>-<condition>` に `report.json.gz` と `replay.json.gz` がある．`public-audit-v1/<case_id>-<condition>.json` は元 report／replay digest，元判定，新判定，最初に盤面が公開された tick を保持する．`legacy-rule-smoke` は，初期の quota cutoff を一律 FAIL としていた native paired 調査の元結果であり，正式結果と区別する．

再現・監査コマンドは [開発文書](../../development/puyo-277-attack-response-gate.md) を参照する．
