"""blendertk WebXR lightmap-export headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_web_export.py

Covers LightmapWebExport (encode / the record and its media in a native GLB) and the
fixture-light builder on LightUtils. Tiny resolution + samples so the real bake stays fast; the GLB is
parsed with the stdlib so the assertions are about the actual deliverable, not about what
the exporter was asked to do.

Regression anchors (each cost a debugging session to find, all measured on Blender 5.1):
  * an image's colorspace must be set BEFORE its pixels are written, or the buffer is
    discarded and the map saves pure black
  * leaving it unset instead double-applies sRGB
  * Blender's glTF exporter writes every UV layer as a TEXCOORD_n, so a lightmap's second
    UV map ships as TEXCOORD_1 with no texture reading it (5.1.2; the wiring that once made
    sure of it retired with the GLB's lightmaps block, 2026-11-07)
  * Cycles does NOT denoise bakes (`use_denoising` is a render setting, already True by
    default), so denoising has to be a post-pass over the saved EXR
"""

import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def glb_json(path):
    """The GLB's parsed JSON chunk, via pythontk's owner of GLB container parsing.

    Reusing ``MeshConvert.open_glb`` rather than re-deriving the chunk offsets here: it
    already handles the truncated-file cases, and a second parser in the tests is exactly
    how the two end up disagreeing about what a valid GLB is. Read-only — nothing sets
    ``dirty``, so the session closes without writing.
    """
    import pythontk as ptk

    with ptk.MeshConvert.open_glb(path) as edit:
        return edit.gltf


tmp_dir = tempfile.mkdtemp(prefix="btk_web_")
try:
    import bpy
    import numpy as np

    import blendertk as btk
    from blendertk.light_utils._light_utils import LightUtils
    from blendertk.light_utils.lightmap_baker.web_export import LightmapWebExport

    # --- fixture lights from geometry -------------------------------------
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    # A thin ceiling plate above a floor: the shape lights_from_geometry is built to read.
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 3))
    plate = bpy.context.active_object
    plate.name = "LIGHT_plate"
    plate.scale = (2.0, 0.5, 0.02)  # thin in Z -> Z is the emission axis
    bpy.context.view_layer.update()

    bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "floor"
    mat = btk.create_mat("standard", name="floor_mat")
    btk.assign_mat([floor], mat)

    created = LightUtils.lights_from_geometry([plate], power=150.0, diffuse_only=True)
    check(
        "lights_from_geometry creates one light per fixture",
        len(created) == 1,
        str(created),
    )
    light = bpy.data.objects.get(created[0]) if created else None
    check("...as an AREA light", light is not None and light.data.type == "AREA")
    check(
        "...with the fixture's power",
        light is not None and abs(light.data.energy - 150.0) < 1e-6,
    )
    # primitive_cube_add(size=1) is one unit across, so the scaled plate is
    # 2.0 x 0.5 x 0.02: the thin axis is dropped and the remaining two become the
    # rectangle (larger -> size, smaller -> size_y).
    check(
        "...sized to the plate's broad face",
        light is not None
        and abs(light.data.size - 2.0) < 0.05
        and abs(light.data.size_y - 0.5) < 0.05,
        f"{light.data.size:.2f} x {light.data.size_y:.2f}" if light else "",
    )
    # Aimed down: the plate sits above the group centre, so "auto" points it at the floor.
    # The update is load-bearing: `matrix_world` is EVALUATED state, and a freshly created
    # object reports identity until the depsgraph runs. Identity happens to read as
    # "aimed down", so without this the check passes even if no rotation was applied.
    import mathutils

    bpy.context.view_layer.update()
    aim = light.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1))
    check("...aimed downward from a ceiling plate", aim.z < -0.9, f"aim.z={aim.z:.3f}")
    check("remove_lights cleans them up", len(LightUtils.remove_lights()) == 1)
    check(
        "...and leaves none behind",
        not [o for o in bpy.data.objects if o.type == "LIGHT"],
    )

    # --- a COPLANAR ceiling grid: every plate must aim down --------------------
    # The real shape of a room's lighting, and the case the old "auto" rule got
    # wrong: the centre of a set of ceiling plates lies IN their own plane, so
    # `toward[axis]` is zero up to modelling noise and its SIGN decided up vs
    # down. Measured on a production office, 2 of 4 troffers fired into the
    # ceiling. The heights below differ by a tenth of a millimetre, as modelled
    # geometry does.
    grid = []
    for i, (x, y, dz) in enumerate(
        [(-2, -2, 0.0), (2, -2, 0.0001), (-2, 2, -0.0001), (2, 2, 0.00005)]
    ):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, 3 + dz))
        # NOT `plate` — that name is the ceiling plate the export section below reuses.
        grid_plate = bpy.context.active_object
        grid_plate.name = f"LIGHT_grid{i}"
        grid_plate.scale = (1.5, 0.3, 0.05)  # a troffer: thin in Z, thickness 0.05
        grid.append(grid_plate)
    bpy.context.view_layer.update()

    def aim_zs(names):
        bpy.context.view_layer.update()
        return [
            round(
                (
                    bpy.data.objects[n].matrix_world.to_quaternion()
                    @ mathutils.Vector((0, 0, -1))
                ).z,
                3,
            )
            for n in names
        ]

    made = LightUtils.lights_from_geometry(grid, power=50.0)
    aims = aim_zs(made)
    check(
        "a coplanar ceiling grid aims every plate DOWN",
        len(aims) == 4 and all(z < -0.9 for z in aims),
        f"aim.z per plate = {aims}",
    )
    # ...and each light must clear its own housing, or the plate blocks it.
    lows = [
        bpy.data.objects[n].location.z
        < min((g.matrix_world @ mathutils.Vector(v)).z for v in g.bound_box)
        for n, g in zip(made, grid)
    ]
    check("...and sits BELOW its own plate, not inside it", all(lows), str(lows))

    # An explicit `toward` must still win over the coplanar fallback.
    LightUtils.remove_lights()
    up = aim_zs(LightUtils.lights_from_geometry(grid, power=50.0, toward=(0, 0, 10)))
    check(
        "an explicit `toward` above the plates aims them UP",
        all(z > 0.9 for z in up),
        str(up),
    )

    # Colour temperature rides Blender's own blackbody, not a hand-rolled table.
    LightUtils.remove_lights()
    warm = LightUtils.lights_from_geometry(grid[:1], power=50.0, kelvin=4000)
    lamp = bpy.data.objects[warm[0]].data
    check(
        "kelvin sets Blender's native light temperature",
        bool(getattr(lamp, "use_temperature", False))
        and abs(lamp.temperature - 4000) < 1,
        f"use_temperature={getattr(lamp, 'use_temperature', None)} temperature={getattr(lamp, 'temperature', None)}",
    )
    LightUtils.remove_lights()
    plain = LightUtils.lights_from_geometry(grid[:1], power=50.0)
    check(
        "...and is left OFF when no kelvin is given",
        not getattr(bpy.data.objects[plain[0]].data, "use_temperature", False),
    )
    LightUtils.remove_lights()
    # NOT `plate` — that name is the ceiling plate the export section below still uses.
    for grid_plate in grid:
        bpy.data.objects.remove(grid_plate, do_unlink=True)

    # --- encode: linear EXR -> sRGB PNG -----------------------------------
    # A known linear ramp written as EXR, encoded, then read back.
    src = os.path.join(tmp_dir, "ramp.exr")
    linear = np.array([0.0, 0.05, 0.25, 0.5, 1.0], dtype=np.float32)
    ramp = bpy.data.images.new("ramp", width=len(linear), height=1, float_buffer=True)
    ramp.colorspace_settings.name = "Non-Color"
    buf = np.ones(len(linear) * 4, dtype=np.float32)
    for i in range(3):
        buf[i::4] = linear
    ramp.pixels.foreach_set(buf)
    ramp.filepath_raw = src
    ramp.file_format = "OPEN_EXR"
    ramp.save()
    bpy.data.images.remove(ramp)

    # percentile=100 -> divisor is the max (1.0), so the encode is a pure sRGB transfer
    # and the expected values are exact.
    encoded = LightmapWebExport.encode_for_web({"ramp": src}, tmp_dir, percentile=100.0)
    png, scalar = encoded.get("ramp", (None, None))
    check("encode_for_web writes a PNG", png is not None and os.path.isfile(png))
    # No folder named: a temp artifact, never a PNG beside the authored EXR.
    import pythontk as ptk

    _default, _s = LightmapWebExport.encode_for_web({"ramp": src}, percentile=100.0)[
        "ramp"
    ]
    check(
        "encode_for_web's default folder is temp, not the EXR's own",
        os.path.isfile(_default)
        and os.path.normcase(os.path.realpath(_default)).startswith(
            os.path.normcase(os.path.realpath(ptk.TempArtifacts.root()))
        )
        and os.path.normcase(os.path.dirname(os.path.abspath(_default)))
        != os.path.normcase(os.path.dirname(os.path.abspath(src))),
        _default,
    )
    check(
        "...and reports the divisor",
        scalar is not None and abs(scalar - 1.0) < 1e-3,
        str(scalar),
    )

    back = bpy.data.images.load(png)
    back.colorspace_settings.name = "Non-Color"
    rb = np.empty(len(back.pixels), dtype=np.float32)
    back.pixels.foreach_get(rb)
    bpy.data.images.remove(back)
    got = rb.reshape(-1, 4)[:, 0]
    want = np.where(
        linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055
    )
    check(
        "...sRGB-encoded exactly once (not black, not double-encoded)",
        bool(np.all(np.abs(want - got) < 0.01)),
        f"want={np.round(want, 3).tolist()} got={np.round(got, 3).tolist()}",
    )

    # A map with a large HDR range must not simply clip: the divisor carries the range.
    hdr_src = os.path.join(tmp_dir, "hdr.exr")
    values = np.concatenate(
        [np.full(99, 0.5, np.float32), np.array([100.0], np.float32)]
    )
    hdr = bpy.data.images.new("hdr", width=len(values), height=1, float_buffer=True)
    hdr.colorspace_settings.name = "Non-Color"
    hbuf = np.ones(len(values) * 4, dtype=np.float32)
    for i in range(3):
        hbuf[i::4] = values
    hdr.pixels.foreach_set(hbuf)
    hdr.filepath_raw = hdr_src
    hdr.file_format = "OPEN_EXR"
    hdr.save()
    bpy.data.images.remove(hdr)
    _p, hdr_scalar = LightmapWebExport.encode_for_web(
        {"hdr": hdr_src}, tmp_dir, percentile=99.0
    )["hdr"]
    # The point is that the divisor tracks the BULK of the map, not its brightest texel:
    # dividing by the max (100.0) would crush the 0.5 body to nothing. Some interpolation
    # across the percentile boundary is expected, so this asserts the order of magnitude.
    check(
        "percentile divisor tracks the body, not the outlier",
        hdr_scalar < 10.0,
        f"scalar={hdr_scalar:.3f} (max was 100.0, body 0.5)",
    )

    # The default encode IS pythontk's (ImgUtils.encode_hdr_pixels_for_web), only the
    # I/O is Blender's: a lit pool wider than 0.5% keeps its peak (dividing by the
    # percentile clipped a production room's pools flat), the same scalar, the same codes.
    pool_src = os.path.join(tmp_dir, "pool.exr")
    pool_values = np.concatenate(
        [np.full(196, 0.1, np.float32), np.array([0.5, 1.0, 1.5, 2.0], np.float32)]
    )
    pool = bpy.data.images.new(
        "pool", width=len(pool_values), height=1, float_buffer=True
    )
    pool.colorspace_settings.name = "Non-Color"
    pbuf = np.ones(len(pool_values) * 4, dtype=np.float32)
    for i in range(3):
        pbuf[i::4] = pool_values
    pool.pixels.foreach_set(pbuf)
    pool.filepath_raw = pool_src
    pool.file_format = "OPEN_EXR"
    pool.save()
    bpy.data.images.remove(pool)
    pool_png, pool_scalar = LightmapWebExport.encode_for_web(
        {"pool": pool_src}, tmp_dir
    )["pool"]
    want_codes, want_scalar = ptk.ImgUtils.encode_hdr_pixels_for_web(
        np.repeat(pool_values[:, None], 3, axis=1)
    )
    back = bpy.data.images.load(pool_png)
    back.colorspace_settings.name = "Non-Color"
    pb = np.empty(len(back.pixels), dtype=np.float32)
    back.pixels.foreach_get(pb)
    bpy.data.images.remove(back)
    got_codes = np.round(pb.reshape(-1, 4)[:, :3] * 255.0)
    check(
        "the default encode is pythontk's: the pool keeps its peak, the codes match",
        abs(pool_scalar - want_scalar) < 1e-5
        and abs(pool_scalar - 2.0) < 1e-4
        and bool((got_codes == want_codes).all()),
        f"scalar={pool_scalar:.4f} want={want_scalar:.4f} "
        f"codes off={int((got_codes != want_codes).any(axis=1).sum())}",
    )

    # A level between two 8-bit codes must average to itself (stochastic rounding,
    # the pythontk twin's ImgUtils.quantize_8bit): rounding every texel to the nearer
    # code terraced a production room's smooth walls into contour bands at 2K.
    mid_src = os.path.join(tmp_dir, "between.exr")
    side = 64
    level = ((127.3 / 255.0 + 0.055) / 1.055) ** 2.4
    mid = bpy.data.images.new("between", width=side, height=side, float_buffer=True)
    mid.colorspace_settings.name = "Non-Color"
    mbuf = np.ones(side * side * 4, dtype=np.float32)
    for i in range(3):
        mbuf[i::4] = level
    mbuf[:3] = 1.0  # one bright texel: the percentile-100 divisor is 1.0
    mid.pixels.foreach_set(mbuf)
    mid.filepath_raw = mid_src
    mid.file_format = "OPEN_EXR"
    mid.save()
    bpy.data.images.remove(mid)
    mid_png, _s = LightmapWebExport.encode_for_web(
        {"between": mid_src}, tmp_dir, percentile=100.0
    )["between"]
    back = bpy.data.images.load(mid_png)
    back.colorspace_settings.name = "Non-Color"
    mb = np.empty(len(back.pixels), dtype=np.float32)
    back.pixels.foreach_get(mb)
    bpy.data.images.remove(back)
    codes = np.round(mb.reshape(-1, 4)[1:, :3] * 255.0)
    check(
        "an 8-bit level between codes is encoded unbiased",
        abs(float(codes.mean()) - 127.3) < 0.05 and codes.max() - codes.min() <= 1,
        f"mean={codes.mean():.3f} min={codes.min()} max={codes.max()}",
    )

    # --- web texture budget -------------------------------------------------
    # A source texture past the ceiling is downsized for the export and reloaded
    # after. The lightmaps are never among the export's images: they ride the
    # GLB as the record's media, at the size the bake wrote them.
    def write_png(name, size):
        img = bpy.data.images.new(name, width=size, height=size)
        path = os.path.join(tmp_dir, f"{name}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        return bpy.data.images.load(path)

    source_img = write_png("budget_source", 128)
    LightmapWebExport(resolution=64, samples=1, device=None)._downsize_images(64)
    check(
        "web budget downsizes a source texture",
        tuple(source_img.size) == (64, 64),
        str(tuple(source_img.size)),
    )
    bpy.data.images.remove(source_img)

    # --- denoise post-pass -------------------------------------------------
    from blendertk.mat_utils.texture_baker import TextureBaker

    noisy_path = os.path.join(tmp_dir, "noisy.exr")
    rng = np.random.default_rng(0)
    noisy_a = (0.5 + rng.normal(0, 0.15, (64, 64))).astype(np.float32)
    noisy = bpy.data.images.new("noisy", width=64, height=64, float_buffer=True)
    noisy.colorspace_settings.name = "Non-Color"
    nbuf = np.ones(64 * 64 * 4, dtype=np.float32)
    flat = noisy_a.reshape(-1)
    for i in range(3):
        nbuf[i::4] = flat
    noisy.pixels.foreach_set(nbuf)
    noisy.filepath_raw = noisy_path
    noisy.file_format = "OPEN_EXR"
    noisy.save()
    bpy.data.images.remove(noisy)

    def grain(path):
        img = bpy.data.images.load(path)
        b = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(b)
        a = b.reshape(img.size[1], img.size[0], img.channels)[..., 0]
        bpy.data.images.remove(img)
        return float(
            np.abs(
                4 * a[1:-1, 1:-1]
                - a[:-2, 1:-1]
                - a[2:, 1:-1]
                - a[1:-1, :-2]
                - a[1:-1, 2:]
            ).mean()
        )

    before_grain = grain(noisy_path)
    denoised = TextureBaker.denoise_image(noisy_path)
    if denoised:
        check(
            "denoise_image reduces grain",
            grain(denoised) < before_grain * 0.6,
            f"{before_grain:.4f} -> {grain(denoised):.4f}",
        )
    else:
        check(
            "denoise_image degrades without losing the map", os.path.isfile(noisy_path)
        )

    # --- commit -> wired_for_export -> GLB ---------------------------------
    # The canonical flow: the BAKE commits scene state (markers incl. the map's
    # ``dir`` locate hint); the EXPORT self-feeds from that state with no
    # knowledge of how or whether the scene was baked. One layout, two
    # containers (2026-11-07): the native GLB carries the ``lightmaps`` record
    # as it stands and each file it names as the bufferView of its name, as
    # the FBX route's build does -- nothing is wired into a material.
    LightUtils.lights_from_geometry([plate], power=300.0)
    web = LightmapWebExport(resolution=64, samples=4, denoise=False, device=None)
    MEDIA = ptk.SceneRecords.GLB_MEDIA
    KEY = ptk.SceneRecords.LIGHTMAPS.key

    def media_view(parsed, name):
        """``(view, bytes)`` of the bufferView the file *name* rides, or ``(None, b"")``."""
        index = MEDIA.view(parsed, name)
        if index is None:
            return None, b""
        return parsed["bufferViews"][index], b""

    def media_bytes(path, name):
        """The bytes the bufferView named *name* holds in the GLB at *path*."""
        with ptk.MeshConvert.open_glb(path) as edit:
            index = MEDIA.view(edit.gltf, name)
            if index is None:
                return b""
            view = edit.gltf["bufferViews"][index]
            start = int(view.get("byteOffset", 0))
            return bytes(edit.bin_data[start : start + int(view["byteLength"])])

    def scene_left_as_found(materials=None):
        """What the export left in the scene: lightmap nodes, material copies,
        the record stamped on the scene, a lightmap image loaded."""
        left = [
            f"{m.name}:{n.name}"
            for m in (materials or bpy.data.materials)
            if btk.MatUtils.get_node_tree(m) is not None
            for n in m.node_tree.nodes
            if n.label == "Lightmap"
        ]
        left += [m.name for m in bpy.data.materials if "~lm" in m.name]
        if KEY in bpy.context.scene:
            left.append(f"scene[{KEY}]")
        return left

    # An uncommitted scene must be a clean no-op (what makes wrapping every
    # export unconditional safe).
    with web.wired_for_export() as noop_token:
        check("no committed bake -> wired_for_export yields None", noop_token is None)

    baked = web.baker.bake_separated([floor], output_dir=tmp_dir)
    packed = web.baker.pack_atlas(baked, output_dir=tmp_dir, suffix="_Lightmap")
    atlas_map = {n: p for n, (p, _r) in packed.items()} if packed else baked
    rects = {n: r for n, (_p, r) in packed.items()} if packed else {}
    web.baker.commit_lightmap(atlas_map, scale_offsets=rects)

    glb_path = os.path.join(tmp_dir, "scene.glb")
    with web.wired_for_export() as manifest_token:
        check(
            "wired_for_export self-feeds from the committed markers",
            bool(manifest_token),
            str(manifest_token)[:120],
        )
        check(
            "...and stamps the record on the scene for the export, as JSON text",
            isinstance(bpy.context.scene.get(KEY), str)
            and json.loads(bpy.context.scene[KEY]) == manifest_token,
        )
        web.export_glb(
            glb_path, objects=[floor], manifest=manifest_token, texture_max_size=None
        )
    check("export writes a GLB", os.path.isfile(glb_path))

    gltf = glb_json(glb_path)
    prims = [p for m in gltf.get("meshes", []) for p in m.get("primitives", [])]
    check(
        "every primitive carries TEXCOORD_1, with no texture reading it",
        bool(prims) and all("TEXCOORD_1" in p.get("attributes", {}) for p in prims),
        f"{len(prims)} primitive(s)",
    )
    check(
        "no material carries the lightmap: no slot is wired",
        not any(m.get("occlusionTexture") for m in gltf.get("materials", [])),
        [m.get("name") for m in gltf.get("materials", []) if m.get("occlusionTexture")],
    )
    # The file is stated as every deliverable states itself (finish_glb): the
    # record the native exporter wrote into the first scene's extras, as JSON
    # text, rides the ROOT's as an object -- the FBX route's record, entry for
    # entry, its lengths in the native writer's unit.
    manifest = (gltf.get("extras") or {}).get(KEY)
    check(
        "the record rides in the file's ROOT extras, as an object",
        isinstance(manifest, dict)
        and manifest.get("version") == ptk.SceneRecords.LIGHTMAPS.version,
        manifest,
    )
    check(
        "...not in the scene's, where the native exporter wrote it",
        KEY not in ((gltf.get("scenes") or [{}])[0].get("extras") or {}),
    )
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

    fbx_route = LightmapRecords._record().payload
    check(
        "...as it stands: the FBX route's record, entry for entry, labelled in the "
        "native writer's unit",
        isinstance(manifest, dict)
        and manifest.get("objects") == fbx_route.get("objects")
        and manifest == manifest_token
        and ptk.SceneRecords.problems(ptk.SceneRecords.LIGHTMAPS, manifest) == [],
        (manifest, fbx_route),
    )
    # Retired 2026-10-20 (removed in 0.21.0): the key is the record's.
    import warnings as _warnings

    with _warnings.catch_warnings(record=True) as _caught:
        _warnings.simplefilter("always")
        _extras_key = LightmapWebExport.EXTRAS_KEY
        with web.wired_for_export(carrier="occlusion") as _carried:
            pass
    _notes = [str(w.message) for w in _caught if w.category is DeprecationWarning]
    check(
        "EXTRAS_KEY still answers the record's key, with a warning",
        _extras_key == KEY and any("EXTRAS_KEY" in n for n in _notes),
        _notes,
    )
    check(
        "...and wired_for_export's carrier is dropped, with a warning (2026-11-07)",
        _carried == manifest_token and any("'carrier'" in n for n in _notes),
        _notes,
    )
    # Held until 0.25.0 (noticed for 0.21.0 on 2026-10-24, the window closing
    # 2026-11-23): the retired block's version and builder still answer, with
    # a warning. Restored: 2026-11-08
    with _warnings.catch_warnings(record=True) as _caught:
        _warnings.simplefilter("always")
        _held_version = getattr(LightmapWebExport, "MANIFEST_VERSION", None)
        try:
            _held_block = web.build_manifest(
                {"floor": ("floor_lm.png", 1.5)}, "occlusion", lighting={"x": 1}
            )
        except Exception as _e:  # noqa: BLE001 -- the check reports it
            _held_block = repr(_e)
    _notes = [str(w.message) for w in _caught if w.category is DeprecationWarning]
    check(
        "MANIFEST_VERSION and build_manifest(lighting=) still answer, with warnings",
        _held_version == 2
        and isinstance(_held_block, dict)
        and _held_block.get("version") == 2
        and _held_block.get("carrier") == "occlusion"
        and _held_block.get("materials")
        and all(m == {"intensity": 1.5} for m in _held_block["materials"].values())
        and any("MANIFEST_VERSION" in n for n in _notes)
        and any("'lighting'" in n for n in _notes),
        (_held_version, _held_block, _notes),
    )

    # The GLB states its lightmaps once, in its ``lightmaps`` record: each
    # object's marker (and the data_export record) leave the nodes
    # (ptk.MeshConvert.finish_glb strips every record and marker off them).
    RECORD_KEYS = {spec.key for spec in ptk.SceneRecords.deliverable()} | {
        "lightmapInfo"
    }

    def floor_map(parsed):
        """The floor's entry in *parsed*'s record and the view its map rides."""
        record = (parsed.get("extras") or {}).get(KEY) or {}
        entry = next(
            (e for e in record.get("objects") or [] if e.get("name") == "floor"), {}
        )
        view, _ = media_view(parsed, entry.get("map"))
        return entry, view

    def node_copies(parsed):
        """The nodes still carrying a record or a lightmap marker."""
        return [
            n.get("name")
            for n in parsed.get("nodes", [])
            if RECORD_KEYS & set(n.get("extras") or {})
        ]

    # A native export with include_lights writes the scene's lights as
    # KHR_lights_punctual; over a bake they leave the file, as they leave an
    # FBX-built one (strip_glb_baked_lights): a reader would light it twice.
    lit_path = os.path.join(tmp_dir, "scene_with_lights.glb")
    shutil.copyfile(glb_path, lit_path)
    with ptk.MeshConvert.open_glb(lit_path) as edit:
        edit.gltf["nodes"].append(
            {"name": "plate_light", "extensions": {"KHR_lights_punctual": {"light": 0}}}
        )
        edit.gltf.setdefault("extensions", {})["KHR_lights_punctual"] = {
            "lights": [{"type": "point", "intensity": 300.0}]
        }
        edit.gltf.setdefault("extensionsUsed", []).append("KHR_lights_punctual")
        edit.dirty = True
    web.embed_media(lit_path, [floor])
    lit_gltf = glb_json(lit_path)
    check(
        "a baked native GLB ships no KHR_lights_punctual over its bake (2026-11-07)",
        "KHR_lights_punctual" not in (lit_gltf.get("extensions") or {})
        and "KHR_lights_punctual" not in (lit_gltf.get("extensionsUsed") or [])
        and not any(
            "KHR_lights_punctual" in (n.get("extensions") or {})
            for n in lit_gltf.get("nodes", [])
        ),
        lit_gltf.get("extensionsUsed"),
    )

    floor_entry, floor_view = floor_map(gltf)
    check(
        "the floor's map rides as the bufferView named by its file: 8-bit sRGB "
        "PNG, its range as extras.scale",
        bool(floor_entry)
        and floor_view is not None
        and floor_view.get("extras", {}).get(MEDIA.mime_type) == "image/png"
        and (floor_view.get("extras", {}).get(MEDIA.scale) or 0) > 0
        and media_bytes(glb_path, floor_entry["map"]).startswith(b"\x89PNG"),
        (floor_entry, floor_view),
    )
    check(
        "...and is no image of the file's",
        not any(
            os.path.splitext(i.get("name") or "")[0]
            == os.path.splitext(floor_entry.get("map") or "")[0]
            for i in gltf.get("images", [])
        ),
        [i.get("name") for i in gltf.get("images", [])],
    )
    check(
        "...and once: no record or marker rides on any node, no authoring folder",
        not node_copies(gltf),
        node_copies(gltf),
    )
    check(
        "...and no handoff: the carrier did not ship, so the file claims no "
        "approved scene",
        "handoff" not in (gltf.get("extras") or {}),
        sorted(gltf.get("extras") or {}),
    )
    check(
        "the scene is left as found: no lightmap node, no material copy, no "
        "record stamped on it",
        not scene_left_as_found(),
        scene_left_as_found(),
    )

    # The route a caller composes itself (tentacle's scene slot): a direct export
    # inside wired_for_export, which finishes the file it is given on exit.
    direct_glb = os.path.join(tmp_dir, "direct.glb")
    with web.wired_for_export(glb_path=direct_glb) as _wired:
        bpy.ops.export_scene.gltf(
            filepath=direct_glb, export_format="GLB", export_extras=True
        )
    dgltf = glb_json(direct_glb)
    dblock = (dgltf.get("extras") or {}).get(KEY)
    dentry, dview = floor_map(dgltf)
    check(
        "a direct export inside wired_for_export(glb_path=) states it once too: "
        "the record an object in ROOT extras, the map riding, no record or marker "
        "on any node",
        isinstance(dblock, dict)
        and bool(dentry)
        and dview is not None
        and not node_copies(dgltf),
        f"lightmaps={dentry} view={dview} copies={node_copies(dgltf)}",
    )
    check(
        "...loading no lightmap image into the scene",
        not any(
            os.path.basename(bpy.path.abspath(i.filepath or "")).lower()
            == os.path.splitext(dentry.get("map") or "")[0].lower() + ".png"
            for i in bpy.data.images
        ),
        [i.name for i in bpy.data.images],
    )
    # The whole scene ships the data_export carrier, whose handoff record the
    # bake's publish stamped: the file states the handoff it implies, and the
    # carrier -- records alone, no content -- leaves with them.
    dhandoff = (dgltf.get("extras") or {}).get("handoff")
    check(
        "...with the handoff its carrier's record implies, in ROOT extras",
        isinstance(dhandoff, dict)
        and dhandoff.get("version") == ptk.SceneRecords.HANDOFF.version
        and any(
            row.get("part") == "lightmaps" and row.get("data") == ["extras." + KEY]
            for row in dhandoff.get("assembly") or []
        ),
        dhandoff and [(r.get("part"), r.get("data")) for r in dhandoff.get("assembly", [])],
    )
    check(
        "...and no carrier node is left in the file",
        not any(n.get("name") == "data_export" for n in dgltf.get("nodes", [])),
        [n.get("name") for n in dgltf.get("nodes", [])],
    )
    check(
        "...and the record it carries is the native one (the writer's unit), not "
        "the carrier's",
        isinstance(dblock, dict) and dblock == web.native_record(),
        (dblock, web.native_record()),
    )
    check("...and the scene is left as found", not scene_left_as_found())

    # --- atlas packing: the rect is PER-INSTANCE ---------------------------
    # A material-atlased bake gives every object a different rect into ONE shared
    # map, so the binding cannot be a material-level property: bound without it,
    # each object samples the WHOLE atlas through its own [0,1] unwrap and wears
    # every other object's lighting -- which reads as blocky patches, not as a
    # missing map. The record states each object's rect; the viewer binds it
    # on a material of the object's own at load.
    shared = bpy.data.materials.new("M_Atlas")
    btk.MatUtils.get_node_tree(shared, create=True)
    pair = []
    for i, loc in enumerate(((6.0, 0.0, 0.0), (6.0, 3.0, 1.0))):
        bpy.ops.mesh.primitive_plane_add(size=2, location=loc)
        ob = bpy.context.active_object
        ob.name = f"atlas_piece_{i}"
        ob.data.materials.append(shared)
        pair.append(ob)
    bpy.context.view_layer.update()  # atlas weights read matrix_world

    packed = web.baker.bake_atlas(pair, output_dir=tmp_dir)
    rects = {n: r for n, (_p, r) in packed.items()}
    check(
        "two objects on one material pack into ONE atlas with distinct rects",
        len({p for p, _r in packed.values()}) == 1
        and len(rects) == 2
        and all(list(r) != [1.0, 1.0, 0.0, 0.0] for r in rects.values()),
        json.dumps({n: [round(v, 4) for v in r] for n, r in rects.items()}),
    )

    web.baker.commit_lightmap(
        {n: p for n, (p, _r) in packed.items()}, scale_offsets=rects
    )
    atlas_glb = os.path.join(tmp_dir, "atlas.glb")
    with web.wired_for_export(objects=pair) as atlas_manifest:
        web.export_glb(
            atlas_glb, objects=pair, manifest=atlas_manifest, texture_max_size=None
        )
    agltf = glb_json(atlas_glb)
    arecord = (agltf.get("extras") or {}).get(KEY) or {}
    shipped = {
        e.get("name"): [round(float(v), 4) for v in e.get("scaleOffset") or ()]
        for e in arecord.get("objects") or []
    }
    expected = {n: [round(float(v), 4) for v in r] for n, r in rects.items()}
    check(
        "each atlased object rides the record with its own committed rect",
        shipped == expected,
        f"got {json.dumps(shipped)} want {json.dumps(expected)}",
    )
    atlas_names = {e.get("map") for e in arecord.get("objects") or []}
    check(
        "...the atlas riding ONE bufferView, no image of the file's",
        len(atlas_names) == 1
        and media_view(agltf, next(iter(atlas_names)))[0] is not None
        and not agltf.get("images"),
        (atlas_names, len(agltf.get("images", []))),
    )
    check(
        "...and both pieces wear the one material, uncopied",
        sorted(m.get("name") for m in agltf.get("materials", [])) == ["M_Atlas"],
        [m.get("name") for m in agltf.get("materials", [])],
    )
    check(
        "...and every lit piece is the record's, by the readers' rule",
        sorted(
            agltf["nodes"][i]["name"] for i in ptk.MeshConvert.lit_nodes(agltf)
        )
        == sorted(o.name for o in pair),
        sorted(agltf["nodes"][i]["name"] for i in ptk.MeshConvert.lit_nodes(agltf)),
    )
    check("...and the scene is left as found", not scene_left_as_found([shared]))

    # --- one material, DIFFERENT maps, no atlas ----------------------------
    # A secondary material several baked objects share (one GLASS on two machine
    # bodies) or a Per-Object bake of linked duplicates (one mesh, one material
    # behind every instance): each object has a map of its own and no rect, and a
    # glTF material carries ONE lightmap. The record names each object with its
    # own map, every map rides the file, and nothing is copied: the viewer gives
    # each object its own binding at load.
    from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET

    def float_map(path, value, w=8, h=8):
        """A constant linear EXR at *path*."""
        img = bpy.data.images.new(os.path.basename(path), w, h, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"  # BEFORE the pixels
        px = np.ones((h, w, 4), dtype=np.float32)
        px[..., :3] = value
        img.pixels.foreach_set(px.reshape(-1))
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    glass = bpy.data.materials.new("M_Glass")
    btk.MatUtils.get_node_tree(glass, create=True)
    bodies = []
    for i, loc in enumerate(((12.0, 0.0, 0.0), (12.0, 3.0, 0.0))):
        bpy.ops.mesh.primitive_plane_add(size=2, location=loc)
        ob = bpy.context.active_object
        ob.name = f"glass_body_{i}"
        ob.data.materials.append(glass)
        ob.data.uv_layers.new(name=LIGHTMAP_UV_SET)
        bodies.append(ob)
    twin = bodies[0].copy()  # a linked duplicate: SAME mesh, same material
    twin.name = "glass_body_twin"
    twin.location = (12.0, 6.0, 0.0)
    bpy.context.scene.collection.objects.link(twin)
    bodies.append(twin)

    split_maps = {
        ob.name: float_map(os.path.join(tmp_dir, f"{ob.name}_lm.exr"), 0.2 + 0.2 * i)
        for i, ob in enumerate(bodies)
    }
    web.baker.commit_lightmap(split_maps)
    split_glb = os.path.join(tmp_dir, "split.glb")
    # The export selects what it ships (use_selection); the artist's selection and
    # active object come back after, as FbxUtils.export / UsdUtils.export restore
    # theirs. It left *bodies* selected. Added: 2026-09-27
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = twin
    with web.wired_for_export(objects=bodies) as split_manifest:
        web.export_glb(
            split_glb, objects=bodies, manifest=split_manifest, texture_max_size=None
        )
    check(
        "a GLB export of objects puts the selection and active object back",
        not [o.name for o in bpy.context.view_layer.objects if o.select_get()]
        and bpy.context.view_layer.objects.active == twin,
        str([o.name for o in bpy.context.view_layer.objects if o.select_get()]),
    )
    sgltf = glb_json(split_glb)
    srecord = (sgltf.get("extras") or {}).get(KEY) or {}
    got_maps = {e.get("name"): e.get("map") for e in srecord.get("objects") or []}
    want_maps = {n: os.path.basename(p) for n, p in split_maps.items()}
    check(
        "objects sharing a material but baked into different maps each ride the "
        "record with their own",
        got_maps == want_maps,
        f"got {json.dumps(got_maps)} want {json.dumps(want_maps)}",
    )
    check(
        "...every map riding the file, each at its own scale",
        all(media_view(sgltf, m)[0] is not None for m in want_maps.values())
        and len(
            {
                round(media_view(sgltf, m)[0]["extras"][MEDIA.scale], 4)
                for m in want_maps.values()
            }
        )
        == len(want_maps),
        [media_view(sgltf, m)[0] for m in want_maps.values()],
    )
    check(
        "...and nothing copied: one material in the file",
        sorted(m.get("name") for m in sgltf.get("materials", [])) == ["M_Glass"],
        [m.get("name") for m in sgltf.get("materials", [])],
    )
    check("...and the scene is left as found", not scene_left_as_found([glass]))

    # --- one material, a BAKED object and one the bake never saw ------------
    # The record names the baked object alone: the prop wearing the same
    # material is in no entry, so no reader lights it with the wall's bake (the
    # viewer binds the wall on a material of its own and leaves the prop's as
    # authored).
    paint = bpy.data.materials.new("M_Paint")
    btk.MatUtils.get_node_tree(paint, create=True)
    painted = {}
    for role, loc in (("wall", (18.0, 0.0, 0.0)), ("prop", (18.0, 3.0, 0.0))):
        bpy.ops.mesh.primitive_plane_add(size=2, location=loc)
        ob = bpy.context.active_object
        ob.name = f"paint_{role}"
        ob.data.materials.append(paint)
        ob.data.uv_layers.new(name=LIGHTMAP_UV_SET)
        painted[role] = ob
    wall_exr = float_map(os.path.join(tmp_dir, "paint_wall_lm.exr"), 0.5)
    web.baker.commit_lightmap({painted["wall"].name: wall_exr})
    paint_glb = os.path.join(tmp_dir, "paint.glb")
    paint_objects = list(painted.values())
    with web.wired_for_export(objects=paint_objects) as paint_manifest:
        web.export_glb(
            paint_glb, objects=paint_objects, manifest=paint_manifest, texture_max_size=None
        )
    pgltf = glb_json(paint_glb)
    lit = sorted(pgltf["nodes"][i]["name"] for i in ptk.MeshConvert.lit_nodes(pgltf))
    check(
        "an unbaked object sharing a baked one's material is in no entry: the "
        "record lights the wall alone",
        lit == ["paint_wall"]
        and any(n.get("name") == "paint_prop" for n in pgltf.get("nodes", [])),
        lit,
    )

    # --- the bracket takes back what it stamped ------------------------------
    # wired_for_export runs on the LIVE scene (tentacle's GLB export wraps the
    # artist's own .blend in it): an export that raises inside it leaves the
    # scene's record property as it found it.
    bracket_raised = False
    try:
        with web.wired_for_export(objects=[floor]):
            raise RuntimeError("the export failed")
    except RuntimeError:
        bracket_raised = True
    check(
        "wired_for_export takes back the record it stamped when the export raises",
        bracket_raised and KEY not in bpy.context.scene,
        sorted(bpy.context.scene.keys()),
    )

    # --- the viewer's side of the contract ---------------------------------
    # The exporter and pythontk's WebXR viewer agree through the record and the
    # media transport (ptk.SceneRecords.GLB_MEDIA, generated into the viewer's
    # records.js), so nothing but this catches a drift: read the record or the
    # media differently there and the lightmap silently stops being applied (or
    # is applied twice, or linear).
    preview_dir = os.path.join(MONO, "pythontk", "pythontk", "net_utils", "preview")
    viewer_path = os.path.join(preview_dir, "viewer.html")
    if os.path.isfile(viewer_path) and manifest:
        # The viewer is viewer.html PLUS the modules it loads (``kernel/``,
        # ``features/``): the binder lives in ``kernel/lightmaps.js``.
        import glob

        sources = [viewer_path] + sorted(
            glob.glob(os.path.join(preview_dir, "**", "*.js"), recursive=True)
        )
        viewer = "\n".join(open(p, encoding="utf-8").read() for p in sources)
        for label, cond in (
            ("viewer reads the key the exporter writes", KEY in viewer),
            (
                "viewer reads each map from the bufferView of its name (GLB_MEDIA)",
                "GLB_MEDIA" in viewer and f"'{MEDIA.mime_type}'" in viewer,
            ),
            ("viewer binds it as a lightMap", ".lightMap = " in viewer),
            ("viewer applies the view's scale", "lightMapIntensity" in viewer),
            (
                "viewer overrides colorSpace for the sRGB map",
                "colorSpace = THREE.SRGBColorSpace" in viewer,
            ),
            (
                "viewer reads the map through the record's UV set",
                "LIGHTMAP_UV_SET" in viewer,
            ),
            # The key light goes off for a pre-lit model; the ENVIRONMENT does
            # not, it is confined to its specular term on the baked materials
            # instead. Asserted as the BEHAVIOUR -- zero when lightmapped, the
            # standing key intensity otherwise -- not as one literal right-hand
            # side.
            (
                "viewer drops its key light for a pre-lit model",
                bool(
                    re.search(
                        r"keyLight\.intensity\s*=\s*lightmapped\s*\?\s*0\s*:", viewer
                    )
                )
                and "DEFAULT_KEY_INTENSITY" in viewer,
            ),
            (
                "viewer keeps the environment for a pre-lit model, specular only",
                "scene.environmentIntensity" in viewer
                and "getIBLIrradiance( geometryNormal )" in viewer,
            ),
        ):
            check(label, cond)
    else:
        check("viewer contract checked", False, "preview/viewer.html not found")

    # --- the reflection probe rides the native GLB ---------------------------
    # The bake captures the room it lit as an HDR (LightmapBaker.bake_probe);
    # the viewer reads it from lightmaps.probe as Radiance bytes in a view of
    # the file's own buffer -- embedded after the export, through Blender's own
    # Radiance writer (Blender ships no cv2).
    import pythontk as ptk

    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

    def pr_float_image(path, top, bottom, w=16, h=8):
        """An EXR whose top half is *top* and bottom half *bottom* (linear)."""
        img = bpy.data.images.new(os.path.basename(path), w, h, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"  # BEFORE the pixels
        px = np.empty((h, w, 4), dtype=np.float32)
        px[...] = 1.0
        px[: h // 2, :, :3] = bottom  # bpy rows run bottom-up
        px[h // 2 :, :, :3] = top
        img.pixels.foreach_set(px.reshape(-1))
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    def pr_read(path):
        """``(header bytes, pixels top-down (h, w, 4))`` of an image file."""
        with open(path, "rb") as fh:
            head = fh.read(256)
        img = bpy.data.images.load(path, check_existing=False)
        try:
            w, h = img.size
            px = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            return head, px.reshape(h, w, 4)[::-1]
        finally:
            bpy.data.images.remove(img)

    pr_src = pr_float_image(os.path.join(tmp_dir, "pr_src.exr"), top=35.0, bottom=0.5)
    pr_hdr = os.path.join(tmp_dir, "pr_roundtrip.hdr")
    with open(pr_hdr, "wb") as fh:
        fh.write(LightmapWebExport.encode_hdr_radiance(pr_src))
    pr_head, pr_px = pr_read(pr_hdr)
    check(
        "encode_hdr_radiance writes Radiance, rows top-down, linear and unclipped",
        pr_head.startswith(b"#?RADIANCE")
        and b"-Y 8 +X 16" in pr_head
        and abs(float(pr_px[0, 0, 0]) - 35.0) < 0.35
        and abs(float(pr_px[-1, -1, 1]) - 0.5) < 0.005,
        (pr_head[:60], float(pr_px[0, 0, 0]), float(pr_px[-1, -1, 1])),
    )

    # A real bake of a small lit room with its probe, exported natively.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pr_dir = os.path.join(tmp_dir, "probe_room")
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.5))
    pr_room = bpy.context.active_object
    pr_room.name = "prRoom"
    pr_room.scale = (6.0, 6.0, 3.0)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.5, 1.0, 0.5))
    pr_crate = bpy.context.active_object
    pr_crate.name = "prCrate"
    bpy.ops.object.light_add(type="POINT", location=(0.0, 0.0, 2.6))
    bpy.context.active_object.data.energy = 300.0
    for obj in (pr_room, pr_crate):
        btk.assign_mat(obj, btk.create_mat("standard", name=f"{obj.name}_mat"))
    pr_result = LightmapBaker(
        resolution=64, samples=4, denoise=False, device="CPU"
    ).bake([pr_room, pr_crate], packing="per_object", output_dir=pr_dir)
    pr_record = LightmapRecords.probe() or {}
    check(
        "the bake captured and recorded the room's probe",
        bool(pr_result.probe)
        and pr_record.get("map") == os.path.basename(pr_result.probe),
        (pr_result.probe, pr_record),
    )

    def pr_probe_of(path):
        """The GLB's ``lightmaps`` record, its probe entry and the bytes of the
        bufferView named by the probe's file -- the record in its root
        ``extras``, as a finished file holds it."""
        with ptk.MeshConvert.open_glb(path) as edit:
            record = (edit.gltf.get("extras") or {}).get(ptk.SceneRecords.LIGHTMAPS.key)
            entry = (record or {}).get("probe")
            index = ptk.SceneRecords.GLB_MEDIA.view(edit.gltf, (entry or {}).get("map"))
            if not entry or index is None:
                return entry, b"", record
            view = edit.gltf["bufferViews"][index]
            if (view.get("extras") or {}).get("mimeType") != ptk.SceneRecords.GLB_MEDIA.radiance:
                return entry, b"", record
            start = int(view.get("byteOffset", 0))
            data = bytes(edit.bin_data[start : start + int(view["byteLength"])])
        return entry, data, record

    pr_web = LightmapWebExport()
    pr_glb = os.path.join(tmp_dir, "probe_room.glb")
    with pr_web.wired_for_export() as pr_manifest:
        pr_web.export_glb(pr_glb, manifest=pr_manifest, texture_max_size=None)
    pr_entry, pr_bytes, pr_web_manifest = pr_probe_of(pr_glb)
    check(
        "a native GLB's record carries the probe, its file riding as Radiance "
        "under its name beside the maps",
        bool(pr_entry)
        and bool((pr_web_manifest or {}).get("objects"))
        and pr_bytes.startswith(b"#?RADIANCE"),
        (pr_entry, pr_bytes[:40]),
    )
    pr_unit = float((pr_web_manifest or {}).get("unit_scale") or 0.0)
    pr_place = [c * pr_unit for c in (pr_entry or {}).get("position") or ()]
    pr_box = (pr_entry or {}).get("box") or {}
    check(
        "...its capture point and box in the record's units, metres at its "
        "unit_scale (the native writer's: 1), in the GLB's own Y-up axes",
        bool(pr_entry)
        and pr_unit == 1.0
        and all(
            abs(a - b) < 1e-6
            for a, b in zip(pr_entry.get("position") or (), pr_record["position"])
        )
        and abs(pr_place[1] - 1.5) < 0.05  # eye level, half of 3 m
        and bool(pr_box)
        and abs(pr_box["min"][1]) < 0.05  # the floor, Blender's z = 0
        and abs(pr_box["max"][1] - 3.0) < 0.05,  # the ceiling
        pr_entry,
    )
    _pr_glb_hdr = os.path.join(tmp_dir, "probe_from_glb.hdr")
    with open(_pr_glb_hdr, "wb") as fh:
        fh.write(pr_bytes)
    _, pr_glb_px = pr_read(_pr_glb_hdr)
    _, pr_exr_px = pr_read(pr_result.probe)
    check(
        "...and its pixels are the probe the bake wrote (RGBE, within 2%)",
        pr_glb_px.shape == pr_exr_px.shape
        and float(
            np.abs(pr_glb_px[..., :3] - pr_exr_px[..., :3]).mean()
            / max(float(pr_exr_px[..., :3].mean()), 1e-6)
        )
        < 0.02,
        (pr_glb_px.shape, pr_exr_px.shape),
    )
    # The probe is 2:1 with the ceiling's light on its top rows: the light
    # (at z 2.6 over the room's middle) reads brightest above the horizon.
    _rows = pr_exr_px[..., :3].mean(axis=(1, 2))
    check(
        "the probe's brightest rows are its top half (+Z, the light, is up)",
        pr_exr_px.shape[1] == 2 * pr_exr_px.shape[0]
        and int(np.argmax(_rows)) < pr_exr_px.shape[0] // 2,
        int(np.argmax(_rows)),
    )

    # The route tentacle's scene slot takes: a direct export inside the bracket.
    pr_direct = os.path.join(tmp_dir, "probe_direct.glb")
    with pr_web.wired_for_export(glb_path=pr_direct):
        with btk.window_context_override():
            bpy.ops.export_scene.gltf(
                filepath=pr_direct, export_format="GLB", export_extras=True
            )
    pr_direct_entry, _bytes, _m = pr_probe_of(pr_direct)
    check(
        "a direct export inside wired_for_export(glb_path=) carries the probe too",
        bool(pr_direct_entry) and _bytes.startswith(b"#?RADIANCE"),
        pr_direct_entry,
    )
    _views_before = len(glb_json(pr_direct).get("bufferViews") or [])
    check(
        "...and a second embed adds nothing",
        bool(pr_web.embed_media(pr_direct))
        and len(glb_json(pr_direct).get("bufferViews") or []) == _views_before,
        _views_before,
    )
    check(
        "a GLB that is not there takes nothing, and nothing raises",
        pr_web.embed_media(os.path.join(tmp_dir, "missing.glb")) == [],
    )
    LightmapRecords.revert()
    pr_reverted = os.path.join(tmp_dir, "probe_reverted.glb")
    with pr_web.wired_for_export(glb_path=pr_reverted) as pr_none:
        with btk.window_context_override():
            bpy.ops.export_scene.gltf(
                filepath=pr_reverted, export_format="GLB", export_extras=True
            )
    pr_rev_entry, pr_rev_bytes, pr_rev_record = pr_probe_of(pr_reverted)
    check(
        "a scene with no probe (its bake reverted) carries no record and no probe",
        LightmapRecords.probe() is None
        and pr_none is None
        and pr_rev_record is None
        and not pr_rev_bytes,
        (pr_none, pr_rev_record),
    )

    # --- the FBX -> GLB route, built inside Blender ---------------------------
    # The Scene Exporter's GLB and the WebXR preview push build through
    # ptk.GlbPipeline IN this process, and Blender's Python ships no cv2 -- the
    # only float-image codec the pipeline had. Measured (2026-10-17, factory
    # Blender 5.1, and the user's 5.1 profile too): both GLBs shipped unlit and
    # without the probe, warned "OpenCV (cv2) is required". The host hands the
    # pipeline its own HDR codec (Blender's image writer). cv2 is forced absent
    # here, so the check holds on a machine that has it.
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter

    def gr_web_of(path):
        """``(record, the objects it lights with their maps riding, probe bytes)``
        of a GLB -- the record in its root extras, each file it names read from
        the bufferView of its name."""
        media = ptk.SceneRecords.GLB_MEDIA
        with ptk.MeshConvert.open_glb(path) as edit:
            record = (
                ptk.SceneRecords.glb_block(edit.gltf, ptk.SceneRecords.LIGHTMAPS) or {}
            )
            lit = sorted(
                edit.gltf["nodes"][i]["name"]
                for i, entry in ptk.MeshConvert.lit_nodes(edit.gltf).items()
                if (
                    edit.gltf["bufferViews"][media.view(edit.gltf, entry["map"])].get(
                        "extras"
                    )
                    or {}
                ).get(media.mime_type)
                in media.hdr_web
            )
            probe = record.get("probe") or {}
            data = b""
            index = media.view(edit.gltf, probe.get("map"))
            if index is not None:
                view = edit.gltf["bufferViews"][index]
                start = int(view.get("byteOffset", 0))
                data = bytes(edit.bin_data[start : start + int(view["byteLength"])])
        return record, lit, data

    bpy.ops.wm.read_factory_settings(use_empty=True)
    gr_dir = os.path.join(tmp_dir, "glb_route")
    btk.EnvUtils.set_current_workspace(gr_dir)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.5))
    gr_room = bpy.context.active_object
    gr_room.name = "grRoom"
    gr_room.scale = (6.0, 6.0, 3.0)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.5, 1.0, 0.5))
    gr_crate = bpy.context.active_object
    gr_crate.name = "grCrate"
    bpy.ops.object.light_add(type="POINT", location=(0.0, 0.0, 2.6))
    bpy.context.active_object.data.energy = 300.0
    for obj in (gr_room, gr_crate):
        btk.assign_mat(obj, btk.create_mat("standard", name=f"{obj.name}_mat"))
    gr_result = LightmapBaker(
        resolution=64, samples=4, denoise=False, device="CPU"
    ).bake(
        [gr_room, gr_crate], packing="atlas", output_dir=os.path.join(gr_dir, "maps")
    )
    check(
        "the GLB route's fixture is baked, with its probe",
        sorted(gr_result.maps) == ["grCrate", "grRoom"] and bool(gr_result.probe),
        gr_result,
    )
    gr_out = os.path.join(gr_dir, "export")
    os.makedirs(gr_out, exist_ok=True)
    gr_published = {}

    def gr_capture(glb, move=False, open_browser=None, scripts=None, labels=None):
        kept = os.path.join(gr_dir, "pushed.glb")
        shutil.copyfile(glb if isinstance(glb, str) else glb[0], kept)
        gr_published["glb"] = kept
        return {"url": "", "version": 1, "asset": kept, "parts": []}

    with mock.patch.dict(sys.modules, {"cv2": None}):
        gr_ok = SceneExporter().perform_export(
            export_dir=gr_out,
            objects=[gr_room, gr_crate],
            output_name="room",
            export_visible=True,
            tasks={"export_data_node": True, "output_format": "glb"},
        )
        gr_preview = btk.WebXrPreview()
        with mock.patch.object(gr_preview.deliverer, "publish", gr_capture):
            gr_pushed = gr_preview.push(objects=[gr_room, gr_crate], open_browser=False)
    for gr_label, gr_glb in (
        ("the Scene Exporter's GLB", os.path.join(gr_out, "room.glb")),
        ("the WebXR preview push", gr_published.get("glb")),
    ):
        gr_web, gr_lit, gr_probe = (
            gr_web_of(gr_glb) if gr_glb and os.path.isfile(gr_glb) else ({}, [], b"")
        )
        check(
            f"{gr_label}, built in a Blender with no cv2, carries its lightmaps: the "
            "record lights both objects, each map riding in an 8-bit web encoding "
            "(a WebP delivery's lossless WebP, 2026-11-07)",
            sorted(e.get("name") for e in gr_web.get("objects") or [])
            == ["grCrate", "grRoom"]
            and gr_lit == ["grCrate", "grRoom"],
            (gr_glb, gr_web.get("objects"), gr_lit),
        )
        check(
            f"...and the bake's reflection probe ({gr_label})",
            gr_probe.startswith(b"#?RADIANCE") and bool(gr_web.get("probe")),
            (gr_web.get("probe"), gr_probe[:20]),
        )
    check("...the export and the push both ran", gr_ok and bool(gr_pushed))
    LightmapRecords.revert()

    # --- a native GLB's probe stands in the GLB's own units --------------------
    # The probe record carries ``unit_scale`` = the scene's scale_length, which
    # the FBX route honours (a 1 BU = 1 cm scene's node at 100 BU converts to
    # 1.0 m). Blender's glTF exporter does NOT (measured 2026-10-17: the same
    # node writes 100), so a native GLB of a centimetre scene put its probe
    # 100x too near the origin. A native embed reads the record at the native
    # exporter's unit, 1 BU = 1 m.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.scale_length = 0.01
    us_dir = os.path.join(tmp_dir, "unit_scale")
    os.makedirs(us_dir, exist_ok=True)
    bpy.ops.mesh.primitive_cube_add(size=40.0, location=(100.0, 0.0, 50.0))
    us_cube = bpy.context.active_object
    us_cube.name = "usCube"
    btk.assign_mat(us_cube, btk.create_mat("standard", name="usCube_mat"))
    btk.UvUtils.create_lightmap_uvs([us_cube], quiet=True)
    us_map = pr_float_image(os.path.join(us_dir, "usCube_LM.exr"), top=0.5, bottom=0.5)
    LightmapRecords.commit({us_cube.name: us_map})
    us_probe = pr_float_image(os.path.join(us_dir, "us_Probe.exr"), top=2.0, bottom=1.0)
    # The record's frame: glTF's axes, scene units (the cube's own point).
    LightmapRecords.commit_probe(us_probe, (100.0, 50.0, 0.0))
    us_web = LightmapWebExport()
    us_glb = os.path.join(us_dir, "unit.glb")
    with us_web.wired_for_export() as us_manifest:
        us_web.export_glb(us_glb, manifest=us_manifest, texture_max_size=None)
    with ptk.MeshConvert.open_glb(us_glb) as edit:
        us_node = next(n for n in edit.gltf["nodes"] if n.get("name") == "usCube")
        us_block = ptk.SceneRecords.glb_block(edit.gltf, ptk.SceneRecords.LIGHTMAPS)
        us_entry = (us_block or {}).get("probe") or {}
        us_unit = float((us_block or {}).get("unit_scale") or 0.0)
    check(
        "a native GLB's probe stands where the GLB puts the scene: its record's "
        "lengths times its unit_scale (the native writer's) are the node's",
        bool(us_entry)
        and us_unit == 1.0
        and all(
            abs(a * us_unit - b) < 1e-4
            for a, b in zip(us_entry.get("position") or (), us_node["translation"])
        ),
        (us_entry.get("position"), us_unit, us_node.get("translation")),
    )
    us_fbx = LightmapRecords._record().payload
    check(
        "...and the FBX route's manifest labels it with the scene's unit (the "
        "record's one unit_scale)",
        abs(us_fbx.get("unit_scale", 0.0) - 0.01) < 1e-6
        and us_fbx["probe"]["position"] == [100.0, 50.0, 0.0]
        and "unit_scale" not in us_fbx["probe"],
        us_fbx,
    )
    LightmapRecords.revert()

    # --- an UNBAKED scene's native GLB is finished too ----------------------
    # wired_for_export touches nothing in an unbaked scene, but the file it is
    # handed is still stated as every deliverable states itself
    # (ptk.MeshConvert.finish_glb): a scene with records -- shots here, no bake
    # -- shipped its data_export carrier as a node holding them, and no
    # handoff. Added: 2026-10-20
    from blendertk.env_utils.fbx_utils import FbxUtils
    from blendertk.node_utils.data_nodes import DataNodes

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "UnbakedCube"
    FbxUtils.publish_authored({ptk.SceneRecords.SHOTS: {"shots": []}})
    check(
        "unbaked fixture: the carrier holds a record and its handoff, no bake",
        ptk.SceneRecords.HANDOFF.load(DataNodes) is not None
        and not LightmapRecords._marker_records(),
        ptk.SceneRecords.HANDOFF.load(DataNodes) is not None,
    )
    ub_glb = os.path.join(tmp_dir, "unbaked.glb")
    with LightmapWebExport().wired_for_export(glb_path=ub_glb) as ub_token:
        bpy.ops.export_scene.gltf(
            filepath=ub_glb, export_format="GLB", export_extras=True
        )
    check("an unbaked scene's wired_for_export yields None", ub_token is None)
    ub = glb_json(ub_glb)
    ub_handoff = (ub.get("extras") or {}).get("handoff")
    check(
        "...and its GLB states the handoff its carrier's record implies",
        isinstance(ub_handoff, dict)
        and ub_handoff.get("version") == ptk.SceneRecords.HANDOFF.version,
        sorted(ub.get("extras") or {}),
    )
    check(
        "...with no data_export node left",
        not any(n.get("name") == DataNodes.EXPORT for n in ub.get("nodes", [])),
        [n.get("name") for n in ub.get("nodes", [])],
    )
    check(
        "...and no record on any node",
        not node_copies(ub),
        node_copies(ub),
    )
    # A caller whose export raised gets nothing finished: the file stays as the
    # exporter left it, as in the baked branch.
    ub_raised = os.path.join(tmp_dir, "unbaked_raised.glb")
    try:
        with LightmapWebExport().wired_for_export(glb_path=ub_raised):
            bpy.ops.export_scene.gltf(
                filepath=ub_raised, export_format="GLB", export_extras=True
            )
            raise RuntimeError("the export failed after writing")
    except RuntimeError:
        pass
    ub_r = glb_json(ub_raised)
    check(
        "...but an export that raised finishes nothing",
        "handoff" not in (ub_r.get("extras") or {})
        and any(n.get("name") == DataNodes.EXPORT for n in ub_r.get("nodes", [])),
        sorted(ub_r.get("extras") or {}),
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(x.startswith("OK") for x in lines)
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for x in lines if x.startswith('OK'))}/{len(lines)})"
)
