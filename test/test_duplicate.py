"""blendertk duplicate_linear / duplicate_radial / duplicate_grid headless test.
Run: blender --background --factory-startup --python blendertk/test/test_duplicate.py
"""
import sys, os, math, traceback

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

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube_at(x=0.0, y=0.0, z=0.0, size=2.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(x, y, z))
        return bpy.context.active_object

    # ---- linear: count, last copy gets the full offset, linear spacing
    reset()
    o = cube_at()
    out = btk.duplicate_linear(o, 4, translate=(8, 0, 0), calculation_mode="linear")
    copies = out[o]
    check("linear makes 4 copies", len(copies) == 4)
    xs = sorted(c.matrix_world.translation.x for c in copies)
    expect = [2.0, 4.0, 6.0, 8.0]  # x=(i+1)/total — first copy is already offset
    check("linear positions ramp to the full offset",
          all(abs(a - b) < 1e-4 for a, b in zip(xs, expect)), f"{[round(x,2) for x in xs]}")

    # ---- linear instance vs copy
    check("linear instance shares data", all(c.data is o.data for c in copies))
    reset()
    o = cube_at()
    out = btk.duplicate_linear(o, 2, translate=(4, 0, 0), calculation_mode="linear", instance=False)
    check("linear copy makes unique data", all(c.data is not o.data for c in out[o]))

    # ---- linear rotate orbits about the pivot (world): a copy at full 180° about world Z
    reset()
    o = cube_at(x=2.0)
    out = btk.duplicate_linear(o, 1, rotate=(0, 0, 180), pivot="world", calculation_mode="linear")
    c = out[o][0]
    t = c.matrix_world.translation
    check("linear 180-deg world orbit lands at -x", abs(t.x + 2.0) < 1e-4 and abs(t.y) < 1e-4,
          f"({t.x:.2f},{t.y:.2f})")

    # ---- linear scale ramp (end copy = full scale factor)
    reset()
    o = cube_at()
    out = btk.duplicate_linear(o, 2, scale=(2, 2, 2), calculation_mode="linear")
    s_last = out[o][-1].matrix_world.to_scale()
    check("linear scale ramps to the full factor", abs(s_last.x - 2.0) < 1e-3, f"{s_last.x:.3f}")

    # ---- radial: full revolution drops the shared endpoint (4 copies at 0/90/180/270)
    reset()
    o = cube_at(x=3.0)
    out = btk.duplicate_radial(o, 4, rotate_axis="z", pivot="world", keep_original=True)
    copies = out[o.name]
    check("radial makes 4 copies", len(copies) == 4)
    angles = sorted(
        (math.degrees(math.atan2(c.matrix_world.translation.y, c.matrix_world.translation.x)) + 360) % 360
        for c in copies
    )
    expect = [0.0, 90.0, 180.0, 270.0]
    check("radial full revolution spaces 90 deg",
          all(abs(a - b) < 1e-3 for a, b in zip(angles, expect)), f"{[round(a,1) for a in angles]}")
    radii = [c.matrix_world.translation.length for c in copies]
    check("radial keeps the orbit radius", all(abs(r - 3.0) < 1e-4 for r in radii))

    # ---- radial keep_original=False removes the source; copies grouped under an Empty
    reset()
    o = cube_at(x=3.0)
    name = o.name
    out = btk.duplicate_radial(o, 3, end_angle=180, rotate_axis="z", pivot="world")
    check("radial drops the original", name not in bpy.data.objects or bpy.data.objects[name].type == "EMPTY")
    empties = [e for e in bpy.data.objects if e.type == "EMPTY"]
    check("radial groups copies under an Empty", len(empties) == 1 and
          all(c.parent is empties[0] for c in out[name]),
          f"empties={len(empties)}")

    # ---- radial arc (not full revolution) keeps both endpoints: 3 copies 0/90/180
    angles = sorted(
        (math.degrees(math.atan2(c.matrix_world.translation.y, c.matrix_world.translation.x)) + 360) % 360
        for c in out[name]
    )
    check("radial arc keeps endpoints (0/90/180)",
          all(abs(a - b) < 1e-3 for a, b in zip(angles, [0.0, 90.0, 180.0])),
          f"{[round(a,1) for a in angles]}")

    # ---- radial combine joins into one mesh
    reset()
    o = cube_at(x=3.0)
    name = o.name
    out = btk.duplicate_radial(o, 4, rotate_axis="z", pivot="world", combine=True)
    res = out[name]
    check("radial combine yields one mesh of 4 cubes",
          len(res) == 1 and len(res[0].data.polygons) == 24,
          f"n={len(res)} f={len(res[0].data.polygons) if res else 0}")

    # ---- grid: 2x2x1 -> 3 copies (origin cell = the source), correct steps
    reset()
    o = cube_at(size=2.0)  # bbox 2 units; spacing 1 -> step 3
    out = btk.duplicate_grid(o, dimensions=(2, 2, 1), spacing=1.0)
    copies = out[o]
    check("grid 2x2x1 makes 3 copies", len(copies) == 3, f"n={len(copies)}")
    locs = sorted((round(c.matrix_world.translation.x, 3), round(c.matrix_world.translation.y, 3)) for c in copies)
    check("grid steps = bbox+spacing", locs == [(0.0, 3.0), (3.0, 0.0), (3.0, 3.0)], f"{locs}")
    check("grid copies grouped under an Empty",
          all(c.parent and c.parent.type == "EMPTY" for c in copies))
    check("grid instance mode shares data", all(c.data is o.data for c in copies))

    # ---- grid negative dimension lays out the other way
    reset()
    o = cube_at(size=2.0)
    out = btk.duplicate_grid(o, dimensions=(-2, 1, 1), spacing=0.0)
    xs = [round(c.matrix_world.translation.x, 3) for c in out[o]]
    check("grid negative count goes -X", xs == [-2.0], f"{xs}")

    # ---- grid combine joins
    reset()
    o = cube_at(size=2.0)
    out = btk.duplicate_grid(o, dimensions=(3, 1, 1), mode="combine")
    res = out[o]
    check("grid combine joins 2 copies into one mesh",
          len(res) == 1 and len(res[0].data.polygons) == 12,
          f"f={len(res[0].data.polygons) if res else 0}")

    # ---- grid cap guards runaway counts
    reset()
    o = cube_at()
    try:
        btk.duplicate_grid(o, dimensions=(100, 100, 100))
        check("grid cap raises", False)
    except ValueError:
        check("grid cap raises", True)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
