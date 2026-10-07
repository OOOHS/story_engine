"""Persistent player-facing story planner agent with a proposal-only tool boundary."""

import json
from copy import deepcopy
from time import time
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field, PrivateAttr

from src.story_engine.core.component import Component
from src.story_engine.environment.narrative_candidates import STORY_PROPOSAL_CAP, STORY_PENDING_STATUSES
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig, StoryletConfig


class StoryPlanner(Component):
    """Own a continuing conversation grounded in the player's narrated story.

    The only world-facing tool submits an unreviewed storylet proposal.
    Host structural registration admits valid definitions to the future storylet pool.
    Planner conversation/log state participates in Runner checkpoints.
    """

    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    interval_turns: int = Field(default=5, ge=1)
    interval_seconds: Optional[float] = Field(default=None, gt=0)
    narrative_messages: List[Dict[str, Any]] = Field(default_factory=list)
    conversation: List[Dict[str, Any]] = Field(default_factory=list)
    narrated_turn_ids: List[str] = Field(default_factory=list)
    fed_message_count: int = 0
    last_inquiry_turn: int = 0
    last_inquiry_time: float = 0.0
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    MAX_CANDIDATES_PER_TICK: ClassVar[int] = 2
    MAX_TOOL_ROUNDS: ClassVar[int] = 3
    REVIEW_STATUS: ClassVar[str] = "structural"

    def __init__(self, **data):
        super().__init__(**data)
        self._llm = LLMProvider(**(self.llm_config or data.get("model_config", {})))

    def record_opening(self, text: str) -> None:
        if text and not self.narrative_messages:
            self.narrative_messages.append({"role": "world", "content": text})

    def record_turn(self, turn_id: str, player_message: str, world_message: str) -> bool:
        if turn_id in self.narrated_turn_ids:
            return False
        self.narrated_turn_ids.append(turn_id)
        if self.last_inquiry_time == 0:
            self.last_inquiry_time = time()
        if player_message:
            self.narrative_messages.append({"role": "player", "content": player_message})
        self.narrative_messages.append({"role": "world", "content": world_message})
        return True

    def inquiry_due(self, now: Optional[float] = None) -> bool:
        turns = len(self.narrated_turn_ids)
        if turns <= self.last_inquiry_turn:
            return False
        if turns - self.last_inquiry_turn >= self.interval_turns:
            return True
        timestamp = time() if now is None else now
        return bool(
            self.interval_seconds is not None
            and self.last_inquiry_time > 0
            and timestamp - self.last_inquiry_time >= self.interval_seconds
        )

    @staticmethod
    def proposal_tool() -> Dict[str, Any]:
        schema = StoryletConfig.model_json_schema()
        definitions = schema.pop("$defs", {})
        parameters = {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "storylet": schema},
            "required": ["reason", "storylet"],
            "additionalProperties": False,
        }
        if definitions:
            parameters["$defs"] = definitions
        return {
            "type": "function",
            "function": {
                "name": "propose_storylet",
                "description": "提出未来故事块：触发条件、剧情方向和预期影响。结构检查后登记；具体生效由世界结算解释。",
                "parameters": parameters,
            },
        }

    def propose(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the same planner conversation whether another story block helps."""
        self.last_inquiry_turn = len(self.narrated_turn_ids)
        self.last_inquiry_time = time()
        history = deepcopy(self.conversation)
        if not history:
            history.append({"role": "system", "content": (
                "你是服务于玩家体验的持续导演 Agent。玩家输入与玩家实际收到的世界消息构成叙事上下文。"
                "依据这段叙事和作者设定判断是否值得增加新的故事块；需要时调用 propose_storylet，"
                "每次询问最多两项。Storylet 是内容、前置条件和执行后世界影响组成的可组合剧情单元。"
                "提出面向未来的剧情方向，可包含多人互动、宏大事件或持续局面。用 trigger 自然语言描述启动条件，在 intent 中说明期望发展与影响。conditions 可选，用于作者明确的状态约束。"
                "scene 条件的 path 从完整 SceneState 快照开始，例如 scene_flags.weather；actor/world_object 条件从 target 实体属性开始。世界结算模型解释 trigger 并落实当前可发生的变化，提交后向角色交付可感知事实。"
                "累计叙事帮助理解局势，产物应提供未来的戏剧机会。可以提出具体场景或参数化情境。"
                "角色言论保持说话人的来源，提及的人物、物品、地点、关系及真实性可以继续待定。"
                "新实体在触发事件的外部结果确实依赖它时，由结算模型按需补全。"
                "可以描述期望角色参与的剧情；执行时需要自主选择的部分转为导演建议，由角色决定回应。工具提交草案供宿主结构登记。"
                "登记创建未来剧情方向；实际影响经过世界结算与提交校对。"
                "已有故事块目录供保持一致性和避免重复。没有合适提案时可以直接结束本次询问。\n"
                "作者私有设定：\n" + (self.scenario.private_author_premise if self.scenario else "")
            )})
        history.append({"role": "user", "content": json.dumps({
            "new_player_narrative_messages": self.narrative_messages[self.fed_message_count:],
            "existing_storylets": input_payload.get("existing_storylets", []),
            "pending_proposals": input_payload.get("pending_proposals", []),
            "storylet_tracking": input_payload.get("storylet_tracking", {}),
            "request": "基于累计玩家叙事，判断是否值得提出带条件、面向未来的新情境或事件。",
        }, ensure_ascii=False)})
        proposals = []
        try:
            for _ in range(self.MAX_TOOL_ROUNDS):
                response = self._llm.generate_messages(history, tools=[self.proposal_tool()])
                content = response.get("content") or ""
                if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
                    return {"narrative_candidates": [], "story_planner_error": "model_unavailable"}
                calls = response.get("tool_calls", []) or []
                if not calls and not content.strip():
                    return {"narrative_candidates": [], "story_planner_error": "invalid_response"}
                reply = {"role": "assistant", "content": content}
                if calls:
                    reply["tool_calls"] = calls
                history.append(reply)
                if not calls:
                    self.conversation = history
                    self.fed_message_count = len(self.narrative_messages)
                    return {"narrative_candidates": proposals}
                for call in calls:
                    feedback: Dict[str, Any]
                    function = call.get("function", {})
                    if function.get("name") != "propose_storylet":
                        feedback = {"error": "unknown_tool"}
                    elif sum(p.get("status") in STORY_PENDING_STATUSES for p in input_payload.get("pending_proposals", [])) + len(proposals) >= STORY_PROPOSAL_CAP:
                        feedback = {"error": "pending_proposal_cap"}
                    elif len(proposals) >= self.MAX_CANDIDATES_PER_TICK:
                        feedback = {"error": "proposal_budget_exhausted"}
                    else:
                        try:
                            args = json.loads(function.get("arguments", ""))
                            storylet = StoryletConfig.model_validate(args["storylet"])
                            if not storylet.storylet_id.strip() or not storylet.intent.strip():
                                raise ValueError("storylet identity and intent must be nonempty")
                            proposal = {
                                "kind": "storylet_definition",
                                "reason": str(args.get("reason", ""))[:300],
                                "payload": storylet.model_dump(),
                            }
                            proposals.append(proposal)
                            feedback = {"status": "pending_registration", "review_status": self.REVIEW_STATUS}
                        except (ValueError, TypeError, KeyError):
                            feedback = {"error": "invalid_storylet_proposal"}
                    history.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": json.dumps(feedback, ensure_ascii=False),
                    })
            self.conversation = history
            self.fed_message_count = len(self.narrative_messages)
            return {"narrative_candidates": proposals, "story_planner_note": "tool_round_limit"}
        except Exception as exc:
            return {"narrative_candidates": [], "story_planner_error": type(exc).__name__}
