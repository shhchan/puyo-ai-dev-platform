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


def nextgen_search_settings(profile_id=DEFAULT_NEXTGEN_PROFILE, *, seed=0):
    quotas = NEXTGEN_PROFILES[profile_id]
    reference = profile_id == "nextgen_safe_build"
    config = LongHorizonSearchConfig(
        depth=16 if reference else 4,
        width=250 if reference else 4,
        scenarios=6 if reference else 1,
        minimum_chain_count=10,
        max_expanded_nodes=quotas[0],
        decision_seed=seed ^ 0x4E4753,
        **({"future_sampling_mode": FUTURE_SAMPLING_LEGACY_FIXED_SIX} if reference else {}),
    )
    return SearchProfile(profile_id, *quotas), config
