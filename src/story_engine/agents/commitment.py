"""Host receipt for an action the character runtime already committed to.

There is deliberately no scoring here. A character's deliberation belongs to
that character's own agent: it weighs its persona, memory, feelings and
situation internally and submits one action. The Host's job at this boundary
is to *record* that choice and keep an auditable receipt of it -- never to
re-rank it, re-sample it, or reconstruct a utility function for why it was
made. Legality, duration, resource contests, uncertainty and authoritative
settlement all remain Host-owned, but they act on the committed action; they
do not replace it.

The receipt keeps the same shape the audit layer already expects for
runtime-committed decisions (``mode="runtime_committed"``), so evaluation
code that only explains Host-sampled choices degrades to "nothing to
explain" rather than special-casing.
"""

from dataclasses import dataclass
from typing import Any, Dict, Set

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.common.action_features import infer_social_response_kinds


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
            "selected_candidate_id": "runtime:0",
            "selected_action": action.to_dict(),
            "candidates": [
                {
                    "candidate_id": "runtime:0",
                    "source": "runtime",
                    "action": action.to_dict(),
                }
            ],
        },
    )


# Keyword families exist only so the stagnation audit can tell "the same plan
# again, reworded" from "a genuinely different plan of the same kind". They
# never influence which action runs.
_TAG_PATTERNS = {
    "risk": ("冒险", "冲", "闯", "强行", "攻击", "威胁", "危险", "risk", "attack"),
    "confront": ("质问", "对峙", "反驳", "拒绝", "挑战", "威胁", "施压", "指责", "控告", "confront", "accuse"),
    "retreat": ("撤退", "逃", "离开", "退开", "避开", "withdraw", "flee"),
    "aid": ("帮助", "保护", "救", "照顾", "支援", "help", "protect", "rescue"),
    "information": ("观察", "检查", "搜索", "询问", "打听", "试探", "偷听", "observe", "ask"),
    "deception": ("欺骗", "撒谎", "假装", "隐瞒", "误导", "deceive", "lie"),
    "rest": ("等待", "休息", "停留", "冷静", "wait", "rest"),
}


def repetition_signature(action: Any) -> str:
    """Stable identity of a plan, for the repeated-choice audit only."""
    action = AgentAction.from_value(action)
    tags = ",".join(sorted(_infer_tags(action)))
    structured = repr(_structured_action_signature(action))
    return "\x1f".join((action.kind, tags, structured))[:1000]


def repetition_target(action: Any) -> str:
    action = AgentAction.from_value(action)
    return " ".join(action.target.casefold().split())


def _infer_tags(action: AgentAction) -> Set[str]:
    tags = {action.kind}
    text = f"{action.detail} {action.target}".casefold()
    for tag, patterns in _TAG_PATTERNS.items():
        if any(pattern.casefold() in text for pattern in patterns):
            tags.add(tag)
    if action.kind == "communicate":
        tags.update(infer_social_response_kinds(text))
        tags.add("social")
    elif action.kind == "observe":
        tags.update({"information", "cautious"})
    elif action.kind == "wait":
        tags.update({"rest", "patient", "cautious"})
    return tags


def _structured_action_signature(action: AgentAction) -> tuple[Any, ...]:
    return (
        action.affordance_id.casefold(),
        action.claim_id.casefold(),
        action.claim_stance.casefold(),
        tuple(item.casefold() for item in action.evidence_refs),
        action.delivery_recipient.casefold(),
        action.agreement_operation.casefold(),
        action.agreement_id.casefold(),
        action.agreement_template_id.casefold(),
        tuple(item.casefold() for item in action.agreement_give_refs),
        tuple(item.casefold() for item in action.agreement_request_refs),
        action.agreement_service_object.casefold(),
        action.agreement_service_destination.casefold(),
        action.agreement_payment_ref.casefold(),
        action.agreement_deadline.casefold(),
        action.route_source.casefold(),
        action.route_target.casefold(),
        tuple(item.casefold() for item in action.route_path),
    )
