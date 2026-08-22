import json
import re
from typing import Any, Dict, List, Optional
from pydantic import PrivateAttr, Field
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig


class NarrativeDirector(Component):
    """Post-settlement narrative intuition, kept out of SimulationControl.

    SimulationControl decides what happened this tick: it must run
    synchronously, exactly once, before the world commits, and a failure
    there has to halt the tick (fail-closed) because an authoritative fact
    cannot be half-decided. That is a settlement concern and has nothing to
    do with narrative judgment.

    NarrativeDirector answers a different question -- "given what just
    became true, is a new plot thread or a nudge to some character worth
    proposing?" -- strictly *after* that commit has already succeeded. It
    never writes a world fact itself: everything it returns is either
    non-binding (``director_signals``, a character may ignore it) or
    conditional on a future tick's facts (``plot_beat_proposals``, cashed
    out later by CausalPlotEngine/StoryletEngine). Its output still passes
    through the same SemanticAuthorityFilter as settlement before use.

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

## 你唯一能做的两件事
1. `director_signals`：给某个在场角色投递一句软性提示。宿主会把它放进该角色的收件箱，
   角色可以采纳、重新解读或直接无视。只有当某个【当前叙事机会】需要具体角色自己做出行动/表态的决定时才给；
   纯环境性的机会已经由结算阶段直接处理成事实，不需要你再提示。每个角色最多一条，全局每轮最多 3 条。
2. `plot_beat_proposals`：登记一个**尚未兑现**的新剧情点（开新线或给已有线加一个 beat），不是修改剧情钟。
   只有当【叙事压力】提示 inject_crisis/raise_pressure，或本轮已提交事实确实打开了新的因果关系时才提案；
   stay_course/allow_release 时什么都不做，留空数组即可。每条必须写清触发条件（scene/world_object/actor/plot），
   条件成立后由结算阶段在未来某一轮兑现——environment 类结算为 actor=World 的事实，
   character_decision 类等在场角色自己 proposal 才结算。开新线要给 opened_reason。
   不要写 clock/advance/plot_updates，那永远只能由已提交事实触发的因果规则推进。

## 本轮刚刚成立的事实（只读，不能改写）
{json.dumps(input_payload.get("committed_facts", {}), ensure_ascii=False, indent=2)}

## 当前剧情线
{json.dumps(input_payload.get("plot_threads", []), ensure_ascii=False, indent=2)}

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
      "source_plot_id": "引用【当前剧情线】里已存在的 plot_id，没有则留空字符串",
      "source_storylet_id": "引用【当前叙事机会】里已存在的 storylet_id，没有则留空字符串",
      "tags": ["可选的简短标签"]
    }}
  ],
  "plot_beat_proposals": [
    {{
      "plot_id": "已有剧情线 id，或与 open_thread 一起给出的新 id",
      "beat_id": "本条剧情点的稳定 id",
      "intent": "条件成立后这条机会是什么",
      "kind": "environment | character_decision",
      "one_shot": true,
      "open_thread": {{
        "title": "仅在开新线时填写",
        "description": "这条线在跟踪什么",
        "opened_reason": "必须填写：依据哪条已提交事实或当前压力打开这条线",
        "participants": ["已有角色"]
      }},
      "conditions": [
        {{
          "scope": "scene | world_object | actor | plot",
          "target": "world_object/actor/plot 必填的已有对象名",
          "path": "location | owner | hidden | kind | scene_flags.xxx；exists 可留空",
          "operator": "eq | ne | gt | gte | lt | lte | contains | in | exists | not_exists",
          "value": "与权威状态比较的值"
        }}
      ],
      "effect": {{
        "visibility": "public | local | hidden",
        "preferred_actors": ["character_decision 时可选的在场角色"],
        "target_actor": "可选的单一角色",
        "stake": "简短利害"
      }}
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
        plot_beat_proposals = data.get("plot_beat_proposals", [])
        return {
            "director_signals": [
                item for item in director_signals if isinstance(item, dict)
            ] if isinstance(director_signals, list) else [],
            "plot_beat_proposals": [
                item for item in plot_beat_proposals if isinstance(item, dict)
            ] if isinstance(plot_beat_proposals, list) else [],
        }

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {"director_signals": [], "plot_beat_proposals": []}
