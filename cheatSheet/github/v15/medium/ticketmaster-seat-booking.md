# Ticketmaster — seat booking

**Medium** · Contention · Two-phase reserve · Consistency

Tags: `Redis SETNX`, `PostgreSQL ACID`, `Elasticsearch`, `Two-phase lock`, `Virtual queue`

## Data flow

Phase 1: SETNX seatId in Redis with a 10-minute TTL — atomic soft lock, first caller wins. Phase 2: payment success → hard-commit SOLD to PostgreSQL. TTL expiry auto-releases stuck holds. Elasticsearch handles event search so reads never hit the primary DB.


> SETNX is atomic — first caller wins  |  TTL = auto-release on abandoned checkout  |  Virtual queue for onsale spikes

## Architecture diagram

```
+-------------------+
                         |      Clients      |
                         |  Web / Mobile     |
                         +---------+---------+
                                   |
                              HTTPS|
                                   v
                         +-------------------+
                         |   Load Balancer   |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         | auth, rate limit  |
                         +----+----+----+----+
                              |    |    |
              ----------------+    |    +----------------
              |                   |                     |
              v                   v                     v
     +----------------+   +----------------+   +----------------+
     | Event Service  |   | Search Service |   | Booking Service|
     +--------+-------+   +--------+-------+   +---+--------+---+
              |                    |               |        |
              |                    |               |        |
              v                    v               v        v
     +----------------+   +----------------+   +------+  +----------------+
     |  Cache Redis   |   | Elasticsearch  |   |Redis |  | Payment        |
     | event details  |   | full text      |   |Locks |  | Processor      |
     +--------+-------+   +--------+-------+   | TTL  |  | Stripe         |
              |                    ^           +--+---+  +--------+-------+
              |                    |              |               |
              v                    |              |               |
     +---------------------------------------------------------------+
     |                         PostgreSQL                            |
     | Events | Venues | Performers | Tickets | Bookings | Users     |
     +---------------------------------------------------------------+
                              ^                    ^
                              |                    |
                              +---------+----------+
                                        |
                                  CDC / sync
                                        |
                                        v
                               +------------------+
                               | Search indexer   |
                               | or CDC pipeline  |
                               +------------------+


Real-time updates for seat map

     +----------------+
     | Realtime/SSE   |
     | update service |
     +-------+--------+
             |
             v
     push seat status changes to clients


Optional protection for huge onsales

     +----------------------+
     | Virtual waiting queue|
     | Redis sorted set     |
     +----------+-----------+
                |
                v
       admits limited users to booking flow
```

The main idea is simple. Reads go through Event Service and Search Service, and writes with contention go through Booking Service. You cache event data for heavy reads, use Elasticsearch for fast search, use Redis TTL locks for temporary seat holds, and use PostgreSQL as the source of truth so you never double book.

If you want, I can also show you a smaller interview-ready version that is easier to draw in 2 minutes.


---

<details open>
<summary><strong>Problem</strong></summary>

Selling seats for a live event without double-booking, while millions of people browse, search, and buy simultaneously.

The hard part: high availability for browsing but strong consistency for booking — one seat can only be sold once.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis seat hold TTL expires while user is filling in payment info**

User completes checkout, gets 'seat no longer available' error. Terrible UX on a high-anxiety purchase.

_Fix:_ Extend TTL on active checkout sessions (heartbeat from client every 2 min). Give generous initial TTL (15 min). Warn user at 2 min remaining.

**Payment service is slow / down during onsale**

Users hold seats via Redis but can't complete payment. TTLs expire, seats released — perceived as site failure.

_Fix:_ Pre-authorize payment before Redis hold (faster path). Async payment confirmation. Queue completed payments if PSP is slow.

**Elasticsearch falls behind during onsale traffic spike**

Search for events is slow/stale. Users can't find events to buy.

_Fix:_ ES cluster pre-scaled before announced onsale. Read-only event data mostly cacheable. CDN cache event detail pages (not seat maps).


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | Taylor Swift onsale: 2M users hit 'buy' within 60s. 50K seats. 10M seat map refreshes in first hour. |
| Read QPS | 10M seat map reads / 3600s ≈ 2,778 read QPS — needs aggressive caching |
| Write QPS | 2M booking attempts / 60s ≈ 33,333 booking QPS — this is the spike problem |
| Storage | 50K seats × 200 bytes = 10 MB per event — trivially small, entirely in Redis |
| Cache math | Seat map cached with 5s TTL means reads = 1 DB read / 5s, serving 2,778 QPS from cache. Without cache: 2,778 QPS to PG = death. |
| Verdict | The 33K booking QPS spike is the actual problem. Mitigate with virtual queue — let only 10K users into booking flow at once. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Redis TTL hold vs. DB-level lock**

→ Redis SETNX with TTL

DB-level locks held for 10 minutes across 50K seats during onsale would create massive lock contention. Redis SETNX is O(1), distributed, auto-expiring. PG is only touched for final commit.

_Revisit when:_ DB-level advisory locks acceptable for small events (<5K seats). Redis overkill at that scale.

**Virtual queue vs. open access**

→ Virtual waiting room

Uncapped traffic at 33K booking QPS would require 33× normal infrastructure capacity for a 60-second spike. Queue smooths demand. Users prefer 'you are #14,532 in queue' to a 500 error.

_Revisit when:_ Open access fine for events with low demand-to-supply ratio.

**Per-seat vs. per-section holds**

→ Per-seat Redis keys

Users select specific seats. Section-level holds would require complex seat-within-section allocation logic. Per-seat is simpler and maps directly to inventory.

_Revisit when:_ Section-level + best-available algorithm for mobile-first products where users don't care about specific seat.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**Two users select the same seat simultaneously — what happens?**

SETNX is atomic. First request sets the key and succeeds. Second request finds key already set and returns 'seat taken'. No race condition possible — Redis single-threaded command execution guarantees this.

**How do you handle bots buying all tickets instantly?**

CAPTCHA at queue entry. Rate limiting per IP and per account. Browser fingerprinting. Verified fan presale (account age/purchase history required). Throttle accounts with no prior purchase history.

**What if Redis goes down during an onsale?**

This is catastrophic. Mitigate: Redis Sentinel or Cluster for HA. If Redis fails, fall back to PG-level advisory locks — slower but correct. Pre-onsale: verify Redis health explicitly, not just assume.

**How do you handle seat map updates (seat released after TTL)?**

On TTL expiry, Redis keyspace notification triggers an event. Seat map cache is invalidated. Next seat map request rebuilds from PG + active Redis holds. SSE pushes updated seat map to active clients.

**How would you scale to 10 simultaneous onsales?**

Each event is sharded independently — different Redis keyspace, different booking service instances. Horizontal scaling by event_id. The virtual queue for each event is independent.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG for all seats. Optimistic locking with retries. Works for small events (<1K seats, low contention). Simple but breaks under any real onsale load.

**v2 — Handle onsales** — Redis SETNX per seat. PG only for final commit. Virtual waiting room for popular events. SSE for real-time seat map. Handles major onsales.

**v3 — At scale** — Pre-scale infrastructure for announced onsales. Regional deployment for global artists. ML-based demand prediction for queue sizing. Dynamic pricing tier support.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is contention under huge spikes. Ticketmaster is not just a read heavy system. It is a system where millions of people may fight over the exact same tiny set of seats at the same moment.

That creates three main scaling pain points. First, the event page and seat map get hammered by refresh traffic, so reads spike hard and cached data goes stale fast. Second, booking is a hotspot write problem because many users try to reserve the same seat, so you need very careful coordination to avoid double booking. Third, search and queueing have to stay responsive during the surge, or users will overload the system before they even reach checkout. A good mental model is broad traffic everywhere, but extreme contention at a few hot seats.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **3 core flows** — View events, search events, book tickets.
- **Core tension** — Browsing wants high availability. Booking wants strong consistency.
- **Booking pattern** — Reserve seat (Redis TTL hold) → user pays → confirm (PG commit). Never hold a DB transaction open for minutes.
- **Scale reads** — Cache event details. Elasticsearch for search, not SQL LIKE scans.
- **Peak demand** — SSE for real-time seat map updates. Virtual waiting room for extreme onsales.

> Lead with the core tension: browsing wants high availability, booking wants strong consistency. Name this split in your first sentence — it frames every decision that follows.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Redis seat holds vs DB-only holds** — Redis gives better UX and auto-expiry. Tradeoff: reserved seats live outside the DB so the seat map must reflect Redis state.

**Elasticsearch vs SQL for search** — ES gives fast fuzzy full-text search. Tradeoff: sync complexity — index can lag behind PostgreSQL.

**Virtual waiting room vs no queue** — Waiting room protects the system and improves fairness. Tradeoff: user friction.

> I optimize reads aggressively (cache, CDN, Elasticsearch) but keep the final purchase path strongly consistent (Redis SETNX hold → PG commit). Never weaken the booking path for the sake of throughput.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Seat reservation — preventing double booking under extreme contention
_This is the defining hard problem for Ticketmaster. The scenario: Taylor Swift onsale, 2M users, 50K seats, all trying to book in 60 seconds_

> [!CAUTION]
> **🔴 Weak** — database SELECT + UPDATE
>
> [!WARNING]
> **🟡 Strong** — Redis SETNX per seat_id with 10-minute TTL creates a soft hold atomically (first caller wins, Redis is single-threaded so no race). On payment success, a short PG transaction commits the hard booking
>
> [!TIP]
> **🟢 Staff+** — the two-phase design explicitly separates hold duration (user has time to pay) from commit duration (DB transaction is < 100ms). Never hold a DB transaction open for 10 minutes — lock contention at scale would serialize all booking requests. TTL auto-release eliminates abandoned carts without any cleanup job. Key failure mode to proactively surface: Redis hold state and PG booking state can diverge if a crash occurs between them — on recovery, reconcile by querying PG for completed payments and releasing any Redis holds for unpaid seats


#### Deep dive 2: Seat map real-time updates — SSE fan-out under spike traffic
_During an onsale, users need to see seat availability update in near-real-time as others hold and release seats_

> [!CAUTION]
> **🔴 Weak** — HTTP polling every 5 seconds
>
> [!WARNING]
> **🟡 Strong** — SSE for server-push updates, Kafka for event distribution, delivery servers partitioned by event_id
>
> [!TIP]
> **🟢 Staff+** — SSE fan-out math matters — 2M users watching the same event × 1 update/hold event = massive write amplification. Mitigation: (1) coalescing — batch seat status changes into 500ms update windows rather than per-event pushes; (2) broadcast the full seat map diff rather than individual seat changes; (3) for extreme events, short TTL CDN caching of the seat map image reduces real-time update pressure. The seat map itself is event-specific static data — cache aggressively. Only availability state is dynamic


#### Deep dive 3: Virtual waiting room — protecting the system under extreme spikes
_Without a queue, 2M simultaneous users hit booking flow in 60 seconds = 33K booking QPS_

> [!CAUTION]
> **🔴 Weak** — scale horizontally
>
> [!WARNING]
> **🟡 Strong** — virtual waiting room caps entry into booking flow at a rate the system can handle (e.g., 5K users/minute). Ticket purchasing is a funnel: users in waiting room → hold seat → payment → confirmation. The queue smooths the spike
>
> [!TIP]
> **🟢 Staff+** — design: waiting room assigns users a randomized position (not FIFO — prevents queue jumping bots who connect milliseconds early). Position is stored in Redis sorted set with score = random. Users poll their position. When it's their turn, they receive a signed token that grants entry to booking flow (token has 5-minute TTL). The token prevents users from sharing their queue position. Estimate: with 2M users and a 5K/min entry rate, median wait ≈ 200 minutes. For popular events this is expected and communicated to users upfront


_Why the deep dives connect to the scaling problem: "Extreme contention at a few hot seats." Deep dive 1 solves booking correctness. Deep dive 2 solves real-time UX. Deep dive 3 solves traffic smoothing._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Requirements-first, tension-first script.

2. "Quick clarifications: are we handling the full flow — event browsing, search, and ticket purchase? And what's the peak demand scenario — a major onsale like Taylor Swift?"

3. "Got it. Core features: view events, search events, book tickets. The defining constraint: browsing should be highly available and low-latency. Booking must be strongly consistent — we can never double-sell a seat."

4. "Scale: I'll assume 10M normal DAU, with onsales hitting millions of concurrent users for a single event in under 60 seconds. That's the hard case to design for."

5. "High-level: three services behind an API gateway — Event Service, Search Service, Booking Service. PostgreSQL is source of truth. Redis for seat holds. Elasticsearch for full-text event search."

6. "Booking deep-dive — this is where it gets interesting. I'd use a two-phase approach. Phase 1: Redis SETNX with a 10-minute TTL per seat_id. SETNX is atomic — first caller wins, no race condition possible. Phase 2: on payment success, a short PostgreSQL transaction marks the ticket SOLD and releases the Redis hold."

7. "For onsales: a virtual waiting room. Cap entry into the booking flow at a rate the system can handle — say 5K users per minute. Users in the queue get a position and a signed token that grants them entry. This converts a 33K QPS spike into a steady 5K QPS stream."

8. "Key tradeoff I want to name explicitly: Redis holds and PostgreSQL records can diverge if a crash occurs between them. On recovery, reconcile by querying PostgreSQL for completed payments and releasing any orphaned Redis holds."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |      Clients      |
                         |  Web / Mobile     |
                         +---------+---------+
                                   |
                              HTTPS|
                                   v
                         +-------------------+
                         |   Load Balancer   |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         | auth, rate limit  |
                         +----+----+----+----+
                              |    |    |
              ----------------+    |    +----------------
              |                   |                     |
              v                   v                     v
     +----------------+   +----------------+   +----------------+
     | Event Service  |   | Search Service |   | Booking Service|
     +--------+-------+   +--------+-------+   +---+--------+---+
              |                    |               |        |
              |                    |               |        |
              v                    v               v        v
     +----------------+   +----------------+   +------+  +----------------+
     |  Cache Redis   |   | Elasticsearch  |   |Redis |  | Payment        |
     | event details  |   | full text      |   |Locks |  | Processor      |
     +--------+-------+   +--------+-------+   | TTL  |  | Stripe         |
              |                    ^           +--+---+  +--------+-------+
              |                    |              |               |
              v                    |              |               |
     +---------------------------------------------------------------+
     |                         PostgreSQL                            |
     | Events | Venues | Performers | Tickets | Bookings | Users     |
     +---------------------------------------------------------------+
                              ^                    ^
                              |                    |
                              +---------+----------+
                                        |
                                  CDC / sync
                                        |
                                        v
                               +------------------+
                               | Search indexer   |
                               | or CDC pipeline  |
                               +------------------+


Real-time updates for seat map

     +----------------+
     | Realtime/SSE   |
     | update service |
     +-------+--------+
             |
             v
     push seat status changes to clients


Optional protection for huge onsales

     +----------------------+
     | Virtual waiting queue|
     | Redis sorted set     |
     +----------+-----------+
                |
                v
       admits limited users to booking flow
```

The main idea is simple. Reads go through Event Service and Search Service, and writes with contention go through Booking Service. You cache event data for heavy reads, use Elasticsearch for fast search, use Redis TTL locks for temporary seat holds, and use PostgreSQL as the source of truth so you never double book.

If you want, I can also show you a smaller interview-ready version that is easier to draw in 2 minutes.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-4)
