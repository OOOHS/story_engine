from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple


@dataclass(frozen=True)
class CommunicationResolution:
    """Deterministic speech results for the explicit offline rule baseline.

    Production SimulationControl supplies semantically resolved delivery,
    recipients and visibility; SimulationSystem preserves those results.
    """

    resolved_actions: Tuple[Dict[str, Any], ...] = ()
    consumed_actors: FrozenSet[str] = field(default_factory=frozenset)


class CommunicationResolver:
    """Resolve ordinary co-located speech for HostRuleSimulationControl.

    The offline legality pass settles blocked proposals. This helper emits
    literal utterances for the remaining allowed proposals.
    """

    def resolve(
        self,
        *,
        intents: Iterable[Dict[str, Any]],
        legality_checks: Iterable[Dict[str, Any]],
        scene_state: Any = None,
    ) -> CommunicationResolution:
        blocked_actors = {
            str(check.get("actor", "")).strip()
            for check in legality_checks or []
            if isinstance(check, dict)
            and str(check.get("action_kind", "")).strip() == "communicate"
            and str(check.get("verdict", "allow")).strip() == "block"
        }
        resolved: List[Dict[str, Any]] = []
        consumed: set = set()
        for item in intents or []:
            if not isinstance(item, dict):
                continue
            actor = str(item.get("actor", "")).strip()
            if (
                not actor
                or actor == "World"
                or str(item.get("action_kind", "")).strip() != "communicate"
            ):
                continue
            if actor in blocked_actors:
                continue
            intent_text = str(item.get("intent", ""))
            location = (
                scene_state.get_actor_location(actor) if scene_state else None
            )
            resolved.append(
                {
                    "actor": actor,
                    "intent": intent_text,
                    "action_kind": "communicate",
                    "action_target": str(item.get("action_target", "")),
                    "outcome": "success",
                    "location": location,
                    "result": intent_text,
                    # Rendering's resolved_actions convention treats "public"
                    # as "narratable to whoever shares this location" (it is
                    # still location-filtered downstream) -- not "broadcast
                    # world-wide". Ordinary speech in a room is exactly that.
                    "visibility": "public",
                }
            )
            consumed.add(actor)
        return CommunicationResolution(
            resolved_actions=tuple(resolved),
            consumed_actors=frozenset(consumed),
        )
