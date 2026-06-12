# YouTube Top K videos

**Hard** · Stream processing · Count-Min Sketch · Lambda architecture

Tags: `Kafka`, `Flink`, `Redis sorted set`, `ClickHouse`, `Count-Min Sketch`, `Watermarking`

_See also: YouTube video platform · upload/streaming path_

## Data flow

View events → Kafka → Flink aggregates per videoId using Count-Min Sketch for approximate frequency counting. Updates Redis sorted set via ZINCRBY. Top-K = ZREVRANGE. Lambda Architecture: daily Spark/ClickHouse batch reconciles exact counts.


> Count-Min Sketch = approx freq O(1) space  |  Watermark 1min for late events  |  Lambda: real-time ≈ accurate + batch = exact

## Architecture diagram

```
+----------------------+
                        |   YouTube Clients    |
                        +----------+-----------+
                                   |
                                   | watch events
                                   v
                        +----------------------+
                        |  Video Serving System |
                        +----------+-----------+
                                   |
                                   | publish ViewEvent(videoId, ts)
                                   v
                         +---------------------+
                         |   Kafka ViewEvent   |
                         | topic partitioned   |
                         | by videoId          |
                         +----+----+----+-----+
                              |    |    |
                consume       |    |    |       consume
                              v    v    v
                    +----------------------------------+
                    |     Flink / Stream Aggregator    |
                    |  watermark for late events       |
                    |  minute or hour tumbling windows |
                    |  count views per video           |
                    +----------------+-----------------+
                                     |
                        batched aggregates per shard
                                     |
            +------------------------+------------------------+
            |                        |                        |
            v                        v                        v
     +--------------+         +--------------+         +--------------+
     | Views DB S1  |         | Views DB S2  |   ...   | Views DB SN  |
     | shard by     |         | shard by     |         | shard by     |
     | videoId      |         | videoId      |         | videoId      |
     +------+-------+         +------+-------+         +------+-------+
            |                        |                        |
            | keep window tables     | keep window tables     |
            |                        |                        |
            |   - all_time           |   - last_hour          |
            |   - last_day           |   - last_month         |
            v                        v                        v
     +---------------------------------------------------------------+
     | indexed aggregate tables per shard                            |
     | query top K locally on each shard                             |
     +---------------------------+-----------------------------------+
                                 |
                                 | periodic fanout query
                                 v
                      +-------------------------------+
                      | Top K Precompute Job / Cron   |
                      | query each shard for local K  |
                      | merge into global top K       |
                      +---------------+---------------+
                                      |
                                      | write precomputed results
                                      v
                             +--------------------+
                             | Redis Cache        |
                             | top-k:last_hour    |
                             | top-k:last_day     |
                             | top-k:last_month   |
                             | top-k:all_time     |
                             +---------+----------+
                                       |
                                       v
                          +----------------------------+
                          | Top K API Service          |
                          | GET /views/top-k?window&k  |
                          +-------------+--------------+
                                        |
                                        v
                              +------------------+
                              | Load Balancer    |
                              +--------+---------+
                                       |
                                       v
                              +------------------+
                              |     Clients      |
                              +------------------+
```

If you are presenting this in an interview, the clean story is this. Kafka absorbs the firehose of view events. Flink batches and aggregates views by video for a time bucket. Sharded databases store pre-aggregated counts for each window. A precompute job pulls local top K from each shard, merges them, and writes the final answers into Redis. The API just reads from Redis, which is how you hit the tens of milliseconds latency target.

If you want, I can also give you a simpler interview version with only 6 boxes so it is easier to draw under time pressure.


---

<details open>
<summary><strong>Problem</strong></summary>

Computing and serving the most-viewed videos in real-time at YouTube scale.

The challenge: exact counting at this scale is prohibitively expensive. The system must balance real-time approximation vs exact billing accuracy.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Flink job falls behind on a viral video event (billion views in 1 hour)**

Top K list is stale. The viral video doesn't appear in trending for minutes.

_Fix:_ Flink auto-scales by input lag. Kafka partition count pre-sized for peak throughput. For extreme events: dedicated high-priority Kafka topic for top-1000 videos by view velocity.

**Redis sorted set for Top K becomes a hot key**

All writes (ZINCRBY) and all reads (ZREVRANGE) go to one Redis shard holding the top-K set. It becomes the bottleneck.

_Fix:_ Shard top-K by time window: one set per time window (top-K-hourly-2024-01-01-14, etc.). Flink writes to current window set. Query merges across windows. Local in-process cache for read-heavy consumers.

**Count-Min Sketch error rate too high for a specific video**

A mid-tier video's count is significantly over-estimated due to hash collisions. Appears in trending incorrectly.

_Fix:_ Increase sketch width (more hash functions) to reduce collision probability. For top-100 known videos, use exact counting in parallel (small set, cheap). Use sketch for the long tail only.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 2B users, 1B views/day, 800M videos, top-K query at global/country/category level |
| Read QPS | 1B views / 86400 ≈ 11,574 view events/s — into Kafka |
| Write QPS | Flink processes 11,574 events/s, emits ~1,000 ZINCRBY updates/s (only changed videos) |
| Storage | Count-Min Sketch: width=2000, depth=7 → 14,000 counters × 8 bytes = 112 KB per sketch. Tiny. Redis sorted set for Top K: K=1,000 × 8 bytes = 8 KB. Both trivially small. |
| Cache math | Top-K query: served from Redis sorted set, O(log N + K), sub-ms. 11,574 events/s fanout → Flink aggregation reduces to ~1,000 Redis writes/s. |
| Verdict | Event ingestion volume (11K/s) is manageable for Kafka. The key design insight is aggregation in Flink before writing to Redis — not writing per-view. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Lambda architecture (batch + stream) vs. stream only**

→ Lambda: Flink (speed layer) + Spark batch (batch layer)

Stream only is approximate. For ad revenue, creator monetization, and copyright detection, exact counts are legally required. Batch path reconciles. Staff-level answer: explicitly say 'Lambda Architecture' and explain why both paths are needed.

_Revisit when:_ Kappa architecture (stream only, replay for corrections) is an alternative if batch reconciliation latency is acceptable.

**Count-Min Sketch vs. exact counting**

→ Count-Min Sketch for long tail, exact counting for Top 1000

Exact counting for 800M videos would require 6.4 GB of counters in memory. CMS handles this in 112 KB with 1-5% error. Top 1000 exact counting is cheap (8 KB) and eliminates error for the videos that actually matter for trending.

_Revisit when:_ HyperLogLog for distinct viewer count (vs. total view count). Different data structure, different problem.

**Tumbling vs. sliding window for trending**

→ Tumbling windows (1hr, 24hr, 7day) for simplicity

Sliding windows give smoother trending signal but require keeping state for the full window duration. Tumbling windows are simpler: clear window at boundary, start fresh. Perceived smoothness difference is minimal for human-readable trending lists.

_Revisit when:_ Sliding windows with Flink's native windowing for smoother real-time trending signal.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you prevent view count manipulation (bots)?**

Filter bot views at ingestion: IP rate limiting, user-agent validation, session token validation. Don't count views < 30s watch time. ML bot detection model. Views that pass validation go to Kafka. Views that fail: logged but not counted. Periodic batch audit of view patterns.

**How would you add a 'trending in your country' feature?**

Add country code to the Kafka event. Flink maintains per-country CMS and sorted set. Redis key: top-k:{country_code}:{window}. Adds linear memory cost per country tracked.

**How do you reconcile the batch and stream layers?**

Batch job (Spark on Hadoop/GCS) runs daily: exact count per video_id from raw event log. Writes exact counts to the serving layer (ClickHouse or BigQuery). Stream layer's approximate counts are used for real-time display. When batch completes, canonical count overwrites approximate count.

**What's the latency from view event to appearing in trending?**

P50: <2 minutes (Kafka ingestion + Flink window emit + Redis write + cache invalidation). This is the Flink tumbling window size. For breaking events: reduce window to 5 minutes at the cost of more Redis writes.

**How would you build this for a specific category (e.g., Top K Gaming videos)?**

Add category_id to Kafka event (requires video metadata join in Flink, or pre-tag at event time). Maintain per-category CMS + sorted set. Flink handles this with keyed streams: key by (category_id, window) instead of just (window).

**How do you handle duplicate view events from retries?**

Client-generated view_id UUID. Bloom filter or Redis SET at ingestion dedupes within 24h window. Exact dedup table for top creators where billing disputes matter.

**What happens when Flink falls behind during a viral video?**

Monitor consumer lag. Autoscale Flink task managers. Pre-provision extra Kafka partitions for peak events. Trending list may lag 2–5 min — show 'updating' indicator in UI.

**How do you store raw events vs aggregates cost-effectively?**

Hot raw events 7–30 days in Kafka/S3. Hourly/daily aggregates in ClickHouse forever. Query API routes by window size — never scan raw log for dashboard Top K.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Batch only** — Daily Spark job counts views from raw log. Top K served from ClickHouse. 24hr latency. Good enough for 'most viewed this week' but not 'trending now'.

**v2 — Stream layer (Lambda)** — Flink stream processing with CMS. Redis sorted set for real-time Top K. Batch layer remains for exact daily counts. Trending now with <2min latency.

**v3 — Global + personalized** — Per-country, per-category Top K. Personalized trending (blends global signal with user history). Bot detection in ingestion pipeline. Live view count display on video pages.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in YouTube Top K is that it looks like a simple ranking problem, but at scale it becomes a streaming aggregation problem. You are not just storing view counts. You are ingesting a huge firehose of views, updating counts fast enough, and still answering top K queries for different time windows with very low latency.

There are three main scaling pain points. First, write volume is massive. Every view is an event, so naive per view database updates fall over quickly. Second, windowed queries are expensive. Top K for the last hour, day, and month means you cannot just sort one static table. You need rolling aggregates over huge amounts of data. Third, precision plus low latency is a tough combo. If the result must be exact and returned in milliseconds, you usually need precomputation and caching, not on demand scans.

A fourth issue is cardinality. There are billions of videos, but only a tiny fraction belong in the top K. That means you need to process a very large universe of IDs just to find a very small answer set. So the interview summary is this. YouTube Top K is hard because it combines massive write throughput, expensive time window aggregation, and a need to precompute exact rankings fast enough to serve cheaply.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Lambda Architecture** — Real-time path for fast approximate results. Batch path for exact daily reconciliation. Name both paths explicitly.
- **Count-Min Sketch** — Approximate frequency in O(1) space with 1–5% error. Massive space savings vs exact counting.
- **Flink tumbling windows** — Aggregate view counts per videoId in fixed time windows.
- **Watermarking** — Flink watermark with 1-minute allowed lateness. Handles late events.
- **Redis sorted set for Top-K** — ZINCRBY to update scores, ZREVRANGE to read Top-K.

> Staff expectation: name Lambda Architecture explicitly with both paths.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Exact counting vs Count-Min Sketch** — Exact counting for 800M videos = 6.4 GB of counters updated at 11K/sec. Count-Min Sketch handles this in 152 KB with 1–5% error — acceptable for a trending list, not for billing.

**Real-time only (Kappa) vs Lambda Architecture** — Real-time alone is approximate — insufficient for creator revenue and copyright. Lambda adds exact batch reconciliation at the cost of two pipelines. For ad revenue, Lambda is non-negotiable.

**Tumbling vs sliding windows for trending** — Tumbling windows (1hr, 24hr) are simpler — state resets at boundary. Sliding windows give smoother trending signal but require keeping state for the full window duration. Tumbling is the right default.

**Kafka partition by video_id vs random** — Partition by video_id ensures all events for one video go to one Flink operator — correct aggregation without cross-partition coordination. Random partitioning requires a shuffle step to aggregate per video.

> "Count-Min Sketch trades a small amount of accuracy for massive reduction in space and compute. Approximate is good enough for trending. The batch path gives exact counts when needed."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Count-Min Sketch — approximate frequency counting at stream scale
> [!CAUTION]
> **🔴 Weak** — maintain a HashMap of video_id → count, increment on every view event. At 800M videos × 8 bytes = 6.4 GB of counters updated at 11K/sec — too expensive, and one hot video creates a write bottleneck
>
> [!WARNING]
> **🟡 Strong** — Count-Min Sketch — a 2D array of W×D counters. On each event: hash video_id with D hash functions, increment the counter at each position. To estimate count: take the minimum of the D values
>
> [!TIP]
> **🟢 Staff+** — at 1% error with 99.9% confidence, W ≈ 2718, D ≈ 7. Total memory: 152 KB per time window vs 6.4 GB for exact counting. For Top K specifically: combine CMS with a Min-Heap of K items — only track candidates whose estimated count exceeds the current K-th largest. The heap has K entries; the sketch stays constant size regardless of cardinality


#### Deep dive 2: Lambda Architecture — why both stream and batch paths are needed
> [!CAUTION]
> **🔴 Weak** — use Flink stream processing only — it handles the volume
>
> [!WARNING]
> **🟡 Strong** — stream-only (Kappa architecture) gives approximate results. For ad revenue, creator monetization, and copyright detection, approximate counts are legally insufficient. Lambda Architecture: real-time path gives approximate counts for the trending dashboard (fast, approximate), batch path gives exact counts for billing and reporting (slow, exact)
>
> [!TIP]
> **🟢 Staff+** — implementation: real-time path (Flink + CMS + Redis sorted set) runs continuously. Batch path (Spark on S3 event lake) runs daily, produces audited exact counts. Serving layer stores both: consumers use exact counts when available, approximate otherwise. Never use approximate counts for financial reporting


#### Deep dive 3: Time window management — tumbling vs. sliding, late event handling
> [!CAUTION]
> **🔴 Weak** — use a single global counter per video — no time window awareness
>
> [!WARNING]
> **🟡 Strong** — separate state per window (1hr, 24hr, 7day) using Flink tumbling windows. Each window starts fresh at its boundary
>
> [!TIP]
> **🟢 Staff+** — tradeoff: tumbling windows are simpler but the trending list jumps at boundaries — a video popular for the last 59 minutes drops off suddenly at the 1-hour mark. Flink sliding windows give smoother signal but require keeping state for the full window duration. Recommendation: tumbling for longer periods (24h, 7d) where boundary jumps are acceptable, sliding for the 1-hour list where users expect smooth changes. Late event handling: Flink watermark with 2-minute allowed lateness. Events arriving later go to the batch path for exact reconciliation


_Why the deep dives connect to the scaling problem: "Massive write throughput, windowed aggregation, and precision vs. latency." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Lambda Architecture framing.

2. "I'd design this as a Lambda Architecture: a real-time streaming path for fast approximate Top-K, and a batch path for exact reconciliation."

3. "Real-time path: view events → Kafka partitioned by video_id → Flink streaming job → Count-Min Sketch for approximate frequencies → update Redis sorted set via ZINCRBY → Top-K = ZREVRANGE."

4. "Count-Min Sketch gives approximate frequency counts with 1–5% error using a fraction of the memory of exact counting. For a trending video list, that's good enough."

5. "Batch path: raw events land in ClickHouse. A daily Spark job computes exact view counts and reconciles against the real-time Redis counts. Exact numbers for reporting and billing."

6. "I'd name this Lambda Architecture explicitly — real-time for speed, batch for correctness. Both are needed."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                        |   YouTube Clients    |
                        +----------+-----------+
                                   |
                                   | watch events
                                   v
                        +----------------------+
                        |  Video Serving System |
                        +----------+-----------+
                                   |
                                   | publish ViewEvent(videoId, ts)
                                   v
                         +---------------------+
                         |   Kafka ViewEvent   |
                         | topic partitioned   |
                         | by videoId          |
                         +----+----+----+-----+
                              |    |    |
                consume       |    |    |       consume
                              v    v    v
                    +----------------------------------+
                    |     Flink / Stream Aggregator    |
                    |  watermark for late events       |
                    |  minute or hour tumbling windows |
                    |  count views per video           |
                    +----------------+-----------------+
                                     |
                        batched aggregates per shard
                                     |
            +------------------------+------------------------+
            |                        |                        |
            v                        v                        v
     +--------------+         +--------------+         +--------------+
     | Views DB S1  |         | Views DB S2  |   ...   | Views DB SN  |
     | shard by     |         | shard by     |         | shard by     |
     | videoId      |         | videoId      |         | videoId      |
     +------+-------+         +------+-------+         +------+-------+
            |                        |                        |
            | keep window tables     | keep window tables     |
            |                        |                        |
            |   - all_time           |   - last_hour          |
            |   - last_day           |   - last_month         |
            v                        v                        v
     +---------------------------------------------------------------+
     | indexed aggregate tables per shard                            |
     | query top K locally on each shard                             |
     +---------------------------+-----------------------------------+
                                 |
                                 | periodic fanout query
                                 v
                      +-------------------------------+
                      | Top K Precompute Job / Cron   |
                      | query each shard for local K  |
                      | merge into global top K       |
                      +---------------+---------------+
                                      |
                                      | write precomputed results
                                      v
                             +--------------------+
                             | Redis Cache        |
                             | top-k:last_hour    |
                             | top-k:last_day     |
                             | top-k:last_month   |
                             | top-k:all_time     |
                             +---------+----------+
                                       |
                                       v
                          +----------------------------+
                          | Top K API Service          |
                          | GET /views/top-k?window&k  |
                          +-------------+--------------+
                                        |
                                        v
                              +------------------+
                              | Load Balancer    |
                              +--------+---------+
                                       |
                                       v
                              +------------------+
                              |     Clients      |
                              +------------------+
```

If you are presenting this in an interview, the clean story is this. Kafka absorbs the firehose of view events. Flink batches and aggregates views by video for a time bucket. Sharded databases store pre-aggregated counts for each window. A precompute job pulls local top K from each shard, merges them, and writes the final answers into Redis. The API just reads from Redis, which is how you hit the tens of milliseconds latency target.

If you want, I can also give you a simpler interview version with only 6 boxes so it is easier to draw under time pressure.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-17)
