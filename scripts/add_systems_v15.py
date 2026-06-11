#!/usr/bin/env python3
"""Append 4 new system cards to the cheatsheet."""

from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "cheatSheet" / "system_design_cheatsheet_v14.html"

NEW_SYSTEMS = r"""
,{d:'h',title:'Google Maps',sub:'Tile CDN · Geospatial index · Routing graph',tags:['S3/GCS','CDN','Quadtree','PostGIS','Dijkstra','Tile cache'],seeAlso:'<a href="SystemDesign_Complete_v10.html">v10</a> · geospatial + CDN patterns',
diag:[[{t:'Client',s:'pan/zoom map',c:'sec'},{a:'→',l:'tile GET'},{t:'CDN',s:'256×256 PNG/WebP',c:'warn'},{a:'→',l:'miss'},{t:'Tile service',s:'generate/cache',c:'info'}],[null,null,null,{a:'→'},{t:'Object store',s:'z/x/y tiles',c:'succ'},null],[{t:'Client',s:'search/route',c:'sec'},{a:'→'},{t:'API',s:'places · routing',c:'info'},{a:'→'},{t:'Search index',s:'Elasticsearch',c:'dang'}]],
dn:'Tiles immutable → CDN forever  |  Routing on preprocessed graph  |  Geospatial index for POI search',
na:'Map rendering is <b>CDN-served tiles</b> (z/x/y) stored in <b>S3</b> — immutable, cacheable for years. <b>Place search</b> uses Elasticsearch with geo filters. <b>Routing</b> runs on a preprocessed road graph (not live OSM queries) with contraction hierarchies for sub-second paths.',
q1:'Build a global maps platform: render maps fast worldwide, search places, and compute driving directions at scale.\n\nHard parts: petabytes of tiles, sub-100ms pan/zoom, and routing on a graph with hundreds of millions of edges.',
q3:[{b:'Tile pyramid',t:'Zoom level z has 4^z tiles globally. Pre-generate popular regions; on-demand for long tail.'},{b:'CDN-first',t:'Tiles are immutable — Cache-Control: max-age=31536000. CDN handles 99% of map traffic.'},{b:'Geospatial search',t:'POIs indexed in ES with geo_point. Query: text match + geo_distance filter + popularity boost.'},{b:'Routing graph',t:'Preprocessed road network offline. Online: bidirectional Dijkstra or contraction hierarchies.'},{b:'Live traffic',t:'Traffic overlay is dynamic — separate tile layer or client-side vector update, not baked into base tiles.'},{b:'Personalization out of scope',t:'Unless asked: saved places, ads, Street View capture pipeline.'}],
c3:'Tiles on CDN, POIs in search index, routing on preprocessed graph.',
q4:[{vs:'Raster tiles vs vector tiles',t:'Raster: simpler CDN caching. Vector: smaller payloads, client-side styling — more client CPU.'},{vs:'On-demand routing vs precomputed',t:'Precompute hub labels for fast queries. On-demand Dijkstra only for local refinement.'},{vs:'PostGIS vs Elasticsearch for POI',t:'ES wins for text+geo hybrid search at scale. PostGIS for complex polygon queries.'}],
c4:'"Immutable tiles on CDN, search index for POIs, offline graph preprocessing for routing."',
q5:'Deep dive 1: Tile storage and CDN\nWeak: generate tiles per request. Strong: pre-render pyramid, store in S3, serve via CDN. Staff+: invalidation only for traffic/incident overlays.\n\nDeep dive 2: POI search ranking\nText relevance × distance decay × popularity. Geo filter first to shrink candidate set.\n\nDeep dive 3: Routing at scale\nGraph partitioned by region. Highway hierarchy: coarse graph for long distances, refine locally.\n\nDeep dive 4: Fresh traffic data\nProbe GPS stream → aggregate speeds per road segment → publish traffic layer every 2–5 min.',
q6:['Map platform script.','"Three paths: tile rendering (read-heavy CDN), place search (ES + geo), routing (preprocessed graph)."', '"Tiles: z/x/y in object storage, immutable, CDN cached forever."', '"Search: Elasticsearch geo_point + text, rank by distance and popularity."', '"Routing: contraction hierarchies on offline graph — not live OSM queries per request."'],
arch:{d:`Client -> CDN -> S3 tiles (base map)
Client -> API -> ES (POI search)
Client -> Routing svc -> Graph shards (CH / hub labels)`,t:`Separate read paths for tiles, search, and routing.`},
scale:{pills:[{"cls":"pat-reads","label":"Scaling Reads"},{"cls":"pat-blob","label":"Large Blobs"}],body:`Petabyte-scale immutable tiles and global CDN hit ratio dominate — routing is compute-heavy but smaller QPS.`},
v13:{f:[{"w":"CDN miss storm on new region launch","i":"Origin tile service overwhelmed.","x":"Pre-warm CDN. Rate limit origin. Autoscale tile generators."},{"w":"Stale traffic overlay","i":"Users routed into closed roads.","x":"Separate traffic freshness SLA. Fallback to historical speeds."},{"w":"POI index drift","i":"New businesses missing from search.","x":"CDC from merchant DB + nightly full rebuild."}],
est:{"a":"500M DAU, 50 tile requests/session, 10M routing requests/day","r":"Tiles: 500M×50/86400 ≈ 290K/s — CDN absorbs","w":"Routing: 10M/86400 ≈ 115/s compute","s":"Zoom 0–18 global pyramid ≈ petabytes — store regional hot sets","c":"CDN cache hot z/x/y prefixes","v":"CDN is the scaling lever for tiles; routing needs graph sharding."},
d:[{"q":"Raster vs vector tiles","c":"Raster for interview default","w":"CDN-friendly, simple. Vector if interviewer asks about dynamic styling.","r":"Vector for offline mobile maps."},{"q":"Routing algorithm","c":"Contraction hierarchies","w":"Sub-second on continental graphs after preprocessing.","r":"A* on small metro graphs only."},{"q":"Traffic freshness","c":"Separate dynamic layer","w":"Base tiles stay immutable; traffic updates frequently.","r":"Bake traffic into tiles only for replay/historical."}],
fq:[{"q":"How do you generate tiles at scale?","a":"Batch MapReduce over planet data. Parallel workers per z/x/y batch. Store to S3. Long tail on-demand generation with cache."},{"q":"How do you handle map updates (new roads)?","a":"Versioned tile sets. Client requests v=2026-06. Gradual CDN rollouts per region."},{"q":"How do you rank search results?","a":"BM25 text score × exp(-distance/λ) × log(popularity). Personalization optional."},{"q":"How do you support offline maps?","a":"Bundle vector tiles + local routing subgraph on device. Sync deltas weekly."},{"q":"How do you reduce routing latency globally?","a":"Regional routing shards. Cross-border: coarse inter-region graph first."},{"q":"How do you detect map vandalism?","a":"Human review queue + automated anomaly detection on edits."},{"q":"What metrics matter?","a":"CDN hit ratio, tile origin QPS, routing p99, search zero-result rate."},{"q":"How do you test routing correctness?","a":"Golden paths vs known benchmarks. A/B on ETA accuracy vs ground truth."}],
ev:[{"s":"v1 — Static tiles","d":"Prebuilt tiles + CDN. No live routing."},{"s":"v2 — Search + routing","d":"ES POI index. Regional routing graphs."},{"s":"v3 — Global","d":"Traffic overlay, CH routing, multi-region CDN, map edit pipeline."}]}}

,{d:'h',title:'Distributed email (Gmail)',sub:'Ingestion · Storage sharding · Search index',tags:['SMTP','Cassandra','HDFS','MapReduce','Elasticsearch','Spam ML'],
diag:[[{t:'SMTP',s:'inbound mail',c:'sec'},{a:'→'},{t:'Ingestion',s:'parse MIME',c:'info'},{a:'→'},{t:'Spam/virus',s:'ML filter',c:'warn'},{a:'→'},{t:'Storage',s:'user shard',c:'succ'}],[null,null,null,{a:'→'},{t:'Search index',s:'async ES',c:'dang'},null],[{t:'Client',s:'read/send',c:'sec'},{a:'→'},{t:'API',s:'IMAP/HTTP',c:'info'},{a:'→'},{t:'User shard',s:'Cassandra/HDFS',c:'succ'}]],
dn:'Shard by user_id  |  Attachments in blob store  |  Search index async from mail log',
na:'Inbound <b>SMTP</b> mail is parsed, scanned for spam, and written to a <b>user shard</b> (Cassandra). Large attachments go to <b>blob storage</b>. <b>Elasticsearch</b> indexes subject/body asynchronously for search. Reads hit the user shard directly — email is write-once, read-many per mailbox.',
q1:'Design webmail at Gmail scale: receive, store, search, and send billions of emails with strong per-user consistency.\n\nHard parts: storage per user, full-text search, and reliable SMTP delivery.',
q3:[{b:'Shard by user_id',t:'All of a user\'s mail on one shard — simplifies inbox listing and ACID per mailbox.'},{b:'Blob for attachments',t:'Message metadata in Cassandra; attachment bytes in S3/HDFS.'},{b:'Async search index',t:'Kafka mail event → ES indexer. Search slightly behind inbox — acceptable.'},{b:'Spam at ingress',t:'Score before storage. Quarantine bucket for suspicious mail.'},{b:'SMTP outbound queue',t:'Retry with exponential backoff. DKIM/SPF signing per domain.'},{b:'Deletion/tombstones',t:'Soft delete + async purge from index and blob store.'}],
c3:'User-sharded storage, blob attachments, async search index.',
q4:[{vs:'SQL vs Cassandra per user shard',t:'Cassandra: write-heavy, tunable consistency, horizontal scale. SQL: simpler but harder at Gmail scale.'},{vs:'Sync vs async search index',t:'Async: faster ingest. Sync: instant search — costly at write volume.'},{vs:'Push vs poll for new mail',t:'IMAP IDLE / WebSocket push for new mail notifications.'}],
c4:'"Shard mail by user, blob store attachments, async ES index, spam filter at SMTP ingress."',
q5:'Deep dive 1: Storage model\nWeak: one row per email in SQL. Strong: Cassandra partition key = user_id, cluster key = timestamp+id. Staff+: separate hot (inbox) and cold (archive) tiers.\n\nDeep dive 2: Search\nInverted index per user or global with user_id filter. Reindex pipeline from mail log for recovery.\n\nDeep dive 3: SMTP reliability\nOutbound queue in Kafka. Multiple MX retries. Bounce handling updates recipient reputation.\n\nDeep dive 4: Spam/abuse\nFeature extraction at edge. ML model ensemble. User feedback loop for false positives.',
q6:['Email platform script.','"Inbound SMTP → parse → spam scan → user shard. Attachments to blob store."','"Search: async indexer from mail events — inbox read path does not wait on ES."','"Outbound: queue + retry + DKIM. Per-user send rate limits."'],
arch:{d:`SMTP -> Ingest -> Spam -> Cassandra (user shard) -> API -> Client
                    -> Blob (attachments)
                    -> Kafka -> ES indexer`,t:`User shard is source of truth; search is derived.`},
scale:{pills:[{"cls":"pat-writes","label":"Scaling Writes"},{"cls":"pat-blob","label":"Large Blobs"}],body:`Billions of messages/day with large attachments — sharding and blob offload are mandatory.`},
v13:{f:[{"w":"Hot user shard (celebrity inbox)","i":"Millions of fan emails to one user.","x":"Rate limit per sender. Separate fan-mail bucket. Async fan-out to readers."},{"w":"Search index lag","i":"New mail not findable for minutes.","x":"Monitor indexer lag. Priority queue for recent mail."},{"w":"Attachment virus","i":"Malware stored in blob system.","x":"Scan before blob write. Block executable MIME types."}],
est:{"a":"1B users, 100 emails/user/day avg, 50KB avg with attachments","r":"Inbox read: 1B×20/86400 ≈ 230K/s","w":"Ingest: 1B×100/86400 ≈ 1.16M/s","s":"100B emails/day × 50KB ≈ 4.5 PB/day raw — tiered storage + dedup","c":"Hot inbox cache per user in Redis","v":"Write sharding by user_id is the core decision."},
d:[{"q":"Cassandra vs HDFS for mail bodies","c":"Cassandra hot + HDFS cold archive","w":"Recent mail low-latency. Archive cheap on HDFS/Glacier.","r":"All Cassandra if simplifying interview."},{"q":"Global vs per-user search index","c":"Global ES with user_id filter","w":"Simpler ops. Per-user index only for enterprise vaults.","r":"Per-user index at extreme privacy requirements."},{"q":"Strong consistency for inbox","c":"Quorum reads on user partition","w":"User expects read-your-writes after send.","r":"Eventual for search index only."}],
fq:[{"q":"How do you handle duplicate delivery (SMTP retry)?","a":"Message-ID dedup at ingestion. Store idempotency key 7 days."},{"q":"How do you implement labels/folders?","a":"Secondary index table: user_id + label → message_ids. Or bitmap per label."},{"q":"How do you support full mailbox search?","a":"ES query with user_id filter + highlighting. Fallback to metadata scan if index lag."},{"q":"How do you migrate a user between shards?","a":"Dual-write period. Background copy. Flip read route. Delete old after verify."},{"q":"How do you handle court-ordered retention?","a":"Legal hold flag bypasses user delete. Separate retention policy store."},{"q":"How do you scale SMTP ingress?","a":"Stateless SMTP proxies → Kafka → storage writers. Horizontal scale on proxies."},{"q":"How do you prevent outbound spam?","a":"Per-user send quotas. Reputation score. Delay suspicious bulk sends."},{"q":"How do you measure deliverability?","a":"Bounce rate, complaint rate, time-to-inbox, indexer lag."}],
ev:[{"s":"v1 — Single DB","d":"Postgres per mail. Works to millions of messages."},{"s":"v2 — Sharded","d":"Cassandra user shards. Blob attachments. Async search."},{"s":"v3 — Gmail scale","d":"Tiered storage, ML spam, global ES, SMTP fleet, legal hold."}]}}

,{d:'m',title:'S3 object storage',sub:'Consistent hashing · Metadata · Durability',tags:['Consistent hashing','Metadata DB','Erasure coding','CDN','Multipart upload'],
diag:[[{t:'Client',s:'PUT/GET object',c:'sec'},{a:'→'},{t:'API gateway',s:'auth · rate limit',c:'info'},{a:'→'},{t:'Metadata svc',s:'bucket/key → nodes',c:'dang'},{a:'→'},{t:'Data nodes',s:'replicated chunks',c:'succ'}]],
dn:'Metadata separate from bytes  |  Consistent hash ring for data nodes  |  11-nines via replication + erasure coding',
na:'Clients call the <b>API gateway</b>. The <b>metadata service</b> maps bucket/key → data node locations via <b>consistent hashing</b>. Object bytes live on <b>data nodes</b> with replication (and erasure coding for cold tier). Large uploads use <b>multipart</b> with coordinator assembly.',
q1:'Design S3-like object storage: PUT/GET/DELETE objects, 11-nines durability, unlimited scale, presigned URLs.\n\nHard parts: metadata scale, rebalancing on node failure, and large object uploads.',
q3:[{b:'Metadata vs data separation',t:'Small metadata in SQL/Cassandra. Payload on commodity disks.'},{b:'Consistent hashing',t:'Ring with virtual nodes. Add/remove nodes with minimal reshuffle.'},{b:'Replication',t:'3 replicas across racks/AZs. Quorum write before ACK.'},{b:'Erasure coding',t:'Cold/archive tier: 10+4 EC reduces storage cost vs 3x replication.'},{b:'Multipart upload',t:'Split >100MB into parts. Parallel upload. Commit manifest on complete.'},{b:'Presigned URLs',t:'HMAC token lets client upload/download without proxying bytes through API.'}],
c3:'Metadata ring mapping, replicated data nodes, multipart for large objects.',
q4:[{vs:'3x replication vs erasure coding',t:'Replication: faster reads, hotter tier. EC: cheaper for cold data.'},{vs:'Strong listing consistency vs eventual',t:'S3 now strong for read-after-write on new objects. Listing can lag slightly.'},{vs:'Central metadata vs per-bucket partition',t:'Partition metadata by bucket hash for scale.'}],
c4:'"Consistent hash for placement, metadata service for lookup, replication for durability."',
q5:'Deep dive 1: Consistent hashing and rebalancing\nVirtual nodes smooth load. On node add: steal ranges. On failure: replicate to successor.\n\nDeep dive 2: Durability\nSync replicate to 3 AZs before 200 OK on PUT. Background scrub detects bit rot.\n\nDeep dive 3: Large objects\nMultipart with part ETags. Coordinator commits manifest atomically.\n\nDeep dive 4: Listing at scale\nPrefix index per bucket shard. Paginate with continuation tokens.',
q6:['Object store script.','"Separate metadata path from data path — never stream gigabytes through metadata DB."','"Consistent hash ring places objects. Three replicas across failure domains."','"Multipart for large uploads. Presigned URLs for direct client ↔ data node transfer."'],
arch:{d:`Client -> API -> Metadata DB (bucket/key -> node list)
              -> Data nodes (replicated chunks)`,t:`Metadata is the control plane; data nodes are the data plane.`},
scale:{pills:[{"cls":"pat-blob","label":"Large Blobs"},{"cls":"pat-writes","label":"Scaling Writes"}],body:`Petabyte payloads and metadata billions of keys — hashing and tiering dominate.`},
v13:{f:[{"w":"Node failure during PUT","i":"Incomplete object visible.","x":"Write to temp key. Commit metadata only after all replicas ACK."},{"w":"Hot key (viral object)","i":"One object saturates single node.","x":"CDN in front. Replication already helps reads. Split hot objects across cache."},{"w":"Ring imbalance","i":"Some nodes 2× fuller than others.","x":"Virtual nodes. Background rebalancer moves ranges."}],
est:{"a":"1T objects, 100K PUT/s peak, 1M GET/s peak, 1MB avg object","r":"1M GET/s — CDN serves 90%","w":"100K PUT/s × 3 replicas = 300K disk writes/s cluster-wide","s":"1T × 1MB = 1 EB logical — EC reduces physical","c":"Metadata: 1T keys × 500B = 500 TB metadata — shard buckets","v":"Metadata sharding and CDN are critical at this scale."},
d:[{"q":"Replication vs erasure coding","c":"3x replication hot, EC cold","w":"Interviewers accept tiered durability.","r":"All EC if cost is the focus."},{"q":"Metadata store","c":"Cassandra partitioned by bucket","w":"Horizontal scale for object index.","r":"FoundationDB/etcd for smaller scale."},{"q":"Strong consistency","c":"Quorum writes + leader for metadata","w":"Read-after-write on new keys matters for clients.","r":"Eventual for cross-region async replication."}],
fq:[{"q":"How do presigned URLs work?","a":"HMAC(bucket, key, expiry, secret). Gateway validates signature before allowing PUT/GET."},{"q":"How do you delete objects at scale?","a":"Tombstone metadata. Async garbage collect bytes when ref count zero."},{"q":"How do you implement versioning?","a":"Version ID in metadata. DELETE marker for latest. List versions API."},{"q":"How do you handle concurrent writers?","a":"If-none-match / version checks. Last writer wins or reject conflict."},{"q":"How do you migrate a bucket between shards?","a":"Background copy with dual metadata. Cutover per key prefix."},{"q":"How do you monitor durability?","a":"Bit rot scrub. Replica lag. Missing replica alerts."},{"q":"How do you support cross-region replication?","a":"Async replicate bytes + metadata. CRR queue per object."},{"q":"How does this relate to Dropbox?","a":"Dropbox adds sync metadata + chunk dedup on top of object storage primitives."}],
ev:[{"s":"v1 — Single machine","d":"Disk + SQLite metadata."},{"s":"v2 — Hash ring","d":"Data nodes + metadata service + replication."},{"s":"v3 — S3-class","d":"Multipart, EC tiers, CDN, cross-region, lifecycle policies."}]}}

,{d:'h',title:'Digital wallet (Apple Pay)',sub:'Tokenization · PCI scope · Double-entry ledger',tags:['HSM','Token vault','Ledger','3DS','Idempotency','PCI DSS'],
diag:[[{t:'Client',s:'tap to pay',c:'sec'},{a:'→'},{t:'Wallet API',s:'token + auth',c:'info'},{a:'→'},{t:'Token vault',s:'PAN never stored',c:'warn'},{a:'→'},{t:'Payment network',s:'Visa/MC',c:'succ'}],[null,null,{a:'→'},{t:'Ledger svc',s:'double-entry',c:'dang'},null]],
dn:'PAN never touches merchant  |  Device-bound tokens  |  Ledger is immutable event log',
na:'Card numbers are <b>tokenized</b> in a <b>HSM-backed vault</b> — merchants never see PAN. Payments authorize against the <b>payment network</b> with device cryptograms. A <b>double-entry ledger</b> records all money movement immutably.',
q1:'Design a digital wallet: add cards, pay in stores/apps, P2P transfers, with PCI compliance and financial correctness.\n\nHard parts: tokenization, fraud, and ledger integrity.',
q3:[{b:'Tokenization',t:'Replace PAN with device-specific token. HSM generates and stores mapping.'},{b:'PCI scope reduction',t:'Merchant handles tokens only. Vault is isolated PCI zone.'},{b:'Double-entry ledger',t:'Every transfer = balanced debit/credit events. Immutable log.'},{b:'Idempotent payments',t:'client_request_id dedup prevents double tap charges.'},{b:'3DS / biometrics',t:'Step-up auth for high-risk transactions.'},{b:'P2P transfers',t:'Internal ledger move before external ACH settlement.'}],
c3:'Token vault + immutable ledger + idempotent authorization.',
q4:[{vs:'Store PAN vs token only',t:'Token-only is mandatory for PCI. PAN in HSM only.'},{vs:'Sync vs async settlement',t:'User sees auth result sync. Settlement with network async.'},{vs:'Ledger event sourcing vs balance table',t:'Event log is audit-proof. Balance is derived/cache.'}],
c4:'"Tokens not PANs, HSM vault, double-entry ledger, idempotent auth requests."',
q5:'Deep dive 1: Tokenization and HSM\nWeak: encrypt PAN in DB. Strong: HSM generates tokens; PAN never leaves secure enclave.\n\nDeep dive 2: Ledger correctness\nAppend-only events. Daily balance invariant check. No in-place balance updates.\n\nDeep dive 3: Fraud\nVelocity limits, device fingerprint, ML risk score. Step-up 3DS above threshold.\n\nDeep dive 4: Offline tap\nStored cryptogram on secure element. Limited offline spend counter.',
q6:['Wallet script.','"Card enrollment tokenizes PAN in HSM — app never stores PAN."','"Payment: token + cryptogram to network. Idempotency key on every tap."','"Ledger records auth/capture/settle as immutable events."'],
arch:{d:`App -> Wallet API -> Token Vault (HSM)
              -> Auth svc -> Payment network
              -> Ledger (event log)`,t:`Vault and ledger are isolated trust boundaries.`},
scale:{pills:[{"cls":"pat-multi","label":"Multi-step Processes"}],body:`Financial correctness and PCI boundaries matter more than raw QPS.`},
v13:{f:[{"w":"Double tap charge","i":"Duplicate authorization.","x":"Idempotency key per tap. Network-level dedup token."},{"w":"HSM unavailable","i":"Cannot decrypt tokens — payments fail.","x":"HSM cluster with failover. No software fallback for PAN ops."},{"w":"Ledger imbalance","i":"Money created/destroyed.","x":"Batch invariant job. Freeze accounts on mismatch."}],
est:{"a":"500M wallets, 5 tx/user/day, $25 avg","r":"Balance reads: 500M×2/86400 ≈ 12K/s","w":"5×500M/86400 ≈ 29K auth/s peak","s":"Event log grows ~100B events/year — cold archive","c":"Active balance cache in Redis per wallet","v":"Ledger sharding by wallet_id. HSM is throughput ceiling."},
d:[{"q":"Token per device vs per user","c":"Per device token","w":"Stolen phone does not expose other devices.","r":"Per user if simplifying."},{"q":"Ledger sharding","c":"Shard by wallet_id","w":"P2P between wallets may need saga if different shards.","r":"Single shard for interview MVP."},{"q":"Offline payments","c":"Secure element counter","w":"Limited offline quota. Sync when online.","r":"Online-only if out of scope."}],
fq:[{"q":"How is PCI scope reduced?","a":"Merchant never sees PAN — only tokens. Vault is isolated. SAQ A scope for merchants."},{"q":"How do P2P transfers work?","a":"Debit sender ledger, credit receiver in one transaction if same shard; else saga with hold."},{"q":"How do refunds work?","a":"New ledger events reversing original. Link refund_id to payment_id."},{"q":"How do you handle currency conversion?","a":"FX rate at auth time stored on event. Settlement may differ — reconcile FX gain/loss."},{"q":"How do you detect fraud?","a":"Velocity, device attestation, geolocation mismatch, ML risk score."},{"q":"How do you support recurring billing?","a":"Merchant-specific token + mandate record. Network initiates charge with idempotency."},{"q":"How do you audit the ledger?","a":"Immutable log + daily sum(debits)+sum(credits)=0 check."},{"q":"How does this differ from Stripe?","a":"Stripe is merchant acquirer platform. Wallet is consumer token vault + pass-through auth."}],
ev:[{"s":"v1 — Token + auth","d":"HSM vault. Network auth. Simple ledger."},{"s":"v2 — P2P + ledger","d":"Double-entry. Idempotency. Fraud rules."},{"s":"v3 — Global wallet","d":"Multi-currency, offline tap, network token lifecycle, regulatory reporting."}]}}
"""

SIDEBAR_INSERT = """
    <a class="sb-sys sb-h" data-diff="h" data-idx="36" onclick="navTo(36,event)">Google Maps</a>
    <a class="sb-sys sb-h" data-diff="h" data-idx="37" onclick="navTo(37,event)">Distributed email (Gmail)</a>
    <a class="sb-sys sb-m" data-diff="m" data-idx="38" onclick="navTo(38,event)">S3 object storage</a>
    <a class="sb-sys sb-h" data-diff="h" data-idx="39" onclick="navTo(39,event)">Digital wallet (Apple Pay)</a>
"""


def main():
    text = HTML.read_text(encoding="utf-8")
    if "Google Maps" in text and "Digital wallet" in text:
        print("Systems already present.")
        return

    text = text.replace("\n];", NEW_SYSTEMS + "\n];", 1)

    # Sidebar
    anchor = '    <a class="sb-sys sb-h" data-diff="h" data-idx="35" onclick="navTo(35,event)">Nearby friends</a>'
    if anchor in text and "Google Maps" not in text.split(anchor)[1][:500]:
        text = text.replace(anchor, anchor + SIDEBAR_INSERT)

    # Counts
    text = text.replace("All 36", "All 40")
    text = text.replace("Medium ×17", "Medium ×18")
    text = text.replace("Hard ×15", "Hard ×18")
    text = text.replace("36 Systems", "40 Systems")
    text = text.replace("36 systems", "40 systems")

    HTML.write_text(text, encoding="utf-8")
    print("Added 4 systems (36 → 40).")


if __name__ == "__main__":
    main()
