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

From prompt version 20 memory is retrieved instead (the second half of this file). Everything a villager
lived through is kept: each line of each day, each note, each night's page. Nothing is deleted. What a
prompt shows is the few records that best match the situation the villager is in at that moment:

    score = relevance x recency x (pick_weight if they chose to hold on to it, else 1)

- relevance: how close the record's text is to the situation, by embedding (llm.py), scaled over the
  records being compared so the best match is 1.
- recency: the same forgetting curve as above, but it never reaches zero (`recency_floor`), so an old
  record is harder to recall, not gone.
- picks: what the villager names at night still matters. A pick counts double and, rehearsed, fades
  more slowly. Retrieval never changes how long anything lasts; only the villager's choice does.
"""

from __future__ import annotations

import math
import re

DEFAULTS = {
    "picks": 3,
    "shown": 10,
    "stability": 4.0,
    "threshold": 0.25,
    "notes_days": 2,
    "max_chars": 200,
    # prompt version 20 on: retrieval
    "k": 8,  # records in a plan, a conversation turn or a turn at the plaza
    "k_quick": 4,  # ... in a quick decision (noticing someone, spotting a bug or fish)
    "chunk_chars": 240,  # a longer line (a conversation) is kept as pieces of at most this
    "per_line": 2,  # at most this many pieces of one line in a prompt, so one long talk can't fill it
    "pick_weight": 2.0,  # how much more a record counts when the villager chose to hold on to it
    "recency_floor": 0.1,  # the least an old record's recency counts: harder to recall, never gone
    "relevance_floor": 0.05,
    "query_chars": 1500,
}
RETRIEVAL_VERSION = 20


def settings(cfg: dict | None) -> dict | None:
    """The memory settings, or None for the older rule (keep the last memory_size notes)."""
    cfg = cfg or {}
    return {**DEFAULTS, **cfg} if cfg.get("mode") == "consolidate" else None


def strength(m: dict, day: int) -> float:
    return math.exp(-(day - m["last"]) / m["stability"])


def recalled(
    memories: list[dict], day: int, cfg: dict, limit: int | None = None
) -> list[dict]:
    """What comes to mind on `day`: memories still above the threshold, strongest first up to
    the limit, then in the order they happened."""
    alive = [m for m in memories if strength(m, day) >= cfg["threshold"]]
    top = sorted(alive, key=lambda m: (-strength(m, day), -m["day"]))[
        : limit or cfg["shown"]
    ]
    return sorted(top, key=lambda m: (m["day"], m["id"]))


def memory_lines(
    memories: list[dict],
    day: int,
    cfg: dict,
    limit: int | None = None,
    numbered: bool = False,
) -> str:
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
    lines = [m.group(1)] + text[m.end() :].splitlines()
    picks = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip() for ln in lines]
    return text[: m.start()].strip(), [p for p in picks if p]


def consolidate(
    memories: list[dict], picks: list[str], day: int, cfg: dict
) -> tuple[list[dict], list[dict]]:
    """Apply one night's picks. A pick that is a number (\"2\" or \"#2\") rehearses the memory with
    that number in tonight's list; anything else becomes a new memory. Returns (new, rehearsed).
    """
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
        m = {
            "id": 1 + max((x["id"] for x in memories), default=0),
            "day": day,
            "text": p[: int(cfg["max_chars"])],
            "stability": float(cfg["stability"]),
            "last": day,
            "times": 1,
        }
        memories.append(m)
        new.append(m)
    return new, rehearsed


def forget(memories: list[dict], day: int, cfg: dict) -> list[dict]:
    """Drop what has faded below the threshold by `day`. Returns what was forgotten."""
    gone = [m for m in memories if strength(m, day) < cfg["threshold"]]
    memories[:] = [m for m in memories if m not in gone]
    return gone


# --- prompt version 20: everything is kept, and retrieved by what it is about -------------------


def retrieval(isl) -> bool:
    """Whether this island retrieves memories (long-term memory on, prompt version 20 or later)."""
    return (
        bool(getattr(isl, "memory", None))
        and getattr(isl, "prompt_version", 1) >= RETRIEVAL_VERSION
    )


_AT = re.compile(r"^(\d\d:\d\d) ")


def chunks(text: str, limit: int) -> list[str]:
    """A line as pieces of at most `limit` characters. A conversation ("09:00 ran into Bo: you said ...;
    Bo said ...") is cut between the things said, and each piece keeps the start of the line (when, who,
    where), so it still reads on its own. Anything else that is too long is cut between words.
    """
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return [text] if text else []
    head, sep, rest = text.partition(": ")
    if not sep or len(head) > limit // 2:
        head, rest = "", text
    lead = f"{head}: " if head else ""
    room = max(40, limit - len(lead))
    parts: list[str] = []
    for piece in rest.split("; "):
        while (
            len(piece) > room
        ):  # one thing said that is too long on its own: cut it between words
            cut = piece.rfind(" ", 0, room)
            cut = cut if cut > room // 2 else room
            parts.append(piece[:cut])
            piece = piece[cut:].strip()
        if parts and len(parts[-1]) + 2 + len(piece) <= room:
            parts[-1] += "; " + piece
        else:
            parts.append(piece)
    return [lead + p for p in parts if p]


class MemoryStore:
    """A villager's memory under retrieval: `vil.memories` (what they chose to hold on to, as before)
    and `vil.experience` (everything else they lived through). Reached as `vil.memory`.

    A record: {"id", "day", "at" ("09:00", or None), "seq" (its place in that day), "kind" (event | chat |
    note | page), "text", "src" (the id of the first piece of the same line), "stability", "last", "times"}.
    A pick has no "kind" and keeps the shape it always had. Vectors are not kept here: they are looked
    up by text (llm.CachedEmbeddings), so changing the embedding model needs no change to a checkpoint.
    """

    def __init__(self, vil):
        self.vil = vil

    # --- writing ---------------------------------------------------------------
    def _add(self, day: int, kind: str, text: str, cfg: dict, seq: int) -> list[dict]:
        out = []
        for piece in chunks(text, int(cfg["chunk_chars"])):
            exp = self.vil.experience
            at = _AT.match(piece)
            r = {
                "id": 1 + max((x["id"] for x in exp), default=0),
                "day": day,
                "at": at.group(1) if at else None,
                "seq": seq,
                "kind": kind,
                "text": piece,
                "src": out[0]["id"] if out else None,
                "stability": float(cfg["stability"]),
                "last": day,
                "times": 0,
            }
            exp.append(r)
            out.append(r)
        return out

    def has_day(self, day: int) -> bool:
        return any(
            r["day"] == day and r["kind"] in ("event", "chat")
            for r in self.vil.experience
        )

    def add_day(self, day: int, lines: list[str], notes: list[str], cfg: dict) -> int:
        """Keep one day: what happened, in order, then the notes they wrote that day. Done once, at the
        end of the day (lines are still being added to and changed while the day runs).
        """
        if self.has_day(day) or any(
            r["day"] == day and r["kind"] == "note" for r in self.vil.experience
        ):
            return 0
        n = 0
        for seq, line in enumerate(lines):
            n += len(
                self._add(day, "chat" if ' said "' in line else "event", line, cfg, seq)
            )
        for i, note in enumerate(notes):
            n += len(self._add(day, "note", note, cfg, len(lines) + i))
        return n

    def add_page(self, day: int, text: str, cfg: dict) -> int:
        """Keep the page written on the night of `day`."""
        if not text or any(
            r["day"] == day and r["kind"] == "page" for r in self.vil.experience
        ):
            return 0
        sentences = "; ".join(
            s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()
        )
        return len(self._add(day, "page", sentences, cfg, 10**6))

    # --- reading ---------------------------------------------------------------
    def corpus(self) -> list[dict]:
        """Everything that can come to mind: the picks, and what was lived through and not since picked
        (a record they chose to hold on to lives on as the pick)."""
        return list(self.vil.memories) + [
            r for r in self.vil.experience if not r.get("promoted")
        ]

    def candidates(
        self,
        day: int,
        cfg: dict,
        skip_yesterday: bool = False,
        night: bool = False,
        skip=None,
    ) -> list[dict]:
        """The records a prompt on `day` can draw on: not what that prompt already shows in full. Today is
        always shown (and is only kept at the end of the day); the notes still in `vil.notes` are shown
        (the last `notes_days` days; by `night` the oldest of those days has been dropped); the latest page
        is in the system prompt; a plan also shows yesterday. `skip(record)` leaves out anything else the
        caller shows (the last conversation with the person they are talking to)."""
        notes_from = (
            day - int(cfg["notes_days"]) + (1 if night else 0)
        )  # the first day whose notes are shown
        pages = [r["day"] for r in self.vil.experience if r["kind"] == "page"]
        last_page = max(pages, default=None)
        out = []
        for r in self.corpus():
            kind = r.get("kind")
            if kind is not None:
                if r["day"] >= day:
                    continue
                if kind == "note" and r["day"] >= notes_from:
                    continue
                if kind == "page" and r["day"] == last_page:
                    continue
                if skip_yesterday and kind in ("event", "chat") and r["day"] == day - 1:
                    continue
            if skip is not None and skip(r):
                continue
            out.append(r)
        return out

    def promote(self, r: dict, day: int, cfg: dict) -> dict:
        """They chose to hold on to something they lived through: it becomes a pick, in the same words."""
        mem = self.vil.memories
        m = {
            "id": 1 + max((x["id"] for x in mem), default=0),
            "day": r["day"],
            "text": r["text"][: int(cfg["max_chars"])],
            "stability": float(cfg["stability"]),
            "last": day,
            "times": 1,
            "from": r["id"],
        }
        mem.append(m)
        r["promoted"] = m["id"]
        return m


def is_pick(r: dict) -> bool:
    return r.get("kind") is None


def record_key(r: dict) -> str:
    """A short name for a record in the log: m3 is their third pick, e17 the 17th thing kept."""
    return f"{'m' if is_pick(r) else 'e'}{r['id']}"


def _order(r: dict) -> tuple:
    return (r["day"], r.get("seq", 10**7), 0 if not is_pick(r) else 1, r["id"])


def score_records(
    cands: list[dict], sims: list[float] | None, day: int, cfg: dict
) -> list[tuple[float, dict]]:
    """(score, record) for each candidate, best first. `sims` is each one's similarity to the situation
    (None when there is nothing to compare with: then only recency and picks count). Ties go to the later
    record, then to its id, so the order is the same on every run."""
    if not cands:
        return []
    if sims is None:
        rel = [1.0] * len(cands)
    else:
        lo, hi = min(sims), max(sims)
        rel = [
            max(float(cfg["relevance_floor"]), (s - lo) / (hi - lo)) if hi > lo else 1.0
            for s in sims
        ]
    out = []
    for r, x in zip(cands, rel):
        rec = max(float(cfg["recency_floor"]), strength(r, day))
        out.append(
            (round(x * rec * (float(cfg["pick_weight"]) if is_pick(r) else 1.0), 6), r)
        )
    out.sort(
        key=lambda t: (-t[0], -t[1]["day"], -t[1].get("seq", 10**7), record_key(t[1]))
    )
    return out


def dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def recall(
    cands: list[dict],
    qvec: list[float] | None,
    vectors: dict,
    day: int,
    cfg: dict,
    k: int,
) -> list[tuple[float, dict]]:
    """The `k` records that come to mind, in the order they happened, each with its score."""
    sims = None if qvec is None else [dot(qvec, vectors[r["text"]]) for r in cands]
    top, per_line = [], {}
    for score, r in score_records(cands, sims, day, cfg):
        if len(top) >= max(0, int(k)):
            break
        line = None if is_pick(r) else (r.get("src") or r["id"])
        if line is not None:
            if per_line.get(line, 0) >= int(cfg["per_line"]):
                continue
            per_line[line] = per_line.get(line, 0) + 1
        top.append((score, r))
    return sorted(top, key=lambda t: _order(t[1]))


def record_label(r: dict) -> str:
    what = {"note": ", a note", "page": ", from your page", None: ", held on to"}.get(
        r.get("kind"), ""
    )
    return f"(day {r['day']}{what})"


def recall_lines(records: list[dict] | None, numbered: bool = False) -> str:
    out = [
        f"{f'{i}. ' if numbered else '- '}{record_label(r)} {r['text']}"
        for i, r in enumerate(records or [], 1)
    ]
    return "\n".join(out) or "(nothing yet)"


def consolidate_recalled(
    vil, picks: list[str], day: int, cfg: dict, shown: list[dict]
) -> tuple[list[dict], list[dict]]:
    """One night's picks under retrieval. A number names something in tonight's list (`shown`): a pick is
    rehearsed, as before (its stability doubles); something lived through and not yet picked becomes a
    pick. Anything else is a new pick in their own words. Returns (new, rehearsed)."""
    mem = vil.memories
    new, rehearsed = [], []
    for p in picks[: int(cfg["picks"])]:
        ref = re.fullmatch(r"#?\s*(\d+)\.?(?:\s.*)?", p)
        if ref and 1 <= int(ref.group(1)) <= len(shown):
            r = shown[int(ref.group(1)) - 1]
            if not is_pick(r):
                if not r.get("promoted"):
                    new.append(vil.memory.promote(r, day, cfg))
                continue
            if any(r is x for x in rehearsed) or any(r is x for x in new):
                continue
            r["stability"] *= 2
            r["last"] = day
            r["times"] += 1
            rehearsed.append(r)
            continue
        m = {
            "id": 1 + max((x["id"] for x in mem), default=0),
            "day": day,
            "text": p[: int(cfg["max_chars"])],
            "stability": float(cfg["stability"]),
            "last": day,
            "times": 1,
        }
        mem.append(m)
        new.append(m)
        # Written out word for word from one of today's lines: that line lives on as this pick, so the
        # same words don't come to mind twice.
        for r in vil.experience:
            if (
                r["day"] == day
                and not r.get("promoted")
                and _AT.sub("", r["text"])[: len(m["text"])] == m["text"]
            ):
                r["promoted"] = m["id"]
                m["from"] = r["id"]
                break
    return new, rehearsed
