"""blendertk lightmap baker headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_lightmap_baker.py

Exercises the engine end-to-end (create_lightmap_uvs → Cycles bake → commit → revert) and the
Unity bridge (DataNodes manifest). Tiny resolution / samples so the real bake stays fast.
"""
import sys, os, json, tempfile, shutil, traceback

import numpy as np  # ships with Blender

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    # str(detail): callers pass the offending VALUE, which is as often a list or
    # dict as a string -- and concatenating one raised out of the reporter, so
    # the failure that had something to say was the one that killed the run
    # before saying it.
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


tmp_dir = tempfile.mkdtemp(prefix="btk_lm_")
try:
    import bpy
    import blendertk as btk
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
    from blendertk.mat_utils.texture_baker import TextureBaker

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

    # GI bounce depth rides the tier, exactly as mayatk's gi_depth does. Unpinned,
    # a Cycles bake ran at whatever the SCENE last rendered with (4 on a factory
    # startup, anything at all in a saved .blend) -- so the same scene baked to a
    # different brightness in two sessions, and to a different one again than the
    # Arnold twin, with nothing in either output to say why. In a closed room each
    # extra bounce adds another rho^n term, which is why it is a tier dial and not
    # a detail.
    tiers = {
        n: LightmapBaker.from_preset(n).bounces
        for n in ("preview", "quest", "desktop", "hero")
    }
    check(
        "every preset carries a bounce depth",
        all(isinstance(v, int) and v >= 1 for v in tiers.values()),
        f"{tiers}",
    )
    check(
        "bounces rise with the tier",
        tiers["preview"] <= tiers["quest"] <= tiers["desktop"],
        f"{tiers}",
    )
    # The production tiers keep CYCLES' own default depth (4), deliberately: pinning
    # exists to make a bake reproducible, not to restyle it, so it must not silently
    # darken results that were already being produced at the factory default. Only
    # preview trades bounces for speed. NOT copied from mayatk's Arnold gi_depth --
    # measured, Cycles at 4 bounces already sits at 0.76x an Arnold gi_depth-2 bake of
    # the same scene, so the two renderers' depth numbers are not interchangeable and
    # the residual is method (Arnold bakes through a white card), not bounce count.
    check(
        "every tier but preview keeps Cycles' own default depth",
        tiers["quest"] == tiers["desktop"] == tiers["hero"] == 4,
        f"{tiers}",
    )
    check(
        "only preview -- the tier that advertises speed -- trades bounces",
        tiers["preview"] < tiers["quest"],
        f"{tiers}",
    )
    check(
        "bounces is overridable like the other constructor args",
        LightmapBaker.from_preset("quest", bounces=5).bounces == 5,
    )
    check(
        "bounces reaches the primitive that applies it",
        LightmapBaker(bounces=7)._texture_baker.bounces == 7,
    )
    # The BARE constructor is the one path that never sees a tier (a scripted
    # caller, the panel's revert-only instance). It must sit on Cycles' own default,
    # not preview's: pinning is here to make a bake reproducible, and a lower
    # default would silently darken every bake that never named a tier.
    check(
        "the bare constructor keeps Cycles' own default depth",
        LightmapBaker().bounces == 4 and TextureBaker().bounces == 4,
        f"{LightmapBaker().bounces} / {TextureBaker().bounces}",
    )

    # The bake must PIN the depth and hand the scene back exactly as it was --
    # leaving a user's render bounce budget on the baker's value reads as a Blender
    # bug rather than ours. max_bounces is raised only when it would clamp the
    # request, and never lowered.
    scn = bpy.context.scene
    scn.cycles.diffuse_bounces, scn.cycles.max_bounces = 7, 3
    tb = TextureBaker(bounces=5)
    state = tb._configure_bake_scene(use_pass_color=False)
    pinned = (scn.cycles.diffuse_bounces, scn.cycles.max_bounces)
    tb._restore_bake_scene(state)
    check(
        "the bake pins its own diffuse depth",
        pinned[0] == 5,
        f"diffuse_bounces={pinned[0]}",
    )
    check(
        "max_bounces is raised so it cannot clamp the request",
        pinned[1] >= 5,
        f"max_bounces={pinned[1]}",
    )
    check(
        "both bounce settings are restored afterwards",
        (scn.cycles.diffuse_bounces, scn.cycles.max_bounces) == (7, 3),
        f"{(scn.cycles.diffuse_bounces, scn.cycles.max_bounces)}",
    )
    # ...and a budget ALREADY above the request is left alone, not lowered.
    scn.cycles.diffuse_bounces, scn.cycles.max_bounces = 4, 12
    state = TextureBaker(bounces=2)._configure_bake_scene(use_pass_color=False)
    kept = scn.cycles.max_bounces
    TextureBaker(bounces=2)._restore_bake_scene(state)
    check("a larger user budget is never lowered", kept == 12, f"max_bounces={kept}")

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

    # A DELIVERED per-object map must carry no background: exact-black texels are
    # what every mip level averages back into the island as a dark halo, i.e. a
    # seam on tiled geometry at distance. Only the atlas path used to heal, so
    # the panel's own DEFAULT packing mode shipped it.
    #
    # The fixture matters. A FRESH create_lightmap_uvs set fills 0-1 (smart_project
    # with scale_to_bounds), so its bake has no background at all -- measured, 0
    # zero texels at 64/256/1024. The case that bites is a lightmap layer that does
    # NOT fill 0-1, which is exactly what "reuses a pre-existing one under its own
    # name" produces for an imported or hand-packed set. Squeezing the islands into
    # a quarter of the map reproduces it: 72% of the frame comes back exact black.
    def _rgb(p):
        i = bpy.data.images.load(p)
        try:
            b = np.empty(len(i.pixels), dtype=np.float32)
            i.pixels.foreach_get(b)
            return b.reshape(i.size[1], i.size[0], i.channels)[..., :3].copy()
        finally:
            bpy.data.images.remove(i)

    partial = LightmapBaker.from_preset(
        "preview", resolution=128, samples=1, denoise=False, device="CPU"
    )
    lm_layer = cube.data.uv_layers[btk.find_lightmap_uv_set(cube)]
    original_uvs = [tuple(loop.uv) for loop in lm_layer.data]
    for loop in lm_layer.data:
        loop.uv = (loop.uv[0] * 0.5, loop.uv[1] * 0.5)
    try:
        raw_map = partial._bake(
            [cube],
            output_dir=tmp_dir,
            suffix="_Partial",
            create_uvs=False,
            heal=False,
        ).get(cube.name, "")
        unhealed_zeros = int((_rgb(raw_map).max(axis=-1) <= 0.0).sum())
        check(
            "the fixture actually reproduces the background",
            unhealed_zeros > 0,
            f"{unhealed_zeros} zero texel(s) unhealed",
        )

        healed_map = partial._bake(
            [cube],
            output_dir=tmp_dir,
            suffix="_Healed",
            create_uvs=False,
        ).get(cube.name, "")
        delivered = _rgb(healed_map)
        check(
            "a partial-coverage map ships no black background",
            not bool((delivered.max(axis=-1) <= 0.0).any()),
            f"{int((delivered.max(axis=-1) <= 0.0).sum())} zero texel(s)",
        )
        # A fixed point: pack_atlas re-runs the heal on solo maps, and a second
        # pass that moved texels would mean the threshold drifts on its own output.
        partial._heal_dead_texels(healed_map)
        check(
            "the heal is idempotent", bool(np.array_equal(delivered, _rgb(healed_map)))
        )
    finally:
        for loop, uv in zip(lm_layer.data, original_uvs):
            loop.uv = uv

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

    # --- a map the layout does not name must not vanish --------------------
    # pack_atlas walks the PLAN and keeps the entries it also has maps for, so
    # a name the plan never knew (an object renamed or removed between bake and
    # pack, or a plan built from a different set) fell out of that walk in
    # silence. The contract is that a bake is never lost: it comes back as its
    # own map with the identity rect.
    orphan_dir = os.path.join(tmp_dir, "orphan")
    orphan_map = _exr_dead("orphanLM.exr", 0.4, 4)
    orphaned = atlas_baker.pack_atlas(
        {"noSuchObject": orphan_map},
        output_dir=orphan_dir,
        suffix="_Orphan",
    )
    check(
        "pack_atlas: an unplanned map is kept, not dropped",
        set(orphaned) == {"noSuchObject"},
        f"{orphaned}",
    )
    if orphaned:
        opath, orect = orphaned["noSuchObject"]
        check(
            "pack_atlas: the kept map is placed in the output dir",
            os.path.dirname(os.path.abspath(opath)) == os.path.abspath(orphan_dir)
            and os.path.exists(opath),
            opath,
        )
        check(
            "pack_atlas: the kept map carries the identity rect",
            orect == list(LightmapBaker._IDENTITY_SCALE_OFFSET),
            f"{orect}",
        )

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

    # --- the tier's bounce depth reaches the PANEL bake ----------------------
    # Resolution and Samples reach the bake through their widgets; bounces has no
    # widget, so _apply_preset has to carry it or the tier silently no-ops for every
    # panel bake and the panel sits on the constructor default whichever tier shows
    # (the exact failure mayatk's _preset_gi comment records for gi_depth).
    from blendertk.light_utils.lightmap_baker.lightmap_baker import (
        LightmapBakerSlots,
    )

    class _Spin:
        def __init__(self):
            self._v = 0

        def blockSignals(self, _b):
            pass

        def setValue(self, v):
            self._v = int(v)

        def value(self):
            return self._v

    panel = LightmapBakerSlots.__new__(LightmapBakerSlots)
    panel.ui = type("U", (), {"spn_samples": _Spin()})()
    panel._set_resolution = lambda v: None
    for tier in ("preview", "quest", "desktop"):
        panel._apply_preset(tier)
        want = LightmapBaker.from_preset(tier).bounces
        check(
            f"_apply_preset carries {tier}'s bounce depth to the bake",
            panel._preset_gi.get("bounces") == want,
            f"{panel._preset_gi} want {want}",
        )
    check(
        "the carried dials are exactly what LightmapBaker accepts",
        LightmapBaker(resolution=64, samples=1, **panel._preset_gi).bounces
        == LightmapBaker.from_preset("desktop").bounces,
    )

    # --- level guard (panel): black AND blown --------------------------------
    # Either failure is a FAITHFUL render of a wrong scene, so nothing errors;
    # the panel's post-bake guard is what tells the artist before the map ships
    # to a black (or white) web preview. The blown half exists because a Maya
    # bridge send crossed at 5.4e8 W per fixture and saturated every atlas while
    # reporting success -- reachable from this panel too, with hot enough lights.
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
    blown = _exr("guard_blown.exr", 40000.0)
    check(
        "level guard fires for an unlit map",
        "BLACK" in guard._level_warning({"a": black}),
    )
    check(
        "level guard stays quiet for a lit map", guard._level_warning({"a": lit}) == ""
    )
    check(
        "one healthy map among dark ones clears the guard",
        guard._level_warning({"a": black, "b": lit}) == "",
    )
    check(
        "a missing map never breaks a finished bake",
        guard._level_warning({"a": os.path.join(tmp_dir, "nope.exr")}) == "",
    )
    check(
        "level guard fires for a blown map",
        "BLOWN" in guard._level_warning({"a": blown}),
    )
    # The brightest map decides BOTH ways: one lit map disproves "unlit", and the
    # worst offender is what a blown bake has to show.
    check(
        "a blown map among lit ones still fires",
        "BLOWN" in guard._level_warning({"a": lit, "b": blown}),
    )

    # --- level primitive (engine) -------------------------------------------
    # The panel and the Maya bridge's headless bake template both ask "did this
    # land in a plausible range", so the measurement and the thresholds live on
    # the baker -- a bridge bake that disagreed with the panel about what counts
    # as blown would be worse than no check.
    levels = LightmapBaker.map_levels([lit, blown, lit])
    check(
        "map_levels collapses duplicate paths",
        len(levels) == 2,
        f"{sorted(os.path.basename(p) for p in levels)}",
    )
    check(
        "map_levels reports the mean per map",
        abs(levels[lit][0] - 1.0) < 1e-3 and levels[blown][0] > 1e4,
        f"{[(os.path.basename(k), v) for k, v in levels.items()]}",
    )
    # Blown and SATURATED are different states, and the guard reports both: a map
    # can be orders too bright while every texel is still real data. Only at the
    # half-float ceiling has the EXR actually lost information -- which is also why
    # a saturated map reads ~6.5e4 rather than the 1e7 its lights implied.
    ceiling = _exr("guard_ceiling.exr", LightmapBaker.HALF_FLOAT_MAX)
    saturation = LightmapBaker.map_levels([lit, blown, ceiling])
    check(
        "a blown-but-unsaturated map reports no lost data",
        saturation[lit][1] == 0.0 and saturation[blown][1] == 0.0,
        f"lit={saturation[lit][1]} blown={saturation[blown][1]}",
    )
    check(
        "map_levels reports the saturated fraction at the half-float ceiling",
        saturation[ceiling][1] > 0.99,
        f"{saturation[ceiling]}",
    )
    check(
        "map_levels skips an unreadable map rather than raising",
        LightmapBaker.map_levels([os.path.join(tmp_dir, "nope.exr")]) == {},
    )
    peak = LightmapBaker.peak_level([lit, blown])
    check(
        "peak_level picks the BRIGHTEST map",
        peak is not None and peak[0] == blown,
        f"{peak}",
    )
    check(
        "peak_level is None when nothing could be read",
        LightmapBaker.peak_level([]) is None,
    )
    # A saturated EXR reads ~4e4, not the 1e7 the lights implied: half-float
    # clamping HIDES magnitude, which is why the guard's line sits where it does.
    check(
        "the blown threshold sits between a hot bake and a saturated one",
        LightmapBaker.BLACK_BAKE_MEAN
        < LightmapBaker.BLOWN_BAKE_MEAN
        < LightmapBaker.HALF_FLOAT_MAX,
    )

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

    # --- Quality follows the dials (panel) ---------------------------------
    # Move Resolution or Samples off the tier and the combobox must say *Custom*
    # rather than keep naming a preset the bake is no longer using. One
    # ``sb.value_from`` rule does the following (uitk covers the rule itself);
    # what is pinned here is the panel's half. Mirrors mayatk's
    # TestQualityFollowsDials.
    class _QualityCombo:
        """Enough of QComboBox for cmb000_init / cmb000, populated by name."""

        def __init__(self):
            self.items, self._index = [], -1

        def clear(self):
            self.items, self._index = [], -1

        def addItems(self, items):
            self.items.extend(items)
            if self._index < 0 and self.items:
                self._index = 0

        def findText(self, text):
            return self.items.index(text) if text in self.items else -1

        def setCurrentIndex(self, index):
            self._index = index

        def currentIndex(self):
            return self._index

        def currentText(self):
            return self.items[self._index] if 0 <= self._index < len(self.items) else ""

    class _ResCombo:
        """cmb_resolution's item-data model: currentData() is the pixel size."""

        _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

        def __init__(self, resolution=1024):
            self._data = resolution

        def currentData(self):
            return self._data

        def setCurrentIndex(self, index):
            self._data = self._RESOLUTIONS[index]

        def blockSignals(self, _b):
            pass

    class _Spin:
        def __init__(self, v):
            self._v = v

        def value(self):
            return self._v

        def setValue(self, v):
            self._v = v

        def blockSignals(self, _b):
            pass

    class _QualityUi:
        def __init__(self, res, samples):
            self.cmb_resolution = _ResCombo(res)
            self.spn_samples = _Spin(samples)
            self.footer = _Field()

    def _quality_slots(res=1024, samples=256):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s._preset_by_dials = {}
        s.ui = _QualityUi(res, samples)
        return s

    q = _quality_slots()
    quality_combo = _QualityCombo()
    q.cmb000_init(quality_combo)
    tier_names = list(store.list())
    tiers = {}
    for n in tier_names:
        data = store.load(n)
        tiers[(int(data["resolution"]), int(data["samples"]))] = n
    check(
        "the Quality combobox ends in a Custom row",
        quality_combo.items == tier_names + ["Custom"],
        f"{quality_combo.items}",
    )
    check("cmb000_init still defaults to quest", quality_combo.currentText() == "quest")
    # Every tier the combo offers must be reachable from its dials, or the rule
    # would report Custom for a preset the user just picked.
    check(
        "every listed tier is reachable from its dials",
        q._preset_by_dials == tiers,
        f"{q._preset_by_dials}",
    )
    check("dials on a tier name that tier", q._preset_for_dials(1024, 256) == "quest")
    check(
        "ONE dial off the tier is enough to read Custom",
        q._preset_for_dials(1024, 255) == "Custom"
        and q._preset_for_dials(512, 256) == "Custom",
    )

    # Custom is not a stored preset: selecting it must move no dial, and say so
    # rather than fall silent.
    q = _quality_slots(res=2048, samples=257)
    quality_combo = _QualityCombo()
    q.cmb000_init(quality_combo)
    quality_combo.setCurrentIndex(quality_combo.findText("Custom"))
    q.cmb000(quality_combo.currentIndex(), quality_combo)
    check(
        "selecting Custom leaves the dials alone",
        q.ui.cmb_resolution.currentData() == 2048 and q.ui.spn_samples.value() == 257,
    )
    check("selecting Custom reports it", "Custom" in q.ui.footer.text())

    # ...and the Custom row must not cost the combobox its original job.
    quality_combo.setCurrentIndex(quality_combo.findText("desktop"))
    q.cmb000(quality_combo.currentIndex(), quality_combo)
    check(
        "selecting a tier still fills the dials",
        q.ui.cmb_resolution.currentData() == 2048 and q.ui.spn_samples.value() == 512,
    )
    check(
        "the dials it wrote resolve back to that tier",
        q._preset_for_dials(2048, 512) == "desktop",
    )

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

    # --- Include Environment (mayatk parity) -------------------------------
    # An HDRI is often a backdrop / look-dev convenience rather than the room's
    # real lighting, and baking one in is a flat ambient lift that cannot be
    # taken back out of the map afterwards. Off DETACHES the world for the bake
    # (mayatk's twin hides the aiSkyDomeLight) and restores it after.
    scene.world = w
    muted = LightmapBaker.from_preset("preview", include_environment=False)
    with muted._muted_environment():
        check(
            "include_environment=False detaches the world for the bake",
            scene.world is None,
        )
    check("...and the world is restored afterwards", scene.world is w)

    kept = LightmapBaker.from_preset("preview")
    with kept._muted_environment():
        check("include_environment=True leaves the world attached", scene.world is w)
    check("the world survives that too", scene.world is w)

    # A restore must happen even when the bake raises.
    boom = LightmapBaker.from_preset("preview", include_environment=False)
    try:
        with boom._muted_environment():
            raise RuntimeError("bake blew up")
    except RuntimeError:
        pass
    check("a failed bake still restores the world", scene.world is w)

    # from_preset must carry the non-numeric overrides: filtering to the int
    # keys silently dropped them, so from_preset(name, device="CPU") built a
    # baker on the other device and nothing said so.
    check(
        "from_preset carries include_environment",
        LightmapBaker.from_preset(
            "preview", include_environment=False
        ).include_environment
        is False,
    )
    check(
        "from_preset carries device",
        LightmapBaker.from_preset("preview", device="CPU").device == "CPU",
    )

    # A world the bake is about to detach is not a light source FOR that bake,
    # so an HDRI-only scene must still trip the guard when it is left out --
    # which is exactly the case where the artist needs to hear it.
    hdri_only = LightmapBaker.from_preset("preview", include_environment=False)
    hdri_only._warn_if_unlit_scene()
    check(
        "an HDRI-only scene trips the guard when the environment is left out",
        hdri_only._warned_no_lights is True,
    )

    # --- panel: Device row + Include Environment ---------------------------
    class _DeviceCombo:
        """Enough of QComboBox for cmb_device_init / _device (item data)."""

        def __init__(self):
            self.items, self._index = [], -1

        def clear(self):
            self.items, self._index = [], -1

        def addItem(self, text, data=None):
            self.items.append((text, data))
            if self._index < 0:
                self._index = 0

        def setCurrentIndex(self, i):
            self._index = i

        def currentData(self):
            if 0 <= self._index < len(self.items):
                return self.items[self._index][1]
            return None

    class _Check:
        def __init__(self, value):
            self._value = value

        def isChecked(self):
            return self._value

    panel = LightmapBakerSlots.__new__(LightmapBakerSlots)
    device_combo = _DeviceCombo()
    panel.cmb_device_init(device_combo)
    panel.ui = type(
        "U", (), {"cmb_device": device_combo, "chk_environment": _Check(False)}
    )()
    check(
        "the Device row offers Auto / GPU / CPU",
        [v for _t, v in device_combo.items] == ["AUTO", "GPU", "CPU"],
        device_combo.items,
    )
    check("Device defaults to Auto", panel._device() == "AUTO")
    check(
        "the panel reads the Include Environment checkbox",
        panel._include_environment() is False,
    )
    device_combo.setCurrentIndex(2)
    check("selecting CPU reads back as CPU", panel._device() == "CPU")

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

    # --- a HIDDEN mesh is baked, not refused ------------------------------
    # Production blocker: one hidden mesh among 48 in the OFFICE_ENV room aborted
    # the whole lightmap job. ``bpy.ops.object.mode_set`` refuses a hidden object
    # ("Cannot edit hidden object"), so create_lightmap_uvs raised out of the
    # entire batch; and even past that, Cycles skips a ``hide_render`` object, so
    # the map would have come back exact black. Hiding is an authoring state (and
    # through the Maya bridge's visibility manifest it can be ANIMATED, so the
    # object is on screen at some other frame) -- never a reason to refuse a bake.
    from blendertk.core_utils._core_utils import CoreUtils

    LightmapBaker().revert()
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 6))
    bpy.context.active_object.data.energy = 5.0

    # Clear of every cube earlier sections left at the origin: coincident
    # geometry occludes the trace and bakes black for a reason that has nothing
    # to do with visibility, which would make this whole section lie.
    bpy.ops.mesh.primitive_cube_add(location=(12, 0, 0))
    control = bpy.context.active_object
    control.name = "control_cube"
    btk.assign_mat(control, mat)
    bpy.ops.mesh.primitive_cube_add(location=(16, 0, 0))
    hid = bpy.context.active_object
    hid.name = "hidden_cube"
    btk.assign_mat(hid, mat)
    bpy.ops.mesh.primitive_cube_add(location=(20, 0, 0))
    sibling = bpy.context.active_object
    sibling.name = "hidden_sibling"
    for o in (hid, sibling):
        o.hide_viewport = True
        o.hide_render = True
        o.hide_set(True)

    # The primitive itself: one target revealed, its neighbours untouched. That
    # scope is the whole design -- revealing the batch would let hidden geometry
    # occlude and bounce into every OTHER object's bake, a lighting change.
    with CoreUtils.visible_override(hid):
        check(
            "visible_override reveals its target",
            not hid.hide_viewport and not hid.hide_render and not hid.hide_get(),
            f"viewport={hid.hide_viewport} render={hid.hide_render} eye={hid.hide_get()}",
        )
        check(
            "...and leaves a hidden SIBLING hidden",
            sibling.hide_viewport and sibling.hide_render and sibling.hide_get(),
        )
    check(
        "visible_override restores every flag it cleared",
        hid.hide_viewport and hid.hide_render and hid.hide_get(),
        f"viewport={hid.hide_viewport} render={hid.hide_render} eye={hid.hide_get()}",
    )

    raised = None
    try:
        btk.create_lightmap_uvs([hid])
    except Exception as exc:  # noqa: BLE001 — the regression IS the raise
        raised = exc
    check("create_lightmap_uvs survives a hidden object", raised is None, f"{raised}")
    check(
        "...and still gives it a lightmap layer",
        len(hid.data.uv_layers) >= 2,
        f"{[l.name for l in hid.data.uv_layers]}",
    )
    check("...leaving it hidden afterwards", hid.hide_viewport and hid.hide_render)

    hidden_baker = LightmapBaker.from_preset(
        "preview", resolution=64, samples=8, denoise=False, device="CPU"
    )
    btk.create_lightmap_uvs([control])
    # The control is the whole point: "black" only means "hidden broke it" if an
    # identical NEVER-hidden twin in the same scene, same light, same settings
    # comes back lit. Without it a dark scene reads as a passing bug.
    control_max = float(
        _rgb(
            hidden_baker.bake_separated(
                [control], output_dir=tmp_dir, suffix="_Control"
            )[control.name]
        ).max()
    )
    check(
        "control: a visible twin bakes lit (the fixture is sound)",
        control_max > 0.0,
        f"max={control_max:.5f}",
    )

    hidden_result = hidden_baker.bake_separated(
        [hid], output_dir=tmp_dir, suffix="_Hidden"
    )
    hidden_map = hidden_result.get(hid.name, "")
    check(
        "a hidden object still bakes",
        bool(hidden_map) and os.path.isfile(hidden_map),
        f"{hidden_result}",
    )
    if hidden_map and os.path.isfile(hidden_map):
        hidden_rgb = _rgb(hidden_map)
        check(
            "...to a LIT map, not the exact black Cycles gives a hide_render object",
            float(hidden_rgb.max()) > 0.0,
            f"max={float(hidden_rgb.max()):.5f} vs control {control_max:.5f}",
        )
    check(
        "...and the bake restores its visibility too",
        hid.hide_viewport and hid.hide_render and hid.hide_get(),
        f"viewport={hid.hide_viewport} render={hid.hide_render} eye={hid.hide_get()}",
    )

    # --- hidden by its COLLECTION, not by its own flags --------------------
    # The case object flags cannot reach: with the parent collection hidden, an
    # object whose every own flag is clear is not in the depsgraph at all. And
    # the obvious remedy is wrong -- clearing the COLLECTION's flags reveals
    # every other member, so hidden geometry starts occluding and bouncing into
    # this object's bake. The primitive links the target into the scene's master
    # collection instead, which reveals it and nothing else.
    grp = bpy.data.collections.new("hidden_grp")
    bpy.context.scene.collection.children.link(grp)
    bpy.ops.mesh.primitive_cube_add(location=(24, 0, 0))
    grouped = bpy.context.active_object
    grouped.name = "grouped_cube"
    btk.assign_mat(grouped, mat)
    bpy.ops.mesh.primitive_cube_add(location=(28, 0, 0))
    grouped_sibling = bpy.context.active_object
    grouped_sibling.name = "grouped_sibling"
    for o in (grouped, grouped_sibling):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        grp.objects.link(o)
    grp.hide_viewport = True
    grp.hide_render = True

    with CoreUtils.visible_override(grouped):
        check(
            "a collection-hidden object is reachable inside the override",
            grouped.visible_get(),
            f"visible_get={grouped.visible_get()}",
        )
        check(
            "...without revealing its collection-mates",
            not grouped_sibling.visible_get() and grp.hide_viewport and grp.hide_render,
            f"sibling={grouped_sibling.visible_get()} grp={grp.hide_viewport}",
        )
    check(
        "...and the collection link is undone afterwards",
        not grouped.visible_get()
        and grouped.name not in bpy.context.scene.collection.objects,
        f"visible={grouped.visible_get()}",
    )

    btk.create_lightmap_uvs([grouped])
    grouped_map = hidden_baker.bake_separated(
        [grouped], output_dir=tmp_dir, suffix="_Grouped"
    ).get(grouped.name, "")
    if grouped_map and os.path.isfile(grouped_map):
        grouped_max = float(_rgb(grouped_map).max())
        check(
            "a collection-hidden object bakes LIT (not the exact black Cycles "
            "gives an excluded object)",
            grouped_max > 0.0,
            f"max={grouped_max:.5f} vs control {control_max:.5f}",
        )
    else:
        check("a collection-hidden object bakes at all", False, "no map written")

    LightmapBaker().revert()

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(l.startswith("OK") for l in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
