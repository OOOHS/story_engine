"""Pending post-commit delivery work for Rendering/Memory recovery."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict


_REFERENCE_KEYS = {
    "dispatcher",
    "clock",
    "agent_registry",
    "action_queue",
    "relation_registry",
    "random_streams",
    "check_resolver",
    "register_agent",
    "unregister_agent",
    "claim_registry",
}


@dataclass
class DeliveryReceipt:
    start_index: int
    context: Dict[str, Any]
    attempts: int = 0

    @classmethod
    def capture(
        cls,
        *,
        start_index: int,
        context: Dict[str, Any],
        attempts: int = 0,
    ) -> "DeliveryReceipt":
        return cls(
            start_index=int(start_index),
            context=clone_delivery_context(context),
            attempts=int(attempts),
        )


def clone_delivery_context(context: Dict[str, Any]) -> Dict[str, Any]:
    cloned: Dict[str, Any] = {}
    for key, value in context.items():
        if key in _REFERENCE_KEYS:
            cloned[key] = value
            continue
        try:
            cloned[key] = deepcopy(value)
        except Exception:
            cloned[key] = value
    return cloned
