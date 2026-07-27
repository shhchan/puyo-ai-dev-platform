# PUYO-180 K-best offline oracle

- config: `puyo-180-contract-d1-w22-s1-n100-k8`
- seeds: 1
- oracle target fires: 0/1
- generator/selection classifications: `{"candidate_generator_failure": 1}`
- K-best root constraint: PASS
- future-input isolation: PASS
- build/fire phase limits: PASS
- latency-free repeat determinism: PASS

The oracle is an offline candidate-set upper bound. Its future queue,
values, and selections are not runtime or learned-policy features.

Reproduce with:

```bash
python -m eval.v1_7_k_best_oracle run \
  --seeds 123 \
  --profile runtime \
  --config-id puyo-180-contract-d1-w22-s1-n100-k8 \
  --depth 1 --width 22 --scenarios 1 \
  --max-expanded-nodes 100 \
  --build-steps 40 --fire-steps 6 \
  --preview-steps 6 --target-chain 10 \
  --potential-max-added-puyos 1 --potential-max-pattern-nodes 1 \
  --potential-max-resolution-nodes 1 --potential-max-alternatives 1 \
  --potential-max-continuation-actions 1 --potential-max-recovery-puyos 0 \
  --repetitions 2
python -m eval.v1_7_k_best_oracle verify
```
