from typing import Dict, Any
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.config.config import config

class NarrativeSystem(System):
    """
    System responsible for the Game Master's narrative control.
    Supports optional INTRODUCE_CHARACTER from GM to spawn new agents at runtime.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        dispatcher = context.get("dispatcher")
        
        for name, entity in entities.items():
            narrative_comp = entity.get_component("NarrativeControl")
            if not narrative_comp:
                continue
            
            outcome = None
            if name in overrides:
                outcome = overrides[name]
                print(f"\n> GM (MANUAL): {outcome}\n")
                self.logger.info(f"GM (MANUAL): {outcome}")
            else:
                if hasattr(narrative_comp, "act"):
                    print(f"... {name} is thinking ...", end="\r", flush=True)
                    outcome = narrative_comp.act()
                    print(" " * 40 + "\r", end="", flush=True)

            # Unpack dict outcome (narration + optional introduce_character)
            narration = outcome
            introduce_character = None
            if isinstance(outcome, dict):
                narration = outcome.get("narration", "")
                introduce_character = outcome.get("introduce_character")

            if narration:
                prefix = "World Engine"
                print(f"\n> {prefix}: {narration}\n")
                self.logger.info(f"{prefix}: {narration}")
                if dispatcher:
                    dispatcher.publish({"type": "narration", "content": narration})
                narrator_msg = f"{prefix}: {narration}"
                if "actions" in context:
                    context["actions"].append(narrator_msg)
                for e in entities.values():
                    obs_comp = e.get_component("Observation")
                    if obs_comp and hasattr(obs_comp, "add_observation"):
                        obs_comp.add_observation(narrator_msg)

            # Spawn new character if GM requested
            if introduce_character and isinstance(introduce_character, dict):
                c = introduce_character
                new_name = c.get("name", "").strip()
                if new_name and new_name not in entities:
                    role = c.get("role", "路人")
                    personality = c.get("personality", "未知")
                    goals = c.get("goals") or []
                    if isinstance(goals, str):
                        goals = [goals]
                    base_cfg = config.get_component_config("agent").copy()
                    new_entity = create_agent(
                        name=new_name,
                        role=role,
                        personality=personality,
                        goals=goals,
                        model_config=base_cfg,
                    )
                    entities[new_name] = new_entity
                    print(f"    [New Character] {new_name} ({role}) joined the story.")
                    self.logger.info(f"GM introduced new character: {new_name}")
