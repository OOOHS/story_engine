from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .narrative_candidates import NarrativeCandidateAuthority

CONSUMED_AUTHORIZATIONS_FLAG = "consumed_character_entry_authorizations"


@dataclass(frozen=True)
class CharacterEntryResolution:
    request: Dict[str, Any] | None = None
    rejected: List[str] = field(default_factory=list)


class CharacterEntryAuthority:
    """Compile a semantic spawn request from a host-issued entry capability.

    The authorization envelope itself (id uniqueness, consumption, validity
    window) is delegated to ``NarrativeCandidateAuthority``, the same gate
    shared with the ``storylet_definition``/``topology`` candidate kinds.
    This method only owns the character-specific field compilation.
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
    ) -> CharacterEntryResolution:
        if request is None:
            return CharacterEntryResolution()
        if not isinstance(request, dict):
            return CharacterEntryResolution(rejected=["spawn_character:not_an_object"])

        resolution = self._authority.resolve_authorization(
            request,
            domain="spawn_character",
            authorizations=authorizations,
            scene_state=scene_state,
            consumed_flag=CONSUMED_AUTHORIZATIONS_FLAG,
            current_step=current_step,
        )
        if resolution.rejected:
            return CharacterEntryResolution(rejected=resolution.rejected)
        authorization = resolution.authorization
        authorization_id = self._text(request.get("authorization_id"), 160)

        name = self._text(authorization.get("name"), 120)
        location = self._text(authorization.get("location"), 160)
        if not name or not location:
            return CharacterEntryResolution(
                rejected=[f"spawn_character:incomplete_authorization:{authorization_id}"]
            )
        if scene_state and location not in scene_state.get_known_locations():
            return CharacterEntryResolution(
                rejected=[f"spawn_character:unknown_authorized_location:{authorization_id}"]
            )

        profile_mode = self._text(
            authorization.get("profile_mode", "fixed"), 20
        ).lower()
        if profile_mode not in {"fixed", "semantic"}:
            profile_mode = "fixed"
        canonical = {
            "authorization_id": authorization_id,
            "name": name,
            "role": self._text(authorization.get("role"), 160) or "来客",
            "location": location,
            "initial_state": deepcopy(authorization.get("initial_state", {}))
            if isinstance(authorization.get("initial_state", {}), dict)
            else {},
            "personality": self._text(authorization.get("personality"), 600),
            "goals": self._text_list(authorization.get("goals", []), 6, 300),
            "activation_policy": self._text(
                authorization.get("activation_policy", "background"), 20
            ),
            "background_interval": authorization.get("background_interval", 3),
            "initial_beliefs": deepcopy(authorization.get("initial_beliefs", [])),
            "initial_secrets": deepcopy(authorization.get("initial_secrets", [])),
            "initial_commitments": deepcopy(
                authorization.get("initial_commitments", [])
            ),
            "initial_needs": deepcopy(authorization.get("initial_needs", [])),
            "risk_tolerance": authorization.get("risk_tolerance", 0.5),
        }
        if profile_mode == "semantic":
            canonical["personality"] = (
                self._text(request.get("personality"), 600)
                or canonical["personality"]
                or "尚未显露"
            )
            semantic_goals = self._text_list(request.get("goals", []), 6, 300)
            if semantic_goals:
                canonical["goals"] = semantic_goals
        canonical["personality"] = canonical["personality"] or "尚未显露"
        return CharacterEntryResolution(request=canonical)

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
