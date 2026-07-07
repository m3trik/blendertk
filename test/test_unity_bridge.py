"""blendertk UnityBridge feature test (headless Blender -- bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_unity_bridge.py

Mirror of mayatk's ``test_unity_bridge.py``. Covers the Qt-free engine surface (delivery
modes, preflight project-dir validation) and a real end-to-end hand-off: export the
Blender selection to FBX and copy it into a temp Unity project's ``Assets/``. No real
Unity install is needed -- the project is just a folder with an ``Assets/`` dir.

Two things stay module-level-Qt-bound and are therefore covered elsewhere, matching how
the Maya/Rizom bridge slots split their coverage:

* ``unity_bridge_slots.py`` imports ``uitk`` at module level (like ``maya_bridge_slots.py``)
  -- never imported here. The scope-resolution *primitives* it delegates to
  (``btk.selected_objects`` / ``btk.get_visible_geometry`` / a scene-mesh scan) are
  exercised directly below; the Slots' ``_resolve_scope_objects`` wiring itself is covered
  under the workspace ``.venv`` by ``test_blender_ui_handler.py``.
* ``UnityBridge.params_defaults()`` (and therefore the public ``send()``, which calls it via
  ``merge_params``) imports ``parameters.py`` -> ``uitk.bridge.AttributeSpec`` -- also Qt-bound
  (see ``mayatk.env_utils.blender_bridge`` and every other hand-off bridge for the same
  split; ``params_defaults()`` output is asserted under the ``.venv`` instead). The
  end-to-end hand-off below drives the engine's own ``_preflight`` / ``_produce`` /
  ``_deliver`` steps directly with an explicit params dict -- the exact pipeline ``send()``
  runs, minus the Qt-only default-merging step -- so real bpy geometry still exercises the
  real FBX export + real ``unitytk.CopyToAssetsDeliverer`` copy-into-Assets/ logic.
"""
import sys
import os
import shutil
import tempfile
import traceback
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "unitytk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from pythontk.core_utils.app_handoff import HandoffRequest
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge, list_delivery_modes

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def copy_req():
        return HandoffRequest(template="copy_to_assets", mode="send_to")

    # ---- pure (no Blender geometry) -- composition + delivery modes ----------
    check("delivery modes: [(copy_to_assets, '')]",
          list_delivery_modes() == [("copy_to_assets", "")], f"{list_delivery_modes()}")

    check("preflight fails with no project set",
          not UnityBridge().deliverer.preflight(UnityBridge(), copy_req()))

    tmp_no_assets = tempfile.mkdtemp(prefix="unity_bridge_noassets_")
    try:
        br_bad = UnityBridge(project_path=tmp_no_assets)
        check("preflight rejects a folder without Assets/",
              not br_bad.deliverer.preflight(br_bad, copy_req()))
    finally:
        shutil.rmtree(tmp_no_assets, ignore_errors=True)

    # ---- scope-resolution primitives (what the Qt-bound Slots delegates to) --
    # Mirrors UnityBridgeSlots._resolve_scope_objects: "all" -> scene mesh objects,
    # "visible" -> btk.get_visible_geometry(), "selected"/default -> btk.selected_objects().
    reset()
    bpy.ops.mesh.primitive_cube_add()
    visible_cube = bpy.context.active_object
    visible_cube.name = "VisibleCube"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    hidden_cube = bpy.context.active_object
    hidden_cube.name = "HiddenCube"
    hidden_cube.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    visible_cube.select_set(True)

    all_names = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    check("scope 'all' gathers every scene mesh",
          {"VisibleCube", "HiddenCube"} <= all_names, f"{sorted(all_names)}")

    selected_names = {o.name for o in btk.selected_objects()}
    check("scope 'selected' uses the selection only",
          selected_names == {"VisibleCube"}, f"{sorted(selected_names)}")

    visible_names = {o.name for o in btk.get_visible_geometry()}
    check("scope 'visible' excludes hidden objects",
          "VisibleCube" in visible_names and "HiddenCube" not in visible_names,
          f"{sorted(visible_names)}")

    # ---- end-to-end: real FBX export -> copy into a temp Unity project -------
    # Drives _preflight/_produce/_deliver directly (the exact steps send() runs via
    # _run) with an explicit, already-merged params dict -- see the module docstring
    # for why the public send()/merge_params() path itself isn't callable headless.
    def run_handoff(bridge, objects, params):
        request = HandoffRequest(template="copy_to_assets", mode="send_to", params=params)
        resolved = bridge._resolve_objects(objects)
        if bridge.requires_objects and not resolved:
            return None
        if not bridge._preflight(resolved, request):
            return None
        payload = bridge._produce(resolved, request)
        if payload is None:
            return None
        return bridge._deliver(payload, request)

    tmp = Path(tempfile.mkdtemp(prefix="unity_bridge_test_"))
    try:
        project = tmp / "UnityProj"
        (project / "Assets").mkdir(parents=True)
        bridge = UnityBridge(project_path=str(project))

        reset()
        bpy.ops.mesh.primitive_cube_add()
        hero = bpy.context.active_object
        hero.name = "UnityHero"
        bpy.ops.object.select_all(action="DESELECT")
        hero.select_set(True)

        result = run_handoff(bridge, [hero], {"ASSETS_SUBDIR": "Models", "ASSET_NAME": ""})
        check("hand-off returns a result (delivery succeeded)", result is not None)
        if result is not None:
            dest = Path(result["asset"])
            check("asset named after the selected object, under Assets/Models",
                  dest == project / "Assets" / "Models" / "UnityHero.fbx", f"{dest}")
            check("copied FBX exists on disk", dest.is_file())
            check("copied FBX is non-empty", dest.is_file() and dest.stat().st_size > 0)
            check("launched is False (no LAUNCH_MODE requested)", result.get("launched") is False)

        # explicit asset name + default subdir
        bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
        cube2 = bpy.context.active_object
        cube2.name = "UnityCube"
        bpy.ops.object.select_all(action="DESELECT")
        cube2.select_set(True)
        result2 = run_handoff(bridge, [cube2], {"ASSET_NAME": "Custom/Name"})
        if result2 is not None:
            dest2 = Path(result2["asset"])
            check("default subdir 'Imported'; name sanitized",
                  dest2 == project / "Assets" / "Imported" / "Custom_Name.fbx", f"{dest2}")
            check("second copied FBX exists on disk", dest2.is_file())
        else:
            check("hand-off returns a result (explicit name)", False)

        # aborts cleanly with no project / empty selection
        bridge.project_path = str(tmp / "not_a_project")
        check("hand-off aborts when the project path has no Assets/",
              run_handoff(bridge, [cube2], {}) is None)

        bridge.project_path = str(project)
        check("hand-off aborts with an empty selection", run_handoff(bridge, [], {}) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
