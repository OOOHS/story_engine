# 角色 Agent 运行时边界

## 目标

故事引擎中的每个角色都是持续存在的 agent，但“角色是 agent”不等于让 agent 直接修改世界。

角色负责：

- 依据自己的身份、目标、记忆和有限视野形成判断；
- 从少量原子大动作中选择本次要观察、移动、互动、交流或等待，并用自然语言填写具体方法与目标；
- 保持跨回合的认知与计划。

世界引擎负责：

- 决定 agent 有资格感知哪些事实；
- 汇总角色 proposal；
- 根据权威状态和世界法则结算结果；
- 以原子方式写回世界状态。

因此角色输出始终是 `proposal`，不是已经发生的事实。

## 分层

```text
ScenarioConfig                     # 故事种子：人物、地点、初态、规则、潜在矛盾
  -> Entity + AgentController      # 可序列化的角色声明
       -> AgentRegistry            # session 内角色与 live runtime 的绑定
            -> CharacterAgentRuntime.decide(perception)
                 -> AgentDecision  # 角色原子行动提议
                      -> ActionEventQueue
                           -> completion batch
                                -> Simulation
                           -> authoritative state updates
```

关键对象：

- `AgentController`：ECS 组件，只保存 runtime 名称和配置，不持有框架对象。
- `AgentRegistry`：session 级注册表，持有 live runtime。
- `AgentPerception`：严格按角色视角过滤后的输入。
- `AgentDecision`：角色的私有想法和公开行动提议。
- `CharacterAgentRuntime`：所有 agent 框架必须实现的最小协议。
- `AgentScheduler`：决定角色本轮是前台、后台还是休眠，不负责修改世界。
- `Cognition`：角色私有的主观信念、秘密、承诺和关注点。

引擎不再自带任何进程内 LLM runtime。角色的思考完全属于角色自己的 agent，宿主没有第二条“自己替角色选行动”的路径，因此也没有内建的兼容 runtime 可以退化到。`CharacterConfig.agent_runtime`、`AgentController.runtime` 均为必填字段，没有默认值；找不到匹配 factory 会在 `register_agent()` 直接报错。测试和本地跑通需要一个哑 runtime 时，调用方显式传入自己的 factory（例如恒定 `wait`），这样"角色为什么这么做"永远有明确归属。

Hermes 采用更强的 subject-owned 边界：`CharacterEntity` 是世界中的身体和法律/资产锚点，长程 `HermesCharacterAgent` 是被指派给该角色的决策过程。Host 只投递 POV-safe 身体视图、可执行能力、被动刺激与主动任务结果；Hermes 按人设、私有知识和本轮证据选择这个人物的下一步，持有跨轮 conversation、JSON memory、注意、评价、动机和计划，并只向 Host 提交一个最终行动 proposal。提示词把 Hermes 写成“负责这个人物的行动”，而不是“你就是这个人”；自治性仍由“只有该角色的 agent 能提交其 proposal”保证。宿主不再对任何 runtime 做候选打分，因此没有把 agent 降级为候选生成器的路径。

Hermes subject 目前已有 `SubjectInbox` 基础层：被动观察、主动观察结果和世界信号以稳定 message id 去重，只有形成合法决策后才确认；失败调用保留消息供重试。消息按 Host 可验证的基础优先级排序，但“人格/目标相关的注意竞争、异步运行中抢占和自然衰退”仍是待实现的 Global Workspace 层，当前不能宣称已经完成。

Host 私人状态通过 `SubjectLedgerProjector` 变成版本化增量，而不是每轮完整上下文。Host 保留 POV/结算所需的事件收据、Claim 来源、身体压力、义务、协议、日程、路线和已登记目标；Hermes 独占计划、focus、appraisal、情绪、私人推断、承诺、笔记和长期回忆。Hermes runtime 不执行 Host Chroma 检索/归档，也不把这些心智字段写回 ECS。Host `SentimentState` 暂时只作为兼容策略和社会规则代理，不会投射为 Hermes 的真实感受。完整字段表见 `SUBJECTIVE_STATE_OWNERSHIP.md`。

## 原子动作协议

角色运行时最终返回五种动作之一：

```json
{
  "thought": "私有判断",
  "action": {
    "kind": "observe | move | interact | communicate | wait",
    "detail": "自然语言说明具体做法，不宣布结果",
    "target": "可选的主要目标"
  }
}
```

Agent 不能填写权威结果或动作耗时。环境将同一逻辑时间的 proposal 全部排入事件队列，按动作种类决定持续时间，再把最早完成的一批动作交给 Simulation。

耗时动作进行期间，其他同场 Agent 的 perception 只会看到外在 `action_kind` 和仍然可见的 target，不会看到行动者的完整 detail。它们可以在自己的下一个决策点介入；动作完成时，环境会基于最新世界重新检查原目标和前提。

`observe` 是主动感知，会占用动作；同场公开事件自动形成被动观察。主动观察的公开过程和私有发现分开保存，旁观者不会因为看见某人检查物品就自动获得其发现。

## Hermes 接入方式

Hermes transport 有两种等价的进程边界实现：生产环境可使用
`HermesContainerConversation`，由 Docker 打包 vendor runtime；本地开发或单机试玩
可使用 `HermesLocalProcessConversation`，直接启动宿主机上的 Hermes entrypoint。
两者都为每个角色维护一个长期 `--subject-server` 子进程，复用同一套 marker JSON
协议、subject memory、超时和关闭语义。Docker 是依赖/资源封装，不是角色主体协议的
必要条件；本地模式仍然不是把多个角色 import 到 Story Engine 同一进程里共享线程。

Story Engine 不应 import 或修改 vendor Hermes。Hermes 仍然遵守“项目薄壳 / vendor runtime / host launcher”的边界。项目薄壳只需提供一个 conversation factory：

```python
from src.story_engine.agents import HermesCharacterAgent
from src.story_engine.session import create_session


def make_hermes_runtime(entity, config):
    return HermesCharacterAgent(
        conversation_factory=project_owned_hermes_conversation_factory,
        config=config,
    )


session = create_session(
    scenario,
    agent_runtime_factories={"hermes": make_hermes_runtime},
)
```

剧本角色只声明：

```python
CharacterConfig(
    name="角色名",
    role="身份",
    personality="性格",
    goals=["目标"],
    agent_runtime="hermes",
    agent_config={"enabled_toolsets": ["memory", "planning"]},
)
```

`project_owned_hermes_conversation_factory` 实现窄 `run_subject_turn(packet)` 边界；仅实现旧 `run_conversation(prompt)` 的测试/兼容适配器会收到同一 packet 的 JSON 编码。仓库默认和评测支持路径始终代理到容器；宿主进程不 import、实例化或修改 vendor `AIAgent`。若实验者另建 host-import 对照线，必须放在独立应用/评测模块并明确标注，不能与默认容器结果混报。Story Engine 核心不关心 Hermes 的配置目录、工具集或内部 loop。

当前仓库已经提供默认的容器黑盒实现：

- `src/story_engine/agents/hermes_container.py`：宿主 launcher/conversation
- `docker/hermes-story/entrypoint.py`：项目自有容器入口
- `docker/hermes-story/config.yaml`：无密钥的容器配置
- `docker/hermes-story/Dockerfile`：封装 vendor Hermes

使用方式：

```python
from src.story_engine.agents import (
    HermesContainerConfig,
    make_hermes_container_runtime_factory,
)

factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(
            image="hermes-story:latest",
            allowed_toolsets=("memory",),
        )
    )

session = create_session(
    scenario,
    agent_runtime_factories={"hermes": factory},
)
```

宿主通过 stdin 发送：

```json
{
  "protocol_version": 1,
  "agent_id": "entity uuid",
  "subject_packet": {
    "subject_protocol_version": 1,
    "subject_id": "entity uuid",
    "wake": {"step": 12, "body": {}, "visible_world": {}},
    "messages": [],
    "identity_bootstrap": "仅首轮存在，含 persona_constraints"
  },
  "enabled_toolsets": ["memory"]
}
```

默认生产 transport 以 `--subject-server` 启动每个角色的容器进程，并通过 JSON-lines 连续发送 turn；入口只构造一次 vendor `AIAgent`，所以 conversation 和 Hermes 原生 memory/tool 上下文可跨轮持续。角色注销、同名实体替换或 registry 关闭时会关闭对应进程。显式注入 `command_runner` 的单元测试仍使用 one-shot transport，不应与生产生命周期混报。

容器只在 stdout marker 中返回：

```text
===STORY_AGENT_JSON_BEGIN===
{"protocol_version":1,"agent_id":"entity uuid","content":"{...角色 AgentDecision JSON...}"}
===STORY_AGENT_JSON_END===
```

宿主要求 stdout 中恰好一个 envelope，并校验协议版本、响应 `agent_id` 与请求角色一致、`content` 非空且总输出不超过宿主上限；`final_response/response/text` 等旧别名不会被接受。宿主不 import Hermes，不猜测普通 stdout 日志，也不读取 `.env`。Docker 命令使用参数列表而非 shell；network 名称、镜像名和正 timeout 先经过宿主校验。密钥只通过 host 配置允许的 `-e KEY` 名称继承，值不会出现在命令或日志中。Scenario 请求的 toolset 必须同时存在于 host `allowed_toolsets`，重复项会去重，未授权项被过滤。

真实 Episode/Sweep 不需要为每个内容包再写一份 Hermes Session factory。
`scripts/eval/run_hermes_episode_sweep.py` 接受一个返回 `ScenarioConfig` 的
工厂，深拷贝配置后把其中每个行为角色统一绑定到 `hermes` container
runtime；原内容对象不会被修改。GM 固定使用通用 `HostRuleSimulationControl`，
Narrator 固定使用事实型 `HostRuleNarrativeRenderer`，因此只有角色决策调用
Hermes，权威结算和评测文本不会暗中调用其他模型。

```bash
python scripts/eval/run_hermes_episode_sweep.py \
  --scenario-factory src.story_engine_content.evaluation.minimal_investigation:build_minimal_investigation_scenario \
  --image hermes-story:latest \
  --seeds 0,1,2 \
  --steps 12 \
  --max-agent-decisions 40 \
  --stop-on-closure \
  --output artifacts/hermes-investigation
```

正式结构验收可追加 `--strict-quality`：权限或执行失败返回 2；结果权威但出现停滞、低闭合率、开放目标积压等任一结构质量标记时返回 3。默认不启用，因为这些标记只负责退化报警，不能替代 transcript 的人工审美判断。

`--max-agent-decisions` 是整个 sweep 共用的 Host 硬上限，默认 40。每次真正
进入 Hermes Docker 调用前原子扣减；失败调用和 `--verify-replay` 重放也计入。
达到上限后后续角色决策 fail closed，不会用规则行动伪装模型结果。最终
`summary.json` metadata 和 launcher 输出都会记录 configured、consumed、remaining
与 exhausted，不记录价格、凭据或环境变量值。

Launcher 默认把仓库的 `docker/hermes-story/entrypoint.py` 与 `config.yaml`
只读挂载到容器固定位置。因此修改项目薄壳、协议 prompt 或无密钥配置不需要重建
包含 vendor Hermes 的镜像。两个文件在启动前必须存在，路径不能含 Docker mount
分隔字符；报告 metadata 保存各自 SHA-256 与是否启用挂载，不保存环境变量值。
显式传空 `--entrypoint-path` / `--config-path` 才会使用镜像内 COPY 的版本。

Launcher 不加载 `.env`。调用它之前由宿主 shell 设置需要继承的环境变量；
`--environment-keys` 仅控制传给 Docker 的名称。`--allowed-toolsets` 是宿主
上限，`--requested-toolsets` 是本次评测请求，两者交集才进入容器。报告元数据
只记录镜像名和 toolset 名，不记录环境值。不同 seed 会创建相互隔离的 Session
和 Agent conversations；`--verify-replay` 会再次从同一 Scenario seed 建立全新
Session，但外部模型若非确定性，replay mismatch 应被视为证据而非隐藏。

Launcher 在创建任何 Session 前执行一次 `docker image inspect` 预检。镜像缺失
或 daemon 不可用时整个任务立即失败，不会让每个 seed、每个角色分别重复撞同一
基础设施错误。Hermes envelope 先验证 `protocol_version/agent_id/content`，随后 `content` 还必须是 JSON object，并且至少含
一个可执行 action 或一组 subject deliberation candidates；普通解释文字、空对象或残缺协议会 fail closed，不能
被通用自然语言 fallback 猜成 `interact`。这种 Input 阶段异常会回滚整步，保留
尚未确认的事件注意力；EpisodeRunner 记录一次 `authoritative_step_failed` 后立即
停止，避免重复付费、重复调用和把基础设施失败统计成角色停滞。

## 信息安全与叙事公平

Agent 不能获得完整 `SceneState`。`InputSystem` 为每个角色构造独立的 `AgentPerception`：

对象状态固定分为三层，不能在调用方临时决定是否过滤：

- **Host 权威状态**：`SceneState.get_snapshot()`，包含完整 affordance、`policy_tags`、`stack_key`、容器能力和私有字段，只供事务、合法性、需求与资产系统使用；
- **GM 语义快照**：`SceneState.get_semantic_snapshot()`，保留开放语义结算所需的 affordance id、requirements、need effects 与物理事实，但剥离 Host 的策略标签、堆叠键和导演型角色字段；
- **Agent / Player 公开投影**：`SceneState.get_view_pov(actor)`，只包含角色当前可见的普通对象状态。原始 affordance、策略标签、堆叠键、可见性 schema、私有字段和下划线字段不会进入 prompt 或玩家渲染；当前可执行能力通过另行过滤的 `affordance_opportunities` 提供。

Host 机制仍可通过 `get_object_state()` / `get_visible_objects()` 读取原始对象定义；公开投影只发生在 `get_public_object_state()` 与 `get_view_pov()`，避免为了隐藏信息破坏权威结算。

- `world_view` 来自 `SceneState.get_view_pov(actor)`；
- `world_view.visible_actor_states` 不是同场 Entity 的原始 ECS 行；默认只包含位置、姿态、表情、外观、公开状态、视线和明显站位，capabilities、skills、fear、sanity、secret、bias、dramatic motive 与导演模板不会因同场自动泄漏；
- `self_state` 是单独的本人投影，可以包含自己的能力与身体/心理状态，但仍排除幕后导演控制。内容若有“公开制服”“明显伤势”等自定义字段，可在初始内容中使用宿主只读的 `public_state_fields`；模型不能运行时把秘密字段加入公开表；
- `world_view.public_scene` 只包含公共环境描述和声明为 public 的 flags；world_version、phase schedule、其他角色 commitments、Storylet/causal ledger 与导演节奏不会混入角色环境观察。默认公开 day phase、weather、alarm、ambient condition 和 public status，内容可用 ScenarioConfig 的宿主只读 scene schema 扩展；
- `world_view.visible_objects` 只包含角色实际持有或在当前地点能够看见的有形对象；
- 对象可以通过 `container` 嵌套放置；关闭且不透明的任一外层容器都会从 POV 中遮蔽内容，关闭的透明容器只允许看见、不授予操作权限；
- Agent 可以对已知容器提出打开、关闭、放入或取出意图，但只有 Simulation 的 `set_container_state` / `relocate` 经容量、同场、访问和循环校验后才会改变世界；
- `private_drives` 只包含该角色自己的 need pressure、critical 状态和 risk tolerance；
- `private_sentiments` 只包含该角色自己对具体他人的当前感受、强度、理由和期限，不包含宿主 Policy 权重；
- `affordance_opportunities` 从当前 POV 中的对象预定义能力生成，并按需求缓解价值排序；环境还会根据 portable、owner 和容器开合状态自动加入 `engine:take/drop/open/close`。`interact` 候选可以把 exact `affordance_id` 随自然语言 proposal 带回 Host，Input 只接受当前 available 且 object target 匹配的引用；
- `communicate` 候选可以引用 `private_knowledge` 中真实存在的 `claim_id`，选择公开 stance，并引用自己已知且当场可出示的 evidence；它表达角色说了什么，不暴露 Claim truth，也不保证接收者相信；
- `interact` 可以通过 `delivery_recipient` 提议把 target 所指的自己持有整件物品交给当前可见角色；这是可被拒绝/阻塞的 proposal，不等于替接收者同意交换。部分数量、互换和条件交易仍走 Exchange/Agreement；
- 每个 affordance opportunity 会声明当前 `available` 状态以及 required/missing capabilities；看见对象不等于有能力或所有权使用它；
- `private_obligations` 只包含该角色承担的 active duty、deadline、grace、creditor 和近期履约/违约历史；
- 只包含同地点可见的本轮 proposal；
- 角色自己的状态单独提供；没有 location 的角色也不会退化成读取全局 actor table；
- 长期记忆检索只使用已经进入该角色 POV/私有状态的结构化主题：当前局面、active Goal、Obligation/Agreement、已知 Claim、可见人物与定性关系、Sentiment、计划和主观信念；不会用完整世界状态构造 query；
- 多路 query 在宿主侧批量检索并按内容去重，最多返回有限条目和字符预算；某一路检索失败不会阻断角色行动；
- episodic memory 的 `salience` 由 MemorySystem 根据角色自己的 Goal、Obligation、Agreement、Claim、Sentiment、对象变化等已提交事件计算，Agent/GM 不能自报重要度；检索结果再结合 route priority、向量距离和时间衰减由宿主重排；
- 每 12 step，宿主可把至少 24 step 前、salience 低于 3 的普通 episodic logs 确定性压缩为 routine summary；高显著记忆、近期记忆和已有摘要不参与删除，且必须先成功写入摘要才删除源记录；
- 每个 Runner 默认生成独立 `memory_namespace`，Chroma collection 名由 namespace + actor name 的 hash 构成；相同角色名、相同 seed 的两个 Session 也不会共享记忆。只有宿主为存档恢复显式传入同一个 namespace 时才复用 collection；动态出生角色继承所在 Session 的 namespace；
- 异地角色的秘密状态不会传给该 agent。
- 隐藏对象不会因为存在于权威 `SceneState` 中就泄漏给其他 agent 或 Rendering。
- 其他角色的 DriveState 不会因为同场、关系亲密或 resolver 需要上下文而泄漏给该 agent。

普通 LLM prompt 在开头明确给出当前世界 step、激活范围和自身地点，并以有界 snapshot 拼接私有状态与 Host 检索记忆。Hermes 不再收到这份同构 prompt：容器接收最小 wake/body/world packet、未处理刺激，以及 `SubjectLedgerProjector` 产生的版本化私人账本增量；计划、focus、SentimentState 和 Host 检索记忆均不进入 packet。当前 `visible_world` 中 `owner == actor` 的对象会移到独立“直接持有物”栏，不再同时混入周围环境对象；这表示角色知道自己带着什么，不代表自动发现暗格、封闭不透明容器内容或细微状态，进一步检查仍需主动 `observe`。

这条边界是悬疑、欺骗、误解和信息差能够真实成立的基础。

## 前台、后台与休眠

角色注册为 agent 不意味着每一轮都要调用每个模型。`AgentScheduler` 使用三种激活范围：

- `foreground`：玩家本人、与玩家同场的角色或被明确设为前台的角色，每轮精细决策；
- `background`：离屏角色按稳定错峰间隔低频决策，或在其所在地收到世界事件时被唤醒；
- `dormant`：休眠角色不推理，除非收到人工 override。

`dormant` 同时是 attention 边界，而不是真相边界。角色仍可通过 self/direct observation 或真实转述把 WorldEvent 写进 Cognition belief/experience，但新事件与 event response 不进入自动 pending attention 队列；环境不能借一次全局警报或阶段变化越过手动激活策略。若角色在已有 pending attention 后被切为 dormant，账本会保留供未来恢复，但 Episode closure 只统计当前可自动处理的 pending 项。

普通可运行角色的 pending attention 采用单一宿主确定性策略，而不是 LLM salience。Obligation/Agreement breach、对象销毁、公共警报和真实社会回应会排在普通移动或 day-phase 之前；角色是 Event subject 时只有有限宿主加权，直接观察和后续转述共用同一目录。每类队列最多保留四十条：主体容量保留当前高排名记录，最多四个位置保护最老的等待项；保留下来的记录每等待四个宿主模拟步获得一点有效优先级，最高仍限制为 100。这样突发事件保持优先，同时普通经历最终能获得一次处理机会。每次 perception 最多交付排序后的二十条，成功获得 AgentDecision 后只确认这批。基础 priority、aging boost、容量保留和比较 trace 都不进入角色 prompt，模型不能把自己的事件自报成紧急。

`public` 不等于“立刻运行所有 Agent”。公共 Event 仍写入所有 witness 的 Host 私人 epistemic receipt；兼容 runtime 可由 MemorySystem 按各自 POV 归档，Hermes 则在下次 wake 时通过 ledger/stimulus 增量收入原生记忆。只有 `attention_recipients` 获得 pending interrupt。默认每个公共 Event 最多选择八个普通 recipient，subject 与所在地现场者强制进入，Goal 的结构化状态依赖优先，其余用稳定散列选择；dormant 不占名额。未被立即中断的 auto/background Agent 会在自己的正常 background tick 中收到该事实。预算、目标匹配和散列不进入 Hermes packet。

manual override 只替换“谁产生行动”，不替换感知协议。InputSystem 会为人工角色调用同一个 `build_agent_perception()`，把有界 packet 保存为 `manual_perceptions`，并与自动 runtime 共用 `_acknowledge_perception_attention()`；无参数清空全部 pending 的旧路径已移除。因此人工连续操作也不能吞掉第 21 条以后尚未交付的后果。Runner/Session 还提供只读 preview，Console/Web 在提交命令前可展示 `manual_decision_context`；该摘要不含 raw beliefs/secrets、精确关系数值、目标条件锁、priority 或隐藏对象。

若 Agent 行动已经通过 WorldEvent barrier，而 Rendering/Memory 随后失败，`retry_delivery` 不会再次调用任何 Agent runtime。pending receipt 会阻止新决策进入同一个 Session，直到交付恢复或宿主显式重置 Session；因此 Hermes 不会因 UI 重试重复思考、重复付费或产生第二个 proposal。兼容 Host Memory 使用稳定 step id upsert；Hermes 不参与该归档，其下一次 subject ledger 投影依靠稳定记录引用和 revision 去重。

后台调度使用角色名生成稳定 offset，不使用 Python 随机 hash，因此同一快照重放时会得到相同的 agent 激活节奏。后台 agent 的 proposal 优先级低于玩家和前台角色，但仍进入同一个 Simulation 权威结算。

`AgentController` 以 Host 状态记录 `decision_count/last_decision_step`。记录只在 runtime 或人工控制器成功形成一次决定后更新，并随权威 step checkpoint 一起回滚；模型不能自报参与次数。默认 Episode closure 会等待所有 autonomous、非 dormant 角色至少决策一次，因此背景错峰节流不会让世界在远端角色第一次行动前被误判为已经结束。

Timeline commitment 不再包含 `stage_actors`。内容只声明 participants、地点、due/grace 和提前唤醒窗口；参与者在自己的 `private_schedule` 中获得 POV-safe 日程，非参与者不会收到。临近日程会以 `schedule_due:<id>` 唤醒离屏 auto/background Agent，并把赴约机会放入角色自己的 POV：Hermes 自主决定是否把它形成候选，兼容 LLM runtime 则仍可收到宿主 affordance 候选；两者都可以观察、等待、拒绝或处理其他目标。Timeline 最终只按真实 location 记录 present/missing participants，绝不直接搬动角色身体或姿态。

Timeline 结算的 provenance 同时保留 commitment、Host clock 与真实位置判定。出席者形成 `actor_presence:<actor>:<location>`，缺席者形成 `actor_absence:<actor>:<location>`；这些是 Host 对该时刻 SceneState 的结算事实，不是 Agent 自报。attendance WorldEvent 指向 `timeline_resolution:<commitment>:<resolved|missed>`，所以后续解释、追责或补救目标可以追溯到真正的时间与位置条件。

角色真实移动、普通可观察对象属性变化、Timeline 出席，以及已提交的物品生命周期、交换、Obligation 终态和 Agreement 状态变化，会在相应宿主系统结算后进入 `WorldEventSystem`。合法图移动的位置写入由宿主根据 LegalityEngine 结果补全，不依赖语义 GM 是否记得填写坐标。movement Event 同时覆盖出发地的离开目击者与目的地的到达目击者；移动者知道自己的行动，但不会因这条 self event 再获得一次被动注意力。普通对象变化由宿主比较事务前后快照派生，语义结果不能伪造 change ledger；人工 `world_edits` 也必须经过独立宿主事务并生成同构的对象差分，不能静默篡改状态。局部对象只通知所在地目击者，hidden 对象只改变客观真相而不自动泄漏。空间拓扑仍是宿主 world-building 权限，不能伪装成普通对象属性更新；已有节点间的开路/断路由 `HostTopologyTransaction` 在 Agent 感知前原子提交，随后以 `route_opened / route_closed` 事件投射给现场者。Agent 会立即服从新的合法移动图，但只有真实观察或后来转述后才会把变化当成已知事件。其他客观事件以独立 Event Entity 存在，但只向现场角色和事件当事人写入私有 cognition belief/passive experience；其他 Agent 的 prompt、Memory query 和 world signals 不会自动获得它。新 event id 同时进入真正观察者的 pending observation 队列：离屏 auto/background Agent 下一决策点会以 `world_event:<id>` 被唤醒一次，感知真正交付给 runtime 后才确认处理；重复转述已知事件不会反复唤醒，显式 dormant 仍保持人工边界。知情角色可以在后续 communicate 中引用 event id 转述，接收者获得固定宿主置信度的 reported event belief；模型不能用同一个 id 改写客观事实。事件 belief 中保留 event id，因此可以成为 Agent 自主形成解释、追责、道歉或调查目标的真实来源。Goal、Sentiment 和普通关系轨道变化不会被投影为 WorldEvent。

这些 Event 的 source 不再重复 object id 或 actor name 充当“原因”。移动与已经通过生命周期证据校验的拿取、放下、开关、使用和销毁指向对应 `resolved_action:step/actor`；普通对象属性差分只有在 Host ledger 精确找到以该对象为 target 的正向行动时才指向 action batch。没有精确行动来源的 Host edit、拓扑或公共环境转换保留各自的 Host transition id，不把同轮无关 Agent 猜成原因。

角色主导不仅依靠“GM 不得凭空增加 actor”这一条 prompt。SimulationSystem 在调用任何语义 resolver 前会从输入契约中物理移除 Storylet、Conflict、Drama directive、Plot snapshot、Situation、reaction pressure 与 motive pressure；社会上下文也会剥离 `bias / framing_style / territorial` 等导演字段。完整 packet 仍留在 Host context 供机会检测、Episode 评估和事后归因，但 GM 看不到它们，不能为了满足一个幕后节拍改变既有 proposal 的结算方向。

这条隔离跨越长期记忆。GameMaster 的 `Memory` 只保存已提交行动及其权威事务后果，不归档完整 Timeline、Host 随机检查、私有 Goal/Modifier 诊断、Plot pressure 或 Narrator 文本。兼容 LLM 角色的 episodic memory 从各自 `Cognition.experiences` 归档本轮亲历事件；Hermes 角色跳过 Host Chroma 检索、归档和 consolidation，只保留有界 Cognition receipt 供 POV/知识校验，再由 subject packet 增量进入 Hermes 原生 memory。同场角色可以获得共同目击，异地角色仍只能获得自己的现场，主动观察的 private result 也只投递给行动者。RenderingSystem 不把玩家文案写回任何角色 Observation。

动作完成批次中的移动使用逐角色观察窗口。SimulationSystem 在事务前保存每个角色的原位置，在提交后与新位置组成 `{origin, destination}`；CognitionSystem 只把发生在这两个端点的非 hidden 行动视为本轮可观察。未移动者仍只有单一地点，动态出生角色只有出生地点。这个窗口只解决离散提交顺序造成的观察丢失，不提供沿途全知，也不会让角色看到第三处事件。

普通秘密转述、WorldEvent 回应和 Claim report 也共享这份 Host 共址定义。传播行动的 `location` 必须同时属于发送者与接收者的本轮窗口，并且 communicate 必须真实结算为 success、partial 或 complication；失败、blocked、第三地或仅由 GM 输出的 knowledge update 都不会改变接收者知识。这样“边走边听见最后一句”可以成立，但“事务提交后已经异地，所以忘了刚才听见什么”和“失败交流仍传递秘密”都不会发生。

同样的窗口还约束外部 Sentiment、Modifier 和 Drive pressure。角色在离开现场前目击帮助、威胁或冲击，后续仍能形成感激、害怕、shaken 或需求压力变化；真正异地、hidden 或失败的来源行动不能隔空影响心理状态。Drive 在 WorldStateTransaction 的候选 Scene 已经写入移动坐标后校验，因此会显式同时读取原 Scene 与候选 Scene，而不是让事务字段顺序决定角色是否“看见”。

WorldEventSystem 使用同一窗口派生对象状态、物品操作、交换和局部拓扑事件的 `direct_witnesses`，公共事件 attention budget 也用窗口识别现场者。事件进入 Cognition 时记录事件实际发生地，不把观察者提交后的新坐标伪装成事件地点；移动事件则继续区分 departure 与 arrival witnesses。由此结构化亲历、Event belief、pending attention、长期记忆和后续 Goal 唤醒不会对“这个角色是否在场”给出互相矛盾的答案。

公共 scene flag 的真实变化与 Timeline day-phase transition 也会成为宿主 Event，并以 `scene_flags.<field>` impact 唤醒相关目标。私有 flag、phase turn、完整 schedule/commitment book 和消费 ledger 不会事件化。Rendering 只收到公开 phase transition，以及玩家本人确实错过的 commitment 结果；其他角色的 due/upcoming 日程和 transition carrier 状态不进入 Narrator。

Agent 可以为已知事件提出 `resolution_kind=communicate_event` 的普通告知目标，或使用 `respond_to_event + resolution_response` 表达 explain、apologize、accuse、request、forgive、acknowledge。`resolution_target` 都必须是当前可见接收者。GoalSystem 在 Event Entity 上编译隐藏的 communication/response 证据锁；CognitionSystem 只在真实 committed communicate、同场、发送者确知事件且目标一致时写入 `WorldEventResponses`。因此 Agent/GM 不能用普通互动、伪造 statement 或一段回顾性叙述让目标完成。response 只是客观行为类别，不会把“道歉”自动写成“对方原谅”，也不会把“指控”自动写成 Claim 真值；接收者的 Sentiment、判断与后续行动仍保持私有。首次 response 会生成稳定 attention id 并进入接收者的 `pending_event_responses`；即使接收者早已知道原 event，离屏 Agent 仍会以 `event_response:<id>` 获得一次决策。perception 成功交付后宿主确认消费，重复的同类同向回应不会无限唤醒。

权衡属于角色自己。Hermes 可以在明显只有一个合理意图时直接返回 action；需要权衡时在 runtime 内部返回 candidates，每项必须带不同 `motive_lens`、结构化 `intent_signature`、有限 utility 与 executable action。`HermesCharacterAgent` 先用角色私有随机源在 motive lens 间做 Gumbel-Max，再在选中 lens 的动作间做第二次 Gumbel-Max，然后只把一个 action 交给 Host。宿主永远看不到这个候选分布，也不再对任何 runtime 做二次抽样：`commit_runtime_action` 只是把角色已经决定的行动记成 `runtime_committed` 收据。配置 `character_seed` 时可复现评测；未配置时生成随机 subject seed，私有 ledger 只记录 fingerprint、draw、lens、option 与选择，不向世界或其他角色公开候选。

宿主既然不选择角色的行动，也就无法重建“她为什么这么做”。因此动机只能由角色自己陈述：decision 可以附带最多四个 `motive_refs`，接受 `{"kind":"goal","ref":"<goal_id>"}` 以及 `obligation` / `sentiment` / `drive_need` 引用。`InputSystem` 对照她当前实际持有的 `GoalState`、Obligation snapshot、`SentimentState` 和 Drive need 校验每一条：引用她并不持有的东西会被丢弃并进入 `agent_motive_ref_rejections` 审计，而不是被采信。这些引用只表示她认为自己为什么这么做，不能创建动机、提供概率、证明行动成功或完成 Goal/Obligation。不陈述动机是合法的，代价只是这一步在因果图里没有动机父边。

角色自己的 `Planning.current_plan`、`Cognition.current_focus` 与私有 commitments 是她维护的主观连续性，通过顶层 `next_plan/next_focus/clear_plan/clear_focus/commitments` 更新。它们不再折算成任何宿主效用——既然没有采样，也就没有需要被“连续性加权”对抗的随机性。这些后续状态必须在行动失败时仍成立，不能预支结算结果；`resolved_commitments` 只允许作为下一轮基于已可见结果的反思更新。

`AgentController` 仍分开记录 Host 语义行为家族（动作类型、有限行为特征和正式引用）与明确 target，但这个 ledger 现在只服务停滞审计：它区分“同一个方案换了措辞再来一次”和“同类里换了一个真正不同的对象”，前者累计计数，后者重置。它不影响任何行动能否执行，只让 `max_repeated_policy_action_count` 和 `repetitive_policy_choices` 能识别真正的原地打转。ledger 属于可序列化 ECS 状态，权威步骤失败时随 checkpoint 回滚。

Episode 只为实际提交、且角色自己给出了通过校验的动机的行动建立 `ResolvedAction <- motive`。评估器不从行动文案反猜动机，也不把宿主的合法性检查伪装成行动原因。

动作持续时间可能跨越多个离散 step，因此完成轮的动机自述不能代表该动作当初的理由。Action queue 会把提交时的 `motive_refs` 保存在不公开的 event metadata 中；公开 queue snapshot、ongoing action、其他 Agent perception 和 Simulation/GM intents 都看不到它。动作完成后 metadata 从语义输入中剥离，只交给 Episode 因果审计，从而避免把后来的新决策错误嫁接到旧动作上。

概率结果也不由 runtime 或语义 GM 掷骰。宿主提供固定 `trivial/easy/normal/hard/extreme/impossible` 映射的 `HostCheckResolver`，并将物理结果与观察噪声分别路由到 `world`、`observation` 随机流。修正必须有宿主可验证的 id、有限 delta 和原因；Agent 声称“我必定成功”不会成为修正。

Runtime 可以依据 `private_sentiments` 理解“为什么我刚刚对乙感到受伤或怀疑”，但不能自行写持续时间、效用权重或长期关系值。Simulation 的 `social_impacts` 必须引用 affected 亲自可观察的已提交 source 行动；SentimentSystem 在宿主副本上原子创建/积累感受，随后让固定目录中的少量效果沉淀到 affected→source Relationship Tracks。宿主会忽略模型自报的 `source_event`，以已验证 action 节点替换；Agreement 的权威履约 transition 则引用独立的 performance resolution。Track provenance 指回 actor-qualified Sentiment，因而审计层可以保留完整社会后果链。

语义结算结果在任何后端之后都会经过 `SemanticAuthorityFilter`。该边界会清空顶层及 success/failure 分支中的 `relationship_updates`、`plot_updates` 和协议 settlement/authorization 伪造字段，并把 social impact、Modifier、Drive 和 Drama 的定性标签编译为宿主固定数值；模型自报 magnitude、drive delta 或 tension delta 会被忽略并记录到 `semantic_authority_rejections`。因此这不是依赖 prompt 的软约定：脚本化 GM、Hermes 容器适配器与未来 resolver 共享同一条宿主边界。已有 Plot 的钟只能由候选世界状态触发 `CausalPlotEngine`；GM 可用 `plot_beat_proposals` 登记带条件的新剧情点，但不能直接写 clock。长期关系只能由宿主社会规则沉淀。

Simulation GM 对真正不确定的动作只能提交 `uncertain_outcomes`，每项同时声明 success/failure 两个结构化分支。`required_capability` 只引用当前 Scene 中的权威 capability 或 0..1 skill；模型不能附带 probability、roll、advantage 数值或 modifier。掷骰前，宿主对两个分支执行相同的位置权限检查：actor.location 只允许当前 move actor 留在原地或到达 LegalityEngine 已授权的位置；非 move 移动、移动其他角色或替换目的地会被剥离并写入 `semantic_authority_rejections`，因此审计不随随机选中哪边而变化。宿主完成检查后只合并一个分支，随后照常经过对象、关系、Plot、Agreement、Obligation 和 Scene 的原子事务。硬合法性已经 block/rewrite 的 actor 不再执行其不确定检查。

若 auto/background 角色自己的私有 need pressure 达到该 meter 的 critical threshold，调度器会跳过普通错峰等待并以 `critical_need:<name>` 原因唤醒一次后台决策。这个判断只读取角色自己的 DriveState，不公开给玩家或其他 agent；显式 `dormant` 角色不会被需求压力自动唤醒。

事件或关系后果可能产生需要多步执行的 Agent-grown Goal。若目标仍 active、`origin=agent` 且带宿主编译的 completion conditions，离屏 auto/background 角色会按受限 `agent_goal_wakeup_interval` 以 `agent_goal:<id>` 继续获得决策机会；默认间隔为 2，并固定限制在 1..20。尚无条件锁的开放 Agent Goal 使用独立的低频 `agent_open_goal_review_interval`，默认 12、限制在 4..80，并共享重复行动指数退避；它只让角色有机会根据新 POV refine 或 abandon，不替目标制造进展。InputSystem 只在 runtime 成功收到 perception 后记录 `last_goal_wakeup_step/id`，调用失败不会消耗机会。初始作者目标仍不提高推理频率，dormant 仍不自动运行。两个间隔都是 WorldStateTransaction 的 engine-managed flag，语义 GM 不能临时改写。

若一个已退避目标收到 POV-safe 的相关 WorldEvent 或 event response，宿主会重置其重复 action signature，并从当前 step 重新按基础间隔调度。相关性优先比较 Event Entity 上宿主派生的 `scope/target/path` impacts 与目标的隐藏状态依赖，并保留 source ref、condition target/value 引用相交作为兼容边界；它不是由 Hermes/GM 判断的自由语义标签。容器开闭会向嵌套对象投影 accessibility/visibility impact，所以角色亲眼看见箱子打开后可以重新尝试取得里面的物品。变化必须已经成为该角色的 direct/self observation 或经 CognitionSystem 验证的转述，异地未知的同一变化不会心灵感应式解除退避。`private_goals.continuation.reactivation_count` 只告诉角色确有多少次相关环境变化，不公开匹配到哪条精确锁。

当回应本身改变角色打算时，Agent 可用 `source_kind=event_response` 和 recent experience 中的真实 response id 形成新 Goal。GoalSystem 从该角色自己的 Cognition 验证来源；确认 attention 不会删除这份经历，未收到回应的旁观角色也不能猜中 id 后借用。Episode 因而可保留 `Goal <- event_response <- WorldEvent`，而不是把一切社会后果都压回原事件。

角色义务进入 `wake_before_steps` 窗口后，也会以 `obligation_due:<id>` 原因唤醒 auto/background Agent。Agent 可以选择履行、协商、拒绝、逃避或承担违约风险，但不能在私有回复里直接把义务标成完成；只有 Simulation 的证据化 `obligation_updates` 或内容包声明的权威完成条件可以改变 ObligationState。

如果多个义务的结构化完成地点和期限形成路线冲突，角色自己的 perception 会额外收到 `private_obligations.conflicts`。`hard` 表示按当前空间图和剩余步数，角色独自行动没有同时完成全部责任的路线；`constrained` 表示只有列出的特定先后顺序仍可行。这只是私有决策证据，不会公开给其他角色，也不会强制 Agent 服从建议。进入宿主配置的冲突 horizon 后，离屏 auto/background Agent 会以 `obligation_conflict:<ids>` 原因提前醒来处理取舍。

外部系统可以注入带地点的结构化事件，以唤醒远离玩家的角色：

```python
session.run_step(
    inject_events=[{
        "event_id": "north_gate_alarm",
        "intent": "北门外响起三次急促钟声。",
        "location": "北门",
        "tags": ["alarm", "gate"],
        "visibility": "local",
    }],
)
```

剧本可声明：

```python
CharacterConfig(
    name="守门人",
    role="城门守卫",
    personality="尽职但胆小",
    goals=["守住城门", "活到天亮"],
    initial_traits=[
        TraitConfig(
            trait_id="brave_but_cautious",
            intensity=0.6,
            description="愿意为守住城门冒险，但不会主动送命",
        )
    ],
    activation_policy="background",
    background_interval=3,
)
```

## 主观认知不是世界状态

角色的 `Cognition` 与 `SceneState` 分离：

```text
SceneState
  门确实是开着的                    # 权威事实

Cognition(甲)
  “我认为门锁着” confidence=0.7     # 可能错误的信念

Cognition(乙)
  “我知道门后有暗道”                # 私有秘密
```

Agent 可以在整轮层面更新自己的：

- `belief_updates`
- `goal_requests`

每个候选还可以声明仅在自己被 Host 选中后才生效的：

- `next_plan` / `clear_plan`
- `next_focus` / `clear_focus`
- `commitments`

`resolved_commitments` 只在整轮层面使用，并且只能依据本轮开始时角色已经看到的结算结果；候选被 Host 选中本身不证明行动成功。

这些更新只进入角色自己的 `Cognition` 或 `Planning`，不能写入 `SceneState`。一个推测只有经过角色行动、Simulation 结算和可观察证据，才可能成为公共事实。

Simulation 完成后，`CognitionSystem` 会把结构化结果归档为角色经验：自己的结果始终可知；其他角色的结果只有在同地点且不为 `hidden` 时可知。该阶段早于文本 Rendering，因此角色学习依赖的是结算事实，不是可能带修辞的叙述文本。

角色间的秘密传播使用显式 `knowledge_updates`。发送者必须此前确实知道该陈述、与接收者同地点，并且本轮存在发送者的已结算交流行动；满足条件后，陈述只进入指定接收者的私有 belief，并记录来源与置信度。普通对白不会让所有角色自动获得知识。

默认 LLM runtime 会解析可选的 `plan / focus / belief_updates / commitments` 字段。角色配置中的 `system_instruction_extras` 进入 Hermes 首轮 `identity_bootstrap.persona_constraints`（以及显式 LLM runtime 的角色 prompt），不再被误传给模型 provider 作为未知网络参数。

动态角色首先必须持有宿主签发的 `character_entry` capability。授权只能来自本轮 `inject_events` 或到期的 Timeline commitment，固定 authorization id、name、role、location、initial_state、runtime 侧配置和私有初始事实；授权不随 prompt 跨步持久化。`profile_mode=fixed` 时 GM 不能改人物表征，`semantic` 时也只能补充 personality 与自然语言 goals，不能制造携带物、能力、秘密或地点。无授权、过期或已消费的 spawn 请求会被忽略并记入 `character_entry_rejections`，不会牺牲同轮其他合法行动。

初始角色遵守同一个存在性边界。Session bootstrap 要求 `ScenarioConfig.characters` 与 `initial_actor_states` 一一对应，每个角色身体还必须位于内容包已经声明的 location；不能用 actor state 创建无 Agent 的群众，也不能声明一个没有世界身体的“幽灵 Agent”。`AgentRegistry.register()` 自身拒绝没有 `AgentController` 的 Entity；Input 也不再接受“只有 registry 绑定”的兼容角色。只要 Runner 中存在 `SceneState`，每个正式 step 前都会审计 `Scene actor ↔ ECS Entity + AgentController ↔ live AgentRegistry runtime`，不依赖是否由标准 Scenario loader 创建。任一绑定丢失时整步在时间和宿主修改之前 fail closed，而不是让 GM、旧工作流或另一个 runtime 临时代演。

授权通过后仍必须经过 `CharacterLifecycle`。它先准备一个尚未发布的 Entity，把角色身体和一次性授权消费记录放进候选世界并参与同轮因果校验，世界事务成功后才创建和确认 live runtime 注册。若 runtime factory 或注册过程失败，注册表、ECS Entity、授权 ledger 以及 Scene/Plot/Drama/Relationship 都会通过 checkpoint 回滚，Rendering 只会看到一次被拒绝的结算。模型不能通过 spawn 请求选择任意 runtime 或宿主工具权限，且默认最多生成六个动态人物。

CharacterLifecycle 不再提供可被旧调用者直接使用的 `spawn()` compatibility wrapper。唯一发布路径是 `prepare` 生成未发布计划、`WorldStateTransaction` 在候选世界中 `stage`、提交后 `finalize`；finalize 要求宿主提供真实注册回调和 live AgentRegistry，并在把 Entity 暴露给 session 前验证 runtime 已登记。旧 `NarrativeSystem`、`NarrativeControl` 和 `AgentActionSystem` 已从代码与系统导出中删除，不能再通过一段 GM narration 绕过 Agent proposal 或权威事务创建角色。

InputSystem 没有 Persona fallback。自动角色只有在 AgentRegistry 中存在 live runtime 时才能形成 proposal；缺失 runtime 会在 activation trace 中标记 `missing_agent_runtime` 并跳过该角色，而不是调用另一套 prompt。ConflictDirector 和 StoryletEngine 同样没有代演权限：它们只生成 advisory pressure/opportunity packet。本轮无人选择兑现时不会留下 unrealized 欠账，更不能从内容模板直接构造某个 NPC 的 resolved action。Storylet 是否发生由宿主在行动后识别，GM 不能自报 hit。

Agent proposal 的结算结果还必须通过 `WorldStateTransaction`。模型不能通过普通 `state_updates` 隐式创造新角色、对象、未知地点或未知 plot；有形对象必须经过 `WorldObjectLifecycle`，且创建、拾取、转交、隐藏和销毁都需要已结算行动与同场证据。事务失败时，agent 的行动可以被记录为一次无效结算，但不会产生玩家可见的虚假世界事实。

事务还逐条核对 `resolved_actions.actor` 与本轮 proposal actor 集合。除 engine-injected `World` 事件外，没有 proposal 的角色不能出现在结算 action 中；因此即使 Simulation 模型从导演模板里擅自挑选了某个 NPC，结果也会在权威提交边界被拒绝。这条检查独立于 prompt，适用于默认 LLM、Hermes 或未来任何 resolver。

对象 `use` 也属于生命周期操作。Agent 只能引用内容包已经声明的 affordance id；对象的 quantity、consumes 与 need effects 由权威内容定义，不能由 Agent 或 Simulation 临时创造。资源消耗与角色私有需求变化一起进入世界事务，因此不会出现“食物已经消失但饥饿没有缓解”或相反的半状态。

同轮多个 Agent 对有限或独占对象提出成功操作时，`ResourceContestResolver` 会在事务前以稳定规则分配 quantity 或单一使用权。角色不会提前看到其他 NPC 尚未结算的 proposal，也不能在 decision 中宣布自己赢得竞争；输家只会在结算后的个人经验中得知尝试被阻止或部分完成。完整竞争 trace 属于 GM 诊断记忆，不会通过 Rendering 或下一轮 perception 泄漏隐藏、异地参与者。

角色也可以在 action proposal 中提出或接受交换，但不能替另一 Agent 宣布同意。Simulation 只有在双方都真实出现在本轮 proposal batch、同场并各自产生可观察正向行动时，才能输出 `exchanges`。交换对象必须由 from 真实拥有并向对方公开；隐藏物品要先通过权威行动揭示，部分货币/资源数量必须来自内容预定义的 `stack_key`。Exchange 与 obligation delegation 可以组成一个原子契约：例如乙明确接受送货责任，同时甲交付报酬；其中任何同意、库存、隐私或义务条件失败，报酬和责任都不会产生半提交。

交换完成后的 WorldEvent 将 exchange id 用作稳定事件身份，但不把它当作因果解释。普通交换由 Host 记录为双方同轮 verified resolved-action batch 的后果；Agreement settlement 物化的 transfer 则直接指向相应 `agreement_resolution:<id>:settled`。因此对象 owner 改变可以追溯到真实同意路径，而不是停在一次性中间结构上。

不要求双方恰好在同一轮完成谈判。含糊协商仍留在 Communication、Cognition 与 Memory；明确且需要跨时执行的承诺才通过 `agreement_updates.propose` 创建 Agreement Entity，条款只进入 parties 的 `private_agreements`。另一方可在后续 Agent turn 中独立 accept/reject，proposer 也可 withdraw；counter 必须提供 `new_agreement_id` 和完整替换条款。所有接受齐备时，宿主从权威 Agreement Entity 生成 engine-owned authorization，再检查当前资产与义务并结算。旧承诺不会冻结物品；临近 expires step 的报价可以以 `agreement_due:<id>` 唤醒离屏 auto/background Agent，dormant 仍保持人工边界。

正式 Agreement 的创建来源由 Host 固定为已验证的提议行动 batch，即 `resolved_action:step:<n>:actor:<proposer>`。该来源随 Agreement Entity 在 SocialRelation provenance 中持久化，Agent/GM 提供的 `source_kind/source_ref` 不会被采用。Episode 因而可以审计“角色真实提出报价 → Agreement → 服务 Obligation → 履约/违约事件”，而不是把协议视为无来源的状态突变。

Agreement 进入终态时另写 resolution provenance。最后一方接受、拒绝、撤回或 counter 都引用对应角色本轮已验证的 communicate action；自动过期引用 Host clock step。服务 Obligation 仍声明自己来自 Agreement，同时 Episode 会根据同轮显式 settled transition 增加 `Obligation <- AgreementResolution` 边。这样提议行动和接受行动都保留在因果图里，模型的自然语言 reason 只作说明，不承担因果身份。

Contract 的 `services` 支持“先付款、后办事”：settlement 当轮完成 upfront exchange，并为 provider 创建新的权威 Obligation；due_after_steps 从成交时开始而不是从报价时开始。之后 Contract performance 只跟随该 Obligation 的真实状态，包含合法 delegation 链。参与者 perception 中的 `counterparty_performance` 是自己亲历契约的局部履约记录，`own_performance` 是自己的表现；它不是全局信誉分，也不会自动修改 trust/malice。角色可依据这些事实形成 belief、拒绝再次交易、要求预付款或提出补偿，但最终态度和行动仍属于各自 Agent。

performance 的 fulfilled/breached/cancelled 不是 Agreement 自己凭空改变。Episode 从 Agreement 上的 `performance_obligations` 读取 Host 已跟随 delegation 后得到的 `current_actor/resolved_status`，建立 `AgreementPerformanceResolution <- Obligation`，并让随后投影的 performance WorldEvent 指向该 resolution。这样违约通知、关系反应和补偿目标可以追溯到真正未履行的责任主体。

角色也可以在同一报价中提出 `escrows`，把报酬从普通 upfront transfer 改为条件托管。每个 escrow 只能引用同一 agreement 中一个 service obligation id，并预先声明 fulfilled 时的 `release_to` 以及 breached/cancelled 时允许的 `refund_to`。最后一方接受后，资产从 payer 的可见库存原子移入 engine custody；它没有虚构角色 owner，也不再作为 Scene 中可拿取或双花的对象。AgreementSystem 在 ObligationSystem 之后读取权威 service 状态，并用专门的 Scene+Agreement 原子事务释放或退款。完整 custody lot 只存在 parties 的 `private_agreements` 与 GM 诊断记录中，Rendering 和旁观者不会收到内部托管字段。

托管的因果身份使用稳定 custody id，而不是只使用 agreement id。入托节点来自 Agreement settlement；释放或退款节点同时引用 held custody lot 与触发它的 performance resolution，随后 escrow WorldEvent 以 `agreement_escrow_resolution:<agreement>:<custody>:<status>` 为来源。这样“哪笔资产被退回”与“因为哪项服务违约而退回”都能重放审计。

`AgentPerception.private_goals` 提供角色自己的 GoalState：active 目标仍参与行动策略，achieved/failed 目标只作为私有历史。普通自然语言目标无需条件即可驱动候选行为，但不能由 Agent 自评完成；可选 `goal_specs` 的完成/失败条件只在宿主 `GoalSystem` 中对权威 Scene/Plot 状态求值。Agent 只知道某个目标是否存在证据规则，不会收到精确条件表达式。

Agent-grown goal 可以通过 `goal_requests refine` 把先前开放的追求具体化。只允许 `origin=agent`、仍 active 且尚无任何结算条件的目标；Agent 提交真实 goal id 和当前 POV 支持的 resolution 模板，Host 重新验证地点、对象、人物、能力或关系并编译隐藏条件。已有条件锁、作者目标和终态目标都不能重写，因此 refine 是“后来找到可执行办法”，不是借模型修改胜利条件。角色也可以主动 `abandon` 自己创建且仍 active 的目标，并提供自然语言理由；Host 保留 abandoned 历史和 resolution step。这样目标既能从事件开放生长，又能在新机会出现后转成可完成链路，或在角色判断代价、风险与意义改变时自然收束。

Authored object affordance 可以携带有限目录中的 `policy_tags`，例如 `aid/risk/information/cooperate`。WorldStateTransaction 拒绝未知、重复、过量或非字符串标签；Input 在构造 `AgentPerception` 前把标签剥离，只把 object、affordance id、label、能力要求和 need opportunity 交给 runtime。Agent 选择 exact `affordance_id` 后，Host 才用对应标签为停滞审计判定行为家族。这样“换了说法的同一个方案”依赖结构化能力引用而不是模型是否恰好写出“帮助”二字，同时 Agent 不能自报标签影响审计。`engine:take/drop/open` 分别具有保守的 `acquire/release/access` Host 特征。Capability 本身不制造 motivation。

普通社会语言同样由 Host 映射到独立的有限特征。除 `social/confront/deception/information` 外，社会回应目录与 WorldEvent response 共用 `explain/apologize/accuse/request/forgive/acknowledge` 六类。它们只对 `communicate` 推导，不属于 authored object affordance 的 `policy_tags` 白名单；内容对象不能给普通 interact 偷挂“道歉”或“请求”动机。它们让同一对象上的“道歉”和“请求”保持为实质不同候选，并允许 Trait、Sentiment 与 Relationship Track 通过确定性函数影响概率；runtime 仍只提交自然语言 communicate action，不能提交这些标签或权重。概率层与 Cognition/WorldEvent 结算调用同一个 Host 分类器：行动文本明确时覆盖 GM 的冲突建议，文本无法确定时才接受有限目录内的 GM 建议，无效建议退化为普通 `report`。因此“Host 按道歉计算概率、GM 却把后果记成指控”的语义分叉不能发生。

`AgentPerception.private_modifiers` 提供角色当前的临时非社交影响。Agent 能看到 kind、描述、强度、层数和到期 step，但持续时间和叠加规则不由运行时决定。它是一项角色可见的临时处境，不是宿主替角色打的分。隐藏行动造成的 Modifier 会隐藏 source 与具体归因，避免借私有状态绕过 POMDP 观察边界。

Modifier 另有不进入 AgentPerception 的宿主 provenance。可见来源的 `source_event` 和隐藏来源的内部 provenance 都由已验证 `resolved_action` 生成，模型填写的任意事件 id 会被覆盖。Episode 因而可以审计 `ResolvedAction -> Modifier -> later ResolvedAction`，同时目标角色仍然可能不知道是谁造成了该状态。

`AgentPerception.private_knowledge` 只包含该角色已经获得的 Claim stance、confidence、basis、来源和 evidence refs，以及宿主派生的 potential leverage。它不包含 Claim truth status 或真值条件。Hermes 可以据此调查、质疑、引用证据、隐瞒或撒谎；发现与传播是否有效由 ClaimKnowledgeSystem 根据 observe/communicate 结果、空间、Evidence links 和来源知识核验。

Claim knowledge 的 Episode provenance 只从这些已提交字段构造。主动观察形成 actor-qualified `EvidenceObservation`，同时指向观察行动和 Evidence object；真实转述形成 `ClaimReport`，指向说话者的 communicate action。角色从 Claim 形成新 Goal 时，source 节点是自己的 `ClaimKnowledge`，不是 Claim Entity 的全局 truth，也不是其他角色已经知道该命题这一事实。

对于威胁解除、羞辱加剧、秩序恢复等非物质后果，Simulation 可以提出 `drive_updates`，但必须指明受影响角色、产生事实的 source actor、该角色已经存在的 need、`increase/decrease` 方向、定性 intensity 与原因，并由本轮 source 的 resolved action 支持。宿主固定把 `minor/moderate/major/extreme` 编译为 `0.05/0.12/0.25/0.4`，Simulation 不能填写 delta。若 source 不是受影响者本人，该行动必须在受影响者所在地且不为 hidden，避免异地密谋通过“心理感应”改变角色压力。更新通过事务后只改变目标角色自己的 DriveState，不直接暴露给 Rendering 或其他 Agent。

DriveState 为每个 need 保留最多 24 条宿主 provenance：对象使用和显式更新指向行动，义务压力指向 Obligation，自然漂移指向 clock。该 ledger 不进入角色的 `private_drives`；它让 Episode 可以把 `cause -> DriveNeed -> selected action` 接成真实链，并随事务 checkpoint 一起回滚。

动态承诺、任务指派、履约、解除和责任转交使用 `obligation_updates`，遵循相同的行动证据与可观察性原则。动态 create 的完成条件被限制为 debtor 自己的地点，或 debtor 当前可见对象的 location/owner/hidden 等安全权威字段，不能借任务描述泄漏秘密 Plot 或隐藏对象。模型不能输出 breach；`ObligationSystem` 只根据世界 step、grace window 和权威 completion conditions 判定到期、违约或自动完成。这样一个角色“说自己做完了”与世界中“任务确实完成了”保持严格区分。

Obligation 的因果来源同样不由运行时填写。内容初始义务由 Host 标记为 `scenario`；Agreement service 结算创建的义务指向 agreement id；普通动态创建指向已经通过行动证据校验的 step/actor batch；delegation 指向原 actor-qualified Obligation。即使 Agent/GM 在 update 中附带自定义 `source_kind/source_ref`，事务也会丢弃它们并写入宿主派生值。

Delegation 是多方 Agent 协议。义务的 `delegation_policy` 可以是 `forbidden`、`bilateral` 或 `creditor_consent`：bilateral 要求旧 debtor 和新 delegate 同地点、双方本轮都实际提交 proposal、双方都产生非 hidden 的正向 resolved action，并由结构化 update 明确 `accepted_by == delegate`；creditor_consent 在 creditor 是第三方时进一步要求其同场 proposal/action 与 `approved_by == creditor`。完成条件引用的对象必须对 delegate 可见，Plot、scene、其他角色或隐藏对象条件不会借委托泄漏。成功后旧记录保留为 `delegated` 历史，新记录保存 `delegated_from`、原期限、policy 和完成条件；原 debtor 的位置条件会改写为新承担者。缺少任一必要 proposal、接受/批准行动、同场事实或目标 ObligationState 时，整个世界事务回滚。

## 内容与引擎的边界

引擎代码中不得出现具体剧本的：

- 人名、地点名和固定对白；
- 类型名驱动的角色专属行为分支；
- 为某个场景强制改写 agent 行动的脚本；
- 只能在某个题材下成立的冲突补丁。

这些内容应进入：

- 角色身份、目标和初始私有状态；
- 世界对象与关系状态；
- 通用 schema 表达的 storylets / situations / plots；
- 可复用、题材无关的规则或策略插件。

如果一个效果只能靠在 `InputSystem` 或 `SimulationSystem` 中写角色姓名才能实现，说明抽象仍然失败。
## Agreement actions

`AgentPerception.agreement_opportunities` contains POV-safe summaries of offer
templates the current actor may propose. A runtime uses the existing
`communicate` action with one of these structured references:

- `agreement_operation=propose` and an exact `agreement_template_id`
- `agreement_operation=accept|reject|withdraw` and an exact pending `agreement_id`

The runtime cannot provide executable terms, expiry, completion conditions or
escrow rules. Invalid, stale or unauthorized references are stripped at Input;
the Host reconstructs the update after positive semantic resolution.

An `asset_offer` opportunity is generated without authored story content. Its
`give_options` and `request_options` are the complete allowed reference sets for
that POV and step. A runtime proposes with `agreement_give_refs` and/or
`agreement_request_refs`; it does not invent quantities, ownership, IDs or
transfers. Capability still does not imply motivation: an offer being legal says nothing
about whether this character wants it.

A `delivery_service_offer` lets the runtime request delivery of one object in
`service_object_options` from the visible provider. It selects a qualitative
deadline and may select one `payment_options` reference for escrow. The Host,
not the runtime, maps the deadline, creates the Obligation completion condition,
and fixes payment release/refund behavior.

If `destination_options` is non-empty, the runtime may set
`agreement_service_destination`. The options are limited to public, immediately
connected known locations. The resulting service is fulfilled only after the
provider physically moves and drops the object there.

`private_knowledge.map` is the runtime's map, not a projection of the complete
Host topology. It contains `known_locations` and `known_routes`; remote movement
and delivery destinations are bounded by this graph. Visiting a place
additively teaches its public exits, but does not silently erase an older
remembered or reported edge merely because it is absent from the current Host
graph. A live attempt or explicit topology observation must expose that
discrepancy. Content may seed prior familiarity through
`initial_known_locations`, but unknown or secret paths remain absent.

A runtime can report one known directed edge with `route_source` and
`route_target` on a `communicate` action. The receiver stores provenance instead
of receiving an authoritative topology update. A report can therefore be
honest yet stale; live movement remains checked against current Host topology.

For a complete direction, use `route_path` with two to eight distinct nodes.
Every consecutive directed edge must already exist in the speaker's
`known_routes`. The legacy single-edge `route_source/route_target` form remains
accepted and is normalized to a two-node path.

When a remembered route is stale or movement is blocked, the Host records a
private `NavigationProblem` containing the failed edge, destination, discovery
place and step, a possible alternative from this runtime's known map, and any
matching delivery Obligation with time remaining. This wakes a background
runtime but is not an automatic Goal or chosen response. The runtime may
reroute, observe, ask, report delay, negotiate, wait, or accept breach. The Host
never reveals secret topology through this record, and leaving the discovery
location resolves the immediate local problem.

A `NavigationProblem` is evidence, not a motive the Host can score. The runtime
sees the failed edge and its options and decides for itself what to do; the Host
does not reward a reroute over an inquiry. Stated motives are limited to what a
character demonstrably holds (`goal`, `obligation`, `sentiment`, `drive_need`),
so a reroute is explained by the goal or delivery obligation it serves rather
than by citing the obstacle.

Non-navigation failures work the same way without creating a permanent world
entity: a committed `fail` or `blocked` action is retained as a bounded private
recent experience with its Host action event id, so the runtime can see what it
already tried. Whether that changes its method is its own call.

An Agent may explicitly adopt a new Goal with
`source_kind=navigation_problem` and the active `problem_id`. In particular,
`reach_location` accepts any destination in that actor's private known map, not
only a currently visible or adjacent place. The Host compiles only the final
authoritative location condition; ordinary movement still advances one live
edge at a time and may encounter another stale edge. The problem therefore
grounds a chosen long-term intention without becoming a hidden movement order.
