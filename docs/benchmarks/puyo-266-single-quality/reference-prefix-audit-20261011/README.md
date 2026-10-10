# reference4519：公開 prefix の読取監査

製品変更は採用せず，単独品質 BLOCKED を維持する．分析 head は `21d6b16779480832412c838219c74f26e708b10d`．保存 raw／固定 gate／閾値／ID／分類は不変で，native 探索・新しい対局・GUI は起動していない．

公開 known3 の配置後は，4手目の色が未知でも操作到達性だけなら確定した占有盤面から判定できる．reference4519 の最初の選択代表 path 不達は，29手目 tick1474 の `[10,15,11,19]` の4手目19だった．その root10 の target支持6経路中4経路が公開 prefix 内で不達，次順位 root0 は6/6が同範囲で到達可能．これは全予測経路や長期生存の証明ではない．以前の31手目 root17 介入は known3 が通る一方，target5経路すべての4手目が不達であり，known3のみのフィルタを採らなかった結論と整合する．

保存 scenario evidence から既存順位を全決定で完全再構成した後，不達な **保存 selected_fire の証拠だけ**を unavailable に置き換え，同じ集約・順位を計算した．未保存の代替発火や quiet を補っていないため，製品フィルタの最終仕様とは扱わない．公開 prefix の control 展開と一意の既知配置 transition を数え，request 内だけで占有幾何 cache を共有した．

| reference pattern／repeat1 | 決定 | 除外 trajectory | 選択差 | 最大追加 node |
| --- | ---: | ---: | --- | ---: |
| 0 | 46 | 121 | なし | 914 |
| 2259 | 46 | 61 | 35手目 root6→4 | 747 |
| 4519 | 42 | 53 | 29手目10→0，30手目11→12，32手目8→11 | 838 |

平均追加は222〜307 nodes／決定．保存runの計算は0.48〜0.66秒，1決定最大45ms．既存 native expanded と追加の合計は134決定中最大465052で，600000未満だった．これらは観測値であり，未観測局面の費用上限や新しい固定 quota ではない．各決定の選択差・費用・原物 SHA を [summary.json](summary.json) に保存する．詳細な実行 script／結果は worktree の永続 `runs/puyo-266-reference-readonly-20261011/` に残す．

既存 PUYO-232／unit は target class を quiet より優先し，class支持数を terminal score より先に比較する．fatal_score は実死亡だけでなく発火後の構造 dead-end／trigger不達も含むため，負の終端評価を持つ target を単に落とす変更は安全バグ修正とは言えない．

今回のフィルタは，11連鎖に成功した正常2259の経路も変更する．未保存の別解が存在し得ること，quiet path が保存されていないこと，予算切れ unknown と native 集約済み support／代表 path の整合を解決できていない．既存600000の残余から事前課金する設計余地はあるが，改善・非回帰の根拠はまだ不足する．全候補フィルタを製品へ採用せず，29手目root0の実 counterfactual も親の GUI QA 枠解放確認まで保留する．
