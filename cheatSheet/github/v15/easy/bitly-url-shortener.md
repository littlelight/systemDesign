# Bitly — URL shortener

**Easy** · Scaling reads · Cache-aside · Base62

Tags: `Redis`, `PostgreSQL`, `Base62`, `Cache-aside`, `CDN`

_See also: v10 reference · URL shortener in System Cards_

## Data flow

A user submits a long URL. The API server hashes it, encodes seven characters in Base62, and writes the mapping to PostgreSQL and Redis. On redirect the service checks Redis first — reads outnumber writes ~100:1 so the cache absorbs almost all traffic. A cache miss falls back to PG, repopulates Redis, and returns a 302 redirect (not 301) so every click hits the server for analytics.


> Write: Base62(hash(longURL)) → Redis + PG  |  302 not 301 → preserves analytics

## Architecture diagram

```
+-------------------+
                    |      Clients      |
                    | browser mobile app|
                    +---------+---------+
                              |
                              v
                    +-------------------+
                    |   Load Balancer   |
                    +----+---------+----+
                         |         |
              write path |         | read path
                         v         v
              +----------------+  +----------------+
              | Write Service  |  |  Read Service  |
              +-------+--------+  +-------+--------+
                      |                   |
                      |                   |
                      v                   v
             +----------------+   +----------------+
             | Redis Counter  |   |  Redis Cache   |
             | atomic INCR    |   | short -> long  |
             +-------+--------+   +-------+--------+
                     |                    |
                     |                    | cache miss
                     |                    v
                     |           +--------------------+
                     +---------->|     Postgres       |
                                 | short_code PK      |
                                 | long_url           |
                                 | expiration         |
                                 +---------+----------+
                                           |
                                           v
                                 +--------------------+
                                 | Background Cleanup |
                                 | delete expired URLs|
                                 +--------------------+
```

If you want to say it out loud, keep it simple. Clients hit a load balancer. Writes go to a write service, which gets a unique ID from Redis Counter, converts it to base62, and stores the mapping in Postgres. Reads go to a read service, which checks Redis Cache first, falls back to Postgres on a miss, checks expiration, and returns a 302 redirect.

If you want a slightly stronger version, you could add a CDN in front of the read path for hot links, but I would start with the sketch above in an interview.


---

<details open>
<summary><strong>Problem</strong></summary>

Bitly solves short link creation and fast redirection at scale. You take a long URL, store a mapping to a shorter code, and when someone visits the short link the system looks up the original and redirects them.

The real challenges: making short codes unique, keeping redirects fast, and handling a read-heavy workload — links are clicked far more than they are created.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis goes down**

All redirects fall through to PostgreSQL. At peak traffic this overwhelms PG. Latency spikes from sub-ms to tens of ms.

_Fix:_ Fail open to DB reads. Set PG connection pool high. Add a second Redis replica. Circuit-break if PG > 80% capacity.

**Counter service fails (code generation)**

No new short URLs can be created. Existing redirects still work.

_Fix:_ Counter is a single point of failure. Mitigate with pre-allocated ID ranges per app server (each takes a batch of 1000 IDs). Graceful degradation: queue creation requests.

**Hot link (viral URL)**

Single short code hammers Redis then PG if evicted. One key exhausts connection pool.

_Fix:_ Local in-process cache on each app server for top-N keys. CDN caching for redirect responses (302 with short Cache-Control).


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500M total URLs, 100M DAU, average user clicks 10 short links/day |
| Read QPS | 100M × 10 / 86400 ≈ 11,600 read QPS |
| Write QPS | 10M new URLs/day / 86400 ≈ 116 write QPS — read:write ratio ~100:1 |
| Storage | 500M rows × 500 bytes (url + metadata) ≈ 250 GB — comfortably single PG node |
| Cache math | Top 20% of URLs get 80% of traffic → cache 100M URLs × 100 bytes ≈ 10 GB Redis — fits easily |
| Verdict | Single PG node fine for writes. Redis absorbs 99%+ of reads. Scale app servers horizontally for redirect serving. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Base62 counter over random hash**

→ Global counter + Base62

Guaranteed uniqueness, no collision handling, predictable length (7 chars at 62^7 = 3.5T URLs). Tradeoff: codes are guessable — acceptable for most use cases.

_Revisit when:_ Switch to random hash + collision retry if enumeration is a security concern.

**302 over 301 redirect**

→ 302 Temporary

301 caches permanently in browser — lose analytics, can't expire, can't update destinations. 302 hits server every time giving full control.

_Revisit when:_ Could offer 301 as an opt-in for customers who want to reduce load.

**Redis as cache, PG as source of truth**

→ Cache-aside pattern

Write-through would add latency on every creation. Cache-aside is simpler: write to PG, lazy-populate Redis on first read.

_Revisit when:_ Write-through if cache miss rate ever exceeds 5%.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**What happens if Redis is completely unavailable?**

Fail open to PostgreSQL. Short-term pain (higher latency) beats failing closed (all redirects return 5xx). Add a circuit breaker so if PG latency exceeds 500ms we start returning 503 with Retry-After.

**How do you handle a single viral URL causing a hot key?**

Two layers: local in-process LRU cache on each app server (top 1000 keys, zero network hops), and optionally a CDN caching 302 responses for a short TTL. The hot key never reaches Redis or PG.

**How would you handle 10× traffic suddenly?**

App servers are stateless — auto-scale horizontally immediately. Redis handles 10× without changes (it's in-memory). PG might need a read replica. The counter service is the only coordination point — pre-allocate large ID batches to reduce contention.

**How do you expire links?**

Store expiry timestamp in PG. On redirect, check expiry — if expired, return 410 Gone and delete from Redis. TTL the Redis key to match expiry time so it auto-evicts.

**How would you support custom aliases?**

Store alias in same URL table with a unique constraint. On creation: check alias not taken (SELECT FOR UPDATE or optimistic retry), store it. Downside: can't guarantee 7-char length for aliases.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: success rate, latency, active users. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: minutes of read unavailability acceptable; rebuild cache from DB. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single PostgreSQL + single app server. No cache. Counter in DB (SELECT MAX + 1). Handles up to ~1K QPS. Fine for early product.

**v2 — Scale reads** — Add Redis cache. Separate read and write app services. Counter service with pre-allocated ID batches. Handles 50K QPS reads. Deploy globally with regional Redis replicas.

**v3 — Optimize** — CDN for popular redirects. Local in-process cache for viral links. Async analytics pipeline (Kafka → ClickHouse). Custom domain support. Link expiry cleanup job. Handles 500K+ QPS.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that reads explode while writes stay small. A Bitly style system might create URLs slowly, but a single popular link can suddenly cause huge redirect traffic, so you need very fast lookups and a way to survive spikes.

The other tricky part is global uniqueness for short codes. If you scale the write path across many servers, they all need to agree on which code comes next or you risk collisions. So the two main scaling pain points are read traffic on redirects and coordination for code generation.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope** — URL in, short URL out. Short URL in, redirect out. Keep analytics and auth out of scope unless asked.
- **Key requirement** — Read-heavy system — optimize redirects first.
- **API** — POST /urls creates a short link. GET /{shortCode} redirects.
- **Data model** — short_code → long_url + created time + optional expiration. short_code is the primary key.
- **Code generation** — Global counter + base62 is the best default. Easy to explain, guarantees uniqueness.
- **Fast reads** — Redis cache in front of DB. Cache first, DB on miss.
- **Redirect** — Return 302 not 301 — keeps control and avoids permanent browser caching.
- **Expiration** — Check expiry on read. Return 410 Gone if expired. Match cache TTL to expiration.
- **Scale** — Stateless app servers behind a load balancer. Split read and write services if needed.

> Memory hook: counter, cache, redirect. Say those three and you have covered the heart of the design.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**First** — Counter vs hash. Counter + base62 guarantees uniqueness and is easy to explain. Tradeoff: needs coordination, codes are predictable. Hash is more distributed but requires collision handling.

**Second** — DB only vs cache + DB. Cache + DB is right for a read-heavy system. Tradeoff: extra complexity around misses, eviction, and expired links.

**Third** — 302 vs 301. 302 keeps control server-side. Tradeoff: 301 reduces repeat load but makes expiration harder.

> "I picked the simplest design that meets the requirements. Main tradeoffs: uniqueness vs coordination, speed vs complexity, control vs caching behavior."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Unique short code generation (the core hard problem)
_The scaling pain here is coordination — every server needs a unique code without collision_

> [!CAUTION]
> **🔴 Weak** — Describe hashing and move on
>
> [!WARNING]
> **🟡 Strong** — articulate a progression of approaches and their tradeoffs. Start with MD5/SHA-256 + Base62 truncation: simple but has collision probability that grows as n/|S|. The fix is a uniqueness check + retry, which adds a DB roundtrip. The better default is a global atomic counter in Redis (INCR is single-threaded, atomic, eliminates collisions entirely) with Base62 encoding. The staff-level concern is the counter as a single point of failure: pre-allocated ID ranges per app server (each batch-fetches 1,000 IDs, eliminating per-request Redis coordination), and the counter node failing means temporary unavailability of writes but no data loss
>
> [!TIP]
> **🟢 Staff+** — Mention the predictability risk: sequential codes are enumerable. Mitigation: XOR the counter with a secret key before encoding, or accept that short URLs are meant to be shared publicly anyway


#### Deep dive 2: Fast redirects at scale (the read scaling problem)
> [!CAUTION]
> **🔴 Weak** — add a database index on short_code and query on every redirect
>
> [!WARNING]
> **🟡 Strong** — this is what makes Bitly hard — 100M DAU clicking links 10× per day = 11,600 read QPS, with viral links creating hot keys at 100× that rate. A database index on short_code is necessary but not sufficient. The right answer layers caching: Redis with cache-aside pattern absorbs 99% of reads
>
> [!TIP]
> **🟢 Staff+** — cache TTL should be set equal to or shorter than URL expiration so expired URLs auto-evict from cache (otherwise you serve expired redirects). For hot keys (viral links), add a local in-process LRU cache on each app server — zero network hops, handles traffic spikes that would otherwise hammer Redis. For global-scale: CDN caching of the 302 response itself with a short Cache-Control header removes Redis from the critical path entirely for the most popular links


#### Deep dive 3: Scaling to 1B URLs and 100M DAU (the DB and infrastructure problem)
> [!CAUTION]
> **🔴 Weak** — scale the database vertically and add read replicas
>
> [!WARNING]
> **🟡 Strong** — storage is not the hard part — 1B × 500 bytes = 500 GB, comfortably on a single PostgreSQL node with read replicas. The hard part is write coordination for the counter and horizontal scaling of stateless redirect servers. Senior answer: stateless redirect service behind a load balancer, Redis cluster for the counter, PG primary + read replicas
>
> [!TIP]
> **🟢 Staff+** — the counter service is the hidden bottleneck — pre-allocated ID ranges mean app servers can generate IDs locally without any network call (counter node becomes a periodic flush rather than a per-request bottleneck). Multi-region: deploy read replicas and Redis caches regionally, write counter remains in one region (acceptable because writes are rare). For URL expiration at scale: don't run a full table scan — use a time-indexed expiry column and batch-delete expired rows during low-traffic windows


_Why the deep dives connect to the scaling problem: The scaling pain is "reads explode while writes stay small." Deep dive 1 solves write uniqueness. Deep dive 2 solves read performance. Deep dive 3 solves infrastructure capacity. Name this arc explicitly in the interview — it shows architectural thinking, not just pattern recall._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Requirements-first script.

2. "Before I design: a few quick clarifying questions. Are we building a public service like Bitly, or an internal tool? And do we care about analytics — click counts by geography, referrer — or just the redirect?"

3. "Great — public service, basic analytics out of scope for now. So my core features are: create a short URL from a long URL, support optional custom alias and expiration, and redirect via the short URL. I'll keep auth and abuse prevention out of scope unless you want them."

4. "For non-functionals: I'd assume 100M DAU, 1B total URLs, read-heavy — maybe 100:1 reads to writes. The main NFRs are fast redirects (sub-100ms), high availability, and globally unique short codes."

5. "API: POST /urls → {shortCode}. GET /{shortCode} → 302 redirect. That's the core contract."

6. "Data model: one table keyed by short_code — stores long_url, created_at, optional expiry, optional user_id. short_code is the primary key, long_url has an index for reverse lookup."

7. "Code generation: I'd use a global counter + Base62 encoding. Counter gives uniqueness guarantees without collision handling. Base62 gives 7 characters for 3.5 trillion possible codes — plenty. Tradeoff: codes are predictable, but that's fine for a public URL shortener."

8. "For read scaling: Redis cache with cache-aside. On redirect, check Redis first. Miss → hit PostgreSQL → populate Redis. 99% of reads served from cache. For viral links: local in-process LRU on each app server — eliminates Redis entirely for the hottest codes."

9. "302 over 301 — I want redirects to hit our server so we can track analytics if needed later. 301 caches permanently in the browser and we lose that."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                    |      Clients      |
                    | browser mobile app|
                    +---------+---------+
                              |
                              v
                    +-------------------+
                    |   Load Balancer   |
                    +----+---------+----+
                         |         |
              write path |         | read path
                         v         v
              +----------------+  +----------------+
              | Write Service  |  |  Read Service  |
              +-------+--------+  +-------+--------+
                      |                   |
                      |                   |
                      v                   v
             +----------------+   +----------------+
             | Redis Counter  |   |  Redis Cache   |
             | atomic INCR    |   | short -> long  |
             +-------+--------+   +-------+--------+
                     |                    |
                     |                    | cache miss
                     |                    v
                     |           +--------------------+
                     +---------->|     Postgres       |
                                 | short_code PK      |
                                 | long_url           |
                                 | expiration         |
                                 +---------+----------+
                                           |
                                           v
                                 +--------------------+
                                 | Background Cleanup |
                                 | delete expired URLs|
                                 +--------------------+
```

If you want to say it out loud, keep it simple. Clients hit a load balancer. Writes go to a write service, which gets a unique ID from Redis Counter, converts it to base62, and stores the mapping in Postgres. Reads go to a read service, which checks Redis Cache first, falls back to Postgres on a miss, checks expiration, and returns a 302 redirect.

If you want a slightly stronger version, you could add a CDN in front of the read path for hot links, but I would start with the sketch above in an interview.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-0)
