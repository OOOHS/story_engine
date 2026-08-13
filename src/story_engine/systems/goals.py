from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class GoalSystem(System):
    """Resolves private goals from committed authoritative world evidence."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        scene_state = self._get_scene_state(entities)
        plot_state = self._get_plot_state(entities)
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        transitions = []
        errors = []
        requests_by_actor = {}
        for request in context.get("agent_goal_requests", []):
            if not isinstance(request, dict):
                continue
            actor = str(request.get("actor", "")).strip()
            if actor and actor not in requests_by_actor:
                requests_by_actor[actor] = request
        for actor, entity in entities.items():
            state = entity.get_component("GoalState")
            if state is None or not hasattr(state, "advance_to"):
                continue
            request = requests_by_actor.get(actor)
            if request is not None:
                transition, error = self._apply_agent_request(
                    actor=actor,
                    entity=entity,
                    entities=entities,
                    state=state,
                    request=request,
                    scene_state=scene_state,
                    context=context,
                    step=step,
                )
                if transition:
                    transitions.append({"actor": actor, **transition})
                if error:
                    errors.append(f"{actor}:{error}")
            actor_transitions, actor_errors = state.advance_to(
                step=step,
                scene_state=scene_state,
                plot_state=plot_state,
                condition_matcher=lambda condition, actor=actor, entity=entity: (
                    self._matches_authoritative_condition(
                        actor=actor,
                        entity=entity,
                        entities=entities,
                        condition=condition,
                        scene_state=scene_state,
                        plot_state=plot_state,
                        context=context,
                    )
                ),
            )
            transitions.extend(
                {"actor": actor, **transition}
                for transition in actor_transitions
            )
            errors.extend(f"{actor}:{error}" for error in actor_errors)
        context["goal_transitions"] = transitions
        context["goal_errors"] = errors

    def _apply_agent_request(
        self,
        *,
        actor: str,
        entity: Entity,
        entities: Dict[str, Entity],
        state: Any,
        request: Dict[str, Any],
        scene_state: Any,
        context: Dict[str, Any],
        step: int,
    ) -> tuple[Dict[str, Any] | None, str]:
        operation = str(request.get("operation", "")).strip().lower()
        if operation == "abandon":
            return state.abandon_agent_goal(
                goal_id=request.get("goal_id"),
                reason=request.get("reason"),
                step=step,
            )
        if operation == "refine":
            goal_id = str(request.get("goal_id", "")).strip()
            record = state.goals.get(goal_id)
            if record is None:
                return None, "agent goal does not exist"
            refinement_request = {
                **request,
                "source_kind": record.source_kind,
                "source_ref": record.source_ref,
            }
            completion_conditions, failure_conditions, resolution_error = (
                self._compile_resolution(
                    actor=actor,
                    entity=entity,
                    entities=entities,
                    request=refinement_request,
                    scene_state=scene_state,
                    context=context,
                )
            )
            if resolution_error:
                return None, resolution_error
            return state.refine_agent_goal(
                goal_id=goal_id,
                step=step,
                completion_conditions=completion_conditions,
                failure_conditions=failure_conditions,
            )
        if operation != "adopt":
            return None, "unsupported agent goal operation"
        source_kind = str(request.get("source_kind", "")).strip()
        source_ref = str(request.get("source_ref", "")).strip()
        valid, priority = self._validate_source(
            actor=actor,
            entity=entity,
            state=state,
            source_kind=source_kind,
            source_ref=source_ref,
            scene_state=scene_state,
            context=context,
            request=request,
        )
        if not valid:
            return None, "agent goal source is not present in the actor's private state"
        completion_conditions, failure_conditions, resolution_error = (
            self._compile_resolution(
            actor=actor,
            entity=entity,
            entities=entities,
            request=request,
            scene_state=scene_state,
            context=context,
            )
        )
        if resolution_error:
            return None, resolution_error
        return state.adopt_agent_goal(
            title=request.get("title"),
            description=request.get("reason", ""),
            source_kind=source_kind,
            source_ref=source_ref,
            priority=priority,
            step=step,
            completion_conditions=completion_conditions,
            failure_conditions=failure_conditions,
        )

    @staticmethod
    def _compile_resolution(
        *,
        actor: str,
        entity: Entity,
        entities: Dict[str, Entity],
        request: Dict[str, Any],
        scene_state: Any,
        context: Dict[str, Any],
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], str]:
        kind = str(request.get("resolution_kind", "")).strip()
        target = " ".join(
            str(request.get("resolution_target", "") or "").split()
        ).strip()[:120]
        if not kind:
            return [], [], ""
        supported = {
            "reach_location",
            "possess_object",
            "deliver_object",
            "fulfill_obligation",
            "settle_agreement",
            "verify_claim",
            "obtain_evidence",
            "become_acquainted",
            "reach_relationship_state",
            "communicate_event",
            "respond_to_event",
            "use_affordance",
        }
        if kind not in supported:
            return [], [], "unsupported agent goal resolution kind"
        if not scene_state or not target:
            return [], [], "agent goal resolution requires a visible target"
        pov = scene_state.get_view_pov(actor)
        current_visible_world = (
            pov.get("visible_world", {}) if isinstance(pov, dict) else {}
        )
        host_pov = request.get("_host_perception", {})
        has_host_pov = isinstance(host_pov, dict) and isinstance(
            host_pov.get("visible_world"), dict
        )
        visible_world = (
            host_pov.get("visible_world", {})
            if has_host_pov
            else current_visible_world
        )
        visible_actors = set(
            host_pov.get("visible_actors", [])
            if has_host_pov
            else pov.get("visible_actors", []) or []
        )
        visible_objects = set(visible_world)
        target_state = (
            visible_world.get(target, {})
            if target in visible_world
            else scene_state.get_object_state(target)
        )
        if kind == "use_affordance":
            affordance_id = " ".join(
                str(request.get("resolution_affordance", "") or "").split()
            ).strip()[:120]
            host_affordances = (
                host_pov.get("affordances", []) if has_host_pov else []
            )
            available = {
                (
                    str(item.get("object_id", "")).strip(),
                    str(item.get("affordance_id", "")).strip(),
                ): item
                for item in host_affordances
                if isinstance(item, dict)
            }
            source_kind = str(request.get("source_kind", ""))
            source_ref = str(request.get("source_ref", ""))
            if source_kind == "visible_object" and source_ref != target:
                return [], [], "use_affordance must reference its visible object"
            if source_kind not in {"visible_object", "drive_need"}:
                return [], [], "use_affordance requires an object or drive need source"
            if not affordance_id or (target, affordance_id) not in available:
                return [], [], "use_affordance is not available in the actor's perception"
            if source_kind == "drive_need":
                effects = available[(target, affordance_id)].get(
                    "need_effects", {}
                )
                if (
                    not isinstance(effects, dict)
                    or float(effects.get(source_ref, 0.0) or 0.0) >= 0
                ):
                    return [], [], "use_affordance does not relieve its source need"
            clock = context.get("clock")
            current_step = int(getattr(clock, "current_step", 0) or 0)
            return [
                {
                    "scope": "affordance_event",
                    "target": target,
                    "path": "metadata.affordance_id",
                    "operator": "eq",
                    "value": affordance_id,
                    "actor": actor,
                    "min_step": current_step,
                }
            ], [], ""
        if kind in {"communicate_event", "respond_to_event"}:
            event_id = str(request.get("source_ref", "")).strip()
            cognition = entity.get_component("Cognition")
            event_entity = entities.get(f"WorldEvent:{event_id}")
            fact = (
                event_entity.get_component("WorldEventFact")
                if event_entity is not None
                else None
            )
            if (
                str(request.get("source_kind", "")) != "world_event"
                or not cognition
                or not cognition.knows_event(event_id)
                or fact is None
            ):
                return [], [], "communicate_event requires an event known by the actor"
            if target == actor or target not in visible_actors:
                return [], [], "communicate_event recipient is not currently visible"
            responses = event_entity.get_component("WorldEventResponses")
            response_key = f"{actor}->{target}"
            if kind == "respond_to_event":
                response_kind = str(
                    request.get("resolution_response", "")
                ).strip().lower()
                allowed = {
                    "explain",
                    "apologize",
                    "accuse",
                    "request",
                    "forgive",
                    "acknowledge",
                }
                if response_kind not in allowed:
                    return [], [], "unsupported event response kind"
                response_key = f"{response_key}:{response_kind}"
                if responses and response_key in responses.response_keys():
                    return [], [], "respond_to_event target is already satisfied"
                path = "responses"
            else:
                if responses and response_key in responses.communication_keys():
                    return [], [], "communicate_event target is already satisfied"
                path = "communications"
            return [
                {
                    "scope": "world_event",
                    "target": event_id,
                    "path": path,
                    "operator": "contains",
                    "value": response_key,
                }
            ], [], ""
        if kind == "reach_location":
            current = (
                host_pov.get("location")
                if has_host_pov
                else scene_state.get_actor_location(actor)
            )
            knowledge = entity.get_component("KnowledgeState")
            known_map = knowledge.get_map_snapshot() if knowledge else {}
            known_targets = set(known_map.get("known_locations", []) or [])
            known_targets.update(visible_world)
            if not target_state.get("is_location", True) or target not in known_targets:
                return [], [], "reach_location target is not currently known to the actor"
            if current == target:
                return [], [], "reach_location target is already satisfied"
            return [
                {
                    "scope": "actor",
                    "target": actor,
                    "path": "location",
                    "operator": "eq",
                    "value": target,
                }
            ], [
                {
                    "scope": "world_object",
                    "target": target,
                    "path": "",
                    "operator": "not_exists",
                    "value": None,
                }
            ], ""
        if kind in {"possess_object", "deliver_object"}:
            if target not in visible_objects:
                return [], [], f"{kind} target is not currently visible to the actor"
            if target_state.get("is_location", True):
                return [], [], f"{kind} target must be a portable world object"
            if not target_state.get("portable", True):
                return [], [], f"{kind} target is not portable"
            recipient = " ".join(
                str(request.get("resolution_recipient", "") or "").split()
            ).strip()[:120]
            if kind == "deliver_object":
                if target_state.get("owner") != actor:
                    return [], [], "deliver_object requires the actor to own the object"
                if recipient not in visible_actors:
                    return [], [], "deliver_object recipient is not currently visible"
                owner = recipient
            else:
                owner = actor
                if target_state.get("owner") == actor:
                    return [], [], "possess_object target is already owned by the actor"
            return [
                {
                    "scope": "world_object",
                    "target": target,
                    "path": "owner",
                    "operator": "eq",
                    "value": owner,
                }
            ], [
                {
                    "scope": "world_object",
                    "target": target,
                    "path": "",
                    "operator": "not_exists",
                    "value": None,
                }
            ], ""
        if kind == "fulfill_obligation":
            obligations = entity.get_component("ObligationState")
            record = obligations.obligations.get(target) if obligations else None
            if not record or str(request.get("source_kind", "")) != "obligation" or str(
                request.get("source_ref", "")
            ) != target:
                return [], [], "fulfill_obligation must reference the actor's own obligation"
            if record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
                return [], [], "fulfill_obligation target is already terminal"
            return [
                {
                    "scope": "obligation",
                    "target": target,
                    "path": "status",
                    "operator": "eq",
                    "value": "fulfilled",
                }
            ], [
                {
                    "scope": "obligation",
                    "target": target,
                    "path": "status",
                    "operator": "in",
                    "value": ["breached", "cancelled", "delegated"],
                }
            ], ""
        if kind in {"verify_claim", "obtain_evidence"}:
            claim_registry = context.get("claim_registry")
            claim = claim_registry.get(target) if claim_registry else None
            knowledge = entity.get_component("KnowledgeState")
            if (
                claim is None
                or not knowledge
                or not knowledge.knows(target)
                or str(request.get("source_kind", "")) != "claim"
                or str(request.get("source_ref", "")) != target
            ):
                return [], [], f"{kind} must reference a claim known by the actor"
            knowledge_record = knowledge.claims[target]
            if kind == "verify_claim" and knowledge_record.evidence_refs:
                return [], [], "verify_claim target already has known evidence"
            evidence_ref = " ".join(
                str(request.get("resolution_evidence", "") or "").split()
            ).strip()[:120]
            completion = [
                {
                    "scope": "knowledge",
                    "target": target,
                    "path": "evidence_refs",
                    "operator": "ne",
                    "value": [],
                }
            ]
            if kind == "obtain_evidence":
                evidence = claim.get_component("ClaimEvidence")
                linked = set(evidence.supports).union(evidence.refutes) if evidence else set()
                if evidence_ref not in linked:
                    return [], [], "obtain_evidence requires evidence linked to the claim"
                if evidence_ref not in visible_objects:
                    return [], [], "obtain_evidence target is not currently visible"
                evidence_state = (
                    visible_world.get(evidence_ref, {})
                    if evidence_ref in visible_world
                    else scene_state.get_object_state(evidence_ref)
                )
                if evidence_state.get("is_location", True) or not evidence_state.get(
                    "portable", True
                ):
                    return [], [], "obtain_evidence target must be a portable object"
                if (
                    evidence_ref in knowledge_record.evidence_refs
                    and evidence_state.get("owner") == actor
                ):
                    return [], [], "obtain_evidence target is already satisfied"
                completion = [
                    {
                        "scope": "knowledge",
                        "target": target,
                        "path": "evidence_refs",
                        "operator": "contains",
                        "value": evidence_ref,
                    },
                    {
                        "scope": "world_object",
                        "target": evidence_ref,
                        "path": "owner",
                        "operator": "eq",
                        "value": actor,
                    },
                ]
            failure = []
            if kind == "obtain_evidence":
                failure = [
                    {
                        "scope": "world_object",
                        "target": evidence_ref,
                        "path": "",
                        "operator": "not_exists",
                        "value": None,
                    }
                ]
            return completion, failure, ""
        if kind == "become_acquainted":
            if (
                str(request.get("source_kind", "")) != "visible_actor"
                or str(request.get("source_ref", "")) != target
                or target not in visible_actors
            ):
                return [], [], "become_acquainted requires a currently visible actor"
            relation_registry = context.get("relation_registry")
            relationship_book = (
                relation_registry.to_relationship_book()
                if relation_registry
                else None
            )
            try:
                relation_id = (
                    relationship_book.relation_id(actor, target)
                    if relationship_book
                    else ""
                )
            except ValueError:
                relation_id = ""
            relation = (
                relationship_book.relationships.get(relation_id)
                if relationship_book
                else None
            )
            if relation and "acquainted" in relation.bits:
                return [], [], "become_acquainted target is already acquainted"
            return [
                {
                    "scope": "relationship",
                    "target": target,
                    "path": "bits",
                    "operator": "contains",
                    "value": "acquainted",
                }
            ], [], ""
        if kind == "reach_relationship_state":
            desired = str(request.get("resolution_state", "")).strip()
            if desired not in {"non_hostile", "trusted", "close"}:
                return [], [], "unsupported qualitative relationship state"
            source_kind = str(request.get("source_kind", ""))
            source_ref = str(request.get("source_ref", ""))
            if source_ref != target or source_kind not in {
                "relationship",
                "visible_actor",
            }:
                return [], [], "relationship goal must reference its target actor"
            relation_registry = context.get("relation_registry")
            relationship_book = (
                relation_registry.to_relationship_book()
                if relation_registry
                else None
            )
            current_states = (
                relationship_book.describe_direction(actor, target)
                if relationship_book
                else ["neutral"]
            )
            if desired in current_states:
                return [], [], "qualitative relationship goal is already satisfied"
            return [
                {
                    "scope": "relationship",
                    "target": target,
                    "path": "actor_to_target_states",
                    "operator": "contains",
                    "value": desired,
                }
            ], [], ""
        registry = context.get("agreement_registry")
        record = registry.to_book().agreements.get(target) if registry else None
        if (
            not record
            or actor not in record.parties
            or str(request.get("source_kind", "")) != "agreement"
            or str(request.get("source_ref", "")) != target
        ):
            return [], [], "settle_agreement must reference the actor's own agreement"
        if record.status != "pending":
            return [], [], "settle_agreement target is already terminal"
        return [
            {
                "scope": "agreement",
                "target": target,
                "path": "status",
                "operator": "eq",
                "value": "settled",
            }
        ], [
            {
                "scope": "agreement",
                "target": target,
                "path": "status",
                "operator": "in",
                "value": ["rejected", "withdrawn", "expired", "countered"],
            }
        ], ""

    @staticmethod
    def _matches_authoritative_condition(
        *,
        actor: str,
        entity: Entity,
        entities: Dict[str, Entity],
        condition: Dict[str, Any],
        scene_state: Any,
        plot_state: Any,
        context: Dict[str, Any],
    ) -> bool:
        scope = str(condition.get("scope", "scene"))
        if scope in {"scene", "world_object", "actor", "plot"}:
            return bool(
                scene_state
                and scene_state.matches_condition(condition, plot_state=plot_state)
            )
        target = str(condition.get("target", ""))
        if scope == "obligation":
            state = entity.get_component("ObligationState")
            record = state.obligations.get(target) if state else None
        elif scope == "agreement":
            registry = context.get("agreement_registry")
            record = registry.to_book().agreements.get(target) if registry else None
            if record and actor not in record.parties:
                record = None
        elif scope == "knowledge":
            state = entity.get_component("KnowledgeState")
            record = state.claims.get(target) if state else None
        elif scope == "relationship":
            registry = context.get("relation_registry")
            book = registry.to_relationship_book() if registry else None
            try:
                relation_id = book.relation_id(actor, target) if book else ""
            except ValueError:
                relation_id = ""
            record = book.relationships.get(relation_id) if book else None
            if record is not None:
                source = {
                    **record.model_dump(),
                    "actor_to_target_states": book.describe_direction(
                        actor, target
                    ),
                    "target_to_actor_states": book.describe_direction(
                        target, actor
                    ),
                }
            else:
                source = {}
        elif scope == "world_event":
            event_entity = entities.get(f"WorldEvent:{target}")
            fact = (
                event_entity.get_component("WorldEventFact")
                if event_entity is not None
                else None
            )
            responses = (
                event_entity.get_component("WorldEventResponses")
                if event_entity is not None
                else None
            )
            source = {
                "event_id": fact.event_id if fact is not None else "",
                "communications": (
                    responses.communication_keys() if responses else []
                ),
                "responses": responses.response_keys() if responses else [],
            }
        elif scope == "affordance_event":
            expected_actor = str(condition.get("actor", "")).strip()
            minimum_step = int(condition.get("min_step", 0) or 0)
            affordance_id = str(condition.get("value", "")).strip()
            if affordance_id in {"engine:take", "engine:drop"}:
                expected_operation = "relocate"
            elif affordance_id in {"engine:open", "engine:close"}:
                expected_operation = "set_container_state"
            else:
                expected_operation = "use"
            event_facts = [
                event_entity.get_component("WorldEventFact")
                for event_entity in entities.values()
                if event_entity.get_component("WorldEventFact") is not None
            ]
            return any(
                fact.source_type == "resolved_action"
                and fact.occurred_step >= minimum_step
                and fact.kind == f"object_{expected_operation}"
                and fact.event_id.startswith(
                    f"object:{fact.occurred_step}:"
                )
                and fact.event_id.endswith(
                    f":{expected_operation}:{target}"
                )
                and fact.source_ref
                == f"step:{fact.occurred_step}:actor:{expected_actor}"
                and expected_actor in fact.subjects
                and target in fact.objects
                and GoalSystem._compare(
                    (fact.metadata or {}).get("affordance_id"),
                    str(condition.get("operator", "eq")),
                    condition.get("value"),
                )
                for fact in event_facts
            )
        else:
            return False
        if scope not in {"relationship", "world_event", "affordance_event"}:
            source = record.model_dump() if record is not None else {}
        actual: Any = source
        for part in str(condition.get("path", "")).split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        return GoalSystem._compare(
            actual,
            str(condition.get("operator", "eq")),
            condition.get("value"),
        )

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        if operator == "exists":
            return actual is not None
        if operator == "not_exists":
            return actual is None
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "in":
            return isinstance(expected, (list, tuple, set, str)) and actual in expected
        if operator == "contains":
            return isinstance(actual, (dict, list, tuple, set, str)) and expected in actual
        if actual is None:
            return False
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        return False

    @staticmethod
    def _validate_source(
        *,
        actor: str,
        entity: Entity,
        state: Any,
        source_kind: str,
        source_ref: str,
        scene_state: Any,
        context: Dict[str, Any],
        request: Dict[str, Any],
    ) -> tuple[bool, float]:
        priorities = {
            "resolved_goal": 0.65,
            "claim": 0.65,
            "sentiment": 0.6,
            "obligation": 0.7,
            "agreement": 0.65,
            "visible_object": 0.5,
            "visible_actor": 0.5,
            "relationship": 0.55,
            "world_event": 0.6,
            "event_response": 0.65,
            "navigation_problem": 0.65,
            "drive_need": 0.5,
        }
        if source_kind not in priorities or not source_ref:
            return False, 0.0
        if source_kind == "resolved_goal":
            record = state.goals.get(source_ref)
            valid = bool(record and record.status in {"achieved", "failed"})
        elif source_kind == "claim":
            knowledge = entity.get_component("KnowledgeState")
            valid = bool(knowledge and knowledge.knows(source_ref))
        elif source_kind == "sentiment":
            sentiments = entity.get_component("SentimentState")
            valid = bool(sentiments and source_ref in sentiments.sentiments)
        elif source_kind == "obligation":
            obligations = entity.get_component("ObligationState")
            valid = bool(obligations and source_ref in obligations.obligations)
        elif source_kind == "agreement":
            registry = context.get("agreement_registry")
            record = registry.to_book().agreements.get(source_ref) if registry else None
            valid = bool(record and actor in record.parties)
        elif source_kind == "visible_object":
            host_pov = request.get("_host_perception", {})
            if isinstance(host_pov, dict) and host_pov.get("visible_world") is not None:
                valid = source_ref in set(host_pov.get("visible_world", {}))
            else:
                visible = scene_state.get_visible_objects(actor) if scene_state else []
                valid = source_ref in set(visible)
        elif source_kind == "visible_actor":
            host_pov = request.get("_host_perception", {})
            if isinstance(host_pov, dict) and host_pov.get("visible_actors") is not None:
                valid = source_ref in set(host_pov.get("visible_actors", []) or [])
            else:
                pov = scene_state.get_view_pov(actor) if scene_state else {}
                valid = source_ref in set(pov.get("visible_actors", []) or [])
        elif source_kind == "world_event":
            cognition = entity.get_component("Cognition")
            valid = bool(
                cognition
                and hasattr(cognition, "knows_event")
                and cognition.knows_event(source_ref)
            )
        elif source_kind == "event_response":
            cognition = entity.get_component("Cognition")
            valid = bool(
                cognition
                and hasattr(cognition, "knows_event_response")
                and cognition.knows_event_response(source_ref)
            )
        elif source_kind == "navigation_problem":
            navigation = entity.get_component("NavigationState")
            problem = navigation.problems.get(source_ref) if navigation else None
            valid = bool(problem and problem.status == "active")
        elif source_kind == "drive_need":
            host_pov = request.get("_host_perception", {})
            drive_snapshot = (
                host_pov.get("drive_needs", {})
                if isinstance(host_pov, dict)
                else {}
            )
            snapshot_meter = (
                drive_snapshot.get(source_ref)
                if isinstance(drive_snapshot, dict)
                else None
            )
            if isinstance(snapshot_meter, dict):
                pressure = float(snapshot_meter.get("pressure", 0.0) or 0.0)
                valid = pressure > 0
                return valid, min(0.9, 0.45 + 0.45 * pressure)
            drives = entity.get_component("DriveState")
            meter = drives.needs.get(source_ref) if drives else None
            valid = bool(meter and meter.pressure > 0)
            if meter is not None:
                return valid, min(0.9, 0.45 + 0.45 * meter.pressure)
        else:
            registry = context.get("relation_registry")
            book = registry.to_relationship_book() if registry else None
            try:
                relation_id = book.relation_id(actor, source_ref) if book else ""
            except ValueError:
                relation_id = ""
            valid = bool(book and relation_id in book.relationships)
        return valid, priorities[source_kind]

    @staticmethod
    def _get_scene_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("SceneState")
            if state is not None:
                return state
        return None

    @staticmethod
    def _get_plot_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("PlotState")
            if state is not None:
                return state
        return None
