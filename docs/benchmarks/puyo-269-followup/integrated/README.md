# PUYO-269／PUYO-273 合成 head の GUI 再測定

固定 source は `5bcdd1278cc70cee3f1edb396706d8d1552c68b3`．PUYO-273 確定 head `d77e3c140e0f00a8c408c09204683be6b710b717` を PUYO-269 branch へ通常 merge した合成結果である．`eval.puyo_269_gui_probe` の変更後経路を使用し，`--reference-269`／`--reference-mask`／`--geometric-reference` は指定していない．native wheel SHA-256 は `79b7d6f43305a27d169722ca014cfba09cd0e97932d49284896de63231bdb0d2`．host・DISPLAY・seed 55・policy/config・各 frame 予算は [最初の A/B](../README.md) と同じ．各条件を別 process で直列実行した．全 frame で match が活動中である．

```bash
DISPLAY=:0 /tmp/puyo271-final-venv/bin/python -m eval.puyo_269_gui_probe \
  --output /tmp/puyo269-integrated-one.json
# 軽量：--policy first
# 両側：--opponent nextgen_tactic_manager --frames 360
# human：--opponent human（合成 50 ms key edge）
python docs/benchmarks/puyo-269-followup/aggregate.py
```

`raw/` に 4 run の gzip 個票，`summary.json` に展開後 SHA-256・source/native/host/config・p50/p95/p99/max・event／lock／worker 資源を保存した．

## 結果

事前 gate は frame と input schedule の p95 ≤ 25 ms／p99 ≤ 50 ms．次表は p95/p99，単位 ms．

| 条件 | frame | input schedule | event 投入→状態 | event 投入→描画 | catch-up p95/max |
| --- | --- | --- | --- | --- | --- |
| 軽量，600 frame | 19.3/22.3 | 15.7/16.6 | 17.1/32.6 | 19.5/34.7 | 1/3 tick |
| 片側 nextgen，600 frame | 30.9/70.9 | 32.6/71.3 | 38.6/94.3 | 65.2/154.8 | 2/10 tick |
| 両側 nextgen，360 frame | 71.6/97.3 | 63.2/92.7 | 72.7/103.5 | 135.0/147.7 | 4/10 tick |
| 1P nextgen／2P human，600 frame | 31.1/64.3 | 36.0/54.7 | 41.1/63.8 | 58.0/89.2 | 2/5 tick |

human の UI/simulator held 不一致は 0 tick，UI 解放済みかつ新規 press なしの余分な横 fired は LEFT/RIGHT とも 0 回．回転 emitted/fired は左右とも 22/22．event は 221 件投入/220 件処理で，ID 重複・内部欠落は 0，未処理 1 件は停止時の末尾 queue 残．実人間が意図した配置を操作できるかは未確認である．

lock receipt は event の実 (x,y,rotation) と，直前に active plan があった場合の予定 root を比較した．片側は AI player_0 **6/6**，両側は AI player_0 **4/4**・player_1 **4/4**，human 条件は AI player_0 **5/5** が一致した．軽量は policy 2 者で 21/21．片側の全 17 lock には random player_1 の 11 件が含まれるため，AI の 6/6 と区別する．human player_1 の 11 lock には AI の予定 root を仮定せず，比較外とした．この短い trace の一致は全状態の保証ではない．

片側には `activation_unreachable_fallback` が 1 件あり，`fallback_actions=1`／`unreachable_plans=1`，stale 4，deadline miss 0．この run の最後の decision reason から判明した事象で，決定的な再現 fixture や planner 境界の原因は未確定．他 3 条件の fallback は 0．全条件で scheduler error は空，worker cleanup は成功した．片側の mask p95 は 51.2 ms，両側 32.6 ms，scheduler accept p95 は片側 34.9 ms，両側 21.1 ms，finish p95 は 9.7/8.2 ms．両側 update/render p95 は 52.3/23.0 ms，親 sampled CPU は約 0.67 core，worker 各約 0.91–0.95 core，最大 RSS は親 267 MiB，worker 各約 339–341 MiB．60 frame 間隔の CPU sample で，生涯平均ではない．

初回 PUYO-269 A/B の旧側 source `7587d0b` とは planner・active input 検証・関連 upstream が異なる．初回片側 frame 26.4/65.6 ms，両側 66.6/126.4 ms を本合成 head の性能差として直接帰属しない．同じ source 内の再現性をもつ A/B も今回実施していない．**片側・両側・human の固定 frame/input gate は未達**で，PR は draft，Jira は In Progress のままにする．
