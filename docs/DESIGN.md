# 项目设计说明

## 一、产品层与引擎层

当前项目有两个不同层级的目标：

- 产品层：先交付一个简单、能玩的终端文字冒险游戏。
- 引擎层：逐步收敛为一个“状态与叙事严格解耦”的故事引擎。

这意味着外层体验可以先朴素，但内层架构不能继续把“状态判定”和“文案输出”混在一次 LLM 调用里。

## 二、核心原则

### 1. 世界状态优先

- 世界的真实状态由 ECS 组件承载。
- ECS 默认按组件类名占槽；可替换实现用 `component_slot` 声明稳定协议槽，例如 `HostRuleSimulationControl` 占用 `SimulationControl`。替换时旧实例解除 Entity 回指，checkpoint 仍按实际组件类型恢复，系统无需识别具体实现类
- 文本只是这些状态的表现层，不是状态本身。
- 任何会改变世界的结算，都必须先落到结构化状态上。

### 2. Storylet 提供未来的条件剧情

- Storylet 由内容、前置条件和执行后对世界的影响组成；可写具体场景或参数化情境。
- `conditions` 决定资格，`intent` 描述未来的外部处境、事件及预期影响。角色如何回应由自己的 Agent 决定。
- 本项目在条件满足后将故事块编为 World 意图，语义结算决定其实际后果，事务提交后记录命中并交付可感知事实。
- 定义参考 [Emily Short](https://emshort.blog/2019/11/29/storylets-you-want-them/) 与 [Failbetter](https://www.failbettergames.com/news/storynexus-developer-diary-2-fewer-spreadsheets-less-swearing)。

### 3. 导演服务于玩家叙事

- `StoryPlanner` 维护独立持续会话，积累公开开场、玩家输入和面向玩家生成的世界消息。
- `StoryPlanningSystem` 位于 WorldEvent 提交屏障与 Rendering 之后；默认每 5 个已生成玩家世界消息的回合询问一次。`story_planner_interval_turns` 配置回合间隔，`story_planner_interval_seconds` 配置时间间隔，任一到期时在下次世界消息生成成功后询问。
- 导演通过 `propose_storylet` 工具提交面向未来的新外部情境或事件，包含条件与预期影响，每次最多两项；累计叙事帮助判断戏剧机会，已有目录和草案用于一致性与去重。角色言论保留来源及待定真实性。
- 草案保存在 `pending_story_planner_proposals`，结构校验后原子登记到动态 Storylet 池，并记录 accepted/rejected。登记只增加未来机会，实际影响在后续触发结算中产生。
- `StoryletExecution` 在 Simulation 中检查已有故事块的条件并提交 World 意图；每个触发必须得到明确结算，事务成功后才记录消费并通过 WorldEventSystem 通知见证角色。角色自主回应。
- `StoryPlanner` 专职提出新故事块；NarrativeRenderer 专职生成玩家文本；两者与已有故事块的执行相互独立。
- 回合计数表示权威步骤已提交且玩家世界消息已生成。客户端送达确认属于单独的传输语义。渲染重试保持相同 turn id，计数去重；生产故事块由独立追踪会话判断 completed/abandoned 后退出；one_shot 完成后消费。

### 4. 角色是 Agent，但 Agent 不是裁判

- 每个角色实体通过 `AgentController` 声明自己的 agent runtime。
- live runtime 注册在 session 级 `AgentRegistry`，不进入 ECS 持久状态。
- agent 只能读取自己的 `AgentPerception`，不能读取完整权威状态。
- agent 输出的是行动 proposal；只有 Simulation 可以决定结果和写回世界。
- Hermes、直接 LLM、人类控制器或未来的规则 agent 都通过同一接口接入。
- 同场角色逐回合运行，离屏角色低频或事件驱动运行，休眠角色不消耗推理。
- Host 可验证的私人知识收据存放在 `Cognition/KnowledgeState`，不能混入公开或物理 `SceneState`；持久 Hermes 的回忆、解释、情绪、计划与秘密笔记属于其原生 subject memory，不在 ECS 双写。

### 5. 玩家与 Agent 对称

- 玩家不是特权 actor，而是共享同一套感知/行动接口的一个 proposer。`InputSystem.build_agent_perception()` 是玩家和 NPC 共用的唯一感知构造函数；`Runner.get_agent_decision_context()`（供人类 UI 使用）直接调用它，不另建一条"玩家专属"通路。
- 行动语法同样共用：人类手输与 agent 决策都先落到同一个 `AgentAction`（`parse_natural_language_action`），再经过同一段 `bind_action_target` 与 affordance/claim/delivery/route 合法性校验；玩家不能引用 NPC 语法上引用不到的对象或人，反之亦然。
- 感知的可见性边界对双方同规则：同地点过滤、同批次未结算 proposal 互相不可见（`InputSystem.build_agent_perception` 里的批次过滤，不为 `is_player` 开例外）、`ongoing_actions` 只暴露公开可见的动作类型与目标。
- 允许存在、且应当保留的不对称，仅限于"注意力预算"这一层，不涉及世界模型或交互语法：
  - `AgentScheduler` 让玩家永远 `foreground`（每步都被询问），后台 NPC 按周期/事件唤醒——这是响应性预算分配，不是信息或语法特权；
  - `ProposalArbiter`/`_proposal_priority` 给玩家手动输入更高排序优先级——只影响同批次内的呈现顺序，不影响谁能看见什么、谁能做什么。
- 新功能凡涉及"玩家如何感知世界/如何与世界交互"，必须先证明同一条路径对 NPC 也成立；反之亦然。任何一次只让其中一方可用的能力，都是需要显式论证的例外，而不是默认可接受的实现捷径。

### 6. 引擎不认识具体故事

- 引擎系统不得通过角色姓名、地点名称或题材标签触发专属代码。
- 剧本效果必须由初始状态、人物目标、关系、通用 storylet 条件和可复用规则表达。
- 一个新故事应主要通过新增 ScenarioConfig 或外部内容包完成，而不是修改核心系统。
- `src/story_engine/scenarios` 只保存通用配置 schema；完整示例故事位于独立的 `src/story_engine_content/bundled`，依赖方向只能是 content → engine，引擎源码不得反向 import 或选择默认内容。

### 7. 初始设定是作者输入

- 生产入口对自由散文使用 GM 模型做一次受限提取：每个角色、地点、对象、客观命题和关系都必须引用原文连续片段。模型没有写 ECS 的权限；提取结果先编译成 `ScenarioConfig`，再通过 seed 交叉引用和 Session bootstrap 的角色身体/地点不变量。
- 完整作者原文放在 `ScenarioConfig.private_author_premise`，只进入语义 GM 和事后导演；自由散文的公开 `initial_state/description/environment` 从已验证地点构造。这样人物秘密不会从原始设定经 Web 开场或 Narrator 环境提示提前泄漏。
- 完整 JSON/YAML 和同时声明角色、地点的行导向 seed 直接走确定性编译。离线 profile 始终确定性编译。若作者明确给出“每个人都有秘密”之类的创作许可，GM 须生成每人一条有来源标记的私有 Claim；缺人或未确认行动角色的当前位置时，编译失败。角色未写明的动机留给 Hermes 主体形成；玩家自己的已知 Claim 通过有界 POV 投影进入界面。
- 运行中的实体补全通过本轮语义结算与原子事务进入世界；作者显式注入仍可使用一次性授权。初始状态和运行时确认的事实共同受 ECS、可见性与行动结算约束。

## 三、当前主循环

```text
Input
  -> 收集玩家/NPC 意图
Action Scheduling
  -> 原子动作进入离散事件队列，推进到最早完成批次
Simulation
  -> 根据绝对状态与结构化约束做结算
Claim / Goal / Modifier / Relationship / Drive
  -> 根据已提交事实推进跨回合生命周期与私有动机
Cognition
  -> 按角色位置与可见性归档结构化亲历结果
Rendering
  -> 把已确定事实渲染成文本
Memory
  -> 归档本轮结构化结果与文本表现
```

Agent 外部动作固定为 `observe / move / interact / communicate / wait` 五种抽象类型，具体参数保留自然语言。环境负责动作持续时间、硬前提和并发；Simulation GM 只补充规则无法穷举的语义结算。主动 `observe` 与环境自动投递的被动观察分离，私有发现不会进入其他角色或 Rendering。

`Cognition` 与 `Memory` 都是结算后的归档层：前者维护角色私有、结构化的主观经验，后者负责长期检索和上下文压缩。它们不改变已经结算的世界事实。

## 四、关键组件

### SceneState

保存当前客观世界状态：

- `description`
- `world_objects`
- `actor_states`
- `scene_flags`

`actor_states` 是权威宿主表，不等于任何角色都能读取整行。SceneState 提供三种投影：host 读取完整状态；self projection 保留角色自己的能力与身体/心理状态，但去掉 `dramatic_push / pressure_profile / signature_templates / bias` 等导演控制；public projection 默认只公开 location、sub-location、姿态、表情、外观、公开身份、视线和明显站位。未知内容字段默认不公开，内容包可通过宿主只读的 `public_state_fields / private_state_fields` 扩展或收紧；语义事务不能改写这两份声明。无 location 的角色也不会因此得到全局 actor table。

`scene_flags` 同样不是公共公告板。`world_version`、动态实体 ledger、Storylet/causal 消费记录、phase schedule、commitment book 和导演节奏属于 engine/host-private；默认只有 day_phase、weather、alarm、ambient_condition、public_status 进入 `public_scene`。内容包可在 ScenarioConfig 的宿主 schema 中增减 `public_scene_fields / private_scene_fields`，普通 state update 不能改 schema。只有公共字段的真实前后差会形成 `scene_state_changes` 和全局 WorldEvent；私有 flag 仍可驱动宿主规则，但不会自动进入 Agent 或 Narrator。

### SimulationControl

负责 Simulation 阶段：

- 输入：GM semantic snapshot、已提交 `intents`、合法性、当前参与者自身的 Drive、可见关系定性状态，以及 Claim/角色入口等结算目录
- 输出：`resolved_actions`、`state_updates`、`social_impacts`、定性 `conflict_level` 等语义候选；Storylet hit、长期关系、Drive magnitude 和 Drama tension 数值不属于该层输出
- 不确定结果的 success/failure 是语义补丁候选。既有地点移动沿用宿主位置授权；分支中新地点可作为当前 move actor 的候选目的地，选中后须通过图引用校验及模型的因果/自主权校对。
- Storylet 条件成立后由 StoryletExecution 提交 World 意图，ConflictPressure 提供可选参考；角色行动仍严格来自当前 proposal，World 环境发展受权威事务约束。Drama 与 Timeline 已退出主路径
- `ScenarioConfig.rules` 只描述客观世界法则与题材常识；叙事节奏、语言风格和揭示方式属于 `ScenarioConfig.narration`。公开 `environment` 只能包含角色可观察环境，不能混入“仅 GM 参考”的隐藏设定
- 语义 GM 的长期记忆只归档已提交 intent、resolved action、状态/对象事务、交换和 WorldEvent；不保存Host roll、Goal/Modifier 策略诊断或最终渲染文本，避免下一轮检索绕过 resolver 输入隔离

### NarrativeRenderer

负责 Rendering 阶段：

- 输入：已经结算好的结构化结果
- 输出：玩家可见文本
- 约束：不得新增结构化结果之外的事实
- 只接收 public actor projection、可见动作/对象 change ledger 和去除导演字段后的社会关系；`conflict`、`storylet_pressure`、宏剧情/causal rule、内部 simulation notes、bias/framing/territorial 不进入渲染 prompt
- 核心默认只要求事实落地、受限视角和中立清楚，不强制快节奏、慢热、锋利或其他题材风格；内容包可通过 `ScenarioConfig.narration.guidance` 提供可选风格，并用 `max_sentences / max_characters` 声明单回合展示上限
- RenderingSystem 只把已提交、玩家可见的事实交给 NarrativeRenderer。玩家文案由 StoryPlanner 记录；角色 Observation 和 Memory 从各自的可感知事实生成。

### Episode 完结边界

- 完结是 evaluation 层的宿主派生状态，不是 Agent/GM 动作，也不是世界关机指令。
- 默认要求可验证 Goal 全部结算、动作队列为空。
- 条件需连续稳定若干 step 才提前停止，避免把临时空窗当成故事结局；未启用策略时仍执行固定步数。
- Sentiment、Relationship Track 和普通记忆不要求清空。一个完整 Episode 可以带着感谢、怨恨或新的长期关系结束，而世界之后仍可继续演化。

### 已裁剪的节奏辅助层

旧 Timeline 模块及其感知、结算、渲染、评测接口已删除；GameClock、行动队列和行为持续时间负责世界时间与动作调度。DramaState 退出生产装配，旧 `drama` 字段保留内容读入兼容。

## 五、系统职责

### InputSystem

- 从 `AgentRegistry` 调用角色 runtime
- 为每个角色构造受限视角的 `AgentPerception`
- 收集玩家输入和 NPC proposal；World 注入同样等待结算。Input 停止将待结算注入送入角色收件箱，WorldEventSystem 提交后按见证范围投递事实
- `interact` 可引用 perception 中 exact `object_id + affordance_id`；Input 只保留当前 available 且 target 匹配的引用，未知、失效或跨对象引用进入拒绝审计而不进入权威 proposal
- 形成 `context["intents"]`

### AgentScheduler

- 判定角色本轮的 `foreground / background / dormant` 激活范围
- 使用确定性的错峰后台 tick，保证快照重放稳定
- 已提交的世界事件进入角色 attention 后按紧急程度唤醒离屏角色；待结算 World 意图保持不可见
- 不直接产生剧情，也不修改任何世界状态

### StoryletEngine / StoryletExecution

- StoryletEngine 合并静态与已注册动态故事块，按权威状态条件和当前玩家地点投影筛选、排序。
- StoryletExecution 把候选故事块编成带精确来源 id 的 World 意图；自然语言 trigger 由同一世界结算模型判断。每条候选都须得到明确结果，遗漏或重复引用使本步失败并重试。
- 事务提交成功后，正向 World 事件记录本轮命中；追踪者持有的故事块继续推进，完成时消费 one_shot。blocked、failure、deferred、inactive 和被拒绝的事务保留资格。deferred/inactive 保持隐藏且没有事实结果；需要角色选择的部分转为 director_suggestions。
- 生产故事块首次提交事件或可选建议后可以持续推进，启动条件与当前玩家地点只约束启动；追踪者结束后停止产生推进。缺少追踪组件的显式测试宿主保留条件从假转真重新触发的事件基线。触发账本由宿主维护。
- WorldEventSystem 发布已提交事件并向见证角色发送可感知事实，角色自主决定回应；StoryPlanner 通过独立会话提出新的草案。

### StoryTracking

- 每个故事块持有独立 StoryletTracker 会话、阅读游标、自然语言进展、状态和待结算 advance；共享 StoryPlanner 的玩家叙事记录，模型配置与传输复用现有 LLMProvider。
- 静态故事块初始化时建立会话记录；动态故事块登记后的同一交付阶段建立追踪记录。初始方向种入待结算步骤，随后每次玩家文本生成成功后读取新增故事和本故事块的提交回执，提出下一步或等待。
- waiting/active 持续调用，completed/abandoned 退出；导演仅获得状态与进展摘要作为目录信息，子会话历史保持独立。
- 追踪者只提交剧情方向；SimulationSystem 将 pending_advance 编为带 source_storylet_id 的 World 意图，原有结算、提交校对、事件见证与角色建议通路负责具体生效。
- 初始事件或建议提交后，后续步骤标记 continuation，可以跨原始启动条件的变化继续展开。权威步骤失败恢复待结算步骤及回执，已提交的推进在玩家消息成功交付后反馈给追踪者。
- 所有会话与进度是普通组件字段，复用现有 checkpoint 与完整存档。旧生产存档按既有 consumed_storylets 建立已结束记录，保留已有导演叙事。

### WorldEventSystem

- 在 Simulation 与社会状态结算完成后运行，只投影已经提交的客观变化
- 把角色真实移动、普通可观察对象属性变化、物品生命周期和 exchange 物化为独立 `WorldEvent:<id>` Entity，而不是把 aftermath 文本当作全世界共同记忆
- `WorldEventFact` 保存客观 kind、statement、step、location、subjects、objects、visibility、来源和宿主派生的 `impacts`；`WorldEventWitnesses` 只登记直接现场者和事件当事人的 self witness
- `impacts` 是 `scope + target + path` 组成的权威状态失效键，不是故事标签或 LLM 语义判断。对象移动、所有权、可见性、容器开闭等由各宿主系统确定性投影；容器开闭还会投影到内部对象的 accessibility/visibility，因此间接但真实的机会变化可以被目标感知
- WorldEvent id 继续描述“哪件事发生”，source type/ref 描述“为什么发生”：角色移动和经过生命周期验证的拿取/放下/开关/使用/销毁指向 actor resolved action；普通对象属性差分只有在 Host ledger 将 action target 与该对象精确匹配时才指向 action batch；Host edit 使用稳定 change id。公共 scene flag 缺少精确 actor ledger 时保持 transition source，不把同轮所有行动者猜成原因
- 合法空间移动由 LegalityEngine 的图结果写入 actor location，语义 GM 即使漏写 `state_updates` 也不能让身体停在原地；提交后宿主从前后位置差生成 movement Event
- movement Event 同时向出发地的离开目击者和目的地的到达目击者投射，各自 Experience 保留自己的观察地点。移动者保留 self cognition，但不把自己明知的移动重新排入 passive attention，避免自唤醒循环
- 普通 `world_objects` 属性在事务成功后由宿主比较前后快照，生成不可伪造的 `object_state_changes`；灯光、门况、机器状态等变化只投射给对象所在地观察者，hidden 对象只投射给真实 source actor。无实际差值不生成 Event
- 空间图采用专用结构通道：新增地点走 world_additions.locations，已有通路变化走 topology_changes 的 connect/disconnect。世界模型判断具体因果，宿主复用图校验，在同一个 WorldStateTransaction 中暂存、语义校对和提交。
- Runner.run_step(topology_changes=...) 保留作者步前注入；生产结算也可输出 topology_changes，实际变更派生 route_opened/route_closed 事件。位置、物品和拓扑一同回滚，每次事务只增加一次 world_version。
- 已提交的开路/断路会形成 POV 安全的 `route_opened / route_closed` WorldEvent 及 `world_object.connected_to` typed impacts；局部、公共、隐藏三种可见性分别决定谁能获知，不因物理图已改变就让异地角色心灵感应
- 人工介入的普通 `world_edits` 由 `HostWorldEditTransaction` 处理，只能补丁已有对象的非生命周期、非拓扑描述字段；同一批重复对象、私有字段、非严格 JSON 值或任一越权字段会整批回滚
- 宿主对象补丁同样从 before/after 生成稳定 `host_object_state_changes`，no-op 不递增 `world_version`；真实差分进入普通 `object_state_changed` Event、typed impacts、目标重激活与 POV Rendering，不再存在“状态已经改了但世界中无人能观察”的静默旁路
- `HostMutationTransaction` 是 `world_edits + topology_changes` 的外层提交边界：两者在同一 Scene 副本执行，任一子事务非法则共同回滚；成功批次无论包含几类变化都只递增一次版本
- 步前宿主批次被拒绝时 Runner fail closed：设置 `step_aborted / step_abort_reason` 后在 Input 之前返回，AgentRegistry、ActionEventQueue、GameClock、WorldEvent 与 Session step_count 均不消费这次无效调用；修正后的 retry 保持原 step 和稳定 id
- 宿主命令通过后，Runner 在 Input 前捕获整步 checkpoint；它按原对象恢复全部 ECS model fields，并恢复 Entity 集合、Agent runtime bindings、Relation/Claim bindings、离散行动堆、busy map、sequence、GameClock，以及本地 Hermes 的 ledger projector 与 subject home 快照
- Dispatcher 在权威 phase 中只缓冲消息；Input～WorldEvent 的 System 或 callback 抛出未预期异常时，后续 phase 不运行，checkpoint 与 dispatcher 一起 rollback，`authoritative_step_failed=true` 且 Session 不计步
- WorldEventSystem 完成是 authoritative commit barrier。Rendering/Memory 属于交付层且各自在执行前捕获短 checkpoint；异常只撤销失败 phase 新增的内部 Component/context 半状态，返回 `delivery_phase_exception + step_committed=true` 而不倒转合法世界或更早成功的交付。已经发出的外部文字、订阅副作用和模型调用不能假装没发生，但本地 Hermes 的 subject home 会随权威 checkpoint 一起恢复；未提交 proposal 永远不成为事实
- 产品适配器不得把失败 context 当普通 turn。`public_step_status` 只投影 `aborted / rolled_back / delivery_failed / committed`、是否提交及 phase/type；异常 message 留在宿主日志。Web 对未提交尝试写 system history 且不增加 step，delivery failure 保留已提交 turn；Console 使用同一投影提示
- delivery failure 创建 `DeliveryReceipt(start_index, committed_context, attempts)`；存在 receipt 时禁止开始下一权威 step。retry 只能遍历 WorldEvent barrier 之后的 phase，失败时更新同一 receipt，成功时清除，不触碰 Agent/queue/clock/event/step_count
- episodic memory id 由 memory namespace、actor、step 和类型确定并使用 upsert；Memory retry 可以覆盖同一条而不能追加副本。一个 receipt context 已记录的 consolidation actor 不在同次 retry 重复 compact。Web retry 原地修复最后一条 turn，EpisodeRunner 自动尝试恢复并审计持续 pending
- `public` Event 的 epistemic witness 集合与 automatic attention recipient 集合分开：前者决定谁知道事实，后者只决定谁被立即调度。默认预算为 8；subjects 与 event location 的现场者强制保留，Goal 条件命中 Event refs/typed impacts 者优先，其余按 event+actor 稳定散列轮换
- `WorldEventWitnesses.attention_recipients` 是宿主审计/重放字段，不投影 priority 或选择 trace 给 Agent；`public_event_attention_budget` 是 engine-managed Scene flag，内容包可初始化，语义状态更新不可写
- manual override 不是 attention 清空能力：人工和 runtime 都先取得同一个排序、POV-safe `AgentPerception`，每次只确认 packet 实际包含的最多 20 条 WorldEvent 与 20 条 response；超出部分继续留在 Cognition/closure ledger
- `manual_decision_context` 是 AgentPerception 的有界 UI 投影，只含 self/visible state、可见对象名、近期主动/被动观察、pending ids、无条件锁的 Goal 摘要和 ongoing actions；Session preview 不改变 Cognition，Console/Web 必须在用户选择前展示它
- simultaneous batch 中每个已完成的角色意图都必须进入 `resolved_actions` 或 `uncertain_outcomes`；Simulation/GM 若遗漏某个 Agent，Host 会只为该遗漏意图应用通用原子回退并记录审计备注。模型不能通过省略输出取消角色已经提交且消耗时间的行动，规范化后的动作文本也始终绑定回原始 proposal
- 正向完成且带已验证 authored affordance 引用的 `interact` 会由 Host 物化为 exact `object_lifecycle.use`；语义 GM 无需重复猜测能力 id，也不能把 blocked 行动或另一个能力写成已使用。对象是否仍存在、同场、可访问、需要所有权/能力、是否消耗和并发赢家继续由生命周期事务与资源竞争系统裁定
- portable/owner/container state 还会自动派生 `engine:take/drop/open/close`，分别编译为 `relocate(owner/location)` 与 `set_container_state`；这些不是内容标签，`engine:*` 为保留命名空间。Input 和动作完成时各校验一次，陈旧能力会把对应 action 改为 blocked，不回滚同批其他合法行动
- 物理 affordance 表示 capability，不表示 motivation：它们进入 Hermes/AgentPerception 供角色规划选择，但不无条件加入宿主自动候选池；否则“可以放下包裹”会错误变成随机丢弃动机。只有 runtime 明确提出，或 authored affordance 产生当前角色真实的 need relief 时，策略才自动考虑该动作；仅声明 `policy_tags` 不会制造候选
- authored affordance 可声明有限 Host `policy_tags`，经过世界事务目录校验后仅进入私有策略上下文，不进入 AgentPerception、GM packet 或 Rendering。runtime 必须先引用当前真实可用的 object/affordance；Host 再把对应 `aid/risk/information/cooperate` 等特征并入 Trait/Drive 评分，避免概率映射依赖自然语言关键词，也避免 Agent 自报标签
- `HostRuleSimulationControl` 提供无 LLM、无故事特例的确定性语义基线：它只生成原子回退结果，移动、内建物理 affordance、资源竞争和事务由现有 Host 系统完成。评测内容可用它证明 seed 机制，而不为每个对象动作手写 Simulation resolver
- 正向主动 `observe(target=evidence)` 会由 `EvidenceObservationResolver` 对照 Claim Entity 的 supports/refutes 边派生 private_result 与 claim_discoveries；GM 的 discovery 声明会被替换，Claim truth 不进入观察。对象无关联、不可见或行动失败时不发现，具体社会解释仍留给角色语义层
- communicate 可携带已知 Claim 和证据引用。生产世界模型判断实际送达者并输出 recipients，支持电话、耳语和打断；宿主核验身份引用及消息来源，交付经验和唤醒信号。ClaimKnowledgeSystem 记录收到的立场和证据，角色自行判断是否相信及如何回应。
- 单边整件交付用 `interact(target, delivery_recipient)`：发送者必须持有公开 portable 对象，接收者当前可见；语义层正向结算后 `ObjectDeliveryResolver` 覆盖该对象的 GM lifecycle 写入并生成 exact relocate。部分数量、互换与付款不走该捷径，继续要求双方 proposal
- public scene flag  会生成 `scope=scene,target=scene,path=scene_flags.<field>` impacts，并向现存角色投递一次环境观察；私有 flag 和消费账本保持内部状态
- 现场见证者获得私有 event belief 与 passive experience；缺席者只在自己真实所在地获得 self experience，不会被伪装成曾在会场
- 每个首次获知的 event id 进入角色私有 pending observation 队列；离屏 background Agent 会被唤醒一次，runtime 成功收到完整 perception 后才由宿主确认处理
- pending observation 不是简单 FIFO。宿主为每条刺激保存不进入 Agent prompt 的 `priority + step + stable id` attention record：销毁/警报/缺席高于普通对象变化，普通移动和时间阶段较低；道歉、解释、指控等 response 与 WorldEvent 在同一个选择边界比较。容量截断保留最高优先级记录，同级按新鲜度和稳定 id 重放；Agent 只看到排序后的真实 id，不看到数值
- Scheduler 的唤醒原因、AgentPerception 中最多二十条待处理刺激和成功决策后的 acknowledge 使用同一个排序 view，避免“按最旧事件唤醒、却交付并消费最新事件”的错位
- 每条首次出现的 `source->target:response_kind` 也生成稳定 event-response attention id。它与原 event knowledge 分开消费，因此已知事故上的新道歉、解释或指控仍会唤醒接收者
- pending event 不允许 Episode 提前 closure；重复收到同一个已知 event id 不会形成无限唤醒，dormant policy 仍只接受人工激活
- 异地非见证者不会自动获知。使用 event id 转述时必须由知情角色同场提交 communicate；宿主读取 Event Entity 的 statement，忽略模型试图夹带的改写文本
- Event Entity 不等于角色态度：是否相信转述、如何评价、形成 Sentiment 或采取行动仍属于角色私有状态与宿主社会规则
- Goal、Sentiment、Modifier 和普通 Relationship Track 衰减/漂移不生成 WorldEvent，避免把私人或连续内部状态误当成公共事实
- 已知 event id 可作为 Agent-grown Goal 的 `world_event` 来源；未知角色不能伪造 source ref
- 通用 `communicate_event` Goal 把“围绕后果进行社会行动”变成可验证闭环：目标只接受当前可见 recipient；完成证据来自 Event Entity 上宿主记录的 `source->target` communication，而不是模型声明
- `respond_to_event` 在同一边界上增加少量稳定语义：explain、apologize、accuse、request、forgive、acknowledge。它们记录客观社会行为，不自动编译接受、相信、宽恕、罪责或关系 delta
- `WorldEventResponses` 只记录通过 CognitionSystem 边界的真实 communicate：发送者必须知道事件、双方同场、行动类型必须为 communicate、显式 action target 不得与接收者冲突；Event Fact 文本始终从规范 Entity 读取
- response attention 是观察投递机制，不是强制回话任务：Agent 可以回应、行动、记住、忽略或沉默；宿主只保证新的社会行为不会因原事件已知而丢失
- Event Entity 与 witness 边界进入 Episode 权威 hash、不可逆变化和 replay 审计，不再只靠角色记忆间接证明事件存在

### LegalityEngine

- 执行硬世界法则、角色能力与空间图约束
- 世界 profile 是开放字符串，可由宿主注册新规则，不要求修改 SimulationSystem
- 移动意图只能沿 `connected_to` 图推进，跨多跳行动会重写为下一跳
- 角色能力必须与行为匹配；拥有魔法能力不自动意味着能够飞行或瞬移
- 只裁定 proposal 是否可执行，不负责生成戏剧结果

### ConflictPressure

- 维护连续安静回合、可见冲突次数和模板重复窗口
- 合并 Storylet 偏好和当前可见反应需求；已有故事块按条件进入 World 事件结算，新增故事块由 StoryPlanner 独立提出
- 只产生 `advisory_pressure` packet，包括压力状态、机会原因和可选模板，不直接替 NPC 决定行动
- 不再产生 `require_visible_conflict`、minimum forced level、forced action budget 或 unrealized marker；安静回合本身可持续存在
- 冲突模板仍由故事内容提供，核心代码不识别具体题材标签

### SocialDynamics

- 构造玩家可见的有向关系、角色压力与即时反应上下文
- 所有权威社会关系统一登记在 session 级 `SocialRelationRegistry`
- 普通人际关系采用稀疏 `relation_kind=pair` 聚合实体：一个 Entity 同时保存双方有向 `RelationshipTracks`、共享 `RelationshipBits` 与 `RelationshipTimeline`
- 精确 Track 数值只供宿主策略、衰减与结算使用，不进入 AgentPerception 或语义 GM packet；宿主派生 `hostile / wary / non_hostile / trusted / friendly / close` 等定性状态供角色理解
- 自然关系目标只能追求 `non_hostile / trusted / close` 等宿主目录状态，不能提交 trust/favor/malice 数值或阈值
- 甲对乙与乙对甲仍是两个逻辑方向，但不再机械创建两个基础 Entity；首次实际定向互动会惰性创建 pair 并添加 `acquainted` bit
- favor / malice / trust 不再保存在 GM 集中图或 `SceneState.actor_states` 的动态字段中
- favor / malice / trust 只由宿主 Sentiment 或其他显式宿主社会规则改变；Simulation 的直接数值写入会在中央权威边界被清空
- 一次失败、拒绝或冲突不会被引擎自动解释为“恶意增加”
- 语义层只提交有可观察事实支持的 `social_impacts`；长期 Track delta 由宿主固定目录派生，不能由 GM 直接填写

### RelationshipSystem

- 推进 pair relationship 中声明了 `decay_per_step` 的连续 Track，使临时怨恨、紧张或熟悉度可以按宿主规则回归，而不是交给 LLM 随意遗忘
- 到期的 Relationship Bit 确定性移除；永久的亲属、婚姻或组织身份使用无期限 bit
- Track decay 与 bit expiry 形成结构化 transition trace，但不会自动替角色生成行动

### SentimentSystem

- `SentimentState` 是角色私有的、指向具体他人的短中期社会感受；它与客观 Pair Relationship、角色长期 Track 和普通 episodic Memory 分离
- Simulation 只能用 `social_impacts` 标注 grateful/admiring/hurt/angry/afraid/suspicious/betrayed/relieved 等通用 appraisal，并必须由 affected 在同地点可观察的 source 已提交行动支持
- 模型只提供 kind、`minor/moderate/major/extreme` 定性 intensity 和事实 reason；宿主先映射为固定 magnitude，再由 SentimentDefinition 决定持续时间、衰减、Policy tag 权重及少量长期 Track effect，精确权重不进入 Agent prompt
- 同 target/kind 的重复体验按饱和公式积累，不做无限线性相加；SentimentSystem 在每个世界 step 确定性衰减并清除过期记录
- 一批 social impacts 在副本上统一验证，任一非法则不发布其中任何感受或关系沉淀
- Sentiment 的来源由宿主改写为已验证的 `resolved_action`；模型自报的 `source_event` 不进入权威状态。由 Sentiment 沉淀的有向 Track 同时保存该 Sentiment 的规范引用，供 Episode 还原“行为 → 感受 → 长期关系 → 后续目标”链

### 按结算依赖补全世界

- 统一原则：当本轮外部结果依赖尚未确定的事实时，由结算 LLM 确定本轮所需的最小部分。判断依据是结果对事实的依赖，由模型理解自然语言。
- 人物说了什么是一项带来源的事实；说法中的人物、物品、地点、关系及真伪可以继续待定。原话沿用消息、可感知事实和记忆保存。普通语言互动维持送达/可感知性检查，原话中的物理行为维持言论身份。
- 正常语义结算通过 `world_additions.locations/characters` 提出必需实体，物品使用既有 `object_lifecycle.spawn`。这些补充无需提前签发具体实体授权。
- 地点批次在同一个候选图里检查引用；先准备地点，再准备角色身体，物品的 owner/location 可以引用同批新人物/地点。既有节点与身份保持原有约束。
- `WorldStateTransaction` 在暂存世界中应用状态与生命周期操作，提交前由同一结算模型校对：新增内容是否服务本轮外部结果、言论是否被误当作真相、事实是否与初始设定及已提交状态一致、角色自主选择是否得到保留。
- 候选语义或结构有问题时，沿用原始意图修正一次；错误持续则整步回滚。角色 runtime 注册失败属于执行故障，直接回滚整步，后续可重试同一步。
- 无法由实体属性完整承载的持续客观结论，通过 `world_additions.facts` 追加到私有 `scene_flags.established_facts`；每条记录保存 `step/statement`。它保留确认的否定事实及欺诈结论，供后续结算和存档恢复使用。历史事实允许后续有因果依据的真实变化。
- 不确定结果的实体补充可写在 success/failure 分支内，只合并宿主选中的分支；通用事务与语义校对随后检查实际结果。
- Narrator 只接收玩家可感知事实，原始 world_additions 和确认日志保持宿主私有。未知内容一直保留到实际结算需要确定它为止。
- 作者显式 `inject_events` 保留原有 Authority/授权消费路径；固定授权仍约束其身份和状态。新 Storylet 定义由导演提出，经结构校验后原子登记；具体事实在未来结算及提交校对中检查。
- 复用 `CandidateLedger` 保存动态身份/容量账本；程序只处理必要引用、位置、容量、原子提交和运行时绑定，世界补全的语义必要性由模型判断。

### CharacterLifecycle

- 初始内容加载与动态出生共享同一个三方不变量：每个 `SceneState.actor_states` 行为主体必须有同名 ECS Entity、AgentController 和 live AgentRegistry runtime；每个 Agent Entity 也必须有位于已知地点的 Scene body。该不变量由 AgentRegistry 注册入口和所有含 SceneState 的 Runner step 共同强制，不依赖标准内容加载器
- 动态出生者本轮的观察窗口显式标记为尚未存在；它可以读取提交后的当前状态，但不会被同地点回退逻辑补成出生前行动、交换、知识传播或社会后果的见证者
- `ScenarioConfig.characters` 与 `initial_actor_states` 在 Session bootstrap 时必须一一对应；无 Agent 的 actor body、无 body 的 Agent、重复角色名和未知初始地点均在创建 GM/Entity 前拒绝
- 正式 Session 每步在任何 Host mutation、时间推进或 Agent perception 之前重新审计绑定；运行时被意外注销、Entity 被替换或 Scene body 脱离时整步 fail closed
- 正常动态出生由结算 LLM 的 `world_additions.characters` 提出，并通过提交前语义校对。作者显式注入可使用 `CharacterEntryAuthority` 的一次性授权，授权信封仍由 `NarrativeCandidateAuthority` 校验。
- 授权固定 name、role、location、initial_state 和私有初始结构；`profile_mode=semantic` 最多允许 GM 补充 personality 与自然语言 goals，不能改写权威出生事实
- 动态人物必须同时进入 Entity 集合、权威 `SceneState.actor_states` 和 `AgentRegistry`
- `prepare` 只验证请求、净化字段并构造尚未发布的 Entity，不修改 ECS、世界或注册表
- `stage` 将候选 actor body、`dynamic_character_names` 和 consumed authorization id 写入事务副本，使新人物可以参与同轮对象、关系和 宏剧情 因果校验，失败时授权也不会被消耗；名字账本与消费账本复用共享的 `CandidateLedger`
- 世界事务成功后才执行 `finalize`：先创建并确认 live runtime 注册，再公开 ECS Entity
- runtime factory、注册回调或注册确认失败时，先注销残留 runtime 和 Entity，再通过事务 checkpoint 恢复 Scene 与 Relationship
- 正常补全的出生注册失败直接回滚整轮；成功后 Rendering 才接收已提交的可感知事实。显式授权路径保留原有 transaction rejection 反馈。
- 出生地点必须存在于本轮候选世界，可引用同批新增地点；未知位置直接拒绝，宿主保留请求中确定的入口。
- 使用 `max_dynamic_characters` 限制宿主事件或内容错误导致的无界人口增长
- runtime、激活策略和初始认知经过白名单与长度限制，模型不能任意选择宿主执行能力
- 动态生成不允许只创建 agent 而遗漏世界状态中的身体实体
- 不提供直接 `spawn()` 兼容旁路；公开发布必须按 prepare → WorldStateTransaction stage → finalize 执行
- finalize 必须收到真实 AgentRegistry 和注册回调，并在 Entity 进入 session entities 前验证 live runtime 已登记；不存在“只有身体没有脑”或未注册角色静默加入世界的成功路径
- 旧 `Persona / AgentActionSystem / NarrativeControl / NarrativeSystem` 管线已删除。核心系统公共导出只包含 Runner 的权威阶段，GM 文本不能直接更新 Scene 或向 entities 字典插入人物
- InputSystem 对缺少 live runtime 的自动角色记录 `missing_agent_runtime` 并跳过，不回退到第二套 Persona 决策器；每个自动角色的唯一大脑来自 AgentRegistry

### WorldObjectLifecycle

- `world_objects` 中旧内容默认保持地点语义；有形对象必须显式设置 `is_location: false`，因此不会成为角色可移动到的空间节点
- 支持 `spawn`、`relocate`、`set_visibility`、`set_container_state`、`use` 与 `destroy`，覆盖创建、拾取、放下、收纳、取出、开合、转交、隐藏、使用和销毁
- 每次操作必须关联同一 actor 本轮已经结算为 success / partial / complication 的行动，并填写事实原因
- 普通角色只能操作与自己同地点且可访问的对象，目标 owner 或 container 也必须同场；`World` 只能在有对应世界行动证据时绕过同场限制
- 有形对象必须且只能拥有 `owner`、`location` 或 `container` 三种放置之一；只有直接位于地点时才能进一步指定 `sub_location`
- 容器是内容包预定义的通用能力：`is_container`、`container_capacity`、`container_open`、`container_opaque` 与物品的 `container_size` 都属于权威字段，动态模型不能发明或用普通 state update 改写
- `container` 可以递归嵌套；有效地点沿容器链解析，因此角色携带外层包移动或把整箱交给另一人时，内部物品会自然随行而无需逐项重写
- 放入和取出要求所有相关容器已打开；关闭的不透明容器遮蔽内容，关闭的透明容器允许看见但不允许直接操作。任何自包含、循环引用、容量超限或缺失容器都会使整轮事务回滚
- 容量按直接 child 的 `container_size * quantity` 计算，避免嵌套内容在多层重复占用；非空容器不能直接销毁或消耗
- 非便携对象不能由普通角色搬动；对象不能借生命周期接口伪装成地点、修改空间图或覆盖保留字段
- `max_dynamic_world_objects` 和 `dynamic_world_object_names` 约束动态对象数量与生命周期账本；名字账本复用共享的 `CandidateLedger`。物品新增与人物/地点补全共同接受提交前语义校对。
- 隐藏对象只进入所有者自己的 POV；其他同场角色和 Rendering 都不会收到其生命周期 payload 或普通属性更新
- 生命周期先在候选 SceneState 上执行，因此对象所有权、位置和存在性可以直接影响后续 Storylet 条件
- 每次 `spawn` 的接受/拒绝结果都会写入统一的 `narrative_candidate_audit` 账本（`kind="object"`），与其余三个候选 kind 共享同一份审计记录

### DriveState 与 NeedDynamics

- 每个角色 Entity 都持有私有 `DriveState`，与公开 `SceneState.actor_states` 和主观 `Cognition` 分离
- need meter 由内容包命名，通用字段只有 `pressure`、`drift_per_turn`、`critical_threshold` 和描述；核心不包含题材或角色专属 need 名称
- `pressure` 规范化到 0..1，越高表示越迫切；`DriveSystem` 按世界 step 确定性漂移，并以 `last_advanced_step` 保证同一步重入不会重复增长
- `risk_tolerance` 也是角色私有状态，用于帮助 Agent 在需求、目标与风险之间形成不同选择
- Agent 只接收自己的 DriveState，不会看到同场角色的私有压力；Simulation 只接收本轮行动角色的 drive context 用于结算
- 有形对象可以预声明 `affordances`；`use` 操作必须引用已有 affordance id，不能在模型输出中临时填写效果
- affordance 的 `need_effects` 和 `consumes` 属于内容定义；动态 spawn 与普通 state update 都不能创建或改写这些字段
- 对象 quantity 扣减/删除与 DriveState need effect 在同一事务副本上执行，checkpoint 也同时覆盖私有 drive 状态
- 非物质后果使用显式 `drive_updates`：affected actor、source actor、已有 need、increase/decrease、定性 intensity 与 reason 都必填，source 必须有本轮 resolved action 证据；宿主固定映射有限 delta，若 source 不是本人，该行动还必须在 affected actor 所在地可观察
- `drive_updates` 不进入玩家 Rendering payload；角色只能在下一轮通过自己的私有 DriveState 感知压力变化
- 每个 need 保存一个有界的宿主 provenance ledger：对象 affordance 与显式社会/环境压力指向已验证 `resolved_action`，自然漂移指向 clock step。ledger 不进入 `private_drives`，只用于回滚、重放和 Episode 因果审计
- `NeedDynamics` 会从当前可见对象生成 affordance opportunity，按角色当前 pressure 与 relief 强度排序，但该分数只影响 Agent 判断，不改写 proposal
- 普通 background 角色的 need 达到 critical threshold 时，`AgentScheduler` 会立即给予一次 background 激活；`autonomous=False` 仍保持人工控制边界

示例内容定义：

```python
"面包": {
    "is_location": False,
    "kind": "food",
    "location": "营地",
    "quantity": 2,
    "affordances": [{
        "id": "eat",
        "label": "吃掉一份面包",
        "need_effects": {"hunger": -0.5},
        "consumes": True,
    }],
}
```

对应角色配置：

```python
NeedConfig(
    name="hunger",
    pressure=0.7,
    drift_per_turn=0.08,
    critical_threshold=0.85,
)
```

### ObligationState / Agreement / escrow

这一层已经删除。角色之间的跨时承诺不再由宿主义务/协议/托管状态机执行；口头承诺留在沟通与角色记忆里，当场交付只走 `exchanges`。

### WorldStateTransaction

- 在 SceneState 与事务级 `RelationshipBook` 的副本上暂存本轮写入
- 同一事务还可以暂存参与本轮结算的私有 DriveState；快照通过可序列化字段重建，不沿 Component 的 Entity 回指复制 live runtime
- 校验更新 section、已有角色、已有对象、地点、子区域、空间图、有形对象放置与生命周期账本、tension 范围，以及宿主关系变化的角色和关系不变量
- RelationshipBook 只从 pair 关系实体重建；宿主应用 Sentiment 等系统派生的有向 track delta 和互动时间线后再原子发布，Scene 不保存关系镜像
- 所有检查通过后才一次提交；任何一项失败时 Scene、SocialRelation 与 DriveState 全部保持原样
- 成功提交返回这些权威组件的提交前恢复 checkpoint；需要 live runtime 等外部资源的生命周期可以在 finalize 失败时撤销已经提交的候选世界
- checkpoint 同时恢复 need pressure、risk tolerance 和 drive 的 step 游标，避免资源已经回滚而角色仍错误地认为需求已被满足
- 被拒绝的结算会清空 resolved facts、relationship/host-derived storylet hit 和 spawn 请求，避免 Rendering 描述未提交事实
- 被拒绝的结算也会清空对象生命周期操作，避免渲染一个没有实际生成、移动或销毁的物品
- 新角色和新对象不能借普通 state update 隐式出现，必须经过专门生命周期接口
- 每条非 World `resolved_action.actor` 必须存在于本轮 proposal actor 集合；Simulation 模型、Conflict template 或 Storylet 即使生成了语法正确的 NPC 行动，只要该 NPC 本轮没有运行并提交 proposal，整个事务就拒绝该伪造结果

### ProposalArbiter

- 同批次 proposal 采用 simultaneous 语义，不按 Entity 插入顺序互相预知；玩家和 NPC 遵守同一条规则——本批次内任何一方尚未结算的 proposal，对同批次决策的其他人一律不可见，直到它进入下一批（见"玩家/Agent 对称"）
- 已经结算完成的历史意图与 World 信号（已经是权威环境事件，不是未决 proposal）不受此限制
- 同优先级 proposal 保持稳定输入顺序，重放结果确定
- 玩家手工输入是强锚点；自动玩家行动只是普通角色 proposal
- Arbiter 只排序和标记意图，不决定行动成功与否

### ResourceContestResolver

- 在结构化 Simulation 输出之后、因果 宏剧情 与 `WorldStateTransaction` 之前解析同轮对象竞争
- 只裁减互不兼容的 `object_lifecycle` claim 并改写受影响 action，不直接修改权威世界
- 消耗型 `use` 按对象当前 `quantity` 分配配额；同一 actor 的重复使用同样占用配额
- 非消耗、非独占 affordance 可以由多名角色同轮共享；`exclusive: true` 只允许一个 claim
- `relocate / destroy / use` 混合竞争和互相矛盾的 `set_visibility / set_container_state` 按 actor 选择单一稳定赢家；相同目标状态允许多方共同完成
- 排序只使用规范化到 0..1 的 proposal priority、边界为 -1..1 的通用 initiative 和稳定 actor 名；原始 operation index 只保留同一 actor 内的操作顺序
- 手工玩家 proposal 保持强锚点；模型不能通过输出 `contest_score` 或调整 JSON 顺序操纵胜负
- 输家 operation 在事务前移除，其 action 改写为 `blocked`；有部分 claim 或其他对象操作成功时改写为 `partial`
- 完整 `resource_contests` trace 只进入 GM 长期记忆，不进入 Rendering 或普通角色的可见记忆
- 非法 object id、未知 affordance 和畸形 schema 不会被竞争解析器静默修复，仍交给世界事务明确拒绝
- `WorldObjectLifecycle` 会拒绝任何绕过 resolver 直接进入事务的未仲裁独占/超额/矛盾 claim；主循环负责解析，权威事务负责兜底，外部宿主不能靠直接调用 transaction 恢复数组顺序语义

对象 affordance 可以额外声明：

```python
{
    "id": "operate_radio",
    "requires_capabilities": ["radio_operation"],
    "requires_owner": True,
    "exclusive": True,
    "consumes": False,
    "need_effects": {},
}
```

`requires_capabilities` 必须是有限的非空字符串列表；`requires_owner` 和 `exclusive` 必须是布尔值。资格检查读取回合开始前的角色能力，因此模型不能在同一份结算中先给角色写入能力、再声称已经成功使用设备。`NeedDynamics` 仍会把看得见但不满足资格的 affordance 放入角色机会列表，并显式提供 `available`、`required_capabilities` 与 `missing_capabilities`，让失败可能成为角色决策的一部分，而不是信息被直接隐藏。

### ExchangeDynamics

- `exchanges` 表示两个已有角色在同轮明确接受的物品或有限资源当面交付，不是自由形式对白的隐式副作用
- parties 必须恰好包含两个不同角色，`accepted_by` 必须与 parties 完全一致；双方必须同场、本轮真实提交 Agent proposal，并分别拥有同地点、非 hidden、outcome 为 success/partial/complication 的 resolved action
- transfer 的 `from` 必须真实拥有 object，`to` 必须是另一 party；对象必须 tangible、portable、已公开且不能同时进入 `object_lifecycle`
- quantity 缺省为整件或整个堆栈；部分转移只有在内容预定义 `stack_key` 时才允许，模型不能通过普通 state update 或动态 spawn 发明 fungibility
- 同 stack_key 且除 owner/location/hidden/quantity 外状态一致的 recipient stack 会确定性合并；否则引擎从 source state 复制出稳定 hash id 的 fragment，并纳入 `dynamic_world_object_names` 与数量上限
- 同轮所有 exchange 先按 object 聚合 claim；总 quantity 超过权威库存、一个对象被指向不同 recipient、或多个 exchange 形成双花时，整笔世界事务拒绝
- 应用按稳定 exchange/object id 计算，不依赖 JSON 数组顺序；fragment id 使用 object、recipient 与排序后的 exchange ids 生成
- exchange 在候选 SceneState 上先于普通 object lifecycle 暂存，因此支付与对象所有权可以处于同一个 `WorldStateTransaction`
- 任一关系、Drive 或对象写入随后失败时，交换也随 checkpoint/事务整体回滚，不存在半提交的转手
- 交易提交后取得钥匙会改变权威世界状态，下一批次的 Storylet 条件检查据此触发事件
- 完整 exchange bundle 只进入 GM episodic memory；Rendering 和普通角色记忆依赖可见 resolved actions，不接收私下的 bundle 明细

示例：

```json
{
  "exchange_id": "key_for_delivery",
  "parties": ["甲", "乙"],
  "accepted_by": ["甲", "乙"],
  "transfers": [
    {
      "from": "乙",
      "to": "甲",
      "object_id": "旧钥匙"
    },
    {
      "from": "甲",
      "to": "乙",
      "object_id": "甲的铜币",
      "quantity": 2
    }
  ],
  "reason": "双方当面确认以钥匙和两枚铜币完成交换"
}
```

内容中的可堆叠资源显式声明：

```python
"甲的铜币": {
    "is_location": False,
    "kind": "currency",
    "owner": "甲",
    "location": None,
    "portable": True,
    "hidden": False,
    "quantity": 5,
    "stack_key": "currency:copper",
}
```

核心不理解“铜币”“信用点”“药剂剂量”或“口粮”这些题材名称；`stack_key` 只声明哪些内容对象可以安全视为同一种可合并资源。

### Knowledge Transfer

- 秘密与信念通过显式 `knowledge_updates` 从 source 传播到 target
- `told` 模式要求 source 的私有 Cognition 中此前确实存在该陈述
- source 必须有本轮已结算交流行动，target 按该行动已确认的 recipients 判断送达。历史及离线消息沿用同场投影。
- 表达作为带说话人来源的 experience 交付；Claim 的发现与转述记录 receipts，已有角色立场保持原值。新收到命题的 uncertain/0.5 是尚无主观立场的存储默认值。
- 同场旁观者不会因为一次定向传递自动获得知识；公开传播需要为每个接收者提供受支持更新
- 普通对白不会自动升级为世界真相，也不能实现异地心灵感应

### GoalState / GoalSystem

角色创建时，每个 `Identity.goals` 字符串都会同步成为一个私有 `GoalState` 记录。它可以零配置地参与 Agent 决策。Agent 可以引用自己当前 active 的 Goal，或本轮收到的 WorldEvent/EventResponse；Host 核验归属、POV 和注意队列。这只表示“这个行动在角色策略中响应了该动机”，不能证明行动成功或目标已经完成。

场景可以选择提供 `goal_specs`，为少数需要确定结算的目标增加 `completion_conditions` 和 `failure_conditions`。这些条件复用 `StateCondition`，只能查询权威 `SceneState`：

```text
自然语言 Goal
  -> Agent 生成相关候选
  -> Host utility 影响选择概率
  -> WorldStateTransaction 提交事实
  -> GoalSystem 检查权威条件
  -> active -> achieved / failed
```

- Agent、Hermes 和 GM 都不能直接写 Goal status；
- 没有条件证据的目标保持 active，不由模型自评；
- 成功与失败条件同时成立时拒绝任意裁决并报告错误；
- Agent 能看到目标、优先级和生命周期结果，但看不到精确条件锁；
- 已终结目标不再参与宿主行动策略；
- Goal 是角色私有动机，不构成全局剧情线。

初始目标结算后，角色还可以从自己真正持有的结构化来源继续形成私人目标，包括 resolved Goal、Claim、WorldEvent/EventResponse、Drive need、Sentiment、可见对象/角色、既有 Relationship 或 NavigationProblem。Agent 只提交自然语言 `goal_requests`；宿主核验 source id、覆盖 actor、防重复与刷目标、限制冷却和 active 数量，并自行决定 priority。新目标不能携带模型编造的完成条件，也不能由 Agent 自报 achieved/failed。宿主模板目前覆盖 `reach_location`、`possess_object`、`deliver_object`、`use_affordance`、`verify_claim`、`obtain_evidence`、`become_acquainted`、`reach_relationship_state`、`communicate_event` 和 `respond_to_event`：地点、对象、能力和人物必须通过形成决策时的 POV 校验，交付要求角色真实持有物品且接收者当前可见，`use_affordance` 只能逐字引用当时 Host 提供的对象机会，并等待之后真正提交的 object WorldEvent 才完成；该事件还必须同时匹配 Host 固定的 `object:<step>:<index>:<operation>:<target>` id、对象操作 kind、精确 `resolved_action:step/actor` 来源、subject、object 和 affordance id，单独伪造 event metadata 或由另一角色执行均不能完成。以 Drive need 为来源时，该能力还必须确实降低同一个 need。来源验证使用形成决策时的 Host 快照，而不是动作结算后的状态，避免“同轮吃掉食物后饥饿已经下降”反过来抹掉目标动机。Claim 证据必须由 Claim Entity 预先关联且最终同时进入角色 KnowledgeState 与真实资产持有状态，相识、关系与事件回应则分别等待真实 Relationship 或 WorldEventResponses 证据。GoalSystem 通过受限的权威证据视图编译隐藏的成功/失败条件；没有安全模板的自然目标仍诚实地保持不可自证。若后续世界变化让具体做法进入角色 POV，Agent 可以对同一个开放目标提交一次 `refine`；Host 只允许修改 `origin=agent + active + 尚无条件锁` 的目标，并以当前受限 POV 编译模板，不创建第二个目标、不接受模型条件，也不允许改写已有锁。只有 `origin=agent` 的目标可被角色明确细化或放弃。普通来源默认只能产生一次目标；Drive need 是可复发状态，旧目标终结、冷却结束且压力重新出现后可以生成新的目标实例，但不能同时复制 active 目标。这样故事可以从后果和身体压力继续生长，同时不把主观愿望升级成世界真相。

收到的 `event_response` 也是独立 Goal 来源，而不必退化成原始 WorldEvent。宿主只接受该角色 Cognition experience 中真实存在的 response id；回应即使已经从 pending attention 确认，仍保留为私有经历来源。由此可以区分“事故本身让我行动”和“对方随后道歉、指控或请求让我形成新决定”，其他角色不能引用这条私有回应。

目标请求携带的可见性证据来自 InputSystem 在决策前构造的宿主 POV 快照，而不是模型字段，也不是动作提交后的新视野。这样角色可以在同一轮提出“去走廊”并实际抵达后获得 achieved，同时不能先移动到陌生地点、再利用结算后的视野反向声称自己行动前已经知道该目标。Agent 伪造的 `_host_perception` 会被项目边界覆盖。

可验证的 Agent-grown Goal 同时形成一个受限后台续行动信号，按受保护的 `agent_goal_wakeup_interval` 提供 `agent_goal:<id>` 激活。开放 Agent Goal 也必须有机会在新世界状态下 refine 或 abandon，但使用更慢的 `agent_open_goal_review_interval`（默认 12 step）并共享最高 8 倍重复行动退避；它不会像可验证目标一样高频续行。成功交付 perception 后才把 step/id 写入 AgentController 调度账本。该账本不属于剧情实质变化，也不进入世界状态变化指标。作者目标仍不增加后台频率，dormant 仍保持人工边界。

调度账本还保存 continuation attempt、最近 action kind/target signature 与重复次数。连续相同签名按 1×/2×/4×/8×指数退避，最多 80 step；方法变化会重置重复计数。次数只作为角色私有规划提示和宿主诊断，不直接改变 Goal status。确定性失败来自模板编译的世界证据：对象型目标使用 `not_exists` 锁识别真正销毁，地点目标识别目标地点被移除。暂时不可见、被他人持有、一次 blocked 或几轮无进展都不会被粗暴视为失败。

`goal_reactivation` 是退避的反向机制。宿主只在变化已经进入该角色 POV 后，先将 Event 的类型化 `scope/target/path` impacts 与 active Agent Goal 的隐藏状态依赖匹配，再以 subject/object/location/source 引用相交作为兼容边界；命中时清空重复签名、保留累计尝试次数，并从事件 step 重启基础间隔。比如开启外层木匣会确定性影响匣中钥匙的 accessibility，从而重新激活“取得钥匙”，即使 Event 的直接 object 是木匣。无关事件不重置，相关但未知的异地事件不重置，重复投递同一个 response 也不重置。更新写入 `goal_reactivations` 审计列表，只记录安全的 `match_basis`，不包含完整隐藏条件。Agent/GM 不能提交 arbitrary relevance 或概率。

### ModifierState / ModifierSystem

Modifier 表达角色当前受到的临时、非社交行为影响。它采用 Paradox 式数据定义：kind 决定持续时间、`refresh / stack / replace` 规则、最大层数与宿主策略权重；具体故事代码不需要认识某个 modifier。

```text
已提交行动
  -> GM 请求 apply/remove(kind, target, qualitative_intensity, reason)
  -> Host 校验行动证据与允许的 catalog
  -> ModifierSystem 原子发布整批私有状态
  -> Host policy 在后续决策中计算影响
  -> 到期后确定性移除
```

- SceneState 仍保存“腿部受伤”等客观物理事实；`injured` Modifier 只表示由此产生的暂时行为倾向；
- Sentiment 保存“甲害怕乙”等有明确对象的社会评价；`shaken` 表示不针对某人的短时冲击；
- Drive 保存饥饿、安全感等持续压力；`exhausted` 是有明确来源和期限的临时条件；
- GM 不能指定 duration、stacking、policy weights、stacks 或 expires step；
- 隐藏外部来源不会通过 Modifier 的 source/reason 泄漏给受影响角色；
- Modifier 的权威 provenance 始终由宿主绑定到已验证 action；GM 自报 `source_event` 会被覆盖。隐藏来源只清除角色可见的 source/reason/source_event，不删除宿主审计证据；
- 任一更新非法时，整批 Modifier 更新不发布。

### Claim Entity / KnowledgeState

只有会参与规则和行动的客观命题才实体化为 Claim；普通回忆、猜测和无结构对白继续保留在 Cognition/Memory 中。

```text
Claim Entity（宿主）
├── ClaimFact：statement、truth status、subjects
├── ClaimConditions：真/假世界条件
└── ClaimEvidence：支持/反驳该命题的世界对象引用

Character
└── KnowledgeState
    └── claim_id、stance、confidence、basis、source、evidence_refs
```

- Claim 真值可以随 Scene/宏剧情 条件变化，但角色知识不会自动同步；
- public Claim 会作为公开主张进入所有角色知识，但不代表角色相信它，更不代表它为真；
- active observe 只有发现与 Claim 相连、且对行动者可见的 evidence 时才能更新 KnowledgeState；
- 同场知情角色可以在 communicate 中断言与自己立场不同的 stance，从而撒谎；
- 说话者必须先知道该 Claim，不能由 GM 凭空创造知识；
- 接收者 confidence 由宿主根据 trust 和是否实际出示证据计算；
- Claim 真值、条件和其他角色知识不会进入 AgentPerception；
- Episode 将角色首次学习或修订 Claim 记录为 `claim_knowledge_learned/revised`。observed basis 从 KnowledgeState 的 Host-owned `source=evidence:<id>` 和 updated step 构造 `EvidenceObservation <- resolved_action + Evidence`；reported basis 构造 `ClaimReport <- speaker resolved action`。Claim-derived Goal 指向 `claim_knowledge:<actor>:<claim>`，而不是全局 Claim truth
- 角色支持某项针对他人的 Claim 时形成 potential leverage，持有支持物时标为 evidence-backed；
- Claim Entity 不是 Agent，也不会自主行动。

### CognitionSystem

- 在 Simulation 后、Rendering 前运行
- 记录角色自己的行动结果，以及同地点可见的其他行动
- 不记录异地结果，也不记录别人执行的 hidden 行动
- 只更新角色私有经验，不改变 `SceneState`
- 让下一轮 agent 依据真实结算修正计划，而不是只依赖行动前推测

### SimulationSystem

- 将条件成立的 Storylet 编成 World 意图，结算并提交后记录生效与消费
- 让 `SimulationControl` 返回结构化结算
- 将结算写回 ECS 状态

### RenderingSystem

- 调用 `NarrativeRenderer`
- 将已提交事实投影为玩家视角文本，并保存成功生成的消息；角色从自己的 Cognition / WorldEvent 获得反馈

### StoryPlanningSystem

- 在世界提交与玩家文本成功生成后记录玩家输入及世界回复，delivery retry 通过 turn id 去重。
- 周期性调用具有独立持续会话和 `propose_storylet` 工具的导演 Agent。
- 存储草案，结构校验通过后原子登记未来 Storylet；格式、重复标识或容量问题记录拒绝原因，登记异常保留待重试。导演可提出涉及角色参与的剧情方向，执行阶段区分世界变化与可拒绝的角色建议。

### MemorySystem

- 归档宿主的已提交 intent、结构化结算与事件事实。
- 角色通过自己的亲历事件归档，Hermes 自主管理原生记忆。
- 导演独立记录玩家叙事上下文，玩家渲染文字保持在导演会话中。

InputSystem 不再只用本轮措辞拼一个单一 query。`AgentMemoryContextBuilder` 从角色已经可见的结构化状态生成 `situation / goals / commitments / claims / social / reflection` 六条检索路线，批量查询角色自己的 Memory collection，再按内容去重并限制最多 6 条、单条 1800 字符、总计 9000 字符。检索 query 与结果都不会进入其他角色、语义 GM 或 Rendering；宿主只保留轻量 retrieval trace 供回归诊断。

MemorySystem 在归档时写入宿主拥有的 `salience`：普通行动较低，物品不可逆变化更高，Goal 结算、Claim 发现和强 Sentiment 属于高显著性事件。检索候选不会直接按 embedding 距离返回，而是由宿主综合 `route priority + salience + recency + vector relevance` 重排；旧但改变人物命运的事件可以压过近期措辞相似的普通日志。模型输出不能写入或覆盖 salience。

长期运行时，`MemoryConsolidator` 每 12 step 扫描至少 24 step 前、`salience < 3` 的普通 `episodic_log`。累计至少 6 条后，宿主从原日志中确定性抽取简短片段，写成一个带 start/end step 和 source count 的 `consolidated_summary`。高显著事件、近期日志与已有摘要永不进入这一批删除。流程严格为“写摘要成功 → 删除源 id”；写入失败时不删除，删除失败时保留摘要和源日志形成可恢复重复，而不冒数据丢失风险。WorldHost 的完整诊断记忆暂不自动压缩。

## 六、当前限制

- `ConsoleDriver` 仍然只暴露玩家在角色空闲决策点输入行动这类简单交互。
- 中途审查、叙述后编辑、快照回退等导演式能力，还没有重新挂回现有权威事件循环。
- Storylet 启动条件支持自然语言 trigger，精确 conditions 保留为可选约束；后续推进由独立追踪会话判断。

### 地图知识与导航问题

- `KnowledgeState` 持有每个角色私有的 `known_locations/known_routes`；亲历地点会增量学习当地公开出口，但不会仅因某个出口当前缺席就静默删除旧记忆或转述路线，断路必须通过真实尝试或明确 topology observation 成为认知事件。seed 可声明角色先验熟悉的地点。长途目的地和 Host 寻路都只使用该角色已知道路，猜中隐藏地点名也不能借权威全图移动。
- 角色可以用结构化 `route_source/route_target` 向同场角色指路；Input 只允许发送者真实知道的有向边，接收方记录 reported 来源和 learned_step。道路关闭不会全局同步擦除旧认知，实际尝试时 Host 以 `stale_route` 阻止，从而保留迷路、过时地图和重新问路的故事空间。
- 一次 communicate 也可携带 2..8 个不重复地点的 `route_path`；Input 逐边验证连续性，任一未知边使整条引用失效，Host 再以相同来源和时间原子传播所有边，避免多回合逐段指路的机械流程。
- `stale_route/movement_blocked` 会生成角色私有、Host 权威的 `NavigationProblem`：保存失败边、Host `failure_rule`、目的地、发现位置/时间和角色已知地图中的备选路径。它只唤醒后台 Agent，不自动创建 Goal 或指定应对；备选路径不会读取 Host 隐藏拓扑。角色离开发现地点后，该局部问题即视为解决。
- 非导航行动的 `fail/blocked` 结果不会制造永久世界实体，而是以带 Host action event id 的近期私有经历保留有限时间。Agent 候选可以引用 `action_failure` 来表达换方法；Host 只接受该角色自己的近期失败，并按经历年龄衰减贡献。成功事件、未知 ID 或其他角色的失败不能成为动机。Episode 可据此记录 `resolved_action <- action_failure`，避免把重复失败误认成合理的持续行动。


### LLM 结算的提交校对

生产 `SimulationControl` 先解释原始意图并产生结构化结算，宿主在现有事务副本中应用权限、对象生命周期和其他必需的结构约束，再让同一结算模型校对提交前后事实。校对检查结果文字与实际状态是否一致，以及角色所有状态字段中的行为、选择和自主回应是否有该角色原始意图作为依据；外部事件导致受伤、摔倒等被动后果由模型判断因果关系。字段名保持开放，宿主只要求明确的有效判定后才发布事实。

正常回合调用一次结算、一次提交校对。校对拒绝时，模型收到具体问题与未提交的候选结果，有一次完整修正机会；原世界与原始意图保持一致，角色 Agent、动作排队和世界时钟均不重复执行。修正仍被拒绝，或校对服务不可用、判定格式错误时，权威步骤失败并走现有整步回滚。失败候选结果不会进入事件通知、玩家叙事或角色记忆。

该校对使用 `SimulationControl` 现有模型，不增加 Agent 或调度阶段。明确选择的离线 `HostRuleSimulationControl` 保持离线测试用途。导演剧情提案只做结构登记；自然语言启动条件、具体外部后果与角色建议统一由世界结算模型解释，提交校对审核实际候选世界与消息来源。

### 生产路径的语义边界

- 玩家与 Hermes 提交自然语言意图；Input 调用现有 SimulationControl 的行动解释接口，依据该主体的可见上下文得到行动类型、目标和正式引用，原意保持不变。解释发生在调度前，动作时长由已解释类型的宿主策略决定。主体各自的意图保持同时提交边界。
- LegalityEngine 只读取已解释的结构字段，核验引用存在性、访问状态与已知道路；物理能力、否定、语言/行动区分、社会反应和世界因果由模型解释与提交校对负责。PhysicsRuleConfig.statement、capability 和 reason 是模型约束，keywords 仅保留旧内容注释。
- 叙述模型校对实际将交付的最终文本；字数整理和玩家结果锚点在校对前完成。拒绝时语义修正一次并重新校对，持续失败进入已有交付重试。生产路径已删除引号台词匹配与文字替换。
- 词表只用于明确选择的离线骨架，解析与目标匹配集中在 rules/offline_semantics.py、rules/offline_targets.py；生产流程不会调用它们。
- 主体停滞记录使用行动类型与正式引用；宿主保持原始动作文本供语义理解，移除按文字猜动作压力和社会回应的逻辑。

- 初始散文提取保留逐字 evidence 引文核验和实体引用校验；群体人数、性格/目标释义和秘密创作许可由同一初始编译模型做来源语义校对，移除人数词表与秘密授权的正则判断。来源校对拒绝时按已有编译重试修正一次，持续失败或服务故障时停止创建会话。结构化 ScenarioConfig 和显式设定语法继续走原有加载路径。

### 语义世界变化与动态物品

生产路径保留模型提出的位移、交流结果和 private_result，对完整暂存世界统一检查因果、角色自主权和事实一致性。空间检查提供普通步行与同场互动的参考。角色自主选择来自原始意图，外力可以改变其他角色的身体和位置。离线 HostRuleSimulationControl 保留独立规则基线。

动态物品 spawn.properties 可声明容器能力、affordances、数量和堆栈身份。宿主核验类型、身份引用、容量和容器循环等结构不变量；能力的存在必要性与物理合理性接受同一次语义校对。位置和拓扑字段保持专用生命周期与图命令通道。

故事块 location 约束首次生效地点，continuation 可以在其他有效地点继续。玩家叙述允许忠实改写和合并结算摘要，事实完整性与来源由语义校对检查，长度根据作者配置由模型控制。玩家保留本人 private_result，他人的私有结果继续隔离。
