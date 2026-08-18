"""Scene Exporter engine test — Blender port of mayatk's ``test_scene_exporter.py``, scoped to
the FBX export-option preset feature this port closed (``SceneExporter``'s ``cmb000`` preset
gap — save/delete/list plus the open-directory/edit slots; the task/check pipeline itself is
covered by ``test_smart_bake.py``'s ``_run_task_manager_wiring_checks``).

Needs **bpy, not Qt** — it drives ``SceneExporter``'s preset API (``pythontk.PresetStore``-
backed named JSON dicts of ``export_scene.fbx`` kwargs; see ``_scene_exporter.py``'s module
docstring for why this design was picked over Blender's native operator-preset system) directly,
then proves a saved preset's kwargs actually reach — and are accepted by — a real
``bpy.ops.export_scene.fbx`` call through :meth:`SceneExporter.perform_export`.

The Slots-layer button handlers (``b007``/``b008`` in ``scene_exporter_slots.py``) are thin
Qt/OS glue over this same engine API (``fbx_preset_dir``/``fbx_preset_path``, each
``os.startfile``-ing a real Explorer window) — exercising the engine calls they delegate to is
the meaningful, headlessly-testable surface; spinning up real widgets just to click a button
that calls the same method adds no coverage, and driving ``os.startfile`` in an automated suite
would pop OS windows.

``save_fbx_preset`` / ``delete_fbx_preset`` are covered below as the *programmatic* preset
surface: the panel's Add/Delete buttons were dropped (2026-08-06) in favour of managing the
preset directory directly through ``b007``, since a preset is a plain JSON file.

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
# uitk is needed even though this suite drives no widgets: ``task_definitions``
# / ``check_definitions`` build their tooltips with ``TooltipFormat`` (lazily
# imported, Qt-free by design), and the block at "task/check definitions" below
# renders every one of them. Same convention as the other uitk-touching suites
# (test_smart_bake, test_hierarchy_sync, test_shots_slots, ...).
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
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
    # INFO, not the WARNING default: verify_fbx_preset's settings report is
    # gated on isEnabledFor(INFO), and a gated report is dead code under a
    # suite that never raises the level — which is how a bad reference inside
    # one ships unnoticed. The checks below call it four times, so this alone
    # keeps that branch executing.
    exp = SceneExporter(log_level="INFO")
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

    # ---- a user preset cannot ship an unreadable carrier -----------------------------------
    # A preset carrying use_custom_props: false (or an object_types without EMPTY)
    # would export the data_export Empty holding nothing — the failure that looks
    # most like success. The write-site guard forces both halves back on whenever
    # the carrier is in the export set, same rule as the hand-off bridges.
    from blendertk.node_utils.data_nodes import DataNodes as _DN

    _carrier = _DN.ensure_export()
    hostile = dict(object_types="MESH", use_custom_props=False)
    exp._force_carrier_readability([_carrier], hostile)
    check(
        "carrier in export set -> hostile preset options repaired",
        hostile["use_custom_props"] is True and "EMPTY" in hostile["object_types"],
        f"{hostile}",
    )
    untouched = dict(use_custom_props=False)
    exp._force_carrier_readability([], untouched)
    check(
        "carrier not in export set -> preset options left alone",
        untouched["use_custom_props"] is False,
    )
    bpy.data.objects.remove(_carrier, do_unlink=True)

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

    # Author a REAL lightmap marker and publish through the producer, exactly
    # like a bake commit: export_data_node now refreshes every producer
    # (FbxUtils.run_export_preparers), so a hand-stamped channel with no scene
    # state behind it would be correctly regenerated away as stale.
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

    cube[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps(
        {"map": "CarrierExportCube_Lightmap.exr", "intensity": 1.0}
    )
    LightmapBaker.refresh_export_metadata()
    payload = DataNodes.get_export_string("lightmap_metadata")
    check(
        "authoring-time publish stamps the carrier from the marker",
        isinstance(payload, str) and "CarrierExportCube" in payload,
        f"{payload!r}",
    )
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

    # The scene-data sidecar records what shipped: decoded carrier channels +
    # exported hierarchy paths (engine-side `_write_scene_data_sidecar`).
    from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar

    sc_data = SceneDataSidecar.read_data(carrier_file) or {}
    sc_md = sc_data.get("lightmap_metadata")
    check(
        "scene-data sidecar written beside the FBX with the decoded channel",
        isinstance(sc_md, dict) and sc_md.get("version") == 1,
        f"{sc_data!r}",
    )
    sc_paths = SceneDataSidecar.read_manifest(carrier_file) or set()
    check(
        "sidecar hierarchy section covers the export set",
        "CarrierExportCube" in sc_paths,
        f"{sorted(sc_paths)}",
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
    check(
        "metadata-free export leaves no sidecar",
        SceneDataSidecar.read_manifest(os.path.join(out_dir, "no_carrier_test.fbx"))
        is None,
    )
    # Carrier present in the scene but NOT in the export set → its channels
    # did not ship, so nothing is recorded (and with nothing else to record,
    # no sidecar at all).
    DataNodes.set_export_string("test_channel", json.dumps({"v": 1}))
    exp5._write_scene_data_sidecar([lone])
    check(
        "carrier outside the export set records no data",
        SceneDataSidecar.read_manifest(os.path.join(out_dir, "no_carrier_test.fbx"))
        is None,
    )

    # ---- keyed-weight curve proxies: staged through the write, gone after --------------------
    # The Blender transport for Emissive Groups' keyable weights: export_data_node
    # stages one transient Empty per keyed group (scale.x carries the weight
    # curve — Blender's FBX exporter can't ship custom-property animation), the
    # proxy rides the write, and perform_export's finally removes it. Prove the
    # full pipeline: FBX carries the animated proxy AND the scene is left clean.
    from blendertk.mat_utils.emissive_groups import EmissiveGroups
    from blendertk.anim_utils._anim_utils import AnimUtils

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    kw = bpy.context.active_object
    kw.name = "KeyedWeightCube"
    EmissiveGroups.add_group("glow", {"KeyedWeightCube": [0]})
    EmissiveGroups.make_weights_keyable(["glow"])
    EmissiveGroups.key_weight("glow", value=1.0, frame=1)
    EmissiveGroups.key_weight("glow", value=0.0, frame=10)

    exp6 = SceneExporter()
    result = exp6.perform_export(
        export_dir=out_dir,
        objects=[kw],
        output_name="keyed_weight_test",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    keyed_file = os.path.join(out_dir, "keyed_weight_test.fbx")
    check(
        "perform_export with a keyed weight writes the FBX",
        result is True and os.path.isfile(keyed_file),
        f"result={result}",
    )
    check(
        "no curve proxy survives in the scene after the export",
        not any(o.get(EmissiveGroups.PROXY_MARKER) for o in bpy.data.objects),
        f"{[o.name for o in bpy.data.objects if o.get(EmissiveGroups.PROXY_MARKER)]}",
    )

    reset_scene()
    imported = FbxUtils.import_fbx(keyed_file, use_custom_props=True)
    iproxy = next(
        (o for o in imported if o.name.startswith("emissiveGroup_glow")), None
    )
    ifc = next(
        (
            f
            for f in AnimUtils.get_fcurves([iproxy] if iproxy else [])
            if f.data_path == "scale" and f.array_index == 0
        ),
        None,
    )
    # Asserted on the curve's SHAPE (starts on, reaches off) rather than on
    # absolute frames: the FBX round-trip rebases a take by one frame, so
    # pinning evaluate(<authored frame>) would be testing Blender's importer.
    ivals = [k.co[1] for k in ifc.keyframe_points] if ifc else []
    check(
        "keyed weight animation rides the FBX on the proxy's scale.x",
        iproxy is not None
        and ivals
        and abs(ivals[0] - 1.0) < 0.01
        and abs(min(ivals) - 0.0) < 0.01,
        f"proxy={iproxy and iproxy.name} first={ivals[:1]} min={min(ivals) if ivals else None}",
    )
    icarrier = next(
        (o for o in imported if o.name.startswith(DataNodes.EXPORT)), None
    )
    manifest_raw = icarrier.get("emissive_groups") if icarrier else None
    check(
        "manifest attr record rides beside the proxy (what Unity joins on)",
        bool(manifest_raw) and '"attr": "emissiveGroup_glow"' in manifest_raw,
        f"{manifest_raw!r}",
    )

    # ---- weight curves keyed OUTSIDE the scene range still ship --------------------------
    # Blender bakes animation over the SCENE frame range, so a proxy keyed past
    # scene.frame_end would export flattened to its extrapolated value. The
    # proxies are staged after set_bake_animation_range has run (and that task is
    # a user checkbox that may be off, as here), so export_data_node widens the
    # range itself — and the widen must be undone with the rest of the staged
    # state once the file is on disk.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    kw2 = bpy.context.active_object
    kw2.name = "LateKeyedCube"
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 250
    EmissiveGroups.add_group("late", {"LateKeyedCube": [0]})
    EmissiveGroups.make_weights_keyable(["late"])
    EmissiveGroups.key_weight("late", value=1.0, frame=300)
    EmissiveGroups.key_weight("late", value=0.0, frame=320)

    exp7 = SceneExporter()
    result = exp7.perform_export(
        export_dir=out_dir,
        objects=[kw2],
        output_name="late_keyed_test",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    late_file = os.path.join(out_dir, "late_keyed_test.fbx")
    check(
        "perform_export with out-of-range keyed weights writes the FBX",
        result is True and os.path.isfile(late_file),
        f"result={result}",
    )
    check(
        "the widened frame range is restored after the write",
        (scene.frame_start, scene.frame_end) == (1, 250),
        f"actual=({scene.frame_start},{scene.frame_end})",
    )

    reset_scene()
    imported = FbxUtils.import_fbx(late_file, use_custom_props=True)
    lproxy = next(
        (o for o in imported if o.name.startswith("emissiveGroup_late")), None
    )
    lfc = next(
        (
            f
            for f in AnimUtils.get_fcurves([lproxy] if lproxy else [])
            if f.data_path == "scale" and f.array_index == 0
        ),
        None,
    )
    # Without the widen the single scene-range take stops at 250, the 300-320
    # ramp never gets sampled, and every baked value is the extrapolated 1.0 —
    # so `min(...) == 0.0` is precisely the regression guard.
    lvals = [k.co[1] for k in lfc.keyframe_points] if lfc else []
    check(
        "keys past scene.frame_end are baked in full, not flattened",
        lproxy is not None
        and lvals
        and abs(lvals[0] - 1.0) < 0.01
        and abs(min(lvals) - 0.0) < 0.01,
        f"proxy={lproxy and lproxy.name} first={lvals[:1]} min={min(lvals) if lvals else None}",
    )
    # Per-action takes are named "<object>|<action>" and are each rebased to
    # frame 1; the single scene-range take is named "…|Scene". Read off the
    # IMPORTED objects (bpy.data.actions still holds authoring leftovers).
    take_names = {
        o.animation_data.action.name
        for o in imported
        if o.animation_data and o.animation_data.action
    }
    check(
        "the weight curve rides ONE scene-range take with the rest of the export "
        "(per-action takes would rebase it to frame 1, silently misaligning it)",
        len(take_names) == 1 and next(iter(take_names)).endswith("|Scene"),
        f"takes={sorted(take_names)}",
    )

    # ---- check_valid_paths is scoped to the textures that actually ship ------------------
    # Bug (mirrored from mayatk): the check walked EVERY FILE image datablock in the
    # .blend, so it failed the export over the World/HDR environment texture and over
    # zero-user images orphaned by a duplicate-material cleanup — neither of which the
    # FBX ever carries. Scope is now `_get_export_images()` (the images feeding the
    # materials assigned to `self.objects`); linked libraries stay whole-file.
    reset_scene()
    for img in list(bpy.data.images):
        if img.source == "FILE":
            bpy.data.images.remove(img)

    tex_dir = os.path.join(tmp, "textures")
    os.makedirs(tex_dir, exist_ok=True)
    good_tex = os.path.join(tex_dir, "assigned.png")
    with open(good_tex, "wb") as fh:
        fh.write(b"PNGDATA")

    bpy.ops.mesh.primitive_cube_add()
    tex_cube = bpy.context.active_object
    tex_cube.name = "PathScopeCube"
    mat = bpy.data.materials.new("PathScopeMat")
    mat.use_nodes = True
    tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(good_tex)
    tex_cube.data.materials.append(mat)

    # An unassigned image with a broken path — the Blender analogue of Maya's
    # skydome HDR / orphaned file node.
    stray = bpy.data.images.new("stray_env_hdr", 1, 1)
    stray.source = "FILE"
    stray.filepath = os.path.join(tex_dir, "machine_shop_8k.hdr")

    # ---- captionless rows must carry a row label (mirrors mayatk) ------------
    # A QCheckBox labels itself via setText and a Separator via title, but a
    # ComboBox / QLineEdit / spin-box row renders as a bare control. These fields
    # ship with a default value so their placeholder is never visible -- without
    # a caption the user just sees "16" with no idea it is a size budget.
    _tm_defs = SceneExporter().task_manager
    _defs = {**_tm_defs.task_definitions, **_tm_defs.check_definitions}
    _captionless = {
        "ComboBox",
        "QLineEdit",
        "SpinBox",
        "DoubleSpinBox",
        "QSpinBox",
        "QDoubleSpinBox",
    }
    _missing = [
        name
        for name, params in _defs.items()
        if params.get("widget_type") in _captionless and not params.get("set_row_label")
    ]
    check(
        "every captionless definition row supplies set_row_label",
        _missing == [],
        f"unlabelled={_missing}",
    )

    # ---- image_paths_scope: the snapshot -> mutate -> restore scope ------------
    # Added: 2026-08-16
    from blendertk.mat_utils._mat_utils import MatUtils as _MU

    _ip_orig = tex_node.image.filepath
    with _MU.image_paths_scope([tex_node.image], new_path="//staged/x.png"):
        _ip_inside = tex_node.image.filepath
    check(
        "image_paths_scope repaths on entry and restores on exit",
        _ip_inside.endswith("x.png") and tex_node.image.filepath == _ip_orig,
        f"inside={_ip_inside} after={tex_node.image.filepath}",
    )
    try:
        with _MU.image_paths_scope([tex_node.image]):
            tex_node.image.filepath = "//elsewhere/y.png"
            raise RuntimeError("body failed halfway")
    except RuntimeError:
        pass
    check(
        "image_paths_scope restores despite a raise in the body",
        tex_node.image.filepath == _ip_orig,
        f"after={tex_node.image.filepath}",
    )

    # ---- Texture Template: check + task keyed off ONE selection (mirrors mayatk) ----
    # The combobox (cmb005) is the single definition; b000 folds it into
    # convert_textures (task phase) and check_material_compatibility (check
    # phase). The check runs post-conversion, judged against the CHOSEN
    # template: a residual MSAO fails a glTF template but is native to (and
    # passes) an HDRP one. Patched at the scene read / MatUpdater so this pins
    # the keying, the pass-through default, and the delegation.
    from blendertk.env_utils import scene_state as _scene_state
    from blendertk.mat_utils import _mat_utils as _mu

    _real_read = _scene_state.SceneState.read
    _real_update = _mu.MatUpdater.update_materials
    try:
        tm_mc = SceneExporter().task_manager

        def _stub(sections):
            """Patch SceneState.read to return *sections* (bound on access)."""
            _scene_state.SceneState.read = classmethod(
                lambda cls, *a, **k: sections
            )

        _stub({"metallic_roughness": {"M": {"metallic": "C:/tex/probe_MSAO.png"}}})
        passed, msgs = tm_mc.check_material_compatibility("glTF 2.0")
        check(
            "check_material_compatibility fails a residual MSAO on a glTF template",
            passed is False
            and any("MSAO" in m for m in msgs)
            and any("probe_MSAO.png" in m for m in msgs),
            f"passed={passed} msgs={msgs}",
        )

        passed, msgs = tm_mc.check_material_compatibility("Unity HDRP")
        check(
            "check_material_compatibility passes the same MSAO on an HDRP template",
            passed is True,
            f"msgs={msgs}",
        )

        _stub({"metallic_roughness": {"M": {"metallic": "C:/tex/probe_ORM.png"}}})
        passed, msgs = tm_mc.check_material_compatibility("glTF 2.0")
        check(
            "check_material_compatibility passes an ORM mask on a glTF template",
            passed is True,
            f"msgs={msgs}",
        )

        # A loose source set must never trip it: an AO or emissive map declares
        # no packing workflow and is not a foreign PACKING.
        _stub(
            {
                "metallic_roughness": {
                    "M": {
                        "metallic": "C:/tex/probe_Metallic.png",
                        "roughness": "C:/tex/probe_Roughness.png",
                        "occlusion": "C:/tex/probe_AO.png",
                    }
                },
                "emissive": {"M": {"texture": "C:/tex/probe_Emissive.png"}},
            }
        )
        passed, msgs = tm_mc.check_material_compatibility("glTF 2.0")
        check(
            "check_material_compatibility passes a loose source set",
            passed is True,
            f"msgs={msgs}",
        )

        # 'As Authored' (falsy template), and a reader failure, both pass.
        passed, msgs = tm_mc.check_material_compatibility(None)
        check(
            "check_material_compatibility no-ops without a template",
            passed is True and msgs == [],
            f"passed={passed} msgs={msgs}",
        )

        def _boom(cls, *a, **k):
            raise RuntimeError("boom")

        _scene_state.SceneState.read = classmethod(_boom)
        passed, msgs = tm_mc.check_material_compatibility("glTF 2.0")
        check(
            "check_material_compatibility survives a scene-read failure",
            passed is True and msgs == [],
            f"passed={passed} msgs={msgs}",
        )

        # convert_textures: delegates to MatUpdater with the template as its
        # config, scoped to the export materials; no-template is a no-op. In
        # write-back mode (Texture Output = Scene Files) the config IS the
        # template — the plain in-place migration.
        calls = []
        _mu.MatUpdater.update_materials = classmethod(
            lambda cls, materials=None, config=None, **k: calls.append(
                (list(materials or []), config)
            )
        )
        tm_ct = SceneExporter().task_manager
        tm_ct.objects = [tex_cube]
        tm_ct._texture_write_back = True
        tm_ct.convert_textures(None)
        check(
            "convert_textures no-ops without a template",
            calls == [],
            f"calls={calls}",
        )
        tm_ct.convert_textures("glTF 2.0")
        check(
            "convert_textures delegates to MatUpdater with the template config",
            len(calls) == 1 and calls[0][1] == "glTF 2.0" and calls[0][0],
            f"calls={calls}",
        )
        check(
            "convert_textures invalidates the material cache",
            tm_ct._cached_materials is None,
            f"cached={tm_ct._cached_materials}",
        )
        check(
            "convert_textures write-back stages no restore (permanent)",
            "convert_textures" not in tm_ct._deferred_restores,
            f"restores={list(tm_ct._deferred_restores)}",
        )

        # Texture Output = Export Copies (the default): the updater writes into
        # this run's staging dir, the images are repathed for the write, and
        # the deferred restore puts every original filepath back and removes
        # a temp staging dir. Added: 2026-08-16
        calls.clear()
        img_orig = tex_node.image.filepath
        seen = {}

        def _fake_repath(cls, materials=None, config=None, **k):
            seen["config"] = config
            tex_node.image.filepath = os.path.join(
                config["move_to_folder"], "PathScope_ORM.png"
            )
            return {}

        _mu.MatUpdater.update_materials = classmethod(_fake_repath)
        tm_st = SceneExporter().task_manager
        tm_st.objects = [tex_cube]
        tm_st._glb_only = True  # temp staging
        tm_st._texture_write_back = False
        tm_st.convert_textures("glTF 2.0")
        cfg = seen.get("config") or {}
        staging = cfg.get("move_to_folder")
        check(
            "convert_textures (Export Copies) runs the updater into a staging dir",
            isinstance(cfg, dict)
            and cfg.get("preset") == "glTF 2.0"
            and bool(staging)
            and os.path.isdir(staging),
            f"config={cfg}",
        )
        check(
            "convert_textures (Export Copies) repaths the export images for the write",
            tex_node.image.filepath != img_orig
            and "convert_textures" in tm_st._deferred_restores,
            f"filepath={tex_node.image.filepath}",
        )
        tm_st.run_deferred_restores()
        check(
            "convert_textures (Export Copies) restores the original paths + temp dir",
            tex_node.image.filepath == img_orig and not os.path.exists(staging),
            f"filepath={tex_node.image.filepath} staging_exists={os.path.exists(staging)}",
        )

        # A MatUpdater exception must not abort the pipeline (TaskFactory
        # re-raises task exceptions): the guard defers to the paired check,
        # which gates cleanly on the actual post-task state.
        def _boom_update(cls, materials=None, config=None, **k):
            raise RuntimeError("unreadable texture")

        _mu.MatUpdater.update_materials = classmethod(_boom_update)
        tm_ct._cached_materials = ["primed"]
        try:
            tm_ct.convert_textures("glTF 2.0")
            raised = False
        except Exception:
            raised = True
        check(
            "convert_textures failure defers to the check (no raise)",
            raised is False and tm_ct._cached_materials is None,
            f"raised={raised} cached={tm_ct._cached_materials}",
        )
    finally:
        _scene_state.SceneState.read = _real_read
        _mu.MatUpdater.update_materials = _real_update

    tm_paths = SceneExporter().task_manager
    tm_paths.objects = [tex_cube]
    passed, msgs = tm_paths.check_valid_paths(True)
    check(
        "check_valid_paths ignores images outside the export materials",
        passed is True,
        f"msgs={msgs}",
    )

    # ... but a genuinely missing map on an export material still fails.
    tex_node.image.filepath = os.path.join(tex_dir, "gone.png")
    tm_paths._cached_materials = None
    passed, msgs = tm_paths.check_valid_paths(True)
    check(
        "check_valid_paths still fails on a missing EXPORT texture",
        passed is False and any("gone" in m or tex_node.image.name in m for m in msgs),
        f"msgs={msgs}",
    )

    # ---- packed + UDIM images in the texture checks (mirrors mayatk's semantics) ----------
    # Bugs: a PACKED image with a stale disk path failed check_valid_paths (and its stale
    # path is still exempt from check_path_length) even though the FBX embeds it from memory; a
    # TILED (UDIM) image was invisible to check_valid_paths entirely (get_image_records is
    # FILE-only), so a deleted tile set passed.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    pu_cube = bpy.context.active_object
    pu_cube.name = "PackedUdimCube"
    pu_mat = bpy.data.materials.new("PackedUdimMat")
    pu_mat.use_nodes = True
    pu_cube.data.materials.append(pu_mat)

    # A real PNG on disk, loaded then PACKED, then its disk copy deleted and the stored
    # path left absolute-and-stale — the embedded bytes are what ship, not the path.
    packed_src = os.path.join(tex_dir, "packed_src.png")
    gen = bpy.data.images.new("packed_gen", 4, 4)
    gen.filepath_raw = packed_src
    gen.file_format = "PNG"
    gen.save()
    bpy.data.images.remove(gen)
    packed_img = bpy.data.images.load(packed_src)
    packed_img.pack()
    os.remove(packed_src)
    packed_img.filepath = os.path.join(tex_dir, "stale_dir", "packed_src.png")
    pu_mat.node_tree.nodes.new("ShaderNodeTexImage").image = packed_img

    tm_pu = SceneExporter().task_manager
    tm_pu.objects = [pu_cube]
    passed, msgs = tm_pu.check_valid_paths(True)
    check(
        "check_valid_paths passes a PACKED image with a stale disk path",
        passed is True,
        f"msgs={msgs}",
    )
    passed, msgs = tm_pu.check_path_length(20)
    check(
        "check_path_length skips a PACKED image (ships embedded)",
        passed is True,
        f"msgs={msgs}",
    )

    # An over-long path fails, and 0 ("OFF") disables the gate entirely.
    long_img = bpy.data.images.new("long_path_img", 4, 4)
    long_img.filepath = os.path.join(tex_dir, *(["d"] * 40), "long.png")
    pu_mat.node_tree.nodes.new("ShaderNodeTexImage").image = long_img
    passed, msgs = tm_pu.check_path_length(60)
    check(
        "check_path_length fails an over-long texture path",
        passed is False and any("long_path_img" in m for m in msgs),
        f"msgs={msgs}",
    )
    passed, msgs = tm_pu.check_path_length(0)
    check(
        "check_path_length OFF disables the gate",
        passed is True,
        f"msgs={msgs}",
    )
    pu_mat.node_tree.nodes.remove(
        next(
            n
            for n in pu_mat.node_tree.nodes
            if getattr(n, "image", None) is long_img
        )
    )
    bpy.data.images.remove(long_img)

    # A TILED (UDIM) image whose tile set does not exist on disk must now FAIL ...
    udim_missing = bpy.data.images.new("udim_missing", 4, 4, tiled=True)
    udim_missing.filepath = os.path.join(tex_dir, "udim_missing.<UDIM>.png")
    pu_mat.node_tree.nodes.new("ShaderNodeTexImage").image = udim_missing
    passed, msgs = tm_pu.check_valid_paths(True)
    check(
        "check_valid_paths fails a TILED image with no tiles on disk",
        passed is False and any("udim_missing" in m for m in msgs),
        f"msgs={msgs}",
    )
    # ... and pass once its first declared tile (1001) exists.
    with open(os.path.join(tex_dir, "udim_missing.1001.png"), "wb") as fh:
        fh.write(b"PNGDATA")
    passed, msgs = tm_pu.check_valid_paths(True)
    check(
        "check_valid_paths passes a TILED image whose first tile exists",
        passed is True,
        f"msgs={msgs}",
    )

    # ---- UDIM size gate: getsize on the raw <UDIM> token path raised OSError into a
    # silent continue, so multi-GB tile sets passed unmeasured. The largest existing
    # tile is now the probe.
    udim_big = bpy.data.images.new("udim_big", 4, 4, tiled=True)
    udim_big.filepath = os.path.join(tex_dir, "udim_big.<UDIM>.png")
    pu_mat.node_tree.nodes.new("ShaderNodeTexImage").image = udim_big
    with open(os.path.join(tex_dir, "udim_big.1001.png"), "wb") as fh:
        fh.write(b"\0" * 1024)  # 1 KB — under the gate
    with open(os.path.join(tex_dir, "udim_big.1002.png"), "wb") as fh:
        fh.write(b"\0" * (2 * 1024 * 1024))  # 2 MB — the tile that must be probed
    passed, msgs = tm_pu.check_texture_file_size(1)  # 1 MB gate
    check(
        "check_texture_file_size probes the largest existing UDIM tile",
        passed is False and any("udim_big.1002" in m for m in msgs),
        f"msgs={msgs}",
    )
    passed, msgs = tm_pu.check_texture_file_size(4)
    check(
        "UDIM tile set under the gate still passes the size check",
        passed is True,
        f"msgs={msgs}",
    )
    # The size gate is a SpinBox (int), but saved templates and direct calls can
    # still hand the limit over as text.
    passed, msgs = tm_pu.check_texture_file_size("1")
    check(
        "check_texture_file_size applies numeric text ('1') as a 1 MB gate",
        passed is False and any("udim_big.1002" in m for m in msgs),
        f"msgs={msgs}",
    )
    passed, msgs = tm_pu.check_texture_file_size("abc")
    check(
        "non-numeric size text skips the check instead of raising",
        passed is True,
        f"msgs={msgs}",
    )

    # Drop this section's datablocks so the later embed-texture FBX writes don't
    # trip over the deliberately-stale packed/UDIM images (log noise only).
    reset_scene()
    bpy.data.materials.remove(pu_mat)
    for _img in (packed_img, udim_missing, udim_big):
        bpy.data.images.remove(_img)

    # ---- GLB-only ordering: a failed FBX→GLB conversion must NOT roll the scene-data
    # sidecar (hierarchy-diff baseline) forward — no deliverable, no record. ------------------
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    gcube = bpy.context.active_object
    gcube.name = "GlbOrderCube"
    gcube[LightmapBaker.LIGHTMAP_INFO_PROP] = json.dumps(
        {"map": "GlbOrderCube_Lightmap.exr", "intensity": 1.0}
    )
    LightmapBaker.refresh_export_metadata()

    glb_dir = os.path.join(tmp, "glb_order")
    os.makedirs(glb_dir, exist_ok=True)
    exp8 = SceneExporter()
    # Deterministic conversion failure (ptk.MeshConvert is environment-dependent).
    exp8._create_glb = lambda fbx_path=None, announce=True, objects=None: None
    result = exp8.perform_export(
        export_dir=glb_dir,
        objects=[gcube],
        output_name="glb_order_fail",
        export_visible=True,
        tasks={"export_data_node": True, "output_format": "glb"},
    )
    check(
        "glb-only export reports failure when the conversion produces no file",
        result is False
        and not os.path.exists(os.path.join(glb_dir, "glb_order_fail.glb")),
        f"result={result}",
    )
    check(
        "failed glb-only conversion leaves NO scene-data sidecar",
        SceneDataSidecar.read_manifest(os.path.join(glb_dir, "glb_order_fail.fbx"))
        is None,
    )

    # ... and with a working conversion the deliverable lands AND the sidecar is written.
    def _fake_glb(fbx_path=None, announce=True, objects=None):
        p = os.path.splitext(fbx_path)[0] + ".glb"
        with open(p, "wb") as fh:
            fh.write(b"GLBDATA")
        return p

    exp9 = SceneExporter()
    exp9._create_glb = _fake_glb
    result = exp9.perform_export(
        export_dir=glb_dir,
        objects=[gcube],
        output_name="glb_order_ok",
        export_visible=True,
        tasks={"export_data_node": True, "output_format": "glb"},
    )
    check(
        "glb-only export succeeds once the conversion yields a file",
        result is True and os.path.isfile(os.path.join(glb_dir, "glb_order_ok.glb")),
        f"result={result}",
    )
    check(
        "sidecar IS written once the glb deliverable is confirmed",
        "GlbOrderCube"
        in (
            SceneDataSidecar.read_manifest(os.path.join(glb_dir, "glb_order_ok.fbx"))
            or set()
        ),
    )

    # ---- GLB texture delivery (glb_texture_format): stamped by perform_export, consumed
    # LAST by _create_glb; a failure fails the deliverable; FBX-only leaves it inert;
    # a missing toktx aborts before any file is written (mirror of mayatk's tests). --------
    import pythontk as ptk
    from unittest import mock

    def _fake_convert(src, **kw):
        p = os.path.splitext(src)[0] + ".glb"
        with open(p, "wb") as fh:
            fh.write(b"GLBDATA")
        return p

    delivered = {}

    def _fake_optimize(path, **kw):
        delivered.update(kw, path=path)
        return {"images": 1, "bytes_before": 2e6, "bytes_after": 1e6}

    exp10 = SceneExporter()
    with mock.patch.object(
        ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert
    ), mock.patch.object(
        ptk.MeshConvert, "optimize_glb_textures", side_effect=_fake_optimize
    ):
        result = exp10.perform_export(
            export_dir=glb_dir,
            objects=[gcube],
            output_name="glb_delivery",
            export_visible=True,
            tasks={"output_format": "glb", "glb_texture_format": "WEBP"},
        )
    check(
        "glb texture delivery runs the optimize pass container-only (max_size=0)",
        result is True
        and delivered.get("image_format") == "WEBP"
        and delivered.get("max_size") == 0
        and os.path.isfile(os.path.join(glb_dir, "glb_delivery.glb")),
        f"result={result}, delivered={delivered}",
    )

    # WEBP, deliberately: KTX2 would hit the real toktx gate on machines
    # without the encoder and abort BEFORE _create_glb — passing this check
    # for the wrong reason. WEBP has no gate, so the delivery-failure branch
    # itself (format-agnostic) is what runs everywhere.
    exp11 = SceneExporter()
    with mock.patch.object(
        ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert
    ), mock.patch.object(
        ptk.MeshConvert,
        "optimize_glb_textures",
        side_effect=RuntimeError("encode failed (test)"),
    ):
        result = exp11.perform_export(
            export_dir=glb_dir,
            objects=[gcube],
            output_name="glb_delivery_fail",
            export_visible=True,
            tasks={"output_format": "glb", "glb_texture_format": "WEBP"},
        )
    check(
        "a failed texture delivery fails the deliverable (no silent fallback)",
        result is False
        and not os.path.isfile(os.path.join(glb_dir, "glb_delivery_fail.glb")),
        f"result={result}",
    )

    exp12 = SceneExporter()
    with mock.patch.object(
        ptk.ImgUtils,
        "resolve_ktx2_encoder",
        side_effect=AssertionError("gate must not run for FBX-only output"),
    ):
        result = exp12.perform_export(
            export_dir=glb_dir,
            objects=[gcube],
            output_name="fbx_only_inert",
            export_visible=True,
            tasks={"output_format": "fbx", "glb_texture_format": "KTX2"},
        )
    check(
        "KTX2 setting is inert for FBX-only output (no gate; stamp cleared)",
        result is True and exp12._glb_texture_format is None,
        f"result={result}, stamp={getattr(exp12, '_glb_texture_format', 'unset')!r}",
    )

    exp_bad = SceneExporter()
    result = exp_bad.perform_export(
        export_dir=glb_dir,
        objects=[gcube],
        output_name="bad_format",
        export_visible=True,
        tasks={"output_format": "glb", "glb_texture_format": "PNG"},
    )
    check(
        "an unknown glb_texture_format aborts loudly at parse (config error)",
        result is False
        and not os.path.exists(os.path.join(glb_dir, "bad_format.fbx"))
        and not os.path.exists(os.path.join(glb_dir, "bad_format.glb")),
        f"result={result}",
    )

    gate_dir = os.path.join(tmp, "ktx2_gate")
    os.makedirs(gate_dir, exist_ok=True)
    exp13 = SceneExporter()
    with mock.patch.object(
        ptk.ImgUtils,
        "resolve_ktx2_encoder",
        side_effect=FileNotFoundError("toktx missing (test)"),
    ):
        result = exp13.perform_export(
            export_dir=gate_dir,
            objects=[gcube],
            output_name="ktx2_gate",
            export_visible=True,
            tasks={"output_format": "glb", "glb_texture_format": "KTX2"},
        )
    check(
        "missing toktx aborts the export before any file is written",
        result is False and os.listdir(gate_dir) == [],
        f"result={result}, files={os.listdir(gate_dir)}",
    )

    # ---- export funnel: unselectable objects are surfaced, never silently lost ------------
    # FbxUtils.export selects with use_selection=True: a HIDDEN object silently fails
    # select_set (dropped from the FBX with no trace), and one in a view-layer-EXCLUDED
    # collection made select_set RAISE and kill the whole export. The funnel now collects
    # both, logs a WARNING naming them (strict=True raises instead), and still ships the
    # selectable rest.
    import logging

    class _ListHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    fun_vis = bpy.context.active_object
    fun_vis.name = "FunnelVisible"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    fun_hidden = bpy.context.active_object
    fun_hidden.name = "FunnelHidden"
    fun_hidden.hide_set(True)
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
    fun_excl = bpy.context.active_object
    fun_excl.name = "FunnelExcluded"
    excl_coll = bpy.data.collections.new("FunnelExclColl")
    bpy.context.scene.collection.children.link(excl_coll)
    for c in list(fun_excl.users_collection):
        c.objects.unlink(fun_excl)
    excl_coll.objects.link(fun_excl)
    bpy.context.view_layer.layer_collection.children["FunnelExclColl"].exclude = True

    fbx_logger = logging.getLogger("blendertk.env_utils.fbx_utils")
    fh = _ListHandler()
    fbx_logger.addHandler(fh)
    funnel_file = os.path.join(tmp, "funnel_guard.fbx")
    try:
        written = FbxUtils.export(
            filepath=funnel_file, objects=[fun_vis, fun_hidden, fun_excl]
        )
        funnel_ok = os.path.isfile(written)
    except RuntimeError as e:
        funnel_ok = False
        written = repr(e)
    finally:
        fbx_logger.removeHandler(fh)
    drop_warns = [m for m in fh.messages if "DROPPED from the FBX" in m]
    check(
        "an excluded-collection member no longer kills the export (file written)",
        funnel_ok,
        f"{written}",
    )
    check(
        "the funnel WARNS with count + names of the dropped members",
        len(drop_warns) == 1
        and "2 requested object(s)" in drop_warns[0]
        and "FunnelHidden" in drop_warns[0]
        and "FunnelExcluded" in drop_warns[0],
        f"{drop_warns}",
    )

    reset_scene()
    funnel_imported = FbxUtils.import_fbx(funnel_file)
    check(
        "only the selectable member ships in the FBX",
        {o.name.split(".")[0] for o in funnel_imported} == {"FunnelVisible"},
        f"{[o.name for o in funnel_imported]}",
    )

    # strict=True: the same drop list raises instead of exporting without them.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    st_vis = bpy.context.active_object
    st_vis.name = "StrictVisible"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    st_hidden = bpy.context.active_object
    st_hidden.name = "StrictHidden"
    st_hidden.hide_set(True)
    try:
        FbxUtils.export(
            filepath=os.path.join(tmp, "funnel_strict.fbx"),
            objects=[st_vis, st_hidden],
            strict=True,
        )
        check("strict=True raises on dropped members", False)
    except RuntimeError as e:
        check(
            "strict=True raises on dropped members (naming them)",
            "StrictHidden" in str(e),
            f"{e}",
        )

    # ---- SceneExporter pre-filters the export set (primary signal, INFO log) --------------
    # With check_hidden_geometry off (no checks in this run) a hidden mesh used to reach
    # the funnel and vanish silently; the engine now drops it up front and says so.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    pf_vis = bpy.context.active_object
    pf_vis.name = "PrefilterVisible"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    pf_hidden = bpy.context.active_object
    pf_hidden.name = "PrefilterHidden"
    pf_hidden.hide_set(True)

    pf_handler = _ListHandler()
    exp_pf = SceneExporter()
    result = exp_pf.perform_export(
        export_dir=out_dir,
        objects=[pf_vis, pf_hidden],
        output_name="prefilter_test",
        export_visible=True,
        log_level="INFO",
        log_handler=pf_handler,
    )
    pf_msgs = [m for m in pf_handler.messages if "cannot ship" in m]
    check(
        "perform_export succeeds while pre-filtering the hidden member",
        result is True and os.path.isfile(os.path.join(out_dir, "prefilter_test.fbx")),
        f"result={result}",
    )
    check(
        "the pre-filter logs an INFO naming what was dropped",
        len(pf_msgs) == 1 and "PrefilterHidden" in pf_msgs[0],
        f"{pf_msgs}",
    )
    reset_scene()
    pf_imported = FbxUtils.import_fbx(os.path.join(out_dir, "prefilter_test.fbx"))
    check(
        "the hidden member is absent from the written FBX",
        {o.name.split(".")[0] for o in pf_imported} == {"PrefilterVisible"},
        f"{[o.name for o in pf_imported]}",
    )

    # ---- data_export carrier in a hidden/EXCLUDED collection still ships ------------------
    # hide_set/hide_select clears can't help when the carrier's COLLECTION is excluded
    # from the view layer (hide_set even raises there): export_data_node now links the
    # carrier to the scene root collection for the write and unlinks it right after
    # (deferred restore).
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    cc_cube = bpy.context.active_object
    cc_cube.name = "CarrierCollCube"
    cc_carrier = DataNodes.ensure_export()
    DataNodes.set_export_string("hidden_coll_probe", json.dumps({"v": 42}))
    hid_coll = bpy.data.collections.new("CarrierHiddenColl")
    bpy.context.scene.collection.children.link(hid_coll)
    for c in list(cc_carrier.users_collection):
        c.objects.unlink(cc_carrier)
    hid_coll.objects.link(cc_carrier)
    bpy.context.view_layer.layer_collection.children["CarrierHiddenColl"].exclude = True

    exp_cc = SceneExporter()
    result = exp_cc.perform_export(
        export_dir=out_dir,
        objects=[cc_cube],
        output_name="carrier_coll_test",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    cc_file = os.path.join(out_dir, "carrier_coll_test.fbx")
    check(
        "perform_export succeeds with the carrier in an excluded collection",
        result is True and os.path.isfile(cc_file),
        f"result={result}",
    )
    check(
        "the transient root-collection link is removed after run_deferred_restores",
        cc_carrier.name not in bpy.context.scene.collection.objects
        and cc_carrier.name in hid_coll.objects,
        f"root={list(bpy.context.scene.collection.objects.keys())}",
    )
    reset_scene()
    cc_imported = FbxUtils.import_fbx(cc_file, use_custom_props=True)
    cc_imp_carrier = next(
        (o for o in cc_imported if o.name.startswith(DataNodes.EXPORT)), None
    )
    check(
        "the carrier from the excluded collection ships in the FBX with its channel",
        cc_imp_carrier is not None
        and cc_imp_carrier.get("hidden_coll_probe") == json.dumps({"v": 42}),
        f"imported={[o.name for o in cc_imported]}",
    )

    # ---- optimize_textures / check_texture_optimization: the Optimize pair ----------------
    # Engine decisions (the per-map-type plan, template spec resolution) are pythontk's,
    # covered by test_map_optimizer; this exercises the Blender glue — image gathering,
    # repathing + deferred restore, temp-staging cleanup, and the paired gate reading
    # post-task state.
    try:
        # --factory-startup leaves the user-modules dir off sys.path; this is
        # the production provisioning call (idempotent, never raises) that the
        # material tools run, so the glue is tested the way it ships.
        from blendertk.core_utils._core_utils import CoreUtils as _BtkCore

        _BtkCore.ensure_image_deps()
        from PIL import Image as _PILImage
    except Exception:
        _PILImage = None

    if _PILImage is None:
        check(
            "texture optimization: SKIPPED — Pillow not provisioned in this "
            "Blender (CoreUtils.ensure_image_deps)",
            True,
        )
    else:
        reset_scene()
        bpy.ops.mesh.primitive_cube_add()
        tb_cube = bpy.context.active_object
        tb_cube.name = "OptimizeCube"
        tb_mat = bpy.data.materials.new("OptimizeMat")
        # 5.x+: use_nodes is deprecated (reading warns) and pinned True
        # regardless — materials already carry a node_tree; 6.0 removes the
        # attribute (reading raises). Version-gated, not probed, so the
        # attribute is never touched where it no longer behaves as a toggle
        # (mirror of light_utils._LightUtilsInternal._world_node_tree).
        if bpy.app.version < (5, 0):
            tb_mat.use_nodes = True
        tb_tex_node = tb_mat.node_tree.nodes.new("ShaderNodeTexImage")
        # A palette-mode normal map — the per-map-type pass must coerce
        # P->RGB (palette transparency reads as alpha downstream); its
        # dimensions must NEVER be resampled (no size dial by design).
        tb_src = os.path.join(tmp, "opt_src_Normal.png")
        _PILImage.new("RGB", (256, 256), (128, 128, 128)).convert("P").save(tb_src)
        tb_tex_node.image = bpy.data.images.load(tb_src)
        tb_bsdf = next(
            n for n in tb_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"
        )
        tb_mat.node_tree.links.new(
            tb_tex_node.outputs["Color"], tb_bsdf.inputs["Base Color"]
        )
        tb_cube.data.materials.append(tb_mat)

        tb_tm = SceneExporter().task_manager
        tb_tm.objects = [tb_cube]
        tb_tm._glb_only = True  # temp staging; the GLB embeds its own copies
        tb_tm._texture_write_back = False
        tb_orig_fp = tb_tex_node.image.filepath
        tb_src_size = os.path.getsize(tb_src)

        passed, msgs = tb_tm.check_texture_optimization(True)
        check(
            "texture optimization: gate fails on an unoptimized source",
            passed is False and any("opt_src_Normal.png" in m for m in msgs),
            f"{msgs}",
        )
        check(
            "texture optimization: OFF (None/False) skips cleanly",
            tb_tm.check_texture_optimization(None) == (True, [])
            and tb_tm.check_texture_optimization(False) == (True, []),
        )

        tb_tm.optimize_textures(True)
        tb_staged = tb_tex_node.image.filepath
        with _PILImage.open(tb_staged) as _img:
            staged_dims, staged_mode = _img.size, _img.mode
        with _PILImage.open(tb_src) as _img:
            src_mode = _img.mode
        check(
            "optimize_textures stages an RGB copy, never resampled, and "
            "repoints the image",
            os.path.normcase(tb_staged) != os.path.normcase(tb_orig_fp)
            and os.path.isfile(tb_staged)
            and staged_mode == "RGB"
            and staged_dims == (256, 256),
            f"staged={tb_staged} mode={staged_mode} dims={staged_dims}",
        )
        check(
            "the scene's source file is untouched",
            src_mode == "P" and os.path.getsize(tb_src) == tb_src_size,
        )
        passed, msgs = tb_tm.check_texture_optimization(True)
        check(
            "the gate passes on the staged post-task state",
            passed is True,
            f"{msgs}",
        )

        tb_tm.run_deferred_restores()
        check(
            "deferred restore repoints the image and deletes the temp staging",
            tb_tex_node.image.filepath == tb_orig_fp
            and not os.path.exists(tb_staged),
            f"filepath={tb_tex_node.image.filepath}",
        )

        # Max Texture Size (mirror of mayatk): the pass's one size dial. A
        # combo row popped by perform_export like Texture Output — OFF first
        # (falsy), pixel ceilings, the template-budget sentinel LAST.
        # Added: 2026-08-17
        tb_defs = tb_tm.task_definitions
        tb_sizes = list(tb_defs["texture_max_size"]["add"].values())
        tb_keys = list(tb_defs)
        check(
            "texture_max_size: ComboBox row between Optimize Textures and "
            "Texture Output; OFF=0 first, template sentinel last",
            tb_defs["texture_max_size"]["widget_type"] == "ComboBox"
            and tb_sizes[0] == 0
            and tb_sizes[-1] == tb_tm.TEXTURE_MAX_SIZE_TEMPLATE
            and tb_sizes[1:-1] == [512, 1024, 2048, 4096, 8192]
            and "texture_max_size" not in tb_tm.TASK_ORDER
            and tb_keys.index("optimize_textures")
            < tb_keys.index("texture_max_size")
            < tb_keys.index("texture_write_back"),
            f"sizes={tb_sizes}",
        )
        tb_tm._texture_max_size = tb_tm.TEXTURE_MAX_SIZE_TEMPLATE
        check(
            "_texture_size_clamp: sentinel enforces the template budget "
            "(no POT), no-op without a template; ceiling = max_size; OFF = {}",
            tb_tm._texture_size_clamp("glTF 2.0")
            == {"enforce_budget": True, "force_pot": False}
            and tb_tm._texture_size_clamp(None) == {}
            and (
                setattr(tb_tm, "_texture_max_size", "1024")
                or tb_tm._texture_size_clamp(None)
                == {"max_size": 1024}
            )
            and (
                setattr(tb_tm, "_texture_max_size", "OFF")
                or tb_tm._texture_size_clamp("glTF 2.0") == {}
            ),
        )

        # A 512x256 source under a 128 ceiling: the staged copy is 128x64
        # (aspect kept), the source keeps its dimensions, and the paired
        # check judges through the same clamp (fails before, passes after).
        tb_big = os.path.join(tmp, "clamp_src_Normal.png")
        _PILImage.new("RGB", (512, 256), (128, 128, 128)).save(tb_big)
        tb_tex_node.image = bpy.data.images.load(tb_big)
        tb_big_fp = tb_tex_node.image.filepath
        tb_tm._texture_max_size = 128
        passed, msgs = tb_tm.check_texture_optimization(True)
        check(
            "max size: over-size source fails the gate before the task",
            passed is False and any("clamp_src_Normal.png" in m for m in msgs),
            f"{msgs}",
        )
        tb_tm.optimize_textures(True)
        tb_clamped = tb_tex_node.image.filepath
        with _PILImage.open(tb_clamped) as _img:
            clamped_dims = _img.size
        with _PILImage.open(tb_big) as _img:
            big_dims = _img.size
        passed, msgs = tb_tm.check_texture_optimization(True)
        check(
            "max size: staged copy clamped to 128x64, source untouched, gate "
            "passes after",
            os.path.normcase(tb_clamped) != os.path.normcase(tb_big_fp)
            and clamped_dims == (128, 64)
            and big_dims == (512, 256)
            and passed is True,
            f"staged={clamped_dims} src={big_dims} msgs={msgs}",
        )
        tb_tm.run_deferred_restores()
        tb_tm._texture_max_size = None
        check(
            "max size: restore repoints the image",
            tb_tex_node.image.filepath == tb_big_fp,
            f"filepath={tb_tex_node.image.filepath}",
        )

    # ---- tiled-token substitution: <uvtile>/<f> must not collapse onto "1001" -------------
    # Bug: the single-token substitution used "1001" for every token kind. <udim> -> "1001"
    # is right; <uvtile>'s own first-tile convention is "u1_v1" (a UDIM tile number is not a
    # UV-tile coordinate); <f> (frame sequences) has no fixed "first" value at all -- it must
    # glob for whatever frame is actually on disk.
    tb_tm2 = SceneExporter(log_level="INFO").task_manager
    udim_rep = tb_tm2._tiled_representative(os.path.join(tex_dir, "tex.<UDIM>.png"))
    check(
        "_tiled_representative: <udim> resolves to its own first tile (1001)",
        os.path.normcase(udim_rep or "")
        == os.path.normcase(os.path.join(tex_dir, "tex.1001.png")),
        f"{udim_rep}",
    )
    uvtile_rep = tb_tm2._tiled_representative(os.path.join(tex_dir, "tex.<uvtile>.png"))
    check(
        "_tiled_representative: <uvtile> resolves to ITS OWN first tile (u1_v1), not 1001",
        os.path.normcase(uvtile_rep or "")
        == os.path.normcase(os.path.join(tex_dir, "tex.u1_v1.png")),
        f"{uvtile_rep}",
    )

    for frame in ("0007", "0008"):
        with open(os.path.join(tex_dir, f"seq.{frame}.exr"), "wb") as fh:
            fh.write(b"EXRDATA")
    frame_rep = tb_tm2._tiled_representative(os.path.join(tex_dir, "seq.<f>.exr"))
    check(
        "_tiled_representative: <f> globs for the first frame actually on disk",
        os.path.normcase(frame_rep or "")
        == os.path.normcase(os.path.join(tex_dir, "seq.0007.exr")),
        f"{frame_rep}",
    )
    missing_rep = tb_tm2._tiled_representative(os.path.join(tex_dir, "nope.<f>.exr"))
    check(
        "_tiled_representative: <f> with no frame on disk reports None, not a fabricated path",
        missing_rep is None,
        f"{missing_rep}",
    )

    # Integration: a <f> image with frames on disk resolves; one with none is skipped and
    # logged (never silently dropped, never collapsed onto "1001" like a UDIM would be).
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    seq_cube = bpy.context.active_object
    seq_mat = bpy.data.materials.new("SeqMat")
    if bpy.app.version < (5, 0):
        seq_mat.use_nodes = True
    seq_cube.data.materials.append(seq_mat)

    found_img = bpy.data.images.new("found_seq", 4, 4)
    found_img.filepath = os.path.join(tex_dir, "found_seq.<f>.exr")
    seq_mat.node_tree.nodes.new("ShaderNodeTexImage").image = found_img

    missing_img = bpy.data.images.new("missing_seq", 4, 4)
    missing_img.filepath = os.path.join(tex_dir, "missing_seq.<f>.exr")
    seq_mat.node_tree.nodes.new("ShaderNodeTexImage").image = missing_img

    for frame in ("0010", "0011"):
        with open(os.path.join(tex_dir, f"found_seq.{frame}.exr"), "wb") as fh:
            fh.write(b"EXRDATA")

    tm_seq = SceneExporter(log_level="INFO").task_manager
    tm_seq.objects = [seq_cube]
    seq_handler = _ListHandler()
    tm_seq.logger.addHandler(seq_handler)
    try:
        seq_sources = tm_seq._export_texture_sources(include_tiled=True)
    finally:
        tm_seq.logger.removeHandler(seq_handler)

    seq_paths = {os.path.normcase(e["path"]) for e in seq_sources.values()}
    check(
        "_export_texture_sources: <f> image with frames on disk resolves to the first frame",
        os.path.normcase(os.path.join(tex_dir, "found_seq.0010.exr")) in seq_paths,
        f"{seq_paths}",
    )
    check(
        "_export_texture_sources: <f> image with no frame on disk is not silently included",
        all("missing_seq" not in p for p in seq_paths),
        f"{seq_paths}",
    )
    check(
        "_export_texture_sources: the skipped <f>-with-no-frame image is logged",
        any("missing_seq" in m for m in seq_handler.messages),
        f"{seq_handler.messages}",
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
