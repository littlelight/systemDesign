# FB Post Search

**Medium** · Inverted index · Privacy filtering · Near-real-time index

Tags: `Elasticsearch`, `Kafka`, `Cassandra`, `Post-query privacy filter`

## Data flow

Posts → Kafka → async index worker tokenizes and writes to Elasticsearch with a privacy_level field. On search, ES handles text matching. A post-query app layer filter enforces friend-list visibility — friend lists are too large and dynamic for ES.


> Privacy filter in app layer (not ES) — friend lists too large and dynamic  |  Rank = relevance × recency

## Architecture diagram

```
+-------------------+
User Search Request -----> |   CDN / Edge      |
                           +---------+---------+
                                     |
                                     v
                           +-------------------+
                           |   API Gateway     |
                           | auth rate limit   |
                           +---------+---------+
                                     |
                                     v
                           +-------------------+
                           |   Search Service   |
                           +----+----------+---+
                                |          |
                    cache hit?  |          | fetch posts and fresh likes
                                |          v
                                |   +--------------+
                                |   | Post Service |
                                |   +--------------+
                                |   +--------------+
                                |   | Like Service |
                                |   +--------------+
                                v
                       +-----------------------+
                       | Distributed Search    |
                       | Cache TTL < 1 minute  |
                       +-----------+-----------+
                                   |
                              cache miss
                                   |
                                   v
                +-------------------------------------------+
                | Keyword Index Store                       |
                | sharded by keyword                        |
                |                                           |
                | creation index = list by recency          |
                | likes index = sorted set by like score    |
                +-------------------+-----------------------+
                                    |
                     hot keywords   |   cold keywords
                                    | 
                    +---------------+---------------+
                    |                               |
                    v                               v
             +-------------+                 +-------------+
             | Redis shard |                 | Blob store  |
             | in memory   |                 | S3 or R2    |
             +-------------+                 +-------------+


Write path
==========

Post Create ----> Post Service ----+
                                   |
Like Event -----> Like Service ----+----> Kafka or event log ----> Ingestion workers
                                                                     |
                                                                     v
                                                          +----------------------+
                                                          | Tokenizer            |
                                                          | split into keywords  |
                                                          | optional bigrams     |
                                                          +----------+-----------+
                                                                     |
                                            +------------------------+----------------------+
                                            |                                               |
                                            v                                               v
                               +--------------------------+                    +--------------------------+
                               | Update creation indexes  |                    | Update likes indexes     |
                               | add postId per keyword   |                    | sorted set score updates |
                               +--------------------------+                    +--------------------------+
                                                                                          |
                                                                                          v
                                                                         +------------------------------+
                                                                         | Optional like batcher or     |
                                                                         | approximate milestone writer |
                                                                         +------------------------------+
```

The mental model is two pipelines. One pipeline builds keyword indexes from posts and likes. The other pipeline serves search by reading those indexes fast, usually from cache or Redis.

If you were drawing this in an interview, I would start with just User, API Gateway, Search Service, Ingestion Service, and Index Store. Then add cache, Kafka, like batching, and cold storage only if the interviewer pushes on scale or freshness trade-offs.


---

<details open>
<summary><strong>Problem</strong></summary>

Keyword search over billions of posts at Facebook scale.

You can't scan raw post text at request time — the corpus is enormous. The real problem is precomputing an inverted index and keeping it fresh.

</details>


<details>
<summary><strong>Failures</strong></summary>

**ES index lag during a viral event**

Posts about breaking news don't appear in search for 5+ minutes. Users see stale results.

_Fix:_ Dedicated fast-lane Kafka topic for high-engagement posts (ML classifier identifies potential viral content within seconds of publish). Fast-lane ES indexer with smaller batch size (<1s latency). Normal posts use standard pipeline.

**Like-count index update storms a hot term**

Popular keyword has millions of posts. One viral post gets 1M likes in an hour → 1M index updates to the same ES shard.

_Fix:_ Batch like updates: count likes in Redis, flush to ES every 5 minutes. Like-sorted index has approximate freshness — acceptable. Don't update ES on every individual like.

**Privacy filter in app layer creates N+1 query problem**

Search returns 100 results. App layer checks each post's privacy settings + friend graph. 100 DB queries per search request.

_Fix:_ Embed privacy_level in ES document. App layer only needs friend-graph check for 'friends only' posts. Batch friend-graph check in one query. For public posts: zero DB calls.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 3B users, 500M posts/day, 100M searches/day, avg 10 likes/post |
| Read QPS | 100M searches / 86400 ≈ 1,157 search QPS — manageable for ES cluster |
| Write QPS | 500M posts / 86400 ≈ 5,787 post index writes/s + 500M × 10 likes / 86400 = 57,870 like events/s → batched to ~200 index updates/s |
| Storage | 3B posts × avg 500 chars × 2 bytes ≈ 3 TB raw text. ES index ≈ 2× = 6 TB. Inverted index per term over 3B docs. |
| Cache math | Hot search terms: top 10K queries account for ~60% of search traffic. Cache results for 30s: 10K × 10KB ≈ 100 MB Redis cache absorbs 60% of ES load. |
| Verdict | Like-update write amplification is the hidden bottleneck. 58K like events/s → 200 batched index updates/s is the key optimization. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Sort by recency vs. sort by popularity (likes)**

→ Two separate indexes, query routing by sort parameter

Recency sort: posts indexed by created_at (append-only, no updates needed). Popularity sort: posts ranked by like_count (frequent updates). Different update patterns require different optimization strategies.

_Revisit when:_ Unified index with sort-at-query-time is simpler but requires ES to handle the like-update storm.

**Privacy filtering: ES vs. app layer**

→ Coarse filter in ES (privacy_level field), fine filter in app layer (friend graph)

Storing full friend lists in ES documents is impossible (500M users × avg 1K friends = 500B entries in ES). Hybrid: ES filters obviously inaccessible posts, app layer handles 'friends only' edge cases.

_Revisit when:_ Graph DB (Neo4j) as a friend-graph service that the app layer queries for batch privacy checks.

**Freshness SLA for new posts appearing in search**

→ < 1 minute for public posts, < 5 minutes for friends-only posts

Users expect to search for something they just read about and find it quickly. 1 minute is the minimum perceptible freshness degradation. Friends-only posts are less time-critical.

_Revisit when:_ Real-time indexing (<5s) for verified accounts and high-engagement posts (breaking news).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle multi-word queries vs. single keyword?**

ES boolean query: AND for exact phrase match (higher score), OR for any-term match (lower score). Phrase proximity boosting: 'coffee shop' as adjacent words scores higher than same words far apart. ES handles this natively with match_phrase and match queries.

**How would you add autocomplete to the search box?**

Separate ES index with completion suggester field. Stores prefix trees of common search terms. Query: prefix match on first few typed characters. Response time < 50ms. Different index from the main post search index.

**How do you prevent search from surfacing harmful content?**

Content moderation pipeline runs before indexing. Posts flagged by ML classifier are soft-blocked (stored but not indexed). User-level block list: exclude posts from blocked users at query time (ES filter). Trending topics monitoring for coordinated abuse.

**How do you handle search in 100 languages?**

Per-language ES index (or per-language field in one index with language-specific analyzers). Language detection on post at index time. Query routed to language-appropriate analyzer. Transliteration index for cross-script queries (Hindi typed in Roman script).

**What's the latency budget for a search request?**

Target: P99 < 500ms. Budget: ES query 100ms + privacy filter 50ms + result hydration 50ms + network 100ms = 300ms. Remaining 200ms for cache miss + query parsing. Achieve with: query result caching (Redis, 30s TTL), connection pooling to ES, async result hydration.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — LIKE '%query%' on PG posts table. Works for 1M posts. Completely breaks at 100M posts.

**v2 — Inverted index** — Elasticsearch for post text + recency index. Kafka async indexing. Privacy coarse filter in ES. Basic friend-graph check in app layer. Handles billions of posts.

**v3 — Scale + freshness** — Batched like-count updates. Fast-lane indexing for viral posts. Per-language indexes. Autocomplete service. ML ranking layer. Result caching.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in FB Post Search is that search looks read heavy, but the indexing work is actually very write heavy. Every new post fans out into many index updates, and likes can create even more updates if you sort by popularity.

There are three scaling pain points to call out. First, you cannot scan raw posts at query time because the data is far too large, so you need an inverted index that maps terms to post IDs. Second, some terms are extremely hot, which means their posting lists get huge and expensive to store, sort, and query. Third, freshness matters. New posts should appear quickly, so you need fast ingestion and index updates without overwhelming the system.

A fourth issue is sorting. Recency is manageable, but sorting by like count is much harder because likes change constantly. That means one user action can force many index updates unless you use approximation or batching.

So the short interview answer is this. FB Post Search is hard because it combines massive inverted indexes, hot keywords, heavy write amplification from posts and likes, and a tight freshness requirement.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Inverted index** — Each keyword points to post IDs that contain it. Never scan raw posts at search time.
- **Write path** — Post → Kafka → index worker tokenizes → updates inverted index in ES.
- **Two sort indexes** — One ordered by creation time (recency). One ordered by like count (popularity).
- **Privacy in app layer** — Post-query filter. Friend lists are too large and dynamic for ES.
- **Shard by keyword** — So writes and reads spread across many machines.

> Mental model: precompute search with an inverted index. Write path tokenizes posts. Read path serves from indexes with caching.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Fast reads vs write cost** — Precomputed indexes make search fast but every post creates many index writes. Accept this — request-time scanning is too slow at billions of posts.

**Exact like ranking vs batched updates** — Updating every keyword ranking on every like is expensive at 57K likes/sec. Batched Redis → ES flush every 5 min keeps performance healthy at the cost of slight staleness.

**Privacy filter in ES vs app layer** — Storing full friend lists in ES documents is impossible at 3B users × avg 1K friends. Coarse privacy_level field in ES plus friend-graph check in app layer is the only viable hybrid.

**One unified index vs recency + popularity indexes** — A single index with sort-at-query-time couples update patterns. Recency index is append-only; popularity index needs frequent like-count updates. Separate indexes let each optimize independently.

> "FB Post Search is an inverted index system. Write path tokenizes posts and updates per-keyword indexes. Read path serves from those indexes with caching, sharding, and batching."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Inverted index design — term mapping to post IDs with two sort orders
> [!CAUTION]
> **🔴 Weak** — one unified Elasticsearch index, sort at query time
>
> [!WARNING]
> **🟡 Strong** — the core data structure: a map from each keyword to an ordered list of post IDs containing that keyword. Two sort orders are required: recency (post_id DESC or created_at DESC) and popularity (like_count DESC). Weak answer: one index, sort at query time. Strong answer: two separate indexes, each optimized for its sort order. The recency index is append-only (new posts are always the most recent — just append to the front). The like-count index is updated on every like — much more expensive because one like on a popular post triggers updates to potentially thousands of term lists
>
> [!TIP]
> **🟢 Staff+** — don't update the like-count index on every individual like. Batch like updates in Redis: increment a like counter in Redis, flush to the index every 5 minutes. This reduces write amplification from O(terms_per_post) per like to O(terms_per_post) per 5-minute window. The tradeoff: like-count rankings are slightly stale (up to 5 minutes) — acceptable, as users don't notice


#### Deep dive 2: Privacy filtering — the friend-graph problem
_Privacy is the hardest part of FB Post Search. Every result must be filtered against the viewer's permission to see it_

> [!CAUTION]
> **🔴 Weak** — post-query filter for every result
>
> [!WARNING]
> **🟡 Strong** — coarse filter in ES (privacy_level field: PUBLIC, FRIENDS, PRIVATE) combined with a friend-graph check in the app layer for FRIENDS-only posts
>
> [!TIP]
> **🟢 Staff+** — why not store friend lists in ES documents? The friend list for a user can be millions of people and changes constantly — storing it in every post document would make ES documents enormous and constantly out of date. The hybrid approach: ES returns candidates filtered by privacy_level. For FRIENDS posts in the result set, batch-query the social graph service: "Is viewer X friends with any of these [user_ids]?" This is a set intersection problem — solved efficiently with Bloom filters per user (is viewer in author's friend set?) or with a graph adjacency lookup. The friend check adds ~20ms to P99 latency — acceptable for a search query


#### Deep dive 3: Freshness — new posts appearing in search within 1 minute
> [!CAUTION]
> **🔴 Weak** — sync Elasticsearch from PostgreSQL with a scheduled batch job every 10 minutes
>
> [!WARNING]
> **🟡 Strong** — users expect to search for something they just posted and find it. The ingestion pipeline: post created in PG → Kafka event → index worker → ES update. End-to-end target: < 1 minute
>
> [!TIP]
> **🟢 Staff+** — bottlenecks to address: (1) ES bulk indexing batch size — larger batches are more efficient but add latency. At 5,787 posts/second, even 1-second batches give reasonable throughput. (2) ES refresh interval — by default ES refreshes the search index every 1 second (making indexed docs visible to search). For breaking news content: reduce refresh interval to 100ms for the first 5 minutes after a high-engagement post is created (dynamic refresh rate based on post engagement velocity). (3) High-engagement posts fast-lane: ML classifier identifies potentially viral posts within seconds of publish — route these to a priority Kafka topic with a dedicated low-latency indexer


_Why the deep dives connect to the scaling problem: "Massive inverted indexes, hot keywords, write amplification, and freshness." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Two-path script.

2. "Clarifying questions: are we searching all posts or only the searcher's social graph? And what's the freshness requirement — how quickly should a new post appear in search?"

3. "Good — full search with privacy filtering, freshness target of under 1 minute for new posts. Core features: text search with relevance ranking, privacy-aware results. Out of scope: hashtags, people search."

4. "Two distinct paths with different concerns: write path (index new posts) and read path (serve search queries). I'd walk them separately."

5. "Write path: post created → Kafka async → index worker tokenizes and NLP-processes text → indexes in Elasticsearch with privacy_level field and timestamp. Target latency: post visible in search within 60 seconds."

6. "Read path: search query → ES compound query (text match + privacy_level filter) → app layer friend-graph check for FRIENDS posts → return results. Privacy filter in app layer, not ES, because friend lists are too large and dynamic to store in ES documents."

7. "Like-count ranking: don't update ES on every like at 57K likes/sec. Batch like counts in Redis, flush to ES every 5 minutes. Like-sorted results are slightly stale — acceptable for search, not for the post itself."

8. "Key tradeoff: one unified ES index vs separate recency and popularity indexes. Unified is simpler but couples different update patterns. Recency index is append-only. Popularity index needs frequent updates. Separate indexes let each optimize independently."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
User Search Request -----> |   CDN / Edge      |
                           +---------+---------+
                                     |
                                     v
                           +-------------------+
                           |   API Gateway     |
                           | auth rate limit   |
                           +---------+---------+
                                     |
                                     v
                           +-------------------+
                           |   Search Service   |
                           +----+----------+---+
                                |          |
                    cache hit?  |          | fetch posts and fresh likes
                                |          v
                                |   +--------------+
                                |   | Post Service |
                                |   +--------------+
                                |   +--------------+
                                |   | Like Service |
                                |   +--------------+
                                v
                       +-----------------------+
                       | Distributed Search    |
                       | Cache TTL < 1 minute  |
                       +-----------+-----------+
                                   |
                              cache miss
                                   |
                                   v
                +-------------------------------------------+
                | Keyword Index Store                       |
                | sharded by keyword                        |
                |                                           |
                | creation index = list by recency          |
                | likes index = sorted set by like score    |
                +-------------------+-----------------------+
                                    |
                     hot keywords   |   cold keywords
                                    | 
                    +---------------+---------------+
                    |                               |
                    v                               v
             +-------------+                 +-------------+
             | Redis shard |                 | Blob store  |
             | in memory   |                 | S3 or R2    |
             +-------------+                 +-------------+


Write path
==========

Post Create ----> Post Service ----+
                                   |
Like Event -----> Like Service ----+----> Kafka or event log ----> Ingestion workers
                                                                     |
                                                                     v
                                                          +----------------------+
                                                          | Tokenizer            |
                                                          | split into keywords  |
                                                          | optional bigrams     |
                                                          +----------+-----------+
                                                                     |
                                            +------------------------+----------------------+
                                            |                                               |
                                            v                                               v
                               +--------------------------+                    +--------------------------+
                               | Update creation indexes  |                    | Update likes indexes     |
                               | add postId per keyword   |                    | sorted set score updates |
                               +--------------------------+                    +--------------------------+
                                                                                          |
                                                                                          v
                                                                         +------------------------------+
                                                                         | Optional like batcher or     |
                                                                         | approximate milestone writer |
                                                                         +------------------------------+
```

The mental model is two pipelines. One pipeline builds keyword indexes from posts and likes. The other pipeline serves search by reading those indexes fast, usually from cache or Redis.

If you were drawing this in an interview, I would start with just User, API Gateway, Search Service, Ingestion Service, and Index Store. Then add cache, Kafka, like batching, and cold storage only if the interviewer pushes on scale or freshness trade-offs.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-11)
