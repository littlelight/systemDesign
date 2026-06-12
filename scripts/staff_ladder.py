#!/usr/bin/env python3
"""Weak → Strong → Staff+ ladder parsing and formatting."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass
class Ladder:
    weak: str = ""
    strong: str = ""
    staff: str = ""
    context: str = ""  # scaling pain / intro before ladder


@dataclass
class DeepDive:
    num: str
    title: str
    intro: str
    ladder: Ladder


WEAK_MARKERS = (
    "Weak answer:",
    "Weak candidates",
    "Weak:",
)
STRONG_MARKERS = (
    "Strong answer:",
    "Strong candidates",
    "Strong:",
    "Senior answer:",
)
STAFF_MARKERS = (
    "Staff+ detail:",
    "Staff+ answer:",
    "Staff+ comparison:",
    "Staff+ concern:",
    "Staff+ level:",
    "Staff+ failure mode",
    "Staff+ optimization:",
    "Staff+ recommendation:",
    "Staff+ principle:",
    "Staff+ point:",
    "Staff+ sizing:",
    "Staff+ schema",
    "Staff+ upload flow:",
    "Staff+ crawl ordering:",
    "Staff+ query shape:",
    "Staff-level concern:",
    "Staff-level detail:",
    "Staff-level answer:",
    "For Staff+",
    "Staff+ ",
)


def _find_marker(text: str, markers: tuple[str, ...]) -> tuple[int, str] | None:
    best = None
    for m in markers:
        i = text.find(m)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, m)
    return best


def _slice_between(text: str, start: int, end: int | None) -> str:
    chunk = text[start:end].strip() if end else text[start:].strip()
    chunk = re.sub(r"\s+", " ", chunk)
    return chunk.strip(" .")


def extract_ladder(block: str) -> Ladder:
    """Extract weak/strong/staff from a deep-dive paragraph."""
    weak = strong = staff = ""
    w = _find_marker(block, WEAK_MARKERS)
    s = _find_marker(block, STRONG_MARKERS)
    st = _find_marker(block, STAFF_MARKERS)

    if w:
        w_end = min([x[0] for x in (s, st) if x], default=len(block))
        raw = block[w[0] + len(w[1]) : w_end]
        weak = _slice_between(raw, 0, None)
        if weak.lower().startswith("describe "):
            weak = weak[0].upper() + weak[1:]

    if s:
        s_end = st[0] if st else len(block)
        raw = block[s[0] + len(s[1]) : s_end]
        strong = _slice_between(raw, 0, None)

    if st:
        raw = block[st[0] + len(st[1]) :]
        staff = _slice_between(raw, 0, None)
        if staff.lower().startswith("mention "):
            staff = staff[0].upper() + staff[1:]
        for stop in ("Why the deep dives connect",):
            i = staff.find(stop)
            if i >= 0:
                staff = staff[:i].strip().rstrip(" .")

    # Bitly-style: "Weak candidates describe X. Strong candidates articulate Y."
    if not weak and "Weak candidates" in block:
        m = re.search(r"Weak candidates\s+(.+?)(?=Strong candidates|Staff\+|Staff-level|$)", block, re.S)
        if m:
            weak = _slice_between(m.group(1), 0, None)

    if not strong and "Strong candidates" in block:
        m = re.search(
            r"Strong candidates\s+(.+?)(?=Start with|Staff\+|Staff-level|The better|For Staff\+|$)",
            block,
            re.S,
        )
        if m:
            strong = _slice_between(m.group(1), 0, None)
        elif not strong and w and st:
            mid = block[w[0] + len(w[1]) : st[0]]
            strong = _slice_between(mid, 0, None)

    if not staff:
        for pat in (
            r"Staff-level concern is\s+(.+?)(?=For Staff\+|$)",
            r"Staff\+ mention\s+(.+?)(?=Deep dive|$)",
        ):
            m = re.search(pat, block, re.S)
            if m:
                staff = _slice_between(m.group(1), 0, None)
                break

    return Ladder(weak=weak, strong=strong, staff=staff)


def _strip_dive_header(block: str) -> str:
    return re.sub(r"^Deep dive \d+:\s*.+?\n", "", block.strip(), count=1).strip()


def _derive_weak_answer(title: str, strong: str) -> str:
    topic = title.split("—")[0].split("–")[0].strip().lower()
    rules: list[tuple[str, str]] = [
        ("dedup", "Retry until delivery succeeds — duplicates are rare."),
        ("fan-out", "Push to every device synchronously from the API handler."),
        ("rate limit", "Reject with 429 when count > N — no per-user fairness."),
        ("clock", "Use system clock on each machine — NTP is optional."),
        ("worker id", "Pick a random worker ID at process start."),
        ("personalization", "Build a per-user trie — one per user at scale."),
        ("latency", "Serve every request from origin — CDN is optional."),
        ("pipeline", "Rebuild the full index nightly — no incremental updates."),
        ("gdpr", "Delete the user row — async workers will stop eventually."),
        ("group chat", "Fan out one-by-one to all group members on every message."),
        ("swipe", "Store every swipe in Cassandra forever."),
        ("feed", "Query the database on every feed request."),
        ("upload", "Upload the whole file in one HTTP request."),
        ("payment", "Retry the charge on any timeout."),
        ("inventory", "UPDATE balance in SQL — no locking story."),
        ("search", "SELECT * WHERE column LIKE '%query%'."),
        ("counter", "One global INCR key for all traffic."),
        ("id ", "UUID v4 everywhere — collisions are negligible."),
    ]
    hay = f"{topic} {strong.lower()}"
    for key, weak in rules:
        if key in hay:
            return weak
    short = topic[:60] if topic else "this problem"
    return f"Oversimplify {short} — name one component, skip failure modes and metrics."


def fill_ladder_gaps(block: str, title: str, ladder: Ladder) -> Ladder:
    """Derive missing Weak/Strong/Staff+ when q5 uses prose-only deep dives."""
    weak, strong, staff = ladder.weak, ladder.strong, ladder.staff
    body = _strip_dive_header(block)
    st = _find_marker(block, STAFF_MARKERS)
    w = _find_marker(block, WEAK_MARKERS)
    s = _find_marker(block, STRONG_MARKERS)

    if not strong:
        if s:
            s_end = st[0] if st else len(block)
            raw = block[s[0] + len(s[1]) : s_end]
            strong = _slice_between(raw, 0, None)
        elif st:
            strong = _slice_between(body[: body.find(st[1])], 0, None)
        elif staff and st:
            strong = _slice_between(body[: body.find(st[1])], 0, None)
        else:
            strong = _slice_between(body, 0, None)

    if not staff:
        if st:
            raw = block[st[0] + len(st[1]) :]
            staff = _slice_between(raw, 0, None)
            for stop in ("Why the deep dives connect", "Deep dive "):
                i = staff.find(stop)
                if i >= 0:
                    staff = staff[:i].strip().rstrip(" .")
        elif strong and strong != _slice_between(body, 0, None):
            staff = "Name the metric you'd alert on and when you'd revisit this design."
        else:
            staff = "Name metric + revisit trigger when they push depth."

    if not weak:
        weak = _derive_weak_answer(title, strong or body)

    return Ladder(weak=weak, strong=strong, staff=staff, context=ladder.context)


def parse_deep_dives(q5: str) -> list[DeepDive]:
    if not q5:
        return []

    text = q5.replace("\\n", "\n")
    intro = ""
    m0 = re.match(r"^(The three deep dives.+?)\n\n", text, re.S)
    if m0:
        intro = m0.group(1).strip()

    parts = re.split(r"\n(?=Deep dive \d+:\s*)", text)
    dives: list[DeepDive] = []

    for part in parts:
        part = part.strip()
        if not part.startswith("Deep dive"):
            continue
        m = re.match(r"Deep dive (\d+):\s*(.+?)(?:\n|$)", part)
        if not m:
            continue
        num, title = m.group(1), m.group(2).strip()
        body = part[m.end() :].strip()
        ladder = extract_ladder(body)
        ladder = fill_ladder_gaps(body, title, ladder)
        # Context = scaling-pain line before ladder markers
        ctx_end = len(body)
        for marker in WEAK_MARKERS + STRONG_MARKERS + STAFF_MARKERS + ("Weak candidates", "Strong candidates"):
            i = body.find(marker)
            if i >= 0:
                ctx_end = min(ctx_end, i)
        context = _slice_between(body[:ctx_end], 0, None)
        context = re.sub(r"^Deep dive \d+:\s*.+?\s*", "", context).strip()
        if context:
            ladder.context = context
        dives.append(DeepDive(num=num, title=title, intro=context, ladder=ladder))

    return dives


def ladder_markdown(ladder: Ladder, *, compact: bool = False) -> str:
    if not any([ladder.weak, ladder.strong, ladder.staff]):
        return ""

    lines = []
    if ladder.weak:
        lines.append("> [!CAUTION]")
        lines.append(f"> **🔴 Weak** — {ladder.weak}")
        lines.append(">")
    if ladder.strong:
        lines.append("> [!WARNING]")
        lines.append(f"> **🟡 Strong** — {ladder.strong}")
        lines.append(">")
    if ladder.staff:
        lines.append("> [!TIP]")
        lines.append(f"> **🟢 Staff+** — {ladder.staff}")

    if compact:
        return "\n".join(lines)
    return "\n".join(lines) + "\n"


def format_q5_markdown(q5: str) -> str:
    dives = parse_deep_dives(q5)
    if not dives:
        return q5.replace("\\n", "\n")

    parts = []
    intro = ""
    if q5.strip().startswith("The three deep dives"):
        intro = parse_deep_dives(q5)
        # re-get intro line
        text = q5.replace("\\n", "\n")
        m0 = re.match(r"^(The three deep dives[^\n]+)", text)
        if m0:
            parts.append(m0.group(1))
            parts.append("")

    for d in dives:
        parts.append(f"#### Deep dive {d.num}: {d.title}")
        if d.intro:
            parts.append(f"_{d.intro}_")
            parts.append("")
        lm = ladder_markdown(d.ladder)
        if lm:
            parts.append(lm)
        else:
            # fallback: keep original body snippet
            parts.append("_See interactive cheatsheet for full narrative._")
        parts.append("")

    # closing arc
    if "Why the deep dives connect" in q5:
        m = re.search(r"(Why the deep dives connect.+)$", q5.replace("\\n", "\n"), re.S)
        if m:
            parts.append(f"_{m.group(1).strip()}_")

    return "\n".join(parts).strip() + "\n"


def format_q5_html(q5: str) -> str:
    dives = parse_deep_dives(q5)
    if not dives:
        return ""

    def esc(s: str) -> str:
        return html.escape(s or "", quote=False)

    h = ""
    text = q5.replace("\\n", "\n")
    if text.strip().startswith("The three deep dives"):
        m0 = re.match(r"^(The three deep dives[^\n]+)", text)
        if m0:
            h += f"<p><em>{esc(m0.group(1))}</em></p>"

    for d in dives:
        h += f'<div class="dd-title"><span class="dd-num">Deep dive {d.num}:</span> {esc(d.title)}</div>'
        if d.intro:
            h += f'<p class="dd-context"><em>{esc(d.intro)}</em></p>'
        lad = d.ladder
        if lad.weak:
            h += f'<div class="ladder weak"><span class="ladder-lbl">🔴 Weak</span><p>{esc(lad.weak)}</p></div>'
        if lad.strong:
            h += f'<div class="ladder strong"><span class="ladder-lbl">🟡 Strong</span><p>{esc(lad.strong)}</p></div>'
        if lad.staff:
            h += f'<div class="ladder staff"><span class="ladder-lbl">🟢 Staff+</span><p>{esc(lad.staff)}</p></div>'

    m = re.search(r"(Why the deep dives connect.+)$", text, re.S)
    if m:
        h += f'<p class="dd-context"><em>{esc(m.group(1).strip())}</em></p>'

    return h


# Quick-fire: one-line weak defaults by slug
QUICK_FIRE_WEAK: dict[str, str] = {
    "thundering-herd": "Add caching — TTL expires, everyone hits the DB.",
    "cache-stampede-dogpile": "Cache the expensive query with a fixed TTL.",
    "retry-storm": "Retry on any timeout — clients will eventually succeed.",
    "metastable-failure": "Wait for autoscale; retries will fix it.",
    "hot-partition-hot-key": "Scale Redis vertically when one key gets hot.",
    "split-brain": "Promote replica on primary failure — keep serving writes.",
    "poison-message": "Restart the consumer until the message processes.",
    "head-of-line-blocking": "One worker pool for all job types.",
    "n1-queries": "Load related rows in a loop — simple and correct.",
    "connection-pool-exhaustion": "Increase max connections on the database.",
    "replica-lag-stale-read": "Add read replicas and route all reads there.",
    "slow-node-straggler": "Wait for the slowest shard — correctness first.",
    "dual-write-problem": "Write to DB and cache in the same request handler.",
    "circular-dependency-retry-loop": "Service A calls B calls A with retries enabled.",
    "reduce-db-read-load": "Put Redis in front of the database.",
    "hot-key-viral-content": "Bigger Redis instance when a celebrity posts.",
    "stale-cache-after-update": "Delete cache key on every write — always consistent.",
    "reduce-global-read-latency": "Deploy one big CDN in the US — covers everyone.",
    "pagination-at-scale": "OFFSET/LIMIT — page 10,000 is fine if indexed.",
    "search-across-billions-of-records": "SELECT * WHERE title LIKE '%query%'.",
    "autocomplete-typeahead": "Prefix scan on the users table on every keystroke.",
    "scale-writes-past-single-db": "Shard later when Postgres is full.",
    "high-write-burst-flash-sale": "Queue everyone in one mutex — fairness first.",
    "idempotent-writes": "Check if row exists, then INSERT — good enough.",
    "prevent-double-booking": "SELECT then UPDATE in application code.",
    "distributed-counter": "INCR one global Redis key for all traffic.",
    "unique-id-at-scale": "UUID v4 everywhere — collisions are negligible.",
    "handle-traffic-spikes": "Autoscale app servers; the DB will keep up.",
    "eliminate-single-point-of-failure": "Run two of everything in one AZ.",
    "db-primary-fails": "Manual failover when someone pages you.",
    "cascading-failure": "Retry until downstream recovers.",
    "regional-outage": "Multi-region active-active from day one.",
    "zero-downtime-deploy": "Rolling restart — users won't notice brief errors.",
    "strong-vs-eventual-consistency": "Always use strong consistency — users hate stale data.",
    "guarantee-exactly-once": "Kafka exactly-once semantics solve it end-to-end.",
    "cross-service-transaction": "Two-phase commit across all microservices.",
    "read-your-writes": "Sticky sessions to any random replica.",
    "fan-out-to-millions-of-followers": "Push every post to every follower's feed on write.",
    "websocket-at-scale": "One giant WebSocket server holds all connections.",
    "push-notifications-at-scale": "Loop over all device tokens and send synchronously.",
    "store-large-files": "Multipart upload to S3 in one HTTP request.",
    "video-streaming": "Serve the original 4K file — clients buffer.",
    "decouple-services": "REST sync call chain between every service.",
    "webhook-delivery": "Fire-and-forget HTTP POST from the request path.",
    "rate-limiting": "Return 429 when count > 100 — no per-user fairness.",
    "ddos-abuse": "Block bad IPs in application code after they hit us.",
    "debug-production-incidents": "SSH in and tail logs on one server.",
    "cardinality-explosion-metrics": "Tag every span with user_id for rich dashboards.",
    "nearby-search-yelp-uber": "PostGIS radius query on every map pan.",
    "payment-correctness": "Charge the card; if timeout, retry the charge.",
    "inventory-wallet-balance": "UPDATE balance = balance - amount — SQL is atomic.",
}


def derive_quick_fire_ladder(
    slug: str, title: str, staff: str, trade: str, example: str
) -> Ladder:
    weak = QUICK_FIRE_WEAK.get(slug, "")
    if not weak:
        m = re.search(r"\*\*([^*]+)\*\*", staff)
        weak = f'Name "{m.group(1) if m else "a tool"}" without trade-offs or failure modes.'
    strong = staff
    parts = []
    if trade:
        parts.append(trade)
    if example:
        parts.append(f"Example: {example.strip('* ')}")
    parts.append("Name metric + revisit trigger when they push depth.")
    staff_plus = " ".join(parts)
    return Ladder(weak=weak, strong=strong, staff=staff_plus)


def quick_fire_ladder_block(ladder: Ladder) -> str:
    return (
        "\n> [!CAUTION]\n"
        f"> **🔴 Weak** — {ladder.weak}\n"
        ">\n"
        "> [!WARNING]\n"
        f"> **🟡 Strong** — {ladder.strong}\n"
        ">\n"
        "> [!TIP]\n"
        f"> **🟢 Staff+** — {ladder.staff}\n"
    )


LADDER_CSS = """
.ladder{margin:10px 14px;padding:10px 12px;border-radius:8px;border-left:4px solid;font-size:12.5px;line-height:1.65}
.ladder-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;display:block;margin-bottom:4px}
.ladder.weak{background:var(--bg-dang);border-color:var(--bdr-dang)}
.ladder.strong{background:var(--bg-warn);border-color:var(--bdr-warn)}
.ladder.staff{background:var(--bg-succ);border-color:var(--bdr-succ)}
.ladder.weak .ladder-lbl{color:var(--txt-dang)}
.ladder.strong .ladder-lbl{color:var(--txt-warn)}
.ladder.staff .ladder-lbl{color:var(--txt-succ)}
.dd-context{font-size:12px;color:var(--txt-sec);margin:6px 14px 10px;line-height:1.6}
"""
