# FB Live Comments

**Medium** · Fan-out at scale · Batching · Comment sampling

Tags: `SSE`, `Kafka`, `Cassandra`, `Batching 100ms`, `Comment sampling`

## Data flow

Comments → Kafka partitioned by video_id. Delivery servers consume their partition and hold SSE connections. Comments are batched in 100ms windows — never pushed individually to 1M connections.


> Batch 100ms bursts — never push per-comment at 1M scale  |  Sample at extreme scale  |  Kafka partition = delivery server locality

## Architecture diagram

```
+-------------------+
                     |   Commenter App   |
                     |  POST comment     |
                     +---------+---------+
                               |
                               v
                     +---------------------+
                     |   API / Comment     |
                     | Management Service  |
                     +----+------------+---+
                          |            |
             write comment|            | publish event
                          v            v
                 +----------------+   +-------------------+
                 | Comments DB    |   | Pub/Sub Bus       |
                 | DynamoDB       |   | Redis or similar  |
                 +----------------+   +---------+---------+
                                                |
                                   fan out to interested servers
                                                |
                      +-------------------------+-------------------------+
                      |                         |                         |
                      v                         v                         v
              +---------------+         +---------------+         +---------------+
              | Realtime Srv 1|         | Realtime Srv 2|   ...   | Realtime Srv N|
              | SSE conns     |         | SSE conns     |         | SSE conns     |
              | local map     |         | local map     |         | local map     |
              +------+--------+         +------+--------+         +------+--------+
                     |                         |                         |
                     v                         v                         v
              +-------------+           +-------------+           +-------------+
              | Viewer Apps |           | Viewer Apps |           | Viewer Apps |
              | SSE stream  |           | SSE stream  |           | SSE stream  |
              +-------------+           +-------------+           +-------------+


History and catch-up path

Viewer App
   |
   | GET /comments/:liveVideoId?cursor=lastCommentId&pageSize=10
   v
+-------------------+
| Comment Management |
| Service            |
+---------+---------+
          |
          v
+-------------------+
| Comments DB       |
| paginated reads   |
+-------------------+
```

If you want the best interview version, I would say it out loud like this. Comments are written through a comment service into DynamoDB. New comment events are published to a pub sub layer. Realtime servers hold SSE connections to viewers and push comments out. Historical comments and reconnect catch-up come from the database using cursor pagination.

For scale, you'll want one extra note on the diagram. Put a load balancer in front of the realtime servers and say you try to co-locate viewers of the same liveVideoId on the same server or small set of servers to reduce fanout waste.


---

<details open>
<summary><strong>Problem</strong></summary>

A real-time fan conversation around a live video with potentially 1M+ simultaneous viewers.

The hard part: naive per-comment fan-out to 1M SSE connections would collapse the system.

</details>


<details>
<summary><strong>Failures</strong></summary>

**A delivery server handling a mega-stream crashes**

All viewers connected to that server instantly lose their comment stream.

_Fix:_ Client auto-reconnects immediately. On reconnect, client sends last_seen_comment_id. Server replays missed comments from Cassandra. Recovery < 5 seconds.

**Kafka consumer lag grows during viral stream**

Comments appear with 30+ second delay. Real-time feel is broken.

_Fix:_ Monitor consumer lag per partition per video_id. Auto-scale delivery servers when lag exceeds 2s. Kafka partition by video_id ensures ordering within a stream is preserved on scale-out.

**Hot comment creates notification storm (1M replies in 10 min)**

Notification service is overwhelmed by reply fan-out.

_Fix:_ Notification batching: group replies from same comment, send one summary notification rather than 1M individual ones. Rate limit notifications per user (max 10/min). This is separate from the comment delivery pipeline.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1M viewers on a viral stream, 1K commenters/s, avg comment = 100 bytes |
| Read QPS | 1M viewers × 1 update/100ms = 10M SSE messages/s — this is why batching is mandatory |
| Write QPS | 1K comments/s → after 100ms batching → 10 batch pushes/s to each delivery server serving 10K viewers |
| Storage | 1K comments/s × 100 bytes × 86400 × 365 ≈ 3 TB/year — Cassandra handles this |
| Cache math | Batch window: 100ms collects 100 comments per batch. 1M viewers receive a 100-comment batch vs. 100 individual pushes. 100× fan-out reduction. |
| Verdict | 100ms batching reduces effective fan-out by 100×. Without it, 10M SSE pushes/s is impossible. With it: 100K batch pushes/s — manageable. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**SSE vs. WebSocket for delivery**

→ SSE (Server-Sent Events)

Comments flow one direction: server → viewer. WebSocket is bidirectional — overkill for this. SSE is simpler, HTTP/2 multiplexed, reconnects automatically, no upgrade handshake.

_Revisit when:_ WebSocket if viewers can react/reply inline (bidirectional interaction). SSE for pure delivery.

**Kafka partition by video_id vs. random partition**

→ Partition by video_id

All comments for a video go to one partition → consumed by one delivery server group → that server holds all viewer connections for that video. No cross-server coordination needed for fan-out.

_Revisit when:_ For mega-streams: multiple partitions per video_id with a coordinator that aggregates before fan-out.

**Show all comments vs. sampling at scale**

→ Full delivery up to 100K viewers, sampling above

Humans can't read > 10 comments/s anyway. For streams with > 100K viewers, sampled delivery (show 10% of comments) is indistinguishable from full delivery in perceived experience. Dramatically reduces fan-out load.

_Revisit when:_ Tiered sampling: VIP comments (verified accounts, top engagement) always shown, rest sampled.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you ensure comment ordering across multiple commenters?**

Kafka partition key = video_id ensures all comments for a stream are ordered within the partition (Kafka offset order). Delivery server appends a server-side sequence number per stream. Client renders in sequence order.

**What happens when a user joins mid-stream?**

On connect, client requests last N comments (e.g., last 30) from Cassandra. Delivery server responds with comment history, then transitions to live SSE stream. Client deduplicates if there's overlap between history and live stream using comment_id.

**How do you handle comment moderation at 1K comments/s?**

Async ML classifier on the write path: comment → Kafka → classifier → store or soft-block. Fast path: store comment immediately (latency matters for live). Slow path: classifier removes blocked comments within 5s. Users may briefly see blocked comments.

**How does this change for a 50M viewer stream (Super Bowl)?**

Two changes: (1) sampling becomes aggressive (0.1% of comments shown — still 1 comment/s per viewer at 1K comments/s). (2) Dedicated comment delivery cluster for the stream, pre-scaled, isolated from normal traffic.

**How do you handle reconnections without missing comments?**

Client stores last_seen_comment_id. On reconnect, includes it in request. Server replays comments after that ID from Cassandra, then transitions to live SSE. Cassandra query: SELECT * FROM comments WHERE video_id=? AND id > last_seen ORDER BY id LIMIT 50.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Long-polling every 2 seconds. Simple DB for comments. Works up to 10K viewers.

**v2 — Real-time** — SSE delivery. Kafka partitioned by video_id. Cassandra for comment storage. 100ms batching. Handles 1M viewers.

**v3 — Mega-streams** — Adaptive comment sampling. Dedicated stream clusters. Tiered delivery (VIP comments always, rest sampled). ML moderation pipeline.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is fan-out under extreme skew. One new comment is a tiny write, but it may need to reach thousands or millions of viewers for the same live video almost immediately.

That creates three scaling pain points. First, the read side explodes because every active viewer needs a near real-time stream, so polling falls over and even push connections become expensive at big scale. Second, hot videos create uneven load. Most streams are quiet, but one viral stream can concentrate huge traffic, connection count, and comment throughput onto a small part of the system. Third, once viewers for the same video are spread across many realtime servers, you need coordination so every server knows which new comments to forward.

The extra twist is that the best design changes for mega-streams. For normal videos, SSE plus pub sub works well. For massive streams, you usually stop trying to show every comment and switch to sampling or snapshot style delivery because humans cannot read thousands of comments per second anyway.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: post comment, deliver to all live viewers in near-real-time, show recent comment history on join. Out of scope: reactions, moderation pipeline, comment replies.
- **Batch — the single most important decision** — 100ms coalescing window. At 1M viewers and 1K comments/sec, per-comment push = 1B SSE pushes/sec. Batching reduces this to 10M/sec. This is a requirement, not an optimization.
- **Kafka partition = delivery server locality** — Partition key = video_id. One partition consumed by one delivery server group. All viewer connections for a video are on those servers. Zero cross-server fan-out coordination needed.
- **SSE over WebSocket** — Comments flow one direction: server → viewer. SSE is simpler (standard HTTP/2, auto-reconnect, no upgrade). Only use WebSocket if viewers need to send data inline.
- **Late joiner catch-up** — On connect, client sends last_seen_comment_id. Server queries Cassandra for comments after that ID (LIMIT 30), then switches to live SSE. Client deduplicates by comment_id.
- **Sampling at extreme scale** — 50M viewers (Super Bowl): show 0.1% of comments — still 1 comment/sec per viewer at 1K comments/sec. Humans cannot read faster. VIP comments (verified accounts) always shown regardless of sampling rate.
- **Failure mode to name** — Delivery server crash: client auto-reconnects, sends last_seen_comment_id, replays missed comments from Cassandra. Cassandra is the durable fallback — never rely on SSE delivery alone for correctness.

> The key insight: batching is everything. 1M individual pushes = system collapse.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Per-comment push vs batching** — Per-comment is low-latency but collapses at 1M viewers. 100ms batching is the only viable approach at scale.

**SSE vs WebSocket** — SSE is simpler for one-directional server push. WebSocket needed if viewers also react inline. For comment delivery only, SSE is the right default.

**Show all comments vs sampling** — At 1M viewers and 1K comments/sec, full delivery means 1B SSE messages/sec. Sampling (show 10% of comments at extreme scale) is indistinguishable to humans — we cannot read faster than ~10/sec anyway.

**Kafka partition by video_id vs random** — Partition by video_id ensures all comments for one stream go to one partition consumed by one delivery server group. This eliminates cross-server fan-out coordination. Random partition would require cross-server routing on every delivery.

> "The key scaling insight is batching. Kafka partition by video_id gives delivery locality. Sample at extreme scale."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Batching — the mandatory optimization that makes fan-out viable
_1M viewers × 1 comment per 100ms = 10M SSE pushes per second if done naively. No system survives this. The critical insight: batching is not an optimization, it's a requirement_

> [!CAUTION]
> **🔴 Weak** — batch at the delivery server
>
> [!WARNING]
> **🟡 Strong** — batch at the Kafka consumption layer. Delivery server consumes Kafka for a video_id partition, buffers incoming comment events for 100ms, then pushes a single batch payload to all 10K connected clients. The batch payload: {comments: [{id, text, author, timestamp}, ...], viewer_count: 1000000}
>
> [!TIP]
> **🟢 Staff+** — the batching window is a tunable parameter. 100ms gives 10 batches/second — fast enough for a live conversation feel, slow enough to be viable at scale. For ultra-high-volume streams: adaptive batching — increase window size as comment rate increases, keeping per-viewer bandwidth constant regardless of comment volume. The coalescing also improves compression ratio on the SSE payload (repeated fields in multiple comments compress well)


#### Deep dive 2: Partitioning and delivery server locality — eliminating cross-server coordination
> [!CAUTION]
> **🔴 Weak** — route comment events to any available delivery server, then fan-out via cross-server pub/sub
>
> [!WARNING]
> **🟡 Strong** — Kafka partition key = video_id. All comments for a video go to one Kafka partition. One delivery server group (2-3 servers) consumes each partition. By contract, all viewers of a video are connected to servers in the group that consumes that video's partition. Result: fan-out requires zero cross-server communication — the consuming server directly has all viewer connections
>
> [!TIP]
> **🟢 Staff+** — what about load balancing? A viral video creates a hot partition (1M viewers on 2-3 servers). Mitigation: hot video detection — when a video exceeds a viewer threshold, allocate multiple Kafka partitions for it and a larger server group. Connection routing: clients are directed to servers in the right group via DNS-based load balancing with video_id affinity. For mega-streams (Super Bowl: 50M+ viewers): dedicated server cluster for the stream, isolated from regular traffic, pre-provisioned


#### Deep dive 3: Historical comments and late joiners — the catch-up problem
> [!CAUTION]
> **🔴 Weak** — send the entire comment history on connect, then switch to live stream
>
> [!WARNING]
> **🟡 Strong** — a viewer joins a live stream 30 minutes in. They need: (1) the last N comments to provide context, (2) then a seamless transition to the live comment stream. Weak answer: load from DB then connect to SSE. Strong answer: explicit catch-up protocol. On SSE connection: client sends last_comment_id = 0 (new viewer). Server queries Cassandra: SELECT * FROM comments WHERE video_id=? AND id > 0 ORDER BY id ASC LIMIT 30. Returns the 30 most recent comments. Then switches client to live SSE stream. Client deduplicates by comment_id in case the SSE stream delivers a comment that was already in the catch-up response
>
> [!TIP]
> **🟢 Staff+** — the catch-up query uses a cursor (comment_id) not a timestamp — comment_ids are monotonically increasing Snowflake IDs that encode time, so they're both ordered and unique. The transition from catch-up to live is seamless: client tracks max comment_id received, SSE stream starts from next ID


_Why the deep dives connect to the scaling problem: "Fan-out under extreme skew." Deep dive 1 solves the fan-out rate problem. Deep dive 2 solves the routing problem. Deep dive 3 solves the late-joiner problem._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Batching-first script.

2. "Clarifying questions: are we delivering comments to live video viewers only, or also recorded videos? And what's the scale target — Twitch-size (thousands of viewers) or Facebook Live scale (millions)?"

3. "Good — Facebook Live scale, millions of viewers. Core features: submit comment, deliver to all viewers in near-real-time, show comment history on join. Out of scope: reactions, moderation pipeline."

4. "The single most important design decision: batching. At 1M viewers and 1K comments/sec, per-comment push = 1B SSE messages/sec. 100ms batching: push the last N comments as a batch payload, 10 times/second. Reduces to 10M messages/sec. This is a requirement, not an optimization."

5. "Architecture: Comment API → Kafka (partition by video_id) → Delivery servers (consume their partition) → SSE to viewers. Kafka partition key = video_id ensures all comments for one stream hit one delivery server group. Zero cross-server coordination for fan-out."

6. "Comment storage: Cassandra, partitioned by video_id. On viewer connect: send last 30 comments from Cassandra (catch-up), then switch to live SSE stream. Client deduplicates by comment_id at the boundary."

7. "For a 50M viewer stream: aggressive sampling — show 0.1% of comments. Humans cannot read faster than ~10/sec. VIP and high-engagement comments always shown. This makes the system feasible at Super Bowl scale."

8. "Key tradeoff: SSE over WebSocket. Comments are unidirectional — server pushes, viewers watch. SSE is simpler, HTTP/2 multiplexed, auto-reconnects. WebSocket adds complexity we don't need for delivery-only."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                     |   Commenter App   |
                     |  POST comment     |
                     +---------+---------+
                               |
                               v
                     +---------------------+
                     |   API / Comment     |
                     | Management Service  |
                     +----+------------+---+
                          |            |
             write comment|            | publish event
                          v            v
                 +----------------+   +-------------------+
                 | Comments DB    |   | Pub/Sub Bus       |
                 | DynamoDB       |   | Redis or similar  |
                 +----------------+   +---------+---------+
                                                |
                                   fan out to interested servers
                                                |
                      +-------------------------+-------------------------+
                      |                         |                         |
                      v                         v                         v
              +---------------+         +---------------+         +---------------+
              | Realtime Srv 1|         | Realtime Srv 2|   ...   | Realtime Srv N|
              | SSE conns     |         | SSE conns     |         | SSE conns     |
              | local map     |         | local map     |         | local map     |
              +------+--------+         +------+--------+         +------+--------+
                     |                         |                         |
                     v                         v                         v
              +-------------+           +-------------+           +-------------+
              | Viewer Apps |           | Viewer Apps |           | Viewer Apps |
              | SSE stream  |           | SSE stream  |           | SSE stream  |
              +-------------+           +-------------+           +-------------+


History and catch-up path

Viewer App
   |
   | GET /comments/:liveVideoId?cursor=lastCommentId&pageSize=10
   v
+-------------------+
| Comment Management |
| Service            |
+---------+---------+
          |
          v
+-------------------+
| Comments DB       |
| paginated reads   |
+-------------------+
```

If you want the best interview version, I would say it out loud like this. Comments are written through a comment service into DynamoDB. New comment events are published to a pub sub layer. Realtime servers hold SSE connections to viewers and push comments out. Historical comments and reconnect catch-up come from the database using cursor pagination.

For scale, you'll want one extra note on the diagram. Put a load balancer in front of the realtime servers and say you try to co-locate viewers of the same liveVideoId on the same server or small set of servers to reduce fanout waste.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-10)
