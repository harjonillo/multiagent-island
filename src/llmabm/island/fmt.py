"""How lists of things are written out, in prompts, records and reports alike."""

from __future__ import annotations

from collections.abc import Mapping


def article(noun: str) -> str:
    """The noun with its article: "an apple", "a sapling"."""
    return ("an " if noun[:1].lower() in "aeiou" else "a ") + noun


def items_x(d: Mapping[str, int], empty: str = "") -> str:
    """{"wood": 3, "apple": 1} -> "wood x3, apple x1"; counts of 0 are left out, and `empty` stands for none."""
    return ", ".join(f"{k} x{n}" for k, n in d.items() if n > 0) or empty


def items_n(d: Mapping[str, int], empty: str = "nothing") -> str:
    """{"wood": 3, "bells": 200} -> "3 wood, 200 bells"; `empty` stands for none."""
    return ", ".join(f"{n} {k}" for k, n in d.items()) or empty


def side_text(d: Mapping[str, int]) -> str:
    """One side of a hand-over: {"wood": 3, "bells": 200} -> "wood x3, 200 bells", or "nothing"."""
    return (
        ", ".join(f"{n} bells" if k == "bells" else f"{k} x{n}" for k, n in d.items())
        or "nothing"
    )


def months_text(spec: Mapping) -> str:
    """The months a species is around, as a report says it: "3, 4, 5", or "all year"."""
    months = spec.get("months")
    return ", ".join(str(m) for m in months) if months else "all year"


def md_table(head: list[str], rows: list[list]) -> str:
    """A Markdown table with one row for each list in `rows`."""
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)
