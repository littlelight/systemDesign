# Notification system (APNs/FCM)

**Medium** · Multi-channel · Fan-out · Dedup · Rate limits

Tags: `Kafka`, `APNs`, `FCM`, `Twilio`, `SendGrid`, `Dedup`, `DLQ`

## Data flow

Triggers hit the Notification API, which checks user preferences, applies per-user rate limits, and deduplicates via Redis SETNX on event_id. Valid events publish to per-channel Kafka topics. Channel workers call APNs/FCM/SMS/email providers with exponential backoff and DLQ for permanent failures.


> Check prefs BEFORE enqueue  |  Redis SETNX event_id = dedup  |  Stagger viral fan-out

## Architecture diagram

```
+------------------+
                    | Trigger sources  |
                    | txn · marketing  |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | Notification API |
                    | prefs · dedup    |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
        +-----------+  +-----------+  +-----------+
        | Kafka iOS |  |Kafka SMS  |  |Kafka Email|
        +-----+-----+  +-----+-----+  +-----+-----+
              |              |              |
              v              v              v
        +-----------+  +-----------+  +-----------+
        | iOS worker|  | SMS worker|  |Email worker|
        +-----+-----+  +-----+-----+  +-----+-----+
              |              |              |
              v              v              v
           APNs/FCM       Twilio        SendGrid
```

Interview version: API checks prefs and dedup, publishes to Kafka, workers call providers. Add DLQ and token cleanup if pushed on reliability.


---

<details open>
<summary><strong>Problem</strong></summary>

Send millions of notifications daily across push, SMS, and email with delivery guarantees, user preference respect, and survival of viral fan-out spikes.

Hard parts: multi-channel routing, at-least-once deduplication, and third-party provider rate limits during events.

</details>


<details>
<summary><strong>Failures</strong></summary>

**APNs returns BadDeviceToken**

Worker retries dead token forever, wasting capacity.

_Fix:_ Delete token on first BadDeviceToken. Subscribe to APNs feedback service for batch cleanup.

**Duplicate send after worker crash**

User receives two identical push notifications.

_Fix:_ Redis SETNX on event_id before send. Provider-level collapse_key as second layer.

**10M notifications in 60 seconds**

Provider rate limits hit. Queue depth grows. Delivery delayed hours.

_Fix:_ Stagger enqueue. Scale workers on consumer lag. Group non-critical notifications.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 100M users, 5 notifications/user/day, peak viral event 10M in 60s |
| Read QPS | Steady: 100M×5/86400 ≈ 5,800 deliveries/s |
| Write QPS | Peak: 10M/60 ≈ 167K enqueue/s to Kafka |
| Storage | 500M notifications/day × 500B ≈ 250 GB/day Kafka retention (7d ≈ 1.75 TB) |
| Cache math | Dedup set: 500M event_ids × 20B × 2d TTL ≈ 20 GB Redis |
| Verdict | Steady state is easy. Peak needs autoscaling workers and staggered fan-out. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Check preferences before or after queue?**

→ Before enqueue

Opted-out users never enter the queue. Preferences cached in Redis for sub-ms checks.

_Revisit when:_ After queue only if preference service is too slow — rare.

**One Kafka topic or per-channel?**

→ Per-channel topics

Independent scaling and retry policies per channel type.

_Revisit when:_ Single topic with channel header if ops simplicity matters more.

**Transactional vs marketing priority**

→ Separate queues / topics

Password resets must not wait behind marketing campaigns.

_Revisit when:_ Single queue with priority field if volume is low.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you deliver to an offline mobile user?**

Retry until APNs/FCM acknowledges receipt (not device delivery). Providers queue latest notification for offline devices. Our job ends at provider ACK.

**How do you implement notification grouping ('5 new likes')?**

Buffer social notifications 15 min. Redis sorted set pending:{user}:{type}. Scheduled job flushes grouped message. Security alerts: delay=0, no grouping.

**How do you handle GDPR deletion?**

Delete tokens. Redis blocklist for deleted user_ids checked at every worker. Provider unsubscribe APIs for email. Document in-flight message SLA.

**How do you prevent notification storms to one user?**

Per-user hourly cap in Redis INCR with TTL. Critical channel bypasses cap. Excess marketing notifications dropped or deferred.

**How do you support scheduled notifications?**

Delayed Kafka message or Redis sorted set scored by send_time. Scheduler publishes when due. Idempotency key includes scheduled slot.

**How do you test without spamming real users?**

Sandbox provider credentials. Feature flag per user for test mode. Shadow queue that logs but does not send.

**How do you measure delivery success?**

Track enqueued → sent → provider_ack → opened. Consumer lag on Kafka as health metric. DLQ depth alerts.

**How would you add in-app notification inbox?**

Persist notification row in Cassandra/Postgres on enqueue. Push is best-effort delivery; inbox is source of truth for history.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Sync HTTP** — Call APNs inline. Works to ~100/s. Blocks request thread.

**v2 — Async queue** — Kafka per channel. Workers with retry + DLQ. Handles 10K/s.

**v3 — Enterprise** — Dedup, preferences, priority queues, grouping, analytics, GDPR blocklist. Handles viral spikes.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is fan-out: one viral event creates millions of deliveries, but third-party providers cap throughput. Kafka handles ingestion; workers and staggered delivery handle the bottleneck.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope** — Core: send push/SMS/email, respect opt-outs, retry failures, track delivery status. Out of scope unless asked: in-app inbox, rich media templates, A/B testing.
- **Async by default** — API enqueues and returns immediately. Never block the caller on APNs/FCM latency.
- **Per-channel Kafka topics** — ios-push, android-push, sms, email — independent worker pools and retry policies.
- **Dedup on event_id** — SETNX event_id with 24h TTL before send. Prevents duplicate notifications on Kafka replay.
- **Preferences before queue** — Cache opt-outs in Redis. Filter before publishing — never waste queue capacity on opted-out users.
- **Two-tier priority** — Transactional (password reset, purchase) bypasses marketing rate limits. Marketing respects max N/user/hour.
- **Invalid token cleanup** — On APNs BadDeviceToken: delete token immediately. Never retry dead endpoints.

> Prefs first, dedup second, per-channel workers third. Say those three and you cover reliability.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**At-least-once vs exactly-once** — At-least-once + Redis dedup is the production default. Exactly-once needs Kafka transactions — slower and rarely worth it for notifications.

**Single topic vs per-channel topics** — Per-channel topics let you scale iOS workers independently from email workers and apply different retry policies.

**Push vs pull delivery** — Mobile notifications must be push (APNs/FCM). Pull polling drains battery and adds latency.

**Sync send vs async queue** — Sync couples user request latency to Twilio/APNs. Async queue is always correct for notifications.

> "At-least-once delivery with dedup, preferences checked before enqueue, and staggered fan-out for viral events."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Multi-channel routing and independent scaling
> [!CAUTION]
> **🔴 Weak** — one worker pool handles all channels
>
> [!WARNING]
> **🟡 Strong** — channel-specific workers because rate limits, payload formats, and failure modes differ. APNs uses device tokens and certificate auth; email uses SMTP/API with bounce handling; SMS has per-country regulations
>
> [!TIP]
> **🟢 Staff+** — partition Kafka by user_id hash for parallelism while keeping per-user ordering within a channel. Critical notifications use a dedicated high-priority topic with reserved worker capacity


#### Deep dive 2: Deduplication under at-least-once delivery
_Worker crashes after sending but before committing offset → message redelivered → duplicate notification. Redis SETNX on event_id before send. If key exists, skip. TTL = 24h covers replay window_

> [!CAUTION]
> **🔴 Weak** — Retry until delivery succeeds — duplicates are rare.
>
> [!WARNING]
> **🟡 Strong** — Worker crashes after sending but before committing offset → message redelivered → duplicate notification. Redis SETNX on event_id before send. If key exists, skip. TTL = 24h covers replay window
>
> [!TIP]
> **🟢 Staff+** — include idempotency key in provider request (FCM collapse_key) so the provider also deduplicates. Document contract: delivery is at-least-once; consumers must be idempotent


#### Deep dive 3: Viral fan-out and provider rate limits
_One event → 10M notifications in 60s. Kafka absorbs the write spike, but APNs/FCM rate-limit per certificate. Stagger enqueue over 60–120s. Monitor provider 429 responses and backoff globally. Batch similar notifications (5 new likes → one grouped push)_

> [!CAUTION]
> **🔴 Weak** — Push to every device synchronously from the API handler.
>
> [!WARNING]
> **🟡 Strong** — One event → 10M notifications in 60s. Kafka absorbs the write spike, but APNs/FCM rate-limit per certificate. Stagger enqueue over 60–120s. Monitor provider 429 responses and backoff globally. Batch similar notifications (5 new likes → one grouped push)
>
> [!TIP]
> **🟢 Staff+** — APNs coalesces offline notifications — only latest is delivered. For badge counts, send silent data push that triggers app to fetch true count from API


#### Deep dive 4: GDPR and user deletion
_Delete device tokens immediately. Publish user_deleted event. All workers check Redis blocklist before send. Kafka messages for deleted users cannot be erased — skip at dispatch. Document 72h purge SLA for compliance_

> [!CAUTION]
> **🔴 Weak** — Delete the user row — async workers will stop eventually.
>
> [!WARNING]
> **🟡 Strong** — Delete device tokens immediately. Publish user_deleted event. All workers check Redis blocklist before send. Kafka messages for deleted users cannot be erased — skip at dispatch. Document 72h purge SLA for compliance
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Three-problem script.

2. "Core features: deliver push, SMS, and email; respect opt-outs; retry transient failures; surface delivery status."

3. "Architecture: API → preference check → dedup → Kafka per channel → channel workers → APNs/FCM/Twilio/SendGrid."

4. "Dedup: SETNX event_id in Redis before every send. At-least-once from Kafka is fine if workers are idempotent."

5. "Preferences: check Redis opt-out cache before enqueue — never queue notifications users opted out of."

6. "Viral events: stagger fan-out, monitor provider rate limits, group low-priority notifications."

7. "Invalid tokens: remove immediately on provider error. Run daily feedback sweep for batch cleanup."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+------------------+
                    | Trigger sources  |
                    | txn · marketing  |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | Notification API |
                    | prefs · dedup    |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
        +-----------+  +-----------+  +-----------+
        | Kafka iOS |  |Kafka SMS  |  |Kafka Email|
        +-----+-----+  +-----+-----+  +-----+-----+
              |              |              |
              v              v              v
        +-----------+  +-----------+  +-----------+
        | iOS worker|  | SMS worker|  |Email worker|
        +-----+-----+  +-----+-----+  +-----+-----+
              |              |              |
              v              v              v
           APNs/FCM       Twilio        SendGrid
```

Interview version: API checks prefs and dedup, publishes to Kafka, workers call providers. Add DLQ and token cleanup if pushed on reliability.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-28)
