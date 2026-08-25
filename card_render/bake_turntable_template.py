"""
Runs INSIDE Blender (headless), ONCE, to bake a texture-independent turntable
template that the per-mint compositor (scripts/cardgen/turntable_compose.py)
reuses for every card:

    blender -b Карточка.blend -P bake_turntable_template.py -- --out TEMPLATE_DIR

What it produces in TEMPLATE_DIR:
    template.json                       # frame metadata: per-frame card quad + side
    light_0001.png ... light_NNNN.png   # baked shading layer (lights/shadows/foil)

How it works
------------
The card geometry, camera, lights and rotation are identical for every card —
only the two face textures change. So we bake the parts that DON'T depend on the
texture:

  * Shading layer: render the scene with a global white material override, so the
    output RGB is pure lighting/shadow (a multiply layer) and the alpha is the
    card's coverage. The compositor multiplies the warped texture by this.

  * Card quad: for each frame we project the visible face's 4 corners to render
    pixel coordinates. The compositor perspective-warps the flat texture into
    that quad — exact for a flat card.

Args after ``--``:
    --out DIR            output template directory (required)
    --object NAME        card mesh object (default: first MESH with >=2 big quads)
    --front-slot N       material slot index of the FRONT face (default 0)
    --start N            first frame (default: from .blend)
    --end   N            last frame  (default: from .blend)
    --step  N            frame step (e.g. 6 -> 60 frames from 360; default 1)
    --res-percent N      render resolution percentage
    --dry-run            compute quads + write template.json, but DON'T render
                         (fast sanity check of the geometry)
    --list               print mesh objects + material slots and exit

NOTE: this is a one-time tool. Blender is NOT needed at mint time.
"""
import bpy
import json
import math
import os
import sys

from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


# ─────────────────────────────────────────────────────────────────────────────
# Arg parsing (mirrors blender_swap_and_render.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_args():
    argv = sys.argv
    return argv[argv.index("--") + 1:] if "--" in argv else []


def parse(args):
    out = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--dry-run", "--list"):
            out[a[2:]] = True
            i += 1
            continue
        if a.startswith("--"):
            out[a[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Mesh / face helpers
# ─────────────────────────────────────────────────────────────────────────────

def _poly_area(obj, poly):
    return poly.area


def find_card_object(name=None):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"[bake] mesh object {name!r} not found (use --list)")
        return obj
    # Heuristic: the MESH object with the largest single polygon (the card face).
    best, best_area = None, -1.0
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data.polygons:
            continue
        a = max(p.area for p in obj.data.polygons)
        if a > best_area:
            best, best_area = obj, a
    if best is None:
        raise SystemExit("[bake] no mesh objects found (use --list)")
    return best


def two_largest_quads(obj):
    """Return the two largest 4-vertex polygons (front + back faces)."""
    quads = [p for p in obj.data.polygons if len(p.vertices) == 4]
    quads.sort(key=lambda p: p.area, reverse=True)
    if len(quads) < 1:
        raise SystemExit("[bake] card mesh has no quad faces to use as the card face")
    return quads[:2] if len(quads) >= 2 else quads[:1]


def face_world_corners(obj, poly):
    mw = obj.matrix_world
    return [mw @ obj.data.vertices[vi].co for vi in poly.vertices]


def face_world_normal(obj, poly):
    return (obj.matrix_world.to_3x3() @ poly.normal).normalized()


def project_px(scene, cam, world_co, rx, ry):
    co = world_to_camera_view(scene, cam, world_co)
    return (co.x * rx, (1.0 - co.y) * ry)


def order_tl_tr_br_bl(pts):
    """Order 4 screen points as TL, TR, BR, BL."""
    by_y = sorted(pts, key=lambda p: p[1])
    top = sorted(by_y[:2], key=lambda p: p[0])      # TL, TR
    bottom = sorted(by_y[2:], key=lambda p: p[0])    # BL, BR
    return [top[0], top[1], bottom[1], bottom[0]]


# ─────────────────────────────────────────────────────────────────────────────
# White material override (texture-independent shading layer)
# ─────────────────────────────────────────────────────────────────────────────

def make_white_override():
    mat = bpy.data.materials.new("PS_TurntableWhite")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        # Keep some spec so foil/edges still read; tweak in the .blend if needed.
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.4
    return mat


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def list_scene():
    print("=== mesh objects ===")
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            slots = [s.material.name if s.material else None for s in obj.material_slots]
            nquad = sum(1 for p in obj.data.polygons if len(p.vertices) == 4)
            print(f"  {obj.name!r:24} quads={nquad:4} slots={slots}")
    print("=== end ===")


def main():
    opts = parse(get_args())

    if opts.get("list"):
        list_scene()
        return

    out_dir = opts.get("out")
    if not out_dir:
        raise SystemExit("[bake] --out DIR is required")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        raise SystemExit("[bake] scene has no active camera")

    if "start" in opts:
        scene.frame_start = int(opts["start"])
    if "end" in opts:
        scene.frame_end = int(opts["end"])
    step = int(opts.get("step", 1))
    if "res-percent" in opts:
        scene.render.resolution_percentage = int(opts["res-percent"])

    rx = int(scene.render.resolution_x * scene.render.resolution_percentage / 100)
    ry = int(scene.render.resolution_y * scene.render.resolution_percentage / 100)

    obj = find_card_object(opts.get("object"))
    faces = two_largest_quads(obj)
    front_slot = int(opts.get("front-slot", 0))

    def face_side(poly):
        # The face on the FRONT material slot is "front"; the other is "back".
        return "front" if poly.material_index == front_slot else "back"

    print(f"[bake] object={obj.name!r} faces={len(faces)} "
          f"render={rx}x{ry} frames={scene.frame_start}-{scene.frame_end}/{step}")

    dry = bool(opts.get("dry-run"))
    if not dry:
        white = make_white_override()
        scene.view_layers[0].material_override = white
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"

    frames = []
    out_index = 0
    f = scene.frame_start
    while f <= scene.frame_end:
        scene.frame_set(f)
        out_index += 1

        cam_loc = cam.matrix_world.translation
        # Pick the visible face: normal pointing toward the camera.
        visible, best_dot = None, 1.0
        for poly in faces:
            center = obj.matrix_world @ poly.center
            view_dir = (center - cam_loc).normalized()
            d = face_world_normal(obj, poly).dot(view_dir)
            if d < best_dot:  # most negative = most front-facing
                best_dot, visible = d, poly
        if visible is None:
            visible = faces[0]

        corners = [project_px(scene, cam, c, rx, ry)
                   for c in face_world_corners(obj, visible)]
        quad = order_tl_tr_br_bl(corners)

        light_name = f"light_{out_index:04d}.png"
        frames.append({
            "index": out_index,
            "blender_frame": f,
            "side": face_side(visible),
            "quad": [[round(x, 2), round(y, 2)] for (x, y) in quad],
            "light": light_name,
        })

        if not dry:
            scene.render.filepath = os.path.join(out_dir, light_name.replace(".png", ""))
            bpy.ops.render.render(write_still=True)

        f += step

    manifest = {
        "version": 1,
        "frame_count": len(frames),
        "width": rx,
        "height": ry,
        "mirror_back": True,
        "object": obj.name,
        "front_slot": front_slot,
        "frames": frames,
    }
    with open(os.path.join(out_dir, "template.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"[bake] {'(dry-run) ' if dry else ''}wrote template.json with "
          f"{len(frames)} frames -> {out_dir}")


main()
