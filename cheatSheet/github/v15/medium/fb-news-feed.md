# FB News Feed

**Medium** · Fan-out on write · Hybrid push/pull · Celebrity problem

Tags: `Redis sorted set`, `Kafka`, `Cassandra`, `Fan-out on write`, `Hybrid push/pull`

_See also: Google News · feed aggregation overlap_

## Data flow

A new post writes to Cassandra then publishes to Kafka. An async fan-out worker prepends the post ID into each follower's Redis sorted set. Feed read = ZREVRANGE. Celebrity problem: accounts above a threshold are skipped at write time, pulled at read time and merged.


> Celeb >1M followers: skip push → pull at read time  |  Feed read = ZREVRANGE (O(1))

## Architecture diagram

```
Clients
  |
  v
API Gateway / Load Balancer
  |
  +-------------------+-------------------+-------------------+
  |                   |                   |
  v                   v                   v
Post Service      Follow Service      Feed Service
  |                   |                   |
  |                   |                   |
  v                   v                   v
Post Table         Follow Table        Precomputed Feed Table
DynamoDB           DynamoDB            DynamoDB
PK postId          PK userFollowing    PK userId
                   SK userFollowed     value recent postIds
                   GSI userFollowed

Post Table GSI
PK creatorId
SK createdAt

Write path for new post
-----------------------
User -> Post Service -> Post Table
                     -> Queue message with postId, creatorId

                         v
                    SQS / Queue
                         |
                         v
                    Feed Workers
                         |
          +--------------+--------------+
          |                             |
          v                             v
   Follow Table GSI              Precomputed Feed Table
   get followers                 prepend new postId

Read path for feed
------------------
User -> Feed Service
     -> read precomputed feed for user
     -> for non-precomputed celebrity accounts, query recent posts by creatorId from Post Table GSI
     -> fetch post objects by postId
     -> merge + sort by createdAt
     -> return page with next cursor

Hot post read protection
------------------------
Feed Service
  |
  v
Replicated Redis Cache
  |
  v
Post Table
```

If you want the best interview version, I would say this out loud as one sentence. Most users read from a precomputed feed, most posts are fanned out asynchronously on write, and celebrity accounts fall back to partial fan-out on read.

If you want, I can also give you a smaller interview-sized sketch that fits in 10 to 12 lines.


---

<details open>
<summary><strong>Problem</strong></summary>

Showing each user a personalized list of recent posts from people they follow, fast and at massive scale.

The hard part: fan-out — one post can need to appear in millions of followers' feeds.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Fan-out worker falls behind during a celebrity's viral post**

Millions of followers don't see the post in their feed for minutes. Feed appears stale.

_Fix:_ Celebrity threshold routing: any user above X followers bypasses fan-out entirely. Pull at read time. Worker backpressure handling with priority queues.

**A user's Redis feed sorted set grows unbounded**

ZREVRANGE on a set with 100K entries is slow. Memory grows for power users who follow thousands of people.

_Fix:_ Cap feed at last 1,000 post IDs per user. Trim on every fan-out write (ZREMRANGEBYRANK). Older posts are always fetchable from Cassandra.

**Hot post (viral content) DDOSes Cassandra**

One post_id is requested by millions of simultaneous users. Single Cassandra partition is hammered.

_Fix:_ Dedicated post cache layer (Redis or Memcached) in front of Cassandra. Viral post detection: if read QPS for a post_id exceeds threshold, promote to L1 cache.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 3B users, 10% DAU = 300M DAU, avg 5 feed reads/day, 1 post/week/user |
| Read QPS | 300M × 5 / 86400 ≈ 17,400 feed read QPS |
| Write QPS | 3B / 7 / 86400 ≈ 5,000 post write QPS → fan-out amplification: 5,000 × avg 200 followers = 1M feed write QPS |
| Storage | Feed cache: 3B users × 1,000 post IDs × 8 bytes ≈ 24 TB Redis — requires Redis cluster with sharding |
| Cache math | Celebrity with 100M followers × 1 post = 100M fan-out writes at 1M/s = 100 seconds to fan out. Too slow — pull model for celebs. |
| Verdict | Fan-out write amplification is the defining constraint. Hybrid model is not optional — it's mathematically required for celebrity accounts. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Fan-out on write vs. fan-out on read vs. hybrid**

→ Hybrid: push for normal users, pull for celebrities

Push-only: celebrity post → 100M writes → catastrophic. Pull-only: feed read → hundreds of DB queries to assemble timeline → too slow. Hybrid: threshold at ~100K followers (tunable) separates the two regimes.

_Revisit when:_ Threshold is a config value, not code. Adjust based on observed fan-out worker lag.

**Redis sorted set vs. Cassandra-backed feed**

→ Redis sorted set per user (score=timestamp)

Feed reads must be sub-100ms. Cassandra read latency ~1ms but requires complex fan-in query. Redis ZREVRANGE is O(log N + K) from in-memory — consistently sub-ms.

_Revisit when:_ For low-activity users (follow few people), on-demand Cassandra query is cheaper than maintaining a Redis entry.

**Ranking: chronological vs. ML**

→ Chronological for v1, ML ranking layer on top

Chronological is simple and understandable. ML ranking is a separate concern — it sits above the retrieval layer and re-scores candidates before serving. Don't conflate retrieval with ranking in the design.

_Revisit when:_ Always run ML ranking as a separate service to avoid coupling.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a user who follows 10,000 people?**

Fan-out on write to all 10K is expensive but one-time per post. The real issue is feed read: ZREVRANGE across 10K followed users' posts merged together. Solution: pre-merge into the user's own sorted set at write time. Reading is always O(1) regardless of follow count.

**What happens if the fan-out worker crashes mid-fan-out?**

Kafka provides at-least-once delivery. Fan-out workers are idempotent — writing the same post_id twice to a Redis sorted set with the same score is a no-op. On restart, worker replays from last committed Kafka offset.

**How do you serve the feed if Redis is unavailable?**

Fallback to on-demand Cassandra assembly with a lower quality (higher latency) feed. Acceptable degradation. Redis failure should be rare with proper replication.

**How do you handle edits or deletions of posts?**

Soft delete: mark post as deleted in Cassandra. Feed service filters deleted posts at read time. Don't try to fan-out deletes — it's the same amplification problem. Users may briefly see deleted posts until their feed refreshes.

**How would you add ranked (non-chronological) feed?**

Keep retrieval layer unchanged — Redis still stores post_id candidates. Add ranking service: takes top-N candidates from Redis, fetches features (engagement, recency, relationship strength), applies ML model, returns re-scored top-K. Separate concern, separate service.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Fan-out on read: SELECT posts FROM follows WHERE user_id IN (...) ORDER BY created_at. Works up to ~100 follows per user. Breaks at scale.

**v2 — Fan-out on write** — Kafka + async fan-out workers. Redis sorted sets per user. Handles normal users. Celebrity accounts still problematic.

**v3 — Hybrid + ranking** — Hybrid fan-out with celebrity threshold. ML ranking layer. Feed capped at 1,000 posts. Viral post detection and L1 caching. Hot key protection.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is fan-out. In FB News Feed, one user action can explode into huge work either when you read the feed or when you write a new post.

There are two core scaling pain points. First, feed reads can be expensive because to build one timeline you may need posts from a very large number of followed users, then merge and sort them fast. That is fan-out on read. Second, feed writes can also be expensive because a user with millions of followers may force you to update millions of feeds when they post. That is fan-out on write.

A third issue is skew. Most posts are quiet, but a few become very hot, so one post or one celebrity account can create uneven load on your databases and caches. So the big idea you should say in an interview is that News Feed is hard because of massive fan-out, hot keys, and the trade-off between precomputing feeds for fast reads versus computing them later for cheaper writes.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Goal** — Show each user a personalized list of recent posts from people they follow.
- **Default scaling move** — Precompute feeds on write. Store a bounded recent feed per user in Redis sorted set.
- **Write path** — New post → Cassandra → Kafka → workers update follower feeds asynchronously.
- **Celebrity fix** — Don't fan out to huge accounts' followers. Pull their recent posts at read time and merge.
- **Hot post fix** — Cache in front of post storage so viral posts don't hammer the DB.

> Mental model: hybrid fan-out. Normal users = fan-out on write. Celebrity users = fan-out on read.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Fan-out on write vs fan-out on read** — Fan-out on write: fast feed reads, expensive for users with many followers. Fan-out on read: cheap writes, slow reads at scale.

**Chronological vs ML-ranked feed** — Chronological is simple, predictable, no model needed. ML ranking improves engagement but adds a two-stage retrieval+scoring pipeline and requires feature infrastructure.

**Cassandra vs PostgreSQL for post store** — Cassandra handles write-heavy append-only workloads well — posts are written once, read many times, partitioned by user. PostgreSQL struggles at Cassandra-scale fan-out writes.

**Bounded feed (top 1K) vs unbounded** — Unbounded Redis sorted set per user grows forever — memory cost becomes prohibitive. Capping at 1K entries with ZREMRANGEBYRANK bounds cost; older posts are always fetchable from Cassandra.

> "Use hybrid fan-out. Precompute for normal users, pull at read time for celebrities."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Fan-out architecture — the push/pull/hybrid decision
> [!CAUTION]
> **🔴 Weak** — always fan-out on write — pre-compute every follower's feed on every post
>
> [!WARNING]
> **🟡 Strong** — the core hard problem is fan-out: one post potentially needs to update millions of feeds. Weak answer: always fan-out on write. Strong answer: articulate the full tradeoff space. Fan-out on write: fast reads (pre-computed), expensive writes (proportional to follower count). Fan-out on read: cheap writes, expensive reads (O(followed_users) per feed request). Hybrid: fan-out on write for normal users, pull on read for celebrities above a threshold
>
> [!TIP]
> **🟢 Staff+** — the threshold is a configuration value, not a code decision — it should be tunable based on observed fan-out worker lag. The math: if a fan-out worker processes 10K writes/second and delivery SLA is 5 minutes, the maximum safe follower count for push fan-out is 10K × 300 = 3M. Any account above that threshold uses pull. At read time: fetch user's pre-built feed from Redis, also fetch the last N posts from each celebrity they follow from Cassandra, merge and sort. The merge is O(C × log C) where C is the number of celebrity accounts — typically small, so fast


#### Deep dive 2: Feed storage and retrieval — Redis sorted sets at petabyte scale
> [!CAUTION]
> **🔴 Weak** — query Cassandra directly on every feed read — join followed users' posts and sort
>
> [!WARNING]
> **🟡 Strong** — each user has a Redis sorted set keyed by user_id with post_ids as members and publish_timestamp as score. ZREVRANGE returns the feed in reverse-chronological order in O(log N + K) time
>
> [!TIP]
> **🟢 Staff+** — concerns: (1) memory: 3B users × 1,000 post IDs × 8 bytes = 24 TB of Redis storage — requires a large Redis cluster with sharding by user_id. Cost is significant — only store the most recent 1,000 posts per user (ZREMRANGEBYRANK after every ZADD to trim). (2) Hot keys: users followed by millions of other users have their post IDs written to millions of sorted sets concurrently — this is the fan-out bottleneck, not the sorted set read. (3) Cold users: users who haven't logged in for 30 days don't need a live Redis feed — evict cold feed entries, rebuild from Cassandra on next login


#### Deep dive 3: ML ranking — candidate retrieval vs. scoring separation
_In production, chronological feed is deprecated in favor of ML-ranked feed_

> [!CAUTION]
> **🔴 Weak** — Query the database on every feed request.
>
> [!WARNING]
> **🟡 Strong** — In production, chronological feed is deprecated in favor of ML-ranked feed
>
> [!TIP]
> **🟢 Staff+** — this is a two-stage architecture. Stage 1 (retrieval): pull top-N candidates from Redis sorted set — fast, based on recency signal only. Stage 2 (ranking): pass candidates to a separate ranking service that scores each post using features (engagement rate, relationship strength, content type, recency, user interest signals). Serves top-K ranked results. These two stages are explicitly separate services with separate scaling characteristics: retrieval is read-heavy and latency-critical, ranking is compute-heavy and can tolerate 50-100ms. Staff+ architectural point: never embed ML ranking logic in the feed service — they change at different cadences, are owned by different teams, and have different failure modes. The interface is clean: retrieval service returns candidates, ranking service returns scores


_Why the deep dives connect to the scaling problem: "Massive fan-out and hot keys." Deep dive 1 solves fan-out architecture. Deep dive 2 solves storage and retrieval. Deep dive 3 solves the product quality layer on top._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Fan-out-first script.

2. "Clarifying questions: are we designing for a social network at Facebook scale — billions of users, celebrity accounts with hundreds of millions of followers? And is the feed chronological or ranked?"

3. "Good — FB scale, ranked feed. Core features: user creates post, feed shows recent posts from followed accounts ranked by relevance. Out of scope: Stories, Groups, ads injection (unless asked)."

4. "Scale: 3B users, 300M DAU, 5K post writes/sec, 17,400 feed read QPS. The defining constraint: fan-out. One post to an account with 50M followers = 50M Redis writes. That's the design problem."

5. "Write path: post → Cassandra (durable) → Kafka → fan-out workers → push post_id to each follower's Redis sorted set. Fast reads: ZREVRANGE is O(1) per feed read."

6. "Celebrity problem: any account above ~1M followers bypasses fan-out. At read time, pull their last N posts from Cassandra and merge with the pre-built feed. Merge cost is O(C × log C) where C is the number of followed celebrity accounts — typically < 10."

7. "ML ranking: two-stage. Stage 1 (retrieval): ZREVRANGE from Redis — fast, recency-sorted, returns top-1000 candidates. Stage 2 (ranking): separate service scores each candidate using engagement signals, relationship strength, content type. Returns top-50. Keep these stages strictly separate — they change at different cadences."

8. "Key failure mode to name: fan-out worker falls behind during a viral event. Fix: celebrity threshold is a config value — lower it dynamically when worker lag exceeds 2 minutes. This is the operational lever that keeps the system stable under load."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Clients
  |
  v
API Gateway / Load Balancer
  |
  +-------------------+-------------------+-------------------+
  |                   |                   |
  v                   v                   v
Post Service      Follow Service      Feed Service
  |                   |                   |
  |                   |                   |
  v                   v                   v
Post Table         Follow Table        Precomputed Feed Table
DynamoDB           DynamoDB            DynamoDB
PK postId          PK userFollowing    PK userId
                   SK userFollowed     value recent postIds
                   GSI userFollowed

Post Table GSI
PK creatorId
SK createdAt

Write path for new post
-----------------------
User -> Post Service -> Post Table
                     -> Queue message with postId, creatorId

                         v
                    SQS / Queue
                         |
                         v
                    Feed Workers
                         |
          +--------------+--------------+
          |                             |
          v                             v
   Follow Table GSI              Precomputed Feed Table
   get followers                 prepend new postId

Read path for feed
------------------
User -> Feed Service
     -> read precomputed feed for user
     -> for non-precomputed celebrity accounts, query recent posts by creatorId from Post Table GSI
     -> fetch post objects by postId
     -> merge + sort by createdAt
     -> return page with next cursor

Hot post read protection
------------------------
Feed Service
  |
  v
Replicated Redis Cache
  |
  v
Post Table
```

If you want the best interview version, I would say this out loud as one sentence. Most users read from a precomputed feed, most posts are fanned out asynchronously on write, and celebrity accounts fall back to partial fan-out on read.

If you want, I can also give you a smaller interview-sized sketch that fits in 10 to 12 lines.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-6)
