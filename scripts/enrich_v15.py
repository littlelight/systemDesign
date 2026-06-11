#!/usr/bin/env python3
"""Enrich v15 cheatsheet: 8 follow-ups per card, see-also links, 4 new systems."""

import re
import json
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "cheatSheet" / "system_design_cheatsheet_v14.html"

SEE_ALSO = {
    "Bitly — URL shortener": '<a href="SystemDesign_Complete_v10.html">v10 reference</a> · URL shortener in System Cards',
    "WhatsApp — messaging": '<a href="SystemDesign_Complete_v10.html">v10</a> · real-time messaging patterns',
    "Yelp — local search": '<a href="SystemDesign_Complete_v10.html">v10</a> · geospatial + search index',
    "YouTube — video platform": '<a href="#card-17">YouTube Top K</a> · streaming vs ranking',
    "YouTube Top K videos": '<a href="#card-22">YouTube video platform</a> · upload/streaming path',
    "FB News Feed": '<a href="#card-3">Google News</a> · feed aggregation overlap',
    "Distributed cache (Redis-like)": '<a href="SystemDesign_Complete_v10.html">v10</a> · Redis / caching foundations',
    "Notification system (APNs/FCM)": '<a href="SystemDesign_Complete_v10.html">v10 Vol 2</a> · notification system',
    "Message queue (Kafka)": '<a href="SystemDesign_Complete_v10.html">v10</a> · pub/sub &amp; streaming',
}

EXTRA_FQ_TEMPLATES = [
    (
        "What metrics and alerts would you put on this system?",
        "Track golden signals: latency p50/p99 per API, error rate, saturation (CPU, queue depth, cache hit ratio). "
        "Business metrics: {metric_hint}. Alert on SLO burn — e.g. p99 redirect latency >200ms for 5min, "
        "cache hit ratio drop below 90%, or write failure rate spike. Dashboard per service with dependency health.",
    ),
    (
        "How would you test and roll out changes safely?",
        "Contract tests on APIs, load tests on read/write hot paths, chaos tests on Redis/DB failures. "
        "Shadow traffic for risky changes (new ranking, new ID scheme). Feature flags for incremental rollout. "
        "Canary 1% → 10% → 100% with automatic rollback on error-rate regression.",
    ),
    (
        "How do you handle a regional outage or disaster recovery?",
        "Multi-AZ by default; multi-region for critical paths. Define RPO/RTO: {dr_hint}. "
        "Async replication to secondary region; DNS/geo routing failover. Run game days. "
        "Document degraded mode — what features drop vs what must stay up.",
    ),
]

METRIC_HINTS = {
    "e": "success rate, latency, active users",
    "m": "throughput, queue lag, cache effectiveness",
    "h": "end-to-end latency, consistency lag, fan-out depth",
}

DR_HINTS = {
    "e": "minutes of read unavailability acceptable; rebuild cache from DB",
    "m": "async replication lag <30s; failover promotes read replica",
    "h": "active-active or warm standby; conflict resolution on merge",
}


def make_extra_fq(diff: str, title: str, n: int) -> list:
    out = []
    for i, (q, a) in enumerate(EXTRA_FQ_TEMPLATES[:n]):
        a = a.format(
            metric_hint=METRIC_HINTS.get(diff, METRIC_HINTS["m"]),
            dr_hint=DR_HINTS.get(diff, DR_HINTS["m"]),
        )
        out.append({"q": q, "a": a})
    return out


def enrich_fq_block(fq_body: str, diff: str, title: str) -> str:
    count = fq_body.count('"q":')
    if count >= 8:
        return fq_body
    need = 8 - count
    extras = make_extra_fq(diff, title, need)
    extra_str = ", ".join(
        json.dumps(x, ensure_ascii=False) for x in extras
    )
    return fq_body.rstrip() + ", " + extra_str


def process_system(block: str, title: str) -> str:
    diff_m = re.match(r"'?([emh])", block.lstrip())
    diff = diff_m.group(1) if diff_m else "m"

    if title in SEE_ALSO and "seeAlso:" not in block:
        see = SEE_ALSO[title].replace("'", "\\'")
        block = re.sub(
            r"(tags:\[[^\]]+\],)",
            rf"\1seeAlso:'{see}',",
            block,
            count=1,
        )

    def repl(m):
        body = enrich_fq_block(m.group(1), diff, title)
        return "fq:[" + body + "],ev:"

    return re.sub(r"fq:\[(.*?)\],\s*ev:", repl, block, count=1, flags=re.S)


def main():
    text = HTML.read_text(encoding="utf-8")
    start = text.index("const C=[")
    end = text.index("function buildDiag", start)
    prefix, body, suffix = text[:start], text[start:end], text[end:]

    parts = re.split(r"(\n\{d:)", body)
    # parts[0] is 'const C=['; then alternating '\n{d:' and block content
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        sep = parts[i]
        block = parts[i + 1] if i + 1 < len(parts) else ""
        title_m = re.search(r"title:'([^']+)'", block)
        title = title_m.group(1) if title_m else ""
        block = process_system(block, title)
        out.append(sep)
        out.append(block)

    new_body = "".join(out)
    HTML.write_text(prefix + new_body + suffix, encoding="utf-8")
    print("Enriched follow-ups and see-also links.")


if __name__ == "__main__":
    main()
