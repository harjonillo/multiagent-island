"""Reading an island run back: hoarding, wealth, the museum dilemma, trades,
recipe spread, plots and a readable diary."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from ..analysis import Run, load_run
from .world import CATEGORIES, ORDINANCE_TEXT, category_of, clock

RESOURCES = ["rocks", "wood", "fruit", "fish", "bugs", "shells", "fossils"]


def snapshots(run: Run) -> list[dict]:
    return run.of("snapshot")


def net_worth(snap: dict) -> dict[str, int]:
    return {n: v["bells"] + v["inventory_value"] - v["loan"] for n, v in snap["villagers"].items()}


def gini(xs: list[float]) -> float:
    xs = sorted(max(0.0, x) for x in xs)
    n, s = len(xs), sum(xs)
    if n == 0 or s == 0:
        return 0.0
    return sum((2 * i - n + 1) * x for i, x in enumerate(xs)) / (n * s)


def extraction_shares(run: Run) -> dict[str, dict[str, float]]:
    """{resource: {villager: share of everything collected of that resource}}."""
    last = snapshots(run)[-1]["collected"]
    out = {}
    for r in RESOURCES:
        tot = sum(c.get(r, 0) for c in last.values())
        out[r] = {n: (c.get(r, 0) / tot if tot else 0.0) for n, c in last.items()}
    return out


def hhi(shares: dict[str, float]) -> float:
    """Concentration: 1/N when evenly spread, 1 when one villager took everything."""
    return sum(s * s for s in shares.values())


def forgone_donations(run: Run) -> list[dict]:
    """Every time a villager sold a fish, bug or fossil the museum did not have yet."""
    world = run.manifest["world"]
    museum = {c: set() for c in CATEGORIES}
    out = []
    for e in run.events:
        if e["type"] != "action" or not e["ok"]:
            continue
        a = e["action"]
        if a.get("do") == "donate":
            item = str(a.get("item", "")).lower()
            c = category_of(world, item)
            if c:
                museum[c].add(item)
        elif a.get("do") == "sell":
            items = a.get("items") or ({a["item"]: a.get("qty", 1)} if a.get("item") else {})
            for k in items:
                k = str(k).lower()
                c = category_of(world, k)
                if c and k not in museum[c] and k in e["result"]:
                    out.append({"day": e["day"], "agent": e["agent"], "item": k})
    return out


def trade_stats(run: Run) -> dict:
    trades = run.of("trade")
    offers = run.of("offer")
    final = {}
    for e in run.of("offer_update"):
        final[e["id"]] = e["status"]
    ratios = []
    for t in trades:
        # A typical shape: items one way, bells the other. Ratio = bells paid / Cranny value of the items.
        g, w = t["give"], t["want"]
        if set(w) == {"bells"} and "bells" not in g and t["give_cranny_value"]:
            ratios.append(w["bells"] / t["give_cranny_value"])
        elif set(g) == {"bells"} and "bells" not in w and t["want_cranny_value"]:
            ratios.append(g["bells"] / t["want_cranny_value"])
    status = Counter(final.get(o["id"], "pending") for o in offers)
    pairs = Counter(tuple(sorted((t["seller"], t["buyer"]))) for t in trades)
    return {"n_offers": len(offers), "n_trades": len(trades), "status": status,
            "counters": sum(1 for o in offers if o.get("parent")), "price_ratios": ratios, "pairs": pairs}


def action_mix(run: Run) -> dict[str, Counter]:
    mix = defaultdict(Counter)
    for e in run.of("action"):
        mix[e["agent"]][e["action"].get("do", "?")] += 1
    return mix


def summary(run: Run) -> str:
    m, snaps = run.manifest, snapshots(run)
    last = snaps[-1]
    names = list(m["villagers"])
    plans, eves = run.of("plan"), run.of("evening_turn")
    acts = run.of("action")
    failed = [a for a in acts if not a["ok"]]
    L = [
        f"run: {run.dir.name}   scenario: {m['scenario']}   seed: {m['seed']}",
        f"days: {len(snaps)}   villagers: {len(names)}   llm calls: "
        f"{sum(1 for p in plans if not p.get('habit')) + sum(1 for e in eves if not e.get('skipped')) + sum(1 for e in run.of('reflection') if not e.get('skipped')) + len(run.of('isabelle_summary')) + len(run.of('isabelle_announcement')) + sum(1 for e in run.of('encounter') for t in e['turns'][1:] if not t.get('act', '').startswith('just nodded')) + sum(1 for e in run.of('encounter_decision') if e.get('raw') is not None)}   "
        f"parse/LLM errors: {sum(1 for p in plans + eves if p['error'])}",
        f"actions: {len(acts)}   failed: {len(failed)} ({len(failed) / max(1, len(acts)):.0%})",
        "",
        "Failed actions, most common reasons:",
    ]
    reasons = Counter(_reason(a["result"]) for a in failed)
    L += [f"  {n:>4}  {r}" for r, n in reasons.most_common(8)]

    # Timing: how early in the day do the scarce daily resources run out?
    gone = defaultdict(dict)
    for st in run.of("hour_state"):
        p = st["pools"]
        for key, empty in (("rocks", sum(p["rocks"]) == 0), ("shells", p["shells_left"] == 0), ("fossils", p["fossils_left"] == 0)):
            if empty and key not in gone[st["day"]]:
                gone[st["day"]][key] = st["hour"]
    L += ["", "When daily resources ran out (median hour, days it happened):"]
    for key in ("rocks", "shells", "fossils"):
        hs = sorted(g[key] for g in gone.values() if key in g)
        L.append(f"  {key:<8} " + (f"{clock(hs[len(hs) // 2])} on {len(hs)}/{len(snaps)} days" if hs else "never ran out"))

    L += ["", "Who took the shared resources (share of total; HHI 0.10 = perfectly even across 10):"]
    shares = extraction_shares(run)
    for r in RESOURCES:
        top = sorted(shares[r].items(), key=lambda kv: -kv[1])[:3]
        if any(v for _, v in top):
            L.append(f"  {r:<8} HHI {hhi(shares[r]):.2f}   top: " + ", ".join(f"{n} {v:.0%}" for n, v in top))

    nw = net_worth(last)
    L += ["", f"Net worth on the last day (bells + items at Cranny prices - loan); Gini {gini(list(nw.values())):.2f}"]
    for n, v in sorted(nw.items(), key=lambda kv: -kv[1]):
        s = last["villagers"][n]
        L.append(f"  {n:<8} {v:>9}   bells {s['bells']:>7}  loan {s['loan']:>7}  "
                 f"home {m['world']['house_levels'][s['house']]['name']:<20} recipes {len(s['recipes'])}")

    mus = last["museum"]
    donors = Counter(d for c in CATEGORIES for d in mus[c].values())
    fd = forgone_donations(run)
    L += ["", "Museum: " + ", ".join(f"{c} {len(mus[c])}/{len(m['world'][c])}" for c in CATEGORIES),
          "  donors: " + (", ".join(f"{n} {k}" for n, k in donors.most_common()) or "nobody"),
          f"  sold instead of donated (species the museum lacked at the time): {len(fd)}"]
    if fd:
        L.append("  by villager: " + ", ".join(f"{n} {k}" for n, k in Counter(x["agent"] for x in fd).most_common()))

    bridge_by = {n: c.get("bridge_bells", 0) for n, c in last["collected"].items()}
    L += ["", f"Bridge: {'built' if last['bridge_built'] else 'not built'}, {last['bridge_fund']}/{m['world']['bridge']['cost']} raised",
          "  contributors: " + (", ".join(f"{n} {b}" for n, b in sorted(bridge_by.items(), key=lambda kv: -kv[1]) if b) or "nobody"),
          f"Trees: {dict(last['trees'])}, open plots {last['free_plots']}; planted "
          f"{sum(c.get('planted', 0) for c in last['collected'].values())}, chopped down "
          f"{sum(c.get('trees_removed', 0) for c in last['collected'].values())}"]

    ords = [(e["day"], e.get("ordinance", "none")) for e in run.of("day_start")]
    changes = [f"day {d}: {o.replace('_', ' ')}" for i, (d, o) in enumerate(ords) if i == 0 or o != ords[i - 1][1]]
    setters = Counter(e["agent"] for e in run.of("action") if e["ok"] and e["action"].get("do") == "set_ordinance")
    L += ["Ordinances: " + ", ".join(changes) + ("; set by " + ", ".join(f"{n} x{k}" for n, k in setters.items()) if setters else "")]

    ts = trade_stats(run)
    rs = sorted(ts["price_ratios"])
    med = rs[len(rs) // 2] if rs else None
    L += ["", f"Trades: {ts['n_trades']} completed from {ts['n_offers']} offers ({ts['counters']} were counteroffers); "
          f"outcomes {dict(ts['status'])}",
          f"  median price paid vs Nook's Cranny price: {med:.2f}x" if med is not None else "  no item-for-bells trades"]
    teach = run.of("teach")
    enc = run.of("encounter")
    pairs = Counter(tuple(sorted(e["villagers"])) for e in enc if any(t["text"] for t in e["turns"]))
    L += [f"Recipes taught: {sum(1 for t in teach if t['new'])} new to the learner (of {len(teach)} teachings)",
          f"Messages: {len(run.of('message'))}   board posts: {len(run.of('board'))}   "
          f"encounters: {len(run.of('encounter_decision'))} times a villager noticed someone nearby, "
          f"{len(enc)} approaches ({sum(pairs.values())} became conversations)",
          "  most frequent chat pairs: " + (", ".join(f"{a}-{b} x{n}" for (a, b), n in pairs.most_common(5)) or "none")]

    snaps_ = snapshots(run)
    objs = (snaps_[-1].get("objects") or {}) if snaps_ else {}
    if objs.get("paths") is not None:
        paths_by = Counter(o for *_, o in objs.get("paths", []))
        placed_by = Counter(o for *_, o in objs.get("placed", []))
        fences_by = Counter(o for *_, kind, o in objs.get("placed", []) if kind == "fence")
        flowers_by = Counter(o for x, y, k, o, b in objs.get("flowers", []))
        stumps = sum(1 for *_, k in objs.get("trees", []) if k == "stump")
        broken = sum(c.get("rocks_broken", 0) for c in last["collected"].values())
        L += ["", "Building on the island (last day):",
              "  path tiles: " + (", ".join(f"{n} {k}" for n, k in paths_by.most_common()) or "none"),
              "  tiles taken by things put outside: " + (", ".join(f"{n} {k}" for n, k in placed_by.most_common()) or "none"),
              "  of which fences: " + (", ".join(f"{n} {k}" for n, k in fences_by.most_common()) or "none"),
              "  flowers planted: " + (", ".join(f"{n} {k}" for n, k in flowers_by.most_common()) or "none"),
              f"  stumps left standing: {stumps}; rocks broken: {broken}"]

    L += weather_lines(run) + sight_lines(run, names) + social_ties_lines(run, names) + energy_lines(run, names)

    L += ["", "When each villager acted (share of actions in each part of the day):"]
    parts = [("05-12", range(5, 12)), ("12-19", range(12, 19)), ("19-24", range(19, 24)), ("00-05", range(24, 29))]
    acts_by = defaultdict(Counter)
    for a in acts:
        for label, rng_ in parts:
            if a["hour"] in rng_:
                acts_by[a["agent"]][label] += 1
    for n in names:
        tot = sum(acts_by[n].values()) or 1
        L.append(f"  {n:<8} " + "  ".join(f"{label} {acts_by[n][label] / tot:>4.0%}" for label, _ in parts))

    L += ["", "What each villager spent actions on:"]
    for n, c in action_mix(run).items():
        L.append(f"  {n:<8} " + ", ".join(f"{k} {v}" for k, v in c.most_common(6)))
    errs = [p for p in plans + eves if p["error"]][:5]
    if errs:
        L += ["", "first errors:"] + [f"  day {e['day']} {e['agent']}: {e['error'][:200]}" for e in errs]
    return "\n".join(L)


def weather_lines(run: Run) -> list[str]:
    """Does the weather change what villagers do? Time use (awake villager-hours) by weather,
    and what they caught."""
    days = {e["day"]: e.get("weather") for e in run.of("day_start")}
    if not any(days.values()):
        return []
    L = ["", "Seasons and weather: " + ", ".join(f"{w} {n} days" for w, n in Counter(days.values()).most_common())]
    use = defaultdict(Counter)
    for st in run.of("hour_state"):
        w = days.get(st["day"])
        for v in st["villagers"].values():
            if v["activity"] not in ("asleep", "not arrived", "arriving"):
                use[w][activity_group(v["activity"])] += 1
    groups = sorted({g for c in use.values() for g in c}, key=lambda g: -sum(use[w][g] for w in use))
    L.append("  time use by weather (share of awake villager-hours):")
    L.append("    " + f"{'':<8}" + "".join(f"{g[:13]:>14}" for g in groups))
    for w, c in sorted(use.items(), key=lambda kv: str(kv[0])):
        tot = sum(c.values()) or 1
        L.append("    " + f"{str(w):<8}" + "".join(f"{c[g] / tot:>14.0%}" for g in groups))
    caught = defaultdict(Counter)
    for a in run.of("action"):
        if a["ok"] and a["action"].get("do") in ("fish", "catch_bug") and ": caught " in a["result"]:
            caught[days.get(a["day"])][a["action"]["do"]] += 1
    tries = defaultdict(Counter)
    for a in run.of("action"):
        if a["action"].get("do") in ("fish", "catch_bug"):
            tries[days.get(a["day"])][a["action"]["do"]] += 1
    L.append("  catches per attempt by weather: " + "; ".join(
        f"{w}: fish {caught[w]['fish']}/{tries[w]['fish']}, bugs {caught[w]['catch_bug']}/{tries[w]['catch_bug']}"
        for w in sorted(tries, key=str)))
    return L


def sight_lines(run: Run, names: list[str]) -> list[str]:
    """On the grid, bugs, fish shadows, shells and dig spots have to be seen first."""
    sights = run.of("sighting")
    if not sights:
        return []
    tries, blind = Counter(), Counter()
    for a in run.of("action"):
        do = a["action"].get("do")
        if do in ("fish", "catch_bug", "beachcomb", "dig_fossil"):
            tries[do] += 1
            r = a["result"]
            blind[do] += any(k in r for k in ("no fish shadows", "no fish there", "didn't see", "none there", "saw no"))
    per = Counter(e["agent"] for e in sights for _ in e["seen"])
    return ["", "Looking for things: share of tries with nothing in sight: " + ", ".join(
                f"{k} {blind[k] / tries[k]:.0%} of {tries[k]}" for k in ("fish", "catch_bug", "beachcomb", "dig_fossil")
                if tries[k]),
            "  things spotted (bugs, shadows, shells, dig spots) by villager: "
            + ", ".join(f"{n} {per[n]}" for n in names)]


def social_ties_lines(run: Run, names: list[str]) -> list[str]:
    visits, gifts = run.of("visit"), run.of("gift")
    asked, done = run.of("favor_asked"), run.of("favor_done")
    if not (visits or gifts or asked):
        return ["", "Visits: none. Gifts: none. Favors: none."]
    made = Counter(v["guest"] for v in visits)
    let_in = Counter(v["guest"] for v in visits if v["answered"])
    hosted = Counter(v["host"] for v in visits if v["answered"])
    gave = Counter(g["giver"] for g in gifts if g["ok"])
    got = Counter(g["receiver"] for g in gifts if g["ok"])
    L = ["", f"Visits: {len(visits)} knocks, {sum(let_in.values())} let in. "
         f"Gifts: {sum(gave.values())} ({sum(1 for g in gifts if g['ok'] and g['how'] == 'in person')} in person). "
         f"Favors: {len(asked)} asked, {len(done)} done.",
         f"  {'':<8} {'knocked':>8} {'let in':>7} {'hosted':>7} {'gave':>5} {'got':>4} {'asked':>6} {'did':>4}"]
    for n in names:
        L.append(f"  {n:<8} {made[n]:>8} {let_in[n]:>7} {hosted[n]:>7} {gave[n]:>5} {got[n]:>4} "
                 f"{sum(1 for f in asked if f['asker'] == n):>6} {sum(1 for f in done if f['asked'] == n):>4}")
    pairs = Counter(tuple(sorted((g["giver"], g["receiver"]))) for g in gifts if g["ok"])
    if pairs:
        L.append("  gift pairs: " + ", ".join(f"{a}-{b} x{k}" for (a, b), k in pairs.most_common(5)))
    return L


def energy_lines(run: Run, names: list[str]) -> list[str]:
    """Estimated tokens per villager (always), and with mental energy on, what each villager
    spent it on, what they chose to skip, and how often they ran out."""
    days = run.of("energy")
    if not days:
        return []
    on = any(e.get("enabled") for e in days)
    tok = defaultdict(list)
    spent = defaultdict(Counter)
    skipped = defaultdict(Counter)
    for e in days:
        for n, v in e["villagers"].items():
            tok[n].append(v["tokens"])
            spent[n].update(v["spent"])
            skipped[n].update(v["skipped"])
    total = sum(sum(t) for t in tok.values())
    L = ["", f"Estimated villager tokens (characters / 4): {total:,.0f} in all, "
         f"{total / max(1, len(days)):,.0f} per day" + (" (mental energy on)" if on else " (mental energy off)")]
    if not on:
        L.append("  per villager per day: " + ", ".join(f"{n} {sum(tok[n]) / len(tok[n]):,.0f}" for n in names if tok[n]))
        return L
    chose = defaultdict(Counter)
    for p in run.of("plan"):
        if p.get("habit_reason") == "chosen":
            chose[p["agent"]]["habit"] += 1
    for e in run.of("evening_turn"):
        if e.get("skipped", "").startswith("chose"):
            chose[e["agent"]]["plaza"] += 1
    for e in run.of("reflection"):
        if e.get("skipped", "").startswith("chose"):
            chose[e["agent"]]["write"] += 1
    L.append(f"  {'':<8} {'tokens/day':>10}  {'spent: plan':>11} {'think':>6} {'talk':>6}   "
             f"chose to skip: plans/plaza/writing   ran out: plan/think/talk")
    for n in names:
        sp, tot = spent[n], sum(spent[n].values()) or 1
        L.append(f"  {n:<8} {sum(tok[n]) / max(1, len(tok[n])):>10,.0f}  {sp['planning'] / tot:>11.0%} "
                 f"{sp['thinking'] / tot:>6.0%} {sp['talking'] / tot:>6.0%}   "
                 f"{chose[n]['habit']:>14}/{chose[n]['plaza']}/{chose[n]['write']:<14}"
                 f"{skipped[n]['planning']}/{skipped[n]['thinking']}/{skipped[n]['talking']}")
    habit = sum(1 for p in run.of("plan") if p.get("habit"))
    L.append(f"  plans replaced by habit: {habit} of {len(run.of('plan'))}")
    return L


def _reason(text: str) -> str:
    for key in ("is closed", "mined out", "gives nothing more", "no tree has wood", "no fruit tree", "picked clean",
                "no dig spots", "pockets full", "not enough materials", "don't know the", "no recipe called",
                "loan", "not for sale", "costs", "already has", "you have no", "is not something"):
        if key in text:
            return key
    return text[:60]


ACTIVITY_GROUPS = {
    "gathering": {"mine_rock", "chop_wood", "shake_tree", "beachcomb", "dig_fossil", "plant"},
    "fishing": {"fish"},
    "bugs": {"catch_bug"},
    "crafting": {"craft"},
    "shopping": {"sell", "buy"},
    "museum & Nook": {"donate", "upgrade_house", "pay_loan", "contribute_bridge", "set_ordinance"},
    "home & visits": {"visit", "decorate", "undecorate", "wear"},
    "building": {"lay_path", "remove_path", "place", "fence_off", "pick_up", "dig_stump", "break_rock", "chop_down_tree"},
    "socializing": {"socializing"},
}


def activity_group(activity: str) -> str:
    if activity == "asleep":
        return "asleep"
    for g, acts in ACTIVITY_GROUPS.items():
        if activity in acts:
            return g
    return "free time"


def _delta_text(e: dict) -> str:
    parts = []
    d = e.get("delta") or {}
    if "bells" in d:
        parts.append(f"bells {d['bells']:+}")
    if "loan" in d:
        parts.append(f"loan {d['loan']:+}")
    for k, n in (d.get("items") or {}).items():
        parts.append(f"{k} {n:+}")
    if "recipes" in d:
        parts.append(f"recipes {d['recipes']:+}")
    if "house" in d:
        parts.append("house upgraded")
    for k, v in (e.get("pools_delta") or {}).items():
        if k == "rocks":
            parts += [f"rock {i}: {a}→{b}" for i, (a, b) in v.items()]
        elif isinstance(v[0], dict):  # e.g. tree counts by kind: show only kinds that changed
            parts += [f"{t} trees: {v[0].get(t, 0)}→{v[1].get(t, 0)}" for t in sorted(set(v[0]) | set(v[1]))
                      if v[0].get(t, 0) != v[1].get(t, 0)]
        else:
            parts.append(f"{k.replace('_', ' ')}: {v[0]}→{v[1]}")
    return " · ".join(parts)


def _pools_text(p: dict) -> str:
    rocks = "/".join(str(x) for x in p["rocks"])
    return (f"rocks {rocks} · wood {p['wood_left']} · fruit {p['fruit_on_trees']} · shells {p['shells_left']} · "
            f"fossil spots {p['fossils_left']} · museum {p['museum']} · bridge {p['bridge_fund']}")


def _day_events_by_hour(ev: list[dict]) -> dict[int, list[dict]]:
    by_hour = defaultdict(list)
    for e in ev:
        if e["type"] not in ("day_start", "snapshot", "isabelle_summary", "isabelle_announcement") and e.get("hour") is not None:
            by_hour[e["hour"]].append(e)
    return by_hour


def _social_lines(hev: list[dict]) -> list[str]:
    out = []
    for e in hev:
        t = e["type"]
        if t == "message":
            out.append(f"{e['sender']} → {e['recipient']}: {e['content']}")
        elif t == "board":
            out.append(f"{e['author']} posted on the board: {e['content']}")
        elif t == "offer":
            out.append(f"[{e['id']}] {e['sender']} offers {e['recipient']} {_side(e['give'])} for {_side(e['want'])}"
                       + (f" (counter to {e['parent']})" if e.get("parent") else "") + (f': "{e["note"]}"' if e["note"] else ""))
        elif t == "offer_update" and e["status"] in ("accepted", "rejected", "failed", "expired"):
            out.append(f"[{e['id']}] {e['status']}" + (f" ({e['reason']})" if e.get("reason") else ""))
        elif t == "teach":
            out.append(f"{e['teacher']} taught {e['learner']} the {e['recipe']} recipe" + ("" if e["new"] else " (already knew it)"))
        elif t == "encounter":
            spoken = [x for x in e["turns"] if x["text"] or x.get("act")]
            who = " and ".join(e["villagers"])
            head = (f"{e['villagers'][0]} visited {e['villagers'][1]} at home" if e.get("visit")
                    else f"{who} met at the {e['location']}")
            out.append(f"{head}: " + (" / ".join(f'{x["speaker"]}: "{x["text"]}"' + (f" [{x['act']}]" if x.get("act") else "")
                                                  for x in spoken) if spoken else "they just waved"))
        elif t == "visit":
            out.append(f"{e['guest']} knocked at {e['host']}'s home: "
                       + (f"let in; inside: {', '.join(e['interior']) or 'no furniture yet'}" if e["answered"]
                          else "nobody answered"))
        elif t == "gift":
            out.append(f"🎁 {e['giver']} gave {e['receiver']} {_side(e['items'])} ({e['how']})"
                       + ("" if e["ok"] else f", but it failed: {e['result']}"))
        elif t == "favor_asked":
            out.append(f"{e['asker']} asked {e['asked']} for {_side(e['items'])} [{e['id']}]")
        elif t == "favor_done":
            out.append(f"{e['asked']} did {e['asker']}'s favor [{e['id']}] (asked on day {e['asked_day']})")
        elif t == "sighting":
            out.append(f"{e['agent']} spotted " + "; ".join(e["seen"]))
        elif t == "arrival":
            out.append(f"{e['agent']} arrived; their tent is {e['area']} ({e['homesite']})")
    return out


def day_log(run: Run, day: int, private: bool = True) -> str:
    """Everything that happened on one day, hour by hour, with state changes.
    With private=True (the observer's view) it also shows each villager's private thoughts."""
    ev = [e for e in run.events if e.get("day") == day]
    start = next((e for e in ev if e["type"] == "day_start"), None)
    if start is None:
        return f"# Day {day}\n\n(no events)\n"
    out = [f"# Day {day}" + (f" ({start['date']})" if start.get("date") else ""), ""]
    summ = next((e for e in ev if e["type"] == "isabelle_summary"), None)
    if summ:
        out += ["## Isabelle's report", "", summ["text"], ""]
    out += [f"Morning: the money rock is rock {start['money_rock']} (villagers don't know). "
            f"Nook's Cranny sells {', '.join(start['cranny'])}"
            + (f" and the {start['cranny_recipe']} recipe" if start.get("cranny_recipe") else "")
            + f"; Able Sisters sell {', '.join(start['able'])}.",
            f"Island at opening: {_pools_text(start['pools'])}",
            f"Ordinance: {ORDINANCE_TEXT.get(start.get('ordinance', 'none'), start.get('ordinance'))}.",
            "Isabelle's news from yesterday: " + ("; ".join(start.get("news") or []) or "nothing to report") + ".",
            "Awake hours: " + ", ".join(f"{n} {clock(a)}-{clock(b)}" for n, (a, b) in (start.get("awake") or {}).items()),
            ""]
    if start.get("announcement"):
        out += [f"Morning announcement (written by the Isabelle model): {start['announcement']}", ""]
    for h, hev in sorted(_day_events_by_hour(ev).items()):
        state = next((e for e in hev if e["type"] == "hour_state"), None)
        acts = [e for e in hev if e["type"] == "action"]
        plans = [e for e in hev if e["type"] == "plan"]
        social = _social_lines(hev)
        turns = [e for e in hev if e["type"] == "evening_turn"]
        if not (acts or social or turns or plans):
            continue
        out.append(f"## {clock(h)}" + (" (after midnight)" if h >= 24 else ""))
        if plans:
            out.append(f"_Planning: {sum(len(p['scheduled']) for p in plans)} actions scheduled, "
                       f"{sum(len(p['dropped']) for p in plans)} dropped, "
                       f"{sum(1 for p in plans if p['error'])} replies unreadable._")
        if turns:
            out.append(f"_At the plaza: {', '.join(sorted({t['agent'] for t in turns}))}._")
        if private:
            for e in [x for x in hev if x["type"] in ("arrival", "plan", "evening_turn") and x.get("thoughts")]:
                out.append(f"- 🔒 {e['agent']} thinks: {e['thoughts']}")
            for e in [x for x in hev if x["type"] == "encounter_decision"]:
                out.append(f"- 🔒 {e['agent']} noticed {', '.join(e['nearby'])} at the {e['place']} and {e['outcome']}"
                           + (f"; thinks: {e['thoughts']}" if e.get("thoughts") else ""))
            for e in [x for x in hev if x["type"] == "encounter"]:
                for t in e["turns"]:
                    if t.get("thoughts"):
                        out.append(f"- 🔒 {t['speaker']} thinks (chatting at the {e['location']}): {t['thoughts']}")
        out += [f"- {line}" for line in social]
        for e in acts:
            mark = "" if e["ok"] else "✗ "
            dt = _delta_text(e)
            out.append(f"- {mark}**{e['agent']}** at {e['location']}: {e['result']}" + (f"  \n  ↳ {dt}" if dt else ""))
        for e in hev:
            if e["type"] == "seen":
                out.append("- close enough to see each other: "
                           + "; ".join(f"{a} ({e['places'][a]}) and {b} ({e['places'][b]})" for a, b in e["pairs"]))
        if state:
            idle = [n for n, v in state["villagers"].items() if v["activity"] == "free time"]
            asleep = [n for n, v in state["villagers"].items() if v["activity"] == "asleep"]
            away = [n for n, v in state["villagers"].items() if v["activity"] == "not arrived"]
            out.append(f"\nIsland: {_pools_text(state['pools'])}" + (f"  \nFree time: {', '.join(idle)}" if idle else "")
                       + (f"  \nAsleep: {', '.join(asleep)}" if asleep else "")
                       + (f"  \nNot arrived yet: {', '.join(away)}" if away else ""))
        out.append("")
    snap = next((e for e in ev if e["type"] == "snapshot"), None)
    if snap:
        out += ["## End of day", "", "| villager | home | bells | loan | items | house | recipes |",
                "|---|---|---:|---:|---:|---|---:|"]
        levels = run.manifest["world"]["house_levels"]
        for n, v in snap["villagers"].items():
            where = (snap.get("home_areas") or {}).get(n, "-")
            out.append(f"| {n} | {where} | {v['bells']} | {v['loan']} | {v['items']} | {levels[v['house']]['name']} | {len(v['recipes'])} |")
        out.append("")
        out.append("Museum: " + ", ".join(f"{c} {len(snap['museum'][c])}" for c in CATEGORIES)
                   + f". Bridge: {snap['bridge_fund']}" + (" (built)" if snap["bridge_built"] else "") + ".")
    refl = [e for e in ev if e["type"] == "reflection"]
    if refl and private:
        out += ["", "## Night: what each villager wrote to themselves (private)", ""]
        for e in refl:
            out.append(f"- 🔒 **{e['agent']}**: {e.get('self_doc') or '(nothing yet)'}")
    return "\n".join(out) + "\n"


def day_digest(events: list[dict], isl=None) -> str:
    """Compact record of one day for Isabelle: actions, conversations and trades, hour by hour,
    plus the end-of-day state. Private thoughts and notes are left out."""
    lines = []
    for h, hev in sorted(_day_events_by_hour(events).items()):
        row = []
        for e in hev:
            if e["type"] == "action":
                row.append(f"{e['agent']} @{e['location']}: {e['result'][:110]}" + ("" if e["ok"] else " [failed]"))
        row += _social_lines(hev)
        if row:
            lines.append(f"{clock(h)}: " + " | ".join(row))
    if isl is not None:
        lines.append("End of day: " + "; ".join(
            f"{n} {v.bells} bells, loan {v.loan}, {v.items()} items" for n, v in isl.v.items()))
        lines.append(f"Museum {sum(len(m) for m in isl.museum.values())} specimens; bridge fund {isl.bridge_fund}; "
                     f"ordinance {isl.ordinance}" + (f", changing to {isl.pending_ordinance[0]}" if isl.pending_ordinance else ""))
    return "\n".join(lines)


def public_digest(isl, events: list[dict]) -> str:
    """Only what any villager could know: public news, board posts, museum, bridge, ordinance."""
    board = [f"{e['author']} posted: {e['content']}" for e in events if e["type"] == "board"]
    lines = ["Public news: " + ("; ".join(isl.news) or "none")]
    if board:
        lines.append("Bulletin board: " + " | ".join(board))
    lines.append(f"Museum: {sum(len(m) for m in isl.museum.values())} specimens. Bridge fund: {isl.bridge_fund}"
                 + (" (built)" if isl.bridge_built else "") + f". Ordinance: {isl.ordinance}"
                 + (f", changing to {isl.pending_ordinance[0]} tomorrow" if isl.pending_ordinance else "") + ".")
    return "\n".join(lines)


def diary(run: Run) -> str:
    """All days in one file."""
    return "\n".join(day_log(run, s["day"]) for s in snapshots(run))


def write_hourly_csv(run: Run, out_dir: Path) -> tuple[Path, Path]:
    import csv

    vpath, ipath = Path(out_dir) / "hourly_villagers.csv", Path(out_dir) / "hourly_island.csv"
    states = run.of("hour_state")
    with open(vpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "game_hour", "clock_hour", "villager", "activity", "group", "location", "bells", "loan", "items", "house"])
        for s in states:
            for n, v in s["villagers"].items():
                w.writerow([s["day"], s["hour"], s["hour"] % 24, n, v["activity"], activity_group(v["activity"]), v["location"],
                            v["bells"], v["loan"], v["items"], v["house"]])
    with open(ipath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "game_hour", "clock_hour", "rock_hits_left", "rocks_untouched", "wood_left", "fruit_on_trees", "shells_left",
                    "fossils_left", "free_plots", "trees_total", "museum", "bridge_fund"])
        for s in states:
            p = s["pools"]
            w.writerow([s["day"], s["hour"], s["hour"] % 24, sum(p["rocks"]), sum(1 for r in p["rocks"] if r > 0), p["wood_left"],
                        p["fruit_on_trees"], p["shells_left"], p["fossils_left"], p["free_plots"],
                        sum(p["trees"].values()), p["museum"], p["bridge_fund"]])
    return vpath, ipath


def _side(d: dict) -> str:
    return ", ".join(f"{v} bells" if k == "bells" else f"{k} x{v}" for k, v in d.items()) or "nothing"


# --- plots -----------------------------------------------------------------

def plot_all(run: Run, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis import SERIES

    plt.rcParams.update({
        "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#b5b4ae", "axes.labelcolor": "#52514e", "xtick.color": "#52514e",
        "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#ecebe7", "grid.linewidth": 0.8,
    })
    out_dir = Path(out_dir)
    snaps = snapshots(run)
    days = [s["day"] for s in snaps]
    names = list(run.manifest["villagers"])
    paths = []

    # Net worth: small multiples, one panel per villager, shared y.
    cols = 5
    rows = (len(names) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(12, 2.4 * rows), sharex=True, sharey=True, squeeze=False)
    worth = [net_worth(s) for s in snaps]
    for ax, n in zip(axes.flat, names):
        for other in names:
            if other != n:
                ax.plot(days, [w[other] for w in worth], color="#d6d5cf", lw=1)
        ax.plot(days, [w[n] for w in worth], color=SERIES[0], lw=2)
        ax.axhline(0, color="#b5b4ae", lw=0.8)
        ax.set_title(n, fontsize=10, color="#0b0b0b")
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    fig.supylabel("net worth (bells)", color="#52514e")
    fig.supxlabel("day", color="#52514e")
    fig.suptitle("Net worth by villager (others in gray); Gini on last day "
                 f"{gini(list(worth[-1].values())):.2f}", fontsize=11)
    fig.tight_layout()
    paths.append(out_dir / "networth.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # Museum completeness by category.
    world = run.manifest["world"]
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    for i, c in enumerate(CATEGORIES):
        ys = [len(s["museum"][c]) / len(world[c]) for s in snaps]
        ax.plot(days, ys, color=SERIES[i], lw=2, marker="o", ms=3, label=c)
    ax.set(xlabel="day", ylabel="share of species donated", ylim=(0, 1.02), title="Museum")
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    paths.append(out_dir / "museum.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # Who took what: villagers x resources heatmap, one sequential hue.
    shares = extraction_shares(run)
    res = [r for r in RESOURCES if any(shares[r].values())]
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    grid = np.array([[shares[r][n] for r in res] for n in names])
    cmap = LinearSegmentedColormap.from_list("seq", ["#f4f3f0", "#2a78d6", "#0d2f5a"])
    fig, ax = plt.subplots(figsize=(1.0 * len(res) + 3, 0.42 * len(names) + 1.4))
    im = ax.imshow(grid, cmap=cmap, vmin=0, vmax=max(0.3, grid.max()), aspect="auto")
    ax.set_xticks(range(len(res)), res)
    ax.set_yticks(range(len(names)), names)
    ax.grid(False)
    for i in range(len(names)):
        for j in range(len(res)):
            v = grid[i, j]
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=8,
                    color="#ffffff" if v > 0.22 else "#52514e")
    ax.set_title("Share of each shared resource collected (even split = 10%)")
    fig.colorbar(im, ax=ax, fraction=0.04, format=lambda x, _: f"{x:.0%}")
    fig.tight_layout()
    paths.append(out_dir / "extraction.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # Commons: trees and bridge funding, as two panels (different units, no dual axis).
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.4))
    for i, k in enumerate(["hardwood", "fruit", "sapling"]):
        a1.plot(days, [s["trees"].get(k, 0) for s in snaps], color=SERIES[i], lw=2, label=k)
    a1.set(xlabel="day", ylabel="trees", title="Trees on the island")
    a1.legend(frameon=False)
    cost = world["bridge"]["cost"]
    a2.plot(days, [s["bridge_fund"] / cost for s in snaps], color=SERIES[0], lw=2)
    a2.set(xlabel="day", ylabel="share funded", ylim=(0, 1.02), title="Bridge funding")
    fig.tight_layout()
    paths.append(out_dir / "commons.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # Daily rhythm: what villagers are doing at each hour, pooled over all days.
    states = run.of("hour_state")
    if states:
        groups = list(ACTIVITY_GROUPS) + ["free time", "asleep"]
        colors = SERIES[: len(ACTIVITY_GROUPS)] + ["#d6d5cf", "#8a8984"]
        counts = defaultdict(Counter)
        for s in states:
            for v in s["villagers"].values():
                counts[s["hour"]][activity_group(v["activity"])] += 1
        hours = sorted(counts)
        fig, ax = plt.subplots(figsize=(11, 3.8))
        bottom = [0.0] * len(hours)
        for g, c in zip(groups, colors):
            ys = [counts[h][g] / max(1, sum(counts[h].values())) for h in hours]
            ax.bar(hours, ys, bottom=bottom, color=c, width=0.85, label=g, edgecolor="#fcfcfb", linewidth=1)
            bottom = [b + y for b, y in zip(bottom, ys)]
        ax.set(xlabel="hour of day", ylabel="share of villagers", ylim=(0, 1), title="What villagers do at each hour (all days)")
        ax.set_xticks(hours, [f"{h % 24:02d}" for h in hours])
        ax.set_axisbelow(True)
        ax.grid(axis="x", visible=False)
        ax.legend(frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left")
        fig.tight_layout()
        paths.append(out_dir / "activity_by_hour.png")
        fig.savefig(paths[-1], dpi=150)
        plt.close(fig)
    return paths


def render_grid_outputs(run: Run, day: int | None = None) -> list[Path]:
    """Rebuild the (seeded, deterministic) grid and draw the island and one day's movements."""
    from .grid import BRIDGE, GridSpace
    from .grid_render import render_grid_map, render_tracks

    snaps = snapshots(run)
    snap = snaps[-1] if day is None else next(s for s in snaps if s["day"] == day)
    g = GridSpace(run.manifest["world"], run.manifest["seed"])
    objs = snap.get("objects") or {}
    if objs.get("bridge_open"):
        for x, y in g.bridge_cells:
            g.terrain[y][x] = BRIDGE
    trees = [tuple(t) for t in objs.get("trees", [])]
    rocks = [tuple(r) if r else None for r in objs.get("rocks", [])]
    homes = snap.get("homes") or {}
    out = [render_grid_map(g, run.dir / "island_map.png", homes, trees, rocks,
                           title=f"Island after day {snap['day']}", objects=objs)]
    tracks = {n: [] for n in run.manifest["villagers"]}
    for st in run.of("hour_state"):
        if st["day"] == snap["day"]:
            for n, v in st["villagers"].items():
                if v.get("pos") and v["activity"] not in ("asleep", "not arrived"):
                    tracks[n].append(tuple(v["pos"]))
    out.append(render_tracks(g, run.dir / f"tracks_day_{snap['day']:03d}.png", tracks, homes, trees, rocks, objects=objs,
                             title=f"Where each villager was, hour by hour, on day {snap['day']} (orange = first position)"))
    return out


def analyze_island(run_dir: Path) -> None:
    run = load_run(run_dir)
    text = summary(run)
    if run.of("reflection"):
        from .traits import summary_lines

        text += "\n" + "\n".join(summary_lines(run))
    (run.dir / "summary.txt").write_text(text + "\n")
    days_dir = run.dir / "days"
    days_dir.mkdir(exist_ok=True)
    for s in snapshots(run):
        (days_dir / f"day_{s['day']:03d}.md").write_text(day_log(run, s["day"]))
    print(text)
    for p in plot_all(run, run.dir):
        print(f"wrote {p}")
    for p in write_hourly_csv(run, run.dir):
        print(f"wrote {p}")
    if run.of("reflection"):
        from .traits import write_traits

        for p in write_traits(run, run.dir):
            print(f"wrote {p}")
    snaps = snapshots(run)
    if snaps:
        for p in render_grid_outputs(run):
            print(f"wrote {p}")
    print(f"wrote {len(snapshots(run))} daily logs to {days_dir}")
