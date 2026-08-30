import json
import re
from typing import Any, Dict, List, Optional
from pydantic import PrivateAttr, Field
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig


class NarrativeDirector(Component):
    """Optional post-settlement soft-signal generator.

    SimulationControl decides what happened this tick: it must run
    synchronously, exactly once, before the world commits, and a failure
    there has to halt the tick (fail-closed) because an authoritative fact
    cannot be half-decided. That is a settlement concern and has nothing to
    do with narrative judgment.

    It runs strictly after a successful world commit and may only emit
    non-binding ``director_signals``. It never creates macro-story threads,
    beats, clocks, or world facts; Storylets are authored opportunities evaluated
    directly from current world state.

    Because nothing here is load-bearing, a failure or an unavailable LLM
    is not fatal: the tick that already committed stays committed, and this
    pass is simply skipped for that tick (``fallback_mode="skip"``). This is
    the one place in the runtime allowed to be stateless-but-optional in a
    way SimulationControl is not.
    """

    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def direct(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
你现在处于故事引擎的【叙事导演】阶段，运行在这一轮结算已经成功提交之后。你看到的是已经成立的事实，
不是待批准的草案；你的输出不会改写任何已发生的事，也不构成对任何角色的强制指令。

## 你唯一能做的一件事
1. `director_signals`：给某个在场角色投递一句软性提示。宿主会把它放进该角色的收件箱，
   角色可以采纳、重新解读或直接无视。只有当某个【当前叙事机会】需要具体角色自己做出行动/表态的决定时才给；
   纯环境性的机会已经由结算阶段直接处理成事实，不需要你再提示。每个角色最多一条，全局每轮最多 3 条。

## 本轮刚刚成立的事实（只读，不能改写）
{json.dumps(input_payload.get("committed_facts", {}), ensure_ascii=False, indent=2)}

## 叙事压力（Drama 节奏，不是强制事件）
{json.dumps(input_payload.get("narrative_pressure", {}), ensure_ascii=False, indent=2)}

## 当前叙事机会（storylet，本轮已经兑现的那些不需要你再处理）
{json.dumps(input_payload.get("storylet_opportunities", []), ensure_ascii=False, indent=2)}

## 输出格式
只输出 JSON，不要输出解释，不要使用 Markdown：
{{
  "director_signals": [
    {{
      "actor": "接收提示的在场角色（不会被强制执行）",
      "suggestion": "一句自然语言提示，说明有什么值得这个角色注意",
      "source_storylet_id": "引用【当前叙事机会】里已存在的 storylet_id，没有则留空字符串",
      "tags": ["可选的简短标签"]
    }}
  ]
}}
"""
        response = self._llm.generate(prompt)
        content = response.get("content", "")
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return self._empty_result()
        parsed = self._parse_json_response(content)
        if parsed is None:
            return self._empty_result()
        return self._normalize(parsed)

    def _parse_json_response(self, content: str) -> Optional[Dict[str, Any]]:
        content = (content or "").strip()
        if not content:
            return None
        block_match = re.search(r"```json\s*(\{.*\})\s*```", content, re.DOTALL)
        candidate = block_match.group(1).strip() if block_match else content
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or start >= end:
                return None
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                return None

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty_result()
        director_signals = data.get("director_signals", [])
        return {
            "director_signals": [
                item for item in director_signals if isinstance(item, dict)
            ] if isinstance(director_signals, list) else [],
        }

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {"director_signals": []}
