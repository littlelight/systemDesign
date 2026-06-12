# LeetCode — coding platform

**Medium** · Long-running tasks · Sandbox isolation · Leaderboard

Tags: `Docker (seccomp)`, `SQS/Kafka`, `Redis`, `PostgreSQL`, `Job queue`

## Data flow

Submit → enqueue in SQS → worker pulls job → runs code inside a Docker container (no network, CPU/memory limits, killed on timeout) → writes verdict to Redis. Client polls for result. Leaderboard = Redis sorted set (ZINCRBY/ZREVRANGE).


> Docker + seccomp: no network, kill on timeout  |  Compare stdout vs expected  |  Autoscale workers by queue depth

## Architecture diagram

```
+-------------------+
                         |   Web / Mobile    |
                         |      Client       |
                         +---------+---------+
                                   |
                     GET problems, submit code, poll
                                   |
                         +---------v---------+
                         |    API Server      |
                         |  auth from JWT     |
                         |  problem APIs      |
                         |  submit API        |
                         |  leaderboard API   |
                         +----+---------+-----+
                              |         |
                 read problems|         |read submission status
                              |         |
                    +---------v--+   +--v----------------+
                    | Problems DB |   |  Submissions DB   |
                    | DynamoDB    |   | results, code,    |
                    | problems,   |   | passed, metadata  |
                    | test cases, |   +---------+---------+
                    | code stubs  |             |
                    +------------ +             |
                                                |
                                   enqueue job  |
                                                |
                                      +---------v---------+
                                      |   Job Queue        |
                                      |   SQS or similar   |
                                      +---------+---------+
                                                |
                                          pull job
                                                |
                                      +---------v---------+
                                      | Submission Worker  |
                                      | picks runtime      |
                                      | loads problem      |
                                      | runs test harness  |
                                      +----+----------+----+
                                           |          |
                              execute code  |          | update leaderboard
                                           |          |
                     +---------------------v--+    +--v------------------+
                     | Sandboxed Containers    |    | Redis Sorted Set    |
                     | python, java, js, etc  |    | competition ranks   |
                     | CPU and memory limits  |    | fast top N reads    |
                     | no network             |    +---------+-----------+
                     | timeout enforced       |              |
                     +-----------+------------+              |
                                 |                           |
                          stdout or result                   |
                                 |                           |
                                 +-------------+-------------+
                                               |
                                      +--------v--------+
                                      |   API Server    |
                                      | returns status  |
                                      | to polling      |
                                      +--------+--------+
                                               |
                                      +--------v--------+
                                      |     Client      |
                                      | shows result    |
                                      | polls leaderboard|
                                      +-----------------+
```

If you want the interview version, I would draw the simpler version first. Start with Client, API Server, Problems DB, Submissions DB, Queue, Worker, Sandboxed Containers, and Redis for leaderboard. Then explain that problem reads are synchronous, but submission execution is asynchronous because code runs for seconds and needs isolation.

The main idea is simple. Reads go straight through the API. Code submission goes through a queue to workers, workers execute inside locked down containers, results are stored in the submissions database, and leaderboard reads come from Redis instead of recomputing from the database every time.


---

<details open>
<summary><strong>Problem</strong></summary>

Safe, fast code evaluation at scale. Users browse problems, submit code, and get feedback quickly while the platform safely runs untrusted code.

The real challenge: executing user code in isolation, not storing problems.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Execution worker pool exhausted during contest start**

All submissions queue up. Users wait minutes for results. Contest rankings are delayed.

_Fix:_ Pre-scale worker pool 30 min before contest (predictable traffic pattern). Autoscale by queue depth with aggressive scale-up policy. Shed load gracefully: queue max depth, return 'system busy, retry in 30s' rather than timing out silently.

**Container escapes resource limits (memory bomb)**

Single submission kills a worker node, slowing all other submissions on that node.

_Fix:_ cgroups + seccomp profile. Hard OOM kill at container level. Node-level memory pressure monitoring — evict container if host reaches 90% memory. Isolate contest traffic on dedicated node pool.

**Leaderboard Redis sorted set becomes hot during live contest**

Thousands of users polling leaderboard every 10s = massive ZREVRANGE QPS on one key.

_Fix:_ Cache leaderboard snapshot (CDN or in-process cache) with 5s TTL. Push updates via SSE instead of polling. Rate-limit leaderboard endpoint per user.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500K DAU normal, 100K concurrent during contest, avg 5 submissions/user/contest |
| Read QPS | 100K users × 1 poll/10s = 10,000 leaderboard QPS during contest peak |
| Write QPS | 100K × 5 submissions / (2hr × 3600) ≈ 69 submission QPS normal, spikes to 5,000 at contest open |
| Storage | Each submission: code (50KB) + test output (10KB) × 100M submissions = 6 TB — S3 + metadata in PG |
| Cache math | 5,000 submission QPS × 10s execution time = 50,000 concurrent containers needed at peak. Reserved capacity + spot instances. Pre-warmed container pool. |
| Verdict | The submission burst at contest open is the design constraint. Not steady-state QPS but the 60-second spike at T=0. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Docker containers vs. VMs vs. AWS Lambda**

→ Docker with seccomp on dedicated worker nodes

Lambda cold start ~100ms is too slow. VMs ~5s startup is too slow. Pre-warmed Docker containers start in <100ms with no cold start. Dedicated nodes prevent noisy neighbor problems.

_Revisit when:_ Firecracker microVMs (AWS's approach) give VM-level isolation at near-container speed. Best of both worlds for security-critical execution.

**Redis for result storage vs. PG**

→ Redis with TTL for ephemeral results, PG for permanent record

Client polls result ID for ~10 seconds. Redis handles this trivially. No need to query PG on every poll. After 1hr, result moves to PG for permanent storage.

_Revisit when:_ WebSocket push instead of polling would eliminate Redis as a result buffer entirely.

**Single queue vs. per-language queues**

→ Per-language worker pools behind a shared queue

Python is 10× slower than C++. Mixed queue means Python submissions starve C++ workers. Separate queues: Python, JavaScript, C++/Java/Go each get dedicated worker pools sized by submission volume.

_Revisit when:_ Start with single queue for simplicity. Split only when language-specific tail latency becomes a user complaint.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you prevent user code from making network calls?**

Docker network namespace with --network none. seccomp profile blocks socket syscalls. No iptables needed — network namespace isolation is at kernel level. Test this explicitly — it's the most critical security property.

**How do you handle an infinite loop submission?**

Hard CPU timeout via cgroups (e.g., 10 seconds CPU time, not wall time). Container killed and result set to TLE (Time Limit Exceeded). PID limits prevent fork bombs. Memory limit prevents memory exhaustion.

**How do you test new language versions or judge upgrades?**

Canary deployment: route 5% of submissions to new judge version. Compare results against current version. Roll back if disagreement rate > 0.1%. Never upgrade judges during live contests.

**How do you support user-defined test cases?**

Store user test cases in S3. Worker downloads test case bundle before execution. Isolate user test cases from canonical test cases — different output comparison logic. Rate-limit custom test case execution (more expensive than standard submission).

**What's your SLA for submission results?**

P50 < 3s, P99 < 30s. Anything beyond 30s should re-queue, not timeout. Users should never see a blank result — always a status (Queued / Running / Accepted / TLE / etc).

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single server running code in subprocess with timeout. No isolation. PG for everything. Works for low traffic, unacceptable security risk.

**v2 — Isolated execution** — Docker containers with resource limits. SQS job queue. Redis for results. Worker autoscaling. Handles normal traffic safely.

**v3 — Contest scale** — Pre-warmed container pools. Per-language queues. Leaderboard with SSE push. Dedicated contest node pool. Contest-time traffic isolation from normal users.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is not storing problems. It is safely running huge bursts of user code while still returning results fast.

There are three main scaling pain points. First, code execution is CPU heavy and spiky, especially during contests, so a wave of submissions can overwhelm workers much faster than normal page reads would. Second, every submission is untrusted code, so you need strong isolation, timeouts, and resource limits, which makes execution slower and more expensive than a normal backend request. Third, live leaderboards can create a read storm if thousands of users poll every few seconds, so you do not want to rebuild rankings from the main database on every request.

A good mental model is this. LeetCode is hard because it mixes a fairly simple content app with a mini compute platform. The content side is easy to scale. The code runner and contest traffic are the parts that make it tricky.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: submit code, execute safely, return verdict (Accepted/TLE/WA/RE), leaderboard for contests. Out of scope: code completion, collaboration, plagiarism detection.
- **Submissions are async — always** — Never execute user code in the request thread. POST /submit returns a submission_id immediately. Client polls GET /submissions/:id. Queue + workers is the only correct pattern.
- **Sandbox is the hard part** — Docker + seccomp profile: block socket syscalls (no network), hard CPU + memory cgroups, OOM kill at container level, read-only filesystem, PID limit. Name all four constraints.
- **Container pre-warming** — Cold-start Docker container: ~100ms. Pre-warm a pool sized to P99 submission rate. For contests: pre-scale 30 min before start. Autoscale by queue depth, not CPU.
- **Per-language worker pools** — Python submissions take 5-10× longer than C++. Single mixed queue starves fast languages. Separate pools per language, sized by submission volume mix.
- **Leaderboard — Redis sorted set** — ZADD contest:{id} score user_id. ZREVRANK for user rank in O(log N). Redis handles 10K leaderboard QPS trivially. SSE push for live updates — never poll.
- **Failure mode to name** — Worker crashes mid-execution: job requeues via SQS visibility timeout. Duplicate execution is safe because verdict is deterministic — same code + same tests always gives same result.

> Lead with the sandbox — it's what makes this system unusual. 'The hard part isn't the queue, it's safely running untrusted code.' Name seccomp, cgroups, and no-network in your opening.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Containers vs VMs** — Containers are faster and cheaper. Tradeoff: need careful seccomp sandboxing. VMs are safer but slower.

**Queue + async vs sync execution** — Queue is more reliable under spikes and gives retries. Tradeoff: client must poll for result.

**Pre-warmed pool vs cold-start containers** — Pre-warmed containers eliminate cold-start latency (~100ms saved) but waste compute when idle. For contest traffic with predictable spikes, pre-warming is worth it. For steady-state, autoscale from zero.

**Per-language queues vs single queue** — Single queue is simpler. Per-language queues prevent slow Python submissions from starving fast C++ workers — tail latency is dramatically better with language affinity.

> Containers over VMs for speed and cost. Pre-warmed pools over cold-start for contest UX. Per-language queues over single queue for fair tail latency. Async over sync because execution takes seconds.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Secure code execution — isolation, resource limits, and the sandbox design
_The defining problem is safely running untrusted code_

> [!CAUTION]
> **🔴 Weak** — Docker containers
>
> [!WARNING]
> **🟡 Strong** — Docker with specific security configuration: (1) seccomp profile (restrict allowed syscalls to the minimum needed — block socket, fork beyond a count, exec of new binaries), (2) no network namespace (--network none), (3) read-only filesystem, (4) cgroups for CPU time limit and memory limit, (5) OOM kill at container level
>
> [!TIP]
> **🟢 Staff+** — VMs give stronger isolation (separate kernel) at the cost of 5-10s startup vs. Docker's <100ms. Firecracker microVMs (used by AWS Lambda) give VM-level isolation at near-container startup speed — the best security/performance tradeoff. For a real coding judge: Docker + seccomp is the standard production answer. Name what your seccomp profile blocks explicitly: socket syscalls (no network), fork/clone beyond the PID limit (no fork bombs), mount syscalls (no filesystem escapes)


#### Deep dive 2: Handling submission spikes — contest traffic at 5,000 QPS
_Contest submissions: 100K users × 5 submissions in 2 hours = 250K total. In the first 10 minutes of a contest, most submissions happen → spike to 5,000 QPS_

> [!CAUTION]
> **🔴 Weak** — scale workers
>
> [!WARNING]
> **🟡 Strong** — SQS queue decouples submission acceptance from execution. API accepts submission immediately (synchronous, <10ms), enqueues job, returns submission ID. Client polls GET /submissions/:id. Queue depth × avg execution time / worker count = queue latency. At 5,000 QPS with 10s avg execution and 200 workers: 5,000 × 10 / 200 = 250 second latency at peak. Fix: pre-scale workers before contest start (predictable traffic pattern). Worker autoscaling metric: SQS queue depth, not CPU
>
> [!TIP]
> **🟢 Staff+** — separate worker pools by language (Python workers, C++ workers) because Python submissions take 5-10× longer than C++ — mixed queue starves C++ users. Per-language queue with proportional worker allocation


#### Deep dive 3: Leaderboard at real-time scale — Redis sorted sets under contest stress
_During a contest, 100K users polling leaderboard every 10 seconds = 10K QPS on one sorted set (the leaderboard is a single ZREVRANGE key)_

> [!CAUTION]
> **🔴 Weak** — cache the leaderboard
>
> [!WARNING]
> **🟡 Strong** — tiered caching — Redis sorted set is the live source, but serve reads from a snapshot cached with 5-second TTL in each app server's local process cache. Push leaderboard updates via SSE to subscribed users rather than polling (eliminates 90% of reads)
>
> [!TIP]
> **🟢 Staff+** — the leaderboard sorted set is a hot key — all reads go to one Redis shard. For a contest with 100K participants this is manageable; for a global contest with 1M: shard the leaderboard by rank range (top 100 is served from one shard, ranks 100-1000 from another) and merge at the API layer. Alternatively: serve approximate leaderboards (top 10% exact, rest approximate from a lower-frequency snapshot) — users care most about top positions


_Why the deep dives connect to the scaling problem: "Safe execution farm plus contest traffic spikes." Each deep dive addresses one dimension._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Sandbox-first script.

2. "Clarifying questions: are we designing for a general OJ — all languages — or focused on a core set? And is the main challenge the execution security, the contest traffic spike, or both?"

3. "Good — all major languages, both challenges matter. Core features: submit code, execute safely, return verdict, contest leaderboard. Out of scope: code editor features, plagiarism detection."

4. "The unusual constraint here: user-submitted code is untrusted. The sandbox design is more interesting than the queue design. I'd lead with that."

5. "Sandbox: Docker container with seccomp profile — block socket syscalls (no network), hard cgroups for CPU time and memory, OOM kill at container level, read-only filesystem, PID limit against fork bombs. Pre-warm a container pool to eliminate cold-start latency."

6. "Queue: SQS between Submit API and worker pool. Submit API acknowledges immediately with a submission_id. Workers execute and write verdict to Redis (TTL 1hr). Client polls GET /submissions/:id."

7. "Per-language queues: Python runs 5-10× slower than C++. Mixed queue starves C++ users. Separate pools per language, sized by submission volume. Autoscale each pool independently by queue depth."

8. "Contest spikes: predictable traffic — pre-scale 30 minutes before start. Contest leaderboard in Redis sorted set with SSE push. Never poll leaderboard during a contest — too much QPS on one key."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |   Web / Mobile    |
                         |      Client       |
                         +---------+---------+
                                   |
                     GET problems, submit code, poll
                                   |
                         +---------v---------+
                         |    API Server      |
                         |  auth from JWT     |
                         |  problem APIs      |
                         |  submit API        |
                         |  leaderboard API   |
                         +----+---------+-----+
                              |         |
                 read problems|         |read submission status
                              |         |
                    +---------v--+   +--v----------------+
                    | Problems DB |   |  Submissions DB   |
                    | DynamoDB    |   | results, code,    |
                    | problems,   |   | passed, metadata  |
                    | test cases, |   +---------+---------+
                    | code stubs  |             |
                    +------------ +             |
                                                |
                                   enqueue job  |
                                                |
                                      +---------v---------+
                                      |   Job Queue        |
                                      |   SQS or similar   |
                                      +---------+---------+
                                                |
                                          pull job
                                                |
                                      +---------v---------+
                                      | Submission Worker  |
                                      | picks runtime      |
                                      | loads problem      |
                                      | runs test harness  |
                                      +----+----------+----+
                                           |          |
                              execute code  |          | update leaderboard
                                           |          |
                     +---------------------v--+    +--v------------------+
                     | Sandboxed Containers    |    | Redis Sorted Set    |
                     | python, java, js, etc  |    | competition ranks   |
                     | CPU and memory limits  |    | fast top N reads    |
                     | no network             |    +---------+-----------+
                     | timeout enforced       |              |
                     +-----------+------------+              |
                                 |                           |
                          stdout or result                   |
                                 |                           |
                                 +-------------+-------------+
                                               |
                                      +--------v--------+
                                      |   API Server    |
                                      | returns status  |
                                      | to polling      |
                                      +--------+--------+
                                               |
                                      +--------v--------+
                                      |     Client      |
                                      | shows result    |
                                      | polls leaderboard|
                                      +-----------------+
```

If you want the interview version, I would draw the simpler version first. Start with Client, API Server, Problems DB, Submissions DB, Queue, Worker, Sandboxed Containers, and Redis for leaderboard. Then explain that problem reads are synchronous, but submission execution is asynchronous because code runs for seconds and needs isolation.

The main idea is simple. Reads go straight through the API. Code submission goes through a queue to workers, workers execute inside locked down containers, results are stored in the submissions database, and leaderboard reads come from Redis instead of recomputing from the database every time.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-8)
