# Phase 2a Spike Findings: LangGraph, Checkpointer and Procrastinate

**Audience:** the owner, who decides go or no-go for step 2b. **Date:** 2026-11-22.
**Status:** spike complete. **Decision: GO, with C-1 to C-8 approved by the owner on 2026-11-22** (DECISIONS).

The spike tests ARCHITECTURE §16 assumptions A-1 to A-8 with throwaway graphs whose nodes are
deterministic (no LLM calls). It also adds A-9, a LangGraph feature found during the spike. All test
code is kept in the suite under `test/spike/` (criterion 2.1).

## Versions tested
| Library | Version | Licence |
|---|---|---|
| langgraph | 1.2.24 | MIT |
| langgraph-checkpoint-postgres | 3.2.1 | MIT |
| procrastinate | 3.12.0 | MIT |
| PostgreSQL | 17 (local dev cluster) | PostgreSQL |

During the spike these are **dev-only** dependencies (`requirements-dev.in`). The production
image and `requirements.txt` are unchanged.

## How it was tested
- **Real crashes.** Each crash is a `kill -9` of a separate runner process at an exact point in a
  node. Each resume runs in a fresh process, which is what happens when a runner pod dies on the
  server.
- **Real database.** Every test gets its own throwaway database, laid out as ARCHITECTURE §7.1
  describes:
  - schema `langgraph`, owned by its own role `lab_graph`;
  - schema `jobs`, holding Procrastinate's tables, owned by `lab_owner`, with DML granted to `lab_app`;
  - schema `spike`, a stand-in for the domain record (`agent_tasks`, attempts, model-call cache).
- **Counting.** Every node records each execution in a table, so the tests count exactly how often
  each piece of work ran.
- **Stability.** 24 tests, run repeatedly. Two early failures were timing bugs in the tests
  themselves, not library defects. They were fixed, and the suite was re-run until stable
  (see "Test-suite notes").

## Results

| ID | Assumption | Result | Test |
|---|---|---|---|
| A-1 | The checkpointer resumes a thread after `kill -9` | ✅ holds | `test_a1_*` |
| A-2 | A node running at crash time re-executes; our idempotency prevents duplicate work and cost | ✅ holds, with our design | `test_a2_*` (2 crash points) |
| A-3 | Completed parallel branches are not re-run | ✅ holds, with one nuance | `test_a3_*` (3 tests) |
| A-4 | Interrupts survive restarts and resume in another process | ✅ holds, with one rule | `test_a4_*` (2 tests) |
| A-5 | Cancelling `ainvoke` stops promptly and leaves a consistent checkpoint | ✅ for async nodes · ❌ for sync nodes | `test_a5_*` (2 tests) |
| A-6 | Own schema and role; pruning | ✅ holds | `test_a6_*` (5 tests) |
| A-7 | Procrastinate: transactional enqueue, retries, per-thread locks, periodic jobs, cancellation | ✅ holds, with one required addition | `test_a7_*` (6 tests) |
| A-8 | Licences permit private self-hosting; no hosted-service dependency | ✅ holds | `test_a8_*` (2 tests) |
| A-9 | *(new)* `RunControl.request_drain()` gives a clean pause at a node boundary | ✅ works | `test_a9_*` |

### A-1: resume after `kill -9`
A 4-node thread was killed while its last node was running. A new process resumed it with
`ainvoke(None)` and finished it.

**What re-ran:** only the node that was running at the crash. The completed nodes ran once.

**Requirement:** runs use `durability="sync"`, so each step's checkpoint is written before the next
step starts.

### A-2: re-execution and idempotency
The agent node was killed at two points:

| Crash point | What the resume did |
|---|---|
| After the "model call", before the task was committed | The node re-ran from its start and got the model result from the `(task, call_seq)` cache. The first attempt was kept and marked `abandoned`. |
| After the task was committed | The node re-ran, found its own completed `agent_tasks` row and returned the stored result. No new attempt was made. |

In both cases there was **one model call and one logical task**.

**Conclusion:** ARCHITECTURE §6.3 step 2 and §6.7 work as designed. In 2b, marking an attempt
`abandoned` moves to the runner reaper, which goes by heartbeats. In the spike the next attempt did it.

### A-3: parallel branches (`Send`)
- **Kill during the branches:** 4 of 5 branches had finished; the fifth was running when the
  process was killed. After resume, the 4 finished branches did **not** run again, because each one's
  writes are saved as it finishes.
- **One branch fails after its siblings finished:** the siblings are kept and only the failed branch
  re-runs.
- **Nuance (new finding):** if a branch fails while siblings **are still running**, LangGraph
  **cancels those siblings**, and they run again on resume. No branch ever *completes* twice,
  but a cancelled sibling's partial work is repeated. This is safe with our idempotent agent nodes
  and model-call cache, since a completed model call is never paid for twice. Branch work must
  stay idempotent.

### A-4: human-in-the-loop interrupts
- An interrupted run's process exits; nothing holds a runner while it waits. A different process
  resumes it later with `Command(resume=…)`. A runner killed while the run is waiting loses nothing.
- **Rule (documented LangGraph behaviour, confirmed):** on resume the interrupted node runs
  **again from its beginning**, so code before `interrupt()` runs twice. Anything there must be
  idempotent. In 2b the `approvals` row is an upsert keyed by task.

### A-5: hard stop (cancel)
- **Async node** waiting on I/O: `ainvoke` stopped within 1 s (the target is 10 s). The checkpoint
  stayed consistent (the pending node is listed as next), and resuming finished the run.
- **Sync node: ❌ (new finding).** LangGraph runs a synchronous node in a worker thread.
  Cancelling `ainvoke` returns at once, but **the thread keeps running and writing** until it
  finishes. A "stop" is therefore not real for sync code.
- **Design consequence (proposed):** all nodes, tools and gateways must be `async`. Blocking work
  goes behind an async interface with its own cancellation, or into a subprocess that can be killed.
  2b adds a test that refuses any non-async node in a registered graph.

### A-6: checkpoint store
- **Own schema:** the library's tables are not schema-qualified, so the schema comes from the
  connection setting `search_path=langgraph`. All checkpoint tables are created in `langgraph`
  and owned by `lab_graph`.
- **Own role:** `lab_graph` has no access to the domain, `jobs` or `public` schemas and cannot
  create anything elsewhere. `lab_app` has no access to `langgraph`.
- **Setup:** `setup()` can be run again safely. It needs an autocommit connection, because it uses
  `CREATE INDEX CONCURRENTLY`, so in 2b it runs in `scripts.migrate` as `lab_graph`.
- **Pruning:**
  - `aprune(strategy="keep_latest")` leaves one checkpoint, and the final state is still readable;
  - `strategy="delete"` removes everything for a thread;
  - neither touches the domain tables (criterion 2.11);
  - a thread waiting on an interrupt can be pruned to its latest checkpoint and still resumes.

### A-7: Procrastinate as the dispatch layer
- **Transactional enqueue works.** The job is deferred on the psycopg connection under our
  SQLAlchemy session (`SET LOCAL search_path = jobs` first). If the transaction rolls back, the job
  never existed. If it commits, the job and the domain row appear together.
- **One runner per thread.**
  - **Setup:** two worker processes, each running one job at a time, and 6 jobs on 2 threads, with
    each job locked by `lock=thread:<id>`.
  - **Result:** jobs of the same thread never overlapped. Jobs of different threads did run at the
    same time, on different workers.
- **Retries:**
  - transient errors are retried;
  - errors not listed in `retry_exceptions` are not retried and the job is marked `failed`;
  - **note:** `max_attempts=3` means 3 *retries*, so 4 runs in total. Our configuration will name
    the field `max_retries` to avoid an off-by-one.
- **Periodic jobs:** with two workers running a 1-second schedule, each tick ran **exactly once**.
- **Cancellation:**
  - aborting a running async job cancelled it in under 2 s, and the job ends as `aborted`;
  - cancelling a queued job removes it before it runs.
- **Required addition (new finding): stalled jobs are not recovered automatically.** When a
  worker is killed:
  - its job stays `doing`;
  - **the thread's lock stays held**, so the thread's next job does not start on another worker.
    That is correct: it stops two runners on one thread;
  - the job stays stuck until someone calls `get_stalled_jobs()` and `retry_job()`.

  The test does exactly that and the job then finishes on the surviving worker, in order. In 2b,
  a **reaper periodic job** does it. The longest time to recover from a dead runner is the
  heartbeat timeout plus the reaper interval, planned as about 30 s plus 15 s.

### A-8: licences and hosted services
- **Licences:** every package in the closure of the three libraries has an allowed licence: MIT,
  BSD, Apache-2.0, PSF, MPL-2.0 (certifi, orjson) or LGPL-3.0 (psycopg, already in use). There
  is no GPL, AGPL, SSPL or BUSL. A test enforces this on every run.
- **No outbound traffic:** `langsmith` (LangSmith tracing) is installed **transitively** through
  langchain-core. With the default configuration (no `LANGSMITH_*` or `LANGCHAIN_*` variables), a
  fan-out run and an interrupt run made **no outbound connection**: a guard on Python sockets
  fails the test on any non-loopback connection. This matches decision U-6 (hosted tracing
  disabled). 2b also sets `LANGSMITH_TRACING=false` explicitly in the deployment.
- `langgraph-sdk`, the client for the hosted LangGraph Server, is installed as a dependency but is
  never imported or used.
- **Caveat:** the socket guard covers Python-level sockets, which is how HTTP clients would call
  out. The database driver (libpq) opens its own local socket.

### A-9 (new): graceful pause through `RunControl`
LangGraph 1.2 has `RunControl.request_drain()`. When drain is requested, the run stops before its
**next** node, saves its checkpoint and raises `GraphDrained`. `ainvoke(None)` later continues
exactly there. The test shows:
- the node that was running finished cleanly;
- nothing after it started;
- the resume ran every node exactly once.

## Proposed design changes (need your approval with the go/no-go)

| # | Change | Where | Why |
|---|---|---|---|
| C-1 | **Pause uses `RunControl.request_drain()`** instead of a guard in every node that calls `interrupt()`. The runner receives the pause request (`NOTIFY lab_control`) and drains its own run. | ARCH §6.6 | A library feature replaces custom code. Paused runs and approval interrupts stay clearly separate. Pause still happens at a clean node boundary, after the current node. |
| C-2 | **Async-only nodes and tools**, enforced by a test | ARCH §6.3, §6.6 | A-5: a stop cannot stop sync code |
| C-3 | **Stalled-job reaper** as a periodic Procrastinate job, with explicit heartbeat and timeout settings | ARCH §7, §14 | A-7: recovery from a dead runner is not automatic |
| C-4 | **Runs use `durability="sync"`** | ARCH §6.7 | A-1: checkpoint is on disk before the next step |
| C-5 | **Code before `interrupt()` must be idempotent**; approvals are upserts | ARCH §6.5 | A-4 |
| C-6 | Connection setup: the checkpointer uses `search_path=langgraph` as `lab_graph`; Procrastinate uses `search_path=jobs`; transactional enqueue uses `SET LOCAL search_path` | ARCH §7.1 | A-6, A-7 |
| C-7 | Schema management: the Procrastinate schema becomes an Alembic migration (it ships an Alembic integration); the checkpointer's `setup()` runs from `scripts.migrate` as `lab_graph` | ARCH §7, §13 | one place for all schema changes |
| C-8 | Retry settings are named `max_retries` | config | A-7 off-by-one |

None of these changes the chosen stack. They make it precise.

## Recommendation
**Go** for step 2b with LangGraph OSS, its PostgreSQL checkpointer and Procrastinate (D-03
confirmed), with changes C-1 to C-8.

**Order:** per the recorded exception in IMPLEMENTATION_PLAN, step 2b starts only after Phase 1
passes its server criteria 1.16–1.19.

## Test-suite notes
Two tests were flaky on the first runs. Both causes were in the tests, not the libraries, and both
were fixed:
1. **A-3 "a sibling fails":** the failing branch and its siblings had the same delay. Sometimes it
   failed before a sibling finished, so the sibling was cancelled and ran again, which is the
   documented nuance above. The failing branch now fails 0.5 s after its siblings.
2. **A-7 lock test:** it assumed both workers would take work, which isn't guaranteed when one
   worker runs four jobs at a time. Each worker now runs one job at a time, and the test asserts
   that any jobs running at the same time ran on different workers.

Run the spike suite: `source scripts/env.sh && python -m pytest test/spike -q` (needs the dev
database; takes about 40 s).
