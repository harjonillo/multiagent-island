# Island explorer handoff

Everything Claude Code needs to build the island explorer in `multiagent-island`.

| file | what it is |
|---|---|
| `SPEC.md` | the three tabs (Home, Days, Social), their behaviour, and where every number comes from in a run directory |
| `tokens.css` | the "Island Pixel" design tokens: colours, fonts, borders, shadows |
| `export_map.py` | reference code for rendering the pixel island map and the overlay positions |
| `mockups/home.dc.html`, `days.dc.html`, `social.dc.html` | the mockup sources from the design canvas: inline styles and sample data to match visually (not standalone pages) |

Design canvas with the interactive mockups: https://claude.ai/artifact/M9KbZmd6cyo24Xsd4crVgq

## Suggested prompt

Put this folder in the repo (for example `docs/explorer/`), then in Claude Code:

> Read docs/explorer/SPEC.md, tokens.css, export_map.py and the mockups. Add an `llmabm explore <run_dir>`
> command that writes a self-contained `explorer/index.html` (plus `island.png`) for a run, as the spec
> describes. Reuse the helpers in src/llmabm/island/analysis.py so the numbers match summary.txt. Start
> with the data builder and a test on a 2-day mock run, then Home, then Days, then Social. Show me the
> page for a mock run after each tab.

Things the spec leaves to the implementer, worth deciding early:
- whether `llmabm analyze` should build the explorer automatically
- how long text (real thoughts and nightly pages) is truncated or expanded
- whether villagers get a fixed colour each (the mockups avoid it: names are labelled, colour is used for activity type)
