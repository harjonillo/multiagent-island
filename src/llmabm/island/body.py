"""Bodies: the part of each villager that exists in space.

The LLM (the mind) only plans, thinks and talks. It schedules intents such as
{"hour": 7, "do": "mine_rock"}. The body carries them out: it works out where the
intent takes place, walks there from wherever the villager currently is, checks the
place can be reached with the tools the villager carries, and then hands the action
to the world rules. Tools wear out with use: each action that needs one, and each trip
whose route climbs a cliff with the ladder or jumps the river with the vaulting pole. Bodies also decide who gets somewhere first within an hour (the
shorter walk wins) and go home when their villager falls asleep.

Nothing here calls an LLM, so all of it is deterministic given the RNG.
"""

from __future__ import annotations

from dataclasses import dataclass

from .space import Point, Space
from .world import MOVEMENT_TOOLS, Island


@dataclass
class Body:
    name: str
    pos: Point | None = None          # None until the villager has arrived
    place: str = "home"               # last place the body was at
    walked_today: float = 0.0


class Bodies:
    def __init__(self, island: Island, space: Space):
        self.isl = island
        self.space = space
        self.b: dict[str, Body] = {n: Body(n) for n in island.v}
        self.broke: list[tuple[str, str, str]] = []  # (villager, tool, how) since the simulation last looked

    # --- positions -----------------------------------------------------------
    def home_point(self, name: str) -> Point | None:
        site = self.isl.home.get(name)
        return self.space.coords_of_home(site) if site else None

    def tools_of(self, name: str) -> frozenset:
        """The tools that change where a villager can walk."""
        inv = self.isl.v[name].inventory
        return frozenset(t for t in MOVEMENT_TOOLS if inv[t] > 0)

    def _trip_wear(self, name: str, a, b) -> list[str]:
        """A ladder or pole wears once on a trip whose route climbs a cliff or jumps the river with it."""
        if a is None or b is None or tuple(a) == tuple(b):
            return []
        notes = []
        for t in sorted(self.space.route_tools(a, b, self.tools_of(name))):
            note = self.isl.use_tool(self.isl.v[name], t)
            if note:
                notes.append(note)
                self.broke.append((name, t, "walking"))
        return notes

    def go_home(self, name: str) -> None:
        body = self.b[name]
        home = self.home_point(name)
        if home is not None and body.pos is not None:
            notes = self._trip_wear(name, body.pos, home)
            body.walked_today += self.space.path_length(body.pos, home, self.tools_of(name)) or 0.0
            body.pos, body.place = tuple(home), "home"
            for note in notes:
                self.isl.v[name].today.append(f"on the way home, {note}")

    def place_at_home(self, name: str) -> None:
        home = self.home_point(name)
        self.b[name].pos, self.b[name].place = (tuple(home) if home else None), "home"

    def new_day(self) -> None:
        for body in self.b.values():
            body.walked_today = 0.0

    # --- movement and action -------------------------------------------------
    def eta(self, name: str, action: dict, rng=None) -> float | None:
        """Walking distance from where the villager is now to where the action happens."""
        body = self.b[name]
        tools = self.tools_of(name)
        _, target, _, err = self.space.resolve(self.isl, name, action, body.pos, tools, rng or _NoRng())
        if err or body.pos is None or target is None:
            return 0.0 if not err else None
        return self.space.path_length(body.pos, target, tools)

    def order(self, batch: list[tuple[str, dict]], rng) -> list[tuple[str, dict]]:
        """Who gets there first this hour: the shortest walk, ties broken at random."""
        keyed = []
        for name, act in batch:
            d = self.eta(name, act, rng)
            keyed.append(((float("inf") if d is None else round(d, 3)), rng.random(), name, act))
        keyed.sort(key=lambda k: (k[0], k[1]))
        return [(name, act) for _, _, name, act in keyed]

    def perform(self, name: str, action: dict, hour: int, rng=None) -> tuple[bool, str]:
        body = self.b[name]
        vil = self.isl.v[name]
        need, why = self.isl.tool_for(vil, action)
        if why:
            return False, why
        tools = self.tools_of(name)
        place, target, extra, err = self.space.resolve(self.isl, name, action, body.pos, tools, rng or _NoRng())
        body.place = place
        if err:
            return False, err
        notes = []
        if target is not None and body.pos is not None:
            d = self.space.path_length(body.pos, target, tools)
            if d is None:
                return False, f"couldn't find a way to the {place} with the tools you carry"
            notes = self._trip_wear(name, body.pos, target)
            body.walked_today += d
            body.pos = tuple(target)
        act = {**action, **extra, **({"_tool": need} if need else {})}
        ok, res = self.isl.act(name, act, hour)
        self.space.after_act(self.isl, name, act, ok)
        if ok and need and not act.get("none_seen"):  # looking around without finding anything doesn't wear it
            note = self.isl.use_tool(vil, need)
            if note:
                notes.append(note)
                self.broke.append((name, need, act.get("do")))
        return ok, res + "".join(f"; {n}" for n in notes)

    def wander(self, name: str, steps: int, rng) -> bool:
        """Free time on the grid: a short random walk. Returns False where wandering isn't modelled."""
        body = self.b[name]
        home = self.home_point(name)
        if body.pos is None or home is None:
            return False
        new = self.space.wander(body.pos, home, steps, rng)
        if new is None:
            return False
        body.walked_today += steps
        body.pos, body.place = tuple(new), "out for a walk"
        return True

    def where_note(self, name: str) -> str:
        return self.space.where_note(self.isl, name, self.b[name].pos, self.tools_of(name))

    def nearby(self, names: list[str], radius: float) -> dict[str, list[str]]:
        """Who each villager can see close by right now (no LLM involved)."""
        out = {n: [] for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa, pb = self.b[a].pos, self.b[b].pos
                if pa is None or pb is None:
                    continue
                if ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5 <= radius:
                    out[a].append(b)
                    out[b].append(a)
        return out

    def state(self, name: str) -> dict:
        body = self.b[name]
        return {"place": body.place, "pos": list(body.pos) if body.pos else None,
                "walked_today": round(body.walked_today, 2)}


class _NoRng:
    """Stand-in when ordering doesn't need randomness (target choice is deterministic)."""
    def choice(self, seq):
        return seq[0]

    def random(self):
        return 0.0
