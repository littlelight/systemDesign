# Distributed cache (Redis-like)

**Hard** · Consistent hashing · Virtual nodes · LRU eviction

Tags: `Consistent hashing`, `Virtual nodes`, `LRU (DLL+HashMap)`, `Leader-follower`, `Gossip protocol`

_See also: v10 · Redis / caching foundations_

## Data flow

A consistent hash ring routes each key to its responsible node. Adding or removing a node remaps only K/n keys (modulo hashing remaps all). Virtual nodes (150+ per physical) ensure even load distribution. Each node uses a DLL + HashMap for O(1) LRU eviction.


> Consistent hash: add/remove node remaps K/n keys only (vs modulo = all keys)  |  Virtual nodes = even load

## Architecture diagram

```
+-------------------+
                           |      Clients      |
                           | app servers, APIs |
                           +---------+---------+
                                     |
                    get set delete   |
                                     v
                  +---------------------------------+
                  | Cache Client Library / SDK      |
                  | - consistent hash routing       |
                  | - connection pooling            |
                  | - write batching for hot writes |
                  | - hot key suffix logic          |
                  +-----------+---------------------+
                              |
              routes directly to owning shard node
                              |
      -------------------------------------------------------------
      |                 Distributed Cache Cluster                 |
      |                                                           |
      |   Shard A                Shard B                Shard C   |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary A |-------> | Replica A |         | Replica A2| |
      | | async repl|         | read copy |         | read copy | |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       | in memory per node                                |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map key -> node pointer  |                       |
      |   | Doubly linked list for LRU    |                       |
      |   | TTL expiry on entries         |                       |
      |   | Background cleanup process    |                       |
      |   +-------------------------------+                       |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary B |-------> | Replica B |-------> | Replica B2| |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map + LRU list + TTL     |                       |
      |   +-------------------------------+                       |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary C |-------> | Replica C |-------> | Replica C2| |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map + LRU list + TTL     |                       |
      |   +-------------------------------+                       |
      -------------------------------------------------------------

Hot read handling

   hot:key
      |
      +--> hot:key#1 on Shard A
      +--> hot:key#2 on Shard B
      +--> hot:key#3 on Shard C

Reads pick one copy to spread load.
Writes update all copies asynchronously.

Hot write handling

   counter:item42
      |
      +--> counter:item42:1 on Shard A
      +--> counter:item42:2 on Shard B
      +--> counter:item42:3 on Shard C

Writes are spread across suffixes.
Reads aggregate across shards.
```

The main mental model is this. Each cache node is just a fast in memory LRU cache, and the distributed part comes from sharding keys across many nodes with replication for availability. If you want, I can also give you a cleaner interview sized version that fits on one whiteboard.


---

<details open>
<summary><strong>Problem</strong></summary>

Building a distributed in-memory cache that supports GET/SET, handles node failure gracefully, evicts LRU items under memory pressure, and rebalances efficiently when nodes change.

</details>


<details>
<summary><strong>Failures</strong></summary>

**A cache node fails unexpectedly**

K/n keys are temporarily unavailable. Application falls through to primary DB — thundering herd if many keys expire simultaneously.

_Fix:_ Replication: each node has a primary and 1-2 replicas. On primary failure, replica promotes (Sentinel-managed election). Keys available from replica within ~30 seconds. DB protection: circuit breaker limits DB fallback throughput.

**Hot key: one cache key accessed 1M times/second**

Single node overwhelmed. Latency spikes for all keys on that node.

_Fix:_ Read replicas for hot keys (multiple copies). Local in-process L1 cache on each application server (100ms TTL, LRU eviction). Hot key detection: monitor access frequency per key, alert at > 10K QPS.

**Network partition: cache cluster splits into two halves**

Each partition continues serving reads from its subset of keys. Writes to either partition are invisible to the other. When partition heals, state diverges.

_Fix:_ During partition: serve reads from whichever partition the client reaches (availability over consistency). On partition heal: use last-write-wins (LWW) with vector clocks. Expired keys automatically resolve: TTL means stale data self-heals within the TTL window.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10K application servers, 1M QPS total cache traffic, 100 GB data set, 1 KB avg value size |
| Read QPS | 1M QPS across 10 cache nodes = 100K QPS/node. Redis handles 500K ops/s/node — fine. |
| Write QPS | Write ratio 20% = 200K writes/s across 10 nodes = 20K writes/node — fine. |
| Storage | 100 GB data / 10 nodes = 10 GB/node. Modern servers: 64-128 GB RAM. Plenty of headroom. |
| Cache math | Consistent hash: adding an 11th node moves 100GB/11 = 9GB of data — 9% of keys remapped. vs. modulo hash: 100% of keys remapped. This is the fundamental argument for consistent hashing. |
| Verdict | Hot key handling and failure mode (thundering herd on node failure) are the real challenges. Consistent hashing + virtual nodes is the foundation but not the whole answer. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Consistent hashing vs. modulo hashing for key distribution**

→ Consistent hashing with 150 virtual nodes per physical node

Modulo hashing: adding node N+1 remaps N/(N+1) = ~100% of keys. During remapping, all cache misses = thundering herd on DB. Consistent hashing: adding one node remaps K/N keys (~10%). 150 vNodes per physical node gives even distribution (without vNodes, distribution variance can be 3×).

_Revisit when:_ Rendezvous hashing as a simpler alternative with the same O(K/N) remapping guarantee.

**LRU vs. LFU eviction policy**

→ LRU as default, LFU available for frequency-stable workloads

LRU: O(1) with DLL + HashMap. Works well for temporal locality (recently used likely to be used again). LFU: better for stable hot/cold split but requires frequency counter per key (memory overhead) and doesn't handle cold-start well (new popular key starts with count=0).

_Revisit when:_ Redis 4.0+ implements LFU with approximation. Use LFU when access patterns are stable and working set is predictable.

**Persistence: RDB snapshots vs. AOF log vs. none**

→ RDB snapshots for cache-as-cache use case, AOF for cache-as-primary

If cache is purely a cache (DB is source of truth): RDB is fine. On failure, reload from DB. If cache is used as primary storage: AOF (append-only log) gives durability at the cost of 2× write throughput.

_Revisit when:_ For a distributed cache system design interview, always clarify: is cache a cache or a durable store? Different answer for each.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle cache stampede when a popular key expires?**

Three strategies: (1) Probabilistic early expiration: re-compute slightly before expiry to avoid thundering herd. (2) Mutex lock: first client to find expired key locks it, recomputes, others wait. (3) Stale-while-revalidate: serve stale value while async recompute runs. Option 3 is best for most use cases — users prefer slightly stale over waiting.

**What happens to in-flight writes during a node failure?**

With replication: write goes to primary, synchronously replicated to replica before ACK (strong consistency) OR async replicated (higher throughput, potential data loss on primary failure). The choice depends on durability requirement. For a cache, async replication is usually fine.

**How do you handle cache invalidation across multiple services?**

Tag-based invalidation: key tagged with entity_id. On entity update, invalidate all keys with that tag. Implementation: tag is a version counter in Redis. Key embeds version: user:123:v5. On update, increment version. Old keys become unreachable (and eventually evicted). No explicit invalidation broadcast needed.

**How would you implement a distributed lock on top of this cache?**

SETNX key value PX ttl_ms: atomic set-if-not-exists with TTL. Returns 1 (acquired) or 0 (already held). On success, caller holds lock for TTL duration. Release: verify key value (compare-and-delete via Lua script to avoid releasing someone else's lock). This is Redlock's basic building block.

**How do you do a zero-downtime node addition?**

(1) Add new node to consistent hash ring with virtual nodes. (2) New node starts receiving writes for its key range immediately. (3) Reads to new node: cache miss → fetch from DB → populate. No migration needed — cache re-warms naturally from read traffic. Keys on old nodes eventually evict. Zero disruption to application.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single node** — Single Redis instance. Modulo hash on client side for theoretical multi-key ops. Works until data > RAM or single node throughput limit.

**v2 — Distributed** — Consistent hashing with virtual nodes. Leader-follower replication per shard. Sentinel for automatic failover. Handles petabyte-scale datasets.

**v3 — Optimized** — Hot key detection and read replicas. LFU eviction for stable workloads. Tag-based invalidation. Client-side L1 cache for hottest keys. Redlock for distributed locking.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that a distributed cache stops being just a fast in memory map and becomes a coordination problem across many machines.

There are three big scaling pain points. First, you need to shard data across nodes so each key lands on the right machine, and adding or removing nodes should not force you to reshuffle almost everything. That is why consistent hashing matters. Second, you usually want high availability, which means replicas, and replicas create sync problems because reads can become stale and failover gets tricky. Third, hot keys break the nice even distribution. One popular key can overload a single shard even if the rest of the cluster is idle.

A fourth issue is that network cost starts to matter. On one machine, a hash lookup is tiny. In a distributed cache, every get and set may involve a network hop, connection management, and sometimes cross node coordination. So the short interview answer is that distributed cache is hard to scale because you need to keep latency low while handling sharding, replication, rebalancing, and hot spots at the same time.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: GET/SET with TTL, LRU eviction, consistent hashing across nodes, replication for HA. Out of scope unless asked: persistence to disk, Pub/Sub, Lua scripting, sorted sets.
- **Consistent hashing — non-negotiable** — Modulo hashing: adding one node remaps all K keys → simultaneous cache miss on every key → thundering herd → database crash. Consistent hashing remaps only K/N keys (~10%). This is not an optimization — it is required for safe topology changes.
- **Virtual nodes for even distribution** — Without vNodes, random ring positions give 3× variance in load across nodes. 150 vNodes per physical node: each node gets 150 ring segments. Adding a new node takes small slices from many existing nodes — balanced from day one.
- **LRU = DLL + HashMap, O(1) both** — Doubly-linked list for recency order (head = MRU, tail = LRU). HashMap for O(1) key → node lookup. get: HashMap lookup + move node to head. evict: remove tail node + delete from HashMap. Both O(1), no approximation needed.
- **Async replication for HA** — Primary + 1-2 replicas per shard. Async replication: lower write latency, potential loss of last few writes on primary failure. For a cache (data rebuildable from DB), this tradeoff is correct. Sync replication doubles write latency — wrong for cache.
- **Hot key handling** — One key accessed 1M/sec overloads one shard. Two layers: (1) local in-process L1 cache on app server (100ms TTL, LRU of top 100 keys — zero network), (2) read replicas for detected hot keys. Hot key detection: monitor per-key QPS, alert at >10K/sec.
- **Failure mode to name** — Node failure during topology change: consistent hashing means only K/N keys are affected. Those keys miss to the DB (thundering herd risk). Circuit breaker: limit DB fallback rate to DB's sustainable write rate. Replica promotes in <30s via Sentinel.

> Mental model: consistent hash for routing, virtual nodes for balance, DLL+HashMap for LRU, gossip for failure detection.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Consistent hashing vs modulo hashing** — Consistent hashing: add/remove node remaps K/N keys (~10%). Modulo: remaps all K keys — causes a thundering herd on the database during any topology change. Consistent hashing is non-negotiable at scale.

**LRU vs LFU eviction** — LRU evicts least recently used — good for temporal locality (recently used items likely needed again). LFU evicts least frequently used — better for stable hot/cold splits but poor cold-start behavior. LRU is the correct default.

**Async replication vs sync replication** — Sync replication: zero data loss, higher write latency (wait for replica ACK). Async: lower latency, potential loss of last few writes on primary failure. For a cache (data rebuildable from DB), async replication is the right tradeoff.

**Write-through vs cache-aside vs write-behind** — Cache-aside (app manages cache): most flexible, handles miss gracefully. Write-through (write to cache and DB together): simpler consistency but couples DB and cache latency. Write-behind (write to cache, async flush to DB): fastest writes, risk of data loss. Cache-aside is the standard interview default.

> "Consistent hashing: O(K/n) key remapping on node change. Modulo = O(K) = catastrophic at scale. This is the core insight."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Consistent hashing — O(K/N) remapping vs. O(K) for modulo
> [!CAUTION]
> **🔴 Weak** — use modulo hashing — key maps to node hash(key) % N. Simple. Adding node N+1 remaps N/(N+1) ≈ 100% of keys simultaneously. Every key misses, every miss hits the database, the database crashes
>
> [!WARNING]
> **🟡 Strong** — consistent hashing — place each node at one or more points on a 0→2^32 ring. A key maps to the first node clockwise from its hash. Adding a node: it takes over the key range between itself and its predecessor. Only K/N keys (~10%) are remapped
>
> [!TIP]
> **🟢 Staff+** — without virtual nodes, random ring positions create 3× variance in load across physical nodes — one node gets 30% of keys, another gets 5%. 150 virtual nodes per physical node (each node occupies 150 ring positions) gives uniform distribution. Adding a new node takes small slices from many existing nodes simultaneously, keeping load balanced from day one


#### Deep dive 2: LRU eviction — O(1) get and O(1) evict with DLL + HashMap
> [!CAUTION]
> **🔴 Weak** — on every get, scan all entries to find the least recently used one to evict. O(N) eviction — unacceptable at any meaningful cache size
>
> [!WARNING]
> **🟡 Strong** — doubly-linked list + HashMap. HashMap stores (key → DLL node) for O(1) lookup. DLL maintains recency order: head = most recently used, tail = least recently used. get: HashMap lookup O(1), move node to head O(1). evict: remove tail O(1), delete from HashMap O(1). put: insert at head O(1), evict tail if at capacity O(1)
>
> [!TIP]
> **🟢 Staff+** — implementation: the DLL needs sentinel head and tail nodes to eliminate edge cases (empty list, single element). On get, moving a node from its current position requires unlinking from prev/next and relinking at head — all O(1) pointer operations. LFU as an alternative: tracks access frequency, evicts least frequently used. Better for stable hot/cold workloads but has O(log N) update cost and poor cold-start behavior (new popular key starts at frequency 1, immediately evictable)


#### Deep dive 3: Replication and failure handling — availability without sacrificing latency
> [!CAUTION]
> **🔴 Weak** — replicate synchronously — every write waits for replica ACK before confirming to the client. Correct, but doubles write latency
>
> [!WARNING]
> **🟡 Strong** — async replication for a cache. Cache data is derived — it can be rebuilt from the source of truth (the DB). Losing the last few milliseconds of writes on primary failure is acceptable; the data is just re-fetched on the next miss. Primary + 1-2 replicas per shard, async replication, Sentinel-managed automatic failover in <30 seconds
>
> [!TIP]
> **🟢 Staff+** — hot key handling: one key accessed 1M times/sec overloads one shard regardless of replication. Two layers: (1) local in-process L1 cache on each app server — top 100 keys, 100ms TTL, zero network hops; (2) read replicas for detected hot keys. Hot key detection: monitor per-key access frequency, alert at >10K QPS. The L1 cache is the most effective lever — it eliminates the hot key problem entirely for the highest-traffic keys


_Why the deep dives connect to the scaling problem: "Coordination across machines with low latency." Deep dive 1 solves distribution. Deep dive 2 solves eviction. Deep dive 3 solves availability and hot spots._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Consistent-hashing-first script.

2. "Clarifying questions: is this a pure cache (data rebuildable from a source of truth) or a durable primary store? And what are our target operations — just GET/SET, or also sorted sets, pub/sub?"

3. "Good — pure cache, GET/SET with TTL. That shapes the consistency model: I can use async replication since data loss on failover is tolerable (we just re-fetch from DB)."

4. "The most important design decision — I'd start here: consistent hashing. Without it, adding or removing any node remaps all keys simultaneously. Every key misses. The database gets hit by the full load at once. That's a practical outage. Consistent hashing remaps only K/N keys."

5. "The ring: hash each node to a point on a 0–2^32 space. A key maps to the first node clockwise from its own hash. Adding a node: it takes over the keys between itself and its predecessor. 150 virtual nodes per physical node for even load distribution."

6. "LRU eviction: doubly-linked list for recency order plus a HashMap for O(1) lookup. get moves the node to the head. Evict removes from the tail. Both operations are O(1). No approximation needed."

7. "Replication: primary-replica per shard. Async replication — lower write latency, acceptable data loss since this is a cache. Sentinel manages automatic failover. Target RTO < 30 seconds."

8. "Hot key handling: I'd add local in-process L1 cache on each app server — top 100 keys, 100ms TTL. Eliminates network hops entirely for the hottest keys. Only cache misses reach Redis."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                           |      Clients      |
                           | app servers, APIs |
                           +---------+---------+
                                     |
                    get set delete   |
                                     v
                  +---------------------------------+
                  | Cache Client Library / SDK      |
                  | - consistent hash routing       |
                  | - connection pooling            |
                  | - write batching for hot writes |
                  | - hot key suffix logic          |
                  +-----------+---------------------+
                              |
              routes directly to owning shard node
                              |
      -------------------------------------------------------------
      |                 Distributed Cache Cluster                 |
      |                                                           |
      |   Shard A                Shard B                Shard C   |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary A |-------> | Replica A |         | Replica A2| |
      | | async repl|         | read copy |         | read copy | |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       | in memory per node                                |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map key -> node pointer  |                       |
      |   | Doubly linked list for LRU    |                       |
      |   | TTL expiry on entries         |                       |
      |   | Background cleanup process    |                       |
      |   +-------------------------------+                       |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary B |-------> | Replica B |-------> | Replica B2| |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map + LRU list + TTL     |                       |
      |   +-------------------------------+                       |
      |                                                           |
      | +-----------+         +-----------+         +-----------+ |
      | | Primary C |-------> | Replica C |-------> | Replica C2| |
      | +-----+-----+         +-----------+         +-----------+ |
      |       |                                                   |
      |       v                                                   |
      |   +-------------------------------+                       |
      |   | Hash map + LRU list + TTL     |                       |
      |   +-------------------------------+                       |
      -------------------------------------------------------------

Hot read handling

   hot:key
      |
      +--> hot:key#1 on Shard A
      +--> hot:key#2 on Shard B
      +--> hot:key#3 on Shard C

Reads pick one copy to spread load.
Writes update all copies asynchronously.

Hot write handling

   counter:item42
      |
      +--> counter:item42:1 on Shard A
      +--> counter:item42:2 on Shard B
      +--> counter:item42:3 on Shard C

Writes are spread across suffixes.
Reads aggregate across shards.
```

The main mental model is this. Each cache node is just a fast in memory LRU cache, and the distributed part comes from sharding keys across many nodes with replication for availability. If you want, I can also give you a cleaner interview sized version that fits on one whiteboard.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-21)
