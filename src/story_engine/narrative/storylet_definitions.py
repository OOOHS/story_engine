"""Runtime registration of brand-new storylets ("storylet_definition" candidates).

``scenario.storylets`` is otherwise entirely static (see ``storylets.py``);
this module is the one new *capability* this candidate-registration effort
adds, not just a refactor of something that already existed. It follows the
exact two-phase shape ``CharacterEntryAuthority``/``CharacterLifecycle``
established: a Host-issued authorization must be cited, ``prepare()``
validates the compiled payload against the scenario/staged pool, and
``stage()`` re-checks the narrower staging-time invariants before writing.

Governance is deliberately as strict as character entry (pre-authorization
required) because a new storylet is a permanent structural addition to the
scenario's opportunity space, not an ephemeral world fact.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from src.story_engine.environment.narrative_candidates import (
    CandidateLedger,
    NarrativeCandidateAuthority,
)
from src.story_engine.scenarios.config import StoryletConfig

CONSUMED_AUTHORIZATIONS_FLAG = "consumed_storylet_definition_authorizations"
DYNAMIC_STORYLET_IDS_FLAG = "dynamic_storylet_ids"
DYNAMIC_STORYLETS_FLAG = "dynamic_storylets"
MAX_DYNAMIC_STORYLETS_FLAG = "max_dynamic_storylets"


@dataclass(frozen=True)
class StoryletDefinitionResolution:
    request: Optional[Dict[str, Any]] = None
    rejected: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoryletDefinitionPlan:
    storylet_id: str
    payload: Dict[str, Any]
    authorization_id: str = ""


@dataclass(frozen=True)
class StoryletDefinitionPreparation:
    plan: Optional[StoryletDefinitionPlan] = None
    errors: List[str] = field(default_factory=list)


class StoryletDefinitionAuthority:
    """Compile a semantic ``storylet_definition`` candidate from a host-issued
    authorization. The authorization envelope (id/consumption/window) is
    delegated to ``NarrativeCandidateAuthority``; this only owns the
    ``StoryletConfig``-shaped field compilation.
    """

    def __init__(self) -> None:
        self._authority = NarrativeCandidateAuthority()

    def resolve(
        self,
        request: Any,
        *,
        authorizations: Any,
        scene_state: Any,
        current_step: int,
    ) -> StoryletDefinitionResolution:
        if request is None:
            return StoryletDefinitionResolution()
        if not isinstance(request, dict):
            return StoryletDefinitionResolution(
                rejected=["storylet_definition:not_an_object"]
            )

        resolution = self._authority.resolve_authorization(
            request,
            domain="storylet_definition",
            authorizations=authorizations,
            scene_state=scene_state,
            consumed_flag=CONSUMED_AUTHORIZATIONS_FLAG,
            current_step=current_step,
        )
        if resolution.rejected:
            return StoryletDefinitionResolution(rejected=resolution.rejected)
        authorization = resolution.authorization
        authorization_id = self._text(request.get("authorization_id"), 160)

        raw_conditions = authorization.get("conditions", [])
        conditions = [
            item for item in raw_conditions[:20] if isinstance(item, dict)
        ] if isinstance(raw_conditions, list) else []
        payload = {
            "storylet_id": self._text(authorization.get("storylet_id"), 120),
            "intent": self._text(authorization.get("intent"), 600),
            "conditions": conditions,
            "priority": authorization.get("priority", 0),
            "one_shot": bool(authorization.get("one_shot", False)),
            "tags": self._text_list(authorization.get("tags", []), 12, 60),
            "situation_kinds": self._text_list(
                authorization.get("situation_kinds", []), 8, 60
            ),
            "situation_tags": self._text_list(
                authorization.get("situation_tags", []), 8, 60
            ),
        }
        try:
            validated = StoryletConfig(**payload)
        except ValidationError as exc:
            return StoryletDefinitionResolution(
                rejected=[
                    f"storylet_definition:invalid_payload:{authorization_id}:{exc}"
                ]
            )
        canonical = validated.model_dump()
        canonical["authorization_id"] = authorization_id
        return StoryletDefinitionResolution(request=canonical)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]

    def _text_list(self, value: Any, limit: int, item_limit: int) -> List[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [
            text
            for item in value[:limit]
            if (text := self._text(item, item_limit))
        ]


class StoryletDefinitionLifecycle:
    """Two-phase staging of a newly-registered storylet into the dynamic pool."""

    def prepare(
        self,
        scenario: Any,
        scene_state: Any,
        request: Any,
    ) -> StoryletDefinitionPreparation:
        if request is None:
            return StoryletDefinitionPreparation()
        if not isinstance(request, dict):
            return StoryletDefinitionPreparation(
                errors=["storylet_definition must be an object or null"]
            )
        if not scene_state:
            return StoryletDefinitionPreparation(
                errors=["storylet_definition requires scene state"]
            )

        storylet_id = str(request.get("storylet_id", "")).strip()
        errors: List[str] = []
        if not storylet_id:
            errors.append("storylet_definition requires a storylet_id")
        static_ids = {
            str(getattr(item, "storylet_id", "")).strip()
            for item in getattr(scenario, "storylets", None) or []
        }
        dynamic_ids = CandidateLedger.normalized_names(
            scene_state, DYNAMIC_STORYLET_IDS_FLAG
        )
        if storylet_id and (storylet_id in static_ids or storylet_id in dynamic_ids):
            errors.append(
                f"storylet_definition storylet_id already exists: {storylet_id}"
            )
        cap_error = CandidateLedger.check_cap(
            scene_state,
            names_flag=DYNAMIC_STORYLET_IDS_FLAG,
            cap_flag=MAX_DYNAMIC_STORYLETS_FLAG,
            default_cap=12,
        )
        if cap_error:
            errors.append(f"storylet_definition {cap_error}")
        if errors:
            return StoryletDefinitionPreparation(errors=errors)

        payload = {k: v for k, v in request.items() if k != "authorization_id"}
        return StoryletDefinitionPreparation(
            plan=StoryletDefinitionPlan(
                storylet_id=storylet_id,
                payload=payload,
                authorization_id=str(request.get("authorization_id", "")).strip(),
            )
        )

    def stage(
        self,
        scene_state: Any,
        plan: Optional[StoryletDefinitionPlan],
    ) -> List[str]:
        if plan is None:
            return []
        # ``scenario.storylets`` is static for the whole run, so only the
        # dynamic pool -- which can change within this same step -- needs a
        # fresh re-check here; the static half was already checked in
        # ``prepare()``.
        dynamic_ids = CandidateLedger.normalized_names(
            scene_state, DYNAMIC_STORYLET_IDS_FLAG
        )
        if plan.storylet_id in dynamic_ids:
            return [
                "storylet_definition storylet_id already exists while staging: "
                f"{plan.storylet_id}"
            ]
        if plan.authorization_id:
            consumed = CandidateLedger.normalized_names(
                scene_state, CONSUMED_AUTHORIZATIONS_FLAG
            )
            if plan.authorization_id in consumed:
                return [
                    "storylet_definition authorization was consumed while "
                    f"staging: {plan.authorization_id}"
                ]

        dynamic_storylets = list(
            scene_state.get_scene_flag(DYNAMIC_STORYLETS_FLAG, []) or []
        )
        dynamic_storylets.append(plan.payload)
        scene_state.update_scene_flags({DYNAMIC_STORYLETS_FLAG: dynamic_storylets})
        CandidateLedger.append_name(
            scene_state, DYNAMIC_STORYLET_IDS_FLAG, plan.storylet_id
        )
        if plan.authorization_id:
            CandidateLedger.consume_authorization(
                scene_state, CONSUMED_AUTHORIZATIONS_FLAG, plan.authorization_id
            )
        return []
