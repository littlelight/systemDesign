# System Design Cheat Sheets

Offline, self-contained HTML reference guides for system design interview prep. Open any file directly in a browser — no build step or server required.

**Start here:** [`cheatSheet/index.html`](cheatSheet/index.html)

```mermaid
flowchart TB
    subgraph repo["systemDesign"]
        README["README.md"]
        INDEX["cheatSheet/index.html"]
        subgraph cheatSheet["cheatSheet/"]
            v10["SystemDesign_Complete_v10.html"]
            v15["system_design_cheatsheet_v14.html (v15)"]
        end
    end

    INDEX --> v10
    INDEX --> v15

    v10 --> bbg["ByteByteGo Reference"]
    v10 --> cards["System Cards"]

    bbg --> foundations["Foundations ×3"]
    bbg --> vol1["Vol. 1 ×11"]
    bbg --> vol2["Vol. 2 ×11"]
    bbg --> appendix["Cloud appendix · CLI commands"]

    cards --> c26["26 systems"]

    v15 --> framework["Delivery Framework + worksheet"]
    v15 --> dbchooser["DB Chooser"]
    v15 --> quickref["Quick Reference + Cloud cmds"]
    v15 --> s40["40 systems · 4E / 18M / 18H"]

    goal{{"What's your goal?"}}
    goal -->|"Study ByteByteGo"| v10
    goal -->|"Practice interviews"| v15
```

## Files

| File | Description |
|------|-------------|
| [`cheatSheet/index.html`](cheatSheet/index.html) | Landing page — pick v10 or v15 |
| [`cheatSheet/SystemDesign_Complete_v10.html`](cheatSheet/SystemDesign_Complete_v10.html) | ByteByteGo Vol. 1 & 2 deep-dive + 26 system cards + cloud appendix |
| [`cheatSheet/system_design_cheatsheet_v14.html`](cheatSheet/system_design_cheatsheet_v14.html) | Staff+ interview prep (v15 content) — 40 systems |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |
| [`scripts/validate_systems.py`](scripts/validate_systems.py) | Card completeness validator |

## Quick start

```bash
open cheatSheet/index.html
# or directly:
open cheatSheet/system_design_cheatsheet_v14.html
open cheatSheet/SystemDesign_Complete_v10.html
```

Validate card data after edits:

```bash
python3 scripts/validate_systems.py
```

## Study paths

### 4-week interview prep (v15)

| Week | Focus | Systems |
|------|-------|---------|
| 1 | Easy + framework | Bitly, Dropbox, GoPuff, Google News — memorize delivery framework + estimation worksheet |
| 2 | Medium reads/writes | WhatsApp, News Feed, Yelp, Rate limiter, Notification, Autocomplete — practice 45-min mocks |
| 3 | Hard distributed | Uber, YouTube, Payment, Kafka, KV store, Google Docs — deep dives + failure modes |
| 4 | Gap fill + review | Maps, Email, S3, Wallet + any weak cards — interview mode + print for flashcards |

### ByteByteGo alignment (v10 → v15)

Use v10 chapters for theory, then drill the matching v15 card:

- URL Shortener → Bitly
- Notification System → Notification system (APNs/FCM)
- Google Maps → Google Maps
- Object Storage (S3) → S3 object storage
- Distributed Email → Distributed email (Gmail)
- Payment System → Payment system + Digital wallet

## system_design_cheatsheet_v14.html (v15)

Staff+ prep built around a repeatable delivery framework.

**Delivery framework** — six-step flow plus clarifying questions, latency/QPS numbers, and a live **estimation worksheet**.

**Database chooser** — comparison table (open by default), decision tree, per-DB deep dives, anti-patterns.

**Quick reference** — CAP, protocols, quick-fire, consistency patterns, **cloud services + AWS commands** (full tables in v10).

**40 systems** by difficulty:

| Easy (4) | Medium (18) | Hard (18) |
|----------|-------------|-----------|
| Bitly | Ticketmaster | Instagram |
| Dropbox | WhatsApp | YouTube Top K |
| Local delivery (GoPuff) | FB News Feed | Uber |
| News aggregator (Google News) | Tinder | Robinhood |
| | LeetCode | Google Docs |
| | Distributed rate limiter | Distributed cache |
| | FB Live Comments | YouTube |
| | FB Post Search | Web crawler |
| | Yelp | Ad click aggregator |
| | Strava | Job scheduler (Airflow) |
| | Online auction (eBay) | Payment system (Stripe) |
| | Price tracking | Metrics monitoring (Datadog) |
| | Notification system (APNs/FCM) | Message queue (Kafka) |
| | Search autocomplete (Google) | Distributed key-value store |
| | Unique ID generator (Snowflake) | Nearby friends |
| | Hotel reservation (Booking.com) | Google Maps |
| | Gaming leaderboard | Distributed email (Gmail) |
| | S3 object storage | Digital wallet (Apple Pay) |

**UX features**
- Sidebar + topbar search
- Interview mode (5 essential tabs)
- Keyboard shortcuts (`/`, `j`/`k`, `1`–`5`, `Esc`)
- Deep links (`#card-12`, `#uber-ride-sharing`)
- Theme, tabs, studied progress — persisted in `localStorage`
- Prev/Next card navigation
- Copy script button
- Print stylesheet

## SystemDesign_Complete_v10.html

Two-tab cheatsheet: ByteByteGo reference + 26 system cards.

**Quick Reference appendix** — database selection, CAP, protocols, **AWS/GCP/Azure** with CLI commands and Java single-server equivalents.

**Features:** topic search, chapter sidebar, print layout. Links to v15 Staff+ prep in header.

## Which one to use?

- **Studying ByteByteGo** or need cloud CLI reference → `SystemDesign_Complete_v10.html`
- **Practicing live interviews** with framework, DB chooser, scripts → `system_design_cheatsheet_v14.html` (v15)

Both files are standalone — styles and scripts embedded inline. Optional: extract system data with `python3 scripts/extract_systems.py`.
