# Dropbox — file storage

**Easy** · Large blobs · Chunking · Delta sync

Tags: `S3`, `CDN`, `PostgreSQL`, `SHA-256 dedup`, `Pre-signed URLs`, `Delta sync`

## Data flow

The client splits a file into 4 MB chunks and computes a SHA-256 hash per chunk. It sends the hash list to the Metadata API, which checks which hashes are already stored. Only missing chunks are uploaded — directly to S3 via a pre-signed URL. The Metadata API writes the file-to-chunk mapping to PostgreSQL. Downloads flow through a CDN.


> Client uploads only NEW chunks  |  SHA-256 = global dedup  |  Delta sync = only changed chunks

## Architecture diagram

```
+----------------------+
                        |   Desktop / Mobile   |
                        |   Web Client + Sync  |
                        |   Agent              |
                        +----------+-----------+
                                   |
                      auth, metadata APIs, change feed
                                   |
                                   v
                        +----------------------+
                        |   LB / API Gateway   |
                        +----------+-----------+
                                   |
                                   v
                        +----------------------+
                        |     File Service     |
                        | - authz checks       |
                        | - file metadata      |
                        | - share management   |
                        | - presigned URLs     |
                        | - signed CDN URLs    |
                        +----+------------+----+
                             |            |
                metadata rw  |            | change events
                             v            v
                  +----------------+   +------------------+
                  | FileMetadataDB |   | Notification /   |
                  | - files        |   | Change Service   |
                  | - sharedFiles  |   | - WebSocket/SSE  |
                  | - upload state |   +--------+---------+
                  +----------------+            |
                                                |
                                   push updates |
                                                v
                                        +---------------+
                                        | Client devices|
                                        +---------------+

Upload path
-----------
Client -> File Service -> get presigned upload URL
Client -------------------------------> Blob Storage / S3
                                         |
                                         | upload complete event
                                         v
                                   File Service updates DB

Download path
-------------
Client -> File Service -> auth check + signed CDN URL
Client -------------------------------> CDN
                                          |
                                   cache miss|
                                          v
                                      Blob Storage / S3

Sharing path
------------
Client -> File Service -> update share records in DB

Sync path
---------
Local file change -> Sync Agent -> upload flow
Remote file change -> Notification Service pushes event
Missed event fallback -> Client polls `GET /files/changes?since=...`
```

The main mental model is this. Your app server is the control plane, not the data plane. It decides who can access a file and issues signed URLs, but the heavy file bytes go directly between the client, blob storage, and CDN.

If you want, I can also give you a more interview ready version that is smaller and faster to draw on a whiteboard.


---

<details open>
<summary><strong>Problem</strong></summary>

Dropbox solves cloud file storage and sync. Let users upload a file once, store it reliably, access it from any device, share it with others, and keep copies in sync.

Two layers: durable blob storage for the actual file bytes, and metadata + change tracking for ownership, sharing, and sync.

</details>


<details>
<summary><strong>Failures</strong></summary>

**S3 upload fails mid-way**

Large file partially uploaded. Client shows error. Resuming from scratch is expensive for GB-sized files.

_Fix:_ TUS or S3 multipart upload — track uploaded chunks, resume from last successful part. Store upload state in DB.

**Sync notification missed (device offline)**

Device misses a change event, shows stale file version.

_Fix:_ Don't rely on push alone. On reconnect, device sends its last-known state vector. Server diffs and sends all missed changes. Pull-on-reconnect as fallback to push.

**Metadata DB goes down**

Can't create, read, or share files. Uploads are blocked (can't get pre-signed URL without metadata record).

_Fix:_ PG primary + synchronous standby with automatic failover (Patroni). RTO < 30s. In-flight uploads continue directly to S3.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 50M DAU, avg 2 uploads/day at avg 5 MB, 100 downloads/day at avg 3 MB |
| Read QPS | 50M × 100 / 86400 ≈ 58K download QPS — nearly all served by CDN |
| Write QPS | 50M × 2 / 86400 ≈ 1,160 upload QPS — direct to S3 |
| Storage | 50M users × 15 GB avg free tier ≈ 750 PB — multi-region S3, lifecycle to cold tier after 90 days |
| Cache math | Metadata per file ≈ 200 bytes × 10B files = 2 TB metadata DB — needs sharding by user_id |
| Verdict | Storage cost dominates. Dedup is critical — even 20% dedup rate saves 150 PB. CDN absorbs download bandwidth. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Chunk size**

→ 4 MB chunks

Balances parallelism (multiple chunks in-flight), dedup granularity, and overhead. Smaller chunks = better dedup but more metadata. Larger = less overhead but worse dedup and resume granularity.

_Revisit when:_ Variable chunk sizing (content-defined chunking) for better dedup on large binary files.

**Direct S3 upload vs. proxying through app servers**

→ Direct S3 via pre-signed URL

Proxying 5 MB files through app servers at 1,160 QPS = ~5.8 GB/s through app tier. Completely unnecessary. Pre-signed URLs give the same security without the load.

_Revisit when:_ Never revisit — proxying is always wrong for large binary objects.

**Per-chunk dedup vs. per-file dedup**

→ Per-chunk (SHA-256)

Per-file dedup only helps for exact duplicates. Per-chunk helps when large files share common sections (e.g., only the last section of a document changed).

_Revisit when:_ Content-defined chunking (CDC) gives better dedup ratios for structured files.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle conflicts when two devices edit the same file offline?**

Vector clocks or last-write-wins per chunk. On conflict: create a conflict copy (like Dropbox does), notify user to resolve. Don't silently overwrite — data loss is worse than a conflict notification.

**How do you handle a user with 1TB of files?**

Same architecture — chunking means we never load the whole file. The chunk manifest for a 1TB file is just a longer list of hashes. The expensive part is the initial upload bandwidth, not the server architecture.

**How would you implement sharing with permissions?**

SharedFiles table: (file_id, shared_with_user_id, permission_level, created_at). Check on every file operation. At scale, cache permission checks in Redis (short TTL, invalidate on permission change).

**What if the same file is uploaded by 1M users?**

SHA-256 dedup: stored once, shared metadata. Only first upload writes to S3. All subsequent uploads just create a new metadata row pointing to the same chunk hashes. True content-addressable storage.

**How do you handle versioning?**

Each save creates a new FileVersion row with a pointer to chunk manifest. Keep last N versions (configurable). Diff between versions = diff of chunk lists — show which chunks changed without re-downloading the file.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: success rate, latency, active users. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: minutes of read unavailability acceptable; rebuild cache from DB. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Direct upload to single S3 bucket. PG for metadata. No chunking, no dedup. Pre-signed URLs for access. Handles early adopters with small files.

**v2 — Scale** — Chunking + SHA-256 dedup. CDN for downloads. Sync notifications via WebSocket. Versioning. PG sharded by user_id. Handles millions of users.

**v3 — Optimize** — Content-defined chunking for better dedup. Cold storage tier for old chunks (S3 Glacier). Delta sync — send only changed bytes within a chunk. Background dedup job reconciles orphaned chunks.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is moving and syncing very large files cheaply and reliably. In Dropbox, the bottleneck is not just request count. It is the sheer amount of data, long upload times, and the fact that users expect the same file to appear quickly across devices.

A few things make this hard. Large files can time out, fail halfway, and need resume support. Downloads are heavy too, especially for users far from your storage region, so you need direct blob storage access and often a CDN. Sync is also tricky because you need to notice file changes, push updates to other devices, and recover if a device misses a notification. On top of that, sharing and permissions add metadata lookups, while reliability means you need durable storage and recovery if a server dies. So the core scaling pain is big blobs plus cross-device coordination, not just more API servers.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Metadata through your service** — Control plane: auth, metadata, sharing, URL signing. Heavy file bytes go around your service, not through it.
- **Blob storage** — Store actual file contents in S3. Never proxy large files through app servers.
- **Pre-signed URL upload** — Client uploads directly to blob storage. Bypasses your servers entirely.
- **CDN download** — Signed CDN URL for downloads. Low latency for users far away.
- **Chunking** — Split large files into 4 MB chunks on the client. Gives progress, retry, and resume.
- **SHA-256 dedup** — Same hash = same content = already stored. Global dedup across all users for free.
- **Delta sync** — On update, only upload changed chunks. This is what makes sync feel fast on large files.
- **Sharing** — Separate SharedFiles table — makes "files shared with me" queries fast.

> One interview sentence: Dropbox is a metadata service plus blob storage plus CDN, with direct upload, direct download, and a sync agent for change tracking.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Upload through app vs direct to blob** — Direct is cheaper and faster. Tradeoff: more coordination around upload state and failures.

**Download from origin vs CDN** — CDN is better for low-latency global reads. Tradeoff: cost and cache invalidation.

**Sharelist in file vs separate table** — Separate table makes "files shared with me" fast. Tradeoff: another write path.

**Push sync vs polling** — Push gives near real-time updates but harder to run reliably. Polling is the fallback.

> "I chose the more scalable design for file transfer and reads, accepting extra complexity for better performance."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Large file uploads — chunking, resumability, and deduplication
_The scaling pain is moving and syncing large files cheaply and reliably_

> [!CAUTION]
> **🔴 Weak** — upload file to S3, done
>
> [!WARNING]
> **🟡 Strong** — client-side chunking (4 MB chunks) with SHA-256 fingerprint per chunk. Before uploading, client sends hash list to server — server responds with which hashes are new. Only missing chunks are transferred. This is content-addressable storage: same chunk stored once globally
>
> [!TIP]
> **🟢 Staff+** — chunk size is a design decision with real tradeoffs — smaller chunks give better dedup granularity and retry precision but more metadata overhead and round-trips; larger chunks have less overhead but worse dedup and waste bandwidth if a chunk fails mid-upload. Content-defined chunking (CDC using rolling hash, e.g., Rabin fingerprint) gives better dedup ratios for structured files (docs, code) by finding natural chunk boundaries rather than fixed offsets. The upload flow must be resumable: store upload state per (file_id, chunk_offset) so a failed upload resumes from the last committed chunk, not from scratch


#### Deep dive 2: Sync across devices — reliability and conflict resolution
_The scaling pain is cross-device coordination — detecting changes, pushing updates, and recovering from missed notifications_

> [!CAUTION]
> **🔴 Weak** — polling
>
> [!WARNING]
> **🟡 Strong** — WebSocket or SSE push for change notifications, with polling as a fallback for reconnect. The hard problem is what happens when two devices edit the same file while one is offline. Dropbox's actual approach: last-write-wins per file, conflicts result in a conflict copy (two files) rather than silent data loss
>
> [!TIP]
> **🟢 Staff+** — vector clocks or file version numbers allow the server to detect when a client's local state diverges from the server's state, enabling explicit conflict presentation rather than silent overwrites. The sync state machine per device: (synced → local_change_pending → uploading → synced) and (synced → remote_change_available → downloading → synced). On reconnect, client sends its last-known state vector; server diffs and returns the minimal change set


#### Deep dive 3: Security, sharing, and performance
> [!CAUTION]
> **🔴 Weak** — store the file URL in a shared link, serve directly from S3 with a public ACL
>
> [!WARNING]
> **🟡 Strong** — sharing model: SharedFiles table (file_id, shared_with_user_id, permission, created_at)
>
> [!TIP]
> **🟢 Staff+** — permission check on every file operation must be fast — cache permission results in Redis with short TTL, invalidate on any permission change. For large organizations: hierarchical permissions (team → folder → file) require careful data modeling to avoid O(n) permission lookups. Security: pre-signed S3 URLs with short expiry (15 min) for downloads — URL cannot be reused after expiry, prevents link sharing beyond the intended recipient. For compliance: server-side encryption (SSE-S3 or SSE-KMS), access logs for auditing, retention policies. Performance: CDN in front of S3 with signed cookies for authenticated users — reduces S3 egress costs and improves global latency


_Why the deep dives connect to the scaling problem: "Big blobs plus cross-device coordination." Deep dive 1 solves the blob problem (chunking + dedup). Deep dive 2 solves coordination (sync state machine + conflict handling). Deep dive 3 solves correctness and performance at scale._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Use a simple script: upload, download, sharing, sync, then one deep dive.

2. "I'll design Dropbox as a cloud file storage and sync system. Core requirements: upload, download, sharing, and automatic sync. I'll prioritize availability over strict consistency."

3. "Key insight: metadata goes through my service, file bytes go around it. The File Service is the control plane — auth, permissions, metadata, and signed URLs."

4. "For uploads, the client asks the File Service for a presigned upload URL. Client uploads directly to blob storage. After upload, storage notifies the backend."

5. "For downloads, the service returns a signed CDN URL after auth checks. Client downloads from CDN, which fetches from blob storage on cache miss."

6. "For sync, each device runs a sync agent. Local changes trigger uploads. Remote changes come via WebSocket push with polling as fallback."

7. "The main tradeoff: sending files through the app server is simpler but direct upload scales much better."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                        |   Desktop / Mobile   |
                        |   Web Client + Sync  |
                        |   Agent              |
                        +----------+-----------+
                                   |
                      auth, metadata APIs, change feed
                                   |
                                   v
                        +----------------------+
                        |   LB / API Gateway   |
                        +----------+-----------+
                                   |
                                   v
                        +----------------------+
                        |     File Service     |
                        | - authz checks       |
                        | - file metadata      |
                        | - share management   |
                        | - presigned URLs     |
                        | - signed CDN URLs    |
                        +----+------------+----+
                             |            |
                metadata rw  |            | change events
                             v            v
                  +----------------+   +------------------+
                  | FileMetadataDB |   | Notification /   |
                  | - files        |   | Change Service   |
                  | - sharedFiles  |   | - WebSocket/SSE  |
                  | - upload state |   +--------+---------+
                  +----------------+            |
                                                |
                                   push updates |
                                                v
                                        +---------------+
                                        | Client devices|
                                        +---------------+

Upload path
-----------
Client -> File Service -> get presigned upload URL
Client -------------------------------> Blob Storage / S3
                                         |
                                         | upload complete event
                                         v
                                   File Service updates DB

Download path
-------------
Client -> File Service -> auth check + signed CDN URL
Client -------------------------------> CDN
                                          |
                                   cache miss|
                                          v
                                      Blob Storage / S3

Sharing path
------------
Client -> File Service -> update share records in DB

Sync path
---------
Local file change -> Sync Agent -> upload flow
Remote file change -> Notification Service pushes event
Missed event fallback -> Client polls `GET /files/changes?since=...`
```

The main mental model is this. Your app server is the control plane, not the data plane. It decides who can access a file and issues signed URLs, but the heavy file bytes go directly between the client, blob storage, and CDN.

If you want, I can also give you a more interview ready version that is smaller and faster to draw on a whiteboard.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-1)
