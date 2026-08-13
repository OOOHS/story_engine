# 长时程世界稳定性审计

`SoakRunner` 与 Episode evaluation 的目标不同：Episode 找到一个故事何时自然收束，Soak 则在达到 closure 后仍继续运行同一个 Session，检查世界、Agent、记忆和事件队列能否长期保持稳定。

```python
from src.story_engine.evaluation import EpisodeClosurePolicy, SoakRunner

report = SoakRunner().run(
    session,
    steps=100,
    sample_every=10,
    closure_policy=EpisodeClosurePolicy(stable_steps=2),
    quiet=True,
)
report.write_json("artifacts/soak.json")
```

Soak 不把 Episode closure 当成 world shutdown。它记录第一次稳定收束的 step，但继续执行后续模拟，审计：

- authority violation；
- Agent Goal adoption、resolution、active/total 上限；
- 每个角色 episodic/consolidated/high-salience/low-salience memory 数量；
- Memory consolidation 次数；
- foreground Agent 是否长期得不到决策机会；
- Pair Relationship 定性状态变化和 A→B→A 振荡；
- 单一动作重复长度，以及 closure 前是否形成动作循环；
- simulation time 是否在 pending queue 存在时停止推进；
- closure 前是否长期没有实质状态变化；
- 最终 action queue 是否仍有未完成事件。

宿主 launcher：

```bash
python scripts/eval/run_soak.py \
  --factory src.story_engine_content.evaluation.minimal_goal_growth:create_minimal_goal_growth_session \
  --seed soak-100 \
  --steps 100 \
  --sample-every 10 \
  --closure-stable-steps 2 \
  --quiet \
  --output artifacts/minimal-goal-growth-soak.json
```

Launcher 不读取 `.env`，也不 import Hermes vendor。Session factory 仍负责选择规则 runtime、LLM runtime 或项目自有 Hermes container adapter。

当前结构退化标记包括：

- `authority_violations`；
- `goal_explosion`；
- `goal_history_explosion`；
- `memory_unbounded`；
- `no_episode_closure`；
- `foreground_starvation`；
- `relationship_oscillation`；
- `preclosure_stagnation`；
- `preclosure_action_loop`；
- `stuck_action_queue`。

这些标记区分 closure 前后的含义。一个已经完结、没有新刺激的最小世界在后续不断 `wait` 不会被误判成“故事无法推进”；但如果 Episode 尚未收束就连续循环同一动作或长期没有状态变化，则属于真实退化。

长程指标分别记录 `goal_adoption_count`、`goal_refinement_count` 与 `goal_resolution_count`，用来区分“不断产生新愿望”“开放目标后来获得具体路径”和“目标最终由权威事实结算”。

仓库已执行过最小目标生长场景的 50-step 实测：

```text
authoritative: true
quality flags: 0
first closure: step 2
goal adoption: 1
goal resolution: 2
memory consolidation: 2
final memory: 30 episodic + 2 consolidated summaries
low-salience episodic: 28（未继续线性增长）
foreground starvation: 0
relationship oscillation: 0
final pending actions: 0
```

该场景在 closure 后长期等待，因此总动作重复很高；Soak 单独记录 `longest_action_repetition`，但只在 closure 前达到循环阈值时产生 `preclosure_action_loop`。
