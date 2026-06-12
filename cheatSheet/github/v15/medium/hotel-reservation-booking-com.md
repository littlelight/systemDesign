# Hotel reservation (Booking.com)

**Medium** · Inventory lock · Saga · Idempotency

Tags: `PostgreSQL`, `Redis`, `SELECT FOR UPDATE`, `Saga`, `Idempotency`, `Elasticsearch`

## Data flow

Search reads Elasticsearch + Redis availability cache. Booking runs SELECT FOR UPDATE on inventory row, increments reserved count, creates PENDING reservation. Payment + email async via Kafka. Idempotency key prevents double-book on retry.


> SELECT FOR UPDATE on inventory row  |  Idempotency key on book  |  Saga: reserve → pay → confirm

## Architecture diagram

```
Search: User -> ES/Redis (approximate)
Book:   User -> Booking Svc -> PG FOR UPDATE -> Kafka -> Payment -> Confirm
```

Two paths: fast approximate search, exact transactional booking.


---

<details open>
<summary><strong>Problem</strong></summary>

Search hotels, book rooms, prevent double-booking when two users grab the last room.

Correctness dominates — 167 bookings/sec is easy; double-book is catastrophic.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Two users book last room**

Double-book without row lock.

_Fix:_ SELECT FOR UPDATE serializes concurrent bookings.

**Payment succeeds, DB write lost**

Charged customer, no reservation.

_Fix:_ Saga: create PENDING before charge. Compensation on failure.

**Stale Redis shows availability**

User sees room, book fails at DB.

_Fix:_ Cache advisory only. DB is source of truth.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 5K hotels, 100 rooms, 10K booking attempts/min peak |
| Read QPS | Search ~100 QPS peak — modest |
| Write QPS | 167 booking attempts/s peak — single MySQL primary fine |
| Storage | 182M inventory rows ≈ 36 GB |
| Cache math | Redis availability 1.5 GB |
| Verdict | Correctness engineering matters more than sharding. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Pessimistic vs optimistic**

→ SELECT FOR UPDATE

Flash sales create real contention — pessimistic wins.

_Revisit when:_ Optimistic for low-contention niche hotels.

**MySQL vs Cassandra for reservations**

→ MySQL ACID

Double-book on Cassandra is unacceptable.

_Revisit when:_ Cassandra for hotel metadata only.

**Reserve/pay ordering**

→ Reserve before charge

Never charge before durable reservation exists.

_Revisit when:_ Sync pay if PSP fast enough.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How does the 10-minute hold work?**

PENDING_PAYMENT row reduces available count. Lock released after commit. Expiry job cancels stale holds.

**How do you search cheapest week in July?**

Precomputed daily min_price summary table. Flexible search queries summary, not raw inventory.

**How does Booking.com scale search?**

ES + Redis for search. MySQL only on booking path. Two-phase: approximate search, exact verification.

**How do you handle overbooking?**

Business allows reserved > total by buffer %. Configurable per hotel. Not a bug — revenue strategy.

**How do you handle cancellation?**

Decrement reserved, update reservation status, invalidate Redis cache for those dates.

**How do you prevent payment double-charge?**

Idempotency key on payment request tied to reservation_id.

**How do you shard at huge scale?**

Shard inventory by hotel_id. Bookings for one hotel single-shard ACID.

**How do you handle partial date ranges?**

Booking transaction locks one row per night in range. All must be available or entire booking fails.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single DB** — FOR UPDATE only. Works for one hotel chain.

**v2 — Cache + saga** — Redis search cache. Async payment. Hold expiry.

**v3 — ES at scale** — Elasticsearch ranking. CDN hotel content. Multi-region.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Hotel reservation is a correctness problem. Contention spikes on popular dates; search volume dwarfs bookings but must not touch OLTP locks.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Inventory model** — room_inventory(hotel, type, date, total, reserved). Available = total - reserved.
- **Pessimistic lock** — SELECT FOR UPDATE on inventory row during booking transaction.
- **Idempotency key** — Client UUID — retry returns same reservation, no double charge.
- **Search vs book separation** — Search is approximate/fast (ES + Redis). Booking hits authoritative MySQL.
- **10-minute hold** — PENDING_PAYMENT reservation occupies slot. Background job expires holds.
- **Saga** — Reserve sync → pay async → confirm. Compensate (release inventory) if pay fails.
- **Cache advisory only** — Stale cache may show availability; DB lock prevents double-book.

> FOR UPDATE + idempotency + saga. Correctness trilogy.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Pessimistic vs optimistic lock** — Pessimistic correct under flash-sale contention. Optimistic retries fail exactly when load is highest.

**Redis cache vs DB-only search** — 99% traffic is search — cache essential. Booking always validates DB.

**Sync vs async payment** — Reserve sync for instant feedback; pay async for PSP latency tolerance.

**Row inventory vs seat map** — Hotels: count-based rows. Airlines: per-seat map.

> "Search is approximate, booking is exact. SELECT FOR UPDATE is non-negotiable for inventory."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Double-booking prevention
_Transaction: SELECT reserved FROM inventory WHERE hotel=X AND date=Y FOR UPDATE. Check available>0. UPDATE reserved++. INSERT reservation. COMMIT. Second transaction blocks until first completes — sees updated count_

> [!CAUTION]
> **🔴 Weak** — UPDATE balance in SQL — no locking story.
>
> [!WARNING]
> **🟡 Strong** — Transaction: SELECT reserved FROM inventory WHERE hotel=X AND date=Y FOR UPDATE. Check available>0. UPDATE reserved++. INSERT reservation. COMMIT. Second transaction blocks until first completes — sees updated count
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Hold window without long locks
_Lock held only for transaction duration (~50ms). PENDING_PAYMENT row holds inventory. Expiry job releases after 10 min. Payment at T+9:59 still valid if timestamp authoritative_

> [!CAUTION]
> **🔴 Weak** — Retry the charge on any timeout.
>
> [!WARNING]
> **🟡 Strong** — Lock held only for transaction duration (~50ms). PENDING_PAYMENT row holds inventory. Expiry job releases after 10 min. Payment at T+9:59 still valid if timestamp authoritative
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Search at scale without touching OLTP
_Elasticsearch for hotel metadata/ranking. Redis for availability counts updated on booking. Search never acquires row locks_

> [!CAUTION]
> **🔴 Weak** — SELECT * WHERE column LIKE '%query%'.
>
> [!WARNING]
> **🟡 Strong** — Elasticsearch for hotel metadata/ranking. Redis for availability counts updated on booking. Search never acquires row locks
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Saga compensation
_Pay fails → cancel reservation → decrement reserved. Idempotent compensation keyed by reservation_id_

> [!CAUTION]
> **🔴 Weak** — Oversimplify saga compensation — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Pay fails → cancel reservation → decrement reserved. Idempotent compensation keyed by reservation_id
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Correctness-first script.

2. "One challenge: prevent double-booking. Everything flows from that."

3. "Inventory: one row per hotel/room_type/date. Booking: SELECT FOR UPDATE, check available, increment reserved."

4. "Idempotency key on every book request."

5. "Search: ES + Redis cache — approximate. Booking: exact DB validation."

6. "Saga: reserve sync, pay + email async. Expire unpaid holds after 10 minutes."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Search: User -> ES/Redis (approximate)
Book:   User -> Booking Svc -> PG FOR UPDATE -> Kafka -> Payment -> Confirm
```

Two paths: fast approximate search, exact transactional booking.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-31)
