"""Host-owned lifecycle for independent, continuing storylet agent sessions."""
from copy import deepcopy
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig, StoryletConfig


class StoryletTracker(BaseModel):
    definition: StoryletConfig
    status: Literal["waiting", "active", "completed", "abandoned"] = "waiting"
    progress: str = "尚待世界结算启动条件与初始剧情方向。"
    conversation: List[Dict[str, Any]] = Field(default_factory=list)
    fed_message_count: int = 0
    last_attempt_turn_id: str = ""
    # Exactly one prospective advance; consumed only by committed settlement.
    pending_advance: Optional[str] = None
    last_execution: Dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""

    @property
    def closed(self) -> bool:
        return self.status in {"completed", "abandoned"}


class TrackingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["waiting", "active", "completed", "abandoned"]
    progress: str = Field(min_length=1)
    advance: Optional[str] = None


class StoryTracking(Component):
    """One transport, independent agent histories, one shared narrative source.

    StoryPlanner holds the common player-narrative ledger. Each tracker owns its
    own conversation, cursor and pending advance. All fields are ordinary saved
    component data; existing Runner checkpoints restore the whole lifecycle.
    """
    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    agents: Dict[str, StoryletTracker] = Field(default_factory=dict)
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        self._llm = LLMProvider(**(self.llm_config or data.get("model_config", {})))

    def register(self, definition: Any, scene: Any = None) -> StoryletTracker:
        definition = StoryletConfig.model_validate(definition)
        existing = self.agents.get(definition.storylet_id)
        if existing is not None:
            return existing
        consumed = scene.get_scene_flag("consumed_storylets", []) if scene else []
        tracker = StoryletTracker(definition=definition,
            pending_advance=definition.intent,
            status="completed" if definition.storylet_id in consumed else "waiting")
        if tracker.closed:
            tracker.pending_advance = None
            tracker.progress = "存档记录该故事块已结束。"
        self.agents[definition.storylet_id] = tracker
        return tracker

    def reconcile(self, scene: Any) -> None:
        for definition in list(self.scenario.storylets if self.scenario else []) + list(
                scene.get_scene_flag("dynamic_storylets", []) or []):
            self.register(definition, scene)

    def track(self, narrative: List[Dict[str, Any]], turn_id: str, scene: Any) -> Dict[str, Any]:
        outcomes = {}
        for storylet_id, tracker in self.agents.items():
            if tracker.closed or tracker.last_attempt_turn_id == turn_id:
                continue
            tracker.last_attempt_turn_id = turn_id
            history = deepcopy(tracker.conversation)
            if not history:
                history.append({"role": "system", "content": (
                    "你是导演委派的独立故事块追踪 Agent，专注自己的剧情方向。"
                    "共同玩家叙事中的消息保留玩家/世界来源。读取整个故事的新增部分，"
                    "结合自己的执行回执追踪进展，判断是否需要继续、等待、完成或放弃。"
                    "每次只提出当前适合的一步 advance；它将由世界模型结算，具体角色行动由角色决定。"
                    "建议尚未发生、角色尚未接受、世界尚未提交的变化保持未来语气。"
                    "完成以已交付事实和成功执行回执为依据；角色可以改变剧情方向。"
                    "确认目标已失去意义或实现可能时可 abandoned。等待条件或角色回应时保持 waiting/active。"
                    "终止时 advance 为 null；其他时候可用 null 保持等待。"
                    "输出 JSON：status(waiting/active/completed/abandoned)、progress(简短进展与判断依据)、"
                    "advance(自然语言的下一步剧情建议或null)。只输出该对象。\n"
                    "作者设定：" + (self.scenario.private_author_premise if self.scenario else "")
                )})
            history.append({"role": "user", "content": json.dumps({
                "storylet": tracker.definition.model_dump(),
                "progress": tracker.progress, "status": tracker.status,
                "new_player_narrative_messages": narrative[tracker.fed_message_count:],
                "last_execution": tracker.last_execution,
                "pending_advance": tracker.pending_advance,
            }, ensure_ascii=False)})
            try:
                response = self._llm.generate_messages(history)
                decision = TrackingDecision.model_validate_json(response.get("content", ""))
                if not decision.progress.strip() or (decision.advance is not None and not decision.advance.strip()):
                    raise ValueError("tracker progress and advance must be nonempty")
                if decision.status in {"completed", "abandoned"} and decision.advance is not None:
                    raise ValueError("closed tracker cannot enqueue an advance")
                history.append({"role": "assistant", "content": response["content"]})
                tracker.conversation = history
                tracker.fed_message_count = len(narrative)
                tracker.status = decision.status
                tracker.progress = decision.progress
                tracker.pending_advance = decision.advance
                tracker.last_error = ""
                if tracker.closed:
                    self._close_in_scene(tracker, scene)
                outcomes[storylet_id] = {"status": tracker.status}
            except Exception as exc:
                # Never acknowledge unread story or publish a malformed decision.
                tracker.last_error = type(exc).__name__
                outcomes[storylet_id] = {"status": "retry_pending", "error_type": tracker.last_error}
        return outcomes

    @staticmethod
    def _close_in_scene(tracker, scene):
        storylet_id = tracker.definition.storylet_id
        active = list(scene.get_scene_flag("active_storylet_triggers", []) or [])
        scene.update_scene_flags({"active_storylet_triggers": [sid for sid in active if sid != storylet_id]})
        if tracker.definition.one_shot and tracker.status == "completed":
            consumed = list(scene.get_scene_flag("consumed_storylets", []) or [])
            if storylet_id not in consumed:
                scene.update_scene_flags({"consumed_storylets": consumed + [storylet_id]})

    def commit_execution(self, triggers, result, step: int) -> None:
        actions = result.get("resolved_actions", []) or []
        for trigger in triggers:
            tracker = self.agents.get(trigger["source_storylet_id"])
            if tracker is None:
                continue
            action = next(a for a in actions if a.get("actor") == "World"
                          and a.get("source_storylet_id") == trigger["source_storylet_id"])
            tracker.last_execution = {"step": step, **deepcopy(action)}
            tracker.pending_advance = None
