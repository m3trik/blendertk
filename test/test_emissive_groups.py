"""Headless suite for blendertk's Emissive Groups (``mat_utils/emissive_groups.py``):
pythontk's model over the Blender half, the mirror of mayatk's
``test_emissive_groups.py``. A group is materials: its faces wear a copy of
their material made for the group (its images shared), its emission strength
keyed by the group's weight; an export stages each animated copy's weight on
its ``<material>__emission`` proxy, read back with ``ptk.FbxFile`` and the GLB.

Run: blender --background --factory-startup --python blendertk/test/test_emissive_groups.py
"""

import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy

    import pythontk as ptk
    import blendertk as btk
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.mat_utils.emissive_groups import EmissiveGroups

    DataNodes = btk.DataNodes
    HOST = EmissiveGroups.HOST

    def material(name, color=None, image=None, strength=1.0):
        mat = bpy.data.materials.new(name)
        bsdf = next(n for n in MatUtils.get_node_tree(mat, create=True).nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Emission Strength"].default_value = strength
        if image:
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.name = "glow"
            tex.image = bpy.data.images.new(image, 8, 8)
            mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
        elif color:
            bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        return mat

    def reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps, scene.render.fps_base = 24, 1.0
        scene.frame_start, scene.frame_end = 1, 48
        bpy.ops.mesh.primitive_cube_add()
        hull = bpy.context.active_object
        hull.name = "hull"
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        panel = bpy.context.active_object
        panel.name = "panel"
        hull.data.materials.append(material("hullMat", image="hullGlow", strength=2.0))
        panel.data.materials.append(material("panelMat", color=(0.2, 0.6, 1.0)))
        return hull, panel

    def wears(obj, index):
        return obj.material_slots[obj.data.polygons[index].material_index].material.name

    # --- a group is materials ---------------------------------------------
    hull, panel = reset()
    copies = EmissiveGroups.add_group("signal", {"hull": [0, 1], "panel": [0]}, weight=0.5)
    check("one copy per material and group", copies == ["hullMat_signal", "panelMat_signal"], copies)
    check(
        "the faces wear the copies, the rest their base",
        (wears(hull, 0), wears(hull, 2), wears(panel, 0)) == ("hullMat_signal", "hullMat", "panelMat_signal"),
        (wears(hull, 0), wears(hull, 2), wears(panel, 0)),
    )
    _copy = bpy.data.materials["hullMat_signal"]
    check(
        "the copy shares its base's image",
        _copy.node_tree.nodes["glow"].image == bpy.data.materials["hullMat"].node_tree.nodes["glow"].image,
    )
    check(
        "emission = the base's strength times the weight",
        (round(HOST.emission("hullMat_signal"), 6), round(HOST.emission("panelMat_signal"), 6)) == (1.0, 0.5),
        (HOST.emission("hullMat_signal"), HOST.emission("panelMat_signal")),
    )
    check(
        "the copy is tagged with its group, base and strength",
        HOST.copies()["hullMat_signal"] == {"group": "signal", "base": "hullMat", "strength": 2.0},
        HOST.copies().get("hullMat_signal"),
    )
    check(
        "the tags sit on the copy's node tree, which the FBX exporter never writes",
        not [k for k in _copy.keys() if k.startswith("emissiveGroup")]
        and _copy.node_tree.get("emissiveGroupBase") == bpy.data.materials["hullMat"],
        list(_copy.keys()),
    )

    # --- the viewport plays the keyed weight -------------------------------
    EmissiveGroups.key_weight("signal", 0.0, frame=1)
    EmissiveGroups.key_weight("signal", 1.0, frame=24)
    _fc = HOST._fcurve("hullMat_signal")
    check(
        "the copy's emission is keyed by the weight times its strength",
        _fc is not None and (round(_fc.evaluate(1), 4), round(_fc.evaluate(24), 4)) == (0.0, 2.0),
        _fc and [tuple(k.co) for k in _fc.keyframe_points],
    )
    check("the base is not keyed", HOST._fcurve("hullMat") is None and HOST.emission_keys("hullMat") == [])
    check("listed as keyed", EmissiveGroups.list_groups()["signal"]["keyed"], EmissiveGroups.list_groups())

    # --- in step with the base ---------------------------------------------
    hull, panel = reset()
    EmissiveGroups.add_group("signal", {"hull": [0]}, weight=0.5)
    _base = bpy.data.materials["hullMat"]
    _bsdf = next(n for n in _base.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    _bsdf.inputs["Roughness"].default_value = 0.9
    _base.node_tree.nodes["glow"].image = bpy.data.images.new("newGlow", 8, 8)
    _found = EmissiveGroups.sync(check_only=True)
    check("a check names the drift and changes nothing", len(_found) == 1 and "Roughness" in _found[0], _found)
    EmissiveGroups.sync()
    _copy = bpy.data.materials["hullMat_signal"]
    _cb = next(n for n in _copy.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    check(
        "a changed base value and map reach the copy on sync",
        round(_cb.inputs["Roughness"].default_value, 4) == 0.9 and _copy.node_tree.nodes["glow"].image.name == "newGlow",
    )
    check("the synced copy keeps its faces and weight", wears(hull, 0) == "hullMat_signal" and round(HOST.emission("hullMat_signal"), 6) == 1.0)
    check("in step after", EmissiveGroups.sync(check_only=True) == [], EmissiveGroups.sync(check_only=True))
    _bsdf.inputs["Emission Strength"].default_value = 4.0
    EmissiveGroups.sync()
    check(
        "a changed base strength rescales the copy, the weight kept",
        round(HOST.emission("hullMat_signal"), 6) == 2.0 and HOST.copies()["hullMat_signal"]["strength"] == 4.0,
    )

    # --- remove restores the base; slots re-index ---------------------------
    hull, panel = reset()
    EmissiveGroups.add_group("a", {"hull": [0]})
    EmissiveGroups.add_group("b", {"hull": [1, 2]})
    EmissiveGroups.remove_group("a")
    check(
        "removing restores the base, and the faces of the slot above keep theirs",
        [wears(hull, i) for i in range(4)] == ["hullMat", "hullMat_b", "hullMat_b", "hullMat"],
        [wears(hull, i) for i in range(4)],
    )
    check("the copy is gone with its slot", "hullMat_a" not in bpy.data.materials and len(hull.material_slots) == 2)
    EmissiveGroups.add_group("b", {"hull": [3]})
    EmissiveGroups.add_group("c", {"hull": [3]})
    check("a face moves between groups", wears(hull, 3) == "hullMat_c")

    # --- refusals ------------------------------------------------------------
    _plain = bpy.data.materials.new("plain")
    for _n in [n for n in MatUtils.get_node_tree(_plain, create=True).nodes if n.type == "BSDF_PRINCIPLED"]:
        _plain.node_tree.nodes.remove(_n)
    panel.data.materials.append(_plain)
    panel.data.polygons[5].material_index = len(panel.material_slots) - 1
    _refused = ""
    try:
        EmissiveGroups.add_group("x", {"panel": [4, 5]})
    except ValueError as exc:
        _refused = str(exc)
    check("a material with no emission to key is refused by name", "plain" in _refused and wears(panel, 4) == "panelMat", _refused)
    _refused = ""
    try:
        EmissiveGroups.add_group("unpicked", {})
    except ValueError as exc:
        _refused = str(exc)
    check("an empty face map is no faces, not the selection (as in Maya)", "No faces" in _refused, _refused)
    for _label, _setup in (
        ("a black emission colour", lambda bsdf, tree: setattr(bsdf.inputs["Emission Color"], "default_value", (0.0, 0.0, 0.0, 1.0))),
        ("a strength a link drives", lambda bsdf, tree: tree.links.new(tree.nodes.new("ShaderNodeValue").outputs[0], bsdf.inputs["Emission Strength"])),
    ):
        _odd = material(f"odd_{len(_label)}", color=(1.0, 1.0, 1.0))
        _tree = _odd.node_tree
        _setup(next(n for n in _tree.nodes if n.type == "BSDF_PRINCIPLED"), _tree)
        panel.data.materials.append(_odd)
        panel.data.polygons[2].material_index = len(panel.material_slots) - 1
        _refused = ""
        try:
            EmissiveGroups.add_group("y", {"panel": [2]})
        except ValueError as exc:
            _refused = str(exc)
        check(f"{_label} is refused by name", _odd.name in _refused, _refused)

    # --- an old scene converts in one action --------------------------------
    hull, panel = reset()
    for _store, _faces in (("emissiveGroup_headlights", {"hull": [0, 1, 2]}), ("emissiveGroup_signal", {"hull": [2, 3], "panel": [0, 1]})):
        for _name, _idx in _faces.items():
            _mesh = bpy.data.objects[_name].data
            _attr = _mesh.attributes.new(_store, "BOOLEAN", "FACE")
            for _i in _idx:
                _attr.data[_i].value = True
    hull.data.color_attributes.new(name="emissiveGroups", type="BYTE_COLOR", domain="CORNER")
    DataNodes.write(
        ptk.Scope.PRIVATE,
        "emissive_registry",
        json.dumps({"schema": 1, "encoding": "vertex-color", "groups": {"headlights": {"slot": 0, "default": 1.0}, "signal": {"slot": 1, "default": 0.5, "attr": "emissiveGroup_signal"}}, "retired_slots": []}),
    )
    DataNodes.write(ptk.Scope.DELIVERABLE, "emissive_groups", json.dumps({"version": 2, "groups": []}))
    _carrier = DataNodes.ensure_export()
    for _frame, _value in ((1, 0.0), (24, 1.0)):
        _carrier["emissiveGroup_signal"] = _value
        _carrier.keyframe_insert(data_path='["emissiveGroup_signal"]', frame=_frame)
    _report = EmissiveGroups.convert_legacy()
    check("face-level groups convert exactly", _report["converted"] == {"headlights": 3, "signal": 3}, _report)
    check(
        "the faces wear the groups' copies",
        [wears(hull, i) for i in range(4)] == ["hullMat_headlights"] * 3 + ["hullMat_signal"] and wears(panel, 1) == "panelMat_signal",
        [wears(hull, i) for i in range(4)],
    )
    _fc = HOST._fcurve("hullMat_signal")
    check("the old keyed weight keys the copies", _fc is not None and round(_fc.evaluate(24), 4) == 2.0, _fc and [tuple(k.co) for k in _fc.keyframe_points])
    check("the overlap is reported by name", any("headlights" in r and "signal" in r for r in _report["reported"]), _report["reported"])
    check(
        "the old data is gone",
        not [a for a in hull.data.attributes if a.name.startswith("emissiveGroup_")]
        and "emissiveGroups" not in hull.data.color_attributes
        and "emissiveGroup_signal" not in DataNodes.ensure_export()
        and DataNodes.read(ptk.Scope.PRIVATE, "emissive_registry") is None
        and DataNodes.read(ptk.Scope.DELIVERABLE, "emissive_groups") is None,
        _report["removed"],
    )
    check("a second run converts nothing", EmissiveGroups.convert_legacy()["converted"] == {})

    # --- a real export, read back --------------------------------------------
    from blendertk.env_utils.fbx_utils import FbxUtils

    hull, panel = reset()
    EmissiveGroups.add_group("signal", {"hull": [0, 1], "panel": [0]})
    for _frame, _value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
        EmissiveGroups.key_weight("signal", _value, frame=_frame)
    _out = tempfile.mkdtemp(prefix="btk_emissive_groups_")
    _fbx = os.path.join(_out, "groups.fbx")
    with FbxUtils.export_prepared(FbxUtils.export_context(), stagers=("render_effects",)):
        check(
            "the render_effects stager stages each animated copy's proxy",
            sorted(o.name for o in bpy.data.objects if o.get("emissiveGroupProxy")) == ["hullMat_signal__emission", "panelMat_signal__emission"],
            sorted(o.name for o in bpy.data.objects if o.get("emissiveGroupProxy")),
        )
        FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
        FbxUtils.export(
            filepath=_fbx,
            objects=[hull, panel, DataNodes.EXPORT],
            selection_only=True,
            bake_anim=True,
            object_types={"MESH", "EMPTY"},
        )
    check("the proxies leave with the write", not [o for o in bpy.data.objects if o.get("emissiveGroupProxy")])
    _file = ptk.FbxFile.load(_fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
    _raw = [v for _o, k, v in _file.user_property_items() if k == ptk.SceneRecords.VISIBILITY.key]
    _record = json.loads(bytes(_raw[0]).decode("utf-8")) if _raw else {}
    check(
        "the FBX record names each animated copy",
        _record.get("materials")
        == [
            {"material": "hullMat_signal", "channels": ["emission"], "emission_color": [1.0, 1.0, 1.0], "emission_strength": 2.0},
            {"material": "panelMat_signal", "channels": ["emission"], "emission_color": [0.2, 0.6, 1.0], "emission_strength": 1.0},
        ],
        _record,
    )
    _curves = ptk.MeshConvert.render_effect_curves(_file)
    check(
        "each copy's weight rides its proxy in each take",
        all({("hullMat_signal", "emission"), ("panelMat_signal", "emission")} <= set(_curves.get(t, {})) for t in ("ShotA", "ShotB")),
        {t: sorted(c) for t, c in _curves.items()},
    )
    _peak = max((v for _t, v, _i in _curves.get("ShotA", {}).get(("hullMat_signal", "emission"), [])), default=None)
    check("the proxy carries the weight, 0..1", _peak is not None and abs(_peak - 1.0) < 1e-3, _peak)
    _verdict = ptk.ExportVerifier(fbx=_fbx, sidecar=None).run(["check_fbx_render_effects", "check_fbx_records"])
    check("the record and the proxies agree", _verdict.ok, _verdict.summary())
    _glb = os.path.join(_out, "groups.glb")
    ptk.MeshConvert.fbx_to_glb(_fbx, dst=_glb, overwrite=True, prompt=False, lightmaps=False)
    with ptk.MeshConvert.open_glb(_glb) as _edit:
        _gltf = _edit.gltf
    _names = [m.get("name") for m in _gltf.get("materials", [])]
    _index = _names.index("hullMat_signal") if "hullMat_signal" in _names else -1
    _pointers = {
        a["name"]: [c["target"]["extensions"]["KHR_animation_pointer"]["pointer"] for c in a["channels"] if c["target"].get("path") == "pointer"]
        for a in _gltf.get("animations", [])
    }
    check(
        "the GLB animates the copy's emissiveFactor per clip",
        _index >= 0 and all(f"/materials/{_index}/emissiveFactor" in _pointers.get(c, []) for c in ("ShotA", "ShotB")),
        (_names, _pointers),
    )
    check(
        "a peak above 1 rides KHR_materials_emissive_strength",
        _index >= 0 and _gltf["materials"][_index].get("extensions", {}).get("KHR_materials_emissive_strength", {}).get("emissiveStrength") == 2.0,
        _index >= 0 and _gltf["materials"][_index],
    )
    check(
        "no vertex-colour mask reaches the GLB (the old encoding's COLOR_0)",
        "COLOR_0" not in {a for _m in _gltf["meshes"] for _p in _m["primitives"] for a in _p["attributes"]},
    )

    # --- a namespaced copy plays (2026-11-07) ---------------------------------
    # A copy whose name carries a namespace (an FBX import of a namespaced Maya
    # scene names it so): the record names it as the delivery does, its proxy
    # and curve go by the transport's name (the leaf), and the GLB plays it.
    # Measured before (mayatk's twin, a real export): no channel was written.
    hull, panel = reset()
    (_copy,) = EmissiveGroups.add_group("signal", {"panel": [0]})
    bpy.data.materials[_copy].name = "ns:panelMat_signal"
    for _frame, _value in ((1, 0.0), (24, 1.0)):
        EmissiveGroups.key_weight("signal", _value, frame=_frame)
    _fbx = os.path.join(_out, "namespaced.fbx")
    with FbxUtils.export_prepared(FbxUtils.export_context(), stagers=("render_effects",)):
        check(
            "the namespaced copy's proxy goes by the transport's name",
            sorted(o.name for o in bpy.data.objects if o.get("emissiveGroupProxy")) == ["panelMat_signal__emission"],
            sorted(o.name for o in bpy.data.objects if o.get("emissiveGroupProxy")),
        )
        FbxUtils.export(
            filepath=_fbx,
            objects=[panel, DataNodes.EXPORT],
            selection_only=True,
            bake_anim=True,
            object_types={"MESH", "EMPTY"},
        )
    _file = ptk.FbxFile.load(_fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
    _raw = [v for _o, k, v in _file.user_property_items() if k == ptk.SceneRecords.VISIBILITY.key]
    _record = json.loads(bytes(_raw[0]).decode("utf-8")) if _raw else {}
    check(
        "the record names the copy as the delivery does",
        [e["material"] for e in _record.get("materials", [])] == ["ns:panelMat_signal"],
        _record,
    )
    _verdict = ptk.ExportVerifier(fbx=_fbx, sidecar=None).run(["check_fbx_render_effects"])
    check("the record and the proxy join", _verdict.ok, _verdict.summary())
    _glb = os.path.join(_out, "namespaced.glb")
    ptk.MeshConvert.fbx_to_glb(_fbx, dst=_glb, overwrite=True, prompt=False, lightmaps=False)
    with ptk.MeshConvert.open_glb(_glb) as _edit:
        _gltf = _edit.gltf
    _played = {
        (c.get("extras") or {}).get("render_effects", {}).get("material")
        for a in _gltf.get("animations", [])
        for c in a["channels"]
    }
    check(
        "the GLB plays the namespaced copy's weight",
        "ns:panelMat_signal" in _played and "ns:panelMat_signal" in [m.get("name") for m in _gltf["materials"]],
        (_played, [m.get("name") for m in _gltf["materials"]]),
    )

    # --- a scoped push carries only the copies its faces wear (2026-11-07) ------
    # Two props, a group on each: a push of one prop names and stages its own
    # copy and no other, through both shapes a Blender writer takes -- the
    # hand-off (stages in the bracket, then publishes its scoped records) and
    # the Scene Exporter (stages in a task, then publishes).
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter

    def scoped_scene():
        hull, panel = reset()
        EmissiveGroups.add_group("a", {"hull": [0, 1]})
        EmissiveGroups.add_group("b", {"panel": [0]})
        for name in ("a", "b"):
            for frame, value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
                EmissiveGroups.key_weight(name, value, frame=frame)
        return hull, panel

    def read_scoped(fbx):
        file = ptk.FbxFile.load(fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
        raw = [v for _o, k, v in file.user_property_items() if k == ptk.SceneRecords.VISIBILITY.key]
        record = json.loads(bytes(raw[0]).decode("utf-8")) if raw else {}
        curves = ptk.MeshConvert.render_effect_curves(file)
        targets = {t for by_take in curves.values() for t, _c in by_take}
        glb = fbx[:-4] + ".glb"
        if not os.path.isfile(glb):
            ptk.MeshConvert.fbx_to_glb(fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False)
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        played = {
            (c.get("extras") or {}).get("render_effects", {}).get("material")
            for a in gltf.get("animations", [])
            for c in a["channels"]
        } - {None}
        verdict = ptk.ExportVerifier(fbx=fbx, sidecar=None).run(["check_fbx_render_effects"])
        return {
            "materials": [e["material"] for e in record.get("materials") or ()],
            "targets": sorted(targets),
            "glb_materials": sorted(m.get("name") for m in gltf.get("materials", [])),
            "played": sorted(played),
            "ok": verdict.ok,
        }

    def only_the_panels_copy(label, found):
        check(
            f"{label}: only the shipped prop's copy, in the record, the proxies and the GLB",
            found["materials"] == ["panelMat_b"]
            and "hullMat_a" not in found["targets"]
            and "panelMat_b" in found["targets"]
            and "hullMat_a" not in found["glb_materials"]
            and found["played"] == ["panelMat_b"]
            and found["ok"],
            found,
        )

    hull, panel = scoped_scene()
    _scoped_fbx = os.path.join(_out, "scoped_handoff.fbx")
    FbxUtils.begin_export(stagers=("render_effects",))
    try:
        FbxUtils.publish(
            FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF, scope=("panel",))
        )
        FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
        FbxUtils.export(
            filepath=_scoped_fbx,
            objects=[panel, DataNodes.EXPORT],
            selection_only=True,
            bake_anim=True,
            object_types={"MESH", "EMPTY"},
        )
    finally:
        FbxUtils.end_export()
    only_the_panels_copy("a hand-off that stages before it publishes", read_scoped(_scoped_fbx))

    hull, panel = scoped_scene()
    from blendertk.anim_utils.shots._shots import ShotStore as _ScopedShots

    _ScopedShots.clear_active()
    _ScopedShots.active().define_shot("ShotA", 1, 24, objects=["hull", "panel"])
    _ScopedShots.active().define_shot("ShotB", 25, 48, objects=["hull", "panel"])
    _written = SceneExporter().perform_export(
        export_dir=_out,
        objects=[panel],
        output_name="scoped_exporter",
        export_visible=True,
        tasks={"output_format": "fbx_glb", "export_data_node": True},
    )
    _ScopedShots.clear_active()
    check("the scoped Scene Exporter run writes", _written is True, _written)
    only_the_panels_copy(
        "the Scene Exporter, which stages before it publishes",
        read_scoped(os.path.join(_out, "scoped_exporter.fbx")),
    )

    # --- weight 1 is the base's glow as the delivery states it (2026-11-07) ---
    # mayatk's openPBR copy stated its luminance (nits) as its strength, 1000x
    # its base. Blender's twin: the emission scalar here scales (Principled's
    # Emission Strength), so the published strength is the scalar itself (the
    # Pure default) -- pinned, per surface, against the materials record's
    # statement of the base. An Emission shader delivers unlit (its glow is
    # its base colour, which glTF's unlit model shades alone), so a weight on
    # it plays nowhere: it is refused by name, as Maya refuses a
    # surfaceShader (2026-11-08; measured before: its entry animated an
    # emissive factor the unlit delivery never shows).
    from blendertk.env_utils.material_records import MaterialRecords

    hull, panel = reset()
    glow = bpy.data.materials.new("emitMat")
    tree = MatUtils.get_node_tree(glow, create=True)
    for node in list(tree.nodes):
        if node.type == "BSDF_PRINCIPLED":
            tree.nodes.remove(node)
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (1.0, 0.5, 0.25, 1.0)
    emit.inputs["Strength"].default_value = 3.0
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
    lamp = bpy.context.active_object
    lamp.name = "lamp"
    lamp.data.materials.append(glow)
    panel.data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 3.0
    _img = bpy.data.images["hullGlow"]  # a map the record reads is a file
    _img.filepath_raw = os.path.join(_out, "hullGlow.png")
    _img.file_format = "PNG"
    _img.save()
    _refused = ""
    try:
        EmissiveGroups.add_group("e", {lamp.name: [0]})
    except ValueError as exc:
        _refused = str(exc)
    check(
        "an Emission surface (delivered unlit) is refused by name, as Maya's surfaceShader",
        "emitMat" in _refused
        and not [m for m in bpy.data.materials if m.name.startswith("emitMat") and m.name != "emitMat"],
        _refused,
    )
    # The delivery's own rule decides (_MatUtilsInternal._is_unlit): a Principled
    # BSDF left in the tree beside an Emission output is unlit too.
    _mixed = bpy.data.materials.new("emitBesideBsdf")
    _mtree = MatUtils.get_node_tree(_mixed, create=True)
    _memit = _mtree.nodes.new("ShaderNodeEmission")
    _memit.inputs["Strength"].default_value = 2.0
    _mtree.links.new(
        _memit.outputs["Emission"],
        next(n for n in _mtree.nodes if n.type == "OUTPUT_MATERIAL").inputs["Surface"],
    )
    lamp.data.materials.append(_mixed)
    lamp.data.polygons[1].material_index = len(lamp.material_slots) - 1
    _refused = ""
    try:
        EmissiveGroups.add_group("e2", {lamp.name: [1]})
    except ValueError as exc:
        _refused = str(exc)
    check(
        "...and so is a Principled BSDF left beside an Emission output",
        "emitBesideBsdf" in _refused
        and any(n.type == "BSDF_PRINCIPLED" for n in _mtree.nodes),
        _refused,
    )
    for obj, base, group in ((panel, "panelMat", "p"), (hull, "hullMat", "h")):
        (copy,) = EmissiveGroups.add_group(group, {obj.name: [0]})
        EmissiveGroups.key_weight(group, 0.0, frame=1)
        EmissiveGroups.key_weight(group, 1.0, frame=24)
        (entry,) = [e for e in EmissiveGroups.render_effect_materials() if e["material"] == copy]
        stated = next(e for e in MaterialRecords.entries([obj]) if e["name"] == base)
        delivered = [c * entry["emission_strength"] for c in entry["emission_color"]]
        check(
            f"{base}: weight 1 is its base's glow as the record states it",
            all(abs(a - b) < 1e-4 for a, b in zip(delivered, stated["emissiveFactor"])),
            (delivered, stated["emissiveFactor"], entry["emission_strength"]),
        )

    # --- the emission curve is read through the package's slot-aware,
    # legacy-safe AnimUtils, never Blender 4.4+'s bpy_extras channelbag helper
    # (absent before 4.4): with it gone, a keyed copy's keys read, its copy
    # keys nothing of its base's and its keys clear. --------------------------
    hull, panel = reset()
    (_lc_copy,) = EmissiveGroups.add_group("legacy", {"hull": [0]}, weight=0.5)
    EmissiveGroups.key_weight("legacy", 0.0, frame=1)
    EmissiveGroups.key_weight("legacy", 1.0, frame=24)
    import bpy_extras.anim_utils as _bx_anim

    _bx_saved = _bx_anim.__dict__.pop("action_get_channelbag_for_slot", None)
    _lc_seen = {}
    try:
        _lc_seen["keys"] = len(HOST.emission_keys(_lc_copy))
        _lc_seen["channel"] = HOST.emission_channel(_lc_copy) is not None
        _lc_fresh = HOST.make_copy(_lc_copy, "hullMat_fresh")
        _lc_seen["fresh"] = HOST.emission_keys(_lc_fresh)
        HOST.clear_emission_keys(_lc_copy)
        _lc_seen["cleared"] = HOST.emission_keys(_lc_copy)
    except Exception as e:
        _lc_seen["error"] = repr(e)
    finally:
        if _bx_saved is not None:
            _bx_anim.action_get_channelbag_for_slot = _bx_saved
    check(
        "the emission curve reads, copies clean and clears without the 4.4+ "
        "channelbag helper",
        _lc_seen.get("keys") == 2
        and _lc_seen.get("channel") is True
        and _lc_seen.get("fresh") == []
        and _lc_seen.get("cleared") == []
        and "error" not in _lc_seen,
        _lc_seen,
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = not any(line.startswith("FAIL") for line in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
