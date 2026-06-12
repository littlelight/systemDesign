# Message queue (Kafka)

**Hard** · Partitions · Consumer groups · Replication

Tags: `Kafka`, `Partitions`, `Consumer groups`, `Replication`, `ISR`, `Offset commit`

## Data flow

Topics split into partitions for parallelism. Consumer groups assign one consumer per partition. Replicas in ISR acknowledge writes for durability. Consumers commit offsets after processing.


> Partition = parallelism unit  |  Consumer group = load balance  |  ISR = durability quorum

## Architecture diagram

```
Producers -> Kafka Topic (P0..Pn) -> Consumer Group -> Workers
         \-> replicas in ISR
```

Draw partitions and consumer group. Mention ISR for durability.


---

<details open>
<summary><strong>Problem</strong></summary>

Durable, high-throughput pub/sub log that decouples producers and consumers, scales horizontally, and replays history.

Hard parts: partition key design, consumer lag, and exactly-once vs at-least-once semantics.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Hot partition key**

One partition overloaded; lag grows.

_Fix:_ Salt hot keys to sub-partitions or dedicated topic.

**Poison message infinite retry**

Consumer stuck, lag unbounded.

_Fix:_ DLQ after max retries.

**Rebalance storm**

Consumers pause frequently during deploy.

_Fix:_ Cooperative rebalancer. Static membership.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1M events/s, 100 partitions, RF=3, 7-day retention |
| Read QPS | 1M consumer records/s with 100 consumers |
| Write QPS | 1M produce/s with proper partitioning |
| Storage | 1M/s × 1KB × 7d ≈ 600 TB — tiered storage |
| Cache math | Consumer lag metric is operational focus |
| Verdict | Partition count is capacity planning knob. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Partition count**

→ Start higher than needed — reducing is hard

Repartitioning changes key→partition mapping.

_Revisit when:_ 100–200 partitions per broker guideline.

**acks setting**

→ acks=all for critical data

acks=1 faster but can lose data on leader failure.

_Revisit when:_ acks=1 for metrics where loss acceptable.

**Kafka vs SQS**

→ Kafka for replay and high throughput

SQS simpler ops, no replay log.

_Revisit when:_ SQS for task queues without replay need.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you preserve global ordering?**

Single partition — limits throughput. Usually order per key is enough.

**How do you add consumers without rebalance pain?**

Cooperative-sticky assignor. Incremental rebalance. Avoid frequent consumer restarts.

**How do you handle messages too large for Kafka?**

Store payload in S3, put pointer in Kafka message. Claim-check pattern.

**How do you migrate clusters?**

MirrorMaker 2 dual-write, switch consumers, drain old cluster.

**How do you achieve exactly-once?**

Idempotent producer + transactional writes + idempotent consumer. Higher latency.

**How do you prioritize topics?**

Separate clusters or dedicated broker pools per SLA tier.

**How do you compact topics?**

log.compaction for changelog topics — keeps latest per key.

**How do you debug consumer lag?**

Check slow processing, GC pauses, insufficient partitions, hot keys.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single broker** — Dev only. No HA.

**v2 — Production cluster** — RF=3, ISR, monitoring, DLQ.

**v3 — Multi-DC** — MirrorMaker, tiered storage, exactly-once where needed.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Kafka scales by adding partitions and brokers. Limits: partition count planning, consumer lag under slow workers, hot keys.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Partition by key** — Same key → same partition preserves ordering per entity.
- **Consumer group scaling** — Max parallelism = partition count. More consumers than partitions sit idle.
- **Replication factor 3** — Leader + followers. ISR replicas must ack before commit (configurable).
- **Retention** — Log retained days/weeks — consumers can replay or catch up.
- **At-least-once default** — Commit offset after process. Idempotent consumers handle duplicates.
- **Dead letter topic** — Poison messages after N failures — do not block partition.
- **Monitor consumer lag** — Lag = high-priority alert. Autoscale consumers on lag.

> Partition key, consumer group, offset commit — Kafka trilogy.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Kafka vs RabbitMQ** — Kafka: high-throughput log, replay, retention. RabbitMQ: task queues, routing, lower latency per message.

**At-least-once vs exactly-once** — Exactly-once needs transactions/idempotent producer — higher latency. At-least-once + idempotent consumer is default.

**More partitions vs bigger messages** — Partitions scale throughput; large messages need compression or external blob store.

**Push vs pull consumers** — Kafka consumers pull — control their own pace, natural backpressure.

> "Partition for ordering, consumer group for scale, offset commit for recovery."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Partition key design
_Wrong key (constant) → one hot partition. Right key (user_id, order_id) spreads load and preserves per-entity order_

> [!CAUTION]
> **🔴 Weak** — Oversimplify partition key design — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Wrong key (constant) → one hot partition. Right key (user_id, order_id) spreads load and preserves per-entity order
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Consumer groups and rebalancing
_Adding consumer triggers rebalance — brief pause. Use cooperative sticky assignor to minimize disruption. Max consumers = partitions_

> [!CAUTION]
> **🔴 Weak** — Oversimplify consumer groups and rebalancing — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Adding consumer triggers rebalance — brief pause. Use cooperative sticky assignor to minimize disruption. Max consumers = partitions
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Replication and ISR
_min.insync.replicas=2 with acks=all prevents data loss on broker failure. Unclean leader election trades availability for loss risk — avoid for financial topics_

> [!CAUTION]
> **🔴 Weak** — UUID v4 everywhere — collisions are negligible.
>
> [!WARNING]
> **🟡 Strong** — min.insync.replicas=2 with acks=all prevents data loss on broker failure. Unclean leader election trades availability for loss risk — avoid for financial topics
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Handling poison pills
_After 3 failures route to DLQ. Skip bad message so partition progresses. Alert on DLQ depth_

> [!CAUTION]
> **🔴 Weak** — Oversimplify handling poison pills — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — After 3 failures route to DLQ. Skip bad message so partition progresses. Alert on DLQ depth
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Log-oriented script.

2. "Kafka is a durable commit log, not a traditional queue."

3. "Producers write to topic partitions. Partition key chooses partition."

4. "Consumer group: each partition consumed by one consumer in group."

5. "Commit offset after successful processing — at-least-once."

6. "Monitor consumer lag. DLQ for poison messages."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Producers -> Kafka Topic (P0..Pn) -> Consumer Group -> Workers
         \-> replicas in ISR
```

Draw partitions and consumer group. Mention ISR for durability.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-33)
