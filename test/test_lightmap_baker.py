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
