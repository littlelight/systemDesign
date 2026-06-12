# Digital wallet (Apple Pay)

**Hard** · Tokenization · PCI scope · Double-entry ledger

Tags: `HSM`, `Token vault`, `Ledger`, `3DS`, `Idempotency`, `PCI DSS`

## Data flow

Card numbers are tokenized in a HSM-backed vault — merchants never see PAN. Payments authorize against the payment network with device cryptograms. A double-entry ledger records all money movement immutably.


> PAN never touches merchant  |  Device-bound tokens  |  Ledger is immutable event log

## Architecture diagram

```
App -> Wallet API -> Token Vault (HSM)
              -> Auth svc -> Payment network
              -> Ledger (event log)
```

Vault and ledger are isolated trust boundaries.


---

<details open>
<summary><strong>Problem</strong></summary>

Design a digital wallet: add cards, pay in stores/apps, P2P transfers, with PCI compliance and financial correctness.

Hard parts: tokenization, fraud, and ledger integrity.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Double tap charge**

Duplicate authorization.

_Fix:_ Idempotency key per tap. Network-level dedup token.

**HSM unavailable**

Cannot decrypt tokens — payments fail.

_Fix:_ HSM cluster with failover. No software fallback for PAN ops.

**Ledger imbalance**

Money created/destroyed.

_Fix:_ Batch invariant job. Freeze accounts on mismatch.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500M wallets, 5 tx/user/day, $25 avg |
| Read QPS | Balance reads: 500M×2/86400 ≈ 12K/s |
| Write QPS | 5×500M/86400 ≈ 29K auth/s peak |
| Storage | Event log grows ~100B events/year — cold archive |
| Cache math | Active balance cache in Redis per wallet |
| Verdict | Ledger sharding by wallet_id. HSM is throughput ceiling. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Token per device vs per user**

→ Per device token

Stolen phone does not expose other devices.

_Revisit when:_ Per user if simplifying.

**Ledger sharding**

→ Shard by wallet_id

P2P between wallets may need saga if different shards.

_Revisit when:_ Single shard for interview MVP.

**Offline payments**

→ Secure element counter

Limited offline quota. Sync when online.

_Revisit when:_ Online-only if out of scope.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How is PCI scope reduced?**

Merchant never sees PAN — only tokens. Vault is isolated. SAQ A scope for merchants.

**How do P2P transfers work?**

Debit sender ledger, credit receiver in one transaction if same shard; else saga with hold.

**How do refunds work?**

New ledger events reversing original. Link refund_id to payment_id.

**How do you handle currency conversion?**

FX rate at auth time stored on event. Settlement may differ — reconcile FX gain/loss.

**How do you detect fraud?**

Velocity, device attestation, geolocation mismatch, ML risk score.

**How do you support recurring billing?**

Merchant-specific token + mandate record. Network initiates charge with idempotency.

**How do you audit the ledger?**

Immutable log + daily sum(debits)+sum(credits)=0 check.

**How does this differ from Stripe?**

Stripe is merchant acquirer platform. Wallet is consumer token vault + pass-through auth.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Token + auth** — HSM vault. Network auth. Simple ledger.

**v2 — P2P + ledger** — Double-entry. Idempotency. Fraud rules.

**v3 — Global wallet** — Multi-currency, offline tap, network token lifecycle, regulatory reporting.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Financial correctness and PCI boundaries matter more than raw QPS.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Tokenization** — Replace PAN with device-specific token. HSM generates and stores mapping.
- **PCI scope reduction** — Merchant handles tokens only. Vault is isolated PCI zone.
- **Double-entry ledger** — Every transfer = balanced debit/credit events. Immutable log.
- **Idempotent payments** — client_request_id dedup prevents double tap charges.
- **3DS / biometrics** — Step-up auth for high-risk transactions.
- **P2P transfers** — Internal ledger move before external ACH settlement.

> Token vault + immutable ledger + idempotent authorization.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Store PAN vs token only** — Token-only is mandatory for PCI. PAN in HSM only.

**Sync vs async settlement** — User sees auth result sync. Settlement with network async.

**Ledger event sourcing vs balance table** — Event log is audit-proof. Balance is derived/cache.

> "Tokens not PANs, HSM vault, double-entry ledger, idempotent auth requests."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Tokenization and HSM
> [!CAUTION]
> **🔴 Weak** — encrypt PAN in DB
>
> [!WARNING]
> **🟡 Strong** — HSM generates tokens; PAN never leaves secure enclave
>
> [!TIP]
> **🟢 Staff+** — Name the metric you'd alert on and when you'd revisit this design.


#### Deep dive 2: Ledger correctness
_Append-only events. Daily balance invariant check. No in-place balance updates_

> [!CAUTION]
> **🔴 Weak** — Oversimplify ledger correctness — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Append-only events. Daily balance invariant check. No in-place balance updates
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Fraud
_Velocity limits, device fingerprint, ML risk score. Step-up 3DS above threshold_

> [!CAUTION]
> **🔴 Weak** — Oversimplify fraud — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Velocity limits, device fingerprint, ML risk score. Step-up 3DS above threshold
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Offline tap
_Stored cryptogram on secure element. Limited offline spend counter_

> [!CAUTION]
> **🔴 Weak** — One global INCR key for all traffic.
>
> [!WARNING]
> **🟡 Strong** — Stored cryptogram on secure element. Limited offline spend counter
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Wallet script.

2. "Card enrollment tokenizes PAN in HSM — app never stores PAN."

3. "Payment: token + cryptogram to network. Idempotency key on every tap."

4. "Ledger records auth/capture/settle as immutable events."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
App -> Wallet API -> Token Vault (HSM)
              -> Auth svc -> Payment network
              -> Ledger (event log)
```

Vault and ledger are isolated trust boundaries.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-39)
