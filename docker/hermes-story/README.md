# Hermes Story Runtime

This directory is the project-owned thin shell. Place the unmodified Hermes
vendor snapshot at `docker/hermes-story/hermes-agent/` before building:

```bash
docker build -t hermes-story:latest docker/hermes-story
```

The Dockerfile installs that snapshot into the isolated `/opt/story-venv`
environment. This is compatible with Hermes base images whose system Python is
marked as externally managed (PEP 668), while keeping both the base environment
and vendor source unchanged.

To reuse a local Hermes-derived image instead of the default Python base:

```bash
docker build \
  --build-arg HERMES_BASE_IMAGE=hermes-seg:latest \
  -t hermes-story:latest \
  docker/hermes-story
```

Story Engine does not import Hermes on the host. In the production transport it
starts one container per character with `--subject-server`, keeps one vendor
`AIAgent` alive, sends one JSON-line `subject_packet` per turn on stdin, and
reads one marker-delimited response per turn from stdout. Conversation, native
JSON memory and tool context therefore survive across turns. The injected test
transport may still execute one request per process.

For local development, the same process boundary can be used without Docker:
configure `HermesLocalProcessConfig` with a Hermes virtualenv Python, the
project-owned `entrypoint.py`, and the vendor source directory. Story Engine
starts one local `--subject-server` child process per character and reuses the
same protocol; Docker remains the packaging and deployment option.

The response envelope is strict and actor-bound:

```json
{"protocol_version":1,"agent_id":"request agent id","content":"AgentDecision JSON text"}
```

Exactly one envelope is allowed. The Host rejects missing/duplicate markers,
unsupported versions, mismatched agent ids, empty content and oversized stdout;
it does not accept legacy `final_response` aliases or infer an action from logs.
Hermes may return one direct executable action when it judges that no real
deliberation is needed. The thin-shell system prompt assigns Hermes to
*operate* that character's next action from persona and evidence; it does
not ask the model to inhabit the character in the first person. Otherwise its content contains at least two genuinely
distinct internal `candidates`, each with a `motive_lens`, structured
`intent_signature`, finite utility and executable action. The project-owned
Hermes adapter samples those candidates privately in two stages (lens, then
action) and exposes only the selected action to the Host. World legality,
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
an executable action or valid internal candidates; prose and empty JSON fail closed and never
become inferred story actions.

The repository launcher bind-mounts the project-owned `entrypoint.py` and
`config.yaml` read-only by default. Editing the protocol shell or prompt policy
therefore does not require rebuilding the vendor image. The Sweep metadata
records SHA-256 for both mounted files, so results remain attributable to the
exact thin-shell revision. Pass an empty `--entrypoint-path` or `--config-path`
only when deliberately evaluating the copies baked into the image.
