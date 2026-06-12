# WhatsApp — messaging

**Medium** · Persistent WebSocket · Pub/sub fan-out · Offline queue

Tags: `WebSocket`, `Kafka pub/sub`, `Cassandra`, `Stateful chat servers`, `Offline delivery`

_See also: v10 · real-time messaging patterns_

## Data flow

Stateful chat servers maintain WebSocket connections. A sends a message → server writes durably to Cassandra + creates Inbox entries → publishes to Kafka → B's server pushes via WebSocket. If B is offline the Inbox entry stays until B reconnects and acks.


> Servers stateful (WS) but app logic stateless  |  Snowflake ID for ordering  |  Group: fan-out to all member servers

## Architecture diagram

```
+----------------------+
                         |   Mobile and Web     |
                         |       Clients        |
                         +----------+-----------+
                                    |
                            WebSocket over TLS
                                    |
                         +----------v-----------+
                         |       L4 Load        |
                         |      Balancer        |
                         +----------+-----------+
                                    |
                 +------------------+------------------+
                 |                                     |
        +--------v--------+                   +--------v--------+
        |   Chat Server   |                   |   Chat Server   |
        |       A         |                   |       B         |
        |-----------------|                   |-----------------|
        | conn map        |                   | conn map        |
        | ack handling    |                   | ack handling    |
        | heartbeat       |                   | heartbeat       |
        | inbox sync      |                   | inbox sync      |
        +---+---------+---+                   +---+---------+---+
            |         |                           |         |
            |         +-----------+   +-----------+         |
            |                     |   |                     |
            |              +------v---v------+              |
            |              | Redis Pub Sub   |              |
            |              | user channels   |              |
            |              +------+----------+              |
            |                     |                         |
            |                     |                         |
   +--------v--------+   +--------v--------+      +--------v--------+
   |   Chat Table    |   | Message Table   |      |   Inbox Table   |
   | chat metadata   |   | durable msgs    |      | undelivered per |
   | by chatId       |   | by messageId    |      | user or client  |
   +-----------------+   +-----------------+      +-----------------+
            \\\\                  |                          /
             \\\\                 |                         /
              \\\\      +---------v---------+              /
               +----->| ChatParticipant   |<------------+
                      | chatId, userId    |
                      | + GSI by userId   |
                      +-------------------+


Attachment flow

Client --get upload target--> Chat Server
Client --upload directly-----> Blob Storage
Client --send message with attachment URL--> Chat Server
Recipient --download with signed URL-------> Blob Storage


Delivery flow
```

---

<details open>
<summary><strong>Problem</strong></summary>

Large-scale real-time chat. Let users send messages with very low delay, and still receive them later if offline.

Four concerns: group chats, fast delivery over persistent WebSocket, durable storage, and media sharing via blob storage.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Chat server goes down with 100K active connections**

100K users instantly disconnected. All in-flight messages buffered on that server are potentially lost.

_Fix:_ Client auto-reconnects within 5s (exponential backoff). Messages written to Cassandra before delivery attempt — durability is in DB, not server memory. On reconnect, server replays pending Inbox entries.

**Kafka consumer falls behind (delivery lag)**

Messages appear delayed. Real-time feel is broken.

_Fix:_ Monitor consumer lag per partition. Add more delivery server consumers. Kafka retention is long enough (7 days) to replay. Alert at >1s lag.

**Group chat with 1,000 members — one message fans out to 1,000 servers**

One message may require 1,000 individual Kafka publishes + 1,000 delivery confirmations. Multiplied by 100 messages/min = massive overhead.

_Fix:_ Group chat has dedicated server affinity — all members of a group are hashed to a small set of servers. Reduces fan-out from 1,000 to ~10. At extreme scale, group server is a separate chat cluster.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 2B users, 100M DAU, avg 50 messages sent/day, avg group size 20 |
| Read QPS | Connection management: 100M persistent WebSocket connections — ~3,000 chat servers at 30K connections each |
| Write QPS | 100M × 50 / 86400 ≈ 58,000 message writes/s to Cassandra |
| Storage | 58K messages/s × 1KB avg × 86400 × 365 ≈ 1.8 PB/year — partitioned by conversation_id + time |
| Cache math | Active conversations in memory: 100M DAU × 20 active conversations × 100 bytes ≈ 200 GB server RAM across fleet |
| Verdict | Connection count (100M WebSockets) is the primary scaling challenge. 3,000 chat servers required just for connection management. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Redis Pub/Sub vs. Kafka for cross-server delivery**

→ Redis Pub/Sub for real-time routing + Cassandra Inbox for durability

Kafka has too much latency (>100ms) for real-time message delivery. Redis Pub/Sub is best-effort sub-10ms. Durability comes from Cassandra Inbox — separate concern from delivery speed.

_Revisit when:_ Could use a dedicated messaging fabric (like Erlang's BEAM, which WhatsApp actually uses) for better connection density.

**End-to-end encryption key management**

→ Signal Protocol — keys stored on device only, server is blind

Server never sees plaintext. No key escrow. This is also a product differentiator. Architectural implication: server can't do server-side search or content moderation of message text.

_Revisit when:_ Backup encryption keys require a separate key backup protocol (WhatsApp uses HSM-backed cloud key backup).

**Message ordering within a conversation**

→ Lamport timestamps from sending client + Cassandra clustering key

Server-side monotonic counters require coordination. Client timestamps + sequence numbers per conversation provide good-enough ordering without server bottleneck.

_Revisit when:_ For strict ordering, use per-conversation sequence numbers issued by the server (adds a write per message for the sequence counter).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a user who is offline for 30 days?**

Cassandra Inbox stores messages durably. On reconnect, server queries Inbox for all undelivered messages, pages them to client. After client acks, remove from Inbox. Retention policy: keep Inbox entries for 30 days, then expire.

**How do you route a message when you don't know which server the recipient is on?**

Service discovery: Redis hash(user_id) → server_id mapping. Updated on connect/disconnect. Message router looks up server_id, publishes to that server's Redis pub/sub channel. If user offline, write to Cassandra Inbox directly.

**How do you scale to 10B users?**

Horizontal: more chat servers. The key insight is that users who never talk to each other are independent — shard by user_id cluster. Regional deployment (EU users mostly talk to EU users). The design is embarrassingly parallel across conversation clusters.

**How do you handle media (photos, videos)?**

Media is never sent through chat servers. Client uploads directly to blob storage (pre-signed URL). Sends message with media URL. Recipients download directly from CDN. Chat server only carries a tiny metadata message.

**What's your read receipt / delivery receipt design?**

Three states: Sent (written to Cassandra), Delivered (client received and ACKed via WebSocket), Read (client opened conversation). Client sends ACK events back to server. Server fans ACK to sender's device. Stored per message in Cassandra.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single server. HTTP polling every 5s. SQLite messages. Works for a demo, breaks at 1,000 users.

**v2 — Real-time** — WebSocket chat servers. Cassandra for messages. Redis Pub/Sub for cross-server delivery. Inbox for offline delivery. Handles 10M users.

**v3 — 2B users** — 100M persistent connections across 3,000 servers. Group chat server affinity. E2E encryption. Regional deployment. Dedicated media pipeline.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that WhatsApp mixes huge connection scale with strict delivery needs. You are not just storing chats. You are keeping hundreds of millions of long lived socket connections open, routing each message to the right server fast, and still making sure offline users eventually get every message.

There are three main scaling pain points. First, connection management is massive because every online user may hold one or more persistent connections, and those users are spread across many chat servers. Second, message routing gets tricky once sender and receiver are on different servers, so you need some way to bounce messages across the fleet without losing them. Third, delivery is both real time and durable. If Redis style pub sub drops a live message, the system still needs inbox storage, acks, reconnect logic, and sync to guarantee the user eventually gets it.

A fourth issue is fan out in group chats and multi device delivery. One message may need to reach many participants and several devices per participant, which multiplies writes and delivery work. So the simple interview answer is that WhatsApp is hard because it combines real time sockets, durable messaging, cross server routing, and offline sync all at once.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Mental model** — WebSocket for now, DB for later, blob storage for files.
- **Send path** — Write message durably first (Cassandra + Inbox), then try real-time delivery. Durability before speed.
- **Offline delivery** — Inbox entry persists until client acks. On reconnect, server replays pending messages.
- **Cross-server routing** — Redis Pub/Sub routes events to whichever server holds a user's connection. Best-effort — durability is in DB.
- **Attachments** — Pre-signed URL upload directly to blob storage. Server only stores the file reference.

> One sentence: Chat servers manage live WebSocket connections, Cassandra stores messages and inbox state, Redis Pub/Sub routes real-time events, blob storage handles media.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**WebSockets vs HTTP polling** — WebSockets are better for chat — low latency two-way. Tradeoff: harder to operate, many long-lived connections.

**Redis Pub/Sub vs Kafka for routing** — Redis is simpler and lighter for routing live events. Tradeoff: no delivery guarantee, so the DB-backed Inbox handles durability.

**At-least-once delivery vs exactly-once** — Exactly-once at messaging scale requires expensive coordination. At-least-once with client-side dedup (message_id) is the right tradeoff — simpler, faster, good enough.

**Per-device Inbox vs per-user Inbox** — Per-device inbox lets each device independently track delivery state and supports multi-device independently. Tradeoff: more rows, more complex fan-out on send.

> "Optimize for low-latency live delivery, but keep durability separate so failures in the real-time path don't lose messages."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Connection management and cross-server message routing at 100M concurrent WebSockets
> [!CAUTION]
> **🔴 Weak** — use a centralized message broker — every server publishes and subscribes to a shared queue
>
> [!WARNING]
> **🟡 Strong** — the core scaling pain is connection state: 100M online users each hold a persistent WebSocket to one of ~3,000 chat servers. A message from user A on server 1 to user B on server 3 must be routed without a central coordinator. Weak answer: Redis Pub/Sub. Strong answer: Redis Pub/Sub + per-user server affinity. Server affinity map: user_id → server_id stored in Redis hash (HSET). On connect: update map. On message send: look up recipient's server, publish to that server's channel. If recipient is offline: write to Cassandra Inbox directly
>
> [!TIP]
> **🟢 Staff+** — to name: what happens if the server holding a user's connection goes down? Client reconnects within 5 seconds (exponential backoff). Server affinity map is updated on reconnect. In-flight messages to the old server that weren't delivered: the Inbox ensures they're eventually delivered — real-time delivery is best-effort, durability is guaranteed by Cassandra. Never lose a message even when the delivery path fails


#### Deep dive 2: Message delivery guarantees — at-least-once with ack protocol
_WhatsApp's delivery promise: messages are never lost_

> [!CAUTION]
> **🔴 Weak** — write to DB, deliver, done
>
> [!WARNING]
> **🟡 Strong** — explicit ack protocol. When A sends a message: (1) server writes to Cassandra Inbox for B, (2) server attempts real-time delivery via WebSocket, (3) server returns delivery confirmation to A. B's client acks receipt: (4) client receives message, (5) sends ack to its chat server, (6) server marks Inbox entry as delivered, (7) server relays ack to A (double-checkmark). Read receipt: B opens the conversation, client sends read event, server relays to A (blue checkmark)
>
> [!TIP]
> **🟢 Staff+** — design: ack messages are small (just message_id + status) and can be batched. If B goes offline mid-conversation, Inbox stores pending messages. On reconnect: server queries Inbox WHERE user_id = B AND delivered = false, sends all pending, waits for acks before marking delivered. Message ordering: Cassandra clustering key on (conversation_id, created_at, message_id) gives total order within a conversation


#### Deep dive 3: Group chats — fan-out and multi-device delivery
> [!CAUTION]
> **🔴 Weak** — fan out one-by-one to all group members on every message. Naive approach: 1,000 individual deliveries per message. At 10 messages/min in an active group: 10,000 deliveries/min for one group
>
> [!WARNING]
> **🟡 Strong** — Weak answer: fan out one-by-one to all group members on every message. Naive approach: 1,000 individual deliveries per message. At 10 messages/min in an active group: 10,000 deliveries/min for one group
>
> [!TIP]
> **🟢 Staff+** — design: group chat server affinity — hash(group_id) to a dedicated set of chat servers. All group members' connections are preferentially routed to these servers. Fan-out from one message: server looks up group members, identifies which are connected to itself (direct push), which are on other servers in the affinity set (local pub/sub channel), which are offline (Cassandra Inbox). This reduces the fan-out from 1,000 individual cross-server calls to a broadcast within a small server cluster. Multi-device: Inbox is per-device, not per-user. Each device has its own Inbox entry and acks independently. Message is marked fully delivered only when all active devices have acked


_Why the deep dives connect to the scaling problem: "Huge connection scale, routing, durable messaging, and group fan-out." Each deep dive addresses one layer._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Durability-first script.

2. "Clarifying questions: are we designing 1:1 messaging only, or also group chats? And what's the delivery guarantee — best-effort or durable?"

3. "Good — both 1:1 and groups, durable delivery. Core features: send message, deliver in real-time when recipient is online, queue for offline delivery, read receipts. Out of scope: voice/video, payments."

4. "Scale: 2B users, 100M DAU, ~50 messages/day per active user = 58K message writes/sec. The hard constraint is 100M persistent WebSocket connections."

5. "Architecture: stateful chat servers hold WebSocket connections. Each server handles ~30K concurrent connections — need ~3,000 chat servers. This is a connection management problem as much as a messaging problem."

6. "Message routing: when A sends to B, A's chat server looks up B's server in Redis (user_id → server_id). Publishes to that server's Redis Pub/Sub channel. B's server pushes to B's WebSocket. If B is offline: write directly to Cassandra Inbox."

7. "Delivery guarantee: Cassandra Inbox is the durability layer. On reconnect, server queries Inbox for undelivered messages and replays them. Client ACKs each message. Real-time delivery is best-effort; Inbox guarantees nothing is lost."

8. "Key tradeoff: Redis Pub/Sub is fast but has no delivery guarantee. That's fine because Cassandra Inbox is the fallback. Never rely on the real-time path alone for delivery correctness."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                         |   Mobile and Web     |
                         |       Clients        |
                         +----------+-----------+
                                    |
                            WebSocket over TLS
                                    |
                         +----------v-----------+
                         |       L4 Load        |
                         |      Balancer        |
                         +----------+-----------+
                                    |
                 +------------------+------------------+
                 |                                     |
        +--------v--------+                   +--------v--------+
        |   Chat Server   |                   |   Chat Server   |
        |       A         |                   |       B         |
        |-----------------|                   |-----------------|
        | conn map        |                   | conn map        |
        | ack handling    |                   | ack handling    |
        | heartbeat       |                   | heartbeat       |
        | inbox sync      |                   | inbox sync      |
        +---+---------+---+                   +---+---------+---+
            |         |                           |         |
            |         +-----------+   +-----------+         |
            |                     |   |                     |
            |              +------v---v------+              |
            |              | Redis Pub Sub   |              |
            |              | user channels   |              |
            |              +------+----------+              |
            |                     |                         |
            |                     |                         |
   +--------v--------+   +--------v--------+      +--------v--------+
   |   Chat Table    |   | Message Table   |      |   Inbox Table   |
   | chat metadata   |   | durable msgs    |      | undelivered per |
   | by chatId       |   | by messageId    |      | user or client  |
   +-----------------+   +-----------------+      +-----------------+
            \\\\                  |                          /
             \\\\                 |                         /
              \\\\      +---------v---------+              /
               +----->| ChatParticipant   |<------------+
                      | chatId, userId    |
                      | + GSI by userId   |
                      +-------------------+


Attachment flow

Client --get upload target--> Chat Server
Client --upload directly-----> Blob Storage
Client --send message with attachment URL--> Chat Server
Recipient --download with signed URL-------> Blob Storage


Delivery flow
```


</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-5)
