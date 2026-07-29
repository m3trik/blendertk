"""blendertk.nurbs_utils headless test — the rolled CV-deformation family (2026-07-28):
bend / curl / scale-curvature / straighten / rebuild / extend, pure control-point math on
POLY/NURBS splines (the SIMPLE_DEFORM route was probed and refuted — silent no-op unless
deform_axis ⊥ the curve plane). Run:
blender --background --factory-startup --python blendertk/test/test_nurbs_utils.py
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk
    from mathutils import Vector

    def reset():
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for c in list(bpy.data.curves):
            bpy.data.curves.remove(c)

    def line_curve(n=9, length=8.0, kind="POLY"):
        """A straight +X line with ``n`` control points."""
        pts = [(length * i / (n - 1), 0.0, 0.0) for i in range(n)]
        return btk.NurbsUtils.create_curve(pts, name="probe", kind=kind)

    def pts(o):
        return [Vector(p.co[:3]) for p in o.data.splines[0].points]

    def arc_len(ps):
        return sum((b - a).length for a, b in zip(ps, ps[1:]))

    # ---- straighten: a bowed curve flattens; preserve_length keeps total length ----
    reset()
    o = line_curve()
    for i, p in enumerate(o.data.splines[0].points):  # bow it in Y
        x, _, z, w = p.co
        p.co = (x, (i % 2) * 0.8, z, w)
    bowed_len = arc_len(pts(o))
    btk.NurbsUtils.straighten_curve(o, straightness=1.0, preserve_length=True)
    ys = [abs(p.y) for p in pts(o)]
    check("straighten flattens the bow", max(ys) < 1e-6, f"max|y|={max(ys):.2e}")
    check(
        "straighten preserve_length keeps arc length",
        abs(arc_len(pts(o)) - bowed_len) < 1e-4,
        f"{arc_len(pts(o)):.3f} vs {bowed_len:.3f}",
    )

    # ---- bend: straight +X line, 90° about Z -> exact quarter-circle arc ----
    import math

    reset()
    o = line_curve()
    btk.NurbsUtils.bend_curve(o, angle=90.0, axis="z")
    R = 8.0 / math.radians(90.0)  # arc radius for a length-8 quarter circle
    end = pts(o)[-1]
    check(
        "bend 90° lands the far end on the quarter-circle (R, R)",
        abs(end.x - R) < 1e-4 and abs(end.y - R) < 1e-4,
        f"end=({end.x:.3f}, {end.y:.3f}) R={R:.3f}",
    )
    # chord-polyline length approaches the true arc length from below (9 samples of a
    # circle): must sit between the inscribed-polygon length and 8.0 itself.
    poly_len = arc_len(pts(o))
    check(
        "bend is arc-length faithful (inscribed polyline of an 8.0 arc)",
        7.9 < poly_len <= 8.0 + 1e-6,
        f"len={poly_len:.4f}",
    )
    mid = pts(o)[4]  # t=0.5 -> 45° along the arc
    expect = (R * math.sin(math.radians(45)), R * (1 - math.cos(math.radians(45))))
    check(
        "bend midpoint sits 45° along the arc",
        abs(mid.x - expect[0]) < 1e-4 and abs(mid.y - expect[1]) < 1e-4,
        f"mid=({mid.x:.3f}, {mid.y:.3f}) expect={tuple(round(v, 3) for v in expect)}",
    )
    # all radii from the arc center (0, R) equal R -> a true circle, not a stretch
    radii = [((p - Vector((0, R, 0))).length) for p in pts(o)]
    check(
        "bend points all sit on the circle",
        max(radii) - min(radii) < 1e-4,
        f"radii {min(radii):.4f}..{max(radii):.4f}",
    )

    # ---- curl: quadratic profile — end rotated by full angle, midpoint by angle/4 ----
    reset()
    o = line_curve()
    btk.NurbsUtils.curl_curve(o, angle=180.0, frequency=1.0, axis="z")
    end = pts(o)[-1]
    check(
        "curl 180° puts the far end on -X",
        end.x < 0 and abs(end.y) < 1e-3,
        f"end=({end.x:.3f}, {end.y:.3f})",
    )
    mid = pts(o)[4]
    check(
        "curl accelerates (t=0.5 -> 45°, not 90°)",
        abs(math.degrees(math.atan2(mid.y, mid.x)) - 45.0) < 1.0,
        f"angle={math.degrees(math.atan2(mid.y, mid.x)):.2f}",
    )

    # ---- scale_curvature: bow deviation doubles / collapses ----
    reset()
    o = line_curve(n=3, length=4.0)
    o.data.splines[0].points[1].co = (2.0, 1.0, 0.0, 1.0)  # bow the midpoint
    btk.NurbsUtils.scale_curvature(o, factor=2.0)
    check(
        "scale_curvature x2 doubles the deviation",
        abs(pts(o)[1].y - 2.0) < 1e-5,
        f"y={pts(o)[1].y:.3f}",
    )
    btk.NurbsUtils.scale_curvature(o, factor=0.0)
    check(
        "scale_curvature x0 collapses onto the chord",
        abs(pts(o)[1].y) < 1e-6,
        f"y={pts(o)[1].y:.2e}",
    )

    # ---- rebuild: uniform arc-length redistribution with a new count ----
    reset()
    o = line_curve(n=5, length=4.0)
    # cluster points unevenly
    for i, x in enumerate((0.0, 0.2, 0.4, 3.0, 4.0)):
        o.data.splines[0].points[i].co = (x, 0.0, 0.0, 1.0)
    btk.NurbsUtils.rebuild_curve(o, spans=8)
    ps = pts(o)
    gaps = [(b - a).length for a, b in zip(ps, ps[1:])]
    check("rebuild produces spans+1 points", len(ps) == 9, f"n={len(ps)}")
    check(
        "rebuild distributes uniformly",
        max(gaps) - min(gaps) < 1e-5,
        f"gaps {min(gaps):.4f}..{max(gaps):.4f}",
    )
    check(
        "rebuild keeps the endpoints",
        ps[0].x == 0.0 and abs(ps[-1].x - 4.0) < 1e-6,
        f"{ps[0].x}..{ps[-1].x:.3f}",
    )

    # rebuild on a MULTI-spline curve: no dangling-reference crash, both splines resampled,
    # a Bézier spline in the same datablock left untouched (index-based two-phase regression).
    reset()
    o = line_curve(n=5, length=4.0)
    btk.NurbsUtils.add_spline(o, [(0, 2, 0), (1, 2, 0), (4, 2, 0)])
    bz = o.data.splines.new("BEZIER")
    bz.bezier_points.add(1)
    btk.NurbsUtils.rebuild_curve(o, spans=4)
    counts = sorted(
        (sp.type, len(sp.points) if sp.type != "BEZIER" else len(sp.bezier_points))
        for sp in o.data.splines
    )
    check(
        "rebuild multi-spline: both POLY splines -> 5 pts, Bézier untouched",
        counts == [("BEZIER", 2), ("POLY", 5), ("POLY", 5)],
        f"{counts}",
    )

    # ---- extend: one point continuing the end tangent ----
    reset()
    o = line_curve(n=5, length=4.0)
    btk.NurbsUtils.extend_curve(o, distance=2.0)
    ps = pts(o)
    check(
        "extend appends along the end tangent",
        len(ps) == 6 and abs(ps[-1].x - 6.0) < 1e-5,
        f"end x={ps[-1].x:.3f}",
    )
    btk.NurbsUtils.extend_curve(o, distance=1.0, from_start=True)
    ps = pts(o)
    check(
        "extend from_start prepends",
        len(ps) == 7 and abs(ps[0].x + 1.0) < 1e-5,
        f"start x={ps[0].x:.3f}",
    )

    # ---- NURBS spline round-trip (the family is POLY+NURBS) ----
    reset()
    o = line_curve(kind="NURBS")
    btk.NurbsUtils.bend_curve(o, angle=90.0, axis="z")
    check("family works on NURBS splines", pts(o)[-1].y > 0, f"end y={pts(o)[-1].y:.3f}")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===NURBS-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
