"""The island's NPCs: who runs each building, and what they say when asked.

NPCs are rules, not LLM agents: each one answers `ask` with fixed text built from the island's
state. Isabelle is the one exception that also calls an LLM, for the nightly report and the
optional morning announcement; the simulation makes that call with her system prompt and prompts.

An NPC holds no state of its own (everything they know is on the `Island`), so these objects are
never part of a checkpoint. Villagers are a separate class (`Villager`); the two share no parent.

This module imports nothing from `world` at import time, because `world` builds `NPCS` from the
roster here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .space import version_of

if TYPE_CHECKING:
    from .body import Body
    from .space import Outcome, Point
    from .villager import Villager
    from .world import Island


class NPC:
    """Someone who runs a building and answers `ask` with fixed text."""

    key: str = ""  # how the simulation names them (prompt files, events)
    name: str = ""  # how villagers see the name
    aliases: tuple[
        str, ...
    ] = ()  # lower-case names `ask` accepts, in the order `NPCS` lists them
    building: str = ""  # the building's name on the map
    place: str = ""  # the key of its opening hours
    listed: bool = True  # whether plans offer "ask <name>"
    gives_map: bool = False  # hands out an island map (prompt version 3 on)

    def describe(self, isl: Island, vil: Villager) -> str:
        """What they say when asked: what they do, and what it costs."""
        raise NotImplementedError

    def ask(self, isl: Island, vil: Villager, hour: int) -> Outcome:
        """Answer a villager who asks. Tom Nook and Isabelle also hand over an island map to anyone who carries none
        (prompt version 3 on).
        """
        from .world import MAP

        text = self.describe(isl, vil)
        if self.gives_map and version_of(isl) >= 3 and vil.inventory[MAP] < 1:
            if isl.space(vil) < 1:
                text += " I'd give you a map of the island, but your pockets are full."
            else:
                vil.inventory[MAP] += 1
                text += (
                    " And here's a map of the island; keep it with you and you'll always know where things are. "
                    "(You got an island map.)"
                )
        return True, f"asked {text}"


class TomNook(NPC):
    """Tom Nook at Resident Services: house upgrades, moving home, bridges and stairs."""

    key, name, aliases = "tom nook", "Tom Nook", ("tom nook",)
    building, place = "Resident Services", "resident_services"
    gives_map = True

    def describe(self, isl: Island, vil: Villager) -> str:
        """The next house upgrade and the loan, moving home, and the bridge and stairs funds."""
        from .world import structures_cfg

        w = isl.w
        version = version_of(isl)
        levels = w["house_levels"]
        if vil.house + 1 < len(levels):
            nxt = levels[vil.house + 1]
            up = (
                f"I can build you a {nxt['name']} (storage {nxt['storage']}, room for {nxt['interior']} pieces "
                f"of furniture inside) right away, on a loan of {nxt['cost']} bells with no interest and no deadline"
            )
        else:
            up = "Your house is as big as it gets"
        if vil.loan:
            up += f". You still owe {vil.loan} bells, and that needs paying off before the next upgrade"
        else:
            up += ". A loan has to be paid off before the next upgrade"
        free = isl.open_sites()
        sites = (
            ", ".join(f"{s} ({isl.sites[s]['area']})" for s in free) or "none right now"
        )
        br = w["bridge"]
        bridge = (
            "The bridge is built."
            if isl.bridge_built
            else f"The {br['name']} costs {br['cost']} bells, {isl.bridge_fund} raised so far; anyone can chip in, "
            + (
                "and it opens more rocks and trees to everyone."
                if version < 15
                else (
                    "and once built it crosses the river by Resident Services, so nobody needs a vaulting pole "
                    "there, and it opens more rocks and trees to everyone."
                    if version < 17
                    else "and once built it crosses the river by Resident Services, so nobody needs a vaulting "
                    "pole there."
                )
            )
        )
        if version >= 17:
            cfg = structures_cfg(w)
            proposed = "; ".join(
                p["name"] + (", built" if p["built"] else f", {p['fund']} raised")
                for p in isl.projects
            )
            bridge += (
                f" Anyone can also propose another bridge, or stairs up a cliff that anyone can climb without "
                f"a ladder, right where they stand; each is built once {cfg['cost']} bells are donated here. "
                f"The island can have {cfg['max']} in all, built or proposed, and has {len(isl.structures())}"
                + (f": the {w['bridge']['name']}; {proposed}" if proposed else "")
                + "."
            )
        return (
            f"Tom Nook: {up}. I can also move your home to a free homesite overnight for "
            f"{w.get('move_house_cost', 10000)} bells. Free homesites: {sites}. {bridge}"
        )


class Isabelle(NPC):
    """Isabelle at Resident Services: ordinances, and the island's nightly report."""

    key, name, aliases = "isabelle", "Isabelle", ("isabelle",)
    building, place = "Resident Services", "resident_services"
    gives_map = True

    def describe(self, isl: Island, vil: Villager) -> str:
        """The ordinance in effect, the others, and what setting one costs."""
        from .world import ORDINANCE_TEXT, ordinance_name

        ords = isl.w.get("ordinances", {})
        opts = "; ".join(
            f"{k.replace('_', ' ')}: {ORDINANCE_TEXT.get(k, k).split(': ', 1)[-1]}"
            for k in ords.get("types", {})
            if k != "none"
        )
        return (
            f"Isabelle: The ordinance now is {ordinance_name(isl.ordinance).lower()}. "
            f"Anyone can pay me {ords.get('cost', 0)} bells to set one for the whole island from the next "
            f"morning, or to lift it (none). Options: {opts}."
        )

    # The part of Isabelle that is an LLM: the nightly report and the morning announcement.
    @property
    def system(self) -> str:
        """Her system prompt for the report and the announcement."""
        from .prompts import ISABELLE_SYSTEM

        return ISABELLE_SYSTEM

    def summary_prompt(self, day: int, digest: str) -> str:
        """The prompt for her nightly report, from the day's digest."""
        from .prompts import isabelle_summary_prompt

        return isabelle_summary_prompt(day, digest)

    def announce_prompt(self, day: int, public: str, version: int) -> str:
        """The prompt for her morning announcement, from what happened in public."""
        from .prompts import isabelle_announce_prompt

        return isabelle_announce_prompt(day, public, version)

    def backend_name(self, override: str | None, cfg: dict) -> str:
        """The LLM backend that writes for her: the override, else the one in her settings, else the default."""
        return override or cfg.get("backend") or "default"


class Blathers(NPC):
    """Blathers at the museum: donations and fossil assessment."""

    key, name, aliases = "blathers", "Blathers", ("blathers",)
    building, place = "museum", "museum"

    def describe(self, isl: Island, vil: Villager) -> str:
        """What the museum takes, how fossils are assessed, and what it holds so far."""
        from .world import FOSSIL

        return (
            "Blathers: I take the first of each kind of fish, bug and fossil for the museum. It stays there for "
            "everyone to see, with the donor's name, and I don't pay for donations. Bring me any fossils you dig "
            "up and I'll assess them for free: until then nobody knows what they are, and Timmy and Tommy pay "
            f"{isl.sell_price(FOSSIL)} bells for an unassessed one. So far: {isl.museum_line()}."
        )


class TimmyAndTommy(NPC):
    """Timmy and Tommy at Nook's Cranny: they buy almost anything and sell a daily stock."""

    key, name, aliases = (
        "timmy and tommy",
        "Timmy and Tommy",
        ("timmy", "tommy", "timmy and tommy"),
    )
    building, place = "Nook's Cranny", "cranny"

    def describe(self, isl: Island, vil: Villager) -> str:
        """What the shop does, and what it would pay for each thing the villager carries (prices they have then
        heard).
        """
        mine = {
            k: isl.sell_price(k)
            for k in sorted(vil.inventory)
            if vil.inventory[k] > 0 and k in isl.prices
        }
        vil.prices_seen.update(mine)
        offer = (
            ", ".join(f"{k} {p}" for k, p in mine.items())
            or "nothing, your pockets are empty"
        )
        tools = ", ".join(isl.tool_recipes())
        ready = (
            ", and so are the tools themselves, ready to use, for more. A recipe card teaches you to make "
            "the thing as often as you like."
            if isl.w["shops"]["cranny"].get("tool_markup")
            else "."
        )
        return (
            f"Timmy and Tommy: We buy almost anything at a fixed price, and sell a small stock that changes "
            f"every day, plus one recipe card. Recipe cards for tools ({tools}) are always in stock{ready} "
            f"For what you're carrying we'd pay: {offer}."
        )


class AbleSisters(NPC):
    """The Able Sisters at their clothes shop."""

    key, name, aliases = (
        "able sisters",
        "Able Sisters",
        ("able sisters", "the able sisters"),
    )
    building, place = "Able Sisters", "able"

    def describe(self, isl: Island, vil: Villager) -> str:
        """That they sell clothes, and that others see what is worn."""
        return "The Able Sisters: We sell clothes, a few different ones every day. Others see what you wear."


class RescueService(NPC):
    """Prompt version 15: takes a stranded villager home, once a day, free. Not someone to `ask`: a plan
    calls them with the `call_rescue` action, wherever the villager is.
    """

    key, name = "rescue service", "Rescue Service"
    listed = False

    def take_home(self, body: Body) -> Outcome:
        """It comes only when there is no way home with the tools they carry, and says why they got stuck. A
        second call that day is turned down: they get home when they fall asleep.
        """
        isl, space = body.isl, body.space
        home = body.home_point()
        if home is None or body.pos is None:
            return False, "you have no home to be taken back to yet"
        if not body.stuck():
            here = tuple(body.pos) == tuple(home)
            return False, (
                "you're at home already"
                if here
                else "you can get home from here with the tools you carry, so the Rescue Service didn't come"
            )
        rescued = isl.__dict__.setdefault("rescued", {})
        if rescued.get(body.name) == isl.day:
            return False, (
                "the Rescue Service already took you home once today and only comes once a day; you'll be "
                "back home when you go to sleep tonight"
            )
        rescued[body.name] = isl.day
        where = (
            space.region(tuple(body.pos))
            if hasattr(space, "region")
            else "where you were"
        )
        gap = (
            space.reach_gap(body.pos, [home], body.tools())
            if hasattr(space, "reach_gap")
            else ""
        )
        why = {
            "a vaulting pole": "the way home crosses the river, and you carry no vaulting pole",
            "a ladder": "the way home climbs a cliff, and you carry no ladder",
            "a ladder and a vaulting pole": "the way home crosses the river and climbs a cliff, and you carry "
            "neither a vaulting pole nor a ladder",
        }.get(gap, "there was no way home " "with the tools you carry")
        body.pos, body.place = cast("Point", tuple(home)), "home"
        return True, (
            f"the Rescue Service took you home from {where}, and gave you a talking-to: you were stuck "
            f"because {why}. Ladders and vaulting poles wear out a little on every trip that needs them, "
            "and your plans show how many uses each has left; Nook's Cranny always sells them, and their "
            "recipe cards. The Rescue Service comes once a day: get stuck again today and you wait until "
            "you go to sleep to be home"
        )


ISABELLE = Isabelle()
RESCUE = RescueService()
ROSTER: list[NPC] = [
    TomNook(),
    ISABELLE,
    Blathers(),
    TimmyAndTommy(),
    AbleSisters(),
]  # who can be asked
_BY_ALIAS = {a: npc for npc in ROSTER for a in npc.aliases}


def find(who: object) -> NPC | None:
    """The NPC a villager means by `who` (an `ask` action's "who"), or None."""
    return _BY_ALIAS.get(str(who or "").strip().lower())
