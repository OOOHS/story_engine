import json
import re
from typing import Dict, Any, Optional, List
from pydantic import PrivateAttr, Field
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig


class NarrativeRenderer(Component):
    """
    Converts already-resolved facts into player-facing prose.
    This stage must not invent new state changes.
    """
    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def render(self, render_payload: Dict[str, Any]) -> str:
        if not self.entity:
            return self._fallback_render(render_payload)

        narration = self.scenario.narration if self.scenario else None
        max_sentences = narration.max_sentences if narration else 6
        max_characters = narration.max_characters if narration else 220
        guidance = list(narration.guidance) if narration else []
        render_contract = {
            "visible_facts_only": True,
            "player_pov_locked": True,
            "no_new_state_changes": True,
            "no_invented_prehistory": True,
            "offscreen_events_return_as_aftereffects": True,
            "max_sentences": max_sentences,
            "max_characters": max_characters,
            "allow_unsignaled_touch": bool(render_payload.get("social", {}).get("allow_unsignaled_touch", False)),
        }

        prompt = f"""
你现在处于故事引擎的【Rendering】阶段。底层事实已经结算完毕，你的职责是把这些确定事实渲染成可读的沉浸式文字。

硬约束：
1. 只能渲染结构化输入里已经确定的事实，不得新增状态改变。
2. 只能写玩家此刻能直接感到的内容；禁止全知旁白，禁止切去异地补拍过程。
3. 若玩家没亲眼见到异地事件，只能写余波、传话、催促、态度变化或场面残响，不要写成共享回忆。
4. 若没有明确锚点，不要用“刚才那句……”“方才那个动作……”之类的精确回指。
5. 若 `simulation_result.resolved_actions` 里已有他人对玩家造成的 `public` 且 `complication/blocked` 动作，应明确写出，不要全部融成泛泛的气氛描写。
6. 若 `social.allow_unsignaled_touch` 为 false，就不要凭空补肢体动作；只描述结构化输入中已经成立的站位、交流和对象变化。
7. 不要把不同角色渲染成重复的同一种动作。
8. 除非结构化输入里已经明确给出了原话，否则不要写直接引号台词；优先改写成间接描述。
9. 没有场景风格指导时保持中立、清楚，不自行选择题材腔调或叙事节奏。

剧本：{self.scenario.name if self.scenario else "通用剧本"}
环境基调：{self.scenario.environment if self.scenario else ""}
场景风格指导：
{json.dumps(guidance, ensure_ascii=False, indent=2)}
渲染契约：
{json.dumps(render_contract, ensure_ascii=False, indent=2)}

本轮结构化输入：
{json.dumps(render_payload, ensure_ascii=False, indent=2)}

请输出一段适合文字冒险游戏展示给玩家的中文叙述。
"""

        response = self._llm.generate(prompt)
        content = (response.get("content", "") or "").strip()
        if not content or content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return self._fallback_render(render_payload)
        return self._ground_render_text(
            self._trim_render_text(content, max_sentences, max_characters),
            render_payload,
        )

    def _fallback_render(self, render_payload: Dict[str, Any]) -> str:
        text = self._build_fallback_text(render_payload)
        narration = self.scenario.narration if self.scenario else None
        return self._ground_render_text(
            self._trim_render_text(
                text,
                narration.max_sentences if narration else 6,
                narration.max_characters if narration else 220,
            ),
            render_payload,
            allow_fallback=False,
        )

    def _build_fallback_text(self, render_payload: Dict[str, Any]) -> str:
        simulation = render_payload.get("simulation_result", {})
        player_pov = render_payload.get("player_pov", {})
        player_name = player_pov.get("viewer")
        parts: List[str] = []

        description = player_pov.get("location")
        if description:
            parts.append(f"你仍在 {description}。")

        visible_actions = [
            item
            for item in simulation.get("resolved_actions", [])
            if isinstance(item, dict) and item.get("visibility", "public") == "public"
        ]
        concrete_actions = [
            item
            for item in visible_actions
            if str(item.get("result", "")).strip()
            and str(item.get("result", "")).strip() != "系统未完成结构化判定，暂按意图记录。"
        ]

        if concrete_actions:
            player_actions = [item for item in concrete_actions if item.get("actor") == player_name]
            world_actions = [item for item in concrete_actions if item.get("actor") == "World"]
            hostile_actions = [
                item
                for item in concrete_actions
                if item.get("actor") not in {player_name, "World"}
                and item.get("outcome") in {"complication", "blocked"}
            ]
            other_actions = [
                item
                for item in concrete_actions
                if item not in player_actions and item not in world_actions and item not in hostile_actions
            ]
            ordered_actions = player_actions + world_actions + hostile_actions + other_actions
        else:
            ordered_actions = visible_actions

        for item in ordered_actions[:4]:
            actor = item.get("actor", "某人")
            intent = item.get("intent", "")
            result = item.get("result", "")
            if not result:
                continue
            if result == "系统未完成结构化判定，暂按意图记录。":
                parts.append(f"{actor}刚刚有了动作，局面暂时还看不出更明确的变化。")
            elif actor == player_name:
                parts.append(f"你{result}")
            elif actor == "World":
                parts.append(result)
            else:
                parts.append(f"{actor}{result or f'尝试了{intent}'}")

        for item in simulation.get("topology_changes", [])[:2]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "")).strip()
            if statement:
                parts.append(statement)

        for item in simulation.get("host_object_state_changes", [])[:2]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "")).strip()
            if statement:
                parts.append(statement)

        notes = simulation.get("simulation_notes", [])
        public_notes = [
            str(item).strip()
            for item in notes
            if str(item).strip()
            and "回退模式" not in str(item)
            and "冲突节拍器" not in str(item)
            and "优先 storylet" not in str(item)
            and "结构化推进" not in str(item)
        ]
        if public_notes:
            parts.append("；".join(public_notes[:2]))

        timeline = render_payload.get("timeline", {})
        last_missed = timeline.get("last_missed_commitment") if isinstance(timeline, dict) else None
        if isinstance(last_missed, dict):
            note = last_missed.get("note", "")
            if note:
                parts.append(note)

        if not parts:
            return "局面暂时没有显著变化。"
        return " ".join(parts)

    def _trim_render_text(self, text: str, max_sentences: int = 6, max_chars: int = 220) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if not normalized:
            return "局面暂时没有显著变化。"

        sentences = re.split(r"(?<=[。！？!?])\s*", normalized)
        kept: List[str] = []
        total_len = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            projected = total_len + len(sentence)
            if kept and (len(kept) >= max_sentences or projected > max_chars):
                break
            kept.append(sentence)
            total_len += len(sentence)

        if not kept:
            kept = [normalized[:max_chars].rstrip("，,、 ") + ("…" if len(normalized) > max_chars else "")]

        result = " ".join(kept).strip()
        if len(result) > max_chars:
            result = result[: max_chars - 1].rstrip("，,、 ") + "…"
        return result

    def _ground_render_text(self, text: str, render_payload: Dict[str, Any], allow_fallback: bool = True) -> str:
        grounded = (text or "").strip()
        if not grounded:
            return "局面暂时没有显著变化。"

        source_text = json.dumps(render_payload, ensure_ascii=False)
        current_fact_text = json.dumps(
            {
                "simulation_result": render_payload.get("simulation_result", {}),
                "current_visible_facts": render_payload.get("current_visible_facts", []),
            },
            ensure_ascii=False,
        )
        if allow_fallback and (
            self._has_ungrounded_touch(grounded, current_fact_text)
            or self._has_ungrounded_dialogue(grounded, source_text)
        ):
            narration = self.scenario.narration if self.scenario else None
            fallback_text = self._trim_render_text(
                self._build_fallback_text(render_payload),
                narration.max_sentences if narration else 6,
                narration.max_characters if narration else 220,
            )
            grounded = fallback_text

        def replace_callback(match: re.Match[str]) -> str:
            quoted = match.group(1).strip()
            if quoted and quoted in source_text:
                return match.group(0)
            return "刚才那点余波"

        grounded = re.sub(
            r"刚才那句[“\"]([^”\"]{1,24})[”\"]的余音",
            replace_callback,
            grounded,
        )
        grounded = re.sub(
            r"方才那句[“\"]([^”\"]{1,24})[”\"]的余音",
            replace_callback,
            grounded,
        )
        grounded = re.sub(r"刚才那句[“\"]([^”\"]{1,24})[”\"]", replace_callback, grounded)
        grounded = re.sub(r"方才那句[“\"]([^”\"]{1,24})[”\"]", replace_callback, grounded)
        grounded = re.sub(
            r"[“\"]([^”\"]{1,24})[”\"]的余音",
            lambda match: match.group(0) if match.group(1).strip() in source_text else "刚才那点余波",
            grounded,
        )
        return grounded

    def _has_ungrounded_touch(self, text: str, source_text: str) -> bool:
        touch_patterns = [
            r"搭在[^。！？]{0,12}(肩|手|手背|背上)",
            r"按在[^。！？]{0,12}(肩|手|手背|背上)",
            r"扶着[^。！？]{0,12}(坐|入席|起身)",
            r"扶住",
            r"握住",
            r"覆上[^。！？]{0,12}(手|手背)",
            r"缩进[^。！？]{0,12}怀里",
            r"揽住",
            r"揽到",
        ]
        has_touch = any(re.search(pattern, text) for pattern in touch_patterns)
        if not has_touch:
            return False
        return not any(re.search(pattern, source_text) for pattern in touch_patterns)

    def _has_ungrounded_dialogue(self, text: str, source_text: str) -> bool:
        quotes = re.findall(r"[“\"]([^”\"]{2,36})[”\"]", text or "")
        if not quotes:
            return False
        for quote in quotes:
            normalized = quote.strip()
            if not normalized:
                continue
            if normalized not in source_text:
                return True
        return False
