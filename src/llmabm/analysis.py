"""Reading a run back: provenance, exposure, drift, probes, plots, and the flow viewer."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED = "#8a8984"


@dataclass
class Run:
    dir: Path
    manifest: dict
    events: list[dict]

    def of(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]

    @property
    def steps(self) -> int:
        return self.manifest["steps"]

    @property
    def agents(self) -> list[str]:
        return list(self.manifest["agents"])


def load_run(run_dir: Path) -> Run:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()]
    return Run(run_dir, manifest, events)


# --- provenance ------------------------------------------------------------

def message_index(run: Run) -> dict[str, dict]:
    idx = {}
    for e in run.of("seed"):
        idx.setdefault(e["id"], e)
    for e in run.of("message"):
        idx[e["id"]] = e
    return idx


def roots_of(mid: str, idx: dict, memo: dict) -> frozenset:
    """Seed ids this message descends from, following declared `based_on` links."""
    if mid in memo:
        return memo[mid]
    memo[mid] = frozenset()  # guards against cycles
    m = idx.get(mid)
    if m is None:
        r = frozenset()
    elif m.get("kind") == "seed":
        r = frozenset([mid])
    else:
        r = frozenset().union(*(roots_of(b, idx, memo) for b in m.get("based_on", [])))
    memo[mid] = r
    return r


def depth_of(mid: str, idx: dict, memo: dict) -> int | None:
    """Fewest hops back to any seed, or None if the message has no seed ancestry."""
    if mid in memo:
        return memo[mid]
    memo[mid] = None
    m = idx.get(mid)
    if m is None:
        d = None
    elif m.get("kind") == "seed":
        d = 0
    else:
        ds = [depth_of(b, idx, memo) for b in m.get("based_on", [])]
        ds = [x for x in ds if x is not None]
        d = min(ds) + 1 if ds else None
    memo[mid] = d
    return d


def exposure(run: Run) -> dict[str, dict[str, int]]:
    """{seed_id: {agent: first tick they read something descended from that seed}}.
    Counts only messages actually read, so the attention limit matters."""
    idx, memo = message_index(run), {}
    first: dict[str, dict[str, int]] = defaultdict(dict)
    for e in run.of("turn"):
        for mid in e["read"]:
            for root in roots_of(mid, idx, memo):
                first[root].setdefault(e["agent"], e["tick"])
    return {s: first.get(f"seed:{s}", {}) for s in seed_ids(run)}


def seed_ids(run: Run) -> list[str]:
    return [s["id"] for s in run.manifest["seeds"]]


def reach_curves(run: Run) -> dict[str, list[float]]:
    n = len(run.agents)
    out = {}
    for s, first in exposure(run).items():
        counts = Counter(first.values())
        cum, curve = 0, []
        for t in range(run.steps):
            cum += counts.get(t, 0)
            curve.append(cum / n)
        out[s] = curve
    return out


def drift(run: Run) -> dict[str, list[tuple[int, float]]]:
    """(hop depth, text similarity to the original seed) for every descendant message."""
    idx, rmemo, dmemo = message_index(run), {}, {}
    out = defaultdict(list)
    for m in run.of("message"):
        d = depth_of(m["id"], idx, dmemo)
        for root in roots_of(m["id"], idx, rmemo):
            sim = SequenceMatcher(None, idx[root]["content"].lower(), m["content"].lower()).ratio()
            out[root.removeprefix("seed:")].append((d, round(sim, 3)))
    return dict(out)


def probe_table(run: Run) -> dict[str, dict[int, Counter]]:
    table: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for e in run.of("probe"):
        table[e["probe"]][e["tick"]][e["answer"] or "(unparsed)"] += 1
    return table


def summary(run: Run) -> str:
    turns, msgs = run.of("turn"), run.of("message")
    errs = [t for t in turns if t["error"]]
    lines = [
        f"run: {run.dir.name}   scenario: {run.manifest['scenario']}   seed: {run.manifest['seed']}",
        f"agents: {len(run.agents)}   edges: {len(run.manifest['edges'])}   ticks: {run.steps}",
        f"turns: {len(turns)}   messages: {len(msgs)}   errors: {len(errs)}   "
        f"cached: {sum(t['cached'] for t in turns)}",
        f"dropped by attention: {sum(len(e['ids']) for e in run.of('dropped'))}   "
        f"invalid recipients: {len(run.of('invalid_recipient'))}   "
        f"unverifiable based_on ids: {sum(len(e['ids']) for e in run.of('unverified_based_on'))}",
        "",
    ]
    exp = exposure(run)
    for s, first in exp.items():
        reached = len(first)
        t_half = next((t for t, v in enumerate(reach_curves(run)[s]) if v >= 0.5), None)
        lines.append(f"seed '{s}': reached {reached}/{len(run.agents)} agents; half reached at tick {t_half}")
    dr = drift(run)
    for s, pts in dr.items():
        by_d = defaultdict(list)
        for d, sim in pts:
            by_d[d].append(sim)
        row = "  ".join(f"hop{d}:{sum(v)/len(v):.2f}" for d, v in sorted(by_d.items()) if d is not None)
        lines.append(f"drift '{s}' (mean similarity to seed by hop): {row}")
    for pid, ticks in probe_table(run).items():
        lines.append(f"probe '{pid}':")
        for t, c in sorted(ticks.items()):
            lines.append(f"  t={t:>3}  " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    if errs:
        lines.append("")
        lines.append("first errors:")
        for e in errs[:5]:
            lines.append(f"  t={e['tick']} {e['agent']}: {e['error'][:200]}")
    return "\n".join(lines)


def cascade_text(run: Run, seed: str, max_lines: int = 300) -> str:
    """Indented tree of who passed what to whom, starting from one seed."""
    idx = message_index(run)
    children = defaultdict(list)
    for m in run.of("message"):
        for b in m["based_on"]:
            children[b].append(m)
    root = f"seed:{seed}"
    if root not in idx:
        return f"no seed '{seed}' in this run"
    s = idx[root]
    targets = [e["recipient"] for e in run.of("seed") if e["id"] == root]
    lines = [f"[{root}] t={s['tick']} -> {', '.join(targets)}: {s['content'][:100]}"]
    seen = set()

    def walk(mid, depth):
        for c in sorted(children[mid], key=lambda m: (m["tick"], m["id"])):
            if len(lines) >= max_lines:
                return
            if c["id"] in seen:
                lines.append("  " * depth + f"(see {c['id']})")
                continue
            seen.add(c["id"])
            lines.append("  " * depth + f"[{c['id']}] t={c['tick']} {c['sender']} -> {c['recipient']}: {c['content'][:100]}")
            walk(c["id"], depth + 1)

    walk(root, 1)
    return "\n".join(lines)


# --- plots -----------------------------------------------------------------

def plot_all(run: Run, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#b5b4ae", "axes.labelcolor": "#52514e", "xtick.color": "#52514e",
        "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#ecebe7", "grid.linewidth": 0.8,
    })
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # Reach over time
    curves = reach_curves(run)
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    for i, (s, c) in enumerate(curves.items()):
        ax.plot(range(len(c)), c, color=SERIES[i % 8], lw=2, marker="o", ms=4, label=s)
        if c:
            ax.annotate(s, (len(c) - 1, c[-1]), xytext=(6, 0), textcoords="offset points", va="center", color="#0b0b0b")
    ax.set(xlabel="tick", ylabel="fraction of agents exposed", ylim=(0, 1.02), title="Reach by seed")
    if len(curves) > 1:
        ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    paths.append(out_dir / "reach.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # Drift by hop
    dr = drift(run)
    if dr:
        fig, ax = plt.subplots(figsize=(6.5, 3.6))
        for i, (s, pts) in enumerate(dr.items()):
            pts = [(d, v) for d, v in pts if d is not None]
            if not pts:
                continue
            jitter = [(d + (i - (len(dr) - 1) / 2) * 0.12, v) for d, v in pts]
            ax.scatter(*zip(*jitter), s=14, alpha=0.45, color=SERIES[i % 8], label=s, edgecolors="none")
            by_d = defaultdict(list)
            for d, v in pts:
                by_d[d].append(v)
            xs = sorted(by_d)
            ax.plot(xs, [sum(by_d[x]) / len(by_d[x]) for x in xs], color=SERIES[i % 8], lw=2)
        ax.set(xlabel="hops from seed", ylabel="text similarity to seed", ylim=(0, 1.02),
               title="Message drift (dots = messages, line = mean)")
        if len(dr) > 1:
            ax.legend(frameon=False)
        fig.tight_layout()
        paths.append(out_dir / "drift.png")
        fig.savefig(paths[-1], dpi=150)
        plt.close(fig)

    # Probes: one panel per probe, share of each answer over time
    pt = probe_table(run)
    if pt:
        fig, axes = plt.subplots(1, len(pt), figsize=(6.5 * len(pt), 3.6), squeeze=False)
        probes = {p["id"]: p for p in run.manifest["probes"]}
        for ax, (pid, ticks) in zip(axes[0], pt.items()):
            opts = probes[pid]["options"] + ["(unparsed)"]
            ts = sorted(ticks)
            for i, o in enumerate(opts):
                ys = [ticks[t][o] / max(1, sum(ticks[t].values())) for t in ts]
                if any(ys):
                    ax.plot(ts, ys, color=SERIES[i % 8] if o != "(unparsed)" else MUTED, lw=2, marker="o", ms=4, label=o)
            ax.set(xlabel="tick", ylabel="share of agents", ylim=(0, 1.02), title=f"Probe: {pid}")
            ax.legend(frameon=False)
        fig.tight_layout()
        paths.append(out_dir / "probes.png")
        fig.savefig(paths[-1], dpi=150)
        plt.close(fig)
    return paths


# --- interactive viewer ----------------------------------------------------

def build_viewer(run: Run, out_path: Path) -> Path:
    idx, memo = message_index(run), {}
    msgs = []
    for m in run.of("message"):
        msgs.append({**{k: m[k] for k in ("id", "tick", "sender", "recipient", "content", "based_on")},
                     "roots": sorted(r.removeprefix("seed:") for r in roots_of(m["id"], idx, memo))})
    beliefs = defaultdict(dict)
    for e in run.of("turn"):
        beliefs[e["agent"]][e["tick"]] = {"beliefs": e["beliefs"], "note": e["note"], "read": e["read"], "error": e["error"]}
    probes = defaultdict(lambda: defaultdict(dict))
    for e in run.of("probe"):
        probes[e["probe"]][e["agent"]][e["tick"]] = e["answer"]
    data = {
        "scenario": run.manifest["scenario"],
        "description": run.manifest.get("description", ""),
        "steps": run.steps,
        "positions": run.manifest["positions"],
        "edges": run.manifest["edges"],
        "agents": {n: {"traits": a["traits"], "docs": a["docs"], "model": a["model"]} for n, a in run.manifest["agents"].items()},
        "seeds": [{**s, "targets": [e["recipient"] for e in run.of("seed") if e["id"] == f"seed:{s['id']}"]} for s in run.manifest["seeds"]],
        "messages": msgs,
        "exposure": exposure(run),
        "beliefs": beliefs,
        "probes": probes,
        "probe_options": {p["id"]: p["options"] for p in run.manifest["probes"]},
        "colors": SERIES,
    }
    html = (Path(__file__).parent / "viewer_template.html").read_text()
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
