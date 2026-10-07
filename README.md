# Story Engine

一个让 AI 角色在权威世界状态里自主生活的叙事引擎。

Story Engine 不是"会聊天的 GM"。世界状态、角色意图和呈现给玩家的文字是三个严格分离的层：世界只由结构化状态决定，角色只能提出意图，语言模型永远不能直接宣布世界发生了什么。这让故事有真正的因果——角色的秘密、误解和信息差是真实存在的，不是文本层面的表演。

作者只需要提供一个尽可能小的故事种子——人物、欲望、关系、地点、少量潜在矛盾——其余的故事由角色 agent 与世界状态的持续互动自然生长出来。

## 特点

- **世界状态与叙事解耦**：所有事实都记录在结构化的世界状态里，渲染层只负责把已经发生的事实讲成故事，不能反向创造事实。
- **每个角色是独立的 Agent**：角色只提出行动意图，具体能否成功、造成什么后果，由世界规则和结算层裁定。角色拥有私有的记忆、信念和目标，看不到别人的秘密，也看不到全局真相。
- **离散事件时间**：动作按真实耗时推进，不是"你一句我一句"的固定轮次；同屏角色可以并发行动，离屏角色也会持续生活。
- **可插拔的角色大脑**：默认使用 Hermes 作为长程存活的角色代理，也可以切换成纯规则驱动的离线角色用于快速验证。
- **条件世界事件（Storylet）**：条件成立后向结算器提交 World 意图，事件提交成功后向在场角色发送可感知事实，角色自主回应。
- **可回放、可评测**：内置多种子扫描与收敛评测工具，用于校验涌现叙事的稳定性。

## 快速开始

无需 API key、无需 Docker，几秒钟看到引擎跑起来：

```bash
pip install -r requirements.txt
python web_main.py --scenario cthulhu-arkham --profile offline --port 8000
```

打开 `http://127.0.0.1:8000` 即可在浏览器里游玩。这是离线模式：世界规则真实生效，但角色决策和叙述文本都是规则/模板驱动，用来体验引擎结构。

也可以用终端版本：

```bash
python main.py --scenario thirteenth-floor --profile offline
```

内置场景：`thirteenth-floor`（悬疑）、`cthulhu-arkham`（克苏鲁调查）、`false-heiress`（宅斗）。

## 完整体验：接入语言模型

要让角色真正"思考"、叙述真正是文学性的语言，需要接入语言模型。运行中有三类模型职责：

1. **世界结算 / 叙述器**——负责结算世界规则和渲染文字。
2. **角色代理（Hermes）**——每个 NPC 独立的大脑，跑在单独的本地进程里。
3. **导演与故事块追踪 Agent**——StoryPlanner 积累玩家输入与面向玩家生成的世界消息，周期性提出新故事块；每个故事块由独立持续会话追踪进展、提出下一步并决定完成或放弃。默认复用世界结算模型配置，每 5 个已生成玩家世界消息的回合询问一次；ScenarioConfig 可设置 `story_planner_interval_turns` 与 `story_planner_interval_seconds`。

运行中，角色的言论以带来源的消息保留。只有本轮外部结果依赖某项未知事实时，结算模型才补全必要的人物、物品、地点或客观结论，并经过语义校对与同一世界事务提交；新人物注册为独立 Agent。动态补充和已确认的否定事实随完整存档恢复。导演提出的 Storylet 描述未来的条件情境、事件及世界影响。

导演草案经结构检查后原子登记为动态 Storylet。`trigger` 支持自然语言条件，由世界结算模型在同一调用里判断并解释剧情方向；具体变化经过提交前语义校对。需要角色自主选择的部分通过带导演来源的建议投递，角色自行回应。Timeline 约定机制与 Drama 节奏辅助层已退出主路径，世界时钟与动作调度继续生效。

生产模式为每个静态或动态故事块创建独立追踪会话，复用世界模型配置与现有系统链。共同玩家叙事只保存一份，每个追踪者保存自己的会话、阅读位置、进展和待结算的一步。初始方向按条件进入世界结算；随后每次玩家文本成功生成后，各追踪者读取新增叙事及自己的结算回执，下一步进入下一轮世界结算。首次事件或导演建议成功提交后，故事可以跨多轮和原始启动条件的变化持续展开；完成或放弃后退出。会话、进度及待结算步骤随完整存档恢复。

### 1. 配置 GM 与叙述器

复制 `.env.example` 为 `.env`，填入 key：

```bash
cp .env.example .env
```

```bash
OPENAI_API_KEY=sk-xxxx
```

默认接入 DeepSeek，可在 `src/config/config.yaml` 里改成任何 OpenAI 兼容接口。

### 2. 启动角色代理

生产默认是本地进程。每个角色一个 `--subject-server` 子进程，会话和原生记忆落在 `.story-hermes/subjects/<id>/`，Host 回滚 step 时会一并恢复。

```bash
python -m venv .hermes-venv
.hermes-venv/bin/pip install -e docker/hermes-story/hermes-agent

python main.py --scenario thirteenth-floor \
  --hermes-python .hermes-venv/bin/python
```

Docker 传输已废弃（`docker run --rm`，会话无法恢复），仅供对照：

```bash
docker build -t hermes-story:latest docker/hermes-story
python main.py --scenario thirteenth-floor --hermes-transport docker
```

### 3. 配置角色代理的 key

同样在 `.env` 里：

```bash
IKUN_API_KEY=sk-xxxx
```

配好之后，直接运行 `python main.py --scenario false-heiress` 或 `python web_main.py --scenario false-heiress --port 8000`，就是完整体验。

也可以从一段自然语言设定开始。在生产模式下，GM 模型先提取原文能支持的人物、地点、物品、客观事实和关系；宿主逐项核对引文，模型再按原文语义校对事实、群体人数与创作许可，随后用同一套 ScenarioConfig 建立世界：

```bash
python main.py --seed "三个陌生人被困在一座雪山小屋里，每个人都有秘密。"
```

设定只说“每个人都有秘密”时，GM 必须为每位角色创作一条私有秘密，作为带 `generated_seed` 标记的客观 Claim 入库；角色本人和玩家视角能看到自己的线索，其他角色仍需通过行动获知。原文未确认角色当前位置时，编译会报错，避免把人物随意放进第一个场景。整段作者设定保留给 GM；玩家开场和叙述器只读取可见的初始地点与后续提交事实。未明确写出的角色目标留给 Hermes 主体形成。生产模式需要前述 GM 和 Hermes key；提取或校验失败会停止启动，并报告错误。

完整 JSON/YAML ScenarioConfig、小型 mapping（`角色`/`地点`/`物品`/`规则`/`目标` 等字段），以及同时声明人物和地点的行导向文本走确定性编译，无须额外的模型提取：

```bash
python main.py --profile offline --seed "地点：雪山小屋->山道
角色：甲|医生|谨慎|查明真相|地点=雪山小屋
角色：乙|向导|急躁|带大家下山|地点=雪山小屋
玩家：甲
初始状态：暴雪封山，幸存者被困在小屋里，每个人都有秘密。"
```

行导向词汇：`地点`（`A->B` 声明通路）、`角色`（管道分隔的 `名字|身份|性格|目标|地点=…`，每行一人）、`物品`、`规则`、`目标`、`玩家`、`初始状态`。离线模式始终使用确定性编译；纯散文在离线模式只作为开场前提。

`--seed-file` 可从 UTF-8 文件读取 seed；`--scenario-ref module.path:attribute` 可指向外部 Python 模块里的 `ScenarioConfig` 对象或工厂。

生产结算由现有 LLM 同时承担语义后果生成和提交校对：宿主暂存候选世界后，模型检查结果文字与实际状态、角色自主权是否一致。校对拒绝时使用原意图修正一次，继续失败则整步回滚。每个新行动先接受一次语义解释；本轮结算为一次结算与一次提交校对，角色 Agent 的决定只采集一次；瞬时事件与被动物理后果仍由模型依据世界事实判断。导演提案通过结构登记进入未来剧情池；实际执行复用世界结算调用。

生产结算可提交外力位移、通路开闭，以及带容器或用途的动态物品，全部在同一候选世界中校对和原子提交。交流由模型判断实际送达者与可见性；已送达消息进入角色经验和注意力队列，角色自行判断是否相信。玩家叙述依据已提交事实和本人可知信息生成，语义校对允许忠实转述。

## 整局存档与恢复

控制台和 Web 共用完整会话存档接口：

```bash
python main.py --seed-file setting.txt --save-path saves/game.storysave
python main.py --load-save saves/game.storysave --save-path saves/game.storysave
python web_main.py --load-save saves/game.storysave --save-path saves/game.storysave
```

`--save-path` 在每轮结束与正常退出时写入一个版本化 ZIP 存档，通过临时文件和原子替换保留上一个完整版本。存档包含 ECS 实体及稳定 ID、动态注册结果、世界时间、行动队列、随机种子、关系与事实绑定、宿主记忆、导演对话，以及尚待补发的交付任务；Web 另保存当前展示的历史。恢复时重建完整生产系统链，继续执行队列或重试交付。

本地 Hermes 已唤醒的角色同时保存原生 `state.db`、`conversation.json` 与 `memories/`，以及宿主投影游标；恢复后使用原角色 ID 接续会话。Docker 缺少原生快照能力，保存已运行会话时会明确报错。模型适配器和 API key 取自当前部署，存档中的已知凭据字段会被剔除。部署路径与 Hermes 启动参数仍按上文配置；恢复离线存档时使用 `--profile offline`。

Python 入口为 `session.save(path)` 与 `load_session(path, agent_runtime_factories=...)`。整局存档和用于同进程事务回滚的 `RunnerStepCheckpoint` 各自承担自己的恢复范围；整局存档当前只接受内置组件与完整系统链，自定义有状态 runtime 需要提供主体快照与恢复接口。

Storylet 可填写 `location` 绑定启动地点；省略时由语义结算模型根据事件与地图给出地点。成功事件缺少地点、地点不存在或首次启动违背指定地点时，本轮结算失败并保留触发资格。已启动故事块由独立追踪 Agent 持续推进，后续事件可以更换地点，完成或放弃时退出。纯散文初始化的物品提取同时支持 `portable` 与有原文依据的物理属性（如 `locked`、`open`、`broken`）。

## 项目结构

```text
main.py / web_main.py     # 终端 / 网页入口
src/
├── story_engine/          # 引擎核心：状态、系统、Agent 运行时边界
├── story_engine_content/  # 内置示例故事（引擎从不反向依赖具体内容）
└── config/                # LLM 与系统配置
docker/hermes-story/        # Hermes 角色代理运行环境
docs/                       # 详细设计文档
```

## 深入了解

- [`docs/DESIGN.md`](docs/DESIGN.md) — 引擎整体设计与各组件职责
- [`docs/AGENT_RUNTIME.md`](docs/AGENT_RUNTIME.md) — 角色 Agent 运行时边界与 Hermes 接入
- [`docs/FORMAL_MODEL.md`](docs/FORMAL_MODEL.md) — 部分可观察多角色博弈的形式化模型
- [`docs/EPISODE_EVALUATION.md`](docs/EPISODE_EVALUATION.md) — 多轮涌现叙事的评测方法
- [`docs/CONTENT_PACKAGES.md`](docs/CONTENT_PACKAGES.md) — 如何编写自己的故事内容包

## 致谢

角色代理由 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（[Nous Research](https://nousresearch.com) 出品，MIT 协议）提供支持。其源码 vendor 在 `docker/hermes-story/hermes-agent/`，保留原始许可证。
