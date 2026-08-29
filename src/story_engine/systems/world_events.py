from copy import deepcopy
import hashlib
from typing import Any, Dict, List
from uuid import NAMESPACE_URL, uuid5

from src.story_engine.attention import HostAttentionPolicy
from src.story_engine.common.observation_window import actor_observation_locations
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.world_event import (
    WorldEventImpact,
    WorldEventResponses,
    WorldEventFact,
    WorldEventWitnesses,
)
from src.story_engine.core.entity import Entity
from src.story_engine.motivation import (
    reactivate_relevant_agent_goal,
    relevant_goal_match,
)
from src.story_engine.systems.system import System


class WorldEventSystem(System):
    """Materialize host-derived occurrences and publish only to witnesses."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        scene_state = self._scene_state(entities)
        simulation_result = context.get("simulation_result")
        if isinstance(simulation_result, dict):
            simulation_result["topology_changes"] = deepcopy(
                context.get("topology_changes", []) or []
            )
            simulation_result["host_object_state_changes"] = deepcopy(
                context.get("host_object_state_changes", []) or []
            )
        candidates = self._collect_candidates(entities, context, scene_state)
        if not candidates:
            context["world_event_updates"] = []
            context["world_event_errors"] = []
            return

        prepared: List[tuple[str, Entity, Dict[str, Any]]] = []
        errors: List[str] = []
        seen = set()
        for index, raw in enumerate(candidates):
            prefix = f"world_events[{index}]"
            if not isinstance(raw, dict):
                errors.append(f"{prefix} must be an object")
                continue
            event_id = self._text(raw.get("event_id"), 180)
            statement = self._text(raw.get("statement"), 800)
            if not event_id or not statement or event_id in seen:
                errors.append(f"{prefix} requires a unique event_id and statement")
                continue
            seen.add(event_id)
            entity_name = f"WorldEvent:{event_id}"
            existing = entities.get(entity_name)
            if existing is not None:
                fact = existing.get_component("WorldEventFact")
                if fact is None or fact.event_id != event_id:
                    errors.append(f"{prefix} collides with an existing entity")
                continue
            subjects = self._text_list(raw.get("subjects", []), 32, 120)
            unknown_subjects = (
                set(subjects).difference(scene_state.actor_states)
                if scene_state
                else set(subjects)
            )
            if unknown_subjects:
                errors.append(
                    f"{prefix} has unknown subjects: {sorted(unknown_subjects)}"
                )
                continue
            direct = self._text_list(raw.get("direct_witnesses", []), 64, 120)
            self_witnesses = self._text_list(
                raw.get("self_witnesses", []), 32, 120
            )
            event_entity = Entity(
                entity_name,
                entity_id=str(
                    uuid5(NAMESPACE_URL, f"story-engine:world-event:{event_id}")
                ),
            )
            event_entity.add_component(
                WorldEventFact(
                    event_id=event_id,
                    kind=self._text(raw.get("kind"), 80) or "timeline_event",
                    title=self._text(raw.get("title"), 240),
                    statement=statement,
                    occurred_step=int(raw.get("occurred_step", 0)),
                    location=self._text(raw.get("location"), 160),
                    subjects=subjects,
                    objects=self._text_list(raw.get("objects", []), 32, 120),
                    source_type=self._text(raw.get("source_type"), 40)
                    or "host_transition",
                    source_ref=self._text(raw.get("source_ref"), 180),
                    visibility=self._visibility(raw.get("visibility")),
                    impacts=self._impacts(raw.get("impacts", [])),
                    metadata={
                        "present_participants": self._text_list(
                            raw.get("present_participants", []), 32, 120
                        ),
                        "missing_participants": self._text_list(
                            raw.get("missing_participants", []), 32, 120
                        ),
                        "origin_location": self._text(
                            raw.get("origin_location"), 160
                        ),
                        "destination_location": self._text(
                            raw.get("destination_location"), 160
                        ),
                        "self_attention": bool(raw.get("self_attention", True)),
                        "changed_paths": self._text_list(
                            raw.get("changed_paths", []), 32, 120
                        ),
                        "affordance_id": self._text(
                            raw.get("affordance_id"), 120
                        ),
                    },
                )
            )
            event_entity.add_component(
                WorldEventWitnesses(
                    direct_witnesses=direct,
                    self_witnesses=self_witnesses,
                )
            )
            event_entity.add_component(WorldEventResponses())
            prepared.append((entity_name, event_entity, raw))

        if errors:
            context["world_event_updates"] = []
            context["world_event_errors"] = errors
            return

        cognition_snapshots = {
            name: Cognition(**deepcopy(cognition.model_dump()))
            for name, entity in entities.items()
            if (cognition := entity.get_component("Cognition")) is not None
        }
        controller_snapshots = {
            name: {
                "last_goal_wakeup_step": controller.last_goal_wakeup_step,
                "last_goal_wakeup_id": controller.last_goal_wakeup_id,
                "goal_reactivation_count": controller.goal_reactivation_count,
                "repeated_goal_action_count": controller.repeated_goal_action_count,
                "last_goal_action_signature": controller.last_goal_action_signature,
            }
            for name, entity in entities.items()
            if (controller := entity.get_component("AgentController")) is not None
        }
        inserted = []
        updates = []
        try:
            for entity_name, event_entity, raw in prepared:
                entities[entity_name] = event_entity
                inserted.append(entity_name)
                fact = event_entity.get_component("WorldEventFact")
                witnesses = event_entity.get_component("WorldEventWitnesses")
                attention_recipients = self._attention_recipients(
                    fact,
                    witnesses,
                    entities,
                    scene_state,
                    context.get("actor_observation_windows", {}),
                )
                witnesses.attention_recipients = sorted(attention_recipients)
                for actor in witnesses.direct_witnesses:
                    cognition = entities.get(actor).get_component("Cognition") if entities.get(actor) else None
                    if cognition is not None:
                        cognition.record_world_event(
                            event_id=fact.event_id,
                            statement=fact.statement,
                            step=fact.occurred_step,
                            location=self._direct_witness_location(
                                actor,
                                fact,
                                scene_state,
                            ),
                            witness_mode="direct",
                            enqueue_attention=actor in attention_recipients,
                            attention_priority=HostAttentionPolicy.event_priority(
                                fact, actor
                            ),
                        )
                        self._reactivate_goal(
                            entities.get(actor),
                            fact,
                            step=fact.occurred_step,
                            context=context,
                            reason="direct world event observation",
                        )
                direct_witnesses = set(witnesses.direct_witnesses)
                for actor in witnesses.self_witnesses:
                    if actor in direct_witnesses:
                        continue
                    cognition = entities.get(actor).get_component("Cognition") if entities.get(actor) else None
                    if cognition is not None:
                        cognition.record_world_event(
                            event_id=fact.event_id,
                            statement=fact.statement,
                            step=fact.occurred_step,
                            location=(
                                scene_state.get_actor_location(actor)
                                if scene_state
                                else ""
                            ),
                            witness_mode="self",
                            enqueue_attention=bool(
                                fact.metadata.get("self_attention", True)
                            ) and actor in attention_recipients,
                            attention_priority=HostAttentionPolicy.event_priority(
                                fact, actor
                            ),
                        )
                        self._reactivate_goal(
                            entities.get(actor),
                            fact,
                            step=fact.occurred_step,
                            context=context,
                            reason="self world event observation",
                        )
                updates.append(
                    {
                        "event_id": fact.event_id,
                        "kind": fact.kind,
                        "statement": fact.statement,
                        "location": fact.location,
                        "subjects": list(fact.subjects),
                        "objects": list(fact.objects),
                        "impacts": [item.model_dump() for item in fact.impacts],
                        "direct_witnesses": list(witnesses.direct_witnesses),
                        "self_witnesses": list(witnesses.self_witnesses),
                        "attention_recipients": list(
                            witnesses.attention_recipients
                        ),
                    }
                )
        except Exception as exc:
            for entity_name in inserted:
                entities.pop(entity_name, None)
            for name, snapshot in cognition_snapshots.items():
                cognition = entities.get(name).get_component("Cognition") if entities.get(name) else None
                if cognition is not None:
                    cognition.beliefs = deepcopy(snapshot.beliefs)
                    cognition.secrets = deepcopy(snapshot.secrets)
                    cognition.commitments = deepcopy(snapshot.commitments)
                    cognition.current_focus = snapshot.current_focus
                    cognition.experiences = deepcopy(snapshot.experiences)
                    cognition.pending_world_events = deepcopy(
                        snapshot.pending_world_events
                    )
                    cognition.pending_event_responses = deepcopy(
                        snapshot.pending_event_responses
                    )
                    cognition.world_event_attention = deepcopy(
                        snapshot.world_event_attention
                    )
                    cognition.event_response_attention = deepcopy(
                        snapshot.event_response_attention
                    )
            for name, snapshot in controller_snapshots.items():
                controller = (
                    entities.get(name).get_component("AgentController")
                    if entities.get(name)
                    else None
                )
                if controller is not None:
                    for field_name, value in snapshot.items():
                        setattr(controller, field_name, deepcopy(value))
            updates = []
            errors = [f"world event publication failed:{type(exc).__name__}:{exc}"]

        context["world_event_updates"] = updates
        context["world_event_errors"] = errors

    def _collect_candidates(
        self,
        entities: Dict[str, Entity],
        context: Dict[str, Any],
        scene_state: Any,
    ) -> List[Dict[str, Any]]:
        timeline = context.get("timeline", {})
        observation_windows = context.get("actor_observation_windows", {})
        candidates = self._topology_events(
            context.get("topology_changes", []),
            scene_state,
            observation_windows,
        )
        candidates.extend(
            self._object_state_events(
                {"object_state_changes": context.get("host_object_state_changes", [])},
                scene_state,
                current_step=self._step(context),
                observation_windows=observation_windows,
            )
        )
        candidates.extend(
            timeline.get("attendance_events", [])
            if isinstance(timeline, dict)
            else []
        )
        candidates.extend(
            self._phase_transition_events(
                timeline,
                scene_state,
                current_step=self._step(context),
            )
        )
        transaction = context.get("state_transaction", {})
        if transaction.get("committed"):
            candidates.extend(
                self._movement_events(
                    context.get("simulation_result", {}),
                    current_step=self._step(context),
                )
            )
            candidates.extend(
                self._object_state_events(
                    context.get("simulation_result", {}),
                    scene_state,
                    current_step=self._step(context),
                    observation_windows=observation_windows,
                )
            )
            candidates.extend(
                self._scene_state_events(
                    context.get("simulation_result", {}),
                    scene_state,
                    current_step=self._step(context),
                )
            )
            candidates.extend(
                self._object_events(
                    context.get("simulation_result", {}),
                    scene_state,
                    current_step=self._step(context),
                    observation_windows=observation_windows,
                )
            )
            candidates.extend(
                self._exchange_events(
                    context.get("simulation_result", {}),
                    scene_state,
                    current_step=self._step(context),
                    observation_windows=observation_windows,
                )
            )
        return candidates

    def _topology_events(
        self,
        changes: Any,
        scene_state: Any,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(changes, list) or scene_state is None:
            return []
        actors = sorted(scene_state.actor_states)
        events: List[Dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            change_id = self._text(change.get("change_id"), 220)
            operation = self._text(change.get("operation"), 20)
            source = self._text(change.get("source"), 160)
            target = self._text(change.get("target"), 160)
            statement = self._text(change.get("statement"), 800)
            visibility = self._visibility(change.get("visibility"))
            if not change_id or operation not in {"connect", "disconnect"}:
                continue
            if visibility == "public":
                witnesses = actors
            elif visibility == "local":
                witnesses = [
                    actor
                    for actor in actors
                    if actor_observation_locations(
                        actor,
                        scene_state,
                        observation_windows,
                    ).intersection({source, target})
                ]
            else:
                witnesses = []
            changed_arcs = [
                arc
                for arc in change.get("changed_arcs", []) or []
                if isinstance(arc, dict)
                and self._text(arc.get("source"), 160)
                and self._text(arc.get("target"), 160)
            ]
            impacts = [
                {
                    "scope": "world_object",
                    "target": self._text(arc.get("source"), 160),
                    "path": "connected_to",
                }
                for arc in changed_arcs
            ]
            events.append(
                {
                    "event_id": f"topology:{change_id}",
                    "kind": "route_opened" if operation == "connect" else "route_closed",
                    "title": "通路开放" if operation == "connect" else "通路中断",
                    "statement": statement,
                    "occurred_step": int(change.get("occurred_step", 0) or 0),
                    "location": source,
                    "subjects": [],
                    "objects": [source, target],
                    "direct_witnesses": witnesses,
                    "self_witnesses": [],
                    "source_type": "host_topology",
                    "source_ref": change_id,
                    "visibility": visibility,
                    "origin_location": source,
                    "destination_location": target,
                    "changed_paths": ["connected_to"],
                    "impacts": impacts,
                }
            )
        return events

    def _phase_transition_events(
        self,
        timeline: Any,
        scene_state: Any,
        *,
        current_step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        transition = (
            timeline.get("phase_transition", {})
            if isinstance(timeline, dict)
            else {}
        )
        previous = self._text(transition.get("from"), 80)
        current = self._text(transition.get("to"), 80)
        if not previous or not current or previous == current:
            return []
        actors = sorted(scene_state.actor_states) if scene_state else []
        return [
            {
                "event_id": f"scene-phase:{current_step}:{previous}->{current}",
                "kind": "scene_phase_changed",
                "title": "时间阶段变化",
                "statement": f"环境阶段从{previous}进入了{current}。",
                "occurred_step": current_step,
                "location": "",
                "subjects": [],
                "objects": [],
                "direct_witnesses": [],
                "self_witnesses": actors,
                "source_type": "clock",
                "source_ref": f"step:{int(current_step)}",
                "visibility": "public",
                "changed_paths": ["day_phase"],
                "impacts": [
                    {
                        "scope": "scene",
                        "target": "scene",
                        "path": "scene_flags.day_phase",
                    }
                ],
            }
        ]

    def _scene_state_events(
        self,
        result: Any,
        scene_state: Any,
        *,
        current_step: int,
    ) -> List[Dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        actors = sorted(scene_state.actor_states) if scene_state else []
        events = []
        for index, change in enumerate(result.get("scene_state_changes", []) or []):
            if not isinstance(change, dict):
                continue
            path = self._text(change.get("path"), 120)
            if not path:
                continue
            value = self._text(change.get("value"), 180)
            events.append(
                {
                    "event_id": f"scene-state:{current_step}:{index}:{path}",
                    "kind": "scene_state_changed",
                    "title": f"环境状态变化：{path}",
                    "statement": f"公共环境状态“{path}”发生了变化：{value}。",
                    "occurred_step": current_step,
                    "location": "",
                    "subjects": [],
                    "objects": [],
                    "direct_witnesses": [],
                    "self_witnesses": actors,
                    "source_type": "public_scene_transition",
                    "source_ref": path,
                    "visibility": "public",
                    "changed_paths": [path],
                    "impacts": [
                        {
                            "scope": "scene",
                            "target": "scene",
                            "path": f"scene_flags.{path}",
                        }
                    ],
                }
            )
        return events

    def _object_state_events(
        self,
        result: Any,
        scene_state: Any,
        *,
        current_step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        events = []
        for index, change in enumerate(result.get("object_state_changes", []) or []):
            if not isinstance(change, dict):
                continue
            object_id = self._text(change.get("object_id"), 120)
            paths = self._text_list(change.get("paths", []), 32, 120)
            location = self._text(change.get("location"), 160)
            source_actors = self._text_list(
                change.get("source_actors", []), 16, 120
            )
            if not object_id or not paths:
                continue
            change_id = self._text(change.get("change_id"), 220)
            occurred_step = int(change.get("occurred_step", current_step) or 0)
            statement = self._text(change.get("statement"), 800) or (
                f"“{object_id}”的可观察状态发生了变化"
                f"（{'、'.join(paths)}）。"
            )
            visibility = self._visibility(change.get("visibility"))
            direct = (
                self._witnesses_at(
                    scene_state,
                    location,
                    observation_windows,
                )
                if visibility != "hidden"
                else []
            )
            direct = sorted(set(direct).difference(source_actors))
            source_type = self._text(change.get("source_type"), 40)
            source_ref = change_id or object_id
            if source_actors:
                source_type = "resolved_action"
                source_ref = (
                    f"step:{int(occurred_step)}:actors:"
                    + "+".join(sorted(source_actors))
                )
            events.append(
                {
                    "event_id": (
                        f"object-state:{change_id}"
                        if change_id
                        else f"object-state:{current_step}:{index}:{object_id}"
                    ),
                    "kind": "object_state_changed",
                    "title": f"{object_id}状态变化",
                    "statement": statement,
                    "occurred_step": occurred_step,
                    "location": location,
                    "subjects": source_actors,
                    "objects": [object_id],
                    "direct_witnesses": direct,
                    "self_witnesses": source_actors,
                    "source_type": source_type or "object_state_transition",
                    "source_ref": source_ref,
                    "visibility": visibility,
                    "self_attention": False,
                    "changed_paths": paths,
                    "impacts": [
                        {
                            "scope": "world_object",
                            "target": object_id,
                            "path": path,
                        }
                        for path in paths
                    ],
                }
            )
        return events

    def _movement_events(
        self,
        result: Any,
        *,
        current_step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        events = []
        for index, movement in enumerate(result.get("actor_movements", []) or []):
            if not isinstance(movement, dict):
                continue
            actor = self._text(movement.get("actor"), 120)
            origin = self._text(movement.get("origin"), 160)
            destination = self._text(movement.get("destination"), 160)
            if not actor or not origin or not destination or origin == destination:
                continue
            visibility = self._visibility(movement.get("visibility"))
            direct = []
            if visibility != "hidden":
                direct = self._text_list(
                    list(movement.get("departure_witnesses", []) or [])
                    + list(movement.get("arrival_witnesses", []) or []),
                    128,
                    120,
                )
                direct = sorted(set(direct).difference({actor}))
            events.append(
                {
                    "event_id": (
                        f"movement:{current_step}:{index}:{actor}:"
                        f"{origin}->{destination}"
                    ),
                    "kind": "actor_moved",
                    "title": f"{actor}移动",
                    "statement": f"{actor}从{origin}移动到了{destination}。",
                    "occurred_step": current_step,
                    "location": destination,
                    "origin_location": origin,
                    "destination_location": destination,
                    "subjects": [actor],
                    "objects": [],
                    "direct_witnesses": direct,
                    "self_witnesses": [actor],
                    "source_type": "resolved_action",
                    "source_ref": f"step:{int(current_step)}:actor:{actor}",
                    "visibility": visibility,
                    "self_attention": False,
                    "impacts": [
                        {
                            "scope": "actor",
                            "target": actor,
                            "path": "location",
                        },
                        {
                            "scope": "scene",
                            "target": origin,
                            "path": "occupancy",
                        },
                        {
                            "scope": "scene",
                            "target": destination,
                            "path": "occupancy",
                        },
                    ],
                }
            )
        return events

    def _object_events(
        self,
        result: Any,
        scene_state: Any,
        *,
        current_step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        actions = [
            item
            for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        events = []
        verbs = {
            "spawn": "创建了",
            "relocate": "移动了",
            "set_visibility": "改变了可见状态：",
            "set_container_state": "改变了容器状态：",
            "use": "使用了",
            "destroy": "销毁了",
        }
        for index, operation in enumerate(result.get("object_lifecycle", []) or []):
            if not isinstance(operation, dict):
                continue
            kind = self._text(operation.get("operation"), 40)
            object_id = self._text(operation.get("object_id"), 120)
            actor = self._text(operation.get("actor"), 120)
            if kind not in verbs or not object_id or not actor:
                continue
            action = next(
                (
                    item
                    for item in actions
                    if self._text(item.get("actor"), 120) == actor
                ),
                {},
            )
            location = self._text(action.get("location"), 160)
            visibility = self._text(action.get("visibility", "local"), 20)
            direct = (
                self._witnesses_at(
                    scene_state,
                    location,
                    observation_windows,
                )
                if visibility != "hidden"
                else []
            )
            detail = ""
            if kind == "relocate":
                destination = (
                    operation.get("owner")
                    or operation.get("location")
                    or operation.get("container")
                )
                detail = f"，目标位置或持有者为{destination}" if destination else ""
            elif kind == "set_visibility":
                detail = "隐藏" if operation.get("hidden") else "显露"
            elif kind == "set_container_state":
                detail = "打开" if operation.get("open") else "关闭"
            statement = f"{actor}{verbs[kind]}物品“{object_id}”{detail}。"
            if actor == "World":
                source_type = "world_action"
                source_ref = self._text(action.get("event_id"), 220) or (
                    f"step:{int(current_step)}:object:{object_id}:operation:{kind}"
                )
            else:
                source_type = "resolved_action"
                source_ref = f"step:{int(current_step)}:actor:{actor}"
            events.append(
                {
                    "event_id": f"object:{current_step}:{index}:{kind}:{object_id}",
                    "kind": f"object_{kind}",
                    "title": f"物品{kind}",
                    "statement": statement,
                    "occurred_step": current_step,
                    "location": location,
                    "subjects": [actor] if actor != "World" else [],
                    "objects": [object_id],
                    "direct_witnesses": direct,
                    "self_witnesses": [actor] if actor != "World" else [],
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "affordance_id": self._text(
                        operation.get("affordance_id"), 120
                    ),
                    "visibility": visibility,
                    "impacts": self._object_impacts(
                        scene_state,
                        object_id=object_id,
                        operation=kind,
                    ),
                }
            )
        return events

    def _exchange_events(
        self,
        result: Any,
        scene_state: Any,
        *,
        current_step: int,
        observation_windows: Any = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        events = []
        for index, exchange in enumerate(result.get("exchanges", []) or []):
            if not isinstance(exchange, dict):
                continue
            exchange_id = self._text(exchange.get("exchange_id"), 160)
            parties = self._text_list(exchange.get("parties", []), 4, 120)
            objects = [
                self._text(item.get("object_id"), 120)
                for item in exchange.get("transfers", []) or []
                if isinstance(item, dict) and self._text(item.get("object_id"), 120)
            ]
            if not exchange_id or len(parties) < 2:
                continue
            location = scene_state.get_actor_location(parties[0]) if scene_state else ""
            statement = (
                f"{'、'.join(parties)}完成了交换"
                + (f"，涉及物品{'、'.join(objects)}" if objects else "")
                + "。"
            )
            source_type = "resolved_action"
            source_ref = (
                f"step:{int(current_step)}:actors:"
                + "+".join(sorted(parties))
            )
            events.append(
                {
                    "event_id": f"exchange:{current_step}:{index}:{exchange_id}",
                    "kind": "exchange_completed",
                    "title": "交换完成",
                    "statement": statement,
                    "occurred_step": current_step,
                    "location": location,
                    "subjects": parties,
                    "objects": objects,
                    "direct_witnesses": self._witnesses_at(
                        scene_state,
                        location,
                        observation_windows,
                    ),
                    "self_witnesses": parties,
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "visibility": "local",
                    "impacts": [
                        {
                            "scope": "world_object",
                            "target": object_id,
                            "path": "owner",
                        }
                        for object_id in objects
                    ],
                }
            )
        return events

    @staticmethod
    def _witnesses_at(
        scene_state: Any,
        location: Any,
        observation_windows: Any = None,
    ) -> List[str]:
        if not scene_state or not location:
            return []
        location_key = str(location).strip()
        return sorted(
            actor
            for actor in scene_state.actor_states
            if location_key
            in actor_observation_locations(
                actor,
                scene_state,
                observation_windows,
            )
        )

    @staticmethod
    def _direct_witness_location(
        actor: str,
        fact: Any,
        scene_state: Any,
    ) -> str:
        origin = str(getattr(fact, "metadata", {}).get("origin_location", "") or "").strip()
        destination = str(
            getattr(fact, "metadata", {}).get("destination_location", "") or ""
        ).strip()
        current = str(
            scene_state.get_actor_location(actor) if scene_state is not None else ""
        ).strip()
        if str(getattr(fact, "kind", "")) == "actor_moved" and current in {
            origin,
            destination,
        }:
            return current
        return str(getattr(fact, "location", "") or current).strip()

    @staticmethod
    def _reactivate_goal(
        entity: Entity | None,
        fact: Any,
        *,
        step: int,
        context: Dict[str, Any],
        reason: str,
    ) -> None:
        if entity is None:
            return
        update = reactivate_relevant_agent_goal(
            entity,
            event_id=fact.event_id,
            references=[
                *list(fact.subjects),
                *list(fact.objects),
                fact.location,
                fact.source_ref,
            ],
            impacts=fact.impacts,
            step=step,
            reason=reason,
        )
        if update:
            context.setdefault("goal_reactivations", []).append(update)

    @staticmethod
    def _step(context: Dict[str, Any]) -> int:
        clock = context.get("clock")
        return int(clock.current_step if clock else 0)

    @staticmethod
    def _scene_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("SceneState")
            if state is not None:
                return state
        return None

    @staticmethod
    def _attention_enabled(entity: Entity | None) -> bool:
        if entity is None:
            return False
        controller = entity.get_component("AgentController")
        if controller is None:
            return True
        return bool(controller.autonomous) and str(
            controller.activation_policy
        ) != "dormant"

    @classmethod
    def _attention_recipients(
        cls,
        fact: Any,
        witnesses: Any,
        entities: Dict[str, Entity],
        scene_state: Any,
        observation_windows: Any = None,
    ) -> set[str]:
        witnessed = set(getattr(witnesses, "direct_witnesses", []) or []).union(
            getattr(witnesses, "self_witnesses", []) or []
        )
        eligible = {
            actor
            for actor in witnessed
            if actor in entities
            and entities[actor].get_component("Cognition") is not None
            and cls._attention_enabled(entities[actor])
        }
        if str(getattr(fact, "visibility", "local")) != "public":
            return eligible

        subjects = set(getattr(fact, "subjects", []) or [])
        forced = eligible.intersection(subjects)
        location = str(getattr(fact, "location", "") or "").strip()
        if location and scene_state is not None:
            forced.update(
                actor
                for actor in eligible
                if location
                in actor_observation_locations(
                    actor,
                    scene_state,
                    observation_windows,
                )
            )
        try:
            raw_budget = (
                scene_state.get_scene_flag("public_event_attention_budget", 8)
                if scene_state is not None
                else 8
            )
            budget = max(0, min(64, int(raw_budget)))
        except (TypeError, ValueError):
            budget = 8
        remaining_slots = max(0, budget - len(forced))
        if remaining_slots == 0:
            return forced

        references = [
            *list(getattr(fact, "subjects", []) or []),
            *list(getattr(fact, "objects", []) or []),
            getattr(fact, "location", ""),
            getattr(fact, "source_ref", ""),
        ]
        candidates = []
        for actor in eligible.difference(forced):
            relevant = relevant_goal_match(
                entities[actor],
                event_id=str(getattr(fact, "event_id", "") or ""),
                references=references,
                impacts=getattr(fact, "impacts", []) or [],
            )
            digest = hashlib.sha256(
                f"{getattr(fact, 'event_id', '')}|{actor}".encode("utf-8")
            ).hexdigest()
            candidates.append((0 if relevant else 1, digest, actor))
        candidates.sort()
        forced.update(actor for _, _, actor in candidates[:remaining_slots])
        return forced

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]

    @classmethod
    def _impacts(cls, value: Any) -> List[WorldEventImpact]:
        if not isinstance(value, list):
            return []
        result: List[WorldEventImpact] = []
        allowed = set(WorldEventImpact.model_fields["scope"].annotation.__args__)
        seen = set()
        for raw in value[:64]:
            if not isinstance(raw, dict):
                continue
            scope = cls._text(raw.get("scope"), 40)
            target = cls._text(raw.get("target"), 180)
            path = cls._text(raw.get("path"), 120) or "*"
            key = (scope, target, path)
            if scope not in allowed or not target or key in seen:
                continue
            seen.add(key)
            result.append(WorldEventImpact(scope=scope, target=target, path=path))
        return result

    @classmethod
    def _object_impacts(
        cls,
        scene_state: Any,
        *,
        object_id: str,
        operation: str,
    ) -> List[Dict[str, str]]:
        paths = {
            "spawn": ["existence"],
            "relocate": ["owner", "location", "container"],
            "set_visibility": ["hidden", "visibility"],
            "set_container_state": ["container_open", "accessibility", "visibility"],
            "use": ["*"],
            "destroy": ["existence"],
        }.get(operation, ["*"])
        impacts = [
            {"scope": "world_object", "target": object_id, "path": path}
            for path in paths
        ]
        if operation != "set_container_state" or scene_state is None:
            return impacts
        # Opening or closing a container changes the effective accessibility
        # and visibility of every nested object, even though their own ECS rows
        # did not change.  This is the first useful indirect causal projection.
        pending = [object_id]
        seen = {object_id}
        while pending:
            parent = pending.pop()
            for child in sorted(scene_state.get_contained_objects(parent)):
                if child in seen:
                    continue
                seen.add(child)
                pending.append(child)
                impacts.extend(
                    [
                        {
                            "scope": "world_object",
                            "target": child,
                            "path": "accessibility",
                        },
                        {
                            "scope": "world_object",
                            "target": child,
                            "path": "visibility",
                        },
                    ]
                )
        return impacts

    @classmethod
    def _visibility(cls, value: Any) -> str:
        visibility = cls._text(value, 20)
        return visibility if visibility in {"public", "local", "hidden"} else "local"

    @classmethod
    def _text_list(cls, value: Any, limit: int, item_limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return [
            text
            for raw in value[:limit]
            if (text := cls._text(raw, item_limit))
        ]
