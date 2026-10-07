# 内容包边界

故事引擎与具体故事采用单向依赖：

```text
story_engine_content / 外部内容包
              │
              ▼
        story_engine schema + runtime
```

`story_engine` 永远不能反向 import `story_engine_content`。因此删除全部捆绑内容后，引擎的 Agent、ECS、时间、概率、关系、资产、事件与渲染机制仍应完整工作。

这个边界也由两个独立 wheel 保证：根目录 `pyproject.toml` 构建
`story-engine`，只包含 `src.config` 与 `src.story_engine`；
`src/story_engine_content/pyproject.toml` 单独构建可选的
`story-engine-content`，并声明对核心发行物的依赖。核心 wheel 不包含任何 bundled
或 evaluation seed，内容 wheel 也不复制引擎实现。

```bash
python -m build --wheel --no-isolation --outdir dist/core .
python -m build --wheel --no-isolation \
  --outdir dist/content src/story_engine_content
```

只开发引擎时安装第一个 wheel；需要仓库示例故事时再安装第二个。两个发行物仍保留
当前的 `src.story_engine` / `src.story_engine_content` import 路径，避免在这次架构
调整中混入无关的全仓 import 重命名。

## 两个内容层

- `src/story_engine_content/bundled/`：可试玩的完整示例故事；应用必须显式选择，没有默认剧本。
- `src/story_engine_content/evaluation/`：只用于验证通用引擎性质的最小种子，不作为产品故事；它与产品内容一样位于核心包之外。

`src/story_engine/scenarios/` 只保留通用 `ScenarioConfig` schema。具体人物、地点、对白风格、秘密、宏剧情、Storylet 和初始关系不得放入这个目录。

## 选择一个故事

```python
from src.story_engine.session import create_session
from src.story_engine_content.bundled.false_heiress import false_heiress_scenario

session = create_session(false_heiress_scenario)
```

导入 `story_engine`、`story_engine.scenarios`、`story_engine_content` 或 `story_engine_content.bundled` 都不会自动实例化或选择故事。

仓库自带的 Console/Web 应用同样没有默认故事，必须显式指定惰性 catalog 中的
别名：

```bash
python main.py --scenario false-heiress
python web_main.py --scenario cthulhu-arkham --port 8000
```

外部内容包不需要修改 bundled catalog。应用也接受明确的
`module.path:attribute`；attribute 可以是 `ScenarioConfig` 对象，或无参数返回
`ScenarioConfig` 的 factory：

```bash
python main.py \
  --scenario-ref my_story.content:build_scenario

python web_main.py \
  --scenario-ref my_story.content:scenario \
  --port 8000
```

这里没有自动扫描、默认选择或隐式 fallback。只有命令行明确命名的模块会被 import；
返回值不是 `ScenarioConfig` 时启动立即失败。

`src.story_engine_content.catalog` 只保存 alias 到 module/attribute 的字符串映射；
导入 catalog 不会 import 三个故事模块。只有调用 `load_bundled_scenario(name)` 后
才加载选中的一个故事。可用别名通过 `available_bundled_scenarios()` 查询。

## 新建外部内容包

内容包只需要构造一个 `ScenarioConfig`，然后交给 `create_session()`。它可以声明：

- 初始地点、物品和公开/私有世界状态；
- 角色身份、目标、需求、特质和 Agent runtime 配置；
- 初始稀疏关系与 Claim；
- 通用 Storylet、因果规则、时间安排和宿主可见性 schema。
- 可选的 `narration.guidance` 与单回合文本上限；不声明时核心 Narrator 保持中立，不替内容选择节奏或题材腔调。

`description`、`environment` 和 `initial_state` 会用于玩家开场或叙述器输入，应只写玩家可以知道的内容。尚未公开的作者背景写在 `private_author_premise`；它只供语义 GM 和事后导演参考，具体秘密仍须用 Claim、对象和角色私有知识建成可验证状态。

其中初始行为角色必须同时出现在 `characters` 与 `initial_actor_states`，名称一一对应，并具有指向 `initial_world_objects` 中已知地点的 `location`。仅仅为了让叙述提到某人，不应创建没有身体的 CharacterConfig；应把死者、历史人物或离场人物保存在 Claim、物品、事件或记忆种子中，直到宿主通过正式 Character Entry Authorization 让其成为可行动角色。同样也不能只在 actor states 中放一个由 GM 代演的“背景 NPC”。

内容包不能：

- 修改引擎系统来识别故事人物或地点名称；
- 让角色 Agent 直接写权威状态、随机数或目标完成；世界模型的物品和拓扑变化使用专用结构通道与完整暂存校对；
- 依赖一个由引擎隐式加载的全局默认故事；
- 把评测专用 seed 当成生产内容入口。

## 架构门禁

`tests/runtime/test_architecture_boundaries.py` 会检查：

- `story_engine/scenarios` 只有 schema；
- 引擎源码不含捆绑故事人物或地点；
- 引擎不 import `story_engine_content`；
- 引擎核心可以定义 Scenario schema 和通用 loader，但不能调用 `ScenarioConfig`、
  `CharacterConfig` 等构造具体故事；
- 内容包 package initializer 不选择默认故事。
- 实际构建的核心 wheel 不包含 `story_engine_content`，内容 wheel 不包含
  `story_engine` 或 `config` 实现。

发行物边界不只检查 manifest。下面的离线门禁会先把必要源码复制到临时 staging
目录，再使用当前 Python 环境构建两个 wheel 并检查 zip 文件清单；工作区已有的
`build/`、egg-info、缓存或未跟踪文件不会混入结果：

```bash
python scripts/check_distribution_boundary.py
```

导演配置：`story_planner_enabled` 控制导演的新故事块提案；共同叙事记录与已有故事块追踪继续运行；`story_planner_interval_turns` 默认 5 个已生成玩家世界消息的回合，`story_planner_interval_seconds` 可配置时间间隔。按时间的询问在下一次世界消息生成成功时检查，任一间隔到期即可触发。导演读取累计玩家叙事、作者私有设定、完整故事块目录和待审核草案，使用 `propose_storylet` 工具登记新草案。宿主检查结构与标识后登记动态 Storylet，未来由世界结算模型判断自然语言触发条件并落实具体影响。角色参与可以写在剧情方向中，实际行动仍由角色提交；需要角色配合的部分转为可拒绝的导演建议。

旧 `drama` 配置保留读入兼容。Timeline 模块与日程接口已删除；世界时间与行动持续长度由 GameClock / ActionEventQueue 管理。

Storylet 的 `intent` 描述未来剧情方向，可包含多人互动、宏大事件和期望的世界影响。`trigger` 是自然语言启动条件，由世界结算模型根据结算前权威事实判断；可选 conditions 保留精确状态约束。宿主提交带 source_storylet_id 的 World 意图，模型逐项返回结果：条件未成立用 inactive，等待角色选择用 deferred，具体外部事件用 success/partial/complication，矛盾后果用 blocked。deferred/inactive 结果隐藏且保持空事实文本；可选建议通过 director_suggestions 输出 recipient/source_storylet_id/text。生产模式中，首次事件或可选建议成功提交后，独立追踪会话持续推进该故事块；启动条件只约束启动，后续推进保持角色自主与世界一致性检查。追踪者输出 waiting/active/completed/abandoned、简短 progress 和下一步 advance 或 null。完成或放弃后会话停止调用；one_shot=True 在 completed 时登记 consumed_storylets，abandoned 单独保留于追踪状态。每个注册 id 对应一个追踪生命周期，再次开展新的剧情可注册新的故事块。明确缺少 StoryTracking 的离线测试宿主保留事件触发基线：一次性事件生效后消费，可重复事件等待条件失效后重新武装。

Storylet 由内容、前置条件和执行后的世界影响组成；conditions 描述何时有资格生效，intent 描述面向未来的外部情境、事件及预期影响。事件实际需要的新人物、物品和地点由本轮结算模型补全，允许在同一个候选世界中交叉引用；每个新人物进入独立 Agent runtime。角色言论保留来源，未知真相延迟到外部结算实际依赖它时确定。程序保留必要身份、位置、容量、事务与存档约束。导演新草案经过结构校验后登记，具体后果经过世界提交校对；scene 条件路径从完整快照开始，例如 scene_flags.weather，actor/world_object 条件从指定 target 属性开始。

生产动态物品可在 spawn.properties 声明容器和用途；topology_changes 支持已有通路的 connect/disconnect。communicate 填写实际 recipients，hidden 保护第三方视角，接收者获得有说话人来源的消息。连续故事块的后续事件可离开初始 location。
