# Card render pipeline

Two paths for producing the 120-frame turntable the frontend plays back.

## Path A — per-card Blender render (legacy, slow)

The original freelancer flow: swap the two card textures in `Карточка.blend`,
render the full turntable, convert PNG → WebP. Reliable but takes minutes per
card and needs Blender on the rendering host.

```
# canonical: 36-frame turntable -> WebP in public/pack/card-frames/
python card_render/render_card.py --start 1 --end 360 --step 10 --webp \
  --card1 "card_render/Карточка 1.jpeg" --card2 "card_render/Карточа 2.jpeg"
```

Texture datablock names default to `Карточка 1.jpeg` / `Карточа 2.jpeg` (the
freelancer's names, note the typo in the second). Override with
`--tex1` / `--tex2` if the .blend changes. `--list` prints the image
datablocks so you can find them.

## Path B — bake once, compose per card (production)

The geometry, camera, lights and rotation are identical for every card; only
the two face textures change. So we bake the texture-independent shading layer
**once** and reuse it for every mint with a pure-CPU perspective warp + multiply.

### B.1 — bake the template (one-time, slow)

```
python card_render/render_card.py --bake-template \
  --start 1 --end 360 --step 3       # → 120 frames
```

Writes to `card_render/template/`:

- `template.json` — per-frame card-quad corners + which face (front/back) is
  visible
- `light_0001.png` … `light_NNNN.png` — baked RGBA shading layer (lighting +
  reflections on a white-override material; alpha = card coverage)

Rebake only when the `.blend`, camera, lights or rotation change.

### B.2 — compose a card (per mint, no Blender)

```
python card_render/render_card.py --compose \
  --template card_render/template \
  --card1 front.png --card2 back.png --webp
```

`--webp` (no value) lands the 120 webp frames directly in
`user_web_frontend/public/pack/card-frames/` so the player picks them up on
reload. Pass `--out DIR` to redirect, or `--webp DIR` for an alternate target.

Pure Pillow + numpy. Order of milliseconds per frame. No GPU, no GL context,
no Blender.

### How B works (math)

The card is flat, so each frame's visible face is a perspective-projected
rectangle (quad). For each baked frame:

1. The bake stores the four screen-space corners of the visible face (TL, TR,
   BR, BL) and which side (front/back) faces the camera.
2. The compositor solves the 3×3 homography mapping the texture rectangle to
   that quad, and warps the texture into the output frame.
3. The warped face is multiplied by the baked light layer (so all the shading
   / shadows / foil / HDRI reflections from Blender stay correct), while the
   edges / thickness pixels come straight from the light layer.

`mirror_back: true` in `template.json` flips the back texture horizontally
before warping (a card seen from behind shows its image mirrored).

## Files

```
card_render/
  Карточка.blend                  # 3D scene (gitignored)
  textures/                       # scratch slots for the current card's art
  PNG/                            # per-card Path A render output (gitignored)
  template/                       # baked Path B template (gitignored)
  blender_swap_and_render.py      # inside Blender: swap + render (Path A)
  bake_turntable_template.py      # inside Blender: bake the template (Path B.1)
  render_card.py                  # host wrapper: routes to A / B.1 / B.2
scripts/cardgen/
  turntable_compose.py            # CPU-only compositor used by Path B.2
```

## Production notes

- Blender has **no separate "server edition"** — the normal build runs headless
  via `blender -b` (background, no GUI). Path A and B.1 both need Blender.
- On a Linux prod box: download the official tarball from blender.org (don't
  use the Steam build). Cycles renders fully headless. **EEVEE (legacy) needs a
  GL context** — run under `xvfb-run` or use Cycles / EEVEE-Next.
- Path B.2 has zero Blender dependency: ship `template/` (or stash it in R2) +
  the mint-side `turntable_compose.py` and you can mint cards on any cheap
  worker that has Pillow + numpy.
- `python scripts/cardgen/turntable_compose.py --selftest` validates the
  homography + multiply math without touching Blender; the same check runs in
  `tests/test_turntable_compose.py`.
