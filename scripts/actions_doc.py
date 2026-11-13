"""Write docs/actions.md: every action, what puts it on a villager's list and what comes of it.

The needs follow the checks in src/llmabm/island/options.py; the outcomes (what you get, with what chance, what
you use up, what changes) are worked out from data/island_world.yaml and the rules in world.py, so rerun this
after changing either:
    python scripts/actions_doc.py
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from llmabm.island.world import ORDINANCE_TEXT, Island, load_world, structures_cfg

ROOT = Path(__file__).resolve().parent.parent
WALK = "you can walk to"
ROOM = "room in your pockets"


def pct(x: float) -> str:
    p = 100 * x
    return f"{p:.0f}%" if p >= 9.5 or p == round(p) else f"{p:.1f}%"


def n(x) -> str:
    return f"{x:,}"


def window(h) -> str:
    return "all day" if list(h) == [0, 24] else f"{h[0]:02d}:00-{h[1]:02d}:00"


def months(spec: dict) -> str:
    m = spec.get("months")
    return "all year" if not m else ", ".join(str(x) for x in m)


def world_numbers(w: dict) -> dict:
    """Everything the outcomes quote, from the world file."""
    isl, b = w["island"], w.get("building") or {}
    probe = Island(w, ["A"], random.Random(0))
    tools = probe.tool_recipes()
    cards = sorted(set(w["recipes"]) - set(tools))
    mult = w["shops"]["cranny"].get("tool_markup")
    return {
        "hits": isl["rock_hits"], "rocks": isl["rocks"], "yields": isl["rock_yields"],
        "money": sum(isl["money_rock_bells"]), "wood": isl["wood_per_tree"], "fruit": isl["fruit_per_tree"],
        "fruit_name": isl["native_fruit"], "regrow": isl["fruit_regrow_days"], "sapling": isl["sapling_mature_days"],
        "fail": isl["catch_fail_rate"], "bottle": isl["bottle_chance"], "shells_action": isl["shells_per_action"],
        "shells_day": isl["shells_per_day"], "fossils_day": isl["fossils_per_day"],
        "clams_day": isl.get("clams_per_day", 0), "spots": (w.get("grid") or {}).get("search_spots", 3),
        "bloom": b.get("flower_bloom_days", 3), "path": b.get("path_tiles_per_action", 30),
        "respawn": b.get("rock_respawn_days", 3), "uses": (w.get("tools") or {}).get("uses", 50),
        "cards": len(cards), "fossils": w["fossils"], "makes": {r: v["makes"] for r, v in w["recipes"].items()
                                                                 if v.get("makes", 1) > 1},
        "tool_price": {t: w["recipes"][t]["price"] * mult for t in tools} if mult else {},
        "ord_cost": w["ordinances"]["cost"], "move": w.get("move_house_cost", 10000), "st": structures_cfg(w),
        "levels": w["house_levels"], "hours": w["hours"],
    }


def entries(N: dict) -> list:
    """The groups of actions: each action with its line on the list, what it needs, and what comes of it.
    Each is (name, line, in pockets, where, only if, gives, uses up, changes, still fails if, extra markdown)."""
    y = N["yields"]
    yields = "<br/>".join(" · ".join(f"{k} {pct(v)}" for k, v in list(y.items())[i:i + 2]) for i in range(0, len(y), 2))
    fail, got = pct(N["fail"]), pct(1 - N["fail"])
    fruit, st = N["fruit_name"], N["st"]
    a = lambda s: ("an " if s[:1] in "aeiou" else "a ") + s  # noqa: E731
    fossil_lo, fossil_hi = min(N["fossils"].values()), max(N["fossils"].values())
    fence = ", ".join(f"{r}: {k} pieces" for r, k in N["makes"].items()) or "everything: 1"
    tool_eg = N["tool_price"].get("shovel", 800)
    return [
        ("Gathering", [
            ("mine_rock", "mine rock 3 (lower ground, centre)", ["shovel"],
             [f"a rock {WALK}<br/>one line per rock"], [ROOM, "the rock is standing<br/>(not broken)", "it isn't mined out today"],
             [f"each hit the rock has left today<br/>(up to {N['hits']}), 1 of:<br/>{yields}",
              f"or, if it's today's money rock<br/>(one rock a day, 1 in {N['rocks']} while all stand):<br/>"
              f"its bells instead, up to {n(N['money'])}"],
             ["1 use of the shovel"], ["the rock gives nothing more today<br/>(it refills tomorrow)"],
             "someone mined it out first; pockets full (what doesn't fit stays in the rock)", ""),
            ("break_rock", "break rock 3 (lower ground, centre), eating 1 apple to be strong enough",
             ["shovel", f"{a(fruit)}<br/>(eaten)"],
             [f"a rock {WALK}<br/>one line per rock"], ["the rock is standing<br/>(not broken)"],
             [], [f"1 {fruit}", "1 use of the shovel"],
             [f"the rock is gone; a new one turns<br/>up somewhere in {N['respawn']} days",
              "everyone hears of it<br/>in Isabelle's news"],
             "someone broke it first; the apple is gone", ""),
            ("chop_wood", "chop wood from a hardwood tree with the axe (the axe brings the tree down when it takes its last wood)",
             ["an axe or a stone axe<br/>one line per axe carried"],
             [f"a hardwood or fruit tree<br/>{WALK}<br/>one line per kind"], [ROOM, "the tree has wood left today"],
             [f"each wood the tree has left today<br/>(up to {N['wood']}), 1 of:<br/>wood, softwood or hardwood,<br/>1 in 3 each",
              f"with the axe, taking its last wood:<br/>a fruit tree drops its {fruit}s too<br/>(up to {N['fruit']})"],
             ["1 use of the axe or stone axe"],
             ["the tree's wood is gone for today<br/>(it grows back tomorrow)",
              "with the axe, at its last wood:<br/>the tree comes down, a stump left<br/>(the stone axe never fells)"],
             "the wood was taken first; pockets full", ""),
            ("shake_tree", "shake a fruit tree", [], [f"a fruit tree {WALK}"], [ROOM, "it has fruit on it"],
             [f"every {fruit} on the tree<br/>(up to {N['fruit']})"], [],
             [f"the tree is bare until it regrows,<br/>{N['regrow']} days later"],
             "the fruit was taken first; pockets full", ""),
            ("fish", "fish in the river", ["fishing rod"],
             [f"the edge of the sea, the river,<br/>the pond or the river up on the cliffs<br/>{WALK}; one line per water"], [ROOM],
             [f"a fish shadow in sight, or found<br/>looking from up to {N['spots']} spots:<br/>{got} you catch it, {fail} it gets away",
              "which fish: by weight among those<br/>out in that water at that hour,<br/>month and weather (tables below)",
              "no shadow found: nothing,<br/>the hour went on looking"],
             ["1 use of the fishing rod<br/>(when a shadow was found)"],
             ["you see what it is<br/>(a shadow shows only its size)"],
             "pockets full; the rod broke", "FISH"),
            ("fish (with bait)", "fish in the river with fishing bait (uses 1; you carry 2)",
             ["fishing rod", "fishing bait<br/>(1 used)"],
             [f"the edge of the sea, the river,<br/>the pond or the river up on the cliffs<br/>{WALK}; one line per water"], [ROOM],
             [f"a shadow appears for sure:<br/>{got} you catch it, {fail} it gets away",
              "which fish: by weight among those<br/>out in that water then (tables below)",
              "no fish out in that water at that<br/>hour: nothing, and you keep the bait"],
             ["1 fishing bait", "1 use of the fishing rod"], [],
             "pockets full; the rod broke (with the bait gone, they fish without it and are told so)", ""),
            ("catch_bug", "catch bugs among the flowers", ["net"],
             [f"flowers in bloom, trees, stumps,<br/>rocks or grass {WALK}<br/>one line per habitat"],
             [ROOM, "there are some of that habitat<br/>on the island"],
             [f"a bug in sight, or found looking<br/>from up to {N['spots']} spots:<br/>{got} you catch it, {fail} it gets away",
              "which bug: by weight among those<br/>out on that habitat at that hour,<br/>month and weather (table below)",
              "no bug found: nothing,<br/>the hour went on looking"],
             ["1 use of the net<br/>(when a bug was found)"], [],
             "pockets full; the net broke", "BUGS"),
            ("beachcomb", "beachcomb", [], [f"a beach {WALK}"], [ROOM],
             [f"up to {N['shells_action']} shells, each 1 of the<br/>shells by weight (table below)",
              f"{pct(N['bottle'])}: a message bottle with a recipe<br/>card, 1 of {N['cards']} (not tools), equal chances:<br/>"
              "you learn it, or keep the card<br/>if you know it already",
              "no shells in sight nearby:<br/>nothing (no bottle either)"],
             [], [f"{N['shells_day']} shells on the island a day;<br/>whoever comes first gets them"],
             "nothing: a picked-clean beach is still an hour on the beach", "SHELLS"),
            ("dig_fossil", "dig for fossils", ["shovel"], [f"open ground {WALK}"], [ROOM, "dig spots left on the island today"],
             [f"1 fossil, unassessed: nobody knows<br/>which of {len(N['fossils'])} it is until Blathers<br/>assesses it (see assess)",
              "no dig spot in sight nearby:<br/>nothing"],
             ["1 use of the shovel<br/>(when you dig one up)"], [f"{N['fossils_day']} dig spots on the island a day"],
             "the last spots were dug first; pockets full", ""),
            ("dig_clam", "dig for manila clams on the beach", ["shovel"], [f"a beach {WALK}"],
             [ROOM, "clam spots left on the beach today"],
             ["1 manila clam", "no clam spot in sight nearby:<br/>nothing"],
             ["1 use of the shovel<br/>(when you dig one up)"],
             ["your first clam teaches you the<br/>fishing bait recipe:<br/>1 manila clam makes 1 fishing bait",
              f"{N['clams_day']} clam spots on the beach a day"],
             "the last spots were dug first; pockets full", ""),
            ("dig_stump", "dig up a stump", ["shovel"], [f"a stump {WALK}"], [], [], ["1 use of the shovel"],
             ["the stump is gone;<br/>the tile is clear again"], "someone dug it up first", ""),
        ]),
        ("Bridges and stairs (from prompt version 17)", [
            (f"propose_{k}", f"propose {what}, at the nearest spot within {st['near']} steps of where you are then "
                             f"(built once {st['cost']} bells are donated at Resident Services; 2 of {st['max']} on the island so far)",
             [], [f"where you stand at that hour:<br/>the nearest spot within {st['near']} steps for<br/>{shape}"],
             [f"fewer than {st['max']} bridges and stairs<br/>on the island, built or proposed<br/>(the first bridge counts)",
              "no proposal of yours<br/>still being funded",
              f"a spot like that somewhere<br/>you can walk to, its land clear<br/>(no tree, rock, home, building,<br/>"
              f"furniture or flowers), {st['spacing']} tiles<br/>from any other bridge or stairs"],
             [], [],
             [f"its own fund opens at Resident<br/>Services: 0 of {n(st['cost'])} bells",
              "its footprint is a construction site:<br/>walkable, but nothing can be<br/>planted or put on it",
              f"once the fund is full, it's built:<br/>{built}", "everyone hears of it<br/>in Isabelle's news"],
             f"there's no such spot within {st['near']} steps of where you are then (it says where the nearest is)", "")
            for k, what, shape, built in (
                ("bridge", "a bridge across the river",
                 f"a 3-wide bridge over at most<br/>{st['bridge_span']} tiles of lower river,<br/>a landing on each bank",
                 "anyone crosses the river<br/>there without a vaulting pole"),
                ("stairs", "stairs up a cliff", "stairs 2 wide and 4 long, two rows<br/>below a cliff edge and two above",
                 "anyone climbs that cliff<br/>there without a ladder"))
        ]),
        ("Outside", [
            ("plant", 'plant an apple (you carry 3); add "near": a place',
             [f"{a(fruit)}, a sapling or flower seeds<br/>one line per kind carried"], [f"near a place you choose<br/>that {WALK}"], [],
             [], ["the one you plant"],
             [f"{a(fruit)}: a sapling that is a<br/>fruit tree in {N['sapling']} days",
              f"a sapling: a hardwood tree<br/>in {N['sapling']} days",
              f"flower seeds: flowers in bloom in<br/>{N['bloom']} days, where bugs come"],
             "no free spot near there", ""),
            ("place", 'put the log stool outside (you carry 1); add "near": a place',
             ["furniture or fencing that<br/>can go outside; one line each"], [f"near a place you choose<br/>that {WALK}"], [],
             [], ["the pieces you put out<br/>(add a count for more than one)"],
             ["they stand outside near there,<br/>blocking the way; only you<br/>can pick them up"],
             "no free space near there", ""),
            ("fence_off", "fence off around your home with simple wooden fence (you carry 5; one piece per tile)",
             ["fencing<br/>one line per kind"], ["around your home"], ["you have a home"],
             [], ["1 piece per tile fenced<br/>(as far as your pieces go)"],
             ["a fence round your home plot,<br/>the door left open"],
             "nothing left to fence there", ""),
            ("pick_up", "pick up log stool you put outside (lower ground, centre) (1 out)", [],
             ["where you put it"], [ROOM, "you put that thing outside<br/>one line per kind of thing"],
             ["the pieces, back in your pockets<br/>(add a count for more than one)"], [],
             ["the tiles are free again"], "pockets full", ""),
            ("lay_path", 'lay a path; add "style", "from" and "to"', [],
             [f"from one place to another,<br/>both on your side of the river<br/>and {WALK}"], ["at least two such places"],
             [], [], [f"up to {N['path']} tiles of path along<br/>the way (dirt, stone, brick, sand<br/>or terracotta); "
                      "do it again<br/>to go on", "walking on paths is no faster"],
             "that stretch is already paved", ""),
            ("remove_path", "take up path tiles you laid around lower ground, centre", [],
             ["a part of the island<br/>one line per part"], ["you laid path tiles there"],
             [], [], ["your path tiles near there<br/>are taken up"], "they were taken up already", ""),
        ]),
        ("At home, and clothes", [
            ("craft", "craft a log stool at home", ["what the recipe needs<br/>(in pockets or kept at home)"],
             [f"your home, {WALK}"], ["you know the recipe<br/>one line per recipe", "room for what it makes"],
             [f"what it makes: 1, except<br/>{fence}; a tool<br/>comes with {N['uses']} uses"],
             ["its materials, from your<br/>pockets first, then home"],
             ["what doesn't fit in your<br/>pockets stays at home"],
             "materials used or given away since; pockets full", ""),
            ("decorate", "put a log stool inside your home", ["the furniture<br/>one line per piece"],
             [f"your home, {WALK}"], ["room inside your house"],
             [], ["the piece, from your pockets"], ["it stands inside your home;<br/>visitors see it"],
             "it was given away or put outside since", ""),
            ("undecorate", "take the log stool out of your home", [],
             [f"your home, {WALK}"], [ROOM, "it is inside your home<br/>one line per piece"],
             ["the piece, back in your pockets"], [], [], "pockets full", ""),
            ("wear", "put on the straw hat", ["the clothing<br/>one line per piece"], ["wherever you are"], [],
             [], [], ["you wear it, and others see it",
                      "what you wore in that slot<br/>goes back to your pockets"],
             "it was given away since", ""),
        ]),
        ("Visiting", [
            ("visit", "visit Jo at their home (lower ground, west)", [],
             [f"the other villager's home<br/>{WALK}; one line per villager"], ["they have a home"],
             [], [],
             ["they're home, awake and free: they<br/>let you in, you see what's inside,<br/>"
              "and you can talk (gifts, requests,<br/>trades), free of any action",
              "nobody answers: they hear<br/>later that you came by"],
             "nothing", ""),
        ]),
        ("Shops and services", [
            ("ask", "ask Tom Nook what they do and what it costs (at Resident Services)", [],
             [f"Resident Services (Tom Nook, Isabelle),<br/>the museum (Blathers), Nook's Cranny<br/>(Timmy and Tommy) or the Able Sisters<br/>"
              f"{WALK}; one line per person"],
             [f"it's open during the plan<br/>Nook's Cranny {window(N['hours']['cranny'])},<br/>Able Sisters "
              f"{window(N['hours']['able'])},<br/>the others all day"],
             ["Tom Nook or Isabelle, when you<br/>carry no map and have room:<br/>an island map"], [],
             ["you hear what they do and what<br/>it costs (houses, moves, bridges<br/>and stairs; ordinances; the museum;<br/>prices; clothes)",
              "Timmy and Tommy: what they'd pay<br/>for what you carry joins the<br/>prices you've heard"],
             "nothing", ""),
            ("sell", 'sell at Nook\'s Cranny; add "items": what and how many', ["things Timmy and Tommy buy"],
             [f"Nook's Cranny, {WALK}"], [f"open during the plan<br/>{window(N['hours']['cranny'])}"],
             ["bells: each thing's Cranny price<br/>(more under Bell Boom)"], ["the things sold"],
             ["their prices join the<br/>prices you've heard"],
             "none of it is in your pockets any more", ""),
            ("buy (a thing)", 'buy fishing bait at Nook\'s Cranny, 300 bells each; add "count"', ["bells for one<br/>(in your wallet)"],
             [f"Nook's Cranny, {WALK}"], [f"open during the plan<br/>{window(N['hours']['cranny'])}",
                                         "it's in today's stock<br/>one line per thing", ROOM],
             ["the thing; up to 10 at once,<br/>as many as you can pay<br/>for and carry"], ["its price in bells, each"], [],
             "bells spent since; pockets full", ""),
            ("buy (a tool)", f"buy a new shovel at Nook's Cranny, {tool_eg} bells each", ["bells for one<br/>(in your wallet)"],
             [f"Nook's Cranny, {WALK}"], [f"open during the plan<br/>{window(N['hours']['cranny'])}",
                                         "one line per tool<br/>(always in stock)", ROOM],
             [f"a new tool with {N['uses']} uses"], ["its price in bells, each"], [],
             "bells spent since; pockets full", ""),
            ("buy (a recipe card)", "buy the shovel recipe card at Nook's Cranny, 200 bells", ["bells for it<br/>(in your wallet)"],
             [f"Nook's Cranny, {WALK}"], [f"open during the plan<br/>{window(N['hours']['cranny'])}",
                                         "it's for sale today<br/>(tool cards always, plus one)",
                                         "room in your pockets,<br/>only if you already know it"],
             ["a card for a recipe you<br/>already know: the card"], ["its price in bells"],
             ["you learn the recipe, and<br/>what it needs"], "bells spent since", ""),
            ("buy (clothes)", "buy the kimono at the Able Sisters, 4800 bells", ["bells for it<br/>(in your wallet)"],
             [f"the Able Sisters, {WALK}"], [f"open during the plan<br/>{window(N['hours']['able'])}",
                                             "it's in today's stock<br/>one line per piece", ROOM],
             ["the piece of clothing"], ["its price in bells"], [], "bells spent since; pockets full", ""),
            ("assess", "have Blathers assess your fossils (1) at the museum", ["an unassessed fossil"],
             [f"the museum, {WALK}"], ["open (all day)"],
             [f"each becomes 1 of {len(N['fossils'])} fossils,<br/>equal chances (table below)"],
             ["every unassessed fossil<br/>you carry"],
             [f"now the museum takes them, and<br/>the Cranny pays {n(fossil_lo)} to {n(fossil_hi)}"],
             "the fossils are gone from your pockets", "FOSSILS"),
            ("donate", "donate a sea bass at the museum", ["a fish, bug or assessed fossil<br/>one line per kind"],
             [f"the museum, {WALK}"], ["open (all day)", "the museum doesn't have one yet"],
             ["no bells"], ["the one you donate"],
             ["the museum has it, with your<br/>name, for everyone to see", "everyone hears of it<br/>in Isabelle's news"],
             "someone donated one first; it's gone from your pockets", ""),
            ("upgrade_house", "have Tom Nook upgrade your tent at Resident Services", [],
             [f"Resident Services, {WALK}"], ["open (all day)", "no loan owed", "a bigger house to go to"],
             [], [], ["the next house, right away:<br/>more storage and room inside<br/>(table below)",
                      "a loan for its cost<br/>(no interest, no deadline);<br/>paid off before the next one"],
             "nothing", "HOUSES"),
            ("pay_loan", 'pay Tom Nook toward your loan (you owe 98000) at Resident Services; add "bells"',
             ["bells<br/>(in your wallet)"], [f"Resident Services, {WALK}"], ["open (all day)", "a loan owed"],
             [], ["the bells you pay<br/>(up to what you owe)"], ["the loan goes down; at 0<br/>you can upgrade again"],
             "the wallet is empty by then", ""),
            ("contribute_bridge", "put bells toward the stairs up to the middle cliff (north) (0/98000 raised, Ari's proposal) "
                                  'at Resident Services; add "bells"',
             ["bells<br/>(in your wallet)"], [f"Resident Services, {WALK}"],
             ["open (all day)", "that bridge or stairs isn't built yet<br/>one line per fund: the first bridge<br/>and each proposal (version 17)"],
             [], ["the bells you put in<br/>(up to what the fund still needs)"],
             [f"that fund goes up; at {n(st['cost'])}<br/>it's built at once: a crossing,<br/>or stairs",
              "everyone hears of a finished one<br/>in Isabelle's news"],
             "the wallet is empty by then; someone else finished the fund first", ""),
            ("set_ordinance", f"pay Isabelle {N['ord_cost']} bells to set the bell boom ordinance from tomorrow at Resident Services",
             [f"{n(N['ord_cost'])} bells<br/>(in your wallet)"], [f"Resident Services, {WALK}"],
             ["open (all day)", "that ordinance isn't in effect<br/>one line per ordinance"],
             [], [f"{n(N['ord_cost'])} bells"],
             ["from tomorrow morning, for<br/>everyone (table below)", "everyone hears of it<br/>in Isabelle's news"],
             "bells spent since; someone set it first", "ORDINANCES"),
            ("move_house", f"pay Tom Nook {N['move']} bells to move your home to site-3 (lower ground, east) overnight at Resident Services",
             [f"{n(N['move'])} bells<br/>(in your wallet)"], [f"Resident Services, {WALK}"],
             ["open (all day)", "a free homesite<br/>one line per site", "no move of yours pending"],
             [], [f"{n(N['move'])} bells"],
             ["overnight, your home moves there<br/>with everything in it",
              "everyone hears of it<br/>in Isabelle's news"],
             "bells spent since; someone took the site first", ""),
        ]),
        ("Resting, getting home, the plaza", [
            ("rest", "rest", [], ["your home, or where you are<br/>if you can't get home"], [],
             [], [], ["nothing: the hour passes"], "nothing", ""),
            ("call_rescue", "call the Rescue Service to take you home (once a day)", [],
             ["where you're stuck"], ["you can't get home with<br/>the ladder and pole you carry", "it hasn't taken you home yet today"],
             [], [], ["you're home now, with a talking-to<br/>about why you got stuck",
                      "it won't come again today"],
             "you can get home by then after all", ""),
            ("plaza", "spend the hour at the plaza: the bulletin board, letters and parcels (not one of your actions)", [],
             [f"the plaza, {WALK}"], ["takes the hour, but isn't<br/>one of the day's actions"],
             [], [], ["one turn there: read and post on<br/>the board, take your posts down,<br/>"
                      "send letters and parcels<br/>(they arrive tomorrow)"],
             "nothing", ""),
        ]),
    ]


STYLE = """    classDef act fill:#e3f4e8,stroke:#2e8b57,color:#0b0b0b
    classDef item fill:#e3edfb,stroke:#3d7bd9,color:#0b0b0b
    classDef place fill:#fde9df,stroke:#d9623d,color:#0b0b0b
    classDef cond fill:#fdf3d6,stroke:#eda100,color:#0b0b0b
    classDef gain fill:#dff3f1,stroke:#1a8a7d,color:#0b0b0b
    classDef spend fill:#fbe3e8,stroke:#c0396b,color:#0b0b0b
    classDef change fill:#eef0f2,stroke:#6b7280,color:#0b0b0b"""


def graph(name, pockets, where, only, gives, uses, changes) -> str:
    """The action in the centre: what it takes on the left (arrows into it), what comes of it on the right."""
    lines = ["```mermaid", "flowchart LR", STYLE, f'    A(["{name}"])']
    ids: dict[str, list] = {k: [] for k in ("item", "place", "cond", "gain", "spend", "change")}
    for cls, label, xs, shape in (("item", "needs in pockets", pockets or ["nothing"], '["{}"]'),
                                  ("place", "done at", where, '{{{{"{}"}}}}'),
                                  ("cond", "only if", only, '("{}")'),
                                  ("gain", "gives", gives, '["{}"]'),
                                  ("spend", "uses up", uses, '["{}"]'),
                                  ("change", "changes", changes, '("{}")')):
        for i, x in enumerate(xs):
            nid = f"{cls[0].upper()}{cls[1]}{i}"
            node = nid + shape.format(x)
            if cls in ("item", "place", "cond"):  # what it takes, to the left
                lines.append(f'    {node} -- "{label}" --> A')
            else:  # what comes of it, to the right
                lines.append(f'    A -- "{label}" --> {node}')
            ids[cls].append(nid)
    lines.append("    class A act")
    lines += [f"    class {','.join(v)} {k}" for k, v in ids.items() if v]
    lines.append("```")
    return "\n".join(lines)


def species_tables(w: dict, kind: str) -> str:
    """Fish by water, or bugs by habitat: each one's weight and its chance when everything there is out."""
    if kind == "FISH":
        groups = [("the sea", ("sea",)), ("the river", ("river",)), ("the pond", ("pond",)),
                  ("the river up on the cliffs", ("river", "clifftop"))]
        table, key = w["fish"], "where"
        head = "| fish | weight | chance if all are out | shadow | hours | months | weather |"
    else:
        groups = [(f"on {h}" if h != "ground" else "in the grass", (h,)) for h in ("flowers", "trees", "stumps", "rocks", "ground")]
        table, key = w["bugs"], "habitat"
        head = "| bug | weight | chance if all are out | hours | months | weather |"
    out = []
    for label, wheres in groups:
        rows = {k: v for k, v in table.items() if v.get(key, "ground") in wheres}
        total = sum(v["weight"] for v in rows.values())
        out += [f"**{label[0].upper()}{label[1:]}**", "", head, "|" + "---|" * (head.count("|") - 1)]
        for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["weight"]):
            cols = [k, str(v["weight"]), pct(v["weight"] / total)] + ([str(v.get("shadow", ""))] if kind == "FISH" else [])
            cols += [window(v["hours"]), months(v), v.get("weather", "any")]
            out.append("| " + " | ".join(cols) + " |")
        out.append("")
    note = ("Which fish are out changes hour by hour: only those whose hours, months and weather fit, and a fish "
            "you see as a shadow is drawn by weight from those. The chances above are with every one of them out; "
            "with fewer out, the rest share the weight." if kind == "FISH" else
            "Only bugs whose hours, months and weather fit are out; the chances above are with all of them out.")
    return "\n".join([note, ""] + out)


def extra(w: dict, N: dict, key: str) -> str:
    if key in ("FISH", "BUGS"):
        return species_tables(w, key)
    if key == "SHELLS":
        total = sum(v[1] for v in w["shells"].values())
        rows = [f"| {k} | {v[1]} | {pct(v[1] / total)} |" for k, v in sorted(w["shells"].items(), key=lambda kv: -kv[1][1])]
        return "\n".join(["| shell | weight | chance each |", "|---|---|---|"] + rows)
    if key == "FOSSILS":
        return ("Each of these equally likely (1 in %d): " % len(N["fossils"])) + ", ".join(sorted(N["fossils"])) + "."
    if key == "HOUSES":
        rows = [f"| {lv['name']} | {n(lv['cost'])} | {lv['storage']} | {lv.get('interior', '')} |" for lv in N["levels"]]
        return "\n".join(["| house | loan (bells) | storage at home | furniture inside |", "|---|---|---|---|"] + rows)
    if key == "ORDINANCES":
        types = w["ordinances"]["types"]
        rows = [f"| {o.replace('_', ' ')} | {ORDINANCE_TEXT.get(o, o).split(': ', 1)[-1]} | "
                + (", ".join(f"{k.replace('_', ' ')} {v:+}" if isinstance(v, int) else f"{k.replace('_', ' ')} x{v}"
                             for k, v in spec.items()) or "") + " |" for o, spec in types.items()]
        return "\n".join(["| ordinance | what it does | in the world file |", "|---|---|---|"] + rows)
    return ""


HEAD = """# Every action, what it takes and what comes of it

Prompt version 16 on. Each plan lists, numbered, the actions a villager can do then; this file shows,
for every action in the game, what puts it on that list and what comes of doing it. How plans fit
into a day and an hour is in [day_flow.md](day_flow.md); the code is `options.py` (the list) and
`world.py` (what happens). Generated by `scripts/actions_doc.py` from `data/island_world.yaml`;
rerun it after changing the world file or the rules.

Each action is a small graph with the action in the centre, what it takes on the left and what comes
of it on the right. What it takes, on the left:
- **green:** the action
- **blue, "needs in pockets":** what has to be in their pockets when they plan
- **orange, "done at":** where it happens; always somewhere they can walk to with the ladder and
  vaulting pole they carry, from where they are (the way is the same in both directions, so this
  holds wherever the plan takes them)
- **yellow, "only if":** anything else that has to be true when they plan

What comes of it, on the right:
- **teal, "gives":** what they get, with its chance
- **pink, "uses up":** what it costs them
- **grey, "changes":** what changes for them or on the island, recipes learned included

If anything it takes is missing, the action isn't on that plan's list; it shows up at a later plan
once everything is there. "One line per ..." means the list has a separate numbered line for each
one (each rock, each villager, each thing in stock). Above each graph is an example of the line the
villager sees, and below it what can still make it fail when its hour comes: something that changed
after the plan was made. Any action away from home can also fail if a ladder or vaulting pole broke
on an earlier trip and the place is now out of reach. Anything that gives things gives only what
fits in their pockets.

**The numbers are this world file's.** In runs, every species has an invented name ("selit fish"),
prices are jittered, and fish move to another water and other hours; the chances (rock yields, the
wood mix, shell, fish and bug weights, the bottle and the catch) stay as here. A tool wears by one use
each time an action uses it, and breaks at {uses} uses.

The list is in the order of this file. The 40 entries are 35 kinds of action (`fish` has a second
entry for fishing with bait, and `buy` has four: things, tools, recipe cards and clothes) plus the
plaza, which takes the hour but isn't one of the day's actions. Proposing bridges and stairs, and a
fund for each, are from prompt version 17; everything else from 16.

One action can fail on where the villager is rather than on the plan: a proposal is made where they
stand at that hour, so it's on the list whenever a workable spot exists somewhere they can walk to,
and fails if there's none within {near} steps of where the plan has taken them by then (saying where
the nearest one is). From the moment a bridge or stairs is proposed, its whole footprint is a
construction site: walkable, but nothing can be planted or put on it, and no tree or rock turns up
there; that's why plant and place can't use those tiles.
"""

ONCE = '''## Once picked (the same for every action)

```mermaid
flowchart TD
    classDef llm fill:#e9e3fb,stroke:#6250d6,color:#1b1240
    classDef file fill:#f1f0ec,stroke:#8a8984,color:#0b0b0b
    classDef gate fill:#fdf3d6,stroke:#eda100,color:#0b0b0b

    plan(["LLM: plan<br/>picks numbers for hours, adding a count,<br/>bells or a place where an option takes one"])
    readable{"Number on the list,<br/>value allowed?"}
    told["Dropped; their day says<br/>'in your plan, there is no 7 on your list,<br/>so it wasn't planned'"]
    hourq{"A free hour? awake, actions left<br/>today and in this plan; a shop or<br/>service pick goes in an hour it's open"}
    dropped["Dropped, with why"]
    sched["Scheduled"]
    pl[("events.jsonl: plan")]
    spot{"Went after a bug or fish<br/>spotted the hour before?<br/>version 14"}
    swapped["That catch takes the hour"]
    run["That hour: walk there, do it<br/>see chart 3 in day_flow.md;<br/>fails only as in the charts above"]
    aev[("events.jsonl: action")]

    plan --> readable
    readable -- yes --> hourq
    readable -- no --> told --> pl
    hourq -- yes --> sched --> pl
    hourq -- no --> dropped --> pl
    pl --> spot
    spot -- no --> run --> aev
    spot -- yes --> swapped

    class plan llm
    class pl,aev file
    class readable,hourq,spot gate
```
'''


def main() -> None:
    w = load_world(ROOT / "data/island_world.yaml")
    N = world_numbers(w)
    out = [HEAD.format(uses=N["uses"], near=N["st"]["near"])]
    k = 0
    for title, actions in entries(N):
        out.append(f"## {title}\n")
        for name, line, pockets, where, only, gives, uses, changes, fails, more in actions:
            k += 1
            out.append(f"### {k}. `{name}`\n")
            out.append(f"On the list: *{line}*\n")
            out.append(graph(name, pockets, where, only, gives, uses, changes) + "\n")
            if more:
                out.append(extra(w, N, more) + "\n")
            if fails == "nothing":
                out.append("Can't fail when its hour comes.\n")
            elif fails.startswith("nothing: "):
                out.append(f"Can't fail when its hour comes: {fails[len('nothing: '):]}.\n")
            else:
                out.append(f"Can still fail when its hour comes: {fails}.\n")
    out.append(ONCE)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    (ROOT / "docs/actions.md").write_text(text)
    print(f"wrote docs/actions.md: {k} actions, {text.count('```mermaid')} charts")


if __name__ == "__main__":
    main()
