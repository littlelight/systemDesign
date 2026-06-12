# Gaming leaderboard

**Medium** · Redis ZSET · ZREVRANK · Top-K cache

Tags: `Redis Sorted Set`, `ZINCRBY`, `Cassandra`, `Top-K`, `Seasonal`

## Data flow

Match ends → ZINCRBY updates player score in Redis sorted set. Top-100 = ZREVRANGE 0 99. Individual rank = ZREVRANK in O(log N). Cassandra stores durable history to rebuild Redis after crash.


> ZINCRBY = atomic score update  |  ZREVRANK = O(log N) rank  |  Cassandra rebuilds Redis on failure

## Architecture diagram

```
Match -> Score Service -> ZINCRBY leaderboard
                |-> Cassandra (audit)
API -> ZREVRANGE top-100 (cached)
API -> ZREVRANK player_id
```

One diagram: write path ZINCRBY, read paths ZREVRANGE and ZREVRANK.


---

<details open>
<summary><strong>Problem</strong></summary>

Real-time global leaderboard for millions of players: update scores instantly, return top-100 and any player rank fast.

Redis sorted set is purpose-built — DB RANK() scans are too slow at 100M players.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis crash loses ZSET**

Ranks unknown until rebuild.

_Fix:_ AOF persistence. Rebuild from Cassandra.

**Concurrent score updates race**

Lost update if read-modify-write.

_Fix:_ ZINCRBY only — never GET then SET.

**1M simultaneous top-100 requests**

Redis read storm.

_Fix:_ Cache top-100 1s TTL.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 100M players, 1M active, 167K ZINCRBY/s, 100K top-100 reads/s |
| Read QPS | Top-100 cached → ~1 ZREVRANGE/s effective |
| Write QPS | 167K ZINCRBY/s — within Redis 1M ops/s |
| Storage | 100M × 40B ≈ 4 GB ZSET |
| Cache math | ZREVRANK 100K/s needs Redis cluster cores |
| Verdict | Individual rank reads scale harder than writes. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Seasonal reset**

→ New ZSET per season

Preserves history in Cassandra.

_Revisit when:_ Score reset in-place loses history.

**Friends leaderboard**

→ ZINTERSTORE on-demand

Precompute fan-out too expensive at 167K updates/s.

_Revisit when:_ Precompute for small friend graphs only.

**Anti-cheat**

→ Server validates score before ZINCRBY

Never trust client-reported final score.

_Revisit when:_ Replay validation for esports.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do daily/weekly/monthly boards coexist?**

Three ZSETs updated in one pipeline per match. Independent scores per period.

**How do you show players around me?**

ZREVRANK for R, ZREVRANGE R-5 R+5. Handle boundary at rank 0 and last rank.

**How do you scale to 1B players?**

40 GB still fits large Redis node. Write throughput driven by concurrent active users, not registered count.

**How do you paginate leaderboard?**

ZREVRANGE start stop WITHSCORES. Page size 100.

**How do you tie-break equal scores?**

Redis sorts by score then lexicographic member. Encode tie-breaker in member or use composite score (score * 1e6 + timestamp).

**How do you rebuild after Redis loss?**

Stream Cassandra scores, batch ZADD. Show 'rank updating' for individual ranks; serve cached top-100.

**How do you handle team leaderboards?**

Separate ZSET per team_id. Team score = sum of member ZINCRBY in pipeline.

**How do you prevent score inflation hacks?**

Server-side match validation. Anomaly detection on score velocity. Manual review queue.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — SQL RANK()** — Minutes at 1M players.

**v2 — Redis ZSET** — Real-time rank. Cassandra backup.

**v3 — Multi-board** — Global/regional/friends/seasonal. Anti-cheat. Cached top-100.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Rank queries stay O(log N) as N grows. Real limits are write throughput during tournaments and top-100 read storms — both solved with ZINCRBY and short TTL cache.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Redis sorted set core** — ZADD/ZINCRBY/ZREVRANK/ZREVRANGE — all O(log N).
- **ZINCRBY not read-modify-write** — Atomic increment — no race on concurrent match results.
- **Cassandra durability** — Redis is serving layer; rebuild ZSET from Cassandra on failure.
- **Top-100 cache** — Cache ZREVRANGE result 1s TTL — tournament end storm.
- **Multiple boards** — Global, regional, friends, seasonal — separate ZSETs updated in pipeline.
- **Rank around me** — ZREVRANK then ZREVRANGE R-5 R+5.
- **Server-side validation** — Anti-cheat: validate score server-side before ZINCRBY.

> ZINCRBY, ZREVRANGE, ZREVRANK — three Redis commands, entire leaderboard.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Redis ZSET vs SQL RANK()** — SQL RANK() is O(N log N). ZREVRANK is O(log N) — only correct answer for real-time rank.

**Real-time vs batch** — Players expect immediate rank change after match — real-time ZINCRBY required.

**Friends board: precompute vs on-demand** — ZINTERSTORE on-demand for small friend lists; precompute only if friends board is primary UI.

**Single ZSET vs sharded** — 100M players ≈ 4 GB — single node often enough. Shard when memory exceeds node capacity.

> "Redis sorted set is the right data structure — name ZINCRBY and ZREVRANK explicitly."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Why sorted set beats SQL
_At 100M players RANK() OVER scans entire table. ZREVRANK is ~27 comparisons. Top-100 is O(100) regardless of N_

> [!CAUTION]
> **🔴 Weak** — Oversimplify why sorted set beats sql — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — At 100M players RANK() OVER scans entire table. ZREVRANK is ~27 comparisons. Top-100 is O(100) regardless of N
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Durability and rebuild
_Redis AOF + RDB. On total loss: batch ZADD from Cassandra — minutes for 100M players. Serve stale cached top-100 during rebuild_

> [!CAUTION]
> **🔴 Weak** — Oversimplify durability and rebuild — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Redis AOF + RDB. On total loss: batch ZADD from Cassandra — minutes for 100M players. Serve stale cached top-100 during rebuild
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Write throughput and tournament storms
_167K ZINCRBY/s within Redis capacity. Top-100 cache with 1s TTL collapses read storm to 1 ZREVRANGE/s_

> [!CAUTION]
> **🔴 Weak** — Oversimplify write throughput and tournament storms — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — 167K ZINCRBY/s within Redis capacity. Top-100 cache with 1s TTL collapses read storm to 1 ZREVRANGE/s
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Seasonal resets without downtime
_RENAME leaderboard:daily to leaderboard:daily:yesterday atomically at midnight. New empty set for new period_

> [!CAUTION]
> **🔴 Weak** — Oversimplify seasonal resets without downtime — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — RENAME leaderboard:daily to leaderboard:daily:yesterday atomically at midnight. New empty set for new period
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Data-structure-first script.

2. "Redis sorted set: ZINCRBY on score update, ZREVRANGE for top-100, ZREVRANK for my rank."

3. "ZINCRBY is atomic — concurrent match results safe."

4. "Cassandra stores history; Redis is serving layer."

5. "Cache top-100 one second during tournament ends."

6. "Multiple boards updated in one Redis pipeline."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Match -> Score Service -> ZINCRBY leaderboard
                |-> Cassandra (audit)
API -> ZREVRANGE top-100 (cached)
API -> ZREVRANK player_id
```

One diagram: write path ZINCRBY, read paths ZREVRANGE and ZREVRANK.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-32)
