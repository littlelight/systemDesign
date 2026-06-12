# YouTube — video platform

**Hard** · Transcoding pipeline · ABR streaming · CDN

Tags: `S3 + CDN`, `Kafka (transcode queue)`, `PostgreSQL`, `Elasticsearch`, `HLS/DASH ABR`, `TUS resumable`

_See also: YouTube Top K · streaming vs ranking_

## Data flow

Upload via TUS resumable protocol. Raw video lands in S3, triggering parallel transcode workers — each resolution processed simultaneously. Output: HLS segments in S3 behind CDN. ABR: client fetches HLS manifest, picks quality based on measured bandwidth.


> ABR: client picks quality tier by bandwidth  |  Transcode = parallel per resolution  |  CDN = #1 cost driver

## Architecture diagram

```
+-------------------+
                         |       Users       |
                         | uploader, viewer  |
                         +---------+---------+
                                   |
                         HTTPS     |
                                   v
                         +-------------------+
                         |   Load Balancer   |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |   Video Service   |
                         | stateless API     |
                         +----+---------+----+
                              |         |
                              |         |
                              |         +----------------------+
                              |                                |
                              v                                v
                 +------------------------+         +---------------------+
                 |   Metadata Cache       |         |   Video Metadata DB |
                 | distributed cache      |         | Cassandra           |
                 +------------------------+         +---------------------+

UPLOAD FLOW
===========

  1. Client asks for upload session and presigned URL

     User ---> Video Service ---> Metadata DB
                    |
                    +--> returns videoId, multipart upload info,
                         presigned URLs

  2. Client uploads video directly to blob storage

     User ---------------------------------------------> S3 Blob Storage
            multipart upload of raw video chunks

  3. Client reports chunk progress

     User ---> Video Service ---> Metadata DB
            chunk uploaded status, ETag info

  4. Upload completion triggers processing

     S3 ObjectCreated event ---> Processing Orchestrator


PROCESSING PIPELINE
===================

                    +-------------------------+
                    | Processing Orchestrator |
                    | DAG workflow manager    |
                    +-----------+-------------+
                                |
          ---------------------------------------------------
          |                         |                        |
          v                         v                        v
 +----------------+        +----------------+      +----------------+
 | Segment Worker |        | Audio Worker   |      | Transcript     |
 | split raw file |        | audio process  |      | Worker         |
 +-------+--------+        +--------+-------+      +--------+-------+
         |                          |                       |
         v                          v                       v
 +----------------+        +----------------+      +----------------+
 | Transcode      |        | audio outputs  |      | transcript out |
 | Workers        |        | in S3          |      | in S3          |
 | many in parallel|       +----------------+      +----------------+
 +-------+--------+
         |
         v
 +------------------------+
 | Manifest Generator     |
 | primary + media files  |
 +-----------+------------+
             |
             v
 +------------------------+
 | S3 processed assets    |
 | segments, manifests    |
 +-----------+------------+
             |
             v
 +------------------------+
 | Metadata DB update     |
 | manifest URL, status   |
 | upload complete        |
 +------------------------+


PLAYBACK FLOW
=============

     User ---> Video Service ---> Cache ---> Metadata DB
                      |
                      +--> returns manifest URL and metadata

     User ---> CDN ---> S3 processed assets
              |         manifests and video segments
              |
              +--> edge serves cached content when possible

     Client playback logic
       - fetch manifest
       - pick bitrate based on network
       - download first segment
       - keep downloading next segments
       - switch quality up or down as bandwidth changes


FULL SYSTEM VIEW
================

                   +-------------------+
                   |       Users       |
                   +---------+---------+
                             |
                             v
                   +-------------------+
                   |   Load Balancer   |
                   +---------+---------+
                             |
                             v
                   +-------------------+
                   |   Video Service   |
                   +---+-----------+---+
                       |           |
                       v           v
              +-------------+   +------------------+
              | Cache       |   | Metadata DB      |
              | popular md  |   | Cassandra        |
              +-------------+   +------------------+

 upload session / metadata |                 ^
                            |                 |
                            v                 |
                      +-------------------------------+
                      | S3 Blob Storage               |
                      | raw uploads                   |
                      | processed segments/manifests  |
                      +---------------+---------------+
                                      |
                         object event |
                                      v
                      +-------------------------------+
                      | Processing Orchestrator       |
                      | workflow / DAG manager        |
                      +---------------+---------------+
                                      |
                           parallel worker fleet
                                      |
             -------------------------------------------------
             |                     |                         |
             v                     v                         v
      +-------------+      +---------------+         +---------------+
      | Split       |      | Transcode     |         | Other media   |
      | workers     |      | workers       |         | workers       |
      +-------------+      +---------------+         +---------------+
                                      |
                                      v
                             +----------------+
                             | Manifest Gen   |
                             +--------+-------+
                                      |
                                      v
                                +-----------+
                                |    CDN    |
                                | edge cache|
                                +-----+-----+
                                      |
                                      v
                                    Users
```

The mental model is two big paths. Upload goes client to S3, then processing pipeline, then metadata update. Watch goes client to metadata, then manifest, then CDN segment fetches.

If you want, I can also give you a smaller interview friendly version that fits in 60 seconds on a whiteboard.


---

<details open>
<summary><strong>Problem</strong></summary>

Hosting and streaming video at YouTube scale — billions of videos, hundreds of millions of simultaneous viewers.

Two hard problems: ingestion and transcoding (raw video → multiple quality levels → CDN), and adaptive bitrate streaming.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Transcode job fails for a specific codec or resolution**

Video uploaded but some quality levels unavailable. Users on slow connections see buffering or error.

_Fix:_ Idempotent transcode jobs with per-resolution retry. Track (video_id, resolution, status) in job table. Failed resolutions re-queued independently. Serve available resolutions while others are processing. Never block video availability on full transcode completion.

**CDN cache miss on first request after upload**

First viewer after upload triggers an origin S3 fetch. For viral videos, thousands of concurrent first-viewers all miss CDN.

_Fix:_ CDN pre-warming for verified/high-subscriber channels: push popular video segments to CDN edge nodes before publication. For cold content: origin shield (a secondary cache layer) reduces origin load from cache miss storms.

**Search index falls behind during a major news event**

Videos about breaking news don't appear in search for 10+ minutes.

_Fix:_ Dedicated fast-lane Kafka topic for recently uploaded videos. High-priority ES indexer with small batch size. Monitor indexing lag with alerting at >60s.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 2B users, 500M DAU, 500 hours of video uploaded/minute, 1B views/day |
| Read QPS | 1B views / 86400 ≈ 11,574 view QPS — but each view = multiple CDN segment requests. CDN absorbs >99% of bandwidth. |
| Write QPS | 500 hours/minute = 30,000 seconds of video/minute = 500 raw video files/min if avg 1hr each ≈ 8 uploads/s |
| Storage | 8 uploads/s × avg 10 GB raw × 86400 = 6.9 PB/day raw. After transcode + compression: ~20% = 1.4 PB/day. Over a year: 500 PB. |
| Cache math | Top 1% of videos (5M) get 95% of views. Cache these 5M × avg 2 GB = 10 PB at CDN edge. CDN hit rate target: >99%. Only 1% of bandwidth hits S3 origin. |
| Verdict | CDN cost is the dominant operating cost. Storage growth (500 PB/year) is the dominant infrastructure planning concern. Transcode compute is significant but one-time per video. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**TUS resumable upload vs. simple multipart upload**

→ TUS protocol (resumable)

Videos are large (1-100 GB). Simple HTTP upload fails on any network interruption — user loses the whole upload. TUS: track chunks independently, resume from last successful chunk. Client-side chunking also enables parallel upload streams.

_Revisit when:_ S3 multipart upload achieves the same result natively. TUS provides a standardized protocol on top.

**HLS vs. DASH for adaptive bitrate**

→ HLS as primary (with DASH for non-Apple platforms)

HLS: Apple-native, required for iOS/Safari. DASH: more flexible, better for DRM, not natively supported in Safari. YouTube actually uses a proprietary format. In interviews: HLS is the safe default answer.

_Revisit when:_ MPEG-DASH if the product needs advanced DRM or non-Apple-centric platform support.

**Transcode: in-house vs. cloud encoding service**

→ Cloud-based transcode (AWS Elemental / GCP Transcoder API)

Building a transcode fleet at scale is a separate product. Elastic scaling for transcode bursts. Cost per minute is higher but operational cost is much lower.

_Revisit when:_ In-house transcode at YouTube's scale (where marginal compute cost savings justify the operational complexity).


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle 4K video playback for users with slow connections?**

ABR (Adaptive Bitrate Streaming): HLS manifest contains multiple quality variants. Player measures available bandwidth and switches to appropriate quality tier. Buffer management: maintain 30s buffer, switch up if buffer > 30s and bandwidth allows, switch down if buffer drops below 10s.

**How do you implement skip-ahead (scrubbing) efficiently?**

HLS segments are typically 6-10 seconds each. Scrubbing skips to the correct segment. For long videos, a thumbnail track (SSTV - spritesheet of thumbnails, one per 10s) is generated during transcode and served separately. Seeking downloads only the segment at the target timestamp, not everything in between.

**How do you handle copyright claims (Content ID)?**

Perceptual hash (pHash) of video fingerprint + audio fingerprint (AcrCloud-style). Compare against Content ID database at upload time. Match → flag for monetization routing or takedown depending on rights holder policy. This is a separate async pipeline, doesn't block video availability.

**How do you serve 1B views per day without S3 being overwhelmed?**

CDN is the answer. S3 origin receives at most 1% of requests (on CDN miss) = 115 QPS to origin. S3 handles this trivially. The expensive part is CDN egress bandwidth cost, not origin request count.

**How would you build offline viewing (download for offline)?**

DRM-protected offline: Widevine (Android) / FairPlay (iOS). User downloads encrypted HLS segments + license key (valid for 30 days). Offline player decrypts using cached key. Key expiry enforces rental window. License server is queried only once per download, not per view.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single video format. FTP upload. Serve from S3 directly. No transcode. Works for a prototype.

**v2 — Scale** — TUS resumable upload. Async transcode pipeline (multiple resolutions). HLS with ABR. CDN for delivery. Elasticsearch for search. Handles millions of uploads.

**v3 — 2B users** — Multi-region deployment. Content ID for copyright. Live streaming as separate pipeline. Shorts/Reels format. Offline download with DRM. 500 PB/year storage.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in YouTube is not storing videos. It is handling huge video files on the upload path and serving smooth playback on the watch path.

There are three main scaling pain points. First, videos are large blobs, so uploads need multipart and resumable transfer straight to blob storage instead of passing through app servers. Second, playback is bandwidth sensitive. Users have different devices and network quality, so you usually split videos into small segments, transcode them into multiple qualities, and let the client switch between them during playback. Third, reads are extremely skewed. A video is uploaded once but may be watched millions of times, so popular videos create hot spots and you need CDN caching for segments and manifests plus caching for metadata.

The extra wrinkle is post processing. One upload turns into a pipeline that splits, transcodes, and writes many output files, which is a lot of CPU work even before anyone watches the video. So the short interview answer is this. YouTube is hard to scale because it combines large file uploads, expensive video processing, and massive read heavy streaming with hot viral traffic.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **TUS resumable upload** — Chunked upload protocol. If upload fails, resume from last successful chunk.
- **Async parallel transcode** — S3 upload triggers a job. Separate workers for each resolution run in parallel. Output: HLS/DASH segments.
- **CDN = cost driver** — CDN absorbs 99%+ of bandwidth. App servers handle only metadata APIs.
- **ABR streaming** — HLS manifest lists quality levels. Client measures bandwidth and requests the appropriate tier.
- **Elasticsearch for search** — Full-text search over titles and descriptions.

> Transcode is parallelizable — all resolutions at once. CDN absorbs 99% of bandwidth. ABR = client picks quality tier.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Parallel vs sequential transcode** — Parallel transcode per resolution is faster for users (360p available in seconds) and uses elastic compute efficiently. Sequential is cheaper but blocks all resolutions behind the slowest. Parallel always wins for UX.

**HLS vs DASH** — HLS is Apple-native, required for iOS/Safari, widely supported. DASH is more flexible for DRM and non-Apple platforms. HLS is the safe interview default; mention DASH if multi-platform DRM is required.

**CDN TTL vs purge on delete** — Long CDN TTL (24h+) maximizes hit rate and reduces origin cost. Deleted or copyright-struck videos must be purged instantly. Tag-based CDN purge (all segments of a video_id) reconciles both — long TTL by default, instant purge on demand.

**TUS resumable vs simple multipart upload** — Simple HTTP upload fails silently on any network drop — user loses the entire upload. TUS tracks chunks independently, resumes from last committed chunk. For files up to 100 GB on mobile connections, resumability is not optional.

> "Transcode parallelism, CDN as cost driver, and ABR for quality selection are the three concepts that define this system."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Upload pipeline — TUS resumable protocol and parallel transcode
> [!CAUTION]
> **🔴 Weak** — standard HTTP multipart upload, transcode sequentially through all resolutions. A 10 GB upload that fails at 9.9 GB restarts from zero. Sequential transcode means 360p isn't available until 4K finishes — user waits minutes before their video is watchable
>
> [!WARNING]
> **🟡 Strong** — TUS resumable protocol. Client splits the file into 10 MB chunks, tracks each independently. On network failure: resume from the last acknowledged chunk. Parallel transcode: one Flink job per resolution running simultaneously on separate worker instances. 360p is available within seconds of upload completion; 4K follows asynchronously
>
> [!TIP]
> **🟢 Staff+** — : transcode job fails for a specific resolution. Store transcode status per (video_id, resolution). Failed resolutions retry independently. Video is available at successful resolutions while others process. Never block video availability on full transcode completion — this is the difference between a 2-minute and a 20-minute time-to-publish


#### Deep dive 2: Adaptive bitrate streaming — HLS manifest and quality switching
> [!CAUTION]
> **🔴 Weak** — serve one video quality to all users — high quality for everyone, or low quality to save bandwidth
>
> [!WARNING]
> **🟡 Strong** — HLS adaptive bitrate streaming. The master manifest (.m3u8) lists all quality variants with bandwidth requirements. Client downloads the master manifest, picks initial quality based on current bandwidth estimate, then switches dynamically per segment
>
> [!TIP]
> **🟢 Staff+** — switching logic: switch up if measured bandwidth > 1.3× current bitrate AND buffer > 30 seconds; switch down if measured bandwidth < 0.8× current bitrate OR buffer drops below 10 seconds. This asymmetry (requires more headroom to switch up than to switch down) prevents oscillation — the player doesn't constantly flip between quality tiers on variable connections. Server-side: HLS segments stored in S3 with deterministic URL structure /{video_id}/{resolution}/{segment_number}.ts. The CDN pre-fetches upcoming segments during playback because the next segment URL is predictable


#### Deep dive 3: CDN architecture — cache warming, multi-CDN, and origin shielding
> [!CAUTION]
> **🔴 Weak** — put a CDN in front of S3 and set a long cache TTL
>
> [!WARNING]
> **🟡 Strong** — multi-layer CDN strategy. (1) Multi-CDN routing: two providers, DNS routes to the one with lower measured P95 latency for that region. Failover in seconds. (2) Origin shielding: instead of all CDN POPs fetching independently from S3 on a miss, a small set of shield POPs (10-20 globally) fetch from S3 and local POPs fetch from the shield. Reduces S3 request rate from O(POPs × misses) to O(shields × misses)
>
> [!TIP]
> **🟢 Staff+** — cache warming: for large channels, the CDN push API pre-populates segments at relevant POPs before the video goes live. No cold cache for the first million viewers of a major release. Purge strategy: deleted or copyright-struck videos need instant CDN purge across all POPs. Tag-based purge: all segments of a video_id are tagged at upload time, purged with a single API call on deletion


_Why the deep dives connect to the scaling problem: "Large blob uploads, expensive transcoding, massive read-heavy streaming." Each deep dive addresses one layer._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Upload then stream framing.

2. "I'll cover two flows: video upload + transcoding, and video playback with ABR."

3. "Upload: creator uses TUS resumable protocol — chunks the file, tracks progress, resumes if interrupted. Raw video lands in S3."

4. "Transcoding: S3 upload triggers a processing job. Separate worker instances process each resolution in parallel — 360p, 720p, 1080p, 4K simultaneously. Output: HLS segments and manifest in S3 behind CDN."

5. "Playback: viewer requests a video. Video API returns metadata and a CDN URL for the HLS manifest. Client fetches the manifest, measures available bandwidth, and requests the appropriate quality. ABR switches quality as bandwidth changes."

6. "CDN is the critical infrastructure here — absorbs 99%+ of all bandwidth. App servers handle only API calls."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |       Users       |
                         | uploader, viewer  |
                         +---------+---------+
                                   |
                         HTTPS     |
                                   v
                         +-------------------+
                         |   Load Balancer   |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         |   Video Service   |
                         | stateless API     |
                         +----+---------+----+
                              |         |
                              |         |
                              |         +----------------------+
                              |                                |
                              v                                v
                 +------------------------+         +---------------------+
                 |   Metadata Cache       |         |   Video Metadata DB |
                 | distributed cache      |         | Cassandra           |
                 +------------------------+         +---------------------+

UPLOAD FLOW
===========

  1. Client asks for upload session and presigned URL

     User ---> Video Service ---> Metadata DB
                    |
                    +--> returns videoId, multipart upload info,
                         presigned URLs

  2. Client uploads video directly to blob storage

     User ---------------------------------------------> S3 Blob Storage
            multipart upload of raw video chunks

  3. Client reports chunk progress

     User ---> Video Service ---> Metadata DB
            chunk uploaded status, ETag info

  4. Upload completion triggers processing

     S3 ObjectCreated event ---> Processing Orchestrator


PROCESSING PIPELINE
===================

                    +-------------------------+
                    | Processing Orchestrator |
                    | DAG workflow manager    |
                    +-----------+-------------+
                                |
          ---------------------------------------------------
          |                         |                        |
          v                         v                        v
 +----------------+        +----------------+      +----------------+
 | Segment Worker |        | Audio Worker   |      | Transcript     |
 | split raw file |        | audio process  |      | Worker         |
 +-------+--------+        +--------+-------+      +--------+-------+
         |                          |                       |
         v                          v                       v
 +----------------+        +----------------+      +----------------+
 | Transcode      |        | audio outputs  |      | transcript out |
 | Workers        |        | in S3          |      | in S3          |
 | many in parallel|       +----------------+      +----------------+
 +-------+--------+
         |
         v
 +------------------------+
 | Manifest Generator     |
 | primary + media files  |
 +-----------+------------+
             |
             v
 +------------------------+
 | S3 processed assets    |
 | segments, manifests    |
 +-----------+------------+
             |
             v
 +------------------------+
 | Metadata DB update     |
 | manifest URL, status   |
 | upload complete        |
 +------------------------+


PLAYBACK FLOW
=============

     User ---> Video Service ---> Cache ---> Metadata DB
                      |
                      +--> returns manifest URL and metadata

     User ---> CDN ---> S3 processed assets
              |         manifests and video segments
              |
              +--> edge serves cached content when possible

     Client playback logic
       - fetch manifest
       - pick bitrate based on network
       - download first segment
       - keep downloading next segments
       - switch quality up or down as bandwidth changes


FULL SYSTEM VIEW
================

                   +-------------------+
                   |       Users       |
                   +---------+---------+
                             |
                             v
                   +-------------------+
                   |   Load Balancer   |
                   +---------+---------+
                             |
                             v
                   +-------------------+
                   |   Video Service   |
                   +---+-----------+---+
                       |           |
                       v           v
              +-------------+   +------------------+
              | Cache       |   | Metadata DB      |
              | popular md  |   | Cassandra        |
              +-------------+   +------------------+

 upload session / metadata |                 ^
                            |                 |
                            v                 |
                      +-------------------------------+
                      | S3 Blob Storage               |
                      | raw uploads                   |
                      | processed segments/manifests  |
                      +---------------+---------------+
                                      |
                         object event |
                                      v
                      +-------------------------------+
                      | Processing Orchestrator       |
                      | workflow / DAG manager        |
                      +---------------+---------------+
                                      |
                           parallel worker fleet
                                      |
             -------------------------------------------------
             |                     |                         |
             v                     v                         v
      +-------------+      +---------------+         +---------------+
      | Split       |      | Transcode     |         | Other media   |
      | workers     |      | workers       |         | workers       |
      +-------------+      +---------------+         +---------------+
                                      |
                                      v
                             +----------------+
                             | Manifest Gen   |
                             +--------+-------+
                                      |
                                      v
                                +-----------+
                                |    CDN    |
                                | edge cache|
                                +-----+-----+
                                      |
                                      v
                                    Users
```

The mental model is two big paths. Upload goes client to S3, then processing pipeline, then metadata update. Watch goes client to metadata, then manifest, then CDN segment fetches.

If you want, I can also give you a smaller interview friendly version that fits in 60 seconds on a whiteboard.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-22)
