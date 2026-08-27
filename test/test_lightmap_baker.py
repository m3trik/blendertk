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

    # --- rendered-dead rescue + border-texel-center rects ------------------
    # Twin of mayatk (test_exact_zero_cell_content_is_healed /
    # test_published_rects_sample_border_texel_centers): Cycles bakes every
    # UV texel of the target regardless of world occlusion, so buried
    # geometry (below a floor slab, behind trim) bakes full-coverage ~black;
    # shipped, those texels smear into visible dark borders. And a rect edge
    # published ON a texel boundary splits every tap along a shared 3D edge
    # onto the neighboring cell's gutter.
    def _exr_dead(name, value, dead_cols):
        path = os.path.join(tmp_dir, name)
        img = bpy.data.images.new(name, 16, 16, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"
        buf = np.tile(
            np.array([value, value, value, 1.0], np.float32), 256
        ).reshape(16, 16, 4)
        buf[:, :dead_cols, :3] = 0.0  # rendered-dead strip (occluded geometry)
        img.pixels.foreach_set(buf.reshape(-1))
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    dead_maps = {
        a.name: _exr_dead("deadA.exr", 1.5, 8),
        b.name: _exr_dead("deadB.exr", 0.8, 0),
    }
    packed_dead = atlas_baker.pack_atlas(dead_maps, output_dir=tmp_dir, suffix="_DeadLM")
    dpath = next(iter(p for p, _so in packed_dead.values()))
    dimg = bpy.data.images.load(dpath)
    dbuf = np.empty(len(dimg.pixels), np.float32)
    dimg.pixels.foreach_get(dbuf)
    drgb = dbuf.reshape(dimg.size[1], dimg.size[0], dimg.channels)[..., :3]
    bpy.data.images.remove(dimg)
    n_zero = int((~(drgb.max(axis=-1) > 0)).sum())
    check("atlas: rendered-dead texels are healed (no exact zeros ship)",
          n_zero == 0, f"{n_zero} zero texel(s)")
    res_px = atlas_baker.resolution
    check("atlas: published rects aim at border-texel centers",
          all(
              abs((ox * res_px) % 1.0 - 0.5) < 1e-4
              and abs(((ox + sx) * res_px) % 1.0 - 0.5) < 1e-4
              and abs((oy * res_px) % 1.0 - 0.5) < 1e-4
              and abs(((oy + sy) * res_px) % 1.0 - 0.5) < 1e-4
              for _p, (sx, sy, ox, oy) in packed_dead.values()),
          f"{[so for _p, so in packed_dead.values()]}")

    solo_dead = atlas_baker.pack_atlas(
        {cube.name: _exr_dead("deadSolo.exr", 1.2, 4)},
        output_dir=tmp_dir, suffix="_DeadSolo",
    )
    spath, srect = solo_dead[cube.name]
    simg = bpy.data.images.load(spath)
    sbuf = np.empty(len(simg.pixels), np.float32)
    simg.pixels.foreach_get(sbuf)
    srgb = sbuf.reshape(simg.size[1], simg.size[0], simg.channels)[..., :3]
    bpy.data.images.remove(simg)
    check("solo: adopted map healed of exact zeros (identity rect kept)",
          int((~(srgb.max(axis=-1) > 0)).sum()) == 0
          and srect == list(LightmapBaker._IDENTITY_SCALE_OFFSET),
          f"zeros={int((~(srgb.max(axis=-1) > 0)).sum())} rect={srect}")

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

    # --- output directory field (panel) -----------------------------------
    # Optional dir, resolved against the workspace's texture folder: empty is
    # that folder, a relative entry lands under it (so the setting survives a
    # project move), an absolute one wins outright. Mirrors mayatk's twin.
    class _Field:
        def __init__(self, text=""):
            self._text = text

        def text(self):
            return self._text

        def setText(self, text):
            self._text = text

    class _Ui:
        def __init__(self, text=""):
            self.txt_output_dir = _Field(text)

    BASE = os.path.normpath("C:/proj/sourceimages" if os.name == "nt" else "/proj/sourceimages")

    def _slots(text=""):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = _Ui(text)
        s._base_output_dir = lambda: BASE
        return s

    check("empty output dir falls back to the texture folder",
          _slots()._output_dir() == BASE)
    check("a relative output dir resolves under the texture folder",
          _slots("lightmaps")._output_dir() == os.path.join(BASE, "lightmaps"))
    check("a nested relative output dir normalizes",
          _slots("bake/lm")._output_dir() == os.path.normpath(os.path.join(BASE, "bake/lm")))
    abs_dir = os.path.normpath("D:/bakes/lm" if os.name == "nt" else "/bakes/lm")
    check("an absolute output dir is used as-is",
          _slots(abs_dir)._output_dir() == abs_dir)
    check("a quoted/padded entry is trimmed before joining",
          _slots('  " lightmaps "  ')._output_dir() == os.path.join(BASE, "lightmaps"))
    # "/lightmaps" is a separator-spelled SUBDIRECTORY, but os.path.isabs calls it
    # absolute on Windows and would resolve it to the current drive's root.
    check("a driveless rooted entry stays a subdirectory",
          _slots("/lightmaps")._output_dir() == os.path.join(BASE, "lightmaps"))

    # The browse dialog can only hand back an absolute path; a pick inside the
    # texture folder is rewritten to the portable relative form, anything
    # outside it is left exactly as the dialog wrote it.
    s = _slots()
    s._relativize_output_dir(os.path.join(BASE, "lightmaps", "hero"))
    check("browsing inside the texture folder stores a relative path",
          s.ui.txt_output_dir.text() == "lightmaps/hero")
    s._relativize_output_dir(BASE)
    check("browsing the texture folder itself clears the field",
          s.ui.txt_output_dir.text() == "")
    s.ui.txt_output_dir.setText(abs_dir)
    s._relativize_output_dir(abs_dir)
    check("browsing outside the texture folder stays absolute",
          s.ui.txt_output_dir.text() == abs_dir)

    # --- pre-bake unlit-scene guard (mayatk parity) ------------------------
    # mayatk warns BEFORE spending the rays; blendertk previously only had the
    # panel's post-bake black-map check, so a scripted bake got no hint at all.
    scene = bpy.context.scene
    world_backup = scene.world

    lit = LightmapBaker.from_preset("preview")
    lit._warn_if_unlit_scene()
    check("a scene with a light does not trip the guard",
          lit._warned_no_lights is False)

    # Drop every light, drop the world (factory startup ships a grey emitting
    # one) -> genuinely unlit.
    for o in [o for o in scene.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(o, do_unlink=True)
    scene.world = None
    dark = LightmapBaker.from_preset("preview")
    dark._warn_if_unlit_scene()
    check("a scene with no light and no world trips the guard",
          dark._warned_no_lights is True)

    # Latch: the warning is once per instance, not once per baked object.
    dark._warned_no_lights = False
    dark._warn_if_unlit_scene()
    check("the guard re-arms when the latch is cleared", dark._warned_no_lights is True)

    # An HDRI/world-lit scene IS lit -- blendertk ships an HDR Manager, so a
    # false "unlit" cry here would fire on a correctly lit setup. (The
    # LightUtils.world_emits primitive itself is unit-tested in
    # test_light_utils.py; this pins that the guard consults it.)
    w = bpy.data.worlds.new("probe_world")
    w.use_nodes = True
    scene.world = w
    bg = next(n for n in w.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Strength"].default_value = 1.0
    bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    worldlit = LightmapBaker.from_preset("preview")
    worldlit._warn_if_unlit_scene()
    check("a world-lit (HDRI) scene does not trip the guard",
          worldlit._warned_no_lights is False)

    bg.inputs["Strength"].default_value = 0.0
    darkworld = LightmapBaker.from_preset("preview")
    darkworld._warn_if_unlit_scene()
    check("a world at zero strength still trips the guard",
          darkworld._warned_no_lights is True)
    bg.inputs["Strength"].default_value = 1.0

    # --- light audit diagnostic (mayatk parity) ----------------------------
    # mayatk attaches a per-light table to the black-bake warning so a dark
    # result carries its own diagnosis; blendertk's warning had no diagnostic.
    audit = LightmapBakerSlots._light_audit()
    check("audit reports the world when the scene has no lights",
          "<no lights in the scene>" in audit and "<world>" in audit, audit)

    bpy.ops.object.light_add(type="AREA", location=(0, 0, 5))
    area = bpy.context.active_object
    area.data.energy = 250.0
    area.data.size = 2.0
    area.hide_render = True
    audit = LightmapBakerSlots._light_audit()
    check("audit lists the light with the dials a black bake traces to",
          area.name in audit and "power=250W" in audit and "type=AREA" in audit
          and "size=2" in audit and "render_visible=False" in audit, audit)
    check("audit still reports world state alongside the lights",
          "<world>: emits=" in audit, audit)

    # Restore what the guard tests tore down.
    bpy.data.objects.remove(area, do_unlink=True)
    scene.world = world_backup
    bpy.data.worlds.remove(w)

    # --- lightmap dependencies (mirror of mayatk's TestLightmapDependencies) ---
    # A committed lightmap is a texture dependency no Image datablock references;
    # these are the engine calls the Texture Path Editor, the exporter's path
    # check and the GLB conversion share. No .blend is saved here, so the
    # workspace walk is out of reach -- the search folders are passed explicitly.
    LightmapBaker().revert()
    bpy.ops.mesh.primitive_cube_add()
    dep_cube = bpy.context.active_object
    dep_cube.name = "dep_cube"
    dep_dir = os.path.join(tmp_dir, "deps")
    os.makedirs(dep_dir, exist_ok=True)
    dep_map = os.path.join(dep_dir, "dep_cube_LightMap.exr")
    open(dep_map, "wb").close()
    dep_baker = LightmapBaker()
    dep_baker.commit_lightmap({dep_cube.name: dep_map})

    def _same_dir(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    deps = dep_baker.lightmap_dependencies()
    check("lightmap_dependencies lists the committed map",
          len(deps) == 1 and deps[0]["map"] == "dep_cube_LightMap.exr", f"{deps}")
    check("...resolved by the marker's own folder",
          bool(deps) and deps[0]["found_by"] == LightmapBaker.FOUND_BY_HINT
          and _same_dir(deps[0]["path"], dep_map), f"{deps}")
    check("...naming the object", bool(deps) and deps[0]["objects"] == ["dep_cube"])
    check("search_dirs includes the folder the map was found in",
          any(_same_dir(d, dep_dir) for d in LightmapBaker.search_dirs()),
          f"{LightmapBaker.search_dirs()}")

    moved_dir = os.path.join(tmp_dir, "moved")
    os.makedirs(moved_dir, exist_ok=True)
    shutil.move(dep_map, os.path.join(moved_dir, "dep_cube_LightMap.exr"))
    deps = dep_baker.lightmap_dependencies(search_dirs=[moved_dir], walk=False)
    check("a moved map is found through the search folders",
          bool(deps) and deps[0]["found_by"] == LightmapBaker.FOUND_BY_SEARCH, f"{deps}")
    deps = dep_baker.lightmap_dependencies(search_dirs=[], walk=False)
    check("a map found nowhere is reported missing",
          bool(deps) and deps[0]["path"] is None and deps[0]["found_by"] is None, f"{deps}")

    dest = os.path.join(tmp_dir, "dest")
    plan = dep_baker.relocate_lightmaps(dest, source_dir=moved_dir, dry_run=True)
    check("relocate dry run plans without touching anything",
          len(plan["relocate"]) == 1 and plan["copied"] == []
          and not os.path.exists(os.path.join(dest, "dep_cube_LightMap.exr")), f"{plan}")
    result = dep_baker.relocate_lightmaps(dest, source_dir=moved_dir)
    marker = json.loads(dep_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    check("relocate copies the map into the destination",
          os.path.isfile(os.path.join(dest, "dep_cube_LightMap.exr")) and len(result["copied"]) == 1,
          f"{result}")
    check("...and repoints the bake marker",
          result["updated"] == 1
          and _same_dir(LightmapBaker._resolved_dir(marker["dir"], marker["map"]), dest),
          f"{marker.get('dir')}")
    manifest = json.loads(btk.DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA) or "{}")
    check("...and the manifest carries the absolute folder",
          _same_dir(manifest.get("dir", ""), dest), f"{manifest.get('dir')}")
    check("the relocated map now resolves by hint",
          dep_baker.lightmap_dependencies(search_dirs=[], walk=False)[0]["found_by"]
          == LightmapBaker.FOUND_BY_HINT)

    bpy.ops.mesh.primitive_cube_add()
    lost_cube = bpy.context.active_object
    lost_cube.name = "lost_cube"
    dep_baker.commit_lightmap({lost_cube.name: os.path.join(tmp_dir, "gone", "lost.exr")})
    result = dep_baker.relocate_lightmaps(dest, source_dir=moved_dir, objects=["lost_cube"])
    check("relocate names what it could not find",
          [d["map"] for d in result["missing"]] == ["lost.exr"] and result["updated"] == 0,
          f"{result}")
    check("scope limits to the given objects",
          [d["map"] for d in dep_baker.lightmap_dependencies(objects=["dep_cube"])]
          == ["dep_cube_LightMap.exr"])
    n = dep_baker.repath_lightmaps({"lost.exr": moved_dir}, ["lost_cube"])
    lost_marker = json.loads(lost_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    check("repath_lightmaps rewrites a marker's folder",
          n == 1 and _same_dir(LightmapBaker._resolved_dir(lost_marker["dir"], "lost.exr"), moved_dir),
          f"{lost_marker.get('dir')}")
    dep_baker.revert()

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(l.startswith("OK") for l in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
