"""Host receipt for an action the character runtime already committed to.

There is deliberately no scoring here. A character's deliberation belongs to
that character's own agent: it weighs its persona, memory, feelings and
situation internally and submits one action. The Host's job at this boundary
is to *record* that choice and keep an auditable receipt of it -- never to
re-rank it, re-sample it, or reconstruct a utility function for why it was
made. Legality, duration, resource contests, uncertainty and authoritative
settlement all remain Host-owned, but they act on the committed action; they
do not replace it.

The receipt records only the committed action. Candidate identifiers and
candidate arrays are deliberately absent because this boundary no longer
supports Host-visible alternatives.
"""

from dataclasses import dataclass
from typing import Any, Dict

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision


@dataclass(frozen=True)
class RuntimeCommitment:
    """The action a runtime committed to, plus the Host's audit receipt."""

    action: AgentAction
    trace: Dict[str, Any]


def commit_runtime_action(decision: AgentDecision) -> RuntimeCommitment:
    action = decision.normalized_action()
    return RuntimeCommitment(
        action=action,
        trace={
            "mode": "runtime_committed",
            "committed_action": action.to_dict(),
        },
    )


def repetition_signature(action: Any) -> str:
    """Stable identity of a plan, for the repeated-choice audit only."""
    action = AgentAction.from_value(action)
    structured = repr(_structured_action_signature(action))
    return "\x1f".join((action.kind, structured))[:1000]


def repetition_target(action: Any) -> str:
    action = AgentAction.from_value(action)
    return " ".join(action.target.casefold().split())


def _structured_action_signature(action: AgentAction) -> tuple[Any, ...]:
    return (
        action.affordance_id.casefold(),
        action.claim_id.casefold(),
        action.claim_stance.casefold(),
        tuple(item.casefold() for item in action.evidence_refs),
        action.delivery_recipient.casefold(),
        action.route_source.casefold(),
        action.route_target.casefold(),
        tuple(item.casefold() for item in action.route_path),
    )
