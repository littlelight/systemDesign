# Online auction (eBay)

**Medium** · Contention · Bid ordering · Real-time updates

Tags: `Redis Lua CAS`, `PostgreSQL`, `Kafka`, `WebSocket`

## Data flow

Bids serialized via Redis Lua compare-and-set: atomically check if new bid exceeds current max, update if yes. Validated bid written to PostgreSQL for durability. Kafka fans out to WebSocket subscribers showing the live bid feed.


> Lua CAS: only update if newBid > currentBid (atomic)  |  Redis for speed, PG for durability

## Architecture diagram

```
+-------------------+
                         |      Clients       |
                         | web mobile browser |
                         +---------+---------+
                                   |
                          GET POST /auctions
                          POST /bids
                          SSE bid updates
                                   |
                         +---------v---------+
                         |    API Gateway     |
                         | auth routing rate  |
                         +----+----------+----+
                              |          |
                    read path |          | write path
                              |          |
               +--------------v--+    +--v----------------+
               | Auction Service |    | Bid Ingest API    |
               | auction details |    | accept bid fast   |
               +-------+---------+    +---------+---------+
                       |                          |
                       |                          |
              +--------v---------+                |
              | Auctions DB      |<---------------+
              | auctions items   |     optional direct read
              | max_bid on row   |
              +--------+---------+                |
                       ^                          |
                       |                          v
                       |                 +--------+---------+
                       |                 | Kafka            |
                       |                 | topic partitioned|
                       |                 | by auctionId     |
                       |                 +--------+---------+
                       |                          |
                       |                          v
                       |                 +--------+---------+
                       |                 | Bidding Service  |
                       |                 | consumer workers |
                       |                 | validate bid     |
                       |                 | OCC or row lock  |
                       |                 +---+----------+---+
                       |                     |          |
                       |                     |          |
                       |        write bid history       |
                       |                     |          |
                       |                     v          v
                       |              +------+--+   +---+----------------+
                       |              | Bids DB |   | Pub Sub / Fanout   |
                       |              | history |   | broadcast updates  |
                       |              +---------+   +---+----------------+
                       |                                  |
                       |                                  |
                +------v----------------------------------v------+
                | SSE / Realtime Gateway instances               |
                | keep client connections by auctionId           |
                | push latest accepted max bid to watchers       |
                +----------------------+-------------------------+
                                       |
                                       v
                                  +----+----+
                                  | Clients |
                                  | live UI |
                                  +---------+
```

The key idea is this. Reads and writes are split. Auction Service handles viewing and creating auctions. Bids go through a durable queue first so you do not lose them under spikes. Then Bidding Service processes bids in order per auction, updates the auction row max bid safely, stores bid history, and publishes the new highest bid to the realtime layer.

If you need to simplify this in an interview, keep the core path to four boxes. Client to API Gateway to Kafka to Bidding Service to Database, plus SSE for live updates. That shows you understand the two hard parts, which are correct bid acceptance and real-time fanout.


---

<details open>
<summary><strong>Problem</strong></summary>

Accepting bids correctly at scale. Ensuring only valid higher bids win, no bids are lost, and everyone sees the current highest bid quickly.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis goes down mid-auction**

No authoritative current bid state. New bids can't be validated. Auction is paused.

_Fix:_ Redis Sentinel for HA. On Redis failure: freeze auction (stop accepting new bids), reconstruct highest bid from PostgreSQL (source of truth), resume when Redis recovers. SLA: < 30s pause.

**Sniping (bid placed in last 3 seconds)**

Most bidders lose because they can't react in time. Bad user experience, low trust.

_Fix:_ Auction extension: if a bid is placed in last 3 minutes, extend auction by 3 minutes (eBay's actual behavior). Prevents pure sniping while still allowing competitive last-minute bidding.

**Bid fan-out overwhelms WebSocket servers during auction close**

Bid in last 30 seconds triggers thousands of simultaneous WebSocket pushes. Server falls over.

_Fix:_ Batch WebSocket pushes with 100ms coalescing window (same as FB Live Comments). Individual bid events merged into a single update payload. At extreme scale, SSE over WebSocket (simpler fan-out).


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10M active auctions, 100K active bidders at peak, 1 bid/10s per active bidder |
| Read QPS | Live updates: 100K watchers × poll/5s = 20K fan-out messages/s peak during popular auction close |
| Write QPS | 100K bidders / 10s = 10K bid write QPS — each is a Redis Lua CAS + PG write |
| Storage | Bid history: 10M auctions × avg 50 bids × 100 bytes ≈ 50 GB PG — small |
| Cache math | Current max bid per auction: 10M × 50 bytes ≈ 500 MB Redis — fits easily on single Redis instance |
| Verdict | Fan-out of bid updates to watchers is the scaling challenge, not bid storage. Coalescing updates is essential for popular auctions. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Redis Lua CAS vs. database transactions for bid atomicity**

→ Redis Lua CAS as fast path, PG as durable record

PG row lock for bid validation adds 5-20ms. Redis Lua CAS adds <1ms. Under high contention (auction closing), Redis wins on latency. PG is written after Redis CAS succeeds — durability is not sacrificed, just sequenced.

_Revisit when:_ PG advisory lock as fallback when Redis is unavailable.

**Proxy bid (auto-bid) design**

→ Proxy bid stored server-side, current bid incremented automatically

User sets a max price. System auto-bids on their behalf, incrementing by minimum bid increment, up to their max. This is eBay's proxy bidding. It's a state machine that runs server-side, not client-side.

_Revisit when:_ This requires careful treatment — proxy bid amounts are private data. Stored encrypted in PG, never exposed via API.

**Auction close time precision**

→ Scheduled job with 1-second precision, not millisecond

Auction close is user-facing time (12:00 PM). 1-second granularity is what users expect. Sub-second precision creates unfair advantages for clients with low-latency connections.

_Revisit when:_ Exact-time close for high-frequency trading-style auctions (different use case, different design).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**Two bids arrive at exactly the same time — which wins?**

Redis is single-threaded. Commands execute serially. The bid whose EVAL command reaches Redis first wins. This is deterministic, fair (first-come), and requires no additional coordination.

**How do you handle a bidder who wins but doesn't pay?**

Reserve winning bid, send payment invoice. Payment window (24-48hr). If unpaid: cancel sale, offer to next-highest bidder (second-chance offer). Track non-payment rate per bidder — suspend bidding privileges after threshold.

**How do you prevent bid retraction abuse (bid high, retract, bid lower)?**

Allow retraction only under specific conditions (item description materially wrong). Track retraction count per bidder. High retraction rate → bidder restriction. Log all retraction events for fraud analysis.

**How would you scale to 1B concurrent auctions?**

Auctions are independent — shard by auction_id. Each shard handles its own Redis CAS and PG writes. No cross-auction coordination needed. Fan-out delivery servers also shard by auction_id range. Near-linear horizontal scale.

**How do you handle currency and international bidding?**

Store all bids in a base currency (USD). Display in local currency at read time using exchange rate service (cached, 1-min TTL). Minimum bid increment calculated in base currency to avoid floating-point issues across currencies.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG for everything. SELECT FOR UPDATE for bid lock. Simple polling for live updates. Works for low-volume auctions.

**v2 — Real-time + correctness** — Redis Lua CAS for fast bid validation. WebSocket for live updates. Kafka bid events. Proxy bidding. Auction extension for sniping. Handles peak auctions.

**v3 — Global scale** — Sharded by auction_id. Regional deployment. Fraud detection ML model. Anti-shill bidding detection. Mobile-optimized push notifications.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is contention on a tiny piece of shared state. Many users may bid on the same auction at nearly the same time, but only one current highest bid can be correct.

That creates three scaling pain points. First, bid writes are hot and correctness matters, so you need atomic updates or version checks to avoid accepting stale lower bids. Second, auctions get bursty near the end, so one popular item can suddenly get hammered even if overall traffic looks manageable. Third, users expect live updates, which means one accepted bid may need to fan out quickly to many watchers across many servers. So the short interview answer is that Online Auction is hard because it combines hotspot writes, strict bid consistency, and real time fan-out.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: list item, place bid, real-time bid feed, auction close, winner determination. Out of scope unless asked: payments, shipping, dispute resolution, seller ratings.
- **Atomic bid check — Redis Lua CAS** — GET current_max, compare, SET if higher — all in one Lua script. Single-threaded Redis execution means no two bids can race. Never use GET + SET as separate operations.
- **Redis for speed, PostgreSQL for truth** — Redis holds current max bid for sub-millisecond reads during live auction. PostgreSQL stores every bid for audit, dispute resolution, and winner determination. Both required.
- **Auction close is a state machine** — ACTIVE → CLOSING (stop accepting bids) → CLOSED (determine winner). Close job must be idempotent: UPDATE SET status=CLOSED WHERE status=CLOSING. If it runs twice, second is a no-op.
- **Anti-sniping extension** — Bid placed in last 3 minutes → extend auction by 3 minutes. UPDATE auctions SET ends_at = ends_at + INTERVAL 3 minutes WHERE id=? AND ends_at - NOW() < 3 minutes. Users prefer fair competition over fixed end time.
- **Real-time bid feed — SSE with coalescing** — 1K watchers × bid storm in last 30s = massive fan-out. 100ms coalescing: push current max bid state, not every individual bid. Watchers see the current price, not a log of every increment.
- **Failure mode to name** — Redis goes down mid-auction: freeze auction (reject new bids), reconstruct highest bid from PostgreSQL bid history, resume when Redis recovers. SLA: < 30s pause. PostgreSQL is always authoritative.

> Mental model: Redis for speed and ordering, PostgreSQL for durability, Kafka for live feed.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Redis CAS vs DB transaction for bid lock** — Redis Lua CAS is sub-millisecond under high bid contention. DB SELECT FOR UPDATE adds 5–20ms and serializes under load. Redis wins on latency; PG write must follow for durability.

**WebSocket vs polling for live bid feed** — WebSocket gives real-time updates with no wasted requests. SSE is simpler for one-directional push and auto-reconnects. Both beat polling. SSE is the right default for a bid feed.

**Hard close vs auction extension anti-sniping** — Hard close at the scheduled time is simple but rewards sniping tools. Extension (add 3 min on any bid in last 3 min) levels the field and increases final price — tradeoff is unpredictable end time, which some bidders dislike.

**Proxy bidding server-side vs client-side** — Server-side proxy bidding (auto-bid up to user max) prevents the user from revealing their max price to competitors. Client-side is simpler but the user must stay online. Server-side is the correct model for a real auction.

> "Redis for speed and ordering, PostgreSQL for durability. Never rely on Redis alone for financial data."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Concurrent bid handling — atomic compare-and-set for bid correctness
_Two bidders submit simultaneously: A bids $100, B bids $102, both at the exact same millisecond. The system must accept $102 and reject $100_

> [!CAUTION]
> **🔴 Weak** — database transaction with SELECT FOR UPDATE
>
> [!WARNING]
> **🟡 Strong** — Redis Lua CAS (Compare-And-Set): (1) GET current_highest_bid, (2) if new_bid > current_highest, SET new_bid, return accepted, else return rejected. All atomic in one Lua script
>
> [!TIP]
> **🟢 Staff+** — Database transaction with SELECT FOR UPDATE is also correct but adds 5-20ms PG lock latency under high contention. Redis Lua CAS is <1ms. The tradeoff: Redis is not durable by default (RDB snapshot may be seconds old), so every accepted bid must also be written to PostgreSQL before returning success. The two-write pattern: Redis for speed and ordering, PG for durability. If Redis and PG diverge (Redis accepts a bid but PG write fails): on startup/recovery, the PG bid history is authoritative. Redis state is reconstructed from PG


#### Deep dive 2: Auction close — preventing last-millisecond races
> [!CAUTION]
> **🔴 Weak** — close the auction at exactly the scheduled time and reject bids that arrive after
>
> [!WARNING]
> **🟡 Strong** — auction close has a subtle correctness problem: a bid submitted at 23:59:59.999 and processed at 00:00:00.001 — is it valid? Weak answer: ignore bids after close time. Strong answer: explicit auction state machine: ACTIVE → CLOSING → CLOSED. On scheduled close time: set state to CLOSING (still accepts bids for a brief grace period). After grace period: set to CLOSED, reject all new bids. Determine winner from highest bid in PG
>
> [!TIP]
> **🟢 Staff+** — the auction close job must be idempotent — if it runs twice (due to retry), it should produce the same result. Implement with optimistic concurrency: UPDATE auctions SET status='CLOSED', winner_id=? WHERE id=? AND status='CLOSING'. If zero rows updated: another instance already closed it. Auction extension (eBay's approach): if a bid arrives in the last 3 minutes, extend the auction by 3 minutes. Implement: on any bid in the last 3 minutes, UPDATE auctions SET ends_at = ends_at + INTERVAL '3 minutes' WHERE id=? AND ends_at - NOW() < INTERVAL '3 minutes'


#### Deep dive 3: Real-time bid feed — fan-out to watchers without overloading
> [!CAUTION]
> **🔴 Weak** — push every individual bid event to all watchers in real-time
>
> [!WARNING]
> **🟡 Strong** — an active auction with 10,000 watchers × 1 new bid every 10 seconds = 1,000 SSE messages/s for one auction. Naive fan-out: one Kafka message → 10,000 individual SSE pushes
>
> [!TIP]
> **🟢 Staff+** — delivery servers partitioned by auction_id (consistent hash). Each delivery server holds all SSE connections for its auction range. On new bid event: Kafka consumer on that delivery server pushes to all 10,000 connected clients in a tight loop. No cross-server coordination needed. For the last 30 seconds of a popular auction (bid storm): coalesce — buffer bids in 100ms windows, push the latest state (current highest bid) rather than every individual bid. Clients don't need every intermediate bid — they need the current highest bid to display correctly. This reduces fan-out from O(bids_per_second × watchers) to O(10 × watchers) during the final sprint


_Why the deep dives connect to the scaling problem: "Hotspot writes, bid consistency, and real-time fan-out." Each deep dive addresses one dimension._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Atomic-bid script.

2. "Clarifying questions: are we building a general auction platform, or focused on a specific model — English auction (ascending price), Dutch (descending), sealed bid? And what's the real-time requirement for the bid feed?"

3. "Good — English auction, real-time bid feed. Core features: list item, place bid, real-time bid updates, auction close and winner determination. Out of scope: payments, shipping, dispute resolution."

4. "The hard problem: two bidders submit simultaneously. The system must accept exactly the higher bid with no race condition. This drives the core design."

5. "Bid acceptance: Redis Lua CAS. GET current_max_bid, compare, SET if higher — all in one atomic Lua script. Single-threaded Redis execution: no race possible. Every accepted bid is also written to PostgreSQL for durability and audit."

6. "Auction close state machine: ACTIVE → CLOSING → CLOSED. The close job is idempotent: UPDATE SET status=CLOSED WHERE status=CLOSING. If it runs twice, the second is a no-op. Winner = highest bid in PostgreSQL at close time."

7. "Anti-sniping: bid in the last 3 minutes → extend auction by 3 minutes. One SQL update. Users prefer a fair final sprint over a fixed end time that rewards sniping tools."

8. "Real-time bid feed: SSE with 100ms coalescing. During a bid storm in the last 30 seconds, push the current max bid state, not every individual increment. Watchers need the current price, not a log of every $1 raise."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |      Clients       |
                         | web mobile browser |
                         +---------+---------+
                                   |
                          GET POST /auctions
                          POST /bids
                          SSE bid updates
                                   |
                         +---------v---------+
                         |    API Gateway     |
                         | auth routing rate  |
                         +----+----------+----+
                              |          |
                    read path |          | write path
                              |          |
               +--------------v--+    +--v----------------+
               | Auction Service |    | Bid Ingest API    |
               | auction details |    | accept bid fast   |
               +-------+---------+    +---------+---------+
                       |                          |
                       |                          |
              +--------v---------+                |
              | Auctions DB      |<---------------+
              | auctions items   |     optional direct read
              | max_bid on row   |
              +--------+---------+                |
                       ^                          |
                       |                          v
                       |                 +--------+---------+
                       |                 | Kafka            |
                       |                 | topic partitioned|
                       |                 | by auctionId     |
                       |                 +--------+---------+
                       |                          |
                       |                          v
                       |                 +--------+---------+
                       |                 | Bidding Service  |
                       |                 | consumer workers |
                       |                 | validate bid     |
                       |                 | OCC or row lock  |
                       |                 +---+----------+---+
                       |                     |          |
                       |                     |          |
                       |        write bid history       |
                       |                     |          |
                       |                     v          v
                       |              +------+--+   +---+----------------+
                       |              | Bids DB |   | Pub Sub / Fanout   |
                       |              | history |   | broadcast updates  |
                       |              +---------+   +---+----------------+
                       |                                  |
                       |                                  |
                +------v----------------------------------v------+
                | SSE / Realtime Gateway instances               |
                | keep client connections by auctionId           |
                | push latest accepted max bid to watchers       |
                +----------------------+-------------------------+
                                       |
                                       v
                                  +----+----+
                                  | Clients |
                                  | live UI |
                                  +---------+
```

The key idea is this. Reads and writes are split. Auction Service handles viewing and creating auctions. Bids go through a durable queue first so you do not lose them under spikes. Then Bidding Service processes bids in order per auction, updates the auction row max bid safely, stores bid history, and publishes the new highest bid to the realtime layer.

If you need to simplify this in an interview, keep the core path to four boxes. Client to API Gateway to Kafka to Bidding Service to Database, plus SSE for live updates. That shows you understand the two hard parts, which are correct bid acceptance and real-time fanout.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-14)
