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

    @property
    def crop_bbox(self) -> Optional[Tuple[int, int, int, int]]:
        """Union alpha bounding box of the card across every baked frame.

        The bake renders the card at the centre of a much larger canvas with
        transparent margins. Cropping to this bbox before downscaling makes the
        card fill the final webp; without it a 1200→900 downscale leaves the
        card occupying ~1/3 of the frame width.

        Returns ``None`` if the manifest doesn't have ``crop_bbox`` cached.
        Use ``ensure_crop_bbox()`` to compute + persist it.
        """
        b = self.manifest.get("crop_bbox")
        return tuple(b) if b else None  # type: ignore[return-value]

    def ensure_crop_bbox(self, pad: int = 12) -> Optional[Tuple[int, int, int, int]]:
        """Return ``crop_bbox`` from the manifest or compute, persist and return it.

        Computation is a one-pass scan of every light frame's alpha (threaded).
        On success the bbox is written back into ``template.json`` so the next
        call is free.
        """
        cached = self.crop_bbox
        if cached is not None:
            return cached
        bbox = _compute_crop_bbox(self, pad=pad)
        if bbox is None:
            return None
        self.manifest["crop_bbox"] = list(bbox)
        try:
            with open(os.path.join(self.root, "template.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(self.manifest, fh, indent=2)
        except OSError:
            pass  # non-fatal — we still return the bbox for this run
        return bbox


# ─────────────────────────────────────────────────────────────────────────────
# Compositing
# ─────────────────────────────────────────────────────────────────────────────

def _quad_corners_px(width: int, height: int) -> List[Tuple[int, int]]:
    """Texture corners in TL, TR, BR, BL pixel order."""
    return [(0, 0), (width, 0), (width, height), (0, height)]


def _compute_crop_bbox(
    template: "TurntableTemplate",
    pad: int = 12,
) -> Optional[Tuple[int, int, int, int]]:
    """Union of every light frame's alpha bbox, padded and clamped to the canvas."""
    import concurrent.futures as cf

    from PIL import Image  # lazy

    def one(frame_meta: Dict[str, Any]):
        path = os.path.join(template.root, frame_meta["light"])
        with Image.open(path) as im:
            return im.convert("RGBA").getchannel("A").getbbox()

    union: Optional[List[int]] = None
    workers = min(8, os.cpu_count() or 4)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for bbox in ex.map(one, template.frames):
            if bbox is None:
                continue
            if union is None:
                union = list(bbox)
            else:
                union[0] = min(union[0], bbox[0])
                union[1] = min(union[1], bbox[1])
                union[2] = max(union[2], bbox[2])
                union[3] = max(union[3], bbox[3])
    if union is None:
        return None
    union[0] = max(0, union[0] - pad)
    union[1] = max(0, union[1] - pad)
    union[2] = min(template.width, union[2] + pad)
    union[3] = min(template.height, union[3] + pad)
    return tuple(union)  # type: ignore[return-value]


def compose_frame(
    front_tex,
    back_tex,
    template: TurntableTemplate,
    frame_meta: Dict[str, Any],
    *,
    out_size: Optional[Tuple[int, int]] = None,
    scale: float = 1.0,
    crop_offset: Tuple[int, int] = (0, 0),
    crop_bbox: Optional[Tuple[int, int, int, int]] = None,
):
    """Compose a single RGBA turntable frame (PIL Image) from the two textures.

    ``crop_bbox`` is the union alpha bbox in bake coords; if given the light
    layer is cropped to it and ``crop_offset = (x0, y0)`` is subtracted from
    every quad coord. ``scale`` is then applied to map into ``out_size`` — so
    we composite at the *cropped, downscaled* resolution end-to-end.
    """
    from PIL import Image  # lazy

    side = frame_meta.get("side", "front")
    tex = front_tex if side == "front" else back_tex
    if side == "back" and template.mirror_back:
        tex = tex.transpose(Image.FLIP_LEFT_RIGHT)

    if out_size is None:
        w, h = template.width, template.height
    else:
        w, h = out_size
    ox, oy = crop_offset
    quad = [((p[0] - ox) * scale, (p[1] - oy) * scale) for p in frame_meta["quad"]]
    src = _quad_corners_px(tex.width, tex.height)
    coeffs = _find_coeffs(quad, src)

    # BILINEAR is ~2× faster than BICUBIC and visually indistinguishable once
    # we composite at the target output resolution (no further downscale).
    warped = tex.transform(
        (w, h), Image.PERSPECTIVE, coeffs, resample=Image.BILINEAR, fillcolor=(0, 0, 0, 0)
    )

    light = template.light_image(frame_meta)
    if crop_bbox is not None:
        light = light.crop(crop_bbox)
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
    webp_method: int = 4,
    workers: Optional[int] = None,
    mirror_back: Optional[bool] = None,
) -> List[Tuple[int, bytes]]:
    """Render the full turntable for one card.

    ``front``/``back`` may be PNG bytes, file paths, or PIL Images. ``template``
    is a ``TurntableTemplate`` or a path to its directory. Returns a list of
    ``(frame_index, webp_bytes)``; if ``out_dir`` is given, also writes
    ``{index:04d}.webp`` files (the naming the frontend already expects).

    Performance notes:

    * ``target_width`` makes us composite **at** that resolution (quad coords
      and light layer scaled once) instead of compositing at the bake
      resolution and LANCZOS-ing down — saves quadratic time on every frame.
    * ``webp_method=4`` is Pillow's default and ~3× faster than ``method=6``
      with barely-visible quality loss at quality 92.
    * ``workers`` runs frames in a thread pool; PIL.transform, numpy math and
      the webp encoder all release the GIL, so threads scale near-linearly.
      Defaults to ``min(8, cpu_count())``; pass ``1`` to force serial.
    """
    import concurrent.futures as cf
    from io import BytesIO

    if isinstance(template, str):
        template = TurntableTemplate.load(template)

    # Caller can override the manifest's mirror_back flag (e.g. when the back
    # texture is already supplied pre-mirrored, our default flip would write
    # text backwards).
    if mirror_back is not None:
        template.mirror_back = mirror_back

    # The bake leaves the card in the centre of a much larger canvas; crop to
    # its union alpha bbox so the downscale to target_width actually fills the
    # frame with card pixels instead of mostly empty margin.
    crop_bbox = template.ensure_crop_bbox()
    if crop_bbox is not None:
        x0, y0, x1, y1 = crop_bbox
        src_w, src_h = x1 - x0, y1 - y0
        crop_offset = (x0, y0)
    else:
        src_w, src_h = template.width, template.height
        crop_offset = (0, 0)

    # Decide working resolution up front: composite directly at target_width
    # so PIL.transform, numpy multiply and webp encode all run on a smaller
    # canvas instead of doing them at bake res and downscaling after.
    if target_width and target_width < src_w:
        scale = target_width / src_w
        out_size = (target_width, round(src_h * scale))
    else:
        scale = 1.0
        out_size = (src_w, src_h)

    front_tex = _load_texture(front)
    back_tex = _load_texture(back)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    def render_one(frame_meta: Dict[str, Any]) -> Tuple[int, bytes]:
        idx = int(frame_meta["index"])
        frame = compose_frame(
            front_tex, back_tex, template, frame_meta,
            out_size=out_size, scale=scale,
            crop_offset=crop_offset, crop_bbox=crop_bbox,
        )
        buf = BytesIO()
        frame.save(buf, "WEBP", quality=webp_quality, method=webp_method)
        data = buf.getvalue()
        if out_dir:
            with open(os.path.join(out_dir, f"{idx:04d}.webp"), "wb") as fh:
                fh.write(data)
        return idx, data

    if workers is None:
        workers = min(8, os.cpu_count() or 4)

    if workers <= 1:
        results = [render_one(fm) for fm in template.frames]
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(render_one, template.frames))

    results.sort(key=lambda r: r[0])
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
