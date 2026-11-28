"""Space: where things are and how far apart they are.

The villagers' minds never see this layer. Bodies use it to walk, to decide who gets
somewhere first, and to tell whether a place can be reached at all. `GridSpace` in
grid.py is the tile island that implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import random

    from .world import Island

# The shapes that go around between the modules, named once.
Point = tuple[float, float]  # a place on the island, as x and y
Cell = tuple[int, int]  # one tile of the grid, as (x, y)
# Things and how many of each ("bells" is money): one side of a hand-over.
Side = dict[str, int]
Action = dict[str, Any]  # one thing to do: {"do": <kind>, ...what that kind needs}
Event = dict[str, Any]  # one line of events.jsonl
# Whether an action worked, and what the villager is told about it.
Outcome = tuple[bool, str]


def version_of(isl: Any) -> int:
    """The prompt version an island (or a stand-in for one) runs on; 1 when it carries none."""
    return getattr(isl, "prompt_version", 1)


class Space:
    """What bodies and the simulation need from a space."""

    def coords_of_place(self, place: str) -> Point | None:
        """Where a place's door is, or None if the space has no such place."""
        raise NotImplementedError

    def coords_of_home(self, site: str) -> Point | None:
        """Where a homesite's door is, or None if there is no such site."""
        raise NotImplementedError

    def path_length(
        self, a: Point, b: Point, tools: frozenset[str] = frozenset()
    ) -> float | None:
        """Walking distance from a to b with the given tools, or None if b can't be reached."""
        raise NotImplementedError

    def route_tools(self, a: Point, b: Point, tools: frozenset[str]) -> frozenset[str]:
        """Which movement tools the walk from a to b uses (they wear out with use)."""
        return frozenset()

    def resolve(
        self,
        island: Island,
        name: str,
        action: Action,
        pos: Point | None,
        tools: frozenset[str],
        rng: random.Random,
    ) -> tuple[str, Point | None, dict[str, Any], str | None]:
        """Where an action happens: (place label, where to stand, extra action fields, error)."""
        raise NotImplementedError

    def attach(self, island: Island) -> None:
        """Lay out what is on the island in the space, once, when the two are joined."""
        pass

    def new_day(self, island: Island, rng: random.Random) -> None:
        """Morning: whatever the space renews each day."""
        pass

    def hour_tick(self, island: Island, hour: int, rng: random.Random) -> None:
        """Start of each hour: things that appear and leave (bugs, fish shadows)."""

    def look(
        self, island: Island, name: str, pos: Point | None, hour: int
    ) -> list[str] | None:
        """End of each hour: what a villager sees around where they are."""

    def sync(self, island: Island) -> None:
        """Catch up with the island after it changed: new trees, broken rocks, a built bridge."""
        pass

    def after_act(self, island: Island, name: str, action: Action, ok: bool) -> None:
        """After an action: what it changed in the space."""
        pass

    def wander(
        self, start: Point, home: Point, steps: int, rng: random.Random
    ) -> Point | None:
        """Where a walk of `steps` from `start` ends, or None where there is nowhere to walk."""
        return None

    def where_note(
        self, island: Island, name: str, pos: Point | None, tools: frozenset[str]
    ) -> str:
        """What a villager knows about getting around from where they are; empty where there is no layout."""
        return ""

    def _cant_reach(
        self,
        island: Island,
        msg: str,
        start: Point | None,
        targets: list[Point],
        tools: frozenset[str],
    ) -> str:
        """A failure to get somewhere, saying which tool is missing where the space knows (GridSpace)."""
        return msg

    def map_text(self, island: Island) -> str:
        """The island map a villager can get from Tom Nook or Isabelle; empty where there is no layout."""
        return ""
