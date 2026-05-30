"""
Host wrapper for the card-render pipeline. Three modes:

  1) Per-card Blender render (legacy, slow):
       python card_render/render_card.py --card1 f.png --card2 b.png [--webp]
     Runs Blender with the texture swap + full 360-frame turntable render.

  2) Bake a texture-independent template (one-time, slow):
       python card_render/render_card.py --bake-template [--template-out DIR]
     Runs Blender with bake_turntable_template.py to produce a shared
     light_NNNN.png sequence + template.json. The template is reused for every
     card, so this only runs when the camera/lights/.blend change.

  3) Compose a turntable from a baked template (per-card, fast, no Blender):
       python card_render/render_card.py --compose \\
         --template card_render/template \\
         --card1 front.png --card2 back.png [--webp]
     Warps the two textures into each baked frame's quad and multiplies by the
     baked shading layer. Pure CPU, runs in seconds.

Mode 3 is the production path: bake once, compose per mint.

Blender executable resolution order:
  1. --blender CLI arg
  2. BLENDER_EXE environment variable
  3. common Steam / standard install locations
  4. "blender" on PATH
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_BLEND = os.path.join(HERE, "Карточка.blend")
DEFAULT_PNG_OUT = os.path.join(HERE, "PNG")
DEFAULT_TEMPLATE_DIR = os.path.join(HERE, "template")
SWAP_SCRIPT = os.path.join(HERE, "blender_swap_and_render.py")
BAKE_SCRIPT = os.path.join(HERE, "bake_turntable_template.py")

BLENDER_CANDIDATES = [
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
]


def find_blender(explicit):
    if explicit:
        return explicit
    if os.environ.get("BLENDER_EXE"):
        return os.environ["BLENDER_EXE"]
    for c in BLENDER_CANDIDATES:
        if os.path.isfile(c):
            return c
    # last resort: glob any versioned install
    for base in (
        r"C:\Program Files\Blender Foundation",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender",
    ):
        hits = glob.glob(os.path.join(base, "**", "blender.exe"), recursive=True)
        if hits:
            return hits[0]
    return shutil.which("blender") or "blender"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card1", help="new image for card texture 1 / front")
    ap.add_argument("--card2", help="new image for card texture 2 / back")
    ap.add_argument("--blend", default=DEFAULT_BLEND)
    ap.add_argument("--out", default=DEFAULT_PNG_OUT, help="output dir")
    ap.add_argument("--blender", help="path to blender.exe")
    ap.add_argument("--tex1", default="Карточка 1.jpeg")
    ap.add_argument("--tex2", default="Карточа 2.jpeg")  # freelancer's typo
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--step", type=int,
                    help="frame step; 10 turns 360 frames into a 36-frame turntable")
    ap.add_argument("--res-percent", type=int,
                    help="render resolution %% (200 = 2x; card region gets more pixels)")
    ap.add_argument("--list", action="store_true",
                    help="print image datablocks in the .blend and exit")
    ap.add_argument("--webp", nargs="?", const="default",
                    help="also convert PNG/composited output to WebP "
                         "(optionally pass a target dir; defaults to "
                         "public/pack/card-frames)")
    # ── Template baking (mode 2) ────────────────────────────────────────────
    ap.add_argument("--bake-template", action="store_true",
                    help="bake a texture-independent template instead of "
                         "rendering this card (runs Blender once)")
    ap.add_argument("--template-out", default=DEFAULT_TEMPLATE_DIR,
                    help="template output dir for --bake-template "
                         "(default: card_render/template)")
    # ── Compose mode (mode 3, no Blender) ───────────────────────────────────
    ap.add_argument("--compose", action="store_true",
                    help="compose a turntable from a baked template + "
                         "--card1/--card2 textures (no Blender)")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE_DIR,
                    help="template dir for --compose (default: card_render/template)")
    ap.add_argument("--target-width", type=int, default=900,
                    help="composite at this width (default 900)")
    ap.add_argument("--workers", type=int,
                    help="thread pool size for --compose "
                         "(default: min(8, cpu_count); pass 1 for serial)")
    ap.add_argument("--mirror-back", dest="mirror_back",
                    action="store_true", default=None,
                    help="force --compose to mirror the back texture "
                         "horizontally (overrides template.json)")
    ap.add_argument("--no-mirror-back", dest="mirror_back",
                    action="store_false",
                    help="force --compose to NOT mirror the back texture "
                         "(use when the supplied back image is already laid "
                         "out for the back UV)")
    args = ap.parse_args()

    if args.compose:
        return run_compose(args)
    if args.bake_template:
        return run_bake(args)
    return run_per_card_render(args)


def run_per_card_render(args):
    blender = _require_blender(args)
    if not os.path.isfile(args.blend):
        sys.exit(f".blend not found: {args.blend}")

    cmd = [blender, "-b", args.blend, "-P", SWAP_SCRIPT, "--"]
    if args.list:
        cmd.append("--list")
    else:
        if args.card1:
            cmd += ["--card1", os.path.abspath(args.card1)]
        if args.card2:
            cmd += ["--card2", os.path.abspath(args.card2)]
        cmd += ["--out", os.path.abspath(args.out)]
        cmd += ["--tex1", args.tex1, "--tex2", args.tex2]
        if args.start is not None:
            cmd += ["--start", str(args.start)]
        if args.end is not None:
            cmd += ["--end", str(args.end)]
        if args.step is not None:
            cmd += ["--step", str(args.step)]
        if args.res_percent is not None:
            cmd += ["--res-percent", str(args.res_percent)]

    print("running:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"Blender exited with code {result.returncode}")

    if args.list:
        return
    if args.webp is not None:
        target = None if args.webp == "default" else args.webp
        convert_webp(args.out, target)


def run_bake(args):
    blender = _require_blender(args)
    if not os.path.isfile(args.blend):
        sys.exit(f".blend not found: {args.blend}")
    if not os.path.isfile(BAKE_SCRIPT):
        sys.exit(f"bake script missing: {BAKE_SCRIPT}")

    template_dir = os.path.abspath(args.template_out)
    os.makedirs(template_dir, exist_ok=True)

    cmd = [blender, "-b", args.blend, "-P", BAKE_SCRIPT, "--",
           "--out", template_dir]
    if args.start is not None:
        cmd += ["--start", str(args.start)]
    if args.end is not None:
        cmd += ["--end", str(args.end)]
    if args.step is not None:
        cmd += ["--step", str(args.step)]
    if args.res_percent is not None:
        cmd += ["--res-percent", str(args.res_percent)]

    print("baking template:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"Blender exited with code {result.returncode}")
    print(f"[bake] template ready at {template_dir}")


def run_compose(args):
    if not args.card1 or not args.card2:
        sys.exit("--compose requires --card1 (front) and --card2 (back)")
    template_dir = os.path.abspath(args.template)
    if not os.path.isfile(os.path.join(template_dir, "template.json")):
        sys.exit(
            f"template.json not found in {template_dir}. "
            "Run --bake-template first."
        )

    # Import the shared compositor; it lives under scripts/ so add repo root.
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from scripts.cardgen.turntable_compose import compose_turntable

    webp_target = None
    if args.webp is not None:
        # By default land directly in the frontend frame dir so the player
        # picks the new turntable up on reload.
        webp_target = (
            None if args.webp == "default"
            else os.path.abspath(args.webp)
        )
        if webp_target is None:
            webp_target = os.path.normpath(
                os.path.join(REPO_ROOT, "user_web_frontend",
                             "public", "pack", "card-frames")
            )
    out_dir = webp_target or os.path.abspath(args.out)

    print(f"[compose] template={template_dir}")
    print(f"[compose] front={args.card1} back={args.card2}")
    print(f"[compose] -> {out_dir}")

    t0 = time.perf_counter()
    frames = compose_turntable(
        os.path.abspath(args.card1),
        os.path.abspath(args.card2),
        template_dir,
        out_dir=out_dir,
        target_width=args.target_width,
        workers=args.workers,
        mirror_back=args.mirror_back,
    )
    elapsed = time.perf_counter() - t0
    per_frame_ms = (elapsed / len(frames) * 1000) if frames else 0.0
    print(
        f"[compose] wrote {len(frames)} webp frames in {elapsed:.2f}s "
        f"({per_frame_ms:.1f} ms/frame)"
    )


def _require_blender(args):
    blender = find_blender(args.blender)
    if not (os.path.isfile(blender) or shutil.which(blender)):
        sys.exit(
            f"Blender not found ({blender}). Install it, then set BLENDER_EXE "
            f"or pass --blender PATH."
        )
    return blender


def convert_webp(png_dir, target_dir):
    """Convert rendered PNGs to WebP. Defers to the frontend converter so the
    frame format/quality matches the pack frames."""
    if target_dir is None:
        target_dir = os.path.normpath(
            os.path.join(HERE, "..", "user_web_frontend",
                         "public", "pack", "card-frames")
        )
    os.makedirs(target_dir, exist_ok=True)
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print(
            "[webp] Pillow not available; skipping. PNGs are in:", png_dir,
            "\n        Install Pillow (pip install pillow) or convert via the "
            "frontend sharp script."
        )
        return
    pngs = sorted(glob.glob(os.path.join(png_dir, "*.png")))
    imgs = [Image.open(p).convert("RGBA") for p in pngs]

    # The card occupies only the centre of each frame with large transparent
    # margins. Compute ONE bounding box (union of every frame's alpha bbox) so
    # the crop is identical across the turntable — the card stays put while it
    # spins, just bigger and sharper.
    union = None
    for im in imgs:
        bbox = im.getchannel("A").getbbox()
        if bbox is None:
            continue
        if union is None:
            union = list(bbox)
        else:
            union[0] = min(union[0], bbox[0])
            union[1] = min(union[1], bbox[1])
            union[2] = max(union[2], bbox[2])
            union[3] = max(union[3], bbox[3])

    if union is not None:
        pad = 12
        fw, fh = imgs[0].size
        union[0] = max(0, union[0] - pad)
        union[1] = max(0, union[1] - pad)
        union[2] = min(fw, union[2] + pad)
        union[3] = min(fh, union[3] + pad)
        crop = tuple(union)
        print(f"[webp] crop box {crop} from {imgs[0].size}")
    else:
        crop = None

    target_w = 900  # width AFTER crop; card fills the frame so this stays sharp
    for i, im in enumerate(imgs, 1):
        if crop:
            im = im.crop(crop)
        if im.width > target_w:
            im = im.resize(
                (target_w, round(im.height * target_w / im.width)),
                Image.LANCZOS,
            )
        out = os.path.join(target_dir, f"{i:04d}.webp")
        im.save(out, "WEBP", quality=92, method=6)
    print(f"[webp] {len(imgs)} PNG -> WebP in {target_dir}")


if __name__ == "__main__":
    main()
