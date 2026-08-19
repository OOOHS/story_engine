import json
import re
from typing import Dict, Any, Optional, List, Literal
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
    # Production semantic resolution is fail-closed.  The deterministic rule
    # resolver is an explicit component (HostRuleSimulationControl), not an
    # implicit response to an LLM outage.
    fallback_mode: Literal["fail_closed", "rule"] = "fail_closed"
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

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
                "超出会被整批回滚。drift 和 threshold 只能用定性档位（none/slow/steady/urgent，"
                "fragile/normal/durable），不得填写具体数值或初始 pressure；新建的 need 永远从 0 开始累积。"
                "创建必须由本轮已结算行动支持并给出 reason；能用已有 need 表达就不要新建。"
            )
        else:
            drive_creation_guidance = (
                "当前剧本未开放运行时创建新的持续压力条，不要输出 `drive_creations`"
                "（留空数组或省略）。"
            )

        director_signal_guidance = (
            "`director_signals` 是可选的、软性的、非事实的私人提示，宿主会把它投递到某个角色的收件箱里，"
            "该角色可以采纳、重新解读或直接无视，不构成本轮或未来任何一轮的强制行动。"
            "先判断一条【当前叙事机会】/剧情线线索的性质：如果它是纯环境性的事实"
            "（不需要哪个具体角色做出行动决定，比如场景变化、消息传来、局势变动），"
            "直接把它结算进 resolved_actions（actor=World）/state_updates/object_lifecycle，"
            "标注 source_storylet_id，不要走 director_signal。"
            "只有当内容需要某个具体在场角色自己做出行动/表态的决定时，"
            "才对一个跟该线索/机会有合理关联的在场角色给出一句提示；不能无凭无据地新建暗示，"
            "不能替角色决定要做什么，只能指出\"有什么值得注意\"。如果某个 storylet 的 target_actor/"
            "preferred_actors 都不在场或都不合适，就不要为它勉强指定别人，留空即可。"
            "每轮每个角色最多一条，全局每轮最多 3 条，超出会被丢弃。没有合适对象时留空数组。"
        )
        plot_beat_guidance = (
            "`plot_beat_proposals` 用来登记**尚未兑现**的新剧情点，不是当场改剧情钟。"
            "当【叙事压力】为 inject_crisis 或 raise_pressure，或本轮已提交事实确实打开了新的因果线时，"
            "可以提案；stay_course / allow_release 时不要为了热闹硬造。"
            "每条必须写清触发条件（StateCondition：scene / world_object / actor / plot）和兑现方式："
            "kind=environment 表示条件成立后由你在之后的回合结算成 actor=World 的事实；"
            "kind=character_decision 表示条件成立后走 director_signal，不能替角色做决定。"
            "需要新的可观察物件时，在**同一输出**里用 object_lifecycle.spawn 放到已有地点；"
            "不能新建地点或改拓扑。触发条件可以引用本轮即将 spawn 的对象，下回合条件成立后才会进入【当前叙事机会】。"
            "开新剧情线时填写 open_thread（必须有 opened_reason）；已有 plot_id 只登记 beat。"
            "不要写 clock / advance / plot_updates。每轮最多 2 条，缺条件或越权字段会被丢弃。"
        )

        prompt = f"""
你现在处于故事引擎的【Simulation】阶段。你的职责是做结构化结算，而不是写给玩家看的文本。

## 引擎核心规则（通用）
1. **只输出 JSON**：普通属性写入使用 `state_updates`；物品的创建、搬动、转交、收纳、开合、隐藏和销毁只能使用 `object_lifecycle`
2. **尊重当前状态**：严格遵守空间连通性、合法性裁决、关系值；不补前情，不伪造玩家历史
3. **玩家意图是锚点**：玩家 proposal 可以失败、受阻或产生 complication，但不能被改写成另一个未经提出的意图
4. **受限视角**：异地事件只能以余波、传话、态度变化回流，不切全知镜头
5. **有效推进**：只依据当前事实形成清晰、可结算的变化；没有有效变化时诚实返回稳定、失败或受阻，不为追求节奏编造结果
6. **可观察事实**：使用可观察的行为和事实，不下文学化诊断结论
7. **不得替角色选角**：resolved_actions 只能结算“本轮意图”中真实出现的 actor，或 actor=World 的事实（已注入的世界事件，或【当前叙事机会】里不需要具体角色决定、纯环境性的 storylet——比如天气突变、公告张贴、远处传来消息，这类可以直接由你写成 actor=World 的 resolved_action/state_updates/object_lifecycle 并落地为事实，同时填上 source_storylet_id）；Drama、Conflict、Storylet 和内容模板都不能凭空让未 proposal 的角色行动，也不能借 actor=World 替某个具体角色做决定或行动——那种情况必须走 director_signal，不能直接写进事实
8. **动作完成时结算**：本轮意图是离散事件队列中同时完成的一批原子动作。`action.kind` 只有 observe、move、interact、communicate、wait；自然语言 detail/target 说明具体语义。规则层先约束可确定部分，你只裁定剩余语义
9. **主动与被动观察分离**：observe 是角色主动花费行动获取细节；可公开观察到的动作是其他角色的被动观察来源。主动观察发现的角色私有信息写入 private_result，不能塞进公开 result 泄漏给旁观者
10. **不替宿主掷骰**：规则和当前事实足以确定的行动直接写 resolved_actions；真正存在不确定性的物理或观察行动只能写 uncertain_outcomes，同时给出成功/失败两个候选事实分支。你只能选择固定 difficulty 和所需 capability，不能输出概率、随机数、数值 modifier，不能同时把该 actor 写进 resolved_actions
11. **临时 Modifier 不是万能状态**：只有本轮已提交行动确实让角色形成疲惫、专注、受伤后的谨慎等临时非社交行为影响时，才可从 `modifier_catalog` 选择 kind。物理事实仍写 SceneState，针对某人的感受仍写 social_impacts；持续时间、叠加、权重和到期由宿主决定
12. **客观事实与角色知识分离**：`claim_catalog` 是 GM 可用的客观命题目录，但角色只能通过有效主动观察发现已连接且可见的 evidence，或由同场知情角色通过 communicate 传播 Claim。WorldEvent 同样只能由直接或自身见证者使用真实 event_id 转述；宿主从事件实体读取原始 statement，不能借 event_id 改写事件内容。不能把 truth_status、宿主条件、未发现的证据或未获知事件写进角色知识
13. **数值效果由宿主管理**：不要输出 relationship_updates、plot_updates、tension_delta、magnitude 或 drive delta。短期反应和 Modifier 使用 `minor/moderate/major/extreme` 定性强度，Drive 另加 increase/decrease 方向；宿主把这些标签编译为固定数值。长期关系轨道由宿主固定映射沉淀。已有 Plot 的钟只能由已提交世界事实触发因果规则；要开新剧情线或登记新剧情点，只能用 `plot_beat_proposals`，不能直接写 plot_updates 或编造 clock

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

## 宿主允许的临时 Modifier
{json.dumps(input_payload.get("modifier_catalog", []), ensure_ascii=False, indent=2)}

## GM 可用的客观 Claim 目录
{json.dumps(input_payload.get("claim_catalog", []), ensure_ascii=False, indent=2)}

## 当前权威 Agreement 快照
{json.dumps(input_payload.get("agreement_snapshot", {}), ensure_ascii=False, indent=2)}

## 本轮宿主签发的角色入口授权
{json.dumps(input_payload.get("character_entry_authorizations", []), ensure_ascii=False, indent=2)}

## 当前剧情线（仅供参考；推进已有钟不能写 plot_updates，开新线/新剧情点用 plot_beat_proposals）
{json.dumps(input_payload.get("plot_threads", []), ensure_ascii=False, indent=2)}

## 叙事压力（Drama 节奏，不是强制事件）
{json.dumps(input_payload.get("narrative_pressure", {}), ensure_ascii=False, indent=2)}

## 当前叙事机会（storylet，仅供参考，不是事实也不是指令）
{json.dumps(input_payload.get("storylet_opportunities", []), ensure_ascii=False, indent=2)}

## 行动角色的私有驱动力
{json.dumps(input_payload.get("drive_context", {}), ensure_ascii=False, indent=2)}

## 行动角色的私有义务与期限
{json.dumps(input_payload.get("obligation_context", {}), ensure_ascii=False, indent=2)}

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
      "outcome": "success | partial | fail | blocked | complication",
      "location": "动作发生地点",
      "result": "内部结果摘要",
      "private_result": "仅 actor 自己通过主动观察获得的信息；没有则为空字符串",
      "visibility": "public | local | hidden",
      "source_storylet_id": "可选；如果这条行动是在实现【当前叙事机会】里的某个 storylet，填它的 storylet_id，否则留空字符串"
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
  "state_updates": {{
    "scene": {{}},
    "world_objects": {{}},
    "actor_states": {{}}
  }},
  "conflict_level": "none | low | medium | high",
  "conflict_flags": ["public", "deception"],
  "social_impacts": [
    {{
      "source": "产生可观察社会影响的行动者",
      "affected": "亲自观察到该行动并形成感受的角色",
      "kind": "grateful | admiring | hurt | angry | afraid | suspicious | betrayed | relieved",
      "intensity": "minor | moderate | major | extreme",
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
      "intensity": "minor | moderate | major | extreme",
      "reason": "哪条已结算行动为何形成或解除该影响",
      "source_event": "可选的稳定事件引用"
    }}
  ],
  "claim_discoveries": [
    {{
      "actor": "本轮成功主动 observe 的角色",
      "claim_id": "claim_catalog 中的命题 id",
      "evidence_ref": "该 Claim 已连接且对 actor 可见的世界对象",
      "reason": "主动观察如何发现这项证据"
    }}
  ],
  "knowledge_updates": [
    {{
      "source": "本轮确实传递信息的角色",
      "target": "同地点的接收角色",
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
  "agreement_updates": [
    {{
      "operation": "propose | counter | accept | reject | withdraw",
      "agreement_id": "propose 的新关系 id；其余操作引用的已有 Agreement Entity id",
      "new_agreement_id": "counter 时必填的新报价关系 id",
      "actor": "本轮实际提出、反报价、接受、拒绝或撤回的角色",
      "parties": ["propose 时填写；counter 时必须与旧报价完全相同"],
      "title": "propose/counter 时的简短报价标题",
      "summary": "propose/counter 时的完整替换条款摘要",
      "expires_step": 8,
      "transfers": [
        {{"from": "甲", "to": "乙", "object_id": "已有对象", "quantity": 1}}
      ],
      "delegations": [
        {{"actor": "甲", "delegate": "乙", "obligation_id": "已有义务 id"}}
      ],
      "services": [
        {{
          "actor": "乙",
          "creditor": "甲",
          "obligation_id": "repair_watch",
          "title": "修好怀表",
          "summary": "收到报酬后完成修理并交还",
          "due_after_steps": 4,
          "grace_steps": 1,
          "wake_before_steps": 1,
          "delegation_policy": "creditor_consent",
          "completion_conditions": [
            {{
              "scope": "world_object",
              "target": "怀表",
              "path": "owner",
              "operator": "eq",
              "value": "甲"
            }}
          ]
        }}
      ],
      "escrows": [
        {{
          "transfer": {{"from": "甲", "object_id": "甲的铜币", "quantity": 2}},
          "release_to": "乙",
          "refund_to": "甲",
          "release_on_service": "repair_watch",
          "refund_on": ["breached", "cancelled"]
        }}
      ],
      "reason": "本轮哪条角色行动提出或回应了该报价"
    }}
  ],
  "drive_updates": [
    {{
      "actor": "需求受到影响的角色",
      "source": "产生该后果的已结算行动 actor",
      "need": "该角色 DriveState 中已有的 need 名称",
      "direction": "increase | decrease",
      "intensity": "minor | moderate | major | extreme",
      "reason": "哪条本轮事实让压力上升或缓解"
    }}
  ],
  "drive_creations": [
    {{
      "actor": "需要新压力条的角色",
      "need": "新 need 的名称，不能与该角色已有 need 重名",
      "drift": "none | slow | steady | urgent",
      "threshold": "fragile | normal | durable",
      "description": "这个持续压力代表什么",
      "reason": "哪条本轮事实催生了这个全新的持续压力"
    }}
  ],
  "director_signals": [
    {{
      "actor": "接收提示的角色（不会被强制执行）",
      "suggestion": "一句自然语言提示，说明有什么值得这个角色注意",
      "source_plot_id": "引用【当前剧情线】里已存在的 plot_id，没有关联线索则留空字符串",
      "source_storylet_id": "引用【当前叙事机会】里已存在的 storylet_id，没有关联机会则留空字符串",
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
          "target": "world_object/actor/plot 必填的已有或本轮 spawn 的对象名",
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
  ],
  "obligation_updates": [
    {{
      "operation": "create | fulfill | cancel | delegate",
      "actor": "承担义务的角色",
      "source": "本轮产生承诺、完成或解除事实的行动 actor",
      "obligation_id": "稳定且角色内唯一的 id",
      "title": "create 时必填的简短责任",
      "summary": "具体要完成什么",
      "creditor": "可选，义务面向的已有角色",
      "due_step": 5,
      "grace_steps": 0,
      "wake_before_steps": 1,
      "pressure_need": "可选，该角色已有的 need 名称",
      "due_pressure_severity": "light | moderate | severe",
      "breach_pressure_severity": "light | moderate | severe",
      "completion_conditions": [
        {{
          "scope": "actor | world_object",
          "target": "已有角色或当前对 debtor 可见的对象",
          "path": "location | owner | hidden",
          "operator": "eq",
          "value": "已有地点、角色或布尔值"
        }}
      ],
      "delegate": "delegate 操作的新承担者",
      "accepted_by": "delegate 操作必须与 delegate 相同",
      "delegate_pressure_need": "可选，新承担者已有的 need",
      "delegation_policy": "create 可选：forbidden | bilateral | creditor_consent",
      "approved_by": "creditor_consent 委托时必须等于原 creditor",
      "reason": "哪条已结算事实创建、完成或解除义务"
    }}
  ],
  "spawn_character": null,
  "simulation_notes": ["供渲染阶段参考的事实备注"]
}}

`spawn_character` 只有在“本轮宿主签发的角色入口授权”中存在可用记录时才能填写。必须引用其中精确的 `authorization_id`；name、role、location、initial_state、runtime、初始秘密和权威需求均以授权为准，不能改写。只有授权的 profile_mode=semantic 时，才可以补充 personality 和自然语言 goals。没有授权时必须保持 null；普通叙述提到陌生人不等于世界中已经出生了一个角色。

`state_updates.world_objects` 只能修改已有对象的普通描述属性，不能创建对象，也不能直接写 owner、location、container、sub_location、hidden、portable、kind、is_location、quantity、stack_key、affordances、is_container、container_capacity、container_size、container_open 或 container_opaque。空间拓扑 `connected_to / zones / default_zone / aliases / is_location` 也属于宿主 world-building 权限，即使引用的地点有效也会被普通语义事务拒绝。`spawn` 与 `relocate` 必须且只能填写 owner/location/container 之一；container 必须是内容包预定义、容量足够且当前打开的容器，不能把对象放入自身或形成嵌套循环。动态 spawn 不能自行发明 affordance、stack_key 或容器能力。`set_visibility` 只需要 hidden；`set_container_state` 只用于已有容器并填写 open；关闭且不透明的容器会遮蔽内容，关闭的容器内容即使透明可见也不能直接操作。`use` 必须填写对象状态中真实存在的 affordance_id，其 consumes、exclusive、requires_owner、requires_capabilities 和 need_effects 都由内容包预先定义，不能由本轮输出伪造；缺少能力或所有权时不能声称使用成功；非空容器不能被直接销毁或消耗。`destroy` 不填写放置字段。对象生命周期操作必须引用本轮同一 actor 的已结算行动；角色必须与对象和目标 owner/location/container 的有效地点物理同场。移动外层容器会自然携带全部嵌套内容，不要逐项伪造 relocate。多个角色同轮争夺有限或独占对象时，引擎会按 simultaneous proposal 语义统一仲裁，不要依靠 object_lifecycle 数组顺序暗示赢家。不要通过对象生命周期创建新地点或修改空间图。

`exchanges` 用于双方明确同意的物品、货币或资源交换。每个 exchange 只允许两个已有角色，双方必须同场、本轮都真实提交 proposal，并分别拥有非 hidden 的正向 resolved action；`accepted_by` 必须与 parties 完全一致。每条 transfer 的 from 必须真实拥有 object_id，物品必须 portable 且已向对方公开；同一对象不能同时出现在 object_lifecycle。quantity 缺省为整件/全部堆栈，部分数量转移要求内容预定义不可由模型改写的 stack_key，引擎会确定性拆分或合并堆栈。所有 transfer、对象所有权、数量变化以及同轮 obligation delegation 在一个 WorldStateTransaction 中原子提交；数量不足、双花、异地接受、隐藏物品或任一后续写入非法都会整批回滚。

你不能输出 `agreement_updates` 或 `contract_updates`。正式 Agreement 只能来自角色 proposal 中经过 Input 校验的模板、资产报价或 pending Agreement 引用，并由宿主在你的语义结算之后编译。你只需把角色真实的 communicate 结算为正向、失败或受阻；普通试探、含糊答复和未正式接受的讨价还价只属于 communication、Cognition 与 Memory。不要声明协议已经 settled/countered/expired，不要生成托管、授权、履约状态或资产条款。

`uncertain_outcomes` 只用于当前规则无法确定成功与否的尝试。`world` 用于物理或环境结果，`observation` 只允许对应 actor 本轮提交 observe。difficulty 只能使用模板枚举；required_capability 只是对权威 actor capabilities/skills 的引用，宿主会自行计算有限修正。success/failure 都必须包含一个 resolved_action，并把该分支才会发生的 Scene、对象、社会影响、Modifier、知识、协议提议、Drive 或 Obligation 变化放在同一分支中；不要在顶层重复这些变化。分支中的 actor.location 只允许当前 move actor 保持原地或到达 LegalityEngine 已授权的目的地，不能移动其他角色、替换目的地或让非 move 行动改变坐标；越权位置会在掷骰前从两个分支同时剥离并审计。分支同样不能直接写 relationship_updates、plot_updates、tension_delta、magnitude 或 drive delta。宿主选择分支后才会把它合并进权威事务，未选分支永远不能进入 Rendering 或 Memory。

`knowledge_updates.event_id` 的规范 statement 由宿主 Event Entity 决定。可选 `response_kind` 只描述本轮真实 communicate 对该事件采取的社会行为：report、explain、apologize、accuse、request、forgive 或 acknowledge；无效值会退化为普通 report。它只形成可审计的 Event response，不代表接收者相信、接受道歉、承认指控或改变关系。

即时羞辱、帮助、威胁、欺骗嫌疑、被救助或违约带来的主观反应写 `social_impacts`。source 必须有一条 affected 在同地点亲自可观察的非 hidden 已结算行动；kind 只能使用模板枚举，intensity 只能使用 `minor/moderate/major/extreme`，不能附带 magnitude、policy weight、概率、关系 delta 或持续时间。宿主会把强度标签映射为固定数值，再依据 Sentiment 定义创建感受、决定衰减和行动效用，并让其中一小部分按固定函数沉淀到长期 Relationship Track。Event response 与 Sentiment 分离：例如“甲道歉”是客观社会行为，“乙感到 relieved 或仍然 angry”才是乙的私有评价。

只有本轮已结算行动确实改变了某个角色的持续压力时才输出 `drive_updates`。使用 direction=`increase/decrease` 和定性 intensity 表示加剧或缓解，不得填写 delta；need 必须已经存在于该角色 DriveState。source 不是 actor 本人时，对应行动必须在 actor 所在地可观察，异地或 hidden 行动不能隔空改变对方压力。对象 affordance 已自动产生的 need_effect 不要在 drive_updates 中重复计算。

{drive_creation_guidance}

{director_signal_guidance}

{plot_beat_guidance}

只有本轮真实出现了承诺、任务指派、履行、明确解除或各方同意的责任转交时才输出 `obligation_updates`。模型不能输出 breach，违约由截止时间自动判定；create 的 due_step 必须是当前 step 到未来 200 步内，pressure_need 必须已经存在。`due_pressure_severity`/`breach_pressure_severity` 只能用定性档位（light/moderate/severe），不得填写具体数值；宿主把档位编译为固定压力值，逾期代价永远高于到期代价。动态 completion_conditions 最多四条，只允许 debtor 自己的 actor.location，或 debtor 当前确实可见对象的 location/owner/hidden 与权威值做 eq 比较；不能引用 plot、秘密对象、其他角色的私有状态或任意字段。fulfill/cancel 必须引用已有 obligation，并由 source 的本轮已结算行动支持。create 可用 delegation_policy 声明 forbidden、bilateral 或 creditor_consent，缺省为 creditor_consent。delegate 必须保留原期限和事实条件：当前 debtor 与新 delegate 必须同场并各自有 proposal 和非 hidden 正向 resolved action，accepted_by 必须等于 delegate；若 policy 为 creditor_consent 且 creditor 是第三方，该 creditor 也必须同场、有自己的 proposal/action，且 approved_by 必须等于 creditor。完成条件中的对象必须对新 delegate 可见，Plot/scene/其他角色条件不能借委托泄漏。引擎会原子地把旧记录标为 delegated，并在新承担者处建立带谱系的 active 记录。仅仅在 Agent 私有回复中声称“我完成了、取消了或把任务交给别人”不能改变义务。

只输出 JSON，不要输出解释，不要使用 Markdown。
"""

        response = self._llm.generate(prompt)
        content = response.get("content", "")
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return self._failure_result("结构化模拟服务不可用，权威结算已暂停。")
        parsed = self._parse_json_response(content)
        if parsed is None:
            return self._failure_result("结构化模拟输出解析失败，权威结算已暂停。")
        normalized = self._normalize_result(parsed, input_payload)
        normalized = self._enforce_legality(normalized, input_payload)
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
            action_kind = self._intent_value(
                item.get("actor"), "action_kind", input_payload
            ) or item.get("action_kind", "interact")
            action_target = self._intent_value(
                item.get("actor"), "action_target", input_payload
            ) or item.get("action_target", "")
            resolved_actions.append(
                {
                    "actor": item.get("actor", "Unknown"),
                    "intent": self._intent_value(
                        item.get("actor"), "intent", input_payload
                    ) or item.get("intent", ""),
                    "action_kind": action_kind,
                    "action_target": action_target,
                    "outcome": item.get("outcome", "partial"),
                    "location": item.get("location") or self._infer_location(item.get("actor"), input_payload),
                    "result": item.get("result", ""),
                    "private_result": (
                        " ".join(str(item.get("private_result", "")).split())[:1200]
                        if action_kind == "observe"
                        else ""
                    ),
                    "visibility": item.get("visibility", "public"),
                    "source_storylet_id": str(
                        item.get("source_storylet_id", "")
                    ).strip(),
                }
            )
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

        try:
            result["tension_delta"] = float(data.get("tension_delta", 0.0))
        except (TypeError, ValueError):
            result["tension_delta"] = 0.0

        # Exact Plot clocks and long-term Relationship Tracks belong to host
        # systems.  Keep the normalized shape for downstream transactions, but
        # never copy these values from semantic model output.
        result["plot_updates"] = []
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

        agreement_updates = data.get(
            "agreement_updates", data.get("contract_updates", [])
        )
        if not isinstance(agreement_updates, list):
            agreement_updates = []
        normalized_agreements = []
        for item in agreement_updates:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            if normalized.get("agreement_id") and not normalized.get("contract_id"):
                normalized["contract_id"] = normalized["agreement_id"]
            if normalized.get("new_agreement_id") and not normalized.get("new_contract_id"):
                normalized["new_contract_id"] = normalized["new_agreement_id"]
            normalized_agreements.append(normalized)
        result["agreement_updates"] = normalized_agreements
        result["contract_updates"] = normalized_agreements

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

        director_signals = data.get("director_signals", [])
        if not isinstance(director_signals, list):
            director_signals = []
        result["director_signals"] = [
            item for item in director_signals if isinstance(item, dict)
        ]

        plot_beat_proposals = data.get("plot_beat_proposals", [])
        if not isinstance(plot_beat_proposals, list):
            plot_beat_proposals = []
        result["plot_beat_proposals"] = [
            item for item in plot_beat_proposals if isinstance(item, dict)
        ]

        obligation_updates = data.get("obligation_updates", [])
        if not isinstance(obligation_updates, list):
            obligation_updates = []
        result["obligation_updates"] = [
            item for item in obligation_updates if isinstance(item, dict)
        ]

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
            and str(item.get("source", "")).strip() not in {"timeline", "injected"}
            and str(item.get("actor", "")).strip() not in covered_actors
        ]
        if not missing_intents:
            return

        missing_actors = {
            str(item.get("actor", "")).strip() for item in missing_intents
        }
        fallback_payload = dict(input_payload)
        fallback_payload["intents"] = missing_intents
        legality = input_payload.get("legality", {})
        if isinstance(legality, dict):
            fallback_payload["legality"] = {
                **legality,
                "checks": [
                    item
                    for item in legality.get("checks", [])
                    if isinstance(item, dict)
                    and str(item.get("actor", "")).strip() in missing_actors
                ],
            }
        actors = ", ".join(sorted(missing_actors))
        if self.fallback_mode == "rule":
            fallback = self._fallback_result(fallback_payload)
            result.setdefault("resolved_actions", []).extend(
                fallback.get("resolved_actions", [])
            )
            fallback_updates = fallback.get("state_updates", {})
            if isinstance(fallback_updates, dict):
                actor_updates = fallback_updates.get("actor_states", {})
                if isinstance(actor_updates, dict):
                    result.setdefault("state_updates", {}).setdefault(
                        "actor_states", {}
                    ).update(actor_updates)
            result.setdefault("simulation_notes", []).append(
                f"Host 为语义结算遗漏的 Agent 动作应用了显式规则回退：{actors}。"
            )
            return
        result.setdefault("simulation_error", {
            "kind": "unresolved_intents",
            "actors": sorted(missing_actors),
            "message": "语义结算未覆盖全部主体意图，权威步骤应重试而非合成行动。",
        })

    def _infer_location(self, actor_name: Optional[str], input_payload: Dict[str, Any]) -> Optional[str]:
        if not actor_name:
            return None
        for item in input_payload.get("intents", []):
            if item.get("actor") == actor_name:
                return item.get("location")
        return input_payload.get("player_pov", {}).get("location")

    def _intent_value(
        self,
        actor_name: Optional[str],
        key: str,
        input_payload: Dict[str, Any],
    ) -> Any:
        for item in input_payload.get("intents", []):
            if isinstance(item, dict) and item.get("actor") == actor_name:
                return item.get(key, "")
        return ""

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
            "conflict_level": "none",
            "conflict_flags": [],
            "tension_delta": 0.0,
            "plot_updates": [],
            "relationship_updates": [],
            "social_impacts": [],
            "modifier_updates": [],
            "knowledge_updates": [],
            "claim_discoveries": [],
            "object_lifecycle": [],
            "exchanges": [],
            "contract_updates": [],
            "agreement_updates": [],
            "drive_updates": [],
            "drive_creations": [],
            "director_signals": [],
            "plot_beat_proposals": [],
            "obligation_updates": [],
            "spawn_character": None,
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
            if not actor:
                continue

            if verdict == "allow":
                # A legal graph move is a deterministic host transition.  The
                # semantic resolver describes it, but cannot accidentally omit
                # (or decline) the actual body movement.
                rewrite_location = check.get("rewrite_location")
                action = self._find_matching_action(
                    result.get("resolved_actions", []), actor, intent
                )
                if (
                    check.get("rule") == "movement"
                    and rewrite_location
                    and action is not None
                    and str(action.get("action_kind", "")).strip() == "move"
                    and str(action.get("outcome", "")).strip() != "blocked"
                ):
                    action["location"] = rewrite_location
                    actor_updates.setdefault(actor, {})[
                        "location"
                    ] = rewrite_location
                continue

            action = self._find_matching_action(result.get("resolved_actions", []), actor, intent)
            if verdict == "block":
                result["uncertain_outcomes"] = [
                    check
                    for check in result.get("uncertain_outcomes", [])
                    if not isinstance(check, dict)
                    or str(check.get("actor", "")).strip() != str(actor)
                ]
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
                result["object_lifecycle"] = [
                    operation
                    for operation in result.get("object_lifecycle", [])
                    if not isinstance(operation, dict)
                    or str(operation.get("actor", "")).strip() != str(actor)
                ]
                result["exchanges"] = [
                    exchange
                    for exchange in result.get("exchanges", [])
                    if not isinstance(exchange, dict)
                    or str(actor) not in {
                        str(party).strip()
                        for party in exchange.get("parties", [])
                    }
                ]
                result["contract_updates"] = [
                    update
                    for update in result.get("contract_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("actor", "")).strip() != str(actor)
                ]
                result["agreement_updates"] = [
                    update
                    for update in result.get("agreement_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("actor", "")).strip() != str(actor)
                ]
                result["social_impacts"] = [
                    impact
                    for impact in result.get("social_impacts", [])
                    if not isinstance(impact, dict)
                    or str(impact.get("source", "")).strip() != str(actor)
                ]
                result["knowledge_updates"] = [
                    update
                    for update in result.get("knowledge_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("source", "")).strip() != str(actor)
                ]
                result["drive_updates"] = [
                    update
                    for update in result.get("drive_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("source", update.get("actor", ""))).strip()
                    != str(actor)
                ]
                result["drive_creations"] = [
                    creation
                    for creation in result.get("drive_creations", [])
                    if not isinstance(creation, dict)
                    or str(creation.get("actor", "")).strip() != str(actor)
                ]
                note = f"{actor}的动作被裁定为不合法：{check.get('reason', '')}".strip()
                if note and note not in notes:
                    notes.append(note)
                continue

            if verdict == "rewrite":
                result["uncertain_outcomes"] = [
                    check
                    for check in result.get("uncertain_outcomes", [])
                    if not isinstance(check, dict)
                    or str(check.get("actor", "")).strip() != str(actor)
                ]
                result["social_impacts"] = [
                    impact
                    for impact in result.get("social_impacts", [])
                    if not isinstance(impact, dict)
                    or str(impact.get("source", "")).strip() != str(actor)
                ]
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

        allowed_locations = {
            str(check.get("actor", "")).strip(): str(
                check.get("rewrite_location", "") or ""
            ).strip()
            for check in legality_checks
            if isinstance(check, dict)
            and str(check.get("actor", "")).strip()
            and str(check.get("rule", "")).strip()
            in {"movement", "movement_path"}
            and str(check.get("verdict", "allow")).strip() in {"allow", "rewrite"}
            and str(check.get("rewrite_location", "") or "").strip()
        }
        for actor, update in list(actor_updates.items()):
            if not isinstance(update, dict) or "location" not in update:
                continue
            authorized = allowed_locations.get(str(actor).strip(), "")
            if authorized:
                update["location"] = authorized
                continue
            update.pop("location", None)
            note = f"{actor}没有宿主移动裁定，语义位置写入已忽略。"
            if note not in notes:
                notes.append(note)
            if not update:
                actor_updates.pop(actor, None)

        return result

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
            action["outcome"] = "blocked"
            action["result"] = "该肢体接触没有对应行动提议支持，因此未发生。"
            rewritten_touch = True

        if rewritten_touch:
            note = "未获 proposal 支持的肢体接触已被拒绝。"
            if note not in notes:
                notes.append(note)
        return result

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
            if actor == "World" or source in {"timeline", "injected"}:
                world_result = self._summarize_world_intent(intent)
                if world_result:
                    resolved_actions.append(
                        {
                            "actor": actor,
                            "intent": intent,
                            "action_kind": item.get("action_kind", "interact"),
                            "action_target": item.get("action_target", ""),
                            "outcome": "complication" if source == "timeline" else "partial",
                            "location": item.get("location") or input_payload.get("player_pov", {}).get("location"),
                            "result": world_result,
                            "private_result": "",
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
                        "action_kind": item.get("action_kind", "move"),
                        "action_target": item.get("action_target", move_target),
                        "outcome": "success",
                        "location": move_target,
                        "result": f"动身前往{move_target}。",
                        "private_result": "",
                        "visibility": visibility,
                    }
                )
                continue

            summary = self._summarize_fallback_intent(actor, intent, item, input_payload)
            if not summary and not is_player:
                continue

            outcome = "success" if is_player else "partial"
            resolved_actions.append(
                {
                    "actor": actor,
                    "intent": intent,
                    "action_kind": item.get("action_kind", "interact"),
                    "action_target": item.get("action_target", ""),
                    "outcome": outcome,
                    "location": item.get("location"),
                    "result": summary or "系统未完成结构化判定，暂按意图记录。",
                    "private_result": "",
                    "visibility": visibility,
                }
            )
        result["resolved_actions"] = resolved_actions
        if actor_updates:
            result["state_updates"]["actor_states"] = actor_updates
        result["storylet_hits"] = []
        result["simulation_notes"] = [note] if note else []
        result = self._enforce_legality(result, input_payload)
        return self._enforce_social_realism(result, input_payload)

    def _summarize_world_intent(self, intent: str) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return ""
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

        return f"{actor}尝试执行其意图：{normalized.rstrip('。')}。"

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
            known_locations=scene_state.get_known_locations(),
            location_aliases={
                str(location): list((state or {}).get("aliases", []) or [])
                for location, state in scene_state.world_objects.items()
                if isinstance(state, dict) and scene_state.is_location(location)
            },
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
            "allow_touch": social.get("allow_unsignaled_touch", False),
        }
