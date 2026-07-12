"""Manual harness for the blendertk ShellXform panel slots (``blendertk/uv_utils/shell_xform.py``).

Requires a real Blender (it ``import bpy``), so it is **not** a CI/unittest target — the non-``test_``
name keeps it out of auto-discovery. Run it against a *fresh* Blender (never an existing session)::

    blender --background --factory-startup --python blendertk/test/shell_xform_slot_check.py

Drives the real ``ShellXformSlots`` op methods (align_* / linear_align / orient_shells /
gather_shells / randomize_shells) against live bmesh geometry with a stubbed switchboard. The
engine helpers themselves are unit-tested in ``test_uv_utils.py``; this proves the *slot* layer —
that each Align button is wired to the right axis + mode (a mislabeled ``align_v_min`` would move U,
which the engine test can't catch) and that the Orient/Gather/Randomize buttons dispatch correctly.
"""
import sys
import os
import math
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
    import mathutils
    import blendertk as btk  # noqa: F401 — the slot methods call btk.* + selected_objects
    from blendertk.uv_utils.shell_xform import ShellXformSlots

    def make_slot():
        """Instance without the UI-loading __init__ (headless: no loaded_ui / Qt)."""
        s = ShellXformSlots.__new__(ShellXformSlots)
        s.sb = NS(message_box=lambda *a, **k: None)
        return s

    slot = make_slot()

    def reset():
        if bpy.context.view_layer.objects.active and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def one_quad(uvs, name="Q"):
        b = bmesh.new()
        u = b.loops.layers.uv.new("UVMap")
        vv = [b.verts.new((dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
        fc = b.faces.new(vv)
        for loop, uv in zip(fc.loops, uvs):
            loop[u].uv = uv
        me = bpy.data.meshes.new(name)
        b.to_mesh(me); b.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        o.select_set(True); bpy.context.view_layer.objects.active = o
        return o

    def us(o):
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        r = [l[u].uv.x for f in b.faces for l in f.loops]; b.free(); return r

    def vs(o):
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        r = [l[u].uv.y for f in b.faces for l in f.loops]; b.free(); return r

    def enter_edit_select_all():
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.scene.tool_settings.use_uv_select_sync = True
        bpy.ops.mesh.select_all(action="SELECT")

    def rotate_uv_map(o, deg):
        """Rotate the object's whole UV map about its centroid (synthesizes a mis-oriented shell)."""
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        loops = [l for f in b.faces for l in f.loops]
        cu = sum(l[u].uv.x for l in loops) / len(loops)
        cv = sum(l[u].uv.y for l in loops) / len(loops)
        rot = mathutils.Matrix.Rotation(math.radians(deg), 2)
        for l in loops:
            d = rot @ (l[u].uv - mathutils.Vector((cu, cv)))
            l[u].uv = (cu + d.x, cv + d.y)
        b.to_mesh(o.data); b.free()

    # U loops = [.2,.6,.6,.2] (min .2, max .6, mean .4); V loops = [.5,.5,.9,.9] (min .5, max .9, mean .7)
    QUAD = [(0.2, 0.5), (0.6, 0.5), (0.6, 0.9), (0.2, 0.9)]

    # ---- Align buttons: each must hit the right axis + mode
    reset(); o = one_quad(QUAD); slot.align_u_min()
    check("slot align_u_min -> U all 0.2", all(abs(u - 0.2) < 1e-5 for u in us(o)), f"{set(round(u,3) for u in us(o))}")
    reset(); o = one_quad(QUAD); slot.align_u_max()
    check("slot align_u_max -> U all 0.6", all(abs(u - 0.6) < 1e-5 for u in us(o)))
    reset(); o = one_quad(QUAD); slot.align_u_avg()
    check("slot align_u_avg -> U all 0.4 (mean)", all(abs(u - 0.4) < 1e-5 for u in us(o)))
    reset(); o = one_quad(QUAD); slot.align_v_min()
    check("slot align_v_min -> V all 0.5", all(abs(v - 0.5) < 1e-5 for v in vs(o)))
    reset(); o = one_quad(QUAD); slot.align_v_max()
    check("slot align_v_max -> V all 0.9", all(abs(v - 0.9) < 1e-5 for v in vs(o)))
    reset(); o = one_quad(QUAD); slot.align_v_avg()
    check("slot align_v_avg -> V all 0.7 (mean)", all(abs(v - 0.7) < 1e-5 for v in vs(o)))
    # align must not touch the OTHER axis (guards an axis/component swap in _align)
    reset(); o = one_quad(QUAD); slot.align_u_min()
    check("slot align_u_min leaves V untouched", sorted(round(v, 3) for v in vs(o)) == [0.5, 0.5, 0.9, 0.9], f"{sorted(round(v,3) for v in vs(o))}")

    # ---- linear_align: zig-zag -> collinear
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.3), (0.8, 0.0), (1.2, -0.3)]); slot.linear_align()
    pts = list(zip(us(o), vs(o)))
    x0, y0 = pts[0]; xn, yn = pts[-1]; bx, by = xn - x0, yn - y0
    mc = max(abs((x - x0) * by - (y - y0) * bx) for x, y in pts)
    check("slot linear_align -> collinear", mc < 1e-5, f"maxcross={mc:.2e}")

    # ---- orient_shells: a 30deg-rotated 0.4x0.2 shell is re-squared
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    enter_edit_select_all()
    slot.orient_shells()
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    asp = (max(U) - min(U)) / (max(V) - min(V))
    check("slot orient_shells re-squares a rotated shell", asp > 1.7, f"aspect={asp:.2f}")

    # ---- orient_edges: EDGE method orients the shell to a selected edge (axis-aligned; the
    # picked edge runs along U or V, so the bbox aspect leaves the rotated ~1.2 for an extreme)
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(o.data); bm.edges.ensure_lookup_table()
    bm.edges[0].select = True
    bmesh.update_edit_mesh(o.data)
    slot.orient_edges()
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    asp = (max(U) - min(U)) / (max(V) - min(V))
    check("slot orient_edges orients to the selected edge (axis-aligned)", asp > 1.7 or asp < 0.6, f"aspect={asp:.2f}")

    # ---- gather_shells: a shell in tile (1,1) returns to 0-1
    reset()
    b = bmesh.new(); uvl = b.loops.layers.uv.new("UVMap")
    for n, (u0, v0, u1, v1) in enumerate(((0.1, 0.1, 0.4, 0.4), (1.2, 1.1, 1.5, 1.5))):
        x = n * 3.0
        vv = [b.verts.new((x + dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
        fc = b.faces.new(vv)
        for loop, (lu, lv) in zip(fc.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
            loop[uvl].uv = (lu, lv)
    me = bpy.data.meshes.new("G"); b.to_mesh(me); b.free()
    o = bpy.data.objects.new("G", me); bpy.context.collection.objects.link(o)
    o.select_set(True); bpy.context.view_layer.objects.active = o
    slot.gather_shells()
    U, V = us(o), vs(o)
    check("slot gather_shells -> all UVs inside 0-1", min(U) >= -1e-6 and max(U) <= 1.0 + 1e-6 and min(V) >= -1e-6 and max(V) <= 1.0 + 1e-6, f"U[{min(U):.2f},{max(U):.2f}] V[{min(V):.2f},{max(V):.2f}]")

    # ---- randomize_shells: the shell centroid moves off its origin
    reset(); o = one_quad([(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4)])
    enter_edit_select_all()
    slot.randomize_shells()
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    cx, cy = sum(U) / len(U), sum(V) / len(V)
    check("slot randomize_shells offsets the shell", (abs(cx - 0.25) + abs(cy - 0.25)) > 1e-4, f"centroid=({cx:.3f},{cy:.3f})")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
