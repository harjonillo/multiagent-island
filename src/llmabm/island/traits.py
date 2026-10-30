"""Emergent personality: behavioural traits per villager per day, self-reports, and the
environment each villager was dealt.

The idea is to measure personality as stable individual differences in behaviour, and to
check it against what villagers say about themselves and against where they ended up.

Outputs (written by `write_traits`):
  traits_daily.csv    one row per villager per day: behavioural metrics and self-ratings
  villagers_env.csv   what each villager was dealt: arrival hour, homesite, reachability
  selves/<name>.md    each villager's self-description, day by day
  traits_divergence.png   how far apart villagers are on each metric, day by day
  traits_profile.png      each villager's recent profile (z-scored behaviour and self-ratings)
"""

from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path

from ..analysis import Run
from .analysis import activity_group, snapshots

METRICS = {
    # name: (description, higher means)
    "actions": "actions taken",
    "first_hour": "hour of first action (game hours; 25 = 01:00)",
    "gathering": "share of actions spent gathering (rocks, wood, fruit, shells, fossils)",
    "fishing": "share of actions spent fishing",
    "bugs": "share of actions spent catching bugs",
    "crafting": "share of actions spent crafting",
    "building": "share of actions spent building (paths, placing things, fences, clearing trees and rocks)",
    "rock_share": "share of the island's rock hits that day",
    "places": "different places visited",
    "walked": "tiles walked",
    "approach_rate": "share of times they noticed someone that they went to talk to",
    "chats": "conversations",
    "messages": "letters sent at the plaza",
    "gifts": "gifts given (in person, or by letter asking nothing back)",
    "visits": "homes they knocked at",
    "hosted": "visitors they let in",
    "favors_asked": "things they asked others for",
    "favors_done": "requests from others they fulfilled",
    "interior": "furniture inside their home (end of day)",
    "talk_energy": "share of mental energy spent talking (mental energy runs only)",
    "habit_plans": "plans skipped for habit because planning energy ran out",
    "donations": "museum donations",
    "bridge_bells": "bells put toward the bridge",
    "trades": "completed trades",
}


def _profile_metrics(run: Run) -> dict[tuple[int, str], dict]:
    names = list(run.manifest["villagers"])
    days = [s["day"] for s in snapshots(run)]
    m: dict[tuple[int, str], dict] = {(d, n): defaultdict(float) for d in days for n in names}
    groups = defaultdict(Counter)
    first = {}
    places = defaultdict(set)
    rocks_total = Counter()
    for e in run.of("action"):
        k = (e["day"], e["agent"])
        if k not in m:
            continue
        m[k]["actions"] += 1
        groups[k][activity_group(e["action"].get("do", ""))] += 1
        first[k] = min(first.get(k, 99), e["hour"])
        if e["location"] not in ("home",):
            places[k].add(e["location"])
        hits = sum(v for v in (e.get("delta", {}).get("items") or {}).values() if v > 0) if e["action"].get("do") == "mine_rock" else 0
        if e["action"].get("do") == "mine_rock" and e["ok"]:
            hits = hits or 8  # the money rock pays bells, not items
            m[k]["_rock_hits"] += hits
            rocks_total[e["day"]] += hits
        if e["ok"] and e["action"].get("do") == "donate":
            m[k]["donations"] += 1
        if e["ok"] and e["action"].get("do") == "contribute_bridge":
            m[k]["bridge_bells"] += -(e.get("delta", {}).get("bells") or 0)
    for k, c in groups.items():
        tot = sum(c.values()) or 1
        for g in ("gathering", "fishing", "bugs", "crafting", "building"):
            m[k][g] = c[g] / tot
    for k, h in first.items():
        m[k]["first_hour"] = h
    for k, p in places.items():
        m[k]["places"] = len(p)
    for k in m:
        tot = rocks_total[k[0]]
        m[k]["rock_share"] = m[k].pop("_rock_hits", 0.0) / tot if tot else 0.0
    dec = Counter()
    appr = Counter()
    for e in run.of("encounter_decision"):
        k = (e["day"], e["agent"])
        dec[k] += 1
        appr[k] += e["outcome"].startswith("approached")
    for k in m:
        m[k]["approach_rate"] = appr[k] / dec[k] if dec[k] else math.nan
    for e in run.of("encounter"):
        for n in e["villagers"]:
            if (e["day"], n) in m and any(t["text"] for t in e["turns"]):
                m[(e["day"], n)]["chats"] += 1
    for e in run.of("message"):
        if (e["day"], e["sender"]) in m:
            m[(e["day"], e["sender"])]["messages"] += 1
    offers = {e["id"]: e for e in run.of("offer")}
    for e in run.of("offer_update"):
        o = offers.get(e["id"])
        if o and e["status"] == "accepted" and not o["want"] and (o["day"], o["sender"]) in m:
            m[(o["day"], o["sender"])]["gifts"] += 1
    for e in run.of("gift"):
        if e["ok"] and (e["day"], e["giver"]) in m:
            m[(e["day"], e["giver"])]["gifts"] += 1
    for e in run.of("visit"):
        if (e["day"], e["guest"]) in m:
            m[(e["day"], e["guest"])]["visits"] += 1
        if e["answered"] and (e["day"], e["host"]) in m:
            m[(e["day"], e["host"])]["hosted"] += 1
    for e in run.of("favor_asked"):
        if (e["day"], e["asker"]) in m:
            m[(e["day"], e["asker"])]["favors_asked"] += 1
    for e in run.of("favor_done"):
        if (e["day"], e["asked"]) in m:
            m[(e["day"], e["asked"])]["favors_done"] += 1
    for e in run.of("energy"):
        if not e.get("enabled"):
            continue
        for n, v in e["villagers"].items():
            if (e["day"], n) in m:
                tot = sum(v["spent"].values())
                m[(e["day"], n)]["talk_energy"] = v["spent"].get("talking", 0) / tot if tot else math.nan
                m[(e["day"], n)]["habit_plans"] = v["skipped"].get("planning", 0)
    for snap in snapshots(run):
        for n, v in snap["villagers"].items():
            if (snap["day"], n) in m:
                m[(snap["day"], n)]["interior"] = len(v.get("interior") or [])
    for e in run.of("trade"):
        for n in (e["seller"], e["buyer"]):
            if (e["day"], n) in m:
                m[(e["day"], n)]["trades"] += 1
    last_walk = {}
    for st in run.of("hour_state"):
        for n, v in st["villagers"].items():
            if "walked_today" in v:
                last_walk[(st["day"], n)] = v["walked_today"]
    for k, w in last_walk.items():
        if k in m:
            m[k]["walked"] = w
    return m


def _val(m, d, n, k):
    v = m[(d, n)].get(k)
    if v is None:
        return math.nan if k in ("approach_rate", "talk_energy") else 0.0
    return v


def _ratings(run: Run) -> tuple[list[str], dict[tuple[int, str], list]]:
    items: list[str] = []
    out = {}
    for e in run.of("reflection"):
        items = e.get("items") or items
        out[(e["day"], e["agent"])] = e.get("ratings") or []
    return items, out


def _env(run: Run) -> dict[str, dict]:
    """What each villager was dealt, before any choices beyond picking a homesite."""
    names = list(run.manifest["villagers"])
    arr = {e["agent"]: e for e in run.of("arrival")}
    env = {n: {"arrival_hour": arr.get(n, {}).get("hour"), "homesite": arr.get(n, {}).get("homesite"),
               "home_area": arr.get(n, {}).get("area")} for n in names}
    if run.manifest.get("space") == "grid":
        from .grid import GridSpace

        g = GridSpace(run.manifest["world"], run.manifest["seed"])
        snaps = snapshots(run)
        rocks = [tuple(r) for r in (snaps[0].get("objects") or {}).get("rocks", []) if r] if snaps else []
        rs = g.coords_of_place("Resident Services")
        for n in names:
            site = env[n]["homesite"]
            door = g.coords_of_home(site) if site else None
            if door is None:
                continue
            bare = g.distances(door, frozenset())
            # With a pole, everything on the lower ground is reachable: a fair measure of how central home is.
            env[n]["steps_to_resident_services"] = g.distances(door, frozenset({"vaulting pole"})).get(rs)
            env[n]["buildings_reachable_without_tools"] = sum(
                1 for b in g.buildings.values() if b["door"] in bare)
            env[n]["rocks_reachable_without_tools"] = sum(
                1 for r in rocks if any(a in bare for a in g.approach_cells(r)))
            env[n]["home_x"], env[n]["home_y"] = door
    return env


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float:
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None and not (isinstance(x, float) and math.isnan(x))
             and not (isinstance(y, float) and math.isnan(y))]
    if len(pairs) < 4:
        return math.nan
    ra, rb = _rank([p[0] for p in pairs]), _rank([p[1] for p in pairs])
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else math.nan


def _nanmean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else math.nan


def _std(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if len(xs) < 2:
        return math.nan
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def recent_profile(run: Run, window: int = 5) -> tuple[dict[str, dict], list[str]]:
    """Each villager's mean behaviour and self-ratings over the last `window` days."""
    m = _profile_metrics(run)
    items, rat = _ratings(run)
    days = sorted({d for d, _ in m})
    recent = days[-window:]
    names = list(run.manifest["villagers"])
    prof = {}
    for n in names:
        row = {k: _nanmean([_val(m, d, n, k) for d in recent]) for k in METRICS}
        for i, it in enumerate(items):
            row[f"self: {it}"] = _nanmean([(rat.get((d, n)) or [None] * len(items))[i]
                                           if i < len(rat.get((d, n)) or []) else None for d in recent])
        prof[n] = row
    return prof, items


def summary_lines(run: Run) -> list[str]:
    if not run.of("reflection") and run.manifest.get("scenario_config", {}).get("reflection", {}).get("enabled") is not True:
        return []
    prof, items = recent_profile(run)
    names = list(prof)
    env = _env(run)
    L = ["", "Emergent traits (last 5 days). Spread across villagers, first day -> last day:"]
    m = _profile_metrics(run)
    days = sorted({d for d, _ in m})
    for k in METRICS:
        first = _std([_val(m, days[0], n, k) for n in names])
        last = _std([_val(m, days[-1], n, k) for n in names])
        L.append(f"  {k:<14} {first:7.2f} -> {last:7.2f}")
    if items:
        L.append("Do they act the way they describe themselves? Spearman across villagers:")
        pairs = [(0, "gifts"), (0, "donations"), (1, "chats"), (3, "chats"), (3, "approach_rate"), (5, "bridge_bells")]
        for i, metric in pairs:
            if i < len(items):
                r = spearman([prof[n][f"self: {items[i]}"] for n in names], [prof[n][metric] for n in names])
                L.append(f"  '{items[i]}' vs {metric}: {r:+.2f}")
    feats = [f for f in ("arrival_hour", "steps_to_resident_services", "buildings_reachable_without_tools",
                         "rocks_reachable_without_tools") if any(env[n].get(f) is not None for n in names)]
    if feats:
        L.append("Does where they landed predict how they behave? Spearman across villagers:")
        for f in feats:
            cells = []
            for k in ("rock_share", "fishing", "chats", "donations", "walked"):
                r = spearman([env[n].get(f) for n in names], [prof[n][k] for n in names])
                cells.append(f"{k} {r:+.2f}")
            L.append(f"  {f}: " + ", ".join(cells))
    L.append("  (10 villagers per run: treat single-run correlations as hints; pool runs across seeds.)")
    return L


def write_traits(run: Run, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    m = _profile_metrics(run)
    items, rat = _ratings(run)
    names = list(run.manifest["villagers"])
    days = sorted({d for d, _ in m})
    paths = []

    p = out_dir / "traits_daily.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "villager", *METRICS, *[f"self_{i + 1}" for i in range(len(items))]])
        for d in days:
            for n in names:
                r = rat.get((d, n)) or []
                w.writerow([d, n, *[_val(m, d, n, k) for k in METRICS],
                            *[(r[i] if i < len(r) else "") for i in range(len(items))]])
    paths.append(p)

    env = _env(run)
    p = out_dir / "villagers_env.csv"
    cols = sorted({k for v in env.values() for k in v})
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["villager", *cols])
        for n in names:
            w.writerow([n, *[env[n].get(c, "") for c in cols]])
    paths.append(p)

    sdir = out_dir / "selves"
    sdir.mkdir(exist_ok=True)
    by = defaultdict(list)
    for e in run.of("reflection"):
        by[e["agent"]].append(e)
    for n, evs in by.items():
        lines = [f"# {n}: self-descriptions, day by day", ""]
        for e in evs:
            lines += [f"## Day {e['day']}", "", e.get("self_doc") or "(none)", ""]
            if e.get("ratings") and items:
                lines.append("Self-ratings: " + "; ".join(f"{it} {r}" for it, r in zip(items, e["ratings"])))
                lines.append("")
        (sdir / f"{n}.md").write_text("\n".join(lines))
    if by:
        paths.append(sdir)

    paths += _plots(run, out_dir, m, items, rat, names, days)
    return paths


def _plots(run, out_dir, m, items, rat, names, days) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    from ..analysis import SERIES

    out = []
    keys = list(METRICS)
    cols = 4
    rows = (len(keys) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(3.4 * cols, 2.4 * rows), squeeze=False, sharex=True)
    for ax, k in zip(axes.flat, keys):
        for n in names:
            ys = [_val(m, d, n, k) for d in days]
            ax.plot(days, ys, color="#c9c8c2", lw=0.9)
        ax.plot(days, [_std([_val(m, d, n, k) for n in names]) for d in days], color=SERIES[0], lw=2)
        ax.set_title(k, fontsize=9)
        ax.grid(False)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes.flat[len(keys):]:
        ax.axis("off")
    fig.suptitle("Behaviour by villager (gray) and spread across villagers (blue), by day", fontsize=11)
    fig.supxlabel("day", fontsize=9)
    fig.tight_layout()
    p = Path(out_dir) / "traits_divergence.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    out.append(p)

    prof, _ = recent_profile(run)
    cols_ = keys + [f"self: {it}" for it in items]
    grid = np.array([[prof[n].get(c, math.nan) for c in cols_] for n in names], dtype=float)
    mu, sd = np.nanmean(grid, axis=0), np.nanstd(grid, axis=0)
    sd[sd == 0] = 1
    z = (grid - mu) / sd
    cmap = LinearSegmentedColormap.from_list("div", ["#2a78d6", "#f4f3f0", "#eb6834"])
    fig, ax = plt.subplots(figsize=(0.55 * len(cols_) + 2.5, 0.42 * len(names) + 3.0))
    im = ax.imshow(np.nan_to_num(z), cmap=cmap, vmin=-2, vmax=2, aspect="auto")
    labels = keys + [f"self {i + 1}" for i in range(len(items))]
    ax.set_xticks(range(len(cols_)), labels, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(len(names)), names, fontsize=9)
    if items:
        ax.axvline(len(keys) - 0.5, color="#52514e", lw=1)
    ax.set_title("Recent profile (last 5 days), z-scored across villagers\nleft: behaviour, right: self-ratings",
                 fontsize=10)
    ax.grid(False)
    if items:
        fig.text(0.01, 0.005, "  ".join(f"self {i + 1}: {it}" for i, it in enumerate(items)), fontsize=7,
                 color="#52514e", wrap=True)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z")
    fig.tight_layout()
    p = Path(out_dir) / "traits_profile.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    out.append(p)
    return out
