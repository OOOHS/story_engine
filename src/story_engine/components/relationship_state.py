from typing import Any, Dict, Iterable, Optional

from pydantic import Field

from src.story_engine.core.component import Component


RELATION_METRICS = ("favor", "malice", "trust")


class RelationshipState(Component):
    """
    Canonical directed social graph for actor-to-actor relation meters.
    """

    relations: Dict[str, Dict[str, Dict[str, Any]]] = Field(default_factory=dict)

    @classmethod
    def from_actor_states(cls, actor_states: Dict[str, Dict[str, Any]]) -> "RelationshipState":
        component = cls()
        component.refresh_from_actor_states(actor_states or {})
        return component

    def refresh_from_actor_states(self, actor_states: Dict[str, Dict[str, Any]]) -> None:
        if not isinstance(actor_states, dict):
            return

        for actor, state in actor_states.items():
            if not isinstance(state, dict):
                continue
            for key, value in state.items():
                parsed = self._parse_relation_key(key)
                if not parsed:
                    continue
                metric, target = parsed
                self.set_metric(actor, target, metric, value)

    def sync_actor_states(self, actor_states: Dict[str, Dict[str, Any]]) -> None:
        if not isinstance(actor_states, dict):
            return

        for actor, targets in self.relations.items():
            actor_state = actor_states.setdefault(actor, {})
            for target, metrics in targets.items():
                for metric in RELATION_METRICS:
                    if metric not in metrics:
                        continue
                    actor_state[f"{metric}_{target}"] = metrics[metric]

    def get_metrics(self, actor: Any, target: Any) -> Dict[str, Any]:
        actor_key = str(actor or "").strip()
        target_key = str(target or "").strip()
        if not actor_key or not target_key:
            return {}
        return dict(self.relations.get(actor_key, {}).get(target_key, {}))

    def set_metric(self, actor: Any, target: Any, metric: str, value: Any) -> None:
        actor_key = str(actor or "").strip()
        target_key = str(target or "").strip()
        metric_key = str(metric or "").strip()
        if not actor_key or not target_key or metric_key not in RELATION_METRICS:
            return
        self.relations.setdefault(actor_key, {}).setdefault(target_key, {})[metric_key] = value

    def apply_delta(self, actor: Any, target: Any, **deltas: Any) -> Dict[str, Any]:
        current = self.get_metrics(actor, target)
        updated = dict(current)
        for metric in RELATION_METRICS:
            delta_value = deltas.get(f"{metric}_delta")
            if delta_value is None:
                continue
            current_value = int(updated.get(metric, 0) or 0)
            next_value = current_value + int(delta_value)
            if metric in {"favor", "malice"}:
                next_value = min(5, max(0, next_value))
            updated[metric] = next_value
            self.set_metric(actor, target, metric, next_value)
        return updated

    def get_visible_relations(
        self,
        viewer: Any,
        visible_actors: Iterable[str],
        actor_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> list[Dict[str, Any]]:
        viewer_key = str(viewer or "").strip()
        results = []
        for actor in visible_actors or []:
            actor_key = str(actor or "").strip()
            if not actor_key or actor_key == viewer_key:
                continue
            actor_state = (actor_states or {}).get(actor_key, {}) if isinstance(actor_states, dict) else {}
            metrics = self.get_metrics(actor_key, viewer_key)
            results.append(
                {
                    "actor": actor_key,
                    "bias": actor_state.get("bias") if isinstance(actor_state, dict) else None,
                    "framing_style": actor_state.get("framing_style") if isinstance(actor_state, dict) else None,
                    "territorial": bool(actor_state.get("territorial")) if isinstance(actor_state, dict) else False,
                    "toward_viewer": {
                        "favor": metrics.get("favor"),
                        "malice": metrics.get("malice"),
                        "trust": metrics.get("trust"),
                    },
                }
            )
        return results

    @staticmethod
    def _parse_relation_key(key: Any) -> Optional[tuple[str, str]]:
        text = str(key or "").strip()
        for metric in RELATION_METRICS:
            prefix = f"{metric}_"
            if text.startswith(prefix) and len(text) > len(prefix):
                return metric, text[len(prefix) :]
        return None
