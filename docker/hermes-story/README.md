# Hermes Story Runtime

Story Engine 默认用本地 `--subject-server` 子进程跑每个角色。容器镜像仍可构建，但 Docker 传输已废弃：`docker run --rm` 没有可写 subject home，会话不能恢复。

```bash
python -m venv .hermes-venv
.hermes-venv/bin/pip install -e docker/hermes-story/hermes-agent
python main.py --scenario thirteenth-floor --hermes-python .hermes-venv/bin/python
```

The vendor source is distributed under its upstream MIT license; see
`hermes-agent/LICENSE`. Tests and Python bytecode caches are intentionally not
copied into this image source tree.

The Dockerfile still exists for the deprecated container transport. It
installs the vendor snapshot into `/opt/story-venv`.

Story Engine does not import Hermes on the host. Production starts one local
`--subject-server` process per character, sends one JSON-line `subject_packet`
per turn on stdin, and reads one marker-delimited response per turn from stdout.
The process is bound to a stable `session_id` and an isolated `HERMES_HOME`,
so a crash or Host step rollback can restore conversation and native memory.
The injected test transport may still execute one request per process.

The response envelope is strict and actor-bound:

```json
{"protocol_version":1,"agent_id":"request agent id","content":"AgentDecision JSON text"}
```

Exactly one envelope is allowed. The Host rejects missing/duplicate markers,
unsupported versions, mismatched agent ids, empty content and oversized stdout;
it does not accept legacy `final_response` aliases or infer an action from logs.
Hermes must return exactly one executable `action` as a non-empty
natural-language string. The thin-shell system prompt assigns Hermes to
*operate* that character's next action from persona and evidence; it does
not ask the model to inhabit the character in the first person. Hermes keeps
deliberation and any randomness inside its own long-lived context. World legality,
duration, resource conflicts, probability checks and authoritative settlement
remain Host-owned.

No `.env` file is copied into the image. The host launcher passes only approved
environment variable names with Docker `-e KEY`. The thin shell maps
`HERMES_PROVIDER`, `HERMES_MODEL`, `HERMES_BASE_URL`, and an approved API key
onto the public `AIAgent` constructor. With a custom endpoint, set at least
`HERMES_BASE_URL`, `HERMES_MODEL`, and `IKUN_API_KEY` (or `OPENAI_API_KEY`);
without an explicit `HERMES_PROVIDER`, the shell selects `custom`. A plain
`OPENAI_API_KEY` selects the `openai` provider.

To run a content-independent multi-seed audit, provide a `ScenarioConfig`
factory to the host launcher:

```bash
python scripts/eval/run_hermes_episode_sweep.py \
  --scenario-factory package.module:build_scenario \
  --image hermes-story:latest \
  --seeds 0,1,2 \
  --steps 12 \
  --stop-on-closure \
  --output artifacts/hermes-story
```

The launcher deep-copies the scenario, binds every behavioral character to the
container adapter, and uses deterministic Host simulation and fact rendering.
It never imports vendor Hermes or reads `.env`.

Before creating any Episode it performs one image inspection. Missing images
fail once instead of once per seed. Marker content must be a JSON decision with
one non-empty natural-language action; prose, candidate arrays and empty JSON fail closed and never
become inferred story actions.

The repository launcher bind-mounts the project-owned `entrypoint.py` and
`config.yaml` read-only by default. Editing the protocol shell or prompt policy
therefore does not require rebuilding the vendor image. The Sweep metadata
records SHA-256 for both mounted files, so results remain attributable to the
exact thin-shell revision. Pass an empty `--entrypoint-path` or `--config-path`
only when deliberately evaluating the copies baked into the image.
