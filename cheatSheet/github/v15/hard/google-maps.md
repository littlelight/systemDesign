# Google Maps

**Hard** · Tile CDN · Geospatial index · Routing graph

Tags: `S3/GCS`, `CDN`, `Quadtree`, `PostGIS`, `Dijkstra`, `Tile cache`

_See also: v10 · geospatial + CDN patterns_

## Data flow

Map rendering is CDN-served tiles (z/x/y) stored in S3 — immutable, cacheable for years. Place search uses Elasticsearch with geo filters. Routing runs on a preprocessed road graph (not live OSM queries) with contraction hierarchies for sub-second paths.


> Tiles immutable → CDN forever  |  Routing on preprocessed graph  |  Geospatial index for POI search

## Architecture diagram

```
Client -> CDN -> S3 tiles (base map)
Client -> API -> ES (POI search)
Client -> Routing svc -> Graph shards (CH / hub labels)
```

Separate read paths for tiles, search, and routing.


---

<details open>
<summary><strong>Problem</strong></summary>

Build a global maps platform: render maps fast worldwide, search places, and compute driving directions at scale.

Hard parts: petabytes of tiles, sub-100ms pan/zoom, and routing on a graph with hundreds of millions of edges.

</details>


<details>
<summary><strong>Failures</strong></summary>

**CDN miss storm on new region launch**

Origin tile service overwhelmed.

_Fix:_ Pre-warm CDN. Rate limit origin. Autoscale tile generators.

**Stale traffic overlay**

Users routed into closed roads.

_Fix:_ Separate traffic freshness SLA. Fallback to historical speeds.

**POI index drift**

New businesses missing from search.

_Fix:_ CDC from merchant DB + nightly full rebuild.


</details>


<details>
<summary><strong>Estimation</strong></summary>

| Field | Value |
|-------|-------|
| Assumptions | 500M DAU, 50 tile requests/session, 10M routing requests/day |
| Read QPS | Tiles: 500M×50/86400 ≈ 290K/s — CDN absorbs |
| Write QPS | Routing: 10M/86400 ≈ 115/s compute |
| Storage | Zoom 0–18 global pyramid ≈ petabytes — store regional hot sets |
| Cache math | CDN cache hot z/x/y prefixes |
| Verdict | CDN is the scaling lever for tiles; routing needs graph sharding. |


</details>


<details>
<summary><strong>Design decisions</strong></summary>

**Raster vs vector tiles**

→ Raster for interview default

CDN-friendly, simple. Vector if interviewer asks about dynamic styling.

_Revisit when:_ Vector for offline mobile maps.

**Routing algorithm**

→ Contraction hierarchies

Sub-second on continental graphs after preprocessing.

_Revisit when:_ A* on small metro graphs only.

**Traffic freshness**

→ Separate dynamic layer

Base tiles stay immutable; traffic updates frequently.

_Revisit when:_ Bake traffic into tiles only for replay/historical.


</details>


<details>
<summary><strong>Follow-up Q&amp;A</strong></summary>

**How do you generate tiles at scale?**

Batch MapReduce over planet data. Parallel workers per z/x/y batch. Store to S3. Long tail on-demand generation with cache.

**How do you handle map updates (new roads)?**

Versioned tile sets. Client requests v=2026-06. Gradual CDN rollouts per region.

**How do you rank search results?**

BM25 text score × exp(-distance/λ) × log(popularity). Personalization optional.

**How do you support offline maps?**

Bundle vector tiles + local routing subgraph on device. Sync deltas weekly.

**How do you reduce routing latency globally?**

Regional routing shards. Cross-border: coarse inter-region graph first.

**How do you detect map vandalism?**

Human review queue + automated anomaly detection on edits.

**What metrics matter?**

CDN hit ratio, tile origin QPS, routing p99, search zero-result rate.

**How do you test routing correctness?**

Golden paths vs known benchmarks. A/B on ETA accuracy vs ground truth.


</details>


<details>
<summary><strong>Evolution</strong></summary>

**v1 — Static tiles** — Prebuilt tiles + CDN. No live routing.

**v2 — Search + routing** — ES POI index. Regional routing graphs.

**v3 — Global** — Traffic overlay, CH routing, multi-region CDN, map edit pipeline.


</details>


<details>
<summary><strong>Why it&#x27;s hard to scale</strong></summary>

Petabyte-scale immutable tiles and global CDN hit ratio dominate — routing is compute-heavy but smaller QPS.

</details>


<details>
<summary><strong>Key points</strong></summary>

- **Tile pyramid** — Zoom level z has 4^z tiles globally. Pre-generate popular regions; on-demand for long tail.
- **CDN-first** — Tiles are immutable — Cache-Control: max-age=31536000. CDN handles 99% of map traffic.
- **Geospatial search** — POIs indexed in ES with geo_point. Query: text match + geo_distance filter + popularity boost.
- **Routing graph** — Preprocessed road network offline. Online: bidirectional Dijkstra or contraction hierarchies.
- **Live traffic** — Traffic overlay is dynamic — separate tile layer or client-side vector update, not baked into base tiles.
- **Personalization out of scope** — Unless asked: saved places, ads, Street View capture pipeline.

> Tiles on CDN, POIs in search index, routing on preprocessed graph.

</details>


<details>
<summary><strong>Tradeoffs</strong></summary>

**Raster tiles vs vector tiles** — Raster: simpler CDN caching. Vector: smaller payloads, client-side styling — more client CPU.

**On-demand routing vs precomputed** — Precompute hub labels for fast queries. On-demand Dijkstra only for local refinement.

**PostGIS vs Elasticsearch for POI** — ES wins for text+geo hybrid search at scale. PostGIS for complex polygon queries.

> "Immutable tiles on CDN, search index for POIs, offline graph preprocessing for routing."


</details>


<details>
<summary><strong>Deep dives</strong></summary>

#### Deep dive 1: Tile storage and CDN
> [!CAUTION]
> **🔴 Weak** — generate tiles per request
>
> [!WARNING]
> **🟡 Strong** — pre-render pyramid, store in S3, serve via CDN. Staff+: invalidation only for traffic/incident overlays
>
> [!TIP]
> **🟢 Staff+** — Name the metric you'd alert on and when you'd revisit this design.


#### Deep dive 2: POI search ranking
_Text relevance × distance decay × popularity. Geo filter first to shrink candidate set_

> [!CAUTION]
> **🔴 Weak** — SELECT * WHERE column LIKE '%query%'.
>
> [!WARNING]
> **🟡 Strong** — Text relevance × distance decay × popularity. Geo filter first to shrink candidate set
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 3: Routing at scale
_Graph partitioned by region. Highway hierarchy: coarse graph for long distances, refine locally_

> [!CAUTION]
> **🔴 Weak** — Oversimplify routing at scale — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Graph partitioned by region. Highway hierarchy: coarse graph for long distances, refine locally
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.


#### Deep dive 4: Fresh traffic data
_Probe GPS stream → aggregate speeds per road segment → publish traffic layer every 2–5 min_

> [!CAUTION]
> **🔴 Weak** — Oversimplify fresh traffic data — name one component, skip failure modes and metrics.
>
> [!WARNING]
> **🟡 Strong** — Probe GPS stream → aggregate speeds per road segment → publish traffic layer every 2–5 min
>
> [!TIP]
> **🟢 Staff+** — Name metric + revisit trigger when they push depth.

</details>


<details>
<summary><strong>Interview script</strong></summary>

1. Map platform script.

2. "Three paths: tile rendering (read-heavy CDN), place search (ES + geo), routing (preprocessed graph)."

3. "Tiles: z/x/y in object storage, immutable, CDN cached forever."

4. "Search: Elasticsearch geo_point + text, rank by distance and popularity."

5. "Routing: contraction hierarchies on offline graph — not live OSM queries per request."


</details>


<details>
<summary><strong>Whiteboard</strong></summary>

```
Client -> CDN -> S3 tiles (base map)
Client -> API -> ES (POI search)
Client -> Routing svc -> Graph shards (CH / hub labels)
```

Separate read paths for tiles, search, and routing.

</details>


---

[← Back to v15 index](index.md) · [Interactive version](../../system_design_cheatsheet_v14.html#card-36)
