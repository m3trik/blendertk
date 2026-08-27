"""blendertk Game Shader headless test: the masked (alpha cutout) build, twin of
mayatk's StingrayPBS masked graph.
Run: blender --background --factory-startup --python blendertk/test/test_game_shader.py
"""

import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


TMP = os.path.join(HERE, "temp_tests", "game_shader_masked")
try:
    import bpy
    from blendertk.mat_utils._mat_utils import MatUtils

    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)

    def write_png(name, rgba_at):
        """8x8 PNG via bpy (no Pillow in Blender's Python)."""
        path = os.path.join(TMP, name)
        img = bpy.data.images.new(name, 8, 8, alpha=True)
        px = []
        for y in range(8):
            for x in range(8):
                px.extend(rgba_at(x, y))
        img.pixels = px
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        return path

    base = write_png("model_Base_Color.png", lambda x, y: (0.8, 0.2, 0.2, 1.0))
    # Grayscale opacity: one dark texel, the rest opaque.
    opacity = write_png(
        "model_Opacity.png",
        lambda x, y: (0.1, 0.1, 0.1, 1.0) if (x, y) == (0, 0) else (1.0, 1.0, 1.0, 1.0),
    )

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def alpha_link(mat):
        links = principled(mat).inputs["Alpha"].links
        return links[0].from_node if links else None

    # --- masked: alpha thresholded to 0/1 in the graph -------------------------
    masked = MatUtils.create_pbr_material(
        [base, opacity], name="gs_masked", config={"opacity_mode": "masked"}
    )
    src = alpha_link(masked)
    check("masked: Alpha is driven", src is not None)
    check(
        "masked: Alpha comes through a GREATER_THAN math node",
        src is not None and src.type == "MATH" and src.operation == "GREATER_THAN",
        f"type={getattr(src, 'type', None)} op={getattr(src, 'operation', None)}",
    )
    check(
        "masked: the threshold node is labelled and defaults to 0.5",
        src is not None
        and src.label == "Mask Threshold"
        and abs(src.inputs[1].default_value - 0.5) < 1e-6,
    )
    upstream = src.inputs[0].links[0].from_node if src and src.inputs[0].links else None
    check(
        "masked: the opacity image feeds the threshold",
        upstream is not None
        and upstream.type == "TEX_IMAGE"
        and os.path.basename(upstream.image.filepath) == "model_Opacity.png",
        f"upstream={getattr(upstream, 'type', None)}",
    )
    # Blender < 4.2 keeps CLIP as its own mode; 4.2+ folds CLIP and HASHED into
    # the dithered surface method (both render in the opaque pass -- the cutout
    # semantics), and reads the legacy enum back as HASHED.
    if hasattr(masked, "surface_render_method"):
        check(
            "masked: dithered surface method (opaque pass), never blended",
            masked.surface_render_method == "DITHERED",
            masked.surface_render_method,
        )
    elif hasattr(masked, "blend_method"):
        check(
            "masked: blend_method is CLIP (or its 4.2+ alias HASHED)",
            masked.blend_method in ("CLIP", "HASHED"),
            masked.blend_method,
        )

    # --- auto (default): alpha straight from the image, blended ----------------
    auto = MatUtils.create_pbr_material([base, opacity], name="gs_auto")
    src = alpha_link(auto)
    check(
        "auto: Alpha comes straight from the opacity image",
        src is not None and src.type == "TEX_IMAGE",
        f"type={getattr(src, 'type', None)}",
    )

    # --- none: opacity ruled out; the alpha stays unwired ----------------------
    # An assertion, not an absence: the set carries a usable alpha and the
    # caller is asking for the solid material anyway (a cutout the target
    # engine masks with its own material, a decal sheet reused as a body).
    ignored = MatUtils.create_pbr_material(
        [base, opacity], name="gs_none", config={"opacity_mode": "none"}
    )
    check(
        "none: Alpha is left unwired even though the set carries one",
        alpha_link(ignored) is None,
        f"driver={getattr(alpha_link(ignored), 'type', None)}",
    )
    check(
        "none: the base colour is still wired",
        bool(principled(ignored).inputs["Base Color"].links),
    )
    check(
        "none: no image node was loaded for the opacity map",
        not any(
            n.type == "TEX_IMAGE"
            and os.path.basename(n.image.filepath) == "model_Opacity.png"
            for n in ignored.node_tree.nodes
        ),
    )

    from blendertk.mat_utils.game_shader import GameShader as _GS

    check(
        "none: the report names the reason instead of blaming another map",
        _GS._shadowed_by("Opacity", {"Base_Color": base}, {"opacity_mode": "none"})
        == "opacity ruled out (Opacity: None)",
        _GS._shadowed_by("Opacity", {"Base_Color": base}, {"opacity_mode": "none"}),
    )
    check(
        "auto: an unconnected map still reports the map that took its input",
        "already drives" in _GS._shadowed_by("Height", {"Normal": base}, None),
        _GS._shadowed_by("Height", {"Normal": base}, None),
    )

    # --- the panel path: opacity_mode must reach the build through create_network
    import blendertk.mat_utils._mat_utils as mat_utils
    from blendertk.mat_utils.game_shader import GameShader

    seen = []
    orig_create = mat_utils.MatUtils.create_pbr_material

    def spy_create(files, name=None, **kw):
        seen.append(kw.get("config") or {})
        return orig_create(files, name=name, **kw)

    mat_utils.MatUtils.create_pbr_material = spy_create
    try:
        built = GameShader().create_network(
            [base, opacity], name="gs_seam", opacity_mode="masked"
        )
    finally:
        mat_utils.MatUtils.create_pbr_material = orig_create
    check(
        "create_network hands opacity_mode to create_pbr_material",
        bool(seen) and seen[-1].get("opacity_mode") == "masked",
        f"config keys={sorted(seen[-1]) if seen else None}",
    )
    seam_mat = (
        built if hasattr(built, "node_tree") else bpy.data.materials.get("gs_seam")
    )
    src = alpha_link(seam_mat) if seam_mat else None
    check(
        "panel-path masked build thresholds the alpha",
        src is not None and src.type == "MATH" and src.operation == "GREATER_THAN",
        f"type={getattr(src, 'type', None)}",
    )

except Exception as e:
    traceback.print_exc()
    check("game shader masked build raised", False, repr(e))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
