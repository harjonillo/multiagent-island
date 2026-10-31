# Island explorer handoff

Everything Claude Code needs to build the island explorer in `multiagent-island`.

Last updated Oct 31, 23:45. New since the first version: the Experiments page ("pick a door"), `door_sprites.py`,
and the "← experiments" link in the top bar of a run's pages. Everything else is unchanged.

| file | what it is |
|---|---|
| `SPEC.md` | the three tabs (Home, Days, Social), their behaviour, and where every number comes from in a run directory |
| `tokens.css` | the "Island Pixel" design tokens: colours, fonts, borders, shadows |
| `export_map.py` | reference code for rendering the pixel island map and the overlay positions |
| `door_sprites.py` | the pixel-art door, its four opening frames and the handle, as SVG; run it for a preview page |
| `mockups/doors.dc.html`, `home.dc.html`, `days.dc.html`, `social.dc.html` | the mockup sources from the design canvas: inline styles and sample data to match visually (not standalone pages) |

Design canvas with the interactive mockups: https://claude.ai/artifact/M9KbZmd6cyo24Xsd4crVgq

## Suggested prompt

Put this folder in the repo (for example `docs/explorer/`), then in Claude Code:

> Read docs/explorer/SPEC.md, tokens.css, export_map.py, door_sprites.py and the mockups. Add an `llmabm explore <run_dir>`
> command that writes a self-contained `explorer/index.html` (plus `island.png`) for a run, as the spec
> describes. Reuse the helpers in src/llmabm/island/analysis.py so the numbers match summary.txt. Start
> with the data builder and a test on a 2-day mock run, then Home, then Days, then Social, then the
> Experiments page over the runs folder. Show me the page for a mock run after each one.

If the explorer is already partly built from the first version, a shorter prompt:

> docs/explorer has been updated (see "Last updated" in its README). Add the Experiments page and the
> "← experiments" link as SPEC.md now describes, using door_sprites.py for the door.

Things the spec leaves to the implementer, worth deciding early:
- whether `llmabm analyze` should build the explorer automatically
- how long text (real thoughts and nightly pages) is truncated or expanded
- whether villagers get a fixed colour each (the mockups avoid it: names are labelled, colour is used for activity type)
- whether each experiment's door gets its own colour (the mockup uses mint for every door)
