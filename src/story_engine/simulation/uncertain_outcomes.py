from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.story_engine.simulation.checks import (
    CheckModifier,
    HostCheckResolver,
    ProbabilityCheck,
)


LIST_PATCH_FIELDS = {
    "social_impacts",
    "modifier_updates",
    "knowledge_updates",
    "claim_discoveries",
    "object_lifecycle",
    "exchanges",
    "agreement_updates",
    "drive_updates",
    "drive_creations",
    "director_signals",
    "obligation_updates",
}
BRANCH_FIELDS = LIST_PATCH_FIELDS | {
    "resolved_action",
    "state_updates",
    "tension_delta",
}
CHECK_FIELDS = {
    "check_id",
    "actor",
    "check_kind",
    "difficulty",
    "required_capability",
    "success",
    "failure",
}


@dataclass(frozen=True)
class UncertainOutcomeResolution:
    result: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    traces: List[Dict[str, Any]] = field(default_factory=list)
    rejected_writes: List[str] = field(default_factory=list)


class UncertainOutcomeResolver:
    """Select GM-proposed consequence branches using host-owned checks."""

    MAX_CHECKS = 8

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        scene_state: Any,
        intents: List[Dict[str, Any]],
        check_resolver: HostCheckResolver,
        current_step: int,
        world_version: int,
        movement_authorizations: Dict[str, str] | None = None,
    ) -> UncertainOutcomeResolution:
        working = deepcopy(result)
        raw_checks = working.pop("uncertain_outcomes", [])
        if not raw_checks:
            return UncertainOutcomeResolution(working)
        if not isinstance(raw_checks, list):
            return UncertainOutcomeResolution(
                working, ["uncertain_outcomes must be a list"]
            )
        if len(raw_checks) > self.MAX_CHECKS:
            return UncertainOutcomeResolution(
                working,
                [f"uncertain_outcomes cannot exceed {self.MAX_CHECKS} per batch"],
            )

        proposal_map = {
            str(item.get("actor", "")).strip(): item
            for item in intents or []
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        existing_action_actors = {
            str(item.get("actor", "")).strip()
            for item in working.get("resolved_actions", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        errors: List[str] = []
        traces: List[Dict[str, Any]] = []
        rejected_writes: List[str] = []
        seen_ids = set()
        authorized_moves = {
            self._text(actor, 120): self._text(destination, 160)
            for actor, destination in (movement_authorizations or {}).items()
            if self._text(actor, 120) and self._text(destination, 160)
        }

        for index, raw in enumerate(raw_checks):
            prefix = f"uncertain_outcomes[{index}]"
            if not isinstance(raw, dict):
                errors.append(f"{prefix} must be an object")
                continue
            unknown_check_fields = set(raw).difference(CHECK_FIELDS)
            if unknown_check_fields:
                errors.append(
                    f"{prefix} has unknown fields: {sorted(unknown_check_fields)}"
                )
            check_id = self._text(raw.get("check_id"), 120)
            actor = self._text(raw.get("actor"), 120)
            difficulty = self._text(raw.get("difficulty", "normal"), 20).lower()
            check_kind = self._text(raw.get("check_kind", "world"), 20).lower()
            capability = self._text(raw.get("required_capability"), 120)
            if not check_id or check_id in seen_ids:
                errors.append(f"{prefix} requires a unique check_id")
            else:
                seen_ids.add(check_id)
            if actor not in proposal_map:
                errors.append(f"{prefix} actor has no current proposal: {actor}")
            if actor in existing_action_actors:
                errors.append(
                    f"{prefix} actor already has a deterministic resolved action: {actor}"
                )
            if check_kind not in {"world", "observation"}:
                errors.append(f"{prefix}.check_kind must be world or observation")
            proposal = proposal_map.get(actor, {})
            proposal_kind = str(proposal.get("action_kind", "")).strip()
            if check_kind == "observation" and proposal_kind != "observe":
                errors.append(
                    f"{prefix} observation check requires an observe proposal"
                )
            for branch_name in ("success", "failure"):
                branch = raw.get(branch_name)
                if isinstance(branch, dict):
                    self._sanitize_branch_locations(
                        branch,
                        actor=actor,
                        proposal=proposal,
                        scene_state=scene_state,
                        authorized_destination=authorized_moves.get(actor, ""),
                        prefix=f"{prefix}.{branch_name}",
                        rejected_writes=rejected_writes,
                    )
                errors.extend(
                    self._validate_branch(branch, prefix=f"{prefix}.{branch_name}")
                )
            if any(error.startswith(prefix) for error in errors):
                continue

            modifiers = self._capability_modifiers(
                scene_state, actor=actor, capability=capability
            )
            try:
                check_result = check_resolver.resolve(
                    ProbabilityCheck(
                        check_id=check_id,
                        actor=actor,
                        difficulty=difficulty,
                        stream=check_kind,
                        modifiers=tuple(modifiers),
                    ),
                    step=current_step,
                    world_version=world_version,
                )
            except ValueError as exc:
                errors.append(f"{prefix}: {exc}")
                continue

            branch_name = "success" if check_result.success else "failure"
            branch = deepcopy(raw[branch_name])
            branch_errors = self._merge_branch(
                working,
                branch,
                actor=actor,
                proposal=proposal,
                scene_state=scene_state,
                authorized_destination=authorized_moves.get(actor, ""),
                prefix=f"{prefix}.{branch_name}",
            )
            if branch_errors:
                errors.extend(branch_errors)
                continue
            traces.append(
                {
                    **check_result.trace,
                    "check_kind": check_kind,
                    "selected_branch": branch_name,
                    "required_capability": capability,
                }
            )
            existing_action_actors.add(actor)

        return UncertainOutcomeResolution(
            working,
            errors,
            traces,
            list(dict.fromkeys(rejected_writes)),
        )

    def _validate_branch(self, branch: Any, *, prefix: str) -> List[str]:
        if not isinstance(branch, dict):
            return [f"{prefix} must be an object"]
        unknown = set(branch).difference(BRANCH_FIELDS)
        errors = [f"{prefix} has unknown fields: {sorted(unknown)}"] if unknown else []
        action = branch.get("resolved_action")
        if not isinstance(action, dict):
            errors.append(f"{prefix}.resolved_action must be an object")
        for field_name in LIST_PATCH_FIELDS:
            value = branch.get(field_name, [])
            if not isinstance(value, list) or not all(
                isinstance(item, dict) for item in value
            ):
                errors.append(f"{prefix}.{field_name} must be a list of objects")
        state_updates = branch.get("state_updates", {})
        if not isinstance(state_updates, dict):
            errors.append(f"{prefix}.state_updates must be an object")
        try:
            tension = float(branch.get("tension_delta", 0.0))
            if not -1.0 <= tension <= 1.0:
                errors.append(f"{prefix}.tension_delta must be between -1 and 1")
        except (TypeError, ValueError):
            errors.append(f"{prefix}.tension_delta must be numeric")
        return errors

    def _merge_branch(
        self,
        result: Dict[str, Any],
        branch: Dict[str, Any],
        *,
        actor: str,
        proposal: Dict[str, Any],
        scene_state: Any,
        authorized_destination: str,
        prefix: str,
    ) -> List[str]:
        errors: List[str] = []
        action = deepcopy(branch.get("resolved_action", {}))
        declared_actor = self._text(action.get("actor"), 120)
        if declared_actor and declared_actor != actor:
            errors.append(f"{prefix}.resolved_action cannot change actor")
            return errors
        action["actor"] = actor
        action["intent"] = proposal.get("intent", "")
        action["action_kind"] = proposal.get("action_kind", "interact")
        action["action_target"] = proposal.get("action_target", "")
        branch_actor_state = (
            branch.get("state_updates", {})
            .get("actor_states", {})
            .get(actor, {})
        )
        branch_location = (
            self._text(branch_actor_state.get("location"), 160)
            if isinstance(branch_actor_state, dict)
            else ""
        )
        origin = self._text(
            scene_state.get_actor_location(actor) if scene_state is not None else "",
            160,
        ) or self._text(proposal.get("location"), 160)
        action["location"] = (
            branch_location
            if branch_location
            and branch_location in {origin, authorized_destination}
            else origin
        )
        outcome = self._text(action.get("outcome", "partial"), 40).lower()
        if outcome not in {"success", "partial", "fail", "blocked", "complication"}:
            errors.append(f"{prefix}.resolved_action has invalid outcome")
            return errors
        action["outcome"] = outcome
        visibility = self._text(action.get("visibility", "local"), 20).lower()
        if visibility not in {"public", "local", "hidden"}:
            errors.append(f"{prefix}.resolved_action has invalid visibility")
            return errors
        action["visibility"] = visibility
        action["result"] = self._text(action.get("result"), 1200)
        action["private_result"] = (
            self._text(action.get("private_result"), 1200)
            if action["action_kind"] == "observe"
            else ""
        )
        result.setdefault("resolved_actions", []).append(action)

        branch_updates = branch.get("state_updates", {})
        target_updates = result.setdefault(
            "state_updates",
            {"scene": {}, "world_objects": {}, "actor_states": {}},
        )
        for section in ("scene", "world_objects", "actor_states"):
            incoming = branch_updates.get(section, {})
            if incoming is None:
                incoming = {}
            if not isinstance(incoming, dict):
                errors.append(f"{prefix}.state_updates.{section} must be an object")
                continue
            target = target_updates.setdefault(section, {})
            for key, value in incoming.items():
                if key in target and target[key] != value:
                    errors.append(
                        f"{prefix}.state_updates conflicts with deterministic result: "
                        f"{section}.{key}"
                    )
                    continue
                target[key] = deepcopy(value)

        for field_name in LIST_PATCH_FIELDS:
            values = deepcopy(branch.get(field_name, []))
            result.setdefault(field_name, []).extend(values)
        if branch.get("agreement_updates"):
            result.setdefault("contract_updates", []).extend(
                deepcopy(branch["agreement_updates"])
            )
        result["tension_delta"] = float(result.get("tension_delta", 0.0)) + float(
            branch.get("tension_delta", 0.0)
        )
        return errors

    def _sanitize_branch_locations(
        self,
        branch: Dict[str, Any],
        *,
        actor: str,
        proposal: Dict[str, Any],
        scene_state: Any,
        authorized_destination: str,
        prefix: str,
        rejected_writes: List[str],
    ) -> None:
        updates = branch.get("state_updates")
        if not isinstance(updates, dict):
            return
        actor_updates = updates.get("actor_states")
        if not isinstance(actor_updates, dict):
            return
        origin = self._text(
            scene_state.get_actor_location(actor) if scene_state is not None else "",
            160,
        ) or self._text(proposal.get("location"), 160)
        proposal_kind = self._text(proposal.get("action_kind"), 40)
        allowed = {origin}
        if proposal_kind == "move" and authorized_destination:
            allowed.add(authorized_destination)
        for target_actor, raw_update in list(actor_updates.items()):
            if not isinstance(raw_update, dict) or "location" not in raw_update:
                continue
            location = self._text(raw_update.get("location"), 160)
            if target_actor == actor and location in allowed:
                continue
            raw_update.pop("location", None)
            rejected_writes.append(
                f"{prefix}.state_updates.actor_states.{target_actor}.location"
            )
            if not raw_update:
                actor_updates.pop(target_actor, None)

    def _capability_modifiers(
        self, scene_state: Any, *, actor: str, capability: str
    ) -> List[CheckModifier]:
        if not capability or scene_state is None:
            return []
        state = scene_state.get_actor_state(actor)
        raw_capabilities = state.get("capabilities", []) if isinstance(state, dict) else []
        if isinstance(raw_capabilities, str):
            raw_capabilities = [raw_capabilities]
        capabilities = {
            str(item).strip()
            for item in raw_capabilities
            if isinstance(item, str) and str(item).strip()
        } if isinstance(raw_capabilities, list) else set()
        skills = state.get("skills", {}) if isinstance(state, dict) else {}
        if isinstance(skills, dict) and capability in skills:
            try:
                level = min(1.0, max(0.0, float(skills[capability])))
            except (TypeError, ValueError):
                level = 0.5
            return [
                CheckModifier(
                    modifier_id=f"skill:{capability}",
                    delta=(level - 0.5) * 0.6,
                    reason=f"authoritative {capability} skill level",
                )
            ]
        if capability in capabilities:
            return [
                CheckModifier(
                    modifier_id=f"capability:{capability}",
                    delta=0.15,
                    reason=f"actor has authoritative capability {capability}",
                )
            ]
        return [
            CheckModifier(
                modifier_id=f"missing_capability:{capability}",
                delta=-0.15,
                reason=f"actor lacks authoritative capability {capability}",
            )
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
