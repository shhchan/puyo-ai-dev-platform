"""Real controller fixture shared by garbage lifecycle tests and GUI captures."""

from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig
from puyo_env.actions import placement_to_action_index
from selfplay.policies import legal_indices
from src.core.constants import Direction
from src.core.headless import PlacementAction


class SpreadPolicy:
    """Keep the choke column clear while observing successive five-row drops."""

    def __init__(self):
        self.decisions = 0

    def select_action(self, observation, info):
        column = (0, 4, 2)[self.decisions % 3]
        self.decisions += 1
        action = placement_to_action_index(PlacementAction(column, Direction.RIGHT))
        legal = legal_indices(info)
        return action if action in legal else legal[0]


def make_garbage_controller(agent="player_1", amount=65, **config):
    controller = RealtimeVersusMatchController(
        RealtimeVersusUiConfig(policy_a="first", policy_b="first", max_ticks=500, **config),
        policy_factory=lambda *_args, **_kwargs: SpreadPolicy(),
    )
    attacker = "player_1" if agent == "player_0" else "player_0"
    controller.env.match.schedule_attack(attacker, amount)
    return controller
