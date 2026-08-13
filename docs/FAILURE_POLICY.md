# 运行时故障降级策略

故事引擎的生产默认策略是 fail-closed：任何会影响客观世界的解析、模型或运行时故障，都只能产生可诊断的失败并暂停当前权威 step，不得合成角色行动、移动或成功结果。

## 世界结算

`SimulationControl` 在以下情况返回 `simulation_error`：LLM 不可用、结构化输出无法解析、或语义结果遗漏了本轮主体意图。`SimulationSystem` 看到该字段后抛出阶段错误，由 Runner 保留 step 前快照并回滚；用户或上层调度器可以在不重复结算的前提下重试。遗漏主体不会再通过 `_fallback_result()` 补写成功行动。

确定性规则结算仍然可用，但必须显式安装 `HostRuleSimulationControl`。它是离线 baseline，不是 LLM 故障时的隐式替代品。

## 角色运行时

普通 `LLMCharacterAgent` 在模型不可用时抛出运行时错误，不生成 observe、wait 或 schedule move。这样角色不会因为基础设施故障自行改变故事。若需要离线角色行为，应注册一个明确命名的规则 runtime。

Hermes 的协议错误继续 fail-closed；Hermes 自己的主体上下文、收件箱和决策账本由 runtime 保留，下一次重试从同一主体状态继续。

## 协议边界

Hermes 和正式结构化 Agent JSON 必须显式提供合法的 `kind`。`AgentAction.from_value(..., strict=True)` 会拒绝缺失或非法 kind；自然语言玩家输入和 legacy adapter 仍可使用旧的语义推断入口。

`SceneState.apply_updates()` 只接受 `world_objects`、`actor_states` 和 `scene` 三个顶层 section，未知 section 直接报错，避免把拼写错误误当成世界对象更新。

叙事渲染仍允许事实型文本降级，因为它只生成表现层文本，不改变权威世界状态。
