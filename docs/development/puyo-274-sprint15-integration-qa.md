# PUYO-274 Sprint 15 統合 QA

この文書は v1.7.3 の最上段で PUYO-275，276，277，266 を組み合わせて確認する手順と判定境界である．この手順の記載や CI の単体テスト成功だけでは統合 QA は完了しない．PUYO-266／277 の正式 gate，PUYO-276 の人間による実画面確認が揃うまで PUYO-274 は In Progress に置く．PUYO-278 の release 判断は別チケットで行う．

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

frame と input schedule は p95 ≤ 25 ms／p99 ≤ 50 ms を維持して観測し，保存 ON／OFF，random／`esports_tsu`，実人間入力を混ぜず個別に記録する．PUYO-276 の旧 1000 tick 合成入力では保存 ON の input schedule p99 が 57.82 ms で未達だった．保存処理の単一コピー化後，OFF／ON 各 2 repeat の frame p99 は 27／32／25／31 ms，input schedule p99 は 34.51／48.46／25.97／25.51 ms で固定 gate 内だった．[4 run の raw と旧失敗 raw](../benchmarks/puyo-276-qa-session/README.md)をともに保持する．この 4 run は実画面の人間 QA でも広い環境の SLA 証明でもなく，ON／OFF の因果差も確定しない．実画面の人間 QA は未了である．厳密な frame 25 ms 安定化の新規改善は Sprint 16 の PUYO-279 が所有するが，未達測定を隠して統合 PASS とはしない．

## 完了判定

CI の 4 suite と既存 boundary が成功し，先行 PR の確定 head で PUYO-266／277 の正式結果が受け入れられ，PUYO-276 の人間 QA が bundle と操作記録付きで確認でき，PUYO-275 の provenance と replay が整合した場合だけ統合結果を受け入れる．判定表には各 artifact path，SHA-256，実行 head，PASS／FAIL／BLOCKED と理由を残す．未達が残る間は PUYO-274 を In Progress に置き，PUYO-278 の release 判定を確定しない．

## 2026-10-11 統合 head の暫定判定

親が確定 PUYO-266 head `21d6b16779480832412c838219c74f26e708b10d` を通常 merge した直後の clean head は `075c1e392dfe789f138476fd620a027937999113`．最下段起点は `7757f2312f34a1d79593f43ebd4f41921c61f0a6`，積層順は 275 → 276 → 277 → 266 → 274 → 278．PUYO-274 の追加差分は CI trigger／検証対象と本記録であり，先行 4 PR の実装・正式 gate を本件の独自成果に数えない．

| 対象 | 証拠と判定 |
| --- | --- |
| PUYO-275 | `51d53a8`，PR #183，Jira Complete．原本は上記外部 path で SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb` を再照合．provider／replay の boundary は下記の統合テストにも含めた． |
| PUYO-276 | `30405d8`，PR #184，Jira In Progress．合成入力 OFF／ON 各 2 repeat の結果は上記のとおり．最終統合 head に対する人間の実画面操作，通常／途中終了の bundle，コピー先での検証，実 frame／input 分布は未取得．BLOCKED． |
| PUYO-277 | `06c0e1a`，PR #185，Jira Complete．正式実行 head `27b9c6e` の 56 条件は PASS 52／回避不能除外 4／FAIL 0，追加 16 条件は全 PASS．72 replay の event／tick／最終 hash を再照合し，288 decisions の quota／実 lock／deadline 異常 0．[監査 JSON](../benchmarks/puyo-277-attack-response/v3-final-audit.json) SHA-256 `4da6540898fcb5d9f9cdc9b73bf2374c69e2bdc49893c8c57c5e57a6b5ee42ee`． |
| PUYO-266 | `21d6b16`，draft PR #186，Jira In Progress．正式 v3 は 14／120 final で中断し，nextgen pattern6779 に理由のない小発火と窒息 unknown，reference4519 に窒息 unknown．[保存した v3 summary](../benchmarks/puyo-266-single-quality/sprint15-blocked-20261011/formal-v3/summary.json) SHA-256 `2609389f7e8a876aa010c8d65d7b039a6d5ced5360a3680ebd2c03cf50adf3a7`．修正後 targeted 4 run で nextgen6779 は両 repeat とも 46 手・最大 10 連鎖・不当小発火 0 となったが，reference4519 の両 repeat は 42 手で死亡し，窒息分類 unknown のまま．[targeted summary](../benchmarks/puyo-266-single-quality/sprint15-blocked-20261011/targeted/summary.json) SHA-256 `cd5d3fd43c088cef1df12f71518040e48d8a64e68d520e510240bd40f03952ae`．正式 v4 は未実施で，全 120 件を PASS としない．BLOCKED． |
| PUYO-274 | 本統合の機械テストは PASS．上記 266／276 の受入未達により統合品質は BLOCKED，Jira In Progress． |

外部原本を指定して，基礎 4 suite と追加の quiet survival／到達可能 mask／攻撃選択／戦術境界の計 8 suite，102 tests を実行し，skip 0 で成功した．対象 Python の Ruff check と shared search の `--select F`，`git diff --check` も成功．手元の Ruff は 0.15.21，CI は 0.16.0 を導入するので，remote CI の結果は別に確認する．`agents/nextgen_survival.py`，`tests/test_nextgen_quiet_survival.py`，固定公開 fixture を native-extension workflow の pull_request／push path と lint／test に追加した．この機械検証は重い正式評価と実人間 GUI QA の代用にはならない．

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
