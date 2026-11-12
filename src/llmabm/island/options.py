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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .world import AXES, BAIT, FOSSIL, NPCS, _a, category_of, clock, item_info, resolve_item, structures_cfg

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
class Option:
    text: str                          # the line in the list, without its number
    action: dict                       # the action it stands for
    hours: frozenset | None = None     # hours of the plan it can be done in (opening hours); None: any of them
    fields: dict = field(default_factory=dict)  # what the reply can add: name -> Count | Choice | Items

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
                add(Option(f"mine rock {i}{where(c)}", {"do": "mine_rock", "rock": i}))
    if has("shovel") and inv[fruit] > 0:
        for i, c in rocks:
            if i - 1 not in isl.rock_back_on and (c or not rock_cells) and reach.near([c]):
                add(Option(f"break rock {i}{where(c)}, eating 1 {fruit} to be strong enough",
                           {"do": "break_rock", "rock": i}))
    axes = [t for t in AXES if has(t)]
    if axes and room > 0:
        for kind in ("hardwood", "fruit"):
            if not reach.near([t.pos for t in isl.trees if t.kind == kind and t.wood_left > 0 and t.pos]):
                continue
            for axe in axes:
                fells = (" (the axe brings the tree down when it takes its last wood"
                         + (", and it drops its fruit)" if kind == "fruit" else ")")) if axe == "axe" else ""
                add(Option(f"chop wood from a {kind} tree with the {axe}{fells}",
                           {"do": "chop_wood", "kind": kind, "axe": axe}))
    if room > 0 and reach.near([t.pos for t in isl.trees if t.kind == "fruit" and t.fruit > 0 and t.pos]):
        add(Option("shake a fruit tree", {"do": "shake_tree"}))
    if has("fishing rod") and room > 0:
        waters = (("sea", "the sea"), ("river", "the river"), ("pond", "the pond"),
                  ("clifftop", "the river up on the cliffs"))
        for key, label in waters:
            cells = space._habitat_cells(isl, "fish", key) if hasattr(space, "_habitat_cells") else [None]
            if cells and reach.near(cells):
                add(Option(f"fish in {label}", {"do": "fish", "where": key}))
                if inv[BAIT] > 0:
                    add(Option(f"fish in {label} with fishing bait (uses 1; you carry {inv[BAIT]})",
                               {"do": "fish", "where": key, "bait": True}))
    if has("net") and room > 0:
        for hab, label in (("flowers", "among the flowers"), ("trees", "around the trees"),
                           ("stumps", "on the stumps"), ("rocks", "by the rocks"), ("ground", "in the grass")):
            cells = space._habitat_cells(isl, "bug", hab) if hasattr(space, "_habitat_cells") else [None]
            if cells and reach.near(cells):
                add(Option(f"catch bugs {label}", {"do": "catch_bug", "where": hab}))
    if room > 0 and reach.terrain(SAND):
        add(Option("beachcomb", {"do": "beachcomb"}))
    if has("shovel") and room > 0 and isl.fossils_left > 0 and reach.terrain(GRASS):
        add(Option("dig for fossils", {"do": "dig_fossil"}))
    if has("shovel") and room > 0 and getattr(isl, "clams_left", 0) > 0 and reach.terrain(SAND):
        add(Option("dig for manila clams on the beach", {"do": "dig_clam"}))
    if has("shovel") and reach.near([t.pos for t in isl.trees if t.kind == "stump" and t.pos]):
        add(Option("dig up a stump", {"do": "dig_stump"}))

    # Bridges and stairs, proposed where they stand (version 17).
    if getattr(isl, "prompt_version", 1) >= 17 and hasattr(space, "structure_spots"):
        cfg = structures_cfg(w)
        count = len(isl.structures())
        if isl.proposal_problem(name) is None:
            for kind, what in (("bridge", "a bridge across the river"), ("stairs", "stairs up a cliff")):
                if any(reach.near(sp["ends"]) for sp in space.structure_spots(isl, kind)):
                    add(Option(f"propose {what}, at the nearest spot within {cfg['near']} steps of where you are then "
                               f"(built once {cfg['cost']} bells are donated at Resident Services; {count} of "
                               f"{cfg['max']} on the island so far)", {"do": f"propose_{kind}"}))

    # Things that go into the ground or outside.
    for k in sorted(inv):
        if k in (fruit, "sapling") or item_info(w, k)["kind"] == "seeds":
            add(Option(f"plant {_one(k)} (you carry {inv[k]})", {"do": "plant", "item": k}, fields={"near": near}))
    for k in sorted(inv):
        info = item_info(w, k)
        if info["kind"] in ("furniture", "fence") and info["outdoor"]:
            add(Option(f"put {k} outside (you carry {inv[k]})", {"do": "place", "item": k},
                       fields={"near": near, **({"count": Count(1, inv[k], 1)} if inv[k] > 1 else {})}))
            if info["kind"] == "fence" and home_cell is not None:
                add(Option(f"fence off around your home with {k} (you carry {inv[k]}; one piece per tile)",
                           {"do": "fence_off", "item": k, "around": "home"}))
    if room > 0:
        for k, cells in _placed_by(space, name).items():
            add(Option(f"pick up {k} you put outside{where(cells[0])} ({len(cells)} out)",
                       {"do": "pick_up", "item": k, "near": list(cells[0])},
                       fields={"count": Count(1, len(cells), 1)} if len(cells) > 1 else {}))
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
            add(Option(f"take up path tiles you laid around {area}", {"do": "remove_path", "near": list(c)}))

    # At home (crafting, furniture inside). Clothes go on wherever you are.
    at_home_ok = home_cell is not None and reach.at(home_cell)
    if at_home_ok:
        split = isl.split_home()
        for r in sorted(vil.recipes):
            if _can_craft(isl, vil, r, split):
                add(Option(f"craft {_one(r)} at home", {"do": "craft", "recipe": r}))
        if len(vil.interior) < isl.interior_slots(vil):
            for k in sorted(inv):
                if item_info(w, k)["kind"] == "furniture":
                    add(Option(f"put {_one(k)} inside your home", {"do": "decorate", "item": k}))
        if room > 0:
            for k in sorted(set(vil.interior)):
                add(Option(f"take the {k} out of your home", {"do": "undecorate", "item": k}))
    for k in sorted(inv):
        if k in (w.get("clothing") or {}):
            add(Option(f"put on the {k}", {"do": "wear", "item": k}))

    # Visiting another villager at home.
    for other in sorted(isl.v):
        site = isl.home.get(other)
        if other != name and site and reach.at(space.coords_of_home(site) if hasattr(space, "sites") else None):
            area = (isl.sites.get(site) or {}).get("area")
            add(Option(f"visit {other} at their home" + (f" ({area})" if area else ""), {"do": "visit", "who": other}))

    # Shops and services, in their opening hours.
    def open_in(place: str, building: str) -> frozenset | None:
        if not reach.at(space.coords_of_place(building) if hasattr(space, "buildings") else None):
            return None
        ok = frozenset(h for h in hours if isl.is_open(place, h))
        return ok or None

    for who in ("Tom Nook", "Isabelle", "Blathers", "Timmy and Tommy", "Able Sisters"):
        building, place = NPCS[who.lower()]
        if (ok := open_in(place, building)):
            add(Option(f"ask {_the(who)} what they do and what it costs (at {_the(building)})",
                       {"do": "ask", "who": who}, ok))

    if (ok := open_in("cranny", "Nook's Cranny")):
        at = " at Nook's Cranny"
        sellable = {k: n for k, n in sorted(inv.items()) if k in isl.prices}
        if sellable:
            add(Option("sell" + at, {"do": "sell", "shop": "cranny"}, ok, {"items": Items(sellable)}))
        f = isl.price_factor()
        for k, p in isl.cranny_stock.items():
            price = round(p * f)
            if price <= vil.bells and room > 0:
                add(Option(f"buy {k}{at}, {price} bells each", {"do": "buy", "shop": "cranny", "item": k}, ok,
                           _how_many(vil.bells, price, room)))
        for k, price in isl.tools_for_sale().items():
            if price <= vil.bells and room > 0:
                add(Option(f"buy a new {k}{at}, {price} bells each", {"do": "buy", "shop": "cranny", "item": k}, ok,
                           _how_many(vil.bells, price, room)))
        for r, price in isl.recipe_cards_for_sale().items():
            known = r in vil.recipes
            if price <= vil.bells and (not known or room > 0):
                add(Option(f"buy the {r} recipe card{at}, {price} bells" + (" (you know it)" if known else ""),
                           {"do": "buy", "shop": "cranny", "item": f"{r} recipe"}, ok))
    if (ok := open_in("able", "Able Sisters")) and room > 0:
        f = isl.price_factor()
        for k, p in isl.able_stock.items():
            price = round(p * f)
            if price <= vil.bells:
                add(Option(f"buy the {k} at the Able Sisters, {price} bells",
                           {"do": "buy", "shop": "able", "item": k}, ok))

    if (ok := open_in("museum", "museum")):
        at = " at the museum"
        if inv[FOSSIL] > 0:
            add(Option(f"have Blathers assess your fossils ({inv[FOSSIL]}){at}", {"do": "assess"}, ok))
        for k in sorted(inv):
            cat = category_of(w, k)
            if k != FOSSIL and cat and k not in isl.museum.get(cat, {}):
                add(Option(f"donate {_one(k)}{at}", {"do": "donate", "item": k}, ok))

    if (ok := open_in("resident_services", "Resident Services")):
        at = " at Resident Services"
        levels = w["house_levels"]
        if vil.loan == 0 and vil.house + 1 < len(levels):
            add(Option(f"have Tom Nook upgrade your {levels[vil.house]['name']}{at}", {"do": "upgrade_house"}, ok))
        if vil.loan > 0 and vil.bells > 0:
            most = min(vil.bells, vil.loan)
            add(Option(f"pay Tom Nook toward your loan (you owe {vil.loan}){at}", {"do": "pay_loan"}, ok,
                       {"bells": Count(1, most, most)}))
        if getattr(isl, "prompt_version", 1) >= 17:  # every bridge and stairs still being funded, each its own line
            cost = structures_cfg(w)["cost"]
            for st in isl.structures():
                if not st["built"] and vil.bells > 0:
                    whose = "" if st["by"] is None else (", your proposal" if st["by"] == name else f", {st['by']}'s proposal")
                    add(Option(f"put bells toward the {st['name']} ({st['fund']}/{cost} raised{whose}){at}",
                               {"do": "contribute_bridge", **({} if st["id"] == "bridge" else {"project": st["id"]})}, ok,
                               {"bells": Count(1, min(vil.bells, cost - st["fund"]))}))
        elif not isl.bridge_built and vil.bells > 0:
            add(Option(f"put bells toward the {w['bridge']['name']} ({isl.bridge_fund}/{w['bridge']['cost']} raised){at}",
                       {"do": "contribute_bridge"}, ok,
                       {"bells": Count(1, min(vil.bells, w["bridge"]["cost"] - isl.bridge_fund))}))
        cost = int((w.get("ordinances") or {}).get("cost", 0))
        if cost and vil.bells >= cost:
            for o in (w.get("ordinances") or {}).get("types", {}):
                if o != isl.ordinance or isl.pending_ordinance:
                    add(Option(f"pay Isabelle {cost} bells to set the {o.replace('_', ' ')} ordinance from tomorrow{at}",
                               {"do": "set_ordinance", "ordinance": o}, ok))
        move = int(w.get("move_house_cost", 10000))
        if vil.bells >= move and name not in isl.pending_moves:
            for s in isl.free_sites():
                if s not in isl.pending_moves.values():
                    add(Option(f"pay Tom Nook {move} bells to move your home to {s} ({isl.sites[s]['area']}) "
                               f"overnight{at}", {"do": "move_house", "to": s}, ok))

    # Resting, getting home, the plaza.
    add(Option("rest" + ("" if at_home_ok or home_cell is None else " (where you are: you can't get home)"),
               {"do": "rest"}))
    rescued = (getattr(isl, "rescued", None) or {}).get(name) == isl.day
    if getattr(isl, "prompt_version", 1) >= 15 and bodies.stuck(name) and not rescued:
        add(Option("call the Rescue Service to take you home (once a day)", {"do": "call_rescue"}))
    plaza = space.coords_of_place("plaza") if hasattr(space, "buildings") else None
    if reach.near([plaza]) if plaza is not None else True:
        add(Option("spend the hour at the plaza: the bulletin board, letters and parcels (not one of your actions)",
                   {"do": "plaza"}))
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
