"""blendertk FbxUtils feature test — export (selection / whole-scene) + import round-trip
(mirror of mayatk's ``env_utils.fbx_utils.FbxUtils``). ``export_selection_fbx`` selection-only
behavior is covered by ``test_bridges.py``; this exercises the import side + ``selection_only=False``.

Run: blender --background --factory-startup --python blendertk/test/test_fbx_utils.py
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
    import blendertk as btk
    from blendertk.env_utils.fbx_utils import FbxUtils

    check("btk.FbxUtils resolves from env_utils.fbx_utils", btk.FbxUtils is FbxUtils)

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    tmp = tempfile.mkdtemp(prefix="btk_fbx_")

    # ---- FbxUtils.export(objects=...) + import round-trip -------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ExportCube"

    out = os.path.join(tmp, "rt.fbx")
    written = FbxUtils.export(filepath=out, objects=[cube])
    check("FbxUtils.export writes the file",
          written == out and os.path.isfile(out) and os.path.getsize(out) > 0)

    reset()
    check("scene cleared before import", len(bpy.data.objects) == 0)
    created = FbxUtils.import_fbx(out)
    check("import_fbx returns the created objects", len(created) >= 1, f"{[o.name for o in created]}")
    check("import_fbx actually adds a mesh to the scene",
          any(o.type == "MESH" for o in bpy.data.objects))

    # ---- .fbx auto-append + parent-dir creation -----------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    nested = os.path.join(tmp, "sub", "dir", "noext")  # no extension, missing dirs
    w2 = FbxUtils.export(filepath=nested, objects=[bpy.context.active_object])
    check("export appends .fbx and creates parent dirs",
          w2 == nested + ".fbx" and os.path.isfile(nested + ".fbx"))

    # ---- selection_only=False exports the whole scene -----------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    bpy.ops.object.select_all(action="DESELECT")  # nothing selected
    all_out = os.path.join(tmp, "all.fbx")
    written_all = FbxUtils.export(filepath=all_out, selection_only=False)
    check("export(selection_only=False) ignores selection + writes",
          written_all == all_out and os.path.isfile(all_out))
    reset()
    created_all = FbxUtils.import_fbx(all_out)
    check("whole-scene export round-trips both meshes",
          sum(1 for o in created_all if o.type == "MESH") == 2,
          f"{[o.name for o in created_all]}")

    # ---- Scene Exporter contract: the exact kwargs the tentacle slot passes -
    # (object_types set incl. CAMERA/LIGHT/ARMATURE, use_tspace, embed/path_mode).
    reset()
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.object.camera_add(location=(7, 0, 0))
    bpy.ops.object.light_add(type="POINT", location=(0, 7, 0))
    exp_out = os.path.join(tmp, "exporter.fbx")
    written_exp = FbxUtils.export(
        filepath=exp_out,
        selection_only=False,
        object_types={"MESH", "EMPTY", "OTHER", "CAMERA", "LIGHT", "ARMATURE"},
        use_tspace=True,
        path_mode="COPY",
        embed_textures=True,
    )
    check("Scene Exporter kwargs export writes the file",
          written_exp == exp_out and os.path.isfile(exp_out) and os.path.getsize(exp_out) > 0)
    # (Round-trip import of a light is skipped: Blender 5.1's bundled io_scene_fbx
    #  importer raises on lights — CyclesLightSettings.cast_shadow — unrelated to export.)

    # ---- GLB sidecar: the slot's 'Also Export GLB' native glTF call ---------
    glb_out = os.path.join(tmp, "exporter.glb")
    bpy.ops.export_scene.gltf(
        filepath=glb_out, export_format="GLB", use_selection=False,
        export_cameras=True, export_lights=True,
    )
    check("GLB sidecar (export_scene.gltf) writes the file",
          os.path.isfile(glb_out) and os.path.getsize(glb_out) > 0)

    # ---- import_fbx missing file -> FileNotFoundError -----------------------
    try:
        FbxUtils.import_fbx(os.path.join(tmp, "does_not_exist.fbx"))
        check("import_fbx missing file -> FileNotFoundError", False)
    except FileNotFoundError:
        check("import_fbx missing file -> FileNotFoundError", True)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
