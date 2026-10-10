# 公開 prefix フィルタの隔離診断：不採用

reference4519 の品質を改善しなかったため，製品修正を採用しない．単独品質 BLOCKED／Jira In Progress／draft PR #186 を維持し，正式 v4 と追加 repeat は開始していない．原本・事前登録 ID・閾値・分類・既存 raw は不変．

測定 source は `6baef386f37a7303b121d4275b05c48c9cf976d8`．親が割り当てた短い CPU 排他枠内で，各 thread1・逐次実行した．本証拠保存 commit は測定 source と区別する．native binary と設定は `manifest.json` に固定し，worker 前後の source／build／clean を照合した．原本配ぷよは同梱しない．

## 1 決定だけの介入

以前の保存 reference4519 を tick1474 まで入力 replay し，29手目 root10 を，公開 known3＋色非依存4手目が到達可能な次候補 root0 へ置換した．実 lock は x0／UP，その後は元の reference policy を継続．結果は34手死亡・最大1連鎖で，元の42手・最大2より悪化した．全 tick replay hash は一致した．

継続31手目では `[11,11,9,21]` の4手目21が，32手目では `[11,9,15,19]` の3手目15が不達だった．単発の介入だけでは毎決定の検証を否定できないため，次の隔離診断を実施した．raw は `root0-counterfactual.json.gz`，公開継続監査は `continuation-public-audit.json`．

## 毎決定の証拠フィルタ

製品ソースを変更せず，診断プロセス内だけで SelectPlacementStep の直前に以下を適用した．

- 公開 adapter の root state と known3 のみから，保存された selected-fire path の先頭3配置と色非依存の4手目操作到達性を検証する．
- 不達な selected-fire 証拠だけを当該 scenario の unavailable として再集約する．元の class優先・数値順位式を使い，変更後の代表 fire もその証拠に揃える．
- 未保存の別解を死亡と断定しない．quiet の保存 path が不足した場合は診断上の root-only plan と明示し，安全な将来継続を捏造しない．この不確実性は各 decision の raw に記録する．
- control 展開と一意の既知配置 transition は実行前に課金し，native expanded と合計して既存600000以下に抑える．予算切れは診断 failure にして，成功へ読み替えない．

| 固定条件 | 結果 | 小発火 | 窒息 |
| --- | --- | --- | --- |
| reference4519／filter | 34手・最大0連鎖 | 0 | unknown |
| reference2259／filter | 46手・最大11連鎖 | 0 | なし |

4519 の選択差は tick1474 root10→0，tick1540 root11→8，tick1651 root9→7．最後の root9 は即時致死と判明した証拠を除外したものだが，root7 でも死亡し，対局全体の不可避性は証明していない．正常2259は tick1756 root6→4 の差があったが，46手・11連鎖を維持した．

両 run は worker exit0，integrity issue0，要求 root／実 lock と全 tick replay hash が一致した．追加処理を含む600000上限も全 decision で満たした．数値は `summary.json`，全 raw は `pattern-*.json.gz` に保存する．2 run は隔離した別 policy の診断であり，事前登録120 run／repeat一致／正式受入の証拠に流用しない．

## 不採用と残課題

公開到達不能な予測は検出できるが，候補集計から取り除くことだけでは4519を救えない．残る未知 future 仮説，未保存の代替解，quiet 継続の証明不足がある．原則として正しい局所フィルタでも大連鎖構築・生存の改善を保証しないことが，実 lock／replay 一致の反例で示された．支持数・重み・判定をこの pattern に合わせて変更しない．一般的で小さい品質修正の根拠は得られず，BLOCKED を維持する．

`diagnostic-runner.py.gz` と `single-intervention.py.gz` は実行 script の可逆圧縮原物．実行は記載された永続 runs と測定 source を前提にするため，現在 head での新しい正式評価コマンドではない．元の progress／log／raw はそれぞれ `runs/puyo-266-prefix-filter-diagnostic-v1/`，`runs/puyo-266-reference-counterfactual-v2/` に保全した．

保存内容の再確認（repository root）：

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('docs/benchmarks/puyo-266-single-quality/rejected-prefix-filter-20261011')
for item in json.loads((root / 'checksums.json').read_text()):
    data = (root / item['path']).read_bytes()
    assert len(data) == item['bytes']
    assert hashlib.sha256(data).hexdigest() == item['sha256']
print('all archived checksums match')
PY
```
