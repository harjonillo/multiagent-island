"""The pixel-art door sprites on the experiments page, as SVG strings (from docs/explorer/door_sprites.py).

Every sprite is a grid of coloured cells drawn as <rect>s with shape-rendering="crispEdges", so it stays
pixel art at any size. Layers, from back to front:

  frame_svg()       24 x 42 cells: wooden frame, dark doorway, stone step
  door_svg(w)       20 x 38 cells: the door panel, laid over the doorway at cells (2, 2)
                    w = 20 is the closed door; 14, 9, 5, 2 are the opening frames
  knob_svg(twist)   5 x 3 cells: the lever handle, level or turned down (hover)

Positions of the overlays, in percent of their parent, are in PANEL, SIGN and KNOB.
"""


OPEN_WIDTHS = [20, 14, 9, 5, 2]  # closed, then four opening frames, about 110 ms apart

PANEL = dict(left="8.333%", width="83.333%", top="4.762%", height="90.476%")  # frame cells 2..21 x 2..39
SIGN = dict(left="10%", width="80%", top="18.42%", height="23.68%")           # door cells 2..17 x 7..15
KNOB = dict(left="70%", top="57.9%", width="25%")                             # door cells 14..18 x 22..24

FRAME_COLOURS = {"F": "#7A4E2E", "L": "#9A6A44", "I": "#3B2F2A", "i": "#5A4639", "P": "#E6DFCC", "Q": "#D2C0AC"}
DOOR_COLOURS = {"D": "#5DB08F", "d": "#4A9477", "S": "#E9C29B", "s": "#8A5A2B", "p": "#7A4E2E", "e": "#2F5E4A"}
EMPTY_DOOR = {"D": "#D2C0AC", "d": "#B59A82", "e": "#9E8670"}  # the faded "next experiment" door
KNOB_COLOURS = {"K": "#E9B949", "k": "#B8862B"}


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


def _door_grid() -> list[list[str]]:
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
    return g


def door_svg(width: int = 20, colours: dict | None = None) -> str:
    """The door face squeezed to `width` cells toward its left hinge (nearest neighbour), with a one-cell
    dark edge for its thickness and the free edge pulled in at top and bottom, so it reads as swinging
    inward. width=20 is the closed door."""
    col = {**DOOR_COLOURS, **(colours or {})}
    src = _door_grid()
    w, h = 20, 38
    if width >= w:
        return _svg(src, col)
    g = [["."] * w for _ in range(h)]
    lift = (w - width) // 6
    for j in range(width):
        sc = min(w - 1, int(j * w / width))
        inset = (lift * j) // max(1, width - 1)
        for r in range(inset, h - inset):
            g[r][j] = src[r][sc]
    for r in range(lift, h - lift):
        g[r][width] = "e"
    return _svg(g, col)


def knob_svg(twisted: bool = False) -> str:
    rows = ["..K..", ".KKk.", "KK.k."] if twisted else ["..K..", "KKKKk", "..k.."]
    return _svg([list(r) for r in rows], KNOB_COLOURS)
