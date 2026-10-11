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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    from mathutils import Vector
    from blendertk.node_utils._node_utils import NodeUtils
    from blendertk.rig_utils._rig_utils import RigUtils
    from blendertk.xform_utils._xform_utils import XformUtils

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
    # A group, never the locator a childless Empty reads as: typed a Maya group it stays
    # one to selection, Suffix By Type and the Maya send (which reads an untyped ARROWS
    # Empty as a locator), and it keeps its arrows as the handle it is grabbed by.
    lone = RigUtils.create_group("lone_grp")
    check(
        "create_group types its Empty a Maya group, its arrows kept",
        grp.get("maya_node_type") == "group"
        and NodeUtils.is_group(lone)
        and lone.empty_display_type == "ARROWS"
        and abs(lone.empty_display_size - 1.0) < 1e-6,
        f"{lone.get('maya_node_type')!r} / {lone.empty_display_size}",
    )

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
    from blendertk.rig_utils.tube_rig.tube_path import TubePath

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

    # ---- locator rigs: create_locator_at_object / remove_locator (mirror of mayatk's) ------
    import pythontk as ptk
    from mathutils import Euler, Matrix

    def _close(a, b, tol=1e-5):
        return all(abs(x - y) < tol for ra, rb in zip(a, b) for x, y in zip(ra, rb))

    reset()
    _box = cube("Box_GEO")
    _box.matrix_world = Matrix.LocRotScale(
        Vector((1.0, 2.0, 3.0)),
        Euler((0.3, 0.2, 0.1)).to_quaternion(),
        Vector((1, 1, 1)),
    )
    bpy.context.view_layer.update()
    _world = [list(r) for r in _box.matrix_world]
    _locs = RigUtils.create_locator_at_object([_box], loc_scale=2.5, lock_rotation=True)
    bpy.context.view_layer.update()
    _loc_name = ptk.NamingConvention.get("locator").apply("Box")
    _grp_name = ptk.NamingConvention.get("group").apply("Box")
    _geo_name = ptk.NamingConvention.get("mesh").apply("Box")
    check(
        "create_locator_at_object: one stem -- the convention affixes each, the old one stripped",
        [o.name for o in _locs] == [_loc_name]
        and _box.name == _geo_name
        and _grp_name in bpy.data.objects,
        f"{[o.name for o in _locs]} / {_box.name}",
    )
    check(
        "create_locator_at_object: group > locator > object, world transform kept",
        _box.parent is _locs[0]
        and _locs[0].parent is bpy.data.objects[_grp_name]
        and _close(_box.matrix_world, _world)
        and _close(_locs[0].matrix_world, _world),
    )
    check(
        "create_locator_at_object: display size + channel locks as asked",
        abs(_locs[0].empty_display_size - 2.5) < 1e-6
        and tuple(_box.lock_rotation) == (True,) * 3
        and tuple(_box.lock_location) == (False,) * 3,
    )
    # The rig is mayatk's, measured in a fresh mayapy: the group holds the object's pose
    # at unit scale, the locator sits on it ZEROED, and the object is frozen under it --
    # so clearing the locator's channels puts the rig back at rest (it used to carry the
    # world in its channels, cancelled by its parent-inverse, and flew to the group's
    # origin). Both Empties carry the node type the bridge restores in Maya; the new
    # locator is selected, as mayatk selects it.
    _ident = Matrix.Identity(4)
    _grp0 = bpy.data.objects[_grp_name]
    check(
        "create_locator_at_object: the locator is zeroed -- identity channels and inverse",
        _close(_locs[0].matrix_basis, _ident)
        and _close(_locs[0].matrix_parent_inverse, _ident),
        f"loc {tuple(_locs[0].location)} rot {tuple(_locs[0].rotation_euler)}",
    )
    check(
        "create_locator_at_object: the group carries the object's pose, at unit scale",
        _close(_grp0.matrix_basis, _world)  # unparented, unit scale: basis == world
        and all(abs(s - 1.0) < 1e-6 for s in _grp0.scale),
        f"scale {tuple(_grp0.scale)}",
    )
    check(
        "create_locator_at_object: the object is frozen under the locator",
        _close(_box.matrix_basis, _ident)
        and _close(_box.matrix_parent_inverse, _ident),
        f"loc {tuple(_box.location)} rot {tuple(_box.rotation_euler)}",
    )
    check(
        "create_locator_at_object: the Empties carry the Maya node type they stand for",
        _locs[0].get("maya_node_type") == "locator"
        and _grp0.get("maya_node_type") == "group",
        f"{_locs[0].get('maya_node_type')!r} / {_grp0.get('maya_node_type')!r}",
    )
    check(
        "create_locator_at_object: the new locator is selected and active",
        set(bpy.context.view_layer.objects.selected) == {_locs[0]}
        and bpy.context.view_layer.objects.active == _locs[0],
        f"{[o.name for o in bpy.context.view_layer.objects.selected]}",
    )
    _crate = cube("Crate07")  # "_07" would be kept: underscore numbering is deliberate
    _l2 = RigUtils.create_locator_at_object(
        "Crate07", obj_suffix="_MSH", obj_affix_mode="suffix", strip_digits=True
    )
    check(
        "create_locator_at_object: a literal affix, digits stripped from the stem",
        _crate.name == "Crate_MSH" and _l2[0].name == _loc_name.replace("Box", "Crate"),
        f"{_crate.name} / {_l2[0].name}",
    )
    # A prop lifted on X then spun about its own Z keys ONE channel only when
    # the order puts Z first (mayatk's SOCKET_WRENCH bug, mirrored).
    from math import radians

    _zloc = RigUtils.create_locator_at_object(cube("Wrench"), rotate_order="zyx")[0]
    _zloc.rotation_euler = Euler((radians(98.3), radians(-6.4), radians(-0.15)), "ZYX")
    _before = tuple(_zloc.rotation_euler)
    _spun = (
        _zloc.rotation_euler.to_matrix() @ Matrix.Rotation(radians(15), 3, "Z")
    ).to_euler("ZYX", _zloc.rotation_euler)
    try:
        RigUtils.create_locator_at_object(cube("Bad"), rotate_order="abc")
        _bad_raised = False
    except ValueError:
        _bad_raised = True
    check(
        "create_locator_at_object: rotate_order sets the Empty's Euler order",
        _zloc.rotation_mode == "ZYX"
        and abs(_spun.x - _before[0]) < 1e-5
        and abs(_spun.y - _before[1]) < 1e-5
        and abs(_spun.z - _before[2] - radians(15)) < 1e-5
        and _bad_raised,
        f"{_zloc.rotation_mode} {tuple(_spun)} vs {_before}",
    )

    _removed = RigUtils.remove_locator(_locs + [_box])
    bpy.context.view_layer.update()
    check(
        "remove_locator: the Empty goes, a non-Empty is skipped, the emptied group follows",
        _removed == [_loc_name]
        and _loc_name not in bpy.data.objects
        and _grp_name not in bpy.data.objects,
        f"{_removed}",
    )
    check(
        "remove_locator: the child lands in world, transform kept, channels unlocked",
        _box.parent is None
        and _close(_box.matrix_world, _world)
        and tuple(_box.lock_rotation) == (False,) * 3,
    )
    check(
        "remove_locator: nothing to dissolve -> []", RigUtils.remove_locator(_box) == []
    )

    # ---- a PARENTED object stays put AND in its hierarchy, through create + remove ----------
    # Blender's world is parent.world @ matrix_parent_inverse @ matrix_basis, so an inverse
    # built from the object's WORLD matrix is right only for an unparented object (world ==
    # basis). Measured on these fixtures before the fix: case A's child jumped 20.9 units
    # and its group landed at the scene root; case B's prop moved 4 units and kept
    # parent_type BONE against an Empty; case C's lower levels moved 6.3; case D's child
    # moved 2.2; remove_locator sent every child to the world.
    import math

    def _empty(name, basis=None, parent=None):
        e = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(e)
        e.parent = parent
        if basis is not None:
            e.matrix_basis = basis
        return e

    def _delta(a, b):
        return max(abs(x - y) for ra, rb in zip(a, b) for x, y in zip(ra, rb))

    def _link(o):
        """The parent link + channels a create/remove round trip must hand back."""
        return (
            o.parent,
            o.parent_type,
            o.parent_bone,
            tuple(o.parent_vertices),
            [list(r) for r in o.matrix_parent_inverse],
            [list(r) for r in o.matrix_basis],
        )

    def _same_link(a, b):
        return a[:4] == b[:4] and _close(a[4], b[4]) and _close(a[5], b[5])

    # A: under a rotated, offset, non-uniformly scaled Empty (the scale shears the child's
    # world) with an arbitrary parent-inverse -- the general case of both Blender parenting
    # styles (an FBX import leaves the inverse identity, Ctrl+P sets the parent's inverse).
    reset()
    _holder = _empty(
        "Holder",
        Matrix.LocRotScale(
            Vector((10, 0, 0)),
            Euler((math.radians(90), 0, 0)).to_quaternion(),
            Vector((1, 2, 1)),
        ),
    )
    _kid = cube("Kid")
    _kid.parent = _holder
    _kid.matrix_parent_inverse = Matrix.Rotation(0.4, 4, "Z")
    _kid.matrix_basis = Matrix.LocRotScale(
        Vector((0, 0, 2)), Euler((0.1, 0.2, 0.3)).to_quaternion(), Vector((1, 1, 1))
    )
    bpy.context.view_layer.update()
    _kw = [list(r) for r in _kid.matrix_world]
    _kl = _link(_kid)
    _kloc = RigUtils.create_locator_at_object([_kid])[0]
    bpy.context.view_layer.update()
    _kgrp = _kloc.parent
    check(
        "create_locator_at_object: a PARENTED object stays put (rotated, offset, scaled parent)",
        _close(_kid.matrix_world, _kw) and _close(_kloc.matrix_world, _kw),
        f"moved {_delta(_kid.matrix_world, _kw):.4f}",
    )
    check(
        "create_locator_at_object: the group takes the object's place under its parent -- "
        "its link, inverse and channels -- and the object is frozen under the locator",
        _kid.parent == _kloc
        and _kgrp is not None
        and _kgrp.parent == _holder
        and _close(_kgrp.matrix_parent_inverse, _kl[4])
        and _close(_kgrp.matrix_basis, _kl[5])
        and _close(_kid.matrix_basis, Matrix.Identity(4)),
        f"group parent {getattr(_kgrp, 'parent', None)}",
    )
    # The whole rig selected (group, locator, object -- a box-select): the object goes back.
    RigUtils.remove_locator([_kgrp, _kloc, _kid])
    bpy.context.view_layer.update()
    check(
        "remove_locator: the child returns to the group's parent -- world, link, channels kept",
        _same_link(_link(_kid), _kl)
        and _close(_kid.matrix_world, _kw)
        and set(bpy.data.objects.keys()) == {"Holder", _kid.name},
        f"parent {_kid.parent}, moved {_delta(_kid.matrix_world, _kw):.4f}, "
        f"left {sorted(bpy.data.objects.keys())}",
    )

    # B: a prop parented to a POSED bone. The group takes the bone link, so the rigged prop
    # keeps following the bone; the prop itself is re-linked as a plain OBJECT child.
    reset()
    _parm = RigUtils.create_armature("PropArm", location=(1, 2, 0))
    RigUtils.add_bone(_parm, "hand", (1, 2, 0), (1, 2, 1))
    _pb = _parm.pose.bones["hand"]
    _pb.rotation_mode = "XYZ"
    _pb.rotation_euler = (0.3, 0.0, 0.2)
    _props = []
    for _n in ("Sword", "SwordRef"):  # the reference twin stays unrigged
        _p = cube(_n)
        _p.parent = _parm
        _p.parent_type = "BONE"
        _p.parent_bone = "hand"
        _p.matrix_parent_inverse = Matrix.Translation((0, -1, 0))
        _p.matrix_basis = Matrix.LocRotScale(
            Vector((0.5, 0, 0)), Euler((0, 0.4, 0)).to_quaternion(), Vector((1, 1, 1))
        )
        _props.append(_p)
    _sword, _ref = _props
    bpy.context.view_layer.update()
    _sl = _link(_sword)
    _sloc = RigUtils.create_locator_at_object(_sword)[0]
    bpy.context.view_layer.update()
    _sgrp = _sloc.parent
    check(
        "create_locator_at_object: a BONE-parented prop stays put, re-linked as a plain child",
        _close(_sword.matrix_world, _ref.matrix_world)
        and _sword.parent == _sloc
        and _sword.parent_type == "OBJECT",
        f"moved {_delta(_sword.matrix_world, _ref.matrix_world):.4f}, "
        f"parent_type {_sword.parent_type!r}",
    )
    # Pose the bone: the rigged prop must follow it.
    _pb.rotation_euler = (1.1, 0.3, -0.4)
    bpy.context.view_layer.update()
    check(
        "create_locator_at_object: the group holds the bone link, so the prop follows the pose",
        _sgrp is not None
        and _sgrp.parent == _parm
        and _sgrp.parent_type == "BONE"
        and _sgrp.parent_bone == "hand"
        and _close(_sword.matrix_world, _ref.matrix_world),
        f"off by {_delta(_sword.matrix_world, _ref.matrix_world):.4f}",
    )
    RigUtils.remove_locator(_sloc)
    bpy.context.view_layer.update()
    check(
        "remove_locator: a bone-parented prop gets its bone link back, world and channels kept",
        _same_link(_link(_sword), _sl)
        and _close(_sword.matrix_world, _ref.matrix_world),
        f"{_sword.parent_type} {_sword.parent_bone!r}, "
        f"off by {_delta(_sword.matrix_world, _ref.matrix_world):.4f}",
    )

    # C: the whole hierarchy selected -- each level rigs inside its parent's rig, and removing
    # the locators hands every object back to its own parent.
    reset()
    _top = _empty(
        "Top",
        Matrix.LocRotScale(
            Vector((0, 5, 0)), Euler((0, 0, 0.7)).to_quaternion(), Vector((2, 2, 2))
        ),
    )
    _mid = _empty("Mid", Matrix.Translation((1, 0, 0)), parent=_top)
    _leaf = cube("Leaf")
    _leaf.parent = _mid
    _leaf.matrix_basis = Matrix.LocRotScale(
        Vector((0, 0, 1)), Euler((0.5, 0, 0)).to_quaternion(), Vector((1, 1, 1))
    )
    bpy.context.view_layer.update()
    _chain = (_top, _mid, _leaf)
    _cw = [[list(r) for r in o.matrix_world] for o in _chain]
    _cl = [_link(o) for o in _chain]
    _clocs = RigUtils.create_locator_at_object(list(_chain))
    bpy.context.view_layer.update()
    check(
        "create_locator_at_object: a whole-hierarchy selection rigs each level in place",
        len(_clocs) == 3
        and all(_close(o.matrix_world, w) for o, w in zip(_chain, _cw))
        and [o.parent for o in _chain] == _clocs
        and _clocs[1].parent.parent == _top
        and _clocs[2].parent.parent == _mid,
        f"moved {[round(_delta(o.matrix_world, w), 4) for o, w in zip(_chain, _cw)]}",
    )
    RigUtils.remove_locator(_clocs)
    bpy.context.view_layer.update()
    check(
        "remove_locator: a whole-hierarchy rig dissolves back to the original tree, nothing moved",
        all(_same_link(_link(o), lk) for o, lk in zip(_chain, _cl))
        and all(_close(o.matrix_world, w) for o, w in zip(_chain, _cw))
        and len(bpy.data.objects) == 3,
        f"parents {[getattr(o.parent, 'name', None) for o in _chain]}, "
        f"moved {[round(_delta(o.matrix_world, w), 4) for o, w in zip(_chain, _cw)]}",
    )

    # D: a VERTEX-parented object. The matrix_world setter cannot place a vertex parent (the
    # original mesh has no evaluated vertices), so remove_locator solves the channels itself;
    # measured through the setter, the child jumped 1.6 units on remove.
    reset()
    _vhost = cube("VHost")
    _vhost.matrix_world = Matrix.LocRotScale(
        Vector((2, 1, 0)), Euler((0.2, 0.5, 0.1)).to_quaternion(), Vector((1, 1.5, 1))
    )
    _vkid = cube("VKid")
    _vkid.parent = _vhost
    _vkid.parent_type = "VERTEX"
    _vkid.parent_vertices = (3, 0, 0)
    _vkid.matrix_basis = Matrix.Translation((0.3, 0, 0.7))
    bpy.context.view_layer.update()
    _vw = [list(r) for r in _vkid.matrix_world]
    _vl = _link(_vkid)
    _vloc = RigUtils.create_locator_at_object(_vkid)[0]
    bpy.context.view_layer.update()
    check(
        "create_locator_at_object: a VERTEX-parented object stays put; the group holds the link",
        _close(_vkid.matrix_world, _vw)
        and _vloc.parent.parent_type == "VERTEX"
        and tuple(_vloc.parent.parent_vertices) == (3, 0, 0),
        f"moved {_delta(_vkid.matrix_world, _vw):.4f}",
    )
    RigUtils.remove_locator(_vloc)
    bpy.context.view_layer.update()
    check(
        "remove_locator: a VERTEX-parented object gets its vertex link back, world kept",
        _same_link(_link(_vkid), _vl) and _close(_vkid.matrix_world, _vw),
        f"{_vkid.parent_type} {tuple(_vkid.parent_vertices)}, "
        f"moved {_delta(_vkid.matrix_world, _vw):.4f}",
    )

    # ---- remove_locator dissolves locator RIGS only (backlog 2026-09-27) ----------------
    # Blender has no locator shape, so "an Empty" read as "a locator": measured before the
    # fix, selecting a user's own group dissolved it AND deleted its childless parent Empty,
    # and selecting a rig's group alone left the rig half-built.
    def _tree():
        return {o.name: getattr(o.parent, "name", None) for o in bpy.data.objects}

    # E: a user's own group, under a user's own Empty -- not a rig, so nothing happens.
    reset()
    _props_e = _empty("Props", Matrix.Translation((10, 0, 0)))
    _table = _empty("Table_group", parent=_props_e)
    for _n in ("Leg1", "Leg2"):
        cube(_n).parent = _table
    bpy.context.view_layer.update()
    _before = _tree()
    check(
        "remove_locator: a user's own group Empty is not a locator rig -- nothing removed",
        RigUtils.remove_locator([_table]) == [] and _tree() == _before,
        f"{_tree()}",
    )

    # F: only a rig's GROUP selected -- in Blender it draws the same cross as the locator,
    # so it names the rig: the whole rig dissolves, not the group alone.
    reset()
    _crate_f = cube("Crate")
    bpy.context.view_layer.update()
    _fw = [list(r) for r in _crate_f.matrix_world]
    _floc = RigUtils.create_locator_at_object(_crate_f)[0]
    _floc_name, _fgrp = _floc.name, _floc.parent
    _fremoved = RigUtils.remove_locator([_fgrp])
    bpy.context.view_layer.update()
    check(
        "remove_locator: a rig's group alone selected dissolves its whole rig",
        _fremoved == [_floc_name]
        and set(bpy.data.objects.keys()) == {_crate_f.name}
        and _crate_f.parent is None
        and _close(_crate_f.matrix_world, _fw),
        f"{_fremoved} / {_tree()}",
    )

    # I: a rig whose group the user took apart -- its locator re-parented under the user's
    # own Empty. The children stay under that Empty, and the Empty survives: only a rig's
    # own group is ever skipped over or deleted.
    reset()
    _tray = _empty("Tray", Matrix.Translation((0, 3, 0)))
    _mug = cube("Mug")
    bpy.context.view_layer.update()
    _iloc = RigUtils.create_locator_at_object(_mug)[0]
    bpy.context.view_layer.update()
    _igrp = _iloc.parent
    _iw = _iloc.matrix_world.copy()
    _iloc.parent = _tray
    _iloc.matrix_world = _iw
    bpy.data.objects.remove(_igrp, do_unlink=True)
    bpy.context.view_layer.update()
    _mw = [list(r) for r in _mug.matrix_world]
    RigUtils.remove_locator(_iloc)
    bpy.context.view_layer.update()
    check(
        "remove_locator: a locator under a user's Empty hands its child to that Empty, "
        "which survives",
        "Tray" in bpy.data.objects
        and _mug.parent == bpy.data.objects.get("Tray")
        and _close(_mug.matrix_world, _mw),
        f"{_tree()}",
    )

    # G: a rig built before the stamp is still a rig -- recognised by its convention names,
    # Blender's ".001" clash suffix included; and a fresh rig carries the stamp.
    reset()
    _vase = cube("Vase")
    bpy.context.view_layer.update()
    _gloc = RigUtils.create_locator_at_object(_vase)[0]
    _ggrp = _gloc.parent
    check(
        "create_locator_at_object: the locator and its group carry the rig stamp",
        _gloc.get(RigUtils.LOCATOR_RIG_PROP) == "locator"
        and _ggrp.get(RigUtils.LOCATOR_RIG_PROP) == "group",
        f"{_gloc.get(RigUtils.LOCATOR_RIG_PROP)!r} / {_ggrp.get(RigUtils.LOCATOR_RIG_PROP)!r}",
    )
    for _o in (_gloc, _ggrp):  # a rig from before both stamps: names alone
        del _o[RigUtils.LOCATOR_RIG_PROP]
        _o.pop("maya_node_type", None)
        _o.name = _o.name + ".001"
    _gloc_name = _gloc.name
    check(
        "remove_locator: an unstamped rig with the convention's names still dissolves",
        RigUtils.remove_locator([_gloc]) == [_gloc_name]
        and set(bpy.data.objects.keys()) == {_vase.name}
        and _vase.parent is None,
        f"{_tree()}",
    )

    # ---- a scaled object: the group carries its pose, never its scale --------------------
    # mayatk's group is unit scale (its freeze bakes the scale into the shape). Taking the
    # object's scale onto the group drew the locator squashed by it (a 2x3x1 box's locator
    # was a 2x3x1 cross); the scale stays with the object, in its parent-inverse, so the
    # mesh is untouched and turning the locator turns the object rigidly.
    reset()
    _sb = cube("Slab")
    _sb.matrix_basis = Matrix.LocRotScale(
        Vector((1, 2, 3)), Euler((0.4, 0.1, 0.6)).to_quaternion(), Vector((2, 3, 1))
    )
    bpy.context.view_layer.update()
    _sbw = [list(r) for r in _sb.matrix_world]
    _sbl0 = _link(_sb)
    _sbco = [tuple(v.co) for v in _sb.data.vertices]
    _sbloc = RigUtils.create_locator_at_object(_sb)[0]
    bpy.context.view_layer.update()
    _sbgrp = _sbloc.parent
    check(
        "create_locator_at_object: a scaled object stays put; its group is unit scale and "
        "its locator draws at its own size",
        _close(_sb.matrix_world, _sbw)
        and all(abs(s - 1.0) < 1e-6 for s in _sbgrp.scale)
        and all(abs(s - 1.0) < 1e-6 for s in _sbloc.matrix_world.to_scale()),
        f"group scale {tuple(_sbgrp.scale)}, locator world scale "
        f"{tuple(_sbloc.matrix_world.to_scale())}",
    )
    check(
        "create_locator_at_object: a scaled object's channels freeze and its mesh is untouched",
        _close(_sb.matrix_basis, Matrix.Identity(4))
        and [tuple(v.co) for v in _sb.data.vertices] == _sbco,
        f"scale {tuple(_sb.scale)}",
    )
    _sbloc.rotation_euler = (0.3, 0.5, 0.7)  # an animator turns the locator
    bpy.context.view_layer.update()
    _axes = [_sb.matrix_world.to_3x3().col[i].normalized() for i in range(3)]
    _shear = max(abs(_axes[i].dot(_axes[j])) for i, j in ((0, 1), (0, 2), (1, 2)))
    check(
        "create_locator_at_object: turning the locator turns a scaled object rigidly",
        _shear < 1e-5,
        f"largest axis cosine {_shear:.6f}",
    )
    _sbloc.rotation_euler = (0.0, 0.0, 0.0)
    RigUtils.remove_locator(_sbloc)
    bpy.context.view_layer.update()
    check(
        "remove_locator: a frozen object gets its own channels back, scale included",
        _same_link(_link(_sb), _sbl0) and _close(_sb.matrix_world, _sbw),
        f"scale {tuple(_sb.scale)}, moved {_delta(_sb.matrix_world, _sbw):.4f}",
    )

    # ---- an animated object keeps playing its keys ---------------------------------------
    # Its channels ARE its animation, so they are not frozen -- the parent-inverse cancels
    # the pose the group took instead. mayatk freezes them and the keys then play offset
    # by that pose (measured: world x 5 -> 10 at frame 1, 15 -> 20 at frame 10; BACKLOG).
    def _plays(o, frames):
        out = []
        for f in frames:
            bpy.context.scene.frame_set(f)
            out.append(o.matrix_world.translation.copy())
        return out

    reset()
    _an = cube("Anim")
    for _f, _x in ((1, 5.0), (10, 15.0)):
        _an.location = (_x, 0.0, 0.5 * _f)
        _an.keyframe_insert("location", frame=_f)
    _anw = _plays(_an, (1, 10))
    bpy.context.scene.frame_set(1)
    _anloc = RigUtils.create_locator_at_object(_an)[0]
    _ana = _plays(_an, (1, 10))
    check(
        "create_locator_at_object: an animated object plays its keys in place",
        all((a - b).length < 1e-5 for a, b in zip(_ana, _anw)),
        f"{[tuple(v) for v in _ana]} vs {[tuple(v) for v in _anw]}",
    )
    check(
        "create_locator_at_object: an animated object's locator is zeroed all the same",
        _close(_anloc.matrix_basis, Matrix.Identity(4)),
        f"loc {tuple(_anloc.location)}",
    )
    # Animated through an NLA strip alone (no active action) -- still animation.
    reset()
    _nl = cube("Stripped")
    for _f, _x in ((1, 2.0), (10, 8.0)):
        _nl.location = (_x, 1.0, 0.0)
        _nl.keyframe_insert("location", frame=_f)
    _nla_track = _nl.animation_data.nla_tracks.new()
    _nla_track.strips.new("take", 1, _nl.animation_data.action)
    _nl.animation_data.action = None
    _nlw = _plays(_nl, (1, 10))
    bpy.context.scene.frame_set(1)
    RigUtils.create_locator_at_object(_nl)
    _nla = _plays(_nl, (1, 10))
    check(
        "create_locator_at_object: an NLA-animated object plays its strip in place",
        (_nlw[1] - _nlw[0]).length > 1.0
        and all((a - b).length < 1e-5 for a, b in zip(_nla, _nlw)),
        f"{[tuple(v) for v in _nla]} vs {[tuple(v) for v in _nlw]}",
    )
    bpy.context.scene.frame_set(1)

    # ---- a group's rig sits on its contents, oriented like the group ----------------------
    # mayatk's 2026-02-23 fix: a group at the origin with its children out at x=10 got its
    # locator at the scene root. The rig goes on the world bounding-box centre of the
    # group's contents, turned like the group; the group itself freezes under it, as
    # mayatk leaves it.
    reset()
    _crt = _empty("Crate_grp", Matrix.Rotation(math.radians(45), 4, "Z"))
    _btl = cube("Bottle")
    _btl.parent = _crt
    _btl.matrix_basis = Matrix.Translation((10, 0, 0))
    bpy.context.view_layer.update()
    _crtw = [list(r) for r in _crt.matrix_world]
    _btlw = [list(r) for r in _btl.matrix_world]
    _ctr = Vector(XformUtils.get_bounding_box([_btl], "center"))
    _crtloc = RigUtils.create_locator_at_object(_crt)[0]
    bpy.context.view_layer.update()
    _turn = (
        _crtloc.matrix_world.to_quaternion()
        .rotation_difference(Matrix(_crtw).to_quaternion())
        .angle
    )
    check(
        "create_locator_at_object: a group's locator sits on its contents' centre, "
        "turned like the group",
        (_crtloc.matrix_world.translation - _ctr).length < 1e-5 and _turn < 1e-5,
        f"{tuple(_crtloc.matrix_world.translation)} vs {tuple(_ctr)}, off by {_turn:.5f} rad",
    )
    check(
        "create_locator_at_object: the group and its contents stay put; the group freezes "
        "and keeps its name",
        _close(_crt.matrix_world, _crtw)
        and _close(_btl.matrix_world, _btlw)
        and _close(_crt.matrix_basis, Matrix.Identity(4))
        and _crt.name == "Crate_grp",
        f"moved {_delta(_btl.matrix_world, _btlw):.4f}, name {_crt.name}",
    )

    # ---- an object named exactly its stem hands that name to its locator ----------------
    # Blender names are global: built before the object was renamed, the locator found
    # its stem taken and became "Vase.001" (measured, with a convention that disables the
    # locator affix). mayatk's locator is "Vase".
    reset()
    _vs = cube("Vase")
    _vsloc = RigUtils.create_locator_at_object(
        _vs, loc_suffix="", obj_suffix="_GEO", obj_affix_mode="suffix"
    )[0]
    check(
        "create_locator_at_object: an empty locator affix names the locator its bare stem",
        _vsloc.name == "Vase" and _vs.name == "Vase_GEO",
        f"{_vsloc.name} / {_vs.name}",
    )

    # ---- remove_locator takes any locator Maya sent across, as mayatk takes any locator ----
    # A Maya locator arrives as an Empty stamped maya_node_type="locator" (the pull's
    # tagging), with no rig stamp and, under a studio's names, no "_LOC" to go by.
    reset()
    _snap = _empty("snap_point", Matrix.Translation((0, 0, 4)))
    _snap["maya_node_type"] = "locator"
    _bolt = cube("bolt")
    _bolt.parent = _snap
    _bolt.matrix_basis = Matrix.Translation((1, 0, 0))
    _asm = _empty("assembly")
    _asm["maya_node_type"] = "group"
    cube("part").parent = _asm
    bpy.context.view_layer.update()
    _boltw = [list(r) for r in _bolt.matrix_world]
    _snap_removed = RigUtils.remove_locator([_snap, _asm])
    bpy.context.view_layer.update()
    check(
        "remove_locator: a Maya-typed locator dissolves, its child kept in place; a "
        "Maya-typed group is no locator",
        _snap_removed == ["snap_point"]
        and "snap_point" not in bpy.data.objects
        and _bolt.parent is None
        and _close(_bolt.matrix_world, _boltw)
        and "assembly" in bpy.data.objects,
        f"{_snap_removed} / {_tree()}",
    )

    # ---- the group draws nothing, as a Maya group (the look the Maya pull gives one) -----
    reset()
    _lamp_loc = RigUtils.create_locator_at_object(cube("Lamp"))[0]
    check(
        "create_locator_at_object: the group draws nothing, as a Maya group",
        abs(_lamp_loc.parent.empty_display_size - NodeUtils.MAYA_GROUP_DISPLAY_SIZE)
        < 1e-9,
        f"{_lamp_loc.parent.empty_display_size}",
    )

    # ---- group_display="ring": a handle that never takes the locator's centre click ------
    # The tiny group still sits under the locator's centre, so a second click there
    # cycles to it; a circle clear of the locator's axes draws nothing at the centre.
    reset()
    _bell_loc = RigUtils.create_locator_at_object(
        cube("Bell"), loc_scale=2.0, group_display="ring"
    )[0]
    _bell_grp = _bell_loc.parent
    check(
        "create_locator_at_object: group_display='ring' draws the group as a circle "
        "clear of the locator's axes -- still a Maya group",
        _bell_grp.empty_display_type == "CIRCLE"
        and abs(_bell_grp.empty_display_size - 2.0 * RigUtils.LOCATOR_RING_SCALE) < 1e-6
        and RigUtils.LOCATOR_RING_SCALE > 1.0
        and NodeUtils.get_maya_node_type(_bell_grp) == "group"
        and abs(_bell_loc.empty_display_size - 2.0) < 1e-6,
        f"{_bell_grp.empty_display_type} {_bell_grp.empty_display_size}",
    )
    _gong = cube("Gong")
    _count = len(bpy.data.objects)
    try:
        RigUtils.create_locator_at_object(_gong, group_display="halo")
        _raised = False
    except ValueError:
        _raised = True
    check(
        "create_locator_at_object: an unknown group_display raises before anything moves",
        _raised
        and len(bpy.data.objects) == _count
        and _gong.parent is None
        and _gong.name == "Gong",
        f"raised={_raised} {_tree()}",
    )

    # ---- freeze_object=False keeps the object's channels (mayatk's flag) -----------------
    reset()
    _kp = cube("Keep")
    _kp.matrix_basis = Matrix.LocRotScale(
        Vector((3, 0, 1)), Euler((0, 0, 0.5)).to_quaternion(), Vector((1, 1, 2))
    )
    bpy.context.view_layer.update()
    _kpb = [list(r) for r in _kp.matrix_basis]
    _kpw = [list(r) for r in _kp.matrix_world]
    _kploc = RigUtils.create_locator_at_object(_kp, freeze_object=False)[0]
    bpy.context.view_layer.update()
    check(
        "create_locator_at_object: freeze_object=False keeps the object's channels; the "
        "locator is zeroed all the same",
        _close(_kp.matrix_basis, _kpb)
        and _close(_kp.matrix_world, _kpw)
        and _close(_kploc.matrix_basis, Matrix.Identity(4)),
        f"moved {_delta(_kp.matrix_world, _kpw):.4f}",
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
