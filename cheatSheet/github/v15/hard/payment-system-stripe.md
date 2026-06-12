# Payment system (Stripe)

**Hard** · Idempotency · Outbox pattern · Double-entry ledger

Tags: `PostgreSQL ACID`, `Idempotency key`, `Outbox pattern`, `Double-entry accounting`

## Data flow

Client sends a UUID idempotency key. API checks if the key exists in DB first. If yes, return cached response. If no, one ACID transaction: debit buyer, credit merchant, write webhook row to the outbox table — all in the same transaction. Async outbox worker delivers the webhook.


> Idempotency key = retry-safe (no double charge)  |  Outbox in same TX = no lost webhooks  |  Double-entry: debit buyer, credit merchant

## Architecture diagram

```
+--------------------+
                         |   Merchant Backend |
                         |  uses API keys     |
                         +---------+----------+
                                   |
                                   | HTTPS
                                   v
+-------------+            +-------+--------+
| Customer    |            |   API Gateway   |
| Browser     |            | auth, routing,  |
| checkout UI |            | rate limiting   |
+------+------+            +---+---------+---+
       |                       |         |
       | card entry via        |         |
       | hosted iframe / SDK   |         |
       v                       |         |
+------+-----------------------+         |
| Secure Payment SDK / iFrame  |         |
| card data goes to processor  |         |
+--------------+---------------+         |
               |                         |
               | encrypted card data     |
               |                         |
               v                         v
      +--------+---------+      +--------+---------+
      | Transaction      |      | PaymentIntent    |
      | Service          |      | Service          |
      | creates charge   |      | create/read      |
      | records          |      | payment intent   |
      +----+--------+----+      +----+--------+----+
           |        |                |        |
           |        +----------------+        |
           |             read/write           |
           v                                  v
      +---------------------------------------------+
      |           Operational Database              |
      | merchants, payment_intents, transactions,   |
      | attempts, statuses                          |
      +-------------------+-------------------------+
                          |
                          | CDC from DB log
                          v
                 +--------+---------+
                 |   Kafka / Event  |
                 |   Stream         |
                 | immutable events |
                 +---+----+----+----+
                     |    |    |
                     |    |    |
                     |    |    +------------------+
                     |    |                       |
                     |    v                       v
                     |  +---------+        +-------------+
                     |  | Audit   |        | Webhook     |
                     |  | Service |        | Service     |
                     |  | history |        | notify      |
                     |  +----+----+        | merchants   |
                     |       |             +------+------+ 
                     |       |                    |
                     |       v                    | HTTPS POST
                     |  +---------+               v
                     |  | Cold    |        +-------------+
                     |  | Storage |        | Merchant    |
                     |  | S3 etc  |        | Webhook URL |
                     |  +---------+        +-------------+
                     |
                     v
             +-------+--------+
             | Reconciliation |
             | Service        |
             | resolves       |
             | timeouts       |
             +-------+--------+
                     |
                     | query status / batch files
                     v
          .---------------------------------------.
          | External Payment Networks and Banks   |
          | Visa, Mastercard, issuing banks       |
          '---------------------------------------'
```

The mental model is simple. PaymentIntent Service manages the customer payment lifecycle, Transaction Service talks to the outside payment world, and the database plus CDC plus Kafka gives you a durable history so you do not lose money movement events.

If you are drawing this in an interview, you can start with just five boxes. Merchant, API Gateway, PaymentIntent Service, Transaction Service, Database. Then add CDC, Kafka, Reconciliation, and Webhooks only if the interviewer pushes on durability or async safety.


---

<details open>
<summary><strong>Problem</strong></summary>

Processing payments reliably without double-charging.

Hard parts: idempotency (retries must not double-charge), atomic webhook delivery (outbox pattern), and double-entry accounting.

</details>


<details>
<summary><strong>Failures</strong></summary>

**PSP (Stripe/Adyen) times out — did the charge go through or not?**

Unknown state: Robinhood's DB shows PENDING, exchange may have charged customer.

_Fix:_ Never assume timeout = failure. Query PSP for status of the idempotency key before retrying. If PSP confirms charge: mark SUCCEEDED. If not found: retry. Idempotency key ensures PSP treats retry as same request.

**Outbox worker fails to deliver webhook after many retries**

Merchant never receives payment confirmation. Their system shows order as unpaid. Customer service nightmare.

_Fix:_ Dead letter queue for permanently failed webhooks (after N retries with exponential backoff). Alert merchant to check their endpoint. Provide webhook delivery history in merchant dashboard. Always store raw webhook payload for manual re-delivery.

**Database splits (partition) during payment processing**

Payment accepted on one DB partition, not visible on the other. Double charges on recovery.

_Fix:_ PG with synchronous replication: leader + synchronous standby. No asynchronous replica for payment writes. Synchronous replication means partition = unavailability, not inconsistency. Availability sacrifice is correct here — better to fail than to double-charge.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 1M merchants, 10K TPS peak (Black Friday), avg transaction value $50 |
| Read QPS | 10K TPS × 1 idempotency key lookup = 10K DB reads/s |
| Write QPS | 10K TPS × (1 payment + 1 outbox row + 1 event log row) = 30K DB writes/s — needs write sharding |
| Storage | Event log: 10K events/s × 300 bytes × 86400 × 365 ≈ 95 TB/year — shard by merchant_id |
| Cache math | Idempotency key cache: 10K active keys × 200 bytes = 2 MB Redis — trivial. Payment state cache: 100K active payments × 500 bytes = 50 MB — also trivial. |
| Verdict | 30K write/s hits PG's practical ceiling (~50K writes/s per node). At Black Friday peak, need write sharding by merchant_id. This is the scaling decision that matters. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Synchronous vs. async PSP call**

→ Synchronous with timeout (2s) + async reconciliation

Users expect immediate feedback ('payment accepted'). PSP async webhooks can arrive 30+ seconds later. Synchronous with a 2s timeout: if PSP responds in 2s (usually), user gets immediate confirmation. If timeout: mark PENDING, reconcile asynchronously, show user 'processing'. Best of both.

_Revisit when:_ Pure async for batch payment use cases where immediate confirmation isn't needed (B2B invoicing).

**Single currency vs. multi-currency ledger**

→ Ledger in minor units of each currency (cents, pence, etc.)

Never store monetary amounts as floating point. $1.50 in the ledger = 150 (cents). Arithmetic stays integer. Display layer divides by minor unit exponent. Currency in the event row prevents conversion errors.

_Revisit when:_ Convert to a single base currency (USD) at storage time if multi-currency reporting is required. Store both original currency and base currency amount.

**Distributed ledger vs. single PG shard**

→ Single PG shard per merchant cluster, sharded by merchant_id

Cross-shard transactions in distributed ledgers are hard (2PC). Merchant's payments are independent — no cross-merchant transactions. Shard by merchant_id: all of a merchant's payments are on one shard, enabling ACID single-shard transactions.

_Revisit when:_ Marketplace payments (split payments to multiple merchants) require cross-shard coordination — this is the hard problem in payment system design.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you handle a refund that crosses a billing period?**

Refund is a new event in the event log: negative debit on customer account, negative credit on merchant account. It doesn't modify the original payment event (immutable). Revenue recognition in accounting handles the period crossing — that's an accounting problem, not a systems problem. Event log has all the data needed.

**How do you prevent money from being created or destroyed (double-entry integrity)?**

Invariant: sum of all debit events + sum of all credit events = 0. Run this audit query periodically (daily batch job). If non-zero: alert on-call, freeze affected accounts, investigate. This is why double-entry accounting is non-negotiable — it's self-auditing.

**How would you handle marketplace payments (buyer pays seller, platform takes a cut)?**

Three-leg transaction: debit buyer, credit seller (minus platform fee), credit platform (the fee). All three legs in one ACID transaction within one shard. If buyer and seller are on different shards: saga pattern (reserve buyer funds → credit seller → commit buyer debit). Two-phase saga with compensating transactions on failure.

**How do you prevent a merchant from issuing more refunds than they received in payments?**

Available balance check before every refund: available_balance = sum(payments) - sum(refunds) - sum(pending_refunds). If refund amount > available_balance: reject. This check is within the same ACID transaction as the refund event creation. No race condition.

**How would you implement subscription billing?**

Subscription is a scheduled payment: cron-triggered job that runs the payment pipeline on the renewal date. Idempotency key = subscription_id + billing_period. Failed charge: retry 3× over 3 days (dunning). After 3 failures: suspend subscription, notify user. Same payment infrastructure, different trigger mechanism.

**What metrics and alerts would you put on this system?**

Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). Business metrics: end-to-end latency, consistency lag, fan-out depth. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.

**How would you test and roll out changes safely?**

Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. Canary 1% → 10% → 100% with automatic rollback on error-rate regression.

**How do you handle a regional outage or disaster recovery?**

Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: active-active or warm standby; conflict resolution on merge. Async replication to secondary region; DNS/geo routing failover. Run game days. Document degraded mode — what features drop vs what must stay up.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — MVP** — Stripe API calls directly. No event log. Simple payment table. Works for early revenue. No audit trail, no retry safety.

**v2 — Financial correctness** — Event sourcing / double-entry ledger. Idempotency keys. Outbox pattern for webhooks. Reconciliation job. Handles SMB merchant volume.

**v3 — Enterprise scale** — Shard by merchant_id. Marketplace split payments (saga). Multi-currency. Subscription billing engine. Regulatory compliance (PCI DSS, SOC2). Fraud detection ML.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

The hard part in a Payment System is that every request involves real money, so you need both scale and correctness at the same time. A slow feed can be annoying. A duplicated or lost payment is a business disaster.

There are three big scaling pain points. First, the write path is safety critical. At 10k plus TPS, you are creating and updating payment records fast, but you also need idempotency so retries do not double charge a customer. Second, the workflow is asynchronous because external payment networks can timeout or respond later, so your system must track uncertain states and reconcile later instead of assuming success or failure immediately. Third, durability and auditability matter much more than in a normal app. You cannot just keep the latest row state. You need a full history of what happened so you can recover, reconcile, and answer disputes.

A good interview summary is this. Payment Systems are hard to scale because they combine high write throughput, strict financial correctness, and unreliable external dependencies. You are not just processing requests quickly. You are making sure money movement is never lost, duplicated, or misreported.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Scope it first** — Core: accept payments, charge PSP, deliver webhooks to merchants, maintain ledger. Out of scope unless asked: subscriptions, marketplace split payments, refunds (unless asked), tax calculation.
- **Idempotency key — store BEFORE PSP call** — Client sends UUID. Server stores key as PENDING before calling PSP. On retry: find PENDING → query PSP by idempotency key → update to COMPLETED. If stored after PSP call: crash between them → retry creates duplicate charge.
- **Outbox in the same ACID transaction** — INSERT INTO payments + INSERT INTO outbox_events in one transaction. Either both commit or neither does. Async worker delivers the webhook. Eliminates lost webhooks without distributed transactions.
- **Double-entry accounting — immutable events** — Every payment = debit buyer + credit merchant in one transaction. Balance = sum(events). Never UPDATE a balance field. Invariant: sum(all debits + credits) = 0. Auditable, replayable, legally required.
- **Shard by merchant_id** — All of a merchant's payments on one shard → single-shard ACID for all their transactions, no 2PC needed. Marketplace split payments (cross-merchant) use saga pattern with compensating transactions.
- **Reconciliation job** — Nightly batch: compare internal ledger against PSP settlement report. Flag any discrepancy for manual review. Financial systems drift — reconciliation catches what the application logic missed.
- **Failure mode to name** — PSP call times out — did the charge happen? Never assume timeout = failure. Query PSP by idempotency key for status. If PSP confirms: mark COMPLETED. If not found: retry. If ambiguous: hold payment in PENDING and alert ops.

> Idempotency key = no double charges. Outbox = no lost webhooks. Double-entry = correct accounting.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Outbox vs direct webhook call** — Direct webhook after DB commit = webhook lost if process crashes between commit and HTTP call. Outbox written in the same ACID transaction = committed atomically. Async worker delivers reliably with retries. Outbox is the only correct pattern for reliable event delivery.

**Synchronous PSP call vs async queue** — Sync PSP call is simpler and gives immediate user feedback — PSP typically responds in <500ms. Async queue adds latency for the common case. Use sync with a 2s timeout; on timeout mark PENDING and reconcile async. Best of both.

**Shard by merchant_id vs global single DB** — All of a merchant's payments on one shard enables ACID single-shard transactions — no 2PC needed. Cross-merchant transactions (marketplace split payments) are the hard case — handle with saga pattern. Never shard randomly; shard by the transaction's natural ownership boundary.

**Idempotency key stored before vs after PSP call** — Storing the key after the PSP call: if the process crashes between PSP success and DB write, retry creates a duplicate charge. Store the key (status=PENDING) BEFORE the PSP call — on retry, find PENDING key, query PSP for status, update to COMPLETED. Sequence matters.

> "Idempotency key prevents double charges. Outbox in the same transaction prevents lost webhooks. Non-negotiable in financial systems."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

The three deep dives that matter most for this system, ordered by what interviewers probe hardest.

#### Deep dive 1: Idempotency — preventing duplicate charges under network failures
> [!CAUTION]
> **🔴 Weak** — check if the payment already exists before processing. At 10K TPS with network timeouts, you can't reliably distinguish "never received" from "received and failed" without an idempotency key
>
> [!WARNING]
> **🟡 Strong** — client generates UUID before sending any payment request. Server stores the key with status=PENDING before calling the PSP. On retry: find PENDING key → query PSP by the same idempotency key → update to COMPLETED or FAILED
>
> [!TIP]
> **🟢 Staff+** — sequence detail: storing the key AFTER the PSP call is a critical mistake. If the process crashes between PSP success and DB write, the retry has no key to find — it creates a new charge. Store the key BEFORE the PSP call unconditionally. On retry: find PENDING key, call PSP with same idempotency key (PSP deduplicates on its end too), update status. The entire chain is idempotent end-to-end. This sequence is the single most important correctness detail in payment system design


#### Deep dive 2: Outbox pattern — guaranteed webhook delivery without distributed transactions
> [!CAUTION]
> **🔴 Weak** — after recording the payment in the DB, make an HTTP call to deliver the webhook. If the process crashes between DB commit and HTTP call, the webhook is lost. The merchant never learns about the payment
>
> [!WARNING]
> **🟡 Strong** — outbox pattern. In the same ACID transaction that records the payment: INSERT INTO outbox (event_type, payload, status). Either both the payment and the outbox entry commit, or neither does. An async worker polls the outbox for PENDING entries and delivers them with retries and exponential backoff
>
> [!TIP]
> **🟢 Staff+** — idempotency for webhooks: the webhook payload must include an idempotency key. The merchant's endpoint may receive the same webhook multiple times (delivery retry after a timeout). If the merchant's system isn't idempotent, a payment can be processed twice on their end. Document this contract explicitly: webhook delivery is at-least-once, merchant endpoints must be idempotent


#### Deep dive 3: Multi-step payment flow — saga pattern for distributed transactions
> [!CAUTION]
> **🔴 Weak** — use a distributed transaction (2PC) across shards to atomically debit the buyer and credit the seller. 2PC is slow, blocks resources during the coordinator phase, and is prone to blocking on coordinator failure
>
> [!WARNING]
> **🟡 Strong** — saga pattern. A saga is a sequence of local transactions, each with a compensating transaction on failure. Payment saga: (1) RESERVE buyer funds (local ACID transaction), (2) CREDIT seller, (3) CAPTURE buyer reservation. On failure at step 2: run compensating transaction for step 1 (release reservation)
>
> [!TIP]
> **🟢 Staff+** — durability: the saga state machine must be stored durably. If the saga orchestrator crashes mid-saga, it must resume from the last committed step on restart — not restart from the beginning (which would double-charge). Store saga state in PostgreSQL with the current step and status. On restart: read uncommitted sagas, resume from last committed step. Sagas are eventually consistent — the DB may briefly be in an intermediate state, but all failures are handled gracefully by the compensating transactions


_Why the deep dives connect to the scaling problem: "High write throughput, strict financial correctness, unreliable external dependencies." Each deep dive addresses one constraint._

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Financial-integrity-first script.

2. "Before I start: are we designing a payment processor like Stripe — where merchants integrate via API — or an end-user checkout flow? And do we need to handle marketplace split payments?"

3. "Good — API-first like Stripe, single merchant for now. Core features: accept payment, call PSP, deliver webhook to merchant, maintain ledger. Out of scope: subscriptions, refunds, multi-currency (unless asked)."

4. "Scale: 10K TPS peak (Black Friday). The hard constraint isn't throughput — it's correctness. A slow feed is annoying. A duplicate charge is a legal problem."

5. "Two non-negotiables I'd state upfront: idempotency and reliable webhook delivery. These are the failure modes that matter in production."

6. "Idempotency: client generates a UUID before sending any payment request. Server checks if this key exists before processing. If yes, return the cached response — no new charge. Key detail: store the idempotency record BEFORE calling the PSP. If stored after and the process crashes in between, retry creates a duplicate charge."

7. "Outbox pattern: in the same ACID transaction that records the payment, write a row to an outbox table. An async worker reads the outbox and delivers the webhook with retries and exponential backoff. Either both the payment and the outbox entry commit, or neither does — no lost webhooks."

8. "Ledger design: double-entry event sourcing. Every payment = debit buyer + credit merchant, both in one transaction. Balance is never a mutable field — it's always derived from the sum of events. Required for regulatory compliance."

9. "Sharding: by merchant_id. All of one merchant's payments on one shard — enables single-shard ACID, no distributed transactions needed."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
+--------------------+
                         |   Merchant Backend |
                         |  uses API keys     |
                         +---------+----------+
                                   |
                                   | HTTPS
                                   v
+-------------+            +-------+--------+
| Customer    |            |   API Gateway   |
| Browser     |            | auth, routing,  |
| checkout UI |            | rate limiting   |
+------+------+            +---+---------+---+
       |                       |         |
       | card entry via        |         |
       | hosted iframe / SDK   |         |
       v                       |         |
+------+-----------------------+         |
| Secure Payment SDK / iFrame  |         |
| card data goes to processor  |         |
+--------------+---------------+         |
               |                         |
               | encrypted card data     |
               |                         |
               v                         v
      +--------+---------+      +--------+---------+
      | Transaction      |      | PaymentIntent    |
      | Service          |      | Service          |
      | creates charge   |      | create/read      |
      | records          |      | payment intent   |
      +----+--------+----+      +----+--------+----+
           |        |                |        |
           |        +----------------+        |
           |             read/write           |
           v                                  v
      +---------------------------------------------+
      |           Operational Database              |
      | merchants, payment_intents, transactions,   |
      | attempts, statuses                          |
      +-------------------+-------------------------+
                          |
                          | CDC from DB log
                          v
                 +--------+---------+
                 |   Kafka / Event  |
                 |   Stream         |
                 | immutable events |
                 +---+----+----+----+
                     |    |    |
                     |    |    |
                     |    |    +------------------+
                     |    |                       |
                     |    v                       v
                     |  +---------+        +-------------+
                     |  | Audit   |        | Webhook     |
                     |  | Service |        | Service     |
                     |  | history |        | notify      |
                     |  +----+----+        | merchants   |
                     |       |             +------+------+ 
                     |       |                    |
                     |       v                    | HTTPS POST
                     |  +---------+               v
                     |  | Cold    |        +-------------+
                     |  | Storage |        | Merchant    |
                     |  | S3 etc  |        | Webhook URL |
                     |  +---------+        +-------------+
                     |
                     v
             +-------+--------+
             | Reconciliation |
             | Service        |
             | resolves       |
             | timeouts       |
             +-------+--------+
                     |
                     | query status / batch files
                     v
          .---------------------------------------.
          | External Payment Networks and Banks   |
          | Visa, Mastercard, issuing banks       |
          '---------------------------------------'
```

The mental model is simple. PaymentIntent Service manages the customer payment lifecycle, Transaction Service talks to the outside payment world, and the database plus CDC plus Kafka gives you a durable history so you do not lose money movement events.

If you are drawing this in an interview, you can start with just five boxes. Merchant, API Gateway, PaymentIntent Service, Transaction Service, Database. Then add CDC, Kafka, Reconciliation, and Webhooks only if the interviewer pushes on durability or async safety.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-26)
