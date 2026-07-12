"""Manual harness for the blendertk TubeRig granular-step slots (``tube_rig.py`` TubeRigSlots).

Requires a real Blender (it ``import bpy``), so it is **not** a CI/unittest target — the non-``test_``
name keeps it out of auto-discovery. Run against a *fresh* Blender (never an existing session)::

    blender --background --factory-startup --python blendertk/test/tube_rig_slot_check.py

Drives the real b001 (Create Joints) → b002 (Create IK/Controls on the existing chain) → b003 (Bind)
slot methods with a stubbed switchboard, resolving each step from the live Blender selection exactly
as the buttons do — then proves the granular-built rig DEFORMS (move a control → evaluated mesh bends).
The engine is unit-tested in test_tube_rig.py; this proves the slot layer's selection resolution +
step wiring (e.g. b002 reading the deform toggles, chk000 reverse).
"""
import sys
import os
import traceback
from types import SimpleNamespace as NS

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
    import bmesh
    from mathutils import Vector
    from blendertk.rig_utils.tube_rig import TubeRigSlots

    def make_slot(reverse=False, rig_name="hose", opts=None):
        """Instance without the UI-loading __init__; stub what the step slots read. ``_collect_opts``
        is stubbed (not the real widget reader — that needs Qt/uitk, absent headless, and isn't what
        this harness tests); an empty dict makes the slots fall back to their per-key defaults."""
        s = TubeRigSlots.__new__(TubeRigSlots)
        s.sb = NS(message_box=lambda *a, **k: None)
        s.ui = NS(
            chk000=NS(isChecked=lambda r=reverse: r),
            txt000=NS(text=lambda n=rig_name: n),
        )
        s._collect_opts = lambda o=dict(opts or {}): dict(o)
        return s

    def reset():
        if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def tube(name="Tube", depth=8.0):
        me = bpy.data.meshes.new(f"{name}_m")
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.5, radius2=0.5, depth=depth)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=10, use_grid_fill=False)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        bpy.context.view_layer.update()
        return o

    def select_only(objs, active=None):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)

    def an_armature():
        return next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)

    def eval_maxx(obj):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        m = ev.to_mesh()
        try:
            mw = obj.matrix_world
            return max((mw @ v.co).x for v in m.vertices)
        finally:
            ev.to_mesh_clear()

    # ---- Step 1: b001 creates joints from the selected mesh
    reset()
    t = tube("Hose", depth=8.0)
    select_only([t])
    slot = make_slot()
    slot.b001()
    arm = an_armature()
    check("b001 creates a joint chain from the selected mesh", arm is not None and len(arm.data.bones) == 11,
          f"bones={len(arm.data.bones) if arm else 0}")
    check("b001 leaves the mesh UNBOUND (no armature modifier yet)",
          not any(m.type == "ARMATURE" for m in t.modifiers))

    # ---- Step 2: b002 adds Spline IK + controls onto the selected armature
    select_only([arm], active=arm)
    slot.b002()
    tip = arm.pose.bones[slot._ordered_chain(arm)[-1]]
    check("b002 adds a SPLINE_IK constraint on the tip bone",
          any(c.type == "SPLINE_IK" for c in tip.constraints))
    controls = [o for o in bpy.data.objects if "_ctrl_" in o.name]
    check("b002 builds the hooked controls", len(controls) == 3, f"controls={len(controls)}")

    # ---- Step 3: b003 binds the mesh to the armature (both selected)
    select_only([t, arm], active=arm)
    slot.b003()
    check("b003 binds the mesh (Armature modifier -> the chain)",
          any(m.type == "ARMATURE" and m.object is arm for m in t.modifiers))

    # ---- the granular-built rig DEFORMS when a control moves (the real invariant)
    x0 = eval_maxx(t)
    mid = controls[len(controls) // 2]
    mid.location.x += 4.0
    bpy.context.view_layer.update()
    x1 = eval_maxx(t)
    check("granular slot workflow builds a rig that DEFORMS (control move bends the mesh)",
          x1 > x0 + 1.0, f"max-x {x0:.2f} -> {x1:.2f}")

    # ---- Step 4: b004 constrains both ends to two anchor objects (falloff blend). Proves the SLOT's
    # selection resolution (armature + 2 anchors + bound-mesh lookup + nearest-end assignment), on the
    # already-bound rig from steps 1-3. The engine falloff math is unit-tested in test_tube_rig.py.
    def mk_empty(name, loc):
        e = bpy.data.objects.new(name, None)
        e.location = loc
        bpy.context.collection.objects.link(e)
        return e

    bpy.context.view_layer.update()
    db = arm.data.bones
    ch = slot._ordered_chain(arm)
    a_lo = mk_empty("Anchor_lo", arm.matrix_world @ db[ch[0]].head_local)
    a_hi = mk_empty("Anchor_hi", arm.matrix_world @ db[ch[-1]].tail_local)
    bpy.context.view_layer.update()
    # select the MESH too (the realistic case) — b004 must NOT mistake it for an anchor
    select_only([t, arm, a_lo, a_hi], active=arm)
    slot.b004()
    anchor_bones = [b for b in arm.data.bones if "_anchor_" in b.name]
    check("b004 grafts one anchor bone per end (mesh selected too, not mistaken for an anchor)",
          len(anchor_bones) == 2, f"anchor bones={len(anchor_bones)}")
    xb0 = eval_maxx(t)
    a_hi.location.x += 6.0                       # drag the tip anchor sideways
    bpy.context.view_layer.update()
    xb1 = eval_maxx(t)
    check("b004 anchor drives the bound mesh (moving an anchor deforms the near end)", xb1 > xb0 + 1.0,
          f"max-x {xb0:.2f} -> {xb1:.2f}")

    # ---- chk000 reverse: Step 1 with Reverse Direction flips the chain start
    reset()
    t = tube("Fwd", depth=8.0)
    select_only([t])
    make_slot(reverse=False, rig_name="fwd").b001()
    head_fwd = an_armature().data.bones[0].head_local.z
    reset()
    t = tube("Rev", depth=8.0)
    select_only([t])
    make_slot(reverse=True, rig_name="rev").b001()
    head_rev = an_armature().data.bones[0].head_local.z
    check("chk000 Reverse Direction flips the joint-chain start", abs(head_fwd - head_rev) > 1.0,
          f"forward head z={head_fwd:.2f} reverse head z={head_rev:.2f}")

    # ---- guards: b001 with nothing selected, b003 with only the mesh -> messaged, no crash
    reset()
    warned = []
    s = TubeRigSlots.__new__(TubeRigSlots)
    s.sb = NS(message_box=lambda *a, **k: warned.append(a[0] if a else ""))
    s.ui = NS(chk000=NS(isChecked=lambda: False), txt000=NS(text=lambda: ""))
    s._collect_opts = lambda: {}
    bpy.ops.object.select_all(action="DESELECT")
    s.b001()
    check("b001 with nothing selected -> message, no crash", len(warned) == 1)
    t = tube("Only", depth=6.0)
    select_only([t])
    s.b003()
    check("b003 with no armature selected -> message, no crash", len(warned) == 2)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
