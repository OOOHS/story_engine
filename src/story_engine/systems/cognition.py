from typing import Any, Dict, List

from src.story_engine.attention import HostAttentionPolicy
from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System
from src.story_engine.common.action_features import resolve_social_response_kind
from src.story_engine.common.observation_window import (
    actor_observation_locations,
    shares_action_location,
)
from src.story_engine.agents.observations import observation_mode_for_action
from src.story_engine.components.world_event import WorldEventResponses
from src.story_engine.motivation import reactivate_relevant_agent_goal


class CognitionSystem(System):
    """Archives structured, personally observable outcomes after Simulation."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        result = context.get("simulation_result", {})
        actions = (
            list(result.get("resolved_actions", []))
            + list(result.get("action_feedback", []))
            if isinstance(result, dict)
            else []
        )
        if not actions:
            return

        scene_state = self._get_scene_state(entities)
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        update_counts: Dict[str, int] = {}
        context["knowledge_transfers"] = self._apply_knowledge_updates(
            entities=entities,
            scene_state=scene_state,
            actions=actions,
            updates=result.get("knowledge_updates", []),
            step=step,
            observation_windows=context.get("actor_observation_windows", {}),
        )
        for transfer in context["knowledge_transfers"]:
            if isinstance(transfer, dict) and transfer.get("goal_reactivation"):
                context.setdefault("goal_reactivations", []).append(
                    transfer["goal_reactivation"]
                )

        for name, entity in entities.items():
            cognition = entity.get_component("Cognition")
            if not cognition or not hasattr(cognition, "record_experience"):
                continue
            observer_locations = actor_observation_locations(
                name,
                scene_state,
                context.get("actor_observation_windows", {}),
            )
            observable = self._observable_actions(
                name,
                observer_locations,
                actions,
            )
            if not observable:
                continue
            cognition.record_experience(step=step, events=observable)
            update_counts[name] = len(observable)

        context["cognition_updates"] = update_counts

    def _apply_knowledge_updates(
        self,
        entities: Dict[str, Entity],
        scene_state: Any,
        actions: List[Dict[str, Any]],
        updates: Any,
        step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        applied = []
        if not scene_state or not isinstance(updates, list):
            return applied
        for update in updates:
            if not isinstance(update, dict):
                continue
            goal_reactivation = None
            if str(update.get("claim_id", "")).strip():
                # Objective Claim references are handled by ClaimKnowledgeSystem.
                continue
            source = str(update.get("source", "")).strip()
            target = str(update.get("target", "")).strip()
            event_id = " ".join(
                str(update.get("event_id", "")).split()
            ).strip()[:180]
            statement = " ".join(str(update.get("statement", "")).split()).strip()[:500]
            reason = " ".join(str(update.get("reason", "")).split()).strip()[:300]
            mode = str(update.get("mode", "told")).strip()
            if not source or not target or source == target or not reason:
                continue
            if mode != "told" or source not in entities or target not in entities:
                continue
            source_cognition = entities[source].get_component("Cognition")
            target_cognition = entities[target].get_component("Cognition")
            if not source_cognition or not target_cognition:
                continue
            if event_id:
                if not source_cognition.knows_event(event_id):
                    continue
                event_entity = entities.get(f"WorldEvent:{event_id}")
                event_fact = (
                    event_entity.get_component("WorldEventFact")
                    if event_entity is not None
                    else None
                )
                if event_fact is None:
                    continue
                statement = event_fact.statement
            elif not statement or not source_cognition.knows(statement):
                continue
            communication = next((
                action
                for action in actions
                if isinstance(action, dict)
                and str(action.get("actor", "")).strip() == source
                and str(action.get("action_kind", "")).strip() == "communicate"
                and str(action.get("outcome", "")).strip()
                in {"success", "partial", "complication"}
                and (
                    not str(action.get("action_target", "")).strip()
                    or str(action.get("action_target", "")).strip() == target
                )
            ), None)
            if communication is None:
                continue
            communication_location = str(
                communication.get("location", "")
            ).strip()
            if not shares_action_location(
                source,
                target,
                communication_location,
                scene_state,
                observation_windows,
            ):
                continue
            if event_id:
                response_kind = resolve_social_response_kind(
                    (
                        communication.get("intent")
                        or communication.get("result")
                        or ""
                    ),
                    update.get("response_kind", "report"),
                )
                confidence = 0.65
                target_cognition.record_world_event(
                    event_id=event_id,
                    statement=statement,
                    step=step,
                    location=communication_location,
                    witness_mode="reported",
                    confidence=confidence,
                    enqueue_attention=self._attention_enabled(entities[target]),
                    attention_priority=HostAttentionPolicy.event_priority(
                        event_fact, target
                    ),
                )
                responses = event_entity.get_component("WorldEventResponses")
                if responses is None:
                    responses = WorldEventResponses()
                    event_entity.add_component(responses)
                created_response = responses.record_communication(
                    source,
                    target,
                    step,
                    response_kind=response_kind,
                    event_id=event_id,
                )
                response_id = responses.response_id_for(
                    event_id,
                    source,
                    target,
                    response_kind if response_kind in responses.RESPONSE_KINDS else "report",
                )
                if created_response:
                    target_cognition.record_event_response(
                        response_id=response_id,
                        event_id=event_id,
                        source=source,
                        response_kind=(
                            response_kind
                            if response_kind in responses.RESPONSE_KINDS
                            else "report"
                        ),
                        statement=statement,
                        step=step,
                        location=communication_location,
                        enqueue_attention=self._attention_enabled(entities[target]),
                        attention_priority=HostAttentionPolicy.response_priority(
                            response_kind
                        ),
                    )
                    goal_reactivation = reactivate_relevant_agent_goal(
                        entities[target],
                        event_id=event_id,
                        references=[
                            source,
                            target,
                            event_fact.location,
                            *list(event_fact.subjects),
                            *list(event_fact.objects),
                        ],
                        impacts=[
                            *list(event_fact.impacts),
                            {
                                "scope": "world_event",
                                "target": event_id,
                                "path": "responses",
                            },
                            {
                                "scope": "world_event",
                                "target": event_id,
                                "path": "communications",
                            },
                        ],
                        step=step,
                        reason=f"event response received:{response_kind}",
                    )
                    if goal_reactivation:
                        goal_reactivation["response_id"] = response_id
            else:
                response_kind = ""
                response_id = ""
                try:
                    confidence = min(1.0, max(0.0, float(update.get("confidence", 0.8))))
                except (TypeError, ValueError):
                    confidence = 0.8
                target_cognition.apply_agent_updates(
                    {
                        "belief_updates": [
                            {
                                "statement": statement,
                                "confidence": confidence,
                                "source": f"told_by:{source}",
                            }
                        ]
                    },
                    step=step,
                )
            applied.append(
                {
                    "source": source,
                    "target": target,
                    "statement": statement,
                    "event_id": event_id,
                    "confidence": confidence,
                    "response_kind": response_kind,
                    "response_id": response_id,
                    "reason": reason,
                    "goal_reactivation": goal_reactivation,
                }
            )
        return applied

    def _observable_actions(
        self,
        observer_name: str,
        observer_locations: Any,
        actions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        locations = {
            str(item).strip()
            for item in (observer_locations or [])
            if str(item).strip()
        }
        observable: List[Dict[str, Any]] = []
        for item in actions or []:
            if not isinstance(item, dict):
                continue
            personal = item.get("actor") == observer_name
            visibility = str(item.get("visibility", "public")).strip()
            same_location = bool(
                locations
                and item.get("location")
                and str(item.get("location")).strip() in locations
            )
            if not personal and (visibility == "hidden" or not same_location):
                continue
            observed = dict(item)
            observed["personal"] = personal
            observed["observation_mode"] = observation_mode_for_action(
                personal=personal,
                action_kind=str(item.get("action_kind", "")),
            )
            if not personal:
                observed.pop("private_result", None)
            observable.append(observed)
        return observable

    def _get_scene_state(self, entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("SceneState")
            if state is not None:
                return state
        return None

    @staticmethod
    def _attention_enabled(entity: Entity) -> bool:
        controller = entity.get_component("AgentController")
        if controller is None:
            return True
        return bool(controller.autonomous)
