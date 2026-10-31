"""Drawing the tile-grid island: the map, and each villager's movements over a day."""

from __future__ import annotations

from pathlib import Path

from .grid import BRIDGE, POND, RIVER, ROCKY, SAND, SEA, GridSpace

COLORS = {
    "sea": (0.62, 0.79, 0.90), "sand": (0.95, 0.89, 0.77), "river": (0.44, 0.69, 0.88),
    "tier1": (0.80, 0.89, 0.71), "tier2": (0.69, 0.83, 0.58), "tier3": (0.57, 0.76, 0.47),
    "bridge": (0.69, 0.54, 0.35), "building": (0.54, 0.54, 0.52), "plaza": (0.86, 0.84, 0.78),
    "home": (0.92, 0.41, 0.20), "site": (0.99, 0.99, 0.98),
}


def _rgb(g: GridSpace, homes: dict | None):
    import numpy as np

    img = np.zeros((g.H, g.W, 3))
    for y in range(g.H):
        for x in range(g.W):
            t = g.terrain[y][x]
            if t == SEA:
                c = COLORS["sea"]
            elif t == SAND:
                c = COLORS["sand"]
            elif t in (RIVER, POND):
                c = COLORS["river"]
            elif t == BRIDGE:
                c = COLORS["bridge"]
            elif t == ROCKY:
                c = (0.55, 0.52, 0.47)
            else:
                c = COLORS[f"tier{g.tier[y][x]}"]
            img[y, x] = c
    for name, b in g.buildings.items():
        for x, y in b["cells"]:
            img[y, x] = COLORS["plaza"] if not b.get("blocking", True) else COLORS["building"]
    occupied = set((homes or {}).values())
    for sid, s in g.sites.items():
        for x, y in s["cells"]:
            img[y, x] = COLORS["home"] if sid in occupied else COLORS["site"]
    return img


PATH_COLORS = {"dirt": (0.72, 0.58, 0.42), "stone": (0.70, 0.70, 0.68), "brick": (0.74, 0.40, 0.32),
               "sand": (0.93, 0.85, 0.66), "terracotta": (0.80, 0.50, 0.33)}


def _overlay_rgb(img, objects: dict):
    for x, y, style, *_ in objects.get("paths", []):
        img[y, x] = PATH_COLORS.get(style, (0.72, 0.58, 0.42))
    return img


def _built(ax, objects: dict, small: bool = False):
    ms = 1.5 if small else 3.0
    fences = [(x, y) for x, y, item, kind, owner in objects.get("placed", []) if kind == "fence"]
    furn = [(x, y) for x, y, item, kind, owner in objects.get("placed", []) if kind != "fence"]
    fl_b = [(x, y) for x, y, kind, owner, bloomed in objects.get("flowers", []) if bloomed]
    fl_s = [(x, y) for x, y, kind, owner, bloomed in objects.get("flowers", []) if not bloomed]
    if fences:
        ax.plot(*zip(*fences), "s", ms=ms * 0.9, color="#5b3a1e", ls="")
    if furn:
        ax.plot(*zip(*furn), "s", ms=ms, color="#6250d6", ls="")
    if fl_b:
        ax.plot(*zip(*fl_b), "*", ms=ms * 1.3, color="#e87ba4", ls="")
    if fl_s:
        ax.plot(*zip(*fl_s), ".", ms=ms, color="#8fbf6a", ls="")


def _objects(ax, g: GridSpace, trees: list, rocks: list, small: bool = False):
    ms = 1.5 if small else 3.2
    rocks = [r for r in rocks if r]
    hard = [(x, y) for x, y, k in trees if k == "hardwood"]
    fruit = [(x, y) for x, y, k in trees if k == "fruit"]
    sap = [(x, y) for x, y, k in trees if k == "sapling"]
    stumps = [(x, y) for x, y, k in trees if k == "stump"]
    if stumps:
        ax.plot(*zip(*stumps), "o", ms=ms * 0.8, color="#8a5a2b", ls="")
    if hard:
        ax.plot(*zip(*hard), "o", ms=ms, color="#2f6b2f", ls="")
    if fruit:
        ax.plot(*zip(*fruit), "o", ms=ms, color="#2f6b2f", mfc="#e34948", ls="")
    if sap:
        ax.plot(*zip(*sap), "o", ms=ms * 0.6, color="#5a8f3a", ls="")
    if rocks:
        ax.plot(*zip(*rocks), "s", ms=ms * 1.1, color="#6b6a66", ls="")


def _legend(ax, g: GridSpace, homes: dict | None, bridge_site: bool) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    sq = lambda c, label: Patch(facecolor=c, edgecolor="#8a8984", label=label)  # noqa: E731
    dot = lambda label, **k: Line2D([], [], ls="", label=label, **k)  # noqa: E731
    h = [sq(COLORS["sea"], "sea"), sq(COLORS["sand"], "beach"), sq((0.55, 0.52, 0.47), "rocky beach (no walking)"),
         sq(COLORS["river"], "river and pond"), sq(COLORS["tier1"], "lower ground"),
         sq(COLORS["tier2"], "middle cliff (ladder)"), sq(COLORS["tier3"], "top cliff (ladder)"),
         sq(COLORS["building"], "building"), sq(COLORS["plaza"], "plaza (walkable)"),
         sq(COLORS["site"], "homesite, free")]
    if any((homes or {}).values()):
        h.append(sq(COLORS["home"], "homesite, lived in"))
    if g.bridge_built:
        h.append(sq(COLORS["bridge"], "bridge"))
    h += [dot("hardwood tree", marker="o", color="#2f6b2f"), dot("fruit tree", marker="o", color="#2f6b2f", mfc="#e34948"),
          dot("rock (numbered)", marker="s", color="#6b6a66"), dot("flowers", marker="*", color="#e87ba4")]
    if bridge_site and not g.bridge_built and g.bridge_cells:
        h.append(dot("where the bridge will go", marker="x", color="#5b3a1e"))
    ax.legend(handles=h, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=8)


def render_grid_map(g: GridSpace, out: Path, homes: dict | None = None, trees: list | None = None,
                    rocks: list | None = None, title: str = "Island", objects: dict | None = None,
                    bridge_site: bool = False) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(13.5, 11 * g.H / g.W + 0.6))
    ax.imshow(_overlay_rgb(_rgb(g, homes), objects or {}), interpolation="nearest", origin="upper")
    _objects(ax, g, trees or [], rocks if rocks is not None else list(g.rock_cells))
    _built(ax, objects or {})
    for i, c in enumerate(rocks if rocks is not None else g.rock_cells):
        if c:
            ax.annotate(str(i + 1), tuple(c), xytext=(3, 3), textcoords="offset points", fontsize=7, color="#0b0b0b")
    for name, b in g.buildings.items():
        xs, ys = zip(*b["cells"])
        ax.annotate(name, (sum(xs) / len(xs), min(ys) - 0.8), ha="center", va="bottom", fontsize=8, color="#0b0b0b")
    who = {s: n for n, s in (homes or {}).items() if s}
    for sid, s in g.sites.items():
        xs, ys = zip(*s["cells"])
        ax.annotate(who.get(sid, sid), (sum(xs) / len(xs), max(ys) + 1.2), ha="center", va="top", fontsize=7,
                    color="#0b0b0b" if sid in who else "#52514e")
    if bridge_site and not g.bridge_built and g.bridge_cells:
        ax.plot(*zip(*g.bridge_cells), "x", ms=4, color="#5b3a1e", ls="")
    _legend(ax, g, homes, bridge_site)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    out = Path(out)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def render_tracks(g: GridSpace, out: Path, tracks: dict[str, list], homes: dict | None, trees: list, rocks: list,
                  title: str, objects: dict | None = None) -> Path:
    """Small multiples: each villager's hour-by-hour positions over one day on a faint map."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis import SERIES

    names = list(tracks)
    cols = 5
    rows = (len(names) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 3.2 * rows * g.H / g.W + 0.6), squeeze=False)
    base = _overlay_rgb(_rgb(g, homes), objects or {}) * 0.45 + 0.55
    for ax, n in zip(axes.flat, names):
        ax.imshow(base, interpolation="nearest", origin="upper")
        _objects(ax, g, trees, rocks, small=True)
        pts = [p for p in tracks[n] if p]
        if pts:
            xs, ys = zip(*pts)
            ax.plot(xs, ys, "-", color=SERIES[0], lw=1.2, alpha=0.9)
            ax.plot(xs, ys, "o", color=SERIES[0], ms=2.5)
            ax.plot([xs[0]], [ys[0]], "o", color=SERIES[1], ms=5)
        ax.set_title(n, fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    out = Path(out)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out
