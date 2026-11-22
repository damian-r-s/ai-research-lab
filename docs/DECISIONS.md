# Architecture Decisions and Risks

**Status:** v1.0 · **Last updated:** 2026-11-22

| Status | Meaning |
|---|---|
| **DECIDED** | set by the user |
| **APPROVED** | the architect's proposal, approved by the owner ("Approved", 2026-11-22) |
| **RECOMMENDED** | the architect's proposal; approval requested |
| **NEEDS INPUT** | cannot be resolved without the user |
| **SUPERSEDED** | replaced, kept for history |

---

## Decided by the user (2026-11-22)

| ID | Decision |
|---|---|
| U-1 | **LangGraph** is the orchestration framework. No custom orchestrator. Business logic, methodology, agent definitions, evaluation and workflow parameters stay separate from the framework. |
| U-2 | **English-only system**: all outputs, prompts, docs, logs, statuses and reports. Non-English input is accepted and normalised (ARCHITECTURE §11). |
| U-3 | The **home server is production from the start**; the laptop is for development and lightweight tests only. Do not design around laptop limits. |
| U-4 | **Reuse the server's existing container infrastructure.** Do not disrupt, stop or modify the other project. Inspect the server (read-only) before any deployment; isolate the lab. *(Verified 2026-11-22: k3s v1.36.4 installed directly on the bare-metal Dell Pro Max Tower T2 `alpha`, with podman-built images. See SERVER_ASSESSMENT.md.)* |
| U-5 | **No custom queue or messaging framework by default.** Evaluate LangGraph persistence first; treat state persistence and message transport separately; introduce a messaging platform only for identified requirements. |
| U-6 | **Hardware-specific configuration stays outside business logic**; capacity growth must not require redesign. |
| U-7 | **The repository structure mirrors `agentic-trading-intelligence-platform`**: root `app.py`, `src/` package, `core/databases/{migrations,repositories}`, LangGraph under `src/agents`, workers in `src/services`, `k8s/` manifests, Vite/React frontend behind nginx, `test/`. Deliberate differences are listed in ARCHITECTURE §5. |
| U-8 | **Models (from D-05):** start with **Ollama**, possibly mixed with external providers later. External providers stay disabled until providers, data-sharing rules and budgets are chosen. |
| U-9 | **Dashboard access (from D-06):** the recommended option (Tailscale, no public exposure) is acceptable. *(Interpreted from the answer "can be no prob"; please correct if a different option was meant.)* |
| U-10 | **Source control and builds (from D-07):** GitHub hosting and the sibling project's build path (podman on the server → k3s import). *(Verified: a self-hosted runner is installed on the server for the other repository, but that repository has no workflow files yet. For the lab see D-07b.)* |
| U-13 | **No CI tests on push (for now).** Tests run on the laptop before every push; the lab's runner only deploys. (Owner decision, 2026-11-22.) |
| U-12 | **The server `alpha` is the owner's own machine** (confirmed 2026-11-22; resolves D-14 ownership). |
| U-11 | **Hosted tracing (from D-08):** disabled, as recommended. No LangSmith or other hosted tracing; prompts and research content stay on the server. *(Interpreted from "ok".)* |

## Superseded

| ID | Was | Replaced by |
|---|---|---|
| v0.1 D-01 | Custom PostgreSQL task queue as the central work queue | U-5 and D-03: LangGraph checkpointing for workflow state, plus an established job library for run dispatch only |
| v0.1 D-02 | Custom workflow engine instead of LangGraph | U-1 |
| v0.1 D-03 | Install Docker on the laptop | U-4: the server's Docker; laptop dev stays native |
| v0.1 D-04 | Clarify whether the laptop is the 24/7 host | U-3 |
| v0.1 D-10 | Polish UI and reports | U-2 |
| v0.2 | Docker Compose deployment on the server | k3s namespaces (U-4, ARCHITECTURE §13) |
| v0.2 | Next.js frontend; `backend/src/lab` package layout | U-7: React + Vite + nginx; `src/` layout |
| v0.2 D-05, D-06, D-07, D-08 | open questions | U-8 to U-11 |

---

## Approved recommendations (approved 2026-11-22)

### D-01 — Three persistence concerns in one lab-owned PostgreSQL · APPROVED
LangGraph checkpoints (schema `langgraph`), the domain record and audit (`lab`), and dispatch jobs
(`jobs`) live in **one dedicated PostgreSQL StatefulSet in the `ai-lab` namespace, separate from the other project**, with one
schema and DB role per concern.

Why:
- one backup gives a consistent state;
- one service to operate;
- transactional enqueue together with domain changes.

Each concern can move to its own instance later through configuration alone. The domain record is
the source of truth; checkpoints are only for resuming.

### D-02 — LangGraph usage boundaries · APPROVED
- Use the **open-source library** only (`langgraph` + `langgraph-checkpoint-postgres`), run inside
  our own runner processes.
- Do **not** depend on LangGraph Platform, LangGraph Server or other hosted products. Their
  licensing and hosting terms would need separate review (A-8), and our dispatch needs are small.
- Graph state holds references, not documents.
- The LangGraph Store is **not** used as the knowledge base. Our knowledge tables carry provenance
  and verification data that the store does not model.

### D-03 — Run dispatch: PostgreSQL-backed job library (Procrastinate) · APPROVED, CONFIRMED by the Phase 2a spike
The requirements review (ARCHITECTURE §7.3) found no need for a message broker. What remains is:
- dispatching start and resume of runs to runner processes;
- periodic jobs;
- retries;
- cancellation;
- per-thread exclusivity.

| Option | For | Against |
|---|---|---|
| **Procrastinate** (recommended) | PostgreSQL only (no new infrastructure); async; transactional enqueue; locks, retries, periodic tasks, cancellation | smaller community than Celery; features to be confirmed in the spike (A-7) |
| Redis + arq / Taskiq / Dramatiq | popular, fast | adds Redis, which is not otherwise needed; no transactional enqueue with domain changes |
| Celery + RabbitMQ / Redis | very mature | heavy; weak fit for async code; extra broker |
| Broker now (NATS JetStream / RabbitMQ) | ready for future fan-out | no current requirement (§7.3); more to operate and secure |

Periodic triggers use **Kubernetes CronJobs** that only enqueue jobs. This matches the sibling
project's pattern and keeps the job library's own periodic feature optional.

Re-evaluation triggers are defined in ARCHITECTURE §7.4. If the spike shows Procrastinate does not
meet A-7, the fallback is Redis + arq, presented for approval first.

**Spike result (2026-11-22):** A-7 holds (SPIKE_2A_FINDINGS.md). Procrastinate stays, with the
stalled-job reaper from C-3.

### D-04 — Model adapters may wrap LangChain chat-model integrations · APPROVED
This gives native LangGraph tool-calling compatibility and many providers without custom HTTP
clients. Routing, budgets, accounting, caching and privacy enforcement remain in our
`ModelGateway`, so no framework can spend money outside our limits.

### D-12 — Fate of the pre-approval Phase 1 draft · APPROVED and DONE
Before approval, an **unexecuted** draft was written under `backend/` and `scripts/`:
- configuration, logging and ids;
- audit log with hash chain;
- auth service;
- resource probes;
- first migration.

Code and comments are in English. It predates U-1 to U-6 and has no tests.

**Recommendation:** keep it as a starting point. At the start of Phase 1, move it into the mirrored
layout (U-7: `backend/src/lab/*` → `src/core/*`, `src/api/*`, …), then review and test it properly. It
does not conflict with the new decisions, because Phase 1 has no orchestration. Nothing is counted
as done until it passes the Phase 1 criteria. The alternative is to delete it and start clean.

**Outcome:** the draft was moved into the mirrored layout in Phase 1 step 1, then tested in steps
2–5. Those tests found and fixed several defects in it, listed in the Phase 1 report in
IMPLEMENTATION_PLAN.md.

### D-13 — Python 3.13 · APPROVED
The laptop's system Python 3.14 has no pip. 3.13 has the widest library support. Containers use the
same version.

---

## Other decisions (status per item)

### D-05 (remainder) — External providers and budgets · NEEDS INPUT LATER (before Phase 4)
Ollama is the starting point (U-8). Before the first real research workflow, two things need
deciding: whether to add an external provider, and the budgets and privacy class. The server has
**no discrete GPU** (verified), so inference runs on 14 CPU cores. RAM allows roughly 7–14B
quantised models, but generation is slow. That is adequate for classification, extraction and
embeddings, and doubtful for the analysis quality spec §1 demands. Phase 3 measures real
throughput and quality, so this decision is made on data.

### D-15 — Ollama: share the host instance or run a lab-owned one · APPROVED: option (b), lab-owned
| Option | For | Against |
|---|---|---|
| (a) Share the existing host Ollama (0.34.0, only `llama3.2:3b`) | no extra RAM for a second server; already running | contention with the other project's analyses; pulling models for the lab changes a shared service; one config for both |
| (b) **Lab-owned Ollama Deployment in `ai-lab`** (recommended) | full isolation; lab-specific models and parallelism; quota-limited; in-cluster Service name, so it is unaffected by the host IP change seen on the server | extra RAM for loaded models; models stored on a lab PVC (needs D-17 disk) |

The server has about 25 GiB of RAM available, so option (b) fits within the proposed quota.

### D-09 — Agent code execution · NEEDS INPUT (not before Phase 5)
Agents do not execute code by default. If calculations need it, the proposal is an isolated
Kubernetes Job in a separate namespace: no network, CPU/RAM/time limits, read-only filesystem.
That needs approval because it runs on the shared cluster.

### D-10 — Source accounts and terms of service · NEEDS INPUT (Phases 4–5)
| Source type | Sources |
|---|---|
| Free, no account | OpenAlex, arXiv, Crossref, Europe PMC |
| Registration or terms acceptance required (by the user) | EPO OPS (Espacenet), USPTO Open Data Portal, Semantic Scholar (key recommended), NCBI (key recommended), Lens.org |
| No official automated-access API (will not be scraped) | Google Patents |

### D-11 — Storage of full source texts · NEEDS INPUT (Phase 4)
Proposal:
- Store full snapshots privately, as evidence for citation checks.
- Reports contain only short excerpts and links.
- Openly licensed content has no restriction.

This needs the user's acceptance for copyright reasons.

### D-14 — Server access · RESOLVED (ownership: U-12)
Inspection ran as the owner running reviewed read-only commands. For deployment the lab uses a
deploy identity limited to the `ai-lab*` namespaces (see D-07b).

### D-07b — Lab CI/CD runner · APPROVED, implemented (setup is runbook step 10)
The installed runner is registered to `agentic-trading-intelligence-platform` only. It runs as
`damian`, which holds a cluster-admin kubeconfig. A runner for one personal-account repository
cannot serve another repository.

**Recommendation:** register a **second runner** for `ai-research-lab` on `alpha`:
- a dedicated system user (e.g. `ailab-ci`) with no login shell and no general sudo;
- a **namespace-scoped kubeconfig** (ServiceAccount + RoleBinding in `ai-lab` and
  `ai-lab-staging` only), so the lab's workflows cannot touch `default` or cluster-wide resources;
- one narrow sudoers rule allowing only `k3s ctr -n k8s.io images import -` (image import is the
  one root-level step; R-14);
- rootless podman for builds under that user.

Workflows run tests on every push, deploy to `ai-lab-staging` automatically, and promote to
`ai-lab` only after a manual approval in GitHub. Until the runner exists, `scripts/deploy.sh`
performs the same steps by hand.

The alternative is no runner, with manual deploys only.

### D-17 — Storage on the server · APPROVED (owner performs the server change)
The volume group has **850.8 GB unallocated**; root has 48 GB free.

**Recommendation:**
1. Create a logical volume `ai-lab` of **300 GB** (it can be grown online later), with an ext4 or
   xfs filesystem mounted at `/srv/ai-lab`. This is an owner-performed server change; the exact
   commands are given for review before anything runs.
2. Lab data lives there as **static local PersistentVolumes** (a `local` StorageClass with no
   provisioner, node affinity to `alpha`), one per purpose:
   - `postgres` (prod and staging);
   - `ollama-models`;
   - `blobs` (source snapshots, documents);
   - `backups`.
3. Nothing in `kube-system` is modified. The k3s-managed local-path provisioner stays as it is,
   because k3s reapplies its bundled manifests on restart.
4. Container images remain in `/var/lib/rancher` on root. `deploy.sh` keeps only the last N image
   tags per component to bound growth.

| Alternative | Why not preferred |
|---|---|
| Extend the root LV | lab growth would share one filesystem with the OS and the other project |
| Stay within 48 GB | too tight once models, the knowledge base and backups exist |

---

## Phase 2a outcome · GO, approved by the owner on 2026-11-22
Step 2b proceeds with LangGraph OSS, its PostgreSQL checkpointer and Procrastinate. The owner
approved the design changes found by the spike
([SPIKE_2A_FINDINGS.md](architecture/SPIKE_2A_FINDINGS.md)). They are applied in ARCHITECTURE.

| ID | Decision | From |
|---|---|---|
| C-1 | Pause uses `RunControl.request_drain()`: the runner that owns the run drains it, and the run stops after the current node with its checkpoint saved. There is no per-node guard calling `interrupt()`. `interrupt()` is used only for approvals | A-9 |
| C-2 | All graph nodes, tools and gateways are `async`. A test rejects any registered graph containing a synchronous node. Blocking work goes behind an async interface with its own cancellation, or into a subprocess | A-5 |
| C-3 | A stalled-job reaper runs as a Procrastinate periodic job inside the runners. It retries jobs whose worker heartbeat has expired and marks their open attempts `abandoned`. Heartbeat interval, stall timeout and reaper interval are configuration | A-7 |
| C-4 | Every run is invoked with `durability="sync"` | A-1 |
| C-5 | Code before an `interrupt()` must be idempotent; an approval request is an upsert keyed by its task | A-4 |
| C-6 | Each library connection sets its own schema: the checkpointer uses `search_path=langgraph` as role `lab_graph`, and Procrastinate uses `search_path=jobs`. A transactional enqueue sets `SET LOCAL search_path` on the domain session | A-6, A-7 |
| C-7 | Every schema is created by `scripts.migrate`: the Procrastinate schema is an Alembic migration; the checkpointer's `setup()` runs afterwards as `lab_graph` over an autocommit connection | A-6 |
| C-8 | Retry limits in lab configuration are named `max_retries`. Procrastinate's `max_attempts` counts retries, not runs | A-7 |

**Order:** the owner extended the exception to spec §19.16: step 2b is built on the laptop
**before** Phase 1 passes its server criteria 1.16–1.19. Phase 1 stays 🟨 until they pass, and the
first server deployment then includes the Phase 2 workloads (criterion 2.12).

---

## Implementation decisions (Phase 1)

Engineering decisions taken during implementation, recorded so the reasons stay traceable. None of
them changes the approved architecture.

| ID | Decision | Reason |
|---|---|---|
| I-1 | Configuration is layered: `config/base.toml` < `config/environments/<LAB_ENV>.toml` < secret files < `.env` < `LAB_*` environment variables. Every section rejects unknown keys | hardware and environment values stay out of code; typos fail loudly instead of being silently ignored |
| I-2 | Secrets are read from **mounted files** (`LAB_SECRETS_DIR`, one file per value), not from environment variables, in staging and production | environment variables leak more easily (process listings, crash dumps, child processes) |
| I-3 | `X-Forwarded-For` is trusted only from `api.trusted_proxies`, read right to left | behind nginx every request comes from the proxy; without this, five failed logins by anyone would lock out every user |
| I-4 | Every audit event goes out over SSE under the single event name `audit` | the browser `EventSource` delivers only event names it subscribed to, so per-type names would silently drop new types |
| I-5 | Lifecycle audit events (`system.process.started`/`stopped`) are best-effort | a database outage must not prevent the API from starting and reporting itself "not ready" |
| I-6 | NVML is initialised once per process | the first driver initialisation takes about 1.6 s; re-initialising on every 5 s sample wasted that time |
| I-7 | Frontend uses TypeScript 6.0, not 7.0 | `typescript-eslint` supports TypeScript < 6.1 |
| I-8 | The dashboard image is `nginx-unprivileged` (non-root, port 8080) instead of the sibling's `nginx:alpine` | least privilege; the same template is tested locally with real nginx in the browser tests |
| I-9 | Browser tests run against the real API behind the **production nginx template**, not a dev server | they test what will be deployed: headers, proxying, SSE unbuffering |
| I-10 | uvicorn always runs with proxy-header handling **off** (`--no-proxy-headers`) | by default uvicorn rewrites the client address from `X-Forwarded-For` for requests from 127.0.0.1, which would bypass `api.trusted_proxies` |
| I-11 | Manifests use kustomize (`base/` plus `overlays/{staging,prod}`) instead of the sibling's flat files | staging and prod share one definition; sizes, quota and ClusterIP differ per overlay; image tags are set to the exact commit at deploy time |
| I-12 | Lab volumes are **static local PersistentVolumes**, each pre-bound to one claim, on `/srv/ai-lab`; k3s's local-path provisioner is left untouched | k3s reapplies its bundled manifests on restart, so its configuration must not be edited; pre-binding keeps lab data off the root filesystem |
| I-13 | Database passwords are generated on the server by `scripts/server/create-secrets.sh`; no secret file exists in git, not even as a template with values | nothing to leak or commit by mistake |
| I-14 | Phase 1 pods have **no internet egress** | nothing in Phase 1 needs it; egress to model and source APIs is opened per component when it is built (Phases 3–4) |
| I-15 | The runner's only root privilege is `/usr/local/sbin/ai-lab-image`, which inspects each image archive and imports or removes only `localhost/ai-lab-{api,frontend}:<sha>` | a plain sudo rule for `k3s ctr` would let a crafted archive replace the other project's images |
| I-16 | Production deploys are a **manual workflow run** (`workflow_dispatch`), not a GitHub "environment" with required reviewers | required reviewers on private repositories need a paid GitHub plan; the manual trigger gives the same control for free, and `deploy.sh` still requires the commit to be verified on staging |
| I-17 | The runner's kubeconfig uses the `lab-deployer` ServiceAccount: write access to lab objects in the two lab namespaces, read-only on three cluster objects, no RBAC changes, Secrets limited to an existence check of `lab-secrets` | least privilege; unlike the existing runner, it cannot affect the other project |

---

## Implementation decisions (Phase 2)

| ID | Decision | Reason |
|---|---|---|
| I-18 | Schema `langgraph` is created by the migration role and owned by it; the graph role gets only USAGE and CREATE on it, and creates the checkpoint tables itself | the migration role cannot create a schema owned by another role; the graph role still reaches nothing outside its schema (test_orchestration_schema.py) |
| I-19 | The Procrastinate schema is applied from the library's own `schema.sql` in migration 0002, pinned to the installed version | one place for all schema changes (C-7); a test fails when the library is upgraded without a new migration |
| I-20 | Every checkpointer setup grants SELECT on the checkpoint tables to the migration role | the nightly `pg_dump` runs as that role; without the grant the backup fails with "permission denied" (found and tested in 2b.1) |
| I-21 | The api pod's migrate init container has its own secret volume with the graph role's URL; the long-running api container never receives it | least privilege: only migrations and the runner use the checkpointer role (manifest policy test) |
| I-22 | The orchestration tables grant the application role no DELETE; the budget ledger is append-only (SELECT, INSERT) and the kill-switch row can only be read and updated | the run and cost record is evidence and is kept |
| I-23 | Controls act immediately when no runner holds the run: pausing a queued run, or stopping a queued, paused or waiting run, changes its status at once. Only a running run gets `pause_requested` or `cancel_requested` and a `NOTIFY lab_control` to its runner. Every allowed status change is listed in `src/runs/states.py` | a stop never waits for a runner that does not exist; the dashboard can tell "stopping" from "stopped" |
| I-24 | Run jobs carry two locks: `thread:<id>` (one execution per thread at a time) and the queueing lock `run:<id>` (one waiting execution per run); the enqueue runs in a savepoint, so a duplicate is reported instead of aborting the caller's transaction | spike A-7; repeated resume clicks queue one execution |
| I-25 | Starting or resuming a run takes a share lock on the kill-switch row and engaging the switch an exclusive one, always before any run row | no run can start while the switch is being engaged, and the fixed lock order rules out deadlocks (race test) |
| I-26 | Any SQL that touches Procrastinate's tables directly must set `search_path=jobs`, because the library's trigger functions use unqualified names | found in 2b.2: an update without it fails inside the trigger |
| I-27 | The executor derives what to invoke the graph with from the checkpoint only: no checkpoint → initial state; pending interrupts → `Command(resume={interrupt_id: answer})` for decided approvals; otherwise `None` (continue). The interrupt payload carries the approval id, so nothing else is stored | one source of truth for where a run is; resuming after a pause, an approval or a crash is the same code path |
| I-28 | A stop wins: if a stop was requested while a run was pausing or reaching an approval, it ends `stopped`. A cancellation without a stop request (for example a runner shutdown) leaves the run `running` for recovery (2b.4) | a user's stop is never turned into a pause; a shutdown is not mistaken for a stop |
| I-29 | Built-in graph `lab.selftest` (deterministic, no models): steps, fan-out, bounded review/rework loop, optional approval, injectable failure | verifies the orchestration end to end in tests and on staging/production before any research graph exists |
| I-30 | A new attempt of a task marks any attempt still open for that task `abandoned`; this is safe because only one execution per thread runs at a time (I-24). The reaper does the same for dead runners (2b.4) | an interrupted attempt is always recorded, even when the next attempt starts before the reaper runs |
| I-31 | On SIGTERM a runner stops taking jobs, drains its runs and hands each back (`running → queued` with a new job) after its current node; nodes still running after `runner.shutdown_timeout_s` are cut off and left to the reaper | a deploy or node restart neither loses work nor turns runs into user-visible pauses |
| I-32 | The reaper first records the domain change (a lost `running` run becomes `queued`; a lost `cancel_requested` run becomes `stopped`), then retries the job; it also marks silent runners `dead` and abandons their open attempts. Worst-case recovery time is the stall timeout plus the reaper interval (defaults 30 s + 15 s) | the record never says "running" for a run nobody executes; each step is idempotent, so a reaper that dies halfway is repeated safely |
| I-33 | Runners act on `NOTIFY lab_control` and also re-read their runs' control flags every `runner.control_poll_interval_s` | a notification sent while a runner's listener was reconnecting is not lost |
| I-34 | Import contracts: the domain (`src.core`, `src.runs`, `src.resources`) never loads LangGraph, not even indirectly; entry points (`src.api`, `src.services`) may reach it only through `src.agents`; only `src.runs.dispatch` and `src.services` use Procrastinate directly | entry points must use the graph runtime, but nothing outside `src.agents` should write LangGraph code |
| I-35 | Checkpoints of runs finished more than `runner.checkpoint_retention_days` (default 7) ago are pruned to the latest one by a periodic job; `research_runs.checkpoints_pruned_at` records it | keeps the checkpoint store small; the final state stays readable and the domain record is untouched (criterion 2.11) |

---

## Risk Register

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R-01 | The lab competes with the other project for 14 cores and 32 GB | either project slowed or starved | ResourceQuota and LimitRange (SERVER_ASSESSMENT §4); the lab's own resource monitoring; the lab pauses itself under pressure; RAM upgrade path |
| R-02 | No discrete GPU (verified); CPU-only inference is slow and limited to mid-sized models | low analysis quality, long run times | measure in Phase 3; D-05 remainder; GPU upgrade in the Tower T2 (ARCHITECTURE §15) |
| R-03 | LangGraph durability semantics differ from our assumptions (A-1 to A-7) | duplicate work or cost, inconsistent resume | Phase 2a spike with a go/no-go gate; idempotent agent tasks regardless |
| R-04 | LangGraph API churn across versions | upgrade work | pinned versions (aligned with the sibling project where practical); framework isolated in `src/agents`; domain modules framework-free |
| R-05 | Analysis quality limited by available models | weak recommendations | routing to capable models (D-05); a different model for verification; a quality evaluation set from Phase 4 |
| R-06 | Hallucinated sources or numbers | loss of credibility | cite only stored `source_id`s; excerpt checks in code; `value_status` on every number |
| R-07 | Prompt injection from web content | manipulated results | data/instruction separation; no side-effecting tools; regression tests |
| R-08 | API cost overrun | financial | pre-call budget reservation; hierarchical limits; default 0 |
| R-09 | Source terms of use and copyright | legal | official APIs only; rate limits; D-10, D-11 |
| R-10 | Language drift (non-English output from models) | violates U-2 | prompt requirement; output language validation with repair; tests (criterion 4.8) |
| R-11 | Single PostgreSQL instance as a single point of failure | data loss, downtime | backups with restore tests from Phase 1; PITR in Phase 7; replication later |
| R-12 | Dev/prod drift (no podman or k3s on the laptop) | "works on laptop" failures | same versions (PG 17, Python 3.13, Node 22); `staging` namespace on the server is mandatory before `prod` |
| R-13 | Over-engineering or scope creep | delay | phase gates with hard criteria; modules created only when needed |
| R-14 | Image import (`sudo k3s ctr`) and cluster-admin kubeconfigs are root-equivalent on a shared server; the existing runner already runs with cluster-admin rights | security of both projects | deploy user with namespace-scoped RBAC; sudo limited to the import command; documented in the assessment |
| R-15 | The node's registered IP (`.160`) no longer matches the host's interfaces; host services addressed by IP break | failures like the other project's recent `auto-analysis` jobs (cause unconfirmed) | the lab uses in-cluster Services only; recommend a DHCP reservation or static IP for `alpha` |
| R-16 | Root filesystem has 48 GB free (images still live there) | deployment failures when it fills | lab data on its own LV (D-17); image tag retention in `deploy.sh`; disk-usage alerts |
| R-17 | Host services are exposed on the LAN without a firewall (Ollama 11434, k3s API 6443, the other project's PostgreSQL 30432) | unauthorised access from the LAN | the lab adds no LAN exposure; host firewall rules are a separate owner decision (not changed by the lab) |
| R-18 | Login throttling per username lets an attacker lock a known account (e.g. the admin) for 15 minutes by failing its login on purpose | admin temporarily locked out | the dashboard is reachable only over the owner's tailnet (U-9); revisit before any wider exposure (e.g. CAPTCHA, exponential back-off) |
| R-19 | A hash chain cannot detect deletion of its newest events | silent loss of the most recent audit entries | export the latest chain hash with every backup and compare on restore (ARCHITECTURE §9.1, Phase 7) |
