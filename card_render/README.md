# Card render pipeline

Automates the freelancer's manual Blender workflow (replace 2 textures → Ctrl+F12 →
36 PNG frames) into a headless CLI suitable for the mint pipeline.

## Files

```
card_render/
  Карточка.blend              # the 3D scene (NOT committed — gitignored)
  textures/                   # scratch slots for the current card's art
  PNG/                        # Blender render output (PNG, gitignored)
  blender_swap_and_render.py  # runs INSIDE Blender: swaps textures + renders
  render_card.py              # host wrapper: finds Blender, runs render, -> WebP
```

## One-time setup

1. Put `Карточка.blend` in this folder.
2. Install Blender (free: Steam, or https://www.blender.org/download/).
3. Confirm the texture datablock names:
   ```
   python card_render/render_card.py --list
   ```
   If they are not `Карточка 1` / `Карточка 2`, pass the real names with
   `--tex1` / `--tex2`.

## Render a card

The scene is a 360-frame turntable (1°/frame). `--step 10` samples it down to a
36-frame turntable (10°/frame, seamless loop) — the format the frontend expects.

```
# canonical: 36-frame turntable -> WebP in public/pack/card-frames/
python card_render/render_card.py --start 1 --end 360 --step 10 --webp \
  --card1 "card_render/Карточка 1.jpeg" --card2 "card_render/Карточа 2.jpeg"

# full 360-frame turntable (heavier, ~18MB):
python card_render/render_card.py --webp
```

Texture datablock names default to `Карточка 1.jpeg` / `Карточа 2.jpeg`
(the freelancer's names, note the typo in the second). Override with
`--tex1` / `--tex2` if the .blend changes.

Blender path resolution: `--blender` arg → `BLENDER_EXE` env → common install
locations → `blender` on PATH.

## Production notes

- Blender has **no separate "server edition"** — the normal build runs headless
  via `blender -b` (background, no GUI).
- On a Linux prod box: download the official tarball from blender.org (don't use
  the Steam build there). Cycles renders fully headless (CPU, or GPU via
  CUDA/OptiX). **EEVEE (legacy) needs a GL context** — run under `xvfb-run` or use
  Cycles / EEVEE-Next.
- Check the engine the .blend uses before deploying (`--list` run also prints the
  scene); it determines whether you need Xvfb and GPU.
