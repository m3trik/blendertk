"""Manual harness for blendertk's UV shell tools (``UvUtils``): the operations tentacle's Shell
Xform panel calls, with the arguments its buttons pass.

Requires a real Blender (it ``import bpy``), so it is **not** a CI/unittest target — the non-``test_``
name keeps it out of auto-discovery. Run it against a *fresh* Blender (never an existing session)::

    blender --background --factory-startup --python blendertk/test/uv_shell_tools_check.py

Drives each tool against live bmesh geometry: every Align axis + mode (a swapped axis would move U
for V), Orient (``to_edge``), Gather, Randomize, Distribute's ``spacing`` row, Rotate about the UV
centroid, the Flip call, ``move_to_uv_space``, Gather to Tile and the neighbour pool the move pad's
shell snap parks against, and that each tool a panel button calls is one undo step. The panel's own
wiring (which button passes what, the move pad's scope and snap math) is
``tentacle/test/test_shell_xform_panel.py``; the engine basics are ``test_uv_utils.py``.
"""
import sys
import os
import math
import traceback

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
    import blendertk as btk

    UV = btk.UvUtils

    def sel():
        """The selection, as the panel passes it."""
        return btk.CoreUtils.selected_objects()

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

    def quads_object(boxes, name="R"):
        """One mesh whose separate quads (one island each) carry the UV *boxes*
        ``(u0, v0, u1, v1)``; selected and active."""
        b = bmesh.new(); uvl = b.loops.layers.uv.new("UVMap")
        for n, (u0, v0, u1, v1) in enumerate(boxes):
            x = n * 3.0
            vv = [b.verts.new((x + dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
            fc = b.faces.new(vv)
            for loop, (lu, lv) in zip(fc.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
                loop[uvl].uv = (lu, lv)
        me = bpy.data.meshes.new(name); b.to_mesh(me); b.free()
        o = bpy.data.objects.new(name, me); bpy.context.collection.objects.link(o)
        o.select_set(True); bpy.context.view_layer.objects.active = o
        return o

    def us(o):
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        r = [l[u].uv.x for f in b.faces for l in f.loops]; b.free(); return r

    def vs(o):
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        r = [l[u].uv.y for f in b.faces for l in f.loops]; b.free(); return r

    def face_boxes(o):
        """Each face's UV box ``(u0, v0, u1, v1)`` (one island per face here)."""
        b = bmesh.new(); b.from_mesh(o.data); u = b.loops.layers.uv.active
        boxes = []
        for f in b.faces:
            fu = [lp[u].uv.x for lp in f.loops]; fv = [lp[u].uv.y for lp in f.loops]
            boxes.append((min(fu), min(fv), max(fu), max(fv)))
        b.free(); return boxes

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

    # ---- one undo step per click: every tool a panel button calls is CoreUtils.undoable
    for name in ("move_to_uv_space", "gather_to_udim", "mirror_uvs", "rotate_uvs",
                 "straighten_uvs", "straighten_uv_shells", "distribute_uv_shells", "align_uvs",
                 "orient_uv_shells", "gather_uv_shells", "randomize_uv_shells"):
        check(f"{name} is one undo step", hasattr(getattr(UV, name), "__wrapped__"))

    # ---- Align: each axis + mode the panel's buttons pass
    for axis, mode, expected, read in (
        ("u", "min", 0.2, us), ("u", "max", 0.6, us), ("u", "avg", 0.4, us),
        ("v", "min", 0.5, vs), ("v", "max", 0.9, vs), ("v", "avg", 0.7, vs),
    ):
        reset(); o = one_quad(QUAD); UV.align_uvs(sel(), axis=axis, mode=mode)
        check(f"align_uvs {axis} {mode} -> {axis.upper()} all {expected}",
              all(abs(x - expected) < 1e-5 for x in read(o)),
              f"{sorted(set(round(x, 3) for x in read(o)))}")
    # align must not touch the OTHER axis (guards an axis/component swap)
    reset(); o = one_quad(QUAD); UV.align_uvs(sel(), axis="u", mode="min")
    check("align_uvs u min leaves V untouched", sorted(round(v, 3) for v in vs(o)) == [0.5, 0.5, 0.9, 0.9], f"{sorted(round(v,3) for v in vs(o))}")

    # ---- linear: zig-zag -> collinear
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.3), (0.8, 0.0), (1.2, -0.3)]); UV.align_uvs(sel(), axis="u", mode="linear")
    pts = list(zip(us(o), vs(o)))
    x0, y0 = pts[0]; xn, yn = pts[-1]; bx, by = xn - x0, yn - y0
    mc = max(abs((x - x0) * by - (y - y0) * bx) for x, y in pts)
    check("align_uvs linear -> collinear", mc < 1e-5, f"maxcross={mc:.2e}")

    # ---- orient_shells (mayatk's name): a 30deg-rotated 0.4x0.2 shell is re-squared
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    enter_edit_select_all()
    oriented = UV.orient_shells(sel())
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    asp = (max(U) - min(U)) / (max(V) - min(V))
    check("orient_shells re-squares a rotated shell", oriented and asp > 1.7, f"aspect={asp:.2f}")

    # ---- orient_shells to_edge: the EDGE method orients the shell to a selected edge
    reset(); o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(o.data); bm.edges.ensure_lookup_table()
    bm.edges[0].select = True
    bmesh.update_edit_mesh(o.data)
    UV.orient_shells(sel(), to_edge=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    asp = (max(U) - min(U)) / (max(V) - min(V))
    check("orient_shells to_edge orients to the selected edge (axis-aligned)", asp > 1.7 or asp < 0.6, f"aspect={asp:.2f}")
    reset(); one_quad(QUAD)  # Object Mode: the operator has no edit mesh to act on
    check("orient_shells outside Edit Mode reports nothing done", UV.orient_shells(sel()) == 0)

    # ---- gather_uv_shells: a shell in tile (1,1) returns to 0-1
    reset(); o = quads_object([(0.1, 0.1, 0.4, 0.4), (1.2, 1.1, 1.5, 1.5)], name="G")
    UV.gather_uv_shells(sel())
    U, V = us(o), vs(o)
    check("gather_uv_shells -> all UVs inside 0-1", min(U) >= -1e-6 and max(U) <= 1.0 + 1e-6 and min(V) >= -1e-6 and max(V) <= 1.0 + 1e-6, f"U[{min(U):.2f},{max(U):.2f}] V[{min(V):.2f},{max(V):.2f}]")

    # ---- randomize_uv_shells: the shell centroid moves off its origin
    reset(); o = one_quad([(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4)])
    enter_edit_select_all()
    UV.randomize_uv_shells(sel(), seed=0)
    bpy.ops.object.mode_set(mode="OBJECT")
    U, V = us(o), vs(o)
    cx, cy = sum(U) / len(U), sum(V) / len(V)
    check("randomize_uv_shells offsets the shell", (abs(cx - 0.25) + abs(cy - 0.25)) > 1e-4, f"centroid=({cx:.3f},{cy:.3f})")

    # ---- move_to_uv_space (mayatk's name): relative by (u, v); absolute onto the point
    reset(); o = one_quad(QUAD)
    UV.move_to_uv_space(sel(), 1.0, -0.5)
    check("move_to_uv_space shifts by (u, v)",
          sorted(round(u, 4) for u in us(o)) == [1.2, 1.2, 1.6, 1.6]
          and sorted(round(v, 4) for v in vs(o)) == [0.0, 0.0, 0.4, 0.4],
          f"U={sorted(round(u, 4) for u in us(o))} V={sorted(round(v, 4) for v in vs(o))}")
    UV.move_to_uv_space(sel(), 0.25, 0.75, relative=False)
    check("move_to_uv_space relative=False puts every UV on the point",
          all(abs(u - 0.25) < 1e-6 for u in us(o)) and all(abs(v - 0.75) < 1e-6 for v in vs(o)))

    # ---- rotate_uvs: about the centroid of the UV points, counter-clockwise, reversible
    reset(); o = one_quad([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    start = list(zip(us(o), vs(o)))
    n = UV.rotate_uvs(sel(), 90.0)
    turned = list(zip(us(o), vs(o)))
    check("rotate_uvs returns the UV points it turned", n == 4, f"{n}")
    check("rotate_uvs 90 turns counter-clockwise: (1, 0) -> (1, 1)",
          abs(turned[1][0] - 1.0) < 1e-5 and abs(turned[1][1] - 1.0) < 1e-5, f"{turned[1]}")
    UV.rotate_uvs(sel(), -90.0)
    back = list(zip(us(o), vs(o)))
    check("rotate_uvs -90 turns it back",
          all(abs(a - c) < 1e-5 and abs(b - d) < 1e-5 for (a, b), (c, d) in zip(back, start)), f"{back}")
    # The pivot is the points' centroid (1.0, 0.5), not the box center (1.5, 0.5).
    reset(); o = one_quad([(0.0, 0.0), (3.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    UV.rotate_uvs(sel(), 180.0)
    check("rotate_uvs pivots on the UV centroid, as Maya's",
          abs(us(o)[0] - 2.0) < 1e-5 and abs(vs(o)[0] - 1.0) < 1e-5, f"({us(o)[0]:.4f}, {vs(o)[0]:.4f})")

    # ---- the Flip call (b034 / b035): each island mirrors about its own center
    reset(); o = quads_object([(0.0, 0.0, 0.2, 0.4), (1.0, 0.0, 1.4, 0.2)], name="F")
    before = face_boxes(o)
    UV.mirror_uvs(sel(), axis="u", per_shell=True, preserve_position=False)
    check("Flip keeps each island's box",
          all(max(abs(a - b) for a, b in zip(x, y)) < 1e-5 for x, y in zip(face_boxes(o), before)),
          f"{face_boxes(o)}")
    check("Flip mirrors the island (its first corner crosses to the other side)",
          abs(us(o)[0] - 0.2) < 1e-5, f"u={us(o)[0]:.4f}")

    # ---- distribute_uv_shells spacing: Maya's texDistributeShells row
    BOXES = [(0.0, 0.0, 0.2, 0.2), (1.0, 0.0, 1.4, 0.3), (3.0, 0.5, 3.1, 0.6)]
    reset(); o = quads_object(BOXES, name="D")
    moved = UV.distribute_uv_shells(sel(), axis="u", spacing=0.01)
    boxes = sorted(face_boxes(o))
    gaps = [boxes[i + 1][0] - boxes[i][2] for i in range(len(boxes) - 1)]
    check("distribute spacing: a row along U, the spacing between neighbours",
          len(gaps) == 2 and all(abs(g - 0.01) < 1e-5 for g in gaps), f"gaps={gaps}")
    check("distribute spacing: the row shares one V center",
          len({round((b[1] + b[3]) / 2.0, 4) for b in boxes}) == 1, f"{boxes}")
    widest = max(boxes, key=lambda b: b[2] - b[0])
    check("distribute spacing: the widest island leads, centered on the combined box",
          widest == boxes[0] and abs((widest[0] + widest[2]) / 2.0 - 1.55) < 1e-5, f"{widest}")
    check("distribute spacing returns the islands moved", moved == 3, f"{moved}")

    reset(); o = quads_object([(0.0, 0.0, 0.2, 0.2), (0.0, 1.0, 0.3, 1.4), (0.5, 3.0, 0.6, 3.1)], name="DV")
    UV.distribute_uv_shells(sel(), axis="v", spacing=0.01)
    boxes = sorted(face_boxes(o), key=lambda b: -b[3])  # top to bottom
    gaps = [boxes[i][1] - boxes[i + 1][3] for i in range(len(boxes) - 1)]
    check("distribute spacing: a column down V, the spacing between neighbours",
          len(gaps) == 2 and all(abs(g - 0.01) < 1e-5 for g in gaps), f"gaps={gaps}")

    # Without a spacing it keeps its even spread: the end islands stay, the middle one centers.
    reset(); o = quads_object(BOXES, name="DE")
    UV.distribute_uv_shells(sel(), axis="u")
    centers = sorted((b[0] + b[2]) / 2.0 for b in face_boxes(o))
    check("distribute without spacing keeps the even spread",
          abs(centers[0] - 0.1) < 1e-5 and abs(centers[2] - 3.05) < 1e-5 and abs(centers[1] - 1.575) < 1e-5,
          f"{centers}")

    # ---- gather_to_udim: the stray shell joins the majority tile; the residents stay put
    reset()
    residents = [
        one_quad([(0.2, 0.2), (0.6, 0.2), (0.6, 0.6), (0.2, 0.6)], name="R1"),
        one_quad([(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)], name="R2"),
    ]
    stray = one_quad([(3.2, 2.2), (3.6, 2.2), (3.6, 2.6), (3.2, 2.6)], name="S")
    resident_before = [(min(us(r)), min(vs(r))) for r in residents]
    UV.gather_to_udim(sel(), map_size=4096)
    check("gather_to_udim pulls the stray shell into the majority tile",
          abs(min(us(stray)) - 0.2) < 1e-5 and abs(min(vs(stray)) - 0.2) < 1e-5,
          f"U min={min(us(stray)):.4f} V min={min(vs(stray)):.4f}")
    check("gather_to_udim leaves the residents put",
          all(abs(min(us(r)) - bu) < 1e-5 and abs(min(vs(r)) - bv) < 1e-5
              for r, (bu, bv) in zip(residents, resident_before)),
          f"{[(round(min(us(r)),3), round(min(vs(r)),3)) for r in residents]}")

    # ---- the move pad's shell-snap pool: neighbours share the UV layer name, never the mover
    reset(); o = one_quad(QUAD, name="M")
    check("alone in its UV layer -> no blockers", UV.get_neighbor_shell_bounds([o]) == [])
    reset()
    other = one_quad([(0.2, 3.0), (0.6, 3.0), (0.6, 3.4), (0.2, 3.4)], name="N")
    other.select_set(False)
    other.data.uv_layers.active.name = "lightmap"
    o = one_quad(QUAD, name="M")
    check("a differently-named UV layer is not a neighbour",
          UV.get_neighbor_shell_bounds([o]) == [], f"{UV.get_neighbor_shell_bounds([o])}")
    other.data.uv_layers.active.name = "UVMap"
    check("renamed back, it counts again", len(UV.get_neighbor_shell_bounds([o])) == 1)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
