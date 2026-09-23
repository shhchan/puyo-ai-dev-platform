# PUYO-229 ghost row 状態・parity 契約

PUYO Sprint 10 の先行着手。親は PUYO-184、PR の起点と base は既存の
`integration/puyo-113-v1-7-2`。2026-09-06 の Jira 確定仕様を正本とする。

## ゲーム状態の契約

座標は左から `x=0..5`、下から `y=0..13`。

| 段 | 座標 | 重力 | 連結消去・隣接おじゃま消去 | 状態への保存 |
| --- | --- | --- | --- | --- |
| 第1〜12行 | y=0..11 | 対象 | 対象 | 必須 |
| 第13行（hidden） | y=12 | 対象 | 対象外 | 必須 |
| 第14行（ghost） | y=13 | 対象外 | 対象外 | 色・各列の占有を必須 |

第14行は各列で1ゲームに1回だけ配置できる。配置後はその色を保持し、下段の
消去・空洞化・後続手・重力で落下しない。占有済みの同じセルへの配置は反映せず、
既存のぷよを保持する。`Field.place_puyo` は `False` を返し、
`remove_puyos` でもこのセルを消去しない。新しい `Field` がゲームのリセット境界。
占有がゲーム中に解放されないため、別の「過去に使用済み」ビットは必要ない。

ペアの配置可否・移動・回転の衝突判定は14行を参照する。第14行に軸・子の
どちらが入る場合も、対象セルが占有済みなら入力を拒否する。placement simulator の
無効手は盤面・現在ペア・次ペア・得点を消費せず `valid=false` を返す。
realtime の `lock_puyo` に第14行との重なりが残った場合も、ペア全体の固定と
ソフトドロップ得点の加算を拒否する。

placement action は軸 `start_y=12` から、そのactionの向きで落下位置を探す。
したがって第14行が占有済みの列では、下段に空きがあっても `UP` は開始位置の
子が衝突して無効となる。`DOWN` は第14行を使わないため、下段が空いていれば
有効になり得る。realtime のtick単位の到達可否は既存reachable plannerで判定する。

spawn/game over は既存の `(2, 11)` の閉塞判定を維持する。第14行の占有だけでは
game over にならない。中央のghost slotとの重なりがあるspawnからも、空いている
下段への落下等は可能だが、重なったままの固定は上記のガードで拒否する。
全消し判定は14行全体を対象にするため、ghost が残る盤面は全消しにならない。

## 観測と探索への伝播

追加情報 `ghost_row` の契約版は `puyo.ghost_row.v1`。

| 項目 | 内容 |
| --- | --- |
| `board` / `own_board` | 従来どおり `(6, 13, 6)`、第13行から下へのone-hot |
| `ghost_row` | 自分の第14行の `(6, 6)` float32 one-hot、軸は `(色, x)` |
| 色順 | RED, BLUE, GREEN, YELLOW, PURPLE, OJAMA |
| 空セル | その列の全色チャネルが0 |
| 値・重複 | 0/1のみ。同じ列への複数色、NaN、不正shapeは拒否 |
| 欠落 | 未知。空行として補完してはならない |

single / placement versus / realtime の観測生成とGym spaceに自分の `ghost_row` を
追加した。versusの結合 `board` は従来の `(12, 13, 6)` のままで、追加行は自分の
状態を表す。相手側の完全状態を表す契約への拡張はこのタスクの対象外。

`VisibleRuntimeInput` は許可リストを通して追加行をコピーし、決定seed、合法なroot手、
Python探索、native request、fallbackの遷移予測、planの盤面・fingerprintへ渡す。
private future / simulatorオブジェクトは引き続きこの境界を越えない。
公開infoの `score` / `last_chain_end_score` / `all_clear_bonus_pending` /
`game_over` もcompactのライフサイクル状態へ渡す。

13行だけの旧入力は完全なcompact状態に変換できない。Python policyは既存の
fallback機構で合法maskの手を返すことがあるが、予測盤面・fingerprintは生成せず
prediction unavailableとする。native policyはadapterエラーを送出する。
どちらも完全parityのPASSにはならない。

`CompactSearchState` / native wireは元から14行全色とライフサイクル状態を持つ。
`puyo.compact_search_state.v1` / `CSK1` / native ABIは変更不要。
第14行が異なる状態はequality、hash、TT、canonical bytesで別状態となる。

既存CNNのboard shape、NEXT/scalars、`flatten_vector_features`、realtimeの既存
checkpoint schemaは維持する。リポジトリ内の学習済みモデルはそれらの既存入力を
明示的に使用するため、checkpoint重みの拡張・再学習は不要。独自にGym Dictの全項目を
flattenしている外部利用者は追加36要素の扱いを決める必要がある。
また、decision seedに第14行を含めるため、旧版の探索trajectory/digestとの同一性は
要求しない。今後の品質評価は新しい実行として記録する。

## 修正した不一致

1. `deep_chain_builder` が13行観測からcompact状態を作る際に第14行を失っていた。
   root手列挙も同じ復元処理に統一した。
2. `Field.place_puyo` は第14行の既存セルを上書きできた。直接配置と重なったlockを
   拒否し、消去処理もvisible範囲に制限した。
3. nativeの `place_reachable_state` は、下段heightが12の場合だけ第14行の衝突を
   確認していた。下段が低くても `UP` の開始位置は衝突するため、常に拒否する。
   `legal_actions_mask`、authoritative、Pythonの判定と一致させた。

## parity v2

盤面状態契約は `puyo.board_state.ghost_row.v1`、比較契約は
`puyo.simulator_parity.v2`。比較対象はplacement境界の状態・遷移であり、
realtimeの入力tickや未公開future queueを含むシミュレータ全体の同一性ではない。

`eval/simulator_parity.py` は次を別々に報告する。

- 14行boardの存在・shape、公開13行の一致、第14行の一致、cell単位の差分。
- action、valid、chain count、score delta、game over。
- 遷移前rootと遷移後の完全compact状態のfingerprint、およびauthoritative boardの整合。

1項目でも不一致・証拠欠落なら `passed=false`。13行投影だけの一致は補助診断。
fingerprintは既存planと同じ `SHA-256(CSK1 bytes)[:24]` で、14行の色・占有と
全消し予約、game over、累積score、last chain end scoreを含む。
独立したPython/native/authoritative回帰テストとraw再生では、fingerprintに加えて
完全な `CompactSearchState` および遷移結果そのものを比較する。

benchmarkの新規recordは予測14行board、valid、root fingerprintを保存する。
run / summary / manifestにparity契約版を記録し、旧契約runのresume・混在を拒否する。
PUYO-189/204の出力ディレクトリとその配下への書き込みを禁止するため、新規benchmark
では明示的に別の `--output-dir` を指定する。既存 `verify` は読み取り専用で使える。

## PUYO-204 の再分類結果

正本は `docs/benchmarks/puyo-229-ghost-row-parity/reclassification.json`。
元の60ファイルのSHA-256、各34件のrun/seed/repeat/turn/action/decision digest、
cell差分、旧/完全状態fingerprint、旧/完全合法手を保存した。

| seed | turn（0始まり） | repeats | decision数 | 差分セル |
| --- | --- | --- | ---: | --- |
| 136 | 32〜39 | 1, 2 | 16 | `(1, 13)` BLUE |
| 138 | 37〜39 | 1, 2 | 6 | `(4, 13)` YELLOW、turn39では `(5, 13)` GREENも欠落 |
| 151 | 34〜39 | 1, 2 | 12 | `(1, 13)` GREEN |
| 合計 | 17 decision × 2 | | 34 | 36セル |

全2,400 decisionについて、seedからauthoritative simulatorを再生してrawの前後盤面・
pair・結果を照合した。旧artifactはpredicted boardを省略していたため、旧13行投影から
再遷移し、保存済みplan fingerprintとの一致を全件確認して予測盤面を再構成した。
公開13行・action・chain・score・game overの不一致は0件。34件はすべて
`ghost_row_lost_at_observation_boundary`、未説明差分は0件。

旧34件の完全parityは **FAILのまま**。追加行を伝播した観測からのPython/native遷移は、
同じ固定手順2,400件すべてでauthoritative状態・結果に一致した。これは過去の選択手の
再生検証であり、新しい探索の60-run品質・性能benchmarkではない。experimental baselineを
昇格させず、target比較はPUYO-231、修正後の60-run再判定はPUYO-236で実施する。

## 再現・確認手順

Linux x86_64 / CPython 3.12でrelease extensionを用意する。

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.ghost_row_reclassification --verify
.venv/bin/python -m unittest tests.test_ghost_row_contract tests.test_ghost_row_reclassification
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
```

`--verify` は60 raw runを再生し、保存済み再分類の意味上の内容と比較する。
native build identityは環境間で異なるため比較から除くが、現在のnativeを使う全件遷移検証は
毎回実施する。再分類artifactの作成には `--verify` を外す。

関連回帰の実行コマンド:

```bash
.venv/bin/python -m unittest \
  tests.test_ghost_row_contract tests.test_ghost_row_reclassification \
  tests.test_compact_search tests.test_logic tests.test_controls \
  tests.test_headless_simulator tests.test_realtime_headless \
  tests.test_single_env_optional tests.test_versus_env tests.test_realtime_ai \
  tests.test_deep_chain_builder tests.test_deep_chain_builder_benchmark \
  tests.test_deep_chain_builder_smoke tests.test_deep_chain_native_transition \
  tests.test_deep_chain_search_backend
```

163 tests PASS（nativeを含みskipなし）。Rustは45 PASS、既存profile用2 testsはignored。
Rust fmt / clippy、変更対象のPython lintもPASS。
再分類artifactは実装commit `1b729f5` のclean worktreeでbuildしたrelease wheelを使用した。
wheel SHA-256は `71f7ad37bb7b7cf76fcfcb759baf89bf6d4268c25c5fc4d10dc82ced76e3a5f2`。
人間は再分類JSONの `summary` と上表の34件を確認し、`--verify` の
`reclassification_verified=true` / `corrected_transition_parity_passed=true` /
`historical_full_parity_passed=false` を確認できる。

## References

- [PUYO-229](https://shhchan.atlassian.net/browse/PUYO-229)
- [PUYO-204](https://shhchan.atlassian.net/browse/PUYO-204)
- [PUYO-231](https://shhchan.atlassian.net/browse/PUYO-231)
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [旧baseline報告](../benchmarks/puyo-204-deep-chain-native-baseline/benchmark_report.md)
