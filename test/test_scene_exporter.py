"""Scene Exporter engine test — Blender port of mayatk's ``test_scene_exporter.py``, scoped to
the FBX export-option preset feature this port closed (``SceneExporter``'s ``cmb000``
add/delete/open-directory/edit parity gap; the task/check pipeline itself is covered by
``test_smart_bake.py``'s ``_run_task_manager_wiring_checks``).

Needs **bpy, not Qt** — it drives ``SceneExporter``'s preset API (``pythontk.PresetStore``-
backed named JSON dicts of ``export_scene.fbx`` kwargs; see ``_scene_exporter.py``'s module
docstring for why this design was picked over Blender's native operator-preset system) directly,
then proves a saved preset's kwargs actually reach — and are accepted by — a real
``bpy.ops.export_scene.fbx`` call through :meth:`SceneExporter.perform_export`.

The Slots-layer button handlers (``b003``/``b004``/``b007``/``b008`` in
``scene_exporter_slots.py``) are thin Qt/OS glue over this same engine API (a name-prompt dialog,
then ``save_fbx_preset``/``delete_fbx_preset``/``fbx_preset_dir``/``fbx_preset_path``, one of
which ``os.startfile``s a real Explorer window) — exercising the engine calls they delegate to
is the meaningful, headlessly-testable surface; spinning up real widgets just to click a button
that calls the same method adds no coverage, and driving ``os.startfile`` in an automated suite
would pop OS windows.

Run: blender --background --factory-startup --python blendertk/test/test_scene_exporter.py
"""
import sys
import os
import json
import shutil
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Isolate PresetStore's user tier in a scratch dir for this run — never touch the real
# %LOCALAPPDATA%/uitk store (pythontk.core_utils.user_config.user_config_root honors this).
_PRESETS_ROOT = tempfile.mkdtemp(prefix="btk_scnexp_presets_")
os.environ["UITK_PRESETS_ROOT"] = _PRESETS_ROOT

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    from blendertk.env_utils.scene_exporter._scene_exporter import (
        SceneExporter,
        _DEFAULT_FBX_OPTIONS,
    )

    def reset_scene():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    tmp = tempfile.mkdtemp(prefix="btk_scnexp_")

    # ---- built-in "default" preset is discoverable + matches _DEFAULT_FBX_OPTIONS ----------
    names = SceneExporter.list_fbx_presets()
    check("list_fbx_presets() includes the shipped 'default' built-in", "default" in names, f"{names}")

    default_path = SceneExporter.fbx_preset_path("default")
    check(
        "fbx_preset_path resolves the built-in default.json",
        bool(default_path) and os.path.isfile(default_path) and default_path.endswith("default.json"),
        f"{default_path}",
    )
    with open(default_path, "r", encoding="utf-8") as fh:
        on_disk_default = json.load(fh)
    check(
        "shipped default.json matches _DEFAULT_FBX_OPTIONS",
        on_disk_default == _DEFAULT_FBX_OPTIONS,
        f"{on_disk_default} != {_DEFAULT_FBX_OPTIONS}",
    )

    # ---- save_fbx_preset seeds from _DEFAULT_FBX_OPTIONS when options=None -----------------
    default_copy_path = SceneExporter.save_fbx_preset("my_default_copy")
    check(
        "save_fbx_preset(options=None) seeds from _DEFAULT_FBX_OPTIONS",
        os.path.isfile(default_copy_path)
        and SceneExporter._preset_store().load("my_default_copy") == _DEFAULT_FBX_OPTIONS,
    )

    # ---- save a real override preset + list/tier resolution --------------------------------
    SceneExporter.save_fbx_preset("lo_poly", {"bake_anim": False, "global_scale": 2.0})
    names = SceneExporter.list_fbx_presets()
    check(
        "list_fbx_presets() includes user-saved presets alongside the built-in",
        {"default", "my_default_copy", "lo_poly"} <= set(names),
        f"{names}",
    )
    check(
        "PresetStore reports 'lo_poly' as a user-tier preset",
        SceneExporter._preset_store().source("lo_poly") == "user",
    )
    check(
        "PresetStore reports 'default' as a built-in-tier preset (not yet shadowed)",
        SceneExporter._preset_store().source("default") == "builtin",
    )

    # ---- load + verify: partial override merges OVER the built-in defaults -----------------
    exp = SceneExporter()
    resolved = exp.load_fbx_export_preset("lo_poly", verify=True)
    check(
        "load_fbx_export_preset merges a partial preset over the built-in defaults",
        resolved["bake_anim"] is False
        and resolved["global_scale"] == 2.0
        and resolved["mesh_smooth_type"] == _DEFAULT_FBX_OPTIONS["mesh_smooth_type"]
        and resolved["embed_textures"] == _DEFAULT_FBX_OPTIONS["embed_textures"],
        f"{resolved}",
    )

    # ---- clearing (None) reverts to the built-in defaults exactly ---------------------------
    exp.load_fbx_export_preset(None)
    check(
        "load_fbx_export_preset(None) clears back to the built-in defaults",
        exp.verify_fbx_preset() == _DEFAULT_FBX_OPTIONS,
        f"{exp.verify_fbx_preset()}",
    )

    # ---- unknown preset name raises RuntimeError (not a silent no-op) ----------------------
    try:
        exp.load_fbx_export_preset("does_not_exist_xyz")
        check("load_fbx_export_preset(unknown name) raises RuntimeError", False)
    except RuntimeError:
        check("load_fbx_export_preset(unknown name) raises RuntimeError", True)

    # ---- a user preset shadows a built-in of the same name ("duplicate to edit") -----------
    SceneExporter.save_fbx_preset("default", {"path_mode": "STRIP"})
    check(
        "saving 'default' as a user preset shadows the built-in",
        SceneExporter._preset_store().source("default") == "user"
        and SceneExporter._preset_store().load("default")["path_mode"] == "STRIP",
    )
    deleted = SceneExporter.delete_fbx_preset("default")
    check(
        "deleting the shadowing 'default' reverts source() back to builtin",
        deleted and SceneExporter._preset_store().source("default") == "builtin",
    )

    # ---- delete_fbx_preset: built-ins are read-only, user presets are removable ------------
    check(
        "delete_fbx_preset on a built-in-only name is a no-op (returns False)",
        SceneExporter.delete_fbx_preset("default") is False,
    )
    check(
        "delete_fbx_preset removes a user preset (returns True) and it drops from list()",
        SceneExporter.delete_fbx_preset("lo_poly") is True
        and "lo_poly" not in SceneExporter.list_fbx_presets(),
    )

    # ---- fbx_preset_dir() is the writable directory presets were actually saved to ---------
    preset_dir = SceneExporter.fbx_preset_dir()
    check(
        "fbx_preset_dir() is the writable dir 'my_default_copy' was saved under",
        os.path.isfile(os.path.join(preset_dir, "my_default_copy.json")),
        f"{preset_dir}",
    )

    # ---- end-to-end: perform_export threads a saved preset's kwargs into a REAL
    # bpy.ops.export_scene.fbx call (the literal parity requirement: presets aren't just
    # stored, they're actually consumed on export). ------------------------------------------
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "PresetExportCube"
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)

    SceneExporter.save_fbx_preset("half_scale", {"global_scale": 0.5, "bake_anim": False})
    exp2 = SceneExporter()
    out_dir = os.path.join(tmp, "export")
    os.makedirs(out_dir, exist_ok=True)
    result = exp2.perform_export(
        export_dir=out_dir,
        objects=[cube],
        preset_name="half_scale",
        output_name="preset_test",
        export_visible=True,
    )
    out_file = os.path.join(out_dir, "preset_test.fbx")
    check(
        "perform_export(preset_name=...) writes the file using the preset's resolved kwargs",
        result is True and os.path.isfile(out_file) and os.path.getsize(out_file) > 0,
        f"result={result} exists={os.path.isfile(out_file)}",
    )

    # ---- an invalid kwarg key in a preset surfaces a clear error (not a silent partial
    # export) -- proves the resolved dict is genuinely forwarded as **kwargs to
    # bpy.ops.export_scene.fbx (a real operator-property KeyError), not merely stored. --------
    SceneExporter.save_fbx_preset("bogus", {"not_a_real_fbx_kwarg_xyz": 123})
    exp3 = SceneExporter()
    try:
        exp3.perform_export(
            export_dir=out_dir,
            objects=[cube],
            preset_name="bogus",
            output_name="bogus_test",
            export_visible=True,
        )
        check("perform_export with an invalid preset kwarg raises", False)
    except RuntimeError:
        check("perform_export with an invalid preset kwarg raises", True)

    # ---- data_export carrier: the metadata channels actually reach the FBX -------------------
    # The whole Blender→Unity metadata hand-off hangs on three defaults working together:
    # use_custom_props=True, an Empty-inclusive object_types, and the export_data_node task
    # folding the carrier into the export set. Prove it end-to-end with a real FBX round-trip
    # (export → wipe scene → re-import) rather than asserting on option dicts alone.
    check(
        "_DEFAULT_FBX_OPTIONS enable the metadata carrier (use_custom_props + EMPTY)",
        _DEFAULT_FBX_OPTIONS.get("use_custom_props") is True
        and "EMPTY" in _DEFAULT_FBX_OPTIONS.get("object_types", []),
        f"{_DEFAULT_FBX_OPTIONS}",
    )

    from blendertk.node_utils.data_nodes import DataNodes
    from blendertk.env_utils.fbx_utils import FbxUtils

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "CarrierExportCube"

    payload = json.dumps({"version": 1, "objects": [{"name": "CarrierExportCube"}]})
    DataNodes.set_export_string("lightmap_metadata", payload)
    check(
        "ensure_export/get_export_node agree on the carrier (mayatk API parity)",
        DataNodes.ensure_export() is DataNodes.get_export_node(create=False),
    )

    # Hide the carrier first — export_data_node must clear hide state, or the
    # use_selection funnel silently drops the metadata.
    carrier = DataNodes.get_export_node(create=False)
    carrier.hide_set(True)
    carrier.hide_select = True

    exp4 = SceneExporter()
    result = exp4.perform_export(
        export_dir=out_dir,
        objects=[cube],
        output_name="carrier_test",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    carrier_file = os.path.join(out_dir, "carrier_test.fbx")
    check(
        "perform_export with export_data_node writes the FBX",
        result is True and os.path.isfile(carrier_file),
        f"result={result} exists={os.path.isfile(carrier_file)}",
    )

    reset_scene()
    imported = FbxUtils.import_fbx(carrier_file, use_custom_props=True)
    imported_carrier = next(
        (o for o in imported if o.name.startswith(DataNodes.EXPORT)), None
    )
    check(
        "data_export Empty rides into the FBX (hidden carrier included)",
        imported_carrier is not None,
        f"imported={[o.name for o in imported]}",
    )
    check(
        "lightmap_metadata survives the FBX round-trip as a user property",
        imported_carrier is not None
        and imported_carrier.get("lightmap_metadata") == payload,
        f"{imported_carrier.get('lightmap_metadata') if imported_carrier else None!r}",
    )

    # No carrier in scene → the task is a clean no-op (still exports the mesh).
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    lone = bpy.context.active_object
    lone.name = "NoCarrierCube"
    exp5 = SceneExporter()
    result = exp5.perform_export(
        export_dir=out_dir,
        objects=[lone],
        output_name="no_carrier_test",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    check(
        "export_data_node no-ops cleanly when the scene has no carrier",
        result is True and os.path.isfile(os.path.join(out_dir, "no_carrier_test.fbx")),
        f"result={result}",
    )

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(_PRESETS_ROOT, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
