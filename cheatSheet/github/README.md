# GitHub-viewable cheat sheets

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
