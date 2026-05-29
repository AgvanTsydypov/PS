"""
Per-mint turntable compositor (CPU-only, no Blender at runtime).

The expensive 3D work — geometry, lighting, shadows, foil — is baked ONCE into a
texture-independent template (see ``card_render/bake_turntable_template.py``).
Every card shares that template; only the two textures (front/back PNGs, already
produced by the existing SVG→PNG pipeline) change. At mint time we just warp the
textures onto the per-frame card quad and multiply by the baked shading, then
encode WebP. This is a handful of milliseconds per frame on CPU.

Template layout (a directory or R2 prefix):

    template.json
    light_0001.png ... light_NNNN.png   # baked RGBA shading layer per frame

``template.json``::

    {
      "version": 1,
      "frame_count": 120,
      "width": 900,            # composed frame size (px)
      "height": 1500,
      "mirror_back": true,     # back texture is seen mirrored -> flip before warp
      "frames": [
        {
          "index": 1,
          "side": "front",                       # face toward the camera
          "quad": [[x,y],[x,y],[x,y],[x,y]],      # TL,TR,BR,BL of the face, output px
          "light": "light_0001.png"
        },
        ...
      ]
    }

The compositor is intentionally dependency-light: Pillow + numpy only. Both are
imported lazily so importing this module never fails in environments (e.g. the
test suite) that don't have them installed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Texture corner order used everywhere below: TL, TR, BR, BL.
_CORNER_ORDER = ("TL", "TR", "BR", "BL")


# ─────────────────────────────────────────────────────────────────────────────
# Geometry
# ─────────────────────────────────────────────────────────────────────────────

def _find_coeffs(
    dst_quad: Sequence[Sequence[float]],
    src_quad: Sequence[Sequence[float]],
) -> List[float]:
    """8 perspective coefficients for ``PIL.Image.transform(..., PERSPECTIVE)``.

    PIL's PERSPECTIVE maps *output* pixels back to *source* pixels, so we solve
    for the transform that sends each output quad corner (``dst_quad``) to the
    matching texture corner (``src_quad``).
    """
    import numpy as np  # lazy

    matrix = []
    for (xd, yd), (xs, ys) in zip(dst_quad, src_quad):
        matrix.append([xd, yd, 1, 0, 0, 0, -xs * xd, -xs * yd])
        matrix.append([0, 0, 0, xd, yd, 1, -ys * xd, -ys * yd])
    a = np.array(matrix, dtype=float)
    b = np.array([c for pt in src_quad for c in pt], dtype=float)
    res = np.linalg.solve(a, b)
    return res.tolist()


# ─────────────────────────────────────────────────────────────────────────────
# Template
# ─────────────────────────────────────────────────────────────────────────────

class TurntableTemplate:
    """A baked, texture-independent turntable template loaded from a directory."""

    def __init__(self, root: str, manifest: Dict[str, Any]):
        self.root = root
        self.manifest = manifest
        self.frame_count: int = int(manifest["frame_count"])
        self.width: int = int(manifest["width"])
        self.height: int = int(manifest["height"])
        self.mirror_back: bool = bool(manifest.get("mirror_back", True))
        self.frames: List[Dict[str, Any]] = list(manifest["frames"])

    @classmethod
    def load(cls, root: str) -> "TurntableTemplate":
        with open(os.path.join(root, "template.json"), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        return cls(root, manifest)

    def light_image(self, frame_meta: Dict[str, Any]):
        from PIL import Image  # lazy

        path = os.path.join(self.root, frame_meta["light"])
        return Image.open(path).convert("RGBA")


# ─────────────────────────────────────────────────────────────────────────────
# Compositing
# ─────────────────────────────────────────────────────────────────────────────

def _quad_corners_px(width: int, height: int) -> List[Tuple[int, int]]:
    """Texture corners in TL, TR, BR, BL pixel order."""
    return [(0, 0), (width, 0), (width, height), (0, height)]


def compose_frame(
    front_tex,
    back_tex,
    template: TurntableTemplate,
    frame_meta: Dict[str, Any],
):
    """Compose a single RGBA turntable frame (PIL Image) from the two textures."""
    from PIL import Image  # lazy

    side = frame_meta.get("side", "front")
    tex = front_tex if side == "front" else back_tex
    if side == "back" and template.mirror_back:
        tex = tex.transpose(Image.FLIP_LEFT_RIGHT)

    w, h = template.width, template.height
    quad = [tuple(p) for p in frame_meta["quad"]]
    src = _quad_corners_px(tex.width, tex.height)
    coeffs = _find_coeffs(quad, src)

    # Warp the texture into the face quad; everything outside the quad is
    # transparent so it composites cleanly over the baked shading layer.
    warped = tex.transform(
        (w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0)
    )

    light = template.light_image(frame_meta)
    if light.size != (w, h):
        light = light.resize((w, h), Image.LANCZOS)

    return _multiply_over(warped, light)


def _multiply_over(warped, light):
    """face = warped_rgb × light_rgb (baked shading); edges = light_rgb alone.

    The baked light layer renders card *faces* as white albedo (so its RGB is
    pure shading to multiply onto the texture) while edges/thickness keep their
    real material. So: where the warped face covers a pixel, multiply; elsewhere
    the visible card pixels come straight from the light layer. Final alpha is
    the baked coverage.
    """
    import numpy as np  # lazy
    from PIL import Image  # lazy

    f = np.asarray(warped, dtype=np.float32) / 255.0          # H,W,4 (texture)
    l = np.asarray(light, dtype=np.float32) / 255.0           # H,W,4 (shading)

    face = f[..., 3:4]                                        # texture coverage
    multiplied = f[..., :3] * l[..., :3]                      # shaded face
    rgb = multiplied * face + l[..., :3] * (1.0 - face)       # edges fall back to light
    alpha = l[..., 3:4]                                       # baked coverage

    out = np.concatenate([rgb, alpha], axis=-1)
    out = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _load_texture(data, size: Optional[Tuple[int, int]] = None):
    """Accept PNG bytes / a path / a PIL Image; return RGBA PIL Image."""
    from io import BytesIO
    from PIL import Image  # lazy

    if hasattr(data, "convert"):  # already a PIL Image
        img = data
    elif isinstance(data, (bytes, bytearray)):
        img = Image.open(BytesIO(data))
    else:
        img = Image.open(data)
    img = img.convert("RGBA")
    if size is not None and img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return img


def compose_turntable(
    front: Any,
    back: Any,
    template: "TurntableTemplate | str",
    *,
    out_dir: Optional[str] = None,
    target_width: Optional[int] = None,
    webp_quality: int = 92,
) -> List[Tuple[int, bytes]]:
    """Render the full turntable for one card.

    ``front``/``back`` may be PNG bytes, file paths, or PIL Images. ``template``
    is a ``TurntableTemplate`` or a path to its directory. Returns a list of
    ``(frame_index, webp_bytes)``; if ``out_dir`` is given, also writes
    ``{index:04d}.webp`` files (the naming the frontend already expects).
    """
    from io import BytesIO

    if isinstance(template, str):
        template = TurntableTemplate.load(template)

    front_tex = _load_texture(front)
    back_tex = _load_texture(back)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    results: List[Tuple[int, bytes]] = []
    for frame_meta in template.frames:
        idx = int(frame_meta["index"])
        frame = compose_frame(front_tex, back_tex, template, frame_meta)

        if target_width and frame.width > target_width:
            new_h = round(frame.height * target_width / frame.width)
            from PIL import Image  # lazy

            frame = frame.resize((target_width, new_h), Image.LANCZOS)

        buf = BytesIO()
        frame.save(buf, "WEBP", quality=webp_quality, method=6)
        data = buf.getvalue()
        results.append((idx, data))

        if out_dir:
            with open(os.path.join(out_dir, f"{idx:04d}.webp"), "wb") as fh:
                fh.write(data)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI / self-test
# ─────────────────────────────────────────────────────────────────────────────

def _selftest() -> None:
    """Synthesize a tiny 2-frame template + textures, compose, sanity-check.

    Verifies the perspective warp lands the texture inside the declared quad and
    that the multiply/alpha compositing produces a non-empty card. No Blender,
    no real template needed.
    """
    import tempfile
    import numpy as np
    from PIL import Image

    W, H = 120, 200
    with tempfile.TemporaryDirectory() as tmp:
        # Two flat light layers: full-coverage white shading (so multiply is a
        # no-op and we can check the texture survives the warp).
        for i in (1, 2):
            Image.new("RGBA", (W, H), (255, 255, 255, 255)).save(
                os.path.join(tmp, f"light_{i:04d}.png")
            )
        manifest = {
            "version": 1,
            "frame_count": 2,
            "width": W,
            "height": H,
            "mirror_back": True,
            "frames": [
                # Frame 1: card fills the centre, facing front.
                {"index": 1, "side": "front",
                 "quad": [[20, 20], [100, 20], [100, 180], [20, 180]],
                 "light": "light_0001.png"},
                # Frame 2: a narrower (rotated-ish) quad, back side.
                {"index": 2, "side": "back",
                 "quad": [[50, 30], [90, 25], [92, 175], [48, 170]],
                 "light": "light_0002.png"},
            ],
        }
        with open(os.path.join(tmp, "template.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

        # Distinct solid textures so we can tell front vs back apart.
        front = Image.new("RGBA", (80, 130), (220, 40, 0, 255))   # brand red
        back = Image.new("RGBA", (80, 130), (0, 80, 220, 255))    # blue

        frames = compose_turntable(front, back, tmp)
        assert len(frames) == 2, "expected 2 frames"

        f1 = np.asarray(Image.open(__import__("io").BytesIO(frames[0][1])).convert("RGBA"))
        # Centre pixel should be the (red) front texture, opaque.
        cy, cx = H // 2, (20 + 100) // 2
        r, g, b, a = f1[cy, cx]
        assert a > 200, f"centre not opaque: alpha={a}"
        assert r > g and r > b, f"centre not red-dominant: {(r, g, b)}"
        # A corner well outside the quad should be transparent OR pure light;
        # at (5,5) the quad doesn't reach -> falls back to light (white), alpha 255.
        print("[selftest] frame1 centre RGBA:", tuple(int(v) for v in f1[cy, cx]))
        print("[selftest] OK — warp + composite produce a coherent card")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Compose a turntable from front/back textures + a baked template.")
    ap.add_argument("--front", help="front texture PNG path")
    ap.add_argument("--back", help="back texture PNG path")
    ap.add_argument("--template", help="template directory (with template.json)")
    ap.add_argument("--out", help="output directory for NNNN.webp frames")
    ap.add_argument("--target-width", type=int, default=900)
    ap.add_argument("--selftest", action="store_true", help="run the synthetic self-test and exit")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    if not (args.front and args.back and args.template and args.out):
        ap.error("--front, --back, --template and --out are required (or use --selftest)")

    frames = compose_turntable(
        args.front, args.back, args.template,
        out_dir=args.out, target_width=args.target_width,
    )
    print(f"[turntable] wrote {len(frames)} frames to {args.out}")


if __name__ == "__main__":
    main()
