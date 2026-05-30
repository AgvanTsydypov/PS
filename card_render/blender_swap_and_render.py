"""
Runs INSIDE Blender (headless):  blender -b Карточка.blend -P blender_swap_and_render.py -- <args>

Swaps the two card textures and renders the 36-frame sequence.

Args after `--`:
  --card1 PATH       new image for the first card texture
  --card2 PATH       new image for the second card texture
  --out   DIR        output directory for rendered PNG frames
  --tex1  NAME       datablock/image name of texture 1 (default: "Карточка 1")
  --tex2  NAME       datablock/image name of texture 2 (default: "Карточка 2")
  --start N          first frame (default: from .blend)
  --end   N          last frame  (default: from .blend)

NOTE: --tex1/--tex2 default to the names the freelancer used. If the image
datablocks inside the .blend are named differently, pass the real names
(use --list to print all image datablocks and exit).
"""
import bpy
import sys
import os


def get_args():
    argv = sys.argv
    return argv[argv.index("--") + 1 :] if "--" in argv else []


def parse(args):
    out = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--list":
            out["list"] = True
            i += 1
            continue
        if a.startswith("--"):
            out[a[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


def list_images():
    print("=== image datablocks in .blend ===")
    for img in bpy.data.images:
        print(f"  name={img.name!r:30} source={img.source:10} file={img.filepath}")
    print("=== end ===")


def swap_image(name, new_path):
    img = bpy.data.images.get(name)
    if img is None:
        # Fallback: match by case-insensitive prefix
        for cand in bpy.data.images:
            if cand.name.lower().startswith(name.lower()):
                img = cand
                break
    if img is None:
        raise SystemExit(
            f"[swap] image datablock {name!r} not found. "
            f"Run with --list to see available names."
        )
    new_path = os.path.abspath(new_path)
    if not os.path.isfile(new_path):
        raise SystemExit(f"[swap] new texture file not found: {new_path}")
    img.filepath = new_path
    img.source = "FILE"
    img.reload()
    print(f"[swap] {img.name!r} -> {new_path}")


def main():
    opts = parse(get_args())

    if opts.get("list"):
        list_images()
        return

    tex1 = opts.get("tex1", "Карточка 1")
    tex2 = opts.get("tex2", "Карточка 2")

    if "card1" in opts:
        swap_image(tex1, opts["card1"])
    if "card2" in opts:
        swap_image(tex2, opts["card2"])

    scene = bpy.context.scene
    if "start" in opts:
        scene.frame_start = int(opts["start"])
    if "end" in opts:
        scene.frame_end = int(opts["end"])
    if "step" in opts:
        scene.frame_step = int(opts["step"])
    if "res-percent" in opts:
        scene.render.resolution_percentage = int(opts["res-percent"])

    if "out" in opts:
        out_dir = os.path.abspath(opts["out"])
        os.makedirs(out_dir, exist_ok=True)
        # frame_#### naming (Blender replaces #### with the frame number)
        scene.render.filepath = os.path.join(out_dir, "frame_")
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"  # keep alpha
        scene.render.film_transparent = True

    print(
        f"[render] frames {scene.frame_start}-{scene.frame_end} -> {scene.render.filepath}"
    )
    bpy.ops.render.render(animation=True)
    print("[render] done")


main()
