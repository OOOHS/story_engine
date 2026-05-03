from typing import List, Dict, Optional, Any, Literal  # noqa: F401 Optional used in property
from pydantic import BaseModel, Field


class StateCondition(BaseModel):
    """
    A structured lock against the authoritative world state.
    """
    scope: Literal["scene", "world_object", "actor", "plot"] = "scene"
    target: Optional[str] = None
    path: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "exists"] = "eq"
    value: Any = None


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
    force_visible_conflict_before_step: int = 3
    minimum_level_when_forced: Literal["low", "medium", "high"] = "medium"
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


class CharacterConfig(BaseModel):
    """
    场景中的角色配置
    """
    name: str
    role: str
    personality: str
    goals: List[str]
    is_player: bool = False  # 是否为玩家控制的角色
    llm_config: Dict[str, Any] = Field(default_factory=dict)  # 可选的模型配置覆盖


class ScenarioConfig(BaseModel):
    """
    故事场景的静态配置
    """
    name: str
    description: str  # 高层描述
    environment: str  # 物理环境细节
    physics_profile: Literal["mundane", "supernatural", "freeform"] = "mundane"
    rules: List[str] = Field(default_factory=list)  # 游戏规则或物理法则
    initial_state: str  # 故事的初始状态
    initial_scene_flags: Dict[str, Any] = Field(default_factory=dict)
    initial_world_objects: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    initial_actor_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    storylets: List[StoryletConfig] = Field(default_factory=list)
    drama: DramaConfig = Field(default_factory=DramaConfig)
    conflict: ConflictConfig = Field(default_factory=ConflictConfig)
    conflict_templates: List[ConflictTemplateConfig] = Field(default_factory=list)
    plot_entities: List[PlotEntityConfig] = Field(default_factory=list)

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
