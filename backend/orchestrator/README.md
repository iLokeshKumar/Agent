# Orchestrator

Runs agent work off the `task` queue.

## Components

- `queue.py` — in-memory `task_queue` (backed by DB `tasks` table)
- `worker.py` — `worker.start()` / `worker.stop()` loop, polls queue, dispatches via `executor`
- `executor.py` — loads agent from `app/agents/registry.py`, runs `policy` gate → `validate()` → `execute()`, writes `result` / `status`
- `workflow_engine.py` — DAG engine for `chain.py` / `app/models/workflow.py` (P2P: P1→S1→P4→F3)
- `chain.py` / `router.py` — workflow definition + routing

Lifecycle is started in `app/main.py:lifespan`:

```python
worker.start()
get_sync_scheduler().start()
...
worker.stop()
```

No separate README needed — see `backend/README.md` → Architecture.
