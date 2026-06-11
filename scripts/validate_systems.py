#!/usr/bin/env python3
"""Validate system card completeness in the v15 cheatsheet."""

import re
import sys
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "cheatSheet" / "system_design_cheatsheet_v14.html"
REQUIRED_V13 = ("f", "est", "d", "fq", "ev")
MIN_FQ = 8
MIN_SCRIPT_LINES = 4


def main() -> int:
    text = HTML.read_text(encoding="utf-8")
    start = text.index("const C=[")
    end = text.index("function buildDiag", start)
    body = text[start:end]
    titles = re.findall(r"title:'([^']+)'", body)
    # Split on system boundaries — handles both `\n{d:` and `,{d:` prefixes
    blocks = re.split(r"(?:\n|,)\{d:", body)[1:]
    errors = []

    if len(blocks) != len(titles):
        errors.append(f"Block count {len(blocks)} != title count {len(titles)}")

    for i, blk in enumerate(blocks):
        title_m = re.search(r"title:'([^']+)'", blk)
        title = title_m.group(1) if title_m else f"system#{i}"
        diff_m = re.match(r"'?([emh])", blk.lstrip())
        diff = diff_m.group(1) if diff_m else "?"

        for field in ("q1", "q3", "q5", "q6", "na", "arch"):
            if f"{field}:" not in blk and f"{field} {{" not in blk:
                if field == "arch" and "arch:{" not in blk:
                    errors.append(f"{title}: missing {field}")

        if "v13:{" not in blk:
            errors.append(f"{title}: missing v13 block")
        else:
            for key in REQUIRED_V13:
                if f"{key}:" not in blk and f'"{key}":' not in blk:
                    errors.append(f"{title}: v13 missing {key}")
            fq_m = re.search(r"fq:\[(.*?)\],\s*ev:", blk, re.S)
            if fq_m:
                n = fq_m.group(1).count('"q":')
                if n < MIN_FQ:
                    errors.append(f"{title}: only {n} follow-ups (need {MIN_FQ})")

        q6_m = re.search(r"q6:\[(.*?)\](?:,arch:|,scale:)", blk, re.S)
        if q6_m:
            lines = q6_m.group(1).count("'") // 2  # rough quote pairs
            if lines < MIN_SCRIPT_LINES:
                errors.append(f"{title}: script has few lines")

        if diff not in ("e", "m", "h"):
            errors.append(f"{title}: invalid difficulty {diff}")

    if len(titles) != len(set(titles)):
        errors.append("Duplicate titles found")

    print(f"Validated {len(titles)} systems")
    if errors:
        print(f"FAILED — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK — all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
