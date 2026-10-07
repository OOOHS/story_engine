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
            raise RuntimeError("NarrativeRenderer is not attached to a world entity")

        narration = self.scenario.narration if self.scenario else None
        max_sentences = narration.max_sentences if narration else 6
        max_characters = narration.max_characters if narration else 220
        guidance = list(narration.guidance) if narration else []
        player_resolution_anchors = self._player_resolution_anchors(render_payload)
        render_contract = {
            "visible_facts_only": True,
            "player_pov_locked": True,
            "no_new_state_changes": True,
            "no_invented_prehistory": True,
            "offscreen_events_return_as_aftereffects": True,
            "max_sentences": max_sentences,
            "max_characters": max_characters,
            "required_player_resolution_anchors": player_resolution_anchors,
        }

        prompt = f"""
你现在处于故事引擎的【Rendering】阶段。底层事实已经结算完毕，你的职责是把这些确定事实渲染成可读的沉浸式文字。

硬约束：
1. 只能渲染结构化输入里已经确定的事实，不得新增状态改变。
2. 只能写玩家此刻能直接感到的内容；禁止全知旁白，禁止切去异地补拍过程。
3. 若玩家没亲眼见到异地事件，只能写余波、传话、催促、态度变化或场面残响，不要写成共享回忆。
4. 若没有明确锚点，不要用“刚才那句……”“方才那个动作……”之类的精确回指。
5. `required_player_resolution_anchors` 是玩家行动结果的事实依据；完整保留重要结果、因果与来源，允许自然改写和合并。
6. 若 `simulation_result.resolved_actions` 里已有他人对玩家造成的 `public` 且 `complication/blocked` 动作，应明确写出，不要全部融成泛泛的气氛描写。
7. 不得添加 `simulation_result` 中没有成立的新动作，包括任何肢体接触；是否成立已经由 Simulation 决定，不要再次裁定。
8. 不要把不同角色渲染成重复的同一种动作。
9. 引用或转述保持原表达的语义、说话者和事实地位；未知内容保持来源。
10. 没有场景风格指导时保持中立、清楚，不自行选择题材腔调或叙事节奏。
11. communicate 的结果确认谁说了什么，保留说话人的来源。原话中的人物、物品、地点及关系可以保持真实性待定；仅渲染这次表达和已提交的外部事实。

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
            raise RuntimeError("narrative model is unavailable; delivery can be retried")
        for attempt in range(2):
            final_text = content
            verdict = self._validate_narration(final_text, render_payload)
            if verdict["valid"] and not verdict["issues"]:
                return final_text
            if attempt == 0:
                response = self._llm.generate(
                    "## 叙述语义修正\n依据问题修正整段叙述，保留已提交的玩家行动结果与说话来源。"
                    "仅输出可交付叙述。\n" + json.dumps({
                        "narration": final_text, "issues": verdict["issues"],
                        "visible_facts": render_payload,
                    }, ensure_ascii=False)
                )
                content = (response.get("content", "") or "").strip()
                if not content or content.startswith("[LLM "):
                    raise RuntimeError("narrative semantic repair unavailable")
        raise RuntimeError("narrative semantic check rejected delivery")

    def _validate_narration(self, text: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._llm.generate(
            "## 叙述语义校对\n"
            "检查最终玩家叙述相对于玩家可感知的已提交事实是否可靠。"
            "输出 {\"valid\":true,\"issues\":[]} 或 {\"valid\":false,\"issues\":[\"具体问题\"]}。"
            "以当前结构化结算与玩家 POV 为事实依据，continuity 中的旧叙述仅用于理解引用来源。逐一检查动作、状态变化、因果、归属、时间、地点和知识来源；"
            "完整交付玩家行动的重要结果和本人 private_result；拒绝新增事件、暗示未提交的持续后果、代替角色选择及泄漏他人私有事实。"
            "语言表达保留说话人的来源，谎言、传闻、假设和未知命题保持各自的事实地位。"
            "允许忠实释义与措辞变化，直接台词必须保留原话语义和说话者，"
            "气氛描述必须符合可见环境。根据语义审核整段文本。\n## 叙述数据\n"
            + json.dumps({"narration": text, "visible_facts": payload}, ensure_ascii=False)
        )
        try:
            verdict = json.loads(response.get("content", ""))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("narrative semantic check unavailable or malformed") from exc
        if (not isinstance(verdict, dict) or type(verdict.get("valid")) is not bool
                or not isinstance(verdict.get("issues"), list)
                or any(not isinstance(issue, str) for issue in verdict["issues"])):
            raise RuntimeError("narrative semantic check unavailable or malformed")
        return verdict

    def _fallback_render(self, render_payload: Dict[str, Any]) -> str:
        text = self._build_fallback_text(render_payload)
        narration = self.scenario.narration if self.scenario else None
        max_sentences = narration.max_sentences if narration else 6
        max_characters = narration.max_characters if narration else 220
        grounded = self._ground_render_text(
            self._trim_render_text(
                text,
                max_sentences,
                max_characters,
            ),
            render_payload,
            allow_fallback=False,
        )
        return self._ensure_player_resolution_anchors(
            grounded,
            self._player_resolution_anchors(render_payload),
            max_sentences=max_sentences,
            max_characters=max_characters,
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
            if isinstance(item, dict)
            and (
                item.get("visibility", "public") != "hidden"
                or item.get("actor") == player_name
            )
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
        """Offline formatting only; production grounding uses semantic validation."""
        if not (text or "").strip():
            raise RuntimeError("narrative model returned no usable text")
        return text.strip()

    def _player_resolution_anchors(self, render_payload: Dict[str, Any]) -> List[str]:
        player_name = str(
            render_payload.get("player_pov", {}).get("viewer", "")
        ).strip()
        if not player_name:
            return []
        anchors: List[str] = []
        for item in render_payload.get("simulation_result", {}).get(
            "resolved_actions", []
        ):
            if not isinstance(item, dict) or item.get("actor") != player_name:
                continue
            result = re.sub(r"\s+", " ", str(item.get("result", "")).strip())
            if result and result not in anchors:
                anchors.append(result)
        return anchors

    def _ensure_player_resolution_anchors(
        self,
        text: str,
        anchors: List[str],
        *,
        max_sentences: int,
        max_characters: int,
    ) -> str:
        missing = [anchor for anchor in anchors if anchor not in text]
        if not missing:
            return text

        prefix = " ".join(missing)
        if not text:
            return prefix
        remaining = max_characters - len(prefix) - 1
        if remaining <= 0:
            return prefix
        suffix = self._trim_render_text(
            text,
            max(1, max_sentences - len(missing)),
            remaining,
        )
        return f"{prefix} {suffix}".strip()
