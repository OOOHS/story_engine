from dataclasses import dataclass
from typing import Any, Dict, Iterable

from src.story_engine.environment.physical_affordances import (
    PhysicalAffordanceEngine,
)


@dataclass(frozen=True)
class AffordanceActionResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, str], ...] = ()


class AffordanceActionResolver:
    """Materialize validated Agent affordance references as Host operations."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def __init__(self) -> None:
        self.physics = PhysicalAffordanceEngine()

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        intents: Iterable[Dict[str, Any]],
        scene_state: Any = None,
    ) -> AffordanceActionResolution:
        references = {
            str(item.get("actor", "")).strip(): (
                str(item.get("action_target", "")).strip(),
                str(item.get("action_affordance_id", "")).strip(),
            )
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("action_kind", "")).strip() == "interact"
            and str(item.get("action_target", "")).strip()
            and str(item.get("action_affordance_id", "")).strip()
        }
        if not references:
            return AffordanceActionResolution(result=result)

        positive = {
            str(action.get("actor", "")).strip()
            for action in result.get("resolved_actions", []) or []
            if isinstance(action, dict)
            and str(action.get("actor", "")).strip() in references
            and str(action.get("outcome", "")).strip() in self.POSITIVE_OUTCOMES
        }
        operations = result.get("object_lifecycle", [])
        if not isinstance(operations, list):
            operations = []
        retained = []
        materialized = set()
        traces = []
        unavailable = set()
        for actor in sorted(positive):
            object_id, affordance_id = references[actor]
            if not self.physics.is_builtin_id(affordance_id):
                continue
            if self.physics.is_available(
                scene_state, actor, object_id, affordance_id
            ):
                continue
            unavailable.add(actor)
            traces.append(
                {
                    "actor": actor,
                    "object_id": object_id,
                    "affordance_id": affordance_id,
                    "status": "host_affordance_unavailable",
                }
            )
            for action in result.get("resolved_actions", []) or []:
                if not isinstance(action, dict):
                    continue
                if str(action.get("actor", "")).strip() != actor:
                    continue
                action["outcome"] = "blocked"
                action["result"] = "对象状态已经变化，这项物理操作不再可用。"
        positive.difference_update(unavailable)
        for operation in operations:
            if not isinstance(operation, dict):
                retained.append(operation)
                continue
            actor = str(operation.get("actor", "")).strip()
            reference = references.get(actor)
            builtin_reference = bool(
                reference and self.physics.is_builtin_id(reference[1])
            )
            is_referenced_use = (
                reference is not None
                and str(operation.get("operation", "")).strip() == "use"
                and str(operation.get("object_id", "")).strip() == reference[0]
            )
            is_referenced_builtin_operation = (
                builtin_reference
                and str(operation.get("object_id", "")).strip() == reference[0]
                and str(operation.get("operation", "")).strip()
                in {"use", "relocate", "set_container_state"}
            )
            if is_referenced_builtin_operation:
                continue
            if not is_referenced_use:
                retained.append(operation)
                continue
            if (
                actor not in positive
                or str(operation.get("affordance_id", "")).strip() != reference[1]
            ):
                traces.append(
                    {
                        "actor": actor,
                        "object_id": reference[0],
                        "affordance_id": reference[1],
                        "status": "semantic_use_rejected",
                    }
                )
                continue
            materialized.add(actor)
            operation = dict(operation)
            operation["affordance_id"] = reference[1]
            retained.append(operation)

        for actor in sorted(positive):
            if actor in materialized:
                continue
            object_id, affordance_id = references[actor]
            if self.physics.is_builtin_id(affordance_id):
                operation = self.physics.build_operation(
                    scene_state, actor, object_id, affordance_id
                )
            else:
                operation = {
                    "operation": "use",
                    "object_id": object_id,
                    "affordance_id": affordance_id,
                    "actor": actor,
                    "reason": "Agent 选择了当前可用的对象能力，Host 据此结算使用操作",
                }
            if operation is None:
                continue
            retained.append(operation)
            traces.append(
                {
                    "actor": actor,
                    "object_id": object_id,
                    "affordance_id": affordance_id,
                    "status": (
                        "host_physical_operation_materialized"
                        if self.physics.is_builtin_id(affordance_id)
                        else "host_use_materialized"
                    ),
                }
            )
        result["object_lifecycle"] = retained
        return AffordanceActionResolution(result=result, traces=tuple(traces))
