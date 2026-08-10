"""blendertk TextureBaker feature test — the generic Cycles bake-to-texture primitive
(mirror of mayatk's ``mat_utils.texture_baker``). Real headless bakes; the lightmap *workflow*
that composes this is covered by ``test_lightmap_baker.py``.

Run: blender --background --factory-startup --python blendertk/test/test_texture_baker.py
"""
import sys
import os
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from blendertk.mat_utils.texture_baker import TextureBaker

    check("btk.TextureBaker resolves from mat_utils.texture_baker", btk.TextureBaker is TextureBaker)

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    tmp = tempfile.mkdtemp(prefix="btk_texbake_")

    # ---- a minimal lit scene -------------------------------------------------
    reset()
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 5))
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "BakeCube"
    bpy.ops.object.editmode_toggle()
    bpy.ops.uv.smart_project()
    bpy.ops.object.editmode_toggle()

    baker = TextureBaker(resolution=32, samples=1)

    # ---- COMBINED bake (mayatk-parity default) ------------------------------
    out = baker.bake([cube], output_dir=tmp, prefix="C_")
    p = out.get("BakeCube")
    check("COMBINED bake returns {name: path}", bool(p), f"{out}")
    check("COMBINED bake wrote a non-empty EXR with the prefix",
          bool(p) and os.path.isfile(p) and os.path.getsize(p) > 0 and os.path.basename(p) == "C_BakeCube.exr",
          f"{p}")

    # ---- DIFFUSE lighting-only bake (no albedo) -----------------------------
    out2 = baker.bake([cube], output_dir=tmp, bake_type="DIFFUSE",
                      pass_filter={"DIRECT", "INDIRECT"}, use_pass_color=False, suffix="_irr")
    p2 = out2.get("BakeCube")
    check("DIFFUSE lighting-only bake wrote a file with the suffix",
          bool(p2) and os.path.isfile(p2) and os.path.basename(p2) == "BakeCube_irr.exr", f"{p2}")

    # ---- explicit stem overrides the object name ----------------------------
    out3 = baker.bake([cube], output_dir=tmp, stem="CustomStem")
    check("stem overrides the output base name",
          os.path.basename(out3.get("BakeCube", "")) == "CustomStem.exr", f"{out3}")

    # ---- per-object output size (the atlas-footprint bake) ------------------
    # Bake cost is linear in pixels, so a caller that knows an object will only occupy
    # part of an atlas bakes it at that footprint. A partial map must stay safe: an
    # object with no entry falls back to the square resolution, never to a 1px map.
    def exr_size(path):
        img = bpy.data.images.load(path)
        try:
            return tuple(img.size)
        finally:
            bpy.data.images.remove(img)

    sized = baker.bake([cube], output_dir=tmp, stem="Sized", size={"BakeCube": (16, 8)})
    check("size dict sets the baked map's dimensions",
          exr_size(sized.get("BakeCube", "")) == (16, 8), f"{sized}")
    scalar = baker.bake([cube], output_dir=tmp, stem="Scalar", size=8)
    check("a scalar size means a square map", exr_size(scalar.get("BakeCube", "")) == (8, 8))
    fallback = baker.bake([cube], output_dir=tmp, stem="Fallback", size={"Absent": (4, 4)})
    check("an object missing from the size map falls back to the resolution",
          exr_size(fallback.get("BakeCube", "")) == (32, 32))

    # ---- temp bake nodes are cleaned up (non-destructive) -------------------
    mat = cube.material_slots[0].material if cube.material_slots else None
    tex_nodes = sum(1 for n in mat.node_tree.nodes if n.type == "TEX_IMAGE") if mat else 0
    check("bake leaves no leftover image-texture nodes", tex_nodes == 0, f"{tex_nodes}")
    check("bake removes its temp image datablock", "C_BakeCube" not in bpy.data.images)

    # ---- scene state restored after the bake --------------------------------
    reset()
    bpy.ops.object.light_add(type="SUN")
    bpy.ops.mesh.primitive_cube_add()
    c2 = bpy.context.active_object
    bpy.ops.object.editmode_toggle(); bpy.ops.uv.smart_project(); bpy.ops.object.editmode_toggle()
    bpy.context.scene.render.engine = "BLENDER_EEVEE"  # a non-Cycles engine to prove restore
    prior_engine = bpy.context.scene.render.engine
    prior_margin = bpy.context.scene.render.bake.margin
    prior_persistent = bpy.context.scene.render.use_persistent_data
    # Opposite of what this baker will set (denoise=True), so the check can actually fail.
    bpy.context.scene.cycles.use_denoising = False
    prior_denoise = bpy.context.scene.cycles.use_denoising
    baker.bake([c2], output_dir=tmp)
    check("bake restores the render engine", bpy.context.scene.render.engine == prior_engine,
          f"{bpy.context.scene.render.engine} vs {prior_engine}")
    check("bake restores bake.margin", bpy.context.scene.render.bake.margin == prior_margin)
    # The batch reuses Cycles' exported scene + BVH across objects instead of rebuilding it
    # per bake, but that is the baker's business -- the user's scene must come back as it was.
    check("bake restores use_persistent_data",
          bpy.context.scene.render.use_persistent_data == prior_persistent)
    check("bake restores cycles.use_denoising",
          bpy.context.scene.cycles.use_denoising == prior_denoise,
          f"{bpy.context.scene.cycles.use_denoising} vs {prior_denoise}")

    # ---- nothing to bake -> {} ----------------------------------------------
    reset()
    check("empty selection -> {}", baker.bake([]) == {})

    # ---- default_output_dir is generic + parameterized ----------------------
    d = TextureBaker.default_output_dir()
    check("default_output_dir defaults to baked_textures", d.endswith("baked_textures"), d)
    check("default_output_dir takes a subdir",
          TextureBaker.default_output_dir("baked_lighting").endswith("baked_lighting"))

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
