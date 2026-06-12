#!/usr/bin/env python3
"""Build GitHub-compatible interactive HTML with pre-rendered cards.

GitHub's file preview strips <script> — empty grids stay empty.
This injects all cards into the DOM so content is visible without JS,
and produces a version suitable for GitHub Pages (JS still enhances UX).
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

# Reuse parsers from build_github_view
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_github_view import (  # noqa: E402
    DIFF_LABEL,
    parse_v15_system,
    split_v15_systems,
    strip_html_tags,
)
from staff_ladder import LADDER_CSS, format_q5_html  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "cheatSheet" / "system_design_cheatsheet_v14.html"
OUT = ROOT / "cheatSheet" / "system_design_cheatsheet_v15_github.html"
V10_SRC = ROOT / "cheatSheet" / "SystemDesign_Complete_v10.html"
V10_OUT = ROOT / "cheatSheet" / "SystemDesign_Complete_v10_github.html"

NODE_COLORS = {
    "info": ("var(--bdr-info)", "var(--txt-info)"),
    "succ": ("var(--bdr-succ)", "var(--txt-succ)"),
    "dang": ("var(--bdr-dang)", "var(--txt-dang)"),
    "warn": ("var(--bdr-warn)", "var(--txt-warn)"),
    "sec": ("var(--bdr-sec)", "var(--txt-sec)"),
}

TAB_SECTIONS = [
    ("p", "Problem"),
    ("f", "Failures"),
    ("e", "Estimation"),
    ("x", "Design decisions"),
    ("q", "Follow-up Q&A"),
    ("v", "Evolution"),
    ("w", "Why it's hard to scale"),
    ("k", "Key points"),
    ("t", "Tradeoffs"),
    ("d", "Deep dives"),
    ("s", "Interview script"),
    ("a", "Whiteboard"),
]


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def paras(text: str) -> str:
    if not text:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    return "".join(f"<p>{esc(p.strip())}</p>" for p in text.split("\n\n") if p.strip())


def render_failures(items: list) -> str:
    if not items:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    return "".join(
        f'<div class="fail-item"><div class="fail-what">⚠ {esc(f.get("w",""))}</div>'
        f'<div class="fail-impact">{esc(f.get("i",""))}</div>'
        f'<div class="fail-fix"><b>Fix:</b> {esc(f.get("x",""))}</div></div>'
        for f in items
    )


def render_est(est: dict) -> str:
    if not est:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    rows = [
        ("Assumptions", est.get("a")),
        ("Read QPS", est.get("r")),
        ("Write QPS", est.get("w")),
        ("Storage", est.get("s")),
        ("Cache math", est.get("c")),
    ]
    h = '<div class="v13-hdr">Back-of-envelope</div><div class="est-grid">'
    for lbl, val in rows:
        if val:
            h += f'<div class="est-row"><div class="est-lbl">{lbl}</div><div class="est-val">{esc(val)}</div></div>'
    h += f'</div><div class="est-verdict"><b>Verdict:</b> {esc(est.get("v",""))}</div>'
    return h


def render_decisions(items: list) -> str:
    if not items:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    return "".join(
        f'<div class="dec-item"><div class="dec-q">{esc(d.get("q",""))}</div>'
        f'<div class="dec-chose">→ {esc(d.get("c",""))}</div>'
        f'<div class="dec-why">{esc(d.get("w",""))}</div>'
        f'<div class="dec-revisit"><b>Revisit when:</b> {esc(d.get("r",""))}</div></div>'
        for d in items
    )


def render_followups(items: list) -> str:
    if not items:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    return "".join(
        f'<div class="fq-item"><div class="fq-q">{esc(fq.get("q",""))}</div>'
        f'<div class="fq-a">{esc(fq.get("a",""))}</div></div>'
        for fq in items
    )


def render_evolution(items: list) -> str:
    if not items:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    h = '<div class="v13-hdr">System evolution — v1 → v2 → v3</div><div class="evo-track">'
    h += "".join(
        f'<div class="evo-item"><div class="evo-dot"></div>'
        f'<div class="evo-stage">{esc(ev.get("s",""))}</div>'
        f'<div class="evo-desc">{esc(ev.get("d",""))}</div></div>'
        for ev in items
    )
    return h + "</div>"


def render_scale(text: str) -> str:
    if not text:
        return '<p style="color:var(--txt-ter)">Scale analysis not available.</p>'
    h = '<div class="scale-lbl">What makes this hard to scale?</div><div class="scale-body">'
    h += paras(text)
    h += '<div class="scale-bridge"><b>Deep dives tab</b> has the solutions to each of these scaling problems.</div></div>'
    return h


def render_keypoints(items: list, closer: str) -> str:
    h = '<ul class="ll">'
    h += "".join(f'<li><b>{esc(b)}.</b> {esc(t)}</li>' for b, t in items)
    h += "</ul>"
    if closer:
        h += f'<div class="closer">{esc(closer)}</div>'
    return h


def render_tradeoffs(items: list, closer: str) -> str:
    h = '<div class="tlist">'
    for kind, label, text in items:
        if kind == "ord":
            h += f'<div class="ti"><span class="t-ord">{esc(label)}</span><span class="t-txt">{esc(text)}</span></div>'
        else:
            h += f'<div class="ti"><span class="t-vs">{esc(label)}</span><span class="t-txt">{esc(text)}</span></div>'
    h += "</div>"
    if closer:
        h += f'<div class="closer">{esc(closer)}</div>'
    return h


def render_script(lines: list) -> str:
    if not lines:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    out = []
    for line in lines:
        lo = line.lower()
        if lo.startswith(("here's", "use a", "clean", "top-down", "steady", "one clear", "two-path", "three-problem")):
            out.append(f"<p>{esc(line)}</p>")
        else:
            out.append(f'<div class="sq">{esc(line)}</div>')
    return "".join(out)


def render_flow_cell(cell: dict | None) -> str:
    if cell is None:
        return '<div class="demp"></div>'
    if "a" in cell:
        lbl = (
            f'<span class="dal">{esc(cell["l"])}</span>'
            if cell.get("l")
            else ""
        )
        return (
            f'<div class="darr"><span class="das">{esc(cell["a"])}</span>{lbl}</div>'
        )
    bv, tv = NODE_COLORS.get(cell.get("c", "sec"), NODE_COLORS["sec"])
    sub = (cell.get("s") or "").replace("\n", "<br>")
    sub_html = f'<div class="ds">{sub}</div>' if sub else ""
    return (
        f'<div class="dnode" style="border-color:{bv}">'
        f'<div class="dt" style="color:{tv}">{esc(cell.get("t", ""))}</div>'
        f"{sub_html}</div>"
    )


def render_diag(rows: list, dn: str) -> str:
    if not rows:
        return ""
    h = ""
    for row in rows:
        h += '<div class="drow">'
        for cell in row:
            h += render_flow_cell(cell)
        h += "</div>"
    note = strip_html_tags(dn) if dn else ""
    if note:
        h += f'<div class="dnote">{esc(note)}</div>'
    return f'<div class="diag">{h}</div>'


def render_scale_pills(pills: list) -> str:
    if not pills:
        return ""
    items = "".join(
        f'<span class="pat-pill {esc(p.get("cls", ""))}">{esc(p.get("label", ""))}</span>'
        for p in pills
    )
    return f'<div class="pat-pills">{items}</div>'


def render_arch_block(arch_d: str, arch_t: str) -> str:
    if not arch_d and not arch_t:
        return ""
    h = '<div class="card-arch"><div class="nlbl">Architecture diagram</div>'
    if arch_d:
        h += f'<div class="ascii-wrap"><pre>{esc(arch_d)}</pre></div>'
    if arch_t:
        for p in arch_t.split("\n\n"):
            if p.strip():
                h += (
                    f'<div class="ascii-desc"><p>{esc(p.replace(chr(10), " ").strip())}</p></div>'
                )
    h += "</div>"
    return h


def render_arch(arch_d: str, arch_t: str) -> str:
    if not arch_d:
        return '<p style="color:var(--txt-ter);padding:4px 0">Architecture diagram not available.</p>'
    h = f'<div class="ascii-wrap"><pre>{esc(arch_d)}</pre></div>'
    if arch_t:
        for p in arch_t.split("\n\n"):
            if p.strip():
                h += f'<div class="ascii-desc"><p>{esc(p.replace(chr(10), " ").strip())}</p></div>'
    return h


def render_deep(text: str) -> str:
    if not text:
        return '<p style="color:var(--txt-ter)">Not available.</p>'
    formatted = format_q5_html(text)
    if formatted:
        return formatted
    parts = []
    for p in re.split(r"\n\n|\\n\\n", text):
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(Deep dive \d+:\s*)(.+?)(?:\n|$)", p)
        if m:
            parts.append(
                f'<div class="dd-title"><span class="dd-num">{esc(m.group(1).strip())}</span> {esc(m.group(2).strip())}</div>'
            )
            rest = p[m.end() :].strip()
            if rest:
                parts.append(f"<p>{esc(rest)}</p>")
        else:
            parts.append(f"<p>{esc(p)}</p>")
    return "".join(parts)


def panel_content(s: dict, key: str) -> str:
    if key == "p":
        return paras(s["q1"])
    if key == "f":
        return render_failures(s["failures"])
    if key == "e":
        return render_est(s["est"])
    if key == "x":
        return render_decisions(s["decisions"])
    if key == "q":
        return render_followups(s["followups"])
    if key == "v":
        return render_evolution(s["evolution"])
    if key == "w":
        return render_scale(s["scale"])
    if key == "k":
        return render_keypoints(s["q3"], s["c3"])
    if key == "t":
        return render_tradeoffs(s["q4"], s["c4"])
    if key == "d":
        return render_deep(s["q5"])
    if key == "s":
        return render_script(s["q6"])
    if key == "a":
        return render_arch(s["arch_d"], s["arch_t"])
    return ""


def render_card_html(s: dict) -> str:
    diff = s["diff"]
    badge = {"e": "be", "m": "bm", "h": "bh"}[diff]
    dtxt = DIFF_LABEL[diff]
    idx = s["idx"]
    cid = f"c{idx}"
    slug = s["slug"]
    title_l = s["title"].lower()

    see = ""
    if s.get("see_also"):
        see = f'<div class="see-also">See also: {s["see_also"]}</div>'

    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in s["tags"])
    pills = render_scale_pills(s.get("scale_pills") or [])
    flow = render_diag(s.get("diag") or [], s.get("dn", ""))
    arch_block = render_arch_block(s.get("arch_d", ""), s.get("arch_t", ""))

    # GitHub-safe fallback: <details> sections (work without JS)
    gh_sections = ""
    for i, (key, label) in enumerate(TAB_SECTIONS):
        body = panel_content(s, key)
        open_attr = " open" if i == 0 else ""
        gh_sections += (
            f'<details class="gh-panel"{open_attr}>'
            f"<summary>{esc(label)}</summary>"
            f'<div class="gh-panel-body">{body}</div></details>'
        )

    # Interactive tabs (work on GitHub Pages / local)
    tabs = ""
    panels = ""
    for i, (key, label) in enumerate(TAB_SECTIONS):
        on = " on" if i == 0 else ""
        tabs += (
            f'<div class="tab{on} js-tab" data-tab="{key}" '
            f'onclick="sw(this,\'{cid}\',\'{key}\',{idx})" role="tab">{esc(label)}</div>'
        )
        panels += (
            f'<div id="{cid}-{key}" class="panel js-panel{on}" role="tabpanel">'
            f"{panel_content(s, key)}</div>"
        )

    na = s["na"]  # contains <b> tags from source
    dn = esc(strip_html_tags(s.get("dn", "")))

    return f"""<div class="card" id="card-{idx}" data-idx="{idx}" data-slug="{slug}" data-diff="{diff}" data-title="{esc(title_l)}">
<div class="card-nav js-only">
  <div class="card-nav-btns">
    <button class="cn-btn" onclick="navCard({idx},-1)">← Prev</button>
    <button class="cn-btn" onclick="navCard({idx},1)">Next →</button>
  </div>
  <label class="studied-lbl"><input type="checkbox" data-studied="{idx}" onchange="toggleStudied({idx},this.checked)"> Studied</label>
</div>
<div class="chdr"><div><div class="ctitle">{esc(s["title"])}</div><div class="csub">{esc(s["sub"])}</div></div><span class="badge {badge}">{dtxt}</span></div>
{see}{pills}{flow}{arch_block}
<div class="narr"><div class="nlbl">Data flow</div><div class="ntxt">{na}</div></div>
<div class="trow">{tags}</div>
<div class="gh-fallback">{gh_sections}</div>
<div class="js-tabs-wrap js-only">
<div class="tabs" role="tablist">{tabs}</div>
{panels}
</div>
</div>"""


def patch_v15_html(text: str, systems: list[dict]) -> str:
    by_diff = {"e": [], "m": [], "h": []}
    for s in systems:
        by_diff[s["diff"]].append(render_card_html(s))

    for diff, gid in [("e", "ge"), ("m", "gm"), ("h", "gh")]:
        cards = "\n".join(by_diff[diff])
        text = re.sub(
            rf'<div class="grid" id="{gid}"></div>',
            f'<div class="grid" id="{gid}">\n{cards}\n</div>',
            text,
            count=1,
        )

    # GitHub preview CSS: show details fallback, hide JS tabs until script runs
    gh_css = """
/* GitHub / no-JS: details panels visible */
.gh-fallback details.gh-panel{margin:6px 14px;border:1px solid var(--bdr-ter);border-radius:var(--r);padding:8px 10px}
.gh-fallback summary{cursor:pointer;font-weight:500;font-size:12px}
.gh-fallback .gh-panel-body{padding-top:8px;font-size:12.5px}
html.js-enabled .gh-fallback{display:none}
html.js-enabled .js-only{display:revert}
html:not(.js-enabled) .js-only{display:none!important}
.noscript-banner{background:var(--bg-warn);border:1px solid var(--bdr-warn);padding:10px 14px;margin:0 16px 12px;border-radius:var(--r);font-size:12px;line-height:1.5}
.card-arch{padding:12px 14px;border-bottom:.5px solid var(--bdr-ter);background:var(--bg-sec)}
.card-arch .ascii-wrap{margin-top:6px}
.card-arch .ascii-desc{margin-top:8px;font-size:12px;color:var(--txt-sec);line-height:1.65}
""" + LADDER_CSS
    text = text.replace("</style>", gh_css + "\n</style>", 1)

    noscript = """
<noscript>
<div class="noscript-banner">
  <b>Viewing on GitHub?</b> JavaScript is disabled in GitHub's file preview — expand the sections below each card.
  Expand the <b>sections below each card</b> to read all tabs.
  For full interactivity (search, tabs, interview mode), clone the repo and open locally, or enable
  <a href="https://docs.github.com/en/pages">GitHub Pages</a> (Settings → Pages → source: GitHub Actions).
  Markdown: <a href="github/v15/index.md">github/v15/</a>
</div>
</noscript>
"""
    text = text.replace('<div class="main">', noscript + '<div class="main">', 1)

    # render() skips if cards exist; mark JS enabled
    text = text.replace(
        "function render(){",
        "function render(){\n  document.documentElement.classList.add('js-enabled');\n  if(document.querySelector('.card'))return;",
        1,
    )

    # Shrink C array — keep empty stub for scripts that reference it
    text = re.sub(
        r"const C=\[[\s\S]*?\];",
        "const C=[];/* cards pre-rendered in HTML for GitHub compatibility */",
        text,
        count=1,
    )

    # Update title/footer for github edition
    text = text.replace(
        "System Design Cheat Sheet v15 — Eddy Hung 2026",
        "System Design Cheat Sheet v15 (GitHub) — Eddy Hung 2026",
    )
    return text


def patch_v10_cards(html_text: str) -> str:
    """Inject v10 system cards from SYSTEMS array using id-marker split."""
    from build_github_view import parse_v10_system, render_v10_markdown, split_v10_systems

    blocks = split_v10_systems(html_text)
    systems = [parse_v10_system(b, i) for i, b in enumerate(blocks)]

    def card_md_html(s: dict) -> str:
        md = render_v10_markdown(s)
        # Convert details blocks to HTML (already HTML details in md)
        body = md.split("---")[1] if "---" in md else md
        return (
            f'<div class="scard" id="card-{s["id"]}" data-diff="{s["diff"]}" '
            f'data-title="{esc((s["title"]+" "+s["sub"]).lower())}">'
            f'<div class="scard-hdr"><div class="scard-title">{esc(s["title"])}</div>'
            f'<div class="scard-sub">{esc(s.get("vol",""))} · {esc(s["sub"])}</div></div>'
            f'<div class="gh-fallback">{body}</div></div>'
        )

    by_diff = {"e": [], "m": [], "h": []}
    for s in systems:
        by_diff[s["diff"]].append(card_md_html(s))

    for diff, gid in [("e", "cards-ge"), ("m", "cards-gm"), ("h", "cards-gh")]:
        cards = "\n".join(by_diff[diff])
        html_text = re.sub(
            rf'<div class="grid" id="{gid}"></div>',
            f'<div class="grid" id="{gid}">\n{cards}\n</div>',
            html_text,
            count=1,
        )

    html_text = re.sub(
        r"const SYSTEMS = \[[\s\S]*?\n\];",
        "const SYSTEMS=[];/* pre-rendered */",
        html_text,
        count=1,
    )
    html_text = html_text.replace(
        "function renderCards(){",
        "function renderCards(){\n  if(document.querySelector('.scard'))return;",
        1,
    )
    return html_text


def main():
    v15 = SRC.read_text(encoding="utf-8")
    blocks = split_v15_systems(v15)
    systems = [parse_v15_system(b, i) for i, b in enumerate(blocks)]
    print(f"v15: prerendering {len(systems)} cards")
    out = patch_v15_html(v15, systems)
    OUT.write_text(out, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")

    if V10_SRC.exists():
        v10 = V10_SRC.read_text(encoding="utf-8")
        v10_out = patch_v10_cards(v10)
        V10_OUT.write_text(v10_out, encoding="utf-8")
        print(f"Wrote {V10_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
