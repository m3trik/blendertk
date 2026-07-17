"""blendertk UsdUtils feature test — export (selection / whole-scene / usdz) + import
round-trip over Blender's native USD runtime (mirror of mayatk's ``env_utils.usd``),
plus cross-validation of pythontk's zero-dep USD author/packager against Blender's
bundled ``pxr`` and importer.

Run: blender --background --factory-startup --python blendertk/test/test_usd.py
"""
import sys
import os
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


try:
    import bpy
    import pythontk as ptk
    import blendertk as btk
    from blendertk.env_utils.usd import UsdUtils, export_selection_usd, import_usd

    check("btk.UsdUtils resolves from env_utils.usd", btk.UsdUtils is UsdUtils)
    check("btk.import_usd / export_selection_usd registered",
          btk.import_usd is import_usd and btk.export_selection_usd is export_selection_usd)
    check("EXTENSIONS shared with pythontk", UsdUtils.EXTENSIONS == ptk.USD_EXTENSIONS)

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    tmp = tempfile.mkdtemp(prefix="btk_usd_")

    # ---- UsdUtils.export(objects=...) + import round-trip -------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "UsdExportCube"

    out = os.path.join(tmp, "rt.usdc")
    written = UsdUtils.export(filepath=out, objects=[cube])
    check("UsdUtils.export writes the file",
          written == out and os.path.isfile(out) and os.path.getsize(out) > 0)
    check("pythontk sniffs the export as crate", ptk.UsdFile.sniff(out) == "usdc")

    reset()
    created = UsdUtils.import_usd(out)
    check("import_usd returns the created objects", len(created) >= 1,
          f"{[o.name for o in created]}")
    check("import_usd adds a mesh to the scene",
          any(o.type == "MESH" for o in bpy.data.objects))

    # ---- .usd auto-append + parent-dir creation -----------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    nested = os.path.join(tmp, "sub", "dir", "noext")  # no extension, missing dirs
    w2 = UsdUtils.export(filepath=nested, objects=[bpy.context.active_object])
    check("export appends .usd and creates parent dirs",
          w2 == nested + ".usd" and os.path.isfile(nested + ".usd"))

    # ---- selection_only=False exports the whole scene -----------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    bpy.ops.object.select_all(action="DESELECT")  # nothing selected
    all_out = os.path.join(tmp, "all.usda")
    written_all = UsdUtils.export(filepath=all_out, selection_only=False)
    check("export(selection_only=False) ignores selection + writes",
          written_all == all_out and os.path.isfile(all_out))
    check("usda export is a text layer", ptk.UsdFile.sniff(all_out) == "usda")
    reset()
    created_all = UsdUtils.import_usd(all_out)
    check("whole-scene export round-trips both meshes",
          sum(1 for o in created_all if o.type == "MESH") == 2,
          f"{[o.name for o in created_all]}")

    # ---- selection export with nothing selected raises ----------------------
    reset()
    try:
        UsdUtils.export(filepath=os.path.join(tmp, "no_sel.usd"))
        check("export with empty selection -> RuntimeError", False)
    except RuntimeError:
        check("export with empty selection -> RuntimeError", True)

    # ---- unknown option is dropped, not fatal -------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    opt_out = os.path.join(tmp, "opt.usd")
    UsdUtils.export(filepath=opt_out, objects=[bpy.context.active_object],
                    not_a_real_usd_option=True)
    check("unknown usd_export option dropped (export still writes)",
          os.path.isfile(opt_out))

    # ---- native .usdz export + spec verification via pythontk ---------------
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add()
    z_out = os.path.join(tmp, "pkg.usdz")
    written_z = UsdUtils.export(filepath=z_out, objects=[bpy.context.active_object])
    z_ok = os.path.isfile(z_out) and os.path.getsize(z_out) > 0
    check("native .usdz export writes a package", z_ok)
    if z_ok:
        report = ptk.UsdzPackager.verify(z_out)
        check("Blender's usdz passes pythontk's spec verifier",
              report["valid"], "; ".join(report["issues"][:3]))
        reset()
        created_z = UsdUtils.import_usd(z_out)
        check("usdz round-trips back in", any(o.type == "MESH" for o in created_z))

    # ---- import_usd missing file -> FileNotFoundError -----------------------
    try:
        UsdUtils.import_usd(os.path.join(tmp, "does_not_exist.usd"))
        check("import_usd missing file -> FileNotFoundError", False)
    except FileNotFoundError:
        check("import_usd missing file -> FileNotFoundError", True)

    # ---- CROSS-VALIDATION: pythontk's zero-dep author vs the real runtime ---
    # Author an OBJ->USDZ with NO pxr/DCC, then make Blender's importer and its
    # bundled pxr accept it — the strongest available proof the hand-authored
    # usda + zip-alignment packaging are spec-correct.
    obj_dir = os.path.join(tmp, "objsrc")
    os.makedirs(obj_dir)
    with open(os.path.join(obj_dir, "quad.obj"), "w") as fh:
        fh.write("mtllib quad.mtl\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
                 "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\n"
                 "usemtl m\nf 1/1/1 2/2/1 3/3/1 4/4/1\n")
    with open(os.path.join(obj_dir, "quad.mtl"), "w") as fh:
        fh.write("newmtl m\nmap_Kd quad_d.png\n")
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fccfc0f01f0005050202b8bcf3ed0000000049454e44ae426082"
    )
    with open(os.path.join(obj_dir, "quad_d.png"), "wb") as fh:
        fh.write(png)

    authored = ptk.obj_to_usdz(os.path.join(obj_dir, "quad.obj"))
    check("ptk.obj_to_usdz authors a package", os.path.isfile(authored))

    reset()
    created_a = UsdUtils.import_usd(authored)
    quad = next((o for o in created_a if o.type == "MESH"), None)
    check("Blender imports the zero-dep authored usdz", quad is not None,
          f"{[o.name for o in created_a]}")
    if quad is not None:
        check("authored quad has 4 verts / 1 face",
              len(quad.data.vertices) == 4 and len(quad.data.polygons) == 1)
        check("authored quad has UVs", bool(quad.data.uv_layers))
        check("authored material came through", len(quad.data.materials) >= 1,
              f"{[m.name for m in quad.data.materials if m]}")

    try:
        from pxr import Usd, UsdGeom  # Blender bundles pxr

        stage = Usd.Stage.Open(authored)
        prim = stage.GetDefaultPrim() if stage else None
        check("bundled pxr opens the authored usdz",
              stage is not None and prim and prim.IsValid())
        if stage is not None and prim and prim.IsValid():
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(f"{prim.GetPath()}/Geom"))
            check("pxr reads authored mesh data",
                  len(mesh.GetPointsAttr().Get()) == 4)
    except ImportError:
        lines.append("OK  bundled pxr not present in this Blender (skipped)")

    # ---- bridge USD fast path: import_scene(.usd) skips headless Maya -------
    # A bogus maya_path proves the point: if the bridge tried to convert, the
    # discovery/require step would fail — a USD source must never reach it.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    fp_out = os.path.join(tmp, "fastpath.usdc")
    UsdUtils.export(filepath=fp_out, objects=[bpy.context.active_object])
    reset()
    from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

    imported_fp = MayaSceneImport(
        maya_path="X:/definitely/not/maya.exe", log_level="WARNING"
    ).import_scene(fp_out)
    check("bridge USD fast path imports natively (no Maya involved)",
          any(getattr(o, "type", "") == "MESH" for o in imported_fp),
          f"{[getattr(o, 'name', o) for o in imported_fp]}")

    # ---- via="usd" conversion route: template selection (no Maya needed) ----
    eng = MayaSceneImport(maya_path="X:/definitely/not/maya.exe", log_level="WARNING")
    s_usd = eng.render_script("C:/scenes/s.ma", "C:/tmp/out.usd", via="usd")
    check("render_script(via='usd') targets mayaUSDExport",
          "mayaUSDExport" in s_usd and "C:/scenes/s.ma" in s_usd
          and "C:/tmp/out.usd" in s_usd)
    check("usd template translates ShaderFX, not the surface family",
          "StingrayPBS" in s_usd and "usd_safe_materials" in s_usd)
    s_fbx = eng.render_script("C:/scenes/s.ma", "C:/tmp/out.fbx")
    check("render_script default stays the FBX route", "FBXExport" in s_fbx)
    try:
        eng.render_script("a.ma", "b", via="alembic")
        check("unknown via -> ValueError", False)
    except ValueError:
        check("unknown via -> ValueError", True)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
