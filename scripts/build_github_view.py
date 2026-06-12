#!/usr/bin/env python3
"""Build GitHub-viewable Markdown + static HTML from interactive cheatsheets.

GitHub strips <script> in HTML previews, so the dynamic cheatsheets show empty cards.
This generates:
  cheatSheet/github/README.md
  cheatSheet/github/v15/index.md + per-system .md files
  cheatSheet/github/v15-static.html  (no JS, uses <details>)
  cheatSheet/github/v10-cards/index.md + per-system .md files
"""

from __future__ import annotations

import html
import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from staff_ladder import format_q5_markdown  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
V15_HTML = ROOT / "cheatSheet" / "system_design_cheatsheet_v14.html"
V10_HTML = ROOT / "cheatSheet" / "SystemDesign_Complete_v10.html"
OUT = ROOT / "cheatSheet" / "github"

DIFF_LABEL = {"e": "Easy", "m": "Medium", "h": "Hard"}
DIFF_DIR = {"e": "easy", "m": "medium", "h": "hard"}


def slugify(title: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", title.lower()))


def unescape_js(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\`", "`")
    )


def find_bracket_end(text: str, start: int, open_c: str, close_c: str) -> int:
    depth = 0
    i = start
    in_sq = in_dq = in_bt = False
    esc = False
    while i < len(text):
        c = text[i]
        if esc:
            esc = False
            i += 1
            continue
        if c == "\\":
            esc = True
            i += 1
            continue
        if in_bt:
            if c == "`":
                in_bt = False
            i += 1
            continue
        if in_sq:
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == "`":
            in_bt = True
            i += 1
            continue
        if c == "'":
            in_sq = True
            i += 1
            continue
        if c == '"':
            in_dq = True
            i += 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_string_field(block: str, key: str) -> str:
    for pat in (
        rf'{key}:\s*"((?:[^"\\]|\\.)*)"',
        rf"{key}:\s*'((?:[^'\\]|\\.)*)'",
        rf"{key}:\s*`((?:[^`\\]|\\.)*)`",
    ):
        m = re.search(pat, block, re.S)
        if m:
            return unescape_js(m.group(1))
    return ""


def extract_array_field(block: str, key: str) -> str:
    m = re.search(rf"{key}:\[", block)
    if not m:
        return ""
    start = m.end() - 1
    end = find_bracket_end(block, start, "[", "]")
    return block[start + 1 : end] if end > 0 else ""


def extract_object_field(block: str, key: str) -> str:
    m = re.search(rf"{key}:\{{", block)
    if not m:
        return ""
    start = m.end() - 1
    end = find_bracket_end(block, start, "{", "}")
    return block[start + 1 : end] if end > 0 else ""


def parse_q3(body: str) -> list[tuple[str, str]]:
    items = []
    for m in re.finditer(
        r'\{b:"((?:[^"\\]|\\.)*)",t:"((?:[^"\\]|\\.)*)"\}'
        r"|\{b:'((?:[^'\\]|\\.)*)',t:'((?:[^'\\]|\\.)*)'\}",
        body,
    ):
        b = m.group(1) or m.group(3) or ""
        t = m.group(2) or m.group(4) or ""
        items.append((unescape_js(b), unescape_js(t)))
    return items


def parse_q4(body: str) -> list[tuple[str, str, str]]:
    items = []
    for m in re.finditer(
        r"\{(?:ord:'((?:[^'\\]|\\.)*)'|vs:'((?:[^'\\]|\\.)*)'),t:'((?:[^'\\]|\\.)*)'\}",
        body,
    ):
        label = unescape_js(m.group(1) or m.group(2) or "")
        items.append(("ord" if m.group(1) else "vs", label, unescape_js(m.group(3))))
    return items


def parse_q6(body: str) -> list[str]:
    lines = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', body):
        lines.append(unescape_js(m.group(1) or m.group(2) or ""))
    return lines


def parse_tags(block: str) -> list[str]:
    body = extract_array_field(block, "tags")
    return re.findall(r"'([^']+)'", body)


def parse_json_array(body: str) -> list:
    if not body.strip():
        return []
    try:
        return json.loads("[" + body + "]")
    except json.JSONDecodeError:
        return []


def strip_html_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def md_escape(s: str) -> str:
    return s.replace("|", "\\|")


def md_block(text: str) -> str:
    if not text:
        return "_Not available._\n"
    text = strip_html_tags(text)
    return textwrap.dedent(text).strip() + "\n"


def details(title: str, body: str, open_: bool = False) -> str:
    flag = " open" if open_ else ""
    return f"<details{flag}>\n<summary><strong>{html.escape(title)}</strong></summary>\n\n{body}\n</details>\n\n"


def parse_diag_cell(s: str) -> dict | None:
    s = s.strip()
    if s == "null":
        return None
    a = re.search(r"a:\s*'((?:[^'\\]|\\.)*)'", s)
    if a:
        l = re.search(r"l:\s*'((?:[^'\\]|\\.)*)'", s)
        return {
            "a": unescape_js(a.group(1)),
            "l": unescape_js(l.group(1)) if l else "",
        }
    t = re.search(r"t:\s*'((?:[^'\\]|\\.)*)'", s)
    sub = re.search(r"s:\s*'((?:[^'\\]|\\.)*)'", s)
    c = re.search(r"c:\s*'([^']*)'", s)
    return {
        "t": unescape_js(t.group(1)) if t else "",
        "s": unescape_js(sub.group(1)) if sub else "",
        "c": unescape_js(c.group(1)) if c else "sec",
    }


def parse_diag(block: str) -> list[list]:
    m = re.search(r"diag:\[", block)
    if not m:
        return []
    start = m.end() - 1
    end = find_bracket_end(block, start, "[", "]")
    if end < 0:
        return []
    inner = block[start + 1 : end]
    rows: list[list] = []
    i = 0
    while i < len(inner):
        while i < len(inner) and inner[i] in " \n,":
            i += 1
        if i >= len(inner):
            break
        if inner[i] != "[":
            i += 1
            continue
        row_end = find_bracket_end(inner, i, "[", "]")
        row_inner = inner[i + 1 : row_end]
        cells: list = []
        j = 0
        while j < len(row_inner):
            while j < len(row_inner) and row_inner[j] in " \n,":
                j += 1
            if j >= len(row_inner):
                break
            if row_inner[j : j + 4] == "null":
                cells.append(None)
                j += 4
                continue
            if row_inner[j] == "{":
                obj_end = find_bracket_end(row_inner, j, "{", "}")
                cells.append(parse_diag_cell(row_inner[j : obj_end + 1]))
                j = obj_end + 1
                continue
            j += 1
        rows.append(cells)
        i = row_end + 1
    return rows


def parse_scale_pills(block: str) -> list[dict]:
    scale_body = extract_object_field(block, "scale")
    if not scale_body:
        return []
    m = re.search(r"pills:\[(.*?)\]", scale_body, re.S)
    if not m:
        return []
    try:
        return json.loads("[" + m.group(1) + "]")
    except json.JSONDecodeError:
        return []


def split_v15_systems(html_text: str) -> list[str]:
    start = html_text.index("const C=[")
    end = html_text.index("function buildDiag", start)
    body = html_text[start:end]
    return re.split(r"(?:\n|,)\{d:", body)[1:]


def parse_v15_system(block: str, idx: int) -> dict:
    diff_m = re.match(r"'?([emh])", block.lstrip())
    diff = diff_m.group(1) if diff_m else "m"
    title = extract_string_field(block, "title")
    sub = extract_string_field(block, "sub")
    na = extract_string_field(block, "na")
    dn = extract_string_field(block, "dn")
    q1 = extract_string_field(block, "q1")
    q5 = extract_string_field(block, "q5")
    c3 = extract_string_field(block, "c3")
    c4 = extract_string_field(block, "c4")
    see_also = extract_string_field(block, "seeAlso")

    q3 = parse_q3(extract_array_field(block, "q3"))
    q4 = parse_q4(extract_array_field(block, "q4"))
    q6 = parse_q6(extract_array_field(block, "q6"))
    tags = parse_tags(block)

    v13_body = extract_object_field(block, "v13")
    failures = parse_json_array(extract_array_field(v13_body, "f"))
    est = {}
    est_m = re.search(r'est:(\{.*?\})(?:,|\s*d:)', v13_body, re.S)
    if est_m:
        try:
            est = json.loads(est_m.group(1))
        except json.JSONDecodeError:
            pass
    decisions = parse_json_array(extract_array_field(v13_body, "d"))
    followups = parse_json_array(extract_array_field(v13_body, "fq"))
    evolution = parse_json_array(extract_array_field(v13_body, "ev"))

    scale_body = extract_object_field(block, "scale")
    scale_text = extract_string_field("scale:{" + scale_body + "}", "body") if scale_body else ""
    if not scale_text:
        scale_text = extract_string_field(block.replace(scale_body, ""), "body")

    arch_body = extract_object_field(block, "arch")
    arch_d = extract_string_field("arch:{" + arch_body + "}", "d") if arch_body else ""
    arch_t = extract_string_field("arch:{" + arch_body + "}", "t") if arch_body else ""
    diag = parse_diag(block)
    scale_pills = parse_scale_pills(block)

    return {
        "idx": idx,
        "diff": diff,
        "title": title,
        "sub": sub,
        "slug": slugify(title),
        "tags": tags,
        "see_also": see_also,
        "na": na,
        "dn": dn,
        "q1": q1,
        "q3": q3,
        "c3": c3,
        "q4": q4,
        "c4": c4,
        "q5": q5,
        "q6": q6,
        "failures": failures,
        "est": est,
        "decisions": decisions,
        "followups": followups,
        "evolution": evolution,
        "scale": scale_text,
        "arch_d": arch_d,
        "arch_t": arch_t,
        "diag": diag,
        "scale_pills": scale_pills,
    }


def render_v15_markdown(s: dict) -> str:
    diff = DIFF_LABEL[s["diff"]]
    lines = [
        f"# {s['title']}",
        "",
        f"**{diff}** · {s['sub']}",
        "",
        f"Tags: {', '.join(f'`{t}`' for t in s['tags'])}",
        "",
    ]
    if s["see_also"]:
        lines += [f"_See also: {strip_html_tags(s['see_also'])}_", ""]
    lines += [
        "## Data flow",
        "",
        md_block(strip_html_tags(s["na"])),
        "",
    ]
    if s["dn"]:
        lines += [f"> {strip_html_tags(s['dn'])}", ""]

    if s.get("arch_d"):
        lines += [
            "## Architecture diagram",
            "",
            "```",
            s["arch_d"].strip(),
            "```",
            "",
        ]
        if s.get("arch_t"):
            lines += [md_block(s["arch_t"]), ""]

    lines += ["---", "", details("Problem", md_block(s["q1"]), open_=True)]

    if s["failures"]:
        body = ""
        for f in s["failures"]:
            body += f"**{f.get('w', '')}**\n\n{f.get('i', '')}\n\n_Fix:_ {f.get('x', '')}\n\n"
        lines.append(details("Failures", body))

    if s["est"]:
        body = "| Field | Value |\n|-------|-------|\n"
        for k, label in [
            ("a", "Assumptions"),
            ("r", "Read QPS"),
            ("w", "Write QPS"),
            ("s", "Storage"),
            ("c", "Cache math"),
            ("v", "Verdict"),
        ]:
            if s["est"].get(k):
                body += f"| {label} | {md_escape(str(s['est'][k]))} |\n"
        lines.append(details("Estimation", body + "\n"))

    if s["decisions"]:
        body = ""
        for d in s["decisions"]:
            body += f"**{d.get('q', '')}**\n\n→ {d.get('c', '')}\n\n{d.get('w', '')}\n\n_Revisit when:_ {d.get('r', '')}\n\n"
        lines.append(details("Design decisions", body))

    if s["followups"]:
        body = ""
        for fq in s["followups"]:
            body += f"**{fq.get('q', '')}**\n\n{fq.get('a', '')}\n\n"
        lines.append(details("Follow-up Q&A", body))

    if s["evolution"]:
        body = ""
        for ev in s["evolution"]:
            body += f"**{ev.get('s', '')}** — {ev.get('d', '')}\n\n"
        lines.append(details("Evolution", body))

    if s["scale"]:
        lines.append(details("Why it's hard to scale", md_block(s["scale"])))

    if s["q3"]:
        body = "\n".join(f"- **{b}** — {t}" for b, t in s["q3"])
        if s["c3"]:
            body += f"\n\n> {s['c3']}"
        lines.append(details("Key points", body + "\n"))

    if s["q4"]:
        body = ""
        for kind, label, text in s["q4"]:
            body += f"**{label}** — {text}\n\n"
        if s["c4"]:
            body += f"> {s['c4']}\n"
        lines.append(details("Tradeoffs", body + "\n"))

    if s["q5"]:
        lines.append(details("Deep dives", format_q5_markdown(s["q5"])))

    if s["q6"]:
        body = ""
        for i, line in enumerate(s["q6"]):
            body += f"{i + 1}. {line}\n\n"
        lines.append(details("Interview script", body))

    if s["arch_d"] or s["arch_t"]:
        body = ""
        if s["arch_d"]:
            body += "```\n" + s["arch_d"].strip() + "\n```\n\n"
        if s["arch_t"]:
            body += md_block(s["arch_t"])
        lines.append(details("Whiteboard", body))

    lines += [
        "---",
        "",
        "[← Back to v15 index](index.md) · "
        f"[Interactive version](../../system_design_cheatsheet_v14.html#card-{s['idx']})",
        "",
    ]
    return "\n".join(lines)


def render_v15_static_html(systems: list[dict], framework_snippet: str) -> str:
    css = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:900px;margin:0 auto;padding:24px 16px;line-height:1.6;color:#1a1a1a}
h1{font-size:1.4rem} h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:4px}
.badge{display:inline-block;font-size:.75rem;padding:2px 8px;border-radius:100px;margin-right:6px}
.be{background:#eaf3de;color:#27500a}.bm{background:#faeeda;color:#633806}.bh{background:#fcebeb;color:#791f1f}
.tags{font-size:.85rem;color:#666;margin:8px 0}
details{margin:12px 0;border:1px solid #e5e5e5;border-radius:8px;padding:8px 12px}
summary{cursor:pointer;font-weight:600}
pre{background:#f5f5f4;padding:12px;border-radius:6px;overflow-x:auto;font-size:.8rem}
blockquote{border-left:3px solid #185fa5;margin:8px 0;padding:4px 12px;color:#444}
.toc a{display:block;padding:2px 0}
.card{margin:32px 0;padding-top:16px;border-top:2px solid #eee}
.note{background:#e6f1fb;padding:12px;border-radius:8px;font-size:.9rem}
"""
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>System Design v15 — GitHub static view</title>",
        f"<style>{css}</style></head><body>",
        "<h1>System Design Cheat Sheet v15</h1>",
        "<p class='note'>Static preview for GitHub — no JavaScript. "
        "<a href='../system_design_cheatsheet_v14.html'>Open interactive version</a> for search, tabs, and interview mode.</p>",
        framework_snippet,
        "<h2>Table of contents</h2><div class='toc'>",
    ]
    for diff_key, label in [("e", "Easy"), ("m", "Medium"), ("h", "Hard")]:
        parts.append(f"<h3>{label}</h3>")
        for s in systems:
            if s["diff"] == diff_key:
                parts.append(
                    f"<a href='#card-{s['idx']}'>{html.escape(s['title'])}</a>"
                )
    parts.append("</div>")

    for s in systems:
        badge = {"e": "be", "m": "bm", "h": "bh"}[s["diff"]]
        md = render_v15_markdown(s)
        # Convert markdown details to HTML already; wrap card
        parts.append(f"<div class='card' id='card-{s['idx']}'>")
        parts.append(
            f"<h2>{html.escape(s['title'])} "
            f"<span class='badge {badge}'>{DIFF_LABEL[s['diff']]}</span></h2>"
        )
        parts.append(f"<p>{html.escape(s['sub'])}</p>")
        parts.append(
            "<p class='tags'>"
            + " ".join(f"<code>{html.escape(t)}</code>" for t in s["tags"])
            + "</p>"
        )
        if s["na"]:
            parts.append(f"<blockquote>{html.escape(strip_html_tags(s['na']))}</blockquote>")
        # Re-use details sections from markdown (already HTML details tags)
        for section in md.split("<details")[1:]:
            parts.append("<details" + section.split("</details>")[0] + "</details>")
        parts.append("</div>")

    parts.append(
        "<footer><p>Eddy Hung 2026 · "
        "<a href='v15/index.md'>Markdown index</a> · "
        "<a href='README.md'>GitHub docs home</a></p></footer>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def extract_framework_md(html_text: str) -> str:
    """Condensed delivery framework for github index."""
    return """## Delivery framework

1. **Requirements** (3–5 min) — functional + NFRs, draw out-of-scope line
2. **Estimation** — QPS, storage, bandwidth to justify decisions
3. **API design** — REST / WebSocket / gRPC, name routes explicitly
4. **Data model** — entities, SQL vs NoSQL with justification
5. **High-level design** — client → LB → services → DB/cache
6. **Deep dives** — 2–3 hardest problems, tradeoffs, failure modes

**Key numbers:** Redis ~100K–1M ops/s · Single DB ~10K–50K QPS · Kafka ~1M+ msgs/s · Cross-region ~100–200ms

[Full interactive cheatsheet](../system_design_cheatsheet_v14.html) includes DB chooser, cloud commands, and estimation worksheet.
"""


def split_v10_systems(html_text: str) -> list[str]:
    start = html_text.index("const SYSTEMS = [")
    end = html_text.index("\n];", start)
    section = html_text[start:end]
    starts = [m.start() for m in re.finditer(r"\{\s*\n\s*id:\s*\"", section)]
    blocks = []
    for i, pos in enumerate(starts):
        chunk_end = starts[i + 1] if i + 1 < len(starts) else len(section)
        block = section[pos:chunk_end].strip().rstrip(",")
        blocks.append(block)
    return blocks


def parse_v10_system(block: str, idx: int) -> dict:
    diff_m = re.search(r'diff:\s*["\']([emh])["\']', block)
    diff = diff_m.group(1) if diff_m else "m"
    title = extract_string_field(block, "title")
    id_m = re.search(r'id:\s*"([^"]+)"', block) or re.search(
        r"id:\s*'([^']+)'", block
    )
    sys_id = id_m.group(1) if id_m else slugify(title or f"system-{idx}")
    if not title:
        title = sys_id.replace("-", " ").title()
    sub = extract_string_field(block, "sub")
    vol = extract_string_field(block, "vol")
    tags_m = re.search(r"tags:\s*\[([^\]]+)\]", block)
    tag_list = []
    if tags_m:
        tag_list = re.findall(r'"([^"]+)"', tags_m.group(1)) or re.findall(
            r"'([^']+)'", tags_m.group(1)
        )
    problem = extract_string_field(block, "problem")
    kp_body = extract_array_field(block, "keypoints")
    keypoints = parse_q3(kp_body)
    tr_body = extract_array_field(block, "tradeoffs")
    tradeoffs = parse_q4(tr_body)
    closer = extract_string_field(block, "closer")
    script_body = extract_array_field(block, "script")
    script = parse_q6(script_body) if script_body else []
    scale = extract_string_field(block, "scale")
    flow = extract_string_field(block, "flow")
    whiteboard = extract_string_field(block, "whiteboard")

    v13_like = {
        "failures": parse_json_array(extract_array_field(block, "failures")),
        "est": {},
        "decisions": parse_json_array(extract_array_field(block, "decisions")),
        "followups": parse_json_array(extract_array_field(block, "followups")),
        "evolution": parse_json_array(extract_array_field(block, "evolution")),
    }
    est_m = re.search(r"estimation:\s*(\{.*?\})\s*,", block, re.S)
    if est_m:
        try:
            v13_like["est"] = json.loads(est_m.group(1))
        except json.JSONDecodeError:
            pass

    return {
        "idx": idx,
        "id": sys_id,
        "diff": diff,
        "title": title,
        "sub": sub,
        "vol": vol,
        "tags": tag_list,
        "slug": slugify(title),
        "problem": problem,
        "flow": flow,
        "keypoints": keypoints,
        "closer": closer,
        "tradeoffs": tradeoffs,
        "script": script,
        "scale": scale,
        "whiteboard": whiteboard,
        **v13_like,
    }


def render_v10_markdown(s: dict) -> str:
    diff = DIFF_LABEL[s["diff"]]
    lines = [
        f"# {s['title']}",
        "",
        f"**{diff}** · {s.get('vol', '')} · {s['sub']}",
        "",
        f"Tags: {', '.join(f'`{t}`' for t in s['tags'])}",
        "",
    ]
    if s.get("flow"):
        lines += [f"**Flow:** {s['flow']}", ""]
    lines += ["---", ""]
    if s["problem"]:
        lines.append(details("Problem", md_block(s["problem"]), open_=True))
    if s.get("failures"):
        body = ""
        for f in s["failures"]:
            body += f"**{f.get('w', '')}**\n\n{f.get('i', '')}\n\n_Fix:_ {f.get('x', '')}\n\n"
        lines.append(details("Failures", body))
    if s.get("est"):
        body = "| Field | Value |\n|-------|-------|\n"
        for k, label in [
            ("a", "Assumptions"),
            ("r", "Read QPS"),
            ("w", "Write QPS"),
            ("s", "Storage"),
            ("c", "Cache math"),
            ("v", "Verdict"),
        ]:
            if s["est"].get(k):
                body += f"| {label} | {md_escape(str(s['est'][k]))} |\n"
        lines.append(details("Estimation", body + "\n"))
    if s.get("decisions"):
        body = ""
        for d in s["decisions"]:
            body += f"**{d.get('q', '')}**\n\n→ {d.get('c', '')}\n\n{d.get('w', '')}\n\n"
        lines.append(details("Design decisions", body))
    if s.get("followups"):
        body = ""
        for fq in s["followups"]:
            body += f"**{fq.get('q', '')}**\n\n{fq.get('a', '')}\n\n"
        lines.append(details("Follow-up Q&A", body))
    if s.get("evolution"):
        body = ""
        for ev in s["evolution"]:
            body += f"**{ev.get('s', '')}** — {ev.get('d', '')}\n\n"
        lines.append(details("Evolution", body))
    if s.get("keypoints"):
        body = "\n".join(f"- **{b}** — {t}" for b, t in s["keypoints"])
        if s.get("closer"):
            body += f"\n\n> {s['closer']}"
        lines.append(details("Key points", body + "\n"))
    if s.get("tradeoffs"):
        body = ""
        for kind, label, text in s["tradeoffs"]:
            body += f"**{label}** — {text}\n\n"
        lines.append(details("Tradeoffs", body + "\n"))
    if s["scale"]:
        lines.append(details("Scale", md_block(s["scale"])))
    if s["script"]:
        body = "\n".join(f"{i+1}. {line}" for i, line in enumerate(s["script"]))
        lines.append(details("Script", body + "\n"))
    if s.get("whiteboard"):
        lines.append(
            details("Whiteboard", "```\n" + s["whiteboard"].strip() + "\n```\n")
        )
    lines += [
        "---",
        "",
        "[← Back to v10 cards index](index.md) · "
        f"[Interactive version](../../SystemDesign_Complete_v10.html#card-{s['id']})",
        "",
    ]
    return "\n".join(lines)


def write_index(path: Path, title: str, systems: list[dict], prefix: str, version: str):
    lines = [
        f"# {title}",
        "",
        f"GitHub-friendly view of **{version}**. "
        "Use the links below — GitHub renders Markdown natively. "
        f"For search, tabs, and interview mode, open the [interactive HTML]({prefix}).",
        "",
    ]
    if "v15" in version:
        lines.append(extract_framework_md(""))
        lines.append("---\n")
    for diff_key, label in [("e", "Easy"), ("m", "Medium"), ("h", "Hard")]:
        group = [s for s in systems if s["diff"] == diff_key]
        if not group:
            continue
        lines.append(f"## {label} ({len(group)})\n")
        for s in group:
            ddir = DIFF_DIR[s["diff"]]
            lines.append(f"- [{s['title']}]({ddir}/{s['slug']}.md)")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_v10_reference_index(html_text: str) -> str:
    chapters = re.findall(
        r'<div class="chapter" id="(ch-[^"]+)">.*?<div class="chapter-title">([^<]+)</div>',
        html_text,
        re.S,
    )
    lines = [
        "# ByteByteGo Reference — Chapter index",
        "",
        "The reference chapters are static HTML in the interactive file. "
        "GitHub cannot run the System Cards JavaScript, but the **ByteByteGo Reference** "
        "tab content is embedded HTML — open the file for full diagrams and cloud tables.",
        "",
        f"[Open interactive v10](../SystemDesign_Complete_v10.html)",
        "",
        "## Chapters",
        "",
    ]
    for cid, title in chapters:
        lines.append(f"- [{title.strip()}](../SystemDesign_Complete_v10.html#{cid})")
    lines += [
        "",
        "## Cloud appendix",
        "",
        "- [AWS services](../SystemDesign_Complete_v10.html#sec-aws)",
        "- [GCP services](../SystemDesign_Complete_v10.html#sec-gcp)",
        "- [Azure services](../SystemDesign_Complete_v10.html#sec-azure)",
        "",
    ]
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    v15_html = V15_HTML.read_text(encoding="utf-8")
    v15_blocks = split_v15_systems(v15_html)
    v15_systems = [parse_v15_system(b, i) for i, b in enumerate(v15_blocks)]
    print(f"Parsed {len(v15_systems)} v15 systems")

    v15_root = OUT / "v15"
    for s in v15_systems:
        ddir = v15_root / DIFF_DIR[s["diff"]]
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / f"{s['slug']}.md").write_text(
            render_v15_markdown(s), encoding="utf-8"
        )

    write_index(
        v15_root / "index.md",
        "Staff+ Interview Prep (v15)",
        v15_systems,
        "../system_design_cheatsheet_v14.html",
        "v15",
    )

    static_html = render_v15_static_html(
        v15_systems, f"<div class='note'>{extract_framework_md('')}</div>"
    )
    (OUT / "v15-static.html").write_text(static_html, encoding="utf-8")

    v10_html = V10_HTML.read_text(encoding="utf-8")
    v10_blocks = split_v10_systems(v10_html)
    v10_systems = [parse_v10_system(b, i) for i, b in enumerate(v10_blocks)]
    print(f"Parsed {len(v10_systems)} v10 card systems")

    v10_root = OUT / "v10-cards"
    for s in v10_systems:
        ddir = v10_root / DIFF_DIR[s["diff"]]
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / f"{s['slug']}.md").write_text(
            render_v10_markdown(s), encoding="utf-8"
        )

    write_index(
        v10_root / "index.md",
        "System Cards (v10)",
        v10_systems,
        "../SystemDesign_Complete_v10.html",
        "v10 cards",
    )

    (OUT / "v10-reference-index.md").write_text(
        build_v10_reference_index(v10_html), encoding="utf-8"
    )

    github_readme = f"""# GitHub-viewable cheat sheets

The interactive HTML files in the parent folder use JavaScript to render system cards.
**GitHub's file preview strips `<script>`**, so those pages look empty here.

This folder contains static versions you can read directly on GitHub:

| View | Best for |
|------|----------|
| [v15 index](v15/index.md) | **40 systems** — Staff+ interview prep (Markdown) |
| [v15 static HTML](v15-static.html) | Same content, single HTML file with expandable sections |
| [v10 cards index](v10-cards/index.md) | **26 systems** — ByteByteGo card summaries |
| [v10 reference index](v10-reference-index.md) | ByteByteGo chapter + cloud appendix links |

## Interactive versions (full features)

- [system_design_cheatsheet_v14.html](../system_design_cheatsheet_v14.html) — v15, search, interview mode, keyboard shortcuts
- [SystemDesign_Complete_v10.html](../SystemDesign_Complete_v10.html) — ByteByteGo reference + cloud CLI tables

## Regenerate

```bash
python3 scripts/build_github_view.py
```
"""
    (OUT / "README.md").write_text(github_readme, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
