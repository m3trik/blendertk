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
    # Measured: bpy.ops.object.bake creates and frees its Cycles session per object
    # whatever this flag says, so the baker no longer pins it -- and must not disturb it.
    check("bake leaves use_persistent_data alone",
          bpy.context.scene.render.use_persistent_data == prior_persistent)
    check("bake restores cycles.use_denoising",
          bpy.context.scene.cycles.use_denoising == prior_denoise,
          f"{bpy.context.scene.cycles.use_denoising} vs {prior_denoise}")

    # ---- the user's selection survives the batch -----------------------------
    # _bake_one makes each target the sole selected+active object, so without a
    # restore a batch leaves ONLY the last one selected -- and the panel's Revert
    # to Source acts on "the selection", i.e. one object of the N just baked.
    reset()
    bpy.ops.object.light_add(type="SUN")
    cubes = []
    for i in range(3):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        c = bpy.context.active_object
        c.name = f"SelCube{i}"
        bpy.ops.object.editmode_toggle(); bpy.ops.uv.smart_project(); bpy.ops.object.editmode_toggle()
        cubes.append(c)
    bpy.ops.object.select_all(action="DESELECT")
    for c in cubes[:2]:
        c.select_set(True)
    bpy.context.view_layer.objects.active = cubes[0]
    baker.bake(cubes, output_dir=tmp, prefix="Sel_")
    selected_after = {o.name for o in bpy.context.selected_objects}
    check("bake restores the user's selection",
          selected_after == {"SelCube0", "SelCube1"}, f"{sorted(selected_after)}")
    check("bake restores the active object",
          bpy.context.view_layer.objects.active is cubes[0],
          f"{getattr(bpy.context.view_layer.objects.active, 'name', None)}")

    # ---- a bare object gets no permanent material ----------------------------
    # _ensure_materials invents one so Cycles has a node tree to bake through; the
    # workflow's whole claim is that it changes nothing about the material, so the
    # stand-in must not outlive the bake (and must not accumulate one per re-bake).
    reset()
    bpy.ops.object.light_add(type="SUN")
    bpy.ops.mesh.primitive_cube_add()
    bare = bpy.context.active_object
    bare.name = "BareCube"
    bpy.ops.object.editmode_toggle(); bpy.ops.uv.smart_project(); bpy.ops.object.editmode_toggle()
    bare.data.materials.clear()
    mats_before = len(bpy.data.materials)
    out_bare = baker.bake([bare], output_dir=tmp, prefix="Bare_")
    check("a bare object still bakes", bool(out_bare.get("BareCube")), f"{out_bare}")
    check("the invented bake material does not outlive the bake",
          len(bare.material_slots) == 0 and len(bpy.data.materials) == mats_before,
          f"slots={len(bare.material_slots)} mats={len(bpy.data.materials)}/{mats_before}")

    # ---- denoise_images: one session, N maps --------------------------------
    # denoise_image is the single-path convenience; the batch form is what bake()
    # uses so a run of N maps pays the compositor build and the engine flip ONCE.
    reset()
    bpy.ops.object.light_add(type="SUN")
    bpy.ops.mesh.primitive_cube_add()
    dn = bpy.context.active_object
    bpy.ops.object.editmode_toggle(); bpy.ops.uv.smart_project(); bpy.ops.object.editmode_toggle()
    raw = TextureBaker(resolution=32, samples=1, denoise=False).bake(
        [dn], output_dir=tmp, prefix="Dn_"
    )
    paths = list(raw.values())
    prior_engine = bpy.context.scene.render.engine
    done = TextureBaker.denoise_images(paths)
    check("denoise_images denoises every map it is given",
          set(done) == set(paths), f"{done}")
    check("denoise_images restores the render engine",
          bpy.context.scene.render.engine == prior_engine)
    check("denoise_images leaves no compositor node group behind",
          not any(g.name.startswith("btk_denoise") for g in bpy.data.node_groups))
    check("denoise_images tolerates a missing file",
          TextureBaker.denoise_images([os.path.join(tmp, "nope.exr")]) == {})
    check("denoise_images on nothing is a no-op", TextureBaker.denoise_images([]) == {})

    # ---- denoise device pin (GPU OIDN measured 5x the CPU on a 1024 map) -----
    big = TextureBaker.DENOISE_GPU_MIN_TEXELS
    check("denoise device: a large map goes to the GPU when allowed",
          TextureBaker._denoise_device(True, big) == "GPU")
    check("denoise device: a small map stays on the CPU even when the GPU is allowed",
          TextureBaker._denoise_device(True, big - 1) == "CPU")
    check("denoise device: gpu=False pins the CPU at any size",
          TextureBaker._denoise_device(False, 10 * big) == "CPU")
    check("denoise device: gpu=None leaves the scene's setting alone",
          TextureBaker._denoise_device(None, 10 * big) is None)
    attr = TextureBaker._DENOISE_DEVICE_ATTR
    if hasattr(bpy.context.scene.render, attr):
        setattr(bpy.context.scene.render, attr, "CPU")
        done = TextureBaker.denoise_images(paths, gpu=True)
        check("denoise_images(gpu=True) still denoises every map",
              set(done) == set(paths), f"{done}")
        check("denoise_images restores the scene's denoise device",
              getattr(bpy.context.scene.render, attr) == "CPU",
              getattr(bpy.context.scene.render, attr))
        done = TextureBaker.denoise_images(paths, gpu=False)
        check("denoise_images(gpu=False) denoises on the CPU", set(done) == set(paths))
    else:
        check("denoise device pin is skipped where the setting does not exist",
              set(TextureBaker.denoise_images(paths, gpu=True)) == set(paths))

    # ---- AUTO device policy --------------------------------------------------
    auto = TextureBaker(resolution=32, samples=1, denoise=False, device="AUTO")
    auto._gpu_devices = ["probe-gpu"]
    check("AUTO: tiny work bakes on the CPU", auto._choose_device(10) == "CPU")
    check("AUTO: heavy work bakes on the GPU",
          auto._choose_device(TextureBaker.GPU_MIN_WORK) == "GPU")
    auto._gpu_devices = []
    check("AUTO: no compute device -> CPU whatever the work",
          auto._choose_device(10**9) == "CPU")
    prior_device = bpy.context.scene.cycles.device
    cycles_prefs = bpy.context.preferences.addons["cycles"].preferences
    prior_backend = cycles_prefs.compute_device_type
    out_auto = auto.bake([dn], output_dir=tmp, prefix="Auto_")
    check("an AUTO bake produces a map", bool(out_auto.get(dn.name)), f"{out_auto}")
    check("an AUTO bake restores scene.cycles.device",
          bpy.context.scene.cycles.device == prior_device)
    # A 32x32x1 bake is CPU work, which parks the compute preference on NONE for
    # its session; the backend must come back enabled once the bake is over -- and
    # on a machine with no compute device the probe must leave the preference as
    # it found it rather than on the last backend it tried.
    check("an AUTO bake hands the compute backend back as it should be",
          cycles_prefs.compute_device_type == (auto._gpu_backend or prior_backend),
          f"{cycles_prefs.compute_device_type} vs {auto._gpu_backend!r}/{prior_backend}")
    # A later CPU bake on the same instance must not inherit that backend.
    cpu_after = TextureBaker(resolution=32, samples=1, denoise=False, device="AUTO")
    cpu_after._gpu_backend = "STALE"
    cpu_after.device = "CPU"
    cpu_after.bake([dn], output_dir=tmp, prefix="CpuAfter_")
    check("a CPU bake clears a backend left by an earlier AUTO run",
          cpu_after._gpu_backend == "")
    bpy.context.scene.cycles.device = "GPU"
    TextureBaker(resolution=32, samples=1, device="CPU")._apply_device(10)
    check("_apply_device is a no-op unless the policy is AUTO",
          bpy.context.scene.cycles.device == "GPU")
    bpy.context.scene.cycles.device = prior_device

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
