"""
Automates the freelancer's manual Blender workflow:
  1. swap the two card textures
  2. render the 36-frame card-reveal sequence (headless Blender)
  3. (optional) convert the rendered PNGs to WebP for the frontend

Usage:
  python card_render/render_card.py --card1 front.png --card2 back.png
  python card_render/render_card.py --card1 f.png --card2 b.png --webp
  python card_render/render_card.py --list      # print image datablocks in the .blend

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

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BLEND = os.path.join(HERE, "Карточка.blend")
DEFAULT_PNG_OUT = os.path.join(HERE, "PNG")
SWAP_SCRIPT = os.path.join(HERE, "blender_swap_and_render.py")

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
    ap.add_argument("--card1", help="new image for card texture 1")
    ap.add_argument("--card2", help="new image for card texture 2")
    ap.add_argument("--blend", default=DEFAULT_BLEND)
    ap.add_argument("--out", default=DEFAULT_PNG_OUT, help="PNG output dir")
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
                    help="also convert PNG output to WebP "
                         "(optionally pass a target dir)")
    args = ap.parse_args()

    blender = find_blender(args.blender)
    if not (os.path.isfile(blender) or shutil.which(blender)):
        sys.exit(
            f"Blender not found ({blender}). Install it, then set BLENDER_EXE "
            f"or pass --blender PATH."
        )
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
