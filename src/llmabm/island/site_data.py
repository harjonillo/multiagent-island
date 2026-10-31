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
from .analysis import (RESOURCES, action_mix, extraction_shares, gini, hhi, llm_calls, net_worth, reply_errors,
                       run_out, snapshots)
from .world import CATEGORIES, clock

VERSION = 1

# Why actions failed, matched on the result text; the first match wins.
FAIL_BUCKETS = [
    ("Not enough materials to craft", ("not enough materials",)),
    ("Can't get there with the tools carried", ("can't get to", "from here with the tools", "couldn't find a way")),
    ("Museum only takes fish, bugs, fossils", ("Blathers only",)),
    ("Has no tool for it", ("you have no shovel", "you have no axe", "you have no fishing rod", "you have no net")),
    ("Shop closed", ("closed",)),
    ("Rock or spot already empty", ("nothing more", "mined out", "picked clean", "no dig spots")),
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
        "buildings": [{"name": n, "x": centre(b["cells"])[0], "top": min(c[1] for c in b["cells"])}
                      for n, b in g.buildings.items()],
        "sites": {sid: {"centre": centre(s["cells"]), "cells": [list(c) for c in s["cells"]]} for sid, s in g.sites.items()},
        "bridge": [list(c) for c in g.bridge_cells],
    }


def _objects(snap: dict) -> dict:
    o = snap.get("objects") or {}
    return {"trees": o.get("trees", []), "rocks": o.get("rocks", []), "paths": [p[:2] for p in o.get("paths", [])],
            "placed": [p[:2] for p in o.get("placed", [])], "flowers": [f[:2] for f in o.get("flowers", [])],
            "bridge_open": bool(o.get("bridge_open"))}


def days_data(run: Run) -> list[dict]:
    starts = {e["day"]: e for e in run.of("day_start")}
    out = []
    for snap in snapshots(run):
        d = snap["day"]
        st = starts.get(d, {})
        out.append({"day": d, "date": _short_date(st.get("date", "")), "season": st.get("season", ""),
                    "weather": st.get("weather", ""), "homes": snap.get("homes", {}),
                    "home_areas": snap.get("home_areas", {}), "objects": _objects(snap)})
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
        {"label": "Trades", "v": f"{len(run.of('trade')):,}", "sub": f"from {len(run.of('offer'))} offers"},
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
            "assets": [sn["villagers"][n]["bells"] + sn["villagers"][n]["inventory_value"] for sn in snaps],
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
    }


def write(run_dir: Path) -> Path:
    out = Path(run_dir) / "site_data.json"
    out.write_text(json.dumps(build(run_dir), ensure_ascii=False, separators=(",", ":")))
    return out
