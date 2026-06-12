# News aggregator (Google News)

**Easy** · Crawl · SimHash dedup · Story clustering

Tags: `Kafka`, `Elasticsearch`, `Cassandra`, `SimHash`, `Cursor pagination`, `CDN`

## Data flow

RSS crawlers feed articles into Kafka. A dedup service computes a SimHash fingerprint per article — Hamming distance Elasticsearch powers search. Cassandra stores raw articles. Regional Redis sorted sets serve the feed path.


> SimHash → near-dup if Hamming dist < 3  |  Cluster into "story"  |  Rank = recency × authority

## Architecture diagram

```
+----------------------+
                        |   News Publishers    |
                        | RSS APIs Webhooks    |
                        +----------+-----------+
                                   |
                                   v
                    +-------------------------------+
                    | Data Collection Service       |
                    | poll feeds parse ingest media |
                    +---------------+---------------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
      +----------------------+              +----------------------+
      |   Article Database   |              |   Object Storage     |
      | articles publishers  |              | thumbnails images    |
      +----------+-----------+              +----------+-----------+
                 |                                     |
                 | new article writes                  |
                 v                                     v
      +----------------------+              +----------------------+
      |   CDC Event Stream   |              |   CDN               |
      | change notifications |              | serve thumbnails    |
      +----------+-----------+              +----------------------+
                 |
                 v
      +------------------------------+
      | Feed Generation Workers      |
      | update regional feed caches  |
      +--------------+---------------+
                     |
                     v
      +------------------------------+
      | Redis Feed Cache             |
      | feed:US feed:UK sorted sets  |
      | recent article ids by time   |
      +--------------+---------------+
                     |
                     v
+-----------+   +----------------------+   +------------------+
|  Client   |-->|     API Gateway      |-->|   Feed Service   |
| web mobile|   | auth rate limit      |   | get feed paginate|
+-----------+   +----------------------+   +---------+--------+
                                                       |
                                  +--------------------+-------------------+
                                  |                                        |
                                  v                                        v
                        +----------------------+                +----------------------+
                        | Redis Feed Cache     |                |  Article Database    |
                        | primary read path    |                | cache miss fallback  |
                        +----------------------+                +----------------------+
                                                       |
                                                       v
                                              +------------------+
                                              | Feed Response    |
                                              | title summary    |
                                              | thumbnail url    |
                                              | publisher url    |
                                              +------------------+
```

The main idea is two pipelines. One pipeline ingests articles from publishers and stores article metadata plus thumbnails. The other pipeline serves users by reading precomputed regional feeds from Redis so feed requests stay fast.

If you want the best interview version, draw the high level boxes first, then call out one improvement. Use cursor pagination for infinite scroll and Redis precomputed regional feeds for low latency. That shows the core system clearly without overcrowding the board.


---

<details open>
<summary><strong>Problem</strong></summary>

Organizing news from thousands of publishers into one fast scrollable feed. The system collects articles, stores metadata, and redirects users to the publisher site on click.

The challenge: fast aggregation + deduplication at scale, not hosting full articles.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Redis feed cache evicts an entry during peak read**

Cache miss causes DB query — at 100M users × breaking news = millions of concurrent misses (thundering herd).

_Fix:_ Cache warming on publish: when new articles are added to a feed, proactively write to Redis rather than waiting for reads. Add probabilistic early expiration to prevent synchronized expiry.

**Crawler is blocked by a publisher**

Publisher's articles stop appearing. Users notice freshness degradation for that source.

_Fix:_ Exponential backoff on crawler errors. Multiple crawl strategies (RSS, scrape, webhook). Monitor per-source freshness SLA with alerts.

**Elasticsearch index falls behind during a breaking news event**

Search results don't show the most recent articles for a rapidly evolving story.

_Fix:_ Dedicated high-priority Kafka topic for breaking news. Separate fast-lane indexing pipeline with lower batch size. Monitor ES indexing lag with alerting at >30s.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 100M DAU, 10 feed refreshes/day, 1M articles ingested/day from 10K publishers |
| Read QPS | 100M × 10 / 86400 ≈ 11,600 feed read QPS — served from Redis |
| Write QPS | 1M articles / 86400 ≈ 12 ingest QPS — trivially small |
| Storage | 1M articles/day × 2 KB × 365 days × 3 years ≈ 2.2 TB article store in Cassandra |
| Cache math | 100M users × 50 article IDs × 8 bytes ≈ 40 GB Redis — fits on a beefy Redis cluster |
| Verdict | Read-dominant by 1000:1. Redis feed cache is the critical path. Ingestion is easy — only 12 QPS. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Pre-computed feeds vs. on-demand assembly**

→ Pre-computed regional Redis sorted sets

100M users × 11,600 QPS — on-demand assembly would require querying Cassandra per user per request. Impossible. Pre-compute on write (ingest), serve from Redis on read.

_Revisit when:_ On-demand assembly with a fast query cache if personalization requirements grow beyond regional buckets.

**Cursor pagination vs. offset**

→ Cursor (published_at + article_id composite)

Offset pagination is broken for feeds — new articles shift positions while the user scrolls, causing duplicates and gaps. Cursor is stateless and stable.

_Revisit when:_ Never use offset for real-time feeds.

**SimHash dedup vs. exact hash**

→ SimHash with Hamming distance threshold

Exact hash only catches identical articles. Same story reported by 50 publishers with slight wording differences = 50 duplicate articles. SimHash catches near-duplicates.

_Revisit when:_ More sophisticated NLP clustering (topic modeling) if higher quality story grouping is required.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a breaking news event with 100× normal traffic?**

Redis feed reads scale horizontally — add read replicas. The bottleneck is cache population during the event. Prioritize updating breaking-news topic feeds first. CDN-cache the feed API responses for 5s to absorb the spike tail.

**How do you personalize feeds without blowing up Redis storage?**

Cluster users into interest profiles (topic affinity vectors). Pre-compute one feed per cluster (thousands, not millions). On read, take the cluster feed and apply lightweight user-specific boosts in app layer.

**How fresh does the feed need to be?**

Explicitly define the SLA: breaking news < 2 min, general news < 10 min. Different ingestion pipelines serve different SLAs. Webhooks from publishers for < 2 min; polling for the rest.

**How do you prevent one spammy publisher from polluting feeds?**

Publisher trust score (authority signal). Rate limit new publishers. Editorial review queue for sources below a trust threshold. SimHash dedup also helps by collapsing near-duplicate spam.

**What's the hardest part to get right in production?**

Cache consistency during feed updates. When you update a regional feed, you have to atomically swap it — partial updates show users an inconsistent feed mid-scroll. Use Redis MULTI/EXEC or REPLACE rather than incremental append.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: success rate, latency, active users. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: minutes of read unavailability acceptable; rebuild cache from DB. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Poller fetches RSS feeds every 5 min. PG stores articles. Feed built on-demand with SQL. Simple and works for <100K users.

**v2 — Scale** — Kafka ingestion pipeline. Cassandra for article storage. Redis pre-computed regional feeds. SimHash dedup. Cursor pagination. Elasticsearch for search.

**v3 — Personalize** — User interest clustering. Personalized re-ranking in app layer. ML-based story clustering. Freshness SLA monitoring per source. Publisher trust scoring.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is that both sides scale at once. You are ingesting articles from thousands of publishers, while also serving a huge read-heavy feed to millions of users with very fresh content.

There are three main pain points. First, feed reads are massive, especially during breaking news, so you cannot query the database for every request and still stay under 200ms. Second, the feed keeps changing while users scroll, which makes simple page-number pagination cause duplicates or missed articles. Third, freshness matters a lot, so ingestion is not just batch ETL work. You need to discover new articles quickly, update regional feeds fast, and keep caches fresh without overwhelming publishers or your own systems.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Two pipelines** — Write pipeline ingests from publishers. Read pipeline serves cached feeds to users.
- **Collect** — RSS, publisher APIs, or web scraping.
- **Dedup** — SimHash fingerprint per article. Hamming distance < 3 = near-duplicate, discard.
- **Precompute feeds** — Regional Redis sorted sets. New articles update them async. Reads are fast cache lookups.
- **Pagination** — Cursor-based, not page numbers. Offset pagination causes gaps when new articles arrive.
- **Thumbnails** — Copy to object storage + CDN rather than hotlinking publisher images.

> Lead with the read/write ratio: ingest is trivial (12 QPS), serving is massive (11,600 QPS). Everything flows from that asymmetry — precompute on write, serve from cache.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Offset pagination vs cursor** — Offset causes gaps when new articles arrive. Cursor is correct for infinite scroll.

**DB reads vs Redis precomputed feeds** — DB reads are simpler. Redis feeds are much faster for 100M users but add update logic.

**Polling vs webhooks for ingestion** — Polling works without coordination, content arrives later. Webhooks are fresher but need publisher adoption.

> I favor precomputed regional feeds over on-demand assembly, cursor pagination over offsets, and SimHash near-dedup over exact dedup. Each tradeoff buys read speed at the cost of some write complexity.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Feed generation — precomputed vs. on-demand at 100M DAU
> [!CAUTION]
> **🔴 Weak** — query Cassandra per request, assemble feed on-demand
>
> [!WARNING]
> **🟡 Strong** — the scaling pain is that you can't query the database for every feed request at 11,600 QPS and stay under 200ms. Weak answer: query Cassandra per request. Strong answer: pre-compute regional Redis sorted sets (score = publish timestamp) and serve feeds from cache. The hard part is keeping pre-computed feeds fresh during breaking news when articles arrive at high frequency
>
> [!TIP]
> **🟢 Staff+** — write-through feed population — when an article passes dedup and is stored, the ingestion service immediately pushes its ID into the relevant regional sorted sets. This inverts the cache pattern: writes are the fan-out, reads are O(1) ZREVRANGE. Tradeoffs to name: (1) feed storage cost (100M users × 50 IDs × 8 bytes = 40 GB Redis — clusters needed), (2) feed staleness during cache update, (3) thundering herd if a regional cache expires simultaneously. Fix the last one with probabilistic early expiration


#### Deep dive 2: Cursor-based pagination — why it's mandatory for live feeds
_Offset pagination is broken for live feeds: new articles shift existing positions while the user scrolls, causing duplicates (article appears twice) or gaps (article skipped)_

> [!CAUTION]
> **🔴 Weak** — use offset pagination, acknowledge the issue
>
> [!WARNING]
> **🟡 Strong** — cursor pagination using a composite cursor (published_at + article_id). The cursor encodes where the user is in the feed at the moment they fetched the previous page — new articles added to the top don't shift relative positions below the cursor
>
> [!TIP]
> **🟢 Staff+** — cursor must be stable under concurrent feed updates. Using (published_at, article_id) as cursor is stable because we sort by published_at DESC, article_id DESC — inserting new articles at the top doesn't reorder articles below any existing cursor position. Implementation: SELECT * FROM feed WHERE (published_at, article_id) < (cursor_time, cursor_id) ORDER BY published_at DESC, article_id DESC LIMIT 20


#### Deep dive 3: Article deduplication and story clustering
> [!CAUTION]
> **🔴 Weak** — use exact SHA-256 hashing — only catches identical articles
>
> [!WARNING]
> **🟡 Strong** — SimHash dedup: each article gets a fingerprint by tokenizing text → compute tf-idf weights → hash weighted term vector to 64-bit integer. Hamming distance < 3 between two fingerprints = near-duplicate. This catches the same story reported by 50 publishers with slight wording differences
>
> [!TIP]
> **🟢 Staff+** — SimHash lookup requires comparing a new fingerprint against all stored fingerprints — at 1M articles/day this is O(N) per insert. The solution: partition fingerprints by their first K bits (locality-sensitive hashing) so only fingerprints in the same bucket need comparison. Story clustering goes further: group near-duplicates into a "story" entity with the highest-authority source as the canonical article. Ranking: recency × source authority score (PageRank-like, precomputed per domain)


_Why the deep dives connect to the scaling problem: "Both sides scale at once — ingestion and reads." Deep dive 1 solves read scaling. Deep dive 2 solves pagination correctness. Deep dive 3 solves the content quality problem that makes the system valuable._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Asymmetry-first script.

2. "Clarifying questions: are we aggregating from a fixed set of publisher RSS feeds, or also crawling arbitrary web pages? And does personalization matter — per-user feeds — or just regional topic feeds?"

3. "Good — RSS plus crawl, regional feeds initially with per-user personalization later. Core features: ingest articles, deduplicate, rank, serve feed. Out of scope: publisher partnerships, comment systems."

4. "The key asymmetry to name immediately: ingest is trivial — ~12 writes/sec. Serving is the hard problem — 11,600 feed read QPS. Everything should optimize for reads."

5. "Ingest pipeline: crawler fetches RSS feeds, extracts article text, pushes to Kafka. SimHash fingerprinting detects near-duplicates (Hamming distance < 3). Unique articles stored in Cassandra, indexed in Elasticsearch."

6. "Feed generation: pre-compute regional Redis sorted sets on write. When a new article lands, push its ID into the relevant topic and region sets immediately. Feed read = ZREVRANGE — sub-millisecond, no query needed."

7. "Cursor pagination over offsets: new articles shift offset-based positions while the user scrolls, causing duplicates and gaps. Cursor using (published_at, article_id) is stable under concurrent inserts."

8. "Freshness: for breaking news, the ingest-to-index pipeline needs to complete in under 2 minutes. Monitor indexing lag per source. Webhook-based ingest from major publishers for the fastest path."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                        |   News Publishers    |
                        | RSS APIs Webhooks    |
                        +----------+-----------+
                                   |
                                   v
                    +-------------------------------+
                    | Data Collection Service       |
                    | poll feeds parse ingest media |
                    +---------------+---------------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
      +----------------------+              +----------------------+
      |   Article Database   |              |   Object Storage     |
      | articles publishers  |              | thumbnails images    |
      +----------+-----------+              +----------+-----------+
                 |                                     |
                 | new article writes                  |
                 v                                     v
      +----------------------+              +----------------------+
      |   CDC Event Stream   |              |   CDN               |
      | change notifications |              | serve thumbnails    |
      +----------+-----------+              +----------------------+
                 |
                 v
      +------------------------------+
      | Feed Generation Workers      |
      | update regional feed caches  |
      +--------------+---------------+
                     |
                     v
      +------------------------------+
      | Redis Feed Cache             |
      | feed:US feed:UK sorted sets  |
      | recent article ids by time   |
      +--------------+---------------+
                     |
                     v
+-----------+   +----------------------+   +------------------+
|  Client   |-->|     API Gateway      |-->|   Feed Service   |
| web mobile|   | auth rate limit      |   | get feed paginate|
+-----------+   +----------------------+   +---------+--------+
                                                       |
                                  +--------------------+-------------------+
                                  |                                        |
                                  v                                        v
                        +----------------------+                +----------------------+
                        | Redis Feed Cache     |                |  Article Database    |
                        | primary read path    |                | cache miss fallback  |
                        +----------------------+                +----------------------+
                                                       |
                                                       v
                                              +------------------+
                                              | Feed Response    |
                                              | title summary    |
                                              | thumbnail url    |
                                              | publisher url    |
                                              +------------------+
```

The main idea is two pipelines. One pipeline ingests articles from publishers and stores article metadata plus thumbnails. The other pipeline serves users by reading precomputed regional feeds from Redis so feed requests stay fast.

If you want the best interview version, draw the high level boxes first, then call out one improvement. Use cursor pagination for infinite scroll and Redis precomputed regional feeds for low latency. That shows the core system clearly without overcrowding the board.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-3)
