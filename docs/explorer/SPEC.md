# Island explorer: spec

A read-only browser view of island runs, built from the files `llmabm run` already writes. An
**Experiments** page ("pick a door") lists every run as a pixel-art door; opening a door leads to that
run's three tabs: **Home** (the whole run), **Days** (one or more days, per villager) and **Social** (who
deals with whom). Visual style: "Island Pixel", see `tokens.css`. Interactive mockups of all four pages are
on the design canvas, and their full markup and logic are in `mockups/` (see "The mockups" below).

Updated Oct 31, 23:45: added the Experiments page, the door sprites (`door_sprites.py`) and the link back
to the experiments page in the top bar.

Updated Nov 9: each experiment's door is now Hannah's hand-drawn door (`door_art/`), with no sign on it:
the number is on the card only. The empty "next experiment" slot keeps the code-drawn door from
`door_sprites.py`.

## Recommended shape

- A new command, `llmabm explore <run_dir>`, writes `<run_dir>/explorer/index.html`: one self-contained
  page (HTML, CSS, JS inline, no server, no build step) with the run's data embedded as JSON, plus
  `explorer/island.png`. Opening the file in a browser is the whole workflow.
- Build the data in Python from `analysis.load_run(run_dir)` and the existing island analysis helpers,
  so the numbers match `summary.txt`. Do not recompute anything differently from `island/analysis.py`.
- Optionally call it at the end of `analyze_island` so every run gets an explorer.
- Pointed at the runs folder instead (`llmabm explore runs/`), it also writes `runs/explorer/index.html`,
  the Experiments page, with one door per run directory that has a `manifest.json`, each linking to that
  run's `explorer/index.html` by relative path.
- No LLM calls. Rebuilding is like `llmabm analyze`: from `events.jsonl` only.
- Keep the page working offline except for the two Google Fonts (fall back to `ui-monospace`).
- Add a test that builds the explorer for a short mock run and checks the embedded JSON has every
  villager, every day and no private field leaking where it shouldn't (see Privacy).

## Type scale

In the repo, every font size is a step on one modular scale: body text 12px, ratio 1.25 (`--text-base` and
`--ratio` in `src/llmabm/island/site_tokens.css`; change those two to rescale everything). Steps:
`--step--1` 9.6px (labels, meta, axis ticks), `--step-0` 12px (body), `--step-1` 15px, `--step-2`
18.75px, `--step-3` 23.44px, `--step-4` 29.3px. The pixel sizes below are the handoff's, designed around
14px body text; each maps to the step nearest its ratio to 14px: 11-12 to -1, 13-15 to 0, 18 to 1, 20-24
to 2, 30 to 3, 34 to 4.

## Shared chrome

- **Top bar:** inset to the page margins (28px), 20px above it, 2px `--tabbar-rule` lines above and below,
  10px vertical padding. Left, on a run's pages: `multiagent-island / experiments / exp NN` in
  Silkscreen 18px, the logo in ink and linking to the Experiments page, the rest muted plain text (in the
  repo; the handoff had a boxed "experiments" link with an arrow). Then the run line in body font 13px muted:
  `<scenario name> · seed <n> · <backend>`. On the Experiments page the left side is just the logo, and
  the right side says `N experiments`. Right: tabs Home, Days, Social. Active tab:
  2px `--tabbar-rule` border, `--tab-active-fill` background, ink text, weight 600. Inactive: 2px
  transparent border, no fill.
- **Page title:** Silkscreen 30px, 20px below the bar. Muted subtitle line under it.
- **Cards:** `--panel`, 2px `--border`, square, `--card-shadow`. Section labels in Silkscreen 12px uppercase.
- **Selected filters** (chips, villager buttons, names on the map, network nodes): keep their normal
  fill (cream for chips, ink for villager buttons and nodes) and show a 2px `--selected-border` border.
  Unselected controls keep a 2px `--border` border so nothing shifts when selected.
- **Private content** (plan thoughts, notes, conversation thoughts, nightly pages): `--private-ink`
  text with a small padlock icon; nightly pages on `--private-fill`. Never colour alone: always the icon.
- **Failures:** the word "failed" or an x icon in `--fail`. Never colour alone.
- Every button and chip is a real `<button>` with `aria-pressed` for toggles; min height 44px.
- Layout: two-column flex-wrap rows that stack below ~1000px; wide tables scroll inside their card.

## The map

Generated per run from the seed, not a stored image. `export_map.py` shows the approach: build
`GridSpace(manifest["world"], manifest["seed"])`, paint one pixel per tile (terrain, tier, buildings,
homesites, paths, trees, rocks from the latest `snapshot.objects`), and scale it up 8x with nearest
neighbour. The page draws it with `image-rendering: pixelated` and overlays HTML labels positioned in
percent of the grid: `left = (x + 0.5) / W`, `top = (y + 0.5) / H`.

- Building labels sit just above each building (centre x, min y of its cells).
- Villager names sit on their homesite centre (`snapshot.homes` → `grid.sites[site]["cells"]`).
- Clicking a name selects that villager (Days) or opens Days for them (Home).
- Pointing at the map (or tapping it, which pins the box until the next tap) shows what is there at the end of
  that day: a tree ("stump, felled by Ari on day 2"; "sapling, planted by Cal on day 4"), a rock (its number,
  whether it was that day's money rock, hits left, who mined it), furniture and fences (who put them there),
  flowers (who planted them, or growing wild, and whether they bloom), paths (style, who laid them), homes
  (whose, which house) and buildings, else the ground. The text is built in `site_data.py` (`objects.info` per
  day). Who planted or felled a tree is read from the log: the villager stands next to the tree, so each
  planting or felling is matched to the sapling or stump one tile away in that day's snapshot.

## Page 0: Experiments, "pick a door"

The landing page. Title `pick a door` (Silkscreen 34px, centred), subtitle `Each door is one experiment.
Open it to see everything that happened on that island.` Doors sit in a centred, wrapping row, 48px apart,
each in a 300px column with its card below.

- **One door per run directory**, oldest first, numbered `EXP 00`, `EXP 01`, ... in that order (in the
  repo: from 00, the first test run, and mock-backend runs are left out).
- **The door** is Hannah's hand-drawn door from `door_art/` (64 x 64 PNGs: the frame, an arched mint
  door with its lever, a stone step and two potted plants), drawn at 5x so every pixel is 5 x 5 px, with
  `image-rendering: pixelated`. The transparent margins around the art are cropped (it spans sprite pixels
  x 9..58, y 8..57), so the door is 250 x 250 px, centred over its card, and the card sits 16px under the
  step. The page is self-contained, so the sprites are
  embedded once as data URIs in its CSS: `door_open_sheet.png` (frames 0 to 5 in a row; 0 is the closed
  door) and `door_hover.png`. Never scale them with smooth transforms or by a fraction.
- **No sign on the door.** The door carries no text; `EXP NN` and the scenario are on the card under it.
- **Hover or keyboard focus:** swap to `door_hover.png` (the lever turned 45 degrees down) and draw a 3px
  `--tabbar-rule` outline around the door leaf (sprite pixels x 20..43, y 11..48). No rotation animation:
  it is a sprite swap.
- **Click (or Enter):** play frames 1 to 5 of the sheet, 80 ms apart, starting at once: the lever turns,
  the door narrows toward its left hinge and darkens, and the dark room shows through. About 300 ms after
  the last frame, go to that run's Home. Swap frames by stepping the sheet's `background-position`; no CSS
  transitions or 3D transforms, so it stays pixel art. Coming back with the back button shows the door
  closed again. With `prefers-reduced-motion`, skip straight to navigation. The whole door is one link
  (`<a href>` with an `aria-label` such as `Open experiment 01: blank villagers, mock backend`), so it
  works without JS (hover and focus are CSS only).
- **The card under each door** (panel style, 13px): `Experiment NN` in Silkscreen 18px with a chip
  `day X of Y` (2px `--selected-border`), then three rows, label muted on the left:
  `agents` (backend and model, e.g. `groq · llama-3.1-8b-instant`, or `mock backend`),
  `scenario` (`<scenario name> · seed <n>`), `started` (date of the run's first event).
  Keep it to these: the card is for telling experiments apart at a glance.
- **Initial conditions** (added Nov 1, in the repo): below the three rows, a closed `<details>` with the
  summary `initial conditions` (Silkscreen 12px, uppercase, muted, a 5 x 5 pixel triangle pointing right,
  down when open). Opened, two groups of the same label/value rows: `Each of the N villagers` (bells,
  tools with their uses, recipes known) and `The island on day 0` (trees, wood, fruit, rocks, shells,
  fossils, wild flower patches), then a muted note that wood, rocks, shells and fossils refill each
  morning. Bells come from the scenario config, tools and recipes from the run's world in
  `manifest.json`, the island's counts from day 0's `day_start` event (`pools`).
- **An empty slot** after the last door keeps the earlier code-drawn door from `door_sprites.py` (SVG
  `<rect>`s with `shape-rendering="crispEdges"`: the frame with a dark doorway and a stone step, and the
  door panel with planks, two braces, a sign board and a handle backplate), faded (grayscale 60%, opacity
  0.55, `EMPTY_DOOR` colours), with its sign showing the next number (`EXP NN`, Silkscreen 15px) and
  `empty lot` (Silkscreen 9px). It is drawn with 6 px cells, so it is 144px wide (about as wide as the drawn
  door's frame) and stands on the same ground line, the step's bottom edge. Then a dashed card "Next
  experiment" with an example command for starting another run. Not a link.
- **Changelog** (added Nov 1, in the repo): a sidebar fixed in the page's right margin, 280px, panel
  style, headed `Changelog` (Silkscreen label) with a fold button (a pixel chevron). Entries newest first
  on a timeline (2px rule, a 10px square per entry): the real date, then `EXP NN` (linking to the run) and
  what happened, then the island day it applies from, then the details as short sentences with a bold
  lead. One entry per experiment's start (scenario, seed, villagers, model, prompt version) and one per
  `intervention` event (such as a prompt version switch on resume). Folded, it is a vertical tab reading
  `Changelog` with the entry count. The doors never move when it opens or folds: both side margins are
  fixed, wide enough for the open panel from 1320px up; narrower, they only fit the folded tab, and the
  open panel covers part of the doors (it starts folded there, and Escape folds it). Phones (below 600px)
  get 16px margins and the tab sits over the doors' edge. Where it has room, open or folded is kept in
  `localStorage`.

Data: `manifest.json` per run (scenario name, seed, backend and model, scenario days, days completed;
check the exact keys) and the `run_start` event or the directory's timestamp for the start date.

## Tab 1: Home, "everything everywhere all at once, so far..."

Subtitle: `<n> days, <first date> to <last date> · <n> villagers · <n> LLM calls, <n> unreadable replies`.

1. **Six stat tiles:** actions (and % failed), conversations (and "from N times someone noticed
   someone"), letters and parcels (and board posts), trades (from N offers), museum species (of total),
   bridge fund (of cost, and who paid).
2. **Map** with homes. Caption: path tiles by villager, saplings planted, trees cut down.
3. **Day by day:** small line charts, one per measure, x = day: failed actions, conversations,
   letters and parcels, houses on loan. One series each; title gives first → last value.
4. **Why actions failed:** horizontal bars, buckets below, count at the end. Header: `N of M`.
5. **Villagers table:** name (links to Days with that villager), home area and house/loan, assets
   sparkline over days with the latest value (assets = bells + items at Cranny prices, loans not
   subtracted; also print the net worth Gini from the summary), things spotted, share of energy spent
   talking, three most frequent actions with counts.
6. **Nightly pages, night by night:** villagers × nights grid of the first line of each page. A cell is
   tinted `--private-fill` when it repeats the night before; "chose not to write" in muted italics.
7. **Who took the commons:** per resource, HHI and the top three takers with shares; then one line on
   when rocks, shells and fossil spots typically ran out.

Failure buckets, matched on the action's `result` text (first match wins):
`not enough materials` → materials; `can't get to` or `from here with the tools` → can't reach;
`Blathers only` → museum only takes fish, bugs, fossils; `you have no <tool>` → has no tool;
`closed` → shop closed; `nothing more` → already empty; else other.

## Tab 2: Days

- **Day multi-select:** one checkbox chip per day (`Day N · <date>`), plus an "All days" link. At least
  one day stays selected. With one day the title is `Day N · <date> · <season>` and the subtitle gives
  actions, failed, conversations, letters and parcels, board posts. With several, the title is
  `Days a, b combined` and every section below groups its entries by day, and charts run across the
  selected days.
- **Resource tiles:** start → end of the selection for rock hits, wood, shells, fossil spots, museum,
  bridge fund.
- **Map** with homes (end of the last selected day). Clicking a name selects that villager.
- **Commons through the day:** small line charts of rock hits left, wood on trees, shells and fruit,
  x = hour 07 to 23 (from `hour_state.pools`; note each `hour_state` is the state after that hour).
- **Villager tabs** (10 buttons) and the selected villager's day card:
  - stats: actions, failed, discoveries, talks and mail
  - **Gathered and sold** (site data version 8): per kind of thing (fish, bugs, shells, fruit, clams,
    wood, stone and ores, fossils, other), how many they gathered and what that is worth at Cranny
    prices, and how many they sold and for how many bells, summed over the days picked; then their bells
    in and out by action (from selling, from rocks, at the shops, to the bridge, on the loan or house).
    From `action.delta`; a sale's bells are split across what was sold by Cranny price. Gifts, trades
    and parcels aren't actions, so they aren't counted here
  - **Plans:** each `plan` event's hour, private `thoughts`, then muted `N actions scheduled, N dropped`
    and the `note` if any
  - **Actions and attempts:** hour, location, `result`, ok/failed
  - **Discoveries:** `sighting` events for the villager: hour and what was seen
  - **Conversations and mail:** encounters they were in (each line with the speaker's words; the
    selected villager's own private thought under their lines only), letters, parcels, offers, gifts
    and board posts they sent or received
  - **Nightly page:** the page written that night (`reflection.self_doc`), or "chose not to write"
- **All conversations:** cards for every encounter of the selection (who, hour, place, lines), plus one
  card per plaza round with the board posts, letters, parcels and offers.

## Tab 3: Social, "Who deals with whom"

- **Filters:**
  - *Kind of contact* (multi-select chips with counts in the current range): chance meetings
    (`encounter`), visits (`visit`, i.e. knocking on a door, answered or not), letters and parcels
    (`message`, `parcel`), gifts in person (`gift` with `how == "in person"`; mailed gifts are the
    parcels), offers and trades (`offer`, `trade`). Each chip's checkbox carries its legend colour.
  - *Time range:* "From day" and "to day" selects.
- **Network:** ten nodes on a circle, node size by total interactions in the filter, label shows the
  count. Undirected edges between pairs with at least one interaction; thickness scales linearly from
  2px (1 interaction) to 10px (max). Edges are clickable `<button>`s laid over the drawing (or SVG with
  a matching accessible list). Selected edge drawn in ink with a count badge at its midpoint.
- **Click an edge (or a row in "Pairs, most contact first")** → side panel lists everything between the
  two, oldest first: kind (colour dot), direction (`A → B`, or `A approached B at <place>` for
  encounters), `day N · HH:00`, and the content (dialogue lines; letter text; parcel items and note;
  offer `give for want`, marking counteroffers; trade terms).
- **Click a node** → only that villager's edges stay at full strength, and the panel lists all of their
  interactions. A "Clear" button returns to pair view.
- Default selection: the pair with the most interactions.
- A pairs list (top 12) with bars mirrors the edges for keyboard and screen-reader use.

## Data sources

All from the run directory. Event fields as written by `sim.py` (check names against the code).

| need | source |
|---|---|
| run line, villagers, seed, world | `manifest.json` |
| dates, weather, money rock, shop stock | `day_start` |
| actions | `action`: `day, hour, agent, action{do,...}, location, ok, result, delta, pools_delta, at` |
| plans | `plan`: `agent, window, thoughts, note, scheduled[], dropped[], choices, error` |
| conversations | `encounter`: `villagers[2]` (the first seems to be the one who approached and speaks first; confirm in `sim.py`), `location`, `turns[{speaker,text,thoughts}]`, `visit` |
| noticing | `encounter_decision`, `seen` (pairs within sight per hour) |
| discoveries | `sighting` (per villager per hour: what they spotted) |
| visits | `visit`: `guest, host, answered, interior` |
| mail | `message` (`sender, recipient, content, arrives_day`), `parcel` (`sender, recipient, items, note`) |
| gifts | `gift`: `giver, receiver, items, how` (`in person` or `by mail`) |
| plaza | `board` (`author, content`), `offer` (`id, sender, recipient, give, want, note, status, parent`), `offer_update`, `trade` (`seller, buyer, give, want`, Cranny values) |
| nightly pages | `reflection`: `agent, self_doc, skipped`; also `selves/<name>.md` |
| commons by hour | `hour_state.pools`: `rocks[]` (hits left per rock), `wood_left, fruit_on_trees, trees{}, shells_left, fossils_left, museum, bridge_fund` |
| villagers by hour | `hour_state.villagers[name]`: `activity, location, place, pos, bells, loan, items, house, walked_today` |
| end of day | `snapshot`: `villagers{bells, loan, house, items, inventory_value, recipes, interior, wearing}`, `homes`, `objects{trees, rocks, paths, placed, flowers, bridge_open}` |
| energy | `energy`: per villager `spent{planning,thinking,talking}, left, skipped, tokens` |
| Isabelle | `isabelle_summary.text` |
| run-level stats (HHI, Gini, run-out hours, top actions, spotted) | reuse the functions behind `summary.txt` in `island/analysis.py` |
| environment per villager | `villagers_env.csv`; behaviour metrics in `traits_daily.csv` |

Hours run 5 to 28 within an island day (24 to 28 are 00:00 to 04:00); display `hour % 24`.

## Privacy

The explorer is for the researcher, so it shows private thoughts, notes and nightly pages, always marked
as private. It must never imply a villager could see another's private content: in conversation views,
show each speaker's own thought under their own line only.

## The mockups

`mockups/*.dc.html` are the canvas sources (`doors` is the Experiments page, `home`, `days` and `social`
are a run's tabs). They are not standalone pages (they need the canvas
runtime), but all layout and styling is inline and readable, and the `renderVals()` script at the bottom
of each shows the exact derived values and sample data from a 3-day mock run (`--backend mock`, seed 0).
Treat them as the visual reference; implement in plain HTML/CSS/JS.

Mock-run text is repetitive ("rocks first, then fishing"); real runs will have long thoughts and pages,
so give every text block room to wrap and cap long lists with "show more".
