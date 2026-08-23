# !/usr/bin/python
# coding=utf-8
"""Blender shot-detection test — ``blendertk.anim_utils.shots._detection.Detection``.

bpy-only suite (no Qt): proves the mayatk-parity ``Detection`` namespace over the
store's scene walks against a real headless Blender scene:

- ``detect_shot_regions`` clusters two disjoint moving cubes into two shots with
  mayatk's candidate shape (``name`` / ``start`` / ``end`` / ``objects``);
- a baked hold *inside* a channel splits the object's segment (motion-rate
  static-interval splitting — the Maya ``SegmentKeys`` property);
- ``objects`` filter, ``ignore`` channel patterns, ``min_duration`` and
  ``motion_rate`` are honoured;
- ``regions_from_selected_keys`` builds boundaries from selected keys and prunes
  flat objects (``_filter_flat_objects``);
- ``resolve_to_transform`` maps Object / object-data / name / Material owners;
- ``BlenderShotStore.detect_regions`` delegates to ``Detection`` in both modes.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_detection.py

Prints the ``===RESULT: PASS/FAIL===`` sentinel ``Run-Tests.ps1`` greps for.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_detection_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy

    from blendertk.anim_utils.shots._detection import Detection
    from blendertk.anim_utils.shots._shots import BlenderShotStore

    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    check("empty scene detects nothing", Detection.detect_shot_regions() == [])

    def add_keyed_cube(name, frames, base_x, vary=True, data_path="location"):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        for f in frames:
            v = base_x + (f * 0.1 if vary else 0.0)
            if data_path == "location":
                obj.location = (v, 0.0, 0.0)
            else:
                obj.scale = (v, 1.0, 1.0)
            obj.keyframe_insert(data_path=data_path, frame=f)
        return obj

    cube_a = add_keyed_cube("CubeA", [1, 5, 10], 0.0)
    add_keyed_cube("CubeB", [50, 55, 60], 5.0)
    add_keyed_cube("CubeFlat", [5, 8], 3.0, vary=False)
    # Scale-only mover, used for the ``ignore`` check.
    add_keyed_cube("CubeScale", [100, 105], 1.0, data_path="scale")

    # ---- detect_shot_regions: mayatk candidate shape ---------------------
    regions = Detection.detect_shot_regions(gap_threshold=5.0)
    check("three moving clusters detected", len(regions) == 3, f"{regions}")
    if len(regions) == 3:
        r0, r1, r2 = sorted(regions, key=lambda r: r["start"])
        check(
            "candidate dicts carry name/start/end/objects",
            set(r0) == {"name", "start", "end", "objects"} and r0["name"] == "Shot 1",
            f"{r0}",
        )
        check(
            "shot 1 bounds+objects",
            r0["start"] == 1.0 and r0["end"] == 10.0 and r0["objects"] == ["CubeA"],
            f"{r0}",
        )
        check(
            "shot 2 bounds+objects",
            r1["start"] == 50.0 and r1["end"] == 60.0 and r1["objects"] == ["CubeB"],
            f"{r1}",
        )
        check(
            "flat cube excluded everywhere",
            all("CubeFlat" not in r["objects"] for r in regions),
        )
        check("scale-only mover is its own shot", r2["objects"] == ["CubeScale"])

    # ---- objects filter / ignore / min_duration / motion_rate ------------
    only_b = Detection.detect_shot_regions(objects=["CubeB", "NoSuchObj"])
    check(
        "objects filter restricts the scan (missing names dropped)",
        len(only_b) == 1 and only_b[0]["objects"] == ["CubeB"],
        f"{only_b}",
    )
    check(
        "objects filter with no existing names yields []",
        Detection.detect_shot_regions(objects=["NoSuchObj"]) == [],
    )
    no_scale = Detection.detect_shot_regions(ignore="scale")
    check(
        "ignore='scale' drops the scale-only mover",
        len(no_scale) == 2 and all("CubeScale" not in r["objects"] for r in no_scale),
        f"{no_scale}",
    )
    no_scale_glob = Detection.detect_shot_regions(ignore=["sca*", "rotation_*"])
    check(
        "ignore accepts a list of glob patterns",
        len(no_scale_glob) == 2,
        f"{no_scale_glob}",
    )
    check(
        "min_duration discards short clusters",
        Detection.detect_shot_regions(min_duration=20.0) == [],
    )
    check(
        "motion_rate above the cubes' 0.1/frame rate treats them as static",
        Detection.detect_shot_regions(motion_rate=1.0) == [],
    )

    # ---- baked hold inside a channel splits the object's segment ---------
    # CubeHold moves 1..10, holds (identical values) 10..40, moves 40..50.
    # Maya's SegmentKeys splits out the static interval; the Blender motion-
    # interval walk must do the same so the hold becomes a shot gap.
    bpy.ops.mesh.primitive_cube_add()
    hold = bpy.context.active_object
    hold.name = "CubeHold"
    for f, x in ((1, 0.0), (10, 1.0), (40, 1.0), (50, 2.0)):
        hold.location = (x, 0.0, 0.0)
        hold.keyframe_insert(data_path="location", frame=f)
    hold_regions = Detection.detect_shot_regions(objects=["CubeHold"])
    check(
        "baked hold splits one channel into two shots",
        [(r["start"], r["end"]) for r in hold_regions] == [(1.0, 10.0), (40.0, 50.0)],
        f"{hold_regions}",
    )
    segs = BlenderShotStore.collect_transform_segments(objects=["CubeHold"])
    check(
        "collect_transform_segments emits a segment per moving run",
        [(s["start"], s["end"]) for s in segs] == [(1.0, 10.0), (40.0, 50.0)],
        f"{segs}",
    )

    # ---- regions_from_selected_keys + flat-object pruning ----------------
    for obj in scene.objects:
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            for kp in fc.keyframe_points:
                kp.select_control_point = False
    check(
        "no selected keys -> []",
        Detection.regions_from_selected_keys() == [],
    )
    flat = bpy.data.objects["CubeFlat"]
    for o in (cube_a, flat):
        for fc in BlenderShotStore.iter_action_fcurves(o):
            for kp in fc.keyframe_points:
                kp.select_control_point = True
    sel = Detection.regions_from_selected_keys(gap_threshold=2.0, key_filter="all")
    check(
        "selected keys become boundaries (first shot starts at key 1)",
        bool(sel) and sel[0]["start"] == 1.0,
        f"{sel}",
    )
    check(
        "flat object pruned from selected-key shots, moving object kept",
        bool(sel)
        and all("CubeFlat" not in r["objects"] for r in sel)
        and any("CubeA" in r["objects"] for r in sel),
        f"{sel}",
    )
    sz = Detection.regions_from_selected_keys(gap_threshold=2.0, key_filter="skip_zero")
    check("skip_zero key_filter is accepted", isinstance(sz, list))

    # ---- resolve_to_transform --------------------------------------------
    cache = {}
    check(
        "resolve_to_transform: Object -> its name",
        Detection.resolve_to_transform(cube_a, cache=cache) == "CubeA",
    )
    check(
        "resolve_to_transform: object data -> owning object",
        Detection.resolve_to_transform(cube_a.data, cache=cache) == "CubeA",
    )
    check(
        "resolve_to_transform: name string -> name when it exists",
        Detection.resolve_to_transform("CubeB") == "CubeB"
        and Detection.resolve_to_transform("NoSuchObj") is None,
    )
    mat = bpy.data.materials.new("DetMat")
    check(
        "resolve_to_transform: Material -> None",
        Detection.resolve_to_transform(mat, cache=cache) is None,
    )
    check(
        "resolve_to_transform memoizes in the shared cache",
        cache.get(cube_a.name_full) == "CubeA" and cache.get(mat.name_full) is None,
        f"{cache}",
    )
    check(
        "_map_standard_curves_to_transforms maps moving + flat transform owners",
        set(Detection._map_standard_curves_to_transforms())
        == {"CubeA", "CubeB", "CubeFlat", "CubeScale", "CubeHold"},
    )

    # ---- BlenderShotStore.detect_regions delegates to Detection ----------
    store = BlenderShotStore()
    store.detection_threshold = 5.0
    store.detection_mode = "auto"
    auto = store.detect_regions()
    check(
        "store.detect_regions(auto) == Detection.detect_shot_regions",
        auto == Detection.detect_shot_regions(gap_threshold=5.0),
    )
    store.detection_mode = "all"
    check(
        "store.detect_regions(all) == Detection.regions_from_selected_keys",
        store.detect_regions()
        == Detection.regions_from_selected_keys(gap_threshold=5.0, key_filter="all"),
    )

    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_detection_checks()
    except Exception as e:  # pragma: no cover - harness failure prints its own trace
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
