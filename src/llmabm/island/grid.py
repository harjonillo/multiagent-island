"""A tile-grid island in the style of ACNH, and the Space that walks on it.

Generation (seeded, so the same seed always gives the same island):
  sea margin -> beach ring -> grass on three cliff tiers (higher toward the north)
  -> a river from a clifftop source that splits into two mouths -> a pond
  -> buildings (Resident Services, plaza, museum, Nook's Cranny, Able Sisters, airport)
  -> homesites on the lower tier -> trees and rocks with ACNH-like spacing.

Movement:
  Walk on beach and grass. Trees, rocks and buildings block their tile.
  Changing cliff tier needs a ladder (one tier at a time).
  Crossing the river needs a vaulting pole (jumping up to `pole_max_width` water tiles)
  or a bridge. Pond and sea are never crossable. From prompt version 17 villagers can
  propose more bridges, and stairs, which climb one cliff edge without a ladder.

Rules marked ASSUMPTION in docs/grid_spec.md are from memory of the game and meant
to be checked.
"""

from __future__ import annotations

import heapq
import math
import random
import re
from collections import Counter, deque

from .space import Space

SEA, SAND, GRASS, RIVER, POND, BRIDGE, ROCKY = range(7)
WATER = {SEA, RIVER, POND}
TIER_NAME = {1: "lower ground", 2: "the middle cliff", 3: "the top cliff"}
Cell = tuple[int, int]
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
N8 = N4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))


def _wave(rng: random.Random, amp: float, n: int = 3):
    """Smooth 1-D noise as a sum of a few sines."""
    parts = [(amp * rng.uniform(0.3, 1.0) / (k + 1), rng.uniform(0.02, 0.09) * (k + 1), rng.uniform(0, 6.28))
             for k in range(n)]
    return lambda t: sum(a * math.sin(f * t + p) for a, f, p in parts)


class GridSpace(Space):
    def __init__(self, world: dict, seed: int = 0):
        cfg = {**DEFAULTS, **(world.get("grid") or {})}
        self.cfg = cfg
        self.W, self.H = int(cfg["width"]), int(cfg["height"])
        self.rng = random.Random(f"grid|{seed}|{cfg.get('layout_seed', 0)}")
        self.terrain = [[SEA] * self.W for _ in range(self.H)]
        self.tier = [[0] * self.W for _ in range(self.H)]
        self.blocked: set[Cell] = set()          # buildings, trees, rocks
        self.fixtures: set[Cell] = set()         # every building (the plaza too) and homesite, for spacing
        self.buildings: dict[str, dict] = {}     # name -> {cells, door}
        self.sites: dict[str, dict] = {}         # homesite id -> {cells, door, area}
        self.rock_cells: list[Cell | None] = []   # index = rock number - 1; None while a broken rock is gone
        self.tree_cells: set[Cell] = set()
        self.paths: dict[Cell, dict] = {}          # tile -> {style, owner}
        self.placed: dict[Cell, dict] = {}         # tile -> {id, item, kind, owner}; furniture and fences
        self.flowers: dict[Cell, dict] = {}        # tile -> {kind, owner, bloom_day}
        self._next_obj = 0
        self.shells: list[Cell] = []
        self.dig_spots: list[Cell] = []
        self.bridge_cells: list[Cell] = []
        self.bridge_built = False
        self._cache: dict = {}
        self._version = 0
        self.path_cost = float((world.get("building") or {}).get("path_step_cost", 1.0))
        self.building = dict(world.get("building") or {})
        self._generate()

    # --- generation --------------------------------------------------------
    def _generate(self) -> None:
        W, H, rng, cfg = self.W, self.H, self.rng, self.cfg
        m = cfg["sea_margin"]
        left, right, top, bot = (_wave(rng, 2.5) for _ in range(4))
        for y in range(H):
            for x in range(W):
                if (m + left(y) <= x < W - m + right(y)) and (m + top(x) <= y < H - m + bot(x)):
                    self.terrain[y][x] = GRASS
        # Beach: land within beach_width of the sea.
        dist = self._distance_from({(x, y) for y in range(H) for x in range(W) if self.terrain[y][x] == SEA}, N8)
        for y in range(H):
            for x in range(W):
                if self.terrain[y][x] == GRASS and dist[y][x] <= cfg["beach_width"]:
                    self.terrain[y][x] = SAND
        # Cliff tiers: higher toward the north.
        y2, y3 = _wave(rng, 4), _wave(rng, 3)
        band_l, band_r = _wave(rng, 4), _wave(rng, 4)
        for y in range(H):
            for x in range(W):
                if self.terrain[y][x] == SEA:
                    continue
                t = 1
                if self.terrain[y][x] == GRASS and y < H * cfg["tier2_line"] + y2(x):
                    t = 2
                    if y < H * cfg["tier3_line"] + y3(x) and W * 0.22 + band_l(y) < x < W * 0.78 + band_r(y):
                        t = 3
                self.tier[y][x] = t
        self._smooth_tiers()
        self._carve_river()
        self._carve_pond()
        self._place_buildings()
        self._place_homesites()
        self._find_bridge_site()

    def _smooth_tiers(self) -> None:
        for _ in range(2):
            new = [row[:] for row in self.tier]
            for y in range(1, self.H - 1):
                for x in range(1, self.W - 1):
                    if self.terrain[y][x] != GRASS:
                        continue
                    votes = [self.tier[y + dy][x + dx] for dx, dy in N8 if self.terrain[y + dy][x + dx] == GRASS]
                    if votes:
                        top = max(set(votes), key=votes.count)
                        if votes.count(top) >= 5:
                            new[y][x] = top
            self.tier = new

    def _paint_water(self, x: int, y: int, kind: int) -> None:
        for dx in (0, 1):
            for dy in (0, 1):
                cx, cy = x + dx, y + dy
                if 0 <= cx < self.W and 0 <= cy < self.H and self.terrain[cy][cx] != SEA:
                    self.terrain[cy][cx] = kind

    def _carve_river(self) -> None:
        W, H, rng = self.W, self.H, self.rng
        x = int(W / 2 + rng.randint(-8, 8))
        y = self.cfg["sea_margin"] + self.cfg["beach_width"] + 3
        self.river_source = (x, y)
        # ASSUMPTION: a rocky stretch of coast cuts the north beach in line with the river's
        # source, so the beach is not a way around the river. (ACNH beaches have rocky
        # outcrops that block walking along the shore.)
        for yy in range(0, int(H * 0.35)):
            for xx in range(x - 2, x + 4):
                if 0 <= xx < W and self.terrain[yy][xx] == SAND:
                    self.terrain[yy][xx] = ROCKY
        split = int(H * 0.6 + rng.randint(-3, 3))
        while y < split:
            self._paint_water(x, y, RIVER)
            y += 1
            x = max(W // 4, min(3 * W // 4, x + rng.choice((-1, 0, 0, 0, 1))))
        for direction in (-1, 1):
            bx, by = x, y
            for _ in range(W * 2):
                self._paint_water(bx, by, RIVER)
                if self.terrain[by][bx] == SEA or not (1 < bx < W - 2 and 1 < by < H - 2):
                    break
                if rng.random() < 0.55:
                    bx += direction
                else:
                    by += 1
                if any(self.terrain[by + dy][bx + dx] == SEA for dx in (0, 1, 2) for dy in (0, 1, 2)
                       if 0 <= by + dy < H and 0 <= bx + dx < W):
                    self._paint_water(bx, by, RIVER)
                    break

    def _carve_pond(self) -> None:
        river = [(x, y) for y in range(self.H) for x in range(self.W) if self.terrain[y][x] == RIVER]
        far = self._distance_from(set(river), N8)
        cands = [(x, y) for y in range(self.H) for x in range(self.W)
                 if self.terrain[y][x] == GRASS and self.tier[y][x] == 1 and far[y][x] > 8 and y > self.H * 0.55]
        if not cands:
            return
        cx, cy = self.rng.choice(cands)
        for y in range(cy - 3, cy + 4):
            for x in range(cx - 4, cx + 5):
                if 0 <= x < self.W and 0 <= y < self.H and ((x - cx) / 3.5) ** 2 + ((y - cy) / 2.5) ** 2 <= 1 \
                        and self.terrain[y][x] == GRASS:
                    self.terrain[y][x] = POND
                    self.tier[y][x] = 1

    def _footprint_ok(self, x0: int, y0: int, w: int, h: int, tiers=(1,), clearance: int = 1) -> bool:
        for y in range(y0 - clearance, y0 + h + clearance + 1):
            for x in range(x0 - clearance, x0 + w + clearance):
                if not (0 <= x < self.W and 0 <= y < self.H):
                    return False
                if (self.terrain[y][x] != GRASS or self.tier[y][x] not in tiers or (x, y) in self.blocked
                        or (x, y) in self.fixtures):
                    return False
        return True

    def _spiral(self, cx: float, cy: float):
        cx, cy = int(cx), int(cy)
        for r in range(0, max(self.W, self.H)):
            for dx in range(-r, r + 1):
                for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                    yield cx + dx, cy + dy

    def _place(self, name: str, w: int, h: int, at: tuple[float, float], tiers=(1,), blocking=True) -> dict | None:
        for x0, y0 in self._spiral(at[0] * self.W, at[1] * self.H):
            if self._footprint_ok(x0, y0, w, h, tiers):
                cells = [(x, y) for y in range(y0, y0 + h) for x in range(x0, x0 + w)]
                self.fixtures.update(cells)
                if blocking:
                    self.blocked.update(cells)
                door = (x0 + w // 2, y0 + h)  # the tile in front of the entrance
                return {"cells": cells, "door": door, "blocking": blocking}
        return None

    def _place_buildings(self) -> None:
        for name, spec in self.cfg["buildings"].items():
            b = self._place(name, spec["size"][0], spec["size"][1], spec["near"], blocking=spec.get("blocking", True))
            if b:
                if not spec.get("blocking", True):
                    b["door"] = b["cells"][len(b["cells"]) // 2]
                self.buildings[name] = b

    def _place_homesites(self) -> None:
        n = int(self.cfg["homesites"])
        rng = self.rng
        tries = 0
        while len(self.sites) < n and tries < 5000:
            tries += 1
            x0, y0 = rng.randrange(self.W), rng.randrange(int(self.H * 0.35), self.H)
            if not self._footprint_ok(x0, y0, 5, 4, tiers=(1,), clearance=2):
                continue
            centre = (x0 + 2.5, y0 + 2)
            if any(math.dist(centre, s["centre"]) < self.cfg["homesite_spacing"] for s in self.sites.values()):
                continue
            if any(math.dist(centre, b["door"]) < 6 for b in self.buildings.values()):
                continue
            cells = [(x, y) for y in range(y0, y0 + 4) for x in range(x0, x0 + 5)]
            self.blocked.update(cells)
            self.fixtures.update(cells)
            sid = f"site-{len(self.sites) + 1}"
            door = (x0 + 2, y0 + 4)
            self.sites[sid] = {"cells": cells, "door": door, "centre": centre, "area": self.region(door)}

    def _find_bridge_site(self) -> None:
        """A short straight crossing over the lower river, near Resident Services."""
        target = self.buildings.get("Resident Services", {}).get("door", (self.W // 2, self.H // 2))
        best = None
        for y in range(self.H):
            for x in range(1, self.W - 1):
                if self.terrain[y][x] == RIVER and self.tier[y][x] == 1 and self.terrain[y][x - 1] in (GRASS, SAND):
                    run = []
                    cx = x
                    while cx < self.W and self.terrain[y][cx] == RIVER:
                        run.append((cx, y))
                        cx += 1
                    if cx < self.W and self.terrain[y][cx] in (GRASS, SAND) and len(run) <= 4:
                        d = math.dist((x, y), target)
                        if best is None or d < best[0]:
                            best = (d, run)
        self.bridge_cells = best[1] if best else []

    # --- bridges and stairs from villagers (prompt version 17) ------------------
    # Footprints: a bridge is 3 tiles wide and as long as the water it crosses plus a landing tile on each bank;
    # stairs are 2 tiles wide and 4 long, two rows below a cliff edge and two above. From the moment one is
    # proposed its footprint is a construction site: walkable, but nothing can be planted or put on it.
    BRIDGE_WIDE, STAIRS_WIDE, STAIRS_LONG = 3, 2, 4

    def stair_steps(self) -> set:
        """Built stairs, as the steps they make walkable in both directions: {(lower tile, upper tile), ...}."""
        return self.__dict__.setdefault("stairs", set())

    def first_bridge_cells(self, island) -> list[Cell]:
        """The first bridge's footprint. From prompt version 17 it is 3 wide with a landing on each bank, like
        any bridge; before, the strip of river tiles the generator picked."""
        if getattr(island, "prompt_version", 1) < 17 or not self.bridge_cells:
            return list(self.bridge_cells)
        xs, y = [c[0] for c in self.bridge_cells], self.bridge_cells[0][1]
        return [(x, y + dy) for dy in (-1, 0, 1) for x in range(min(xs) - 1, max(xs) + 2)
                if 0 <= x < self.W and 0 <= y + dy < self.H]

    def structure_tiles(self, island) -> set[Cell]:
        """Every tile under a bridge or stairs, built or proposed (version 17; none before)."""
        if getattr(island, "prompt_version", 1) < 17:
            return set()
        out = set(self.first_bridge_cells(island))
        for st in getattr(island, "projects", None) or []:
            out |= {tuple(c) for c in st["cells"]}
        return out

    def _clear(self, c: Cell, taken: set) -> bool:
        """A tile a footprint can cover: `taken` (structure_spots) holds what is on tiles, homes, buildings and doors."""
        return c not in self.blocked and c not in taken

    def structure_spots(self, island, kind: str, around: Cell | None = None, radius: float | None = None) -> list[dict]:
        """Where a bridge or stairs could go, nearest to `around` first (centre within `radius` tiles, if given).
        Each spot is {"cells": its footprint, "water": tiles that become bridge, "steps": the stair steps,
        "ends": the tiles you can stand on to propose it, "at": its centre}. A bridge's middle lane crosses at
        most `bridge_span` lower-river tiles, with land at both ends; the side lanes need land at both ends too, and
        in between, river (which becomes bridge) or land where the bank bends. Every land tile has to be clear, and
        the footprint at least `spacing` tiles from any other bridge or stairs."""
        from .world import structures_cfg

        # Everyone plans at about the same time with the island unchanged: the full list is kept until it changes.
        key = (kind, island.day, self._version, len(self.flowers), len(self.placed), len(self.paths),
               len(getattr(island, "projects", None) or []), getattr(island, "prompt_version", 1))
        cache = self.__dict__.setdefault("_spot_cache", {})
        if key not in cache:
            cache.clear() if len(cache) > 8 else None
            cache[key] = self._structure_spots(island, kind, structures_cfg(island.w))
        out = cache[key]
        if around is not None:
            out = sorted((sp for sp in out if radius is None or math.dist(sp["at"], around) <= radius),
                         key=lambda sp: math.dist(sp["at"], around))
        return list(out)

    def _structure_spots(self, island, kind: str, cfg: dict) -> list[dict]:
        others = self.structure_tiles(island)
        taken = ((self._occupied(island) - set(self.paths)) | getattr(self, "fixtures", set())  # paths are fine
                 | {s["door"] for s in self.sites.values()} | {b["door"] for b in self.buildings.values()})
        apart = lambda cells: all(math.dist(c, t) > cfg["spacing"] for c in cells for t in others)  # noqa: E731
        inside = lambda c: 0 <= c[0] < self.W and 0 <= c[1] < self.H  # noqa: E731
        land = lambda c: inside(c) and self.terrain[c[1]][c[0]] in (GRASS, SAND) and self._level(c) == 1  # noqa: E731
        water = lambda c: inside(c) and self.terrain[c[1]][c[0]] == RIVER and self.tier[c[1]][c[0]] == 1  # noqa: E731
        out = []
        if kind == "bridge":
            half = self.BRIDGE_WIDE // 2
            for y in range(self.H):
                for x in range(self.W):
                    for (dx, dy), (px, py) in (((1, 0), (0, 1)), ((0, 1), (1, 0))):
                        start = (x - dx, y - dy)  # the landing on the near bank, centre lane
                        if not (water((x, y)) and land(start)):
                            continue
                        k = 0
                        while water((x + k * dx, y + k * dy)) and k <= cfg["bridge_span"]:
                            k += 1
                        if k > cfg["bridge_span"]:
                            continue
                        lanes = [[(start[0] + i * dx + j * px, start[1] + i * dy + j * py) for i in range(k + 2)]
                                 for j in range(-half, half + 1)]
                        if not all(land(ln[0]) and land(ln[-1]) and all(water(c) or land(c) for c in ln[1:-1])
                                   for ln in lanes):
                            continue
                        cells = [c for ln in lanes for c in ln]
                        dry = [c for c in cells if land(c)]
                        centre = lanes[half][len(lanes[half]) // 2]
                        if all(self._clear(c, taken) for c in dry) and apart(cells):
                            out.append({"cells": cells, "water": [c for c in cells if water(c)], "steps": [],
                                        "ends": [c for ln in lanes for c in (ln[0], ln[-1])], "at": centre})
        else:
            for y in range(self.H):
                for x in range(self.W):
                    for (dx, dy), (px, py) in (((1, 0), (0, 1)), ((-1, 0), (0, 1)), ((0, 1), (1, 0)), ((0, -1), (1, 0))):
                        # (x, y) is the lower tile at the edge in the first lane; (dx, dy) points up the cliff.
                        if not (inside((x, y)) and self.terrain[y][x] == GRASS):
                            continue
                        t = self.tier[y][x]
                        lanes = [[(x + i * dx + j * px, y + i * dy + j * py) for i in (-1, 0, 1, 2)]
                                 for j in range(self.STAIRS_WIDE)]
                        ok = all(inside(c) and self.terrain[c[1]][c[0]] == GRASS for ln in lanes for c in ln)
                        ok = ok and all(self.tier[ln[0][1]][ln[0][0]] == t and self.tier[ln[1][1]][ln[1][0]] == t
                                        and self.tier[ln[2][1]][ln[2][0]] == t + 1 and self.tier[ln[3][1]][ln[3][0]] == t + 1
                                        for ln in lanes)
                        cells = [c for ln in lanes for c in ln]
                        if (ok and all(self._clear(c, taken) for c in cells)
                                and apart(cells)):
                            out.append({"cells": cells, "water": [], "steps": [[ln[1], ln[2]] for ln in lanes],
                                        "ends": cells, "at": lanes[0][1]})
        return out

    def structure_name(self, kind: str, cells) -> str:
        """"bridge across the river (lower ground, east)", "stairs up to the middle cliff (north)"."""
        cells = [tuple(c) for c in cells]
        if kind == "bridge":
            return f"bridge across the river ({self.region(cells[len(cells) // 2])})"
        top = max(cells, key=self._level)
        return f"stairs up to {TIER_NAME.get(self._level(top), 'the cliff')} ({self._area(top)})"

    # --- objects ------------------------------------------------------------
    def spot_ok_for_object(self, cell: Cell, others: set[Cell]) -> bool:
        """ASSUMPTION: trees and rocks need their 8 neighbours free of other trees, rocks, buildings
        (the plaza too) and homes, so every fixture has at least one free tile around it. They
        can't sit on an entrance, next to water or on a cliff edge."""
        x, y = cell
        if not (0 < x < self.W - 1 and 0 < y < self.H - 1):
            return False
        fixtures = getattr(self, "fixtures", set())
        if self.terrain[y][x] != GRASS or cell in self.blocked or cell in others or cell in fixtures:
            return False
        t = self.tier[y][x]
        for dx, dy in N8:
            n = (x + dx, y + dy)
            if self.terrain[n[1]][n[0]] in WATER or self.tier[n[1]][n[0]] != t:
                return False
            if n in others or n in self.blocked or n in fixtures:
                return False
        doors = {s["door"] for s in self.sites.values()} | {b["door"] for b in self.buildings.values()}
        return cell not in doors

    def attach(self, island) -> None:
        """Give every tree and rock on the island a tile, and pin them in place."""
        rng = self.rng
        taken = self._occupied(island)
        grass = [(x, y) for y in range(self.H) for x in range(self.W) if self.terrain[y][x] == GRASS]
        forest = _wave(random.Random(rng.random()), 1.0)
        weights = [1.0 + 0.8 * forest(x * 0.7 + y * 0.4) for x, y in grass]
        for t in island.trees:
            if getattr(t, "pos", None):
                continue
            for _ in range(400):
                c = rng.choices(grass, weights=weights)[0]
                if self.spot_ok_for_object(c, taken):
                    t.pos = c
                    taken.add(c)
                    self.blocked.add(c)
                    self.tree_cells.add(c)
                    break
        while len(self.rock_cells) < island.n_rocks:
            c = self._free_spot(grass, taken)
            if c is None:
                break
            self.rock_cells.append(c)
            taken.add(c)
            self.blocked.add(c)
        if not getattr(self, "_wild_done", False):
            self._wild_flowers(island, grass, taken)
        self._version += 1

    def _wild_flowers(self, island, grass: list, taken: set) -> None:
        """A few patches of wild flowers, as a new ACNH island has. Nobody owns them."""
        self._wild_done = True
        kinds = ["tulips", "cosmos", "pansies", "hyacinths"]
        for _ in range(int(self.cfg.get("wild_flower_patches", 6))):
            cx, cy = self.rng.choice(grass)
            kind = self.rng.choice(kinds)
            for _ in range(self.rng.randint(3, 6)):
                c = (cx + self.rng.randint(-2, 2), cy + self.rng.randint(-2, 2))
                if (0 <= c[0] < self.W and 0 <= c[1] < self.H and self.terrain[c[1]][c[0]] == GRASS
                        and c not in self.blocked and c not in taken and c not in self.flowers
                        and c not in self.fixtures):
                    self.flowers[c] = {"kind": kind, "owner": None, "bloom_day": 0}

    def _occupied(self, island) -> set:
        """Tiles with something on them. From prompt version 17 that includes every bridge and stairs footprint,
        built or proposed, so nothing is planted or put there (and no tree or rock turns up there)."""
        return ({c for c in self.rock_cells if c} | {t.pos for t in island.trees if getattr(t, "pos", None)}
                | set(self.placed) | set(self.paths) | set(self.flowers) | self.structure_tiles(island))

    def _free_spot(self, cells: list, taken: set, tries: int = 2000):
        for _ in range(tries):
            c = self.rng.choice(cells)
            if self.spot_ok_for_object(c, taken):
                return c
        return None

    def sync(self, island) -> None:
        """Called after world changes: new trees/rocks get tiles; removed trees free theirs;
        a funded bridge opens its crossing."""
        live = {t.pos for t in island.trees if getattr(t, "pos", None)}
        for c in self.tree_cells - live:
            self.blocked.discard(c)
            self._version += 1
        for c in live - self.tree_cells:
            self.blocked.add(c)
            self._version += 1
        self.tree_cells = set(live)
        if any(getattr(t, "pos", None) is None for t in island.trees) or len(self.rock_cells) < island.n_rocks:
            self.attach(island)
        if island.bridge_built and not self.bridge_built:
            for x, y in self.first_bridge_cells(island):  # from version 17, the river tiles of its 3-wide footprint
                if self.terrain[y][x] in (RIVER, BRIDGE):
                    self.terrain[y][x] = BRIDGE
            self.bridge_built = True
            self._version += 1
        done = self.__dict__.setdefault("structures_built", set())
        for st in getattr(island, "projects", None) or []:
            if st["built"] and st["id"] not in done:
                if st["kind"] == "bridge":
                    for x, y in st.get("water") or st["cells"]:
                        if self.terrain[y][x] == RIVER:
                            self.terrain[y][x] = BRIDGE
                else:
                    for lo, hi in st.get("steps") or [st["cells"][:2]]:
                        self.stair_steps().update({(tuple(lo), tuple(hi)), (tuple(hi), tuple(lo))})
                done.add(st["id"])
                self._version += 1

    def new_day(self, island, rng: random.Random) -> None:
        self.day = island.day
        for i in island.rocks_respawned:
            if i < len(self.rock_cells) and self.rock_cells[i] is None:
                grass = [(x, y) for y in range(self.H) for x in range(self.W) if self.terrain[y][x] == GRASS]
                c = self._free_spot(grass, self._occupied(island))
                if c:
                    self.rock_cells[i] = c
                    self.blocked.add(c)
                    self._version += 1
                    island.news.append(f"a new rock turned up on {self.region(c)} (rock {i + 1})")
        island.rocks_respawned = []
        sand = [(x, y) for y in range(self.H) for x in range(self.W) if self.terrain[y][x] == SAND]
        grass = [(x, y) for y in range(self.H) for x in range(self.W)
                 if self.terrain[y][x] == GRASS and (x, y) not in self.blocked]
        self.shells = rng.sample(sand, min(len(sand), island.shells_left))
        self.dig_spots = rng.sample(grass, min(len(grass), island.fossils_left))
        clams = getattr(island, "clams_left", 0)
        self.clam_spots = rng.sample(sand, min(len(sand), clams)) if clams else []
        # What each villager has seen is today's knowledge: shells and dig spots are new every morning.
        self.critters, self.known, self.looked, self.seen_ids = [], {}, {}, {}
        island.spotted = {}

    # --- movement -----------------------------------------------------------
    def walkable(self, c: Cell) -> bool:
        x, y = c
        return (0 <= x < self.W and 0 <= y < self.H and self.terrain[y][x] in (SAND, GRASS, BRIDGE)
                and c not in self.blocked)

    def _level(self, c: Cell) -> int:
        x, y = c
        return 1 if self.terrain[y][x] in (SAND, BRIDGE) else self.tier[y][x]

    def _lookups(self):
        """Walkability and level of every tile, rebuilt only when the island changes."""
        cached = self.__dict__.get("_lk")
        if cached and cached[0] == self._version:
            return cached[1], cached[2]
        walk = [[self.terrain[y][x] in (SAND, GRASS, BRIDGE) and (x, y) not in self.blocked for x in range(self.W)]
                for y in range(self.H)]
        lvl = [[1 if self.terrain[y][x] in (SAND, BRIDGE) else self.tier[y][x] for x in range(self.W)]
               for y in range(self.H)]
        self.__dict__["_lk"] = (self._version, walk, lvl)
        return walk, lvl

    def distances(self, start: Cell, tools: frozenset) -> dict[Cell, float]:
        key = (start, tools, self._version)
        if key in self._cache:
            return self._cache[key]
        used_cache = self.__dict__.setdefault("_used", {})
        if len(self._cache) > 400:
            self._cache.clear()
            used_cache.clear()
        ladder, pole = "ladder" in tools, "vaulting pole" in tools
        stairs = self.stair_steps()
        pole_w = int(self.cfg["pole_max_width"])
        W, H, terrain, paths, path_cost = self.W, self.H, self.terrain, self.paths, self.path_cost
        walk, lvl = self._lookups()
        dist = {start: 0.0}
        used: dict[Cell, frozenset] = {start: frozenset()}  # tools the best route to each cell climbs or jumps with
        none, by_ladder, by_pole = frozenset(), frozenset({"ladder"}), frozenset({"vaulting pole"})
        pq = [(0.0, start)]
        while pq:
            d, c = heapq.heappop(pq)
            if d > dist.get(c, 1e18):
                continue
            x, y = c
            lc = lvl[y][x]
            for dx, dy in N4:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                n = (nx, ny)
                cost, via = None, none
                if walk[ny][nx]:
                    ln = lvl[ny][nx]
                    if ln == lc:
                        cost = path_cost if n in paths else 1.0
                    elif (c, n) in stairs:  # version 17: built stairs, for anyone
                        cost = 2.0
                    elif ladder and abs(ln - lc) == 1:
                        cost, via = 3.0, by_ladder
                elif pole and terrain[ny][nx] == RIVER:
                    k, jx, jy = 0, nx, ny
                    while 0 <= jx < W and 0 <= jy < H and terrain[jy][jx] == RIVER and k < pole_w:
                        k, jx, jy = k + 1, jx + dx, jy + dy
                    if 0 <= jx < W and 0 <= jy < H and walk[jy][jx] and lvl[jy][jx] == lc:
                        n, cost, via = (jx, jy), k + 2.0, by_pole
                if cost is None:
                    continue
                nd = d + cost
                if nd < dist.get(n, 1e18):
                    dist[n] = nd
                    used[n] = used[c] if via is none else used[c] | via
                    heapq.heappush(pq, (nd, n))
        self._cache[key] = dist
        used_cache[key] = used
        return dist

    def route_tools(self, a: Cell, b: Cell, tools: frozenset) -> frozenset:
        """The ladder and/or vaulting pole the shortest walk from a to b climbs or jumps with."""
        a, b = tuple(a), tuple(b)
        self.distances(a, tools)
        return self.__dict__.get("_used", {}).get((a, tools, self._version), {}).get(b, frozenset())

    def reach_gap(self, a: Cell, targets: list[Cell], tools: frozenset) -> str:
        """Prompt version 15: what a villager at `a` would need to reach the nearest of `targets` they can't
        reach now ("a vaulting pole", "a ladder", "a ladder and a vaulting pole"), or "" if no tool would do."""
        if a is None:
            return ""
        every = frozenset(tools) | frozenset(("ladder", "vaulting pole"))
        best = self.nearest(tuple(a), every, [tuple(t) for t in targets])
        if best is None:
            return ""
        need = sorted(self.route_tools(tuple(a), best[1], every) - frozenset(tools))
        return " and ".join(f"a {t}" for t in need)

    def _cant_reach(self, island, msg: str, start, targets, tools) -> str:
        """`msg` ends "with the tools you carry"; from prompt version 15 it says which tool is missing instead."""
        if getattr(island, "prompt_version", 1) < 15:
            return msg
        gap = self.reach_gap(start, targets, tools)
        return msg.replace("with the tools you carry", f"without {gap}") if gap else msg

    def approach_cells(self, target: Cell) -> list[Cell]:
        """Where you stand to use something: the tile itself if walkable, else its neighbours."""
        if self.walkable(target):
            return [target]
        x, y = target
        return [(x + dx, y + dy) for dx, dy in N4 if self.walkable((x + dx, y + dy))]

    def nearest(self, start: Cell, tools: frozenset, targets: list[Cell]) -> tuple[int, Cell, float] | None:
        """Index of the reachable target with the shortest walk, where to stand, and the walk."""
        dist = self.distances(start, tools)
        best = None
        for i, t in enumerate(targets):
            for a in self.approach_cells(t):
                d = dist.get(a)
                if d is not None and (best is None or d < best[2]):
                    best = (i, a, d)
        return best

    def wander(self, start: Cell, home: Cell, steps: int, rng: random.Random) -> Cell:
        """A short random walk on one level, drifting back toward home when far away.
        Villagers follow roads: on or next to a path, they step onto path tiles with
        probability `road_bias`, and keep their heading along a road when they can."""
        bias = float(self.building.get("road_bias", 0.8))
        c, heading = start, None
        for _ in range(steps):
            opts = [(c[0] + dx, c[1] + dy) for dx, dy in N4]
            opts = [n for n in opts if self.walkable(n) and self._level(n) == self._level(c)]
            if not opts:
                break
            if math.dist(c, home) > self.cfg["wander_leash"]:
                opts.sort(key=lambda n: math.dist(n, home))
                nxt = opts[0]
            else:
                on_road = [n for n in opts if n in self.paths]
                if on_road and rng.random() < bias:
                    ahead = [n for n in on_road if heading and (n[0] - c[0], n[1] - c[1]) == heading]
                    nxt = ahead[0] if ahead and rng.random() < 0.8 else rng.choice(on_road)
                else:
                    nxt = rng.choice(opts)
            heading = (nxt[0] - c[0], nxt[1] - c[1])
            c = nxt
        return c

    # --- Space interface ------------------------------------------------------
    def coords_of_place(self, place: str):
        b = self.buildings.get(place)
        return b["door"] if b else None

    def coords_of_home(self, site: str):
        s = self.sites.get(site)
        return s["door"] if s else None

    def path_length(self, a, b, tools=frozenset()):
        return self.distances(tuple(a), frozenset(tools)).get(tuple(b))

    def region(self, c: Cell) -> str:
        x, y = c
        ns = "north" if y < self.H / 3 else "south" if y > 2 * self.H / 3 else ""
        ew = "west" if x < self.W / 3 else "east" if x > 2 * self.W / 3 else ""
        compass = (ns + ew) or "centre"
        return f"{TIER_NAME.get(self._level(c), 'lower ground')}, {compass}"

    def describe(self) -> str:
        return "; ".join(f"{n} ({self.region(b['door'])})" for n, b in self.buildings.items())

    # --- what bodies ask of the grid ---------------------------------------------
    _BUILDING_FOR = {"sell": "Nook's Cranny", "donate": "museum", "assess": "museum", "upgrade_house": "Resident Services",
                     "pay_loan": "Resident Services", "contribute_bridge": "Resident Services",
                     "set_ordinance": "Resident Services", "move_house": "Resident Services"}

    def __getstate__(self):
        state = dict(self.__dict__)
        state["_cache"], state["_used"] = {}, {}
        state.pop("_lk", None)
        state.pop("_spot_cache", None)
        return state

    def resolve(self, island, name: str, action: dict, pos, tools, rng):
        """Pick the concrete target for an intent and where to stand to use it.
        Bodies are simple: they take the nearest suitable thing they can reach."""
        do = str(action.get("do", "")).lower()
        tools = frozenset(tools)
        home = self.coords_of_home(island.home.get(name)) if island.home.get(name) else None
        start = tuple(pos) if pos else home
        if start is None:
            return "home", None, {}, None

        def nearest(targets, label, key=None, ids=None, what=None):
            best = self.nearest(start, tools, targets)
            if best is None:
                return label, None, {}, self._cant_reach(
                    island, f"there is no {what or label} you can reach from here with the tools you carry", start, targets, tools)
            i, stand, _ = best
            return label, stand, ({key: ids[i]} if key else {"_target": i}), None

        if do == "mine_rock":
            if action.get("rock") is not None:
                try:
                    idx = int(action["rock"]) - 1
                except (TypeError, ValueError):
                    idx = -1
                if not 0 <= idx < len(self.rock_cells):
                    return "rocks", None, {}, f"there is no rock {action['rock']}"
                if self.rock_cells[idx] is None:
                    return "rocks", None, {}, f"rock {idx + 1} is broken; a new one will turn up in a few days"
                ids = [idx + 1]
            else:
                ids = ([i + 1 for i, h in enumerate(island.rocks) if h > 0 and self.rock_cells[i]]
                       or [i + 1 for i, c in enumerate(self.rock_cells) if c])
            return nearest([self.rock_cells[i - 1] for i in ids], "rocks", "rock", ids, "rock")
        if do in ("chop_down_tree", "dig_stump") and action.get("near"):
            ref = self.landmark(island, name, action["near"], start)
            if ref is None:
                return "woods", None, {}, f"don't know where '{action['near']}' is"
            want = "stump" if do == "dig_stump" else str(action.get("kind", "hardwood")).lower()
            ids = sorted((i for i, t in enumerate(island.trees) if t.kind == want and t.pos),
                         key=lambda i: math.dist(island.trees[i].pos, ref))[:5]
            return nearest([island.trees[i].pos for i in ids], "woods", "tree", ids, want if want == "stump" else f"{want} tree")
        if do == "dig_stump":
            ids = [i for i, t in enumerate(island.trees) if t.kind == "stump" and t.pos]
            return nearest([island.trees[i].pos for i in ids], "woods", "tree", ids, "stump")
        if do == "break_rock":
            try:
                idx = int(action.get("rock")) - 1
            except (TypeError, ValueError):
                return "rocks", None, {}, "which rock? give its number"
            if not 0 <= idx < len(self.rock_cells) or self.rock_cells[idx] is None:
                return "rocks", None, {}, f"there is no rock {action.get('rock')} standing right now"
            return nearest([self.rock_cells[idx]], "rocks", "rock", [idx + 1], "rock")
        if do in ("lay_path", "remove_path", "place", "fence_off", "pick_up") or (
                do == "plant" and str(action.get("item", "")).lower().endswith("seeds")):
            return self._resolve_building(island, name, action, start, tools)
        if do in ("chop_wood", "shake_tree", "chop_down_tree"):
            kind = {"chop_wood": "hardwood", "shake_tree": "fruit"}.get(do, str(action.get("kind", "hardwood")).lower())
            if do == "chop_wood" and getattr(island, "prompt_version", 1) >= 4:  # fruit trees have wood too
                kind = str(action.get("kind", "hardwood")).lower()
                if kind not in ("hardwood", "fruit") and getattr(island, "prompt_version", 1) >= 15:
                    return "woods", None, {}, (f"there are no {kind} trees: trees are hardwood or fruit, and chopping "
                                               "either gives wood, softwood and hardwood")
            def ok(t):
                if t.kind != kind or not t.pos:
                    return False
                return do == "chop_down_tree" or (t.wood_left > 0 if do == "chop_wood" else t.fruit > 0)
            ids = [i for i, t in enumerate(island.trees) if ok(t)]
            label = "woods" if do != "shake_tree" else "orchard"
            return nearest([island.trees[i].pos for i in ids], label, "tree", ids, f"{kind} tree")
        if do == "plant":
            taken = self._occupied(island)
            dist = self.distances(start, tools)
            ref = self.landmark(island, name, action.get("near", "here"), start) or start
            spots = sorted((math.dist(c, ref) + 0.01 * d, c) for c, d in dist.items() if math.dist(c, ref) <= 25)
            for d, c in spots:
                for dx, dy in N4:
                    spot = (c[0] + dx, c[1] + dy)
                    if self.spot_ok_for_object(spot, taken):
                        return "woods", c, {"at": list(spot)}, None
            return "woods", None, {}, "there is no free spot to plant near here"
        if do == "fish":
            where = str(action.get("where", "river")).lower()
            label = where if where in ("sea", "river", "pond", "clifftop") else "river"
            if str(action.get("bait", "")).lower() in ("true", "yes", "1"):
                # Bait makes a fish appear wherever you throw it: walk to the nearest spot on that water.
                if island.v[name].inventory["fishing bait"] < 1:
                    if getattr(island, "prompt_version", 1) < 15:
                        return label, None, {}, "you have no fishing bait"
                    # From prompt version 15 they fish without it, as anyone casting would, and are told so.
                    place, target, extra, err = self._resolve_catch(
                        island, name, "fish", label, self._habitat_cells(island, "fish", label), action, start, tools,
                        rng, label, f"{label} to fish in")
                    return place, target, {**extra, "_no_bait": True}, err
                best = self.nearest(start, tools, self._habitat_cells(island, "fish", label))
                if best is None:
                    return label, None, {}, f"there is no {label} to fish in you can reach from here with the tools you carry"
                return label, best[1], {"_baited": True}, None
            return self._resolve_catch(island, name, "fish", label, self._habitat_cells(island, "fish", label),
                                       action, start, tools, rng, label, f"{label} to fish in")
        if do == "catch_bug":
            hab = str(action.get("where", "") or "ground").lower().rstrip("s") + "s"
            hab = {"grounds": "ground", "grasss": "ground", "fields": "ground", "flowerss": "flowers"}.get(hab, hab)
            if hab in ("flowers", "trees", "stumps", "rocks"):
                targets = self._habitat_cells(island, "bug", hab)
                if not targets:
                    return hab, None, {}, f"there are no {hab} on the island to look for bugs on"
                return self._resolve_catch(island, name, "bug", hab, targets, action, start, tools, rng, hab,
                                           f"{hab} to search")
            return self._resolve_catch(island, name, "bug", "ground", None, action, start, tools, rng, "fields",
                                       "open ground")
        if do == "visit":
            host = action.get("who")
            site = island.home.get(host) if host in island.home else None
            if not site:
                if getattr(island, "prompt_version", 1) >= 2:
                    return "visiting", None, {}, island.not_a_villager(host, "visit")
                return "visiting", None, {}, f"there is no home of {host} to visit"
            return self._to_building_door(f"{host}'s home", self.sites[site]["door"], start, tools, island)
        if do in ("beachcomb", "dig_fossil", "dig_clam"):
            return self._resolve_spots(island, name, do, action, start, tools)
        if do == "buy":
            b = "Able Sisters" if str(action.get("shop", "")).lower() == "able" else "Nook's Cranny"
            return self._to_building(b, start, tools, island)
        if do == "ask":
            from .world import NPCS, npc_of

            if npc_of(action) not in NPCS:
                return "around the island", None, {}, None  # the world explains who can be asked
            return self._to_building(NPCS[npc_of(action)][0], start, tools, island)
        if do in self._BUILDING_FOR:
            return self._to_building(self._BUILDING_FOR[do], start, tools, island)
        if do in ("propose_bridge", "propose_stairs") and getattr(island, "prompt_version", 1) >= 17:
            # Proposed where they stand: the nearest workable spot within `near` tiles that they can walk to.
            from .world import structures_cfg

            kind = do.split("_")[1]
            if (why := island.proposal_problem(name)):  # the island's limits come before where they stand
                return "around the island", None, {}, why
            r = structures_cfg(island.w)["near"]
            label = "river" if kind == "bridge" else "cliffs"
            spots = self.structure_spots(island, kind, tuple(start), r)
            dist = self.distances(start, tools)
            for sp in spots:
                ends = [e for e in sp["ends"] if e in dist]
                if ends:
                    stand = min(ends, key=lambda e: dist[e])
                    return label, stand, {"_cells": [list(c) for c in sp["cells"]],
                                          "_water": [list(c) for c in sp["water"]],
                                          "_steps": [[list(a), list(b)] for a, b in sp["steps"]],
                                          "_name": self.structure_name(kind, sp["cells"])}, None
            what = "stretch of river narrow enough for a bridge" if kind == "bridge" else "cliff edge for stairs"
            if spots:
                return label, None, {}, self._cant_reach(
                    island, f"you can't get to the {what} near here with the tools you carry", start,
                    [e for sp in spots for e in sp["ends"]], tools)
            # Say where the nearest one they could walk to is, as the island does for other places out of reach.
            best = min(((dist[e], e) for sp in self.structure_spots(island, kind) for e in sp["ends"] if e in dist),
                       default=None)
            where = (f"; the nearest you can walk to is about {int(best[0])} steps away ({self.region(best[1])})"
                     if best else "; there's none you can walk to with the tools you carry")
            return label, None, {}, (f"there's no {what} within {r} steps of here ({self.region(tuple(start))}), "
                                     f"away from other bridges and stairs{where}")
        if do in ("rest", "wear") and getattr(island, "prompt_version", 1) >= 15 and (
                home is None or self.path_length(start, home, tools) is None):
            return "around the island", None, {}, None  # from version 15, resting or dressing needs no walk home
        if do in ("craft", "rest", "wear", "decorate", "undecorate"):
            return "home", home, {}, None
        return "around the island", None, {}, None

    def _to_building_door(self, label: str, door, start, tools, island=None):
        if self.path_length(start, door, tools) is None:
            return label, None, {}, self._cant_reach(
                island, f"you can't get to {label} from here with the tools you carry", start, [door], tools)
        return label, door, {}, None

    def _to_building(self, b: str, start, tools, island=None):
        door = self.coords_of_place(b)
        if door is None:
            return b, None, {}, None
        if self.path_length(start, door, tools) is None:
            return b, None, {}, self._cant_reach(
                island, f"you can't get to {b} from here with the tools you carry", start, [door], tools)
        return b, door, {}, None

    def after_act(self, island, name: str, action: dict, ok: bool) -> None:
        do = str(action.get("do", "")).lower()
        if ok:
            self._commit_building(island, name, do, action)
        here = None
        if do == "beachcomb" and self.shells and "_target" in action:
            here = self.shells[action["_target"]]
            taken = sorted(self.shells, key=lambda c: math.dist(c, here))[: island.w["island"]["shells_per_action"]]
            self.shells = [c for c in self.shells if c not in taken][: max(0, island.shells_left)]
        if do == "dig_fossil" and ok and "_target" in action and action["_target"] < len(self.dig_spots):
            self.dig_spots.pop(action["_target"])
        clams = getattr(self, "clam_spots", [])
        if do == "dig_clam" and ok and "_target" in action and action["_target"] < len(clams):
            clams.pop(action["_target"])
        if "_critter" in action:  # caught, or it got away: either way it's gone from that spot
            self.critters = [c for c in self._crit() if c["id"] != action["_critter"]]
        self.sync(island)

    # --- building --------------------------------------------------------------
    def landmark(self, island, name: str, ref, start) -> Cell | None:
        """A place the mind can name: here, home, <villager>'s home, a building, the plaza, rock N. A tile
        ([x, y]) is itself: from prompt version 16 an option can point at where something already is."""
        if isinstance(ref, (list, tuple)) and len(ref) == 2 and all(isinstance(v, int) for v in ref):
            return tuple(ref)
        r = str(ref or "here").strip().lower()
        if getattr(island, "prompt_version", 1) >= 16:  # "southwest", as region() says it, is south and west
            r = re.sub(r"\b(north|south)(east|west)\b", r"\1 \2", r)
        if r in ("here", ""):
            return start
        if r in ("home", "my home", "my house"):
            site = island.home.get(name)
            return self.coords_of_home(site) if site else None
        for v, site in island.home.items():
            if site and r in (f"{v.lower()}'s home", f"{v.lower()}'s house", v.lower()):
                return self.coords_of_home(site)
        for b, info in self.buildings.items():
            if r in (b.lower(), b.lower().replace("'", "")):
                return info["door"]
        if r.startswith("rock"):
            try:
                c = self.rock_cells[int(r.split()[-1]) - 1]
                return c
            except (ValueError, IndexError):
                return None
        return self._compass(r)

    def _compass(self, r: str) -> Cell | None:
        """'north beach', 'the south-west', 'east cliffs', 'centre': a rough spot on the island."""
        words = r.replace("-", " ").split()
        if not any(w in ("north", "south", "east", "west", "centre", "center", "middle", "beach") for w in words):
            return None
        fx = 1 / 6 if "west" in words else 5 / 6 if "east" in words else 1 / 2
        fy = 1 / 6 if "north" in words else 5 / 6 if "south" in words else 1 / 2
        p = (self.W * fx, self.H * fy)
        kinds = (SAND,) if "beach" in words else (GRASS, SAND)
        best = None
        for y in range(self.H):
            for x in range(self.W):
                if self.terrain[y][x] in kinds and (x, y) not in self.blocked:
                    d = math.dist((x, y), p)
                    if best is None or d < best[0]:
                        best = (d, (x, y))
        return best[1] if best else None

    def route(self, a: Cell, b: Cell, tools: frozenset) -> list[Cell]:
        """Shortest walk from a to b as a list of tiles (empty if unreachable)."""
        ladder, pole = "ladder" in tools, "vaulting pole" in tools
        prev, dist = {a: None}, {a: 0.0}
        pq = [(0.0, a)]
        goal = set(self.approach_cells(b)) | {b}
        end = None
        while pq:
            d, c = heapq.heappop(pq)
            if c in goal:
                end = c
                break
            if d > dist.get(c, 1e18):
                continue
            for dx, dy in N4:
                n = (c[0] + dx, c[1] + dy)
                if not self.walkable(n):
                    continue
                lc, ln = self._level(c), self._level(n)
                cost = (1.0 if ln == lc else 2.0 if (c, n) in self.stair_steps()
                        else 3.0 if ladder and abs(ln - lc) == 1 else None)
                if cost is None:
                    continue
                if d + cost < dist.get(n, 1e18):
                    dist[n], prev[n] = d + cost, c
                    heapq.heappush(pq, (d + cost, n))
        if end is None:
            return []
        out = []
        while end is not None:
            out.append(end)
            end = prev[end]
        return out[::-1]

    def _free_tiles_near(self, ref: Cell, start: Cell, tools, w: int, h: int, n: int, island, max_d: float,
                         allow_path: bool = True) -> list[list[Cell]]:
        """Up to n free w x h footprints near ref, closest first, that the villager can walk up to.
        Footprints never cover a door, a building (the plaza too) or a homesite, and multi-tile pieces
        keep a one-tile gap between them."""
        dist = self.distances(start, tools)
        taken = self._occupied(island) - (set(self.paths) if allow_path else set())
        doors = {s["door"] for s in self.sites.values()} | {b["door"] for b in self.buildings.values()}
        out: list[list[Cell]] = []
        used: set[Cell] = set()
        r = int(max_d)
        cands = sorted(((x, y) for x in range(ref[0] - r, ref[0] + r + 1) for y in range(ref[1] - r, ref[1] + r + 1)
                        if math.dist((x, y), ref) <= max_d), key=lambda c: (math.dist(c, ref), c))
        for x, y in cands:
            if len(out) >= n:
                break
            cells = [(x + i, y + j) for j in range(h) for i in range(w)]
            fixtures = getattr(self, "fixtures", set())
            if any(c in used or c in taken or c in doors or c in fixtures or not self.walkable(c)
                   or self.terrain[c[1]][c[0]] not in (GRASS, SAND) for c in cells):
                continue
            if not any(dist.get(a) is not None for a in self._around(cells)):
                continue
            out.append(cells)
            used.update(cells)
            if w * h > 1:
                used.update((c[0] + dx, c[1] + dy) for c in cells for dx, dy in N4)
        return out

    def _resolve_building(self, island, name, action, start, tools):
        from .world import item_info

        do = str(action.get("do", "")).lower()
        b = self.building
        max_d = float(b.get("max_place_distance", 12))
        label = "outside"
        ref = self.landmark(island, name, action.get("near") or action.get("around") or action.get("from") or "here", start)
        if ref is None:
            return label, None, {}, f"don't know where '{action.get('near') or action.get('around') or action.get('from')}' is"
        if do == "lay_path":
            to = self.landmark(island, name, action.get("to"), start)
            if to is None:
                return label, None, {}, f"lay a path to where? '{action.get('to')}' isn't a place I know"
            line = self.route(ref, to, tools)
            if not line:
                return label, None, {}, "there is no way to walk between those two places with the tools you carry"
            todo = [c for c in line if c not in self.paths and self.terrain[c[1]][c[0]] in (GRASS, SAND)
                    and c not in self.flowers]
            k = int(b.get("path_tiles_per_action", 30))
            stand = todo[min(k, len(todo)) - 1] if todo else line[-1]
            if self.path_length(start, stand, tools) is None:
                return label, None, {}, "you can't get to where that path starts"
            return "roadworks", stand, {"path": [list(c) for c in todo[:k]], "more": len(todo) > k}, None
        if do == "remove_path":
            own = b.get("owner_only_pickup", True)
            cells = sorted((c for c, p in self.paths.items() if math.dist(c, ref) <= max_d and (p["owner"] == name or not own)),
                           key=lambda c: math.dist(c, ref))[: int(action.get("count", 30))]
            return "roadworks", (cells[0] if cells else None), {"cells": [list(c) for c in cells]}, None
        item = str(action.get("item", "")).lower()
        info = item_info(island.w, item)
        if do == "place":
            count = max(1, int(action.get("count", 1) or 1))
            have = island.v[name].inventory[item]
            count = min(count, have) if have else count
            w, h = info.get("size", [1, 1])
            spots = self._free_tiles_near(ref, start, tools, w, h, count, island, max_d)
            stand = self._stand_for(spots[0], start, tools) if spots else None
            return label, stand, {"cells": [[list(c) for c in sp] for sp in spots]}, None
        if do == "fence_off":
            site = island.home.get(name) if str(action.get("around", "home")).lower() in ("home", "my home") else None
            if not site:
                return label, None, {}, "you can only fence off around your own home"
            plot = set(self.sites[site]["cells"])
            ring = {(x + dx, y + dy) for x, y in plot for dx, dy in N8} - plot
            door = self.sites[site]["door"]
            ring.discard(door)
            ring -= {(door[0] + dx, door[1] + dy) for dx, dy in N8}  # keep the entrance open
            taken = self._occupied(island)
            cells = sorted((c for c in ring if self.walkable(c) and c not in taken), key=lambda c: (c[1], c[0]))
            cells = cells[: island.v[name].inventory[item]] if island.v[name].inventory[item] else cells
            return "home", door, {"cells": [[list(c)] for c in cells]}, None
        if do == "pick_up":
            own = b.get("owner_only_pickup", True)
            objs = {}
            for c, o in self.placed.items():
                if o["item"] == item and (o["owner"] == name or not own) and math.dist(c, ref) <= max_d:
                    objs.setdefault(o["id"], c)
            anchors = sorted(objs.values(), key=lambda c: math.dist(c, ref))[: max(1, int(action.get("count", 1) or 1))]
            stand = self._stand_for([anchors[0]], start, tools) if anchors else None
            return label, stand, {"cells": [[list(c)] for c in anchors]}, None
        if do == "plant":  # flower seeds
            spots = self._free_tiles_near(ref, start, tools, 1, 1, 1, island, max_d, allow_path=False)
            spots = [sp for sp in spots if self.terrain[sp[0][1]][sp[0][0]] == GRASS]
            stand = self._stand_for(spots[0], start, tools) if spots else None
            return "garden", stand, {"cells": [[list(c) for c in sp] for sp in spots[:1]]}, None
        return label, None, {}, None

    def _around(self, cells) -> list[Cell]:
        """Walkable tiles next to a footprint, where you stand to put something there."""
        cs = {tuple(c) for c in cells}
        return [(c[0] + dx, c[1] + dy) for c in cs for dx, dy in N4
                if (c[0] + dx, c[1] + dy) not in cs and self.walkable((c[0] + dx, c[1] + dy))]

    def _stand_for(self, cells, start, tools):
        dist = self.distances(start, tools)
        opts = [(dist[a], a) for a in self._around(cells) if a in dist]
        if not opts:  # e.g. picking something up: its own tile isn't walkable, so stand beside it
            opts = [(dist[a], a) for c in cells for a in self.approach_cells(tuple(c)) if a in dist]
        return min(opts)[1] if opts else None

    def _commit_building(self, island, name, do, action):
        if do == "lay_path":
            for c in action.get("path", []):
                self.paths[tuple(c)] = {"style": str(action.get("style", "dirt")).lower(), "owner": name}
            if self.path_cost != 1.0:
                self._version += 1
        elif do == "remove_path":
            for c in action.get("cells", []):
                self.paths.pop(tuple(c), None)
            if self.path_cost != 1.0:
                self._version += 1
        elif do in ("place", "fence_off"):
            from .world import item_info

            item = str(action.get("item", "")).lower()
            kind = item_info(island.w, item)["kind"]
            for piece in action.get("cells", []):
                self._next_obj += 1
                for c in piece:
                    self.placed[tuple(c)] = {"id": self._next_obj, "item": item, "kind": kind, "owner": name}
                    self.blocked.add(tuple(c))
            self._version += 1
        elif do == "pick_up":
            for piece in action.get("cells", []):
                oid = self.placed.get(tuple(piece[0]), {}).get("id")
                for c in [c for c, o in self.placed.items() if o["id"] == oid]:
                    del self.placed[c]
                    self.blocked.discard(c)
            self._version += 1
        elif do == "plant" and str(action.get("item", "")).lower().endswith("seeds"):
            from .world import item_info

            item = str(action.get("item", "")).lower()
            days = int(self.building.get("flower_bloom_days", 3))
            for piece in action.get("cells", []):
                for c in piece:
                    self.flowers[tuple(c)] = {"kind": item_info(island.w, item).get("grows", "flowers"),
                                              "owner": name, "bloom_day": island.day + days}
        elif do == "break_rock":
            idx = int(action.get("rock", 0)) - 1
            if 0 <= idx < len(self.rock_cells) and self.rock_cells[idx]:
                self.blocked.discard(self.rock_cells[idx])
                self.rock_cells[idx] = None
                self._version += 1

    def objects_snapshot(self, island) -> dict:
        objs = {o["id"]: (c, o) for c, o in self.placed.items()}
        return {
            "trees": [[*t.pos, t.kind] for t in island.trees if t.pos],
            "rocks": [list(c) if c else None for c in self.rock_cells],
            "bridge_open": self.bridge_built,
            "bridge_cells": [list(c) for c in self.first_bridge_cells(island)],
            "structures": [[st["kind"], [list(c) for c in st["cells"]], bool(st["built"]), st["fund"], st["name"]]
                           for st in getattr(island, "projects", None) or []],
            "paths": [[*c, p["style"], p["owner"]] for c, p in self.paths.items()],
            "placed": [[*c, o["item"], o["kind"], o["owner"]] for c, o in self.placed.items()],
            "flowers": [[*c, f["kind"], f["owner"], int(island.day >= f["bloom_day"])] for c, f in self.flowers.items()],
            "objects_count": len(objs),
        }

    def where_note(self, island, name: str, pos, tools) -> str:
        """What the villager knows about getting around: tools, where things are, what they can reach."""
        tools = frozenset(tools)
        start = tuple(pos) if pos else self.coords_of_home(island.home.get(name) or "")
        if start is None:
            return ""
        dist = self.distances(start, tools)
        carry = sorted(t for t in tools if t in ("ladder", "vaulting pole")) or ["no ladder or vaulting pole"]

        def reach(cells):
            ds = [dist.get(a) for c in cells for a in self.approach_cells(c)]
            ds = [d for d in ds if d is not None]
            if ds:
                return f"about {int(min(ds))} steps"
            gap = self.reach_gap(start, cells, tools) if getattr(island, "prompt_version", 1) >= 15 else ""
            return f"out of reach without {gap}" if gap else "out of reach for you right now"

        rocks = "; ".join(f"{i + 1} ({self.region(c)}, {reach([c])})" if c else f"{i + 1} (broken, coming back soon)"
                          for i, c in enumerate(self.rock_cells))
        mine = Counter(o["item"] for o in {o["id"]: o for o in self.placed.values() if o["owner"] == name}.values())
        n_path = sum(1 for p in self.paths.values() if p["owner"] == name)
        n_flowers = sum(1 for f in self.flowers.values() if f["owner"] == name)
        yours = ", ".join(f"{k} x{v}" for k, v in mine.items())
        places = "; ".join(f"{b} ({reach([v['door']])})" for b, v in self.buildings.items()
                           if b in ("Resident Services", "Nook's Cranny", "Able Sisters", "museum")
                           or (b == "plaza" and getattr(island, "prompt_version", 1) >= 7))  # open all day from v7
        built = []
        if yours:
            built.append(f"things you put outside: {yours}")
        if n_path:
            built.append(f"{n_path} path tiles you laid")
        if n_flowers:
            built.append(f"{n_flowers} flowers you planted")
        home = self.coords_of_home(island.home.get(name)) if island.home.get(name) else None
        stuck = ""
        if (getattr(island, "prompt_version", 1) >= 15 and home is not None and tuple(start) != tuple(home)
                and dist.get(tuple(home)) is None):
            gap = self.reach_gap(start, [home], tools)
            stuck = f" Your home is out of reach{f' without {gap}' if gap else ''} (call_rescue, once a day)."
        return (f"You are at {self.region(start)}. You carry: {', '.join(carry)}. Cliffs need a ladder and the river "
                f"needs a vaulting pole (or a bridge).{stuck}\nRocks: {rocks}\nBuildings: {places}"
                + (self.trees_here(island, start) if getattr(island, "prompt_version", 1) >= 10 else "")
                + (f"\nYour mark on the island: {'; '.join(built)}" if built else ""))

    def trees_here(self, island, at) -> str:
        """Prompt version 10: the trees a villager can see in the part of the island they're in (one of nine
        areas), as plainly as the rocks: standing trees, stumps and saplings."""
        here = self._area(tuple(at))
        c = Counter(t.kind for t in island.trees if t.pos and self._area(tuple(t.pos)) == here)
        names = (("hardwood", "hardwood tree"), ("fruit", "fruit tree"), ("stump", "stump"), ("sapling", "sapling"))
        seen = [f"{c[k]} {name}{'s' if c[k] > 1 else ''}" for k, name in names if c[k]]
        return f"\nTrees around here ({here}): " + (", ".join(seen) or "none")

    _AREAS = ["northwest", "north", "northeast", "west", "centre", "east", "southwest", "south", "southeast"]

    def _area(self, c: Cell) -> str:
        return self.region(c).split(", ")[-1]

    def map_text(self, island) -> str:
        """The island map, one line per area of a 3 x 3 grid, in the same words as "Getting around":
        what ground and water is there, buildings, whose homes, rock numbers and trees."""
        land, water = {a: Counter() for a in self._AREAS}, {a: set() for a in self._AREAS}
        for y in range(self.H):
            for x in range(self.W):
                t, a = self.terrain[y][x], self._area((x, y))
                if t == GRASS:
                    land[a][TIER_NAME.get(self.tier[y][x], "lower ground")] += 1
                elif t == SAND:
                    water[a].add("beach")
                elif t == ROCKY:
                    water[a].add("rocky shore")
                elif t == RIVER:
                    water[a].add("river")
                elif t == POND:
                    water[a].add("pond")
                elif t == BRIDGE:
                    water[a].add("the bridge")
        things = {a: [] for a in self._AREAS}
        for b, v in self.buildings.items():
            things[self._area(v["door"])].append(b)
        homes = {a: [] for a in self._AREAS}
        for n, site in island.home.items():
            if site in self.sites:
                homes[self._area(self.sites[site]["door"])].append(n)
        rocks = {a: [] for a in self._AREAS}
        for i, c in enumerate(self.rock_cells):
            if c:
                rocks[self._area(c)].append(str(i + 1))
        trees = {a: Counter() for a in self._AREAS}
        for t in island.trees:
            if t.pos and t.kind in ("hardwood", "fruit"):
                trees[self._area(tuple(t.pos))][t.kind] += 1
        lines = []
        for a in self._AREAS:
            ground = [k for k, n in land[a].most_common() if n >= 20]
            if not ground and not water[a]:
                lines.append(f"- {a}: sea")
                continue
            bits = [" and ".join(ground) if ground else "no dry land"]
            bits += sorted(water[a])
            if things[a]:
                bits.append("buildings: " + ", ".join(things[a]))
            if homes[a]:
                bits.append("homes: " + ", ".join(sorted(homes[a])))
            if rocks[a]:
                bits.append("rocks " + ", ".join(rocks[a]))
            if trees[a]:
                bits.append(", ".join(f"{n} {k} tree{'s' if n > 1 else ''}" for k, n in sorted(trees[a].items())))
            lines.append(f"- {a}: " + "; ".join(bits))
        return "\n".join(lines)

    # --- helpers -----------------------------------------------------------------
    def _distance_from(self, sources: set[Cell], nbrs) -> list[list[int]]:
        d = [[10 ** 9] * self.W for _ in range(self.H)]
        q = deque()
        for x, y in sources:
            d[y][x] = 0
            q.append((x, y))
        while q:
            x, y = q.popleft()
            for dx, dy in nbrs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.W and 0 <= ny < self.H and d[ny][nx] > d[y][x] + 1:
                    d[ny][nx] = d[y][x] + 1
                    q.append((nx, ny))
        return d

    # --- sight: bugs, fish shadows, shells and dig spots ---------------------------------
    SIZES = {1: "tiny", 2: "small", 3: "medium", 4: "large", 5: "very large", 6: "huge"}
    HAB_WORD = {"flowers": "on the flowers", "trees": "on a tree", "stumps": "on a stump", "rocks": "on a rock",
                "ground": "in the grass"}

    @property
    def sight(self) -> float:
        return float(self.cfg.get("sight_radius", 8))

    def _crit(self) -> list:
        if not hasattr(self, "critters"):  # grids pickled before sight existed
            self.critters, self.known, self.looked, self.seen_ids = [], {}, {}, {}
        return self.critters

    def _habitat_cells(self, island, kind: str, hab: str) -> list[Cell]:
        if kind == "bug":
            if hab == "flowers":
                return [c for c, f in self.flowers.items() if island.day >= f["bloom_day"]]
            if hab == "rocks":
                return [c for c in self.rock_cells if c]
            if hab in ("trees", "stumps"):
                kinds = ("stump",) if hab == "stumps" else ("hardwood", "fruit")
                return [t.pos for t in island.trees if t.pos and t.kind in kinds]
            key = ("ground", self._version)
        else:
            key = ("water", hab, self._version)
        cache = self.__dict__.setdefault("_hab_cache", {})
        if key not in cache:
            if kind == "bug":
                cache[key] = [(x, y) for y in range(self.H) for x in range(self.W)
                              if self.terrain[y][x] == GRASS and (x, y) not in self.blocked]
            else:
                cache[key] = [c for c in self.water_edges(hab) if self.approach_cells(c)]
            for k in [k for k in cache if k[-1] != self._version]:
                del cache[k]
        return cache[key]

    def _table(self, island, kind: str, hab: str, hour: int) -> dict:
        if kind == "bug":
            return {k: v for k, v in island.w["bugs"].items()
                    if v.get("habitat", "ground") == hab and island._available(v, hour)}
        wheres = ("river", "clifftop") if hab == "clifftop" else (hab,)
        return {k: v for k, v in island.w["fish"].items() if v["where"] in wheres and island._available(v, hour)}

    def hour_tick(self, island, hour: int, rng) -> None:
        """Bugs and fish shadows whose hours are over leave; new ones appear up to each habitat's number."""
        crit = self._crit()
        live, homes = [], {}
        for c in crit:
            spec = island.w["bugs" if c["kind"] == "bug" else "fish"].get(c["species"])
            if c["habitat"] == "ground":
                ok_cell = self.walkable(c["cell"]) and self.terrain[c["cell"][1]][c["cell"][0]] == GRASS
            else:
                key = (c["kind"], c["habitat"])
                if key not in homes:
                    homes[key] = set(self._habitat_cells(island, *key))
                ok_cell = c["cell"] in homes[key]
            if spec and island._available(spec, hour) and ok_cell:
                live.append(c)
        self.critters = live
        want = {"bug": {"flowers": 6, "trees": 5, "stumps": 2, "rocks": 2, "ground": 6, **(self.cfg.get("bugs_out") or {})},
                "fish": {"sea": 14, "river": 10, "pond": 3, "clifftop": 3, **(self.cfg.get("fish_out") or {})}}
        for kind, per in want.items():
            for hab, n in per.items():
                cells = self._habitat_cells(island, kind, hab)
                table = self._table(island, kind, hab, hour)
                if not cells or not table:
                    continue
                taken = {c["cell"] for c in self.critters}
                have = sum(1 for c in self.critters if c["kind"] == kind and c["habitat"] == hab)
                cap = min(int(n), max(1, len(cells) // 2))
                names, weights = list(table), [v["weight"] for v in table.values()]
                for _ in range(cap - have):
                    for _try in range(20):
                        cell = rng.choice(cells)
                        if cell not in taken:
                            break
                    else:
                        continue
                    sp = rng.choices(names, weights=weights)[0]
                    self.__dict__["_cid"] = self.__dict__.get("_cid", 0) + 1
                    entry = {"id": self._cid, "kind": kind, "species": sp, "habitat": hab, "cell": tuple(cell)}
                    if kind == "fish":
                        entry["size"] = self.SIZES.get(int(table[sp].get("shadow", 3)), "medium")
                    self.critters.append(entry)
                    taken.add(tuple(cell))

    def _search_stands(self, dist: dict, tiles, aim, avoid, n: int) -> list[Cell]:
        """Up to n places to stand and look from, nearest to `aim` first, each out of sight of
        the others and of the places in `avoid`."""
        r = self.sight
        out = []
        for c in sorted(tiles, key=lambda c: (math.dist(c, aim), dist[c])):
            if all(math.dist(c, x) > r for x in out) and all(math.dist(c, x) > r for x in avoid):
                out.append(c)
                if len(out) == n:
                    break
        return out

    def _resolve_catch(self, island, name, kind, hab, targets, action, start, tools, rng, label, what):
        """Go after a bug or fish shadow you can see. If none is in sight, walk to where they live
        and look around, trying up to `search_spots` places within the hour; if there is still
        nothing, you spent the hour looking."""
        crit = self._crit()
        r = self.sight
        if kind == "fish":
            want = str(action.get("size") or "").lower().strip()
            prefer = lambda c: want and c.get("size") == want  # noqa: E731
        else:
            want = str(action.get("species") or action.get("bug") or "").lower().strip()
            prefer = lambda c: want and c["species"] == want  # noqa: E731

        def pick(frm):
            seen = [c for c in crit if c["kind"] == kind and c["habitat"] == hab and math.dist(c["cell"], frm) <= r]
            for group in ([c for c in seen if prefer(c)], seen):
                if group:
                    best = self.nearest(start, tools, [c["cell"] for c in group])
                    if best:
                        return group[best[0]], best[1]
            return None

        extra = {"habitat": hab} if kind == "bug" else {}
        got = pick(start)
        if got is None:
            dist = self.distances(start, tools)
            if targets is None:  # open ground: anywhere on the grass a short walk away
                tiles = [c for c, d in dist.items() if d <= 20 and self.terrain[c[1]][c[0]] == GRASS]
                aim = rng.choice(sorted(tiles)) if tiles else start
            else:
                tiles = {a for t in targets for a in self.approach_cells(t) if a in dist}
                aim = start
            stands = self._search_stands(dist, tiles, aim, [start], int(self.cfg.get("search_spots", 3)))
            if not stands:
                return label, None, {}, f"there is no {what} you can reach from here with the tools you carry"
            for st in stands:
                got = pick(st)
                if got:
                    break
            else:
                out_now = any(c["kind"] == kind and c["habitat"] == hab for c in crit)
                return label, stands[-1], {**extra, "none_seen": True, **({} if out_now else {"none_out": True})}, None
        c, where = got
        if kind == "fish":
            extra["_size"] = c["size"]
        return label, where, {**extra, "_species": c["species"], "_critter": c["id"]}, None

    def _resolve_spots(self, island, name, do, action, start, tools):
        """Shells and dig spots: go to one you know of; otherwise walk to places you haven't looked
        yet today (or where you were told to look) and take one you can see from there."""
        self._crit()
        spots, terrain, label, what = ((self.shells, SAND, "beach", "beach") if do == "beachcomb"
                                       else (getattr(self, "clam_spots", []), SAND, "beach", "beach") if do == "dig_clam"
                                       else (self.dig_spots, GRASS, "fields", "open ground"))
        if not spots:
            return label, None, {}, None  # the world says the beach is picked clean / no dig spots
        known = self.known.setdefault(name, set())
        near = action.get("near")
        ref = self.landmark(island, name, near, start) if near else None
        r = self.sight
        cand = [i for i, c in enumerate(spots) if c in known and (ref is None or math.dist(c, ref) <= 2 * r)]
        if cand:
            best = self.nearest(start, tools, [spots[i] for i in cand])
            if best:
                return label, best[1], {"_target": cand[best[0]]}, None
        dist = self.distances(start, tools)
        looked = self.looked.setdefault(name, [])
        tiles = [c for c in dist if self.terrain[c[1]][c[0]] == terrain]
        if not tiles:
            return label, None, {}, f"there is no {what} you can reach from here with the tools you carry"
        n = int(self.cfg.get("search_spots", 3))
        stands = (self._search_stands(dist, tiles, ref or start, [] if ref else looked, n)
                  or self._search_stands(dist, tiles, ref or start, [], 1))
        for st in stands:
            looked.append(st)
            vis = [i for i, c in enumerate(spots) if math.dist(c, st) <= r]
            known |= {spots[i] for i in vis}
            if vis:
                best = self.nearest(start, tools, [spots[i] for i in vis])
                if best:
                    return label, best[1], {"_target": vis[best[0]]}, None
        return label, stands[-1], {"none_seen": True}, None

    def _near_text(self, c: Cell) -> str:
        marks = [(math.dist(c, b["door"]), f"near {'the ' if n in ('museum', 'plaza', 'airport') else ''}{n}")
                 for n, b in self.buildings.items()]
        marks += [(math.dist(c, rc), f"near rock {i + 1}") for i, rc in enumerate(self.rock_cells) if rc]
        d, txt = min(marks) if marks else (99, "")
        return f"{self.region(c)}, {txt}" if d <= 12 else self.region(c)

    def look(self, island, name: str, pos, hour: int) -> None:
        """What a villager notices around them at the end of the hour. Shells and dig spots become
        known; new bugs and fish shadows are remembered for today."""
        if pos is None:
            return []
        from .world import clock

        crit = self._crit()
        r = self.sight
        known = self.known.setdefault(name, set())
        seen = self.seen_ids.setdefault(name, set())
        just = self.__dict__.setdefault("just_seen", {})
        just[name] = []  # bugs and fish shadows first seen this hour, for a decision to go after one (version 14)
        notes = []
        for spots, word in ((self.shells, "shells on the beach"), (self.dig_spots, "fossil dig spots"),
                            (getattr(self, "clam_spots", []), "clam spots on the beach")):
            new = [c for c in spots if c not in known and math.dist(c, pos) <= r]
            if new:
                known |= set(new)
                notes.append(f"{len(new)} {word if len(new) > 1 else word.replace('shells', 'shell').replace('spots', 'spot')} "
                             f"({self._near_text(new[0])})")
        for c in crit:
            if c["id"] in seen or math.dist(c["cell"], pos) > r:
                continue
            seen.add(c["id"])
            if c["kind"] == "bug":
                notes.append(f"{_article(c['species'])} {self.HAB_WORD.get(c['habitat'], '')} ({self._near_text(c['cell'])})")
            else:
                notes.append(f"a {c['size']} fish shadow in the {c['habitat']} ({self._near_text(c['cell'])})")
            just[name].append({**c, "text": notes[-1]})
        if notes:
            sp = island.__dict__.setdefault("spotted", {})
            sp[name] = (sp.get(name, []) + [f"{clock(hour)} {n}" for n in notes])[-8:]
        return notes

    def water_edges(self, kind: str) -> list[Cell]:
        """Land tiles you can fish from, by habitat: sea, river, clifftop (river on a cliff), pond."""
        out = []
        for y in range(1, self.H - 1):
            for x in range(1, self.W - 1):
                t = self.terrain[y][x]
                ok = ((kind == "sea" and t == SEA) or (kind == "pond" and t == POND)
                      or (kind == "river" and t == RIVER) or (kind == "clifftop" and t == RIVER and self.tier[y][x] >= 2))
                if ok:
                    out.append((x, y))
        return out


def _article(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


DEFAULTS = {
    "width": 112,
    "height": 96,
    "sea_margin": 7,
    "beach_width": 3,
    "tier2_line": 0.42,
    "tier3_line": 0.22,
    "homesites": 14,
    "homesite_spacing": 9,
    "pole_max_width": 3,
    "wander_leash": 30,
    "buildings": {
        "Resident Services": {"size": [6, 4], "near": [0.5, 0.6]},
        "plaza": {"size": [7, 5], "near": [0.5, 0.68], "blocking": False},
        "museum": {"size": [6, 4], "near": [0.36, 0.56]},
        "Nook's Cranny": {"size": [5, 4], "near": [0.62, 0.66]},
        "Able Sisters": {"size": [5, 4], "near": [0.6, 0.76]},
        "airport": {"size": [6, 4], "near": [0.5, 0.86]},
    },
}
