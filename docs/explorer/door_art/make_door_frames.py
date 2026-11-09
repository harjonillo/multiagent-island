"""Build the door animation frames from Hannah's hand-drawn door (door_source.png, 64 x 64).

Outputs, all 64 x 64 with transparency, written next to this file:
  door_hover.png         closed door with the lever turned 45 degrees down (hover / focus sprite)
  door_open_0..5.png     opening frames: 0 is the untouched source, 1 has the lever turned and the door
                         slightly open, 2..5 narrow the door toward its left hinge
  door_open_sheet.png    the six frames side by side (384 x 64)
  door_preview.gif       6x preview: open, hold, close, hold, 80 ms per frame

Only the door leaf (x 20..43, y 11..48) changes. The frame, step and plants are never redrawn.
usage: python make_door_frames.py   (needs Pillow)
"""
from pathlib import Path
from PIL import Image

HERE = Path(__file__).parent
src = Image.open(HERE / "door_source.png").convert("RGBA")

X0, X1, Y0, Y1 = 20, 44, 11, 49           # door leaf, 24 x 38, hinge on the left
W, H = X1 - X0, Y1 - Y0
INTERIOR = (28, 38, 36, 255)               # dark room behind the door
FLOOR = (52, 48, 44, 255)                  # bottom 3 rows of the doorway
G = (255, 197, 25, 255); Hh = (225, 174, 22, 255); F = (153, 119, 15, 255); D = (131, 203, 189, 255)
E = (97, 75, 10, 255)

# Lever: turned 45 degrees down about the centre of its 2x2 hub (x 39..40, y 30..31).
OLD_LEVER = [(37, 30), (38, 30), (39, 30), (40, 30), (36, 31), (37, 31), (39, 31), (40, 31)]
NEW_LEVER = {(39, 30): G, (40, 30): G, (39, 31): G, (40, 31): Hh,   # hub unchanged
             (38, 31): G, (37, 32): G, (37, 33): G, (38, 33): Hh}   # arm and hooked tip, pointing down-left

plain = src.copy()                          # door with no lever (plate only)
for (x, y) in OLD_LEVER:
    plain.putpixel((x, y), F if 38 <= x <= 41 and 30 <= y <= 32 else D)
turned = plain.copy()
for k, c in NEW_LEVER.items():
    turned.putpixel(k, c)
KNOB = [(x, y) for x in range(36, 42) for y in range(28, 35) if turned.getpixel((x, y)) in (G, Hh, F, E)]

# (width of the leaf, brightness, how far the free edge pulls in at top and bottom)
SPECS = [(24, 1.00, 0), (20, 0.90, 1), (15, 0.80, 2), (10, 0.70, 3), (6, 0.60, 3), (3, 0.50, 4)]


def shade(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k), c[3])


def squish(base, w, k, shrink, mapping=None):
    leaf = base.crop((X0, Y0, X1, Y1)).load()
    im = src.copy()
    px = im.load()
    for y in range(Y0, Y1):
        for x in range(X0, X1):
            px[x, y] = FLOOR if y >= Y1 - 3 else INTERIOR
    for dx in range(w):
        sx = min(W - 1, dx * W // w)
        s = round(shrink * (dx + 1) / w)
        h = H - 2 * s
        for dy in range(h):
            sy = min(H - 1, dy * H // h)
            if mapping is not None:
                mapping.setdefault((X0 + sx, Y0 + sy), (X0 + dx, Y0 + s + dy))
            px[X0 + dx, Y0 + s + dy] = shade(leaf[sx, sy], k)
    return im


frames = [src.copy()]
# Frame 1: squish the door without its lever, then lay the turned knob on top at full size, so the
# column-dropping squish does not cut the lever's arm off.
m = {}
f1 = squish(plain, *SPECS[1], mapping=m)
ox, oy = m[(39, 30)][0] - 39, m[(39, 30)][1] - 30
for (x, y) in KNOB:
    f1.putpixel((x + ox, y + oy), shade(turned.getpixel((x, y)), SPECS[1][1]))
frames.append(f1)
frames += [squish(turned, *s) for s in SPECS[2:]]

turned.save(HERE / "door_hover.png")
for i, f in enumerate(frames):
    f.save(HERE / f"door_open_{i}.png")
sheet = Image.new("RGBA", (64 * len(frames), 64))
for i, f in enumerate(frames):
    sheet.paste(f, (64 * i, 0))
sheet.save(HERE / "door_open_sheet.png")

seq = frames + [frames[-1]] * 6 + frames[::-1] + [frames[0]] * 8
out = []
for f in seq:
    b = f.resize((384, 384), Image.NEAREST)
    c = Image.new("RGBA", b.size, (238, 232, 214, 255))
    c.alpha_composite(b)
    out.append(c.convert("RGB"))
out[0].save(HERE / "door_preview.gif", save_all=True, append_images=out[1:], duration=80, loop=0)
print("wrote frames to", HERE)
