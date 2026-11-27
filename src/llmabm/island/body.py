"""Bodies: the part of each villager that exists in space.

The LLM (the mind) only plans, thinks and talks. It schedules intents such as
{"hour": 7, "do": "mine_rock"}. The body carries them out: it works out where the
intent takes place, walks there from wherever the villager currently is, checks the
place can be reached with the tools the villager carries, and then hands the action
to the world rules. Tools wear out with use: each action that needs one, and each trip
whose route climbs a cliff with the ladder or jumps the river with the vaulting pole. Bodies also decide who gets somewhere first within an hour (the
shorter walk wins) and go home when their villager falls asleep.

Nothing here calls an LLM, so all of it is deterministic given the RNG.

A `Body` does its own walking and acting. `Bodies` is the set of them: it creates and links them, and
answers the questions that take more than one body (who gets somewhere first, who is near whom). Its
other methods take a villager's name and pass straight through to that villager's body.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .npc import RESCUE
from .space import Point, Space, version_of
from .world import MOVEMENT_TOOLS, Island

if TYPE_CHECKING:
    import random
    from collections.abc import Sequence

    from .villager import Villager


@dataclass
class Body:
    """One villager's body: where they are, the last place they were at, and how far they have walked today."""

    name: str
    pos: Point | None = None  # None until the villager has arrived
    place: str = "home"  # last place the body was at
    walked_today: float = 0.0
    # The set of bodies this one belongs to, for the island and the space it moves in. Not saved in a
    # checkpoint: `Bodies` links it again on load.
    world: Bodies | None = field(default=None, repr=False, compare=False)

    def __getstate__(self) -> dict[str, Any]:
        """What goes into a checkpoint: everything but the link back to `Bodies`."""
        state = dict(self.__dict__)
        state.pop("world", None)
        return state

    @property
    def isl(self) -> Island:
        """The island this body lives on."""
        return cast("Bodies", self.world).isl

    @property
    def space(self) -> Space:
        """The space this body moves in."""
        return cast("Bodies", self.world).space

    @property
    def vil(self) -> Villager:
        """The villager whose body this is."""
        return self.isl.v[self.name]

    # --- positions -----------------------------------------------------------
    def home_point(self) -> Point | None:
        """The door of the villager's home, or None while they have none."""
        site = self.isl.home.get(self.name)
        return self.space.coords_of_home(site) if site else None

    def tools(self) -> frozenset[str]:
        """The tools that change where a villager can walk."""
        inv = self.vil.inventory
        return frozenset(t for t in MOVEMENT_TOOLS if inv[t] > 0)

    def _trip_wear(self, a: Point | None, b: Point | None) -> list[str]:
        """A ladder or pole wears once on a trip whose route climbs a cliff or jumps the river with it."""
        if a is None or b is None or tuple(a) == tuple(b):
            return []
        notes = []
        for t in sorted(self.space.route_tools(a, b, self.tools())):
            note = self.isl.use_tool(self.vil, t)
            if note:
                notes.append(note)
                cast("Bodies", self.world).broke.append((self.name, t, "walking"))
        return notes

    def stuck(self) -> bool:
        """Away from home with no way back with the tools they carry (say a vaulting pole broke on the way out)."""
        home = self.home_point()
        return (
            home is not None
            and self.pos is not None
            and tuple(self.pos) != tuple(home)
            and self.space.path_length(self.pos, home, self.tools()) is None
        )

    def go_home(self, asleep: bool = True) -> None:
        """Back home in free time, or for the night. From prompt version 15 a villager who is awake and has no
        way home with the tools they carry stays where they are (the Rescue Service can take them, once a day);
        falling asleep always brings them home, as the day ends.
        """
        home = self.home_point()
        if not asleep and version_of(self.isl) >= 15 and self.stuck():
            return
        if home is not None and self.pos is not None:
            notes = self._trip_wear(self.pos, home)
            self.walked_today += (
                self.space.path_length(self.pos, home, self.tools()) or 0.0
            )
            self.pos, self.place = cast(Point, tuple(home)), "home"
            for note in notes:
                self.vil.today.append(f"on the way home, {note}")

    def at_home(self) -> bool:
        """Whether the villager is at their own home right now."""
        home = self.home_point()
        return (
            home is not None
            and self.place == "home"
            and self.pos is not None
            and tuple(self.pos) == tuple(home)
        )

    def place_at_home(self) -> None:
        """Put the body at its home's door (nowhere, while the villager has no home)."""
        home = self.home_point()
        self.pos, self.place = (cast(Point, tuple(home)) if home else None), "home"

    # --- movement and action -------------------------------------------------
    def eta(self, action: dict, rng: random.Random | None = None) -> float | None:
        """Walking distance from where the villager is now to where the action happens."""
        tools = self.tools()
        action = self.isl.tidy(action)
        _, target, _, err = self.space.resolve(
            self.isl,
            self.name,
            action,
            self.pos,
            tools,
            cast("random.Random", rng or _NoRng()),  # _NoRng stands in for one
        )
        if err or self.pos is None or target is None:
            return 0.0 if not err else None
        return self.space.path_length(self.pos, target, tools)

    def perform(
        self, action: dict, hour: int, rng: random.Random | None = None
    ) -> tuple[bool, str]:
        """Carry out one action: walk to where it happens, hand it to the world's rules, and wear the tools used.

        Returns:
            Whether it worked, and what the villager is told about it (a tool that broke on the way included).
        """
        isl, space, name, vil = self.isl, self.space, self.name, self.vil
        action = isl.tidy(action)
        if str(action.get("do", "")).lower() == "call_rescue" and version_of(isl) >= 15:
            return self.rescue()
        need, why = isl.tool_for(vil, action)
        if why:
            return False, why
        tools = self.tools()
        place, target, extra, err = space.resolve(
            isl,
            name,
            action,
            self.pos,
            tools,
            cast("random.Random", rng or _NoRng()),  # _NoRng stands in for one
        )
        self.place = place
        if err:
            return False, err
        notes = []
        if target is not None and self.pos is not None:
            d = space.path_length(self.pos, target, tools)
            if d is None:
                return False, space._cant_reach(
                    isl,
                    f"couldn't find a way to the {place} with the tools you carry",
                    self.pos,
                    [target],
                    tools,
                )
            notes = self._trip_wear(self.pos, target)
            self.walked_today += d
            self.pos = cast(Point, tuple(target))
        act = {
            **action,
            **extra,
            **({"_tool": need} if need else {}),
            **({"_at_home": True} if place == "home" else {}),
        }
        ok, res = isl.act(name, act, hour)
        space.after_act(isl, name, act, ok)
        if (
            ok and need and not act.get("none_seen")
        ):  # looking around without finding anything doesn't wear it
            note = isl.use_tool(vil, need)
            if note:
                notes.append(note)
                cast("Bodies", self.world).broke.append(
                    (name, need, cast(str, act.get("do")))
                )
        return ok, res + "".join(f"; {n}" for n in notes)

    def rescue(self) -> tuple[bool, str]:
        """Prompt version 15: the Rescue Service takes a stranded villager home, once a day, free; the action's
        hour is spent on it. What they do and say is theirs (npc.py).
        """
        return RESCUE.take_home(self)

    def go_to(self, landmark: str, label: str) -> tuple[bool, str]:
        """Walk to a building (the plaza, from prompt version 7) in free time. Not an action: nothing
        happens there but being there. Returns whether they got there, and why not.
        """
        if self.pos is None:
            return False, "you haven't arrived yet"
        tools = self.tools()
        # Only the grid has landmarks, and free-time walks only happen on it.
        door = self.space.landmark(self.isl, self.name, landmark, self.pos)  # type: ignore[attr-defined]  # GridSpace only
        best = (
            self.space.nearest(tuple(self.pos), tools, [door])  # type: ignore[attr-defined]  # GridSpace only
            if door is not None
            else None
        )
        if best is None:
            return False, self.space._cant_reach(
                self.isl,
                f"couldn't find a way to the {label} with the tools you carry",
                self.pos,
                [door] if door is not None else [],
                tools,
            )
        _, stand, d = best
        notes = self._trip_wear(self.pos, stand)
        self.walked_today += d
        self.pos, self.place = tuple(stand), label
        return True, "".join(f"; {n}" for n in notes)

    def wander(self, steps: int, rng: random.Random) -> bool:
        """Free time on the grid: a short random walk. Returns False where wandering isn't modelled."""
        home = self.home_point()
        if self.pos is None or home is None:
            return False
        new = self.space.wander(self.pos, home, steps, rng)
        if new is None:
            return False
        self.walked_today += steps
        self.pos, self.place = cast(Point, tuple(new)), "out for a walk"
        return True

    def where_note(self) -> str:
        """What the villager knows about getting around from where they are."""
        return self.space.where_note(self.isl, self.name, self.pos, self.tools())

    def state(self) -> dict:
        """The body as it is saved with a day's state: place, position and distance walked."""
        return {
            "place": self.place,
            "pos": list(self.pos) if self.pos else None,
            "walked_today": round(self.walked_today, 2),
        }


class Bodies:
    """Every villager's body, with the island and the space they share."""

    def __init__(self, island: Island, space: Space) -> None:
        """Make a body for every villager on the island."""
        self.isl = island
        self.space = space
        self.b: dict[str, Body] = {n: Body(n) for n in island.v}
        self.broke: list[tuple[str, str, str]] = (
            []
        )  # (villager, tool, how) since the simulation last looked
        self._link()

    def __setstate__(
        self, state: dict[str, Any]
    ) -> None:  # a body's link back here is not saved, so it is made again on load
        """Restore from a checkpoint and link the bodies again."""
        self.__dict__.update(state)
        self._link()

    def _link(self) -> None:
        """Each body knows the set it belongs to, and each villager their body."""
        for name, body in self.b.items():
            body.world = self
            if name in getattr(self.isl, "v", {}):
                self.isl.v[name].body = body

    # --- questions about more than one body ----------------------------------
    def new_day(self) -> None:
        """A new morning: nobody has walked anywhere yet."""
        for body in self.b.values():
            body.walked_today = 0.0

    def order(
        self, batch: list[tuple[str, dict]], rng: random.Random
    ) -> list[tuple[str, dict]]:
        """Who gets there first this hour: the shortest walk, ties broken at random."""
        keyed = []
        for name, act in batch:
            d = self.b[name].eta(act, rng)
            keyed.append(
                ((float("inf") if d is None else round(d, 3)), rng.random(), name, act)
            )
        keyed.sort(key=lambda k: (k[0], k[1]))
        return [(name, act) for _, _, name, act in keyed]

    def nearby(self, names: list[str], radius: float) -> dict[str, list[str]]:
        """Who each villager can see close by right now (no LLM involved)."""
        out: dict[str, list[str]] = {n: [] for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                pa, pb = self.b[a].pos, self.b[b].pos
                if pa is None or pb is None:
                    continue
                if math.dist(pa, pb) <= radius:
                    out[a].append(b)
                    out[b].append(a)
        return out

    # --- one body, by its villager's name ------------------------------------
    def home_point(self, name: str) -> Point | None:
        """The door of this villager's home."""
        return self.b[name].home_point()

    def tools_of(self, name: str) -> frozenset[str]:
        """The movement tools this villager carries."""
        return self.b[name].tools()

    def stuck(self, name: str) -> bool:
        """Whether this villager is away from home with no way back."""
        return self.b[name].stuck()

    def go_home(self, name: str, asleep: bool = True) -> None:
        """Send this villager home, in free time or for the night."""
        self.b[name].go_home(asleep)

    def at_home(self, name: str) -> bool:
        """Whether this villager is at their own home."""
        return self.b[name].at_home()

    def place_at_home(self, name: str) -> None:
        """Put this villager at their home's door."""
        self.b[name].place_at_home()

    def perform(
        self, name: str, action: dict, hour: int, rng: random.Random | None = None
    ) -> tuple[bool, str]:
        """Have this villager's body carry out an action."""
        return self.b[name].perform(action, hour, rng)

    def rescue(self, name: str) -> tuple[bool, str]:
        """Have the Rescue Service take this villager home."""
        return self.b[name].rescue()

    def go_to(self, name: str, landmark: str, label: str) -> tuple[bool, str]:
        """Walk this villager to a building in free time."""
        return self.b[name].go_to(landmark, label)

    def wander(self, name: str, steps: int, rng: random.Random) -> bool:
        """Take this villager on a short random walk."""
        return self.b[name].wander(steps, rng)

    def where_note(self, name: str) -> str:
        """What this villager knows about getting around."""
        return self.b[name].where_note()

    def state(self, name: str) -> dict:
        """This villager's body as it is saved."""
        return self.b[name].state()


class _NoRng:
    """Stand-in when ordering doesn't need randomness (target choice is deterministic)."""

    def choice(self, seq: Sequence[Any]) -> Any:
        """Always the first."""
        return seq[0]

    def random(self) -> float:
        """Always 0."""
        return 0.0
