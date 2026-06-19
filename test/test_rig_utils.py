"""blendertk.rig_utils.RigUtils armature/bone/Spline-IK/bind primitives — headless test.
Run: blender --background --factory-startup --python blendertk/test/test_rig_utils.py

These are the net-new Blender rigging primitives the TubeRig strategies sit on: Maya joints →
Armature bones, ikSplineSolver → the Spline IK bone constraint, skinCluster → Armature-deform +
auto weights. Verifies the bone chain geometry, the Spline IK FUNCTIONALLY bends the chain to a
bowed curve (the wire-driver invariant), the bind modifier + weights, and that the mode-scoping
context manager restores OBJECT mode + the prior active object.
"""
import sys, os, traceback

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
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0); bm.to_mesh(me); bm.free()
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
    check("create_armature makes an ARMATURE object", arm is not None and arm.type == "ARMATURE")
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
    check("first bone head at points[0]", (Vector(b0.head_local) - Vector(pts[0])).length < 1e-5)
    check("first bone tail at points[1]", (Vector(b0.tail_local) - Vector(pts[1])).length < 1e-5)
    _b1 = bones[names[1]]
    # NB: bpy returns a fresh wrapper per access, so compare bones by .name (identity `is` fails).
    check("chain is parented + connected",
          _b1.parent is not None and _b1.parent.name == names[0] and _b1.use_connect,
          f"parent={_b1.parent.name if _b1.parent else None} use_connect={_b1.use_connect}")

    # ============================ add_spline_ik (functional bend) ============================
    reset()
    arm = RigUtils.create_armature("TubeArm")
    straight = [(i, 0.0, 0.0) for i in range(4)]
    names = RigUtils.add_bone_chain(arm, straight, prefix="jnt")
    # a curve bowed UP in +Z away from the straight bone line
    curve = nurbs_curve([(0, 0, 0), (1, 0, 1.0), (2, 0, 1.0), (3, 0, 0)], name="IKCurve")
    con = RigUtils.add_spline_ik(arm, names[-1], curve, chain_count=len(names))
    check("spline IK constraint added to the tip bone",
          con.type == "SPLINE_IK" and con.target is curve and con.chain_count == 3)

    def pose_tail_z(armature, bone_name):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = armature.evaluated_get(depsgraph)
        pb = ev.pose.bones[bone_name]
        return (ev.matrix_world @ pb.tail).z

    bpy.context.view_layer.update()
    mid_z = pose_tail_z(arm, names[len(names) // 2])
    check("Spline IK bends the chain up to follow the bowed curve", mid_z > 0.3,
          f"mid pose-bone tail z={mid_z:.3f}")

    # ============================ bind_armature ============================
    reset()
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(arm, [(0, 0, 0), (1, 0, 0), (2, 0, 0)], prefix="jnt")
    box = cube("Tube")
    mod = RigUtils.bind_armature(box, arm, auto_weights=False)
    check("bind (no auto-weights) adds an Armature modifier", mod is not None
          and mod.type == "ARMATURE" and mod.object is arm)

    reset()
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(arm, [(-1, 0, 0), (0, 0, 0), (1, 0, 0)], prefix="jnt")
    box = cube("Tube")
    mod = RigUtils.bind_armature(box, arm, auto_weights=True)
    check("bind (auto-weights) adds the Armature modifier",
          mod is not None and mod.type == "ARMATURE" and mod.object is arm)
    check("auto-weights creates bone vertex groups", len(box.vertex_groups) > 0,
          f"{len(box.vertex_groups)} groups")
    check("auto-weights parents the mesh to the armature", box.parent is arm)

    # ============================ create_group (non-origin, lazy-matrix guard) ============================
    reset()
    child = cube("Child")
    child.location = (2, 0, 0)
    grp = RigUtils.create_group("rig_grp", location=(2, 0, 0), children=[child])
    bpy.context.view_layer.update()
    # the group keeps the child's world transform — without the lazy-matrix settle, a non-origin
    # group would double the child's offset (land it at 4,0,0).
    check("create_group keeps a non-origin child's world transform",
          (child.matrix_world.translation - Vector((2, 0, 0))).length < 1e-5,
          f"{tuple(round(v, 2) for v in child.matrix_world.translation)}")
    check("create_group parents the child", child.parent is grp)

    # ============================ _active_mode restores context ============================
    reset()
    other = cube("Active")
    bpy.context.view_layer.objects.active = other
    arm = RigUtils.create_armature("TubeArm")
    RigUtils.add_bone_chain(arm, [(0, 0, 0), (1, 0, 0)], prefix="jnt")  # uses _active_mode
    check("_active_mode restored the prior active object", bpy.context.view_layer.objects.active is other)
    check("_active_mode left everything in OBJECT mode", other.mode == "OBJECT")

    # ============================ Controls shape factory ============================
    from blendertk.rig_utils.controls import Controls, ControlNodes

    reset()
    c = Controls.create("circle", name="hand_ctrl", size=2.0, axis="y")
    check("create returns a CURVE object", c is not None and c.type == "CURVE")
    check("circle is one cyclic spline", len(c.data.splines) == 1 and c.data.splines[0].use_cyclic_u)
    # size 2 scales the unit circle -> max radius ~2 in the curve's local coords
    radii = [Vector(p.co[:3]).length for p in c.data.splines[0].points]
    check("size scales the shape (unit*2)", abs(max(radii) - 2.0) < 1e-4, f"max r={max(radii):.3f}")
    # axis='y' -> circle plane normal is Y -> all points have y~0
    check("axis='y' orients the plane (y~0)",
          all(abs(p.co[1]) < 1e-5 for p in c.data.splines[0].points))

    reset()
    cx = Controls.create("circle", name="x_ctrl", axis="x")
    check("axis='x' orients the plane (x~0)",
          all(abs(p.co[0]) < 1e-5 for p in cx.data.splines[0].points))

    reset()
    cube_ctrl = Controls.create("cube", name="root_ctrl")
    check("cube is a multi-spline wireframe", len(cube_ctrl.data.splines) == 6,
          f"{len(cube_ctrl.data.splines)}")

    reset()
    colored = Controls.create("diamond", name="col_ctrl", color=(1, 1, 0))
    check("color sets the object's viewport color",
          tuple(round(v, 3) for v in colored.color) == (1.0, 1.0, 0.0, 1.0), f"{tuple(colored.color)}")

    reset()
    nodes = Controls.create("circle", name="grp_ctrl", location=(3, 0, 0), group=True, return_nodes=True)
    check("return_nodes gives a ControlNodes(control, group)",
          isinstance(nodes, ControlNodes) and nodes.group is not None)
    check("group is the control's parent (offset buffer)", nodes.control.parent is nodes.group)
    # control zeroed relative to its group (both at the location)
    check("control is zeroed under the group",
          (nodes.control.matrix_world.translation - Vector((3, 0, 0))).length < 1e-5)

    reset()
    Controls.register_preset("xline", lambda: [([(-1, 0, 0), (1, 0, 0)], False)])
    xl = Controls.create("xline", name="x")
    check("register_preset adds an extensible shape",
          "xline" in Controls.shapes() and len(xl.data.splines) == 1)

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
            bm, cap_ends=False, segments=verts, radius1=radius, radius2=radius, depth=depth
        )
        bm.to_mesh(me); bm.free()
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
    check("get_centerline returns the requested joint count", n == 6 and len(pts) == 6,
          f"n={n} len={len(pts)}")
    check("centerline runs along the tube core (x~0, y~0 for a Z-tube)",
          all(abs(p[0]) < 1e-4 and abs(p[1]) < 1e-4 for p in pts))
    zs = [p[2] for p in pts]
    check("centerline spans the tube length (~8) ordered", abs(zs[-1] - zs[0]) > 7.0,
          f"span={abs(zs[-1] - zs[0]):.2f}")

    # rotated tube (along X) — auto dominant-axis still finds the core; world matrix applied
    reset()
    tube = cylinder("TubeX", radius=0.5, depth=6.0, axis="x")
    pts, n = TubePath.get_centerline(tube, num_joints=4)
    check("centerline handles a world-rotated tube (spans along X)",
          len(pts) == 4 and abs(pts[-1][0] - pts[0][0]) > 5.0,
          f"x-span={abs(pts[-1][0] - pts[0][0]):.2f}")

    # explicit-edge override: one ring of edges -> their verts ordered
    reset()
    tube = cylinder("TubeE", radius=1.0, depth=4.0, verts=8, axis="z")
    ring_edges = [e for e in tube.data.edges
                  if abs(tube.data.vertices[e.vertices[0]].co.z
                         - tube.data.vertices[e.vertices[1]].co.z) < 1e-4][:8]
    pts = TubePath.get_centerline_using_edges(tube, ring_edges)
    check("get_centerline_using_edges returns the ring's ordered verts", len(pts) >= 2,
          f"{len(pts)} pts from {len(ring_edges)} edges")

    # bmesh edges (.verts, not .vertices) — an edit-mode selection hands these over
    import bmesh as _bm
    bm = _bm.new(); bm.from_mesh(tube.data); bm.edges.ensure_lookup_table()
    bm_ring = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) < 1e-4][:8]
    pts_bm = TubePath.get_centerline_using_edges(tube, bm_ring)
    bm.free()
    check("get_centerline_using_edges accepts bmesh edges (.verts)", len(pts_bm) >= 2,
          f"{len(pts_bm)} pts")

    # non-tube guard: a single point cloud -> empty (degenerate), no crash
    reset()
    pt = bpy.data.objects.new("Empty", None)
    bpy.context.collection.objects.link(pt)
    check("get_centerline_using_edges with <2 verts -> []",
          TubePath.get_centerline_using_edges(cylinder("T2", axis="z"), []) == [])

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
