# Google Docs — collaborative editing

**Hard** · Operational transform · OT server · Convergence

Tags: `Operational Transform`, `CRDT (alternative)`, `WebSocket`, `PostgreSQL`

## Data flow

Every edit is an operation (insert/delete at position + document version). The OT server serializes concurrent ops from multiple clients and transforms conflicting ops so all clients converge. Clients apply ops optimistically for low latency.


> OT: transform(op_A, op_B) so all clients converge  |  CRDT alternative: no server, commutative ops

## Architecture diagram

```
+------------------+
                   |      Client      |
                   |  Web / Mobile    |
                   +---------+--------+
                             |
                    HTTP + WebSocket
                             |
                    +--------v--------+
                    |   API Gateway   |
                    +--------+--------+
                             |
            +----------------+----------------+
            |                                 |
            | POST /docs                      | WS /docs/{docId}
            |                                 |
   +--------v--------+               +--------v-------------------+
   | Document Meta   |               | Document Service           |
   | Service         |               | owns active doc sessions   |
   +--------+--------+               | runs OT transform          |
            |                        | tracks presence in memory  |
            |                        +----+-------------------+---+
            |                             |                   |
            |                             |                   |
   +--------v--------+                    |                   |
   | Postgres        |                    |                   |
   | Document MetaDB |                    |                   |
   | docId, title,   |                    |                   |
   | versionId       |                    |                   |
   +-----------------+                    |                   |
                                          |                   |
                              append ops  |                   | broadcast edits
                                          |                   | and cursors
                                  +-------v--------+          |
                                  | Document Ops   |<---------+
                                  | DB Cassandra   |
                                  | partition by   |
                                  | documentId     |
                                  +----------------+

In memory inside Document Service per active document

  documentId
    -> active websocket connections
    -> latest loaded operations or materialized doc state
    -> pending unacked edits
    -> cursor positions
    -> presence list
```

The core idea is simple. Document Meta Service creates documents and stores lightweight metadata. Document Service handles live collaboration, receives edit operations over WebSocket, applies Operational Transformation, writes durable ops to Cassandra, then pushes the transformed updates to every connected editor.

If you want the scaled version, add this around Document Service.
```
Clients
  |
  v
+------------------+
| Load Balancer    |
+--------+---------+
         |
         v
+-----------------------------------------------+
| Document Service Cluster                      |
|                                               |
|  +-----------+  +-----------+  +-----------+  |
|  | Doc Srv A |  | Doc Srv B |  | Doc Srv C |  |
|  +-----+-----+  +-----+-----+  +-----+-----+  |
|        \\\\            |              //        |
|         \\\\           |             //         |
|          +---------- v -----------+           |
|          | Consistent Hash Ring   |           |
|          | docId -> owning server |           |
|          +-----------+------------+           |
+----------------------+------------------------+
                       |
                       v
                +------+------+
                | ZooKeeper   |
                | ring config |
                +-------------+

Each docId maps to one owning Document Service.
All editors for the same document connect to the same server.
That keeps OT and fanout simple.
```
If you want, I can also give you a cleaner interview-ready version with just 6 boxes so it is easier to draw under time pressure.


---

<details open>
<summary><strong>Problem</strong></summary>

Real-time collaborative document editing where multiple users can type simultaneously and all see a consistent document.

The hard part: two users editing the same position concurrently — without OT, the document diverges.

</details>


<details>
<summary><strong>Failures</strong></summary>

**OT server is a single point of failure for an active document**

If the OT server crashes, all active editors lose their connection. In-flight operations are lost.

_Fix:_ OT server state is the op log, which is persisted to Cassandra/PG. On crash + reconnect, new OT server instance loads op log and reconstructs document state. Client buffered operations replay from last acknowledged version. Recovery < 10 seconds.

**Op log grows indefinitely for long-lived documents**

Loading a document that has 10 years of individual keystrokes takes forever. Document load time = time to replay entire op log.

_Fix:_ Periodic snapshot: every N operations (e.g., every 100 ops), write a full document snapshot. On load: fetch latest snapshot + only the ops since the snapshot. Bounded load time regardless of document age.

**Two editors on slow connections cause excessive conflicts**

Slow client sends ops based on stale document version. OT server must transform against many intervening ops. Transform logic gets complex and slow.

_Fix:_ Version vector on every op. OT server rejects ops too far behind (>100 ops stale) and asks client to resync. Client downloads current state and resumes. Better UX than silently corrupting the document.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 2B total docs, 100M DAU, avg 5 active docs per active user, avg 10 ops/minute when active |
| Read QPS | Presence/cursor updates: 100M × 5 docs × 1 cursor update/10s = 50M cursor updates/s across all docs — distributed across millions of OT server instances |
| Write QPS | 100M × 5 × 10 ops/60s ≈ 833K op/s — heavily partitioned by document (each doc is independent) |
| Storage | Op log: 833K ops/s × 50 bytes × 86400 ≈ 3.6 TB/day if all kept. Snapshots + recent ops: much smaller. Retain last 30 days ops + snapshot = feasible. |
| Cache math | Active document state in OT server memory: avg 10 KB × 500K concurrently active docs = 5 GB — fits in OT server fleet memory. |
| Verdict | Partitioned by document, not by user. Each document is an independent unit of work. Scaling is near-linear: more documents = more OT servers. Hot documents (1000 concurrent editors) need dedicated OT server instances. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**OT vs. CRDT for conflict resolution**

→ OT for Google Docs-style rich text

CRDT: commutative operations, eventual consistency without a central server. OT: strong consistency, server serializes operations. For rich text with complex formatting (bold spans, nested lists, tables), CRDT data structures are very complex and memory-heavy. OT is simpler to reason about for this content type.

_Revisit when:_ CRDTs work well for plain text (used by many collaborative note apps). For rich document structure, OT or CRDT with a merge function per content type.

**Document affinity: always route same doc to same OT server**

→ Consistent hash routing by document_id to OT server cluster

OT server maintains in-memory document state (current content + pending ops). If different operations for the same document land on different servers, they can't be serialized locally. Single server per document is required.

_Revisit when:_ Primary-secondary per document for HA — operations go to primary, secondary has replicated state for failover.

**Persistence: write-through vs. async write to op log**

→ Write-through: op log written to Cassandra before ACK to client

Document edits must not be lost. ACKing before persistence means a server crash loses the op. At 833K ops/s, Cassandra handles the write load (it's append-only, partitioned by doc_id).

_Revisit when:_ Async write with in-memory buffer (WAL pattern) for lower latency if Cassandra write latency is too high.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle 1,000 simultaneous editors on a viral document?**

OT server for that document is a hot spot. Assign a dedicated server instance (or small cluster with primary-secondary) for high-concurrent documents. Detect via Prometheus metric: concurrent_editors_per_doc > threshold. Auto-migrate document to dedicated instance. Other documents unaffected.

**How does undo work in a collaborative document?**

Undo is per-user: undo my last op, not the globally last op. OT keeps per-user op history. Undo generates an inverse op that's sent through the same OT pipeline — it gets transformed against all ops since the one being undone, then applied. Complex but correct.

**How do you handle offline editing (airplane mode)?**

Client buffers ops locally while offline. On reconnect: client sends all buffered ops with version numbers. Server replays them through OT against all ops that happened since disconnection. If conflict is unresolvable (e.g., document was deleted), server notifies client.

**How do you display cursor positions for other editors?**

Cursor positions are ephemeral — not persisted to op log. Sent via a separate low-latency ephemeral channel (WebSocket or WebRTC data channel). OT server broadcasts cursor updates to all active editors. Cursors use the same OT position transformation so they stay in the right place as others type.

**How do you implement suggestions / track changes mode?**

Suggestions are ops with a special metadata flag: proposed = true, author = user_id. They render with strikethrough/highlight styling but don't modify the canonical document state. Accepting a suggestion applies the op normally. Rejecting deletes the pending op. These are regular OT operations with additional metadata — no change to the core OT logic.

**OT vs CRDT — when would you switch?**

OT when you want server-authoritative rich text with simpler conflict semantics. CRDT (Yjs) when you need peer-to-peer or offline-first with higher per-character memory cost. State the tradeoff explicitly — interviewers expect it.

**How do you version documents for 'restore to Tuesday'?**

Snapshot + op log: find snapshot before target time, replay ops until timestamp. Never delete ops — snapshots are acceleration, log is source of truth.

**How do you scale comment threads without overloading OT server?**

Comments are overlay metadata keyed by (doc_id, anchor_position). Stored separately from text ops. OT server broadcasts comment events on same WebSocket channel but does not merge comments into document OT sequence.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single server, last-write-wins. Locks document while editing. Only one editor at a time. Breaks any collaborative use case.

**v2 — OT collaboration** — OT server per document. Op log in Cassandra. Snapshots every 100 ops. WebSocket delivery. Handles real-time collaboration for millions of documents.

**v3 — Enterprise scale** — Document affinity with consistent hashing. Hot document dedicated instances. Offline editing with op buffering. Comments as separate overlay system. Real-time presence and cursors.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Google Docs is concurrent editing on the same shared document. You are not just storing text. You are merging many tiny edits from different users, keeping everyone’s screen nearly in sync, and making sure the document is still correct after races and reconnects.

There are three main scaling pain points. First, consistency is tricky because two users can edit the same spot at the same time, so naive last write wins will lose data or corrupt positions. Second, the system is real time and stateful. Each active document has connected editors, cursor positions, and a stream of low latency updates, which is much harder than normal stateless HTTP traffic. Third, the hot spot is per document. Most docs are quiet, but one shared doc can suddenly have many active editors all sending and receiving updates at once, so you need to route everyone for that doc to the right place and recover cleanly if that server fails.

A fourth issue is storage shape. If you store every keystroke forever, loading a document gets slower and storage keeps growing, so you usually compact old edits into snapshots. The short interview answer is this. Google Docs is hard because it combines concurrent write correctness, real time fan out, and per document hotspot state in one system.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: real-time collaborative text editing with conflict resolution, persistent document storage, version history. Out of scope unless asked: comments, suggestions, offline mode, access control, spreadsheets.
- **OT server serializes all ops** — Every edit = (type, position, content, version). OT server transforms concurrent ops so all clients converge to the same document. transform(op_A, op_B) → op_A_prime that accounts for op_B having happened first.
- **Optimistic local application** — Client applies op immediately (low latency UX). When server sends the transformed op back, client reconciles. Users see their own keystrokes instantly — server confirms within ~50ms.
- **Op log + snapshots = bounded load time** — Every op is an immutable append to Cassandra. Snapshot every 100 ops. On document load: fetch latest snapshot + ops since snapshot. Load time is O(recent ops), not O(total history).
- **Document affinity — one OT server per doc** — All editors for a document must route to the same OT server (consistent hash by doc_id). OT cannot be distributed across servers for the same document without cross-server ordering — never shard one doc's ops.
- **CRDT as the alternative** — Commutative data structures — no central server needed. Better for offline-first. Higher per-character memory overhead. Yjs (YATA algorithm) is the production CRDT for rich text. OT is simpler for server-authoritative systems.
- **Failure mode to name** — OT server crashes mid-session: all clients disconnect. Warm standby (replicated op log from Cassandra) promotes in <10s. Clients reconnect, send buffered ops from last acknowledged version. Op log is always the source of truth.

> OT: requires central server. CRDT: no server needed but more memory. Both achieve convergence.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**OT vs CRDT** — OT requires a central server to serialize and transform ops — strong consistency. CRDT: commutative operations, no server needed, better for offline-first. OT is simpler for rich text; CRDT has higher per-character memory overhead.

**Snapshot + log vs log only** — Log-only is simpler but reconstructing a large document requires replaying thousands of ops — unbounded load time. Snapshot every N ops bounds reconstruction to snapshot + recent ops only, regardless of document age.

**Synchronous op log write vs async** — Write-through (ack after Cassandra write) guarantees no op is lost if server crashes. Async write (ack before Cassandra) is lower latency but can lose ops on crash. For a document editor, data loss is unacceptable — write-through.

**Single OT server per doc vs sharded** — Single OT server serializes all ops for a document — correct but single point of failure. Primary + warm standby with replicated op log gives HA. Sharding across multiple OT servers for one doc requires cross-shard ordering — avoid.

> "OT and CRDT both achieve convergence. OT requires a central server. CRDT doesn't but uses more memory. Google Docs uses OT."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Operational Transform — the concurrency correctness algorithm
> [!CAUTION]
> **🔴 Weak** — last-write-wins — the most recent save overwrites earlier concurrent edits. Data loss is guaranteed for any concurrent editing session
>
> [!WARNING]
> **🟡 Strong** — Operational Transform. Every edit is an operation with type, position, content, and the document version the client was on when they made the edit. The OT server serializes all operations: when concurrent ops arrive, it transforms each op against the ops that happened since the client's version
>
> [!TIP]
> **🟢 Staff+** — OT vs CRDT. OT requires a central server to serialize and transform — strong consistency, simpler for rich text. CRDT: commutative operations, no central server needed, better for offline-first. Yjs (YATA algorithm) is the production CRDT for rich text. OT is correct for server-authoritative systems; CRDT is correct for peer-to-peer. State this tradeoff explicitly — it's the question interviewers ask


#### Deep dive 2: Document state persistence — snapshot + op log for bounded load time
> [!CAUTION]
> **🔴 Weak** — store every op in Cassandra, replay all ops on document load. For a document with 1M keystrokes, load time = time to replay 1M ops — unbounded and growing forever
>
> [!WARNING]
> **🟡 Strong** — periodic snapshots. Every 100 ops: write a full document snapshot to PostgreSQL. On load: fetch latest snapshot + ops since the snapshot. Load time is O(recent ops), not O(total history)
>
> [!TIP]
> **🟢 Staff+** — implementation detail: the snapshot interval is a tunable config, not a code constant. Large documents (100-page reports) need more frequent snapshots; small documents can go longer. The snapshot is always derivable from the op log — it's a cache, never the source of truth. If a snapshot is corrupted, you can always reconstruct from the full op log


#### Deep dive 3: Scaling beyond one OT server — document affinity and hot document handling
> [!CAUTION]
> **🔴 Weak** — shard the OT server horizontally — split documents across multiple OT servers for throughput
>
> [!WARNING]
> **🟡 Strong** — document affinity is mandatory. All ops for a single document must go to one OT server — distributed sharding across multiple servers for the same document requires cross-shard op ordering, which is the same problem OT was designed to solve. Consistent hash by doc_id routes all editors to the same server
>
> [!TIP]
> **🟢 Staff+** — hot document handling: monitor concurrent_editors per document. When >50 concurrent editors: provision a dedicated OT server instance for that document, isolated from other docs. Primary + warm standby per hot document: standby replicates the op log from Cassandra and can promote in <10 seconds on primary failure. This gives hot document HA without sharding the OT logic


_Why the deep dives connect to the scaling problem: "Concurrent write correctness, real-time stateful system, per-document hot spot." Each deep dive addresses one layer._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. OT-first script.

2. "Clarifying questions: are we building a collaborative text editor — like Google Docs for plain documents — or also spreadsheets and presentations? And what's the scale — thousands of concurrent editors per document, or millions of documents with a few editors each?"

3. "Good — text documents, millions of documents with 1-10 concurrent editors typically, with occasional hot documents at 100+. Core features: real-time collaborative editing, persistent document storage, version history. Out of scope: comments, permissions, offline mode."

4. "The hard problem — and I'd lead with this: two users type simultaneously at the same position. Without coordination, their changes conflict and the document diverges on each client."

5. "Solution: Operational Transform. Every edit is an operation: type (insert or delete), position, content, and the document version the client was on when they made the edit."

6. "The OT server serializes all operations. When two concurrent ops arrive — say A inserts at position 5 and B deletes at position 3 — the server transforms A's op against B's to compute where A's insert should actually land given that B's delete happened first. It then broadcasts the transformed op to all clients."

7. "Clients apply their own ops immediately (optimistic local application) for low-latency UX. When the server sends the transformed op back, the client reconciles — typically within 50ms."

8. "Persistence: every op is an immutable append to Cassandra, keyed by (doc_id, version). Snapshot every 100 ops — on document load, fetch latest snapshot plus ops since. Load time is O(recent ops), not O(total history)."

9. "Document affinity: all editors for the same document must route to the same OT server. I'd use consistent hash by doc_id. For hot documents — a company all-hands doc — assign a dedicated OT server instance."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+------------------+
                   |      Client      |
                   |  Web / Mobile    |
                   +---------+--------+
                             |
                    HTTP + WebSocket
                             |
                    +--------v--------+
                    |   API Gateway   |
                    +--------+--------+
                             |
            +----------------+----------------+
            |                                 |
            | POST /docs                      | WS /docs/{docId}
            |                                 |
   +--------v--------+               +--------v-------------------+
   | Document Meta   |               | Document Service           |
   | Service         |               | owns active doc sessions   |
   +--------+--------+               | runs OT transform          |
            |                        | tracks presence in memory  |
            |                        +----+-------------------+---+
            |                             |                   |
            |                             |                   |
   +--------v--------+                    |                   |
   | Postgres        |                    |                   |
   | Document MetaDB |                    |                   |
   | docId, title,   |                    |                   |
   | versionId       |                    |                   |
   +-----------------+                    |                   |
                                          |                   |
                              append ops  |                   | broadcast edits
                                          |                   | and cursors
                                  +-------v--------+          |
                                  | Document Ops   |<---------+
                                  | DB Cassandra   |
                                  | partition by   |
                                  | documentId     |
                                  +----------------+

In memory inside Document Service per active document

  documentId
    -> active websocket connections
    -> latest loaded operations or materialized doc state
    -> pending unacked edits
    -> cursor positions
    -> presence list
```

The core idea is simple. Document Meta Service creates documents and stores lightweight metadata. Document Service handles live collaboration, receives edit operations over WebSocket, applies Operational Transformation, writes durable ops to Cassandra, then pushes the transformed updates to every connected editor.

If you want the scaled version, add this around Document Service.
```
Clients
  |
  v
+------------------+
| Load Balancer    |
+--------+---------+
         |
         v
+-----------------------------------------------+
| Document Service Cluster                      |
|                                               |
|  +-----------+  +-----------+  +-----------+  |
|  | Doc Srv A |  | Doc Srv B |  | Doc Srv C |  |
|  +-----+-----+  +-----+-----+  +-----+-----+  |
|        \\\\            |              //        |
|         \\\\           |             //         |
|          +---------- v -----------+           |
|          | Consistent Hash Ring   |           |
|          | docId -> owning server |           |
|          +-----------+------------+           |
+----------------------+------------------------+
                       |
                       v
                +------+------+
                | ZooKeeper   |
                | ring config |
                +-------------+

Each docId maps to one owning Document Service.
All editors for the same document connect to the same server.
That keeps OT and fanout simple.
```
If you want, I can also give you a cleaner interview-ready version with just 6 boxes so it is easier to draw under time pressure.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-20)
