"""blendertk.rig_utils.RigUtils armature/bone/Spline-IK/bind primitives — headless test.
Run: blender --background --factory-startup --python blendertk/test/test_rig_utils.py

These are the net-new Blender rigging primitives the TubeRig strategies sit on: Maya joints →
Armature bones, ikSplineSolver → the Spline IK bone constraint, skinCluster → Armature-deform +
auto weights. Verifies the bone chain geometry, the Spline IK FUNCTIONALLY bends the chain to a
bowed curve (the wire-driver invariant), the bind modifier + weights, and that the mode-scoping
context manager restores OBJECT mode + the prior active object.
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    from mathutils import Vector
    from blendertk.rig_utils._rig_utils import RigUtils

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube(name="Box"):
        import bmesh

        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        return o

    def nurbs_curve(points, name="Curve"):
        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("NURBS")
        sp.points.add(len(points) - 1)
        for pt, p in zip(sp.points, points):
            pt.co = (p[0], p[1], p[2], 1.0)
        sp.order_u = min(4, len(points))
        sp.use_endpoint_u = True
        obj = bpy.data.objects.new(name, cu)
        bpy.context.collection.objects.link(obj)
        return obj

    # ============================ create_armature ============================
    reset()
    arm = RigUtils.create_armature("TubeArm")
    check(
        "create_armature makes an ARMATURE object",
        arm is not None and arm.type == "ARMATURE",
    )
    check("armature starts with no bones", len(arm.data.bones) == 0)

    # ============================ add_bone_chain ============================
    pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]  # straight along X
    names = RigUtils.add_bone_chain(arm, pts, prefix="jnt")
    check("N points -> N-1 bones", len(names) == 3, f"{len(names)}")
    check("returns to OBJECT mode after editing", arm.mode == "OBJECT", arm.mode)
    bones = arm.data.bones
    check("bones exist by the returned names", all(n in bones for n in names))
    # head[i] = pts[i], tail[i] = pts[i+1] (armature at origin -> local == world)
    b0 = bones[names[0]]
    check(
        "first bone head at points[0]",
        (Vector(b0.head_local) - Vector(pts[0])).length < 1e-5,
    )
    check(
        "first bone tail at points[1]",
        (Vector(b0.tail_local) - Vector(pts[1])).length < 1e-5,
    )
    _b1 = bones[names[1]]
    # NB: bpy returns a fresh wrapper per access, so compare bones by .name (identity `is` fails).
    check(
        "chain is parented + connected",
        _b1.parent is not None and _b1.parent.name == names[0] and _b1.use_connect,
        f"parent={_b1.parent.name if _b1.parent else None} use_connect={_b1.use_connect}",
    )

    # ============================ add_spline_ik (functional bend) ============================
    reset()
    arm = RigUtils.create_armature("TubeArm")
    straight = [(i, 0.0, 0.0) for i in range(4)]
    names = RigUtils.add_bone_chain(arm, straight, prefix="jnt")
    # a curve bowed UP in +Z away from the straight bone line
    curve = nurbs_curve(
        [(0, 0, 0), (1, 0, 1.0), (2, 0, 1.0), (3, 0, 0)], name="IKCurve"
    )
    con = RigUtils.add_spline_ik(arm, names[-1], curve, chain_count=len(names))
    check(
        "spline IK constraint added to the tip bone",
        con.type == "SPLINE_IK" and con.target is curve and con.chain_count == 3,
    )

    def pose_tail_z(armature, bone_name):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = armature.evaluated_get(depsgraph)
        pb = ev.pose.bones[bone_name]
        return (ev.matrix_world @ pb.tail).z

    bpy.context.view_layer.update()
    mid_z = pose_tail_z(arm, names[len(names) // 2])
    check(
        "Spline IK bends the chain up to follow the bowed curve",
        mid_z > 0.3,
        f"mid pose-bone tail z={mid_z:.3f}",
    )

    # ============================ bind_armature ============================
    reset()
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(arm, [(0, 0, 0), (1, 0, 0), (2, 0, 0)], prefix="jnt")
    box = cube("Tube")
    mod = RigUtils.bind_armature(box, arm, auto_weights=False)
    check(
        "bind (no auto-weights) adds an Armature modifier",
        mod is not None and mod.type == "ARMATURE" and mod.object is arm,
    )

    reset()
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(arm, [(-1, 0, 0), (0, 0, 0), (1, 0, 0)], prefix="jnt")
    box = cube("Tube")
    mod = RigUtils.bind_armature(box, arm, auto_weights=True)
    check(
        "bind (auto-weights) adds the Armature modifier",
        mod is not None and mod.type == "ARMATURE" and mod.object is arm,
    )
    check(
        "auto-weights creates bone vertex groups",
        len(box.vertex_groups) > 0,
        f"{len(box.vertex_groups)} groups",
    )
    check("auto-weights parents the mesh to the armature", box.parent is arm)

    # ============================ create_group (non-origin, lazy-matrix guard) ============================
    reset()
    child = cube("Child")
    child.location = (2, 0, 0)
    grp = RigUtils.create_group("rig_grp", location=(2, 0, 0), children=[child])
    bpy.context.view_layer.update()
    # the group keeps the child's world transform — without the lazy-matrix settle, a non-origin
    # group would double the child's offset (land it at 4,0,0).
    check(
        "create_group keeps a non-origin child's world transform",
        (child.matrix_world.translation - Vector((2, 0, 0))).length < 1e-5,
        f"{tuple(round(v, 2) for v in child.matrix_world.translation)}",
    )
    check("create_group parents the child", child.parent is grp)

    # ============================ _active_mode restores context ============================
    reset()
    other = cube("Active")
    bpy.context.view_layer.objects.active = other
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(
        arm, [(0, 0, 0), (1, 0, 0)], prefix="jnt"
    )  # uses _active_mode
    check(
        "_active_mode restored the prior active object",
        bpy.context.view_layer.objects.active is other,
    )
    check("_active_mode left everything in OBJECT mode", other.mode == "OBJECT")

    # ============================ Controls shape factory ============================
    from blendertk.rig_utils.controls import Controls, ControlNodes

    reset()
    c = Controls.create("circle", name="hand_ctrl", size=2.0, axis="y")
    check("create returns a CURVE object", c is not None and c.type == "CURVE")
    check(
        "circle is one cyclic spline",
        len(c.data.splines) == 1 and c.data.splines[0].use_cyclic_u,
    )
    # size 2 scales the unit circle -> max radius ~2 in the curve's local coords
    radii = [Vector(p.co[:3]).length for p in c.data.splines[0].points]
    check(
        "size scales the shape (unit*2)",
        abs(max(radii) - 2.0) < 1e-4,
        f"max r={max(radii):.3f}",
    )
    # axis='y' -> circle plane normal is Y -> all points have y~0
    check(
        "axis='y' orients the plane (y~0)",
        all(abs(p.co[1]) < 1e-5 for p in c.data.splines[0].points),
    )

    reset()
    cx = Controls.create("circle", name="x_ctrl", axis="x")
    check(
        "axis='x' orients the plane (x~0)",
        all(abs(p.co[0]) < 1e-5 for p in cx.data.splines[0].points),
    )

    reset()
    cube_ctrl = Controls.create("cube", name="root_ctrl")
    check(
        "cube is a multi-spline wireframe",
        len(cube_ctrl.data.splines) == 6,
        f"{len(cube_ctrl.data.splines)}",
    )

    reset()
    colored = Controls.create("diamond", name="col_ctrl", color=(1, 1, 0))
    check(
        "color sets the object's viewport color",
        tuple(round(v, 3) for v in colored.color) == (1.0, 1.0, 0.0, 1.0),
        f"{tuple(colored.color)}",
    )

    reset()
    nodes = Controls.create(
        "circle", name="grp_ctrl", location=(3, 0, 0), group=True, return_nodes=True
    )
    check(
        "return_nodes gives a ControlNodes(control, group)",
        isinstance(nodes, ControlNodes) and nodes.group is not None,
    )
    check(
        "group is the control's parent (offset buffer)",
        nodes.control.parent is nodes.group,
    )
    # control zeroed relative to its group (both at the location)
    check(
        "control is zeroed under the group",
        (nodes.control.matrix_world.translation - Vector((3, 0, 0))).length < 1e-5,
    )

    reset()
    Controls.register_preset("xline", lambda: [([(-1, 0, 0), (1, 0, 0)], False)])
    xl = Controls.create("xline", name="x")
    check(
        "register_preset adds an extensible shape",
        "xline" in Controls.shapes() and len(xl.data.splines) == 1,
    )

    raised = False
    try:
        Controls.create("nope")
    except ValueError:
        raised = True
    check("unknown shape raises ValueError", raised)

    # ============================ TubePath centerline extraction ============================
    from blendertk.rig_utils.tube_path import TubePath

    def cylinder(name="Tube", radius=1.0, depth=8.0, verts=16, axis="z"):
        import bmesh

        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cone(
            bm,
            cap_ends=False,
            segments=verts,
            radius1=radius,
            radius2=radius,
            depth=depth,
        )
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        # bmesh cone is built along Z; rotate to lay it along X for the axis test
        if axis == "x":
            import math as _m

            o.rotation_euler = (0.0, _m.radians(90), 0.0)
        bpy.context.view_layer.update()
        return o

    reset()
    tube = cylinder("Tube", radius=1.0, depth=8.0, axis="z")  # along Z
    pts, n = TubePath.get_centerline(tube, num_joints=6)
    check(
        "get_centerline returns the requested joint count",
        n == 6 and len(pts) == 6,
        f"n={n} len={len(pts)}",
    )
    check(
        "centerline runs along the tube core (x~0, y~0 for a Z-tube)",
        all(abs(p[0]) < 1e-4 and abs(p[1]) < 1e-4 for p in pts),
    )
    zs = [p[2] for p in pts]
    check(
        "centerline spans the tube length (~8) ordered",
        abs(zs[-1] - zs[0]) > 7.0,
        f"span={abs(zs[-1] - zs[0]):.2f}",
    )

    # rotated tube (along X) — auto dominant-axis still finds the core; world matrix applied
    reset()
    tube = cylinder("TubeX", radius=0.5, depth=6.0, axis="x")
    pts, n = TubePath.get_centerline(tube, num_joints=4)
    check(
        "centerline handles a world-rotated tube (spans along X)",
        len(pts) == 4 and abs(pts[-1][0] - pts[0][0]) > 5.0,
        f"x-span={abs(pts[-1][0] - pts[0][0]):.2f}",
    )

    # explicit-edge override: one ring of edges -> their verts ordered
    reset()
    tube = cylinder("TubeE", radius=1.0, depth=4.0, verts=8, axis="z")
    ring_edges = [
        e
        for e in tube.data.edges
        if abs(
            tube.data.vertices[e.vertices[0]].co.z
            - tube.data.vertices[e.vertices[1]].co.z
        )
        < 1e-4
    ][:8]
    pts = TubePath.get_centerline_using_edges(tube, ring_edges)
    check(
        "get_centerline_using_edges returns the ring's ordered verts",
        len(pts) >= 2,
        f"{len(pts)} pts from {len(ring_edges)} edges",
    )

    # bmesh edges (.verts, not .vertices) — an edit-mode selection hands these over
    import bmesh as _bm

    bm = _bm.new()
    bm.from_mesh(tube.data)
    bm.edges.ensure_lookup_table()
    bm_ring = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) < 1e-4][:8]
    pts_bm = TubePath.get_centerline_using_edges(tube, bm_ring)
    bm.free()
    check(
        "get_centerline_using_edges accepts bmesh edges (.verts)",
        len(pts_bm) >= 2,
        f"{len(pts_bm)} pts",
    )

    # non-tube guard: a single point cloud -> empty (degenerate), no crash
    reset()
    pt = bpy.data.objects.new("Empty", None)
    bpy.context.collection.objects.link(pt)
    check(
        "get_centerline_using_edges with <2 verts -> []",
        TubePath.get_centerline_using_edges(cylinder("T2", axis="z"), []) == [],
    )

    # ---- set_bone_lengths: cosmetic by construction -------------------------
    # The carriers store no bone length and infer one from the distance to a
    # bone's children, which is wrong for any skeleton flattened for export. The
    # correction is only safe because a length edit moves the tail along the
    # bone's OWN axis: head, direction and roll stay, so every rest matrix a
    # deform reads is untouched. Proven against the shapes that would betray it --
    # non-uniform scale, rotation, roll, and a resized PARENT.
    reset()
    _ad = bpy.data.armatures.new("A")
    _arm = bpy.data.objects.new("A", _ad)
    bpy.context.collection.objects.link(_arm)
    bpy.context.view_layer.objects.active = _arm
    bpy.ops.object.mode_set(mode="EDIT")
    _root = _ad.edit_bones.new("root")
    _root.head, _root.tail = (0, 0, 0), (0, 2.5, 0)
    for _i, _x in enumerate((0.1, 0.2, 0.3)):
        _b = _ad.edit_bones.new("j%d" % _i)
        _b.head, _b.tail = (_x, 0, 0), (_x, 0.6, 0)
        _b.roll = 0.3 * _i
        _b.parent = _root
    bpy.ops.object.mode_set(mode="OBJECT")
    _me = bpy.data.meshes.new("M")
    _me.from_pydata([(0.1, 0.01, 0.02), (0.2, 0.0, 0.03), (0.3, 0.02, 0.0)], [], [])
    _me.update()
    _obj = bpy.data.objects.new("M", _me)
    bpy.context.collection.objects.link(_obj)
    for _i in range(3):
        _obj.vertex_groups.new(name="j%d" % _i).add([_i], 1.0, "REPLACE")
    _obj.modifiers.new("Armature", "ARMATURE").object = _arm
    for _i in range(3):
        _pb = _arm.pose.bones["j%d" % _i]
        _pb.rotation_mode = "XYZ"
        _pb.rotation_euler = (0.4 + _i * 0.2, -0.3, 0.7)
        _pb.scale = (1.2, 0.8, 1.05)
        _pb.location = (0.01 * _i, 0.02, -0.01)
    _arm.pose.bones["root"].rotation_mode = "XYZ"
    _arm.pose.bones["root"].rotation_euler = (0.2, 0.1, -0.15)
    _arm.pose.bones["root"].scale = (1.1, 1.3, 0.9)

    def _deformed():
        _dg = bpy.context.evaluated_depsgraph_get()
        _dg.update()
        _ev = _obj.evaluated_get(_dg)
        _tmp = _ev.to_mesh()
        _out = [Vector(v.co) for v in _tmp.vertices]
        _ev.to_mesh_clear()
        return _out

    _was = _deformed()
    _n = RigUtils.set_bone_lengths(
        _arm, {"root": 0.02, "j0": 0.009, "j1": 0.009, "j2": 0.009, "absent": 0.5}
    )
    _now = _deformed()
    _worst = max((a - b).length for a, b in zip(_was, _now))
    check(
        "set_bone_lengths: resizes by name, skipping a name with no bone",
        _n == 4
        and [round(_ad.bones["j%d" % i].length, 4) for i in range(3)] == [0.009] * 3
        and round(_ad.bones["root"].length, 4) == 0.02,
        f"{_n} resized",
    )
    check(
        "set_bone_lengths: the deform does not move (scaled, rotated, rolled, and "
        "a resized parent included)",
        _worst == 0.0,
        f"worst {_worst:.3e} m",
    )
    check(
        "set_bone_lengths: a non-positive length is refused, not applied",
        RigUtils.set_bone_lengths(_arm, {"j0": 0.0, "j1": -1.0}) == 0
        and round(_ad.bones["j0"].length, 4) == 0.009,
    )
    check(
        "set_bone_lengths: idempotent -- a bone already at that length is not "
        "rewritten (a tail is stored absolute in float32, so a no-op write still "
        "re-quantizes it: measured 4.3e-05 of vertex movement on a rig 250 units "
        "from its armature origin)",
        RigUtils.set_bone_lengths(_arm, {"j0": 0.009, "j1": 0.009}) == 0,
    )
    check(
        "set_bone_lengths: leaves the armature in OBJECT mode",
        _arm.mode == "OBJECT",
        _arm.mode,
    )
    # A Maya pull imports skeletons whose visibility is ANIMATED, so at the import
    # frame the armature is routinely hidden -- and `mode_set` refuses a hidden
    # object outright ("Cannot edit hidden object"), which silently failed the
    # whole bone-sizing step on a production scene.
    _arm.hide_viewport = True
    _arm.hide_render = True
    _hidden_n = RigUtils.set_bone_lengths(_arm, {"j0": 0.004})
    check(
        "set_bone_lengths: works on a HIDDEN armature, and leaves it hidden",
        _hidden_n == 1
        and round(_ad.bones["j0"].length, 4) == 0.004
        and _arm.hide_viewport
        and _arm.hide_render,
        f"{_hidden_n} resized, hide_viewport={_arm.hide_viewport}",
    )
    _arm.hide_viewport = False
    _arm.hide_render = False

    # ---- a CONNECTED child must not be dragged by its parent's length --------
    # `use_connect` glues a bone's head to its parent's TAIL, so resizing the
    # parent moves the child's head -- a REST change. At rest that is invisible
    # (the pose follows), but a pull bakes a pose onto every bone, and a rest
    # that shifts under a pinned pose moves the skin: measured 0.3546 m on a
    # 0.6 m resize, and 9.5 mm across a production skeleton, which is what made
    # a pulled scene miss Maya by 380 mm after the synthetic root landed close
    # enough to its chain for the importer to connect to it.
    reset()
    _cd = bpy.data.armatures.new("C")
    _carm = bpy.data.objects.new("C", _cd)
    bpy.context.collection.objects.link(_carm)
    bpy.context.view_layer.objects.active = _carm
    bpy.ops.object.mode_set(mode="EDIT")
    _cr = _cd.edit_bones.new("c_root")
    _cr.head, _cr.tail = (0, 0, 0), (0, 1, 0)
    _ck = _cd.edit_bones.new("c_kid")
    _ck.head, _ck.tail = (0, 1, 0), (0, 2, 0)
    _ck.parent = _cr
    _ck.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    _cme = bpy.data.meshes.new("CM")
    _cme.from_pydata([(0.0, 1.5, 0.0), (0.05, 1.8, 0.02)], [], [])
    _cme.update()
    _cobj = bpy.data.objects.new("CM", _cme)
    bpy.context.collection.objects.link(_cobj)
    _cobj.vertex_groups.new(name="c_kid").add([0, 1], 1.0, "REPLACE")
    _cobj.modifiers.new("Armature", "ARMATURE").object = _carm
    _cpb = _carm.pose.bones["c_kid"]
    _cpb.rotation_mode = "XYZ"
    _cpb.rotation_euler = (0.6, 0.0, 0.0)

    def _cdeformed():
        _dg = bpy.context.evaluated_depsgraph_get()
        _dg.update()
        _ev = _cobj.evaluated_get(_dg)
        _tmp = _ev.to_mesh()
        _out = [Vector(v.co) for v in _tmp.vertices]
        _ev.to_mesh_clear()
        return _out

    _cwas = _cdeformed()
    _chead = Vector(_cd.bones["c_kid"].head_local)
    RigUtils.set_bone_lengths(_carm, {"c_root": 0.4})
    _cmoved = (Vector(_cd.bones["c_kid"].head_local) - _chead).length
    _cworst = max((a - b).length for a, b in zip(_cwas, _cdeformed()))
    check(
        "set_bone_lengths: a connected child's head stays put",
        _cmoved == 0.0,
        f"head moved {_cmoved:.4f} m",
    )
    check(
        "set_bone_lengths: a posed skin does not move when its parent is resized",
        _cworst == 0.0,
        f"worst {_cworst:.3e} m",
    )
    check(
        "set_bone_lengths: the parent still took the requested length",
        round(_cd.bones["c_root"].length, 4) == 0.4,
        f"{_cd.bones['c_root'].length:.4f} m",
    )

    # ---- set_bone_heads: the whole bone translates ---------------------------
    # The inverse of set_bone_lengths: a head IS part of the rest matrix, so this
    # moves a skin and may only be used on a bone nothing is weighted to (the
    # flatten's synthetic root, which the carrier writes at the world origin).
    # RE-CONNECT first: the set_bone_lengths check above already unglued this
    # child, so without this the "unglued, not dragged" check below would pass
    # on a bone that was never connected -- a test with no teeth.
    bpy.ops.object.mode_set(mode="EDIT")
    _cd.edit_bones["c_kid"].use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    _hwas = Vector(_cd.bones["c_kid"].head_local)
    _tlen = _cd.bones["c_root"].length
    _hmoved = RigUtils.set_bone_heads(_carm, {"c_root": (1.0, 0.0, 0.5)})
    check(
        "set_bone_heads: the bone translates -- head lands, length and direction keep",
        _hmoved == 1
        and (Vector(_cd.bones["c_root"].head_local) - Vector((1.0, 0.0, 0.5))).length
        < 1e-6
        and abs(_cd.bones["c_root"].length - _tlen) < 1e-6,
        f"head {_cd.bones['c_root'].head_local}, length {_cd.bones['c_root'].length}",
    )
    check(
        "set_bone_heads: a connected child is unglued, not dragged along",
        (Vector(_cd.bones["c_kid"].head_local) - _hwas).length == 0.0,
        f"child head moved {(Vector(_cd.bones['c_kid'].head_local) - _hwas).length:.4f} m",
    )
    check(
        "set_bone_heads: idempotent, and a name with no bone is skipped",
        RigUtils.set_bone_heads(_carm, {"c_root": (1.0, 0.0, 0.5), "absent": (0, 0, 0)})
        == 0,
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
