# 项目设计说明

## 一、产品层与引擎层

当前项目有两个不同层级的目标：

- 产品层：先交付一个简单、能玩的终端文字冒险游戏。
- 引擎层：逐步收敛为一个“状态与叙事严格解耦”的故事引擎。

这意味着外层体验可以先朴素，但内层架构不能继续把“状态判定”和“文案输出”混在一次 LLM 调用里。

## 二、核心原则

### 1. 世界状态优先

- 世界的真实状态由 ECS 组件承载。
- 文本只是这些状态的表现层，不是状态本身。
- 任何会改变世界的结算，都必须先落到结构化状态上。

### 2. Storylet 不是分支树

- 事件不再是写死的分叉剧情。
- Storylet 只描述“什么条件下，它有资格介入”以及“它的叙事意图是什么”。
- 具体如何兑现，由 Simulation 层结合当前状态、导演指令、长线 plot 自然收敛。

### 3. 导演系统是结构化约束

- 张力系统不直接写戏。
- 它只注入高层约束，如“本轮必须出现危机”或“允许短暂缓释”。
- 具体危机由 Simulation 层在当前因果中落地。

### 4. 主线必须实体化

- 长线阴谋、政治暗流、秘密、灾厄，不能只是 prompt 提示。
- 它们应以 `PlotState` 中的时钟实体存在，并被推进、停滞、兑现。

## 三、当前主循环

```text
Input
  -> 收集玩家/NPC 意图
Simulation
  -> 根据绝对状态与结构化约束做结算
Rendering
  -> 把已确定事实渲染成文本
Memory
  -> 归档本轮结构化结果与文本表现
```

`Memory` 仍然存在，但它不属于玩家认知中的核心三步曲，而是结算后的归档层。

## 四、关键组件

### SceneState

保存当前客观世界状态：

- `description`
- `world_objects`
- `actor_states`
- `scene_flags`

### SimulationControl

负责 Simulation 阶段：

- 输入：`SceneState`、`intents`、storylets、director packet、plot snapshot
- 输出：`resolved_actions`、`state_updates`、`storylet_hits`、`plot_updates`、`tension_delta`

### NarrativeRenderer

负责 Rendering 阶段：

- 输入：已经结算好的结构化结果
- 输出：玩家可见文本
- 约束：不得新增结构化结果之外的事实

### DramaState

负责维护张力并产出导演指令：

- `stay_course`
- `raise_pressure`
- `inject_crisis`
- `allow_release`

### PlotState

将长线事件实体化成可推进的 clocks。

## 五、系统职责

### InputSystem

- 收集玩家输入和 NPC 意图
- 形成 `context["intents"]`

### SimulationSystem

- 解析 storylets
- 读取 plot pressure
- 向 drama manager 申请导演指令
- 让 `SimulationControl` 返回结构化结算
- 将结算写回 ECS 状态

### RenderingSystem

- 调用 `NarrativeRenderer`
- 把结果广播到各角色 `Observation`

### MemorySystem

- 归档本轮 `intents`
- 归档结构化结算
- 归档渲染文本

## 六、当前限制

- `ConsoleDriver` 仍然只暴露玩家每回合输入这类简单交互。
- 中途审查、叙述后编辑、快照回退等导演式能力，还没有重新挂回现有三阶段架构。
- Storylet 条件语言目前仍是最小实现，后面可以继续扩展成更丰富的状态查询表达式。
