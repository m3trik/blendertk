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

    # ---- move pad (b023-b026): scope combobox + snap toggle
    # The .ui ships no static item list — `cmb_move_scope_init` builds the combo
    # from this table with the step as item data. Its shape is therefore the
    # panel's item list; exactly one scope must carry the derive-from-selection
    # sentinel, or `_move_step` would read `bounds` for a fixed-step scope.
    # (The Qt-side population itself is checked in mayatk/test/shell_xform_ui_check.py.)
    SCOPES = ShellXformSlots._MOVE_SCOPES
    check("_MOVE_SCOPES item list", list(SCOPES) == ["Tile", "Half Tile", "Quarter Tile", "Selection Bounds"], f"{list(SCOPES)}")
    check("_MOVE_SCOPES has one derived scope", [k for k, v in SCOPES.items() if v is None] == ["Selection Bounds"])
    check("_MOVE_SCOPES fixed steps are positive", all(v > 0 for v in SCOPES.values() if v is not None))

    # `_move` reads the scope text and the cached snap toggle, both stubbed here —
    # no Qt in headless Blender, which is why `_snap_enabled` must not import it.
    def set_move_scope(text, snap=False):
        # Item data comes from the real `_MOVE_SCOPES` table (what
        # `cmb_move_scope_init` populates the combo from), so a renamed scope
        # raises here rather than silently testing a stale label.
        data = ShellXformSlots._MOVE_SCOPES[text]
        slot.ui = NS(cmb_move_scope=NS(currentText=lambda: text, currentData=lambda: data))
        slot._snap_toggle = NS(is_on=snap)

    # Scope = the distance one press travels. Shell sits at V [0.5, 0.9].
    for scope, expected in (("Tile", 1.0), ("Half Tile", 0.5), ("Quarter Tile", 0.25)):
        reset(); o = one_quad(QUAD); set_move_scope(scope)
        slot.b025()  # up
        check(f"slot b025 scope={scope} -> V +{expected}",
              all(abs(v - e) < 1e-5 for v, e in zip(sorted(vs(o)), sorted(x + expected for x in (0.5, 0.5, 0.9, 0.9)))),
              f"V={sorted(round(v,3) for v in vs(o))}")

    # Opposite arrows must cancel exactly (guards a sign/axis swap).
    reset(); o = one_quad(QUAD); set_move_scope("Half Tile")
    slot.b023(); slot.b026()  # left then right
    check("slot b023+b026 cancel", all(abs(u - e) < 1e-5 for u, e in zip(sorted(us(o)), sorted((0.2, 0.2, 0.6, 0.6)))), f"U={sorted(round(u,3) for u in us(o))}")
    slot.b024(); slot.b025()  # down then up
    check("slot b024+b025 cancel", all(abs(v - e) < 1e-5 for v, e in zip(sorted(vs(o)), sorted((0.5, 0.5, 0.9, 0.9)))), f"V={sorted(round(v,3) for v in vs(o))}")

    # Selection Bounds: the step is the shell's own size (0.4 x 0.4 here), so one
    # press puts the shell edge-to-edge with where it was.
    reset(); o = one_quad(QUAD); set_move_scope("Selection Bounds")
    slot.b026()  # right, by the 0.4-wide bounds
    check("slot b026 scope=Selection Bounds -> U +0.4 (own width)",
          all(abs(u - e) < 1e-5 for u, e in zip(sorted(us(o)), sorted(x + 0.4 for x in (0.2, 0.2, 0.6, 0.6)))),
          f"U={sorted(round(u,3) for u in us(o))}")

    # Snap ON: the user's case — an off-grid shell (V min 0.5) moving up on a
    # Half Tile grid lands its bottom edge on the next half line (0.5 -> 1.0),
    # not at 0.5 + 0.5 with the drift carried along. It lands one border margin
    # INSIDE the line, not on it — a shell flush against a tile seam bleeds
    # across it at render time.
    margin = slot._border_margin()
    reset(); o = one_quad([(0.2, 0.6), (0.6, 0.6), (0.6, 0.8), (0.2, 0.8)])
    set_move_scope("Half Tile", snap=True)
    slot.b025()
    check("slot b025 snap=on -> V min lands on the padded 0.5 grid", abs(min(vs(o)) - (1.0 + margin)) < 1e-5, f"Vmin={min(vs(o)):.4f} margin={margin:.6f}")
    slot.b024()  # back down: a full step, not stranded on the margin it just added
    check("slot b024 snap=on -> V min back to the padded 0.5", abs(min(vs(o)) - (0.5 + margin)) < 1e-5, f"Vmin={min(vs(o)):.4f}")

    # Snap OFF on the same shell keeps the 0.1 drift.
    reset(); o = one_quad([(0.2, 0.6), (0.6, 0.6), (0.6, 0.8), (0.2, 0.8)])
    set_move_scope("Half Tile", snap=False)
    slot.b025()
    check("slot b025 snap=off keeps sub-tile drift", abs(min(vs(o)) - 1.1) < 1e-5, f"Vmin={min(vs(o)):.4f}")

    # Snapping stays reversible from a padded position: padding the RESULT instead of
    # the anchor would strand the reverse press on the margin it just added (dead arrow).
    reset(); o = one_quad(QUAD); set_move_scope("Tile", snap=True)
    slot.b025()
    landed = min(vs(o))
    check("slot b025 snap=on lands one margin above the line", abs((landed % 1.0) - margin) < 1e-5, f"Vmin={landed:.6f}")
    slot.b025()
    check("a second press moves a full tile", abs(min(vs(o)) - (landed + 1.0)) < 1e-5, f"Vmin={min(vs(o)):.6f}")
    slot.b024()
    check("the reverse press returns exactly", abs(min(vs(o)) - landed) < 1e-5, f"Vmin={min(vs(o)):.6f}")

    # ---- gather_to_udim: the Gather button is wired to the engine and acts on the selection.
    # Two residents define the target tile (the majority vote), so only the stray travels —
    # with a lone shell there is by definition no stray, and the button correctly no-ops.
    reset()
    residents = [
        one_quad([(0.2, 0.2), (0.6, 0.2), (0.6, 0.6), (0.2, 0.6)], name="R1"),
        one_quad([(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)], name="R2"),
    ]
    stray = one_quad([(3.2, 2.2), (3.6, 2.2), (3.6, 2.6), (3.2, 2.6)], name="S")
    for r in residents:
        r.select_set(True)
    resident_before = [(min(us(r)), min(vs(r))) for r in residents]
    slot.gather_to_udim()
    check("slot gather_to_udim pulls the stray shell into the majority tile",
          abs(min(us(stray)) - 0.2) < 1e-5 and abs(min(vs(stray)) - 0.2) < 1e-5,
          f"U min={min(us(stray)):.4f} V min={min(vs(stray)):.4f}")
    check("slot gather_to_udim leaves the residents put",
          all(abs(min(us(r)) - bu) < 1e-5 and abs(min(vs(r)) - bv) < 1e-5
              for r, (bu, bv) in zip(residents, resident_before)),
          f"{[(round(min(us(r)),3), round(min(vs(r)),3)) for r in residents]}")

    reset()
    messages = []
    slot.sb = NS(message_box=lambda *a, **k: messages.append(a))
    slot.gather_to_udim()  # nothing selected
    check("slot gather_to_udim with nothing selected warns", bool(messages), f"messages={len(messages)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
