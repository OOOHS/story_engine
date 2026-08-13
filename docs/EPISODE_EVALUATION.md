# 多轮涌现回归

单系统单元测试只能证明资产、关系或 Agent 边界各自正确，不能证明一个小故事种子真的能连续生长。`EpisodeRunner` 提供运行时无关的多轮审计层：它调用普通 `Session.run_step()`，不拥有世界特权，也不替 Agent 或 GM 生成行为。

```python
from src.story_engine.evaluation import EpisodeRunner

report = EpisodeRunner().run(
    session,
    steps=12,
    step_inputs=lambda index, session: {
        "inject_events": []
    },
)

report.write_json("artifacts/episode-001/summary.json")
```

固定步数适合机制回归；需要测试一个故事是否会自然收束时，可以启用宿主完结策略：

```python
from src.story_engine.evaluation import EpisodeClosurePolicy

report = EpisodeRunner().run(
    session,
    steps=24,
    closure_policy=EpisodeClosurePolicy(stable_steps=2),
)
```

完结资格只由权威状态推导：已有的可验证 Goal 已结算、没有活动 Obligation、没有待接受 Agreement 或待结算 performance、没有尚未出席/错过/取消的 Timeline commitment、没有可自动处理的活动 NavigationProblem、动作队列为空，并且没有尚未交付给可自动运行角色策略的 WorldEvent 或 event response；内容需要时还可要求 Plot 完结。默认不强迫内容包预写至少一个可验证 Goal，因此纯世界种子可以在所有自然生长的线程真正平息后结束；传统任务式评测可用 `require_goal_anchor=True` 或 launcher 的 `--require-goal-anchor` 明确要求人工目标锚点。每个 autonomous、非 dormant 的角色还必须至少真正获得过一次决策机会；`AgentController.decision_count` 是可回滚的 Host 状态，离屏角色仍按错峰 schedule 运行，但 closure 不会抢在其第一次背景 turn 前发生。章节式审计若确实允许忽略尚未运行的远端角色，可使用 `require_all_autonomous_agents_exercised=False` 或 `--allow-unexercised-agents`。显式 dormant/autonomy-disabled 角色的旧 pending attention 和 NavigationProblem 会单独计入诊断，但不会永久阻塞 closure，因为它只能由人工恢复；新事件仍进入其 belief/experience，却不会越过策略创建自动 interrupt。临界 Drive need 只有在角色当前 POV 中存在 Host 已验证、available 且确实降低同一 need 的对象能力时才阻塞 closure；普通压力漂移、当前没有结构化解决办法的压力和 dormant 角色的压力只进入诊断，避免永久卡死。章节式截断可用 `require_no_actionable_critical_needs=False` 或 `--allow-actionable-critical-needs-closure` 放宽。资格必须连续保持 `stable_steps`，且默认这些步骤不能继续产生 `scene / plot / relationship / agreement / obligation / goal / knowledge / navigation / claim / world_event` 结构变化；最终目标刚结算、物品刚转手或角色仍在形成新 Claim knowledge 的那一步不会被误算作安静尾声。持续 Drive 漂移、记忆归档等低层变化不单独阻塞；章节式评估若有意在重大变化后立即截断，可设置 `require_stable_material_state=False` 或 launcher 的 `--allow-material-change-closure`。这避免把“一件大事刚发生但当事人还没反应”“对方刚道歉但接收者尚未获得决策机会”“角色刚发现道路中断但还没应对”“眼前有食物却仍处于临界饥饿”“场景种子中明确安排的仪式还没开始”或一轮短暂无事误判成结局。Agent 与 GM 都不能输出 `story_complete=true` 来绕过这些条件。

Episode 完结不等于世界关闭。它只是说明本次评估已经形成一个可停止的叙事边界；同一个 Session 仍可继续运行，角色也可以保留感谢、怨恨、伤痛等未消退状态。未传 `closure_policy` 时，运行器仍精确执行 `steps` 轮。

每个 step 还保存最多 12,000 字符的 `narrative_text`。它只取 RenderingSystem 已经完成最小权限投影后的玩家可见 narration，不保存 Agent thought、private_result、完整 GM packet、隐藏关系值或 Storylet/Plot director 信息。这样 Episode JSON 可以同时用于结构审计和人工/外部模型的文本质量评审，而不需要重新运行世界或读取后台状态。

每步记录：

- simulation time 前后值；
- 权威世界与角色私有状态的稳定 hash；
- proposal actor、resolved actor 和动作类型；
- WorldStateTransaction 是否提交；
- Pair Relationship、Sentiment、Agreement 数量；
- 当前激活的临时 Modifier 数量；
- 客观 Claim Entity 数量与所有角色当前持有的 Claim knowledge 数量；
- 客观 WorldEvent Entity 数量，以及本轮新增的不可逆事件引用；
- 排除内部版本号后的实质状态变化种类；
- 物品归属、角色/物品出现消失、Plot 阶段、义务与 Agreement 生命周期等不可逆变化；
- 宿主因果规则命中、相邻行动响应链和角色动作画像；
- 被宿主策略选中的行动是否确实获得目标相关贡献；
- 候选集的安全摘要：runtime/environment 候选数量、runtime 动作类别与非空目标数量、选中来源和动作类别，以及所选方案是否得到结构化动机或主观连续性支持；
- 权威性违规。
- 当前宿主完结资格与阻塞原因。

当前硬审计包括：

- 没有 proposal 的 actor 不能出现在 resolved actions；
- 未选择的 uncertain outcome 不能越过 Simulation 边界；
- `private_result` 不能进入玩家 visible simulation；
- 被拒事务不能保留 resolved facts；
- Relation/Agreement Entity 不能进入 AgentRegistry；
- Scene 中每个行为角色必须同时拥有 Entity、AgentController 和 live runtime；
- live runtime 缺失、概率检查错误、Sentiment 或 WorldEvent 发布错误会成为 Episode violation；
- Intent 的 `policy_candidate_id` 必须与宿主 Policy Trace 的选中项一致。

质量标记不是“故事好不好”的自动裁判，而是低成本退化报警：

- `stagnant_episode`：世界和角色状态都没有变化；
- `single_actor_monopoly`：多个角色提交 proposal，但长期只有一个角色形成结果；
- `repetitive_actions`：同一 `actor + action kind + explicit target` 完成批次连续重复；仅仅连续数步都属于 `interact`，但角色或目标不同，不再误判为循环；
- `mostly_rejected_transactions`：多数世界事务被拒绝；
- `no_social_state_growth`：多角色持续行动，却没有形成任何关系或 Sentiment。
- `deadlocked_episode`：末尾连续多轮只有等待/观察，且没有实质状态变化；
- `undifferentiated_actor_behavior`：多个活跃角色长期呈现近乎相同的动作类型分布。

这里把三种容易混淆的信号分开：

- `world_change_steps` 只计算实质状态，`world_version`、时间阶段计数、渲染连续性缓存等内部记账不会伪装成剧情推进；
- `goal_engagement_*` 只表示宿主策略选择的行动受角色目标影响，不声称自然语言目标已经完成；
- `commitment_resolution_count` 只统计由权威条件证明的 Obligation/Agreement performance 结算。没有条件证据时，评估器宁可报告“不知道”，不会让 LLM 自评成功。
- `goal_resolution_count`、`goal_achievement_count` 和 `goal_failure_count` 只统计 `GoalState` 根据权威世界条件产生的生命周期转换；普通自然语言目标没有验证条件时会继续保持 active。

额外指标包括：

- `causal_transition_steps`：宿主因果规则实际触发的轮数；
- `causal_handoff_steps/count`：本轮新增多少条带权威 provenance 的后果承接边，例如 `agreement <- resolved_action`、`obligation <- agreement`、`world_event <- resolved_action/host_transition/obligation`、`event_response <- world_event`、`sentiment <- resolved_action/agreement performance`、`relationship track <- sentiment`、`goal_resolution <- goal`、`agent goal <- goal_resolution/world_event/event_response/sentiment/navigation_problem`；resolved Goal 使用带 actor/id/status 的结算事件节点，而不是长期存在的 Goal 实体，因此“上一轮完成旧目标、下一轮长出后续目标”会被正确识别为跨 step 因果；
- `policy_motive_handoff_count` / `policy_motivated_action_count`：有多少条实际被选中且已提交的行动，由宿主 Policy Trace 证明获得了 Goal、Sentiment、有向 Relationship Track、Obligation、Claim knowledge、Agreement、Modifier、Drive need、Timeline commitment、WorldEvent 或 EventResponse 的正向效用支持。负向贡献只表示抑制，不建立因果父边；评估器也不会从行动文案反猜动机；
- `attention_motive_available_decision_count` / `urgent_attention_motive_available_decision_count`：有多少次宿主抽样决策时，角色实际收到普通/高显著性 pending WorldEvent 或 EventResponse；`event_motive_reference_decision_count` / `event_motive_selected_decision_count` 分别记录候选中声明有效事件动机引用、以及最终选中事件动机行动的决策数。`event_motive_reference_rate` / `event_motive_selection_rate` 用于观察 Agent 是否把刚发生的 POV-safe 后果接入下一步行动，而不是只被动收到事件；这些是诊断指标，不强迫角色回应每一条事件。
- `policy_decision_count` / `sampled_policy_decision_count`：实际形成策略记录的角色决策数，以及其中交给 Host 抽样的决策数；
- `runtime_candidate_count` / `minimum_runtime_candidate_count` / `maximum_runtime_candidate_count` / `mean_runtime_candidate_count`：Agent runtime 提供并通过 Host 语义去重后的候选规模。真实 Hermes 每轮必须至少保留两个实质不同候选，否则该轮直接失败；
- `selected_runtime_candidate_count`：Host 最终选择 runtime 候选而非环境补充候选的次数；`continuity_supported_selection_count` 和 `motivated_selection_count` 分别记录所选方案是否受到角色当前 plan/focus/private commitment 或其他结构化动机的正向支持。这些审计只保存数量、来源、动作类别和布尔值，不保存 thought、未选行动文本、target、秘密引用或精确效用；
- `causal_source_kind_count`：Episode 实际使用了多少种不同的权威来源类型，避免仅用动作/状态变化数量冒充因果丰富度；
- `causal_consequence_node_count`：显式因果图中有来源的后果节点数量；
- `max_causal_chain_depth`：沿 `consequence <- source` provenance 图得到的最长链深度；例如 `Goal <- NavigationProblem <- movement_failure` 深度为 2。算法只遍历显式边并对循环 fail-safe，不尝试从自然语言补边；
- `cross_step_causal_handoff_count` / `cross_step_causal_step_count` / `max_causal_span_steps`：因果父节点先在某轮成为真实 consequence、子节点在后续轮才出现的传播数量、涉及步数和最长时间跨度。它把“同轮一次性派生八层账本”与“事件在下一轮形成目标、目标再驱动行动、行动继续产生后果”区分开；Episode 只使用显式 provenance 和 consequence 首次出现 step，不从文案猜测先后关系；
- `causal_arc_present`：至少一条显式因果链跨越两个 Episode step；`resolved_causal_arc` 还要求该 Episode 达到 Host closure 且发生过不可逆状态转换。它们是结构诊断，不宣称文本必然有趣，也不把同轮原子事务错误拆成多个戏剧阶段；
- `narrative_step_count` / `narrative_character_count` / `unique_narrative_step_count` / `narrative_repetition_rate` / `max_narrative_repetition`：前台 transcript 的覆盖、规模、整体重复率和单一模板最高出现次数。已提交 Episode 完全没有 narration 会标记 `missing_narrative_output`；任一归一化 narration 出现四次以上会标记 `repetitive_narration`，即使中间夹有少量不同文本也不会漏报。这些指标不评价文风优劣，但能发现 renderer 失效和明显模板循环；
- `material_stability_blocked_steps` / `terminal_material_change_count`：启用自然闭合时，有多少步因为仍在发生结构变化而不能进入安静窗口，以及 step limit 最后一轮仍有多少类变化。若 Episode 未闭合且最后一步仍被这一条件阻塞，会额外标记 `materially_active_at_step_limit`；它表示故事仍在发展，不等同于 deadlock 或未结义务；
- `actionable_critical_need_blocked_steps` / `terminal_actionable_critical_need_count`：多少步因为 autonomous 角色仍有临界且眼前可缓解的 need 而不能闭合，以及上限时仍剩多少项。持续到 step limit 会标记 `actionable_critical_needs_at_step_limit`；无可见结构化解决办法或 dormant 角色的 need 分别只进入 closure details 的 `unactionable_critical_need_count` / `dormant_actionable_critical_need_count`；
- `max_repeated_policy_action_count`：任一角色连续被 Host 选中同一语义行动的最大次数。达到四次会标记 `repetitive_policy_choices`；它比只看粗动作类型的 `repetitive_actions` 更精确，因为连续观察不同对象不会被当成同一个方案，而同义改写无法逃避计数；
- `irreversible_change_steps/count`：产生不可逆局势变化的轮数和事件数；新建/被新事实更新的 Sentiment 与有新 provenance 的 Relationship Track/Bit 计入，单纯衰减和到期不计入；
- `modifier_change_count`：由新权威事实创建或更新的 Modifier 数量；单纯到期不计入。若存在变化却没有 `modifier <- source`，报告标记 `unattributed_modifier`；
- `drive_need_cause_count`：新增的 `drive_need <- resolved_action/obligation/clock` provenance 数量；need ledger 本身属于宿主审计状态，不进入角色或玩家视图；
- `interaction_chain_steps` / `longest_interaction_chain`：本轮行动是否继续响应上一轮涉及的角色或对象；
- `actor_differentiation`：角色动作类型分布的平均差异，范围为 0～1；
- `goal_engagement_rate`：存在宿主抽样策略时，目标影响所选行动的轮次比例。
- `goal_continuation_steps` / `goal_continuation_actor_count`：离屏角色实际因可验证 Agent-grown Goal 获得续行动机会的轮数与角色数；不把普通 background tick 误记为目标持续性。
- `goal_continuation_attempt_count` / `max_repeated_goal_action_count`：累计续行动尝试和单角色最高重复签名次数，用于发现低频但永久的目标循环；这些值是调度诊断，不是故事进展或失败证明。
- `goal_reactivation_steps` / `goal_reactivation_actor_count` / `goal_reactivation_count`：多少轮、多少角色以及累计多少次因为 POV-safe 的相关事件解除目标退避；用于区分真实条件变化与无关世界噪声。

Event pending 数量按宿主优先 attention queue 的实际可消费记录计算。队列容量会先保留违约、警报、销毁和社会回应，再保留普通移动/阶段噪声；因此 closure 不会因 FIFO 截断恰好丢失关键后果，也不会把 dormant 的不可自动消费账本算作 blocker。

Timeline commitment 是世界级未决种子，不等同于 Obligation，但默认同样阻塞 closure。`scheduled` 与 `due` 都计入 `active_timeline_commitment_count`；只有 TimelineEngine 根据真实时间和角色位置结算为 `resolved / missed / cancelled` 后才释放。策略可显式关闭 `require_no_active_timeline_commitments`，用于故意截取一个仍有未来日程的章节边界。
- `closure_reached` / `steps_to_closure`：是否在步数上限前达到稳定完结，以及实际用时。
- `agent_goal_adoption_count` / `active_agent_goal_count`：角色是否从已发生后果继续形成新目标，以及 Episode 结束时仍有多少这类追求。
- `agent_goal_refinement_count` / `active_open_agent_goal_count`：开放目标有多少真正成熟为 Host 可验证目标，以及 Episode 结束时还有多少自主目标仍只有动机、没有具体完成路径。refine 以 `goal_refinement:<actor>:<goal>:step:<n>` 进入因果图；后续 Goal resolution 指向该节点，而不是假装目标从创建起就已经具体。

`NavigationState` 现在属于 Episode material snapshot。问题创建和解决分别形成
`navigation_problem_created/resolved` 不可逆 trace，影响角色 hash，并可作为
Agent-grown Goal 的显式来源。每个问题还保存 Host 的 `failure_rule`，所以
`stale_route/movement_blocked -> NavigationProblem -> Goal` 可以形成完整的两跳
provenance 链。报告只接受组件中已有的 source type/ref、Goal
source 和 Event response id，不用 LLM 或字符串相似度猜测两个事件“看起来有关”。
Obligation 创建现在也形成 `obligation_created:<actor>:<id>` trace，并从其宿主来源
生成 `Obligation <- Agreement/resolved_action/scenario/original Obligation` 边；义务
终态事件使用 actor-qualified ref，因此可以继续形成
`WorldEvent <- Obligation <- Agreement`，而不会因不同角色复用 obligation id 串错链。
Agreement 创建同样形成 `agreement_created:<id>`，并从 Entity 上宿主保存的
`resolved_action:step/actor` 生成来源边。缺少该边时报告标记
`unattributed_agreement`；`agreement_creation_count` 与
`obligation_creation_count` 分开统计，避免把“提出承诺”和“承诺成交后产生责任”
混成一次变化。

Agreement 终态转换形成 `agreement_resolution:<id>:<status>` 节点。该节点同时
指向原 Agreement 与实际解决它的 resolved action（自动过期则指向 clock）；同轮
materialize 的服务义务再指向 settled resolution。因果深度算法保存一个节点的全部
显式 parents 并取最深路径，不再因后写入的一条浅边覆盖较早的深边。

服务履约终态另形成
`agreement_performance_resolution:<id>:<fulfilled|breached|cancelled>`。它指向
settled Agreement resolution，并逐条指向最终承担者的 actor-qualified Obligation；
performance WorldEvent 再以该节点为 source。最小托管服务因此可以审计
`propose action → Agreement → settlement → Obligation → performance resolution → WorldEvent`，
而不是用 Agreement 的存在本身解释违约。

托管 lot 首次进入 held 状态时形成 `agreement_escrow:<agreement>:<custody>`；
release/refund 时形成带 disposition 的 `agreement_escrow_resolution`，父节点同时包含
原 custody lot 与相应 performance resolution。escrow WorldEvent 的 source ref 包含
agreement、custody id 和 disposition，多个托管资产即使同轮结算也不会串链。

exchange WorldEvent 保留 exchange id 作为事件 identity，但来源按结算路径区分：
协议 transfer 指向 `agreement_resolution:<id>:settled`，普通双边交换指向
`resolved_action:step:<n>:actors:<sorted parties>`。因此 `exchange:<id>` 不再作为
没有父节点的伪因果根。

Timeline 出席/缺席事件指向 `timeline_resolution:<commitment>:<status>`。该节点的
显式 parents 包括 `timeline_commitment:<id>`、`clock:step:<n>`，以及约定地点上的
`actor_presence/actor_absence`。因此 `Timeline seed → 时间/位置结算 → WorldEvent →
event response → Goal` 可以作为完整链进入深度和 replay 审计。

KnowledgeState 中 Claim 首次出现或 updated step 改变会形成
`claim_knowledge_learned/revised:<actor>:<claim>`。observed 记录生成
`ClaimKnowledge <- EvidenceObservation <- resolved_action + Evidence`；reported 记录生成
`ClaimKnowledge <- ClaimReport <- speaker resolved_action`。Claim-derived Agent Goal 指向
actor-qualified ClaimKnowledge。缺少对应边时报告标记
`unattributed_claim_knowledge`，并通过 `claim_knowledge_change_count` 单独计数。
若一个 Agent Goal 或 WorldEvent 已创建却没有对应 provenance edge，报告会标记
`unattributed_agent_goal` / `unattributed_agreement` / `unattributed_obligation` /
`unattributed_claim_knowledge` /
`unattributed_world_event`，防止随机状态噪声被误当成
自然涌现链。

自然目标的结算同样进入 `goal_resolution_count`。其中物理模板读取 SceneState，履约模板读取角色自己的 ObligationState，协议模板只读取角色实际参与的 Agreement Entity，调查模板要求 KnowledgeState 中出现 Claim-linked Evidence，关系模板要求 Pair Relationship 真正产生 `acquainted` Bit；评估器不会把 Agent 的自然语言声明当成完成证据。

报告保存 Session seed，因此相同内容、runtime、seed 和宿主规则可以重放。它不会把“动作多”“关系多”误当成有趣故事。这些指标仍然只是结构性证据：相邻响应链不等于完整因果解释，目标参与不等于目标达成，状态变化也不自动等于好故事。

测试中的最小 Episode 只有两个独立 Agent、一个房间和各自目标，没有 Storylet 或人物专属核心逻辑。四轮后由真实互动惰性形成 Pair Relationship、双向 Sentiment 和角色经验，用来防止引擎退化成“单轮文本生成器”。

另一个无 Storylet 的最小事件响应 Episode 从一次 Timeline 缺席开始：宿主创建 Event Entity，离屏当事人被 pending observation 唤醒并形成 `respond_to_event(explain)` 目标，真实解释写入权威 response ledger；接收者先作出 acknowledge，再形成可验证的移动目标。新事件和回应已经消费后，接收者仍会在受限间隔后单独以 `agent_goal:<id>` 醒来并前往现场。这个回归用于证明“世界变化 → 局部知识 → 自主社会回应 → 私有评价/关系后果 → 多步目标续行动 → 稳定 closure”可以完整走通。

最小托管服务 Episode 使用真正返回多候选的两个 Agent，并由宿主带种子采样。固定 seed 下，它验证 `Agreement resolution → Obligation → host-sampled interact → 两步后完成的 ResolvedAction → 物品移动 WorldEvent`，同时保留 `Goal → ResolvedAction` 的第二父边；最长显式链至少为 8。这个回归不是调用静态提取器拼快照，而是运行普通 Session 的完整动作队列、事务、协议、义务、策略和事件系统，用来防止跨 step 动作把完成轮的新 Policy Trace 错当成选择时动机。

单 seed 报告之上还提供 `EpisodeSweepRunner` 和宿主 launcher，用于汇总多随机种子的停滞率、僵局率、目标结算率、动作轨迹多样性与 replay 一致性。完整协议见 [EPISODE_SWEEPS.md](EPISODE_SWEEPS.md)。
