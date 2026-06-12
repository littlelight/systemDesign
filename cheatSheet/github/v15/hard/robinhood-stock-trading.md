# Robinhood — stock trading

**Hard** · Financial integrity · Event sourcing · Market data

Tags: `PostgreSQL ACID`, `Kafka`, `Redis (prices)`, `Idempotency key`, `Event sourcing`

## Data flow

Market data flows exchange → Kafka → Redis price cache. Order placement: client sends an idempotency key → API checks key in DB → one ACID transaction reserves buying power and appends an immutable event log entry → calls Broker API.


> Idempotency key = no double trades on retry  |  Event sourcing: balance derived by replaying events  |  2-phase: reserve → confirm

## Architecture diagram

```
+-------------------+
                          |   Mobile / Web    |
                          |      Clients      |
                          +---------+---------+
                                    |
                     HTTPS for API   |   SSE for live prices
                                    |
                           +--------v--------+
                           |   Load Balancer  |
                           |  sticky for SSE  |
                           +---+----------+---+
                               |          |
                +--------------+          +----------------+
                |                                        |
       +--------v--------+                      +---------v---------+
       |  Order Service  |                      |   Symbol Service  |
       | create cancel   |                      | SSE subscriptions |
       | list orders     |                      | fanout to clients |
       +---+---------+---+                      +----+----------+---+
           |         |                               |          |
           |         |                               |          |
           |         |                      subscribe by symbol |
           |         |                               |          |
           |         |                         +-----v----------v-----+
           |         |                         |      Redis Pub Sub   |
           |         |                         | channels per symbol  |
           |         |                         +-----------+----------+
           |         |                                     |
           |         |                                     |
           |   +-----v------------------+                  |
           |   |  Order DB              |                  |
           |   | relational sharded by  |                  |
           |   | userId                 |                  |
           |   +------------------------+                  |
           |                                               |
           |   +------------------------+                  |
           +-->| ExternalOrderId KV     |<-----------------+
               | externalOrderId ->     |        trade lookup
               | (orderId, userId)      |                  |
               +------------------------+                  |
                                                           |
                                                  +--------v---------+
                                                  | Trade Processor  |
                                                  | consumes exchange|
                                                  | trade feed       |
                                                  +--------+---------+
                                                           |
                              updates price cache          |
                              publishes symbol updates     |
                              updates order state          |
                                                           |
                                             +-------------v--------------+
                                             |       Price Cache          |
                                             | latest price per symbol    |
                                             +-------------+--------------+
                                                           |
                                             initial snapshot for SSE
                                                           |
                                                           |
                     outbound requests through small set of IPs
                                                           |
                                                   +-------v-------+
                                                   | NAT Gateway   |
                                                   | / Egress GW   |
                                                   +-------+-------+
                                                           |
                                              sync place cancel APIs
                                                           |
                                              async trade feed / webhook
                                                           |
                                                   +-------v---------+
                                                   |   Exchange      |
                                                   | order API +     |
                                                   | trade feed      |
                                                   +-----------------+


                    +-----------------------------+
                    | Cleanup Worker              |
                    | scans pending and           |
                    | pending_cancel orders       |
                    | reconciles with exchange    |
                    +-----------------------------+
```

The key idea is that you split the system into two flows. One flow is fast live price distribution through Trade Processor -> Redis -> Symbol Service -> SSE clients. The other flow is consistent order handling through Order Service -> DB first -> Exchange -> reconcile state.

If you draw this in an interview, you should call out three important choices. Use SSE for live prices, use a relational orders database partitioned by userId, and use a small egress layer so you do not open too many direct exchange connections.


---

<details open>
<summary><strong>Problem</strong></summary>

A commission-free stock trading platform. Real-time market prices, place orders, and portfolio accuracy.

Hard parts: idempotency (retries must not produce double trades), financial integrity (double-entry accounting), and event sourcing for audit trail.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Broker API call succeeds but Robinhood server crashes before writing confirmation**

Order placed at exchange but no record in Robinhood's system. Customer's position is wrong. Money at risk.

_Fix:_ Two-phase: write 'pending' order to PG before calling broker. If broker succeeds, update to 'filled'. If Robinhood crashes after broker success: on restart, reconciliation job queries broker for pending orders and updates state. Idempotency key prevents duplicate broker calls.

**Market data Redis cache lags during extreme volatility (circuit breaker event)**

Customers see stale prices. Execute orders at wrong expected prices.

_Fix:_ Price feed has a freshness TTL — if price hasn't updated in 500ms, mark it as stale and show staleness indicator to user. Don't let customers execute orders on prices > 2s old without explicit acknowledgment.

**Order book for a hot stock becomes a hot key in the system**

GME squeeze: millions of users watching and placing orders for the same ticker. Single Redis key for GME price is hammered.

_Fix:_ Ticker-level read sharding: multiple Redis nodes hold the same key, consistent-hash read routing. Write goes to one master, fan-out to replicas. For options chain data (larger): separate read-through cache with CDN for static expirations.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 15M users, 1M DAU, 5 trades/day per active user, 10K price updates/s from exchange |
| Read QPS | 1M users × 20 price checks/day / 86400 ≈ 231 price check QPS — trivial, Redis handles |
| Write QPS | 1M × 5 / 86400 ≈ 58 order writes/s — tiny. Exchange feed: 10K price updates/s to Redis. |
| Storage | Event log: 58 events/s × 300 bytes × 86400 × 365 ≈ 600 GB/year. PG sharded by user_id after 1TB. |
| Cache math | Price cache: 10K active tickers × 100 bytes = 1 MB Redis. Trivial. Options chain cache: 10K tickers × 1KB = 10 MB. Still trivial. |
| Verdict | Financial correctness (not throughput) is the design constraint. 58 orders/s is easy. The hard part is the 2-phase commit with the exchange and the reconciliation job. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Event sourcing vs. mutable balance**

→ Event sourcing with materialized view for balance

Mutable balance: update one field. Simple. But you lose the audit trail — can't reconstruct 'what happened'. Event sourcing: every debit/credit is a row. Balance = sum(events) or maintained as a materialized view updated per-event. Legal and regulatory requirement for financial systems.

_Revisit when:_ Never revert this decision for a financial system. Auditability is non-negotiable.

**Synchronous vs. asynchronous broker API calls**

→ Synchronous with timeout + async reconciliation

Market orders should feel instant (< 500ms feedback). But broker API may be slow. Synchronous with 2s timeout: if timeout, mark order as PENDING, reconcile later. User sees 'order submitted' immediately, confirmation comes async.

_Revisit when:_ Pure async queue for all orders if broker latency is consistently > 500ms.

**Cash equity vs. margin account architecture**

→ Separate account types with separate collateral logic

Cash account: simple debit/credit. Margin account: buying power = cash + margin credit × leverage. Complex collateral math. These are different enough to warrant separate services with shared event log.

_Revisit when:_ Start with cash-only. Add margin as a separate service on top of the same event sourcing infrastructure.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a market halt (trading suspended on an exchange)?**

Market status comes from exchange data feed. When halt received: set market_status = HALTED for that exchange in Redis. Order service rejects new orders with 'market halted' error. Existing open orders: cancel or hold based on halt type (circuit breaker vs. halted for news). Resume when market_status = OPEN.

**How do you handle fractional shares?**

Store shares as integer (millionths of a share). 1 share = 1,000,000 units. All arithmetic in integer math — no floating point for financial values (never use float for money). Display layer divides by 1,000,000 for human-readable output.

**How would you add options trading?**

Options are separate instruments with expiration dates and strike prices. New instruments table. Options chain cache (all strikes for a ticker × all expirations) is larger than equity price cache — pre-fetch and cache with options-specific TTL (changes with time decay). Order book logic same as equity but with additional validation (sufficient collateral for selling options).

**How do you ensure tax lot accounting (FIFO/LIFO) for sells?**

Event log already has all buy events with purchase price and date. On sell: query buy events for this ticker in FIFO order, determine cost basis per lot sold. This is a read operation over the event log — expensive but only triggered on sell. Results cached in a tax lot materialized view.

**What happens during a market close → open gap (overnight price change)?**

Last price cached in Redis with a 'market closed' flag. During pre-market and post-market, update with AH/PM prices but show clearly as AH/PM prices (different from regular market hours). On market open, switch to real-time feed. No functional change to the architecture — just metadata on the price entry.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — PG for everything. Synchronous broker API. Simple balance table. Handles early users. Would fail audit and at any real trading volume.

**v2 — Financial correctness** — Event sourcing for ledger. Idempotency keys. 2-phase order flow. Reconciliation job. Redis price cache. Separate market data pipeline.

**v3 — Scale + products** — Options trading. Margin accounts. Fractional shares. Tax lot accounting. Order routing (multiple brokers). Extended hours trading.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in Robinhood is that it combines real-time market data with high-stakes correctness. You need prices to update fast, but you also need orders and cancels to be reflected accurately because a stale or lost update can cost real money.

There are three main scaling pain points. First, live price fan-out is big. Many users may watch the same symbol at once, so you do not want every client talking to the exchange directly. You usually centralize exchange connections, ingest the feed once, then fan updates out internally to many app servers. Second, order handling is latency sensitive and consistency sensitive at the same time. A user expects a buy or cancel to happen quickly, but you also need durable local state so you can recover if your system talks to the exchange and then crashes mid-flow. Third, the exchange is an external dependency, which makes everything harder. You have limited connections, limited request patterns, and partial failure cases where your database and the exchange can get out of sync.

A good mental model is this. Robinhood is hard because it is part realtime system and part financial workflow engine. The realtime side is about efficiently broadcasting shared price updates. The harder side is making sure order state stays correct across your system and the exchange, even when failures happen.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: view portfolio, get real-time quotes, place market/limit orders, view order history. Out of scope unless asked: options, margin, crypto, tax lot optimization, fractional shares (unless asked).
- **Idempotency key — store before PSP call** — Client generates UUID. Server stores idempotency_key (status=PENDING) BEFORE calling broker. On retry: find PENDING key → query broker for status → update to COMPLETED or FAILED. Sequence prevents double orders.
- **Event sourcing for ledger** — Balance is never stored as a mutable field. Every debit/credit is an immutable event row. Balance = sum(events) materialized by a checkpoint. Required for regulatory compliance — 7-year immutable audit trail.
- **Market data — separate pipeline** — 10K price updates/sec from exchange → Kafka → Redis. App servers subscribe by ticker. Fan-out to users watching that ticker via WebSocket. High-frequency, approximate-OK (1-2s stale is fine for display).
- **Two-phase order execution** — (1) Reserve buying power in PG ledger (ACID). (2) Call broker API with idempotency key. (3) On broker success: commit debit + record fill. On broker timeout: mark PENDING, reconcile async. Never debit before broker confirmation.
- **Fractional shares — integers only** — Store shares as integer units (1 share = 1,000,000 units). All arithmetic stays exact. IEEE 754 float accumulates rounding errors across millions of transactions. Display layer divides by 1M.
- **Failure mode to name** — Broker API times out — did the order go through? Query broker using the idempotency key before retrying. Idempotency key tells the broker this is a retry, not a new order. Never assume timeout = failure for financial operations.

> Mental model: idempotency key prevents double charges. Event sourcing gives immutable audit trail.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Event sourcing vs mutable balance** — Event sourcing = immutable audit trail, temporal queries, full history — required for financial regulatory compliance. Mutable balance is simpler but loses the audit trail. No real choice for a financial system.

**Market data in Redis vs DB** — Redis handles thousands of price updates/sec easily. DB would be overwhelmed. Redis is cache — source of truth is the exchange feed. On Redis failure, re-seed from the feed.

**Synchronous vs async broker API call** — Synchronous with 2s timeout gives users immediate feedback. If timeout occurs: mark PENDING, reconcile async. Pure async queue adds latency for the common case (PSP responds in <500ms). Sync with fallback is the right default.

**Fractional shares as float vs integer** — Storing share quantities as IEEE 754 float introduces rounding errors that compound across millions of transactions. Store as integer (millionths of a share: 1 share = 1,000,000 units). All arithmetic stays exact. Display layer divides.

> "Idempotency key is the #1 concept. Without it, network retries = double orders. Event sourcing = immutable audit log you can't corrupt."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Idempotency — preventing duplicate orders under network retries
> [!CAUTION]
> **🔴 Weak** — check if a similar order exists before placing a new one
>
> [!WARNING]
> **🟡 Strong** — the hardest financial correctness problem: a network timeout means the client doesn't know if the order went through. If the client retries, it might place two orders. At $50 average trade value and 58 orders/second, a 1% duplicate rate = $29,000/second in double-charges. Unacceptable
>
> [!TIP]
> **🟢 Staff+** — design: client-generated idempotency key (UUID v4) included in every order request. Server logic: (1) check if idempotency_key exists in DB — if yes, return the cached response (no new order created). (2) If no, proceed: insert idempotency_key record + create order in one ACID transaction. On broker API call: include the idempotency_key as the broker's own idempotency parameter (Stripe, Alpaca, etc. support this). If broker returns success + idempotency key matches: return cached success. If broker returns error: return cached error. The idempotency record must be stored BEFORE the broker API call — if stored after, a crash between broker success and DB write results in a duplicate on retry. Timing: store idempotency key as PENDING, call broker, update to COMPLETED/FAILED. On retry: if PENDING state found, check broker directly (re-query by the same idempotency key)


#### Deep dive 2: Event sourcing — the immutable ledger for financial compliance
> [!CAUTION]
> **🔴 Weak** — maintain a balance column, UPDATE on every debit and credit
>
> [!WARNING]
> **🟡 Strong** — a mutable balance field (UPDATE accounts SET balance = balance - 100 WHERE id=?) fails regulatory requirements: you cannot reconstruct the history of how the balance arrived at its current value. Event sourcing: every financial movement is an immutable append-only event row: (account_id, event_type, amount, currency, timestamp, order_id, description). The current balance is computed as the sum of all events for an account
>
> [!TIP]
> **🟢 Staff+** — materializing the balance. Recomputing from the full event log on every balance check would be O(N) per query. Solution: maintain a checkpoint table (account_id, balance_as_of_timestamp) updated periodically (e.g., end of day). For live balance: checkpoint + sum of events since checkpoint = current balance. The checkpoint is always derivable from the event log — it's a cache, not the source of truth. If the checkpoint is wrong: recompute from event log (always possible, always correct). Regulatory requirement: event log must be immutable (no UPDATE or DELETE), retained for 7 years


#### Deep dive 3: Market data fan-out — high-frequency prices to millions of users
_Exchange feed delivers 10,000 price updates/second for thousands of tickers. Distributing this to 1M users watching various tickers is a fan-out problem_

> [!CAUTION]
> **🔴 Weak** — WebSocket to every user
>
> [!WARNING]
> **🟡 Strong** — topic-based pub/sub with ticker as the topic. Architecture: Market Data Service subscribes to exchange feed → normalizes updates → publishes to Kafka topics (one per ticker). App servers subscribe to tickers that their connected users are watching. On price update: app server pushes to relevant user WebSocket connections
>
> [!TIP]
> **🟢 Staff+** — app server maintains an in-memory map of (ticker → [user_connection_ids]). On price update event: look up connections for that ticker, push to each. This is a local fan-out within one server — no cross-server coordination needed because connections are affined by ticker. Hot tickers (GME, AAPL during earnings): the app server handling those users gets more messages but handles them without cross-server coordination. The Kafka topic for hot tickers may need multiple partitions to distribute ingestion load


_Why the deep dives connect to the scaling problem: "Real-time system plus financial workflow engine." Deep dive 1 solves order correctness. Deep dive 2 solves auditability. Deep dive 3 solves market data distribution._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Financial-integrity script.

2. "Clarifying questions: are we building the full trading platform — equities, options, crypto — or just equities with market orders? And what are the latency requirements for order execution?"

3. "Good — equities, market and limit orders. Latency target: order submission confirmed to user within 500ms. Core features: view portfolio, get quotes, place orders, view order history. Out of scope: options, margin, tax optimization."

4. "Two non-negotiables for any financial system: idempotency and immutable audit trail. I'd establish both upfront."

5. "Idempotency: client generates UUID before every order request. Server stores idempotency_key as PENDING before calling broker. On retry: find key, query broker for status, update to COMPLETED or FAILED. Store before the call — not after — or a crash between them creates a duplicate order."

6. "Ledger: event sourcing. Every debit and credit is an immutable event row. Balance = sum(events) via a materialized checkpoint. Never UPDATE a balance field. Required for 7-year regulatory audit trail."

7. "Order execution: two-phase. (1) Reserve buying power in PG within an ACID transaction. (2) Call broker API with idempotency key. (3) On broker success: commit the debit and record the fill. On timeout: mark PENDING, reconcile async by re-querying broker."

8. "Market data pipeline: separate concern. Exchange feed → Kafka → Redis. App servers subscribe by ticker symbol. Fan-out to users watching that ticker via WebSocket. High-frequency, eventual consistency OK — 1-2 second staleness is fine for display."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+-------------------+
                          |   Mobile / Web    |
                          |      Clients      |
                          +---------+---------+
                                    |
                     HTTPS for API   |   SSE for live prices
                                    |
                           +--------v--------+
                           |   Load Balancer  |
                           |  sticky for SSE  |
                           +---+----------+---+
                               |          |
                +--------------+          +----------------+
                |                                        |
       +--------v--------+                      +---------v---------+
       |  Order Service  |                      |   Symbol Service  |
       | create cancel   |                      | SSE subscriptions |
       | list orders     |                      | fanout to clients |
       +---+---------+---+                      +----+----------+---+
           |         |                               |          |
           |         |                               |          |
           |         |                      subscribe by symbol |
           |         |                               |          |
           |         |                         +-----v----------v-----+
           |         |                         |      Redis Pub Sub   |
           |         |                         | channels per symbol  |
           |         |                         +-----------+----------+
           |         |                                     |
           |         |                                     |
           |   +-----v------------------+                  |
           |   |  Order DB              |                  |
           |   | relational sharded by  |                  |
           |   | userId                 |                  |
           |   +------------------------+                  |
           |                                               |
           |   +------------------------+                  |
           +-->| ExternalOrderId KV     |<-----------------+
               | externalOrderId ->     |        trade lookup
               | (orderId, userId)      |                  |
               +------------------------+                  |
                                                           |
                                                  +--------v---------+
                                                  | Trade Processor  |
                                                  | consumes exchange|
                                                  | trade feed       |
                                                  +--------+---------+
                                                           |
                              updates price cache          |
                              publishes symbol updates     |
                              updates order state          |
                                                           |
                                             +-------------v--------------+
                                             |       Price Cache          |
                                             | latest price per symbol    |
                                             +-------------+--------------+
                                                           |
                                             initial snapshot for SSE
                                                           |
                                                           |
                     outbound requests through small set of IPs
                                                           |
                                                   +-------v-------+
                                                   | NAT Gateway   |
                                                   | / Egress GW   |
                                                   +-------+-------+
                                                           |
                                              sync place cancel APIs
                                                           |
                                              async trade feed / webhook
                                                           |
                                                   +-------v---------+
                                                   |   Exchange      |
                                                   | order API +     |
                                                   | trade feed      |
                                                   +-----------------+


                    +-----------------------------+
                    | Cleanup Worker              |
                    | scans pending and           |
                    | pending_cancel orders       |
                    | reconciles with exchange    |
                    +-----------------------------+
```

The key idea is that you split the system into two flows. One flow is fast live price distribution through Trade Processor -> Redis -> Symbol Service -> SSE clients. The other flow is consistent order handling through Order Service -> DB first -> Exchange -> reconcile state.

If you draw this in an interview, you should call out three important choices. Use SSE for live prices, use a relational orders database partitioned by userId, and use a small egress layer so you do not open too many direct exchange connections.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-19)
