"""blendertk lightmap baker headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_lightmap_baker.py

Exercises the engine end-to-end (create_lightmap_uvs → Cycles bake → commit → revert) and the
Unity bridge (DataNodes manifest). Tiny resolution / samples so the real bake stays fast.
"""

import json
import math
import os
import shutil
import sys
import tempfile
import traceback
import warnings

import numpy as np  # ships with Blender

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
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
    import pythontk as ptk
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.mat_utils.texture_baker import TextureBaker
    from unittest import mock as _mock

    # A re-bake sets the maps it superseded aside -- into this machine's
    # Recycle Bin wherever the volume has one. Pinned for the whole script to a
    # volume with none, so every bake here sets its leftovers beside them in
    # ``_superseded`` inside the scratch folder, never into the user's bin
    # (``ptk.FileUtils.move_to_trash`` has its own tests in pythontk).
    _no_trash = _mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None)
    _no_trash.start()

    # --- presets -----------------------------------------------------------
    store = LightmapBaker.preset_store()
    check(
        "built-in presets ship",
        set(store.list()) >= {"preview", "mobile", "desktop"},
        f"{store.list()}",
    )
    # Cycles-appropriate sampling: the presets originally mirrored mayatk's Arnold
    # tiers (2/4/8 AA samples), which as CYCLES path-tracing samples are pure noise.
    baker = LightmapBaker.from_preset("mobile")
    check(
        "from_preset reads the dials",
        baker.resolution == 1024 and baker.samples == 1024,
        f"{baker.resolution}/{baker.samples}",
    )
    # "quest" was renamed "mobile" (mirrors mayatk): a script naming the old tier
    # still bakes at its dials, and is told the new name.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old = LightmapBaker.from_preset("quest")
    check(
        "the renamed quest tier still builds mobile, with a notice",
        (old.resolution, old.samples, old.bounces) == (1024, 1024, 4)
        and any(
            issubclass(w.category, DeprecationWarning) and "mobile" in str(w.message)
            for w in caught
        ),
        f"{[str(w.message) for w in caught]}",
    )
    baker = LightmapBaker.from_preset("preview", resolution=64, samples=1)
    check(
        "overrides win over the preset", baker.resolution == 64 and baker.samples == 1
    )
    baker = LightmapBaker.from_preset("preview", denoise=False, device="CPU")
    check(
        "constructor-arg overrides pass through from_preset",
        baker.denoise is False and baker.device == "CPU",
        f"denoise={baker.denoise} device={baker.device}",
    )

    # GI bounce depth rides the tier, exactly as mayatk's gi_depth does. Unpinned,
    # a Cycles bake ran at whatever the SCENE last rendered with (4 on a factory
    # startup, anything at all in a saved .blend) -- so the same scene baked to a
    # different brightness in two sessions, and to a different one again than the
    # Arnold twin, with nothing in either output to say why. In a closed room each
    # extra bounce adds another rho^n term, which is why it is a tier dial and not
    # a detail.
    tiers = {
        n: LightmapBaker.from_preset(n).bounces
        for n in ("preview", "mobile", "desktop", "hero")
    }
    check(
        "every preset carries a bounce depth",
        all(isinstance(v, int) and v >= 1 for v in tiers.values()),
        f"{tiers}",
    )
    check(
        "bounces rise with the tier",
        tiers["preview"] <= tiers["mobile"] <= tiers["desktop"],
        f"{tiers}",
    )
    # The SAME depth as mayatk's tier of the same name: both engines count bounces
    # alike -- a grey calibration room baked by Cycles and by Arnold agreed with each
    # other and the analytic value to 2% at 0 and at 2 bounces (2026-10-01). The
    # 0.76x once measured against an Arnold depth-2 bake was the scene, not the
    # depth: the target's own material (no white card) and area lights crossing
    # double-scaled and turned 90 degrees. Four is the default: 95% of a grey room's
    # converged light.
    check(
        "every tier bakes mayatk's depth for that tier",
        tiers == {"preview": 2, "mobile": 4, "desktop": 6, "hero": 8},
        f"{tiers}",
    )
    check(
        "only preview -- the tier that advertises speed -- trades bounces",
        tiers["preview"] < tiers["mobile"],
        f"{tiers}",
    )
    check(
        "bounces is overridable like the other constructor args",
        LightmapBaker.from_preset("mobile", bounces=5).bounces == 5,
    )
    check(
        "bounces reaches the primitive that applies it",
        LightmapBaker(bounces=7)._texture_baker.bounces == 7,
    )
    # The BARE constructor is the one path that never sees a tier (a scripted
    # caller, the panel's revert-only instance): it bakes the default tier's dials,
    # as mayatk's does, so a script that names no tier gets what the panel would.
    bare, mobile = LightmapBaker(), LightmapBaker.from_preset("mobile")
    check(
        "the bare constructor bakes the default (mobile) tier",
        (bare.resolution, bare.samples, bare.bounces)
        == (mobile.resolution, mobile.samples, mobile.bounces)
        and TextureBaker().bounces == 4,
        f"{(bare.resolution, bare.samples, bare.bounces)}",
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
    btk.DataNodes.write(ptk.Scope.DELIVERABLE, "probe", "hello")
    check(
        "DataNodes roundtrips a string",
        btk.DataNodes.read(ptk.Scope.DELIVERABLE, "probe") == "hello",
    )
    check("data_export Empty exists", bpy.data.objects.get("data_export") is not None)
    btk.DataNodes.write(ptk.Scope.DELIVERABLE, "probe", None)
    check(
        "clearing leaves the carrier, reads back None",
        bpy.data.objects.get("data_export") is not None
        and btk.DataNodes.read(ptk.Scope.DELIVERABLE, "probe") is None,
    )

    # --- lightmap UVs ------------------------------------------------------
    btk.create_lightmap_uvs([cube])
    check(
        "lightmap UV layer created (2nd channel)",
        len(cube.data.uv_layers) >= 2,
        f"{[uv.name for uv in cube.data.uv_layers]}",
    )
    check("find_lightmap_uv_set detects it", btk.find_lightmap_uv_set(cube) is not None)
    # Idempotent: a second call reuses, doesn't pile on layers.
    n = len(cube.data.uv_layers)
    btk.create_lightmap_uvs([cube])
    check("create_lightmap_uvs is idempotent", len(cube.data.uv_layers) == n)
    # A regenerated layout that still overlaps is named (mirror of mayatk;
    # a 9544-face production door kept 375 overlapping faces, 2026-10-08).
    import contextlib as _ctx
    import io as _io
    from blendertk.uv_utils._uv_utils import UvUtils as _UvUtils

    _overlap_out = _io.StringIO()
    with (
        _mock.patch.object(_UvUtils, "_is_bakeable_lightmap", return_value=False),
        _ctx.redirect_stdout(_overlap_out),
    ):
        btk.create_lightmap_uvs([cube], force=True)
    check(
        "a regenerated lightmap layout that still overlaps is named",
        "overlap" in _overlap_out.getvalue() and cube.name in _overlap_out.getvalue(),
        _overlap_out.getvalue()[-200:],
    )

    # --- lighting-only bake + commit (the default path) -------------------
    result = baker.bake_separated([cube], output_dir=tmp_dir, suffix="_Lightmap")
    check("bake produced a map", cube.name in result, f"{result}")
    path = result.get(cube.name, "")
    check(
        "EXR written to disk",
        path and os.path.isfile(path) and os.path.getsize(path) > 0,
        path,
    )
    check(
        "name follows the affix", path.endswith("_Lightmap.exr"), os.path.basename(path)
    )
    check(
        "material kept (non-destructive bake)",
        any(s.material is mat for s in cube.material_slots),
    )

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
    raw = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    check("manifest published to data_export", bool(raw), repr(raw)[:80])
    manifest = json.loads(raw) if raw else {}
    rec = (manifest.get("objects") or [{}])[0]
    check(
        "manifest record has camelCase keys + uvIndex 1",
        rec.get("name") == cube.name
        and rec.get("uvIndex") == 1
        and "scaleOffset" in rec
        and os.path.basename(path) == rec.get("map"),
        f"{rec}",
    )

    # --- revert (subtractive) ---------------------------------------------
    reverted = baker.revert([cube])
    check(
        "revert clears the marker",
        LightmapBaker.LIGHTMAP_INFO_PROP not in cube and cube.name in reverted,
    )
    check(
        "manifest cleared when nothing remains",
        (ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes) or "") == "",
    )

    # --- intensity applied into the texels, once per unique file ----------
    # (mirror of mayatk: Unity ignores the manifest intensity field, so a
    # non-1.0 value must be baked into the map -- shared files scale ONCE.)
    ipath = os.path.join(tmp_dir, "intensity_probe.exr")
    src = bpy.data.images.new(
        "intSrc", width=4, height=4, alpha=True, float_buffer=True
    )
    src.pixels.foreach_set(np.tile(np.array([0.25, 0.25, 0.25, 1.0], np.float32), 16))
    src.filepath_raw = ipath
    src.file_format = "OPEN_EXR"
    src.save()
    bpy.data.images.remove(src)

    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    cube_b = bpy.context.active_object
    import warnings

    with warnings.catch_warnings(record=True) as caught_intensity:
        warnings.simplefilter("always")
        baker.commit_lightmap({cube.name: ipath, cube_b.name: ipath}, intensity=2.0)
    check(
        "commit_lightmap(intensity=) is deprecated (bake(intensity=) replaces it)",
        any(issubclass(w.category, DeprecationWarning) for w in caught_intensity),
    )
    reload = bpy.data.images.load(ipath)
    ibuf = np.empty(len(reload.pixels), dtype=np.float32)
    reload.pixels.foreach_get(ibuf)
    bpy.data.images.remove(reload)
    check(
        "intensity x2 applied once (0.25 -> 0.5, not 1.0)",
        abs(float(ibuf.reshape(-1, 4)[0, 0]) - 0.5) < 1e-3,
        f"{ibuf.reshape(-1, 4)[0, :3]}",
    )
    raw_i = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    recs = (json.loads(raw_i).get("objects") if raw_i else []) or []
    check(
        "manifest records intensity 2.0 for both objects",
        len(recs) == 2 and all(r.get("intensity") == 2.0 for r in recs),
        f"{[(r.get('name'), r.get('intensity')) for r in recs]}",
    )
    baker.revert([cube, cube_b])
    check(
        "intensity commit reverts clean",
        (ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes) or "") == "",
    )

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
    check(
        "uv_rects recorded on the marker (uvRect)",
        info_a.get("uvRect") == rect,
        f"{info_a}",
    )
    check("identity uv_rect records no uvRect key", "uvRect" not in info_b, f"{info_b}")
    raw_r = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    recs_r = (json.loads(raw_r).get("objects") if raw_r else []) or []
    check(
        "manifest scaleOffset stays identity with uv_rects",
        recs_r
        and all(
            r.get("scaleOffset") == [1.0, 1.0, 0.0, 0.0] and "uvRect" not in r
            for r in recs_r
        ),
        f"{recs_r}",
    )
    baker.revert([cube, cube_b])
    check(
        "uv_rects commit reverts clean",
        (ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes) or "") == "",
    )

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
        return (
            float(uv[:, 0].min()),
            float(uv[:, 0].max()),
            float(uv[:, 1].min()),
            float(uv[:, 1].max()),
        )

    atlas_baker = LightmapBaker(resolution=64, samples=1)
    maps = atlas_baker.bake_separated([a, b], output_dir=tmp_dir, suffix="_Lightmap")
    check("atlas: both objects baked", set(maps) == {a.name, b.name}, f"{maps}")
    src_a, src_b = maps.get(a.name), maps.get(b.name)
    before_a = uv_bbox(a)  # the shared 0-1 unwrap -- pack_atlas must NOT touch it

    packed = atlas_baker.pack_atlas(maps, output_dir=tmp_dir, suffix="_Lightmap")
    check(
        "atlas: both objects in the packed result",
        set(packed) == {a.name, b.name},
        f"{packed}",
    )
    atlas_paths = {p for p, _so in packed.values()}
    check(
        "atlas: one shared atlas for the shared material",
        len(atlas_paths) == 1,
        f"{atlas_paths}",
    )
    atlas_path = next(iter(atlas_paths))
    check(
        "atlas: shared EXR written to disk",
        os.path.isfile(atlas_path) and os.path.getsize(atlas_path) > 0,
        atlas_path,
    )
    check(
        "atlas: per-object source maps consolidated (removed)",
        not os.path.exists(src_a) and not os.path.exists(src_b),
        f"{src_a} | {src_b}",
    )

    def rect_ok(so):
        sx, sy, ox, oy = so
        return (
            sx < 1.0
            and sy < 1.0
            and ox >= -1e-6
            and oy >= -1e-6
            and ox + sx <= 1.0001
            and oy + sy <= 1.0001
        )

    check(
        "atlas: rects are non-identity and inside the unit square",
        all(rect_ok(so) for _, so in packed.values()),
        f"{[so for _, so in packed.values()]}",
    )

    # The rect is the ENGINE BINDING, not a UV edit: the shared [0,1] unwrap must
    # come through the pack bit-identical (that is what lets instances share the mesh).
    check(
        "atlas: object A's lightmap UVs untouched by the pack",
        all(abs(x - y) < 1e-6 for x, y in zip(before_a, uv_bbox(a))),
        f"before={before_a} after={uv_bbox(a)}",
    )

    atlas_baker.commit_lightmap(
        {n: p for n, (p, _so) in packed.items()},
        scale_offsets={n: so for n, (_p, so) in packed.items()},
    )
    info_a = json.loads(a[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "atlas: commit records the rect as the scaleOffset binding, no uvRect",
        info_a.get("scaleOffset") == [float(v) for v in packed[a.name][1]]
        and "uvRect" not in info_a,
        f"{info_a}",
    )
    raw_a = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    recs_a = (json.loads(raw_a).get("objects") if raw_a else []) or []
    check(
        "atlas: manifest publishes the real per-object scaleOffset",
        {r["name"]: r.get("scaleOffset") for r in recs_a}
        == {n: [float(v) for v in so] for n, (_p, so) in packed.items()},
        f"{recs_a}",
    )

    atlas_baker.revert([a, b])
    after_a = uv_bbox(a)
    check(
        "atlas: revert leaves the unit-square layout intact (nothing to restore)",
        all(abs(x - y) < 1e-6 for x, y in zip(before_a, after_a)),
        f"before={before_a} after={after_a}",
    )

    # --- legacy uvRect commits still revert (old scenes repacked UVs in place) ---
    legacy_rect = [0.5, 0.5, 0.25, 0.25]
    lm_name = btk.find_lightmap_uv_set(a)
    # simulate an old pack
    LightmapRecords._transform_lightmap_uvs(a, lm_name, legacy_rect)
    a[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps(
        {
            "map": "legacy.exr",
            "uv_set": lm_name,
            "intensity": 1.0,
            "scaleOffset": [1.0, 1.0, 0.0, 0.0],
            "mode": "separated",
            "uvRect": legacy_rect,
        }
    )
    atlas_baker.revert([a])
    check(
        "legacy: revert inverts an old uvRect marker's UV repack",
        all(abs(x - y) < 1e-3 for x, y in zip(before_a, uv_bbox(a))),
        f"before={before_a} after={uv_bbox(a)}",
    )

    # ...and a bake MIGRATES one instead, losslessly: the UVs come back to 0-1 and
    # the rect moves into the binding, so the object still samples its own cell of
    # the old atlas. Restored without the fold, the marker kept an identity binding
    # over unsqueezed UVs -- the WHOLE atlas -- for any object the bake then failed on.
    LightmapRecords._transform_lightmap_uvs(a, lm_name, legacy_rect)
    a[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps(
        {
            "map": "legacy.exr",
            "uv_set": lm_name,
            "scaleOffset": [1.0, 1.0, 0.0, 0.0],
            "uvRect": legacy_rect,
        }
    )
    migrated = LightmapRecords.migrate_legacy([a])
    info_m = json.loads(a[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "legacy: migrate_legacy restores the UVs and folds the rect into the binding",
        migrated == [a.name]
        and all(abs(x - y) < 1e-3 for x, y in zip(before_a, uv_bbox(a)))
        and "uvRect" not in info_m
        and info_m.get("scaleOffset") == legacy_rect,
        f"{migrated} {info_m}",
    )
    check("legacy: migration is idempotent", LightmapRecords.migrate_legacy([a]) == [])

    # ...and a rect whose recorded UV map is gone (deleted, or renamed) has no
    # remap left to undo: dropped, nothing restored or folded. Left in place,
    # the next bake's commit carried it onto the UV map that bake built, for a
    # later migration to invert over the fresh unwrap.
    before_gone = uv_bbox(a)
    a[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps(
        {
            "map": "legacy.exr",
            "uv_set": "deleted_uv_map",
            "scaleOffset": [1.0, 1.0, 0.0, 0.0],
            "uvRect": legacy_rect,
        }
    )
    gone = LightmapRecords.migrate_legacy([a])
    info_g = json.loads(a[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "legacy: a rect whose UV map is gone is dropped; binding and UVs untouched",
        gone == [a.name]
        and "uvRect" not in info_g
        and info_g.get("scaleOffset") == [1.0, 1.0, 0.0, 0.0]
        and all(abs(x - y) < 1e-6 for x, y in zip(before_gone, uv_bbox(a))),
        f"{gone} {info_g}",
    )
    atlas_baker.revert([a])

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
        buf = np.tile(np.array([value, value, value, 1.0], np.float32), 256).reshape(
            16, 16, 4
        )
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
    packed_dead = atlas_baker.pack_atlas(
        dead_maps, output_dir=tmp_dir, suffix="_DeadLM"
    )
    dpath = next(iter(p for p, _so in packed_dead.values()))
    dimg = bpy.data.images.load(dpath)
    dbuf = np.empty(len(dimg.pixels), np.float32)
    dimg.pixels.foreach_get(dbuf)
    drgb = dbuf.reshape(dimg.size[1], dimg.size[0], dimg.channels)[..., :3]
    bpy.data.images.remove(dimg)
    n_zero = int((~(drgb.max(axis=-1) > 0)).sum())
    check(
        "atlas: rendered-dead texels are healed (no exact zeros ship)",
        n_zero == 0,
        f"{n_zero} zero texel(s)",
    )
    res_px = atlas_baker.resolution
    check(
        "atlas: published rects aim at border-texel centers",
        all(
            abs((ox * res_px) % 1.0 - 0.5) < 1e-4
            and abs(((ox + sx) * res_px) % 1.0 - 0.5) < 1e-4
            and abs((oy * res_px) % 1.0 - 0.5) < 1e-4
            and abs(((oy + sy) * res_px) % 1.0 - 0.5) < 1e-4
            for _p, (sx, sy, ox, oy) in packed_dead.values()
        ),
        f"{[so for _p, so in packed_dead.values()]}",
    )

    # --- a partial-coverage island is cropped into its cell ----------------
    # Twin of mayatk (test_cropped_islands_sample_disjoint_atlas_regions):
    # create_lightmap_uvs REUSES a bakeable layout, and a Maya scene's own set
    # need not fill 0-1 -- the production walls span u 0..1/3. Uncropped, two
    # thirds of every wall cell held the bake's margin fill (the smeared
    # tiles of the 2026-10-01 production room bake) and the island got a third of
    # its texels. The tile is cropped to the island, the crop folded into the
    # published rect, and the bake sized so the island lands at cell density.
    partials = []
    for src in (a, b):
        dup = src.copy()
        dup.data = src.data.copy()
        dup.name = "Partial" + src.name
        bpy.context.collection.objects.link(dup)
        layer = dup.data.uv_layers[btk.find_lightmap_uv_set(dup)]
        uv = np.empty(len(layer.data) * 2, np.float32)
        layer.data.foreach_get("uv", uv)
        uv[0::2] *= 1.0 / 3.0  # squeeze the islands into u 0..1/3
        layer.data.foreach_set("uv", uv)
        partials.append(dup)

    def _exr_partial(name, value):
        path = os.path.join(tmp_dir, name)
        img = bpy.data.images.new(name, 24, 24, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"
        buf = np.full((24, 24, 4), 9.0, np.float32)  # margin fill: extreme
        buf[:, :8, :3] = value  # the island's third of the map
        buf[..., 3] = 1.0
        img.pixels.foreach_set(buf.reshape(-1))
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    packed_partial = atlas_baker.pack_atlas(
        {
            partials[0].name: _exr_partial("partialA.exr", 1.0),
            partials[1].name: _exr_partial("partialB.exr", 0.5),
        },
        output_dir=tmp_dir,
        suffix="_PartialLM",
    )
    ppath = next(iter(p for p, _so in packed_partial.values()))
    pimg = bpy.data.images.load(ppath)
    pbuf = np.empty(len(pimg.pixels), np.float32)
    pimg.pixels.foreach_get(pbuf)
    bpy.data.images.remove(pimg)
    check(
        "atlas: a partial island's margin fill never reaches its cell",
        float(pbuf.reshape(-1, 4)[:, :3].max()) <= 1.0 + 1e-3,
        f"max {float(pbuf.reshape(-1, 4)[:, :3].max()):.3f}",
    )
    res_px = atlas_baker.resolution
    edges = []
    for obj in partials:
        _p, (sx, sy, ox, oy) = packed_partial[obj.name]
        tris = btk.UvUtils.get_uv_triangles(obj, btk.find_lightmap_uv_set(obj))
        u0, u1 = float(tris[..., 0].min()), float(tris[..., 0].max())
        edges += [(ox + sx * u0) * res_px, (ox + sx * u1) * res_px]
    check(
        "atlas: a cropped island's u edges sample border-texel centers",
        all(abs(e % 1.0 - 0.5) < 1e-3 for e in edges),
        f"{edges}",
    )
    tw, th = atlas_baker._tile_size(partials[0], (10, 10))
    check(
        "atlas: a partial island bakes above its cell by its crop",
        28 <= tw <= 31 and th <= 11,
        f"{(tw, th)}",
    )
    for dup in partials:
        bpy.data.objects.remove(dup, do_unlink=True)

    # --- delivered maps are written like mayatk's: half float, ZIP ---------
    # Image.save() wrote uncompressed 32-bit -- 16.8 MB per 1024 atlas on the
    # production office against Arnold's ~2 MB -- and the per-object writes
    # (heal, intensity) skipped the NaN/clamp sanitize the atlas had.
    def _exr_header(path):
        """``(pixel types of the channels, compression id)`` from the EXR header."""
        import struct

        with open(path, "rb") as f:
            data = f.read(4096)
        pos, types, compression = 8, [], None
        while data[pos] != 0:
            end = data.index(b"\0", pos)
            name = data[pos:end]
            tend = data.index(b"\0", end + 1)
            (size,) = struct.unpack("<i", data[tend + 1 : tend + 5])
            value = data[tend + 5 : tend + 5 + size]
            if name == b"channels":
                p = 0
                while value[p] != 0:
                    p = value.index(b"\0", p) + 1
                    types.append(struct.unpack("<i", value[p : p + 4])[0])
                    p += 16
            elif name == b"compression":
                compression = value[0]
            pos = tend + 5 + size
        return types, compression

    HALF, ZIP = 1, 3
    types, compression = _exr_header(dpath)
    check(
        "atlas: written half-float + ZIP, like mayatk's maps",
        types and set(types) == {HALF} and compression == ZIP,
        f"pixel types={types} compression={compression}",
    )

    nan_map = _exr_dead("nanSolo.exr", 0.9, 0)
    nimg = bpy.data.images.load(nan_map)
    nimg.colorspace_settings.name = "Non-Color"
    nbuf = np.empty(len(nimg.pixels), np.float32)
    nimg.pixels.foreach_get(nbuf)
    nbuf.reshape(16, 16, 4)[3, 5, :3] = (np.nan, np.inf, -1.0)
    nimg.pixels.foreach_set(nbuf)
    nimg.save()
    bpy.data.images.remove(nimg)
    atlas_baker._heal_dead_texels(nan_map)
    nimg = bpy.data.images.load(nan_map)
    nimg.colorspace_settings.name = "Non-Color"
    nbuf = np.empty(len(nimg.pixels), np.float32)
    nimg.pixels.foreach_get(nbuf)
    bpy.data.images.remove(nimg)
    nrgb = nbuf.reshape(16, 16, 4)[..., :3]
    types, compression = _exr_header(nan_map)
    check(
        "per-object map: sanitized (finite, in [0, 65504]) and half-float + ZIP",
        bool(np.isfinite(nrgb).all())
        and float(nrgb.min()) >= 0.0
        and float(nrgb.max()) <= 65504.0
        and set(types) == {HALF}
        and compression == ZIP,
        f"finite={bool(np.isfinite(nrgb).all())} min={float(np.nanmin(nrgb))} "
        f"types={types} compression={compression}",
    )

    # A scene saved with Video (or Multi-Layer EXR) output. Blender 5.x gates
    # file_format by media_type, so pinning OPEN_EXR alone raised: the atlas fell
    # back to its unhealed tiles, every heal and intensity write was skipped, and
    # the denoise kept the raw bake. 4.x has no media_type.
    out_settings = bpy.context.scene.render.image_settings
    if hasattr(out_settings, "media_type"):
        video_maps = {
            a.name: _exr_dead("videoA.exr", 1.5, 8),
            b.name: _exr_dead("videoB.exr", 0.8, 0),
        }
        video_raw = _exr_dead("videoRaw.exr", 0.9, 4)
        out_prior = (out_settings.media_type, out_settings.file_format)
        out_settings.media_type, out_settings.file_format = "VIDEO", "FFMPEG"
        try:
            packed_video = atlas_baker.pack_atlas(
                video_maps, output_dir=tmp_dir, suffix="_VideoLM"
            )
            video_atlas = {p for p, _so in packed_video.values()}
            types, compression = (
                _exr_header(next(iter(video_atlas)))
                if len(video_atlas) == 1
                else ([], None)
            )
            check(
                "video output scene: the atlas still assembles, half-float + ZIP",
                len(video_atlas) == 1 and set(types) == {HALF} and compression == ZIP,
                f"maps={len(video_atlas)} types={types} compression={compression}",
            )
            denoised = TextureBaker.denoise_images([video_raw])
            check(
                "video output scene: the denoise still runs",
                set(denoised) == {video_raw},
                f"{denoised}",
            )
            check(
                "video output scene: its output settings come back unchanged",
                (out_settings.media_type, out_settings.file_format)
                == ("VIDEO", "FFMPEG"),
                f"{out_settings.media_type} / {out_settings.file_format}",
            )
        finally:
            out_settings.media_type, out_settings.file_format = out_prior
    else:
        lines.append("OK   (skipped) video output scene: no media_type before 5.0")

    solo_dead = atlas_baker.pack_atlas(
        {cube.name: _exr_dead("deadSolo.exr", 1.2, 4)},
        output_dir=tmp_dir,
        suffix="_DeadSolo",
    )
    spath, srect = solo_dead[cube.name]
    simg = bpy.data.images.load(spath)
    sbuf = np.empty(len(simg.pixels), np.float32)
    simg.pixels.foreach_get(sbuf)
    srgb = sbuf.reshape(simg.size[1], simg.size[0], simg.channels)[..., :3]
    bpy.data.images.remove(simg)
    check(
        "solo: adopted map healed of exact zeros (identity rect kept)",
        int((~(srgb.max(axis=-1) > 0)).sum()) == 0
        and srect == list(LightmapBaker._IDENTITY_SCALE_OFFSET),
        f"zeros={int((~(srgb.max(axis=-1) > 0)).sum())} rect={srect}",
    )

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
    check(
        "plan: objects sharing a material land in one group",
        len(plan) == 1 and planned == {a.name, b.name},
        f"{plan}",
    )
    sizes = atlas_baker.plan_sizes(plan)
    res = atlas_baker.resolution
    check(
        "plan: each tile is sized to its rect, not the whole atlas",
        set(sizes) == planned and all(w < res and h < res for w, h in sizes.values()),
        f"{sizes} (atlas {res})",
    )
    check(
        "plan: the tiles together fit inside one atlas (no supersampling waste)",
        sum(w * h for w, h in sizes.values()) <= res * res,
        f"{sum(w * h for w, h in sizes.values())} vs {res * res}",
    )

    # Intermediates must never reach the destination: a project's texture folder is not a
    # scratch dir, and nothing downstream ever cleans one up.
    atlas_dir = os.path.join(tmp_dir, "atlas_out")
    packed2 = atlas_baker.bake_atlas([a, b], output_dir=atlas_dir, suffix="_Lightmap")
    check(
        "bake_atlas: both objects packed",
        set(packed2) == {a.name, b.name},
        f"{packed2}",
    )
    written = sorted(os.listdir(atlas_dir)) if os.path.isdir(atlas_dir) else []
    check(
        "bake_atlas: the output dir holds ONLY the finished atlas",
        len(written) == 1 and written[0].endswith(".exr"),
        f"{written}",
    )
    check(
        "bake_atlas: every returned path lives in the output dir",
        all(
            os.path.dirname(os.path.abspath(p)) == os.path.abspath(atlas_dir)
            for p, _so in packed2.values()
        ),
        f"{packed2}",
    )
    atlas_baker.revert([a, b])

    # A solo group assembles no atlas, but its map is still a RESULT -- it has to be moved
    # out of the work dir, or it would be swept and the commit left pointing at nothing.
    solo_dir = os.path.join(tmp_dir, "solo_out")
    solo = atlas_baker.bake_atlas([cube], output_dir=solo_dir, suffix="_Lightmap")
    check(
        "bake_atlas: a solo group's map is published to the output dir too",
        bool(solo)
        and all(
            os.path.isfile(p)
            and os.path.dirname(os.path.abspath(p)) == os.path.abspath(solo_dir)
            for p, _so in solo.values()
        ),
        f"{solo}",
    )
    check(
        "bake_atlas: a solo group leaves no tile behind either",
        len(os.listdir(solo_dir)) == len({p for p, _so in solo.values()}),
        f"{sorted(os.listdir(solo_dir))}",
    )
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
    check(
        "instances: one lightmap layer on the shared datablock",
        ia.data is ib.data
        and sum(1 for uv in ia.data.uv_layers if "lightmap" in uv.name.lower()) == 1,
        f"{[uv.name for uv in ia.data.uv_layers]}",
    )

    inst_plan = atlas_baker.atlas_plan([ia, ib])
    inst_entries = [e for entries in inst_plan.values() for e in entries]
    check(
        "instances: atlas_plan gives every instance its own entry",
        {n for n, _r in inst_entries} == {"InstA", "InstB"},
        f"{inst_plan}",
    )

    def rect_area(r):
        return float(r[0]) * float(r[1])

    inst_rects = {n: r for n, r in inst_entries}
    check(
        "instances: the scaled copy earns the larger rect (world-area weights)",
        rect_area(inst_rects["InstB"]) > rect_area(inst_rects["InstA"]),
        f"{inst_rects}",
    )

    inst_dir = os.path.join(tmp_dir, "inst_out")
    inst_before = uv_bbox(ia)
    inst_packed = atlas_baker.bake_atlas(
        [ia, ib], output_dir=inst_dir, suffix="_Lightmap"
    )
    check(
        "instances: both baked into one shared atlas",
        set(inst_packed) == {"InstA", "InstB"}
        and len({p for p, _so in inst_packed.values()}) == 1,
        f"{inst_packed}",
    )
    check(
        "instances: two DISTINCT non-identity rects",
        inst_packed["InstA"][1] != inst_packed["InstB"][1]
        and all(rect_ok(so) for _p, so in inst_packed.values()),
        f"{[so for _p, so in inst_packed.values()]}",
    )
    check(
        "instances: the shared unwrap is untouched",
        all(abs(x - y) < 1e-6 for x, y in zip(inst_before, uv_bbox(ia))),
        f"before={inst_before} after={uv_bbox(ia)}",
    )

    atlas_baker.commit_lightmap(
        {n: p for n, (p, _so) in inst_packed.items()},
        scale_offsets={n: so for n, (_p, so) in inst_packed.items()},
    )
    raw_inst = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    recs_inst = {r["name"]: r for r in (json.loads(raw_inst).get("objects") or [])}
    check(
        "instances: manifest carries one record per instance with its own rect",
        {"InstA", "InstB"} <= set(recs_inst)
        and recs_inst["InstA"]["scaleOffset"] != recs_inst["InstB"]["scaleOffset"],
        f"{recs_inst}",
    )
    atlas_baker.revert([ia, ib])

    # --- the atlas is PACKED: a gutter at every resolution, copies in name order ---
    # Twin of mayatk's TestAtlasPlanFirst: the tiled layout this replaced inset each
    # cell's gutter only as far as a small cell could afford, so at a low resolution
    # neighbours met with none; and it ordered cells by area, so copies of one mesh
    # (equal but for float noise) traded places between bakes of one room.
    gutter_mat = btk.create_mat("standard", name="gutter_shared_mat")
    gutter_objs = []
    for i in range(30):
        size = 0.2 + (i % 7) ** 2 * 0.35
        bpy.ops.mesh.primitive_cube_add(size=size, location=(40 + i * 6, 0, 0))
        obj = bpy.context.active_object
        obj.name = f"GutterCube{i:02d}"
        btk.assign_mat(obj, gutter_mat)
        gutter_objs.append(obj)
    low = LightmapBaker(resolution=256)
    low_entries = next(iter(low.atlas_plan(gutter_objs).values()))
    cells = ptk.ImgUtils.atlas_pixel_rects([r for _n, r in low_entries], 256)

    def gap(p, q):
        return max(q[2] - p[3], p[2] - q[3], q[0] - p[1], p[0] - q[1])

    worst = min(gap(cells[i], cells[j]) for i in range(len(cells)) for j in range(i))
    check(
        "atlas: every cell keeps 2 x gutter from every other at 256 px",
        worst >= 2 * low._atlas_gutter()
        and min(min(r1 - r0, c1 - c0) for r0, r1, c0, c1 in cells) >= 2,
        f"worst gap {worst}, gutter {low._atlas_gutter()}",
    )
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, -30, 0))
    copy_a = bpy.context.active_object
    copy_a.name = "CopyA"
    copy_a.scale = (5.0, 0.17, 1.0)
    copies = [copy_a]
    for name, angle in (("CopyB", 90), ("CopyC", 180), ("CopyD", 270)):
        dup = copy_a.copy()  # linked: one mesh
        dup.name = name
        dup.rotation_euler = (0.0, 0.0, math.radians(angle))
        bpy.context.collection.objects.link(dup)
        copies.append(dup)
    bpy.context.view_layer.update()
    copy_mat = btk.create_mat("standard", name="copy_shared_mat")
    for obj in copies:
        btk.assign_mat(obj, copy_mat)
    copy_entries = dict(
        next(iter(LightmapBaker(resolution=512).atlas_plan(copies).values()))
    )
    copy_cells = {
        n: ptk.ImgUtils.atlas_pixel_rects([copy_entries[n]], 512)[0]
        for n in ("CopyA", "CopyB", "CopyC", "CopyD")
    }
    check(
        "atlas: copies of one mesh take equal cells, in name order",
        len({(r1 - r0, c1 - c0) for r0, r1, c0, c1 in copy_cells.values()}) == 1
        and sorted(copy_cells, key=lambda n: (copy_cells[n][0], copy_cells[n][2]))
        == ["CopyA", "CopyB", "CopyC", "CopyD"],
        f"{copy_cells}",
    )
    # A layout stretched over its square gets a cell of its SURFACE's shape (twin of
    # mayatk's test_a_stretched_layout_gets_a_cell_of_its_surfaces_shape): every wall
    # and baseboard of a production room is laid out filling its square whatever its
    # shape, and a square cell shipped that stretch -- texels 27x longer along a
    # baseboard than up it. The panel's lightmap layer copies its square-filling uvs.
    stretch_mat = btk.create_mat("standard", name="stretch_shared_mat")
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, -60, 0))
    panel = bpy.context.active_object
    panel.name = "StretchPanel"
    panel.scale = (4.0, 1.0, 1.0)
    panel.data.uv_layers.new(name="Lightmap")
    stretch_objs = [panel]
    for i in range(3):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(10 + i * 3, -60, 0))
        bpy.context.active_object.name = f"StretchCube{i}"
        stretch_objs.append(bpy.context.active_object)
    bpy.context.view_layer.update()
    for obj in stretch_objs:
        btk.assign_mat(obj, stretch_mat)
    stretch_entries = dict(
        next(iter(LightmapBaker(resolution=512).atlas_plan(stretch_objs).values()))
    )
    sr0, sr1, sc0, sc1 = ptk.ImgUtils.atlas_pixel_rects(
        [stretch_entries["StretchPanel"]], 512
    )[0]
    check(
        "atlas: a layout stretched over its square gets its surface's 4:1 cell",
        abs((sc1 - sc0) / (sr1 - sr0) - 4.0) < 0.3,
        f"cell {sc1 - sc0} x {sr1 - sr0}",
    )
    # The layouts are settled before the plan reads them: a first bake's generated
    # layout (Smart UV Project stretches its islands to fill the square) is what the
    # plan sizes and shapes. Planned first, the panel below took the unwrap-fill
    # square of a mesh with no lightmap yet and rendered its stretched layout into it.
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, -90, 0))
    fresh = bpy.context.active_object
    fresh.name = "FreshPanel"
    fresh.scale = (4.0, 1.0, 1.0)
    fresh_objs = [fresh]
    for i in range(2):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(10 + i * 3, -90, 0))
        bpy.context.active_object.name = f"FreshCube{i}"
        fresh_objs.append(bpy.context.active_object)
    bpy.context.view_layer.update()
    fresh_mat = btk.create_mat("standard", name="fresh_shared_mat")
    for obj in fresh_objs:
        btk.assign_mat(obj, fresh_mat)
    fresh_baker = LightmapBaker(resolution=256)
    packed_plan = {}

    def fake_render(_self, meshes, **_kw):
        return {m.name: os.path.join(tmp_dir, f"{m.name}_fresh.exr") for m in meshes}

    def fake_pack(_self, maps, plan=None, **_kw):
        packed_plan.update(plan or {})
        return {}

    with (
        _mock.patch.object(TextureBaker, "bake", fake_render),
        _mock.patch.object(LightmapBaker, "pack_atlas", fake_pack),
    ):
        fresh_baker.bake_atlas(fresh_objs, output_dir=tmp_dir)
    check(
        "atlas: the plan is drawn from the layouts the bake renders (built first)",
        bool(packed_plan) and packed_plan == fresh_baker.atlas_plan(fresh_objs),
        f"{packed_plan}",
    )

    # --- the Bake Set: plain, or spelled by the naming convention -------------
    from blendertk.mat_utils.bake_sets import LightmapBakeSet

    saved_cache = ptk.NamingConvention._cache
    ptk.NamingConvention._cache = {
        **ptk.NamingConvention.DEFAULTS,
        "objectSet": ptk.AffixRule("_SET", "auto", "Set"),
    }
    try:
        LightmapBakeSet.define([copy_a])
        plain = LightmapBakeSet.name()
        LightmapBakeSet.define([copy_a], conventional=True)
        spelled = LightmapBakeSet.name()
        respelled = LightmapBakeSet.respell(False)
        stamped = [c for c in bpy.data.collections if LightmapBakeSet.STAMP in c]
        check(
            "bake set: plain by default, by the convention when asked, one collection",
            plain == "lightmapBaker_baked"
            and spelled == "lightmapBaker_baked_SET"
            and respelled == "lightmapBaker_baked"
            and len(stamped) == 1
            and [o.name for o in LightmapBakeSet.members()] == ["CopyA"],
            f"{plain} / {spelled} / {respelled} / {[c.name for c in stamped]}",
        )
        LightmapBakeSet.clear()
        check(
            "bake set: clear removes it and leaves its objects",
            not LightmapBakeSet.exists() and "CopyA" in bpy.data.objects,
        )
    finally:
        ptk.NamingConvention._cache = saved_cache

    # --- the lightmap is named after the TEXTURE SET, not the material -------------
    # A lightmap is one more map of the set the object already wears, so it has to sort
    # beside them: MAT_ROOM_ENV_Lightmap.exr next to ROOM_ENV_Base_color.png reads as
    # a stray from a different set. The material above carries no image nodes, so nothing
    # here is exercised by the atlas checks -- a regression would pass them silently.
    named_mat = btk.create_mat("standard", name="MAT_ROOM_ENV")
    for fname in ("ROOM_ENV_Base_color.png", "ROOM_ENV_Normal.png", "stray_noise.png"):
        img = bpy.data.images.new(fname, 4, 4)
        img.name = fname
        tex_node = named_mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
    check(
        "naming: the texture set's base name wins over the material name",
        atlas_baker._material_texture_base("MAT_ROOM_ENV") == "ROOM_ENV",
        f"{atlas_baker._material_texture_base('MAT_ROOM_ENV')}",
    )
    check(
        "naming: one oddly-named map cannot rename the whole set",
        atlas_baker._atlas_base("MAT_ROOM_ENV", ["AtlasA"]) == "ROOM_ENV",
        f"{atlas_baker._atlas_base('MAT_ROOM_ENV', ['AtlasA'])}",
    )
    check(
        "naming: a material with no textures falls back to its own name",
        atlas_baker._atlas_base("atlas_shared_mat", ["AtlasA"]) == "atlas_shared_mat",
        f"{atlas_baker._atlas_base('atlas_shared_mat', ['AtlasA'])}",
    )
    check(
        "naming: no material at all falls back to the object name",
        atlas_baker._atlas_base("__no_material__", ["AtlasA"]) == "AtlasA",
        f"{atlas_baker._atlas_base('__no_material__', ['AtlasA'])}",
    )

    # A SOLO group is renamed too -- its per-object tile name is an intermediate, and the
    # rename is what makes ``_place``'s foreign-source guard load-bearing.
    named_cube_dir = os.path.join(tmp_dir, "named_out")
    bpy.ops.mesh.primitive_cube_add(location=(0, 9, 0))
    named_cube = bpy.context.active_object
    named_cube.name = "SomeObjectName"
    btk.assign_mat(named_cube, named_mat)
    btk.create_lightmap_uvs([named_cube])
    named = atlas_baker.bake_atlas(
        [named_cube], output_dir=named_cube_dir, suffix="_Lightmap"
    )
    named_files = (
        sorted(os.listdir(named_cube_dir)) if os.path.isdir(named_cube_dir) else []
    )
    check(
        "naming: a solo group's map is named for its texture set, not the object",
        named_files == ["ROOM_ENV_Lightmap.exr"],
        f"{named_files}",
    )
    atlas_baker.revert([named_cube])

    # --- the bake leaves the source material alone ------------------------
    # The fused/unlit level was removed; what replaces its coverage is the
    # guarantee that made it the wrong default -- a lightmap bake must never
    # touch the object's shading.
    src_mat = cube.material_slots[0].material if cube.material_slots else None
    mapping = baker.bake_separated([cube], output_dir=tmp_dir, suffix="_LM")
    check("bake produced a map", cube.name in mapping)
    baker.commit_lightmap(mapping)
    check(
        "commit_lightmap keeps the source material",
        cube.material_slots[0].material is src_mat,
    )
    check(
        "commit_lightmap stamps the lighting-only marker",
        LightmapBaker.LIGHTMAP_INFO_PROP in cube,
    )
    baker.revert([cube])
    check(
        "revert clears the marker and leaves the material",
        LightmapBaker.LIGHTMAP_INFO_PROP not in cube
        and cube.material_slots[0].material is src_mat,
    )

    # --- level verdict (engine): black AND blown ----------------------------
    # Either failure is a FAITHFUL render of a wrong scene, so nothing errors;
    # the bake's verdict is what tells the artist before the map ships to a
    # black (or white) web preview. The blown half exists because a Maya bridge
    # send crossed at 5.4e8 W per fixture and saturated every atlas while
    # reporting success. It moved from the panel to the engine, so a scripted
    # bake hears it too (mirror of mayatk).
    from blendertk.light_utils.lightmap_baker.lightmap_baker_slots import (
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

    guard = LightmapBaker()
    black, lit = _exr("guard_black.exr", 0.001), _exr("guard_lit.exr", 1.0)
    blown = _exr("guard_blown.exr", 40000.0)
    check(
        "the verdict fires for an unlit map",
        "BLACK" in (guard.bake_verdict([black]) or ""),
    )
    check("the verdict stays quiet for a lit map", guard.bake_verdict([lit]) is None)
    check(
        "one healthy map among dark ones clears the verdict",
        guard.bake_verdict([black, lit]) is None,
    )
    check(
        "a missing map never breaks a finished bake",
        guard.bake_verdict([os.path.join(tmp_dir, "nope.exr")]) is None,
    )
    check(
        "the verdict fires for a blown map",
        "BLOWN" in (guard.bake_verdict([blown]) or ""),
    )
    # The brightest map decides BOTH ways: one lit map disproves "unlit", and the
    # worst offender is what a blown bake has to show.
    check(
        "a blown map among lit ones still fires",
        "BLOWN" in (guard.bake_verdict([lit, blown]) or ""),
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

    BASE = os.path.normpath(
        "C:/proj/sourceimages" if os.name == "nt" else "/proj/sourceimages"
    )

    def _slots(text=""):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = _Ui(text)
        s._base_output_dir = lambda: BASE
        return s

    check(
        "empty output dir falls back to the texture folder",
        _slots()._output_dir() == BASE,
    )
    check(
        "a relative output dir resolves under the texture folder",
        _slots("lightmaps")._output_dir() == os.path.join(BASE, "lightmaps"),
    )
    check(
        "a nested relative output dir normalizes",
        _slots("bake/lm")._output_dir()
        == os.path.normpath(os.path.join(BASE, "bake/lm")),
    )
    abs_dir = os.path.normpath("D:/bakes/lm" if os.name == "nt" else "/bakes/lm")
    check(
        "an absolute output dir is used as-is", _slots(abs_dir)._output_dir() == abs_dir
    )
    check(
        "a quoted/padded entry is trimmed before joining",
        _slots('  " lightmaps "  ')._output_dir() == os.path.join(BASE, "lightmaps"),
    )
    # "/lightmaps" is a separator-spelled SUBDIRECTORY on Windows, but os.path.isabs
    # calls it absolute there and would resolve it to the current drive's root. On
    # POSIX it IS a full path (ptk.FileUtils.resolve_output_dir), and wins.
    check(
        "a driveless rooted entry stays a subdirectory (a full path on POSIX)",
        _slots("/lightmaps")._output_dir()
        == (os.path.join(BASE, "lightmaps") if os.name == "nt" else "/lightmaps"),
    )

    # A UNC share keeps its leading two backslashes: spelled "//nas/..." the
    # marker's folder is what bpy.path.abspath reads as relative to the .blend.
    if os.name == "nt":
        unc = LightmapRecords._portable_dir(r"\\nas\share\maps\room_Lightmap.exr")
        check(
            "a UNC map folder keeps its share prefix",
            unc.startswith("\\\\") and unc.endswith("maps"),
            unc,
        )

    # The browse dialog can only hand back an absolute path; a pick inside the
    # texture folder is rewritten to the portable relative form, anything
    # outside it is left exactly as the dialog wrote it.
    s = _slots()
    s._relativize_output_dir(os.path.join(BASE, "lightmaps", "hero"))
    check(
        "browsing inside the texture folder stores a relative path",
        s.ui.txt_output_dir.text() == "lightmaps/hero",
    )
    s._relativize_output_dir(BASE)
    check(
        "browsing the texture folder itself clears the field",
        s.ui.txt_output_dir.text() == "",
    )
    s.ui.txt_output_dir.setText(abs_dir)
    s._relativize_output_dir(abs_dir)
    check(
        "browsing outside the texture folder stays absolute",
        s.ui.txt_output_dir.text() == abs_dir,
    )

    # --- pre-bake unlit-scene guard (mayatk parity) ------------------------
    # mayatk warns BEFORE spending the rays; blendertk previously only had the
    # panel's post-bake black-map check, so a scripted bake got no hint at all.
    scene = bpy.context.scene
    world_backup = scene.world

    lit = LightmapBaker.from_preset("preview")
    lit._warn_if_unlit_scene()
    check(
        "a scene with a light does not trip the guard", lit._warned_no_lights is False
    )

    # Drop every light, drop the world (factory startup ships a grey emitting
    # one) -> genuinely unlit.
    for o in [o for o in scene.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(o, do_unlink=True)
    scene.world = None
    dark = LightmapBaker.from_preset("preview")
    dark._warn_if_unlit_scene()
    check(
        "a scene with no light and no world trips the guard",
        dark._warned_no_lights is True,
    )

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
    check(
        "a world-lit (HDRI) scene does not trip the guard",
        worldlit._warned_no_lights is False,
    )

    bg.inputs["Strength"].default_value = 0.0
    darkworld = LightmapBaker.from_preset("preview")
    darkworld._warn_if_unlit_scene()
    check(
        "a world at zero strength still trips the guard",
        darkworld._warned_no_lights is True,
    )
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

    # --- light audit diagnostic (mayatk parity) ----------------------------
    # mayatk attaches a per-light table to the black-bake warning so a dark
    # result carries its own diagnosis; blendertk's warning had no diagnostic.
    audit = LightmapBaker._light_audit()
    check(
        "audit reports the world when the scene has no lights",
        "<no lights in the scene>" in audit and "<world>" in audit,
        audit,
    )

    bpy.ops.object.light_add(type="AREA", location=(0, 0, 5))
    area = bpy.context.active_object
    area.data.energy = 250.0
    area.data.size = 2.0
    area.hide_render = True
    audit = LightmapBaker._light_audit()
    check(
        "audit lists the light with the dials a black bake traces to",
        area.name in audit
        and "power=250W" in audit
        and "type=AREA" in audit
        and "size=2" in audit
        and "render_visible=False" in audit,
        audit,
    )
    check(
        "audit still reports world state alongside the lights",
        "<world>: emits=" in audit,
        audit,
    )

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
    dep_baker = LightmapRecords
    dep_baker.commit({dep_cube.name: dep_map})

    def _same_dir(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
            os.path.abspath(b)
        )

    deps = dep_baker.lightmap_dependencies()
    check(
        "lightmap_dependencies lists the committed map",
        len(deps) == 1 and deps[0]["map"] == "dep_cube_LightMap.exr",
        f"{deps}",
    )
    check(
        "...resolved by the marker's own folder",
        bool(deps)
        and deps[0]["found_by"] == LightmapBaker.FOUND_BY_HINT
        and _same_dir(deps[0]["path"], dep_map),
        f"{deps}",
    )
    check("...naming the object", bool(deps) and deps[0]["objects"] == ["dep_cube"])
    check(
        "search_dirs leads with the folder the marker's map resolves to",
        _same_dir((LightmapRecords.search_dirs() or [""])[0], dep_dir),
        f"{LightmapRecords.search_dirs()}",
    )
    with warnings.catch_warnings(record=True) as caught_alias:
        warnings.simplefilter("always")
        alias_deps = LightmapBaker().lightmap_dependencies()
    check(
        "the baker's old lightmap_dependencies spelling warns and still answers",
        [d["map"] for d in alias_deps] == ["dep_cube_LightMap.exr"]
        and any(issubclass(w.category, DeprecationWarning) for w in caught_alias),
    )

    moved_dir = os.path.join(tmp_dir, "moved")
    os.makedirs(moved_dir, exist_ok=True)
    shutil.move(dep_map, os.path.join(moved_dir, "dep_cube_LightMap.exr"))
    deps = dep_baker.lightmap_dependencies(search_dirs=[moved_dir], walk=False)
    check(
        "a moved map is found through the search folders",
        bool(deps) and deps[0]["found_by"] == LightmapBaker.FOUND_BY_SEARCH,
        f"{deps}",
    )
    deps = dep_baker.lightmap_dependencies(search_dirs=[], walk=False)
    check(
        "a map found nowhere is reported missing",
        bool(deps) and deps[0]["path"] is None and deps[0]["found_by"] is None,
        f"{deps}",
    )

    dest = os.path.join(tmp_dir, "dest")
    plan = dep_baker.relocate_lightmaps(dest, source_dir=moved_dir, dry_run=True)
    check(
        "relocate dry run plans without touching anything",
        len(plan["relocate"]) == 1
        and plan["copied"] == []
        and not os.path.exists(os.path.join(dest, "dep_cube_LightMap.exr")),
        f"{plan}",
    )
    result = dep_baker.relocate_lightmaps(dest, source_dir=moved_dir)
    marker = json.loads(dep_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "relocate copies the map into the destination",
        os.path.isfile(os.path.join(dest, "dep_cube_LightMap.exr"))
        and len(result["copied"]) == 1,
        f"{result}",
    )
    folder = LightmapRecords._folder_hint(marker, LightmapRecords._folder_hints())
    check(
        "...and repoints the map's recorded folder",
        result["updated"] == 1
        and _same_dir(LightmapRecords._resolved_dir(folder, marker["map"]), dest),
        f"{folder}",
    )
    manifest = json.loads(ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes) or "{}")
    check(
        "...and the republished manifest names no folder (the GLB embeds the maps)",
        [r.get("name") for r in manifest.get("objects") or []] == ["dep_cube"]
        and "dir" not in manifest
        and "dirs" not in manifest,
        f"{manifest}",
    )
    check(
        "...and search_dirs leads with the marker's new folder",
        _same_dir((LightmapRecords.search_dirs() or [""])[0], dest),
        f"{LightmapRecords.search_dirs()}",
    )
    check(
        "the relocated map now resolves by hint",
        dep_baker.lightmap_dependencies(search_dirs=[], walk=False)[0]["found_by"]
        == LightmapBaker.FOUND_BY_HINT,
    )

    bpy.ops.mesh.primitive_cube_add()
    lost_cube = bpy.context.active_object
    lost_cube.name = "lost_cube"
    dep_baker.commit({lost_cube.name: os.path.join(tmp_dir, "gone", "lost.exr")})
    result = dep_baker.relocate_lightmaps(
        dest, source_dir=moved_dir, objects=["lost_cube"]
    )
    check(
        "relocate names what it could not find",
        [d["map"] for d in result["missing"]] == ["lost.exr"]
        and result["updated"] == 0,
        f"{result}",
    )
    check(
        "scope limits to the given objects",
        [d["map"] for d in dep_baker.lightmap_dependencies(objects=["dep_cube"])]
        == ["dep_cube_LightMap.exr"],
    )
    n = dep_baker.repath_lightmaps({"lost.exr": moved_dir}, ["lost_cube"])
    lost_marker = json.loads(lost_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    lost_folder = LightmapRecords._folder_hint(
        lost_marker, LightmapRecords._folder_hints()
    )
    check(
        "repath_lightmaps rewrites a map's recorded folder",
        n == 1
        and _same_dir(
            LightmapRecords._resolved_dir(lost_folder, "lost.exr"), moved_dir
        ),
        f"{lost_folder}",
    )
    dep_baker.revert()

    # --- search_dirs order: the markers' folders first, most-named first ------
    # The deliverable names no folder (the GLB embeds the maps), so search_dirs
    # is the one answer to where they are, and the GLB applier takes the FIRST
    # folder holding a basename -- the order is a priority. A texture folder
    # holding a same-named map from an earlier bake must not outrank the folder
    # the markers name; of the marker folders, the one more objects were baked
    # into leads, and a tie is broken on the path so the order is stable.
    from unittest import mock

    from blendertk.env_utils._env_utils import EnvUtils

    order_root = os.path.join(tmp_dir, "order")
    fresh_dir = os.path.join(order_root, "z_fresh")  # 3 objects: leads despite "z"
    old_dir = os.path.join(order_root, "a_old")  # 1 object
    mid_dir = os.path.join(order_root, "m_mid")  # 1 object: ties a_old, sorts after
    tex_dir = os.path.join(order_root, "textures")  # a same-named stale copy
    for folder, name in (
        (fresh_dir, "shared_LightMap.exr"),
        (tex_dir, "shared_LightMap.exr"),
        (old_dir, "old_LightMap.exr"),
        (mid_dir, "mid_LightMap.exr"),
    ):
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, name), "wb").close()
    order_objs = []
    for name in ("ord_a", "ord_b", "ord_c", "ord_old", "ord_mid"):
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = name
        order_objs.append(bpy.context.active_object)
    order_baker = LightmapBaker()
    order_baker.commit_lightmap(
        {
            **{
                n: os.path.join(fresh_dir, "shared_LightMap.exr")
                for n in ("ord_a", "ord_b", "ord_c")
            },
            "ord_old": os.path.join(old_dir, "old_LightMap.exr"),
            "ord_mid": os.path.join(mid_dir, "mid_LightMap.exr"),
        }
    )
    with mock.patch.object(EnvUtils, "texture_search_dirs", return_value=[tex_dir]):
        ordered = LightmapRecords.search_dirs()
    check(
        "search_dirs: most-named marker folder first, ties by path, texture dirs last",
        len(ordered) == 4
        and all(
            _same_dir(got, want)
            for got, want in zip(ordered, (fresh_dir, old_dir, mid_dir, tex_dir))
        ),
        f"{ordered}",
    )
    order_baker.revert()
    for obj in order_objs:
        bpy.data.objects.remove(obj, do_unlink=True)

    # --- bake(): the whole workflow, the same for a script as for the panel ---
    # Mirror of mayatk's LightmapBaker.bake: preflight, the bake, the record and
    # the verdict, returned as a LightmapBakeResult. Nothing is reverted first.
    from blendertk.light_utils.lightmap_baker.lightmap_baker import (
        LightmapBakeResult,
    )

    LightmapBaker().revert()
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 6))
    wf_sun = bpy.context.active_object
    wf_sun.data.energy = 3.0
    bpy.ops.mesh.primitive_cube_add(location=(40, 0, 0))
    wf_cube = bpy.context.active_object
    wf_cube.name = "wf_cube"
    btk.assign_mat(wf_cube, mat)
    wf_baker = LightmapBaker.from_preset(
        "preview", resolution=64, samples=4, denoise=False, device="CPU"
    )
    wf_dir = os.path.join(tmp_dir, "wf")
    wf = wf_baker.bake([wf_cube], packing="per_object", output_dir=wf_dir)
    check("bake(): returns a LightmapBakeResult", isinstance(wf, LightmapBakeResult))
    wf_map = wf.maps.get(wf_cube.name, "")
    check(
        "bake(): the map is written and recorded",
        bool(wf)
        and os.path.isfile(wf_map)
        and LightmapRecords._marker_info(wf_cube).get("map")
        == os.path.basename(wf_map),
        f"{wf}",
    )
    check(
        "bake(): nothing refused, nothing left unbaked",
        wf.refused is None and wf.unbaked == [],
        f"{wf}",
    )

    # --- the white card: a target bakes its GEOMETRY's light, as Arnold's does ---
    # Baked through its own material (pass colour divided out), a metal had no
    # diffuse lobe and came back near black, and a normal-mapped target took the
    # map's detail. Two planes in the same sun light, one fully metallic: the same
    # level, the card gone afterwards and each object's own slots back as they were.
    def _plane(name, x, metallic):
        bpy.ops.mesh.primitive_plane_add(size=2, location=(x, 0, 0))
        obj = bpy.context.active_object
        obj.name = name
        pm = bpy.data.materials.new(f"{name}_mat")
        pm.use_nodes = True
        pm.node_tree.nodes["Principled BSDF"].inputs[
            "Metallic"
        ].default_value = metallic
        obj.data.materials.append(pm)
        return obj, pm

    card_metal, metal_mat = _plane("card_metal", 200, 1.0)
    card_plain, _plain_mat = _plane("card_plain", 210, 0.0)
    card_maps = LightmapBaker.from_preset(
        "preview", resolution=32, samples=16, denoise=False, device="CPU"
    ).bake_separated([card_metal, card_plain], output_dir=os.path.join(tmp_dir, "card"))

    def _mean(path):
        cimg = bpy.data.images.load(path)
        cbuf = np.empty(len(cimg.pixels), np.float32)
        cimg.pixels.foreach_get(cbuf)
        bpy.data.images.remove(cimg)
        return float(cbuf.reshape(-1, 4)[:, :3].mean())

    metal_level = _mean(card_maps[card_metal.name])
    plain_level = _mean(card_maps[card_plain.name])
    check(
        "white card: a metal bakes the light it receives, not black",
        plain_level > 0 and abs(metal_level / plain_level - 1.0) < 0.05,
        f"metal={metal_level:.4f} plain={plain_level:.4f}",
    )
    check(
        "white card: gone after the bake, the target's own slot back",
        "lm_whitecard" not in bpy.data.materials
        and card_metal.material_slots[0].link == "DATA"
        and card_metal.material_slots[0].material is metal_mat,
        f"{[m.name for m in bpy.data.materials if 'whitecard' in m.name]} "
        f"{card_metal.material_slots[0].link} {card_metal.material_slots[0].material}",
    )

    # Every light hidden from the render, and no world the bake keeps: refused
    # before a ray is spent (mayatk's rule; its skydome is the world here).
    wf_sun.hide_render = True
    dark = LightmapBaker.from_preset(
        "preview",
        resolution=64,
        samples=4,
        denoise=False,
        device="CPU",
        include_environment=False,
    ).bake([wf_cube], packing="per_object", output_dir=wf_dir)
    check(
        "bake(): every light hidden from the render is refused, not baked dark",
        bool(dark.refused) and "hidden" in dark.refused and not dark.maps,
        f"{dark}",
    )
    check(
        "preflight: an emitting world the bake keeps still lights it",
        wf_baker.preflight() is None,
    )
    wf_sun.hide_render = False

    # A light is hidden by its COLLECTION too -- the usual Blender idiom -- and
    # the lit test read the object's own flag alone.
    wf_lights = bpy.data.collections.new("wf_lights")
    bpy.context.scene.collection.children.link(wf_lights)
    for home in list(wf_sun.users_collection):
        home.objects.unlink(wf_sun)
    wf_lights.objects.link(wf_sun)
    no_env = LightmapBaker.from_preset(
        "preview",
        resolution=64,
        samples=4,
        denoise=False,
        device="CPU",
        include_environment=False,
    )
    wf_lights.hide_render = True
    check(
        "preflight: a light in a render-disabled collection is no light",
        bool(no_env.preflight()),
    )
    wf_lights.hide_render = False
    layer_lights = bpy.context.view_layer.layer_collection.children["wf_lights"]
    layer_lights.exclude = True
    check(
        "preflight: a light in an excluded collection is no light",
        bool(no_env.preflight()),
    )
    layer_lights.exclude = False
    check(
        "preflight: the same light in an enabled collection lights the bake",
        no_env.preflight() is None,
    )
    LightmapRecords.revert([wf_cube])  # the crates' claims below are the only ones

    # --- a partial re-bake never writes over another object's map ----------
    # Maps are named after their texture set, so two objects sharing one both
    # want Crate_Lightmap.exr. Names were unique only within ONE bake, so
    # re-baking one of them landed on the file the other still read, which then
    # shipped this bake's lighting. The file claims (LightmapRecords.claims)
    # decide now: a name another object reads is never taken; an object's own
    # name is kept -- in both packings.
    crate_mat = btk.create_mat("standard", name="crate_mat")
    crate_img = bpy.data.images.new("Crate_BaseColor.png", 4, 4)
    crate_node = crate_mat.node_tree.nodes.new("ShaderNodeTexImage")
    crate_node.image = crate_img
    crates = []
    for i, name in enumerate(("crate_a", "crate_b")):
        bpy.ops.mesh.primitive_cube_add(location=(44 + 4 * i, 0, 0))
        crates.append(bpy.context.active_object)
        crates[-1].name = name
        btk.assign_mat(crates[-1], crate_mat)
    crate_a, crate_b = crates
    s1_dir = os.path.join(tmp_dir, "s1")
    os.makedirs(s1_dir, exist_ok=True)
    theirs = os.path.join(s1_dir, "Crate_Lightmap.exr")
    with open(theirs, "wb") as fh:
        fh.write(b"theirs")
    mine = os.path.join(s1_dir, "Crate_Lightmap_1.exr")
    with open(mine, "wb") as fh:
        fh.write(b"mine")
    LightmapRecords.commit({crate_a.name: theirs, crate_b.name: mine})
    check(
        "claims name the reader of every map",
        LightmapRecords.claims()
        == {
            "crate_lightmap.exr": frozenset({"crate_a"}),
            "crate_lightmap_1.exr": frozenset({"crate_b"}),
        },
        f"{LightmapRecords.claims()}",
    )
    for packing in ("per_object", "atlas"):
        rebaked = wf_baker.bake([crate_b], packing=packing, output_dir=s1_dir)
        check(
            f"a partial re-bake keeps its own map's name ({packing})",
            os.path.basename(rebaked.maps.get(crate_b.name, ""))
            == "Crate_Lightmap_1.exr",
            f"{rebaked.maps}",
        )
        with open(theirs, "rb") as fh:
            check(
                f"...and never writes over the map another object reads ({packing})",
                fh.read() == b"theirs",
            )
    LightmapBaker().revert()
    for obj in (wf_cube, wf_sun, crate_a, crate_b):
        bpy.data.objects.remove(obj, do_unlink=True)

    # --- a HIDDEN mesh is baked, not refused ------------------------------
    # Production blocker: one hidden mesh among 48 in the ROOM_ENV room aborted
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
        f"{[uv.name for uv in hid.data.uv_layers]}",
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

    # --- parity with mayatk's 2026-09-22 panel: Exclude, presets, switches ---
    # mayatk's baker grew an Exclude set, Beside Material Textures, Adaptive
    # Sampling, Bounces on the panel and a preset template that stores the
    # switches as well as the dials; these pin the Blender twin of each, engine
    # half first, then the panel's.
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace

    from blendertk.core_utils._core_utils import CoreUtils
    from blendertk.mat_utils.bake_sets import (
        BakeSet,
        BakeSourceSet,
        LightmapExcludeSet,
    )
    from blendertk.light_utils.lightmap_baker import lightmap_baker_slots as slots_mod

    LightmapBaker().revert()
    par_dir = os.path.join(tmp_dir, "parity")

    def _par_cube(name, location, size=2.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    # The Exclude set: members, an Empty's descendants, and bake_targets.
    ex_a = _par_cube("ex_a", (60, 0, 0))
    ex_b = _par_cube("ex_b", (64, 0, 0))
    ex_group = bpy.data.objects.new("ex_group", None)
    bpy.context.scene.collection.objects.link(ex_group)
    ex_c = _par_cube("ex_c", (68, 0, 0))
    ex_c.parent = ex_group
    LightmapExcludeSet.define([ex_b, ex_group])
    check(
        "the Exclude set counts the meshes under an Empty member",
        sorted(o.name for o in LightmapExcludeSet.meshes()) == ["ex_b", "ex_c"],
        f"{[o.name for o in LightmapExcludeSet.meshes()]}",
    )
    check(
        "bake_targets subtracts the Exclude set",
        LightmapBaker.bake_targets([ex_a, ex_b, ex_c]) == ["ex_a"],
        f"{LightmapBaker.bake_targets([ex_a, ex_b, ex_c])}",
    )
    ex_col = LightmapExcludeSet.collection()
    check(
        "the set is a stamped collection, disabled in viewports and renders",
        ex_col is not None
        and LightmapExcludeSet.STAMP in ex_col
        and ex_col.hide_viewport
        and ex_col.hide_render,
    )
    check(
        "members stay in their own collections too (the set is a tag)",
        len(ex_b.users_collection) == 2,
        f"{[c.name for c in ex_b.users_collection]}",
    )
    bpy.ops.object.select_all(action="DESELECT")
    ex_a.select_set(True)
    check(
        "define([]) clears the set rather than capturing the selection",
        LightmapExcludeSet.define([]) == [] and not LightmapExcludeSet.exists(),
    )
    check(
        "...and leaves its objects in the file",
        all(o.name in bpy.data.objects and o.users_collection for o in (ex_b, ex_c)),
    )
    # Found by its stamp, never by name: a user collection that happens to
    # carry the name is not adopted, and defining the set leaves it alone.
    impostor = bpy.data.collections.new(LightmapExcludeSet.SET_NAME)
    impostor.objects.link(ex_c)
    check(
        "a same-named user collection is not the set",
        LightmapExcludeSet.collection() is None and LightmapExcludeSet.meshes() == [],
    )
    LightmapExcludeSet.define([ex_a])
    check(
        "...and defining the set leaves it alone",
        LightmapExcludeSet.collection() is not impostor
        and [o.name for o in impostor.objects] == ["ex_c"],
    )
    LightmapExcludeSet.clear()
    impostor.objects.unlink(ex_c)
    bpy.data.collections.remove(impostor)
    # ...nor is a stamped set a LIBRARY links in: that set is the library file's,
    # read-only here (a Maya reference's set is namespaced away the same way).
    # Adopted, it kept the library's objects out of this file's bake, Set From
    # Selection raised on it ("the collection is linked"), and Clear deleted the
    # linked collection from the file.
    lib_obj = bpy.data.objects.new("ex_lib_obj", bpy.data.meshes.new("ex_lib_mesh"))
    lib_set = bpy.data.collections.new("ex_lib_set")
    lib_set[LightmapExcludeSet.STAMP] = True
    lib_set.objects.link(lib_obj)
    lib_path = os.path.join(tmp_dir, "exclude_library.blend")
    bpy.data.libraries.write(lib_path, {lib_set})
    lib_mesh = lib_obj.data
    bpy.data.collections.remove(lib_set)
    bpy.data.objects.remove(lib_obj)
    bpy.data.meshes.remove(lib_mesh)
    with bpy.data.libraries.load(lib_path, link=True) as (_lib_src, lib_dst):
        lib_dst.collections = ["ex_lib_set"]
    linked_set = lib_dst.collections[0]
    linked_library = linked_set.library
    check(
        "a stamped set a library links in is not this file's set",
        linked_library is not None
        and LightmapExcludeSet.collection() is None
        and LightmapExcludeSet.meshes() == [],
        f"{[o.name for o in LightmapExcludeSet.meshes()]}",
    )
    try:
        LightmapExcludeSet.define([ex_a])
        own = LightmapExcludeSet.collection()
        defined = (
            own is not None
            and own.library is None
            and [o.name for o in own.objects] == ["ex_a"]
        )
        define_error = ""
    except RuntimeError as error:
        defined, define_error = False, str(error)
    check(
        "...Set From Selection beside it defines this file's own", defined, define_error
    )
    LightmapExcludeSet.clear()
    check(
        "...and Clear leaves the linked collection in the file",
        any(
            c.library is not None and c.name == "ex_lib_set"
            for c in bpy.data.collections
        ),
    )
    bpy.data.libraries.remove(linked_library)
    check(
        "with no set, bake_targets bakes everything",
        LightmapBaker.bake_targets([ex_a, ex_b, ex_c]) == ["ex_a", "ex_b", "ex_c"],
    )
    check(
        "resolve_meshes names each mesh once",
        [o.name for o in TextureBaker.resolve_meshes([ex_a, "ex_a", ex_b, ex_a])]
        == ["ex_a", "ex_b"],
    )
    # A faceless mesh has no surface to bake: it passed, and its lightmap UV
    # unwrap then failed the WHOLE bake (measured 2026-09-23, Blender 5.1) --
    # mayatk's twin crashed mayapy in Arnold on one. Modifiers that BUILD faces
    # on an empty base (geometry nodes) still count: the bake reads them.
    faceless = bpy.data.objects.new("ex_faceless", bpy.data.meshes.new("ex_faceless"))
    bpy.context.scene.collection.objects.link(faceless)
    built = bpy.data.objects.new("ex_built", bpy.data.meshes.new("ex_built"))
    bpy.context.scene.collection.objects.link(built)
    gn_tree = bpy.data.node_groups.new("ex_built_cube", "GeometryNodeTree")
    gn_tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    gn_tree.links.new(
        gn_tree.nodes.new("GeometryNodeMeshCube").outputs["Mesh"],
        gn_tree.nodes.new("NodeGroupOutput").inputs[0],
    )
    built.modifiers.new("build", "NODES").node_group = gn_tree
    check(
        "resolve_meshes leaves out a mesh with no faces, keeps one its modifiers build",
        [o.name for o in TextureBaker.resolve_meshes([faceless, ex_a, built])]
        == ["ex_a", "ex_built"],
    )
    for ob in (faceless, built):
        mesh = ob.data
        bpy.data.objects.remove(ob)
        bpy.data.meshes.remove(mesh)
    bpy.data.node_groups.remove(gn_tree)
    check(
        "BakeSourceSet stores its set the same way (BakeSet)",
        issubclass(BakeSourceSet, BakeSet)
        and BakeSourceSet.STAMP != LightmapExcludeSet.STAMP,
    )

    # Measured, not assumed: an excluded object gets no map but stays in the
    # render -- it still shadows the floor -- while membership itself changes
    # nothing about what renders. An object is in the render when ANY
    # collection it is in renders, so a plain set collection brought a box its
    # artist had switched off back into the bake (0.955 -> 0.000 under it).
    bpy.ops.object.light_add(type="SUN", location=(80, 0, 8))
    par_sun = bpy.context.active_object
    par_sun.data.energy = 3.0
    bpy.ops.mesh.primitive_plane_add(size=4, location=(80, 0, 0))
    floor = bpy.context.active_object
    floor.name = "ex_floor"
    btk.assign_mat(floor, mat)
    box = _par_cube("ex_box", (80, 0, 1.2), size=1.2)
    btk.assign_mat(box, mat)
    box_home = bpy.data.collections.new("ex_box_home")
    bpy.context.scene.collection.children.link(box_home)
    for c in list(box.users_collection):
        c.objects.unlink(box)
    box_home.objects.link(box)
    ex_baker = LightmapBaker(
        resolution=32, samples=16, denoise=False, device="CPU", bounces=1
    )

    def _floor_levels():
        """(centre, corner) mean of the floor's fresh map: under the box, and not."""
        rgb = _rgb(
            # heal=False: the dead-texel rescue refills a deep shadow from its
            # lit neighbours -- right for a shipped map, wrong for a measurement.
            ex_baker.bake_separated(
                [floor], output_dir=par_dir, suffix="_Probe", heal=False
            )[floor.name]
        )
        h, w = rgb.shape[:2]
        centre = float(rgb[h // 2 - 3 : h // 2 + 3, w // 2 - 3 : w // 2 + 3].mean())
        corner = float(rgb[1:5, 1:5].mean())
        return centre, corner

    box_home.hide_render = True
    open_centre, open_corner = _floor_levels()
    box_home.hide_render = False
    # Neither baked nor excluded, the box moves (an interactive prop): out of
    # the render for the bake, so it shadows nothing -- production, 2026-10-07,
    # props on a cart shadowed the cart's map (mirror of mayatk's).
    moved_centre, _moved_corner = _floor_levels()
    check(
        "what the bake leaves out casts no shadow, and is handed back",
        open_centre > 0.05
        and abs(moved_centre / open_centre - 1.0) < 0.2
        and not box.hide_render,
        f"open {open_centre:.4f} with the box {moved_centre:.4f}",
    )

    LightmapExcludeSet.define([box])
    wf_ex = ex_baker.bake([floor, box], packing="per_object", output_dir=par_dir)
    check(
        "bake() gives an Exclude-set member no map, and names it",
        list(wf_ex.maps) == [floor.name] and wf_ex.excluded == [box.name],
        f"{wf_ex}",
    )
    ex_centre, _ex_corner = _floor_levels()
    check(
        "an excluded object still shadows the objects that bake",
        ex_centre < 0.5 * open_centre,
        f"excluded {ex_centre:.4f} vs open {open_centre:.4f}",
    )
    box_home.hide_render = True  # the artist switches the box off for renders
    neutral_centre, _n_corner = _floor_levels()
    check(
        "set membership never puts a render-hidden object back in the bake",
        neutral_centre > 0.8 * open_centre,
        f"{neutral_centre:.4f} vs open {open_centre:.4f}",
    )
    box_home.hide_render = False
    only_box = ex_baker.bake([box], packing="per_object", output_dir=par_dir)
    check(
        "bake() of the Exclude set alone is refused, and says so",
        bool(only_box.refused)
        and "Exclude set" in only_box.refused
        and not only_box.maps,
        f"{only_box}",
    )
    plan = LightmapBaker(resolution=64).atlas_plan([floor, box])
    check(
        "atlas_plan gives an excluded object no cell",
        [n for entries in plan.values() for n, _r in entries] == [floor.name],
        f"{plan}",
    )
    check(
        "bake_separated gives an excluded object no map",
        list(ex_baker.bake_separated([floor, box], output_dir=par_dir)) == [floor.name],
    )
    LightmapRecords.revert([floor])
    LightmapExcludeSet.clear()

    # A selected Empty is "bake what is in it": production (2026-10-07, Maya)
    # selected a bake set's members -- a mesh and two groups -- and the bake took
    # the mesh alone. Mirror of mayatk's.
    grp = bpy.data.objects.new("grp_room", None)
    bpy.context.scene.collection.objects.link(grp)
    grp_a = _par_cube("grp_a", (120, 0, 0), size=1.0)
    grp_b = _par_cube("grp_b", (122, 0, 0), size=1.0)
    for child in (grp_a, grp_b):
        child.parent = grp
    check(
        "a selected Empty bakes the meshes under it; resolve alone keeps none",
        sorted(LightmapBaker.bake_targets([grp])) == ["grp_a", "grp_b"]
        and TextureBaker.resolve_meshes([grp]) == [],
        f"{LightmapBaker.bake_targets([grp])}",
    )
    grp_b.parent = grp_a  # a mesh under a mesh: the mesh names itself alone
    check(
        "a selected mesh with a mesh child bakes itself alone",
        LightmapBaker.bake_targets([grp_a]) == ["grp_a"],
        f"{LightmapBaker.bake_targets([grp_a])}",
    )
    # Any object that is not a mesh stands for what is under it -- a rig's
    # curve control parenting meshes too, as mayatk's locator (2026-10-09:
    # a table under a ``*_LOC`` re-baked none of its meshes).
    ctrl = bpy.data.objects.new("grp_ctrl", bpy.data.curves.new("grp_ctrl", "CURVE"))
    bpy.context.scene.collection.objects.link(ctrl)
    grp_b.parent = ctrl
    check(
        "a selected curve control bakes the meshes under it",
        LightmapBaker.bake_targets([ctrl]) == ["grp_b"],
        f"{LightmapBaker.bake_targets([ctrl])}",
    )
    # The production workflow (mirror of mayatk's): an include set -- here a
    # collection holding the group and the control -- and the Exclude set,
    # which overrules it. The set stands for its members, ref or name.
    include = bpy.data.collections.new("grp_include")
    bpy.context.scene.collection.children.link(include)
    for obj in (grp, ctrl):
        include.objects.link(obj)
    LightmapExcludeSet.define([grp_b])
    check(
        "a bake set bakes its members, the Exclude set overruling it",
        LightmapBaker.bake_targets([include]) == ["grp_a"]
        and LightmapBaker.bake_targets(["grp_include"]) == ["grp_a"],
        f"{LightmapBaker.bake_targets([include])}",
    )
    LightmapExcludeSet.clear()
    bpy.data.collections.remove(include)
    for obj in (grp_a, grp_b, grp, ctrl):
        bpy.data.objects.remove(obj)

    # Beside Material Textures: each map in its texture set's folder, the rest
    # in output_dir, and a folder that resolves nowhere here never created.
    def _textured(name, location, folder, set_name, material=None):
        obj = _par_cube(name, location)
        if material is None:
            material = btk.create_mat("standard", name=f"{name}_mat")
            node = material.node_tree.nodes.new("ShaderNodeTexImage")
            image = bpy.data.images.new(f"{set_name}_BaseColor.png", 4, 4)
            image.filepath = os.path.join(folder, f"{set_name}_BaseColor.png")
            node.image = image
        btk.assign_mat(obj, material)
        return obj, material

    tex_root = os.path.join(par_dir, "tex")
    crate_dir = os.path.join(tex_root, "crate")
    shelf_dir = os.path.join(tex_root, "shelf")
    gone_dir = os.path.join(tex_root, "moved", "library")
    for folder in (crate_dir, shelf_dir):
        os.makedirs(folder, exist_ok=True)
    bs_crate, _ = _textured("bs_crate", (72, 8, 0), crate_dir, "Crate_Wood_01")
    bs_gone, _ = _textured("bs_gone", (76, 8, 0), gone_dir, "Gone_Set_01")
    bs_plain = _par_cube("bs_plain", (80, 8, 0))
    found = TextureBaker.texture_set(bs_crate)
    check(
        "the texture set names the map and picks its folder",
        found is not None
        and found[0] == "Crate_Wood_01"
        and os.path.normcase(found[1]) == os.path.normcase(os.path.normpath(crate_dir)),
        f"{found}",
    )
    check(
        "texture_set_stem reads the same answer",
        TextureBaker.texture_set_stem(bs_crate) == "Crate_Wood_01",
    )
    bs_out = os.path.join(par_dir, "out")
    beside = LightmapBaker(
        resolution=32,
        samples=4,
        denoise=False,
        device="CPU",
        beside_textures=True,
    )
    # The bake's project: maps land beside textures only inside it (mirror of
    # mayatk's TestBesideTextures, which opens a workspace on its root).
    btk.EnvUtils.set_current_workspace(par_dir)
    sep = beside.bake_separated([bs_crate, bs_gone, bs_plain], output_dir=bs_out)

    def _folder_of(path):
        return os.path.normcase(os.path.dirname(os.path.abspath(path)))

    check(
        "beside textures: the map lands in its texture set's folder",
        _folder_of(sep.get("bs_crate", "")) == os.path.normcase(crate_dir),
        f"{sep}",
    )
    check(
        "...an object with none takes output_dir",
        _folder_of(sep.get("bs_plain", "")) == os.path.normcase(bs_out),
        f"{sep}",
    )
    check(
        "...and a texture folder missing here falls back, never created",
        _folder_of(sep.get("bs_gone", "")) == os.path.normcase(bs_out)
        and not os.path.exists(gone_dir),
        f"{sep}",
    )
    check(
        "...placed, not baked there: the texture folder holds the map alone",
        os.listdir(crate_dir) == [os.path.basename(sep.get("bs_crate", "?"))],
        f"{os.listdir(crate_dir)}",
    )
    # Another project's (or Downloads') texture folder is not this bake's to
    # write: a library two projects share took both projects' lightmaps under
    # one name, each bake replacing the other's (mayatk, 2026-10-03).
    library = os.path.join(tmp_dir, "lm_library")
    os.makedirs(library, exist_ok=True)
    bs_shared, _ = _textured("bs_shared", (92, 8, 0), library, "Office_Wall_01")
    out_lib = beside.bake_separated([bs_shared], output_dir=bs_out)
    check(
        "beside textures: a texture folder outside the project is never written to",
        _folder_of(out_lib.get("bs_shared", "")) == os.path.normcase(bs_out)
        and os.listdir(library) == [],
        f"{out_lib} {os.listdir(library)}",
    )
    shelf_a, shelf_mat = _textured(
        "bs_shelf_a", (84, 8, 0), shelf_dir, "Shelf_Metal_01"
    )
    shelf_b, _ = _textured(
        "bs_shelf_b", (88, 8, 0), shelf_dir, None, material=shelf_mat
    )
    atlas_out = os.path.join(par_dir, "atlas_out")
    packed = beside.bake_atlas([shelf_a, shelf_b], output_dir=atlas_out)
    atlas_paths = {p for p, _r in packed.values()}
    check(
        "beside textures: an atlas lands beside its material group's textures",
        len(atlas_paths) == 1
        and _folder_of(next(iter(atlas_paths))) == os.path.normcase(shelf_dir)
        and os.path.basename(next(iter(atlas_paths))) == "Shelf_Metal_01_Lightmap.exr"
        and os.path.isfile(next(iter(atlas_paths))),
        f"{packed}",
    )
    check("...and nothing fell back to output_dir", not os.path.exists(atlas_out))
    btk.EnvUtils.set_current_workspace(None)
    # A file in a shared texture folder that no marker in this file claims is
    # another file's map: replacing it would hand that file this one's lighting.
    # Once a marker claims it for the very objects being placed, it is theirs.
    work = os.path.join(par_dir, "work")
    os.makedirs(work, exist_ok=True)
    theirs = os.path.join(crate_dir, "Shared_Set_Lightmap.exr")
    with open(theirs, "wb") as fh:
        fh.write(b"theirs")
    staged = os.path.join(work, "Shared_Set_Lightmap.exr")
    with open(staged, "wb") as fh:
        fh.write(b"new")
    placed = beside._place_unpacked(
        {"bs_crate": (staged, None)}, bs_out, {"bs_crate": crate_dir}
    )
    with open(theirs, "rb") as fh:
        untouched = fh.read() == b"theirs"
    check(
        "a map no marker claims is another file's: left alone, the bake takes _1",
        untouched
        and os.path.basename(placed.get("bs_crate", ("",))[0])
        == "Shared_Set_Lightmap_1.exr",
        f"{placed}",
    )
    with open(staged, "wb") as fh:
        fh.write(b"newer")
    placed = beside._place_unpacked(
        {"bs_crate": (staged, None)},
        bs_out,
        {"bs_crate": crate_dir},
        claims={"shared_set_lightmap.exr": frozenset({"bs_crate"})},
    )
    with open(theirs, "rb") as fh:
        replaced = fh.read() == b"newer"
    check(
        "...and one this object's marker claims is its own to replace",
        replaced and placed["bs_crate"][0] == theirs,
        f"{placed}",
    )

    # _place never deletes the old map before the new one is in: a swap that
    # fails leaves the destination as it was and takes an adjacent name.
    held_dir = os.path.join(par_dir, "held")
    os.makedirs(held_dir, exist_ok=True)
    old_map = os.path.join(held_dir, "Held_Lightmap.exr")
    with open(old_map, "wb") as fh:
        fh.write(b"old")
    fresh = os.path.join(par_dir, "Held_Lightmap.exr")
    with open(fresh, "wb") as fh:
        fh.write(b"new")
    real_move = LightmapBaker.__dict__["_move_into_place"]

    def _held(source, destination):
        raise PermissionError("held open")

    LightmapBaker._move_into_place = staticmethod(_held)
    try:
        landed = LightmapBaker._place(fresh, held_dir, set())
    finally:
        LightmapBaker._move_into_place = real_move
    with open(old_map, "rb") as fh:
        old_kept = fh.read() == b"old"
    check(
        "a held destination keeps its map and the bake takes an adjacent name",
        old_kept and os.path.basename(landed) == "Held_Lightmap_1.exr",
        f"{landed}",
    )
    # ...and the real swap, through _place_unpacked (Beside Material Textures):
    # a map held open (a viewer, a sync client) goes to the next free name,
    # never onto another file's, and one whose every move fails is left out --
    # the bake reports it unbaked -- with the old map intact and nothing staged
    # left behind. Mirror of mayatk's.
    import errno
    from unittest import mock

    def _written(path, data):
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _read(path):
        """*path*'s bytes, or ``None`` once it is gone (a lost map FAILs, not raises)."""
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    lock_dir = os.path.join(par_dir, "locked")
    os.makedirs(lock_dir, exist_ok=True)
    own_lock = _written(os.path.join(lock_dir, "Floor_Lightmap.exr"), b"old")
    theirs_lock = _written(os.path.join(lock_dir, "Floor_Lightmap_1.exr"), b"theirs")
    lock_src = _written(os.path.join(work, "Floor_Lightmap.exr"), b"new")
    if os.name == "nt":  # needs Windows' delete-while-open lock
        with open(own_lock, "rb"):  # held: it cannot be replaced
            locked = beside._place_unpacked(
                {"lock_obj": (lock_src, None)},
                lock_dir,
                claims={"floor_lightmap.exr": frozenset({"lock_obj"})},
            )
        check(
            "a map held open goes to the next free name, never onto another file's",
            os.path.basename(locked.get("lock_obj", ("",))[0]) == "Floor_Lightmap_2.exr"
            and _read(theirs_lock) == b"theirs"
            and _read(own_lock) == b"old",
            f"{locked} {sorted(os.listdir(lock_dir))}",
        )
    fail_dir = os.path.join(par_dir, "unplaceable")
    os.makedirs(fail_dir, exist_ok=True)
    own_fail = _written(os.path.join(fail_dir, "Fail_Lightmap.exr"), b"old")
    fail_src = _written(os.path.join(work, "Fail_Lightmap.exr"), b"new")
    # Every move fails at both layers FileUtils.move_file uses: the replace
    # over an existing map (refused as cross-volume, so the overwrite is
    # staged -- the one path a full disk can fail) and the move into the
    # stage or onto a free name.
    with (
        mock.patch.object(
            ptk.FileUtils,
            "replace_file",
            side_effect=OSError(errno.EXDEV, "Cross-device link"),
        ),
        mock.patch.object(
            shutil, "move", side_effect=OSError(28, "No space left on device")
        ),
    ):
        unplaced = beside._place_unpacked(
            {"fail_obj": (fail_src, None)},
            fail_dir,
            claims={"fail_lightmap.exr": frozenset({"fail_obj"})},
        )
    check(
        "a map that cannot be placed is left out, the old one intact, nothing staged",
        "fail_obj" not in unplaced
        and _read(own_fail) == b"old"
        and sorted(os.listdir(fail_dir)) == ["Fail_Lightmap.exr"],
        f"{unplaced} {sorted(os.listdir(fail_dir))}",
    )

    # Adaptive sampling: Cycles bakes honour the scene's flag, so the bake pins
    # it (with Cycles' own threshold) and puts the scene's back afterwards.
    scn.cycles.use_adaptive_sampling = False
    scn.cycles.adaptive_threshold = 0.2
    state = TextureBaker(adaptive=True)._configure_bake_scene(use_pass_color=False)
    pinned = (
        scn.cycles.use_adaptive_sampling,
        round(scn.cycles.adaptive_threshold, 4),
    )
    TextureBaker()._restore_bake_scene(state)
    check(
        "the bake pins adaptive sampling and Cycles' own threshold",
        pinned == (True, TextureBaker.ADAPTIVE_THRESHOLD),
        f"{pinned}",
    )
    check(
        "...and restores the scene's afterwards",
        scn.cycles.use_adaptive_sampling is False
        and round(scn.cycles.adaptive_threshold, 4) == 0.2,
    )
    state = TextureBaker(adaptive=False)._configure_bake_scene(use_pass_color=False)
    off = scn.cycles.use_adaptive_sampling
    TextureBaker()._restore_bake_scene(state)
    check("adaptive=False gives every texel the full budget", off is False)
    scn.cycles.use_adaptive_sampling = True
    scn.cycles.adaptive_threshold = 0.01
    check(
        "the baker carries the switch to its bake primitive",
        LightmapBaker(adaptive=False).adaptive is False
        and LightmapBaker(adaptive=False)._texture_baker.adaptive is False
        and LightmapBaker().adaptive is True,
    )

    # Presets carry the switches: a preset saved from the panel is a headless
    # recipe too. The store's user tier is pointed at scratch, never the user's.
    shipped = LightmapBaker.preset_store()
    scratch_store = ptk.PresetStore(
        "lightmap",
        builtin_dir=shipped.builtin_dir,
        user_dir=os.path.join(par_dir, "presets"),
    )
    panel_preset = {
        "packing": "atlas",
        "resolution": 512,
        "samples": 3,
        "bounces": 1,
        "adaptive": False,
        "include_environment": False,
        "denoise": False,
        "beside_textures": True,
        "device": "CPU",
    }
    scratch_store.save("roomPass", panel_preset)
    real_store = LightmapBaker.__dict__["preset_store"]
    LightmapBaker.preset_store = staticmethod(lambda: scratch_store)
    try:
        saved = LightmapBaker.from_preset("roomPass")
        overridden = LightmapBaker.from_preset(
            "roomPass", denoise=True, adaptive=True, device="GPU"
        )
    finally:
        LightmapBaker.preset_store = real_store
    check(
        "from_preset builds every switch a panel-saved preset stores",
        (saved.resolution, saved.samples, saved.bounces) == (512, 3, 1)
        and (saved.adaptive, saved.include_environment, saved.denoise)
        == (False, False, False)
        and saved.beside_textures is True,
    )
    check(
        "...never the device (one machine's hardware), and overrides still win",
        saved.device == LightmapBaker().device
        and (overridden.denoise, overridden.adaptive, overridden.device)
        == (True, True, "GPU"),
    )

    # The panel half needs uitk and a Qt binding -- the switches are found by
    # uitk's option type -- which tentacle's Blender carries (PySide6); a bare
    # Blender does not, and there it is reported rather than failed.
    try:
        import uitk.managers.preset_manager  # noqa: F401
        import uitk.managers.reset_gesture  # noqa: F401
        import uitk.widgets.optionBox.options.toggle  # noqa: F401

        panel_qt = None
    except Exception as exc:  # noqa: BLE001
        panel_qt = exc
    if panel_qt is not None:
        lines.append(f"OK   (skipped) the panel half: no uitk/Qt here ({panel_qt})")
    else:
        # --- the panel's half (no Qt: stubs answer the way uitk's widgets do) ---
        class _Toggle:
            def __init__(self, on=False):
                self.is_on = bool(on)

            def set_on(self, value, *, emit=True):
                self.is_on = bool(value)

        class _SwitchBox:
            """A field's option box: the one toggle it carries, found by type."""

            def __init__(self, on=None):
                self.toggle = None if on is None else _Toggle(on)
                self.wired = None
                self.actions = []

            def find_option(self, _option_type):
                return self.toggle

            def set_toggle(self, *, initial=True, **kwargs):
                self.toggle = _Toggle(initial)
                self.wired = dict(kwargs, initial=initial)
                return self

            def add_action(self, **kwargs):
                self.actions.append(kwargs)

            def browse(self, **kwargs):
                self.browsed = kwargs

            def resolve_affix(self, text=None, *, default="prefix"):
                return ptk.StrUtils.split_affix(
                    text or "", mode="auto", default=default
                )

        class _Spin:
            def __init__(self, v, switch=None):
                self._v = v
                self.option_box = _SwitchBox(switch)

            def value(self):
                return self._v

            def setValue(self, v):
                self._v = int(v)

        class _Combo:
            """Enough of QComboBox: addItem(s) by name or with item data."""

            def __init__(self, switch=None):
                self.items, self._index = [], -1
                self.option_box = _SwitchBox(switch)

            def clear(self):
                self.items, self._index = [], -1

            def addItems(self, items):
                for text in items:
                    self.addItem(text)

            def addItem(self, text, data=None):
                self.items.append((text, data))
                if self._index < 0:
                    self._index = 0

            def setCurrentIndex(self, index):
                self._index = index

            def currentIndex(self):
                return self._index

            def currentText(self):
                return (
                    self.items[self._index][0]
                    if 0 <= self._index < len(self.items)
                    else ""
                )

            def currentData(self):
                return (
                    self.items[self._index][1]
                    if 0 <= self._index < len(self.items)
                    else None
                )

        class _Line:
            def __init__(self, text="", placeholder="", switch=None):
                self._text, self._placeholder = text, placeholder
                self.option_box = _SwitchBox(switch)

            def text(self):
                return self._text

            def setText(self, text):
                self._text = text

            def placeholderText(self):
                return self._placeholder

            def setPlaceholderText(self, text):
                self._placeholder = text

        class _Progress:
            def __enter__(self):
                return lambda value=None, text=None: True

            def __exit__(self, *exc):
                return False

        class _Footer:
            def __init__(self):
                self._text = ""

            def setText(self, text):
                self._text = text

            def text(self):
                return self._text

            def progress(self, total=None, text=""):
                return _Progress()

        class _Label(_Footer):
            pass

        def _panel(
            res=1024,
            samples=256,
            bounces=4,
            packing="atlas",
            environment=True,
            adaptive=True,
            denoise=True,
            beside=False,
            device="AUTO",
            scope="Selected",
        ):
            """A panel over stub widgets, each wired by its own ``_init``."""
            p = LightmapBakerSlots.__new__(LightmapBakerSlots)
            ui = SimpleNamespace(
                cmb_scope=_Combo(),
                cmb002=_Combo(),
                cmb_device=_Combo(),
                cmb_resolution=_Combo(),
                spn_samples=_Spin(samples),
                spn_bounces=_Spin(bounces),
                txt_output_dir=_Line(placeholder="sourceimages"),
                txt000=_Line("_Lightmap", "_Lightmap"),
                lbl_exclude=_Label(),
                footer=_Footer(),
            )
            p.ui = ui
            p._baker = None
            p._last_output_dir = None
            p.cmb_scope_init(ui.cmb_scope)
            p.cmb002_init(ui.cmb002)
            p.cmb_device_init(ui.cmb_device)
            p.cmb_resolution_init(ui.cmb_resolution)
            p.spn_samples_init(ui.spn_samples)
            p.txt_output_dir_init(ui.txt_output_dir)
            p._set_resolution(res)
            p._set_packing(packing)
            ui.cmb_scope.setCurrentIndex(LightmapBakerSlots._SCOPE_LABELS.index(scope))
            ui.cmb_device.setCurrentIndex(
                [v for _t, v in LightmapBakerSlots._DEVICES].index(device)
            )
            for key, on in (
                ("include_environment", environment),
                ("adaptive", adaptive),
                ("denoise", denoise),
                ("beside_textures", beside),
            ):
                p._set_toggle_state(key, on)
            return p

        fresh_panel = _panel()
        check(
            "the panel opens on Atlas by Material (mayatk's default)",
            fresh_panel._packing() == "atlas",
        )
        check(
            "...the Selected scope with the environment in",
            fresh_panel._scope() == "selected" and fresh_panel._include_environment(),
        )
        check(
            "the Processor row offers Auto / GPU / CPU, Auto first",
            [v for _t, v in fresh_panel.ui.cmb_device.items] == ["AUTO", "GPU", "CPU"]
            and fresh_panel._device() == "AUTO"
            and all(
                t.startswith("Processor:") for t, _v in fresh_panel.ui.cmb_device.items
            ),
        )
        check(
            "each field wires its own switch under a panel-scoped key",
            fresh_panel.ui.spn_samples.option_box.wired["settings_key"]
            == "lightmap_baker_adaptive"
            and fresh_panel.ui.cmb_scope.option_box.wired["settings_key"]
            == "lightmap_baker_include_environment",
        )
        check(
            "the switches ship on, Beside Material Textures off",
            (
                fresh_panel._adaptive(),
                fresh_panel._denoise(),
                fresh_panel._beside_textures(),
            )
            == (True, True, False),
        )
        check(
            "the switches are keyed as the preset store keys them",
            set(LightmapBakerSlots._TOGGLES) == set(LightmapBaker.PRESET_BOOL_KEYS),
        )
        bare = _panel()
        for field in ("cmb_scope", "spn_samples", "cmb_resolution", "txt_output_dir"):
            getattr(bare.ui, field).option_box.toggle = None
        check(
            "a switch read before its field is wired gives the shipped default",
            (
                bare._include_environment(),
                bare._adaptive(),
                bare._denoise(),
                bare._beside_textures(),
            )
            == (True, True, True, False),
        )
        # Scope -> the meshes b000 bakes (before the Exclude set), per word: the
        # selection is resolved to meshes (a selected light never bakes), Scene
        # takes the scene's meshes, Visible only the ones on screen.
        bpy.ops.mesh.primitive_cube_add(location=(40, 0, 0))
        _seen = bpy.context.active_object
        _seen.name = "scopeProbeSeen"
        bpy.ops.mesh.primitive_cube_add(location=(44, 0, 0))
        _hidden = bpy.context.active_object
        _hidden.name = "scopeProbeHidden"
        bpy.ops.object.light_add(type="POINT", location=(42, 0, 3))
        _lamp = bpy.context.active_object
        _lamp.name = "scopeProbeLamp"
        _hidden.hide_set(True)
        bpy.ops.object.select_all(action="DESELECT")
        _seen.select_set(True)
        _lamp.select_set(True)
        try:

            def _names(scope):
                return {o.name for o in _panel(scope=scope)._scope_objects()}

            _probe = {"scopeProbeSeen", "scopeProbeHidden", "scopeProbeLamp"}
            _sel, _vis, _all = _names("Selected"), _names("Visible"), _names("Scene")
            check(
                "Scope Selected bakes the selected meshes, never the selected light",
                _sel == {"scopeProbeSeen"},
                f"{sorted(_sel)}",
            )
            check(
                "Scope Visible bakes the visible meshes only",
                _vis & _probe == {"scopeProbeSeen"},
                f"{sorted(_vis & _probe)}",
            )
            check(
                "Scope Scene bakes every scene mesh, hidden ones included",
                _all & _probe == {"scopeProbeSeen", "scopeProbeHidden"},
                f"{sorted(_all & _probe)}",
            )
        finally:
            for _o in (_seen, _hidden, _lamp):
                bpy.data.objects.remove(_o, do_unlink=True)
        tuned = _panel(
            res=2048,
            samples=64,
            bounces=2,
            packing="per_object",
            environment=False,
            adaptive=False,
            denoise=False,
            beside=True,
        )
        tuned_values = tuned._preset_values()
        check(
            "Save reads every bake setting under its store key",
            tuned_values
            == {
                "packing": "per_object",
                "resolution": 2048,
                "samples": 64,
                "bounces": 2,
                "include_environment": False,
                "adaptive": False,
                "denoise": False,
                "beside_textures": True,
            },
            f"{tuned_values}",
        )
        check(
            "every key the panel saves is one from_preset reads",
            set(tuned_values) - {"packing"}
            == set(LightmapBaker.PRESET_INT_KEYS) | set(LightmapBaker.PRESET_BOOL_KEYS),
        )
        reloaded = _panel()
        applied = reloaded._apply_preset_values(dict(tuned_values, description="x"))
        check(
            "a saved preset loads back onto every widget and switch",
            applied == len(tuned_values) and reloaded._preset_values() == tuned_values,
            f"{applied} {reloaded._preset_values()}",
        )
        keep = _panel(environment=False, adaptive=False, denoise=False, beside=True)
        keep._apply_preset_values(store.load("desktop"))
        kept_values = keep._preset_values()
        check(
            "a shipped tier moves the dials, bounces included, and leaves the switches",
            (kept_values["resolution"], kept_values["samples"], kept_values["bounces"])
            == (2048, 2048, 6)
            and (
                kept_values["include_environment"],
                kept_values["adaptive"],
                kept_values["denoise"],
                kept_values["beside_textures"],
            )
            == (False, False, False, True),
            f"{kept_values}",
        )
        bad = _panel(samples=64)
        check(
            "a bad preset value is skipped, not fatal",
            bad._apply_preset_values({"samples": "lots", "bounces": 3}) == 1
            and bad.ui.spn_samples.value() == 64
            and bad.ui.spn_bounces.value() == 3,
        )
        shown = _panel()
        shown._show_output_mode(True)
        check(
            "the beside switch says in the empty field where the maps go",
            shown.ui.txt_output_dir.placeholderText()
            == "beside textures, else sourceimages"
            and shown.ui.txt_output_dir.option_box.wired.get("on_toggled")
            == shown._show_output_mode,
        )

        # The .ui: the sections read top to bottom as mayatk's do, each switch on
        # the field it qualifies (no checkbox rows), and the dial defaults ARE the
        # default preset -- nothing re-applies a preset at open any more.
        ui_root = ET.parse(
            os.path.join(os.path.dirname(slots_mod.__file__), "lightmap_baker.ui")
        ).getroot()

        def _layout_items(name):
            layout = next(i for i in ui_root.iter("layout") if i.get("name") == name)
            return [
                child.get("name") for item in layout.findall("item") for child in item
            ]

        check(
            "the panel's sections read top to bottom as mayatk's do",
            _layout_items("main_layout")
            == [
                "header",
                "cmb_scope",
                "exclude_layout",
                "bake_set_layout",
                "cmb002",
                "cmb_device",
                "quality_group",
                "output_group",
                "grp_process",
                "verticalSpacer",
                "footer",
            ],
            f"{_layout_items('main_layout')}",
        )
        check(
            "the Bake Set row mirrors the Exclude row: a label and one button "
            "(its switches ride the button's option box)",
            _layout_items("bake_set_layout") == ["lbl_bake_set", "select_bake_set"],
            f"{_layout_items('bake_set_layout')}",
        )
        check(
            "the Quality group holds Resolution, Samples and Bounces",
            _layout_items("quality_layout")
            == ["cmb_resolution", "spn_samples", "spn_bounces"],
        )
        check(
            "the action group reads Preset, Reset, Bake",
            _layout_items("process_layout") == ["cmb000", "btn_reset_defaults", "b000"],
        )
        check(
            "no switch is left as a checkbox row",
            [
                w.get("name")
                for w in ui_root.iter("widget")
                if w.get("class") == "QCheckBox"
            ]
            == [],
        )

        def _ui_value(widget_name):
            widget = next(
                w for w in ui_root.iter("widget") if w.get("name") == widget_name
            )
            prop = next(
                p for p in widget.findall("property") if p.get("name") == "value"
            )
            return int(prop.findtext("number"))

        default_tier = store.load(LightmapBakerSlots._DEFAULT_PRESET)
        check(
            "the .ui's dial defaults are the default preset's dials",
            (
                _ui_value("spn_samples"),
                _ui_value("spn_bounces"),
                fresh_panel._resolution(),
            )
            == (
                default_tier["samples"],
                default_tier["bounces"],
                default_tier["resolution"],
            ),
            f"{default_tier}",
        )

        # cmb000_init: seeded once per machine; a pointer on a retired tier follows it.
        class _Settings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

        class _SeedPresets:
            pointer = None

            def __init__(self, **_kwargs):
                self.active_preset = _SeedPresets.pointer

            def use_logger(self, _logger):
                pass

            def exists(self, name):
                return name == LightmapBakerSlots._DEFAULT_PRESET

            def wire_combo(self, widget, placeholder=None):
                pass

        import uitk.managers.preset_manager as preset_manager_mod

        def _open(settings):
            p = LightmapBakerSlots.__new__(LightmapBakerSlots)
            p.ui = SimpleNamespace(settings=settings)
            real_manager = preset_manager_mod.PresetManager
            preset_manager_mod.PresetManager = _SeedPresets
            try:
                p.cmb000_init(SimpleNamespace(restore_state=True))
            finally:
                preset_manager_mod.PresetManager = real_manager
            return p._presets.active_preset

        seed = _Settings()
        _SeedPresets.pointer = None
        check("the first open names the default tier", _open(seed) == "mobile")
        _SeedPresets.pointer = None  # ...and a reset cleared it
        check("an open after a reset keeps the pointer cleared", _open(seed) is None)
        _SeedPresets.pointer = "quest"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            followed = _open(seed)
        check(
            "a pointer left on a renamed tier follows the rename", followed == "mobile"
        )

        class _FakePresets:
            def __init__(self, active):
                self.active_preset = active
                self.refreshed = 0

            def refresh_combo(self, select_name=None):
                self.refreshed += 1

        reset_panel = LightmapBakerSlots.__new__(LightmapBakerSlots)
        reset_panel._presets = _FakePresets("mobile")
        reset_panel._after_reset("reset")
        check(
            "a reset lets go of the active preset",
            reset_panel._presets.active_preset is None
            and reset_panel._presets.refreshed == 1,
        )
        reset_panel._presets = _FakePresets("mobile")
        reset_panel._after_reset("save")
        check(
            "saving the current values as defaults keeps it",
            reset_panel._presets.active_preset == "mobile",
        )

        # b000 hands the Scope (minus the Exclude set) and every dial and switch to
        # bake(); the fake keeps the engine's contract, exclusion included.
        class _FakeWorkflow:
            instances = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.calls = []
                _FakeWorkflow.instances.append(self)

            def bake(self, objects, packing="atlas", output_dir=None, **kwargs):
                from blendertk.light_utils.lightmap_baker.lightmap_baker import (
                    LightmapBakeResult,
                )

                targets = LightmapBaker.bake_targets(objects)
                names = [o.name for o in TextureBaker.resolve_meshes(objects)]
                result = LightmapBakeResult(
                    excluded=[n for n in names if n not in targets]
                )
                self.calls.append(("bake", tuple(targets), packing))
                if not targets:
                    result.refused = (
                        "Nothing to bake: all objects are in the Exclude set."
                    )
                    return result
                for n in targets:
                    result.maps[n] = os.path.join(
                        output_dir or par_dir, f"{n}_Lightmap.exr"
                    )
                return result

            def baked_objects(self, objects=None):
                self.calls.append(
                    ("baked_objects", tuple(objects) if objects else None)
                )
                return list(objects) if objects else ["marked"]

            def revert(self, objects=None):
                self.calls.append(("revert", tuple(objects) if objects else None))
                return list(objects) if objects else ["marked"]

        real_workflow = slots_mod.LightmapBaker
        slots_mod.LightmapBaker = _FakeWorkflow
        try:
            _FakeWorkflow.instances = []
            bpy.ops.object.select_all(action="DESELECT")
            for obj in (ex_a, ex_b):
                obj.select_set(True)
            LightmapExcludeSet.define([ex_b])
            run = _panel(
                bounces=6,
                adaptive=False,
                environment=False,
                denoise=False,
                beside=True,
                device="CPU",
                packing="per_object",
            )
            run._output_dir = lambda: par_dir
            run.b000()
            wf_fake = _FakeWorkflow.instances[0]
            check(
                "b000 carries Bounces, Adaptive, Beside, Environment, Denoise and "
                "the Processor to the bake",
                {
                    k: wf_fake.kwargs[k]
                    for k in (
                        "bounces",
                        "adaptive",
                        "beside_textures",
                        "include_environment",
                        "denoise",
                        "device",
                    )
                }
                == {
                    "bounces": 6,
                    "adaptive": False,
                    "beside_textures": True,
                    "include_environment": False,
                    "denoise": False,
                    "device": "CPU",
                },
                f"{wf_fake.kwargs}",
            )
            check(
                "b000 never bakes an excluded object, and says how many it left",
                wf_fake.calls == [("bake", ("ex_a",), "per_object")]
                and "1 excluded" in run.ui.footer.text(),
                f"{wf_fake.calls} | {run.ui.footer.text()}",
            )
            bpy.ops.object.select_all(action="DESELECT")
            ex_b.select_set(True)
            run.b000()
            check(
                "with everything excluded b000 bakes nothing and says why",
                "Exclude set" in run.ui.footer.text(),
                run.ui.footer.text(),
            )
            LightmapExcludeSet.clear()

            # Revert to Source: asks first, and a Cancel changes nothing.
            class _Sb:
                def __init__(self, answer):
                    self.answer, self.asked = answer, []

                def confirm(self, question, yes="Yes", no="No"):
                    self.asked.append((question, (yes, no)))
                    return self.answer == yes

            bpy.ops.object.select_all(action="DESELECT")
            ex_a.select_set(True)
            rv = _panel()
            rv.sb = _Sb("Cancel")
            rv.revert_to_source()
            check(
                "Revert to Source asks first, and Cancel reverts nothing",
                len(rv.sb.asked) == 1
                and "1 selected object" in rv.sb.asked[0][0]
                and rv.sb.asked[0][1] == ("Ok", "Cancel")
                and "revert" not in [c[0] for c in rv._baker.calls]
                and "cancelled" in rv.ui.footer.text(),
                f"{rv.sb.asked} {rv._baker.calls}",
            )
            rv.sb = _Sb("Ok")
            rv._baker = None
            rv.revert_to_source()
            check(
                "...and once confirmed it reverts the selection",
                rv._baker.calls
                == [("baked_objects", ("ex_a",)), ("revert", ("ex_a",))],
                f"{rv._baker.calls}",
            )
            bpy.ops.object.select_all(action="DESELECT")
            rv._baker = None
            rv.sb = _Sb("Ok")
            rv.revert_to_source()
            check(
                "...with nothing selected it offers every baked object, and says so",
                "all of them" in rv.sb.asked[0][0]
                and rv._baker.calls[-1] == ("revert", None),
            )
        finally:
            slots_mod.LightmapBaker = real_workflow

        # The Exclude row: set, select and clear the file's set; the label counts
        # the meshes the bake will skip (an Empty counts what is under it).
        row = _panel()
        bpy.ops.object.select_all(action="DESELECT")
        ex_group.select_set(True)
        row.set_exclusions()
        check(
            "the Exclude row stores the selection and counts its meshes",
            row.ui.lbl_exclude.text() == "Exclude (1):"
            and "1 mesh excluded" in row.ui.footer.text(),
            f"{row.ui.lbl_exclude.text()} | {row.ui.footer.text()}",
        )
        bpy.ops.object.select_all(action="DESELECT")
        row.select_exclusions()
        check(
            "Select selects the set's members",
            [o.name for o in CoreUtils.selected_objects()] == ["ex_group"],
            f"{[o.name for o in CoreUtils.selected_objects()]}",
        )
        row.clear_exclusions()
        check(
            "Clear removes the set and resets the label",
            not LightmapExcludeSet.exists() and row.ui.lbl_exclude.text() == "Exclude:",
        )
        bpy.ops.object.select_all(action="DESELECT")
        row.set_exclusions()
        check(
            "Set with nothing selected clears, and says so",
            not LightmapExcludeSet.exists() and "cleared" in row.ui.footer.text(),
        )
        bpy.ops.object.select_all(action="DESELECT")
        par_sun.select_set(True)
        row.set_exclusions()
        check(
            "a selection with no mesh in it excludes nothing, and says so",
            row.ui.lbl_exclude.text() == "Exclude:"
            and "holds no meshes" in row.ui.footer.text(),
            row.ui.footer.text(),
        )
        LightmapExcludeSet.clear()

    LightmapBaker().revert()
    for obj in (
        ex_a,
        ex_b,
        ex_c,
        ex_group,
        floor,
        box,
        par_sun,
        bs_crate,
        bs_gone,
        bs_plain,
        shelf_a,
        shelf_b,
    ):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(box_home)

    # --- the folder hint never rides a marker (BACKLOG 2026-09-19) -----------
    # A marker is an object property, so it rides every FBX; a folder on it put
    # the authoring machine's build setup on the deliverable. The folder lives
    # in the private LIGHTMAP_DIRS record now, and a marker baked before the
    # move is lifted -- by a bake's migration, and by every export bracket.
    bpy.ops.mesh.primitive_cube_add()
    hint_cube = bpy.context.active_object
    hint_cube.name = "hint_cube"
    hint_dir = os.path.join(tmp_dir, "hint_maps")
    os.makedirs(hint_dir, exist_ok=True)
    hint_map = os.path.join(hint_dir, "hint_cube_LightMap.exr")
    open(hint_map, "wb").close()
    LightmapRecords.commit({hint_cube.name: hint_map})
    hint_marker = json.loads(hint_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    hints = LightmapRecords._folder_hints()
    check(
        "a commit writes no folder onto the marker",
        "dir" not in hint_marker and hint_marker.get("map") == "hint_cube_LightMap.exr",
        f"{hint_marker}",
    )
    check(
        "...the private record holds it, and the map still resolves by it",
        _same_dir(
            LightmapRecords._resolved_dir(
                hints.get("hint_cube_lightmap.exr", ""), "hint_cube_LightMap.exr"
            ),
            hint_dir,
        )
        and LightmapRecords.lightmap_dependencies(search_dirs=[], walk=False)[0][
            "found_by"
        ]
        == LightmapBaker.FOUND_BY_HINT,
        f"{hints}",
    )

    # A marker baked BEFORE the move: its folder rides on it.
    legacy_info = dict(hint_marker, dir=LightmapRecords._portable_dir(hint_map))
    LightmapRecords._write_marker(hint_cube, legacy_info)
    LightmapRecords._save_folder_hints({})
    check(
        "a legacy marker's folder still resolves before any migration",
        LightmapRecords.lightmap_dependencies(search_dirs=[], walk=False)[0]["found_by"]
        == LightmapBaker.FOUND_BY_HINT,
    )
    stagers = btk.FbxUtils.stagers(["lightmap_folder_hints"])
    check(
        "every export bracket stages the lift, one way (no finish)",
        "lightmap_folder_hints" in stagers
        and callable(stagers["lightmap_folder_hints"][0])
        and stagers["lightmap_folder_hints"][1] is None,
        f"{stagers}",
    )
    fbx_path = os.path.join(tmp_dir, "hint_cube.fbx")
    bpy.ops.object.select_all(action="DESELECT")
    hint_cube.select_set(True)
    with btk.FbxUtils.export_prepared():
        bpy.ops.export_scene.fbx(
            filepath=fbx_path, use_selection=True, use_custom_props=True
        )
    with open(fbx_path, "rb") as fh:
        fbx_bytes = fh.read()
    lifted = json.loads(hint_cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "an export of a legacy file ships the marker WITHOUT its folder",
        b"hint_cube_LightMap.exr" in fbx_bytes and b'"dir"' not in fbx_bytes,
        f"marker in FBX: {b'hint_cube_LightMap.exr' in fbx_bytes}, "
        f"dir in FBX: {b'dir' in fbx_bytes}",
    )
    check(
        "...the folder moved into the private record, off the marker",
        "dir" not in lifted
        and LightmapRecords._folder_hints().get("hint_cube_lightmap.exr")
        == legacy_info["dir"],
        f"{lifted} {LightmapRecords._folder_hints()}",
    )
    # A folder the record already holds for a map wins over a stale marker's.
    LightmapRecords._write_marker(hint_cube, dict(lifted, dir="//stale_folder"))
    check(
        "the record's folder wins over a legacy marker's own",
        LightmapRecords.migrate_folder_hints() == ["hint_cube"]
        and LightmapRecords._folder_hints().get("hint_cube_lightmap.exr")
        == legacy_info["dir"],
        f"{LightmapRecords._folder_hints()}",
    )
    check(
        "...and a second lift has nothing to do",
        LightmapRecords.migrate_folder_hints() == [],
    )
    LightmapRecords.revert()
    check(
        "a revert drops the reverted maps' folders from the record",
        LightmapRecords._folder_hints() == {},
        f"{LightmapRecords._folder_hints()}",
    )

    # --- the folder is spelled from the FILE's own project (2026-09-23) ----------
    # BACKLOG 2026-09-22, decided 2026-09-23 (mirror of mayatk): relative to the
    # project the .blend lives in -- a ../ chain to a shared library beside it --
    # the spelling mayatk stores, so the record reads alike across the bridge.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    proj = os.path.join(tmp_dir, "path_rule", "show")
    os.makedirs(os.path.join(proj, "scenes"), exist_ok=True)
    with open(os.path.join(proj, "workspace.mel"), "w") as fh:
        fh.write("//Maya 2025 Project Definition\n")
    in_dir = os.path.join(proj, "sourceimages", "lm")
    lib_dir = os.path.join(tmp_dir, "path_rule", "library")
    for folder in (in_dir, lib_dir):
        os.makedirs(folder, exist_ok=True)
    check(
        "an unsaved file has no project: the folder is spelled absolute",
        os.path.isabs(LightmapRecords._portable_dir(os.path.join(in_dir, "a.exr"))),
    )
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "scenes", "room.blend"))
    spelled_in = LightmapRecords._portable_dir(os.path.join(in_dir, "a.exr"))
    spelled_lib = LightmapRecords._portable_dir(os.path.join(lib_dir, "b.exr"))
    check(
        "saved into a project: inside it, and a ../ chain beside it",
        (spelled_in, spelled_lib) == ("sourceimages/lm", "../library"),
        f"{spelled_in} {spelled_lib}",
    )
    check(
        "...each resolves back to its folder",
        _same_dir(LightmapRecords._resolved_dir(spelled_in, "a.exr"), in_dir)
        and _same_dir(LightmapRecords._resolved_dir(spelled_lib, "b.exr"), lib_dir),
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- a re-bake deletes the maps it superseded, and only its own ----------
    # Mirror of mayatk's TestSupersededMaps: changing where or how maps are
    # written left the old ones on disk, read by nobody. Every keep rule is a
    # reader a delete would strand. Last, since it saves .blend files.
    from unittest import mock

    def _norm(path):
        return os.path.normcase(os.path.abspath(path))

    def _exr(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"map")
        return path

    sup_dir = os.path.join(tmp_dir, "superseded")
    # The file's project: only a map inside it is ever set aside (mirror of
    # mayatk's TestSupersededMaps, which opens a workspace on its root).
    btk.EnvUtils.set_current_workspace(sup_dir)
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 6))
    bpy.ops.mesh.primitive_cube_add()
    sup_cube = bpy.context.active_object
    sup_cube.name = "sup_cube"
    btk.assign_mat(sup_cube, btk.create_mat("standard", name="sup_mat"))
    sup_baker = LightmapBaker.from_preset(
        "preview", resolution=64, samples=4, denoise=False, device="CPU"
    )
    first = sup_baker.bake([sup_cube], packing="per_object", output_dir=sup_dir)
    old = first.maps.get(sup_cube.name, "")
    second = sup_baker.bake(
        [sup_cube], packing="per_object", output_dir=sup_dir, suffix="_LM"
    )
    check(
        "a re-bake under another affix retires the old map",
        os.path.isfile(second.maps.get(sup_cube.name, ""))
        and not os.path.isfile(old)
        and [_norm(p) for p in second.retired] == [_norm(old)],
        f"{first.maps} {second.maps} {second.retired}",
    )
    # BACKLOG 2026-09-23, decided 2026-09-27: no file can see another's reads
    # (a Save As source, an Explorer copy), so a retired map stays one restore
    # away -- the Recycle Bin, else a _superseded folder beside it (pinned).
    _aside = os.path.join(
        os.path.dirname(old), ptk.FileDependencies.SUPERSEDED_DIR, os.path.basename(old)
    )
    check(
        "...set aside beside it, never deleted",
        os.path.isfile(_aside),
        _aside,
    )
    check(
        "...where no walk finds it again",
        ptk.FileDependencies.find_files([os.path.basename(old)], sup_dir) == [],
    )
    _bin = os.path.join(tmp_dir, "bin")
    os.makedirs(_bin, exist_ok=True)

    def _to_bin(path):
        target = os.path.join(_bin, os.path.basename(path))
        os.replace(path, target)
        return target

    with _mock.patch.object(ptk.FileUtils, "move_to_trash", side_effect=_to_bin):
        third = sup_baker.bake(
            [sup_cube], packing="per_object", output_dir=sup_dir, suffix="_L3"
        )
    _was = second.maps.get(sup_cube.name, "")
    check(
        "a volume with a trash takes the retired map instead",
        os.path.isfile(os.path.join(_bin, os.path.basename(_was)))
        and [_norm(p) for p in third.retired] == [_norm(_was)],
        f"{third.retired}",
    )

    # A map the file once wrote OUTSIDE its project -- beside a texture
    # library another project shares -- sits in that project's folder too,
    # whose files may read it and this one cannot see: a soldering assembly's
    # re-bake sent another project's room lightmap to the Recycle Bin (mayatk,
    # 2026-10-03). Only a map inside the file's project is set aside.
    outside_dir = os.path.join(tmp_dir, "sup_outside")
    _far = sup_baker.bake(
        [sup_cube], packing="per_object", output_dir=outside_dir, suffix="_Far"
    ).maps.get(sup_cube.name, "")
    _back = sup_baker.bake([sup_cube], packing="per_object", output_dir=sup_dir)
    check(
        "a map outside the project is never set aside",
        os.path.isfile(_far) and _back.retired == [],
        f"{_far} {_back.retired}",
    )

    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    sup_other = bpy.context.active_object
    sup_other.name = "sup_other"
    shared = _exr(os.path.join(sup_dir, "Shared_Lightmap.exr"))
    LightmapRecords.commit({sup_cube.name: shared, sup_other.name: shared})
    with LightmapRecords.superseding([sup_cube.name]):
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "Moved_Lightmap.exr"))}
        )
    check("a map another object still reads is kept", os.path.isfile(shared))
    with LightmapRecords.superseding([sup_other.name]):
        LightmapRecords.commit(
            {sup_other.name: _exr(os.path.join(sup_dir, "Moved2_Lightmap.exr"))}
        )
    check("...and deleted once nothing does", not os.path.isfile(shared))

    source = os.path.join(sup_dir, "source.blend")
    check(
        "a map baked while unsaved is stamped unsaved",
        LightmapRecords._writers().get("moved_lightmap.exr") == "",
        f"{LightmapRecords._writers()}",
    )
    bpy.ops.wm.save_as_mainfile(filepath=source)
    # BACKLOG 2026-09-23: "" is the file's own only while it is unsaved, so a
    # map baked before the first save was never retired once saved (measured:
    # the old map stayed). The first save stamps it with the file written.
    check(
        "the first save stamps the maps baked before it with the file",
        LightmapRecords._writers().get("moved_lightmap.exr") == "source.blend",
        f"{LightmapRecords._writers()}",
    )
    _pre_save = LightmapRecords._marker_info(sup_cube)["map"]
    with LightmapRecords.superseding([sup_cube.name]) as _gone:
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "FirstSave_Lightmap.exr"))}
        )
    check(
        "...so a re-bake after it retires a map baked before it",
        not os.path.isfile(os.path.join(sup_dir, _pre_save))
        and [os.path.basename(p) for p in _gone] == [_pre_save],
        f"{_pre_save} {_gone}",
    )
    written = _exr(os.path.join(sup_dir, "Source_Lightmap.exr"))
    LightmapRecords.commit({sup_cube.name: written})
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(sup_dir, "copy.blend"))
    with LightmapRecords.superseding([sup_cube.name]) as gone:
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "Copy_Lightmap.exr"))}
        )
    check(
        "the maps a Save As copy's source wrote are kept",
        os.path.isfile(written) and gone == [],
        f"{gone}",
    )

    copy_map = LightmapRecords._marker_info(sup_cube)["map"]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(sup_dir, "renamed.blend"))
    os.remove(os.path.join(sup_dir, "copy.blend"))
    with LightmapRecords.superseding([sup_cube.name]):
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "Renamed_Lightmap.exr"))}
        )
    check(
        "a renamed file's own maps are still its own",
        not os.path.isfile(os.path.join(sup_dir, copy_map)),
    )

    kept = LightmapRecords._marker_info(sup_cube)["map"]
    LightmapRecords._save_writers({})
    with LightmapRecords.superseding([sup_cube.name]):
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "Unstamped_Lightmap.exr"))}
        )
    check(
        "a map committed before writers were recorded is kept",
        os.path.isfile(os.path.join(sup_dir, kept)),
    )

    linked = LightmapRecords._marker_info(sup_cube)["map"]
    with mock.patch.object(LightmapRecords, "_referenced", return_value=True):
        with LightmapRecords.superseding([sup_cube.name]):
            LightmapRecords.commit(
                {sup_cube.name: _exr(os.path.join(sup_dir, "Linked_Lightmap.exr"))}
            )
    check(
        "a map a linked object reads is kept",
        os.path.isfile(os.path.join(sup_dir, linked)),
    )

    raised = LightmapRecords._marker_info(sup_cube)["map"]
    try:
        with LightmapRecords.superseding([sup_cube.name]):
            LightmapRecords.commit(
                {sup_cube.name: _exr(os.path.join(sup_dir, "Raised_Lightmap.exr"))}
            )
            raise RuntimeError("the bake failed after its commit")
    except RuntimeError:
        pass
    check(
        "a block that raises moves nothing",
        os.path.isfile(os.path.join(sup_dir, raised)),
    )
    # The boundary is the workspace blendertk's workspace tool names (decided
    # 2026-10-04): the pin -- here sup_dir, which holds this file -- else the
    # nearest workspace.mel, else the .blend's own folder. A pin that does not
    # hold the saved file is another project's: the file's own stands.
    check(
        "the project is the pinned workspace that holds the file",
        _norm(LightmapRecords.project_root() or "") == _norm(sup_dir),
        f"{LightmapRecords.project_root()}",
    )
    elsewhere = os.path.join(tmp_dir, "sup_elsewhere")
    os.makedirs(elsewhere, exist_ok=True)
    btk.EnvUtils.set_current_workspace(elsewhere)
    check(
        "...a pin that does not hold it falls back to the file's own project",
        _norm(LightmapRecords.project_root() or "") == _norm(sup_dir),
        f"{LightmapRecords.project_root()}",
    )
    btk.EnvUtils.set_current_workspace(None)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    check(
        "...and an unsaved file with no pin has none",
        LightmapRecords.project_root() is None,
        f"{LightmapRecords.project_root()}",
    )

    # --- a LINKED library's maps resolve by ITS folder record (2026-09-23) -----
    # Mirror of mayatk's referenced-module record: a library baked in its own
    # file keeps its folders in ITS scene group, and the markers carry none, so
    # a host linking it found its maps by the texture folders only (measured:
    # found_by None) -- a GLB build shipped them unlit, or bound a same-named
    # stale map.
    import time as _time

    from blendertk.node_utils.data_nodes import DataNodes as _DN

    def _project(root):
        os.makedirs(os.path.join(root, "scenes"), exist_ok=True)
        with open(os.path.join(root, "workspace.mel"), "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        return root

    lib_proj = _project(os.path.join(tmp_dir, "linked", "library"))
    host_proj = _project(os.path.join(tmp_dir, "linked", "host"))
    lib_file = os.path.join(lib_proj, "scenes", "props.blend")
    bpy.ops.wm.save_as_mainfile(filepath=lib_file)
    bpy.ops.mesh.primitive_cube_add()
    lib_cube = bpy.context.active_object
    lib_cube.name = "lib_crate"
    lib_map = _exr(os.path.join(lib_proj, "sourceimages", "lm", "lib_crate_LM.exr"))
    LightmapRecords.commit({lib_cube.name: lib_map})
    check(
        "the library records its folder spelled from ITS project",
        LightmapRecords._folder_hints() == {"lib_crate_lm.exr": "sourceimages/lm"},
        f"{LightmapRecords._folder_hints()}",
    )
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(host_proj, "scenes", "set.blend"))
    with bpy.data.libraries.load(lib_file, link=True) as (_src, _dst):
        _dst.objects = ["lib_crate"]
    _linked = bpy.data.objects.get("lib_crate")
    check(
        "the host links the marked object",
        _linked is not None
        and _linked.library is not None
        and LightmapBaker.LIGHTMAP_INFO_PROP in _linked,
    )
    _DN._LIBRARY_SCENE_VALUES.clear()  # measure a cold read
    _t0 = _time.perf_counter()
    _deps = LightmapRecords.lightmap_dependencies(search_dirs=[], walk=False)
    _cold = _time.perf_counter() - _t0
    _t0 = _time.perf_counter()
    _dirs = LightmapRecords.search_dirs()
    _warm = _time.perf_counter() - _t0
    check(
        "a linked library's map resolves by the library's own folder record",
        [(d["map"], d["found_by"]) for d in _deps]
        == [("lib_crate_LM.exr", LightmapBaker.FOUND_BY_HINT)]
        and _same_dir(
            os.path.dirname(_deps[0]["path"] or ""), os.path.dirname(lib_map)
        ),
        f"{_deps}",
    )
    check(
        "...and leads the folders a GLB build searches",
        bool(_dirs) and _same_dir(_dirs[0], os.path.dirname(lib_map)),
        f"{_dirs}",
    )
    check(
        "...read, not copied: the host's own record stays empty",
        LightmapRecords._folder_hints() == {},
        f"{LightmapRecords._folder_hints()}",
    )
    check(
        "...and the library's scenes leave nothing linked behind",
        [s.name for s in bpy.data.scenes] == [bpy.context.scene.name],
        f"{[s.name for s in bpy.data.scenes]}",
    )
    print(
        f"linked-library folder read: cold {_cold * 1000:.1f} ms, warm {_warm * 1000:.1f} ms"
    )

    # Two levels: the host links an assembly that links the library. Blender
    # re-spells the INDIRECT library's path from the HOST's file (measured 5.1:
    # the assembly stores "//../../library/...", the host shows it from its own
    # folder, fresh and reopened alike), so it resolves as a direct one does --
    # read from the assembly's folder (``library=parent``) it named a file that
    # is not there.
    asm_proj = _project(os.path.join(tmp_dir, "linked", "assembly"))
    asm_file = os.path.join(asm_proj, "scenes", "set_dressing.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=asm_file)
    with bpy.data.libraries.load(lib_file, link=True, relative=True) as (_src, _dst):
        _dst.objects = ["lib_crate"]
    _asm = bpy.data.collections.new("ASM_PROPS")
    bpy.context.scene.collection.children.link(_asm)
    _asm.objects.link(bpy.data.objects["lib_crate"])
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # A level deeper than the assembly, so the two spellings differ.
    nested_host = os.path.join(host_proj, "scenes", "shots", "nested.blend")
    os.makedirs(os.path.dirname(nested_host), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=nested_host)
    with bpy.data.libraries.load(asm_file, link=True, relative=True) as (_src, _dst):
        _dst.collections = ["ASM_PROPS"]
    bpy.context.scene.collection.children.link(bpy.data.collections["ASM_PROPS"])
    for _when in ("linked", "reopened"):
        if _when == "reopened":
            bpy.ops.wm.save_mainfile()
            bpy.ops.wm.open_mainfile(filepath=nested_host)
        _DN._LIBRARY_SCENE_VALUES.clear()
        _deps = LightmapRecords.lightmap_dependencies(search_dirs=[], walk=False)
        _crate = bpy.data.objects.get("lib_crate")
        check(
            f"a library linked through another resolves by its own record ({_when})",
            _crate is not None
            and _crate.library is not None
            and [(d["map"], d["found_by"]) for d in _deps]
            == [("lib_crate_LM.exr", LightmapBaker.FOUND_BY_HINT)]
            and _same_dir(
                os.path.dirname(_deps[0]["path"] or ""), os.path.dirname(lib_map)
            ),
            f"{[(lib.name, lib.filepath) for lib in bpy.data.libraries]} {_deps}",
        )
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- a scoped export names only the export set's objects ---------------
    # The record is export-scoped (mirror of mayatk's): a marker outside the
    # export set ships its map with nothing to bind it to -- a production
    # FBX's hidden group of stale copies shipped five outdated maps
    # (2026-10-09). Roots, descendants included; none is the file.
    bpy.ops.mesh.primitive_cube_add()
    _ship = bpy.context.active_object
    _ship.name = "ship_leaf"
    _ship_root = bpy.data.objects.new("SHIP_ROOT", None)
    bpy.context.scene.collection.objects.link(_ship_root)
    _ship.parent = _ship_root
    bpy.ops.mesh.primitive_cube_add()
    _stale = bpy.context.active_object
    _stale.name = "stale_leaf"
    LightmapRecords.commit(
        {
            _ship.name: os.path.join(tmp_dir, "ship_LM.exr"),
            _stale.name: os.path.join(tmp_dir, "stale_LM.exr"),
        }
    )

    def _scoped_names(scope):
        rec = LightmapRecords.export_record(ptk.ExportContext(scope=scope))
        return sorted(o["name"] for o in rec.payload["objects"]) if rec else []

    check(
        "a scoped export names only the export set's objects",
        _scoped_names(("SHIP_ROOT",)) == ["ship_leaf"],
        _scoped_names(("SHIP_ROOT",)),
    )
    check(
        "...no scope is the whole file; an empty one names nothing",
        _scoped_names(None) == ["ship_leaf", "stale_leaf"] and _scoped_names(()) == [],
        (_scoped_names(None), _scoped_names(())),
    )
    LightmapRecords.revert()
    bpy.ops.wm.read_factory_settings(use_empty=True)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    try:
        _no_trash.stop()
    except (NameError, RuntimeError):
        pass  # never started
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(ln.startswith("OK") for ln in lines)
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
