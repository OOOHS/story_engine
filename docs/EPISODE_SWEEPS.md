# 多随机种子 Episode Sweep

单个 Episode 只能证明某个 seed 下发生了什么，不能证明角色策略和世界机制在不同随机选择下仍然稳定。`EpisodeSweepRunner` 对同一个 Session factory 顺序运行多个 seed，保存每轮完整 Episode trace，并生成跨 seed 汇总。

## Python API

```python
from src.story_engine.evaluation import EpisodeClosurePolicy, EpisodeSweepRunner


def create_evaluation_session(seed):
    return create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories=runtime_factories,
    )


sweep = EpisodeSweepRunner().run(
    create_evaluation_session,
    seeds=range(20),
    steps=16,
    verify_replay=True,
    closure_policy=EpisodeClosurePolicy(stable_steps=2),
    metadata={
        "scenario": "minimal-investigation",
        "runtime": "hermes-container",
    },
)

sweep.write_directory("artifacts/minimal-investigation")
```

Session factory 必须真正使用收到的 seed；返回不同 `Session.random_seed` 会作为失败记录，而不是悄悄产生不可重放数字。

## Host launcher

项目提供独立 launcher：

```bash
python scripts/eval/run_episode_sweep.py \
  --factory my_content.evaluation:create_evaluation_session \
  --seeds 0,1,2,3,4,5,6,7,8,9 \
  --steps 16 \
  --verify-replay \
  --stop-on-closure \
  --closure-stable-steps 2 \
  --quiet \
  --metadata scenario=minimal-investigation \
  --metadata runtime=hermes-container \
  --output artifacts/minimal-investigation
```

`--factory` 使用 `module.path:callable_name`，callable 接受一个 int/string seed 并返回普通 `Session`。Launcher 不读取 `.env`，也不 import Hermes vendor；Hermes 是否通过 Docker 运行仍由项目自有 Session/runtime factory 决定。

`--quiet` 只压制每个 System 的控制台进度文本，完整 Episode trace、错误和 summary 仍会写入 artifacts；大规模 sweep 建议启用。

`--stop-on-closure` 启用宿主审计的自然停止。`--closure-minimum-steps` 可要求至少运行若干轮，`--require-plot-closure` 可把未完成 Plot 也列为阻塞项。默认允许没有手工可验证 Goal 的纯世界种子在动态线程平息后结束；任务式基准可加 `--require-goal-anchor`，要求内容至少提供一个 Host 可验证的 Goal。默认还会等待每个 autonomous、非 dormant Agent 至少完成一次决策，并要求连续 closure steps 不再产生新的结构化世界/社会/知识变化；避免错峰离屏角色尚未参与、或调查仍在改变人物认知时提前收束。章节式评测可分别用 `--allow-unexercised-agents` 和 `--allow-material-change-closure` 放宽这两项。未启用自然停止时，每个 Episode 仍跑满 `--steps`。

## Artifacts 协议

```text
artifacts/minimal-investigation/
├── summary.json
├── review.md
├── episodes/
│   ├── 0000-<seed-hash>.json
│   ├── 0001-<seed-hash>.json
│   └── ...
└── transcripts/
    ├── 0000-<seed-hash>.md
    ├── 0001-<seed-hash>.md
    └── ...
```

`summary.json` 包括：

- requested/completed/authoritative episode 数；
- 单个 seed factory/runtime 失败；
- replay mismatch；
- 每项 Episode metric 的 mean/min/max/sum；
- 每种质量 flag 的 count/rate；
- 不同动作轨迹和最终世界状态数量；
- 实际发生过角色决策的 Episode 数；
- 每个 Episode 的决策数、通过校验的动机自述数与被驳回的动机引用数，并通过 `metric_summary` 汇总 mean/min/max/sum；
- 有多少 Episode 至少出现一条角色自己归因到 Goal、Sentiment 或 Drive need 的真实行动，以及跨 seed 的动机边数量/行动数量；
- 可验证 Goal 的结算率；
- Agent Goal 的采用、开放目标细化、结尾开放积压和最终结算数量；这些指标也直接进入 `review.md` 与 Hermes launcher 的终端 JSON 摘要；
- 自然完结数量/比例与 steps-to-closure 的 mean/min/max；
- 具有跨 step 因果弧线、以及同时达到 closure 的 resolved causal arc 的 Episode 数量/比例；
- 跨 step 显式 causal handoff 总量，避免只用同轮因果深度误判长程故事生长；
- 每个 seed 的轻量摘要与完整报告相对路径。

完整的 `episodes/*.json` 在每个 step 中保存有界、POV-safe 的 `narrative_text`。`transcripts/*.md` 只整理这些玩家可见文本，便于直接人工审阅；`summary.json` 的每个 `episode_files` 条目同时给出 `path` 和 `transcript_path`。顶层 summary 不复制整段故事，从而保持批量摘要轻量，也避免把私有 Agent/GM 状态误当作玩家文本。

`review.md` 是人工审阅入口：它汇总闭合、因果弧、决策规模、动机自述与驳回情况、Replay、Hermes 调用预算和质量标记，并为每个 seed 链接 transcript 与完整 JSON。它不复制故事正文，也不保存 Agent thought、未选行动、隐藏关系或 GM 私有包；最后的“故事是否有趣”仍明确要求阅读 transcript，结构指标不能代替审美判断。

单个 seed 失败不会终止整个 sweep。失败会记录 seed、phase、异常类型和截断后的错误信息，其余 seed 继续运行。

## Sweep 级退化标记

- `empty_sweep`：没有 seed；
- `episode_failures`：至少一个 seed 无法完成；
- `authority_violations`：Episode 硬权限审计失败；
- `replay_mismatch`：相同 seed 重跑得到不同权威 trace；
- `all_episodes_stagnant`：所有完成 Episode 都停滞；
- `frequent_deadlock`：至少一半 Episode 进入僵局；
- `frequent_repetitive_actions`：至少一半 Episode 长期重复同一动作批次；
- `seed_insensitive_policy`：至少三个发生过角色决策的 Episode 得到完全相同的角色/动作类型/显式目标与动机自述轨迹；
- `no_verifiable_goal_resolution`：存在有权威条件的 Goal，但跨 sweep 没有任何 achieved/failed 转换。
- `open_goal_backlog`：至少一个运行了 12 step 的 Episode，且这些长程 Episode 平均每个仍保留至少一个没有权威完成路径的 active Agent Goal；这是结构退化提醒，不等于审美判定。
- `no_episode_closure`：启用完结策略后，没有 Episode 在上限内收束；
- `low_closure_rate`：启用完结策略后，少于一半 Episode 收束。
- `no_policy_motive_chain`：存在发生过角色决策的 Episode，但没有任何 Episode 产生动机到真实行动的因果边；
- `low_policy_motive_coverage`：至少三个有决策的 Episode 中，少于一半出现带动机自述的真实行动；
- `unbacked_motive_claims`：被驳回的动机引用数达到决策数的一半以上，说明 runtime 在编造自己并不持有的目标或义务；引用不存在的动机比不解释动机更糟，因此单独标记而不是稀释上面的覆盖率；

这些仍然是结构性证据，不是“故事好看分数”。跨 seed 多样性太低可能表示策略失效，但多样性很高也可能只是随机噪声；需要结合因果变化、Goal 收束、角色区分度和权限审计共同判断。

Launcher 默认只报告质量标记：权限正确但结构退化的 Sweep 仍返回 0，避免把启发式指标伪装成硬真理。CI 或正式验收可显式使用 `--strict-quality`；此时权限/执行失败仍返回 2，而权威结果包含任一质量标记时返回 3。终端 JSON 同时写出 `strict_quality` 与 `strict_quality_failed`，便于脚本区分两类失败。

## 内置最小调查回归种子

`src.story_engine_content.evaluation.minimal_investigation:create_minimal_investigation_session` 提供一个只用于组合回归的内容包：两名 Agent、一本可争夺账册、一个秘密 Claim、相反的可验证 Goal，没有 Storylet、Plot 或核心人物专属代码，也没有故事专用 Simulation resolver。账册争夺使用 `engine:take`，主动调查由通用 EvidenceObservationResolver 从 Claim-Evidence 边派生，保管人的否认通过结构化 Claim communication proposal 表达，整个案例运行在 HostRuleSimulationControl 上。

```bash
python scripts/eval/run_episode_sweep.py \
  --factory src.story_engine_content.evaluation.minimal_investigation:create_minimal_investigation_session \
  --seeds 0,1,2,3,4,5,6,7,8,9,10,11 \
  --steps 6 \
  --quiet \
  --output artifacts/minimal-investigation
```

当前规则候选基线在 12 个 seed 上得到：

- 12/12 authoritative；
- 0 violation、0 sweep quality flag；
- 12 条不同动作/策略轨迹；
- 可验证 Goal 结算率 100%；
- 调查者取得证据 4 次，保管人取得证据 8 次；
- 11/12 Episode 结束时双方都已获得该 Claim；
- active Goal 平均值为 0；
- actor differentiation 平均约 0.47。

这组数字是机制回归基线，不是产品质量目标。测试只固定较宽的涌现下限，并允许今后在有证据的情况下重新校准权重。

## 内置最小目标生长回归种子

`src.story_engine_content.evaluation.minimal_goal_growth:create_minimal_goal_growth_session` 验证故事不会在第一个目标结算后机械停止。该 seed 使用通用 `HostRuleSimulationControl`，没有故事专用 Simulation resolver：旅人通过环境自动派生的 `engine:take` 取得钥匙后，从宿主已确认的 resolved Goal 自主形成“带着钥匙离开房间”的新目标；宿主核验来源、生成 id 和 priority，并把 `reach_location` 编译为隐藏的权威条件。合法移动由空间图确定性结算；角色实际抵达走廊后目标 achieved，Episode 经过稳定窗口才自然收束。

这个内容包没有 Storylet 或 Plot，固定验证：

- Agent 不能伪造 goal source 或 actor；
- 初始 Goal 结算后的下一轮仍有机会形成后续目标；
- 新目标进入宿主策略和 Episode stakes；
- `stable_steps` 不会把第一个目标刚结束的短暂空窗误判为结局；
- Agent 目标被宿主依据真实位置结算后，世界才重新获得完结资格。
