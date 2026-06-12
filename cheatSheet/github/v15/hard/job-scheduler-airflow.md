# Job scheduler (Airflow)

**Hard** · Leader election · DAG dependencies · At-least-once

Tags: `ZooKeeper/etcd`, `PostgreSQL`, `SQS`, `Leader election`, `DAG dependency`, `Heartbeat requeue`

## Data flow

A single scheduler leader is elected via ZooKeeper/etcd. It polls PostgreSQL for due jobs and enqueues to SQS. Workers pull jobs, execute, send heartbeats. DAG dependency: a job runs only when all parent jobs have succeeded. Heartbeat timeout → requeue.


> Leader election = single scheduler = no double-schedule  |  Heartbeat timeout → requeue  |  SETNX = idempotent

## Architecture diagram

```
+-------------------+
                           |       User        |
                           +---------+---------+
                                     |
                               POST /jobs
                               GET /jobs
                                     |
                                     v
                        +------------+------------+
                        |        API Service       |
                        +------------+------------+
                                     |
                   +-----------------+-----------------+
                   |                                   |
                   v                                   v
          +--------+--------+                 +--------+---------+
          |    Jobs Table   |                 | Executions Table |
          | job definition  |                 | run instances    |
          +--------+--------+                 +--------+---------+
                   |                                   |
                   |                         GSI on user_id + time
                   |                                   |
                   |                                   v
                   |                         +---------+---------+
                   |                         | Status Query Path |
                   |                         +-------------------+
                   |
                   |        every 5 min scans next ~5 min
                   v
          +--------+--------+
          | Scheduler Cron  |
          | / Dispatcher    |
          +--------+--------+
                   |
                   | enqueue with delay
                   v
          +--------+---------+
          |   Delayed Queue  |
          |   SQS / Redis    |
          +--------+---------+
                   |
                   | messages become visible near run time
                   v
        +----------+----------+------------+
        |                     |            |
        v                     v            v
   +----+-----+          +----+-----+  +---+------+
   | Worker A |          | Worker B |  | Worker N |
   +----+-----+          +----+-----+  +---+------+
        |                     |            |
        +----------+----------+------------+
                   |
                   | fetch job details
                   v
          +--------+--------+
          |    Jobs Table   |
          +--------+--------+
                   |
                   | execute task
                   v
          +--------+--------+
          | Task Handler(s) |
          | email, webhook, |
          | cleanup, etc.   |
          +--------+--------+
                   |
         +---------+----------+
         |                    |
         v                    v
 +-------+-------+    +-------+--------+
 | success       |    | failure        |
 | mark complete |    | retry w backoff|
 +-------+-------+    +-------+--------+
         |                    |
         +---------+----------+
                   |
                   v
          +--------+---------+
          | Executions Table |
          | status updates   |
          +------------------+
```

Draw the two tables first — that is the data model. Then draw the Scheduler scanning and enqueueing. Then the worker pool. Then the success/failure split at the bottom. Save the Status Query Path and GSI for if the interviewer asks about read patterns.


---

<details open>
<summary><strong>Problem</strong></summary>

Running scheduled and dependency-based jobs reliably.

Hard parts: preventing double-scheduling, enforcing DAG dependencies, and ensuring jobs complete even when workers crash.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Scheduler leader crashes mid-dispatch cycle**

Some jobs were enqueued but not recorded as enqueued. On leader re-election, new leader re-scans and re-enqueues them. Workers execute jobs twice.

_Fix:_ Transactional dispatch: enqueue to SQS + mark job as DISPATCHED in PG in the same transaction (using SQS FIFO with deduplication ID). Redis SETNX per job_id in workers prevents double execution even if enqueued twice.

**A long-running job (8 hours) holds a worker indefinitely, blocking queue progress**

Worker pool capacity exhausted by a few long-running jobs. Short jobs queue up and miss their schedule.

_Fix:_ Worker timeout per job type (configured separately). Separate worker pools: long-running jobs (timeout = 24h) and short-running jobs (timeout = 15min). Priority queue for time-sensitive jobs.

**DAG has a cycle (A → B → C → A)**

Scheduler loops infinitely trying to find a ready job in a circular dependency.

_Fix:_ Validate DAG topology at definition time (not at runtime). Topological sort: if a cycle is detected during sort, reject the DAG definition with a clear error. Never store invalid DAG definitions.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 100K job definitions, 10M job executions/day, avg execution time 5 min |
| Read QPS | 10M / 86400 ≈ 116 job dispatches/s — low throughput |
| Write QPS | 116 job state updates/s (PENDING → RUNNING → SUCCEEDED/FAILED) in PG — trivially low |
| Storage | Job definitions: 100K × 1KB ≈ 100 MB. Execution history: 10M/day × 500 bytes × 365 ≈ 1.8 TB/year — keep 90 days rolling. |
| Cache math | Near-term schedule: pre-compute jobs due in next 5 minutes → load into Redis sorted set (score = scheduled_time). Scheduler polls Redis instead of DB. Reduces DB query rate from 1/s to 1/5min. |
| Verdict | Throughput (116/s) is not the challenge. Correctness (no double-execution, no missed jobs, correct DAG ordering) is the hard problem. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Pull model (workers pull from queue) vs. push model (scheduler pushes to workers)**

→ Pull: workers pull from SQS

Push requires scheduler to track worker capacity and health. Pull: worker signals availability by polling. SQS handles capacity naturally — idle workers drain queue, busy workers don't pull. No scheduler-to-worker communication needed.

_Revisit when:_ Push model for tighter scheduling precision (< 1 second accuracy) where pull latency is too high.

**ZooKeeper vs. etcd vs. DB-based leader election**

→ etcd (or ZooKeeper) for leader election

DB-based election (UPDATE schedulers SET leader=1 WHERE id=? AND leader=0) works but has 1-5 second failover time and poll-based heartbeat adds DB load. etcd/ZooKeeper: dedicated coordination service, watch-based (event-driven) failover in < 1s.

_Revisit when:_ DB-based election is fine for non-critical schedulers or where adding etcd is operationally too heavy.

**Cron expression vs. interval-based scheduling**

→ Both: cron expressions for calendar-based jobs, interval for rate-based jobs

Cron expressions (0 9 * * MON = every Monday at 9 AM) cover calendar-aware scheduling. Intervals (every 15 min) are simpler for periodic jobs. Different data model: cron requires computing next_run_time from expression, interval just adds the interval.

_Revisit when:_ Unified model: always compute next_run_time and store it — abstracting over both cron and interval.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a job that consistently fails (always returns error)?**

Retry policy per job: max_retries with exponential backoff. After max_retries: mark as DEAD, move to DLQ, alert on-call. Never automatically retry indefinitely — it would starve the queue. Jobs in DEAD state are visible in UI for manual investigation and re-trigger.

**How do you implement job dependencies across different DAGs?**

Cross-DAG dependencies are dangerous — they create hidden coupling. Better pattern: use an event/message when DAG-A completes, trigger DAG-B as a separate Kafka event. Avoids circular dependency in DAG topology validation. If you must have cross-DAG deps: treat other DAG's completion as an external sensor (polling or event-based trigger).

**How do you handle jobs that need exclusive access to a resource?**

Job-level mutex: before starting execution, worker acquires a Redis lock (SETNX) on the resource_id. If lock already held: job goes back to queue with a delay. Lock TTL slightly longer than max job duration to auto-release on worker crash.

**How would you implement backfill (run past missed executions)?**

Backfill creates explicit execution instances for each missed time slot. Generates N jobs (one per missed window) and enqueues them. Worker executes with execution_date parameter = the historical date, not current time. This is how Airflow's backfill feature works.

**What's the right timeout for the heartbeat?**

Heartbeat interval = 30s. Timeout = 2× interval (60s) as minimum to avoid false positives on GC pause or slow disk. For jobs that do no I/O for long periods (pure computation): progress-based heartbeat (% complete) instead of time-based.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Cron** — Linux cron. Simple shell scripts. No dependencies, no retry, no visibility. Works for < 100 jobs. Breaks at any scale.

**v2 — Distributed scheduler** — etcd leader election. SQS job queue. Worker pool with heartbeats. PG for job state + DAG definitions. DLQ for failed jobs. Handles 10M jobs/day.

**v3 — Enterprise** — Web UI for DAG visualization. Sensor-based external triggers. SLA monitoring and alerting. Job priority queues. Backfill support. Multi-tenant with team-level isolation.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Job Scheduler is time. You are not just storing jobs. You have to find the right jobs at the right moment, execute them close to their scheduled time, and still keep the system durable when workers crash.

There are three main scaling pain points. First, time based lookup gets expensive. If you store recurring schedules like cron expressions, you cannot scan every job every second to see what is due. That is why you usually separate the job definition from execution instances and index executions by time. Second, precision and throughput fight each other. At 10k jobs per second, polling the database very frequently creates huge read load, but polling less often makes jobs late. A common fix is a two phase design where the database holds durable schedule state and a queue handles near term delivery. Third, retries and failures create duplicate work. If a worker dies mid job, the system must retry, which means you need at least once delivery and idempotent tasks so running a job twice does not break things.

A good interview summary is this. Job Scheduler is hard to scale because it combines time based querying, high throughput dispatch, and failure handling in one system. The system has to be both precise like a clock and resilient like a queue.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: define jobs with schedules (cron or interval), define DAG dependencies, execute jobs reliably, retry on failure, monitor status. Out of scope unless asked: live streaming jobs, sub-second scheduling, multi-tenant isolation.
- **Single leader — no double-dispatch** — Leader election via etcd or ZooKeeper. Only the leader polls the DB for due jobs and enqueues them. Two schedulers running simultaneously = same job enqueued twice = duplicate execution. Single leader is the correct default.
- **Pull model — workers poll SQS** — Workers signal availability by pulling from SQS. SQS handles capacity naturally: idle workers drain queue, busy workers don't pull. Scheduler needs no knowledge of worker count or health. Scale workers independently.
- **Heartbeat requeue for fault tolerance** — Worker sends heartbeat every 30s. Scheduler monitors: if heartbeat stops for 60s, mark job as timed out and re-enqueue. At-least-once execution — jobs must be idempotent (check "already ran" at start).
- **DAG validation at definition time** — Topological sort on DAG definition. Cycle detected → reject with error. Never store an invalid DAG. Cycle detection at runtime is too late — jobs would loop forever without a natural termination condition.
- **Dead letter queue for permanent failures** — Max retries (e.g., 3) with exponential backoff. After max retries: move to DLQ, alert on-call. Never silently discard failed jobs. DLQ entries visible in UI for manual re-trigger after root cause fix.
- **Failure mode to name** — Leader crashes mid-dispatch: SQS deduplication ID prevents duplicate enqueue (idempotent). New leader is elected in <1s (etcd watch-based). Jobs in-flight continue executing under their existing workers — no interruption.

> Leader election + heartbeat requeue + SETNX idempotency = no double schedule, no lost jobs, no double execution.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Single leader vs multi-scheduler** — Single leader prevents double-scheduling without coordination. Multi-scheduler requires distributed locking (etcd/ZooKeeper) for every dispatch decision. Single leader is correct until scheduling throughput itself becomes a bottleneck (rare — 116 jobs/sec is trivial).

**At-least-once vs exactly-once execution** — Exactly-once requires 2PC or distributed transactions — complex and slow. At-least-once + idempotent job logic (check "already ran" at job start) is simpler and correct. Make idempotency a platform contract, not a framework guarantee.

**Pull (workers poll SQS) vs push (scheduler dispatches)** — Pull: workers signal availability by polling — scheduler needs no knowledge of worker capacity. SQS handles capacity naturally. Push: tighter scheduling precision but scheduler must track worker health and capacity. Pull is the right default.

**DB-based leader election vs etcd/ZooKeeper** — DB election (UPDATE SET leader WHERE leader=0) works but has 30–60s failover via polling. etcd/ZooKeeper: event-driven watch, <1s failover. If etcd is already in the stack (e.g., Kubernetes cluster), use it. If not, DB election avoids adding a new dependency.

> "Leader election prevents double-scheduling. Heartbeat requeue prevents lost jobs. SETNX prevents double execution."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Leader election — preventing double-dispatch with ZooKeeper/etcd
> [!CAUTION]
> **🔴 Weak** — run multiple scheduler instances in parallel for redundancy
>
> [!WARNING]
> **🟡 Strong** — two scheduler instances running simultaneously would both scan the same job table and enqueue the same jobs twice. Workers execute jobs twice → idempotency violations, corrupted state, duplicate emails, double payments. Weak answer: use a DB lock. Strong answer: ZooKeeper or etcd leader election. etcd approach: all scheduler instances compete to create an ephemeral key /scheduler/leader with their instance ID. The TTL is 15 seconds (heartbeat interval). Only one instance can create the key — that instance is the leader. Other instances watch the key and wait. If the leader crashes: key expires in 15 seconds, election re-runs
>
> [!TIP]
> **🟢 Staff+** — DB-based election (UPDATE schedulers SET is_leader=1, heartbeat_at=now() WHERE id=? AND is_leader=0) works but has 30-60 second failover (depends on heartbeat check frequency) and adds polling load to PG. etcd/ZooKeeper: event-driven (watch-based), <1 second failover, designed for coordination. The choice is operational: if you already have etcd in your stack (Kubernetes uses it), use it. If not, DB-based is acceptable


#### Deep dive 2: DAG dependency enforcement — correct job ordering at scale
_Airflow-style DAGs: task B can only run after task A succeeds. At 10M executions/day with complex DAGs (some with 50+ tasks), the scheduler must efficiently find tasks that are ready to run_

> [!CAUTION]
> **🔴 Weak** — scan all tasks periodically
>
> [!WARNING]
> **🟡 Strong** — event-driven dependency resolution. When a task completes: publish a TASK_COMPLETED event. The scheduler consumes this event, checks if all dependencies for downstream tasks are now satisfied, and enqueues ready tasks. Dependency check: SELECT count(*) FROM task_instances WHERE dag_run_id=? AND task_id IN (upstream_tasks) AND status != 'SUCCESS'. If count = 0: all upstreams succeeded, enqueue the task
>
> [!TIP]
> **🟢 Staff+** — this check is a hotspot under high fan-out DAGs (one task → 100 downstream tasks). Batch the dependency check: on TASK_COMPLETED, add the dag_run_id to a Redis set. A low-frequency background scanner processes the set, checks all downstream tasks for that dag_run, enqueues ready ones. Reduces per-event DB queries from O(downstream_tasks) to O(1) per event. At-least-once delivery: if the dependency check fails (DB unavailable), the task remains in PENDING state. The periodic scanner catches it on the next cycle


#### Deep dive 3: Fault tolerance — heartbeat timeout, at-least-once, and idempotent workers
> [!CAUTION]
> **🔴 Weak** — mark a job as failed only when the worker explicitly reports failure
>
> [!WARNING]
> **🟡 Strong** — a worker executing a job may crash mid-execution. The job must be retried
>
> [!TIP]
> **🟢 Staff+** — at-least-once design: worker sends heartbeat every 30 seconds (UPDATE task_instances SET last_heartbeat=now() WHERE id=? AND status='RUNNING'). Scheduler scans for stale heartbeats: SELECT id FROM task_instances WHERE status='RUNNING' AND last_heartbeat < now() - INTERVAL '60s'. Re-enqueues timed-out tasks. This gives at-least-once execution — the job may run twice if the worker crashes and recovers but the heartbeat was temporarily delayed. Exactly-once requires idempotent jobs: a job that can be safely run twice must produce the same result (send-email with deduplication ID, db-insert with upsert, file-generation with atomic rename). Staff+ design principle: the scheduler guarantees at-least-once; job authors are responsible for idempotency. This is an explicit contract documented in the platform's API. For non-idempotent jobs: add an explicit "already-ran" check at job start (SELECT 1 FROM job_executions WHERE job_id=? AND execution_date=? AND status='SUCCEEDED'). Dead letter queue for jobs that fail beyond max_retries — never silently discard


_Why the deep dives connect to the scaling problem: "Time-based querying, high-throughput dispatch, failure handling." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Leader-election script.

2. "Clarifying questions: are we building a general-purpose scheduler — cron-style plus DAG dependencies — or focused on a specific use case like ETL pipelines? And what's the job volume?"

3. "Good — general-purpose, DAG dependencies, 10M job executions/day. Core features: define jobs with schedules, define dependencies, execute reliably, retry on failure, monitor status. Out of scope: real-time streaming jobs, sub-second scheduling."

4. "The most important correctness requirement: no double-dispatch. Two schedulers running simultaneously would both enqueue the same job. Workers would execute it twice. This drives the leader election design."

5. "Leader election: etcd ephemeral key with TTL. All scheduler instances compete to create /scheduler/leader. Only one wins — that instance dispatches. Others watch the key. On leader crash: key expires in 15 seconds, re-election completes in <1 second."

6. "Dispatch: leader polls PostgreSQL for jobs due in the next 5 minutes, enqueues to SQS. Workers pull from SQS — pull model means workers signal availability naturally, no capacity tracking needed."

7. "DAG dependency: job enters the queue only when all parent jobs have status SUCCEEDED. Event-driven check: on job completion, evaluate downstream tasks. Cycle detection at DAG definition time via topological sort — reject invalid DAGs before they're stored."

8. "Heartbeat requeue: worker sends heartbeat every 30s. If heartbeat stops for 60s, scheduler re-enqueues. At-least-once execution — jobs must be idempotent. Platform contract: idempotency is the job author's responsibility, not the scheduler's."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                           |       User        |
                           +---------+---------+
                                     |
                               POST /jobs
                               GET /jobs
                                     |
                                     v
                        +------------+------------+
                        |        API Service       |
                        +------------+------------+
                                     |
                   +-----------------+-----------------+
                   |                                   |
                   v                                   v
          +--------+--------+                 +--------+---------+
          |    Jobs Table   |                 | Executions Table |
          | job definition  |                 | run instances    |
          +--------+--------+                 +--------+---------+
                   |                                   |
                   |                         GSI on user_id + time
                   |                                   |
                   |                                   v
                   |                         +---------+---------+
                   |                         | Status Query Path |
                   |                         +-------------------+
                   |
                   |        every 5 min scans next ~5 min
                   v
          +--------+--------+
          | Scheduler Cron  |
          | / Dispatcher    |
          +--------+--------+
                   |
                   | enqueue with delay
                   v
          +--------+---------+
          |   Delayed Queue  |
          |   SQS / Redis    |
          +--------+---------+
                   |
                   | messages become visible near run time
                   v
        +----------+----------+------------+
        |                     |            |
        v                     v            v
   +----+-----+          +----+-----+  +---+------+
   | Worker A |          | Worker B |  | Worker N |
   +----+-----+          +----+-----+  +---+------+
        |                     |            |
        +----------+----------+------------+
                   |
                   | fetch job details
                   v
          +--------+--------+
          |    Jobs Table   |
          +--------+--------+
                   |
                   | execute task
                   v
          +--------+--------+
          | Task Handler(s) |
          | email, webhook, |
          | cleanup, etc.   |
          +--------+--------+
                   |
         +---------+----------+
         |                    |
         v                    v
 +-------+-------+    +-------+--------+
 | success       |    | failure        |
 | mark complete |    | retry w backoff|
 +-------+-------+    +-------+--------+
         |                    |
         +---------+----------+
                   |
                   v
          +--------+---------+
          | Executions Table |
          | status updates   |
          +------------------+
```

Draw the two tables first — that is the data model. Then draw the Scheduler scanning and enqueueing. Then the worker pool. Then the success/failure split at the bottom. Save the Status Query Path and GSI for if the interviewer asks about read patterns.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-25)
