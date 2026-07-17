"""blendertk target_weld (btk.target_weld / TargetWeld) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_target_weld.py

Covers the pure screen-space picking math (projection, radius/front-most pick, dash
geometry, merge position), the BMesh weld (edge-connected + unconnected pairs, at-target
vs at-center), the pick-cache hidden-vert exclusion, lazy operator registration, and the
headless activation guard. The interactive gesture itself (modal event loop, viewport
drawing) requires a windowed Blender and is exercised live, not here.
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
    import types
    import numpy as np
    import bpy
    import bmesh
    import blendertk as btk
    from blendertk.edit_utils import target_weld as tw

    # ---- surface: btk.target_weld / btk.TargetWeld resolve --------------------------------
    check("btk.target_weld resolves", callable(getattr(btk, "target_weld", None)))
    check("btk.TargetWeld resolves", getattr(btk, "TargetWeld", None) is not None)

    # ---- pure math: project_points ---------------------------------------------------------
    identity = np.eye(4)
    xy, depth = tw.project_points(identity, [(0.0, 0.0, 0.5)], 200, 100)
    check("project_points maps NDC origin to region center",
          np.allclose(xy[0], (100.0, 50.0)), f"xy={xy[0]}")
    behind = np.eye(4); behind[3, :] = (0, 0, 0, -1.0)  # forces w < 0
    xy_b, depth_b = tw.project_points(behind, [(0.0, 0.0, 0.0)], 200, 100)
    check("project_points flags behind-camera as NaN/inf",
          np.isnan(xy_b[0]).all() and np.isinf(depth_b[0]))

    # ---- pure math: pick_screen_point ------------------------------------------------------
    pts = np.array([(10.0, 10.0), (50.0, 50.0), (52.0, 50.0)])
    dep = np.array([0.5, 0.5, 0.5])
    check("pick: nearest within radius wins",
          tw.pick_screen_point((49, 50), pts, dep, radius=14) == 1)
    check("pick: nothing within radius -> None",
          tw.pick_screen_point((150, 150), pts, dep, radius=14) is None)
    dep_front = np.array([0.5, 0.9, 0.1])  # idx 2 nearer the viewer
    check("pick: front-most wins over screen-nearest",
          tw.pick_screen_point((49, 50), pts, dep_front, radius=14) == 2)
    check("pick: exclude removes the armed source",
          tw.pick_screen_point((49, 50), pts, dep, radius=14, exclude=1) == 2)
    check("pick: empty input -> None",
          tw.pick_screen_point((0, 0), np.empty((0, 2)), np.empty(0)) is None)

    # ---- pure math: weld_position / dash_segments ------------------------------------------
    check("weld_position at target",
          tw.weld_position((0, 0, 0), (2, 4, 6)) == (2.0, 4.0, 6.0))
    check("weld_position at center",
          tw.weld_position((0, 0, 0), (2, 4, 6), merge_to_center=True) == (1.0, 2.0, 3.0))
    dashes = tw.dash_segments((0, 0), (100, 0), dash=6, gap=4)
    check("dash_segments returns paired endpoints",
          len(dashes) >= 2 and len(dashes) % 2 == 0, f"n={len(dashes)}")
    check("dash_segments spans the full run",
          dashes[0] == (0.0, 0.0) and abs(dashes[-1][0] - 100.0) <= 6.0, f"end={dashes[-1]}")
    check("dash_segments zero-length -> empty", tw.dash_segments((5, 5), (5, 5)) == [])
    check("prompt text reflects mode",
          "merge at target" in tw._prompt_text(
              types.SimpleNamespace(_notice="", _source=None, merge_to_center=False)))

    # ---- bmesh weld: edge-connected pair, at target ----------------------------------------
    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def edit_cube():
        bpy.ops.mesh.primitive_cube_add(size=2)
        obj = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        return obj, bm

    reset()
    obj, bm = edit_cube()
    v_src, v_tgt = bm.edges[0].verts
    tgt_co = v_tgt.co.copy()
    tw.weld_pair(bm, v_src, v_tgt, merge_to_center=False)
    check("edge-connected weld removes one vert", len(bm.verts) == 7, f"verts={len(bm.verts)}")
    check("welded vert sits at the target",
          any((v.co - tgt_co).length < 1e-6 for v in bm.verts))

    # ---- bmesh weld: edge-connected pair, at center ----------------------------------------
    reset()
    obj, bm = edit_cube()
    v_src, v_tgt = bm.edges[0].verts
    mid = (v_src.co + v_tgt.co) * 0.5
    tw.weld_pair(bm, v_src, v_tgt, merge_to_center=True)
    check("center weld removes one vert", len(bm.verts) == 7)
    check("center weld sits at the midpoint",
          any((v.co - mid).length < 1e-6 for v in bm.verts))

    # ---- bmesh weld: unconnected pair (opposite cube corners) ------------------------------
    reset()
    obj, bm = edit_cube()
    v_src = next(v for v in bm.verts if (v.co - type(v.co)((-1, -1, -1))).length < 1e-6)
    v_tgt = next(v for v in bm.verts if (v.co - type(v.co)((1, 1, 1))).length < 1e-6)
    tw.weld_pair(bm, v_src, v_tgt, merge_to_center=False)
    check("unconnected weld removes one vert", len(bm.verts) == 7)

    # ---- pick cache: hidden verts excluded, rebuild on invalidation ------------------------
    reset()
    obj, bm = edit_cube()
    op = types.SimpleNamespace(_caches={})
    cache = tw._cache_for(op, obj)
    check("cache holds all 8 visible verts", len(cache["coords"]) == 8)
    bm.verts[0].hide = True
    op._caches.clear()
    cache = tw._cache_for(op, obj)
    check("hidden vert excluded from the pick cache",
          len(cache["coords"]) == 7 and 0 not in cache["index"].tolist())
    cached_again = tw._cache_for(op, obj)
    check("cache is reused while valid", cached_again is cache)

    # ---- modal navigation contract: nothing the camera needs is ever consumed ---------------
    # Maya keeps Alt tumble/track/dolly live inside a tool context, and Maya-style Blender
    # keymaps (Industry Compatible / Alt-nav) bind navigation to Alt+LMB/MMB/RMB — so every
    # Alt-modified event must pass through (a consumed Alt+LMB kills tumbling; a consumed
    # Alt+RMB would EXIT the tool mid-dolly). Plain MMB/wheel pass as before.
    reset()
    obj, bm = edit_cube()

    def op_stub():
        return types.SimpleNamespace(
            merge_to_center=False, _caches={}, _source=None, _hover=None,
            _notice="", _welds=0, _mouse=(0, 0), _region=None, _handle=None)

    def ev(etype, value="PRESS", alt=False):
        return types.SimpleNamespace(type=etype, value=value, alt=alt,
                                     mouse_region_x=0, mouse_region_y=0)

    op = op_stub()
    check("modal: Alt+LMB (tumble) passes through",
          tw._op_modal(op, bpy.context, ev("LEFTMOUSE", alt=True)) == {"PASS_THROUGH"})
    check("modal: Alt+RMB (dolly) passes through instead of exiting",
          tw._op_modal(op, bpy.context, ev("RIGHTMOUSE", alt=True)) == {"PASS_THROUGH"})
    check("modal: MMB orbit passes through",
          tw._op_modal(op, bpy.context, ev("MIDDLEMOUSE")) == {"PASS_THROUGH"})
    check("modal: wheel zoom passes through",
          tw._op_modal(op, bpy.context, ev("WHEELUPMOUSE")) == {"PASS_THROUGH"})
    # Alt mid-drag hands the gesture to the camera: the armed source must be dropped (a
    # passed-through Alt+LMB release would otherwise leave a ghost rubber band), while
    # plain-MMB nav keeps the armed source (its anchor re-projects) but drops the hover
    # highlight (cached screen position goes stale as the view moves).
    hit = {"obj": obj.name, "index": 0, "co": (0.0, 0.0, 0.0), "xy": (0.0, 0.0), "depth": 0.0}
    op._source, op._hover = dict(hit), dict(hit)
    check("modal: Alt-nav abandons the in-flight drag (no ghost rubber band)",
          tw._op_modal(op, bpy.context, ev("LEFT_ALT", alt=True)) == {"PASS_THROUGH"}
          and op._source is None and op._hover is None)
    op._source, op._hover = dict(hit), dict(hit)
    check("modal: MMB nav drops the stale hover but keeps the armed source",
          tw._op_modal(op, bpy.context, ev("MIDDLEMOUSE")) == {"PASS_THROUGH"}
          and op._source is not None and op._hover is None)
    op._source = None

    check("modal: plain Esc still exits (CANCELLED with no welds)",
          tw._op_modal(op, bpy.context, ev("ESC")) == {"CANCELLED"})

    # ---- operator registration (lazy + idempotent) -----------------------------------------
    tw._ensure_operator()
    check("operator registers", hasattr(bpy.types, "BTK_OT_target_weld"))
    tw._ensure_operator()  # second call must be a no-op
    check("re-registration is idempotent", hasattr(bpy.types, "BTK_OT_target_weld"))

    # ---- activation guard: background mode raises cleanly ----------------------------------
    # (--background still has a VIEW_3D area in its layout, so the guard must key off
    # bpy.app.background, not the area scan — a modal invoke there CANCELs silently.)
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2)
    raised = False
    try:
        tw.target_weld()
    except RuntimeError:
        raised = True
    check("headless activation raises RuntimeError (background session)", raised)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
