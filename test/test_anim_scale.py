"""blendertk scale_keys headless test — absolute/relative uniform mode, grouping
(single_group / per_object / overlap_groups), split_static segmentation, speed-mode motion
sampling (translation / rotation / both), and post-scale snap composition.

Run: blender --background --factory-startup --python blendertk/test/test_anim_scale.py
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

    def keyed_at(pairs, name="Anim"):
        """Empty with location.x keyed at the given (frame, value) pairs; returns (obj, xfcurve).
        Interpolation forced LINEAR so world-space motion sampling (speed mode) is exact/monotonic
        regardless of default bezier handles."""
        o = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(o)
        for f, v in pairs:
            o.location.x = v
            o.keyframe_insert("location", index=0, frame=f)
        fc = next(fc for fc in btk.get_fcurves([o]) if fc.array_index == 0)
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
        return o, fc

    def rot_keyed_at(pairs, name="Rot"):
        """Empty with rotation_euler.z keyed (in degrees) at the given (frame, degrees) pairs."""
        import math

        o = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(o)
        for f, deg in pairs:
            o.rotation_euler.z = math.radians(deg)
            o.keyframe_insert("rotation_euler", index=2, frame=f)
        fc = next(fc for fc in btk.get_fcurves([o]) if fc.data_path == "rotation_euler")
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
        return o, fc

    def frames(fc):
        return sorted(round(k.co.x, 3) for k in fc.keyframe_points)

    # ---- uniform, relative (default): factor is a plain multiplier about the pivot ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    n = btk.scale_keys([o], factor=2.0)
    check("uniform relative scales about the block's own start (single_group default)",
          frames(fc) == [10, 30, 50] and n == 3, f"{frames(fc)} n={n}")

    # ---- uniform, absolute: factor is a target duration in frames ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])  # duration 20
    n = btk.scale_keys([o], factor=10.0, absolute=True)  # target duration 10 -> block_factor 0.5
    check("uniform absolute scales to the target duration",
          frames(fc) == [10, 15, 20] and n == 3, f"{frames(fc)} n={n}")

    # ---- explicit pivot overrides the auto (block-start) pivot ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    btk.scale_keys([o], factor=2.0, pivot=0)
    check("explicit pivot overrides the auto block-start pivot",
          frames(fc) == [20, 40, 60], f"{frames(fc)}")

    # ---- split_static: segments separated by a static hold scale independently (per_object) ----
    reset()
    o, fc = keyed_at([(1, 0), (2, 5), (3, 10), (10, 10), (11, 5), (12, 0)])
    n = btk.scale_keys([o], factor=2.0, group_mode="per_object", split_static=True)
    check("split_static scales each segment independently about its own start",
          frames(fc) == [1, 3, 5, 10, 12, 14] and n == 6, f"{frames(fc)} n={n}")

    # contrast: without split_static the whole object is one block (own start = 1)
    reset()
    o, fc = keyed_at([(1, 0), (2, 5), (3, 10), (10, 10), (11, 5), (12, 0)])
    btk.scale_keys([o], factor=2.0, group_mode="per_object", split_static=False)
    check("without split_static the whole object scales as one block",
          frames(fc) == [1, 3, 5, 19, 21, 23], f"{frames(fc)}")

    # ---- single_group: identical result whether split_static is on or off (one shared pivot) ----
    reset()
    o, fc = keyed_at([(1, 0), (2, 5), (3, 10), (10, 10), (11, 5), (12, 0)])
    btk.scale_keys([o], factor=2.0, group_mode="single_group", split_static=True)
    a = frames(fc)
    reset()
    o, fc = keyed_at([(1, 0), (2, 5), (3, 10), (10, 10), (11, 5), (12, 0)])
    btk.scale_keys([o], factor=2.0, group_mode="single_group", split_static=False)
    b = frames(fc)
    check("single_group scaling is unaffected by split_static (one shared pivot/factor)",
          a == b == [1, 3, 5, 19, 21, 23], f"{a} vs {b}")

    # ---- overlap_groups: two objects with overlapping ranges share a group pivot ----
    reset()
    a_obj, fa = keyed_at([(1, 0), (5, 1)], "A")
    b_obj, fb = keyed_at([(3, 0), (7, 1)], "B")   # overlaps A
    c_obj, fc_ = keyed_at([(20, 0), (22, 1)], "C")  # separate
    btk.scale_keys([a_obj, b_obj, c_obj], factor=2.0, group_mode="overlap_groups")
    check("overlap_groups scales the overlapping pair about a shared pivot",
          frames(fa) == [1, 9] and frames(fb) == [5, 13], f"A={frames(fa)} B={frames(fb)}")
    check("overlap_groups scales the separate object about its own pivot",
          frames(fc_) == [20, 24], f"C={frames(fc_)}")

    # ---- snap_mode composes with the result (post-scale rounding) ----
    reset()
    o, fc = keyed_at([(1, 0), (2, 1), (3, 2)])
    btk.scale_keys([o], factor=1.3, snap_mode="nearest")
    check("snap_mode rounds the scaled keys to whole frames",
          all(float(k.co.x).is_integer() for k in fc.keyframe_points), f"{frames(fc)}")

    # ---- speed mode, absolute: block_factor derives from sampled motion distance ----
    reset()
    o, fc = keyed_at([(1, 0), (11, 100)])  # distance 100 over 10 frames
    btk.scale_keys([o], factor=20.0, mode="speed", absolute=True, samples=8)
    # target_speed=20 -> target_duration=100/20=5 -> block_factor=5/10=0.5
    check("speed absolute mode retimes to hit the target units/frame speed",
          frames(fc) == [1, 6], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (11, 50)])  # distance 50 over 10 frames
    btk.scale_keys([o], factor=20.0, mode="speed", absolute=True, samples=8)
    # target_duration=50/20=2.5 -> block_factor=2.5/10=0.25
    check("speed absolute mode genuinely depends on the sampled distance",
          frames(fc) == [1, 3.5], f"{frames(fc)}")

    # ---- speed mode, rotation-only ----
    reset()
    o, fc = rot_keyed_at([(1, 0), (11, 90)])  # 90 degrees over 10 frames
    btk.scale_keys([o], factor=45.0, mode="speed", absolute=True, include_rotation="only", samples=8)
    # target_duration=90/45=2 -> block_factor=2/10=0.2
    check("speed mode include_rotation='only' samples rotation instead of translation",
          frames(fc) == [1, 3], f"{frames(fc)}")

    # ---- factor <= 0 is a no-op (guards against garbage UI input) ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    n = btk.scale_keys([o], factor=0.0)
    check("factor <= 0 is a no-op", n == 0 and frames(fc) == [10, 20], f"n={n} {frames(fc)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
