# Search autocomplete (Google)

**Medium** · Trie · Top-K cache · Batch rebuild · Debounce

Tags: `Trie`, `Redis`, `MapReduce`, `CDN`, `Top-K`, `Debounce`

## Data flow

Query logs aggregate nightly via MapReduce into frequency counts. A trie builder stores top-10 completions per prefix in Redis (prefix → JSON array). Keystrokes hit the API after 100ms debounce; CDN caches the hottest prefixes.


> Top-K cached at every prefix node  |  Weekly batch rebuild  |  CDN covers top 10K prefixes

## Architecture diagram

```
[Query logs] --> [MapReduce] --> [Trie Builder] --> [Redis shards]
                                              ^
  [User keystroke] --> [Debounce] --> [API] --+--> [CDN hit?] --> return
                                    | miss
                                    v
                              [Redis prefix lookup]
```

Say offline build + online lookup. CDN and debounce are the scaling story.


---

<details open>
<summary><strong>Problem</strong></summary>

Return top-10 search completions within 100ms as the user types, at billions of queries per day.

Hard parts: sub-100ms latency, fresh trending terms, and trie size small enough to serve from cache.

</details>


<details>
<summary><strong>Failures</strong></summary>

**Trie rebuild fails at 70%**

Live trie is weeks old. Missing trending terms.

_Fix:_ Shadow build, validate, atomic flip. Rollback = flip back.

**Viral term not in trie**

Breaking news queries return empty suggestions.

_Fix:_ Hot-term detector injects temporary entries.

**'th' shard hotspot**

English prefixes skew traffic to one shard.

_Fix:_ Sub-shard by second/third character.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 10B queries/day, 5 keystrokes/query, 100ms SLA |
| Read QPS | 10B×5/86400 ≈ 578K keystroke QPS; ~58K after debounce |
| Write QPS | Nightly log ingest 1 TB/day; weekly trie rebuild batch |
| Storage | Trie ~500 MB Redis for 1M prefixes |
| Cache math | CDN top 10K prefixes → ~90% hit rate |
| Verdict | Backend QPS modest after debounce + CDN. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Rebuild frequency**

→ Weekly full + 5min hot-term injection

Captures stable trends weekly; injection handles breaking news.

_Revisit when:_ Daily rebuild for news-heavy products.

**K suggestions per node**

→ K=10

UI shows at most 10; storing more wastes space.

_Revisit when:_ K=20 if post-rank filtering removes many.

**Personalization approach**

→ Global trie + query-time re-rank

Per-user trie at 1B users is impossible.

_Revisit when:_ Per-user trie only for small enterprise search.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How does Google differ from this design?**

Same trie + top-K retrieval; Google adds ML re-ranking, location, freshness, and A/B testing between trie and ranker.

**How do you handle multiple languages?**

Separate trie per language in Redis namespace en:prefix, ja:prefix. Detect from Accept-Language and script.

**How do you add spell correction?**

Symspell parallel branch: if exact trie returns <5 results, fuzzy lookup within edit distance 2 within remaining latency budget.

**How do you filter inappropriate suggestions?**

Blocklist applied at trie build time. Human review queue for flagged terms.

**How do you A/B test ranking changes?**

Cohort flag routes to different re-ranker version. Trie unchanged; only ranking layer varies.

**How do you handle CJK input?**

Character n-gram trie instead of word-based. Segment Japanese input before lookup.

**How do you update without downtime?**

Build new Redis key namespace v2:, flip routing config, delete v1 after TTL.

**How do you measure quality?**

Track suggestion CTR, zero-result rate, latency p99. Alert on zero-result spike for popular prefixes.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — SQL LIKE** — LIKE 'typ%'. Works to 10K queries.

**v2 — Redis trie** — Weekly rebuild. CDN. Debounce. 1B queries/day.

**v3 — Personalized** — Global trie + user boost re-rank. Hot injection. Multilingual shards.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Autocomplete looks read-heavy but the real trick is avoiding work: debounce cuts QPS 10×, CDN absorbs 90%, trie pre-computation eliminates subtree scans.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Trie with top-K per node** — Traverse to prefix node, return cached top-10 — no subtree walk.
- **Batch rebuild** — Do not update trie on every query. Weekly full rebuild + hot-term injection for breaking news.
- **Redis serving** — Key = prefix, value = suggestions array. Sub-ms lookup.
- **CDN for hot prefixes** — Top 10K prefixes cover ~90% of traffic.
- **Client debounce** — 100–200ms debounce cuts backend QPS ~10×.
- **Shard by prefix** — First character (or 2-gram) → independent Redis shard.
- **Content filter** — Blocklist inappropriate suggestions before storing in trie.

> Debounce, CDN, Redis trie — three free wins before any complex ML.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Real-time trie update vs batch rebuild** — Batch is correct — slight staleness acceptable; real-time updates pollute the write path.

**Trie in memory vs Redis** — Redis shared across API fleet, easy blue-green trie deploy. In-memory fastest but harder to update.

**Top-K cached vs compute on query** — Pre-cached top-K avoids O(subtree) traversal — required at scale.

**Global vs personalized trie** — Per-user trie impossible at 1B users. Global trie + query-time re-rank boost from user history.

> "Batch-built trie with top-K at every node, served from Redis/CDN, debounced client queries."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Trie structure and top-K at every node
> [!CAUTION]
> **🔴 Weak** — scan all queries matching prefix on every keystroke
>
> [!WARNING]
> **🟡 Strong** — prefix tree where each node stores top-10 completions by global frequency. Query = O(prefix length) traversal + O(1) return
>
> [!TIP]
> **🟢 Staff+** — 1M unique prefixes × 10 suggestions × 50B ≈ 500 MB in Redis


#### Deep dive 2: Data pipeline — batch rebuild with hot-term injection
_Weekly MapReduce over query logs → frequency table → trie builder → shadow deploy → atomic flip. Breaking news: real-time detector flags queries with no trie match exceeding 1K/5min → inject temporary hot entry until next rebuild_

> [!CAUTION]
> **🔴 Weak** — Rebuild the full index nightly — no incremental updates.
>
> [!WARNING]
> **🟡 Strong** — Weekly MapReduce over query logs → frequency table → trie builder → shadow deploy → atomic flip. Breaking news: real-time detector flags queries with no trie match exceeding 1K/5min → inject temporary hot entry until next rebuild
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Latency budget — debounce, CDN, sharding
_578K QPS raw keystrokes → 58K with debounce. CDN serves 90% from edge. Backend sees ~5.8K QPS for long tail. Shard Redis by first character; sub-shard hot prefixes like "th"_

> [!CAUTION]
> **🔴 Weak** — Serve every request from origin — CDN is optional.
>
> [!WARNING]
> **🟡 Strong** — 578K QPS raw keystrokes → 58K with debounce. CDN serves 90% from edge. Backend sees ~5.8K QPS for long tail. Shard Redis by first character; sub-shard hot prefixes like "th"
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Personalization without per-user tries
_Fetch user boost vector from Redis (recent searches). Re-rank trie top-50 candidates in ~5ms. Decouple retrieval (trie) from ranking (lightweight model)_

> [!CAUTION]
> **🔴 Weak** — Build a per-user trie — one per user at scale.
>
> [!WARNING]
> **🟡 Strong** — Fetch user boost vector from Redis (recent searches). Re-rank trie top-50 candidates in ~5ms. Decouple retrieval (trie) from ranking (lightweight model)
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Two-pipeline script.

2. "Two components: offline trie builder and online query service."

3. "Offline: aggregate query logs daily, build trie with top-10 per prefix, load into Redis weekly with blue-green deploy."

4. "Online: debounce 100ms → Redis HGET prefix → return top-10 in <5ms."

5. "CDN caches top 10K prefixes — 90% of traffic never hits origin."

6. "Hot-term injection for viral queries between rebuilds."

7. "Shard trie by first character for horizontal scale."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
[Query logs] --> [MapReduce] --> [Trie Builder] --> [Redis shards]
                                              ^
  [User keystroke] --> [Debounce] --> [API] --+--> [CDN hit?] --> return
                                    | miss
                                    v
                              [Redis prefix lookup]
```

Say offline build + online lookup. CDN and debounce are the scaling story.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-29)
