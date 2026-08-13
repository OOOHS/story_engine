from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class ObjectDeliveryResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, str], ...] = ()


class ObjectDeliveryResolver:
    """Compile a positive single-owner delivery into Host object relocation."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        intents: Iterable[Dict[str, Any]],
        scene_state: Any,
    ) -> ObjectDeliveryResolution:
        references = {
            str(item.get("actor", "")).strip(): (
                str(item.get("action_target", "")).strip(),
                str(item.get("action_delivery_recipient", "")).strip(),
            )
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("action_kind", "")).strip() == "interact"
            and str(item.get("action_target", "")).strip()
            and str(item.get("action_delivery_recipient", "")).strip()
        }
        if not references:
            return ObjectDeliveryResolution(result=result)
        operations = result.get("object_lifecycle", [])
        if not isinstance(operations, list):
            operations = []
        delivery_objects = {object_id for object_id, _ in references.values()}
        retained = [
            item
            for item in operations
            if not isinstance(item, dict)
            or str(item.get("object_id", "")).strip() not in delivery_objects
        ]
        traces = []
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            reference = references.get(actor)
            if reference is None:
                continue
            object_id, recipient = reference
            if str(action.get("outcome", "")).strip() not in self.POSITIVE_OUTCOMES:
                continue
            state = scene_state.get_object_state(object_id) if scene_state else {}
            valid = (
                actor in (scene_state.actor_states if scene_state else {})
                and recipient in (scene_state.actor_states if scene_state else {})
                and str(state.get("owner") or "").strip() == actor
                and not bool(state.get("hidden", False))
                and bool(state.get("portable", True))
                and scene_state.get_actor_location(actor)
                == scene_state.get_actor_location(recipient)
            )
            if not valid:
                action["outcome"] = "blocked"
                action["result"] = "物品归属或接收条件已经变化，交付没有发生。"
                traces.append(
                    {
                        "actor": actor,
                        "object_id": object_id,
                        "recipient": recipient,
                        "status": "host_delivery_unavailable",
                    }
                )
                continue
            action["action_kind"] = "interact"
            action["action_target"] = object_id
            retained.append(
                {
                    "operation": "relocate",
                    "object_id": object_id,
                    "actor": actor,
                    "owner": recipient,
                    "hidden": False,
                    "reason": "Agent 的单边物品交付已由语义层正向结算，Host 提交权威转移",
                }
            )
            traces.append(
                {
                    "actor": actor,
                    "object_id": object_id,
                    "recipient": recipient,
                    "status": "host_delivery_materialized",
                }
            )
        result["object_lifecycle"] = retained
        return ObjectDeliveryResolution(result=result, traces=tuple(traces))
