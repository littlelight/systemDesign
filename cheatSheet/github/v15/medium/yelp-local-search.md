# Yelp — local search

**Medium** · Geo search · Text index · Rating aggregates

Tags: `Elasticsearch`, `PostgreSQL`, `Redis`, `Geohash / Quadtree`, `CDN (photos)`

_See also: v10 · geospatial + search index_

## Data flow

Search combines geo_distance filter and full-text match in a single Elasticsearch query. PostgreSQL is source of truth. Average ratings are maintained in Redis and synced to PG asynchronously. Photos live on S3 + CDN.


> ES geo_distance + text in one query  |  PostgreSQL = source of truth  |  Photos: S3 + CDN

## Architecture diagram

```
+-------------------+
                         |   Web / Mobile    |
                         |      Client       |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         +----+---------+----+
                              |         |
                 GET search / view      | POST review
                              |         |
                              v         v
                  +----------------+   +----------------+
                  | Business       |   | Review         |
                  | Service        |   | Service        |
                  +---+--------+---+   +---+--------+---+
                      |        |           |        |
                      |        |           |        |
                      |        |           |        +------------------+
                      |        |           |                           |
                      |        |           v                           v
                      |        |   +------------------+       +------------------+
                      |        |   | Reviews Table    |       | Businesses Table |
                      |        |   | unique(userId,   |       | avg_rating       |
                      |        |   | businessId)      |       | num_reviews      |
                      |        |   +---------+--------+       +---------+--------+
                      |        |             |                          ^
                      |        +-------------+--------------------------|
                      |                     sync rating update          |
                      |               optimistic locking on write       |
                      |                                                 |
                      v                                                 |
           +-------------------------+                                  |
           | Read Replica / Cache    |----------------------------------+
           | for hot business reads  |
           +-----------+-------------+
                       |
                       v
           +-------------------------+
           | Search Store            |
           | Elasticsearch or        |
           | Postgres + PostGIS      |
           | + full text indexes     |
           +-----------+-------------+
                       ^
                       |
              CDC / async indexing
                       |
           +-----------+-------------+
           | Primary DB              |
           | businesses + reviews    |
           +-------------------------+

Optional for named locations

           +-------------------------+
           | Locations Table         |
           | name -> polygon         |
           | city / neighborhood     |
           +-------------------------+
```

The main story you should tell is simple. Search and business reads go through the Business Service, reviews go through the Review Service, the primary database is the source of truth, and search is powered either by Elasticsearch with CDC sync or by Postgres extensions if you want the simpler version. For interviews, I would present the simple version first, then add the search store and location polygons only if the interviewer pushes on search quality or scale.


---

<details open>
<summary><strong>Problem</strong></summary>

Local business discovery and trust. Find a place nearby, filter by category and location, decide based on reviews and ratings.

Two challenges: geospatial search at scale, and keeping ratings consistent.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Elasticsearch index goes out of sync with PostgreSQL**

Search results show stale business info (wrong hours, closed businesses still appearing).

_Fix:_ CDC (Change Data Capture) from PG → Kafka → ES indexer. Monitor sync lag with alerting at >60s. Periodic full reconciliation job (nightly) to catch missed events.

**Hot neighborhood (NYC midtown) hammers ES with geo queries**

One ES shard handling all NYC queries gets overloaded.

_Fix:_ Shard ES index by geohash prefix — ensures queries for the same region go to the same shard (locality). Add replica shards for hot regions. Route to nearest replica.

**Review rating aggregation is slow under write load**

New review posted, but average rating on search result takes minutes to update.

_Fix:_ Maintain running average in Redis (HINCRBY sum + count). Async batch-sync to PG every 30s. ES rating field updated via Kafka event. Rating is eventually consistent — seconds of lag is acceptable.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 100M DAU, 10 searches/day, 5 page views/search, 1M new reviews/day |
| Read QPS | 100M × 10 / 86400 ≈ 11,600 search QPS — served by Elasticsearch |
| Write QPS | 1M reviews / 86400 ≈ 12 review write QPS — trivially small |
| Storage | 10M businesses × 5 KB metadata ≈ 50 GB PG. ES index ≈ 3× PG size = 150 GB. Reviews: 500M × 500 bytes ≈ 250 GB. |
| Cache math | Top 10K businesses get 80% of page views. Cache business pages: 10K × 50 KB ≈ 500 MB — tiny Redis cache handles this. |
| Verdict | Read-dominated by 1000:1. ES and CDN are the critical path. PG writes are trivial. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**ES vs. PostgreSQL with PostGIS for search**

→ Elasticsearch with geo_distance + full-text

PG with PostGIS can do geo queries, but combining geo + full-text + category filters + rating sort in one PG query gets slow at 11K QPS. ES handles all four dimensions natively in one query.

_Revisit when:_ PG + PostGIS works fine up to ~1K QPS geo+text queries. Only switch to ES at higher scale.

**Running average in Redis vs. recompute from reviews table**

→ Precomputed running average in Redis

Recomputing avg(rating) across 10K reviews on every search request at 11K QPS = impossible. Maintain running sum and count: avg = sum/count. O(1) update per new review.

_Revisit when:_ Could do approximate aggregation with Redis HyperLogLog for distinct reviewer count.

**User photos: serve from S3 directly vs. CDN**

→ S3 + CDN with image resizing on first access

Business photos are requested millions of times. Serving from origin = massive S3 egress cost. CDN caches at edge. On-demand image resizing (Lambda@Edge or Imgix) serves appropriate resolution per device.

_Revisit when:_ Pre-generate thumbnails at upload time if resizing latency on first access is too high.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you rank search results?**

Multi-factor score: ES relevance score (text match quality) × distance decay function × rating × review count. Tuned with A/B testing. ML re-ranking for personalized results (separate service, separate concern from retrieval).

**How do you handle fake reviews?**

ML classifier on review text + user behavior signals (new account, same IP, suspiciously similar text). Soft delete (hide from display, keep for model training). Business owner can flag. Manual review queue for borderline cases.

**How do you keep ES in sync when PG is the source of truth?**

Debezium CDC: streams PG WAL changes to Kafka. ES indexer consumes Kafka topic, applies updates to ES. Idempotent — replaying the same change twice is safe. Nightly reconciliation job for drift detection.

**What if a business has 100,000 reviews — is that a hot partition in PG?**

Reviews table partitioned by business_id + time. Reads are paginated — never full table scan. Business-level aggregates (avg rating, review count) are precomputed, not queried from the reviews table directly. PG handles this fine.

**How would you add 'open now' filtering?**

Store hours as structured data (day_of_week, open_time, close_time). At query time, compute current UTC + business timezone → is it open? Apply as a post-ES filter in app layer (too dynamic to index). Cache 'open now' status per business with 15-min TTL.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: throughput, queue lag, cache effectiveness. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: async replication lag <30s; failover promotes read replica. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG for everything. LIKE query for search. No geo filtering. Works for a city with 1K businesses and 1K users.

**v2 — Real search** — Elasticsearch for text + geo. PG stays as source of truth. CDC sync. Redis for rating cache. CDN for photos. Handles city-scale.

**v3 — Global scale** — Regional ES clusters. Sharding by geohash. ML ranking. Personalization. Business owner API. Real-time review moderation.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Yelp is search, not writes. You need to search by text, category, and location at the same time, and location is especially tricky because geospatial queries do not scale well with a plain relational lookup.

There are three main pain points you should call out. First, search is multi-dimensional. A user might ask for coffee, in a certain area, in a certain category, so you need full-text indexing plus geospatial indexing, not just a normal database query. Second, reads dominate writes by a lot, so popular areas create heavy search and business page traffic and you need read scaling with replicas or caching. Third, some derived data like average rating must stay cheap to read, so you usually precompute it instead of recalculating from all reviews on every search.

A good interview summary is this. Yelp is hard because it combines read-heavy traffic with geospatial search and text search in one request. The write path for reviews is comparatively small, so the main challenge is making search fast while keeping results fresh enough.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **PostgreSQL = source of truth** — Businesses, reviews, and users.
- **Elasticsearch = search index** — geo_distance filter + full-text in one query.
- **Redis = rating cache** — Average ratings served from Redis for fast reads. Synced to PG asynchronously.
- **Photos = S3 + CDN** — Never serve photos from app servers.
- **Search → ES, not PG** — Never do SQL LIKE queries on business names at scale.

> Three databases, one per concern. PG for writes and truth. ES for search. Redis for hot reads.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**ES geo_distance vs PostGIS** — ES handles both text and geo in one compound query. PostGIS is more powerful for complex spatial queries but adding a second query join adds latency and complexity.

**Redis for ratings vs recompute from DB** — Redis running average (sum + count) gives sub-millisecond rating reads. Recomputing avg(rating) at 11K QPS from a reviews table is O(N) per request — impossible.

**CDC sync vs dual-write for ES freshness** — Dual-write is simpler but ES and PG can diverge on partial failure. CDC (Debezium → Kafka → ES indexer) is more reliable because it catches all writes regardless of source, at the cost of eventual consistency (~5s lag).

**"Open now" filter in ES vs app layer** — Hours data changes every minute by definition (time passes). Indexing a boolean that changes constantly creates massive re-indexing overhead. Compute "open now" in app layer with 15-min TTL cache per business — far cheaper.

> "PostgreSQL for truth, ES for search (geo + text), Redis for hot reads. Three stores, each with one job."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Geospatial + text search — Elasticsearch as the single query surface
> [!CAUTION]
> **🔴 Weak** — run separate queries for geo (PostGIS) and text (LIKE), merge results in the app layer
>
> [!WARNING]
> **🟡 Strong** — the scaling pain is that a user query like "best sushi near me open now" combines full-text search, geo filtering, category filtering, and rating sorting in one request. Weak answer: separate queries to separate services, merge in app layer. Strong answer: Elasticsearch as a single query surface with a compound query: geo_distance filter (within X km of lat/lng) + multi_match on business name/category/description + term filter on category + range filter on rating + sort
>
> [!TIP]
> **🟢 Staff+** — the ES query shape matters for performance. Structured fields (category, rating, hours) should use filter context (cached, no scoring overhead) and text fields should use query context (scored, more expensive). The geo_distance filter is the most selective first — apply it first to reduce the candidate set before text scoring. For "open now": this is a computed field that changes every minute — too dynamic to index. Apply as a post-query filter in app layer, not in ES. Cache "is_open" status per business with 15-minute TTL


#### Deep dive 2: Keeping ES in sync with PostgreSQL — CDC and eventual consistency
_ES is derived data — PostgreSQL is the source of truth. Sync strategy options: (1) dual write (write to PG + ES in same request) — simple but PG and ES can diverge on partial failure; (2) CDC (Debezium reads PG WAL → Kafka → ES indexer) — eventual consistency, reliable, standard production approach; (3) async event-driven (after PG write succeeds, publish Kafka event → ES indexer) — same reliability as CDC but more explicit_

> [!CAUTION]
> **🔴 Weak** — Oversimplify keeping es in sync with postgresql — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — ES is derived data — PostgreSQL is the source of truth. Sync strategy options: (1) dual write (write to PG + ES in same request) — simple but PG and ES can diverge on partial failure; (2) CDC (Debezium reads PG WAL → Kafka → ES indexer) — eventual consistency, reliable, standard production approach; (3) async event-driven (after PG write succeeds, publish Kafka event → ES indexer) — same reliability as CDC but more explicit
>
> [!TIP]
> **🟢 Staff+** — CDC is the production default because it works for all write patterns (including bulk imports, migrations, and direct DB writes by other services) without requiring every writer to know about ES. Lag: typically 1-5 seconds — acceptable for search. Monitor lag metric: alert at >60s. For new business data (most time-sensitive): reduce batch size in the indexer for faster propagation


#### Deep dive 3: Rating aggregation — precomputed running average
_The access pattern for ratings: millions of reads per business page, one write per new review. Recomputing avg(rating) from all reviews on every page view is O(N) per request at 11,600 QPS — impossible_

> [!CAUTION]
> **🔴 Weak** — cache the result
>
> [!WARNING]
> **🟡 Strong** — maintain a running average in the database: (sum_of_ratings, review_count) per business. On new review: UPDATE businesses SET sum_of_ratings = sum_of_ratings + ?, review_count = review_count + 1 WHERE id = ?. Average = sum/count, computed at read time (O(1))
>
> [!TIP]
> **🟢 Staff+** — concurrent reviews on a popular business can create a write hotspot on the businesses row. Mitigation: use PG row-level locking for the increment (short lock duration), or batch review aggregation (flush accumulated ratings every 30s from Redis). In ES: rating field updated via the same CDC pipeline — search results always reflect recent ratings


_Why the deep dives connect to the scaling problem: "Multi-dimensional search plus precomputed read data." Each deep dive solves one dimension of the read scaling problem._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Compound-query script.

2. "Clarifying questions: are we building just search, or also reviews, photos, and business owner tools? And what's the primary query pattern — geo + text combined, or mostly one or the other?"

3. "Good — full local business search with reviews. Primary pattern: geo + text combined — 'best sushi near me.' Core features: search with location, business profiles, reviews. Out of scope: reservations, ads."

4. "The key design decision: Elasticsearch as the single query surface. A 'best sushi near me' query combines full-text matching, geo_distance filtering, rating sorting, and category filtering — all in one request. ES handles all four dimensions natively in a compound query."

5. "Data architecture: PostgreSQL is source of truth for businesses and reviews. ES is a derived read index. Sync via CDC: Debezium reads PG WAL → Kafka → ES indexer. Typical lag: 1-5 seconds. Acceptable for a local search product."

6. "Rating aggregation: maintain a running average in PG — sum_of_ratings and review_count columns. On new review: increment both atomically. Average = sum/count, O(1). Recomputing avg from all reviews at 11K QPS is impossible."

7. "Open now filter: hours data changes every minute by definition. Too dynamic to index. Compute in app layer with 15-min TTL cache per business. Apply as post-ES filter — never in the ES query itself."

8. "Photos: S3 + CDN. On-demand resizing at CDN edge (Lambda@Edge or Imgix). Pre-generate thumbnails at upload time for the most common sizes."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |   Web / Mobile    |
                         |      Client       |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |    API Gateway    |
                         +----+---------+----+
                              |         |
                 GET search / view      | POST review
                              |         |
                              v         v
                  +----------------+   +----------------+
                  | Business       |   | Review         |
                  | Service        |   | Service        |
                  +---+--------+---+   +---+--------+---+
                      |        |           |        |
                      |        |           |        |
                      |        |           |        +------------------+
                      |        |           |                           |
                      |        |           v                           v
                      |        |   +------------------+       +------------------+
                      |        |   | Reviews Table    |       | Businesses Table |
                      |        |   | unique(userId,   |       | avg_rating       |
                      |        |   | businessId)      |       | num_reviews      |
                      |        |   +---------+--------+       +---------+--------+
                      |        |             |                          ^
                      |        +-------------+--------------------------|
                      |                     sync rating update          |
                      |               optimistic locking on write       |
                      |                                                 |
                      v                                                 |
           +-------------------------+                                  |
           | Read Replica / Cache    |----------------------------------+
           | for hot business reads  |
           +-----------+-------------+
                       |
                       v
           +-------------------------+
           | Search Store            |
           | Elasticsearch or        |
           | Postgres + PostGIS      |
           | + full text indexes     |
           +-----------+-------------+
                       ^
                       |
              CDC / async indexing
                       |
           +-----------+-------------+
           | Primary DB              |
           | businesses + reviews    |
           +-------------------------+

Optional for named locations

           +-------------------------+
           | Locations Table         |
           | name -> polygon         |
           | city / neighborhood     |
           +-------------------------+
```

The main story you should tell is simple. Search and business reads go through the Business Service, reviews go through the Review Service, the primary database is the source of truth, and search is powered either by Elasticsearch with CDC sync or by Postgres extensions if you want the simpler version. For interviews, I would present the simple version first, then add the search store and location polygons only if the interviewer pushes on search quality or scale.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-12)
