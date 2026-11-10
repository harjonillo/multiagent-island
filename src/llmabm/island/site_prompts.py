"""Every prompt a villager saw, for the explorer: read from <run_dir>/prompts/ (written while the run
logs prompts, the default from November 2026) and split into one script per day next to the page.

A day's file is loaded only when someone opens a prompt on the page, so the page itself stays small. It
is a script rather than JSON so that it also loads when the page is opened from disk (file://), where
browsers refuse fetch(). Each file calls window.__islandPrompts(day, {sys, calls}):
  sys    the day's distinct system prompts (the rules, the persona and last night's page)
  calls  {id: [index into sys, user prompt, hidden reasoning, reasoning tokens, reply]}
and the page's data gets an index of who made which call when (`index`).

No LLM calls; the prompt logs are only read.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

# The kinds of call, in the order they come within an hour, and what each tag in a file name means.
KINDS = ["plan", "spot", "notice", "chat", "plaza", "evening", "night", "isabelle"]
_NAME = re.compile(r"^d(\d+)_(?:p(\d)|h(\d+)(?:_([a-z]+))?|(reflect)|(isabelle))_([A-Za-z]+)(?:_(\d+))?$")
_CLOCK = re.compile(r"^Day \d+ \([^)]*\), (\d\d):\d\d", re.M)
_PART = re.compile(r"^=== (SYSTEM|USER|REASONING|REPLY)(?: \((\d+) tokens[^)]*\))? ===$", re.M)
DAY_START = 5  # an hour before this is the night after the day


def _parts(text: str) -> dict:
    found = list(_PART.finditer(text))
    out = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        out[m.group(1)] = text[m.end():end].strip("\n")
        if m.group(2):
            out["tokens"] = int(m.group(2))
    return out


def _call(path: Path) -> dict | None:
    m = _NAME.match(path.stem)
    if not m:
        return None
    day, plan, hour, kind, reflect, isabelle, name, seq = m.groups()
    parts = _parts(path.read_text(errors="replace"))
    if "USER" not in parts:
        return None
    if plan is not None:
        kind = "plan"
        clock = _CLOCK.search(parts["USER"])
        hour = int(clock.group(1)) if clock else None
    elif reflect:
        kind, hour = "night", None
    elif isabelle:
        kind, hour = "isabelle", None
    else:
        kind, hour = kind or "evening", int(hour)
    return {"id": path.stem, "day": int(day), "kind": kind if kind in KINDS else "other", "hour": hour,
            "name": name, "seq": int(seq or 1), **parts}


def _order(c: dict) -> tuple:
    h = c["hour"]
    h = 99 if h is None else h + 24 if h < DAY_START else h
    return (h, KINDS.index(c["kind"]) if c["kind"] in KINDS else len(KINDS), c["seq"])


def build(run_dir: Path, days: int, villagers: list[str]) -> tuple[dict | None, dict[int, dict]]:
    """(index, files): the index for the page's data, {day: {name: [[id, kind, hour], ...]}} in the order
    each villager made them, and each day's file contents. (None, {}) when the run kept no prompts."""
    folder = Path(run_dir) / "prompts"
    if not folder.is_dir():
        return None, {}
    by_day: dict[int, list[dict]] = defaultdict(list)
    for p in sorted(folder.glob("d*.txt")):
        c = _call(p)
        if c and c["day"] < days and (c["name"] in villagers or c["kind"] == "isabelle"):
            by_day[c["day"]].append(c)
    index, files = {}, {}
    for day, calls in sorted(by_day.items()):
        calls.sort(key=_order)
        systems: list[str] = []
        seen: dict[str, int] = {}
        payload = {}
        for c in calls:
            s = c.get("SYSTEM", "")
            if s not in seen:
                seen[s] = len(systems)
                systems.append(s)
            payload[c["id"]] = [seen[s], c["USER"], c.get("REASONING"), c.get("tokens"), c.get("REPLY")]
            index.setdefault(str(day), {}).setdefault(c["name"], []).append([c["id"], c["kind"], c["hour"]])
        files[day] = {"sys": systems, "calls": payload}
    return (index or None), files


def write(run_dir: Path, out_dir: Path, days: int, villagers: list[str]) -> dict | None:
    """Write <out_dir>/prompts/day_NNN.js for each day with prompts and return the index (None if none)."""
    index, files = build(run_dir, days, villagers)
    target = Path(out_dir) / "prompts"
    if files:
        target.mkdir(parents=True, exist_ok=True)
    for day, data in files.items():
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        (target / f"day_{day:03d}.js").write_text(f"window.__islandPrompts({day},{body});\n")
    return index
