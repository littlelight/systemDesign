# Strava — activity tracking

**Medium** · Time-series · Segment matching · Leaderboard

Tags: `TimescaleDB`, `S3`, `Kafka`, `Redis sorted set`, `Hausdorff distance`

## Data flow

Upload raw GPS → S3. Async processing worker computes stats and matches segments using Hausdorff distance. TimescaleDB stores GPS time-series. Redis sorted set per segment for leaderboards — ZADD on completion, ZREVRANGE for top rankings.


> Polyline encoding ~75% compression  |  Segment match = Hausdorff distance  |  Leaderboard = ZADD/ZREVRANGE

## Architecture diagram

```
+----------------------+
                           |      Mobile App      |
                           | start pause stop     |
                           | local GPS tracking   |
                           | local stats display  |
                           | offline buffer       |
                           +----------+-----------+
                                      |
                         create sync fetch activities
                                      |
                                      v
                           +----------------------+
                           |    API or LB Layer    |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           |   Activity Service   |
                           | activity lifecycle   |
                           | ingest route uploads |
                           | fetch activity feed  |
                           +-----+-----------+----+
                                 |           |
                                 |           |
                                 v           v
                    +------------------+   +------------------+
                    |   Activities DB   |   |    Friends DB    |
                    | activity metadata |   | user friendships |
                    | route points      |   +------------------+
                    | state log         |
                    +------------------+
                                 |
                                 |
                                 v
                    +------------------------------+
                    | optional cache for hot reads |
                    +------------------------------+

Read flow
  Mobile App -> API -> Activity Service -> Activities DB or Friends DB

Write flow for normal offline-first design
  Mobile App stores GPS locally during run
  Mobile App uploads completed activity in one sync
  Activity Service writes metadata and route data to DB

Optional realtime sharing extension

          athlete app sends periodic updates every few seconds
                                  |
                                  v
                           +----------------------+
                           |   Activity Service   |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           |   Activities DB      |
                           +----------+-----------+
                                      |
                     friends poll for latest activity updates
                                      |
                                      v
                           +----------------------+
                           |     Friends Apps     |
                           +----------------------+
```

The main idea is that the client does most of the live tracking work locally. That is the key simplification here. The backend mainly handles activity creation, final sync, and reads for completed runs. If you want, I can also give you a cleaner interview-style version that fits in 30 seconds on a whiteboard.


---

<details open>
<summary><strong>Problem</strong></summary>

Activity tracking, segment performance, and social fitness. Users upload GPS-tracked workouts, get stats, compete on segment leaderboards.

Hard parts: efficient GPS time-series storage, segment matching, and fast leaderboard reads.

</details>


<details>
<summary><strong>Failures</strong></summary>

**GPS track upload fails mid-upload (poor cellular connection on a run)**

Partial activity data. User loses their run.

_Fix:_ Client buffers GPS track locally. Chunked upload with resume support (TUS protocol). Client marks upload complete only after server confirms all chunks received and processing started.

**Segment matching job times out for a very long ride (200km, 50K GPS points)**

Leaderboard not updated. User sees no segment efforts after a long ride.

_Fix:_ Async processing with dedicated job queue. Timeout per segment match attempt, not per activity. Break large activities into bounding box segments, parallelize matching. Retry partial failures.

**Redis segment leaderboard grows unbounded**

ZADD/ZREVRANGE on a segment with 10M athletes is slow and memory-heavy.

_Fix:_ Leaderboard is per-segment sorted set. Store only top 10K entries per segment (ZREMRANGEBYRANK after every ZADD to trim). Historical ranking available from PG for display purposes.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10M athletes, avg 5 activities/week, avg 1 hour at 1 GPS point/sec = 3,600 points/activity |
| Read QPS | 10M × 5 / 7 / 86400 ≈ 83 activity uploads/s but: 83 × 3,600 GPS points = 300K GPS point writes/s to TimescaleDB |
| Write QPS | 300K GPS points/s is the dominant write load — TimescaleDB's append-only model handles this well |
| Storage | 10M users × 5 activities/week × 52 weeks × 3,600 points × 24 bytes (lat,lon,time,elevation) ≈ 45 TB GPS data/year |
| Cache math | 10K active segments × top-10K entries × 8 bytes = 800 MB leaderboard data in Redis — manageable |
| Verdict | GPS write volume (300K/s) is larger than it looks. TimescaleDB's time-partitioned append model is essential. Random-access DB would struggle. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**TimescaleDB vs. Cassandra vs. InfluxDB for GPS time-series**

→ TimescaleDB (PostgreSQL extension)

GPS data is time-series but also needs joins with activity metadata, segment boundaries, and athlete data. TimescaleDB gives time-series performance with full SQL. Cassandra would require denormalization of all joins.

_Revisit when:_ InfluxDB if write volume exceeds TimescaleDB limits (~500K points/s per node).

**Sync vs. async segment matching**

→ Async via Kafka processing job

Segment matching is CPU-intensive (Hausdorff distance over 50K points against thousands of segments). Doing it synchronously would make activity uploads take minutes. User gets immediate upload confirmation, segment efforts appear within minutes.

_Revisit when:_ Real-time matching possible for popular segments only (cache segment geometry, match inline).

**Polyline encoding client-side vs. server-side**

→ Client-side encoding before upload

Reduces upload payload by 75%. Client CPU cost is negligible (mobile has fast FPUs). Encoded polyline is still decodable server-side for processing.

_Revisit when:_ Server-side encoding if client implementation is buggy across platforms (just send raw and compress server-side).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle GPS noise (jumpy coordinates during tunnel)?**

Kalman filter or simple smoothing on the GPS stream client-side before upload. Server-side validation: reject points with speed > 200km/h (teleporting GPS artifact). Flag activities with high noise ratio for manual review.

**How would you add live activity tracking (share live location with friends)?**

Separate real-time pipeline: client streams GPS every 5s to a WebSocket server. Friends who are watching subscribe via SSE. Store live track in Redis (not TimescaleDB — too much write pressure). Persist to TimescaleDB async. This is the Strava Beacon feature.

**How do you prevent segment leaderboard manipulation?**

Statistical outlier detection: flag segment efforts with impossible speed (segment KOM for a 20km segment in 5 minutes = 240 km/h on a bike — suspicious). Cross-reference with heart rate and power data if available. Manual review queue for suspected anomalies.

**How do you handle athletes deleting activities?**

Soft delete the activity. Asynchronously remove all segment efforts for that activity from Redis leaderboards (ZREM). Remove GPS points from TimescaleDB (expensive — runs as background job). Leaderboard is eventually consistent after deletion.

**What's your data retention policy for GPS tracks?**

Hot storage (TimescaleDB): full resolution GPS for 2 years. Cold storage (S3): compressed polyline for lifetime. Old GPS data archived to S3 Glacier after 2 years. User-facing: always show full track (decompress on demand from S3 for old activities).

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG for all activity data. GPS as JSON blob in one column. Basic stats computed on upload. No segment matching. Works for 100K athletes.

**v2 — Time-series + segments** — TimescaleDB for GPS points. Async Kafka segment matching. Redis leaderboards. Polyline encoding. Live activity sharing. Handles 10M athletes.

**v3 — Social + ML** — Fitness trend analysis. Training load ML model. Route recommendations. Group challenges. Beacon (live tracking). Global athlete clustering for social features.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Strava is not raw request volume. It is that the product mixes offline tracking, large route data, and social reads in one system.

There are three pain points you should call out. First, each activity generates a long stream of GPS points, so storage grows fast and older route data can get expensive. Second, the app must work with weak or no connectivity, which means the client has to buffer data locally and sync later without losing too much progress. Third, if you add live sharing, the system becomes much harder because now you are handling frequent location updates plus many friends reading those updates at the same time.

A good interview summary is this. Strava is hard because the client does a lot of the work, route data is much heavier than normal app metadata, and real time sharing can turn a simple upload system into a continuous update system.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: upload GPS activity, compute stats, match segments, leaderboards, social feed. Out of scope unless asked: live tracking, route planning, training analytics, coaching.
- **Upload is async — always** — Client sends polyline-encoded GPS track to S3. Upload API acknowledges immediately. Kafka job triggers async processing. Stats and segment matches appear minutes later — that is the expected UX.
- **TimescaleDB for GPS time-series** — 300K GPS points/sec appended across all users. TimescaleDB: time-partitioned, columnar compression (5× smaller), SQL joins with activity metadata. Never use plain PostgreSQL for this volume.
- **Segment matching — Hausdorff distance** — Spatial index on segment bounding boxes (R-tree or geohash). For each activity: query segments whose bounding box overlaps. Then run Hausdorff distance check on candidates only. Reduces 1M segments to ~200 candidates per activity.
- **Leaderboard — Redis sorted set with trimming** — ZADD leaderboard:{segment_id} elapsed_seconds user_id. ZREMRANGEBYRANK after each ZADD to cap at top 10K entries. ZREVRANGE for display. Historical beyond top 10K fetched from PostgreSQL.
- **GPS noise handling** — Douglas-Peucker simplification or Kalman filter client-side before upload. Server-side: reject points implying speed > 200 km/h. Noisy tracks create false segment efforts — flag for review.
- **Failure mode to name** — Segment matching job fails mid-activity: idempotent Kafka consumer replays. Track transcode status per (activity_id, segment_id). Retry only failed segments, not the whole activity.

> Mental model: async pipeline. Upload to S3, process via Kafka worker, write to TimescaleDB + Redis leaderboards.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Sync vs async processing** — Async decouples upload from computation — upload confirmation is instant. Tradeoff: leaderboard and stats appear minutes later, not immediately on save.

**Redis sorted set vs DB query for leaderboard** — Redis ZREVRANGE is O(log N + K) in memory — sub-millisecond. A DB query scanning all efforts for a segment at 83 uploads/sec would be too slow.

**TimescaleDB vs Cassandra for GPS time-series** — TimescaleDB gives full SQL joins with activity metadata — needed for segment matching queries. Cassandra requires denormalizing all joins upfront. TimescaleDB is the right call when structured queries matter.

**Bloom filter vs exact set for segment dedup** — A user accumulates ~73K swipes/2yr. Bloom filter at 1% false positive is O(1), 730KB RAM — fast and cheap for deck generation. Exact Cassandra check is only needed for match creation where a false negative is unacceptable.

> "Upload decoupled from processing via S3 + Kafka. Polyline encoding cuts storage. Redis sorted sets for leaderboards."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: GPS time-series storage — TimescaleDB design for append-only high-volume data
> [!CAUTION]
> **🔴 Weak** — store GPS points as rows in a PostgreSQL table indexed by activity_id
>
> [!WARNING]
> **🟡 Strong** — the core constraint is 300K GPS point writes/second (10M users × 5 activities/week × 3,600 points/activity) combined with complex time-range queries for activity display and segment analysis. Weak answer: PostgreSQL with a GPS_points table. Strong answer: TimescaleDB with a hypertable partitioned by (activity_id, time). Time-partitioned storage means: (1) recent data is in memory or SSD, old data on cheaper storage; (2) time-range queries touch only the relevant partitions; (3) TTL/retention policies drop old partitions cheaply (DROP is instant, DELETE is O(N))
>
> [!TIP]
> **🟢 Staff+** — design: GPS_points table has (activity_id, timestamp, lat, lng, elevation, heart_rate, power) as a time-series table. The partition key must be on timestamp for range query efficiency. Compression: TimescaleDB columnar compression reduces GPS data to 20% of original size with no query changes. Rollup: pre-aggregate GPS points to 10-second intervals for activities older than 30 days — reduces storage by 10× at the cost of reduced resolution for old activities (acceptable — users rarely scroll old activity GPS at full resolution)


#### Deep dive 2: Segment matching — Hausdorff distance at scale
> [!CAUTION]
> **🔴 Weak** — iterate through all 1M segments and run Hausdorff distance on each for every uploaded activity
>
> [!WARNING]
> **🟡 Strong** — every activity must be matched against all segments whose bounding box overlaps the activity's bounding box. At 83 activity uploads/second and 1M segments, naive O(activities × segments) is impossible
>
> [!TIP]
> **🟢 Staff+** — approach: spatial index on segment bounding boxes (R-tree or geohash grid). For each activity: (1) compute activity bounding box, (2) query spatial index for segments whose bounding box overlaps, (3) run Hausdorff distance check on the reduced candidate set. The spatial index reduces candidates from 1M to ~100-500 per activity. Hausdorff distance: the maximum of the minimum distances between two polylines. Two polylines match if Hausdorff distance < threshold (e.g., 25 meters). GPS noise handling: smooth the activity GPS track with a Kalman filter or Douglas-Peucker simplification before matching — reduces false negatives from noisy data. Parallel matching: each activity's segment matches can be computed independently — embarrassingly parallel across worker nodes, partitioned by activity bounding box geohash


#### Deep dive 3: Segment leaderboards — Redis sorted sets with trimming
_Each segment has a leaderboard: fastest time by athletes who have completed that segment. The access pattern: frequent reads (users checking their ranking), moderate writes (new segment efforts after activity processing)_

> [!CAUTION]
> **🔴 Weak** — Oversimplify segment leaderboards — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Each segment has a leaderboard: fastest time by athletes who have completed that segment. The access pattern: frequent reads (users checking their ranking), moderate writes (new segment efforts after activity processing)
>
> [!TIP]
> **🟢 Staff+** — design: Redis sorted set per segment_id (key: leaderboard:{segment_id}, score: elapsed_seconds, member: user_id). ZADD adds a new effort; if the user has a previous effort, ZADD with NX flag only adds new members, so first add only — use regular ZADD but also track the user's best time separately to avoid storing worse efforts. On ZADD, also check if user's new time is better than their stored best (ZSCORE lookup, O(log N)), and only update if improved. ZREVRANGE for top-N is O(log N + K). Leaderboard trimming: ZREMRANGEBYRANK removes bottom entries, keeping only top 10K per segment (50K × 10K × 8 bytes = 4 GB total Redis for all segments). Historical rankings beyond top 10K are fetched from PostgreSQL


_Why the deep dives connect to the scaling problem: "Offline tracking, large route data, and social reads." Deep dive 1 solves time-series storage. Deep dive 2 solves segment matching at scale. Deep dive 3 solves leaderboard performance._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Async-pipeline script.

2. "Clarifying questions: are we designing the full platform — upload, segment matching, social feed, live tracking — or just the core activity pipeline?"

3. "Good — upload plus segment matching plus leaderboards. Core features: upload GPS activity, compute stats, match segments, update leaderboards. Out of scope: live tracking, coaching, route planning."

4. "Upload is always async: client sends polyline-encoded GPS track to S3. API acknowledges immediately. Kafka event triggers async processing. Users expect stats and segment matches to appear within minutes — not blocking the upload."

5. "GPS storage: TimescaleDB. 300K GPS points/sec is an append-only time-series workload. TimescaleDB: time-partitioned, columnar compression (5× smaller), full SQL for joins with activity metadata. Plain PostgreSQL cannot handle this write volume."

6. "Segment matching: spatial index (R-tree or geohash) on segment bounding boxes. For each activity, query segments whose bounding box overlaps. Then run Hausdorff distance on the ~200 candidate segments only. Without the spatial index, checking all 1M segments per activity is impossible."

7. "Leaderboards: Redis sorted set per segment_id. ZADD new effort, ZREMRANGEBYRANK to cap at top 10K. ZREVRANGE for display. Personal ranking uses ZRANK. Historical beyond top 10K is fetched from PostgreSQL on demand."

8. "Key tradeoff: async processing means the leaderboard is not updated instantly on upload. A user who sets a segment PR sees it reflected a few minutes later. This is the acceptable tradeoff for decoupling upload latency from segment matching computation."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                           |      Mobile App      |
                           | start pause stop     |
                           | local GPS tracking   |
                           | local stats display  |
                           | offline buffer       |
                           +----------+-----------+
                                      |
                         create sync fetch activities
                                      |
                                      v
                           +----------------------+
                           |    API or LB Layer    |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           |   Activity Service   |
                           | activity lifecycle   |
                           | ingest route uploads |
                           | fetch activity feed  |
                           +-----+-----------+----+
                                 |           |
                                 |           |
                                 v           v
                    +------------------+   +------------------+
                    |   Activities DB   |   |    Friends DB    |
                    | activity metadata |   | user friendships |
                    | route points      |   +------------------+
                    | state log         |
                    +------------------+
                                 |
                                 |
                                 v
                    +------------------------------+
                    | optional cache for hot reads |
                    +------------------------------+

Read flow
  Mobile App -> API -> Activity Service -> Activities DB or Friends DB

Write flow for normal offline-first design
  Mobile App stores GPS locally during run
  Mobile App uploads completed activity in one sync
  Activity Service writes metadata and route data to DB

Optional realtime sharing extension

          athlete app sends periodic updates every few seconds
                                  |
                                  v
                           +----------------------+
                           |   Activity Service   |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           |   Activities DB      |
                           +----------+-----------+
                                      |
                     friends poll for latest activity updates
                                      |
                                      v
                           +----------------------+
                           |     Friends Apps     |
                           +----------------------+
```

The main idea is that the client does most of the live tracking work locally. That is the key simplification here. The backend mainly handles activity creation, final sync, and reads for completed runs. If you want, I can also give you a cleaner interview-style version that fits in 30 seconds on a whiteboard.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-13)
