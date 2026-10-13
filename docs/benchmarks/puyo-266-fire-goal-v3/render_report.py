"""Render the saved PUYO-266 comparison summary as a standalone HTML report."""

from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "docs/benchmarks/puyo-266-single-quality/sprint15-formal-v4/report.json"


def cell(value):
    return html.escape(str(value))


def main():
    report = json.loads((HERE / "report.json").read_text(encoding="utf-8"))
    old = json.loads(OLD.read_text(encoding="utf-8"))
    if report["status"] != "COMPLETE_EXPLORATORY" or report["failed_workers"]:
        raise ValueError("comparison must be complete before rendering")
    rows = {(r["goal"], r["pattern_id"]): r for r in report["rows"] if r["repeat"] == 1}
    old_rows = {r["pattern_id"]: r for r in old["rows"]
                if r["policy"] == "nextgen_tactic_manager" and r["repeat"] == 1}
    if len(rows) != 60 or len(old_rows) != 30:
        raise ValueError("expected 30 paired patterns")
    chain = report["goals"]["chain10"]
    score = report["goals"]["score95000"]
    low = (11299, 18078, 42936)
    low_cards = []
    for pattern in low:
        before = old_rows[pattern]["final"]["max_chain"]
        after = rows[("chain10", pattern)]
        descriptor = (f'{after["placements"]} 手で実 {after["max_chain"]} 連鎖'
                      if after["target_chain"] is not None else
                      f'60 手で最大 {after["max_chain"]} 連鎖')
        low_cards.append(f'<div class="case"><strong>{pattern}</strong><p>46 手：{before} 連鎖</p>'
                         f'<span class="arrow">→</span><p>60 手枠：{descriptor}</p></div>')
    table = []
    for pattern in sorted(old_rows):
        a, b = rows[("chain10", pattern)], rows[("score95000", pattern)]
        old_max = old_rows[pattern]["final"]["max_chain"]
        score_text = "—" if b["target_score"] is None else f'{b["target_score"]:,}'
        note = "旧低連鎖" if pattern in low else (
            "95,000 点到達" if b["max_fired_score"] >= 95_000 else "")
        table.append(f'<tr class="{"focus" if pattern in low else ""}"><th>{pattern}</th>'
                     f'<td>{old_max}</td><td>{a["placements"]}</td><td>{a["max_chain"]}</td>'
                     f'<td>{b["placements"]}</td><td>{b["max_chain"]}</td><td>{score_text}</td>'
                     f'<td>{cell(b["termination"])}</td><td>{note}</td></tr>')
    counts = {goal: {n: sum(rows[(goal, p)]["max_chain"] == n for p in sorted(old_rows))
                     for n in range(0, 18)} for goal in ("chain10", "score95000")}
    bars = []
    for n in range(0, 18):
        left, right = counts["chain10"][n], counts["score95000"][n]
        bars.append(f'<div class="bucket"><div class="twobars">'
                    f'<span class="blue" title="10 連鎖目標・{n} 連鎖：{left} 件" style="height:{max(2,left*8)}px"></span>'
                    f'<span class="amber" title="95,000 点目標・{n} 連鎖：{right} 件" style="height:{max(2,right*8)}px"></span>'
                    f'</div><small>{n}</small></div>')
    note = "，".join(str(p) for p in sorted(old_rows))
    document = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PUYO-266｜発火目標と 60 手の比較</title><style>
:root{{font-family:system-ui,"Noto Sans JP",sans-serif;color:#243348;background:#f1f5fa}}
*{{box-sizing:border-box}} body{{margin:0}} main{{max-width:1200px;margin:auto;padding:28px 20px 70px}}
h1{{font-size:clamp(2rem,4vw,3.2rem);line-height:1.25;margin:8px 0 16px}} h2{{margin:44px 0 14px;font-size:1.45rem}}
p{{line-height:1.8}} .kicker{{font-size:.8rem;letter-spacing:.14em;color:#46617f;font-weight:700}}
.lead{{font-size:1.1rem;max-width:72ch}} .banner{{background:#173b65;color:white;border-radius:22px;padding:26px 30px;margin:26px 0}}
.banner strong{{font-size:1.2rem}} .banner p{{margin-bottom:0;color:#e2ebf6}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}
.card,.panel,.case{{background:white;border:1px solid #e0e8f1;border-radius:18px;padding:20px;box-shadow:0 3px 14px #203b5910}}
.card h3{{margin:0 0 12px}} .card .number{{font-size:2.7rem;font-weight:800;line-height:1.1}}
.blue-text{{color:#1762b2}} .amber-text{{color:#b76500}} .card dl{{display:grid;grid-template-columns:1fr auto;gap:8px 16px;margin-bottom:0}}
.card dt,.card dd{{border-top:1px solid #e7edf3;padding-top:8px;margin:0}} .card dd{{font-weight:700}}
.stack{{display:flex;overflow:hidden;border-radius:9px;height:36px;background:#e6ebf2;margin:18px 0 8px}}
.stack span{{display:block;height:100%}} .hit{{background:#2887dc}} .death{{background:#db744f}} .window{{background:#a6b6c8}}
.legend{{font-size:.9rem;color:#4b5f77}} .legend b{{color:#243348}} .cases{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}
.case strong{{font-size:1.35rem}} .case p{{margin:4px 0}} .arrow{{font-size:1.4rem;color:#3381ca}}
.bars{{display:flex;align-items:end;height:210px;border-bottom:1px solid #91a2b4;gap:4px;margin:10px 0 26px}}
.bucket{{flex:1;min-width:0;text-align:center}} .twobars{{height:184px;display:flex;align-items:end;justify-content:center;gap:2px}}
.twobars span{{width:44%;border-radius:4px 4px 0 0}} .blue{{background:#3983df}} .amber{{background:#edaa42}} .bucket small{{font-size:.72rem}}
.tablewrap{{overflow:auto;border-radius:16px;border:1px solid #dce5ef}} table{{border-collapse:collapse;width:100%;background:white;font-size:.87rem}}
th,td{{padding:10px 12px;border-bottom:1px solid #e7edf3;text-align:right;white-space:nowrap}} thead th{{background:#dfeaf7;position:sticky;top:0}}
tbody th{{font-weight:700}} tr.focus{{background:#fff0d9}} td:last-child{{text-align:left}} code{{background:#e8eef6;padding:2px 5px;border-radius:4px}}
.note{{border-left:4px solid #e9a03c;padding:12px 18px;background:#fffaf1;border-radius:0 12px 12px 0}}
a{{color:#145ba5}} .small{{font-size:.86rem;color:#52657d}}
</style></head><body><main><p class="kicker">PUYO-266 · EXPLORATORY COMPARISON · 2026-10-12</p>
<h1>発火目標を変え，60 手まで待つとどうなるか</h1>
<p class="lead">とことんぷよぷよ相当の単独環境で，同じ eスポーツ通 30 pattern を各 2 回評価した．両設定とも<b>最初の実 10 連鎖以上</b>，窒息，または 60 手で測定を止める．</p>
<div class="banner"><strong>結論：60 手への延長は 1 件を救ったが，95,000 点目標への単純な切替は全体品質を下げた．</strong>
<p>10 連鎖目標は 28/30 pattern が実 10 連鎖以上に到達し，窒息は 0 件．95,000 点目標は 3/30 pattern が実際に 95,000 点を超えた一方，16/30 pattern が窒息した．</p></div>
<section class="grid"><div class="card"><h3>10 連鎖目標</h3><div class="number blue-text">{chain["first_10_plus"]}/30</div><p>実 10 連鎖以上</p>
<dl><dt>平均最大実連鎖</dt><dd>{chain["mean_max_chain"]:.2f}</dd><dt>窒息</dt><dd>{chain["game_overs"]}/30</dd><dt>平均停止手数</dt><dd>{chain["mean_placements"]:.1f}</dd></dl></div>
<div class="card"><h3>95,000 点目標</h3><div class="number amber-text">{score["score_95000_plus"]}/30</div><p>実 95,000 点以上（実 10 連鎖以上は {score["first_10_plus"]}/30）</p>
<dl><dt>平均最大実連鎖</dt><dd>{score["mean_max_chain"]:.2f}</dd><dt>窒息</dt><dd>{score["game_overs"]}/30</dd><dt>平均停止手数</dt><dd>{score["mean_placements"]:.1f}</dd></dl></div></section>
<h2>終了理由の内訳</h2><div class="panel"><p class="small">各棒は 30 pattern．水色＝最初の実 10 連鎖以上，赤＝窒息，灰＝60 手まで到達して 10 連鎖未達．</p>
<strong>10 連鎖目標</strong><div class="stack"><span class="hit" style="width:{chain["first_10_plus"]/30*100}%"></span><span class="window" style="width:{(30-chain["first_10_plus"]-chain["game_overs"])/30*100}%"></span></div>
<p class="legend"><b>{chain["first_10_plus"]}</b> 本線到達 · <b>{chain["game_overs"]}</b> 窒息 · <b>{30-chain["first_10_plus"]-chain["game_overs"]}</b> 60 手未達</p>
<strong>95,000 点目標</strong><div class="stack"><span class="hit" style="width:{score["first_10_plus"]/30*100}%"></span><span class="death" style="width:{score["game_overs"]/30*100}%"></span><span class="window" style="width:{(30-score["first_10_plus"]-score["game_overs"])/30*100}%"></span></div>
<p class="legend"><b>{score["first_10_plus"]}</b> 本線到達（うち <b>{score["score_95000_plus"]}</b> 件が 95,000 点超） · <b>{score["game_overs"]}</b> 窒息 · <b>{30-score["first_10_plus"]-score["game_overs"]}</b> 60 手未達</p></div>
<h2>旧 46 手で低かった 3 pattern</h2><div class="cases">{''.join(low_cards)}</div>
<p>11299 は 49 手目に 11 連鎖・51,401 点となり，46 手打ち切りが未達の原因だった．18078 と 42936 は 60 手でも最大 3 連鎖で，窒息はしていない．</p>
<h2>最大実連鎖の分布</h2><div class="panel"><p class="small">各 pattern の repeat 1 の最大値．青＝10 連鎖目標，橙＝95,000 点目標．</p>
<div class="bars">{''.join(bars)}</div></div>
<h2>実 95,000 点を超えた例</h2><div class="grid"><div class="case"><strong>51976</strong><p>34 手 · 14 連鎖 · 100,481 点</p></div>
<div class="case"><strong>54235</strong><p>59 手 · 14 連鎖 · 107,961 点</p></div>
<div class="case"><strong>63275</strong><p>53 手 · 13 連鎖 · 98,580 点</p></div></div>
<p>31637 は 13 連鎖・81,641 点，65535 は 11 連鎖・56,981 点で，10 連鎖以上には達したが 95,000 点には届かなかった．</p>
<h2>全 30 pattern の結果</h2><p class="small">点数は最初の実 10 連鎖以上の <code>score_delta</code>．橙色は旧 46 手の低連鎖 3 件．</p>
<div class="tablewrap"><table><thead><tr><th>pattern</th><th>旧 46 手最大</th><th>10 連鎖目標・手数</th><th>最大連鎖</th><th>95,000 点目標・手数</th><th>最大連鎖</th><th>本線点数</th><th>終了</th><th>注記</th></tr></thead><tbody>{''.join(table)}</tbody></table></div>
<h2>解釈と制約</h2><div class="note"><p>新評価は最初の実 10 連鎖で停止する．旧 46 手評価はその後も測定を続けたため，旧平均最大 9.67 と新平均 10.03 を同一指標の改善幅として差し引けない．10 連鎖目標の新しい 28/30 は 60 手以内の到達率として読む．また 95,000 点未満の 10 連鎖で止まった場合，その先の 95,000 点到達可否は観測していない．</p>
<p>score 設定は，探索の発火分類・不足点減点，候補生成，rule selector を 95,000 点へ切り替える．盤面の構造評価には従来の連鎖数目標も残る．この組合せでは高得点を待つ間の生存切替が不十分と考えられるが，原因の確定には個別局面の追加解析が必要．学習による重み調整は今回の変更範囲に含めていない．</p>
<p>pattern 29377 では 6 手目の 1 連鎖後，29 手目に 11 連鎖・52,501 点となった．旧 gate の「不当」分類はその手の公開情報に基づく局所的な代替証明で，後続の本線失敗を意味しない．旧受入基準は変更しておらず，この追加比較だけで PUYO-266 の PASS／COMPLETE は宣言しない．</p></div>
<h2>検証資料</h2><div class="panel"><p>30 pattern × 2 repeat × 2 設定＝120 run．全 run の tick replay，lock 照合，repeat の意味的ハッシュ一致を確認した．不足 run 0，worker 失敗 0，repeat 不一致 0．source・native build・配ぷよ SHA は <a href="manifest.json">manifest.json</a> に固定．全件集計と artifact SHA は <a href="report.json">report.json</a>，代表 raw は <a href="README.md">README.md</a> に記載．</p>
<p class="small">manifest SHA-256：<code>{cell(report["manifest_sha256"])}</code>．対象 pattern：{note}．</p></div>
</main></body></html>'''
    (HERE / "index.html").write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
