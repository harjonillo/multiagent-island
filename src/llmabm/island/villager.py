"""A villager: everything that belongs to one of the island's LLM-driven residents.

A `Villager` holds what they own and know (bells, pockets, home, recipes, notes, memories, the page
they wrote last night) and is the one place to reach the rest of them:

    vil.spec      who they are: persona, traits, documents, model (the cast file)
    vil.body      where they are, and the walking and acting (body.py)
    vil.memory    what they remember, and what comes to mind in a given situation (memory.py)
    vil.energy    their share of the day's mental energy (energy.py)
    vil.isl       the island they live on, whose rules decide what their actions do

Those four are links to objects kept elsewhere, so they are not saved with the villager: the
simulation makes them again whenever it is built or loaded from a checkpoint. The same goes for the
system prompts, which are rebuilt from the spec and the page.

The island's rules stay on `Island` (world.py); the methods here that ask about rules pass the
question on. NPCs are a separate class (npc.py) and share no parent with villagers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

_LINKS = (
    "spec",
    "body",
    "energy",
    "isl",
    "keeps_page",
    "system",
    "talk_system",
    "light_system",
    "recalled",
)


def _choices() -> dict:
    return {"plaza": True, "write": True, "next": "plan"}


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
    today: list[str] = field(
        default_factory=list
    )  # results of today's actions, in order
    wakes: int = 6  # clock hour the villager usually gets up
    sleeps: int = (
        24  # clock hour they go to bed; values past 24 or below `wakes` mean after midnight
    )
    self_doc: str = (
        ""  # private free-form page, rewritten nightly when reflection is on
    )
    memories: list = field(
        default_factory=list
    )  # what they chose to hold on to at night (see memory.py)
    wearing: dict = field(
        default_factory=dict
    )  # slot -> clothing item; others can see it
    wear: dict = field(
        default_factory=dict
    )  # tool -> uses so far of each one they carry, in-use first
    interior: list = field(
        default_factory=list
    )  # furniture inside their home; visitors see it
    # From prompt version 12, `inventory` is only what is in their pockets; what they keep at home is
    # here, and is only reachable when they are at home.
    home_items: Counter = field(default_factory=Counter)
    home_bells: int = 0
    home_wear: dict = field(
        default_factory=dict
    )  # tool -> uses so far of each one kept at home
    # What the simulation used to keep in its own name-keyed dicts.
    choices: dict = field(
        default_factory=_choices
    )  # where they chose to spend energy next (set in plans)
    yesterday: list[str] = field(
        default_factory=list
    )  # yesterday's `today`, shown in the next day's plans
    noticed: list[str] = field(
        default_factory=list
    )  # things they noticed today without doing anything
    arrival_hour: int | None = None  # the game hour they land on day 0
    # Prompt version 20: everything they lived through, kept for retrieval (see memory.MemoryStore).
    experience: list = field(default_factory=list)
    # Links, made again on load (see the module docstring).
    spec: object = field(default=None, repr=False, compare=False)
    body: object = field(default=None, repr=False, compare=False)
    energy: object = field(default=None, repr=False, compare=False)
    isl: object = field(default=None, repr=False, compare=False)
    keeps_page: bool = field(
        default=False, repr=False, compare=False
    )  # whether nightly reflection is on
    system: str = field(
        default="", repr=False, compare=False
    )  # system prompt for plans
    talk_system: str = field(
        default="", repr=False, compare=False
    )  # ... for conversations and the plaza
    light_system: str = field(
        default="", repr=False, compare=False
    )  # ... for quick decisions and the night
    # What comes to mind for the prompt being written now (prompt version 20): set by the simulation just
    # before the prompt is built, cleared after. None means nothing was retrieved for this prompt.
    recalled: list | None = field(default=None, repr=False, compare=False)

    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k not in _LINKS}

    def __setstate__(
        self, state
    ):  # checkpoints from before a field existed get its default
        fresh = Villager(state.get("name", ""))
        self.__dict__.update({**fresh.__dict__, **state})

    # --- what they own -------------------------------------------------------
    def items(self) -> int:
        return sum(self.inventory.values())

    def home_count(self) -> int:
        return sum(self.home_items.values())

    def capacity(self) -> int:
        """Slots for what they carry."""
        return self.isl.capacity(self)

    # --- who they are --------------------------------------------------------
    @property
    def persona(self) -> str:
        return self.spec.persona

    @property
    def model(self) -> str:
        return self.spec.model

    # --- their day -----------------------------------------------------------
    def awake_window(self) -> tuple[int, int]:
        """[wake, sleep) in game hours, after any ordinance shift."""
        return self.isl.awake_window(self.name)

    def is_awake(self, hour: int) -> bool:
        return self.isl.is_awake(self.name, hour)

    def neighbors(self) -> list[str]:
        return self.isl.neighbors(self.name)

    # --- their mind ----------------------------------------------------------
    def page(self) -> str | None:
        """What they wrote to themselves last night, when the nightly page is on."""
        return self.self_doc if self.keeps_page else None

    def need(self, user: str) -> float:
        """Points of mental energy it takes to read this prompt: what changes from call to call
        (the situation, and their own nightly page), not the fixed rules and persona."""
        return self.energy.pool.points(self.page() or "", user)

    def can_read(self, user: str) -> bool:
        """Whether they have the energy left to read this prompt and answer it."""
        return self.energy.can(self.need(user))

    @property
    def memory(self):
        """Their memory as one thing: what they chose to hold on to and everything they lived through."""
        from .memory import MemoryStore

        return MemoryStore(self)

    def note(self, day: int, text: str) -> None:
        """Keep a note they wrote to themselves in a reply."""
        self.notes.append((day, text))
