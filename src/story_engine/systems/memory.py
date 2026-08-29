from typing import Dict, Any
import json
import hashlib
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.agents.memory_consolidation import MemoryConsolidator
from src.story_engine.agents.runtime import runtime_owns_subjective_state


class MemorySystem(System):
    """
    System responsible for managing long-term memory and context windows.
    It archives resolved step results and prunes the Observation buffer.
    """
    def __init__(self) -> None:
        super().__init__()
        self.consolidator = MemoryConsolidator()

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        intents_buffer = context.get("intents", [])
        simulation_result = context.get("simulation_result", {})
        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        
        if (
            not intents_buffer
            and not simulation_result
            and not context.get("world_event_updates")
        ):
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
        state_updates = json.dumps(simulation_result.get("state_updates", {}), ensure_ascii=False)
        object_lifecycle = json.dumps(
            simulation_result.get("object_lifecycle", []), ensure_ascii=False
        )
        exchanges = json.dumps(
            simulation_result.get("exchanges", []), ensure_ascii=False
        )
        world_event_updates = json.dumps(
            context.get("world_event_updates", []), ensure_ascii=False
        )
        semantic_gm_memory_text = (
            f"Step {current_step}\n"
            f"Intents:\n{full_intents_text or '- None'}\n"
            f"Resolved:\n{full_resolved_text or '- None'}\n"
            f"State Updates:\n{state_updates}\n"
            f"Object Lifecycle:\n{object_lifecycle}\n"
            f"Exchanges:\n{exchanges}\n"
            f"World Events:\n{world_event_updates}"
        )
        scene_state = next(
            (
                entity.get_component("SceneState")
                for entity in entities.values()
                if entity.get_component("SceneState") is not None
            ),
            None,
        )
        archived_count = 0
        for entity_name, entity in entities.items():
            memory_comp = entity.get_component("Memory")
            if memory_comp and not runtime_owns_subjective_state(entity):
                memory_text = None
                if entity_name == "GameMaster":
                    memory_text = semantic_gm_memory_text
                else:
                    memory_text = self._actor_experience_memory(
                        entity,
                        current_step=current_step,
                        scene_state=scene_state,
                    )
                    own_records = [
                        item for item in simulation_result.get("resolved_actions", [])
                        if item.get("actor") == entity_name
                    ]
                    if not memory_text and own_records:
                        own_text = "\n".join(
                            [
                                f"- {item.get('outcome', 'partial')}: "
                                f"{item.get('result', '')}"
                                + (
                                    f" 私有发现：{item.get('private_result')}"
                                    if item.get("private_result")
                                    else ""
                                )
                                for item in own_records
                            ]
                        )
                        memory_text = f"Step {current_step}\nPersonal Outcome:\n{own_text}"

                own_goal_transitions = [
                    item
                    for item in context.get("goal_transitions", [])
                    if isinstance(item, dict) and item.get("actor") == entity_name
                ]
                if own_goal_transitions:
                    goal_text = "\n".join(
                        f"- {item.get('title', item.get('goal_id', '目标'))}: "
                        f"{item.get('status', '')}（{item.get('reason', '')}）"
                        for item in own_goal_transitions
                    )
                    if memory_text:
                        memory_text += "\nPrivate Goal Transition:\n" + goal_text
                    else:
                        memory_text = (
                            f"Step {current_step}\nPrivate Goal Transition:\n{goal_text}"
                        )

                own_modifier_changes = [
                    item
                    for item in (
                        list(context.get("modifier_updates", []))
                        + list(context.get("modifier_transitions", []))
                    )
                    if isinstance(item, dict)
                    and (item.get("target") or item.get("actor")) == entity_name
                ]
                if own_modifier_changes:
                    modifier_text = "\n".join(
                        f"- {item.get('kind', item.get('modifier_id', 'modifier'))}: "
                        f"{item.get('operation', item.get('status', 'changed'))}"
                        for item in own_modifier_changes
                    )
                    if memory_text:
                        memory_text += "\nPrivate Modifier Change:\n" + modifier_text
                    else:
                        memory_text = (
                            f"Step {current_step}\nPrivate Modifier Change:\n"
                            f"{modifier_text}"
                        )

                own_claim_updates = [
                    item
                    for item in context.get("claim_knowledge_updates", [])
                    if isinstance(item, dict)
                    and (item.get("actor") or item.get("target")) == entity_name
                ]
                if own_claim_updates:
                    claim_text = "\n".join(
                        f"- {item.get('claim_id', 'claim')}: "
                        f"{item.get('operation', 'updated')}"
                        for item in own_claim_updates
                    )
                    if memory_text:
                        memory_text += "\nPrivate Claim Knowledge:\n" + claim_text
                    else:
                        memory_text = (
                            f"Step {current_step}\nPrivate Claim Knowledge:\n"
                            f"{claim_text}"
                        )

                own_world_events = [
                    item
                    for item in context.get("world_event_updates", [])
                    if isinstance(item, dict)
                    and entity_name
                    in set(item.get("direct_witnesses", []))
                    .union(item.get("self_witnesses", []))
                ]
                if own_world_events:
                    event_text = "\n".join(
                        f"- {item.get('event_id', 'event')}: "
                        f"{item.get('statement', '')}"
                        for item in own_world_events
                    )
                    if memory_text:
                        memory_text += "\nPrivate World Event:\n" + event_text
                    else:
                        memory_text = (
                            f"Step {current_step}\nPrivate World Event:\n{event_text}"
                        )

                own_sentiment_updates = [
                    item
                    for item in context.get("sentiment_updates", [])
                    if isinstance(item, dict)
                    and item.get("affected") == entity_name
                ]
                own_resolved_actions = [
                    item
                    for item in simulation_result.get("resolved_actions", [])
                    if isinstance(item, dict) and item.get("actor") == entity_name
                ]
                own_object_changes = [
                    item
                    for item in simulation_result.get("object_lifecycle", [])
                    if isinstance(item, dict) and item.get("actor") == entity_name
                ]
                salience = 1.0
                event_kinds = []
                if own_world_events:
                    salience += 2.0
                    event_kinds.append("world_event")
                if own_resolved_actions:
                    salience += 0.5
                    event_kinds.append("action")
                if own_object_changes:
                    salience += 1.5
                    event_kinds.append("object")
                if own_goal_transitions:
                    salience += 3.0
                    event_kinds.append("goal")
                if own_claim_updates:
                    salience += 2.5
                    event_kinds.append("claim")
                if own_sentiment_updates:
                    salience += min(
                        2.0,
                        max(
                            float(item.get("intensity", 0.0) or 0.0)
                            for item in own_sentiment_updates
                        )
                        * 2.0,
                    )
                    event_kinds.append("sentiment")
                if own_modifier_changes:
                    salience += 0.75
                    event_kinds.append("modifier")
                salience = min(8.0, salience)

                if memory_text:
                    memory_id = "episodic-" + hashlib.sha256(
                        (
                            f"{context.get('memory_namespace', '')}|"
                            f"{entity_name}|{int(current_step)}|episodic_log"
                        ).encode("utf-8")
                    ).hexdigest()
                    memory_metadata = {
                            "step": current_step,
                            "type": "episodic_log",
                            "phase_model": "input-action-events-simulation-rendering",
                            "salience": salience,
                            "event_kinds": ",".join(event_kinds) or "ordinary",
                        }
                    try:
                        memory_comp.add_memory(
                            content=memory_text,
                            metadata=memory_metadata,
                            memory_id=memory_id,
                        )
                    except TypeError:
                        memory_comp.add_memory(
                            content=memory_text,
                            metadata=memory_metadata,
                        )
                    archived_count += 1
                    if entity_name != "GameMaster":
                        traces = context.setdefault(
                            "memory_consolidation_traces", {}
                        )
                        if entity_name not in traces:
                            traces[entity_name] = self.consolidator.maybe_consolidate(
                                memory_comp,
                                current_step=current_step,
                            )
                
            obs_comp = entity.get_component("Observation")
            if obs_comp and hasattr(obs_comp, "prune"):
                obs_comp.prune(keep_n=20)
                
        print(f"    -> Archived episodic memory for {archived_count} entities.")
        self.logger.info(f"Archived memory for Step {current_step}")

    @staticmethod
    def _actor_experience_memory(
        entity: Entity,
        *,
        current_step: int,
        scene_state: Any,
    ) -> str:
        cognition = entity.get_component("Cognition")
        experiences = list(getattr(cognition, "experiences", []) or [])
        events = []
        seen = set()
        for experience in experiences:
            if not isinstance(experience, dict):
                continue
            try:
                experience_step = int(experience.get("step", -1))
            except (TypeError, ValueError):
                continue
            if experience_step != int(current_step):
                continue
            for event in experience.get("events", []) or []:
                if not isinstance(event, dict):
                    continue
                signature = json.dumps(event, ensure_ascii=False, sort_keys=True)
                if signature in seen:
                    continue
                seen.add(signature)
                events.append(event)
        if not events:
            return ""

        lines = []
        for event in events:
            actor = str(event.get("actor") or "World").strip()
            outcome = str(event.get("outcome") or "occurred").strip()
            result = str(event.get("result") or "").strip()
            private_result = str(event.get("private_result") or "").strip()
            if result:
                lines.append(f"- {actor} [{outcome}]: {result}")
            if private_result:
                lines.append(f"- Private discovery: {private_result}")
        if not lines:
            return ""

        public_scene = (
            scene_state.get_public_scene_state()
            if scene_state is not None
            and hasattr(scene_state, "get_public_scene_state")
            else {}
        )
        return (
            f"Step {int(current_step)}\n"
            f"Public Scene:\n{json.dumps(public_scene, ensure_ascii=False)}\n"
            f"Personally Observed Outcomes:\n" + "\n".join(lines)
        )
