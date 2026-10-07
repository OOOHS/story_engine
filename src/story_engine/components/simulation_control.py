import json
import re
from typing import Dict, Any, Optional, List
from pydantic import PrivateAttr, Field
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig


class SettlementRejected(RuntimeError):
    """A candidate may be corrected once using the same actor intents."""

    def __init__(self, issues: List[str], candidate: Dict[str, Any]):
        from copy import deepcopy
        self.issues = list(issues)
        self.candidate = deepcopy(candidate)
        super().__init__("Semantic settlement rejected: " + "; ".join(issues))


class SimulationControl(Component):
    """
    Resolves intents into structured consequences.
    This stage is not allowed to generate player-facing prose.
    """
    llm_config: Dict[str, Any] = Field(default_factory=dict)
    scenario: Optional[ScenarioConfig] = None
    # Production semantic resolution is fail-closed.  The deterministic rule
    # resolver is an explicit component (HostRuleSimulationControl), not an
    # implicit response to an LLM outage.
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def interpret_action(self, intent: str, actor: str, perception: Any):
        """Translate one committed choice into scheduling IR using actor-visible facts."""
        from src.story_engine.agents.actions import AgentAction
        payload = {
            "actor": actor, "intent": intent,
            "visible_context": {"world_view": perception.world_view, "self_state": perception.self_state,
                                "affordances": perception.affordance_opportunities,
                                "knowledge": perception.private_knowledge},
        }
        response = self._llm.generate(
            "## 行动语义解释\n"
            "解释主体已决定执行的自然语言行动，保留原意，不选择或改写行动，不结算结果。"
            "结合上下文区分说话内容、否定、假设、回忆与实际要做的事；复杂表达按本次主要行动解释。"
            "输出一个 JSON 对象：kind 为 observe/move/interact/communicate/wait，target 为主要目标或空字符串。"
            "引用可见实体时使用精确标识；语义不明确时保留空 target，允许结算阶段澄清。"
            "语言提及的实体无需绑定为动作目标。已知地图可用于识别远处目的地，未知探索保留原意。"
            "可选结构字段：affordance_id、claim_id、claim_stance、evidence_refs、delivery_recipient、"
            "route_source、route_target、route_path，仅在原意明确且上下文提供相应引用时填写。"
            "仅输出行动结构，任何实体或事实的创建均留在结算阶段。\n## 行动数据\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        data = self._parse_json_response(response.get("content", ""))
        if not isinstance(data, dict) or not isinstance(data.get("target"), str):
            raise RuntimeError("action semantic interpretation unavailable or malformed")
        action = AgentAction.from_value({**data, "detail": intent}, strict=True)
        return action

    def simulate(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.entity:
            return self._failure_result("SimulationControl has no attached entity.")

        scene_state = self.entity.get_component("SceneState")
        memory = self.entity.get_component("Memory")

        state_snapshot = (
            scene_state.get_semantic_snapshot()
            if scene_state and hasattr(scene_state, "get_semantic_snapshot")
            else scene_state.get_snapshot() if scene_state else {}
        )
        query = "\n".join([item.get("intent", "") for item in input_payload.get("intents", [])]).strip()

        relevant_memories: List[str] = []
        if memory and query:
            relevant_memories = memory.retrieve(query, n_results=3)

        # 构建简化的场景上下文（合并 player_pov + spatial_layout + social）
        scene_context = self._build_scene_context(input_payload)

        emergent_meter_budget = int(
            getattr(self.scenario, "emergent_meter_budget", 0) or 0
        )
        if emergent_meter_budget > 0:
            drive_creation_guidance = (
                "只有当现有 DriveState 中确实没有 need 能承载某个新出现、且会反复起效的持续压力时，"
                f"才输出 `drive_creations` 新建一个 need；每个角色本局最多创建 {emergent_meter_budget} 个，"
                "超出会被整批回滚。drift_per_turn 和 critical_threshold 直接给浮点数（宿主只做范围裁剪），"
                "不得填写初始 pressure；新建的 need 永远从 0 开始累积。"
                "创建必须由本轮已结算行动支持并给出 reason；能用已有 need 表达就不要新建。"
            )
        else:
            drive_creation_guidance = (
                "当前剧本未开放运行时创建新的持续压力条，不要输出 `drive_creations`"
                "（留空数组或省略）。"
            )

        prompt = f"""
你现在处于故事引擎的【Simulation】阶段。你的职责是做结构化结算，而不是写给玩家看的文本。

## 引擎核心规则（通用）
1. **只输出 JSON**：普通属性写入使用 `state_updates`；物品的创建、搬动、转交、收纳、开合、隐藏和销毁只能使用 `object_lifecycle`
2. **尊重当前状态**：依据空间图、能力、场景规则与当前事实判断实际结果；合法性 packet 的 advisory_only=true 时，其中的空间裁定仅作为普通步行与同场互动的参考；不补前情，不伪造玩家历史
3. **玩家意图是锚点**：玩家 proposal 可以失败、受阻或产生 complication，但不能被改写成另一个未经提出的意图
4. **受限视角**：异地事件只能以余波、传话、态度变化回流，不切全知镜头
5. **有效推进**：只依据当前事实形成清晰、可结算的变化；没有有效变化时诚实返回稳定、失败或受阻，不为追求节奏编造结果
6. **可观察事实**：使用可观察的行为和事实，不下文学化诊断结论
结算文本中的已发生事实与 state_updates/object_lifecycle 必须一致；持续后果落实到状态，瞬时事件由 resolved_actions 承载。角色的状态更新应对应本人的原始意图或其他已结算行动造成的被动后果。
Storylet 的成功事件必须填写实际发生地点 location；地点必须已存在或由本轮 world_additions.locations 确认。触发条件、玩家视点均不代表事件发生地；没有指定地点时依据事件内容与世界地图判断，无法判断则返回 blocked。
7. **角色选择属于角色主体**：resolved_actions 逐一结算本轮 intents 中的角色行动和 World 事件。故事块是宿主提供的未来剧情方向，必须按 source_storylet_id 分别解释。落实当前可成立的外部变化；需要角色自主选择的部分填写 director_suggestions，角色自行决定回应。角色本轮已经提交的选择照常结算。
8. **动作完成时结算**：本轮意图是离散事件队列中同时完成的一批原子动作。`action.kind` 只有 observe、move、interact、communicate、wait；自然语言 detail/target 说明具体语义。动作类型用于调度；你结合完整自然语言、媒介、身体状态、环境及同轮事件判断实际行为与结果
9. **主动与被动观察分离**：observe 是角色主动花费行动获取细节；可公开观察到的动作是其他角色的被动观察来源。主动观察发现的角色私有信息写入 private_result，不能塞进公开 result 泄漏给旁观者
10. **不替宿主掷骰**：规则和当前事实足以确定的行动直接写 resolved_actions；真正存在不确定性的物理或观察行动只能写 uncertain_outcomes，同时给出成功/失败两个候选事实分支。你只能选择固定 difficulty 和所需 capability，不能输出概率、随机数、数值 modifier，不能同时把该 actor 写进 resolved_actions
11. **临时 Modifier 不是万能状态**：只有本轮已提交行动确实让角色形成疲惫、专注、受伤后的谨慎等临时非社交行为影响时，才可从 `modifier_catalog` 选择 kind。物理事实仍写 SceneState，针对某人的感受仍写 social_impacts；持续时间、叠加、权重和到期由宿主决定
12. **客观事实与角色知识分离**：`claim_catalog` 是 GM 可用的客观命题目录，但角色通过实际感知发现可见且已连接的 evidence，或由知情角色通过已送达的 communicate 传播 Claim。WorldEvent 同样只能由直接或自身见证者使用真实 event_id 转述；宿主从事件实体读取原始 statement，不能借 event_id 改写事件内容。不能把 truth_status、宿主条件、未发现的证据或未获知事件写进角色知识
13. **结构性写权由宿主管理，数值幅度由你直接给**：不要输出 relationship_updates；长期关系轨道由宿主固定映射沉淀。宿主在事务提交后把可感知事件发送给见证角色。但短期社会反应（social_impacts/modifier_updates 的 magnitude）、Drive 变化（drive_updates 的 delta）、新压力条的漂移与临界值（drive_creations 的 drift_per_turn/critical_threshold）、以及场景张力（tension_delta），都由你按情境给出具体浮点数；宿主只做范围裁剪，不重新定档
14. **交流的送达由你结算**：结合表达方式、距离、媒介、身体状态与同轮打断判断谁实际听到或收到。明确输出 recipients（实际接收角色的标识列表）；失败或无人接收时为空。成功不代表听者相信或响应。耳语与电话使用 hidden 保护第三方视角，接收者仍获得这条有来源的消息。普通当面讲话也填写实际听者；语言中的物理事实保持待定。
15. **按结算依赖补全世界**：当本轮外部结果依赖尚未确定的事实时，确定本轮所需的最小部分。
    人物言论、谣言、幻想和许诺保留说话人及原话；其中提到的人、物、地点及关系保持待定。
    外部行动的结果需要确定其存在或性质时，由你结合初始设定、权威状态、已提交事实和本轮意图判断。
    判断依据是结果对事实的依赖，不按动作关键词或动作种类枚举。语言内容的物理真伪可以继续待定。
    已确认不存在、已经揭穿的欺诈等事实约束后续补全；重复传播与相信承载在语言/角色上下文中。
    补全可以确认存在、确认不存在或继续待定；只有实际需要的实体进入注册。
    在 world_additions 的每个新地点/人物上填写 actor 和 reason，说明哪条本轮外部结算需要它。
    新人物拥有独立主体，只补充必要出生背景与状态；其下一步行动由自己的 Agent 决定。
    持续的存在性、关系和否定结论若无法由实体属性完整表达，写入 world_additions.facts；
    宿主将这些已确认陈述与提交 step 追加到私有 established_facts，后续结算及存档继续保留。
    带 step 的事实记录描述当时已经确认的结果；后续有真实因果依据的变化在新结算中处理。
    私有事实通过本人的 private_result 或可感知事件交付，角色的言论和作者提示维持来源边界。

## 剧本设定
**剧本**：{self.scenario.name if self.scenario else "通用剧本"}
**初始局面**：{self.scenario.initial_state if self.scenario else ""}
**作者原始设定（提案线索；当前 ECS 状态、Claim 目录和已提交事件才是结算依据）**：
{self.scenario.private_author_premise if self.scenario else ""}

## 世界物理约束（按语义判断实际行为与能力；区分语言、假设、否定和物理结果）
{self.scenario.physics_profile if self.scenario else "mundane"}
{json.dumps([rule.model_dump() for rule in self.scenario.physics_rules] if self.scenario else [], ensure_ascii=False)}

## 场景特定规则
{json.dumps(self.scenario.rules if self.scenario else [], ensure_ascii=False, indent=2)}

## 当前状态
{json.dumps(state_snapshot, ensure_ascii=False, indent=2)}

## 场景上下文
{json.dumps(scene_context, ensure_ascii=False, indent=2)}

## 本轮意图
{json.dumps(input_payload.get("intents", []), ensure_ascii=False, indent=2)}

## 宿主允许的临时 Modifier
{json.dumps(input_payload.get("modifier_catalog", []), ensure_ascii=False, indent=2)}

## GM 可用的客观 Claim 目录
{json.dumps(input_payload.get("claim_catalog", []), ensure_ascii=False, indent=2)}

## 本轮宿主签发的角色入口授权
{json.dumps(input_payload.get("character_entry_authorizations", []), ensure_ascii=False, indent=2)}

## 本轮宿主签发的剧情点注册授权
{json.dumps(input_payload.get("storylet_definition_authorizations", []), ensure_ascii=False, indent=2)}

## 本轮宿主签发的空间图新增授权
{json.dumps(input_payload.get("topology_candidate_authorizations", []), ensure_ascii=False, indent=2)}

## 本轮已触发故事块
宿主只检查显式 conditions；自然语言 trigger 由你结合结算前的权威世界与已确认事实判断。每条都返回一条带精确 source_storylet_id 的 resolved_action。trigger 尚未成立用 inactive；条件成立但需要等待角色选择用 deferred，并可输出 director_suggestions。inactive/deferred 使用 hidden 可见性与空 result，保持事实与建议分离。continuation=true 表示已启动故事块的独立追踪 Agent 提交了新的一步，直接根据本轮 intent 结算这一步；storylet_direction 是整体剧情方向，初始 trigger 已经满足，后续步骤可以继续发生。already_triggered=true 且 continuation=false 表示可复用事件本次条件期间已生效，条件继续成立用 deferred 且避免重复建议，条件失效用 inactive 以便未来重新触发。实际外部变化填写 success/partial/complication、结果及地点。剧情方向可调整其实现方式，矛盾的具体后果保持 blocked。
{json.dumps(input_payload.get("storylet_triggers", []), ensure_ascii=False, indent=2)}

## 当前冲突节奏压力（仅供参考，不是事实也不是指令）
下面这条压力信号只描述场上是否已经有敌意观察者在场、场景是否已经安静太久，供你判断某个已经
proposal 的角色的行动是否应当升级为冲突（比如把一次 observe 结算成撞见对峙）。它不能让你替任何
未 proposal 的角色发起冲突，也不能凭空生成一个新角色或新事件。若为空对象，代表当前没有可参考的节奏信号。
{json.dumps(input_payload.get("conflict_pressure", {}), ensure_ascii=False, indent=2)}

## 行动角色的私有驱动力
{json.dumps(input_payload.get("drive_context", {}), ensure_ascii=False, indent=2)}

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
      "action_kind": "observe | move | interact | communicate | wait",
      "action_target": "主要目标",
      "outcome": "success | partial | failure | blocked | complication | inactive | deferred",
      "location": "动作发生地点",
      "result": "内部结果摘要",
      "private_result": "仅 actor 本人获得的信息或身体感受；没有则为空字符串",
      "recipients": ["communicate 实际接收者；其他行动省略此字段"],
      "visibility": "public | local | hidden",
      "source_storylet_id": "World 结算【本轮已触发故事块】时填写对应 storylet_id；其他行动留空字符串"
    }}
  ],
  "uncertain_outcomes": [
    {{
      "check_id": "本轮稳定且唯一的检查 id",
      "actor": "实际提交该行动的角色",
      "check_kind": "world | observation",
      "difficulty": "trivial | easy | normal | hard | extreme | impossible",
      "required_capability": "可选，必须来自权威角色 capability/skill 名称",
      "success": {{
        "resolved_action": {{"outcome":"success","result":"成功分支事实","visibility":"local"}},
        "topology_changes": [{{"operation":"connect | disconnect","source":"地点标识","target":"地点标识","bidirectional":true,"visibility":"local | public | hidden","reason":"本轮已结算事件造成通路变化的原因"}}],
        "state_updates": {{"scene":{{}},"world_objects":{{}},"actor_states":{{}}}},
        "object_lifecycle": []
      }},
      "failure": {{
        "resolved_action": {{"outcome":"fail","result":"失败分支事实","visibility":"local"}},
        "state_updates": {{"scene":{{}},"world_objects":{{}},"actor_states":{{}}}},
        "object_lifecycle": []
      }}
    }}
  ],
  "topology_changes": [{{"operation":"connect | disconnect","source":"地点标识","target":"地点标识","bidirectional":true,"visibility":"local | public | hidden","reason":"本轮已结算事件造成通路变化的原因"}}],
  "state_updates": {{
    "scene": {{}},
    "world_objects": {{}},
    "actor_states": {{}}
  }},
  "conflict_level": "none | low | medium | high",
  "conflict_flags": ["public", "deception"],
  "applied_conflict_templates": ["若本轮某个已提交行动确实呼应了【当前冲突节奏压力】里某条 active_templates 的 instruction，填它的 template_id；否则留空数组"],
  "social_impacts": [
    {{
      "source": "产生可观察社会影响的行动者",
      "affected": "亲自观察到该行动并形成感受的角色",
      "kind": "grateful | admiring | hurt | angry | afraid | suspicious | betrayed | relieved",
      "magnitude": "0.0~1.0 之间的浮点数，感受强度",
      "reason": "哪条已提交行动为何形成这种短期感受",
      "source_event": "可选的稳定事件引用"
    }}
  ],
  "modifier_updates": [
    {{
      "operation": "apply | remove",
      "target": "受到临时影响的角色",
      "source": "产生该影响的本轮行动者，或 World",
      "kind": "只能来自 modifier_catalog",
      "magnitude": "0.0~1.0 之间的浮点数，影响强度",
      "reason": "哪条已结算行动为何形成或解除该影响",
      "source_event": "可选的稳定事件引用"
    }}
  ],
  "claim_discoveries": [
    {{
      "actor": "本轮行动确实获得该证据的角色",
      "claim_id": "claim_catalog 中的命题 id",
      "evidence_ref": "该 Claim 已连接且对 actor 可见的世界对象",
      "reason": "主动观察如何发现这项证据"
    }}
  ],
  "knowledge_updates": [
    {{
      "source": "本轮确实传递信息的角色",
      "target": "已送达的接收角色",
      "statement": "legacy 自由文本传递，或与 claim_id 对应的表述",
      "claim_id": "可选；发送者此前确实知道的 Claim",
      "event_id": "可选；发送者亲历或此前获知的 WorldEvent id，内容由宿主实体确定",
      "response_kind": "event_id 可选：report | explain | apologize | accuse | request | forgive | acknowledge",
      "asserted_stance": "supports | rejects | uncertain；允许知情角色撒谎",
      "cited_evidence": ["可选；发送者确实知道且能够当场出示的关联对象"],
      "confidence": "仅 legacy 自由文本传递可填写；有 claim_id 时由宿主计算",
      "mode": "told",
      "reason": "哪条已结算行动完成了传递"
    }}
  ],
  "object_lifecycle": [
    {{
      "operation": "spawn | relocate | set_visibility | set_container_state | use | destroy",
      "object_id": "稳定且唯一的对象名",
      "actor": "实际完成该动作的角色名，或 World",
      "reason": "必须由该 actor 本轮已结算的成功、部分成功或 complication 行动支持；actor=World 时可引用【当前叙事机会】里的 storylet_id 作为依据",
      "object_kind": "item | clue | document | weapon | resource",
      "affordance_id": "use 操作必须填写对象已有的 affordance id",
      "owner": null,
      "location": "已有地点，owner、location 与 container 必须且只能填写一个",
      "container": "已有且内容预定义为容器的有形对象",
      "sub_location": null,
      "open": true,
      "hidden": false,
      "portable": true,
      "properties": {{}},
      "source_storylet_id": "可选；如果这条操作是在实现【当前叙事机会】里的某个 storylet，填它的 storylet_id，否则留空字符串"
    }}
  ],
  "exchanges": [
    {{
      "exchange_id": "本轮稳定且唯一的交换 id",
      "parties": ["甲", "乙"],
      "accepted_by": ["甲", "乙"],
      "transfers": [
        {{
          "from": "甲",
          "to": "乙",
          "object_id": "甲当前真实拥有且已向乙公开的有形对象",
          "quantity": 1
        }}
      ],
      "reason": "双方哪两条本轮行动明确达成了交换"
    }}
  ],
  "drive_updates": [
    {{
      "actor": "需求受到影响的角色",
      "source": "产生该后果的已结算行动 actor",
      "need": "该角色 DriveState 中已有的 need 名称",
      "delta": "-0.4~0.4 之间的浮点数，正数加剧、负数缓解",
      "reason": "哪条本轮事实让压力上升或缓解"
    }}
  ],
  "drive_creations": [
    {{
      "actor": "需要新压力条的角色",
      "need": "新 need 的名称，不能与该角色已有 need 重名",
      "drift_per_turn": "0.0~0.08 之间的浮点数，每步自然上升量",
      "critical_threshold": "0.5~0.95 之间的浮点数，超过此比例视为危急",
      "description": "这个持续压力代表什么",
      "reason": "哪条本轮事实催生了这个全新的持续压力"
    }}
  ],
  "spawn_character": null,
  "storylet_definition": null,
  "topology_candidate": null,
  "world_additions": {{
    "locations": [{{"location_id": "本轮必须确认的新地点", "connects_to": ["已有或本轮新增地点"], "properties": {{"description": "最小地点事实"}}, "actor": "本轮意图的主体或World", "reason": "哪项外部结果依赖这个地点"}}],
    "characters": [{{"name": "本轮必须确认的新人物", "role": "必要背景", "location": "已有或本轮新增地点", "personality": "必要初始性格", "goals": [], "initial_state": {{}}, "actor": "本轮意图的主体或World", "reason": "哪项外部结果依赖这个人物"}}],
    "facts": ["本轮必须确定、且实体属性尚未承载的客观结论"]
  }},
  "director_suggestions": [{{"source_storylet_id": "本轮故事块标识", "recipient": "已有或本轮出生的角色标识", "text": "角色可以自行考虑的剧情建议"}}],
  "simulation_notes": ["供渲染阶段参考的事实备注"]
}}

`spawn_character` 只有在“本轮宿主签发的角色入口授权”中存在可用记录时才能填写。必须引用其中精确的 `authorization_id`；name、role、location、initial_state、runtime、初始秘密和权威需求均以授权为准，不能改写。只有授权的 profile_mode=semantic 时，才可以补充 personality 和自然语言 goals。没有授权时必须保持 null；普通叙述提到陌生人不等于世界中已经出生了一个角色。

`storylet_definition` 只有在“本轮宿主签发的剧情点注册授权”中存在可用记录时才能填写，且必须引用其中精确的 `authorization_id`；storylet_id、intent、location、trigger、conditions、priority、one_shot、tags、situation_kinds、situation_tags 均以授权为准，不能改写。没有授权时必须保持 null；这是往剧本里永久新增一个可被将来命中的叙事机会点，不是记录已经发生的事。

`topology_candidate` 只有在“本轮宿主签发的空间图新增授权”中存在可用记录时才能填写，且必须引用其中精确的 `authorization_id`；location_id、connects_to、visibility、reason 均以授权为准，不能改写。没有授权时必须保持 null；这是往世界地图里永久新增一个地点节点及其初始通路，与本轮临时的场景描述无关，也不能用来断开或改写已有地点之间的连接（那属于宿主步前专属权限）。

world_additions 是正常语义结算中的最小世界补充，无需宿主预先签发具体实体授权；没有外部结果依赖时三个列表均为空。locations 和 characters 可以同批引用，物品继续使用 object_lifecycle.spawn，物品的 owner/location 可以引用同批新人物/地点。普通 state_updates 修改已有或同批新人物/地点的描述状态，物品出生属性放在 spawn.properties。新增人物与地点的身份引用、容量和位置由宿主检查，存在必要性与事实一致性由提交前语义校对判断。既有授权字段仅用于作者显式的注入；沿用它们时保留授权固定内容。Storylet 定义由导演提出并通过结构登记，结算解释本轮故事块的条件与具体落实。导演建议保持可拒绝的提议语气，信息以接收者当前可知内容和本轮可感知事实为限；作者秘密与未来预定结果留在宿主上下文。建议只通过 director_suggestions 输出，避免写入事实、角色动机、知识或已发生行动。
不确定结果所依赖的 world_additions 放在对应 success/failure 分支内，宿主只合并选中的分支；两边均已成立的事实才放在顶层。

`state_updates.world_objects` 只能修改已有对象的普通描述属性，不能创建对象，也不能直接写 owner、location、container、sub_location、hidden、portable、kind、is_location、quantity、stack_key、affordances、is_container、container_capacity、container_size、container_open 或 container_opaque。空间连接变化通过 topology_changes 的 connect/disconnect 提交，支持本轮新地点；图结构字段保持专用通道以保证引用一致性。`spawn` 与 `relocate` 必须且只能填写 owner/location/container 之一；container 必须是已存在或本轮创建、容量足够且当前可访问的容器，不能把对象放入自身或形成嵌套循环。动态 spawn 可在 properties 中定义 affordances、stack_key、quantity 和容器能力；按世界事实给出类型正确的属性，其存在必要性和物理一致性一起接受语义校对。`set_visibility` 只需要 hidden；`set_container_state` 只用于已有容器并填写 open；关闭且不透明的容器会遮蔽内容，关闭的容器内容即使透明可见也不能直接操作。`use` 必须填写对象状态中真实存在的 affordance_id，其 consumes、exclusive、requires_owner、requires_capabilities 和 need_effects 均以当前对象状态中的能力定义为准；动态出生对象的能力随物品共同校对；缺少能力或所有权时不能声称使用成功；非空容器不能被直接销毁或消耗。`destroy` 不填写放置字段。对象生命周期操作必须引用本轮同一 actor 的已结算行动；角色必须与对象和目标 owner/location/container 的有效地点物理同场。移动外层容器会自然携带全部嵌套内容，不要逐项伪造 relocate。多个角色同轮争夺有限或独占对象时，引擎会按 simultaneous proposal 语义统一仲裁，不要依靠 object_lifecycle 数组顺序暗示赢家。不要通过对象生命周期创建新地点或修改空间图。

`exchanges` 用于双方明确同意的物品、货币或资源交换。每个 exchange 只允许两个已有角色，双方必须同场、本轮都真实提交 proposal，并分别拥有非 hidden 的正向 resolved action；`accepted_by` 必须与 parties 完全一致。每条 transfer 的 from 必须真实拥有 object_id，物品必须 portable 且已向对方公开；同一对象不能同时出现在 object_lifecycle。quantity 缺省为整件/全部堆栈，部分数量转移要求内容预定义不可由模型改写的 stack_key，引擎会确定性拆分或合并堆栈。所有 transfer、对象所有权与数量变化在一个 WorldStateTransaction 中原子提交；数量不足、双花、异地接受、隐藏物品或任一后续写入非法都会整批回滚。

`uncertain_outcomes` 只用于当前规则无法确定成功与否的尝试。`world` 用于物理或环境结果，`observation` 只允许对应 actor 本轮提交 observe。difficulty 只能使用模板枚举；required_capability 只是对权威 actor capability/skill 名称的引用，宿主会自行计算有限修正。success/failure 都必须包含一个 resolved_action，并把该分支才会发生的 Scene、对象、社会影响、Modifier、知识或 Drive 变化放在同一分支中；不要在顶层重复这些变化。分支中的位移和拓扑变化遵循实际因果；主动选择属于原始行动者，外力可以造成其他角色的被动位移。宿主暂存选中的完整分支后统一检查地点引用、事实一致性及角色自主权。分支同样不能直接写 relationship_updates 或 tension_delta；分支内的 social_impacts/drive_updates 遵循与顶层相同的数值规则。宿主选择分支后才会把它合并进权威事务，未选分支永远不能进入 Rendering 或 Memory。

`knowledge_updates.event_id` 的规范 statement 由宿主 Event Entity 决定。可选 `response_kind` 只描述本轮真实 communicate 对该事件采取的社会行为：report、explain、apologize、accuse、request、forgive 或 acknowledge；无效值会退化为普通 report。它只形成可审计的 Event response，不代表接收者相信、接受道歉、承认指控或改变关系。

即时羞辱、帮助、威胁、欺骗嫌疑或被救助带来的主观反应写 `social_impacts`。source 必须有一条 affected 在同地点亲自可观察的非 hidden 已结算行动；kind 只能使用模板枚举，magnitude 直接给 0.0~1.0 的浮点数（宿主只做范围裁剪），不能附带 policy weight、概率、关系 delta 或持续时间。这只是你替 affected 给出的默认猜测：affected 如果有自己的角色 agent，她本人这一轮如果已经自己报告了对 source 的感受，宿主会直接采用她自己的账本，丢弃你在这里的猜测，不会重复叠加。宿主依据 Sentiment 定义用这个数创建感受、决定衰减和行动效用，并让其中一小部分按固定函数沉淀到长期 Relationship Track。Event response 与 Sentiment 分离：例如"甲道歉"是客观社会行为，"乙感到 relieved 或仍然 angry"才是乙的私有评价。

只有本轮已结算行动确实改变了某个角色的持续压力时才输出 `drive_updates`。delta 直接给 -0.4~0.4 之间的浮点数（正数加剧、负数缓解，宿主只做范围裁剪）；need 必须已经存在于该角色 DriveState。source 不是 actor 本人时，对应行动必须在 actor 所在地可观察，异地或 hidden 行动不能隔空改变对方压力。对象 affordance 已自动产生的 need_effect 不要在 drive_updates 中重复计算。

{drive_creation_guidance}

只输出 JSON，不要输出解释，不要使用 Markdown。
"""

        feedback = input_payload.get("settlement_feedback")
        if feedback:
            prompt += "\n## 上次候选结算的提交校对反馈\n" + json.dumps(feedback, ensure_ascii=False)
            prompt += "\n请修正这份完整结算，保持本轮角色原始意图。被拒绝的候选事实尚未发生。"
        response = self._llm.generate(prompt)
        content = response.get("content", "")
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return self._failure_result("结构化模拟服务不可用，权威结算已暂停。")
        parsed = self._parse_json_response(content)
        if parsed is None:
            return self._failure_result("结构化模拟输出解析失败，权威结算已暂停。")
        return self._normalize_result(parsed, input_payload)

    def validate_commit(self, before: Any, after: Any,
                        result: Dict[str, Any], input_payload: Dict[str, Any]) -> None:
        """The settlement model checks actual staged facts before publication.

        One semantic check covers text/state coherence and subject autonomy.
        Physical causality is judged by the model; the host owns the commit gate.
        """
        payload = {
            "before": before.get_semantic_snapshot(),
            "after": after.get_semantic_snapshot(),
            "intents": input_payload.get("intents", []),
            "storylet_triggers": input_payload.get("storylet_triggers", []),
            "legality_context": input_payload.get("legality", {}),
            "candidate": result,
            "rules": self.scenario.rules if self.scenario else [],
            "initial_state": self.scenario.initial_state if self.scenario else "",
            "author_premise": self.scenario.private_author_premise if self.scenario else "",
            "physics_profile": self.scenario.physics_profile if self.scenario else "mundane",
            "physics_rules": [rule.model_dump() for rule in self.scenario.physics_rules] if self.scenario else [],
            "claim_catalog": input_payload.get("claim_catalog", []),
            "authorizations": {
                key: input_payload.get(key, []) for key in (
                    "character_entry_authorizations", "storylet_definition_authorizations",
                    "topology_candidate_authorizations")
            },
        }
        prompt = """## 提交前语义校对
你是本轮结算模型，当前候选世界已由宿主暂存，事实尚待提交。
仅判断该完整结算的语义一致性与角色自主权，输出 {"valid":true,"issues":[]}；
发现问题时输出 {"valid":false,"issues":["指明具体文字、状态、行为来源及需要修正的原因"]}。
1. 将每条 result/private_result/exchange/scene 描述中的已发生事实与 after 对照。
   解锁、打开、损坏、获得、失去、改变身体状态等持续后果应落实为对应状态。
   已满足的事实可以保持原值；天气、钟声等瞬时事件可以仅由已结算事件承载。
   动作失败时仍可有确实发生的被动后果。检查所有字段的语义，不依赖字段名称。
2. 角色的选择、行为、计划、动机与自主回应由该角色提交的原始 intent 决定。
   检查所有 actor_states 属性、result、场景文字和其他输出中的角色行为。
   角色即使提交了等待或观察，也只授权结算该意图；追赶、举刀威胁等新选择需要本人的意图。
   World 事件及他人的行动可以造成受伤、摔倒、被推移等被动物理后果，
   可以影响尚未行动的角色。根据已提交意图与世界事实判断其因果关系。
   角色主观的信任、接受道歉、选择立场等回应同样属于角色主体。
   新角色的最小出生背景和状态作为注册事实处理，后续选择由新主体决定。
   director_suggestions 是有导演来源的可选提议，检查其内容对接收者的知识边界；角色可以接受、改写或拒绝。建议与故事块中的期望行为保持未来语气，已发生行动仍需本人的原始 intent。
   continuation=true 的故事块已经启动，允许更换事件地点及跨越原始启动条件；其余故事块对照 before 判断 trigger 是否成立；inactive/deferred 的剧情方向尚未作为事件发生。
3. after 是宿主实际会提交的世界。候选输出中被剥离或未落实的状态不能当作已发生事实。
   判断中只依据 before、原始 intents、候选结算、after 和有效授权，保持明确的事实边界。
4. 检查交流的 recipients：原始表达、媒介、距离与状态是否支持实际送达？未送达内容保持待定；耳语、电话的接收者可跨地点获得消息，第三方可见性按实际感知判断。Claim 的传播只记录收到的说法与证据，个人立场和相信程度由角色 Agent 决定。
5. 检查每项新增实体和新增 established_facts：本轮哪项外部结果确实依赖它？
   言论的送达只能确认这次表达，原话中的人物、物品、地点、关系及真实性可以继续待定。
   包括物品 object_lifecycle.spawn 在内，拒绝仅因言论/谣言/幻想就把内容变成客观事实。
   检查结果里的引用与转述，保留来源，逐一识别其中宣称的事实是否已经确认。
   topology_changes、动态物品容器与 affordances 均检查是否由本轮结算支持、是否符合世界物理与事实；主动及被动位移使用完整因果校对。
   外部结果需要未知事实时允许必要的最小补全；同时遵守初始设定、作者固定事实、
   Claim 目录、before 中实体属性及 established_facts。已确认的欺诈和不存在结论继续生效。
   established_facts 的 step 标记历史确认时点，允许后续原始行动造成真实变化并提交新的结论。
   单次投递等动作若只依赖过程，就保留尚未需要确定的收件人等事实。
   facts 仅存客观结论；个人信念、承诺和未知命题继续保留在带来源的语言上下文中。
校对只返回判定与问题。修正由同一结算接口根据反馈完成。
""" + "\n## 候选提交数据\n" + json.dumps(payload, ensure_ascii=False)
        response = self._llm.generate(prompt)
        verdict = self._parse_json_response(response.get("content", ""))
        if (not isinstance(verdict, dict) or type(verdict.get("valid")) is not bool
                or not isinstance(verdict.get("issues"), list)
                or any(not isinstance(issue, str) for issue in verdict["issues"])):
            raise RuntimeError("Semantic settlement check unavailable or malformed")
        if verdict["valid"] is True and not verdict["issues"]:
            return
        issues = [issue[:800] for issue in verdict["issues"][:12] if issue.strip()]
        raise SettlementRejected(issues or ["semantic check rejected the candidate"], result)

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
            origin = self._action_origin(item, input_payload)
            action_kind = origin.get("action_kind") or item.get("action_kind", "interact")
            action_target = origin.get("action_target") or item.get("action_target", "")
            resolved_actions.append(
                {
                    "actor": item.get("actor", "Unknown"),
                    "intent": origin.get("intent") or item.get("intent", ""),
                    "action_kind": action_kind,
                    "action_target": action_target,
                    "outcome": item.get("outcome", "partial"),
                    "location": (item.get("location") or origin.get("location") or ("" if origin.get("source_storylet_id") else self._infer_location(item.get("actor"), input_payload))),
                    "result": item.get("result", ""),
                    "private_result": (
                        " ".join(str(item.get("private_result", "")).split())[:1200]
                        if item.get("private_result")
                        else ""
                    ),
                    "visibility": item.get("visibility", "public"),
                    **({"recipients": item["recipients"]} if "recipients" in item else {}),
                    "source_storylet_id": str(
                        origin.get("source_storylet_id", item.get("source_storylet_id", ""))
                    ).strip(),
                }
            )
        for action in resolved_actions:
            if action.get("source_storylet_id") and action["outcome"] in {"inactive", "deferred"}:
                action.update({"visibility": "hidden", "result": "", "private_result": ""})
        uncertain_outcomes = data.get("uncertain_outcomes", [])
        if not isinstance(uncertain_outcomes, list):
            uncertain_outcomes = []
        result["uncertain_outcomes"] = [
            item for item in uncertain_outcomes if isinstance(item, dict)
        ]
        result["resolved_actions"] = resolved_actions

        state_updates = data.get("state_updates", {})
        if not isinstance(state_updates, dict):
            state_updates = {}
        result["state_updates"] = state_updates

        # Storylet realization is detected by the host after semantic and
        # stochastic resolution; a model cannot claim narrative progress.
        result["storylet_hits"] = []

        conflict_level = str(data.get("conflict_level", "none")).strip().lower()
        if conflict_level not in {"none", "low", "medium", "high"}:
            conflict_level = "none"
        result["conflict_level"] = conflict_level

        conflict_flags = data.get("conflict_flags", [])
        if not isinstance(conflict_flags, list):
            conflict_flags = [str(conflict_flags)]
        result["conflict_flags"] = [str(item).strip() for item in conflict_flags if str(item).strip()]

        applied_conflict_templates = data.get("applied_conflict_templates", [])
        if not isinstance(applied_conflict_templates, list):
            applied_conflict_templates = [str(applied_conflict_templates)]
        result["applied_conflict_templates"] = [
            str(item).strip() for item in applied_conflict_templates if str(item).strip()
        ]

        try:
            result["tension_delta"] = float(data.get("tension_delta", 0.0))
        except (TypeError, ValueError):
            result["tension_delta"] = 0.0

        # Long-term relationship tracks belong to host systems.
        result["relationship_updates"] = []

        social_impacts = data.get("social_impacts", [])
        if not isinstance(social_impacts, list):
            social_impacts = []
        result["social_impacts"] = [
            item for item in social_impacts if isinstance(item, dict)
        ]

        modifier_updates = data.get("modifier_updates", [])
        if not isinstance(modifier_updates, list):
            modifier_updates = []
        result["modifier_updates"] = [
            item for item in modifier_updates if isinstance(item, dict)
        ]

        knowledge_updates = data.get("knowledge_updates", [])
        if not isinstance(knowledge_updates, list):
            knowledge_updates = []
        result["knowledge_updates"] = [
            item for item in knowledge_updates if isinstance(item, dict)
        ]

        claim_discoveries = data.get("claim_discoveries", [])
        if not isinstance(claim_discoveries, list):
            claim_discoveries = []
        result["claim_discoveries"] = [
            item for item in claim_discoveries if isinstance(item, dict)
        ]

        object_lifecycle = data.get("object_lifecycle", [])
        if not isinstance(object_lifecycle, list):
            object_lifecycle = []
        result["object_lifecycle"] = [
            item for item in object_lifecycle if isinstance(item, dict)
        ]

        exchanges = data.get("exchanges", [])
        if not isinstance(exchanges, list):
            exchanges = []
        result["exchanges"] = [
            item for item in exchanges if isinstance(item, dict)
        ]

        drive_updates = data.get("drive_updates", [])
        if not isinstance(drive_updates, list):
            drive_updates = []
        result["drive_updates"] = [
            item for item in drive_updates if isinstance(item, dict)
        ]

        drive_creations = data.get("drive_creations", [])
        if not isinstance(drive_creations, list):
            drive_creations = []
        result["drive_creations"] = [
            item for item in drive_creations if isinstance(item, dict)
        ]

        # World facts and attributed optional suggestions use separate channels.
        result["world_additions"] = data.get("world_additions", {})

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

        storylet_definition = data.get("storylet_definition")
        if isinstance(storylet_definition, dict) and storylet_definition.get(
            "authorization_id"
        ):
            result["storylet_definition"] = storylet_definition

        topology_candidate = data.get("topology_candidate")
        if isinstance(topology_candidate, dict) and topology_candidate.get(
            "authorization_id"
        ):
            result["topology_candidate"] = topology_candidate

        notes = data.get("simulation_notes", [])
        if not isinstance(notes, list):
            notes = [str(notes)]
        result["simulation_notes"] = [str(item) for item in notes if str(item).strip()]
        self._ensure_intent_coverage(result, input_payload)
        return result

    def _ensure_intent_coverage(
        self,
        result: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> None:
        """Prevent a semantic resolver from silently dropping an Agent action."""
        covered_actors = {
            str(item.get("actor", "")).strip()
            for item in result.get("resolved_actions", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        covered_actors.update(
            str(item.get("actor", "")).strip()
            for item in result.get("uncertain_outcomes", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        )
        missing_intents = [
            item
            for item in input_payload.get("intents", [])
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("actor", "")).strip() != "World"
            and str(item.get("source", "")).strip() not in {"injected"}
            and str(item.get("actor", "")).strip() not in covered_actors
        ]
        if not missing_intents:
            return

        missing_actors = {
            str(item.get("actor", "")).strip() for item in missing_intents
        }
        # ``result["simulation_error"]`` is always present (as ``None``) from
        # ``_empty_result()``, so ``setdefault`` here would be a silent no-op.
        # Coverage gaps must win over a prior "no error" baseline.
        if result.get("simulation_error") is None:
            result["simulation_error"] = {
                "kind": "unresolved_intents",
                "actors": sorted(missing_actors),
                "message": "语义结算未覆盖全部主体意图，权威步骤应重试而非合成行动。",
            }

    def _infer_location(self, actor_name: Optional[str], input_payload: Dict[str, Any]) -> Optional[str]:
        if not actor_name:
            return None
        for item in input_payload.get("intents", []):
            if item.get("actor") == actor_name:
                return item.get("location")
        return input_payload.get("player_pov", {}).get("location")

    def _action_origin(self, action: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        candidates = [item for item in payload.get("intents", [])
                      if isinstance(item, dict) and item.get("actor") == action.get("actor")]
        if action.get("actor") != "World":
            return candidates[0] if candidates else {}
        storylet_id = str(action.get("source_storylet_id", "")).strip()
        if storylet_id:
            matches = [item for item in candidates if item.get("source_storylet_id") == storylet_id]
        else:
            matches = [item for item in candidates if item.get("intent") == action.get("intent")]
            if not matches and len(candidates) == 1:
                matches = candidates
        return matches[0] if len(matches) == 1 else {}

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "resolved_actions": [],
            "uncertain_outcomes": [],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
            },
            "storylet_hits": [],
            "director_suggestions": [],
            "conflict_level": "none",
            "conflict_flags": [],
            "tension_delta": 0.0,
            "relationship_updates": [],
            "social_impacts": [],
            "modifier_updates": [],
            "knowledge_updates": [],
            "claim_discoveries": [],
            "object_lifecycle": [],
            "exchanges": [],
            "drive_updates": [],
            "drive_creations": [],
            "spawn_character": None,
            "storylet_definition": None,
            "topology_candidate": None,
            "world_additions": {},
            "route_knowledge_updates": [],
            "topology_changes": [],
            "simulation_notes": [],
            "applied_conflict_templates": [],
            "action_feedback": [],
            "simulation_error": None,
        }

    def _failure_result(self, message: str) -> Dict[str, Any]:
        result = self._empty_result()
        result["simulation_error"] = {
            "kind": "resolver_unavailable",
            "message": str(message),
        }
        result["simulation_notes"] = [str(message)]
        return result

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
                        "toward_player_states": item.get(
                            "toward_viewer_states", []
                        ),
                        "player_toward_actor_states": item.get(
                            "viewer_toward_actor_states", []
                        ),
                        "relationship_bits": item.get("relationship_bits", []),
                    }

        return {
            "location": location,
            "spatial_layout": player_pov.get("spatial_layout", {}),
            "visible_actors": visible_actors,
            "actor_states": visible_actor_states,
            "relations": relations,
        }
