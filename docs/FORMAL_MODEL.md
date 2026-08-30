# 角色—世界形式模型

Story Engine 采用事件驱动的部分可观察多角色博弈（POSG）作为架构约束，而不是尝试求解一个完整的最优策略。

## 映射

- `S`：Scene、Object、宏剧情、Relationship、Drive、Obligation、Contract 等权威状态。
- `O_i`：角色 `i` 的 POV、被动观察和主动观察结果；尚未交付给策略的新世界事件与社会回应分别保存在宿主拥有的有限 pending 队列中。
- `L_i`：Host 可验证的私人账本，包括 POV 事件收据、Claim 来源、身体压力、义务、协议、日程、地图和已登记目标；它不是公共知识，也不等于角色的完整心智。
- `M_i`：持久 subject 自己的回忆、注意、评价、情绪、信念解释、计划、笔记、动机和选择。Hermes 的 `M_i` 位于其 conversation/JSON memory/tool 上下文。宿主不保留 `M_i` 的镜像。
- `G_i`：Host 登记的目标 watch。它保存来源、调度与权威完成锁；对 Hermes 而言不直接等于欲望或注意优先级，只有权威状态条件能令其 achieved/failed。
- `K`：客观 Claim Entity 集合及其 truth conditions / evidence links；`K_i` 是角色私有 KnowledgeState，只保存该角色对 Claim 的立场和证据来源。
- `C_i^H`：角色 runtime 内部生成的私人候选集合；不会暴露给宿主或其他角色。宿主一侧不存在对应的候选集合。
- `A_i`：角色自己从 `C_i^H` 中选出并提交的单个原子大动作 proposal。宿主只接收 `A_i`，不重排。
- `T`：确定性环境规则、宿主 world/observation 随机流、语义 GM 和 WorldStateTransaction 共同构成的状态转移。
- `R_i`：角色自己的 active `G_i`、needs、relationships、obligations 与风险偏好；不同角色不共享统一奖励。

对每个可行动角色 `i`，宿主维持一一映射
`SceneBody_i ↔ ECSEntity_i(AgentController) ↔ LiveRuntime_i`。初始内容缺少任一项时
Session 不成立；运行中任一绑定脱落时，下一步在时间推进和状态修改前 fail closed。
Claim、Agreement、Relationship、WorldEvent 和 GM 虽然也是 Entity，但不是行为主体，
不参与这条映射，也不会获得角色行动回合。

这里选择 POSG 而不是 Dec-POMDP，是因为故事角色通常具有冲突或不一致的目标。Hermes 是被指派给该角色的长程决策过程：它保存自己的 conversation、原生 JSON memory/tool 上下文，接收环境投递的私人刺激，并按人设与当前证据选择该人物的下一步。宿主不读取或重排这些私人候选，只接收一个 `A_i` 并负责合法性、时间、资源、概率结果和权威结算。宿主一侧没有 Utility Policy，也不运行 Bellman 求解。

因为宿主不选择 `A_i`，它也无法重建角色的动机。角色可以随 `A_i` 附带 `motive_refs`，声明这一步响应的是自己哪个 active Goal、Obligation、Sentiment 或 Drive need；宿主在角色私有 snapshot 中核验后才编入 provenance，引用她并不持有的记录会被驳回。Episode 只记录角色提交了什么、她给出的理由、以及世界如何结算，不把宿主事后推断伪装成角色的真实动机。

临界 `DriveNeed_i` 会唤醒 auto/background 策略，但不自动等于未完成剧情。自然闭合只检查 `actionable_critical_need(i,n)`：need 已达到自己的 critical threshold，且当前受限观察中至少存在一个 Host 验证为 available、对同一 need 有负 delta 的 affordance。普通漂移、无已知解法的压力与 dormant 角色压力只保留诊断。这样既不会在角色眼前有食物却仍严重饥饿时提前结束，也不会因世界暂时没有水源而让 Episode 永远无法形成章节边界。

`use_affordance` Goal 不以语义文本或单个 metadata 字段作为完成证明。令 `u=(actor, object, affordance, step)`，完成证据必须是 Host 在已提交对象事务后派生的 `WorldEventFact`，并同时满足确定性 event id、与 affordance 类型对应的 `object_use/object_relocate/object_set_container_state` kind、`source_ref=step:<t>:actor:<actor>`、subject/object 和 affordance metadata。任一字段不一致都保持 Goal active；因此 GM 输出、注入事件或其他角色的相似行为不能替目标所有者自证完成。

## 动作空间

动作类型固定为五种：

- `observe`：主动观察、检查、搜索或偷听；
- `move`：改变空间位置；
- `interact`：与对象或环境互动；
- `communicate`：向角色表达或回应；
- `wait`：等待、保持现状或让出行动机会。

每种动作只固定类型，具体方法与目标仍使用自然语言 `detail / target`。`interact` 可以额外携带 `affordance_id`，但它只能逐字引用当前观察 `O_i` 中该 target 已有且 available 的对象能力；这是 proposal 对现有能力的稳定引用，不是新的动作类型或成功声明。环境把可见能力和 authored affordance 作为机会投射给角色，但不会因为“能放下”就自动替 Hermes 生成动机。Hermes 自己形成并选择意图；兼容 runtime 才由宿主根据 Trait、Drive、Goal、Relationship、Obligation 与风险给候选计分并采样。环境规则始终裁定硬约束；GM 只补充无法由规则确定的语义结果。已验证 authored affordance 的 `use` 操作由宿主从最终正向行动物化；`engine:take/drop/open/close` 则是从 portable、owner、container_open、可见性和可访问性即时派生的通用物理能力，并编译为现有 `relocate/set_container_state` 事务。内容不能声明 `engine:*` id。动作完成时 Host 会重新验证，因并发或排队而失效的引用只阻塞该动作。GM 漏写、改写 affordance id 或为 blocked 行动声称操作都不能改变该结果。

## 策略与随机性

`Agent` 是完整角色策略，不等同于单次 LLM 调用。Hermes 在自己的长程上下文、记忆和内部决策中完成观察、权衡与随机性，向 Host 只提交一条非空自然语言 action。Host 将该字符串解析为内部 Action IR，再执行合法性、资源与世界结算；外部协议不暴露候选、utility 或人格采样字段。

这仍是性格机制的第一层实现：当前 Trait 以结构化 bootstrap 进入长程主体，但人格/目标条件化的注意竞争、评价理论 appraisal、刺激衰减与真正异步抢占尚未完成，不能把候选 utility 当成已经客观校准的心理模型。

宿主一侧不存在第二套策略：结构化 Trait、关系 Track、临期义务、需求和风险偏好都只作为证据进入角色的输入包，由角色自己权衡，宿主不用它们给候选打分或采样。任何 runtime 都不能提供世界结果的概率、随机数或最终成功声明。

Session 从一个公开可记录的 seed 派生两个互不干扰的宿主随机流：

- `world`：行动在世界中的概率检查；
- `observation`：带噪声的主动或被动感知检查。

没有第三个“角色选择”流：角色在自己的 runtime 内部采样自己的决定，宿主只掷客观结果和"这个角色看清了多少"。宿主随机值由 `(seed, stream, stable key)` 的哈希生成，不依赖调用顺序；世界检查记录固定难度、宿主修正、概率、roll 与结果。同一 seed、step、world version 和输入可重放。Hermes 的主体采样使用独立 per-character seed：评测必须同时固定 Session seed 与 character seed 才能重放角色选择；生产只记录 subject seed fingerprint 和被选 lens/option，不把私人思考公开到世界审计。

语义 GM 不能为不确定行动直接写最终成功或失败。它必须输出一个 `uncertain_outcome`：固定难度、可选的权威 capability 名称，以及 success/failure 两个完整候选补丁。宿主先对两边执行相同的 authority projection：位置分量只能属于当前 move actor，并落在 `{原位置, LegalityEngine 授权目的地}`；其余坐标从两个候选同时删除并审计。随后宿主从 SceneState 的 capabilities/skills 生成有限修正，使用对应随机流选择一个分支，再把唯一被选中的补丁合并进 WorldStateTransaction。模型提供的 probability、roll 或数值 modifier 属于非法字段；未选分支不会进入 Cognition、Memory 或 Rendering。

短期社会评价不直接等于长期奖励或关系真相。已提交、对角色可观察的社会行动可以形成受限 `social_impact`；宿主将其映射为该角色私有 Sentiment，并由 Sentiment 的强度与衰减构成后续 `R_i` 的一部分——它作为证据进入角色的输入包，由角色自己决定要不要照它行动，宿主不用它给选项打分。少量、确定性的 Track effect 用来沉淀长期关系，避免一次事件把永久 trust/favor 粗暴改满。不可观察事件不进入该角色的 Sentiment。来源始终由宿主绑定到已验证 action 或 Agreement performance resolution，关系轨道再指回该 Sentiment；模型提供的自由文本理由只用于角色理解，不作为因果权限。

非社会临时条件通过 `ModifierState` 进入 `R_i`。语义 GM 只能从宿主 catalog 选择 kind，并提供已提交行动支持的 target、source、定性 intensity 和 reason；duration、stacking 与精确 magnitude 属于宿主定义。Modifier 既不修改随机数也不给行动打分：它是角色能看到的一项临时处境，由角色自己权衡。其角色可见归因服从 `O_i`，但宿主另存 `Modifier <- ResolvedAction` provenance；隐藏来源可以对角色未知，同时仍对权威审计可知。

持续需求 `DriveNeed_i` 保存有界的宿主因果 ledger。对象 affordance/语义压力产生 `DriveNeed <- ResolvedAction`，义务期限产生 `DriveNeed <- Obligation`，确定性漂移产生 `DriveNeed <- Clock`。ledger 不属于 `O_i`，但与 DriveState 一起事务化，使后续 `ResolvedAction <- DriveNeed <- cause` 可以重放。

Claim 的客观 truth status 属于 `S`，但不会直接进入 `O_i`。角色只能通过成功主动观察已连接且可见的 Evidence，或通过同场角色的 communicate 更新 `K_i`。传播内容可以是谎言，因此接收的是 asserted stance，不是 Claim 真值；接收 confidence 由宿主根据 trust 与现场证据确定。

Evidence observation 由 Host 从 `observe(target)`、当前可见对象和 Claim Entity 的 `supports/refutes` 边确定性派生。它只告诉角色“该证据支持或反驳哪个命题”，不读取或暴露 `truth_status`；模型输出的 `claim_discoveries` 会被 Host 派生集合替换。没有结构化 evidence edge 的开放调查仍由语义 GM 处理，因此核心不需要认识“账册、血迹或信件”等故事对象。

Claim communication 使用 `communicate(target, claim_id, claim_stance, evidence_refs)` 的非权威引用。Input 只保留发送者 `K_i` 中存在的 Claim、当前可见接收者，以及发送者已知且当场可见/持有的 evidence；`claim_stance` 是角色选择公开表达的立场，可以与私有 stance 不同，从而允许撒谎。正向沟通后 Host 替换模型自报的 Claim knowledge update，再由 ClaimKnowledgeSystem 验证同场与可出示性并根据 evidence/trust 计算接收置信度。该转移改变 `K_j`，不改变 `K` 的 truth，也不强迫 `B_j` 形成特定情绪或行动。

单边整件交付使用 `interact(target=object_id, delivery_recipient=actor)`。Input 只接受发送者当前持有、公开、portable 的可见对象和同场可见接收者；语义层仍决定对方是否实际接住/允许交付，只有正向动作才由 Host 把 exact object/recipient 编译为 `relocate(owner=recipient)`。完成时再次验证所有权与同场，陈旧交付只阻塞该动作。它不用于部分堆栈、互换或附条件交易；这些仍必须由双方 proposal 与 Exchange/Agreement 原子事务处理，单方面语言不能制造对方同意。

同理，角色 Entity 的完整 actor row 属于 `S`，而不是 `O_i`。观察函数使用 `public_projection(S_j)` 生成他人状态，只公开物理位置、外显姿态与内容明确声明的字段；`self_projection(S_i)` 额外包含角色自己的能力和内在状态，但仍删除导演控制。宿主 transition、Legality、概率和 motive 系统继续读取完整 `host_projection(S)`。因此“同场”只产生可观察身体信息，不等价于读取对方能力、恐惧、偏见或剧情用途。

对于在同一 completion batch 内完成移动的角色，观察位置不是简单使用提交后的 `location_i(t+1)`。Host 保存 `W_i(t)={location_i(t), location_i(t+1)}` 的去重窗口；Cognition 允许角色观察发生在该窗口任一端点的非 hidden 行动，但不扩展到第三个地点。未移动角色的窗口退化为单点。若角色在本批次提交后才动态出生，则 `present_i(t)=false` 且 `W_i(t)=∅`：它可以从下一次 observation 读取当前世界，却不会倒灌获得出生前的行动、交换、知识传播或社会后果。这样角色不会因为事务先写入新坐标而遗忘刚离开的现场，也不会获得与本次移动或自身存在区间无关的事件。

交流传播使用同一个窗口，而不是提交后的坐标相等。若 communicate 行动发生于地点 `l`，普通 belief、WorldEvent response 与 Claim report 只有在 `l∈W_source(t)∩W_target(t)` 且行动 outcome 属于 `success/partial/complication` 时才能传播。于是接收者在本批次离开原地时仍可能听见离开前发生的交流，真正异地或失败的交流仍不能制造知识。

外部社会与心理后果使用相同证据：由另一角色造成的 Sentiment、Modifier 或 Drive delta 只有在来源行动非 hidden、结果为正向，并且行动地点属于来源与受影响者窗口交集时成立。自身行动造成的 Modifier/Drive 不要求第二个观察者；World 环境 Modifier 只要求事件地点属于目标窗口。窗口验证发生在相应事务副本上，Drive 即使与移动坐标同批提交，也会同时读取提交前后的地点，而不会被字段应用顺序改变。

WorldEvent 的 `direct_witnesses` 也由同一个 `W_i(t)` 计算。对象状态、物品操作、交换与局部拓扑变化发生在地点 `l` 时，只有 `l∈W_i(t)` 的角色成为直接见证者；事件写入 Cognition 时保存 `event.location`，而不是角色提交后的当前位置。公共事件 attention budget 在决定现场者强制名额时同样使用窗口，避免“见证事实已记录，但因刚移动而失去事件注意力”的内部矛盾。移动事件本身继续使用分别派生的 departure/arrival witnesses。

全局 `scene_flags` 也先经过 `public_scene_projection`。默认时间阶段、天气、警报和公共环境状态可以进入所有角色的 observation；世界版本、队列、schedule、commitment、Storylet/causal ledger 和内容私有条件只留在 `S`。公共 flag 的真实差值生成 `I(e)={(scene,scene,scene_flags.path)}`；私有 flag 即使改变，也不会凭借“全局存储”自动广播到所有 `O_i`。

## 主动与被动观察

- 被动观察来自 observation function：世界事件或其他角色的可见行动完成后，环境按地点、可见性和容器边界自动投递，不占用新的行动。
- 主动观察是 `observe` 动作：角色主动投入一个行动完成事件来取得额外细节。公开行为写入 `result`，只对行动者可知的发现写入 `private_result`。
- 新的被动 WorldEvent 会唤醒一次离屏 auto/background 策略；只有 perception 已交付给 runtime 后才从 pending 队列确认，因而不会丢失，也不会每轮重复触发。
- 对已知事件作出的新社会回应仍是新的 observation。宿主使用 response identity 而不是 event identity 去重，避免“我早就知道事故”错误吞掉“对方刚刚为事故道歉”这一新信息。
- 已提交的普通对象属性由宿主以事务前后差值形成 change set，再映射到 `I(e)`；只有对象所在地的观察者得到这一 Event，hidden 对象只进入真实操作者的 `O_i`。没有差值或伪造 change ledger 不产生 observation。语义空间拓扑写入始终拒绝；宿主命令批次 `T_t` 则在副本图 `G'_t` 上验证端点、冲突与引用闭包，只有整个批次合法时才令 `G_{t+}=G'_t`，并把真实 edge delta 映射为 `route_opened/route_closed` Event 与 `connected_to` impact。
- 人工对象补丁批次 `H_t` 只接受已有对象上的严格 JSON 普通属性，且 `keys(H_t)` 与生命周期/拓扑/私有字段不相交；先在 Scene 副本计算 `ΔH_t`，任一项非法则 `ΔH_t=∅` 且不提交。只有非空差分才递增版本并形成带稳定 id 的 `object_state_changed`，因此 host intervention 与语义结算共享同一观察因果语义。
- 同一步的对象补丁 `H_t` 与图命令 `T_t` 不按 API 调用顺序分别提交，而由外层事务计算 `M_t(H_t,T_t,S_t)`。仅当两个子验证都成功时，`S_{t+}=commit(S'_t)` 且 `world_version+=1`；否则 `S_{t+}=S_t`，`clock_{t+}=clock_t`，action queue、Agent decision、Event 与 episode step 均为空操作。retry 因此仍使用同一个 `t` 与 deterministic ids。
- 权威 phase 以 `C_t=(ECS_t, Registry_t, Queue_t, Clock_t)` 为 checkpoint，Dispatcher 输出保存在未发布缓冲 `D'_t`。若 WorldEvent commit barrier 前任一 phase/callback 抛出异常，则 `(C_{t+},D_{t+})=(C_t,D_t)`，清除候选 context 并令 `authoritative_step_failed=true`；模型已经执行过的不可逆私有计算不在 `C_t` 中，但其输出未提交且不可观察为世界事实。barrier 后有 `C_{t+}=commit(C'_t)`；每个 delivery phase 从 `L_k` 短 checkpoint 开始，失败时恢复 `L_k` 而保持 `C_{t+}` 与先前 `L_{<k}`，同时产生可重试诊断。已经发送到外部进程/订阅者的副作用不能假装撤回，只能显式报告。
- UI history 是提交状态的投影而不是另一条事实源。令 `π_status(context)∈{aborted,rolled_back,delivery_failed,committed}`；只有后两者满足 `committed=true` 并使产品 step counter 前进。`aborted/rolled_back` 可记录为非叙事 system diagnostics，但不得生成角色行动、世界余波或“无显著变化”等貌似发生过的 turn。异常原文不属于 POV-safe projection。
- delivery receipt `R_t=(k,ctx_t,n)` 只在 `C_{t+}` 已提交且交付 phase `k` 失败时存在。若 `R_t≠∅`，新的权威 transition 被拒绝；`retry(R_t)` 仅计算 `L_k…L_m`，保持 `C_{t+}`、clock、queue、Agent calls 与 episode step 不变。成功令 `R_t=∅`，失败令 `R_t=(k',ctx'_t,n+1)`。episodic record key `h(namespace,actor,t,type)` 采用 upsert，使重复 `L_memory` 对同一角色/step 幂等。

旁观者可以被动看到“某人正在检查门锁”，但不会自动获得行动者发现的锁孔铜屑。

## 离散事件时间

引擎使用与成熟离散事件模拟相同的基本队列形式：

```text
(completion_time, stable_sequence, event)
```

同一逻辑时间提交的动作先全部排队，然后世界时间跳到最早完成时刻。相同完成时刻的动作组成一个 simultaneous batch，由 Simulation 和资源竞争系统统一裁定。该设计参考 SimPy 等离散事件框架的事件队列语义，但同时间事件不会按插入顺序逐条修改世界，而是合并结算，以保护多 Agent 公平性。

耗时动作在完成前保持为 ongoing。其他同场角色可以感知其外在动作类型和当前可见目标，并在自己的决策点响应；引擎不会把行动者的自然语言私有意图直接泄漏给旁观者。长动作完成时会携带提交时与当前的世界版本差，规则层重新验证目标、位置和访问前提。

动作的持续时间由环境根据动作类型决定，Agent 和 GM 不能自行缩短。思考耗时未来可以成为调度成本；当前只记录这是模型边界，尚不模拟真实推理延迟。

离屏策略的运行频率不是奖励函数的一部分。世界事件、事件回应、期限和关键需求形成一次性中断；可验证的 Agent-grown Goal 则形成有最小间隔的 continuation wakeup。这样半马尔可夫策略可以跨多个动作继续推进后果链，同时不会把每个 active 自然语言目标都变成每个时间点的模型调用。

认知更新与调度中断是两个不同函数：`belief_update_i(e)` 可以对 dormant 角色成立，而 `attention_enqueue_i(e)` 只有在 controller autonomous 且 activation policy 非 dormant 时成立。这样休眠角色不会失去客观经历，但环境 observation 也不能绕过手动控制边界。Episode closure 只等待可由当前 policy 消费的 attention；休眠账本保留为恢复诊断。

对可运行角色，attention queue 使用单一宿主函数排序。令基础重要度为 `p(e)=priority(kind,role)`，等待提升为 `a_t(e)=min(100-p(e), floor(max(0,t-step(e))/4))`，则有效重要度为 `p_t(e)=p(e)+a_t(e)`；比较键依次为有效重要度、等待提升、发生 step 与稳定 id。priority 只由已提交 Event kind、response kind 与角色是否为 subject 确定，不接受 Agent/GM 数值；WorldEvent 和 event-response 在同一比较中选择。四十条的有限容量以高排名项为主体并预留最多四个最老项，使关键后果不被移动噪声淹没，同时让被保留的普通事件不会在持续输入下永久饥饿。Scheduler、`O_i` 的 pending slice 与 acknowledge 集合来自同一个带当前宿主 step 的排序视图，保证调度和实际消费一致并可重放。

对 public Event，先计算 epistemic projection `W(e)`，再独立计算 interrupt projection `A(e)⊆W(e)`。令预算为 `B`，则 subjects 与 `location(e)` 现场者构成强制集 `F`；普通名额为 `max(0,B-|F|)`，候选先按结构化 Goal dependency 命中排序，再以 `sha256(event_id|actor)` 稳定排序。所有 `i∈W(e)` 都执行 belief update，只有 `i∈A(e)` 执行 attention enqueue；dormant 从候选中排除但不从 `W(e)` 排除。于是公共知识一致性不依赖计算预算，而同一 Event 的自动 Agent fan-out 有确定上界（强制现场/当事人除外）。

对任一实际决策者（Hermes、规则 runtime 或 human override），先生成同一观察 `O_i^t`。设其中 attention delivery slices 为 `L^E_i(t)` 与 `L^R_i(t)`，各自至多二十条，则成功形成 decision proposal 后仅执行 `pending_i := pending_i \ (L^E_i∪L^R_i)`；禁止用控制器类型触发 `pending_i:=∅`。人工 preview 只是 `π_ui(O_i^t)` 的纯投影，不执行该差集，因而查看界面不会改变 Episode closure 状态。

Continuation 使用重复 action kind/target 的宿主账本做有限指数退避，而不是把模型自称的“我卡住了”当作状态。退避改变下一次决策时间，不改变目标真值；目标失败仍必须由 transition model 中明确的权威条件证明。核心新增 `not_exists` 条件，用来表达对象或地点实体确实已从状态中消失，而不是把暂时不可见误判为不存在。

新 observation 可以作为目标调度的外生中断，但必须同时满足 epistemic boundary 与结构相关性。宿主把状态 transition 投影为有限的影响集合 `I(e)={(scope,target,path)}`，把 `G_i` 的隐藏完成/失败查询视为依赖集合 `D(g)`；只有 `e` 已进入 `O_i` 且 `I(e)` 与 `D(g)` 在宿主定义的路径族上相交时，continuation backoff 才归零。实体引用相交保留为旧内容兼容边界。容器开闭对嵌套对象 accessibility 的影响由世界模型确定性展开，不由 Agent 猜测。同一变化若未进入 `O_i`，即使存在于全局 `S`，也不能影响角色 `i` 的调度。这保持了 POSG 的部分可观察性，而不是让宿主用全知状态偷偷提示最优行动。

## 权限边界

```text
角色策略主导：候选生成、效用评估、带种子行动采样、计划和交流内容
Environment 主导：时间、范围、资源、并发、权威状态
GM 主导：规则无法穷举的语义后果
Rendering 主导：已提交事实的文字表达
```

无论运行时是默认 LLM、Hermes 还是人类控制器，proposal 都不能直接成为世界事实。

Rendering 还执行独立的最小权限投影：即使 POV 已过滤，单轮 `state_updates.actor_states`、social director flags、Storylet/Conflict 建议、causal rule 和内部 notes 也不会原样进入 Narrator。这样渲染模型不能从旁路恢复被观察函数删除的幕后状态。

Timeline 给 Rendering 的投影只保留公开 day phase/phase transition，以及玩家本人确实错过的 commitment 结果；其他角色的 due/upcoming schedule、carrier states 和 transition pressure 不进入 Narrator。
### Agreement offer capabilities

An authored agreement offer is a Host-owned capability, not a Storylet and not
an authoritative Agent payload. The proposer observes only a bounded summary
when all counterparties and referenced assets are currently available. Its
formal action is a reference:

```text
communicate(propose, agreement_template_id)
communicate(accept|reject|withdraw, agreement_id)
```

The Host expands a proposal into fixed parties, terms, expiry, transfers,
services, escrow and delegation rules. Responses may reference only a pending
Agreement in the actor's private snapshot. The semantic GM may decide whether
the communication succeeds, but cannot substitute an Agreement ID, party,
asset or hidden completion condition.

For ordinary asset offers the Host derives a bounded capability directly from
the actor's current POV: public portable objects owned by the actor may be
offered, and public portable objects owned by a visible counterparty may be
requested. The Agent selects references on either side; the Host derives a
stable ID and exact transfers. Objects absent from the POV, including contents
behind an opaque closed container, never enter this capability catalog.

A delivery-service capability is likewise derived when a visible counterparty
owns a visible portable object. The proposer selects that object, one qualitative
deadline (`urgent`, `soon`, or `flexible`), and optionally one visible owned
asset as escrow payment. The Host compiles a delivery Obligation whose only
completion evidence is authoritative ownership by the proposer; deadline
offsets are fixed at 1/3/12 steps. Payment release and refund rules are fixed by
the Host and cannot be authored by the Agent or semantic GM.

The proposer may instead select one destination exposed by the current
location's public topology. The completion predicate then becomes authoritative
object location at that destination. Only immediately connected known
locations enter this catalog; the service cannot reveal undiscovered map nodes.
Moving there and using the normal Host `drop` affordance is therefore necessary
for fulfillment.

Each character has a Host-owned private map consisting of known locations and
known route edges. Entering a location additively teaches that node and its
public outgoing edges; an absent exit does not silently erase an older
remembered or reported edge. Scenario seeds may grant prior map familiarity. A remote move is legal
only when its destination and every path edge are present in that actor's map.
The Host may compute the next hop, but never uses undiscovered topology to help
an Agent that guessed a hidden place name.

Route knowledge may be communicated as a directed edge. Input proves that the
speaker currently knows the edge and can address the recipient; a positive
communication records `basis=reported`, reporter identity and learned step in
the recipient's private map. It does not inspect or repair against current Host
topology. On attempted travel, the Host compares the next remembered edge with
the live current location and returns `stale_route` if it is closed.

A single communication may carry a simple path of two to eight distinct nodes.
Input validates every directed edge against the speaker's private map; one
unknown or discontinuous edge rejects the whole reference. Publication records
each edge atomically with common reporter and learned-step provenance.

A failed remembered edge becomes a Host-owned private `NavigationProblem` for
that actor. It records the failed edge, intended destination, discovery place
and step, the Host legality `failure_rule`, any route still reachable in the actor's own known map, and a related
delivery Obligation with remaining time when applicable. It never consults
secret Host topology, creates no Goal, and prescribes no response; it only wakes
a background Agent so that the Agent may reroute, investigate, ask for help,
renegotiate, wait, or accept failure. Leaving the discovery location resolves
the immediate local problem.

Episode closure also treats every non-terminal Timeline commitment as an
unresolved world seed. A scheduled ceremony cannot disappear merely because
the currently visible Goal has completed: the Host must first advance it to
`resolved`, `missed`, or `cancelled` from clock and location truth. This check
is policy-controlled for chapter cuts, but enabled by default for complete
Episode evaluation.

Episode closure counts active `NavigationProblem` records owned by autonomous
characters as unresolved attention. Problems retained by explicitly dormant or
non-autonomous characters remain visible in closure diagnostics but do not
block an otherwise complete Episode, matching the same controllability boundary
used for pending events and responses.

An active navigation problem is also a valid provenance source for an
Agent-proposed Goal. A `reach_location` resolution may name any location in the
actor's private map; the Host stores only an authoritative final-location lock.
It does not copy the remembered route into truth or guarantee reachability, so
each actual move remains subject to live topology and may generate a new
problem.

Episode evaluation projects only explicit provenance into a causal graph. In
particular, a navigation recovery may form
`Goal <- NavigationProblem <- movement_failure:failure_rule`; WorldEvent and
response edges similarly use their Host source refs and response ids. An
Obligation also carries Host-owned immediate provenance: authored seeds point
to `scenario`, service duties point to their Agreement, action-created duties
point to the verified resolved-action batch, and delegation points to the
original actor-qualified Obligation. This permits
`WorldEvent <- Obligation <- Agreement/resolved_action/scenario` without
inferring causality from summaries. A response id that is actually present in
the receiving actor's private Cognition may itself ground a new Goal, yielding
`Goal <- event_response <- WorldEvent`; another actor cannot cite that private
response as its own source. Graph
depth is diagnostic rather than utility or quality reward, and cycles are
bounded instead of being semantically repaired by a model.

Agreement creation is also attributed to the Host-verified positive
communication batch that proposed it. The Agent may choose a visible offer or
asset/service reference, but cannot write `source_kind/source_ref`; the Host
stores `resolved_action:step/actor` on the Agreement Entity. Consequently a
service story can expose
`WorldEvent <- Obligation <- Agreement <- resolved_action` rather than treating
the formal promise as an unexplained root.

Terminal Agreement transitions carry a separate Host-owned source. Settlement,
rejection, withdrawal and countering point to the verified response action;
expiry points to the authoritative clock step. A settlement node therefore has
two explicit parents—the Agreement being resolved and the action that resolved
it—and a service Obligation may depend on that settlement node. The evaluation
graph is a DAG with sets of parents rather than a single-parent tree, so chain
depth follows the deepest explicit path instead of whichever edge was observed
last.

Agreement performance is a third, distinct consequence. When linked service
Obligations become fulfilled, breached, or cancelled, evaluation creates an
`AgreementPerformanceResolution` node with parents for the settled Agreement
and every matching actor-qualified Obligation. The projected performance
WorldEvent points to this resolution node, so “the contract exists” is never
used as a substitute explanation for “the service was breached.”

Escrow custody and escrow disposition are also separate nodes. Deposit creates
`AgreementEscrow <- AgreementResolution:settled`; later release or refund
creates `AgreementEscrowResolution` with parents for both the custody lot and
the triggering AgreementPerformanceResolution. The escrow WorldEvent points to
that exact custody resolution, preserving which held asset moved and why.

Completed bilateral exchange events never use a free-standing semantic
exchange id as their causal root. Agreement-materialized transfers point to
`AgreementResolution:settled`; ordinary consent-backed exchanges point to the
Host-verified same-step actor action batch. The exchange id remains in the
WorldEvent id for identity and replay, while provenance describes why ownership
changed.

Physical WorldEvent identity and provenance are likewise separate. Movement
and validated object lifecycle operations point to the responsible actor's
resolved-action batch; ordinary object-property changes do so only when the
Host-derived change ledger matched the changed object to a positive action
target. Host edits retain their stable host change id, and unattributed public
scene transitions are not guessed onto whichever actors happened to act in the
same step.

Timeline attendance uses an explicit resolution node rather than the
commitment id alone. `TimelineResolution(commitment,status)` has parents for the
Host clock step, the authored Timeline commitment, and Host-evaluated
`actor_presence/actor_absence` at the required location. The attendance
WorldEvent points to that resolution; day-phase changes point directly to the
clock step that selected the phase.

Private Claim knowledge is also a consequence, not a copy of global Claim
truth. A Host-verified evidence observation creates
`ClaimKnowledge_i <- EvidenceObservation <- resolved_action + Evidence`; a
verified report creates `ClaimKnowledge_i <- ClaimReport <- speaker action`.
An Agent Goal sourced from a Claim points to that actor-qualified private
knowledge node, so another character's knowledge and the Claim Entity's truth
cannot silently substitute for the actor's own epistemic state.
