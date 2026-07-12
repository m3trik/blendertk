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
        lines.append(f"{'OK' if ok else 'FAIL'}: {label}" + (f" — {detail}" if detail and not ok else ""))
        return ok

    import bpy

    from blendertk import BlenderShotStore
    from blendertk.anim_utils.shots._shots import iter_action_fcurves
    from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer

    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_seq_prefs_")
    BlenderShotStore.clear_active()

    def key_times(obj_name):
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return []
        return sorted({round(float(kp.co[0]), 3) for fc in iter_action_fcurves(obj) for kp in fc.keyframe_points})

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

        keyed("A", list(range(0, 11)))     # keys 0..10
        keyed("B", list(range(20, 31)))    # keys 20..30
        keyed("C", list(range(40, 51)))    # keys 40..50

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
    check("move_shot: A bounds shifted +5", (a.start, a.end) == (5, 15), f"{(a.start, a.end)}")
    check("move_shot: B rippled +5", (b.start, b.end) == (25, 35), f"{(b.start, b.end)}")
    check("move_shot: C rippled +5", (c.start, c.end) == (45, 55), f"{(c.start, c.end)}")
    check("move_shot: A keys shifted +5", key_times("A") == [round(5 + i, 3) for i in range(11)],
          f"{key_times('A')[:3]}..")
    check("move_shot: B keys shifted +5", key_times("B") == [round(25 + i, 3) for i in range(11)],
          f"{key_times('B')[:3]}..")
    check("move_shot: C keys shifted +5", key_times("C") == [round(45 + i, 3) for i in range(11)],
          f"{key_times('C')[:3]}..")

    # ---- ripple_downstream directly --------------------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    # shift everything starting at/after frame 40 by +10 (only C qualifies)
    seq.ripple_downstream(b_id, 40, 10)
    c = store.shot_by_name("C")
    check("ripple_downstream: C bounds +10", (c.start, c.end) == (50, 60), f"{(c.start, c.end)}")
    check("ripple_downstream: C keys +10", key_times("C") == [round(50 + i, 3) for i in range(11)],
          f"{key_times('C')[:3]}..")
    check("ripple_downstream: A untouched", key_times("A") == list(range(0, 11)))
    check("ripple_downstream: B untouched (pivot excluded, before frame)", key_times("B") == list(range(20, 31)))

    # ---- apply_gap(all): respace whole set -------------------------------
    build_scene()
    store = fresh_store()
    store.gap = 5
    seq = ShotSequencer(store)
    seq.apply_gap(5, scope="all")
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    # anchor at A.start=0, durations 10 each, gap 5 -> A[0,10] B[15,25] C[30,40]
    check("apply_gap all: A[0,10]", (a.start, a.end) == (0, 10), f"{(a.start, a.end)}")
    check("apply_gap all: B[15,25]", (b.start, b.end) == (15, 25), f"{(b.start, b.end)}")
    check("apply_gap all: C[30,40]", (c.start, c.end) == (30, 40), f"{(c.start, c.end)}")
    check("apply_gap all: B keys respaced to [15..25]", key_times("B") == [round(15 + i, 3) for i in range(11)],
          f"{key_times('B')[:3]}..")
    check("apply_gap all: C keys respaced to [30..40]", key_times("C") == [round(30 + i, 3) for i in range(11)],
          f"{key_times('C')[:3]}..")

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
    check("reorder: A keys teleported to [40..50]", key_times("A") == [round(40 + i, 3) for i in range(11)],
          f"{key_times('A')[:3]}..")
    check("reorder: B keys landed at [0..10]", key_times("B") == list(range(0, 11)),
          f"{key_times('B')[:3]}..")
    check("reorder: C keys landed at [20..30]", key_times("C") == list(range(20, 31)),
          f"{key_times('C')[:3]}..")
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

    keyed("A", list(range(0, 11)))      # keys 0..10
    keyed("B", list(range(20, 31)))     # keys 20..30 (content)
    keyed("C", list(range(45, 56)))     # keys 45..55

    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("A", 0, 10, objects=["A"])
    store.define_shot("B", 15, 40, objects=["B"])   # padded: content is 20..30
    store.define_shot("C", 45, 55, objects=["C"])
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    b_keys_before = key_times("B")
    head, tail = seq.trim_shot_to_content(b_id)
    b = store.shot_by_name("B")
    check("trim: B bounds pulled to content [20,30]", (b.start, b.end) == (20, 30), f"{(b.start, b.end)}")
    check("trim: head delta +5", head == 5, f"{head}")
    check("trim: tail delta -10", tail == -10, f"{tail}")
    check("trim: B own keys NOT moved", key_times("B") == b_keys_before, f"{key_times('B')[:3]}..")
    # C ripples by tail delta (-10) since its start (45) >= old_end (40)
    c = store.shot_by_name("C")
    check("trim: C rippled by tail delta -10", (c.start, c.end) == (35, 45), f"{(c.start, c.end)}")
    check("trim: C keys rippled -10", key_times("C") == [round(45 + i - 10, 3) for i in range(11)],
          f"{key_times('C')[:3]}..")
    # A ripples upstream by head delta (+5): its end (10) <= old_start (15), so the
    # leading gap trim created is closed and the original A→B gap (5) is preserved.
    a = store.shot_by_name("A")
    check("trim: A rippled upstream by head delta +5", (a.start, a.end) == (5, 15), f"{(a.start, a.end)}")
    check("trim: A keys rippled +5", key_times("A") == [round(0 + i + 5, 3) for i in range(11)],
          f"{key_times('A')[:3]}..")
    check("trim: original A->B and B->C gaps (5) preserved",
          (b.start - a.end == 5) and (c.start - b.end == 5),
          f"A->B={b.start - a.end} B->C={c.start - b.end}")

    # ---- move_object_keys: shift one object's keys, bounds untouched ------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    seq.move_object_keys("A", 0, 10, 3)  # delta +3
    a = store.shot_by_name("A")
    check("move_object_keys: A keys shifted to 3..13",
          key_times("A") == [round(3 + i, 3) for i in range(11)], f"{key_times('A')[:3]}..")
    check("move_object_keys: shot A bounds untouched", (a.start, a.end) == (0, 10), f"{(a.start, a.end)}")

    # ---- scale_object_keys: double a run in place ------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    seq.scale_object_keys("A", 0, 10, 0, 20)  # scale x2
    check("scale_object_keys: A keys doubled 0,2,..20",
          key_times("A") == [round(i * 2.0, 3) for i in range(11)], f"{key_times('A')[:4]}..")

    # ---- resize_shot: scale keys both edges + ripple downstream ----------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    seq.resize_shot(b_id, 20, 40)  # B[20,30] -> [20,40] (x2), tail +10
    b, c = store.shot_by_name("B"), store.shot_by_name("C")
    check("resize_shot: B bounds [20,40]", (b.start, b.end) == (20, 40), f"{(b.start, b.end)}")
    check("resize_shot: B keys scaled 20,22,..40",
          key_times("B") == [round(20 + i * 2.0, 3) for i in range(11)], f"{key_times('B')[:4]}..")
    check("resize_shot: C rippled +10 by tail delta", (c.start, c.end) == (50, 60), f"{(c.start, c.end)}")

    # ---- move_object_in_shot: grow shot + ripple when clip overruns ------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.move_object_in_shot(a_id, "A", 0, 10, 5)  # A keys ->5..15, end 10->15
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    check("move_object_in_shot: A keys ->5..15", key_times("A") == [round(5 + i, 3) for i in range(11)],
          f"{key_times('A')[:3]}..")
    check("move_object_in_shot: A shot grew end to 15", (a.start, a.end) == (0, 15), f"{(a.start, a.end)}")
    check("move_object_in_shot: B rippled +5", (b.start, b.end) == (25, 35), f"{(b.start, b.end)}")
    check("move_object_in_shot: C rippled +5", (c.start, c.end) == (45, 55), f"{(c.start, c.end)}")

    # ---- collect_object_segments: per-object keyed span ------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    segs = seq.collect_object_segments(store.shot_by_name("A").shot_id)
    check("collect_object_segments: one segment for shot A", len(segs) == 1, f"{len(segs)}")
    if segs:
        s0 = segs[0]
        check("collect_object_segments: segment is object A [0,10]",
              s0["obj"] == "A" and s0["start"] == 0 and s0["end"] == 10 and s0["duration"] == 10,
              f"{s0.get('obj')} [{s0.get('start')},{s0.get('end')}]")

    # ---- _find_keyed_transforms: only non-flat, only in range ------------
    build_scene()
    store = fresh_store()
    found = ShotSequencer._find_keyed_transforms(0, 10)
    check("_find_keyed_transforms: A found in [0,10]", "A" in found, f"{found}")
    check("_find_keyed_transforms: B/C excluded (no keys in range)",
          "B" not in found and "C" not in found, f"{found}")

    # ---- accessors + hide state + reconcile no-op ------------------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    check("accessor: seq.shots mirrors store", list(seq.shots) == list(store.shots))
    check("accessor: seq.sorted_shots mirrors store",
          [s.shot_id for s in seq.sorted_shots()] == [s.shot_id for s in store.sorted_shots()])
    check("accessor: seq.shot_by_id resolves", seq.shot_by_id(a_id).name == "A")
    seq.set_object_hidden("A", True)
    check("hide: is_object_hidden True after set", seq.is_object_hidden("A") is True)
    check("hide: 'A' in hidden_objects", "A" in seq.hidden_objects)
    seq.set_object_hidden("A", False)
    check("hide: is_object_hidden False after unset", seq.is_object_hidden("A") is False)
    check("reconcile_all_shots: no-op returns False (flat Blender names)",
          seq.reconcile_all_shots() is False)

    # ---- display-data layer (segment_collector + clip_motion helpers) ----
    # The widget PAINTING needs bpy+Qt together (GUI-pending), but the DATA the
    # controller feeds the SequencerWidget is pure and live-testable here.
    import logging as _logging
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        collect_segments, active_object_set, extract_attributes, build_curve_preview,
    )
    from blendertk.anim_utils.shots.shot_sequencer.clip_motion import curves_for_attr
    from blendertk.anim_utils.shots._shots import iter_action_fcurves

    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_shot = store.shot_by_name("A")
    _log = _logging.getLogger("seqtest")

    sbs, all_objs = collect_segments(seq, a_shot, [a_shot], {}, {}, _log)
    check("collect_segments: active shot has A's segment",
          a_shot.shot_id in sbs and any(s["obj"] == "A" for s in sbs[a_shot.shot_id]),
          f"{list(sbs)}")
    check("active_object_set: {A}", active_object_set(a_shot, sbs) == {"A"},
          f"{active_object_set(a_shot, sbs)}")

    attrs = extract_attributes(sbs[a_shot.shot_id])
    check("extract_attributes: A moves on translateX (location[0])",
          "translateX" in attrs, f"{attrs}")

    # curves_for_attr resolves the label back to the right fcurve
    fcs = curves_for_attr("A", "translateX")
    check("curves_for_attr: one location[0] fcurve for translateX",
          len(fcs) == 1 and fcs[0].data_path == "location" and fcs[0].array_index == 0,
          f"{[(f.data_path, f.array_index) for f in fcs]}")

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
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import attr_label
    qfcs = curves_for_attr("Q", attr_label(next(iter_action_fcurves(q))))
    check("curves_for_attr: resolves a rotation_quaternion channel (not just euler)",
          len(qfcs) >= 1 and all(f.data_path == "rotation_quaternion" for f in qfcs),
          f"{[(f.data_path, f.array_index) for f in qfcs]}")

    # build_curve_preview reads bezier data straight off the fcurve
    a_obj = bpy.data.objects.get("A")
    loc_fc = next((fc for fc in iter_action_fcurves(a_obj)
                   if fc.data_path == "location" and fc.array_index == 0), None)
    preview = build_curve_preview(loc_fc, 0, 10) if loc_fc else None
    check("build_curve_preview: returns keys+segments over [0,10]",
          preview is not None and len(preview["keys"]) >= 2 and len(preview["segments"]) >= 1,
          f"{None if preview is None else (len(preview['keys']), len(preview['segments']))}")

    # ---- attr_label: quaternion channels are W-first + distinct from euler ----
    # (pre-fix: quats mapped through the X-first axis table -> every channel
    # mislabeled by one axis, and the shared "rotate" base collided with euler
    # so a sub-row edit through curves_for_attr moved BOTH rotation families)
    q_labels = sorted({attr_label(fc) for fc in iter_action_fcurves(q)})
    check("attr_label: quaternion channels labeled W-first",
          q_labels == ["quatRotateW", "quatRotateX", "quatRotateY", "quatRotateZ"],
          f"{q_labels}")
    q.keyframe_insert(data_path="rotation_euler", frame=0)
    eul = curves_for_attr("Q", "rotateX")
    check("curves_for_attr: euler label no longer drags quaternion curves",
          bool(eul) and all(f.data_path == "rotation_euler" for f in eul),
          f"{[(f.data_path, f.array_index) for f in eul]}")

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

    host = _KeysHost(_FakeWidget(_FakeClip(
        {"obj": "ChainMv", "attr_name": "translateX", "shot_id": None}
    )))
    host.on_keys_moved(1, [(10.0, 12.0), (12.0, 14.0)])
    fc_x = next(fc for fc in iter_action_fcurves(cm_obj)
                if fc.data_path == "location" and fc.array_index == 0)
    moved = sorted((round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc_x.keyframe_points)
    check("on_keys_moved: chained moves land distinctly (no key stacking)",
          moved == [(12.0, 1.0), (14.0, 2.0)], f"{moved}")

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

    del_host = _KeysHost(_FakeWidget(_FakeClip(
        {"obj": "DelScope", "orig_start": 0.0, "orig_end": 10.0}
    )), sequencer=object())
    ShotSequencerController._delete_clip_keys(del_host, [1])
    remaining = {fc.data_path for fc in iter_action_fcurves(del_obj)
                 if len(fc.keyframe_points)}
    check("Delete Key: transform keys deleted, custom-prop key survives",
          "location" not in remaining and '["myprop"]' in remaining, f"{remaining}")

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
    check("depsgraph filter: transform-only update is NOT an animation update",
          transform_only is False, f"{transform_only}")
    check("depsgraph filter: keyframe insert IS an animation update",
          key_edit is True, f"{key_edit}")

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
