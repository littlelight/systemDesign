# Unique ID generator (Snowflake)

**Medium** · 64-bit IDs · Clock drift · Coordination

Tags: `Snowflake`, `ZooKeeper`, `Clock sync`, `64-bit layout`, `Coordination`

## Data flow

Each ID is 64 bits: timestamp (ms) + worker_id (from ZooKeeper lease) + per-ms sequence. IDs are time-sortable and unique without central DB roundtrip per ID.


> 64-bit: 41b timestamp + 10b worker + 12b sequence  |  NTP required  |  worker_id from ZK lease

## Architecture diagram

```
[Service] --> [ID Generator lib]
                      |
            worker_id from ZK lease
            seq++ per millisecond
            pack 64-bit ID
```

Draw three fields in the 64-bit ID. ZK assigns worker_id. Generation is local after lease.


---

<details open>
<summary><strong>Problem</strong></summary>

Generate unique, roughly time-ordered IDs at high throughput across thousands of servers.

Hard parts: coordination without per-ID DB calls, clock drift, and sequence overflow within one millisecond.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Clock moves backward after NTP correction**

Risk duplicate IDs if not handled.

_Fix:_ Wait until clock >= last_timestamp. Refuse to generate and alert.

**ZooKeeper unavailable**

Cannot assign new worker_ids. New instances cannot start.

_Fix:_ Pre-allocated worker_id pools per instance. Renew lease in background.

**Sequence overflow within 1ms**

>4096 IDs in one ms on one worker.

_Fix:_ Wait next millisecond. Add second worker or widen sequence bits.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1024 workers, 1M IDs/s cluster-wide |
| Read QPS | 1M IDs/s — trivial in-process |
| Write QPS | No DB writes per ID |
| Storage | Zero storage for ID generation itself |
| Cache math | ZK stores worker registry only — KB scale |
| Verdict | Throughput is not the problem. Correctness under clock drift is. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Snowflake vs UUID**

→ Snowflake for DB-primary keys

Sortable, index-friendly, high throughput.

_Revisit when:_ UUID v7 (time-ordered) as modern alternative.

**ZK vs DB for worker_id**

→ ZooKeeper/etcd

Fast lease, ephemeral nodes, designed for coordination.

_Revisit when:_ DB lease if ZK ops burden too high.

**Custom epoch start**

→ Custom epoch (Twitter 2010)

Extends effective timestamp range.

_Revisit when:_ Unix epoch if simplicity preferred.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**What if two workers get the same worker_id?**

ZK ephemeral nodes prevent duplicates. On conflict, one instance must exit and re-register.

**Can IDs leak information?**

Yes — timestamp and worker_id are embedded. Do not expose externally if enumeration is a concern.

**How do you migrate from auto-increment?**

Dual-write period: new rows get Snowflake, old rows keep int IDs. Application handles both types during migration.

**How do you handle leap seconds?**

NTP smears leap second. Monitor clock monotonicity. Some systems use logical clock instead of wall clock.

**Multi-region ID generation?**

Embed datacenter bits in worker_id range. Avoid cross-region ZK for latency.

**What about JavaScript Number precision?**

Snowflake exceeds JS safe integer — return IDs as strings in JSON APIs.

**How do you test uniqueness?**

Chaos: kill workers, force clock skew in test env, generate billions in parallel, verify no collisions.

**When is DB auto-increment fine?**

Single-region, <10K writes/s, no sortable ID requirement — Postgres sequence is simpler.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — DB sequence** — SELECT nextval(). Simple. Bottleneck ~50K/s.

**v2 — Snowflake** — ZK worker leases. Per-process generation. Millions/s.

**v3 — Multi-DC** — DC-bit ranges. Monitoring for clock skew. String IDs in APIs.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

ID generation is embarrassingly parallel once worker_ids are assigned. The coordination pain is clock sync and worker_id leasing, not throughput.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **64-bit layout** — 41b timestamp | 10b worker_id | 12b sequence = 4096 IDs/ms per worker.
- **Worker ID lease** — ZooKeeper/etcd assigns worker_id. Ephemeral node — reclaimed on crash.
- **Clock synchronization** — NTP required. If clock moves backward: wait until caught up or fail loudly — never emit duplicate IDs.
- **Sequence per millisecond** — INCR sequence within same ms. Rollover → wait next ms.
- **No DB per ID** — Generation is in-process after worker_id assigned — millions/sec per node.
- **Sortable** — Time-ordered IDs useful for sharding and debugging.
- **Not cryptographic** — IDs are predictable — do not use as security tokens.

> Timestamp + worker + sequence. Mention clock backward handling — interviewers always ask.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Snowflake vs DB auto-increment** — DB: bottleneck + single point. Snowflake: decentralized, sortable, no per-ID network call.

**Snowflake vs UUID v4** — UUID: random, not sortable, index fragmentation in B-trees. Snowflake: time-ordered, better DB index locality.

**Central counter vs Snowflake** — Central Redis INCR works but is SPOF and network per ID. Snowflake scales horizontally.

**48-bit vs 64-bit timestamp** — More timestamp bits = longer lifespan before overflow. Twitter Snowflake 41b ≈ 69 years.

> "Snowflake trades predictability for throughput and sortability — correct for internal IDs, not public tokens."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: 64-bit layout and throughput math
_12-bit sequence = 4096 IDs/ms per worker. 10-bit worker = 1024 machines. Cluster theoretical max ≈ 4M IDs/ms_

> [!CAUTION]
> **🔴 Weak** — use UUID
>
> [!WARNING]
> **🟡 Strong** — explain bit allocation and why sortability helps DB indexing
>
> [!TIP]
> **🟢 Staff+** — Name the metric you'd alert on and when you'd revisit this design.


#### Deep dive 2: Clock drift and backward time
_NTP sync required. If current_ms < last_ms: wait or error. Never reuse timestamp+sequence combo_

> [!CAUTION]
> **🔴 Weak** — Use system clock on each machine — NTP is optional.
>
> [!WARNING]
> **🟡 Strong** — NTP sync required. If current_ms < last_ms: wait or error. Never reuse timestamp+sequence combo
>
> [!TIP]
> **🟢 Staff+** — leap seconds and VM migration can move clock backward — monitor and alert


#### Deep dive 3: Worker ID coordination
_ZooKeeper ephemeral sequential nodes assign worker_id. On crash, ID reclaimed after session timeout. Alternative: DB lease table with heartbeat — slower failover_

> [!CAUTION]
> **🔴 Weak** — Pick a random worker ID at process start.
>
> [!WARNING]
> **🟡 Strong** — ZooKeeper ephemeral sequential nodes assign worker_id. On crash, ID reclaimed after session timeout. Alternative: DB lease table with heartbeat — slower failover
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Multi-datacenter IDs
_Per-DC worker_id ranges avoid cross-DC ZK dependency. Or dedicated ID service per region with DC bits in layout_

> [!CAUTION]
> **🔴 Weak** — UUID v4 everywhere — collisions are negligible.
>
> [!WARNING]
> **🟡 Strong** — Per-DC worker_id ranges avoid cross-DC ZK dependency. Or dedicated ID service per region with DC bits in layout
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Bit-layout script.

2. "Need unique, time-sortable IDs at millions/sec without DB per ID."

3. "Snowflake: 41b timestamp + 10b worker_id + 12b sequence."

4. "Worker_id from ZooKeeper ephemeral lease — reclaimed on crash."

5. "Sequence increments per millisecond; wait next ms on overflow."

6. "If clock goes backward, block generation — duplicates are worse than brief unavailability."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
[Service] --> [ID Generator lib]
                      |
            worker_id from ZK lease
            seq++ per millisecond
            pack 64-bit ID
```

Draw three fields in the 64-bit ID. ZK assigns worker_id. Generation is local after lease.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-30)
