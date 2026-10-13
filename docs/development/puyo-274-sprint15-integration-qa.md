# PUYO-274 Sprint 15 統合 QA

この文書は v1.7.3 の最上段で PUYO-275，276，277，266，280 を組み合わせて確認する手順と判定境界である．この手順の記載や CI の単体テスト成功だけでは統合 QA は完了しない．以下の過去の FAIL／BLOCKED は当時の判定記録であり，Sprint 15 の最終受入は末尾の 2026-10-13 更新を優先する．PUYO-278 の release 判断は別チケットで行う．10 連鎖能力と窒息判定はとことんぷよぷよ相当の単独環境で測る．対戦相手のおじゃまからの回復改善は将来のモデル改良課題とし，単独品質の達成として扱わない．

## 起点と証拠

統合前に作業ブランチの起点，取り込む各 PR の確定 head，差分，Jira の受け入れ状態を記録する．順序は 275 → 276 → 277 → 266 → 274 → 278 とし，最下段の base は `integration/puyo-228-v1-7-3` とする．PUYO-277 の response ranking を PUYO-266 の正式 120 run に含める．親セッションが先行 PR を確定 head で取り込み，PUYO-274 の作業ブランチを更新する．取り込み後に `git status --short` が空であることと `git rev-parse HEAD` を記録する．先行 PR の変更を PUYO-274 の独自実績として重複計上しない．

| 条件 | 確認する記録 |
| --- | --- |
| PUYO-275 | `random` と `esports_tsu` の選択，pattern ID，原本 SHA-256，色 mapping，公開 current／NEXT／NEXT2，replay 決定性． |
| PUYO-266 | 事前登録 30 pattern × 2 repeat × 2 policy の 120 run，46 resolution または game over，平均最大実連鎖 ≥ 10，理由のない premature 0，回避可能窒息 0，repeat と integrity． |
| PUYO-277 | 固定 4 pattern × 7 case × 攻撃あり／なしの 56 条件と，`preserve_mainline`／`post_arrival_recovery` の追加 16 条件を別 manifest で確認．receipt／実 lock／解決／相殺・着弾・副砲の観測，replay hash，未実行・unknown・例外を記録． |
| PUYO-276 | 保存修正後の合成入力 OFF／ON 各 2 repeat，1P nextgen／2P human，速度 1.0 の実画面操作，通常・途中終了，保存 bundle の別場所での検証，frame／input 分布． |

原本 `haipuyo.txt` は repository と QA bundle に含めない．照合済み原本はローカルの `/home/sion2000114/.cache/puyo-s15/haipuyo.txt` にあり，期待 SHA-256 は `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb` である．別 host では各自が原本を入手し，この checksum を確認する．元の random seed 59／127 対局は replay が残っていないため，同一対局の再現成功とは主張しない．旧 raw と新しい `esports_tsu` の pattern ID は別の証拠として扱う．

## 統合後の機械検証

以下は先行実装をすべて取り込んだ **確定 head** の repository root で行う．基礎 4 module を明示して実行する．対象の test ファイルがない場合は止め，0 test の成功表示で代用しない．この単体テストは外部原本を伴う正式 gate の代用ではない．

```bash
git status --short
git rev-parse HEAD
test -f tests/test_tsumo_provider.py
test -f tests/test_qa_session.py
test -f tests/test_nextgen_single_quality_gate.py
test -f tests/test_nextgen_attack_response_gate.py
sha256sum /home/sion2000114/.cache/puyo-s15/haipuyo.txt
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m unittest \
  tests.test_tsumo_provider tests.test_qa_session \
  tests.test_nextgen_single_quality_gate tests.test_nextgen_attack_response_gate -v
```

`tests.test_tsumo_provider` の原本往復テストは `PUYO_TSUMO_SOURCE` が未設定なら skip される．`tests.test_nextgen_attack_response_gate` の外部原本を要する runtime 2 件も source がなければ skip される．原本付きでそれらを走らせる場合は次を用い，結果の skipped 数まで保存する．GitHub CI では原本を配布しないので，原本依存部分の skip を正式 gate PASS としない．

```bash
PUYO_TSUMO_SOURCE=/home/sion2000114/.cache/puyo-s15/haipuyo.txt \
PUYO277_SOURCE=/home/sion2000114/.cache/puyo-s15/haipuyo.txt \
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m unittest \
  tests.test_tsumo_provider tests.test_nextgen_attack_response_gate -v
```

PUYO-266 の 120 run と PUYO-277 の元 56＋追加 16 条件は各チケットの正式手順と親セッションの CPU 排他枠で行う．CI の単体テストで再実行せず，[単独品質 gate](puyo-266-sprint15-single-gate.md)と[攻撃対応 gate](puyo-277-attack-response-gate.md)にある manifest／report／replay／checksum／summary の identity と判定を照合する．PUYO-277 の初回 56 条件は 47 PASS／5 FAIL／回避不能除外 4 として保持し，修正後の 56 条件と追加 16 条件は別々の manifest／出力で照合する．初回 FAIL を新結果へ読み替えない．欠損，未分類，失敗を成功母集団から除かず，その状態を PUYO-274 の統合結果へ記録する．

## 人間の実画面確認

GUI と native の排他枠が空いた状態で行う．[QA session 契約](puyo-276-qa-session.md)に従い，1P `nextgen_tactic_manager`，2P `human`，速度 `1.0` とし，`random` と `esports_tsu` を別対局で確認する．`esports_tsu` は検証済み原本と事前登録 ID の 1 つを明示する．各対局の source／config／native identity，seed または pattern ID，画面速度，開始・終了時刻，session path と人間の観測を保存する．原本 path を変えても checksum は一致させる．

```bash
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 \
  --seed 127 --max-ticks 10000 --qa-auto-save \
  --qa-save-root runs/gui-qa-sessions
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 \
  --seed 127 --max-ticks 10000 --tsumo-mode esports_tsu \
  --tsumo-source /home/sion2000114/.cache/puyo-s15/haipuyo.txt --tsumo-pattern-id 0 \
  --qa-auto-save --qa-save-root runs/gui-qa-sessions
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a nextgen_tactic_manager --policy-b human --speed 1.0 \
  --seed 127 --max-ticks 10000
```

2P で soft drop を押し続けながら左／右と回転を加え，意図した列・向きに実 lock するか確認する．既定では `W`／`S`／`Enter`／`Space` が soft drop，`A`／`D` または矢印が左右，`Q`／`E` または上下矢印が回転である．変更済みの割当は `F1` で確認する．`O` を 2 回押し，N=3 の将来配置 preview が消えて再表示されることを，現在組の落下位置 ghost と区別して確認する．表示がない場面は plan／receipt status を記録し，無表示を即座に表示回帰と断定しない．通常終了と `Esc` による途中終了の bundle を別々に保存する．保存 OFF では session dir が作られないことを確認する．

表示された `<session-id>` に置き換え，bundle をコピーしてから検証する．成功時は終了コード 0 と `QA session valid:` を期待する．`esports_tsu` の移送先検証には同じ SHA-256 の原本を `--tsumo-source` で渡す．`random` の bundle ではこの引数を省く．`manifest.json` の checksum，source/native/config，replay 各 tick hash・最終 hash，result の `interrupted` と実 lock を照合する．不一致は失敗として保存する．

```bash
mkdir -p /tmp/puyo-274-qa-review
cp -a runs/gui-qa-sessions/<session-id> /tmp/puyo-274-qa-review/
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.qa_session \
  /tmp/puyo-274-qa-review/<session-id> \
  --tsumo-source /home/sion2000114/.cache/puyo-s15/haipuyo.txt
```

frame と input schedule は p95 ≤ 25 ms／p99 ≤ 50 ms を維持して観測し，保存 ON／OFF，random／`esports_tsu`，実人間入力を混ぜず個別に記録する．PUYO-276 の旧 1000 tick 合成入力では保存 ON の input schedule p99 が 57.82 ms で未達だった．保存処理の単一コピー化後，OFF／ON 各 2 repeat の frame p99 は 27／32／25／31 ms，input schedule p99 は 34.51／48.46／25.97／25.51 ms で固定 gate 内だった．[4 run の raw と旧失敗 raw](../benchmarks/puyo-276-qa-session/README.md)をともに保持する．この 4 run は実画面の人間 QA でも広い環境の SLA 証明でもなく，ON／OFF の因果差も確定しない．実画面の人間 QA は下記の 3 bundle で確認済みである．厳密な frame 25 ms 安定化の新規改善は Sprint 16 の PUYO-279 が所有するが，未達測定を隠して統合 PASS とはしない．

## 2026-10-11 人間実画面 QA の結果

依頼者は統合 head `361398981f3a9083dbf63b79a9993f8548ec0fb7`（dirty=false），native SHA-256 `35735a1a6bce0a45a64a46453cd4302f34099fb626ef4fc226a61c32bb2c52ca`，1P `nextgen_tactic_manager`／2P `human`／速度 1.0 で対局した．2P の左右移動・回転・soft drop が意図どおりで，`O` を 2 回押すと先読み表示が消えて再表示されると報告した．

| mode／終了 | seed／pattern | session ID | tick | frame p95／p99 | コピー先 validator |
| --- | --- | --- | ---: | ---: | --- |
| random／通常終了 | seed 1127 | `f54e176489a148d78515ea7a31cf533b` | 2962 | 18／37 ms | exit 0，`QA session valid:` |
| esports_tsu／通常終了 | pattern 0 | `abb6fc23d5984d24821b3c8ad546f485` | 2980 | 17／35 ms | exit 0，`QA session valid:` |
| random／`Esc` 中断 | seed 127 | `ae8b6b865c7a4c3b9742935e455df51c` | 4641 | 17／30 ms | exit 0，`QA session valid:` |

各 bundle は worktree の `runs/gui-qa-sessions/<session ID>/` から `/tmp/puyo-274-qa-review/` へコピーした．validator は同梱 replay の入力，全 tick／最終 hash と manifest の source／config／native・ファイル checksum を検査した．`Esc` bundle の `result.interrupted` は `true`．esports_tsu 原本 SHA-256 は `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．3 bundle の `replay.json` SHA-256 は順に `cfb09fb6c68586f4fa1bcca2975994552775f95fa0bdff42d2b5a04a173d0c85`，`3d289f6e2437e499eaf433068e07c40bde42e3a20e8e344f3a0a94a25adec096`，`e779380a5c8ae26fc7a13d4efb9955cbfd3eae3f7b304619af4d68a2d85699d3`．原物 bundle は Git 非収録の `runs/` に保持する．

保存 OFF は同じ統合 head の dummy GUI を `--qa-auto-save` なし・`--max-frames 30` で起動し，exit 0 と指定 QA root の非生成を確認した．既存の合成入力 OFF／ON 各 2 repeat は frame／input schedule gate を満たす．この 3 実人間対局の frame p95／p99 は 25／50 ms 以内だが，実人間入力の input schedule 分位点は bundle に存在しないため合成入力結果と混同しない．PUYO-276 は Jira Complete．PUYO-266 の正式品質 gate は別件として BLOCKED のまま．

## 完了判定

CI の 4 suite と既存 boundary が成功し，先行 PR の確定 head で PUYO-266／277 の正式結果が受け入れられ，PUYO-276 の人間 QA が bundle と操作記録付きで確認でき，PUYO-275 の provenance と replay が整合した場合だけ統合結果を受け入れる．判定表には各 artifact path，SHA-256，実行 head，PASS／FAIL／BLOCKED と理由を残す．未達が残る間は PUYO-274 を In Progress に置き，PUYO-278 の release 判定を確定しない．

## 2026-10-11 統合 head の暫定判定（正式 v4 前）

親が確定 PUYO-266 head `21d6b16779480832412c838219c74f26e708b10d` を通常 merge した直後の clean head は `075c1e392dfe789f138476fd620a027937999113`．最下段起点は `7757f2312f34a1d79593f43ebd4f41921c61f0a6`，積層順は 275 → 276 → 277 → 266 → 274 → 278．PUYO-274 の追加差分は CI trigger／検証対象と本記録であり，先行 4 PR の実装・正式 gate を本件の独自成果に数えない．

| 対象 | 証拠と判定 |
| --- | --- |
| PUYO-275 | 最新 head `2e338b6`，PR #183，Jira Complete．原本は上記外部 path で SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb` を再照合．provider／replay の boundary は下記の統合テストにも含めた．最終 head は初回 `51d53a8` に Ruff 0.16.0 の import 修正を積んだもの． |
| PUYO-276 | 最新 head `6d71dc4`，PR #184，Jira Complete．合成入力 OFF／ON 各 2 repeat，実人間対局の通常終了 2 bundle と `Esc` 中断 1 bundle，コピー先 validator，保存 OFF を確認．frame p95／p99 は 3 対局とも 25／50 ms 以内．上記の実人間入力 schedule の計測限界は保持する．PASS． |
| PUYO-277 | 最新 head `555880c`，PR #185，Jira Complete．正式実行 head `27b9c6e` の 56 条件は PASS 52／回避不能除外 4／FAIL 0，追加 16 条件は全 PASS．72 replay の event／tick／最終 hash を再照合し，288 decisions の quota／実 lock／deadline 異常 0．[監査 JSON](../benchmarks/puyo-277-attack-response/v3-final-audit.json) SHA-256 `4da6540898fcb5d9f9cdc9b73bf2374c69e2bdc49893c8c57c5e57a6b5ee42ee`． |
| PUYO-266 | 当時の head `b5c9881`，draft PR #186，Jira In Progress．正式 v3 は 14／120 final で中断し，nextgen pattern6779 に理由のない小発火と窒息 unknown，reference4519 に窒息 unknown．[保存した v3 summary](../benchmarks/puyo-266-single-quality/sprint15-blocked-20261011/formal-v3/summary.json) SHA-256 `2609389f7e8a876aa010c8d65d7b039a6d5ced5360a3680ebd2c03cf50adf3a7`．修正後 targeted 4 run で nextgen6779 は両 repeat とも 46 手・最大 10 連鎖・不当小発火 0 となったが，reference4519 の両 repeat は 42 手で死亡し，窒息分類 unknown のまま．[targeted summary](../benchmarks/puyo-266-single-quality/sprint15-blocked-20261011/targeted/summary.json) SHA-256 `cd5d3fd43c088cef1df12f71518040e48d8a64e68d520e510240bd40f03952ae`．追加の公開 prefix 診断では 29 手目だけの代替 root0 は 34 手死亡・最大 1 連鎖，毎決定の診断フィルタは 34 手死亡・最大 0 連鎖だった．[不採用の診断 summary](../benchmarks/puyo-266-single-quality/rejected-prefix-filter-20261011/summary.json) SHA-256 `d6bc048a0b011049e4e92001912ece44aa128b074b0eaa6224b57f02777e9889`．製品フィルタは不採用，この時点では正式 v4 未実施のため BLOCKED． |
| PUYO-274 | draft PR #187．機械テストと人間 GUI QA は PASS．PUYO-266 の正式単独品質 gate 未達により，統合品質は BLOCKED，Jira In Progress． |

外部原本を `PUYO_TSUMO_SOURCE`／`PUYO277_SOURCE` に指定して，基礎 4 suite と追加の quiet survival／到達可能 mask／攻撃選択／戦術境界の計 8 suite，102 tests を実行し，skip 0 で成功した．親の統合 head で CI と同じ Ruff 0.16.0 の対象 16 ファイル，shared search の `--select F`，`git diff --check` も成功した．その後の PUYO-266 取り込みは証拠ファイルのみで製品コードを変更していない．`agents/nextgen_survival.py`，`tests/test_nextgen_quiet_survival.py`，固定公開 fixture を native-extension workflow の pull_request／push path と lint／test に追加した．初回 PR #187 の remote CI は Ruff の I001／B023 で失敗し，各担当 branch の限定修正を通常 merge した．最終 head `361398981f3a9083dbf63b79a9993f8548ec0fb7` の remote CI 2 件は SUCCESS と確認した．この機械検証は重い正式評価と実人間 GUI QA の代用にはならない．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s15-274
PUYO_TSUMO_SOURCE=/home/sion2000114/.cache/puyo-s15/haipuyo.txt \
PUYO277_SOURCE=/home/sion2000114/.cache/puyo-s15/haipuyo.txt \
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m unittest \
  tests.test_tsumo_provider tests.test_qa_session \
  tests.test_nextgen_single_quality_gate tests.test_nextgen_quiet_survival \
  tests.test_deep_chain_execution_mask tests.test_nextgen_attack_response_gate \
  tests.test_nextgen_response_search tests.test_nextgen_tactic_manager
```

## 2026-10-11 正式 v4 単独品質 gate の統合判定

PUYO-266 の正式 v4 は source commit `b5c988140462beed05a3b5d913c72a066a8b6290` と宣言済み manifest SHA-256 `054e2dd9379b667d186c2ea0e2a7f82bcc11684b4fe2f2c639591cb0a74d38a0` で 30 pattern × 2 repeat × 2 policy の 120／120 run を完走した．親セッションは finalize と全 replay verify が exit 0 で一致し，source 変更と worker failure が 0 であることを確認した．[Git 配布用の manifest／report／選択 raw と SHA](../benchmarks/puyo-266-single-quality/sprint15-formal-v4/README.md)を照合済み．全 120 raw のローカル原物は `/home/sion2000114/workspaces/dev/puyo-s15-266/runs/puyo-266-formal-v4/` にあり，`report.json` ファイル自体の SHA-256 は `f11c56c54fff0d2253502f38bc9377d042467852de30d2297c2dbfd94a7e1632`．

| policy | 完了数 | repeat1 の平均最大実連鎖 | 小発火 | 死亡／窒息分類 | repeat／integrity | 判定 |
| --- | ---: | ---: | --- | --- | --- | --- |
| `nextgen_tactic_manager` | 60／60 | 9.6667（目標 ≥ 10） | 必要生存 10，不当 2，unknown 8（両 repeat 合計） | 死亡 0，回避可能窒息 0，unknown 窒息 0 | 不一致 0，欠損 0 | FAIL：平均連鎖・不当小発火が未達，unknown 小発火も未分類． |
| `deep_chain_builder` | 60／60 | 9.1 | 必要生存 4，不当 0，unknown 0（両 repeat 合計） | 死亡 10，回避可能窒息 0，unknown 窒息 10 | 不一致 0，欠損 0 | FAIL：比較対象の平均連鎖未達と窒息未分類． |

run 完走と replay integrity の成功を単独品質 PASS としない．PUYO-266 は `single_quality_status=FAIL`，`G2=BLOCKED`，`long_training_allowed=false` であり，PUYO-274 の統合品質も BLOCKED，Jira In Progress，PR #187 は draft のままとする．PUYO-275／276／277 と既存の人間 GUI QA の受入結果は維持する．次回の品質改善・再評価は PUYO-266 側で行い，この文書は確定した artifact identity と最新結果を追記して判定し直す．

## 2026-10-12 発火目標と 60 手の追加比較

PUYO-266 head `ba1c9f981827db296c6ea16f0089b16932477667` を #187 の base から通常 merge し，PUYO-274 の clean head は `54333c44575bcc5d42b7539f737b4e2397812947` となった．この追加比較は正式 v4 の 46 手・閾値・raw を変更していない．[HTML レポート](../benchmarks/puyo-266-fire-goal-v3/index.html)と manifest SHA-256 `3554f9184ec8098f7a4520743c4f3872de59eaec723e1a5331a525e6851dcbd0` を確認した．30 pattern × 2 repeat × 2 発火目標の 120／120 run と全 tick replay は成功し，欠損・worker failure・repeat 不一致は 0．

| 目標 | 実 10 連鎖以上 | 実 95,000 点以上 | 平均最大実連鎖 | 窒息 |
| --- | ---: | ---: | ---: | ---: |
| 10 連鎖 | 28／30 | 0／30 | 10.03 | 0／30 |
| 95,000 点 | 5／30 | 3／30 | 4.27 | 16／30 |

旧 46 手で低連鎖だった 11299 は 49 手目に実 11 連鎖へ到達したが，18078 と 42936 は 60 手でも最大 3 連鎖．旧「不当」小発火の 29377 は 1 連鎖の後に 11 連鎖へ到達している．新評価は最初の実 10 連鎖で停止するため，旧平均最大実連鎖と直接比較しない．95,000 点目標は一部で 13～14 連鎖を実現したものの，現行の生存判断との併用で窒息が増えた．正式 v4 の品質 A/C は未達のままで，PUYO-266 と本件の Jira は In Progress，統合品質は BLOCKED，release 判断は保留する．

新しい stack head で原本付きの統合 8 suite を再実行し，103 tests・skip 0 で成功した．既存 3 件の実人間 GUI QA は旧 head での証拠であり，新しい 95,000 点設定での GUI 動作保証に読み替えない．#187 の required CI はこの head の結果を別途 read-back する．

## 2026-10-13 Sprint 15 の統合受入

依頼者は，PUYO-266／280 の 60 手・30 配牌・2 repeat 正式第 6 回で，10 連鎖目標の平均最大実連鎖 **10.633** を確認し，数件の窒息・小発火を許容して合格とした．旧厳格 gate の `FAIL` と raw は保存する．許容した残差は配牌 62726 の窒息 **1／30** と不当小発火 **2／60** である．全 120 対局の欠損・worker 失敗・repeat 不一致・実 lock／全 tick replay 不一致は **0**．95,000 点目標は補助評価で，実 95,000 点到達 **6／30**，窒息 **5／30** を開示する．

PUYO-275 の配ぷよ来歴，PUYO-276 の人間実画面 QA 3 bundle と保存 OFF，PUYO-277 の攻撃対応 gate は既に受入済みである．PUYO-280 の確定 head を本 PR に通常 merge し，原本付き境界テストと CI を再確認した上で，Sprint 15 の統合 QA を **PASS** とする．人間実画面 QA は旧製品 head の結果であり，新 head の全操作・全配牌への保証には拡張しない．`nextgen_tactic_manager` の単独本線と bounded 対戦 gate を受け入れるが，旧 legacy G2 の失敗を消したり，本学習の無条件開始を宣言したりしない．release PR／tag の実行は人間による stack review／merge 後の別段階である．

最終の #190 head は，凍結済み第 6 回 report と 120 raw の SHA-256 を照合する `acceptance` コマンドを備える．[受入結果 JSON](../benchmarks/puyo-280-search-tuning/formal-v6-acceptance.json) は Sprint 15 `PASS`，旧厳格判定 `FAIL` を別欄で示す．この追加後の結合 head で原本付き 8 suite・**111 tests，skip 0**，Ruff，`git diff --check` が成功した．
