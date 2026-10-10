# PUYO-266 Sprint 15 単独品質 gate

`eval.nextgen_single_quality_gate` は公開者推定 eスポーツ通配ぷよの単独品質を測る．旧 random seed の safe-build／G2 artifact と schema は変更しない．この runner の導入や短い smoke は品質 PASS，Jira COMPLETE，本学習の許可を意味しない．

## 固定条件

事前登録原本を `tests/fixtures/nextgen_single_preregistration.json` に保存した．0～65535 の両端を含む等間隔 30 pattern ID × repeat1／2 × nextgen_tactic_manager／deep_chain_builder の 120 run を，1 run ごとの fresh process で直列実行する．pattern ID を policy seed に流用せず，環境と nextgen の policy seed は 55 に固定する．reference の補完 seed は既存どおり公開観測の digest から導出する．

停止相手・両方向攻撃抑止の同一環境で，40 resolved placements の checkpoint を残し，その後も同じ policy を最大 6 手継続する．正式窓は 46 resolutions または game over．上限 30000 tick に達した run は未完了であり，成功 run に置換・除外しない．target10，reference depth16／width250／6 scenarios／shared quota600000，nextgen template128／response256 を維持する．template binding budget は既存 make_policy の 4096 として別記する．runtime の quota と offline 分類の費用を混同しない．

nextgen の平均最大実連鎖 ≥ 10，理由のない premature0，回避可能窒息0 を判定する．平均の母集団は repeat1 の 30 pattern で，repeat2 は semantic 再現性を検証する．全120runの証拠を要求し，reference 品質は比較結果として別表示する．reference 自身が平均10未満でも nextgen の閾値を変えない．両 policy の incomplete／未知分類／integrity エラーは比較 gate を BLOCKED にする．

## 公開境界

nextgen は既存 scheduler の公開 request を用いる．reference は評価専用 `reference_input` で公開 snapshot と既存 PublicInferenceTracker の出力から観測配列を構成する．私的な盤面，実 ghost 行，simulator，provider，pattern ID，将来配列を adapter に渡さない．非公開2行は空盤面起点と公開の実 lock／clear履歴から known と証明できた場合だけ復元し，unknown を空きセルに置換しない．公開 scoreboard の数値，現在／NEXT／NEXT2，到達可能 mask を併用する．

reference は公開履歴からの推定を search に使い，nextgen は同じ公開推定を survival にのみ使うというアルゴリズム上の差がある．nextgen の GTR phase，reference の定型なし，補完 seed 導出の差も manifest に残す．同一 batch に対する selector 単独の比較とはしない．

配ぷよ source は [PUYO-275 の契約](puyo-275-tsumo-source.md)を使う．各行の原本 r/g/b/y は同色を保持し，紫だけその行で欠けた内部色へ全単射で置換する．checksum／source version／各 ID の color mapping を manifest と各 run の replay rules へ記録する．原本データは repository に含めない．

## 証拠と判定

全 tick の入力・state hash，実 lock／resolution，policy の候補・選択・phase・quota，controller receipt，公開推定，40手 checkpoint と最終結果を保存する．worker は policy を実行しない再生で全 tick hash と最終 hash を確認し，要求 root と実 lock を照合する．fallback／stale／未対応 lock／未実行決定／quota超過／定型上限超過は合格しない．

小発火は全1～9連鎖の raw 件数を保持し，公開盤面・known3組・root到達 mask から別分類する．全到達可能な非小発火rootが即時致死で，選択した小発火が非致死なら `necessary_survival`．quiet または10連鎖以上の代替rootが，既知3組＋色に依存しない追加1配置の幾何 witnessを持つなら `unjustified`．この witness は長期安全の証明ではない．証明できない場合は `unknown` で，有限探索の不発見を必然性に読み替えない．offline 分類は別上限100000nodeで，runtimeの選択・予算にフィードバックしない．

窒息したrunでは，公開推定上で選択rootが即時致死なのに到達可能な非致死代替が存在する場合だけ `avoidable` とする．死の直前に代替がないことを対局全体の不可避性へ昇格せず，残りは `unknown` とする．未分類を残したまま PASS にしない．

manifest は source／testファイル／native binary／host／設定／provider identity を固定し，workerの前後で照合する．結果を上書きせず，失敗もidentity付きで保存する．run途中はresolutionごとに `.progress.json.gz` をatomic更新し，正常完了時だけfinalへ置き換える．`run-all` の再開では既存finalを検証してskipし，既存failureを再試行しない．中断progressは原物を保持して InterruptedWorker として記録し，独立する未実行identityを継続する．完全な再測定が必要なら新しいmanifest/outputを使う．

## 実行

先に source と tests を commit し，親の CPU 排他枠を取得する．共有 native 環境への install はしない．以下は repository root から実行する．出力先は未使用のディレクトリにする．

```bash
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m unittest tests.test_nextgen_single_quality_gate tests.test_nextgen_safe_build tests.test_nextgen_public_snapshot tests.test_nextgen_public_inference tests.test_nextgen_inference_wire tests.test_nextgen_safe_build_gate
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.nextgen_single_quality_gate smoke --source /tmp/puyo275-haipuyo.txt --output /tmp/puyo266-single-smoke
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.nextgen_single_quality_gate init --source /tmp/puyo275-haipuyo.txt --output /tmp/puyo266-single-formal
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.nextgen_single_quality_gate run-all --output /tmp/puyo266-single-formal
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.nextgen_single_quality_gate finalize --output /tmp/puyo266-single-formal
/home/sion2000114/workspaces/dev/puyo-s14-266/.venv/bin/python -m eval.nextgen_single_quality_gate verify --output /tmp/puyo266-single-formal
```

smoke は事前登録最初の pattern0・両policy各3手を固定し，通常profileで判断時間とgzip保存量を測る．その線形見積は終盤の重さを保証しない．正式120runは，smoke結果と最大5520resolution・30000tick/runの費用を親へ報告した後に実行枠を得る．

全体 G2 と学習可否は常に BLOCKED／false を返す．単独 gate に加え PUYO-277 の対戦評価，PUYO-274 の統合・人間 QA の証拠が必要である．
