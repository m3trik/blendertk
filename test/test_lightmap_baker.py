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
    baker = LightmapBaker.from_preset("quest")
    check("from_preset reads the dials", baker.resolution == 1024 and baker.samples == 4,
          f"{baker.resolution}/{baker.samples}")
    baker = LightmapBaker.from_preset("preview", resolution=64, samples=1)
    check("overrides win over the preset", baker.resolution == 64 and baker.samples == 1)

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
    before_a = uv_bbox(a)  # 0-1 island layout, captured before pack_atlas mutates it

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

    def uv_in_rect(obj, so):
        umin, umax, vmin, vmax = uv_bbox(obj)
        sx, sy, ox, oy = so
        eps = 2e-3
        return (umin >= ox - eps and umax <= ox + sx + eps
                and vmin >= oy - eps and vmax <= oy + sy + eps)

    check("atlas: object A's lightmap UVs repacked into its rect",
          uv_in_rect(a, packed[a.name][1]), f"{uv_bbox(a)} vs {packed[a.name][1]}")
    check("atlas: object B's lightmap UVs repacked into its rect",
          uv_in_rect(b, packed[b.name][1]), f"{uv_bbox(b)} vs {packed[b.name][1]}")

    atlas_baker.commit_lightmap(
        {n: p for n, (p, _so) in packed.items()},
        uv_rects={n: so for n, (_p, so) in packed.items()},
    )
    info_a = json.loads(a[LightmapBaker.LIGHTMAP_INFO_PROP])
    check("atlas: commit records the applied rect as uvRect",
          info_a.get("uvRect") == [float(v) for v in packed[a.name][1]], f"{info_a}")

    atlas_baker.revert([a, b])
    after_a = uv_bbox(a)
    check("atlas: revert exactly restores object A's lightmap UVs (unit-square layout)",
          all(abs(x - y) < 1e-3 for x, y in zip(before_a, after_a)),
          f"before={before_a} after={after_a}")

    # --- fused → unlit commit/revert --------------------------------------
    fused = baker.bake_fused([cube], output_dir=tmp_dir, suffix="_LM")
    check("fused bake produced a map", cube.name in fused)
    src_mat = cube.material_slots[0].material if cube.material_slots else None
    baker.commit_unlit(fused)
    check("commit_unlit stamps + assigns an unlit material",
          LightmapBaker.COMMIT_PROP in cube
          and cube.material_slots[0].material is not src_mat)
    baker.revert([cube])
    check("revert_unlit restores the source material",
          LightmapBaker.COMMIT_PROP not in cube
          and cube.material_slots[0].material is src_mat)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n".join(lines))
ok = bool(lines) and all(l.startswith("OK") for l in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
