# AI Research & Innovation Lab

Autonomous 24/7 AI research lab: it discovers innovation opportunities, investigates them with specialised agents, red-teams and independently verifies the evidence, and reports only well-supported recommendations.

**Status:** Phase 1 (foundation) in progress. A web dashboard and API provide login, user administration, health checks, system status and a live audit log; no research functionality exists yet. See [the implementation plan](docs/IMPLEMENTATION_PLAN.md) for the real state.

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
python -m scripts.create_user --username admin --role admin   # prompts for a password
python main.py                 # API on http://127.0.0.1:8000 (docs: /api/docs)
python -m pytest               # tests (creates and drops throwaway databases)
python -m scripts.verify_events  # check the audit log's hash chain
ruff check . && mypy src scripts test app.py main.py
```

## API (current)
Interactive docs at `/api/docs`. State-changing requests must send the `X-Lab-CSRF: 1` header.

| Endpoint | Access |
|---|---|
| `GET /api/health/live`, `GET /api/health/ready` | public (probes) |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | login / any user |
| `GET /api/events`, `GET /api/events/latest`, `GET /api/events/stream` (SSE) | viewer and above |
| `GET /api/system/status` (CPU, RAM, disks, GPU, database, audit log) | viewer and above |
| `GET /api/users`, `POST /api/users` | admin |

## Dashboard (frontend/)
React + TypeScript + Vite + Tailwind, served in production by unprivileged nginx (`frontend/nginx.conf.template`).

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm run lint && npm run typecheck && npm run build
npx playwright install chromium   # once
npm run e2e                    # browser tests: real API + production nginx config, desktop and phone
```
