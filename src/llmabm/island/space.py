"""Space: where things are and how far apart they are.

The villagers' minds never see this layer. Bodies use it to walk, to decide who gets
somewhere first, and to tell whether a place can be reached at all. `GridSpace` in
grid.py is the tile island that implements it.
"""

from __future__ import annotations

Point = tuple[float, float]


class Space:
    """What bodies and the simulation need from a space."""

    def coords_of_place(self, place: str) -> Point | None:
        raise NotImplementedError

    def coords_of_home(self, site: str) -> Point | None:
        raise NotImplementedError

    def path_length(self, a: Point, b: Point, tools: frozenset[str] = frozenset()) -> float | None:
        """Walking distance from a to b with the given tools, or None if b can't be reached."""
        raise NotImplementedError

    def resolve(self, island, name: str, action: dict, pos, tools, rng):
        """Where an action happens: (place label, where to stand, extra action fields, error)."""
        raise NotImplementedError

    def attach(self, island) -> None:
        pass

    def new_day(self, island, rng) -> None:
        pass

    def hour_tick(self, island, hour: int, rng) -> None:
        """Start of each hour: things that appear and leave (bugs, fish shadows)."""

    def look(self, island, name: str, pos, hour: int) -> None:
        """End of each hour: what a villager sees around where they are."""

    def sync(self, island) -> None:
        pass

    def after_act(self, island, name: str, action: dict, ok: bool) -> None:
        pass

    def wander(self, start, home, steps: int, rng):
        return None

    def where_note(self, island, name: str, pos, tools) -> str:
        return ""
