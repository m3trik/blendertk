"""blendertk animation depth-parity test — the option-box behaviors added to close the
animation parity gap: stagger (grouping/intervals/invert/start), adjust-spacing (exact-gap /
selected), move (align/selected), intermediate (time-range / ignore-visibility), snap
(selected / range), tie (absolute), visibility (when/offset) and CSV info.

Run: blender --background --factory-startup --python blendertk/test/test_anim_depth.py
"""

import sys
import os
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


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
    check(
        "stagger sequential keeps first, offsets second",
        frames(fa) == [1, 3] and frames(fb) == [8, 10],
        f"A={frames(fa)} B={frames(fb)}",
    )

    # ---- stagger: fixed intervals ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=10, use_intervals=True)
    check(
        "stagger intervals places blocks at fixed step",
        frames(fa) == [1, 3] and frames(fb) == [11, 13],
        f"A={frames(fa)} B={frames(fb)}",
    )

    # ---- stagger: start_frame override ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, start_frame=20)
    check(
        "stagger start_frame anchors the first block",
        frames(fa) == [20, 22] and frames(fb) == [27, 29],
        f"A={frames(fa)} B={frames(fb)}",
    )

    # ---- stagger: invert reverses which object holds ----
    reset()
    a, fa = keyed_at([(1, 0), (3, 1)], "A")
    b, fb = keyed_at([(1, 0), (3, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, invert=True)
    check(
        "stagger invert holds the last object, moves the first",
        frames(fb) == [1, 3] and frames(fa) == [8, 10],
        f"A={frames(fa)} B={frames(fb)}",
    )

    # ---- stagger: blocks run in TIME order from the earliest key, Invert included ----
    # mayatk (the reference DCC) sorts blocks by start, reverses them on Invert, and
    # starts the run at the earliest key; each layout below was measured in mayapy.
    for label, spans, order, kwargs, expected in (
        (
            "invert starts the reversed run at the earliest key",
            {"A": (0, 10), "B": (20, 30)},
            "AB",
            dict(spacing=0, invert=True),
            {"A": [10, 20], "B": [0, 10]},
        ),
        (
            "lays blocks out by start time, not selection order",
            {"A": (0, 10), "B": (20, 30)},
            "BA",
            dict(spacing=0),
            {"A": [0, 10], "B": [10, 20]},
        ),
        (
            "invert with spacing over three blocks",
            {"A": (0, 10), "B": (20, 30), "C": (40, 50)},
            "ABC",
            dict(spacing=5, invert=True),
            {"A": [30, 40], "B": [15, 25], "C": [0, 10]},
        ),
        (
            "invert moves an overlapping group as one block",
            {"A": (0, 10), "B": (5, 15), "C": (30, 40)},
            "ABC",
            dict(spacing=0, invert=True, group_overlapping=True),
            {"A": [10, 20], "B": [15, 25], "C": [0, 10]},
        ),
        (
            "invert on intervals starts the grid at the earliest key",
            {"A": (0, 10), "B": (20, 30)},
            "AB",
            dict(spacing=100, invert=True, use_intervals=True),
            {"A": [100, 110], "B": [0, 10]},
        ),
    ):
        reset()
        made = {n: keyed_at([(s, 0), (e, 1)], n) for n, (s, e) in spans.items()}
        btk.stagger_keys([made[n][0] for n in order], **kwargs)
        got = {n: frames(fcv) for n, (_o, fcv) in made.items()}
        check(f"stagger {label}", got == expected, f"got={got} want={expected}")

    # ---- stagger: group_overlapping keeps overlapping pair together ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")  # overlaps A
    c, fc = keyed_at([(20, 0), (22, 1)], "C")  # separate
    btk.stagger_keys([a, b, c], spacing=5, group_overlapping=True)
    check(
        "stagger group_overlapping holds the overlapping block, retimes the separate one",
        frames(fa) == [1, 5] and frames(fb) == [3, 7] and frames(fc) == [12, 14],
        f"A={frames(fa)} B={frames(fb)} C={frames(fc)}",
    )

    # contrast: without grouping, B is retimed (does not stay at 3,7)
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")
    btk.stagger_keys([a, b], spacing=5, group_overlapping=False)
    check(
        "stagger without grouping retimes the overlapping object",
        frames(fb) != [3, 7],
        f"B={frames(fb)}",
    )

    # ---- adjust_key_spacing: exact_gap lands first key after frame at frame+amount ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    moved = btk.adjust_key_spacing([o], spacing=5, frame=2, exact_gap=True)
    check(
        "adjust exact_gap shifts keys>=frame so first lands at frame+gap",
        frames(fc) == [1, 7, 12] and moved == 2,
        f"{frames(fc)} moved={moved}",
    )

    # ---- adjust_key_spacing: selected_keys_only shifts only selected ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    next(k for k in fc.keyframe_points if k.co.x == 5).select_control_point = True
    moved = btk.adjust_key_spacing([o], spacing=3, frame=2, selected_keys_only=True)
    check(
        "adjust selected_keys_only shifts only the selected key",
        frames(fc) == [1, 8, 10] and moved == 1,
        f"{frames(fc)} moved={moved}",
    )

    # ---- adjust_key_spacing: relative=True offsets the adjustment point from the current frame ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    bpy.context.scene.frame_set(5)
    moved = btk.adjust_key_spacing(
        [o], spacing=10, frame=15, relative=True
    )  # adjusted = 5+15=20
    check(
        "adjust relative=True offsets frame from the current frame",
        frames(fc) == [10, 30, 40] and moved == 2,
        f"{frames(fc)} moved={moved}",
    )

    # ---- adjust_key_spacing: relative=False (default) treats frame as absolute ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    bpy.context.scene.frame_set(999)  # current frame must not matter
    moved = btk.adjust_key_spacing([o], spacing=5, frame=15)
    check(
        "adjust relative=False treats frame as an absolute frame number",
        frames(fc) == [10, 25, 35] and moved == 2,
        f"{frames(fc)} moved={moved}",
    )
    bpy.context.scene.frame_set(1)

    # ---- adjust_key_spacing: preserve_keys anchors a key at the pivot while it shifts away ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1), (30, 2)])
    moved = btk.adjust_key_spacing([o], spacing=5, frame=20, preserve_keys=True)
    co_pairs = sorted((round(k.co.x, 3), round(k.co.y, 3)) for k in fc.keyframe_points)
    check(
        "adjust preserve_keys anchors a key at the pivot while the original shifts away",
        co_pairs == [(10, 0), (20, 1), (25, 1), (35, 2)] and moved == 2,
        f"{co_pairs} moved={moved}",
    )

    # ---- adjust_key_spacing: objects=None means every scene object, not "nothing" (mirrors
    # optimize_keys/get_animation_info/tie_keyframes/repair_corrupted_curves/fit_playback_range's
    # shared objects=None -> scene-wide convention in this module; backs tentacle's Adjust
    # Spacing "Scope: Entire Scene" cmb036 option, which calls this with objects=None) ----
    reset()
    o1, fc1 = keyed_at([(10, 0), (20, 1)], "A")
    o2, fc2 = keyed_at([(10, 0), (20, 1)], "B")
    moved = btk.adjust_key_spacing(None, spacing=5, frame=15)
    check(
        "adjust_key_spacing(objects=None) shifts keys across every scene object",
        frames(fc1) == [10, 25] and frames(fc2) == [10, 25] and moved == 2,
        f"A={frames(fc1)} B={frames(fc2)} moved={moved}",
    )

    # ---- move_keys_to_frame: align=end anchors the LAST key to the frame ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    btk.move_keys_to_frame([o], frame=20, align="end")
    check(
        "move align=end lands the last key on the frame",
        frames(fc) == [11, 15, 20],
        f"{frames(fc)}",
    )

    # ---- move_keys_to_frame: align=auto picks the nearer end (mayatk's rule:
    # End when the range's midpoint sits before the frame, Start otherwise) ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    btk.move_keys_to_frame([o], frame=20, align="auto")
    check(
        "move align=auto lands the last key on a frame after the range",
        frames(fc) == [11, 15, 20],
        f"{frames(fc)}",
    )
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    btk.move_keys_to_frame([o], frame=-20, align="auto")
    check(
        "move align=auto lands the first key on a frame before the range",
        frames(fc) == [-20, -16, -11],
        f"{frames(fc)}",
    )
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    for k in fc.keyframe_points:
        k.select_control_point = k.co.x in (1, 5)
    btk.move_keys_to_frame([o], frame=30, selected_keys_only=True, align="auto")
    check(
        "move align=auto with selected keys anchors the selection's nearer end",
        frames(fc) == [10, 26, 30],
        f"{frames(fc)}",
    )

    # ---- move_keys_to_frame: selected_keys_only moves only the selected key ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    next(k for k in fc.keyframe_points if k.co.x == 5).select_control_point = True
    n = btk.move_keys_to_frame([o], frame=20, selected_keys_only=True)
    check(
        "move selected_keys_only moves only the selected key",
        frames(fc) == [1, 10, 20] and n == 1,
        f"{frames(fc)} n={n}",
    )

    # ---- add_intermediate_keys: time_range bounds the inserted frames ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 9)])
    added = btk.add_intermediate_keys([o], step=1, time_range=(3, 6))
    check(
        "add_intermediate time_range only fills inside the window",
        frames(fc) == [1, 3, 4, 5, 6, 10] and added == 4,
        f"{frames(fc)} added={added}",
    )

    # ---- add_intermediate_keys: ignore_visibility leaves vis curves alone ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 9)])
    o.hide_viewport = False
    o.keyframe_insert("hide_viewport", frame=1)
    o.keyframe_insert("hide_viewport", frame=10)
    btk.add_intermediate_keys([o], step=1, ignore_visibility=True)
    check(
        "add_intermediate ignore_visibility skips hide_viewport",
        vis_frames(o) == [1, 10] and len(fc.keyframe_points) > 2,
        f"vis={vis_frames(o)} loc={len(fc.keyframe_points)}",
    )

    # ---- remove_intermediate_keys: time_range only removes interior keys inside it ----
    reset()
    o, fc = keyed_at([(1, 0), (3, 1), (5, 2), (7, 3), (10, 4)])
    removed = btk.remove_intermediate_keys([o], time_range=(2, 6))
    check(
        "remove_intermediate time_range removes only interior keys in window",
        frames(fc) == [1, 7, 10] and removed == 2,
        f"{frames(fc)} removed={removed}",
    )

    # ---- snap_keys: selected_only + return count ----
    reset()
    o, fc = keyed_at([(1.4, 0), (2.6, 1), (5.2, 2)])
    next(
        k for k in fc.keyframe_points if abs(k.co.x - 2.6) < 1e-3
    ).select_control_point = True
    n = btk.snap_keys([o], selected_only=True)
    check(
        "snap selected_only snaps only the selected key (returns count)",
        frames(fc) == [1.4, 3, 5.2] and n == 1,
        f"{frames(fc)} n={n}",
    )

    # ---- snap_keys: time_range ----
    reset()
    o, fc = keyed_at([(1.4, 0), (2.6, 1), (5.2, 2)])
    n = btk.snap_keys([o], time_range=(2, 4))
    check(
        "snap time_range snaps only keys inside the window",
        frames(fc) == [1.4, 3, 5.2] and n == 1,
        f"{frames(fc)} n={n}",
    )

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
    check(
        "snap method=preferred snaps close values to clean numbers",
        frames(fc) == [25],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(48.5, 0)])
    btk.snap_keys([o], method="aggressive_preferred")
    check(
        "snap method=aggressive_preferred snaps even farther values to clean numbers",
        frames(fc) == [50],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(10.7, 0)])
    n = btk.snap_keys([o], method="none")
    check(
        "snap method=none is a documented no-op (no keys touched, count 0)",
        frames(fc) == [10.7] and n == 0,
        f"{frames(fc)} n={n}",
    )

    # ---- snap_keys: objects=None is the whole scene (repair_corrupted_curves' default) ----
    reset()
    a, fca = keyed_at([(1.4, 0)], name="AnimA")
    b, fcb = keyed_at([(5.2, 0)], name="AnimB")
    n = btk.snap_keys()
    check(
        "snap with no objects sweeps every scene object",
        frames(fca) == [1] and frames(fcb) == [5] and n == 2,
        f"a={frames(fca)} b={frames(fcb)} n={n}",
    )

    # ---- snap_keys: handles travel with the key (curve shape preserved) ----
    reset()
    o, fc = keyed_at([(1.4, 0), (5, 1)])
    k = next(k for k in fc.keyframe_points if abs(k.co.x - 1.4) < 1e-3)
    k.handle_left_type = k.handle_right_type = "FREE"
    k.handle_left.x = k.co.x - 2.0
    k.handle_right.x = k.co.x + 2.0
    btk.snap_keys([o])
    check(
        "snap moves FREE handles in lock-step with the key",
        abs(k.co.x - 1.0) < 1e-6
        and abs(k.handle_left.x - (k.co.x - 2.0)) < 1e-4
        and abs(k.handle_right.x - (k.co.x + 2.0)) < 1e-4,
        f"co={k.co.x} hl={k.handle_left.x} hr={k.handle_right.x}",
    )

    # ---- tie_keyframes: absolute uses the keyed extent, not the scene range ----
    reset()
    o, fc = keyed_at([(5, 0), (6, 1), (7, 2)])
    changed_abs = btk.tie_keyframes([o], absolute=True)
    check(
        "tie absolute adds no scene-range bookends (keys already span the extent)",
        changed_abs == 0 and frames(fc) == [5, 6, 7],
        f"changed={changed_abs} {frames(fc)}",
    )
    reset()
    o, fc = keyed_at([(5, 0), (6, 1), (7, 2)])
    changed_scene = btk.tie_keyframes([o], absolute=False)
    check(
        "tie non-absolute bookends at the scene frame range",
        changed_scene == 2,
        f"changed={changed_scene} {frames(fc)}",
    )

    # ---- set_visibility_keys: when=both keys the range ends; offset shifts ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="both")
    check(
        "visibility when=both keys range start and end",
        vis_frames(o) == [10, 20],
        f"{vis_frames(o)}",
    )
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="start", offset=2)
    check("visibility when=start + offset", vis_frames(o) == [12], f"{vis_frames(o)}")

    # ---- set_visibility_keys: before_start / after_end / current (cmb002 full vocabulary) ----
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="before_start")
    check(
        "visibility when=before_start keys one frame before the range start",
        vis_frames(o) == [9],
        f"{vis_frames(o)}",
    )
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    btk.set_visibility_keys([o], visible=False, when="after_end")
    check(
        "visibility when=after_end keys one frame after the range end",
        vis_frames(o) == [21],
        f"{vis_frames(o)}",
    )
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)])
    bpy.context.scene.frame_set(15)
    btk.set_visibility_keys([o], visible=False, when="current")
    check(
        "visibility when=current keys the playhead frame",
        vis_frames(o) == [15],
        f"{vis_frames(o)}",
    )
    bpy.context.scene.frame_set(1)

    # ---- set_visibility_keys: group_overlapping shares one combined range ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")  # overlaps A
    c, fc_ = keyed_at([(20, 0), (22, 1)], "C")  # separate
    btk.set_visibility_keys(
        [a, b, c], visible=False, when="start", group_overlapping=True
    )
    check(
        "visibility group_overlapping keys the group's combined start on every member",
        vis_frames(a) == [1] and vis_frames(b) == [1] and vis_frames(c) == [20],
        f"A={vis_frames(a)} B={vis_frames(b)} C={vis_frames(c)}",
    )

    # contrast: without grouping, each object keys its own range start
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b, fb = keyed_at([(3, 0), (7, 1)], "B")
    btk.set_visibility_keys(
        [a, b], visible=False, when="start", group_overlapping=False
    )
    check(
        "visibility without grouping keys each object's own start",
        vis_frames(a) == [1] and vis_frames(b) == [3],
        f"A={vis_frames(a)} B={vis_frames(b)}",
    )

    # ---- delete_keys: time-scoped deletion relative to the current frame ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    cleared = btk.delete_keys([o], time="after")
    check(
        "delete_keys time='after' drops keys strictly after the current frame",
        frames(fc) == [1, 5] and cleared == [o],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="before")
    check(
        "delete_keys time='before' drops keys strictly before the current frame",
        frames(fc) == [5, 10],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="current")
    check(
        "delete_keys time='current' drops only the key at the current frame",
        frames(fc) == [1, 10],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="before|current")
    check(
        "delete_keys time='before|current' includes the current frame",
        frames(fc) == [10],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 2)])
    bpy.context.scene.frame_set(5)
    btk.delete_keys([o], time="after|current")
    check(
        "delete_keys time='after|current' includes the current frame",
        frames(fc) == [1],
        f"{frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1)])
    bpy.context.scene.frame_set(999)
    no_hit = btk.delete_keys([o], time="after")
    check(
        "delete_keys time scope with nothing matching returns no touched objects and keeps keys",
        no_hit == [] and frames(fc) == [1, 5],
        f"{no_hit} {frames(fc)}",
    )

    reset()
    o, fc = keyed_at([(1, 0), (5, 1)])
    cleared_all = btk.delete_keys([o])
    check(
        "delete_keys default (time=None) still clears all animation data",
        cleared_all == [o] and getattr(o, "animation_data", None) is None,
        f"{cleared_all}",
    )
    bpy.context.scene.frame_set(1)

    # ---- copy_keys / paste_keys: action mode (whole-action, target_time realigns) ----
    reset()
    a, fa = keyed_at([(1, 0), (5, 1)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    action = btk.copy_keys(a, mode="action")
    pasted = btk.paste_keys([b], action)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check(
        "paste_keys action-mode, no target_time, pastes unshifted at the original frames",
        frames(fb) == [1, 5] and pasted == [b],
        f"{frames(fb)}",
    )
    check(
        "paste_keys action-mode links an INDEPENDENT copy (own Action datablock)",
        b.animation_data.action is not a.animation_data.action,
    )
    for k in fb.keyframe_points:  # nudging the copy must not move the source
        k.co.x += 1
    fb.update()
    check(
        "editing the pasted copy leaves the source's keys untouched",
        frames(fa) == [1, 5],
        f"{frames(fa)}",
    )

    reset()
    a, fa = keyed_at([(10, 0), (14, 1)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    action = btk.copy_keys(a, mode="action")
    btk.paste_keys([b], action, target_time=100)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check(
        "paste_keys action-mode target_time re-anchors the earliest key there",
        frames(fb) == [100, 104],
        f"{frames(fb)}",
    )

    # ---- copy_keys / paste_keys: current_frame mode (pose snapshot) ----
    reset()
    a, fa = keyed_at([(1, 0.0), (10, 5.0)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    bpy.context.scene.frame_set(4)  # captures whatever fa evaluates to at frame 4
    buf = btk.copy_keys(a, mode="current_frame")
    btk.paste_keys([b], buf)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check(
        "copy/paste current_frame keys the sampled value at the frame it was captured",
        frames(fb) == [4]
        and round(fb.keyframe_points[0].co.y, 3) == round(fa.evaluate(4), 3),
        f"{frames(fb)} y={fb.keyframe_points[0].co.y if fb.keyframe_points else None}",
    )

    reset()
    a, fa = keyed_at([(1, 0.0), (10, 5.0)], "A")
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    bpy.context.scene.frame_set(4)
    buf = btk.copy_keys(a, mode="current_frame")
    btk.paste_keys([b], buf, target_time=50)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check(
        "copy/paste current_frame honors an explicit target_time",
        frames(fb) == [50],
        f"{frames(fb)}",
    )
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
    check(
        "copy/paste selected-mode carries only the selected key, unshifted",
        frames(fb) == [5],
        f"{frames(fb)}",
    )

    reset()
    a, fa = keyed_at([(1, 0), (5, 1), (10, 2)], "A")
    next(k for k in fa.keyframe_points if k.co.x == 5).select_control_point = True
    b = bpy.data.objects.new("B", None)
    bpy.context.collection.objects.link(b)
    buf = btk.copy_keys(a, mode="selected")
    btk.paste_keys([b], buf, target_time=100)
    fb = next(fc for fc in btk.get_fcurves([b]) if fc.array_index == 0)
    check(
        "copy/paste selected-mode target_time shifts the (single) earliest key there",
        frames(fb) == [100],
        f"{frames(fb)}",
    )

    reset()
    a, fa = keyed_at([(1, 0), (5, 1), (10, 2)], "A")
    empty_buf = btk.copy_keys(a, mode="selected")
    check(
        "copy_keys selected-mode returns None when nothing is selected",
        empty_buf is None,
    )

    # ---- format_animation_info_csv ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 1)], "Cube")
    csv = btk.format_animation_info_csv(btk.get_animation_info([o]))
    check(
        "csv info has a header + a data row",
        csv.splitlines()[0].startswith("Object,Action") and "Cube" in csv,
        repr(csv[:40]),
    )
    check("csv info empty -> ''", btk.format_animation_info_csv([]) == "")

    # ---- get_animation_info: ignore_holds trims leading/trailing static holds ----
    reset()
    o, fc = keyed_at([(1, 0), (5, 0), (10, 5), (15, 5)], "Cube")
    full = btk.get_animation_info([o])[0]
    check(
        "get_animation_info default reports the full first/last key extent",
        (full["start"], full["end"]) == (1, 15),
        f"{full['start']},{full['end']}",
    )
    trimmed = btk.get_animation_info([o], ignore_holds=True)[0]
    check(
        "get_animation_info ignore_holds trims the leading/trailing hold to the active range",
        (trimmed["start"], trimmed["end"]) == (5, 10),
        f"{trimmed['start']},{trimmed['end']}",
    )

    # ---- get_animation_info: ignore_holds drops a fully-static (hold-only) object ----
    reset()
    o, fc = keyed_at([(1, 0), (10, 0)], "Flat")
    check(
        "get_animation_info default still reports a fully-static object",
        len(btk.get_animation_info([o])) == 1,
    )
    check(
        "get_animation_info ignore_holds excludes a fully-static object entirely",
        btk.get_animation_info([o], ignore_holds=True) == [],
    )

    # ---- add_intermediate_keys: percent overrides step, scaled to each curve's own span ----
    reset()
    o, fc = keyed_at([(0, 0), (101, 9)])
    added = btk.add_intermediate_keys([o], percent=10)
    check(
        "add_intermediate percent=10 subsamples the interior span to ~10%",
        added == 10 and frames(fc) == [0, 1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101],
        f"added={added} {frames(fc)}",
    )

    # ---- set_current_frame: absolute / relative ----
    reset()
    bpy.context.scene.frame_set(1)
    r = btk.set_current_frame(time=50)
    check(
        "set_current_frame absolute sets the frame directly",
        r == 50 and bpy.context.scene.frame_current == 50,
        f"r={r}",
    )
    bpy.context.scene.frame_set(10)
    r = btk.set_current_frame(time=5, relative=True)
    check(
        "set_current_frame relative offsets from the current frame",
        r == 15 and bpy.context.scene.frame_current == 15,
        f"r={r}",
    )

    # ---- set_current_frame: clean-number snapping (preferred / aggressive) ----
    r = btk.set_current_frame(time=24.8, snap_mode="preferred")
    check(
        "set_current_frame snap_mode=preferred rounds to a nearby clean number",
        r == 25,
        f"r={r}",
    )
    r = btk.set_current_frame(time=48.5, snap_mode="aggressive")
    check(
        "set_current_frame snap_mode=aggressive rounds even when farther",
        r == 50,
        f"r={r}",
    )
    r = btk.set_current_frame(time=24, snap_mode="none")
    check("set_current_frame snap_mode=none is a no-op on the value", r == 24, f"r={r}")

    # ---- set_current_frame: time=None re-snaps the subframe-aware CURRENT playhead ----
    bpy.context.scene.frame_set(24, subframe=0.8)
    r = btk.set_current_frame(time=None, snap_mode="preferred")
    check(
        "set_current_frame time=None reads the subframe-aware current frame before snapping",
        r == 25,
        f"r={r}",
    )

    # ---- set_current_frame: invert_snap swaps floor <-> ceil ----
    r = btk.set_current_frame(time=24.5, snap_mode="floor")
    check("set_current_frame snap_mode=floor rounds down", r == 24, f"r={r}")
    r = btk.set_current_frame(time=24.5, snap_mode="floor", invert_snap=True)
    check("set_current_frame invert_snap swaps floor for ceil", r == 25, f"r={r}")
    bpy.context.scene.frame_set(1)

    # ---- set_current_frame: update=False still writes frame_current ----
    r = btk.set_current_frame(time=42, update=False)
    check(
        "set_current_frame update=False still sets frame_current",
        r == 42 and bpy.context.scene.frame_current == 42,
        f"r={r}",
    )
    bpy.context.scene.frame_set(1)

    # ==== the composed Animation panel's calls: mayatk's names and signatures ====
    def select_only(*objs):
        btk.CoreUtils.select(list(objs), mode="replace")
        bpy.context.view_layer.objects.active = objs[0]

    check("ShotStore is mayatk's name for the store class", btk.ShotStore is btk.BlenderShotStore)

    # invert_keys: None is the selection; mayatk's mode and time names
    reset()
    o, fc = keyed_at([(10, 0), (20, 1)], "Inv")
    select_only(o)
    btk.invert_keys(None, mode="horizontal")
    check("invert_keys(None, mode='horizontal') mirrors the selection in place",
          frames(fc) == [10.0, 20.0] and fc.keyframe_points[0].co.y == 1.0, frames(fc))
    btk.invert_keys([o], mode="horizontal", time=30, relative=False)
    check("invert_keys time= is start_frame: the copy starts there",
          frames(fc) == [10.0, 20.0, 30.0, 40.0], frames(fc))

    # adjust_key_spacing: time is frame
    reset()
    o, fc = keyed_at([(1, 0), (10, 1)], "Spc")
    btk.adjust_key_spacing([o], spacing=5, time=5, relative=False)
    check("adjust_key_spacing time= is frame", frames(fc) == [1.0, 15.0], frames(fc))

    # intermediate keys: ignore + an open range end
    reset()
    o, fc = keyed_at([(1, 0), (5, 4)], "Mid")
    o.hide_viewport = False
    o.keyframe_insert("hide_viewport", frame=1)
    o.keyframe_insert("hide_viewport", frame=5)
    added = btk.add_intermediate_keys([o], time_range=(None, 3), percent=100, ignore="visibility")
    vis = next(f for f in btk.get_fcurves([o]) if f.data_path == "hide_viewport")
    check("add_intermediate_keys: ignore='visibility' and a None start",
          frames(fc) == [1.0, 2.0, 5.0] and frames(vis) == [1.0, 5.0], f"{added} {frames(fc)}")
    removed = btk.remove_intermediate_keys([o], time_range=(None, None), ignore="visibility")
    check("remove_intermediate_keys: None bounds are each curve's own ends",
          removed == 1 and frames(fc) == [1.0, 5.0], f"{removed} {frames(fc)}")

    # snap_keys_to_frames: None is the SELECTION (snap_keys' None is the scene)
    reset()
    a, fa = keyed_at([(1.4, 0), (5, 1)], "SnapSel")
    b, fb = keyed_at([(2.4, 0), (6, 1)], "SnapOther")
    select_only(a)
    n = btk.snap_keys_to_frames(None)
    check("snap_keys_to_frames(None) snaps the selection only",
          n == 1 and frames(fa) == [1.0, 5.0] and frames(fb) == [2.4, 6.0], f"{n}")

    # repair_fractional_keys: None is the scene; reports what is left off-frame
    rep = btk.repair_fractional_keys(None)
    check("repair_fractional_keys(None) sweeps the scene",
          rep["snapped"] == 1 and rep["off_frame"] == 0 and frames(fb) == [2.0, 6.0], rep)
    reset()
    bare = bpy.data.objects.new("Bare", None)
    bpy.context.collection.objects.link(bare)
    check("repair_fractional_keys on an unanimated object finds no curves",
          btk.repair_fractional_keys([bare]) == {"curves": 0, "snapped": 0, "off_frame": 0})

    # tie / untie
    reset()
    o, fc = keyed_at([(5, 0), (10, 1)], "Tie")
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 20
    tied = btk.tie_keyframes([o])
    untied = btk.untie_keyframes([o])
    check("untie_keyframes removes what tie_keyframes inserted",
          tied == 2 and untied == 2 and frames(fc) == [5.0, 10.0], f"{tied} {untied}")

    # copy_keys: None is the active object; auto = selected keys, else the pose
    reset()
    o, fc = keyed_at([(1, 0), (10, 5)], "Copy")
    select_only(o)
    sc.frame_set(1)
    pose = btk.copy_keys(None, "auto")
    check("copy_keys(None, 'auto') with no key picked is the current-frame pose",
          isinstance(pose, dict) and pose["mode"] == "current_frame", pose)
    check("copied_key_counts counts a pose's values",
          btk.copied_key_counts(pose) == {"keys": 1, "attributes": 1, "objects": 1})
    fc.keyframe_points[1].select_control_point = True
    picked = btk.copy_keys(None, "auto")
    check("copy_keys(None, 'auto') prefers the picked keys",
          picked and picked["mode"] == "selected"
          and btk.copied_key_counts(picked)["keys"] == 1, picked)
    fc.select = False
    check("copy_keys 'channel_box' reads only the selected channels",
          btk.copy_keys(o, "channel_box") is None)

    # transfer_keyframes(None): the active onto the other selected
    reset()
    src, sfc = keyed_at([(1, 0), (10, 5)], "XSrc")
    tgt = bpy.data.objects.new("XTgt", None)
    bpy.context.collection.objects.link(tgt)
    select_only(src)
    try:
        btk.transfer_keyframes(None)
        refused = False
    except ValueError:
        refused = True
    check("transfer_keyframes(None) refuses a source with no target", refused)
    btk.CoreUtils.select([src, tgt], mode="replace")
    bpy.context.view_layer.objects.active = src
    moved = btk.transfer_keyframes(None, transfer_tangents=False)
    check("transfer_keyframes(None) copies the active onto the other selected",
          moved == [tgt] and btk.get_fcurves([tgt]), moved)

    # set_key_interpolation
    reset()
    o, fc = keyed_at([(1, 0), (5, 1), (10, 0)], "Interp")
    select_only(o)
    try:
        btk.set_key_interpolation(None, keys="selected")
        refused = False
    except ValueError:
        refused = True
    check("set_key_interpolation keys='selected' refuses with nothing picked", refused)
    fc.keyframe_points[1].select_control_point = True
    r = btk.set_key_interpolation(None, interpolation="linear", keys="auto")
    kinds = [k.interpolation for k in fc.keyframe_points]
    check("set_key_interpolation auto sets the picked keys only",
          r == {"curves": 1, "objects": 1} and kinds == ["BEZIER", "LINEAR", "BEZIER"], kinds)
    btk.set_key_interpolation([o], interpolation="stepped", keys="all")
    check("set_key_interpolation all steps every key",
          {k.interpolation for k in fc.keyframe_points} == {"CONSTANT"})

    # repair_corrupted_curves: mayatk's keyword names
    reset()
    o, fc = keyed_at([(1, 0), (10, 1)], "Corrupt")
    fc.keyframe_points[0].co.y = 1e9
    r = btk.repair_corrupted_curves([o], delete_corrupted=False, time_range_threshold=1e6)
    check("repair_corrupted_curves takes mayatk's names",
          r["corrupted_found"] == 1 and frames(fc) == [10.0], r)

    # optimize_keys: mayatk's progress callback
    reset()
    o, fc = keyed_at([(0, 1), (10, 1), (20, 1), (30, 5)], "Opt")
    heard = []
    btk.optimize_keys([o], progress_callback=lambda *a: heard.append(a), recursive=True, quiet=True)
    check("optimize_keys reports progress per object", heard == [(0, 1, "Opt")], heard)

    # stagger: a fraction of the block's duration; touching implies grouping
    reset()
    a, fa = keyed_at([(0, 0), (10, 1)], "SA")
    b, fb = keyed_at([(0, 0), (10, 1)], "SB")
    btk.StaggerKeys.stagger_keys([a, b], spacing=0.5, avoid_overlap=True, verbose=True)
    check("stagger_keys spacing 0.5 leaves a half-block gap",
          frames(fb) == [15.0, 25.0], f"a={frames(fa)} b={frames(fb)}")
    reset()
    a, fa = keyed_at([(0, 0), (10, 1)], "TA")
    b, fb = keyed_at([(10, 0), (20, 1)], "TB")
    # Staggered apart, B would move to 15; grouped with A (they touch), it stays.
    btk.StaggerKeys.stagger_keys([a, b], spacing=5, merge_touching=True)
    check("stagger_keys merge_touching groups touching blocks without group_overlapping",
          frames(fa) == [0.0, 10.0] and frames(fb) == [10.0, 20.0],
          f"{frames(fa)} {frames(fb)}")

    # scale_keys takes mayatk's flags
    reset()
    o, fc = keyed_at([(0, 0), (10, 1)], "Scl")
    n = btk.ScaleKeys.scale_keys(
        objects=[o], factor=2.0, keys="auto", channel_box_attrs_only=False,
        prevent_overlap=True, verbose=True,
    )
    check("scale_keys accepts mayatk's flags", n and frames(fc) == [0.0, 20.0], n)

    # SegmentKeys.format_scene_info_html: mayatk's report entry point
    html = btk.SegmentKeys.format_scene_info_html([o], traversal="upstream")
    csv = btk.SegmentKeys.format_scene_info_html([o], csv_output=True)
    check("SegmentKeys.format_scene_info_html renders HTML and CSV",
          "Scl" in html and "Scl" in csv and "," in csv)
    check("SegmentKeys.format_scene_info_html is empty with nothing animated",
          btk.SegmentKeys.format_scene_info_html([bpy.data.objects.new("None", None)]) == "")
    bpy.context.scene.frame_set(1)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
