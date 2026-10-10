# PUYO-274 nextgen の 3 手参考配置

## 原因と変更範囲

nextgen の `tactical_diagnostics` は `nextgen` 契約と探索診断を返すが，既存 renderer が読む最上位の `plan` を返していなかった．そのため `o` を ON にしても配置 preview は空だった．PUYO-273 に起因する回帰と断定する証拠はない．

`agents/nextgen_plan_preview.py` は採用候補の root を `n-turn-plan-v1` へ投影する．`nextgen_tactic_manager.py` の diagnostics に `plan`，`plan_id`，`replan_reason`，`plan_preview` を追加し，decision context ごとに一度だけ生成する．返す plan は複製し，表示側の変更を次の参照へ持ち越さない．reset 時は cache を破棄する．

## 表示と判断の契約

- 最大 3 手．公開されている現在組・NEXT・NEXT2 だけを使う．
- 複数 step の選択候補がある場合はその候補を使う．build 系の root-only 候補では，同じ root の既存探索代表軌跡を使う．root 不一致や軌跡欠損の場合は選択 root だけを表示する．候補・順位・戦術・探索 quota は変更しない．
- 新しい探索はしない．既存軌跡を最大 3 回の compact transition で再生し，配置セルと解決後盤面を生成する．描画の ON/OFF によって policy 側の処理は変わらない．
- `reference_only=true`，`continuation_guaranteed=false`，`execution_queue=false`．現在公開の色でも，2/3 手目は次の判断で変わり得る参考配置である．template の将来完成や sampled scenario の成立を保証しない．
- 盤面上部の非公開セルは既存共有探索と同じ公開盤面推定を使い，`board_complete=false` を保持する．未知のツモや simulator/隠れた queue は読み込まない．
- 未着弾攻撃があるときは root までで止める．response witness に含まれ得る途中のおじゃま落下を，単なる配置列から推測して復元しない．
- plan は `candidate_id`，`request_identity`，`public_snapshot_digest`，`root_action` を持つ．GUI 側は scheduler の採用 receipt と現在の実行 identity に照合してから表示する．worker が生成しただけの plan は採用済みと扱わない．GUI の採用判定・古い表示の抑止・非表示理由ラベルは cadence 側の関連 PR が担当する．

`plan_preview.status` は `available`（3 手），`partial`（1〜2 手），`unavailable`（0 手）．`reason` で公開 prefix 不足，探索代表欠損，root/色不一致，着弾待ち，無効な推定配置，推定終端を区別する．参考配置の番号付き輪郭と，操作中の組の現在位置に従う落下位置 ghost は別物である．

## 検証

専用テストは root/plan ID の一致，3 手の公開色，renderer の配置セル互換，candidate 不変，追加探索なし，cache と reset，JSON 化，代表欠損/root 不一致，公開ツモ境界，着弾時短縮，既存複数 step 候補優先を検証する．

```bash
.venv/bin/python -m pytest tests/test_nextgen_plan_preview.py tests/test_nextgen_tactic_manager.py -q
.venv/bin/python -m ruff check agents/nextgen_plan_preview.py agents/nextgen_tactic_manager.py tests/test_nextgen_plan_preview.py
```

2026-09-28 の結果は 33 件成功．軽量 32 件は 15.56 秒，親が別枠で許可した native seed 55/GTR 開幕 14 手テストは 2.25 秒で成功した．Ruff と `git diff --check` も成功．[公開 prefix の機械生成例](../benchmarks/puyo-274-nextgen-preview/public-prefix.json) に root-only 候補と生成された 3 step を保存した．同じ軽量 fixture の生成 100 回は中央値 0.571 ms/p95 0.669 ms だった．これは表示投影処理だけの参考値であり，GUI frame/input gate の結果ではない．

実行環境は Linux x86_64/CPython 3.12 の担当 worktree 専用 venv．親が生成した native wheel の source revision は `732ed3d-dirty`（親実行表の未追跡のみ，native/build/requirements-native 差分なし），wheel SHA-256 は `b1c2baf386a2dd4cc32faefcd508e9156fd452bc695a774b2b24c7a6237cfa39`．準備時には旧 root venv に native がなく既存 2 テストが import error になったが，その後専用 venv で両方の成功を確認した．

人間 GUI QA は未実施．親による関連 GUI PR との組合せ QA で，次を確認する．

1. launcher または引継ぎ記載 CLI で nextgen を起動し，採用中の plan ID と step 1 が diagnostics/実配置と一致することを確認する．
2. `o` で参考配置だけが切り替わり，現在組の drop ghost と判別できることを確認する．
3. 参考表示の注意と，短縮・非表示の理由を確認する．着弾や再判断で古い配置が残らないことを確認する．
4. 同条件の ON/OFF で判断結果が一致することを機械確認し，人間目視の結果を別途記録する．機械試験だけで人間 QA や PUYO-274 全体の A/C を完了としない．

## References

- [PUYO-274](https://shhchan.atlassian.net/browse/PUYO-274)
- [既存表示契約 PUYO-82](https://shhchan.atlassian.net/browse/PUYO-82)
- [GUI 統合契約 PUYO-188](https://shhchan.atlassian.net/browse/PUYO-188)
- [デスクトップ引継ぎ](puyo-274-desktop-handoff.md)
