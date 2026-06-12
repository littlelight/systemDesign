# Distributed key-value store

**Hard** · Consistent hashing · Quorum · Replication

Tags: `Consistent hashing`, `Virtual nodes`, `Quorum`, `Vector clock`, `Gossip`

## Data flow

Consistent hashing maps keys to nodes; adding a node remaps only K/n keys. Writes go to N replicas; reads use quorum (R+W>N). Vector clocks detect concurrent writes for conflict resolution.


> Consistent hash: add node remaps K/n keys  |  W+R>N for quorum read  |  Vector clock resolves conflicts

## Architecture diagram

```
Client -> Coordinator -> hash ring -> Replica nodes (N=3)
         W writes, R reads, gossip membership
```

Ring diagram + quorum numbers on whiteboard.


---

<details open>
<summary><strong>Problem</strong></summary>

Build Dynamo-style highly available KV store: partition data across nodes, replicate for durability, remain available during partitions.

Tradeoff: availability + partition tolerance vs strong consistency.

</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

KV store scales by adding nodes to the ring. Pain points: hot keys, quorum latency, conflict resolution complexity.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Consistent hashing + vnodes** — Even load distribution; minimal remapping on node add/remove.
- **Replication factor N=3** — Write to N nodes. Tolerate N-1 failures with proper quorum.
- **Quorum reads/writes** — W writes, R reads, R+W>N gives consistency guarantee.
- **Vector clocks** — Detect concurrent updates; client or system resolves siblings.
- **Sloppy quorum + hinted handoff** — Write to W healthy nodes even if primary down; hand off when node returns.
- **Anti-entropy** — Merkle tree sync repairs divergence between replicas.
- **CAP choice** — AP system — available during partition, eventual consistency.

> Consistent hash, quorum, vector clocks — Dynamo paper vocabulary.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Strong consistency vs eventual** — Quorum with R+W>N approaches strong for single-key ops. Full strong needs consensus (Paxos/Raft) per write — slower.

**Leader-based vs leaderless** — Leaderless (Dynamo/Cassandra) better availability. Leader (Redis primary) simpler consistency.

**Modulo vs consistent hash** — Modulo remaps all keys on resize — unacceptable at scale.

**LWW vs vector clocks** — Last-write-wins loses data on concurrent edits. Vector clocks preserve conflict history.

> "AP KV with quorum tuning — expose consistency/latency tradeoff via W and R."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Consistent hashing and virtual nodes
_Without vnodes, ring imbalance 3×. 150 vnodes per physical node smooths distribution. Node add/remove remaps only adjacent key ranges_

> [!CAUTION]
> **🔴 Weak** — Oversimplify consistent hashing and virtual nodes — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Without vnodes, ring imbalance 3×. 150 vnodes per physical node smooths distribution. Node add/remove remaps only adjacent key ranges
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 2: Quorum math
_N=3, W=2, R=2 → R+W>N guarantees read sees latest write. W=1, R=1 fastest but stale reads possible. Tune per use case_

> [!CAUTION]
> **🔴 Weak** — Oversimplify quorum math — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — N=3, W=2, R=2 → R+W>N guarantees read sees latest write. W=1, R=1 fastest but stale reads possible. Tune per use case
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Failure handling — hinted handoff and Merkle trees
_Node down: write to alternative node with hint. On recovery, hand off data. Background Merkle tree comparison finds drift_

> [!CAUTION]
> **🔴 Weak** — Oversimplify failure handling — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Node down: write to alternative node with hint. On recovery, hand off data. Background Merkle tree comparison finds drift
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Conflict resolution
_Concurrent puts create sibling versions. Client reads all siblings, merges (e.g., cart union), writes resolved version_

> [!CAUTION]
> **🔴 Weak** — Oversimplify conflict resolution — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Concurrent puts create sibling versions. Client reads all siblings, merges (e.g., cart union), writes resolved version
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Dynamo vocabulary script.

2. "Partition with consistent hashing. Replicate N=3."

3. "Quorum: W writes, R reads, R+W>N for consistency."

4. "Vector clocks detect concurrent writes."

5. "Hinted handoff maintains availability during node failure."

6. "Merkle trees for anti-entropy repair."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Client -> Coordinator -> hash ring -> Replica nodes (N=3)
         W writes, R reads, gossip membership
```

Ring diagram + quorum numbers on whiteboard.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-34)
