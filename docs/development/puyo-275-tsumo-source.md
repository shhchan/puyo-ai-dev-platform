# PUYO-275 配ぷよ source と検証境界

## 出所と扱い

[公開者の一次投稿](https://puyo-camp.jp/posts/86154)は，四色通ルール対戦で使われると推定した配ぷよを [Axfc の ZIP](https://www.axfc.net/u/3980710) で公開している．投稿によれば，65,536 行の各行が 128 手／256 個，`r/g/b/y/p` は赤／緑／青／黄／紫，軸ぷよが先で，128 手後に同じ行を繰り返す．公開者自身が公式仕様との完全一致を保証していない．

Axfc は権利・責任がアップロード者に帰属すると表示する．投稿はデータの公開意思を示すが，再配布ライセンスは確認できない．この repository に原本を同梱しない．利用者が原本を入手し，ローカルの `haipuyo.txt` を明示指定する．ゲーム内表示と評価結果は「公開者推定データ」と記し，セガ公式準拠とは称さない．

2019-05-19 公開 ZIP の SHA-256 は `2dbed9e94fd95dd3c4cc2bfa343e6442a3be33183da52dee21511d6474e3b115` で，Axfc 表示の値と一致した．展開後 `haipuyo.txt` は 16,842,752 byte，SHA-256 `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．実装は展開後の SHA-256，65,536 行，1 行 256 文字，5 色から 4 色の使用を開始前に検証する．LF 原本のみを認識し，改行 CRLF 版は別 checksum として拒否する．

ID は行番号の 0 起点である．一次投稿の「34067 行目」の先頭 `bpbpbpypgybgbpbbpyby` は ID 34066 と一致した．[別作者のシミュレータ記事](https://puyo-camp.jp/posts/91012)が明記する [シミュレータの配ぷよ API](https://www.puyop.com/Sim/get-haipuyo?pattern=34067) から ID 0／34066／65535 の各 256 文字を取得し，原本の序盤・中盤・末尾まで完全一致した．[別作者の検索ツール](https://puyo-camp.jp/posts/108218)の公開 JS データでは，全 65,536 ID の先頭 24 文字が原本と一致した．いずれも原本データを利用した別実装による照合で，実対局の独立実測やセガ公式仕様の証明ではない．128 手の繰返しは一次投稿と実装 fixture で確認したが，実対局の 128 手越えは未照合である．対戦で 2P に同じ pattern ID を渡す方式と，明示した別 ID を渡す方式を提供する．原作の 2P 配布規則も独立実測が未達である．

内部 engine／AI 公開 wire は 4 色である．原本の各行も 4 色なので，`r/g/b/y` は同色へ保ち，`p` があればその行で欠けた `r/g/b/y` の色へ写す全単射を適用する．例えば `b/g/p/y` の ID 34066 では `p→RED`．色名以外の同色関係，軸／子順，連鎖構造と 128 手周期は保存される．replay の `color_mapping` に 1P／2P それぞれの対応を記録する．画面色は原本の紫と一致しない場合がある．これは四色列の色同型符号化であり，別配ぷよ列を生成する近似ではない．

## ローカル確認

Axfc の ZIP をブラウザーで取得し，以下の path は手元の取得場所に置き換える．

```bash
sha256sum ~/Downloads/haipuyo.zip
unzip -p ~/Downloads/haipuyo.zip haipuyo.txt > /tmp/puyo275-haipuyo.txt
sha256sum /tmp/puyo275-haipuyo.txt
wc -l -c /tmp/puyo275-haipuyo.txt
```

次の軽量 CLI は ID 34066 を 2P 共有で 30 tick 実行し，replay を保存する．出力先は `/tmp/puyo275-replay.json`．異なる PC で replay を確認するときは原本テキストをその PC に置き，`tsumo_source_override` に新しい path を指定する．checksum が違えば再生を拒否する．

```bash
python -m eval.realtime_arena --policy-a first --policy-b first --games 1 --seed 7 --max-ticks 30 --tsumo-mode esports_tsu --tsumo-source /tmp/puyo275-haipuyo.txt --tsumo-pattern-id 34066 --replay /tmp/puyo275-replay.json
python -c 'import json; from eval.realtime_arena import replay_realtime_match; print(replay_realtime_match(json.load(open("/tmp/puyo275-replay.json")), tsumo_source_override="/tmp/puyo275-haipuyo.txt"))'
```

通常の表示環境で `python -m eval.realtime_versus_ui --policy-a human --policy-b first --tsumo-mode esports_tsu --tsumo-source /tmp/puyo275-haipuyo.txt --tsumo-pattern-id 34066 --replay /tmp/puyo275-gui-replay.json` を起動し，1P／2P の current・NEXT・NEXT2 が同じ色と軸／子順で始まること，配置後に NEXT が一組進むことを確認する．この ID の初手は原本 `bp` から内部 `BLUE/RED` へ正規化される．実際の対局が終了したら上記 replay 検証コマンドの path を GUI の保存先へ変更して hash を照合する．launcher では事前に `PUYO_TSUMO_SOURCE=/tmp/puyo275-haipuyo.txt` を設定すると source 候補を選べる．配ぷよ方式を `esports_tsu`，ID を 34066 にして開始する．

既定 `random` は既存 seed／hash と同じ．旧 seed corpus は legacy random 回帰として扱い，新しい ID と同一視しない．新方式の pattern ID／source の来歴は replay の `match_rules.tsumo` だけに記録する．policy 公開 snapshot と legacy policy `info` の simulator には渡さない．legacy policy の simulator コピーは現在組／NEXT／NEXT2 を維持し，その後は独立した合成乱数列になる．この合成列は原本の将来組を予告しない．
