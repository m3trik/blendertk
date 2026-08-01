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
    # Bugs: a PACKED image with a stale disk path failed check_valid_paths (and its absolute
    # path form failed check_absolute_paths) even though the FBX embeds it from memory; a
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
    passed, msgs = tm_pu.check_absolute_paths(True)
    check(
        "check_absolute_paths skips a PACKED image (ships embedded)",
        passed is True,
        f"msgs={msgs}",
    )

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
    exp8._create_glb = lambda fbx_path=None, announce=True: None
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

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(_PRESETS_ROOT, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
