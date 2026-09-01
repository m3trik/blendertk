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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


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
    check("lights_from_geometry creates one light per fixture", len(created) == 1, str(created))
    light = bpy.data.objects.get(created[0]) if created else None
    check("...as an AREA light", light is not None and light.data.type == "AREA")
    check("...with the fixture's power", light is not None and abs(light.data.energy - 150.0) < 1e-6)
    # primitive_cube_add(size=1) is one unit across, so the scaled plate is
    # 2.0 x 0.5 x 0.02: the thin axis is dropped and the remaining two become the
    # rectangle (larger -> size, smaller -> size_y).
    check(
        "...sized to the plate's broad face",
        light is not None and abs(light.data.size - 2.0) < 0.05 and abs(light.data.size_y - 0.5) < 0.05,
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
    check("...and leaves none behind", not [o for o in bpy.data.objects if o.type == "LIGHT"])

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
    check("an explicit `toward` above the plates aims them UP", all(z > 0.9 for z in up), str(up))

    # Colour temperature rides Blender's own blackbody, not a hand-rolled table.
    LightUtils.remove_lights()
    warm = LightUtils.lights_from_geometry(grid[:1], power=50.0, kelvin=4000)
    lamp = bpy.data.objects[warm[0]].data
    check(
        "kelvin sets Blender's native light temperature",
        bool(getattr(lamp, "use_temperature", False)) and abs(lamp.temperature - 4000) < 1,
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
    check("...and reports the divisor", scalar is not None and abs(scalar - 1.0) < 1e-3, str(scalar))

    back = bpy.data.images.load(png)
    back.colorspace_settings.name = "Non-Color"
    rb = np.empty(len(back.pixels), dtype=np.float32)
    back.pixels.foreach_get(rb)
    bpy.data.images.remove(back)
    got = rb.reshape(-1, 4)[:, 0]
    want = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
    check(
        "...sRGB-encoded exactly once (not black, not double-encoded)",
        bool(np.all(np.abs(want - got) < 0.01)),
        f"want={np.round(want,3).tolist()} got={np.round(got,3).tolist()}",
    )

    # A map with a large HDR range must not simply clip: the divisor carries the range.
    hdr_src = os.path.join(tmp_dir, "hdr.exr")
    values = np.concatenate([np.full(99, 0.5, np.float32), np.array([100.0], np.float32)])
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
        return float(np.abs(4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1]
                            - a[1:-1, :-2] - a[1:-1, 2:]).mean())

    before_grain = grain(noisy_path)
    denoised = TextureBaker.denoise_image(noisy_path)
    if denoised:
        check("denoise_image reduces grain", grain(denoised) < before_grain * 0.6,
              f"{before_grain:.4f} -> {grain(denoised):.4f}")
    else:
        check("denoise_image degrades without losing the map", os.path.isfile(noisy_path))

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
    uv_rects = {n: r for n, (_p, r) in packed.items()} if packed else {}
    web.baker.commit_lightmap(atlas_map, uv_rects=uv_rects)

    glb_path = os.path.join(tmp_dir, "scene.glb")
    with web.wired_for_export() as manifest_token:
        check("wired_for_export self-feeds from the committed markers",
              bool(manifest_token), str(manifest_token)[:120])
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
    extras = (gltf.get("scenes") or [{}])[0].get("extras") or {}
    raw = extras.get(LightmapWebExport.EXTRAS_KEY)
    manifest = json.loads(raw) if isinstance(raw, str) else raw
    check("the viewer manifest rides in extras", bool(manifest))
    check(
        "...naming the baked material with its intensity",
        bool(manifest)
        and bool(manifest.get("materials"))
        and all("intensity" in v and "map" in v for v in manifest["materials"].values()),
        json.dumps(manifest.get("materials", {}))[:120] if manifest else "",
    )

    # The wiring is transport-only: the source materials must come back untouched.
    leftover = [
        n.name
        for m in bpy.data.materials
        if m.use_nodes and m.node_tree
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
    shared.use_nodes = True
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
    named = {m.get("name") for m in agltf.get("materials", []) if m.get("occlusionTexture")}
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
        rects={pair[0].name: [1.0, 1.0, 0.0, 0.0],
               pair[1].name: [0.5, 0.5, 0.25, 0.25]},
    )
    per_clone = [
        (
            sum(1 for n in bpy.data.materials[c].node_tree.nodes if n.label == "Lightmap"),
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
        if sl.link == "OBJECT" or (sl.material is not None and sl.material is not shared)
    ]
    check("per-instance material overrides are reverted", not overrides, str(overrides))

    # --- the viewer's side of the contract ---------------------------------
    # The exporter and pythontk's WebXR viewer agree by convention, not by an interface,
    # so nothing but this catches a drift: edit the carrier here or the binder there and
    # the lightmap silently stops being applied (or is applied twice, or linear).
    viewer_path = os.path.join(
        MONO, "pythontk", "pythontk", "net_utils", "preview_viewer.html"
    )
    if os.path.isfile(viewer_path) and manifest:
        viewer = open(viewer_path, encoding="utf-8").read()
        slot = {"occlusion": "aoMap", "emissive": "emissiveMap"}.get(manifest["carrier"])
        for label, cond in (
            ("viewer reads the key the exporter writes",
             LightmapWebExport.EXTRAS_KEY in viewer),
            (f"viewer knows the {manifest['carrier']!r} carrier's three.js slot",
             bool(slot) and slot in viewer),
            ("viewer rebinds it as a lightMap", "material.lightMap" in viewer),
            ("viewer applies the recorded intensity", "lightMapIntensity" in viewer),
            ("viewer overrides colorSpace for the sRGB map",
             "SRGBColorSpace" in viewer and "encoding === 'srgb'" in viewer),
            ("viewer samples the manifest's UV channel", "texture.channel = uv" in viewer),
            ("viewer clears the carrier so it is not applied twice",
             "material[slot] = null" in viewer),
            # The key light goes off for a pre-lit model; the ENVIRONMENT does
            # not, it is dimmed per material instead. Killing it outright (what
            # this asserted until 2026-08-10) takes normal maps and specular
            # with it, because three.js adds lightmap irradiance through a
            # Lambert term with no normal dependence — leaving nothing in the
            # render that samples the normal at all.
            # Asserted as the BEHAVIOUR -- zero when lightmapped, the standing
            # key intensity otherwise -- not as one literal right-hand side.
            # Pinning the spelling broke the moment the viewer routed its numbers
            # through a published rendering policy (DEFAULT_KEY_INTENSITY ->
            # policy.keyIntensity, the same value via POLICY_FALLBACK) -- a refactor
            # this cross-package check has no business failing on, which is exactly
            # the reasoning its environment sibling below already carries. The
            # default is still named separately so it cannot quietly disappear.
            ("viewer drops its key light for a pre-lit model",
             bool(re.search(r"keyLight\.intensity\s*=\s*lightmapped\s*\?\s*0\s*:",
                            viewer))
             and "DEFAULT_KEY_INTENSITY" in viewer),
            # Asserted as "the dimming factor exists and drives envMapIntensity"
            # rather than as one literal assignment: whether the write happens at
            # bind time or in the lighting pass is an implementation detail this
            # cross-package check has no business pinning.
            ("viewer dims, rather than kills, the environment it keeps",
             "LIGHTMAP_ENV_INTENSITY" in viewer and "envMapIntensity" in viewer),
        ):
            check(label, cond)
    else:
        check("viewer contract checked", False, "preview_viewer.html not found")

    # The budget exemption above is only real if the production path actually marks the
    # image — the unit test set the marker by hand.
    baked_names = {
        v.get("map") for v in (manifest_token or {}).get("materials", {}).values()
    }
    marked = [
        img.name
        for img in bpy.data.images
        if os.path.basename(bpy.path.abspath(img.filepath or "")) in baked_names
        and img.get(LightmapWebExport.LIGHTMAP_IMAGE_MARKER)
    ]
    check("wire_lightmaps marks the lightmap image for the budget", bool(marked), str(marked))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(x.startswith("OK") for x in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for x in lines if x.startswith('OK'))}/{len(lines)})")
