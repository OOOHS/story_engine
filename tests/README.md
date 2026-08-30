# Test layout

`pytest` recursively discovers the suite under this directory. Tests are grouped by the boundary they primarily verify:

- `agents/`: character runtimes, policy, Hermes subject ownership, goals, drives and attention;
- `world/`: actions, legality, topology, objects, events, probability and world transactions;
- `social/`: claims, knowledge transfer, relationships, sentiments, obligations, agreements and exchange;
- `narrative/`: storylets, storylets, conflict, timeline and narrative engines;
- `evaluation/`: Episode/Sweep/Soak evaluation and minimal end-to-end scenarios;
- `runtime/`: configuration, component structure, content boundaries, lifecycle, authority and rollback;
- `web/`: web/UI projections and interaction contracts.

Shared fixtures and repository path setup remain in `conftest.py`.

Run everything:

```bash
python -m pytest -q
```

Run one domain:

```bash
python -m pytest -q tests/agents
python -m pytest -q tests/world
python -m pytest -q tests/social
```

Old interactive prompt experiments are not automated tests. They live under `scripts/legacy/manual_prompt_probes/` and are excluded by `pytest.ini`.
