# AI Research & Innovation Lab

Autonomous 24/7 AI research lab: it discovers innovation opportunities, investigates them with specialised agents, red-teams and independently verifies the evidence, and reports only well-supported recommendations.

**Status:** Phase 1 (foundation) in progress. The API skeleton starts and serves health checks; no research functionality exists yet. See [the implementation plan](docs/IMPLEMENTATION_PLAN.md) for the real state.

## Documents
- [Specification](AI_RESEARCH_LAB_SPEC.md): governing requirements
- [Architecture](docs/architecture/ARCHITECTURE.md)
- [Implementation plan and test criteria](docs/IMPLEMENTATION_PLAN.md)
- [Decisions and risks](docs/DECISIONS.md)
- [Server assessment](docs/operations/SERVER_ASSESSMENT.md)

## Planned stack
LangGraph · FastAPI · PostgreSQL + pgvector · React + Vite · Ollama · k3s

## Local development (laptop)
Requires the user-space toolchain (micromamba env `ai-lab`; see `scripts/env.sh`).

```bash
source scripts/env.sh          # toolchain, LAB_ENV=dev, database URLs
scripts/dev-db.sh init         # once: local PostgreSQL cluster in var/pgdata
scripts/dev-db.sh start
python -m scripts.migrate upgrade
python main.py                 # API on http://127.0.0.1:8000 (docs: /api/docs)
python -m pytest               # tests (creates and drops throwaway databases)
ruff check . && mypy src scripts test app.py main.py
```
