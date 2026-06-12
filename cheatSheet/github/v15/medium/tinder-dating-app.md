# Tinder — dating app

**Medium** · Geospatial deck · Like detection · Match notification

Tags: `Redis GEO`, `Cassandra`, `WebSocket`, `Geohash`, `Pre-computed deck`

## Data flow

Swipe deck is pre-computed via GEORADIUS + preference filters, cached per user. On a like: write to Cassandra + atomically check if the other person already liked back. Mutual = match → WebSocket push to both users.


> Deck pre-computed per user  |  GEORADIUS + filter prefs + cache  |  Match = atomic SETNX mutual check

## Architecture diagram

```
+-------------------+
                         |   Mobile Client   |
                         | profile, feed,    |
                         | swipe, match UI   |
                         +---------+---------+
                                   |
                            HTTPS  |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         | auth, routing     |
                         +----+---------+----+
                              |         |
                 +------------+         +-----------------+
                 |                                        |
                 v                                        v
      +---------------------+                  +----------------------+
      |   Profile Service   |                  |    Swipe Service     |
      | prefs, profile data |                  | record swipe, detect |
      +----------+----------+                  | match, emit events   |
                 |                             +----+-----------+-----+
                 |                                  |           |
                 v                                  |           |
      +---------------------+                       |           v
      |   User DB           |                       |   +------------------+
      | profiles, prefs     |                       |   | Notification Svc |
      +---------------------+                       |   | APNS or FCM      |
                                                    |   +--------+---------+
                                                    |            |
                                                    |            v
                                                    |    +---------------+
                                                    |    | Other User     |
                                                    |    | push device    |
                                                    |    +---------------+
                                                    |
                                                    v
                                         +----------------------+
                                         | Redis Match Store    |
                                         | atomic pair check    |
                                         | low latency match    |
                                         +----------+-----------+
                                                    |
                                                    v
                                         +----------------------+
                                         | Swipe DB             |
                                         | Cassandra style      |
                                         | durable swipe log    |
                                         +----------------------+


                 Feed generation path

      +---------------------+
      |   Feed Service      |
      | build candidate set |
      +-----+---------+-----+
            |         |
            |         v
            |   +----------------------+
            |   | Feed Cache           |
            |   | precomputed stacks   |
            |   +----------------------+
            |
            v
   +-------------------------+
   | Search Index            |
   | geo plus preference     |
   | filtering               |
   +-----------+-------------+
               |
               v
      +---------------------+
      | User DB / CDC sync   |
      | profile updates flow |
      | into search index    |
      +---------------------+
```

The mental model is two main paths. One path serves profiles fast through feed cache plus a search index. The other path handles swipes safely through Redis for atomic match detection and Cassandra for durable swipe history.

If you want, I can also give you a simpler interview version with only 6 boxes, or a step by step swipe flow sketch.


---

<details open>
<summary><strong>Problem</strong></summary>

Tinder solves real-time recommendation and mutual match detection. Show relevant nearby profiles fast, record huge swipe volumes, and reliably detect when two users both say yes.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis GEO index too large for a dense city**

GEORADIUS in a city with 10M users returns too many candidates. Filter step is expensive.

_Fix:_ Narrow GEORADIUS radius. Apply age/preference filters at Redis level using sorted set intersection. Pre-filter by coarse geohash first.

**Swipe history grows unbounded per user**

Checking 'have I already swiped this profile?' requires scanning a large set. Slow.

_Fix:_ Bloom filter per user for seen profiles. O(1) check, small memory. False positives = occasionally hiding an unseen profile (acceptable). Exact check for match creation only.

**Match notification fails to deliver**

User swipes right, their match already swiped right, but neither gets notified. Match silently lost.

_Fix:_ Write match to Cassandra first. Push notification is best-effort but match is durable. On app open, always sync pending matches from Cassandra. Never rely on real-time delivery alone for match creation.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 50M DAU, avg 100 swipes/day, 1 match per 100 swipes |
| Read QPS | Deck generation: 50M × 10 deck requests/day / 86400 ≈ 5,800 deck QPS |
| Write QPS | 50M × 100 swipes / 86400 ≈ 58,000 swipe write QPS — Cassandra's sweet spot |
| Storage | 58K swipes/s × 86400s × 365 days × 100 bytes ≈ 183 TB/year of swipe history |
| Cache math | Pre-computed deck: 50M users × 100 profiles × 200 bytes ≈ 1 TB Redis — requires Redis cluster, but feasible. |
| Verdict | Swipe writes are the dominant workload. Cassandra is the right choice. Deck pre-computation is memory-heavy but essential for sub-second UX. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Pre-compute deck vs. real-time generation**

→ Pre-compute deck, refresh async

Real-time GEORADIUS + preference filter + swipe history dedup on every swipe takes too long. Pre-compute a stack of 50-100 candidates, serve instantly, refresh in background when stack is low.

_Revisit when:_ Real-time generation acceptable if filter set is small (sparse area, few active users nearby).

**Redis atomic check vs. Cassandra for match detection**

→ Redis SETNX for atomic mutual check + Cassandra for durability

Two users can swipe simultaneously. Need atomic operation to detect mutual like. Redis SETNX: set key 'match:min(a,b):max(a,b)' — if already set, it's a match. Cassandra stores durably.

_Revisit when:_ If Redis is unavailable, fall back to Cassandra with conditional writes (LWT).

**Bloom filter vs. exact set for swipe history dedup**

→ Bloom filter for deck generation, exact Cassandra for match detection

Bloom filter for deck: O(1), ~10 bytes/entry, 1% false positive rate acceptable (occasionally hide an unseen profile). For match: must be exact — false negative (missing a match) is unacceptable.

_Revisit when:_ Cuckoo filter instead of Bloom for deletable entries (users can revoke swipes in some markets).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a user who moves to a new city?**

Update Redis GEOADD with new location. Invalidate pre-computed deck (now stale — wrong city). Async re-generate deck for new location. Deck generation is triggered by location change events, not just swipes.

**What happens if two users swipe on each other simultaneously?**

Redis SETNX is atomic. First SETNX sets the key. Second SETNX finds key present = match detected. No race condition. Both users get notified via their respective response objects.

**How do you prevent running out of profiles in a small market?**

Track deck depth. When < 10 profiles remain, async re-generate. Widen radius or relax preference constraints progressively. Show 'no more profiles in your area' only as last resort.

**How does the recommendation system affect this design?**

ML model is a separate concern — it scores candidate profiles by predicted swipe probability. Add a scoring step after GEORADIUS but before serving deck. Score computed async, stored per (user_id, candidate_id). Deck is pre-scored candidates sorted by ML score, not just distance.

**How would you add a rewind feature (undo last swipe)?**

Soft-delete swipes in Cassandra (status = PENDING_UNDO). Remove from Bloom filter (use Cuckoo filter instead). Re-add profile to deck head. Match is undone only if the other user hasn't matched back yet.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — SQL with PostGIS. Swipe history in Postgres. Match detection with DB unique constraint. Real-time deck generation. Works up to ~100K users per city.

**v2 — Scale** — Cassandra for swipe writes. Redis GEO for location. Pre-computed deck with Bloom filter dedup. Redis SETNX for atomic match detection. Push notifications via APNS/FCM.

**v3 — Personalize** — ML scoring layer for deck ranking. Smart radius expansion for sparse markets. ELO-style attractiveness score. Super Likes as separate flow with stricter rate limiting.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that Tinder has both a fast read problem and a correctness problem. You need to generate a fresh stack of nearby profiles in well under a second, while also making sure swipes and matches are recorded correctly.

There are three main scaling pain points. First, feed generation is expensive because it mixes filters like age and preferences with geospatial search, which means a simple database query gets slow fast at large scale. Second, swiping is a huge write stream, and match creation is tricky because two users can swipe on each other at nearly the same time, so you need low latency plus strong enough consistency to not miss a match. Third, you must avoid re-showing profiles a user already swiped on, which gets harder as each user builds a large swipe history.

A good mental model is this. Tinder is hard because it combines search, real-time decisions, and deduping in one loop. You are not just storing profiles. You are constantly finding nearby candidates, filtering out old ones, and atomically detecting mutual likes.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: swipe, match, chat stub, nearby profiles. Out of scope unless asked: payments, Super Likes, video, reporting. Geo is central — say so upfront.
- **Build the deck fast** — Pre-computed candidate stack per user. Async re-fill from GEORADIUS + preference filter when stack runs low. Never compute deck on every swipe.
- **High write volume — Cassandra** — 58K swipe writes/sec. Cassandra append-only, partitioned by user_id — the right DB for this access pattern. Never use PostgreSQL for raw swipe writes at this scale.
- **Instant match detection — Redis SETNX** — SETNX on key min(a,b):max(a,b). Atomic. First caller creates the key. Second caller finds it exists = match. No race possible.
- **Swipe dedup — Bloom filter** — 73K swipes/user over 2 years. Bloom filter: O(1) check, ~730 KB per user. 1% false positive acceptable for deck (skip an unseen profile). Exact Cassandra check only for match creation.
- **Privacy by design** — Never expose who liked you without a mutual match. The SETNX key reveals nothing — it only exists on mutual. The pending swipe in the other direction is never returned by any API.
- **Failure mode to name** — Redis goes down → fall back to Cassandra LWT (Lightweight Transaction) for match detection. Slower but correct. Always have a fallback for the atomic match check.

> Frame as three problems: low-latency feed (pre-computed deck), high-volume swipe writes (Cassandra), instant match detection (Redis atomic). Name all three in your opening.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Cached deck vs real-time search** — Cached deck is fast but stale. Real-time search is fresher but slower. Answer: cached candidates with search index refill.

**Redis for match vs Cassandra only** — Cassandra alone can miss near-simultaneous mutual likes. Redis gives atomic match detection.

**Bloom filter vs exact set for swipe dedup** — Bloom filter: O(1) check, ~10 bytes/entry, 1% false positive (occasionally hides an unseen profile — acceptable). Exact Cassandra set: correct but O(log N) per check at 58K swipes/sec. Use Bloom for deck, exact for match.

**Push notifications vs in-app WebSocket for match** — Push (APNs/FCM) reaches users when app is backgrounded. WebSocket is lower latency when app is open. Both are needed — WebSocket for active session, push as fallback.

> Deck: cache wins over freshness. Match: Redis atomic over Cassandra-only. Swipe storage: Cassandra over PostgreSQL. Each decision prioritizes the dominant access pattern for that flow.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Recommendation stack — low-latency candidate generation
> [!CAUTION]
> **🔴 Weak** — run GEORADIUS + preference filter on every swipe in real-time
>
> [!WARNING]
> **🟡 Strong** — the scaling pain is that feed generation mixes geospatial search, preference filtering, and swipe history deduplication into one pipeline that must complete in under a second. Weak answer: GEORADIUS + filter in one query. Strong answer: pre-compute candidate decks asynchronously — GEORADIUS query + preference filter runs in background, results cached per user. When user opens the app, deck is ready instantly
>
> [!TIP]
> **🟢 Staff+** — the deck has a TTL (location changes, preferences update). Rather than a fixed TTL, invalidate the deck reactively: location update event → deck invalidation → async re-generation. Stack depth monitoring: when user swipes through to the last 10 profiles in their deck, trigger an async deck refresh before they run out. Candidate scoring: after GEORADIUS + preference filter, score remaining candidates using ML model (predicted swipe probability based on historical data) and serve the highest-scoring profiles first


#### Deep dive 2: Swipe storage and match detection — correctness under concurrent writes
_58,000 swipe writes/second to Cassandra with one correctness requirement: two simultaneous mutual likes must be detected exactly once_

> [!CAUTION]
> **🔴 Weak** — check Cassandra for mutual like
>
> [!WARNING]
> **🟡 Strong** — Redis SETNX on a derived key (min(a,b):max(a,b)) for atomic mutual check. If A likes B and B already liked A, the key exists → match. If A likes B and B hasn't liked yet, SETNX succeeds → key set for later. When B likes A, SETNX finds the key → match detected
>
> [!TIP]
> **🟢 Staff+** — durability: Redis is not the source of truth. Cassandra stores all swipes durably. Redis is only for the real-time match detection signal. If Redis is down: fall back to Cassandra CAS (Compare-And-Swap using Lightweight Transactions) — slower but correct. Partition key for swipes: (user_id, created_at_bucket) — queries like "all swipes by user X" are efficient


#### Deep dive 3: Avoiding re-shows — Bloom filter for swipe history
> [!CAUTION]
> **🔴 Weak** — store all swipe history in Cassandra, check each candidate with a point query. Each user has accumulated swipe history that must be filtered from their deck. At 100 swipes/day × 2 years = 73,000 swipes per active user. Checking all 73K against candidate profiles is expensive
>
> [!WARNING]
> **🟡 Strong** — Weak answer: store all swipe history in Cassandra, check each candidate with a point query. Each user has accumulated swipe history that must be filtered from their deck. At 100 swipes/day × 2 years = 73,000 swipes per active user. Checking all 73K against candidate profiles is expensive
>
> [!TIP]
> **🟢 Staff+** — solution: Bloom filter per user (probabilistic, O(1) check, ~10 bytes/entry at 1% false positive rate). 73K swipes × 10 bytes = 730 KB per user — stored in Redis. False positive means occasionally hiding an unseen profile (user never sees it) — this is acceptable, better than showing an already-swiped profile. Critical distinction: Bloom filter is for deck generation only. For match detection, exact Cassandra check is required — a false negative (missing a match) is unacceptable. Cuckoo filter as an upgrade: supports deletions (allowing swipe undo) at slightly higher memory cost


_Why the deep dives connect to the scaling problem: "Fast read, correctness, and deduplication in one loop." Deep dives address each dimension of that loop._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Three-problem script.

2. "Clarifying questions: are we designing for the core swipe + match flow, or also chat? And is the deck generation per-session or pre-computed?"

3. "Good — swipe, match, and chat stub. I'd frame this as three distinct problems: deck generation (low latency), swipe storage (high volume), match detection (correctness under concurrency)."

4. "Scale: 50M DAU, 100 swipes/day = 58K swipe writes/sec. Deck generation: 5,800 requests/day per user but needs sub-100ms response."

5. "Deck generation: pre-compute per user async. GEORADIUS finds candidates within radius, filter by age and preferences, score by ML model, cache in Redis. On app open, deck is ready. Async re-fill when stack drops below 10 profiles."

6. "Swipe storage: Cassandra, partitioned by user_id and time bucket. Write-optimized. 58K writes/sec is well within Cassandra's sweet spot."

7. "Match detection: Redis SETNX on key min(a,b):max(a,b). Both users swipe right — second SETNX finds the key already set, that's the match. Atomic, no race. Cassandra stores all swipes durably — Redis is only for the real-time detection signal."

8. "Bloom filter for swipe dedup: each user accumulates ~73K swipes over 2 years. Bloom filter: O(1) check, ~730 KB per user in Redis. Prevents re-showing profiles without scanning the full history."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |   Mobile Client   |
                         | profile, feed,    |
                         | swipe, match UI   |
                         +---------+---------+
                                   |
                            HTTPS  |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         | auth, routing     |
                         +----+---------+----+
                              |         |
                 +------------+         +-----------------+
                 |                                        |
                 v                                        v
      +---------------------+                  +----------------------+
      |   Profile Service   |                  |    Swipe Service     |
      | prefs, profile data |                  | record swipe, detect |
      +----------+----------+                  | match, emit events   |
                 |                             +----+-----------+-----+
                 |                                  |           |
                 v                                  |           |
      +---------------------+                       |           v
      |   User DB           |                       |   +------------------+
      | profiles, prefs     |                       |   | Notification Svc |
      +---------------------+                       |   | APNS or FCM      |
                                                    |   +--------+---------+
                                                    |            |
                                                    |            v
                                                    |    +---------------+
                                                    |    | Other User     |
                                                    |    | push device    |
                                                    |    +---------------+
                                                    |
                                                    v
                                         +----------------------+
                                         | Redis Match Store    |
                                         | atomic pair check    |
                                         | low latency match    |
                                         +----------+-----------+
                                                    |
                                                    v
                                         +----------------------+
                                         | Swipe DB             |
                                         | Cassandra style      |
                                         | durable swipe log    |
                                         +----------------------+


                 Feed generation path

      +---------------------+
      |   Feed Service      |
      | build candidate set |
      +-----+---------+-----+
            |         |
            |         v
            |   +----------------------+
            |   | Feed Cache           |
            |   | precomputed stacks   |
            |   +----------------------+
            |
            v
   +-------------------------+
   | Search Index            |
   | geo plus preference     |
   | filtering               |
   +-----------+-------------+
               |
               v
      +---------------------+
      | User DB / CDC sync   |
      | profile updates flow |
      | into search index    |
      +---------------------+
```

The mental model is two main paths. One path serves profiles fast through feed cache plus a search index. The other path handles swipes safely through Redis for atomic match detection and Cassandra for durable swipe history.

If you want, I can also give you a simpler interview version with only 6 boxes, or a step by step swipe flow sketch.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-7)
