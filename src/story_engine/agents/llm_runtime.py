import json
import re
from typing import Any, Dict

from src.story_engine.agents.types import (
    AgentDecision,
    AgentMotiveReference,
    AgentPerception,
)
from src.story_engine.agents.actions import AgentAction
from src.story_engine.components.identity import Identity
from src.story_engine.core.entity import Entity
from src.story_engine.core.logger import logger
from src.story_engine.llm.provider import LLMProvider


class LLMCharacterAgent:
    """Explicit opt-in escape hatch, not a default character runtime.

    This hand-assembles a single-shot prompt from ``AgentPerception`` and
    makes one stateless LLM call per turn: no persistent memory, no tool
    use, no multi-candidate deliberation. It exists for quick local runs
    and unit tests that need *some* runtime and do not care about character
    cognition quality. Story Engine no longer registers this as a default
    runtime anywhere -- a caller must explicitly pass
    ``agent_runtime_factories={"llm": ...}`` (or similar) to ``Runner`` to
    use it. Prefer a real agent framework (e.g. the Hermes container
    runtime in ``hermes_container.py``) for anything beyond throwaway runs.
    """

    def __init__(self, llm_config: Dict[str, Any] | None = None) -> None:
        logger.warning(
            "LLMCharacterAgent instantiated: this is a hand-rolled, "
            "single-shot prompt escape hatch, not a real agent framework. "
            "It must be explicitly registered (e.g. "
            "agent_runtime_factories={'llm': ...}) -- it is never a "
            "silent default."
        )
        provider_config = dict(llm_config or {})
        self._instruction_extras = str(
            provider_config.pop("system_instruction_extras", "") or ""
        ).strip()
        self._llm = LLMProvider(**provider_config)

    def decide(self, entity: Entity, perception: AgentPerception) -> AgentDecision:
        identity = entity.get_component_by_type(Identity)
        prompt = self._build_prompt(identity, perception)
        response = self._llm.generate(prompt)
        content = str(response.get("content", "")).strip()
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            raise RuntimeError(
                f"LLM character runtime unavailable for {perception.actor_name}; "
                "the agent produced no proposal"
            )
        return self._parse_decision(content)

    def _fallback_decision(self, perception: AgentPerception) -> AgentDecision:
        candidates = []
        current_location = str(perception.world_view.get("location") or "").strip()
        scheduled = sorted(
            [
                item
                for item in perception.private_schedule.get("active", [])
                if isinstance(item, dict)
            ],
            key=lambda item: (
                int(item.get("steps_until_due", 10**9)),
                str(item.get("commitment_id", "")),
            ),
        )
        for item in scheduled[:2]:
            location = str(item.get("location") or "").strip()
            if location and location != current_location:
                candidates.append(
                    AgentAction(
                        "move",
                        f"考虑日程“{item.get('title', item.get('commitment_id', ''))}”，前往{location}。",
                        location,
                    )
                )
        candidates.extend(
            [
                AgentAction("observe", "留在当前处境中观察眼前的变化。"),
                AgentAction("wait", "暂时保持现状，等待新的信息。"),
            ]
        )
        return AgentDecision(
            thought="当前无法形成更细致的判断，先依据可见日程与环境行动。",
            action=candidates[0].detail,
            action_spec=candidates[0],
            candidates=tuple(candidates),
            metadata={"fallback": True},
        )

    def _build_prompt(self, identity: Identity | None, perception: AgentPerception) -> str:
        role = identity.role if identity else "未知角色"
        personality = identity.personality if identity else ""
        goals = perception.private_goals or {
            "active": [
                {"title": goal, "status": "active"}
                for goal in (identity.goals if identity else [])
            ]
        }
        background = identity.background if identity else None
        visible_world = perception.world_view.get("visible_world", {})
        visible_world = visible_world if isinstance(visible_world, dict) else {}
        possessions = {
            str(name): state
            for name, state in visible_world.items()
            if isinstance(state, dict)
            and str(state.get("owner", "")).strip() == perception.actor_name
        }
        surrounding_world = {
            str(name): state
            for name, state in visible_world.items()
            if str(name) not in possessions
        }
        projected_world_view = dict(perception.world_view)
        projected_world_view.pop("viewer", None)
        projected_world_view["visible_world"] = surrounding_world
        if isinstance(projected_world_view.get("visible_objects"), list):
            projected_world_view["visible_objects"] = [
                item
                for item in projected_world_view["visible_objects"]
                if str(item) not in possessions
            ]
        current_location = (
            perception.self_state.get("location")
            or perception.world_view.get("location")
            or "未知"
        )
        return f"""
你是故事世界中的角色 {perception.actor_name}，身份是：{role}。

你不是旁白或导演。你只能依据自己的认知提出本轮行动意图；是否成功由世界模拟器决定。

当前决策时点：
- 世界步：{int(perception.step)}
- 激活范围：{perception.activation_scope}
- 所在地点：{current_location}

角色设定：
- 性格：{personality}
- 背景：{background or '未提供'}
- 角色专属约束：{self._instruction_extras or '无'}

你此刻能够感知的周围世界（不重复列出下方直接持有物）：
{json.dumps(projected_world_view, ensure_ascii=False, indent=2)}

你自己的当前状态：
{json.dumps(perception.self_state, ensure_ascii=False, indent=2)}

当前由你直接持有的物品：
{json.dumps(possessions, ensure_ascii=False, indent=2)}

持有物清单表示你知道自己带着什么，不等于已经检查了每个细节。暗格、封闭不透明容器内容和细微状态仍需要合法的主动 observe。

你的私有认知（其中的信念可能是错误的，不等于世界真相）：
{json.dumps(perception.private_cognition, ensure_ascii=False, indent=2)}

`belief_updates.operation` 省略或为 `upsert` 时可新增、修正自己的主观推断；若后来确认某条无 `event_id` 的主观推断不再成立，可用 `operation=retract` 并逐字引用原 statement。带 `event_id` 的记录是 Host 投递给你的事件知识，不能由你删除、降置信度或覆盖来源；你可以另写一条主观解释，但不能改写自己是否实际见证或获知过该事件。

你的私有驱动力（pressure 越接近 1 越迫切；这些不是其他角色自动知道的事实）：
{json.dumps(perception.private_drives, ensure_ascii=False, indent=2)}

你的结构化性格倾向（宿主会据此计算候选行动效用；你不能自行决定概率，也看不到精确权重和随机数）：
{json.dumps(perception.private_traits, ensure_ascii=False, indent=2)}

你当前对具体角色形成的短中期 Sentiment（这是你的私有主观感受，会衰减，不等于客观世界事实）：
{json.dumps(perception.private_sentiments, ensure_ascii=False, indent=2)}

你与当前可见角色的关系上下文：
{json.dumps(perception.relationship_context, ensure_ascii=False, indent=2)}

关系中的 `*_states` 是宿主从隐藏 Relationship Tracks 派生的定性判断。你可以理解 hostile、wary、non_hostile、trusted、friendly、close 和 Relationship Bits，但不能推断、索取或优化精确 favor/trust/malice 数值。

当前可见对象提供的结构化行动机会：
{json.dumps(perception.affordance_opportunities, ensure_ascii=False, indent=2)}

对象可能位于其他对象的 `container` 中。关闭且不透明的容器不会向你泄漏其内容；关闭的透明容器可以让你看见内容，但你仍需先提出打开容器的行动，才能取出或使用其中物品。移动外层容器会连同嵌套内容一起移动。不要假定自己能操作当前感知中没有出现、或隔着关闭容器的物品。

你当前承担的私有义务、期限与状态：
{json.dumps(perception.private_obligations, ensure_ascii=False, indent=2)}

你自己的日程邀请与时间窗口：
{json.dumps(perception.private_schedule, ensure_ascii=False, indent=2)}

日程不是强制移动命令，也不等同于 Obligation。你可以赴约、提前前往、迟到、拒绝或承担缺席产生的社会后果；世界时间线不会替你移动身体。

你的私有目标状态：
{json.dumps(goals, ensure_ascii=False, indent=2)}

只有 active 目标仍需要追求。achieved/failed 来自宿主对权威世界条件的核验；你不能自行把目标标记为完成或失败。`has_*_evidence_rule` 只表示宿主是否具备可验证规则，不会向你泄漏精确锁条件。

若 `private_goals.continuation` 存在，它只表示这个 Agent-grown Goal 已经获得过多少次离屏续行动机会，以及最近是否反复选择相同动作类型和目标。重复次数增加时宿主会逐步降低唤醒频率，但不会据此替你宣布失败。你应当结合当前 POV 改变方法、等待条件、协商，在开放目标出现具体办法时提交 refine，或在确实不再愿意追求时提交 abandon；不要为了清除计数而伪造进展。

如果一次已结算目标、你确实知道的 Claim 或 WorldEvent、现存 Drive need/Sentiment、自己的 Obligation、自己参与的 Agreement、当前可见对象/角色、已有 Relationship 或活动 NavigationProblem 自然引出了新的追求，你可以提交一个 `goal_requests` adopt 请求。`source_ref` 必须逐字引用上面私有状态中真实存在的 id、对象或角色；需求使用 `source_kind=drive_need` 和 `private_drives.needs` 中真实的 need id，导航问题使用 `source_kind=navigation_problem` 和真实 problem_id。宿主决定优先级、去重、冷却和是否采纳，你不能提供完成条件。WorldEvent 必须使用 belief 中真实的 event_id 和 `source_kind=world_event`，目击或可靠转述都可以成为主观目标来源，但事件本身不替你决定目标内容。可验证模板包括：`reach_location` 到达自己地图中的已知地点（不要求当前可见或相邻）、`possess_object` 取得可见物品、`deliver_object` 把自己持有的物品交给当前可见角色、`use_affordance` 实际使用当前结构化机会中明确列出的对象能力、`fulfill_obligation` 履行自己的义务、`settle_agreement` 促成自己参与的协议、`verify_claim` 为自己已知的 Claim 获得实际证据关联、`obtain_evidence` 同时识别并持有一件与已知 Claim 相连的证据、`become_acquainted` 与当前可见角色形成实际 acquaintance、`reach_relationship_state` 使自己对某人的关系达到宿主定性状态，以及 `communicate_event` 向当前可见角色真实转述一个自己知道的 WorldEvent。`use_affordance` 的 `resolution_target` 填对象、`resolution_affordance` 填该对象机会中逐字存在的 affordance_id；如果来源是 `drive_need`，该机会还必须明确降低同一个 need。只有之后真正提交并形成权威对象事件才会完成，单纯声称使用不算。更明确的事件社会回应使用 `respond_to_event`，并从 `explain / apologize / accuse / request / forgive / acknowledge` 中选择 `resolution_response`；这些类别只记录角色实际做出的回应，不自动规定接收者必须原谅、相信或改变关系。两种事件沟通目标的 `resolution_target` 都填当前可见接收者，`source_ref` 填 event id；只有经过宿主知识边界验证的真实 communicate 才会完成。关系目标只能选择 `non_hostile / trusted / close`，填写 `resolution_state`，不能填写 trust/favor/malice 数值。交付填写 `resolution_recipient`，证据目标填写 `resolution_evidence`；义务、协议、Claim、NavigationProblem 或 Relationship 必须引用自己的真实状态。与可见角色相识时使用 `source_kind=visible_actor`。宿主会根据 POV 与权威组件编译隐藏的成功/失败条件。若你先形成了没有 `resolution_kind` 的开放目标，后来从当前 POV 找到了具体做法，可对同一个 active agent goal 提交 `operation=refine`、真实 `goal_id` 和上述 resolution 字段；宿主只允许细化一次，并自行编译条件。不要输出 `completion_conditions` 或 `failure_conditions`。只有 `origin=agent` 的 active 目标可以由你请求 refine 或 abandon；初始作者目标不能自行修改，任何目标都不能由你自报 achieved/failed。

若新目标不是由原事件本身，而是由你实际收到的解释、道歉、指控、请求、宽恕、确认或转述引出，可以使用 `source_kind=event_response`；`source_ref` 必须逐字引用 recent experiences 中真实存在的 `response_id`。宿主会验证该回应确实进入过你的私有 Cognition，不能引用其他角色收到的回应或编造 id。

你当前受到的临时非社交 Modifier：
{json.dumps(perception.private_modifiers, ensure_ascii=False, indent=2)}

Modifier 表示疲惫、专注、受伤后的谨慎等暂时行为影响。它不替代物理世界事实，也不等于针对某人的 Sentiment。持续时间、叠加和行动权重由宿主维护，你不能自行增删或改写。

你掌握的结构化 Claim、自己的立场与潜在 leverage：
{json.dumps(perception.private_knowledge, ensure_ascii=False, indent=2)}

Claim 是世界中的客观命题实体，但这里不会向你泄漏宿主真值。`stance` 只表示你当前支持、反对或不确定；你的判断可能错误。`potential_leverage` 只表示你可能在沟通中利用该信息，`evidence_backed` 表示你实际持有与 Claim 相连的支持物。你可以调查、引用、隐瞒、撒谎或质疑，但不能凭空知道未出现在此处的 Claim 或 evidence。

`private_knowledge.map` 是你自己的地图知识，不是世界完整地图。你可以向当前可见角色一次转述一条自己真实知道的连续路线：在 communicate 候选中填写 `route_path`，包含 2 到 8 个不重复地点，且每个相邻节点都必须逐字存在于 known_routes 中。兼容的单边指路也可使用 `route_source/route_target`。对方会逐边把它记为你报告的路线，而不是自动同步成当前世界真相；任意一段道路都可能已经关闭。不能凭空编造路线，也不能用该字段保证对方相信或立刻前往。

你当前遇到的私有路线问题：
{json.dumps(perception.private_navigation, ensure_ascii=False, indent=2)}

Navigation problem 是宿主从你亲自遭遇的受阻移动派生的问题，不是自动目标。`failure_rule` 是 Host 对失败类型的定性分类（例如 stale_route），不是成功概率或隐藏拓扑。`alternative_path` 只是你当前地图中避开已知失效边后仍存在的路线；为空表示你尚不知道替代路线。若关联 obligation，steps_remaining 表示当前剩余时间。你可以绕路、观察出口、问路、指路、通知债权人、协商、等待或承担违约；不要宣称道路已经修复，也不要把问题本身当成 Host 强迫的选择。

其中 `conflicts` 是引擎根据权威地点图和期限计算出的私有日程冲突。`hard` 表示以你当前的位置和剩余步数无法独自按时完成全部责任，`constrained` 表示只有列出的特定顺序仍可行。你可以取舍、协商、委托、拒绝或承担违约后果；不要在私有回复中自行把义务标记为完成或取消。

与你有关的跨回合协商与 Agreement 关系：
{json.dumps(perception.private_agreements, ensure_ascii=False, indent=2)}

你当前可以选择提出的 Host 固定条款模板或通用资产报价能力（这里只显示当前 POV 允许使用的摘要和对象引用）：
{json.dumps(perception.agreement_opportunities, ensure_ascii=False, indent=2)}

pending agreement 是参与者之间已经实体化的权威社会关系，不等于条款已经结算。复杂协议正式提出时使用 `agreement_operation=propose` 和上方机会中的精确 `agreement_template_id`；Host 会填入完整条款和固定 Agreement ID，你不能改写它。`opportunity_kind=asset_offer` 表示可自由组合的普通资产报价：target 填 counterparty，`agreement_give_refs` 只能选择 give_options，`agreement_request_refs` 只能选择 request_options，至少一侧非空。`opportunity_kind=delivery_service_offer` 表示请当前可见 provider 交付其公开持有的一件物品：填写 `agreement_service_object`、`agreement_deadline=urgent|soon|flexible`，可从 destination_options 选择 `agreement_service_destination` 让对方送到已知相邻地点；不填 destination 时默认交给提议者。还可从 payment_options 选择一个 `agreement_payment_ref` 作为完成后释放、违约时退回的托管报酬。Host 生成 Agreement ID、期限、完成条件、Obligation 和 Escrow；你不能自己填写这些底层条款。回应时只能引用自己 private snapshot 中真实 pending 项的 `agreement_id`，使用 accept/reject；proposer 可使用 withdraw。不要替其他 party 接受，也不要把尚未 settled 的条款当成已经获得的物品或已转交的责任。这组简化字段暂不支持 counter，修改条款时先普通沟通，再提出一份由当前世界状态生成的新报价。

`counterparty_performance` 只根据你亲自参与过的已成交 service contract 统计：pending 表示服务责任仍未终结，fulfilled/breached/cancelled 来自权威 ObligationState。它不是全世界共享的声誉分，也不强迫你信任或敌视对方；请结合关系、理由、人格和具体历史自行判断。

刚刚发生且你能察觉的行动提议：
{json.dumps(perception.visible_proposals, ensure_ascii=False, indent=2)}

世界节奏信号：
{json.dumps(perception.world_signals, ensure_ascii=False, indent=2)}

近期观察：
{json.dumps(perception.recent_observations[-8:], ensure_ascii=False, indent=2)}

环境自动投递给你的被动观察：
{json.dumps(perception.passive_observations[-12:], ensure_ascii=False, indent=2)}

其中 `observed_step` 是你实际获得该经历的世界步，`age_steps` 是它距当前决策已过去的步数；请优先回应新近后果，不要把旧事件误当作刚刚发生。

你此前主动观察得到的结果（其中 private_result 只属于你）：
{json.dumps(perception.active_observation_results[-12:], ensure_ascii=False, indent=2)}

你当前能够察觉的其他角色进行中动作（只提供外在类型和可见目标，不泄漏其私有意图）：
{json.dumps(perception.ongoing_actions[-12:], ensure_ascii=False, indent=2)}

与你当前处境相关的记忆：
{json.dumps(perception.relevant_memories, ensure_ascii=False, indent=2)}

当前计划：{perception.current_plan or '无'}

每个候选可用 `motive_refs` 声明它是在响应哪个当前 active Goal、Obligation、NavigationProblem、近期真实失败的 action event，或本轮刚投递到你注意队列的 WorldEvent/EventResponse，格式是 `{{"kind":"goal | obligation | navigation_problem | action_failure | world_event | event_response","ref":"真实 id"}}`。NavigationProblem 引用必须逐字使用 `private_navigation.active[].problem_id`；它只表示你是在绕路、问路、调查或协商处理该问题，不表示道路已经恢复。`action_failure` 引用必须逐字使用自己近期经历中 outcome 为 `fail` 或 `blocked` 的 action `event_id`；它只表示你在改变方法，不表示下一次尝试会成功。事件引用必须逐字来自上方 `pending_world_events / pending_event_responses`，已经处理过的旧事件不能反复作为新刺激。这些都只是可审计的主观行动理由，不表示目标会完成、义务会履行或行动会成功；Host 会核验引用并决定它对概率的影响。不要引用别人的、已终结的、异地未知的或当前私有状态中不存在的记录。候选还可分别声明“如果 Host 选中这个行动”之后应保留的 `next_plan` / `next_focus`；它们必须在行动随后失败时仍然合理，若后续状态依赖成功结果就先省略，下一轮看到真实结算后再更新。省略会保留现状；只有做出该选择本身就表示放弃旧路线时，才输出 `clear_plan=true` 或 `clear_focus=true`。候选可以新增因选择本身成立的私有承诺，但不能因为预期行动成功就提前完成旧承诺。只有 Host 实际选中的候选会应用这些连续性更新；整轮观察形成的 `belief_updates`、基于已经可见结果的 `resolved_commitments` 和目标请求放在顶层。这些字段只维护你自己的连续性，不会完成或取消 Host 的 Goal、Obligation、Agreement 或日程责任。

请提出 2 到 4 个真正不同、具体且可执行的候选行动，不要自行随机选择，也不要给出成功概率。仅仅改写措辞、增加“仔细地”等程度词，或对同一目标重复同一种行为策略，不算不同候选；至少应在原子动作类型、明确 target、社会策略，或 Claim / Agreement / Affordance / 路线等正式操作上形成实质差异。有主要对象时必须填写 `target`。宿主会结合人格、目标、需求、关系、风险和带种子的随机数选择最终行动。外部行动只使用五种抽象类型：`observe` 主动观察或搜索、`move` 移动、`interact` 与物品或环境互动、`communicate` 对角色表达、`wait` 等待或保持现状。`detail` 和 `target` 使用自然语言说明具体做法，是否成功及实际耗时由环境和 GM 共同裁定。只有选择上方“当前可用对象能力”中真实列出的能力时，才在 `interact` 候选中填写其精确 `affordance_id`，且 `target` 必须是该能力所属对象；它只是让宿主识别所选能力，不能声明结果。若 `interact` 是把自己当前持有且公开的整件物品交给当前可见角色，target 填物品并填写精确 `delivery_recipient`；这只是交付 proposal，语义结算仍可判定对方没有接住或拒绝。部分堆栈、互换、附条件交易不能使用该字段。若 `communicate` 是向当前可见角色表达你确实知道的 Claim，可以填写精确 `claim_id`、你选择公开表达的 `claim_stance=supports/rejects/uncertain`，以及可选的 `evidence_refs`。你可以撒谎，即 claim_stance 可以不同于自己的立场，但不能引用自己不知道的 Claim，evidence_refs 只能来自该 Claim 中你确实知道且能当场出示的证据。

被动观察不需要你主动声明：同场可见的世界变化会由环境自动进入你的经验。只有当你要集中注意、检查细节、翻找、偷听或搜索时才选择 `observe`；这是会占用行动机会的主动感知。允许说话、移动、打开或关闭容器、收纳或取出物品、使用当前可操作对象、提出或接受物品/资源交换、履行或拒绝义务、协商转交责任、明确接受他人委托、试探、等待或隐瞒；不要替世界宣布结果，也不要描述其他角色必然如何反应。交换和责任转交必须由各方分别公开提出或接受，不能替另一个角色表示同意，也不能拿自己并不拥有、尚未公开、仍装在容器内或数量不足的物品许诺直接交换。不要为了制造戏剧而违背角色利益。高压需求和临期义务应当真实影响优先级，但不要求机械地选择 relief_score 最高的动作。

你可以同时更新自己的主观认知，但不得把推测声明成世界事实。后台行动应优先处理角色所在地的事务，不要假定自己看到了玩家所在地的事件。

只输出 JSON：
{{
  "thought":"一句简短的私有判断",
  "candidates":[
    {{
      "kind":"observe | move | interact | communicate | wait",
      "detail":"一句清楚、具体但不预先宣布成功的自然语言行动意图",
      "target":"可选，主要对象、角色或地点",
      "affordance_id":"可选；仅 interact 使用，必须逐字引用当前可用对象能力",
      "delivery_recipient":"可选；仅 interact 交付自己持有的整件物品时填写当前可见接收者",
      "claim_id":"可选；仅 communicate 使用，必须逐字引用自己知道的 Claim",
      "claim_stance":"可选：supports | rejects | uncertain；表示公开表态而非客观真值",
      "evidence_refs":["可选；自己知道且当场可出示的关联证据"]
      ,"agreement_operation":"可选；仅 communicate 使用：propose | accept | reject | withdraw"
      ,"agreement_template_id":"可选；propose 时逐字引用当前模板机会"
      ,"agreement_id":"可选；回应时逐字引用自己的 pending Agreement"
      ,"agreement_give_refs":["可选；普通资产报价中自己交出的公开物品引用"]
      ,"agreement_request_refs":["可选；普通资产报价中请求对方交出的公开物品引用"]
      ,"agreement_service_object":"可选；交付委托中 provider 当前公开持有的物品"
      ,"agreement_service_destination":"可选；交付委托中 destination_options 的已知相邻地点"
      ,"agreement_payment_ref":"可选；交付委托中自己公开持有的托管报酬"
      ,"agreement_deadline":"可选；交付委托使用 urgent | soon | flexible"
      ,"route_source":"可选；指路时自己已知道的道路起点"
      ,"route_target":"可选；指路时 known_routes[route_source] 中的终点"
      ,"route_path":["可选；一次指路的 2 到 8 个连续已知地点"]
      ,"motive_refs":[{{"kind":"goal | obligation | navigation_problem | action_failure | world_event | event_response","ref":"当前私有状态中的真实 active、近期失败或 pending id"}}]
      ,"next_plan":"可选；仅当本候选被 Host 选中时采用的新私有计划"
      ,"clear_plan":false
      ,"next_focus":"可选；仅当本候选被 Host 选中时采用的新关注点"
      ,"clear_focus":false
      ,"commitments":["可选；本候选被选中后新增的私有承诺"]
    }}
  ],
  "belief_updates":[
    {{"operation":"upsert | retract","statement":"可选的主观判断；retract 时逐字引用现有无 event_id 推断","confidence":0.0,"source":"观察或推理来源"}}
  ],
  "resolved_commitments":["可选；仅根据本轮开始时已经看到的真实结果，逐字引用已完成或明确放弃的现有私有承诺"],
  "goal_requests":[
    {{"operation":"adopt","title":"可选的新目标","source_kind":"resolved_goal | claim | world_event | event_response | drive_need | sentiment | obligation | agreement | visible_object | visible_actor | relationship | navigation_problem","source_ref":"私有状态中真实存在的引用","reason":"为什么自然产生这个目标","resolution_kind":"可选：reach_location | possess_object | deliver_object | use_affordance | fulfill_obligation | settle_agreement | verify_claim | obtain_evidence | become_acquainted | reach_relationship_state | communicate_event | respond_to_event","resolution_target":"可选：地点、物品、义务、协议、Claim 或角色 id；事件沟通时填接收者","resolution_affordance":"use_affordance 时填对象机会中真实的 affordance_id","resolution_recipient":"deliver_object 时的当前可见接收者","resolution_evidence":"obtain_evidence 时的当前可见证据物品","resolution_state":"关系目标可选：non_hostile | trusted | close","resolution_response":"respond_to_event 时可选：explain | apologize | accuse | request | forgive | acknowledge"}},
    {{"operation":"refine","goal_id":"当前尚无结算规则的 active agent goal id","resolution_kind":"当前 POV 已支持的可验证模板","resolution_target":"模板要求的真实目标","resolution_affordance":"可选","resolution_recipient":"可选","resolution_evidence":"可选","resolution_state":"可选","resolution_response":"可选"}},
    {{"operation":"abandon","goal_id":"当前 active 的 agent goal id","reason":"为什么这个目标已经不再值得继续"}}
  ]
}}
""".strip()

    def _parse_decision(
        self, content: str, *, strict_json: bool = False
    ) -> AgentDecision:
        candidate = content
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        else:
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                candidate = content[start : end + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            if strict_json:
                raise ValueError("agent response is not valid decision JSON") from exc
            action_match = re.search(r"ACTION:\s*(.*)", content, re.DOTALL | re.IGNORECASE)
            thought_match = re.search(
                r"THOUGHT:\s*(.*?)(?=ACTION:|$)", content, re.DOTALL | re.IGNORECASE
            )
            action = action_match.group(1).strip() if action_match else content.strip()
            thought = thought_match.group(1).strip() if thought_match else ""
            return AgentDecision(action=self._one_line(action), thought=self._one_line(thought))

        if not isinstance(data, dict):
            raise ValueError("agent decision JSON must be an object")

        raw_candidates = data.get("candidates", [])
        candidates = []
        candidate_motive_refs = []
        candidate_updates = []
        bounded_candidates = (
            raw_candidates[:4] if isinstance(raw_candidates, list) else []
        )
        for item in bounded_candidates:
            if not isinstance(item, (dict, str)):
                continue
            action = AgentAction.from_value(item, strict=strict_json)
            if not action.detail:
                continue
            candidates.append(action)
            raw_update = item if isinstance(item, dict) else {}
            raw_motive_refs = raw_update.get("motive_refs", [])
            if not isinstance(raw_motive_refs, list):
                raw_motive_refs = []
            motive_refs = []
            for raw_ref in raw_motive_refs[:8]:
                motive_ref = AgentMotiveReference.from_value(raw_ref)
                if motive_ref is not None and motive_ref not in motive_refs:
                    motive_refs.append(motive_ref)
            candidate_motive_refs.append(tuple(motive_refs))
            candidate_updates.append(
                {
                    "plan": self._one_line(
                        str(raw_update.get("next_plan", ""))
                    ),
                    "clear_plan": raw_update.get("clear_plan") is True,
                    "focus": self._one_line(
                        str(raw_update.get("next_focus", ""))
                    ),
                    "clear_focus": raw_update.get("clear_focus") is True,
                    "commitments": raw_update.get("commitments", []),
                }
            )
        candidates = tuple(candidates)
        action_spec = (
            candidates[0]
            if candidates
            else self._parse_single_action(data, strict_json=strict_json)
        )
        if strict_json and not action_spec.detail:
            raise ValueError("agent decision JSON contains no executable action")
        metadata = {
            "plan": self._one_line(str(data.get("plan", ""))),
            "clear_plan": data.get("clear_plan") is True,
            "focus": self._one_line(str(data.get("focus", ""))),
            "clear_focus": data.get("clear_focus") is True,
            "belief_updates": data.get("belief_updates", []),
            "commitments": data.get("commitments", []),
            "resolved_commitments": data.get("resolved_commitments", []),
            "goal_requests": data.get("goal_requests", []),
            "candidate_updates": candidate_updates,
        }
        return AgentDecision(
            action=self._one_line(action_spec.detail),
            thought=self._one_line(str(data.get("thought", ""))),
            metadata=metadata,
            action_spec=action_spec,
            candidates=candidates,
            candidate_motive_refs=tuple(candidate_motive_refs),
        )

    @staticmethod
    def _parse_single_action(data: Dict[str, Any], *, strict_json: bool) -> AgentAction:
        raw_action = data.get("action")
        if strict_json and not isinstance(raw_action, dict):
            raise ValueError("agent decision JSON contains no executable action")
        action = AgentAction.from_value(raw_action or "", strict=strict_json)
        if not action.detail:
            raise ValueError("agent decision JSON contains no executable action")
        return action

    def _one_line(self, value: str) -> str:
        return " ".join(value.split()).strip()
