from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class CharacterEntryResolution:
    request: Dict[str, Any] | None = None
    rejected: List[str] = field(default_factory=list)


class CharacterEntryAuthority:
    """Compile a semantic spawn request from a host-issued entry capability."""

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
        authorization_id = self._text(request.get("authorization_id"), 160)
        if not authorization_id:
            return CharacterEntryResolution(
                rejected=["spawn_character:missing_authorization_id"]
            )
        records: Dict[str, Dict[str, Any]] = {}
        duplicates = set()
        for item in authorizations or []:
            if not isinstance(item, dict):
                continue
            item_id = self._text(item.get("authorization_id"), 160)
            if not item_id:
                continue
            if item_id in records:
                duplicates.add(item_id)
            records[item_id] = item
        if authorization_id in duplicates:
            return CharacterEntryResolution(
                rejected=[f"spawn_character:ambiguous_authorization:{authorization_id}"]
            )
        authorization = records.get(authorization_id)
        if authorization is None:
            return CharacterEntryResolution(
                rejected=[f"spawn_character:unknown_authorization:{authorization_id}"]
            )
        raw_consumed = (
            scene_state.get_scene_flag(
                "consumed_character_entry_authorizations", []
            )
            if scene_state
            else []
        )
        if not isinstance(raw_consumed, list):
            return CharacterEntryResolution(
                rejected=["spawn_character:invalid_consumed_authorization_ledger"]
            )
        consumed = {
            self._text(item, 160) for item in raw_consumed if self._text(item, 160)
        }
        if authorization_id in consumed:
            return CharacterEntryResolution(
                rejected=[f"spawn_character:consumed_authorization:{authorization_id}"]
            )
        try:
            not_before = int(authorization.get("not_before_step", current_step))
            expires_step = int(authorization.get("expires_step", current_step))
        except (TypeError, ValueError):
            return CharacterEntryResolution(
                rejected=[f"spawn_character:invalid_window:{authorization_id}"]
            )
        if int(current_step) < not_before or int(current_step) > expires_step:
            return CharacterEntryResolution(
                rejected=[f"spawn_character:authorization_out_of_window:{authorization_id}"]
            )

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
                authorization.get("activation_policy", "auto"), 20
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
