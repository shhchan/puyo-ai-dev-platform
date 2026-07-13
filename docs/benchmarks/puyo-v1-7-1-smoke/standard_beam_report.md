# Arena Report: v1_7_1_vs_standard_beam

- policy_a: `v1_7_bootstrap_manager`
- policy_b: `beam`
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
| win_rate_policy_a | 0.775 |
| score_rate_policy_a_ci95 | 0.775 [0.644, 0.906] |
| mean_score_player_0 | 3333.25 |
| mean_score_player_1 | 3127.00 |
| mean_max_chain_player_0 | 2.23 |
| mean_max_chain_player_1 | 1.85 |
| mean_decision_ms_policy_a | 155.73 |
| mean_expanded_nodes_policy_a | 1049.33 |
| mean_strategy_switches_policy_a | 4.50 |
| mean_missed_lethal_policy_a | 0.00 |
| mean_failed_counter_policy_a | 0.65 |
| mean_max_chain_policy_a | 2.27 |
| mean_sent_ojama_policy_a | 30.80 |
| mean_canceled_ojama_policy_a | 0.07 |
| profile_counts_policy_a | {"build_large": 318, "counter": 725} |
| elo_delta_policy_a | 95.47 |
