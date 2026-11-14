"""Write docs/world.md and docs/island_grid.png from data/island_world.yaml and the island generator.

Rerun after changing the world file or the grid code, so the doc can't drift:
    python scripts/world_doc.py            # seed 0
    python scripts/world_doc.py --seed 3   # another island
"""

from __future__ import annotations

import argparse
import random
from collections import Counter, deque
from pathlib import Path

from llmabm.island.grid import POND, RIVER, ROCKY, SAND, SEA, TIER_NAME, WATER, GridSpace
from llmabm.island.grid_render import render_grid_map
from llmabm.island.world import ORDINANCE_TEXT, Island, clock, fmt_window, load_world, structures_cfg

ROOT = Path(__file__).resolve().parent.parent
TOOLS = [("no tools", frozenset()), ("vaulting pole", frozenset({"vaulting pole"})),
         ("ladder", frozenset({"ladder"})), ("both", frozenset({"vaulting pole", "ladder"}))]


def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def n(x: float) -> str:
    return f"{x:,.0f}"


def needs(d: dict) -> str:
    return ", ".join(f"{q} {k}" for k, q in d.items())


def build(seed: int):
    w = load_world(ROOT / "data/island_world.yaml")
    g = GridSpace(w, seed)
    isl = Island(w, [], random.Random(seed))
    isl.sites = {sid: {"area": v["area"], "at": list(v["door"])} for sid, v in g.sites.items()}
    g.attach(isl)
    isl.day = 0  # day 0 morning: the wild flowers are in bloom
    return w, g, isl


def river_widths(g: GridSpace) -> list[int]:
    """How many river tiles across the river is at each river tile: the shorter of its run across and down."""
    def run(x, y, dx, dy):
        k = 1
        for s in (1, -1):
            cx, cy = x + s * dx, y + s * dy
            while 0 <= cx < g.W and 0 <= cy < g.H and g.terrain[cy][cx] == RIVER:
                k, cx, cy = k + 1, cx + s * dx, cy + s * dy
        return k
    return [min(run(x, y, 1, 0), run(x, y, 0, 1)) for y in range(g.H) for x in range(g.W) if g.terrain[y][x] == RIVER]


def mouths(g: GridSpace) -> int:
    """Separate places where the river meets the sea."""
    touch = {(x, y) for y in range(g.H) for x in range(g.W) if g.terrain[y][x] == RIVER
             and any(0 <= x + dx < g.W and 0 <= y + dy < g.H and g.terrain[y + dy][x + dx] == SEA
                     for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
    seen, groups = set(), 0
    for c in touch:
        if c in seen:
            continue
        groups += 1
        q = deque([c])
        seen.add(c)
        while q:
            x, y = q.popleft()
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    m = (x + dx, y + dy)
                    if m in touch and m not in seen:
                        seen.add(m)
                        q.append(m)
    return groups


def map_section(w: dict, g: GridSpace, isl: Island, seed: int) -> str:
    cells = [(x, y) for y in range(g.H) for x in range(g.W)]
    kind = Counter()
    for x, y in cells:
        t = g.terrain[y][x]
        if t == SEA:
            kind["sea"] += 1
        elif t == SAND:
            kind["beach"] += 1
        elif t == ROCKY:
            kind["rocky beach"] += 1
        elif t == RIVER:
            kind[f"river ({TIER_NAME[g.tier[y][x]]})"] += 1
        elif t == POND:
            kind["pond"] += 1
        else:
            kind[TIER_NAME[g.tier[y][x]]] += 1
    order = ["sea", "beach", "rocky beach", "lower ground", "the middle cliff", "the top cliff",
             "river (the top cliff)", "river (the middle cliff)", "river (lower ground)", "pond"]
    share = lambda k: f"{kind[k] / len(cells):.0%}" if kind[k] / len(cells) >= 0.005 else "<1%"  # noqa: E731
    tiles = table(["tile", "count", "share"], [[k, n(kind[k]), share(k)] for k in order if kind[k]])

    trees = [t for t in isl.trees if t.pos]
    by_tier = Counter((TIER_NAME[g.tier[t.pos[1]][t.pos[0]]], t.kind) for t in trees)
    tree_rows = [[tier, by_tier[(tier, "hardwood")], by_tier[(tier, "fruit")]] for tier in TIER_NAME.values()]
    rocks = [[i + 1, g.region(c)] for i, c in enumerate(g.rock_cells) if c]
    buildings = [[name, f"{len({x for x, _ in b['cells']})} x {len({y for _, y in b['cells']})}", g.region(b["door"]),
                  "walkable" if not b.get("blocking", True) else ""] for name, b in g.buildings.items()]
    areas = Counter(s["area"].removeprefix("lower ground, ") for s in g.sites.values())

    rs = g.buildings["Resident Services"]["door"]
    land = [c for c in cells if g.terrain[c[1]][c[0]] not in WATER | {ROCKY}]
    reach = []
    for label, tools in TOOLS:
        dist = g.distances(rs, tools)
        reach.append([label, f"{sum(1 for c in land if c in dist) / len(land):.0%}",
                      f"{sum(1 for c in g.rock_cells if c and any(a in dist for a in g.approach_cells(c)))} of "
                      f"{sum(1 for c in g.rock_cells if c)}",
                      f"{sum(1 for t in trees if any(a in dist for a in g.approach_cells(t.pos)))} of {len(trees)}",
                      f"{sum(1 for b in g.buildings.values() if b['door'] in dist)} of {len(g.buildings)}",
                      f"{sum(1 for s in g.sites.values() if s['door'] in dist)} of {len(g.sites)}"])
    gc, br = g.cfg, w["bridge"]
    st = structures_cfg(w)
    bridge = (f"across {len(g.bridge_cells)} tiles of river on {g.region(g.bridge_cells[0])}" if g.bridge_cells
              else "nowhere on this island (no short crossing was found)")
    widths = river_widths(g)
    spots = {k: len(g.structure_spots(isl, k)) for k in ("bridge", "stairs")}
    fo, bo = gc["fish_out"], gc["bugs_out"]
    out_fish = (f"{fo['sea']} in the sea, {fo['river']} in the river, {fo['pond']} in the pond and "
                f"{fo['clifftop']} in the river up on the cliffs")
    out_bugs = ", ".join(f"{v} on {k}" if k != "ground" else f"{v} in the grass" for k, v in bo.items())
    return f"""## The map

![The island for seed {seed}](island_grid.png)

Every run generates its island from the seed, so each seed gives a different island built the same
way. This is seed {seed}, before anyone arrives. North is at the top.

**How it's built.** A {g.W} x {g.H} tile grid: a sea margin of {gc['sea_margin']} tiles, a beach
{gc['beach_width']} tiles wide all round, and grass on three cliff tiers that rise toward the north.
A river starts on the top cliff, drops down the tiers and splits on its way to the sea
({mouths(g)} mouths on this island). There is one pond, and a rocky stretch of the north beach that
can't be walked. The buildings and all {len(g.sites)} homesites are on the lower ground. Trees and
rocks each take one tile and need the 8 tiles around them clear of other trees, rocks, buildings,
water and cliff edges.

{tiles}

**Getting around.** Beach and grass are walkable. Trees, rocks, buildings, homes, placed furniture
and fences block their tiles; the plaza doesn't. Going up or down one cliff tier needs a **ladder**.
Crossing the river needs a **vaulting pole**, which clears up to {gc['pole_max_width']} tiles of water,
except over a built bridge. The river is {sum(widths) / len(widths):.1f} tiles across on average
(measured straight across at each river tile; {min(widths)} to {max(widths)}). The pond and the sea can
never be crossed. Everyone starts with both, and they wear out (see Tools). Trees, rocks, buildings
(the plaza too) and homes always keep at least one free tile between them.

**Bridges and stairs.** The first bridge goes {bridge}, near Resident Services; it costs
{n(br['cost'])} bells, raised by anyone at Resident Services, and once built it is a way across the
river without a pole. Villagers can propose more bridges, and stairs, where they stand, each built
once its own fund reaches {n(st['cost'])} bells; {st['max']} in all, built or proposed, the first bridge
included, and one proposal of each villager's still being funded at a time. A bridge is 3 tiles wide
and crosses at most {st['bridge_span']} tiles of lower river, with a landing on each bank (the first
bridge too); stairs are 2 tiles wide and 4 long, two rows below a cliff edge and two above, and let
anyone climb there without a ladder. Every land tile under one has to be clear, it has to be at least
{st['spacing']} tiles from any other, and from the moment it's proposed nothing can be planted or put
on it. This island has {spots['bridge']} spots for a bridge and {spots['stairs']} for stairs before any
are taken. They only give access. (In runs on prompt versions before 17, the first bridge also adds
{br['unlocks'].get('rocks', 0)} rocks and {br['unlocks'].get('hardwood_trees', 0)} hardwood trees at
random places when built, and villagers can't propose more.)

What you can reach from Resident Services:

{table(["carrying", "land", "rocks", "trees", "buildings", "homesites"], reach)}

**Buildings**

{table(["building", "size (tiles)", "where", "notes"], buildings)}

**Homesites.** {len(g.sites)} sites of 5 x 4 tiles on the lower ground, at least
{gc['homesite_spacing']} tiles apart: {"; ".join(f"{k} {v}" for k, v in sorted(areas.items()))}.
Villagers whose homes are in the same area are neighbors and notice each other heading out. On day 0
each villager gets one at random; moving costs {n(w['move_house_cost'])} bells.

**Trees** ({len(trees)} at the start; chopped-down trees leave a stump and don't come back)

{table(["tier", "hardwood", "fruit"], tree_rows)}

**Rocks** ({len(rocks)}; a broken rock turns up somewhere new after
{w['building'].get('rock_respawn_days', 3)} days)

{table(["rock", "where"], rocks)}

**Flowers.** {gc.get('wild_flower_patches', 6)} patches of wild flowers at the start, which bugs visit.

**What lives where, and what villagers can see.** Villagers see {n(gc['sight_radius'])} tiles around
them. Fish show as shadows at the water's edge, at most {out_fish} at a time. Bugs sit where
they live, at most {out_bugs}. Shells ({w['island']['shells_per_day']} a day) lie on the beach and
fossil dig spots ({w['island']['fossils_per_day']} a day) on the grass, in new places every
morning, and a villager only knows the ones they have seen. Fish and bugs come and go by the hour.
"""


def daily_section(w: dict) -> str:
    i = w["island"]
    yields = ", ".join(f"{k} {v:.0%}" for k, v in i["rock_yields"].items())
    rows = [
        ["rocks", f"{i['rocks']} rocks, {i['rock_hits']} items each a day ({yields}); one rock a day pays "
                  f"{n(min(i['money_rock_bells']))} to {n(max(i['money_rock_bells']))} bells a hit instead", "every morning"],
        ["hardwood trees", f"{i['hardwood_trees']} trees, {i['wood_per_tree']} wood a day each (wood, softwood, hardwood)",
         "every morning"],
        ["fruit trees", f"{i['fruit_trees']} trees, {i['fruit_per_tree']} {i['native_fruit']}s each", f"{i['fruit_regrow_days']} days after shaking"],
        ["shells", f"{i['shells_per_day']} a day on the beach, up to {i['shells_per_action']} per beachcombing; "
                   f"{i['bottle_chance']:.0%} chance of a message bottle with a recipe", "every morning"],
        ["fossils", f"{i['fossils_per_day']} dig spots a day", "every morning"],
        ["fish and bugs", f"by hour, month and weather; {i['catch_fail_rate']:.0%} of tries get away", "hour by hour"],
        ["saplings and fruit", f"planted, they grow into trees in {i['sapling_mature_days']} days", ""],
        ["flower seeds", f"bloom in {w['building'].get('flower_bloom_days', 3)} days", ""],
    ]
    return "## What the island gives\n\n" + table(["resource", "how much", "refills"], rows) + "\n"


def items_section(w: dict) -> str:
    mats = w["materials"]
    src = {"wood": "chop_wood", "softwood": "chop_wood", "hardwood": "chop_wood",
           "stone": "mine_rock", "clay": "mine_rock", "iron nugget": "mine_rock", "gold nugget": "mine_rock, rarely",
           w["island"]["native_fruit"]: "shake_tree; eat one to break_rock", "sapling": "Nook's Cranny"}
    mat_rows = [[k, src.get(k, ""), n(v)] for k, v in mats.items()]

    starters = set(w.get("starter_recipes", []))
    items = w.get("items") or {}
    rec_rows = []
    for k, r in w["recipes"].items():
        info = items.get(k, {})
        notes = []
        if info.get("kind") == "tool":
            notes.append("tool")
        if r.get("makes", 1) > 1:
            notes.append(f"makes {r['makes']}")
        if info.get("size", [1, 1]) != [1, 1]:
            notes.append(f"{info['size'][0]} x {info['size'][1]} tiles")
        rec_rows.append([("★ " if k in starters else "") + k, needs(r["needs"]), n(r["price"]), ", ".join(notes)])

    cr, ab = w["shops"]["cranny"], w["shops"]["able"]
    uses = {"seeds": "plant: flowers", "furniture": "furniture", "fence": "fence, makes a 1-tile piece",
            "bait": "fish with \"bait\": true and a fish is sure to appear", "sapling": "plant: a hardwood tree"}
    shop_rows = []
    for pool, note in (("always", "always in stock"), ("seeds_pool", f"{cr.get('seeds_daily', 0)} kinds a day"),
                       ("stock_pool", f"{cr['sells_daily']} a day")):
        for k, v in (cr.get(pool) or {}).items():
            info = items.get(k, {})
            what = uses.get(info.get("kind", ""), "")
            if info.get("outdoor") is False:
                what += ", indoor only"
            shop_rows.append([k, "Nook's Cranny", note, n(v), what])
    shop_rows.append([f"every tool's recipe card", "Nook's Cranny", "always in stock", n(cr["tool_recipe_price"]),
                      "learn to make that tool"])
    if cr.get("tool_markup"):
        shop_rows.append(["every tool, ready-made", "Nook's Cranny", "always in stock",
                          f"{cr['tool_markup']}x its sell value", "a new tool"])
    shop_rows.append([f"one other recipe card", "Nook's Cranny", "1 a day, never a tool", n(cr["recipe_price"]),
                      "learn the recipe"])
    for k, v in (w.get("clothing") or {}).items():
        shop_rows.append([k, "Able Sisters", f"{ab['sells_daily']} a day", n(v["price"]), f"{v['slot']}: {v['description']}"])

    def rng_(vals):
        vals = list(vals)
        return f"{n(min(vals))}-{n(max(vals))}"

    col = [["fish", len(w["fish"]), rng_(v["price"] for v in w["fish"].values()), "fish, in the sea, river (also on cliffs) or pond"],
           ["bugs", len(w["bugs"]), rng_(v["price"] for v in w["bugs"].values()), "catch_bug on flowers, trees, stumps, rocks or the ground"],
           ["fossils", len(w["fossils"]), rng_(w["fossils"].values()), "dig_fossil, then assess at the museum"],
           ["shells", len(w["shells"]), rng_(v[0] for v in w["shells"].values()), "beachcomb"]]
    fish = table(["fish", "sells for", "shadow", "where", "hours", "months", "weather"],
                 [[k, n(v["price"]), v["shadow"], v["where"], fmt_window(v["hours"]),
                   ", ".join(map(str, v["months"])) if v.get("months") else "all year", v.get("weather", "any")]
                  for k, v in w["fish"].items()])
    bugs = table(["bug", "sells for", "habitat", "hours", "months", "weather"],
                 [[k, n(v["price"]), v.get("habitat", "ground"), fmt_window(v["hours"]),
                   ", ".join(map(str, v["months"])) if v.get("months") else "all year", v.get("weather", "any")]
                  for k, v in w["bugs"].items()])
    fossils = table(["fossil", "sells for"], [[k, n(v)] for k, v in w["fossils"].items()])
    fa = w["fossil_assessment"]
    shells = table(["shell", "sells for"], [[k, n(v[0])] for k, v in w["shells"].items()])
    return f"""## Items

### Materials (gathered)

{table(["item", "where it comes from", "sells for"], mat_rows)}

### Recipes ({len(w['recipes'])})

Everyone knows the ones marked ★ from day 0, plus {w['recipes_known_at_start']} drawn at random from the
rest (never a tool). More come on recipe cards: the Cranny's tool cards and its one other card a
day, message bottles on the beach, and other villagers. A card for a recipe you don't know is
learned on the spot; a card for one you know stays in your pockets to give, mail, trade or sell.
Furniture can go inside a home (visitors see it) or outside, where it blocks its tiles. Fences come
out in pieces of one tile.

{table(["recipe", "needs", "sells for", "notes"], rec_rows)}

### Bought

Shop goods sell back for a quarter of their price.

{table(["item", "shop", "stock", "price", "what it is"], shop_rows)}

### Collectibles (museum or Nook's Cranny)

Blathers takes the first of each species, and the museum shows who donated each one and how many
each villager has given. Names below are the unperturbed ones; see the last section.

A dug-up fossil is a plain "fossil" until Blathers assesses it (free, at the museum, `assess`).
Unassessed, Nook's Cranny pays {n(fa['unassessed_price'])} for it and the museum won't take it. Assessed, it
becomes one species, picked at random, worth its price below; perturbation keeps fossil prices
between {n(fa['min_price'])} and {n(fa['max_price'])}.

{table(["kind", "species", "sells for", "how"], col)}

<details><summary>Every fish, bug, fossil and shell</summary>

{fish}

{bugs}

{fossils}

{shells}

</details>
"""


def tools_section(w: dict) -> str:
    t, rec = w["tools"], w["recipes"]
    wears = {"shovel": "mine_rock, dig_fossil, dig_stump, break_rock", "stone axe": "chop_wood (the tree stays)",
             "axe": "chop_wood; brings the tree down with its last wood (a fruit tree drops its fruit)",
             "fishing rod": "fish", "net": "catch_bug", "ladder": "each trip whose route climbs a cliff",
             "vaulting pole": "each trip whose route jumps the river"}
    rows = [[k, wears.get(k, ""), needs(rec[k]["needs"]) if k in rec else ""]
            for k in t["start_with"] + [r for r in rec if (w.get("items") or {}).get(r, {}).get("kind") == "tool"
                                        and r not in t["start_with"]]]
    mult = w["shops"]["cranny"].get("tool_markup")
    ready = f", or the tool itself, ready-made ({mult}x its sell value)" if mult else ""
    return f"""## Tools

Everyone starts with one of each, except the axe, which is an upgrade (its recipe card is at
Nook's Cranny); nobody knows how to make them. Each breaks after {t['uses']}
uses. Without the tool an action fails before the villager sets out. Villagers aren't told how worn a
tool is; they find out when it breaks (from prompt version 12 they see each tool's uses left). A
replacement takes the tool's recipe card (always at Nook's Cranny,
{n(w['shops']['cranny']['tool_recipe_price'])} bells) and materials{ready}. Spare tools can be handed over
or mailed, and a worn tool stays worn.

{table(["tool", "worn by", "made from"], rows)}
"""


def services_section(w: dict) -> str:
    hrs = w["hours"]
    houses = table(["house", "storage", "furniture inside", "costs (Tom Nook loan)"],
                   [[h["name"], h["storage"], h.get("interior", ""), n(h["cost"])] for h in w["house_levels"]])
    ords = w["ordinances"]
    ord_rows = [[k.replace("_", " "), ORDINANCE_TEXT.get(k, k).split(": ", 1)[-1]] for k in ords["types"]]
    return f"""## Services and prices

| who | where | open | what they do |
|---|---|---|---|
| Tom Nook | Resident Services | {fmt_window(hrs['resident_services'])} | house upgrades on a loan with no interest and no deadline (pay it off before the next one); moves your home for {n(w['move_house_cost'])} bells overnight; bridge and stairs funds ({n(w['bridge']['cost'])} bells each) |
| Isabelle | Resident Services | {fmt_window(hrs['resident_services'])} | sets an ordinance for {n(ords['cost'])} bells, from the next morning |
| Timmy and Tommy | Nook's Cranny | {fmt_window(hrs['cranny'])} | buy almost anything at a fixed price; sell a small daily stock |
| the Able Sisters | Able Sisters | {fmt_window(hrs['able'])} | sell clothes |
| Blathers | museum | {fmt_window(hrs['museum'])} | takes the first of each fish, bug and fossil and keeps a public tally of donors; pays nothing; assesses fossils for free |

Everyone carries {w['pockets']} items. From prompt version 12 a house's storage is room to keep things at
home, reachable only there (a tent has none); before, it added to what you could carry.

{houses}

Ordinances (one at a time):

{table(["ordinance", "effect"], ord_rows)}
"""


def perturb_section() -> str:
    return """## What changes from run to run

Runs perturb the world with the seed, so models can't lean on what they remember about ACNH:
- every fish, bug, fossil and shell gets an invented name (recipes that use shells follow)
- material, collectible and recipe sell prices are scaled by a random 0.6-1.6x
- each fish gets a new kind of water (sea, river or pond) and new hours

Shop prices, house prices, the bridge and the ordinances stay as above. The island layout comes
from the seed too, so it changes with every seed.
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    w, g, isl = build(args.seed)
    objects = g.objects_snapshot(isl)
    trees = [tuple(t) for t in objects["trees"]]
    render_grid_map(g, ROOT / "docs/island_grid.png", {}, trees, list(g.rock_cells),
                    title=f"The island, seed {args.seed}, before anyone arrives", objects=objects, bridge_site=True)
    doc = "\n".join([
        "# The island world",
        "",
        "Everything on the island, for you. Villagers never see this page: they learn the island by",
        "trying things, asking the NPCs and talking to each other (see the README). Generated by",
        f"`scripts/world_doc.py` from `data/island_world.yaml` and the island generator; rerun it after",
        "changing either. Day starts at " + clock(w["calendar"]["day_start"]) + ".",
        "",
        map_section(w, g, isl, args.seed),
        daily_section(w),
        tools_section(w),
        items_section(w),
        services_section(w),
        perturb_section(),
    ])
    (ROOT / "docs/world.md").write_text(doc)
    print("wrote docs/world.md and docs/island_grid.png")


if __name__ == "__main__":
    main()
