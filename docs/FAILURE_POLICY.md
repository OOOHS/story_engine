# 运行时故障降级策略

故事引擎的生产默认策略是 fail-closed：任何会影响客观世界的解析、模型或运行时故障，都只能产生可诊断的失败并暂停当前权威 step，不得合成角色行动、移动或成功结果。

## 世界结算

`SimulationControl` 在以下情况返回 `simulation_error`：LLM 不可用、结构化输出无法解析、或语义结果遗漏了本轮主体意图。`SimulationSystem` 看到该字段后抛出阶段错误，由 Runner 保留 step 前快照并回滚；用户或上层调度器可以在不重复结算的前提下重试。遗漏主体不会再通过 `_fallback_result()` 补写成功行动。

确定性规则结算仍然可用，但必须显式安装 `HostRuleSimulationControl`。它是离线 baseline，不是 LLM 故障时的隐式替代品。

## 角色运行时

角色 runtime 在模型不可用时应抛出运行时错误，不代替角色生成 observe、wait 或 schedule move。这样角色不会因为基础设施故障自行改变故事。引擎不自带任何进程内 LLM runtime 作为兜底；若需要离线角色行为，应注册一个明确命名的规则 runtime。

Hermes 的协议错误继续 fail-closed。本地 Hermes 的主体上下文落在隔离的 `HERMES_HOME` 里；权威 step 回滚会恢复 Host ledger 与这份 subject 快照，下一次调用从回滚后的主体状态继续。Docker 传输已废弃，不能用于需要恢复会话的生产路径。

## 协议边界

玩家和 Hermes 主体以非空自然语言 `action` 提交意图；Host 再生成内部 `AgentAction`。内部结构化接口使用 `AgentAction.from_value(..., strict=True)` 时必须提供合法 `kind`。生产动作由现有结算模型依据主体可见上下文解释成 kind 与目标引用，再进入动作调度。否定、转述、假设、别名及复合表达按语义解释；解析故障回滚步骤。显式离线模式的简化文本解析隔离在 rules/offline_semantics.py 与 rules/offline_targets.py。

世界事件的物化和角色通知属于权威步骤。`WorldEventSystem` 报告发布错误时，Runner 回滚整个步骤，恢复世界、动作队列和主体上下文。回滚同时关闭本步骤新增的角色 runtime；初始化中途失败也释放已创建的 runtime。

提交后的交付阶段失败会恢复该阶段开始时的完整上下文与组件状态，待重试记录保留原始已提交事实。重试继续 Rendering、导演记录或 Memory 交付，世界时钟和角色决策次数保持原值。

Dispatcher 在提交时将整批事件保留到事件记录，并尝试通知所有订阅者。订阅者异常会报告 `DispatcherCommit` 交付失败，并保留后续玩家叙述的重试入口。已经调用的外部订阅者各自负责其副作用与重试；`retry_delivery` 从 Rendering 继续执行。

`SceneState.apply_updates()` 只接受 `world_objects`、`actor_states` 和 `scene` 三个顶层 section，未知 section 直接报错，避免把拼写错误误当成世界对象更新。

生产 `NarrativeRenderer` 生成叙述后，使用同一叙述模型校对最终交付文本与玩家可见事实，覆盖动作、状态、因果、来源、时间、地点和知识边界。拒绝时语义修正一次并再次校对；服务故障、协议错误或再次拒绝时 Rendering 交付失败；已提交世界保持原状，Session 暂停下一步并允许重试交付。规则/模板叙述只通过显式 `narration_mode="rules"` 使用。作者散文的初始提取或来源语义校对失败同样在 Session 创建前报错，不会悄悄生成一个通用玩家与起始场景。

持续 `StoryPlanner` 只在玩家消息成功生成后按间隔询问。模型或工具协议错误保留玩家叙事积累，写入 `story_planner_status=failed`；已提交世界继续有效。渲染失败时导演等待 delivery retry；Memory 重试保持已经记录的导演上下文。新增故事块通过结构检查后登记，草案状态变为 accepted/rejected；登记失败的草案留待后续交付阶段重试。具体后果由世界结算与提交前语义校对检查，同一草案只登记一次。导演建议保存在接收者 Cognition 待收取队列；权威步骤失败会恢复队列，Hermes 成功回应后只确认本次实际发送的建议，队列随存档恢复。


生产故事块的独立追踪会话由 StoryTracking 管理。每次已交付玩家消息后，逐个追踪者读取共同叙事的新增部分及自身结算回执；模型故障或协议错误保留既有会话、进度、未读位置和待结算建议，仅记录 retry_pending。其他追踪者继续运行。相同 narrative_turn_id 只尝试一次，后续新交付触发重试；未读叙事在重试时一并提供。世界事务成功才确认待结算建议与回执，权威步骤失败由现有 Runner checkpoint 恢复；渲染失败推迟追踪，Memory 交付重试保持已成功调用的追踪结果。completed/abandoned 会话停止调用，状态与历史保留用于存档与导演的进展参考。

生产拓扑变化与动态物品能力通过完整世界的提交前语义校对。结构引用和容量错误回滚整批变化；语义校对拒绝时可以在原始意图下修正一次。定向交流复用 WorldEvent 的注意力、存档和回滚机制，消息事实保留说话人来源。
