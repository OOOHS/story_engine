# 主观状态 Ownership

本文件定义持久 Hermes subject 与 Host/ECS 之间的唯一主观状态边界。目标不是把所有私人数据移出 Host，而是区分：Host 可以证明角色接触过什么，以及角色实际上如何记忆、理解、感受和打算。

## 核心规则

```text
Host private ledger = POV-safe、可验证、会参与结算或调度的私人记录
Hermes subject mind = 回忆、注意、评价、情绪、信念解释、计划、动机和选择
```

“private”不自动等于“属于 Hermes”。一项信息即使只有单个角色可见，只要 Host 必须用它防止读心、伪造知识或自报目标完成，就必须保留一份权威私人记录。反过来，Host 不能因为某个心理描述有助于策略，就把它升级成角色真实感受。

## 字段归属

| 状态 | Host 保存什么 | Hermes 保存什么 | Hermes 接收方式 |
|---|---|---|---|
| 身体与位置 | 当前身体状态、位置、能力和物理条件 | 对身体状态的体验与解释 | 每次 wake 的 embodied allowlist；`fear/mood/focus` 不可借 self-state 进入 |
| 亲历事件 | 事件 ID、来源、时间、地点、witness mode、可验证 statement | 事件如何被记住、联想和重构 | `ledger_update:epistemic_record` 与 stimulus |
| Claim 知识 | stance、confidence、basis、source、evidence refs | 相信程度的主观意义、怀疑和推理 | 增量 `claim_position` |
| 地图与路线 | 已观察或合法获知的地点、边和来源 | 路线偏好、担忧和策略 | 增量 `known_map/navigation_problem` |
| 日程 | 条款、参与者、期限与出席/缺席证据 | 是否赴约、重视、怨恨或回避 | 增量 ledger；状态消失时 retraction |
| Goal | 已登记目标、来源、调度信息和隐藏完成锁 | 真正欲望、目标层级、注意与放弃原因 | `goal_registration`；Hermes 可提交一个 `goal_requests` 登记请求 |
| Drive/Modifier | 身体或规则压力、有效期及来源 | 主观感受、意义和应对倾向 | `drive_signal/condition_signal`，不是情绪声明 |
| Trait | 稳定倾向参数 | 当下表达、冲突和自我理解 | 仅 identity bootstrap |
| SentimentState | 兼容策略和关系规则使用的社会响应代理 | 角色真正的情绪与 appraisal | 不投递 SentimentState；Hermes读取底层经历 |
| Planning/focus | 仅兼容非 subject runtime 的镜像 | 完整计划与当前关注 | Hermes 不读写 Host Planning/focus |
| 长期记忆 | 有界 Cognition/WorldEvent 收据，用于 POV 与审计 | conversation、JSON memory、私人笔记和主动回忆 | Hermes 不使用 Host Chroma 检索/归档 |
| 私人承诺/笔记 | 初始内容的 legacy seed；不再由 Hermes 回写 | 后续承诺、笔记和记住的线索 | 初次作为 legacy ledger seed，之后由 Hermes 原生记忆维护 |

## 增量协议

`SubjectLedgerProjector` 为每个角色维护上一版投影。新记录或内容变化产生：

```json
{
  "kind": "ledger_update",
  "payload": {
    "category": "schedule_commitment",
    "ref": "schedule:evening-gathering",
    "revision": 2,
    "record": {}
  }
}
```

记录不再 active、可用或存在时产生 `ledger_retraction`。消息拥有稳定引用和单调 revision；Hermes turn 失败时消息留在 `SubjectInbox`，只有形成合法 decision 后才确认。未变化的记录不会每轮重复发送。

Ledger message 是证据或约束，不是心理命令。`goal_registration` 不表示目标此刻一定有最高优先级，`drive_signal` 不规定角色必须采取哪种缓解方式，关系状态也不规定角色必须喜欢或信任对方。

## Hermes 输出边界

Hermes 对 Host 只输出：

1. 一个最终 action。Hermes 在自己的长程上下文中完成候选生成、权衡和随机性，宿主看不到中间候选，也不重排；
2. 可选的一个 `goal_requests` 登记请求，用于让 Host 编译完成证据、安排 wakeup 或审计进度；
3. 可选的 `sentiment_updates`：她现在对某人的感受。这是她自己的账目，不是登记请求——GM 最没有资格代她决定这件事；
4. 可选的 `motive_refs`：这一步是为了她自己的哪个目标、感受或需求。宿主既然不替她选行动，就无法重建理由，只能由她说。

第 3、4 项都会被 Host 校验并限量后才能触碰权威状态或因果审计：引用她并不持有的东西会被丢弃而不是采信。

Hermes 不向 Host 输出或同步 `plan`、`focus`、`belief_updates`、私人 commitments、emotion、reflection 或 memory state。即使响应中包含这些字段，`HermesCharacterAgent` 也不会把它们写入 ECS。

## 没有兼容路径

引擎不再提供任何“宿主替角色思考”的 runtime。`CharacterPolicy` 和 `LLMCharacterAgent` 已删除，宿主不保留角色心智的第二份副本，也没有可以静默退化到的候选打分路径。`AgentController.config.subjective_state_owner=runtime` 让未来非 Hermes 的持久 runtime 使用同一边界。

## 尚未完成

- Hermes 内部的 Appraisal、Global Workspace、刺激衰减和注意竞争仍需实现；
- Host 的 `SentimentState` 仍参与长期 Relationship 规则，未来应改名为更准确的 social-response proxy，或用直接社会证据替代；
- 外部 Hermes 进程崩溃后的原生记忆恢复、identity replay 和幂等重试尚未形成完整协议；
- 目前 ledger 在 Hermes 被调用时投影，并非 WorldEvent commit 时真正异步推送。

这些缺口不能通过重新把完整 Host cognition 拼进 prompt 来规避。
