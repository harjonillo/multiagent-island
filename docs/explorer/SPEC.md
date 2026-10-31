# Island explorer: spec

A read-only browser view of one island run, built from the files `llmabm run` already writes. Three
tabs: **Home** (the whole run), **Days** (one or more days, per villager) and **Social** (who deals with
whom). Visual style: "Island Pixel", see `tokens.css`. Interactive mockups of all three tabs are on the
design canvas, and their full markup and logic are in `mockups/` (see "The mockups" below).

## Recommended shape

- A new command, `llmabm explore <run_dir>`, writes `<run_dir>/explorer/index.html`: one self-contained
  page (HTML, CSS, JS inline, no server, no build step) with the run's data embedded as JSON, plus
  `explorer/island.png`. Opening the file in a browser is the whole workflow.
- Build the data in Python from `analysis.load_run(run_dir)` and the existing island analysis helpers,
  so the numbers match `summary.txt`. Do not recompute anything differently from `island/analysis.py`.
- Optionally call it at the end of `analyze_island` so every run gets an explorer.
- No LLM calls. Rebuilding is like `llmabm analyze`: from `events.jsonl` only.
- Keep the page working offline except for the two Google Fonts (fall back to `ui-monospace`).
- Add a test that builds the explorer for a short mock run and checks the embedded JSON has every
  villager, every day and no private field leaking where it shouldn't (see Privacy).

## Shared chrome

- **Top bar:** inset to the page margins (28px), 20px above it, 2px `--tabbar-rule` lines above and below,
  10px vertical padding. Left: `multiagent-island` in Silkscreen 18px, then the run line in body font
  13px muted: `<scenario name> · seed <n> · <backend>`. Right: tabs Home, Days, Social. Active tab:
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

`mockups/*.dc.html` are the canvas sources. They are not standalone pages (they need the canvas
runtime), but all layout and styling is inline and readable, and the `renderVals()` script at the bottom
of each shows the exact derived values and sample data from a 3-day mock run (`--backend mock`, seed 0).
Treat them as the visual reference; implement in plain HTML/CSS/JS.

Mock-run text is repetitive ("rocks first, then fishing"); real runs will have long thoughts and pages,
so give every text block room to wrap and cap long lists with "show more".
