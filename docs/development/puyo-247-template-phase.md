# PUYO-247 template phase controller

`agents.template_phase.TemplatePhaseController` は 1 対局・1 プレイヤーごとに作成する．catalog と専用選択 RNG は生成時に固定する．開始盤面の `MatchResult` を `start(result, decision_id=...)` に渡すと，fit score が低くても有効な定型を 1 つ選ぶ．同じ controller の `start` は 2 回呼べない．

実行器は `puyo_env.realtime_ai.advance_template_phase_runtime` を，操作判断の採用時と公開イベント更新後に呼ぶ．採用時には `activated_receipt`，組の安定した `piece_id`，試行ごとの `request_id`，採用戦術を渡す．fallback，timeout，stale，未採用の試行は枠を消費しない．同じ組を再探索しても `piece_id` が同じなら 1 回だけ消費する．`phase_snapshot()` は PUYO-244 `PhaseSnapshot` に投影し，`diagnostics()` は phase ID，終了理由，実消費数，待機中の解決を返す．

`build_template` の 14 回目は採用でき，採用直後に `limit` で終了する．次の組は定型を採用できない．対局開始後に設定を読み直さず，template ごとの `commit_turns` を使用する．毎操作可能盤面の matcher 結果で同じ variant／変換／色割当の完成を確認したら `completed`，不適合なら `no_compatible_candidate`，予算不足または fit 不明なら `search_unknown` で閉じる．通常構築へ切り替えた場合は `interrupted` で閉じる．

相殺／counter／本線発火／短期攻撃への切替時に現 phase を閉じる．公開 packet の全量相殺，または対象 packet の最初の落下後の**最初の自配置**が連鎖 resolution を迎えるまで，応答後の再選択を待つ．最初の自配置が不発なら，後続の発火をその counter の成功として扱わない．本線／短期攻撃は自分の連鎖 resolution を待つ．発火開始・配置・相手の resolution だけでは完了しない．完了後も `control` かつ脅威 packet がない状態まで待ち，最新の matcher 結果から fit が証明された候補だけを一度選ぶ．fit がない／不明なら自由構築にする．古い定型を自動復元しない．

この adapter は PUYO-251 の次世代 scheduler が呼び出す opt-in 接続点であり，既存 policy の action ID から戦術を推定しない．呼び出し側は対象 packet ID を公開 snapshot から渡し，matcher 結果をその時点の操作可能盤面で計算する．履歴を取りこぼさないよう `match.public_snapshot()` を最初の step 前に有効化する．
