# PUYO-277 攻撃対応・副砲 gate

本線品質の PUYO-266 と独立した，公開盤面 challenge の gate である．対戦中の平均最大 10 連鎖を条件にしない．既存 PUYO-250 の response search，PUYO-270 の生存 envelope，scheduler，controller，対戦 engine をそのまま使用する．公開 schema は変更しない．後続改善では，相殺候補の順位だけを限定して変更した（下記）．

## 事前固定

[fixture](../../tests/fixtures/puyo_277_attack_response_cases.json) は，初回探索・実行前の commit `00a8fe1` で固定した．pattern ID は `0，1，32768，65535`，各 ID に以下の 7 ケースを用意し，攻撃あり・なしの計 56 条件を実行する．色・盤面・公開 3 組・攻撃 script・期待する分類・timing・予算を結果に合わせて変更しない．公開 corpus の全 65,536 pattern や自然対戦全般の性能を代表するとは主張しない．

| ケース | 確認する境界 |
| --- | --- |
| `cancel_boundary` | 到着済み packet を実際の現在組の解決で相殺する． |
| `post_arrival_recovery` | 最初の実着弾を観測し，その後に発火する．先に相殺しただけではこの条件を満たさない． |
| `independent_subchain` | 主線と独立した小連鎖を使う． |
| `mainline_without_key` | キーぷよがあれば 2 連鎖へつながる形の一部を，現盤面では小連鎖として使う． |
| `no_secondary_chain` | 副砲がない盤面で，公開情報による生存と通常配置を検証する． |
| `preserve_mainline` | 遅延した攻撃を処理し，本線の登録 cell と相対形状を保持する． |
| `unavoidable_loss` | 固定盤面の全合法 current root が最初の相殺・落下境界で致死になることを別 oracle で確認する．成功数から除外する． |

盤面は人為的な開始条件であり，AI が空盤面からその形を構築できた証拠ではない．初期化時は empty-reset の隠れ行推定を無効化する．実 provider の current/NEXT/NEXT2 を固定値と照合し，piece の差替えはしない．相手は停止し，攻撃あり条件だけ `schedule_attack` に登録済み script を入力する．相手の私的な future や pattern ID を policy へ渡さない．

## 肯定的な実行証明と探索の完全性

`prepared` は登録された副砲 cell が現在の公開盤面に存在する状態である．`fireable_current_actions` は公開 current 組でその cell を消す幾何学的な候補のうち，request の到達可能 mask に含まれる action である．どちらも採用・実行の証明ではない．隠れ行が未確定なら `conditional_hidden_rows` を残す．キーぷよ追加後の連鎖は明示的な反実仮想であり，利用可能な手や未公開 future として扱わない．

実行の肯定的証明は，公開 root の合法性・到達可能 mask，採用 receipt，実 lock，実 resolution の連鎖数，解決後の公開色盤面が一致した場合だけ成立する．同境界で追加されたおじゃまは別の実着弾 event で確認する．相殺量・着弾時刻・発火時刻は予測ではなく engine の event から集計する．arrival tick は落下可能となる時刻であり，自動的な発火の hard deadline ではない．設定した request timeout と action deadline は実 receipt／lock の tick で検証する．

`response_quota` の cutoff，search 全体の `partial`，公開幾何の条件付き推定は，実際に確認できた応答とは独立に保存する．**探索全体が partial でも，必要な公開 root と実行結果を肯定的に確認した条件は observed response PASS にできる**．全候補の列挙完了は要求しない．未発見や unknown を応答不能・回避不能・成功へ変換しない．相殺・着弾後発火・必要 resolution 数が未観測なら FAIL とする．遅延攻撃が未着弾かつ未相殺のまま run が終了した場合も FAIL とする．

副砲・本線の cell は，各候補の全連鎖段階で元の ID を重力に従って追跡する．消去集合と連鎖解決は既存 `compact_search.transition` を使用し，独自の連鎖 engine を作らない．全 cell が残り，相対座標が変わらない場合だけ登録形状の保持とする．これは登録 annotation の保持であり，一般的な本線の潜在連鎖数や，変形した後の構築価値の推定ではない．本線を消費する場合は selection 理由・公開脅威・消去された元 cell・残存盤面を保存する．

攻撃なし対照では実 lock と生存に加え，生存理由のない小発火を検出する．この短い正常配置回帰は PUYO-266 の長期とこぷよ gate の代用ではない．

## 実行 identity と replay

`init` は clean な実装・test と固定 fixture を確認し，commit，ファイル checksum，native binary／capabilities，Python・package・thread 設定，実 policy の探索値と catalog digest を manifest に保存する．各条件は manifest と実際の値を再照合する．既存の結果は上書きせず，paired 条件の初期 hash・provider・config・script identity が一致しない場合は集約を拒否する．

replay は fixture 初期化，固定 script，tick 入力，実 event，tick ごとの hash を記録する．再生は同じ初期化と script を適用した既存 `RealtimeVersusMatch.step` だけで行う．source の移動時は `--source` で指定し，元の source checksum・version・色 mapping は維持する．これは人工盤面の eval 専用 wrapper であり，GUI replay engine の変更ではない．

configured latency は tick 上の締切確認に使い，CPU 時間を別途保存する．GUI の measured latency SLA 達成は主張しない．正式 backend は native，shared/template/response quota は `256/128/4000`，depth/width/scenarios は `4/4/1`，各条件は最大 3 resolution／1,800 tick である．

## 再現手順

共有 CPU/native 計測枠は親セッションが割り当てる．以下は source の取得・checksum 検証が済んだ環境の例である．

```bash
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m unittest tests.test_nextgen_attack_response_gate -v

PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_attack_response_gate --output /tmp/puyo277-formal-v1 \
  init --source /tmp/puyo275-haipuyo.txt

PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_attack_response_gate --output /tmp/puyo277-formal-v1 \
  run --case cancel_boundary-0 --condition attack

PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_attack_response_gate --output /tmp/puyo277-formal-v1 summarize
```

各 `case_id × condition` を順に実行する．保存先は `<output>/<case_id>-<condition>/{report,replay}.json.gz`，集約は `<output>/summary.json` である．未実行条件があれば `incomplete` となる．例外・停止時は `error.json` と，取得済みの `partial.json.gz` を保持する．PASS を確認するには，summary の全条件と除外理由，report の receipt／実 lock／resolution／公開盤面，replay 検証 hash を照合する．

初期実装時の `/tmp/puyo277-native-smoke-v1` は cutoff を一律 FAIL とした旧判定の調査証跡であり，正式結果へ置き換えない．

## 公開盤面の観測タイミングと再監査

おじゃま演出中は公開盤面が `None` になる．resolution event の時点で公開盤面が未確定なら照合を保留し，演出後に初めて公開された盤面で検証する．この待機中に policy の手を差し替えない．また上端が埋まっている盤面では，30 個の攻撃が予告されても 4 個しか配置できず窒息する場合がある．全合法 root の致死証明と実 lock／resolution／窒息がある場合は `excluded_unavoidable` とし，未落下 packet を応答成功に数えない．

旧 collector で記録した結果は保存したまま，次のコマンドで既存 tick 入力だけを再監査できる．元 report／replay の digest と engine の全 event／hash を照合し，追加 tick・policy 呼出しはともに 0 でなければならない．runtime や source が元 manifest から変わっている場合は拒否する．修正版 collector の source checksum と元の判定を sidecar に保存する．

```bash
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_attack_response_gate --output /tmp/puyo277-formal-v1 \
  audit --destination /tmp/puyo277-formal-v1-public-audit
```

元 replay の範囲に観測可能な盤面がなければ未達のままとする．観測窓を伸ばす評価は別の事前登録を必要とする．元の未観測 FAIL や実際の本線消費を再監査で隠さない．

## 必要量を満たす相殺と追加観測

`agents/nextgen_shared_search.py` の cancel 専用順位では，公開 pending 全量を現在の合法・非致死 response transition で相殺できる場合に，過剰発生量，連鎖数，score の順で小さい候補を優先する．現在組の発火開始推定が request timeout 内であり，発火と相殺が同じ解決境界の時間区間に収まることを要求する．時間区間は相関しており，arrival tick を独立した hard deadline にしない．これらは公開推定であり，実 receipt／lock の期限確認は gate が別に行う．

外側の survival 優先順位を保持し，全量相殺に足りない候補の最大火力順位，counter／短攻撃／build の順位は変更しない．future，pattern ID，fixture の本線・副砲 annotation を製品へ渡さない．小さい消去は保持の代理であり，任意の本線を保持できる保証ではない．実際の登録形状保持は公開 trace で検証する．

[追加観測登録](../../tests/fixtures/puyo_277_attack_response_observations.json) は commit `b9e277b` で実行前に固定した．`preserve_mainline` と `post_arrival_recovery` の全 4 IDs × attack/no_attack，計 16 条件を，同じ自然 controller で 8 resolutions／1,800 ticks まで観測する．入力盤面，pattern，script，quota，120 tick request／lock deadline，期待 witness は元登録のままである．preserve の attack 条件は全相殺，または tick 240 以後の resolution の観測を最低窓とし，8 resolution 上限で未観測なら FAIL を保持する．途中成功による早期終了や policy の停止・手の差し替えは行わない．

```bash
PYTHONPATH=. /home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python \
  -m eval.nextgen_attack_response_gate --output /tmp/puyo277-extended-v2 \
  init --source /tmp/puyo275-haipuyo.txt --extended-observations
```

元 56 条件の v1 と公開再監査は不変である．モデル変更後の 56 条件は別出力 `formal-v2`，追加 16 条件は `extended-v2` として保存し，元の 3 resolution 未観測 FAIL を追加結果で書き換えない．
