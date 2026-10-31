"""Mental energy: one daily budget of LLM tokens per villager, spent on whatever they choose.

Every villager call draws from the same pool: plans, noticing someone and deciding whether to
approach, each line in a conversation, the evening at the plaza, and writing at night. What it
is spent on is still recorded in three kinds, for analysis only:
- planning: the plan calls (05:00, 12:00, 19:00)
- thinking: the private "thoughts" and "note" text in every reply, and the nightly page
- talking: noticing someone, conversations, and the plaza

One point is about `tokens_per_point` tokens read or written, estimated as characters / 4 so
the cost doesn't depend on the backend. A call is charged for what changes from call to call
(the situation in the prompt, and the villager's own nightly page) plus everything written
back. The fixed rules and persona in the system prompt are not charged.

Villagers choose where it goes: in each plan they can skip the plaza tonight, skip writing
tonight, or follow habit at the next planning time (free). When the pool runs out they can't
make any call until the next morning: they follow habit instead of planning, only nod when
spoken to, skip the plaza, and don't write.

Isabelle is an NPC and is not charged.
"""

from __future__ import annotations

import threading
from collections import defaultdict

KINDS = ("planning", "thinking", "talking")
CHARS_PER_TOKEN = 4


class MentalEnergy:
    def __init__(self, cfg: dict | None, names: list[str]):
        cfg = dict(cfg or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.daily = float(cfg.get("daily", 150))
        self.tpp = float(cfg.get("tokens_per_point", 100))
        self.carry = float(cfg.get("carry_over", 0.0))
        self.left = {n: 0.0 for n in names}
        self.spent = {n: defaultdict(float) for n in names}
        self.skipped = {n: defaultdict(int) for n in names}
        self.tokens = defaultdict(float)       # estimated tokens sent and received per villager today, all of it
        self.cost_sum = defaultdict(float)     # call type -> points, for "typical cost" hints
        self.cost_n = defaultdict(int)
        self.estimates = {"plan": 12, "notice": 3, "chat": 8, "evening": 12, "reflect": 5, **(cfg.get("estimates") or {})}
        self._pending: list[tuple] = []
        self._lock = threading.Lock()

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._lock = threading.Lock()

    # --- daily cycle -------------------------------------------------------------
    def new_day(self) -> None:
        for n in self.left:
            self.left[n] = self.daily + self.carry * self.left[n]
            self.spent[n] = defaultdict(float)
            self.skipped[n] = defaultdict(int)
        self.tokens = defaultdict(float)

    def day_record(self) -> dict:
        return {n: {"spent": {k: round(self.spent[n][k], 1) for k in KINDS}, "left": round(self.left[n], 1),
                    "skipped": dict(self.skipped[n]), "tokens": round(self.tokens[n])} for n in self.left}

    # --- costs -----------------------------------------------------------------------
    def points(self, *texts: str) -> float:
        return sum(len(t or "") for t in texts) / CHARS_PER_TOKEN / self.tpp

    def typical(self, what: str) -> int:
        if self.cost_n[what]:
            return max(1, round(self.cost_sum[what] / self.cost_n[what]))
        return int(self.estimates.get(what, 10))

    def can(self, name: str, need: float) -> bool:
        """Is there energy left for a call that reads `need` points (plus a short reply)?"""
        if not self.enabled or name not in self.left:
            return True
        return self.left[name] >= need + 1.0

    def skip(self, name: str, kind: str) -> None:
        if name in self.skipped:
            self.skipped[name][kind] += 1

    def record(self, name: str, kind: str | None, what: str, read: float, written: float, thought: float,
               immediate: bool, fixed: float = 0.0, social: float = 0.0) -> None:
        """Queue (or apply) the charge for one call. `fixed` is the uncharged part of the prompt (rules
        and persona), counted only in `tokens`. `social` is the part of what was read that is other
        villagers' mail, recorded as talking. Calls made in parallel are queued and applied in a
        fixed order afterwards, so a resumed run charges exactly the same."""
        item = (name, kind, what, read, written, thought, fixed, social)
        if immediate:
            self._apply(item)
        else:
            with self._lock:
                self._pending.append(item)

    def settle(self) -> None:
        with self._lock:
            items, self._pending = sorted(self._pending, key=lambda x: (x[0], x[2])), []
        for it in items:
            self._apply(it)

    def _apply(self, item) -> None:
        name, kind, what, read, written, thought, fixed, *rest = item
        social = min(rest[0] if rest else 0.0, read)
        if name not in self.left:
            return
        total = read + written
        self.tokens[name] += (total + fixed) * self.tpp
        self.cost_sum[what] += total
        self.cost_n[what] += 1
        if not kind:
            return
        thought = min(thought, written)
        self.spent[name][kind] += total - thought - social
        self.spent[name]["talking"] += social
        self.spent[name]["thinking"] += thought
        if self.enabled:
            self.left[name] = max(0.0, self.left[name] - total)

    def status_line(self, name: str) -> str:
        if not self.enabled or name not in self.left:
            return ""
        return (f"Mental energy left today: {round(self.left[name])} of {round(self.daily)}. "
                f"Typical costs: a plan {self.typical('plan')}, noticing someone {self.typical('notice')}, "
                f"one thing you say in a conversation {self.typical('chat')}, an evening at the plaza "
                f"{self.typical('evening')}, writing at night {self.typical('reflect')}; longer replies cost more.")
