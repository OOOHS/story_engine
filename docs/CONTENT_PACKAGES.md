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

`src/story_engine/scenarios/` 只保留通用 `ScenarioConfig` schema。具体人物、地点、对白风格、秘密、Plot、Storylet 和初始关系不得放入这个目录。

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
- 初始稀疏关系、Claim 与 Plot；
- 通用 Storylet、因果规则、时间安排和宿主可见性 schema。
- 可选的 `narration.guidance` 与单回合文本上限；不声明时核心 Narrator 保持中立，不替内容选择节奏或题材腔调。

其中初始行为角色必须同时出现在 `characters` 与 `initial_actor_states`，名称一一对应，并具有指向 `initial_world_objects` 中已知地点的 `location`。仅仅为了让叙述提到某人，不应创建没有身体的 CharacterConfig；应把死者、历史人物或离场人物保存在 Claim、物品、事件或记忆种子中，直到宿主通过正式 Character Entry Authorization 让其成为可行动角色。同样也不能只在 actor states 中放一个由 GM 代演的“背景 NPC”。

内容包不能：

- 修改引擎系统来识别故事人物或地点名称；
- 让 Agent/GM 直接写概率、资产、关系数值、目标完成或空间拓扑；
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
