"""blendertk operations test: the ``blender`` host (BlenderSceneFiles) and its declarations.

Runs inside the harness Blender; the round trip launches a FRESH ``blender --background``
per call (never this session), the batch runner's contract (an MCP session keeps one
instance instead: pythontk's ``test_session_operation_host.py``). Mirror of mayatk's
``test_maya_scene_files.py``.

Run: blender --background --factory-startup --python blendertk/test/test_blender_scene_files.py
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")


try:
    import bpy
    import pythontk as ptk
    import blendertk.operations as declarations
    from blendertk.env_utils.blender_scene_files import BlenderSceneFiles

    problems = ptk.OperationProvider.problems(declarations.__name__)
    check("the committed catalog is current and within the rules", not problems, problems)

    store = ptk.TempArtifacts.scoped("btk_ops_test")
    scene = os.path.join(store.dir_path(), "ops_scene.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.data.materials.new("M_old").use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=scene)
    catalog = ptk.OperationCatalog.discover(
        [declarations.__name__], hosts={"blender": BlenderSceneFiles.operation_host()}
    )
    args = {"code": 'import bpy\nbpy.data.materials["M_old"].name = "M_new"'}

    plan = catalog.run("blender.python.exec", args, scenes=[scene])
    check(
        "code has no plan: apply runs it",
        plan.status == "failed" and "no dry run" in (plan.error or ""),
        plan.error,
    )
    bpy.ops.wm.open_mainfile(filepath=scene)
    check("a refused plan leaves the file", "M_old" in bpy.data.materials)

    left = catalog.run("blender.python.exec", args, apply=True, scenes=[scene])
    check("apply runs the code", left.ok, left.error)
    bpy.ops.wm.open_mainfile(filepath=scene)
    check("code that saved nothing leaves the file as it was", "M_old" in bpy.data.materials)
    saved = {"code": args["code"] + "\nbpy.ops.wm.save_mainfile()"}
    done = catalog.run("blender.python.exec", saved, apply=True, scenes=[scene])
    check("code that saves keeps its change", done.ok, done.error)
    bpy.ops.wm.open_mainfile(filepath=scene)
    check(
        "the saved file holds the new name",
        "M_new" in bpy.data.materials and "M_old" not in bpy.data.materials,
    )

    # What a kept instance holds: background Blender's is_dirty is never set by
    # a data-API edit and never cleared by a save once set, so the class keeps it.
    files = BlenderSceneFiles()
    files.open([scene])
    check("a file just opened holds nothing", not files.unsaved() and not files.modified())
    files.changed()
    check("a live change is held", files.unsaved() and files.modified() == [bpy.data.filepath])
    copy = os.path.join(store.dir_path(), "ops_copy.blend")
    bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)  # a backup elsewhere
    check("a copy saved elsewhere keeps it", files.unsaved())
    bpy.ops.wm.save_as_mainfile(filepath=scene)  # as code saves it
    check("a save the code makes clears it", not files.unsaved(), bpy.data.is_dirty)
    BlenderSceneFiles()
    check(
        "its handlers are added once",
        list(bpy.app.handlers.save_post).count(BlenderSceneFiles._saved) == 1,
    )
    files.changed()
    bpy.ops.wm.open_mainfile(filepath=scene)
    check("opening a file clears it", not files.unsaved())

    import blendertk as btk

    check("a scene is read by name", btk.EnvUtils.scene_settings(scene="Scene").get("fps", 0) > 0)
    try:
        btk.EnvUtils.scene_settings(scene="Nope")
        check("an unknown scene name is refused", False, "no error")
    except ValueError as exc:
        check("an unknown scene name is refused", "No scene named 'Nope'" in str(exc), exc)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    store.cleanup()
except Exception:
    check("unexpected exception", False, traceback.format_exc())

failed = [line for line in lines if line.startswith("FAIL")]
print("\n".join(lines))
print(f"===RESULT: {'FAIL' if failed else 'PASS'}===")
