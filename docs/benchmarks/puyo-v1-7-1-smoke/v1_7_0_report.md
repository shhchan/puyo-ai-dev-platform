# Arena Report: v1_7_1_vs_v1_7_0

- policy_a: `v1_7_bootstrap_manager`
- policy_b: `v1_7_analyzer_manager`
- checkpoint_a: `runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt`
- checkpoint_b: `-`
- seed: `123`
- max_steps: `40`

| metric | value |
|---|---:|
| games | 40 |
| wins_player_0 | 21 |
| wins_player_1 | 19 |
| draws | 0 |
| win_rate_policy_a | 0.725 |
| score_rate_policy_a_ci95 | 0.725 [0.585, 0.865] |
| mean_score_player_0 | 3180.75 |
| mean_score_player_1 | 2830.00 |
| mean_max_chain_player_0 | 2.45 |
| mean_max_chain_player_1 | 2.40 |
| mean_decision_ms_policy_a | 166.54 |
| mean_expanded_nodes_policy_a | 1096.95 |
| mean_strategy_switches_policy_a | 10.47 |
| mean_missed_lethal_policy_a | 0.00 |
| mean_failed_counter_policy_a | 1.18 |
| mean_max_chain_policy_a | 2.65 |
| mean_sent_ojama_policy_a | 35.38 |
| mean_canceled_ojama_policy_a | 14.88 |
| profile_counts_policy_a | {"build_large": 563, "counter": 1006} |
| elo_delta_policy_a | 54.12 |
