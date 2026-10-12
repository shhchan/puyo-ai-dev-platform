"""Fixed shared/template/response budgets, chosen before tactic selection."""

from agents.long_horizon_search import (
    FUTURE_SAMPLING_LEGACY_FIXED_SIX,
    LongHorizonSearchConfig,
)
from agents.nextgen_contracts import SearchProfile

DEFAULT_NEXTGEN_PROFILE = "nextgen_safe_build"
NEXTGEN_PROFILES = {
    "nextgen_safe_build": (600000, 128, 256),
    "nextgen_smoke": (256, 128, 256),
    "nextgen_diagnostic": (512, 256, 512),
}
NEXTGEN_PROFILE_CHOICES = tuple(NEXTGEN_PROFILES)


def nextgen_search_settings(profile_id=DEFAULT_NEXTGEN_PROFILE, *, seed=0,
                            fire_goal_score=None):
    if fire_goal_score is not None and (type(fire_goal_score) is not int or fire_goal_score <= 0):
        raise ValueError("fire_goal_score must be a positive integer")
    quotas = NEXTGEN_PROFILES[profile_id]
    reference = profile_id == "nextgen_safe_build"
    config = LongHorizonSearchConfig(
        depth=16 if reference else 4,
        width=250 if reference else 4,
        scenarios=6 if reference else 1,
        minimum_chain_count=10,
        winning_score_threshold=fire_goal_score,
        max_expanded_nodes=quotas[0],
        decision_seed=seed ^ 0x4E4753,
        **({"future_sampling_mode": FUTURE_SAMPLING_LEGACY_FIXED_SIX} if reference else {}),
    )
    return SearchProfile(profile_id, *quotas), config
