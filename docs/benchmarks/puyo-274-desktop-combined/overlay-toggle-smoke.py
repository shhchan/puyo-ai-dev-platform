import json
from pathlib import Path
from copy import deepcopy
import pygame
from src.ui.versus_renderer import VersusRenderer
from eval.puyo_273_gui_probe import main

original = VersusRenderer.draw
captured = False
out = Path('docs/benchmarks/puyo-274-desktop-combined')

def draw(self, controller):
    global captured
    original(self, controller)
    if captured:
        return
    plan = controller.plan_overlay('player_0')
    if len(plan.get('steps', [])) != 3:
        return
    captured = True
    runtime = controller.controllers['player_0']
    before = (controller.env.match.tick, runtime.active_action_index, deepcopy(runtime.diagnostics.last_decision.to_json()))
    pygame.image.save(self.screen, str(out / 'overlay-on.png'))
    controller.handle_keydown(pygame.K_o)
    assert controller.plan_overlay('player_0') == {}
    original(self, controller)
    pygame.image.save(self.screen, str(out / 'overlay-off.png'))
    controller.handle_keydown(pygame.K_o)
    assert controller.plan_overlay('player_0') == plan
    after = (controller.env.match.tick, runtime.active_action_index, runtime.diagnostics.last_decision.to_json())
    assert before == after
    original(self, controller)
    (out / 'overlay-toggle-smoke.json').write_text(json.dumps({'unchanged_tick_action_receipt': True, 'tick': before[0], 'active_action': before[1], 'plan': plan}, indent=2)+'\n')

if __name__ == "__main__":
    VersusRenderer.draw = draw
    main()
    assert captured, 'No adopted three-step preview was rendered'
