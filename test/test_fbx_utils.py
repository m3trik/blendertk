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
    # conversion binds ``lightmap_metadata``) has to ship the carrier Empty AND the
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
    # declared in _fbx_options -- a subclass overriding that method wholesale (the
    # Substance/Marmoset bridges do) must not be able to ship a carrier holding
    # nothing. Driven through a stubbed exporter so it pins the real call.
    _seen_opts = {}

    class _CarrierProbe(WebXrPreview):
        def _fbx_options(self, params):  # a hostile override: neither half present
            # `object_types` deliberately a BARE STRING -- the preset-sourced shape
            # that set("MESH") explodes into {'M','E','S','H'}. Pins that the carrier
            # union goes through the shared coercion.
            return dict(object_types="MESH", use_custom_props=False)

    _real_export = btk.FbxUtils.export_selection_fbx
    try:
        btk.FbxUtils.export_selection_fbx = (
            lambda filepath=None, objects=None, **o: _seen_opts.update(o) or filepath
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

    DataNodes.set_export_string("lightmap_metadata", '{"version": 1}')
    bpy.ops.mesh.primitive_cube_add()
    lit = bpy.context.active_object
    lit.name = "LitMesh"

    _seen_opts.clear()
    _real_export = btk.FbxUtils.export_selection_fbx
    try:
        btk.FbxUtils.export_selection_fbx = (
            lambda filepath=None, objects=None, **o: _seen_opts.update(o) or filepath
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
        shipped is not None
        and shipped.get("lightmap_metadata") == '{"version": 1}',
        f"{shipped and dict(shipped.items())}",
    )

    # ---- declared takes reach ANY animated hand-off, not just the exporter ----
    # Mirror of mayatk's check of the same contract. The take split lived only in
    # the Scene Exporter's task, so a preview push of a shot-carrying scene came
    # back with one whole-timeline take -- two writers of the same deliverable
    # disagreeing about whether shots survive. Armed takes are sticky, so the
    # reset matters as much as the arm: left armed they would split the next
    # export nobody asked to split.
    DataNodes.set_export_string(
        DataNodes.FBX_TAKES, '[{"name": "Shot_1", "start": 1, "end": 10}]'
    )
    for include_animation, expect_armed in ((True, 1), (False, 0)):
        _armed = {}
        _real_export = btk.FbxUtils.export_selection_fbx
        try:
            btk.FbxUtils.export_selection_fbx = (
                lambda filepath=None, objects=None, **o: _armed.update(
                    during=btk.FbxUtils._pending_takes
                )
                or filepath
            )
            WebXrPreview()._export_fbx(
                [lit],
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
    DataNodes.set_export_string(DataNodes.FBX_TAKES, "")

    # ---- data_internal must never ride a hand-off -----------------------------
    # In Maya the guarantee is structural (network node); here the carrier is a
    # plain Empty a whole-scene send would sweep in — with use_custom_props forced
    # on by include_data_export, it would ship SmartBake/emissive state as user
    # properties. The hierarchy closure (every bridge path) drops it by name.
    reset()
    DataNodes.set_internal_string("smart_bake_sessions", "[]")
    bpy.ops.mesh.primitive_cube_add()
    _mesh = bpy.context.active_object
    _mesh.name = "HandoffMesh"
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
        n_armed == 2 and FbxUtils._pending_takes == [("shotA", 1, 10), ("shotB", 20, 30)],
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
    FbxUtils.import_fbx(takes_out)

    def action_fcurves(act):  # Blender 5.1 slotted-action walk
        layers = getattr(act, "layers", None)
        if not layers:
            yield from getattr(act, "fcurves", None) or ()
            return
        for layer in layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    yield from cb.fcurves

    take_actions = sorted(a.name for a in bpy.data.actions)
    check(
        "the FBX ships one AnimStack per declared take and ONLY those",
        take_actions
        == [
            "TakesBall|shotA",
            "TakesBall|shotB",
            "TakesCube|shotA",
            "TakesCube|shotB",
        ],
        f"{take_actions}",
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

    # ---- apply_takes_from_node reads the carrier's fbx_takes channel --------
    reset()
    check(
        "apply_takes_from_node -> 0 with no carrier in the scene",
        FbxUtils.apply_takes_from_node() == 0,
    )
    DataNodes.set_export_json(
        DataNodes.FBX_TAKES,
        [{"name": "intro", "start": 1, "end": 12}],
    )
    n_node = FbxUtils.apply_takes_from_node()
    check(
        "apply_takes_from_node arms the declared channel",
        n_node == 1 and FbxUtils._pending_takes == [("intro", 1, 12)],
        f"{FbxUtils._pending_takes}",
    )
    FbxUtils.reset_takes()

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

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
