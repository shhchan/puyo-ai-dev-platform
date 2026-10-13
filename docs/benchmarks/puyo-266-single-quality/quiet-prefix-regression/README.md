# Quiet prefix 回帰

formal v3 の pattern6779／nextgen は tick1527 に 1 連鎖を選び，39 手で窒息した．公開 inference と既知 3 組だけを使った offline 証明には quiet root0 の `[0,5,7]`＋色に依存しない終端配置0があった．runtime は root0 の最初の幾何 witness `[0,3,15]` が制御不能なため unknown とし，別の継続を探す前に小発火 root1 を採用していた．reason の `legitimate_survival_exception` を必要性の証明に読み替えない．

request 内で同一占有盤面と完全な planner pose に対する control successor を再利用し，制御不能な prefix を早期に枝刈りする．無攻撃・おじゃまなし・公開 inference known の場合だけ，目標未満の小発火を採用する前に quiet 代替を元の根順で確認する．目標以上の発火と既存攻撃／着弾おじゃま経路は維持する．

固定 request の proof は probe28＋control48＋placement32＋terminal8＝116 nodes．terminal8 のうち6は新しい末端幾何 prefilter の初回計算，残り2は証明成立分である．同一占有盤面の prefilter 再利用も診断に残す．128 node と response256 の上限は変えない．

`targeted-native.json` は公開 request の native 探索と実 selector による action0，実 controller の lock，非発火 resolution，全追加 tick の hash 再生を記録する．元の root 順位は維持し，root2を不成立と確認した後でroot0の代替を証明する．code SHA256 と修正前 HEAD を証拠に含めた．fixture は `tests/fixtures/nextgen_single_quiet_6779_public.json`．正式 v3 の全証拠は同 worktree の `runs/puyo-266-formal-v3/` と `runs/puyo-266-control-v3/` に保全する．この targeted 証明は品質 gate の PASS ではない．reference4519 の窒息 unknown は別途調査する．
