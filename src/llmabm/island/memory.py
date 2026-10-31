"""Long-term memory: what each villager chooses to hold on to, fading unless they come back to it.

A basic model, loosely after what is known about human memory:
- Consolidation happens at night. After writing their page, a villager can name up to `picks`
  things from the day to hold on to, in their own words. Nothing else from the day is kept
  beyond yesterday's list of events and the notes of the last `notes_days` days. What counts
  as memorable is the villager's call, not a rule here.
- Forgetting follows a curve: a memory's strength is exp(-days since last recalled / stability).
  New memories have `stability` days; below `threshold` they are forgotten for good.
- Rehearsal: naming an older memory again (by its number) doubles its stability, so things a
  villager keeps coming back to last longer and longer (spacing and testing effects).
- A villager who doesn't write that night (by choice or out of energy) keeps nothing new.

Only memories still above the threshold are shown, at most `shown` of them, strongest first
and then in the order they happened.
"""

from __future__ import annotations

import math
import re

DEFAULTS = {"picks": 3, "shown": 10, "stability": 4.0, "threshold": 0.25, "notes_days": 2, "max_chars": 200}


def settings(cfg: dict | None) -> dict | None:
    """The memory settings, or None for the older rule (keep the last memory_size notes)."""
    cfg = cfg or {}
    return {**DEFAULTS, **cfg} if cfg.get("mode") == "consolidate" else None


def strength(m: dict, day: int) -> float:
    return math.exp(-(day - m["last"]) / m["stability"])


def recalled(memories: list[dict], day: int, cfg: dict, limit: int | None = None) -> list[dict]:
    """What comes to mind on `day`: memories still above the threshold, strongest first up to
    the limit, then in the order they happened."""
    alive = [m for m in memories if strength(m, day) >= cfg["threshold"]]
    top = sorted(alive, key=lambda m: (-strength(m, day), -m["day"]))[: limit or cfg["shown"]]
    return sorted(top, key=lambda m: (m["day"], m["id"]))


def memory_lines(memories: list[dict], day: int, cfg: dict, limit: int | None = None, numbered: bool = False) -> str:
    out = []
    for i, m in enumerate(recalled(memories, day, cfg, limit), 1):
        out.append(f"{f'{i}. ' if numbered else '- '}(day {m['day']}) {m['text']}")
    return "\n".join(out) or "(nothing yet)"


_REMEMBER = re.compile(r"^\W*remember\W*:\s*(.*)$", re.I | re.M)


def split_picks(text: str) -> tuple[str, list[str]]:
    """(page, picks) from a nightly reply: the page, then an optional `Remember:` section."""
    m = _REMEMBER.search(text or "")
    if not m:
        return (text or "").strip(), []
    lines = [m.group(1)] + text[m.end():].splitlines()
    picks = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip() for ln in lines]
    return text[: m.start()].strip(), [p for p in picks if p]


def consolidate(memories: list[dict], picks: list[str], day: int, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Apply one night's picks. A pick that is a number (\"2\" or \"#2\") rehearses the memory with
    that number in tonight's list; anything else becomes a new memory. Returns (new, rehearsed)."""
    shown = recalled(memories, day, cfg)
    new, rehearsed = [], []
    for p in picks[: int(cfg["picks"])]:
        ref = re.fullmatch(r"#?\s*(\d+)\.?(?:\s.*)?", p)
        if ref and 1 <= int(ref.group(1)) <= len(shown):
            m = shown[int(ref.group(1)) - 1]
            if m in rehearsed:
                continue
            m["stability"] *= 2
            m["last"] = day
            m["times"] += 1
            rehearsed.append(m)
            continue
        m = {"id": 1 + max((x["id"] for x in memories), default=0), "day": day, "text": p[: int(cfg["max_chars"])],
             "stability": float(cfg["stability"]), "last": day, "times": 1}
        memories.append(m)
        new.append(m)
    return new, rehearsed


def forget(memories: list[dict], day: int, cfg: dict) -> list[dict]:
    """Drop what has faded below the threshold by `day`. Returns what was forgotten."""
    gone = [m for m in memories if strength(m, day) < cfg["threshold"]]
    memories[:] = [m for m in memories if m not in gone]
    return gone
