from typing import List, Dict, Any, Optional, Callable
from src.story_engine.clocks.game_clock import GameClock
from src.story_engine.core.entity import Entity
from src.story_engine.core.logger import logger
from src.story_engine.environment.dispatcher import Dispatcher
from src.story_engine.systems import System, InputSystem, SimulationSystem, RenderingSystem
from src.story_engine.systems.memory import MemorySystem


def _default_phase_order() -> List[str]:
    return ["InputSystem", "SimulationSystem", "RenderingSystem", "MemorySystem"]

class Runner:
    """
    The main engine runner that orchestrates the simulation loop.
    It manages entities and executes systems in a defined order.
    Supports optional human-in-the-loop: world_edits, inject_events, on_phase_done callback.
    """
    def __init__(self, clock: GameClock = None):
        self.clock = clock or GameClock()
        self.entities: Dict[str, Entity] = {}
        self.dispatcher = Dispatcher()
        self.logger = logger
        self.systems: List[System] = [
            InputSystem(),
            SimulationSystem(),
            RenderingSystem(),
            MemorySystem()
        ]
        self._phase_order = _default_phase_order()

    def add_entity(self, entity: Entity):
        self.entities[entity.name] = entity
        self.logger.info(f"Registered entity: {entity.name}")

    def _apply_world_edits(self, world_edits: List[tuple]):
        """Apply (object_name, {key: value}) to GM's SceneState."""
        for name, entity in self.entities.items():
            scene = entity.get_component("SceneState")
            if scene:
                for obj_name, state in world_edits:
                    scene.update_object_state(obj_name, state)
                self.logger.info(f"World edits applied via {name}")
                break

    def run_step(
        self,
        overrides: Dict[str, str] = None,
        world_edits: Optional[List[tuple]] = None,
        inject_events: Optional[List[str]] = None,
        player_name: Optional[str] = None,
        on_phase_done: Optional[Callable[[str, Dict[str, Any], Dict[str, Entity]], Optional[Dict[str, Any]]]] = None,
    ):
        """
        Executes one simulation step.

        - overrides: entity_name -> intent string, or GM render override string for this step.
        - world_edits: list of (object_name, {property: value}) to apply to GM's SceneState before the step.
        - inject_events: list of strings added to the Input phase as world-originated intents/events.
        - on_phase_done: callback(phase_name, context, entities) called after each system; return dict to merge into context for remaining systems.
        """
        overrides = overrides or {}
        context = {
            "dispatcher": self.dispatcher,
            "overrides": overrides,
            "clock": self.clock,
            "player_name": player_name,
            "inject_events": list(inject_events) if inject_events else [],
            "intents": [],
        }

        if world_edits:
            self._apply_world_edits(world_edits)

        print(f"\n=== Step {self.clock.current_step} : {self.clock.get_time_display()} ===")
        self.logger.info(f"Starting Step {self.clock.current_step}")
        self.clock.tick()

        for system in self.systems:
            system_name = system.__class__.__name__
            print(f"  [System] {system_name} running...")
            try:
                system.update(self.entities, context)
            except Exception as e:
                self.logger.error(f"Error in system {system_name}: {e}", exc_info=True)
                print(f"Error executing system {system_name}: {e}")

            if on_phase_done:
                out = on_phase_done(system_name, context, self.entities)
                if isinstance(out, dict):
                    overrides_update = out.pop("overrides", None)
                    context.update(out)
                    if overrides_update:
                        context.setdefault("overrides", {}).update(overrides_update)

        print(f"=== Step {self.clock.current_step} Complete ===\n")
