# PUYO-232 target10 contract regression

PUYO-232の部分回帰QA。新しいPUYO-236 canonical実行契約を使い、
seed123/126/130/133/135/147のrepeat1を固定予算で再実行した。
6/60runのみ実行済みで、全体評価・baseline採用の証跡ではない。

- `experiment_manifest.json`: 契約・source/build/host・設定・予定60 identities。
- `target-10/*.json.gz`: 新規取得した6runの全decision raw。
- `diagnostic-target-10.json`: 同一盤面cold/warmとprivate counterfactual。
- `target-10/summary.json`: 残り54runをpendingとして集計。全体gateは未達を保持。
- `target10_regression.json`: PUYO-231のtarget10 rawと比較。全239手のaction・実結果が一致。
- `compare.py`: 過去rawと新rawから比較結果を再計算する。
- `evidence_checksums.json`: runnerの証跡checksum。
- `regression.sha256`: 比較script・比較結果・runner checksum indexのchecksum。

品質の残課題と再現手順は[開発記録](../../development/puyo-232-safe-build-target-contract.md)を参照。
