# Implementation Plan and Test Criteria

**Status:** v1.0, approved 2026-11-22 · Phase 1 in progress (steps 1–7a done; 7b on the server) · **Last updated:** 2026-11-22

**Governing rules:**
- Spec §19.16: a phase does not start until the previous phase's tests pass.
- Each phase starts only after the user explicitly approves it.

"Tests pass" means automated tests run with one command against a real PostgreSQL instance, never
against a mocked database. Tests that need the server are marked as such and are reported
separately from laptop runs.

**Status legend:**
- ⬜ not started
- 🟨 in progress
- ✅ done and tested
- ⚠️ built but not fully tested (the reason is always stated)

---

## Phase 0 — Architecture and design · ✅ approved by the owner on 2026-11-22
Deliverables: [ARCHITECTURE.md](architecture/ARCHITECTURE.md), this plan,
[DECISIONS.md](DECISIONS.md).
Exit criterion: the user approves the architecture and the decisions marked *Needs input*.

## Phase 0.5 — Server assessment (read-only) · ✅ complete (24 h sampling dropped; see SERVER_ASSESSMENT §5)
**Prerequisite for any deployment and for capacity sizing.** Requirements are in ARCHITECTURE §12.
- Done 2026-11-22: the owner ran reviewed read-only inspection commands on the server. The
  planned 24 h load sampling was dropped (owner decision); the lab's own resource monitoring
  replaces it once deployed.
- Produce `docs/operations/SERVER_ASSESSMENT.md`: inventory, measured headroom, coexistence plan,
  capacity budget for the lab, and a list of server changes that need approval.

**Exit criteria:**
- The assessment is reviewed by the user.
- The capacity budget and coexistence plan are approved.
- No server change was made during the assessment. This is verified by comparing a `kubectl get
  all,pvc,cm,secret -A -o name` inventory and the `podman images` list before and after.

## Phase 1 — Foundation · 🟨 in progress

| Step | Content | Status |
|---|---|---|
| 1 | Project skeleton in the mirrored layout; draft moved in; app starts; migrations apply; smoke tests | ✅ 4/4 tests pass (2026-11-22) |
| 2 | Config, logging and migrations (criteria 1.1–1.2) | ✅ 29 tests pass (2026-11-22); total suite 33 |
| 3 | Audit log and stream engine (1.3–1.7, 1.13 engine part) | ✅ 28 tests pass (2026-11-22); total suite 61. The HTTP `/api/events/stream` endpoint moves to step 4 so it is protected by login from the start |
| 4 | Login, sessions, roles (1.8–1.10); authenticated event list and SSE endpoints (1.13 HTTP part) | ✅ 37 tests pass (2026-11-22); total suite 98 |
| 5 | Health, system status and resource monitoring; lifecycle audit events; restart survival (1.11–1.12, 1.14) | ✅ 17 tests pass (2026-11-22); total suite 115 |
| 6 | Dashboard (1.15): login, status, live audit log, user admin; production nginx config; browser tests | ✅ 12 browser tests (desktop + phone) and 4 new API tests pass (2026-11-22); backend suite 119 |
| 7a | Backend `Dockerfile`, kustomize manifests (base + staging/prod), `scripts/deploy.sh`, server scripts (secrets, inventory, isolation, restore), runbook | ✅ built locally (2026-11-22): 17 manifest policy tests pass; images not built yet (no podman on the laptop) |
| 7b | Server: storage volume, cluster objects, first staging deployment, criteria 1.16–1.19 ([SERVER_SETUP.md](operations/SERVER_SETUP.md)) | ⬜ waiting for the owner to run the runbook |
| 7c | The lab's deploy runner (D-07b): GitHub workflow, namespace-scoped deployer identity, restricted image helper | ✅ built locally (2026-11-22): 21 policy and helper tests pass; activation is runbook step 10 |
**Scope** (repository layout mirrors `agentic-trading-intelligence-platform`, ARCHITECTURE §5):
- Restructure the existing draft into that layout (root `app.py`, `src/` package, `test/`).
- Backend: FastAPI app, configuration with environment and hardware profiles, structured logging.
- Database: PostgreSQL and Alembic migrations for the `lab` schema.
- Audit: hash-chained audit log with an SSE stream.
- Auth: session authentication and roles.
- Health and readiness checks; real resource probes (CPU, RAM, disk, GPU).
- Dashboard in React + Vite + TypeScript (English), served by nginx: login, system status,
  live audit log, user administration.
- Images: backend `Dockerfile` (one image for api, runner, CronJobs, migrate init container) and
  `frontend/Dockerfile`; built with podman.
- Server deployment: `k8s/` manifests (namespace, quota, LimitRange, NetworkPolicy, ConfigMap,
  Secret template, PostgreSQL, api, frontend, backup CronJob), `scripts/deploy.sh` (podman build →
  k3s import → apply → rollout status), basic `pg_dump` backup with a restore script.

| # | Criterion | Where | Status |
|---|---|---|---|
| 1.1 | Invalid configuration aborts start-up with a clear English error; secrets never appear in logs or `repr` | laptop | ✅ |
| 1.2 | Migrations: `upgrade head` → `downgrade base` → `upgrade head` on an empty DB; ORM metadata matches the migrated schema (autogenerate diff is empty) | laptop | ✅ |
| 1.3 | An event appended in a rolled-back transaction does not exist | laptop | ✅ |
| 1.4 | `UPDATE` / `DELETE` / `TRUNCATE` on the audit table are rejected; the app role lacks those privileges | laptop | ✅ |
| 1.5 | The hash chain verifies; a row tampered with (bypassing the trigger) is detected | laptop | ✅ |
| 1.6 | ≥ 50 concurrent appends keep a consistent chain and strictly increasing `seq` | laptop | ✅ |
| 1.7 | Unknown event types and invalid payloads are rejected | laptop | ✅ |
| 1.8 | Login cases: correct and wrong password, inactive user, expired and revoked session, throttling after N failures that survives an app restart | laptop | ✅ |
| 1.9 | Protected endpoints return 401 without a session and 403 with too low a role; state-changing requests without the CSRF header return 403 | laptop | ✅ |
| 1.10 | Session tokens are stored only as hashes; passwords as argon2id | laptop | ✅ |
| 1.11 | `/health/live` always returns 200; `/health/ready` returns 503 when the DB is unreachable or migrations are not current | laptop + server | ✅ laptop; server ⬜ step 7 |
| 1.12 | `/api/system/status` values match an independent psutil/NVML reading; a missing GPU yields an explicit `unavailable` status with a reason, never zeros | laptop + server | ✅ laptop; server ⬜ step 7 |
| 1.13 | SSE delivers events appended after connecting; reconnecting with `Last-Event-ID` delivers missed events with no duplicates | laptop | ✅ |
| 1.14 | Data written by one app instance is read by a new instance after the first shuts down (restart simulation) | laptop | ✅ |
| 1.15 | Frontend: typecheck, lint and production build pass; UI smoke test (login → status → logout); all UI strings are English | laptop | ✅ |
| 1.16 | Manifests pass `kubectl apply --dry-run=server` in `ai-lab-staging`; images build with podman and import into k3s; all pods become Ready within the namespace quota; `/health/ready` returns 200 | server | ⬜ step 7 |
| 1.17 | Coexistence: the `default` namespace's resources, images and NodePorts are unchanged before and after the lab deploys (inventory diff); lab pods cannot reach the other project's Services (NetworkPolicy test) | server | ⬜ step 7 |
| 1.18 | A backup restores into a scratch DB and the audit chain verifies there | server | ⬜ step 7 |
| 1.19 | Pod deletion and server reboot: the lab comes back on its own and no data is lost | server | ⬜ step 7 |

## Phase 2 — LangGraph orchestration, runs and dispatch · 🟨 step 2a in progress
**Recorded exception to spec §19.16 (owner's instruction, 2026-11-22):** step 2a started before
Phase 1 passed its server criteria 1.16–1.19. The spike runs on the laptop only, adds no runtime
dependency to the deployed images (its libraries are dev-only until go/no-go) and changes no
Phase 1 code. Phase 1 stays 🟨 until 1.16–1.19 pass, and **step 2b does not start before that**.

**Step 2a, evaluation spike.** Verify assumptions A-1 to A-8 (ARCHITECTURE §16) with automated
tests on throwaway graphs, then write a short findings report. **The user decides go or no-go
before step 2b.** If an assumption fails, the design is revised and presented first.

**Step 2a status (2026-11-22): ✅ spike complete, waiting for the owner's go/no-go.** Findings,
proposed design changes C-1 to C-8 and the recommendation are in
[SPIKE_2A_FINDINGS.md](architecture/SPIKE_2A_FINDINGS.md). 24 spike tests are in `test/spike/`
(criterion 2.1 met for the laptop; the staging run is part of 2.12).

**Step 2b, implementation:**
- LangGraph Postgres checkpointer in its own schema and with its own DB role.
- Job library (D-03) for run dispatch and periodic jobs.
- Domain tables: `research_runs`, `agent_tasks`, `agent_task_attempts`, `approvals`, `budgets`,
  `budget_ledger`, `system_state`, `runners`.
- Run lifecycle service: start, pause, resume, stop, kill switch.
- Node guard for pause and stop.
- Human-in-the-loop approvals via `interrupt()`.
- Time, attempt and parallelism limits; budget framework (model costs come in Phase 3).
- Runner Deployment with heartbeats; CronJobs that enqueue periodic ticks.
- Dashboard: runs and tasks views, plus control buttons wired to real effects.

Test graphs use deterministic nodes, with no LLM calls.

| # | Criterion |
|---|---|
| 2.1 | Spike findings for A-1 to A-8 documented, with the test code kept in the suite |
| 2.2 | `kill -9` of a runner mid-run → the run resumes on another runner from its last checkpoint; every logical task succeeds exactly once (`agent_tasks` unique) and the interrupted attempt is recorded as `abandoned` |
| 2.3 | Two runners never execute the same thread at the same time (concurrency test) |
| 2.4 | Stop: the running node is cancelled within ≤ 10 s (measured); the status reads `cancel_requested` until it is confirmed, then `stopped`; the kill switch stops all runs and blocks new ones, including after a restart |
| 2.5 | Pause suspends at the next node boundary with its checkpoint saved, holds no runner while paused, and resume continues from that point |
| 2.6 | Approval interrupts survive a restart and resume from a different runner |
| 2.7 | Retries: transient failures are retried with backoff up to the limit; permanent failures are not retried |
| 2.8 | Time, attempt and parallelism limits are enforced; exceeding a budget → `blocked_budget` + approval request + audit event |
| 2.9 | Bounded loops: the graph cannot exceed configured rework cycles (test with a node that always requests rework) |
| 2.10 | Module boundaries pass import-linter (domain modules do not import LangGraph) |
| 2.11 | Checkpoint pruning works and does not affect the domain record |
| 2.12 | All Phase 2 tests also pass on `staging` |

## Phase 3 — Model layer · ⬜
**Scope:**
- `ModelGateway` with router, adapters (Ollama, OpenAI-compatible, and approved external providers
  per D-05), model registry and environment placement profiles.
- Accounting: budget reservations, call cache, token, time and cost measurement.
- Resilience: circuit breakers and per-model concurrency limits.
- Ollama adapter first (D-05), against the shared host Ollama or a lab-owned Ollama Deployment
  (D-15); an external-provider adapter built and contract-tested, but disabled.

| # | Criterion |
|---|---|
| 3.1 | Adapter contract tests on recorded responses; live tests against a local inference server (marked; run when available) |
| 3.2 | Unavailable model → fallback; no candidates → explicit task failure, never an empty success |
| 3.3 | Budgets: a call that would exceed a limit is never sent; concurrent reservations never overshoot (concurrency test) |
| 3.4 | A re-executed node does not pay again for completed calls |
| 3.5 | The router never selects an excluded model (`must_differ_from`) or an external provider for `confidential` data |
| 3.6 | External and local costs are reported separately; token counts are labelled `provider` or `estimated` |
| 3.7 | No paid API is called by automated tests unless an explicit flag and budget are set |
| 3.8 | Changing a role's model requires only a configuration change (test swaps the profile) |

## Phase 4 — First real research workflow · ⬜
**Scope:** the 11 steps of spec §18 Phase 4, built as a LangGraph graph. It includes:
- role registry (YAML, English prompts), agent node factory and tool gateway;
- 2–3 free official source adapters (for example OpenAlex, arXiv, Crossref) with source snapshots;
- the evidence domain: claims, evidence links, verification, red team findings, scoring and
  decisions;
- intake normalisation of non-English input to English;
- versioned documents with Markdown export;
- dashboard project and history views.

| # | Criterion |
|---|---|
| 4.1 | A full run on a real topic produces a report in which every `sourced` claim points to an existing snapshot whose content contains the excerpt |
| 4.2 | The database rejects a verification written by the producer's task or role |
| 4.3 | The red team raises high-severity findings against a deliberately flawed control hypothesis |
| 4.4 | Runs can end in `reject` and `withhold_insufficient_data` (control cases) |
| 4.5 | Injected instructions inside fetched content do not change agent behaviour (regression suite) |
| 4.6 | Stopping and resuming at any stage gives a consistent result |
| 4.7 | The dashboard history traces inspiration source → hypothesis → analyses → critique → verification → decision |
| 4.8 | A Polish project brief yields English-only outputs (reports, claims, statuses); the original input is preserved verbatim |

## Phases 5–9 · ⬜
Scope follows spec §18:

| Phase | Scope |
|---|---|
| 5 | research engines: science, patents, competition, finance, regulation, technology, risk |
| 6 | more roles and tools |
| 7 | 24/7 autonomy: scheduler, recovery, resource monitoring, periodic reports, PITR backups, alerts |
| 8 | dashboard expansion |
| 9 | optimisation and scaling |

Criteria are refined before each phase starts. Standing requirements for every new module:
persistence, restart, limit, stop, model-failure, security and permission tests.
- Phase 7 adds a weekly automated restore test and a 72-hour continuous-operation test on the server.
- Phase 5 adds compliance with source terms of use and API rate limits.

---

## Current Implementation State (2026-11-22)

**Done and tested (Phase 1 steps 1–6)**
| Area | What exists | Evidence |
|---|---|---|
| Skeleton | root `app.py`/`main.py`, `src/` package, layered TOML config, Alembic migrations, `python -m scripts.migrate` | app starts; migrations apply |
| Config and logging | validated layered settings, secrets from files, JSON logs with request IDs and redaction | 18 config + 6 logging tests |
| Database | `lab` schema; least-privilege roles (`lab_owner` for migrations, `lab_app` for runtime) | 5 migration tests, including drift detection |
| Audit log | append-only (trigger + grants), SHA-256 hash chain, `python -m scripts.verify_events`, live stream engine | 21 audit + 7 stream tests |
| Auth | login, logout, sessions, roles, persistent throttling, CSRF, trusted proxies, `python -m scripts.create_user` | 34 auth tests |
| API | health, system status, users, audit list, latest and SSE endpoints | 7 HTTP stream tests (real server), 10 system tests, 7 probe tests |
| Dashboard | login, live status, live audit log, user admin; production nginx template; frontend Dockerfile | 12 browser tests (desktop + phone) |

Totals: **157 backend tests (including manifest, RBAC, workflow and image-helper policy checks) and 12 browser tests pass**. ruff, strict mypy, ESLint and the
TypeScript check are clean.

**Defects found by the tests and fixed**
- Typos in configuration sections were silently ignored.
- Secrets mounted as files were not read.
- Logs could vanish when the error stream was swapped.
- A notification race could delay live events by up to 15 s.
- Login crashed (HTTP 500) for clients without an IP address.
- Behind a proxy, five failed logins would have locked out every user.
- Login had an open-redirect hole.
- Live audit events were invisible to browsers.
- The GPU driver was re-initialised on every sample.

**Phase 2a, LangGraph evaluation spike (recorded exception, laptop only)**
- 24 tests in `test/spike/` cover A-1 to A-8 plus A-9, using real `kill -9` crashes and
  process hand-over. All assumptions hold; A-5 holds only for async nodes.
- Findings and proposed design changes: [SPIKE_2A_FINDINGS.md](architecture/SPIKE_2A_FINDINGS.md).
- The libraries are dev-only (`requirements-dev.in`); the production image is unchanged.

**Not done yet**
- Step 7b: server preparation, the first deployment to `ai-lab-staging`, and criteria 1.16–1.19
  plus the server part of 1.11–1.12. Everything for it is written and checked locally. The
  container images themselves have not been built yet, because the laptop has no podman; the
  first build happens on the server.
- Activating the lab's GitHub runner (runbook step 10), after staging passes.
- No research functionality exists yet (Phase 2 onwards).

**Known limitations**
- Throttling per username can lock a known account for 15 minutes (R-18).
- Deleting the newest audit events is not detectable by the chain alone (R-19).
- One harmless third-party deprecation warning (Starlette test client).
