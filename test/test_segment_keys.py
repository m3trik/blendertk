# !/usr/bin/python
# coding=utf-8
"""``btk.SegmentKeys`` — motion/hold segmentation over fcurves (mirror of mtk.SegmentKeys).

bpy-only suite.  Builds keyed objects and checks the Stage-1 collection contract
the sequencer depends on: static holds split motion runs, stepped keys are point
events, hold absorption / hold-only synthesis, channel filters, time-range clip,
and ``shift_curves`` (incl. the flat-at-destination pre-clean).

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_segment_keys.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy

    from blendertk.anim_utils.segment_keys import SegmentKeys
    from blendertk.anim_utils.shots._shots import BlenderShotStore

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    def keyed(name, frames_values, path="location", index=0):
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        o.name = name
        for f, v in frames_values:
            if path == "location":
                o.location[index] = v
            else:
                setattr(o, path, v)
            o.keyframe_insert(data_path=path, index=index, frame=f)
        return o

    def spans(segs):
        return sorted((sg["start"], sg["end"]) for sg in segs)

    # motion / hold / motion
    keyed("H", [(0, 0.0), (10, 1.0), (30, 1.0), (50, 1.0), (60, 2.0)])
    segs = SegmentKeys.collect_segments(
        ["H"], split_static=True, motion_only=True, ignore_holds=True
    )
    check(
        "split_static: two motion runs around the hold",
        spans(segs) == [(0, 10), (50, 60)],
        f"{spans(segs)}",
    )
    check(
        "segment dict shape",
        all(
            {"obj", "curves", "keyframes", "start", "end", "duration", "segment_range"}
            <= set(sg)
            for sg in segs
        ),
    )
    check(
        "keyframes limited to the run",
        segs and segs[0]["keyframes"] == [0.0, 10.0],
        f"{segs and segs[0]['keyframes']}",
    )

    segs = SegmentKeys.collect_segments(
        ["H"], split_static=True, motion_only=True, ignore_holds=False
    )
    check(
        "hold absorption: first run extends to the hold key before the next run",
        spans(segs)[0] == (0, 30),
        f"{spans(segs)}",
    )

    segs = SegmentKeys.collect_segments(["H"], split_static=False)
    check(
        "split_static=False: one span over all keys",
        spans(segs) == [(0, 60)],
        f"{spans(segs)}",
    )

    segs = SegmentKeys.collect_segments(
        ["H"],
        split_static=True,
        motion_only=True,
        ignore_holds=True,
        time_range=(40, 100),
    )
    check("time_range clips to [50,60]", spans(segs) == [(50, 60)], f"{spans(segs)}")

    # hold-only object: synthesised when holds are kept, nothing when ignored
    keyed("Flat", [(0, 1.0), (20, 1.0)])
    segs = SegmentKeys.collect_segments(["Flat"], split_static=True, motion_only=True)
    check(
        "hold-only object synthesises one segment (ignore_holds=False)",
        spans(segs) == [(0, 20)],
        f"{spans(segs)}",
    )
    segs = SegmentKeys.collect_segments(
        ["Flat"], split_static=True, motion_only=True, ignore_holds=True
    )
    check(
        "hold-only object yields nothing with ignore_holds=True",
        segs == [],
        f"{spans(segs)}",
    )

    # stepped keys are point events (interpolation CONSTANT), value change emits the span
    st = keyed("Step", [(0, 0.0), (10, 0.0), (20, 5.0)])
    for fc in BlenderShotStore.iter_action_fcurves(st):
        for kp in fc.keyframe_points:
            kp.interpolation = "CONSTANT"
    segs = SegmentKeys.collect_segments(
        ["Step"], split_static=True, motion_only=True, ignore_holds=True
    )
    check(
        "stepped: same-value hold skipped, value change spans [10,20]",
        spans(segs) == [(10, 20)],
        f"{spans(segs)}",
    )

    # channel filters
    keyed("Multi", [(0, 0.0), (10, 1.0)], index=0)
    m = bpy.data.objects["Multi"]
    m.location[2] = 0.0
    m.keyframe_insert(data_path="location", index=2, frame=0)
    m.location[2] = 4.0
    m.keyframe_insert(data_path="location", index=2, frame=30)
    segs = SegmentKeys.collect_segments(
        ["Multi"],
        split_static=True,
        motion_only=True,
        channel_box_attrs=["translateZ"],
        ignore_holds=True,
    )
    check(
        "channel_box_attrs: only translateZ's run [0,30]",
        spans(segs) == [(0, 30)],
        f"{spans(segs)}",
    )
    segs = SegmentKeys.collect_segments(
        ["Multi"],
        split_static=True,
        motion_only=True,
        ignore="translateZ",
        ignore_holds=True,
    )
    check(
        "ignore: translateZ dropped -> [0,10]",
        spans(segs) == [(0, 10)],
        f"{spans(segs)}",
    )
    check(
        "curves list scoped by the filter",
        segs and all(fc.array_index == 0 for fc in segs[0]["curves"]),
    )

    # custom-prop curves are outside the transform scope by default
    m["prop"] = 0.0
    m.keyframe_insert(data_path='["prop"]', frame=100)
    segs = SegmentKeys.collect_segments(["Multi"], split_static=False)
    check(
        "transform_only: custom-prop key not in span",
        spans(segs) == [(0, 30)],
        f"{spans(segs)}",
    )
    segs = SegmentKeys.collect_segments(
        ["Multi"], split_static=False, transform_only=False
    )
    check(
        "transform_only=False: custom-prop key included",
        spans(segs) == [(0, 100)],
        f"{spans(segs)}",
    )

    # shift_curves
    sh = keyed("Shift", [(0, 0.0), (10, 1.0), (20, 1.0), (30, 2.0)])
    fcs = list(BlenderShotStore.iter_action_fcurves(sh))
    SegmentKeys.shift_curves(fcs, 5, time_range=(0, 10))
    t = sorted(
        round(kp.co[0], 3)
        for fc in fcs
        for kp in fc.keyframe_points
        if fc.array_index == 0
    )
    check(
        "shift_curves: only keys in range moved (+5)",
        t == [5.0, 15.0, 20.0, 30.0],
        f"{t}",
    )
    # flat key at 20 sits exactly where the run lands -> pre-cleaned
    SegmentKeys.shift_curves(fcs, 5, time_range=(5, 15), remove_flat_at_dest=True)
    t = sorted(
        round(kp.co[0], 3)
        for fc in fcs
        for kp in fc.keyframe_points
        if fc.array_index == 0
    )
    check(
        "shift_curves: flat destination key removed, run landed",
        t == [10.0, 20.0, 30.0],
        f"{t}",
    )

    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_checks()
    except Exception as e:  # pragma: no cover
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]
    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
