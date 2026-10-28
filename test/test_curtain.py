"""blendertk curtain (vendored CurtainDrape-backed) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_curtain.py
"""

import sys
import os
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    from mathutils import Vector
    import pythontk as ptk
    import blendertk as btk

    from blendertk.edit_utils.curtain import Curtain, CurtainMesh, Rail
    from blendertk.edit_utils.curtain._curtain_drape import CurtainDrape

    # ---- surface: mayatk's names and shape (Rail + CurtainMesh), unregistered
    # like mayatk's; CurtainRig and the tool API Curtain are the registered twins
    check(
        "curtain engine mirrors mayatk's Rail + CurtainMesh",
        issubclass(Rail, ptk.Polyline)
        and all(
            callable(getattr(Rail, n, None)) for n in ("from_selection", "sample_curve")
        )
        and issubclass(CurtainMesh, CurtainDrape)
        and all(callable(getattr(CurtainMesh, n, None)) for n in ("create", "build"))
        and btk.CurtainRig is not None
        and btk.Curtain is Curtain
        and not any(hasattr(btk, n) for n in ("Rail", "CurtainMesh")),
    )

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # ---- build matches the engine grid exactly
    reset()
    rail, closed = ptk.Polyline.make(width=6.0)
    obj = CurtainMesh.create(rail, height=2.0, gravity=0.4, irregularity=0.0)
    u_segs, v_segs, pts = CurtainDrape(
        rail, height=2.0, gravity=0.4, irregularity=0.0
    ).grid_points()
    check("curtain object created", obj is not None and obj.type == "MESH")
    check(
        "vert count matches the engine grid",
        len(obj.data.vertices) == len(pts),
        f"{len(obj.data.vertices)} vs {len(pts)}",
    )
    check("faces are the full grid", len(obj.data.polygons) == u_segs * v_segs)
    drift = max(
        (obj.data.vertices[i].co - Vector(pts[i])).length
        for i in range(0, len(pts), 97)
    )
    check("positions match the engine drape", drift < 1e-5, f"drift={drift:.2e}")
    check("grid UVs present", obj.data.uv_layers.active is not None)
    check("soft shading applied", all(p.use_smooth for p in obj.data.polygons))

    # ---- gravity sags between pinned hang points (world result, not just engine)
    ys = [v.co.y for v in obj.data.vertices]
    check("hem drops below the rail", min(ys) < -2.0, f"min_y={min(ys):.2f}")

    # ---- thickness shells, reduce decimates, invert flips
    reset()
    flat = CurtainMesh.create(rail, height=1.0, gravity=0.0, irregularity=0.0)
    base_faces = len(flat.data.polygons)
    reset()
    shelled = CurtainMesh.create(
        rail, height=1.0, gravity=0.0, irregularity=0.0, thickness=0.05
    )
    check(
        "thickness shells the cloth",
        len(shelled.data.polygons) > base_faces * 1.8,
        f"{base_faces} -> {len(shelled.data.polygons)}",
    )
    reset()
    reduced = CurtainMesh.create(
        rail, height=1.0, gravity=0.0, irregularity=0.0, reduce=50.0
    )
    check(
        "reduce decimates",
        len(reduced.data.polygons) < base_faces * 0.75,
        f"{base_faces} -> {len(reduced.data.polygons)}",
    )
    reset()
    normal_obj = CurtainMesh.create(rail, height=1.0, gravity=0.0, irregularity=0.0)
    n0 = normal_obj.data.polygons[0].normal.copy()
    reset()
    inverted = CurtainMesh.create(
        rail, height=1.0, gravity=0.0, irregularity=0.0, invert=True
    )
    n1 = inverted.data.polygons[0].normal
    check("invert flips the normals", n0.dot(n1) < -0.9, f"dot={n0.dot(n1):.2f}")

    # ---- rail from selection: curve object
    reset()
    bpy.ops.curve.primitive_bezier_curve_add()
    curve = bpy.context.active_object
    rail_sel = Rail.from_selection([curve])
    check(
        "curve resolves to a rail",
        rail_sel is not None and len(rail_sel[0]) >= 2,
        f"n={rail_sel and len(rail_sel[0])}",
    )
    check("open curve -> open rail", rail_sel is not None and rail_sel[1] is False)

    # ---- rail from selection: 2+ object positions
    reset()
    a = bpy.data.objects.new("A", None)
    a.location = (0, 0, 0)
    b = bpy.data.objects.new("B", None)
    b.location = (4, 0, 0)
    for o in (a, b):
        bpy.context.collection.objects.link(o)
    rail_sel = Rail.from_selection([a, b])
    check(
        "two objects resolve to their positions",
        rail_sel is not None
        and len(rail_sel[0]) == 2
        and abs(rail_sel[0][1][0] - 4.0) < 1e-6,
    )

    # ---- nothing usable -> None
    reset()
    check(
        "empty selection -> None",
        Rail.from_selection([]) is None,
    )

    # ---- the tool API (what tentacle's panel calls): names in, names out
    reset()
    check(
        "Curtain keeps blendertk's preset store over the shipped tier",
        Curtain.PRESET_DIR == "blendertk/curtain"
        and os.path.isdir(Curtain.BUILTIN_PRESET_DIR),
    )
    name = Curtain.create(rail, height=1.0, gravity=0.0, irregularity=0.0)
    made = bpy.data.objects.get(name)
    check("create returns the curtain's name", made is not None and made.type == "MESH")
    made.data.calc_loop_triangles()
    check(
        "triangle_count counts the curtain",
        Curtain.triangle_count(name) == len(made.data.loop_triangles),
    )
    check(
        "triangle_count of nothing is None",
        Curtain.triangle_count(None) is None and Curtain.triangle_count("NoSuch") is None,
    )
    check("rail_from_selection of nothing is None", Curtain.rail_from_selection([]) is None)

    curve_name = Curtain.rail_curve(ptk.Polyline.resample(rail, 5), select=True)
    curve = bpy.data.objects.get(curve_name)
    check(
        "rail_curve builds a selected, active curve through the points",
        curve is not None
        and curve.type == "CURVE"
        and len(curve.data.splines[0].points) == 5
        and curve.select_get()
        and bpy.context.view_layer.objects.active == curve,
    )
    wide = ptk.Polyline.resample(ptk.Polyline.make(width=12.0)[0], 7)
    same = Curtain.rail_curve(wide, closed=True, curve=curve_name)
    splines = bpy.data.objects[curve_name].data.splines
    check(
        "rail_curve reshapes the curve in place",
        same == curve_name
        and len(splines) == 1
        and len(splines[0].points) == 7
        and splines[0].use_cyclic_u,
        f"{same} / {len(splines)} spline(s)",
    )
    data_name = bpy.data.objects[curve_name].data.name
    Curtain.delete_rail_curve(curve_name)
    Curtain.delete_rail_curve(curve_name)  # gone: a no-op
    Curtain.delete_rail_curve(None)
    check(
        "delete_rail_curve frees the object and its curve data",
        curve_name not in bpy.data.objects and data_name not in bpy.data.curves,
    )

    # ============================ RIG (control handles + hooks) ============================
    # Blender mirror of mayatk's CurtainRig: control Empties + Hook-with-falloff replace the
    # Maya curve + wire deformer + clusters. The invariant: moving a control pulls the cloth.
    from blendertk.edit_utils.curtain import CurtainRig

    def top_y(obj):
        """Max Y of the EVALUATED (modifier-deformed) curtain — the rail/top edge."""
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            mw = obj.matrix_world
            return max((mw @ v.co).y for v in me.vertices)
        finally:
            ev.to_mesh_clear()

    reset()
    cur = CurtainMesh.create(rail, height=2.0, gravity=0.0, irregularity=0.0)
    n_mods_before = len(cur.modifiers)
    root = CurtainRig.attach(cur, controls=5, dropoff=2.0)
    check("attach returns a root empty", root is not None and root.type == "EMPTY")
    ctrls = [c for c in root.children if c.type == "EMPTY" and "_ctrl_" in c.name]
    check("one control empty per requested control", len(ctrls) == 5, f"{len(ctrls)}")
    check("curtain joins the rig group (parented to root)", cur.parent is root)
    hooks = [m for m in cur.modifiers if m.type == "HOOK"]
    check("one hook modifier per control", len(hooks) == 5, f"{len(hooks)}")
    check(
        "each hook binds a control empty + smooth falloff",
        all(
            h.object in ctrls
            and h.falloff_type == "SMOOTH"
            and abs(h.falloff_radius - 2.0) < 1e-6
            for h in hooks
        ),
    )
    check(
        "hooks added on top of the curtain's own modifiers",
        len(cur.modifiers) == n_mods_before + 5,
    )

    # ---- functional invariant: moving a control LIFTS the curtain (the wire-driver test) ----
    reset()
    cur = CurtainMesh.create(rail, height=2.0, gravity=0.0, irregularity=0.0)
    rest = top_y(cur)  # un-rigged rest pose
    root = CurtainRig.attach(
        cur, controls=5, dropoff=10.0
    )  # wide reach, like Maya's dropoff=10
    ctrls = sorted(
        (c for c in root.children if "_ctrl_" in c.name), key=lambda c: c.location.x
    )
    before = top_y(cur)
    # the hook bind (hook_bind_inverse) must be an IDENTITY deform at rest — attaching the rig may
    # NOT move the cloth until a control is dragged (no jump-on-bind).
    check(
        "attaching the rig does not deform the rest pose",
        abs(before - rest) < 1e-4,
        f"rest {rest:.4f} -> {before:.4f}",
    )
    mid = ctrls[len(ctrls) // 2]
    mid.location.y += 3.0  # lift a mid control (mirrors Maya moving crv.cv[1] +Y)
    bpy.context.view_layer.update()
    after = top_y(cur)
    check(
        "moving a control lifts the curtain",
        after > before + 0.1,
        f"top_y {before:.3f} -> {after:.3f}",
    )

    # ---- rigid root motion translates without deforming (group behavior) ----
    reset()
    cur = CurtainMesh.create(rail, height=2.0, gravity=0.0, irregularity=0.0)
    root = CurtainRig.attach(cur, controls=4, dropoff=3.0)
    base_top = top_y(cur)
    root.location.y += 5.0  # move the whole rig
    bpy.context.view_layer.update()
    check(
        "moving the rig root translates the curtain rigidly",
        abs(top_y(cur) - (base_top + 5.0)) < 1e-3,
        f"{top_y(cur):.3f}",
    )

    # ---- controls from a curve object's control points (Maya per-CV parity) ----
    reset()
    cur = CurtainMesh.create(rail, height=2.0, gravity=0.0, irregularity=0.0)
    cu = bpy.data.curves.new("Rail", "CURVE")
    sp = cu.splines.new("POLY")
    sp.points.add(2)  # 3 points
    for i, p in enumerate(sp.points):
        p.co = (i * 2.0 - 2.0, 0.0, 0.0, 1.0)
    cobj = bpy.data.objects.new("Rail", cu)
    bpy.context.collection.objects.link(cobj)
    root = CurtainRig.attach(cur, controls=cobj, dropoff=2.0)
    cv_hooks = [m for m in cur.modifiers if m.type == "HOOK"]
    check(
        "curve-driven rig makes one control per CV",
        len(cv_hooks) == 3,
        f"{len(cv_hooks)}",
    )

    # ---- vertex guard ----
    reset()
    empty = bpy.data.objects.new("NoMesh", None)
    bpy.context.collection.objects.link(empty)
    raised = False
    try:
        CurtainRig.attach(empty)
    except (ValueError, AttributeError):
        raised = True
    check("attach guards a non-mesh / empty target", raised)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
