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

import pythontk as ptk  # noqa: E402 -- after the sibling paths above

# Isolate PresetStore's user tier in a scratch dir for this run — never touch the real
# %LOCALAPPDATA%/uitk store (pythontk.core_utils.user_config.user_config_root honors this).
_PRESETS_ROOT = tempfile.mkdtemp(prefix="btk_scnexp_presets_")
os.environ["UITK_PRESETS_ROOT"] = _PRESETS_ROOT

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


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

    # ---- store identity: the FBX tier must NOT be the window-template dir ------------------
    # REGRESSION (2026-08-19): PRESET_NAME was "scene_exporter", which resolved the user
    # tier to the SAME directory the panel's uitk PresetManager stores window templates
    # in — the FBX combo listed window templates, a template named like a shipped preset
    # shadowed it, and the two stores fought over one .active sidecar.
    from pathlib import Path as _Path

    _store = SceneExporter._preset_store()
    check(
        "FBX preset user tier is blendertk/fbx_presets, not the window-template dir",
        str(_store.user_dir).replace("\\", "/").endswith("blendertk/fbx_presets"),
        f"{_store.user_dir}",
    )

    # ---- legacy migration: FBX presets stranded in the window-template dir ----------------
    _legacy = _Path(_PRESETS_ROOT) / "blendertk" / "scene_exporter"
    _legacy.mkdir(parents=True, exist_ok=True)
    (_legacy / "wintemplate.json").write_text(
        json.dumps({"_meta": {"version": 1}, "chk001": True}), encoding="utf-8"
    )
    (_legacy / "stranded_fbx.json").write_text(
        json.dumps({"bake_anim": False, "global_scale": 3.0}), encoding="utf-8"
    )
    (_legacy / "default.json").write_text(  # value-equal shadow of the shipped built-in
        json.dumps(_DEFAULT_FBX_OPTIONS), encoding="utf-8"
    )
    (_legacy / ".active").write_text(
        json.dumps({"name": "stranded_fbx"}), encoding="utf-8"
    )
    SceneExporter._legacy_fbx_presets_migrated = False  # re-arm the one-shot guard
    _store = SceneExporter._preset_store()
    check(
        "migration moves a stranded FBX preset into the fbx_presets tier",
        (_Path(_store.user_dir) / "stranded_fbx.json").is_file()
        and not (_legacy / "stranded_fbx.json").exists(),
    )
    check(
        "migration leaves window templates in place",
        (_legacy / "wintemplate.json").is_file(),
    )
    check(
        "migration drops a built-in-identical shadow instead of promoting it",
        not (_legacy / "default.json").exists()
        and not (_Path(_store.user_dir) / "default.json").exists(),
    )
    check(
        "migration clears the cross-store .active pointer",
        not (_legacy / ".active").exists(),
    )
    check(
        "migrated preset loads through the store",
        SceneExporter._preset_store().load("stranded_fbx")
        == {"bake_anim": False, "global_scale": 3.0},
    )

    # ---- built-in "default" preset is discoverable + matches _DEFAULT_FBX_OPTIONS ----------
    names = SceneExporter.list_fbx_presets()
    check(
        "list_fbx_presets() includes both shipped built-ins (default + game_asset)",
        {"default", "game_asset"} <= set(names),
        f"{names}",
    )

    default_path = SceneExporter.fbx_preset_path("default")
    check(
        "fbx_preset_path resolves the built-in default.json",
        bool(default_path)
        and os.path.isfile(default_path)
        and default_path.endswith("default.json"),
        f"{default_path}",
    )
    with open(default_path, "r", encoding="utf-8") as fh:
        on_disk_default = json.load(fh)

    # The shipped "default" preset must be Blender's OWN export_scene.fbx defaults --
    # what the user gets from File > Export > FBX -- not this tool's opinion (which
    # ships as "game_asset"). Pinned against the LIVE operator RNA so the file cannot
    # drift from the running Blender, and cannot be hand-fabricated.
    _rna = bpy.ops.export_scene.fbx.get_rna_type()
    _live_defaults = {}
    for _k in on_disk_default:
        _p = _rna.properties[_k]
        _live_defaults[_k] = (
            sorted(_p.default_flag)
            if (_p.type == "ENUM" and _p.is_enum_flag)
            else _p.default
        )
    check(
        "shipped default.json IS Blender's live export_scene.fbx defaults",
        on_disk_default == _live_defaults,
        f"{on_disk_default} != {_live_defaults}",
    )
    check(
        "default preset carries no pipeline-owned props (scope/path stay the panel's)",
        not (
            {"use_selection", "use_visible", "filepath", "check_existing"}
            & set(on_disk_default)
        ),
        f"{sorted(on_disk_default)}",
    )

    game_asset_path = SceneExporter.fbx_preset_path("game_asset")
    with open(game_asset_path, "r", encoding="utf-8") as fh:
        on_disk_game_asset = json.load(fh)
    check(
        "shipped game_asset.json matches _DEFAULT_FBX_OPTIONS (the engine baseline)",
        on_disk_game_asset == _DEFAULT_FBX_OPTIONS,
        f"{on_disk_game_asset} != {_DEFAULT_FBX_OPTIONS}",
    )

    # The take-structure invariant survives a preset carrying Blender's own values:
    # both bake_anim_* flags default True, which makes the exporter skip the
    # scene-range take entirely (per-action, start-zeroed takes instead). Checked
    # through the PUBLIC path -- verify_fbx_preset is what perform_export writes
    # from and what the settings report discloses, so the guarantee has to hold
    # there, not merely in the private helper.
    _exp_take = SceneExporter(log_level="INFO")
    _exp_take.load_fbx_export_preset("default")
    _resolved_default = _exp_take.verify_fbx_preset()
    check(
        "the stock 'default' preset still resolves to ONE scene-range take",
        _resolved_default["bake_anim"] is True
        and _resolved_default["bake_anim_use_nla_strips"] is False
        and _resolved_default["bake_anim_use_all_actions"] is False,
        f"{_resolved_default}",
    )
    check(
        "the rest of the stock preset survives the take invariant untouched",
        all(
            _resolved_default[k] == v
            for k, v in on_disk_default.items()
            if k not in ("bake_anim_use_nla_strips", "bake_anim_use_all_actions")
        ),
        f"{_resolved_default}",
    )
    _opts_off = {"bake_anim": False, "bake_anim_use_all_actions": True}
    _exp_take._force_scene_range_take(_opts_off)
    check(
        "the take invariant is a no-op when animation isn't baked",
        _opts_off["bake_anim_use_all_actions"] is True,
    )
    _exp_take.load_fbx_export_preset(None)

    # ---- save_fbx_preset seeds from _DEFAULT_FBX_OPTIONS when options=None -----------------
    default_copy_path = SceneExporter.save_fbx_preset("my_default_copy")
    check(
        "save_fbx_preset(options=None) seeds from _DEFAULT_FBX_OPTIONS",
        os.path.isfile(default_copy_path)
        and SceneExporter._preset_store().load("my_default_copy")
        == _DEFAULT_FBX_OPTIONS,
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

    SceneExporter.save_fbx_preset(
        "half_scale", {"global_scale": 0.5, "bake_anim": False}
    )
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
    # (FbxUtils.publish), so a hand-stamped channel with no scene
    # state behind it would be correctly regenerated away as stale.
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

    cube[LightmapRecords.LIGHTMAP_INFO_PROP] = json.dumps(
        {"map": "CarrierExportCube_Lightmap.exr", "intensity": 1.0}
    )
    LightmapRecords.refresh_export_metadata()
    payload = DataNodes.read(ptk.Scope.DELIVERABLE, "lightmap_metadata")
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
    # exported hierarchy paths (`TaskManager.write_scene_data_sidecar`).
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
    DataNodes.write(ptk.Scope.DELIVERABLE, "test_channel", json.dumps({"v": 1}))
    exp5.task_manager.objects = [lone]
    exp5.task_manager.write_scene_data_sidecar()
    check(
        "carrier outside the export set records no data",
        SceneDataSidecar.read_manifest(os.path.join(out_dir, "no_carrier_test.fbx"))
        is None,
    )

    # --- hierarchy baseline: on the .blend, not the deliverable's name --------
    # Mirror of mayatk. blendertk has no hierarchy CHECK yet (a declared
    # PARITY_GAPS entry), so this covers the WRITER: the record has to exist and
    # roll forward from the day the writer does, or the check lands with no
    # history behind it.
    from blendertk.env_utils.hierarchy_sync.hierarchy_baseline import HierarchyBaseline

    DataNodes.write(ptk.Scope.PRIVATE, HierarchyBaseline.ATTR_NAME, "")
    _hb_dir = os.path.join(out_dir, "baseline")
    os.makedirs(_hb_dir, exist_ok=True)
    _hb_tm = exp5.task_manager
    _hb_tm.objects = [lone]
    _hb_tm.run = _hb_tm.run.replace(export_path=os.path.join(_hb_dir, "asset.fbx"))
    _hb_tm.write_scene_data_sidecar()
    _hb_first = HierarchyBaseline.read()
    check("baseline recorded on the .blend", bool(_hb_first))

    # The SAME scene exported under a different name keeps the same record --
    # the bug this replaced keyed it by the output stem, so a rename silently
    # started over and the next export passed no matter what had changed.
    _hb_tm.run = _hb_tm.run.replace(
        export_path=os.path.join(_hb_dir, "WIP_prod_thing_v007.fbx")
    )
    _hb_tm.write_scene_data_sidecar()
    check(
        "an output rename does not reset the baseline",
        HierarchyBaseline.read() == _hb_first,
        f"{sorted(HierarchyBaseline.read())} != {sorted(_hb_first)}",
    )

    # A second scope accumulates rather than replacing the first.
    _hb_other = bpy.data.objects.new("OtherAsset", None)
    bpy.context.scene.collection.objects.link(_hb_other)
    _hb_tm.objects = [_hb_other]
    _hb_tm.run = _hb_tm.run.replace(export_path=os.path.join(_hb_dir, "other.fbx"))
    _hb_tm.write_scene_data_sidecar()
    _hb_both = HierarchyBaseline.read()
    check(
        "a second export scope accumulates, it does not replace",
        _hb_first <= _hb_both and len(_hb_both) > len(_hb_first),
        f"first={sorted(_hb_first)} both={sorted(_hb_both)}",
    )

    # An unreadable channel is "no baseline", not a crash -- and is reported as
    # LOST rather than silently replaced.
    DataNodes.write(ptk.Scope.PRIVATE, HierarchyBaseline.ATTR_NAME, "{not json")
    check("an unreadable baseline reads empty", HierarchyBaseline.read() == set())
    check("an unreadable baseline is flagged", HierarchyBaseline.is_unreadable())
    DataNodes.write(ptk.Scope.PRIVATE, HierarchyBaseline.ATTR_NAME, "")

    # After a GLB the sidecar records the lightmap manifest the GLB ships (mirror of
    # mayatk's): the GLB pass corrects that copy to the encoded map and the scalar
    # restoring the bake range, while the scene's still names the pre-encode .exr
    # at 1.0. An FBX-only run records the scene's. Added: 2026-09-15
    import struct as _struct

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    _sg_cube = bpy.context.active_object
    _sg_cube.name = "SidecarGlbCube"
    _sg_entry = {"name": "SidecarGlbCube", "map": "room_Lightmap.exr", "intensity": 1.0}
    _sg_scene = {"version": 1, "objects": [_sg_entry]}
    _sg_shipped = {
        "version": 1,
        "objects": [dict(_sg_entry, map="room_Lightmap.png", intensity=0.5)],
    }
    DataNodes.write(ptk.Scope.DELIVERABLE, "lightmap_metadata", json.dumps(_sg_scene))
    _sg_gltf = {
        "asset": {"version": "2.0"},
        "nodes": [
            {
                "name": "data_export",
                "extras": {"lightmap_metadata": json.dumps(_sg_shipped)},
            }
        ],
    }
    _sg_chunk = json.dumps(_sg_gltf).encode("utf-8")
    _sg_chunk += b" " * (-len(_sg_chunk) % 4)
    _sg_glb = os.path.join(out_dir, "sidecar_glb.glb")
    with open(_sg_glb, "wb") as _sg_file:
        _sg_file.write(_struct.pack("<4sII", b"glTF", 2, 20 + len(_sg_chunk)))
        _sg_file.write(_struct.pack("<I4s", len(_sg_chunk), b"JSON") + _sg_chunk)
    _sg_fbx = os.path.join(out_dir, "sidecar_glb.fbx")
    _sg_tm = SceneExporter().task_manager
    _sg_tm.run = _sg_tm.run.replace(export_path=_sg_fbx)
    _sg_tm.objects = [_sg_cube, DataNodes.get_export_node(create=False)]
    _sg_tm.write_scene_data_sidecar(glb_path=_sg_glb)
    _sg_data = SceneDataSidecar.read_data(_sg_fbx) or {}
    check(
        "after a GLB the sidecar records the lightmap manifest the GLB ships",
        _sg_data.get("lightmap_metadata") == _sg_shipped,
        f"{_sg_data!r}",
    )
    _sg_tm.write_scene_data_sidecar()
    _sg_data = SceneDataSidecar.read_data(_sg_fbx) or {}
    check(
        "...and without a GLB it records the scene's",
        _sg_data.get("lightmap_metadata") == _sg_scene,
        f"{_sg_data!r}",
    )

    # A run that stops before its write names what its tasks KEPT, and only that.
    # Blender's key tasks keep their edits (no Animation Output gate yet), so a run
    # blocked after a snap names them -- and not the material cleanup and texture
    # rewrites a fixed list claimed. Added: 2026-09-15
    import logging as _bk_logging

    class _BlockedLog(_bk_logging.Handler):
        def __init__(self):
            super().__init__(level=_bk_logging.WARNING)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    _bk_cube = bpy.context.active_object
    _bk_cube.name = "BlockedKeysCube"
    _bk_cube.location = (0.0, 0.0, 0.0)
    _bk_cube.keyframe_insert("location", index=0, frame=1.5)  # the snap moves it
    _bk_cube.keyframe_insert("location", index=1, frame=1)  # ends at 1: untied
    _bk_cube.location = (2.0, 0.0, 0.0)
    _bk_cube.keyframe_insert("location", index=0, frame=10)
    _bk_exp = SceneExporter()
    _bk_exp.confirm = lambda question: False
    _bk_log = _BlockedLog()
    _bk_exp.logger.addHandler(_bk_log)
    try:
        _bk_result = _bk_exp.perform_export(
            export_dir=out_dir,
            objects=[_bk_cube],
            output_name="blocked_keys",
            export_visible=True,
            tasks={"snap_keys_to_frame": True, "check_untied_keyframes": True},
        )
    finally:
        _bk_exp.logger.removeHandler(_bk_log)
    _bk_blocked = [m for m in _bk_log.messages if "Export blocked" in m]
    check(
        "a blocked export returns False and says so once",
        _bk_result is False and len(_bk_blocked) == 1,
        f"{_bk_result} {_bk_log.messages}",
    )
    check(
        "...naming the key edits the snap kept",
        bool(_bk_blocked) and "key edits" in _bk_blocked[0],
        f"{_bk_blocked}",
    )
    check(
        "...and no material or texture edit that never happened",
        bool(_bk_blocked)
        and "material" not in _bk_blocked[0]
        and "texture" not in _bk_blocked[0],
        f"{_bk_blocked}",
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
    icarrier = next((o for o in imported if o.name.startswith(DataNodes.EXPORT)), None)
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

    # ---- Below floor: a depth spin box, 0 = OFF (mirrors mayatk, 2026-09-13) --
    _bf_spec = _tm_defs.check_definitions["check_objects_below_floor"]
    check(
        "the below-floor check is a depth spin box, OFF at zero, under a fresh objectName",
        _bf_spec["widget_type"] == "SpinBox"
        and _bf_spec["object_name"] == "floor_depth"
        and _bf_spec["setCustomDisplayValues"] == {0: "OFF"}
        and _bf_spec["value_method"] == "value"
        and _bf_spec["setValue"] == _tm_defs._DEFAULT_FLOOR_TOLERANCE,
        detail=f"{_bf_spec}",
    )
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, -0.75))
    _bf_cube = bpy.context.active_object  # min z = -1.75
    _tm_defs.objects = [_bf_cube]
    check(
        "0, None and False disable the check; True means the default depth; a depth is a limit",
        _tm_defs.check_objects_below_floor(0)[0]
        and _tm_defs.check_objects_below_floor(None)[0]
        and _tm_defs.check_objects_below_floor(False)[0]
        and not _tm_defs.check_objects_below_floor(True)[0]
        and not _tm_defs.check_objects_below_floor(1.0)[0]
        and _tm_defs.check_objects_below_floor(2.0)[0],
        detail=f"{_tm_defs.check_objects_below_floor(True)}",
    )
    bpy.data.objects.remove(_bf_cube, do_unlink=True)

    # ---- Duplicate Names: one check, four widths (mirrors mayatk) -------------
    # Added: 2026-08-29. The check was Empty-only, so a duplicate bone name
    # (which breaks skinning and retargeting on import) or a duplicate mesh
    # name shipped unreported. Each tier must catch what the narrower one
    # cannot, and catch nothing more. Blender force-uniques object names, so
    # every collision here is the auto ".001" suffix the check strips.
    _dn_tm = _tm_defs

    def _dn_link(obj):
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def _dn_empty(name):
        return _dn_link(bpy.data.objects.new(name, None))

    def _dn_mesh(name):
        return _dn_link(bpy.data.objects.new(name, bpy.data.meshes.new(name)))

    def _dn_rig(name, bone):
        """An armature object called *name* carrying one bone called *bone*."""
        obj = _dn_link(bpy.data.objects.new(name, bpy.data.armatures.new(name)))
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bone = obj.data.edit_bones.new(bone)
        edit_bone.head, edit_bone.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
        return obj

    def _dn_run(objects, scope):
        _dn_tm.objects = objects
        return _dn_tm.check_duplicate_names(scope)

    _dn_empties = [_dn_empty("SNAP"), _dn_empty("SNAP")]
    _dn_ok, _dn_msgs = _dn_run(_dn_empties, "locators")
    check(
        "Locators tier flags two Empties sharing a base name",
        not _dn_ok and any("SNAP" in m for m in _dn_msgs),
        f"ok={_dn_ok} msgs={_dn_msgs}",
    )
    check(
        "the retired pre-dial check stays removed (2026-09-21)",
        not hasattr(_dn_tm, "check_duplicate_locator_names"),
    )
    _dn_ok, _dn_msgs = _dn_tm.check_duplicate_names("al")
    check(
        "an unknown scope fails loudly instead of narrowing",
        # "al" falls through the resolver to its widest branch's neighbour --
        # a NARROWER tier than asked for, which would pass on that basis.
        not _dn_ok and any("Unknown duplicate-name scope" in m for m in _dn_msgs),
        f"ok={_dn_ok} msgs={_dn_msgs}",
    )
    check(
        "OFF short-circuits before touching the scene",
        all(
            _dn_tm.check_duplicate_names(v) == (True, [])
            for v in (None, False, "", "OFF")
        ),
        "a falsy scope still ran the scan",
    )

    # Bones share the FBX node namespace: DISTINCT armature objects, one bone
    # name between them -- so only the bone half can be what collides.
    _dn_rigs = [_dn_rig("RIG_A", "spine"), _dn_rig("RIG_B", "spine")]
    check(
        "Locators tier ignores a bone collision",
        _dn_run(_dn_rigs, "locators")[0],
        "the Empty-only tier flagged armature bones",
    )
    _dn_ok, _dn_msgs = _dn_run(_dn_rigs, "joints")
    check(
        "Locators & Joints tier flags a bone name shared across armatures",
        not _dn_ok and any("spine" in m for m in _dn_msgs),
        f"ok={_dn_ok} msgs={_dn_msgs}",
    )

    # A constrained pair: inert to every narrower tier, caught by Connected.
    _dn_props = [_dn_mesh("PROP"), _dn_mesh("PROP")]
    for _dn_obj in _dn_props:
        _dn_obj.constraints.new(type="COPY_LOCATION").target = _dn_empties[0]
    check(
        "Connected & Animated is the narrowest tier that flags a constrained pair",
        _dn_run(_dn_props, "locators")[0]
        and _dn_run(_dn_props, "joints")[0]
        and not _dn_run(_dn_props, "connected")[0],
        f"scopes={[_dn_run(_dn_props, s)[0] for s in ('locators', 'joints', 'connected')]}",
    )

    # Inert geometry: only the widest tier has any reason to look at it.
    _dn_crates = [_dn_mesh("CRATE"), _dn_mesh("CRATE")]
    check(
        "All Export Objects is the only tier that flags inert geometry",
        all(_dn_run(_dn_crates, s)[0] for s in ("locators", "joints", "connected"))
        and not _dn_run(_dn_crates, "all")[0],
        f"all_tier={_dn_run(_dn_crates, 'all')}",
    )

    _dn_spec = _dn_tm.check_definitions["check_duplicate_names"]
    _dn_keys = list(_dn_spec)
    check(
        "the check renders as a labelled scope combo opening on Locators",
        _dn_spec["widget_type"] == "ComboBox"
        and _dn_spec["set_row_label"] == "Duplicate Names"
        and list(_dn_spec["add"])
        == [
            "OFF",
            "Locators",
            "Locators & Joints",
            "Connected & Animated",
            "All Export Objects",
        ]
        and _dn_spec["add"]["OFF"] is None
        # ``add`` lands the combo on index 0 (OFF), so the default index has
        # to be applied AFTER it -- set_attributes walks the dict in order.
        and _dn_keys.index("add") < _dn_keys.index("setCurrentIndex")
        and list(_dn_spec["add"])[_dn_spec["setCurrentIndex"]] == "Locators",
        f"spec={ {k: v for k, v in _dn_spec.items() if k != 'setToolTip'} }",
    )
    check(
        "the pre-dial key is gone from the panel definitions",
        "check_duplicate_locator_names" not in _dn_tm.check_definitions,
        "the retired checkbox still renders a row",
    )

    for _dn_obj in _dn_empties + _dn_rigs + _dn_props + _dn_crates:
        bpy.data.objects.remove(_dn_obj, do_unlink=True)
    _dn_tm.objects = None

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
            _scene_state.SceneState.read = classmethod(lambda cls, *a, **k: sections)

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
        tm_ct.run = tm_ct.run.replace(texture_write_back=True)
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
        tm_st.run = tm_st.run.replace(output_format="glb")  # temp staging
        tm_st.run = tm_st.run.replace(texture_write_back=False)
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
        next(n for n in pu_mat.node_tree.nodes if getattr(n, "image", None) is long_img)
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

    # ---- the same gates for a u#_v# set, which Blender stores as <UVTILE> (a
    # create_pbr_material build of *.u1_v1 + *.u2_v1 tiles does, measured 5.1). The
    # helpers substituted <UDIM> alone, so a valid UVTILE set read as missing and
    # its tiles were never size-probed.
    uvtile = bpy.data.images.new("uvtile_set", 4, 4, tiled=True)
    uvtile.filepath = os.path.join(tex_dir, "uvtile_set.<UVTILE>.png")
    pu_mat.node_tree.nodes.new("ShaderNodeTexImage").image = uvtile
    passed, msgs = tm_pu.check_valid_paths(True)
    check(
        "check_valid_paths fails a UVTILE image with no tiles on disk",
        passed is False and any("uvtile_set" in m for m in msgs),
        f"msgs={msgs}",
    )
    with open(os.path.join(tex_dir, "uvtile_set.u1_v1.png"), "wb") as fh:
        fh.write(b"\0" * 1024)
    passed, msgs = tm_pu.check_valid_paths(True)
    check(
        "check_valid_paths passes a UVTILE image whose first tile (u1_v1) exists",
        passed is True,
        f"msgs={msgs}",
    )
    with open(os.path.join(tex_dir, "uvtile_set.u2_v1.png"), "wb") as fh:
        fh.write(b"\0" * (2 * 1024 * 1024))  # 2 MB -- the tile that must be probed
    passed, msgs = tm_pu.check_texture_file_size(1)
    check(
        "check_texture_file_size probes the largest existing UVTILE tile",
        passed is False and any("uvtile_set.u2_v1" in m for m in msgs),
        f"msgs={msgs}",
    )
    pu_mat.node_tree.nodes.remove(
        next(n for n in pu_mat.node_tree.nodes if getattr(n, "image", None) is uvtile)
    )
    bpy.data.images.remove(uvtile)

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
    # The row is read by pythontk's one reading (ExportProfile.
    # texture_size_limit_bytes), which both exporters share: a negative limit
    # is OFF, not a gate every map fails.
    passed, msgs = tm_pu.check_texture_file_size(-1)
    check(
        "a negative size limit is OFF (ptk.ExportProfile.texture_size_limit_bytes)",
        passed is True and msgs == [],
        f"msgs={msgs}",
    )
    # The failure names the fix by the Optimize Textures dial the run used: a
    # production "Optimize" run failed this gate and read as if the pass never
    # ran -- without a ceiling it never resamples (mirrors mayatk, 2026-09-13).
    for optimize, max_size, expected in (
        (False, None, "Optimize Textures is OFF"),
        (True, None, "no size ceiling"),
        (True, 1024, "clamped to 1024 px"),
    ):
        tm_pu.run = tm_pu.run.replace(optimize_textures=optimize)
        tm_pu.run = tm_pu.run.replace(texture_max_size=max_size)
        passed, msgs = tm_pu.check_texture_file_size(1)
        check(
            f"check_texture_file_size names the remedy ({expected})",
            passed is False and any(expected in m for m in msgs),
            f"msgs={msgs}",
        )
    tm_pu.run = tm_pu.run.replace(optimize_textures=False)
    tm_pu.run = tm_pu.run.replace(texture_max_size=None)
    # A GLB-only export ships no scene map (its GLB pass re-encodes each one),
    # so the check steps aside; FBX + GLB still gates and names the FBX as the
    # carrier (mirrors mayatk, 2026-09-13).
    tm_pu.run = tm_pu.run.replace(output_format="glb")
    passed, msgs = tm_pu.check_texture_file_size(1)
    check(
        "check_texture_file_size steps aside for GLB-only output",
        passed is True and msgs == [],
        f"msgs={msgs}",
    )
    tm_pu.run = tm_pu.run.replace(output_format="fbx_glb")
    passed, msgs = tm_pu.check_texture_file_size(1)
    check(
        "check_texture_file_size names the FBX as the carrier for FBX + GLB",
        passed is False and "the FBX" in msgs[0],
        f"msgs={msgs}",
    )
    tm_pu.run = tm_pu.run.replace(output_format="fbx")

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
    gcube[LightmapRecords.LIGHTMAP_INFO_PROP] = json.dumps(
        {"map": "GlbOrderCube_Lightmap.exr", "intensity": 1.0}
    )
    LightmapRecords.refresh_export_metadata()

    # ---- USD output format (mirror of mayatk) --------------------------------

    usd_dir = os.path.join(tmp, "usd_format")
    os.makedirs(usd_dir, exist_ok=True)
    bpy.ops.mesh.primitive_cube_add()
    ucube = bpy.context.active_object
    ucube.name = "UsdFormatCube"
    exp_usd = SceneExporter()
    result = exp_usd.perform_export(
        export_dir=usd_dir,
        objects=[ucube],
        output_name="usd_format.fbx",  # a typed .fbx must not leak into the name
        export_visible=True,
        tasks={"output_format": "usd"},
    )
    usd_path = exp_usd.export_path
    check(
        "usd output format writes a real USD layer (and no FBX)",
        result is True
        and os.path.basename(usd_path) == "usd_format.usd"
        and os.path.isfile(usd_path)
        and ptk.UsdFile.is_usd_file(usd_path)
        and not os.path.exists(os.path.join(usd_dir, "usd_format.fbx")),
        f"result={result} path={usd_path}",
    )
    try:
        from pxr import Usd

        stage = Usd.Stage.Open(usd_path)
        names = {prim.GetName() for prim in stage.Traverse()}
        check(
            "usd output format: the object is a prim in the layer",
            "UsdFormatCube" in names,
            str(sorted(names)),
        )
    except ImportError:
        pass

    # inert knobs are reported, and animation samples only its own span
    from blendertk.env_utils.usd import UsdUtils

    captured_usd = {}

    def _fake_usd_export(
        filepath=None, objects=None, selection_only=True, frame_range=None, **opts
    ):
        captured_usd["frame_range"] = frame_range
        captured_usd["opts"] = dict(opts)
        with open(filepath, "w") as fh:
            fh.write("#usda 1.0")
        return filepath

    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 100
    ucube.keyframe_insert("location", frame=5)
    ucube.location.z += 1
    ucube.keyframe_insert("location", frame=12)
    orig_usd = UsdUtils.export
    UsdUtils.export = staticmethod(_fake_usd_export)
    try:
        exp_usd2 = SceneExporter()
        exp_usd2.perform_export(
            export_dir=usd_dir,
            objects=[ucube],
            output_name="usd_anim",
            export_visible=True,
            preset_name="game_asset",
            tasks={"output_format": "usd", "set_bake_animation_range": True},
        )
    finally:
        UsdUtils.export = orig_usd
        ucube.animation_data_clear()
    check(
        "usd output format samples only the animated span",
        tuple(captured_usd.get("frame_range") or ()) == (5, 12)
        and captured_usd["opts"].get("export_animation") is True
        and captured_usd["opts"].get("generate_preview_surface") is True,
        str(captured_usd),
    )

    glb_dir = os.path.join(tmp, "glb_order")
    os.makedirs(glb_dir, exist_ok=True)
    exp8 = SceneExporter()
    # Deterministic conversion failure (ptk.MeshConvert is environment-dependent).
    exp8.task_manager.create_glb = lambda fbx_path=None, announce=True: None
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
    def _fake_glb(fbx_path=None, announce=True):
        p = os.path.splitext(fbx_path)[0] + ".glb"
        with open(p, "wb") as fh:
            fh.write(b"GLBDATA")
        return p

    exp9 = SceneExporter()
    exp9.task_manager.create_glb = _fake_glb
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

    # ---- FBX+GLB: the sidecar must be the LAST step ------------------------------------
    # glb-only ordering is covered above; this is the mode that was actually wrong -- the
    # sidecar was written BEFORE the conversion, so nothing it recorded could describe the
    # GLB that shipped. Mirror of mayatk's TestSidecarWriteOrdering.
    order = []

    def _ordered_glb(fbx_path=None, announce=True):
        order.append("glb")
        p = os.path.splitext(fbx_path or "")[0] + ".glb" if fbx_path else None
        if p:
            with open(p, "wb") as fh:
                fh.write(b"GLBDATA")
        return p

    both_dir = os.path.join(tmp, "fbx_glb_order")
    os.makedirs(both_dir, exist_ok=True)
    exp10 = SceneExporter()
    exp10.task_manager.create_glb = _ordered_glb
    _real_sidecar = exp10.task_manager.write_scene_data_sidecar

    def _ordered_sidecar(**kwargs):
        order.append("sidecar")
        return _real_sidecar(**kwargs)

    exp10.task_manager.write_scene_data_sidecar = _ordered_sidecar
    result = exp10.perform_export(
        export_dir=both_dir,
        objects=[gcube],
        output_name="both_order",
        export_visible=True,
        tasks={"export_data_node": True, "output_format": "fbx_glb"},
    )
    check(
        "fbx+glb writes the sidecar AFTER the glb, so it can describe the deliverable",
        result is True and order == ["glb", "sidecar"],
        f"result={result} order={order}",
    )

    # A failed conversion must not cost the FBX its sidecar: the FBX still shipped, and
    # _create_glb never raises (every failure path inside it logs and returns None), which
    # is what makes writing the sidecar after it safe.
    fail_dir = os.path.join(tmp, "fbx_glb_fail")
    os.makedirs(fail_dir, exist_ok=True)
    exp11 = SceneExporter()
    exp11.task_manager.create_glb = lambda fbx_path=None, announce=True: None
    result = exp11.perform_export(
        export_dir=fail_dir,
        objects=[gcube],
        output_name="both_fail",
        export_visible=True,
        tasks={"export_data_node": True, "output_format": "fbx_glb"},
    )
    check(
        "a failed glb still leaves the fbx's sidecar written",
        result is True
        and SceneDataSidecar.read_manifest(os.path.join(fail_dir, "both_fail.fbx"))
        is not None,
        f"result={result}",
    )

    # ---- Check scheduling: CHECK_DEPENDENCIES hoists each check above the tasks it does
    # not read (pythontk TaskFactory._schedule), so a failing gate aborts BEFORE the
    # texture and animation phases run. blendertk declared no map until 2026-09-13, so
    # every check ran after the whole pipeline. Mirror of mayatk's TestCheckScheduling.
    from unittest import mock as _sched_mock
    from blendertk.env_utils.scene_exporter.task_manager import TaskManager as _TM

    _sched_tm = SceneExporter().task_manager
    _sched_checks = {
        n for n in dir(_TM) if n.startswith("check_") and n != "check_definitions"
    }
    check(
        "every check declares what it reads (an undeclared one runs after EVERY task)",
        not (_sched_checks - set(_TM.CHECK_DEPENDENCIES)),
        f"undeclared={sorted(_sched_checks - set(_TM.CHECK_DEPENDENCIES))}",
    )
    check(
        "no entry names a check that does not exist",
        all(hasattr(_TM, n) for n in _TM.CHECK_DEPENDENCIES),
        f"{[n for n in _TM.CHECK_DEPENDENCIES if not hasattr(_TM, n)]}",
    )
    _sched_bad = {
        c: [t for t in ts if t not in _TM.TASK_ORDER]
        for c, ts in _TM.CHECK_DEPENDENCIES.items()
    }
    check(
        "every dependency names a TASK_ORDER task (an unknown name hoists to the front)",
        not any(_sched_bad.values()),
        f"{ {k: v for k, v in _sched_bad.items() if v} }",
    )
    _sched_tasks = {n: True for n in _TM.TASK_ORDER}
    _sched_order = list(
        _sched_tm._schedule(
            _sched_tasks, {"check_referenced_objects": True, "check_valid_paths": True}
        )
    )
    check(
        "a scene-wide check is decided before the scene is touched",
        _sched_order[0] == "check_referenced_objects",
        f"{_sched_order[:3]}",
    )
    _sched_after = _sched_order[_sched_order.index("check_valid_paths") :]
    check(
        "a path check is decided before the animation phase",
        all(
            t in _sched_after
            for t in ("smart_bake", "optimize_keys", "tie_all_keyframes")
        ),
        f"{_sched_order}",
    )
    check(
        "TASK_ORDER is never reordered by scheduling",
        [
            n
            for n in _sched_tm._schedule(
                _sched_tasks, {n: True for n in _TM.CHECK_DEPENDENCIES}
            )
            if n in _sched_tasks
        ]
        == list(_TM.TASK_ORDER),
    )
    _sched_ran = []
    with (
        _sched_mock.patch.object(
            _TM,
            "reassign_duplicate_materials",
            lambda self_tm: _sched_ran.append("reassign"),
        ),
        _sched_mock.patch.object(
            _TM, "smart_bake", lambda self_tm: _sched_ran.append("bake")
        ),
        _sched_mock.patch.object(
            _TM,
            "check_referenced_objects",
            lambda self_tm: (_sched_ran.append("check"), (False, ["a library link"]))[
                1
            ],
        ),
    ):
        _sched_passed = _sched_tm.run_tasks(
            {
                "reassign_duplicate_materials": True,
                "smart_bake": True,
                "check_referenced_objects": True,
            }
        )
    check(
        "a failing early check never reaches the costly tasks",
        _sched_passed is False and _sched_ran == ["check"],
        f"ran={_sched_ran}",
    )

    # ---- Shared tables + declared parity gaps (2026-09-13) ----------------------------
    # TASK_ORDER / CHECK_DEPENDENCIES are ptk.ExportProfile's, scoped to this class by
    # its decorator; what is scoped away is PARITY_GAPS, pinned here so porting a name
    # means deleting its entry and a task mayatk adds shows up as an undeclared gap.
    _gaps = ptk.ExportProfile.unimplemented(_TM)
    check(
        "the scoped tables' gap is exactly the declared PARITY_GAPS",
        set(_gaps["tasks"] + _gaps["checks"]) == set(_TM.PARITY_GAPS),
        f"derived={_gaps} declared={sorted(_TM.PARITY_GAPS)}",
    )
    check(
        "TASK_ORDER is the shared order minus the gaps",
        _TM.TASK_ORDER
        == [t for t in ptk.ExportProfile.TASK_ORDER if t not in _TM.PARITY_GAPS],
        f"{_TM.TASK_ORDER}",
    )
    check(
        "check_output_writable is ported and decided before the first task",
        callable(getattr(_TM, "check_output_writable", None))
        and _TM.CHECK_DEPENDENCIES.get("check_output_writable") == ()
        and list(_sched_tm._schedule(_sched_tasks, {"check_output_writable": True}))[0]
        == "check_output_writable",
    )
    _ow_tm = SceneExporter().task_manager
    _ow_tm.begin_run(
        ptk.ExportRun(export_path=os.path.join(tmp, "ow.fbx"), output_format="fbx_glb")
    )
    check(
        "check_output_writable passes on destinations that do not exist yet",
        _ow_tm.check_output_writable() == (True, [])
        and _ow_tm._deliverable_paths()
        == [os.path.join(tmp, "ow.fbx"), os.path.join(tmp, "ow.glb")],
        f"{_ow_tm._deliverable_paths()}",
    )

    # ---- _live_objects: a removed datablock never poisons a bulk read -----------------
    _lo_tm = SceneExporter().task_manager
    _lo_mesh = bpy.data.meshes.new("LiveMesh")
    _lo_obj = bpy.data.objects.new("LiveObj", _lo_mesh)
    _lo_keep = bpy.data.objects.new("KeepObj", bpy.data.meshes.new("KeepMesh"))
    _lo_tm.objects = [_lo_obj, _lo_keep]
    bpy.data.objects.remove(_lo_obj, do_unlink=True)
    check(
        "_live_objects drops a removed object and the material read survives",
        [o.name for o in _lo_tm._live_objects()] == ["KeepObj"]
        and _lo_tm._get_all_materials() == [],
    )
    bpy.data.objects.remove(_lo_keep, do_unlink=True)

    # ---- exclude_hdr: a real filter (was a documented no-op) --------------------------
    _hdr_tm = SceneExporter().task_manager
    _hdr_light_data = bpy.data.lights.new("HdrSun", type="SUN")
    _hdr_light_data.use_nodes = True
    _hdr_light_data.node_tree.nodes.new("ShaderNodeTexEnvironment")
    _hdr_light = bpy.data.objects.new("HdrSun", _hdr_light_data)
    _plain_light = bpy.data.objects.new(
        "PlainSun", bpy.data.lights.new("PlainSun", type="SUN")
    )
    _dome_mat = bpy.data.materials.new("HdrDome")
    _dome_mat.use_nodes = True
    _dome_mat.node_tree.nodes.new("ShaderNodeTexEnvironment")
    _dome = bpy.data.objects.new("HdrDome", bpy.data.meshes.new("HdrDomeMesh"))
    _dome.data.materials.append(_dome_mat)
    _prop = bpy.data.objects.new("Prop", bpy.data.meshes.new("PropMesh"))
    _prop.data.materials.append(bpy.data.materials.new("PlainMat"))
    _hdr_tm.objects = [_hdr_light, _plain_light, _dome, _prop]
    _hdr_tm.exclude_hdr()
    check(
        "exclude_hdr drops the environment-textured light and the HDR dome only",
        [o.name for o in _hdr_tm.objects] == ["PlainSun", "Prop"],
        f"{[o.name for o in _hdr_tm.objects]}",
    )
    # The exclude_hdr(enabled) form (deprecated 2026-09-15) was retired
    # 2026-09-21: the task takes nothing, like mayatk's, and the dispatcher
    # gates it on its checkbox (TaskFactory._task_is_disabled counts positional
    # parameters).
    _hdr_tm.objects = [_hdr_light, _plain_light, _dome, _prop]
    _refused = []
    for _args, _kwargs in (((False,), {}), ((), {"enabled": False})):
        try:
            _hdr_tm.exclude_hdr(*_args, **_kwargs)
        except TypeError:
            _refused.append(True)
    check(
        "the retired exclude_hdr(enabled) forms are refused and strip nothing",
        _refused == [True, True]
        and [o.name for o in _hdr_tm.objects]
        == ["HdrSun", "PlainSun", "HdrDome", "Prop"],
        f"refused={_refused} objects={[o.name for o in _hdr_tm.objects]}",
    )
    check(
        "the dispatcher still gates Exclude HDR on its checkbox",
        _hdr_tm._task_is_disabled(_hdr_tm.exclude_hdr, False)
        and not _hdr_tm._task_is_disabled(_hdr_tm.exclude_hdr, True),
    )
    # The Visible scope holds a visible mesh dome, which is why the panel keeps
    # Exclude HDR on screen there (mayatk hides it: its sky dome is a light).
    from blendertk.display_utils._display_utils import DisplayUtils

    bpy.context.scene.collection.objects.link(_dome)
    _visible = DisplayUtils.get_visible_geometry()
    _hdr_tm.objects = list(_visible)
    _hdr_tm.exclude_hdr()
    check(
        "the Visible scope can hold an HDR dome, and exclude_hdr strips it there",
        _dome in _visible and _dome not in _hdr_tm.objects,
        f"visible={[o.name for o in _visible]}",
    )
    for _o in (_hdr_light, _plain_light, _dome, _prop):
        bpy.data.objects.remove(_o, do_unlink=True)

    # ---- Exclude Rig Helpers: only the deforming bones are written --------------
    # Mirror of mayatk's census-based drop. A Blender rig's apparatus is its
    # control and mechanism BONES, and the exporter's own deform-only mode leaves
    # them out -- keeping a non-deform bone that deform bones hang under, so the
    # hierarchy carrying them survives.
    from blendertk.rig_utils._rig_utils import RigUtils as _RhRig
    from blendertk.env_utils.webxr_preview import WebXrPreview as _RhPreview
    from blendertk.env_utils.handoff_export import BlenderExportMixin as _RhMixin

    _rh_dir = os.path.join(tmp, "rig_helpers")
    os.makedirs(_rh_dir, exist_ok=True)
    _rh_arm = _RhRig.create_armature("rh_skel")
    _rh_hold = _RhRig.add_bone_chain(_rh_arm, [(0, 0, 0), (0, 0, 1)], prefix="rh_hold")
    _rh_body = _RhRig.add_bone_chain(
        _rh_arm, [(0, 0, 1), (0, 0, 2), (0, 0, 3)], prefix="rh_body"
    )
    _rh_ctrl = _RhRig.add_bone_chain(_rh_arm, [(1, 0, 0), (1, 0, 1)], prefix="rh_ctrl")
    with _RhRig._active_mode(_rh_arm, "EDIT"):
        _rh_arm.data.edit_bones[_rh_body[0]].parent = _rh_arm.data.edit_bones[
            _rh_hold[0]
        ]
    for _name in _rh_hold + _rh_ctrl:
        _rh_arm.data.bones[_name].use_deform = False
    _rh_mesh = bpy.data.objects.new("rh_mesh", bpy.data.meshes.new("rh_mesh"))
    _rh_mesh.data.from_pydata([(0, 0, 1), (1, 0, 2), (0, 1, 3)], [], [(0, 1, 2)])
    bpy.context.scene.collection.objects.link(_rh_mesh)
    for _name in _rh_body:
        _rh_mesh.vertex_groups.new(name=_name).add([0, 1, 2], 1.0, "REPLACE")
    _rh_mesh.modifiers.new("Armature", "ARMATURE").object = _rh_arm

    def _rh_bones(path):
        return set(ptk.FbxFile.load(path, raw_payloads=False).object_names("Model"))

    _rh_names = {}
    for _label, _tasks in (
        ("kept", {}),
        ("dropped", {"drop_rig_apparatus": True}),
    ):
        _rh_result = SceneExporter().perform_export(
            export_dir=_rh_dir,
            objects=[_rh_mesh, _rh_arm],
            output_name=f"rig_helpers_{_label}",
            tasks=_tasks,
        )
        _rh_file = os.path.join(_rh_dir, f"rig_helpers_{_label}.fbx")
        _rh_names[_label] = _rh_bones(_rh_file) if _rh_result else set()
    check(
        "Exclude Rig Helpers: the control bones stay out, the deform chain and "
        "the non-deform bone holding it ship",
        set(_rh_ctrl) <= _rh_names["kept"]
        and not (set(_rh_ctrl) & _rh_names["dropped"])
        and set(_rh_body + _rh_hold) <= _rh_names["dropped"],
        f"{sorted(_rh_names['dropped'])}",
    )
    check(
        "Exclude Rig Helpers is a default-on settings mode (a DCC hand-off opts out)",
        SceneExporter().task_manager.task_definitions["drop_rig_apparatus"][
            "setChecked"
        ]
        is True
        and "drop_rig_apparatus" in ptk.ExportRun.MODE_KEYS
        and _RhPreview.drop_rig_apparatus is True
        and _RhMixin.drop_rig_apparatus is False,
    )
    _rh_preview_fbx = os.path.join(_rh_dir, "preview.fbx")
    _RhPreview()._export_fbx(
        [_rh_mesh, _rh_arm], _rh_preview_fbx, dict(_RhPreview().params_defaults())
    )
    check(
        "the WebXR preview payload leaves out the bones the row leaves out",
        os.path.isfile(_rh_preview_fbx)
        and not (set(_rh_ctrl) & _rh_bones(_rh_preview_fbx))
        and set(_rh_body) <= _rh_bones(_rh_preview_fbx),
        f"{sorted(_rh_bones(_rh_preview_fbx)) if os.path.isfile(_rh_preview_fbx) else 'no file'}",
    )
    for _o in (_rh_mesh, _rh_arm):
        bpy.data.objects.remove(_o, do_unlink=True)

    # ---- Animation Clips: the takes row is the three-mode combo (mirror of mayatk) ----
    _clips_tm = SceneExporter().task_manager
    _clips_tm.begin_run(ptk.ExportRun())
    _clips_tm.apply_declared_takes("full")
    check(
        "apply_declared_takes records the Animation Clips choice for create_glb",
        _clips_tm._clip_mode == "full"
        and ptk.ExportRun.clip_mode(True) == "both"
        and ptk.ExportRun.clip_mode(None) == "full"
        and ptk.ExportRun.clip_mode("Shots") == "shots",
    )
    _clips_tm.objects = list(_clips_tm.objects or [])
    check(
        "assigning objects mid-run keeps the choice; begin_run resets it",
        _clips_tm._clip_mode == "full"
        and (_clips_tm.begin_run(ptk.ExportRun()) or _clips_tm._clip_mode == "both"),
    )
    _clips_row = _clips_tm.task_definitions["apply_declared_takes"]
    check(
        "the Animation Clips row is a combo keyed by a fresh objectName",
        _clips_row["widget_type"] == "ComboBox"
        and _clips_row["object_name"] == "animation_clips"
        and _clips_row["add"] is ptk.ExportProfile.ANIMATION_CLIPS_OPTIONS
        and _clips_row["setCurrentIndex"] == 2,
    )

    # ---- smart_bake stages its own restore (was a block in perform_export's finally) --
    from types import SimpleNamespace as _NS

    _sb_tm = SceneExporter().task_manager
    _sb_tm.begin_run(ptk.ExportRun())
    _sb_tm.objects = []
    with _sched_mock.patch(
        "blendertk.anim_utils.smart_bake._smart_bake.SmartBake"
    ) as _Baker:
        _Baker.return_value.analyze.return_value = {"x": _NS(requires_bake=True)}
        _Baker.return_value.bake.return_value = _NS(
            session_id="bake_s1", baked_count=1, time_range=(1, 10)
        )
        _sb_tm.smart_bake()
        _sb_staged = "smart_bake" in _sb_tm._deferred_restores
        _sb_tm.run_deferred_restores()
        _sb_restored = _Baker.restore.call_args_list == [_sched_mock.call("bake_s1")]
    check(
        "smart_bake stages its restore and run_deferred_restores restores the session",
        _sb_staged and _sb_restored and _sb_tm._bake_session_id is None,
        f"staged={_sb_staged} restore_calls={_Baker.restore.call_args_list}",
    )

    # ---- Texture File Type: ONE container dial for every texture the export ships -------
    # Replaces the GLB-only carrier (glb_texture_format) and its redundant companion
    # "Optimize GLB Textures" flag (the general Optimize Textures covers resolution now).
    # Each destination clamps what it cannot carry: no scene image or FBX importer reads
    # KTX2, and a GLB can only embed what glTF accepts. Mirror of mayatk's
    # TestGeneralTextureFileType. BACKLOG 2026-08-12 is why the resize half exists at all:
    # the exporter converted to GLB and stopped, shipping authored 4096-square PNGs while
    # the pass that closes the gap was already wired into the WebXR preview.
    from unittest import mock
    from blendertk.env_utils.scene_exporter.scene_exporter_slots import (
        SceneExporterSlots as _Slots,
    )

    def _fake_convert(src, **kw):
        p = os.path.splitext(src)[0] + ".glb"
        with open(p, "wb") as fh:
            fh.write(b"GLBDATA")
        return p

    _tf_defs = SceneExporter().task_manager.task_definitions
    check(
        "texture_file_type is a Textures-group dial, never a dispatched task",
        _tf_defs.get("texture_file_type", {}).get("group") == "Textures"
        and _tf_defs["texture_file_type"]["widget_type"] == "ComboBox"
        and "texture_file_type" not in SceneExporter().task_manager.TASK_ORDER,
        f"{_tf_defs.get('texture_file_type')}",
    )
    check(
        "the redundant Optimize GLB Textures row is gone",
        "glb_optimize_textures" not in _tf_defs,
    )
    # The WebXR preview offers these rows by the label and table pythontk
    # declares (ExportProfile.GLB_ROWS / the combo tables); a row renamed or
    # re-tabled here without it would leave the two panels naming the same
    # setting differently, and Baked Reflections starts where the lighting
    # recipe itself stands. Mirrors mayatk's test. Added: 2026-09-21
    _glb_tables = {
        "texture_file_type": ptk.ExportProfile.texture_file_type_options(),
        "optimize_textures": ptk.ExportProfile.optimize_textures_options(),
        "secondary_max_size": ptk.ExportProfile.SECONDARY_MAX_SIZE_OPTIONS,
        "uastc_rdo": ptk.ExportProfile.UASTC_RDO_OPTIONS,
        "baked_reflections": ptk.ExportProfile.BAKED_REFLECTIONS_OPTIONS,
    }
    check(
        "the GLB rows are the ones the WebXR preview mirrors",
        set(_glb_tables) == set(ptk.ExportProfile.GLB_ROWS)
        and all(
            _tf_defs[row]["set_row_label"] == label
            and _tf_defs[row]["add"] == _glb_tables[row]
            for row, label in ptk.ExportProfile.GLB_ROWS.items()
        ),
        f"{[(row, _tf_defs[row].get('set_row_label')) for row in ptk.ExportProfile.GLB_ROWS]}",
    )
    _reflections = _tf_defs["baked_reflections"]
    check(
        "Baked Reflections starts at the lighting recipe's own level",
        list(_reflections["add"].values())[_reflections["setCurrentIndex"]]
        == ptk.ExportProfile.baked_reflections_default(),
        f"{_reflections.get('setCurrentIndex')}",
    )
    _tf_options = list(SceneExporter().task_manager._texture_file_type_options.items())
    check(
        "Original is index 0 and falsy (templates persist combos by index)",
        _tf_options[0][0] == "Original" and not _tf_options[0][1],
        f"{_tf_options[:2]}",
    )
    check(
        "KTX2 is offered (a GLB can carry it even though the scene cannot)",
        "ktx2" in dict(_tf_options).values(),
    )
    check(
        "KTX2 + PNG/JPEG is offered too (the importable twin-carrying GLB)",
        SceneExporter().task_manager.KTX2_WITH_FALLBACK in dict(_tf_options).values(),
    )
    check(
        "texture template moved to the Tasks combo as convert_textures (cmb005)",
        _tf_defs.get("convert_textures", {}).get("object_name") == "cmb005"
        and _tf_defs["convert_textures"].get("group") == "Textures"
        and _tf_defs["convert_textures"].get("panel") != "settings",
        f"{_tf_defs.get('convert_textures', {}).get('object_name')}",
    )
    check(
        "the Settings combo has no Textures section (the whole texture block "
        "lives in the Tasks combo, gate row first)",
        "Textures" not in dict(_Slots._SETTINGS_LAYOUT)
        and [k for k in _tf_defs if _tf_defs[k].get("group") == "Textures"]
        == [
            "texture_write_back",
            "convert_textures",
            "optimize_textures",
            "texture_file_type",
            "secondary_max_size",
            "uastc_rdo",
        ],
        f"{list(dict(_Slots._SETTINGS_LAYOUT))}",
    )
    # The GLB key tolerance is the GLB half of Optimize Keys: directly under
    # it, defaulting to the measured 1e-4 (2026-09-14). Mirrors mayatk.
    _anim = [k for k in _tf_defs if _tf_defs[k].get("group") == "Animation"]
    check(
        "GLB Key Tolerance sits directly under Optimize Keys and defaults to 1e-4",
        _anim[_anim.index("optimize_keys") + 1] == "glb_key_tolerance"
        and _tf_defs["glb_key_tolerance"].get("setCurrentIndex") == 2
        and list(_tf_defs["glb_key_tolerance"]["add"].values())[2] == 1e-4,
        f"{_anim}",
    )
    # The dependency rules hide (show_when), and gate the GLB-only, KTX2-only
    # and FBX-only rows. Mirrors mayatk's
    # test_wire_dependencies_hides_irrelevant_settings.
    _calls = []

    class _RuleSB:
        def show_when(self, ui, targets, trigger, condition=True, **kw):
            _calls.append((targets, trigger, condition))

        def enable_when(self, *args, **kw):
            raise AssertionError("greyed out where it should be hidden")

    _rs = _Slots.__new__(_Slots)
    _rs.sb = _RuleSB()
    _rs.ui = object()
    _rs._wire_dependencies()
    _by = {t: (trig, cond) for t, trig, cond in _calls}
    _rdo = _by.get("uastc_rdo", (None, lambda *a: None))[1]
    _keys = _by.get("glb_key_tolerance", (None, lambda *a: None))[1]
    _usd = _by.get(
        "cmb000,animation_clips,bake_range,drop_rig_apparatus",
        (None, lambda *a: None),
    )[1]
    check(
        "the dependency rules are show_when and gate the GLB/KTX2/FBX-only rows",
        _by.get("texture_write_back", (None,))[0] == ["texture_optimize", "cmb005"]
        and _by.get("secondary_max_size") == ("cmb004", {"glb", "fbx_glb"})
        and _by.get("uastc_rdo", (None,))[0] == ["cmb004", "texture_file_type"]
        and (_rdo("glb", "ktx2"), _rdo("fbx", "ktx2"), _rdo("glb", "png"))
        == (True, False, False)
        and _by.get("glb_key_tolerance", (None,))[0] == ["cmb004", "optimize_level"]
        and (_keys("glb", "extremes"), _keys("glb", None), _keys("fbx", "extremes"))
        == (True, False, False)
        and (_usd("fbx"), _usd("usd")) == (True, False),
        f"{sorted(_by)}",
    )
    # A deliberate divergence from mayatk's rule, pinned so a re-mirror cannot
    # restore it: Blender's exclusion also strips a visible mesh dome, which the
    # Visible scope holds (the exclude_hdr checks above), and a hidden row still
    # applies its value.
    check(
        "Exclude HDR has no show_when rule: the Visible scope can hold a dome it strips",
        "exclude_hdr" not in _by,
        f"{_by.get('exclude_hdr')}",
    )

    # The manager's class-shared logger never reaches the panel's txt003 sink
    # (setup_logging_redirect wires the SLOTS logger only), so cmb007_init
    # hands it the slots logger BEFORE wire_combo -- whose active-preset
    # restore is exactly the load that emits the schema-drift "preset doesn't
    # cover N new panel settings" warning. Mirror of mayatk's
    # test_preset_manager_adopts_the_panel_logger.
    _events = []

    class _PresetMgrStub:
        def use_logger(self, logger):
            _events.append(("use_logger", logger))

        def setup(self, **kw):
            _events.append(("setup", None))

        def exclude(self, *names):
            _events.append(("exclude", names))

        def wire_combo(self, widget, placeholder=None):
            _events.append(("wire_combo", widget))

    class _UIStub:
        presets = _PresetMgrStub()

    _sl = _Slots.__new__(_Slots)
    _sl.ui = _UIStub()
    _sl.cmb007_init(object())
    _kinds = [k for k, _ in _events]
    check(
        "cmb007_init adopts the panel logger on the preset manager before wire_combo",
        "use_logger" in _kinds
        and _kinds.index("use_logger") < _kinds.index("wire_combo")
        and _events[_kinds.index("use_logger")][1] is _Slots.logger,
        f"{_kinds}",
    )

    # ---- "Override Checks" (b009) is a per-run escape hatch, not a sticky mode ----------
    # Nothing else resets it (__init__ clears it once, at panel build), so an export forced
    # past a failing check used to leave every later export in the session unvalidated too,
    # silently. b000 disarms it once the deliverable actually shipped; a failed export
    # leaves it armed so a retry does not have to re-arm it by hand. Mirror of mayatk's
    # TestOverrideChecksDisarm. Qt-free: the slot only calls isChecked/setChecked.
    from types import SimpleNamespace

    class _StubWidget:
        def __init__(self):
            self._checked = False

        def text(self):
            return ""

        def currentData(self):
            return None

        def isChecked(self):
            return self._checked

        def setChecked(self, state):
            self._checked = bool(state)

        def clear(self):
            pass

    def _override_slots(export_result, calls, armed=True):
        import contextlib

        _s = _Slots.__new__(_Slots)
        _s.task_manager = SimpleNamespace(task_definitions={}, check_definitions={})
        _s.sb = SimpleNamespace(
            convert_to_legal_name=lambda n: n,
            # The real Switchboard's footer-progress seam is a no-op on a UI
            # without a footer (this one has none); the stub mirrors it.
            progress=lambda **kw: contextlib.nullcontext(lambda *a: True),
            progress_adapter=lambda update: update,
        )
        _s.ui = SimpleNamespace(
            **{
                n: _StubWidget()
                for n in (
                    "txt000",
                    "txt001",
                    "txt003",
                    "b009",
                    "b011",
                    "cmb000",
                    "cmb003",
                    "cmb004",
                )
            }
        )
        _s.ui.b009.setChecked(armed)
        _s.perform_export = lambda **kw: calls.append(kw) or export_result
        _s.save_output_dir = lambda *a: None
        _s.save_output_name = lambda *a: None
        return _s

    _ok_calls = []
    _ok = _override_slots(True, _ok_calls)
    _ok.b000()
    check(
        "a successful export disarms Override Checks",
        len(_ok_calls) == 1 and not _ok.ui.b009.isChecked(),
        f"calls={len(_ok_calls)} checked={_ok.ui.b009.isChecked()}",
    )

    _fail_calls = []
    _fail = _override_slots(False, _fail_calls)
    _fail.b000()
    check(
        "a failed export leaves Override Checks armed for the retry",
        len(_fail_calls) == 1 and _fail.ui.b009.isChecked(),
        f"calls={len(_fail_calls)}",
    )

    # Disarming happens AFTER the run -- the run itself still overrides. The
    # disarmed control is what makes the armed assertion mean anything: without
    # it, a payload missing the check proves only that the stub never
    # collected one.
    def _run_with_one_check(armed):
        calls = []
        _s = _override_slots(True, calls, armed=armed)
        _s.task_manager.check_definitions = {
            "check_framerate": {"object_name": "check_framerate"}
        }
        _s.ui.check_framerate = _StubWidget()
        _s.ui.check_framerate.setChecked(True)
        _s.b000()
        return _s, calls

    _ctl, _ctl_calls = _run_with_one_check(armed=False)
    check(
        "unarmed, the enabled check rides the payload",
        "check_framerate" in _ctl_calls[0]["tasks"],
        f"{sorted(_ctl_calls[0]['tasks'])}",
    )

    _skip, _skip_calls = _run_with_one_check(armed=True)
    check(
        "the armed override still skips the checks on the run that disarms it",
        "check_framerate" not in _skip_calls[0]["tasks"]
        and not _skip.ui.b009.isChecked(),
        f"{sorted(_skip_calls[0]['tasks'])}",
    )

    # -- the GLB half: the rows resolve through ExportRun.glb_texture_params ----------
    _tm = SceneExporter().task_manager

    def _glb_params(file_type=None, optimize=False, max_size=None, template=None):
        _tm.run = _tm.run.replace(texture_file_type=file_type)
        _tm.run = _tm.run.replace(optimize_textures=optimize)
        _tm.run = _tm.run.replace(texture_max_size=max_size)
        _tm.run = _tm.run.replace(texture_template=template)
        return _tm.run.glb_texture_params()

    # CONTRACT CHANGE (2026-08-29): untouched dials used to mean no pass at all.
    # Measured on a production assembly through mayatk's twin of this path, the
    # byte-stable default shipped 280.13 MB where the WebXR preview published
    # 8.71 MB of the same scene. Both dials are now OVERRIDES of one shared
    # web-delivery policy rather than the only thing that turns the pass on.
    # CONTRACT CHANGE (2026-09-21): Optimize Textures OFF resizes nothing -- the
    # policy's container, every pixel kept. Mirrors mayatk's test.
    _policy = ptk.MeshConvert.web_delivery_texture_params()
    _full = {**_policy, "max_size": 0}
    check(
        "neither dial set = the web container at full resolution, not a raw GLB",
        _glb_params() == _full,
        f"{_glb_params()} vs {_full}",
    )
    _p = _glb_params(file_type="webp")
    check(
        "file type alone overrides the container and leaves OFF resizing nothing",
        _p == {**_full, "image_format": "WEBP"},
        f"{_p}",
    )
    _p = _glb_params(optimize=True, max_size=1024)
    check(
        "optimize alone overrides the ceiling and keeps the policy container",
        _p == {**_policy, "max_size": 1024},
        f"{_p}",
    )
    _p = _glb_params(file_type="webp", optimize=True, max_size=1024)
    check(
        "the GLB honors the general Max Texture Size dial (ONE size policy)",
        _p == {**_policy, "image_format": "WEBP", "max_size": 1024},
        f"{_p}",
    )
    _p = _glb_params(file_type="webp", optimize=True, max_size=None)
    check(
        "a dial naming no ceiling takes the policy's, never 'keep every pixel'",
        _p == {**_policy, "image_format": "WEBP"},
        f"{_p}",
    )
    _p = _glb_params(file_type="tga", optimize=False)
    check(
        "a container glTF cannot embed falls back to the web-delivery container",
        _p == _full,
        f"{_p}",
    )
    # The GLB-only dials (2026-09-13): the packed-data ceiling and the UASTC
    # RDO lambda ride the same policy call; the key-reduction bound reaches
    # the pipeline as key_tolerance. Mirrors mayatk's test.
    from unittest import mock as _mock

    _tm.run = _tm.run.replace(
        secondary_max_size=2048, uastc_rdo=1.0, glb_key_tolerance=1e-4
    )
    _p = _tm.run.glb_texture_params()
    check(
        "secondary map size and UASTC RDO ride the policy call",
        (_p.get("secondary_max_size"), _p.get("uastc_rdo")) == (2048, 1.0),
        f"{_p}",
    )
    with (
        _mock.patch.object(
            ptk.GlbPipeline, "build", return_value={"glb": "x.glb"}
        ) as _build,
        _mock.patch.object(ptk.GlbPipeline, "envelope", return_value={}),
    ):
        try:
            _built = _tm.create_glb("x.fbx", announce=False)
        except Exception as _e:  # noqa: BLE001 -- reported by the check
            _built = _e
    check(
        "create_glb forwards the key-reduction bound as key_tolerance",
        _built == "x.glb"
        and _build.called
        and _build.call_args.kwargs.get("key_tolerance") == 1e-4,
        f"built={_built!r} kwargs={_build.call_args.kwargs if _build.called else None}",
    )
    # The Baked Reflections row: one decision, both carriers -- the GLB's
    # envelope and the FBX's handoff record. Mirrors mayatk's tests.
    # Added: 2026-09-21
    _tm.run = _tm.run.replace(baked_reflections=0.5)
    with (
        _mock.patch.object(ptk.GlbPipeline, "build", return_value={"glb": "x.glb"}),
        _mock.patch.object(ptk.GlbPipeline, "envelope", return_value={}) as _envelope,
        _mock.patch.object(_tm, "_live_objects", return_value=["obj"]),
    ):
        _tm.create_glb("x.fbx", announce=False)
    check(
        "create_glb hands the run's lighting choices to the envelope",
        _envelope.called
        and _envelope.call_args.kwargs.get("rendering")
        == {"lightmappedMaterials": {"envMapIntensity": 0.5}},
        f"{_envelope.call_args.kwargs if _envelope.called else None}",
    )
    from blendertk.env_utils.fbx_utils import FbxUtils as _FbxUtils

    _tm.run = _tm.run.replace(baked_reflections=0.0)
    with (
        _mock.patch.object(_FbxUtils, "publish", return_value=None) as _publish,
        _mock.patch.object(_tm, "stage_deferred_restore"),
    ):
        _tm._publish_scene_records()
    check(
        "the FBX handoff record is given the run's lighting choices",
        _publish.called
        and _publish.call_args[0][0].rendering
        == {"lightmappedMaterials": {"envMapIntensity": 0.0}},
        f"{_publish.call_args if _publish.called else None}",
    )
    _tm.run = _tm.run.replace(baked_reflections=None)
    _tm.run = _tm.run.replace(
        secondary_max_size=None, uastc_rdo=None, glb_key_tolerance=None
    )
    _p = _tm.run.glb_texture_params()
    check(
        "unset GLB dials take the policy (off)",
        (_p.get("secondary_max_size"), _p.get("uastc_rdo")) == (0, None),
        f"{_p}",
    )

    # REGRESSION: the dial's value is a file EXTENSION, but optimize_glb_textures
    # passes image_format straight to Pillow and builds the glTF mime as
    # image/<lowercased>. "JPG" raises KeyError in Pillow, and would be an invalid
    # glTF mime if it didn't.
    check(
        "jpg/jpeg reach the encoder as the JPEG format id",
        _glb_params(file_type="jpg")["image_format"] == "JPEG"
        and _glb_params(file_type="jpeg")["image_format"] == "JPEG",
        f"{_glb_params(file_type='jpg')}",
    )

    # -- the scene half: _resolved_output_type ------------------------------------------
    _tm.run = _tm.run.replace(texture_file_type="tga")
    check(
        "a chosen container outranks the template's per-map spec",
        _tm._resolved_output_type("C:/tex/rock_Base_color.png", "glTF 2.0") == "tga",
    )
    _tm.run = _tm.run.replace(texture_file_type="ktx2")
    check(
        "a delivery-only container never reaches a scene image (source kept)",
        _tm._resolved_output_type("C:/tex/rock_Base_color.png", None) == "png",
    )
    # WebP is clamped here even though Blender itself reads it: the constraint
    # is the FBX's consumers (a Maya file node reports a .webp as 0x0, measured
    # 2026-08-25, and a shipped webp-textured FBX bound nothing anywhere).
    _tm.run = _tm.run.replace(texture_file_type="webp")
    check(
        "webp never reaches a scene image or the FBX (source kept)",
        _tm._resolved_output_type("C:/tex/rock_Base_color.png", None) == "png"
        and _tm._resolved_output_type("C:/tex/rock_Base_color.tga", "glTF 2.0")
        == "tga",
        f"{_tm._resolved_output_type('C:/tex/rock_Base_color.png', None)}",
    )
    # (that the GLB still carries webp is pinned above, by the container-only check)
    _tm.run = _tm.run.replace(texture_file_type=None)
    check(
        "Original defers to the template",
        _tm._resolved_output_type("C:/tex/rock_Base_color.png", None) is None,
    )

    # -- parse + stamp -------------------------------------------------------------------
    exp_tf = SceneExporter(log_level="DEBUG")
    tf_dir = os.path.join(tmp, "texture_file_type")
    os.makedirs(tf_dir, exist_ok=True)
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()

    result = exp_tf.perform_export(
        objects=[bpy.context.object],
        export_dir=tf_dir,
        tasks={"output_format": "glb", "texture_file_type": "pngg"},
    )
    check(
        "an unknown texture_file_type aborts loudly at parse (config error)",
        result is False and os.listdir(tf_dir) == [],
        f"result={result}, dir={os.listdir(tf_dir)}",
    )

    exp_ktx = SceneExporter(log_level="DEBUG")
    with mock.patch.object(
        ptk.ImgUtils,
        "resolve_ktx2_encoder",
        side_effect=AssertionError("gate must not run without a GLB"),
    ):
        exp_ktx.perform_export(
            objects=[bpy.context.object],
            export_dir=tf_dir,
            tasks={"output_format": "fbx", "texture_file_type": "ktx2"},
        )
    check(
        "KTX2 is inert (and ungated) for FBX-only output — it ships only in a GLB",
        exp_ktx.task_manager.run.texture_file_type is None,
        f"{exp_ktx.task_manager.run.texture_file_type!r}",
    )

    gate_dir = os.path.join(tmp, "ktx2_gate")
    os.makedirs(gate_dir, exist_ok=True)
    exp_gate = SceneExporter(log_level="DEBUG")
    with mock.patch.object(
        ptk.ImgUtils,
        "resolve_ktx2_encoder",
        side_effect=FileNotFoundError("toktx missing (test)"),
    ):
        gate_result = exp_gate.perform_export(
            objects=[bpy.context.object],
            export_dir=gate_dir,
            tasks={"output_format": "glb", "texture_file_type": "ktx2"},
        )
    check(
        "a missing toktx aborts before any file is written",
        gate_result is False and os.listdir(gate_dir) == [],
        f"result={gate_result}, dir={os.listdir(gate_dir)}",
    )

    # KTX2 + PNG/JPEG reaches every consumer as the ktx2 container, plus the one
    # flag the GLB pass forwards; stamped per run, so plain KTX2 and Original
    # never inherit the previous run's twins. Mirror of mayatk's.
    twin_dir = os.path.join(tmp, "ktx2_fallback")
    os.makedirs(twin_dir, exist_ok=True)
    exp_twin = SceneExporter(log_level="DEBUG")
    twin_seen = []
    for _file_type in (exp_twin.task_manager.KTX2_WITH_FALLBACK, "ktx2", ""):
        with (
            mock.patch.object(ptk.ImgUtils, "ktx2_available", return_value=True),
            mock.patch.object(ptk.ImgUtils, "ensure_ktx2_encoder", return_value=None),
            mock.patch.object(exp_twin, "_initialize_objects", return_value=[]),
        ):
            exp_twin.perform_export(
                objects=[bpy.context.object],
                export_dir=twin_dir,
                tasks={"output_format": "glb", "texture_file_type": _file_type},
            )
        twin_seen.append(
            (
                exp_twin.task_manager.run.texture_file_type,
                exp_twin.task_manager.run.ktx2_fallback,
                exp_twin.task_manager.run.glb_texture_params()["ktx2_fallback"],
            )
        )
    check(
        "KTX2 + PNG/JPEG = the ktx2 container plus the twin flag; plain KTX2 and "
        "Original never inherit it",
        twin_seen
        == [("ktx2", True, True), ("ktx2", False, False), (None, False, False)],
        f"{twin_seen}",
    )

    # Consent seam: a missing toktx is OFFERED through confirm() (the panel's
    # dialog), installed on a yes, and the run carries on past the gate.
    def _fake_resolve_toktx(required=False, auto_install=False, prompt=True):
        if auto_install and ptk.AppInstaller.consent(
            prompt, "KTX-Software (toktx) is not installed. Download it now?"
        ):
            return os.path.join(tmp, "toktx")
        if required:
            raise FileNotFoundError(
                "KTX2 encoding requires 'toktx' (KTX-Software). Install it from "
                "https://github.com/KhronosGroup/KTX-Software/releases"
            )
        return None

    consent_dir = os.path.join(tmp, "ktx2_consent")
    os.makedirs(consent_dir, exist_ok=True)
    exp_consent = SceneExporter(log_level="DEBUG")
    asked = []

    def _consent(question):
        asked.append(question)
        return True

    with (
        mock.patch.multiple(
            ptk.Ktx2Encoder, available=lambda: False, resolve_toktx=_fake_resolve_toktx
        ),
        mock.patch.object(exp_consent, "confirm", side_effect=_consent),
        mock.patch.object(
            exp_consent,
            "_initialize_objects",
            return_value=[],  # first seam past the gate
        ),
    ):
        consent_result = exp_consent.perform_export(
            objects=[bpy.context.object],
            export_dir=consent_dir,
            tasks={"output_format": "glb", "texture_file_type": "ktx2"},
        )
    check(
        "a missing toktx asks confirm() once (naming KTX-Software); a yes passes the gate",
        consent_result is False
        and len(asked) == 1
        and "KTX-Software" in asked[0]
        and exp_consent.task_manager.run.texture_file_type == "ktx2",
        f"result={consent_result}, asked={asked}, "
        f"type={exp_consent.task_manager.run.texture_file_type!r}",
    )

    declined_dir = os.path.join(tmp, "ktx2_declined")
    os.makedirs(declined_dir, exist_ok=True)
    exp_declined = SceneExporter(log_level="DEBUG")
    with (
        mock.patch.multiple(
            ptk.Ktx2Encoder, available=lambda: False, resolve_toktx=_fake_resolve_toktx
        ),
        mock.patch.object(exp_declined, "confirm", return_value=False) as _confirm,
        mock.patch.object(ptk.AppInstaller, "ensure") as _ensure,
    ):
        declined_result = exp_declined.perform_export(
            objects=[bpy.context.object],
            export_dir=declined_dir,
            tasks={"output_format": "glb", "texture_file_type": "ktx2"},
        )
    check(
        "a declined install aborts before any file is written and never downloads",
        declined_result is False
        and _confirm.call_count == 1
        and not _ensure.called
        and os.listdir(declined_dir) == [],
        f"result={declined_result}, confirm={_confirm.call_count}, "
        f"ensure={_ensure.called}, dir={os.listdir(declined_dir)}",
    )

    legacy_dir = os.path.join(tmp, "legacy_glb_format")
    os.makedirs(legacy_dir, exist_ok=True)
    exp_legacy = SceneExporter(log_level="DEBUG")
    delivered = {}

    def _fake_optimize(path, **kw):
        delivered.update(kw, path=path)
        return {"images": 1, "bytes_before": 2e6, "bytes_after": 1e6}

    with (
        mock.patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert),
        mock.patch.object(
            ptk.MeshConvert, "optimize_glb_textures", side_effect=_fake_optimize
        ),
    ):
        legacy_result = exp_legacy.perform_export(
            objects=[bpy.context.object],
            export_dir=legacy_dir,
            tasks={
                "output_format": "glb",
                "glb_texture_format": "WEBP",
                "glb_optimize_textures": True,
            },
        )
    check(
        "a template saved before the unification still loads (legacy key mapped)",
        legacy_result is True
        and exp_legacy.task_manager.run.texture_file_type == "webp"
        and delivered.get("image_format") == "WEBP",
        f"result={legacy_result}, stamp={exp_legacy.task_manager.run.texture_file_type!r}, "
        f"delivered={delivered}",
    )

    exp_both = SceneExporter(log_level="DEBUG")
    with mock.patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert):
        exp_both.perform_export(
            objects=[bpy.context.object],
            export_dir=legacy_dir,
            tasks={
                "output_format": "glb",
                "texture_file_type": "png",
                "glb_texture_format": "WEBP",
            },
        )
    check(
        "the new key wins over the legacy one",
        exp_both.task_manager.run.texture_file_type == "png",
        f"{exp_both.task_manager.run.texture_file_type!r}",
    )

    # REGRESSION: run_tasks returns early on an empty task dict, so a run with
    # nothing checked never reaches the dispatcher. Stamping the texture dials
    # there let the PREVIOUS run's Optimize Textures survive and re-encode the
    # next GLB behind the user; perform_export hands the manager a fresh
    # ExportRun (begin_run) now, which every run goes through.
    exp_stale = SceneExporter(log_level="DEBUG")
    stale_dir = os.path.join(tmp, "stale_state")
    os.makedirs(stale_dir, exist_ok=True)
    with mock.patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert):
        exp_stale.perform_export(
            objects=[bpy.context.object],
            export_dir=stale_dir,
            tasks={"output_format": "glb", "optimize_textures": True},
        )
    first = exp_stale.task_manager.run.optimize_textures
    with mock.patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert):
        exp_stale.perform_export(
            objects=[bpy.context.object],
            export_dir=stale_dir,
            tasks={"output_format": "glb"},
        )
    check(
        "a run with no tasks does not inherit the prior run's texture pass",
        first is True
        and exp_stale.task_manager.run.optimize_textures is False
        # The untouched rows: the policy's container, every pixel kept (OFF
        # resizes nothing since 2026-09-21) -- never the prior run's ceiling.
        and exp_stale.task_manager.run.glb_texture_params()
        == ptk.MeshConvert.web_delivery_texture_params(max_size=0),
        f"first={first}, second={exp_stale.task_manager.run.optimize_textures}",
    )

    fail_dir = os.path.join(tmp, "glb_delivery_fail")
    os.makedirs(fail_dir, exist_ok=True)
    exp_fail = SceneExporter(log_level="DEBUG")
    with (
        mock.patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=_fake_convert),
        mock.patch.object(
            ptk.MeshConvert,
            "optimize_glb_textures",
            side_effect=RuntimeError("encode failed (test)"),
        ),
    ):
        fail_result = exp_fail.perform_export(
            objects=[bpy.context.object],
            export_dir=fail_dir,
            tasks={"output_format": "glb", "texture_file_type": "webp"},
        )
    check(
        "a failed texture delivery fails the deliverable (no silent fallback)",
        fail_result is False,
        f"result={fail_result}",
    )

    # ---- Override Checks: offered at the failure point, never carried over --------------
    # A failed check used to abort outright, leaving "arm Override Checks and export
    # again" as the only way through -- a second full pipeline (re-bake, re-optimize
    # textures, re-rewrite paths) over a scene the first run had already mutated. The
    # override is now offered where the check fails, so accepting it continues the SAME
    # run. Mirror of mayatk's test_failed_checks_offer_the_override_in_the_same_run.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    _ovr_dir = os.path.join(tmp, "check_override")
    os.makedirs(_ovr_dir, exist_ok=True)

    _runs = []
    _asked = []

    def _make_failing_exporter(answer):
        exp = SceneExporter(log_level="DEBUG")

        def _fail(tasks):
            _runs.append(dict(tasks))
            exp.task_manager._last_failed_checks = ["check_path_length"]
            return False

        exp.task_manager.run_tasks = _fail
        exp.confirm = lambda question: (_asked.append(question), answer)[1]
        return exp

    _exp_ovr = _make_failing_exporter(True)
    _ovr_result = _exp_ovr.perform_export(
        objects=[bpy.context.object],
        export_dir=_ovr_dir,
        output_name="OverrideAccepted",
        tasks={"check_path_length": 60},
    )
    check(
        "an accepted override writes the file without re-running the task pipeline",
        _ovr_result is True
        and len(_runs) == 1
        and os.path.exists(os.path.join(_ovr_dir, "OverrideAccepted.fbx")),
        f"result={_ovr_result}, task runs={len(_runs)}",
    )
    check(
        "the prompt names the failed check, and the run records what it shipped past",
        len(_asked) == 1
        and "check_path_length" in _asked[0]
        and _exp_ovr._overridden_checks == ["check_path_length"],
        f"asked={_asked}, overridden={_exp_ovr._overridden_checks}",
    )

    _exp_no = _make_failing_exporter(False)
    _no_result = _exp_no.perform_export(
        objects=[bpy.context.object],
        export_dir=_ovr_dir,
        output_name="OverrideDeclined",
        tasks={"check_path_length": 60},
    )
    check(
        "declining the override keeps the abort -- consent, never an automatic pass",
        _no_result is False
        and _exp_no._overridden_checks == []
        and not os.path.exists(os.path.join(_ovr_dir, "OverrideDeclined.fbx")),
        f"result={_no_result}",
    )

    # The runner stops dispatching tasks at the first failed check -- everything below it
    # is work an aborted write would throw away. An override turns that write back on, so
    # those tasks must run before it, or the file ships missing (say) the texture
    # conversion the user asked for. Only the SKIPPED names re-dispatch; re-running the
    # ones above would repeat their mutation. Mirror of mayatk's
    # test_an_override_runs_the_tasks_the_failed_check_had_stopped.
    _exp_res = SceneExporter(log_level="DEBUG")
    _tm_res = _exp_res.task_manager
    _dispatched = []

    # The resume goes to the dispatcher directly, never through run_tasks:
    # run_tasks re-derives the run's task-driven modes from what it is handed,
    # and a subset would zero the Optimize Keys level mid-run (mirror of mayatk).
    def _record(tasks_only, checks_only):
        _dispatched.append(dict(tasks_only))
        return True

    _tm_res._last_skipped_tasks = ["convert_to_relative_paths"]
    _tm_res._last_task_count, _tm_res._last_check_count = 7, 4
    _tm_res._execute_tasks_and_checks = _record
    _exp_res._resume_skipped_tasks(
        {"convert_to_relative_paths": True, "set_linear_unit": "cm"}
    )
    check(
        "an override resumes ONLY the tasks the failed check had stopped",
        _dispatched == [{"convert_to_relative_paths": True}],
        f"dispatched={_dispatched}",
    )
    check(
        "...and the resume keeps the first pass's banner counts",
        (_tm_res._last_task_count, _tm_res._last_check_count) == (7, 4),
        f"counts={(_tm_res._last_task_count, _tm_res._last_check_count)}",
    )
    _dispatched.clear()
    _tm_res._last_skipped_tasks = []
    _exp_res._resume_skipped_tasks({"set_linear_unit": "cm"})
    check(
        "a run the gate never cut short dispatches no second pass",
        _dispatched == [],
        f"dispatched={_dispatched}",
    )

    # sb.message_box hands its string to Qt's rich-text engine, which collapses a newline
    # to a space -- so the seam's plain text (documented as "newlines allowed") arrived as
    # one run-on paragraph, the KTX2 install prompt included. The panel's confirm
    # translates instead of making every caller author HTML.
    _seen = {}

    class _MsgSB:
        def message_box(self, string, *buttons):
            _seen["string"] = string
            _seen["buttons"] = buttons
            return "Yes"

    _msg_slots = _Slots.__new__(_Slots)
    _msg_slots.sb = _MsgSB()
    _confirmed = _msg_slots.confirm("line one\n\nline two & <three>")
    check(
        "the panel's confirm survives the rich-text engine (newlines kept, HTML escaped)",
        _confirmed is True
        and "<br><br>" in _seen["string"]
        and "\n" not in _seen["string"]
        and "&amp;" in _seen["string"]
        and "<three>" not in _seen["string"]
        and _seen["buttons"] == ("Yes", "No"),
        f"string={_seen.get('string')!r}",
    )

    # Override Checks must not ride QSettings into the next session: a registered widget
    # persists by default and its restore runs AFTER the slots __init__, which used to
    # re-arm the toggle right over its setChecked(False) -- silently disabling every
    # validation check on the next launch.
    class _OverrideButton:
        def __init__(self):
            self.restore_state = True
            self.checked = True
            self.enabled = False
            self.style = ""

        def setEnabled(self, value):
            self.enabled = value

        def setChecked(self, value):
            self.checked = value

        def setStyleSheet(self, value):
            self.style = value

    _btn = _OverrideButton()
    _Slots._init_override_button(_btn)
    check(
        "the Override Checks toggle never restores its armed state across sessions",
        _btn.restore_state is False and _btn.checked is False and _btn.enabled is True,
        f"restore_state={_btn.restore_state}, checked={_btn.checked}",
    )

    # ---- convert_to_relative_paths: scoped to the project; externals untouched ----------
    # An external reference is usually deliberate (a shared library, another project's
    # published maps), so the task must not relocate it. The copy pass that used to
    # consolidate externals into the project textures folder was dropped 2026-08-20;
    # MatUtils.to_project_relative already returns an out-of-project path unchanged.
    reset_scene()
    _rp_dir = os.path.join(tmp, "relpath_external")
    os.makedirs(_rp_dir, exist_ok=True)
    _ext_tex = os.path.join(_rp_dir, "wood_ext.png")
    with open(_ext_tex, "wb") as fh:
        fh.write(b"PNGDATA")

    bpy.ops.mesh.primitive_cube_add()
    _rp_obj = bpy.context.object
    _rp_mat = bpy.data.materials.new("RelPathMat")
    _rp_mat.use_nodes = True
    _rp_img = bpy.data.images.new("wood_ext", 4, 4)
    _rp_img.filepath = _ext_tex
    _rp_node = _rp_mat.node_tree.nodes.new("ShaderNodeTexImage")
    _rp_node.image = _rp_img
    _rp_obj.data.materials.append(_rp_mat)

    _rp_exp = SceneExporter(log_level="DEBUG")
    _rp_exp.task_manager.objects = [_rp_obj]
    _rp_exp.task_manager.convert_to_relative_paths()

    check(
        "an external texture keeps its absolute path (never relocated)",
        _rp_img.filepath == _ext_tex and os.path.isfile(_ext_tex),
        f"filepath={_rp_img.filepath!r}",
    )
    check(
        "and nothing was copied into the project textures folder",
        not os.path.isfile(
            os.path.join(
                os.path.dirname(bpy.data.filepath or tmp), "textures", "wood_ext.png"
            )
        ),
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
    DataNodes.write(ptk.Scope.DELIVERABLE, "hidden_coll_probe", json.dumps({"v": 42}))
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
        tb_bsdf = next(n for n in tb_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        tb_mat.node_tree.links.new(
            tb_tex_node.outputs["Color"], tb_bsdf.inputs["Base Color"]
        )
        tb_cube.data.materials.append(tb_mat)

        tb_tm = SceneExporter().task_manager
        tb_tm.objects = [tb_cube]
        tb_tm.run = tb_tm.run.replace(
            output_format="glb"
        )  # temp staging; the GLB embeds its own copies
        tb_tm.run = tb_tm.run.replace(texture_write_back=False)
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
            tb_tex_node.image.filepath == tb_orig_fp and not os.path.exists(tb_staged),
            f"filepath={tb_tex_node.image.filepath}",
        )

        # Optimize Textures combined combo (mirror of mayatk, 2026-08-20):
        # the pass switch and its ceiling are ONE ComboBox — OFF=0 first
        # (falsy), plain True second, pixel ceilings, the template-budget
        # sentinel LAST; the old texture_max_size row is gone (b000 decomposes
        # the choice back into the optimize_textures + texture_max_size
        # inputs). A fresh objectName (texture_optimize) so an old preset's
        # bool trips the uncovered-keys warning instead of silently dropping
        # its ceiling. Added: 2026-08-17 (as Max Texture Size); merged.
        tb_defs = tb_tm.task_definitions
        tb_sizes = list(tb_defs["optimize_textures"]["add"].values())
        check(
            "optimize_textures: ONE ComboBox (texture_optimize) — OFF=0 "
            "first, True second, template sentinel last; texture_max_size "
            "row gone",
            tb_defs["optimize_textures"]["widget_type"] == "ComboBox"
            and tb_defs["optimize_textures"]["object_name"] == "texture_optimize"
            and "texture_max_size" not in tb_defs
            and tb_sizes[0] == 0
            and tb_sizes[1] is True
            and tb_sizes[-1] == tb_tm.TEXTURE_MAX_SIZE_TEMPLATE
            and tb_sizes[2:-1] == [512, 1024, 2048, 4096, 8192]
            and "texture_max_size" not in tb_tm.TASK_ORDER,
            f"sizes={tb_sizes}",
        )
        tb_tm.run = tb_tm.run.replace(texture_max_size=tb_tm.TEXTURE_MAX_SIZE_TEMPLATE)
        check(
            "_texture_size_clamp: sentinel enforces the template budget "
            "(no POT), no-op without a template; ceiling = max_size; OFF = {}",
            tb_tm._texture_size_clamp("glTF 2.0")
            == {"enforce_budget": True, "force_pot": False}
            and tb_tm._texture_size_clamp(None) == {}
            and (
                setattr(tb_tm, "run", tb_tm.run.replace(texture_max_size="1024"))
                or tb_tm._texture_size_clamp(None) == {"max_size": 1024}
            )
            and (
                setattr(tb_tm, "run", tb_tm.run.replace(texture_max_size="OFF"))
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
        tb_tm.run = tb_tm.run.replace(texture_max_size=128)
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
        tb_tm.run = tb_tm.run.replace(texture_max_size=None)
        check(
            "max size: restore repoints the image",
            tb_tex_node.image.filepath == tb_big_fp,
            f"filepath={tb_tex_node.image.filepath}",
        )

        # An inefficiently encoded PNG with nothing to change is re-encoded when
        # the deliverable carries the scene's maps, and the copy ships only when
        # it saves RECOMPRESS_MIN_SAVING; GLB-only skips it (the GLB pass
        # re-encodes every map). Mirrors mayatk, 2026-09-13.
        tb_bloated = os.path.join(tmp, "bloated_src.png")
        _PILImage.new("RGB", (256, 256), (128, 128, 128)).save(
            tb_bloated, compress_level=0
        )
        tb_tex_node.image = bpy.data.images.load(tb_bloated)
        tb_bloated_fp = tb_tex_node.image.filepath
        tb_tm.run = tb_tm.run.replace(output_format="glb")
        tb_tm.optimize_textures(True)
        check(
            "recompress: GLB-only leaves a bloated PNG to the GLB pass",
            tb_tex_node.image.filepath == tb_bloated_fp,
            f"filepath={tb_tex_node.image.filepath}",
        )
        tb_tm.run = tb_tm.run.replace(output_format="fbx")
        tb_tm.optimize_textures(True)
        tb_recompressed = tb_tex_node.image.filepath
        check(
            "recompress: an FBX deliverable ships a smaller re-encode of the same pixels",
            os.path.normcase(tb_recompressed) != os.path.normcase(tb_bloated_fp)
            and os.path.isfile(tb_recompressed)
            and os.path.getsize(tb_recompressed) < os.path.getsize(tb_bloated) / 2,
            f"staged={tb_recompressed}",
        )
        tb_tm.run_deferred_restores()
        tb_tm.run = tb_tm.run.replace(output_format="glb")

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

    # The default is part of the parity: off, a scene with shots exports
    # shot_metadata naming clips the file does not contain (mayatk pins the
    # same, test_scene_exporter.test_takes_are_default_on_beside_the_carrier).
    _takes_row = SceneExporter().task_manager.task_definitions["apply_declared_takes"]
    check(
        "apply_declared_takes defaults to Shots + Full Sequence, like the carrier it describes",
        list(_takes_row["add"].values())[_takes_row["setCurrentIndex"]] == "both",
        "off, the deliverable describes shots it cannot play",
    )

    # ---- apply_declared_takes: shots -> named engine clips, end to end -------
    # The Maya-parity pipeline (shot_export_unity.md): the Shots store publishes
    # shot_metadata (each clip carrying its range) onto the carrier; the takes
    # task refreshes, folds the carrier in, arms FbxUtils, and the write ships
    # one AnimStack per shot with the metadata as user properties — with every staged
    # mutation (armed takes, widened frame range) undone after the write.
    from blendertk.anim_utils.shots._shots import BlenderShotStore

    reset_scene()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_takes_prefs_")
    BlenderShotStore.clear_active()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 25  # takes will widen to 30

    bpy.ops.mesh.primitive_cube_add()
    shot_cube = bpy.context.active_object
    shot_cube.name = "ShotCube"
    for frame, x in ((1, 0.0), (10, 4.0), (20, 4.0), (30, 0.0)):
        shot_cube.location.x = x
        shot_cube.keyframe_insert("location", frame=frame)

    takes_store = BlenderShotStore.active()
    takes_store.define_shot("open", 1, 10, objects=["ShotCube"])
    takes_store.define_shot("close", 20, 30, objects=["ShotCube"])
    # Saving publishes at authoring time; the export refreshes again through
    # the producer registry, so a stale channel could never ship anyway.
    takes_store.publish_export_view()

    exp_takes = SceneExporter()
    result = exp_takes.perform_export(
        export_dir=out_dir,
        objects=[shot_cube],
        output_name="takes_task_test",
        export_visible=True,
        tasks={"apply_declared_takes": True},  # deliberately WITHOUT export_data_node
    )
    takes_file = os.path.join(out_dir, "takes_task_test.fbx")
    check(
        "perform_export with apply_declared_takes writes the FBX",
        result is True and os.path.isfile(takes_file),
        f"result={result}",
    )
    check(
        "the armed takes are reset after the write (deferred restore ran)",
        FbxUtils._pending_takes is None,
        f"{FbxUtils._pending_takes}",
    )
    check(
        "the widened frame range is restored after the write",
        (scene.frame_start, scene.frame_end) == (1, 25),
        f"actual=({scene.frame_start},{scene.frame_end})",
    )

    reset_scene()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    imported = FbxUtils.import_fbx(takes_file, use_custom_props=True)
    take_actions = sorted(a.name for a in bpy.data.actions)
    check(
        "the file ships one AnimStack per declared shot and ONLY those",
        take_actions == ["ShotCube|close", "ShotCube|open"],
        f"{take_actions}",
    )
    icarrier2 = next((o for o in imported if o.name.startswith(DataNodes.EXPORT)), None)
    meta_raw = icarrier2.get("shot_metadata") if icarrier2 else None
    check(
        "the takes task alone ships the carrier with the joinable shot_metadata",
        bool(meta_raw)
        and [s["clip"] for s in json.loads(meta_raw)["shots"]] == ["open", "close"],
        f"{meta_raw!r}",
    )
    check(
        "each shipped clip carries its own range, and no legacy fbx_takes ships",
        bool(meta_raw)
        and [(s["clip"], s["start"], s["end"]) for s in json.loads(meta_raw)["shots"]]
        == [("open", 1, 10), ("close", 20, 30)]
        and DataNodes.FBX_TAKES not in icarrier2.keys(),
        f"{meta_raw!r} {sorted(icarrier2.keys()) if icarrier2 else None}",
    )

    # A Full Sequence Only export DECLARES its mode on the shot_metadata envelope
    # (mirror of mayatk's): its clips still carry every shot's range, so the
    # deliverable gate reads the declared mode instead of calling the shots missing.
    # Added: 2026-09-15
    reset_scene()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    bpy.ops.mesh.primitive_cube_add()
    full_cube = bpy.context.active_object
    full_cube.name = "FullSequenceCube"
    for frame, x in ((1, 0.0), (30, 4.0)):
        full_cube.location.x = x
        full_cube.keyframe_insert("location", frame=frame)
    takes_store.publish_export_view()
    full_result = SceneExporter().perform_export(
        export_dir=out_dir,
        objects=[full_cube],
        output_name="takes_full_test",
        export_visible=True,
        tasks={"export_data_node": True, "apply_declared_takes": "full"},
    )
    full_data = SceneDataSidecar.read_data(os.path.join(out_dir, "takes_full_test.fbx"))
    full_meta = (full_data or {}).get("shot_metadata") or {}
    check(
        "a Full Sequence Only export declares its mode on the shot_metadata envelope",
        full_result is True
        and full_meta.get(ptk.MeshConvert.SHOT_CLIP_MODE_KEY) == "full",
        f"result={full_result} meta={full_meta!r}",
    )

    # An empty store publishes a CLEAR; the takes task then finds no channel
    # and the export ships a plain single-take file.
    for sid in [s.shot_id for s in list(takes_store.shots)]:
        takes_store.remove_shot(sid)
    takes_store.publish_export_view()
    reset_scene()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    bpy.ops.mesh.primitive_cube_add()
    plain_cube = bpy.context.active_object
    plain_cube.name = "PlainCube"
    for frame, x in ((1, 0.0), (10, 2.0)):
        plain_cube.location.x = x
        plain_cube.keyframe_insert("location", frame=frame)
    exp_plain = SceneExporter()
    result = exp_plain.perform_export(
        export_dir=out_dir,
        objects=[plain_cube],
        output_name="takes_none_test",
        export_visible=True,
        tasks={"apply_declared_takes": True},
    )
    check(
        "no declared takes -> the task no-ops and the export still succeeds",
        result is True
        and os.path.isfile(os.path.join(out_dir, "takes_none_test.fbx"))
        and FbxUtils._pending_takes is None,
        f"result={result}",
    )
    BlenderShotStore.clear_active()
    BlenderShotStore._prefs_dir_override = None

    # ---- ignore_groups match mode: the Ignore row's option-box "Aa" toggle -----------
    # Insensitive is the default and the behavior the task shipped with, so the
    # first check pins the contract every existing caller relies on. The dict form
    # is how the panel arms the toggle: TaskFactory unpacks a dict value into the
    # method's kwargs, so the payload key must stay ``names`` -- this parameter was
    # renamed from ``value`` to match mayatk for exactly that reason.
    reset_scene()
    _temp_root = bpy.data.objects.new("TEMP", None)
    bpy.context.collection.objects.link(_temp_root)
    bpy.ops.mesh.primitive_cube_add()
    _scratch = bpy.context.active_object
    _scratch.name = "SCRATCH"
    _scratch.parent = _temp_root
    bpy.ops.mesh.primitive_cube_add()
    _hero = bpy.context.active_object
    _hero.name = "HERO"

    _tm = SceneExporter(log_level="DEBUG").task_manager

    _tm.objects = [_scratch, _hero]
    _tm.ignore_groups("temp")
    check(
        "ignore_groups defaults to case-insensitive ('temp' drops TEMP)",
        _tm.objects == [_hero],
        f"{[o.name for o in _tm.objects]}",
    )

    _tm.objects = [_scratch, _hero]
    _tm.ignore_groups("temp", case_sensitive=True)
    check(
        "case_sensitive=True requires an exact match ('temp' keeps TEMP)",
        _tm.objects == [_scratch, _hero],
        f"{[o.name for o in _tm.objects]}",
    )
    _tm.ignore_groups("TEMP", case_sensitive=True)
    check(
        "case_sensitive=True matches on the exact name",
        _tm.objects == [_hero],
        f"{[o.name for o in _tm.objects]}",
    )

    # The exact payload b000 builds -- guards the ``names`` kwarg key.
    _tm.objects = [_scratch, _hero]
    _tm.run_tasks({"ignore_groups": {"names": "temp", "case_sensitive": True}})
    check(
        "dict payload carries the mode through TaskFactory (no match)",
        _tm.objects == [_scratch, _hero],
        f"{[o.name for o in _tm.objects]}",
    )
    _tm.run_tasks({"ignore_groups": {"names": "TEMP", "case_sensitive": True}})
    check(
        "dict payload carries the mode through TaskFactory (match)",
        _tm.objects == [_hero],
        f"{[o.name for o in _tm.objects]}",
    )

    # The branch every pre-existing caller takes. TaskFactory picks
    # method(value) vs method(**value) off the POSITIONAL parameter COUNT, so
    # adding case_sensitive is precisely what could push a plain string onto the
    # kwargs branch and raise instead of run.
    _tm.objects = [_scratch, _hero]
    _tm.run_tasks({"ignore_groups": "temp"})
    check(
        "a bare string still dispatches at the insensitive default",
        _tm.objects == [_hero],
        f"{[o.name for o in _tm.objects]}",
    )

    _spec = _tm.task_definitions["ignore_groups"]
    check(
        "the Ignore row stays a QLineEdit -- the option box hangs off it",
        _spec["widget_type"] == "QLineEdit"
        and _spec["value_method"] == "text"
        and _spec["panel"] == "settings",
        f"{ {k: _spec.get(k) for k in ('widget_type', 'value_method', 'panel')} }",
    )
    reset_scene()

    # ---- ignore_groups patterns are shell-style globs (mirror of mayatk) ------------
    # The field shipped as exact set-membership, so "temp*" matched nothing and
    # every temp_01/temp_02 group had to be listed by hand. Matching now runs
    # through ptk.filter_list, which owns the glob, the comma split and the case
    # fold for every filter field in the ecosystem.
    _roots, _geo = {}, {}
    for _n in ("temp_01", "temp_02", "hull_proxy", "HERO"):
        _root = bpy.data.objects.new(_n, None)
        bpy.context.collection.objects.link(_root)
        bpy.ops.mesh.primitive_cube_add()
        _child = bpy.context.active_object
        _child.name = f"{_n}_geo"
        _child.parent = _root
        _roots[_n], _geo[_n] = _root, _child
    _all = [_geo[n] for n in ("temp_01", "temp_02", "hull_proxy", "HERO")]

    _tm = SceneExporter(log_level="DEBUG").task_manager

    _no_temps = [_geo["hull_proxy"], _geo["HERO"]]
    _no_proxy = [_geo["temp_01"], _geo["temp_02"], _geo["HERO"]]
    _hero = [_geo["HERO"]]
    # (label, pattern, case_sensitive, expected survivors). The last case is the
    # footgun the early return guards: filter_list with no patterns is a no-op
    # returning the list unfiltered, which here would read as every root
    # matching and drop the whole scene out of the export.
    _cases = [
        ("a trailing star matches a name prefix", "temp*", False, _no_temps),
        ("a leading star matches a name suffix", "*_proxy", False, _no_proxy),
        ("'?' matches exactly one character", "temp_0?", False, _no_temps),
        ("wildcards compose with the comma split", "temp*, *_proxy", False, _hero),
        ("no wildcard still matches only that exact name", "temp", False, _all),
        ("case_sensitive=True applies to wildcards", "TEMP*", True, _all),
        ("the insensitive default applies to wildcards", "TEMP*", False, _no_temps),
        ("an all-separator field excludes nothing", " , ,  ", False, _all),
    ]
    for _label, _pattern, _case_sensitive, _expect in _cases:
        _tm.objects = list(_all)
        _tm.ignore_groups(_pattern, case_sensitive=_case_sensitive)
        check(
            f"ignore_groups: {_label}",
            _tm.objects == _expect,
            f"pattern={_pattern!r} -> {[o.name for o in _tm.objects]}",
        )
    reset_scene()

    # ---- check_valid_paths covers the lightmaps the bake markers name (mirror of mayatk) --
    # They have no Image datablock, so the image gate never saw them: a scene
    # migrated with all its textures passed the check and shipped its GLB unlit.
    from blendertk.light_utils.lightmap_baker.lightmap_baker import (
        LightmapBaker as _LmBaker,
    )

    bpy.ops.mesh.primitive_cube_add()
    lm_cube = bpy.context.active_object
    lm_cube.name = "LitCube"
    lm_dir = os.path.join(tmp, "lm")
    os.makedirs(lm_dir, exist_ok=True)
    lm_map = os.path.join(lm_dir, "LitCube_LightMap.exr")
    open(lm_map, "wb").close()
    _LmBaker().commit_lightmap({lm_cube.name: lm_map})
    tm_lm = SceneExporter().task_manager
    tm_lm.objects = [lm_cube]
    passed, msgs = tm_lm.check_valid_paths(True)
    check(
        "check_valid_paths passes a lightmap in its recorded folder",
        passed is True and not any("Lightmap" in m for m in msgs),
        f"msgs={msgs}",
    )
    bpy.ops.mesh.primitive_cube_add()
    other_cube = bpy.context.active_object
    other_cube.name = "NotShipping"
    _LmBaker().commit_lightmap(
        {other_cube.name: os.path.join(tmp, "gone", "NotShipping_LightMap.exr")}
    )
    passed, msgs = tm_lm.check_valid_paths(True)
    check(
        "a lightmap outside the export set is not reported",
        passed is True,
        f"msgs={msgs}",
    )
    os.remove(lm_map)
    passed, msgs = tm_lm.check_valid_paths(True)
    check(
        "check_valid_paths fails a lightmap found nowhere and names the object",
        passed is False
        and any("Missing Lightmap" in m and "LitCube" in m for m in msgs),
        f"msgs={msgs}",
    )
    _LmBaker().revert()
    reset_scene()

    # ---- Bake Range: the one dial that owns the range the export bakes over -------------
    # It used to share the range with apply_declared_takes, which widened to the shot
    # union as an undeclared side effect of SPLITTING -- so clamping an export to its
    # shots meant arming a split you might not want, and which won was decided by
    # TASK_ORDER rather than by anything visible in the panel.  Mirrors mayatk's
    # TestBakeRangeModes.
    from blendertk.anim_utils.shots._shots import BlenderShotStore

    def _br_manager():
        reset_scene()
        BlenderShotStore.clear_active()
        obj = bpy.data.objects.new("br_probe", None)
        bpy.context.collection.objects.link(obj)
        obj.location.x = 0.0
        obj.keyframe_insert(data_path="location", frame=10, index=0)
        obj.location.x = 5.0
        obj.keyframe_insert(data_path="location", frame=200, index=0)
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 48
        tm = SceneExporter().task_manager
        tm.objects = [obj]
        return tm, scene

    def _br_shots(*spans):
        store = BlenderShotStore()
        BlenderShotStore.set_active(store)
        for i, (start, end) in enumerate(spans):
            store.define_shot(f"Shot_{i}", start, end)
        return store

    _tm_br, _scene_br = _br_manager()
    _br_shots((20, 60), (80, 120))
    _tm_br.set_bake_animation_range("auto")
    check(
        "Auto clamps the export to the shot union, not the keyed extent",
        (_scene_br.frame_start, _scene_br.frame_end) == (20, 120),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _tm_br.set_bake_animation_range("auto")
    check(
        "Auto falls back to the animated extent when the scene declares no shots",
        (_scene_br.frame_start, _scene_br.frame_end) == (10, 200),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _br_shots((20, 60))
    _tm_br.set_bake_animation_range("keys")
    check(
        "Keyframe Extent ignores the shots and measures the curves",
        (_scene_br.frame_start, _scene_br.frame_end) == (10, 200),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _tm_br.set_bake_animation_range(None)
    check(
        "OFF leaves the scene range alone",
        (_scene_br.frame_start, _scene_br.frame_end) == (1, 48),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _br_shots((20, 60))
    _tm_br.set_bake_animation_range(True)
    check(
        "a headless caller's legacy True still reads as the keyframe extent",
        (_scene_br.frame_start, _scene_br.frame_end) == (10, 200),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    # A shot can outrun the last keyframe (a hold authored on the sequencer), so a raw
    # override would write a range CLIPPING a clip the same export declared -- metadata
    # describing animation the file does not contain.  Every mode widens instead.
    _tm_br, _scene_br = _br_manager()
    _tm_br._required_range_coverage = (5, 260)
    _tm_br.set_bake_animation_range("keys")
    check(
        "every mode widens to cover a realized take",
        (_scene_br.frame_start, _scene_br.frame_end) == (5, 260),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _tm_br._required_range_coverage = (50, 60)
    _tm_br.set_bake_animation_range("keys")
    check(
        "...and never narrows a source that is already wider",
        (_scene_br.frame_start, _scene_br.frame_end) == (10, 200),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    _tm_br, _scene_br = _br_manager()
    _tm_br._required_range_coverage = (5, 260)
    _tm_br.objects = list(_tm_br.objects)  # tasks assign this mid-run: no reset
    _kept_mid_run = _tm_br._required_range_coverage == (5, 260)
    _tm_br.begin_run(_tm_br.run)  # the per-run reset
    check(
        "the realized-take range is cleared per run (never leaks to the next export)",
        _kept_mid_run and _tm_br._required_range_coverage is None,
        f"kept mid-run={_kept_mid_run} after={_tm_br._required_range_coverage}",
    )

    # REGRESSION (found while reordering): export_data_node stages the emissive
    # keyed-weight curve proxies -- whose keys sit OUTSIDE the exported objects'
    # extent by construction -- and widens the scene range for them. Moving the
    # range task last made it overwrite that widen: a 300-400 proxy span was
    # clipped back to the objects' 10-200, so the weight curves would have
    # shipped flattened to their extrapolated value. _cover_frame_range now
    # CLAIMS its span through _require_range_coverage as well as widening.
    _tm_br, _scene_br = _br_manager()
    _br_proxy = bpy.data.objects.new("br_proxy", None)
    bpy.context.collection.objects.link(_br_proxy)
    _br_proxy.scale.x = 0.0
    _br_proxy.keyframe_insert(data_path="scale", frame=300, index=0)
    _br_proxy.scale.x = 1.0
    _br_proxy.keyframe_insert(data_path="scale", frame=400, index=0)
    _tm_br._cover_frame_range([_br_proxy])
    _covered = (_scene_br.frame_start, _scene_br.frame_end)
    _tm_br.set_bake_animation_range("keys")
    check(
        "a staged proxy's claimed span survives the range task running last",
        _covered == (1, 400) and _scene_br.frame_end >= 400,
        f"covered={_covered} after={(_scene_br.frame_start, _scene_br.frame_end)}",
    )

    # ...including when the scene range ALREADY covers the proxies, so
    # _cover_frame_range takes its no-widen-needed early return: the range task
    # measures the EXPORTED OBJECTS (10-200 here), a different and narrower
    # span, so the claim has to be registered before that return, not after it.
    _tm_br, _scene_br = _br_manager()
    _scene_br.frame_start, _scene_br.frame_end = 1, 500
    _br_proxy2 = bpy.data.objects.new("br_proxy2", None)
    bpy.context.collection.objects.link(_br_proxy2)
    _br_proxy2.scale.x = 0.0
    _br_proxy2.keyframe_insert(data_path="scale", frame=300, index=0)
    _br_proxy2.scale.x = 1.0
    _br_proxy2.keyframe_insert(data_path="scale", frame=400, index=0)
    _tm_br._cover_frame_range([_br_proxy2])
    _tm_br.set_bake_animation_range("keys")
    check(
        "a proxy span the scene range already covered is still claimed",
        _scene_br.frame_end >= 400,
        f"after={(_scene_br.frame_start, _scene_br.frame_end)}",
    )

    _tm_br, _scene_br = _br_manager()
    _tm_br._require_range_coverage(100, 150)
    _tm_br._require_range_coverage(40, 120)
    check(
        "coverage claims union rather than overwrite each other",
        _tm_br._required_range_coverage == (40, 150),
        f"{_tm_br._required_range_coverage}",
    )

    _tm_br, _scene_br = _br_manager()
    try:
        _tm_br.set_bake_animation_range("widest")
        _br_raised = False
    except ValueError:
        _br_raised = True
    check("an unknown bake range mode raises rather than guessing", _br_raised)

    _br_order = _tm_br.TASK_ORDER
    check(
        "the range task runs AFTER the split, so it can cover the takes it declared",
        _br_order.index("apply_declared_takes")
        < _br_order.index("set_bake_animation_range"),
    )

    _tm_br.set_bake_animation_range("keys")
    _tm_br.run_deferred_restores()
    check(
        "the original frame range is restored once the post-write cleanups run",
        (_scene_br.frame_start, _scene_br.frame_end) == (1, 48),
        f"({_scene_br.frame_start},{_scene_br.frame_end})",
    )

    # Templates persist combos by INDEX, so row 0 and the default index are contracts,
    # and the retired checkbox's objectName must not resolve to the combo (a restored
    # `true` would select index 1 -- a mode nobody chose).
    _br_defs = SceneExporter().task_manager
    _br_spec = _br_defs.task_definitions["set_bake_animation_range"]
    _br_rows = list(_br_defs._bake_range_options.items())
    check(
        "Bake Range is a combo with OFF at index 0, defaulting to Auto",
        _br_spec["widget_type"] == "ComboBox"
        and _br_spec["object_name"] == "bake_range"
        and _br_rows[0] == ("OFF", None)
        and _br_rows[_br_spec["setCurrentIndex"]][1] == "auto",
        f"{_br_spec.get('widget_type')} {_br_spec.get('object_name')} {_br_rows[:2]}",
    )
    check(
        "every Bake Range row is a mode the task accepts",
        sorted(v for v in _br_defs._bake_range_options.values() if v)
        == sorted(_br_defs.BAKE_RANGE_MODES),
    )

    # ---- Optimize Keys levels ------------------------------------------------------------
    from blendertk.anim_utils._anim_utils import AnimUtils as _BrAnimUtils

    _ok_spec = _br_defs.task_definitions["optimize_keys"]
    _ok_rows = list(_br_defs._optimize_keys_options.items())
    check(
        "Optimize Keys is a combo defaulting to the old checkbox's level",
        _ok_spec["widget_type"] == "ComboBox"
        and _ok_spec["object_name"] == "optimize_level"
        and _ok_rows[0] == ("OFF", None)
        and _ok_rows[_ok_spec["setCurrentIndex"]][1] == "flat",
        f"{_ok_spec.get('widget_type')} {_ok_spec.get('object_name')} {_ok_rows[:2]}",
    )
    check(
        "every Optimize Keys row is a level AnimUtils knows",
        sorted(v for v in _br_defs._optimize_keys_options.values() if v)
        == sorted(_BrAnimUtils.OPTIMIZE_LEVELS),
    )
    check(
        "the level tokens mirror mayatk's exactly (one template, both DCCs)",
        sorted(_BrAnimUtils.OPTIMIZE_LEVELS)
        == ["extremes", "flat", "simplify", "static"],
    )
    check(
        "a bare True still resolves to the pre-levels behavior",
        _BrAnimUtils.resolve_optimize_level(True)
        == dict(_BrAnimUtils.OPTIMIZE_LEVELS[_BrAnimUtils.DEFAULT_OPTIMIZE_LEVEL]),
    )
    check(
        "every falsy value is OFF (None, so a caller SKIPS rather than runs a no-op pass)",
        all(
            _BrAnimUtils.resolve_optimize_level(v) is None for v in (None, False, "", 0)
        ),
    )
    try:
        _BrAnimUtils.resolve_optimize_level("aggressive")
        _ok_raised = False
    except ValueError:
        _ok_raised = True
    check(
        "an unknown optimize level raises rather than silently defaulting", _ok_raised
    )

    # Static Curves Only must keep every flat key of a curve that carries motion.
    reset_scene()
    BlenderShotStore.clear_active()
    _ok_obj = bpy.data.objects.new("ok_probe", None)
    bpy.context.collection.objects.link(_ok_obj)
    for _f, _v in ((1, 0.0), (10, 5.0), (20, 5.0), (30, 5.0), (40, 9.0)):
        _ok_obj.location.x = _v
        _ok_obj.keyframe_insert(data_path="location", frame=_f, index=0)
    for _f in (1, 20, 40):
        _ok_obj.location.y = 0.0
        _ok_obj.keyframe_insert(data_path="location", frame=_f, index=1)

    def _ok_counts():
        counts = {}
        for fc in _BrAnimUtils.get_fcurves([_ok_obj]):
            counts[fc.array_index] = len(fc.keyframe_points)
        return counts

    _before = _ok_counts()
    _tm_ok = SceneExporter().task_manager
    _tm_ok.objects = [_ok_obj]
    _tm_ok.optimize_keys(None)
    check(
        "OFF touches nothing",
        _ok_counts() == _before,
        f"{_ok_counts()} != {_before}",
    )
    _tm_ok.optimize_keys("static")
    _after_static = _ok_counts()
    check(
        "Static Curves Only drops the static curve and keeps every flat key",
        1 not in _after_static and _after_static.get(0) == 5,
        f"{_after_static}",
    )
    _tm_ok.optimize_keys("flat")
    check(
        "...and Static + Flat Keys then drops the redundant interior key of the hold",
        _ok_counts().get(0) == 4,
        f"{_ok_counts()}",
    )
    reset_scene()
    BlenderShotStore.clear_active()

    # ---- progress stream (mirror of mayatk, 2026-09-04): ONE (current, total, message)
    # feed drives the panel footer's bar; a False from it cancels before the write and is
    # only reported after it. -----------------------------------------------------------
    bpy.ops.mesh.primitive_cube_add()
    _pcube = bpy.context.active_object
    _pcube.name = "ProgressCube"
    _pdir = os.path.join(tmp, "progress")
    os.makedirs(_pdir, exist_ok=True)
    _events = []
    _presult = SceneExporter().perform_export(
        export_dir=_pdir,
        objects=[_pcube],
        output_name="progress_test",
        progress_callback=lambda c, t, m: _events.append((c, t, m)),
    )
    _currents = [c for c, _, _ in _events]
    _pmsgs = [m for _, _, m in _events if m]
    check(
        "perform_export reports one monotonic (current, total, message) stream ending at total/total",
        _presult is True
        and bool(_events)
        and _currents == sorted(_currents)
        and {t for _, t, _ in _events} == {2}
        and _events[-1][:2] == (2, 2)
        and any(m.startswith("Writing FBX") for m in _pmsgs)
        and any(m.startswith("Writing scene sidecar") for m in _pmsgs),
        f"result={_presult} events={_events}",
    )
    _cresult = SceneExporter().perform_export(
        export_dir=_pdir,
        objects=[_pcube],
        output_name="progress_cancelled",
        progress_callback=lambda c, t, m: not (m or "").startswith("Writing"),
    )
    check(
        "a False from progress_callback before the write cancels: False, nothing written",
        _cresult is False
        and not os.path.isfile(os.path.join(_pdir, "progress_cancelled.fbx")),
        f"result={_cresult}",
    )
    _lresult = SceneExporter().perform_export(
        export_dir=_pdir,
        objects=[_pcube],
        output_name="progress_late",
        progress_callback=lambda c, t, m: (
            not (m or "").startswith("Writing scene sidecar")
        ),
    )
    check(
        "a False after the write began is reported, not honoured: the deliverable is finished",
        _lresult is True and os.path.isfile(os.path.join(_pdir, "progress_late.fbx")),
        f"result={_lresult}",
    )

    # ---- the export bracket: every stager a publish prepares is finished -----------------
    # REGRESSION (2026-09-18): the run never opened FbxUtils' bracket, so the session
    # stagers export_data_node's publish prepared (the shadow preview stands down for
    # the write) were never finished -- on a completed run or on one stopped before
    # its write. Pinned with a probe stager: the shadow preview's own pair is
    # test_shadow_preview's, and headless it cannot draw.
    _stages = []
    _bracket_file = os.path.join(_pdir, "bracket_probe.fbx")
    FbxUtils.register_export_stager(
        "probe_stager",
        prepare=lambda: _stages.append(("prepare", os.path.isfile(_bracket_file))),
        finish=lambda: _stages.append(("finish", os.path.isfile(_bracket_file))),
    )
    try:
        _bresult = SceneExporter().perform_export(
            export_dir=_pdir,
            objects=[_pcube],
            output_name="bracket_probe",
            tasks={"export_data_node": True},
        )
        check(
            "a completed run finishes its stagers AFTER the file is written",
            _bresult is True
            and ("prepare", False) in _stages
            and bool(_stages)
            and _stages[-1] == ("finish", True)
            and FbxUtils._export_depth == 0,
            f"result={_bresult} stages={_stages} depth={FbxUtils._export_depth}",
        )
        _stages.clear()
        _sresult = SceneExporter().perform_export(
            export_dir=_pdir,
            objects=[_pcube],
            output_name="bracket_stopped",
            tasks={"export_data_node": True},
            # Stop at the first tick after the publish staged: before the write.
            progress_callback=lambda c, t, m: not _stages,
        )
        check(
            "a run stopped before its write still finishes what its publish prepared",
            _sresult is False
            and bool(_stages)
            and _stages[0][0] == "prepare"
            and _stages[-1][0] == "finish"
            and FbxUtils._export_depth == 0,
            f"result={_sresult} stages={_stages} depth={FbxUtils._export_depth}",
        )
    finally:
        FbxUtils.unregister_export_stager("probe_stager")
    reset_scene()

    # ---- Output Filename: one wildcard for the default, {tokens} for the rest -------------
    # The field composes LAST — after the RegEx, which shapes the default name the
    # wildcard stands for. Mirrors mayatk's test_scene_exporter.py.
    _ndir = os.path.join(tmp, "names")
    os.makedirs(_ndir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(_ndir, "test_scene.blend"))

    def _stem_for(output_name, name_regex=None, timestamp=False):
        _e = SceneExporter()
        _e.export_dir = _ndir
        _e.output_name = output_name
        _e.name_regex = name_regex
        _e.timestamp = timestamp
        return os.path.splitext(os.path.basename(_e.generate_export_path()))[0]

    for _pattern, _expected in (
        (None, "test_scene"),
        ("", "test_scene"),
        ("*", "test_scene"),
        ("*_export", "test_scene_export"),
        ("WIP_*", "WIP_test_scene"),
        ("WIP_*_export", "WIP_test_scene_export"),
        ("asset", "asset"),  # no wildcard — a literal name
    ):
        _got = _stem_for(_pattern)
        check(
            f"output name {_pattern!r} resolves to {_expected!r}",
            _got == _expected,
            f"got={_got!r}",
        )

    _got = _stem_for("{folder}_{scene}")
    check(
        "{tokens} fill from the .blend",
        _got == f"{os.path.basename(_ndir)}_test_scene",
        f"got={_got!r}",
    )
    _got = _stem_for("{nope}_x")
    check(
        "an unsupported token is left in the name as typed",
        _got == "{nope}_x",
        f"got={_got!r}",
    )
    _got = _stem_for("WIP_*", name_regex="test_->prod_")
    check(
        "the wildcard resolves AFTER the regex, which shapes the default name",
        _got == "WIP_prod_scene",
        f"got={_got!r}",
    )
    _got = _stem_for("test_asset", name_regex="test_->prod_")
    check(
        "a literal name is the user's explicit choice — the regex leaves it be",
        _got == "test_asset",
        f"got={_got!r}",
    )
    _got = _stem_for("{scene}_my text", name_regex="test_->prod_")
    check(
        "the regex shapes {scene} too — every token spelling the file name carries it",
        _got == "prod_scene_my text",
        f"got={_got!r}",
    )
    _got = _stem_for("*_v{n:03d}")
    check(
        "a {n} counter versions the export", _got == "test_scene_v001", f"got={_got!r}"
    )
    open(os.path.join(_ndir, "test_scene_v003.fbx"), "w").close()
    _got = _stem_for("*_v{n:03d}")
    check(
        "the counter takes the next free version",
        _got == "test_scene_v004",
        f"got={_got!r}",
    )
    open(os.path.join(_ndir, "test_scene_v006.glb"), "w").close()
    _e = SceneExporter()
    _glb = _e.resolve_export_path("*_v{n:03d}", _ndir, output_format="glb")
    _pair = _e.resolve_export_path("*_v{n:03d}", _ndir, output_format="fbx_glb")
    check(
        "a GLB-only export counts .glb siblings; FBX + GLB versions as one pair",
        [os.path.basename(p) for p in _glb["paths"]] == ["test_scene_v007.glb"]
        and [os.path.basename(p) for p in _pair["paths"]]
        == ["test_scene_v007.fbx", "test_scene_v007.glb"],
        f"glb={_glb['paths']} pair={_pair['paths']}",
    )
    _e = SceneExporter()
    _e.export_dir, _e.output_name = _ndir, "WIP_*"
    _legacy = os.path.basename(
        _e.generate_export_path(version_format="{stem}_v{n:03d}")
    )
    check(
        "the retired Version pattern still resolves for one release",
        _legacy == "WIP_test_scene_v001.fbx",
        f"got={_legacy!r}",
    )
    import logging as _layout_logging

    from blendertk.env_utils.scene_exporter.scene_exporter_slots import (
        SceneExporterSlots as _LayoutSlots,
    )
    from blendertk.env_utils.scene_exporter.task_manager import (
        TaskManager as _LayoutTasks,
    )

    check(
        "the Version row and the Timestamp checkbox are folded into the filename",
        "version"
        not in _LayoutTasks(_layout_logging.getLogger("layout")).task_definitions
        and "version"
        not in [n for _, names in _LayoutSlots._SETTINGS_LAYOUT for n in names]
        and "n" in SceneExporter.NAME_TOKENS,
    )
    for _stale in ("test_scene_v003.fbx", "test_scene_v006.glb"):
        os.remove(os.path.join(_ndir, _stale))
    _got = _stem_for("*_a?b")
    check(
        "a character illegal in a filename cannot reach the write",
        _got == "test_scene_ab",
        f"got={_got!r}",
    )
    # The field's hover resolves through the same call as the write, so the two
    # cannot drift — and it resolves QUIETLY, since it renders the diagnostics
    # itself and a hover must not file a warning per mouse-over.
    import logging as _logging
    from types import SimpleNamespace as _NS

    from uitk.widgets.mixins.tooltip_mixin import TooltipFormat as _Tip
    from blendertk.env_utils.scene_exporter.scene_exporter_slots import (
        SceneExporterSlots as _Slots,
    )

    _slots = _Slots.__new__(_Slots)
    _slots.sb = _NS(tooltip=_Tip)
    _slots.ui = _NS(
        txt000=_NS(text=lambda: _ndir),
        txt001=_NS(text=lambda: "WIP_*_{nope}"),
        cmb004=_NS(currentData=lambda: "fbx"),
    )

    class _Counter(_logging.Handler):
        count = 0

        def emit(self, record):
            _Counter.count += 1

    _handler = _Counter(level=_logging.WARNING)
    _slots.logger.addHandler(_handler)
    _html = _slots.output_name_preview()
    _slots.logger.removeHandler(_handler)

    check(
        "hovering the field logs nothing — the tooltip shows the diagnostics itself",
        _Counter.count == 0,
        f"records={_Counter.count}",
    )
    check(
        "the tooltip teaches every token, with the wildcard first",
        ">*</td>" in _html and all("{" + t + "}" in _html for t in _slots.NAME_TOKENS),
    )
    check(
        "an unsupported token is flagged, not silently kept",
        "unknown" in _html,
    )
    check(
        "the tooltip previews the path the export would write",
        os.path.join(_ndir, "WIP_test_scene_{nope}.fbx") in _html,
        _html[-200:],
    )

    # --- Export Scene Data Node's viewer action (mirror of mayatk's) ---------------
    import json as _json

    from blendertk.node_utils.data_nodes import DataNodes as _DN

    _actions = []
    _row = _NS(
        is_initialized=False,
        option_box=_NS(add_action=lambda **kw: _actions.append(kw)),
    )
    _slots.export_data_node_init(_row)
    _row.is_initialized = True
    _slots.export_data_node_init(_row)
    check(
        "export_data_node row gets exactly one viewer action",
        len(_actions) == 1 and _actions[0]["callback"] == _slots._show_data_node,
        str(_actions),
    )
    _shown = []
    _slots.sb = _NS(data_view_dialog=lambda data, **kw: _shown.append((data, kw)))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    reset_scene()
    _slots._show_data_node()
    check(
        "an absent carrier hands the viewer nothing and creates nothing",
        _shown[-1][0] == {} and bpy.data.objects.get(_DN.EXPORT) is None,
        str(_shown[-1]),
    )
    _DN.write(ptk.Scope.DELIVERABLE, "probe_channel", _json.dumps({"a": [1, 2]}))
    _DN.write(ptk.Scope.PRIVATE, "private_probe", "internal-only")
    _slots._show_data_node()
    _data, _kw = _shown[-1]
    check(
        "the shared viewer gets data_export's channels, decoded, without data_internal's",
        _data == {_DN.EXPORT: {"probe_channel": {"a": [1, 2]}}}
        and _kw["save_path"].endswith("_data_export.json"),
        str(_shown[-1])[:300],
    )

    bpy.ops.wm.read_factory_settings(use_empty=True)
    reset_scene()

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(_PRESETS_ROOT, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
