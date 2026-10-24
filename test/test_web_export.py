"""blendertk WebXR lightmap-export headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_web_export.py

Covers LightmapWebExport (encode / carrier wiring / manifest / GLB) and the fixture-light
builder on LightUtils. Tiny resolution + samples so the real bake stays fast; the GLB is
parsed with the stdlib so the assertions are about the actual deliverable, not about what
the exporter was asked to do.

Regression anchors (each cost a debugging session to find, all measured on Blender 5.1):
  * an image's colorspace must be set BEFORE its pixels are written, or the buffer is
    discarded and the map saves pure black
  * leaving it unset instead double-applies sRGB
  * a lightmap only reaches the browser if its UV layer is referenced by a real texture
    slot — otherwise the exporter emits no TEXCOORD_1 at all
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

    # --- web texture budget exempts the lightmaps --------------------------
    # The lightmaps are loaded from disk like any other image, so a filepath check alone
    # would shrink them: a 4096 bake would silently ship at 2048 with nothing to show for
    # it. They are marked instead.
    def write_png(name, size):
        img = bpy.data.images.new(name, width=size, height=size)
        path = os.path.join(tmp_dir, f"{name}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        return bpy.data.images.load(path)

    source_img = write_png("budget_source", 128)
    lightmap_img = write_png("budget_lightmap", 128)
    lightmap_img[LightmapWebExport.LIGHTMAP_IMAGE_MARKER] = True

    LightmapWebExport(resolution=64, samples=1, device=None)._downsize_images(64)
    check(
        "web budget downsizes a source texture",
        tuple(source_img.size) == (64, 64),
        str(tuple(source_img.size)),
    )
    check(
        "...and exempts a marked lightmap",
        tuple(lightmap_img.size) == (128, 128),
        str(tuple(lightmap_img.size)),
    )
    for img in (source_img, lightmap_img):
        bpy.data.images.remove(img)

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
    # knowledge of how or whether the scene was baked.
    LightUtils.lights_from_geometry([plate], power=300.0)
    web = LightmapWebExport(resolution=64, samples=4, denoise=False, device=None)

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
        web.export_glb(
            glb_path, objects=[floor], manifest=manifest_token, texture_max_size=None
        )
    check("export writes a GLB", os.path.isfile(glb_path))

    gltf = glb_json(glb_path)
    prims = [p for m in gltf.get("meshes", []) for p in m.get("primitives", [])]
    check(
        "every primitive carries TEXCOORD_1",
        bool(prims) and all("TEXCOORD_1" in p.get("attributes", {}) for p in prims),
        f"{len(prims)} primitive(s)",
    )
    occl = [m for m in gltf.get("materials", []) if m.get("occlusionTexture")]
    check("the carrier slot is populated", bool(occl))
    check(
        "...on texCoord 1",
        bool(occl) and all(m["occlusionTexture"].get("texCoord") == 1 for m in occl),
    )
    # The file is stated as every deliverable states itself (finish_glb, since
    # 2026-10-20): the block the native exporter wrote into the first scene's
    # extras, as JSON text, now rides the ROOT's as an object.
    manifest = (gltf.get("extras") or {}).get(ptk.SceneRecords.LIGHTMAPS.key)
    check(
        "the viewer's block rides in the file's ROOT extras, as an object",
        isinstance(manifest, dict)
        and manifest.get("version") == ptk.SceneRecords.LIGHTMAPS.glb.version,
        manifest,
    )
    check(
        "...not in the scene's, where the native exporter wrote it",
        ptk.SceneRecords.LIGHTMAPS.key
        not in ((gltf.get("scenes") or [{}])[0].get("extras") or {}),
    )
    check(
        "...naming each baked material with its intensity alone (its map is the "
        "texture its carrier slot binds)",
        isinstance(manifest, dict)
        and bool(manifest.get("materials"))
        and all(set(v) == {"intensity"} for v in manifest["materials"].values()),
        json.dumps((manifest or {}).get("materials", {}))[:120],
    )
    check(
        "...in the one shape its builder states: no uv, no lighting",
        isinstance(manifest, dict)
        and manifest
        == ptk.MeshConvert.lightmaps_block(
            {k: v["intensity"] for k, v in manifest["materials"].items()},
            manifest.get("carrier"),
            manifest.get("probe"),
        ),
        manifest,
    )
    # Retired 2026-10-20 (removed in 0.21.0): the block's version is the
    # registry's, its key the record's, and it states no lighting -- the
    # handoff does.
    import warnings as _warnings

    with _warnings.catch_warnings(record=True) as _caught:
        _warnings.simplefilter("always")
        _version = LightmapWebExport.MANIFEST_VERSION
        _extras_key = LightmapWebExport.EXTRAS_KEY
        _bare = web.build_manifest({}, "occlusion", lighting={"keyLight": {}})
    _notes = [str(w.message) for w in _caught if w.category is DeprecationWarning]
    check(
        "MANIFEST_VERSION still answers -- the registry's -- and warns",
        _version == ptk.SceneRecords.LIGHTMAPS.glb.version
        and any("MANIFEST_VERSION" in n for n in _notes),
        _notes,
    )
    check(
        "...and EXTRAS_KEY the record's key, with a warning",
        _extras_key == ptk.SceneRecords.LIGHTMAPS.key
        and any("EXTRAS_KEY" in n for n in _notes),
        _notes,
    )
    check(
        "...and build_manifest's lighting is dropped, with a warning",
        "lighting" not in _bare and any("'lighting'" in n for n in _notes),
        (_bare, _notes),
    )

    # The GLB states its lightmaps once, in its ``lightmaps`` block. The bake
    # writes each object's marker (and the data_export manifest) before the web
    # encode exists -- the EXR at 1.0 beside its authoring folder (measured
    # 2026-09-15) -- so the written file drops those copies
    # (ptk.MeshConvert.finish_glb strips every record and marker off the
    # nodes, since 2026-10-20; the copies were corrected before).
    RECORD_KEYS = {spec.key for spec in ptk.SceneRecords.deliverable()} | {
        "lightmapInfo"
    }

    def floor_map(parsed, web_manifest):
        """The floor material's entry in *web_manifest*."""
        node = next(
            (n for n in parsed.get("nodes", []) if n.get("name") == "floor"), {}
        )
        if "mesh" not in node:
            return {}
        prim = parsed["meshes"][node["mesh"]]["primitives"][0]
        material = parsed["materials"][prim["material"]]["name"]
        return (web_manifest or {}).get("materials", {}).get(material, {})

    def node_copies(parsed):
        """The nodes still carrying a record or a lightmap marker."""
        return [
            n.get("name")
            for n in parsed.get("nodes", [])
            if RECORD_KEYS & set(n.get("extras") or {})
        ]

    worn = floor_map(gltf, manifest)
    check(
        "the floor's lightmap is stated in the block, at its scalar",
        "intensity" in worn,
        f"lightmaps={worn}",
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

    # The route a caller composes itself (tentacle's scene slot): a direct export
    # inside wired_for_export, which finishes the file it is given on exit.
    direct_glb = os.path.join(tmp_dir, "direct.glb")
    with web.wired_for_export(glb_path=direct_glb) as _wired:
        bpy.ops.export_scene.gltf(
            filepath=direct_glb, export_format="GLB", export_extras=True
        )
        _pngs = [
            bpy.path.abspath(i.filepath)
            for i in bpy.data.images
            if i.filepath and i.filepath.lower().endswith(".png")
        ]
    check(
        "the bracket's web PNGs are released once the export is done",
        bool(_pngs) and not any(os.path.exists(p) for p in _pngs),
        _pngs,
    )
    dgltf = glb_json(direct_glb)
    dblock = (dgltf.get("extras") or {}).get(ptk.SceneRecords.LIGHTMAPS.key)
    dworn = floor_map(dgltf, dblock if isinstance(dblock, dict) else None)
    check(
        "a direct export inside wired_for_export(glb_path=) states it once too: "
        "the block an object in ROOT extras, no record or marker on any node",
        isinstance(dblock, dict) and "intensity" in dworn and not node_copies(dgltf),
        f"lightmaps={dworn} copies={node_copies(dgltf)}",
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
            row.get("part") == "lightmaps"
            and row.get("data") == ["extras." + ptk.SceneRecords.LIGHTMAPS.key]
            for row in dhandoff.get("assembly") or []
        ),
        dhandoff and [(r.get("part"), r.get("data")) for r in dhandoff.get("assembly", [])],
    )
    check(
        "...and no carrier node is left in the file",
        not any(n.get("name") == "data_export" for n in dgltf.get("nodes", [])),
        [n.get("name") for n in dgltf.get("nodes", [])],
    )

    # The wiring is transport-only: the source materials must come back untouched.
    leftover = [
        n.name
        for m in bpy.data.materials
        if btk.MatUtils.get_node_tree(m) is not None
        for n in m.node_tree.nodes
        if n.label == "Lightmap"
    ]
    check("wiring is reverted after export", not leftover, str(leftover))

    # --- atlas packing: the rect is PER-INSTANCE ---------------------------
    # A material-atlased bake gives every object a different rect into ONE shared
    # map, so the binding cannot be a material-level property: bound without it,
    # each object samples the WHOLE atlas through its own [0,1] unwrap and wears
    # every other object's lighting -- which reads as blocky patches, not as a
    # missing map. The single-object flow above can never catch it (a solo group
    # keeps the identity rect). Twin of ptk.MeshConvert.apply_glb_lightmaps,
    # which ships the same rect as KHR_texture_transform.
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

    # One transform per object, each matching that object's own committed rect.
    import pythontk as ptk

    shipped = []
    for m in agltf.get("materials", []):
        occ = m.get("occlusionTexture") or {}
        tf = (occ.get("extensions") or {}).get("KHR_texture_transform")
        if tf:
            shipped.append((tf.get("scale"), tf.get("offset")))

    def as_gltf(rect):
        sx, sy, ox, oy = ptk.ImgUtils.flip_rect_v(rect)
        return ([round(sx, 4), round(sy, 4)], [round(ox, 4), round(oy, 4)])

    expected = sorted(as_gltf(r) for r in rects.values())
    got = sorted(
        ([round(v, 4) for v in sc], [round(v, 4) for v in off]) for sc, off in shipped
    )
    check(
        "each atlased object ships its own rect as KHR_texture_transform",
        got == expected,
        f"got {json.dumps(got)} want {json.dumps(expected)}",
    )

    # The viewer rebinds by MATERIAL NAME, so the per-instance clones have to be
    # the names the manifest carries -- otherwise every clone falls through the
    # lookup and ships as plain glTF occlusion.
    named = {
        m.get("name") for m in agltf.get("materials", []) if m.get("occlusionTexture")
    }
    check(
        "the manifest names the per-instance materials the GLB actually holds",
        bool(named) and named <= set((atlas_manifest or {}).get("materials", {})),
        f"glb {sorted(named)} vs manifest {sorted((atlas_manifest or {}).get('materials', {}))}",
    )

    # Texture bytes are the file size, so the per-instance clones must share ONE
    # image: the rect rides the texture REFERENCE (KHR_texture_transform sits on
    # the textureInfo), never a copy of the atlas per object.
    check(
        "the clones share a single embedded atlas image",
        len(agltf.get("images", [])) == 1,
        f"{len(agltf.get('images', []))} image(s), {len(agltf.get('materials', []))} material(s)",
    )

    # A material can span BOTH modes (two bakes into one scene: per-object, then
    # atlased), and then the clone must still be taken from a PRISTINE material --
    # cloning one already wired in place gives it two lightmap textures and two
    # carrier groups, of which the exporter picks one, so the atlased object ships
    # the UNTRANSFORMED binding: the exact bug the rect exists to prevent.
    mixed_png = os.path.join(tmp_dir, "mixed.png")
    mixed_img = bpy.data.images.new("mixed", 8, 8)
    mixed_img.filepath_raw = mixed_png
    mixed_img.file_format = "PNG"
    mixed_img.save()
    bpy.data.images.remove(mixed_img)

    # A slot carries a data-level AND an object-level material; the link only says
    # which is live. Overriding the slot must not cost the object the hidden one.
    spare = bpy.data.materials.new("SPARE")
    hidden_slot = pair[1].material_slots[0]
    hidden_slot.link = "OBJECT"
    hidden_slot.material = spare
    hidden_slot.link = "DATA"

    mixed_token = web.wire_lightmaps(
        {o.name: (mixed_png, 1.0) for o in pair},
        rects={
            pair[0].name: [1.0, 1.0, 0.0, 0.0],
            pair[1].name: [0.5, 0.5, 0.25, 0.25],
        },
    )
    per_clone = [
        (
            sum(
                1
                for n in bpy.data.materials[c].node_tree.nodes
                if n.label == "Lightmap"
            ),
            sum(1 for n in bpy.data.materials[c].node_tree.nodes if n.type == "GROUP"),
        )
        for c in mixed_token["copies"]
    ]
    check(
        "an atlased clone is taken from a pristine material, not a wired one",
        bool(per_clone) and all(t == (1, 1) for t in per_clone),
        f"(lightmap_tex, carrier_group) per clone: {per_clone}",
    )
    web.unwire_lightmaps(mixed_token)
    hidden_slot.link = "OBJECT"
    recovered = hidden_slot.material.name if hidden_slot.material else None
    hidden_slot.link = "DATA"
    check(
        "a material hidden behind a DATA link survives the override",
        recovered == "SPARE",
        f"object-level material came back as {recovered!r}",
    )

    # The per-object binding needs per-object materials; they must not leak.
    overrides = [
        f"{o.name}[{i}]"
        for o in pair
        for i, sl in enumerate(o.material_slots)
        if sl.link == "OBJECT"
        or (sl.material is not None and sl.material is not shared)
    ]
    check("per-instance material overrides are reverted", not overrides, str(overrides))

    # --- one material, DIFFERENT maps, no atlas ----------------------------
    # A secondary material several baked objects share (one GLASS on two machine
    # bodies) or a Per-Object bake of linked duplicates (one mesh, one material
    # behind every instance): each object has a map of its own and no rect, and a
    # glTF material carries ONE lightmap. Wired in place, the first object's map
    # rode the material and every later object sampled it through its own UV2 --
    # build_manifest could only warn "cannot be published". The twin of
    # ptk.MeshConvert.apply_glb_lightmaps' 2026-09-21 fix: the first object keeps
    # the material, each later one with a different map binds a per-object clone.
    from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET

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

    split_maps = {}
    for ob in bodies:
        path = os.path.join(tmp_dir, f"{ob.name}_lm.png")
        img = bpy.data.images.new(f"{ob.name}_lm", 8, 8)
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        split_maps[ob.name] = (path, 1.0)

    def reached(ob):
        """Basenames of the lightmaps the object's LIVE slot material samples."""
        mat = ob.material_slots[0].material
        return sorted(
            os.path.basename(bpy.path.abspath(n.image.filepath))
            for n in mat.node_tree.nodes
            if n.label == "Lightmap" and n.image is not None
        )

    split_token = web.wire_lightmaps(split_maps)
    got_maps = {ob.name: reached(ob) for ob in bodies}
    want_maps = {n: [os.path.basename(p)] for n, (p, _s) in split_maps.items()}
    check(
        "objects sharing a material but baked into different maps each reach their own",
        got_maps == want_maps,
        f"got {json.dumps(got_maps)} want {json.dumps(want_maps)}",
    )
    split_manifest = web.build_manifest(split_maps, split_token["carrier"])
    # The block names each material that carries a map, once; WHICH map is the
    # texture that material's carrier slot binds (checked in the GLB below).
    published = sorted(split_manifest.get("materials", {}))
    wearing = sorted({ob.material_slots[0].material.name for ob in bodies})
    check(
        "...and the block names every material that carries one, one per map",
        published == wearing and len(published) == len(want_maps),
        json.dumps(split_manifest.get("materials", {})),
    )
    split_glb = os.path.join(tmp_dir, "split.glb")
    # The export selects what it ships (use_selection); the artist's selection and
    # active object come back after, as FbxUtils.export / UsdUtils.export restore
    # theirs. It left *bodies* selected. Added: 2026-09-27
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = twin
    web.export_glb(
        split_glb, objects=bodies, manifest=split_manifest, texture_max_size=None
    )
    check(
        "a GLB export of objects puts the selection and active object back",
        not [o.name for o in bpy.context.view_layer.objects if o.select_get()]
        and bpy.context.view_layer.objects.active == twin,
        str([o.name for o in bpy.context.view_layer.objects if o.select_get()]),
    )
    web.unwire_lightmaps(split_token)
    sgltf = glb_json(split_glb)

    def shipped_map(node_name):
        """The image name each exported node's material samples as its lightmap."""
        node = next(
            (n for n in sgltf.get("nodes", []) if n.get("name") == node_name), None
        )
        if node is None or "mesh" not in node:
            return None
        prim = sgltf["meshes"][node["mesh"]]["primitives"][0]
        occ = sgltf["materials"][prim["material"]].get("occlusionTexture")
        if not occ:
            return None
        texture = sgltf["textures"][occ["index"]]
        # A WEBP export names its image under the extension, not `source`.
        source = texture.get("source")
        if source is None:
            source = (texture.get("extensions") or {}).get("EXT_texture_webp", {})
            source = source.get("source")
        image = sgltf["images"][source] if source is not None else {}
        return image.get("name") or image.get("uri")

    got_glb = {ob.name: shipped_map(ob.name) for ob in bodies}
    check(
        "...and the GLB ships each object with its OWN lightmap",
        all(
            (got_glb[n] or "").startswith(os.path.splitext(want_maps[n][0])[0])
            for n in got_glb
        ),
        json.dumps(got_glb),
    )

    def split_leftovers():
        """What is left of the split wiring: re-linked or foreign slots, lightmap
        nodes in the glass, and clones of it."""
        return (
            [
                f"{o.name}[{i}]"
                for o in bodies
                for i, sl in enumerate(o.material_slots)
                if sl.link == "OBJECT" or sl.material is not glass
            ]
            + [n.name for n in glass.node_tree.nodes if n.label == "Lightmap"]
            + [m.name for m in bpy.data.materials if m.name.startswith("M_Glass~")]
        )

    split_left = split_leftovers()
    check("...and the split wiring is fully reverted", not split_left, str(split_left))

    # --- one material, a BAKED object and one the bake never saw ------------
    # Wired in place, the material carried the wall's lightmap to everything
    # wearing it, so the unbaked prop exported lit by the wall's bake (through
    # its own UV2) and the viewer counted the material as lightmapped. The twin
    # of ptk.MeshConvert.apply_glb_lightmaps' 2026-09-24 fix: the baked object
    # binds a clone, and the material stays as authored for the prop.
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
    wall_png = os.path.join(tmp_dir, "paint_wall_lm.png")
    wall_img = bpy.data.images.new("paint_wall_lm", 8, 8)
    wall_img.filepath_raw = wall_png
    wall_img.file_format = "PNG"
    wall_img.save()
    bpy.data.images.remove(wall_img)

    paint_token = web.wire_lightmaps({painted["wall"].name: (wall_png, 1.0)})
    got_paint = {role: reached(ob) for role, ob in painted.items()}
    check(
        "an unbaked object sharing a baked one's material is not lit by its bake",
        got_paint == {"wall": ["paint_wall_lm.png"], "prop": []},
        json.dumps(got_paint),
    )
    web.unwire_lightmaps(paint_token)
    paint_left = [n.name for n in paint.node_tree.nodes if n.label == "Lightmap"] + [
        m.name for m in bpy.data.materials if m.name.startswith("M_Paint~")
    ]
    check("...and that wiring is fully reverted", not paint_left, str(paint_left))

    # --- a wiring that fails part-way takes back what it did ---------------
    # wire_lightmaps edits the LIVE scene (tentacle's GLB export wraps the
    # artist's own .blend in wired_for_export), and it takes every clone and
    # re-links its slot before the in-place pass. An error the per-material
    # guard does not catch -- a slot that refuses the re-link, an interrupt --
    # left those clones and overrides in the scene, to be saved with it.
    class _Escape(BaseException):
        """Raised past wire_lightmaps' per-material ``except Exception``."""

    real_wire = LightmapWebExport._wire_material

    def wire_then_escape(self, material, *args, **kwargs):
        if material.name == glass.name:  # the in-place pass: every clone is made
            raise _Escape()
        return real_wire(self, material, *args, **kwargs)

    escaped = False
    try:
        with mock.patch.object(LightmapWebExport, "_wire_material", wire_then_escape):
            web.wire_lightmaps(split_maps)
    except _Escape:
        escaped = True
    escape_left = split_leftovers()
    check(
        "a wiring that fails part-way takes back what it had done",
        escaped and not escape_left,
        f"escaped={escaped} left={escape_left}",
    )

    # ...and so does the export bracket, when its manifest cannot be built.
    def no_manifest(*_args, **_kwargs):
        raise RuntimeError("no manifest")

    bracket_raised = False
    try:
        with mock.patch.object(LightmapWebExport, "build_manifest", no_manifest):
            with web.wired_for_export(objects=[floor]):
                pass
    except RuntimeError:
        bracket_raised = True
    floor_left = [
        n.name
        for sl in floor.material_slots
        if sl.material is not None and sl.material.node_tree is not None
        for n in sl.material.node_tree.nodes
        if n.label == "Lightmap"
    ]
    check(
        "wired_for_export takes its wiring back when the manifest fails",
        bracket_raised and not floor_left,
        f"raised={bracket_raised} left={floor_left}",
    )

    # --- the viewer's side of the contract ---------------------------------
    # The exporter and pythontk's WebXR viewer agree by convention, not by an interface,
    # so nothing but this catches a drift: edit the carrier here or the binder there and
    # the lightmap silently stops being applied (or is applied twice, or linear).
    preview_dir = os.path.join(MONO, "pythontk", "pythontk", "net_utils", "preview")
    viewer_path = os.path.join(preview_dir, "viewer.html")
    if os.path.isfile(viewer_path) and manifest:
        # The viewer is viewer.html PLUS the modules it loads (``kernel/``,
        # ``features/`` since the 2026-10-03 split): the binder lives in
        # ``kernel/lightmaps.js`` now, and the contract is wherever it is.
        import glob

        sources = [viewer_path] + sorted(
            glob.glob(os.path.join(preview_dir, "**", "*.js"), recursive=True)
        )
        viewer = "\n".join(open(p, encoding="utf-8").read() for p in sources)
        slot = {"occlusion": "aoMap", "emissive": "emissiveMap"}.get(
            manifest["carrier"]
        )
        for label, cond in (
            (
                "viewer reads the key the exporter writes",
                ptk.SceneRecords.LIGHTMAPS.key in viewer,
            ),
            (
                f"viewer knows the {manifest['carrier']!r} carrier's three.js slot",
                bool(slot) and slot in viewer,
            ),
            ("viewer rebinds it as a lightMap", "material.lightMap" in viewer),
            ("viewer applies the recorded intensity", "lightMapIntensity" in viewer),
            # The block names no encoding (since 2026-10-20): a carrier holds
            # 8-bit sRGB alone, which the viewer sets outright.
            (
                "viewer overrides colorSpace for the sRGB map",
                "colorSpace = THREE.SRGBColorSpace" in viewer,
            ),
            # The block names no UV set (since 2026-10-20): the carrier
            # slot's own texCoord does, which the loader applies -- so the
            # viewer must not override it.
            (
                "viewer keeps the UV set the carrier slot binds",
                "texture.channel = uv" not in viewer and "manifest.uv" not in viewer,
            ),
            (
                "viewer clears the carrier so it is not applied twice",
                "material[slot] = null" in viewer,
            ),
            # The key light goes off for a pre-lit model; the ENVIRONMENT does
            # not, it is confined to its specular term on the baked materials
            # instead. Killing it outright (what this asserted until
            # 2026-08-10) takes normal maps and specular with it, because
            # three.js adds lightmap irradiance through a Lambert term with no
            # normal dependence — leaving nothing in the render that samples
            # the normal at all.
            # Asserted as the BEHAVIOUR -- zero when lightmapped, the standing
            # key intensity otherwise -- not as one literal right-hand side.
            # Pinning the spelling broke the moment the viewer routed its numbers
            # through a published rendering policy (DEFAULT_KEY_INTENSITY ->
            # policy.keyIntensity, the same value via POLICY_FALLBACK) -- a refactor
            # this cross-package check has no business failing on, which is exactly
            # the reasoning its environment sibling below already carries. The
            # default is still named separately so it cannot quietly disappear.
            (
                "viewer drops its key light for a pre-lit model",
                bool(
                    re.search(
                        r"keyLight\.intensity\s*=\s*lightmapped\s*\?\s*0\s*:", viewer
                    )
                )
                and "DEFAULT_KEY_INTENSITY" in viewer,
            ),
            # Asserted as "the environment level is still spent, and the line
            # that would add its diffuse to a bake is the one named for
            # removal" rather than as one literal assignment: where the shader
            # edit is installed is an implementation detail this cross-package
            # check has no business pinning. (Until 2026-09-21 the viewer
            # dimmed the environment per baked material instead -- diffuse and
            # specular together -- and a lightmapped room rendered washed out.)
            (
                "viewer keeps the environment for a pre-lit model, specular only",
                "scene.environmentIntensity" in viewer
                and "getIBLIrradiance( geometryNormal )" in viewer,
            ),
        ):
            check(label, cond)
    else:
        check("viewer contract checked", False, "preview/viewer.html not found")

    # The budget exemption above is only real if the production path actually marks the
    # image — the unit test set the marker by hand. The PNGs are the committed maps
    # encoded (encode_for_web: each map's stem); the block names no map any more.
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

    baked_names = {
        os.path.splitext(os.path.basename(info["map"]))[0] + ".png"
        for _obj, info in LightmapRecords._marker_records()
        if info.get("map")
    }
    marked = [
        img.name
        for img in bpy.data.images
        if os.path.basename(bpy.path.abspath(img.filepath or "")) in baked_names
        and img.get(LightmapWebExport.LIGHTMAP_IMAGE_MARKER)
    ]
    check(
        "wire_lightmaps marks the lightmap image for the budget",
        bool(marked),
        str(marked),
    )

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
        """The GLB's ``lightmaps`` probe entry and its bytes -- the block in its
        root ``extras``, as a finished file holds it."""
        with ptk.MeshConvert.open_glb(path) as edit:
            web_manifest = (edit.gltf.get("extras") or {}).get(
                ptk.SceneRecords.LIGHTMAPS.key
            )
            entry = (web_manifest or {}).get("probe")
            if not entry:
                return None, b"", web_manifest
            view = edit.gltf["bufferViews"][entry["bufferView"]]
            start = int(view.get("byteOffset", 0))
            data = bytes(edit.bin_data[start : start + int(view["byteLength"])])
        return entry, data, web_manifest

    pr_web = LightmapWebExport()
    pr_glb = os.path.join(tmp_dir, "probe_room.glb")
    with pr_web.wired_for_export() as pr_manifest:
        pr_web.export_glb(pr_glb, manifest=pr_manifest, texture_max_size=None)
    pr_entry, pr_bytes, pr_web_manifest = pr_probe_of(pr_glb)
    check(
        "a native GLB carries lightmaps.probe beside its lightmaps",
        bool(pr_entry)
        and bool((pr_web_manifest or {}).get("materials"))
        and pr_bytes.startswith(b"#?RADIANCE"),
        (pr_entry, pr_bytes[:40]),
    )
    check(
        "...its capture point and box in metres, in the GLB's own Y-up axes",
        bool(pr_entry)
        and pr_entry.get("position") == [round(c, 6) for c in pr_record["position"]]
        and abs(pr_entry["position"][1] - 1.5) < 0.05  # eye level, half of 3 m
        and pr_entry.get("box") is not None
        and abs(pr_entry["box"]["min"][1]) < 0.05  # the floor, Blender's z = 0
        and abs(pr_entry["box"]["max"][1] - 3.0) < 0.05,  # the ceiling
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
    check(
        "a GLB that is not there takes no probe, and nothing raises",
        pr_web.embed_probe(os.path.join(tmp_dir, "missing.glb")) is None,
    )
    LightmapRecords.revert()
    check(
        "a scene with no probe (its bake reverted) embeds none",
        LightmapRecords.probe() is None and pr_web.embed_probe(pr_direct) is None,
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
        """``(lightmaps, materials wired on TEXCOORD_1, probe bytes)`` of a GLB."""
        with ptk.MeshConvert.open_glb(path) as edit:
            web = (
                ptk.SceneRecords.glb_block(edit.gltf, ptk.SceneRecords.LIGHTMAPS) or {}
            )
            wired = sorted(
                m.get("name")
                for m in edit.gltf.get("materials", [])
                if (m.get("occlusionTexture") or {}).get("texCoord") == 1
            )
            probe = web.get("probe") or {}
            data = b""
            if isinstance(probe.get("bufferView"), int):
                view = edit.gltf["bufferViews"][probe["bufferView"]]
                start = int(view.get("byteOffset", 0))
                data = bytes(edit.bin_data[start : start + int(view["byteLength"])])
        return web, wired, data

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
        gr_web, gr_wired, gr_probe = (
            gr_web_of(gr_glb) if gr_glb and os.path.isfile(gr_glb) else ({}, [], b"")
        )
        check(
            f"{gr_label}, built in a Blender with no cv2, carries its lightmaps",
            sorted(gr_web.get("materials") or {}) == ["grCrate_mat", "grRoom_mat"]
            and gr_wired == ["grCrate_mat", "grRoom_mat"],
            (gr_glb, sorted(gr_web.get("materials") or {}), gr_wired),
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
    check(
        "a native GLB's probe stands where the GLB puts the scene (its own units)",
        bool(us_entry)
        and all(
            abs(a - b) < 1e-4
            for a, b in zip(us_entry.get("position") or (), us_node["translation"])
        ),
        (us_entry.get("position"), us_node.get("translation")),
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
