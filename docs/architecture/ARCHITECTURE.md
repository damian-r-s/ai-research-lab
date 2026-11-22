# AI Research & Innovation Lab — Technical Architecture

**Status:** Draft v0.4, awaiting approval · **Date:** 2026-11-22
**Governing spec:** [AI_RESEARCH_LAB_SPEC.md](../../AI_RESEARCH_LAB_SPEC.md)
**Related:** [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) · [DECISIONS.md](../DECISIONS.md)

This document describes the target design. Nothing here is implemented unless
`IMPLEMENTATION_PLAN.md` says so. Statements about third-party behaviour (LangGraph, the job library,
inference servers) are design assumptions. Each one is listed in §16 and must be confirmed by the
Phase 2 evaluation spike before anything depends on it.

### Changes from earlier drafts
| Earlier | Now |
|---|---|
| Custom workflow engine on a PostgreSQL task queue | **LangGraph** orchestrates the workflows; PostgreSQL checkpointer for workflow state |
| One custom durable queue for all work | Three separate concerns: workflow checkpointing (LangGraph), domain persistence (our schema) and run dispatch (an established library, chosen after a requirements review) |
| Laptop as the reference host | **Home server is production**; the laptop is the dev machine |
| Polish UI and reports | **English-only system**; non-English user input is accepted and normalised to English |
| Install Docker | Reuse the server's existing container platform; coexist with the project already hosted there |
| (v0.2) Docker Compose on the server | **k3s on the server** (verified: installed directly on the bare-metal Tower T2); own namespaces, podman-built images imported into k3s |
| (v0.2) Next.js frontend, `backend/src/lab` layout | **v0.3: repository structure mirrors `agentic-trading-intelligence-platform`**: root `app.py`, `src/` package, `k8s/`, React + Vite + nginx frontend |

---

## 0. Starting Point (inspected 2026-11-22)

### Repository
- Contents: the spec, the v0.1 docs and an unexecuted Phase 1 draft under `backend/` and `scripts/`
  (see DECISIONS D-12). No git repository.

### Development laptop (not the production host)
Dell Pro Max 14 MC14250 · Intel Core Ultra 7 265H (16 threads) · 30 GiB RAM · NVIDIA RTX PRO 500
Blackwell Laptop 6 GB VRAM · 937 GB NVMe · Ubuntu 26.04 · no Docker · sudo needs a password.
A user-space toolchain exists (micromamba env `ai-lab`: Python 3.13, PostgreSQL 17 + pgvector,
Node 22). It is enough for unit, component and most integration tests.

### Production server `alpha` (inspected read-only on 2026-11-22)
Full details: [SERVER_ASSESSMENT.md](../operations/SERVER_ASSESSMENT.md).

| Fact (verified on the server) | Consequence for the lab |
|---|---|
| **Bare-metal Dell Pro Max Tower T2**, Intel Core Ultra 5 245K (14 cores), 32 GB RAM (25 GiB available), 1 TB NVMe, Ubuntu 26.04, 63 days uptime | this is the spec's machine; there is no VM layer |
| **No discrete GPU** (Intel iGPU only) | inference is CPU-only for now; the future GPU goes into this tower's PCIe slot (spec §11 checks still apply) |
| Root LVM volume only 98 GB with **48 GB free**; **850 GB unallocated** in the volume group | lab data goes on a dedicated LV via static local PVs (D-17) |
| **k3s v1.36.4 installed directly on the host** with default settings: single node, Traefik, local-path storage, **NetworkPolicy enforcement enabled** | deploy to k3s in own namespaces; isolation policies will be enforced |
| The other project runs in `default` (NodePorts 30000, 30080, 30432) and uses about 2 GiB requests / 4.1 GiB limits | plenty of headroom; the lab must never touch `default` |
| Images are built with podman and imported into k3s containerd (`localhost/…:local`) | the lab uses the same path with git-SHA tags |
| A **self-hosted GitHub Actions runner is installed** (as `damian`, with cluster-admin) but registered only to the other repository, and **no workflows exist** | the lab gets its own runner with a namespace-scoped identity (D-07b) |
| Host Ollama 0.34.0, `llama3.2:3b` only, listening on all interfaces | sharing is possible; a lab-owned Ollama is cleaner (D-15) |
| Tailscale is running on the server and the laptop | dashboard access via the tailnet (U-9) |
| The node's registered IP (`.160`) no longer matches the host's interfaces (`.150`, `.158`) | the lab never hard-codes host IPs; in-cluster Services only |


---

## 1. Architectural Principles

1. **Use established frameworks for generic problems, own the domain.** LangGraph runs the
   workflows. Established libraries handle job dispatch, HTTP and migrations. Our code is the
   research methodology, agent roles, evidence model, scoring and reporting.
2. **Three separate persistence concerns** (§7):
   1. workflow checkpoints, i.e. *where a graph run is* (owned by LangGraph);
   2. the domain record, i.e. *what the lab knows and decided* (owned by us);
   3. dispatch and messaging, i.e. *who runs what, and who gets told* (minimal; a broker only
      when real requirements appear).
3. **The domain record is the source of truth.** Checkpoints exist so a run can resume. Reports,
   audit and the dashboard read the domain tables, never checkpoint blobs.
4. **The LLM proposes; code and graph structure decide.** Stage transitions, budgets, independence
   rules and acceptance are encoded in graph edges, guards and the database. Model output is data
   that must pass a schema (Pydantic) and business rules.
5. **Every agent invocation is a recorded task** with the fields required by spec §4.2: id, goal,
   context, inputs, deadline, token, cost and attempt limits, completion criteria, status, result,
   sources and errors.
6. **Independence is enforced structurally, not by prompt.** Database constraints stop a
   producer from verifying its own claims. The model router excludes the producer's model for
   critic and verifier roles.
7. **Untrusted content is data, never instructions.** Fetched material is wrapped and labelled with
   its provenance. The lab has no tools with external side effects (payments, messaging,
   publishing), so spec §15 holds by construction.
8. **Agents may only cite sources that are already in the database.** A claim references a stored
   `source_id`. Code checks that the quoted excerpt actually appears in the stored snapshot.
9. **Hardware-specific settings live in configuration, never in business logic.** This covers
   GPU slots, model placement, concurrency and resource limits. Scaling means adding processes,
   inference servers or hosts.
10. **Nothing is silently overwritten.** The audit log is append-only and hash-chained. Documents,
    hypotheses and role, workflow and scoring definitions are versioned.
11. **Failures are explicit.** An unavailable model, missing evidence or an exhausted budget each has
    its own visible status. None of them is ever turned into a "successful" result.
12. **English-only.** See §11.

---

## 2. Revised Technology Stack

| Concern | Choice | Notes |
|---|---|---|
| Language (backend) | Python 3.13 | same version in dev and in containers |
| API | FastAPI, Pydantic v2, REST + Server-Sent Events | OpenAPI docs in English |
| Workflow orchestration | **LangGraph** (open-source library) | `StateGraph`, conditional edges, `Send` fan-out, subgraphs, `interrupt()` human-in-the-loop |
| Workflow persistence | **LangGraph PostgreSQL checkpointer** (`langgraph-checkpoint-postgres`) | separate `langgraph` schema in the lab's own PostgreSQL |
| Domain database | PostgreSQL 17 + pgvector (`pgvector/pgvector:pg17` image) | separate `lab` schema; SQLAlchemy 2 + Alembic (migrations under `src/core/databases/migrations`, as in the sibling project's layout) |
| Run dispatch and scheduling | **PostgreSQL-backed job library; Procrastinate recommended** (D-03) | no new infrastructure; a broker only when triggers in §8.3 occur |
| Live UI updates | PostgreSQL `LISTEN/NOTIFY` + SSE; LangGraph streaming for in-run progress | no broker required |
| Model access | Our `ModelGateway` (routing, budgets, accounting) over provider adapters | adapters may wrap LangChain chat-model integrations (D-04) |
| Local inference | **Ollama first** (D-05): the host's existing Ollama or a lab-owned Ollama Deployment (D-15); vLLM or llama.cpp server later on the GPU | reached over HTTP; endpoints are configuration |
| External models | Adapter interface built in Phase 3; providers enabled later only with an approved budget and privacy class (D-05) | disabled by default |
| Frontend | **React + TypeScript + Vite**, Tailwind, React Query, Zustand, React Router; production build served by **nginx**, which proxies `/api` | same stack as the sibling project; English UI; SSE proxied with buffering off |
| Packaging and runtime | OCI images built with **podman** on the server and imported into **k3s**; Kubernetes manifests in `k8s/` | own namespaces `ai-lab` / `ai-lab-staging`, §13 |
| Scheduled work | **Kubernetes CronJobs** (`concurrencyPolicy: Forbid`) that only enqueue jobs | same pattern as the sibling project's workers |
| Observability | structlog JSON logs, Prometheus metrics endpoint, OpenTelemetry-ready | external tracing SaaS (LangSmith) off by default (D-08) |
| Tests | pytest, pytest-asyncio, httpx; Playwright for UI smoke tests (later) | real PostgreSQL, never a mocked DB |

**Not included, and why:**
- **Redis:** not required for checkpointing, dispatch or events at this scale. It can be added later
  as a cache or rate limiter.
- **A custom queue or orchestration framework:** replaced by LangGraph plus an established job
  library.
- **Temporal:** stronger durability guarantees, but it duplicates LangGraph's role and adds heavy
  infrastructure.
- **Next.js:** the spec lists it as a preference. React + Vite matches the sibling project and
  needs no Node server at runtime (static files behind nginx).
- **Docker Compose in production:** the server runs k3s.

---

## 3. Components and Process Topology

### 3.1. Spec components (§3) mapped to modules

| # | Spec component | Implementation | Phase |
|---|---|---|---|
| 1 | Core Orchestrator | LangGraph graphs (`src/agents/graphs`) + run lifecycle (`src/runs`) | 2 |
| 2 | Agent Runtime | `src/agents` (role registry, node factory, tool gateway) inside `runner` pods | 2–4 |
| 3 | Research Engine | `src/agents/graphs/research.py` (top-level graph) | 4 |
| 4 | Discovery Engine | `src/agents/graphs/discovery.py` + discovery CronJob | 4, 7 |
| 5 | Idea Generation | `src/research/hypotheses.py` + hypothesis node | 4 |
| 6 | Evidence Engine | `src/research/evidence.py` + `src/sources` | 4 |
| 7 | Market Intelligence | dimension `market` in the deep-research subgraph | 4–5 |
| 8 | Scientific Research | `src/sources/adapters/{openalex,arxiv,crossref,…}` + dimension `science` | 5 |
| 9 | Patent Intelligence | `src/sources/adapters/{epo_ops,uspto,…}` + dimension `patents` | 5 |
| 10 | Technical Feasibility | dimension `feasibility` | 4 |
| 11 | Financial Analysis | dimension `finance` | 5 |
| 12 | Red Team Engine | `src/agents/graphs/red_team.py` (critic roles) + `src/research/red_team.py` | 4 |
| 13 | Independent Review | `src/agents/graphs/verification.py` (verifier roles) + `src/research/verification.py` | 4 |
| 14 | Scoring Engine | `src/research/scoring.py` (deterministic aggregation + agent rationale) | 4 |
| 15 | Report Generator | `src/documents` + `src/research/reports.py` | 4 |
| 16 | Knowledge Base | `src/retrieval` (pgvector, similarity, freshness) | 4–5 |
| 17 | Audit & Event System | `src/core/events` | 1 |
| 18 | Web Dashboard | `frontend/` | 1, 8 |
| 19 | Resource Manager | `src/resources` (probes, capacity, model placement) | 1 (probes), 3, 7 |
| 20 | Security Layer | `src/core/security.py`, `src/api/auth.py`, tool gateway, egress control, secrets | 1, 4 |

### 3.2. Runtime workloads (k3s namespace `ai-lab`)

```
 browser (desktop / phone) ──HTTPS via Tailscale (D-06)──┐
                                                         ▼
 ┌─────────────────────────── namespace ai-lab ──────────────────────────────────┐
 │  frontend (Deployment: nginx + React build) ── /api ──► api (Deployment)       │
 │                                                    REST + SSE, no research work │
 │                                                            │ enqueue / control  │
 │                                                            ▼                    │
 │  postgres (StatefulSet, pgvector, local-path PVC)                               │
 │    schemas: lab (domain + audit) · langgraph (checkpoints) · jobs (dispatch)    │
 │        ▲ claim jobs, heartbeats      ▲ checkpoints          ▲ LISTEN/NOTIFY      │
 │  runner (Deployment, replicas = N)   │                      │                    │
 │    LangGraph graph.ainvoke(thread)───┘                      │                    │
 │    ├─ agent nodes → ModelGateway ──HTTP──► Ollama (host service or lab Deployment) │
 │    └─ tool nodes  → ToolGateway  ──HTTP──► source APIs (allowlisted egress)      │
 │  CronJobs: discovery-tick, refresh-tick, report-tick, backup (enqueue only)      │
 │  ResourceQuota + LimitRange + NetworkPolicy (default deny)                       │
 └──────────────────────────────────────────────────────────────────────────────────┘
   namespace default: the existing trading project. Never referenced or modified.
```

- **api** is stateless and can scale out with `replicas`. It writes intent (start, pause, stop,
  approve) in one transaction together with an audit event and, where needed, a dispatch job.
- **runner** executes LangGraph runs claimed from the job queue. Scaling means raising
  `replicas`, or later scheduling runners on more k3s nodes. Runners hold no hardware assumptions:
  inference happens behind HTTP endpoints.
- **CronJobs** replace a scheduler process. Each one only enqueues an idempotent job (for example
  `schedule:discovery:2026-11-22T06`), mirroring how the sibling project runs its workers as
  CronJobs.
- **Inference** runs outside the lab's code paths: the host's Ollama, a lab-owned Ollama
  Deployment, or later vLLM requesting the GPU (`nvidia.com/gpu`).

---

## 4. Communication Between Components

| Path | Mechanism | Guarantees / notes |
|---|---|---|
| web → api | HTTPS REST (JSON), cookie session, CSRF header | Pydantic validation on every endpoint |
| api → web (live) | SSE `GET /api/events/stream` | resumes from `Last-Event-ID` = audit `seq`; source is the table, so nothing is lost |
| api → runner (start/resume a run) | dispatch job in PostgreSQL, enqueued **in the same transaction** as the domain change | no dual-write problem; the job library handles retries and locking |
| api → running graph (pause/stop/approve) | control fields on `research_runs` + `NOTIFY lab_control`; approvals via LangGraph `Command(resume=…)` dispatched as a resume job | see §6.6 |
| runner ↔ workflow state | LangGraph checkpointer (PostgreSQL) | checkpoint per super-step; resume by `thread_id` |
| runner → domain record | SQL transactions from node code, idempotency keys | §7.2 |
| runner → models | HTTP via `ModelGateway` | timeouts, retries, fallback, budget reservation |
| runner → external sources | HTTP via `ToolGateway` | allowlist, rate limits, GET-only fetching, provenance tagging |
| any → audit log | `events` table insert in the caller's transaction + `NOTIFY lab_events` | NOTIFY is a wake-up hint only; consumers re-read the table |

**Agents never message each other directly.** Collaboration is expressed in the graph: one node's
validated output, stored in the domain tables and referenced in graph state, is the next node's
input. This rules out uncontrolled conversations (spec §4.2, §14). Every loop has an explicit
bound, and LangGraph's recursion limit is an additional backstop.

---

## 5. Directory and Module Structure

The layout **mirrors `agentic-trading-intelligence-platform`**. It has a root `app.py`, a `src/`
package imported as `src.*`, `core/databases/{migrations,repositories}`, LangGraph code under
`src/agents`, process entry points in `src/services`, Kubernetes manifests in `k8s/`, a
Vite/React frontend served by nginx, and tests in `test/`. It extends that layout only where the
lab needs more (configuration-driven roles, the model layer, sources, the research domain).

```
ai-research-lab/
├── AI_RESEARCH_LAB_SPEC.md
├── README.md
├── app.py                    # FastAPI app: `uvicorn app:app` (as in the sibling project)
├── main.py                   # local dev entry point (uvicorn with reload)
├── Dockerfile                # one backend image, reused by api, runner, CronJobs and the migrate init container
├── requirements.txt          # pinned dependencies (generated from requirements.in)
├── requirements.in
├── requirements-dev.in/.txt  # test and lint tools
├── pyproject.toml            # tool configuration only (ruff, mypy)
├── pytest.ini
├── .env.example              # template only; .env is git-ignored
├── .dockerignore / .gitignore
├── config/                   # non-secret, versioned configuration
│   ├── base.toml             # hardware-independent defaults
│   ├── environments/         # dev.toml, staging.toml, prod.toml (endpoints, limits, hardware profile)
│   ├── roles/*.yaml          # agent role definitions (English prompts)
│   ├── workflows/*.yaml      # loop bounds, dimensions, gates
│   ├── scoring/*.yaml        # scoring schemes and weights (versioned)
│   └── models.yaml           # providers, models, capabilities, prices (with verification date)
├── src/
│   ├── api/                  # FastAPI routers: auth, system, events, projects, runs, approvals, reports
│   ├── runs/                 # run lifecycle: start/pause/resume/stop, kill switch, dispatch jobs (framework-free)
│   ├── agents/               # the ONLY package that imports LangGraph
│   │   ├── graphs/           # research.py, discovery.py, red_team.py, verification.py, synthesis.py
│   │   ├── state.py          # graph state types (references, not documents)
│   │   ├── nodes/            # node functions: agent_node factory, guards, gates
│   │   ├── roles.py          # role registry: loads and validates config/roles/*.yaml
│   │   ├── prompts.py        # prompt assembly (trusted vs. untrusted blocks)
│   │   └── tools/            # tool gateway + tool implementations
│   ├── core/
│   │   ├── config.py, logging.py, security.py, ids.py, clock.py, errors.py, language.py (F4)
│   │   ├── auth/             # login, sessions, users (use cases) + auth event types
│   │   ├── events/           # audit log: registry, store, hash chain, notifier, system event types
│   │   └── databases/
│   │       ├── base.py, database.py   # ORM base; engine and session factory
│   │       ├── models/       # ORM tables (users, events, …)
│   │       ├── migrate.py, health.py  # programmatic Alembic; readiness check
│   │       ├── migrations/   # Alembic environment + versions
│   │       └── repositories/ # one repository per aggregate (users_repo, runs_repo, hypotheses_repo, …)
│   ├── llm/                  # model gateway, router, accounting, call cache, providers/ (ollama, openai_compatible, …)
│   ├── sources/              # source adapters (openalex, arxiv, crossref, …), snapshots, rate limits, egress
│   ├── research/             # domain services, framework-free: hypotheses, evidence, verification, red_team, scoring, decisions, reports
│   ├── retrieval/            # embeddings, similarity, dedup, freshness (named as in the sibling project)
│   ├── documents/            # versioned documents, diff, Markdown/PDF export
│   ├── resources/            # hardware probes, capacity, model placement
│   └── services/             # process entry points: runner.py, enqueue_tick.py, backup.py (like the sibling's *_worker.py)
├── test/                     # unit/, integration/, workflow/, system/ (system tests need the cluster)
├── frontend/                 # React + TypeScript + Vite + Tailwind
│   ├── src/{api,components,pages,store}/
│   ├── Dockerfile            # node build stage → nginx
│   └── nginx.conf            # serves the build, proxies /api to the api Service (SSE: buffering off)
├── k8s/
│   ├── README.md             # build, import, apply, verify (same shape as the sibling's)
│   ├── namespace.yaml        # ai-lab (+ ai-lab-staging via overlay)
│   ├── quota.yaml            # ResourceQuota + LimitRange sized from the server assessment
│   ├── networkpolicy.yaml    # default deny; allow intra-namespace + required egress
│   ├── configmap.yaml, secret.example.yaml
│   ├── postgres.yaml, api.yaml, runner.yaml, frontend.yaml
│   ├── cronjobs/*.yaml       # discovery, refresh, reports, backup
│   └── ollama.yaml           # only if a lab-owned Ollama is chosen (D-15)
├── scripts/
│   ├── create_user.py        # `kubectl exec … python -m scripts.create_user` (as in the sibling)
│   ├── migrate.py            # `python -m scripts.migrate upgrade` (also the k3s init container)
│   ├── deploy.sh             # on the server: git checkout <sha> → podman build → k3s import → kubectl apply → rollout status
│   ├── dev-db.sh, env.sh     # laptop toolchain (micromamba, no root)
│   └── server/               # inspect.sh (read-only server inspection)
├── grafana/                  # Phase 7 (optional dashboards, as in the sibling)
└── docs/
    ├── architecture/ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, DECISIONS.md
    └── operations/           # SERVER_ASSESSMENT.md, runbooks
```

**Where the lab deliberately differs from the sibling project:**

| Topic | Sibling project | Lab | Reason |
|---|---|---|---|
| Migrations | yoyo SQL files | Alembic, same directory | tested up/down migrations; ORM drift check (criterion 1.2) |
| DB driver | psycopg2, sync pool | psycopg 3 (async) + SQLAlchemy 2 | async runners and LISTEN/NOTIFY |
| LangGraph state | in-memory, no checkpointer | PostgreSQL checkpointer | runs must survive restarts |
| Namespace | `default` | `ai-lab`, `ai-lab-staging` | isolation from the other project |
| Image tags | `:local` | git SHA | traceable deploys and rollback |
| Exposure | NodePorts on the LAN, including PostgreSQL | no NodePorts for internal services; dashboard via Tailscale (D-06) | least exposure |

**Module boundary rules (enforced with import-linter from Phase 2):**
- Only `src/agents` imports LangGraph. `research`, `retrieval`, `documents` and `sources` stay
  framework-free and testable without it.
- `src/agents` turns graph positions into calls to domain services. It holds no research logic
  of its own beyond orchestration.
- `src/api` contains only HTTP concerns and delegates to services and repositories.
- Packages are created in their phase; no empty placeholders.

---

## 6. LangGraph Integration

### 6.1. What LangGraph owns vs. what we own

| LangGraph (framework) | Lab code (domain) |
|---|---|
| Graph execution, super-steps, conditional routing | research methodology (stages, gates, criteria) |
| Parallel fan-out (`Send`) and joining | which dimensions apply to a hypothesis |
| Checkpointing and resume per `thread_id` | domain record of results, claims, sources, decisions |
| `interrupt()` / `Command(resume=…)` for human-in-the-loop | approval policies (when to ask the user) |
| Streaming of node progress | audit events, cost accounting, dashboard views |
| Recursion limit (backstop) | explicit loop bounds, budgets, timeouts |

### 6.2. Graph design

- **Top-level research graph (one thread per research run):**
  `intake → discovery → hypothesis_generation → screening → [per hypothesis: deep_research] →
  red_team → verification → (bounded rework) → scoring → synthesis → final_review → decision`
- **Subgraphs:** discovery, deep research (fans out per dimension with `Send`: problem, market,
  competition, existing solutions, science, patents, technology, feasibility, costs, revenue models,
  regulation, risk), red team, verification, report synthesis. Each subgraph can be tested on its own.
- **State holds references, not documents.** It contains IDs of domain rows (hypotheses, analyses,
  claims, documents), control data (stage, counters, budget snapshot) and small summaries.
  Checkpoints stay small, and the domain record stays authoritative.
- **Rework loops are bounded** by values in `config/workflows/*.yaml` (for example
  `max_rework_cycles: 2`). The edge function checks the counter explicitly. The recursion limit is
  only a backstop.
- **Negative outcomes are first-class:** `reject`, `further_research` and
  `withhold_insufficient_data` are normal terminal routes. No route forces a positive result.

### 6.3. Agent nodes

A generic node factory, `agent_node(role_key)`, produces the node function:
1. Load the role definition at its current version hash. Resolve its inputs from domain tables
   using the IDs in state.
2. Open or reuse an `agent_tasks` record with a deterministic idempotency key (§7.2). If a
   completed record exists (the node is re-executing after a crash), return its stored result
   without calling any model again.
3. Assemble the prompt: role instructions, then trusted context, then untrusted material in labelled
   data blocks.
4. Run a bounded tool loop through `ModelGateway` and `ToolGateway`, within the iteration, token,
   cost and time limits.
5. Validate the output against the role's output schema and the code rules (for example, every
   cited `source_id` exists and every excerpt is found in its snapshot). Allow one repair attempt,
   then fail explicitly.
6. In one transaction: persist the domain results, close `agent_tasks` and append audit events.
   Return only references to the graph state.

Prebuilt LangGraph agent helpers may be used *inside* step 4 if the Phase 2 spike shows they fit
our accounting and limits. Otherwise the tool loop is a few dozen lines in `src/agents`.

### 6.4. Role definitions (`config/roles/<key>.yaml`)
Fields follow spec §4.1:

| Field | Content |
|---|---|
| `key`, `name` | identifier and display name |
| `responsibility`, `competencies` | what the role does and knows |
| `system_prompt` | template file, in English |
| `input_schema`, `output_schema` | registered Pydantic models |
| `allowed_tools` | tools the role may call |
| `constraints`, `quality_criteria` | rules for the output |
| `model_policy` | capabilities, tier, `must_differ_from_roles` |
| `limits` | tokens, cost, time, tool iterations |
| `independence_class` | `producer`, `critic`, `verifier` or `coordinator` |

Definitions are validated at start-up, and their content hash is recorded on every task. Adding a
role means adding YAML (and an output schema if a new shape is needed), with no engine changes. The
design targets 40–60+ roles. The number of roles does not affect the number of processes.

### 6.5. Human-in-the-loop
`interrupt()` is used at policy-defined points:
- budget extension requests;
- the user's accept or reject of a recommendation;
- optional review gates per project;
- any step that would send `confidential` data to an external provider.

The interrupt payload is stored in the domain table `approvals` and shown in the dashboard. The
user's answer resumes the thread with `Command(resume=…)` through a resume job. Interrupted runs
hold no runner while they wait.

### 6.6. Pause, stop and the global kill switch (spec §8.5: control must be real)

| Action | Mechanism | Resulting status |
|---|---|---|
| **Pause** (graceful) | `research_runs.pause_requested`; a guard checked at the entry of every node calls `interrupt()`, so the graph suspends at a clean boundary with its checkpoint saved | `paused` |
| **Resume** | enqueue a resume job for the thread | continues from the last checkpoint |
| **Stop** (hard) | `cancel_requested` + `NOTIFY lab_control` → the runner cancels the asyncio task running `graph.ainvoke`; in-flight HTTP calls to models and sources are cancelled with it | `cancel_requested`, then `stopped` once the runner confirms (or once its heartbeat expires because it is dead) |
| **Kill switch** | `system_state.kill_switch` stored in the database (survives restarts) | runners cancel all runs, dispatch is refused, CronJob ticks enqueue nothing |

The dashboard shows `cancel_requested` and `stopped` as different states.

**Target:** a running node stops within 10 s. This is measured in Phase 2 tests.

### 6.7. Durability semantics on resume
LangGraph checkpoints at super-step boundaries. A node that was running when a process died will
**re-execute from its start** on resume. Our design makes that safe:
- Agent nodes are idempotent through `agent_tasks` keys (§6.3 step 2).
- Model calls are cached by `(agent_task, call_seq)`, so a re-executed node never pays twice for a
  completed call.
- Writes to the domain record are idempotent upserts keyed by task.
- The interrupted attempt is recorded as `abandoned` on `agent_task_attempts`. It is never silently
  erased.

Whether LangGraph preserves the completed parallel branches of a partially failed super-step, and
how this interacts with interrupts, is an assumption that must be verified (A-3 in §16).

---

## 7. Persistence and Messaging Architecture

### 7.1. Three concerns

| Concern | Question it answers | Owner | Store | Retention |
|---|---|---|---|---|
| **Workflow checkpointing** | "Where is run X, and what is its in-flight state?" | LangGraph | PostgreSQL schema `langgraph` (checkpointer tables) | full history while active; pruned to the final checkpoint N days after the run ends (configurable) |
| **Domain persistence** | "What does the lab know, what did each agent produce, what was decided and why, what did it cost?" | Lab | PostgreSQL schema `lab` | permanent; append-only audit; versioned documents |
| **Dispatch and messaging** | "Which process should execute run X now? What runs periodically? Who must be notified of a change?" | job library + LISTEN/NOTIFY | PostgreSQL schema `jobs` | completed jobs pruned after N days |

All three can share **one lab-owned PostgreSQL instance** in separate schemas, with separate
database roles where the libraries allow it. One backup then captures a consistent state of
everything. Each concern can move to its own instance later without code changes (its connection
string is configuration).

### 7.2. Domain record and its link to checkpoints
- `research_runs` row ↔ LangGraph `thread_id` (1:1).
- `agent_tasks` row per agent invocation. This is the spec §4.2 task record and the basis for
  the dashboard's agents view and cost accounting. It is **not** a queue.
- Idempotency key: `{thread_id}:{node_path}:{fanout_key}` (for example
  `run_42:deep_research/market:hyp_17`). A unique constraint guarantees one logical task per graph
  position. Re-executions add rows to `agent_task_attempts`.
- The dashboard, reports and history never parse checkpoints. If the checkpoint store is lost,
  in-flight runs cannot resume, but **no research results or history are lost**. Those runs are
  marked `failed: checkpoint_lost` and can be restarted, reusing completed `agent_tasks`.

### 7.3. Messaging requirements review (performed for this revision)

| Candidate requirement | Present in this system? | Satisfied by |
|---|---|---|
| Asynchronous agent-to-agent communication | **No.** Agents communicate through graph state by design (§4) | LangGraph |
| Distributed workers | **Yes, modestly.** Several runner processes, possibly on more than one host later; GPU work goes to inference servers over HTTP | job library (claims with locks, retries); inference servers have their own request queues |
| Task dispatch | **Yes.** Start or resume a run, plus periodic jobs | job library |
| Event delivery to the UI | **Yes.** One consumer type (dashboard via api) | audit table + LISTEN/NOTIFY + SSE; LangGraph streaming for live progress |
| Integration between independent services | **No.** There is no executive system or external consumer (out of scope by spec) | — |
| Backpressure and workload management | **Yes.** Limits on concurrent runs, per-model concurrency, budgets | job-library queue concurrency + run admission control + per-model semaphores in `ModelGateway` |
| High-throughput streaming | **No.** Expected volume is tens to hundreds of jobs per hour, not per second | — |

**Conclusion:** none of the current requirements needs a dedicated message broker. A
PostgreSQL-backed job library covers dispatch, retries, locking, periodic jobs and cancellation
without new infrastructure, and it enqueues in the same transaction as the domain change.
**Recommendation (D-03): Procrastinate**, to be confirmed by the Phase 2 spike. Alternatives are
in DECISIONS.md.

### 7.4. When to introduce a broker (explicit triggers)
Reconsider a broker (NATS JetStream or RabbitMQ; Redis Streams only if Redis is introduced anyway)
when **any** of these becomes true:
1. Independent services outside this codebase must consume lab events.
2. Several consumer types need fan-out of the same event stream with independent offsets.
3. Runners on several hosts cannot reach PostgreSQL with acceptable latency, or connection counts
   become a bottleneck that PgBouncer does not solve.
4. Job throughput exceeds what the job library sustains on PostgreSQL in load tests.

The `runs` service hides dispatch behind an interface, so changing transport does not touch
workflows or domain code.

### 7.5. Domain schema (target; created by phase)

Conventions:
- UUIDv7 primary keys, plus human-readable codes (`PRJ-0001`, `HYP-0042`).
- `timestamptz` in UTC.
- Statuses as `text` + `CHECK` (easier to migrate than enums), with **English** values.
- `jsonb` only for variable-shaped data, validated by Pydantic.
- `version` column (optimistic locking) on mutable tables.

**Foundation (Phase 1)**
```
users(id, username, password_hash, role[admin|researcher|viewer], is_active, created_at, updated_at, last_login_at)
auth_sessions(id, user_id, token_hash, created_at, expires_at, last_seen_at, revoked_at, ip, user_agent)
login_attempts(id, username, ip, succeeded, attempted_at)
events(seq, id, type, type_version, occurred_at, recorded_at, actor_type[user|agent|system], actor_id,
       aggregate_type, aggregate_id, correlation_id, causation_id, payload, prev_hash, hash)   -- append-only
system_state(singleton: kill_switch, reason, changed_by, changed_at)
```

**Runs and agent tasks (Phase 2)**
```
research_runs(id, project_id, thread_id UNIQUE, graph_key, graph_version, status
              [queued|running|paused|awaiting_approval|cancel_requested|stopped|completed|failed],
              pause_requested, cancel_requested_at, stop_reason, runner_id, heartbeat_at,
              started_at, ended_at, budget_id)
agent_tasks(id, run_id, idempotency_key UNIQUE, node_path, role_key, role_version_hash, goal,
            input_refs, limits{timeout_s,max_tokens,max_cost,max_attempts}, completion_criteria,
            status[pending|running|succeeded|failed|cancelled|blocked_budget], result_refs,
            source_ids, error, tokens_in, tokens_out, cost_external, compute_seconds, created_at, ended_at)
agent_task_attempts(id, agent_task_id, attempt_no, runner_id, started_at, ended_at,
            outcome[succeeded|failed|timed_out|cancelled|abandoned], error)
approvals(id, run_id, kind, request_payload, status[pending|approved|rejected|expired], decided_by, decided_at, response)
budgets(id, scope_type[global_day|global_month|project|run|model|role], scope_id, period_start,
        limit_tokens, limit_cost, limit_seconds, limit_attempts, limit_parallel)
budget_ledger(id, budget_id, ref_type, ref_id, entry[reserve|commit|release], tokens, cost, created_at)  -- append-only
runners(id, hostname, pid, started_at, heartbeat_at, status, capabilities)
```

**Model layer (Phase 3)**
```
model_providers(key, kind[ollama|openai_compatible|anthropic|…], base_url, is_local, secret_ref, enabled)
models(key, provider_key, model_name, capabilities[], context_window, price_in_per_mtok, price_out_per_mtok,
       currency, price_source, price_verified_at, max_concurrency, max_privacy_class, enabled)
llm_calls(id, agent_task_id, attempt_id, call_seq, role_key, model_key, request_hash, status, error_class,
          fallback_from_call_id, started_at, ended_at, latency_ms, tokens_in, tokens_out,
          token_source[provider|estimated], cost_external, compute_seconds, gpu_seconds, response_ref)
          UNIQUE(agent_task_id, call_seq)
```

**Research domain (Phases 4–5)**
```
research_projects(id, code, title, description, status, stage, priority, created_by, created_at, budget_id, version)
user_inputs(id, project_id, original_text, detected_language, english_text, normalised_by_model, created_at)
discoveries(id, project_id, title, description, signal_type, rationale, agent_task_id)
hypotheses(id, code, project_id, discovery_id, parent_id, revision, problem, solution, target_group,
           applications, novelty_prelim, feasibility_prelim, unknowns, inspiration_source_ids,
           status[proposed|screened_out|screened_in|under_research|recommended|further_research|rejected|withheld],
           screening_rationale, agent_task_id)
analyses(id, hypothesis_id, dimension, agent_task_id, document_version_id, status, confidence)
claims(id, hypothesis_id, analysis_id, text, importance, value_numeric, unit,
       value_status[sourced|estimate|assumption|model_scenario], produced_by_task, produced_by_role, produced_by_model)
sources(id, canonical_url, title, publisher, source_type, external_ids, published_at, first_retrieved_at,
        adapter_key, reliability_score, reliability_rationale, availability[available|paywalled|removed|error], stale_after)
source_snapshots(id, source_id, retrieved_at, http_status, content_hash, blob_ref)          -- immutable
evidence_links(id, claim_id, source_id, snapshot_id, excerpt, locator,
               relation[supports|partially_supports|contradicts|context], excerpt_verified, agent_task_id)
verifications(id, claim_id, verdict[confirmed|partially_confirmed|unconfirmed|contradicted|needs_research],
              issues[no_source|same_source_duplicate|stale|conflict|miscitation|overreach], rationale,
              verifier_task_id, verifier_role, verifier_model)
              -- trigger: verifier task ≠ producer task; verifier role ≠ producer role; role class = verifier
red_team_findings(id, hypothesis_id, agent_task_id, question, argument, severity, evidence_link_ids,
                  status[open|addressed|accepted_risk|refuted], response)
scoring_schemes(id, key, version, criteria)                                                -- immutable versions
scores(id, hypothesis_id, scheme_id, total, confidence, per_criterion{score,rationale,confidence,missing_data}, agent_task_id)
decisions(id, project_id, hypothesis_id,
          decision[recommend|further_research|reject|withhold_insufficient_data|user_accept|user_reject],
          rationale, actor_type, actor_id, evidence, created_at)
open_questions(id, project_id, hypothesis_id, text, raised_by_task, status)
```

**Documents and knowledge (Phases 4–5)**
```
documents(id, project_id, kind, title, current_version_id)
document_versions(id, document_id, version_no, format[markdown|json], content, content_hash, parent_version_id,
                  change_summary, created_by_actor, agent_task_id, created_at)          -- immutable
document_links(document_version_id, target_type, target_id)
embedding_spaces(id, model_key, dim, is_active, created_at)
embeddings(id, space_id, owner_type, owner_id, content_hash, embedding vector)  -- partial HNSW index per space
resource_samples(ts, host, cpu, memory, gpu, disk)                               -- Phase 7, with retention
```

Changing the embedding model creates a new space plus a re-embed job. The old space serves
queries until the switch, so there is no downtime.

---

## 8. Model Layer

```
agent node ──► ModelRouter.select(role policy, task requirements, privacy class, budget, exclusions)
                  │ ordered candidates: capability → privacy → health → cost → current load
                  ▼
            ModelGateway.call(candidate): budget reservation → call cache → timeout → retry
                  │                                       └─ fallback to the next candidate
                  ▼
   ProviderAdapter (interface: chat, structured output, tool calls, embed, list_models, health)
     ├─ Ollama               (dev, small local models)
     ├─ OpenAI-compatible    (vLLM, llama.cpp server, LM Studio and many APIs)
     └─ native providers     (as approved in D-05)
```

- **Roles declare requirements, not models.** Changing a model is a configuration change.
  `must_differ_from_roles` routes critique and verification to a different model than the analysis.
  If no alternative exists, that fact is recorded in the verdict.
- **Placement is hardware-independent.** `config/models.yaml` describes models and their
  requirements, for example VRAM. Environment profiles say which inference endpoints exist and which
  GPU each one uses. The Resource Manager reports what is actually available and flags placements
  that do not fit.
- **Privacy:** every task carries a data class (`public` / `internal` / `confidential`). The router
  never sends data above a provider's `max_privacy_class`.
- **Costs:** real money (`cost_external`, from configured prices with their source and verification
  date) is kept separate from local compute (`compute_seconds`, `gpu_seconds`). Token counts come
  from the provider where available; otherwise they are marked `estimated`.
- **Budgets:**
  - Reserve the worst case (`max_tokens × price`) before the call, commit the actual cost after it
    and release the difference.
  - Hierarchy: global per day and month → project → run → task → model and role.
  - When a budget is exhausted, the task becomes `blocked_budget`, an approval interrupt is raised
    and the state is visible in the dashboard. Nothing continues without approval.
- **Errors:** typed taxonomy:
  - `ModelUnavailable`
  - `RateLimited(retry_after)`
  - `ContextTooLong`
  - `InvalidOutput`
  - `ContentFiltered`
  - `Timeout`
  - `BudgetExceeded`

  Each model has a circuit breaker. If no candidate works, the task fails explicitly.
- **Adapter implementation** (D-04): adapters may wrap LangChain chat-model integrations, which gives
  native compatibility with LangGraph tool calling. Routing, budgets, accounting and caching stay in
  `ModelGateway`, so no framework controls spend.

---

## 9. Events, Audit and Versioning

### 9.1. Audit log
- **Model:** current state lives in domain tables, and **every state change appends an audit event
  in the same transaction**. This is a hybrid, not full event sourcing. LangGraph checkpoints are
  *not* the audit log.
- **Naming:** `<domain>.<entity>.<past_tense_verb>`, all English, for example
  `auth.session.revoked`, `run.paused`, `agent_task.completed`, `llm.call.failed`,
  `budget.exceeded`, `system.kill_switch.engaged`.
- **Type registry:** every type has a versioned Pydantic payload schema and an English description.
  Emitting an unregistered type or an invalid payload is a programming error.
- **Context fields:**
  - `actor_type` / `actor_id`: who acted.
  - `aggregate_type` / `aggregate_id`: what the event is about.
  - `correlation_id`: the whole process, for example a research run or an HTTP request.
  - `causation_id`: the event that caused this one.

  Together they reconstruct spec §8.4: where an idea came from, who analysed what and why the final
  recommendation was made.
- **Immutability:** a trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`. The application database
  role has only `SELECT` and `INSERT` on this table.
- **Tamper evidence:** each hash is `SHA-256(prev_hash ‖ canonical_json(event))`, computed with
  appends serialised by an advisory lock. `python -m scripts.verify_events` recomputes the chain. The head hash is
  exported with every backup, which also detects truncation of the tail.
- **Notification:** `pg_notify('lab_events', seq)`. SSE reads the table from `Last-Event-ID`.

### 9.2. Versioning
| Artifact | Mechanism |
|---|---|
| Documents and reports | immutable `document_versions` (number, hash, parent, author, task); diff on demand; Markdown and PDF export |
| Hypotheses | a revision is a new row with `parent_id` |
| Role, workflow and scoring definitions | content hash recorded on every task and score; scoring schemes are immutable versions |
| Graphs | `graph_key` + `graph_version` on `research_runs`; a running thread finishes on the graph version it started with, or is explicitly migrated |
| Admin settings (budgets, limits) | every change is an audit event with before and after values |
| Database schema | Alembic; up and down migrations tested in CI; expand → migrate → contract in production |
| Sources | immutable snapshots with a content hash; evidence points to a specific snapshot |

---

## 10. Security

| Requirement (spec §15) | Design | Phase |
|---|---|---|
| Least privilege | app roles `admin`, `researcher`, `viewer`; per-role tool allowlists for agents; separate DB roles (migrator, app, checkpointer, jobs) | 1–4 |
| Isolation, file access | pods run as non-root with `readOnlyRootFilesystem` and dropped capabilities where possible; agents have no shell and no filesystem access; code execution only via D-09 sandbox | 1–5 |
| Secrets | Kubernetes Secrets in the `ai-lab` namespace, created from git-ignored files (`k8s/secret.yaml`, as in the sibling project); DB stores `secret_ref` names, never values | 1 |
| Logging and audit | audit log + structured JSON logs with `request_id`, `run_id`, `agent_task_id` | 1 |
| Input validation | Pydantic at every boundary (HTTP, config, model output, event payloads) | 1+ |
| Prompt injection | untrusted data in labelled blocks; no side-effecting tools; citation checks in code; regression tests | 4 |
| Backup and recovery | §14 | 1 (basic), 7 |
| Global stop | persistent kill switch (§6.6) | 2 |
| Dashboard access | sessions (argon2id, HttpOnly + SameSite=Strict cookies, CSRF header, persistent login throttling) | 1 |
| Encryption in transit | Tailscale (WireGuard) for dashboard access (D-06); internal traffic stays on the cluster network behind NetworkPolicy | 1 |
| Egress restriction | all outbound traffic through `ModelGateway` / `ToolGateway` with a domain allowlist; plus an egress NetworkPolicy on runner pods | 3–4 |
| Coexistence with the other project | own namespaces, ResourceQuota, LimitRange, NetworkPolicy, own PostgreSQL and PVCs; no shared credentials (§13.3) | 1 |

Third-party tracing services (for example LangSmith) would send prompts and research content off
the server. They are **disabled by default** (D-08).

---

## 11. Language Policy (strict requirement)

- **English everywhere the system produces output:** UI, API messages and OpenAPI docs, logs,
  errors, status values, event types and descriptions, agent prompts, inter-agent data, agent
  outputs, research reports, documents and all repository documentation.
- **Non-English input is accepted** (for example a Polish project brief or instruction). On intake:
  1. The original text is stored verbatim in `user_inputs.original_text`, with its detected language.
  2. A normalisation step produces `english_text`, recording the model that did it. Everything
     downstream uses only `english_text`.
  3. The dashboard can show the original next to the English version for transparency. All
     generated deliverables remain English.
- **Sources in other languages** may be retrieved and stored in their original language as evidence.
  Excerpts are kept verbatim for citation checking. Analyses and reports discuss them in English,
  with a translation where quoted.
- Enforcement:
  - Prompts state the English output requirement.
  - Output validation includes a language check on free-text fields; a failure triggers a repair
    attempt.
  - Tests assert English outputs for non-English inputs.

---

## 12. Server Inspection Requirements (Phase 0.5 prerequisite)

No deployment, and no capacity-dependent sizing decision, happens before a **read-only**
assessment of the production server. The result is `docs/operations/SERVER_ASSESSMENT.md`. The
facts listed in §0 come from the sibling repository's files and must be confirmed here.

**Rules:**
- Read-only commands only: `kubectl get`/`describe`/`top`, `podman images`/`ps`, `ss`,
  `nvidia-smi`, `/proc`, `df`.
- No `kubectl apply`/`delete`/`edit`/`exec` into the other project's pods, no `rollout`, no
  restarts, no image pulls or prunes, no Ollama model pulls.
- The inspection script is reviewed by the user before it runs. It never prints Secret values
  or ConfigMap values that may hold credentials (keys only).

| Area | What to collect | Purpose |
|---|---|---|
| Machine | model, CPU, RAM, swap, disks and LVM layout, uptime and power behaviour | real capacity and upgrade options |
| GPU | `lspci`, `nvidia-smi`; whether Ollama uses a GPU | local inference options |
| k3s | version, node list, allocatable vs. requested CPU/memory (`kubectl describe node`), storage classes (`local-path`), whether bundled Traefik/ServiceLB are enabled, network policy support | quotas, ingress, isolation |
| Existing project | namespaces, workloads, Services/NodePorts, PVCs and sizes, CronJob schedules, requests/limits (metadata only) | avoid collisions; never touch |
| Host Ollama | version, bind address, models present and their sizes, configured parallelism, CPU/RAM usage while the other project's CronJobs run | whether sharing is viable (D-15) |
| Storage | filesystem free space, the `local-path` provisioner directory, podman storage size, existing PVC usage | PVC sizing, backup space |
| Load | one snapshot of node CPU/RAM/disk and allocated requests and limits (done); longer-term usage comes from the lab's own resource monitoring after deployment | headroom |
| Networking | listening ports, LAN address, firewall, whether Tailscale is installed, DNS | dashboard exposure (D-06) |
| Access and security | sudo, kubeconfig location and permissions, SSH configuration, automatic updates | deploy user and permissions (D-14) |
| Backups | existing backups of the host or PVCs | where lab backups go |

**Outputs:**
1. A capacity budget for the lab (CPU, RAM, disk, inference capacity), reserving headroom for
   the existing project's peaks. This becomes `k8s/quota.yaml`.
2. A coexistence plan: namespaces, ports, quotas, network policies, Ollama sharing.
3. A list of server changes the user must approve, if any (for example extending the LVM volume,
   registering a runner, or a lab-owned Ollama).

---

## 13. Development and Deployment Workflow

### 13.1. Environments
| Environment | Where | Purpose | Data |
|---|---|---|---|
| `dev` | laptop: native processes via the micromamba toolchain (Python, PostgreSQL + pgvector, Node), Ollama optional | coding, unit, component and most integration tests | throwaway |
| `staging` | **server**: k3s namespace `ai-lab-staging`, own PostgreSQL, small quota | deploy verification, full integration, restart, stop/resume and load tests | disposable |
| `prod` | **server**: k3s namespace `ai-lab` | 24/7 operation | durable, backed up |

LLM-dependent tests on the laptop use recorded responses or a small local Ollama model.
Realistic workload tests (real models, real sources, real limits) run in `staging`.

### 13.2. Workflow (same build path as the sibling project, with traceable tags)
1. **Laptop:** develop; run `pytest` (unit, component and integration tests against the local
   PostgreSQL), frontend `npm run lint` and `npm run build`.
2. **GitHub:** push to a private repository (`git@github.com:<owner>/ai-research-lab.git`, D-07).
   An optional GitHub-hosted CI workflow runs the same tests on push; it needs no access to the
   server.
3. **Server:** `scripts/deploy.sh <git-sha> staging`, run by hand at first and later by the lab's
   own self-hosted runner (D-07b):
   1. `git fetch` and `git checkout <sha>` in a dedicated deploy checkout;
   2. `podman build` the backend and frontend images, tagged `localhost/ai-lab-api:<sha>` and
      `localhost/ai-lab-frontend:<sha>`;
   3. `podman save | sudo k3s ctr images import -`;
   4. `kubectl -n ai-lab-staging apply`, with the image tag set;
   5. migrations run in the `migrate` init container (as in the sibling project);
   6. `kubectl rollout status`.
4. Run the system test suite against `staging`: health, workflow, restart, stop/resume, budget
   limits.
5. **Promote:** `scripts/deploy.sh <git-sha> prod`. It takes a backup first; the remaining steps
   are the same.
6. **Roll back:** redeploy the previous SHA (its images stay imported). Migrations follow
   expand/contract, so the previous version runs against the new schema.
7. **CI/CD:** a self-hosted runner already exists on the server for the other repository, but runners
   registered to a personal account's repository serve only that repository. The lab needs its
   **own runner registration**, ideally under a separate low-privilege service user (D-07b).
   Workflow: on push to `main`, run tests, then `deploy.sh <sha> staging`; promotion to `prod` is a
   manual approval in GitHub.

### 13.3. Coexistence with the existing project on the same k3s cluster
- **Own namespaces** (`ai-lab`, `ai-lab-staging`). The lab never creates, edits or reads
  resources in `default`.
- **ResourceQuota + LimitRange** per namespace, sized from the assessment. Every lab pod has
  requests and limits, so lab pods cannot starve the other project's pods.
- **NetworkPolicy:** default deny for ingress into lab namespaces; only intra-namespace traffic
  and the dashboard entry point are allowed. Runner egress is limited to the Ollama endpoint and
  allowlisted source and API hosts. k3s enforces NetworkPolicy (verified: default configuration,
  SERVER_ASSESSMENT §5).
- **Own data:** a dedicated PostgreSQL StatefulSet. Lab volumes are static local PVs on a
  dedicated LV mounted at `/srv/ai-lab` (D-17), so lab growth never fills the root filesystem the
  other project depends on. The lab never uses the other project's database (or its PostgreSQL
  NodePort 30432).
- **No NodePorts for internal services** (PostgreSQL, runners). The dashboard is reached over the
  tailnet: a Traefik Ingress, or `tailscale serve` in front of the frontend Service (chosen and
  tested in Phase 1). If a NodePort is needed in the interim, it uses a range that does not collide
  with 30000, 30030, 30080 or 30432.
- **Image names prefixed** `localhost/ai-lab-*`, so imports never overwrite the other project's
  `localhost/agentic-trading-platform*:local` images.
- **Shared Ollama, if chosen (D-15):** the lab caps its own concurrency to the host Ollama and
  pulls no models there without approval.
- **No cluster-wide changes** (CRDs, cluster roles, k3s configuration, node labels) without
  approval. The only cluster-scoped objects the lab needs are its two Namespaces, its
  PersistentVolumes and the `local` StorageClass; they are created once, by the owner.
- **Least-privilege deploys:** the lab's runner and `deploy.sh` use a ServiceAccount bound only to
  the `ai-lab*` namespaces (D-07b).

---

## 14. 24/7 Operation, Monitoring and Recovery

**Autonomy (Phase 7):**
- Kubernetes CronJobs trigger discovery cycles, stale-source refreshes, resumption of interrupted
  runs, periodic reports and backups. Each CronJob only enqueues a job; runners do the work.
- Each firing has an idempotency key (`schedule:{key}:{slot}`), so a restart never doubles a cycle.
- Admission control limits concurrent runs per project and globally. The lab pauses itself when
  resources (RAM, disk, GPU temperature or VRAM) or budgets cross their thresholds.
- An idle lab is a valid state.

**Monitoring:**

| Layer | Mechanism | Phase |
|---|---|---|
| Health checks | `/health/live`, `/health/ready` (DB, migrations, checkpointer, job queue) | 1–2 |
| Resource probes | CPU, RAM, disk, GPU (NVML), stored as samples with retention | 1 / 7 |
| Logs | JSON with correlation IDs | 1 |
| Audit stream | live in the dashboard | 1 |
| Metrics | Prometheus endpoint (queue depth, run durations, tokens, costs, model errors); optional Grafana | 7 |
| Alerts | stalled queue, budget at 80% or more, disk at 90% or more, dead runner, failed backup — shown in the dashboard; external notifications only if approved | 7 |

**Recovery:**

| Scenario | Recovery |
|---|---|
| Process crash | Kubernetes restarts the pod (liveness probe); dead runner detected by heartbeat; its jobs are retried; the run resumes from its checkpoint (§6.7) |
| Host or power loss | k3s and the pods restart with the host; PostgreSQL WAL recovery; interrupted attempts marked `abandoned`; runs resumed automatically unless the kill switch is set |
| Data loss | Phase 1: daily `pg_dump` of all three schemas + blob store, rotated, with the audit head hash. Phase 7: WAL archiving (PITR), off-host copy, weekly automated restore test into a scratch database followed by `python -m scripts.verify_events`. **A backup that has never been restored counts as no backup.** |
| Checkpoint store loss | domain record intact; affected runs marked `checkpoint_lost` and restartable, reusing completed tasks |

---

## 15. Hardware Scaling Path

The application code stays the same at every stage. What changes is configuration (environment
profiles, `models.yaml`, quotas, `replicas`) and the k3s nodes available.

| Stage | Hardware | What changes |
|---|---|---|
| Dev | laptop (6 GB VRAM) | small local model or recorded responses; no GPU assumptions in code |
| Server, initial | Tower T2, 14 CPU cores, 32 GB, no discrete GPU, shared with the other project | quota from SERVER_ASSESSMENT §4; Ollama on CPU (roughly 7–14B quantised models, slow) as the first inference backend (D-05); the external-provider adapter exists but stays disabled until approved |
| Server + GPU (e.g. RTX PRO 6000, 96 GB VRAM) | GPU installed in the Tower T2 (after the spec §11 compatibility checks), plus RAM upgrade | NVIDIA driver, container toolkit and k3s device plugin (approved server change); vLLM or Ollama Deployment requests `nvidia.com/gpu`; routing shifts work to larger local models through configuration |
| Multi-GPU / multi-host | more GPUs or additional k3s agent nodes | runner `replicas` spread across nodes; one inference server per GPU; PgBouncer; read replica for the dashboard; broker only if §7.4 triggers occur |
| Larger cluster | full Kubernetes | same images and manifests (registry instead of local import); S3-compatible blob store; replicated PostgreSQL |

---

## 16. Assumptions to Verify in the Phase 2 Spike

Each assumption gets an automated test before the dependent design is built on it. If one fails,
the design is revised and the user is told.

| ID | Assumption | Test |
|---|---|---|
| A-1 | The async PostgreSQL checkpointer persists state per super-step and resumes a thread after `kill -9` of the runner process | crash a runner mid-graph, resume on a fresh process, compare state |
| A-2 | A node that was running at crash time re-executes on resume, and our `agent_tasks` idempotency prevents duplicate work and cost | crash inside an agent node; assert one succeeded task and no duplicate model calls |
| A-3 | Completed parallel branches (`Send`) are not re-run when a sibling branch fails or the process dies | fan-out of N branches, fail one, resume |
| A-4 | `interrupt()` / `Command(resume=…)` survive process restarts and work from a different runner process | interrupt, stop the runner, resume elsewhere |
| A-5 | Cancelling the asyncio task running `ainvoke` stops promptly and leaves a consistent checkpoint | measure stop latency; resume after stop |
| A-6 | The checkpointer can use its own schema and DB role, and supports pruning old checkpoints | inspect tables and permissions; prune test |
| A-7 | The job library (D-03) provides transactional enqueue with our SQLAlchemy session, retries, locks per thread (no two runners on one thread), periodic jobs and cancellation | concurrency test: 2+ workers, same thread, assert exclusivity |
| A-8 | The licences of all chosen libraries permit self-hosted private use, and the LangGraph OSS library has no dependency on hosted services | licence review |
