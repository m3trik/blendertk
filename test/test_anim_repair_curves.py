"""blendertk repair_corrupted_curves headless test — NaN/inf values, absurd key times, delete-unfixable,
and the option gating (fix_infinite off leaves inf keys alone).
Run: blender --background --factory-startup --python blendertk/test/test_anim_repair_curves.py
"""
import sys, os, traceback, math

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
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def keyed_object(name="Anim"):
        """An empty with location keyed at frames 1-3 → fcurves x/y/z (array_index 0/1/2)."""
        o = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(o)
        for f in (1, 2, 3):
            o.location = (f, f, f)
            o.keyframe_insert("location", frame=f)
        return o

    def by_index(o):
        return {fc.array_index: fc for fc in btk.get_fcurves([o])}

    # ---- corruption across three curves: inf value / all-NaN / absurd time ----
    reset()
    o = keyed_object()
    fcs = by_index(o)
    fcs[0].keyframe_points[1].co.y = float("inf")          # x: one infinite value
    for k in fcs[1].keyframe_points:                       # y: every value NaN → unfixable
        k.co.y = float("nan")
    fcs[2].keyframe_points[0].co.x = 5e8                   # z: one absurd frame time

    r = btk.repair_corrupted_curves([o], delete_unfixable=True)
    check("corrupted_found == 3", r["corrupted_found"] == 3, str(r["corrupted_found"]))
    check("keys_fixed == 5 (1 inf + 3 nan + 1 time)", r["keys_fixed"] == 5, str(r["keys_fixed"]))
    check("curves_deleted == 1 (all-NaN y)", r["curves_deleted"] == 1, str(r["curves_deleted"]))

    remaining = by_index(o)
    check("y curve (all-NaN) deleted", 1 not in remaining, str(sorted(remaining)))
    check("x curve kept, inf key dropped", 0 in remaining and len(remaining[0].keyframe_points) == 2,
          f"{[len(fc.keyframe_points) for i, fc in sorted(remaining.items())]}")
    check("z curve kept, bad-time key dropped", 2 in remaining and len(remaining[2].keyframe_points) == 2)
    check("no NaN/inf values survive",
          not any(math.isnan(k.co.y) or math.isinf(k.co.y)
                  for fc in btk.get_fcurves([o]) for k in fc.keyframe_points))

    # ---- option gating: fix_infinite off leaves an inf value alone ----
    reset()
    o = keyed_object()
    by_index(o)[0].keyframe_points[1].co.y = float("inf")
    r2 = btk.repair_corrupted_curves([o], fix_infinite=False, fix_invalid_times=True)
    check("fix_infinite off → inf key not flagged", r2["corrupted_found"] == 0, str(r2["corrupted_found"]))
    # we must not touch the curve (Blender itself may sanitize the stored inf on eval, so assert
    # OUR no-op — all keys intact — rather than that a literal inf persists).
    check("fix_infinite off → curve left untouched (3 keys)",
          len(by_index(o)[0].keyframe_points) == 3, str(len(by_index(o)[0].keyframe_points)))

    # ---- delete_unfixable off: an all-corrupt curve is emptied, not deleted ----
    reset()
    o = keyed_object()
    for k in by_index(o)[1].keyframe_points:
        k.co.y = float("nan")
    r3 = btk.repair_corrupted_curves([o], delete_unfixable=False)
    check("delete_unfixable off → curve kept", 1 in by_index(o), str(sorted(by_index(o))))
    check("delete_unfixable off → curve emptied", len(by_index(o)[1].keyframe_points) == 0)
    check("delete_unfixable off → counted repaired not deleted",
          r3["curves_deleted"] == 0 and r3["curves_repaired"] == 1, str(r3))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
