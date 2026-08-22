from typing import Any, Dict, List

from src.story_engine.common.observation_window import shares_action_location

from src.story_engine.common.action_features import normalize_action_policy_tags
from src.story_engine.environment.world_object_lifecycle import WorldObjectLifecycle


class NeedDynamics:
    """Applies object-declared need effects to private DriveState copies."""

    def apply_object_affordances(
        self,
        drive_states: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
        *,
        current_step: int = 0,
    ) -> List[str]:
        operations = result.get("object_lifecycle", [])
        if not isinstance(operations, list):
            return ["object_lifecycle must be a list"]
        errors: List[str] = []
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or operation.get("operation") != "use":
                continue
            prefix = f"object_lifecycle[{index}]"
            actor = str(operation.get("actor", "")).strip()
            object_id = str(operation.get("object_id", "")).strip()
            affordance_id = str(operation.get("affordance_id", "")).strip()
            state = scene_state.get_object_state(object_id) if scene_state else {}
            affordance = WorldObjectLifecycle.get_affordance(state, affordance_id)
            if affordance is None:
                # The object lifecycle reports the canonical error.
                continue
            effects = affordance.get("need_effects", {})
            if not isinstance(effects, dict):
                errors.append(f"{prefix} affordance need_effects must be an object")
                continue
            if len(effects) > 12:
                errors.append(f"{prefix} affordance has too many need effects")
                continue
            drive = drive_states.get(actor)
            for raw_need, raw_delta in effects.items():
                need = str(raw_need).strip()
                if not need:
                    errors.append(f"{prefix} affordance has empty need name")
                    continue
                if (
                    isinstance(raw_delta, bool)
                    or not isinstance(raw_delta, (int, float))
                    or not -1.0 <= float(raw_delta) <= 1.0
                ):
                    errors.append(
                        f"{prefix} need effect must be numeric between -1 and 1: {need}"
                    )
                    continue
                if drive is None or need not in drive.needs:
                    continue
                drive.apply_need_delta(
                    need,
                    float(raw_delta),
                    provenance={
                        "source_kind": "resolved_action",
                        "source_ref": f"step:{int(current_step)}:actor:{actor}",
                        "object_id": object_id,
                        "affordance_id": affordance_id,
                    },
                )
        return errors

    def apply_explicit_updates(
        self,
        drive_states: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
        *,
        current_step: int = 0,
        previous_scene_state: Any = None,
    ) -> List[str]:
        updates = result.get("drive_updates", [])
        if not isinstance(updates, list):
            return ["drive_updates must be a list"]
        resolved_actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        errors: List[str] = []
        for index, update in enumerate(updates):
            prefix = f"drive_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            actor = str(update.get("actor", "")).strip()
            source = str(update.get("source", actor)).strip()
            need = str(update.get("need", "")).strip()
            reason = " ".join(str(update.get("reason", "")).split()).strip()
            drive = drive_states.get(actor)
            if actor not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown affected actor: {actor}")
            if drive is None:
                errors.append(f"{prefix} actor has no DriveState: {actor}")
            elif need not in drive.needs:
                errors.append(f"{prefix} has unknown need for {actor}: {need}")
            if not source:
                errors.append(f"{prefix} requires source")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            source_actions = [
                action
                for action in resolved_actions
                if str(action.get("actor", "")).strip() == source
            ]
            if not source_actions:
                errors.append(f"{prefix} is not supported by a resolved action")
            elif actor != source:
                observation_windows = {
                    name: {
                        "locations": list(
                            dict.fromkeys(
                                location
                                for location in (
                                    str(
                                        previous_scene_state.get_actor_location(name)
                                        or ""
                                    ).strip()
                                    if previous_scene_state is not None
                                    else "",
                                    str(
                                        scene_state.get_actor_location(name) or ""
                                    ).strip(),
                                )
                                if location
                            )
                        )
                    }
                    for name in {actor, source}
                }
                observable = any(
                    str(action.get("visibility", "public")).strip() != "hidden"
                    and shares_action_location(
                        source,
                        actor,
                        str(action.get("location", "")).strip(),
                        scene_state,
                        observation_windows,
                    )
                    for action in source_actions
                )
                if not observable:
                    errors.append(
                        f"{prefix} source action is not observable by affected actor"
                    )
            delta = update.get("delta")
            if (
                isinstance(delta, bool)
                or not isinstance(delta, (int, float))
                or not -0.5 <= float(delta) <= 0.5
            ):
                errors.append(f"{prefix}.delta must be numeric between -0.5 and 0.5")
            if any(error.startswith(prefix) for error in errors):
                continue
            drive.apply_need_delta(
                need,
                float(delta),
                provenance={
                    "source_kind": "resolved_action",
                    "source_ref": f"step:{int(current_step)}:actor:{source}",
                },
            )
        return errors

    def apply_creations(
        self,
        drive_states: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
        *,
        current_step: int = 0,
        budget: int = 0,
    ) -> List[str]:
        """Validate and apply drive_creations against real DriveState.

        By the time this runs, SemanticAuthorityFilter has already bounded
        drift_per_turn/critical_threshold to a sane numeric range -- this
        pass only owns things that need live state to check: the actor must
        exist, the need must not already exist, the
        creation must be backed by a resolved action this round (same
        evidence requirement as drive_updates), and the actor must still be
        under emergent_meter_budget. budget<=0 means the scenario has not
        opted into runtime meter growth at all; every creation is rejected.
        """
        creations = result.get("drive_creations", [])
        if not isinstance(creations, list):
            return ["drive_creations must be a list"]
        resolved_actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        errors: List[str] = []
        for index, creation in enumerate(creations):
            prefix = f"drive_creations[{index}]"
            if not isinstance(creation, dict):
                errors.append(f"{prefix} must be an object")
                continue
            actor = str(creation.get("actor", "")).strip()
            need = " ".join(str(creation.get("need", "")).split()).strip()
            description = str(creation.get("description", "")).strip()
            reason = " ".join(str(creation.get("reason", "")).split()).strip()
            if actor not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown actor: {actor}")
                continue
            drive = drive_states.get(actor)
            if drive is None:
                errors.append(f"{prefix} actor has no DriveState: {actor}")
                continue
            if not need:
                errors.append(f"{prefix} requires a need name")
                continue
            if need in drive.needs:
                errors.append(
                    f"{prefix} need already exists for {actor}: {need}"
                    " (use drive_updates, not drive_creations)"
                )
                continue
            if not reason:
                errors.append(f"{prefix} requires a reason")
            if int(budget) <= 0 or drive.created_count >= int(budget):
                errors.append(
                    f"{prefix} actor is at or over emergent_meter_budget: {actor}"
                )
            source_actions = [
                action
                for action in resolved_actions
                if str(action.get("actor", "")).strip() == actor
            ]
            if not source_actions:
                errors.append(f"{prefix} is not supported by a resolved action")
            if any(error.startswith(prefix) for error in errors):
                continue
            drive.create_need(
                need,
                drift_per_turn=float(creation.get("drift_per_turn", 0.0)),
                critical_threshold=float(
                    creation.get("critical_threshold", 0.8)
                ),
                description=description,
                provenance={
                    "source_kind": "resolved_action",
                    "source_ref": f"step:{int(current_step)}:actor:{actor}",
                    "reason": reason,
                },
            )
        return errors

    def build_opportunities(
        self,
        scene_state: Any,
        actor_name: str,
        drive_state: Any,
    ) -> List[Dict[str, Any]]:
        if not scene_state:
            return []
        visible = scene_state.get_visible_objects(actor_name)
        opportunities: List[Dict[str, Any]] = []
        for object_id, state in visible.items():
            affordances = state.get("affordances", []) if isinstance(state, dict) else []
            if not isinstance(affordances, list):
                continue
            for affordance in affordances:
                if not isinstance(affordance, dict):
                    continue
                affordance_id = str(affordance.get("id", "")).strip()
                if not affordance_id or affordance_id.startswith("engine:"):
                    continue
                effects = affordance.get("need_effects", {})
                if not isinstance(effects, dict):
                    continue
                raw_required = affordance.get("requires_capabilities", [])
                requirements_valid = isinstance(raw_required, list) and all(
                    isinstance(item, str) and str(item).strip()
                    for item in raw_required
                )
                required_capabilities = (
                    [str(item).strip() for item in raw_required]
                    if requirements_valid
                    else []
                )
                actor_state = scene_state.get_actor_state(actor_name)
                raw_actor_capabilities = (
                    actor_state.get("capabilities", [])
                    if isinstance(actor_state, dict)
                    else []
                )
                if isinstance(raw_actor_capabilities, str):
                    raw_actor_capabilities = [raw_actor_capabilities]
                actor_capabilities = (
                    {
                        str(item).strip()
                        for item in raw_actor_capabilities
                        if isinstance(item, str) and str(item).strip()
                    }
                    if isinstance(raw_actor_capabilities, list)
                    else set()
                )
                missing_capabilities = sorted(
                    set(required_capabilities).difference(actor_capabilities)
                )
                raw_requires_owner = affordance.get("requires_owner", False)
                owner_requirement_valid = isinstance(raw_requires_owner, bool)
                requires_owner = raw_requires_owner is True
                owns_object = str(state.get("owner") or "").strip() == actor_name
                available = (
                    requirements_valid
                    and owner_requirement_valid
                    and not missing_capabilities
                    and (not requires_owner or owns_object)
                )
                relevant_effects = {}
                relief_contributions = {}
                relief_score = 0.0
                for need, raw_delta in effects.items():
                    meter = (
                        drive_state.needs.get(str(need))
                        if drive_state is not None
                        else None
                    )
                    if meter is None or isinstance(raw_delta, bool) or not isinstance(
                        raw_delta, (int, float)
                    ):
                        continue
                    delta = float(raw_delta)
                    relevant_effects[str(need)] = delta
                    if delta < 0:
                        contribution = meter.pressure * abs(delta)
                        relief_contributions[str(need)] = round(contribution, 4)
                        relief_score += contribution
                opportunities.append(
                    {
                        "object_id": object_id,
                        "affordance_id": affordance_id,
                        "label": str(affordance.get("label", affordance_id)).strip(),
                        "need_effects": relevant_effects,
                        "consumes": bool(affordance.get("consumes", False)),
                        "exclusive": bool(affordance.get("exclusive", False)),
                        "requires_owner": requires_owner,
                        "required_capabilities": required_capabilities,
                        "missing_capabilities": missing_capabilities,
                        "available": available,
                        "relief_score": round(relief_score, 4),
                        "relief_contributions": relief_contributions,
                        "policy_tags": list(normalize_action_policy_tags(
                            affordance.get("policy_tags", [])
                        )),
                        "source": "object_definition",
                    }
                )
        opportunities.sort(
            key=lambda item: (
                not item.get("available", True),
                -item.get("relief_score", 0.0),
                item.get("object_id", ""),
                item.get("affordance_id", ""),
            )
        )
        return opportunities
