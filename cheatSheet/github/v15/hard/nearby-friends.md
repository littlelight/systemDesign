# Nearby friends

**Hard** · Geohash grid · Redis GEO · Location fan-out

Tags: `Redis GEO`, `Geohash`, `WebSocket`, `Quadtree`, `Location updates`

## Data flow

Users report GPS every 30s → geohash cell → Redis GEOADD in per-cell sorted set. Finding nearby friends = query same + adjacent cells. Notify matches via WebSocket. Location is ephemeral — not written to PostgreSQL.


> Update location in Redis only — never PG per GPS fix  |  Query geohash neighbors  |  Push via WebSocket

## Architecture diagram

```
GPS -> Location Svc -> Redis GEO (per geohash cell)
Friend list <- PostgreSQL
Matcher -> intersect -> WebSocket push
```

Two stores: ephemeral geo in Redis, social graph in PG.


---

<details open>
<summary><strong>Problem</strong></summary>

Show users which friends are physically nearby in real time.

Hard parts: high-frequency location updates, efficient geo queries, and privacy/battery constraints.

</details>


<details>
<summary><strong>Failures</strong></summary>

**User on cell boundary**

Missed nearby friends if only querying one cell.

_Fix:_ Always query cell + 8 neighbors.

**Redis memory exhaustion**

Millions of live locations.

_Fix:_ TTL on keys. Only store active users last 5 min.

**Push notification spam**

Notify every location tick.

_Fix:_ State machine: notify only on enter/exit radius.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 50M DAU, 2M concurrent sharing location, update/30s |
| Read QPS | Nearby queries: 2M/60s ≈ 33K/s |
| Write QPS | 2M/30s ≈ 67K GEOADD/s |
| Storage | 2M × 100B ≈ 200 MB Redis |
| Cache math | Friend list cache per user in Redis |
| Verdict | Redis handles write rate; query uses cell intersection. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Redis GEO vs custom geohash**

→ Redis GEO (geohash under hood)

Built-in GEOADD/GEORADIUS — battle-tested.

_Revisit when:_ Custom quadtree for uneven density maps.

**Update frequency**

→ Adaptive 30s–5min

Battery and write volume scale with frequency.

_Revisit when:_ Fixed 30s if product requires live map.

**Friend graph lookup**

→ Cache friend IDs in Redis per user

Avoid PG join on every geo query.

_Revisit when:_ PG query if friend list small and rare.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle users without GPS?**

Fall back to coarse IP geolocation — mark precision as LOW in UI.

**How do you support 'nearby strangers' mode?**

Skip friend intersection — query all users in cell. Stronger privacy fuzzing required.

**How do you scale to multiple cities?**

Shard Redis by geohash prefix region. Users query local shard.

**How do you prevent stalking?**

Mutual opt-in, precision limits, sharing session timeout, block list.

**How do you test geo correctness?**

Unit test haversine + cell neighbor coverage. Simulation with known coordinates.

**How do you handle indoor GPS drift?**

Kalman filter smooth positions. Minimum movement threshold before updating cell.

**How do you show 'last seen' vs live?**

Separate last_seen timestamp in PG; live map uses Redis TTL presence only.

**How does this differ from Yelp geo search?**

Yelp searches businesses (static POIs). Nearby friends tracks moving users with ephemeral coordinates.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Poll + PG** — Store lat/long in PG. Too slow for live.

**v2 — Redis GEO** — Ephemeral locations. Geohash queries. Friend intersection.

**v3 — Production** — Adaptive updates, privacy modes, enter/exit push FSM, regional sharding.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

High-frequency ephemeral writes and geo indexing are the pain points — not friend graph size.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Ephemeral location in Redis** — 2M users × update/30s = 67K writes/s — Redis yes, PostgreSQL no.
- **Geohash cells** — Map lat/long to cell. Query cell + 8 neighbors covers edge cases.
- **Friend graph in PG** — Friendships durable in PostgreSQL. Intersect geo results with friend list.
- **Push on proximity** — WebSocket notify when friend enters radius — not poll.
- **Privacy controls** — Ghost mode, precision reduction, sharing window.
- **Battery** — Adaptive update interval: stationary → 5 min, moving → 30s.
- **Stale location TTL** — EXPIRE location keys 5 min — no ghost users on map.

> Redis for location, PG for friendships, geohash for query.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Redis GEO vs PostGIS** — Redis: in-memory, ephemeral, fast. PostGIS: durable, complex queries, slower — wrong for live location stream.

**Geohash vs quadtree** — Geohash simpler with Redis. Quadtree better for variable density — more complex.

**Push vs poll for updates** — WebSocket push for real-time map. Poll drains battery.

**Exact GPS vs fuzzed** — Fuzz location to ~100m for privacy unless user opts in to precise.

> "Location is ephemeral in Redis; friendships durable in PG; geohash makes neighbor search O(cells) not O(users)."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Write path at scale
_67K GEOADD/s is Redis-comfortable. Never persist every fix to PG. TTL keys expire stale users_

> [!CAUTION]
> **🔴 Weak** — Oversimplify write path at scale — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — 67K GEOADD/s is Redis-comfortable. Never persist every fix to PG. TTL keys expire stale users
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Query algorithm
_Compute user cell. Fetch users in cell + 8 neighbors. Filter by haversine distance < R. Intersect with friend IDs from PG/cache_

> [!CAUTION]
> **🔴 Weak** — Oversimplify query algorithm — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Compute user cell. Fetch users in cell + 8 neighbors. Filter by haversine distance < R. Intersect with friend IDs from PG/cache
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Privacy and precision
_Reduce precision to 5-char geohash (~5km) by default. Ghost mode deletes Redis entry immediately_

> [!CAUTION]
> **🔴 Weak** — Oversimplify privacy and precision — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Reduce precision to 5-char geohash (~5km) by default. Ghost mode deletes Redis entry immediately
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Notification dedup
_Do not push every 30s if friend still nearby. State machine: ENTERED_NEARBY → INSIDE → EXITED. Push only on transitions_

> [!CAUTION]
> **🔴 Weak** — Retry until delivery succeeds — duplicates are rare.
>
> [!WARNING]
> **🟡 Strong** — Do not push every 30s if friend still nearby. State machine: ENTERED_NEARBY → INSIDE → EXITED. Push only on transitions
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Geo + graph script.

2. "Location updates high frequency — Redis only, TTL 5 min."

3. "Geohash cell + neighbors for nearby query."

4. "Intersect geo candidates with friend list from PG."

5. "WebSocket push on enter/exit proximity, not continuous poll."

6. "Privacy: fuzz precision, ghost mode, adaptive update rate."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
GPS -> Location Svc -> Redis GEO (per geohash cell)
Friend list <- PostgreSQL
Matcher -> intersect -> WebSocket push
```

Two stores: ephemeral geo in Redis, social graph in PG.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-35)
