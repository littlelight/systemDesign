#!/usr/bin/env python3
"""Add Weak → Strong → Staff+ ladders to interview-quick-fire.md patterns."""

from __future__ import annotations

import re
from pathlib import Path

from staff_ladder import derive_quick_fire_ladder, quick_fire_ladder_block

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "cheatSheet" / "interview-quick-fire.md"


def slugify(title: str) -> str:
    title = re.sub(r"^[🔴🟠🟣🟢🔵]\s*", "", title)
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s)


def enrich(md: str) -> str:
    if "## Staff answer ladder" not in md:
        ladder_note = """
## Staff answer ladder

Interviewers score you by depth. For every pattern, practice three rungs:

| Rung | What to say | What it signals |
|------|-------------|-----------------|
| 🔴 **Weak** | Name a tool, skip trade-offs | Junior — pattern recall only |
| 🟡 **Strong** | Pattern + why it fits this workload | Mid — credible design |
| 🟢 **Staff+** | Failure mode + metric + when you'd revisit | Staff — operated production |

Each pattern below includes all three. **Default answer in interview:** Strong in 30s → offer Staff+ if they probe.

"""
        md = md.replace("## Severity legend", ladder_note + "## Severity legend", 1)

    parts = re.split(r"(?=\n### )", md)
    out = []
    for part in parts:
        if not part.startswith("\n### ") and not part.startswith("### "):
            out.append(part)
            continue
        m = re.match(r"\n?### ([🔴🟠🟣🟢🔵] )?(.+?)\n", part)
        if not m:
            out.append(part)
            continue
        emoji, title = m.group(1) or "", m.group(2).strip()
        if title.startswith("Diagram ·") or "diagram map" in title.lower():
            out.append(part)
            continue
        if "🔴 Weak" in part and "🟡 Strong" in part:
            out.append(part)
            continue

        staff = trade = example = ""
        sm = re.search(
            r"\*\*Staff-level answer:\*\*\s*(.+?)(?=\*\*Trade-offs:\*\*|\n📊|\n---|\Z)",
            part,
            re.S,
        )
        if sm:
            staff = re.sub(r"\n> ?", " ", sm.group(1)).strip()
        if not staff:
            sm2 = re.search(r"\*\*🟡 Strong\*\* —\s*(.+?)(?=\n> \[!TIP\]|\n\*\*Trade|\n📊|\n---|\Z)", part, re.S)
            if sm2:
                staff = re.sub(r"\n> ?", " ", sm2.group(1)).strip()
        tm = re.search(
            r"\*\*Trade-offs:\*\*\s*(.+?)(?=\*\*Example:\*\*|\n📊|\n---|\Z)",
            part,
            re.S,
        )
        if tm:
            trade = re.sub(r"\n> ?", " ", tm.group(1)).strip()
        em = re.search(
            r"\*\*Example:\*\*\s*(.+?)(?=\n📊|\n---|\Z)",
            part,
            re.S,
        )
        if em:
            example = re.sub(r"\n> ?", " ", em.group(1)).strip()

        if not staff:
            out.append(part)
            continue

        slug = slugify(title)
        ladder = derive_quick_fire_ladder(slug, title, staff, trade, example)

        vis_m = re.search(r"📊 \*\*Visual:\*\*.+", part)
        visual = vis_m.group(0) if vis_m else ""

        tail = ""
        if trade:
            tail += f"\n**Trade-offs:** {trade}\n"
        if example:
            tail += f"\n**Example:** {example}\n"
        if visual:
            tail += f"\n{visual}\n"

        header_match = re.match(r"\n?### ([🔴🟠🟣🟢🔵] )?(.+?)\n", part)
        hdr = f"### {header_match.group(1) or ''}{title}\n" if header_match else f"### {title}\n"
        new_part = "\n" + hdr + quick_fire_ladder_block(ladder) + tail
        out.append(new_part)

    return "".join(out)


def main():
    md = MD.read_text(encoding="utf-8")
    MD.write_text(enrich(md), encoding="utf-8")
    count = md.count("### ")
    print(f"Enriched {MD.name} with Weak → Strong → Staff+ ladders")


if __name__ == "__main__":
    main()
