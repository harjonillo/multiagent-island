"""The pixel-art doors on the experiments page.

Each experiment's door is Hannah's hand-drawn door (docs/explorer/door_art/, built there from
door_source.png by make_door_frames.py). The package carries two of its files in door_art/:

  door_open_sheet.png   six 64 x 64 frames in a row: 0 is the closed door, 1..5 the opening
  door_hover.png        the closed door with the lever turned down, for hover and keyboard focus

They go into the page once, as data URIs in its CSS (sprite_css), drawn at SCALE x so every pixel is the
same size, with image-rendering: pixelated. The opening steps the sheet's background-position, one frame
every FRAME_MS.

The empty "next experiment" slot keeps the earlier code-drawn door, as SVG <rect>s with
shape-rendering="crispEdges" (from docs/explorer/door_sprites.py), faded:

  frame_svg()       24 x 42 cells: wooden frame, dark doorway, stone step
  door_svg(...)     20 x 38 cells: the door panel with its sign board, laid over the doorway at cells (2, 2)

Positions of its overlays, in percent of their parent, are in PANEL and SIGN.
"""

from __future__ import annotations

import base64
from pathlib import Path

ART = Path(__file__).with_name("door_art")
SIZE = 64          # each sprite is 64 x 64 px
SCALE = 5          # drawn at 5x, every pixel 5 x 5
FRAMES = 6         # in the sheet: 0 closed, 1..5 opening
FRAME_MS = 80
# The art's extent: columns 9..58 (left plant to right plant), rows 8..57 (frame top to step bottom). The
# transparent margins around it are cropped, so the door is 50 x 50 sprite pixels (250 px at 5x).
LEFT, RIGHT, TOP, GROUND = 9, 59, 8, 58
BOX_W, BOX_H = (RIGHT - LEFT) * SCALE, (GROUND - TOP) * SCALE


def _pct(a: int, n: int) -> str:
    return f"{100 * a / n:.4g}%"


# The door leaf (x 20..43, y 11..48) in percent of the cropped box: the hover and focus outline goes round it.
LEAF = dict(left=_pct(20 - LEFT, RIGHT - LEFT), width=_pct(24, RIGHT - LEFT),
            top=_pct(11 - TOP, GROUND - TOP), height=_pct(38, GROUND - TOP))

# The empty slot's door: whole-pixel cells, so it is about as wide as the drawn door's frame (30 sprite
# pixels; 24 cells of 6 px = 144 px at 5x) and stands on the same ground line.
EMPTY_CELL = round(30 * SCALE / 24)

PANEL = dict(left="8.333%", width="83.333%", top="4.762%", height="90.476%")  # frame cells 2..21 x 2..39
SIGN = dict(left="10%", width="80%", top="18.42%", height="23.68%")           # door cells 2..17 x 7..15

FRAME_COLOURS = {"F": "#7A4E2E", "L": "#9A6A44", "I": "#3B2F2A", "i": "#5A4639", "P": "#E6DFCC", "Q": "#D2C0AC"}
DOOR_COLOURS = {"D": "#5DB08F", "d": "#4A9477", "S": "#E9C29B", "s": "#8A5A2B", "p": "#7A4E2E", "e": "#2F5E4A"}
EMPTY_DOOR = {"D": "#D2C0AC", "d": "#B59A82", "e": "#9E8670"}  # the faded "next experiment" door


def _data_uri(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode((ART / name).read_bytes()).decode("ascii")


def sprite_css() -> str:
    """CSS for the drawn door: the closed sprite, the hover sprite, and one rule per opening frame
    (set by the page's script as data-frame on the link)."""
    px, left, top = SIZE * SCALE, LEFT * SCALE, TOP * SCALE
    rules = [
        f".door, .door-box {{ width: {BOX_W}px; height: {BOX_H}px; }}",
        f".door .spr {{ position: absolute; inset: 0; image-rendering: pixelated; "
        f"background: url({_data_uri('door_open_sheet.png')}) -{left}px -{top}px / {FRAMES * px}px {px}px no-repeat; }}",
        f".door:hover:not([data-frame]) .spr, .door:focus-visible:not([data-frame]) .spr {{ "
        f"background-image: url({_data_uri('door_hover.png')}); background-size: {px}px {px}px; }}",
    ]
    rules += [f'.door[data-frame="{i}"] .spr {{ background-position: -{i * px + left}px -{top}px; }}'
              for i in range(1, FRAMES)]
    rules.append(f".door.empty {{ position: absolute; left: {(BOX_W - 24 * EMPTY_CELL) // 2}px; bottom: 0; "
                 f"width: {24 * EMPTY_CELL}px; height: auto; }}")
    return "\n".join(rules)


def _svg(grid: list[list[str]], colours: dict) -> str:
    h, w = len(grid), len(grid[0])
    rects = []
    for r, row in enumerate(grid):
        c = 0
        while c < w:
            ch = row[c]
            if ch == ".":
                c += 1
                continue
            e = c
            while e + 1 < w and row[e + 1] == ch:
                e += 1
            rects.append(f'<rect x="{c}" y="{r}" width="{e - c + 1}" height="1" fill="{colours[ch]}"></rect>')
            c = e + 1
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" shape-rendering="crispEdges" aria-hidden="true" '
            f'style="display: block">{"".join(rects)}</svg>')


def frame_svg() -> str:
    w, h = 24, 42
    g = [["."] * w for _ in range(h)]
    for r in range(40):
        for c in range(w):
            if c in (0, 22, 23) or r == 0:
                g[r][c] = "F"
            elif c == 1 or r == 1:
                g[r][c] = "L"
            else:
                g[r][c] = "i" if r >= 34 else "I"
    for c in range(w):
        g[40][c], g[41][c] = "P", "Q"
    return _svg(g, FRAME_COLOURS)


def door_svg(colours: dict | None = None) -> str:
    """The closed door panel: planks, two braces, a sign board with two nails, a handle backplate."""
    w, h = 20, 38
    g = [["d" if c % 4 == 3 else "D" for c in range(w)] for _ in range(h)]
    for r in (3, 32):  # cross-braces
        g[r] = ["d"] * w
    c0, c1, r0, r1 = 2, 17, 7, 15  # the sign board, with two nails above it
    g[r0 - 1][c0 + 2] = g[r0 - 1][c1 - 2] = "s"
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            g[r][c] = "s" if r in (r0, r1) or c in (c0, c1) else "S"
    for r in range(20, 26):  # handle backplate
        g[r][16] = "p"
    return _svg(g, {**DOOR_COLOURS, **(colours or {})})
