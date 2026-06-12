#!/usr/bin/env python3
"""Build Notion-style colorful HTML from interview-quick-fire.md and enrich the MD with severity badges."""

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "cheatSheet" / "interview-quick-fire.md"
HTML_PATH = ROOT / "cheatSheet" / "interview-quick-fire.html"

SEVERITY = {
    "critical": {
        "emoji": "🔴",
        "label": "Critical",
        "alert": "CAUTION",
        "hint": "Outage / data-loss risk — probe failure modes first",
        "css": "sev-critical",
    },
    "high": {
        "emoji": "🟠",
        "label": "High",
        "alert": "WARNING",
        "hint": "Resilience under stress — name degraded mode + recovery",
        "css": "sev-high",
    },
    "important": {
        "emoji": "🟣",
        "label": "Important",
        "alert": "IMPORTANT",
        "hint": "Correctness / invariants — strong consistency territory",
        "css": "sev-important",
    },
    "pattern": {
        "emoji": "🟢",
        "label": "Pattern",
        "alert": "TIP",
        "hint": "Core design pattern — pattern + trade-off + anchor",
        "css": "sev-pattern",
    },
    "prep": {
        "emoji": "🔵",
        "label": "Prep",
        "alert": "NOTE",
        "hint": "Interview framework — how to answer and go deeper",
        "css": "sev-prep",
    },
}

SECTION_SEV = {
    "Classic failure modes & distributed pitfalls": "critical",
    "Availability & resilience": "high",
    "Security & abuse": "high",
    "Consistency & correctness": "important",
    "Money & transactions": "important",
    "Writes & throughput": "pattern",
    "Reads & caching": "pattern",
    "Fan-out & real-time": "pattern",
    "Storage & media": "pattern",
    "Messaging & async": "pattern",
    "Geo & search": "pattern",
    "Observability & ops": "prep",
    "How to go deeper — interview prep": "prep",
    "Answer template (use every time)": "prep",
    "Visual archetypes": "prep",
    "Practice drill": "prep",
    "Quick decision shortcuts": "prep",
    "Navigation": "prep",
}

PATTERN_OVERRIDE = {
    "High write burst (flash sale)": "high",
    "Prevent double booking": "high",
    "Cascading failure": "high",
    "DB primary fails": "high",
    "Metastable failure": "critical",
    "Split brain": "critical",
    "Dual-write problem": "critical",
    "Payment correctness": "critical",
    "Inventory / wallet balance": "critical",
    "Retry storm": "critical",
}


def slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


def parse_patterns(body: str) -> list[dict]:
    parts = re.split(r"\n### ", body)
    patterns = []
    for part in parts[1:]:
        lines = part.strip().split("\n")
        title = lines[0].strip()
        title = re.sub(r"^[🔴🟠🟣🟢🔵]\s*", "", title)
        title = re.sub(r"^Diagram ·\s*", "Diagram · ", title)  # keep diagram titles distinct
        rest = "\n".join(lines[1:])
        weak = staff = staff_plus = trade = example = visual = ""
        m = re.search(r"\*\*🔴 Weak\*\* —\s*(.+?)(?=\n> \[!|\n\*\*Trade|\n📊|\n---|\Z)", rest, re.S)
        if m:
            weak = re.sub(r"\n> ?", " ", m.group(1)).strip()
        m = re.search(
            r"\*\*🟡 Strong\*\* —\s*(.+?)(?=\n> \[!|\n\*\*Trade|\n📊|\n---|\Z)",
            rest,
            re.S,
        )
        if m:
            staff = re.sub(r"\n> ?", " ", m.group(1)).strip()
        if not staff:
            m = re.search(
                r"(?:^|\n)>?\s*\*\*Staff-level answer:\*\*\s*(.+?)(?=\n>?\s*\*\*Trade-offs|\n\n📊|\n---|\n### |\Z)",
                rest,
                re.S,
            )
            if m:
                staff = re.sub(r"\n> ?", " ", m.group(1)).strip()
        m = re.search(
            r"\*\*🟢 Staff\+\*\* —\s*(.+?)(?=\n\*\*Trade|\n📊|\n---|\Z)",
            rest,
            re.S,
        )
        if m:
            staff_plus = re.sub(r"\n> ?", " ", m.group(1)).strip()
        else:
            staff_plus = ""
        m = re.search(
            r"(?:^|\n)>?\s*\*\*Trade-offs:\*\*\s*(.+?)(?=\n>?\s*\*\*Example|\n\n📊|\n---|\n### |\Z)",
            rest,
            re.S,
        )
        if m:
            trade = re.sub(r"\n> ?", " ", m.group(1)).strip()
        m = re.search(
            r"(?:^|\n)>?\s*\*\*Example:\*\*\s*(.+?)(?=\n\n📊|\n---|\n### |\Z)",
            rest,
            re.S,
        )
        if m:
            example = re.sub(r"\n> ?", " ", m.group(1)).strip()
        m = re.search(r"📊 \*\*Visual:\*\*\s*(.+)", rest)
        if m:
            visual = m.group(1).strip()
        if not staff or title.startswith("Diagram ·") or title.startswith("Pattern →"):
            continue
        patterns.append(
            {
                "title": title,
                "slug": slugify(title),
                "weak": weak,
                "staff": staff,
                "staff_plus": staff_plus,
                "trade": trade,
                "example": example,
                "visual": visual,
            }
        )
    return patterns


def parse_sections(md: str) -> list[dict]:
    md = md.split("\n", 1)[1] if md.startswith("# ") else md
    intro = ""
    if "## Navigation" in md:
        intro, md = md.split("## Navigation", 1)
        md = "## Navigation" + md

    chunks = re.split(r"\n(?=## )", md)
    sections = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk.startswith("## "):
            continue
        first_nl = chunk.find("\n")
        title = chunk[3:first_nl].strip() if first_nl != -1 else chunk[3:].strip()
        body = chunk[first_nl + 1 :] if first_nl != -1 else ""
        sev_key = SECTION_SEV.get(title, "prep")
        patterns = parse_patterns(body) if title not in ("Navigation",) else []
        for p in patterns:
            p["severity"] = PATTERN_OVERRIDE.get(p["title"], sev_key)
        sections.append(
            {
                "title": title,
                "slug": slugify(title),
                "severity": sev_key,
                "body": body,
                "patterns": patterns,
            }
        )
    return intro.strip(), sections


def md_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def enrich_markdown(md: str, sections: list[dict]) -> str:
    legend = """
## Severity legend

| Badge | Level | When interviewers probe here |
|-------|-------|------------------------------|
| 🔴 **Critical** | Outage / cascade / data loss | Failure modes, "what if X dies?", metastable |
| 🟠 **High** | Resilience under stress | Availability, security, flash-sale contention |
| 🟣 **Important** | Correctness & invariants | Consistency tiers, money, inventory |
| 🟢 **Pattern** | Standard staff answer | Reads, writes, fan-out, storage flows |
| 🔵 **Prep** | Framework & drill | Answer template, DMOP, practice |

> [!NOTE]
> **Colorful view:** open [interview-quick-fire.html](interview-quick-fire.html) for Notion-style callouts, filters, and severity sidebar.

"""

    if "## Severity legend" in md:
        md = re.sub(r"\n## Severity legend\n.*?(?=\n## Navigation)", "\n" + legend, md, flags=re.S)
    else:
        md = md.replace(
            "\n## Navigation",
            legend + "\n## Navigation",
            1,
        )

    for sec in sections:
        if not sec["patterns"]:
            continue
        sev = SEVERITY[sec["severity"]]
        banner = (
            f"\n> [!{sev['alert']}]\n"
            f"> **{sev['emoji']} {sev['label']}** — {sev['hint']}\n\n"
        )
        marker = f"## {sec['title']}"
        if banner.strip() not in md and f"## {sec['title']}\n\n> [!{sev['alert']}]" not in md:
            md = md.replace(f"## {sec['title']}\n", f"## {sec['title']}\n{banner}", 1)

        for p in sec["patterns"]:
            if p["title"].startswith("Diagram ·"):
                continue
            sev_p = SEVERITY[p["severity"]]
            md = md.replace(f"### {sev_p['emoji']} {p['title']}", f"### {p['title']}", 1)  # normalize
            if f"### {sev_p['emoji']} {p['title']}\n" not in md:
                md = md.replace(f"### {p['title']}\n", f"### {sev_p['emoji']} {p['title']}\n", 1)

            if not p["staff"]:
                continue
            if f"**🔴 Weak** —" in md and f"### {sev_p['emoji']} {p['title']}" in md:
                continue
            if f"> [!{sev_p['alert']}]\n> **Staff-level answer:** {p['staff'][:40]}" in md:
                continue
            card = (
                f"\n> [!{sev_p['alert']}]\n"
                f"> **Staff-level answer:** {p['staff']}\n>\n"
                f"> **Trade-offs:** {p['trade']}\n>\n"
                f"> **Example:** {p['example']}\n"
            )
            if p["visual"]:
                card += f"\n📊 **Visual:** {p['visual']}\n"

            block_re = re.compile(
                rf"### {re.escape(sev_p['emoji'])} {re.escape(p['title'])}\n\n"
                rf"(?:\*\*Staff-level answer:\*\*.*?(?=\n---|\n### |\Z))",
                re.S,
            )
            md = block_re.sub(f"### {sev_p['emoji']} {p['title']}\n{card}\n", md, count=1)

    if "interview-quick-fire.html" not in md.split("## Navigation")[0]:
        md = md.replace(
            "interview-quick-fire-diagrams.html)",
            "interview-quick-fire-diagrams.html) · [Colorful view](interview-quick-fire.html)",
            1,
        )
    nav_colorful = "- [Colorful HTML view](interview-quick-fire.html) — Notion-style severity callouts\n"
    if nav_colorful not in md:
        md = md.replace("## Navigation\n\n", f"## Navigation\n\n{nav_colorful}", 1)

    return md


def build_html(intro: str, sections: list[dict]) -> str:
    data = []
    for sec in sections:
        if sec["title"] == "Navigation":
            continue
        data.append(
            {
                "title": sec["title"],
                "slug": sec["slug"],
                "severity": sec["severity"],
                "patterns": [
                    {
                        **p,
                        "weak_html": md_inline(p.get("weak", "")),
                        "staff_html": md_inline(p["staff"]),
                        "staff_plus_html": md_inline(p.get("staff_plus", "")),
                        "trade_html": md_inline(p["trade"]),
                        "example_html": md_inline(p["example"]),
                        "visual_html": md_inline(p["visual"]) if p["visual"] else "",
                    }
                    for p in sec["patterns"]
                ],
            }
        )

    payload = json.dumps(data, ensure_ascii=False)
    intro_html = md_inline(intro) if intro else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Interview Quick-Fire — Colorful View</title>
<meta name="description" content="Notion-style system design quick-fire — severity-coded callouts, filters, offline.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23185fa5'/><text x='16' y='22' text-anchor='middle' fill='white' font-size='14' font-family='sans-serif' font-weight='700'>SD</text></svg>">
<style>
:root{{
  --bg:#f7f6f3;--card:#fff;--text:#37352f;--muted:#787774;
  --critical-bg:#fdebec;--critical-bdr:#e16259;--critical-txt:#7f1d1d;
  --high-bg:#fbf3db;--high-bdr:#d9a006;--high-txt:#713f12;
  --important-bg:#f3e8ff;--important-bdr:#9065b0;--important-txt:#581c87;
  --pattern-bg:#edf3ec;--pattern-bdr:#448361;--pattern-txt:#1a3d2a;
  --prep-bg:#e7f3f8;--prep-bdr:#337ea9;--prep-txt:#0c4a6e;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --r:10px;
}}
html[data-theme="dark"]{{
  --bg:#191919;--card:#252525;--text:#e3e2de;--muted:#9b9a97;
  --critical-bg:#3d1f1f;--critical-bdr:#e16259;--critical-txt:#fecaca;
  --high-bg:#3d3018;--high-bdr:#d9a006;--high-txt:#fde68a;
  --important-bg:#2e1f3d;--important-bdr:#9065b0;--important-txt:#e9d5ff;
  --pattern-bg:#1f2e22;--pattern-bdr:#448361;--pattern-txt:#bbf7d0;
  --prep-bg:#1a2a33;--prep-bdr:#337ea9;--prep-txt:#bae6fd;
}}
@media(prefers-color-scheme:dark){{
  html:not([data-theme="light"]){{
    --bg:#191919;--card:#252525;--text:#e3e2de;--muted:#9b9a97;
    --critical-bg:#3d1f1f;--critical-bdr:#e16259;--critical-txt:#fecaca;
    --high-bg:#3d3018;--high-bdr:#d9a006;--high-txt:#fde68a;
    --important-bg:#2e1f3d;--important-bdr:#9065b0;--important-txt:#e9d5ff;
    --pattern-bg:#1f2e22;--pattern-bdr:#448361;--pattern-txt:#bbf7d0;
    --prep-bg:#1a2a33;--prep-bdr:#337ea9;--prep-txt:#bae6fd;
  }}
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.55}}
a{{color:var(--prep-bdr)}}
.app{{display:flex;min-height:100vh}}
.sidebar{{width:280px;background:var(--card);border-right:1px solid rgba(0,0,0,.08);padding:16px 12px;position:sticky;top:0;height:100vh;overflow-y:auto;flex-shrink:0}}
.main{{flex:1;max-width:920px;padding:28px 32px 80px}}
h1{{font-size:1.75rem;margin-bottom:8px;letter-spacing:-.02em}}
.lead{{color:var(--muted);margin-bottom:20px;font-size:.95rem}}
.legend{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:20px 0 28px}}
.leg{{padding:10px 12px;border-radius:var(--r);border-left:4px solid;font-size:.8rem;font-weight:600}}
.leg small{{display:block;font-weight:400;color:var(--muted);margin-top:2px;font-size:.72rem}}
.leg.critical{{background:var(--critical-bg);border-color:var(--critical-bdr);color:var(--critical-txt)}}
.leg.high{{background:var(--high-bg);border-color:var(--high-bdr);color:var(--high-txt)}}
.leg.important{{background:var(--important-bg);border-color:var(--important-bdr);color:var(--important-txt)}}
.leg.pattern{{background:var(--pattern-bg);border-color:var(--pattern-bdr);color:var(--pattern-txt)}}
.leg.prep{{background:var(--prep-bg);border-color:var(--prep-bdr);color:var(--prep-txt)}}
.toolbar{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px;align-items:center}}
.toolbar input{{flex:1;min-width:180px;padding:8px 12px;border-radius:8px;border:1px solid rgba(0,0,0,.12);background:var(--card);color:var(--text)}}
.fchip{{font-size:.75rem;padding:5px 12px;border-radius:100px;border:1px solid rgba(0,0,0,.1);background:var(--card);cursor:pointer}}
.fchip.on{{font-weight:600}}
.fchip.critical.on{{background:var(--critical-bg);border-color:var(--critical-bdr);color:var(--critical-txt)}}
.fchip.high.on{{background:var(--high-bg);border-color:var(--high-bdr);color:var(--high-txt)}}
.fchip.important.on{{background:var(--important-bg);border-color:var(--important-bdr);color:var(--important-txt)}}
.fchip.pattern.on{{background:var(--pattern-bg);border-color:var(--pattern-bdr);color:var(--pattern-txt)}}
.fchip.prep.on{{background:var(--prep-bg);border-color:var(--prep-bdr);color:var(--prep-txt)}}
.tb{{font-size:.8rem;padding:6px 12px;border-radius:8px;border:1px solid rgba(0,0,0,.1);background:var(--card);cursor:pointer;color:var(--muted)}}
.sec{{margin-bottom:36px}}
.sec-hdr{{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding:12px 16px;border-radius:var(--r);font-size:1.05rem;font-weight:700}}
.sec-hdr.critical{{background:var(--critical-bg);color:var(--critical-txt);border:1px solid var(--critical-bdr)}}
.sec-hdr.high{{background:var(--high-bg);color:var(--high-txt);border:1px solid var(--high-bdr)}}
.sec-hdr.important{{background:var(--important-bg);color:var(--important-txt);border:1px solid var(--important-bdr)}}
.sec-hdr.pattern{{background:var(--pattern-bg);color:var(--pattern-txt);border:1px solid var(--pattern-bdr)}}
.sec-hdr.prep{{background:var(--prep-bg);color:var(--prep-txt);border:1px solid var(--prep-bdr)}}
.card{{background:var(--card);border-radius:var(--r);margin-bottom:12px;overflow:hidden;border:1px solid rgba(0,0,0,.06);box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.card-hdr{{display:flex;align-items:center;gap:10px;padding:14px 16px;cursor:pointer;user-select:none}}
.card-hdr:hover{{background:rgba(0,0,0,.02)}}
.badge{{font-size:.7rem;font-weight:700;padding:3px 8px;border-radius:6px;white-space:nowrap}}
.badge.critical{{background:var(--critical-bg);color:var(--critical-txt);border:1px solid var(--critical-bdr)}}
.badge.high{{background:var(--high-bg);color:var(--high-txt);border:1px solid var(--high-bdr)}}
.badge.important{{background:var(--important-bg);color:var(--important-txt);border:1px solid var(--important-bdr)}}
.badge.pattern{{background:var(--pattern-bg);color:var(--pattern-txt);border:1px solid var(--pattern-bdr)}}
.badge.prep{{background:var(--prep-bg);color:var(--prep-txt);border:1px solid var(--prep-bdr)}}
.card-title{{font-weight:600;font-size:.95rem;flex:1}}
.card-chev{{color:var(--muted);transition:transform .15s}}
.card.open .card-chev{{transform:rotate(90deg)}}
.card-body{{display:none;border-top:1px solid rgba(0,0,0,.06)}}
.card.open .card-body{{display:block}}
.callout{{margin:12px 16px;padding:12px 14px;border-radius:8px;border-left:4px solid;font-size:.88rem;line-height:1.65}}
.callout strong{{display:block;margin-bottom:4px;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;opacity:.85}}
.callout.critical{{background:var(--critical-bg);border-color:var(--critical-bdr)}}
.callout.high{{background:var(--high-bg);border-color:var(--high-bdr)}}
.callout.important{{background:var(--important-bg);border-color:var(--important-bdr)}}
.callout.pattern{{background:var(--pattern-bg);border-color:var(--pattern-bdr)}}
.callout.prep{{background:var(--prep-bg);border-color:var(--prep-bdr)}}
.visual{{margin:0 16px 14px;font-size:.82rem;color:var(--muted)}}
.visual a{{font-weight:500}}
.sb-link{{display:block;padding:5px 10px;font-size:.8rem;color:var(--muted);text-decoration:none;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sb-link:hover{{background:rgba(0,0,0,.04);color:var(--text)}}
.sb-sec{{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);padding:12px 8px 4px}}
.sb-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}}
.links{{margin-top:16px;font-size:.85rem}}
.links a{{margin-right:12px}}
code{{font-family:var(--mono);font-size:.85em;background:rgba(0,0,0,.06);padding:1px 5px;border-radius:4px}}
@media(max-width:768px){{
  .app{{display:block}}
  .sidebar{{position:relative;height:auto;width:100%;border-right:none;border-bottom:1px solid rgba(0,0,0,.08)}}
  .main{{padding:20px 16px}}
}}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div style="font-weight:700;font-size:.9rem;margin-bottom:4px">Quick-fire</div>
    <div style="font-size:.75rem;color:var(--muted);margin-bottom:12px">Severity sidebar</div>
    <div id="sb-nav"></div>
    <div class="links">
      <a href="interview-quick-fire.md">Markdown</a>
      <a href="interview-quick-fire-diagrams.html">Diagrams</a>
      <a href="index.html">Index</a>
    </div>
  </aside>
  <main class="main">
    <h1>Interview quick-fire</h1>
    <p class="lead">{intro_html or "Problem → staff-level answer. Filter by severity — drill critical failure modes first."}</p>
    <div class="legend">
      <div class="leg critical">🔴 Critical<small>Outage / cascade</small></div>
      <div class="leg high">🟠 High<small>Resilience stress</small></div>
      <div class="leg important">🟣 Important<small>Correctness / money</small></div>
      <div class="leg pattern">🟢 Pattern<small>Standard flows</small></div>
      <div class="leg prep">🔵 Prep<small>Framework & drill</small></div>
    </div>
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search patterns…" autocomplete="off">
      <button class="fchip critical" data-f="critical" type="button">🔴 Critical</button>
      <button class="fchip high" data-f="high" type="button">🟠 High</button>
      <button class="fchip important" data-f="important" type="button">🟣 Important</button>
      <button class="fchip pattern" data-f="pattern" type="button">🟢 Pattern</button>
      <button class="fchip prep on" data-f="prep" type="button">🔵 Prep</button>
      <button class="fchip on" data-f="all" type="button">All</button>
      <button class="tb" id="theme" type="button">◐ Theme</button>
      <button class="tb" id="expand" type="button">Expand all</button>
    </div>
    <div id="root"></div>
  </main>
</div>
<script>
const SEV = {json.dumps(SEVERITY, ensure_ascii=False)};
const DATA = {payload};

function render() {{
  const root = document.getElementById('root');
  const sb = document.getElementById('sb-nav');
  const q = (document.getElementById('q').value || '').toLowerCase();
  const active = [...document.querySelectorAll('.fchip.on')].map(b => b.dataset.f);
  const showAll = active.includes('all') || active.length === 0;
  root.innerHTML = '';
  sb.innerHTML = '';
  DATA.forEach(sec => {{
    const patterns = sec.patterns.filter(p => {{
      if (!showAll && !active.includes(p.severity)) return false;
      const hay = (p.title + ' ' + (p.weak||'') + ' ' + p.staff + ' ' + p.trade).toLowerCase();
      return !q || hay.includes(q);
    }});
    if (!patterns.length) return;
    const s = SEV[sec.severity] || SEV.prep;
    const secEl = document.createElement('section');
    secEl.className = 'sec';
    secEl.id = sec.slug;
    secEl.innerHTML = `<div class="sec-hdr ${{sec.severity}}">${{s.emoji}} ${{sec.title}}</div>`;
    const sbSec = document.createElement('div');
    sbSec.innerHTML = `<div class="sb-sec">${{s.emoji}} ${{sec.title}}</div>`;
    patterns.forEach(p => {{
      const ps = SEV[p.severity] || SEV.pattern;
      const card = document.createElement('div');
      card.className = 'card';
      card.id = p.slug;
      card.innerHTML = `
        <div class="card-hdr">
          <span class="badge ${{p.severity}}">${{ps.emoji}} ${{ps.label}}</span>
          <span class="card-title">${{p.title}}</span>
          <span class="card-chev">▶</span>
        </div>
        <div class="card-body">
          ${{p.weak_html ? `<div class="callout critical"><strong>🔴 Weak</strong>${{p.weak_html}}</div>` : ''}}
          <div class="callout ${{p.severity}}"><strong>🟡 Strong</strong>${{p.staff_html}}</div>
          ${{p.staff_plus_html ? `<div class="callout pattern"><strong>🟢 Staff+</strong>${{p.staff_plus_html}}</div>` : ''}}
          <div class="callout ${{p.severity}}" style="opacity:.92"><strong>Trade-offs</strong>${{p.trade_html}}</div>
          <div class="callout ${{p.severity}}" style="opacity:.85"><strong>Example</strong>${{p.example_html}}</div>
          ${{p.visual_html ? `<div class="visual">📊 <strong>Visual:</strong> ${{p.visual_html}}</div>` : ''}}
        </div>`;
      card.querySelector('.card-hdr').onclick = () => card.classList.toggle('open');
      secEl.appendChild(card);
      const link = document.createElement('a');
      link.className = 'sb-link';
      link.href = '#' + p.slug;
      link.innerHTML = `<span class="sb-dot" style="background:var(--${{p.severity}}-bdr)"></span>${{p.title}}`;
      link.onclick = e => {{ e.preventDefault(); document.getElementById(p.slug)?.scrollIntoView({{behavior:'smooth'}}); card.classList.add('open'); }};
      sbSec.appendChild(link);
    }});
    root.appendChild(secEl);
    sb.appendChild(sbSec);
  }});
}}

document.querySelectorAll('.fchip').forEach(b => {{
  b.onclick = () => {{
    if (b.dataset.f === 'all') {{
      document.querySelectorAll('.fchip').forEach(x => x.classList.toggle('on', x.dataset.f === 'all'));
    }} else {{
      document.querySelector('.fchip[data-f="all"]').classList.remove('on');
      b.classList.toggle('on');
      if (!document.querySelectorAll('.fchip.on').length) document.querySelector('.fchip[data-f="all"]').classList.add('on');
    }}
    render();
  }};
}});
document.getElementById('q').oninput = render;
document.getElementById('theme').onclick = () => {{
  const d = document.documentElement;
  const dark = d.dataset.theme ? d.dataset.theme === 'light' : !matchMedia('(prefers-color-scheme:dark)').matches;
  d.dataset.theme = dark ? 'dark' : 'light';
  localStorage.setItem('qf-color-theme', d.dataset.theme);
}};
const saved = localStorage.getItem('qf-color-theme');
if (saved) document.documentElement.dataset.theme = saved;
let expanded = false;
document.getElementById('expand').onclick = () => {{
  expanded = !expanded;
  document.querySelectorAll('.card').forEach(c => c.classList.toggle('open', expanded));
  document.getElementById('expand').textContent = expanded ? 'Collapse all' : 'Expand all';
}};
// Default: show critical + high for drill focus; user can click All
document.querySelectorAll('.fchip').forEach(x => x.classList.remove('on'));
['critical','high','important','pattern'].forEach(f => document.querySelector(`.fchip[data-f="${{f}}"]`)?.classList.add('on'));
render();
if (location.hash) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView({{behavior:'smooth'}}), 100);
</script>
</body>
</html>"""


def main():
    md = MD_PATH.read_text(encoding="utf-8")
    intro, sections = parse_sections(md)
    md = enrich_markdown(md, sections)
    MD_PATH.write_text(md, encoding="utf-8")
    HTML_PATH.write_text(build_html(intro, sections), encoding="utf-8")
    n_patterns = sum(len(s["patterns"]) for s in sections)
    print(f"Updated {MD_PATH.name} with severity badges & callouts")
    print(f"Wrote {HTML_PATH.name} ({n_patterns} patterns)")


if __name__ == "__main__":
    main()
