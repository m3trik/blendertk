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
    check("native .usdz export writes a package", z_ok)
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
        and UsdUtils.export_prim_path(monitor_hidden, "/root") == "/root/ic_grp/ic_hidden_001",
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
    mover.data.name = "mover"  # datablock named like the object (a USD round trip does this)
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

    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
