# Ad click aggregator

**Hard** · Lambda architecture · Stream + batch · Watermarking

Tags: `Kafka`, `Flink`, `ClickHouse`, `Lambda Architecture`, `Watermarking`, `Click dedup (Bloom)`

## Data flow

Click events → Kafka. Real-time path: Flink tumbling windows aggregate per ad_id, with a 1-minute watermark. Results land in ClickHouse. Batch path: Spark computes exact daily counts and reconciles. Click dedup: UUID + Bloom filter at ingestion.


> Lambda: real-time ≈ accurate + batch = exact  |  Name both paths explicitly  |  Click dedup: UUID + Bloom filter

## Architecture diagram

```
+----------------------+
                           |  Ad Placement Svc    |
                           |  returns ad, target  |
                           |  impressionId, sig   |
                           +----------+-----------+
                                      |
                                      v
+---------+        click ad        +--------------------+
|  User   | ---------------------> |   Click Endpoint   |
| Browser |                        |   /click           |
+----+----+                        +-----+----------+---+
     ^                                   |          |
     | 302 redirect to advertiser        |          |
     |                                   |          |
     |                                   |          v
     |                                   |   +-------------+
     |                                   |   | Signature   |
     |                                   |   | Verification|
     |                                   |   +------+------+ 
     |                                   |          |
     |                                   |          v
     |                                   |   +-------------+
     |                                   |   | Redis Cache |
     |                                   |   | dedup by    |
     |                                   |   | impressionId|
     |                                   |   +-------+-----+ 
     |                                   |           |
     |                                   | duplicate?| 
     |                                   |    yes -> drop
     |                                   |           |
     |                                   v          no
     |                             +-----------------------+
     |                             | Kafka or Kinesis      |
     |                             | durable click stream  |
     |                             +-----+------------+----+
     |                                   |            |
     |                                   |            +------------------+
     |                                   |                               |
     |                                   v                               v
     |                         +-------------------+          +-------------------+
     |                         | Flink stream proc |          | S3 data lake      |
     |                         | window by minute  |          | raw click archive |
     |                         | aggregate clicks  |          +---------+---------+
     |                         +---------+---------+                    |
     |                                   |                              |
     |                                   v                              v
     |                         +-------------------+          +-------------------+
     |                         | OLAP analytics DB | <--------| Batch reconcile   |
     |                         | ClickHouse,       |          | Spark daily or    |
     |                         | BigQuery, etc     |          | hourly recompute  |
     |                         +---------+---------+          +-------------------+
     |                                   |
     |                                   v
     |                         +-------------------+
     +-------------------------| Advertiser Query  |
                               | dashboard or API  |
                               +-------------------+
```

The mental model is two paths. The serving path handles the user click and redirect fast, and the analytics path turns raw clicks into queryable per minute metrics.

If you were drawing this in an interview, I would keep the main story to five boxes first. User, Click Endpoint, Stream, Stream Processor, OLAP DB. Then add Redis for dedup and S3 plus batch reconcile only if the interviewer asks about idempotency or correctness.


---

<details open>
<summary><strong>Problem</strong></summary>

Counting ad clicks accurately at massive scale for billing advertisers.

The core challenge: exact counting at this scale is expensive. The system must balance real-time approximation vs exact billing accuracy.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Flink job crashes mid-window**

Partial window aggregates lost. When job restarts, window starts fresh. Some clicks are counted twice (from Kafka replay) or not at all (lost in-memory state).

_Fix:_ Flink checkpointing to S3/HDFS every 30 seconds. On restart, restore from checkpoint. Kafka retention covers the replay window. Exactly-once semantics via Flink's transaction sink (idempotent writes to ClickHouse).

**Hot ad partition: one viral ad gets 100× normal click volume**

Single Kafka partition handling all clicks for the hot ad is overloaded. Flink consumer for that partition falls behind. Click data delayed.

_Fix:_ Repartition hot keys: detect high-volume ad_ids, assign multiple Kafka partitions to them (key-based routing with partition count per ad_id). Flink state is partitioned by ad_id anyway — just needs more partitions to distribute load.

**Duplicate clicks from user double-tapping or network retry**

Advertiser billed for clicks that never happened. Financial integrity issue.

_Fix:_ Click dedup: each click event has a UUID generated client-side. Bloom filter at ingestion service checks UUID. Duplicate UUID → drop event. Bloom filter false positive rate < 0.01%. For billing-grade accuracy: exact dedup in DB for top-spend advertisers.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10B ads served/day, 1% CTR = 100M clicks/day, 10M active ad campaigns |
| Read QPS | 100M clicks / 86400 ≈ 1,157 click events/s ingested |
| Write QPS | Flink aggregation: 1,157 events/s → ~100 ClickHouse INSERT/s (batch aggregated) |
| Storage | Raw events: 1,157/s × 200 bytes × 86400 × 30 days ≈ 600 GB/month raw event log |
| Cache math | Advertiser dashboard reads: 10M campaigns × 1 dashboard refresh/minute = 167K QPS. Must be served from ClickHouse + Redis cache (not recomputed from raw events). |
| Verdict | The 167K dashboard QPS vs 1,157 event QPS ratio shows reads are the harder scaling problem, not writes. ClickHouse pre-aggregation + Redis caching for the dashboard layer is essential. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Lambda (stream + batch) vs. Kappa (stream only with replay)**

→ Lambda Architecture

Advertising revenue requires exact billing. Stream layer: fast, approximate, good for real-time dashboards. Batch layer: exact, reconciled daily, used for invoices. Both are needed because advertisers dispute charges based on exact counts. Kappa (stream replay for corrections) has higher latency for corrections.

_Revisit when:_ Kappa if reconciliation latency of daily batch is acceptable for advertiser billing.

**ClickHouse vs. Cassandra for aggregated storage**

→ ClickHouse (columnar OLAP)

Dashboard queries: SELECT sum(clicks), sum(impressions) GROUP BY campaign_id, date WHERE campaign_id = 123 AND date BETWEEN ... — this is OLAP, not OLTP. ClickHouse columnar compression gives 10× better scan performance than Cassandra for these queries.

_Revisit when:_ Cassandra for time-series click storage (high write throughput). ClickHouse for aggregated analytics. Both can coexist.

**Click dedup: Bloom filter vs. exact set vs. Redis SET**

→ Bloom filter for fast path, exact DB check for billing-critical top advertisers

1,157 events/s × 1M clicks/event_window = 1B unique IDs to check. Bloom filter handles this in ~1 GB RAM with 0.01% false positive rate. For top 1K advertisers by spend: exact UUID check in Redis (smaller set, exact required for billing disputes).

_Revisit when:_ HyperLogLog for approximate distinct click count — different problem (count unique users, not dedup specific clicks).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle click fraud?**

Layered approach: (1) dedup within session (same user, same ad, < 1s = duplicate), (2) IP rate limiting (> 100 clicks/hour from one IP), (3) ML model (click pattern analysis — bot traffic has different timing/behavior distributions), (4) manual review queue for high-value campaigns.

**How do you handle attribution (click → conversion)?**

Attribution is a separate pipeline: join click events with conversion events (purchase, signup) by user_id within attribution window (7-30 days). Multi-touch attribution: different weight for first click, last click, or linear. This requires long-term event correlation, not just click aggregation.

**How do you serve real-time dashboard updates to 10M advertisers?**

ClickHouse pre-aggregated tables: hourly rollups materialized by (campaign_id, hour). Dashboard query hits materialized table, not raw events. Redis caches dashboard snapshots with 1-min TTL. Flink streams recent (last-hour) data to Redis directly for sub-minute freshness.

**What's your SLA for click data appearing in dashboards?**

Real-time dashboard: P99 < 2 minutes. Billing-grade exact counts: end of day + 2 hours (batch job window). Make this explicit: advertisers see approximate real-time data and exact daily billing data — two different SLAs, two different pipelines.

**How would you add impression counting to this system?**

Impressions are 10× more frequent than clicks (100B/day). Same Kafka → Flink → ClickHouse pipeline but with higher throughput. Key difference: impression events are larger volume, less fraud-sensitive (nobody pays for impressions in CPC model). Separate Kafka topics, separate Flink jobs, same ClickHouse database with separate tables.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Batch only** — Hourly MapReduce job over raw click logs. 1-hour staleness. Good enough for daily reporting but useless for real-time campaign optimization.

**v2 — Lambda Architecture** — Flink stream processing for real-time aggregates. Spark batch for daily exact counts. ClickHouse for serving. Bloom filter dedup. Handles 100M clicks/day.

**v3 — Scale + fraud** — ML click fraud detection. Attribution modeling. Budget pacing (stop serving when campaign budget exhausted, detected in near-real-time). 10B impressions/day.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Ad Click Aggregator is that it looks like a simple counter system, but it is really a high write analytics pipeline with correctness requirements.

There are three main scaling pain points. First, the write path is heavy. Every click is an event, so you cannot do raw database writes and then run GROUP BY queries on demand. You need to buffer and pre aggregate the data. Second, freshness matters. Advertisers want near real time metrics, so pure batch processing makes data too stale, which pushes you toward streaming aggregation. Third, correctness matters a lot because clicks map to money. You cannot lose events, and you also do not want to double count duplicate clicks.

A fourth issue to call out is skew. Most ads are quiet, but one viral ad can create a hot shard if all clicks for that ad land on the same partition. That means the system is hard not because 10k clicks per second is huge by itself, but because you need high write throughput, low latency analytics, and accurate counting all at once.

A good interview summary is this. Ad Click Aggregator is hard because it combines write heavy ingestion, near real time aggregation, idempotency, and hot key traffic in one pipeline.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Lambda Architecture** — Real-time path for fast approximate aggregates. Batch path for exact billing reconciliation. Name both paths explicitly.
- **Click dedup is critical** — Each click has a UUID. Bloom filter at ingestion for fast dedup. Prevents inflated advertiser bills.
- **Flink watermarking** — Allow up to 1 minute of lateness. Bounds output latency.
- **ClickHouse for OLAP** — Columnar store optimized for aggregation queries.
- **Batch for billing** — Billing must use exact counts. Daily Spark job reconciles.

> Staff expectation: name Lambda Architecture explicitly. Real-time = fast and approximate. Batch = exact for billing.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Stream only vs Lambda Architecture** — Stream only is approximate — not suitable for advertiser billing or revenue disputes. Lambda adds exact batch reconciliation at the cost of two pipelines. For ad revenue, Lambda is non-negotiable.

**Watermarking vs waiting indefinitely for late events** — Watermark allows a fixed lateness window (2 min). Beyond that, late events are routed to the batch path for exact reconciliation. Tradeoff: perfect accuracy vs bounded stream latency. The batch path catches everything the stream misses.

**ClickHouse vs Cassandra for aggregated storage** — ClickHouse is columnar OLAP — GROUP BY campaign_id with SUM(clicks) over billions of rows is hardware-accelerated. Cassandra is row-oriented OLTP — efficient for point lookups, slow for analytical aggregations. ClickHouse is the right choice for ad dashboards.

**Bloom filter dedup vs exact UUID check** — Bloom filter (0.01% false positive) handles 100M click UUIDs/day in 300 MB RAM — fast, cheap, good enough for real-time dashboards. For billing-critical top advertisers: exact UUID dedup via DB. Two-tier approach gives both speed and precision where it matters.

> "Lambda Architecture: real-time path for speed, batch path for accuracy. Name both paths — that's the staff-level signal."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Lambda Architecture — real-time stream + batch reconciliation
_The business requirement creates the architectural constraint: real-time approximate metrics for dashboards (advertisers want to see campaign performance now) AND exact counts for billing (advertisers dispute invoices with exact numbers). No single pipeline satisfies both_

> [!CAUTION]
> **🔴 Weak** — stream only (approximate)
>
> [!WARNING]
> **🟡 Strong** — Lambda Architecture explicitly. Speed layer (Flink): processes Kafka events in near-real-time, aggregates with 100ms batch windows, writes to ClickHouse. Results are fast but approximate (Flink checkpointing can reprocess, but windows have an allowed lateness boundary beyond which events are dropped). Batch layer (Spark): reads raw events from the event lake (S3-compatible, 90-day retention), runs daily exact aggregation. Batch results are exact, with 24-hour latency. Serving layer (ClickHouse): stores both approximate (updated every 30s by Flink) and exact (updated daily by Spark) counts. Advertisers see approximate for real-time view, exact for invoice
>
> [!TIP]
> **🟢 Staff+** — architectural point: the batch layer is not a fallback — it's a first-class part of the design that serves a different SLA requirement


#### Deep dive 2: Click deduplication — UUID + Bloom filter + exact reconciliation
_Click fraud via duplicate clicks directly translates to advertiser overbilling. Dedup must be accurate_

> [!CAUTION]
> **🔴 Weak** — check DB for duplicates
>
> [!WARNING]
> **🟡 Strong** — UUID per click event, Bloom filter at ingestion. The Bloom filter: expected 100M unique events/day, 0.01% false positive rate → 300 MB memory, acceptably small. On each click event: check Bloom filter. If not present: pass through, add to Bloom filter. If present: probable duplicate, drop the event. Bloom filter false positives (0.01%) = 10K legitimate clicks dropped per day out of 100M — acceptable for real-time dashboard. For billing-critical accuracy: the batch path uses exact dedup. The raw event log in S3 is the source of truth. Spark job: GROUP BY (click_id, UUID) and count distinct — exact dedup. Any click that appears in the raw log but not in the Bloom-filtered stream is captured in the batch reconciliation
>
> [!TIP]
> **🟢 Staff+** — the Bloom filter for a 24-hour window is reset daily. Old Bloom filters are discarded, new ones start fresh. UUID expiry aligns with the billing period


#### Deep dive 3: Hot partition handling — skewed ad traffic
> [!CAUTION]
> **🔴 Weak** — increase the total number of Kafka partitions cluster-wide
>
> [!WARNING]
> **🟡 Strong** — one viral ad campaign can generate 100× normal click volume. In a Kafka cluster partitioned by ad_id, this creates a hot partition
>
> [!TIP]
> **🟢 Staff+** — detection: monitor consumer lag per (topic, partition). When a partition exceeds a lag threshold, detect the hot ad_id causing the spike. Use a compound partition key (ad_id + random_suffix_0_to_N) to spread the load across N partitions. The Flink job handles the merge: KEY BY ad_id across multiple partitions gives correct aggregation regardless of how many partitions the ad's events are spread across. Weak answer: increase partition count. Strong answer: dynamic repartitioning. Monitor partition consumer lag per (topic, partition). When a partition exceeds a lag threshold: detect the hot ad_id causing the spike. Create additional Kafka partitions for that ad_id using a compound key (ad_id + random_suffix). The Flink job handles this: multiple partitions for the same ad_id, Flink aggregates across all partitions in a keyed stream (KEY BY ad_id → Flink handles the merge). The output is correct regardless of how many partitions the ad's events are spread across. At the ClickHouse write side: batching (Flink emits aggregated counts rather than raw events) reduces ClickHouse write amplification. For ClickHouse: ad_id is the partition key for the aggregated table — hot ad_id creates a hot ClickHouse partition. Fix: ReplicatedMergeTree with multiple replicas for hot ad_ids (ClickHouse routes reads to replicas)


_Why the deep dives connect to the scaling problem: "Write-heavy analytics pipeline with correctness requirements." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Lambda framing.

2. "This system has a hard constraint: we need real-time approximate aggregates for dashboards AND exact counts for billing. That immediately implies Lambda Architecture."

3. "Real-time path: click events → Kafka partitioned by ad_id → Flink streaming job → tumbling windows aggregate clicks per ad per time window → write to ClickHouse."

4. "Batch path: raw click events stored in a data lake. A daily Spark job computes exact click counts and reconciles against real-time ClickHouse aggregates. Billing uses these exact counts."

5. "Click dedup: each click carries a client-generated UUID. Bloom filter check at ingestion. The Bloom filter's false positives just mean occasionally missing a dup — dramatically reduces load on the exact dedup check."

6. "I'd name this Lambda Architecture explicitly."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                           |  Ad Placement Svc    |
                           |  returns ad, target  |
                           |  impressionId, sig   |
                           +----------+-----------+
                                      |
                                      v
+---------+        click ad        +--------------------+
|  User   | ---------------------> |   Click Endpoint   |
| Browser |                        |   /click           |
+----+----+                        +-----+----------+---+
     ^                                   |          |
     | 302 redirect to advertiser        |          |
     |                                   |          |
     |                                   |          v
     |                                   |   +-------------+
     |                                   |   | Signature   |
     |                                   |   | Verification|
     |                                   |   +------+------+ 
     |                                   |          |
     |                                   |          v
     |                                   |   +-------------+
     |                                   |   | Redis Cache |
     |                                   |   | dedup by    |
     |                                   |   | impressionId|
     |                                   |   +-------+-----+ 
     |                                   |           |
     |                                   | duplicate?| 
     |                                   |    yes -> drop
     |                                   |           |
     |                                   v          no
     |                             +-----------------------+
     |                             | Kafka or Kinesis      |
     |                             | durable click stream  |
     |                             +-----+------------+----+
     |                                   |            |
     |                                   |            +------------------+
     |                                   |                               |
     |                                   v                               v
     |                         +-------------------+          +-------------------+
     |                         | Flink stream proc |          | S3 data lake      |
     |                         | window by minute  |          | raw click archive |
     |                         | aggregate clicks  |          +---------+---------+
     |                         +---------+---------+                    |
     |                                   |                              |
     |                                   v                              v
     |                         +-------------------+          +-------------------+
     |                         | OLAP analytics DB | <--------| Batch reconcile   |
     |                         | ClickHouse,       |          | Spark daily or    |
     |                         | BigQuery, etc     |          | hourly recompute  |
     |                         +---------+---------+          +-------------------+
     |                                   |
     |                                   v
     |                         +-------------------+
     +-------------------------| Advertiser Query  |
                               | dashboard or API  |
                               +-------------------+
```

The mental model is two paths. The serving path handles the user click and redirect fast, and the analytics path turns raw clicks into queryable per minute metrics.

If you were drawing this in an interview, I would keep the main story to five boxes first. User, Click Endpoint, Stream, Stream Processor, OLAP DB. Then add Redis for dedup and S3 plus batch reconcile only if the interviewer asks about idempotency or correctness.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-24)
