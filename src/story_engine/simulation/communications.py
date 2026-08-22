from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple


@dataclass(frozen=True)
class CommunicationResolution:
    """Deterministic host settlement for every ``communicate`` proposal.

    Speaking is not a judgment call. Legality already decides, mechanically,
    whether a character can reach her target (co-located, not blocked); once
    that is true the words leave her mouth exactly as proposed. Whether a
    listener believes a Claim, whether an agreement forms, whether anyone
    cares -- that is settled downstream by trust-weighted belief formation
    and contract dynamics, never by a narrative "did this communication
    succeed" verdict.

    The semantic GM still *sees* ``communicate`` intents (it may still
    contribute ``knowledge_updates``/``social_impacts`` -- content-level
    interpretation of what was said has no other pathway), but it no longer
    has a say in whether the utterance itself landed: any resolved_action it
    writes for these actors is discarded here and replaced unconditionally.
    """

    resolved_actions: Tuple[Dict[str, Any], ...] = ()
    consumed_actors: FrozenSet[str] = field(default_factory=frozenset)


class CommunicationResolver:
    """Settles every legality-allowed ``communicate`` proposal without the
    semantic GM.

    Blocked communicate proposals (target not co-located, etc.) are left
    alone here -- ``SimulationControl._enforce_legality`` already turns a
    ``block`` verdict into a deterministic ``blocked`` resolved_action for
    any actor with no matching entry, regardless of whether the GM ever saw
    that actor's intent. This resolver only has to cover the case that used
    to require an LLM: "legality allows it, so what happened?" The answer is
    always the same -- she said exactly what she proposed.
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
