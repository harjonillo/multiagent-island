"""Mental energy: a daily budget of LLM tokens per villager, split three ways.

- planning: the plan calls (05:00, 12:00, 19:00)
- thinking: the private "thoughts" and "note" text in every reply, and the nightly reflection
- talking: noticing someone and deciding whether to approach, each line in a conversation,
  and the evening at the plaza

One point is about `tokens_per_point` tokens read or written, estimated as characters / 4 so
the cost doesn't depend on the backend. Everything a villager reads counts, including its
system prompt, which is the biggest part of most calls. Villagers choose how to split what is
left each time they plan; the split they last chose is how tomorrow's energy is divided.
When a part runs out, the villager can't do that kind of thinking until the next morning:
- no planning energy: they follow habit (yesterday's plan for the same part of the day)
- no talking energy: they don't approach anyone, only nod when spoken to, and skip the plaza
- no thinking energy: their replies carry no thoughts or notes, and they skip reflecting

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
        base = cfg.get("split") or {"planning": 0.45, "thinking": 0.25, "talking": 0.3}
        per = cfg.get("villagers") or {}
        self.split = {n: _norm(per.get(n) or base) for n in names}
        self.left = {n: {k: 0.0 for k in KINDS} for n in names}
        self.spent = {n: defaultdict(float) for n in names}
        self.skipped = {n: defaultdict(int) for n in names}
        self.tokens = defaultdict(float)       # estimated tokens per villager today (enabled or not)
        self.cost_sum = defaultdict(float)     # call type -> points, for "typical cost" hints
        self.cost_n = defaultdict(int)
        self.estimates = {"plan": 30, "notice": 5, "chat": 18, "evening": 30, "reflect": 12, **(cfg.get("estimates") or {})}
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
            kept = self.carry * sum(self.left[n].values())
            total = self.daily + kept
            self.left[n] = {k: total * self.split[n][k] for k in KINDS}
            self.spent[n] = defaultdict(float)
            self.skipped[n] = defaultdict(int)
        self.tokens = defaultdict(float)

    def day_record(self) -> dict:
        return {n: {"split": {k: round(v, 3) for k, v in self.split[n].items()},
                    "spent": {k: round(self.spent[n][k], 1) for k in KINDS},
                    "left": {k: round(v, 1) for k, v in self.left[n].items()},
                    "skipped": dict(self.skipped[n]), "tokens": round(self.tokens[n])} for n in self.left}

    # --- costs -----------------------------------------------------------------------
    def points(self, *texts: str) -> float:
        return sum(len(t or "") for t in texts) / CHARS_PER_TOKEN / self.tpp

    def typical(self, what: str) -> int:
        if self.cost_n[what]:
            return max(1, round(self.cost_sum[what] / self.cost_n[what]))
        return int(self.estimates.get(what, 10))

    def can(self, name: str, kind: str, need: float) -> bool:
        """Is there energy of this kind for a call that reads `need` points (plus a short reply)?"""
        if not self.enabled or name not in self.left:
            return True
        return self.left[name][kind] >= need + 1.0

    def has_thinking(self, name: str) -> bool:
        return not self.enabled or name not in self.left or self.left[name]["thinking"] > 0.5

    def skip(self, name: str, kind: str) -> None:
        if name in self.skipped:
            self.skipped[name][kind] += 1

    def record(self, name: str, kind: str | None, what: str, read: float, written: float, thought: float,
               immediate: bool) -> None:
        """Queue (or apply) the charge for one call. Calls made in parallel are queued and applied
        in a fixed order afterwards, so a resumed run charges exactly the same."""
        item = (name, kind, what, read, written, thought)
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
        name, kind, what, read, written, thought = item
        if name not in self.left:
            return
        total = read + written
        self.tokens[name] += total * self.tpp
        self.cost_sum[what] += total
        self.cost_n[what] += 1
        if not kind:
            return
        thought = min(thought, written)
        self._take(name, kind, total - thought)
        self._take(name, "thinking", thought)

    def _take(self, name: str, kind: str, pts: float) -> None:
        self.spent[name][kind] += pts
        if self.enabled:
            self.left[name][kind] = max(0.0, self.left[name][kind] - pts)

    # --- choices ---------------------------------------------------------------------
    def resplit(self, name: str, shares) -> dict | None:
        """The villager re-divides what is left today; the new split also sets tomorrow's."""
        if not self.enabled or not isinstance(shares, dict) or name not in self.left:
            return None
        try:
            clean = {k: max(0.0, float(shares.get(k, 0) or 0)) for k in KINDS}
        except (TypeError, ValueError):
            return None
        if sum(clean.values()) <= 0:
            return None
        self.split[name] = _norm(clean)
        total = sum(self.left[name].values())
        self.left[name] = {k: total * self.split[name][k] for k in KINDS}
        return self.split[name]

    def status_line(self, name: str) -> str:
        if not self.enabled or name not in self.left:
            return ""
        lf = self.left[name]
        return (f"Mental energy left today: {round(sum(lf.values()))} of {round(self.daily)} "
                f"(planning {round(lf['planning'])}, thinking {round(lf['thinking'])}, talking {round(lf['talking'])}). "
                f"Typical costs: a plan {self.typical('plan')}, noticing someone {self.typical('notice')}, "
                f"one thing you say in a conversation {self.typical('chat')}, an evening at the plaza "
                f"{self.typical('evening')}, the nightly reflection {self.typical('reflect')}; long private thoughts "
                "use thinking energy.")


def _norm(shares: dict) -> dict:
    vals = {k: max(0.0, float(shares.get(k, 0) or 0)) for k in KINDS}
    s = sum(vals.values()) or 1.0
    return {k: v / s for k, v in vals.items()}
