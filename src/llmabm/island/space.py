"""Space: where things are and how far apart they are.

The villagers' minds never see this layer. Bodies use it to walk, to decide who gets
somewhere first, and to tell whether a place can be reached at all.

`PlaceSpace` is the current model: named places and homesites on a 10x10 plane,
straight-line distances, everything reachable. A tile grid with cliffs and rivers
(needing a ladder or a vaulting pole) will be another `Space` with the same methods.
"""

from __future__ import annotations

Point = tuple[float, float]


class Space:
    def coords_of_place(self, place: str) -> Point | None:
        raise NotImplementedError

    def coords_of_home(self, site: str) -> Point | None:
        raise NotImplementedError

    def path_length(self, a: Point, b: Point, tools: frozenset[str] = frozenset()) -> float | None:
        """Walking distance from a to b with the given tools, or None if b can't be reached."""
        raise NotImplementedError

    def describe(self) -> str:
        """Short text description of the island's layout for villager prompts."""
        raise NotImplementedError

    # Optional hooks; the named-place map needs none of them.
    def attach(self, island) -> None:
        pass

    def new_day(self, island, rng) -> None:
        pass

    def hour_tick(self, island, hour: int, rng) -> None:
        """Start of each hour: things that appear and leave (bugs, fish shadows)."""

    def look(self, island, name: str, pos, hour: int) -> None:
        """End of each hour: what a villager sees around where they are."""

    def rules_text(self) -> str:
        """Extra island rules that only apply to this kind of space."""
        return ""

    def sync(self, island) -> None:
        pass

    def resolve(self, island, name: str, action: dict, pos, tools, rng):
        """Where an action happens: (place label, where to stand, extra action fields, error)."""
        from .world import location_of

        if str(action.get("do", "")).lower() == "visit":
            host = action.get("who")
            site = island.home.get(host) if host in island.home else None
            return f"{host}'s home", (self.coords_of_home(site) if site else None), {}, None
        place = location_of(action)
        if place == "home":
            site = island.home.get(name)
            return place, (self.coords_of_home(site) if site else None), {}, None
        if place == "clifftop":
            place = "river"
        return place, self.coords_of_place(place), {}, None

    def after_act(self, island, name: str, action: dict, ok: bool) -> None:
        pass

    def wander(self, start, home, steps: int, rng):
        return None  # no wandering on the named-place map: free time is spent at home

    def where_note(self, island, name: str, pos, tools) -> str:
        return ""


class PlaceSpace(Space):
    def __init__(self, world: dict):
        mp = world.get("map") or {}
        self.places: dict[str, Point] = {k: tuple(v) for k, v in (mp.get("places") or {}).items()}
        self.sites: dict[str, Point] = {k: tuple(v["at"]) for k, v in (mp.get("homesites") or {}).items()}

    def coords_of_place(self, place: str) -> Point | None:
        return self.places.get(place)

    def coords_of_home(self, site: str) -> Point | None:
        return self.sites.get(site)

    def path_length(self, a: Point, b: Point, tools: frozenset[str] = frozenset()) -> float | None:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def describe(self) -> str:
        return ", ".join(f"{p} {tuple(xy)}" for p, xy in self.places.items())
