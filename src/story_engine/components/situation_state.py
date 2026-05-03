from copy import deepcopy
from typing import Dict, Any, List, Optional

from pydantic import Field

from src.story_engine.core.component import Component


class SituationState(Component):
    """
    Stores mid-level situation slices derived from authoritative world state.
    Situations do not replace truth; they organize it into playable scene units.
    """

    active_situations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    resolved_situations: List[Dict[str, Any]] = Field(default_factory=list)
    focus_situation_id: Optional[str] = None
    current_step: int = 0

    def replace_active(
        self,
        situations: List[Dict[str, Any]],
        focus_situation_id: Optional[str],
        current_step: int,
    ) -> None:
        previous_active = dict(self.active_situations or {})
        next_active: Dict[str, Dict[str, Any]] = {}

        for item in situations or []:
            if not isinstance(item, dict):
                continue
            situation_id = str(item.get("situation_id", "")).strip()
            if not situation_id:
                continue
            next_active[situation_id] = deepcopy(item)

        for situation_id, prior in previous_active.items():
            if situation_id in next_active:
                continue
            resolved = deepcopy(prior)
            resolved["status"] = "resolved"
            resolved["ended_step"] = current_step
            self.resolved_situations.append(resolved)

        self.active_situations = next_active
        self.focus_situation_id = focus_situation_id
        self.current_step = int(current_step)
        if len(self.resolved_situations) > 24:
            self.resolved_situations = self.resolved_situations[-24:]

    def get_focus_situation(self) -> Dict[str, Any]:
        if self.focus_situation_id and self.focus_situation_id in self.active_situations:
            return deepcopy(self.active_situations[self.focus_situation_id])
        return {}

    def build_packet(self, limit: int = 8) -> Dict[str, Any]:
        active = sorted(
            (deepcopy(item) for item in self.active_situations.values()),
            key=self._sort_key,
            reverse=True,
        )
        focus_situation = self.get_focus_situation()
        player_visible = [
            item for item in active
            if str(item.get("visibility", "")).strip() in {"player_visible", "rumor"}
        ]
        background = [
            item for item in active
            if str(item.get("visibility", "")).strip() not in {"player_visible", "rumor"}
        ]
        return {
            "focus_situation": focus_situation,
            "active_situations": active[:limit],
            "player_visible_situations": player_visible[:limit],
            "background_situations": background[:limit],
            "resolved_situations": deepcopy(self.resolved_situations[-6:]),
        }

    def get_snapshot(self) -> Dict[str, Any]:
        return {
            "focus_situation_id": self.focus_situation_id,
            "current_step": self.current_step,
            "active_situations": deepcopy(list(self.active_situations.values())),
            "resolved_situations": deepcopy(self.resolved_situations),
        }

    @staticmethod
    def _sort_key(item: Dict[str, Any]) -> Any:
        status = str(item.get("status", "")).strip()
        visibility = str(item.get("visibility", "")).strip()
        focus_score = int(item.get("focus_score", 0) or 0)
        status_rank = {"active": 3, "scheduled": 2, "cooling": 1, "resolved": 0}.get(status, 0)
        visibility_rank = {"player_visible": 3, "rumor": 2, "hidden": 1}.get(visibility, 0)
        return (
            focus_score,
            status_rank,
            visibility_rank,
            len(item.get("participants", []) or []),
            str(item.get("situation_id", "")),
        )
