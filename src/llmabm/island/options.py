"""Prompt version 16: what a villager can do, as a numbered list built for them each time they plan.

Before, a plan wrote its actions free-form ({"do": "fish", "where": "river", "bait": true}) and the island
read them as best it could. Many failed on form (visiting Nook's Cranny, "walk", "post", asking a villager)
or on something the villager didn't have yet (bait, an apple, bells, a way across the river). From version 16
each plan lists only the actions this villager can do right then, each spelled out in full, and the reply
picks them by number. What isn't on the list can't be planned.

"Right then" is when the plan is made: what they carry, their bells, what they keep at home (crafting
happens at home), where they can walk with the tools they carry, and the opening hours within the plan's
hours. Something an earlier pick in the same plan would bring (buying bait, then fishing with it) shows up
in their next plan. The list is in a fixed order and says nothing about what is worth doing.

A few options take a value the reply adds (how many to buy, how many bells, a place). Those values are
checked against what the option allows too; a pick that can't be read is dropped and the villager is told.

Version 21 keeps the same list of what can be done and changes how a plan names it: not by number but by the
action and what it takes, in the three parts each chart in docs/actions.md has. {"action": "sell", "items":
{"wood": 3}, "at": "Nook's Cranny"}: "items" is what it uses from the pockets, "at" where it is done, "what"
which one of several (a recipe, something in stock, a fund). The plan shows one line per kind of action with
the choices for each part, headed by what is in the pockets, and a pick naming something that isn't in the
pockets isn't planned. A part with only one possibility can be left out. Before, the one option that needed
"items" (sell) was never written with them, so nobody ever sold anything.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .npc import ROSTER
from .world import AXES, BAIT, FOSSIL, _a, category_of, clock, item_info, resolve_item, structures_cfg

AREAS = ["northwest", "north", "northeast", "west", "centre", "east", "southwest", "south", "southeast"]  # as in region()


# --- what a reply can add to an option ------------------------------------------------------------------
@dataclass
class Count:
    """A whole number from lo to hi; `default` when left out (None: it has to be given)."""
    lo: int
    hi: int
    default: int | None = None

    def hint(self, name: str) -> str:
        span = str(self.lo) if self.lo == self.hi else f"{self.lo} to {self.hi}"
        return f'"{name}": {span}' + (f" (or leave it out for {self.default})" if self.default is not None else "")

    def read(self, name: str, value):
        if value is None:
            return (self.default, None) if self.default is not None else (None, f'it needs "{name}": {self.lo} to {self.hi}')
        try:
            n = int(float(str(value).replace(",", "").strip()))
        except ValueError:
            return None, f'"{name}" should be a number from {self.lo} to {self.hi}'
        return max(self.lo, min(self.hi, n)), None


@dataclass
class Choice:
    """One of a fixed set of words. `label` names the set in the hint when it is long (the places)."""
    values: list[str]
    default: str | None = None
    label: str | None = None

    def hint(self, name: str) -> str:
        what = self.label or " | ".join(self.values)
        return f'"{name}": {what}' + (f" (or leave it out for {self.default})" if self.default is not None else "")

    def read(self, name: str, value):
        if value is None or str(value).strip() == "":
            return (self.default, None) if self.default is not None else (None, f'it needs "{name}"')
        want = str(value).strip().lower()
        want = want[4:] if want.startswith("the ") else want
        for v in self.values:
            if v.lower() == want:
                return v, None
        return None, f'"{value}" isn\'t one of the choices for "{name}"'


@dataclass
class Items:
    """Things from the pockets and how many of each: {"<thing>": <how many>}, each at most what is carried."""
    have: dict[str, int]
    optional: bool = False             # version 21's send: a letter has no items

    def hint(self, name: str) -> str:
        return f'"{name}": {{"<thing>": <how many>}} from ' + ", ".join(f"{k} x{n}" for k, n in self.have.items())

    def read(self, name: str, value):
        if not isinstance(value, dict) or not value:
            return None, f'it needs "{name}": {{"<thing>": <how many>}}'
        out, unknown = {}, []
        for k, n in value.items():
            k = resolve_item(k, self.have)
            if k not in self.have:
                unknown.append(str(k))
                continue
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = self.have[k]
            if n > 0:
                out[k] = min(n, self.have[k])
        if not out:
            return None, (f"{' and '.join(unknown)} isn't something you can do that with" if unknown
                          else f'"{name}" named nothing')
        return out, None


@dataclass
class Text:
    """Something written, cut at `most` characters; nothing when left out."""
    most: int

    def hint(self, name: str) -> str:
        return f'"{name}": what you write (up to {self.most} characters)'

    def read(self, name: str, value):
        return str(value or "").strip()[: self.most], None


@dataclass
class Option:
    text: str                          # the line in the list, without its number
    action: dict                       # the action it stands for
    hours: frozenset | None = None     # hours of the plan it can be done in (opening hours); None: any of them
    fields: dict = field(default_factory=dict)  # what the reply can add: name -> Count | Choice | Items
    # Version 21, the parts of a pick as in docs/actions.md: what it uses from the pockets ("items"), where it
    # is done ("at") and which one of several ("what"), with what the menu says about each.
    uses: dict = field(default_factory=dict)
    at: str | None = None
    what: str | None = None
    at_note: str = ""
    what_note: str = ""
    tail: str = ""
    aka: tuple = ()                    # other words for `at` ("Bo" for "Bo's home")

    @property
    def kind(self) -> str:
        return self.action["do"]

    def line(self) -> str:
        if not self.fields:
            return self.text
        return self.text + "; add " + ", ".join(spec.hint(k) for k, spec in self.fields.items())


# --- where a villager can get to ------------------------------------------------------------------------
class _Reach:
    """Where a villager can walk from where they are, with the tools they carry (the grid's distances).
    The way across the island is the same in both directions, so this holds for the whole plan, unless a
    ladder or pole breaks on the way. Without a grid, everywhere counts as reachable."""

    def __init__(self, space, start, tools):
        self.space = space
        grid = start is not None and hasattr(space, "distances")
        self.dist = space.distances(tuple(start), frozenset(tools)) if grid else None

    def at(self, cell) -> bool:
        """The cell itself (doors: where the body walks to for buildings and homes)."""
        return self.dist is None or (cell is not None and tuple(cell) in self.dist)

    def near(self, cells) -> bool:
        """Somewhere to stand next to any of `cells` (rocks, trees, water, bugs' spots)."""
        if self.dist is None:
            return True
        return any(a in self.dist for c in cells if c for a in self.space.approach_cells(tuple(c)))

    def terrain(self, kind) -> bool:
        if self.dist is None:
            return True
        t = self.space.terrain
        return any(t[y][x] == kind for x, y in self.dist)


# --- the list ---------------------------------------------------------------------------------------------
def plan_options(isl, bodies, space, name: str, hours: list[int]) -> list[Option]:
    """Everything `name` can do in the hours of this plan, in a fixed order: out on the island, at home,
    visiting, at the shops and services, then rest and the plaza."""
    from .grid import GRASS, SAND

    vil, w = isl.v[name], isl.w
    inv = Counter({k: n for k, n in vil.inventory.items() if n > 0})
    room = isl.space(vil)
    fruit = w["island"]["native_fruit"]
    body = bodies.b[name]
    home_cell = bodies.home_point(name)
    start = body.pos if body.pos is not None else home_cell
    reach = _Reach(space, start, bodies.tools_of(name))
    has = lambda tool: inv[tool] > 0  # noqa: E731
    out: list[Option] = []
    add = out.append
    spot = lambda p: space.landmark(isl, name, p, start) if hasattr(space, "landmark") else None  # noqa: E731
    places = [p for p in place_names(isl, space, name) if reach.near([spot(p)])]
    near = Choice(places, "home", label="a place from the list below")

    # Out on the island: gathering needs room in the pockets.
    rock_cells = getattr(space, "rock_cells", None)
    rocks = [(i + 1, rock_cells[i] if rock_cells else None) for i in range(isl.n_rocks)]
    where = lambda c: f" ({space.region(tuple(c))})" if c and hasattr(space, "region") else ""  # noqa: E731
    if has("shovel") and room > 0:
        for i, c in rocks:
            if isl.rocks[i - 1] > 0 and (c or not rock_cells) and reach.near([c]):
                add(Option(f"mine rock {i}{where(c)}", {"do": "mine_rock", "rock": i},
                           uses={"shovel": 1}, at=f"rock {i}", at_note=where(c).strip(" ()")))
    if has("shovel") and inv[fruit] > 0:
        for i, c in rocks:
            if i - 1 not in isl.rock_back_on and (c or not rock_cells) and reach.near([c]):
                add(Option(f"break rock {i}{where(c)}, eating 1 {fruit} to be strong enough",
                           {"do": "break_rock", "rock": i}, uses={"shovel": 1, fruit: 1}, at=f"rock {i}",
                           at_note=where(c).strip(" ()"), tail=f"you eat the {fruit} first, to be strong enough"))
    axes = [t for t in AXES if has(t)]
    if axes and room > 0:
        for kind in ("hardwood", "fruit"):
            if not reach.near([t.pos for t in isl.trees if t.kind == kind and t.wood_left > 0 and t.pos]):
                continue
            for axe in axes:
                fells = (" (the axe brings the tree down when it takes its last wood"
                         + (", and it drops its fruit)" if kind == "fruit" else ")")) if axe == "axe" else ""
                add(Option(f"chop wood from a {kind} tree with the {axe}{fells}",
                           {"do": "chop_wood", "kind": kind, "axe": axe}, uses={axe: 1}, at=f"{kind} tree",
                           tail=("the axe (not the stone axe) brings the tree down when it takes its last wood, "
                                 "and a fruit tree then drops its fruit") if axe == "axe" else ""))
    if room > 0 and reach.near([t.pos for t in isl.trees if t.kind == "fruit" and t.fruit > 0 and t.pos]):
        add(Option("shake a fruit tree", {"do": "shake_tree"}, at="fruit tree"))
    if has("fishing rod") and room > 0:
        waters = (("sea", "the sea"), ("river", "the river"), ("pond", "the pond"),
                  ("clifftop", "the river up on the cliffs"))
        for key, label in waters:
            cells = space._habitat_cells(isl, "fish", key) if hasattr(space, "_habitat_cells") else [None]
            if cells and reach.near(cells):
                wat = {"at": label[4:], "aka": (key,)}
                add(Option(f"fish in {label}", {"do": "fish", "where": key}, uses={"fishing rod": 1}, **wat))
                if inv[BAIT] > 0:
                    add(Option(f"fish in {label} with fishing bait (uses 1; you carry {inv[BAIT]})",
                               {"do": "fish", "where": key, "bait": True},
                               uses={"fishing rod": 1, BAIT: 1}, **wat))
    if has("net") and room > 0:
        for hab, label in (("flowers", "among the flowers"), ("trees", "around the trees"),
                           ("stumps", "on the stumps"), ("rocks", "by the rocks"), ("ground", "in the grass")):
            cells = space._habitat_cells(isl, "bug", hab) if hasattr(space, "_habitat_cells") else [None]
            if cells and reach.near(cells):
                add(Option(f"catch bugs {label}", {"do": "catch_bug", "where": hab}, uses={"net": 1},
                           at=label.split()[-1], aka=(hab,)))
    if room > 0 and reach.terrain(SAND):
        add(Option("beachcomb", {"do": "beachcomb"}, at="beach"))
    if has("shovel") and room > 0 and isl.fossils_left > 0 and reach.terrain(GRASS):
        add(Option("dig for fossils", {"do": "dig_fossil"}, uses={"shovel": 1}, at="open ground"))
    if has("shovel") and room > 0 and getattr(isl, "clams_left", 0) > 0 and reach.terrain(SAND):
        add(Option("dig for manila clams on the beach", {"do": "dig_clam"}, uses={"shovel": 1}, at="beach"))
    if has("shovel") and reach.near([t.pos for t in isl.trees if t.kind == "stump" and t.pos]):
        add(Option("dig up a stump", {"do": "dig_stump"}, uses={"shovel": 1}, at="stump"))

    # Bridges and stairs, proposed where they stand (version 17).
    if getattr(isl, "prompt_version", 1) >= 17 and hasattr(space, "structure_spots"):
        cfg = structures_cfg(w)
        count = len(isl.structures())
        if isl.proposal_problem(name) is None:
            for kind, what in (("bridge", "a bridge across the river"), ("stairs", "stairs up a cliff")):
                if any(reach.near(sp["ends"]) for sp in space.structure_spots(isl, kind)):
                    add(Option(f"propose {what}, at the nearest spot within {cfg['near']} steps of where you are then "
                               f"(built once {cfg['cost']} bells are donated at Resident Services; {count} of "
                               f"{cfg['max']} on the island so far)", {"do": f"propose_{kind}"},
                               tail=f"{what}, at the nearest spot within {cfg['near']} steps of where you are then; "
                                    f"built once {cfg['cost']} bells are donated at Resident Services; {count} of "
                                    f"{cfg['max']} on the island so far"))

    # Things that go into the ground or outside.
    for k in sorted(inv):
        if k in (fruit, "sapling") or item_info(w, k)["kind"] == "seeds":
            add(Option(f"plant {_one(k)} (you carry {inv[k]})", {"do": "plant", "item": k}, fields={"near": near},
                       uses={k: 1}))
    for k in sorted(inv):
        info = item_info(w, k)
        if info["kind"] in ("furniture", "fence") and info["outdoor"]:
            add(Option(f"put {k} outside (you carry {inv[k]})", {"do": "place", "item": k},
                       fields={"near": near, **({"count": Count(1, inv[k], 1)} if inv[k] > 1 else {})},
                       uses={k: 1}))
            if info["kind"] == "fence" and home_cell is not None:
                add(Option(f"fence off around your home with {k} (you carry {inv[k]}; one piece per tile)",
                           {"do": "fence_off", "item": k, "around": "home"}, uses={k: 1}, at="home",
                           tail="one piece per tile, as far as your pieces go"))
    if room > 0:
        for k, cells in _placed_by(space, name).items():
            add(Option(f"pick up {k} you put outside{where(cells[0])} ({len(cells)} out)",
                       {"do": "pick_up", "item": k, "near": list(cells[0])},
                       fields={"count": Count(1, len(cells), 1)} if len(cells) > 1 else {},
                       what=k, what_note=", ".join(x for x in (f"{len(cells)} out", where(cells[0]).strip(" ()")) if x)))
    styles = list((w.get("building") or {}).get("path_styles") or [])
    if styles and hasattr(space, "paths"):
        # A path is laid along a walk, and a walk for a path doesn't jump the river: both ends on one side.
        walk = _Reach(space, home_cell or start, bodies.tools_of(name) - {"vaulting pole"})
        ends = [p for p in places if walk.near([spot(p)])]
        side = "a place from the list below" + (" on your side of the river" if len(ends) < len(places) else "")
        if len(ends) > 1:
            add(Option("lay a path", {"do": "lay_path"},
                       fields={"style": Choice(styles, styles[0]), "from": Choice(ends, ends[0], label=side),
                               "to": Choice(ends, label=side)}))
        laid: dict[str, tuple] = {}
        for c, p in sorted(space.paths.items()):
            if p["owner"] == name:
                laid.setdefault(space.region(c), c)
        for area, c in laid.items():
            add(Option(f"take up path tiles you laid around {area}", {"do": "remove_path", "near": list(c)},
                       at=area))

    # At home (crafting, furniture inside). Clothes go on wherever you are.
    at_home_ok = home_cell is not None and reach.at(home_cell)
    if at_home_ok:
        split = isl.split_home()
        for r in sorted(vil.recipes):
            if _can_craft(isl, vil, r, split):
                add(Option(f"craft {_one(r)} at home", {"do": "craft", "recipe": r}, at="home", what=r))
        if len(vil.interior) < isl.interior_slots(vil):
            for k in sorted(inv):
                if item_info(w, k)["kind"] == "furniture":
                    add(Option(f"put {_one(k)} inside your home", {"do": "decorate", "item": k},
                               uses={k: 1}, at="home"))
        if room > 0:
            for k in sorted(set(vil.interior)):
                add(Option(f"take the {k} out of your home", {"do": "undecorate", "item": k},
                           at="home", what=k))
    for k in sorted(inv):
        if k in (w.get("clothing") or {}):
            add(Option(f"put on the {k}", {"do": "wear", "item": k}, uses={k: 1}))

    # Visiting another villager at home.
    for other in sorted(isl.v):
        site = isl.home.get(other)
        if other != name and site and reach.at(space.coords_of_home(site) if hasattr(space, "sites") else None):
            area = (isl.sites.get(site) or {}).get("area")
            add(Option(f"visit {other} at their home" + (f" ({area})" if area else ""), {"do": "visit", "who": other},
                       at=f"{other}'s home", at_note=area or "", aka=(other,)))

    # Sending someone a letter, things from the pockets, or both (version 21): it arrives the next morning.
    if getattr(isl, "prompt_version", 1) >= 21:
        from .prompts import LETTER_CHARS

        sendable = {**({"bells": vil.bells} if vil.bells > 0 else {}), **dict(sorted(inv.items()))}
        for other in sorted(isl.v):
            if other != name:
                add(Option(f"send {other} a letter or things from your pockets", {"do": "send", "to": other},
                           fields={**({"items": Items(sendable, optional=True)} if sendable else {}),
                                   "note": Text(LETTER_CHARS)},
                           at=other, aka=(f"{other}'s home",),
                           tail="a note, things, or both; it arrives tomorrow morning"))

    # Shops and services, in their opening hours.
    def open_in(place: str, building: str) -> frozenset | None:
        if not reach.at(space.coords_of_place(building) if hasattr(space, "buildings") else None):
            return None
        ok = frozenset(h for h in hours if isl.is_open(place, h))
        return ok or None

    for npc in ROSTER:
        if npc.listed and (ok := open_in(npc.place, npc.building)):
            add(Option(f"ask {_the(npc.name)} what they do and what it costs (at {_the(npc.building)})",
                       {"do": "ask", "who": npc.name}, ok, at=npc.name, at_note=npc.building, aka=(npc.building,)))

    if (ok := open_in("cranny", "Nook's Cranny")):
        at = " at Nook's Cranny"
        sellable = {k: n for k, n in sorted(inv.items()) if k in isl.prices}
        if sellable:
            add(Option("sell" + at, {"do": "sell", "shop": "cranny"}, ok, {"items": Items(sellable)},
                       at="Nook's Cranny"))
        f = isl.price_factor()
        for k, p in isl.cranny_stock.items():
            price = round(p * f)
            if price <= vil.bells and room > 0:
                add(Option(f"buy {k}{at}, {price} bells each", {"do": "buy", "shop": "cranny", "item": k}, ok,
                           (n := _how_many(vil.bells, price, room)), at="Nook's Cranny", what=k,
                           what_note=_price_note(price, n)))
        for k, price in isl.tools_for_sale().items():
            if price <= vil.bells and room > 0:
                add(Option(f"buy a new {k}{at}, {price} bells each", {"do": "buy", "shop": "cranny", "item": k}, ok,
                           (n := _how_many(vil.bells, price, room)), at="Nook's Cranny", what=k,
                           what_note="new, " + _price_note(price, n)))
        for r, price in isl.recipe_cards_for_sale().items():
            known = r in vil.recipes
            if price <= vil.bells and (not known or room > 0):
                add(Option(f"buy the {r} recipe card{at}, {price} bells" + (" (you know it)" if known else ""),
                           {"do": "buy", "shop": "cranny", "item": f"{r} recipe"}, ok, at="Nook's Cranny",
                           what=f"{r} recipe", what_note=f"{price} bells" + (", you know it" if known else "")))
    if (ok := open_in("able", "Able Sisters")) and room > 0:
        f = isl.price_factor()
        for k, p in isl.able_stock.items():
            price = round(p * f)
            if price <= vil.bells:
                add(Option(f"buy the {k} at the Able Sisters, {price} bells",
                           {"do": "buy", "shop": "able", "item": k}, ok, at="Able Sisters", what=k,
                           what_note=f"{price} bells"))

    if (ok := open_in("museum", "museum")):
        at = " at the museum"
        if inv[FOSSIL] > 0:
            add(Option(f"have Blathers assess your fossils ({inv[FOSSIL]}){at}", {"do": "assess"}, ok,
                       uses={FOSSIL: inv[FOSSIL]}, at="museum", tail="Blathers assesses every fossil you carry"))
        for k in sorted(inv):
            cat = category_of(w, k)
            if k != FOSSIL and cat and k not in isl.museum.get(cat, {}):
                add(Option(f"donate {_one(k)}{at}", {"do": "donate", "item": k}, ok, uses={k: 1}, at="museum"))

    if (ok := open_in("resident_services", "Resident Services")):
        at = " at Resident Services"
        levels = w["house_levels"]
        if vil.loan == 0 and vil.house + 1 < len(levels):
            add(Option(f"have Tom Nook upgrade your {levels[vil.house]['name']}{at}", {"do": "upgrade_house"}, ok,
                       at="Resident Services", tail=f"Tom Nook upgrades your {levels[vil.house]['name']}"))
        if vil.loan > 0 and vil.bells > 0:
            most = min(vil.bells, vil.loan)
            add(Option(f"pay Tom Nook toward your loan (you owe {vil.loan}){at}", {"do": "pay_loan"}, ok,
                       {"bells": Count(1, most, most)}, at="Resident Services", tail=f"you owe {vil.loan}"))
        if getattr(isl, "prompt_version", 1) >= 17:  # every bridge and stairs still being funded, each its own line
            cost = structures_cfg(w)["cost"]
            for st in isl.structures():
                if not st["built"] and vil.bells > 0:
                    whose = "" if st["by"] is None else (", your proposal" if st["by"] == name else f", {st['by']}'s proposal")
                    add(Option(f"put bells toward the {st['name']} ({st['fund']}/{cost} raised{whose}){at}",
                               {"do": "contribute_bridge", **({} if st["id"] == "bridge" else {"project": st["id"]})}, ok,
                               {"bells": Count(1, min(vil.bells, cost - st["fund"]))}, at="Resident Services",
                               what=st["name"], what_note=f"{st['fund']}/{cost} raised{whose}"))
        elif not isl.bridge_built and vil.bells > 0:
            add(Option(f"put bells toward the {w['bridge']['name']} ({isl.bridge_fund}/{w['bridge']['cost']} raised){at}",
                       {"do": "contribute_bridge"}, ok,
                       {"bells": Count(1, min(vil.bells, w["bridge"]["cost"] - isl.bridge_fund))},
                       at="Resident Services", what=w["bridge"]["name"]))
        cost = int((w.get("ordinances") or {}).get("cost", 0))
        if cost and vil.bells >= cost:
            for o in (w.get("ordinances") or {}).get("types", {}):
                if o != isl.ordinance or isl.pending_ordinance:
                    add(Option(f"pay Isabelle {cost} bells to set the {o.replace('_', ' ')} ordinance from tomorrow{at}",
                               {"do": "set_ordinance", "ordinance": o}, ok, at="Resident Services",
                               what=o.replace("_", " "), tail=f"you pay Isabelle {cost} bells; it holds from tomorrow"))
        move = int(w.get("move_house_cost", 10000))
        if vil.bells >= move and name not in isl.pending_moves:
            for s in isl.free_sites():
                if s not in isl.pending_moves.values():
                    add(Option(f"pay Tom Nook {move} bells to move your home to {s} ({isl.sites[s]['area']}) "
                               f"overnight{at}", {"do": "move_house", "to": s}, ok, at="Resident Services",
                               what=s, what_note=isl.sites[s]["area"],
                               tail=f"you pay Tom Nook {move} bells; your home moves overnight"))

    # Resting, getting home, the plaza.
    add(Option("rest" + ("" if at_home_ok or home_cell is None else " (where you are: you can't get home)"),
               {"do": "rest"}, tail="" if at_home_ok or home_cell is None else "where you are: you can't get home"))
    rescued = (getattr(isl, "rescued", None) or {}).get(name) == isl.day
    if getattr(isl, "prompt_version", 1) >= 15 and bodies.stuck(name) and not rescued:
        add(Option("call the Rescue Service to take you home (once a day)", {"do": "call_rescue"},
                   tail="the Rescue Service takes you home; once a day"))
    plaza = space.coords_of_place("plaza") if hasattr(space, "buildings") else None
    if reach.near([plaza]) if plaza is not None else True:
        add(Option("spend the hour at the plaza: the bulletin board, letters and parcels (not one of your actions)",
                   {"do": "plaza"},
                   tail="the bulletin board, letters and parcels; it takes the hour but isn't one of your actions"))
    return out


def place_names(isl, space, name: str) -> list[str]:
    """The places an option can be done near: home, other villagers' homes, the buildings and the nine parts
    of the island, each in words the island's landmarks understand."""
    homes = [f"{n}'s home" for n in sorted(isl.v) if n != name and isl.home.get(n)]
    buildings = list(getattr(space, "buildings", {}) or {})
    return ["home"] + homes + buildings + AREAS


def options_text(options: list[Option], isl, name: str, space) -> str:
    """The numbered list for a plan prompt, and the places when an option takes one."""
    lines = [f"{i}. {o.line()}" for i, o in enumerate(options, 1)]
    named = [s.values for o in options for s in o.fields.values() if isinstance(s, Choice) and s.label]
    if named:
        lines.append("Places: " + ", ".join(max(named, key=len)))
    return "\n".join(lines)


def read_picks(options: list[Option], entries: list) -> tuple[list[dict], list[dict]]:
    """A plan reply's actions as the actions they stand for. Each entry is {"hour": h, "pick": n} plus any
    values the option takes. Returns (actions, each with "hour" and its hours as "_open"; what couldn't be
    read, each with the pick and why)."""
    acts, bad = [], []
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = e.get("pick", e.get("option", e.get("number")))
        if raw is None and str(e.get("do", "")).strip().lower() == "plaza":
            raw = next((i for i, o in enumerate(options, 1) if o.action["do"] == "plaza"), None)
        try:
            n = int(str(raw).strip().lstrip("#").rstrip("."))
        except (TypeError, ValueError):
            bad.append({"pick": raw, "hour": e.get("hour"), "why": "it didn't say which number"})
            continue
        if not 1 <= n <= len(options):
            bad.append({"pick": n, "hour": e.get("hour"), "why": f"there is no {n} on your list"})
            continue
        opt = options[n - 1]
        act, why = dict(opt.action), None
        for k, spec in opt.fields.items():
            val, why = spec.read(k, e.get(k))
            if why:
                break
            act[k] = val
        if why:
            bad.append({"pick": n, "hour": e.get("hour"), "why": f"{n} ({opt.text}): {why}"})
            continue
        acts.append({**({"hour": e["hour"]} if "hour" in e else {}), **act,
                     **({"_open": opt.hours} if opt.hours is not None else {})})
    return acts, bad


# --- version 21: picks by name, shaped like the charts in docs/actions.md ------------------------------------
# Every kind of action, in the order of docs/actions.md, with the few words the menu adds where the name alone
# doesn't say what it is. scripts/actions_doc.py draws one chart for each (a test keeps the two the same).
KINDS = {
    "mine_rock": "", "break_rock": "", "chop_wood": "", "shake_tree": "", "fish": "", "catch_bug": "",
    "beachcomb": "", "dig_fossil": "", "dig_clam": "dig for manila clams", "dig_stump": "dig up a stump",
    "propose_bridge": "", "propose_stairs": "",
    "plant": "", "place": "put furniture or fencing outside", "fence_off": "fence off around your home",
    "pick_up": "something you put outside", "lay_path": "", "remove_path": "take up path tiles you laid",
    "craft": "", "decorate": "put furniture inside your home", "undecorate": "take furniture out of your home",
    "wear": "put on clothes",
    "visit": "", "send": "someone a letter or things from your pockets",
    "ask": "what they do and what it costs", "sell": "", "buy": "",
    "assess": "", "donate": "", "upgrade_house": "", "pay_loan": "",
    "contribute_bridge": "put bells toward a bridge or stairs", "set_ordinance": "", "move_house": "",
    "rest": "", "call_rescue": "", "plaza": "",
}
PICK_SHAPE = '{"action": "<action>", "items": {"<thing>": <how many>}, "at": "<where>", "what": "<which one>"}'


def _norm(x) -> str:
    """A name as written, for comparing: lower case, no "the" in front."""
    return re.sub(r"\s+", " ", re.sub(r"^(?:the|a|an|your|my)\s+", "", str(x).strip().lower())).strip()


def _words(x) -> set:
    return set(re.findall(r"[a-z0-9]+", _norm(x))) - {"s"}


def _which(given, names: dict[str, tuple]) -> str | None:
    """The one of `names` (each with its other ways of saying it, the line's own wording among them) that
    `given` means, or None when none or several fit. The same words win, as written and then without anything
    in brackets after them; then the only name with all the words given ("3" is rock 3, "Bo" is Bo's home)."""
    g, gw = _norm(given), _words(given)
    if not g:
        return None
    short = _norm(re.sub(r"\s*\(.*$", "", str(given)))
    for fits in (lambda n: _norm(n) == g, lambda n: _norm(n) == short, lambda n: bool(gw) and gw <= _words(n)):
        hits = [k for k, aka in names.items() if any(fits(n) for n in (k, *aka))]
        if hits:
            return hits[0] if len(hits) == 1 else None
    # More words than the name has ("shovel recipe card"): the name that takes up most of them.
    inside = sorted(((len(_words(k)), k) for k in names if _words(k) and _words(k) <= gw), reverse=True)
    if inside and (len(inside) == 1 or inside[0][0] > inside[1][0]):
        return inside[0][1]
    return None


def _groups(options: list[Option]) -> list[tuple[str, list[Option]]]:
    """The options kind by kind, in the list's order; a kind done at several places with a choice at each
    (buying, at Nook's Cranny and the Able Sisters) is one group per place."""
    by: dict[tuple, list[Option]] = {}
    split = {k for k in {o.kind for o in options}
             if any(o.what for o in options if o.kind == k) and len({o.at for o in options if o.kind == k}) > 1}
    for o in options:
        by.setdefault((o.kind, o.at if o.kind in split else None), []).append(o)
    return [(k, v) for (k, _), v in by.items()]


def _label(name: str, note: str) -> str:
    return f"{name} ({note})" if note and note != name else name


def _items_part(opts: list[Option], inv) -> str:
    for o in opts:
        if isinstance(o.fields.get("items"), Items):
            return "any of " + ", ".join(f"{k} x{n}" for k, n in o.fields["items"].have.items())
    bells = [o.fields["bells"] for o in opts if isinstance(o.fields.get("bells"), Count)]
    if bells:
        hi, d = max(b.hi for b in bells), bells[0].default
        return f"bells, 1 to {hi}" + (f" ({d} if left out)" if d is not None and len(bells) == 1 else "")
    if not any(o.uses for o in opts):
        return ""
    count = lambda k: f"{k} x{inv[k]}"  # noqa: E731
    common = [k for k in opts[0].uses if all(k in o.uses for o in opts)]
    rest = list(dict.fromkeys(tuple(k for k in o.uses if k not in common) for o in opts))
    fixed = " and ".join(k if opts[0].uses[k] == 1 else f"{k} x{opts[0].uses[k]}" for k in common)
    if rest == [()]:
        return fixed
    extra = ", ".join(count(k) for r in rest for k in r)
    if () in rest:  # the same action with or without something more (fishing bait)
        return f"{fixed}, and if you like {extra}"
    return (f"{fixed} and " if fixed else "") + f"one of {extra}"


def _menu_line(kind: str, opts: list[Option], inv) -> str:
    parts = [_label(kind, KINDS.get(kind, ""))]
    if (it := _items_part(opts, inv)):
        parts.append("items: " + it)
    ats = list(dict.fromkeys(_label(o.at, o.at_note) for o in opts if o.at))
    if any("near" in o.fields for o in opts):
        parts.append(f"at: a place from the list below ({opts[0].fields['near'].default} if left out)")
    elif ats:
        parts.append("at: " + " | ".join(ats))
    whats = list(dict.fromkeys(_label(o.what, o.what_note) for o in opts if o.what))
    if whats:
        several = any("count" in o.fields for o in opts)
        parts.append("what: " + " | ".join(whats) + (' (for more than one, {"<thing>": <how many>})' if several else ""))
    more = [spec.hint(k) for k, spec in opts[0].fields.items() if k not in ("items", "bells", "near", "count")]
    if more:
        parts.append("also " + ", ".join(more))
    parts += list(dict.fromkeys(o.tail for o in opts if o.tail))
    return "- " + ". ".join(parts)


def menu_text(options: list[Option], isl, name: str, space) -> str:
    """Version 21's list for a plan prompt: what is in the pockets, then one line for each kind of action the
    villager can do, with what it uses from the pockets, where, and which one; and the places."""
    vil = isl.v[name]
    inv = Counter({k: n for k, n in vil.inventory.items() if n > 0})
    carried = ", ".join(f"{k} x{n}" for k, n in sorted(inv.items())) or "nothing"
    lines = [f'In your pockets (the only things that can go in "items"): {carried}; {vil.bells} bells in your wallet']
    lines += [_menu_line(kind, opts, inv) for kind, opts in _groups(options)]
    named = [s.values for o in options for s in o.fields.values() if isinstance(s, Choice) and s.label]
    if named:
        lines.append("Places: " + ", ".join(max(named, key=len)))
    return "\n".join(lines)


def _as_items(x) -> dict:
    """What a reply wrote for "items" or "what", as {name: how many}."""
    if isinstance(x, dict):
        return {str(k): v for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return {str(k): 1 for k in x}
    return {str(x): None} if x not in (None, "") else {}


def _num(x, default=None):
    try:
        return int(float(str(x).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _read_one(by_kind: dict, pick: dict, pockets, home) -> tuple[Option | None, dict, str | None]:
    """One pick as (the option it means, the values for that option's fields, or why it can't be read)."""
    kind = re.sub(r"[\s-]+", "_", str(pick.get("action", pick.get("do")) or "").strip().lower())
    if kind not in by_kind:
        on = ", ".join(by_kind)
        if kind in KINDS:
            return None, {}, f"{kind} isn't on your list for this plan, which has: {on}"
        return None, {}, (f'there is no action called "{kind}"; your list has: {on}' if kind else
                          f"it didn't name an action; a pick is written as {PICK_SHAPE}")
    cands = by_kind[kind]
    items, what = _as_items(pick.get("items")), _as_items(pick.get("what"))
    at = pick.get("at") if pick.get("at") not in (None, "") else pick.get("near")
    if pick.get("bells") is not None:
        items.setdefault("bells", pick["bells"])
    whats = {o.what: (_label(o.what, o.what_note),) for o in cands if o.what}
    # Read as meant: the one thing named, in the other of "items" and "what".
    if not whats and what and not items:
        items, what = what, {}
    stuff = {k: v for k, v in items.items() if resolve_item(k, ["bells"]) != "bells"}
    if whats and not what and len(stuff) == 1 and _which(next(iter(stuff)), whats):
        what, items = stuff, {k: v for k, v in items.items() if k not in stuff}

    qty = _num(pick.get("count"))
    if whats:
        said = next(iter(what), None)
        w = _which(said, whats) if said is not None else (next(iter(whats)) if len(whats) == 1 else None)
        if w is None:
            choices = '"what": ' + " | ".join(whats)
            return None, {}, (f'"{said}" isn\'t one of {choices}' if said is not None else f"say which one, {choices}")
        cands = [o for o in cands if o.what == w]
        qty = _num(what.get(said), qty) if said is not None else qty

    ats = {o.at: (*o.aka, _label(o.at, o.at_note)) for o in cands if o.at}
    takes_place = any("near" in o.fields for o in cands)
    if not takes_place and len(ats) > 1:
        a = _which(at, ats) if at is not None else None
        if a is None:
            choices = '"at": ' + " | ".join(ats)
            return None, {}, (f'"{at}" isn\'t one of {choices}' if at is not None else f"say where, {choices}")
        cands = [o for o in cands if o.at == a]

    # Things named have to be in the pockets (crafting can also use what is kept at home).
    pool = Counter(pockets)
    if kind == "craft":
        pool.update(home or {})
    takes = list(dict.fromkeys(k for o in cands for k in (*o.uses, *(getattr(o.fields.get("items"), "have", None) or ()))))
    given: dict[str, int | None] = {}
    bells = None
    for k, n in items.items():
        r = resolve_item(k, [x for x in pool if pool[x] > 0] or [k])
        if r == "bells":
            bells = n
        elif pool[r] <= 0:
            return None, {}, (f"you have no {_norm(k)} in your pockets" +
                              (f"; for this you carry {', '.join(f'{x} x{pool[x]}' for x in takes)}" if takes else ""))
        else:
            given[r] = n
    if kind == "craft":  # its materials are the recipe's: nothing to choose
        given = {}
    if kind == "send" and bells is not None:  # bells can be sent like anything else
        given["bells"] = bells
    fits = [o for o in cands if set(given) <= set(o.uses) | set(getattr(o.fields.get("items"), "have", None) or ())]
    if not fits:
        odd = next(k for k in given if k not in takes)
        return None, {}, (f"it doesn't use {odd}" + (f"; it uses {', '.join(f'{x} x{pool[x]}' for x in takes)}"
                                                       if takes else ' (no "items")'))
    exact = [o for o in fits if set(o.uses) == set(given)]
    least = [o for o in fits if all(set(o.uses) <= set(x.uses) for x in fits)]
    if not (exact or least):
        return None, {}, 'say which, "items": one of ' + ", ".join(f"{k} x{pool[k]}" for k in takes)
    opt = (exact or least)[0]

    vals = {}
    for k, spec in opt.fields.items():
        if k == "items":
            if not given and spec.optional:
                continue
            if not given:
                return None, {}, ('say what and how many, "items": {"<thing>": <how many>} from '
                                  + ", ".join(f"{x} x{n}" for x, n in spec.have.items()))
            val, why = spec.read(k, given)
        elif k == "bells":
            val, why = spec.read(k, bells)
            if why:
                why = f'say how many bells, "items": {{"bells": <1 to {spec.hi}>}}'
        elif k == "count":
            val, why = spec.read(k, qty if qty is not None else next((n for n in given.values() if n is not None), None))
        elif k == "near":
            val, why = spec.read(k, at)
            why = why and why.replace('"near"', '"at"')
        else:
            val, why = spec.read(k, pick.get(k))
        if why:
            return None, {}, why
        vals[k] = val
    if kind == "send" and not (vals.get("items") or vals.get("note")):
        return None, {}, 'say what you send: "items" from your pockets, a "note", or both'
    return opt, vals, None


def read_actions(options: list[Option], entries: list, pockets, home=None) -> tuple[list[dict], list[dict]]:
    """Version 21: a plan reply's actions as the actions they stand for. Each entry is {"hour": h, "pick":
    {"action", "items", "at", "what"}}; `pockets` is what the villager carries (and `home` what they keep there,
    for crafting). Returns what `read_picks` does: the actions, and what couldn't be read with why."""
    by_kind: dict[str, list[Option]] = {}
    for o in options:
        by_kind.setdefault(o.kind, []).append(o)
    acts, bad = [], []
    for e in entries:
        if not isinstance(e, dict):
            continue
        pick = e.get("pick")
        if isinstance(pick, str) and not pick.strip().lstrip("#").rstrip(".").isdigit():
            pick = {**e, "action": pick}
        elif not isinstance(pick, dict):
            pick = e if ("action" in e or "do" in e) else None
        hour = e.get("hour", (pick or {}).get("hour"))
        if pick is None:
            bad.append({"pick": e.get("pick"), "hour": hour,
                        "why": f"a pick names its action, as {PICK_SHAPE}; there are no numbers"})
            continue
        opt, vals, why = _read_one(by_kind, pick, pockets, home)
        if why:
            name = re.sub(r"[\s-]+", "_", str(pick.get("action", pick.get("do")) or "").strip().lower())
            bad.append({"pick": {k: v for k, v in pick.items() if k not in ("hour", "pick")}, "hour": hour,
                        "why": f"{name}: {why}" if name in by_kind else why})
            continue
        acts.append({**({"hour": hour} if hour is not None else {}), **opt.action, **vals,
                     **({"_open": opt.hours} if opt.hours is not None else {})})
    return acts, bad


def unread_line(at: int, bad: dict) -> str:
    """What a villager is told about a pick that couldn't be read, in their day so far."""
    return f"{clock(at)} in your plan, {bad['why']}, so it wasn't planned"


# --- helpers ------------------------------------------------------------------------------------------------
def _one(thing: str) -> str:
    """"an apple", "a sapling", but "tulip seeds"."""
    return thing if thing.endswith("seeds") else _a(thing)


def _the(place: str) -> str:
    """"the museum", "the Able Sisters", but "Nook's Cranny" and "Tom Nook"."""
    return f"the {place}" if not place[:1].isupper() or place == "Able Sisters" else place


def _price_note(price: int, count: dict) -> str:
    """"300 bells each, up to 3" for something a plan can buy several of; "300 bells" for one."""
    return f"{price} bells each, up to {count['count'].hi}" if count else f"{price} bells"


def _how_many(bells: int, price: int, room: int) -> dict:
    most = min(10, bells // max(1, price), room)
    return {"count": Count(1, most, 1)} if most > 1 else {}


def _can_craft(isl, vil, r: str, split: bool) -> bool:
    """The recipe's materials from the pockets, and from home when things are kept there (crafting is at home),
    and room for what it makes, as the world's craft checks them."""
    rec = isl.w["recipes"].get(r)
    if not rec:
        return False
    needs = rec["needs"]
    have = lambda k: vil.inventory[k] + (vil.home_items[k] if split else 0)  # noqa: E731
    if any(have(k) < n for k, n in needs.items()):
        return False
    makes = int(rec.get("makes", 1))
    free = isl.space(vil) + sum(min(n, vil.inventory[k]) for k, n in needs.items())
    return free >= makes or (split and free + isl.home_space(vil) >= makes)


def _placed_by(space, name: str) -> dict[str, list]:
    """Things `name` put outside, by item: one cell per piece, so pick_up can go to the first."""
    out: dict[str, list] = {}
    seen = set()
    for c, o in sorted((getattr(space, "placed", None) or {}).items()):
        if o["owner"] == name and o["id"] not in seen:
            seen.add(o["id"])
            out.setdefault(o["item"], []).append(c)
    return out
