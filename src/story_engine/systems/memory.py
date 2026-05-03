from typing import Dict, Any
import json
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity


class MemorySystem(System):
    """
    System responsible for managing long-term memory and context windows.
    It archives resolved step results and prunes the Observation buffer.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        intents_buffer = context.get("intents", [])
        simulation_result = context.get("simulation_result", {})
        visible_simulation_result = context.get("visible_simulation_result", simulation_result)
        rendered_text = context.get("rendered_text", "")
        timeline = context.get("timeline", {})
        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        visible_actor_names = set(context.get("visible_actor_names", []))
        player_location = context.get("player_pov", {}).get("location")
        
        if not intents_buffer and not simulation_result and not rendered_text:
            return

        full_intents_text = "\n".join(
            [f"- {item.get('actor', 'Unknown')}: {item.get('intent', '')}" for item in intents_buffer]
        )
        full_resolved_text = "\n".join(
            [
                f"- {item.get('actor', 'Unknown')} [{item.get('outcome', 'partial')}]: {item.get('result', '')}"
                for item in simulation_result.get("resolved_actions", [])
            ]
        )
        public_intents_text = "\n".join(
            [
                f"- {item.get('actor', 'Unknown')}: {item.get('intent', '')}"
                for item in intents_buffer
                if item.get("is_player") or item.get("actor") in visible_actor_names or item.get("location") == player_location
            ]
        )
        public_resolved_text = "\n".join(
            [
                f"- {item.get('actor', 'Unknown')} [{item.get('outcome', 'partial')}]: {item.get('result', '')}"
                for item in visible_simulation_result.get("resolved_actions", [])
            ]
        )
        timeline_text = json.dumps(timeline, ensure_ascii=False)
        state_updates = json.dumps(simulation_result.get("state_updates", {}), ensure_ascii=False)
        plot_updates = json.dumps(simulation_result.get("plot_updates", []), ensure_ascii=False)

        full_memory_text = (
            f"Step {current_step}\n"
            f"Timeline:\n{timeline_text}\n"
            f"Intents:\n{full_intents_text or '- None'}\n"
            f"Resolved:\n{full_resolved_text or '- None'}\n"
            f"State Updates:\n{state_updates}\n"
            f"Plot Updates:\n{plot_updates}\n"
            f"Rendered:\n{rendered_text or 'None'}"
        )
        public_memory_text = (
            f"Step {current_step}\n"
            f"Timeline:\n{timeline_text}\n"
            f"Visible Intents:\n{public_intents_text or '- None'}\n"
            f"Visible Resolved:\n{public_resolved_text or '- None'}\n"
            f"Rendered:\n{rendered_text or 'None'}"
        )
        
        archived_count = 0
        for entity_name, entity in entities.items():
            memory_comp = entity.get_component("Memory")
            if memory_comp:
                memory_text = None
                if entity_name == "GameMaster":
                    memory_text = full_memory_text
                elif entity_name in visible_actor_names:
                    memory_text = public_memory_text
                else:
                    own_records = [
                        item for item in simulation_result.get("resolved_actions", [])
                        if item.get("actor") == entity_name
                    ]
                    if own_records:
                        own_text = "\n".join(
                            [f"- {item.get('outcome', 'partial')}: {item.get('result', '')}" for item in own_records]
                        )
                        memory_text = f"Step {current_step}\nPersonal Outcome:\n{own_text}"

                if memory_text:
                    memory_comp.add_memory(
                        content=memory_text,
                        metadata={
                            "step": current_step,
                            "type": "episodic_log",
                            "phase_model": "input-simulation-rendering",
                        }
                    )
                    archived_count += 1
                
            obs_comp = entity.get_component("Observation")
            if obs_comp and hasattr(obs_comp, "prune"):
                obs_comp.prune(keep_n=20)
                
        print(f"    -> Archived episodic memory for {archived_count} entities.")
        self.logger.info(f"Archived memory for Step {current_step}")
