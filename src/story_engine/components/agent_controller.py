from typing import Any, Dict, Literal

from pydantic import Field, field_validator

from src.story_engine.core.component import Component

# Legacy scenario/save data may still spell out the older four-way policy.
# "auto" and "background" were always behaviorally identical (every branch in
# AgentScheduler grouped them together), and "dormant" duplicates the already
# -independent ``autonomous`` switch below. Both collapse into "background" on
# load instead of failing validation.
_LEGACY_ACTIVATION_POLICY_ALIASES = {"auto": "background", "dormant": "background"}


class AgentController(Component):
    """Declares that an entity is controlled by a registered character agent.

    The component contains durable, serialisable configuration only. The live
    runtime object is owned by ``AgentRegistry`` so ECS state never depends on
    a particular agent framework (Hermes, a direct LLM client, or a human UI).
    """

    # No default: every AgentController must state which runtime backs it.
    runtime: str
    # Whether this character is ever allowed to act autonomously at all. This
    # is the sole "fully off" switch; activation_policy only ever decides
    # *how* an autonomous character is scheduled, not *whether* it may act.
    autonomous: bool = True
    activation_policy: Literal["foreground", "background"] = "background"

    @field_validator("activation_policy", mode="before")
    @classmethod
    def _normalize_legacy_activation_policy(cls, value: Any) -> Any:
        return _LEGACY_ACTIVATION_POLICY_ALIASES.get(str(value), value)
    background_interval: int = Field(default=3, ge=1)
    decision_count: int = Field(default=0, ge=0)
    last_decision_step: int = -1
    last_goal_wakeup_step: int = -1
    last_goal_wakeup_id: str = ""
    goal_continuation_attempts: int = Field(default=0, ge=0)
    goal_reactivation_count: int = Field(default=0, ge=0)
    repeated_goal_action_count: int = Field(default=0, ge=0)
    last_goal_action_signature: str = ""
    repeated_policy_action_count: int = Field(default=0, ge=0)
    max_repeated_policy_action_count: int = Field(default=0, ge=0)
    last_policy_action_signature: str = ""
    last_policy_action_target: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)

    def record_decision(self, step: int) -> None:
        self.decision_count += 1
        self.last_decision_step = int(step)

    def record_policy_action(self, signature: str, target: str = "") -> None:
        normalized = str(signature or "").strip()
        if not normalized:
            return
        normalized_target = " ".join(str(target or "").casefold().split())
        same_family = self.last_policy_action_signature == normalized
        same_attempt = same_family and (
            not self.last_policy_action_target
            or not normalized_target
            or self.last_policy_action_target == normalized_target
        )
        self.repeated_policy_action_count = (
            self.repeated_policy_action_count + 1
            if same_attempt
            else 1
        )
        self.max_repeated_policy_action_count = max(
            self.max_repeated_policy_action_count,
            self.repeated_policy_action_count,
        )
        self.last_policy_action_signature = normalized
        self.last_policy_action_target = (
            normalized_target
            or (self.last_policy_action_target if same_family else "")
        )
