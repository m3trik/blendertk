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
        TubeRig, TubeStrategy, TUBE_STRATEGIES, register_strategy, SplineIKStrategy, TubeRigSlots,
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

    # SQUASH/VOLUME (native Spline IK XZ scale — Maya's squash+volume node systems collapse onto the
    # one XZ enum): stretch the SAME hose under each of the three xz modes and compare the resulting
    # cross-section radius. squash OFF (NONE) keeps it; squash+volume (VOLUME_PRESERVE, XZ=1/sqrtY)
    # thins it; squash without volume (INVERSE_PRESERVE, XZ=1/Y) thins it MORE. Evaluated (deformed) mesh.
    def eval_radius(obj):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            mw = obj.matrix_world
            ws = [mw @ v.co for v in me.vertices]
            cx = sum(p.x for p in ws) / len(ws)
            cy = sum(p.y for p in ws) / len(ws)
            return sum(((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5 for p in ws) / len(ws)
        finally:
            ev.to_mesh_clear()

    def stretched_radius(squash, volume):
        """Radius before + after a fixed +5 stretch of a fresh hose built with the given toggles."""
        reset()
        tt = tube("SQ", depth=8.0)
        bb = TubeRig(tt, rig_name="sq").build(
            "spline", num_joints=12, num_controls=3, radius=0.6,
            enable_stretch=True, enable_squash=squash, enable_volume=volume)
        r_before = eval_radius(tt)
        bb.controls[-1].location.z += 5.0  # stretch the hose along its axis
        bpy.context.view_layer.update()
        return r_before, eval_radius(tt)

    r0, r_none = stretched_radius(squash=False, volume=False)
    _, r_vol = stretched_radius(squash=True, volume=True)
    _, r_inv = stretched_radius(squash=True, volume=False)
    check("spline squash off: cross-section stays ~constant on stretch (NONE)",
          abs(r_none - r0) < 0.02, f"radius {r0:.3f} -> {r_none:.3f}")
    check("spline squash+volume: stretch shrinks the cross-section (VOLUME_PRESERVE)",
          r_vol < r0 - 0.02, f"radius {r0:.3f} -> {r_vol:.3f}")
    check("spline squash w/o volume thins MORE (INVERSE_PRESERVE < VOLUME_PRESERVE)",
          r_inv < r_vol - 0.01, f"volume={r_vol:.3f} inverse={r_inv:.3f}")

    # AUTO_BEND: with auto_bend ON, compressing the two ends together bulges the middle out in +Y
    # (distance-driven mid offset — a driver mirror of Maya's setup_auto_bend multiplyDivide).
    reset()
    t = tube("Bend", depth=8.0)
    b = TubeRig(t, rig_name="bend").build(
        "spline", num_joints=12, num_controls=3, radius=0.6,
        enable_stretch=True, enable_squash=False, enable_auto_bend=True)
    _, mx0 = eval_bounds(t)
    b.controls[-1].location.z -= 4.0  # pull the end toward the start (compression)
    bpy.context.view_layer.update()
    _, mx1 = eval_bounds(t)
    check("spline auto_bend: compressing the ends bulges the middle (+Y)", mx1.y > mx0.y + 0.3,
          f"max-y {mx0.y:.2f} -> {mx1.y:.2f}")

    # auto_bend OFF: the same compression does NOT bulge the middle
    reset()
    t = tube("NoBend", depth=8.0)
    b = TubeRig(t, rig_name="nobend").build(
        "spline", num_joints=12, num_controls=3, radius=0.6,
        enable_stretch=True, enable_squash=False, enable_auto_bend=False)
    _, mx0 = eval_bounds(t)
    b.controls[-1].location.z -= 4.0
    bpy.context.view_layer.update()
    _, mx1 = eval_bounds(t)
    check("spline auto_bend off: compression does not bulge the middle", mx1.y < mx0.y + 0.1,
          f"max-y {mx0.y:.2f} -> {mx1.y:.2f}")

    # TWIST (chk_twist): Blender Spline IK ignores the curve's point tilt, so enable_twist builds a
    # roll-control bone + per-bone Copy Rotation (constant 1/N influence) that composes AFTER the solve.
    # Rolling the control twists the hose PROGRESSIVELY — the tip cross-section rotates ~fully, the
    # start barely moves (evaluated mesh cross-section angle, the invariant the ledger called for).
    def ring_vert(obj, top=True):
        zc = (max if top else min)(v.co.z for v in obj.data.vertices)
        ring = [(v.index, v.co) for v in obj.data.vertices
                if abs(v.co.z - zc) < 0.3 and (v.co.x ** 2 + v.co.y ** 2) ** 0.5 > 0.2]
        return max(ring, key=lambda ic: ic[1].x)[0]  # the off-axis vert nearest +x

    def eval_angle(obj, idx):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            p = obj.matrix_world @ me.vertices[idx].co
            return math.degrees(math.atan2(p.y, p.x))
        finally:
            ev.to_mesh_clear()

    def roll_bone(arm, bone, deg):
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        pb = arm.pose.bones[bone]
        pb.rotation_mode = "XYZ"
        pb.rotation_euler.y = math.radians(deg)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()

    reset()
    t = tube("Twist", depth=8.0)
    b = TubeRig(t, rig_name="tw").build(
        "spline", num_joints=12, num_controls=3, radius=0.6,
        enable_stretch=True, enable_squash=False, enable_twist=True)
    twist_bone = next((bn.name for bn in b.armature.data.bones if "_twist_ctrl" in bn.name), None)
    check("twist: roll-control bone added", twist_bone is not None, f"{twist_bone}")
    # the isolated twist bone is a 2nd parentless root — _ordered_chain must still return the DEFORM
    # chain (11 bones), not mistake the single-bone control for the chain (b004 relies on this too).
    ordered = TubeRigSlots._ordered_chain(b.armature)
    check("twist: _ordered_chain returns the deform chain, not the isolated control bone",
          len(ordered) == 11 and twist_bone not in ordered, f"chain len={len(ordered)}")
    tip_i, start_i = ring_vert(t, top=True), ring_vert(t, top=False)
    tip_a0, start_a0 = eval_angle(t, tip_i), eval_angle(t, start_i)
    roll_bone(b.armature, twist_bone, 90)
    def wrap(d):
        d = abs(d) % 360.0
        return min(d, 360.0 - d)
    tip_d = wrap(eval_angle(t, tip_i) - tip_a0)
    start_d = wrap(eval_angle(t, start_i) - start_a0)
    check("twist: rolling the control twists the tip cross-section", tip_d > 45,
          f"tip rotated {tip_d:.0f} deg for a 90 deg roll")
    check("twist: twist is progressive (start rotates far less than the tip)", start_d < tip_d - 30,
          f"start {start_d:.0f} vs tip {tip_d:.0f} deg")

    reset()
    t = tube("NoTwist", depth=8.0)
    b = TubeRig(t, rig_name="ntw").build(
        "spline", num_joints=12, num_controls=3, radius=0.6, enable_twist=False)
    check("twist off: no roll-control bone built (the toggle gates it)",
          not any("_twist_ctrl" in bn.name for bn in b.armature.data.bones))

    # ============================ END CONSTRAINTS w/ FALLOFF (b004) ============================
    # constrain_end_with_falloff grafts an anchor bone that tracks an external object PLUS a per-vertex
    # distance-falloff weight blend (RigUtils.apply_falloff_weights) so the near-end skin sticks to the
    # anchor and fades to the existing deform by the radius. Bare BOUND chain (no Spline IK competing)
    # isolates the blend — the novel algorithm the parity gap called for.
    from blendertk.rig_utils._rig_utils import RigUtils

    reset()
    t = tube("Anchored", depth=8.0)
    rig = TubeRig(t, rig_name="anch")
    armobj, bones = rig.create_joint_chain(rig.resolve_centerline(6), radius=0.5)
    RigUtils.bind_armature(t, armobj, auto_weights=True)
    mn, mx = eval_bounds(t)

    def empty(name, loc):
        e = bpy.data.objects.new(name, None)
        e.location = loc
        bpy.context.collection.objects.link(e)
        return e

    a_start = empty("A_start", (0, 0, mn.z))
    a_end = empty("A_end", (0, 0, mx.z))
    bpy.context.view_layer.update()

    sb = rig.constrain_end_with_falloff(armobj, bones, a_start, t, falloff=3.0, bone_index=0)
    eb = rig.constrain_end_with_falloff(armobj, bones, a_end, t, falloff=3.0, bone_index=-1)
    check("b004: anchor bones grafted for both ends",
          sb in armobj.data.bones and eb in armobj.data.bones, f"{sb}, {eb}")
    check("b004: falloff vertex groups created on the mesh",
          sb in t.vertex_groups and eb in t.vertex_groups)

    def eval_vert(obj, idx):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            return (obj.matrix_world @ me.vertices[idx].co).copy()
        finally:
            ev.to_mesh_clear()

    zs = [(v.co.z, v.index) for v in t.data.vertices]
    end_idx = max(zs)[1]                             # near a_end (top cap)
    mid_idx = min(zs, key=lambda p: abs(p[0]))[1]    # nearest z=0 (beyond either radius)
    pe0, pm0 = eval_vert(t, end_idx), eval_vert(t, mid_idx)
    a_end.location.x += 5.0                          # drag the end anchor sideways
    bpy.context.view_layer.update()
    end_shift = (eval_vert(t, end_idx) - pe0).length
    mid_shift = (eval_vert(t, mid_idx) - pm0).length
    check("b004: moving an anchor drags the NEAR-end skin (falloff blend)", end_shift > 1.0,
          f"end vertex shift={end_shift:.2f}")
    check("b004: the MIDDLE barely moves (falloff decays to zero by the radius)", mid_shift < 0.5,
          f"mid vertex shift={mid_shift:.2f}")
    check("b004: near-end follows the anchor MORE than the middle (monotonic falloff)",
          end_shift > mid_shift + 1.0, f"end={end_shift:.2f} mid={mid_shift:.2f}")

    # precise redistribution invariant (Blender does NOT normalize raw group weights until deform
    # time, so a raw-sum check is meaningless): at a vertex a KNOWN distance from the falloff center,
    # apply_falloff sets target = 1 - d/r AND scales the vertex's existing influences by (1-w). A
    # broken version that ADDED the weight would leave the others unscaled — this catches that.
    reset()
    t2 = tube("Redist", depth=8.0)
    r2 = TubeRig(t2, rig_name="rd")
    arm2, _ = r2.create_joint_chain(r2.resolve_centerline(6), radius=0.5)
    RigUtils.bind_armature(t2, arm2, auto_weights=True)
    vidx = max((v.co.z, v.index) for v in t2.data.vertices)[1]
    vpos = t2.matrix_world @ t2.data.vertices[vidx].co
    before = sum(g.weight for g in t2.data.vertices[vidx].groups)
    RigUtils.apply_falloff_weights(t2, "rd_test", vpos + Vector((0, 0, 1.0)), 4.0)  # d=1, r=4 -> w=0.75
    tgt = t2.vertex_groups["rd_test"].index
    gw = {g.group: g.weight for g in t2.data.vertices[vidx].groups}
    w_target = gw.get(tgt, 0.0)
    w_others = sum(v for k, v in gw.items() if k != tgt)
    check("b004: apply_falloff sets target weight = 1 - d/r (linear)", abs(w_target - 0.75) < 0.02,
          f"w={w_target:.3f} (expected 0.75)")
    check("b004: apply_falloff scales existing influences by (1-w) (redistribution, not addition)",
          abs(w_others - before * 0.25) < 0.02, f"others {before:.3f} -> {w_others:.3f} (want {before * 0.25:.3f})")

    # apply_falloff redistribution touches ONLY deform-bone groups — a cloth
    # pin / selection-set group inside the radius keeps its weights verbatim
    # (mayatk's skinPercent touches skinCluster influences only; pre-fix EVERY
    # vertex group was scaled by (1-w), corrupting non-skin data).
    pin = t2.vertex_groups.new(name="pin_group")
    pin.add([vidx], 0.8, "REPLACE")
    RigUtils.apply_falloff_weights(t2, "rd_test2", vpos + Vector((0, 0, 1.0)), 4.0)
    gw2 = {g.group: g.weight for g in t2.data.vertices[vidx].groups}
    check("b004: apply_falloff leaves non-deform groups untouched (pin keeps 0.8)",
          abs(gw2.get(pin.index, 0.0) - 0.8) < 1e-6, f"pin={gw2.get(pin.index)}")

    # b004 on a SPLINE-BUILT rig: the end CONTROL auto-resolves + routes through
    # the anchor (mirror of Maya's route-through-the-end-control). Pre-fix the
    # `control` parameter was dead — nothing ever passed it — so the falloff
    # blend dragged only near-end skin while the IK curve/control stayed put.
    reset()
    t4 = tube("AnchSpline", depth=8.0)
    b4 = TubeRig(t4, rig_name="asp").build("spline", num_joints=8, num_controls=3, radius=0.6)
    bpy.context.view_layer.update()
    mn4, mx4 = eval_bounds(t4)
    a_end2 = empty("A_end2", (0, 0, mx4.z))
    bpy.context.view_layer.update()
    r4 = TubeRig(t4, rig_name="asp")
    r4.constrain_end_with_falloff(b4.armature, b4.bones, a_end2, t4, falloff=3.0, bone_index=-1)
    end_ctrl4, start_ctrl4 = b4.controls[-1], b4.controls[0]
    check("b004 spline: end control auto-resolved + CHILD_OF-bound to the anchor",
          any(c.type == "CHILD_OF" and c.target is a_end2 for c in end_ctrl4.constraints),
          f"{[c.type for c in end_ctrl4.constraints]}")
    check("b004 spline: start control NOT bound (only the constrained end routes)",
          not any(c.type == "CHILD_OF" for c in start_ctrl4.constraints))
    # moving the anchor carries the whole end assembly (control -> hook -> IK)
    tip_idx4 = max((v.co.z, v.index) for v in t4.data.vertices)[1]
    p0 = eval_vert(t4, tip_idx4)
    a_end2.location.x += 4.0
    bpy.context.view_layer.update()
    p1 = eval_vert(t4, tip_idx4)
    check("b004 spline: moving the anchor carries the tube end with it",
          (p1 - p0).length > 2.0, f"shift={(p1 - p0).length:.2f}")

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
