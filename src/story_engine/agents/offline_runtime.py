"""Explicit deterministic runtime used by the offline smoke profile.

This is not a production fallback for Hermes.  Applications must opt into it
by assigning ``agent_runtime="offline"`` (the CLI's ``--profile offline`` does
that for a copied seed).  Its purpose is to make the structural world loop
testable on a machine with no model credentials or Docker daemon.
"""

from __future__ import annotations

from typing import Any

from .types import AgentDecision


class OfflineCharacterRuntime:
    """A small, honest subject that waits unless a visible affordance exists."""

    def decide(self, entity: Any, perception: Any) -> AgentDecision:
        opportunities = getattr(perception, "affordance_opportunities", []) or []
        if opportunities:
            first = opportunities[0] if isinstance(opportunities[0], dict) else {}
            label = str(first.get("label") or first.get("description") or "").strip()
            target = str(first.get("object_id") or first.get("target") or "").strip()
            if label and target:
                return AgentDecision(action=f"尝试{label}（目标：{target}）。")
        return AgentDecision(action="暂时等待并观察当前局面。")

    def close(self) -> None:
        return None


def default_offline_runtime_factories() -> dict[str, Any]:
    """Return the explicit ``offline`` runtime registration."""

    return {"offline": lambda entity, config: OfflineCharacterRuntime()}


__all__ = ["OfflineCharacterRuntime", "default_offline_runtime_factories"]
