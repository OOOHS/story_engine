# Story Engine

`Story Engine` 当前对外交付的产品形态是一个简单的终端文字冒险游戏，但引擎层已经开始按“状态与叙事严格解耦”的方向重构。

核心目标不是做一个会聊天的 GM，而是做一个有绝对世界状态、可插拔文本渲染器、可逐步长成完整叙事沙盒的故事引擎。

## 当前引擎原则

- 世界真相由 ECS 状态决定，不由渲染文本决定。
- 主循环遵循 `Input -> Simulation -> Rendering` 三阶段。
- `Simulation` 阶段只产出结构化结果，不直接向玩家讲话。
- `Rendering` 阶段只能渲染已经确定的事实，不得新增状态变化。
- Storylets、Drama、Plot Clocks 都是结构化约束，不是 prompt 里的散文备注。

## 三阶段主循环

### 1. Input

- 玩家输入本轮行动，或让玩家角色自主决策。
- NPC 通过 `Persona` 产出“意图”而不是最终结果。
- 系统收集 `intents`，这只是尝试，不是已经发生的事实。

### 2. Simulation

- `SimulationControl` 读取当前 `SceneState`、激活的 `Storylets`、`DramaState`、`PlotState`。
- 大模型在这里充当“结构化常识处理器”，只返回 JSON 风格的结算结果。
- ECS 依据 `state_updates`、`plot_updates`、`tension_delta` 等字段更新世界。

### 3. Rendering

- `NarrativeRenderer` 读取已经结算完成的结构化结果。
- 它只负责把确定事实渲染成给玩家看的文字。
- 渲染后文本进入 `Observation`，结构化结果与渲染文本一起写入长期记忆。

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
└── story_engine/
    ├── components/
    │   ├── scene_state.py
    │   ├── simulation_control.py
    │   ├── narrative_renderer.py
    │   ├── drama_state.py
    │   └── plot_state.py
    ├── systems/
    │   ├── input.py
    │   ├── simulation.py
    │   ├── rendering.py
    │   └── memory.py
    ├── session/
    ├── environment/
    ├── scenarios/
    └── prefabs/
docs/
```

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
- 引擎层已切到三阶段循环。
- Arkham 剧本已补上初始状态、storylets、drama、plot entities 的最小示例。
- `ConsoleDriver` 仍然是轻量交互层，复杂导演式介入后续再接回去。
