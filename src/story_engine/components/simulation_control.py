import json
import re
from typing import Dict, Any, Optional, List, Tuple
from pydantic import PrivateAttr, Field
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.common.movement_intent import extract_move_target_from_intent


class SimulationControl(Component):
    """
    Resolves intents into structured consequences.
    This stage is not allowed to generate player-facing prose.
    """
    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def simulate(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.entity:
            return self._fallback_result(input_payload, note="SimulationControl has no attached entity.")

        scene_state = self.entity.get_component("SceneState")
        memory = self.entity.get_component("Memory")

        state_snapshot = scene_state.get_snapshot() if scene_state else {}
        query = "\n".join([item.get("intent", "") for item in input_payload.get("intents", [])]).strip()

        relevant_memories: List[str] = []
        if memory and query:
            relevant_memories = memory.retrieve(query, n_results=3)

        # 构建简化的场景上下文（合并 player_pov + spatial_layout + social）
        scene_context = self._build_scene_context(input_payload)

        # 构建简化的压力上下文（合并 storylet + conflict + motive）
        pressure_context = self._build_pressure_context(input_payload)

        prompt = f"""
你现在处于故事引擎的【Simulation】阶段。你的职责是做结构化结算，而不是写给玩家看的文本。

## 引擎核心规则（通用）
1. **只输出 JSON**：`state_updates` 是唯一允许改变世界状态的来源
2. **尊重当前状态**：严格遵守空间连通性、合法性裁决、关系值；不补前情，不伪造玩家历史
3. **玩家意图优先**：玩家的明确提议是主锚点，除非被 legality 阻止或被更强压力改变
4. **受限视角**：异地事件只能以余波、传话、态度变化回流，不切全知镜头
5. **快节奏**：优先落下清晰局面变化，不写长段气氛描写
6. **可观察事实**：使用可观察的行为和事实，不下文学化诊断结论

## 剧本设定
**剧本**：{self.scenario.name if self.scenario else "通用剧本"}
**初始局面**：{self.scenario.initial_state if self.scenario else ""}

## 场景特定规则
{json.dumps(self.scenario.rules if self.scenario else [], ensure_ascii=False, indent=2)}

## 当前状态
{json.dumps(state_snapshot, ensure_ascii=False, indent=2)}

## 场景上下文
{json.dumps(scene_context, ensure_ascii=False, indent=2)}

## 本轮意图
{json.dumps(input_payload.get("intents", []), ensure_ascii=False, indent=2)}

## 压力上下文
{json.dumps(pressure_context, ensure_ascii=False, indent=2)}

## 合法性裁决
{json.dumps(input_payload.get("legality", {}), ensure_ascii=False, indent=2)}

## 相关记忆
{json.dumps(relevant_memories, ensure_ascii=False, indent=2)}

## 输出格式
输出 JSON 模板：
{{
  "resolved_actions": [
    {{
      "actor": "角色名",
      "intent": "输入意图",
      "outcome": "success | partial | fail | blocked | complication",
      "location": "动作发生地点",
      "result": "内部结果摘要",
      "visibility": "public | local | hidden"
    }}
  ],
  "state_updates": {{
    "scene": {{}},
    "world_objects": {{}},
    "actor_states": {{}}
  }},
 "storylet_hits": ["storylet_id"],
  "conflict_level": "none | low | medium | high",
  "conflict_flags": ["white_lotus", "public_blame"],
  "tension_delta": 0.0,
  "plot_updates": [
    {{
      "plot_id": "plot_id",
      "advance": 1,
      "stage_shift": 0,
      "note": "为何推进"
    }}
  ],
  "spawn_character": null,
  "simulation_notes": ["供渲染阶段参考的事实备注"]
}}

只输出 JSON，不要输出解释，不要使用 Markdown。
"""

        response = self._llm.generate(prompt)
        content = response.get("content", "")
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return self._fallback_result(input_payload, note="结构化模拟回退模式已启用。")
        parsed = self._parse_json_response(content)
        if parsed is None:
            return self._fallback_result(input_payload, note="结构化模拟输出解析失败，已回退。")
        normalized = self._normalize_result(parsed, input_payload)
        normalized = self._enforce_legality(normalized, input_payload)
        normalized = self._enforce_conflict(normalized, input_payload)
        normalized = self._enforce_storylets(normalized, input_payload)
        return self._enforce_social_realism(normalized, input_payload)

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

    def _normalize_result(self, data: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._empty_result()
        result.update({k: v for k, v in data.items() if k in result})

        resolved_actions = []
        for item in data.get("resolved_actions", []):
            if not isinstance(item, dict):
                continue
            resolved_actions.append(
                {
                    "actor": item.get("actor", "Unknown"),
                    "intent": item.get("intent", ""),
                    "outcome": item.get("outcome", "partial"),
                    "location": item.get("location") or self._infer_location(item.get("actor"), input_payload),
                    "result": item.get("result", ""),
                    "visibility": item.get("visibility", "public"),
                }
            )
        if not resolved_actions:
            resolved_actions = self._fallback_result(input_payload)["resolved_actions"]
        result["resolved_actions"] = resolved_actions

        state_updates = data.get("state_updates", {})
        if not isinstance(state_updates, dict):
            state_updates = {}
        result["state_updates"] = state_updates

        storylet_hits = data.get("storylet_hits", [])
        if not isinstance(storylet_hits, list):
            storylet_hits = []
        result["storylet_hits"] = storylet_hits

        conflict_level = str(data.get("conflict_level", "none")).strip().lower()
        if conflict_level not in {"none", "low", "medium", "high"}:
            conflict_level = "none"
        result["conflict_level"] = conflict_level

        conflict_flags = data.get("conflict_flags", [])
        if not isinstance(conflict_flags, list):
            conflict_flags = [str(conflict_flags)]
        result["conflict_flags"] = [str(item).strip() for item in conflict_flags if str(item).strip()]

        try:
            result["tension_delta"] = float(data.get("tension_delta", 0.0))
        except (TypeError, ValueError):
            result["tension_delta"] = 0.0

        plot_updates = data.get("plot_updates", [])
        if not isinstance(plot_updates, list):
            plot_updates = []
        result["plot_updates"] = [item for item in plot_updates if isinstance(item, dict)]

        spawn_character = data.get("spawn_character")
        if spawn_character is None and isinstance(data.get("introduce_character"), dict):
            spawn_character = data.get("introduce_character")
        if isinstance(spawn_character, dict) and spawn_character.get("name"):
            spawn_character.setdefault("role", "路人")
            spawn_character.setdefault("personality", "未知")
            goals = spawn_character.get("goals", [])
            if isinstance(goals, str):
                goals = [goals]
            spawn_character["goals"] = goals
            result["spawn_character"] = spawn_character

        notes = data.get("simulation_notes", [])
        if not isinstance(notes, list):
            notes = [str(notes)]
        result["simulation_notes"] = [str(item) for item in notes if str(item).strip()]
        return result

    def _infer_location(self, actor_name: Optional[str], input_payload: Dict[str, Any]) -> Optional[str]:
        if not actor_name:
            return None
        for item in input_payload.get("intents", []):
            if item.get("actor") == actor_name:
                return item.get("location")
        return input_payload.get("player_pov", {}).get("location")

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "resolved_actions": [],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
            },
            "storylet_hits": [],
            "conflict_level": "none",
            "conflict_flags": [],
            "tension_delta": 0.0,
            "plot_updates": [],
            "spawn_character": None,
            "simulation_notes": [],
            "applied_conflict_templates": [],
        }

    def _enforce_legality(self, result: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        legality_checks = input_payload.get("legality", {}).get("checks", [])
        if not isinstance(legality_checks, list):
            return result

        actor_updates = result.setdefault("state_updates", {}).setdefault("actor_states", {})
        notes = result.setdefault("simulation_notes", [])

        for check in legality_checks:
            if not isinstance(check, dict):
                continue
            actor = check.get("actor")
            intent = check.get("intent", "")
            verdict = check.get("verdict", "allow")
            if not actor or verdict == "allow":
                continue

            action = self._find_matching_action(result.get("resolved_actions", []), actor, intent)
            if verdict == "block":
                if action is None:
                    action = {
                        "actor": actor,
                        "intent": intent,
                        "outcome": "blocked",
                        "location": self._infer_location(actor, input_payload),
                        "result": "",
                        "visibility": "public" if actor == input_payload.get("player_name") else "local",
                    }
                    result.setdefault("resolved_actions", []).append(action)
                action["outcome"] = "blocked"
                action["location"] = self._infer_location(actor, input_payload)
                action["result"] = check.get("reason", "这个动作不符合当前世界法则。")
                actor_updates.pop(actor, None)
                note = f"{actor}的动作被裁定为不合法：{check.get('reason', '')}".strip()
                if note and note not in notes:
                    notes.append(note)
                continue

            if verdict == "rewrite":
                rewrite_location = check.get("rewrite_location")
                suggested = check.get("suggested_intent", "")
                if action is None:
                    action = {
                        "actor": actor,
                        "intent": intent,
                        "outcome": "partial",
                        "location": rewrite_location or self._infer_location(actor, input_payload),
                        "result": "",
                        "visibility": "public" if actor == input_payload.get("player_name") else "local",
                    }
                    result.setdefault("resolved_actions", []).append(action)
                action["outcome"] = "partial" if action.get("outcome") == "success" else action.get("outcome", "partial")
                if rewrite_location:
                    action["location"] = rewrite_location
                    actor_updates.setdefault(actor, {})["location"] = rewrite_location
                base_result = action.get("result", "").strip()
                rewrite_line = check.get("reason", "")
                if suggested:
                    rewrite_line = f"{rewrite_line} 实际只能{suggested}。".strip()
                action["result"] = rewrite_line if not base_result else f"{rewrite_line} {base_result}".strip()
                note = f"{actor}的动作被改写为合法版本。"
                if note not in notes:
                    notes.append(note)

        return result

    def _enforce_conflict(self, result: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        conflict_packet = input_payload.get("conflict", {})
        if not isinstance(conflict_packet, dict):
            return result
        if not conflict_packet.get("require_visible_conflict"):
            return result

        current_level = str(result.get("conflict_level", "none"))
        minimum_level = str(conflict_packet.get("minimum_level_when_forced", "medium"))
        if (
            self._conflict_rank(current_level) >= self._conflict_rank(minimum_level)
            and self._has_concrete_public_conflict(result)
        ):
            return result

        forced_actions, fallback_flags, template_ids = self._build_forced_conflict_actions(conflict_packet, input_payload)
        if not forced_actions:
            return result

        existing_actions = list(result.get("resolved_actions", []))
        result["resolved_actions"] = forced_actions + existing_actions
        result["applied_conflict_templates"] = template_ids
        result["conflict_level"] = minimum_level
        merged_flags = list(result.get("conflict_flags", []))
        for flag in fallback_flags:
            if flag not in merged_flags:
                merged_flags.append(flag)
        result["conflict_flags"] = merged_flags
        tension_floor = 0.16 + max(0, len(forced_actions) - 1) * 0.06
        result["tension_delta"] = max(float(result.get("tension_delta", 0.0)), tension_floor)
        notes = result.setdefault("simulation_notes", [])
        note = "冲突节拍器补入了当场可见的挑刺/护短。"
        if note not in notes:
            notes.append(note)
        return result

    def _enforce_storylets(self, result: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        storylet_packet = input_payload.get("storylet_pressure", {})
        if not isinstance(storylet_packet, dict):
            return result

        priority_storylets = [
            item for item in storylet_packet.get("priority_storylets", [])
            if isinstance(item, dict) and str(item.get("storylet_id", "")).strip()
        ]
        if not priority_storylets:
            return result
        forced_storylet_id = str(storylet_packet.get("forced_storylet_id", "")).strip()
        forced_storylet = next(
            (
                item for item in priority_storylets
                if str(item.get("storylet_id", "")).strip() == forced_storylet_id
            ),
            priority_storylets[0],
        )

        claimed_hits = [
            str(item).strip()
            for item in result.get("storylet_hits", [])
            if str(item).strip()
        ]
        hits = [
            str(item.get("storylet_id", "")).strip()
            for item in priority_storylets
            if str(item.get("storylet_id", "")).strip() in claimed_hits
            and self._storylet_has_concrete_realization(item, result)
        ]
        if not hits:
            for storylet in priority_storylets:
                if self._storylet_matches_result(storylet, result):
                    storylet_id = str(storylet.get("storylet_id", "")).strip()
                    if storylet_id and storylet_id not in hits:
                        hits.append(storylet_id)

        forced_storylet_realized = self._storylet_has_concrete_realization(forced_storylet, result)
        if storylet_packet.get("require_hit") and not forced_storylet_realized:
            forced_actions, forced_flags, template_ids = self._build_forced_storylet_actions(
                forced_storylet,
                input_payload,
            )
            if forced_actions:
                result["resolved_actions"] = forced_actions + list(result.get("resolved_actions", []))
                merged_flags = list(result.get("conflict_flags", []))
                for flag in forced_flags:
                    if flag not in merged_flags:
                        merged_flags.append(flag)
                result["conflict_flags"] = merged_flags
                applied_templates = list(result.get("applied_conflict_templates", []))
                for template_id in template_ids:
                    if template_id and template_id not in applied_templates:
                        applied_templates.append(template_id)
                result["applied_conflict_templates"] = applied_templates
            forced_storylet_hit = str(forced_storylet.get("storylet_id", "")).strip()
            if forced_storylet_hit:
                hits.append(forced_storylet_hit)
            notes = result.setdefault("simulation_notes", [])
            note = f"优先 storylet 已被结构化推进：{forced_storylet.get('storylet_id', '')}"
            if note not in notes:
                notes.append(note)

        result["storylet_hits"] = list(dict.fromkeys(item for item in hits if item))
        return result

    def _storylet_has_concrete_realization(self, storylet: Dict[str, Any], result: Dict[str, Any]) -> bool:
        beat = storylet.get("beat", {})
        if not isinstance(beat, dict) or not beat:
            return False

        preferred_template_ids = {
            str(item).strip()
            for item in beat.get("preferred_template_ids", [])
            if str(item).strip()
        }
        preferred_actors = {
            str(item).strip()
            for item in beat.get("preferred_actors", [])
            if str(item).strip()
        }
        required_visibility = str(beat.get("visibility", "")).strip()
        required_flags = {
            str(item).strip()
            for item in beat.get("required_flags", [])
            if str(item).strip()
        }
        applied_template_ids = {
            str(item).strip()
            for item in result.get("applied_conflict_templates", [])
            if str(item).strip()
        }
        if preferred_template_ids and applied_template_ids.intersection(preferred_template_ids):
            return True

        anchored = bool(preferred_template_ids or preferred_actors)
        for action in result.get("resolved_actions", []):
            if not isinstance(action, dict):
                continue
            if required_visibility and str(action.get("visibility", "")).strip() != required_visibility:
                continue
            template_id = str(action.get("template_id", "")).strip()
            if preferred_template_ids and template_id in preferred_template_ids:
                return True
            actor = str(action.get("actor", "")).strip()
            detail = str(action.get("result", "")).strip()
            if (
                preferred_actors
                and actor in preferred_actors
                and action.get("outcome") in {"blocked", "complication", "partial"}
                and detail
                and not self._is_placeholder_result(detail)
            ):
                return True

        if anchored:
            return False

        if required_flags:
            conflict_flags = {
                str(item).strip()
                for item in result.get("conflict_flags", [])
                if str(item).strip()
            }
            if required_flags.intersection(conflict_flags):
                return True

        return False

    def _storylet_matches_result(self, storylet: Dict[str, Any], result: Dict[str, Any]) -> bool:
        beat = storylet.get("beat", {})
        if isinstance(beat, dict) and beat:
            if self._storylet_has_concrete_realization(storylet, result):
                return True
            preferred_template_ids = {
                str(item).strip()
                for item in beat.get("preferred_template_ids", [])
                if str(item).strip()
            }
            preferred_actors = {
                str(item).strip()
                for item in beat.get("preferred_actors", [])
                if str(item).strip()
            }
            required_visibility = str(beat.get("visibility", "")).strip()
            required_flags = {
                str(item).strip()
                for item in beat.get("required_flags", [])
                if str(item).strip()
            }

            for action in result.get("resolved_actions", []):
                if not isinstance(action, dict):
                    continue
                if preferred_template_ids and str(action.get("template_id", "")).strip() in preferred_template_ids:
                    return True
                if preferred_actors and str(action.get("actor", "")).strip() not in preferred_actors:
                    continue
                if required_visibility and str(action.get("visibility", "")).strip() != required_visibility:
                    continue
                if preferred_actors:
                    return True

            if required_flags and not (preferred_template_ids or preferred_actors):
                conflict_flags = {
                    str(item).strip()
                    for item in result.get("conflict_flags", [])
                    if str(item).strip()
                }
                if required_flags.intersection(conflict_flags):
                    return True

        storylet_tags = [
            str(item).strip()
            for item in storylet.get("tags", [])
            if str(item).strip()
        ]
        if not storylet_tags:
            return False

        conflict_flags = [
            str(item).strip()
            for item in result.get("conflict_flags", [])
            if str(item).strip()
        ]
        for tag in storylet_tags:
            for flag in conflict_flags:
                if tag == flag or tag in flag or flag in tag:
                    return True

        resolved_text = " ".join(
            str(item.get("result", "")).strip()
            for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        )
        intent_text = str(storylet.get("intent", "")).strip()
        return bool(intent_text and any(token and token in resolved_text for token in re.split(r"[、，。；,\s]+", intent_text)[:4]))

    def _build_forced_storylet_actions(
        self,
        storylet: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        beat = storylet.get("beat", {})
        if not isinstance(beat, dict) or not beat:
            return [], [], []

        conflict_packet = input_payload.get("conflict", {})
        if not isinstance(conflict_packet, dict):
            return [], [], []

        preferred_template_ids = [
            str(item).strip()
            for item in beat.get("preferred_template_ids", [])
            if str(item).strip()
        ]
        if not preferred_template_ids:
            return [], list(beat.get("required_flags", [])), []

        templates_by_id = {
            str(item.get("template_id", "")).strip(): item
            for item in conflict_packet.get("active_templates", [])
            if isinstance(item, dict) and str(item.get("template_id", "")).strip()
        }
        public_location = input_payload.get("player_pov", {}).get("location")
        actions: List[Dict[str, Any]] = []
        template_ids: List[str] = []
        flags: List[str] = [str(item).strip() for item in beat.get("required_flags", []) if str(item).strip()]
        used_actors = set()

        for template_id in preferred_template_ids:
            template = templates_by_id.get(template_id)
            if not template:
                continue
            template_payload = dict(template)
            if beat.get("preferred_actors"):
                template_payload["preferred_actors"] = list(beat.get("preferred_actors", []))
            actor = self._pick_conflict_actor(
                template_payload,
                conflict_packet,
                input_payload,
                exclude=list(used_actors),
            )
            if not actor:
                continue
            result_text = self._pick_template_result(template_payload, conflict_packet)
            if not result_text:
                continue
            visibility = str(beat.get("visibility", "public")).strip() or "public"
            actions.append(
                {
                    "actor": actor,
                    "intent": template_payload.get("instruction", storylet.get("intent", "")),
                    "outcome": "complication",
                    "location": public_location,
                    "result": result_text,
                    "visibility": visibility,
                    "template_id": template_id,
                }
            )
            used_actors.add(actor)
            template_ids.append(template_id)
            for flag in template_payload.get("tags", []):
                flag_text = str(flag).strip()
                if flag_text and flag_text not in flags:
                    flags.append(flag_text)

        return actions, flags, template_ids

    def _enforce_social_realism(self, result: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        social_packet = input_payload.get("social", {})
        if not isinstance(social_packet, dict):
            return result

        if social_packet.get("allow_unsignaled_touch", True):
            return result

        notes = result.setdefault("simulation_notes", [])
        rewritten_touch = False
        for action in result.get("resolved_actions", []):
            if not isinstance(action, dict):
                continue
            detail = str(action.get("result", "")).strip()
            if not detail or not self._contains_touch_motif(detail):
                continue
            if self._touch_is_explicitly_supported(action, input_payload):
                continue
            action["result"] = self._soften_touch_result(action.get("actor", "某人"), detail)
            rewritten_touch = True

        if rewritten_touch:
            note = "未获状态支持的肢体接触被改写为更克制的站位/态度表达。"
            if note not in notes:
                notes.append(note)
        return result

    def _pick_conflict_template(self, conflict_packet: Dict[str, Any]) -> Dict[str, Any]:
        templates = self._pick_conflict_templates(conflict_packet, max_actions=1)
        return templates[0] if templates else {}

    def _pick_conflict_templates(
        self,
        conflict_packet: Dict[str, Any],
        max_actions: int = 1,
    ) -> List[Dict[str, Any]]:
        templates = [
            item
            for item in conflict_packet.get("active_templates", [])
            if isinstance(item, dict)
        ]
        if not templates:
            return []

        preferred_modes = [
            str(item).strip()
            for item in conflict_packet.get("preferred_modes", [])
            if str(item).strip()
        ]
        recent_template_ids = {
            str(item).strip()
            for item in conflict_packet.get("recent_template_ids", [])
            if str(item).strip()
        }
        surface_style = str(conflict_packet.get("surface_style", "implicit")).strip()
        directness = float(conflict_packet.get("verbal_directness", 0.0) or 0.0)
        storylet_tags = {
            str(item).strip()
            for item in conflict_packet.get("storylet_tags", [])
            if str(item).strip()
        }
        preferred_template_ids = {
            str(item).strip()
            for item in conflict_packet.get("storylet_template_ids", [])
            if str(item).strip()
        }
        director_directive = str(conflict_packet.get("director_directive", "")).strip()
        prefer_public_pressure = bool(conflict_packet.get("prefer_public_pressure"))

        def score(template: Dict[str, Any]) -> int:
            tags = [str(item).strip() for item in template.get("tags", []) if str(item).strip()]
            template_id = str(template.get("template_id", "")).strip()
            total = 0
            for index, mode in enumerate(preferred_modes):
                if mode in tags:
                    total += 200 - index * 40
                    break
            if template_id and template_id in preferred_template_ids:
                total += 280
            if "public" in tags:
                total += 12
            if "trap" in tags:
                total += 8
            if "family_bias" in tags or "public_blame" in tags:
                total += 6
            if prefer_public_pressure and any(tag in tags for tag in ["public", "trap", "comparison", "provocation", "white_lotus"]):
                total += 24
            if director_directive in {"inject_crisis", "raise_pressure"} and any(
                tag in tags for tag in ["trap", "public_blame", "comparison", "white_lotus", "engagement_pressure"]
            ):
                total += 30
            if storylet_tags and any(
                tag == storylet_tag or tag in storylet_tag or storylet_tag in tag
                for tag in tags
                for storylet_tag in storylet_tags
            ):
                total += 26
            if surface_style in {"barbed", "acid"} and any(tag in tags for tag in ["provocation", "comparison", "mockery", "white_lotus"]):
                total += 10
            if directness >= 0.8 and any(tag in tags for tag in ["provocation", "public_blame", "comparison"]):
                total += 10
            if template_id in recent_template_ids:
                total -= 120
            return total

        ranked = sorted(templates, key=score, reverse=True)
        selected: List[Dict[str, Any]] = []
        used_tags = set()
        for template in ranked:
            tags = {str(item).strip() for item in template.get("tags", []) if str(item).strip()}
            if not selected:
                selected.append(template)
                used_tags.update(tags)
                if len(selected) >= max_actions:
                    break
                continue
            if len(selected) >= max_actions:
                break
            if tags and tags.issubset(used_tags):
                continue
            selected.append(template)
            used_tags.update(tags)

        if not selected:
            return ranked[:max_actions]
        return selected[:max_actions]

    def _build_forced_conflict_actions(
        self,
        conflict_packet: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        reaction_context = input_payload.get("reaction_context", {})
        hostile_watchers = list(reaction_context.get("hostile_watchers", []) or [])
        intensity = float(conflict_packet.get("intensity", 0.0) or 0.0)
        visible_conflict_count = int(conflict_packet.get("visible_conflict_count", 0) or 0)
        directness = float(conflict_packet.get("verbal_directness", 0.0) or 0.0)
        player_action = str(reaction_context.get("player_action", "")).strip()
        explicit_challenge = any(
            token in player_action
            for token in ["问", "质问", "为什么", "凭什么", "是不是", "默认", "什么意思", "冒牌", "你们都", "不去"]
        )
        pressure_actors = self._rank_pressure_actors(input_payload, conflict_packet)
        max_requested_actions = int(conflict_packet.get("max_forced_actions", 0) or 0)
        prefer_public_pressure = bool(conflict_packet.get("prefer_public_pressure"))

        max_actions = 1
        if intensity >= 0.85 and pressure_actors:
            max_actions = 2
        if explicit_challenge and len(pressure_actors) >= 2:
            max_actions = 2
        if explicit_challenge and len(pressure_actors) >= 3 and directness >= 0.85:
            max_actions = 3
        if prefer_public_pressure and intensity >= 0.9:
            max_actions = max(max_actions, 2)
        if max_requested_actions > 0:
            max_actions = max(max_actions, max_requested_actions)
        if not pressure_actors and (
            intensity >= 0.85
            and len(hostile_watchers) >= 2
            and (visible_conflict_count == 0 or directness >= 0.85)
        ):
            max_actions = 2

        public_location = input_payload.get("player_pov", {}).get("location")
        used_actors = set()
        used_templates = set()
        actions: List[Dict[str, Any]] = []
        flags: List[str] = []
        template_ids: List[str] = []

        if pressure_actors:
            selected_actors = pressure_actors[:max_actions]
            for actor_entry in selected_actors:
                actor = str(actor_entry.get("actor", "")).strip()
                if not actor or actor in used_actors:
                    continue
                template = self._pick_template_for_actor(
                    actor_entry=actor_entry,
                    conflict_packet=conflict_packet,
                    used_templates=list(used_templates),
                )
                if not template:
                    continue
                fallback_result = self._pick_template_result(
                    template,
                    conflict_packet=conflict_packet,
                    turn_offset=len(actions),
                )
                if not fallback_result:
                    continue
                fallback_intent = str(template.get("instruction", "当场给玩家施加压力")).strip() or "当场给玩家施加压力"
                actions.append(
                    {
                        "actor": actor,
                        "intent": fallback_intent,
                        "outcome": "complication",
                        "location": public_location,
                        "result": fallback_result,
                        "visibility": "public",
                        "template_id": str(template.get("template_id", "")).strip(),
                    }
                )
                used_actors.add(actor)
                template_id = str(template.get("template_id", "")).strip()
                if template_id:
                    used_templates.add(template_id)
                    if template_id not in template_ids:
                        template_ids.append(template_id)
                for flag in template.get("tags", []):
                    flag_text = str(flag).strip()
                    if flag_text and flag_text not in flags:
                        flags.append(flag_text)
            if actions:
                return actions, flags, template_ids

        templates = self._pick_conflict_templates(conflict_packet, max_actions=max_actions)
        for template in templates:
            actor = self._pick_conflict_actor(
                template,
                conflict_packet,
                input_payload,
                exclude=list(used_actors),
            )
            if not actor:
                continue

            fallback_result = self._pick_template_result(
                template,
                conflict_packet=conflict_packet,
                turn_offset=len(actions),
            )
            fallback_intent = str(template.get("instruction", "当场给玩家施加压力")).strip() or "当场给玩家施加压力"
            if not fallback_result:
                fallback_result = "当场接过话头，轻飘飘地挑了你一句，把场面拉向了对你不利的方向。"

            actions.append(
                {
                    "actor": actor,
                    "intent": fallback_intent,
                    "outcome": "complication",
                    "location": public_location,
                    "result": fallback_result,
                    "visibility": "public",
                    "template_id": str(template.get("template_id", "")).strip(),
                }
            )
            used_actors.add(actor)
            template_id = str(template.get("template_id", "")).strip()
            if template_id and template_id not in template_ids:
                template_ids.append(template_id)
            for flag in template.get("tags", []):
                flag_text = str(flag).strip()
                if flag_text and flag_text not in flags:
                    flags.append(flag_text)

        return actions, flags, template_ids

    def _rank_pressure_actors(
        self,
        input_payload: Dict[str, Any],
        conflict_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        motive_packet = input_payload.get("motive_pressure", {})
        if not isinstance(motive_packet, dict):
            return []
        visible_pressures = [
            item for item in motive_packet.get("visible_pressures", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        ]
        if not visible_pressures:
            return []

        storylet_template_ids = {
            str(item).strip()
            for item in conflict_packet.get("storylet_template_ids", [])
            if str(item).strip()
        }
        def score(item: Dict[str, Any]) -> int:
            total = int(item.get("pressure_score", 0) or 0) * 10
            signatures = {
                str(entry).strip()
                for entry in item.get("signature_templates", [])
                if str(entry).strip()
            }
            if signatures.intersection(storylet_template_ids):
                total += 25
            profile = str(item.get("pressure_profile", "")).strip()
            if profile in {"white_lotus", "order_control", "cutoff_guard", "shielding", "polite_comparison"}:
                total += 10
            return total

        return sorted(visible_pressures, key=score, reverse=True)

    def _pick_template_for_actor(
        self,
        actor_entry: Dict[str, Any],
        conflict_packet: Dict[str, Any],
        used_templates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        actor = str(actor_entry.get("actor", "")).strip()
        if not actor:
            return {}
        excluded = {
            str(item).strip()
            for item in (used_templates or [])
            if str(item).strip()
        }
        templates = [
            item
            for item in conflict_packet.get("active_templates", [])
            if isinstance(item, dict)
        ]
        if not templates:
            return {}

        signature_template_list = [
            str(item).strip()
            for item in actor_entry.get("signature_templates", [])
            if str(item).strip()
        ]
        signature_templates = set(signature_template_list)
        storylet_template_ids = {
            str(item).strip()
            for item in conflict_packet.get("storylet_template_ids", [])
            if str(item).strip()
        }
        profile = str(actor_entry.get("pressure_profile", "")).strip()
        public_lever = str(actor_entry.get("public_lever", "")).strip()

        def score(template: Dict[str, Any]) -> int:
            template_id = str(template.get("template_id", "")).strip()
            tags = {
                str(item).strip()
                for item in template.get("tags", [])
                if str(item).strip()
            }
            preferred_actors = {
                str(item).strip()
                for item in template.get("preferred_actors", [])
                if str(item).strip()
            }
            total = 0
            if template_id in excluded:
                total -= 200
            if template_id in signature_templates:
                total += 260
                total += max(0, 30 - signature_template_list.index(template_id) * 10)
            if template_id in storylet_template_ids:
                total += 100
            if actor in preferred_actors:
                total += 80
            if profile == "white_lotus" and tags.intersection({"white_lotus", "trap", "comparison"}):
                total += 70
            if profile == "order_control" and tags.intersection({"public_blame", "family_bias", "provocation"}):
                total += 70
            if profile == "polite_comparison" and tags.intersection({"comparison", "public", "bias"}):
                total += 70
            if profile == "cutoff_guard" and tags.intersection({"public_blame", "provocation", "family_bias"}):
                total += 70
            if profile == "shielding" and tags.intersection({"engagement_pressure", "bias", "public"}):
                total += 70
            if "顺序" in public_lever or "礼数" in public_lever:
                if tags.intersection({"comparison", "dinner", "public"}):
                    total += 20
            if "规矩" in public_lever or "长辈" in public_lever:
                if tags.intersection({"public_blame", "provocation"}):
                    total += 20
            if "解释" in public_lever or "旧情" in public_lever:
                if tags.intersection({"engagement_pressure", "bias"}):
                    total += 20
            return total

        ranked = sorted(templates, key=score, reverse=True)
        return ranked[0] if ranked and score(ranked[0]) > 0 else {}

    def _pick_conflict_actor(
        self,
        template: Dict[str, Any],
        conflict_packet: Dict[str, Any],
        input_payload: Dict[str, Any],
        exclude: Optional[List[str]] = None,
    ) -> str:
        reaction_context = input_payload.get("reaction_context", {})
        hostile_watchers = list(reaction_context.get("hostile_watchers", []) or [])
        visible_watchers = list(reaction_context.get("visible_watchers", []) or [])
        preferred_actors = list(template.get("preferred_actors", []) or []) if isinstance(template, dict) else []
        antagonist_names = list(conflict_packet.get("antagonist_names", []) or [])
        excluded = {str(item) for item in (exclude or []) if str(item).strip()}

        for pool in (preferred_actors, hostile_watchers, antagonist_names, visible_watchers):
            for actor in pool:
                actor = str(actor)
                if not actor or actor in excluded:
                    continue
                if actor in hostile_watchers or actor in visible_watchers or actor in antagonist_names or actor in preferred_actors:
                    return str(actor)
        return ""

    def _has_concrete_public_conflict(self, result: Dict[str, Any]) -> bool:
        for item in result.get("resolved_actions", []):
            if not isinstance(item, dict):
                continue
            if item.get("visibility") != "public":
                continue
            if item.get("outcome") in {"blocked", "complication"}:
                return True
            detail = str(item.get("result", "")).strip()
            if detail and not self._is_placeholder_result(detail):
                return True
        return False

    def _contains_touch_motif(self, text: str) -> bool:
        patterns = [
            r"搭在[^。！？]{0,10}(肩|手|手背|背上)",
            r"按在[^。！？]{0,10}(肩|手|手背|背上)",
            r"扶住",
            r"握住",
            r"覆上[^。！？]{0,10}(手|手背)",
            r"缩进[^。！？]{0,12}怀里",
            r"揽住",
            r"揽到",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def _touch_is_explicitly_supported(self, action: Dict[str, Any], input_payload: Dict[str, Any]) -> bool:
        actor = str(action.get("actor", "")).strip()
        intent = str(action.get("intent", "")).strip()
        text = f"{intent} {action.get('result', '')}"
        if any(token in text for token in ["扶", "拉住", "抱", "搂", "握住", "按住", "搭肩"]):
            return True
        for item in input_payload.get("intents", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("actor", "")).strip() != actor:
                continue
            intent_text = str(item.get("intent", "")).strip()
            if any(token in intent_text for token in ["扶", "拉住", "抱", "搂", "握住", "按住", "搭肩"]):
                return True
        return False

    def _soften_touch_result(self, actor: Any, detail: str) -> str:
        actor_name = str(actor or "某人").strip() or "某人"
        target_match = re.search(
            r"(?:搭在|按在|扶着|扶住|握住|覆上)([\u4e00-\u9fffA-Za-z0-9·]{2,12})",
            detail,
        )
        target = target_match.group(1) if target_match else ""

        if target:
            if any(token in detail for token in ["肩", "背"]):
                return f"已经站到了{target}那边，护短的意味几乎不加掩饰。"
            if any(token in detail for token in ["手", "手背"]):
                return f"动作明显偏向{target}，先把对方护住了。"
            return f"立场明显偏向{target}，先一步替对方撑住了场面。"

        if "怀里" in detail:
            return "一下子退到了更安全的那一边，把依附和偏心都摆到了明面上。"
        return f"{actor_name}的动作明显带着护短意味，立场已经偏得很清楚。"

    def _pick_template_result(
        self,
        template: Dict[str, Any],
        conflict_packet: Dict[str, Any],
        turn_offset: int = 0,
    ) -> str:
        variants = [
            str(item).strip()
            for item in template.get("fallback_results", [])
            if str(item).strip()
        ]
        single = str(template.get("fallback_result", "")).strip()
        if single:
            variants.append(single)
        if not variants:
            return ""

        visible_conflict_count = int(conflict_packet.get("visible_conflict_count", 0) or 0)
        current_step = int(conflict_packet.get("current_step", 0) or 0)
        index = (current_step * 2 + visible_conflict_count + turn_offset) % len(variants)
        return variants[index]

    def _conflict_rank(self, level: str) -> int:
        mapping = {
            "none": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        return mapping.get(str(level).lower(), 0)

    def _find_matching_action(
        self,
        actions: List[Dict[str, Any]],
        actor: Any,
        intent: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(actions, list):
            return None
        for item in actions:
            if not isinstance(item, dict):
                continue
            if item.get("actor") == actor and item.get("intent", "") == intent:
                return item
        for item in actions:
            if isinstance(item, dict) and item.get("actor") == actor:
                return item
        return None

    def _fallback_result(self, input_payload: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        result = self._empty_result()
        scene_state = self.entity.get_component("SceneState") if self.entity else None
        actor_updates: Dict[str, Dict[str, Any]] = {}
        resolved_actions = []
        for item in input_payload.get("intents", []):
            actor = item.get("actor", "Unknown")
            intent = item.get("intent", "")
            source = str(item.get("source", ""))
            is_player = bool(item.get("is_player"))
            visibility = "public" if is_player or item.get("location") == input_payload.get("player_pov", {}).get("location") else "hidden"
            if (not is_player) and self._is_passive_observe_intent(intent):
                continue

            if actor == "World" or source in {"timeline", "injected"}:
                world_result = self._summarize_world_intent(intent)
                if world_result:
                    resolved_actions.append(
                        {
                            "actor": actor,
                            "intent": intent,
                            "outcome": "complication" if source == "timeline" else "partial",
                            "location": item.get("location") or input_payload.get("player_pov", {}).get("location"),
                            "result": world_result,
                            "visibility": "public",
                        }
                    )
                continue

            move_target = self._extract_move_target(actor, intent, scene_state)
            if move_target:
                actor_updates.setdefault(actor, {})["location"] = move_target
                resolved_actions.append(
                    {
                        "actor": actor,
                        "intent": intent,
                        "outcome": "success",
                        "location": move_target,
                        "result": f"动身前往{move_target}。",
                        "visibility": visibility,
                    }
                )
                continue

            summary = self._summarize_fallback_intent(actor, intent, item, input_payload)
            if not summary and not is_player:
                continue

            outcome = "success" if is_player else "partial"
            if not is_player and any(token in summary for token in ["压了你一句", "替你圆场", "抢过了话头", "挡在了前面", "偏到你对面"]):
                outcome = "complication"
            resolved_actions.append(
                {
                    "actor": actor,
                    "intent": intent,
                    "outcome": outcome,
                    "location": item.get("location"),
                    "result": summary or "系统未完成结构化判定，暂按意图记录。",
                    "visibility": visibility,
                }
            )
        result["resolved_actions"] = resolved_actions
        if actor_updates:
            result["state_updates"]["actor_states"] = actor_updates
        result["storylet_hits"] = []
        result["simulation_notes"] = [note] if note else []
        result = self._enforce_legality(result, input_payload)
        result = self._enforce_conflict(result, input_payload)
        result = self._enforce_storylets(result, input_payload)
        return self._enforce_social_realism(result, input_payload)

    def _is_passive_observe_intent(self, intent: str) -> bool:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return True
        passive_patterns = [
            "先观察局势，避免贸然暴露自己。",
            "先观察局势",
            "先看情况",
            "暂时按兵不动",
        ]
        return any(pattern in normalized for pattern in passive_patterns)

    def _is_placeholder_result(self, result: str) -> bool:
        normalized = " ".join(str(result or "").split())
        return normalized == "系统未完成结构化判定，暂按意图记录。"

    def _summarize_world_intent(self, intent: str) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return ""
        if "请人入席" in normalized or "入席" in normalized or "家宴" in normalized:
            return "佣人已经开始请人入席，原本还能拖一拖的停顿一下子被收紧了。"
        if "书房" in normalized and "灯" in normalized:
            return "书房那边的灯迟迟没灭，这一晚显然还不会安静过去。"
        if "饭后" in normalized or "收束" in normalized:
            return "饭后的余波已经开始在屋子里扩散，显然有人还想把话接着说下去。"
        return normalized.rstrip("。") + "。"

    def _summarize_fallback_intent(
        self,
        actor: str,
        intent: str,
        item: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return ""

        if item.get("is_player"):
            if any(token in normalized for token in ["不欢迎", "现在就走", "什么意思", "定罪", "凭什么", "你是在", "怎么还", "为什么", "是不是", "你们都"]) or "？" in normalized or "?" in normalized:
                return "把话挑明了问了回去。"
            if any(token in normalized for token in ["不去", "不肯过去", "站着", "就站在这儿", "留在这儿"]):
                return "站在原地不肯顺着他们的节奏过去。"
            if any(token in normalized for token in ["坐", "入席", "用餐", "落座"]):
                return "先一步占了位置，等着看谁来挑你的刺。"
            if any(token in normalized for token in ["问", "打听", "追问"]):
                return "开口追问了一句。"
            if any(token in normalized for token in ["反驳", "拒绝", "顶回去", "不肯"]):
                return "当面把话顶了回去。"
            if "看" in normalized and any(token in normalized for token in ["不说", "沉默", "盯", "冷眼"]):
                return "只是站在原地看着对方，没有接话。"
            if "看" in normalized:
                return "把众人的反应和站位都看在了眼里。"
            if any(token in normalized for token in ["拿", "碰", "翻"]):
                return "伸手碰了碰眼前那样东西，摆明不打算只做旁观者。"
            return "按自己的意思做了动作。"

        conflict_style = str(input_payload.get("conflict", {}).get("surface_style", "implicit")).strip()
        if any(token in normalized for token in ["圆场", "委屈", "误会", "示弱"]):
            if conflict_style in {"barbed", "acid"}:
                return "笑着替你打圆场，字字句句却都在把你往不懂事的那边推。"
            return "带着体贴的口吻替你圆场，却把你放到了理亏的位置上。"
        if any(token in normalized for token in ["规矩", "懂事", "久等", "体面", "先坐", "别闹"]):
            if conflict_style in {"barbed", "acid"}:
                return "当众拿规矩压了你一句，明摆着是要你别再开口。"
            return "不轻不重地拿规矩压了你一句，摆明要你先低头。"
        if any(token in normalized for token in ["解释", "护着", "别误会", "别多想"]):
            if conflict_style in {"barbed", "acid"}:
                return "先一步替别人把话说满了，顺手把你的解释堵了回去。"
            return "先替别人接过了话头，把你的解释挡在了后面。"
        if any(token in normalized for token in ["提醒", "催", "安排"]):
            return "顺着场面的需要催了你一句，态度明显偏到你对面。"

        player_location = input_payload.get("player_pov", {}).get("location")
        if item.get("location") == player_location:
            return f"{actor}顺着眼前的局面接了一句，立场已经偏到你对面。"
        return ""

    def _extract_move_target(self, actor_name: str, intent: str, scene_state: Any) -> Optional[str]:
        if not scene_state or not intent or not actor_name:
            return None

        current_location = scene_state.get_actor_location(actor_name)
        connected_locations = []
        if current_location:
            connected_locations = scene_state.get_object_state(current_location).get("connected_to", [])
        return extract_move_target_from_intent(
            intent=intent,
            current_location=current_location,
            connected_locations=connected_locations,
            known_locations=scene_state.world_objects.keys(),
        )

    def _build_scene_context(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        """构建简化的场景上下文，合并 player_pov + spatial_layout + social"""
        player_pov = input_payload.get("player_pov", {})
        social = input_payload.get("social", {})

        # 提取关键场景信息
        location = player_pov.get("location")
        visible_actors = player_pov.get("visible_actors", [])
        visible_actor_states = player_pov.get("visible_actor_states", {})

        # 提取关系信息
        relations = {}
        for item in social.get("visible_relations", []):
            if isinstance(item, dict):
                actor = item.get("actor")
                if actor:
                    relations[actor] = {
                        "bias": item.get("bias"),
                        "framing_style": item.get("framing_style"),
                        "territorial": item.get("territorial"),
                        "toward_player": item.get("toward_viewer", {}),
                    }

        return {
            "location": location,
            "spatial_layout": player_pov.get("spatial_layout", {}),
            "visible_actors": visible_actors,
            "actor_states": visible_actor_states,
            "relations": relations,
            "allow_touch": social.get("allow_unsignaled_touch", False),
        }

    def _build_pressure_context(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        """构建简化的压力上下文，合并 storylet + conflict + motive"""
        storylet_pressure = input_payload.get("storylet_pressure", {})
        conflict = input_payload.get("conflict", {})
        motive_pressure = input_payload.get("motive_pressure", {})
        reaction_context = input_payload.get("reaction_context", {})

        # 提取优先 storylets
        priority_storylets = storylet_pressure.get("priority_storylets", [])[:3]

        # 提取冲突模板
        active_templates = conflict.get("active_templates", [])[:5]

        # 提取高压角色
        visible_pressures = motive_pressure.get("visible_pressures", [])[:3]

        # 提取反应要求
        requires_reaction = reaction_context.get("requires_reaction", False)
        hostile_watchers = reaction_context.get("hostile_watchers", [])

        return {
            "priority_storylets": priority_storylets,
            "forced_storylet_id": storylet_pressure.get("forced_storylet_id", ""),
            "require_storylet_hit": storylet_pressure.get("require_hit", False),
            "conflict_templates": active_templates,
            "require_visible_conflict": conflict.get("require_visible_conflict", False),
            "conflict_intensity": conflict.get("intensity", 0.0),
            "high_pressure_actors": visible_pressures,
            "requires_reaction": requires_reaction,
            "hostile_watchers": hostile_watchers,
            "player_action": input_payload.get("player_intent", {}).get("intent", ""),
        }
