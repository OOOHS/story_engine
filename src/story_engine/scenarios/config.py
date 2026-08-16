from typing import List, Dict, Optional, Any, Literal  # noqa: F401 Optional used in property
from pydantic import BaseModel, Field


class StateCondition(BaseModel):
    """
    A structured lock against the authoritative world state.
    """
    scope: Literal["scene", "world_object", "actor", "plot"] = "scene"
    target: Optional[str] = None
    path: str
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte",
        "contains", "in", "exists", "not_exists",
    ] = "eq"
    value: Any = None


class PhysicsRuleConfig(BaseModel):
    """A content-declared, keyword-triggered capability gate.

    This lets a scenario state "these words imply a capability, and only
    actors with that capability may act on them" as data, instead of
    requiring a new Python function in LegalityEngine for every setting
    (e.g. a magic-world scenario allowing flight for actors tagged
    capabilities=["flight"], with no engine code change).
    """

    keywords: List[str] = Field(default_factory=list)
    capability: str = ""
    reason: str = ""


class ClaimConfig(BaseModel):
    """An objective proposition whose truth is owned by the host world."""

    claim_id: str
    statement: str
    initial_truth: Literal["true", "false", "unknown"] = "unknown"
    visibility: Literal["public", "secret"] = "secret"
    subjects: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    supporting_evidence: List[str] = Field(default_factory=list)
    refuting_evidence: List[str] = Field(default_factory=list)
    truth_conditions: List[StateCondition] = Field(default_factory=list)
    false_conditions: List[StateCondition] = Field(default_factory=list)


class AgreementOfferTemplateConfig(BaseModel):
    """Host-owned terms that a character may choose to propose."""

    template_id: str
    agreement_id: str
    proposer: str
    parties: List[str] = Field(min_length=2, max_length=3)
    title: str
    summary: str = ""
    expires_after_steps: int = Field(default=8, ge=1, le=20)
    transfers: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    escrows: List[Dict[str, Any]] = Field(default_factory=list)
    delegations: List[Dict[str, Any]] = Field(default_factory=list)


class StoryBeatConfig(BaseModel):
    """
    Structured beat guidance carried by a storylet when it becomes active.
    """
    beat_type: str = "generic"
    visibility: Literal["public", "local", "hidden"] = "public"
    preferred_actors: List[str] = Field(default_factory=list)
    preferred_template_ids: List[str] = Field(default_factory=list)
    required_flags: List[str] = Field(default_factory=list)
    target_actor: Optional[str] = None
    stake: str = ""


class StoryletConfig(BaseModel):
    """
    Atomic narrative trigger that only knows its locks and intent.
    """
    storylet_id: str
    intent: str
    conditions: List[StateCondition] = Field(default_factory=list)
    priority: int = 0
    one_shot: bool = False
    tags: List[str] = Field(default_factory=list)
    beat: Optional[StoryBeatConfig] = None
    situation_kinds: List[str] = Field(default_factory=list)
    situation_tags: List[str] = Field(default_factory=list)




class DramaConfig(BaseModel):
    """
    Global pacing targets used by the drama manager.
    """
    initial_tension: float = 0.4
    target_min: float = 0.4
    target_max: float = 0.75
    crisis_threshold: float = 0.25
    recovery_bias: float = 0.05


class ConflictConfig(BaseModel):
    """
    Global knobs for how aggressively the engine should surface visible conflict.
    """
    intensity: float = 0.5
    max_quiet_turns: int = 2
    early_pressure_window_end: int = 3
    antagonist_names: List[str] = Field(default_factory=list)
    preferred_modes: List[str] = Field(default_factory=list)
    surface_style: Literal["implicit", "barbed", "acid"] = "implicit"
    verbal_directness: float = 0.5
    repetition_window: int = 2


class ConflictTemplateConfig(BaseModel):
    """
    Reusable conflict beat template used when pacing needs a stronger push.
    """
    template_id: str
    instruction: str
    fallback_result: str = ""
    fallback_results: List[str] = Field(default_factory=list)
    phases: List[str] = Field(default_factory=list)
    preferred_actors: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    min_step: int = 0


class PlotStageConfig(BaseModel):
    """
    One visible/hidden phase of a long-running plot entity.
    """
    label: str
    summary: str
    pressure_hint: str = ""


class PlotEntityConfig(BaseModel):
    """
    A long-running macro conflict represented as a clocked entity.
    """
    plot_id: str
    title: str
    description: str
    clock: int = 0
    max_clock: int = 4
    current_stage: int = 0
    stages: List[PlotStageConfig] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class PlotRuleConfig(BaseModel):
    """A deterministic causal edge from authoritative state to a plot clock."""

    rule_id: str
    plot_id: str
    conditions: List[StateCondition] = Field(default_factory=list)
    advance: int = 0
    stage_shift: int = 0
    one_shot: bool = True
    priority: int = 0
    reason: str = ""


class NeedConfig(BaseModel):
    """One private pressure meter for a character agent."""

    name: str
    pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    drift_per_turn: float = Field(default=0.0, ge=-1.0, le=1.0)
    critical_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    description: str = ""


class TraitConfig(BaseModel):
    """Data-driven personality tendency for host-side action utility."""

    trait_id: str
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str = ""
    policy_weights: Dict[str, float] = Field(default_factory=dict)


class ObligationConfig(BaseModel):
    """A private, deadline-bearing responsibility owned by one character."""

    obligation_id: str
    title: str
    summary: str = ""
    creditor: Optional[str] = None
    due_step: int = Field(ge=0)
    grace_steps: int = Field(default=0, ge=0, le=100)
    wake_before_steps: int = Field(default=1, ge=0, le=100)
    pressure_need: Optional[str] = None
    due_pressure_delta: float = Field(default=0.1, ge=0.0, le=0.5)
    breach_pressure_delta: float = Field(default=0.2, ge=0.0, le=0.5)
    completion_conditions: List[StateCondition] = Field(default_factory=list)
    delegation_policy: Literal["forbidden", "bilateral", "creditor_consent"] = (
        "creditor_consent"
    )


class GoalConfig(BaseModel):
    """A character goal with optional host-verifiable resolution evidence."""

    goal_id: str
    title: str
    description: str = ""
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    completion_conditions: List[StateCondition] = Field(default_factory=list)
    failure_conditions: List[StateCondition] = Field(default_factory=list)


class ClaimKnowledgeConfig(BaseModel):
    """One character's initial private position on a seeded Claim Entity."""

    claim_id: str
    stance: Literal["supports", "rejects", "uncertain"] = "uncertain"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: Literal["reported", "observed", "inferred", "public"] = "reported"
    source: str = "scenario"
    evidence_refs: List[str] = Field(default_factory=list)


class CharacterConfig(BaseModel):
    """
    场景中的角色配置
    """
    name: str
    role: str
    personality: str
    goals: List[str]
    goal_specs: List[GoalConfig] = Field(default_factory=list)
    is_player: bool = False  # 是否为玩家控制的角色
    agent_runtime: str = "llm"
    agent_config: Dict[str, Any] = Field(default_factory=dict)
    activation_policy: Literal["auto", "foreground", "background", "dormant"] = "auto"
    background_interval: int = Field(default=3, ge=1)
    initial_beliefs: List[Dict[str, Any]] = Field(default_factory=list)
    initial_secrets: List[str] = Field(default_factory=list)
    initial_commitments: List[str] = Field(default_factory=list)
    initial_needs: List[NeedConfig] = Field(default_factory=list)
    initial_traits: List[TraitConfig] = Field(default_factory=list)
    risk_tolerance: float = Field(default=0.5, ge=0.0, le=1.0)
    initial_obligations: List[ObligationConfig] = Field(default_factory=list)
    initial_claim_knowledge: List[ClaimKnowledgeConfig] = Field(default_factory=list)
    initial_known_locations: List[str] = Field(default_factory=list)
    llm_config: Dict[str, Any] = Field(default_factory=dict)  # 可选的模型配置覆盖


class RelationshipDirectionConfig(BaseModel):
    """One participant's continuous tracks toward the other participant."""

    source: str
    target: str
    tracks: Dict[str, float] = Field(default_factory=dict)


class RelationshipBitConfig(BaseModel):
    bit_id: str
    roles: Dict[str, str] = Field(default_factory=dict)
    visibility: Literal["participants", "public", "hidden"] = "participants"


class RelationshipConfig(BaseModel):
    """Sims-style sparse pair relationship seed."""

    participants: List[str] = Field(min_length=2, max_length=2)
    directions: List[RelationshipDirectionConfig] = Field(default_factory=list)
    bits: List[RelationshipBitConfig] = Field(default_factory=list)


class NarrationConfig(BaseModel):
    """Optional presentation policy owned by content, not world mechanics."""

    guidance: List[str] = Field(default_factory=list)
    max_sentences: int = Field(default=6, ge=1, le=20)
    max_characters: int = Field(default=220, ge=40, le=4000)


class ScenarioConfig(BaseModel):
    """
    故事场景的静态配置
    """
    name: str
    description: str  # 高层描述
    environment: str  # 物理环境细节
    physics_profile: str = "mundane"
    # When non-empty, these fully replace LegalityEngine's built-in
    # "mundane" keyword table for this scenario's physics_profile: content
    # can declare a magic/wuxia/etc. world's capability gates as data, with
    # no LegalityEngine code change and no register_profile() call.
    physics_rules: List[PhysicsRuleConfig] = Field(default_factory=list)
    # How many new DriveState needs a single character may have created at
    # runtime (via drive_creations) over the whole episode. 0 (default)
    # preserves today's behavior: needs only come from initial_actor_states.
    emergent_meter_budget: int = Field(default=0, ge=0, le=50)
    rules: List[str] = Field(default_factory=list)  # 游戏规则或物理法则
    narration: NarrationConfig = Field(default_factory=NarrationConfig)
    initial_state: str  # 故事的初始状态
    initial_scene_flags: Dict[str, Any] = Field(default_factory=dict)
    public_scene_fields: List[str] = Field(default_factory=list)
    private_scene_fields: List[str] = Field(default_factory=list)
    initial_world_objects: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    initial_actor_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    initial_relationships: List[RelationshipConfig] = Field(default_factory=list)
    claims: List[ClaimConfig] = Field(default_factory=list)
    agreement_offer_templates: List[AgreementOfferTemplateConfig] = Field(
        default_factory=list
    )
    storylets: List[StoryletConfig] = Field(default_factory=list)
    drama: DramaConfig = Field(default_factory=DramaConfig)
    conflict: ConflictConfig = Field(default_factory=ConflictConfig)
    conflict_templates: List[ConflictTemplateConfig] = Field(default_factory=list)
    plot_entities: List[PlotEntityConfig] = Field(default_factory=list)
    plot_rules: List[PlotRuleConfig] = Field(default_factory=list)
    causal_plot_max_triggers_per_turn: int = Field(default=3, ge=1, le=20)
    causal_plot_max_total_advance: int = Field(default=3, ge=0, le=20)

    # 场景预定义角色
    characters: List[CharacterConfig] = Field(default_factory=list)

    metadata: Dict[str, str] = Field(default_factory=dict)

    @property
    def player_character_name(self) -> Optional[str]:
        """First character with is_player=True, or None."""
        for c in self.characters:
            if c.is_player:
                return c.name
        return None
