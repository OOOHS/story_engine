from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.story_engine.agents.actions import AgentAction


@dataclass(frozen=True)
class AgentActivation:
    """Why and at what fidelity a character agent runs this turn."""

    active: bool
    scope: str = "dormant"
    reason: str = ""


@dataclass(frozen=True)
class AgentPerception:
    """The knowledge made available to one character for one turn.

    It deliberately contains a POV-filtered world view rather than the full
    authoritative scene state. This is the main information boundary between
    the world simulation and autonomous characters.
    """

    actor_name: str
    step: int
    activation_scope: str = "foreground"
    world_view: Dict[str, Any] = field(default_factory=dict)
    self_state: Dict[str, Any] = field(default_factory=dict)
    private_cognition: Dict[str, Any] = field(default_factory=dict)
    private_drives: Dict[str, Any] = field(default_factory=dict)
    private_traits: Dict[str, Any] = field(default_factory=dict)
    private_sentiments: Dict[str, Any] = field(default_factory=dict)
    relationship_context: Dict[str, Any] = field(default_factory=dict)
    affordance_opportunities: List[Dict[str, Any]] = field(default_factory=list)
    private_obligations: Dict[str, Any] = field(default_factory=dict)
    private_schedule: Dict[str, Any] = field(default_factory=dict)
    private_goals: Dict[str, Any] = field(default_factory=dict)
    private_modifiers: Dict[str, Any] = field(default_factory=dict)
    private_knowledge: Dict[str, Any] = field(default_factory=dict)
    private_navigation: Dict[str, Any] = field(default_factory=dict)
    private_agreements: Dict[str, Any] = field(default_factory=dict)
    agreement_opportunities: List[Dict[str, Any]] = field(default_factory=list)
    recent_observations: List[str] = field(default_factory=list)
    passive_observations: List[Dict[str, Any]] = field(default_factory=list)
    active_observation_results: List[Dict[str, Any]] = field(default_factory=list)
    ongoing_actions: List[Dict[str, Any]] = field(default_factory=list)
    relevant_memories: List[str] = field(default_factory=list)
    current_plan: str = ""
    visible_proposals: List[Dict[str, Any]] = field(default_factory=list)
    world_signals: List[Dict[str, Any]] = field(default_factory=list)
    # Soft, non-authoritative suggestions queued by the Host (e.g. from an
    # unrealized plot thread). Never a proposal, never validated against
    # proposal_actors -- purely advisory inbox content the character may
    # act on, reinterpret, or ignore.
    director_signals: List[Dict[str, Any]] = field(default_factory=list)

    def manual_decision_context(self) -> Dict[str, Any]:
        """Bounded player/UI projection of the same packet an Agent receives."""

        visible_world = self.world_view.get("visible_world", {})
        visible_objects = (
            sorted(str(item) for item in visible_world)
            if isinstance(visible_world, dict)
            else []
        )
        return {
            "actor": self.actor_name,
            "step": int(self.step),
            "location": self.world_view.get("location")
            or self.self_state.get("location"),
            "self_state": dict(self.self_state),
            "visible_actors": list(self.world_view.get("visible_actors", []) or []),
            "visible_objects": visible_objects,
            "pending_world_events": list(
                self.private_cognition.get("pending_world_events", []) or []
            ),
            "pending_event_responses": list(
                self.private_cognition.get("pending_event_responses", []) or []
            ),
            "current_focus": self.private_cognition.get("current_focus", ""),
            "passive_observations": [
                {
                    key: item.get(key)
                    for key in (
                        "actor",
                        "result",
                        "action_kind",
                        "action_target",
                        "event_id",
                        "response_id",
                        "response_kind",
                        "location",
                        "observed_step",
                        "age_steps",
                    )
                    if item.get(key) not in (None, "")
                }
                for item in self.passive_observations[-8:]
                if isinstance(item, dict)
            ],
            "active_observation_results": [
                {
                    key: item.get(key)
                    for key in (
                        "result",
                        "private_result",
                        "action_target",
                        "location",
                    )
                    if item.get(key) not in (None, "")
                }
                for item in self.active_observation_results[-8:]
                if isinstance(item, dict)
            ],
            "active_goals": list(self.private_goals.get("active", []) or []),
            "navigation_problems": list(
                self.private_navigation.get("active", []) or []
            )[:6],
            "ongoing_actions": list(self.ongoing_actions),
        }


@dataclass(frozen=True)
class AgentDecision:
    """A character proposal. It is not an authoritative world outcome.

    A runtime submits exactly one action. It deliberates internally and the
    Host does not re-rank the result, so there is no candidate distribution
    on this boundary -- whatever weighing happened stays inside the agent.
    """

    action: str
    thought: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    action_spec: AgentAction | None = None

    def normalized_action(self) -> AgentAction:
        return self.action_spec or AgentAction.from_value(self.action)
