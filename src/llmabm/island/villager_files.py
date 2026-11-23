"""Each villager's own folder in a run directory: <run_dir>/villagers/<name>/.

  profile.md   who they are: the persona their prompts start with, their hours, model and home
  memory.md    what they carry from earlier days into their prompts, as of the last finished day:
               the nightly page, what they chose to hold on to (and when each fades), their notes,
               yesterday, the last conversation and what changed hands with each villager; then
               night by night, what they held on to, came back to and forgot
  assets.md    what they own now, and at the end of each day
  pages.md     what they wrote to themselves, night by night (written by traits.write_traits)
  prompts/     every call they made, one file each (written by the simulation as it runs)

Nothing here is read back by the simulation: memory.md describes the state the prompts are built
from, it is not that state. profile.md, memory.md and assets.md are written by `write`, from the
run's events and, for what a villager carries now, its checkpoint. No LLM calls.
"""

from __future__ import annotations

import math
import pickle
from collections import Counter
from pathlib import Path

from .memory import retrieval, strength
from .world import clock


def villager_dir(run_dir: Path, name: str) -> Path:
    return Path(run_dir) / "villagers" / name


def prompt_dir(run_dir: Path, name: str | None) -> Path:
    """Where prompts are logged: a villager's in their own folder, anyone else's (Isabelle's) in prompts/."""
    return villager_dir(run_dir, name) / "prompts" if name else Path(run_dir) / "prompts"


def _count(d: dict) -> str:
    return ", ".join(f"{k} x{n}" for k, n in sorted(d.items()) if n) or "nothing"


def _load_checkpoint(run_dir: Path):
    """The simulation as it stood at the end of the last finished day, or None. Only the run's own
    checkpoint is loaded (pickle files can run code when loaded)."""
    p = Path(run_dir) / "checkpoint.pkl"
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception:  # noqa: BLE001  a checkpoint from code that has since changed shape
        return None


# --- profile -----------------------------------------------------------------

def profile_text(run, name: str) -> str:
    m = run.manifest
    spec = m["villagers"][name]
    snaps = run.of("snapshot")
    traits = spec.get("traits") or {}
    lines = [f"# {name}", ""]
    facts = [f"- Model: {m.get('backend_override') or spec.get('model', 'default')}"]
    if "wakes" in traits:
        facts.append(f"- Usually up from {clock(traits['wakes'])} to {clock(traits.get('sleeps', 24))}")
    if (m.get("arrivals") or {}).get(name):
        facts.append(f"- Arrived at {m['arrivals'][name]} on day 0")
    if snaps:
        last = snaps[-1]
        v = last["villagers"].get(name)
        if v:
            house = m["world"]["house_levels"][v["house"]]["name"]
            area = (last.get("home_areas") or {}).get(name)
            facts.append(f"- Home at the end of day {last['day']}: a {house}" + (f" in {area}" if area else ""))
    if spec.get("source"):
        facts.append(f"- Persona from: {spec['source']}")
    lines += facts + ["", "## Persona", "", "The start of every prompt they get; nobody else sees it.", "",
                      spec.get("persona", "").strip() or "(none)", "",
                      "## In this folder", "",
                      "- `memory.md`: what they carry from earlier days",
                      "- `assets.md`: what they own, now and day by day",
                      "- `pages.md`: what they wrote to themselves, night by night",
                      "- `prompts/`: every call they made, exactly as sent, with the hidden reasoning and the reply",
                      ""]
    return "\n".join(lines)


# --- memory ------------------------------------------------------------------

def _last_shown(m: dict, cfg: dict) -> int:
    """The last day a memory still comes to mind if it isn't come back to."""
    return m["last"] + int(m["stability"] * math.log(1 / cfg["threshold"]) + 1e-9)


def _nights(run, name: str, upto: int | None) -> list[str]:
    """Night by night: the page, what was held on to, come back to and forgotten."""
    gone = {e["day"]: e.get("forgotten") or [] for e in run.of("memory") if e.get("agent") == name}
    out = []
    for e in run.of("reflection"):
        if e.get("agent") != name or (upto is not None and e["day"] > upto):
            continue
        if e.get("skipped"):
            parts = [f"didn't write ({e['skipped']})"]
        else:
            parts = ["wrote the page" if e.get("raw") else "wrote nothing"]
            if e.get("remembered"):
                parts.append("held on to: " + "; ".join(f'"{t}"' for t in e["remembered"]))
            if e.get("rehearsed"):
                parts.append("came back to: " + "; ".join(f'"{t}"' for t in e["rehearsed"]))
        if gone.get(e["day"]):
            parts.append("forgot: " + "; ".join(f'"{t}"' for t in gone[e["day"]]))
        out.append(f"- Day {e['day']}: " + ". ".join(p[:1].upper() + p[1:] for p in parts) + ".")
    return out


def _page(run, name: str, upto: int | None) -> tuple[str, int | None]:
    """(the latest nightly page, the night it was written)."""
    text, day = "", None
    for e in run.of("reflection"):
        if e.get("agent") != name or (upto is not None and e["day"] > upto):
            continue
        if not e.get("skipped") and e.get("self_doc") and e["self_doc"] != text:
            text, day = e["self_doc"], e["day"]
    return text, day


def memory_text(run, name: str, sim=None) -> str:
    """What `name` carries from earlier days. With `sim` (the checkpoint), the state itself; without,
    only what the events tell: the page and the night-by-night record."""
    isl = getattr(sim, "isl", None)
    done = isl.day if isl is not None else None  # the last finished day
    page, page_day = _page(run, name, done)
    out = [f"# {name}'s memory" + (f", waking on day {done + 1}" if done is not None else ""), "",
           "Everything they carry from earlier days into their prompts. Whatever isn't here is gone for them, "
           "unless someone tells them or they see it again.", ""]

    out += ["## The page", "",
            "Private and free-form; it replaces the one before and is in every prompt they get."
            + (f" Last written on the night of day {page_day}." if page_day is not None else ""), "",
            page.strip() or "(nothing yet)", ""]

    if isl is None or name not in isl.v:
        out += ["## What they carry now", "", "(not available: this run has no checkpoint to read it from)", ""]
    else:
        from .prompts import EXCHANGES_SHOWN, LAST_CHAT_LINES, at_place, exchange_line, turn_act_text

        vil, wake = isl.v[name], done + 1
        version = getattr(isl, "prompt_version", 1)
        cfg = getattr(isl, "memory", None)
        out += ["## Things they remember", ""]
        if not cfg:
            out += ["Long-term memory is off in this run: they keep their latest notes and nothing else.", ""]
        elif retrieval(isl):  # prompt version 20: nothing is forgotten; each prompt retrieves what fits it
            kinds = Counter(r["kind"] for r in vil.experience if not r.get("promoted"))
            days = sorted({r["day"] for r in vil.experience})
            out += [f"From prompt version 20 nothing is forgotten. Everything they lived through is kept, and each "
                    f"prompt shows the few records closest to the situation in it (up to {cfg['k']} in plans, the "
                    f"plaza and conversations, {cfg['k_quick']} in quick decisions, {cfg['shown']} at night), weighed "
                    f"by how recent they are. What they chose to hold on to at night counts {cfg['pick_weight']:g} "
                    "times as much, and fades more slowly each time they come back to it. So there is no fixed list "
                    "here: the `recall` events in events.jsonl say what came to mind for each prompt.", "",
                    f"Kept so far: {kinds['event']} things that happened, {kinds['chat']} pieces of conversations, "
                    f"{kinds['note']} notes and {kinds['page']} pieces of nightly pages"
                    + (f", from days {days[0]} to {days[-1]}." if days else "."), ""]
            out += ["### What they chose to hold on to", ""]
            if not vil.memories:
                out += ["(nothing)", ""]
            else:
                out += ["| from day | memory | times held | recency now |", "|---|---|---|---|"]
                for m in sorted(vil.memories, key=lambda m: (m["day"], m["id"])):
                    out.append(f"| {m['day']} | {m['text'].replace('|', chr(92) + '|')} | {m['times']} "
                               f"| {max(cfg['recency_floor'], strength(m, wake)):.2f} |")
                out.append("")
        else:
            alive = sorted((m for m in vil.memories if strength(m, wake) >= cfg["threshold"]),
                           key=lambda m: (-strength(m, wake), -m["day"]))
            shown = {m["id"] for m in alive[: cfg["shown"]]}
            out += [f"Chosen by them at night, up to {cfg['picks']} a night, in their own words. Each fades unless "
                    f"they come back to it at night, which doubles how long it lasts. Plans, the plaza and "
                    f"conversations show the strongest {cfg['shown']}; quick decisions show 4.", ""]
            if not alive:
                out += ["(nothing)", ""]
            else:
                out += ["| from day | memory | times held | strength now | comes to mind through |",
                        "|---|---|---|---|---|"]
                for m in sorted(alive, key=lambda m: (m["day"], m["id"])):
                    text = m["text"].replace("|", "\\|") + ("" if m["id"] in shown else " *(not shown: weaker than the others)*")
                    out.append(f"| {m['day']} | {text} | {m['times']} | {strength(m, wake):.2f} "
                               f"| day {_last_shown(m, cfg)} |")
                out.append("")
        if cfg:
            head = f"## Notes from the last {cfg['notes_days']} days"
        else:
            head = "## Notes"
        out += [head, "", "What they jotted down in plans, conversations and at the plaza.", ""]
        out += [f"- (day {d}) {n}" for d, n in vil.notes] or ["(none)"]
        out.append("")

        yesterday = (getattr(sim, "yesterday", None) or {}).get(name) or []
        out += [f"## Yesterday (day {done})", "", "What happened to them, as their first plan of the day lists it.", ""]
        out += [f"- {r}" for r in yesterday] or ["(nothing)"]
        out.append("")

        others = [o for o in isl.v if o != name]
        talks, hands = [], []
        for o in others:
            chats = [c for c in getattr(isl, "last_chats", {}).get(frozenset((name, o)), []) if c["day"] < wake]
            if chats:
                c = chats[-1]
                where = (f"at {c['b']}'s home" if c.get("visit")
                         else at_place(c["place"]))
                talks += [f"### {o} (day {c['day']}, {clock(c['hour'])}, {where})", ""]
                for t in c["turns"][-LAST_CHAT_LINES:]:
                    act = (f" [{turn_act_text(t, name, o, version >= 13, version >= 18)}]"
                           if t.get("act") or t.get("acts") else "")
                    talks.append(f"- **{t['speaker']}:** {t['text'][:200]}{act}")
                talks.append("")
            ex = [e for e in getattr(isl, "exchanges", []) if {e["from"], e["to"]} == {name, o}]
            if ex:
                more = len(ex) - len(ex[-EXCHANGES_SHOWN:])
                hands += [f"### {o}" + (f" (the latest {EXCHANGES_SHOWN}; {more} earlier)" if more else ""), ""]
                hands += [f"- day {e['day']}: {exchange_line(e, name, version >= 18)}" for e in ex[-EXCHANGES_SHOWN:]]
                hands.append("")
        out += ["## The last conversation with each villager", "",
                "Shown to them when they meet that villager again (the latest lines; \"you\" is " + name + ").", ""]
        out += talks or ["(none yet)", ""]
        out += ["## What has changed hands with each villager", "",
                "Shown to them when they meet that villager (\"you\" is " + name + ").", ""]
        out += hands or ["(nothing yet)", ""]
        if vil.prices_seen:
            out += ["## Prices they have seen", "",
                    ", ".join(f"{k} {p}" for k, p in sorted(vil.prices_seen.items())), ""]

    nights = _nights(run, name, done)
    out += ["## Night by night", ""] + (nights or ["(no nights yet)"]) + [""]
    return "\n".join(out)


# --- assets ------------------------------------------------------------------

def _worth(v: dict) -> int:
    return v["bells"] + v.get("home_bells", 0) + v.get("inventory_value", 0) + v.get("home_value", 0)


def assets_text(run, name: str, sim=None) -> str:
    world = run.manifest["world"]
    snaps = [s for s in run.of("snapshot") if name in s["villagers"]]
    out = [f"# {name}'s assets", ""]
    if not snaps:
        return "\n".join(out + ["(no day has finished yet)", ""])
    last = snaps[-1]
    v = last["villagers"][name]
    house = world["house_levels"][v["house"]]
    area = (last.get("home_areas") or {}).get(name)
    out += [f"## At the end of day {last['day']}", "",
            f"- Bells with them: {v['bells']:,}",
            f"- Bells at home: {v.get('home_bells', 0):,}",
            f"- Loan from Tom Nook: {v['loan']:,}",
            f"- Home: a {house['name']}" + (f" in {area}" if area else "") + f" (storage for {house['storage']} items)",
            f"- Pockets ({v['items']} of {world.get('pockets', '?')} slots, worth {v.get('inventory_value', 0):,} "
            f"at Nook's Cranny): {_count(v.get('inventory') or {})}",
            f"- Kept at home (worth {v.get('home_value', 0):,}): {_count(v.get('home_items') or {})}"]
    vil = getattr(getattr(sim, "isl", None), "v", {}).get(name)
    if vil is not None and sim.isl.day == last["day"]:
        wear = {**{t: u for t, u in vil.wear.items() if u}, **{f"{t} (at home)": u for t, u in vil.home_wear.items() if u}}
        if wear:
            out.append("- Tools, uses so far: " + ", ".join(f"{t} {'/'.join(str(n) for n in (u if isinstance(u, list) else [u]))}"
                                                            for t, u in sorted(wear.items())))
    out += [f"- Wearing: {', '.join(f'{i} ({s})' for s, i in sorted((v.get('wearing') or {}).items())) or 'nothing special'}",
            f"- Furniture inside: {', '.join(v.get('interior') or []) or 'none'}",
            f"- Recipes known: {', '.join(v.get('recipes') or []) or 'none'}",
            f"- Net worth (bells and things at Cranny prices, less the loan): {_worth(v) - v['loan']:,}", "",
            "## Day by day", "",
            "At the end of each day. Worth is at Nook's Cranny prices.", "",
            "| day | bells with them | bells at home | pocket items | pockets worth | home worth | home | loan | net worth |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in snaps:
        x = s["villagers"][name]
        out.append(f"| {s['day']} | {x['bells']:,} | {x.get('home_bells', 0):,} | {x['items']} "
                   f"| {x.get('inventory_value', 0):,} | {x.get('home_value', 0):,} "
                   f"| {world['house_levels'][x['house']]['name']} | {x['loan']:,} | {_worth(x) - x['loan']:,} |")
    out.append("")
    return "\n".join(out)


def write_pages(run, out_dir: Path | None = None) -> list[Path]:
    """Write pages.md for every villager who has had a night: what they wrote to themselves, night by night."""
    by: dict[str, list[dict]] = {}
    for e in run.of("reflection"):
        by.setdefault(e["agent"], []).append(e)
    paths = []
    for n, evs in by.items():
        lines = [f"# {n}: what they wrote to themselves, night by night", ""]
        for e in evs:
            lines += [f"## Day {e['day']}", "", e.get("self_doc") or "(none)", ""]
        d = villager_dir(out_dir or run.dir, n)
        d.mkdir(parents=True, exist_ok=True)
        (d / "pages.md").write_text("\n".join(lines))
        paths.append(d / "pages.md")
    return paths


def write(run) -> list[Path]:
    """Write profile.md, memory.md and assets.md for every villager of `run`. Returns the folders."""
    sim = _load_checkpoint(run.dir)
    dirs = []
    for name in run.manifest["villagers"]:
        d = villager_dir(run.dir, name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "profile.md").write_text(profile_text(run, name))
        (d / "memory.md").write_text(memory_text(run, name, sim))
        (d / "assets.md").write_text(assets_text(run, name, sim))
        dirs.append(d)
    return dirs
