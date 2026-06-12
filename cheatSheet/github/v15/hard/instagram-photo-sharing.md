# Instagram — photo sharing

**Hard** · Media pipeline · Hybrid fan-out · CDN

Tags: `S3 + CDN`, `Redis sorted set`, `Kafka`, `Cassandra`, `Hybrid push/pull`

## Data flow

Upload: client gets a pre-signed S3 URL and uploads directly. Async transcode generates multiple resolutions. Fan-out: post event → Kafka → fan-out worker pushes post_id into follower Redis sorted sets. Celebrities: fan-out skipped, pulled at read time and merged.


> Celeb >1M followers: skip push → pull at read time  |  Pre-signed URL: client uploads direct to S3

## Architecture diagram

```
+-------------------+
                             |   Mobile / Web    |
                             |      Clients      |
                             +---------+---------+
                                       |
                                       v
                             +-------------------+
                             |    API Gateway    |
                             | auth rate limit   |
                             +----+----+----+----+
                                  |    |    |
                 POST /posts -----+    |    +----- GET /feed
                 POST /follows ---------+
                                       
        +-------------------+    +-------------------+    +-------------------+
        |   Post Service    |    |  Follow Service   |    |   Feed Service    |
        | create post meta  |    | follow unfollow   |    | read feed         |
        +----+---------+----+    +---------+---------+    +----+---------+----+
             |         |                     |                   |         |
             |         |                     |                   |         |
             |         v                     v                   |         v
             |   +-----------+         +-----------+             |   +------------+
             |   |  Posts DB |         | FollowsDB |             |   |   Redis    |
             |   | DynamoDB  |         | DynamoDB  |             |   | feed zset  |
             |   +-----------+         +-----------+             |   | post cache |
             |                                                   |   +-----+------+
             |                                                   |         |
             |                                                   |         v
             |                                                   |   +------------+
             |                                                   +-->|  Posts DB  |
             |                                                       | BatchGet   |
             |                                                       +------------+
             |
             |  presigned upload URL
             v
      +-------------------+        multipart upload        +-------------------+
      |   Blob Storage    |<------------------------------>|      Client       |
      |       S3          |                                +-------------------+
      +---------+---------+
                |
                v
      +-------------------+
      |       CDN         |
      | edge cache media  |
      +---------+---------+
                |
                v
      +-------------------+
      |   Media Delivery  |
      | photos and videos |
      +-------------------+


                 Async fanout path after new post

        Post Service
             |
             v
      +-------------------+
      |   Queue / Topic   |
      | new post events   |
      +---------+---------+
                |
                v
      +-------------------+
      | Feed Fanout Worker|
      | async background  |
      +----+---------+----+
           |         |
           |         v
           |   +-----------+
           |   | FollowsDB |
           |   | followers |
           |   +-----------+
           |
           v
      +-------------------+
      |       Redis       |
      | update feed:user  |
      +-------------------+


                 Celebrity hybrid read path

      Feed Service
           |
           +----> Redis precomputed feed for normal accounts
           |
           +----> Posts DB for recent celebrity posts
           |
           v
      merge by timestamp and return page
```

The mental model is two big flows. Write flow stores post metadata and media, then asynchronously updates follower feeds. Read flow pulls mostly from Redis, then hydrates post metadata from the posts store, with a hybrid read for celebrity accounts.


---

<details open>
<summary><strong>Problem</strong></summary>

Photo sharing at massive scale. Two hard problems: media pipeline (ingest → transcode → CDN) and feed generation (hybrid push/pull for celebrity accounts).

</details>


<details>
<summary><strong>Failures</strong></summary>

**Fan-out worker falls behind for a celebrity's post (50M followers)**

Millions of followers don't see the post for 10+ minutes. Feed appears stale.

_Fix:_ Celebrity accounts (> threshold followers) bypass fan-out entirely. At feed read time, pull celebrity post IDs from Cassandra and merge. Threshold is a config value, not code.

**Media transcode job fails for a specific format/codec**

Some users see a broken media preview. Reels content doesn't play on older devices.

_Fix:_ Idempotent transcode jobs: store transcode status per (media_id, resolution). Failed resolution re-queued automatically. Serve available resolutions while others process. Never block upload confirmation on transcode completion.

**CDN cache miss on a viral Reel**

First million requests all reach origin S3. S3 request cost spike. Origin latency degrades.

_Fix:_ Pre-warm CDN for content from verified/celebrity accounts. Use CDN cache-forward headers. Multi-CDN setup (primary + fallback) prevents single CDN saturation.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 2B users, 500M DAU, 100M photos/videos uploaded/day, 5B feed reads/day |
| Read QPS | 5B / 86400 ≈ 57,870 feed read QPS — served from Redis sorted sets |
| Write QPS | 100M uploads / 86400 ≈ 1,157 upload QPS. Fan-out: 1,157 × avg 500 followers = 578,500 feed writes/s |
| Storage | 100M uploads/day × avg 3 MB processed = 300 TB/day to S3. Over a year: 100 PB. |
| Cache math | Feed cache: 2B users × 500 post IDs × 8 bytes ≈ 8 TB Redis. Across Redis cluster with 100 nodes: 80 GB/node — fine. |
| Verdict | Media storage cost is the defining cost constraint. CDN is the largest spend. Fan-out write volume (578K/s) requires massive Kafka + worker fleet. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Upload to CDN origin vs. S3 + CDN**

→ Upload to S3, CDN fronts delivery

S3 is the durable store. CDN is the delivery layer. Never upload directly to CDN origin (CDN is not durable storage). Pre-signed S3 URL for direct upload (bypass app servers entirely).

_Revisit when:_ Some CDNs support direct upload (Cloudflare R2). Evaluate if CDN vendor provides both storage and delivery.

**Stories vs. Feed: different architectures**

→ Stories: 24hr TTL objects with different ranking. Feed: ranked by ML model.

Stories are ephemeral — Redis with 24hr TTL, no fan-out needed (pull on open). Feed is curated — ML ranking over pre-fetched candidates. Different content types need different storage and serving strategies.

_Revisit when:_ Stories could use the same fan-out infrastructure as feed if the architectures converge in product.

**Explore vs. Follow feed**

→ Separate serving stack for Explore (interest graph) vs. Follow feed (social graph)

Follow feed: fan-out from people you follow, Redis sorted sets. Explore: interest-based recommendations from the whole graph, ML model, no fan-out needed. Mixing these in one pipeline creates complexity.

_Revisit when:_ Separate teams, separate models, separate serving. Explore is essentially a recommendation system problem, not a social feed problem.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a 100GB video upload?**

TUS resumable protocol: chunk the video client-side (10MB chunks). Server accepts chunks independently, stores in S3 multipart. Upload can pause/resume. Server stitches chunks once all received, then enqueues transcode job. Client gets confirmation of receipt, not completion of transcode.

**How do you serve the right image resolution for different devices?**

Transcode generates multiple resolutions: thumbnail (150x150), feed (1080x1080), full (original). URL encodes resolution: cdn.instagram.com/media/{id}/1080.jpg. Client requests the appropriate resolution based on device screen density and connection speed.

**How does the ML recommendation ranking work?**

Two-stage retrieval + ranking: (1) candidate generation — retrieve top-1000 posts from social graph + interest graph, (2) ranking — ML model scores each candidate using user features, post features, interaction history. Serve top-50. This is a separate service from the feed delivery infrastructure.

**How do you handle copyright violations in user uploads?**

Perceptual hash (pHash) of every upload compared against a hash database of known copyrighted content. Match within threshold → quarantine for review. Audio fingerprinting for music in Reels (AcrCloud). DMCA takedown system for post-publish violations.

**How would you handle a data center outage?**

Multi-region active-active deployment. Cassandra replication across regions. S3 cross-region replication for media. Redis leader in primary region, read replicas in secondary. DNS failover (Route 53 health checks). Feed reads may be briefly stale — acceptable.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single server, local disk storage, PG. Filters applied client-side. Upload + view. Handles 10K users.

**v2 — Scale** — S3 + CDN for media. Redis feed cache with fan-out. Pre-signed URL upload. Async transcode pipeline. Celebrity pull model. Handles 100M users.

**v3 — 2B users** — Multi-region. ML ranking for both Feed and Explore. Reels (video) as separate pipeline. Stories with 24hr TTL. Shopping integration. 100 PB/year media storage.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Instagram is the feed. One new post can create a lot of downstream work, and one feed request can also be expensive if you build it on demand.

There are three main scaling pain points. First, feed generation has a fan-out problem. If you compute the feed at read time, one user request may need posts from hundreds or thousands of followed accounts, then merge and sort them fast. If you precompute feeds at write time, one new post may need to be pushed into millions of follower feeds. Second, media delivery is heavy. Photos and especially videos are large, so uploads, storage, and global low-latency delivery all get expensive fast. Third, load is very uneven. Most users are normal, but celebrity accounts create hot spots because one post can trigger huge write amplification and huge read traffic at the same time.

So the short interview answer is this. Instagram is hard to scale because it combines feed fan-out, massive media storage and delivery, and hot celebrity traffic. That is why a hybrid feed model is usually the best default. Precompute for normal users, then merge celebrity posts at read time.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Pre-signed URL upload** — Client uploads directly to S3. App server only issues the URL and records metadata.
- **Async parallel transcode** — S3 upload triggers a job. Workers for each resolution run in parallel.
- **CDN delivery** — Processed images served from CDN. Never serve media from app servers.
- **Fan-out on write** — Post event → Kafka → fan-out worker → push post_id into follower Redis sorted sets.
- **Celebrity threshold** — Accounts above a follower threshold get pull-at-read treatment.

> Pre-signed URL + async transcode for upload. Hybrid fan-out for feeds.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Push fan-out vs pull for celebs** — Push to 50M+ follower caches is catastrophic write amplification. Pull celebrity posts at read time and merge with pre-built feed is the only viable approach for large accounts.

**Pre-signed URL vs proxy upload** — Pre-signed URL: client uploads directly to S3, app servers never touch the bytes. Proxy through app servers at 1,157 uploads/sec × avg 3MB = 3.5 GB/s through app tier — completely impractical.

**Transcode before vs after acknowledgment** — Blocking upload confirmation on full transcode (all resolutions) adds minutes of latency. Acknowledge on S3 receipt, transcode async, serve available resolutions progressively. Never block the user.

**CDN TTL vs cache purge on delete** — Long TTL maximizes CDN hit rate and reduces origin cost. But deleted posts must be purged immediately. Tag-based CDN purge (all variants of a post_id in one API call) reconciles both — long TTL by default, instant purge when needed.

> "Never push to 100M+ follower caches — catastrophic write amplification. Hybrid model is the critical staff-level insight."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Media upload pipeline — pre-signed URLs, async transcode, multiple resolutions
> [!CAUTION]
> **🔴 Weak** — accept upload through app servers, store to S3, transcode sequentially before acknowledging
>
> [!WARNING]
> **🟡 Strong** — the scaling pain is the read side of media: photos and videos are large, globally distributed, and must load fast. The write side has different constraints: uploads are infrequent, can tolerate latency, but must be resumable
>
> [!TIP]
> **🟢 Staff+** — (1) client requests a pre-signed S3 URL — app server never sees the file bytes. (2) Client uploads directly to S3 (bypasses all app servers — critical for cost and throughput). (3) S3 upload event triggers async transcode job (Kafka or Lambda). (4) Transcode workers process in parallel: thumbnail (150×150), feed (1080×1080), full resolution (original), HEVC-compressed video if applicable. (5) Processed outputs written to S3 behind CDN. Transcode workers can be right-sized: CPU-heavy but stateless, easy to scale horizontally. Staff+ failure mode: transcode job fails for one resolution. Store transcode status per (media_id, resolution). Failed resolutions are retried independently. Video is available at successfully-transcoded resolutions while others are processing. User never sees a blank feed card — serve the best available resolution


#### Deep dive 2: Feed generation — hybrid fan-out with celebrity threshold
_The fan-out problem is the defining system design challenge for Instagram. At 2B users with 500M DAU, a celebrity with 50M followers posting once generates 50M Redis write operations for that post_

> [!CAUTION]
> **🔴 Weak** — async workers handle it
>
> [!WARNING]
> **🟡 Strong** — tiered fan-out with explicit threshold. Normal accounts (< 1M followers): fan-out on write — post ID pushed to each follower's Redis sorted set via async Kafka workers. Celebrity accounts (> 1M followers): skip fan-out entirely. At feed read time: user's pre-built feed from Redis + latest N posts from each followed celebrity fetched from Cassandra, merged in-memory
>
> [!TIP]
> **🟢 Staff+** — the threshold is not binary — it's a function of follower count and current fan-out worker lag. Monitor lag: if fan-out workers fall behind (>2 min), dynamically lower the threshold to reduce load. The merge at read time: user follows 3 celebrities, each contributes last 20 posts = 60 post candidates + 980 from pre-built feed. Sort by timestamp, serve top 50. Merge cost: O(C × log C) where C is typically < 10, negligible


#### Deep dive 3: CDN strategy for media delivery at global scale
> [!CAUTION]
> **🔴 Weak** — put one CDN in front of S3, set Cache-Control headers, let it fill naturally
>
> [!WARNING]
> **🟡 Strong** — 99%+ of media reads are served from CDN — this is what makes Instagram's media delivery economically viable
>
> [!TIP]
> **🟢 Staff+** — CDN design: (1) Multi-CDN — use two CDN providers. Route requests to whichever CDN has lower P95 latency for that region (measured by synthetic probes). CDN failover in seconds. (2) Cache warming — for content from large accounts, pre-push content to CDN POPs (Points of Presence) in relevant regions before publication. (3) URL structure encodes resolution: cdn.instagram.com/media/{id}/1080.jpg — CDN can cache all resolutions independently. (4) Signed CDN URLs — prevent hotlinking and unauthorized access. URL includes HMAC signature with expiry. CDN validates signature at edge without origin call. (5) Cache invalidation — when a post is deleted, purge the CDN cache for all resolution variants. CDN APIs support tag-based purge: tag all media variants with the post_id, purge by tag on deletion


_Why the deep dives connect to the scaling problem: "Feed fan-out, media blob delivery, and celebrity hot spots." Each deep dive addresses one dimension._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Media-pipeline-first script.

2. "Clarifying questions: are we designing just photos, or also Reels (video)? And what are the two core use cases — upload + feed read?"

3. "Good — photos plus short video. Core features: upload media, fan-out to followers, serve feed. Out of scope: Stories (different TTL model), Shopping, live streaming."

4. "Scale: 2B users, 500M DAU, 100M uploads/day, 5B feed reads/day. Two big numbers: 1,157 upload QPS and 578K fan-out writes/sec (avg 500 followers × 1,157 uploads/s). Fan-out is the dominant write load."

5. "Upload pipeline: client gets a pre-signed S3 URL — never proxy media bytes through app servers. Client uploads directly. S3 receipt triggers async transcode: thumbnail, 720p, 1080p in parallel. Video is available at 360p within seconds, higher resolutions follow. Never block upload confirmation on full transcode completion."

6. "Fan-out: new post → Kafka → async fan-out workers → write post_id to each follower's Redis sorted set. For accounts above ~1M followers, skip fan-out entirely. At feed read time, pull their recent posts from Cassandra and merge with the pre-built feed."

7. "CDN: all media reads served from CDN. 99%+ hit rate target. For large accounts: pre-warm CDN before publication. Signed CDN URLs with short expiry prevent hotlinking."

8. "Key tradeoff I'd name: the celebrity threshold for fan-out is a config value, not code. Tune it based on observed fan-out worker lag — if workers fall behind, lower the threshold dynamically."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                             |   Mobile / Web    |
                             |      Clients      |
                             +---------+---------+
                                       |
                                       v
                             +-------------------+
                             |    API Gateway    |
                             | auth rate limit   |
                             +----+----+----+----+
                                  |    |    |
                 POST /posts -----+    |    +----- GET /feed
                 POST /follows ---------+
                                       
        +-------------------+    +-------------------+    +-------------------+
        |   Post Service    |    |  Follow Service   |    |   Feed Service    |
        | create post meta  |    | follow unfollow   |    | read feed         |
        +----+---------+----+    +---------+---------+    +----+---------+----+
             |         |                     |                   |         |
             |         |                     |                   |         |
             |         v                     v                   |         v
             |   +-----------+         +-----------+             |   +------------+
             |   |  Posts DB |         | FollowsDB |             |   |   Redis    |
             |   | DynamoDB  |         | DynamoDB  |             |   | feed zset  |
             |   +-----------+         +-----------+             |   | post cache |
             |                                                   |   +-----+------+
             |                                                   |         |
             |                                                   |         v
             |                                                   |   +------------+
             |                                                   +-->|  Posts DB  |
             |                                                       | BatchGet   |
             |                                                       +------------+
             |
             |  presigned upload URL
             v
      +-------------------+        multipart upload        +-------------------+
      |   Blob Storage    |<------------------------------>|      Client       |
      |       S3          |                                +-------------------+
      +---------+---------+
                |
                v
      +-------------------+
      |       CDN         |
      | edge cache media  |
      +---------+---------+
                |
                v
      +-------------------+
      |   Media Delivery  |
      | photos and videos |
      +-------------------+


                 Async fanout path after new post

        Post Service
             |
             v
      +-------------------+
      |   Queue / Topic   |
      | new post events   |
      +---------+---------+
                |
                v
      +-------------------+
      | Feed Fanout Worker|
      | async background  |
      +----+---------+----+
           |         |
           |         v
           |   +-----------+
           |   | FollowsDB |
           |   | followers |
           |   +-----------+
           |
           v
      +-------------------+
      |       Redis       |
      | update feed:user  |
      +-------------------+


                 Celebrity hybrid read path

      Feed Service
           |
           +----> Redis precomputed feed for normal accounts
           |
           +----> Posts DB for recent celebrity posts
           |
           v
      merge by timestamp and return page
```

The mental model is two big flows. Write flow stores post metadata and media, then asynchronously updates follower feeds. Read flow pulls mostly from Redis, then hydrates post metadata from the posts store, with a hybrid read for celebrity accounts.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-16)
