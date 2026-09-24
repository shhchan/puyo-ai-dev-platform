# PUYO-250 公開 response 候補

`PublicResponseProvider(timing_profile, horizon=3)` を `SharedSearchBatchBuilder` の `response_provider` に渡す．`TimingProfile.digest`，schema，latency mode と request の一致を検証する．公開 current/NEXT/NEXT2 だけを使い，共有探索で補完した未来や runtime seed は読まない．旧 worker/planner と対戦エンジンは変更しない．

```python
from agents.nextgen_response_search import PublicResponseProvider

batch = SharedSearchBatchBuilder(
    backend, search_config,
    response_provider=PublicResponseProvider(timing_profile),
).build(request)
selected = batch.select("counter")  # 固定順位の選択のみ．再探索しない．
```

## 候補と境界

公開 prefix を幅優先で展開し，毎回「配置・連鎖解決 → score 差分と carry の攻撃変換 → incoming の相殺 → 到着済み packet の最大 30 個落下 → 次組」を評価する．設定した `max_ojama_drop` が 30 以外ならその公開ルールを使う．相殺準備は落下前の prefix 内，counter は最初の落下直後のちょうど一手で発火する候補である．総 incoming による 5 段制限はない．火力不足は `response_surplus` に負値として残し，mask 条件にしない．脅威がない場合も provider を呼び，短期攻撃候補を生成する．

落下の端数列は最大 20 通りの公開分布を列挙する．runtime RNG は読まず，端数列または落下する packet 集合が不明な witness は `partial/public_estimate` とする．14 行の公開盤面が完全で，落下列が一意の場合だけ幾何学的な既知 witness になる．隠れ行が unknown の場合も候補は残すが，既知 witness にしない．arrival が不明な packet は落下なし/全量到着の条件付き枝だけを調べ，`public_arrival_unknown` を記録する．複数の arrival 不明 packet の全ての部分集合を探索したとは主張しない．

構造的に配置不可能，既知の configured timeout，最初の落下後の全一手を検査して発火点が使えない場合は，それぞれ構造理由を残す．予算不足や horizon 内で見つからない場合は `not_found_within_budget` とし，不可能の証明にしない．

## 推定と実行結果の区別

arrival tick は落下可能になる時刻で，盤面への落下期限ではない．到着済みでも現在組の連鎖解決による相殺が先に行われる．候補の deadline はこの配置境界の推定区間である．`fire_start_*` は公開操作 cadence に基づく lock/発火開始見積り，`fire_end_*` は flash/drop 演出を含む resolution 見積りで，全て `public_estimate` とする．未知の cadence や measured completion の上限を有限値に置き換えない．

PUYO-251 の scheduler は実際の completion/activation/timeout，公開 snapshot digest，到達可能な root を再検証する．この探索は authoritative receipt を生成しない．counter の plan は次組を自動実行する指示ではなく，最初の落下を実際に観測した次 decision で再探索するための witness である．相手の将来の同時攻撃や未公開 packet は予測しないため，outgoing は現在公開された incoming に対する差分である．

`ResponseSearchResult.traces` は wire candidate の外に置く．plan/tactic ごとに最初の落下数・列分布，残 packet，発火時刻区間，counter 後の次回落下数・分布・残 packet・窒息を保持する．`first_drop_after_step` は 1 始まりの落下境界で，counter の最後の step がその次 decision である．`following_drops` が予算で途中までなら結果全体に `response_quota` が付く．`fatal_rate` は調べた次回落下分布の死亡割合で，生存確率の保証ではない．同じ plan に複数の条件付き証拠があれば全 trace を残し，候補の固定順位には保守的な低火力の代表を使う．

## 計算予算と検証

追加の配置/連鎖解決と落下を実行する直前に `ResponseBudget.consume()` を呼ぶ．失敗・窒息も node として数え，quota の再配分はしない．特徴計数は有効な配置結果の評価回数で，落下だけの処理を特徴計数へ加えない．選択後の追加探索はない．

```bash
.venv/bin/python -m unittest tests.test_nextgen_response_search tests.test_nextgen_shared_search tests.test_nextgen_public_snapshot tests.test_nextgen_contracts tests.test_realtime_versus -v
.venv/bin/python -m eval.nextgen_response_fixtures --output /tmp/puyo250-response.json
```

固定した 8 局面の候補 coverage と CPU/経過時間は [計測結果](../benchmarks/puyo-250-response-candidates.json) を参照する．相殺準備・火力不足・到着後の相殺・incoming 31/61/120 の落下後一手・未知の端数列・脅威なし短期攻撃を含み，candidate gap は 0 だった．Python の単一プロセスで測定し，各局面の response quota は 2,000，端数列だけ 4,000 nodes とした．この局面集合の候補検証は measured latency，対戦勝率，safe-build，G2 全体の合格を意味しない．
