# Distributed rate limiter

**Medium** · Token bucket · Sliding window · Redis Lua atomicity

Tags: `Redis Lua`, `Token Bucket`, `Sliding Window Counter`, `Fail open vs closed`

## Data flow

Every request hits the API gateway which runs a Lua script in Redis — atomically checks the counter and decrements it in a single operation, eliminating race conditions. Key = userId:window_id. Token Bucket: tokens refill at rate R, each request costs 1.


> Token Bucket: refill rate R, cost 1/req  |  Lua = atomic check+decrement  |  Fail open vs closed = design choice

## Architecture diagram

```
+----------------------+
                              |   Config Service     |
                              | rules and limits     |
                              +----------+-----------+
                                         |
                                   periodic sync
                                         |
+---------+      HTTPS      +------------v-------------+
| Clients | --------------> | API Gateway / LB Layer   |
| users   |                 | auth parse + rate limit  |
| IPs     |                 | check before app traffic |
+---------+                 +------+---------+---------+
                                   |         |
                     allow request |         | reject request
                                   |         |
                                   |         v
                                   |   +----------------------+
                                   |   | 429 Response Builder |
                                   |   | limit remaining reset|
                                   |   +----------------------+
                                   |
                                   v
                         +---------+----------+
                         | Backend Services   |
                         | social media APIs  |
                         +--------------------+

Inside the gateway rate limiter path

        extract client key
 userId or IP or apiKey + endpoint rule
                 |
                 v
      +----------+-----------+
      | Shard Router         |
      | hash client key      |
      +----------+-----------+
                 |
                 v
      +----------+-----------------------------------+
      | Redis Cluster                                |
      | shared bucket state across gateway instances |
      |                                              |
      |  shard 1     shard 2     shard 3     ...     |
      | +--------+  +--------+  +--------+           |
      | |alice   |  |bob     |  |carol   |           |
      | |tokens  |  |tokens  |  |tokens  |           |
      | |refill  |  |refill  |  |refill  |           |
      | +--------+  +--------+  +--------+           |
      +-------------------+--------------------------+
                          |
                 atomic Lua script
                          |
                          v
         read bucket -> refill tokens -> consume 1 -> return decision

Per shard HA

        +------------------+
        | Redis Primary    |
        +--------+---------+
                 |
             replicate
                 |
        +--------v---------+
        | Redis Replica    |
        +------------------+

Request flow
```

---

<details open>
<summary><strong>Problem</strong></summary>

A rate limiter stops one user, bot, or client from sending too many requests in a short time.

The hard parts: distributed enforcement across many app servers, atomic check+decrement without race conditions, and choosing the right algorithm.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis goes down — all counter state lost**

Two choices: fail open (allow all requests, lose rate limiting) or fail closed (deny all, break the product). Both are bad.

_Fix:_ Explicit choice: fail open for user-facing APIs (availability > protection), fail closed for auth endpoints (security > availability). Redis Sentinel for HA. Local in-process fallback limiter for fail-open path.

**Hot user / abusive IP hammers one Redis shard**

One shard gets 100× the write load. Latency spikes for all rate-limited requests on that shard.

_Fix:_ Consistent hash distributes users across shards. For known hot keys (viral API key, DDoS source IP), local in-process counter absorbs most checks. Redis is the shared truth, local cache is the fast path.

**Race condition: two servers check quota simultaneously and both allow a request that should be denied**

User gets 2× their quota allowance — rate limiter is ineffective.

_Fix:_ Lua script in Redis: INCR + TTL set + check, all atomic. Never do GET → check → SET as separate operations. Single-threaded Redis + Lua = no race possible.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10K API servers, 100K requests/s total, each request does 1 Redis check, limit per user = 1000 req/min |
| Read QPS | 100K rate limit checks/s — all go to Redis |
| Write QPS | 100K INCR operations/s — Redis handles ~500K ops/s on one node, fine for one shard |
| Storage | Active users × 1 counter × 8 bytes × 2 windows = trivial. 10M users × 16 bytes = 160 MB — fits in single Redis node RAM |
| Cache math | Local in-process cache: each of 10K servers caches hot user counters. Reduces Redis QPS by ~80% for hot users. Redis sees only cache misses and periodic sync. |
| Verdict | Single Redis node handles 100K checks/s comfortably. Scale by sharding when users exceed 100M active keys. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Token bucket vs. sliding window vs. fixed window**

→ Sliding window counter

Fixed window allows burst at boundary (double rate in 2s straddling window). Token bucket is accurate but harder to implement distributed. Sliding window: curr_count + prev_count × (1 - elapsed%) is accurate and simple.

_Revisit when:_ Token bucket if smooth burst tolerance is required (API that should allow short bursts).

**Rate limit at gateway vs. at service level**

→ API gateway layer

Single enforcement point. No need to add rate limiting logic to every service. Rejected requests never reach service layer — saves service resources too.

_Revisit when:_ Service-level limits needed for internal service-to-service calls that bypass the public gateway.

**Per-user vs. per-IP vs. per-API-key**

→ Per-API-key primary, per-IP secondary

Authenticated requests use API key (accounts for shared IPs like NAT gateways). Unauthenticated requests fall back to IP. This prevents one mobile carrier's shared IP from affecting millions of legitimate users.

_Revisit when:_ Add per-endpoint limits for expensive operations (e.g., batch endpoints = 10x cost multiplier).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle distributed Redis — do you need strong consistency across nodes?**

No. Slight over-allowance (10-20%) is acceptable for most rate limiters. Each shard holds counts for its key range. Race condition within a shard is eliminated by Lua. Race between shards is acceptable — user might get 110% of their quota in a rare race, not 1000%.

**What's your strategy for a DDoS with millions of unique IPs?**

Per-IP rate limiting at L7 (Nginx/HAProxy) or L3 (BGP blackholing for volumetric). This rate limiter is for application-layer limiting, not DDoS mitigation. Those are different systems. Always mention this distinction.

**How do you allow burst traffic (e.g., a user can burst to 2× for 10 seconds)?**

Token bucket with burst capacity: bucket size = sustained_rate × burst_window. User can drain the bucket instantly (burst), then refills at sustained_rate. This is what leaky bucket / token bucket is designed for — sliding window counter is harder to express burst with.

**How do you return accurate Retry-After headers?**

After INCR, read TTL on the window key. Return Retry-After: TTL seconds. For token bucket: (tokens_needed - tokens_available) / refill_rate = seconds to wait.

**How would you add per-plan rate limiting (free vs. paid tiers)?**

Store rate limit config per API key in a config service. Rate limiter fetches limit for the key (cached in local memory, refreshed every 60s). No code change needed to upgrade a customer's limits.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — In-process token bucket per API server. Simple, no coordination. Problem: each server has its own quota — user can get N × (num_servers) requests by hitting all servers.

**v2 — Distributed** — Redis Lua script for atomic sliding window. Single enforcement point at API gateway. Handles millions of users correctly.

**v3 — Optimized** — Local in-process cache reduces Redis QPS by 80%. Per-plan configurable limits. Graduated limits for burst. DDoS-specific L3/L7 filtering as separate concern.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that a rate limiter turns every incoming request into a fast, shared counter update. At small scale that sounds simple. At large scale, millions of requests all need low latency decisions, and those decisions must be consistent enough that one user cannot bypass limits just by hitting different servers.

There are three pain points you should call out. First, the state is write heavy. Every request updates a token bucket or counter, so your shared store can become the bottleneck. Second, correctness gets tricky under concurrency. Two servers can read the same remaining quota at once and both allow the request unless the whole read modify write step is atomic. Third, distribution makes it harder. If you shard by user or IP, you need all requests for that client to land on the same shard, and hot users or abusive IPs can overload one shard.

A good interview summary is this. Rate limiting is hard to scale because it needs very fast per request decisions, shared mutable state across many servers, and enough coordination to avoid race conditions without adding too much latency.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Enforce at gateway** — Single enforcement point at the API gateway layer.
- **Redis for shared state** — All app servers need to see the same counter.
- **Lua script = atomic** — Check + decrement in a single Lua script. No race condition.
- **Token bucket default** — Tokens refill at rate R, each request costs 1. Best default for most APIs.
- **Fail behavior** — If Redis is down: fail open (allow all) or fail closed (deny all). Name this explicitly.

> Mental model: Redis Lua script enforces atomically. Token bucket smooths bursts. Fail open vs closed is the hidden design choice.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Fixed window vs sliding window** — Fixed window has boundary burst risk. Sliding window is accurate but uses more memory.

**Token bucket vs sliding window** — Token bucket smooths bursts best. Sliding window is more accurate for rate enforcement.

**Fail open vs fail closed** — Fail open = allow all when Redis is down. Fail closed = deny all. No right answer — depends on use case.

> "Fixed window is simple, sliding window is accurate, token bucket smooths bursts. Choose based on your traffic pattern."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Algorithm selection — token bucket vs. sliding window vs. fixed window
> [!CAUTION]
> **🔴 Weak** — use fixed window counting — INCR a key, expire at the window boundary
>
> [!WARNING]
> **🟡 Strong** — the algorithm choice has real operational tradeoffs, not just theoretical ones. Fixed window: simplest (INCR key, expire at window boundary), but the "double spend" attack — a burst of N requests at window T-1 and N requests at window T+1 = 2N requests in a 2-second window while the limit is N per window. Sliding window counter: curr_window_count + (prev_window_count × fraction_of_prev_window_elapsed) ≈ accurate sliding window at low memory cost. Token bucket: refill rate R tokens per second, cost 1 per request, bucket size B allows bursts up to B. Leaky bucket: queue requests and process at fixed rate — smoothest, but adds latency
>
> [!TIP]
> **🟢 Staff+** — sliding window counter for most API rate limiting (accurate, cheap). Token bucket for APIs that explicitly want to allow short bursts (e.g., batch endpoints). Fixed window only for very simple use cases where boundary bursts are acceptable. The algorithm choice should be justified against your NFRs — don't just name it


#### Deep dive 2: Distributed atomicity — why Lua scripts are mandatory
_The race condition: two servers both read the same counter (value: 999), both check 999 < 1000 (pass), both increment to 1000. User gets 2× their quota. The naive GET → check → INCR is broken_

> [!CAUTION]
> **🔴 Weak** — use Redis transactions (MULTI/EXEC)
>
> [!WARNING]
> **🟡 Strong** — Redis Lua scripts — the script executes atomically on the Redis server; no other command can run between any two lines of the Lua script. The script: (1) LOLWUT or EXISTS to check the key, (2) GET the current count, (3) if count < limit, INCR and return allowed, else return denied. All as one atomic operation
>
> [!TIP]
> **🟢 Staff+** — why not MULTI/EXEC? MULTI/EXEC is optimistic — if another client modifies the key between WATCH and EXEC, the transaction fails and must retry. Under high concurrency this creates a hot retry loop. Lua is unconditional — no retry needed. INCR alone is atomic too, but only for the increment — the conditional check requires Lua


#### Deep dive 3: Failure modes and degradation strategy
> [!CAUTION]
> **🔴 Weak** — fail closed — return 429 to all requests when Redis is down
>
> [!WARNING]
> **🟡 Strong** — what happens when Redis is unavailable? This is the question that separates senior from staff. Options: (1) fail open — allow all requests. Better for user-facing APIs where availability > protection. (2) Fail closed — deny all requests with 429. Better for auth endpoints where one compromised request is worse than a temporary outage. (3) Local in-process fallback — each app server maintains its own token bucket. Allows N × (num_servers) total requests instead of N, but prevents total failure
>
> [!TIP]
> **🟢 Staff+** — design: the failure mode should be a configuration option, not a code decision. Different endpoints have different failure mode requirements. The rate limiter must be explicit about which mode it's in and expose this via metrics. Additionally: Redis Sentinel for HA (automatic failover in <30s); circuit breaker pattern at the rate limiter client so a slow Redis doesn't add latency to every API request


_Why the deep dives connect to the scaling problem: "Fast per-request shared counter decisions." Deep dive 1 solves algorithm correctness. Deep dive 2 solves distributed atomicity. Deep dive 3 solves failure handling._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Algorithm-first script.

2. "Clarifying questions: are we rate limiting by user, by IP, or by API key? And is the limit per-second, per-minute, or per-day? And what's the failure mode requirement — should the limiter fail open or closed?"

3. "Good — by API key, per-minute window. Failure mode: fail open for user-facing APIs, fail closed for auth endpoints. I'd make this a configuration option, not a code decision."

4. "Algorithm: sliding window counter. Fixed window has a boundary burst problem — 2N requests possible in 2 seconds straddling a boundary. Sliding window: count = curr_window + prev_window × (1 - elapsed%). Accurate and cheap."

5. "Implementation: Redis Lua script. The check-and-increment must be atomic — GET + check + INCR as separate operations has a TOCTOU race. Lua executes atomically on the Redis server, no race possible."

6. "Deployment: at the API gateway layer. Single enforcement point. Rejected requests never reach the service — saves downstream resources too. Service-level limits are a separate concern for internal traffic."

7. "Local in-process cache: for hot API keys, cache the counter locally with a 100ms TTL. Reduces Redis QPS by ~80%. Accept slight over-allowance (10-20%) in exchange for lower latency and less Redis load."

8. "Return Retry-After header on 429: after INCR, read TTL on the window key. Return Retry-After: TTL seconds. This is a small detail that signals production awareness."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                              |   Config Service     |
                              | rules and limits     |
                              +----------+-----------+
                                         |
                                   periodic sync
                                         |
+---------+      HTTPS      +------------v-------------+
| Clients | --------------> | API Gateway / LB Layer   |
| users   |                 | auth parse + rate limit  |
| IPs     |                 | check before app traffic |
+---------+                 +------+---------+---------+
                                   |         |
                     allow request |         | reject request
                                   |         |
                                   |         v
                                   |   +----------------------+
                                   |   | 429 Response Builder |
                                   |   | limit remaining reset|
                                   |   +----------------------+
                                   |
                                   v
                         +---------+----------+
                         | Backend Services   |
                         | social media APIs  |
                         +--------------------+

Inside the gateway rate limiter path

        extract client key
 userId or IP or apiKey + endpoint rule
                 |
                 v
      +----------+-----------+
      | Shard Router         |
      | hash client key      |
      +----------+-----------+
                 |
                 v
      +----------+-----------------------------------+
      | Redis Cluster                                |
      | shared bucket state across gateway instances |
      |                                              |
      |  shard 1     shard 2     shard 3     ...     |
      | +--------+  +--------+  +--------+           |
      | |alice   |  |bob     |  |carol   |           |
      | |tokens  |  |tokens  |  |tokens  |           |
      | |refill  |  |refill  |  |refill  |           |
      | +--------+  +--------+  +--------+           |
      +-------------------+--------------------------+
                          |
                 atomic Lua script
                          |
                          v
         read bucket -> refill tokens -> consume 1 -> return decision

Per shard HA

        +------------------+
        | Redis Primary    |
        +--------+---------+
                 |
             replicate
                 |
        +--------v---------+
        | Redis Replica    |
        +------------------+

Request flow
```


</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-9)
