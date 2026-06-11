# Changelog

## v15 (2026-06-11)

### UX
- Visible topbar search (was hidden)
- Theme, collapsed sections, tab memory, and studied checkboxes persist via `localStorage`
- Deep links: `#card-N`, `#card-N-tab`, or `#system-slug`
- Keyboard: `/` search, `j`/`k` prev/next card, `1`–`5` tabs (interview mode), `Esc` close sidebar
- Interview mode toggle — shows 5 essential tabs (Problem, Key points, Tradeoffs, Deep dives, Script)
- Print stylesheet for clean card export
- Prev/Next navigation per card
- Copy-to-clipboard on Script tab
- ARIA labels on tabs and toolbar
- Favicon and meta description
- DB comparison table open by default
- Estimation worksheet in delivery framework

### Content
- All 40 cards now have 8 follow-up Q&A entries
- See-also links on overlapping systems (Bitly, WhatsApp, Yelp, YouTube pair, etc.)
- Cloud services quick-ref + copy-paste AWS commands (links to v10 full appendix)
- 4 new systems: Google Maps, Distributed email (Gmail), S3 object storage, Digital wallet (Apple Pay)

### Structure
- `cheatSheet/index.html` landing page
- `scripts/validate_systems.py` — card completeness checks
- `scripts/enrich_v15.py` — follow-up enrichment
- `scripts/add_systems_v15.py` — new system insertion
- Cross-links between v10 and v15 cheatsheets

## v14 (prior)
- 36 system cards with 12-tab structure
- Database chooser and quick reference sections
- 8 new systems (notification, autocomplete, snowflake, hotel, leaderboard, kafka, kv store, nearby friends)

## v10
- ByteByteGo Vol. 1 & 2 reference + 26 system cards
- AWS/GCP/Azure appendix with CLI commands
