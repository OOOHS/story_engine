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

完结资格只由权威状态推导：已有的可验证 Goal 已结算、没有尚未出席/错过/取消的 Timeline commitment、没有可自动处理的活动 NavigationProblem、动作队列为空，并且没有尚未交付给可自动运行角色策略的 WorldEvent 或 event response；内容需要时还可要求 Plot 完结。默认不强迫内容包预写至少一个可验证 Goal，因此纯世界种子可以在所有自然生长的线程真正平息后结束；传统任务式评测可用 `require_goal_anchor=True` 或 launcher 的 `--require-goal-anchor` 明确要求人工目标锚点。每个 autonomous、非 dormant 的角色还必须至少真正获得过一次决策机会；`AgentController.decision_count` 是可回滚的 Host 状态，离屏角色仍按错峰 schedule 运行，但 closure 不会抢在其第一次背景 turn 前发生。章节式审计若确实允许忽略尚未运行的远端角色，可使用 `require_all_autonomous_agents_exercised=False` 或 `--allow-unexercised-agents`。显式 dormant/autonomy-disabled 角色的旧 pending attention 和 NavigationProblem 会单独计入诊断，但不会永久阻塞 closure，因为它只能由人工恢复；新事件仍进入其 belief/experience，却不会越过策略创建自动 interrupt。临界 Drive need 只有在角色当前 POV 中存在 Host 已验证、available 且确实降低同一 need 的对象能力时才阻塞 closure；普通压力漂移、当前没有结构化解决办法的压力和 dormant 角色的压力只进入诊断，避免永久卡死。章节式截断可用 `require_no_actionable_critical_needs=False` 或 `--allow-actionable-critical-needs-closure` 放宽。资格必须连续保持 `stable_steps`，且默认这些步骤不能继续产生 `scene / plot / relationship / goal / knowledge / navigation / claim / world_event` 结构变化；最终目标刚结算、物品刚转手或角色仍在形成新 Claim knowledge 的那一步不会被误算作安静尾声。持续 Drive 漂移、记忆归档等低层变化不单独阻塞；章节式评估若有意在重大变化后立即截断，可设置 `require_stable_material_state=False` 或 launcher 的 `--allow-material-change-closure`。Agent 与 GM 都不能输出 `story_complete=true` 来绕过这些条件。

Episode 完结不等于世界关闭。它只是说明本次评估已经形成一个可停止的叙事边界；同一个 Session 仍可继续运行，角色也可以保留感谢、怨恨、伤痛等未消退状态。未传 `closure_policy` 时，运行器仍精确执行 `steps` 轮。

每个 step 还保存最多 12,000 字符的 `narrative_text`。它只取 RenderingSystem 已经完成最小权限投影后的玩家可见 narration，不保存 Agent thought、private_result、完整 GM packet、隐藏关系值或 Storylet/Plot director 信息。这样 Episode JSON 可以同时用于结构审计和人工/外部模型的文本质量评审，而不需要重新运行世界或读取后台状态。

每步记录：

- simulation time 前后值；
- 权威世界与角色私有状态的稳定 hash；
- proposal actor、resolved actor 和动作类型；
- WorldStateTransaction 是否提交；
- Pair Relationship、Sentiment 数量；
- 当前激活的临时 Modifier 数量；
- 客观 Claim Entity 数量与所有角色当前持有的 Claim knowledge 数量；
- 客观 WorldEvent Entity 数量，以及本轮新增的不可逆事件引用；
- 排除内部版本号后的实质状态变化种类；
- 物品归属、角色/物品出现消失、Plot 阶段等不可逆变化；
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
- Relation Entity 不能进入 AgentRegistry；
- Scene 中每个行为角色必须同时拥有 Entity、AgentController 和 live runtime；
- live runtime 缺失、概率检查错误、Sentiment 或 WorldEvent 发布错误会成为 Episode violation；
- Intent 的 `policy_candidate_id` 必须与该角色本轮提交收据上的行动一致，确保交给 GM 结算的意图就是角色实际提交的那一个。

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
- `goal_engagement_*` 只表示角色自己把该行动归因于某个目标，不声称自然语言目标已经完成；
- `commitment_resolution_count` 目前为 0：跨时口头承诺不再由宿主状态机结算。没有条件证据时，评估器宁可报告“不知道”，不会让 LLM 自评成功。
- `goal_resolution_count`、`goal_achievement_count` 和 `goal_failure_count` 只统计 `GoalState` 根据权威世界条件产生的生命周期转换；普通自然语言目标没有验证条件时会继续保持 active。

额外指标包括：

- `causal_transition_steps`：宿主因果规则实际触发的轮数；
- `causal_handoff_steps/count`：本轮新增多少条带权威 provenance 的后果承接边，例如 `world_event <- resolved_action/host_transition`、`event_response <- world_event`、`sentiment <- resolved_action`、`relationship track <- sentiment`、`goal_resolution <- goal`、`agent goal <- goal_resolution/world_event/event_response/sentiment/navigation_problem`；resolved Goal 使用带 actor/id/status 的结算事件节点，而不是长期存在的 Goal 实体，因此“上一轮完成旧目标、下一轮长出后续目标”会被正确识别为跨 step 因果；
- `motive_handoff_count` / `motivated_action_count`：有多少条已提交的行动被角色自己说明了动机。宿主不再选择角色的行动，因此也无法重建“她为什么这么做”；这些边只来自角色自报的 `motive_refs`，且必须先通过 InputSystem 对照她实际持有的 Goal、Sentiment、Drive need 校验。引用她并不持有的东西会被丢弃而不是采信，评估器也不会从行动文案反猜动机；
- `decision_count` / `stated_motive_count` / `rejected_motive_ref_count`：本 Episode 有多少次角色决策、其中多少条附带了通过校验的动机自述、以及多少条动机引用因为角色并不持有而被驳回。驳回数持续偏高说明 runtime 在编造自己的内部状态；
- `causal_source_kind_count`：Episode 实际使用了多少种不同的权威来源类型，避免仅用动作/状态变化数量冒充因果丰富度；
- `causal_consequence_node_count`：显式因果图中有来源的后果节点数量；
- `max_causal_chain_depth`：沿 `consequence <- source` provenance 图得到的最长链深度；例如 `Goal <- NavigationProblem <- movement_failure` 深度为 2。算法只遍历显式边并对循环 fail-safe，不尝试从自然语言补边；
- `cross_step_causal_handoff_count` / `cross_step_causal_step_count` / `max_causal_span_steps`：因果父节点先在某轮成为真实 consequence、子节点在后续轮才出现的传播数量、涉及步数和最长时间跨度。它把“同轮一次性派生八层账本”与“事件在下一轮形成目标、目标再驱动行动、行动继续产生后果”区分开；Episode 只使用显式 provenance 和 consequence 首次出现 step，不从文案猜测先后关系；
- `causal_arc_present`：至少一条显式因果链跨越两个 Episode step；`resolved_causal_arc` 还要求该 Episode 达到 Host closure 且发生过不可逆状态转换。它们是结构诊断，不宣称文本必然有趣，也不把同轮原子事务错误拆成多个戏剧阶段；
- `narrative_step_count` / `narrative_character_count` / `unique_narrative_step_count` / `narrative_repetition_rate` / `max_narrative_repetition`：前台 transcript 的覆盖、规模、整体重复率和单一模板最高出现次数。已提交 Episode 完全没有 narration 会标记 `missing_narrative_output`；任一归一化 narration 出现四次以上会标记 `repetitive_narration`，即使中间夹有少量不同文本也不会漏报。这些指标不评价文风优劣，但能发现 renderer 失效和明显模板循环；
- `material_stability_blocked_steps` / `terminal_material_change_count`：启用自然闭合时，有多少步因为仍在发生结构变化而不能进入安静窗口，以及 step limit 最后一轮仍有多少类变化。若 Episode 未闭合且最后一步仍被这一条件阻塞，会额外标记 `materially_active_at_step_limit`；它表示故事仍在发展，不等同于 deadlock；
- `actionable_critical_need_blocked_steps` / `terminal_actionable_critical_need_count`：多少步因为 autonomous 角色仍有临界且眼前可缓解的 need 而不能闭合，以及上限时仍剩多少项。持续到 step limit 会标记 `actionable_critical_needs_at_step_limit`；无可见结构化解决办法或 dormant 角色的 need 分别只进入 closure details 的 `unactionable_critical_need_count` / `dormant_actionable_critical_need_count`；
- `max_repeated_policy_action_count`：任一角色连续提交同一语义行动的最大次数。达到四次会标记 `repetitive_policy_choices`；它比只看粗动作类型的 `repetitive_actions` 更精确，因为连续观察不同对象不会被当成同一个方案，而同义改写无法逃避计数；
- `irreversible_change_steps/count`：产生不可逆局势变化的轮数和事件数；新建/被新事实更新的 Sentiment 与有新 provenance 的 Relationship Track/Bit 计入，单纯衰减和到期不计入；
- `modifier_change_count`：由新权威事实创建或更新的 Modifier 数量；单纯到期不计入。若存在变化却没有 `modifier <- source`，报告标记 `unattributed_modifier`；
- `drive_need_cause_count`：新增的 `drive_need <- resolved_action/clock` provenance 数量；need ledger 本身属于宿主审计状态，不进入角色或玩家视图；
- `interaction_chain_steps` / `longest_interaction_chain`：本轮行动是否继续响应上一轮涉及的角色或对象；
- `actor_differentiation`：角色动作类型分布的平均差异，范围为 0～1；
- `goal_engagement_rate`：存在宿主抽样策略时，目标影响所选行动的轮次比例。
- `goal_continuation_steps` / `goal_continuation_actor_count`：离屏角色实际因可验证 Agent-grown Goal 获得续行动机会的轮数与角色数；不把普通 background tick 误记为目标持续性。
- `goal_continuation_attempt_count` / `max_repeated_goal_action_count`：累计续行动尝试和单角色最高重复签名次数，用于发现低频但永久的目标循环；这些值是调度诊断，不是故事进展或失败证明。
- `goal_reactivation_steps` / `goal_reactivation_actor_count` / `goal_reactivation_count`：多少轮、多少角色以及累计多少次因为 POV-safe 的相关事件解除目标退避；用于区分真实条件变化与无关世界噪声。

Event pending 数量按宿主优先 attention queue 的实际可消费记录计算。队列容量会先保留警报、销毁和社会回应，再保留普通移动/阶段噪声；因此 closure 不会因 FIFO 截断恰好丢失关键后果，也不会把 dormant 的不可自动消费账本算作 blocker。

Timeline commitment 是世界级未决种子，默认同样阻塞 closure。`scheduled` 与 `due` 都计入 `active_timeline_commitment_count`；只有 TimelineEngine 根据真实时间和角色位置结算为 `resolved / missed / cancelled` 后才释放。策略可显式关闭 `require_no_active_timeline_commitments`，用于故意截取一个仍有未来日程的章节边界。
- `closure_reached` / `steps_to_closure`：是否在步数上限前达到稳定完结，以及实际用时。
- `agent_goal_adoption_count` / `active_agent_goal_count`：角色是否从已发生后果继续形成新目标，以及 Episode 结束时仍有多少这类追求。
- `agent_goal_refinement_count` / `active_open_agent_goal_count`：开放目标有多少真正成熟为 Host 可验证目标，以及 Episode 结束时还有多少自主目标仍只有动机、没有具体完成路径。refine 以 `goal_refinement:<actor>:<goal>:step:<n>` 进入因果图；后续 Goal resolution 指向该节点，而不是假装目标从创建起就已经具体。

`NavigationState` 现在属于 Episode material snapshot。问题创建和解决分别形成
`navigation_problem_created/resolved` 不可逆 trace，影响角色 hash，并可作为
Agent-grown Goal 的显式来源。每个问题还保存 Host 的 `failure_rule`，所以
`stale_route/movement_blocked -> NavigationProblem -> Goal` 可以形成完整的两跳
provenance 链。报告只接受组件中已有的 source type/ref、Goal
source 和 Event response id，不用 LLM 或字符串相似度猜测两个事件“看起来有关”。

exchange WorldEvent 保留 exchange id 作为事件 identity，来源指向
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
`unattributed_agent_goal` /
`unattributed_claim_knowledge` /
`unattributed_world_event`，防止随机状态噪声被误当成
自然涌现链。

自然目标的结算同样进入 `goal_resolution_count`。其中物理模板读取 SceneState，调查模板要求 KnowledgeState 中出现 Claim-linked Evidence，关系模板要求 Pair Relationship 真正产生 `acquainted` Bit；评估器不会把 Agent 的自然语言声明当成完成证据。

报告保存 Session seed，因此相同内容、runtime、seed 和宿主规则可以重放。它不会把“动作多”“关系多”误当成有趣故事。这些指标仍然只是结构性证据：相邻响应链不等于完整因果解释，目标参与不等于目标达成，状态变化也不自动等于好故事。

测试中的最小 Episode 只有两个独立 Agent、一个房间和各自目标，没有 Storylet 或人物专属核心逻辑。四轮后由真实互动惰性形成 Pair Relationship、双向 Sentiment 和角色经验，用来防止引擎退化成“单轮文本生成器”。

另一个无 Storylet 的最小事件响应 Episode 从一次 Timeline 缺席开始：宿主创建 Event Entity，离屏当事人被 pending observation 唤醒并形成 `respond_to_event(explain)` 目标，真实解释写入权威 response ledger；接收者先作出 acknowledge，再形成可验证的移动目标。新事件和回应已经消费后，接收者仍会在受限间隔后单独以 `agent_goal:<id>` 醒来并前往现场。这个回归用于证明“世界变化 → 局部知识 → 自主社会回应 → 私有评价/关系后果 → 多步目标续行动 → 稳定 closure”可以完整走通。

单 seed 报告之上还提供 `EpisodeSweepRunner` 和宿主 launcher，用于汇总多随机种子的停滞率、僵局率、目标结算率、动作轨迹多样性与 replay 一致性。完整协议见 [EPISODE_SWEEPS.md](EPISODE_SWEEPS.md)。
