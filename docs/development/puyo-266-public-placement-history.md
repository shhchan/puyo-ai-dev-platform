# PUYO-266 公開自配置履歴と生存推定

実装済みなのは実 lock の公開履歴，独立した公開推定 observer，後続配置が完全満杯列を横断する誤った witness の除外である．hidden 推定の worker 接続，時間を含む後続操作の保証，窒息修正完了を意味しない．

## 公開履歴の境界

`RealtimeVersusMatch.public_placement_history(player_id)` は，既存 `PublicSnapshot`/`PublicTimingHistory` と独立した `puyo.nextgen.public_placements.v1` を返す．既存 wire schema，actor feature，snapshot digest は変えない．

各 record は player，lock tick，event ID，実 lock の列・向きから求めた action，その tick の直前に公開されていた current pair だけを持つ．採用 intent/receipt からは生成しない．したがって stale/timeout/未 lock は記録を増やさず，fallback が実際に lock すればその実結果を記録する．axis_y，field の着地点，hidden board，未公開ツモ，garbage RNG は読まない．lock は消去完了でも最終着地点でもない．split pair の落下・clear・おじゃまは既存公開 lifecycle と別に照合する必要がある．

adapter の設置前に起きた lock は復元しない．`started_tick` は途中参加の開始点であり，0 という値だけで完全な初期盤面を証明しない．履歴の ID は同じ adapter/episode 内だけで有効で，reset は adapter 全体を作り直す．呼出側は別 episode の履歴を結合しない．player 別の取得と schema 検証で相手履歴の混入を拒否する．

## 採用した必要条件

既存有限 probe の後続候補から，spawn 列 2 と着地軸列の間に 14 セルすべてが埋まった列がある配置だけを除外する．軸の水平移動・side kick は 1 列ずつで，満杯列のどの高さにも軸が入れない．root の可到達性は引き続き scheduler の authoritative mask を使う．

最上段のセルだけで満杯と判定しない．14 行目は消去後に浮いたまま残る仕様であり，13 段の壁や合法な hidden 継続を一律禁止しない．これは定数個の occupancy 検査で，探索の追加や quota の拡張はない．placement/drop は従来どおり response 256 内の survival 128 に事前課金する．cutoff は fatal にしない．

## 不採用の control 探索案

公開 compact board と既知 pair だけで fresh spawn の geometry BFS を構成し，control-state 展開も同じ 128 枠に事前課金する試作を行った．placement/drop/control を別集計し，generator の round robin と root の初回 coverage は保った．しかし正常 GTR 123/28 で placement 23 + control 105 に達し，従来の正当な `[1,3,8]` が cutoff になった．NEXT 救済と実採用 receipt の回帰も失敗したため runtime へ採用していない．[棄却 patch と結果](../benchmarks/puyo-266-safe-build/sprint14-human-20261008/rejected-control-prototype.json)を保存した．

単独 witness の対象 pose だけなら正常 123 は 8 + 13 control states で証明できるが，全 root の同時探索へ単純追加すると共有予算を使い切る．加えて，fresh spawn/補間 0 の幾何探索は自然落下・floor kick を含む実時間の到達不能証明ではない．完全盤面を policy へ渡すことや，unknown/cutoff を死亡扱いして小消しへ切り替えることで代用しない．

## 次の最小設計と検証条件

1. 実 lock 履歴から推定した hidden を visible board とは別の sidecar に保持する．推定値，観測値，unknown を分離し，episode ID，公開入力 digest，最終 lock ID を bind する．未確定の着地点や receipt のみを確定情報に昇格しない．
2. 次の settled snapshot と visible 領域を照合する．clear/garbage/途中参加/欠損/不整合では，復元できないセルを unknown に戻す．private garbage RNG による着弾列を推測して確定しない．worker transport と replay/receipt に provenance を保存する．
3. shared/native 順位に沿う段階的な証明と公開 geometry cache を設計し，placement と control の合計を既存 128 内に保つ．どの候補を先に証明したかで quota 切れを fatal と誤認しない．正常 123/28 の有効な証明を維持してから scheduler 契約へ接続する．
4. seed 127/26 の root 3 を排除するだけでは不十分である．公開モデルには root 11 の `[11,0,3]` も残り，実 hidden が列 0 を塞いでいる．履歴推定と後続操作証明を同時に満たして，合法 4 連鎖 `[7,8,10]` の実 lock/clear まで検証する．55/123/124，126/128/132/135/144，3 定型，reset/stale/fallback/timeout/unknown/quota を再確認する．

正式 G2 の 30 seed × 2 repeat と残る品質条件，人間 GUI QA は未達であり，PUYO-266 は In Progress のまま扱う．

## 公開推定 observer（第一段階）

`public_board_inference()` は独立 opt-in observer である．空の Field を作る `reset()` の明示起点を，最初の step より前に受け取った場合だけ hidden 2 行を空と確定する．tick 0 や可視盤面が空というだけでは確定しない．遅い設置，episode/tick 欠測，実 lock/pair 不一致，複数の lock 高さ，clear/visible 不整合は unknown とし，unknown の wire に推測セルを含めない．reset は observer を破棄して起点を更新する．

推定器は public state，実 lock action/pair，公開 lifecycle だけを受け取る．settled な既知盤面から grounded lock の可能な高さを列挙し，lock 直後の visible と一致する結果を公開の重力/消去ルールで解決する．次の control snapshot と整合する一意の結果のみ確定する．clear 中やおじゃま animation 中は unknown．おじゃまは公開ルールで visible 行の空セルだけに配置されるため，表示が確定した結果と照合して hidden の保存を判断する．乱数や着弾予定位置を読まない．scratch Field は公開セルだけから構成し，元 match/simulator/private field は推定器へ渡さない．

sidecar は episode/player/tick，visible digest，last lock ID，known/unknown と hidden 2 行を持つ．既存 PublicSnapshot は hidden=None のままで，actor と native の入力は変えない．この段階では worker/request へは接続していない．`tests/test_nextgen_public_inference.py` が起点，欠測，clear/drop 整合と private 非干渉を検証する．保存済み GTR 123/132/135 と新規 human fixture の 133 判断を比較する offline script は `docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/public-inference-audit.py`．完全盤面は script の期待結果監査だけに使い，推定器の入力ではない．

## request への明示 bind（第二段階）

新規 request は `puyo.nextgen.request.v2` とし，独立した `inference` field を持つ．scheduler の prepare が identity/execution の digest に sidecar を結び付け，accept は返却された sidecar の厳密一致を確認する．既存 decode cache 本体は変更しない．phase worker はこの値を request に明示して渡す．

`known_inference()` は known，player，request tick，visible digest，identity/execution digest がすべて一致し，control 中の場合だけ値を返す．missing/unknown/mismatch は利用不能であり，従来の公開盤面経路へ戻す．履歴 producer の信頼境界を置き換える API ではない．sidecar の raw hidden セルは native 検索入力や actor feature に追加しない．

旧 `request.v1` は inference field を含めず，旧 wire と semantic digest の完全一致を保って読む．v1 に新 field を混入させる入力は拒否する．新 v2 は field の明示を必須とするが，値 None を許容する．専用 wire tests で v1/v2 の往復，nested digest，bind 不整合，native 入力一致，actor feature 非露出を検証する．
