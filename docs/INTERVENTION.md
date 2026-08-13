# 人类介入

## 当前现实状态

引擎层已经支持这些接口：

- `overrides`
- `world_edits`
- `topology_changes`
- `inject_events`
- `on_phase_done`

但当前对外的 `ConsoleDriver` 仍然是简化版，只暴露：

- 玩家在角色空闲决策点手动输入行动
- `/a` 让该轮玩家角色自主行动
- `/q` 退出

也就是说，介入能力在引擎 API 已有挂点，在产品层 UI 还没有重新完整接回来。

## 介入点如何理解

### 步前

- 改玩家/NPC 的本轮意图
- 通过 `world_edits` 修改已有对象的普通描述属性；它经过 `HostWorldEditTransaction` 原子差分，只接受严格 JSON 状态，不能改所有权、放置、容器或空间拓扑，并会像普通世界变化一样生成 POV-safe Event
- 通过 `topology_changes` 原子开放/中断已有地点间的通路
- 同一步同时提供两类命令时，由 `HostMutationTransaction` 作为一个批次提交；任一项非法都会共同回滚并中止该 step，修正后可在相同模拟时间重试
- Console/Web 使用 `public_step_status` 区分未开始、权威回滚、交付失败和成功；未提交的介入尝试只能成为 system 诊断，不能出现在普通故事回合中
- 注入世界事件

### Simulation 后

- 审核结构化结算结果
- 强制补充导演指令
- 手动修正状态更新

### Rendering 后

- 覆盖本轮显示给玩家的文字
- 保留底层状态不动，只改表现层

## 与权威事件循环的关系

在旧模型里，人类介入经常意味着“直接改 GM 的输出”。  
在新模型里，最好把介入分成两类：

- 改状态或改结算：属于 `Simulation`
- 改给玩家看的文字：属于 `Rendering`

这也是后续恢复高级导演式交互时应遵守的边界。
