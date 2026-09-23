"""blendertk UsdUtils feature test — export (selection / whole-scene / usdz) + import
round-trip over Blender's native USD runtime (mirror of mayatk's ``env_utils.usd``),
plus cross-validation of pythontk's zero-dep USD author/packager against Blender's
bundled ``pxr`` and importer.

Run: blender --background --factory-startup --python blendertk/test/test_usd.py
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
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    import pythontk as ptk
    import blendertk as btk
    from blendertk.env_utils.usd import UsdUtils

    check("btk.UsdUtils resolves from env_utils.usd", btk.UsdUtils is UsdUtils)
    check(
        "USD helpers are class-only (not flat on btk)",
        btk.UsdUtils.export_selection_usd is UsdUtils.export_selection_usd
        and not hasattr(btk, "import_usd")
        and not hasattr(btk, "export_selection_usd"),
    )
    check("EXTENSIONS shared with pythontk", UsdUtils.EXTENSIONS == ptk.USD_EXTENSIONS)

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    tmp = tempfile.mkdtemp(prefix="btk_usd_")

    # ---- UsdUtils.export(objects=...) + import round-trip -------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "UsdExportCube"

    out = os.path.join(tmp, "rt.usdc")
    written = UsdUtils.export(filepath=out, objects=[cube])
    check(
        "UsdUtils.export writes the file",
        written == out and os.path.isfile(out) and os.path.getsize(out) > 0,
    )
    check("pythontk sniffs the export as crate", ptk.UsdFile.sniff(out) == "usdc")

    reset()
    created = UsdUtils.import_usd(out)
    check(
        "import_usd returns the created objects",
        len(created) >= 1,
        f"{[o.name for o in created]}",
    )
    check(
        "import_usd adds a mesh to the scene",
        any(o.type == "MESH" for o in bpy.data.objects),
    )

    # ---- .usd auto-append + parent-dir creation -----------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    nested = os.path.join(tmp, "sub", "dir", "noext")  # no extension, missing dirs
    w2 = UsdUtils.export(filepath=nested, objects=[bpy.context.active_object])
    check(
        "export appends .usd and creates parent dirs",
        w2 == nested + ".usd" and os.path.isfile(nested + ".usd"),
    )

    # ---- selection_only=False exports the whole scene -----------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    bpy.ops.object.select_all(action="DESELECT")  # nothing selected
    all_out = os.path.join(tmp, "all.usda")
    written_all = UsdUtils.export(filepath=all_out, selection_only=False)
    check(
        "export(selection_only=False) ignores selection + writes",
        written_all == all_out and os.path.isfile(all_out),
    )
    check("usda export is a text layer", ptk.UsdFile.sniff(all_out) == "usda")
    reset()
    created_all = UsdUtils.import_usd(all_out)
    check(
        "whole-scene export round-trips both meshes",
        sum(1 for o in created_all if o.type == "MESH") == 2,
        f"{[o.name for o in created_all]}",
    )

    # ---- selection export with nothing selected raises ----------------------
    reset()
    try:
        UsdUtils.export(filepath=os.path.join(tmp, "no_sel.usd"))
        check("export with empty selection -> RuntimeError", False)
    except RuntimeError:
        check("export with empty selection -> RuntimeError", True)

    # ---- unknown option is dropped, not fatal -------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    opt_out = os.path.join(tmp, "opt.usd")
    UsdUtils.export(
        filepath=opt_out,
        objects=[bpy.context.active_object],
        not_a_real_usd_option=True,
    )
    check(
        "unknown usd_export option dropped (export still writes)",
        os.path.isfile(opt_out),
    )

    # ---- native .usdz export + spec verification via pythontk ---------------
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add()
    z_out = os.path.join(tmp, "pkg.usdz")
    written_z = UsdUtils.export(filepath=z_out, objects=[bpy.context.active_object])
    z_ok = os.path.isfile(z_out) and os.path.getsize(z_out) > 0
    check(".usdz export writes a package (scratch layer, shared packager)", z_ok)
    if z_ok:
        report = ptk.UsdzPackager.verify(z_out)
        check(
            "Blender's usdz passes pythontk's spec verifier",
            report["valid"],
            "; ".join(report["issues"][:3]),
        )
        reset()
        created_z = UsdUtils.import_usd(z_out)
        check("usdz round-trips back in", any(o.type == "MESH" for o in created_z))

    # ---- import_usd missing file -> FileNotFoundError -----------------------
    try:
        UsdUtils.import_usd(os.path.join(tmp, "does_not_exist.usd"))
        check("import_usd missing file -> FileNotFoundError", False)
    except FileNotFoundError:
        check("import_usd missing file -> FileNotFoundError", True)

    # ---- CROSS-VALIDATION: pythontk's zero-dep author vs the real runtime ---
    # Author an OBJ->USDZ with NO pxr/DCC, then make Blender's importer and its
    # bundled pxr accept it — the strongest available proof the hand-authored
    # usda + zip-alignment packaging are spec-correct.
    obj_dir = os.path.join(tmp, "objsrc")
    os.makedirs(obj_dir)
    with open(os.path.join(obj_dir, "quad.obj"), "w") as fh:
        fh.write(
            "mtllib quad.mtl\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
            "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\n"
            "usemtl m\nf 1/1/1 2/2/1 3/3/1 4/4/1\n"
        )
    with open(os.path.join(obj_dir, "quad.mtl"), "w") as fh:
        fh.write("newmtl m\nmap_Kd quad_d.png\n")
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fccfc0f01f0005050202b8bcf3ed0000000049454e44ae426082"
    )
    with open(os.path.join(obj_dir, "quad_d.png"), "wb") as fh:
        fh.write(png)

    authored = ptk.UsdMeshWriter.obj_to_usdz(os.path.join(obj_dir, "quad.obj"))
    check("ptk.obj_to_usdz authors a package", os.path.isfile(authored))

    reset()
    created_a = UsdUtils.import_usd(authored)
    quad = next((o for o in created_a if o.type == "MESH"), None)
    check(
        "Blender imports the zero-dep authored usdz",
        quad is not None,
        f"{[o.name for o in created_a]}",
    )
    if quad is not None:
        check(
            "authored quad has 4 verts / 1 face",
            len(quad.data.vertices) == 4 and len(quad.data.polygons) == 1,
        )
        check("authored quad has UVs", bool(quad.data.uv_layers))
        check(
            "authored material came through",
            len(quad.data.materials) >= 1,
            f"{[m.name for m in quad.data.materials if m]}",
        )

    try:
        from pxr import Usd, UsdGeom  # Blender bundles pxr

        stage = Usd.Stage.Open(authored)
        prim = stage.GetDefaultPrim() if stage else None
        check(
            "bundled pxr opens the authored usdz",
            stage is not None and prim and prim.IsValid(),
        )
        if stage is not None and prim and prim.IsValid():
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(f"{prim.GetPath()}/Geom"))
            check("pxr reads authored mesh data", len(mesh.GetPointsAttr().Get()) == 4)
    except ImportError:
        lines.append("OK  bundled pxr not present in this Blender (skipped)")

    # ---- bridge USD fast path: import_scene(.usd) skips headless Maya -------
    # A bogus maya_path proves the point: if the bridge tried to convert, the
    # discovery/require step would fail — a USD source must never reach it.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    fp_out = os.path.join(tmp, "fastpath.usdc")
    UsdUtils.export(filepath=fp_out, objects=[bpy.context.active_object])
    reset()
    from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

    imported_fp = MayaSceneImport(
        maya_path="X:/definitely/not/maya.exe", log_level="WARNING"
    ).import_scene(fp_out)
    check(
        "bridge USD fast path imports natively (no Maya involved)",
        any(getattr(o, "type", "") == "MESH" for o in imported_fp),
        f"{[getattr(o, 'name', o) for o in imported_fp]}",
    )

    # ---- via="usd" conversion route: template selection (no Maya needed) ----
    eng = MayaSceneImport(maya_path="X:/definitely/not/maya.exe", log_level="WARNING")
    s_usd = eng.render_script("C:/scenes/s.ma", "C:/tmp/out.usd", via="usd")
    check(
        "render_script(via='usd') targets mayaUSDExport",
        "mayaUSDExport" in s_usd
        and "C:/scenes/s.ma" in s_usd
        and "C:/tmp/out.usd" in s_usd,
    )
    check(
        "usd template translates ShaderFX, not the surface family",
        "StingrayPBS" in s_usd and "usd_safe_materials" in s_usd,
    )
    # The default is FBX: its instancing is format-native on both sides, so no
    # sidecar replay stands between a Maya instance set and Blender linked
    # duplicates. USD's equivalent is a recorded grouping replayed on import, and
    # that replay degrades SILENTLY into a flattened scene -- so USD is opt-in.
    s_def = eng.render_script("C:/scenes/s.ma", "C:/tmp/out.fbx")
    check(
        "render_script default is the FBX route",
        "FBXExport" in s_def and "mayaUSDExport" not in s_def,
    )
    s_fbx = eng.render_script("C:/scenes/s.ma", "C:/tmp/out.fbx", via="fbx")
    check("render_script via='fbx' is explicit-equivalent", "FBXExport" in s_fbx)
    check("render_script via='usd' stays available (opt-in)", "mayaUSDExport" in s_usd)
    try:
        eng.render_script("a.ma", "b", via="alembic")
        check("unknown via -> ValueError", False)
    except ValueError:
        check("unknown via -> ValueError", True)

    # ---- interchange: Y-up, hidden objects, prim paths, primary UV set ------
    # Production pull 2026-08-22: a .blend landed in Maya rotated +90 X (Z-up
    # stage, mayaUsd converts nothing on import), its hidden bake-source set
    # visible (Blender's exporter skips hidden objects outright, so they were
    # never in the layer at all), and every mesh on UV set ``st``.
    check(
        "INTERCHANGE_EXPORT_OPTIONS convert to Y-up / -Z forward",
        UsdUtils.INTERCHANGE_EXPORT_OPTIONS.get("convert_orientation") is True
        and UsdUtils.INTERCHANGE_EXPORT_OPTIONS.get("export_global_up_selection") == "Y"
        and UsdUtils.INTERCHANGE_EXPORT_OPTIONS.get("export_global_forward_selection")
        == "NEGATIVE_Z",
    )
    check(
        "INTERCHANGE_IMPORT_OPTIONS read every prim, previews on",
        UsdUtils.INTERCHANGE_IMPORT_OPTIONS.get("import_visible_only") is False
        and UsdUtils.INTERCHANGE_IMPORT_OPTIONS.get("import_usd_preview") is True,
    )

    reset()
    bpy.ops.object.empty_add(location=(0, 0, 0))
    grp = bpy.context.active_object
    grp.name = "ic_grp"
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
    up_cube = bpy.context.active_object
    up_cube.name = "ic_up"
    up_cube.parent = grp
    up_cube.data.uv_layers.new(name="lightmap")  # sorts AHEAD of map1 in USD
    up_cube.data.uv_layers.new(name="map1")
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    monitor_hidden = bpy.context.active_object
    monitor_hidden.name = "ic_hidden.001"  # the importer's collision spelling
    monitor_hidden.parent = grp
    monitor_hidden.hide_viewport = True
    monitor_hidden.hide_render = True  # the exporter's RENDER evaluation skips it
    bpy.ops.mesh.primitive_cube_add(location=(-3, 0, 0))
    eye_hidden = bpy.context.active_object
    eye_hidden.name = "ic_eye_hidden"
    eye_hidden.hide_set(True)

    check(
        "hidden_objects: the monitor toggle and the eye, not the visible ones",
        sorted(o.name for o in UsdUtils.hidden_objects())
        == ["ic_eye_hidden", "ic_hidden.001"],
        str([o.name for o in UsdUtils.hidden_objects()]),
    )
    check(
        "export_prim_path spells the exporter's prim (dots sanitized, parent chain)",
        UsdUtils.export_prim_path(monitor_hidden) == "/ic_grp/ic_hidden_001"
        and UsdUtils.export_prim_path(monitor_hidden, "/root")
        == "/root/ic_grp/ic_hidden_001",
        UsdUtils.export_prim_path(monitor_hidden),
    )
    check(
        "prim_path strips ONLY the importer's .NNN collision suffix",
        UsdUtils.prim_path(monitor_hidden) == "/ic_grp/ic_hidden"
        and UsdUtils.prim_path(up_cube) == "/ic_grp/ic_up",
    )

    ic_out = os.path.join(tmp, "interchange.usda")
    UsdUtils.export(
        filepath=ic_out,
        selection_only=False,
        **dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, convert_scene_units="CENTIMETERS"),
    )
    check(
        "export restores the hidden state it revealed for the exporter",
        monitor_hidden.hide_viewport is True
        and monitor_hidden.hide_render is True
        and eye_hidden.hide_get() is True
        and up_cube.hide_viewport is False,
    )
    try:
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(ic_out)
        root = stage.GetPrimAtPath("/ic_grp")
        ops = {
            op.GetOpName(): tuple(round(v, 3) for v in op.Get())
            for op in UsdGeom.Xformable(root).GetOrderedXformOps()
        }
        check(
            "export: the stage is Y-up, the conversion baked on the ROOT prim",
            UsdGeom.GetStageUpAxis(stage) == "Y"
            and ops.get("xformOp:rotateXYZ", ())[:1] == (-90.0,)
            and ops.get("xformOp:scale") == (100.0, 100.0, 100.0),
            f"{UsdGeom.GetStageUpAxis(stage)} {ops}",
        )
        vis = {
            str(p.GetPath()): UsdGeom.Imageable(p).GetVisibilityAttr().Get()
            for p in stage.Traverse()
            if p.GetTypeName() == "Mesh"
        }
        check(
            "export: hidden objects are IN the layer, stamped invisible",
            vis.get("/ic_grp/ic_hidden_001") == "invisible"
            and vis.get("/ic_eye_hidden") == "invisible"
            and vis.get("/ic_grp/ic_up") == "inherited",
            str(vis),
        )
        del stage
    except ImportError:
        pass

    skip_out = os.path.join(tmp, "interchange_skip.usda")
    UsdUtils.export(filepath=skip_out, selection_only=False, include_hidden=False)
    try:
        from pxr import Usd

        stage = Usd.Stage.Open(skip_out)
        names = {str(p.GetPath()) for p in stage.Traverse()}
        check(
            "export(include_hidden=False): the exporter's own behavior, hidden skipped",
            any(n.endswith("/ic_grp/ic_up") for n in names)
            and not any(n.endswith("/ic_hidden_001") for n in names),
            str(sorted(names)),
        )
        del stage
    except ImportError:
        pass

    reset()
    created = UsdUtils.import_scene(ic_out)
    by_name = {o.name: o for o in created}
    check(
        "import_scene: every prim arrives, the invisible ones HIDDEN",
        {"ic_hidden_001", "ic_eye_hidden", "ic_up"} <= set(by_name)
        and by_name["ic_hidden_001"].hide_viewport
        and by_name["ic_hidden_001"].hide_render
        and by_name["ic_eye_hidden"].hide_viewport
        and not by_name["ic_up"].hide_viewport,
        str({n: (o.hide_viewport, o.hide_render) for n, o in by_name.items()}),
    )
    world = by_name["ic_up"].matrix_world.translation if "ic_up" in by_name else None
    check(
        "import_scene: Y-up cm layer lands back at its Z-up metre position",
        world is not None and tuple(round(v, 3) for v in world) == (0.0, 0.0, 5.0),
        str(world),
    )
    uv = by_name["ic_up"].data.uv_layers if "ic_up" in by_name else None
    check(
        "import_scene: map1 is the render-active UV map although lightmap sorts first",
        uv is not None
        and uv.active is not None
        and uv.active.name == "map1"
        and uv.get("map1").active_render,
        str([(m.name, m.active, m.active_render) for m in uv] if uv else None),
    )

    # apply_visibility: a group's invisibility hides its children too, by PATH.
    reset()
    bpy.ops.object.empty_add()
    g = bpy.context.active_object
    g.name = "vis_grp"
    bpy.ops.mesh.primitive_cube_add()
    child = bpy.context.active_object
    child.name = "vis_child"
    child.parent = g
    bpy.ops.mesh.primitive_cube_add()
    loose = bpy.context.active_object
    loose.name = "vis_loose"
    vis_out = os.path.join(tmp, "vis.usda")
    g.hide_viewport = True
    UsdUtils.export(
        filepath=vis_out, selection_only=False, **UsdUtils.INTERCHANGE_EXPORT_OPTIONS
    )
    g.hide_viewport = False
    reset()
    created = UsdUtils.import_scene(vis_out)
    by_name = {o.name: o for o in created}
    check(
        "import_scene: a single-child group stays a group (no parent-xform merge)",
        "vis_grp" in by_name
        and by_name.get("vis_child") is not None
        and by_name["vis_child"].parent is by_name["vis_grp"],
        str(sorted(by_name)),
    )
    check(
        "apply_visibility: an invisible group hides its children (computed), not strangers",
        {"vis_grp", "vis_child", "vis_loose"} <= set(by_name)
        and by_name["vis_grp"].hide_viewport
        and by_name["vis_child"].hide_viewport
        and not by_name["vis_loose"].hide_viewport,
        str({n: o.hide_viewport for n, o in by_name.items()}),
    )

    # apply_visibility: an ANIMATED prim is keyed, inheritance included. Blender's
    # USD importer reads visibility for nothing (probed 5.1: no hidden state, no
    # fcurves), and Blender does not hide a child with its parent -- so a group
    # whose visibility switches needs every descendant keyed. A production pull
    # lost 8 such prims' subtrees (1113 objects) to this, a RenderOpacity fade
    # among them: that fade IS a gap between two opposite visibility keys.
    reset()
    anim_vis = os.path.join(tmp, "anim_vis.usda")
    from pxr import Usd, UsdGeom

    st = Usd.Stage.CreateInMemory()
    grp_vis = UsdGeom.Xform.Define(st, "/av_grp").CreateVisibilityAttr()
    for time, token in ((1, "inherited"), (10, "invisible"), (20, "inherited")):
        grp_vis.Set(token, time)
    UsdGeom.Cube.Define(st, "/av_grp/av_child")
    steady = UsdGeom.Cube.Define(st, "/av_steady").CreateVisibilityAttr()
    for time in (1, 30):  # sampled, but never changing
        steady.Set("inherited", time)
    st.GetRootLayer().Export(anim_vis)

    created = UsdUtils.import_scene(anim_vis)
    by_name = {o.name: o for o in created}

    def vis_keys(obj):
        ad = getattr(obj, "animation_data", None)
        out = []
        for layer in getattr(getattr(ad, "action", None), "layers", []) or []:
            for strip in layer.strips:
                for cbag in strip.channelbags:
                    for fc in cbag.fcurves:
                        if fc.data_path == "hide_viewport":
                            out += [
                                (
                                    round(k.co[0], 1),
                                    bool(round(k.co[1])),
                                    k.interpolation,
                                )
                                for k in fc.keyframe_points
                            ]
        return sorted(out)

    want = [
        (1.0, False, "CONSTANT"),
        (10.0, True, "CONSTANT"),
        (20.0, False, "CONSTANT"),
    ]
    check(
        "apply_visibility: animated visibility arrives as CONSTANT hide keys",
        vis_keys(by_name.get("av_grp")) == want,
        str(vis_keys(by_name.get("av_grp"))),
    )
    check(
        "apply_visibility: a child inherits the group's switch as its OWN keys",
        vis_keys(by_name.get("av_child")) == want,
        str(vis_keys(by_name.get("av_child"))),
    )
    check(
        "apply_visibility: samples that never change the value key nothing",
        vis_keys(by_name.get("av_steady")) == [],
        str(vis_keys(by_name.get("av_steady"))),
    )
    scene = bpy.context.scene
    scene.frame_set(12)
    hidden_mid = {n for n, o in by_name.items() if o.hide_viewport}
    scene.frame_set(25)
    hidden_late = {n for n, o in by_name.items() if o.hide_viewport}
    check(
        "apply_visibility: the keys actually switch the objects at those frames",
        hidden_mid == {"av_grp", "av_child"} and hidden_late == set(),
        f"frame 12 {sorted(hidden_mid)}, frame 25 {sorted(hidden_late)}",
    )
    # The stage must outlive the prim: a temporary one is collected and the prim
    # goes invalid, which reads as "no samples" rather than as an error.
    reopened = Usd.Stage.Open(anim_vis)
    times = UsdUtils._visibility_sample_times(
        reopened.GetPrimAtPath("/av_grp/av_child")
    )
    check(
        "_visibility_sample_times: an ancestor's samples are the child's too",
        times == [1.0, 10.0, 20.0],
        str(times),
    )

    # ---- animated export keeps its meshes (Blender 5.1 exporter bug + fold) ---
    # merge_parent_xform + export_animation DROPS an animated object's Mesh prim
    # (probed on 5.1.2). The engine exports unmerged and folds each Xform+Mesh
    # pair back into one Mesh prim -- including a mesh datablock named like its
    # object and a nested parent, the two shapes a naive namespace edit trips on.
    reset()
    grp = bpy.data.objects.new("fold_grp", None)
    bpy.context.scene.collection.objects.link(grp)
    bpy.ops.mesh.primitive_cube_add()
    mover = bpy.context.active_object
    mover.name = "mover"
    mover.data.name = (
        "mover"  # datablock named like the object (a USD round trip does this)
    )
    mover.parent = grp
    mover.keyframe_insert("location", frame=1)
    mover.location.x += 5
    mover.keyframe_insert("location", frame=10)
    bpy.ops.mesh.primitive_cube_add()
    still = bpy.context.active_object
    still.name = "still"
    still.parent = grp
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 10
    anim_out = os.path.join(tmp, "anim.usda")
    UsdUtils.export(
        filepath=anim_out,
        objects=[grp, mover, still],
        export_animation=True,
        merge_parent_xform=True,
        use_instancing=False,
        root_prim_path="",
    )
    try:
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(anim_out)
        types = {str(p.GetPath()): p.GetTypeName() for p in stage.Traverse()}
        mover_prim = stage.GetPrimAtPath("/fold_grp/mover")
        translate = mover_prim.GetAttribute("xformOp:translate") if mover_prim else None
        check(
            "animated export: every object is ONE merged Mesh prim (fold)",
            types.get("/fold_grp/mover") == "Mesh"
            and types.get("/fold_grp/still") == "Mesh"
            and not any(k.startswith("/fold_grp/mover/") for k in types),
            str(types),
        )
        check(
            "animated export: the folded Mesh carries the time samples",
            translate is not None and translate.GetNumTimeSamples() >= 2,
            str(translate.GetNumTimeSamples() if translate else None),
        )
        check(
            "fold_single_mesh_xforms is idempotent on a folded layer",
            UsdUtils.fold_single_mesh_xforms(anim_out) == 0,
        )
    except ImportError:
        pass

    # ---- a SKINNED, ANIMATED mesh: UV indices and the skinning method -------
    # Both were measured lost on a production module returning from Blender.
    # The animation is load-bearing: it is export_animation that makes Blender
    # split the UV primvar across time (values at the default, indices as a lone
    # sample), which mayaUsd then refuses -- the mesh lands with the right UV
    # COUNT and zero assigned faces. The RAW operator is the unfixed path, so
    # this is red and green in one run.
    reset()
    bpy.ops.object.armature_add(enter_editmode=True)
    arm = bpy.context.object
    edit_bones = arm.data.edit_bones
    second = edit_bones.new("Bone2")
    second.head, second.tail, second.parent = (0, 0, 1), (0, 0, 2), edit_bones[0]
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_cube_add()
    skinned = bpy.context.object
    for bone_name in ("Bone", "Bone2"):
        skinned.vertex_groups.new(name=bone_name).add(
            range(len(skinned.data.vertices)), 0.5, "REPLACE"
        )
    skin_mod = skinned.modifiers.new("Armature", "ARMATURE")
    skin_mod.object = arm
    skin_mod.use_deform_preserve_volume = True  # Blender's dual-quaternion skinning
    arm.keyframe_insert("rotation_euler", frame=1)
    arm.rotation_euler = (0.5, 0.0, 0.0)
    arm.keyframe_insert("rotation_euler", frame=10)
    bpy.context.scene.frame_end = 10

    skin_opts = dict(
        export_animation=True,
        merge_parent_xform=True,
        root_prim_path="",
        selected_objects_only=False,
    )

    def skin_facts(path):
        """(corner count, default index count, authored skinning method) per skin."""
        from pxr import Usd, UsdGeom, UsdSkel

        stage = Usd.Stage.Open(path)
        out = []
        for prim in stage.Traverse():
            if not (prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdSkel.BindingAPI)):
                continue
            corners = sum(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or [])
            primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
            indices = primvar.GetIndices() if primvar else None
            attr = UsdSkel.BindingAPI(prim).GetSkinningMethodAttr()
            out.append(
                (
                    corners,
                    len(indices) if indices else None,
                    str(attr.Get()) if attr and attr.HasAuthoredValue() else None,
                )
            )
        return out

    bpy.ops.object.select_all(action="SELECT")
    raw_path = os.path.join(tmp, "skin_raw.usd")
    bpy.ops.wm.usd_export(filepath=raw_path, **skin_opts)
    raw_facts = skin_facts(raw_path)
    check(
        "the fixture reproduces BOTH defects on the raw exporter",
        bool(raw_facts)
        and all(n != corners or m is not None for corners, n, m in raw_facts),
        f"raw={raw_facts} -- a green here means the fixture stopped testing anything",
    )

    fixed_path = os.path.join(tmp, "skin_fixed.usd")
    UsdUtils.export(filepath=fixed_path, selection_only=False, **skin_opts)
    fixed_facts = skin_facts(fixed_path)
    check(
        "export pins the UV indices to the DEFAULT time",
        bool(fixed_facts) and all(n == corners for corners, n, _ in fixed_facts),
        f"fixed={fixed_facts}",
    )
    check(
        "export stamps Preserve Volume as dualQuaternion",
        bool(fixed_facts) and all(m == "dualQuaternion" for _, _, m in fixed_facts),
        f"fixed={fixed_facts}",
    )
    check(
        "skinning_methods reads back what export stamped",
        set(UsdUtils.skinning_methods(fixed_path).values()) == {"dualQuaternion"},
        str(UsdUtils.skinning_methods(fixed_path)),
    )
    skin_mod.use_deform_preserve_volume = False
    linear_path = os.path.join(tmp, "skin_linear.usd")
    UsdUtils.export(filepath=linear_path, selection_only=False, **skin_opts)
    check(
        "a linear skin is stamped classicLinear, not left to a default",
        set(UsdUtils.skinning_methods(linear_path).values()) == {"classicLinear"},
        str(UsdUtils.skinning_methods(linear_path)),
    )
    check(
        "pinning is idempotent -- a repaired layer reports nothing left to do",
        UsdUtils.pin_primvar_indices(fixed_path) == 0,
    )

    # ---- container Skeletons are marked for mayaUsd ------------------------
    # Blender writes an armature's DATA as a Skeleton nested under the object's
    # Xform, and mayaUsd imports every Skeleton prim as a joint of its own unless
    # it is marked generated -- the extra joint a bone id then resolved to, 2.4-
    # 2.6 m from the real one (backlog 2026-09-17). The same raw-vs-fixed pair
    # as above; only a pure CONTAINER may be marked (mayaUsd drops the joints of
    # a marked skeleton that carries a transform of its own).
    def skeleton_facts(path):
        """{skeleton path: (Maya:generated, carries its own transform)}."""
        from pxr import Usd, UsdGeom, UsdSkel

        stage = Usd.Stage.Open(path)
        return {
            str(p.GetPath()): (
                p.GetCustomDataByKey("Maya:generated"),
                bool(UsdGeom.Xformable(p).GetOrderedXformOps()),
            )
            for p in stage.Traverse()
            if p.IsA(UsdSkel.Skeleton)
        }

    # The raw operator with the options export() really runs for an animated
    # armature: UNMERGED (see fold_single_mesh_xforms). Merged, the raw exporter
    # folds the object into its Skeleton instead -- a prim with a transform.
    bpy.ops.object.select_all(action="SELECT")
    raw_skeleton_path = os.path.join(tmp, "skeleton_raw.usda")
    bpy.ops.wm.usd_export(
        filepath=raw_skeleton_path, **dict(skin_opts, merge_parent_xform=False)
    )
    raw_skeletons = skeleton_facts(raw_skeleton_path)
    check(
        "the raw exporter writes the armature's data as an UNMARKED container",
        bool(raw_skeletons)
        and all(g is None and not own for g, own in raw_skeletons.values()),
        f"raw={raw_skeletons} -- a green here means the fixture stopped testing anything",
    )
    fixed_skeletons = skeleton_facts(fixed_path)
    check(
        "export marks the container Skeleton generated",
        bool(fixed_skeletons) and all(g is True for g, _ in fixed_skeletons.values()),
        f"fixed={fixed_skeletons}",
    )
    check(
        "marking is idempotent -- a marked layer reports nothing left to do",
        UsdUtils.mark_container_skeletons(fixed_path) == 0,
    )

    # A merged export folds a leaf armature's object into its Skeleton, so the
    # prim carries the object's transform: marking it would lose the joints.
    reset()
    bpy.ops.object.armature_add(enter_editmode=False, location=(1.0, 2.0, 3.0))
    leaf_path = os.path.join(tmp, "leaf_armature.usda")
    UsdUtils.export(
        filepath=leaf_path, objects=[bpy.context.object], merge_parent_xform=True
    )
    leaf_skeletons = skeleton_facts(leaf_path)
    check(
        "a merged leaf armature's Skeleton carries its transform and stays unmarked",
        bool(leaf_skeletons)
        and all(own and g is None for g, own in leaf_skeletons.values()),
        f"leaf={leaf_skeletons}",
    )

    # A primvar whose indices GENUINELY vary must be left alone: pinning one
    # sample would publish that frame's mapping as the answer for every other
    # frame -- a quieter wrong than the bug being fixed.
    from pxr import Sdf, Usd, UsdGeom, Vt

    vary_path = os.path.join(tmp, "vary.usda")
    vary_stage = Usd.Stage.CreateNew(vary_path)
    vary_pv = UsdGeom.PrimvarsAPI(
        UsdGeom.Mesh.Define(vary_stage, "/animated").GetPrim()
    ).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying")
    vary_pv.Set([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    vary_pv.SetIndices(Vt.IntArray([0, 1, 2]), 0.0)
    vary_pv.SetIndices(Vt.IntArray([2, 1, 0]), 10.0)
    vary_stage.GetRootLayer().Save()
    pinned_vary = UsdUtils.pin_primvar_indices(vary_path)
    # Hold the stage: a temporary one is collected and its prims go invalid
    # ("Accessed schema on invalid prim").
    vary_reopened = Usd.Stage.Open(vary_path)
    reopened = UsdGeom.PrimvarsAPI(vary_reopened.GetPrimAtPath("/animated")).GetPrimvar(
        "st"
    )
    check(
        "a genuinely time-varying primvar is NOT pinned to one frame",
        pinned_vary == 0 and reopened.GetIndicesAttr().Get() is None,
        f"pinned={pinned_vary}, default={reopened.GetIndicesAttr().Get()}",
    )
    check(
        "...and its samples survive untouched",
        reopened.GetIndicesAttr().GetNumTimeSamples() == 2,
    )

    # ---- orthographic cameras: Blender's exporter writes only PERSPECTIVE ones ----
    # Probed on 5.1: an ORTHO camera leaves a bare Xform (no Camera prim), so a
    # production pull's only authored camera never reached Maya on the USD route.
    reset()
    ortho_data = bpy.data.cameras.new("ortho_cam")
    ortho_data.type = "ORTHO"
    ortho_data.ortho_scale = 7.5
    ortho_cam = bpy.data.objects.new("ortho_cam", ortho_data)
    bpy.context.scene.collection.objects.link(ortho_cam)
    persp_cam = bpy.data.objects.new("persp_cam", bpy.data.cameras.new("persp_cam"))
    bpy.context.scene.collection.objects.link(persp_cam)
    cam_out = os.path.join(tmp, "cameras.usda")
    UsdUtils.export(filepath=cam_out, selection_only=False, root_prim_path="")
    check(
        "export leaves an ORTHO camera ORTHO (flipped only for the exporter)",
        ortho_data.type == "ORTHO" and persp_cam.data.type == "PERSP",
        f"{ortho_data.type} / {persp_cam.data.type}",
    )
    from pxr import Usd as _Usd, UsdGeom as _UsdGeom

    cam_stage = _Usd.Stage.Open(cam_out)

    def camera_prim(path):
        """The Camera prim an object exported to: the object's own prim when the
        export merged it, else the camera child the exporter nests under it."""
        prim = cam_stage.GetPrimAtPath(path)
        for candidate in [prim] + (list(prim.GetChildren()) if prim else []):
            if candidate and candidate.IsA(_UsdGeom.Camera):
                return _UsdGeom.Camera(candidate)
        return _UsdGeom.Camera()

    ortho_prim = camera_prim("/ortho_cam")
    check(
        "an ORTHO camera exports as an orthographic Camera prim",
        bool(ortho_prim)
        and ortho_prim.GetProjectionAttr().Get() == _UsdGeom.Tokens.orthographic,
        str(
            [
                c.GetTypeName()
                for c in cam_stage.GetPrimAtPath("/ortho_cam").GetChildren()
            ]
        ),
    )
    check(
        "...its width as both apertures (what Blender's own reader takes back)",
        bool(ortho_prim)
        and abs(ortho_prim.GetHorizontalApertureAttr().Get() - 7.5) < 1e-4
        and abs(ortho_prim.GetVerticalApertureAttr().Get() - 7.5) < 1e-4,
    )
    persp_prim = camera_prim("/persp_cam")
    check(
        "a PERSP camera is left perspective",
        bool(persp_prim)
        and persp_prim.GetProjectionAttr().Get() == _UsdGeom.Tokens.perspective,
    )
    reset()
    bpy.ops.wm.usd_import(filepath=cam_out)
    ortho_back = next(
        (
            o
            for o in bpy.data.objects
            if o.type == "CAMERA" and o.name.startswith("ortho")
        ),
        None,
    )
    check(
        "Blender reads the layer back ORTHO at the same ortho_scale",
        ortho_back is not None
        and ortho_back.type == "CAMERA"
        and ortho_back.data.type == "ORTHO"
        and abs(ortho_back.data.ortho_scale - 7.5) < 1e-4,
        str(
            ortho_back
            and (
                ortho_back.type,
                getattr(ortho_back.data, "type", None),
                getattr(ortho_back.data, "ortho_scale", None),
            )
        ),
    )

    # ---- static transforms keep no time samples --------------------------------
    # Blender's writer samples the whole transform of ANY object holding an action,
    # so an object whose only animation is a show/hide ships a per-frame copy of a
    # STATIC transform, and mayaUsd turns the float noise in it into curves:
    # measured on a production round trip, flat +-90/180 deg rotation curves that
    # appeared and vanished pass to pass (+11/-2, then -9/+1) and kept the USD
    # route's fixed point red.
    from pxr import Gf as _Gf, Usd as _Usd, UsdGeom as _UsdGeom

    static_path = os.path.join(tmp, "static_xforms.usda")
    st = _Usd.Stage.CreateNew(static_path)
    noisy = _UsdGeom.Xform.Define(st, "/noisy")
    move = noisy.AddTranslateOp()
    turn = noisy.AddRotateXYZOp()
    for t, dx, rx in ((1, 0.0, 180.0), (2, 3e-5, -180.0), (3, -3e-5, 179.99999)):
        move.Set(_Gf.Vec3d(5.0 + dx, 0, 0), t)  # noise under the tolerance
        turn.Set(_Gf.Vec3f(rx, 0, 90.0), t)  # +-180 is ONE orientation
    moving = _UsdGeom.Xform.Define(st, "/moving")
    slide = moving.AddTranslateOp()
    for t in (1, 2, 3):
        slide.Set(_Gf.Vec3d(float(t), 0, 0), t)
    single = _UsdGeom.Xform.Define(st, "/single")
    single.AddTranslateOp().Set(_Gf.Vec3d(1, 2, 3), 1)
    st.GetRootLayer().Save()
    del st

    collapsed = UsdUtils.collapse_static_xforms(static_path)
    st = _Usd.Stage.Open(static_path)

    def samples(path):
        return [
            op.GetAttr().GetNumTimeSamples()
            for op in _UsdGeom.Xformable(st.GetPrimAtPath(path)).GetOrderedXformOps()
        ]

    check(
        "collapse_static_xforms: a static-within-noise prim loses its samples",
        collapsed == 2 and samples("/noisy") == [0, 0],
        f"collapsed={collapsed}, samples={samples('/noisy')}",
    )
    held = _UsdGeom.Xformable(st.GetPrimAtPath("/noisy")).GetLocalTransformation()
    check(
        "...holding the transform it had (the Euler flip is one orientation)",
        all(abs(held[3][i] - (5.0, 0.0, 0.0)[i]) < 1e-4 for i in range(3)),
        str(held),
    )
    check(
        "...a moving prim is left alone; a lone sample is held static too",
        samples("/moving") == [3] and samples("/single") == [0],
        f"{samples('/moving')} / {samples('/single')}",
    )

    # The translation test is PHYSICAL: a local delta times the parent's world
    # scale times metersPerUnit, against ``distance`` metres. A single unitless
    # threshold failed on production: a static child of a moving parent carried
    # float noise with a 1.22e-4 spread in its cm-scaled local space -- 1.2 um --
    # which a 1e-4 bound read as motion and mayaUsd keyed (hop6: +6 curves).
    def noisy_child(stage_path, mpu, parent_scale, spread):
        stage = _Usd.Stage.CreateNew(stage_path)
        _UsdGeom.SetStageMetersPerUnit(stage, mpu)
        parent = _UsdGeom.Xform.Define(stage, "/parent")
        parent.AddScaleOp().Set(_Gf.Vec3f(parent_scale, parent_scale, parent_scale))
        parent_move = parent.AddTranslateOp()
        child = _UsdGeom.Xform.Define(stage, "/parent/child")
        child_move = child.AddTranslateOp()
        for t, sign in ((1, -1.0), (2, 1.0), (3, 0.0)):
            parent_move.Set(_Gf.Vec3d(float(t), 0, 0), t)  # the parent MOVES
            child_move.Set(_Gf.Vec3d(0.5 * spread * sign, 0.25, 0), t)
        stage.GetRootLayer().Save()

    cases = (
        # (mpu, parent scale, spread in the child's local numbers, expect held)
        (0.01, 1.0, 1.22e-4, True),  # production: cm layer, 1.2 um of noise
        (0.01, 1.0, 0.5, False),  # a real 5 mm slide in the same layer
        (1.0, 1.0, 2e-6, True),  # metre layer: 2 um of noise
        (1.0, 1.0, 1e-3, False),  # metre layer: a real 1 mm slide
        (0.01, 100.0, 1.22e-4, False),  # x100 parent: the same numbers are 0.12 mm
    )
    for index, (mpu, scale, spread, held) in enumerate(cases):
        case_path = os.path.join(tmp, f"units_{index}.usda")
        noisy_child(case_path, mpu, scale, spread)
        UsdUtils.collapse_static_xforms(case_path)
        case_stage = _Usd.Stage.Open(case_path)  # held: a prim dies with its stage
        child_samples = [
            op.GetAttr().GetNumTimeSamples()
            for op in _UsdGeom.Xformable(
                case_stage.GetPrimAtPath("/parent/child")
            ).GetOrderedXformOps()
        ]
        check(
            f"collapse_static_xforms is physical: mpu={mpu} parent x{scale} "
            f"spread {spread} -> {'held' if held else 'kept'}",
            (child_samples == [0]) == held,
            str(child_samples),
        )

    # ...and export() runs it: a show/hide-only object ships a static transform.
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))
    blinker = bpy.context.active_object
    blinker.name = "blinker"
    for frame, hidden in ((1, False), (5, True), (9, False)):
        blinker.hide_viewport = hidden
        blinker.keyframe_insert("hide_viewport", frame=frame)
    blink_out = os.path.join(tmp, "blink.usda")
    UsdUtils.export(
        filepath=blink_out,
        selection_only=False,
        root_prim_path="",
        export_animation=True,
        frame_range=(1, 9),
    )
    blink_stage = _Usd.Stage.Open(blink_out)
    blink_ops = [
        op.GetAttr().GetNumTimeSamples()
        for p in blink_stage.Traverse()
        if p.GetName().startswith("blinker")
        for op in _UsdGeom.Xformable(p).GetOrderedXformOps()
    ]
    check(
        "export: a show/hide-only object's transform carries no time samples",
        bool(blink_ops) and not any(blink_ops),
        str(blink_ops),
    )

    # ---- a .usdz export runs its post-passes before packaging -------------------
    # pxr refuses to SAVE into a package, and every post-pass edits the layer: a
    # hidden object raised inside mark_invisible (and left an unstamped package on
    # disk), as would an ortho camera, a DQ skin or a container skeleton.
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0))
    hidden_ball = bpy.context.active_object
    hidden_ball.name = "hidden_ball"
    hidden_ball.hide_set(True)
    pkg_cam_data = bpy.data.cameras.new("pkg_cam")
    pkg_cam_data.type = "ORTHO"
    pkg_cam_data.ortho_scale = 4.0
    pkg_cam = bpy.data.objects.new("pkg_cam", pkg_cam_data)
    bpy.context.scene.collection.objects.link(pkg_cam)
    pkg_out = os.path.join(tmp, "packaged.usdz")
    try:
        written = UsdUtils.export(
            filepath=pkg_out, selection_only=False, root_prim_path=""
        )
        raised = None
    except Exception as error:  # noqa: BLE001 -- the report is the assertion
        written, raised = None, error
    check(
        "a .usdz export with post-pass work does not raise",
        raised is None and written == pkg_out and ptk.UsdFile.sniff(pkg_out) == "usdz",
        repr(raised),
    )
    if raised is None:
        pkg_stage = _Usd.Stage.Open(pkg_out)
        ball = next(
            (p for p in pkg_stage.Traverse() if p.GetName().startswith("hidden_ball")),
            None,
        )
        check(
            "...the hidden object is stamped invisible INSIDE the package",
            ball is not None
            and _UsdGeom.Imageable(ball).ComputeVisibility() == "invisible",
            str(ball and _UsdGeom.Imageable(ball).ComputeVisibility()),
        )
        cams = [p for p in pkg_stage.Traverse() if p.IsA(_UsdGeom.Camera)]
        check(
            "...and the ortho camera is orthographic inside it",
            len(cams) == 1
            and _UsdGeom.Camera(cams[0]).GetProjectionAttr().Get()
            == _UsdGeom.Tokens.orthographic,
            str([c.GetPath() for c in cams]),
        )
        check(
            "...with the scene state restored after the call",
            pkg_cam_data.type == "ORTHO" and hidden_ball.hide_get() is True,
        )

    # ...and the package still carries its textures: the writer puts them beside
    # the scratch layer and the packager pulls them in (Blender's native usdz
    # writer used to), so a textured deliverable cannot lose its maps silently.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    textured = bpy.context.active_object
    image = bpy.data.images.new("pkg_albedo", 4, 4)
    image.filepath_raw = os.path.join(tmp, "pkg_albedo.png")
    image.file_format = "PNG"
    image.save()
    material = bpy.data.materials.new("pkg_mat")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.links.new(
        node.outputs["Color"],
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"],
    )
    textured.data.materials.append(material)
    tex_out = os.path.join(tmp, "textured.usdz")
    UsdUtils.export(filepath=tex_out, selection_only=False)
    packed = ptk.UsdFile.list_package(tex_out)
    check(
        "a .usdz export packages its textures (the layer first, the map inside)",
        packed[0].endswith(".usda")
        and any(name.endswith("pkg_albedo.png") for name in packed),
        str(packed),
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
