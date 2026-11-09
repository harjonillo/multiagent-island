# Hand-drawn door art

Added Nov 9. Hannah drew her own door to replace the code-drawn one on the Experiments page
(`docs/explorer/door_sprites.py`, used in the repo as `src/llmabm/island/site_doors.py` and
`site_experiments.py`).

| file | what it is |
|---|---|
| `door_source.png` | Hannah's original drawing, 64 x 64, transparent background. Do not edit; everything else is built from it |
| `door_hover.png` | closed door with the lever turned 45 degrees down, for hover and keyboard focus |
| `door_open_0.png` .. `door_open_5.png` | the opening animation. 0 is the closed door (same as the source), 5 is nearly fully open |
| `door_open_sheet.png` | the six frames in one row, 384 x 64, each frame 64 x 64 |
| `door_preview.gif` | preview at 6x: open, hold, close, 80 ms per frame |
| `make_door_frames.py` | rebuilds every file above from `door_source.png` (needs Pillow) |

## How the frames were made

- Only the door leaf changes (x 20..43, y 11..48 in the 64 x 64 image, including its dark outline). The
  frame, the step and both plants are identical in every frame, so they can be one static layer.
- The hinge is on the left (the handle is on the right). Each frame squeezes the leaf toward the hinge:
  widths 24, 20, 15, 10, 6, 3 px. The free edge pulls in 0 to 4 px at top and bottom so the door reads as
  swinging inward, and the leaf darkens to about half brightness by the last frame.
- Behind the door is a dark interior (`#1C2624`) with a 3 px floor strip (`#34302C`).
- The lever turns 45 degrees down about the centre of its 2x2 hub between frame 0 and frame 1 (Hannah's
  call). In frame 1 the lever is drawn on top at full size, because the squeeze would drop its arm.
  From frame 2 on the squeeze mostly hides it, which Hannah is fine with.
- Suggested timing: 80 ms per frame. Closing is the same frames in reverse.

## Where this differs from SPEC.md

These are the parts of the current spec the new art does not fit. Ask Hannah before settling the first two.

1. **No sign board.** The old door has a sign board where `EXP NN` and the scenario name sit (`SIGN`).
   The new door has an arched panel instead. The label needs a new home (on the arch, above the door,
   or only in the card underneath).
2. **One colour.** The old sprites were recoloured per state (`EMPTY_DOOR` for the faded "next
   experiment" door). The new door is a fixed PNG. The spec already fades the empty door with
   grayscale 60% and opacity 0.55, which may be enough on its own.
3. **The handle is baked in.** There is no separate knob layer. Hover swaps the whole sprite to
   `door_hover.png` instead of swapping `knob_svg(True)`.
4. **Six frames, 80 ms**, instead of four opening frames at 110 ms.
5. **The plants are part of the sprite**, so the door's footprint is wider than the old frame.
6. **Size.** The spec draws the door 240 px wide, which is 3.75x and will give uneven pixels.
   192 px (3x) or 256 px (4x) keeps every pixel the same size. Use `image-rendering: pixelated`
   if the PNGs are used as images.

The explorer page is self-contained, so the frames have to be embedded: either base64 PNGs, or converted
to `<rect>` SVGs with the existing `_svg()` helper in `site_doors.py` (keeps the crisp-edges approach and
the no-JS fallback the same).

## Settled (Nov 9)

Built into the Experiments page (`src/llmabm/island/site_doors.py`, which carries `door_open_sheet.png` and
`door_hover.png` in `src/llmabm/island/door_art/`; rerun `make_door_frames.py` and copy those two again
after changing the drawing). Hannah's calls: the door has no label, `EXP NN` is on the card only; the empty
"next experiment" slot keeps the old code-drawn door with its sign. Drawn at 5x; see SPEC.md.

## Suggested prompt for Claude Code

> docs/explorer/door_art/ has Hannah's hand-drawn door and its animation frames; read its README.md first.
> Replace the code-drawn door on the Experiments page with these sprites: door_open_0.png closed,
> door_hover.png on hover and focus, door_open_1..5 for the opening at about 80 ms per frame, then
> navigate as before. Keep the reduced-motion and no-JS behaviour from SPEC.md. Before building, ask me
> where the EXP NN label should go now that the door has no sign board, and how the empty "next
> experiment" door should look. Update SPEC.md and site_doors.py to match, keep the tests passing, and
> show me the Experiments page for a mock run when it's done.
