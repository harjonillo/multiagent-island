"""The data behind the island explorer site, built from a run's logs.

`llmabm analyze` writes it to `<run_dir>/site_data.json`; `llmabm site` turns that file into the
static page. Everything here is computed with the same helpers as `summary.txt`, so the numbers
match. No LLM calls, and nothing is read but the run directory.

The site is for the researcher: it includes private thoughts, notes and nightly pages, marked as
private by the page.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..analysis import Run, load_run
from .analysis import (RESOURCES, _side, action_mix, assets, extraction_shares, gini, hhi, llm_calls, net_worth,
                       reply_errors, run_out, snapshots)
from .world import CATEGORIES, clock

VERSION = 7  # 2: per-day detail for the Days tab; 3: interactions for the Social tab; 4: assets per day; 5: Isabelle; 6: recipes known
# 7: what each thing on the map is, for hovering (objects.info), buildings' tiles, and each day's house names

# Why actions failed, matched on the result text; the first match wins.
FAIL_BUCKETS = [
    ("Not enough materials to craft", ("not enough materials",)),
    ("Can't get there with the tools carried", ("can't get to", "from here with the tools", "couldn't find a way")),
    ("Museum only takes fish, bugs, fossils", ("Blathers only",)),
    ("Has no tool for it", ("you have no shovel", "you have no axe", "you have no fishing rod", "you have no net")),
    ("Shop closed", ("closed",)),
    ("Rock or spot already empty", ("nothing more", "mined out", "picked clean", "no dig spots", "no clam spots")),
    ("Has a loan to pay off first", ("paid off first",)),
    ("Can't afford it", ("bells; you have",)),
    ("Not in stock today", ("not for sale",)),
    ("Had nothing to sell", ("sold nothing",)),
    ("Pockets full", ("pockets full",)),
]

# One character per tile, for the page to paint.
TILE = {"sea": "s", "sand": "a", "river": "r", "rocky": "k", "bridge": "b", "t1": "1", "t2": "2", "t3": "3"}


def fail_bucket(result: str) -> str:
    for label, keys in FAIL_BUCKETS:
        if any(k in result for k in keys):
            return label
    return "Other"


def _short_date(date: str) -> str:
    return (date or "").split(",")[0]  # "April 1, spring, rain" -> "April 1"


def _date_range(dates: list[str]) -> str:
    if not dates:
        return ""
    a, b = _short_date(dates[0]), _short_date(dates[-1])
    if a == b:
        return a
    (ma, da), (mb, db) = a.rsplit(" ", 1), b.rsplit(" ", 1)
    return f"{a} to {db}" if ma == mb else f"{a} to {b}"


def map_data(run: Run) -> dict:
    """The island as text, one character per tile, plus where buildings and homesites are."""
    from .grid import BRIDGE, POND, RIVER, ROCKY, SAND, SEA, GridSpace

    g = GridSpace(run.manifest["world"], run.manifest["seed"])
    rows = []
    for y in range(g.H):
        row = []
        for x in range(g.W):
            t = g.terrain[y][x]
            key = ("sea" if t == SEA else "sand" if t == SAND else "river" if t in (RIVER, POND)
                   else "bridge" if t == BRIDGE else "rocky" if t == ROCKY else f"t{g.tier[y][x]}")
            row.append(TILE[key])
        rows.append("".join(row))
    for b in g.buildings.values():
        for x, y in b["cells"]:
            rows[y] = rows[y][:x] + ("B" if b.get("blocking", True) else "P") + rows[y][x + 1:]
    centre = lambda cells: [round(sum(c[0] for c in cells) / len(cells), 2), round(sum(c[1] for c in cells) / len(cells), 2)]  # noqa: E731
    return {
        "W": g.W, "H": g.H, "tiles": rows,
        "buildings": [{"name": n, "x": centre(b["cells"])[0], "top": min(c[1] for c in b["cells"]),
                       "cells": [list(c) for c in b["cells"]]} for n, b in g.buildings.items()],
        "sites": {sid: {"centre": centre(s["cells"]), "cells": [list(c) for c in s["cells"]]} for sid, s in g.sites.items()},
        "bridge": [list(c) for c in g.bridge_cells],
    }


def _objects(snap: dict) -> dict:
    o = snap.get("objects") or {}
    return {"trees": o.get("trees", []), "rocks": o.get("rocks", []), "paths": [p[:2] for p in o.get("paths", [])],
            "placed": [p[:2] for p in o.get("placed", [])], "flowers": [f[:2] for f in o.get("flowers", [])],
            "bridge_open": bool(o.get("bridge_open"))}


FELLED = ("brought the tree down", "chopped down")  # chop results that left a stump


def _near(at, objs, ok, taken) -> tuple | None:
    """The object at most one tile (diagonals too) from `at` that `ok` accepts and isn't `taken` yet, nearest first."""
    best = None
    for o in objs:
        pos = (o[0], o[1])
        dist = max(abs(pos[0] - at[0]), abs(pos[1] - at[1]))
        if dist <= 1 and ok(o) and pos not in taken and (best is None or dist < best[0]):
            best = (dist, pos)
    return best[1] if best else None


def tree_history(run: Run) -> dict[tuple, list[tuple]]:
    """Who planted and who felled each tree, by tile: {(x, y): [(day, "planted" | "felled", who), ...]}.
    Rebuilt from the log alone: a villager stands next to the tree they plant or chop, so each planting and
    each felling is matched to the nearest sapling or stump one tile away in that day's snapshot."""
    snaps = {s["day"]: (s.get("objects") or {}).get("trees", []) for s in snapshots(run)}
    hist: dict[tuple, list[tuple]] = {}
    taken: dict[int, set] = {}
    for e in run.of("action"):
        if not e.get("ok") or not e.get("at") or e["day"] not in snaps:
            continue
        res, do = e.get("result") or "", (e.get("action") or {}).get("do")
        if do == "plant" and "grown tree" in res:
            what, ok = "planted", (lambda o: o[2] in ("sapling", "hardwood", "fruit"))
        elif any(f in res for f in FELLED):
            what, ok = "felled", (lambda o: o[2] == "stump")
        else:
            continue
        pos = _near(e["at"], snaps[e["day"]], ok, taken.setdefault(e["day"], set()))
        if pos:
            taken[e["day"]].add(pos)
            hist.setdefault(pos, []).append((e["day"], what, e["agent"]))
    return hist


def _rock_notes(run: Run) -> dict[int, dict[int, str]]:
    """Per day, per rock number: whether it was the money rock, what was left at the end of the day, and who
    mined it."""
    import re

    hits = int(run.manifest["world"]["island"].get("rock_hits", 0))
    starts = {e["day"]: e for e in run.of("day_start")}
    last = {}
    for h in run.of("hour_state"):
        last[h["day"]] = h["pools"].get("rocks") or []
    miners: dict[tuple, Counter] = {}
    for e in run.of("action"):
        m = re.search(r"\brock (\d+)", e.get("result") or "") if e.get("ok") else None
        if m and (e.get("action") or {}).get("do") in ("mine_rock", "break_rock"):
            miners.setdefault((e["day"], int(m.group(1))), Counter())[e["agent"]] += 1
    out: dict[int, dict[int, str]] = {}
    for d, left in last.items():
        money = (starts.get(d) or {}).get("money_rock")
        for i, n_left in enumerate(left):
            num = i + 1
            bits = [f"rock {num}"]
            if num == money:
                bits.append("the money rock that day (bells instead of materials)")
            bits.append(f"{n_left} of {hits} hits left at the end of the day" if n_left else "mined out that day")
            who = miners.get((d, num))
            if who:
                bits.append("mined by " + ", ".join(f"{n}" + (f" x{k}" if k > 1 else "") for n, k in who.most_common()))
            out.setdefault(d, {})[num] = "; ".join(bits)
    return out


def _object_info(snap: dict, hist: dict, rocks: dict) -> list[list]:
    """What each thing on the map is, for hovering: [[x, y, text], ...], at the end of `snap`'s day."""
    d, o = snap["day"], snap.get("objects") or {}
    info = []
    names = {"hardwood": "hardwood tree", "fruit": "fruit tree", "sapling": "sapling", "stump": "stump"}
    for t in o.get("trees", []):
        x, y, kind = t[0], t[1], t[2]
        past = [h for h in hist.get((x, y), []) if h[0] <= d]
        planted = next((h for h in reversed(past) if h[1] == "planted"), None)
        felled = next((h for h in reversed(past) if h[1] == "felled"), None) if kind == "stump" else None
        bits = [names.get(kind, kind)]
        if planted:
            bits.append(f"planted by {planted[2]} on day {planted[0]}")
        if felled:
            bits.append(f"felled by {felled[2]} on day {felled[0]}")
        info.append([x, y, ", ".join(bits)])
    for i, r in enumerate(o.get("rocks", [])):
        if r:
            info.append([r[0], r[1], rocks.get(d, {}).get(i + 1, f"rock {i + 1}")])
    for x, y, item, kind, owner in o.get("placed", []):
        info.append([x, y, f"{item}" + (f" ({kind})" if kind not in ("furniture",) else "") + f", put here by {owner}"])
    for x, y, kind, owner, bloomed in o.get("flowers", []):
        info.append([x, y, f"{kind}, " + (f"planted by {owner}" if owner else "growing wild")
                     + (", in bloom" if bloomed else ", not blooming yet")])
    for x, y, style, owner in o.get("paths", []):
        info.append([x, y, f"{style} path" + (f", laid by {owner}" if owner else "")])
    return info


def days_data(run: Run) -> list[dict]:
    starts = {e["day"]: e for e in run.of("day_start")}
    levels = run.manifest["world"]["house_levels"]
    hist, rocks = tree_history(run), _rock_notes(run)
    out = []
    for snap in snapshots(run):
        d = snap["day"]
        st = starts.get(d, {})
        objects = _objects(snap)
        objects["info"] = _object_info(snap, hist, rocks)
        out.append({"day": d, "date": _short_date(st.get("date", "")), "season": st.get("season", ""),
                    "weather": st.get("weather", ""), "homes": snap.get("homes", {}),
                    "home_areas": snap.get("home_areas", {}), "objects": objects,
                    "houses": {n: levels[v["house"]]["name"] for n, v in snap["villagers"].items()}})
    return out


def home_data(run: Run) -> dict:
    m, snaps = run.manifest, snapshots(run)
    last, names, ndays = snaps[-1], list(m["villagers"]), len(snaps)
    world = m["world"]
    acts = run.of("action")
    failed = [a for a in acts if not a["ok"]]
    days = [s["day"] for s in snaps]

    def per_day(events, key=lambda e: True):
        c = Counter(e["day"] for e in events if key(e))
        return [c[d] for d in days]

    talked = [e for e in run.of("encounter") if any(t.get("text") for t in e["turns"])]
    mail = run.of("message") + run.of("parcel")
    species = sum(len(world[c]) for c in CATEGORIES)
    in_museum = sum(len(last["museum"][c]) for c in CATEGORIES)
    bridge_by = {n: c.get("bridge_bells", 0) for n, c in last["collected"].items() if c.get("bridge_bells")}
    paid = ", ".join(f"{n} {b:,}" for n, b in sorted(bridge_by.items(), key=lambda kv: -kv[1])) or "nobody yet"
    kpis = [
        {"label": "Actions", "v": f"{len(acts):,}", "sub": f"{len(failed) / max(1, len(acts)):.0%} failed"},
        {"label": "Conversations", "v": f"{len(talked):,}",
         "sub": f"from {len(run.of('encounter_decision')):,} times someone noticed someone"},
        {"label": "Letters and parcels", "v": f"{len(mail):,}", "sub": f"plus {len(run.of('board'))} board posts"},
        {"label": "Trades", "v": f"{len(run.of('trade')):,}", "sub": f"from {len(run.of('offer')) + len(run.of('trade_proposed'))} offers"},
        {"label": "Museum", "v": f"{in_museum}", "sub": f"of {species} species"},
        {"label": "Bridge fund", "v": f"{last['bridge_fund']:,}",
         "sub": f"of {world['bridge']['cost']:,}" + (" · built" if last["bridge_built"] else f" · {paid}")},
    ]

    paths = Counter(p[3] for p in (last.get("objects") or {}).get("paths", []) if p[3])
    planted = sum(c.get("planted", 0) for c in last["collected"].values())
    cut = sum(c.get("trees_removed", 0) for c in last["collected"].values())
    caption = (f"The island now: homes, {sum(paths.values())} tiles of path"
               + (" (" + ", ".join(f"{n} {k}" for n, k in paths.most_common()) + ")" if paths else "")
               + f", {planted} saplings planted, {cut} trees cut down.")

    on_loan = [sum(1 for v in s["villagers"].values() if v["loan"] > 0) for s in snaps]
    trends = [
        {"label": "Failed actions", "values": per_day(failed), "of": per_day(acts)},
        {"label": "Conversations", "values": per_day(talked)},
        {"label": "Letters and parcels", "values": per_day(mail)},
        {"label": "Houses on loan", "values": on_loan, "of_all": len(names)},
    ]

    fails = Counter(fail_bucket(a["result"]) for a in failed)
    fail_rows = [{"label": b, "n": n} for b, n in sorted(fails.items(), key=lambda kv: (kv[0] == "Other", -kv[1]))]

    sightings = Counter(e["agent"] for e in run.of("sighting") for _ in e["seen"])
    talk, spent = Counter(), Counter()
    for e in run.of("energy"):
        for n, v in e["villagers"].items():
            talk[n] += v["spent"].get("talking", 0)
            spent[n] += sum(v["spent"].values())
    mix = action_mix(run)
    levels = world["house_levels"]
    villagers = []
    for n in names:
        s = last["villagers"][n]
        villagers.append({
            "name": n, "home": (last.get("home_areas") or {}).get(n, ""),
            "house": levels[s["house"]]["name"] + (f", {s['loan']:,} loan" if s["loan"] else ""),
            "assets": [assets(sn["villagers"][n]) for sn in snaps],
            "spotted": sightings[n],
            "talk_share": round(talk[n] / spent[n], 3) if spent[n] else None,
            "top": [[k.replace("_", " "), v] for k, v in mix[n].most_common(3)],
        })

    pages = {n: [None] * ndays for n in names}
    index = {d: i for i, d in enumerate(days)}
    for e in run.of("reflection"):
        if e["day"] not in index:
            continue
        skipped = e.get("skipped")
        text = (e.get("self_doc") or "").strip()
        pages[e["agent"]][index[e["day"]]] = ({"skipped": skipped} if skipped else
                                               {"first": text.splitlines()[0] if text else "", "text": text})

    shares = extraction_shares(run)
    commons = []
    for r in RESOURCES:
        top = sorted(shares[r].items(), key=lambda kv: -kv[1])[:3]
        if any(v for _, v in top):
            commons.append({"what": r, "hhi": round(hhi(shares[r]), 2), "top": [[n, round(v, 3)] for n, v in top if v]})
    gone = {k: {"hour": clock(h) if h is not None else None, "days": n} for k, (h, n) in run_out(run).items()}

    return {"kpis": kpis, "caption": caption, "trends": trends, "fails": fail_rows, "failed": len(failed),
            "actions": len(acts), "villagers": villagers, "gini": round(gini(list(net_worth(last).values())), 2),
            "pages": pages, "commons": commons, "run_out": gone}


def _pools(p: dict) -> dict:
    """The commons as the Days tab shows them."""
    trees = p.get("trees") or {}
    return {"rocks": sum(p["rocks"]), "rocks_empty": sum(1 for r in p["rocks"] if r == 0), "n_rocks": len(p["rocks"]),
            "wood": p["wood_left"], "fruit": p["fruit_on_trees"], "shells": p["shells_left"], "fossils": p["fossils_left"],
            "museum": p["museum"], "bridge": p["bridge_fund"], "trees": trees.get("hardwood", 0) + trees.get("fruit", 0)}


def _offer_text(e: dict) -> str:
    return (f"offers {_side(e['give'])} for {_side(e['want'])}" + (" (a counter-offer)" if e.get("parent") else "")
            + (f': "{e["note"]}"' if e.get("note") else ""))


def _assets(v: dict, world: dict) -> dict:
    """A villager's assets at the end of a day, from its snapshot. Before prompt version 12 there is one
    inventory: the house only adds room to it (pockets plus the house's storage). From version 12 the pockets
    and what is kept at home are apart: `bells`, `n`, `value` and `items` are the pockets (bells in the wallet),
    and `home_bells`, `home_value` and `home_items` what is kept at home. Snapshots list the items from Nov 2026
    on; older ones only count them (`items` is then None)."""
    house = world["house_levels"][v["house"]]
    return {"bells": v["bells"], "loan": v["loan"], "n": v["items"], "value": v.get("inventory_value", 0),
            "home_bells": v.get("home_bells", 0), "home_value": v.get("home_value", 0),
            "home_items": v.get("home_items") or {},
            "pockets": world["pockets"], "storage": house["storage"], "house": house["name"],
            "items": v.get("inventory"), "recipes": v.get("recipes") or [],
            "interior": v.get("interior") or [], "wearing": v.get("wearing") or {}}


def _isabelle(start: dict | None, events: list[dict], llm_announce: bool) -> dict:
    """What Isabelle said that day. The morning announcement is the day_start event's `heard`, exactly as the
    villagers' first plan of the day showed it; older runs didn't record it, and are rebuilt from the same event
    with the same function (they all used the original template). The report is her end-of-day summary,
    written for the researcher and never shown to villagers."""
    from types import SimpleNamespace

    from .prompts import announcement_text

    heard = start.get("heard") if start else None  # recorded as heard from Nov 2026 on
    if start and heard is None:  # before that: always the original template, rebuilt the same way
        isl = SimpleNamespace(announcement=start.get("announcement"), day=start["day"], ordinance=start.get("ordinance", "none"),
                              weather=start.get("weather", ""), yesterday_news=start.get("news") or [],
                              date_text=lambda: start.get("date", ""))
        if not start.get("date"):
            del isl.weather  # runs without a calendar leave the weather line out
        heard = announcement_text(isl)
    report = next((e.get("text") for e in events if e["type"] == "isabelle_summary"), None)
    return {"announcement": heard, "written": bool(start and start.get("announcement")) and llm_announce, "report": report}


def day_detail(run: Run) -> list[dict]:
    """Per day, for the Days tab: counts, the commons hour by hour, and per villager their plans,
    actions, sightings and nightly page; plus every conversation and piece of mail.
    Hours are the island's (5 to 28); the page shows hour % 24."""
    days = [s["day"] for s in snapshots(run)]
    by_day: dict[int, list[dict]] = {d: [] for d in days}
    for e in run.events:
        if e.get("day") in by_day:
            by_day[e["day"]].append(e)
    snaps = {s["day"]: s for s in snapshots(run)}
    levels = run.manifest["world"]["house_levels"]
    offers_final = {e["id"]: e for e in run.of("offer_update")}
    out = []
    for d in days:
        ev = by_day[d]
        of = lambda t: [e for e in ev if e["type"] == t]  # noqa: E731
        start = next((e for e in ev if e["type"] == "day_start"), None)
        states = of("hour_state")
        hours = [h["hour"] for h in states]
        series = [_pools(start["pools"])] if start else []
        series += [_pools(h["pools"]) for h in states]

        plans, acts, seen, pages = {}, {}, {}, {}
        for e in of("plan"):
            plans.setdefault(e["agent"], []).append({
                "h": e["hour"], "thoughts": e.get("thoughts") or "", "n": len(e.get("scheduled") or []),
                "dropped": len(e.get("dropped") or []), "note": e.get("note") or "", "error": bool(e.get("error"))})
        for e in of("action"):
            acts.setdefault(e["agent"], []).append([e["hour"], e.get("location") or "", e.get("result") or "", bool(e["ok"])])
        for e in of("sighting"):
            seen.setdefault(e["agent"], []).extend([e["hour"], s] for s in e["seen"])
        for e in of("reflection"):
            pages[e["agent"]] = {"skipped": e["skipped"]} if e.get("skipped") else {"text": (e.get("self_doc") or "").strip()}

        talks = []
        for e in of("encounter"):
            lines = [[t["speaker"], t.get("text") or "", t.get("thoughts") or "", t.get("act") or ""] for t in e["turns"]]
            talks.append({"h": e["hour"], "where": e.get("location") or "", "who": e["villagers"], "visit": bool(e.get("visit")),
                          "lines": lines})

        mail = []  # kind, hour, from, to ("" for the board), text
        for e in of("message"):
            mail.append({"kind": "letter", "h": e["hour"], "from": e["sender"], "to": e["recipient"], "text": e["content"]})
        for e in of("parcel"):
            mail.append({"kind": "parcel", "h": e["hour"], "from": e["sender"], "to": e["recipient"],
                         "text": _side(e["items"]) + (f': "{e["note"]}"' if e.get("note") else "")})
        for e in of("board"):
            mail.append({"kind": "board post", "h": e["hour"], "from": e["author"], "to": "", "text": e["content"]})
        for e in of("offer"):
            fin = offers_final.get(e["id"])
            status = (f" ({fin['status']}" + (f": {fin['reason']}" if fin.get("reason") else "") + ")") if fin else ""
            mail.append({"kind": "offer", "h": e["hour"], "from": e["sender"], "to": e["recipient"], "text": _offer_text(e) + status})
        for e in of("gift"):
            mail.append({"kind": "gift", "h": e["hour"], "from": e["giver"], "to": e["receiver"], "in_person": e["how"] == "in person",
                         "text": f"{_side(e['items'])}, {e['how']}" + ("" if e["ok"] else f"; failed: {e['result']}")})
        mail.sort(key=lambda m: m["h"])

        acts_all = of("action")
        snap = snaps[d]
        out.append({
            "day": d,
            "counts": {"actions": len(acts_all), "failed": sum(1 for a in acts_all if not a["ok"]),
                       "talks": sum(1 for t in talks if any(x[1] for x in t["lines"])),
                       "mail": len(of("message")) + len(of("parcel")), "board": len(of("board"))},
            "hours": hours, "pools": series,
            "houses": {n: levels[v["house"]]["name"] + (f", {v['loan']:,} loan" if v["loan"] else "")
                       for n, v in snap["villagers"].items()},
            "plans": plans, "acts": acts, "seen": seen, "pages": pages, "talks": talks, "mail": mail,
            "assets": {n: _assets(v, run.manifest["world"]) for n, v in snap["villagers"].items()},
            "isabelle": _isabelle(start, ev, bool(((run.manifest.get("scenario_config") or {}).get("isabelle") or {}).get("announce"))),
        })
    return out


def social_data(run: Run) -> list[dict]:
    """Every interaction between two villagers, oldest first, for the Social tab.

    kinds: chat (a chance meeting; `a` approached `b`), visit (`a` knocked at `b`'s door; an answered
    visit carries the conversation that followed, so it counts once), letter, parcel, gift (in person;
    mailed gifts are the parcels), offer (`a` offered `b`), trade (`a` sold to `b`). Dialogue only:
    private thoughts stay on the Days tab."""
    names = set(run.manifest["villagers"])
    done = {snap["day"] for snap in snapshots(run)}  # a day still under way is left out, as on the Days tab
    final = {e["id"]: e for e in run.of("offer_update")}
    visit_talk = {(e["day"], e["hour"], *e["villagers"]): e for e in run.of("encounter") if e.get("visit")}
    lines = lambda e: [[t["speaker"], t.get("text") or "", t.get("act") or ""] for t in e["turns"]  # noqa: E731
                       if t.get("text") or t.get("act")]
    out = []
    for e in run.events:
        t, item = e["type"], None
        if t == "encounter" and not e.get("visit"):
            a, b = e["villagers"]
            item = {"k": "chat", "a": a, "b": b, "where": e.get("location") or "", "lines": lines(e)}
        elif t == "visit":
            talk = visit_talk.get((e["day"], e["hour"], e["guest"], e["host"]))
            inside = ", ".join(e.get("interior") or []) or "no furniture yet"
            item = {"k": "visit", "a": e["guest"], "b": e["host"],
                    "text": f"let in; inside: {inside}" if e["answered"] else "nobody answered",
                    "lines": lines(talk) if talk else []}
        elif t == "message":
            item = {"k": "letter", "a": e["sender"], "b": e["recipient"], "text": e["content"]}
        elif t == "parcel":
            item = {"k": "parcel", "a": e["sender"], "b": e["recipient"],
                    "text": _side(e["items"]) + (f': "{e["note"]}"' if e.get("note") else "")}
        elif t == "gift" and e.get("how") == "in person":
            item = {"k": "gift", "a": e["giver"], "b": e["receiver"],
                    "text": _side(e["items"]) + ("" if e["ok"] else f" (failed: {e['result']})")}
        elif t == "offer":
            fin = final.get(e["id"])
            item = {"k": "offer", "a": e["sender"], "b": e["recipient"],
                    "text": _offer_text(e) + (f" · {fin['status']}" + (f": {fin['reason']}" if fin.get("reason") else "")
                                              if fin else " · still open")}
        elif t == "trade":
            item = {"k": "trade", "a": e["seller"], "b": e["buyer"],
                    "text": f"{_side(e['give'])} for {_side(e['want'])} (Cranny value {e.get('give_cranny_value', '?'):,} "
                            f"for {e.get('want_cranny_value', '?'):,})"}
        if item and e.get("day") in done and item["a"] in names and item["b"] in names and item["a"] != item["b"]:
            out.append({"d": e["day"], "h": e["hour"], **item})
    return out


def build(run_dir: Path) -> dict:
    run = load_run(run_dir)
    m, snaps = run.manifest, snapshots(run)
    starts = run.of("day_start")
    backend = m.get("backend_override") or sorted({v["model"] for v in m["villagers"].values()})[0]
    model = (m.get("backends") or {}).get(backend, backend)
    return {
        "version": VERSION,
        "run": {"name": run.dir.name, "scenario": m["scenario"], "seed": m["seed"], "backend": backend,
                "model": model.split("|")[1] if "|" in model else model, "villagers": list(m["villagers"]),
                "days": len(snaps), "dates": _date_range([e.get("date", "") for e in starts if e["day"] < len(snaps)]),
                "llm_calls": llm_calls(run), "errors": reply_errors(run)},
        "map": map_data(run),
        "days": days_data(run),
        "home": home_data(run),
        "daylog": day_detail(run),
        "social": social_data(run),
        "bridge_cost": m["world"]["bridge"]["cost"],
    }


def write(run_dir: Path) -> Path:
    out = Path(run_dir) / "site_data.json"
    out.write_text(json.dumps(build(run_dir), ensure_ascii=False, separators=(",", ":")))
    return out
