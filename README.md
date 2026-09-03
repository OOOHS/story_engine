# Story Engine

一个让 AI 角色在权威世界状态里自主生活的叙事引擎。

Story Engine 不是"会聊天的 GM"。世界状态、角色意图和呈现给玩家的文字是三个严格分离的层：世界只由结构化状态决定，角色只能提出意图，语言模型永远不能直接宣布世界发生了什么。这让故事有真正的因果——角色的秘密、误解和信息差是真实存在的，不是文本层面的表演。

作者只需要提供一个尽可能小的故事种子——人物、欲望、关系、地点、少量潜在矛盾——其余的故事由角色 agent 与世界状态的持续互动自然生长出来。

## 特点

- **世界状态与叙事解耦**：所有事实都记录在结构化的世界状态里，渲染层只负责把已经发生的事实讲成故事，不能反向创造事实。
- **每个角色是独立的 Agent**：角色只提出行动意图，具体能否成功、造成什么后果，由世界规则和结算层裁定。角色拥有私有的记忆、信念和目标，看不到别人的秘密，也看不到全局真相。
- **离散事件时间**：动作按真实耗时推进，不是"你一句我一句"的固定轮次；同屏角色可以并发行动，离屏角色也会持续生活。
- **可插拔的角色大脑**：默认使用 Hermes 作为长程存活的角色代理，也可以切换成纯规则驱动的离线角色用于快速验证。
- **结构化叙事机会（Storylet）**：剧情节点是对世界状态的条件声明，命中与否由角色的真实行动决定，不是脚本强制触发。
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

要让角色真正"思考"、叙述真正是文学性的语言，需要接入语言模型。有两层模型：

1. **GM / 叙述器**——负责结算世界规则和渲染文字。
2. **角色代理（Hermes）**——每个 NPC 独立的大脑，跑在单独的进程/容器里。

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

两种方式任选一种：

**Docker（推荐）**

```bash
docker build -t hermes-story:latest docker/hermes-story
python main.py --scenario thirteenth-floor
```

**本地进程**（不需要 Docker，但需要一个独立的 Python 环境）

```bash
python -m venv .hermes-venv
.hermes-venv/bin/pip install -e docker/hermes-story/hermes-agent

python main.py --scenario thirteenth-floor \
  --hermes-transport local \
  --hermes-python .hermes-venv/bin/python
```

### 3. 配置角色代理的 key

同样在 `.env` 里：

```bash
IKUN_API_KEY=sk-xxxx
```

配好之后，直接运行 `python main.py --scenario false-heiress` 或 `python web_main.py --scenario false-heiress --port 8000`，就是完整体验。

也可以跳过内置场景，从一段文字或 YAML 直接编译出新故事：

```bash
python main.py --seed "三个陌生人被困在一座雪山小屋里，每个人都有秘密。" --profile offline
```

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
