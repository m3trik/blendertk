# !/usr/bin/python
# coding=utf-8
"""Blender ShotSequencer engine test — timeline moves over the shared planner.

bpy-only suite: builds a real headless scene of three keyed cubes bound to three
shots and drives ``ShotSequencer`` through the operations the Shots panel calls,
asserting that BOTH the shot bounds AND the underlying fcurve keyframes actually
move together:

- ``move_shot`` advances a shot's start (its own keys shift + downstream shots ripple);
- ``ripple_downstream`` shifts only at/after a pivot frame;
- ``apply_gap(scope="all")`` respaces the whole set;
- ``move_shot_to_position`` reorders via the pure ``plan_reorder`` + apply park/land,
  teleporting each shot's keys to the reordered slot;
- ``trim_shot_to_content`` shrinks a padded shot's bounds inward to its keyed content
  WITHOUT moving the shot's own keys, rippling the neighbour.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_shot_sequencer.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_sequencer_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy

    from blendertk import BlenderShotStore
    from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer

    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_seq_prefs_")
    BlenderShotStore.clear_active()

    def key_times(obj_name):
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return []
        return sorted(
            {
                round(float(kp.co[0]), 3)
                for fc in BlenderShotStore.iter_action_fcurves(obj)
                for kp in fc.keyframe_points
            }
        )

    def build_scene():
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        def keyed(name, frames):
            bpy.ops.mesh.primitive_cube_add()
            o = bpy.context.active_object
            o.name = name
            for f in frames:
                o.location = (f * 0.1, 0.0, 0.0)
                o.keyframe_insert(data_path="location", frame=f)
            return o

        keyed("A", list(range(0, 11)))  # keys 0..10
        keyed("B", list(range(20, 31)))  # keys 20..30
        keyed("C", list(range(40, 51)))  # keys 40..50

    def fresh_store():
        BlenderShotStore.clear_active()
        store = BlenderShotStore()
        store.define_shot("A", 0, 10, objects=["A"])
        store.define_shot("B", 20, 30, objects=["B"])
        store.define_shot("C", 40, 50, objects=["C"])
        return store

    # ---- move_shot: pivot keys shift + downstream ripple ------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.move_shot(a_id, 5)  # delta +5

    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    check(
        "move_shot: A bounds shifted +5",
        (a.start, a.end) == (5, 15),
        f"{(a.start, a.end)}",
    )
    check(
        "move_shot: B rippled +5", (b.start, b.end) == (25, 35), f"{(b.start, b.end)}"
    )
    check(
        "move_shot: C rippled +5", (c.start, c.end) == (45, 55), f"{(c.start, c.end)}"
    )
    check(
        "move_shot: A keys shifted +5",
        key_times("A") == [round(5 + i, 3) for i in range(11)],
        f"{key_times('A')[:3]}..",
    )
    check(
        "move_shot: B keys shifted +5",
        key_times("B") == [round(25 + i, 3) for i in range(11)],
        f"{key_times('B')[:3]}..",
    )
    check(
        "move_shot: C keys shifted +5",
        key_times("C") == [round(45 + i, 3) for i in range(11)],
        f"{key_times('C')[:3]}..",
    )

    # ---- ripple_downstream directly --------------------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    # shift everything starting at/after frame 40 by +10 (only C qualifies)
    seq.ripple_downstream(b_id, 40, 10)
    c = store.shot_by_name("C")
    check(
        "ripple_downstream: C bounds +10",
        (c.start, c.end) == (50, 60),
        f"{(c.start, c.end)}",
    )
    check(
        "ripple_downstream: C keys +10",
        key_times("C") == [round(50 + i, 3) for i in range(11)],
        f"{key_times('C')[:3]}..",
    )
    check("ripple_downstream: A untouched", key_times("A") == list(range(0, 11)))
    check(
        "ripple_downstream: B untouched (pivot excluded, before frame)",
        key_times("B") == list(range(20, 31)),
    )

    # ---- apply_gap(all): respace whole set -------------------------------
    build_scene()
    store = fresh_store()
    store.gap = 5
    seq = ShotSequencer(store)
    seq.apply_gap(5, scope="all")
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    # anchor at A.start=0, durations 10 each, gap 5 -> A[0,10] B[15,25] C[30,40]
    check("apply_gap all: A[0,10]", (a.start, a.end) == (0, 10), f"{(a.start, a.end)}")
    check(
        "apply_gap all: B[15,25]", (b.start, b.end) == (15, 25), f"{(b.start, b.end)}"
    )
    check(
        "apply_gap all: C[30,40]", (c.start, c.end) == (30, 40), f"{(c.start, c.end)}"
    )
    check(
        "apply_gap all: B keys respaced to [15..25]",
        key_times("B") == [round(15 + i, 3) for i in range(11)],
        f"{key_times('B')[:3]}..",
    )
    check(
        "apply_gap all: C keys respaced to [30..40]",
        key_times("C") == [round(30 + i, 3) for i in range(11)],
        f"{key_times('C')[:3]}..",
    )

    # ---- move_shot_to_position: reorder via plan_reorder + park/land ------
    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.move_shot_to_position(a_id, 3)  # A -> last
    order = [s.name for s in store.sorted_shots()]
    check("reorder: order is B,C,A", order == ["B", "C", "A"], f"{order}")
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    # B anchored at old-first-start 0 -> B[0,10] C[20,30] A[40,50], gap 10
    check("reorder: B[0,10]", (b.start, b.end) == (0, 10), f"{(b.start, b.end)}")
    check("reorder: C[20,30]", (c.start, c.end) == (20, 30), f"{(c.start, c.end)}")
    check("reorder: A[40,50]", (a.start, a.end) == (40, 50), f"{(a.start, a.end)}")
    # keys must have followed each shot to its new slot
    check(
        "reorder: A keys teleported to [40..50]",
        key_times("A") == [round(40 + i, 3) for i in range(11)],
        f"{key_times('A')[:3]}..",
    )
    check(
        "reorder: B keys landed at [0..10]",
        key_times("B") == list(range(0, 11)),
        f"{key_times('B')[:3]}..",
    )
    check(
        "reorder: C keys landed at [20..30]",
        key_times("C") == list(range(20, 31)),
        f"{key_times('C')[:3]}..",
    )
    # no keys stranded in the park zone (>1e5)
    stranded = [t for n in ("A", "B", "C") for t in key_times(n) if t > 1e5]
    check("reorder: no keys stranded in park zone", not stranded, f"{stranded[:3]}")

    # ---- trim_shot_to_content: shrink bounds inward, own keys unmoved -----
    # Custom scene so each object's keys match its shot's content exactly (A[0..10],
    # B[20..30], C[45..55]) — B's shot is deliberately padded [15,40] around its
    # [20..30] content so trim has empty space to remove on both sides.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    def keyed(name, frames):
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        o.name = name
        for f in frames:
            o.location = (f * 0.1, 0.0, 0.0)
            o.keyframe_insert(data_path="location", frame=f)
        return o

    keyed("A", list(range(0, 11)))  # keys 0..10
    keyed("B", list(range(20, 31)))  # keys 20..30 (content)
    keyed("C", list(range(45, 56)))  # keys 45..55

    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("A", 0, 10, objects=["A"])
    store.define_shot("B", 15, 40, objects=["B"])  # padded: content is 20..30
    store.define_shot("C", 45, 55, objects=["C"])
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    b_keys_before = key_times("B")
    head, tail = seq.trim_shot_to_content(b_id)
    b = store.shot_by_name("B")
    check(
        "trim: B bounds pulled to content [20,30]",
        (b.start, b.end) == (20, 30),
        f"{(b.start, b.end)}",
    )
    check("trim: head delta +5", head == 5, f"{head}")
    check("trim: tail delta -10", tail == -10, f"{tail}")
    check(
        "trim: B own keys NOT moved",
        key_times("B") == b_keys_before,
        f"{key_times('B')[:3]}..",
    )
    # C ripples by tail delta (-10) since its start (45) >= old_end (40)
    c = store.shot_by_name("C")
    check(
        "trim: C rippled by tail delta -10",
        (c.start, c.end) == (35, 45),
        f"{(c.start, c.end)}",
    )
    check(
        "trim: C keys rippled -10",
        key_times("C") == [round(45 + i - 10, 3) for i in range(11)],
        f"{key_times('C')[:3]}..",
    )
    # A ripples upstream by head delta (+5): its end (10) <= old_start (15), so the
    # leading gap trim created is closed and the original A→B gap (5) is preserved.
    a = store.shot_by_name("A")
    check(
        "trim: A rippled upstream by head delta +5",
        (a.start, a.end) == (5, 15),
        f"{(a.start, a.end)}",
    )
    check(
        "trim: A keys rippled +5",
        key_times("A") == [round(0 + i + 5, 3) for i in range(11)],
        f"{key_times('A')[:3]}..",
    )
    check(
        "trim: original A->B and B->C gaps (5) preserved",
        (b.start - a.end == 5) and (c.start - b.end == 5),
        f"A->B={b.start - a.end} B->C={c.start - b.end}",
    )

    # ---- move_object_keys: shift one object's keys, bounds untouched ------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    seq.move_object_keys("A", 0, 10, 3)  # delta +3
    a = store.shot_by_name("A")
    check(
        "move_object_keys: A keys shifted to 3..13",
        key_times("A") == [round(3 + i, 3) for i in range(11)],
        f"{key_times('A')[:3]}..",
    )
    check(
        "move_object_keys: shot A bounds untouched",
        (a.start, a.end) == (0, 10),
        f"{(a.start, a.end)}",
    )

    # ---- scale_object_keys: double a run in place ------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    seq.scale_object_keys("A", 0, 10, 0, 20)  # scale x2
    check(
        "scale_object_keys: A keys doubled 0,2,..20",
        key_times("A") == [round(i * 2.0, 3) for i in range(11)],
        f"{key_times('A')[:4]}..",
    )

    # ---- resize_shot: scale keys both edges + ripple downstream ----------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    seq.resize_shot(b_id, 20, 40)  # B[20,30] -> [20,40] (x2), tail +10
    b, c = store.shot_by_name("B"), store.shot_by_name("C")
    check(
        "resize_shot: B bounds [20,40]",
        (b.start, b.end) == (20, 40),
        f"{(b.start, b.end)}",
    )
    check(
        "resize_shot: B keys scaled 20,22,..40",
        key_times("B") == [round(20 + i * 2.0, 3) for i in range(11)],
        f"{key_times('B')[:4]}..",
    )
    check(
        "resize_shot: C rippled +10 by tail delta",
        (c.start, c.end) == (50, 60),
        f"{(c.start, c.end)}",
    )

    # ---- move_object_in_shot: grow shot + ripple when clip overruns ------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.move_object_in_shot(a_id, "A", 0, 10, 5)  # A keys ->5..15, end 10->15
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    check(
        "move_object_in_shot: A keys ->5..15",
        key_times("A") == [round(5 + i, 3) for i in range(11)],
        f"{key_times('A')[:3]}..",
    )
    check(
        "move_object_in_shot: A shot grew end to 15",
        (a.start, a.end) == (0, 15),
        f"{(a.start, a.end)}",
    )
    check(
        "move_object_in_shot: B rippled +5",
        (b.start, b.end) == (25, 35),
        f"{(b.start, b.end)}",
    )
    check(
        "move_object_in_shot: C rippled +5",
        (c.start, c.end) == (45, 55),
        f"{(c.start, c.end)}",
    )

    # ---- collect_object_segments: per-object keyed span ------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    segs = seq.collect_object_segments(store.shot_by_name("A").shot_id)
    check(
        "collect_object_segments: one segment for shot A",
        len(segs) == 1,
        f"{len(segs)}",
    )
    if segs:
        s0 = segs[0]
        check(
            "collect_object_segments: segment is object A [0,10]",
            s0["obj"] == "A"
            and s0["start"] == 0
            and s0["end"] == 10
            and s0["duration"] == 10,
            f"{s0.get('obj')} [{s0.get('start')},{s0.get('end')}]",
        )

    # ---- _find_keyed_transforms: only non-flat, only in range ------------
    build_scene()
    store = fresh_store()
    found = ShotSequencer._find_keyed_transforms(0, 10)
    check("_find_keyed_transforms: A found in [0,10]", "A" in found, f"{found}")
    check(
        "_find_keyed_transforms: B/C excluded (no keys in range)",
        "B" not in found and "C" not in found,
        f"{found}",
    )

    # ---- accessors + hide state + reconcile no-op ------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    check("accessor: seq.shots mirrors store", list(seq.shots) == list(store.shots))
    check(
        "accessor: seq.sorted_shots mirrors store",
        [s.shot_id for s in seq.sorted_shots()]
        == [s.shot_id for s in store.sorted_shots()],
    )
    check("accessor: seq.shot_by_id resolves", seq.shot_by_id(a_id).name == "A")
    seq.set_object_hidden("A", True)
    check("hide: is_object_hidden True after set", seq.is_object_hidden("A") is True)
    check("hide: 'A' in hidden_objects", "A" in seq.hidden_objects)
    seq.set_object_hidden("A", False)
    check(
        "hide: is_object_hidden False after unset", seq.is_object_hidden("A") is False
    )
    check(
        "reconcile_all_shots: no-op returns False (flat Blender names)",
        seq.reconcile_all_shots() is False,
    )

    # ---- display-data layer (segment_collector + clip_motion helpers) ----
    # The widget PAINTING needs bpy+Qt together (GUI-pending), but the DATA the
    # controller feeds the SequencerWidget is pure and live-testable here.
    import logging as _logging
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector,
    )
    from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
    from blendertk.anim_utils.shots._shots import BlenderShotStore

    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_shot = store.shot_by_name("A")
    _log = _logging.getLogger("seqtest")

    sbs, all_objs = SegmentCollector.collect_segments(
        seq, a_shot, [a_shot], {}, {}, _log
    )
    check(
        "collect_segments: active shot has A's segment",
        a_shot.shot_id in sbs and any(s["obj"] == "A" for s in sbs[a_shot.shot_id]),
        f"{list(sbs)}",
    )
    check(
        "active_object_set: {A}",
        SegmentCollector.active_object_set(a_shot, sbs) == {"A"},
        f"{SegmentCollector.active_object_set(a_shot, sbs)}",
    )

    attrs = SegmentCollector.extract_attributes(sbs[a_shot.shot_id])
    check(
        "extract_attributes: A moves on translateX (location[0])",
        "translateX" in attrs,
        f"{attrs}",
    )

    # curves_for_attr resolves the label back to the right fcurve
    fcs = ClipMotionMixin.curves_for_attr("A", "translateX")
    check(
        "curves_for_attr: one location[0] fcurve for translateX",
        len(fcs) == 1 and fcs[0].data_path == "location" and fcs[0].array_index == 0,
        f"{[(f.data_path, f.array_index) for f in fcs]}",
    )

    # Regression: curves_for_attr must resolve a QUATERNION channel via attr_label
    # (the old hand-kept reverse map only knew rotation_euler → silently returned []).
    # Added alongside A/B/C — don't clear the scene (later checks still use "A").
    bpy.ops.mesh.primitive_cube_add()
    q = bpy.context.active_object
    q.name = "Q"
    q.rotation_mode = "QUATERNION"
    for f in (0, 10):
        q.rotation_quaternion = (1.0, 0.0, f * 0.05, 0.0)
        q.keyframe_insert(data_path="rotation_quaternion", frame=f)
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector,
    )

    qfcs = ClipMotionMixin.curves_for_attr(
        "Q", SegmentCollector.attr_label(next(BlenderShotStore.iter_action_fcurves(q)))
    )
    check(
        "curves_for_attr: resolves a rotation_quaternion channel (not just euler)",
        len(qfcs) >= 1 and all(f.data_path == "rotation_quaternion" for f in qfcs),
        f"{[(f.data_path, f.array_index) for f in qfcs]}",
    )

    # build_curve_preview reads bezier data straight off the fcurve
    a_obj = bpy.data.objects.get("A")
    loc_fc = next(
        (
            fc
            for fc in BlenderShotStore.iter_action_fcurves(a_obj)
            if fc.data_path == "location" and fc.array_index == 0
        ),
        None,
    )
    preview = SegmentCollector.build_curve_preview(loc_fc, 0, 10) if loc_fc else None
    check(
        "build_curve_preview: returns keys+segments over [0,10]",
        preview is not None
        and len(preview["keys"]) >= 2
        and len(preview["segments"]) >= 1,
        f"{None if preview is None else (len(preview['keys']), len(preview['segments']))}",
    )

    # ---- attr_label: quaternion channels are W-first + distinct from euler ----
    # (pre-fix: quats mapped through the X-first axis table -> every channel
    # mislabeled by one axis, and the shared "rotate" base collided with euler
    # so a sub-row edit through curves_for_attr moved BOTH rotation families)
    q_labels = sorted(
        {
            SegmentCollector.attr_label(fc)
            for fc in BlenderShotStore.iter_action_fcurves(q)
        }
    )
    check(
        "attr_label: quaternion channels labeled W-first",
        q_labels == ["quatRotateW", "quatRotateX", "quatRotateY", "quatRotateZ"],
        f"{q_labels}",
    )
    q.keyframe_insert(data_path="rotation_euler", frame=0)
    eul = ClipMotionMixin.curves_for_attr("Q", "rotateX")
    check(
        "curves_for_attr: euler label no longer drags quaternion curves",
        bool(eul) and all(f.data_path == "rotation_euler" for f in eul),
        f"{[(f.data_path, f.array_index) for f in eul]}",
    )

    # ---- on_keys_moved: chained batch moves are two-pass ------------------
    # (pre-fix: single in-place pass let [(10,12),(12,14)] stack both keys on 14)
    from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin

    class _FakeClip:
        def __init__(self, data):
            self.data = data

    class _FakeWidget:
        def __init__(self, clip):
            self._clip = clip

        def get_clip(self, cid):
            return self._clip

    class _KeysHost(ClipMotionMixin):
        """Minimal duck host for the mixin's per-key handlers (no Qt needed)."""

        def __init__(self, widget, sequencer=None):
            self._widget = widget
            self.sequencer = sequencer
            self._segment_cache = {}
            self._sub_row_cache = {}

        def _get_sequencer_widget(self):
            return self._widget

        def _save_shot_state(self):
            pass

        def _sync_to_widget(self, **kw):
            pass

        def _set_footer(self, *a, **k):
            pass

    bpy.ops.mesh.primitive_cube_add()
    cm_obj = bpy.context.active_object
    cm_obj.name = "ChainMv"
    for f, x in ((10, 1.0), (12, 2.0)):
        cm_obj.location = (x, 0.0, 0.0)
        cm_obj.keyframe_insert(data_path="location", index=0, frame=f)

    host = _KeysHost(
        _FakeWidget(
            _FakeClip({"obj": "ChainMv", "attr_name": "translateX", "shot_id": None})
        )
    )
    host.on_keys_moved(1, [(10.0, 12.0), (12.0, 14.0)])
    fc_x = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(cm_obj)
        if fc.data_path == "location" and fc.array_index == 0
    )
    moved = sorted(
        (round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc_x.keyframe_points
    )
    check(
        "on_keys_moved: chained moves land distinctly (no key stacking)",
        moved == [(12.0, 1.0), (14.0, 2.0)],
        f"{moved}",
    )

    # ---- _delete_clip_keys: whole-object clips scope to TRANSFORM curves ----
    # (pre-fix: every action fcurve in span was wiped — custom props included)
    from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
        ShotSequencerController,
    )

    bpy.ops.mesh.primitive_cube_add()
    del_obj = bpy.context.active_object
    del_obj.name = "DelScope"
    del_obj.location = (1.0, 0.0, 0.0)
    del_obj.keyframe_insert(data_path="location", frame=5)
    del_obj["myprop"] = 1.0
    del_obj.keyframe_insert(data_path='["myprop"]', frame=5)

    del_host = _KeysHost(
        _FakeWidget(
            _FakeClip({"obj": "DelScope", "orig_start": 0.0, "orig_end": 10.0})
        ),
        sequencer=object(),
    )
    ShotSequencerController._delete_clip_keys(del_host, [1])
    remaining = {
        fc.data_path
        for fc in BlenderShotStore.iter_action_fcurves(del_obj)
        if len(fc.keyframe_points)
    }
    check(
        "Delete Key: transform keys deleted, custom-prop key survives",
        "location" not in remaining and '["myprop"]' in remaining,
        f"{remaining}",
    )

    # ---- depsgraph filter: keyframe edits pass, everything else doesn't ----
    # (pre-fix: a bare selection click reached the debounce -> the epilogue
    # could silently merge the clicked object into the active shot)
    seen = {}

    def _cap(scene, depsgraph):
        seen["anim"] = ShotSequencerController._is_animation_update(depsgraph)

    bpy.app.handlers.depsgraph_update_post.append(_cap)
    try:
        seen.clear()
        del_obj.location.x += 1.0
        bpy.context.view_layer.update()
        transform_only = seen.get("anim")
        seen.clear()
        del_obj.keyframe_insert(data_path="location", frame=30)
        bpy.context.view_layer.update()
        key_edit = seen.get("anim")
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(_cap)
    check(
        "depsgraph filter: transform-only update is NOT an animation update",
        transform_only is False,
        f"{transform_only}",
    )
    check(
        "depsgraph filter: keyframe insert IS an animation update",
        key_edit is True,
        f"{key_edit}",
    )

    # ---- select paths guard objects outside the active view layer ----
    # (pre-fix: select_set raised RuntimeError mid-loop on an object in an
    # excluded collection, aborting shot selection / the context-menu action)
    from types import SimpleNamespace

    from blendertk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin

    bpy.ops.mesh.primitive_cube_add()
    vlg_vis = bpy.context.active_object
    vlg_vis.name = "VlgVisible"
    vlg_col = bpy.data.collections.new("VlgExcluded")
    bpy.context.scene.collection.children.link(vlg_col)
    vlg_out = bpy.data.objects.new("VlgOutside", None)
    vlg_col.objects.link(vlg_out)
    bpy.context.view_layer.layer_collection.children["VlgExcluded"].exclude = True
    bpy.context.view_layer.update()

    vlg_raises = False
    try:
        vlg_out.select_set(True)
    except RuntimeError:
        vlg_raises = True
    check(
        "fixture: select_set on an excluded-collection object raises RuntimeError",
        vlg_raises,
    )

    class _NavHost(ShotNavMixin):
        def __init__(self, sequencer):
            self.sequencer = sequencer
            self._syncing = False
            self._playback_range_mode = "off"

    vlg_shot = SimpleNamespace(
        objects=["VlgVisible", "VlgOutside"], start=1.0, end=10.0
    )
    vlg_seq = SimpleNamespace(
        shot_by_id=lambda sid: vlg_shot,
        store=SimpleNamespace(set_active_shot=lambda sid: None, select_on_load=True),
    )
    nav_err = ""
    try:
        _NavHost(vlg_seq).select_shot(1)
        nav_ok = True
    except RuntimeError as e:
        nav_ok, nav_err = False, repr(e)
    check(
        "select_shot skips an outside-view-layer object without raising",
        nav_ok,
        nav_err,
    )
    check("select_shot still selected the in-layer object", vlg_vis.select_get())
    check(
        "select_shot active object = the in-layer object",
        bpy.context.view_layer.objects.active is vlg_vis,
    )

    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    sel_err = ""
    try:
        ShotSequencerController._select_and_show(
            SimpleNamespace(), ["VlgVisible", "VlgOutside"]
        )
        sel_ok = True
    except RuntimeError as e:
        sel_ok, sel_err = False, repr(e)
    check(
        "_select_and_show skips an outside-view-layer object without raising",
        sel_ok,
        sel_err,
    )
    check("_select_and_show still selected the in-layer object", vlg_vis.select_get())

    # =====================================================================
    # mayatk-parity surface (2026-08-22): gap holds, motion/hold segments,
    # audio sequences, extend/fit, detect, expand/set_shot_start, to/from_dict
    # =====================================================================
    import wave

    import pythontk as ptk

    from blendertk.audio_utils._audio_utils import AudioUtils
    from blendertk.audio_utils.segments import AudioSegment

    def key_interp(obj_name):
        obj = bpy.data.objects.get(obj_name)
        return {
            round(float(kp.co[0]), 3): kp.interpolation
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            for kp in fc.keyframe_points
            if fc.array_index == 0
        }

    # ---- _enforce_gap_holds: last key before each gap goes CONSTANT --------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    seq.respace(gap=5, start_frame=0)  # A[0,10] B[15,25] C[30,40] -> epilogue runs
    ia, ib, ic = key_interp("A"), key_interp("B"), key_interp("C")
    check(
        "gap holds: A's last key (10) is CONSTANT, earlier keys untouched",
        ia.get(10.0) == "CONSTANT" and ia.get(5.0) == "BEZIER",
        f"{ia.get(10.0)} / {ia.get(5.0)}",
    )
    check(
        "gap holds: B's last key (25) is CONSTANT",
        ib.get(25.0) == "CONSTANT",
        f"{ib.get(25.0)}",
    )
    check(
        "gap holds: C (timeline-last, no gap after) untouched",
        ic.get(40.0) == "BEZIER",
        f"{ic.get(40.0)}",
    )

    # ---- collect_object_segments: motion runs split at a hold -------------
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.mesh.primitive_cube_add()
    h = bpy.context.active_object
    h.name = "Holder"
    for f, x in ((0, 0.0), (10, 1.0), (30, 1.0), (50, 1.0), (60, 2.0)):  # hold 10..50
        h.location = (x, 0.0, 0.0)
        h.keyframe_insert(data_path="location", index=0, frame=f)
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    hid = store.define_shot("H", 0, 60, objects=["Holder"]).shot_id
    seq = ShotSequencer(store)
    segs = seq.collect_object_segments(hid)
    spans = sorted((sg["start"], sg["end"]) for sg in segs)
    check(
        "segments: hold splits the object into two motion runs [0,10] + [50,60]",
        spans == [(0.0, 10.0), (50.0, 60.0)],
        f"{spans}",
    )
    check(
        "segments: each dict carries its fcurves (extract_attributes source)",
        all(sg.get("curves") for sg in segs),
    )
    segs_h = seq.collect_object_segments(hid, ignore_holds=False)
    spans_h = sorted((sg["start"], sg["end"]) for sg in segs_h)
    check(
        "segments: ignore_holds=False absorbs the hold key (30) into the first run",
        spans_h and spans_h[0] == (0.0, 30.0),
        f"{spans_h}",
    )
    # flat-only object still gets ONE backfill span (GUI invariant)
    bpy.ops.mesh.primitive_cube_add()
    fl = bpy.context.active_object
    fl.name = "Flat"
    for f in (0, 30):
        fl.location = (3.0, 0.0, 0.0)
        fl.keyframe_insert(data_path="location", index=0, frame=f)
    store.update_shot(hid, objects=["Holder", "Flat"])
    flat_segs = [sg for sg in seq.collect_object_segments(hid) if sg["obj"] == "Flat"]
    check(
        "segments: flat-keyed object backfilled as one span-of-keys segment",
        len(flat_segs) == 1 and (flat_segs[0]["start"], flat_segs[0]["end"]) == (0, 30),
        f"{[(sg['start'], sg['end']) for sg in flat_segs]}",
    )

    # ---- audio: VSE strip as a sequence; travels with its shot -----------
    with ptk.TempArtifacts(prefix="btk_seq_audio_") as tmp:
        wav_path = tmp.path(".wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 4000)  # 0.5 s of silence
        build_scene()
        store = fresh_store()
        seq = ShotSequencer(store)
        scene = bpy.context.scene
        strip_name = AudioUtils.add_clip(wav_path, frame_start=22, scene=scene)
        strip_start = AudioUtils.get_clip(strip_name)["frame_start"]
        check("audio fixture: strip placed at 22", strip_start == 22, f"{strip_start}")

        b_id = store.shot_by_name("B").shot_id
        seqs = seq.collect_shot_sequences(b_id)
        kinds = sorted(sq["kind"] for sq in seqs)
        check(
            "collect_shot_sequences: anim + audio for shot B",
            kinds == ["anim", "audio"],
            f"{kinds}",
        )
        audio_seq = next(sq for sq in seqs if sq["kind"] == "audio")
        check(
            "collect_shot_sequences: audio obj is the strip name",
            audio_seq["obj"] == strip_name and audio_seq["start"] == 22,
            f"{audio_seq}",
        )
        a_segs = AudioSegment.collect_all_segments(include_waveform=True)
        check(
            "AudioSegment: one segment, waveform envelope read from the WAV",
            len(a_segs) == 1 and len(a_segs[0].waveform) > 0,
            f"{len(a_segs)} / {len(a_segs[0].waveform) if a_segs else None}",
        )

        # move_shot(B, +10): B's keys AND its strip travel; C ripples
        seq.move_shot(b_id, 30)
        moved_start = AudioUtils.get_clip(strip_name)["frame_start"]
        check(
            "move_shot: audio strip travelled with shot B (+10)",
            moved_start == 32,
            f"{moved_start}",
        )
        check(
            "move_shot: B keys +10",
            key_times("B") == [round(30 + i, 3) for i in range(11)],
            f"{key_times('B')[:3]}..",
        )

        # move_sequences_to_shot: audio + anim from B into A (adjacent placement)
        a_id = store.shot_by_name("A").shot_id
        seqs = seq.collect_shot_sequences(b_id)
        seq.move_sequences_to_shot(seqs, a_id)
        a, b = store.shot_by_name("A"), store.shot_by_name("B")
        strip_after = AudioUtils.get_clip(strip_name)["frame_start"]
        check(
            "move_sequences_to_shot: B's anim now lives in A's range",
            all(a.start <= t <= a.end for t in key_times("B")),
            f"A=({a.start},{a.end}) B keys={key_times('B')[:2]}..",
        )
        check(
            "move_sequences_to_shot: audio strip now inside A's range",
            a.start <= strip_after <= a.end,
            f"A=({a.start},{a.end}) strip={strip_after}",
        )
        check(
            "move_sequences_to_shot: B's objects recomputed (B emptied)",
            "B" in a.objects and "B" not in b.objects,
            f"A.objects={a.objects} B.objects={b.objects}",
        )
        check(
            "move_sequences_to_shot: extend-to-fit grew A to enclose the move",
            a.end >= max(key_times("B")),
            f"A.end={a.end} max={max(key_times('B'))}",
        )
        AudioUtils.remove_all_clips(scene)

    # ---- extend_shot_to_fit: outer keys (not owned elsewhere) enclose -----
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    store.update_shot(a_id, end=6)  # keys 7..10 are now outside A but owned by no shot
    head, tail = seq.extend_shot_to_fit(a_id)
    a = store.shot_by_name("A")
    check(
        "extend_shot_to_fit: A grew back to its outer keys [0,10]",
        (a.start, a.end) == (0, 10) and tail == 4 and head == 0,
        f"{(a.start, a.end)} head={head} tail={tail}",
    )
    # keys owned by another shot are never attributed
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    store.update_shot(a_id, objects=["A", "B"])  # share B; B's keys belong to shot B
    head, tail = seq.extend_shot_to_fit(a_id)
    a = store.shot_by_name("A")
    check(
        "extend_shot_to_fit: keys owned by shot B don't drag A over it",
        (a.start, a.end) == (0, 10) and (head, tail) == (0, 0),
        f"{(a.start, a.end)} {(head, tail)}",
    )

    # ---- detect_shots / detect_next_shot -----------------------------------
    build_scene()
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    seq = ShotSequencer(store)
    cands = seq.detect_shots(gap_threshold=5.0)
    check(
        "detect_shots: three clusters A/B/C",
        [(c["start"], c["end"]) for c in cands] == [(0, 10), (20, 30), (40, 50)],
        f"{[(c['start'], c['end']) for c in cands]}",
    )
    store.define_shot("A", 0, 10, objects=["A"])
    nxt = seq.detect_next_shot(gap_threshold=5.0)
    check(
        "detect_next_shot: first cluster after existing shots is B's",
        nxt is not None
        and (nxt["start"], nxt["end"]) == (20, 30)
        and nxt["objects"] == ["B"],
        f"{nxt}",
    )
    store.define_shot("B", 20, 30, objects=["B"])
    store.define_shot("C", 40, 50, objects=["C"])
    check(
        "detect_next_shot: None when everything is covered",
        seq.detect_next_shot(gap_threshold=5.0) is None,
    )

    # ---- expand_shot / set_shot_start --------------------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    d = seq.expand_shot(a_id, 15)
    a, b = store.shot_by_name("A"), store.shot_by_name("B")
    check(
        "expand_shot: A end ->15 (+5), B rippled +5",
        d == 5 and a.end == 15 and (b.start, b.end) == (25, 35),
        f"d={d} A.end={a.end} B={(b.start, b.end)}",
    )
    check(
        "expand_shot: never contracts", seq.expand_shot(a_id, 12) == 0.0 and a.end == 15
    )
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.set_shot_start(a_id, 3, ripple=False)
    a, b = store.shot_by_name("A"), store.shot_by_name("B")
    check(
        "set_shot_start(ripple=False): A ->[3,13], keys +3, B untouched",
        (a.start, a.end) == (3, 13)
        and key_times("A") == [round(3 + i, 3) for i in range(11)]
        and (b.start, b.end) == (20, 30),
        f"A={(a.start, a.end)} B={(b.start, b.end)}",
    )

    # ---- to_dict / from_dict -------------------------------------------------
    data = seq.to_dict()
    seq2 = ShotSequencer.from_dict(data)
    check(
        "to_dict/from_dict: round-trips shots onto a BlenderShotStore",
        isinstance(seq2.store, BlenderShotStore)
        and [(s.name, s.start, s.end) for s in seq2.sorted_shots()]
        == [(s.name, s.start, s.end) for s in seq.sorted_shots()],
    )

    # ---- move_curve_keys / recreate_curve_keys (public key primitives) -----
    build_scene()
    a_obj = bpy.data.objects["A"]
    fc0 = next(
        fc for fc in BlenderShotStore.iter_action_fcurves(a_obj) if fc.array_index == 0
    )
    ShotSequencer.move_curve_keys(fc0, [0.0, 1.0], 100.0)
    t = sorted(round(kp.co[0], 3) for kp in fc0.keyframe_points)
    check(
        "move_curve_keys: only the named keys moved (+100)",
        t[-2:] == [100.0, 101.0] and t[0] == 2.0,
        f"{t[:2]}..{t[-2:]}",
    )
    ShotSequencer.recreate_curve_keys(fc0, [(100.0, 0.0), (101.0, 1.0)])
    t = sorted(round(kp.co[0], 3) for kp in fc0.keyframe_points)
    check(
        "recreate_curve_keys: keys back at 0..10",
        t == [float(i) for i in range(11)],
        f"{t}",
    )

    BlenderShotStore.clear_active()
    BlenderShotStore._prefs_dir_override = None
    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_sequencer_checks()
    except Exception as e:  # pragma: no cover
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
