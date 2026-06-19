"""blendertk animation depth-parity test — the option-box behaviors added to close the
animation parity gap: stagger (grouping/intervals/invert/start), adjust-spacing (exact-gap /
selected), move (align/selected), intermediate (time-range / ignore-visibility), snap
(selected / range), tie (absolute), visibility (when/offset) and CSV info.

Run: blender --background --factory-startup --python blendertk/test/test_anim_depth.py
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
        """Empty with location.x keyed at the given (frame, value) pairs; returns (obj, xfcurve)."""
        o = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(o)
        for f, v in pairs:
            o.location.x = v
            o.keyframe_insert("location", index=0, frame=f)
        fc = next(fc for fc in btk.get_fcurves([o]) if fc.array_index == 0)
        for k in fc.keyframe_points:  # start from a known (deselected) state
            k.select_control_point = False
        return o, fc

    def frames(fc):
        return sorted(round(k.co.x, 3) for k in fc.keyframe_points)

    def vis_frames(o):
        fc = next(fc for fc in btk.get_fcurves([o]) if fc.data_path == "hide_viewport")
        return sorted(round(k.co.x) for k in fc.keyframe_points)

    # ---- stagger: sequential (first stays, next starts after prev end + spacing) ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=5)
    check("stagger sequential keeps first, offsets second",
          frames(fa) == [1, 3] and frames(fb) == [8, 10], f"A={frames(fa)} B={frames(fb)}")

    # ---- stagger: fixed intervals ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=10, use_intervals=True)
    check("stagger intervals places blocks at fixed step",
          frames(fa) == [1, 3] and frames(fb) == [11, 13], f"A={frames(fa)} B={frames(fb)}")

    # ---- stagger: start_frame override ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, start_frame=20)
    check("stagger start_frame anchors the first block",
          frames(fa) == [20, 22] and frames(fb) == [27, 29], f"A={frames(fa)} B={frames(fb)}")

    # ---- stagger: invert reverses which object holds ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, invert=True)
    check("stagger invert holds the last object, moves the first",
          frames(fb) == [1, 3] and frames(fa) == [8, 10], f"A={frames(fa)} B={frames(fb)}")

    # ---- stagger: group_overlapping keeps overlapping pair together ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")   # overlaps A
    c, fc = keyed_at([(20, 0), (22, 1)], "C")  # separate
    btk.stagger_keys([a, b, c], spacing=5, group_overlapping=True)
    check("stagger group_overlapping holds the overlapping block, retimes the separate one",
          frames(fa) == [1, 5] and frames(fb) == [3, 7] and frames(fc) == [12, 14],
          f"A={frames(fa)} B={frames(fb)} C={frames(fc)}")

    # contrast: without grouping, B is retimed (does not stay at 3,7)
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, group_overlapping=False)
    check("stagger without grouping retimes the overlapping object",
          frames(fb) != [3, 7], f"B={frames(fb)}")

    # ---- adjust_key_spacing: exact_gap lands first key after frame at frame+amount ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    moved = btk.adjust_key_spacing([o], spacing=5, frame=2, exact_gap=True)
    check("adjust exact_gap shifts keys>=frame so first lands at frame+gap",
          frames(fc) == [1, 7, 12] and moved == 2, f"{frames(fc)} moved={moved}")

    # ---- adjust_key_spacing: selected_keys_only shifts only selected ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    next(k for k in fc.keyframe_points if k.co.x == 5).select_control_point = True
    moved = btk.adjust_key_spacing([o], spacing=3, frame=2, selected_keys_only=True)
    check("adjust selected_keys_only shifts only the selected key",
          frames(fc) == [1, 8, 10] and moved == 1, f"{frames(fc)} moved={moved}")

    # ---- move_keys_to_frame: align=end anchors the LAST key to the frame ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    btk.move_keys_to_frame([o], frame=20, align="end")
    check("move align=end lands the last key on the frame",
          frames(fc) == [11, 15, 20], f"{frames(fc)}")

    # ---- move_keys_to_frame: selected_keys_only moves only the selected key ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    next(k for k in fc.keyframe_points if k.co.x == 5).select_control_point = True
    n = btk.move_keys_to_frame([o], frame=20, selected_keys_only=True)
    check("move selected_keys_only moves only the selected key",
          frames(fc) == [1, 10, 20] and n == 1, f"{frames(fc)} n={n}")

    # ---- add_intermediate_keys: time_range bounds the inserted frames ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 9)])
    added = btk.add_intermediate_keys([o], step=1, time_range=(3, 6))
    check("add_intermediate time_range only fills inside the window",
          frames(fc) == [1, 3, 4, 5, 6, 10] and added == 4, f"{frames(fc)} added={added}")

    # ---- add_intermediate_keys: ignore_visibility leaves vis curves alone ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 9)])
    o.hide_viewport = False
    o.keyframe_insert("hide_viewport", frame=1)
    o.keyframe_insert("hide_viewport", frame=10)
    btk.add_intermediate_keys([o], step=1, ignore_visibility=True)
    check("add_intermediate ignore_visibility skips hide_viewport",
          vis_frames(o) == [1, 10] and len(fc.keyframe_points) > 2,
          f"vis={vis_frames(o)} loc={len(fc.keyframe_points)}")

    # ---- remove_intermediate_keys: time_range only removes interior keys inside it ----
    reset()
    o, fc = keyed_at([(1, 0), (3, 1), (5, 2), (7, 3), (10, 4)])
    removed = btk.remove_intermediate_keys([o], time_range=(2, 6))
    check("remove_intermediate time_range removes only interior keys in window",
          frames(fc) == [1, 7, 10] and removed == 2, f"{frames(fc)} removed={removed}")

    # ---- snap_keys: selected_only + return count ----
    reset()
    o, fc = keyed_at([(1.4, 0), (2.6, 1), (5.2, 2)])
    next(k for k in fc.keyframe_points if abs(k.co.x - 2.6) < 1e-3).select_control_point = True
    n = btk.snap_keys([o], selected_only=True)
    check("snap selected_only snaps only the selected key (returns count)",
          frames(fc) == [1.4, 3, 5.2] and n == 1, f"{frames(fc)} n={n}")

    # ---- snap_keys: time_range ----
    reset()
    o, fc = keyed_at([(1.4, 0), (2.6, 1), (5.2, 2)])
    n = btk.snap_keys([o], time_range=(2, 4))
    check("snap time_range snaps only keys inside the window",
          frames(fc) == [1.4, 3, 5.2] and n == 1, f"{frames(fc)} n={n}")

    # ---- tie_keyframes: absolute uses the keyed extent, not the scene range ----
    reset()
    o, fc = keyed_at([(5, 0), (6, 1), (7, 2)])
    changed_abs = btk.tie_keyframes([o], absolute=True)
    check("tie absolute adds no scene-range bookends (keys already span the extent)",
          changed_abs == 0 and frames(fc) == [5, 6, 7], f"changed={changed_abs} {frames(fc)}")
    reset()
    o, fc = keyed_at([(5, 0), (6, 1), (7, 2)])
    changed_scene = btk.tie_keyframes([o], absolute=False)
    check("tie non-absolute bookends at the scene frame range",
          changed_scene == 2, f"changed={changed_scene} {frames(fc)}")

    # ---- set_visibility_keys: when=both keys the range ends; offset shifts ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="both")
    check("visibility when=both keys range start and end", vis_frames(o) == [10, 20], f"{vis_frames(o)}")
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="start", offset=2)
    check("visibility when=start + offset", vis_frames(o) == [12], f"{vis_frames(o)}")

    # ---- format_animation_info_csv ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 1)], "Cube")
    csv = btk.format_animation_info_csv(btk.get_animation_info([o]))
    check("csv info has a header + a data row",
          csv.splitlines()[0].startswith("Object,Action") and "Cube" in csv, repr(csv[:40]))
    check("csv info empty -> ''", btk.format_animation_info_csv([]) == "")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
