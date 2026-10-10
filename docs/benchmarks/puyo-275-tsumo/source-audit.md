# PUYO-275 原本監査

実施日: 2026-10-10．source: https://puyo-camp.jp/posts/86154 → https://www.axfc.net/u/3980710．

| 項目 | 観測値 |
| --- | --- |
| ZIP SHA-256 | `2dbed9e94fd95dd3c4cc2bfa343e6442a3be33183da52dee21511d6474e3b115`（Axfc 表示と一致） |
| テキスト SHA-256 | `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb` |
| サイズ／行数 | 16,842,752 byte／65,536 行 |
| 行検査 | 全行 256 文字，`rgbyp` のうち 4 色，重複行なし |
| ID 34066 先頭 | `bpbpbpypgybgbpbbpyby`，一次投稿の 34067 行目と一致 |
| 別作者実装との照合 | puyop.com の API で ID 0／34066／65535 の各 256 文字が完全一致．weakflour の公開 JS で全 ID の先頭 24 文字が一致．両者とも原本利用の系譜であり，公式ゲームの独立証明ではない |
| 実対局との照合 | 中盤・末尾・128 手境界と 2P 配布規則は未達 |
| 権利 | 公開意思は確認．再配布許諾の記載なし．原本を commit しない |

軽量 QA: provider／legacy headless／legacy replay／public snapshot の 37 unittest 成功．原本 ID 0／34066／65535 の全 128 組を内部色から逆写像すると原本の 256 文字へ戻り，129 手目が 1 手目と一致．30 tick の `first` 対 `first` replay を原本 ID 34066 で生成し，同じ原本から復元した hash `470f6851a0969a2b49a798fdf7b86e7e69d50f55aca1628c33c014c6b520dee9` と一致．GUI 実画面 QA は未実施．
