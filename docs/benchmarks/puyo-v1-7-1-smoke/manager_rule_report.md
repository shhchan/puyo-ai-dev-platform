# Arena Report: v1_7_1_vs_manager_rule

- policy_a: `v1_7_bootstrap_manager`
- policy_b: `manager_rule`
- checkpoint_a: `runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt`
- checkpoint_b: `-`
- seed: `123`
- max_steps: `40`

| metric | value |
|---|---:|
| games | 40 |
| wins_player_0 | 23 |
| wins_player_1 | 17 |
| draws | 0 |
| win_rate_policy_a | 0.675 |
| score_rate_policy_a_ci95 | 0.675 [0.528, 0.822] |
| mean_score_player_0 | 3051.75 |
| mean_score_player_1 | 2113.75 |
| mean_max_chain_player_0 | 2.52 |
| mean_max_chain_player_1 | 2.23 |
| mean_decision_ms_policy_a | 155.31 |
| mean_expanded_nodes_policy_a | 1066.51 |
| mean_strategy_switches_policy_a | 5.25 |
| mean_missed_lethal_policy_a | 0.00 |
| mean_failed_counter_policy_a | 0.97 |
| mean_max_chain_policy_a | 2.52 |
| mean_sent_ojama_policy_a | 31.95 |
| mean_canceled_ojama_policy_a | 1.50 |
| profile_counts_policy_a | {"build_large": 368, "counter": 811} |
| elo_delta_policy_a | 62.26 |
