#!/usr/bin/env python3
"""Extract const C=[...] from HTML into systems-v15.js for easier editing.

Usage: python3 scripts/extract_systems.py
Keeps HTML working offline — both files must stay in cheatSheet/.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "cheatSheet"
HTML = ROOT / "system_design_cheatsheet_v14.html"
JS = ROOT / "systems-v15.js"


def main():
    text = HTML.read_text(encoding="utf-8")
    start = text.index("const C=[")
    end = text.index("];", start) + 2
    data = text[start:end]
    JS.write_text(data + "\n", encoding="utf-8")
    print(f"Wrote {JS.name} ({len(data)} bytes)")
    print("To wire up: replace inline const C with <script src=\"systems-v15.js\"></script>")


if __name__ == "__main__":
    main()
