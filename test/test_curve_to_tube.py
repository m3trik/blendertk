"""blendertk CurveToTube (btk.CurveToTube engine + CurveToTubeSlots) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_curve_to_tube.py

Covers the engine — NURBS output (a live beveled curve; in-place vs duplicate per Keep History),
polygon output (a baked mesh with exactly `sections` sides via a bevel_object circle, ring density
from Path Res, caps, quads/triangulate), source-curve preservation, and the under-selection guard.
Also exercises CurveToTubeSlots.perform_operation routing (stub ui/sb) + Select Result.
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
    import blendertk as btk
    from blendertk.nurbs_utils.curve_to_tube import CurveToTube, CurveToTubeSlots
    from blendertk.nurbs_utils._nurbs_utils import NurbsUtils

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def make_curve(name="path"):
        # an open 4-point NURBS path with a bend
        return NurbsUtils.create_curve(
            [(0, 0, 0), (2, 0, 0), (4, 2, 0), (6, 2, 0)], name=name, kind="NURBS"
        )

    def mesh_count(obj):
        deps = bpy.context.evaluated_depsgraph_get()
        me = obj.evaluated_get(deps).to_mesh()
        try:
            return len(me.vertices), len(me.polygons)
        finally:
            obj.evaluated_get(deps).to_mesh_clear()

    # ---- NURBS output: a live beveled curve, source mutated in place (Keep History) ----------
    reset()
    src = make_curve("c")
    tubes = CurveToTube.create([src], output_type="nurbs", radius=0.5, sections=8, live=True)
    check("nurbs live returns one object", len(tubes) == 1)
    check("nurbs live result is a beveled CURVE", tubes[0].type == "CURVE", tubes[0].type)
    check("nurbs live bevels the SOURCE in place (it is the tube)", tubes[0] is src)
    # In-place source keeps its name — renaming it would make the Preview's name-based rollback
    # treat it as a new object (remove+recreate) instead of cleanly restoring its data.
    check("nurbs live does NOT rename the in-place source (Preview rollback safety)",
          tubes[0].name == "c", tubes[0].name)
    check("nurbs round bevel set with the radius",
          tubes[0].data.bevel_mode == "ROUND" and abs(tubes[0].data.bevel_depth - 0.5) < 1e-6)
    nv, _ = mesh_count(tubes[0])
    check("nurbs tube has swept geometry", nv > 0, f"verts={nv}")

    # ---- NURBS baked: a duplicate beveled curve, SOURCE preserved un-beveled -----------------
    reset()
    src = make_curve("c")
    tubes = CurveToTube.create([src], output_type="nurbs", radius=0.5, live=False)
    check("nurbs baked result is a curve, NOT the source", tubes[0].type == "CURVE" and tubes[0] is not src)
    check("nurbs baked preserves the source curve un-beveled",
          src.name in bpy.data.objects and src.data.bevel_depth == 0.0)

    # ---- Polygon output: a baked mesh with exactly `sections` sides --------------------------
    reset()
    src = make_curve("c")
    tubes = CurveToTube.create(
        [src], output_type="polygon", radius=0.5, sections=6, path_divisions=2, caps=False, quads=True
    )
    check("polygon result is a MESH", tubes[0].type == "MESH", tubes[0].type)
    poly = tubes[0]
    # a `sections`-gon tube body: vert count divides evenly by sections (one ring of N per step)
    check("polygon tube has exactly `sections` sides (verts % 6 == 0)",
          len(poly.data.vertices) % 6 == 0 and len(poly.data.vertices) > 0,
          f"verts={len(poly.data.vertices)}")
    check("polygon (no caps) baked to a mesh with faces", len(poly.data.polygons) > 0)
    check("polygon preserves the source curve (baked keeps it via live=False? no→consumed)",
          "c" not in bpy.data.objects)  # live=False consumes the source
    check("polygon bake leaves no orphan profile circle",
          not any(o.name.endswith("_profile") for o in bpy.data.objects))

    # ---- Path Res (resolution_u) increases ring density --------------------------------------
    reset()
    a = make_curve("a")
    t_lo = CurveToTube.create([a], output_type="polygon", sections=6, path_divisions=1)[0]
    lo = len(t_lo.data.vertices)
    reset()
    b = make_curve("b")
    t_hi = CurveToTube.create([b], output_type="polygon", sections=6, path_divisions=4)[0]
    hi = len(t_hi.data.vertices)
    check("higher Path Res → more rings (more verts)", hi > lo, f"{lo} -> {hi}")

    # ---- Caps add end faces -------------------------------------------------------------------
    reset()
    a = make_curve("a")
    no_caps = len(CurveToTube.create([a], output_type="polygon", sections=8, caps=False)[0].data.polygons)
    reset()
    b = make_curve("b")
    with_caps = len(CurveToTube.create([b], output_type="polygon", sections=8, caps=True)[0].data.polygons)
    check("caps add end faces", with_caps > no_caps, f"{no_caps} -> {with_caps}")

    # ---- Quads off → triangulated ------------------------------------------------------------
    reset()
    a = make_curve("a")
    tris = CurveToTube.create([a], output_type="polygon", sections=8, quads=False)[0]
    check("quads=False triangulates (every face a triangle)",
          all(len(p.vertices) == 3 for p in tris.data.polygons) and len(tris.data.polygons) > 0)

    # ---- polygon live keeps the source curve as the editable driver --------------------------
    reset()
    src = make_curve("driver")
    tubes = CurveToTube.create([src], output_type="polygon", sections=8, live=True)
    check("polygon live → mesh result", tubes[0].type == "MESH")
    check("polygon live keeps the source curve", "driver" in bpy.data.objects)

    # ---- guard: no curve selected raises ------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    raised = False
    try:
        CurveToTube.create([cube])
    except RuntimeError:
        raised = True
    check("non-curve selection raises RuntimeError", raised)

    # ---- CurveToTubeSlots.perform_operation routing ------------------------------------------
    class _W:
        def __init__(self, v):
            self.v = v
        def value(self):
            return self.v
        def isChecked(self):
            return self.v
        def currentData(self):
            return self.v

    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"curve_to_tube": ui})()
            self.messages = []
        def message_box(self, msg):
            self.messages.append(msg)

    reset()
    src = make_curve("sc")
    src.select_set(True)
    bpy.context.view_layer.objects.active = src
    ui = type("U", (), {})()
    ui.cmb000 = _W("polygon")
    ui.s000, ui.s001, ui.s002, ui.s003 = _W(0.5), _W(8), _W(1), _W(3)
    ui.chk001, ui.chk003, ui.chk004 = _W(False), _W(False), _W(True)
    ui.cmb_topology = _W("quads")  # Quads/Triangles combo (replaced the chk002 'Quads' checkbox)
    slots = CurveToTubeSlots.__new__(CurveToTubeSlots)  # bypass Qt-heavy __init__
    slots.sb = _SB(ui)
    slots.ui = ui
    slots.last_tubes = []
    slots.perform_operation([src])
    check("slot perform_operation builds a tube", len(slots.last_tubes) == 1 and slots.last_tubes[0].type == "MESH")
    check("slot Select Result selects the tube",
          slots.last_tubes[0].select_get() and bpy.context.view_layer.objects.active is slots.last_tubes[0])

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
