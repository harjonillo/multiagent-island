"""The explorer's Experiments page ("pick a door"): one pixel-art door per run in a runs folder.

Each door links to that run's explorer page (<run>/site/index.html). The card under a door tells the
experiments apart (agents, scenario, start date) and opens to show the initial conditions: what every
villager starts with and how much of each shared resource the island has on day 0.

Only manifest.json and the first few lines of events.jsonl are read, so this is quick on large runs.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from .site_doors import EMPTY_DOOR, KNOB, OPEN_WIDTHS, PANEL, SIGN, door_svg, frame_svg, knob_svg

TEMPLATE = Path(__file__).with_name("site_experiments.html")
TOKENS = Path(__file__).with_name("site_tokens.css")


def is_run(path: Path) -> bool:
    return (Path(path) / "manifest.json").exists()


def _first_events(run_dir: Path) -> tuple[dict, dict]:
    """The run_start event and day 0's day_start event (empty dicts if missing)."""
    start, day0 = {}, {}
    p = run_dir / "events.jsonl"
    if p.exists():
        with p.open() as f:
            for i, line in enumerate(f):
                e = json.loads(line)
                if e.get("type") == "run_start" and not start:
                    start = e
                elif e.get("type") == "day_start" and e.get("day") == 0:
                    day0 = e
                if (start and day0) or i > 200:
                    break
    return start, day0


def is_mock(run_dir: Path) -> bool:
    """A run whose villagers all used the mock backend (scripted replies, for testing the code)."""
    m = json.loads((Path(run_dir) / "manifest.json").read_text())
    names = [m["backend_override"]] if m.get("backend_override") else {v["model"] for v in m["villagers"].values()}
    return all(n == "mock" or (m.get("backends") or {}).get(n) == "mock" for n in names)


def experiment_order(runs_dir: Path) -> list[Path]:
    """Run directories in a runs folder, oldest first (by the run_start time, else the folder's).
    Mock runs are left out: they test the code, they aren't experiments."""
    runs = [p for p in Path(runs_dir).iterdir() if p.is_dir() and is_run(p) and not is_mock(p)]

    def started(p: Path) -> float:
        return _first_events(p)[0].get("time") or p.stat().st_mtime

    return sorted(runs, key=lambda p: (started(p), p.name))


def agents_label(m: dict) -> str:
    """Backend and model the villagers use, e.g. `groq · openai/gpt-oss-20b`, or `mock backend`."""
    names = [m["backend_override"]] if m.get("backend_override") else sorted({v["model"] for v in m["villagers"].values()})
    parts = []
    for name in names:
        spec = (m.get("backends") or {}).get(name, name)
        if spec == "mock" or name == "mock":
            parts.append("mock backend")
            continue
        url, _, rest = spec.partition("|")
        model = rest.split("|")[0] if rest else url
        if name == "default" and rest:  # name the provider rather than "default"
            host = urlparse(url).hostname or ""
            name = host.split(".")[-2] if host.count(".") >= 1 else host or name
        parts.append(f"{name} · {model}")
    return ", ".join(parts)


def _list(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def conditions(m: dict, day0: dict) -> dict:
    """What each villager starts with, and the island's shared resources on day 0."""
    w, sc = m["world"], m.get("scenario_config") or {}
    tools = (w.get("tools") or {})
    uses = tools.get("uses")
    starters = list(w.get("starter_recipes") or [])
    n_random = int(w.get("recipes_known_at_start") or 0)
    recipes = " + ".join(x for x in [_list(starters) if starters else "", f"{n_random} at random" if n_random else ""] if x)
    villager = [
        ["bells", f"{int(sc.get('start_bells', 1000)):,}"],
        ["tools", _list(tools.get("start_with") or []) + (f" ({uses} uses each)" if uses and tools.get("start_with") else "")],
        ["recipes", recipes or "none"],
    ]

    isl, pools = w["island"], day0.get("pools") or {}
    trees = pools.get("trees") or {"hardwood": isl["hardwood_trees"], "fruit": isl["fruit_trees"]}
    rocks = pools.get("rocks")
    n_rocks = len(rocks) if rocks is not None else isl["rocks"]
    hits = max(rocks) if rocks else isl["rock_hits"]
    fruit = isl.get("native_fruit", "fruit")
    island = [
        ["trees", f"{trees.get('hardwood', 0)} hardwood, {trees.get('fruit', 0)} {fruit}"],
        ["wood", f"{pools.get('wood_left', trees.get('hardwood', 0) * isl['wood_per_tree'])} a day ({isl['wood_per_tree']} a tree)"],
        ["fruit", f"{pools.get('fruit_on_trees', trees.get('fruit', 0) * isl['fruit_per_tree'])} {fruit}s, back "
                  f"{isl['fruit_regrow_days']} days after picking"],
        ["rocks", f"{n_rocks}, {hits} hits each a day"],
        ["shells", f"{pools.get('shells_left', isl['shells_per_day'])} a day on the beach"],
        ["fossils", f"{pools.get('fossils_left', isl['fossils_per_day'])} a day to dig up"],
    ]
    if m.get("space") == "grid":
        island.append(["flowers", f"{(w.get('grid') or {}).get('wild_flower_patches', 6)} wild patches"])
    return {"villager": villager, "island": island}


def experiment(run_dir: Path, number: int) -> dict:
    run_dir = Path(run_dir)
    m = json.loads((run_dir / "manifest.json").read_text())
    start, day0 = _first_events(run_dir)
    lt = time.localtime(start.get("time") or run_dir.stat().st_mtime)
    total = (m.get("scenario_config") or {}).get("days") or m.get("days")
    return {
        "number": number, "dir": run_dir.name, "scenario": m["scenario"], "seed": m["seed"],
        "agents": agents_label(m), "days_done": m.get("days_completed", 0), "days_total": total,
        "started": f"{time.strftime('%b', lt)} {lt.tm_mday}, {lt.tm_year}",
        "villagers": len(m["villagers"]),
        "conditions": conditions(m, day0),
    }


# --- the page ------------------------------------------------------------------------------
def _pos(d: dict) -> str:
    return "; ".join(f"{k}: {v}" for k, v in d.items())


def _rows(rows: list[list[str]]) -> str:
    return "".join(f'<div class="row"><div class="k">{html.escape(k)}</div><div>{html.escape(v)}</div></div>' for k, v in rows)


def _sign(number: int, line: str) -> str:
    return (f'<div class="sign" style="{_pos(SIGN)}"><span class="exp">EXP {number:02d}</span>'
            f'<span class="scn">{html.escape(line)}</span></div>')


def door_html(e: dict) -> str:
    n = e["number"]
    label = f"Open experiment {n:02d}: {e['scenario']}, {e['agents']}"
    c = e["conditions"]
    return f"""
    <div class="slot">
      <a class="door" href="{html.escape(e['href'])}" aria-label="{html.escape(label)}">
        {frame_svg()}
        <div class="panel-box" style="{_pos(PANEL)}">
          <div class="panel">
            {door_svg(20)}
            {_sign(n, e['scenario'])}
            <div class="knob" style="{_pos(KNOB)}"><span class="up">{knob_svg(False)}</span><span class="down">{knob_svg(True)}</span></div>
          </div>
        </div>
      </a>
      <div class="card">
        <div class="card-head">
          <div class="title">Experiment {n:02d}</div>
          <div class="chip">day {e['days_done']} of {e['days_total']}</div>
        </div>
        {_rows([["agents", e['agents']], ["scenario", f"{e['scenario']} · seed {e['seed']}"], ["started", e['started']]])}
        <details class="start">
          <summary><span class="tri" aria-hidden="true"></span>initial conditions</summary>
          <div class="group">Each of the {e['villagers']} villagers</div>
          {_rows(c['villager'])}
          <div class="group">The island on day 0</div>
          {_rows(c['island'])}
          <div class="note">Wood, rocks, shells and fossils refill each morning.</div>
        </details>
        {'' if e['has_site'] else f'<div class="note warn">No page yet: run <code>llmabm site runs/{html.escape(e["dir"])}</code></div>'}
      </div>
    </div>"""


def empty_html(number: int, runs_dir: Path) -> str:
    cmd = f"llmabm run scenarios/island_blank.yaml --backend groq --seed 0 --run-dir {runs_dir.name}/exp{number:02d}"
    return f"""
    <div class="slot">
      <div class="door empty" aria-hidden="true">
        {frame_svg()}
        <div class="panel-box" style="{_pos(PANEL)}">
          <div class="panel">{door_svg(20, EMPTY_DOOR)}{_sign(number, 'empty lot')}</div>
        </div>
      </div>
      <div class="card next">
        <div class="title">Next experiment</div>
        A new door appears here when another run directory exists, for example a real model on the same scenario:
        <div class="cmd">{html.escape(cmd)}</div>
      </div>
    </div>"""


def build_experiments(runs_dir: Path, out_dir: Path | None = None) -> Path:
    """Write the Experiments page over every run in the folder.

    By default it goes to <runs_dir>/site/index.html, linking to each run's <run>/site/index.html.
    With out_dir (a folder to publish, as for GitHub Pages) it is <out_dir>/index.html, linking to
    <out_dir>/<run>/index.html; build_all writes the run pages there."""
    runs_dir = Path(runs_dir)
    exps = [experiment(p, i) for i, p in enumerate(experiment_order(runs_dir))]
    for e in exps:
        if out_dir:
            e["href"], page = f"{e['dir']}/index.html", Path(out_dir) / e["dir"] / "index.html"
        else:
            e["href"], page = f"../{e['dir']}/site/index.html", runs_dir / e["dir"] / "site" / "index.html"
        e["has_site"] = page.exists()
    out_dir = Path(out_dir) if out_dir else runs_dir / "site"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = [door_svg(w) for w in OPEN_WIDTHS[1:]]
    doors = "".join(door_html(e) for e in exps) + empty_html(len(exps), runs_dir)
    page = (TEMPLATE.read_text().replace("__TOKENS__", TOKENS.read_text())
            .replace("__COUNT__", f"{len(exps)} experiment" + ("" if len(exps) == 1 else "s"))
            .replace("__DOORS__", doors)
            .replace("__FRAMES__", json.dumps(frames).replace("</", "<\\/")))
    out = out_dir / "index.html"
    out.write_text(page)
    return out
