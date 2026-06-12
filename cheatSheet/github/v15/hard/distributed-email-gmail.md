# Distributed email (Gmail)

**Hard** · Ingestion · Storage sharding · Search index

Tags: `SMTP`, `Cassandra`, `HDFS`, `MapReduce`, `Elasticsearch`, `Spam ML`

## Data flow

Inbound SMTP mail is parsed, scanned for spam, and written to a user shard (Cassandra). Large attachments go to blob storage. Elasticsearch indexes subject/body asynchronously for search. Reads hit the user shard directly — email is write-once, read-many per mailbox.


> Shard by user_id  |  Attachments in blob store  |  Search index async from mail log

## Architecture diagram

```
SMTP -> Ingest -> Spam -> Cassandra (user shard) -> API -> Client
                    -> Blob (attachments)
                    -> Kafka -> ES indexer
```

User shard is source of truth; search is derived.


---

<details open>
<summary><strong>Problem</strong></summary>

Design webmail at Gmail scale: receive, store, search, and send billions of emails with strong per-user consistency.

Hard parts: storage per user, full-text search, and reliable SMTP delivery.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Hot user shard (celebrity inbox)**

Millions of fan emails to one user.

_Fix:_ Rate limit per sender. Separate fan-mail bucket. Async fan-out to readers.

**Search index lag**

New mail not findable for minutes.

_Fix:_ Monitor indexer lag. Priority queue for recent mail.

**Attachment virus**

Malware stored in blob system.

_Fix:_ Scan before blob write. Block executable MIME types.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1B users, 100 emails/user/day avg, 50KB avg with attachments |
| Read QPS | Inbox read: 1B×20/86400 ≈ 230K/s |
| Write QPS | Ingest: 1B×100/86400 ≈ 1.16M/s |
| Storage | 100B emails/day × 50KB ≈ 4.5 PB/day raw — tiered storage + dedup |
| Cache math | Hot inbox cache per user in Redis |
| Verdict | Write sharding by user_id is the core decision. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Cassandra vs HDFS for mail bodies**

→ Cassandra hot + HDFS cold archive

Recent mail low-latency. Archive cheap on HDFS/Glacier.

_Revisit when:_ All Cassandra if simplifying interview.

**Global vs per-user search index**

→ Global ES with user_id filter

Simpler ops. Per-user index only for enterprise vaults.

_Revisit when:_ Per-user index at extreme privacy requirements.

**Strong consistency for inbox**

→ Quorum reads on user partition

User expects read-your-writes after send.

_Revisit when:_ Eventual for search index only.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle duplicate delivery (SMTP retry)?**

Message-ID dedup at ingestion. Store idempotency key 7 days.

**How do you implement labels/folders?**

Secondary index table: user_id + label → message_ids. Or bitmap per label.

**How do you support full mailbox search?**

ES query with user_id filter + highlighting. Fallback to metadata scan if index lag.

**How do you migrate a user between shards?**

Dual-write period. Background copy. Flip read route. Delete old after verify.

**How do you handle court-ordered retention?**

Legal hold flag bypasses user delete. Separate retention policy store.

**How do you scale SMTP ingress?**

Stateless SMTP proxies → Kafka → storage writers. Horizontal scale on proxies.

**How do you prevent outbound spam?**

Per-user send quotas. Reputation score. Delay suspicious bulk sends.

**How do you measure deliverability?**

Bounce rate, complaint rate, time-to-inbox, indexer lag.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Single DB** — Postgres per mail. Works to millions of messages.

**v2 — Sharded** — Cassandra user shards. Blob attachments. Async search.

**v3 — Gmail scale** — Tiered storage, ML spam, global ES, SMTP fleet, legal hold.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Billions of messages/day with large attachments — sharding and blob offload are mandatory.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Shard by user_id** — All of a user's mail on one shard — simplifies inbox listing and ACID per mailbox.
- **Blob for attachments** — Message metadata in Cassandra; attachment bytes in S3/HDFS.
- **Async search index** — Kafka mail event → ES indexer. Search slightly behind inbox — acceptable.
- **Spam at ingress** — Score before storage. Quarantine bucket for suspicious mail.
- **SMTP outbound queue** — Retry with exponential backoff. DKIM/SPF signing per domain.
- **Deletion/tombstones** — Soft delete + async purge from index and blob store.

> User-sharded storage, blob attachments, async search index.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**SQL vs Cassandra per user shard** — Cassandra: write-heavy, tunable consistency, horizontal scale. SQL: simpler but harder at Gmail scale.

**Sync vs async search index** — Async: faster ingest. Sync: instant search — costly at write volume.

**Push vs poll for new mail** — IMAP IDLE / WebSocket push for new mail notifications.

> "Shard mail by user, blob store attachments, async ES index, spam filter at SMTP ingress."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Storage model
> [!CAUTION]
> **🔴 Weak** — one row per email in SQL
>
> [!WARNING]
> **🟡 Strong** — Cassandra partition key = user_id, cluster key = timestamp+id. Staff+: separate hot (inbox) and cold (archive) tiers
>
> [!TIP]
> **🟢 Staff+** — Name the metric you'd alert on and when you'd revisit this design.


#### Deep dive 2: Search
_Inverted index per user or global with user_id filter. Reindex pipeline from mail log for recovery_

> [!CAUTION]
> **🔴 Weak** — Rebuild the full index nightly — no incremental updates.
>
> [!WARNING]
> **🟡 Strong** — Inverted index per user or global with user_id filter. Reindex pipeline from mail log for recovery
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: SMTP reliability
_Outbound queue in Kafka. Multiple MX retries. Bounce handling updates recipient reputation_

> [!CAUTION]
> **🔴 Weak** — Oversimplify smtp reliability — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Outbound queue in Kafka. Multiple MX retries. Bounce handling updates recipient reputation
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Spam/abuse
_Feature extraction at edge. ML model ensemble. User feedback loop for false positives_

> [!CAUTION]
> **🔴 Weak** — Query the database on every feed request.
>
> [!WARNING]
> **🟡 Strong** — Feature extraction at edge. ML model ensemble. User feedback loop for false positives
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Email platform script.

2. "Inbound SMTP → parse → spam scan → user shard. Attachments to blob store."

3. "Search: async indexer from mail events — inbox read path does not wait on ES."

4. "Outbound: queue + retry + DKIM. Per-user send rate limits."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
SMTP -> Ingest -> Spam -> Cassandra (user shard) -> API -> Client
                    -> Blob (attachments)
                    -> Kafka -> ES indexer
```

User shard is source of truth; search is derived.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-37)
