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

    # ---- adjust_key_spacing: relative=True offsets the adjustment point from the current frame ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    bpy.context.scene.frame_set(5)
    moved = btk.adjust_key_spacing([o], spacing=10, frame=15, relative=True)  # adjusted = 5+15=20
    check("adjust relative=True offsets frame from the current frame",
          frames(fc) == [10, 30, 40] and moved == 2, f"{frames(fc)} moved={moved}")

    # ---- adjust_key_spacing: relative=False (default) treats frame as absolute ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    bpy.context.scene.frame_set(999)  # current frame must not matter
    moved = btk.adjust_key_spacing([o], spacing=5, frame=15)
    check("adjust relative=False treats frame as an absolute frame number",
          frames(fc) == [10, 25, 35] and moved == 2, f"{frames(fc)} moved={moved}")
    bpy.context.scene.frame_set(1)

    # ---- adjust_key_spacing: preserve_keys anchors a key at the pivot while it shifts away ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    moved = btk.adjust_key_spacing([o], spacing=5, frame=20, preserve_keys=True)
    co_pairs = sorted((round(k.co.x, 3), round(k.co.y, 3)) for k in fc.keyframe_points)
    check("adjust preserve_keys anchors a key at the pivot while the original shifts away",
          co_pairs == [(10, 0), (20, 1), (25, 1), (35, 2)] and moved == 2,
          f"{co_pairs} moved={moved}")

    # ---- adjust_key_spacing: objects=None means every scene object, not "nothing" (mirrors
    # optimize_keys/get_animation_info/tie_keyframes/repair_corrupted_curves/fit_playback_range's
    # shared objects=None -> scene-wide convention in this module; backs tentacle's Adjust
    # Spacing "Scope: Entire Scene" cmb036 option, which calls this with objects=None) ----
    reset()
    o1, fc1 = keyed_at([(10, 0), (20, 1)], "A")
    o2, fc2 = keyed_at([(10, 0), (20, 1)], "B")
    moved = btk.adjust_key_spacing(None, spacing=5, frame=15)
    check("adjust_key_spacing(objects=None) shifts keys across every scene object",
          frames(fc1) == [10, 25] and frames(fc2) == [10, 25] and moved == 2,
          f"A={frames(fc1)} B={frames(fc2)} moved={moved}")

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

    # ---- snap_keys: method variety (cmb003 vocabulary, DRY over ptk.MathUtils.round_value) ----
    reset()
    o, fc = keyed_at([(10.7, 0)])
    btk.snap_keys([o], method="floor")
    check("snap method=floor always rounds down", frames(fc) == [10], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(10.2, 0)])
    btk.snap_keys([o], method="ceil")
    check("snap method=ceil always rounds up", frames(fc) == [11], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(10.5, 0)])
    btk.snap_keys([o], method="half_up")
    check("snap method=half_up rounds .5 up", frames(fc) == [11], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(24.8, 0)])
    btk.snap_keys([o], method="preferred")
    check("snap method=preferred snaps close values to clean numbers", frames(fc) == [25], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(48.5, 0)])
    btk.snap_keys([o], method="aggressive_preferred")
    check("snap method=aggressive_preferred snaps even farther values to clean numbers",
          frames(fc) == [50], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(10.7, 0)])
    n = btk.snap_keys([o], method="none")
    check("snap method=none is a documented no-op (no keys touched, count 0)",
          frames(fc) == [10.7] and n == 0, f"{frames(fc)} n={n}")

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

    # ---- set_visibility_keys: before_start / after_end / current (cmb002 full vocabulary) ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="before_start")
    check("visibility when=before_start keys one frame before the range start",
          vis_frames(o) == [9], f"{vis_frames(o)}")
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="after_end")
    check("visibility when=after_end keys one frame after the range end",
          vis_frames(o) == [21], f"{vis_frames(o)}")
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    bpy.context.scene.frame_set(15)
    btk.set_visibility_keys([o], visible=False, when="current")
    check("visibility when=current keys the playhead frame",
          vis_frames(o) == [15], f"{vis_frames(o)}")
    bpy.context.scene.frame_set(1)

    # ---- set_visibility_keys: group_overlapping shares one combined range ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")  # overlaps A
    c, fc_ = keyed_at([(20, 0), (22, 1)], "C")  # separate
    btk.set_visibility_keys([a, b, c], visible=False, when="start", group_overlapping=True)
    check("visibility group_overlapping keys the group's combined start on every member",
          vis_frames(a) == [1] and vis_frames(b) == [1] and vis_frames(c) == [20],
          f"A={vis_frames(a)} B={vis_frames(b)} C={vis_frames(c)}")

    # contrast: without grouping, each object keys its own range start
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")
    btk.set_visibility_keys([a, b], visible=False, when="start", group_overlapping=False)
    check("visibility without grouping keys each object's own start",
          vis_frames(a) == [1] and vis_frames(b) == [3], f"A={vis_frames(a)} B={vis_frames(b)}")

    # ---- delete_keys: time-scoped deletion relative to the current frame ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    cleared = btk.delete_keys([o], time="after")
    check("delete_keys time='after' drops keys strictly after the current frame",
          frames(fc) == [1, 5] and cleared == [o], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="before")
    check("delete_keys time='before' drops keys strictly before the current frame",
          frames(fc) == [5, 10], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="current")
    check("delete_keys time='current' drops only the key at the current frame",
          frames(fc) == [1, 10], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="before|current")
    check("delete_keys time='before|current' includes the current frame",
          frames(fc) == [10], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="after|current")
    check("delete_keys time='after|current' includes the current frame",
          frames(fc) == [1], f"{frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1)])
    bpy.context.scene.frame_set(999)
    no_hit = btk.delete_keys([o], time="after")
    check("delete_keys time scope with nothing matching returns no touched objects and keeps keys",
          no_hit == [] and frames(fc) == [1, 5], f"{no_hit} {frames(fc)}")

    reset()
    o, fc = keyed_at([(1, 0), (5, 1)])
    cleared_all = btk.delete_keys([o])
    check("delete_keys default (time=None) still clears all animation data",
          cleared_all == [o] and getattr(o, "animation_data", None) is None, f"{cleared_all}")
    bpy.context.scene.frame_set(1)

    # ---- copy_keys / paste_keys: action mode (whole-action, target_time realigns) ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    action = btk.copy_keys(a)
    pasted = btk.paste_keys([b], action)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("paste_keys action-mode, no target_time, pastes unshifted at the original frames",
          frames(fb) == [1, 5] and pasted == [b], f"{frames(fb)}")
    check("paste_keys action-mode links an INDEPENDENT copy (own Action datablock)",
          b.animation_data.action is not a.animation_data.action)
    for k in fb.keyframe_points:  # nudging the copy must not move the source
        k.co.x += 1
    fb.update()
    check("editing the pasted copy leaves the source's keys untouched",
          frames(fa) == [1, 5], f"{frames(fa)}")

    reset()
    a, fa = keyed_at([(10, 0), (14, 1)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    action = btk.copy_keys(a)
    btk.paste_keys([b], action, target_time=100)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("paste_keys action-mode target_time re-anchors the earliest key there",
          frames(fb) == [100, 104], f"{frames(fb)}")

    # ---- copy_keys / paste_keys: current_frame mode (pose snapshot) ----
    reset()
    a, fa = keyed_at([(1, 0.0), (10, 5.0)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    bpy.context.scene.frame_set(4)  # captures whatever fa evaluates to at frame 4
    buf = btk.copy_keys(a, mode="current_frame")
    btk.paste_keys([b], buf)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("copy/paste current_frame keys the sampled value at the frame it was captured",
          frames(fb) == [4] and round(fb.keyframe_points[0].co.y, 3) == round(fa.evaluate(4), 3),
          f"{frames(fb)} y={fb.keyframe_points[0].co.y if fb.keyframe_points else None}")

    reset()
    a, fa = keyed_at([(1, 0.0), (10, 5.0)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    bpy.context.scene.frame_set(4)
    buf = btk.copy_keys(a, mode="current_frame")
    btk.paste_keys([b], buf, target_time=50)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("copy/paste current_frame honors an explicit target_time",
          frames(fb) == [50], f"{frames(fb)}")
    bpy.context.scene.frame_set(1)

    # ---- copy_keys / paste_keys: selected mode (only selected keys, per fcurve) ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1), (10, 2)], "A")
    next(k for k in fa.keyframe_points if k.co.x == 5).select_control_point = True
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    buf = btk.copy_keys(a, mode="selected")
    btk.paste_keys([b], buf)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("copy/paste selected-mode carries only the selected key, unshifted",
          frames(fb) == [5], f"{frames(fb)}")

    reset()
    a, fa = keyed_at([(1, 0), (5, 1), (10, 2)], "A")
    next(k for k in fa.keyframe_points if k.co.x == 5).select_control_point = True
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    buf = btk.copy_keys(a, mode="selected")
    btk.paste_keys([b], buf, target_time=100)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check("copy/paste selected-mode target_time shifts the (single) earliest key there",
          frames(fb) == [100], f"{frames(fb)}")

    reset()
    a, fa = keyed_at([(1, 0), (5, 1), (10, 2)], "A")
    empty_buf = btk.copy_keys(a, mode="selected")
    check("copy_keys selected-mode returns None when nothing is selected", empty_buf is None)

    # ---- format_animation_info_csv ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 1)], "Cube")
    csv = btk.format_animation_info_csv(btk.get_animation_info([o]))
    check("csv info has a header + a data row",
          csv.splitlines()[0].startswith("Object,Action") and "Cube" in csv, repr(csv[:40]))
    check("csv info empty -> ''", btk.format_animation_info_csv([]) == "")

    # ---- get_animation_info: ignore_holds trims leading/trailing static holds ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 0), (10, 5), (15, 5)], "Cube")
    full = btk.get_animation_info([o])[0]
    check("get_animation_info default reports the full first/last key extent",
          (full["start"], full["end"]) == (1, 15), f"{full['start']},{full['end']}")
    trimmed = btk.get_animation_info([o], ignore_holds=True)[0]
    check("get_animation_info ignore_holds trims the leading/trailing hold to the active range",
          (trimmed["start"], trimmed["end"]) == (5, 10),
          f"{trimmed['start']},{trimmed['end']}")

    # ---- get_animation_info: ignore_holds drops a fully-static (hold-only) object ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 0)], "Flat")
    check("get_animation_info default still reports a fully-static object",
          len(btk.get_animation_info([o])) == 1)
    check("get_animation_info ignore_holds excludes a fully-static object entirely",
          btk.get_animation_info([o], ignore_holds=True) == [])

    # ---- add_intermediate_keys: percent overrides step, scaled to each curve's own span ----
    reset()
    o, fc = keyed_at([(0, 0), (101, 9)])
    added = btk.add_intermediate_keys([o], percent=10)
    check("add_intermediate percent=10 subsamples the interior span to ~10%",
          added == 10 and frames(fc) == [0, 1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101],
          f"added={added} {frames(fc)}")

    # ---- set_current_frame: absolute / relative ----
    reset()
    bpy.context.scene.frame_set(1)
    r = btk.set_current_frame(time=50)
    check("set_current_frame absolute sets the frame directly",
          r == 50 and bpy.context.scene.frame_current == 50, f"r={r}")
    bpy.context.scene.frame_set(10)
    r = btk.set_current_frame(time=5, relative=True)
    check("set_current_frame relative offsets from the current frame",
          r == 15 and bpy.context.scene.frame_current == 15, f"r={r}")

    # ---- set_current_frame: clean-number snapping (preferred / aggressive) ----
    r = btk.set_current_frame(time=24.8, snap_mode="preferred")
    check("set_current_frame snap_mode=preferred rounds to a nearby clean number",
          r == 25, f"r={r}")
    r = btk.set_current_frame(time=48.5, snap_mode="aggressive")
    check("set_current_frame snap_mode=aggressive rounds even when farther",
          r == 50, f"r={r}")
    r = btk.set_current_frame(time=24, snap_mode="none")
    check("set_current_frame snap_mode=none is a no-op on the value", r == 24, f"r={r}")

    # ---- set_current_frame: time=None re-snaps the subframe-aware CURRENT playhead ----
    bpy.context.scene.frame_set(24, subframe=0.8)
    r = btk.set_current_frame(time=None, snap_mode="preferred")
    check("set_current_frame time=None reads the subframe-aware current frame before snapping",
          r == 25, f"r={r}")

    # ---- set_current_frame: invert_snap swaps floor <-> ceil ----
    r = btk.set_current_frame(time=24.5, snap_mode="floor")
    check("set_current_frame snap_mode=floor rounds down", r == 24, f"r={r}")
    r = btk.set_current_frame(time=24.5, snap_mode="floor", invert_snap=True)
    check("set_current_frame invert_snap swaps floor for ceil", r == 25, f"r={r}")
    bpy.context.scene.frame_set(1)

    # ---- set_current_frame: update=False still writes frame_current ----
    r = btk.set_current_frame(time=42, update=False)
    check("set_current_frame update=False still sets frame_current",
          r == 42 and bpy.context.scene.frame_current == 42, f"r={r}")
    bpy.context.scene.frame_set(1)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
