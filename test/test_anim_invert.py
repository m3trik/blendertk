"""blendertk invert_keys headless test — time / value / both inversion modes (+ default back-compat).
Run: blender --background --factory-startup --python blendertk/test/test_anim_invert.py
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

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def keyed(values):
        """An empty with location.x keyed at frames 1..N to the given values; returns the x fcurve."""
        o = bpy.data.objects.new("Anim", None)
        bpy.context.collection.objects.link(o)
        for f, v in enumerate(values, start=1):
            o.location.x = v
            o.keyframe_insert("location", index=0, frame=f)
        return o, next(fc for fc in btk.get_fcurves([o]) if fc.array_index == 0)

    co = lambda fc: [(round(k.co.x, 3), round(k.co.y, 3)) for k in fc.keyframe_points]

    # ---- value inversion about pivot 0: values flip sign, frames unchanged ----
    reset()
    o, fc = keyed([0.0, 5.0, 10.0])  # frames 1,2,3
    btk.invert_keys([o], mode="value", value_pivot=0.0)
    check("value mode flips values about pivot", co(fc) == [(1, 0), (2, -5), (3, -10)], f"{co(fc)}")

    # ---- value inversion about pivot 5: mirrors values across 5 ----
    reset()
    o, fc = keyed([0.0, 5.0, 10.0])
    btk.invert_keys([o], mode="value", value_pivot=5.0)
    check("value mode mirrors about a custom pivot", co(fc) == [(1, 10), (2, 5), (3, 0)], f"{co(fc)}")

    # ---- time inversion (default): frames mirror about range center, values stay ----
    reset()
    o, fc = keyed([0.0, 5.0, 10.0])  # center frame = 2
    btk.invert_keys([o])  # default mode='time' (back-compat)
    check("default (time) mirrors frames about center", sorted(co(fc)) == [(1, 10), (2, 5), (3, 0)],
          f"{sorted(co(fc))}")

    # ---- both: frames AND values inverted ----
    reset()
    o, fc = keyed([0.0, 5.0, 10.0])
    btk.invert_keys([o], mode="both", value_pivot=0.0)
    check("both inverts frames and values", sorted(co(fc)) == [(1, -10), (2, -5), (3, 0)],
          f"{sorted(co(fc))}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
