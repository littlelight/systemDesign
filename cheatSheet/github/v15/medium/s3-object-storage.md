# S3 object storage

**Medium** · Consistent hashing · Metadata · Durability

Tags: `Consistent hashing`, `Metadata DB`, `Erasure coding`, `CDN`, `Multipart upload`

## Data flow

Clients call the API gateway. The metadata service maps bucket/key → data node locations via consistent hashing. Object bytes live on data nodes with replication (and erasure coding for cold tier). Large uploads use multipart with coordinator assembly.


> Metadata separate from bytes  |  Consistent hash ring for data nodes  |  11-nines via replication + erasure coding

## Architecture diagram

```
Client -> API -> Metadata DB (bucket/key -> node list)
              -> Data nodes (replicated chunks)
```

Metadata is the control plane; data nodes are the data plane.


---

<details open>
<summary><strong>Problem</strong></summary>

Design S3-like object storage: PUT/GET/DELETE objects, 11-nines durability, unlimited scale, presigned URLs.

Hard parts: metadata scale, rebalancing on node failure, and large object uploads.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Node failure during PUT**

Incomplete object visible.

_Fix:_ Write to temp key. Commit metadata only after all replicas ACK.

**Hot key (viral object)**

One object saturates single node.

_Fix:_ CDN in front. Replication already helps reads. Split hot objects across cache.

**Ring imbalance**

Some nodes 2× fuller than others.

_Fix:_ Virtual nodes. Background rebalancer moves ranges.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1T objects, 100K PUT/s peak, 1M GET/s peak, 1MB avg object |
| Read QPS | 1M GET/s — CDN serves 90% |
| Write QPS | 100K PUT/s × 3 replicas = 300K disk writes/s cluster-wide |
| Storage | 1T × 1MB = 1 EB logical — EC reduces physical |
| Cache math | Metadata: 1T keys × 500B = 500 TB metadata — shard buckets |
| Verdict | Metadata sharding and CDN are critical at this scale. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Replication vs erasure coding**

→ 3x replication hot, EC cold

Interviewers accept tiered durability.

_Revisit when:_ All EC if cost is the focus.

**Metadata store**

→ Cassandra partitioned by bucket

Horizontal scale for object index.

_Revisit when:_ FoundationDB/etcd for smaller scale.

**Strong consistency**

→ Quorum writes + leader for metadata

Read-after-write on new keys matters for clients.

_Revisit when:_ Eventual for cross-region async replication.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do presigned URLs work?**

HMAC(bucket, key, expiry, secret). Gateway validates signature before allowing PUT/GET.

**How do you delete objects at scale?**

Tombstone metadata. Async garbage collect bytes when ref count zero.

**How do you implement versioning?**

Version ID in metadata. DELETE marker for latest. List versions API.

**How do you handle concurrent writers?**

If-none-match / version checks. Last writer wins or reject conflict.

**How do you migrate a bucket between shards?**

Background copy with dual metadata. Cutover per key prefix.

**How do you monitor durability?**

Bit rot scrub. Replica lag. Missing replica alerts.

**How do you support cross-region replication?**

Async replicate bytes + metadata. CRR queue per object.

**How does this relate to Dropbox?**

Dropbox adds sync metadata + chunk dedup on top of object storage primitives.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single machine** — Disk + SQLite metadata.

**v2 — Hash ring** — Data nodes + metadata service + replication.

**v3 — S3-class** — Multipart, EC tiers, CDN, cross-region, lifecycle policies.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Petabyte payloads and metadata billions of keys — hashing and tiering dominate.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Metadata vs data separation** — Small metadata in SQL/Cassandra. Payload on commodity disks.
- **Consistent hashing** — Ring with virtual nodes. Add/remove nodes with minimal reshuffle.
- **Replication** — 3 replicas across racks/AZs. Quorum write before ACK.
- **Erasure coding** — Cold/archive tier: 10+4 EC reduces storage cost vs 3x replication.
- **Multipart upload** — Split >100MB into parts. Parallel upload. Commit manifest on complete.
- **Presigned URLs** — HMAC token lets client upload/download without proxying bytes through API.

> Metadata ring mapping, replicated data nodes, multipart for large objects.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**3x replication vs erasure coding** — Replication: faster reads, hotter tier. EC: cheaper for cold data.

**Strong listing consistency vs eventual** — S3 now strong for read-after-write on new objects. Listing can lag slightly.

**Central metadata vs per-bucket partition** — Partition metadata by bucket hash for scale.

> "Consistent hash for placement, metadata service for lookup, replication for durability."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Consistent hashing and rebalancing
_Virtual nodes smooth load. On node add: steal ranges. On failure: replicate to successor_

> [!CAUTION]
> **🔴 Weak** — Oversimplify consistent hashing and rebalancing — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Virtual nodes smooth load. On node add: steal ranges. On failure: replicate to successor
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Durability
_Sync replicate to 3 AZs before 200 OK on PUT. Background scrub detects bit rot_

> [!CAUTION]
> **🔴 Weak** — Oversimplify durability — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Sync replicate to 3 AZs before 200 OK on PUT. Background scrub detects bit rot
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Large objects
_Multipart with part ETags. Coordinator commits manifest atomically_

> [!CAUTION]
> **🔴 Weak** — Oversimplify large objects — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Multipart with part ETags. Coordinator commits manifest atomically
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Listing at scale
_Prefix index per bucket shard. Paginate with continuation tokens_

> [!CAUTION]
> **🔴 Weak** — Oversimplify listing at scale — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Prefix index per bucket shard. Paginate with continuation tokens
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Object store script.

2. "Separate metadata path from data path — never stream gigabytes through metadata DB."

3. "Consistent hash ring places objects. Three replicas across failure domains."

4. "Multipart for large uploads. Presigned URLs for direct client ↔ data node transfer."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Client -> API -> Metadata DB (bucket/key -> node list)
              -> Data nodes (replicated chunks)
```

Metadata is the control plane; data nodes are the data plane.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-38)
