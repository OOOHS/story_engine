from copy import deepcopy
from math import isfinite
from typing import Any, Dict, List, Tuple

from src.story_engine.environment.world_object_lifecycle import WorldObjectLifecycle


class ResourceContestResolver:
    """Resolve simultaneous claims on finite or exclusive world objects.

    The resolver runs after the model has proposed structured consequences but
    before those consequences reach the authoritative transaction.  It never
    creates a successful action.  It only removes mutually incompatible object
    operations and rewrites affected action outcomes so array order cannot act
    as an accidental arbitration rule.
    """

    CONTESTABLE_OPERATIONS = {
        "relocate",
        "set_visibility",
        "set_container_state",
        "use",
        "destroy",
    }

    def resolve(
        self,
        scene_state: Any,
        result: Dict[str, Any],
        *,
        intents: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return result
        resolved = deepcopy(result)
        operations = resolved.get("object_lifecycle", [])
        if not scene_state or not isinstance(operations, list) or len(operations) < 2:
            # The trace is engine-owned.  Never preserve a resolver/model
            # supplied field when there was no contest to derive it from.
            resolved["resource_contests"] = []
            return resolved

        grouped: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                continue
            operation_name = str(operation.get("operation", "")).strip()
            object_id = str(operation.get("object_id", "")).strip()
            if operation_name not in self.CONTESTABLE_OPERATIONS or not object_id:
                continue
            if object_id not in scene_state.world_objects or scene_state.is_location(object_id):
                # Preserve malformed requests for the transaction's canonical
                # validation error instead of silently repairing model output.
                continue
            grouped.setdefault(object_id, []).append((index, operation))

        priorities = self._proposal_priorities(intents or [])
        retained = set(range(len(operations)))
        traces: List[Dict[str, Any]] = []
        actor_claims: Dict[str, Dict[str, int]] = {}

        for object_id in sorted(grouped):
            claims = grouped[object_id]
            actors = {
                self._actor(operation)
                for _, operation in claims
                if self._actor(operation)
            }
            operation_names = {
                str(operation.get("operation", "")).strip()
                for _, operation in claims
            }
            if len(actors) < 2 and operation_names != {"use"}:
                continue

            decision = self._resolve_group(
                scene_state,
                object_id,
                claims,
                priorities,
            )
            if decision is None:
                continue
            kept_indices, mode, available_units = decision
            dropped_indices = {index for index, _ in claims}.difference(kept_indices)
            if not dropped_indices:
                continue
            retained.difference_update(dropped_indices)

            winners = []
            losers = []
            partial = []
            for actor in sorted(actors):
                actor_indices = {
                    index
                    for index, operation in claims
                    if self._actor(operation) == actor
                }
                kept_count = len(actor_indices.intersection(kept_indices))
                dropped_count = len(actor_indices.intersection(dropped_indices))
                totals = actor_claims.setdefault(actor, {"kept": 0, "dropped": 0})
                totals["kept"] += kept_count
                totals["dropped"] += dropped_count
                if kept_count:
                    winners.append(actor)
                if dropped_count and kept_count:
                    partial.append(actor)
                elif dropped_count:
                    losers.append(actor)

            traces.append(
                {
                    "object_id": object_id,
                    "mode": mode,
                    "available_units": available_units,
                    "winners": winners,
                    "losers": losers,
                    "partial": partial,
                    "dropped_claims": [
                        {
                            "actor": self._actor(operation),
                            "operation": str(operation.get("operation", "")).strip(),
                            "affordance_id": str(
                                operation.get("affordance_id", "")
                            ).strip()
                            or None,
                        }
                        for index, operation in claims
                        if index in dropped_indices
                    ],
                    "reason": self._reason(mode),
                }
            )

        resolved["object_lifecycle"] = [
            operation for index, operation in enumerate(operations) if index in retained
        ]
        resolved["resource_contests"] = traces
        if traces:
            self._rewrite_actions(resolved, actor_claims)
        return resolved

    def _resolve_group(
        self,
        scene_state: Any,
        object_id: str,
        claims: List[Tuple[int, Dict[str, Any]]],
        priorities: Dict[str, float],
    ) -> Tuple[set[int], str, int | None] | None:
        operations = {str(item.get("operation", "")).strip() for _, item in claims}
        if any(not self._actor(item) for _, item in claims):
            return None
        if operations == {"set_visibility"}:
            values = [item.get("hidden") for _, item in claims]
            if values and all(value == values[0] for value in values[1:]):
                return None
        if operations == {"set_container_state"}:
            values = [item.get("open") for _, item in claims]
            if values and all(value == values[0] for value in values[1:]):
                return None

        if operations == {"use"}:
            return self._resolve_use_group(
                scene_state,
                object_id,
                claims,
                priorities,
            )

        winner = min(
            {self._actor(operation) for _, operation in claims},
            key=lambda actor: self._actor_sort_key(
                scene_state, actor, priorities, 0
            ),
        )
        kept = {
            index for index, operation in claims if self._actor(operation) == winner
        }
        return kept, "exclusive_claim", 1

    def _resolve_use_group(
        self,
        scene_state: Any,
        object_id: str,
        claims: List[Tuple[int, Dict[str, Any]]],
        priorities: Dict[str, float],
    ) -> Tuple[set[int], str, int | None] | None:
        state = scene_state.get_object_state(object_id)
        parsed: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for index, operation in claims:
            affordance_id = str(operation.get("affordance_id", "")).strip()
            affordance = WorldObjectLifecycle.get_affordance(state, affordance_id)
            if affordance is None:
                return None
            consumes = affordance.get("consumes", False)
            exclusive = affordance.get("exclusive", False)
            if not isinstance(consumes, bool) or not isinstance(exclusive, bool):
                return None
            parsed.append((index, operation, affordance))

        consuming = [item for item in parsed if item[2].get("consumes", False)]
        exclusive = [item for item in parsed if item[2].get("exclusive", False)]
        if not consuming and not exclusive:
            return None

        # A consuming claim mixed with a shareable/non-consuming affordance has
        # no safe generic ordering: the former can remove the object while the
        # latter is using it.  Treat the whole object as one exclusive claim.
        if consuming and len(consuming) != len(parsed):
            winner = min(
                {self._actor(operation) for _, operation, _ in parsed},
                key=lambda actor: self._actor_sort_key(
                    scene_state, actor, priorities, 0
                ),
            )
            kept = {
                index
                for index, operation, _ in parsed
                if self._actor(operation) == winner
            }
            return kept, "exclusive_claim", 1

        ordered = sorted(
            parsed,
            key=lambda item: self._actor_sort_key(
                scene_state,
                self._actor(item[1]),
                priorities,
                item[0],
            ),
        )
        if exclusive:
            return {ordered[0][0]}, "exclusive_use", 1

        quantity = state.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return None
        return {
            index for index, _, _ in ordered[:quantity]
        }, "consuming_quota", quantity

    def _proposal_priorities(
        self,
        intents: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        priorities: Dict[str, float] = {}
        for intent in intents:
            if not isinstance(intent, dict):
                continue
            actor = self._actor(intent)
            if not actor:
                continue
            raw_priority = intent.get("proposal_priority", 0.5)
            try:
                priority = float(raw_priority)
            except (TypeError, ValueError):
                priority = 0.5
            if not isfinite(priority):
                priority = 0.5
            priority = max(0.0, min(1.0, priority))
            if str(intent.get("source", "")).strip() == "manual":
                priority = 1.0
            priorities[actor] = max(priority, priorities.get(actor, 0.0))
        return priorities

    def _actor_sort_key(
        self,
        scene_state: Any,
        actor: str,
        priorities: Dict[str, float],
        operation_index: int,
    ) -> Tuple[float, float, str, int]:
        state = scene_state.get_actor_state(actor) if scene_state else {}
        raw_initiative = state.get("initiative", 0.0) if isinstance(state, dict) else 0.0
        if isinstance(raw_initiative, bool) or not isinstance(raw_initiative, (int, float)):
            initiative = 0.0
        else:
            initiative = max(-1.0, min(1.0, float(raw_initiative)))
        return (
            -priorities.get(actor, 0.5),
            -initiative,
            actor,
            operation_index,
        )

    def _rewrite_actions(
        self,
        result: Dict[str, Any],
        actor_claims: Dict[str, Dict[str, int]],
    ) -> None:
        actions = result.get("resolved_actions", [])
        if not isinstance(actions, list):
            return
        remaining_operations = result.get("object_lifecycle", [])
        remaining_by_actor: Dict[str, int] = {}
        for operation in remaining_operations if isinstance(remaining_operations, list) else []:
            if isinstance(operation, dict):
                actor = self._actor(operation)
                remaining_by_actor[actor] = remaining_by_actor.get(actor, 0) + 1

        for actor, counts in actor_claims.items():
            if counts.get("dropped", 0) <= 0:
                continue
            action = next(
                (
                    item
                    for item in actions
                    if isinstance(item, dict) and self._actor(item) == actor
                ),
                None,
            )
            if action is None:
                continue
            if counts.get("kept", 0) or remaining_by_actor.get(actor, 0):
                action["outcome"] = "partial"
                action["result"] = (
                    "行动只完成了一部分；同时发生的资源竞争阻止了其余尝试。"
                )
            else:
                action["outcome"] = "blocked"
                action["result"] = "同时发生的资源竞争使该行动未能完成。"

    @staticmethod
    def _actor(item: Dict[str, Any]) -> str:
        return " ".join(str(item.get("actor", "")).split()).strip()[:120]

    @staticmethod
    def _reason(mode: str) -> str:
        return {
            "consuming_quota": "simultaneous claims exceeded the available quantity",
            "exclusive_use": "the affordance permits only one simultaneous user",
            "exclusive_claim": "simultaneous operations would create incompatible object states",
        }.get(mode, "simultaneous claims were mutually incompatible")
