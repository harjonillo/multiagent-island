"""Reference: render a run's island as a pixel map for the explorer.

Paints one pixel per tile from the generated grid and the latest snapshot, then scales it up with
nearest neighbour so it stays crisp. Colours follow tokens.css. Written against the repo as of
2026-10-31; check attribute names (terrain, tier, buildings, sites) against island/grid.py.

usage: python export_map.py <run_dir> <out.png>
"""

import json
import sys
from pathlib import Path

from PIL import Image

from llmabm.island.grid import BRIDGE, POND, RIVER, ROCKY, SAND, SEA, GridSpace

PALETTE = {
    "sea": "#A9D3E3", "sand": "#F1E6C8", "river": "#7FBCDB", "t1": "#CFE3B4", "t2": "#B5D49A", "t3": "#97C27E",
    "rocky": "#A39C8E", "building": "#8E8A80", "plaza": "#E6DFCC", "site": "#F7F5EE", "home": "#E9C29B",
    "bridge": "#B08A5A", "tree": "#3E7A3A", "fruit": "#D2553F", "stump": "#8A5A2B", "sapling": "#6E9E4A",
    "rock": "#5F5B55", "path": "#B98A5E",
}
SCALE = 8


def rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def main(run_dir: Path, out: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    snap = None
    with open(run_dir / "events.jsonl") as f:
        for line in f:
            e = json.loads(line)
            if e["type"] == "snapshot":
                snap = e
    objs = (snap or {}).get("objects") or {}
    homes = (snap or {}).get("homes") or {}

    g = GridSpace(manifest["world"], manifest["seed"])
    if objs.get("bridge_open"):
        for x, y in g.bridge_cells:
            g.terrain[y][x] = BRIDGE

    img = Image.new("RGB", (g.W, g.H))
    px = img.load()
    for y in range(g.H):
        for x in range(g.W):
            t = g.terrain[y][x]
            key = ("sea" if t == SEA else "sand" if t == SAND else "river" if t in (RIVER, POND)
                   else "bridge" if t == BRIDGE else "rocky" if t == ROCKY else f"t{g.tier[y][x]}")
            px[x, y] = rgb(PALETTE[key])
    lived_in = set(homes.values())
    for sid, s in g.sites.items():
        for x, y in s["cells"]:
            px[x, y] = rgb(PALETTE["home" if sid in lived_in else "site"])
    for b in g.buildings.values():
        for x, y in b["cells"]:
            px[x, y] = rgb(PALETTE["building" if b.get("blocking", True) else "plaza"])
    for x, y, *_ in objs.get("paths", []):
        px[x, y] = rgb(PALETTE["path"])
    for x, y, kind in objs.get("trees", []):
        px[x, y] = rgb(PALETTE[{"hardwood": "tree", "fruit": "fruit", "stump": "stump", "sapling": "sapling"}[kind]])
    for r in objs.get("rocks", []):
        if r:
            px[r[0], r[1]] = rgb(PALETTE["rock"])
    img.resize((g.W * SCALE, g.H * SCALE), Image.NEAREST).save(out)

    # Positions the page needs for its HTML overlays, in tile coordinates.
    centre = lambda cells: (sum(c[0] for c in cells) / len(cells), sum(c[1] for c in cells) / len(cells))  # noqa: E731
    return {
        "W": g.W, "H": g.H,
        "buildings": {n: {"x": centre(b["cells"])[0], "top": min(c[1] for c in b["cells"])} for n, b in g.buildings.items()},
        "homes": {n: centre(g.sites[s]["cells"]) for n, s in homes.items() if s},
        "rocks": [[i + 1, r[0], r[1]] for i, r in enumerate(objs.get("rocks", [])) if r],
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), Path(sys.argv[2])), indent=1))
