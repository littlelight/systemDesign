# Local delivery (GoPuff)

**Easy** · Geospatial · Inventory consistency · Order flow

Tags: `PostGIS`, `Redis`, `PostgreSQL`, `Optimistic lock`, `Geohash`

## Data flow

The user sends a location and item list. The Geo Service finds the nearest warehouse via geohash or PostGIS. For browsing, inventory is served from Redis — slight staleness is fine. At order placement the system uses a PostgreSQL transaction with row-level lock: decrement only if quantity > 0, otherwise reject.


> Browse: Redis cache (eventual OK)  |  Order: PG tx with row-level lock — no oversell

## Architecture diagram

```
+------------------+
                 |      Client      |
                 |  Web or Mobile   |
                 +---------+--------+
                           |
          Availability API |              Order API
                           |
          +----------------+----------------+
          |                                 |
          v                                 v
+-----------------------+         +-----------------------+
|  Availability Service |         |    Orders Service     |
|  read path            |         |    write path         |
+----------+------------+         +-----------+-----------+
           |                                    |
           | asks for serviceable DCs           | asks for serviceable DCs
           v                                    v
                +---------------------------+
                |      Nearby Service       |
                | find DCs within 1 hour    |
                +-------------+-------------+
                              |
                              | candidate DCs
                              v
                +---------------------------+
                | Travel Time Service       |
                | external ETA estimation   |
                +---------------------------+


Availability read flow
----------------------
           |
           v
+-----------------------+
| Redis Cache           |
| availability results  |
| short TTL             |
+----------+------------+
           |
     cache miss
           v
+-----------------------+
| Postgres Read Replica |
| inventory reads       |
+----------+------------+
           |
           v
+-----------------------+
| Partitioned by Region |
| inventory + items     |
+-----------------------+


Order write flow
----------------
           |
           v
+-----------------------+
| Postgres Leader       |
| serializable txn      |
+----------+------------+
           |
           v
+-----------------------+
| Tables                |
| Inventory             |
| Items                 |
| Orders                |
| OrderItems            |
+-----------------------+
           |
           v
+-----------------------+
| Cache Invalidation    |
| expire affected keys  |
+-----------------------+
```

The mental model is simple. Availability is a fast read path that can tolerate slight staleness, so it uses Nearby Service, cache, and read replicas. Orders are the strict write path, so they go to the Postgres leader in one transaction so you do not double sell inventory.

If you were drawing this in an interview, I would show just these boxes first. Then I would say reads go through cache and replicas, while writes go through the leader with an atomic transaction.


---

<details open>
<summary><strong>Problem</strong></summary>

Given a user's location, show which items are available for delivery within 1 hour, and let the user place an order without selling the same physical inventory twice.

The hard part: fast reads for availability, strongly consistent writes for orders.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Inventory cache (Redis) goes stale during a spike**

Users see items as available that just sold out. They complete checkout, then get a cancellation. Poor UX.

_Fix:_ Short TTL (30s). On order placement, always recheck PG transactionally regardless of cache state. Cache is for browsing only, never for purchase commitment.

**PostgreSQL write node fails mid-order**

Order transaction rolls back. Inventory not decremented. Customer may be charged but order not created.

_Fix:_ 2-phase: charge authorization first (hold, not capture), then create order in PG, then capture charge. If PG fails, release auth. Idempotency key prevents double-charge on retry.

**Geo service slow on radius queries**

Availability check exceeds 100ms SLA. Bad mobile UX.

_Fix:_ Pre-compute geohash cells for all warehouses. Radius query becomes a set lookup on geohash prefixes. Cache warehouse list per geohash (changes slowly).


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10M DAU, 5 availability checks/session, 1 order/3 sessions, avg 5 items/order |
| Read QPS | 10M × 5 / 86400 ≈ 578 availability QPS — dominated by Redis reads |
| Write QPS | 10M / 3 / 86400 ≈ 39 order QPS — low, but each touches 5 inventory rows atomically |
| Storage | 100K SKUs × 100 warehouses × 50 bytes ≈ 500 MB inventory table — tiny, fits in Redis entirely |
| Cache math | Full inventory in Redis ≈ 500 MB. Trivial. TTL 30s means at worst 30s staleness on availability. |
| Verdict | Writes are the hard part, not reads. 39 QPS of atomic inventory transactions — single PG node is fine up to ~500 QPS of orders. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Optimistic vs. pessimistic locking for inventory**

→ Pessimistic (SELECT FOR UPDATE)

Inventory contention is high for popular items during peak hours. Optimistic locking would cause high retry rates and bad UX. Pessimistic lock duration is <50ms so throughput is acceptable.

_Revisit when:_ Optimistic locking if order volume exceeds 5K QPS per warehouse (unlikely).

**Separate read and write paths vs. single DB**

→ Redis for reads, PG for writes

Availability checks are 10× more frequent than orders. Serving availability from PG would add unnecessary load. Redis with short TTL is the right tradeoff.

_Revisit when:_ Single DB path for simpler consistency if scale stays small.

**Geo service vs. in-DB geospatial query**

→ Separate geo service with geohash

PostGIS radius queries are powerful but slow at high QPS. Geohash prefix lookup is a simple index scan — much faster.

_Revisit when:_ PostGIS is fine up to ~1K QPS geo queries. Only separate at higher scale.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you prevent overselling the last unit?**

SELECT FOR UPDATE on the inventory row inside the order transaction. Decrement only if quantity > 0, else roll back and return 'sold out'. Atomicity in PostgreSQL guarantees no two transactions decrement simultaneously.

**What happens if a user adds to cart but doesn't check out?**

Don't reserve inventory at cart time — reserve only at checkout. Cart is a soft state in Redis with TTL. Inventory is only committed when the order transaction commits.

**How do you handle a warehouse going offline?**

Health check pings warehouses every 30s. On failure: mark inactive in geo service, remove from availability results. Orders route to next nearest warehouse. Alert ops.

**How do you handle a sudden 10× spike in a neighborhood (e.g., bad weather)?**

Read path (Redis) handles spikes trivially. Write path (PG transactions) is the bottleneck. Queue order submissions with SQS. Workers drain queue at PG's max safe write rate. Show estimated wait time to user.

**How would you add real-time ETA?**

Separate routing service calls Google Maps / internal routing engine. ETA is an estimate, not a commitment. Cache ETA per (warehouse, delivery_zone) pair with 5-minute TTL. Driver tracking is a separate real-time system.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: success rate, latency, active users. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: minutes of read unavailability acceptable; rebuild cache from DB. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single PG for inventory + orders. No geo service — just distance formula in app code. No caching. Works for 1-2 warehouses.

**v2 — Scale reads** — Redis for inventory browsing. Geo service with geohash. PG read replica for reporting. Handles 10+ warehouses, 100K DAU.

**v3 — Optimize** — Order queue with SQS for spike absorption. Predictive inventory replenishment from ML model. Real-time driver tracking as separate service. Dynamic delivery window pricing.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that reads are huge, but writes must be correct. In this Gopuff style system, availability checks happen constantly and need to stay under 100ms, while actual orders are less frequent but need strong consistency so you never sell the same item twice.

So there are really two scaling pain points. First, availability is expensive because you are not just reading one row. You have to find nearby distribution centers, read inventory from several of them, and union the results fast enough for search-like traffic. Second, ordering creates contention because many users may try to buy the last unit at the same time, so inventory updates need atomic transactions or locking. A good mental model is that reads are broad and frequent, while writes are rare but delicate.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Two-path model** — Fast reads for availability. Safe writes for orders. Different consistency requirements.
- **Availability read** — Find nearby DCs via geohash, aggregate inventory, cache in Redis with short TTL.
- **Order write** — Recheck inventory and create order in one PostgreSQL serializable transaction.
- **Nearby service** — Coarse geographic filter first, then travel time estimation on the small candidate set.
- **Cache invalidation** — Expire affected Redis keys after an order decrements inventory.

> Open with the two-path split: browsing is eventually consistent (Redis), ordering is strongly consistent (PG row lock). Don't conflate them.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Cache + replicas vs single DB for reads** — Cache + replicas is better because reads are huge and can tolerate slight staleness. Tradeoff: freshness vs speed.

**Single PG transaction vs distributed lock** — Single transaction is simpler and correct. Tradeoff: couples orders and inventory, bottleneck sooner.

**Distance filter vs travel time** — Distance filter is fast and cheap. Travel time is accurate but adds latency.

> For availability I'm trading read freshness for speed — Redis cache with 30s TTL. For ordering I'm choosing correctness with SELECT FOR UPDATE. Two paths, two consistency models, both intentional.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Availability reads at scale — the geo + inventory problem
> [!CAUTION]
> **🔴 Weak** — query PostgreSQL with PostGIS radius filter on every availability request
>
> [!WARNING]
> **🟡 Strong** — the scaling pain is that availability reads are expensive: find nearby DCs, read inventory from each, union results, respond in <100ms. This is not a simple DB read — it's a geospatial + multi-source aggregation under search-like QPS. Weak answer: query PG with PostGIS radius filter. Strong answer: pre-compute a geohash index of all DCs, cache inventory in Redis with short TTL (30s). Availability query = geohash prefix lookup (O(1)) + Redis HGETALL for each DC in range
>
> [!TIP]
> **🟢 Staff+** — inventory in Redis is a projection, not the source of truth — it gets stale within the TTL window. This is intentional: users browsing see approximately-fresh inventory, users purchasing get a real-time DB check. The two paths have different consistency requirements and must be explicitly separated. At high QPS, even Redis can become a bottleneck — read-through cache with local in-process LRU for the hottest items (fast movers, popular items) adds another layer


#### Deep dive 2: Order placement — preventing oversell under concurrent writes
_The core hard problem: many users buying the last unit simultaneously_

> [!CAUTION]
> **🔴 Weak** — check inventory then decrement (two operations — TOCTOU race)
>
> [!WARNING]
> **🟡 Strong** — SELECT FOR UPDATE within a PostgreSQL transaction — atomic lock, check, and decrement
>
> [!TIP]
> **🟢 Staff+** — tradeoff: pessimistic locking works but creates serialization under high concurrent orders for the same item. Optimistic locking (CAS via version number) is better under low contention, worse under high contention. For a local delivery system where popular items genuinely spike (weather events, flash sales), pessimistic is the safer default. SQS queue for order submission acts as a shock absorber: workers drain the queue at a safe DB write rate, and user sees "order processing" rather than an error. Idempotency key on every order prevents double-charge on client retry


#### Deep dive 3: Geo-routing — nearest DC with ETA, not just distance
> [!CAUTION]
> **🔴 Weak** — find the nearest DC by Euclidean distance
>
> [!WARNING]
> **🟡 Strong** — a production system doesn't just find the nearest DC by Euclidean distance — it finds the nearest DC that can fulfill the order within the delivery window. This requires: (1) drive time estimate from DC to delivery address (routing API, not just radius), (2) DC capacity check (is there a driver available?), (3) inventory check (does this DC have the items?)
>
> [!TIP]
> **🟢 Staff+** — this is a constraint satisfaction problem across three dimensions. The production approach is coarse + fine: geohash radius filter first (fast, eliminates 99% of DCs), then routing API call on the small candidate set (expensive, accurate). Cache routing estimates per (DC_geohash, delivery_zone) pair with 5-minute TTL to reduce routing API costs


_Why the deep dives connect to the scaling problem: "Reads are broad and frequent; writes are rare but delicate." Deep dive 1 solves the read problem. Deep dive 2 solves the write correctness problem. Deep dive 3 solves the geo-routing precision problem._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Two-path script.

2. "Clarifying questions: are we building a dark-store model — our own warehouses — or a marketplace model routing to third-party stores? And what's the delivery promise — 30 minutes, on-demand?"

3. "Good — own warehouses, 30-min delivery. Core features: browse available items by location, place order, track delivery. Out of scope: driver routing optimization, demand forecasting, warehouse management."

4. "Two fundamentally different consistency requirements I'd name upfront: browsing inventory can be eventually consistent — a 30-second stale read is fine. Placing an order must be strongly consistent — two customers cannot buy the last unit."

5. "Browse path: Redis cache per warehouse, 30-second TTL. Geo service finds nearest warehouses via geohash prefix lookup. Client gets near-real-time inventory without hitting the DB."

6. "Order path: PostgreSQL SELECT FOR UPDATE inside a transaction. Lock the row, check quantity > 0, decrement. If quantity is 0: rollback, return sold-out. Atomic. No oversell possible."

7. "SQS queue in front of order processing acts as a shock absorber for spikes — bad weather, local events. Workers drain the queue at a safe DB write rate. Users see a short wait rather than an error."

8. "Key tradeoff: Redis is eventually consistent — a user might see an item as available in browse mode that sold out 29 seconds ago. That's acceptable. The purchase path always gets a fresh DB check regardless of cache state."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+------------------+
                 |      Client      |
                 |  Web or Mobile   |
                 +---------+--------+
                           |
          Availability API |              Order API
                           |
          +----------------+----------------+
          |                                 |
          v                                 v
+-----------------------+         +-----------------------+
|  Availability Service |         |    Orders Service     |
|  read path            |         |    write path         |
+----------+------------+         +-----------+-----------+
           |                                    |
           | asks for serviceable DCs           | asks for serviceable DCs
           v                                    v
                +---------------------------+
                |      Nearby Service       |
                | find DCs within 1 hour    |
                +-------------+-------------+
                              |
                              | candidate DCs
                              v
                +---------------------------+
                | Travel Time Service       |
                | external ETA estimation   |
                +---------------------------+


Availability read flow
----------------------
           |
           v
+-----------------------+
| Redis Cache           |
| availability results  |
| short TTL             |
+----------+------------+
           |
     cache miss
           v
+-----------------------+
| Postgres Read Replica |
| inventory reads       |
+----------+------------+
           |
           v
+-----------------------+
| Partitioned by Region |
| inventory + items     |
+-----------------------+


Order write flow
----------------
           |
           v
+-----------------------+
| Postgres Leader       |
| serializable txn      |
+----------+------------+
           |
           v
+-----------------------+
| Tables                |
| Inventory             |
| Items                 |
| Orders                |
| OrderItems            |
+-----------------------+
           |
           v
+-----------------------+
| Cache Invalidation    |
| expire affected keys  |
+-----------------------+
```

The mental model is simple. Availability is a fast read path that can tolerate slight staleness, so it uses Nearby Service, cache, and read replicas. Orders are the strict write path, so they go to the Postgres leader in one transaction so you do not double sell inventory.

If you were drawing this in an interview, I would show just these boxes first. Then I would say reads go through cache and replicas, while writes go through the leader with an atomic transaction.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-2)
