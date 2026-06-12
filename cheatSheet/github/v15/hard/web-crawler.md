# Web crawler

**Hard** · URL frontier · Politeness · Bloom filter dedup

Tags: `Kafka (URL frontier)`, `Cassandra`, `S3`, `Bloom filter`, `SimHash`, `Politeness`

## Data flow

The URL Frontier is a priority queue with per-domain rate limiting. Crawler workers fetch pages, extract links, respect robots.txt. New links → Dedup Service: Bloom filter for fast probabilistic check, Cassandra for confirmation. Raw HTML → S3.


> Bloom filter = fast prob. check before Cassandra lookup  |  Per-domain queue = politeness

## Architecture diagram

```
+-------------------+
                         |   Seed URL Input  |
                         +---------+---------+
                                   |
                                   v
                    +-------------------------------+
                    | URL Frontier Queue   SQS      |
                    +-------------------------------+
                                   |
                            dequeue URL msg
                                   |
                                   v
         +---------------------------------------------------+
         | URL Fetcher Workers                               |
         | - check URL dedup in Metadata DB                  |
         | - read domain robots rules                        |
         | - acquire per-domain lock in Redis                |
         | - enforce crawl delay and rate limit              |
         | - resolve DNS and fetch page                      |
         +-------------------+-------------------------------+
                             |                    |
                    disallowed or delayed         | fetched HTML
                             |                    v
                             |         +------------------------+
                             |         | Raw HTML Blob Storage  |
                             |         | S3                     |
                             |         +-----------+------------+
                             |                     |
                             |                     v
                             |      +-------------------------------+
                             |      | Processing Queue              |
                             |      | URL id or blob pointer        |
                             |      +---------------+---------------+
                             |                      |
                             |                      v
                             |      +-------------------------------+
                             |      | Text and URL Extraction       |
                             |      | Workers                       |
                             |      | - parse HTML                  |
                             |      | - extract text                |
                             |      | - extract outgoing links      |
                             |      | - hash content for dedup      |
                             |      +----------+----------+---------+
                             |                 |          |
                             |                 |          |
                             |                 |          v
                             |                 |   +-------------------+
                             |                 |   | New URL Discovery |
                             |                 |   +---------+---------+
                             |                 |             |
                             |                 |     check seen URL
                             |                 |     check depth limit
                             |                 |             |
                             |                 |             v
                             |                 |   +--------------------+
                             |                 |   | URL Frontier Queue |
                             |                 |   +--------------------+
                             |                 |
                             |                 v
                             |      +-------------------------------+
                             |      | Text Blob Storage   S3        |
                             |      +-------------------------------+
                             |
                             v
                  +-------------------------------+
                  | Retry with Backoff            |
                  | SQS visibility timeout + DLQ  |
                  +-------------------------------+


     +--------------------+      +----------------------+      +------------------+
     | Metadata DB        |      | Redis                |      | DNS Cache        |
     | - URL state        |      | - per-domain lock    |      | - domain to IP   |
     | - crawl depth      |      | - rate limiting      |      +------------------+
     | - html pointer     |      +----------------------+
     | - text pointer     |
     | - content hash     |
     | - robots rules     |
     | - last crawl time  |
     +--------------------+


 Outside system

     +------------------+        +------------------+
     | DNS Providers    |        | External Websites|
     +------------------+        +------------------+
```

If you are presenting this in an interview, the simplest way to walk through it is this. URLs enter the frontier queue, fetchers crawl pages politely, raw HTML goes to blob storage, parser workers extract text and links, text goes to storage, and new links go back into the frontier after dedup checks.

The two things that make this feel complete are the control plane pieces. Metadata DB tracks crawl state and dedup, while Redis handles per-domain coordination so you do not overload a site.


---

<details open>
<summary><strong>Problem</strong></summary>

Systematically discovering and fetching web pages. Handle billions of URLs, respect site rate limits, avoid re-crawling duplicates, and recover from failures.

Hard parts: politeness, deduplication at scale, and prioritizing fresh content.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Crawler enters a trap (infinite URL space generated dynamically)**

Crawler spends all capacity on one domain. Rest of the web is starved.

_Fix:_ Per-domain URL count limit (e.g., max 1M URLs per domain). Path depth limit (max 10 levels deep). Detect trap patterns: if URLs follow a counter pattern (page?id=1, page?id=2 ... page?id=1000000) — truncate.

**Bloom filter for URL dedup fills up (too many URLs)**

False positive rate rises above acceptable threshold. Legitimate new URLs are incorrectly identified as 'already visited'.

_Fix:_ Monitor Bloom filter load factor. When > 70% capacity: rotate to a new Bloom filter, move old one to cold storage. Use Counting Bloom Filter to support deletions (crawl frequency requires periodic re-crawl). Or: scalable Bloom filter that grows dynamically.

**DNS resolution becomes a bottleneck at high crawl throughput**

At 10K fetches/s, DNS lookups at 100ms each would cap throughput at 10K DNS QPS. DNS servers get hammered.

_Fix:_ Local DNS cache per crawler node (TTL-respecting). Pre-fetch DNS for domains in the near-term crawl queue. DNS cache miss ratio target < 5%.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 5B pages to crawl, re-crawl every 30 days, 100ms avg fetch time (including DNS + TCP + transfer) |
| Read QPS | 5B / (30 × 86400) ≈ 1,929 crawl fetches/s needed. At 100ms avg: 193 concurrent crawlers. |
| Write QPS | 1,929 pages/s → extract ~50 links/page = 96K new URL candidates/s → Bloom filter checks |
| Storage | 5B URLs × 200 bytes (URL + metadata) = 1 TB URL frontier store. Bloom filter for 5B URLs: at 1% false positive rate ≈ 9.6 GB. Raw page content: 5B × avg 100KB = 500 TB. |
| Cache math | Bloom filter: 9.6 GB fits in RAM per node — no DB needed for URL dedup fast path. Cassandra only for confirmation of uncertain positives. |
| Verdict | 96K URL checks/s against the Bloom filter is the high-frequency operation. DNS caching and politeness throttling limit effective throughput more than raw compute. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**BFS vs. priority-based crawl order**

→ Priority queue based on PageRank estimate + freshness + domain authority

Pure BFS treats all pages equally. In practice, CNN.com's homepage is more valuable than a personal blog's page 47. Priority queue maximizes value of crawled content within resource constraints.

_Revisit when:_ BFS is fine for small-scale crawls. Priority becomes critical when crawl budget is limited.

**Centralized URL frontier vs. distributed frontier**

→ Centralized with consistent hash assignment of domains to crawler nodes

Centralized frontier provides global URL dedup and politeness enforcement. Consistent hash: each domain is assigned to one crawler node, which enforces per-domain rate limiting without coordination.

_Revisit when:_ Distributed frontier (each crawler maintains a local queue) reduces coordination overhead but complicates global dedup.

**Respect robots.txt vs. ignore**

→ Always respect robots.txt — no discussion

Legal requirement (most jurisdictions). Practical requirement (violating robots.txt leads to IP bans). Cache robots.txt per domain (TTL = 24h). Check before every fetch.

_Revisit when:_ Never negotiate on this.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle JavaScript-rendered pages (SPAs)?**

Headless browser (Puppeteer/Playwright) for JS rendering. Expensive: ~10× slower than simple HTTP fetch, ~100 MB RAM per instance. Use selectively: only for high-value domains known to require JS rendering. Most crawled content is static HTML.

**How do you detect and handle duplicate content (same content, different URLs)?**

SimHash (Locality Sensitive Hash) of page content. Hamming distance < 3 = near-duplicate. Canonical URL from HTML <link rel='canonical'>. Store canonical URL in URL frontier. Near-duplicate detected after fetch — content is stored but marked as duplicate in the index.

**How do you freshness-schedule re-crawls?**

Change frequency estimation: track how often a page changes (ratio of content changes per re-crawl). High-change pages (news sites): re-crawl every hour. Low-change pages (static content): re-crawl every 30 days. Schedule based on estimated next-change time, not fixed interval.

**How do you handle login-required content?**

Generally: don't crawl login-gated content (no authorization). Exception: explicit partnership with site (authenticated crawling with OAuth credentials). Social network crawlers maintain long-lived sessions with their own accounts.

**How do you distribute 1,929 fetches/s across crawlers fairly?**

Consistent hash assigns domain → crawler node. Each node gets a share of domains. Within a node: token bucket per domain (respects crawl-delay from robots.txt). Global load balancing: if one node falls behind, domain reassignment via consistent hash ring adjustment.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Single-threaded BFS crawler. Python + requests. SQLite for visited URLs. Crawls ~1 page/second. Good for a search engine prototype.

**v2 — Distributed** — Consistent hash URL frontier. Bloom filter dedup. Per-domain politeness queues. Kafka for URL distribution. Cassandra for visited URLs. Handles 1B pages.

**v3 — Production quality** — JS rendering with headless browser pool. SimHash content dedup. Freshness-based re-crawl scheduling. robots.txt cache. Trap detection. Multi-datacenter deployment.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Web Crawler is not storing pages. It is coordinating a huge number of fetches across the public internet without wasting work or being rude to other sites.

There are four scaling pain points you should call out. First, the crawl frontier gets huge. You keep discovering new URLs, but you need to dedupe them so different workers do not crawl the same page again and again. Second, politeness limits parallelism. You may want massive throughput, but you still need per domain rate limits and robots.txt checks, so scaling is not just adding more workers. Third, the internet is unreliable. DNS lookups, slow servers, dead links, retries, and crawler traps all waste time unless you pipeline the work and track progress carefully. Fourth, the workload is very uneven. Some domains are tiny, some are enormous, and some generate endless near-duplicate pages, so load balancing is messy.

A good interview summary is this. Web Crawler is hard because it combines massive frontier management, external bottlenecks like DNS and website limits, duplicate avoidance, and fault tolerance in one system.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: crawl the web, extract text, store pages, enable search indexing. Out of scope unless asked: JavaScript rendering, login-gated content, real-time re-crawl, entity extraction.
- **URL frontier is the core data structure** — Two-tier priority queue. Back queues: one per domain (enforces politeness — one request per domain per N seconds). Front queue: priority-ordered list of domains to crawl next. Never crawl faster than the site allows.
- **Always respect robots.txt** — Legal requirement in most jurisdictions. Cache per domain (TTL 24h). Check before every fetch. Violation = IP ban. State this proactively in interviews — it signals production awareness.
- **Bloom filter for URL dedup** — 96K new URL candidates/sec. DB-only dedup = impossible. Bloom filter (9.6 GB for 5B URLs at 1% FPR): O(1) per check. False positive = occasionally skip a valid URL — acceptable. Cassandra exact check only for Bloom positives.
- **DNS caching per domain** — At 1,929 fetches/sec, uncached DNS at 100ms per lookup = DNS becomes the bottleneck. Cache per domain with TTL (1 hour). Pre-fetch DNS for domains in the near-term frontier queue. DNS miss rate target < 5%.
- **Content dedup — SimHash** — 30-40% of the web is near-duplicate content. SimHash fingerprint: 64-bit, Hamming distance < 3 = near-duplicate. Partition fingerprints by first K bits for efficient lookup (LSH). Canonical URL from <link rel=canonical> takes priority.
- **Failure mode to name** — Crawler enters a trap (infinite URL space): per-domain URL count limit (1M max), path depth limit (10 levels), counter-pattern detection. Without traps, one rogue domain can starve the entire crawl budget.

> Mental model: URL frontier for politeness + priority. Bloom filter + Cassandra for dedup. S3 for raw content.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**BFS vs priority-queue traversal** — BFS discovers breadth-first — finds popular pages sooner but treats all pages equally. Priority queue (weighted by PageRank estimate + domain authority) maximizes crawl value per unit of resource. Priority queue is the production choice.

**Bloom filter vs DB-only for dedup** — DB-only check at 96K URL candidates/sec would require 96K queries/sec — impossible. Bloom filter (9.6 GB for 5B URLs at 1% false positive) handles this in memory at O(1) per check. False positives just mean occasionally skipping a valid URL.

**Centralized frontier vs distributed** — Centralized frontier provides global dedup and politeness enforcement — simpler to reason about. Distributed frontier reduces coordination overhead at extreme scale but complicates dedup and rate limiting. Centralized is correct up to billions of URLs.

**Respect robots.txt vs ignore** — This is not a tradeoff — always respect robots.txt. Legal requirement in most jurisdictions. Practical requirement: violating it leads to IP bans. Cache robots.txt per domain (TTL 24h). State this proactively; it signals production awareness.

> "Bloom filter = fast probabilistic check before the expensive DB lookup. Per-domain rate limiting = politeness. robots.txt = required compliance."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: URL frontier — priority queue, politeness, and domain assignment
> [!CAUTION]
> **🔴 Weak** — a single queue of URLs to crawl in BFS order — simple and correct for small scale
>
> [!WARNING]
> **🟡 Strong** — two-tier priority frontier. Back queues: one per domain, enforces politeness (one request per domain per N seconds, respects robots.txt Crawl-delay). Front queue: priority-ordered list of (domain, priority) pairs. The scheduler picks the highest-priority domain whose next_allowed_crawl_time ≤ now. This ensures politeness and value-maximization simultaneously
>
> [!TIP]
> **🟢 Staff+** — priority = f(PageRank estimate, domain authority, freshness score). High-authority domains (CNN, Wikipedia) get crawled most frequently. Pages within a domain are prioritized by inbound link count. The priority function is configurable — it's the lever that determines what percentage of your crawl budget goes to fresh high-value content vs. long-tail pages


#### Deep dive 2: URL deduplication — Bloom filter + Cassandra two-tier
> [!CAUTION]
> **🔴 Weak** — store all crawled URLs in a database, check before each fetch. At 96K new URL candidates/sec, 96K DB queries/sec for dedup alone — impossible
>
> [!WARNING]
> **🟡 Strong** — Bloom filter as a fast first-pass. At 5B URLs, 1% false positive rate: 9.6 GB memory, 7 hash functions, O(1) per check. False positive = occasionally skipping a valid URL — acceptable tradeoff. Cassandra exact check only for Bloom filter positives (~1% of checks)
>
> [!TIP]
> **🟢 Staff+** — operational concern: Bloom filter fills up. Monitor load factor — alert at 70% capacity, rotate to a new filter. During rotation: new URLs go to the new filter; old filter kept read-only for 30 days to catch duplicates of recently-crawled pages. Counting Bloom filter (supports deletions) if you need to remove URLs that have been re-crawled and should be treated as fresh


#### Deep dive 3: Content deduplication and relevance scoring — SimHash and PageRank
> [!CAUTION]
> **🔴 Weak** — detect exact duplicates using SHA-256 of page content. Misses near-duplicates — the same article republished on 50 domains with minor wording differences all get indexed, wasting storage and diluting search quality
>
> [!WARNING]
> **🟡 Strong** — SimHash fingerprint. Process: tokenize → tf-idf weighted term vector → hash each term with a random 64-bit weight vector → sum → take sign of each bit. Result: 64-bit fingerprint where similar documents have high Hamming similarity. Hamming distance < 3 = near-duplicate
>
> [!TIP]
> **🟢 Staff+** — lookup efficiency: comparing a new fingerprint against all 5B stored fingerprints is O(N). Solution: partition fingerprints into bands (groups of bits) and only compare fingerprints in the same band — Locality Sensitive Hashing. Reduces lookup from O(N) to O(1) average case. For 5B fingerprints × 8 bytes = 40 GB total — fits in a distributed Redis cluster for fast lookups


_Why the deep dives connect to the scaling problem: "Massive frontier, external bottlenecks, duplicate avoidance, fault tolerance." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Frontier-first script.

2. "Clarifying questions: are we building a general-purpose crawler for a search engine, or a focused crawler for a specific domain? And what are the freshness requirements — how often should pages be re-crawled?"

3. "Good — general-purpose, re-crawl high-value pages weekly, low-value monthly. Core features: discover and fetch pages, extract text and links, detect duplicates, feed a search index. Out of scope: JavaScript rendering (unless asked), login-gated content."

4. "The URL frontier is the central data structure — I'd design it first. Two-tier queue: back queues grouped by domain (enforces politeness, one request per domain per N seconds), front queue that priority-selects which domain to crawl next (PageRank + freshness score)."

5. "Always respect robots.txt — legal requirement, and violating it leads to IP bans. Cache per domain with 24-hour TTL. Check before every fetch. State this proactively — it signals production awareness."

6. "URL dedup: Bloom filter first. At 96K new URL candidates/sec, DB-only check is impossible. Bloom filter (9.6 GB for 5B URLs, 1% FPR) handles this in memory at O(1). Cassandra exact-check for Bloom positives only."

7. "Content dedup: SimHash fingerprint per page. Hamming distance < 3 = near-duplicate — skip indexing. Catches the same article reposted on 50 domains. Canonical URL from <link rel=canonical> takes priority."

8. "DNS caching: at 1,929 fetches/sec, uncached DNS at 100ms per lookup makes DNS the bottleneck. Cache per domain with TTL. Pre-fetch DNS for domains in the near-term frontier queue."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                         |   Seed URL Input  |
                         +---------+---------+
                                   |
                                   v
                    +-------------------------------+
                    | URL Frontier Queue   SQS      |
                    +-------------------------------+
                                   |
                            dequeue URL msg
                                   |
                                   v
         +---------------------------------------------------+
         | URL Fetcher Workers                               |
         | - check URL dedup in Metadata DB                  |
         | - read domain robots rules                        |
         | - acquire per-domain lock in Redis                |
         | - enforce crawl delay and rate limit              |
         | - resolve DNS and fetch page                      |
         +-------------------+-------------------------------+
                             |                    |
                    disallowed or delayed         | fetched HTML
                             |                    v
                             |         +------------------------+
                             |         | Raw HTML Blob Storage  |
                             |         | S3                     |
                             |         +-----------+------------+
                             |                     |
                             |                     v
                             |      +-------------------------------+
                             |      | Processing Queue              |
                             |      | URL id or blob pointer        |
                             |      +---------------+---------------+
                             |                      |
                             |                      v
                             |      +-------------------------------+
                             |      | Text and URL Extraction       |
                             |      | Workers                       |
                             |      | - parse HTML                  |
                             |      | - extract text                |
                             |      | - extract outgoing links      |
                             |      | - hash content for dedup      |
                             |      +----------+----------+---------+
                             |                 |          |
                             |                 |          |
                             |                 |          v
                             |                 |   +-------------------+
                             |                 |   | New URL Discovery |
                             |                 |   +---------+---------+
                             |                 |             |
                             |                 |     check seen URL
                             |                 |     check depth limit
                             |                 |             |
                             |                 |             v
                             |                 |   +--------------------+
                             |                 |   | URL Frontier Queue |
                             |                 |   +--------------------+
                             |                 |
                             |                 v
                             |      +-------------------------------+
                             |      | Text Blob Storage   S3        |
                             |      +-------------------------------+
                             |
                             v
                  +-------------------------------+
                  | Retry with Backoff            |
                  | SQS visibility timeout + DLQ  |
                  +-------------------------------+


     +--------------------+      +----------------------+      +------------------+
     | Metadata DB        |      | Redis                |      | DNS Cache        |
     | - URL state        |      | - per-domain lock    |      | - domain to IP   |
     | - crawl depth      |      | - rate limiting      |      +------------------+
     | - html pointer     |      +----------------------+
     | - text pointer     |
     | - content hash     |
     | - robots rules     |
     | - last crawl time  |
     +--------------------+


 Outside system

     +------------------+        +------------------+
     | DNS Providers    |        | External Websites|
     +------------------+        +------------------+
```

If you are presenting this in an interview, the simplest way to walk through it is this. URLs enter the frontier queue, fetchers crawl pages politely, raw HTML goes to blob storage, parser workers extract text and links, text goes to storage, and new links go back into the frontier after dedup checks.

The two things that make this feel complete are the control plane pieces. Metadata DB tracks crawl state and dedup, while Redis handles per-domain coordination so you do not overload a site.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-23)
