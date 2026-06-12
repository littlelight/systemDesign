# Price tracking (CamelCamelCamel)

**Medium** · Crawl scheduler · Time-series · Alert pipeline

Tags: `Kafka`, `InfluxDB / TimescaleDB`, `Consistent hashing`, `Alert dedup`

## Data flow

A distributed crawler fleet scrapes prices. A consistent hash scheduler assigns products to crawlers stably. Price changes publish to Kafka then fan out to: the TSDB for storage and the Alert Service for watchlist evaluation.


> Consistent hash = stable product→crawler assignment  |  TSDB >> SQL for time-series  |  Dedup: don't re-alert at same price

## Architecture diagram

```
+----------------------+
                         |   Website / Chrome   |
                         |      Extension       |
                         +----------+-----------+
                                    |
                                    v
                           +------------------+
                           |    API Gateway   |
                           | auth rate limit  |
                           +---+----------+---+
                               |          |
                GET price hist |          | POST subscription
                               v          v
                    +----------------+   +-------------------+
                    | Price History  |   | Subscription      |
                    | Service        |   | Service           |
                    +-------+--------+   +---------+---------+
                            |                      |
                            v                      v
                 +---------------------+   +---------------------+
                 |  Price DB           |   | Primary DB          |
                 | time series prices  |   | users products subs |
                 +----------+----------+   +----------+----------+
                            |                         |
                            | new validated price     |
                            v                         |
                      +-------------------+           |
                      | Kafka / Event Bus |<----------+
                      | price change evt  |
                      +---------+---------+
                                |
                                v
                    +--------------------------+
                    | Notification Service     |
                    | find matching subs       |
                    +------------+-------------+
                                 |
                                 v
                       +----------------------+
                       | Email Provider       |
                       +----------------------+


   PRICE COLLECTION SIDE

   +----------------------+        +----------------------+
   | Chrome Extension     |        | Web Crawler Service  |
   | product page views   |        | selective crawling   |
   +----------+-----------+        +----------+-----------+
              |                                 |
              v                                 v
        +-----------------------------------------------+
        | Price Ingestion / Validation Service          |
        | trust but verify suspicious updates           |
        +-------------------+---------------------------+
                            |
                valid price | write
                            v
                     +--------------+
                     |   Price DB    |
                     +------+--------+
                            |
                            | publish price changed
                            v
                     +--------------+
                     | Kafka / Bus   |
                     +--------------+


   OPTIONAL FAST VERIFICATION LOOP

   suspicious extension update
              |
              v
   +------------------------------+
   | Verification Queue           |
   +--------------+---------------+
                  |
                  v
   +------------------------------+
   | Priority Crawler             |
   | checks Amazon quickly        |
   +--------------+---------------+
                  |
                  v
   +------------------------------+
   | Validation Service updates   |
   | trust score and final price  |
   +------------------------------+
```

The main idea is this. Extension plus crawler collect prices, validation decides what to trust, validated price changes go into the price database, and those changes produce events that drive notifications. Separately, the read path for charts stays simple and fast through the Price History Service querying the time series price store.

If you are drawing this in an interview, I would start with just three lanes. Client API read path, data collection path, and notification path. That keeps the whiteboard clean and makes the story easy to explain.


---

<details open>
<summary><strong>Problem</strong></summary>

Helping users know whether an Amazon item is a good deal. Collect prices over time, show history charts, and alert when price drops below a threshold.

The hard part: getting accurate price data for a huge product catalog at scale.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Amazon blocks the crawler (IP ban)**

Price data for all products stops updating. Users miss deals. Trust in the service erodes.

_Fix:_ IP rotation pool (residential proxies). Respect crawl delays. Randomize request timing and user agent strings. Chrome extension as distributed crowdsourced price collection bypasses this entirely for high-priority products.

**Alert fanout overwhelms email service during a major sale (Amazon Prime Day)**

1M users have alerts set for discounted products. All fire simultaneously. Email service throttled.

_Fix:_ Alert queue with rate limiting (SQS). Partition by alert priority (price drop % determines urgency). Batch similar alerts per user (one email per user with all triggered alerts vs. N separate emails).

**Price history TimescaleDB query slow for old products**

3-year price chart takes 5 seconds to load for a product tracked since launch.

_Fix:_ Pre-aggregate price history: daily min/max/avg stored alongside raw data. Charts use aggregated data for time ranges > 30 days. Raw data only for recent window. Query time drops from seconds to milliseconds.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500M products tracked, 1% re-crawled daily = 5M crawls/day, 10M users, 20% have active alerts |
| Read QPS | 10M users × 5 price chart views/day / 86400 ≈ 579 chart read QPS — served from TSDB + cache |
| Write QPS | 5M crawls / 86400 ≈ 58 price updates/s — tiny, but each write triggers alert evaluation |
| Storage | 500M products × 365 days × 2 prices (min/max) × 8 bytes ≈ 2.9 TB/year TSDB — manageable |
| Cache math | Top 1M products get 90% of chart views. Cache price history snapshots: 1M × 10KB = 10 GB Redis. Serves 90% of chart reads from cache. |
| Verdict | Alert fanout on sale events is the peak load problem. 2M alerts firing simultaneously needs queue + rate limiting. Normal operation is low QPS. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Extension-first vs. crawler-first data collection**

→ Chrome extension as primary for user-active products, crawler as secondary

Extension: zero crawl cost, real-time prices when users browse Amazon, no rate limiting concerns. Covers top 1M products users actually care about. Crawler: covers the long tail but at much lower frequency.

_Revisit when:_ Extension alone has coverage gaps (products no active user browses). Crawler is essential for the full catalog.

**Alert evaluation: per-write vs. scheduled polling**

→ Event-driven: Kafka price-change event triggers alert evaluation

Polling all 100M active alerts every 5 minutes = 333K DB queries/s. Event-driven: only evaluate alerts for products that actually changed price (58/s × avg 5 alerts/product = 290 evaluations/s). 1000× more efficient.

_Revisit when:_ Scheduled polling for users who set time-based alerts (e.g., 'alert me if price drops by Monday').

**TSDB vs. PG time-series for price history**

→ InfluxDB / TimescaleDB

Price history is pure time-series: one price per product per time. Never updated (immutable). Queried by product + time range. TSDB's columnar time-partitioned storage gives 10× better query performance than PG for this access pattern.

_Revisit when:_ PG with proper indexing works up to ~50M products. Switch to TSDB when query latency becomes noticeable.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you detect a fake price drop (item marked up then 'discounted')?**

Price history is the answer — you can see the baseline. ML model trained on price history patterns: flag items where 'original price' was only briefly at that level. Show price history chart prominently. Let users judge.

**How would you add support for multiple retailers (BestBuy, Walmart)?**

Abstract crawler behind a retailer interface. Each retailer has its own rate limits, HTML parser, and price extraction logic. Price is always normalized to a canonical schema (product_id, retailer_id, price, currency, timestamp). Product matching across retailers is a separate hard problem (same product, different SKUs).

**How do you keep price data fresh for the most popular products?**

Freshness priority queue: products with the most active alerts and highest user interest get crawled most frequently (every 5 min). Long tail products crawled daily. Extension users provide real-time updates for products they browse, bypassing the crawl priority queue entirely.

**What's your data model for products across multiple retailers?**

Products table: (product_id, canonical_name, category, created_at). Listings table: (listing_id, product_id, retailer_id, retailer_sku, url). Prices table: (listing_id, price, currency, timestamp). One product maps to many listings across retailers. Alerts are on listing_id, not product_id.

**How do you handle price errors (data scraping bugs returning wrong prices)?**

Price validation: flag if new price is >50% different from previous price (likely scraping error). Hold for manual review or second-source confirmation before storing. Never trigger alerts on potentially bad data. Log all validation failures for crawler improvement.

**How do you avoid re-alerting on the same price drop?**

Track last_alert_sent_at and last_alert_price per watchlist. Re-alert only when price recovers above threshold then drops again, or drops by another meaningful delta (e.g., 5%). Prevents notification spam on hover pricing.

**How do you prioritize crawl budget across 500M products?**

Priority queue scored by active alerts + extension page views + sales rank. Top 1M products crawled every 5–15 min; long tail daily or weekly. Budget is a product decision expressed as crawl slots per hour.

**How do you serve price history charts fast for 3-year ranges?**

TimescaleDB continuous aggregates: raw for 7 days, daily min/max for 90 days, weekly for multi-year. Chart API selects rollup tier by requested range — 170× fewer points with no visible quality loss.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Simple crawler polls Amazon hourly for top 10K products. PG stores price history. Email alerts via cron. Works for early users.

**v2 — Scale** — Chrome extension for crowdsourced prices. TSDB for price history. Kafka event-driven alerts. Consistent hash crawler fleet. Handles 50M products.

**v3 — Intelligence** — ML fake-discount detection. Deal score model. Price prediction. Multi-retailer support. Browser extension for all major browsers. Affiliate revenue tracking.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part is not storing prices. It is collecting and updating them at huge scale without overwhelming Amazon or your own system.

There are three main pain points. First, data collection is constrained. You may want to track 500 million products, but you cannot crawl them all frequently because Amazon rate limits scraping, so freshness becomes a resource allocation problem. Second, the workload is very uneven. A small set of products matters a lot more than the long tail, so you need prioritization based on user interest or extension traffic instead of treating every product the same. Third, one price change can create downstream fan-out. A single update may trigger validation, storage, chart updates, and notifications to many subscribers, so the system is not just a crawler. It is also an event processing pipeline.

A good interview summary is this. Price Tracking Service is hard to scale because data ingestion is externally constrained, freshness matters, and each accepted price update can fan out into a lot of follow-on work.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: track product prices, store history, trigger alerts when price crosses threshold, display price charts. Out of scope unless asked: affiliate links, deal scoring, ML price prediction.
- **Two collection strategies** — Chrome extension: real-time, zero crawl cost, covers products users actively browse. Crawler: covers the long tail at lower frequency. Extension data for top 1M products, crawler for the rest.
- **TSDB for price history — not SQL** — Every price change is an immutable append. Queried by (product_id, time_range). InfluxDB or TimescaleDB: columnar, time-partitioned, compression. Plain PostgreSQL at 58 writes/sec × 500M products = wrong tool.
- **Multi-resolution rollups** — Raw price events: keep forever (small — only changes stored, not every poll). Daily rollup: min/max/avg per product per day. Chart rendering: raw for 7-day view, daily for 90-day, weekly for multi-year. Pre-computed by TimescaleDB continuous aggregates.
- **Event-driven alerts** — Price change → Kafka event → Alert Service evaluates watchlists for that product_id only. At 58 changes/sec × 5 alerts/product = 290 evaluations/sec. 1000× cheaper than polling all 100M alerts every 5 min.
- **Trust but verify** — Extension data can be wrong: A/B test pricing, regional variants, scraping errors. Fast-accept for display. Flag if new price differs >50% from previous. Priority-crawl flagged products for verification before storing permanently.
- **Failure mode to name** — Crawler gets IP-banned: rotate residential proxies, respect Crawl-delay, randomize timing. Extension-first strategy means the most important products still get real-time data even if crawler is blocked.

> Open with the data collection split: extension gives real-time coverage for user-active products, crawler covers the long tail. This framing immediately shows you understand the scale constraint.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Coverage vs freshness** — Crawling more products = better coverage, but 500M products cannot all be kept fresh. Answer: extension provides real-time data for user-active products; crawler handles the long tail at lower frequency.

**Polling vs event-driven for alerts** — Polling all 100M active alerts every 5 min = 333K DB queries/sec. Event-driven evaluation triggers only on price-change Kafka events — 290 evaluations/sec at 58 changes/sec. 1000× more efficient.

**Raw data vs rollups for price history charts** — Raw data for a 3-year chart = 26K points per query at full resolution. Daily rollups for >30-day ranges reduce this to 1,095 points — 24× faster query with no visible chart difference at typical chart widths.

**Trust extension data vs verify** — Extension data from user browsers is cheap and real-time but can be wrong (A/B test prices, regional variants). Fast-accept for display, but flag >50% change deviations for crawler re-verification before storing permanently.

> Coverage vs freshness, polling vs event-driven, raw data vs rollups. My defaults: extension-first, event-driven alerts, multi-resolution TSDB. Each choice is driven by the read/write asymmetry.


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Data collection strategy — extension + crawler with trust-but-verify
> [!CAUTION]
> **🔴 Weak** — crawl Amazon every hour for all tracked products
>
> [!WARNING]
> **🟡 Strong** — two-tier collection — Chrome extension gives real-time prices for user-active products at zero crawl cost; crawler covers the long tail at lower frequency. The extension is the primary path for the top 1M products users actually care about
>
> [!TIP]
> **🟢 Staff+** — extension data can be wrong — A/B test prices, regional variants, scraping bugs. Fast-accept for display, but flag price changes >50% from previous as anomalies and trigger a priority crawler re-fetch before storing permanently. This two-layer validation gives low latency for normal updates and high confidence for outliers


#### Deep dive 2: Event-driven alert evaluation — Kafka over polling
> [!CAUTION]
> **🔴 Weak** — cron job every 5 minutes queries all 100M active alerts for products that changed price. This is O(active_alerts) per run = 333K DB queries per second just for alerts. Unacceptable
>
> [!WARNING]
> **🟡 Strong** — event-driven evaluation. When a price update is stored, publish a Kafka event {product_id, old_price, new_price}. Alert service consumes the event and evaluates only alerts for that product_id. At 58 changes/sec × avg 5 alerts/product = 290 evaluations/sec — 1000× more efficient
>
> [!TIP]
> **🟢 Staff+** — the dedup contract — don't re-alert if the price hasn't crossed the threshold fresh. Track last_alert_sent_at per alert; require the price to recover above threshold and drop below again before re-alerting. Without this, a price hovering at the threshold triggers a new alert on every crawl cycle


#### Deep dive 3: TSDB design for price history charts
> [!CAUTION]
> **🔴 Weak** — store every price event in PostgreSQL, query by product_id and time range
>
> [!WARNING]
> **🟡 Strong** — InfluxDB or TimescaleDB — append-only, time-partitioned, columnar compression. But the real win is multi-resolution rollups: raw price events for all-time history, daily min/max/avg for chart display. 7-day view uses raw data; 90-day uses daily rollup; 3-year uses weekly rollup
>
> [!TIP]
> **🟢 Staff+** — pre-compute rollups with TimescaleDB continuous aggregates — they update automatically on every insert, so a 3-year chart query fetches 156 weekly rows instead of 26K raw events. 170× faster query with no visible chart difference at typical chart widths


_Why the deep dives connect to the scaling problem: "Data collection at scale, event-driven processing, and efficient time-series queries." Each deep dive solves one challenge._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Collection-first script.

2. "Clarifying questions: are we tracking prices on a single retailer like Amazon, or multiple? And what's the primary data collection strategy — web crawling, browser extension, or both?"

3. "Good — primarily Amazon, extension plus crawler. Core features: track product prices, store history, trigger threshold alerts, display price charts. Out of scope: affiliate revenue, deal scoring."

4. "Collection strategy: Chrome extension covers products users actively browse — real-time, zero crawl cost. Crawler covers the long tail at lower frequency. Extension-first for the top 1M products; crawler for the rest."

5. "Storage: two databases. PostgreSQL for users, products, subscriptions — relational, low volume. InfluxDB or TimescaleDB for price history — append-only, queried by time range, compressed. Never use PostgreSQL for time-series price data at 500M products."

6. "Multi-resolution rollups: raw price events for all-time history. Daily min/max/avg for chart display. 7-day view uses raw. 90-day view uses daily rollup. 3-year view uses weekly rollup. Pre-computed by TimescaleDB continuous aggregates — query hits the rollup table directly."

7. "Alerts: event-driven via Kafka. Price change event → Alert Service checks watchlists for that product_id. At 58 changes/sec × avg 5 alerts/product = 290 evaluations/sec. 1000× cheaper than polling all 100M active alerts every 5 minutes."

8. "Trust but verify: extension data can be wrong — A/B test prices, regional variants. Fast-accept for display. Flag >50% price changes for priority crawler verification before storing permanently."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+----------------------+
                         |   Website / Chrome   |
                         |      Extension       |
                         +----------+-----------+
                                    |
                                    v
                           +------------------+
                           |    API Gateway   |
                           | auth rate limit  |
                           +---+----------+---+
                               |          |
                GET price hist |          | POST subscription
                               v          v
                    +----------------+   +-------------------+
                    | Price History  |   | Subscription      |
                    | Service        |   | Service           |
                    +-------+--------+   +---------+---------+
                            |                      |
                            v                      v
                 +---------------------+   +---------------------+
                 |  Price DB           |   | Primary DB          |
                 | time series prices  |   | users products subs |
                 +----------+----------+   +----------+----------+
                            |                         |
                            | new validated price     |
                            v                         |
                      +-------------------+           |
                      | Kafka / Event Bus |<----------+
                      | price change evt  |
                      +---------+---------+
                                |
                                v
                    +--------------------------+
                    | Notification Service     |
                    | find matching subs       |
                    +------------+-------------+
                                 |
                                 v
                       +----------------------+
                       | Email Provider       |
                       +----------------------+


   PRICE COLLECTION SIDE

   +----------------------+        +----------------------+
   | Chrome Extension     |        | Web Crawler Service  |
   | product page views   |        | selective crawling   |
   +----------+-----------+        +----------+-----------+
              |                                 |
              v                                 v
        +-----------------------------------------------+
        | Price Ingestion / Validation Service          |
        | trust but verify suspicious updates           |
        +-------------------+---------------------------+
                            |
                valid price | write
                            v
                     +--------------+
                     |   Price DB    |
                     +------+--------+
                            |
                            | publish price changed
                            v
                     +--------------+
                     | Kafka / Bus   |
                     +--------------+


   OPTIONAL FAST VERIFICATION LOOP

   suspicious extension update
              |
              v
   +------------------------------+
   | Verification Queue           |
   +--------------+---------------+
                  |
                  v
   +------------------------------+
   | Priority Crawler             |
   | checks Amazon quickly        |
   +--------------+---------------+
                  |
                  v
   +------------------------------+
   | Validation Service updates   |
   | trust score and final price  |
   +------------------------------+
```

The main idea is this. Extension plus crawler collect prices, validation decides what to trust, validated price changes go into the price database, and those changes produce events that drive notifications. Separately, the read path for charts stays simple and fast through the Price History Service querying the time series price store.

If you are drawing this in an interview, I would start with just three lanes. Client API read path, data collection path, and notification path. That keeps the whiteboard clean and makes the story easy to explain.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-15)
