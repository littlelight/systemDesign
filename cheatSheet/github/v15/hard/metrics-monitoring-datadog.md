# Metrics monitoring (Datadog)

**Hard** · Time-series · Rollups · Stream alert evaluation

Tags: `InfluxDB / TimescaleDB`, `Kafka`, `Flink`, `Rollup/downsampling`, `Cardinality explosion`

## Data flow

Host agents batch metrics and push every 10s to Kafka. Three consumers: TSDB write path, Flink alert evaluator, and Rollup Worker. The TSDB is columnar and time-partitioned. Rollup: 1s samples → 1min averages (30 days) → 1hr averages (forever). Cardinality explosion: each unique metric + label combination creates a new series.


> Cardinality explosion: each unique metric+labels = new series → control labels  |  Dashboards can be stale; alerts must not be lost

## Architecture diagram

```
+----------------------+
                         |   Users / Engineers  |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | Dashboard / Query UI |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |    Query Service     |
                         | parse DSL, auth,     |
                         | cache, query split   |
                         +----+------------+----+
                              |            |
                    cache hit |            | query raw or rollups
                              v            v
                        +---------+   +-------------------+
                        |  Redis  |   | Time Series DB    |
                        | Cache   |   | raw + rollups     |
                        +---------+   | sharded + replica |
                                      +---------+---------+
                                                ^
                                                |
                                      +---------+---------+
                                      | Storage Consumers |
                                      | batch writes      |
                                      +---------+---------+
                                                ^
                                                |
+-------------+      +-------------------+      |
| Servers and | ---> | Local Agent /     | ---> |
| Services    |      | Collector         |      |
| emit metrics|      | buffer + batch    |      |
+-------------+      +---------+---------+      |
                                 |              |
                                 v              |
                      +-------------------------+
                      | Ingestion Service       |
                      | validate, normalize,    |
                      | auth, rate limit        |
                      +-----+-------------+-----+
                            |             |
                            |             v
                            |   +----------------------+
                            |   | Cardinality Guard    |
                            |   | policy check         |
                            |   | label allowlist      |
                            |   +----+------------+----+
                            |        |            |
                            |        |            v
                            |        |      +-----------+
                            |        |      | Postgres  |
                            |        |      | Policies  |
                            |        |      | Alert cfg |
                            |        |      +-----------+
                            |        v
                            |   +-----------+
                            |   |   Redis   |
                            |   | series set|
                            |   | counters   |
                            |   +-----------+
                            |
                            v
                     +----------------------+
                     |        Kafka         |
                     | durable buffer       |
                     | partitioned stream   |
                     +----+-------------+---+
                          |             |
                          |             |
                          |             +----------------------+
                          |                                    |
                          v                                    v
               +----------------------+             +----------------------+
               | Storage Consumers    |             | Alert Evaluator      |
               | write to TSDB        |             | poll rules, query    |
               +----------------------+             | TSDB every 30 to 60s |
                                                    +----------+-----------+
                                                               |
                                                               v
                                                    +----------------------+
                                                    | Alert Events         |
                                                    | firing or resolved   |
                                                    +----------+-----------+
                                                               |
                                                               v
                                                    +----------------------+
                                                    | Notification Service |
                                                    | dedupe, grouping,    |
                                                    | silence, escalation  |
                                                    +----+----------+------+
                                                         |          |
                                                         |          |
                                                         v          v
                                                   +---------+   +----------+
                                                   | Slack   |   | PagerDuty|
                                                   +---------+   +----------+
                                                         |
                                                         v
                                                      +------+
                                                      |Email |
                                                      +------+
```

The main story is ingest, buffer, store, query, then alert. If you are drawing this in an interview, I would keep the first pass even simpler with agents, ingestion, Kafka, time-series DB, query service, alert evaluator, and notification service. Then add cardinality control, cache, and rollups only if the interviewer pushes on scale or latency.


---

<details open>
<summary><strong>Problem</strong></summary>

Collect and use system health data at very large scale. Ingest measurements, store as time series, query in dashboards, and trigger alerts.

The hard part: huge write volume, fast queries across long time ranges, reliable alerting, and cardinality explosion.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Cardinality explosion: a developer adds user_id as a metric tag**

1M active users × 100 metrics each = 100M new series created in hours. TSDB memory exhausted. Query performance degrades catastrophically.

_Fix:_ Cardinality enforcement at ingestion: count distinct tag combinations per metric. Alert when a metric's cardinality exceeds 10K series. Reject (or quarantine) metrics that exceed the cardinality cap with an actionable error message to the developer.

**Alert evaluator falls behind during an incident**

Alerts that should fire during the incident are delayed. On-call engineer doesn't get paged. Incident duration extends.

_Fix:_ Alert evaluation is the highest-priority consumer. Dedicated Kafka consumer group for alerts with separate scaling from dashboard consumers. Alert evaluation SLA: P99 < 30s. Monitor alert lag as a first-class metric.

**TSDB write node goes down during peak ingestion**

5M points/s cannot be written. If Kafka buffer is only 1 hour, unprocessed metrics are lost.

_Fix:_ Kafka retention: 24 hours. This gives 24h to recover TSDB before data loss. TSDB primary + synchronous replica. On primary failure: promote replica (< 30s). Replay from Kafka after recovery. Dashboards may be briefly stale — acceptable.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500K hosts, 200 metrics/host, push every 10s, 1M alert rules evaluated every 60s |
| Read QPS | 500K × 200 / 10 = 10M data points/s ingested → Kafka → TSDB |
| Write QPS | Dashboard queries: 100K users × 5 queries/min / 60 = 8,333 dashboard QPS — served from TSDB + cache |
| Storage | 10M points/s × 8 bytes × 86400 = 6.9 TB/day raw. With rollup: 90% reduction after 24h → 690 GB/day avg. Over a year: ~250 TB. |
| Cache math | 10M series × avg active series metadata 100 bytes = 1 GB series index in memory per TSDB node (10 nodes = 10 GB total for the index). Feasible. |
| Verdict | 10M writes/s is the real scale challenge. Single TSDB node handles ~500K writes/s. Need 20 TSDB nodes minimum. Kafka's role as the durable buffer (24h retention) is critical. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Push vs. pull for metric collection**

→ Push (agents push to ingestion service) as default, pull (Prometheus-style) for some use cases

Push: simpler agent, no firewall rules needed from collector to target, better for ephemeral containers. Pull: collector controls sampling rate, easier to detect down targets. Datadog uses push. Prometheus uses pull. For interview: state both exist, default to push for cloud-native environments.

_Revisit when:_ Pull for service health checks (if target doesn't send a metric, collector can detect it's down). Push for all other metrics.

**TSDB choice: InfluxDB vs. TimescaleDB vs. Prometheus vs. M3DB**

→ InfluxDB or M3DB (purpose-built for high-cardinality time-series at Datadog scale)

Prometheus: excellent for Kubernetes monitoring but limited long-term storage and single-node. TimescaleDB: excellent SQL support but lower write throughput. InfluxDB/M3DB: designed for 10M+ writes/s with high cardinality.

_Revisit when:_ TimescaleDB if SQL query flexibility is needed for complex analytics on top of metrics.

**Alert evaluation: polling vs. streaming**

→ Polling (scheduled query every 30-60s) as default

Streaming (Flink/Kafka Streams): lower latency (<5s) but much higher operational complexity. Most monitoring alerts don't require sub-minute latency. Polling is simpler, testable, and predictable.

_Revisit when:_ Streaming for anomaly detection use cases where <60s detection time matters (e.g., real-time fraud, SLO burn rate alerting).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a host that stops sending metrics?**

Absence detection: alert on 'metric not received in last N intervals'. Implementation: each metric has a last_received timestamp. Background job checks all active series every 30s, fires 'host down' alert if any series hasn't updated. This is a separate alert type from threshold-based alerts.

**How do you make dashboards feel fast even for 30-day time ranges?**

Rollup architecture: raw 1s data retained for 24h. 1min aggregates for 30 days. 1hr aggregates forever. Dashboard query selects the appropriate rollup based on time range. 30-day dashboard: uses 1min rollups (43,200 points) vs. raw (2.6M points). 10× faster query.

**How do you handle high cardinality without completely blocking users?**

Graduated enforcement: warn at 1K series, soft cap at 10K (logs + alert), hard cap at 100K (reject with actionable error). Allow overrides for trusted teams with explicit justification. Provide tooling to identify which tags are causing the explosion. Don't just block — help users fix it.

**How do you ensure alert reliability during an incident?**

Separate alert evaluation pipeline from dashboard query pipeline. During an incident, users flood dashboards — this must not compete with alert evaluation. Separate Kafka consumer groups, separate compute. Alert evaluation gets reserved CPU quota. Circuit breaker: if TSDB is overloaded, serve alerts from a pre-computed alert state cache.

**How would you add distributed tracing to this system?**

Tracing is a different data model: tree of spans with parent-child relationships. Store traces in a columnar store (Cassandra or ClickHouse) partitioned by trace_id. Sampling is critical — storing every trace at 100K requests/s is expensive. Head-based or tail-based sampling at 0.1-1%. Separate from metrics pipeline but can share Kafka infrastructure.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single host** — StatsD + Graphite. Single TSDB node. Dashboard is a couple of time-series charts. Works for one team monitoring one service.

**v2 — Scale ingestion** — Kafka for ingestion buffering. 10-node TSDB cluster. Rollup pipeline. Alert evaluation service. Cardinality caps. Handles 500K hosts.

**v3 — Enterprise** — M3DB or purpose-built TSDB for 10M writes/s. Distributed tracing integration. ML anomaly detection. Cross-service SLO tracking. Custom metrics plugins. Multi-tenant with per-team quotas.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hardest part is cardinality explosion. In a metrics system, every unique combination of metric name and labels creates a new time series, so a few extra labels can turn one metric into millions of series very quickly.

That causes three scaling problems. First, ingestion gets expensive because the system is not just appending values. It also has to track metadata and indexes for huge numbers of series. Second, queries get slower because dashboards often need to scan and aggregate across many series over long time ranges. Third, alerts add pressure because they need fresh enough data and reliable evaluation even while the write path is constantly busy.

A good short interview answer is this. Metrics Monitoring is hard to scale because it combines a massive continuous write stream, expensive time range queries, and exploding series count from labels, all while the system itself needs to stay available during incidents.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Ingest path is write-heavy** — Agents batch metrics. Kafka buffers spikes and decouples ingestion from storage.
- **TSDB for storage** — Metrics are append-only, queried by time range. TSDB is the right default.
- **Query path is read-heavy** — Rollups + caching make dashboards fast. Rollups: raw 24h → 1min 30d → 1hr forever.
- **Alert path must be reliable** — Polling every 30–60s is the simple default. Dashboards can be stale. Alerts must not be lost.
- **Cardinality explosion** — Each unique metric + label combination = a new time series. A label like user_id creates billions of series. Enforce label allowlists and caps.

> "I'd design it as a pipeline from agents → Kafka → TSDB, then split into a query path for dashboards and an alert path for scheduled checks. The key scaling risk is cardinality explosion — control labels and use rollups for efficient reads."

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Polling alerts vs stream processing** — Polling every 30–60s is simpler and good enough for most monitoring. Stream processing gives lower latency but adds operational complexity.

**Raw data vs rollups** — Raw gives accuracy but makes long time-range queries too slow. Rollups make dashboards fast but lose detail. Both are needed.

**Accept all labels vs cardinality controls** — Flexible labels give visibility but create too many unique series. Allowlists or caps protect the system but limit visibility.

> "Start with agents, Kafka, a time-series database, rollups, and polling-based alerts. Main tradeoffs: freshness vs complexity, query flexibility vs cost, label flexibility vs cardinality explosion."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Cardinality explosion — the hidden scaling killer
> [!CAUTION]
> **🔴 Weak** — accept all metrics with any label combinations — flexibility is good, users know what they need
>
> [!WARNING]
> **🟡 Strong** — cardinality enforcement at ingestion. Each unique combination of metric name + label values creates a new time series. One developer adds user_id to a request_latency metric: 10M users × 1 metric = 10M new series overnight. TSDB memory exhausted, query performance collapses. Hard cap per metric (10K series), alert at 1K series, reject above cap with an actionable error
>
> [!TIP]
> **🟢 Staff+** — label allowlist: developers define allowed labels per metric at registration time. user_id is not in the allowlist by default — it requires explicit review. Provide a cardinality dashboard so developers can see the impact of their instrumentation choices before hitting the cap. The allowlist is the proactive control; the hard cap is the safety net


#### Deep dive 2: Query performance — rollups and columnar storage for time-range queries
> [!CAUTION]
> **🔴 Weak** — store raw metrics at 1-second resolution forever, query from raw data for all dashboard requests. A 30-day dashboard chart at 1-second resolution = 2.6M data points per metric per host. At 500K hosts, this is 1.3 trillion data points per query — impossible
>
> [!WARNING]
> **🟡 Strong** — multi-resolution rollup architecture. Raw (1s): retain 24 hours. 1-minute rollup: retain 30 days. 1-hour rollup: retain forever. Dashboard query routing: time range > 24h → 1-minute rollups; > 30 days → 1-hour rollups. A 30-day chart at 1-min resolution = 1,440 points vs 2.6M raw — 1800× fewer data points, no visible chart difference at typical widths
>
> [!TIP]
> **🟢 Staff+** — implementation: TimescaleDB continuous aggregates or InfluxDB tasks compute rollups automatically on insert. The rollup is always up-to-date without a separate batch job. ClickHouse for the rollup serving layer: columnar compression and SIMD-accelerated GROUP BY queries make analytical aggregations over millions of series fast


#### Deep dive 3: Alert evaluation — reliability and isolation from dashboard traffic
> [!CAUTION]
> **🔴 Weak** — run alert evaluation as a query against the same TSDB that serves dashboards. During an incident, dashboard traffic spikes 3×. Alert evaluation competes for TSDB resources and gets delayed precisely when alerts are most critical
>
> [!WARNING]
> **🟡 Strong** — dedicate separate compute for alert evaluation — separate Kafka consumer group, separate TSDB read replicas, reserved CPU quota. Alert evaluation must never compete with dashboard queries
>
> [!TIP]
> **🟢 Staff+** — s: (1) alert evaluation falls behind (Kafka consumer lag) → autoscale alert evaluation workers, alert at >30s lag; (2) TSDB replica goes down → circuit breaker switches to alternate replica; (3) flapping alerts — cooldown periods (alert must be in firing state for N consecutive evaluations before paging) prevent noise during unstable incidents. Monitor the monitoring system: alert evaluation latency is itself a first-class SLA metric


_Why the deep dives connect to the scaling problem: "Massive write stream, expensive time-range queries, exploding series count." Each deep dive addresses one dimension._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Ingest-first, cardinality-aware script.

2. "Clarifying questions: are we monitoring infrastructure metrics like CPU and memory, or also custom application metrics and distributed tracing? And what are the target SLAs — how fresh do dashboards need to be, how fast do alerts need to fire?"

3. "Good — infrastructure plus custom app metrics. Dashboard freshness: 30 seconds is fine. Alert latency: under 1 minute. No tracing in scope for now."

4. "Scale: 500K hosts, 200 metrics each, push every 10 seconds = 10M data points per second. That's the number that drives everything."

5. "I'd lead with the most dangerous anti-pattern: cardinality explosion. Every unique combination of metric name and label values creates a new time series. One developer adds user_id as a label on a request latency metric: 10M users × 1 metric = 10M new series overnight. TSDB memory exhausted, queries collapse. I'd design cardinality enforcement in at ingestion — hard cap per metric, alert before the cap."

6. "Ingest path: agents batch metrics locally and push every 10s. Kafka buffers the spike and decouples ingestion from storage. 10M points/sec is within Kafka's range with appropriate partitioning."

7. "Three consumers from Kafka: (1) TSDB writer — InfluxDB or TimescaleDB, append-only, time-partitioned; (2) Alert evaluator — polls TSDB every 30-60s, isolated from dashboard traffic; (3) Rollup worker — 1s data → 1min aggregates → 1hr aggregates."

8. "Query path: rollups make dashboard queries fast. A 30-day chart at 1-min resolution = 1,440 points vs 2.6M raw points. The rollup is pre-computed — query hits the rollup table directly."

9. "Key isolation point: alert evaluation must be on dedicated compute, isolated from dashboard queries. During an incident, dashboard traffic spikes 3×. If alerts compete with dashboards for TSDB resources, alerts get delayed exactly when they're most critical."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                         |   Users / Engineers  |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | Dashboard / Query UI |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |    Query Service     |
                         | parse DSL, auth,     |
                         | cache, query split   |
                         +----+------------+----+
                              |            |
                    cache hit |            | query raw or rollups
                              v            v
                        +---------+   +-------------------+
                        |  Redis  |   | Time Series DB    |
                        | Cache   |   | raw + rollups     |
                        +---------+   | sharded + replica |
                                      +---------+---------+
                                                ^
                                                |
                                      +---------+---------+
                                      | Storage Consumers |
                                      | batch writes      |
                                      +---------+---------+
                                                ^
                                                |
+-------------+      +-------------------+      |
| Servers and | ---> | Local Agent /     | ---> |
| Services    |      | Collector         |      |
| emit metrics|      | buffer + batch    |      |
+-------------+      +---------+---------+      |
                                 |              |
                                 v              |
                      +-------------------------+
                      | Ingestion Service       |
                      | validate, normalize,    |
                      | auth, rate limit        |
                      +-----+-------------+-----+
                            |             |
                            |             v
                            |   +----------------------+
                            |   | Cardinality Guard    |
                            |   | policy check         |
                            |   | label allowlist      |
                            |   +----+------------+----+
                            |        |            |
                            |        |            v
                            |        |      +-----------+
                            |        |      | Postgres  |
                            |        |      | Policies  |
                            |        |      | Alert cfg |
                            |        |      +-----------+
                            |        v
                            |   +-----------+
                            |   |   Redis   |
                            |   | series set|
                            |   | counters   |
                            |   +-----------+
                            |
                            v
                     +----------------------+
                     |        Kafka         |
                     | durable buffer       |
                     | partitioned stream   |
                     +----+-------------+---+
                          |             |
                          |             |
                          |             +----------------------+
                          |                                    |
                          v                                    v
               +----------------------+             +----------------------+
               | Storage Consumers    |             | Alert Evaluator      |
               | write to TSDB        |             | poll rules, query    |
               +----------------------+             | TSDB every 30 to 60s |
                                                    +----------+-----------+
                                                               |
                                                               v
                                                    +----------------------+
                                                    | Alert Events         |
                                                    | firing or resolved   |
                                                    +----------+-----------+
                                                               |
                                                               v
                                                    +----------------------+
                                                    | Notification Service |
                                                    | dedupe, grouping,    |
                                                    | silence, escalation  |
                                                    +----+----------+------+
                                                         |          |
                                                         |          |
                                                         v          v
                                                   +---------+   +----------+
                                                   | Slack   |   | PagerDuty|
                                                   +---------+   +----------+
                                                         |
                                                         v
                                                      +------+
                                                      |Email |
                                                      +------+
```

The main story is ingest, buffer, store, query, then alert. If you are drawing this in an interview, I would keep the first pass even simpler with agents, ingestion, Kafka, time-series DB, query service, alert evaluator, and notification service. Then add cardinality control, cache, and rollups only if the interviewer pushes on scale or latency.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-27)
