"""blendertk.rig_utils.tube_rig — TubeRig engine + the 3 strategies, headless test.
Run: blender --background --factory-startup --python blendertk/test/test_tube_rig.py

Each strategy builds a valid rig on a cylinder and DEFORMS the mesh when its control moves (the
real invariant): Spline IK (hook a curve control → bones refit → mesh follows), Anchor (move an
anchor → the bone stretches), FK (rotate a bone → descendants + mesh follow). Plus the registry +
per-strategy option declarations (the HYBRID source of both defaults and the panel widgets).
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
    import bpy, math
    from mathutils import Vector
    from blendertk.rig_utils.tube_rig import (
        TubeRig, TubeStrategy, TUBE_STRATEGIES, register_strategy, SplineIKStrategy,
    )

    def reset():
        if (bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def tube(name="Tube", radius=0.5, depth=8.0, segs=12, rings=10):
        """A capped cylinder along Z subdivided into `rings` loops (so it deforms when bent)."""
        import bmesh
        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=radius, radius2=radius, depth=depth)
        # subdivide along the length so a bend actually shows
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=rings, use_grid_fill=False)
        bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        bpy.context.view_layer.update()
        return o

    def eval_bounds(obj):
        """Evaluated (deformed) world-space bbox min/max as Vectors."""
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            mw = obj.matrix_world
            ws = [mw @ v.co for v in me.vertices]
            mn = Vector((min(p.x for p in ws), min(p.y for p in ws), min(p.z for p in ws)))
            mx = Vector((max(p.x for p in ws), max(p.y for p in ws), max(p.z for p in ws)))
            return mn, mx
        finally:
            ev.to_mesh_clear()

    # ============================ registry + option declarations ============================
    check("registry has the 3 built-in strategies",
          set(TUBE_STRATEGIES) == {"spline", "anchor", "fk"}, f"{sorted(TUBE_STRATEGIES)}")
    spec = SplineIKStrategy()
    check("strategy declares Qt-free option dicts (AttributeSpec kwargs)",
          all(isinstance(o, dict) and "key" in o and "kind" in o for o in spec.options))
    check("defaults derive from the option dicts",
          spec.defaults()["num_joints"] == 12 and spec.defaults()["enable_stretch"] is True)
    check("resolve overrides defaults", spec.resolve({"num_joints": 20})["num_joints"] == 20
          and spec.resolve({"num_joints": 20})["num_controls"] == 3)

    # ============================ SPLINE IK ============================
    reset()
    t = tube("Hose", depth=8.0)
    rig = TubeRig(t, rig_name="hose")
    bundle = rig.build("spline", num_joints=12, num_controls=3, radius=0.6)
    check("spline: armature + bones built", bundle.armature.type == "ARMATURE" and len(bundle.bones) == 11,
          f"{len(bundle.bones)} bones")
    check("spline: armature + curve + controls grouped under the rig root",
          bundle.armature.parent is bundle.root and bundle.curve.parent is bundle.root
          and all(c.parent is bundle.root for c in bundle.controls))
    check("spline: driver curve + N controls", bundle.curve is not None and len(bundle.controls) == 3,
          f"{len(bundle.controls)} controls")
    check("spline: mesh bound to the armature",
          any(m.type == "ARMATURE" and m.object is bundle.armature for m in t.modifiers))
    check("spline: spline IK constraint on the tip bone",
          any(c.type == "SPLINE_IK" for c in bundle.armature.pose.bones[bundle.bones[-1]].constraints))
    # DEFORM: move the middle control sideways in X -> the hose bends -> mesh x-extent grows
    _, mx0 = eval_bounds(t)
    mid = bundle.controls[len(bundle.controls) // 2]
    mid.location.x += 4.0
    bpy.context.view_layer.update()
    _, mx1 = eval_bounds(t)
    check("spline: moving a control bends the mesh (x-extent grows)", mx1.x > mx0.x + 1.0,
          f"max-x {mx0.x:.2f} -> {mx1.x:.2f}")

    # ============================ ANCHOR ============================
    reset()
    t = tube("Piston", depth=6.0)
    rig = TubeRig(t, rig_name="piston")
    bundle = rig.build("anchor", radius=0.6, enable_stretch=True)
    check("anchor: 1 bone + 2 controls", len(bundle.bones) == 1 and len(bundle.controls) == 2,
          f"bones={len(bundle.bones)} controls={len(bundle.controls)}")
    check("anchor: stretch constraint on the bone",
          any(c.type == "STRETCH_TO" for c in bundle.armature.pose.bones[bundle.bones[0]].constraints))
    # DEFORM: pull the end anchor further out -> the bone (mesh) stretches along Z
    mn0, mx0 = eval_bounds(t)
    end_ctrl = bundle.controls[-1]
    end_ctrl.location.z += 4.0
    bpy.context.view_layer.update()
    mn1, mx1 = eval_bounds(t)
    check("anchor: pulling the end anchor stretches the mesh", (mx1.z - mn1.z) > (mx0.z - mn0.z) + 1.0,
          f"z-len {(mx0.z - mn0.z):.2f} -> {(mx1.z - mn1.z):.2f}")

    # ============================ FK CHAIN ============================
    reset()
    t = tube("Tail", depth=8.0)
    rig = TubeRig(t, rig_name="tail")
    bundle = rig.build("fk", num_joints=8, radius=0.6)
    check("fk: bones are the controls", len(bundle.bones) == 7 and bundle.controls == list(bundle.bones),
          f"{len(bundle.bones)} bones")
    check("fk: each bone has a custom shape",
          all(bundle.armature.pose.bones[b].custom_shape is not None for b in bundle.bones))
    _fkshape = bundle.armature.pose.bones[bundle.bones[0]].custom_shape
    check("fk: custom-shape source parented under root (no orphan)", _fkshape.parent is bundle.root)
    check("fk: mesh bound", any(m.type == "ARMATURE" for m in t.modifiers))
    # DEFORM: rotate the root pose bone -> descendants + mesh swing (the whole bbox shifts)
    mn0, mx0 = eval_bounds(t)
    arm = bundle.armature
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    pb = arm.pose.bones[bundle.bones[0]]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler.x = math.radians(45)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    mn1, mx1 = eval_bounds(t)
    # a 45° root swing over the tube length moves the tail several units; measure the largest shift
    # across ALL bbox components (the swing direction depends on the bone's local axes).
    delta = max(max(abs(mn1[i] - mn0[i]), abs(mx1[i] - mx0[i])) for i in range(3))
    check("fk: rotating the root bone swings the tail (mesh follows)", delta > 1.0,
          f"max bbox shift={delta:.2f}")

    # ============================ dispatch guard + extensibility ============================
    reset()
    t = tube("G")
    raised = False
    try:
        TubeRig(t).build("nope")
    except ValueError:
        raised = True
    check("unknown strategy raises ValueError", raised)

    @register_strategy
    class _Custom(TubeStrategy):
        name = "custom_test"
        label = "Custom"
        options = [{"key": "radius", "kind": "float", "default": 1.0}]
        def build(self, rig, **opts):
            from blendertk.rig_utils.tube_rig import TubeRigBundle
            root = rig.create_root()
            arm, bones = rig.create_armature([(0, 0, 0), (0, 0, 4)])
            return TubeRigBundle(root, arm, bones)
    check("register_strategy extends the registry", "custom_test" in TUBE_STRATEGIES)
    reset()
    t = tube("C")
    b = TubeRig(t, rig_name="c").build("custom_test")
    check("custom registered strategy builds", b.armature.type == "ARMATURE")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
