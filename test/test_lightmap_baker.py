"""blendertk lightmap baker headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_lightmap_baker.py

Exercises the engine end-to-end (create_lightmap_uvs → Cycles bake → commit → revert) and the
Unity bridge (DataNodes manifest). Tiny resolution / samples so the real bake stays fast.
"""
import sys, os, json, tempfile, shutil, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


tmp_dir = tempfile.mkdtemp(prefix="btk_lm_")
try:
    import bpy
    import blendertk as btk
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

    # --- presets -----------------------------------------------------------
    store = LightmapBaker.preset_store()
    check("built-in presets ship", set(store.list()) >= {"preview", "quest", "desktop"},
          f"{store.list()}")
    # Cycles-appropriate sampling: the presets originally mirrored mayatk's Arnold
    # tiers (2/4/8 AA samples), which as CYCLES path-tracing samples are pure noise.
    baker = LightmapBaker.from_preset("quest")
    check("from_preset reads the dials", baker.resolution == 1024 and baker.samples == 256,
          f"{baker.resolution}/{baker.samples}")
    baker = LightmapBaker.from_preset("preview", resolution=64, samples=1)
    check("overrides win over the preset", baker.resolution == 64 and baker.samples == 1)
    baker = LightmapBaker.from_preset("preview", denoise=False, device="CPU")
    check("constructor-arg overrides pass through from_preset",
          baker.denoise is False and baker.device == "CPU",
          f"denoise={baker.denoise} device={baker.device}")

    # --- a cube with a material under the factory light --------------------
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
    mat = btk.create_mat("standard", name="cube_mat")
    btk.assign_mat(cube, mat)

    # --- DataNodes bridge --------------------------------------------------
    btk.DataNodes.set_export_string("probe", "hello")
    check("DataNodes roundtrips a string", btk.DataNodes.get_export_string("probe") == "hello")
    check("data_export Empty exists", bpy.data.objects.get("data_export") is not None)
    btk.DataNodes.set_export_string("probe", "")
    check("clearing leaves the carrier, reads back None",
          bpy.data.objects.get("data_export") is not None
          and btk.DataNodes.get_export_string("probe") is None)

    # --- lightmap UVs ------------------------------------------------------
    btk.create_lightmap_uvs([cube])
    check("lightmap UV layer created (2nd channel)", len(cube.data.uv_layers) >= 2,
          f"{[l.name for l in cube.data.uv_layers]}")
    check("find_lightmap_uv_set detects it", btk.find_lightmap_uv_set(cube) is not None)
    # Idempotent: a second call reuses, doesn't pile on layers.
    n = len(cube.data.uv_layers)
    btk.create_lightmap_uvs([cube])
    check("create_lightmap_uvs is idempotent", len(cube.data.uv_layers) == n)

    # --- lighting-only bake + commit (the default path) -------------------
    result = baker.bake_separated([cube], output_dir=tmp_dir, suffix="_Lightmap")
    check("bake produced a map", cube.name in result, f"{result}")
    path = result.get(cube.name, "")
    check("EXR written to disk", path and os.path.isfile(path) and os.path.getsize(path) > 0,
          path)
    check("name follows the affix", path.endswith("_Lightmap.exr"), os.path.basename(path))
    check("material kept (non-destructive bake)",
          any(s.material is mat for s in cube.material_slots))

    baker.commit_lightmap(result, intensity=1.0)
    check("commit stamps the marker", LightmapBaker.LIGHTMAP_INFO_PROP in cube)
    raw = btk.DataNodes.get_export_string("lightmap_metadata")
    check("manifest published to data_export", bool(raw), repr(raw)[:80])
    manifest = json.loads(raw) if raw else {}
    rec = (manifest.get("objects") or [{}])[0]
    check("manifest record has camelCase keys + uvIndex 1",
          rec.get("name") == cube.name and rec.get("uvIndex") == 1
          and "scaleOffset" in rec and os.path.basename(path) == rec.get("map"),
          f"{rec}")

    # --- revert (subtractive) ---------------------------------------------
    reverted = baker.revert([cube])
    check("revert clears the marker", LightmapBaker.LIGHTMAP_INFO_PROP not in cube
          and cube.name in reverted)
    check("manifest cleared when nothing remains",
          (btk.DataNodes.get_export_string("lightmap_metadata") or "") == "")

    # --- intensity applied into the texels, once per unique file ----------
    # (mirror of mayatk: Unity ignores the manifest intensity field, so a
    # non-1.0 value must be baked into the map -- shared files scale ONCE.)
    import numpy as np

    ipath = os.path.join(tmp_dir, "intensity_probe.exr")
    src = bpy.data.images.new("intSrc", width=4, height=4, alpha=True,
                              float_buffer=True)
    src.pixels.foreach_set(
        np.tile(np.array([0.25, 0.25, 0.25, 1.0], np.float32), 16)
    )
    src.filepath_raw = ipath
    src.file_format = "OPEN_EXR"
    src.save()
    bpy.data.images.remove(src)

    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    cube_b = bpy.context.active_object
    baker.commit_lightmap({cube.name: ipath, cube_b.name: ipath}, intensity=2.0)
    reload = bpy.data.images.load(ipath)
    ibuf = np.empty(len(reload.pixels), dtype=np.float32)
    reload.pixels.foreach_get(ibuf)
    bpy.data.images.remove(reload)
    check("intensity x2 applied once (0.25 -> 0.5, not 1.0)",
          abs(float(ibuf.reshape(-1, 4)[0, 0]) - 0.5) < 1e-3,
          f"{ibuf.reshape(-1, 4)[0, :3]}")
    raw_i = btk.DataNodes.get_export_string("lightmap_metadata")
    recs = (json.loads(raw_i).get("objects") if raw_i else []) or []
    check("manifest records intensity 2.0 for both objects",
          len(recs) == 2 and all(r.get("intensity") == 2.0 for r in recs),
          f"{[(r.get('name'), r.get('intensity')) for r in recs]}")
    baker.revert([cube, cube_b])
    check("intensity commit reverts clean",
          (btk.DataNodes.get_export_string("lightmap_metadata") or "") == "")

    # --- uv_rects marker mirror (mayatk pack_atlas bookkeeping) ------------
    # (mirror of mayatk commit_lightmap: a non-identity uv_rect is recorded on
    # the marker as ``uvRect`` -- revert bookkeeping only -- while the manifest
    # keeps publishing an identity scaleOffset; identity rects record nothing.)
    rect = [0.5, 0.5, 0.25, 0.25]
    baker.commit_lightmap(
        {cube.name: ipath, cube_b.name: ipath},
        uv_rects={cube.name: rect, cube_b.name: [1.0, 1.0, 0.0, 0.0]},
    )
    info_a = json.loads(cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    info_b = json.loads(cube_b[LightmapBaker.LIGHTMAP_INFO_PROP])
    check("uv_rects recorded on the marker (uvRect)", info_a.get("uvRect") == rect,
          f"{info_a}")
    check("identity uv_rect records no uvRect key", "uvRect" not in info_b,
          f"{info_b}")
    raw_r = btk.DataNodes.get_export_string("lightmap_metadata")
    recs_r = (json.loads(raw_r).get("objects") if raw_r else []) or []
    check("manifest scaleOffset stays identity with uv_rects",
          recs_r and all(r.get("scaleOffset") == [1.0, 1.0, 0.0, 0.0]
                         and "uvRect" not in r for r in recs_r),
          f"{recs_r}")
    baker.revert([cube, cube_b])
    check("uv_rects commit reverts clean",
          (btk.DataNodes.get_export_string("lightmap_metadata") or "") == "")

    # --- atlas by material: 2 objects sharing a material -> one shared EXR + repacked UVs ---
    for nm in ("AtlasA", "AtlasB"):
        old = bpy.data.objects.get(nm)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(location=(0, 5, 0))
    a = bpy.context.active_object
    a.name = "AtlasA"
    bpy.ops.mesh.primitive_cube_add(location=(2, 5, 0))
    b = bpy.context.active_object
    b.name = "AtlasB"
    shared_mat = btk.create_mat("standard", name="atlas_shared_mat")
    btk.assign_mat(a, shared_mat)
    btk.assign_mat(b, shared_mat)
    btk.create_lightmap_uvs([a, b])

    def uv_bbox(obj):
        lm = btk.find_lightmap_uv_set(obj)
        d = obj.data.uv_layers[lm].data
        buf = np.empty(len(d) * 2, np.float32)
        d.foreach_get("uv", buf)
        uv = buf.reshape(-1, 2)
        return (float(uv[:, 0].min()), float(uv[:, 0].max()),
                float(uv[:, 1].min()), float(uv[:, 1].max()))

    atlas_baker = LightmapBaker(resolution=64, samples=1)
    maps = atlas_baker.bake_separated([a, b], output_dir=tmp_dir, suffix="_Lightmap")
    check("atlas: both objects baked", set(maps) == {a.name, b.name}, f"{maps}")
    src_a, src_b = maps.get(a.name), maps.get(b.name)
    before_a = uv_bbox(a)  # the shared 0-1 unwrap -- pack_atlas must NOT touch it

    packed = atlas_baker.pack_atlas(maps, output_dir=tmp_dir, suffix="_Lightmap")
    check("atlas: both objects in the packed result", set(packed) == {a.name, b.name}, f"{packed}")
    atlas_paths = {p for p, _so in packed.values()}
    check("atlas: one shared atlas for the shared material", len(atlas_paths) == 1, f"{atlas_paths}")
    atlas_path = next(iter(atlas_paths))
    check("atlas: shared EXR written to disk",
          os.path.isfile(atlas_path) and os.path.getsize(atlas_path) > 0, atlas_path)
    check("atlas: per-object source maps consolidated (removed)",
          not os.path.exists(src_a) and not os.path.exists(src_b), f"{src_a} | {src_b}")

    def rect_ok(so):
        sx, sy, ox, oy = so
        return sx < 1.0 and sy < 1.0 and ox >= -1e-6 and oy >= -1e-6 \
            and ox + sx <= 1.0001 and oy + sy <= 1.0001

    check("atlas: rects are non-identity and inside the unit square",
          all(rect_ok(so) for _, so in packed.values()),
          f"{[so for _, so in packed.values()]}")

    # The rect is the ENGINE BINDING, not a UV edit: the shared [0,1] unwrap must
    # come through the pack bit-identical (that is what lets instances share the mesh).
    check("atlas: object A's lightmap UVs untouched by the pack",
          all(abs(x - y) < 1e-6 for x, y in zip(before_a, uv_bbox(a))),
          f"before={before_a} after={uv_bbox(a)}")

    atlas_baker.commit_lightmap(
        {n: p for n, (p, _so) in packed.items()},
        scale_offsets={n: so for n, (_p, so) in packed.items()},
    )
    info_a = json.loads(a[LightmapBaker.LIGHTMAP_INFO_PROP])
    check("atlas: commit records the rect as the scaleOffset binding, no uvRect",
          info_a.get("scaleOffset") == [float(v) for v in packed[a.name][1]]
          and "uvRect" not in info_a, f"{info_a}")
    raw_a = btk.DataNodes.get_export_string("lightmap_metadata")
    recs_a = (json.loads(raw_a).get("objects") if raw_a else []) or []
    check("atlas: manifest publishes the real per-object scaleOffset",
          {r["name"]: r.get("scaleOffset") for r in recs_a}
          == {n: [float(v) for v in so] for n, (_p, so) in packed.items()},
          f"{recs_a}")

    atlas_baker.revert([a, b])
    after_a = uv_bbox(a)
    check("atlas: revert leaves the unit-square layout intact (nothing to restore)",
          all(abs(x - y) < 1e-6 for x, y in zip(before_a, after_a)),
          f"before={before_a} after={after_a}")

    # --- legacy uvRect commits still revert (old scenes repacked UVs in place) ---
    legacy_rect = [0.5, 0.5, 0.25, 0.25]
    lm_name = btk.find_lightmap_uv_set(a)
    LightmapBaker._transform_lightmap_uvs(a, lm_name, legacy_rect)  # simulate old pack
    a[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps({
        "map": "legacy.exr", "uv_set": lm_name, "intensity": 1.0,
        "scaleOffset": [1.0, 1.0, 0.0, 0.0], "mode": "separated",
        "uvRect": legacy_rect,
    })
    atlas_baker.revert([a])
    check("legacy: revert inverts an old uvRect marker's UV repack",
          all(abs(x - y) < 1e-3 for x, y in zip(before_a, uv_bbox(a))),
          f"before={before_a} after={uv_bbox(a)}")

    # --- bake_atlas: plan first, bake to the plan, publish only results ----
    # The two-call form above bakes every object at the FULL atlas resolution and then
    # downscales it into a small rect -- N times the rays to supersample away noise the
    # denoise pass removes anyway. bake_atlas plans the layout up front (it needs only
    # geometry + material assignment, both known before a ray is traced) and bakes each
    # object at the footprint it will occupy.
    plan = atlas_baker.atlas_plan([a, b])
    planned = {n for entries in plan.values() for n, _r in entries}
    check("plan: objects sharing a material land in one group",
          len(plan) == 1 and planned == {a.name, b.name}, f"{plan}")
    sizes = atlas_baker.plan_sizes(plan)
    res = atlas_baker.resolution
    check("plan: each tile is sized to its rect, not the whole atlas",
          set(sizes) == planned and all(w < res and h < res for w, h in sizes.values()),
          f"{sizes} (atlas {res})")
    check("plan: the tiles together fit inside one atlas (no supersampling waste)",
          sum(w * h for w, h in sizes.values()) <= res * res,
          f"{sum(w * h for w, h in sizes.values())} vs {res * res}")

    # Intermediates must never reach the destination: a project's texture folder is not a
    # scratch dir, and nothing downstream ever cleans one up.
    atlas_dir = os.path.join(tmp_dir, "atlas_out")
    packed2 = atlas_baker.bake_atlas([a, b], output_dir=atlas_dir, suffix="_Lightmap")
    check("bake_atlas: both objects packed", set(packed2) == {a.name, b.name}, f"{packed2}")
    written = sorted(os.listdir(atlas_dir)) if os.path.isdir(atlas_dir) else []
    check("bake_atlas: the output dir holds ONLY the finished atlas",
          len(written) == 1 and written[0].endswith(".exr"), f"{written}")
    check("bake_atlas: every returned path lives in the output dir",
          all(os.path.dirname(os.path.abspath(p)) == os.path.abspath(atlas_dir)
              for p, _so in packed2.values()), f"{packed2}")
    atlas_baker.revert([a, b])

    # A solo group assembles no atlas, but its map is still a RESULT -- it has to be moved
    # out of the work dir, or it would be swept and the commit left pointing at nothing.
    solo_dir = os.path.join(tmp_dir, "solo_out")
    solo = atlas_baker.bake_atlas([cube], output_dir=solo_dir, suffix="_Lightmap")
    check("bake_atlas: a solo group's map is published to the output dir too",
          bool(solo) and all(
              os.path.isfile(p)
              and os.path.dirname(os.path.abspath(p)) == os.path.abspath(solo_dir)
              for p, _so in solo.values()),
          f"{solo}")
    check("bake_atlas: a solo group leaves no tile behind either",
          len(os.listdir(solo_dir)) == len({p for p, _so in solo.values()}),
          f"{sorted(os.listdir(solo_dir))}")
    atlas_baker.revert([cube])

    # --- linked duplicates (instances) are FIRST-CLASS atlas citizens ------
    # Each instance stands somewhere different and receives different light, so each
    # gets its own bake + rect over the ONE shared unwrap (probe-verified: Cycles bakes
    # per object transform through the shared material's sequentially-rebound image node).
    bpy.ops.mesh.primitive_cube_add(location=(0, 14, 0))
    ia = bpy.context.active_object
    ia.name = "InstA"
    ib = ia.copy()  # linked: shares ia.data
    ib.name = "InstB"
    ib.location = (4, 14, 0)
    ib.scale = (2.0, 2.0, 2.0)  # world-area weighting must earn it a bigger rect
    bpy.context.collection.objects.link(ib)
    inst_mat = btk.create_mat("standard", name="inst_shared_mat")
    btk.assign_mat(ia, inst_mat)

    btk.create_lightmap_uvs([ia, ib])
    check("instances: one lightmap layer on the shared datablock",
          ia.data is ib.data
          and sum(1 for l in ia.data.uv_layers if "lightmap" in l.name.lower()) == 1,
          f"{[l.name for l in ia.data.uv_layers]}")

    inst_plan = atlas_baker.atlas_plan([ia, ib])
    inst_entries = [e for entries in inst_plan.values() for e in entries]
    check("instances: atlas_plan gives every instance its own entry",
          {n for n, _r in inst_entries} == {"InstA", "InstB"}, f"{inst_plan}")

    def rect_area(r):
        return float(r[0]) * float(r[1])

    inst_rects = {n: r for n, r in inst_entries}
    check("instances: the scaled copy earns the larger rect (world-area weights)",
          rect_area(inst_rects["InstB"]) > rect_area(inst_rects["InstA"]),
          f"{inst_rects}")

    inst_dir = os.path.join(tmp_dir, "inst_out")
    inst_before = uv_bbox(ia)
    inst_packed = atlas_baker.bake_atlas([ia, ib], output_dir=inst_dir, suffix="_Lightmap")
    check("instances: both baked into one shared atlas",
          set(inst_packed) == {"InstA", "InstB"}
          and len({p for p, _so in inst_packed.values()}) == 1, f"{inst_packed}")
    check("instances: two DISTINCT non-identity rects",
          inst_packed["InstA"][1] != inst_packed["InstB"][1]
          and all(rect_ok(so) for _p, so in inst_packed.values()),
          f"{[so for _p, so in inst_packed.values()]}")
    check("instances: the shared unwrap is untouched",
          all(abs(x - y) < 1e-6 for x, y in zip(inst_before, uv_bbox(ia))),
          f"before={inst_before} after={uv_bbox(ia)}")

    atlas_baker.commit_lightmap(
        {n: p for n, (p, _so) in inst_packed.items()},
        scale_offsets={n: so for n, (_p, so) in inst_packed.items()},
    )
    raw_inst = btk.DataNodes.get_export_string("lightmap_metadata")
    recs_inst = {r["name"]: r for r in (json.loads(raw_inst).get("objects") or [])}
    check("instances: manifest carries one record per instance with its own rect",
          {"InstA", "InstB"} <= set(recs_inst)
          and recs_inst["InstA"]["scaleOffset"] != recs_inst["InstB"]["scaleOffset"],
          f"{recs_inst}")
    atlas_baker.revert([ia, ib])

    # --- the lightmap is named after the TEXTURE SET, not the material -------------
    # A lightmap is one more map of the set the object already wears, so it has to sort
    # beside them: MAT_OFFICE_ENV_Lightmap.exr next to OFFICE_ENV_Base_color.png reads as
    # a stray from a different set. The material above carries no image nodes, so nothing
    # here is exercised by the atlas checks -- a regression would pass them silently.
    named_mat = btk.create_mat("standard", name="MAT_OFFICE_ENV")
    for fname in ("OFFICE_ENV_Base_color.png", "OFFICE_ENV_Normal.png", "stray_noise.png"):
        img = bpy.data.images.new(fname, 4, 4)
        img.name = fname
        tex_node = named_mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
    check("naming: the texture set's base name wins over the material name",
          atlas_baker._material_texture_base("MAT_OFFICE_ENV") == "OFFICE_ENV",
          f"{atlas_baker._material_texture_base('MAT_OFFICE_ENV')}")
    check("naming: one oddly-named map cannot rename the whole set",
          atlas_baker._atlas_base("MAT_OFFICE_ENV", ["AtlasA"]) == "OFFICE_ENV",
          f"{atlas_baker._atlas_base('MAT_OFFICE_ENV', ['AtlasA'])}")
    check("naming: a material with no textures falls back to its own name",
          atlas_baker._atlas_base("atlas_shared_mat", ["AtlasA"]) == "atlas_shared_mat",
          f"{atlas_baker._atlas_base('atlas_shared_mat', ['AtlasA'])}")
    check("naming: no material at all falls back to the object name",
          atlas_baker._atlas_base("__no_material__", ["AtlasA"]) == "AtlasA",
          f"{atlas_baker._atlas_base('__no_material__', ['AtlasA'])}")

    # A SOLO group is renamed too -- its per-object tile name is an intermediate, and the
    # rename is what makes ``_place``'s foreign-source guard load-bearing.
    named_cube_dir = os.path.join(tmp_dir, "named_out")
    bpy.ops.mesh.primitive_cube_add(location=(0, 9, 0))
    named_cube = bpy.context.active_object
    named_cube.name = "SomeObjectName"
    btk.assign_mat(named_cube, named_mat)
    btk.create_lightmap_uvs([named_cube])
    named = atlas_baker.bake_atlas([named_cube], output_dir=named_cube_dir, suffix="_Lightmap")
    named_files = sorted(os.listdir(named_cube_dir)) if os.path.isdir(named_cube_dir) else []
    check("naming: a solo group's map is named for its texture set, not the object",
          named_files == ["OFFICE_ENV_Lightmap.exr"], f"{named_files}")
    atlas_baker.revert([named_cube])

    # --- the bake leaves the source material alone ------------------------
    # The fused/unlit level was removed; what replaces its coverage is the
    # guarantee that made it the wrong default -- a lightmap bake must never
    # touch the object's shading.
    src_mat = cube.material_slots[0].material if cube.material_slots else None
    mapping = baker.bake_separated([cube], output_dir=tmp_dir, suffix="_LM")
    check("bake produced a map", cube.name in mapping)
    baker.commit_lightmap(mapping)
    check("commit_lightmap keeps the source material",
          cube.material_slots[0].material is src_mat)
    check("commit_lightmap stamps the lighting-only marker",
          LightmapBaker.LIGHTMAP_INFO_PROP in cube)
    baker.revert([cube])
    check("revert clears the marker and leaves the material",
          LightmapBaker.LIGHTMAP_INFO_PROP not in cube
          and cube.material_slots[0].material is src_mat)

    # --- black-bake guard (panel) -----------------------------------------
    # A black bake is a FAITHFUL render of an unlit scene, so nothing errors;
    # the panel's post-bake guard is what tells the artist before the map
    # ships to a black web preview (mirrors mayatk's guard + threshold).
    from blendertk.light_utils.lightmap_baker.lightmap_baker import (
        LightmapBakerSlots,
    )

    def _exr(name, value):
        path = os.path.join(tmp_dir, name)
        img = bpy.data.images.new(name, 8, 8, float_buffer=True)
        # Colorspace BEFORE pixels: assigned after, the save goes through a
        # view transform and a float EXR can come out black (known gotcha).
        img.colorspace_settings.name = "Non-Color"
        img.pixels = [value, value, value, 1.0] * 64
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    guard = LightmapBakerSlots.__new__(LightmapBakerSlots)
    black, lit = _exr("guard_black.exr", 0.001), _exr("guard_lit.exr", 1.0)
    check("black-bake guard fires for an unlit map",
          "BLACK" in guard._black_bake_warning({"a": black}))
    check("black-bake guard stays quiet for a lit map",
          guard._black_bake_warning({"a": lit}) == "")
    check("one healthy map among dark ones clears the guard",
          guard._black_bake_warning({"a": black, "b": lit}) == "")
    check("a missing map never breaks a finished bake",
          guard._black_bake_warning({"a": os.path.join(tmp_dir, "nope.exr")}) == "")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(l.startswith("OK") for l in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
