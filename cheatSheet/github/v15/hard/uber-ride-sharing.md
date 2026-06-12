# Uber — ride-sharing

**Hard** · Geo matching · Location tracking · Trip state machine

Tags: `Redis GEO`, `PostgreSQL`, `WebSocket`, `Geohash`, `Trip state machine`

## Data flow

Driver location → Redis GEOADD every 4 seconds. This is ephemeral — never written to PostgreSQL. Rider requests → GEORADIUS finds nearby drivers → rank → dispatch. Trip state machine in PostgreSQL: REQUESTED → MATCHED → IN_PROGRESS → COMPLETED.


> NEVER write driver loc to PG every 4s — Redis only  |  PG = trip durability  |  Surge = demand/supply ratio per geohash

## Architecture diagram

```
+-------------------+
                        |   Rider Client    |
                        | iOS / Android App |
                        +---------+---------+
                                  |
                                  v
                           +------+------+
                           | API Gateway |
                           | auth, rate  |
                           | limiting    |
                           +------+------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
            +-------+--------+          +-------+--------+
            |  Ride Service  |          | Notification   |
            | fares, rides,  |<-------->| Service        |
            | ride state     |          | push to driver |
            +---+--------+---+          +-------+--------+
                |        |                      |
                |        |                      v
                |        |               +------+------+
                |        |               | Driver      |
                |        |               | Client      |
                |        |               +------+------+
                |        |                      |
                |        |                      v
                |        |               PATCH /rides/{id}
                |        |
                |        +------------------------------+
                |                                       |
                v                                       v
        +-------+--------+                    +---------+---------+
        | Ride DB        |                    | Fare DB           |
        | rides, status, |                    | estimate records  |
        | rider, driver  |                    +-------------------+
        +----------------+

                Fare estimate path
                ------------------
 Rider Client -> API Gateway -> Ride Service -> Maps API
                                      |
                                      v
                                   Fare DB
                                      |
                                      v
                                 Fare response


                        Matching and location path
                        --------------------------

+-------------------+        POST /drivers/location      +----------------------+
|   Driver Client   | ---------------------------------> |  Location Service    |
| GPS updates       |                                    | ingest driver coords |
+-------------------+                                    +----------+-----------+
                                                                    |
                                                                    v
                                                           +--------+---------+
                                                           | Redis Geo Store  |
                                                           | current driver   |
                                                           | locations        |
                                                           +--------+---------+
                                                                    |
                                                                    v
                                                           +--------+----------+
                                                           | Matching Service  |
                                                           | find nearest      |
                                                           | available driver  |
                                                           +---+-----------+---+
                                                               |           |
                                                               |           v
                                                               |     +-----+------+
                                                               |     | Redis Lock |
                                                               |     | driver TTL |
                                                               |     +-----+------+
                                                               |           |
                                                               v           v
                                                        +------+-----------+------+
                                                        | Ride DB update ride     |
                                                        | requested or accepted   |
                                                        +-------------------------+

Request ride flow
-----------------
```

---

<details open>
<summary><strong>Problem</strong></summary>

Matching riders with drivers in real time, tracking trips, and calculating pricing dynamically.

The hard parts: driver location is a high-frequency ephemeral stream (never the DB), geospatial matching must be fast, and the trip must be durably recorded.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis GEO data is stale when drivers stop sending location updates**

Matching service finds nearby drivers who are actually offline or far away. Dispatching fails, rider waits.

_Fix:_ Driver location has a TTL in Redis (e.g., 15 seconds). Expired entries automatically removed from the geo index. Driver app sends heartbeat even when idle. If location key expires, driver is considered offline.

**Matching service assigns the same driver to two riders simultaneously**

Double booking. Driver gets two pickup requests. One rider is stranded.

_Fix:_ Driver state machine: AVAILABLE → DISPATCHED (atomic in Redis). Use SETNX driver:{id}:state = DISPATCHED. First match wins. Second match sees DISPATCHED status and picks the next available driver.

**City-level demand spike (concert ending)**

GEORADIUS returns no available drivers in a 2km radius. Demand/supply ratio spikes 10×.

_Fix:_ Expand search radius dynamically when no drivers found. Notify nearby idle drivers. Surge pricing activation triggers driver supply response. Circuit breaker: stop new requests if matching latency exceeds 5 seconds.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10M drivers active at peak, location update every 4s, 5M ride requests/day |
| Read QPS | GEORADIUS queries: 5M requests / 86400 ≈ 58 matching QPS — low, surprisingly |
| Write QPS | 10M drivers × 1 update / 4s = 2.5M GEOADD writes/s — this is the scaling challenge |
| Storage | Driver location in Redis: 10M entries × 50 bytes ≈ 500 MB. Trips in PG: 5M/day × 500 bytes × 365 ≈ 914 GB/year. |
| Cache math | 2.5M GEOADD/s is the number. Redis handles ~500K ops/s per node → need 5 Redis nodes just for location writes. Or: batch updates per driver to 1/8s (still fresh enough for matching). |
| Verdict | Location write throughput (2.5M/s) is the actual scaling challenge. Not ride requests (58 QPS). Every optimization discussion should start here. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Redis GEO vs. PostGIS vs. custom geospatial index**

→ Redis GEO (GEOADD / GEORADIUS)

PostGIS at 2.5M writes/s is impossible — it's a relational DB. Custom geohash grid in Redis is equivalent to what Redis GEO does internally. Redis GEO is built-in, battle-tested, and handles our write volume with horizontal sharding.

_Revisit when:_ H3 hexagonal indexing (Uber's actual approach) gives more uniform cell sizes than geohash and handles region boundaries more cleanly.

**Trip state in PG vs. distributed state machine**

→ PostgreSQL with state machine transitions

Trip state changes are infrequent (one per minute per trip) and require ACID guarantees (billing, receipts). PG at 58 QPS is trivial. Distributed state machine (Temporal, Conductor) adds complexity not needed at this scale.

_Revisit when:_ Temporal workflow engine for complex multi-step trip flows (shared rides, multi-leg trips) where saga pattern is needed.

**Surge pricing: reactive vs. predictive**

→ Reactive (demand/supply ratio) with ML prediction overlay

Pure reactive surge leads to oscillation (surge → drivers flood in → surge drops → drivers leave → surge returns). ML prediction smooths this: forecast demand 15 minutes ahead, pre-surge before the spike.

_Revisit when:_ Pure reactive is simpler. Add ML prediction only when driver supply oscillation becomes a measurable problem.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle driver location privacy?**

Driver location is visible to matched rider only (not general public). Location is ephemeral in Redis — never persisted to PG in raw form. Trip GPS logs are retained for dispute resolution but not accessible in real-time by anyone except matched rider/driver.

**How does ETA calculation work?**

ETA = routing API call (Google Maps / Mapbox / internal routing). Inputs: driver current location + destination. Returns route + ETA. Cached per (driver_location_geohash, destination_geohash) with 1-min TTL. Never calculated from raw Euclidean distance — road network matters.

**How would you handle simultaneous ride requests from 10,000 users in one city?**

Matching is per-request (not batched). Each request does a GEORADIUS query independently. Redis GEO handles concurrent reads trivially. The bottleneck is driver availability, not the matching system. At 10K simultaneous requests in one area, surge pricing reduces demand while the driver fleet adjusts.

**How do you handle the vehicle type selection (UberX vs. UberXL vs. Black)?**

Redis GEO index is per vehicle type. GEORADIUS query targets the correct type index. Driver registration sets their vehicle type, which determines which geo index they're added to. Adding a vehicle type adds one more Redis GEO set — linear complexity.

**What happens if the rider cancels after the driver is dispatched?**

Trip state machine: DISPATCHED → CANCELLED. Driver state: DISPATCHED → AVAILABLE (immediate). Kafka event triggers: cancel notification to driver, cancel fee calculation (if within grace period), driver geo index re-add. Idempotent — driver can receive cancel event more than once safely.

**How do you implement shared rides (UberPool)?**

Matching becomes a combinatorial problem: find driver + route that accommodates multiple riders with minimal detour. Store active pool trips with ordered pickup/dropoff waypoints. Re-match only between trips, not mid-ride unless rider cancels.

**How do you shard location data globally?**

Shard Redis GEO by city/region geohash prefix. Cross-region matching only for airport/long-distance product modes. Each shard owns its driver supply pool.

**How do you recover if matching service crashes mid-dispatch?**

Trip row created with status MATCHING and driver_id candidate. On retry, idempotent matching reads existing trip — re-confirms same driver if still DISPATCHED, otherwise picks next. Never double-dispatch same trip_id.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG with PostGIS for driver location. Polling every 30s. 500ms matching. Works for 1 city with 1000 drivers. Breaks at any real load.

**v2 — Redis GEO** — Redis GEO for driver location (ephemeral). PG for trip state machine. WebSocket for live driver tracking. Basic surge pricing. Handles 10M drivers globally.

**v3 — Prediction + scale** — ML demand prediction for proactive surge. H3 hexagonal geo indexing. Multi-modal (Express Pool, Delivery). Real-time ETA with road network. Driver incentive system.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Uber is not storing rides. It is matching the right driver to the right rider very fast while the whole system is constantly changing.

There are three big scaling pain points. First, driver location is a huge real time write stream. If millions of drivers send updates every few seconds, a normal database gets overwhelmed, and proximity search on raw latitude and longitude is too slow. Second, matching needs low latency and strong enough consistency. You cannot assign the same driver to two riders at once, so the system needs some kind of lock or reservation while still moving quickly. Third, demand is bursty and local. A concert ending can create a massive spike in one neighborhood, so even if global traffic looks fine, one region can become a hotspot.

A good interview summary is this. Uber is hard because it combines real time geospatial search, hotspot traffic, and correctness during matching. You are not just finding a nearby driver. You are doing it from fast moving data, under heavy local spikes, without double booking drivers.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: rider requests ride, match to nearby driver, track driver location, complete trip, compute fare. Out of scope unless asked: surge pricing algorithm details, driver incentives, scheduled rides.
- **Driver location — Redis GEO only** — 2.5M GEOADD writes/sec. Redis handles this; PostgreSQL collapses at ~50K writes/sec. Location is ephemeral: EXPIRE on every key (15s TTL). Expired = driver offline. Never persist raw location to PostgreSQL.
- **GEORADIUS for matching** — GEORADIUS drivers:available LONGITUDE LATITUDE 5 km ASC COUNT 10. Returns nearest available drivers. Filter by AVAILABLE state (SETNX driver:{id}:status=DISPATCHED — atomic dispatch).
- **Trip state machine — PostgreSQL** — REQUESTED → MATCHED → DRIVER_EN_ROUTE → IN_PROGRESS → COMPLETED. Each transition is an immutable event. PostgreSQL for ACID durability — the trip is the financial record.
- **Surge pricing — geohash cells** — Divide city into geohash cells (~1 km²). Compute demand/supply ratio per cell every 2 min. Surge multiplier = f(ratio). ML demand prediction pre-surges 15 min ahead to prevent oscillation.
- **Driver state machine — prevent double-dispatch** — AVAILABLE → DISPATCHED: SETNX driver:{id}:status = DISPATCHED. First matcher wins. Second matcher finds key exists → pick next driver. TTL on the key prevents stuck state if matcher crashes.
- **Failure mode to name** — Matching service assigns driver but crashes before confirming to rider: driver gets a ping, rider sees no driver. On client retry, matching service finds driver already DISPATCHED to this trip (idempotency via trip_id) — re-confirms without re-dispatching.

> The staff-level key: driver location is ephemeral (Redis only). PostgreSQL is trip durability only.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Redis for driver location vs PostgreSQL** — Redis handles 2.5M GEOADD writes/sec. PostgreSQL at that write rate collapses. Redis is the only correct choice for ephemeral location data — never write driver location to PG on every update.

**GEORADIUS vs H3 hexagonal indexing** — Redis GEORADIUS is built-in and works well. H3 hexagonal cells give more uniform area coverage and cleaner region boundaries. H3 is Uber's production choice but adds implementation complexity — GEORADIUS is the right interview answer.

**Surge pricing reactive vs predictive** — Pure reactive surge creates oscillation — prices spike, drivers flood in, prices drop, drivers leave. ML demand prediction (15-min lookahead) pre-surges before the spike and smooths driver supply. Tradeoff: model infrastructure required.

**Pull matching vs push dispatch** — Pull: workers poll Redis for ride requests. Push: matching service dispatches directly to driver app. Push is lower latency (driver gets notified immediately) but requires reliable push delivery. Pull is simpler but adds polling overhead.

> "Driver location = Redis, trip = PostgreSQL. Never write ephemeral high-frequency data to a relational database."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Driver location — ephemeral Redis GEO at 2.5M writes/second
> [!CAUTION]
> **🔴 Weak** — write driver location to PostgreSQL on every update — it's the source of truth. At 10M drivers × 1 update/4s = 2.5M writes/sec, PostgreSQL collapses at ~50K writes/sec
>
> [!WARNING]
> **🟡 Strong** — Redis GEO (GEOADD) for location — in-memory, O(1) write, supports GEORADIUS queries natively. Set a 15-second TTL on every driver key: if the driver stops sending updates, they're automatically removed from the geo index
>
> [!TIP]
> **🟢 Staff+** — never write ephemeral high-frequency data to a relational database. Location is ephemeral — it changes every 4 seconds and is only needed for the current moment. PostgreSQL is for durable trip records, not live location pings. This distinction drives the entire architecture


#### Deep dive 2: Matching — low-latency GEORADIUS with driver state management
> [!CAUTION]
> **🔴 Weak** — query Redis for nearby drivers, pick the closest, dispatch
>
> [!WARNING]
> **🟡 Strong** — GEORADIUS returns candidates but dispatch requires an atomic state check — the same driver cannot be assigned to two riders simultaneously. SETNX driver:{id}:status = DISPATCHED: first matcher wins, second finds the key already set and picks the next available driver. TTL on the key prevents stuck DISPATCHED state if the matching service crashes mid-dispatch
>
> [!TIP]
> **🟢 Staff+** — : matching service assigns a driver but crashes before confirming to the rider. On client retry, the matching service finds the driver already DISPATCHED to this trip (idempotency via trip_id) and re-confirms without re-dispatching. The trip_id is the idempotency key, not the driver_id


#### Deep dive 3: Trip state machine and financial integrity
> [!CAUTION]
> **🔴 Weak** — store current trip status in Redis for fast access
>
> [!WARNING]
> **🟡 Strong** — PostgreSQL for all trip state — REQUESTED → MATCHED → DRIVER_EN_ROUTE → ARRIVED → IN_PROGRESS → COMPLETED. Each transition uses optimistic locking (UPDATE trips SET status=? WHERE id=? AND status=? AND version=N). Zero-row update means a concurrent transition happened — retry or surface the conflict
>
> [!TIP]
> **🟢 Staff+** — the trip is the financial record. It must be durable, auditable, and ACID. Event sourcing for the trip: every state transition is an immutable event row. The current state is derived from the event log. This gives a complete audit trail for fare disputes and is required for financial regulatory compliance


_Why the deep dives connect to the scaling problem: "Real-time geospatial search, hotspot traffic, correctness during matching." Each deep dive addresses one layer._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Separation-of-concerns script.

2. "Before I start: are we designing the full platform — rider, driver, matching, payments — or focusing on the core matching and location flow?"

3. "Good — core flow. Functional requirements: rider requests ride, system matches to a nearby driver, both track each other live, trip completes and fare is calculated. Out of scope: surge pricing algorithm internals, driver incentives."

4. "Scale: 10M drivers active at peak, location update every 4 seconds. That's 2.5M writes per second just for location — this is the number that drives the entire architecture."

5. "The core insight I'd lead with: separate ephemeral driver location from durable trip state. These have completely different consistency and storage requirements."

6. "Driver location: Redis GEO with GEOADD. TTL of 15 seconds — if a driver stops sending updates, they're automatically removed from the index. Never write location to PostgreSQL. 2.5M writes/sec would destroy a relational database."

7. "Matching: GEORADIUS returns nearby drivers. Dispatch atomically using SETNX on the driver's status key — first matcher wins. Second matcher finds the key already set and picks the next available driver."

8. "Trip state machine in PostgreSQL: REQUESTED → MATCHED → DRIVER_EN_ROUTE → IN_PROGRESS → COMPLETED. Each transition is a row in the event log. PostgreSQL gives ACID durability — the trip is the financial record."

9. "Live tracking: after matching, rider's app opens a WebSocket. Driver location updates flow from Redis through the delivery service to the rider's connection every 4 seconds."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                        |   Rider Client    |
                        | iOS / Android App |
                        +---------+---------+
                                  |
                                  v
                           +------+------+
                           | API Gateway |
                           | auth, rate  |
                           | limiting    |
                           +------+------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
            +-------+--------+          +-------+--------+
            |  Ride Service  |          | Notification   |
            | fares, rides,  |<-------->| Service        |
            | ride state     |          | push to driver |
            +---+--------+---+          +-------+--------+
                |        |                      |
                |        |                      v
                |        |               +------+------+
                |        |               | Driver      |
                |        |               | Client      |
                |        |               +------+------+
                |        |                      |
                |        |                      v
                |        |               PATCH /rides/{id}
                |        |
                |        +------------------------------+
                |                                       |
                v                                       v
        +-------+--------+                    +---------+---------+
        | Ride DB        |                    | Fare DB           |
        | rides, status, |                    | estimate records  |
        | rider, driver  |                    +-------------------+
        +----------------+

                Fare estimate path
                ------------------
 Rider Client -> API Gateway -> Ride Service -> Maps API
                                      |
                                      v
                                   Fare DB
                                      |
                                      v
                                 Fare response


                        Matching and location path
                        --------------------------

+-------------------+        POST /drivers/location      +----------------------+
|   Driver Client   | ---------------------------------> |  Location Service    |
| GPS updates       |                                    | ingest driver coords |
+-------------------+                                    +----------+-----------+
                                                                    |
                                                                    v
                                                           +--------+---------+
                                                           | Redis Geo Store  |
                                                           | current driver   |
                                                           | locations        |
                                                           +--------+---------+
                                                                    |
                                                                    v
                                                           +--------+----------+
                                                           | Matching Service  |
                                                           | find nearest      |
                                                           | available driver  |
                                                           +---+-----------+---+
                                                               |           |
                                                               |           v
                                                               |     +-----+------+
                                                               |     | Redis Lock |
                                                               |     | driver TTL |
                                                               |     +-----+------+
                                                               |           |
                                                               v           v
                                                        +------+-----------+------+
                                                        | Ride DB update ride     |
                                                        | requested or accepted   |
                                                        +-------------------------+

Request ride flow
-----------------
```


</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-18)
