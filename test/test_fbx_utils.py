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

import pythontk as ptk  # noqa: E402 -- after the sibling paths above

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


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
    check(
        "FbxUtils.export writes the file",
        written == out and os.path.isfile(out) and os.path.getsize(out) > 0,
    )
    # No path: a fresh ptk.TempArtifacts folder per write, never a fixed
    # <system temp>/<stem>_bridge.fbx two writes (or two files) share.
    import pythontk as _ptk

    first = FbxUtils.export(objects=[cube])
    second = FbxUtils.export(objects=[cube])
    _root = os.path.normcase(os.path.realpath(_ptk.TempArtifacts.root()))
    check(
        "a default FBX export is a fresh temp artifact named for the file",
        all(
            os.path.normcase(os.path.realpath(p)).startswith(_root)
            and os.path.basename(p) == "untitled_bridge.fbx"
            and os.path.isfile(p)
            for p in (first, second)
        )
        and os.path.dirname(first) != os.path.dirname(second),
        f"{first} / {second}",
    )

    reset()
    check("scene cleared before import", len(bpy.data.objects) == 0)
    created = FbxUtils.import_fbx(out)
    check(
        "import_fbx returns the created objects",
        len(created) >= 1,
        f"{[o.name for o in created]}",
    )
    check(
        "import_fbx actually adds a mesh to the scene",
        any(o.type == "MESH" for o in bpy.data.objects),
    )

    # ---- .fbx auto-append + parent-dir creation -----------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    nested = os.path.join(tmp, "sub", "dir", "noext")  # no extension, missing dirs
    w2 = FbxUtils.export(filepath=nested, objects=[bpy.context.active_object])
    check(
        "export appends .fbx and creates parent dirs",
        w2 == nested + ".fbx" and os.path.isfile(nested + ".fbx"),
    )

    # ---- selection_only=False exports the whole scene -----------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    bpy.ops.object.select_all(action="DESELECT")  # nothing selected
    all_out = os.path.join(tmp, "all.fbx")
    written_all = FbxUtils.export(filepath=all_out, selection_only=False)
    check(
        "export(selection_only=False) ignores selection + writes",
        written_all == all_out and os.path.isfile(all_out),
    )
    reset()
    created_all = FbxUtils.import_fbx(all_out)
    check(
        "whole-scene export round-trips both meshes",
        sum(1 for o in created_all if o.type == "MESH") == 2,
        f"{[o.name for o in created_all]}",
    )

    # ---- the default export set preserves PARENTING -------------------------
    # Regression: ``_EXPORT_DEFAULTS`` pinned ``object_types={"MESH"}``, and Blender's FBX
    # exporter drops every excluded type and RE-ROOTS its children — so every bridge
    # hand-off (Maya / Unity) arrived with the scene graph flattened to parentless meshes.
    reset()
    grp = bpy.data.objects.new("HierGrp", None)  # EMPTY == Maya group
    sub = bpy.data.objects.new("HierSub", None)
    for empty in (grp, sub):
        bpy.context.scene.collection.objects.link(empty)
    sub.parent = grp
    bpy.ops.mesh.primitive_cube_add()
    leaf = bpy.context.active_object
    leaf.name = "HierLeaf"
    leaf.parent = sub

    hier_out = os.path.join(tmp, "hierarchy.fbx")
    FbxUtils.export(filepath=hier_out, objects=[grp, sub, leaf])
    reset()
    FbxUtils.import_fbx(hier_out)
    parents = {o.name: (o.parent.name if o.parent else None) for o in bpy.data.objects}
    check(
        "default export set carries the Empties (hierarchy) through the round trip",
        parents.get("HierLeaf") == "HierSub" and parents.get("HierSub") == "HierGrp",
        f"{parents}",
    )

    # The DCC hand-off (Maya + Unity bridges) widens that set: an armature dropped from a
    # bridge FBX is silent skin loss on the far side (mirror of mayatk's FBXExportSkins).
    # Driven through the mixin's real producer, not just its option dict — the set has to
    # survive ``export_scene.fbx``'s enum validation AND carry the hierarchy.
    from blendertk.env_utils.handoff_export import BlenderExportMixin

    handoff_types = BlenderExportMixin()._fbx_options({}).get("object_types", set())
    check(
        "BlenderExportMixin hand-off options carry EMPTY + ARMATURE",
        {"EMPTY", "ARMATURE"} <= set(handoff_types),
        f"{sorted(handoff_types)}",
    )

    reset()
    rig_grp = bpy.data.objects.new("RigGrp", None)
    bpy.context.scene.collection.objects.link(rig_grp)
    bpy.ops.object.armature_add()
    arm = bpy.context.active_object
    arm.name = "RigArm"
    arm.parent = rig_grp
    bpy.ops.mesh.primitive_cube_add()
    skinned = bpy.context.active_object
    skinned.name = "RigMesh"
    skinned.parent = arm

    handoff_out = os.path.join(tmp, "handoff.fbx")
    BlenderExportMixin()._export_fbx(
        [rig_grp, arm, skinned], handoff_out, {"EMBED_TEXTURES": False}
    )
    reset()
    FbxUtils.import_fbx(handoff_out)
    kinds = {o.name: o.type for o in bpy.data.objects}
    hier = {o.name: (o.parent.name if o.parent else None) for o in bpy.data.objects}
    check(
        "hand-off export carries the armature and its hierarchy",
        "ARMATURE" in kinds.values()
        and hier.get("RigArm") == "RigGrp"
        and hier.get("RigMesh") == "RigArm",
        f"{kinds} {hier}",
    )

    # ---- the data_export carrier: opt-in, and both halves of the opt-in ------
    # A bridge whose consumer READS in-band metadata (the WebXR preview, whose GLB
    # conversion binds ``lightmaps``) has to ship the carrier Empty AND the
    # custom properties on it -- Blender's exporter drops custom props by default, so
    # half the switch would ship a named Empty carrying nothing.
    reset()
    from blendertk.node_utils.data_nodes import DataNodes
    from blendertk.env_utils.webxr_preview import WebXrPreview

    check(
        "hand-off bridges do not ship the carrier by default",
        BlenderExportMixin().include_data_export is False,
    )
    check("WebXrPreview opts in", WebXrPreview().include_data_export is True)

    # The options that make the carrier readable are forced where it is APPENDED, not
    # declared in _fbx_options -- a subclass overriding that method wholesale
    # must not be able to ship a carrier holding nothing. Driven through a
    # stubbed exporter so it pins the real call.
    _seen_opts = {}

    class _CarrierProbe(WebXrPreview):
        def _fbx_options(self, params):  # a hostile override: neither half present
            # `object_types` deliberately a BARE STRING -- the preset-sourced shape
            # that set("MESH") explodes into {'M','E','S','H'}. Pins that the carrier
            # union goes through the shared coercion.
            return dict(object_types="MESH", use_custom_props=False)

    _real_export = btk.FbxUtils.export_selection_fbx
    try:
        btk.FbxUtils.export_selection_fbx = lambda filepath=None, objects=None, **o: (
            _seen_opts.update(o) or filepath
        )
        _CarrierProbe()._export_fbx([], os.path.join(tmp, "probe.fbx"), {})
    finally:
        btk.FbxUtils.export_selection_fbx = _real_export
    check(
        "no carrier in the scene -> a hostile override is left alone",
        _seen_opts.get("use_custom_props") is False
        and _seen_opts.get("object_types") == "MESH",
        f"{_seen_opts}",
    )

    check(
        "no carrier in the scene -> nothing added, and none invented",
        WebXrPreview()._data_export_carrier() == []
        and DataNodes.get_export_node(create=False) is None,
    )

    DataNodes.write(ptk.Scope.DELIVERABLE, "lightmaps", '{"version": 1}')
    bpy.ops.mesh.primitive_cube_add()
    lit = bpy.context.active_object
    lit.name = "LitMesh"

    _seen_opts.clear()
    _real_export = btk.FbxUtils.export_selection_fbx
    try:
        btk.FbxUtils.export_selection_fbx = lambda filepath=None, objects=None, **o: (
            _seen_opts.update(o) or filepath
        )
        _CarrierProbe()._export_fbx([lit], os.path.join(tmp, "probe2.fbx"), {})
    finally:
        btk.FbxUtils.export_selection_fbx = _real_export
    check(
        "carrier shipped -> the options it needs are forced past the override",
        _seen_opts.get("use_custom_props") is True
        and _seen_opts.get("object_types") == {"MESH", "EMPTY"},
        f"{_seen_opts}",
    )

    carrier_out = os.path.join(tmp, "carrier.fbx")
    WebXrPreview()._export_fbx([lit], carrier_out, {"EMBED_TEXTURES": False})
    reset()
    FbxUtils.import_fbx(carrier_out, use_custom_props=True)
    shipped = bpy.data.objects.get(DataNodes.EXPORT)
    check(
        "the WebXR preview export round-trips the manifest on the carrier",
        shipped is not None and shipped.get("lightmaps") == '{"version": 1}',
        f"{shipped and dict(shipped.items())}",
    )

    # ---- a hand-off states what it ships (mirror of mayatk's) ----------------
    # Its context is scoped to the objects it sends, as the Scene Exporter's
    # is: a send of one of two shaded meshes states that mesh's material alone,
    # and the scene keeps its own records -- the materials record, the
    # write's alone, cleared after it. Added: 2026-10-20
    reset()
    from blendertk.mat_utils._mat_utils import MatUtils as _HoMatUtils

    def _ho_shaded(name):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        mat = bpy.data.materials.new(f"{name}_mat")
        _HoMatUtils.get_node_tree(mat, create=True)
        obj.data.materials.append(mat)
        return obj, mat

    _ho_sent, _ho_sent_mat = _ho_shaded("HoSent")
    _ho_kept, _ho_kept_mat = _ho_shaded("HoKept")
    _ho_stated = {}

    def _ho_capture(filepath=None, objects=None, **_opts):
        _ho_stated["record"] = ptk.SceneRecords.MATERIALS.load(DataNodes)
        return filepath

    _real_export = btk.FbxUtils.export_selection_fbx
    try:
        btk.FbxUtils.export_selection_fbx = _ho_capture
        WebXrPreview()._export_fbx([_ho_sent], os.path.join(tmp, "subset.fbx"), {})
    finally:
        btk.FbxUtils.export_selection_fbx = _real_export
    _ho_names = {
        m.get("name")
        for m in (_ho_stated.get("record") or {}).get("materials", [])
    }
    check(
        "a hand-off of a subset states only that subset's materials",
        _ho_sent_mat.name in _ho_names and _ho_kept_mat.name not in _ho_names,
        f"{sorted(_ho_names)}",
    )
    check(
        "...and the scene keeps its own records: the write's materials cleared",
        ptk.SceneRecords.MATERIALS.load(DataNodes) is None,
    )

    # ---- the start camera: the page's views start through it ----------------
    # The page opens its views through the scene's `user_pos` camera and stands a
    # headset under it -- so the preview lets cameras through its FBX (the hand-off
    # default keeps them out), and a push joins the start camera to whatever its
    # scope chose. Mirror of mayatk's check.
    reset()
    preview = WebXrPreview()
    types = preview._fbx_options({})["object_types"]
    check(
        "the preview's FBX lets cameras through, keeping the hand-off's types",
        types >= {"MESH", "EMPTY", "ARMATURE", "OTHER", "CAMERA"},
        f"{types}",
    )
    check("no start camera -> none found", preview._start_node("user_pos") is None)
    bpy.ops.mesh.primitive_cube_add()
    start_mesh = bpy.context.active_object
    start_mesh.name = "StartMesh"
    start = bpy.data.objects.new("user_pos", bpy.data.cameras.new("user_pos"))
    check(
        "an object no scene links is not the start",
        preview._start_node("user_pos") is None,
    )
    bpy.context.scene.collection.objects.link(start)
    check("the scene's user_pos is the start", preview._start_node("user_pos") == start)
    _sent = []
    preview.send = lambda objects, **kwargs: _sent.append(list(objects))
    preview.push(objects=[start_mesh])
    preview.push(objects=[])
    check(
        "a push ships the start camera it was not given; an empty one stays empty",
        _sent == [[start_mesh, start], []],
        f"{_sent}",
    )
    start_out = os.path.join(tmp, "start.fbx")
    WebXrPreview()._export_fbx(
        [start_mesh, start], start_out, {"EMBED_TEXTURES": False}
    )
    reset()
    FbxUtils.import_fbx(start_out)
    arrived = bpy.data.objects.get("user_pos")
    check(
        "the start camera survives the preview's FBX",
        arrived is not None and arrived.type == "CAMERA",
        f"{arrived and arrived.type}",
    )

    # The materials strip copies what it clears, and only that: a camera has no
    # slots, and its copy would ship as `user_pos.001` -- found by nothing.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    strip_mesh = bpy.context.active_object
    strip_mesh.name = "StripMesh"
    strip_cam = bpy.data.objects.new("user_pos", bpy.data.cameras.new("user_pos"))
    bpy.context.scene.collection.objects.link(strip_cam)
    strip_out = os.path.join(tmp, "strip.fbx")
    WebXrPreview()._export_fbx(
        [strip_mesh, strip_cam],
        strip_out,
        {"EMBED_TEXTURES": False, "INCLUDE_MATERIALS": False},
    )
    reset()
    FbxUtils.import_fbx(strip_out)
    names = sorted(o.name for o in bpy.data.objects)
    check(
        "a materials strip ships the start camera as itself, under its own name",
        "user_pos" in names and bpy.data.objects["user_pos"].type == "CAMERA",
        f"{names}",
    )

    # ---- declared takes reach ANY animated hand-off, not just the exporter ----
    # Mirror of mayatk's check of the same contract. The take split lived only in
    # the Scene Exporter's task, so a preview push of a shot-carrying scene came
    # back with one whole-timeline take -- two writers of the same deliverable
    # disagreeing about whether shots survive. Armed takes are sticky, so the
    # reset matters as much as the arm: left armed they would split the next
    # export nobody asked to split. The take is declared the way a scene
    # declares it: a shot in the store, which the send's own refresh
    # publishes (the shots record is DERIVED: the store is its authority).
    # A live object to send: every reset above removed ``lit``, and a send now
    # reads what it ships (its records are scoped to it).
    bpy.ops.mesh.primitive_cube_add()
    takes_mesh = bpy.context.active_object
    takes_mesh.name = "TakesMesh"
    from blendertk.anim_utils.shots._shots import BlenderShotStore as _TakesStore

    _TakesStore.clear_active()
    _takes_store = _TakesStore.active()
    _takes_store.shots = []
    _takes_store.define_shot("Shot_1", 1, 10, objects=[takes_mesh.name])
    for include_animation, expect_armed in ((True, 1), (False, 0)):
        _armed = {}
        _real_export = btk.FbxUtils.export_selection_fbx
        try:
            btk.FbxUtils.export_selection_fbx = (
                lambda filepath=None, objects=None, **o: (
                    _armed.update(during=btk.FbxUtils._pending_takes) or filepath
                )
            )
            WebXrPreview()._export_fbx(
                [takes_mesh],
                os.path.join(tmp, "takes.fbx"),
                {"INCLUDE_ANIMATION": include_animation},
            )
        finally:
            btk.FbxUtils.export_selection_fbx = _real_export
        during = _armed.get("during") or []
        check(
            f"INCLUDE_ANIMATION={include_animation} -> "
            f"{expect_armed} take(s) armed during the write",
            len(during) == expect_armed,
            f"{during}",
        )
        check(
            f"INCLUDE_ANIMATION={include_animation} -> takes cleared afterwards",
            not btk.FbxUtils._pending_takes,
            f"{btk.FbxUtils._pending_takes}",
        )
    ptk.SceneRecords.SHOTS.clear(DataNodes)
    ptk.SceneRecords.SHOT_STORE.clear(DataNodes)
    _TakesStore.clear_active()

    # ---- the producer table: every row a declared record, every row importable ----
    _specs = ptk.SceneRecords.check_producers(FbxUtils.PRODUCERS)
    check(
        "every PRODUCERS row is a declared deliverable record and imports",
        len(_specs) == len(FbxUtils.PRODUCERS)
        and set(FbxUtils.producers()) == set(FbxUtils.PRODUCERS),
        str(sorted(s.key for s in FbxUtils.PRODUCERS)),
    )
    # A misspelt row warns (mirror of mayatk's ``_resolve_row``): producers()
    # swallowed it at debug level and stagers() resolved it to None, so a typo
    # in a row dropped that record from every export, or left a stager
    # staged, with no word. A None column is declared: quiet. Added: 2026-10-24
    import logging as _rr_logging
    from unittest import mock as _rr_mock

    class _RowLog(_rr_logging.Handler):
        def __init__(self):
            super().__init__(_rr_logging.WARNING)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    _rr_log = _rr_logging.getLogger("blendertk.env_utils.fbx_utils")
    _rr_handler = _RowLog()
    _rr_log.addHandler(_rr_handler)
    try:
        _row = FbxUtils.PRODUCERS[ptk.SceneRecords.MATERIALS]
        with _rr_mock.patch.dict(
            FbxUtils.PRODUCERS,
            {ptk.SceneRecords.MATERIALS: (_row[0], _row[1], "export_recrd")},
        ):
            _typo_producers = FbxUtils.producers([ptk.SceneRecords.MATERIALS])
        _stage_row = FbxUtils.STAGERS["render_effects"]
        with _rr_mock.patch.dict(
            FbxUtils.STAGERS,
            {
                "probe_typo": (*_stage_row[:3], "finsh_export"),
                "probe_one_way": (*_stage_row[:3], None),
            },
        ):
            _typo_stagers = FbxUtils.stagers(["probe_typo", "probe_one_way"])
    finally:
        _rr_log.removeHandler(_rr_handler)
    check(
        "a misspelt producer or stager method warns rather than vanishing",
        not _typo_producers
        and _typo_stagers["probe_typo"][1] is None
        and any("export_recrd" in m for m in _rr_handler.messages)
        and any("finsh_export" in m for m in _rr_handler.messages),
        _rr_handler.messages,
    )
    check(
        "...and a declared empty column resolves quietly",
        callable(_typo_stagers["probe_one_way"][0])
        and _typo_stagers["probe_one_way"][1] is None
        and len(_rr_handler.messages) == 2,
        _rr_handler.messages,
    )
    # ---- data_internal must never ride a hand-off -----------------------------
    # Structural for a current file: Maya's is a network node, and Blender's
    # private records live in a scene ID property group, not on an object. A
    # file saved before 2026-09-18 keeps them on a ``data_internal`` Empty until
    # its first private write folds them in, so the closure drops that by name.
    reset()
    DataNodes.write(ptk.Scope.PRIVATE, "smart_bake_sessions", "[]")
    bpy.ops.mesh.primitive_cube_add()
    _mesh = bpy.context.active_object
    _mesh.name = "HandoffMesh"
    _legacy = bpy.data.objects.new(DataNodes.INTERNAL, None)  # an unfolded file's
    bpy.context.scene.collection.objects.link(_legacy)
    _legacy[ptk.SceneRecords.EMISSIVE_REGISTRY.key] = "{}"
    _closure = BlenderExportMixin()._hierarchy_closure(list(bpy.context.scene.objects))
    _names = {o.name for o in _closure}
    check(
        "hierarchy closure drops data_internal (whole-scene send)",
        "HandoffMesh" in _names and DataNodes.INTERNAL not in _names,
        f"{_names}",
    )

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
    check(
        "Scene Exporter kwargs export writes the file",
        written_exp == exp_out
        and os.path.isfile(exp_out)
        and os.path.getsize(exp_out) > 0,
    )
    # (Round-trip import of a light is skipped: Blender 5.1's bundled io_scene_fbx
    #  importer raises on lights — CyclesLightSettings.cast_shadow — unrelated to export.)

    # ---- GLB sidecar: the slot's 'Also Export GLB' native glTF call ---------
    glb_out = os.path.join(tmp, "exporter.glb")
    bpy.ops.export_scene.gltf(
        filepath=glb_out,
        export_format="GLB",
        use_selection=False,
        export_cameras=True,
        export_lights=True,
    )
    check(
        "GLB sidecar (export_scene.gltf) writes the file",
        os.path.isfile(glb_out) and os.path.getsize(glb_out) > 0,
    )

    # ---- import_fbx missing file -> FileNotFoundError -----------------------
    try:
        FbxUtils.import_fbx(os.path.join(tmp, "does_not_exist.fbx"))
        check("import_fbx missing file -> FileNotFoundError", False)
    except FileNotFoundError:
        check("import_fbx missing file -> FileNotFoundError", True)

    # ---- animation takes: armed state -> post-write AnimStack splitting -----
    # Mirror of mayatk's apply_takes/apply_takes_from_node/reset_takes. The
    # write's single baked scene-range stack is split into one windowed stack
    # per take (fbx_utils module docstring documents why Blender's own
    # multi-stack modes can't be used: they null active actions and emit one
    # stack per strip/action, so every OTHER object freezes inside a take).
    def reset_anim():  # reset() leaves zero-user action datablocks behind
        reset()
        for a in list(bpy.data.actions):
            bpy.data.actions.remove(a)

    reset_anim()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30
    bpy.ops.mesh.primitive_cube_add()
    tk_cube = bpy.context.active_object
    tk_cube.name = "TakesCube"
    for frame, x in ((1, 0.0), (10, 10.0), (20, 10.0), (30, 0.0)):
        tk_cube.location.x = x
        tk_cube.keyframe_insert("location", frame=frame)
    bpy.ops.mesh.primitive_uv_sphere_add()
    tk_ball = bpy.context.active_object
    tk_ball.name = "TakesBall"
    for frame, z in ((1, 0.0), (30, 6.0)):
        tk_ball.location.z = z
        tk_ball.keyframe_insert("location", frame=frame)

    n_armed = FbxUtils.apply_takes(
        [{"name": "shotA", "start": 1, "end": 10}, ("shotB", 20, 30)]
    )
    check(
        "apply_takes arms both dict- and tuple-shaped defs",
        n_armed == 2
        and FbxUtils._pending_takes == [("shotA", 1, 10), ("shotB", 20, 30)],
        f"{FbxUtils._pending_takes}",
    )

    takes_out = os.path.join(tmp, "takes.fbx")
    FbxUtils.export(filepath=takes_out, objects=[tk_cube, tk_ball], bake_anim=True)
    check(
        "armed state is sticky through the write (Maya-parity; reset is the caller's)",
        FbxUtils._pending_takes is not None,
    )
    FbxUtils.reset_takes()
    check("reset_takes clears the armed state", FbxUtils._pending_takes is None)

    reset_anim()
    # The stock importer is the oracle of what the file holds: it makes an
    # action per (take, object). import_fbx plays only the covering take.
    bpy.ops.import_scene.fbx(filepath=takes_out, anim_offset=0.0)

    def action_fcurves(act):  # Blender 5.1 slotted-action walk
        layers = getattr(act, "layers", None)
        if not layers:
            yield from getattr(act, "fcurves", None) or ()
            return
        for layer in layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    yield from cb.fcurves

    # The whole-timeline stack stays beside the takes it was split into, as
    # Maya's Take 001 does (2026-10-05): the GLB's clips are cut from it and
    # a Shots + Full Sequence deliverable ships it.
    take_actions = sorted(a.name for a in bpy.data.actions)
    check(
        "the FBX ships one AnimStack per declared take beside the whole timeline",
        take_actions
        == [
            "TakesBall|Scene",
            "TakesBall|shotA",
            "TakesBall|shotB",
            "TakesCube|Scene",
            "TakesCube|shotA",
            "TakesCube|shotB",
        ],
        f"{take_actions}",
    )
    whole = bpy.data.actions.get("TakesCube|Scene")
    check(
        "the whole-timeline stack keeps the scene range it was baked over",
        whole is not None and tuple(round(f) for f in whole.frame_range) == (1, 30),
        f"{whole and tuple(whole.frame_range)}",
    )

    def x_extent(action_name):
        act = bpy.data.actions.get(action_name)
        for fc in action_fcurves(act) if act else ():
            if fc.data_path == "location" and fc.array_index == 0:
                vals = [k.co[1] for k in fc.keyframe_points]
                return (min(vals), max(vals)) if vals else None
        return None

    def z_extent(action_name):
        act = bpy.data.actions.get(action_name)
        for fc in action_fcurves(act) if act else ():
            if fc.data_path == "location" and fc.array_index == 2:
                vals = [k.co[1] for k in fc.keyframe_points]
                return (min(vals), max(vals)) if vals else None
        return None

    xa = x_extent("TakesCube|shotA")
    xb = x_extent("TakesCube|shotB")
    check(
        "cube's motion is windowed per take (0->10 in shotA, back down in shotB)",
        xa is not None
        and abs(xa[0]) < 0.01
        and abs(xa[1] - 10.0) < 0.01
        and xb is not None
        and abs(xb[0]) < 0.01
        and abs(xb[1] - 10.0) < 0.01,
        f"shotA={xa} shotB={xb}",
    )
    # THE multi-object guarantee (what NLA-strip takes cannot do): the second
    # object keeps moving inside both takes, values continuous with its curve.
    za = z_extent("TakesBall|shotA")
    zb = z_extent("TakesBall|shotB")
    check(
        "second object animates inside BOTH takes (no frozen bystanders)",
        za is not None
        and za[1] > 0.5
        and zb is not None
        and zb[0] > 3.5
        and abs(zb[1] - 6.0) < 0.01,
        f"shotA={za} shotB={zb}",
    )

    # ---- no takes armed: still ONE scene-range take (2026-09-19) -------------
    # Blender's operator defaults turn both multi-stack modes ON, and either
    # one writes a start-zeroed take per action INSTEAD of the scene-range take
    # -- so a caller passing only bake_anim=True got a file whose takes matched
    # neither the scene clock nor the data_export carrier's clip span (seeded
    # from the scene range). Every animated write is one scene-range take now;
    # an explicit request for the per-action modes is overridden, and said.
    from unittest import mock as _mock

    from blendertk.env_utils import fbx_utils as _fbx_module

    def written_takes(**opts):
        """``{take: [action frame spans]}`` for what the write shipped."""
        reset_anim()
        scene.frame_start, scene.frame_end = 1, 30
        bpy.ops.mesh.primitive_cube_add()
        early = bpy.context.active_object
        early.name = "EarlyCube"
        for frame, x in ((5, 0.0), (15, 4.0)):
            early.location.x = x
            early.keyframe_insert("location", frame=frame)
        bpy.ops.mesh.primitive_uv_sphere_add()
        late = bpy.context.active_object
        late.name = "LateBall"
        for frame, z in ((20, 0.0), (30, 3.0)):
            late.location.z = z
            late.keyframe_insert("location", frame=frame)
        out = os.path.join(tmp, "no_takes.fbx")
        FbxUtils.export(filepath=out, objects=[early, late], bake_anim=True, **opts)
        reset_anim()
        FbxUtils.import_fbx(out)
        takes = {}
        for act in bpy.data.actions:
            take = act.name.split("|", 1)[-1]
            takes.setdefault(take, []).append(
                round(act.frame_range[1] - act.frame_range[0])
            )
        return takes

    bare = written_takes()
    check(
        "no takes armed: the write is ONE take spanning the scene range",
        len(bare) == 1 and all(span == 29 for spans in bare.values() for span in spans),
        f"{bare}",
    )
    with _mock.patch.object(_fbx_module.logger, "warning") as warned:
        asked = written_takes(bake_anim_use_all_actions=True)
    check(
        "an explicit per-action request is overridden, and said",
        len(asked) == 1
        and any("bake_anim_use_all_actions" in str(c) for c in warned.call_args_list),
        f"{asked} warnings={warned.call_args_list}",
    )
    reset_anim()

    # ---- apply_takes_from_node reads the takes the carrier declares ----------
    # The shot record's clip ranges (what a publish writes;
    # ptk.SceneRecords.declared_takes) -- the current standard only.
    reset()
    check(
        "apply_takes_from_node -> 0 with no carrier in the scene",
        FbxUtils.apply_takes_from_node() == 0,
    )
    _ranged = {"shots": [{"clip": "intro", "start": 1, "end": 12, "objects": []}]}
    ptk.SceneRecords.SHOTS.save(DataNodes, _ranged)
    n_node = FbxUtils.apply_takes_from_node()
    check(
        "apply_takes_from_node arms the shot record's clip ranges",
        n_node == 1 and FbxUtils._pending_takes == [("intro", 1, 12)],
        f"{FbxUtils._pending_takes}",
    )
    FbxUtils.reset_takes()
    # A clip without its range declares no take, and a retired fbx_takes list
    # beside it is never read (2026-10-20: no legacy branch).
    import json as _takes_json

    ptk.SceneRecords.SHOTS.save(
        DataNodes, {"shots": [{"clip": "intro", "objects": []}]}
    )
    _list = _takes_json.dumps([{"name": "intro", "start": 2, "end": 14}])
    DataNodes.write(ptk.Scope.DELIVERABLE, "fbx_takes", _list)
    check(
        "a clip without its range arms nothing; a retired fbx_takes is not read",
        FbxUtils.apply_takes_from_node() == 0 and not FbxUtils._pending_takes,
        f"{FbxUtils._pending_takes}",
    )
    # An explicit attr reads the JSON take list it names; a node alone names none.
    DataNodes.write(ptk.Scope.DELIVERABLE, "btk_takes", _list)
    check(
        "an explicit attr arms the take list it names",
        FbxUtils.apply_takes_from_node(attr="btk_takes") == 1
        and FbxUtils._pending_takes == [("intro", 2, 14)],
        f"{FbxUtils._pending_takes}",
    )
    FbxUtils.reset_takes()
    # ...and says so: a node alone read the retired fbx_takes list before
    # 2026-10-20, so a silent 0 on that public signature hid the change
    # (CODE_STANDARD §8). Added: 2026-10-24
    from unittest import mock as _takes_mock

    with _takes_mock.patch.object(_fbx_module.logger, "warning") as _takes_warned:
        _alone = FbxUtils.apply_takes_from_node(node=DataNodes.EXPORT)
    check(
        "...and a node alone names no list, arms nothing, and warns why",
        _alone == 0
        and _takes_warned.called
        and "attr=" in str(_takes_warned.call_args),
        str(_takes_warned.call_args),
    )
    FbxUtils.reset_takes()
    ptk.SceneRecords.SHOTS.clear(DataNodes)
    for _key in ("fbx_takes", "btk_takes"):
        DataNodes.remove(ptk.Scope.DELIVERABLE, _key)

    # ---- armed takes + a write with no baked animation: file left as written -
    reset_anim()
    bpy.ops.mesh.primitive_cube_add()
    static = bpy.context.active_object
    FbxUtils.apply_takes([("ghost", 1, 10)])
    static_out = os.path.join(tmp, "takes_static.fbx")
    FbxUtils.export(filepath=static_out, objects=[static])  # bake_anim defaults False
    FbxUtils.reset_takes()
    reset_anim()
    created_static = FbxUtils.import_fbx(static_out)
    check(
        "no-anim write with armed takes stays a valid single-mesh FBX (warned, not broken)",
        any(o.type == "MESH" for o in created_static) and not bpy.data.actions,
        f"{[o.name for o in created_static]} actions={[a.name for a in bpy.data.actions]}",
    )

    # ---- a bracket that fails to OPEN finishes what it staged ---------------
    from unittest import mock

    ran = []
    FbxUtils.register_export_stager(
        "probe",
        prepare=lambda: ran.append("prepare"),
        finish=lambda: ran.append("finish"),
    )
    raised = False
    try:
        with mock.patch.object(FbxUtils, "publish", side_effect=RuntimeError("x")):
            FbxUtils.begin_export(FbxUtils.export_context(), stagers=())
    except RuntimeError:
        raised = True
    finally:
        FbxUtils.unregister_export_stager("probe")
    check(
        "a bracket whose publish raises finishes its stagers, depth restored",
        raised and ran == ["prepare", "finish"] and FbxUtils._export_depth == 0,
        f"raised={raised} ran={ran} depth={FbxUtils._export_depth}",
    )

    # ---- a whole-timeline stack named like a take keeps a name of its own ----
    # The kept stack is named after the scene; a shot named the same would make
    # two stacks one name, and every consumer that picks the sequence as "the
    # take no shot names" would find none.
    reset_anim()
    scene.frame_start, scene.frame_end = 1, 20
    bpy.ops.mesh.primitive_cube_add()
    twin = bpy.context.active_object
    twin.name = "TwinCube"
    for frame, x in ((1, 0.0), (20, 2.0)):
        twin.location.x = x
        twin.keyframe_insert("location", frame=frame)
    FbxUtils.apply_takes([(scene.name, 1, 10)])
    twin_out = os.path.join(tmp, "takes_twin.fbx")
    try:
        FbxUtils.export(filepath=twin_out, objects=[twin], bake_anim=True)
    finally:
        FbxUtils.reset_takes()
    twin_takes = ptk.FbxFile.load(twin_out, raw_payloads=False).take_names()
    check(
        "a take named like the scene leaves the whole timeline a distinct name",
        len(twin_takes) == 2 and scene.name in twin_takes and len(set(twin_takes)) == 2,
        f"{twin_takes}",
    )
    scene.frame_start, scene.frame_end = 1, 30

    # ---- the data_export carrier ships READABLE from any write (2026-10-05) --
    # Blender's exporter defaults custom-property export OFF, and the carrier
    # can be hidden: both repairs lived in the Scene Exporter and the bridges
    # alone, so a scripted btk.FbxUtils.export shipped the carrier as an Empty
    # holding no channel -- or, hidden, dropped it with a warning -- and the
    # deliverable arrived with no metadata.
    def carrier_round_trip(label, prepare=None, **opts):
        """Export a cube + the carrier; return (shipped channel, state after)."""
        reset()
        DataNodes.write(ptk.Scope.DELIVERABLE, "lightmaps", '{"version": 1}')
        node = DataNodes.get_export_node(create=False)
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.active_object
        mesh.name = "CarrierCube"
        if prepare is not None:
            prepare(node)
        out = os.path.join(tmp, f"carrier_{label}.fbx")
        FbxUtils.export(filepath=out, objects=[mesh, node], **opts)
        vl = bpy.context.view_layer
        after = {
            "hide_viewport": node.hide_viewport,
            "hide_select": node.hide_select,
            "collections": sorted(c.name for c in node.users_collection),
            "visible": node.visible_get(view_layer=vl),
        }
        reset()
        FbxUtils.import_fbx(out, use_custom_props=True)
        arrived = bpy.data.objects.get(DataNodes.EXPORT)
        return (arrived.get("lightmaps") if arrived else None), after

    shipped, _after = carrier_round_trip("plain")
    check(
        "a scripted export with no options ships the carrier readable",
        shipped == '{"version": 1}',
        f"{shipped!r}",
    )
    with mock.patch.object(_fbx_module.logger, "warning") as warned:
        shipped, _after = carrier_round_trip(
            "denied", use_custom_props=False, object_types={"MESH"}
        )
    check(
        "options that would ship it unreadable are forced, and said",
        shipped == '{"version": 1}'
        and any("use_custom_props" in str(c) for c in warned.call_args_list),
        f"{shipped!r} warnings={warned.call_args_list}",
    )

    def hide_carrier(node):
        node.hide_viewport = True
        node.hide_select = True

    shipped, after = carrier_round_trip("hidden", prepare=hide_carrier)
    check(
        "a hidden carrier ships, and is hidden again after the write",
        shipped == '{"version": 1}'
        and after["hide_viewport"] is True
        and after["hide_select"] is True,
        f"{shipped!r} {after}",
    )

    def exclude_carrier(node):
        coll = bpy.data.collections.get("CarrierHold") or bpy.data.collections.new(
            "CarrierHold"
        )
        if coll.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(coll)
        for owner in list(node.users_collection):
            owner.objects.unlink(node)
        coll.objects.link(node)
        bpy.context.view_layer.layer_collection.children[coll.name].exclude = True

    shipped, after = carrier_round_trip("excluded", prepare=exclude_carrier)
    check(
        "a carrier in an excluded collection ships, and is left where it was",
        shipped == '{"version": 1}' and after["collections"] == ["CarrierHold"],
        f"{shipped!r} {after}",
    )
    _hold = bpy.data.collections.get("CarrierHold")
    if _hold is not None:
        bpy.data.collections.remove(_hold)

    # The bridges' USD route shows it for the write too, as the Scene
    # Exporter's USD write does: the selection funnel dropped it there.
    from pxr import Usd

    reset()
    DataNodes.write(ptk.Scope.DELIVERABLE, "lightmaps", '{"version": 1}')
    usd_carrier = DataNodes.get_export_node(create=False)
    bpy.ops.mesh.primitive_cube_add()
    usd_mesh = bpy.context.active_object
    usd_mesh.name = "UsdCarrierCube"
    exclude_carrier(usd_carrier)
    usd_out = os.path.join(tmp, "carrier_excluded.usd")
    WebXrPreview()._export_usd([usd_mesh], usd_out, {})
    usd_stage = Usd.Stage.Open(usd_out)  # held: a temporary stage expires mid-walk
    usd_prims = {prim.GetName() for prim in usd_stage.Traverse()}
    del usd_stage
    check(
        "the bridge USD route ships a carrier from an excluded collection, left there",
        DataNodes.EXPORT in usd_prims
        and sorted(c.name for c in usd_carrier.users_collection) == ["CarrierHold"],
        f"{sorted(usd_prims)}",
    )
    _hold = bpy.data.collections.get("CarrierHold")
    if _hold is not None:
        bpy.data.collections.remove(_hold)

    # ---- staged transport nodes ride beside the object they hang under -------
    # Blender writes only selected objects, so a curve proxy a stager made
    # never reached a bridge's FBX: the bracket records what each stager's
    # prepare returned, and the write selects the ones whose parent ships.
    def keyed_fader(name):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        for frame, alpha in ((1, 1.0), (10, 0.0)):
            obj["opacity"] = alpha
            obj.keyframe_insert('["opacity"]', frame=frame)
        return obj

    reset_anim()
    scene.frame_start, scene.frame_end = 1, 10
    fader = keyed_fader("FadeCube")
    keyed_fader("OtherCube")
    marker = ptk.MeshConvert.CURVE_PROXY_MARKER
    proxy_out = os.path.join(tmp, "proxies.fbx")
    alone_out = os.path.join(tmp, "proxies_alone.fbx")
    check(
        "render_effects and emissive_groups are export stagers",
        {"render_effects", "emissive_groups"} <= set(FbxUtils.STAGERS),
        f"{sorted(FbxUtils.STAGERS)}",
    )
    with FbxUtils.export_prepared(stagers=("render_effects",)):
        FbxUtils.export(filepath=proxy_out, objects=[fader], bake_anim=True)
    left = [o.name for o in bpy.data.objects if o.get(marker)]
    shipped_models = ptk.FbxFile.load(proxy_out, raw_payloads=False).object_names(
        "Model"
    )
    check(
        "a staged curve proxy ships beside its object; its sibling's stays out",
        "FadeCube__opacity" in shipped_models
        and "OtherCube__opacity" not in shipped_models,
        f"{sorted(shipped_models)}",
    )
    check("the bracket removes the proxies after the write", not left, f"{left}")
    FbxUtils.export(filepath=alone_out, objects=[fader], bake_anim=True)
    check(
        "a write outside a staging bracket ships no proxy",
        "FadeCube__opacity"
        not in ptk.FbxFile.load(alone_out, raw_payloads=False).object_names("Model"),
    )

    # A hidden object cannot be selected, so the write drops it (and says so);
    # its staged proxy, a fresh visible Empty, shipped anyway, re-rooted at the
    # file's root -- where Unity applies a proxy's curve to every Renderer.
    hidden_fader = keyed_fader("HiddenFader")
    hidden_fader.hide_set(True)
    hidden_out = os.path.join(tmp, "proxies_hidden.fbx")
    with FbxUtils.export_prepared(stagers=("render_effects",)):
        FbxUtils.export(
            filepath=hidden_out, objects=[fader, hidden_fader], bake_anim=True
        )
    hidden_models = ptk.FbxFile.load(hidden_out, raw_payloads=False).object_names(
        "Model"
    )
    check(
        "a proxy whose object the write drops stays out with it",
        "HiddenFader" not in hidden_models
        and "HiddenFader__opacity" not in hidden_models
        and "FadeCube__opacity" in hidden_models,
        f"{sorted(hidden_models)}",
    )

    # The keyed emissive weights' proxies hang under the carrier, linked into
    # its collections: a carrier in an excluded collection was shown for the
    # write and its proxies were not, so the weights stayed out of the file.
    from blendertk.mat_utils.emissive_groups import EmissiveGroups

    reset_anim()
    glow_cube = keyed_fader("GlowCube")
    EmissiveGroups.add_group("glow", {"GlowCube": [0]})
    EmissiveGroups.make_weights_keyable(["glow"])
    EmissiveGroups.key_weight("glow", value=1.0, frame=1)
    EmissiveGroups.key_weight("glow", value=0.0, frame=10)
    glow_carrier = DataNodes.get_export_node(create=False)
    if glow_carrier is None:
        check("the keyed emissive group made a carrier", False)
    else:
        exclude_carrier(glow_carrier)
        glow_out = os.path.join(tmp, "emissive_excluded.fbx")
        with FbxUtils.export_prepared(stagers=("emissive_groups",)):
            FbxUtils.export(
                filepath=glow_out, objects=[glow_cube, glow_carrier], bake_anim=True
            )
        glow_models = ptk.FbxFile.load(glow_out, raw_payloads=False).object_names(
            "Model"
        )
        check(
            "a carrier in an excluded collection ships its keyed weights' proxies",
            DataNodes.EXPORT in glow_models and "emissiveGroup_glow" in glow_models,
            f"{sorted(glow_models)}",
        )
        check(
            "the carrier is left in its excluded collection after the write",
            sorted(c.name for c in glow_carrier.users_collection) == ["CarrierHold"],
            f"{sorted(c.name for c in glow_carrier.users_collection)}",
        )
    _hold = bpy.data.collections.get("CarrierHold")
    if _hold is not None:
        bpy.data.collections.remove(_hold)
    scene.frame_start, scene.frame_end = 1, 30

    # ---- keyed visibility rides the FBX's own Visibility channel -------------
    # Blender's exporter writes no visibility animation, so keyed hide_render
    # with no opacity never reached Unity; Maya writes it as each node's
    # Visibility curve, which Unity imports as the Renderer's m_Enabled.
    def visibility_keys(path, model_name):
        """``{stack: ([(frame, value), ...], KeyAttrFlags)}`` for *model_name*."""
        from io_scene_fbx import parse_fbx

        root, version = parse_fbx.parse(path)

        def kids(parent, eid):
            return [e for e in parent.elems if e.id == eid]

        objects = kids(root, b"Objects")[0]
        by_uid = {e.props[0]: e for e in objects.elems if e.props}
        parents, children = {}, {}
        for c in kids(root, b"Connections")[0].elems:
            prop = c.props[3] if len(c.props) > 3 else None
            parents.setdefault(c.props[1], []).append((c.props[2], prop))
            children.setdefault(c.props[2], []).append((c.props[1], prop))

        def label(e):
            return e.props[1].split(b"\x00")[0].decode()

        def parent_of(uid, kind):
            for p, _prop in parents.get(uid, []):
                if by_uid.get(p) is not None and by_uid[p].id == kind:
                    return p
            return None

        model = next(
            uid
            for uid, e in by_uid.items()
            if e.id == b"Model" and label(e) == model_name
        )
        per_frame = FbxUtils._file_ktime(root, version) / (
            scene.render.fps / scene.render.fps_base
        )
        found = {}
        for uid, e in by_uid.items():
            if e.id != b"AnimationCurveNode":
                continue
            if (model, b"Visibility") not in parents.get(uid, []):
                continue
            stack = parent_of(parent_of(uid, b"AnimationLayer"), b"AnimationStack")
            curve = next(
                c for c, prop in children.get(uid, []) if prop == b"d|Visibility"
            )
            times = kids(by_uid[curve], b"KeyTime")[0].props[0]
            values = kids(by_uid[curve], b"KeyValueFloat")[0].props[0]
            flags = list(kids(by_uid[curve], b"KeyAttrFlags")[0].props[0])
            found[label(by_uid[stack])] = (
                [(round(t / per_frame, 3), float(v)) for t, v in zip(times, values)],
                flags,
            )
        return found

    def blinker(name="Blink"):
        bpy.ops.mesh.primitive_cube_add(location=(0, 4, 0))
        obj = bpy.context.active_object
        obj.name = name
        for frame, hidden in ((1, False), (10, True), (20, False)):
            obj.hide_render = hidden
            obj.keyframe_insert("hide_render", frame=frame)
        obj.hide_render = False
        return obj

    reset_anim()
    scene.frame_start, scene.frame_end = 1, 30
    bpy.ops.mesh.primitive_cube_add()
    walker = bpy.context.active_object
    walker.name = "Walker"
    for frame, x in ((1, 0.0), (30, 3.0)):
        walker.location.x = x
        walker.keyframe_insert("location", frame=frame)
    blink = blinker()
    vis_out = os.path.join(tmp, "visibility.fbx")
    FbxUtils.export(filepath=vis_out, objects=[walker, blink], bake_anim=True)
    keyed = visibility_keys(vis_out, "Blink")
    keys, flags = keyed.get(scene.name, ([], []))
    check(
        "keyed hide_render ships as the node's stepped Visibility curve",
        keys == [(1.0, 1.0), (10.0, 0.0), (20.0, 1.0), (30.0, 1.0)]
        and flags
        and all((f & 0x0E) == 0x02 for f in flags),
        f"{keyed}",
    )
    check(
        "an object with no keyed visibility gets no Visibility curve",
        visibility_keys(vis_out, "Walker") == {},
    )
    reset_anim()
    created_vis = FbxUtils.import_fbx(vis_out)
    check(
        "Blender's importer still reads the file",
        {"Walker", "Blink"} <= {o.name for o in created_vis},
        f"{[o.name for o in created_vis]}",
    )

    # Nothing else animated: the exporter writes no stack at all, so the
    # visibility needs one of its own to ride.
    reset_anim()
    blink = blinker()
    only_out = os.path.join(tmp, "visibility_only.fbx")
    FbxUtils.export(filepath=only_out, objects=[blink], bake_anim=True)
    keys, _flags = visibility_keys(only_out, "Blink").get(scene.name, ([], []))
    check(
        "visibility alone still ships, in a scene-range stack of its own",
        keys == [(1.0, 1.0), (10.0, 0.0), (20.0, 1.0), (30.0, 1.0)]
        and ptk.FbxFile.load(only_out, raw_payloads=False).take_names() == [scene.name],
        f"{keys}",
    )
    static_vis_out = os.path.join(tmp, "visibility_static.fbx")
    FbxUtils.export(filepath=static_vis_out, objects=[blink])  # bake_anim off
    check(
        "a write that bakes no animation carries no visibility curve either",
        visibility_keys(static_vis_out, "Blink") == {}
        and not ptk.FbxFile.load(static_vis_out, raw_payloads=False).take_names(),
    )

    # Split into takes: each take carries its own window of the curve, held
    # at the boundary rather than interpolated across the step.
    reset_anim()
    blink = blinker()
    bpy.ops.mesh.primitive_cube_add()
    walker = bpy.context.active_object
    walker.name = "Walker"
    for frame, x in ((1, 0.0), (30, 3.0)):
        walker.location.x = x
        walker.keyframe_insert("location", frame=frame)
    FbxUtils.apply_takes([("early", 1, 15), ("late", 15, 30)])
    split_out = os.path.join(tmp, "visibility_takes.fbx")
    try:
        FbxUtils.export(filepath=split_out, objects=[walker, blink], bake_anim=True)
    finally:
        FbxUtils.reset_takes()
    split = {
        take: keys for take, (keys, _f) in visibility_keys(split_out, "Blink").items()
    }
    check(
        "each take carries its window of the visibility, held across the step",
        split.get("early") == [(1.0, 1.0), (10.0, 0.0), (15.0, 0.0)]
        and split.get("late") == [(15.0, 0.0), (20.0, 1.0), (30.0, 1.0)],
        f"{split}",
    )

    # ---- a shadow source LIGHT ships through a type filter without LIGHT -----
    # The shadow record names its source for the engine to follow; Blender's
    # exporter drops every object whose type the filter excludes, so a SUN
    # source never reached Unity and follow-source fell back to the baked keys.
    reset()
    sun = bpy.data.objects.new("ShadowSun", bpy.data.lights.new("ShadowSun", "SUN"))
    lamp = bpy.data.objects.new("FillLamp", bpy.data.lights.new("FillLamp", "POINT"))
    for light in (sun, lamp):
        bpy.context.scene.collection.objects.link(light)
    bpy.ops.mesh.primitive_cube_add()
    caster = bpy.context.active_object
    caster.name = "Caster"
    ptk.SceneRecords.SHADOWS.save(
        DataNodes,
        {
            "unit_scale": 1.0,
            "planes": [{"name": "Caster_shadow", "source": "ShadowSun"}],
        },
    )
    sun_out = os.path.join(tmp, "shadow_source.fbx")
    try:
        FbxUtils.export(filepath=sun_out, objects=[caster, sun, lamp])
    finally:
        ptk.SceneRecords.SHADOWS.clear(DataNodes)
    sun_models = ptk.FbxFile.load(sun_out, raw_payloads=False).object_names("Model")
    check(
        "the shadow record's source light ships; a light it does not name stays out",
        "ShadowSun" in sun_models and "FillLamp" not in sun_models,
        f"{sorted(sun_models)}",
    )

    # ---- an armed take outside the scene range is baked, not clamped ---------
    # Only the Scene Exporter's takes task widened the scene range to cover the
    # takes it arms; a bridge hand-off arms them too and wrote with the scene
    # range as it stood, so a shot past it was clamped to the baked span. The
    # write now covers every armed take for itself (as Maya's apply_takes sets
    # the bake range), and gives the scene its range back after.
    reset_anim()
    scene.frame_start, scene.frame_end = 1, 20
    bpy.ops.mesh.primitive_cube_add()
    outrun = bpy.context.active_object
    outrun.name = "OutrunCube"
    for frame, x in ((1, 0.0), (40, 4.0)):
        outrun.location.x = x
        outrun.keyframe_insert("location", frame=frame)
    FbxUtils.apply_takes([("late", 25, 40)])
    outrun_out = os.path.join(tmp, "takes_outrun.fbx")
    try:
        FbxUtils.export(filepath=outrun_out, objects=[outrun], bake_anim=True)
    finally:
        FbxUtils.reset_takes()
    outrun_spans = {
        take: tuple(round(t * 24) for t in span)
        for take, span in ptk.FbxFile.load(
            outrun_out, raw_payloads=False, span_arrays=("KeyTime",)
        )
        .take_spans()
        .items()
    }
    check(
        "an armed take past the scene range ships its whole window",
        outrun_spans.get("late") == (25, 40)
        and outrun_spans.get(scene.name) == (1, 40),
        f"{outrun_spans}",
    )
    check(
        "the write gives the scene its own range back",
        (scene.frame_start, scene.frame_end) == (1, 20),
        f"{(scene.frame_start, scene.frame_end)}",
    )
    scene.frame_start, scene.frame_end = 1, 30

    # ---- baked keys are the scene's own values (2026-10-05) ------------------
    # Blender's exporter thins its baked keys by default (simplify factor 1.0),
    # and the thinned curve is interpolated back: a 0-5 m ease-out shipped
    # 1.47e-3 m off at frame 98, in the sequence and every shot cut from it,
    # where Maya's bake is exact. A factor of 0.0 is exact too but keys EVERY
    # channel of every shipped object, static ones included.
    reset_anim()
    scene.frame_start, scene.frame_end = 1, 100
    bpy.ops.mesh.primitive_cube_add()
    eased = bpy.context.active_object
    eased.name = "EasedCube"
    for frame, x in ((1, 0.0), (100, 5.0)):
        eased.location.x = x
        eased.keyframe_insert("location", index=0, frame=frame)
    bpy.ops.mesh.primitive_cube_add(location=(0, 3, 0))
    parked = bpy.context.active_object
    parked.name = "ParkedCube"
    eased_truth = {}
    for frame in range(1, 101):
        scene.frame_set(frame)
        eased_truth[frame] = eased.matrix_world.translation.x
    scene.frame_set(1)

    def eased_error(path):
        """Worst |FBX - scene| over every frame for EasedCube's X, in metres,
        and the names of the Models the file animates."""
        import numpy as np
        from io_scene_fbx import parse_fbx

        root, version = parse_fbx.parse(path)

        def kids(parent, eid):
            return [e for e in parent.elems if e.id == eid]

        objects = kids(root, b"Objects")[0]
        by_uid = {e.props[0]: e for e in objects.elems if e.props}
        conns = kids(root, b"Connections")[0].elems
        models = {
            e.props[0]: e.props[1].split(b"\x00")[0].decode()
            for e in kids(objects, b"Model")
        }
        animated = {
            models[c.props[2]]
            for c in conns
            if len(c.props) > 3
            and c.props[2] in models
            and by_uid.get(c.props[1]) is not None
            and by_uid[c.props[1]].id == b"AnimationCurveNode"
        }
        model = next(uid for uid, name in models.items() if name == "EasedCube")
        node = next(
            c.props[1]
            for c in conns
            if len(c.props) > 3
            and c.props[2] == model
            and c.props[3] == b"Lcl Translation"
        )
        curve = next(
            c.props[1]
            for c in conns
            if len(c.props) > 3 and c.props[2] == node and c.props[3] == b"d|X"
        )
        kt = np.array(kids(by_uid[curve], b"KeyTime")[0].props[0], dtype=np.float64)
        kv = np.array(
            kids(by_uid[curve], b"KeyValueFloat")[0].props[0], dtype=np.float64
        )
        per_frame = FbxUtils._file_ktime(root, version) / (
            scene.render.fps / scene.render.fps_base
        )
        # The FBX is in centimetres (the exporter's unit scale).
        error = max(
            abs(np.interp(f * per_frame, kt, kv) / 100.0 - eased_truth[f])
            for f in range(1, 101)
        )
        return error, animated

    exact_out = os.path.join(tmp, "eased_exact.fbx")
    FbxUtils.export(filepath=exact_out, objects=[eased, parked], bake_anim=True)
    exact_error, exact_animated = eased_error(exact_out)
    check(
        "baked keys are the scene's per-frame values, and a static object gets none",
        exact_error < 1e-5 and exact_animated == {"EasedCube"},
        f"error={exact_error:.3e} m animated={sorted(exact_animated)}",
    )
    thinned_out = os.path.join(tmp, "eased_thinned.fbx")
    FbxUtils.export(
        filepath=thinned_out,
        objects=[eased, parked],
        bake_anim=True,
        bake_anim_simplify_factor=1.0,
    )
    check(
        "a caller's own simplify factor is honored",
        eased_error(thinned_out)[0] > 1e-4,
    )
    scene.frame_start, scene.frame_end = 1, 30

    # ---- a multi-take import carries ALL its animation (2026-10-11) ---------
    # No importer merges takes: Blender's makes an action per (stack, object),
    # assigns each object the FIRST stack's and parks the rest under a fake
    # user -- a Shots Only file played its first shot, a whole-timeline take
    # listed after its slices played the first slice. import_fbx reads the
    # file's take layout (ptk.FbxFile.take_layout) and hands the importer ONE
    # file holding exactly what plays (merged where the file splits it) and
    # what is parked (each conflicting take whole, into Key Stash).
    import logging as _logging

    from blendertk.anim_utils.key_stash._key_stash import KeyStash

    class _Records(_logging.Handler):
        def __init__(self):
            super().__init__(_logging.DEBUG)
            self.records = []

        def emit(self, record):
            self.records.append((record.levelname, record.getMessage()))

    fbx_log = _logging.getLogger(_fbx_module.__name__)

    def import_logged(path, **kwargs):
        """``(created, [(level, message)])`` of one ``import_fbx``."""
        handler, level = _Records(), fbx_log.level
        fbx_log.addHandler(handler)
        fbx_log.setLevel(_logging.DEBUG)
        try:
            return FbxUtils.import_fbx(path, **kwargs), handler.records
        finally:
            fbx_log.removeHandler(handler)
            fbx_log.setLevel(level)

    def reset_stash():
        backend = KeyStash._persistence
        if backend is not None and hasattr(backend, "remove_callbacks"):
            backend.remove_callbacks()
        KeyStash._active = None
        KeyStash.set_persistence(None)

    def fresh():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        reset_stash()

    def shots_file(name, takes, drop=("Scene",)):
        """A cube keyed every frame 1-100 (x = f / 10), written with *takes*
        armed, then -- as the Scene Exporter's Shots Only does -- the
        whole-timeline take dropped."""
        fresh()
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 100
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        cube.name = "TkCube"
        for f in range(1, 101):
            cube.location.x = f * 0.1
            cube.keyframe_insert("location", index=0, frame=f)
        FbxUtils.apply_takes(takes)
        path = os.path.join(tmp, name)
        try:
            FbxUtils.export(filepath=path, objects=[cube], bake_anim=True)
        finally:
            FbxUtils.reset_takes()
        if drop:
            ptk.FbxMedia.drop_takes(path, names=list(drop))
        return path

    def x_keys(action):
        """``[(frame, value)]`` of *action*'s location.x curve."""
        for fc in action_fcurves(action) if action is not None else ():
            if fc.data_path == "location" and fc.array_index == 0:
                return [(round(k.co.x, 3), k.co.y) for k in fc.keyframe_points]
        return []

    def played(name):
        obj = bpy.data.objects.get(name)
        ad = obj.animation_data if obj is not None else None
        return ad.action if ad is not None else None

    def importer_actions():
        """The actions named ``<object>|<take>`` -- what the importer made."""
        return sorted(a.name for a in bpy.data.actions if "|" in a.name)

    # Shots Only: every take holds only its window, so ANY one take loses the
    # rest; the union is the whole performance.
    shots_only = shots_file("shots_only.fbx", [("early", 1, 30), ("late", 50, 70)])
    fresh()
    _created, logged = import_logged(shots_only)
    keys = x_keys(played("TkCube"))
    want = list(range(1, 31)) + list(range(50, 71))
    check(
        "Shots Only: the one action plays every shot's keys",
        [f for f, _v in keys] == want,
        f"{len(keys)} keys, {keys[:2]}..{keys[-2:]}",
    )
    check(
        "Shots Only: each key keeps its value",
        keys and all(abs(v - f * 0.1) < 1e-4 for f, v in keys),
    )
    check(
        "Shots Only: no per-take action is left behind (one action, named for "
        "the merged take)",
        importer_actions() == ["TkCube|Take 001"],
        importer_actions(),
    )
    info = [m for lvl, m in logged if lvl == "INFO" and "shots_only.fbx" in m]
    check(
        "Shots Only: one INFO line names the layout and the takes merged",
        len(info) == 1
        and "merged" in info[0]
        and "early" in info[0]
        and "late" in info[0],
        logged,
    )

    # The scratch rewrite failing never refuses the import: the file lands as
    # written (each object its first take), and a warning says why.
    with _mock.patch.object(
        ptk.FbxMedia, "merge_takes", side_effect=ValueError("no merge here")
    ):
        fresh()
        created, logged = import_logged(shots_only)
    check(
        "a failed merge never refuses: the file imports as written, warned",
        [o.name for o in created] == ["TkCube"]
        and x_keys(played("TkCube"))
        and any(lvl == "WARNING" and "no merge here" in m for lvl, m in logged),
        logged,
    )

    # The layout read tripping on an odd file (a KeyError, not the reader's
    # ValueError) never refuses either: the importer's own take rule runs, as
    # it did before the layout was read (mayatk's twin, 2026-10-12).
    with _mock.patch.object(ptk.FbxFile, "load", side_effect=KeyError("Takes")):
        fresh()
        try:
            created, logged = import_logged(shots_only)
            tripped = None
        except Exception as exc:  # noqa: BLE001 -- the defect raised KeyError
            created, logged, tripped = [], [], repr(exc)
    check(
        "a layout read that trips never refuses: the file imports, warned",
        tripped is None
        and [o.name for o in created] == ["TkCube"]
        and any(lvl == "WARNING" and "Takes" in m for lvl, m in logged),
        f"raised={tripped} {logged}",
    )

    # A whole-timeline take listed AFTER its slices: the stock importer hands
    # every object the first stack's action -- a slice.
    cover_last = shots_file(
        "cover_last.fbx",
        [("early", 1, 30), ("late", 50, 70), ("Whole", 1, 100)],
    )
    fresh()
    import_logged(cover_last)
    keys = x_keys(played("TkCube"))
    check(
        "covered, the cover listed last: the covering take plays",
        [f for f, _v in keys] == list(range(1, 101)),
        f"{len(keys)} keys, {keys[:1]}..{keys[-1:]}",
    )
    check(
        "covered: no slice's action is left behind",
        importer_actions() == ["TkCube|Whole"],
        importer_actions(),
    )

    # A gap-0 seam: two shots share a frame; its key lands once.
    seam = shots_file("seam.fbx", [("s1", 1, 30), ("s2", 30, 60)])
    fresh()
    import_logged(seam)
    keys = x_keys(played("TkCube"))
    check(
        "gap-0 seam: both shots play, the shared frame keyed once",
        [f for f, _v in keys] == list(range(1, 61))
        and abs(dict(keys).get(30.0, 0.0) - 3.0) < 1e-4,
        f"{len(keys)} keys",
    )

    # A clip library: alternatives keying the same channels over the same
    # frames. Blender's all-actions mode writes one take per (object, action).
    fresh()
    bpy.ops.mesh.primitive_cube_add()
    lib_cube = bpy.context.active_object
    lib_cube.name = "LibCube"
    bpy.ops.mesh.primitive_uv_sphere_add()
    lib_ball = bpy.context.active_object
    lib_ball.name = "LibBall"
    for act_name, rate in (("Walk", 0.1), ("Run", 0.3)):
        act = bpy.data.actions.new(act_name)
        lib_cube.animation_data_create()
        lib_cube.animation_data.action = act
        for f in range(1, 21):
            lib_cube.location.x = f * rate
            lib_cube.keyframe_insert("location", index=0, frame=f)
    # The exporter applies every action to every ANIMATED object.
    lib_ball.animation_data_create()
    lib_ball.animation_data.action = bpy.data.actions["Walk"]
    library = os.path.join(tmp, "clip_library.fbx")
    for o in (lib_cube, lib_ball):
        o.select_set(True)
    bpy.ops.export_scene.fbx(
        filepath=library,
        use_selection=True,
        bake_anim=True,
        bake_anim_use_all_actions=True,
        bake_anim_use_nla_strips=False,
    )
    lib_layout = ptk.FbxFile.load(library).take_layout()
    check(
        "the clip library is conflicted: two takes play (one per object, so "
        "they merge), the rest are alternatives",
        lib_layout["kind"] == "conflicted"
        and len(lib_layout["live"]) == 2
        and lib_layout["stash"],
        {k: lib_layout[k] for k in ("kind", "live", "stash")},
    )
    # The oracle: what the stock importer reads each take as.
    fresh()
    bpy.ops.import_scene.fbx(filepath=library, anim_offset=0.0)
    oracle = {}
    for take in lib_layout["live"] + lib_layout["stash"]:
        for obj_name in ("LibCube", "LibBall"):
            act = bpy.data.actions.get(f"{obj_name}|{take}")
            if act is not None and x_keys(act):
                oracle[take] = (obj_name, x_keys(act))
    fresh()
    _created, logged = import_logged(library)
    for take in lib_layout["live"]:
        obj_name, take_keys = oracle[take]
        got = x_keys(played(obj_name))
        check(
            f"conflicted: live take {take!r} plays on {obj_name}",
            got == take_keys,
            f"{got[:2]} vs {take_keys[:2]}",
        )
    clips = {c.label: c for c in KeyStash.active().clips}
    check(
        "conflicted: one Key Stash clip per stashed take, named after it",
        sorted(clips) == sorted(lib_layout["stash"]),
        sorted(clips),
    )
    for take in lib_layout["stash"]:
        clip = clips.get(take)
        obj_name, take_keys = oracle[take]
        recs = [
            r for r in (clip.curves if clip else ()) if r["data_path"] == "location"
        ]
        rec = next((r for r in recs if r["array_index"] == 0), None)
        parked = bpy.data.actions.get(rec["action"]) if rec else None
        parked_keys = []
        for fc in action_fcurves(parked) if parked else ():
            if fc.data_path == "location" and fc.array_index == 0:
                parked_keys = [(round(k.co.x, 3), k.co.y) for k in fc.keyframe_points]
        check(
            f"conflicted: clip {take!r} holds that take's keys on {obj_name}",
            clip is not None
            and clip.objects == [obj_name]
            and parked_keys == take_keys
            and clip.metadata
            == {"source": "fbx", "path": "clip_library.fbx", "take": take},
            f"{parked_keys[:2]} vs {take_keys[:2]} meta={clip and clip.metadata}",
        )
    check(
        "conflicted: no importer action is left behind",
        importer_actions() == ["LibBall|Take 001", "LibCube|Take 001"]
        and len(bpy.data.actions) == 2 + len(clips),
        [a.name for a in bpy.data.actions],
    )
    info = [m for lvl, m in logged if lvl == "INFO" and "clip_library.fbx" in m]
    check(
        "conflicted: the INFO line names the stashed takes and why",
        len(info) == 1
        and "conflicted" in info[0]
        and all(t in info[0] for t in lib_layout["stash"]),
        logged,
    )

    # A throwaway import (a staged comparison scene) must not write records:
    # stash=False plays the same, parks nothing, and says -- to the caller's
    # own logger -- what it left out (mirror of mayatk's import_scene).
    fresh()
    caller_log = _logging.getLogger("fbx_take_opt_out")
    caller_records, caller_level = _Records(), caller_log.level
    caller_log.addHandler(caller_records)
    caller_log.setLevel(_logging.DEBUG)
    try:
        _created, logged = import_logged(library, stash=False, logger=caller_log)
    finally:
        caller_log.removeHandler(caller_records)
        caller_log.setLevel(caller_level)
    warned = [m for lvl, m in caller_records.records if lvl == "WARNING"]
    check(
        "stash=False: a WARNING to the caller's logger names every take left out",
        len(warned) == 1
        and all(t in warned[0] for t in lib_layout["stash"])
        and not logged,
        (caller_records.records, logged),
    )
    check(
        "stash=False: no Key Stash clip, no stash action, no leftover",
        not KeyStash.active().clips
        and not [a for a in bpy.data.actions if a.name.startswith("keyStash")]
        and importer_actions() == ["LibBall|Take 001", "LibCube|Take 001"],
        [a.name for a in bpy.data.actions],
    )
    for take in lib_layout["live"]:
        obj_name, take_keys = oracle[take]
        check(
            f"stash=False: live take {take!r} still plays on {obj_name}",
            x_keys(played(obj_name)) == take_keys,
        )

    # A takeless file (a static export) imports as it is.
    fresh()
    bpy.ops.mesh.primitive_cube_add()
    static_cube = bpy.context.active_object
    static_cube.name = "StaticCube"
    takeless = os.path.join(tmp, "takeless.fbx")
    FbxUtils.export(filepath=takeless, objects=[static_cube])
    fresh()
    created, logged = import_logged(takeless)
    check(
        "takeless: the objects land, no action, no error",
        [o.name for o in created] == ["StaticCube"]
        and not len(bpy.data.actions)
        and not [m for lvl, m in logged if lvl in ("WARNING", "ERROR")],
        f"{[o.name for o in created]} {logged}",
    )

    # ---- the curves Blender's importer drops land too (2026-10-17) ----------
    # The importer animates transforms, shape keys and camera lenses only: a
    # node's Visibility and every user-property curve (Maya's extra
    # attributes -- the render effects' opacity / highlight) were read and
    # dropped; a production module keyed them on 558 channel-takes and none
    # arrived. import_fbx lands them where the Maya bridge's pull lands the
    # same channels, in the same take structure.
    import array as _array

    from io_scene_fbx import data_types as _fbx_types
    from io_scene_fbx import encode_bin as _fbx_encode
    from io_scene_fbx import parse_fbx as _fbx_parse
    from io_scene_fbx.fbx_utils import FBX_ANIM_KEY_VERSION as _KEY_VERSION

    CONST, LIN, CUBIC_USER = 0x2, 0x4, 0x408  # KeyAttrFlags, as Maya writes them

    def add_property_curves(path, curves):
        """Key user properties in a Blender-written FBX as Maya writes them:
        *curves* ``{take: {(model, prop): [(frame, value, KeyAttrFlags)]}}`` --
        a ``d|<prop>`` curve on the take's layer, the property declared on the
        Model as an animated user property (``A+U``)."""
        root, version = _fbx_parse.parse(path)
        find, finds = FbxUtils._find_elem, FbxUtils._find_elems
        make, conn = FbxUtils._make_parsed, FbxUtils._connection
        objects_el, conns_el = find(root, b"Objects"), find(root, b"Connections")
        fps, ktime = FbxUtils._file_frame_scale(root, version)

        def label(e):
            return e.props[1].split(b"\x00\x01")[0].decode()

        models = {label(e): e for e in finds(objects_el, b"Model")}
        stacks = {label(e): e.props[0] for e in finds(objects_el, b"AnimationStack")}
        layers = {e.props[0] for e in finds(objects_el, b"AnimationLayer")}
        layer_of = {}
        for c in conns_el.elems:
            if c.props[0] == b"OO" and c.props[1] in layers:
                layer_of.setdefault(c.props[2], c.props[1])
        new_uid = FbxUtils._uid_allocator(objects_el)
        added = 0
        for take, channels in curves.items():
            for (model_name, prop), keys in channels.items():
                model, prop_b = models[model_name], prop.encode()
                node, curve = new_uid(), new_uid()
                first = float(keys[0][1])
                channel = make(
                    _fbx_parse,
                    b"P",
                    [b"d|" + prop_b, b"Number", b"", b"A", first],
                    b"SSSSD",
                )
                objects_el.elems.append(
                    make(
                        _fbx_parse,
                        b"AnimationCurveNode",
                        [node, prop_b + b"\x00\x01AnimCurveNode", b""],
                        b"LSS",
                        [make(_fbx_parse, b"Properties70", elems=[channel])],
                    )
                )
                arrays = (
                    (
                        b"KeyTime",
                        "l",
                        _fbx_types.ARRAY_INT64,
                        [round(f * ktime / fps) for f, _v, _m in keys],
                    ),
                    (
                        b"KeyValueFloat",
                        "f",
                        _fbx_types.ARRAY_FLOAT32,
                        [float(v) for _f, v, _m in keys],
                    ),
                    (
                        b"KeyAttrFlags",
                        "i",
                        _fbx_types.ARRAY_INT32,
                        [m for _f, _v, m in keys],
                    ),
                    (
                        b"KeyAttrDataFloat",
                        "f",
                        _fbx_types.ARRAY_FLOAT32,
                        [0.0, 0.0, 9.419963346924634e-30, 0.0] * len(keys),
                    ),
                    (b"KeyAttrRefCount", "i", _fbx_types.ARRAY_INT32, [1] * len(keys)),
                )
                objects_el.elems.append(
                    make(
                        _fbx_parse,
                        b"AnimationCurve",
                        [curve, b"\x00\x01AnimCurve", b""],
                        b"LSS",
                        [
                            make(_fbx_parse, b"Default", [first], b"D"),
                            make(_fbx_parse, b"KeyVer", [_KEY_VERSION], b"I"),
                        ]
                        + [
                            make(
                                _fbx_parse,
                                name,
                                [_array.array(code, values)],
                                kind.encode(),
                            )
                            for name, kind, code, values in arrays
                        ],
                    )
                )
                conns_el.elems.extend(
                    [
                        conn(_fbx_parse, b"OO", node, layer_of[stacks[take]]),
                        conn(_fbx_parse, b"OP", node, model.props[0], prop_b),
                        conn(_fbx_parse, b"OP", curve, node, b"d|" + prop_b),
                    ]
                )
                props70 = find(model, b"Properties70")
                if props70 is None:
                    props70 = make(_fbx_parse, b"Properties70")
                    model.elems.append(props70)
                declared = next(
                    (p for p in props70.elems if p.props and p.props[0] == prop_b), None
                )
                if declared is None:
                    props70.elems.append(
                        make(
                            _fbx_parse,
                            b"P",
                            [prop_b, b"Number", b"", b"A+U", first],
                            b"SSSSD",
                        )
                    )
                else:
                    declared.props[3] = b"A+U"
                added += 1
        FbxUtils._count_definitions(
            _fbx_parse, root, {b"AnimationCurveNode": added, b"AnimationCurve": added}
        )
        enc = _fbx_encode.FBXElem(b"")
        for child in root.elems:
            enc.elems.append(FbxUtils._parsed_to_encode(child, _fbx_encode))
        _fbx_encode.write(path, enc, version)
        return path

    def fcurve_keys(obj, data_path):
        """``[(frame, value, interpolation)]`` of the curve *obj* plays."""
        ad = obj.animation_data
        for fc in action_fcurves(ad.action) if ad and ad.action else ():
            if fc.data_path == data_path:
                return [
                    (round(k.co.x, 3), round(k.co.y, 4), k.interpolation)
                    for k in fc.keyframe_points
                ]
        return []

    def hidden_at(obj, frames):
        """Per channel, the frames of *frames* the played hide curve hides."""
        ad = obj.animation_data
        out = {}
        for fc in action_fcurves(ad.action) if ad and ad.action else ():
            if fc.data_path in ("hide_render", "hide_viewport"):
                out[fc.data_path] = [f for f in frames if fc.evaluate(f) >= 0.5]
        return out

    # A Shots Only file (two windows, merged on import): a keyed Empty whose
    # visibility steps over a child that keys nothing, an opacity curve per
    # shot, and an object keying only a custom property.
    fresh()
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 60
    lamp = bpy.data.objects.new("Lamp", None)
    bpy.context.scene.collection.objects.link(lamp)
    for f in range(1, 61):
        lamp.location.x = f * 0.1
        lamp.keyframe_insert("location", index=0, frame=f)
    for f, hide in ((1, False), (10, True), (20, False), (40, True), (50, False)):
        lamp.hide_render = hide
        lamp.keyframe_insert("hide_render", frame=f)
    lamp.hide_render = False
    lamp["opacity"] = 0.5
    bpy.ops.mesh.primitive_cube_add()
    bulb = bpy.context.active_object
    bulb.name = "Bulb"
    bulb.parent = lamp
    dial = bpy.data.objects.new("Dial", None)
    bpy.context.scene.collection.objects.link(dial)
    dial["highlight"] = 0.0
    dropped_fbx = os.path.join(tmp, "dropped_channels.fbx")
    FbxUtils.apply_takes([("ShotA", 1, 30), ("ShotB", 31, 60)])
    try:
        FbxUtils.export(
            filepath=dropped_fbx,
            objects=[lamp, bulb, dial],
            bake_anim=True,
            use_custom_props=True,
        )
    finally:
        FbxUtils.reset_takes()
    ptk.FbxMedia.drop_takes(dropped_fbx, names=["Scene"])
    add_property_curves(
        dropped_fbx,
        {
            "ShotA": {
                ("Lamp", "opacity"): [(1, 0.0, LIN), (15, 1.0, LIN), (30, 1.0, LIN)],
                ("Dial", "highlight"): [(5, 0.0, CUBIC_USER), (10, 1.0, CUBIC_USER)],
            },
            "ShotB": {
                ("Lamp", "opacity"): [(31, 1.0, CONST), (45, 0.0, LIN), (60, 0.0, LIN)]
            },
        },
    )
    fixture = ptk.FbxFile.load(dropped_fbx).take_keys(("Visibility",), user=True)
    check(
        "the fixture: two shots keying Visibility and the user properties, merged "
        "on import",
        ptk.FbxFile.load(dropped_fbx).take_layout()["kind"] == "merged"
        and {k[:2] for k in fixture.get("ShotA", {})}
        == {("Lamp", "Visibility"), ("Lamp", "opacity"), ("Dial", "highlight")}
        and {k[:2] for k in fixture.get("ShotB", {})}
        == {("Lamp", "Visibility"), ("Lamp", "opacity")},
        {t: sorted(k[:2] for k in c) for t, c in fixture.items()},
    )
    probe = list(range(1, 61, 3))
    want_hidden = [f for f in probe if 10 <= f < 20 or 40 <= f < 50]
    # The premise, pinned: the stock importer drops every one of them.
    fresh()
    bpy.ops.import_scene.fbx(filepath=dropped_fbx, anim_offset=0.0)
    stock = {
        "curves": sorted(
            (o.name, fc.data_path)
            for o in bpy.data.objects
            for a in [o.animation_data.action if o.animation_data else None]
            for fc in (action_fcurves(a) if a else ())
            if fc.data_path
            in ('["opacity"]', '["highlight"]', "hide_render", "hide_viewport")
        ),
        "opacity": bpy.data.objects["Lamp"].get("opacity"),
        "hide": (
            bpy.data.objects["Lamp"].hide_render,
            bpy.data.objects["Lamp"].hide_viewport,
        ),
        "unkeyed": [n for n in ("Bulb", "Dial") if played(n) is None],
    }
    check(
        "the stock importer drops Visibility and the user-property curves",
        stock["curves"] == [] and stock["opacity"] is not None,
        stock,
    )
    check(
        "the fixture: the child and the property-only object key no transform",
        stock["unkeyed"] == ["Bulb", "Dial"],
        stock,
    )
    fresh()
    _created, logged = import_logged(dropped_fbx)
    lamp, bulb, dial = (bpy.data.objects[n] for n in ("Lamp", "Bulb", "Dial"))
    check(
        "dropped channels: the opacity curve plays, both shots' keys, each "
        "key's own interpolation",
        fcurve_keys(lamp, '["opacity"]')
        == [
            (1.0, 0.0, "LINEAR"),
            (15.0, 1.0, "LINEAR"),
            (30.0, 1.0, "LINEAR"),
            (31.0, 1.0, "CONSTANT"),
            (45.0, 0.0, "LINEAR"),
            (60.0, 0.0, "LINEAR"),
        ],
        fcurve_keys(lamp, '["opacity"]'),
    )
    check(
        "dropped channels: Visibility plays as stepped hide_render + hide_viewport",
        hidden_at(lamp, probe)
        == {"hide_render": want_hidden, "hide_viewport": want_hidden}
        and {i for _f, _v, i in fcurve_keys(lamp, "hide_render")} == {"CONSTANT"},
        f"{hidden_at(lamp, probe)} {fcurve_keys(lamp, 'hide_render')}",
    )
    check(
        "dropped channels: the child of a hidden node is hidden with it",
        hidden_at(bulb, probe)
        == {"hide_render": want_hidden, "hide_viewport": want_hidden},
        hidden_at(bulb, probe),
    )
    check(
        "dropped channels: an object keying only a custom property plays it",
        fcurve_keys(dial, '["highlight"]')
        == [(5.0, 0.0, "BEZIER"), (10.0, 1.0, "BEZIER")],
        fcurve_keys(dial, '["highlight"]'),
    )
    check(
        "dropped channels: one action per object, named for the take it plays",
        importer_actions() == ["Bulb|Take 001", "Dial|Take 001", "Lamp|Take 001"]
        and all(
            played(n) is not None and played(n).name == f"{n}|Take 001"
            for n in ("Lamp", "Bulb", "Dial")
        ),
        importer_actions(),
    )
    check(
        "dropped channels: static values stay as imported",
        abs(lamp["opacity"] - stock["opacity"]) < 1e-6
        and (lamp.hide_render, lamp.hide_viewport) == stock["hide"],
        f"{lamp['opacity']} {(lamp.hide_render, lamp.hide_viewport)} vs {stock}",
    )
    info = [m for lvl, m in logged if lvl == "INFO" and "drops" in m]
    check(
        "dropped channels: one INFO line says what landed",
        len(info) == 1
        and "Visibility on 1 object(s), inherited by 1 more" in info[0]
        and "custom-property curve(s)" in info[0],
        logged,
    )
    # A caller that carries them itself (the bridge) lands none.
    fresh()
    import_logged(dropped_fbx, channels=())
    check(
        "channels=(): nothing the importer drops is landed",
        not fcurve_keys(bpy.data.objects["Lamp"], '["opacity"]')
        and not hidden_at(bpy.data.objects["Lamp"], probe)
        and played("Bulb") is None
        and played("Dial") is None,
        importer_actions(),
    )
    try:
        FbxUtils.import_fbx(dropped_fbx, channels=("visibility", "opacity"))
        check("an unknown channel family raises before importing", False)
    except ValueError:
        check("an unknown channel family raises before importing", True)
    # A keyed enum the importer made a string (use_custom_props_enum_as_string,
    # its default) is no channel: landed as a float, the static value put back
    # after keying left its curve on a string property. Added: 2026-10-19
    fresh()
    switch = bpy.data.objects.new("Switch", None)
    bpy.context.scene.collection.objects.link(switch)
    switch["space"], switch["opacity"] = "World", 1.0
    _vis, _props = FbxUtils._take_channels(
        [
            {
                ("Switch", "space", "d|space"): [[0, 1.0, "step"]],
                ("Switch", "opacity", "d|opacity"): [[0, 0.5, "linear"]],
            }
        ],
        lambda target: switch if target == "Switch" else None,
        1.0,
        0.0,
        set(),
        {},
    )
    check(
        "a property the importer made a string (an enum) lands no curve",
        sorted(_props.get(switch, {})) == ["opacity"],
        _props,
    )

    # A clip library: the take that plays keys no custom property, a stashed
    # alternative does -- its curve rides its Key Stash clip, and the playing
    # action gains none.
    fresh()
    clip_obj = bpy.data.objects.new("Clip", None)
    bpy.context.scene.collection.objects.link(clip_obj)
    clip_obj["opacity"] = 0.25
    for act_name, rate in (("Walk", 0.1), ("Run", 0.3)):
        act = bpy.data.actions.new(act_name)
        clip_obj.animation_data_create()
        clip_obj.animation_data.action = act
        for f in range(1, 21):
            clip_obj.location.x = f * rate
            clip_obj.keyframe_insert("location", index=0, frame=f)
    clip_obj.select_set(True)
    clips_fbx = os.path.join(tmp, "dropped_channels_library.fbx")
    bpy.ops.export_scene.fbx(
        filepath=clips_fbx,
        use_selection=True,
        object_types={"EMPTY"},
        use_custom_props=True,
        bake_anim=True,
        bake_anim_use_all_actions=True,
        bake_anim_use_nla_strips=False,
    )
    clips_layout = ptk.FbxFile.load(clips_fbx).take_layout()
    plays, alt = clips_layout["live"][:1], clips_layout["stash"][:1]
    if alt:
        add_property_curves(
            clips_fbx,
            {alt[0]: {("Clip", "opacity"): [(1, 1.0, LIN), (20, 0.0, LIN)]}},
        )
    fresh()
    import_logged(clips_fbx)
    clip_obj = bpy.data.objects["Clip"]
    stashed = {c.label: c for c in KeyStash.active().clips}
    rec = next(
        (
            r
            for r in (stashed[alt[0]].curves if alt and alt[0] in stashed else ())
            if r["data_path"] == '["opacity"]'
        ),
        None,
    )
    parked_keys = []
    parked_act = bpy.data.actions.get(rec["action"]) if rec else None
    for fc in action_fcurves(parked_act) if parked_act else ():
        if fc.data_path == '["opacity"]':
            parked_keys = [(round(k.co.x, 3), k.co.y) for k in fc.keyframe_points]
    played_opacity = fcurve_keys(clip_obj, '["opacity"]')
    check(
        "a stashed take's custom-property curve rides its clip; the take that "
        "plays gains none",
        plays
        and alt
        and parked_keys == [(1.0, 1.0), (20.0, 0.0)]
        and not played_opacity
        and abs(clip_obj["opacity"] - 0.25) < 1e-6,
        f"layout={clips_layout['kind']} {plays} {alt} parked={parked_keys} "
        f"played={played_opacity} static={clip_obj.get('opacity')}",
    )
    reset_stash()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30

    # ---- skins beside their armatures keep every motion (2026-10-11) --------
    # A Maya payload leaves each skeleton at the scene root beside the mesh it
    # deforms. Blender's importer binds such a mesh UNDER its armature at the
    # FBX bind matrix and, by design, reads no animation on it -- a skinned
    # mesh's own node transform is ignored by FBX and glTF skinning too (on the
    # production assembly those channels were constant). What must not be lost
    # is the skin's MOTION: every vertex, at every frame of every shot, where
    # the skeleton puts it. Stock, half the sibling skins stayed unbound (no
    # Armature modifier, frozen) and one shot played (SIBLING_ARMATURES + the
    # take merge); measured on this fixture: 2.0 m and 1.54 m off.
    import numpy as _np

    def skin_scene(count=4):
        fresh()
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 30
        objects = []
        for i in range(count):
            data = bpy.data.armatures.new(f"Rig_{i}")
            arm = bpy.data.objects.new(f"Rig_{i}", data)
            bpy.context.scene.collection.objects.link(arm)
            arm.location.x = i * 3.0
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
            root = data.edit_bones.new("root")
            root.head, root.tail = (0, 0, 0), (0, 0, 0.5)
            tip = data.edit_bones.new("tip")
            tip.head, tip.tail = (0, 0, 0.5), (0, 0, 1)
            tip.parent = root
            bpy.ops.object.mode_set(mode="OBJECT")
            for frame, travel, bend in ((1, 0.0, 0.0), (30, 2.0, 0.8)):
                arm.pose.bones["root"].location.y = travel
                arm.pose.bones["root"].keyframe_insert("location", index=1, frame=frame)
                arm.pose.bones["tip"].rotation_mode = "XYZ"
                arm.pose.bones["tip"].rotation_euler.x = bend
                arm.pose.bones["tip"].keyframe_insert(
                    "rotation_euler", index=0, frame=frame
                )
            bpy.ops.mesh.primitive_cylinder_add(
                vertices=8, radius=0.2, depth=1.0, location=(i * 3.0, 0, 0.5)
            )
            skin = bpy.context.active_object
            skin.name = f"Loom_{i}"  # sorts ahead of its rig: the Maya root order
            lower, upper = (
                skin.vertex_groups.new(name="root"),
                skin.vertex_groups.new(name="tip"),
            )
            for v in skin.data.vertices:
                group = lower if (skin.matrix_world @ v.co).z < 0.5 else upper
                group.add([v.index], 1.0, "REPLACE")
            skin.modifiers.new("Armature", "ARMATURE").object = arm
            objects += [arm, skin]
        return objects

    skin_frames = (1, 5, 10, 20, 25, 30)

    def skin_positions(names):
        found = {}
        for frame in skin_frames:
            bpy.context.scene.frame_set(frame)
            graph = bpy.context.evaluated_depsgraph_get()
            for name in names:
                evaluated = bpy.data.objects[name].evaluated_get(graph)
                mesh = evaluated.to_mesh()
                world = _np.array(evaluated.matrix_world)
                co = _np.array([v.co[:] for v in mesh.vertices])
                found[(name, frame)] = co @ world[:3, :3].T + world[:3, 3]
                evaluated.to_mesh_clear()
        return found

    skin_objects = skin_scene()
    skins = [o.name for o in skin_objects if o.type == "MESH"]
    skin_truth = skin_positions(skins)
    skin_fbx = os.path.join(tmp, "sibling_skins.fbx")
    FbxUtils.apply_takes([("a", 1, 10), ("b", 20, 30)])
    try:
        FbxUtils.export(
            filepath=skin_fbx,
            objects=skin_objects,
            bake_anim=True,
            object_types={"MESH", "ARMATURE", "EMPTY"},
        )
    finally:
        FbxUtils.reset_takes()
    ptk.FbxMedia.drop_takes(skin_fbx, names=["Scene"])  # Shots Only
    fresh()
    FbxUtils.import_fbx(skin_fbx)
    bound = [
        n
        for n in skins
        if n in bpy.data.objects
        and any(
            m.type == "ARMATURE" and m.object for m in bpy.data.objects[n].modifiers
        )
    ]
    landed = skin_positions([n for n in skins if n in bpy.data.objects])
    skin_error = {
        n: max(
            float(_np.abs(landed[(n, f)] - skin_truth[(n, f)]).max())
            if (n, f) in landed and landed[(n, f)].shape == skin_truth[(n, f)].shape
            else float("inf")
            for f in skin_frames
        )
        for n in skins
    }
    check(
        "sibling skins: every skin binds to its armature",
        bound == skins,
        f"{bound} of {skins}",
    )
    check(
        "sibling skins: every vertex follows its skeleton through both shots",
        all(err < 1e-4 for err in skin_error.values()),
        {n: round(e, 5) for n, e in skin_error.items()},
    )
    fresh()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30

    # ---- upstream patches: still needed, and still working ------------------
    from blendertk.env_utils.upstream_patches import SIBLING_ARMATURES, _armature_tree

    # The sweep. Every patch declares a probe that reproduces the defect against
    # the STOCK code, so the release that fixes one upstream makes this fail and
    # names the patch to delete -- which is the only reason a monkey patch here
    # ever gets removed.
    for patch in ptk.UpstreamPatch.registry():
        check(
            f"upstream patch still needed: {patch.name}",
            patch.available and patch.still_needed(),
            f"{patch.target} -- if upstream fixed this, DELETE the patch and this "
            "check goes with it",
        )

    # ...and the replacement actually corrects it, on the same fixture the probe
    # measured, so the two can never drift apart.
    from io_scene_fbx.import_fbx import FbxImportHelperNode as _helper

    stock = vars(_helper)["collect_armature_meshes"]
    root, armatures = _armature_tree()
    with SIBLING_ARMATURES.applied() as active:
        root.collect_armature_meshes()
    bound = [a.fbx_name for a in armatures if a.meshes]
    check("the patch applied at all", active)
    check("it binds every sibling armature", len(bound) == 7, f"bound {bound}")
    # The very object that was there, not its ``__module__``: ``functools.wraps``
    # copies that onto the replacement, so a patch leaked into the whole process
    # still read as upstream's.
    check(
        "and is removed again on the way out",
        vars(_helper)["collect_armature_meshes"] is stock,
        repr(vars(_helper)["collect_armature_meshes"]),
    )

    # A mesh already under its armature is Blender's own layout, which the defect
    # cannot reach; it must still bind, patched or not.
    root, armatures = _armature_tree(count=1)
    nested = armatures[0]
    for mesh in [c for c in root.children if c.fbx_name == "mesh0"]:
        mesh.parent = nested
    with SIBLING_ARMATURES.applied():
        root.collect_armature_meshes()
    check("a mesh already under its armature still binds", bool(nested.meshes))

    # ---- every record is in the units the geometry is written in (2026-10-17) --
    # Mirror of mayatk's TestRecordUnits. bpy answers every length in Blender
    # units (BU) and converts nothing; the Scene Exporter sets the scene's
    # units before it runs the producers (``set_linear_unit``) and the FBX
    # write scales the geometry by them (``apply_unit_scale``): a BU is written
    # as ``scale_length`` metres, or one metre under a unit system of None
    # (io_scene_fbx ``units_blender_to_fbx_factor``). So a record ships its
    # lengths in BU -- the file's own units -- with ``unit_scale`` the metres
    # per BU in effect when it is PRODUCED, and lengths x unit_scale follow the
    # geometry. Each measured producer's fixture is authored under each scale
    # and produced under each setting, whatever scale the tool ran under (the
    # probe kept the bake's: a centimetre bake exported in metres labelled its
    # probe 100x too near). The articulation record carries no unit_scale: its
    # runtime measures its scale against the rest pose
    # (``ptk.ArticulationModel.scale_of``), so it ships the same BU always.
    import json as _ru_json

    from blendertk.env_utils._env_utils import EnvUtils
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.node_utils.data_nodes import DataNodes as _RuDataNodes
    from blendertk.rig_utils.articulated_rig import ArticulatedRig
    from blendertk.rig_utils.shadow_rig import ShadowRig

    #: The producers whose records carry no length, and what they carry
    #: instead. A new producer belongs here or in ``ru_fixtures``.
    RU_UNITLESS = {
        ptk.SceneRecords.SHOTS.key: "frames, with their fps",
        ptk.SceneRecords.VISIBILITY.key: "frames with their fps, and colours",
        ptk.SceneRecords.AUDIO.key: "frames, with their fps",
        ptk.SceneRecords.EMISSIVE_GROUPS.key: "weights",
        ptk.SceneRecords.MATERIALS.key: "factors and file names",
    }
    #: The scale each fixture is authored under.
    RU_AUTHORED = (1.0, 0.01)
    #: ``(unit system, scale_length) -> metres per BU`` the FBX write uses.
    RU_PRODUCED = {("METRIC", 1.0): 1.0, ("METRIC", 0.01): 0.01, ("NONE", 0.01): 1.0}
    ru_store = ptk.TempArtifacts("btk_record_units", policy="session")
    ru_dir = ru_store.dir_path()
    ru_pin = _RuDataNodes._session_root
    EnvUtils.set_current_workspace(ru_dir)  # the shadow rig writes its maps there

    def ru_file(name):
        """An empty stand-in for a baked map: the records name it, nothing reads it."""
        path = os.path.join(ru_dir, name)
        open(path, "wb").close()
        return path

    def ru_cube(name, location, size=2.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    def ru_lightmaps():
        """A marked bench, and a probe recorded at a point and a box, in BU."""
        bench = ru_cube("Bench", (0.0, 0.0, 1.0))
        btk.UvUtils.create_lightmap_uvs([bench], quiet=True)
        LightmapRecords.commit({bench.name: ru_file("Bench_Lightmap.exr")})
        LightmapRecords.commit_probe(
            ru_file("Bench_Probe.exr"),
            (0.0, 1.2, 0.5),
            [(-2.0, 0.0, -1.0), (2.0, 2.5, 1.5)],
        )

    def ru_lightmap_metres(payload, factor=None):
        """*payload* in metres: its lengths times *factor* (its one unit_scale
        when None -- version 2 states it once, for the probe and the lights),
        labelled 1."""
        probe = payload["probe"]
        s = payload["unit_scale"] if factor is None else factor
        probe["position"] = [c * s for c in probe["position"]]
        probe["box"] = {k: [c * s for c in v] for k, v in probe["box"].items()}
        payload["unit_scale"] = 1.0
        return payload

    def ru_shadows():
        """A horizon shadow rig on a box, its ground raised off the world's."""
        box = ru_cube("Box", (0.0, 0.0, 1.5))
        bpy.context.view_layer.update()
        ShadowRig.create(
            [box],
            light_pos=(5.0, 5.0, 10.0),
            texture_res=32,
            ground_height=0.25,
            rig_type="horizon",
            horizon_size=32,
            horizon_spans=2,
        )

    def ru_shadow_metres(payload, factor=None):
        s = payload["unit_scale"] if factor is None else factor
        for plane in payload["planes"]:
            for key in ("source_size", "ground", "radius", "height"):
                plane[key] *= s
            horizon = plane.get("horizon")
            if horizon:
                horizon["bounds"] = [c * s for c in horizon["bounds"]]
                horizon["height_scale"] *= s
        payload["unit_scale"] = 1.0
        return payload

    def ru_articulation():
        """A hinge and a slide, a limit typed on each (degrees, BU), then each
        max taken from a pose."""
        names = []
        for i, x in enumerate((0.0, 4.0, 8.0)):
            names.append(ru_cube(f"RU{i}", (x, 0.0, 0.0), size=1.5).name)
        joints = [
            {
                "link": i,
                "parent": i - 1,
                "type": kind,
                "position": [4.0 * i - 2.0, 0.0, 0.0],
                "aim": [1.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "limits": limits,
                "size": 1.0,
            }
            for i, kind, limits in (
                (1, "hinge", {"rz": [-30.0, 45.0]}),
                (2, "slide", {"tx": [-1.0, 2.0]}),
            )
        ]
        rig = ArticulatedRig.create(
            [[n] for n in names], joints, name="units", end_control=False
        )
        hinge, slide = (j["id"] for j in rig.spec["joints"])
        rig.set_state([20.0, 1.5], key=False)  # model order: degrees, BU
        rig.set_limit_from_pose(hinge, "rz", "max")
        rig.set_limit_from_pose(slide, "tx", "max")

    #: key -> (fixture, to-metres of a labelled record, or None for a record
    #: that ships BU unlabelled -- the same numbers under every setting).
    ru_fixtures = {
        ptk.SceneRecords.LIGHTMAPS.key: (ru_lightmaps, ru_lightmap_metres),
        ptk.SceneRecords.SHADOWS.key: (ru_shadows, ru_shadow_metres),
        ptk.SceneRecords.ARTICULATION.key: (ru_articulation, None),
    }

    def ru_differences(want, got, where, out):
        """Every place *got* differs from *want* (floats to the records' 6 places)."""
        if isinstance(want, dict):
            if not isinstance(got, dict) or sorted(want) != sorted(got):
                out.append(f"{where}: keys {sorted(want)} vs {got!r}")
                return out
            for k in want:
                ru_differences(want[k], got[k], f"{where}.{k}", out)
        elif isinstance(want, list):
            if not isinstance(got, list) or len(want) != len(got):
                out.append(f"{where}: {want!r} vs {got!r}")
                return out
            for i, (a, b) in enumerate(zip(want, got)):
                ru_differences(a, b, f"{where}[{i}]", out)
        elif isinstance(want, float) or isinstance(got, float):
            if not isinstance(got, (int, float)) or abs(float(want) - float(got)) > (
                2e-6 + 1e-6 * abs(float(want))
            ):
                out.append(f"{where}: {want} vs {got}")
        elif want != got:
            out.append(f"{where}: {want!r} vs {got!r}")
        return out

    def ru_unit_scales(payload, where="", out=None):
        """``(where, unit_scale)`` of every ``unit_scale`` in *payload*."""
        out = [] if out is None else out
        if isinstance(payload, dict):
            if "unit_scale" in payload:
                out.append((where or "payload", payload["unit_scale"]))
            for k, v in payload.items():
                ru_unit_scales(v, f"{where}.{k}", out)
        elif isinstance(payload, list):
            for i, v in enumerate(payload):
                ru_unit_scales(v, f"{where}[{i}]", out)
        return out

    def ru_produce(key):
        """``{(authored scale, (system, produced scale)): payload}`` for *key*."""
        fixture, _metres = ru_fixtures[key]
        payloads = {}
        for authored in RU_AUTHORED:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.context.scene.unit_settings.scale_length = authored
            fixture()
            for setting in RU_PRODUCED:
                units = bpy.context.scene.unit_settings
                units.system, units.scale_length = setting
                (producer,) = FbxUtils.producers([key]).values()
                record = producer(FbxUtils.export_context())
                payloads[(authored, setting)] = (
                    _ru_json.loads(record.text) if record is not None else None
                )
        return payloads

    def ru_copy(payload):
        return _ru_json.loads(_ru_json.dumps(payload))

    check(
        "record units: every producer is measured or declared unitless",
        {spec.key for spec in FbxUtils.PRODUCERS}
        == set(RU_UNITLESS) | set(ru_fixtures),
        sorted({spec.key for spec in FbxUtils.PRODUCERS}),
    )
    ru_payloads = {}
    for ru_key, (_fixture, ru_metres) in ru_fixtures.items():
        try:
            payloads = ru_payloads[ru_key] = ru_produce(ru_key)
        except Exception:  # noqa: BLE001 -- one fixture's crash is its own FAIL
            check(f"record units: {ru_key} fixture runs", False, traceback.format_exc())
            continue
        reference = payloads[(1.0, ("METRIC", 1.0))]  # a BU is a metre: BU == m
        if reference is None or None in payloads.values():
            check(f"record units: {ru_key} fixture made a record", False, payloads)
            continue
        wrong, labels = [], []
        for (authored, setting), payload in payloads.items():
            where = f"authored {authored}, produced {setting}"
            if ru_metres is None:
                ru_differences(reference, payload, where, wrong)
                labels += [(where, u) for u in ru_unit_scales(payload)]
                continue
            factor = RU_PRODUCED[setting]
            ru_differences(
                ru_metres(ru_copy(reference), factor),
                ru_metres(ru_copy(payload)),
                where,
                wrong,
            )
            labels += [
                (where, u)
                for u in ru_unit_scales(payload)
                if abs(u[1] - factor) > 1e-6 * max(1.0, factor)
            ]
        if ru_metres is None:
            check(
                f"record units: {ru_key} ships the same BU whatever the units, unlabelled",
                not wrong and not labels,
                "; ".join(wrong[:6]) or labels[:4],
            )
            continue
        check(
            f"record units: {ru_key} lengths x unit_scale follow the geometry",
            not wrong,
            "; ".join(wrong[:6]),
        )
        check(
            f"record units: {ru_key} unit_scale is the metres per BU the write uses",
            not labels,
            labels[:4],
        )

    # Anchors: under a scale of 1 a record's lengths ARE the BU the tool used.
    ru_probe = (ru_payloads.get(ptk.SceneRecords.LIGHTMAPS.key) or {}).get(
        (0.01, ("METRIC", 1.0))
    ) or {}
    check(
        "record units: a centimetre bake's probe exported in metres stands where it was baked",
        not ru_differences(
            {
                "position": [0.0, 1.2, 0.5],
                "box": {"min": [-2.0, 0.0, -1.0], "max": [2.0, 2.5, 1.5]},
                "unit_scale": 1.0,
            },
            {
                "position": (ru_probe.get("probe") or {}).get("position"),
                "box": (ru_probe.get("probe") or {}).get("box"),
                "unit_scale": ru_probe.get("unit_scale"),
            },
            "probe",
            [],
        ),
        ru_probe,
    )
    ru_rig = (ru_payloads.get(ptk.SceneRecords.ARTICULATION.key) or {}).get(
        (1.0, ("METRIC", 1.0))
    ) or {"rigs": [{"joints": []}]}
    ru_limits = {
        c["channel"]: [c["min"], c["max"]]
        for j in ru_rig["rigs"][0]["joints"]
        for c in j["channels"]
    }
    check(
        "record units: a limit typed and one taken from a pose ship as set (degrees, BU)",
        not ru_differences({"rz": [-30.0, 20.0], "tx": [-1.0, 1.5]}, ru_limits, "limits", []),
        ru_limits,
    )

    # ---- every record a producer makes is one its declaration reads --------
    # The shared reader gate (``MeshConvert._current_record``) refuses what a
    # record's declared shape refuses, so a producer drifting from its shape
    # ships a record nothing applies -- which a test pinning a copy of the
    # producer's own dict never sees. Every producer runs, through the
    # pipeline's own publish, on one small scene that gives each something to
    # say, and each record -- the handoff the commit stamps too -- is checked
    # against its declaration (``ptk.SceneRecords.problems``). Mirror of
    # mayatk's. Added: 2026-10-20
    import wave as _ev_wave

    from blendertk.anim_utils.shots._shots import BlenderShotStore
    from blendertk.audio_utils._audio_utils import AudioUtils as _EvAudio
    from blendertk.mat_utils._mat_utils import MatUtils as _EvMatUtils
    from blendertk.mat_utils.emissive_groups import EmissiveGroups
    from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 40
    ru_lightmaps()
    ru_shadows()
    ru_articulation()
    ev_cube = ru_cube("EvCube", (0.0, 6.0, 1.0))
    ev_mat = bpy.data.materials.new("EvMat")
    _EvMatUtils.get_node_tree(ev_mat, create=True)
    ev_cube.data.materials.append(ev_mat)
    BlenderShotStore.clear_active()
    BlenderShotStore.active().define_shot("Intro", 1, 20, objects=[ev_cube.name])
    RenderEffects.create([ev_cube])
    RenderEffects.key_fade([ev_cube], start=1, end=11, direction="out")
    EmissiveGroups.add_group("glow", {ev_cube.name: [0]})
    ev_wav = os.path.join(ru_dir, "ev_tone.wav")
    with _ev_wave.open(ev_wav, "wb") as ev_file:
        ev_file.setnchannels(1)
        ev_file.setsampwidth(2)
        ev_file.setframerate(8000)
        ev_file.writeframes(b"\x00\x00" * 800)
    _EvAudio.add_clip(ev_wav, frame_start=5, name="tone")

    # A field a record's shape no longer declares is pruned as the record
    # is made, with a warning (``RecordSpec.make``): what a producer still
    # writes ships nothing, but says so on every export. Added: 2026-10-20
    import logging as _ev_logging
    import warnings as _ev_warnings

    class _EvPruned(_ev_logging.Handler):
        """The shape-pruning warnings a publish logs."""

        def __init__(self):
            super().__init__(_ev_logging.WARNING)
            self.messages = []

        def emit(self, record):
            message = record.getMessage()
            if "left out what its shape does not declare" in message:
                self.messages.append(message)

    ev_pruned = _EvPruned()
    ev_log = _ev_logging.getLogger(ptk.SceneRecords.__module__)
    ev_log.addHandler(ev_pruned)
    try:
        with _ev_warnings.catch_warnings(record=True) as ev_warned:
            _ev_warnings.simplefilter("always")
            ev_snapshot = FbxUtils.publish()
        ev_log.removeHandler(ev_pruned)
        ev_retired = sorted(
            {
                str(w.message)
                for w in ev_warned
                if issubclass(w.category, DeprecationWarning)
            }
        )
        check(
            "...and no producer writes a field its record's shape does not "
            "declare (nothing pruned) or calls a retired API",
            not ev_pruned.messages and not ev_retired,
            {"pruned": ev_pruned.messages, "retired": ev_retired},
        )
        ev_made = {k for k, r in ev_snapshot.produced.items() if r is not None}
        check(
            "every record: each producer made one, on a scene giving each something to say",
            ev_made >= {spec.key for spec in FbxUtils.PRODUCERS}
            and not ev_snapshot.failed,
            f"made {sorted(ev_made)}; failed {sorted(ev_snapshot.failed)}",
        )
        ev_problems = {
            key: ptk.SceneRecords.problems(record.spec, record.payload)
            for key, record in ev_snapshot.produced.items()
            if record is not None
        }
        ev_problems[ptk.SceneRecords.HANDOFF.key] = ptk.SceneRecords.problems(
            ptk.SceneRecords.HANDOFF, ptk.SceneRecords.HANDOFF.load(_RuDataNodes)
        )
        check(
            "...and each, the handoff the commit stamps too, is one its declaration reads",
            not any(ev_problems.values()),
            {k: v for k, v in ev_problems.items() if v},
        )
    finally:
        ev_log.removeHandler(ev_pruned)
        _EvAudio.remove_all_clips()
        BlenderShotStore.clear_active()

    EnvUtils.set_current_workspace(ru_pin)
    ru_store.cleanup()

    # ---- finish_deliverable: a scene that cannot say where its files are
    # still gets the split, the copies and the drop, which need none of them
    # (mirror of mayatk). Added: 2026-10-19 --------------------------------
    from unittest import mock as _fd_mock

    fd_path = os.path.join(tmp, "finish.fbx")
    with open(fd_path, "wb") as fh:
        fh.write(b"Kaydara FBX Binary  \x00\x1a\x00")
    fd_seen = {}

    def _fd_finish(fbx, glb=None, **kwargs):
        fd_seen.update(kwargs, fbx=fbx)
        return {}

    with _fd_mock.patch.object(
        FbxUtils, "_dependency_inputs", side_effect=RuntimeError("no scene")
    ), _fd_mock.patch.object(
        ptk.MeshConvert, "finish_fbx_deliverable", side_effect=_fd_finish
    ):
        fd_report = FbxUtils.finish_deliverable(fd_path, search_dirs=["D:/maps"])
    check(
        "finish_deliverable: a scene whose files are not resolved still finishes",
        fd_report == {}
        and fd_seen.get("fbx") == fd_path
        and (fd_seen.get("search_dirs"), fd_seen.get("walk_root"), fd_seen.get("files"))
        == (["D:/maps"], "", ()),
        fd_seen,
    )

    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
