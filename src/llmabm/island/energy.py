"""Mental energy: one daily budget of LLM tokens per villager, spent on whatever they choose.

Every villager call draws from the same pool: plans, noticing someone and deciding whether to
approach, each line in a conversation, the evening at the plaza, and writing at night. What it
is spent on is still recorded in three kinds, for analysis only:
- planning: the plan calls (05:00 and 15:00)
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

Reasoning models also think before they answer, in text the villager never writes down.
That hidden reasoning is always recorded (tokens per villager and call type, as the provider
reports them). With `charge_reasoning: true` it is also charged to the pool, like any other
thinking; by default it is free, so only what a villager reads and writes costs energy.

From prompt version 7 villagers are told about two directions instead: inward (planning and
thinking, the first two kinds) and outward (talking). Each morning's plan splits the day's total
between them, and whatever is left over is spare. The split is only a plan: it is shown back to them
with what they have spent so far, and nothing stops them going over it. There is no habit: a
villager too tired to plan keeps what they already planned.

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
        self.charge_reasoning = bool(cfg.get("charge_reasoning", False))
        self.left = {n: 0.0 for n in names}
        self.spent = {n: defaultdict(float) for n in names}
        self.skipped = {n: defaultdict(int) for n in names}
        self.tokens = defaultdict(float)       # estimated tokens sent and received per villager today, all of it
        self.reasoning = {n: defaultdict(float) for n in names}  # hidden reasoning tokens today, by call type
        self.cost_sum = defaultdict(float)     # call type -> points, for "typical cost" hints
        self.cost_n = defaultdict(int)
        self.estimates = {"plan": 12, "notice": 3, "chat": 8, "evening": 12, "reflect": 5, **(cfg.get("estimates") or {})}
        self._pending: list[tuple] = []
        self._lock = threading.Lock()
        self.plans: dict[str, dict | None] = {n: None for n in names}  # today's split, from version 7

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        state.setdefault("charge_reasoning", False)  # checkpoints from before reasoning was tracked
        state.setdefault("reasoning", {n: defaultdict(float) for n in state["left"]})
        state.setdefault("plans", {n: None for n in state["left"]})  # checkpoints from before energy plans
        self.__dict__.update(state)
        self._lock = threading.Lock()

    # --- daily cycle -------------------------------------------------------------
    def new_day(self) -> None:
        for n in self.left:
            self.left[n] = self.daily + self.carry * self.left[n]
            self.spent[n] = defaultdict(float)
            self.skipped[n] = defaultdict(int)
            self.reasoning[n] = defaultdict(float)
            self.plans[n] = None
        self.tokens = defaultdict(float)

    def day_record(self) -> dict:
        return {n: {"spent": {k: round(self.spent[n][k], 1) for k in KINDS}, "left": round(self.left[n], 1),
                    "skipped": dict(self.skipped[n]), "tokens": round(self.tokens[n]),
                    "reasoning": {k: round(v) for k, v in self.reasoning[n].items()},
                    **({"plan": self.plans[n]} if self.plans.get(n) else {})} for n in self.left}

    # --- the day's plan (prompt version 7) -------------------------------------------
    def set_plan(self, name: str, plan) -> dict | None:
        """Read {"inward": n, "outward": n} from a plan reply. Unreadable parts keep today's plan;
        each side is at most the daily total. Returns the plan if it changed."""
        if not isinstance(plan, dict) or name not in self.left:
            return None
        new = dict(self.plans.get(name) or {})
        for k in ("inward", "outward"):
            try:
                new[k] = max(0, min(round(self.daily), int(round(float(plan[k])))))
            except (KeyError, TypeError, ValueError):
                continue
        if set(new) != {"inward", "outward"} or new == self.plans.get(name):
            return None
        self.plans[name] = new
        return new

    def inward_outward(self, name: str) -> tuple[float, float]:
        """What was spent today, inwardly (planning and thinking) and outwardly (talking)."""
        sp = self.spent[name]
        return sp["planning"] + sp["thinking"], sp["talking"]

    def plan_text(self, name: str) -> str:
        """The day's plan against what was spent, e.g. " Your plan for today: inward 50 (spent 24), ..."."""
        plan = self.plans.get(name)
        if not plan:
            return ""
        spare = max(0, round(self.daily) - plan["inward"] - plan["outward"])
        sides = []
        for k, spent in zip(("inward", "outward"), self.inward_outward(name)):
            over = round(spent) - plan[k]
            sides.append(f"{k} {plan[k]} (spent {round(spent)}"
                         + (f": {over} more than planned, from your spare" if over > 0 else "") + ")")
        used = sum(max(0, round(s) - plan[k]) for k, s in zip(("inward", "outward"), self.inward_outward(name)))
        return f" Your plan for today: {', '.join(sides)}, spare {spare} (used {used})."

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
               immediate: bool, fixed: float = 0.0, social: float = 0.0, hidden: float = 0.0) -> None:
        """Queue (or apply) the charge for one call. `fixed` is the uncharged part of the prompt (rules
        and persona), counted only in `tokens`. `social` is the part of what was read that is other
        villagers' mail, recorded as talking. `hidden` is the model's reasoning, in points: recorded
        always, charged as thinking only with charge_reasoning. Calls made in parallel are queued and
        applied in a fixed order afterwards, so a resumed run charges exactly the same."""
        item = (name, kind, what, read, written, thought, fixed, social, hidden)
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
        hidden = rest[1] if len(rest) > 1 else 0.0
        if name not in self.left:
            return
        self.reasoning[name][what] += hidden * self.tpp
        self.tokens[name] += (read + written + hidden + fixed) * self.tpp
        if self.charge_reasoning:  # hidden reasoning counts as thinking the villager did
            written, thought = written + hidden, thought + hidden
        total = read + written
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

    def status_line(self, name: str, v7: bool = False) -> str:
        if not self.enabled or name not in self.left:
            return ""
        if v7:
            fresh = not self.plans.get(name) and self.left[name] >= self.daily - 0.5
            return ((f"Mental energy today: {round(self.daily)}." if fresh else
                     f"Mental energy left today: {round(self.left[name])} of {round(self.daily)}.")
                    + self.plan_text(name)
                    + f" Typical costs: a plan {self.typical('plan')}, noticing someone {self.typical('notice')}, "
                    f"one thing you say in a conversation {self.typical('chat')}, a turn at the plaza "
                    f"{self.typical('evening')}, writing at night {self.typical('reflect')}; longer replies cost more.")
        return (f"Mental energy left today: {round(self.left[name])} of {round(self.daily)}. "
                f"Typical costs: a plan {self.typical('plan')}, noticing someone {self.typical('notice')}, "
                f"one thing you say in a conversation {self.typical('chat')}, an evening at the plaza "
                f"{self.typical('evening')}, writing at night {self.typical('reflect')}; longer replies cost more.")
