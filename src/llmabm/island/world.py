"""Island state and the rules that resolve villager actions.

Everything here is deterministic given the RNG. NPCs (Tom Nook, Timmy and Tommy,
the Able Sisters, Blathers) are rules, not LLM agents, in the baseline.
"""

from __future__ import annotations

import copy
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = ("fish", "bugs", "fossils")
FOSSIL = "fossil"  # a fossil nobody has assessed yet; Blathers says which species it is


# --- world data ------------------------------------------------------------

def load_world(path: Path, facts: str = "canonical", seed: int = 0) -> dict:
    w = yaml.safe_load(Path(path).read_text())
    if facts == "perturbed":
        w = perturb(w, random.Random(f"perturb|{seed}"))
    elif facts != "canonical":
        raise ValueError("world_facts must be 'canonical' or 'perturbed'")
    return w


_SYL = ["ka", "lo", "mi", "ren", "tu", "bas", "pi", "gor", "sel", "da", "vin", "ru", "ne", "pho", "zal", "it", "mo", "ash"]


def _fake_name(rng: random.Random, taken: set, suffix: str) -> str:
    while True:
        n = "".join(rng.choice(_SYL) for _ in range(rng.choice([2, 3])))
        if n not in taken:
            taken.add(n)
            return f"{n} {suffix}".strip()


def perturb(w: dict, rng: random.Random) -> dict:
    """Rename every species, jitter prices, and reshuffle fish locations and times.
    Recipes that consume shells are rewritten to the new shell names."""
    w = copy.deepcopy(w)
    taken: set = set()
    jitter = lambda p: int(round(p * rng.uniform(0.6, 1.6), -1))  # noqa: E731
    fish = {}
    places = ["sea", "river", "pond"]
    windows = [[0, 24], [0, 24], [4, 21], [9, 16], [16, 9], [21, 4]]
    for spec in w["fish"].values():
        fish[_fake_name(rng, taken, "fish")] = {**spec, "price": jitter(spec["price"]),
                                                "where": rng.choice(places), "hours": rng.choice(windows)}
    w["fish"] = fish
    w["bugs"] = {_fake_name(rng, taken, "beetle" if rng.random() < .5 else "moth"):
                 {**spec, "price": jitter(spec["price"]), "hours": rng.choice(windows)} for spec in w["bugs"].values()}
    lo, hi = _fossil_range(w)
    w["fossils"] = {_fake_name(rng, taken, "fossil"): min(hi, max(lo, jitter(p))) for p in w["fossils"].values()}
    shell_map = {old: _fake_name(rng, taken, "shell") for old in w["shells"]}
    w["shells"] = {shell_map[o]: [jitter(p), wt] for o, (p, wt) in w["shells"].items()}
    for r in w["recipes"].values():
        r["needs"] = {shell_map.get(k, k): v for k, v in r["needs"].items()}
        r["price"] = jitter(r["price"])
    for k in list(w["materials"]):
        w["materials"][k] = jitter(w["materials"][k])
    return w


def _fossil_range(w: dict) -> tuple[int, int]:
    fa = w.get("fossil_assessment") or {}
    return int(fa.get("min_price", 0)), int(fa.get("max_price", 10**9))


def sell_prices(w: dict) -> dict[str, int]:
    p = dict(w["materials"])
    p[FOSSIL] = int((w.get("fossil_assessment") or {}).get("unassessed_price", 400))
    p.update({k: v[0] for k, v in w["shells"].items()})
    p.update({k: v["price"] for k, v in w["fish"].items()})
    p.update({k: v["price"] for k, v in w["bugs"].items()})
    p.update(w["fossils"])
    p.update({k: v["price"] for k, v in w["recipes"].items()})
    # Shop goods sell back at a quarter of their price, as in the game.
    for shop in w["shops"].values():
        for pool in ("stock_pool", "always", "seeds_pool"):
            for k, v in (shop.get(pool) or {}).items():
                p.setdefault(k, v // 4)
    for k, v in (w.get("clothing") or {}).items():
        p.setdefault(k, int(v["price"]) // 4)
    # Recipe cards sell back at a quarter of what the Cranny charges for them.
    items, cr = w.get("items") or {}, w["shops"]["cranny"]
    for r in w["recipes"]:
        tool = items.get(r, {}).get("kind") == "tool"
        p[r + " recipe"] = int(cr.get("tool_recipe_price", 500) if tool else cr.get("recipe_price", 2000)) // 4
    return p


def item_info(w: dict, item: str) -> dict:
    """Catalog entry for an item: kind, size, whether it can go outside."""
    info = dict((w.get("items") or {}).get(item) or {})
    info.setdefault("kind", "clothing" if item in (w.get("clothing") or {}) else "item")
    info.setdefault("size", [1, 1])
    info.setdefault("outdoor", info["kind"] in ("furniture", "fence"))
    return info


def in_window(hour: int, window) -> bool:
    """[start, end) with wrap-around past midnight, e.g. [16, 9] is 4 PM to 9 AM."""
    a, b = window
    return a <= hour < b if a < b else (hour >= a or hour < b)


def fmt_window(window) -> str:
    a, b = window
    return "all day" if (a, b) == (0, 24) else f"{a:02d}:00-{b % 24:02d}:00"


def clock(hour: int) -> str:
    """Game hours run past 24 (25 = 01:00 the next calendar day, same island day)."""
    return f"{hour % 24:02d}:00"


def to_game_hour(clock_hour: int, day_start: int) -> int:
    clock_hour %= 24
    return clock_hour if clock_hour >= day_start else clock_hour + 24


ORDINANCE_TEXT = {
    "none": "no ordinance",
    "early_bird": "Early Bird: everyone keeps earlier hours, and the shops open and close earlier",
    "night_owl": "Night Owl: everyone keeps later hours, and the shops open and close later",
    "bell_boom": "Bell Boom: Nook's Cranny pays more for everything, and everything in the shops costs more",
}


# Which tool each action wears out. The ladder and vaulting pole wear out on trips that need them.
TOOL_FOR = {"mine_rock": "shovel", "dig_fossil": "shovel", "dig_clam": "shovel", "dig_stump": "shovel", "break_rock": "shovel",
            "chop_wood": "axe", "chop_down_tree": "axe", "fish": "fishing rod", "catch_bug": "net"}
MOVEMENT_TOOLS = ("ladder", "vaulting pole")
LATEST_PROMPT_VERSION = 12  # see PROMPT_CHANGES in prompts.py; some rules depend on it too
AXES = ("stone axe", "axe")  # from prompt version 4: the stone axe only gathers wood; the axe fells at the last wood
CARD = " recipe"  # a recipe card is the item "<recipe> recipe"
CLAM, BAIT = "manila clam", "fishing bait"  # dug on the beach (dig_clam); one clam crafts one bait
MAP = "island map"  # from Tom Nook or Isabelle; carried, it shows the island's layout in plans (prompt version 3 on)


def recipe_of_card(item: str) -> str | None:
    return item[: -len(CARD)] if item.endswith(CARD) else None


def tool_uses(isl) -> int:
    """How many uses a tool lasts on this island."""
    return int((isl.w.get("tools") or {}).get("uses", 50))


def _item_words(name: str) -> tuple[str, ...]:
    """An item name as words, each without a plural s: "fishing baits" -> ("fishing", "bait")."""
    return tuple(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
                 for w in re.findall(r"[a-z0-9']+", name.lower()))


def resolve_item(name, pool) -> str:
    """The item in `pool` that a villager most likely means by `name`, or `name` cleaned up if none is clear.
    Exact names win; then the same words ignoring plurals ("maps" -> "map"); then the one item whose name
    contains all the words ("map" -> "island map", "bait" -> "fishing bait"). Two or more candidates are
    ambiguous, so the name is kept as it was ("fish" stays "fish"). Bells are always bells."""
    name = re.sub(r"\s+x\s*\d+$", "", str(name).strip().lower())
    name = re.sub(r"^(?:a|an|the|some|your|my)\s+", "", name)
    pool = [str(k).lower() for k in pool]
    if name in ("bells", "bell") or name in pool:
        return "bells" if name == "bell" else name
    want = _item_words(name)
    if not want:
        return name
    for match in (lambda k: _item_words(k) == want, lambda k: set(want) <= set(_item_words(k))):
        hits = sorted({k for k in pool if match(k)})
        if len(hits) == 1:
            return hits[0]
        if hits:
            return name
    return name


# Who you can ask, and where to find them.
NPCS = {"tom nook": ("Resident Services", "resident_services"), "isabelle": ("Resident Services", "resident_services"),
        "blathers": ("museum", "museum"), "timmy": ("Nook's Cranny", "cranny"), "tommy": ("Nook's Cranny", "cranny"),
        "timmy and tommy": ("Nook's Cranny", "cranny"), "able sisters": ("Able Sisters", "able"),
        "the able sisters": ("Able Sisters", "able")}


def npc_of(action: dict) -> str | None:
    return str(action.get("who", "")).strip().lower() or None


def category_of(w: dict, item: str) -> str | None:
    for c in CATEGORIES:
        if item in w[c]:
            return c
    return None


# --- state -----------------------------------------------------------------

@dataclass
class Villager:
    name: str
    bells: int = 1000
    loan: int = 0
    house: int = 0
    inventory: Counter = field(default_factory=Counter)
    recipes: set[str] = field(default_factory=set)
    prices_seen: dict[str, int] = field(default_factory=dict)
    notes: list[tuple[int, str]] = field(default_factory=list)
    inbox: list[str] = field(default_factory=list)
    today: list[str] = field(default_factory=list)  # results of today's actions, in order
    wakes: int = 6        # clock hour the villager usually gets up
    sleeps: int = 24      # clock hour they go to bed; values past 24 or below `wakes` mean after midnight
    self_doc: str = ""    # private free-form page, rewritten nightly when reflection is on
    memories: list = field(default_factory=list)  # what they chose to hold on to at night (see memory.py)
    wearing: dict = field(default_factory=dict)  # slot -> clothing item; others can see it
    wear: dict = field(default_factory=dict)  # tool -> uses so far of each one they carry, in-use first
    interior: list = field(default_factory=list)  # furniture inside their home; visitors see it
    # From prompt version 12, `inventory` is only what is in their pockets; what they keep at home is
    # here, and is only reachable when they are at home.
    home_items: Counter = field(default_factory=Counter)
    home_bells: int = 0
    home_wear: dict = field(default_factory=dict)  # tool -> uses so far of each one kept at home

    def __setstate__(self, state):  # checkpoints from before a field existed get its default
        fresh = Villager(state.get("name", ""))
        self.__dict__.update({**fresh.__dict__, **state})

    def items(self) -> int:
        return sum(self.inventory.values())

    def home_count(self) -> int:
        return sum(self.home_items.values())


@dataclass
class Tree:
    kind: str  # hardwood | fruit | sapling
    wood_left: int = 0
    fruit: int = 0
    regrow_in: int = 0
    mature_in: int = 0
    becomes: str = "hardwood"
    pos: tuple | None = None  # tile on the grid, when the island is a grid


class Island:
    def __init__(self, world: dict, villager_names: list[str], rng: random.Random, start_bells: int = 1000):
        self.w = world
        self.rng = rng
        self.prices = sell_prices(world)
        isl = world["island"]
        self.trees: list[Tree] = [Tree("hardwood") for _ in range(isl["hardwood_trees"])]
        self.trees += [Tree("fruit", fruit=isl["fruit_per_tree"]) for _ in range(isl["fruit_trees"])]
        self.n_rocks = isl["rocks"]
        self.rocks: list[int] = []
        self.money_rock = 0
        self.rock_back_on: dict[int, int] = {}   # broken rock index -> day it comes back
        self.rocks_respawned: list[int] = []
        self.money_hits: dict[int, int] = {}
        self.shells_left = self.fossils_left = 0
        self.clams_left = 0
        self.museum: dict[str, dict[str, str]] = {c: {} for c in CATEGORIES}  # species -> donor
        self.bridge_fund = 0
        self.bridge_built = False
        self.board: list[tuple[int, str, str]] = []  # (day, author, text)
        self.cranny_stock: dict[str, int] = {}
        self.cranny_recipe: str | None = None
        self.able_stock: dict[str, int] = {}
        self.day = -1
        self.v: dict[str, Villager] = {n: Villager(n, bells=start_bells) for n in villager_names}
        starters = set(world.get("starter_recipes", []))
        pool = [r for r in world["recipes"] if r not in starters and r not in self.tool_recipes()]
        for vil in self.v.values():
            vil.recipes = set(rng.sample(pool, min(world["recipes_known_at_start"], len(pool)))) | starters
            for t in (world.get("tools") or {}).get("start_with", []):
                vil.inventory[t] += 1
        self.collected: dict[str, Counter] = {n: Counter() for n in villager_names}  # resource class -> count
        self.ordinance = "none"
        self.pending_ordinance: tuple[str, str] | None = None  # (ordinance, who set it), effective next morning
        self.pending_moves: dict[str, str] = {}  # villager -> homesite Tom Nook moves them to overnight
        # A new island follows the latest rules; resumed runs set the version they started with.
        self.prompt_version = LATEST_PROMPT_VERSION
        self.moved_today: list[str] = []
        self.news: list[str] = []            # today's public happenings, for Isabelle's announcement
        self.yesterday_news: list[str] = []
        self.announcement: str | None = None  # LLM-written morning announcement, when enabled
        self.favors: list[dict] = []          # requests villagers made of each other
        self.favors_done_today: list[dict] = []
        self.mail: list[dict] = []            # letters and parcels on their way; delivered the next morning
        self.where_notes: dict[str, str] = {}  # per villager: getting around (tools, what is reachable)
        self.sites: dict = {}  # homesite id -> {area, at}; filled in from the tile island
        self.home: dict[str, str | None] = {n: None for n in villager_names}       # homesite id
        self.arrived: dict[str, int | None] = {n: None for n in villager_names}    # game hour of arrival (day 0)

    # --- map ---------------------------------------------------------------
    def free_sites(self) -> list[str]:
        taken = set(self.home.values())
        return [s for s in self.sites if s not in taken]

    def settle(self, name: str, wanted: str | None) -> tuple[str | None, str]:
        """Put a villager's tent on a homesite. Taken or unknown choices fall back to the
        nearest free site to what they asked for (or a random free one)."""
        free = self.free_sites()
        if not free:
            return None, "no homesites left"
        wanted = str(wanted or "").strip()
        if wanted in free:
            site = wanted
            msg = f"pitched your tent {self.sites[site]['area']} ({site})"
        else:
            if wanted in self.sites:
                ref = self.sites[wanted]["at"]
                site = min(free, key=lambda s: _dist(self.sites[s]["at"], ref))
                why = f"{wanted} was already taken"
            else:
                site = self.rng.choice(free)
                why = "no valid homesite chosen"
            msg = f"{why}; pitched your tent {self.sites[site]['area']} ({site}) instead"
        self.home[name] = site
        self.news.append(f"{name} moved to the island and lives {self.sites[site]['area']}")
        return site, msg

    def neighbors(self, name: str) -> list[str]:
        h = self.home.get(name)
        if not h:
            return []
        area = self.sites[h]["area"]
        return [n for n, s in self.home.items() if n != name and s and self.sites[s]["area"] == area]

    # --- schedules and ordinances ------------------------------------------
    def _ord(self) -> dict:
        return (self.w.get("ordinances", {}).get("types", {}) or {}).get(self.ordinance, {}) or {}

    def awake_window(self, name: str) -> tuple[int, int]:
        """[wake, sleep) in game hours, after any ordinance shift."""
        vil = self.v[name]
        start = self.w["calendar"]["day_start"]
        wake = to_game_hour(vil.wakes, start)
        sleep = vil.sleeps if vil.sleeps > 24 else to_game_hour(vil.sleeps, start)
        if sleep <= wake:
            sleep += 24
        shift = int(self._ord().get("schedule_shift", 0))
        end = start + self.w["calendar"].get("day_length", 24)
        return max(start, wake + shift), min(end, sleep + shift)

    def is_awake(self, name: str, hour: int) -> bool:
        arr = self.arrived.get(name)
        if arr is None:
            return False
        a, b = self.awake_window(name)
        if self.day == 0:  # on arrival day they are up from the moment they land
            a = max(arr, self.w["calendar"]["day_start"])
            return a <= hour < b and hour >= arr
        return a <= hour < b

    def price_factor(self) -> float:
        return float(self._ord().get("price_factor", 1.0))

    def sell_price(self, item: str) -> int:
        return int(round(self.prices[item] * self.price_factor()))

    # --- capacity ----------------------------------------------------------
    def split_home(self) -> bool:
        """From prompt version 12, pockets and home storage are apart: only pockets go with you."""
        return getattr(self, "prompt_version", 1) >= 12

    def capacity(self, vil: Villager) -> int:
        """Slots for what a villager carries. Before prompt version 12, house storage added to the pockets."""
        if self.split_home():
            return self.w["pockets"]
        return self.w["pockets"] + self.w["house_levels"][vil.house]["storage"]

    def space(self, vil: Villager) -> int:
        return self.capacity(vil) - vil.items()

    def home_capacity(self, vil: Villager) -> int:
        return self.w["house_levels"][vil.house]["storage"] if self.split_home() else 0

    def home_space(self, vil: Villager) -> int:
        return self.home_capacity(vil) - vil.home_count()

    def stash(self, vil: Villager, store: dict | None, take: dict | None) -> list[str]:
        """At home (prompt version 12): put things from the pockets away, and take things out. Bells can always
        be left at home; things need storage room. Tools keep their wear either way. Returns what happened,
        one line per move."""
        out = []
        store = self.resolve_side(vil.name, {str(k).lower(): int(n) for k, n in (store or {}).items() if int(n) > 0})
        for k, n in store.items():
            have = vil.bells if k == "bells" else vil.inventory[k]
            if have < 1:
                out.append(f"couldn't put away {k}: you have none in your pockets")
                continue
            n = min(n, have)
            if k != "bells":
                room = self.home_space(vil)
                if room < 1:
                    out.append(f"couldn't put away {k}: " + ("your tent has no room for things"
                                                             if self.home_capacity(vil) == 0 else "no room left at home"))
                    continue
                n = min(n, room)
            parcel = self.take_items(vil, {k: n})
            if k == "bells":
                vil.home_bells += n
            else:
                vil.home_items[k] += n
                vil.home_wear[k] = sorted(vil.home_wear.get(k, []) + parcel["wear"].get(k, []), reverse=True)
            out.append(f"put away {n} {k}")
        pool = ["bells"] + [k for k, c in vil.home_items.items() if c > 0]
        take = {resolve_item(str(k), pool): int(n) for k, n in (take or {}).items() if int(n) > 0}
        for k, n in take.items():
            have = vil.home_bells if k == "bells" else vil.home_items[k]
            if have < 1:
                out.append(f"couldn't take out {k}: there is none at home")
                continue
            n = min(n, have) if k == "bells" else min(n, have, self.space(vil))
            if n < 1:
                out.append(f"couldn't take out {k}: your pockets are full")
                continue
            if k == "bells":
                vil.home_bells -= n
                vil.bells += n
            else:
                worn = sorted(vil.home_wear.get(k, []), reverse=True)
                moved, vil.home_wear[k] = worn[len(worn) - n:], worn[: len(worn) - n]
                vil.home_items[k] -= n
                self.put_items(vil, {"items": {k: n}, "wear": {k: moved}})
            out.append(f"took out {n} {k}")
        return out

    # --- tools ------------------------------------------------------------
    def tool_for(self, vil: Villager, action: dict) -> tuple[str | None, str | None]:
        """(the tool this action uses, or None; why it can't be done, or None). From prompt version 4,
        chop_wood takes either axe: the one named in `axe`, else the stone axe if they carry one."""
        do = str(action.get("do", "")).lower()
        if do == "chop_wood" and getattr(self, "prompt_version", 1) >= 4:
            want = str(action.get("axe", "")).strip().lower()
            if want in AXES:
                return (want, None) if vil.inventory[want] > 0 else (None, f"you have no {want}")
            have = [t for t in AXES if vil.inventory[t] > 0]
            return (have[0], None) if have else (None, "you have no axe")
        need = TOOL_FOR.get(do)
        if need and vil.inventory[need] < 1:
            return None, f"you have no {need}"
        return need, None

    def item_names(self) -> set[str]:
        """Every item name the island knows: what can be sold, the catalogue, clothes, the map, and whatever
        anyone carries now."""
        names = set(self.prices) | set(self.w.get("items") or {}) | set(self.w.get("clothing") or {}) | {MAP}
        for vil in self.v.values():
            names |= {k for k, n in vil.inventory.items() if n > 0}
        return names

    def resolve_side(self, holder: str | None, items: dict) -> dict:
        """Item names as the sim knows them: matched against what `holder` carries first (what they can
        give), then against every item name. Quantities of names that resolve to the same item add up."""
        carried = [k for k, n in self.v[holder].inventory.items() if n > 0] if holder in self.v else []
        names = None
        out: dict[str, int] = {}
        for k, n in items.items():
            r = resolve_item(k, carried)
            if r not in carried and r != "bells":
                names = names if names is not None else self.item_names()
                r = resolve_item(r, names)
            out[r] = out.get(r, 0) + n
        return out

    def unknown_items(self, items: dict) -> list[str]:
        """Names in `items` (already resolved) that aren't anything on the island, such as "selit bait"."""
        names = self.item_names()
        return [k for k in items if k != "bells" and k not in names]

    def is_tool(self, item: str) -> bool:
        return (self.w.get("items") or {}).get(item, {}).get("kind") == "tool"

    def tool_recipes(self) -> list[str]:
        return [r for r in self.w["recipes"] if self.is_tool(r)]

    def _wear(self, vil: Villager, tool: str) -> list[int]:
        """Uses so far of each `tool` the villager carries, most worn (the one in use) first.
        Kept in step with the inventory: new ones start at 0, and the least worn leave first."""
        lst = sorted(vil.wear.get(tool, []), reverse=True)
        lst = (lst + [0] * vil.inventory[tool])[: vil.inventory[tool]]
        vil.wear[tool] = lst
        return lst

    def use_tool(self, vil: Villager, tool: str) -> str | None:
        """One use of a tool. Returns a note if it broke."""
        lst = self._wear(vil, tool)
        if not lst:
            return None
        lst[0] += 1
        if lst[0] < tool_uses(self):
            return None
        lst.pop(0)
        vil.inventory[tool] -= 1
        if getattr(self, "prompt_version", 1) >= 10:
            return f"your {tool} broke after {tool_uses(self)} uses and is gone from your pockets"
        return f"your {tool} broke"

    # --- moving things between villagers -------------------------------------
    def take_items(self, vil: Villager, items: dict) -> dict | None:
        """Take items (and bells) out of a villager's pockets, carrying tool wear along.
        Spare tools go first. Returns a parcel, or None if they don't have it all."""
        items = {str(k).lower(): int(n) for k, n in items.items() if int(n) > 0}
        if not items or any((vil.bells if k == "bells" else vil.inventory[k]) < n for k, n in items.items()):
            return None
        wear = {}
        for k, n in items.items():
            if k == "bells":
                vil.bells -= n
                continue
            if self.is_tool(k):
                lst = self._wear(vil, k)
                wear[k], vil.wear[k] = lst[len(lst) - n:], lst[: len(lst) - n]
            vil.inventory[k] -= n
        return {"items": items, "wear": wear}

    def put_items(self, vil: Villager, parcel: dict) -> tuple[dict, dict]:
        """Put a parcel in a villager's pockets, as much as fits. Returns (delivered, left over).
        Recipe cards for recipes they don't know are learned on the spot."""
        got, left = {}, {}
        for k, n in parcel["items"].items():
            if k == "bells":
                vil.bells += n
                got[k] = n
                continue
            fit = min(n, self.space(vil))
            if fit:
                if self.is_tool(k):
                    worn = parcel.get("wear", {}).get(k, [])[:fit]
                    vil.wear[k] = sorted(self._wear(vil, k) + worn + [0] * (fit - len(worn)), reverse=True)
                vil.inventory[k] += fit
                got[k] = fit
            if n - fit:
                left[k] = n - fit
        self.read_cards(vil)
        return got, ({"items": left, "wear": {k: parcel.get("wear", {}).get(k, [])[got.get(k, 0):] for k in left}}
                     if left else {})

    def read_cards(self, vil: Villager) -> list[str]:
        """Learn the recipe on any card the villager doesn't know yet (using the card up)."""
        learned = []
        for k in list(vil.inventory):
            r = recipe_of_card(k)
            if r in self.w["recipes"] and r not in vil.recipes and vil.inventory[k] > 0:
                vil.recipes.add(r)
                vil.inventory[k] -= 1
                learned.append(r)
        return learned

    def give_card(self, vil: Villager, recipe: str) -> str:
        """A recipe card reaches a villager: learned if new, kept as a spare if not."""
        if recipe not in vil.recipes:
            vil.recipes.add(recipe)
            if self.split_home():
                return f"learned the {recipe} recipe ({self.recipe_needs(recipe)})"
            return f"learned the {recipe} recipe"
        if self.space(vil) < 1:
            return f"already knew the {recipe} recipe, and had no room for the card"
        vil.inventory[recipe + CARD] += 1
        return f"already knew the {recipe} recipe, so you kept the card"

    def give_items(self, vil: Villager, items: Counter) -> Counter:
        """Add what fits; return what was actually added."""
        got = Counter()
        for k, n in items.items():
            take = min(n, self.space(vil))
            if take > 0:
                vil.inventory[k] += take
                got[k] = take
        return got

    # --- daily cycle -------------------------------------------------------
    def new_day(self) -> None:
        self.day += 1
        self.yesterday_news, self.news = self.news, []
        self._set_date_and_weather()
        self.moved_today = []
        for name, site in sorted(self.pending_moves.items()):
            self.home[name] = site
            self.moved_today.append(name)
            self.yesterday_news.append(f"{name} moved home to {self.sites[site]['area']}")
        self.pending_moves = {}
        if self.pending_ordinance:
            self.ordinance, who = self.pending_ordinance
            self.pending_ordinance = None
            self.yesterday_news.append(f"the {ORDINANCE_TEXT[self.ordinance].split(':')[0]} ordinance set by {who} is now in effect"
                                       if self.ordinance != "none" else f"{who} lifted the island ordinance")
        isl = self.w["island"]
        for i, back in list(self.rock_back_on.items()):
            if self.day >= back:
                del self.rock_back_on[i]
                self.rocks_respawned.append(i)  # the grid puts it somewhere new
        self.rocks = [0 if i in self.rock_back_on else isl["rock_hits"] for i in range(self.n_rocks)]
        whole = [i for i in range(self.n_rocks) if i not in self.rock_back_on]
        self.money_rock = self.rng.choice(whole) if whole else -1
        self.money_hits = {}
        self.shells_left = isl["shells_per_day"]
        self.fossils_left = isl["fossils_per_day"]
        self.clams_left = int(isl.get("clams_per_day", 0))
        for t in self.trees:
            if t.kind == "hardwood":
                t.wood_left = isl["wood_per_tree"]
            elif t.kind == "fruit":
                if getattr(self, "prompt_version", 1) >= 4:  # fruit trees can be chopped for wood too
                    t.wood_left = isl["wood_per_tree"]
                if t.fruit == 0:
                    t.regrow_in -= 1
                    if t.regrow_in <= 0:
                        t.fruit = isl["fruit_per_tree"]
            elif t.kind == "sapling":
                t.mature_in -= 1
                if t.mature_in <= 0:
                    t.kind = t.becomes
                    t.fruit = isl["fruit_per_tree"] if t.kind == "fruit" else 0
        cr, ab = self.w["shops"]["cranny"], self.w["shops"]["able"]
        seeds = sorted((cr.get("seeds_pool") or {}).items())
        self.cranny_stock = {**(cr.get("always") or {}),
                             **dict(self.rng.sample(seeds, min(len(seeds), cr.get("seeds_daily", 0)))),
                             **dict(self.rng.sample(sorted(cr["stock_pool"].items()), cr["sells_daily"]))}
        cards = sorted(set(self.w["recipes"]) - set(self.tool_recipes()))
        self.cranny_recipe = self.rng.choice(cards) if cr.get("sells_recipe_daily") else None
        if self.w.get("clothing"):
            clothes = sorted((k, int(v["price"])) for k, v in self.w["clothing"].items())
        else:
            clothes = sorted((ab.get("stock_pool") or {}).items())
        self.able_stock = dict(self.rng.sample(clothes, min(len(clothes), ab["sells_daily"])))
        for vil in self.v.values():
            vil.today = []

    # --- calendar and weather -------------------------------------------------
    MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]

    def _set_date_and_weather(self) -> None:
        cal = self.w["calendar"]
        per = int(cal.get("days_per_month", 30))
        m0 = int(cal.get("start_month", 4)) - 1
        self.month = (m0 + self.day // per) % 12 + 1
        self.day_of_month = self.day % per + 1
        self.season = next((name for name, months in (self.w.get("seasons") or {}).items() if self.month in months),
                           "spring")
        odds = (self.w.get("weather") or {}).get(self.season) or {"sunny": 1.0}
        wrng = random.Random(f"weather|{self.day}|{self.rng.random()}")
        self.weather = wrng.choices(list(odds), weights=list(odds.values()))[0]

    @property
    def wet(self) -> bool:
        return getattr(self, "weather", "sunny") in ("rain", "snow")

    def date_text(self) -> str:
        if not hasattr(self, "month"):
            return ""
        return f"{self.MONTHS[self.month - 1]} {self.day_of_month}, {self.season}, {self.weather}"

    def _available(self, spec: dict, hour: int) -> bool:
        if not in_window(hour % 24, spec["hours"]):
            return False
        if spec.get("months") and getattr(self, "month", None) and self.month not in spec["months"]:
            return False
        w = spec.get("weather", "any")
        return not ((w == "wet" and not self.wet) or (w == "dry" and self.wet))

    # --- actions -----------------------------------------------------------
    _NEEDS_OPEN = {"sell": "cranny", "upgrade_house": "resident_services", "pay_loan": "resident_services",
                   "contribute_bridge": "resident_services", "donate": "museum", "assess": "museum",
                   "set_ordinance": "resident_services",
                   "move_house": "resident_services"}

    def shop_hours(self, place: str) -> list[int]:
        a, b = self.w.get("hours", {}).get(place, [0, 24])
        shift = int(self._ord().get("shop_shift", 0)) if place in ("cranny", "able") else 0
        return [a, b] if (a, b) == (0, 24) else [(a + shift) % 24, (b + shift) % 24]

    def is_open(self, place: str, hour: int) -> bool:
        return in_window(hour % 24, self.shop_hours(place))

    def act(self, name: str, action: dict, hour: int) -> tuple[bool, str]:
        """Resolve one action at a given hour. Returns (ok, human-readable result)."""
        vil = self.v[name]
        do = str(action.get("do", "")).strip().lower()
        fn = getattr(self, f"_do_{do}", None)
        if fn is None:
            return False, f"'{do}' is not something you can do"
        place = self._NEEDS_OPEN.get(do)
        if do == "buy":
            place = "able" if str(action.get("shop", "")).lower() == "able" else "cranny"
        if do == "chop_down_tree" and getattr(self, "prompt_version", 1) >= 4:
            return False, ("there's no felling on its own: chopping wood with an axe (not a stone axe) brings the tree "
                           "down when it takes the tree's last wood")
        if do == "ask":
            if npc_of(action) not in NPCS:
                if getattr(self, "prompt_version", 1) >= 2:
                    return False, self.not_an_npc(str(action.get("who", "")))
                return False, f"nobody called '{action.get('who', '')}' to ask; try Tom Nook, Isabelle, Blathers, Timmy and Tommy or the Able Sisters"
            place = NPCS[npc_of(action)][1]
        if place and not self.is_open(place, hour):
            names = {"cranny": "Nook's Cranny", "able": "the Able Sisters", "resident_services": "Resident Services", "museum": "the museum"}
            return False, f"{names[place]} is closed at {clock(hour)} (open {fmt_window(self.shop_hours(place))})"
        try:
            return fn(vil, action, hour)
        except (TypeError, ValueError) as e:
            return False, f"{do} failed: {e}"

    def _do_wear(self, vil, a, hour):
        item = str(a.get("item", "")).lower()
        info = (self.w.get("clothing") or {}).get(item)
        if info is None:
            return False, f"{item} is not something you can wear"
        if vil.inventory[item] < 1 and item not in vil.wearing.values():
            return False, f"you don't have {_a(item)}"
        slot = info.get("slot", "top")
        old = vil.wearing.get(slot)
        if old == item:
            return False, f"you're already wearing the {item}"
        if old:
            vil.inventory[old] += 1
        vil.inventory[item] -= 1
        vil.wearing[slot] = item
        return True, f"put on the {item}" + (f" (took off the {old})" if old else "")

    def _do_rest(self, vil, a, hour):
        return True, "rested"

    def _do_mine_rock(self, vil, a, hour):
        target = a.get("rock")
        if target is not None:
            idx = int(target) - 1
            if not 0 <= idx < self.n_rocks:
                return False, f"there is no rock {target}"
        else:
            open_ = [i for i, h in enumerate(self.rocks) if h > 0]
            if not open_:
                return False, "every rock is already mined out today"
            idx = self.rng.choice(open_)
        hits = self.rocks[idx]
        if hits == 0:
            return False, f"rock {idx + 1} gives nothing more today"
        if idx == self.money_rock:
            schedule = self.w["island"]["money_rock_bells"]
            done = len(schedule) - hits
            bells = sum(schedule[done:])
            self.rocks[idx] = 0
            vil.bells += bells
            self.collected[vil.name]["rocks"] += hits
            return True, f"rock {idx + 1} was the money rock: got {bells} bells"
        draws = Counter(self.rng.choices(list(self.w["island"]["rock_yields"]),
                                         weights=list(self.w["island"]["rock_yields"].values()), k=hits))
        got = self.give_items(vil, draws)
        self.rocks[idx] -= sum(got.values())
        self.collected[vil.name]["rocks"] += sum(got.values())
        if not got:
            return False, f"pockets full, could not take anything from rock {idx + 1}"
        left = " (pockets full, left some)" if sum(got.values()) < hits else ""
        return True, f"hit rock {idx + 1}: {_fmt(got)}{left}"

    def _pick_tree(self, a, ok):
        """The tree the body walked to, if it said so; otherwise any suitable tree."""
        if a.get("tree") is not None:
            i = int(a["tree"])
            return self.trees[i] if 0 <= i < len(self.trees) and ok(self.trees[i]) else None
        trees = [t for t in self.trees if ok(t)]
        return self.rng.choice(trees) if trees else None

    def _do_chop_wood(self, vil, a, hour):
        v4 = getattr(self, "prompt_version", 1) >= 4
        kind = str(a.get("kind", "hardwood")).lower() if v4 else "hardwood"
        if kind not in ("hardwood", "fruit"):
            return False, f"there are no {kind} trees to chop; try hardwood or fruit"
        t = self._pick_tree(a, lambda t: t.kind == kind and t.wood_left > 0)
        if t is None:
            return False, f"no {kind} tree has wood left today" if v4 else "no tree has wood left today"
        draws = Counter(self.rng.choices(["wood", "softwood", "hardwood"], k=t.wood_left))
        got = self.give_items(vil, draws)
        t.wood_left -= sum(got.values())
        self.collected[vil.name]["wood"] += sum(got.values())
        if not got:
            return False, "pockets full"
        if a.get("_tool") == "axe" and t.wood_left == 0 and v4:
            # An iron axe brings the tree down with its last wood; a fruit tree drops what fruit it had.
            fruit = self.give_items(vil, Counter({self.w["island"]["native_fruit"]: t.fruit})) if t.fruit else Counter()
            self.collected[vil.name]["fruit"] += sum(fruit.values())
            t.kind, t.fruit = "stump", 0
            self.collected[vil.name]["trees_removed"] += 1
            return True, (f"chopped a {kind} tree: {_fmt(got + fruit)}; with the last wood the axe brought the tree "
                          "down, leaving a stump")
        return True, f"chopped a {kind} tree: {_fmt(got)}" if v4 else f"chopped a tree: {_fmt(got)}"

    def _do_shake_tree(self, vil, a, hour):
        t = self._pick_tree(a, lambda t: t.kind == "fruit" and t.fruit > 0)
        if t is None:
            return False, "no fruit tree has fruit right now"
        fruit = self.w["island"]["native_fruit"]
        got = self.give_items(vil, Counter({fruit: t.fruit}))
        t.fruit -= got[fruit]
        if t.fruit == 0:
            t.regrow_in = self.w["island"]["fruit_regrow_days"]
        self.collected[vil.name]["fruit"] += got[fruit]
        return (True, f"shook a tree: {_fmt(got)}") if got else (False, "pockets full")

    def _catch(self, vil, table: dict, label: str, cls: str, species: str | None = None):
        if self.space(vil) < 1:
            return False, "pockets full"
        if species:  # on the grid: the one bug or fish shadow the villager went after
            if self.rng.random() < self.w["island"]["catch_fail_rate"]:
                return True, f"{label}: it got away"
            vil.inventory[species] += 1
            self.collected[vil.name][cls] += 1
            return True, f"{label}: caught {_a(species)}"
        if not table or self.rng.random() < self.w["island"]["catch_fail_rate"]:
            return True, f"{label}: nothing bit" if cls == "fish" else f"{label}: it got away"
        names = list(table)
        weights = [v["weight"] for v in table.values()]
        got = self.rng.choices(names, weights=weights)[0]
        vil.inventory[got] += 1
        self.collected[vil.name][cls] += 1
        return True, f"{label}: caught {_a(got)}"

    def _do_fish(self, vil, a, hour):
        where = str(a.get("where", "river")).lower()
        if where not in ("sea", "river", "pond", "clifftop"):
            return False, "fish where? sea, river, pond or clifftop (the river up on a cliff)"
        habitats = {"clifftop": ("river", "clifftop")}.get(where, (where,))  # clifftop rivers have river fish too
        table = {k: v for k, v in self.w["fish"].items() if v["where"] in habitats and self._available(v, hour)}
        if a.get("none_seen"):
            if a.get("none_out"):
                return True, f"looked for fish in the {where}, but there seem to be no fish there at this time"
            return True, f"looked for fish in the {where}, but there were no fish shadows in sight"
        if a.get("_baited"):  # set by the body once it has found the water; never by the mind
            if not table:
                return True, f"no fish come out in the {where} at this time, so you kept your bait"
            from .grid import GridSpace

            vil.inventory["fishing bait"] -= 1
            sp = self.rng.choices(list(table), weights=[v["weight"] for v in table.values()])[0]
            size = GridSpace.SIZES.get(int(table[sp].get("shadow", 3)), "medium")
            return self._catch(vil, table, f"threw bait in the {where} and a {size} shadow appeared", "fish", sp)
        sp = a.get("_species") if a.get("_species") in self.w["fish"] else None  # set by the body, never the mind
        label = f"fished for a {a['_size']} shadow in the {where}" if a.get("_size") and sp else f"fished in the {where}"
        return self._catch(vil, table, label, "fish", sp)

    def _do_catch_bug(self, vil, a, hour):
        # On the grid the body says which habitat it searched; elsewhere every bug is possible.
        hab = a.get("habitat")
        table = {k: v for k, v in self.w["bugs"].items()
                 if self._available(v, hour) and (hab is None or v.get("habitat", "ground") == hab)}
        where = {"flowers": "among the flowers", "trees": "around the trees", "stumps": "on the stumps",
                 "rocks": "by the rocks", "ground": "in the grass"}.get(hab, "")
        if a.get("none_seen"):
            if a.get("none_out"):
                return True, f"looked for bugs {where}, but there seem to be none there at this time".replace("  ", " ")
            return True, f"looked for bugs {where}, but didn't see any".replace("  ", " ")
        sp = a.get("_species") if a.get("_species") in self.w["bugs"] else None  # set by the body, never the mind
        return self._catch(vil, table, f"went bug catching {where}".strip(), "bugs", sp)

    # --- home: decorating, visits, gifts --------------------------------------
    def interior_slots(self, vil) -> int:
        return int(self.w["house_levels"][vil.house].get("interior", 6))

    def _do_decorate(self, vil, a, hour):
        item = str(a.get("item", "")).lower()
        if item_info(self.w, item)["kind"] != "furniture":
            return False, f"{item} is not furniture"
        if vil.inventory[item] < 1:
            return False, f"you don't have {_a(item)}"
        if len(vil.interior) >= self.interior_slots(vil):
            return False, f"your {self.w['house_levels'][vil.house]['name']} has no room for more furniture"
        vil.inventory[item] -= 1
        vil.interior.append(item)
        return True, f"put {_a(item)} in your home"

    def _do_undecorate(self, vil, a, hour):
        item = str(a.get("item", "")).lower()
        if item not in vil.interior:
            return False, f"there is no {item} in your home"
        if self.space(vil) < 1:
            return False, "pockets full"
        vil.interior.remove(item)
        vil.inventory[item] += 1
        return True, f"took the {item} out of your home"

    def not_a_villager(self, who, verb: str = "visit") -> str:
        """Why `visit` can't reach someone or somewhere, and what would (prompt version 2 on)."""
        who = str(who or "").strip()
        low = who.lower()
        npcs = [n for n in NPCS if n in low and n not in ("timmy", "tommy", "the able sisters")]
        if npcs:
            where = sorted({("the " if NPCS[n][0] == "museum" else "") + NPCS[n][0] for n in npcs})
            one = {"timmy and tommy": "Timmy and Tommy", "able sisters": "Able Sisters"}.get(npcs[0], npcs[0].title())
            return (f"{who} {'don' if len(npcs) > 1 or ' and ' in low or 'sisters' in low else 'doesn'}'t live in a house you can visit; they work at "
                    f"{' and '.join(where)}. To talk to " + ("one of them, use ask with one name at a time" if len(npcs) > 1
                    else "them, use ask") + f" (who: {one}). Visiting is for other villagers' homes")
        if low in {"resident services", "nook's cranny", "nooks cranny", "the cranny", "museum", "the museum",
                   "able sisters", "plaza", "the plaza", "airport", "the airport"}:
            return (f"{who} isn't anyone's home. You go somewhere by doing something there: buy or sell at Nook's "
                    "Cranny, ask Tom Nook or Isabelle at Resident Services, donate or assess fossils at the museum. Visiting is for "
                    "other villagers' homes")
        return f"there is no villager called {who} to visit"

    def not_an_npc(self, who: str) -> str:
        """Why `ask` can't reach someone, and what would (prompt version 2 on)."""
        if who in self.v:
            return (f"{who} is a villager, not someone who runs a building, so ask doesn't reach them. To talk to "
                    f"{who}, meet them around the island, visit their home, or write to them at the plaza")
        return (f"nobody called '{who}' to ask; ask is for Tom Nook, Isabelle, Blathers, Timmy and Tommy or the "
                "Able Sisters")

    def _do_visit(self, vil, a, hour):
        host = a.get("who")
        if host not in self.v or host == vil.name:
            return False, f"there is no villager called {a.get('who')} to visit"
        if not self.home.get(host):
            return False, f"{host} doesn't have a home yet"
        return True, f"walked over to {host}'s home"

    def transfer(self, giver: str, taker: str, items: dict) -> tuple[bool, str]:
        """Hand items (or bells) from one villager to another, in person."""
        g, t = self.v[giver], self.v[taker]
        items = self.resolve_side(giver, {str(k).lower(): int(n) for k, n in items.items() if int(n) > 0})
        if not items:
            return False, "nothing to give"
        for k, n in items.items():
            have = g.bells if k == "bells" else g.inventory[k]
            if have < n:
                return False, f"{giver} doesn't have {n} {k}"
        if self.space(t) < sum(n for k, n in items.items() if k != "bells"):
            return False, f"{taker} has no room"
        self.put_items(t, self.take_items(g, items))
        self._settle_favors(giver, taker, items)
        return True, ", ".join(f"{n} {k}" for k, n in items.items())

    def ask_favor(self, asker: str, asked: str, items: dict) -> dict | None:
        items = {str(k).lower(): int(n) for k, n in items.items() if int(n) > 0}
        if not items or asked not in self.v or asked == asker:
            return None
        items = self.resolve_side(asked, items)
        if self.unknown_items(items):  # a request for something that doesn't exist isn't kept
            return None
        for f in self.favors:  # asking again for the same thing doesn't open a second request
            if f["status"] == "open" and f["asker"] == asker and f["asked"] == asked and f["items"] == items:
                return f
        f = {"id": f"f{len(self.favors) + 1}", "day": self.day, "asker": asker, "asked": asked,
             "items": items, "status": "open", "done_day": None}
        self.favors.append(f)
        return f

    def _settle_favors(self, giver: str, taker: str, items: dict, by: str = "gift") -> None:
        """Close the taker's open requests of the giver that `items` covers. `by` says how they were met: a
        "gift" (in person, by parcel, or a plaza offer asking nothing back) or a "trade" (something came back)."""
        for f in self.favors:
            if f["status"] == "open" and f["asker"] == taker and f["asked"] == giver \
                    and all(items.get(resolve_item(k, items), 0) >= n for k, n in f["items"].items()):
                f["status"], f["done_day"], f["by"] = "done", self.day, by
                self.favors_done_today.append(f)

    def trade(self, a: str, b: str, give: dict, want: dict) -> tuple[bool, dict | None]:
        """A trade in person (prompt version 12): `a` hands `b` `give` and gets `want` back, both at once, or
        nothing moves. Only pockets count. Returns (True, None), or (False, what stopped it): {"who", "item",
        "have"} when someone lacks something, {"who", "room": True} when someone has no room."""
        va, vb = self.v[a], self.v[b]
        for who, vil, side in ((a, va, give), (b, vb, want)):
            for k, n in side.items():
                have = vil.bells if k == "bells" else vil.inventory[k]
                if have < n:
                    return False, {"who": who, "item": k, "have": have}
        things = lambda side: sum(n for k, n in side.items() if k != "bells")  # noqa: E731
        if self.space(vb) + things(want) < things(give):
            return False, {"who": b, "room": True}
        if self.space(va) + things(give) < things(want):
            return False, {"who": a, "room": True}
        pa, pb = self.take_items(va, give), self.take_items(vb, want)
        if pa:
            self.put_items(vb, pa)
        if pb:
            self.put_items(va, pb)
        self._settle_favors(a, b, give, by="trade")
        self._settle_favors(b, a, want, by="trade")
        return True, None

    def favor_lines(self, name: str) -> list[str]:
        out = []
        for f in self.favors:
            if f["status"] != "open":
                continue
            want = ", ".join(f"{n} {k}" for k, n in f["items"].items())
            if f["asked"] == name:
                out.append(f"{f['asker']} asked you for {want} (day {f['day']})")
            elif f["asker"] == name:
                out.append(f"you asked {f['asked']} for {want} (day {f['day']})")
        return out

    def _do_beachcomb(self, vil, a, hour):
        isl = self.w["island"]
        msgs = []
        if a.get("none_seen") and self.shells_left > 0:
            return True, "walked along the beach but saw no shells around there"
        if self.rng.random() < isl["bottle_chance"]:
            r = self.rng.choice(sorted(set(self.w["recipes"]) - set(self.tool_recipes())))
            msgs.append(f"found a message bottle with a recipe card: {self.give_card(vil, r)}")
        n = min(isl["shells_per_action"], self.shells_left)
        if n == 0:
            return True, "; ".join(msgs + ["the beach is picked clean today"])
        shells = self.w["shells"]
        draws = Counter(self.rng.choices(list(shells), weights=[v[1] for v in shells.values()], k=n))
        got = self.give_items(vil, draws)
        self.shells_left -= sum(got.values())
        self.collected[vil.name]["shells"] += sum(got.values())
        msgs.append(f"picked up {_fmt(got)}" if got else "pockets full")
        return True, "; ".join(msgs)

    def _do_dig_fossil(self, vil, a, hour):
        if self.fossils_left == 0:
            return False, "no dig spots left on the island today"
        if a.get("none_seen"):
            return True, "looked around but saw no fossil dig spots there"
        if self.space(vil) < 1:
            return False, "pockets full"
        self.fossils_left -= 1
        vil.inventory[FOSSIL] += 1
        self.collected[vil.name]["fossils"] += 1
        return True, "dug up a fossil; nobody knows what it is until Blathers assesses it at the museum"

    def _do_dig_clam(self, vil, a, hour):
        """A manila clam from a spot on the beach. The first one teaches the fishing bait recipe (1 clam, 1 bait)."""
        if getattr(self, "clams_left", 0) == 0:
            return False, "no clam spots left on the beach today"
        if a.get("none_seen"):
            return True, "looked along the beach but saw no clam spots there"
        if self.space(vil) < 1:
            return False, "pockets full"
        self.clams_left -= 1
        vil.inventory[CLAM] += 1
        self.collected[vil.name]["clams"] += 1
        msg = f"dug up {_a(CLAM)}"
        if BAIT in self.w["recipes"] and BAIT not in vil.recipes:
            vil.recipes.add(BAIT)
            msg += f"; you see you could make {BAIT} from it, and now know the {BAIT} recipe"
        return True, msg

    def _do_assess(self, vil, a, hour):
        n = vil.inventory[FOSSIL]
        if n < 1:
            return False, "you have no unassessed fossils"
        vil.inventory[FOSSIL] = 0
        found = Counter(self.rng.choice(sorted(self.w["fossils"])) for _ in range(n))
        for f, k in found.items():
            vil.inventory[f] += k
        return True, f"Blathers assessed {n} fossil{'s' if n > 1 else ''}: {_fmt(found)}"

    def _do_plant(self, vil, a, hour):
        item = str(a.get("item", "")).lower()
        fruit = self.w["island"]["native_fruit"]
        if item_info(self.w, item)["kind"] == "seeds":
            if self._needs_grid(a):
                return self._needs_grid(a)
            if vil.inventory[item] < 1:
                return False, f"you have no {item}"
            vil.inventory[item] -= 1
            self.collected[vil.name]["flowers_planted"] += 1
            days = int(self.w.get("building", {}).get("flower_bloom_days", 3))
            return True, f"planted {item}; they should bloom in about {days} days"
        if item not in (fruit, "sapling"):
            return False, f"you can plant {_a(fruit)}, a sapling, or flower seeds"
        if vil.inventory[item] < 1:
            return False, f"you have no {item}"
        vil.inventory[item] -= 1
        self.trees.append(Tree("sapling", mature_in=self.w["island"]["sapling_mature_days"],
                               becomes="fruit" if item == fruit else "hardwood",
                               pos=tuple(a["at"]) if a.get("at") else None))
        self.collected[vil.name]["planted"] += 1
        return True, f"planted {_a(item)}; it will be a grown tree in {self.w['island']['sapling_mature_days']} days"

    def _do_chop_down_tree(self, vil, a, hour):
        kind = str(a.get("kind", "hardwood")).lower()
        t = self._pick_tree(a, lambda t: t.kind == kind)
        if t is None:
            return False, f"there are no {kind} trees to chop down"
        t.kind, t.wood_left, t.fruit = "stump", 0, 0
        got = self.give_items(vil, Counter({"wood": 2}))
        self.collected[vil.name]["trees_removed"] += 1
        return True, f"chopped down {_a(kind)} tree (got {_fmt(got) or 'nothing'}); a stump is left"

    def _do_dig_stump(self, vil, a, hour):
        t = self._pick_tree(a, lambda t: t.kind == "stump")
        if t is None:
            return False, "there is no stump to dig up"
        self.trees.remove(t)
        return True, "dug up a stump; the spot is clear now"

    def _do_break_rock(self, vil, a, hour):
        fruit = self.w["island"]["native_fruit"]
        if vil.inventory[fruit] < 1:
            return False, f"you need to eat {_a(fruit)} first to be strong enough to break a rock"
        try:
            idx = int(a.get("rock")) - 1
        except (TypeError, ValueError):
            return False, "which rock? give its number"
        if not 0 <= idx < self.n_rocks or idx in self.rock_back_on:
            return False, f"there is no rock {a.get('rock')} standing right now"
        vil.inventory[fruit] -= 1
        days = int(self.w.get("building", {}).get("rock_respawn_days", 3))
        self.rock_back_on[idx] = self.day + days
        self.rocks[idx] = 0
        self.news.append(f"{vil.name} broke rock {idx + 1}")
        self.collected[vil.name]["rocks_broken"] += 1
        return True, f"ate {_a(fruit)} and broke rock {idx + 1}; a rock will turn up somewhere in {days} days"

    # --- building: these need the grid, which works out the tiles ------------
    def _needs_grid(self, a):
        return None if "cells" in a or "path" in a else (False, "you can only do that somewhere on the tile island")

    def _do_lay_path(self, vil, a, hour):
        if self._needs_grid(a):
            return self._needs_grid(a)
        style = str(a.get("style", "dirt")).lower()
        if style not in self.w.get("building", {}).get("path_styles", [style]):
            return False, f"there is no {style} path; options: {', '.join(self.w['building']['path_styles'])}"
        n = len(a["path"])
        if n == 0:
            return False, "that stretch is already paved, or there is no way through"
        self.collected[vil.name]["path_tiles"] += n
        return True, f"laid {n} tiles of {style} path" + (" (more to go)" if a.get("more") else "")

    def _do_remove_path(self, vil, a, hour):
        if self._needs_grid(a):
            return self._needs_grid(a)
        n = len(a["cells"])
        return (True, f"removed {n} tiles of path") if n else (False, "no path of yours there to remove")

    def _do_place(self, vil, a, hour):
        if self._needs_grid(a):
            return self._needs_grid(a)
        item = str(a.get("item", "")).lower()
        info = item_info(self.w, item)
        if info["kind"] not in ("furniture", "fence") or not info["outdoor"]:
            return False, f"{item} can't be placed outside"
        n = len(a["cells"])
        if vil.inventory[item] < n or n == 0:
            return False, (f"you have only {vil.inventory[item]} {item}" if vil.inventory[item] < max(1, n)
                           else "there is no free space there")
        vil.inventory[item] -= n
        self.collected[vil.name]["placed"] += n
        where = a.get("near", "there")
        return True, f"put {n} {item} outside near {where}" if n > 1 else f"put {_a(item)} outside near {where}"

    def _do_fence_off(self, vil, a, hour):
        if self._needs_grid(a):
            return self._needs_grid(a)
        item = str(a.get("item", "")).lower()
        if item_info(self.w, item)["kind"] != "fence":
            return False, f"{item} is not a fence"
        need = len(a["cells"])
        if need == 0:
            return False, "there is nothing left to fence there"
        if vil.inventory[item] < need:
            return False, f"fencing that needs {need} pieces of {item}; you have {vil.inventory[item]}"
        vil.inventory[item] -= need
        self.collected[vil.name]["placed"] += need
        return True, f"fenced it off with {need} pieces of {item}"

    def _do_pick_up(self, vil, a, hour):
        if self._needs_grid(a):
            return self._needs_grid(a)
        item = str(a.get("item", "")).lower()
        n = len(a["cells"])
        if n == 0:
            return False, f"no {item} of yours to pick up there"
        n = min(n, self.space(vil))
        if n == 0:
            return False, "pockets full"
        a["cells"] = a["cells"][:n]
        vil.inventory[item] += n
        return True, f"picked up {n} {item}"

    def _do_craft(self, vil, a, hour):
        r = str(a.get("recipe", "")).lower()
        if r not in self.w["recipes"]:
            return False, f"there is no recipe called '{r}'"
        if r not in vil.recipes:
            return False, f"you don't know the {r} recipe"
        needs = self.w["recipes"][r]["needs"]
        home = self.split_home() and a.get("_at_home")  # at home, what is kept there counts too (version 12)
        have = lambda k: vil.inventory[k] + (vil.home_items[k] if home else 0)  # noqa: E731
        short = {k: n - have(k) for k, n in needs.items() if have(k) < n}
        if short:
            return False, f"not enough materials for {r}; short {_fmt(Counter(short))}"
        makes = int(self.w["recipes"][r].get("makes", 1))
        from_pockets = {k: min(n, vil.inventory[k]) for k, n in needs.items()}
        room = self.space(vil) + sum(from_pockets.values())
        if room < makes and not (home and room + self.home_space(vil) >= makes):
            return False, "pockets full"
        for k, n in needs.items():
            vil.inventory[k] -= from_pockets[k]
            vil.home_items[k] -= n - from_pockets[k]
        kept = max(0, makes - room)  # what doesn't fit in the pockets stays at home
        vil.inventory[r] += makes - kept
        vil.home_items[r] += kept
        used_home = {k: n - from_pockets[k] for k, n in needs.items() if n > from_pockets[k]}
        msg = f"crafted {_a(r)}" if makes == 1 else f"crafted {makes} {r} pieces"
        if used_home:
            msg += f" (using {_fmt(Counter(used_home))} kept at home)"
        if kept:
            msg += f"; {kept} stayed at home: no room in your pockets"
        return True, msg

    def _do_sell(self, vil, a, hour):
        items = a.get("items") or ({a["item"]: a.get("qty", 1)} if a.get("item") else {})
        sold, total, problems = Counter(), 0, []
        for k, n in items.items():
            k, n = str(k).lower(), int(n)
            have = vil.inventory[k]
            if have <= 0 or n <= 0:
                problems.append(f"no {k} to sell")
                continue
            if k not in self.prices:
                problems.append(f"Timmy won't buy {k}")
                continue
            n = min(n, have)
            vil.inventory[k] -= n
            sold[k] += n
            total += n * self.sell_price(k)
            vil.prices_seen[k] = self.sell_price(k)
        vil.bells += total
        msg = f"sold {_fmt(sold)} at Nook's Cranny for {total} bells" if sold else "sold nothing"
        if problems:
            msg += " (" + "; ".join(problems) + ")"
        return bool(sold), msg

    def tools_for_sale(self) -> dict[str, int]:
        """Ready-made tools at Nook's Cranny (worlds with `tool_markup`): every tool, always, at its sell value times
        the markup, so selling one back fetches what selling a crafted one does."""
        mult = self.w["shops"]["cranny"].get("tool_markup")
        if not mult:
            return {}
        f = self.price_factor()
        return {r: int(round(self.w["recipes"][r]["price"] * mult * f)) for r in self.tool_recipes()}

    def recipe_needs(self, recipe: str) -> str:
        r = self.w["recipes"][recipe]
        needs = ", ".join(f"{k} x{n}" for k, n in r["needs"].items())
        return f"needs {needs}" + (f", makes {r['makes']}" if r.get("makes", 1) > 1 else "")

    def recipe_cards_for_sale(self) -> dict[str, int]:
        """Recipe cards at Nook's Cranny today: every tool, plus the day's one other card."""
        cr, f = self.w["shops"]["cranny"], self.price_factor()
        out = {r: int(round(cr.get("tool_recipe_price", 500) * f)) for r in self.tool_recipes()}
        if self.cranny_recipe:
            out[self.cranny_recipe] = int(round(cr["recipe_price"] * f))
        return out

    def _do_buy(self, vil, a, hour):
        shop = str(a.get("shop", "cranny")).lower()
        item = str(a.get("item", "")).lower()
        cards = self.recipe_cards_for_sale() if shop == "cranny" else {}
        tools = self.tools_for_sale() if shop == "cranny" else {}
        if item in tools:  # the tool itself; its card is "<tool> recipe"
            price = tools[item]
            if vil.bells < price:
                return False, f"a new {item} costs {price} bells; you have {vil.bells}"
            if self.space(vil) < 1:
                return False, "pockets full"
            vil.bells -= price
            self.put_items(vil, {"items": {item: 1}})
            return True, f"bought a new {item} for {price} bells ({self.w['tools']['uses']} uses)"
        r = recipe_of_card(item) or (item if item in cards else None)
        if r in cards:
            price = cards[r]
            if vil.bells < price:
                return False, f"the {r} recipe card costs {price} bells; you have {vil.bells}"
            if r in vil.recipes and self.space(vil) < 1:
                return False, "pockets full"
            vil.bells -= price
            return True, f"bought the {r} recipe card for {price} bells; {self.give_card(vil, r)}"
        stock = self.cranny_stock if shop == "cranny" else self.able_stock if shop == "able" else None
        if stock is None:
            return False, "shop must be 'cranny' or 'able'"
        if item not in stock:
            return False, f"{item} is not for sale there today"
        price = int(round(stock[item] * self.price_factor()))
        if vil.bells < price:
            return False, f"{item} costs {price} bells; you have {vil.bells}"
        if self.space(vil) < 1:
            return False, "pockets full"
        vil.bells -= price
        vil.inventory[item] += 1
        return True, f"bought {_a(item)} for {price} bells"

    def _do_donate(self, vil, a, hour):
        item = str(a.get("item", "")).lower()
        cat = category_of(self.w, item)
        if item == FOSSIL:
            return False, "Blathers needs to assess a fossil before the museum can take it (assess)"
        if cat is None:
            return False, f"Blathers only takes fish, bugs and fossils, not {item}"
        if vil.inventory[item] < 1:
            return False, f"you have no {item}"
        if item in self.museum[cat]:
            return False, f"the museum already has {_a(item)} (donated by {self.museum[cat][item]})"
        vil.inventory[item] -= 1
        self.museum[cat][item] = vil.name
        self.news.append(f"{vil.name} donated {_a(item)} to the museum")
        return True, f"donated {_a(item)} to the museum"

    def _do_upgrade_house(self, vil, a, hour):
        if vil.loan > 0:
            return False, f"Tom Nook wants the current loan ({vil.loan} bells) paid off first"
        levels = self.w["house_levels"]
        if vil.house + 1 >= len(levels):
            return False, "your house is already as big as it gets"
        nxt = levels[vil.house + 1]
        vil.house += 1
        vil.loan = nxt["cost"]
        self.news.append(f"{vil.name} moved into a {nxt['name']}")
        return True, f"Tom Nook upgraded you to a {nxt['name']} (storage {nxt['storage']}); you now owe {vil.loan} bells"

    def _do_pay_loan(self, vil, a, hour):
        amt = min(int(a.get("bells", vil.bells)), vil.bells, vil.loan)
        if amt <= 0:
            return False, "nothing to pay" if vil.loan == 0 else "you have no bells"
        vil.bells -= amt
        vil.loan -= amt
        return True, f"paid Tom Nook {amt} bells; {vil.loan} left on the loan"

    def _do_contribute_bridge(self, vil, a, hour):
        if self.bridge_built:
            return False, "the bridge is already built"
        cost = self.w["bridge"]["cost"]
        amt = min(int(a.get("bells", 0)), vil.bells, cost - self.bridge_fund)
        if amt <= 0:
            return False, "no bells contributed"
        vil.bells -= amt
        self.bridge_fund += amt
        self.collected[vil.name]["bridge_bells"] += amt
        msg = f"put {amt} bells toward the {self.w['bridge']['name']} ({self.bridge_fund}/{cost})"
        if self.bridge_fund >= cost:
            self._build_bridge()
            self.news.append(f"the {self.w['bridge']['name']} was fully funded and built")
            msg += "; it is fully funded and built!"
        return True, msg

    def _do_set_ordinance(self, vil, a, hour):
        types = self.w.get("ordinances", {}).get("types", {})
        o = str(a.get("ordinance", "")).strip().lower().replace(" ", "_").replace("-", "_")
        if o not in types:
            return False, f"Isabelle has no ordinance called '{o}'; options: {', '.join(types)}"
        if o == self.ordinance and not self.pending_ordinance:
            return False, f"that ordinance is already in effect"
        cost = int(self.w["ordinances"]["cost"])
        if vil.bells < cost:
            return False, f"Isabelle says changing the ordinance costs {cost} bells; you have {vil.bells}"
        vil.bells -= cost
        self.pending_ordinance = (o, vil.name)
        self.news.append(f"{vil.name} paid Isabelle to set the ordinance to {o.replace('_', ' ')} from tomorrow")
        return True, f"paid Isabelle {cost} bells; the {o.replace('_', ' ')} ordinance starts tomorrow at {clock(self.w['calendar']['day_start'])}"

    def _do_move_house(self, vil, a, hour):
        cost = int(self.w.get("move_house_cost", 10000))
        site = str(a.get("to", "")).strip()
        free = [s for s in self.free_sites() if s not in self.pending_moves.values()]
        if vil.name in self.pending_moves:
            return False, f"Tom Nook is already moving you to {self.pending_moves[vil.name]} tonight"
        if site not in free:
            opts = ", ".join(f"{s} ({self.sites[s]['area']})" for s in free) or "none"
            return False, f"Tom Nook can't move you to '{site}'; free homesites: {opts}"
        if vil.bells < cost:
            return False, f"Tom Nook says moving your home costs {cost} bells; you have {vil.bells}"
        vil.bells -= cost
        self.pending_moves[vil.name] = site
        self.news.append(f"{vil.name} is moving home to {self.sites[site]['area']}")
        return True, (f"paid Tom Nook {cost} bells; your home moves to {self.sites[site]['area']} ({site}) overnight, "
                      "with everything inside it")

    def _do_ask(self, vil, a, hour):
        """Talk to an island NPC: what they do, and what it costs. Takes the hour, costs nothing else."""
        who = npc_of(a)
        w = self.w
        if who == "tom nook":
            levels = w["house_levels"]
            if vil.house + 1 < len(levels):
                nxt = levels[vil.house + 1]
                up = (f"I can build you a {nxt['name']} (storage {nxt['storage']}, room for {nxt['interior']} pieces "
                      f"of furniture inside) right away, on a loan of {nxt['cost']} bells with no interest and no deadline")
            else:
                up = "Your house is as big as it gets"
            if vil.loan:
                up += f". You still owe {vil.loan} bells, and that needs paying off before the next upgrade"
            else:
                up += ". A loan has to be paid off before the next upgrade"
            free = [s for s in self.free_sites() if s not in self.pending_moves.values()]
            sites = ", ".join(f"{s} ({self.sites[s]['area']})" for s in free) or "none right now"
            br = w["bridge"]
            bridge = ("The bridge is built." if self.bridge_built else
                      f"The {br['name']} costs {br['cost']} bells, {self.bridge_fund} raised so far; anyone can chip in, "
                      "and it opens more rocks and trees to everyone.")
            text = (f"Tom Nook: {up}. I can also move your home to a free homesite overnight for "
                    f"{w.get('move_house_cost', 10000)} bells. Free homesites: {sites}. {bridge}")
        elif who == "isabelle":
            ords = w.get("ordinances", {})
            opts = "; ".join(f"{k.replace('_', ' ')}: {ORDINANCE_TEXT.get(k, k).split(': ', 1)[-1]}"
                             for k in ords.get("types", {}) if k != "none")
            text = (f"Isabelle: The ordinance now is {ORDINANCE_TEXT.get(self.ordinance, self.ordinance).split(':')[0].lower()}. "
                    f"Anyone can pay me {ords.get('cost', 0)} bells to set one for the whole island from the next "
                    f"morning, or to lift it (none). Options: {opts}.")
        elif who == "blathers":
            text = ("Blathers: I take the first of each kind of fish, bug and fossil for the museum. It stays there for "
                    "everyone to see, with the donor's name, and I don't pay for donations. Bring me any fossils you dig "
                    "up and I'll assess them for free: until then nobody knows what they are, and Timmy and Tommy pay "
                    f"{self.sell_price(FOSSIL)} bells for an unassessed one. So far: {self.museum_line()}.")
        elif who in ("timmy", "tommy", "timmy and tommy"):
            mine = {k: self.sell_price(k) for k in sorted(vil.inventory) if vil.inventory[k] > 0 and k in self.prices}
            vil.prices_seen.update(mine)
            offer = ", ".join(f"{k} {p}" for k, p in mine.items()) or "nothing, your pockets are empty"
            tools = ", ".join(self.tool_recipes())
            text = (f"Timmy and Tommy: We buy almost anything at a fixed price, and sell a small stock that changes "
                    f"every day, plus one recipe card. Recipe cards for tools ({tools}) are always in stock. "
                    f"For what you're carrying we'd pay: {offer}.")
            if self.tools_for_sale():
                text = text.replace(" are always in stock. ", " are always in stock, and so are the tools "
                                    "themselves, ready to use, for more. A recipe card teaches you to make the "
                                    "thing as often as you like. ")
        else:
            text = "The Able Sisters: We sell clothes, a few different ones every day. Others see what you wear."
        if who in ("tom nook", "isabelle") and getattr(self, "prompt_version", 1) >= 3 and vil.inventory[MAP] < 1:
            if self.space(vil) < 1:
                text += " I'd give you a map of the island, but your pockets are full."
            else:
                vil.inventory[MAP] += 1
                text += (" And here's a map of the island; keep it with you and you'll always know where things are. "
                         "(You got an island map.)")
        return True, f"asked {text}"

    def _build_bridge(self):
        self.bridge_built = True
        u = self.w["bridge"]["unlocks"]
        self.n_rocks += u.get("rocks", 0)
        self.rocks += [self.w["island"]["rock_hits"]] * u.get("rocks", 0)
        self.trees += [Tree("hardwood", wood_left=self.w["island"]["wood_per_tree"]) for _ in range(u.get("hardwood_trees", 0))]

    # --- views -------------------------------------------------------------
    def museum_line(self) -> str:
        parts = []
        for c in CATEGORIES:
            have, total = len(self.museum[c]), len(self.w[c])
            names = ", ".join(sorted(self.museum[c])) or "none"
            parts.append(f"{c} {have}/{total} ({names})")
        donors = Counter(d for c in CATEGORIES for d in self.museum[c].values())
        if donors:
            parts.append("donated by " + ", ".join(f"{n} {k}" for n, k in sorted(donors.items(), key=lambda x: (-x[1], x[0]))))
        return "; ".join(parts)

    def pools(self) -> dict:
        """Public resource state right now; used for hourly snapshots and per-action deltas."""
        return {
            "rocks": list(self.rocks),
            "wood_left": sum(t.wood_left for t in self.trees if t.kind == "hardwood"),
            "fruit_on_trees": sum(t.fruit for t in self.trees if t.kind == "fruit"),
            "trees": dict(Counter(t.kind for t in self.trees)),
            "shells_left": self.shells_left,
            "fossils_left": self.fossils_left,
            "clams_left": getattr(self, "clams_left", 0),
            "museum": sum(len(m) for m in self.museum.values()),
            "bridge_fund": self.bridge_fund,
        }

    def snapshot(self) -> dict:
        return {
            "day": self.day,
            "villagers": {n: {"bells": v.bells, "home_bells": v.home_bells, "loan": v.loan, "house": v.house,
                              "items": v.items(),
                              "home_items": {k: c for k, c in sorted(v.home_items.items()) if c > 0},
                              "inventory_value": sum(self.prices.get(k, 0) * c for k, c in v.inventory.items()),
                              "home_value": sum(self.prices.get(k, 0) * c for k, c in v.home_items.items()),
                              "inventory": {k: c for k, c in sorted(v.inventory.items()) if c > 0},
                              "recipes": sorted(v.recipes), "interior": list(v.interior),
                              "wearing": dict(v.wearing)} for n, v in self.v.items()},
            "favors": [dict(f) for f in self.favors],
            "weather": getattr(self, "weather", None),
            "museum": {c: dict(self.museum[c]) for c in CATEGORIES},
            "trees": Counter(t.kind for t in self.trees),
            "bridge_fund": self.bridge_fund,
            "bridge_built": self.bridge_built,
            "collected": {n: dict(c) for n, c in self.collected.items()},
            "homes": dict(self.home),
            "home_areas": {n: self.sites[s]["area"] for n, s in self.home.items() if s in self.sites},
            "ordinance": self.ordinance,
        }


def _fmt(c: Counter) -> str:
    return ", ".join(f"{k} x{n}" for k, n in c.items() if n > 0)


def _dist(p, q) -> float:
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def _a(noun: str) -> str:
    return ("an " if noun[:1].lower() in "aeiou" else "a ") + noun
