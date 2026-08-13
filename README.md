# Story Engine

`Story Engine` 当前对外交付的产品形态是一个简单的终端文字冒险游戏，但引擎层已经开始按“状态与叙事严格解耦”的方向重构。

核心目标不是做一个会聊天的 GM，而是做一个有绝对世界状态、可插拔文本渲染器、可逐步长成完整叙事沙盒的故事引擎。

更长期的产品目标是：作者只提供一个尽可能小的故事种子——人物、欲望、关系、地点、世界法则和少量潜在矛盾——其余故事由角色 agent 与权威世界状态的持续互动自然生长出来。

## 当前引擎原则

- 世界真相由 ECS 状态决定，不由渲染文本决定。
- 主循环遵循 `Input -> Action Scheduling -> Simulation -> Rendering` 的权威顺序。
- `Simulation` 阶段只产出结构化结果，不直接向玩家讲话。
- `Rendering` 阶段只能渲染已经确定的事实，不得新增状态变化。
- Storylets 和 Drama 是结构化机会/压力观测，Plot Clocks 是宿主状态；它们都不是 prompt 里的散文命令。
- 每个角色注册为独立 agent；agent 只能提出意图，不能直接宣布世界结果。
- 角色使用 `observe / move / interact / communicate / wait` 五种原子大动作，具体参数保留自然语言，由环境规则与 GM 共同结算。
- 时间采用离散事件队列：动作按环境决定的持续时间完成，同一完成时刻的动作作为 simultaneous batch 结算，不再假定所有角色严格一轮一动。
- 被动观察由环境按 POV 自动投递；仔细检查、搜索和偷听属于会占用行动的主动 `observe`，私有发现不会泄漏给旁观者。
- 同场且当前未忙碌的 agent 会在决策时点行动；离屏 agent 低频或由本地事件唤醒，因此玩家看不见的世界仍会继续变化。
- 角色拥有与世界真相分离的私有信念、秘密、承诺和计划，允许真实的信息差与误判。
- Agent 运行时可替换并共享单一行动边界；Hermes 是长程存活的角色主体，而不是宿主上下文检索适配器。
- 引擎层不得包含具体剧本的人名、地点、固定对白或角色专属补丁。

## 权威主循环

### 1. Input

- 玩家输入本轮行动，或让玩家角色自主决策。
- 玩家与 NPC 通过注册的角色 agent 产出“意图”而不是最终结果。
- 系统收集 `intents`，这只是尝试，不是已经发生的事实。
- 每个角色只收到基于自身位置、观察和记忆构建的 `AgentPerception`，不会读取完整世界真相。
- 同场角色的 `actor_states` 也经过三层投影：宿主保留完整 ECS 状态；本人可以看到自己的能力和身体/心理状态；他人默认只能看到位置、姿态、表情、外观、公开身份、视线和明显站位。`dramatic_motive / dramatic_push / bias / pressure_profile / signature_templates / capabilities / skills / secret` 不会因为同场而进入 Agent 或玩家 POV。内容包可用宿主只读的 `public_state_fields / private_state_fields` 声明“制服”“明显伤势”等自定义外显字段，语义 GM 不能运行时修改可见性 schema。
- Narrator 还会再次删除 actor 幕后更新、bias/framing/territorial、Storylet/Conflict 建议、causal rule 和内部 notes，避免从渲染旁路恢复读心信息。
- `scene_flags` 也按 public/private/engine 分层：默认只有 day phase、weather、alarm、ambient condition 和 public status 进入 Agent/玩家 POV；world version、phase schedule、commitment book、动态实体账本和 Storylet/causal 消费记录仍是宿主私有。内容包可用 ScenarioConfig 的只读 `public_scene_fields / private_scene_fields` 扩展，GM 不能在运行时修改可见性 schema。公共 flag 的实际变化会形成全局 WorldEvent 和 scene impact；私有 flag 只驱动宿主规则，不会自动广播。

### 2. Action Scheduling

- 本轮激活角色提交的原子动作先进入 session 级离散事件队列。
- 动作耗时由环境拥有，角色和 GM 不能自行声明更快完成。
- 世界时间跳到最早完成事件；相同完成时间的动作一起进入 Simulation。
- 尚未完成动作的角色保持 busy，其他角色可以继续行动。

### 3. Simulation

- `SimulationControl` 读取当前 `SceneState`、激活的 `Storylets`、`DramaState`、`PlotState`。
- 大模型在这里充当“结构化常识处理器”，只返回 JSON 风格的结算结果。
- ECS 依据经过验证的 `state_updates` 等语义后果更新世界；`plot_updates` 由宿主因果规则追加，长期关系变化由宿主社会系统派生，最后统一事务提交。
- `CognitionSystem` 随后只把角色亲历或同场可见的结构化结果写入其私有经验。

### 4. Rendering

- `NarrativeRenderer` 读取已经结算完成的结构化结果。
- 它只负责把确定事实渲染成给玩家看的文字。
- 渲染后文本进入 `Observation`，结构化结果与渲染文本一起写入长期记忆。

`Cognition` 与 `Memory` 都不会改写已经结算的世界。对普通 LLM runtime，它们仍分别承担 Host 私有经验与长期检索；对 Hermes，`Cognition` 只保留 POV/知识校验需要的有界收据，长期记忆、解释和回忆由 Hermes 原生 memory 独占。

普通 `LLMCharacterAgent` 的长期记忆上下文由宿主按 `situation / goals / commitments / claims / social / reflection` 多路构造，并受固定条数/字符预算约束。Hermes 不接收“一股脑拼好的完整回忆”：它持续保有自己的 conversation 与原生 JSON memory/tool 上下文，宿主只投递当前身体/世界边界和新的 POV-safe 私人刺激，由 Hermes 主动回忆。两条路径都不能混入 GM 记忆或其他角色私有状态。

Hermes 的主观状态不再与 Host 双写。Host 只维护可验证的私人账本，例如角色实际接触的事件/Claim、身体压力、义务、协议、日程、地图和已登记目标；这些以带 revision 的 `ledger_update/ledger_retraction` 增量投递。计划、focus、情绪、主观推断、私人承诺和长期回忆由 Hermes 原生 memory 独占，Hermes 角色也不会触发 Host Chroma 检索与归档。逐字段契约见 `docs/SUBJECTIVE_STATE_OWNERSHIP.md`。

兼容 Host 记忆排序还会结合显著性与时间衰减；Goal/Obligation 结算、Agreement 后果、Claim 证据、强 social-response proxy 和重要物品变化获得更高 salience。Hermes 不使用这套排序，其注意与回忆属于 subject 内部机制。

旧的低显著普通日志会由宿主定期做确定性整合：只处理至少 24 step 前且 salience 低于 3 的记录，至少累计 6 条才生成 routine summary。Goal、Claim Evidence、违约、重大关系后果等高显著记忆永久保留；摘要必须先成功写入，源日志随后才删除，因此压缩失败不会造成角色历史丢失。

每个 Session 默认拥有独立的 `memory_namespace`，同名角色不会跨 Session 共享 Chroma collection。显式复用 namespace 只作为宿主控制的存档恢复边界；动态出生角色自动继承当前 Session namespace。

## 状态模型

### SceneState

保存“此刻世界真实是什么样”的快照，包括：

- `description`
- `world_objects`
- `actor_states`
- `scene_flags`

### Storylets

Storylet 是散落在状态空间里的原子触发器，只包含：

- `conditions`
- `intent`
- `priority`
- 可选 `one_shot`

Storylet 现在由独立 `StoryletEngine` 选择。它代表一个当前可用的叙事机会，不等于强制某个角色执行预写动作。

Drama、Conflict 和 Storylet 只向语义结算提供可选的解释线索，不构成本轮任务。如果没有真实 Agent proposal 自然兑现，系统什么也不追责，也不会产生 `unrealized` 欠账。GM 自报的 `storylet_hits` 会被中央权威边界清空；宿主只在不确定性、资源竞争和协议结算之后，从实际候选行动识别自然命中，并且只在世界事务成功后消费 one-shot。角色选择沉默、回避或暂缓本身就是完整有效的自主行为。

时间阶段、日程到期、缺席事件与转场压力由独立 `TimelineEngine` 管理。Commitment 通过 `participants + location + due/grace + wake_before_steps` 表示：参与者会在自己的 `private_schedule` 中看到邀请，离屏 Agent 会在时间窗口前被唤醒；Hermes 自己决定是否把赴约形成候选，兼容 LLM runtime 仍可接收宿主生成的赴约候选。Timeline 不直接修改任何角色的 location、stance 或 focus。角色可以赴约、迟到、拒绝或继续处理更重要的目标；最终出席和缺席只根据结算时的真实位置判定。玩家不在场时，预定的非角色世界事件仍可发生，但引擎不会反向伪造任何角色曾经参与。

宿主已提交的角色移动、普通可观察对象属性、出席/缺席、物品生命周期、交换、义务终态和协议生命周期变化会由 `WorldEventSystem` 物化为独立 ECS Event Entity，而不是只留下一句 aftermath 文本。合法移动由空间规则直接补全坐标，GM 漏写位置也不会让角色原地不动；移动事件同时向出发地与目的地的真实目击者投射，移动者会记住自身行动但不会被它重复唤醒。灯光、门况等普通属性由宿主比较事务前后快照生成 change ledger，模型伪造的 ledger 会被覆盖；`world_edits` 也已收束为 `HostWorldEditTransaction`，只允许已有对象的严格 JSON 描述属性，整批原子提交并生成相同的 POV-safe Event，no-op 不增加版本也不制造事实。局部或隐藏对象仍遵守各自 POV。空间拓扑 `connected_to / zones / default_zone / aliases / is_location` 不能由普通语义更新改写；已有地点之间的开路与断路只能通过 `HostTopologyTransaction` 整批校验、原子提交，并形成 `route_opened / route_closed` Event。默认只通知通路两端的现场者，宿主明确声明 public 才全局投射，hidden 则只改变物理可达性。旧 `world_edits` 也不能绕过这一入口。`WorldEventFact` 保存客观发生时间、地点、subjects、objects、状态 impacts 和中性事实陈述，`WorldEventWitnesses` 只保存直接现场者与事件当事人。事件不会自动广播：见证者获得带 event id 的私有 Cognition/Experience，异地角色仍不知道；知情者必须通过真实 `communicate` 行动才能转述，宿主从 Event Entity 取回原始 statement，GM 不能借转述改写客观事件。围绕旧事件发生的新解释、道歉、指控等回应拥有独立 `event-response:<...>` 注意力 id：即使接收者早已知道原事件，这次新的社会行为仍会作为一次被动观察唤醒离屏 Agent，而不会被“event 已知”吞掉。亲历或获知的事件可以继续形成新的私人目标。Goal、Sentiment 和普通关系数值漂移仍是内部状态，不会被错误提升成客观公共事件。

同一步的 `world_edits` 与 `topology_changes` 还会被外层 `HostMutationTransaction` 组合成一个 fail-closed 步前事务：两类命令在同一 Scene 副本上验证，全部成功才共同提交并只增加一次 `world_version`。任何一项非法都会让整个宿主批次回滚，并在 Agent Input 之前返回 `step_aborted=true`；该次调用不运行 Hermes、不推进 GameClock/ActionEventQueue、不生成 Event，也不增加 Session step_count。修正命令后重试仍使用原 step，因此稳定 change/event id 不会出现空洞。

Runner 的权威 phase chain 还具有整步 `RunnerStepCheckpoint`。checkpoint 保留原 ECS Component 对象、动态实体、Agent runtime 绑定、Relation/Agreement/Claim registry、ActionEventQueue 与 GameClock；Dispatcher 在事务期间只缓冲消息。若 Input 到 WorldEvent 之间出现未预期异常或 phase callback 抛错，Runner 会停止后续系统、恢复 checkpoint、丢弃外部消息并返回 `authoritative_step_failed=true`，不会让 Rendering/Memory 描述半状态。WorldEventSystem 完成后是权威 commit barrier；其后的 Rendering/Memory 每个 phase 另有短 checkpoint，异常时只撤销该交付 phase 的半写入，保留已经合法提交的世界以及先前已完成的交付，并返回 `step_committed=true`，避免为了表现层故障倒转故事历史。外部已经输出的文字或订阅回调无法物理撤回，因此会明确记录 delivery error；内部 Observation、continuity 或 Memory 半状态则不会残留。Agent runtime 已经进行过的私有推理同样无法“取消”，但其 proposal、注意确认和任何世界写入都会回滚，绝不升级成事实。

Session 用 `public_step_status()` 把复杂内部诊断收束为四种产品状态：`aborted`（宿主输入未开始）、`rolled_back`（权威步骤未成为历史）、`delivery_failed`（世界已提交但表现/归档失败）和 `committed`。Console 会给出对应提示；Web 历史对前两者写 `kind=system`，不会追加“局面没有显著变化”的伪剧情回合，delivery failure 则保留 `kind=turn + committed=true` 并明确没有可靠新文本。公开 payload 只包含 status、committed、failure phase/type，不包含可能带秘密状态的原始异常 message；step_count 始终只随权威提交增长。

`delivery_failed` 会生成 session-local `DeliveryReceipt`，保存已提交步骤供 Rendering/Memory 使用的 context 以及失败 phase 下标。receipt 未处理时 Runner 以 `pending_delivery_retry` 拒绝新世界步骤；`Session.retry_delivery()` 只从失败交付 phase 继续，不重新运行 Input、Agent、ActionScheduling、Simulation 或 WorldEvent，也不增加时间与 step_count。每次失败 phase 都先从短 checkpoint 开始，因此可反复重试。单步 episodic memory 使用 `namespace + actor + step` 的稳定 ID 并通过 Chroma upsert 写入；归档到一半重试不会复制已成功角色的记忆，已完成的 consolidation trace 也不会在同一次 delivery 中重复执行。Web 提供 `POST /api/retry-delivery` 和专用按钮，恢复成功后原地修复最后一条历史，不新增一个故事回合；EpisodeRunner 会自动尝试一次交付恢复并把持续失败列为质量违规。

硬物理法则、角色能力与空间连通性由可扩展的 `LegalityEngine` 裁定。新题材可以注册自己的 physics profile，而不需要修改 SimulationSystem。

动态人物不能再由 Simulation GM 凭空引入。宿主注入事件或时间线承诺必须先携带一次性 `character_entry` 授权，固定 authorization id、人物身份、地点和初始世界事实；只有 `profile_mode=semantic` 时，GM 才能补充自然语言 personality/goals。随后 `CharacterLifecycle` 执行两阶段出生：先纯验证并准备 Entity，再把候选 actor body、授权消费记录与 Scene/Plot/Drama/Relationship 一起事务提交，最后注册 live agent runtime。注册失败会使用事务 checkpoint 恢复全部权威状态、注销残留 runtime 并移除 ECS Entity，避免产生只有“脑”没有“身体”、只有剧情没有人物或授权已用但人物未出生的半状态。动态人物仍受已有地点和数量上限约束。

旧版 `Persona -> AgentActionSystem -> NarrativeControl -> NarrativeSystem` 旁路已经移除。该路径曾允许 GM 文本直接改 Scene、直接向 entities 字典插入人物，并绕过 proposal、WorldStateTransaction、CharacterLifecycle 和 AgentRegistry。系统公共 API 现在只暴露 Runner 使用的权威阶段；动态人物没有直接 `spawn()` 兼容入口，必须经过 prepare → transaction stage → runtime finalize，且 finalize 必须证明角色已经进入 live AgentRegistry。

InputSystem 也不再回退到 Entity 上的旧 Persona brain。自动行动角色若没有 live AgentRegistry runtime，本轮不会生成 proposal，并会留下 `missing_agent_runtime` 诊断；引擎不会为了让场面继续而偷偷换用第二套角色决策逻辑。内置 `Sherlock/Moriarty` 示例 prefab 同样已经删除，核心只保留通用 `create_agent`。

钥匙、信件、证据、武器和资源等有形对象通过 `WorldObjectLifecycle` 创建、搬动、收纳、取出、开合、转交、隐藏、揭示或销毁。每次操作都必须有同一角色本轮已结算的行动证据，并遵守所有者、地点、容器访问、同场性、便携性和动态对象数量约束。物品可以权威地位于人物、地点或另一个预定义容器中；嵌套内容随外层容器移动，关闭和不透明属性决定可操作性与 POV 可见性，容量、循环引用和非空销毁均由事务不变量裁定。旧内容包中的 `world_objects` 默认继续作为地点；有形对象显式使用 `is_location: false`，不会混入移动图。

角色的持续动机由私有 `DriveState` 表达。内容包可以声明多个通用 need meter，每个 meter 包含当前 pressure、每回合 drift、critical threshold 和角色自己的解释；`risk_tolerance` 则帮助 Agent 在高压需求与危险之间权衡。核心不认识“饥饿”“归属”“真相”或“家庭体面”等具体名称，只负责确定性推进和边界校验。

对象可以由内容包预先声明 affordance，例如一份食物的 `eat` 会缓解 hunger 并消耗一份 quantity。Agent 只能使用自己当前可见且物理同场的对象；资源扣减和私有需求效果在同一个 `WorldStateTransaction` 中提交，任何一边非法都会同时回滚。模型不能在运行时发明新的 affordance 或 need effect。

多个角色同轮争夺同一件有限对象时，由确定性的 `ResourceContestResolver` 在世界事务前统一仲裁。消耗型 affordance 的 `quantity` 是本轮可满足的 claim 配额；非消耗且非独占的 affordance 可以共享；`exclusive: true`、搬动、销毁以及互相矛盾的可见性或容器开合操作只保留一个稳定赢家。赢家由受限的 proposal priority、可选的通用 initiative 和稳定角色名决定，不读取模型临时输出的“胜负分”，也不依赖 `object_lifecycle` 数组或 Entity 插入顺序。输家行动会被改写为 blocked/partial，而不会因为引用已被消耗的对象拖垮整轮事务。

Affordance 还可以声明 `requires_capabilities`、`requires_owner` 与 `exclusive`。资格由权威角色状态和对象所有权校验；Agent 的机会列表会同时说明 required/missing capabilities 和当前是否 available，使角色可以感知“看得见但不会用”，却不能自行宣布已经掌握能力。

社会关系采用《模拟人生》式的稀疏双人关系容器，并由 `SocialRelationRegistry` 维护。`Relationship:甲<->乙` 同时保存双方各自的有向 `RelationshipTracks`、共享的 `RelationshipBits` 和互动时间线；favor、malice、trust 不再复制到 GM 总表或角色 Scene 状态。角色实际发生定向互动时会惰性创建关系，内容包也可以通过 `initial_relationships` 声明初始关系种子。

Agent 与语义 GM 不会看到精确的 favor/trust/malice 数值。宿主把 Tracks 派生为 `hostile / wary / non_hostile / trusted / friendly / close` 等定性关系状态，精确数值只用于宿主概率策略和衰减。角色可以形成“让彼此达到 trusted/close”这样的自然目标，但不能指定数值阈值或直接写 Track。所有语义结算器还必须经过统一的 `SemanticAuthorityFilter`；即使脚本、Hermes 或未来 resolver 输出了精确关系 delta，也会在进入不确定性检定和世界事务前被清空并留下审计记录。

即时社会反应使用角色私有的 `SentimentState`，而不是直接把一次羞辱、帮助或威胁全部写成永久 trust/favor。GM 只能提出有可观察行动证据的通用 `social_impacts` 和 `minor/moderate/major/extreme` 定性强度；宿主将其固定映射为 `0.25/0.5/0.75/1.0`，再决定感受的持续时间、衰减、行动效用权重和少量长期 Track 沉淀。重复同类感受以饱和方式积累，隐藏或异地事件不会隔空改变角色。Agreement 的真实 fulfilled/breached/cancelled performance 也会确定性地产生参与者局部的 grateful/betrayed/hurt，而不是全局声誉。

角色之间可以通过结构化 `exchanges` 达成有代价的交易。每份 exchange 是两个独立 Agent 的双边契约：双方必须同场、本轮真实提交 proposal、分别产生公开的正向 resolved action，并在 `accepted_by` 中明确同意。整件对象直接转移 owner；转移容器时嵌套内容保持原有 container 引用并随新持有者移动，容器内单件物品则必须先通过权威行动取出才能直接交易。内容预定义相同 `stack_key` 的货币或资源可以按 quantity 部分转移，引擎会确定性合并已有堆栈或创建受动态对象上限约束的拆分片段。隐藏物品、非便携对象、数量不足、双花和同时走 object lifecycle 的对象都会使整笔事务回滚。交换可以与 obligation delegation 同事务提交，因此“乙收下一把钥匙并接手任务”要么全部发生，要么全部不发生。

即时 exchange 之外，明确且需要跨时执行的承诺会成为独立 Agreement Entity。双人协议通过 `parent_relation_id` 挂靠 `Relationship:甲<->乙`，多方协议挂靠稀疏 Group Relationship；协议不塞进关系组件数组。`AgreementTerms` 和 `AgreementLifecycle` 保存协议自身的条款及状态。普通试探和含糊讨价还价仍只存在于 Communication、Cognition 与 Memory。第一回合 propose 不移动资产；后续参与者可独立 accept、reject 或 counter。最后一方接受时，引擎根据当时真实的所有权、库存、地点、可见性和义务状态重新结算，不能凭旧承诺冻结或复制资产。

Hermes 的概率策略属于角色主体：需要权衡时，它先用角色私有的分层 Gumbel 采样选择 `motive_lens`，再在该动机下采样具体行动，最后只向宿主提交一个 action；宿主看不到也不会重排私人候选。评测可固定 per-character seed，生产使用随机 subject seed 并只记录 fingerprint。普通 `LLMCharacterAgent` 暂时保留旧 `CharacterPolicy` 兼容路径，由宿主根据 Trait、风险承受力、需求、义务和关系给候选计分并使用 `policy` 流采样。世界成功率与观察噪声始终由独立的宿主 `world` / `observation` 流决定，因此 Hermes 的主体性不会越过客观结算边界。当前尚未实现人格/目标条件化的全局工作空间竞争和 appraisal，只完成了持久主体、私人 inbox 与两级采样骨架。

语义 GM 也不能替宿主决定概率结果。真正不确定的尝试必须输出 `uncertain_outcomes`，同时给出成功和失败两个结构化后果分支；宿主先同时检查两边的位置权限，只允许当前 move actor 留在原地或抵达空间规则已经授权的目的地，再根据固定难度、权威 capability/skill 与独立随机流选中一个分支并提交 `WorldStateTransaction`。模型自报的 probability、roll、数值 modifier、非移动坐标或替换目的地都会被拒绝，未选分支不会泄漏给叙述或角色记忆。

谈判不必停留在“接受或拒绝”。任一 pending Contract party 可以提出完整 `counter`：旧报价进入 terminal `countered` 并记录 `superseded_by`，新报价保存 `countered_from`，资产仍不移动，只有反报价人自动接受。反报价不能偷偷增删参与者，也不能只 patch 一个价格字段而继承含糊旧条款；它必须重新声明完整 transfer、delegation、service 和 escrow 条款并重新通过安全校验。无效反报价不会破坏原报价，多方契约仍要求其余每一方在后续 Agent turn 中独立接受，因此可以形成可追踪、可回滚的真实协商链，而不是作者预写谈判树。

Agreement 还可以包含延迟 `services`：成交时先原子支付 transfer，同时为服务提供者创建带权威 completion conditions 的新 Obligation，期限从实际成交 step 起算。Agreement Entity 根据链接义务的真实 fulfilled、breached、cancelled 或 delegation 链更新 performance。每个角色只获得自己参与协议中对手的 `counterparty_performance` 和自己的 `own_performance`，不会形成全世界心灵感应式的统一声誉分；信任、宽恕、报复或再次合作仍由 Agent 根据具体历史决定。

需要条件支付时，Agreement 可以用通用 `escrows` 托管已公开、可携带的对象或部分 `stack_key` 资源。资产本身仍由资产/库存机制管理；Agreement Lifecycle 只记录 custody lot 和预先接受的释放、退款条件。入托、服务责任创建、普通 transfer 和 Agreement Entity 在同一世界事务中提交，后续释放或退款也保持原子性。

非物质压力可以通过有证据的 `drive_updates` 改变：affected actor、产生后果的 source actor、已有 need、`increase/decrease` 方向、定性强度和事实原因都必须明确，并且 source 本轮必须有已结算行动。宿主把强度固定映射为 `0.05/0.12/0.25/0.4` 的 need delta；GM 自报 delta 会被忽略。Drive 更新只进入权威私有状态，不交给 Rendering；无证据更新会连同本轮其他世界变化一起回滚。

Simulation 的 Scene、Plot、Drama 与 Relationship 写入会先经过 `WorldStateTransaction`：在副本上验证全部不变量后才提交。语义 GM 不能直接提供 Plot clock 或长期关系 delta；Plot 更新只由 `CausalPlotEngine` 根据候选世界事实生成，关系变化由 Sentiment、Agreement performance 和其他宿主社会规则按固定映射产生。低层事务仍会验证宿主生成的关系写入及其角色、不变量和事实来源。成功事务会携带可恢复 checkpoint，供动态 Agent 注册等提交后边界失败时恢复；任一阶段非法时整批回滚，同时清除可能被 Rendering 误当成事实的 resolved actions。

秘密和信念通过受证据约束的 `knowledge_updates` 在角色间传播：发送者必须真正知道该陈述、双方必须同场、且本轮必须有已结算的传递行动；知识只进入指定接收者的私有 Cognition。

长期剧情可用 `PlotRuleConfig` 声明状态因果边：角色实际到达地点、对象状态被揭露或世界 flag 成立时，`CausalPlotEngine` 会按优先级自动推进 plot clock，并与世界状态一起事务提交；场景级触发数和推进预算可以防止一次宽条件造成剧情跳跃。

对象生命周期发生后的候选世界同样会参与因果规则求值，因此“角色真正取得钥匙”“证据被销毁”或“信件确实交到某人手中”可以直接推进 Plot，而不是依赖模型猜测剧情进度。

AgentPerception 会额外提供角色自己的私有需求快照，以及当前 POV 中对象可用 affordance 的排序机会列表。排序只是一种处境提示，不会强制 Agent 机械选择最高分行动；人格、目标、承诺、风险承受力和错误信念仍然共同决定 proposal。

离屏角色不会因为形成目标就每轮调用模型。只有 `origin=agent`、仍 active 且具有宿主完成条件的目标获得受限续行动调度，默认每 2 个逻辑 step 至多以 `agent_goal:<id>` 唤醒一次；runtime 成功收到 perception 后才记录本次机会。作者给出的长期目标和没有验证锁的开放愿望继续使用普通 `background_interval`。该间隔是受保护的宿主配置，GM 不能为赶剧情临时缩短。

若同一目标连续选择相同的动作类型和 target，续行动间隔会按 1×、2×、4×、8×退避，最大间隔 80 step；换用不同方法会重置重复计数。退避只控制推理成本，不会自动宣告目标失败。只有能够由权威状态明确证明的不可达才进入失败锁，例如 `possess_object / deliver_object / obtain_evidence` 所依赖的对象已经从 Scene 中被销毁，或目标地点已不存在。物品暂时被别人持有、角色暂时不在场或一次行动失败都不等于永久不可达。

退避目标会对角色真正获知的相关变化重新敏感。WorldEvent 保存由宿主状态 transition 派生的 `scope/target/path` impacts，并与目标隐藏的状态依赖匹配；旧式实体引用相交仍作为兼容边界。这样“打开木匣”可以通过宿主确定的 accessibility 影响重新激活“取得匣中钥匙”，不需要两个文本共享关键词，也不允许 LLM 自由声明相关性。事件仍必须先通过角色 POV/Cognition：角色不知道的异地开箱不会唤醒它，无关变化也不会重置。相关性只改变调度，不表示目标已经取得进展或完成。

`GoalState` 现在应理解为 Host 登记的目标 watch：它保存来源、调度和权威完成锁。对普通 LLM runtime，目标仍参与 Host 候选效用；对 Hermes，真正欲望和优先级保留在 subject 内部，只有需要 Host 监督进度或安排 wakeup 的目标才登记。Agent、Hermes 与 GM 都不能直接宣告目标完成，且看不到精确条件锁。

已结算 Goal、角色确知的 Claim/WorldEvent、Sentiment、Obligation、Agreement、可见对象/角色和既有 Relationship 可以成为新私人目标的来源。Agent/Hermes 只提出带真实 source ref 的自然语言目标请求；宿主负责引用校验、actor 归属、去重、冷却、容量和 priority，拒绝模型自带完成条件或伪造来源。宿主模板覆盖移动、取得/交付物品、履约、协议成交、Claim 验证/证据取得、实际相识、定性关系状态，以及围绕已知 WorldEvent 的真实沟通。事件回应可以定性为 `report / explain / apologize / accuse / request / forgive / acknowledge`；只有 committed `communicate`、同场事实、发送者知识和接收者实际获得规范 Event Fact 全部成立后才进入 `WorldEventResponses`。这些标签只证明角色做过什么，不代表对方相信、接受道歉、承认指控或改变关系；接收者的 Sentiment 与长期 Relationship 仍由独立宿主证据链产生。活动 Agent Goal 会阻止 Episode 过早收束。

短时、非社交的行为影响由私有 `ModifierState` 表达，例如 `exhausted`、`injured`、`focused`、`inspired`、`shaken`。它不取代 SceneState 中的物理事实，也不取代针对具体人物的 Sentiment。GM 只能在已提交行动的支持下请求 apply/remove；持续时间、叠加上限、策略权重与到期由宿主的数据定义控制。

会参与调查、欺骗、揭露和谈判的客观命题由独立 `Claim Entity` 表达；角色自己的 `KnowledgeState` 只记录对 Claim 的 stance、confidence、来源和已知 evidence。Claim 的宿主真值不会进入角色感知。有效主动观察可以通过已连接的可见 evidence 发现 Claim；同场知情角色可以传播或故意歪曲 stance，但不能凭空创造自己不知道的 Claim。

引擎提供 `EpisodeRunner` 与 `EpisodeSweepRunner`。前者审计单个多回合 trace，后者通过宿主 launcher 对同一故事种子运行多个 deterministic seed，聚合停滞、僵局、角色差异、Goal 收束、权限违规、动作轨迹多样性与 replay mismatch。可选的 `EpisodeClosurePolicy` 会根据 Goal、Obligation、Agreement performance、动作队列、尚未被角色处理的 WorldEvent/事件回应与可选 Plot 状态判断一个 Episode 是否已稳定收束；Agent/GM 不能自行宣布完结。Scene 中每个行为角色还会被审计是否具有对应 Entity、AgentController 和 live runtime。Episode 停止只是评估边界，不会关闭仍可继续模拟的世界。

仓库内置一个无 Storylet 的最小调查回归种子：两名 Agent 围绕秘密 Claim 和唯一 Evidence 自主调查、否认、施压或争夺物品。它用于跨 seed 校准组合机制，不属于核心引擎的固定故事内容。

另一个无 Storylet 的托管服务种子覆盖报价、接受、Escrow custody、限时 Obligation、按时履约、违约退款、晚交付与补偿 Agreement，用来验证社会承诺确实能通过离散时间和权威资产状态自然产生后果。

最小事件响应种子验证另一条无 Storylet 因果链：角色真实缺席日程 → WorldEvent → 离屏当事人被唤醒 → 自主形成告知目标 → 经宿主验证的转述 → 接收者形成移动目标并前往现场。它只存在于 evaluation content，不进入核心故事逻辑。

离屏角色的 need 达到自己的 critical threshold 时，`AgentScheduler` 会把普通 auto/background 角色从错峰休眠中唤醒，让它处理所在地的迫切事务；显式 `dormant` 策略仍然只接受人工唤醒。这样远处角色不会因为低频调度而在资源耗尽、危险逼近或义务临界时继续无动于衷。

`dormant` 严格区分“知道”和“被自动唤醒”：事件与转述仍可进入角色 Cognition belief/experience，但不会创建 pending attention interrupt。已有 pending 项在角色后来休眠时会保留供恢复，却不会永久阻塞 Episode closure；closure 只等待当前 policy 能自动消费的事件或回应。

可运行角色的 pending attention 不是 FIFO。单一的宿主注意策略按已提交事件类型和角色参与关系确定性排序：违约、销毁、警报、缺席及真实道歉/解释/指控优先于普通对象变化、移动和时间阶段；现场观察与后续转述不会再维护两份可能分叉的优先级表。大量低价值事件不能通过固定容量挤掉关键后果，队列同时预留最多四个最老项目，并按宿主模拟时间给予仍在等待的项目有界提升，避免持续输入让普通经历永久饥饿。Scheduler 的唤醒原因、AgentPerception 实际交付的前二十条和随后确认消费的集合使用同一排序视图。Agent/Hermes 只看到真实 event ids 的顺序，看不到基础 priority、等待提升或容量决策，也不能自报紧急度。

公开事实传播与立即注意中断是两回事。`public` WorldEvent 仍会写入所有真实 witness 的 Cognition/Memory，但宿主用 `public_event_attention_budget`（默认 8）限制同一步自动 interrupt 数；事件 subjects、事件所在地的现场者不受普通预算挤压，剩余名额优先交给 completion/failure conditions 真正依赖该 Event refs 或 typed impacts 的 Goal，最后才用 `sha256(event_id|actor)` 做与 Entity 插入顺序无关的稳定轮换。`dormant` 角色获得事实但不占预算。选择结果保存在 `WorldEventWitnesses.attention_recipients` 供 replay/audit，预算是 engine-managed flag，Agent 与语义 GM 不能修改。这样全局警报或昼夜转换不会让几十个 Hermes 容器同一瞬间惊群，同时没有丢失公共知识。

人工 override 与自动 Agent 使用同一个 `AgentPerception` 决策边界。Input 不再因为收到一条手动行动就无参数清空全部 pending attention；它先构建 POV-safe packet，将排序后的最多二十条 WorldEvent 与二十条 event-response 写入 `manual_perceptions[actor]`，然后只确认这批实际交付项。队列中其余后果继续保留并阻止 Episode 假收束。`Runner/Session.get_agent_decision_context()` 将同一 packet 投影成有界 UI 摘要，Console 在询问行动前显示最近变化，Web 的 `player.decision_context` 也提供可见人物/对象、被动观察、pending ids、活动目标和进行中动作；preview 是只读的，不会提前 acknowledge，也不会暴露完整 beliefs、secrets、Goal 条件锁或宿主账本。

有期限的责任由私有 `ObligationState` 管理，与 Cognition 中较松散的主观 commitments、Timeline 中的世界级日程分离。义务记录 debtor、可选 creditor、截止 step、宽限期、提前唤醒窗口、绑定的 pressure need，以及到期/违约压力。承诺或指派可以在本轮已结算行动的证据下动态创建；动态责任可以携带受严格白名单约束的权威完成条件，完成或解除同样需要证据，模型不能自行宣布 breach。

义务也可以声明权威完成条件，例如角色确实进入指定地点、关键对象真正交付或某条 Plot clock 达到阈值。`ObligationSystem` 会优先依据候选世界事实自动履行，再确定性标记 due/breached；临期义务会唤醒离屏 Agent，违约则推高其绑定的 DriveState need。义务更新和其他权威状态一起事务提交，并且不会直接泄漏到 Rendering。

当一个角色同时承担多个带地点和期限的义务时，`ObligationConflictAnalyzer` 会根据权威空间图计算其独自执行的可行顺序。若没有任何顺序能在宽限期内完成全部义务，角色会收到私有 `hard` 冲突；若只有一个顺序可行，则收到 `constrained` 冲突及可行顺序。分析器不解析标题中的散文含义，也不强迫角色选择某个责任；协商、委托、拒绝和接受违约都仍由角色 Agent 决定。临近的冲突会唤醒离屏 auto/background Agent，显式 dormant 角色仍保持休眠。

责任可以通过 `delegate` 在角色间原子转交，但不能单方面甩给另一个人。每条义务可声明 `forbidden / bilateral / creditor_consent` 三种 delegation policy；原 debtor 与新 delegate 必须同场，双方都必须在本轮真实提交自己的 Agent proposal，并分别产生公开可观察的正向 resolved action。若责任欠向独立 creditor，缺省还要求 creditor 本人同场提案并明确批准。完成条件中的对象也必须对新承担者可见，避免委托动作顺便泄漏秘密。旧责任进入 `delegated` 历史，新承担者获得保留原 deadline、creditor、policy 和完成条件的 active 记录。这样一个角色发现自己做不完之后，可以真正去协商分工，而 Simulation 不能替一个未运行或明确拒绝的 Agent 伪造同意。

### DramaState

后台节奏控制器，维护全局张力，并生成导演指令，例如：

- `stay_course`
- `raise_pressure`
- `inject_crisis`
- `allow_release`

### PlotState

将长线阴谋或大事件实体化为进度时钟，用于给导演系统提供“优先兑现哪条暗流”的依据。

## 当前目录

```text
main.py
src/
├── config/
├── story_engine/
│   ├── components/
│   │   ├── scene_state.py
│   │   ├── simulation_control.py
│   │   ├── narrative_renderer.py
│   │   ├── drama_state.py
│   │   └── plot_state.py
│   ├── systems/
│   │   ├── input.py
│   │   ├── simulation.py
│   │   ├── rendering.py
│   │   └── memory.py
│   ├── session/
│   ├── environment/
│   ├── scenarios/        # 仅通用 ScenarioConfig schema
│   └── prefabs/
└── story_engine_content/
    └── bundled/          # 显式选择的示例故事，引擎从不反向 import
docs/
```

角色 agent 的运行时边界与 Hermes 接入方式见 [`docs/AGENT_RUNTIME.md`](docs/AGENT_RUNTIME.md)。

部分可观察多角色博弈、主动/被动观察和离散事件语义见 [`docs/FORMAL_MODEL.md`](docs/FORMAL_MODEL.md)。

多轮小种子涌现、重放与权威性审计见 [`docs/EPISODE_EVALUATION.md`](docs/EPISODE_EVALUATION.md)。

closure 后继续运行 50～200 step 的世界稳定性、记忆收敛、目标增长、关系振荡与队列审计见 [`docs/SOAK_EVALUATION.md`](docs/SOAK_EVALUATION.md)。

具体故事与引擎的单向依赖、捆绑示例位置和外部内容包约束见 [`docs/CONTENT_PACKAGES.md`](docs/CONTENT_PACKAGES.md)。

## 运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境

在根目录创建 `.env`：

```bash
OPENAI_API_KEY=sk-xxxx
# 或配置所用兼容接口的 key / base_url
```

### 3. 启动

```bash
python main.py
```

### 4. 启动网页试玩

```bash
python web_main.py --host 127.0.0.1 --port 8000
```

然后在浏览器打开 `http://127.0.0.1:8000`。

## 当前状态

- 交付形态仍是简单终端文字冒险。
- 已新增一个与引擎解耦的 Web UI 适配层，可直接在浏览器里试玩。
- 引擎层已切到带离散事件调度的权威循环。
- Arkham 剧本已补上初始状态、storylets、drama、plot entities 的最小示例。
- `ConsoleDriver` 仍然是轻量交互层，复杂导演式介入后续再接回去。
