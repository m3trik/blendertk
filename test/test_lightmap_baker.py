"""blendertk lightmap baker headless test — real Cycles bake on a tiny scene.

Run: blender --background --factory-startup --python blendertk/test/test_lightmap_baker.py

Exercises the engine end-to-end (create_lightmap_uvs → Cycles bake → commit → revert) and the
Unity bridge (DataNodes manifest). Tiny resolution / samples so the real bake stays fast.
"""

import dataclasses
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

    # The old names of the lightmaps record's key: aliases, retired (removed
    # in 0.21.0). Added: 2026-10-20
    import warnings as _lm_warnings

    for _lm_owner in (LightmapRecords, LightmapBaker):
        with _lm_warnings.catch_warnings(record=True) as _lm_caught:
            _lm_warnings.simplefilter("always")
            _lm_key = _lm_owner.LIGHTMAP_METADATA
        check(
            f"{_lm_owner.__name__}.LIGHTMAP_METADATA: a deprecated alias of "
            "the lightmaps record's key",
            _lm_key == ptk.SceneRecords.LIGHTMAPS.key
            and any("LIGHTMAP_METADATA" in str(w.message) for w in _lm_caught),
            [str(w.message) for w in _lm_caught],
        )

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

    baker.commit_lightmap(result)
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
    # What bake(intensity=) runs on the maps it wrote: one file named twice
    # (two objects on one atlas) scales once.
    baker._apply_intensity([ipath, ipath], 2.0)
    LightmapRecords.commit({cube.name: ipath, cube_b.name: ipath}, intensity=2.0)
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
        "the markers keep intensity 2.0 for both objects; the record ships none "
        "(its texels carry it: the map is applied as stored)",
        len(recs) == 2
        and all("intensity" not in r for r in recs)
        and all(
            LightmapRecords.lightmap_info(o).get("intensity") == 2.0
            for o in (cube, cube_b)
        ),
        f"{[(r.get('name'), r.get('intensity')) for r in recs]}",
    )
    baker.revert([cube, cube_b])
    check(
        "intensity commit reverts clean",
        (ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes) or "") == "",
    )

    # --- a legacy uvRect marker (mirror of mayatk's pre-0.17 pack bookkeeping) --
    # (an old pack's squeeze recorded on the marker as ``uvRect`` -- revert
    # bookkeeping only -- while the manifest keeps publishing an identity
    # scaleOffset.)
    rect = [0.5, 0.5, 0.25, 0.25]
    LightmapRecords.commit({cube.name: ipath, cube_b.name: ipath})
    legacy = LightmapRecords._marker_info(cube)
    legacy["uvRect"] = list(rect)
    LightmapRecords._write_marker(cube, legacy)
    info_a = json.loads(cube[LightmapBaker.LIGHTMAP_INFO_PROP])
    info_b = json.loads(cube_b[LightmapBaker.LIGHTMAP_INFO_PROP])
    check(
        "a legacy uvRect is recorded on the marker",
        info_a.get("uvRect") == rect,
        f"{info_a}",
    )
    check("an unsqueezed object records no uvRect key", "uvRect" not in info_b, f"{info_b}")
    raw_r = ptk.SceneRecords.LIGHTMAPS.read_text(btk.DataNodes)
    recs_r = (json.loads(raw_r).get("objects") if raw_r else []) or []
    check(
        "manifest scaleOffset stays identity with a legacy uvRect",
        recs_r
        and all(
            r.get("scaleOffset") == [1.0, 1.0, 0.0, 0.0] and "uvRect" not in r
            for r in recs_r
        ),
        f"{recs_r}",
    )
    baker.revert([cube, cube_b])
    check(
        "a legacy uvRect commit reverts clean",
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

    # --- coplanar atlas members meet in one value at every edge they share ---
    # Twin of mayatk (test_noisy_coplanar_panels_read_one_value_along_their_edge,
    # test_a_corner_is_not_stitched, test_a_panel_lying_on_another_along_its_
    # border_is_not_stitched): every member is its own cell with its own
    # sampling noise, so however well each cell is finished, two coplanar
    # panels disagree along the edge they share and a tiled wall reads as a
    # patchwork. The pack STITCHES every open edge two members share in one
    # plane (ptk.ImgUtils.atlas_seam_pairs -> stitch_seams); a corner and a
    # decal along a border are left alone. The second panel is a LINKED
    # duplicate: the edges are read per object, through its own matrix_world.
    from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET as _LM_SET

    seam_mat = btk.create_mat("standard", name="seam_mat")
    seam_objs = []
    SEAM_W, SEAM_D = 2.0, 1.3
    SEAM_X0 = 40.0  # clear of the earlier sections' objects (a cube at the origin)

    def _seam_panel(name, corners=None, mesh=None, location=(0.0, 0.0, 0.0)):
        """A one-quad panel, its lightmap layer the unit square over it."""
        if mesh is None:
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(corners, [], [(0, 1, 2, 3)])
            for layer_name in ("UVMap", _LM_SET):
                mesh.uv_layers.new(name=layer_name).data.foreach_set(
                    "uv", [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
                )
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        btk.assign_mat(obj, seam_mat)
        seam_objs.append(obj)
        return obj

    def _flat(x0, x1, z=0.0):
        x0, x1 = SEAM_X0 + x0, SEAM_X0 + x1
        return [(x0, 0.0, z), (x1, 0.0, z), (x1, SEAM_D, z), (x0, SEAM_D, z)]

    def _seam_tile(name, size, value, noise=0.0, seed=0):
        """A uniform tile at *size*, with iid relative noise per texel."""
        w, h = size
        rgb = np.full((h, w), value, np.float32)
        if noise:
            rng = np.random.default_rng(seed)
            rgb *= (1.0 + noise * rng.standard_normal(rgb.shape)).astype(np.float32)
        buf = np.ones((h, w, 4), np.float32)
        buf[..., :3] = rgb[..., None]
        p = os.path.join(tmp_dir, name)
        img = bpy.data.images.new(name, w, h, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"
        img.pixels.foreach_set(buf.reshape(-1))
        img.filepath_raw = p
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return p

    def _seam_reader(packed):
        """``read(obj, point)``: the packed atlas at *point* through obj's own rect."""
        img = bpy.data.images.load(next(iter(packed.values()))[0])
        img.colorspace_settings.name = "Non-Color"
        buf = np.empty(len(img.pixels), np.float32)
        img.pixels.foreach_get(buf)
        lum = np.flipud(buf.reshape(img.size[1], img.size[0], img.channels))[..., 0]
        bpy.data.images.remove(img)
        h, w = lum.shape

        def read(obj, point):
            # *point* in the fixture's own frame (x from SEAM_X0).
            point = np.asarray(point, np.float64) + (SEAM_X0, 0.0, 0.0)
            uvs, pts = btk.UvUtils.get_uv_world_triangles(obj, _LM_SET)
            for uv, (a0, b0, c0) in zip(uvs, pts):
                nrm = np.cross(b0 - a0, c0 - a0)
                nn = float(nrm @ nrm)
                q = point - ((point - a0) @ nrm) / nn * nrm
                w0 = np.cross(c0 - b0, q - b0) @ nrm / nn
                w1 = np.cross(a0 - c0, q - c0) @ nrm / nn
                if min(w0, w1, 1.0 - w0 - w1) >= -1e-6:
                    u, v = w0 * uv[0] + w1 * uv[1] + (1.0 - w0 - w1) * uv[2]
                    break
            else:
                raise RuntimeError(f"{point} is not on {obj.name}")
            sx, sy, ox, oy = packed[obj.name][1]
            x = (u * sx + ox) * w - 0.5
            y = (1.0 - (v * sy + oy)) * h - 0.5
            x0, y0 = int(np.floor(x)), int(np.floor(y))
            fx, fy = x - x0, y - y0

            def tap(r, c):
                return float(lum[min(max(r, 0), h - 1), min(max(c, 0), w - 1)])

            return (
                (1 - fx) * (1 - fy) * tap(y0, x0)
                + fx * (1 - fy) * tap(y0, x0 + 1)
                + (1 - fx) * fy * tap(y0 + 1, x0)
                + fx * fy * tap(y0 + 1, x0 + 1)
            )

        return read

    def _seam_pack(objs, values, noise=0.0):
        seam_baker = LightmapBaker(resolution=64)
        bpy.context.view_layer.update()
        seam_plan = seam_baker.atlas_plan([o.name for o in objs])
        seam_sizes = seam_baker.plan_sizes(seam_plan)
        mapping = {
            o.name: _seam_tile(f"{o.name}.exr", seam_sizes[o.name], val, noise, seed=i)
            for i, (o, val) in enumerate(zip(objs, values))
        }
        return seam_baker.pack_atlas(
            mapping, output_dir=tmp_dir, suffix="_SeamLM", plan=seam_plan
        )

    seam_a = _seam_panel("seamA", _flat(0.0, SEAM_W))
    seam_b = _seam_panel("seamB", mesh=seam_a.data, location=(SEAM_W, 0.0, 0.0))
    seam_read = _seam_reader(_seam_pack([seam_a, seam_b], [1.0, 1.0], noise=0.08))
    seam_steps = []
    for y in np.linspace(0.03, 0.97, 25) * SEAM_D:
        ra = seam_read(seam_a, (SEAM_W - 1e-4, y, 0.0))
        rb = seam_read(seam_b, (SEAM_W + 1e-4, y, 0.0))
        seam_steps.append(abs(ra - rb) / ((ra + rb) / 2.0))
    check(
        "atlas: noisy coplanar members read one value along their shared edge",
        max(seam_steps) < 0.01,
        f"seam step median {100 * float(np.median(seam_steps)):.2f}% "
        f"max {100 * max(seam_steps):.2f}%",
    )

    # A wall standing on the floor's far edge (shares it, not its plane).
    seam_floor = _seam_panel("seamFloor", _flat(0.0, SEAM_W))
    seam_wall = _seam_panel(
        "seamWall",
        [
            (SEAM_X0, SEAM_D, 0.0),
            (SEAM_X0 + SEAM_W, SEAM_D, 0.0),
            (SEAM_X0 + SEAM_W, SEAM_D, SEAM_D),
            (SEAM_X0, SEAM_D, SEAM_D),
        ],
    )
    corner_read = _seam_reader(_seam_pack([seam_floor, seam_wall], [1.0, 2.0]))
    corner = [
        (
            corner_read(seam_floor, (x, SEAM_D - 1e-3, 0.0)),
            corner_read(seam_wall, (x, SEAM_D, 1e-3)),
        )
        for x in np.linspace(0.1, 0.9, 5) * SEAM_W
    ]
    check(
        "atlas: a wall meeting a floor is not stitched (an edge, not a plane)",
        all(abs(f - 1.0) < 0.02 and abs(wl - 2.0) < 0.04 for f, wl in corner),
        f"{corner}",
    )

    # A ramp folding 30 degrees up off the floor's edge: across the edge, but
    # not in its plane (the 90-degree wall above is refused by its side alone).
    cos30, sin30 = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    seam_flat = _seam_panel("seamFlat", _flat(0.0, SEAM_W))
    seam_ramp = _seam_panel(
        "seamRamp",
        [
            (SEAM_X0, SEAM_D, 0.0),
            (SEAM_X0 + SEAM_W, SEAM_D, 0.0),
            (SEAM_X0 + SEAM_W, SEAM_D + SEAM_D * cos30, SEAM_D * sin30),
            (SEAM_X0, SEAM_D + SEAM_D * cos30, SEAM_D * sin30),
        ],
    )
    fold_read = _seam_reader(_seam_pack([seam_flat, seam_ramp], [1.0, 2.0]))
    fold = [
        (
            fold_read(seam_flat, (x, SEAM_D - 1e-3, 0.0)),
            fold_read(seam_ramp, (x, SEAM_D + 1e-3 * cos30, 1e-3 * sin30)),
        )
        for x in np.linspace(0.1, 0.9, 5) * SEAM_W
    ]
    check(
        "atlas: a panel folding off another's edge is not stitched",
        all(abs(f - 1.0) < 0.02 and abs(r - 2.0) < 0.04 for f, r in fold),
        f"{fold}",
    )

    # A decal along a panel's border: the edge's LINE, both faces on one side.
    seam_base = _seam_panel("seamBase", _flat(0.0, SEAM_W))
    seam_decal = _seam_panel("seamDecal", _flat(SEAM_W / 2.0, SEAM_W))
    decal_read = _seam_reader(_seam_pack([seam_base, seam_decal], [1.0, 2.0]))
    decal = [
        (
            decal_read(seam_base, (SEAM_W - 1e-3, y, 0.0)),
            decal_read(seam_decal, (SEAM_W - 1e-3, y, 0.0)),
        )
        for y in np.linspace(0.1, 0.9, 5) * SEAM_D
    ]
    check(
        "atlas: a decal along a panel's border is not stitched",
        all(abs(bv - 1.0) < 0.02 and abs(dv - 2.0) < 0.04 for bv, dv in decal),
        f"{decal}",
    )

    # The same through a real Cycles bake: two floor panels under a soft light
    # and an occluder's penumbra, each baked with its own noise. Measured per
    # seam sample (2026-10-16, 16 samples, denoised): median 0.4-1.8%, max up
    # to 5.7% before the stitch.
    for obj in (seam_floor, seam_wall, seam_flat, seam_ramp, seam_base, seam_decal):
        obj.hide_render = True
    seam_light = bpy.data.objects.new(
        "seamLight", bpy.data.lights.new("seamLight", type="AREA")
    )
    seam_light.data.energy, seam_light.data.size = 300.0, 1.2
    bpy.context.collection.objects.link(seam_light)
    seam_light.location = (SEAM_X0 + SEAM_W + 0.5, SEAM_D * 0.6, 1.4)
    seam_occ = _seam_panel(
        "seamOccluder",
        [
            (SEAM_X0 + SEAM_W - 0.4, 0.3, 0.5),
            (SEAM_X0 + SEAM_W + 0.2, 0.3, 0.5),
            (SEAM_X0 + SEAM_W + 0.2, 0.9, 0.5),
            (SEAM_X0 + SEAM_W - 0.4, 0.9, 0.5),
        ],
    )
    seam_occ.data.materials.clear()
    seam_occ.data.materials.append(btk.create_mat("standard", name="seam_occ_mat"))
    baked_seams = LightmapBaker(resolution=128, samples=16, bounces=2).bake_atlas(
        [seam_a.name, seam_b.name], output_dir=os.path.join(tmp_dir, "seam_bake")
    )
    baked_read = _seam_reader(baked_seams)
    baked_steps = []
    for y in np.linspace(0.03, 0.97, 33) * SEAM_D:
        ra = baked_read(seam_a, (SEAM_W - 1e-4, y, 0.0))
        rb = baked_read(seam_b, (SEAM_W + 1e-4, y, 0.0))
        baked_steps.append(abs(ra - rb) / max((ra + rb) / 2.0, 1e-9))
    check(
        "atlas: a Cycles-baked pair of coplanar panels meets in one value",
        len(baked_seams) == 2 and max(baked_steps) < 0.005,
        f"seam step median {100 * float(np.median(baked_steps)):.2f}% "
        f"max {100 * max(baked_steps):.2f}%",
    )
    for obj in seam_objs + [seam_light]:
        bpy.data.objects.remove(obj, do_unlink=True)

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
    from blendertk.mat_utils.bake_sets import _LightmapBakeSet as LightmapBakeSet

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

    # --- duplicate materials: one atlas group, materials untouched ------------------
    # A duplicate material (the same textures by content) groups with the one it
    # duplicates (_atlas_keys), so they bake one atlas -- no merge, nothing reassigned.
    dup_img = bpy.data.images.new("Crate_BaseColor", 4, 4)
    dup_img.filepath_raw = os.path.join(tmp_dir, "Crate_BaseColor.png")
    dup_img.file_format = "PNG"
    dup_img.save()
    dup_objs, dup_maps = {}, {}
    for i, name in enumerate(("DupA", "DupB")):
        dup_mat = btk.create_mat("standard", name=f"{name}_mat")
        dup_mat.node_tree.nodes.new("ShaderNodeTexImage").image = dup_img
        bpy.ops.mesh.primitive_cube_add(location=(20 + 3 * i, 0, 0))
        dup_obj = bpy.context.active_object
        dup_obj.name = name
        btk.assign_mat(dup_obj, dup_mat)
        btk.create_lightmap_uvs([dup_obj])
        dup_maps[dup_obj.name] = os.path.join(tmp_dir, f"{name}_Lightmap.exr")
        open(dup_maps[dup_obj.name], "wb").close()
        dup_objs[name] = dup_obj
    LightmapBaker(resolution=64).commit_lightmap(dup_maps)
    dup_keys = LightmapBaker._atlas_keys(["DupA", "DupB"])
    check(
        "duplicate materials: one atlas group, keyed by a material of the pair, "
        "with no merge offered and each object keeping its own material",
        len(set(dup_keys.values())) == 1
        and set(dup_keys.values()) <= {"DupA_mat", "DupB_mat"}
        and not hasattr(LightmapBaker, "merge_duplicate_materials")
        and [o.material_slots[0].material.name for o in dup_objs.values()]
        == ["DupA_mat", "DupB_mat"],
        f"{dup_keys}",
    )
    LightmapBaker().revert([o.name for o in dup_objs.values()])
    for dup_obj in dup_objs.values():
        bpy.data.objects.remove(dup_obj, do_unlink=True)

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
        "empty output dir is the texture folder's lightmaps (mayatk's default)",
        _slots()._output_dir() == os.path.join(BASE, LightmapBaker.OUTPUT_SUBDIR),
    )
    _none = LightmapBakerSlots.__new__(LightmapBakerSlots)
    _none.ui = _Ui("lightmaps")
    _none._base_output_dir = lambda: ""
    check(
        "no project: the panel names no folder (the engine's default decides)",
        _none._output_dir() is None,
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
    _none_abs = LightmapBakerSlots.__new__(LightmapBakerSlots)
    _none_abs.ui = _Ui(abs_dir)
    _none_abs._base_output_dir = lambda: ""
    check(
        "no project: a typed full path is still used as-is (nothing to resolve)",
        _none_abs._output_dir() == abs_dir,
        str(_none_abs._output_dir()),
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
    btk.MatUtils.get_node_tree(w, create=True)
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
    check(
        "the baker's old lightmap_dependencies spelling is gone (removed in 0.20.0)",
        not hasattr(LightmapBaker, "lightmap_dependencies"),
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
    # 2026-10-21 audit: a Find & Move moved a map another file still reads.
    bpy.ops.mesh.primitive_cube_add()
    shared_cube = bpy.context.active_object
    shared_cube.name = "shared_cube"
    shared_src = os.path.join(tmp_dir, "shared", "shared_LightMap.exr")
    os.makedirs(os.path.dirname(shared_src), exist_ok=True)
    with open(shared_src, "wb") as fh:
        fh.write(b"exr")
    other_blend = os.path.join(tmp_dir, "shared", "other.blend")
    with open(other_blend, "wb") as fh:
        fh.write(b"BLENDER")
    dep_baker.commit({shared_cube.name: shared_src})
    LightmapRecords._save_writers(
        dict(LightmapRecords._writers(), **{"shared_lightmap.exr": other_blend})
    )
    dep_baker.relocate_lightmaps(dest, mode="move", objects=["shared_cube"])
    check(
        "relocate copies, never moves, a map another file wrote",
        os.path.isfile(shared_src)
        and os.path.isfile(os.path.join(dest, "shared_LightMap.exr")),
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

    # --- Include Scene Lighting / Include Hidden Lights (mayatk parity) -------
    # Which lights light a bake: every scene light (the default) or only the
    # Scope's own -- the rest switched off for the bake and handed back; the
    # world by its own switch, whatever the first says; and, with Include Hidden
    # Lights, the hidden ones of that set switched on -- a light whose COLLECTION
    # hides it too, without revealing what else the collection holds. The lights
    # at render time are recorded by a stand-in for the bake primitive.
    from blendertk.core_utils._core_utils import CoreUtils as _SlCore

    sl_dir = os.path.join(tmp_dir, "scene_lights")
    # The scene's other lights out of the way for this section, put back after.
    sl_prior = {
        o.name: o.hide_render for o in bpy.context.scene.objects if o.type == "LIGHT"
    }
    for _name in sl_prior:
        bpy.data.objects[_name].hide_render = True

    def _sl_parent(child, parent):
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()

    def _sl_light(name, loc, energy=500.0, parent=None):
        bpy.ops.object.light_add(type="POINT", location=loc)
        lamp = bpy.context.active_object
        lamp.name = name
        lamp.data.energy = energy
        if parent is not None:
            _sl_parent(lamp, parent)
        return lamp

    bpy.ops.object.empty_add(location=(60, 0, 0))
    sl_module = bpy.context.active_object
    sl_module.name = "sl_module"
    bpy.ops.mesh.primitive_plane_add(size=2, location=(60, 0, 0))
    sl_plane = bpy.context.active_object
    sl_plane.name = "sl_plane"
    btk.assign_mat(sl_plane, mat)
    _sl_parent(sl_plane, sl_module)
    sl_inside = _sl_light("sl_inside", (60, 0, 2), parent=sl_module)
    sl_outside = _sl_light("sl_outside", (60, 0, 2.5))
    sl_own_hidden = _sl_light("sl_own_hidden", (60, 0, 3))
    sl_own_hidden.hide_render = True
    sl_rig = bpy.data.collections.new("sl_hidden_rig")
    bpy.context.scene.collection.children.link(sl_rig)
    sl_rig_lamp = _sl_light("sl_rig_lamp", (60, 0, 3.5))
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(60, 0, 1))
    sl_rig_prop = bpy.context.active_object
    sl_rig_prop.name = "sl_rig_prop"
    for _o in (sl_rig_lamp, sl_rig_prop):
        for _c in list(_o.users_collection):
            _c.objects.unlink(_o)
        sl_rig.objects.link(_o)
    sl_rig.hide_render = True

    def _sl_rendered(obj):
        rendered = LightmapBaker._render_collections(_SlCore._active_view_layer())
        return not obj.hide_render and any(
            c.name in rendered for c in obj.users_collection
        )

    def _sl_bake(objects, fail=False, **switches):
        """bake() with the primitive recording what rendered; ``(seen, result)``."""
        baker = LightmapBaker.from_preset(
            "preview", resolution=32, samples=1, denoise=False, device="CPU", **switches
        )
        seen = {}

        def record(members, **kwargs):
            if fail:
                raise RuntimeError("bake blew up")
            seen.update(
                {
                    o.name: _sl_rendered(o)
                    for o in bpy.context.scene.objects
                    if o.type in ("LIGHT", "MESH") and o.name.startswith("sl_")
                }
            )
            seen["<world>"] = bpy.context.scene.world is not None
            return {}

        with _mock.patch.object(baker._texture_baker, "bake", side_effect=record):
            result = baker.bake(objects, packing="per_object", output_dir=sl_dir)
        return seen, result

    check(
        "the lighting switches default to every light and no hidden one",
        (LightmapBaker().include_scene_lights, LightmapBaker().include_hidden_lights)
        == (True, False),
    )
    seen, _r = _sl_bake([sl_plane])
    check(
        "every scene light lights the bake by default",
        seen.get("sl_inside") and seen.get("sl_outside"),
        seen,
    )
    seen, _r = _sl_bake([sl_module], include_scene_lights=False)
    check(
        "Include Scene Lighting off: the Scope's light lights it, one outside does not",
        seen.get("sl_inside") is True and seen.get("sl_outside") is False,
        seen,
    )
    check(
        "...and the light outside is handed back as found",
        sl_outside.hide_render is False,
    )
    seen, _r = _sl_bake([sl_plane, sl_outside], include_scene_lights=False)
    check(
        "a light handed to the bake is in its Scope",
        seen.get("sl_outside") is True and seen.get("sl_inside") is False,
        seen,
    )
    world_before = bpy.context.scene.world
    seen, _r = _sl_bake([sl_plane], include_scene_lights=False)
    seen_off, _r = _sl_bake([sl_plane], include_environment=False)
    check(
        "the world answers only to Include Environment, not to the Scope",
        world_before is None
        or (seen.get("<world>") is True and seen_off.get("<world>") is False),
        f"{seen.get('<world>')} {seen_off.get('<world>')}",
    )
    seen, _r = _sl_bake([sl_plane])
    check(
        "hidden lights stay off by default",
        seen.get("sl_own_hidden") is False and seen.get("sl_rig_lamp") is False,
        seen,
    )
    seen, _r = _sl_bake([sl_plane], include_hidden_lights=True)
    check(
        "Include Hidden Lights switches on a hidden light and a collection-hidden one",
        seen.get("sl_own_hidden") is True and seen.get("sl_rig_lamp") is True,
        seen,
    )
    check(
        "...without revealing what else the collection hides",
        seen.get("sl_rig_prop") is False,
        seen,
    )
    check(
        "...and hands both back hidden",
        sl_own_hidden.hide_render is True
        and sl_rig.hide_render is True
        and sl_rig_lamp.name not in bpy.context.scene.collection.objects
        and not _sl_rendered(sl_rig_lamp),
    )
    seen, _r = _sl_bake(
        [sl_module], include_scene_lights=False, include_hidden_lights=True
    )
    check(
        "with the master off only the Scope's hidden lights are switched on",
        seen.get("sl_own_hidden") is False and seen.get("sl_rig_lamp") is False,
        seen,
    )
    try:
        _sl_bake(
            [sl_module],
            fail=True,
            include_scene_lights=False,
            include_hidden_lights=True,
        )
    except RuntimeError:
        pass
    check(
        "a failed bake hands every light back",
        sl_outside.hide_render is False
        and sl_own_hidden.hide_render is True
        and not _sl_rendered(sl_rig_lamp),
    )

    # The refusal reads the set the bake renders with.
    sl_inside.hide_render = True
    no_env = dict(include_environment=False)
    check(
        "every Scope light hidden is refused with the master off",
        "hidden"
        in (
            LightmapBaker(include_scene_lights=False, **no_env).preflight([sl_module])
            or ""
        ),
    )
    check(
        "...not with it on (a light outside the Scope lights it)",
        LightmapBaker(**no_env).preflight([sl_module]) is None,
    )
    check(
        "...nor with Include Hidden Lights on (the bake switches it on)",
        LightmapBaker(
            include_scene_lights=False, include_hidden_lights=True, **no_env
        ).preflight([sl_module])
        is None,
    )
    sl_inside.hide_render = False

    # Presets carry both switches; one saved before they existed builds the defaults.
    sl_store = ptk.PresetStore(
        "lightmap",
        builtin_dir=LightmapBaker.preset_store().builtin_dir,
        user_dir=os.path.join(sl_dir, "presets"),
    )
    sl_store.save(
        "lightPass",
        {
            "resolution": 64,
            "include_scene_lights": False,
            "include_hidden_lights": True,
        },
    )
    sl_store.save("olderPass", {"resolution": 64, "include_environment": False})
    _real_store = LightmapBaker.__dict__["preset_store"]
    LightmapBaker.preset_store = staticmethod(lambda: sl_store)
    try:
        sl_saved = LightmapBaker.from_preset("lightPass")
        sl_older = LightmapBaker.from_preset("olderPass")
    finally:
        LightmapBaker.preset_store = _real_store
    check(
        "the lighting switches round-trip through a preset",
        {"include_scene_lights", "include_hidden_lights"}
        <= set(LightmapBaker.PRESET_BOOL_KEYS)
        and (sl_saved.include_scene_lights, sl_saved.include_hidden_lights)
        == (False, True)
        and (sl_older.include_scene_lights, sl_older.include_hidden_lights)
        == (True, False),
    )

    # A REAL bake per switch: the map changes with it. The plane alone in the
    # scene's light (the world detached), each pair one switch apart.
    def _sl_mean(objects, **switches):
        baker = LightmapBaker.from_preset(
            "preview",
            resolution=32,
            samples=8,
            denoise=False,
            device="CPU",
            include_environment=False,
            **switches,
        )
        result = baker.bake(objects, packing="per_object", output_dir=sl_dir)
        path = result.maps.get(sl_plane.name)
        return float(_rgb(path).mean()) if path and os.path.isfile(path) else None

    mean_all = _sl_mean([sl_module])
    mean_scope = _sl_mean([sl_module], include_scene_lights=False)
    mean_hidden = _sl_mean([sl_module], include_hidden_lights=True)
    lines.append(
        "OK   (measured) real Cycles bake means: every light %s, Scope only %s, "
        "hidden lights on %s" % (mean_all, mean_scope, mean_hidden)
    )
    check(
        "a real bake: leaving the light outside the Scope out darkens the map",
        mean_all is not None and mean_scope is not None and mean_scope < mean_all,
        f"{mean_scope} < {mean_all}",
    )
    check(
        "a real bake: switching the hidden lights on brightens it",
        mean_hidden is not None and mean_all is not None and mean_hidden > mean_all,
        f"{mean_hidden} > {mean_all}",
    )
    LightmapBaker().revert([sl_plane])
    for _o in (
        sl_inside,
        sl_outside,
        sl_own_hidden,
        sl_rig_lamp,
        sl_rig_prop,
        sl_plane,
        sl_module,
    ):
        bpy.data.objects.remove(_o, do_unlink=True)
    bpy.data.collections.remove(sl_rig)
    for _name, _was in sl_prior.items():
        if _name in bpy.data.objects:
            bpy.data.objects[_name].hide_render = _was

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
        btk.MatUtils.get_node_tree(pm, create=True)
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
    # The pack fallback -- a group whose assembly fails keeps its per-object maps --
    # named by claims alone, and replaced another file's same-named map that nothing
    # here claims and this file never wrote. Named by the atlas rule now
    # (LightmapRecords.strangers), as the atlas itself is. Mirror of mayatk's.
    fb_dir = os.path.join(par_dir, "fallback")
    os.makedirs(fb_dir, exist_ok=True)
    fb_theirs = os.path.join(fb_dir, "Fb_Set_Lightmap.exr")
    with open(fb_theirs, "wb") as fh:
        fh.write(b"theirs")
    fb_source = os.path.join(work, "Fb_Set_Lightmap.exr")
    with open(fb_source, "wb") as fh:
        fh.write(b"mine")
    real_pack_group = LightmapBaker.__dict__["_pack_group"]

    def _assembly_fails(self, *args, **kwargs):
        raise RuntimeError("assembly failed")

    LightmapBaker._pack_group = _assembly_fails
    try:
        fb_packed = beside.pack_atlas(
            {"fb_crate": fb_source},
            fb_dir,
            plan={"Fb_Set": [("fb_crate", [1.0, 1.0, 0.0, 0.0])]},
            claims={},
        )
    finally:
        LightmapBaker._pack_group = real_pack_group
    with open(fb_theirs, "rb") as fh:
        fb_untouched = fh.read() == b"theirs"
    check(
        "a failed group's map never lands on another file's map of its name",
        fb_untouched
        and os.path.basename(fb_packed.get("fb_crate", ("",))[0])
        == "Fb_Set_Lightmap_1.exr",
        f"{fb_packed}",
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
        "reflection_probe": False,
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
        and saved.beside_textures is True
        and saved.reflection_probe is False,
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
            """A field's option box: its toggles, the first found by type and
            all of them by ``get_options``, in the order they were added (the
            Scope field carries three)."""

            def __init__(self, on=None):
                self.toggles = [] if on is None else [_Toggle(on)]
                self.wired = None
                self.wired_all = []
                self.actions = []

            @property
            def toggle(self):
                return self.toggles[0] if self.toggles else None

            @toggle.setter
            def toggle(self, value):
                self.toggles = [] if value is None else [value]

            def find_option(self, _option_type):
                return self.toggle

            def get_options(self):
                return list(self.toggles)

            def set_toggle(self, *, initial=True, replace=True, **kwargs):
                if replace:
                    self.toggles, self.wired_all = [], []
                self.toggles.append(_Toggle(initial))
                self.wired = dict(kwargs, initial=initial)
                self.wired_all.append(self.wired)
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

            def setText(self, text, level=None):
                self._text, self.level = text, level

            def text(self):
                return self._text

            def progress(self, total=None, text=""):
                return _Progress()

        class _Label(_Footer):
            pass

        class _RunButton:
            """The shared run button: what it says, and whether it may run."""

            def __init__(self):
                self._text, self.enabled = "Bake Lightmaps", True

            def text(self):
                return self._text

            def setText(self, text):
                self._text = text

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

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
            scene_lights=True,
            hidden_lights=False,
            probe=True,
            page="Bake",
        ):
            """A panel over stub widgets, each wired by its own ``_init``."""
            p = LightmapBakerSlots.__new__(LightmapBakerSlots)
            ui = SimpleNamespace(
                # The panel's destroyed signal, which a window's listeners follow.
                destroyed=SimpleNamespace(connect=lambda *_a: None),
                cmb_scope=_Combo(),
                cmb002=_Combo(),
                cmb_device=_Combo(),
                cmb_resolution=_Combo(),
                spn_samples=_Spin(samples),
                spn_bounces=_Spin(bounces),
                txt_output_dir=_Line(placeholder="sourceimages"),
                txt000=_Line("_Lightmap", "_Lightmap"),
                lbl_exclude=_Label(),
                lbl_isolate=_Label(),
                footer=_Footer(),
                cmb_page=_Combo(),
                stk_pages=SimpleNamespace(setCurrentIndex=lambda index: None),
                b000=_RunButton(),
            )
            p.ui = ui
            p._baker = None
            p._last_output_dir = None
            p._map_rows = {}
            p._fill_maps = lambda rows, report: None  # no Qt table here
            p._highlight_maps = lambda names: None
            p.cmb_page_init(ui.cmb_page)
            ui.cmb_page.setCurrentIndex(LightmapBakerSlots._PAGES.index(page))
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
                ("include_scene_lights", scene_lights),
                ("include_environment", environment),
                ("include_hidden_lights", hidden_lights),
                ("adaptive", adaptive),
                ("denoise", denoise),
                ("beside_textures", beside),
                ("reflection_probe", probe),
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
            and [
                w["settings_key"] for w in fresh_panel.ui.cmb_scope.option_box.wired_all
            ]
            == [
                "lightmap_baker_include_scene_lights",
                "lightmap_baker_include_environment",
                "lightmap_baker_include_hidden_lights",
            ],
        )
        _scope_wired = fresh_panel.ui.cmb_scope.option_box.wired_all
        _icons = os.path.join(os.path.dirname(__import__("uitk").__file__), "icons")
        check(
            "the Scope carries Include Scene Lighting, Include Environment and "
            "Include Hidden Lights, in that order (on, on, off), each read from "
            "its own button, every icon one uitk ships",
            [w["initial"] for w in _scope_wired] == [True, True, False]
            and (
                fresh_panel._include_scene_lights(),
                fresh_panel._include_environment(),
                fresh_panel._include_hidden_lights(),
            )
            == (True, True, False)
            and all(
                os.path.isfile(os.path.join(_icons, f"{w[k]}.svg"))
                for w in _scope_wired
                for k in ("icon", "icon_off")
                if w.get(k)
            ),
            f"{_scope_wired}",
        )
        flipped = _panel(scene_lights=False, hidden_lights=True)
        check(
            "...and flipping one never reads as another",
            (
                flipped._include_scene_lights(),
                flipped._include_environment(),
                flipped._include_hidden_lights(),
            )
            == (False, True, True),
        )
        check(
            "the switches ship on, Beside Material Textures off",
            (
                fresh_panel._adaptive(),
                fresh_panel._denoise(),
                fresh_panel._beside_textures(),
                fresh_panel._reflection_probe(),
            )
            == (True, True, False, True),
        )
        check(
            "the Packing field carries the Reflection Probe camera switch",
            fresh_panel.ui.cmb002.option_box.wired.get("settings_key")
            == "lightmap_baker_reflection_probe"
            and fresh_panel.ui.cmb002.option_box.wired.get("icon") == "camera"
            and os.path.isfile(
                os.path.join(
                    os.path.dirname(__import__("uitk").__file__),
                    "icons",
                    "camera.svg",
                )
            )
            and _panel(probe=False)._reflection_probe() is False,
            fresh_panel.ui.cmb002.option_box.wired,
        )
        check(
            "the help card links mayatk's reference, which covers both twins",
            LightmapBakerSlots.HELP_URL.endswith(
                "/mayatk/blob/main/docs/lightmap_baker.md"
            ),
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
            scene_lights=False,
            hidden_lights=True,
            probe=False,
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
                "include_scene_lights": False,
                "include_environment": False,
                "include_hidden_lights": True,
                "adaptive": False,
                "denoise": False,
                "beside_textures": True,
                "reflection_probe": False,
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
        keep = _panel(
            environment=False,
            adaptive=False,
            denoise=False,
            beside=True,
            scene_lights=False,
            hidden_lights=True,
            probe=False,
        )
        keep._apply_preset_values(store.load("desktop"))
        kept_values = keep._preset_values()
        check(
            "a shipped tier moves the dials, bounces included, and leaves the switches",
            (kept_values["resolution"], kept_values["samples"], kept_values["bounces"])
            == (2048, 2048, 6)
            and (
                kept_values["include_scene_lights"],
                kept_values["include_environment"],
                kept_values["include_hidden_lights"],
                kept_values["adaptive"],
                kept_values["denoise"],
                kept_values["beside_textures"],
                kept_values["reflection_probe"],
            )
            == (False, False, True, False, False, True, False),
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
            == "(blank: beside textures, else sourceimages/lightmaps)"
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
                "cmb_page",
                "stk_pages",
                "verticalSpacer",
                "footer",
            ],
            f"{_layout_items('main_layout')}",
        )
        check(
            "the Isolate row mirrors the Exclude row: a label and one button",
            _layout_items("isolate_layout") == ["lbl_isolate", "set_isolation"],
            f"{_layout_items('isolate_layout')}",
        )
        check(
            "the Bake page reads top to bottom as mayatk's does",
            _layout_items("bake_layout")
            == [
                "cmb_scope",
                "exclude_layout",
                "isolate_layout",
                "cmb002",
                "cmb_device",
                "quality_group",
                "output_group",
                "grp_process",
            ],
            f"{_layout_items('bake_layout')}",
        )
        check(
            "the Manage page is its table: its verbs are the table menu's and the "
            "header menu's",
            _layout_items("manage_layout") == ["tbl_maps"],
            f"{_layout_items('manage_layout')}",
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

            # The REAL Scope-lights read, as the real exclusion filter below.
            _scope_lights = staticmethod(LightmapBaker._scope_lights)

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.calls = []
                _FakeWorkflow.instances.append(self)

            def bake(self, objects, packing="atlas", output_dir=None, **kwargs):
                from blendertk.light_utils.lightmap_baker.lightmap_baker import (
                    LightmapBakeResult,
                )

                self.bake_objects = list(objects)
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
                scene_lights=False,
                hidden_lights=True,
                probe=False,
            )
            run._output_dir = lambda: par_dir
            run.b000()
            wf_fake = _FakeWorkflow.instances[0]
            check(
                "b000 carries Bounces, Adaptive, Beside, the three lighting "
                "switches, Denoise, the Reflection Probe and the Processor to "
                "the bake",
                {
                    k: wf_fake.kwargs.get(k)
                    for k in (
                        "bounces",
                        "adaptive",
                        "beside_textures",
                        "include_environment",
                        "include_scene_lights",
                        "include_hidden_lights",
                        "denoise",
                        "reflection_probe",
                        "device",
                    )
                }
                == {
                    "bounces": 6,
                    "adaptive": False,
                    "beside_textures": True,
                    "include_environment": False,
                    "include_scene_lights": False,
                    "include_hidden_lights": True,
                    "denoise": False,
                    "reflection_probe": False,
                    "device": "CPU",
                },
                f"{wf_fake.kwargs}",
            )
            # The Scope's lights ride beside its meshes: what lights the bake
            # with Include Scene Lighting off (the targets stay the meshes).
            bpy.ops.object.light_add(type="POINT", location=(0, 0, 3))
            ex_lamp = bpy.context.active_object
            ex_lamp.name = "ex_lamp"
            bpy.ops.object.light_add(type="POINT", location=(0, 0, 4))
            other_lamp = bpy.context.active_object
            other_lamp.name = "ex_other_lamp"
            bpy.ops.object.select_all(action="DESELECT")
            ex_a.select_set(True)
            ex_lamp.select_set(True)
            run.b000()
            handed = {
                getattr(o, "name", o) for o in _FakeWorkflow.instances[-1].bake_objects
            }
            check(
                "b000 hands the bake the lights its Scope holds, and only those",
                "ex_lamp" in handed and "ex_other_lamp" not in handed,
                f"{sorted(handed)}",
            )
            scene_run = _panel(scope="Scene", scene_lights=False)
            scene_run._output_dir = lambda: par_dir
            scene_run.b000()
            handed = {
                getattr(o, "name", o) for o in _FakeWorkflow.instances[-1].bake_objects
            }
            check(
                "...every scene light under the Scene scope",
                {"ex_lamp", "ex_other_lamp"} <= handed,
                f"{sorted(handed)}",
            )
            for _o in (ex_lamp, other_lamp):
                bpy.data.objects.remove(_o, do_unlink=True)
            _FakeWorkflow.instances = _FakeWorkflow.instances[:1]
            bpy.ops.object.select_all(action="DESELECT")
            for obj in (ex_a, ex_b):
                obj.select_set(True)
            run.b000()
            wf_fake = _FakeWorkflow.instances[-1]
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
            rv.revert_selected()
            check(
                "Revert Selected asks first, and Cancel reverts nothing",
                len(rv.sb.asked) == 1
                and "1 selected object" in rv.sb.asked[0][0]
                and rv.sb.asked[0][1] == ("Ok", "Cancel")
                and "revert" not in [c[0] for c in rv._baker.calls]
                and "cancelled" in rv.ui.footer.text(),
                f"{rv.sb.asked} {rv._baker.calls}",
            )
            rv.sb = _Sb("Ok")
            rv._baker = None
            rv.revert_selected()
            check(
                "...and once confirmed it reverts the selection",
                rv._baker.calls
                == [("baked_objects", ("ex_a",)), ("revert", ("ex_a",))],
                f"{rv._baker.calls}",
            )
            bpy.ops.object.select_all(action="DESELECT")
            rv._baker = None
            rv.sb = _Sb("Ok")
            rv.revert_selected()
            check(
                "...with nothing selected it asks nothing and says to select "
                "(an empty selection is not every baked object)",
                rv.sb.asked == []
                and "revert" not in [c[0] for c in rv._baker.calls]
                and "select their objects" in rv.ui.footer.text(),
                (rv.sb.asked, rv.ui.footer.text()),
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

        # The Isolate row: the Exclude row's twin over its own set (mirror of
        # mayatk's). A door brings the glass under it: they move together.
        from blendertk.mat_utils.bake_sets import LightmapIsolateSet

        iso_row = _panel()
        iso_row_door = _par_cube("iso_row_door", (140, 0, 0), size=1.0)
        iso_row_glass = _par_cube("iso_row_glass", (140, 1, 0), size=1.0)
        iso_row_glass.parent = iso_row_door
        bpy.ops.object.select_all(action="DESELECT")
        iso_row_door.select_set(True)
        iso_row.set_isolation()
        check(
            "the Isolate row stores the selection and counts the meshes under it",
            iso_row.ui.lbl_isolate.text() == "Isolate (2):"
            and "2 meshes isolated" in iso_row.ui.footer.text()
            and iso_row.ui.lbl_exclude.text() == "",
            f"{iso_row.ui.lbl_isolate.text()} | {iso_row.ui.footer.text()} | "
            f"{iso_row.ui.lbl_exclude.text()!r}",
        )
        bpy.ops.object.select_all(action="DESELECT")
        iso_row.select_isolation()
        check(
            "...Select selects its members",
            [o.name for o in CoreUtils.selected_objects()] == ["iso_row_door"],
            f"{[o.name for o in CoreUtils.selected_objects()]}",
        )
        iso_row.clear_isolation()
        check(
            "...and Clear removes the set and resets the label",
            not LightmapIsolateSet.exists()
            and iso_row.ui.lbl_isolate.text() == "Isolate:"
            and "one pass" in iso_row.ui.footer.text(),
            f"{iso_row.ui.lbl_isolate.text()} | {iso_row.ui.footer.text()}",
        )
        for obj in (iso_row_glass, iso_row_door):
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)

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

    # --- every entry names its node's path from the root (2026-10-20) ---------
    # The record's shape (ptk.LightmapRecord) requires each object's
    # ``hierarchy``, its parent chain root first -- what tells apart objects
    # that share a name once an import renames -- and the GLB build's reader
    # refuses a record without it (MeshConvert._current_record). One shape for
    # every host, mayatk's DAG path included.
    hy_room = bpy.data.objects.new("hyRoom", None)
    bpy.context.scene.collection.objects.link(hy_room)
    bpy.ops.mesh.primitive_cube_add()
    hy_crate = bpy.context.active_object
    hy_crate.name = "hyCrate"
    hy_crate.parent = hy_room
    hy_map = os.path.join(tmp_dir, "hyCrate_LightMap.exr")
    open(hy_map, "wb").close()
    LightmapRecords.commit({hy_crate.name: hy_map})
    hy_payload = LightmapRecords._record().payload
    hy_entry = next(
        (e for e in hy_payload["objects"] if e.get("name") == "hyCrate"), {}
    )
    check(
        "a record entry names its object's path from the root, its own name last",
        hy_entry.get("hierarchy") == ["hyRoom", "hyCrate"],
        f"{hy_entry}",
    )
    hy_problems = ptk.SceneRecords.problems(ptk.SceneRecords.LIGHTMAPS, hy_payload)
    check(
        "...and the record is one its declaration reads (SceneRecords.problems)",
        hy_problems == [],
        f"{hy_problems}",
    )
    LightmapRecords.revert()
    bpy.data.objects.remove(hy_crate)
    bpy.data.objects.remove(hy_room)

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

    def _saved():
        """What a save does first (``save_pre``): the maps written since the
        last save go home and the superseded ones are set aside
        (``ptk.PendingFiles.saving``) -- the .blend itself not written."""
        ptk.PendingFiles.session().saving(
            bpy.data.filepath, saved_from=bpy.data.filepath
        )

    def _sup_bake(*args, **kwargs):
        """A bake, then :func:`_saved`: these checks are about where the maps
        end up once the file keeps them -- its maps named by their homes."""
        result = sup_baker.bake(*args, **kwargs)
        _saved()
        result.maps = {n: LightmapRecords._home(p) for n, p in result.maps.items()}
        if result.probe:
            result.probe = LightmapRecords._home(result.probe)
        return result

    first = _sup_bake([sup_cube], packing="per_object", output_dir=sup_dir)
    old = first.maps.get(sup_cube.name, "")
    second = _sup_bake(
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
    # away -- the Recycle Bin, else the "superseded" temp store, kept a month
    # (pinned) -- never a _superseded folder left in the project (2026-10-13).
    import glob as _glob

    _found = _glob.glob(
        os.path.join(
            ptk.TempArtifacts.root(),
            ptk.FileDependencies.SUPERSEDED_STORE,
            "*",
            os.path.basename(old),
        )
    )
    check(
        "...set aside into the temp store, never deleted, nothing left beside it",
        bool(_found)
        and not os.path.exists(
            os.path.join(os.path.dirname(old), ptk.FileDependencies.SUPERSEDED_DIR)
        ),
        _found,
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
        third = _sup_bake(
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
    _far = _sup_bake(
        [sup_cube], packing="per_object", output_dir=outside_dir, suffix="_Far"
    ).maps.get(sup_cube.name, "")
    _back = _sup_bake([sup_cube], packing="per_object", output_dir=sup_dir)
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
    _saved()
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
    _saved()
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
    # BACKLOG 2026-10-21 (S2, ported from mayatk): once the copy's markers name
    # only its own maps, the writer record alone keeps a later bake off the
    # source's name -- pruned, a bake back under it wrote over the file.
    _src_key = os.path.basename(written).lower()
    LightmapRecords._prune_folder_hints()
    check(
        "a copy never takes back a name its source wrote",
        _src_key in LightmapRecords._writers()
        and LightmapRecords.FOREIGN
        in LightmapRecords.claims().get(_src_key, frozenset())
        and os.path.normcase(
            ptk.FileUtils.unique_path(
                sup_dir,
                "Source_Lightmap",
                ".exr",
                claims=LightmapRecords.claims(),
                owners=[sup_cube.name],
            )
        )
        != os.path.normcase(written),
        f"{LightmapRecords._writers()}",
    )

    copy_map = LightmapRecords._marker_info(sup_cube)["map"]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(sup_dir, "renamed.blend"))
    os.remove(os.path.join(sup_dir, "copy.blend"))
    with LightmapRecords.superseding([sup_cube.name]):
        LightmapRecords.commit(
            {sup_cube.name: _exr(os.path.join(sup_dir, "Renamed_Lightmap.exr"))}
        )
    _saved()
    check(
        "a renamed file's own maps are still its own",
        not os.path.isfile(os.path.join(sup_dir, copy_map)),
    )
    # 2026-10-21: a stamp naming a file that is gone read as this file's own
    # only while nothing was there; a file back at the old name then owned the
    # map. A save writes the ownership down.
    _orphans = dict(LightmapRecords._writers(), **{"orphan.exr": "copy.blend"})
    LightmapRecords._save_writers(_orphans)
    bpy.ops.wm.save_mainfile()
    with open(os.path.join(sup_dir, "copy.blend"), "wb") as _fh:
        _fh.write(b"BLENDER")  # the old name is back
    check(
        "a save gives a gone file's maps to the file saved, for good",
        LightmapRecords._writers().get("orphan.exr") == "renamed.blend"
        and LightmapRecords.foreign_writer("orphan.exr") is None,
        f"{LightmapRecords._writers()}",
    )
    os.remove(os.path.join(sup_dir, "copy.blend"))

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

    # --- the Isolate set: a moving part bakes in a pass of its own ---------
    # Mirror of mayatk's (production 2026-10-15: a locker door left a
    # door-shaped shadow on its cabinet's map). Each member -- with the meshes
    # under it -- bakes alone, the static scene in its render and every other
    # isolated part out, and is out of every other pass's render.
    from blendertk.mat_utils.bake_sets import LightmapIsolateSet

    def _iso_cube(name, location, parent=None):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
        obj = bpy.context.active_object
        obj.name = name
        obj.parent = parent
        return obj

    iso_cabinet = _iso_cube("iso_cabinet", (0, 0, 0))
    iso_door = _iso_cube("iso_door", (2, 0, 0))
    iso_glass = _iso_cube("iso_glass", (2, 1, 0), parent=iso_door)
    iso_lid = _iso_cube("iso_lid", (4, 0, 0))
    iso_mat = btk.create_mat("standard", name="iso_shared_mat")
    for obj in (iso_cabinet, iso_door, iso_glass, iso_lid):
        btk.assign_mat(obj, iso_mat)
    # The glass stored beside its door counts with the door, the outermost.
    LightmapIsolateSet.define([iso_lid, iso_glass, iso_door])
    iso_groups = sorted(sorted(o.name for o in g) for g in LightmapIsolateSet.groups())
    check(
        "Isolate groups keep each member with the meshes under it",
        iso_groups == [["iso_door", "iso_glass"], ["iso_lid"]],
        iso_groups,
    )
    check(
        "...and its meshes are the groups flattened",
        sorted(o.name for o in LightmapIsolateSet.meshes())
        == ["iso_door", "iso_glass", "iso_lid"],
        [o.name for o in LightmapIsolateSet.meshes()],
    )

    iso_dir = os.path.join(tmp_dir, "isolate")
    iso_passes = []

    def iso_render(_self, meshes, **kw):
        """One pass as TextureBaker bakes one: names apart within the call only."""
        hidden = sorted(
            o.name
            for o in bpy.context.scene.objects
            if o.type == "MESH" and o.hide_render
        )
        iso_passes.append(([m.name for m in meshes], hidden))
        progress = kw.get("on_progress")
        used, out = set(), {}
        for i, m in enumerate(meshes):
            if progress and progress(i, len(meshes), m.name) is False:
                break
            # One material, one stem: the collision a later pass must avoid.
            out[m.name] = ptk.FileUtils.unique_path(
                iso_dir,
                "iso_shared_Lightmap",
                ".exr",
                used,
                claims=kw.get("claims"),
                owners=(m.name,),
            )
        if progress:
            progress(len(meshes), len(meshes), "")
        return out

    def _iso_bake(objects, on_progress=None):
        iso_passes.clear()
        with _mock.patch.object(TextureBaker, "bake", iso_render):
            return LightmapBaker(resolution=32)._bake(
                objects,
                output_dir=iso_dir,
                create_uvs=False,
                heal=False,
                on_progress=on_progress,
                claims=LightmapRecords.claims(),
            )

    iso_ticks = []
    iso_maps = _iso_bake(
        [iso_cabinet, iso_door, iso_lid],
        on_progress=lambda d, t, n: iso_ticks.append((d, t)) or True,
    )
    check(
        "each isolated member bakes alone, out of the others' render",
        iso_passes
        == [
            (["iso_cabinet"], ["iso_door", "iso_glass", "iso_lid"]),
            (["iso_door"], ["iso_lid"]),
            (["iso_lid"], ["iso_door", "iso_glass"]),
        ],
        iso_passes,
    )
    check(
        "...every mesh is handed back as found",
        not any(o.hide_render for o in bpy.context.scene.objects),
        [o.name for o in bpy.context.scene.objects if o.hide_render],
    )
    check(
        "...a pass names its maps apart from the earlier passes' (one stem)",
        sorted(iso_maps) == ["iso_cabinet", "iso_door", "iso_lid"]
        and len({os.path.normcase(p) for p in iso_maps.values()}) == 3,
        iso_maps,
    )
    check(
        "...and progress runs once across the passes",
        iso_ticks[-1] == (3, 3)
        and all(t == 3 for _d, t in iso_ticks)
        and [d for d, _t in iso_ticks] == sorted(d for d, _t in iso_ticks),
        iso_ticks,
    )

    _iso_bake(
        [iso_cabinet, iso_door, iso_lid],
        on_progress=lambda d, t, n: n != "iso_door",
    )
    check(
        "a cancel stops the later passes",
        [names for names, _hidden in iso_passes] == [["iso_cabinet"], ["iso_door"]],
        iso_passes,
    )

    iso_packed = {}

    def iso_pack(_self, maps, plan=None, **_kw):
        iso_packed.update(plan or {})
        return {}

    iso_passes.clear()
    with (
        _mock.patch.object(TextureBaker, "bake", iso_render),
        _mock.patch.object(LightmapBaker, "pack_atlas", iso_pack),
    ):
        LightmapBaker(resolution=64).bake_atlas(
            [iso_cabinet, iso_door], output_dir=iso_dir
        )
    check(
        "an atlas bake isolates the same way",
        [names for names, _hidden in iso_passes] == [["iso_cabinet"], ["iso_door"]]
        and iso_passes[0][1] == ["iso_door", "iso_glass", "iso_lid"]
        and bool(iso_packed),
        iso_passes,
    )

    # A map recorded earlier makes the door "baked before" -- static to the
    # mover rule; the Isolate set must still take it out.
    LightmapRecords.commit({iso_door.name: os.path.join(iso_dir, "iso_door_LM.exr")})
    _iso_bake([iso_cabinet])
    check(
        "a member baked before stays out of a later bake's render",
        iso_passes == [(["iso_cabinet"], ["iso_door", "iso_glass", "iso_lid"])],
        iso_passes,
    )
    LightmapRecords.revert()

    LightmapIsolateSet.clear()
    check("no Isolate set, no groups", LightmapIsolateSet.groups() == [])
    _iso_bake([iso_cabinet, iso_door])
    # The glass is a mesh of its own again: unbaked, it moves.
    check(
        "no isolated target is one pass, as before",
        iso_passes == [(["iso_cabinet", "iso_door"], ["iso_glass", "iso_lid"])],
        iso_passes,
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- ProbePlacement: where the reflection probe stands -----------------
    # Mirror of mayatk's TestProbePlacement, scene for scene, in Blender's own
    # units (metres) and axes (Z up). The placement lives once in pythontk
    # (ptk.ProbePlacement); these prove Blender's rays (BVH, two-sided), bounds
    # and lights feed it as Maya's do. Each scene is one the first rule put the
    # probe where it could not see the room: inside a car, a column, a dark
    # cabinet, an open cupboard; 6 m up a hall; a courtyard read as distant.
    import bmesh

    from blendertk.light_utils.lightmap_baker._probe_placement import (
        ProbePlacement,
    )

    def pp_reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        # No sky unless a scene adds one, as mayatk's scenes have no dome.
        bpy.context.scene.world = None

    def pp_box(name, size, at, inward=False, extra=()):
        """A box mesh object in world space (several boxes in one mesh with
        *extra* ``[(size, at)]``); *inward* turns its faces in, as a room a
        runtime draws."""
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        for (sx, sy, sz), (x, y, z) in [(size, at), *extra]:
            for v in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
                v.co.x, v.co.y, v.co.z = (
                    v.co.x * sx + x,
                    v.co.y * sy + y,
                    v.co.z * sz + z,
                )
        if inward:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def pp_plane(name, w, d, at=(0.0, 0.0, 0.0)):
        mesh = bpy.data.meshes.new(name)
        x, y, z = at
        mesh.from_pydata(
            [
                (x - w / 2, y - d / 2, z),
                (x + w / 2, y - d / 2, z),
                (x + w / 2, y + d / 2, z),
                (x - w / 2, y + d / 2, z),
            ],
            [],
            [(0, 1, 2, 3)],
        )
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def pp_light(kind, at=(0.0, 0.0, 0.0), name="ppLight"):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, kind))
        light.location = at
        bpy.context.scene.collection.objects.link(light)
        return light

    def pp_site(baked, unbaked=()):
        return ProbePlacement(baked, unbaked).place()

    def pp_box_is(box, want, delta=0.01):
        """*box* matches *want*; ``None`` is an open face, pushed out of reach."""
        if box is None:
            return False
        for corner, wanted in zip(box, want):
            for got, value in zip(corner, wanted):
                if value is None:
                    if abs(got) < 500.0:
                        return False
                elif abs(got - value) > delta:
                    return False
        return True

    def pp_clear_of(position, obj, by=0.3):
        """*position* is at least *by* outside *obj*'s world bounds."""
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        pts = [ev.matrix_world @ v.co for v in ev.data.vertices]
        lo = [min(p[a] for p in pts) for a in range(3)]
        hi = [max(p[a] for p in pts) for a in range(3)]
        gap = [max(l - c, 0.0, c - h) for c, l, h in zip(position, lo, hi)]
        return sum(g * g for g in gap) ** 0.5 >= by - 0.005

    def pp_near(got, want, delta=0.01):
        return all(abs(g - w) <= delta for g, w in zip(got, want))

    pp_reset()
    ground = pp_plane("ppGround", 40, 40)
    car = pp_box("ppCar", (4.5, 1.8, 1.4), (0, 0, 0.7))
    pp_light("POINT", (0, 0, 8))
    site = pp_site([ground, car])
    check(
        "probe: a solid at the bake's middle is flown over, never entered",
        site is not None
        and pp_near(site.position, (0.0, 0.0, 2.0))
        and pp_clear_of(site.position, car)
        and pp_box_is(site.box, [[None, None, 0.0], [None, None, None]])
        and site.open_faces == 5
        and site.hide == [],
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (10, 10, 4), (0, 0, 2), inward=True)
    column = pp_box("ppColumn", (0.6, 0.6, 4), (0, 0, 2))
    pp_light("POINT", (3, -3, 3.5))
    site = pp_site([room, column])
    check(
        "probe: a column at the room's middle is stepped around",
        site is not None
        and abs(site.position[2] - 2.0) < 0.01
        and pp_clear_of(site.position, column, by=0.5)
        and abs(site.position[0]) + abs(site.position[1]) < 2.0
        and any("ppColumn" in note for note in site.notes)
        and pp_box_is(site.box, [[-5, -5, 0], [5, 5, 4]]),
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (10, 10, 4), (0, 0, 2), inward=True)
    cabinet = pp_box("ppCabinet", (1, 1, 1), (0, 0, 2), inward=True)
    pp_light("POINT", (3, -3, 3.5))
    site = pp_site([room, cabinet])
    check(
        "probe: a dark cavity at the bake's middle is left for the lit room",
        site is not None
        and pp_clear_of(site.position, cabinet)
        and pp_box_is(site.box, [[-5, -5, 0], [5, 5, 4]]),
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (6, 6, 3), (0, 0, 1.5))
    pp_light("POINT", (0, 0, 2.5))
    site = pp_site([room])
    check(
        "probe: a lit room built inside out is still a room",
        site is not None
        and pp_near(site.position, (0.0, 0.0, 1.5))
        and site.notes == []
        and pp_box_is(site.box, [[-3, -3, 0], [3, 3, 3]]),
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (10, 10, 4), (0, 0, 2), inward=True)
    cabinet = pp_box(
        "ppCabinet",
        (1.2, 0.03, 2.15),
        (0, 0.435, 1.075),  # the back; the front (-Y) is open
        extra=[
            ((0.03, 0.9, 2.15), (-0.585, 0, 1.075)),
            ((0.03, 0.9, 2.15), (0.585, 0, 1.075)),
            ((1.2, 0.9, 0.03), (0, 0, 2.135)),
            ((1.2, 0.9, 0.03), (0, 0, 0.015)),
        ],
    )
    part = pp_box("ppPart", (0.2, 0.2, 0.2), (0, 0, 1.0))
    pp_light("POINT", (0, -4.5, 3.0))  # in view through the opening
    site = pp_site([room, cabinet], [part])
    check(
        "probe: an open cupboard over what moves is left through its opening",
        site is not None
        and site.reason == "over what moves"
        and pp_clear_of(site.position, cabinet)
        and site.position[1] < -0.45
        and any("ppCabinet" in note for note in site.notes),
        site,
    )

    pp_reset()
    hall = pp_box("ppHall", (20, 20, 12), (0, 0, 6), inward=True)
    table = pp_box("ppTable", (2.4, 1.0, 0.9), (2, -1, 0.45))
    pp_light("POINT", (0, 0, 11))
    site = pp_site([hall, table])
    check(
        "probe: a tall hall keeps the probe at eye level",
        site is not None
        and abs(site.position[2] - 2.0) < 0.01
        and pp_box_is(site.box, [[-10, -10, 0], [10, 10, 12]]),
        site,
    )

    pp_reset()
    floor = pp_plane("ppFloor", 12, 12)
    walls = [
        pp_box("ppWallN", (12, 0.2, 4), (0, 6, 2)),
        pp_box("ppWallS", (12, 0.2, 4), (0, -6, 2)),
        pp_box("ppWallE", (0.2, 12, 4), (6, 0, 2)),
        pp_box("ppWallW", (0.2, 12, 4), (-6, 0, 2)),
    ]
    pp_light("SUN", (0, 0, 10))
    site = pp_site([floor] + walls)
    check(
        "probe: a courtyard reads its sky as distant and projects its walls",
        site is not None
        and site.open_faces == 1
        and pp_box_is(site.box, [[-5.9, -5.9, 0], [5.9, 5.9, None]])
        and abs(site.position[2] - 2.0) < 0.01,
        site,
    )

    pp_reset()
    floor = pp_plane("ppFloor", 10, 10)
    faces = ProbePlacement([floor]).faces([0.0, 0.0, 1.0])
    site = pp_site([floor])
    check(
        "probe: a bare ground projects onto the ground alone",
        [[c is None for c in corner] for corner in faces]
        == [[True, True, False], [True, True, True]]
        and abs(faces[0][2]) < 0.001
        and site is not None
        and pp_box_is(site.box, [[None, None, 0.0], [None, None, None]])
        and abs(site.position[2] - 2.0) < 0.01,
        (faces, site),
    )

    pp_reset()
    decal = pp_plane("ppDecal", 0.1, 0.1, (0, 0, 5))
    site = pp_site([decal])
    check(
        "probe: a lone object in empty space is read as distant",
        site is not None and site.box is None and site.open_faces == 6,
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (10, 10, 4), (0, 0, 2), inward=True)
    shelf = pp_box("ppShelf", (2, 1, 0.1), (-3, 1.5, 2.15))
    prop = pp_box("ppProp", (0.2, 0.2, 0.2), (-3, 1.5, 1.0))
    pp_light("POINT", (3, -3, 3.5))
    site = pp_site([room, shelf], [prop])
    check(
        "probe: a shelf over what moves is kept clear of",
        site is not None
        and site.reason == "over what moves"
        and pp_clear_of(site.position, shelf)
        and abs(site.position[0] + 3.0) < 0.01
        and pp_box_is(site.box, [[-5, -5, 0], [5, 5, 4]]),
        site,
    )

    pp_reset()
    building = pp_box("ppBuilding", (8, 8, 6), (0, 0, 3))
    _dome_mesh = bpy.data.meshes.new("ppDome")
    _bm = bmesh.new()
    bmesh.ops.create_uvsphere(_bm, u_segments=20, v_segments=20, radius=50.0)
    _bm.to_mesh(_dome_mesh)
    _bm.free()
    dome = bpy.data.objects.new("ppDome", _dome_mesh)
    bpy.context.scene.collection.objects.link(dome)
    ground = pp_plane("ppGround", 200, 200)
    prop = pp_box("ppProp", (0.2, 0.2, 0.2), (7, 0, 0.5))
    pp_light("SUN", (0, 0, 10))
    site = pp_site([building], [dome, ground, prop])
    check(
        "probe: what surrounds the bake stays in view and what moves does not",
        site is not None
        and site.hide == ["ppProp"]
        and site.reason == "over what moves"
        and site.box is not None
        and abs(site.box[0][2]) < 0.01
        and pp_clear_of(site.position, building),
        site,
    )

    pp_reset()
    room = pp_box("ppRoom", (8, 8, 3), (0, 0, 1.5), inward=True)
    room.rotation_euler = (0.0, 0.0, math.radians(45))
    pp_light("POINT", (0, 0, 2.5))
    site = pp_site([room])
    half = 4 * 2**0.5
    check(
        "probe: a turned room projects onto its whole extent",
        site is not None
        and pp_box_is(site.box, [[-half, -half, 0], [half, half, 3]], delta=0.02),
        site,
    )

    pp_reset()
    bpy.context.scene.unit_settings.scale_length = 0.01  # a unit is a centimetre
    room = pp_box("ppRoom", (1000, 1000, 400), (0, 0, 200), inward=True)
    prop = pp_box("ppProp", (20, 20, 20), (-300, 150, 100))
    pp_light("POINT", (300, -300, 350))
    site = pp_site([room], [prop])
    check(
        "probe: a room in centimetres measures in centimetres",
        site is not None
        and pp_near(site.position, (-300.0, 150.0, 200.0), delta=1.0)
        and pp_box_is(site.box, [[-500, -500, 0], [500, 500, 400]], delta=1.0),
        site,
    )
    # A unit system of None reads a unit as a metre whatever the scale, as the
    # exporters and the probe's own record (btk.Units.metres_per) read it.
    bpy.context.scene.unit_settings.system = "NONE"
    _none_m = ProbePlacement([room]).INTERNAL_M
    check(
        "probe: a unit system of None places in metres, as the record labels it",
        _none_m == btk.Units.metres_per("internal") == 1.0,
        _none_m,
    )
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    # --- The reflection probe: the room the bake lit, as an HDR -------------
    # Mirror of mayatk's TestReflectionProbe. A lightmap is irradiance with no
    # direction, so a baked METAL -- no diffuse -- shows only what it reflects,
    # and an unbaked object is lit by nothing of the room: mayatk measured a
    # metal housing at 0.12 of Arnold's luminance under the WebXR preview's
    # studio, 0.62 with the probe. A fake TextureBaker stands in for Cycles;
    # the real render is the last block.
    from blendertk.mat_utils.bake_sets import LightmapExcludeSet as _RpExclude

    rp_dir = os.path.join(tmp_dir, "probe")
    os.makedirs(rp_dir, exist_ok=True)

    def rp_exr(path, value=0.5, w=8, h=8):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = bpy.data.images.new(os.path.basename(path), w, h, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"  # BEFORE the pixels
        img.pixels = [value, value, value, 1.0] * (w * h)
        img.filepath_raw = path
        img.file_format = "OPEN_EXR"
        img.save()
        bpy.data.images.remove(img)
        return path

    rp_calls = {}

    def rp_fake_bake(meshes, **kw):
        """A map per object as TextureBaker.bake writes one, asking
        on_progress before each, as it does."""
        progress = kw.get("on_progress")
        out = {}
        for i, m in enumerate(meshes):
            if progress and progress(i, len(meshes), m.name) is False:
                break
            out[m.name] = rp_exr(
                os.path.join(kw.get("output_dir") or rp_dir, f"{m.name}_LM.exr")
            )
        return out

    def rp_fake_pano(position, path, width=1024, hide=None, lights=None):
        """The call recorded -- with what rendered as it rendered -- and a
        2:1 panorama written."""
        scene = bpy.context.scene
        rp_calls["pano"] = {
            "position": [float(c) for c in position],
            "width": width,
            "hide": sorted(o if isinstance(o, str) else o.name for o in hide or []),
            "hidden": sorted(
                o.name
                for o in scene.objects
                if o.type in ("MESH", "LIGHT") and o.hide_render
            ),
            "world": scene.world is not None,
        }
        return rp_exr(path, 0.5, w=width, h=width // 2)

    def rp_baker(**switches):
        return LightmapBaker(
            resolution=1024, samples=1, denoise=False, device="CPU", **switches
        )

    def rp_bake(objects, baker=None, pano=rp_fake_pano, **kwargs):
        rp_calls.clear()
        baker = baker or rp_baker()
        with (
            _mock.patch.object(baker._texture_baker, "bake", side_effect=rp_fake_bake),
            _mock.patch.object(
                baker._texture_baker, "render_panorama", side_effect=pano
            ),
        ):
            result = baker.bake(
                objects, packing="per_object", output_dir=rp_dir, **kwargs
            )
        return result

    def rp_room():
        """A 10 x 10 x 4 m room on the floor, a table in it and an unbaked
        prop beside it, off-centre: ``(room, table, prop)`` (mayatk's, in
        Blender's axes: Maya's (x, y, z) is Blender's (x, -z, y))."""
        pp_reset()
        room = pp_box("pbRoom", (10, 10, 4), (0, 0, 2))
        table = pp_box("pbTable", (2.4, 1.0, 0.9), (-1, 2, 0.45))
        prop = pp_box("pbProp", (0.2, 0.2, 0.2), (-3, 1.5, 1.0))
        return room, table, prop

    def rp_deps(objects=None, walk=False):
        return {
            d["map"]: d
            for d in LightmapRecords.lightmap_dependencies(objects, walk=walk)
        }

    check(
        "the reflection probe is a preset switch, on by default",
        "reflection_probe" in LightmapBaker.PRESET_BOOL_KEYS
        and LightmapBaker().reflection_probe is True
        and LightmapBaker.from_preset(
            "preview", reflection_probe=False
        ).reflection_probe
        is False,
    )

    room, table, prop = rp_room()
    result = rp_bake([room, table])
    call = rp_calls.get("pano") or {}
    check(
        "a bake captures the room it lit as a probe beside its maps",
        bool(result.probe)
        and os.path.normcase(os.path.dirname(LightmapRecords._home(result.probe)))
        == os.path.normcase(rp_dir)
        and result.probe.endswith("untitled_Probe.exr"),
        result.probe,
    )
    check(
        "...over what moves, at eye level, half the bake's resolution wide",
        pp_near(call.get("position", ()), (-3.0, 1.5, 2.0))
        and call.get("width") == 512,
        call,
    )
    check(
        "...and what moves is see-through to it",
        call.get("hide") == ["pbProp"],
        call,
    )
    rp_record = LightmapRecords.probe() or {}
    check(
        "the probe is recorded in glTF's Y-up axes (x, z, -y), in scene units",
        rp_record.get("map") == os.path.basename(result.probe or "")
        and pp_near(rp_record.get("position") or (), (-3.0, 2.0, -1.5))
        and rp_record.get("unit_scale") == 1.0
        and pp_box_is(rp_record.get("box"), [[-5, 0, -5], [5, 4, 5]]),
        rp_record,
    )
    rp_manifest = json.loads(LightmapRecords._record().text)
    check(
        "the manifest carries the probe to the GLB, in the record's shape: its box "
        "{min, max}, its unit the record's one unit_scale",
        rp_manifest.get("probe")
        == {
            "map": rp_record.get("map"),
            "position": rp_record.get("position"),
            "box": ptk.LightmapRecord.box(rp_record.get("box")),
        }
        and rp_manifest.get("unit_scale") == rp_record.get("unit_scale"),
        rp_manifest,
    )
    rp_name = os.path.basename(result.probe or "")
    deps = rp_deps(walk=True)
    check(
        "the probe is a dependency of the file's, no object's, and resolves",
        rp_name in deps
        and deps[rp_name]["objects"] == []
        and bool(deps[rp_name]["path"]),
        deps.get(rp_name),
    )
    # The folder it is in now: the session's until the file is saved
    # (LightmapRecords._stage).
    check(
        "...and a GLB build is handed its folder",
        os.path.normcase(os.path.dirname(result.probe or ""))
        in [os.path.normcase(d) for d in LightmapRecords.search_dirs()],
        LightmapRecords.search_dirs(),
    )
    check(
        "a table under the probe is furniture, not the floor",
        abs(ProbePlacement([room, table]).faces([-1.0, 2.0, 2.0])[0][2]) < 0.01,
    )

    # The probe shows the room the maps lit: what moves shades neither, while
    # an Exclude-set member is the room, seen and shading in both.
    room, table, prop = rp_room()
    crate = pp_box("pbCrate", (0.5, 0.5, 0.5), (3, -3, 0.25))
    _RpExclude.define([crate])
    rp_bake([room, table])
    call = rp_calls.get("pano") or {}
    check(
        "what moves is out of the probe's render as it is out of the bake's",
        call.get("hidden") == ["pbProp"] and call.get("hide") == ["pbProp"],
        call,
    )
    check(
        "...and handed back as found",
        not any(o.hide_render for o in bpy.context.scene.objects),
    )
    _RpExclude.clear()

    room, table, prop = rp_room()
    outside = pp_box("pbOutside", (0.2, 0.2, 0.2), (20, -15, 1.0))
    rp_bake([room, table])
    call = rp_calls.get("pano") or {}
    check(
        "what moves outside the room does not pull the probe, and is still hidden",
        pp_near(call.get("position", ())[:2], (-3.0, 1.5))
        and "pbOutside" in call.get("hide", []),
        call,
    )

    # The render does not see a hidden object, so the box must not: a baked
    # shell around the probe, hidden from the render since its bake.
    room, table, prop = rp_room()
    shell = pp_box("pbShell", (2, 2, 2), (-3, 1.5, 2.0))
    rp_bake([room, table, shell])
    shell.hide_render = True
    _probe_baker = rp_baker()
    with _mock.patch.object(
        _probe_baker._texture_baker, "render_panorama", side_effect=rp_fake_pano
    ):
        _probe_baker.bake_probe()
    _box = (LightmapRecords.probe() or {}).get("box")
    check(
        "a hidden baked object is no wall",
        _box is not None
        and all(
            abs(got - want) < 0.01
            for got, want in (
                (_box[0][0], -5),
                (_box[0][2], -5),
                (_box[1][0], 5),
                (_box[1][2], 5),
            )
        ),
        _box,
    )

    room, table, prop = rp_room()
    result = rp_bake([room, table])
    rp_name = os.path.basename(result.probe)
    moved = os.path.join(rp_dir, "moved")
    os.makedirs(moved, exist_ok=True)
    shutil.move(result.probe, os.path.join(moved, rp_name))
    check(
        "the probe follows its file when repathed",
        LightmapRecords.repath_lightmaps({rp_name.lower(): moved}, []) == 1
        and os.path.normcase(os.path.dirname(rp_deps()[rp_name]["path"] or ""))
        == os.path.normcase(moved),
        rp_deps().get(rp_name),
    )
    scoped = rp_deps([table])
    check(
        "an export of baked objects reads the probe it ships",
        rp_name in scoped
        and bool(scoped[rp_name]["path"])
        and os.path.normcase(moved)
        in [os.path.normcase(d) for d in LightmapRecords.search_dirs([table])],
        scoped.get(rp_name),
    )
    check(
        "...and a set with nothing baked ships no probe",
        LightmapRecords.lightmap_dependencies([prop]) == [],
    )
    _relocated = os.path.join(rp_dir, "relocated")
    _plan = LightmapRecords.relocate_lightmaps(_relocated, maps=[rp_name], dry_run=True)
    check(
        "Find & Copy relocates a row's own file -- the probe's, which names no object",
        [os.path.basename(src) for src, _dst in _plan["relocate"]] == [rp_name],
        _plan,
    )
    _map_row = next(d for d in rp_deps().values() if d["objects"])
    _plan = LightmapRecords.relocate_lightmaps(
        _relocated, maps=[_map_row["map"]], dry_run=True
    )
    check(
        "...and a map's row never drags the probe along",
        [os.path.basename(src) for src, _dst in _plan["relocate"]] == [_map_row["map"]],
        _plan,
    )

    # A lightmap is bound by its FILE NAME: renamed on disk, every record
    # naming it follows -- the probe's too.
    old_map = _map_row["map"]
    new_map = "renamed_" + old_map
    os.rename(
        _map_row["path"], os.path.join(os.path.dirname(_map_row["path"]), new_map)
    )
    renamed = LightmapRecords.rename_lightmap(old_map, new_map)
    probe_new = "renamed_" + rp_name
    os.rename(os.path.join(moved, rp_name), os.path.join(moved, probe_new))
    renamed_probe = LightmapRecords.rename_lightmap(rp_name, probe_new)
    after = rp_deps()
    check(
        "a renamed map is followed by its markers and the probe by its record",
        renamed == len(_map_row["objects"])
        and renamed_probe == 1
        and bool(after.get(new_map, {}).get("path"))
        and bool(after.get(probe_new, {}).get("path"))
        and (LightmapRecords.probe() or {}).get("map") == probe_new
        and old_map not in after,
        (renamed, renamed_probe, sorted(after)),
    )

    LightmapRecords.revert([room])
    _kept = LightmapRecords.probe() is not None
    LightmapRecords.revert()
    check(
        "reverting every bake drops the probe (one baked object keeps it)",
        _kept and LightmapRecords.probe() is None,
    )

    room, table, prop = rp_room()
    result = rp_bake([room, table], baker=rp_baker(reflection_probe=False))
    check(
        "with the switch off there is no probe",
        result.probe is None
        and "pano" not in rp_calls
        and LightmapRecords.probe() is None
        and "probe" not in json.loads(LightmapRecords._record().text),
        result.probe,
    )

    def rp_raising(*_a, **_k):
        raise RuntimeError("Cycles failed")

    room, table, prop = rp_room()
    _warned = []
    with _mock.patch.object(
        LightmapBaker.logger, "warning", side_effect=lambda *a, **k: _warned.append(a)
    ):
        result = rp_bake([room, table], pano=rp_raising)
    check(
        "a probe that fails never loses the bake",
        sorted(result.maps) == ["pbRoom", "pbTable"]
        and result.probe is None
        and sorted(LightmapRecords.baked_objects()) == ["pbRoom", "pbTable"]
        and any("Reflection probe not captured" in str(a[0]) for a in _warned),
        _warned,
    )

    room, table, prop = rp_room()
    result = rp_bake([room, table], on_progress=lambda done, _total, _name: done < 1)
    check(
        "a cancelled bake captures no probe",
        len(result.maps) == 1 and result.probe is None and "pano" not in rp_calls,
        (result.maps, rp_calls),
    )

    # Lit as the maps were: a world the bake leaves out stays out of its probe,
    # and a light outside the Scope too.
    room, table, prop = rp_room()
    rp_world = bpy.data.worlds.new("rp_world")
    btk.MatUtils.get_node_tree(rp_world, create=True)
    bpy.context.scene.world = rp_world
    lamp = pp_light("POINT", (20, 20, 3), name="pbLamp")
    rp_bake([room, table], baker=rp_baker(include_environment=False))
    _no_world = rp_calls.get("pano", {}).get("world")
    rp_bake([room, table], baker=rp_baker(include_scene_lights=False))
    _outside = rp_calls.get("pano", {}).get("hidden", [])
    check(
        "an environment and lights the bake leaves out stay out of its probe",
        _no_world is False
        and "pbLamp" in _outside
        and bpy.context.scene.world == rp_world
        and not lamp.hide_render,
        (_no_world, _outside),
    )

    def rp_selected():
        return [o.name for o in bpy.context.view_layer.objects if o.select_get()]

    room, table, prop = rp_room()
    # Objects made through bpy.data reach the layer's selection only once it
    # is updated: selected before, select_set is a silent no-op.
    bpy.context.view_layer.update()
    for o in list(bpy.context.view_layer.objects):
        o.select_set(o == prop)
    _before = rp_selected()
    rp_bake([room, table])
    check(
        "a bake with its probe leaves the selection as it found it",
        _before == ["pbProp"] and rp_selected() == ["pbProp"],
        (_before, rp_selected()),
    )

    # A real Cycles probe of a small lit room: the file is the 2:1 HDR the
    # record names, at the tier's floor width, and it holds the room's light.
    pp_reset()
    rp_room_obj = pp_box("rpRealRoom", (6, 6, 3), (0, 0, 1.5), inward=True)
    rp_crate = pp_box("rpRealCrate", (1, 1, 1), (1.5, 1.0, 0.5))
    rp_lamp = pp_light("POINT", (0, 0, 2.6), name="rpRealLamp")
    rp_lamp.data.energy = 300.0
    real = LightmapBaker(resolution=64, samples=4, denoise=False, device="CPU").bake(
        [rp_room_obj, rp_crate], packing="per_object", output_dir=rp_dir
    )
    _real_img = None
    if real.probe and os.path.isfile(real.probe):
        _img = bpy.data.images.load(real.probe, check_existing=False)
        try:
            _px = np.empty(_img.size[0] * _img.size[1] * _img.channels, np.float32)
            _img.pixels.foreach_get(_px)
            _real_img = (
                tuple(_img.size),
                float(_px.reshape(-1, _img.channels)[:, :3].mean()),
            )
        finally:
            bpy.data.images.remove(_img)
    check(
        "a real Cycles bake captures its room as a 2:1 HDR probe, lit",
        _real_img is not None
        and _real_img[0] == (256, 128)
        and _real_img[1] > 0.01
        and (LightmapRecords.probe() or {}).get("map") == os.path.basename(real.probe),
        (real.probe, _real_img),
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- Re-bake Changed (mirror of mayatk's TestRebakeChanged) ---------------
    # What a map was baked against is recorded (LightmapRecords.bake_state), so a
    # re-bake redoes only what changed and what it shades -- and writes those
    # back into the cells they own in their atlas, each material keeping ONE
    # atlas. Before it a partial re-bake of an atlas group packed the subset
    # into an atlas of its own (_1) at another texel density, and nothing said
    # what had changed since the last bake. A fake bake renders a flat value
    # per object, so a cell written in place is told from one left as it was.
    import inspect

    from blendertk.light_utils.lightmap_baker.lightmap_baker import (
        LightmapBakeResult as _RcResult,
    )
    from blendertk.mat_utils.bake_sets import LightmapExcludeSet as _RcExclude
    from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET

    RC_ATLAS = "Crate_Lightmap.exr"
    RC_RES = 64
    _R = ptk.BakeBaseline.Reason
    rc_values = {}
    rc_renders = []
    # The lights in the render at each bake call: the rendered lamp names.
    rc_lamps = []

    def rc_render(_self, objects=None, **kw):
        """TextureBaker.bake as one flat value per object, at the size asked."""
        rc_lamps.append(
            sorted(
                o.name
                for o in bpy.context.scene.objects
                if o.type == "LIGHT" and not o.hide_render
            )
        )
        out, used = {}, set()
        for obj in TextureBaker.resolve_meshes(objects):
            w, h = _self._resolve_size(obj, kw.get("size"))
            px = np.full((h, w, 4), float(rc_values.get(obj.name, 1.0)), np.float32)
            px[..., 3] = 1.0
            path = ptk.FileUtils.unique_path(
                kw["output_dir"],
                f"{kw.get('prefix', '')}{obj.name}{kw.get('suffix', '')}",
                ".exr",
                used,
                claims=kw.get("claims"),
                owners=(obj.name,),
            )
            used.add(path)
            LightmapBaker._write_lightmap_exr(path, px)
            out[obj.name] = path
            rc_renders.append(obj.name)
        return out

    rc_artifacts = ptk.TempArtifacts("btk_rebake", policy="scoped")
    rc_root = rc_artifacts.__enter__().dir_path()
    rc_out = os.path.join(rc_root, "maps")
    rc_patch = _mock.patch.object(TextureBaker, "bake", rc_render)
    rc_patch.start()

    def rc_reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        rc_values.clear()
        rc_renders.clear()
        shutil.rmtree(rc_out, ignore_errors=True)
        os.makedirs(rc_out)

    def rc_material(name="rcCrate", texture="Crate"):
        """A material whose texture set names its atlas ``<texture>_Lightmap``."""
        mat = btk.create_mat("standard", name=f"{name}Mat")
        if texture:
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.new(f"{texture}_BaseColor.png", 4, 4)
        return mat

    def rc_crates(names=("rcA", "rcB", "rcC"), spacing=10.0, mat=None):
        """Unit cubes on one material, *spacing* apart: far enough that a
        change to one reaches none of the others."""
        mat = mat or rc_material()
        out = []
        for i, name in enumerate(names):
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=(i * spacing, 0.0, 0.0))
            obj = bpy.context.active_object
            obj.name = name
            btk.assign_mat(obj, mat)
            out.append(obj.name)
        bpy.context.view_layer.update()
        return out

    def rc_baker(**kw):
        return LightmapBaker(resolution=RC_RES, reflection_probe=False, **kw)

    def rc_saved():
        """What a save does first (``save_pre``): the maps written since the
        last save go home and the superseded ones are set aside
        (``ptk.PendingFiles.saving``) -- the .blend itself not written."""
        ptk.PendingFiles.session().saving(
            bpy.data.filepath, saved_from=bpy.data.filepath
        )

    def rc_as_saved(result):
        """*result* once saved (:func:`rc_saved`): its maps named by their homes."""
        rc_saved()
        result.maps = {n: LightmapRecords._home(p) for n, p in result.maps.items()}
        return result

    def rc_bake(objects, **kw):
        result = rc_baker().bake(objects, packing="atlas", output_dir=rc_out, **kw)
        if not result.maps:
            raise RuntimeError(f"fixture bake of {objects} refused: {result.refused}")
        return rc_as_saved(result)

    def rc_rebake(**kw):
        return rc_as_saved(rc_baker().rebake_changed(output_dir=rc_out, **kw))

    def rc_report(**kw):
        return rc_baker().stale_report(**kw)

    def rc_atlas(name=RC_ATLAS):
        return LightmapBaker._read_exr_top_down(os.path.join(rc_out, name))[..., :3]

    def rc_cell(atlas, name):
        """*name*'s cell in *atlas*, by the cell the record holds for it."""
        cell = LightmapRecords.bake_state()["objects"][name]["cell"]
        row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects([cell], RC_RES)[0]
        return atlas[row0:row1, col0:col1]

    def rc_files():
        return sorted(f for f in os.listdir(rc_out) if f.lower().endswith(".exr"))

    def rc_said(method):
        """What a patched logger method was asked to log, rendered."""
        return [
            (c.args[0] % c.args[1:]) if len(c.args) > 1 else str(c.args[0])
            for c in method.call_args_list
        ]

    def rc_move(name, dz=0.5):
        bpy.data.objects[name].location.z += dz
        bpy.context.view_layer.update()

    def rc_record_maps():
        return {e["map"] for e in LightmapRecords._record().payload["objects"]}

    try:
        # -- duplicate materials ----------------------------------------------
        # Mirror of mayatk's split tests: a material and its duplicate are one
        # atlas group (_atlas_keys), so a bake packs one atlas and touches no
        # material; a file baked before that (one map per material, Crate_
        # Lightmap and Crate_Lightmap_1) reads both maps as split -- no
        # re-bake target -- and Combine packs the group into one from the
        # lighting they hold, no render, the duplicate merged.
        def rc_split_scene(legacy, values=None):
            rc_reset()
            rc_values.update(values or {})
            mat = rc_material()
            img = next(n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
            img.filepath_raw = os.path.join(rc_root, "Crate_BaseColor.png")
            img.file_format = "PNG"
            img.save()  # the duplicate finder compares files
            dup = btk.create_mat("standard", name="rcDupMat")
            dup.node_tree.nodes.new("ShaderNodeTexImage").image = img
            a, b = rc_crates(("rcA", "rcB"), mat=mat)
            (c,) = rc_crates(("rcC",), mat=dup)
            bpy.data.objects[c].location.x = 20.0
            bpy.context.view_layer.update()
            raw = {n: bpy.data.objects[n].material_slots[0].material.name for n in (a, b, c)}
            with _ctx.ExitStack() as stack:
                if legacy:
                    stack.enter_context(
                        _mock.patch.object(
                            LightmapBaker,
                            "_atlas_keys",
                            classmethod(lambda cls, names: {n: raw[n] for n in names}),
                        )
                    )
                rc_bake([a, b, c])
            return (a, b, c), raw

        def rc_maps_read(objs):
            return {
                os.path.basename(p) for n, _m, p in LightmapRecords._reads() if n in objs and p
            }

        def rc_mats(objs):
            return {n: bpy.data.objects[n].material_slots[0].material.name for n in objs}

        objs, raw = rc_split_scene(legacy=False)
        check(
            "duplicate materials: one bake packs a material and its duplicate into "
            "one atlas, every material left as it was",
            rc_maps_read(objs) == {RC_ATLAS}
            and rc_mats(objs) == raw
            and rc_report().targets == (),
            (rc_maps_read(objs), rc_mats(objs)),
        )
        # Fixing the duplicates elsewhere afterwards (the Scene Exporter task,
        # MatUtils' merge) moves rcC within its one atlas group: it re-bakes
        # alone into the cell it owns -- nothing splits or re-packs.
        from blendertk.mat_utils._mat_utils import MatUtils as rc_MatUtils

        rc_MatUtils.reassign_duplicate_materials(
            [[bpy.data.materials[raw[objs[0]]], bpy.data.materials[raw[objs[2]]]]],
            delete=True,
        )
        rc_merged = rc_report()
        rc_rebake()
        check(
            "duplicate materials: merging them after a bake keeps the one atlas -- "
            "the moved object re-bakes in place, nothing re-packs",
            rc_merged.repack == {}
            and rc_merged.targets == (objs[2],)
            and rc_merged.in_place == (objs[2],)
            and rc_maps_read(objs) == {RC_ATLAS}
            and rc_report().targets == (),
            rc_merged.lines(),
        )
        def rc_cell_means(objs):
            """{name: mean of its cell in the map it reads}, through its binding."""
            marks = {o.name: i for o, i in LightmapRecords._marker_records()}
            paths = {n: p for n, _m, p in LightmapRecords._reads() if p}
            out = {}
            for n in objs:
                img = LightmapBaker._read_exr_top_down(paths[n])
                h, w = img.shape[:2]
                sx, sy, ox, oy = marks[n]["scaleOffset"]
                u0, v0, u1, v1 = LightmapBaker._lightmap_uv_bbox(n) or (0, 0, 1, 1)
                c0, c1 = int((u0 * sx + ox) * w) + 1, int((u1 * sx + ox) * w) - 1
                r0, r1 = int((1 - (v1 * sy + oy)) * h) + 1, int((1 - (v0 * sy + oy)) * h) - 1
                out[n] = float(img[r0 : max(r1, r0 + 1), c0 : max(c1, c0 + 1), :3].mean())
            return out

        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_split = rc_maps_read((a, b, c))
        rc_split_report = rc_report()
        rc_split_rows = rc_baker().bake_maps(rc_split_report)
        # Untracked (baked before change tracking): no record says rcC's map
        # was an atlas of one, but beside its group's atlas it is the split.
        rc_state = LightmapRecords.bake_state.__func__

        def rc_without_c(cls, *args, **kw):
            out = rc_state(cls, *args, **kw)
            out["objects"] = {k: e for k, e in out["objects"].items() if k != c}
            return out

        with _mock.patch.object(LightmapRecords, "bake_state", classmethod(rc_without_c)):
            rc_untracked = rc_report()
        check(
            "an untracked split-off map beside its group's atlas is the split, "
            "and no re-bake target",
            set(rc_untracked.split) == {RC_ATLAS, "Crate_Lightmap_1.exr"}
            and rc_untracked.targets == (),
            rc_untracked.lines(),
        )
        rc_sizes = rc_baker().combine_sizes()
        check(
            "Combine's sizes start at the largest map the split reads and end "
            "at the first that keeps every cell's detail",
            rc_sizes
            and rc_sizes[0][0] == RC_RES
            and [x for x, _k in rc_sizes] == sorted(x for x, _k in rc_sizes)
            and rc_sizes[-1][1] >= LightmapBaker.COMBINE_KEEP,
            rc_sizes,
        )
        rc_old = rc_cell_means((a, b, c))
        rc_dated = {n: LightmapRecords.bake_state()["objects"][n]["baked_at"] for n in (a, b, c)}
        with _mock.patch.object(
            LightmapBaker, "bake", side_effect=AssertionError("rendered")
        ), _mock.patch.object(
            LightmapBaker, "_bake_atlas", side_effect=AssertionError("rendered")
        ):
            rc_combined = rc_baker().combine()
        rc_new = rc_cell_means((a, b, c))
        rc_after = rc_report()
        rc_worst = max(abs(rc_new[n] - rc_old[n]) / max(rc_old[n], 1e-3) for n in (a, b, c))
        check(
            "a duplicate material baked to its own map reads as split (no re-bake "
            "target), and Combine packs it into one atlas with no render -- each "
            "cell keeps its light and its bake date, the duplicate merged",
            rc_split == {RC_ATLAS, "Crate_Lightmap_1.exr"}
            and rc_split_report.targets == ()
            and set(rc_split_report.split) == {RC_ATLAS, "Crate_Lightmap_1.exr"}
            and len(rc_split_report.split_materials[RC_ATLAS]) == 2
            and {r["state"] for r in rc_split_rows} == {"split"}
            and all("Combine" in r["hint"] for r in rc_split_rows)
            and not rc_combined.refused
            and rc_maps_read((a, b, c)) == {RC_ATLAS}
            and rc_worst < 0.02
            and {n: LightmapRecords.bake_state()["objects"][n]["baked_at"] for n in (a, b, c)}
            == rc_dated
            and len(set(rc_mats((a, b, c)).values())) == 1
            and (rc_after.targets, dict(rc_after.split)) == ((), {}),
            (rc_split_report.lines(), rc_combined.refused, rc_worst, rc_after.lines()),
        )
        # Combine runs under the footer's progress: each cut is reported, and a
        # cancel stops before any file or record is written.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_listed = sorted(os.listdir(rc_out))
        rc_reads = rc_maps_read((a, b, c))
        rc_seen = []
        rc_cancel = rc_baker().combine(
            on_progress=lambda done, total, name: rc_seen.append((done, total)) or False
        )
        check(
            "Combine reports each cut, and a cancel writes nothing",
            rc_seen == [(0, 3)]
            and "cancelled" in (rc_cancel.refused or "")
            and sorted(os.listdir(rc_out)) == rc_listed
            and rc_maps_read((a, b, c)) == rc_reads,
            (rc_seen, rc_cancel.refused),
        )
        # A map this file did not write (no writer record, or another file's)
        # may be read elsewhere: Combine packs into a file of its own.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_atlas_path = os.path.join(rc_out, RC_ATLAS)
        with open(rc_atlas_path, "rb") as fh:
            rc_bytes = fh.read()
        with _mock.patch.object(LightmapRecords, "wrote", return_value=False):
            rc_own = rc_baker().combine()
        with open(rc_atlas_path, "rb") as fh:
            rc_kept = fh.read() == rc_bytes
        check(
            "Combine never writes over a map this file did not write",
            rc_kept
            and RC_ATLAS not in rc_maps_read((a, b, c))
            and len(set(rc_own.maps.values())) == 1,
            (rc_maps_read((a, b, c)), rc_own.refused),
        )
        # Review 2026-10-24 (S2, mirror of mayatk's): Combine merged the
        # duplicate materials -- deleting them -- and only then read the maps;
        # one that would not load raised out of the cut, the duplicates gone
        # and no atlas written. Every map is read first: one that cannot be
        # leaves its objects their map, named, and no material is merged.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_bad = {n: p for n, _m, p in LightmapRecords._reads()}[c]
        with open(rc_bad, "wb") as fh:
            fh.write(b"cut short")
        rc_cut, rc_cut_error = None, None
        with _mock.patch.object(LightmapBaker.logger, "warning") as rc_warn:
            try:
                rc_cut = rc_baker().combine()
            except Exception as error:  # the defect: a traceback, not a result
                rc_cut_error = error
        check(
            "Combine reads every map before it merges a material: one that cannot "
            "be read keeps its objects' map, and every material stays",
            rc_cut_error is None
            and sorted(rc_cut.maps) == sorted([a, b])
            and rc_maps_read((c,)) == {os.path.basename(rc_bad)}
            and len(set(raw.values())) == 2
            and all(bpy.data.materials.get(m) is not None for m in raw.values())
            and rc_mats((a, b, c)) == raw
            and any("rcC" in m and "keep their map" in m for m in rc_said(rc_warn)),
            (repr(rc_cut_error), rc_cut and rc_cut.maps, rc_mats((a, b, c))),
        )
        # Review 2026-10-24: the merge ran before the first cut, so a cancel --
        # "nothing written" -- had already deleted the duplicates. It runs last.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_cancel = rc_baker().combine(on_progress=lambda *_args: False)
        check(
            "a cancelled Combine merges no material",
            "cancelled" in (rc_cancel.refused or "")
            and len(set(raw.values())) == 2
            and all(bpy.data.materials.get(m) is not None for m in raw.values())
            and rc_mats((a, b, c)) == raw,
            (rc_cancel.refused, rc_mats((a, b, c))),
        )
        # Review 2026-10-24: a group no longer split by the time Combine reached
        # it offered no size, and ``combine_sizes(...)[-1]`` raised IndexError.
        rc_split_scene(legacy=True)
        rc_real_groups = LightmapBaker._combine_groups

        def rc_mended(baker, maps=None):
            return rc_real_groups(baker, maps) if maps is None else []

        rc_mend, rc_mend_error = None, None
        with _mock.patch.object(LightmapBaker, "_combine_groups", rc_mended):
            try:
                rc_mend = rc_baker().combine()
            except Exception as error:  # the defect: IndexError
                rc_mend_error = error
        check(
            "Combine skips a group an earlier group's merge mended",
            rc_mend_error is None
            and rc_mend.maps == {}
            and "Nothing to combine" in (rc_mend.refused or ""),
            (repr(rc_mend_error), rc_mend and rc_mend.refused),
        )
        # Review 2026-10-24: each cell is rescaled by ``intensity / its own``, and
        # a marker at 0 raised ZeroDivisionError; such a map holds no light.
        (a, b, c), raw = rc_split_scene(legacy=True)
        LightmapRecords._write_marker(
            c, dict(LightmapRecords._marker_info(c), intensity=0.0)
        )
        rc_zero, rc_zero_error = None, None
        try:
            rc_zero = rc_baker().combine()
        except Exception as error:  # the defect: ZeroDivisionError
            rc_zero_error = error
        check(
            "Combine takes a map baked at intensity 0",
            rc_zero_error is None and sorted(rc_zero.maps) == sorted([a, b, c]),
            (repr(rc_zero_error), rc_zero and rc_zero.maps),
        )

        # -- saved with the file ----------------------------------------------
        # Mirror of mayatk's: an unsaved Combine wrote its atlas over the
        # production room's LX2000_Lightmap.exr and recycled
        # LX2000_Lightmap_1.exr; the saved scene reopened with a map missing
        # and three objects lit from the wrong cells (2026-10-23). Every map
        # waits in the session until the file is saved (LightmapRecords._stage).
        def rc_disk():
            """``{file name: bytes}`` of the output folder -- what the saved
            .blend reads."""
            out = {}
            for name in rc_files():
                with open(os.path.join(rc_out, name), "rb") as fh:
                    out[name] = fh.read()
            return out

        def rc_saved_split(name="room.blend"):
            """The legacy split, saved as *name* in the re-bake root."""
            # Each its own light: a flat 1.0 everywhere packs into an atlas
            # byte for byte the old one, and no overwrite would show.
            objs, _raw = rc_split_scene(
                legacy=True, values={"rcA": 0.25, "rcB": 0.5, "rcC": 0.75}
            )
            path = os.path.join(rc_root, name)
            bpy.ops.wm.save_as_mainfile(filepath=path)
            return objs, path

        rc_pair = {RC_ATLAS, "Crate_Lightmap_1.exr"}
        (a, b, c), rc_room = rc_saved_split()
        rc_before = rc_disk()
        rc_unsaved = rc_baker().combine()
        rc_reads_now = rc_maps_read((a, b, c))
        rc_untouched = rc_disk() == rc_before
        bpy.ops.wm.open_mainfile(filepath=rc_room)
        check(
            "an unsaved Combine leaves the saved file's maps and folder as they "
            "were, the file reading the new atlas meanwhile; reopened, the split "
            "is back and nothing is left behind",
            not rc_unsaved.refused
            and rc_reads_now == {RC_ATLAS}
            and rc_untouched
            and rc_maps_read((a, b, c)) == rc_pair
            and not any(os.path.exists(p) for p in rc_unsaved.files),
            (rc_reads_now, rc_untouched, rc_maps_read((a, b, c)), rc_unsaved.files),
        )

        (a, b, c), rc_room = rc_saved_split()
        rc_baker().combine()
        bpy.ops.wm.save_mainfile()
        rc_home = {
            n: os.path.normcase(os.path.dirname(p))
            for n, _m, p in LightmapRecords._reads()
            if n in (a, b, c) and p
        }
        bpy.ops.wm.open_mainfile(filepath=rc_room)
        check(
            "saving a Combine moves its atlas home under its own name -- no "
            "suffix -- and sets the split-off map aside",
            rc_files() == [RC_ATLAS]
            and set(rc_home.values()) == {os.path.normcase(rc_out)}
            and rc_maps_read((a, b, c)) == {RC_ATLAS}
            and dict(rc_report().split) == {},
            (rc_files(), rc_home),
        )

        (a, b, c), rc_room = rc_saved_split()
        rc_before = rc_disk()
        rc_baker().combine()
        rc_copy = os.path.join(rc_root, "room_v2.blend")
        bpy.ops.wm.save_as_mainfile(filepath=rc_copy)
        rc_fork_reads = rc_maps_read((a, b, c))
        rc_fork_own = all(LightmapRecords.wrote(n) for n in rc_fork_reads)
        rc_fork_writers = (LightmapRecords._writers(), bpy.data.filepath)
        rc_first_kept = all(rc_disk().get(n) == data for n, data in rc_before.items())
        bpy.ops.wm.open_mainfile(filepath=rc_room)
        rc_first_reads = rc_maps_read((a, b, c))
        check(
            "a Save As after an unsaved Combine leaves the first file its maps: "
            "the taken name goes to the new file under the next free one, and "
            "nothing is set aside",
            rc_first_kept
            and rc_fork_reads == {"Crate_Lightmap_2.exr"}
            and rc_first_reads == rc_pair,
            (rc_fork_reads, rc_first_kept, rc_first_reads),
        )
        check(
            "...and the new file owns the map it forked",
            rc_fork_own,
            rc_fork_writers,
        )

        # Undo: the file reads its old maps again, so the save drops the
        # session's atlas and leaves the folder as it was.
        (a, b, c), rc_room = rc_saved_split()
        rc_before = rc_disk()
        bpy.ops.ed.undo_push(message="before combine")
        rc_baker().combine()  # one undo step (its undo chunk)
        bpy.ops.ed.undo()
        rc_undone_reads = rc_maps_read((a, b, c))
        bpy.ops.wm.save_mainfile()
        check(
            "an undone Combine is not saved: the folder is as it was",
            rc_undone_reads == rc_pair and rc_disk() == rc_before,
            (rc_undone_reads, sorted(rc_disk()), sorted(rc_before)),
        )
        # Production: Start Tracking two rows turned every row "up to date" --
        # the commit took every other untracked object in as tracked, and the
        # split-off map, recorded now, no longer read as split.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_record = LightmapRecords.bake_state()
        for n in (a, c):
            rc_record["objects"].pop(n)
        ptk.SceneRecords.LIGHTMAP_BAKE_STATE.save(
            LightmapRecords._data_nodes(), ptk.BakeBaseline.encode(rc_record)
        )
        rc_before_track = rc_report().untracked
        rc_tracked_one = rc_baker().track([c])
        rc_tracked_report = rc_report()
        check(
            "tracking one map leaves the split split and the other untracked",
            rc_before_track == tuple(sorted((a, c)))
            and rc_tracked_one == [c]
            and rc_tracked_report.untracked == (a,)
            and set(rc_tracked_report.split) == {RC_ATLAS, "Crate_Lightmap_1.exr"},
            (rc_before_track, rc_tracked_one, rc_tracked_report.lines()),
        )
        # The split row's tooltip says "right-click it: Re-Bake": a Re-Bake of
        # the main atlas's objects mends it too, not only the split-off one's.
        (a, b, c), raw = rc_split_scene(legacy=True)
        rc_baker().rebake([a, b], output_dir=rc_out)
        check(
            "Re-Bake of the main atlas's objects packs a split group into one atlas",
            rc_maps_read((a, b, c)) == {RC_ATLAS} and rc_report().targets == (),
            rc_maps_read((a, b, c)),
        )

        # -- Start Tracking (mirror of mayatk's) --------------------------------
        rc_reset()
        names = rc_crates()
        rc_bake(names)
        rc_record = LightmapRecords.bake_state()
        rc_record["objects"].pop(names[2])  # baked before change tracking
        ptk.SceneRecords.LIGHTMAP_BAKE_STATE.save(
            LightmapRecords._data_nodes(), ptk.BakeBaseline.encode(rc_record)
        )
        rc_untracked_before = rc_report().untracked
        rc_map = os.path.join(rc_out, RC_ATLAS)
        rc_mtime = os.path.getmtime(rc_map)
        rc_tracked = rc_baker().track([names[0], names[2]])
        rc_entry = LightmapRecords.bake_state()["objects"][names[2]]
        rc_after = rc_report()
        bpy.data.objects[names[2]].location.z += 5.0
        bpy.context.view_layer.update()
        check(
            "Start Tracking records an untracked map as it stands, rendering "
            "nothing, so its next change reads as changed",
            rc_untracked_before == (names[2],)
            and rc_tracked == [names[2]]
            and os.path.getmtime(rc_map) == rc_mtime
            and rc_entry["baked_at"] is not None
            and rc_entry["lighting"] is not None
            and rc_entry["cell"] is None
            and (rc_after.untracked, rc_after.targets) == ((), ())
            and names[2] in rc_report().targets
            and rc_baker().track([names[2]]) == [],
            (rc_untracked_before, rc_tracked, rc_entry, rc_after.lines()),
        )

        # -- what is stale ----------------------------------------------------
        rc_reset()
        names = rc_crates()
        rc_bake(names)
        first = rc_report()
        rc_blend = os.path.join(rc_root, "rebake_room.blend")
        bpy.ops.wm.save_as_mainfile(filepath=rc_blend)
        bpy.ops.wm.open_mainfile(filepath=rc_blend)
        reopened = rc_report()
        check(
            "Re-bake Changed: nothing is stale after a bake, nor after a save and reopen",
            first.targets == ()
            and first.untracked == ()
            and reopened.targets == ()
            and reopened.untracked == ()
            and reopened.summary() == "Nothing changed since the last bake.",
            (first.lines(), reopened.lines()),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        rc_move(a)
        report = rc_report()
        check(
            "Re-bake Changed: a moved object is stale, and its far neighbours are not",
            report.targets == (a,) and report.stale.get(a) == (_R.MOVED,),
            report.lines(),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        rc_mesh = bpy.data.objects[a].data
        for vert in list(rc_mesh.vertices)[:4]:
            vert.co.z += 0.4
        rc_mesh.update()
        bpy.context.view_layer.update()
        report = rc_report()
        check(
            "Re-bake Changed: a deformed object is stale",
            report.targets == (a,) and _R.DEFORMED in report.stale.get(a, ()),
            report.lines(),
        )

        rc_reset()
        names = rc_crates()
        rc_bake(names)
        os.remove(os.path.join(rc_out, RC_ATLAS))
        report = rc_report()
        check(
            "Re-bake Changed: a missing map stales its readers",
            report.targets == tuple(sorted(names))
            and all(_R.MISSING_MAP in report.stale.get(n, ()) for n in names),
            report.lines(),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.data.objects[a].hide_render = True
        report = rc_report()
        check(
            "Re-bake Changed: an object hidden from the render is stale, and "
            "cannot bake (it shades nothing)",
            report.unbakeable == (a,)
            and report.targets == ()
            and _R.VISIBILITY in report.stale.get(a, ()),
            report.lines(),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(11.2, 0.0, 0.0))
        rc_stool = bpy.context.active_object
        rc_stool.name = "rcStool"
        _RcExclude.define([rc_stool])
        report = rc_report()
        check(
            "Re-bake Changed: an object added to the Exclude set stales its "
            "neighbour (it is in every bake's render)",
            report.targets == (b,) and report.neighbours.get(b) == ("rcStool",),
            report.lines(),
        )

        rc_reset()
        bpy.ops.object.light_add(type="POINT", location=(10.0, 0.0, 4.0))
        rc_lamp = bpy.context.active_object
        rc_lamp.data.energy = 100.0
        names = rc_crates()
        rc_bake(names)
        before_light = rc_report()
        rc_lamp.data.energy = 500.0
        report = rc_report()
        check(
            "Re-bake Changed: a changed light stales the whole room, said so, "
            "and the dry run is repeatable",
            before_light.targets == ()
            and report.lighting == (rc_lamp.name,)
            and report.targets == tuple(sorted(names))
            and "the whole room" in report.summary()
            and rc_report() == report,
            (before_light.lines(), report.lines()),
        )
        check(
            "...and opted out, the lighting is not compared",
            rc_rebake(lighting=False).maps == {},
        )

        # A bake re-bakes the way it was LIT (mirror of mayatk's
        # test_changed_relights_the_way_the_bake_lit_and_settles): Include
        # Scene Lighting off, a loose lamp handed beside the crates is the
        # Scope's own. The dry run reads that lamp -- not the lights under
        # what is baked -- the re-bake is lit by it alone, and a second
        # Changed finds nothing.
        rc_reset()
        a, b = rc_crates(names=("rcA", "rcB"))
        bpy.ops.object.light_add(type="POINT", location=(0.0, 0.0, 4.0))
        bpy.context.active_object.name = "rcScopeLamp"
        bpy.ops.object.light_add(type="POINT", location=(10.0, 0.0, 4.0))
        bpy.context.active_object.name = "rcLooseLamp"

        def rc_scoped():
            return rc_baker(include_scene_lights=False)

        rc_scoped().bake([a, b, "rcScopeLamp"], packing="atlas", output_dir=rc_out)
        rc_rule = next(iter(LightmapRecords.bake_state()["lightings"].values())).get(
            "@rule"
        )
        settled_first = rc_scoped().stale_report()
        # Changed from a panel whose switch was flipped since (every light):
        # the recorded rule lights the re-bake, not the baker's own switches.
        rc_changed = rc_baker()
        settled_on = rc_changed.stale_report()
        rc_move(a)
        rc_lamps.clear()
        rc_first = rc_changed.rebake_changed(output_dir=rc_out)
        rc_lit = list(rc_lamps)
        rc_second = rc_changed.rebake_changed(output_dir=rc_out)
        check(
            "Re-bake Changed, Include Scene Lighting off: the dry run reads the "
            "lights the bake used, the re-bake is lit by them alone (whatever "
            "the baker's switches), and a second Changed finds nothing",
            settled_first.targets == ()
            and settled_on.targets == ()
            and list(rc_first.maps) == [a]
            and bool(rc_lit)
            and all(lamps == ["rcScopeLamp"] for lamps in rc_lit)
            and rc_changed.include_scene_lights is True
            and rc_second.maps == {}
            and rc_scoped().stale_report().targets == (),
            (settled_first.lines(), list(rc_first.maps), rc_lit, rc_second.refused),
        )
        check(
            "...the bake records the rule it lit by, in mayatk's shape",
            rc_rule
            == {
                "include_scene_lights": False,
                "include_environment": True,
                "include_hidden_lights": False,
                "scope": ["rcScopeLamp"],
            },
            rc_rule,
        )

        # -- written back in place ------------------------------------------
        rc_reset()
        a, b, c = rc_crates()
        rc_values.update(rcA=1.0, rcB=2.0, rcC=3.0)
        rc_bake([a, b, c])
        before = rc_atlas()
        rc_values["rcA"] = 5.0
        rc_move(a)
        result = rc_rebake()
        after = rc_atlas()
        check(
            "Re-bake Changed: the changed object is written back into its own "
            "atlas cell -- no _1 atlas",
            result.in_place == [a]
            and result.repacked == {}
            and list(result.maps) == [a]
            and rc_files() == [RC_ATLAS]
            and result.report is not None
            and result.report.in_place == (a,),
            (result.in_place, result.repacked, list(result.maps), rc_files()),
        )
        check(
            "...every other cell stays byte-identical",
            all(np.array_equal(rc_cell(after, n), rc_cell(before, n)) for n in (b, c)),
        )
        check(
            "...and the cell written holds the new bake",
            bool(np.allclose(rc_cell(after, a), 5.0, atol=1e-3)),
            float(rc_cell(after, a).mean()),
        )
        check(
            "...nothing is left stale, and what ships names ONE map for the material",
            rc_report().targets == () and rc_record_maps() == {RC_ATLAS},
            (rc_report().lines(), rc_record_maps()),
        )

        # The same partial re-bake shipped natively: one lightmap image for the
        # material in the GLB (the per-object clones share it).
        from blendertk.light_utils.lightmap_baker.web_export import LightmapWebExport

        def rc_glb_json(path):
            import struct

            with open(path, "rb") as fh:
                data = fh.read()
            length = struct.unpack_from("<I", data, 12)[0]
            return json.loads(data[20 : 20 + length].decode("utf-8"))

        rc_glb = os.path.join(rc_root, "rebake.glb")
        rc_objs = [bpy.data.objects[n] for n in (a, b, c)]
        rc_web = LightmapWebExport(
            resolution=RC_RES, samples=1, denoise=False, device=None
        )
        with rc_web.wired_for_export(objects=rc_objs) as rc_manifest:
            rc_web.export_glb(
                rc_glb, objects=rc_objs, manifest=rc_manifest, texture_max_size=None
            )
        rc_gltf = rc_glb_json(rc_glb)
        rc_carried = [
            m for m in rc_gltf.get("materials", []) if m.get("occlusionTexture")
        ]

        def rc_source(texture):
            """An image index, plain or through an image-format extension."""
            if "source" in texture:
                return texture["source"]
            for ext in (texture.get("extensions") or {}).values():
                if isinstance(ext, dict) and "source" in ext:
                    return ext["source"]
            return None

        rc_images = {
            rc_source(rc_gltf["textures"][m["occlusionTexture"]["index"]])
            for m in rc_carried
        }
        check(
            "...and a native GLB export ships ONE lightmap image for the material",
            len(rc_carried) == 3 and len(rc_images) == 1,
            f"{len(rc_carried)} carrier material(s), {len(rc_images)} lightmap image(s)",
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_values.update(rcA=1.0, rcB=2.0, rcC=3.0)
        rc_bake([a, b, c], intensity=math.pi)
        before = rc_atlas()
        rc_move(a)
        result = rc_rebake(intensity=math.pi)
        after = rc_atlas()
        check(
            "Re-bake Changed: intensity is applied ONCE to a cell written in place",
            result.in_place == [a]
            and bool(np.allclose(rc_cell(before, b), 2.0 * math.pi, rtol=1e-3))
            and bool(np.allclose(rc_cell(after, a), math.pi, rtol=1e-3))
            and np.array_equal(rc_cell(after, b), rc_cell(before, b)),
            (result.in_place, float(rc_cell(after, a).mean())),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.data.objects[a].scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        with _mock.patch.object(LightmapBaker.logger, "info") as rc_info:
            result = rc_rebake()
        why = result.repacked.get(RC_ATLAS, "")
        check(
            "Re-bake Changed: a cell that no longer fits re-packs its whole group, "
            "into its one atlas, and says why",
            result.in_place == []
            and sorted(result.maps) == sorted([a, b, c])
            and list(result.repacked) == [RC_ATLAS]
            and "no longer fits" in why
            and "2 other members re-bake with it" in why
            and rc_files() == [RC_ATLAS]
            and any(why in m for m in rc_said(rc_info))
            and rc_report().targets == (),
            (result.in_place, sorted(result.maps), result.repacked, rc_files()),
        )

        # An update writes each map back where it is; *relocate* moves them,
        # and its dry run says what that re-packs (mayatk's production
        # PLAYGROUND_wStands: "29 written in place", then 97 maps re-packed).
        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        rc_move(a)
        rc_elsewhere = os.path.join(rc_root, "elsewhere")
        shutil.rmtree(rc_elsewhere, ignore_errors=True)
        os.makedirs(rc_elsewhere)
        kept = rc_report(output_dir=rc_elsewhere)
        moved = rc_report(output_dir=rc_elsewhere, relocate=True)
        result = rc_as_saved(rc_baker().rebake_changed(output_dir=rc_elsewhere))
        check(
            "Re-bake Changed keeps each atlas in its folder unless told to relocate",
            kept.in_place == (a,)
            and moved.in_place == ()
            and "another folder" in moved.repack.get(RC_ATLAS, "")
            and result.in_place == [a]
            and os.listdir(rc_elsewhere) == [],
            (kept.in_place, moved.repack, result.in_place, os.listdir(rc_elsewhere)),
        )
        rc_move(b)
        result = rc_as_saved(
            rc_baker().rebake_changed(output_dir=rc_elsewhere, relocate=True)
        )
        check(
            "Re-bake Changed relocates when told to, re-packing into the new folder",
            list(result.repacked) == [RC_ATLAS]
            and [f for f in os.listdir(rc_elsewhere) if f.endswith(".exr")] == [RC_ATLAS]
            and rc_report().targets == (),
            (result.repacked, os.listdir(rc_elsewhere)),
        )

        # The dry run counts what a re-pack drags in.
        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.data.objects[a].scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        report = rc_report(output_dir=rc_out)
        check(
            "Re-bake Changed: the dry run counts what a re-pack drags in",
            report.targets == (a,)
            and report.joining == tuple(sorted([b, c]))
            and "(+2 more objects)" in report.summary(),
            (report.targets, report.joining, report.summary()),
        )

        # Re-bake Selected: given objects redone as baked, changed or not.
        rc_reset()
        a, b, c = rc_crates()
        rc_values.update(rcA=1.0, rcB=2.0, rcC=3.0)
        rc_bake([a, b, c])
        before = rc_atlas()
        rc_values["rcB"] = 7.0
        unseen = rc_report().targets
        result = rc_as_saved(rc_baker().rebake([b], output_dir=rc_out))
        after = rc_atlas()
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 30.0, 0.0))
        loose = bpy.context.active_object.name
        refused = rc_baker().rebake([loose], output_dir=rc_out)
        check(
            "rebake redoes the given objects as baked, changed or not, in place",
            unseen == ()
            and result.in_place == [b]
            and list(result.maps) == [b]
            and bool(np.allclose(rc_cell(after, b), 7.0, atol=1e-3))
            and all(
                np.array_equal(rc_cell(after, o), rc_cell(before, o)) for o in (a, c)
            )
            and refused.maps == {}
            and "None of these objects is baked yet" in (refused.refused or ""),
            (result.in_place, list(result.maps), refused.refused),
        )

        # A renamed atlas keeps its baseline: the next partial re-bake goes
        # in place instead of refusing ("recorded against another map").
        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        os.rename(os.path.join(rc_out, RC_ATLAS), os.path.join(rc_out, "Room_LM.exr"))
        LightmapRecords.rename_lightmap(RC_ATLAS, "Room_LM.exr")
        rc_move(a)
        result = rc_rebake()
        check(
            "a renamed atlas keeps its baseline: the next re-bake goes in place",
            result.repacked == {} and result.in_place == [a],
            (result.repacked, result.in_place),
        )

        # BACKLOG 2026-10-21 (S2, ported from mayatk): a Save As copy's
        # re-bake wrote its cells into the atlas its source still reads. It now
        # writes them into its own copy of it, the source's file untouched.
        rc_reset()
        a, b, c = rc_crates()
        rc_values.update(rcA=1.0, rcB=2.0, rcC=3.0)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcSource.blend"))
        rc_bake([a, b, c])
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            src_bytes = fh.read()
        src_atlas = rc_atlas()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcCopy.blend"))
        rc_values["rcA"] = 5.0
        rc_move(a)
        report = rc_report(output_dir=rc_out)
        result = rc_rebake()
        fork = RC_ATLAS.replace(".exr", "_1.exr")
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            kept = fh.read() == src_bytes
        after = rc_atlas(fork) if os.path.isfile(os.path.join(rc_out, fork)) else None
        check(
            "a copy's first re-bake copies its source's atlas, then writes in place",
            report.in_place == (a,)
            and list(report.forked) == [RC_ATLAS]
            and result.in_place == [a]
            and result.repacked == {}
            and kept
            and rc_files() == [RC_ATLAS, fork]
            and all(
                LightmapRecords._marker_info(bpy.data.objects[o])["map"] == fork
                for o in (a, b, c)
            )
            and after is not None
            and bool(np.array_equal(rc_cell(after, b), rc_cell(src_atlas, b)))
            and bool(np.allclose(rc_cell(after, a), 5.0, atol=1e-3))
            and rc_report().targets == (),
            (report.in_place, report.forked, result.in_place, rc_files(), kept),
        )
        rc_move(b)
        check(
            "...and the next change goes in place into the copy: no third file",
            rc_rebake().in_place == [b] and rc_files() == [RC_ATLAS, fork],
            rc_files(),
        )

        # Review 2026-10-24 (S2, mirror of mayatk's): the copy's fork chose its
        # name against claims read before it existed, and a group packing anew
        # in the same bake under the same texture set chose it too -- the new
        # atlas replaced the fork, whose readers sampled its layout, nothing said.
        rc_reset()
        rc_mat = rc_material()
        a, b, c = rc_crates(mat=rc_mat)
        rc_values.update(rcA=1.0, rcB=2.0, rcC=3.0, rcD=7.0, rcE=7.0)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcForkSource.blend"))
        rc_bake([a, b, c])
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcForkCopy.blend"))
        # The same texture set on a material of its own (not a duplicate: its
        # own roughness), so a group of its own that packs anew under that name.
        rc_other = rc_material(name="rcOther", texture=None)
        rc_other.node_tree.nodes.new("ShaderNodeTexImage").image = next(
            n.image for n in rc_mat.node_tree.nodes if n.type == "TEX_IMAGE"
        )
        for n in rc_other.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                n.inputs["Roughness"].default_value = 0.1
        d, e = rc_crates(("rcD", "rcE"), mat=rc_other)
        for n in (d, e):
            bpy.data.objects[n].location.y += 40.0
        bpy.context.view_layer.update()
        rc_keys = LightmapBaker._atlas_keys([a, d])
        rc_values["rcA"] = 5.0
        rc_move(a)
        res = rc_bake([a, d, e])
        fork, rc_new = "Crate_Lightmap_1.exr", "Crate_Lightmap_2.exr"
        rc_forked = rc_atlas(fork) if os.path.isfile(os.path.join(rc_out, fork)) else None
        rc_fresh = rc_atlas(rc_new) if os.path.isfile(os.path.join(rc_out, rc_new)) else None
        check(
            "a copy's fork and a new atlas of its texture set, in one bake, never "
            "share a file",
            len(set(rc_keys.values())) == 2
            and res.in_place == [a]
            and rc_maps_read((a, b, c)) == {fork}
            and rc_maps_read((d, e)) == {rc_new}
            and rc_files() == [RC_ATLAS, fork, rc_new]
            and rc_forked is not None
            and bool(np.allclose(rc_cell(rc_forked, b), 2.0, atol=1e-3))
            and bool(np.allclose(rc_cell(rc_forked, a), 5.0, atol=1e-3))
            and rc_fresh is not None
            and bool(np.allclose(rc_cell(rc_fresh, d), 7.0, atol=1e-3)),
            (rc_keys, res.in_place, rc_maps_read((a, b, c)), rc_maps_read((d, e)), rc_files()),
        )

        # 2026-10-21 audit: two files in one project whose materials share a
        # texture set name one atlas; the second's bake replaced the first's.
        rc_reset()
        a, b, c = rc_crates()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcDay.blend"))
        rc_bake([a, b, c])
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            day = fh.read()
        bpy.ops.wm.read_factory_settings(use_empty=True)  # rc_reset wipes the maps
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcNight.blend"))
        rc_values.update(rcN1=9.0, rcN2=9.0)
        night = rc_crates(names=("rcN1", "rcN2"))
        res = rc_bake(night)
        rc_move(night[0])
        again = rc_bake(night)
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            day_kept = fh.read() == day
        own = RC_ATLAS.replace(".exr", "_1.exr")
        check(
            "another file's atlas of the same name is never written over",
            day_kept
            and {os.path.basename(p) for p in res.maps.values()} == {own}
            and {os.path.basename(p) for p in again.maps.values()} == {own}
            and rc_files() == [RC_ATLAS, own],
            (day_kept, rc_files()),
        )
        rc_reset()
        names = rc_crates()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcReverted.blend"))
        rc_bake(names)
        LightmapRecords.revert()
        res = rc_bake(names)
        check(
            "...while a reverted file's own atlas is still its own to bake over",
            {os.path.basename(p) for p in res.maps.values()} == {RC_ATLAS}
            and rc_files() == [RC_ATLAS],
            rc_files(),
        )
        # Review 2026-10-24 (S2): a group of ONE takes the texture set's name
        # too, and its map moved in (``_place``) without asking the disk -- the
        # other file's atlas replaced at the save all the same.
        rc_reset()
        names = rc_crates()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcSoloDay.blend"))
        rc_bake(names)
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            day = fh.read()
        bpy.ops.wm.read_factory_settings(use_empty=True)  # rc_reset wipes the maps
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcSoloNight.blend"))
        rc_values.update(rcS1=9.0)
        res = rc_bake(rc_crates(names=("rcS1",)))
        with open(os.path.join(rc_out, RC_ATLAS), "rb") as fh:
            day_kept = fh.read() == day
        rc_solo = RC_ATLAS.replace(".exr", "_1.exr")
        check(
            "...nor by a one-object group of that texture set",
            day_kept
            and {os.path.basename(p) for p in res.maps.values()} == {rc_solo}
            and rc_files() == [RC_ATLAS, rc_solo],
            (day_kept, rc_files()),
        )

        # Review 2026-10-24 (S2, mirror of mayatk's): what a bake supersedes
        # was read off the files its objects read -- for a map re-baked in
        # place and not saved yet, its session copy -- and the project filter
        # dropped that path: the home file was never set aside.
        rc_reset()
        btk.EnvUtils.set_current_workspace(rc_root)
        try:
            names = rc_crates()
            bpy.ops.wm.save_as_mainfile(
                filepath=os.path.join(rc_root, "rcSuperseded.blend")
            )
            rc_bake(names)
            rc_move(names[0])
            rc_waiting = rc_baker().rebake_changed(output_dir=rc_out)  # not saved
            # To maps of their own: an atlas bake writes the group back in place.
            res = rc_as_saved(
                rc_baker().bake(names, packing="per_object", output_dir=rc_out)
            )
            check(
                "a map waiting in the session for the save is set aside once "
                "superseded",
                rc_waiting.in_place == [names[0]]
                and RC_ATLAS not in rc_maps_read(names)
                and [os.path.basename(p) for p in res.retired] == [RC_ATLAS]
                and RC_ATLAS not in rc_files(),
                (rc_waiting.in_place, rc_maps_read(names), res.retired, rc_files()),
            )
        finally:
            btk.EnvUtils.set_current_workspace(None)

        # Review 2026-10-24: save_pre cannot tell a Save Copy from a Save As,
        # and a fork dropped the open file's superseded maps -- a Save Copy left
        # them on disk for good. Held, they go back unless the open file moved.
        from blendertk.node_utils.data_nodes import DataNodes as _RcDataNodes

        rc_reset()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(rc_root, "rcKeep.blend"))
        rc_old = os.path.join(rc_out, "rcOld_Lightmap.exr")
        with open(rc_old, "wb") as fh:
            fh.write(b"map")
        rc_copy = os.path.join(rc_root, "rcKeepCopy.blend")
        LightmapRecords._pending().retire([rc_old])
        _RcDataNodes._rebase_before_save(rc_copy)  # save_pre: a fork
        _RcDataNodes._restore_after_copy(rc_copy)  # save_post: the open file stayed
        rc_kept = ptk.PendingFiles.session().take_retiring()
        LightmapRecords._pending().retire([rc_old])
        _RcDataNodes._rebase_before_save(rc_copy)
        _RcDataNodes._restore_after_copy(bpy.data.filepath)  # a Save As: it moved
        rc_dropped = ptk.PendingFiles.session().take_retiring()
        check(
            "a Save Copy keeps the open file's superseded maps for its next save; "
            "a Save As drops them",
            [os.path.normcase(p) for p in rc_kept]
            == [os.path.normcase(os.path.abspath(rc_old))]
            and rc_dropped == [],
            (rc_kept, rc_dropped),
        )

        # A rendered mesh no bake renders is named, and stales nothing.
        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.2, 0.0, 0.0))
        stand = bpy.context.active_object.name
        report = rc_report()
        _RcExclude.define([stand])
        excluded = rc_report()
        check(
            "a rendered mesh no bake renders is named out of the bake, staling nothing",
            report.targets == ()
            and report.out_of_bake == (stand,)
            and "1 visible mesh out of the bake" in report.summary()
            and excluded.out_of_bake == (),
            (report.targets, report.out_of_bake, excluded.out_of_bake),
        )
        _RcExclude.clear()

        # The Bake State breakdown names each map and its state.
        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        rc_move(a)
        doc = rc_baker().bake_breakdown(output_dir=rc_out)
        text = doc.to_text()
        check(
            "the bake breakdown names each map, its size, state and writer",
            all(
                s in text
                for s in (RC_ATLAS, f"{RC_RES} x {RC_RES}", "1 changed", "this scene")
            )
            and "action://select?node=" in doc.to_html(),
            text,
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        ptk.SceneRecords.LIGHTMAP_BAKE_STATE.clear(LightmapRecords._data_nodes())
        first = rc_bake([a])
        second = rc_bake([a])
        check(
            "Re-bake Changed: an atlas baked before change tracking re-packs whole "
            "ONCE, then goes in place",
            sorted(first.maps) == sorted([a, b, c])
            and "no recorded cell" in first.repacked.get(RC_ATLAS, "")
            and second.in_place == [a]
            and list(second.maps) == [a]
            and rc_files() == [RC_ATLAS]
            and rc_record_maps() == {RC_ATLAS},
            (first.repacked, second.in_place, list(second.maps), rc_files()),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        bpy.data.objects[a].scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        _RcExclude.define([bpy.data.objects[c]])
        with _mock.patch.object(LightmapBaker.logger, "warning") as rc_warn:
            result = rc_rebake()
        check(
            "Re-bake Changed: a member that cannot bake keeps its atlas, and the "
            "group takes the next name",
            sorted(result.maps) == sorted([a, b])
            and {os.path.basename(p) for p in result.maps.values()}
            == {"Crate_Lightmap_1.exr"}
            and LightmapRecords._marker_info(c).get("map") == RC_ATLAS
            and any("rcC" in m and "cannot bake" in m for m in rc_said(rc_warn)),
            (result.maps, rc_said(rc_warn)),
        )

        # Two coplanar panels share an edge: the one re-baked is stitched to the
        # one left as it was -- the free side moves the whole way, and the held
        # cell keeps every texel.
        rc_reset()
        rc_panel_mat = rc_material("rcPanel", texture=None)
        rc_panels = []
        for i in range(2):
            x0, x1 = 2.0 * i, 2.0 * (i + 1)
            mesh = bpy.data.meshes.new(f"rcPanel{i}")
            mesh.from_pydata(
                [(x0, 0.0, 0.0), (x1, 0.0, 0.0), (x1, 1.3, 0.0), (x0, 1.3, 0.0)],
                [],
                [(0, 1, 2, 3)],
            )
            mesh.uv_layers.new(name=LIGHTMAP_UV_SET).data.foreach_set(
                "uv", [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
            )
            obj = bpy.data.objects.new(f"rcPanel{i}", mesh)
            bpy.context.collection.objects.link(obj)
            btk.assign_mat(obj, rc_panel_mat)
            rc_panels.append(obj.name)
        bpy.context.view_layer.update()
        p0, p1 = rc_panels
        rc_values.update(rcPanel0=1.0, rcPanel1=2.0)
        panel_atlas = os.path.basename(rc_bake([p0, p1]).maps[p0])
        before = rc_atlas(panel_atlas)
        rc_values["rcPanel1"] = 3.0
        result = rc_bake([p1])
        after = rc_atlas(panel_atlas)
        rects = [LightmapRecords._marker_info(n)["scaleOffset"] for n in (p0, p1)]
        pairs = ptk.ImgUtils.atlas_seam_pairs(
            [LightmapBaker._boundary_edges(n) for n in (p0, p1)],
            rects,
            RC_RES,
            tolerance=LightmapBaker._SEAM_TOLERANCE,
            coplanar=LightmapBaker._SEAM_COPLANAR,
        )
        held_rows = ptk.ImgUtils.atlas_pixel_rects(
            [LightmapRecords.bake_state()["objects"][p0]["cell"]], RC_RES
        )[0]

        def rc_read(image, x, y):
            full = image[..., 0].astype(np.float64)
            h, w = full.shape
            x0, y0 = int(np.floor(x)), int(np.floor(y))
            fx, fy = x - x0, y - y0

            def tap(r, c):
                return full[min(max(r, 0), h - 1), min(max(c, 0), w - 1)]

            return (
                tap(y0, x0) * (1 - fx) * (1 - fy)
                + tap(y0, x0 + 1) * fx * (1 - fy)
                + tap(y0 + 1, x0) * (1 - fx) * fy
                + tap(y0 + 1, x0 + 1) * fx * fy
            )

        def rc_in_held(x, y):
            row0, row1, col0, col1 = held_rows
            return col0 - 0.5 <= x <= col1 - 0.5 and row0 - 0.5 <= y <= row1 - 0.5

        seam = []
        for xa, ya, xb, yb in pairs:
            if not rc_in_held(xa, ya):
                xa, ya, xb, yb = xb, yb, xa, ya
            seam.append(
                (
                    abs(rc_read(after, xa, ya) - rc_read(before, xa, ya)),
                    abs(rc_read(after, xb, yb) - rc_read(after, xa, ya)),
                )
            )
        check(
            "Re-bake Changed: a cell written in place meets its held neighbour "
            "ONE-SIDED -- the held cell keeps every texel, the new one meets it",
            result.in_place == [p1]
            and len(pairs) > 0
            and np.array_equal(rc_cell(after, p0), rc_cell(before, p0))
            and all(held < 1e-6 and step < 0.02 for held, step in seam)
            and abs(float(np.median(rc_cell(after, p1))) - 3.0) < 1e-3
            and abs(float(np.median(rc_cell(after, p0))) - 1.0) < 1e-3,
            (result.in_place, len(pairs), seam[:4]),
        )

        rc_reset()
        a, b, c = rc_crates(spacing=1.5)
        far = rc_crates(names=("rcFar",), mat=rc_material("rcFarMat", "Far"))[0]
        bpy.data.objects[far].location.x = 40.0
        bpy.context.view_layer.update()
        rc_bake([a, b, c, far])
        bpy.data.objects.remove(bpy.data.objects[c], do_unlink=True)
        report = rc_report()
        pruned_first = report.targets == (b,) and report.neighbours.get(b) == (c,)
        rc_bake([far])
        kept = LightmapRecords.bake_state()["objects"].get(c, {}).get("removed_at")
        still = rc_report().targets
        rc_bake([a, b, far])
        check(
            "Re-bake Changed: what leaves stales its neighbours, and is remembered "
            "until every map within its reach was baked after it left",
            pruned_first
            and kept is not None
            and still == (b,)
            and c not in LightmapRecords.bake_state()["objects"]
            and rc_report().targets == (),
            (report.lines(), kept, still),
        )

        rc_reset()
        names = rc_crates()
        rc_bake(names)
        result = rc_rebake()
        check(
            "Re-bake Changed: nothing changed bakes nothing, and says so",
            result.maps == {}
            and result.refused == "Nothing changed since the last bake."
            and rc_renders == sorted(names),
            (result.refused, rc_renders),
        )

        rc_reset()
        a, b, c = rc_crates()
        rc_bake([a, b, c])
        LightmapRecords.revert([a])
        partial = a in LightmapRecords.bake_state()["objects"]
        LightmapRecords.revert()
        check(
            "Re-bake Changed: a whole-file revert clears the record (a partial "
            "one keeps it)",
            partial and LightmapRecords.bake_state()["objects"] == {},
        )

        rc_reset()
        a, b = rc_crates(names=("rcA", "rcB"))
        rc_bake([a, b])
        rc_src = bpy.data.objects[a]
        rc_twin = rc_src.copy()
        rc_twin.data = rc_src.data.copy()
        rc_twin.name = "rcTwin"
        bpy.context.collection.objects.link(rc_twin)
        bpy.context.view_layer.update()
        LightmapRecords.transfer_lightmaps([rc_twin], rc_src)
        state = LightmapRecords.bake_state()["objects"]
        check(
            "Re-bake Changed: a transferred lightmap inherits its source's bake "
            "(rebound: its cell too)",
            all(
                state.get("rcTwin", {}).get(k) == state[a][k]
                for k in ("baked_at", "cell", "lighting", "packing")
            ),
            {k: (state.get("rcTwin", {}).get(k), state[a][k]) for k in ("cell",)},
        )

        # The Maya bridge's template bakes through whatever blendertk the other
        # DCC has installed: the public call keeps its signature, and the
        # private one hands back each object's cell for the record.
        rc_reset()
        names = rc_crates()
        rc_done = rc_baker()._bake_atlas(names, output_dir=rc_out)
        check(
            "bake_atlas keeps its public signature; _bake_atlas returns the cells",
            list(inspect.signature(LightmapBaker.bake_atlas).parameters)
            == ["self", "objects", "output_dir", "prefix", "suffix", "claims", "kwargs"]
            and set(rc_done.packed) == set(names)
            and set(rc_done.cells) == set(names)
            and all(
                cell["packing"] == "atlas"
                and cell["size"] == RC_RES
                and len(cell["cell"]) == 4
                and len(cell["extent"]) == 2
                for cell in rc_done.cells.values()
            ),
            rc_done.cells,
        )

        # -- the panel's half: the Manage page (mirror of TestChangedScope) --
        if panel_qt is None:

            class _RcWorkflow:
                instances = []
                report = ptk.StaleReport()
                _scope_lights = staticmethod(LightmapBaker._scope_lights)

                def __init__(self, **kwargs):
                    self.kwargs, self.calls = kwargs, []
                    self.resolution = kwargs.get("resolution")
                    _RcWorkflow.instances.append(self)

                folders = {}

                def stale_report(
                    self, objects=None, reach=None, output_dir=None, relocate=False
                ):
                    self.calls.append(("stale_report",))
                    self.stale_output_dir = output_dir
                    return _RcWorkflow.report

                def bake_maps(self, report=None, output_dir=None, relocate=False):
                    report = report or _RcWorkflow.report
                    # One map, read by what the report re-bakes.
                    return report.map_rows(
                        [{"map": RC_ATLAS, "path": os.path.join(rc_out, RC_ATLAS),
                          "folder": "lm", "objects": list(report.targets) or ["csCube"]}]
                    )

                def rebake_report(self, objects=None, output_dir=None, relocate=False):
                    self.calls.append(("rebake_report",))
                    return _RcWorkflow.report

                def combine(self, maps=None, size=None, merge_materials=True, on_progress=None):
                    self.calls.append(("combine", tuple(maps or ())))
                    return _RcResult(maps={"rcS": os.path.join(rc_out, "S_Lightmap.exr")})

                def map_folders(self, objects=None):
                    return dict(_RcWorkflow.folders)

                def bake_breakdown(self, output_dir=None, relocate=False):
                    self.calls.append(("bake_breakdown",))
                    return ptk.ReportDoc().heading("Lightmap Bake State", 1)

                def rebake(self, objects=None, **kwargs):
                    self.calls.append(("rebake",))
                    self.rebake_objects = list(objects or [])
                    return self.rebake_changed(_recorded=False, **kwargs)

                def revert(self, objects=None):
                    self.calls.append(("revert", tuple(objects or ())))
                    return list(objects or ())

                def track(self, objects=None):
                    self.calls.append(("track", tuple(objects or ())))
                    return list(objects or ())

                def rebake_changed(self, _recorded=True, **kwargs):
                    if _recorded:
                        self.calls.append(("rebake_changed",))
                    self.rebake_kwargs = kwargs
                    result = _RcResult(report=_RcWorkflow.report)
                    if not _RcWorkflow.report.targets:
                        result.refused = _RcWorkflow.report.summary()
                        return result
                    for name in _RcWorkflow.report.targets:
                        result.maps[name] = os.path.join(kwargs["output_dir"], RC_ATLAS)
                        result.rects[name] = [1.0, 1.0, 0.0, 0.0]
                    result.in_place = list(_RcWorkflow.report.in_place)
                    return result

            rc_reset()
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            rc_cube = bpy.context.active_object
            rc_cube.name = "csCube"
            moved = ptk.StaleReport(
                targets=("csCube",),
                stale={"csCube": (_R.MOVED,)},
                in_place=("csCube",),
            )
            rc_real_workflow = slots_mod.LightmapBaker
            slots_mod.LightmapBaker = _RcWorkflow
            try:
                _RcWorkflow.report = moved
                check(
                    "Scope bakes fresh (Selected, Visible, Scene); Changed is the "
                    "Manage page's, beside it",
                    LightmapBakerSlots._SCOPE_LABELS == ("Selected", "Visible", "Scene")
                    and LightmapBakerSlots._PAGES == ("Bake", "Manage"),
                    LightmapBakerSlots._SCOPE_LABELS,
                )

                _RcWorkflow.instances = []
                dry = _panel(page="Manage", res=2048)
                dry._output_dir = lambda: rc_out
                dry.cmb_page(1)
                dry_baker = _RcWorkflow.instances[-1] if _RcWorkflow.instances else None
                _RcWorkflow.instances = []
                bake_page = _panel(page="Bake")
                bake_page.cmb_page(0)
                check(
                    "the Manage page writes the dry run to the footer, with the "
                    "panel's dials and the folder Bake writes to; the Bake page "
                    "reads nothing",
                    dry.ui.footer.text() == "1 changed"
                    and dry_baker is not None
                    and dry_baker.resolution == 2048
                    and dry_baker.calls == [("stale_report",)]
                    and dry_baker.stale_output_dir == rc_out
                    and _RcWorkflow.instances == [],
                    (dry.ui.footer.text(), getattr(dry_baker, "calls", None)),
                )

                def rc_redo_changed(panel):
                    """Select: Changed, then Re-Bake: how the Manage page redoes
                    what changed."""
                    panel.select_by_state("changed")
                    panel.rebake_selected()

                bpy.ops.object.select_all(action="DESELECT")
                _RcWorkflow.instances = []
                run = _panel(page="Manage", res=512)
                run._output_dir = lambda: rc_out
                run.select_by_state("changed")
                rc_picked = sorted(o.name for o in bpy.context.selected_objects)
                run.rebake_selected()
                run_baker = next(  # not the Manage table's refresh after it
                    (b for b in _RcWorkflow.instances if ("rebake",) in b.calls),
                    None,
                )
                check(
                    "Select: Changed, then Re-Bake, re-bakes exactly the changed with the "
                    "panel's dials",
                    rc_picked == ["csCube"]
                    and run_baker is not None
                    and run_baker.resolution == 512
                    and run_baker.rebake_objects == ["csCube"]
                    and run_baker.rebake_kwargs.get("suffix") == "_Lightmap"
                    and run_baker.rebake_kwargs.get("relocate") is False
                    and "1 in place" in run.ui.footer.text(),
                    (rc_picked, getattr(run_baker, "calls", None), run.ui.footer.text()),
                )

                _RcWorkflow.report = ptk.StaleReport()
                idle = _panel(page="Manage")
                idle._output_dir = lambda: rc_out
                # A pick that finds nothing clears the last pick (a user
                # request, 2026-10-23): csCube selected going in.
                _idle_layer = CoreUtils._active_view_layer()
                bpy.data.objects["csCube"].select_set(True, view_layer=_idle_layer)
                idle.select_by_state("changed")
                _idle_left = [o.name for o in _idle_layer.objects if o.select_get(view_layer=_idle_layer)]
                check(
                    "...with nothing changed Select: Changed says so, and clears "
                    "the last pick",
                    idle.ui.footer.text() == "No changed maps." and _idle_left == [],
                    (idle.ui.footer.text(), _idle_left),
                )

                text = _panel(packing="atlas")._bake_report(
                    _RcResult(
                        maps={"csCube": os.path.join(rc_out, RC_ATLAS)},
                        rects={"csCube": [1.0, 1.0, 0.0, 0.0]},
                        repacked={RC_ATLAS: "rcA: its cell no longer fits"},
                    )
                )
                check(
                    "the report counts the atlases re-packed, short (the reasons "
                    "were the confirmation's)",
                    "1 atlas" in text and "1 re-packed" in text,
                    text,
                )

                def rc_rebaker(call="rebake"):
                    found = [b for b in _RcWorkflow.instances if (call,) in b.calls]
                    return found[-1] if found else None

                # A re-pack bringing in more than it re-bakes for asks first.
                _RcWorkflow.report = ptk.StaleReport(
                    targets=("csCube",),
                    stale={"csCube": (_R.MOVED,)},
                    repack={RC_ATLAS: "csCube: its cell no longer fits"},
                    joining=("rcJ1", "rcJ2"),
                )
                _RcWorkflow.instances = []
                asked = []
                ask = _panel(page="Manage")
                ask._output_dir = lambda: rc_out
                ask.sb = SimpleNamespace(
                    message_box=lambda q, *b, **k: asked.append((q, b)) or "Cancel"
                )
                rc_redo_changed(ask)
                cancelled = rc_rebaker() is None
                ask.sb = SimpleNamespace(message_box=lambda *a, **k: "Bake All")
                rc_redo_changed(ask)
                check(
                    "Re-Bake asks before a re-pack more than doubles the bake",
                    cancelled
                    and asked
                    and "3 objects" in asked[0][0]
                    and "its cell no longer fits" in asked[0][0]
                    and rc_rebaker() is not None,
                    asked,
                )

                # The panel's dials are not the bake's (a fresh panel, not the
                # preset): every map re-bakes -- asked first.
                _RcWorkflow.report = ptk.StaleReport(
                    targets=("csCube",),
                    stale={"csCube": (_R.LIGHTING,)},
                    lighting=(ptk.BakeBaseline.SETTINGS,),
                    in_place=("csCube",),
                )
                _RcWorkflow.instances = []
                asked = []
                dials = _panel(page="Manage")
                dials._output_dir = lambda: rc_out
                dials.sb = SimpleNamespace(
                    message_box=lambda q, *b, **k: asked.append(q) or "Cancel"
                )
                rc_redo_changed(dials)
                check(
                    "Re-Bake asks when the panel's dials are not the bake's",
                    asked
                    and "Bounces" in asked[0]
                    and "preset" in asked[0]
                    and rc_rebaker() is None,
                    asked,
                )

                # An Output Directory typed in, where the maps do not live: Keep or Move.
                _RcWorkflow.report = moved
                _RcWorkflow.folders = {"C:/proj/sourceimages": [RC_ATLAS]}
                where = _panel(page="Manage")
                where.ui.txt_output_dir.setText("C:/bakes/lm")
                where._output_dir = lambda: "C:/bakes/lm"
                answers = {}
                for answer in ("Keep", "Move"):
                    _RcWorkflow.instances = []
                    rc_box = _mock.Mock(return_value=answer)
                    where.sb = SimpleNamespace(message_box=rc_box)
                    rc_redo_changed(where)
                    answers[answer] = (
                        rc_box.call_args.args[1:],
                        rc_rebaker().rebake_kwargs.get("relocate"),
                    )
                _RcWorkflow.folders = {}
                check(
                    "Re-Bake asks Keep or Move when its maps live elsewhere",
                    answers["Keep"] == (("Keep", "Move", "Cancel"), False)
                    and answers["Move"][1] is True,
                    answers,
                )

                state = _panel()
                rc_view = _mock.Mock()
                state.sb = SimpleNamespace(text_view_dialog=rc_view)
                state.show_bake_state()
                check(
                    "Bake State shows the breakdown in a viewer whose links act",
                    rc_view.call_args is not None
                    and rc_view.call_args.kwargs.get("title") == "Lightmap Bake State"
                    and callable(rc_view.call_args.kwargs.get("link_handler")),
                    getattr(rc_view, "call_args", None),
                )
                rc_window = rc_view.return_value
                rc_view.call_args.kwargs["on_refresh"]()  # the header's button
                check(
                    "Bake State reads itself again on its header's refresh button, "
                    "no footer Refresh",
                    "Refresh" not in rc_view.call_args.args
                    and rc_window.setText.called,
                    rc_view.call_args.args,
                )

                # Fix All (ptk.StaleReport.fix_plan): a missing map no re-bake
                # renders reverted, each split no re-bake mends combined, the
                # untracked tracked as they stand, then what changed re-bakes --
                # the render last; the footer one short line.
                _RcWorkflow.report = dataclasses.replace(
                    moved,
                    untracked=("rcU",),
                    unbakeable=("rcH",),
                    split_materials={"S_LM.exr": ("sMat", "sMatDup")},
                )
                rc_fix_rows = (
                    moved.map_rows(
                        [{"map": RC_ATLAS, "path": os.path.join(rc_out, RC_ATLAS),
                          "folder": "lm", "objects": ["csCube"]}]
                    )
                    + ptk.StaleReport(split={"S_LM.exr": ("S_LM.exr", "S_LM_1.exr")})
                    .map_rows([{"map": "S_LM.exr", "path": "C:/p/S_LM.exr",
                                "folder": "lm", "objects": ["rcS"]}])
                    + ptk.StaleReport(untracked=("rcU",))
                    .map_rows([{"map": "U_LM.exr", "path": "C:/p/U_LM.exr",
                                "folder": "lm", "objects": ["rcU"]}])
                    + ptk.StaleReport(unbakeable=("rcH",))
                    .map_rows([{"map": "H_LM.exr", "path": None,
                                "folder": "lm", "objects": ["rcH"]}])
                )

                class _FixSb:
                    def __init__(self):
                        self.asked = []

                    def confirm(self, question, yes="Yes", no="No"):
                        self.asked.append(question)
                        return yes == "Fix All"

                    def message_box(self, question, *buttons, **kwargs):
                        self.asked.append(question)
                        return "Cancel"

                _RcWorkflow.instances = []
                fixer = _panel(page="Manage")
                fixer._output_dir = lambda: rc_out
                fixer._fill_maps = lambda rows, report: setattr(
                    fixer, "_map_rows", {row["map"]: row for row in rows}
                )
                fixer.sb = _FixSb()

                def rc_steps():
                    return [
                        c
                        for b in _RcWorkflow.instances
                        for c in b.calls
                        if c[0] in ("revert", "combine", "track", "rebake_changed")
                    ]

                # Review 2026-10-24 (mirror of mayatk's): the steps that render
                # nothing are one undo step.
                rc_chunks = []

                @_ctx.contextmanager
                def rc_chunk(name=""):
                    rc_chunks.append((name, len(rc_steps())))
                    yield
                    rc_chunks.append((f"/{name}", len(rc_steps())))

                with _mock.patch.object(
                    _RcWorkflow, "bake_maps", lambda self, r=None, **k: rc_fix_rows
                ), _mock.patch.object(slots_mod.CoreUtils, "undo_chunk", rc_chunk):
                    fixer.fix_all()
                rc_fix_steps = rc_steps()
                check(
                    "Fix All reverts, combines, tracks, then re-bakes -- no row left; "
                    "the footer one short line",
                    fixer.sb.asked
                    and all(
                        w in fixer.sb.asked[0]
                        for w in ("Take the lightmap off 1 object", "Combine 1 split map",
                                  "Start tracking 1 untracked object",
                                  "Re-bake 1 changed object")
                    )
                    and rc_fix_steps
                    == [("revert", ("rcH",)), ("combine", ("S_LM.exr",)),
                        ("track", ("rcU",)), ("rebake_changed",)]
                    and fixer.ui.footer.text()
                    == "Fix All: 1 reverted, 1 combined, 1 tracked, 1 re-baked.",
                    (fixer.sb.asked, rc_fix_steps, fixer.ui.footer.text()),
                )
                check(
                    "Fix All names the merge its Combine makes, and the steps that "
                    "render nothing are one undo step",
                    fixer.sb.asked
                    and "merges the duplicate materials (sMat, sMatDup)" in fixer.sb.asked[0]
                    and rc_chunks == [("Fix All", 0), ("/Fix All", 3)],
                    (fixer.sb.asked[:1], rc_chunks),
                )
                # A refused step is counted as not done and the rest run; the
                # footer points at Blender's console, not Maya's Script Editor.
                rc_refused = _RcResult(refused="Nothing to combine: no map is split.")
                with _mock.patch.object(
                    _RcWorkflow, "bake_maps", lambda self, r=None, **k: rc_fix_rows
                ), _mock.patch.object(
                    _RcWorkflow, "combine", lambda self, *a, **k: rc_refused
                ):
                    fixer.fix_all()
                check(
                    "a refused Combine is counted as not done, the console named",
                    fixer.ui.footer.text()
                    == "Fix All: 1 reverted, 1 not combined, 1 tracked, 1 re-baked "
                    "(see the console).",
                    fixer.ui.footer.text(),
                )
                # Review 2026-10-24: a cancel stops Fix All -- it tracked and
                # re-baked on after the artist said stop.
                _RcWorkflow.instances = []
                rc_stop = _RcResult(refused="Combine cancelled: nothing written.", cancelled=True)
                with _mock.patch.object(
                    _RcWorkflow, "bake_maps", lambda self, r=None, **k: rc_fix_rows
                ), _mock.patch.object(_RcWorkflow, "combine", lambda self, *a, **k: rc_stop):
                    fixer.fix_all()
                check(
                    "a cancelled Combine stops Fix All",
                    rc_steps() == [("revert", ("rcH",))]
                    and fixer.ui.footer.text()
                    == "Fix All cancelled: 1 reverted, 1 not combined.",
                    (rc_steps(), fixer.ui.footer.text()),
                )
                _RcWorkflow.report = moved

                # Review 2026-10-24 (mirror of mayatk's): every Undo/Redo ran the
                # Manage page's whole dry run, once per step of a held Ctrl+Z.
                class _RcTimer:
                    made = []

                    def __init__(self, _parent=None):
                        self.starts, self.slot = [], None
                        self.timeout = SimpleNamespace(connect=self._connect)
                        _RcTimer.made.append(self)

                    def _connect(self, slot):
                        self.slot = slot

                    def setSingleShot(self, _on):
                        pass

                    def start(self, ms):
                        self.starts.append(ms)

                undo = _panel(page="Manage")
                undo.ui.isVisible = lambda: True
                undo.sb = SimpleNamespace(QtCore=SimpleNamespace(QTimer=_RcTimer))
                rc_rows, rc_reads = [], []
                undo._refresh_set_row = rc_rows.append
                undo._refresh_manage = lambda footer=True: rc_reads.append(footer)
                rc_undo_error = None
                try:
                    undo._scene_undone()
                    undo._scene_undone()
                except Exception as error:  # the defect: no such step
                    rc_undo_error = error
                rc_read_before = list(rc_reads)
                if _RcTimer.made and _RcTimer.made[0].slot:
                    _RcTimer.made[0].slot()
                check(
                    "an Undo reads the set rows now and the Manage table once it settles",
                    rc_undo_error is None
                    and rc_rows[: 2 * len(slots_mod.LightmapBakerSlots._SET_ROWS)]
                    == list(slots_mod.LightmapBakerSlots._SET_ROWS) * 2
                    and rc_read_before == []
                    and len(_RcTimer.made) == 1
                    and len(_RcTimer.made[0].starts) == 2
                    and rc_reads == [True],
                    (repr(rc_undo_error), rc_read_before, rc_reads, len(_RcTimer.made)),
                )
                try:  # the shared panel's own visibility back for what follows
                    del undo.ui.isVisible
                except AttributeError:
                    pass

                _RcWorkflow.instances = []
                bpy.ops.object.select_all(action="DESELECT")
                rc_cube.select_set(True)
                again = _panel()
                again._output_dir = lambda: rc_out
                again.rebake_selected()
                check(
                    "Re-Bake re-bakes the selection as baked",
                    rc_rebaker("rebake") is not None
                    and rc_rebaker("rebake").rebake_objects == ["csCube"]
                    and "1 in place" in again.ui.footer.text(),
                    again.ui.footer.text(),
                )

            finally:
                slots_mod.LightmapBaker = rc_real_workflow
    finally:
        rc_patch.stop()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        rc_artifacts.__exit__(None, None, None)

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
