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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")


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

    # ---- default_output_dir: the file project's texture folder + a kind -----
    # One rule in both DCCs (EnvUtils.scene_source_images_dir); never temp: an
    # unsaved file with no workspace has nowhere permanent and says so.
    from blendertk.env_utils._env_utils import EnvUtils as _Env

    _pin = _Env.workspace_root() or None
    _Env.set_current_workspace(None)
    try:
        TextureBaker.default_output_dir()
        check("an unsaved file with no workspace has no default folder", False)
    except ValueError:
        check("an unsaved file with no workspace has no default folder", True)
    _ws = os.path.join(tmp, "ws")
    os.makedirs(_ws, exist_ok=True)
    _Env.set_current_workspace(_ws)
    try:
        d = TextureBaker.default_output_dir()
        check(
            "default_output_dir is <texture folder>/baked",
            os.path.normcase(d)
            == os.path.normcase(os.path.join(_ws, "sourceimages", "baked")),
            d,
        )
        check(
            "default_output_dir takes a subdir",
            TextureBaker.default_output_dir("lightmaps").endswith("lightmaps"),
        )
    finally:
        _Env.set_current_workspace(_pin)

    # ---- render_panorama: the HDR a reflection probe is ---------------------
    # Mirror of mayatk's TestRenderPanorama. Its layout is a contract with every
    # reader (the GLB's probe, the WebXR page's equirect lookup): u = 0.5 +
    # atan2(z, x) / 2pi, top row +Y, in glTF's Y-up axes -- which Blender's Z-up
    # ones are read as (x, z, -y). Cycles' panorama camera centres its own -Z,
    # so only walls of a known colour each way can see a turn.
    import math as _math

    import numpy as _np

    reset()

    def pano_wall(name, location, rotation, rgb, strength=1.0):
        bpy.ops.mesh.primitive_plane_add(size=4, location=location, rotation=rotation)
        wall = bpy.context.active_object
        wall.name = name
        mat = bpy.data.materials.new(name)
        nt = btk.MatUtils.get_node_tree(mat, create=True)
        nt.nodes.clear()
        emit = nt.nodes.new("ShaderNodeEmission")  # flat: the colour, unlit
        emit.inputs["Color"].default_value = (*rgb, 1.0)
        emit.inputs["Strength"].default_value = strength
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(emit.outputs[0], out.inputs["Surface"])
        wall.data.materials.append(mat)
        return wall

    _quarter = _math.radians(90)
    pano_walls = {
        "pxWall": ((1.2, 0, 0), (0, _quarter, 0), (1.0, 0.0, 0.0)),  # +X red
        "nxWall": ((-1.2, 0, 0), (0, _quarter, 0), (0.0, 0.0, 1.0)),  # -X blue
        "nyWall": ((0, -1.2, 0), (_quarter, 0, 0), (0.0, 1.0, 0.0)),  # glTF +Z green
        "pyWall": ((0, 1.2, 0), (_quarter, 0, 0), (1.0, 1.0, 1.0)),  # glTF -Z white
        "pzWall": ((0, 0, 1.2), (0, 0, 0), (1.0, 1.0, 0.0)),  # up: yellow
    }
    for _name, (_loc, _rot, _rgb) in pano_walls.items():
        pano_wall(_name, _loc, _rot, _rgb)
    pano_dir = os.path.join(tmp, "pano")
    os.makedirs(pano_dir, exist_ok=True)

    def read_exr(path):
        """The EXR top-down, ``(h, w, channels)``."""
        img = bpy.data.images.load(path, check_existing=False)
        try:
            w, h = img.size
            px = _np.empty(w * h * img.channels, dtype=_np.float32)
            img.pixels.foreach_get(px)
            return px.reshape(h, w, img.channels)[::-1]
        finally:
            bpy.data.images.remove(img)

    def render_pano(name="probe.exr", **kwargs):
        path = os.path.join(pano_dir, name)
        out = TextureBaker(samples=1, device="CPU", denoise=False).render_panorama(
            (0, 0, 0), path, width=64, **kwargs
        )
        return out, (read_exr(out) if out and os.path.isfile(out) else None)

    def dominant(img, u, row=16):
        """The strongest channel ('r', 'g', 'b' or 'white') at *u* on the horizon."""
        r, g, b = (float(c) for c in img[row, int(u * img.shape[1]), :3])
        if min(r, g, b) > 0.5 * max(r, g, b):
            return "white"
        return "rgb"[[r, g, b].index(max(r, g, b))]

    out, img = render_pano()
    check(
        "render_panorama writes the path asked for, 2:1 at the width asked for",
        out == os.path.join(pano_dir, "probe.exr")
        and img is not None
        and img.shape[:2] == (32, 64),
        f"{out} {None if img is None else img.shape}",
    )
    if img is not None:
        layout = {
            "+X at the centre": dominant(img, 0.5) == "r",
            "glTF +Z (Blender -Y) a quarter right": dominant(img, 0.75) == "g",
            "glTF -Z (Blender +Y) a quarter left": dominant(img, 0.25) == "white",
            "-X at the seam": dominant(img, 0.02) == "b",
            "+Z (glTF +Y) the top row": float(img[0, 32, 0]) > 0.5
            and float(img[0, 32, 1]) > 0.5
            and float(img[0, 32, 2]) < 0.2,
        }
        check(
            "the panorama is laid out as its readers read it",
            all(layout.values()),
            [k for k, v in layout.items() if not v],
        )
        check(
            "the walls render at their own linear level (no view transform)",
            abs(float(img[16, 32, 0]) - 1.0) < 0.1,
            float(img[16, 32, 0]),
        )

    # What moves is seen through, and nothing in the scene is left changed --
    # the camera, the light's own camera switch, the render settings, the
    # selection.
    blocker = pano_wall("blocker", (0.6, 0, 0), (0, _quarter, 0), (0.0, 0.0, 0.0))
    bpy.ops.object.light_add(type="POINT", location=(0, 0, -1.0))
    pano_light = bpy.context.active_object
    pano_light.visible_camera = False
    scene = bpy.context.scene
    before = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "res": (scene.render.resolution_x, scene.render.resolution_y),
        "format": scene.render.image_settings.file_format,
        "filepath": scene.render.filepath,
        "film_transparent": scene.render.film_transparent,
        "cameras": len(bpy.data.cameras),
        "objects": len(bpy.data.objects),
    }
    for o in list(bpy.context.view_layer.objects):
        o.select_set(o == blocker)
    bpy.context.view_layer.objects.active = blocker
    _, seen = render_pano("seen.exr")
    check(
        "the blocker hides the red wall",
        seen is not None and float(seen[16, 32, 0]) < 0.05,
        None if seen is None else float(seen[16, 32, 0]),
    )
    _, through = render_pano("through.exr", hide=[blocker])
    check(
        "a hidden mesh is seen through",
        through is not None and dominant(through, 0.5) == "r",
    )
    after = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "res": (scene.render.resolution_x, scene.render.resolution_y),
        "format": scene.render.image_settings.file_format,
        "filepath": scene.render.filepath,
        "film_transparent": scene.render.film_transparent,
        "cameras": len(bpy.data.cameras),
        "objects": len(bpy.data.objects),
    }
    check("render_panorama leaves the scene as found", after == before, after)
    check(
        "...the light's camera switch and the blocker's included",
        pano_light.visible_camera is False and blocker.visible_camera is True,
    )
    check(
        "...and the selection",
        [o.name for o in bpy.context.view_layer.objects if o.select_get()]
        == ["blocker"]
        and bpy.context.view_layer.objects.active == blocker,
    )
    bpy.data.objects.remove(blocker, do_unlink=True)

    # A reflection sees a light's own radiance: a light the scene hides from
    # the camera is seen by the probe, and the world too.
    pano_light.data.shadow_soft_size = 0.3
    pano_light.data.energy = 5000.0
    world = bpy.data.worlds.new("pano_world")
    btk.MatUtils.get_node_tree(world, create=True)
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.2, 0.2, 0.9, 1)
    world.cycles_visibility.camera = False
    scene.world = world
    bpy.data.objects.remove(bpy.data.objects["pzWall"], do_unlink=True)
    _, lit = render_pano("lit.exr")
    check(
        "a light hidden from the camera is seen by the probe (below)",
        lit is not None and float(lit[-3:, :, :3].max()) > 5.0,
        None if lit is None else float(lit[-3:, :, :3].max()),
    )
    check(
        "the world hidden from the camera is seen by the probe (above)",
        lit is not None and float(lit[0, :, 2].mean()) > 0.5,
        None if lit is None else float(lit[0, :, 2].mean()),
    )
    check(
        "...and both are handed back hidden",
        pano_light.visible_camera is False
        and world.cycles_visibility.camera is False,
    )

    # The view exposure is a viewing control: it never reaches the probe, as
    # it never reaches a bake's maps.
    scene.view_settings.exposure = 3.0
    _, exposed = render_pano("exposed.exr")
    check(
        "the scene's view exposure never reaches the probe",
        exposed is not None and abs(float(exposed[16, 32, 0]) - 1.0) < 0.1,
        None if exposed is None else float(exposed[16, 32, 0]),
    )
    check("...and is handed back", scene.view_settings.exposure == 3.0)
    scene.view_settings.exposure = 0.0

    # A probe open in a viewer, or held by a sync client: the render is the
    # expensive part, the file name is not -- it lands beside it.
    held = os.path.join(pano_dir, "held.exr")
    with open(held, "wb") as fh:
        fh.write(b"held")
    _held_rendered = None
    with open(held, "rb"):
        _held_rendered = TextureBaker(
            samples=1, device="CPU", denoise=False
        ).render_panorama((0, 0, 0), held, width=64)
    check(
        "a held probe file costs its name, not the render",
        _held_rendered is not None
        and os.path.basename(_held_rendered) == "held_1.exr"
        and os.path.isfile(_held_rendered),
        _held_rendered,
    )
    with open(held, "rb") as fh:
        check("...and the held file is untouched", fh.read() == b"held")

    # A scene saved with Video (or Multi-Layer EXR) output: Blender 5.x offers
    # only the file formats its media type allows, so OPEN_EXR was refused and
    # the probe lost -- the trap the bake's own EXR writers already pin around.
    _fmt = scene.render.image_settings
    if hasattr(_fmt, "media_type"):
        _fmt_prior = (_fmt.media_type, _fmt.file_format)
        _fmt.media_type, _fmt.file_format = "VIDEO", "FFMPEG"
        try:
            _video_out, _video_img = render_pano("video_output.exr")
            _video_error = None
        except Exception as e:  # noqa: BLE001 -- the failure is the finding
            _video_out, _video_img, _video_error = None, None, e
        check(
            "a scene saved with Video output still renders its panorama",
            _video_img is not None and _video_img.shape[:2] == (32, 64),
            _video_error or _video_out,
        )
        check(
            "...and its output format is handed back",
            (_fmt.media_type, _fmt.file_format) == ("VIDEO", "FFMPEG"),
            (_fmt.media_type, _fmt.file_format),
        )
        _fmt.media_type, _fmt.file_format = _fmt_prior
    else:
        lines.append("OK   (skipped) video output scene: no media_type before 5.0")
    reset()

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
