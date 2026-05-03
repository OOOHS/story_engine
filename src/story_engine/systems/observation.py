from typing import Dict, Any
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity

class ObservationSystem(System):
    """
    System responsible for distributing observations to entities.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        actions_buffer = context.get("actions", [])
        
        if not actions_buffer:
            # print("    No actions to distribute.")
            return

        observation_text = "\n".join(actions_buffer)
        count = 0
        for entity in entities.values():
            obs_comp = entity.get_component("Observation")
            if obs_comp and hasattr(obs_comp, "add_observation"):
                obs_comp.add_observation(observation_text)
                count += 1
        
        print(f"    -> Distributed {len(actions_buffer)} events to {count} entities.")
