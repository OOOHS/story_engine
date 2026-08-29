from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.config.config import config
from src.story_engine.prefabs.templates import create_agent


@dataclass(frozen=True)
class CharacterSpawnPlan:
    name: str
    entity: Any = field(repr=False, compare=False)
    actor_state: Dict[str, Any] = field(default_factory=dict)
    authorization_id: str = ""


@dataclass(frozen=True)
class CharacterPreparation:
    plan: Optional[CharacterSpawnPlan] = None
    errors: List[str] = field(default_factory=list)


class CharacterLifecycle:
    """Two-phase dynamic character lifecycle across ECS, world and runtime."""

    def prepare(
        self,
        entities: Dict[str, Any],
        scene_state: Any,
        request: Any,
        *,
        agent_runtime: str,
        player_name: Any = None,
        agent_registry: Any = None,
        memory_namespace: str | None = None,
    ) -> CharacterPreparation:
        if request is None:
            return CharacterPreparation()
        if not isinstance(request, dict):
            return CharacterPreparation(errors=["spawn_character must be an object or null"])
        if not scene_state:
            return CharacterPreparation(errors=["spawn_character requires scene state"])

        errors: List[str] = []
        name = self._text(request.get("name"), 120)
        if not name:
            errors.append("spawn_character requires a name")
        elif (
            name in entities
            or name in scene_state.actor_states
            or name in scene_state.world_objects
            or (
                agent_registry is not None
                and hasattr(agent_registry, "is_registered")
                and agent_registry.is_registered(name)
            )
        ):
            errors.append(f"spawn_character name already exists: {name}")

        dynamic_names = scene_state.get_scene_flag("dynamic_character_names", [])
        if not isinstance(dynamic_names, list):
            errors.append("dynamic_character_names must be a list")
            dynamic_names = []
        normalized_dynamic_names = [
            str(item).strip() for item in dynamic_names if str(item).strip()
        ]
        if len(normalized_dynamic_names) != len(set(normalized_dynamic_names)):
            errors.append("dynamic_character_names contains duplicates")
        try:
            limit = max(
                0, int(scene_state.get_scene_flag("max_dynamic_characters", 6) or 0)
            )
        except (TypeError, ValueError):
            limit = 0
            errors.append("max_dynamic_characters must be an integer")
        if len(normalized_dynamic_names) >= limit:
            errors.append("spawn_character exceeds max_dynamic_characters")

        raw_state = request.get("initial_state", {})
        if not isinstance(raw_state, dict):
            errors.append("spawn_character.initial_state must be an object")
            raw_state = {}
        if len(raw_state) > 40:
            errors.append("spawn_character.initial_state has too many fields")
        state = deepcopy(raw_state)
        requested_location = request.get("location") or state.get("location")
        if requested_location not in scene_state.get_known_locations():
            requested_location = (
                scene_state.get_actor_location(player_name) if player_name else None
            )
        if requested_location:
            state["location"] = requested_location
        if not state.get("location"):
            errors.append("spawn_character has no valid location")
        if errors:
            return CharacterPreparation(errors=errors)

        role = self._text(request.get("role"), 160) or "路人"
        personality = self._text(request.get("personality"), 600) or "尚未显露"
        goals = self._text_list(request.get("goals", []), limit=6, item_limit=300)
        policy = str(request.get("activation_policy", "auto"))
        if policy not in {"auto", "foreground", "background", "dormant"}:
            policy = "auto"
        try:
            background_interval = max(
                1, int(request.get("background_interval", 3) or 3)
            )
        except (TypeError, ValueError):
            background_interval = 3
        raw_beliefs = request.get("initial_beliefs", [])
        if not isinstance(raw_beliefs, list):
            raw_beliefs = []
        initial_beliefs = [
            deepcopy(item) for item in raw_beliefs if isinstance(item, dict)
        ][:12]
        initial_secrets = self._text_list(
            request.get("initial_secrets", []), limit=12, item_limit=500
        )
        initial_commitments = self._text_list(
            request.get("initial_commitments", []), limit=12, item_limit=300
        )
        raw_needs = request.get("initial_needs", [])
        if not isinstance(raw_needs, list):
            raw_needs = []
        initial_needs = [
            deepcopy(item) for item in raw_needs if isinstance(item, dict)
        ][:12]
        try:
            risk_tolerance = min(
                1.0, max(0.0, float(request.get("risk_tolerance", 0.5)))
            )
        except (TypeError, ValueError):
            risk_tolerance = 0.5
        base_config = config.get_component_config("agent").copy()
        try:
            entity = create_agent(
                name=name,
                role=role,
                personality=personality,
                goals=goals,
                agent_runtime=agent_runtime,
                model_config=base_config,
                activation_policy=policy,
                background_interval=background_interval,
                initial_beliefs=initial_beliefs,
                initial_secrets=initial_secrets,
                initial_commitments=initial_commitments,
                initial_needs=initial_needs,
                risk_tolerance=risk_tolerance,
                memory_namespace=memory_namespace,
            )
        except Exception as exc:
            return CharacterPreparation(
                errors=[f"spawn_character entity preparation failed: {type(exc).__name__}:{exc}"]
            )
        return CharacterPreparation(
            plan=CharacterSpawnPlan(
                name=name,
                entity=entity,
                actor_state=state,
                authorization_id=self._text(request.get("authorization_id"), 160),
            )
        )

    def stage(self, scene_state: Any, plan: Optional[CharacterSpawnPlan]) -> List[str]:
        if plan is None:
            return []
        if plan.name in scene_state.actor_states:
            return [f"spawn_character actor already exists while staging: {plan.name}"]
        if plan.actor_state.get("location") not in scene_state.get_known_locations():
            return [f"spawn_character location disappeared while staging: {plan.name}"]
        dynamic_names = scene_state.get_scene_flag("dynamic_character_names", [])
        if not isinstance(dynamic_names, list):
            return ["dynamic_character_names must be a list"]
        normalized = [str(item).strip() for item in dynamic_names if str(item).strip()]
        if plan.name in normalized:
            return [f"spawn_character already exists in lifecycle ledger: {plan.name}"]
        consumed = []
        if plan.authorization_id:
            consumed = list(
                scene_state.get_scene_flag(
                    "consumed_character_entry_authorizations", []
                )
                or []
            )
            if plan.authorization_id in consumed:
                return [
                    "spawn_character authorization was consumed while staging: "
                    f"{plan.authorization_id}"
                ]
        scene_state.actor_states[plan.name] = deepcopy(plan.actor_state)
        normalized.append(plan.name)
        flag_updates = {"dynamic_character_names": normalized}
        if plan.authorization_id:
            consumed.append(plan.authorization_id)
            flag_updates["consumed_character_entry_authorizations"] = consumed
        scene_state.update_scene_flags(flag_updates)
        return []

    def finalize(
        self,
        entities: Dict[str, Any],
        plan: Optional[CharacterSpawnPlan],
        *,
        register_agent: Any = None,
        unregister_agent: Any = None,
        agent_registry: Any = None,
    ) -> List[str]:
        if plan is None:
            return []
        try:
            if not callable(register_agent):
                raise RuntimeError("agent registration callback is required")
            if agent_registry is None or not hasattr(
                agent_registry, "is_registered"
            ):
                raise RuntimeError("live AgentRegistry is required")
            register_agent(plan.entity)
            if not agent_registry.is_registered(plan.entity):
                raise RuntimeError("agent registration callback did not register entity")
            entities[plan.name] = plan.entity
        except Exception as exc:
            entities.pop(plan.name, None)
            if callable(unregister_agent):
                try:
                    unregister_agent(plan.entity)
                except Exception:
                    pass
            raise RuntimeError(
                f"spawn_character finalization failed: {type(exc).__name__}:{exc}"
            ) from exc
        return [plan.name]

    def _text_list(self, value: Any, *, limit: int, item_limit: int) -> List[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [
            text
            for item in value[:limit]
            if (text := self._text(item, item_limit))
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
