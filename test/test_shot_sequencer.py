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

It also checks the host half the shared Shot Sequencer panel reaches the scene
through (``SequencerHost``).  The panel's own checks -- its drag, delete, key
menu, undo and scene-event handlers over this engine -- are tentacle's
``test/blender/shot_sequencer_panel_check.py``.

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
    from blendertk.anim_utils._anim_utils import AnimUtils
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
                for fc in AnimUtils.get_fcurves(obj)
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
    # ripple from B's END: everything beyond that bound moves (only C qualifies;
    # the sample ON the bound is B's own and stays)
    seq.ripple_downstream(b_id, 30, 10)
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

    # ---- Shift To: re-base the whole sequence onto a start frame ---------
    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)
    first = seq.sorted_shots()[0]
    spans_before = [(s.start, s.end) for s in seq.sorted_shots()]
    seq.move_shot(first.shot_id, first.start - 100)
    spans_after = [(s.start, s.end) for s in seq.sorted_shots()]
    check(
        "shift all: every shot moved by the same delta",
        [(s - 100, e - 100) for s, e in spans_before] == spans_after,
        f"{spans_before} -> {spans_after}",
    )
    check(
        "shift all: the keys came with them",
        key_times("B") == [round(t - 100, 3) for t in range(20, 31)],
        f"{key_times('B')[:3]}..",
    )

    # ---- apply_gap(all) can override a locked gap ------------------------
    build_scene()
    store = fresh_store()
    store.gap = 5
    a0, b0 = (store.shot_by_name(n) for n in ("A", "B"))
    store.lock_gap(a0.shot_id, b0.shot_id)
    locked_width = b0.start - a0.end
    seq = ShotSequencer(store)
    seq.apply_gap(5, scope="all")
    a, b = (store.shot_by_name(n) for n in ("A", "B"))
    check(
        "apply_gap all: a locked gap keeps its width by default",
        abs((b.start - a.end) - locked_width) < 1e-6,
        f"{b.start - a.end} vs {locked_width}",
    )
    build_scene()
    store = fresh_store()
    store.gap = 5
    a0, b0 = (store.shot_by_name(n) for n in ("A", "B"))
    store.lock_gap(a0.shot_id, b0.shot_id)
    seq = ShotSequencer(store)
    seq.apply_gap(5, scope="all", respect_locks=False)
    a, b = (store.shot_by_name(n) for n in ("A", "B"))
    check(
        "apply_gap all: respect_locks=False spends the gap on it anyway",
        abs((b.start - a.end) - 5) < 1e-6,
        f"{b.start - a.end}",
    )
    check(
        "apply_gap all: the lock itself survives the override",
        store.is_gap_locked(a0.shot_id, b0.shot_id),
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

    # ---- swap_shots / copy_shot_content: the Shots window's Swap With -----
    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)
    a_id, c_id = (store.shot_by_name(n).shot_id for n in ("A", "C"))
    seq.swap_shots(a_id, c_id, retain=("name",))
    layout = [(s.shot_id, s.name, s.start, s.end) for s in store.sorted_shots()]
    check(
        "swap: C's shot first under A's name, A's last under C's",
        [(i, n) for i, n, _s, _e in layout]
        == [(c_id, "A"), (store.shot_by_name("B").shot_id, "B"), (a_id, "C")],
        f"{layout}",
    )
    check("swap: C keys landed at [0..10]", key_times("C") == list(range(0, 11)))
    check("swap: A keys landed at [40..50]", key_times("A") == list(range(40, 51)))

    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)
    a_id, c_id = (store.shot_by_name(n).shot_id for n in ("A", "C"))
    seq.copy_shot_content(c_id, a_id)

    def within(name, lo, hi):
        return [t for t in key_times(name) if lo <= t <= hi]

    # The cut pins the shots beside it (B's bounds hold their poses): judge
    # A's own frames and C's.
    check("copy: A's own keys cut", within("A", 0, 10) == [], f"{key_times('A')}")
    check(
        "copy: C's keys copied into A, C's own kept",
        within("C", 0, 10) == list(range(0, 11))
        and within("C", 40, 50) == list(range(40, 51)),
        f"{key_times('C')}",
    )
    check(
        "copy: A keeps its name and takes C as a member",
        (store.shot_by_id(a_id).name, store.shot_by_id(a_id).objects) == ("A", ["C"]),
        f"{store.shot_by_id(a_id).objects}",
    )

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

    # ---- resize_shot_bounds: boundary moves, keys stay -------------------
    # Audit contract: a plain edge drag moves the BOUNDARY only.  EVERY edge
    # move ripples the neighbours on that side, so a gap keeps its width —
    # growing pushes them away, shrinking pulls them in behind the bound —
    # while the pivot's own keys, stranded ones included, never move.
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    seq.resize_shot_bounds(b_id, 20, 40)  # tail grows +10
    b, c = store.shot_by_name("B"), store.shot_by_name("C")
    check(
        "resize_shot_bounds: grow moves bounds only (keys untouched)",
        (b.start, b.end) == (20, 40) and key_times("B") == list(range(20, 31)),
        f"{(b.start, b.end)} keys={key_times('B')[:4]}..",
    )
    check(
        "resize_shot_bounds: tail grow ripples C away (+10, keys ride)",
        (c.start, c.end) == (50, 60) and key_times("C") == list(range(50, 61)),
        f"{(c.start, c.end)} keys={key_times('C')[:3]}..",
    )

    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    seq.resize_shot_bounds(b_id, 20, 25)  # tail SHRINKS onto content: it stops
    b, c = store.shot_by_name("B"), store.shot_by_name("C")
    check(
        "resize_shot_bounds: a plain shrink stops at the shot's content",
        (b.start, b.end) == (20, 30) and (c.start, c.end) == (40, 50),
        f"B={(b.start, b.end)} C={(c.start, c.end)}",
    )
    seq.resize_shot_bounds(b_id, 20, 25, clamp=False)  # the escape hatch strands 26..30
    check(
        "resize_shot_bounds: shrink pulls C in behind the bound, gap kept",
        (b.start, b.end) == (20, 25)
        and (c.start, c.end) == (35, 45)
        and c.start - b.end == 10,
        f"B={(b.start, b.end)} C={(c.start, c.end)}",
    )
    check(
        "resize_shot_bounds: shrink leaves the pivot's OWN stranded keys alone",
        key_times("B") == list(range(20, 31)) and key_times("C") == list(range(35, 46)),
        f"B={key_times('B')[:3]}.. C={key_times('C')[:3]}..",
    )

    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    seq.resize_shot_bounds(b_id, 32, 22, clamp=False)  # inverted input
    b = store.shot_by_name("B")
    check(
        "resize_shot_bounds: inverted bounds are normalised (start<=end)",
        b.start <= b.end and (b.start, b.end) == (22, 32),
        f"{(b.start, b.end)}",
    )
    err = ""
    try:
        seq.resize_shot_bounds(999, 0, 10)
    except ValueError as e:
        err = str(e)
    check("resize_shot_bounds: unknown id raises ValueError", "999" in err, err)

    # ---- insert_shot: opens space; append clears the trailing tail -------
    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)
    b_id = store.shot_by_name("B").shot_id
    new = seq.insert_shot("Mid", duration=20, after_shot_id=b_id)
    c = store.shot_by_name("C")
    check(
        "insert_shot: new shot fills the opened hole after B",
        (new.start, new.end) == (40, 60),
        f"{(new.start, new.end)}",
    )
    check(
        "insert_shot: C rippled by duration+gap with its keys",
        (c.start, c.end) == (70, 80) and key_times("C") == list(range(70, 81)),
        f"{(c.start, c.end)} keys={key_times('C')[:3]}..",
    )

    build_scene()
    store = fresh_store()
    store.gap = 5
    seq = ShotSequencer(store)
    # Give C a trailing fade-tail key past its end (the +INF envelope
    # content appending must never build over).
    c_obj = bpy.data.objects["C"]
    c_obj.location = (9.9, 0.0, 0.0)
    c_obj.keyframe_insert(data_path="location", frame=75)
    new = seq.insert_shot("Tail", duration=10)
    check(
        "insert_shot: append starts past the trailing fade tail + gap",
        new.start >= 80,
        f"{new.start}",
    )

    # REGRESSION (2026-09-18): insert_shot rippled every downstream shot, keys
    # and all, BEFORE define_shot refused the name, and split_shot trimmed the
    # head first -- a refused name left the scene edited. Validated up front.
    build_scene()
    store = fresh_store()
    store.gap = 10
    seq = ShotSequencer(store)

    def bounds():
        return [(s.name, s.start, s.end) for s in seq.sorted_shots()]

    before, c_keys = bounds(), key_times("C")
    b_id = store.shot_by_name("B").shot_id
    err = ""
    try:
        seq.insert_shot("B", duration=20, after_shot_id=b_id)
    except ValueError as e:
        err = str(e)
    check(
        "insert_shot: a refused name raises before anything moves",
        bool(err) and bounds() == before and key_times("C") == c_keys,
        f"err={err!r} {bounds()} keys={key_times('C')[:3]}..",
    )
    err = ""
    try:
        seq.split_shot(store.shot_by_name("A").shot_id, 5, name="bad name")
    except ValueError as e:
        err = str(e)
    check(
        "split_shot: a refused tail name raises before the head is trimmed",
        bool(err) and bounds() == before,
        f"err={err!r} {bounds()}",
    )

    # ---- trim edge= : one-ended trims ------------------------------------
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    keyed("A", list(range(20, 31)))
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("A", 10, 40, objects=["A"])  # padded both sides
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    head, tail = seq.trim_shot_to_content(a_id, edge="leading")
    a = store.shot_by_name("A")
    check(
        "trim edge=leading: head pulled in, tail untouched",
        (a.start, a.end) == (20, 40) and head == 10 and tail == 0,
        f"{(a.start, a.end)} head={head} tail={tail}",
    )
    head, tail = seq.trim_shot_to_content(a_id, edge="trailing")
    a = store.shot_by_name("A")
    check(
        "trim edge=trailing: tail pulled in, head untouched",
        (a.start, a.end) == (20, 30) and head == 0 and tail == -10,
        f"{(a.start, a.end)} head={head} tail={tail}",
    )

    # A trailing HOLD is not empty space: ``collect_shot_sequences`` reports
    # MOTION, so a shot whose tail holds still read as empty at the end and
    # the trim moved the bound in FRONT of real keys -- which then stayed
    # behind while the downstream ripple pulled the next shot onto them.
    # (mayatk parity: "Step 4.1" trimmed to 1313 with keys out to 1533,
    # dragging "Step 4.8" from 1605 to 1328.)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    hold = keyed("H", [20, 60])  # motion 20..60
    for f in (80, 95):  # ...then a flat hold out to 95
        hold.location = (60 * 0.1, 0.0, 0.0)
        hold.keyframe_insert(data_path="location", frame=f)
    keyed("N", [160, 190])
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("H", 0, 120, objects=["H"])
    store.define_shot("N", 160, 200, objects=["N"])
    seq = ShotSequencer(store)
    h_id = store.shot_by_name("H").shot_id
    head, tail = seq.trim_shot_to_content(h_id, edge="trailing")
    h, n = store.shot_by_name("H"), store.shot_by_name("N")
    check(
        "trim: a trailing hold's keys hold the bound",
        (h.start, h.end) == (0, 95) and tail == -25,
        f"{(h.start, h.end)} head={head} tail={tail}",
    )
    check(
        "trim: the next shot never lands on the keys the trim left behind",
        min(key_times("N")) > max(key_times("H")) and n.start == 135,
        f"N@{min(key_times('N'))} H@{max(key_times('H'))} n.start={n.start}",
    )

    # A slide ripples ONE side; the other has to hold.  Without the clamp a
    # slide TOWARD the un-rippled side went straight over the neighbour and
    # the store held two shots claiming one span.  (mayatk parity: an outer
    # gap drag pulled "Step 4.8" 212 frames earlier onto "Step 4.4".)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    keyed("SA", [0, 100])
    keyed("SB", [100, 200])
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("SA", 0, 100, objects=["SA"])  # zero gap: no room at all
    store.define_shot("SB", 100, 200, objects=["SB"])
    seq = ShotSequencer(store)
    sa_id = store.shot_by_name("SA").shot_id
    sb_id = store.shot_by_name("SB").shot_id

    def _no_overlap(tag):
        ss = store.sorted_shots()
        bad = [
            (a.name, a.end, b.name, b.start)
            for a, b in zip(ss, ss[1:])
            if b.start < a.end - 1e-6
        ]
        check(f"slide: {tag} leaves no overlap", not bad, f"{bad}")

    seq.slide_shot(sb_id, 70, direction="downstream")
    _no_overlap("SB earlier, rippling downstream")
    seq.slide_shot(sa_id, 30, direction="upstream")
    _no_overlap("SA later, rippling upstream")
    seq.move_shot(sb_id, 70)
    _no_overlap("move_shot(SB) earlier")
    seq.set_shot_start(sb_id, 70, ripple=True)
    _no_overlap("set_shot_start(SB) earlier")
    check(
        "slide: a clamped slide leaves both shots exactly where they were",
        (store.shot_by_name("SA").start, store.shot_by_name("SA").end) == (0, 100)
        and (store.shot_by_name("SB").start, store.shot_by_name("SB").end)
        == (100, 200),
        f"{[(s.name, s.start, s.end) for s in store.sorted_shots()]}",
    )
    # Open room still absorbs a slide — the clamp only holds at a neighbour.
    store.update_shot(sb_id, start=140, end=240)
    seq.slide_shot(sb_id, 120, direction="downstream")
    check(
        "slide: a slide into real room still moves",
        store.shot_by_name("SB").start == 120,
        f"{store.shot_by_name('SB').start}",
    )

    # A lock is a statement about a gap's WIDTH: both edge handles refuse,
    # while a shot resize (which ripples, so the width survives) is free to
    # run.  The lock used to be read by the respace planner and the overlay
    # drawing only, so a locked gap dragged like any other.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    keyed(
        "LA", [0, 60]
    )  # content ends short of the tail: the shrink is over empty space
    keyed("LB", [115, 200])
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    la = store.define_shot("LA", 0, 100, objects=["LA"])
    lb = store.define_shot("LB", 115, 200, objects=["LB"])
    seq = ShotSequencer(store)
    store.lock_gap(la.shot_id, lb.shot_id)
    check(
        "lock: the gap reads as locked",
        store.is_gap_locked(la.shot_id, lb.shot_id),
    )
    seq.resize_shot_bounds(la.shot_id, 0, 80)
    a2, b2 = store.shot_by_name("LA"), store.shot_by_name("LB")
    check(
        "lock: a shot resize still ripples, so the locked width survives",
        (a2.start, a2.end) == (0, 80) and b2.start - a2.end == 15,
        f"LA={(a2.start, a2.end)} LB={(b2.start, b2.end)}",
    )

    # ---- extend one-sided rescue -----------------------------------------
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    keyed("A", [70, 90])  # ALL content past the shot's end
    BlenderShotStore.clear_active()
    store = BlenderShotStore()
    store.define_shot("A", 0, 50, objects=["A"])
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.extend_shot_to_fit(a_id)
    a = store.shot_by_name("A")
    check(
        "extend: rescues content entirely past one edge (other side kept)",
        (a.start, a.end) == (0, 90),
        f"{(a.start, a.end)}",
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

    # An overrun that reaches PAST the next shot's start must still travel
    # exactly the drag distance: committing the keys before the ripple let the
    # downstream envelope sweep them a second time (mirrors mayatk).
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_id = store.shot_by_name("A").shot_id
    seq.move_object_in_shot(a_id, "A", 0, 10, 15)  # new_end 25 > B.start 20
    a, b, c = (store.shot_by_name(n) for n in ("A", "B", "C"))
    check(
        "move_object_in_shot: an overrunning clip is not rippled twice",
        key_times("A") == [round(15 + i, 3) for i in range(11)],
        f"{key_times('A')[:3]}..{key_times('A')[-1:]}",
    )
    check(
        "move_object_in_shot: the overrun still ripples the downstream shots",
        (a.start, a.end) == (0, 25)
        and (b.start, b.end) == (35, 45)
        and (c.start, c.end) == (55, 65),
        f"{(a.start, a.end)} {(b.start, b.end)} {(c.start, c.end)}",
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

    # ---- display-data layer (segment_collector + the host's curve lookup) ----
    # The widget PAINTING needs bpy+Qt together (GUI-pending), but the DATA the
    # controller feeds the SequencerWidget is pure and live-testable here.
    import logging as _logging
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector,
    )
    from blendertk.anim_utils.shots.shot_sequencer.sequencer_host import SequencerHost
    from blendertk.anim_utils.shots._shots import BlenderShotStore
    from blendertk.anim_utils._anim_utils import AnimUtils

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
    fcs = SequencerHost.curves_for_attr("A", "translateX")
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

    qfcs = SequencerHost.curves_for_attr(
        "Q", SegmentCollector.attr_label(next(iter(AnimUtils.get_fcurves(q))))
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
            for fc in AnimUtils.get_fcurves(a_obj)
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
            for fc in AnimUtils.get_fcurves(q)
        }
    )
    check(
        "attr_label: quaternion channels labeled W-first",
        q_labels == ["quatRotateW", "quatRotateX", "quatRotateY", "quatRotateZ"],
        f"{q_labels}",
    )
    q.keyframe_insert(data_path="rotation_euler", frame=0)
    eul = SequencerHost.curves_for_attr("Q", "rotateX")
    check(
        "curves_for_attr: euler label no longer drags quaternion curves",
        bool(eul) and all(f.data_path == "rotation_euler" for f in eul),
        f"{[(f.data_path, f.array_index) for f in eul]}",
    )

    # ---- the panel's key / clip drags are tentacle's --------------------------
    # The Shot Sequencer panel's handlers -- chained and batched key moves, key
    # and clip drags onto keyed neighbours, contiguous / split / keyless seams,
    # the closing rule, Move to Shot's head landing, a Shift retime and a plain
    # edge shrink through the drag -- moved with the panel:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``
    # runs them over this engine.  The seam scene helpers stay: the engine's own
    # seam checks below build their scenes with them.
    from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
        _ShotSequencerInternal,
    )

    _seam_objects: list = []

    def _seam_scene(name, shots, keys):
        """A cube keyed on location.x, in every one of *shots*: (obj, store, seq).

        The previous seam scene's cube goes first: the sequencer moves (and
        checks for collisions on) the scene's whole keyed content, so a cube
        left keyed from the case before is part of the next one."""
        BlenderShotStore.clear_active()
        while _seam_objects:
            _old = bpy.data.objects.get(_seam_objects.pop())
            if _old is not None:
                bpy.data.objects.remove(_old, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        o.name = name
        _seam_objects.append(o.name)
        for f, v in keys:
            o.location = (float(v), 0.0, 0.0)
            o.keyframe_insert(data_path="location", index=0, frame=f)
        st = BlenderShotStore()
        for sname, s, e in shots:
            st.define_shot(sname, s, e, objects=[o.name])
        return o, st, ShotSequencer(st)

    def _loc_x(o):
        return next(
            fc
            for fc in AnimUtils.get_fcurves(o)
            if fc.data_path == "location" and fc.array_index == 0
        )

    # ---- Move to Shot keeps what stays in the source (mirror of mayatk's
    # TestMoveToShotKeepsWhatStaysInTheSource; BACKLOG 2026-10-11): the source
    # plays the same up to its last stayed key (from its first, for a head
    # run).  75 is no extreme: its handles come from both neighbours.  No
    # shot on the source's far side: a neighbour pin there freezes the whole
    # curve's handles (``_insert_shape_keys``), which would hide the case.
    _tail_keys = ((55, 0), (65, 2), (75, 4), (85, 6), (95, 1), (110, 5), (140, 0))
    _head_keys = ((10, 3), (55, 6), (65, 5), (75, 3), (85, 1), (95, 0))
    for _label, _keys, _run, _dest, _gap, _from in (
        ("a tail run", _tail_keys, (85.0, 95.0), "R", 0, 0),
        ("a tail run across a gap", _tail_keys, (85.0, 95.0), "R", 10, 0),
        ("a head run", _head_keys, (55.0, 65.0), "P", 0, 25),
    ):
        for _left in list(bpy.data.objects):
            bpy.data.objects.remove(_left, do_unlink=True)
        _name = "Stays" + _label.title().replace(" ", "")
        _shots = (
            [("Q", 50, 100), ("R", 100 + _gap, 150 + _gap)]
            if _dest == "R"
            else [("P", 0, 50), ("Q", 50, 100)]
        )
        _o, _st, _sq = _seam_scene(_name, _shots, _keys)
        _st.gap = float(_gap)
        _sq.reconcile_system_edits()
        _span = range(26) if _from == 0 else range(25, 51)
        _q0 = [_loc_x(_o).evaluate(50 + d) for d in _span]
        _sq.move_sequences_to_shot(
            [{"kind": "anim", "obj": _o.name, "start": _run[0], "end": _run[1]}],
            _st.shot_by_name(_dest).shot_id,
        )
        _qs = _st.shot_by_name("Q").start
        _fc = _loc_x(_o)
        _off = max(abs(v - _fc.evaluate(_qs + d)) for d, v in zip(_span, _q0))
        check(
            f"Move to Shot of {_label} leaves the source playing its stayed keys",
            _off < 1e-4,
            f"Q off {_off:.5f}",
        )

    # (Move to Shot copying a shared seam sample through the drag is the
    # panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # ---- a released hold other shots play stays (mirror of mayatk's) -----------
    for _left in list(bpy.data.objects):
        bpy.data.objects.remove(_left, do_unlink=True)
    _o, _st, _sq = _seam_scene(
        "LegacyHold",
        [("A", 0, 20), ("B", 30, 50), ("C", 60, 80), ("D", 90, 110)],
        ((10, 0), (20, 10), (100, 0)),
    )
    _fc = _loc_x(_o)
    _kp = next(k for k in _fc.keyframe_points if round(k.co[0]) == 20)
    _key = _ShotSequencerInternal._fc_key(_o.name, _fc)
    _sq.ledger.record_step(_key, 20.0, _kp.easing, _kp.interpolation, _kp.co[1])
    _kp.interpolation = "CONSTANT"  # the old hold: no stop pin on B's start
    _fc.update()
    _before = {
        s.name: [_fc.evaluate(f) for f in range(int(s.start), int(s.end) + 1)]
        for s in _st.sorted_shots()
    }
    _sq.respace(gap=0, start_frame=0)
    _layout = [(s.start, s.end) for s in _st.sorted_shots()]
    _fc = _loc_x(_o)
    _off = max(
        abs(x - _fc.evaluate(f))
        for s in _st.sorted_shots()
        for f, x in zip(range(int(s.start), int(s.end) + 1), _before[s.name])
    )
    _sq.respace(gap=0, start_frame=0)
    check(
        "a released hold other shots play stays, and a respace settles in one pass",
        _off < 1e-4 and [(s.start, s.end) for s in _st.sorted_shots()] == _layout,
        f"{_off:.5f} {_layout}",
    )

    # ---- a respace keeps a held tail HOLDING (mirror of mayatk's) ----------
    # BACKLOG 2026-10-05 (maintainer 2026-10-10): the respace pinned a tail
    # the hold had not reached yet where the curve PLAYED ON (A 1.96 off its
    # held playback); it now holds every gap before it pins.
    _held_keys = ((0, 0), (30, 3), (70, 7), (90, 9))
    _o, _st, _sq = _seam_scene("HeldRef", [("A", 0, 50), ("B", 60, 100)], _held_keys)
    _sq.reconcile_system_edits()
    _ref_a = [_loc_x(_o).evaluate(f) for f in range(0, 51)]
    _ref_b = [_loc_x(_o).evaluate(f) for f in range(60, 101)]
    _o, _st, _sq = _seam_scene("HeldResp", [("A", 0, 50), ("B", 60, 100)], _held_keys)
    _sq.respace(gap=20, start_frame=0)
    _da = max(abs(x - _loc_x(_o).evaluate(f)) for f, x in zip(range(0, 51), _ref_a))
    _db = max(
        abs(x - _loc_x(_o).evaluate(f + 10)) for f, x in zip(range(60, 101), _ref_b)
    )
    check(
        "a respace holds a tail as every other op does",
        _da < 1e-4 and _db < 1e-4 and _ref_a[30:] == [3.0] * 21,
        f"A {_da:.5f} B {_db:.5f}",
    )
    _o, _st, _sq = _seam_scene(
        "HeldIdent",
        [("A", 0, 50), ("B", 60, 100)],
        ((0, 0), (20, 5), (60, 9), (120, 30), (140, 40)),
    )
    _sq.reconcile_system_edits()
    _a0 = [_loc_x(_o).evaluate(f) for f in range(0, 51)]
    _b0 = [_loc_x(_o).evaluate(f) for f in range(60, 101)]
    _sq.respace(gap=200, start_frame=0)
    _da = max(abs(x - _loc_x(_o).evaluate(f)) for f, x in zip(range(0, 51), _a0))
    _db = max(
        abs(x - _loc_x(_o).evaluate(f + 190)) for f, x in zip(range(60, 101), _b0)
    )
    check(
        "a held tail plays identically across a respace",
        _da < 1e-4 and _db < 1e-4,
        f"A {_da:.5f} B {_db:.5f}",
    )

    # ---- ledger format 2: a hold freezes its key's handles and knows its key
    # by value + left handle (maintainer 2026-10-11; mirror of mayatk's
    # TestAHoldKeepsWhatItHolds / TestGapHoldKeepsAPastedStep).
    for _to, _was in ((70, "0.07"), (250, "0.69")):
        _o, _st, _sq = _seam_scene(
            f"HeldMove{_to}",
            [("A", 0, 50), ("B", 60, 100)],
            ((0, 0), (20, 5), (60, 9), (90, 2)),
        )
        _sq.reconcile_system_edits()  # 20 holds; B is keyed on its start
        _a0 = [_loc_x(_o).evaluate(f) for f in range(0, 51)]
        _sq.move_shot(_st.shot_by_name("B").shot_id, _to)
        _da = max(abs(x - _loc_x(_o).evaluate(f)) for f, x in zip(range(0, 51), _a0))
        check(
            f"a held tail keeps its last segment as the next shot moves to {_to}",
            _da < 1e-4,
            f"A {_da:.5f} (was {_was} with the handles derived)",
        )
    _o, _st, _sq = _seam_scene(
        "HeldPaste", [("A", 0, 50), ("B", 60, 100)], ((0, 0), (20, 5), (60, 9), (90, 2))
    )
    _sq.reconcile_system_edits()
    _fc = _loc_x(_o)
    _kp = next(p for p in _fc.keyframe_points if abs(p.co[0] - 20) < 1e-3)
    _easing = _kp.easing
    _kp.co[1] = 7.0  # a step pasted over the hold: another value, the same easing
    _kp.handle_left[1] += 2.0
    _kp.handle_right[1] += 2.0
    _fc.update()
    _o.location = (6.0, 0.0, 0.0)
    _o.keyframe_insert(data_path="location", index=0, frame=45)  # a new seam
    _sq.reconcile_system_edits()
    _kp = next(p for p in _loc_x(_o).keyframe_points if abs(p.co[0] - 20) < 1e-3)
    _key = _ShotSequencerInternal._fc_key(_o.name, _loc_x(_o))
    # (45 comes in CONSTANT after a CONSTANT key: Blender's insert takes the
    # previous key's interpolation, so it is the animator's hold, unclaimed.)
    check(
        "a step pasted over a held seam with the same easing is the animator's",
        _kp.interpolation == "CONSTANT"
        and _kp.easing == _easing
        and 20.0 not in _sq.ledger.step_times(_key),
        f"{_kp.interpolation} steps {_sq.ledger.step_times(_key)}",
    )

    # ---- the SOL gap-0 acceptance findings (mirror of mayatk's) -----------
    # 2026-10-11, both in pythontk.  (1) Deleting an empty shot: D's new
    # closed end only TOUCHED C's old closed start, C moved second and
    # carried D's closing sample on (150 landed at 50, not 100).
    _o, _st, _sq = _seam_scene(
        "DelEmpty",
        [("A", 0, 50), ("B", 50, 100), ("C", 100, 120), ("D", 120, 150)]
        + [("E", 150, 200)],
        ((0, 0), (110, 3), (150, -5), (180, 2)),
    )
    _b_id = _st.shot_by_name("B").shot_id
    _st.update_shot(_b_id, objects=[])
    _a0 = [_loc_x(_o).evaluate(f) for f in range(0, 51)]
    _sq.delete_shot(_b_id)
    _kps = [(round(k.co[0], 3), round(k.co[1], 3)) for k in _loc_x(_o).keyframe_points]
    _da = max(abs(x - _loc_x(_o).evaluate(f)) for f, x in zip(range(0, 51), _a0))
    # A closes mid-ramp, C opens elsewhere on it: C lands a frame after A
    # (maintainer 2026-10-11), each pose pinned on its bound.
    check(
        "deleting an empty shot moves each key once",
        [t for t, _v in _kps] == [0.0, 50.0, 51.0, 61.0, 101.0, 131.0]
        and _kps[3:] == [(61.0, 3.0), (101.0, -5.0), (131.0, 2.0)]
        and _da < 1e-4,
        f"{_kps} A {_da:.5f}",
    )
    # (2) A run whose drawn extent ends on the next shot's claimed start pin
    # (the system's sample, on the shared frame) read as content from AFTER
    # the destination and was appended behind it, where C then played it.
    _o, _st, _sq = _seam_scene(
        "HeadPin",
        [("A", 0, 50), ("B", 50, 100), ("C", 100, 150)],
        ((0, 0), (20, 5), (40, 2), (70, 6), (90, 1)),
    )
    _ids = {n: _st.shot_by_name(n).shot_id for n in "ABC"}
    _st.update_shot(_ids["C"], objects=[])
    _fc = _loc_x(_o)
    _key = _ShotSequencerInternal._fc_key(_o.name, _fc)
    _pinned = _sq._pin_bound_keys({_key: [(50.0, _ids["B"], "start")]})
    _c0 = [_fc.evaluate(f) for f in range(100, 151)]
    _run = next(
        s
        for s in _sq.collect_shot_sequences(_ids["A"], include_audio=False)
        if s["obj"] == _o.name
    )
    _run_end = _run["end"]
    _sq.move_sequences_to_shot([_run], _ids["B"])
    _c_start = int(_sq.shot_by_id(_ids["C"]).start)
    _fc = _loc_x(_o)
    _d = max(
        abs(x - _fc.evaluate(f)) for f, x in zip(range(_c_start, _c_start + 51), _c0)
    )
    _first = round(_fc.keyframe_points[0].co[0], 3)
    check(
        "a head block ending on the next shot's pin lands in front",
        _pinned == 1 and _run_end == 50.0 and _d < 1e-4 and _first == 50.0,
        f"pinned={_pinned} run_end={_run_end} C {_d:.5f} first={_first}",
    )
    # (3) Content leaving a shot keeps its neighbours' poses (gap 10): the
    # value its last key held over every shot after it, the ramp out of the
    # previous shot's last key, a step held into the next shot, an object
    # keyed only in the shot that goes.
    BlenderShotStore.clear_active()
    while _seam_objects:  # the last seam scene's cube is keyed content too
        _old = bpy.data.objects.get(_seam_objects.pop())
        if _old is not None:
            bpy.data.objects.remove(_old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    _o = bpy.context.active_object
    _o.name = "Vacate"
    _seam_objects.append(_o.name)
    _shapes = {
        ("location", 0): ((0, 0), (20, 5), (70, 3), (90, 8)),
        ("location", 1): ((0, 0), (20, 5), (70, 3), (90, 8), (130, 2)),
        ("location", 2): ((0, 0), (80, 4), (130, 9)),
        ("rotation_euler", 0): ((70, 6), (90, 1)),
    }
    for (_path, _i), _keys in _shapes.items():
        for _f, _v in _keys:
            getattr(_o, _path)[_i] = float(_v)
            _o.keyframe_insert(data_path=_path, index=_i, frame=_f)
    _fcs = {
        (fc.data_path, fc.array_index): fc
        for fc in AnimUtils.get_fcurves(_o)
    }
    for _kp in _fcs[("location", 2)].keyframe_points:
        if round(_kp.co[0]) == 80:
            _kp.interpolation = "CONSTANT"
    _st = BlenderShotStore()
    for _n, _s, _e in (("A", 0, 50), ("B", 60, 100), ("C", 110, 150)):
        _st.define_shot(_n, _s, _e, objects=[_o.name])
    _sq = ShotSequencer(_st)
    _sq.reconcile_system_edits()
    _a0 = {k: [fc.evaluate(f) for f in range(0, 51)] for k, fc in _fcs.items()}
    _c0 = {k: [fc.evaluate(f) for f in range(110, 151)] for k, fc in _fcs.items()}
    _sq.delete_shot(_st.shot_by_name("B").shot_id)
    _c = _st.shot_by_name("C")
    _offs = {}
    for (_path, _i), _ in _shapes.items():
        _fc = next(
            (
                fc
                for fc in AnimUtils.get_fcurves(_o)
                if fc.data_path == _path and fc.array_index == _i
            ),
            None,
        )
        if _fc is None:
            _offs[(_path, _i)] = "curve gone"
            continue
        _da = max(
            abs(x - _fc.evaluate(f)) for f, x in zip(range(0, 51), _a0[(_path, _i)])
        )
        _dc = max(
            abs(x - _fc.evaluate(f))
            for f, x in zip(range(int(_c.start), int(_c.start) + 41), _c0[(_path, _i)])
        )
        if _da > 1e-4 or _dc > 1e-4:
            _offs[(_path, _i)] = f"A {_da:.5f} C {_dc:.5f}"
    check(
        "deleting a shot keeps both neighbours playing",
        (_c.start, _c.end) == (60.0, 100.0) and not _offs,
        f"C=({_c.start}, {_c.end}) {_offs}",
    )
    # (4) A run sent out of B to A: B's start pin stays with B, and C keeps
    # the value B's run held over it.
    _o, _st, _sq = _seam_scene(
        "SendOut",
        [("A", 0, 50), ("B", 60, 100), ("C", 110, 150)],
        ((10, 0), (30, 5), (70, 2), (90, 7)),
    )
    _ids = {n: _st.shot_by_name(n).shot_id for n in "ABC"}
    _st.update_shot(_ids["C"], objects=[])
    _fc = _loc_x(_o)
    _key = _ShotSequencerInternal._fc_key(_o.name, _fc)
    _pinned = _sq._pin_bound_keys({_key: [(60.0, _ids["B"], "start")]})
    _sq.reconcile_system_edits()
    _c0 = [_fc.evaluate(f) for f in range(110, 151)]
    _sq.move_sequences_to_shot(
        [{"kind": "anim", "obj": _o.name, "start": 70.0, "end": 90.0}], _ids["A"]
    )
    _c_start = int(_sq.shot_by_id(_ids["C"]).start)
    _fc = _loc_x(_o)
    _d = max(
        abs(x - _fc.evaluate(f)) for f, x in zip(range(_c_start, _c_start + 41), _c0)
    )
    check(
        "a run sent out of a shot keeps the next one playing",
        _pinned == 1 and _d < 1e-4,
        f"pinned={_pinned} C {_d:.5f}",
    )
    # (5) A shot a plan moves away from a key it does not take along keeps
    # its boundary slopes (mayatk's production curve: "B06_1" SCREWDRIVER).
    _o, _st, _sq = _seam_scene(
        "Slopes",
        [("A", 0, 111), ("B", 941, 1081)],
        (
            (12, 0.0),
            (941, -46.6029),
            (991, -51.0343),
            (1012, -52.3835),
            (1031, -53.384),
            (1041, -53.772),
            (1081, 0.0),
        ),
    )
    _fc = _loc_x(_o)
    for _kp in _fc.keyframe_points:
        if round(_kp.co[0]) in (12, 1041, 1081):
            _kp.interpolation = "CONSTANT"
    _fc.update()
    _sq.reconcile_system_edits()
    _b0 = [_fc.evaluate(f) for f in range(941, 1082)]
    _sq.add_shot_space(_st.shot_by_name("A").shot_id, -4.0, edge="trailing")
    _b = _st.shot_by_name("B")
    _fc = _loc_x(_o)
    _d = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(937, 1078), _b0))
    check(
        "a moved shot keeps its boundary slopes",
        (_b.start, _b.end) == (937.0, 1077.0) and _d < 1e-4,
        f"B=({_b.start}, {_b.end}) {_d:.5f}",
    )
    # ...and so does one slid alone, the drag mover's path (measured
    # 2026-10-11 on this curve: 0.0009 off with no smoothing, 0.0001 with
    # CONT_ACCEL, where the plan path was exact).
    for _smoothing in ("NONE", "CONT_ACCEL"):
        _o, _st, _sq = _seam_scene(
            f"SlideSlopes{_smoothing}",
            [("A", 0, 111), ("B", 941, 1081)],
            (
                (12, 0.0),
                (941, -46.6029),
                (991, -51.0343),
                (1012, -52.3835),
                (1031, -53.384),
                (1041, -53.772),
                (1081, 0.0),
            ),
        )
        _fc = _loc_x(_o)
        _fc.auto_smoothing = _smoothing
        for _kp in _fc.keyframe_points:
            if round(_kp.co[0]) in (12, 1041, 1081):
                _kp.interpolation = "CONSTANT"
        _fc.update()
        _sq.reconcile_system_edits()
        _b0 = [_fc.evaluate(f) for f in range(941, 1082)]
        _sq.slide_shot(_st.shot_by_name("B").shot_id, 937, direction=None)
        _fc = _loc_x(_o)
        _d = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(937, 1078), _b0))
        check(
            f"a shot slid alone keeps its boundary slopes ({_smoothing})",
            _st.shot_by_name("B").start == 937.0 and _d < 1e-4,
            f"B={_st.shot_by_name('B').start} {_d:.5f}",
        )
    # A plan moving a shot that holds ONE key: the keys it leaves behind keep
    # their slopes.  The hold read only a run of two keys or more, so C's
    # opening key re-derived its handles from where B's lone key went, and C
    # played another curve (mayatk holds the halves a run leaves behind,
    # whatever it holds; 2026-10-12).
    _o, _st, _sq = _seam_scene(
        "LoneKey",
        [("A", 0, 10), ("B", 20, 30), ("C", 40, 50)],
        ((20, 8.0), (40, 5.0), (50, 1.0)),
    )
    _sq.reconcile_system_edits()
    _fc = _loc_x(_o)
    _c0 = [_fc.evaluate(f) for f in range(40, 51)]
    _sq.ripple_upstream(_st.shot_by_name("C").shot_id, 40, 3)  # A and B move
    _fc = _loc_x(_o)
    _dc = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(40, 51), _c0))
    check(
        "a shot holding one key moved leaves the shot after it playing on",
        (_st.shot_by_name("B").start, _st.shot_by_name("C").start) == (23.0, 40.0)
        and _dc < 1e-4,
        f"B={_st.shot_by_name('B').start} C={_st.shot_by_name('C').start} "
        f"off {_dc:.5f}",
    )
    # (6) A ramp keyed only in the first and last of four flush shots runs
    # keyless through the two between, which list neither it nor a key of
    # it: pulling R away pins the seam it opens all the same.
    _o, _st, _sq = _seam_scene(
        "Ramp",
        [("P", 0, 50), ("Q", 50, 100), ("R", 100, 150), ("S", 150, 200)],
        ((10, -70.0), (190, 0.0)),
    )
    for _n in ("Q", "R"):
        _st.update_shot(_st.shot_by_name(_n).shot_id, objects=[])
    _sq.reconcile_system_edits()
    _fc = _loc_x(_o)
    _q0 = [_fc.evaluate(f) for f in range(50, 101)]
    _r0 = [_fc.evaluate(f) for f in range(101, 151)]
    _sq.move_shot(_st.shot_by_name("R").shot_id, 107)
    _fc = _loc_x(_o)
    _dq = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(50, 101), _q0))
    _dr = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(108, 158), _r0))
    check(
        "a ramp through shots that do not key it is pinned where opened",
        _st.shot_by_name("R").start == 107.0 and _dq < 1e-4 and _dr < 1e-4,
        f"R={_st.shot_by_name('R').start} Q {_dq:.5f} R {_dr:.5f}",
    )

    # ---- the gap-hold pin pass offers only the gap an edit changed ---------
    # BACKLOG 2026-10-05 perf note (mirror of mayatk's): every gap was offered
    # its pin again on every edit -- 190 pin writes for one New Shot.
    _o, _st, _sq = _seam_scene(
        "PinScope",
        [("A", 0, 50), ("B", 60, 100), ("C", 110, 150)],
        ((0, 0), (30, 3), (70, 7), (90, 9), (130, 2), (140, 5)),
    )
    _fc = _loc_x(_o)
    for _kp in _fc.keyframe_points:  # the animator's own holds on both seams
        if round(_kp.co[0]) in (30, 90):
            _kp.interpolation = "CONSTANT"
    _fc.update()
    _sq.reconcile_system_edits()

    def _pt_keys(fc):
        return [
            (round(kp.co[0], 4), round(kp.co[1], 4), kp.interpolation)
            + tuple(round(v, 4) for v in (*kp.handle_left, *kp.handle_right))
            + (kp.handle_left_type, kp.handle_right_type)
            for kp in fc.keyframe_points
            if kp.co[0] < 105
        ]

    _untouched = _pt_keys(_fc)
    _asked = []
    _real_insert = _sq._insert_shape_keys
    _sq._insert_shape_keys = lambda fc, times, **kw: (
        _asked.extend(float(t) for t in times) or _real_insert(fc, times, **kw)
    )
    _sq.insert_shot("D", 20, gap=10)
    del _sq._insert_shape_keys
    check(
        "a new shot writes no pin on an untouched gap; untouched shots' keys unchanged",
        not ({60.0, 110.0} & set(_asked)) and _pt_keys(_fc) == _untouched,
        f"pins written at {_asked}",
    )
    # Review 2026-10-11 (mirror of mayatk's): the pass keeps no memory, so a
    # seam an undo unsettled -- the curve back without its pin, the ledger
    # back without the claim -- is pinned again by the next op.
    _o, _st, _sq = _seam_scene(
        "PinUnsettled",
        [("A", 0, 50), ("B", 60, 100), ("C", 110, 150)],
        ((0, 0), (30, 3), (70, 7), (90, 9), (130, 2), (140, 5)),
    )
    _fc = _loc_x(_o)
    _start = [
        (kp.co[0], kp.co[1], kp.interpolation, tuple(kp.handle_left),
         tuple(kp.handle_right), kp.handle_left_type, kp.handle_right_type)
        for kp in _fc.keyframe_points
    ]  # fmt: skip
    _st.push_boundary_snapshot()
    _sq.reconcile_system_edits()
    _pinned = any(abs(kp.co[0] - 60.0) < 1e-3 for kp in _fc.keyframe_points)
    _settled = [_fc.evaluate(f) for f in range(60, 101)]
    while len(_fc.keyframe_points):  # the undo, by hand: the curve as it was
        _fc.keyframe_points.remove(_fc.keyframe_points[0])
    for _t, _v, _i, _hl, _hr, _lt, _rt in _start:
        _kp = _fc.keyframe_points.insert(_t, _v)
        _kp.interpolation = _i
        _kp.handle_left_type, _kp.handle_right_type = _lt, _rt
        _kp.handle_left, _kp.handle_right = _hl, _hr
    _fc.update()
    _st.restore_boundary_snapshot()
    _sq.reconcile_system_edits()
    _again = any(abs(kp.co[0] - 60.0) < 1e-3 for kp in _fc.keyframe_points)
    _d = max(abs(x - _fc.evaluate(f)) for f, x in zip(range(60, 101), _settled))
    check(
        "a seam an undo unsettled is pinned again by the next op",
        _pinned and _again and _d < 1e-4,
        f"pinned={_pinned} again={_again} B {_d:.5f}",
    )

    # ---- a hold claimed in Maya releases to a Blender interpolation ----------
    # ShotTransfer carries the ledger verbatim, so a scene pulled through the
    # bridge holds claims naming Maya tangent types; written straight onto
    # kp.interpolation, "auto" raised TypeError on the first edit that moved a
    # seam (2026-10-11, SOL_REPLACE_ASSEMBLY pulled into Blender 5.1).
    _o, _st, _sq = _seam_scene(
        "ForeignHold",
        [("A", 0, 50), ("B", 60, 100)],
        ((0, 0), (30, 3), (70, 7), (90, 9)),
    )
    _fc = _loc_x(_o)
    _key = _ShotSequencerInternal._fc_key(_o.name, _fc)
    _sq.reconcile_system_edits()
    _held = _sq.ledger.step_times(_key) == [30.0]
    _claim = _sq.ledger.claimed_step(_key, 30.0)
    _sq.ledger.release_step(_key, 30.0)
    _sq.ledger.record_step(_key, 30.0, "auto", "auto", _claim[2] if _claim else None)
    _o.location = (4.0, 0.0, 0.0)
    _o.keyframe_insert(data_path="location", index=0, frame=45)  # the seam moves on
    try:
        _sq.reconcile_system_edits()
        _err = None
    except Exception as _exc:  # noqa: BLE001 -- the defect raised TypeError
        _err = repr(_exc)
    _kp = next(k for k in _fc.keyframe_points if abs(k.co[0] - 30.0) < 1e-3)
    check(
        "a hold claimed in Maya releases to a Blender interpolation",
        _held and _err is None and _kp.interpolation == "BEZIER",
        f"held={_held} err={_err} interp={_kp.interpolation}",
    )

    # ---- Copy to Shot (mayatk 2026-10-07): Move's placement, originals kept --
    o, st, sq = _seam_scene(
        "CopyTail",
        [("S0", 100, 200), ("S1", 210, 260)],
        ((110, 0), (150, 5), (220, 7), (250, 1)),
    )
    _loc_x(o).keyframe_points[2].interpolation = "CONSTANT"
    sq.copy_sequences_to_shot(
        [{"kind": "anim", "obj": o.name, "start": 220.0, "end": 250.0}],
        st.shot_by_name("S0").shot_id,
    )
    pts = [
        (round(kp.co[0], 3), round(kp.co[1], 3), kp.interpolation)
        for kp in _loc_x(o).keyframe_points
    ]
    s0, s1 = st.shot_by_name("S0"), st.shot_by_name("S1")
    landed = [pt for pt in pts if 150 < pt[0] <= s0.end]
    kept = [(pt[0] - s1.start, pt[1]) for pt in pts if s1.start <= pt[0] <= s1.end]
    check(
        "Copy to Shot: the copy lands after the destination's content, its "
        "interpolation carried",
        [pt[1] for pt in landed][:2] == [7.0, 1.0]
        and landed[0][2] == "CONSTANT"
        and round(landed[1][0] - landed[0][0], 3) == 30.0,
        f"{pts}",
    )
    check(
        "Copy to Shot: the source keeps its keys and its member",
        (10.0, 7.0) in kept and (40.0, 1.0) in kept and o.name in s1.objects,
        f"{kept} {s1.objects}",
    )

    # ---- Move to Shot: a head block owns the contiguous seam it lands on -----
    # Mirror of mayatk's test_a_head_block_owns_the_contiguous_seam_it_lands_on
    # (BACKLOG 2026-09-19, decided 2026-09-23): the leading room's split left
    # the source's closing-pose copy on the seam, where the block's first key
    # landed, and the copy was pushed to 290.1 -- a stray sixth key.  The
    # block owns S1's start, a frame after S0's closing 7 (the closing rule at
    # the head, maintainer 2026-10-11).
    block = [110.7, 150.0, 199.8]
    o, st, sq = _seam_scene(
        "SeamHead",
        [("S0", 100, 200), ("S1", 200, 240)],
        ((110.7, 0), (150, 5), (199.8, 2), (200, 7), (230, 1)),
    )
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": o.name,
                "attr": "location",
                "times": block,
                "start": block[0],
                "end": block[-1],
            }
        ],
        st.shot_by_name("S1").shot_id,
    )
    got = [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in _loc_x(o).keyframe_points]
    check(
        "Move to Shot: a head block owns the contiguous seam (no stray key)",
        got
        == [(200.0, 7.0), (201.0, 0.0), (240.3, 5.0), (290.1, 2.0)]
        + [(291.0, 7.0), (321.0, 1.0)],
        f"{got}",
    )
    # The same move as a whole-object sequence (mayatk's fixture), which lands
    # through move_object_keys rather than a key selection.
    o, st, sq = _seam_scene(
        "SeamHeadObj",
        [("S0", 100, 200), ("S1", 200, 240)],
        ((110.7, 0), (150, 5), (199.8, 2), (200, 7), (230, 1)),
    )
    sq.move_sequences_to_shot(
        [{"kind": "anim", "obj": o.name, "start": 110.7, "end": 199.8}],
        st.shot_by_name("S1").shot_id,
    )
    got = [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in _loc_x(o).keyframe_points]
    check(
        "Move to Shot: a whole-object head block owns the contiguous seam too",
        got
        == [(200.0, 7.0), (201.0, 0.0), (240.3, 5.0), (290.1, 2.0)]
        + [(291.0, 7.0), (321.0, 1.0)]
        and [(s.start, s.end) for s in st.sorted_shots()] == [(100, 200), (201, 331)],
        f"{got} {[(s.start, s.end) for s in st.sorted_shots()]}",
    )
    # ...and on a curve of just the block's key and the seam pose: "never below
    # two keys" left that pose to be pushed a frame into S1 (201 (7)), though
    # the block's own key keeps the curve alive.  (location.y gives S1 content
    # of this object, so the leading room is made.)
    o, st, sq = _seam_scene(
        "SeamHeadTwo", [("S0", 100, 200), ("S1", 200, 240)], ((150, 5), (200, 7))
    )
    for f, v in ((200, 1.0), (230, 2.0)):
        o.location[1] = v
        o.keyframe_insert(data_path="location", index=1, frame=f)
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": o.name,
                "attr": "location",
                "times": [150.0],
                "start": 150.0,
                "end": 150.0,
            }
        ],
        st.shot_by_name("S1").shot_id,
    )
    got = [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in _loc_x(o).keyframe_points]
    check(
        "Move to Shot: a head block owns the seam on a two-key curve too",
        got == [(200.0, 7.0), (201.0, 5.0)],
        f"{got}",
    )

    # (An audio clip moved by its visible part, a refused drag reported instead
    # of raised, and the Delete Key paths are the panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)
    # ---- the host's span delete: whole-object scopes are TRANSFORM curves ----
    # (pre-fix: every action fcurve in span was wiped — custom props included)
    bpy.ops.mesh.primitive_cube_add()
    del_obj = bpy.context.active_object
    del_obj.name = "DelScope"
    del_obj.location = (1.0, 0.0, 0.0)
    del_obj.keyframe_insert(data_path="location", frame=5)
    del_obj["myprop"] = 1.0
    del_obj.keyframe_insert(data_path='["myprop"]', frame=5)
    del_count = SequencerHost.delete_span_keys("DelScope", [], 0.0, 10.0)
    remaining = {
        fc.data_path for fc in AnimUtils.get_fcurves(del_obj) if len(fc.keyframe_points)
    }
    check(
        "delete_span_keys: transform keys deleted, custom-prop key survives",
        del_count >= 1 and "location" not in remaining and '["myprop"]' in remaining,
        f"deleted={del_count} {remaining}",
    )

    # ---- depsgraph filter: keyframe edits pass, everything else doesn't ----
    # (pre-fix: a bare selection click reached the debounce -> the epilogue
    # could silently merge the clicked object into the active shot)
    seen = {}

    def _cap(scene, depsgraph):
        seen["anim"] = SequencerHost._is_animation_update(depsgraph)

    # The span delete above left its Action tagged; flushed here, or the
    # transform-only update below reports the delete's own (real) key edit.
    bpy.context.view_layer.update()
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
    # excluded collection, aborting shot selection / the context-menu action).
    # The panel's selection mirrors reach the scene through these two.
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

    nav_err, nav_picked = "", []
    try:
        nav_picked = SequencerHost.select_objects(["VlgVisible", "VlgOutside"])
        nav_ok = True
    except RuntimeError as e:
        nav_ok, nav_err = False, repr(e)
    check(
        "select_objects skips an outside-view-layer object without raising",
        nav_ok,
        nav_err,
    )
    check(
        "select_objects still selected the in-layer object",
        vlg_vis.select_get(),
        f"{nav_picked}",
    )
    check(
        "select_objects active object = the in-layer object",
        bpy.context.view_layer.objects.active is vlg_vis,
    )

    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    sel_err = ""
    try:
        SequencerHost.mirror_selection(["VlgVisible", "VlgOutside"])
        sel_ok = True
    except RuntimeError as e:
        sel_ok, sel_err = False, repr(e)
    check(
        "mirror_selection skips an outside-view-layer object without raising",
        sel_ok,
        sel_err,
    )
    check("mirror_selection still selected the in-layer object", vlg_vis.select_get())

    # ---- the channel pick: NAMES in, and the pick replaces ----------------------
    # (pre-fix: the clear read ``animation_data`` off the name strings, found no
    # fcurves, and left every earlier channel selected beside the new pick).
    # The panel's header / sub-row / clip picks that reach it are tentacle's.
    bpy.ops.mesh.primitive_cube_add()
    chan_obj = bpy.context.active_object
    chan_obj.name = "ChanSel"
    chan_obj.keyframe_insert(data_path="location", frame=1)
    chan_obj.keyframe_insert(data_path="rotation_euler", frame=1)

    def _picked_channels():
        return sorted(
            (fc.data_path, fc.array_index)
            for fc in AnimUtils.get_fcurves(chan_obj)
            if fc.select
        )

    def _pick_every_channel():
        for fc in AnimUtils.get_fcurves(chan_obj):
            fc.select = True

    for label, attrs, expected in (
        ("no channel clears the object's channel selection", (), []),
        ("a channel selects only itself", ["translateX"], [("location", 0)]),
        ("a rotation channel, only itself", ["rotateZ"], [("rotation_euler", 2)]),
    ):
        _pick_every_channel()
        try:
            SequencerHost.select_channels(["ChanSel"], attrs)
            picked = _picked_channels()
        except Exception as e:  # report, don't abort the suite
            picked = repr(e)
        check(f"select_channels: {label}", picked == expected, f"{picked}")
    bpy.data.objects.remove(chan_obj, do_unlink=True)

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
            for fc in AnimUtils.get_fcurves(obj)
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
    # a flat-keyed member draws no track; a drift below motion_rate still does
    bpy.ops.mesh.primitive_cube_add()
    fl = bpy.context.active_object
    fl.name = "Flat"
    for f in (0, 30):
        fl.location = (3.0, 0.0, 0.0)
        fl.keyframe_insert(data_path="location", index=0, frame=f)
    bpy.ops.mesh.primitive_cube_add()
    dr = bpy.context.active_object
    dr.name = "Drift"
    for f, x in ((0, 0.0), (30, 0.01)):  # 3.3e-4 / frame, under motion_rate
        dr.location = (x, 0.0, 0.0)
        dr.keyframe_insert(data_path="location", index=0, frame=f)
    store.update_shot(hid, objects=["Holder", "Flat", "Drift"])
    by_obj = {}
    for sg in seq.collect_object_segments(hid):
        by_obj.setdefault(sg["obj"], []).append((sg["start"], sg["end"]))
    check(
        "segments: a flat-keyed member's few keys are markers, not a track",
        by_obj.get("Flat") == [(0.0, 0.0), (30.0, 30.0)]
        and "Flat" not in {q["obj"] for q in seq.collect_shot_sequences(hid)},
        f"{by_obj.get('Flat')}",
    )
    check(
        "segments: a drift below motion_rate is backfilled as one span",
        by_obj.get("Drift") == [(0, 30)],
        f"{by_obj.get('Drift')}",
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

        # move_sequences_to_shot: audio + anim from B into A, appended after
        # A's own run with room the eye can see.
        a_id = store.shot_by_name("A").shot_id
        # Object "B" has no run of its own in A, so there is nothing to clear
        # and it anchors at A's start -- the other half of the placement rule.
        b_runs = [
            s
            for s in seq.collect_shot_sequences(a_id, include_audio=False)
            if s["obj"] == "B"
        ]
        b_run_end = max((s["end"] for s in b_runs), default=None)
        a_start_before = store.shot_by_name("A").start
        sep = seq.sequence_separation()
        seqs = seq.collect_shot_sequences(b_id)
        seq.move_sequences_to_shot(seqs, a_id)
        a, b = store.shot_by_name("A"), store.shot_by_name("B")
        strip_after = AudioUtils.get_clip(strip_name)["frame_start"]

        def moved_times(name):
            """*name*'s keys less the system's bound samples: C's start keeps
            the pose B's run held over it, claimed (the one rule)."""
            out: set = set()
            for fc in AnimUtils.get_fcurves(bpy.data.objects[name]):
                claimed = seq.ledger.key_times(_ShotSequencerInternal._fc_key(name, fc))
                out |= {
                    round(float(kp.co[0]), 3)
                    for kp in fc.keyframe_points
                    if not any(abs(kp.co[0] - c) <= 1e-3 for c in claimed)
                }
            return sorted(out)

        check(
            "move_sequences_to_shot: B's anim now lives in A's range",
            all(a.start <= t <= a.end for t in moved_times("B")),
            f"A=({a.start},{a.end}) B keys={moved_times('B')[:2]}..",
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
            a.end >= max(moved_times("B")),
            f"A.end={a.end} max={max(moved_times('B'))}",
        )
        check(
            "move_sequences_to_shot: separation exceeds the inter-shot gap",
            sep > store.gap and sep >= 1.0,
            f"sep={sep} gap={store.gap}",
        )
        if b_run_end is None:
            check(
                "move_sequences_to_shot: nothing of its own to clear, so it "
                "anchors at the shot start",
                min(key_times("B")) == a_start_before,
                f"landed={min(key_times('B'))} A.start={a_start_before}",
            )
        else:
            check(
                "move_sequences_to_shot: the arrival clears B's own run",
                min(key_times("B")) >= b_run_end + sep,
                f"landed={min(key_times('B'))} b_run_end={b_run_end} sep={sep}",
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
    # A middle shot's probe reads its own envelope only: a key of ITS object
    # in the gap before it belongs to the previous shot, one in a far gap
    # to whoever precedes that -- only its own trailing gap may grow it.
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    b_obj = bpy.data.objects["B"]
    for f in (15, 33, 55):
        b_obj.location = (f * 0.1, 0.0, 0.0)
        b_obj.keyframe_insert(data_path="location", frame=f)
    b_id = store.shot_by_name("B").shot_id
    head, tail = seq.extend_shot_to_fit(b_id)
    b, c = store.shot_by_name("B"), store.shot_by_name("C")
    check(
        "extend_shot_to_fit: a middle shot claims only its trailing gap",
        (b.start, b.end) == (20, 33) and (head, tail) == (0, 3),
        f"{(b.start, b.end)} {(head, tail)}",
    )
    check(
        "extend_shot_to_fit: the far-gap key never rippled C",
        (c.start, c.end) == (43, 53),
        f"{(c.start, c.end)}",
    )

    # ---- move_attribute_keys: a key selection, one channel ---------------
    build_scene()
    store = fresh_store()
    seq = ShotSequencer(store)
    a_obj = bpy.data.objects["A"]
    a_obj.rotation_euler = (0.0, 0.0, 1.0)
    a_obj.keyframe_insert(data_path="rotation_euler", frame=3)
    a_id, b_id = (store.shot_by_name(n).shot_id for n in ("A", "B"))
    seq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "A",
                "attr": "translateX",
                "times": [2.0, 4.0],
                "start": 2.0,
                "end": 4.0,
            }
        ],
        b_id,
    )

    def channel_times(obj_name, data_path, index):
        obj = bpy.data.objects[obj_name]
        for fc in AnimUtils.get_fcurves(obj):
            if fc.data_path == data_path and fc.array_index == index:
                return sorted(round(float(kp.co[0]), 3) for kp in fc.keyframe_points)
        return []

    tx = channel_times("A", "location", 0)
    ty = channel_times("A", "location", 1)
    # 40 is C's opening pose, pinned: the landed 22 became the last key before
    # C, which played key 10's extrapolation (the head landing's next-shot pin).
    check(
        "move_attribute_keys: only the named keys of the named channel moved",
        2.0 not in tx
        and 4.0 not in tx
        and 3.0 in tx
        and len(tx) == 12
        and 40.0 in tx
        and abs(
            next(
                fc
                for fc in AnimUtils.get_fcurves(bpy.data.objects["A"])
                if fc.data_path == "location" and fc.array_index == 0
            ).evaluate(45)
            - 1.0
        )
        < 1e-4,
        f"tx={tx}",
    )
    check(
        "move_attribute_keys: sibling channels untouched",
        ty == [float(i) for i in range(11)]
        and channel_times("A", "rotation_euler", 2) == [3.0],
        f"ty={ty}",
    )
    b = store.shot_by_name("B")
    check(
        "move_attribute_keys: the keys landed inside the destination",
        all(
            b.start <= t <= b.end
            for t in tx
            if t not in [float(i) for i in range(11)] + [40.0]
        ),
        f"B=({b.start},{b.end}) tx={tx}",
    )
    check(
        "move_sequences_to_shot: membership re-decided for the moved object only",
        "A" in store.shot_by_name("A").objects and "A" in b.objects,
        f"A.objects={store.shot_by_name('A').objects} B.objects={b.objects}",
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
        fc for fc in AnimUtils.get_fcurves(a_obj) if fc.array_index == 0
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

    # ---- landing on occupied frames (mirror of mayatk) --------------------
    # A cluster dropped where keys already sit used to land INTERLEAVED with
    # them -- the arriving motion and the old poses sharing one span, playing
    # as neither.  The landing zone is cleared first: flat holds are absorbed,
    # poses are pushed aside.

    def _lone_curve(obj_name, keys):
        """One object with a single translateX fcurve carrying *keys*."""
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            obj = bpy.data.objects.new(obj_name, None)
            bpy.context.scene.collection.objects.link(obj)
        obj.animation_data_clear()
        for t, v in keys:
            obj.location[0] = v
            obj.keyframe_insert("location", index=0, frame=t)
        return next(
            fc
            for fc in AnimUtils.get_fcurves(obj)
            if fc.data_path == "location" and fc.array_index == 0
        )

    def _times(fc):
        return sorted(round(kp.co[0], 3) for kp in fc.keyframe_points)

    build_scene()
    fc = _lone_curve(
        "collide_hold", [(100, 5.0), (120, 5.0), (140, 5.0), (150, 9.0), (160, 1.0)]
    )
    ShotSequencer.move_curve_keys(fc, [140.0, 150.0, 160.0], -20.0)
    t = _times(fc)
    check(
        "landing zone: a flat hold in the way is absorbed",
        t == [100.0, 120.0, 130.0, 140.0],
        f"{t}",
    )

    build_scene()
    fc = _lone_curve(
        "collide_pose",
        [(100, 5.0), (120, 20.0), (125, 30.0), (140, 5.0), (150, 9.0), (160, 1.0)],
    )
    ShotSequencer.move_curve_keys(fc, [140.0, 150.0, 160.0], -22.0)
    t = _times(fc)
    vals = sorted(round(kp.co[1], 3) for kp in fc.keyframe_points)
    check(
        "landing zone: a pose in the way is pushed, never deleted",
        len(t) == 6 and vals == sorted([5.0, 20.0, 30.0, 5.0, 9.0, 1.0]),
        f"{t} {vals}",
    )
    check(
        "landing zone: the moved cluster lands where it was asked to",
        all(x in t for x in (118.0, 128.0, 138.0)),
        f"{t}",
    )
    pushed = sorted(x for x in t if 100.0 < x < 118.0)
    check(
        "landing zone: a pushed pose keeps its internal timing",
        len(pushed) == 2 and abs((pushed[1] - pushed[0]) - 5.0) < 1e-3,
        f"{pushed}",
    )

    build_scene()
    fc = _lone_curve(
        "collide_straddle",
        [
            (1245, 0.0),
            (1258, 7.0),
            (1272, 0.0),
            (1297, 0.0),
            (1310, 7.0),
            (1324, 0.0),
        ],
    )
    ShotSequencer.move_curve_keys(fc, [1297.0, 1310.0, 1324.0], -40.0)
    t = _times(fc)
    gaps = [round(b - a, 3) for a, b in zip(t[:3], t[1:3])]
    check(
        "landing zone: a cluster straddling the edge is displaced whole",
        len(t) == 6 and gaps == [13.0, 14.0] and t[3:] == [1257.0, 1270.0, 1284.0],
        f"{t} gaps={gaps}",
    )

    build_scene()
    fc = _lone_curve("collide_sparse", [(10, 1.0), (30, 2.0), (50, 3.0), (60, 9.0)])
    check(
        "landing zone: a key left BETWEEN the moved ones makes it sparse",
        not ShotSequencer._is_contiguous_run(fc, [10.0, 50.0]),
    )
    ShotSequencer.move_curve_keys(fc, [10.0, 50.0], 30.0)
    t = _times(fc)
    v = [round(kp.co[1], 3) for kp in sorted(fc.keyframe_points, key=lambda k: k.co[0])]
    check(
        "landing zone: a sparse selection pushes nothing",
        t == [30.0, 40.0, 60.0, 80.0] and v == [2.0, 1.0, 9.0, 3.0],
        f"{t} {v}",
    )

    build_scene()
    fc = _lone_curve("collide_clean", [(100, 0.0), (110, 5.0)])
    ShotSequencer.move_curve_keys(fc, [100.0, 110.0], 100.0)
    t = _times(fc)
    check(
        "landing zone: a clear destination still moves untouched",
        t == [200.0, 210.0],
        f"{t}",
    )

    # An OBJECT move clears its landing zone too (mirror of mayatk, whose
    # move_object_keys is move_attribute_keys): a raw shift laid 20 (5) onto
    # 40 (3) and Blender kept one of the two.
    build_scene()
    fc = _lone_curve("collide_object", [(10, 0.0), (20, 5.0), (40, 3.0), (60, 9.0)])
    ShotSequencer(BlenderShotStore()).move_object_keys("collide_object", 10, 20, 30)
    got = [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc.keyframe_points]
    check(
        "landing zone: an object move pushes the pose it lands on, never drops it",
        got == [(30.0, 0.0), (40.0, 5.0), (41.0, 3.0), (60.0, 9.0)],
        f"{got}",
    )

    # A displaced key merges into a same-valued key it would be pushed onto
    # only when it is ALONE: the rest of a block are still pushed by the same
    # delta, past the key the merged one joined, and 50 (5), 55 (8) ahead of
    # 56 (5) came out 56 (5), 61 (8) -- the return to 5 after the 8 gone.
    build_scene()
    fc = _lone_curve(
        "collide_merge_order",
        [(0, 0.0), (40, 1.0), (45, 2.0), (50, 5.0), (55, 8.0), (56, 5.0), (70, 0.0)],
    )
    ShotSequencer.move_curve_keys(fc, [40.0, 45.0], 10.0)
    got = [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc.keyframe_points]
    check(
        "landing zone: a same-value merge never reorders a displaced block",
        got
        == [
            (0.0, 0.0),
            (50.0, 1.0),
            (55.0, 2.0),
            (56.0, 5.0),
            (61.0, 8.0),
            (62.0, 5.0),
            (70.0, 0.0),
        ],
        f"{got}",
    )

    # ---- edit ledger + shot lifecycle -------------------------------------
    # The two writes the shot system makes on the animator's curves have to be
    # releasable, and a shot has to be deletable / mergeable / splittable.

    from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
        _ShotSequencerInternal as _SSI,
    )

    def fresh(name_keys):
        """A store + sequencer over freshly keyed cubes.  ``{obj: {frame: x}}``."""
        bpy.ops.wm.read_factory_settings(use_empty=True)
        BlenderShotStore.clear_active()
        st = BlenderShotStore()
        BlenderShotStore.set_active(st)
        made = {}
        for obj_name, keys in name_keys.items():
            bpy.ops.mesh.primitive_cube_add()
            ob = bpy.context.active_object
            ob.name = obj_name
            for f, v in sorted(keys.items()):
                ob.location.x = v
                ob.keyframe_insert(data_path="location", index=0, frame=f)
            made[obj_name] = ob
        return st, ShotSequencer(store=st), made

    def fc_of(ob):
        return next(
            fc
            for fc in AnimUtils.get_fcurves(ob)
            if fc.data_path == "location" and fc.array_index == 0
        )

    def interp_at(ob, t):
        fc = fc_of(ob)
        for kp in fc.keyframe_points:
            if abs(kp.co[0] - t) < 1e-3:
                return kp.interpolation
        return None

    def times_of(ob):
        return sorted(round(kp.co[0], 3) for kp in fc_of(ob).keyframe_points)

    # -- a gap hold is claimed once, and released when the gap closes -------
    st, sq, obs = fresh({"ledA": {1: 0, 10: 4, 20: 9}, "ledB": {40: 9, 50: 3}})
    sq.define_shot("A", 1, 20, objects=["ledA"])
    b_shot = sq.define_shot("B", 40, 50, objects=["ledB"])
    sq._enforce_gap_holds()
    check("gap hold: seam at 20 is CONSTANT", interp_at(obs["ledA"], 20) == "CONSTANT")
    check("gap hold: claimed once", sq.ledger.step_count == 1, f"{sq.ledger}")
    sq._enforce_gap_holds()
    check("gap hold: idempotent", sq.ledger.step_count == 1, f"{sq.ledger}")

    # An animator's own hold on a non-seam key is never claimed, never undone.
    fc_of(obs["ledA"]).keyframe_points[1].interpolation = "CONSTANT"
    sq._enforce_gap_holds()
    key = _SSI._fc_key("ledA", fc_of(obs["ledA"]))
    check(
        "gap hold: the animator's own hold is not claimed",
        not sq.ledger.owns_step(key, 10.0),
    )
    st.update_shot(b_shot.shot_id, start=20.0)  # gap closes
    sq._enforce_gap_holds()
    check(
        "gap hold: released when the gap closes",
        interp_at(obs["ledA"], 20) != "CONSTANT" and sq.ledger.step_count == 0,
        f"{interp_at(obs['ledA'], 20)} / {sq.ledger}",
    )
    check(
        "gap hold: the animator's hold survives the release",
        interp_at(obs["ledA"], 10) == "CONSTANT",
    )

    # -- a key a sparse move OVERWRITES takes every claim with it (BTK-SHOTS-3)
    # ``_overwrite_landed`` released only the sample claim: the replaced key's
    # step and a behavior's authored claim stayed on the frame, and ``remap``
    # handed them to the animator's key that landed there -- whose next Build
    # ``release_authored`` then deleted.
    st, sq, obs = fresh({"owA": {0: 0, 10: 5, 20: 9}})
    ow_fc = fc_of(obs["owA"])
    ow_key = _SSI._fc_key("owA", ow_fc)
    sq.ledger.record_authored(ow_key, 10.0, 0, "fade_in", "owA", "stamp")
    sq.ledger.record_step(ow_key, 10.0, "BEZIER", "BEZIER")
    ShotSequencer.move_curve_keys(
        ow_fc, [0.0, 20.0], 10.0, ledger=sq.ledger, ledger_key=ow_key
    )
    check(
        "sparse move: the overwritten key's authored and step claims go with it",
        times_of(obs["owA"]) == [10.0, 30.0]
        and not sq.ledger.owns_authored(ow_key, 10.0)
        and not sq.ledger.owns_step(ow_key, 10.0),
        f"{times_of(obs['owA'])} {sq.ledger.to_dict()}",
    )

    # -- the system's own bound samples are never a member's marks ---------
    # Mirror of mayatk's TestEdgeCaseSegmentDetection (2026-09-07): a flat
    # member whose only keys in the shot are its two bound samples draws
    # nothing, claimed or released; the animator's own hold key inside does.
    def marks_of(keys, claim):
        st, sq, obs = fresh({"pinned": keys})
        sh = sq.define_shot("P", 10, 50, objects=["pinned"])
        if claim:
            key = _SSI._fc_key("pinned", fc_of(obs["pinned"]))
            sq.ledger.record_key(key, 10.0, sh.shot_id, "start")
            sq.ledger.record_key(key, 50.0, sh.shot_id, "end")
        return [
            (sg["start"], sg.get("marker", False))
            for sg in sq.collect_object_segments(sh.shot_id)
            if sg["obj"] == "pinned"
        ]

    flat = {0: 2.0, 10: 2.0, 50: 2.0, 100: 2.0}
    check("marks: two claimed bound samples draw nothing", marks_of(flat, True) == [])
    check(
        "marks: a released sample on a bound that holds nothing draws nothing",
        marks_of(flat, False) == [],
        f"{marks_of(flat, False)}",
    )
    check(
        "marks: the animator's own hold key inside is still drawn",
        marks_of({0: 2.0, 10: 2.0, 30: 2.0, 50: 2.0, 100: 2.0}, True) == [(30.0, True)],
        f"{marks_of({0: 2.0, 10: 2.0, 30: 2.0, 50: 2.0, 100: 2.0}, True)}",
    )

    # One curve spanning three shots holds at EACH of its two seams -- shot
    # objects are routinely shared, so collapsing the seam set to one entry per
    # curve leaves every gap but the last one interpolating across the cut.
    st, sq, obs = fresh({"shA": {1: 0, 20: 5, 40: 8, 60: 2, 80: 9}})
    sq.define_shot("A", 1, 20, objects=["shA"])
    sq.define_shot("B", 40, 60, objects=["shA"])
    sq.define_shot("C", 80, 90, objects=["shA"])
    sq._enforce_gap_holds()
    check(
        "gap hold: every gap on a shared curve holds",
        interp_at(obs["shA"], 20) == "CONSTANT"
        and interp_at(obs["shA"], 60) == "CONSTANT"
        and sq.ledger.step_count == 2,
        f"{interp_at(obs['shA'], 20)} / {interp_at(obs['shA'], 60)} / {sq.ledger}",
    )

    # A curve whose FIRST key sits in the gap is the next shot's lead-in, not
    # this shot's overhang: shot membership is per object, so it reaches the
    # seam scan on the strength of a SIBLING channel's content.  Holding it
    # freezes the next shot's own motion.  (mayatk parity: production case
    # ``FAILED_CMPT_LOC``, member of "Step 2.1" through its opacity fade while
    # its translate curves start in the gap and run on into "Step 3.1".)
    st, sq, obs = fresh({"leadA": {48: 0, 63: 1, 66: 1}})
    lead = obs["leadA"]
    for f, v in ((66, -16.8), (101, -3.75)):
        lead.location.z = v
        lead.keyframe_insert(data_path="location", index=2, frame=f)
    fc_z = next(
        fc
        for fc in AnimUtils.get_fcurves(lead)
        if fc.data_path == "location" and fc.array_index == 2
    )
    sq.define_shot("A", 33, 65, objects=["leadA"])
    sq.define_shot("B", 80, 256, objects=["leadA"])
    sq._enforce_gap_holds()
    check(
        "gap hold: the next shot's lead-in key is not held",
        fc_z.keyframe_points[0].interpolation != "CONSTANT",
        f"{fc_z.keyframe_points[0].interpolation}",
    )
    check(
        "gap hold: the seam on the pre-gap-fed channel still holds",
        interp_at(lead, 66) == "CONSTANT",
        f"{interp_at(lead, 66)}",
    )

    # -- membership is a motion label; the envelope still carries a hold ---
    st, sq, obs = fresh({"holdX": {10: 5, 40: 5}})
    found = ShotSequencer._find_keyed_transforms(0, 50)
    check(
        "membership: a hold-only object is not discovered (keys are not motion)",
        "holdX" not in found
        and "holdX"
        in ShotSequencer._find_keyed_transforms(0, 50, require_motion=False),
        f"{found}",
    )
    s0 = sq.define_shot("S0", 0, 50)  # objects=None -> discover
    sq.move_shot(s0.shot_id, 100)
    check(
        "membership: not a member, still inside the envelope, it travels unadopted",
        s0.objects == [] and times_of(obs["holdX"]) == [110.0, 140.0],
        f"objects={s0.objects} {times_of(obs['holdX'])}",
    )

    # -- a render-effect channel is content: a pulse is listed and travels --
    from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

    st, sq, obs = fresh({"pulseBoard": {}})
    board = obs["pulseBoard"]
    RenderEffects.key_pulse([board], start=10, end=100)
    hl = RenderEffects._fcurve(board, '["highlight"]')
    found = ShotSequencer._find_keyed_transforms(0, 120)
    check(
        "render effect: a highlight pulse keyed in the range is discovered",
        hl is not None and "pulseBoard" in found,
        f"{found}",
    )
    s0 = sq.define_shot("S0", 0, 120)  # objects=None -> discover
    before = sorted(round(kp.co[0], 3) for kp in hl.keyframe_points)
    sq.move_shot(s0.shot_id, 200)
    after = sorted(round(kp.co[0], 3) for kp in hl.keyframe_points)
    check(
        "render effect: the pulse is a member and travels with its shot",
        s0.objects == ["pulseBoard"] and after == [t + 200 for t in before],
        f"objects={s0.objects} before={before[:3]}.. after={after[:3]}..",
    )
    bpy.ops.mesh.primitive_cube_add()
    marker = bpy.context.active_object
    marker.name = "audioMarker"
    marker["audio_trigger"] = 0
    marker.keyframe_insert(data_path='["audio_trigger"]', frame=10)
    marker["audio_trigger"] = 1
    marker.keyframe_insert(data_path='["audio_trigger"]', frame=20)
    found = ShotSequencer._find_keyed_transforms(0, 120)
    check(
        "render effect: a marker property is still not content",
        "audioMarker" not in found,
        f"{found}",
    )

    # -- a bounds grow moves the neighbour WHOLE (a plain drag never claims) --
    st, sq, obs = fresh(
        {"leadOut": {10: 0, 20: 1, 40: 1, 50: 0}, "bOwn": {55: 0, 60: 1, 80: 1, 90: 5}}
    )
    a = sq.define_shot("A", 0, 40, objects=["leadOut"])
    b = sq.define_shot("B", 55, 100, objects=["bOwn"])
    sq.resize_shot_bounds(a.shot_id, 0, 75)  # +35, past B's start
    check(
        "bounds grow: the shot's own keys stay; the lead-out in the gap rides the ripple",
        times_of(obs["leadOut"]) == [10.0, 20.0, 40.0, 85.0],
        f"{times_of(obs['leadOut'])}",
    )
    check(
        "bounds grow: the neighbour moves whole, its head keys too",
        times_of(obs["bOwn"]) == [90.0, 95.0, 115.0, 125.0]
        and (b.start, b.end) == (90, 135),
        f"{times_of(obs['bOwn'])} B={(b.start, b.end)}",
    )
    check(
        "bounds grow: the ramp is intact and the shot's last key holds the gap",
        interp_at(obs["leadOut"], 20) != "CONSTANT"
        and interp_at(obs["leadOut"], 85) == "CONSTANT",
        f"{interp_at(obs['leadOut'], 20)} / {interp_at(obs['leadOut'], 85)}",
    )
    st, sq, obs = fresh({"aTail": {10: 0, 30: 5, 40: 5, 48: 2, 52: 0}})
    a = sq.define_shot("A", 0, 40, objects=["aTail"])
    b = sq.define_shot("B", 55, 100, objects=[])
    sq.resize_shot_bounds(b.shot_id, 45, 100)  # head -10, over A's lead-out
    check(
        "bounds grow: a head grow carries the previous shot's lead-out with it",
        times_of(obs["aTail"]) == [0.0, 20.0, 30.0, 38.0, 42.0]
        and (a.start, a.end) == (-10, 30),
        f"{times_of(obs['aTail'])} A={(a.start, a.end)}",
    )
    st, sq, obs = fresh({"aTail": {10: 0, 30: 5, 40: 5}})
    a = sq.define_shot("A", 0, 40, objects=["aTail"])
    b = sq.define_shot("B", 55, 100, objects=[])
    sq.resize_shot_bounds(b.shot_id, 30, 100)  # head -25, back over A's tail keys
    check(
        "bounds grow: a head grow past the previous shot's end moves it whole",
        times_of(obs["aTail"]) == [-15.0, 5.0, 15.0] and (a.start, a.end) == (-25, 15),
        f"{times_of(obs['aTail'])} A={(a.start, a.end)}",
    )
    # Every-frame content across a shared seam: the only new key is the
    # neighbour's opening pose re-keyed at its new start (the fencepost split).
    st, sq, obs = fresh({"baked": {t: float(t) for t in range(0, 121)}})
    a = sq.define_shot("A", 0, 50, objects=["baked"])
    b = sq.define_shot("B", 50, 100, objects=["baked"])
    sq.resize_shot_bounds(a.shot_id, 0, 62)  # +12 over the shared seam
    t = times_of(obs["baked"])
    kv = sorted(
        (round(kp.co[0], 3), round(kp.co[1], 3))
        for kp in fc_of(obs["baked"]).keyframe_points
    )
    check(
        "bounds grow: contiguous shots keep every key, plus the neighbour's opening pose",
        t
        == [float(x) for x in range(0, 51)]
        + [62.0]
        + [float(x) for x in range(63, 133)]
        and kv[51] == (62.0, 50.0)
        and (a.start, a.end, b.start, b.end) == (0, 62, 62, 112),
        f"{len(t)} keys, {t[48:54]}, {kv[51]}, A={(a.start, a.end)} B={(b.start, b.end)}",
    )

    # -- content from before the destination lands at its HEAD --------------
    st, sq, obs = fresh({"hdA": {20: 3, 110: 0, 140: 5}, "hdB": {120: 1, 150: 2}})
    st.gap = 5
    sq.define_shot("Src", 0, 50, objects=["hdA"])
    d = sq.define_shot("Dest", 100, 160, objects=["hdA", "hdB"])
    after = sq.define_shot("After", 170, 200, objects=[])
    sep = sq.sequence_separation()
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "hdA",
                "attr": "location",
                "times": [20.0],
                "start": 20.0,
                "end": 20.0,
            }
        ],
        d.shot_id,
    )
    ta, tb = set(times_of(obs["hdA"])), set(times_of(obs["hdB"]))
    check(
        "move to shot: from before -> lands at the start, content pushed later",
        100.0 in ta and {110.0 + sep, 140.0 + sep} <= ta and not ({110.0, 140.0} & ta),
        f"A={sorted(ta)} sep={sep}",
    )
    check(
        "move to shot: the insert moves every object and the downstream shot",
        {120.0 + sep, 150.0 + sep} <= tb
        and (d.start, d.end) == (100, 160 + sep)
        and (after.start, after.end) == (170 + sep, 200 + sep),
        f"B={sorted(tb)} dest=({d.start}, {d.end}) after=({after.start}, {after.end})",
    )

    # -- a head run carries its gap overhang and keeps its timing -----------
    st, sq, obs = fresh({"ovA": {45: 0, 54: 1, 64.2: 1, 71.7: 0, 84: 1}})
    st.gap = 5
    sq.define_shot("A", 0, 50, objects=["ovA"])
    b = sq.define_shot("B", 60, 100, objects=["ovA"])
    c = sq.define_shot("C", 110, 140, objects=[])
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "ovA",
                "attr": "location",
                "times": [45.0],
                "start": 45.0,
                "end": 45.0,
            }
        ],
        b.shot_id,
    )
    check(
        "move to shot: the run carries its overhang and the train follows as one",
        times_of(obs["ovA"]) == [60.0, 69.0, 79.2, 86.7, 99.0]
        and (b.start, b.end) == (60, 115)
        and (c.start, c.end) == (125, 155),
        f"{times_of(obs['ovA'])} B=({b.start}, {b.end}) C=({c.start}, {c.end})",
    )
    st, sq, obs = fresh({"ovN": {20: 3, 40: 0, 54: 1, 64.2: 1, 84: 0}})
    st.gap = 5
    sq.define_shot("A", 0, 50, objects=["ovN"])
    b = sq.define_shot("B", 60, 100, objects=["ovN"])
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "ovN",
                "attr": "location",
                "times": [20.0],
                "start": 20.0,
                "end": 20.0,
            }
        ],
        b.shot_id,
    )
    t = times_of(obs["ovN"])
    check(
        "move to shot: the overhang stays when the run is not the curve's last",
        60.0 in t and 40.0 in t and 54.0 in t,
        f"{t}",
    )

    st, sq, obs = fresh({"ovS": {45: 0, 50: 0, 54: 1, 64.2: 1, 71.7: 0, 84: 1}})
    st.gap = 5
    a = sq.define_shot("A", 0, 50, objects=["ovS"])
    b = sq.define_shot("B", 60, 100, objects=["ovS"])
    sq.ledger.record_key(_SSI._fc_key("ovS", fc_of(obs["ovS"])), 50.0, a.shot_id, "end")
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "ovS",
                "attr": "location",
                "times": [45.0],
                "start": 45.0,
                "end": 45.0,
            }
        ],
        b.shot_id,
    )
    t = times_of(obs["ovS"])
    check(
        "move to shot: a system seam sample neither pins the overhang nor travels",
        69.0 in t and 50.0 in t and 54.0 not in t,
        f"{t}",
    )

    # -- trim: the system's own sample on the bound is not content ---------
    st, sq, obs = fresh({"pinned": {20: 0, 60: 10, 200: 10}})
    s0 = sq.define_shot("S0", 0, 200, objects=["pinned"])
    sq.ledger.record_key(
        _SSI._fc_key("pinned", fc_of(obs["pinned"])), 200.0, s0.shot_id, "end"
    )
    _h, tail = sq.trim_shot_to_content(s0.shot_id, edge="trailing")
    check(
        "trim: the system's own end sample is not content (a pinned end trims)",
        (s0.start, s0.end) == (0, 60) and tail == -140,
        f"{(s0.start, s0.end)} tail={tail}",
    )
    st, sq, obs = fresh({"stepped": {20: 0, 150: 10}})
    s0 = sq.define_shot("S0", 0, 200, objects=["stepped"])
    sq.ledger.record_step(
        _SSI._fc_key("stepped", fc_of(obs["stepped"])), 150.0, "BEZIER", "BEZIER"
    )
    _h, tail = sq.trim_shot_to_content(s0.shot_id, edge="trailing")
    check(
        "trim: a hold stepped onto the animator's own key still holds the bound",
        (s0.start, s0.end) == (0, 150),
        f"{(s0.start, s0.end)} tail={tail}",
    )
    st, sq, obs = fresh(
        {"mover": {20: 0, 60: 10}, "proxyFlat": {t: 1.0 for t in range(0, 201, 2)}}
    )
    s0 = sq.define_shot("S0", 0, 100, objects=["mover", "proxyFlat"])
    _h, tail = sq.trim_shot_to_content(s0.shot_id, edge="trailing")
    check(
        "trim: a member with no motion in the shot holds no bound (a flat bake)",
        (s0.start, s0.end) == (0, 60)
        and tail == -40
        and len(times_of(obs["proxyFlat"])) == 101,
        f"{(s0.start, s0.end)} tail={tail} proxy keys={len(times_of(obs['proxyFlat']))}",
    )

    # -- a moving bound takes the system's own samples with it -------------
    # Mirror of mayatk's Step 3.1 -> Shot 3.3 case: the trim rippled the
    # neighbour back onto the frames the shot gave up and only then
    # reconciled, leaving the pivot's end sample mid-content ("the plug
    # animation now jumps").  mayatk's tests carry the production numbers.
    def play(ob, frames):
        fc = fc_of(ob)
        return [round(fc.evaluate(f), 4) for f in frames]

    st, sq, obs = fresh(
        {
            "plugY": {
                20: -7.6,
                40: -5.9,
                50: 0.0,
                100: 0.0,
                115: 0.0,
                120: 0.0,
                140: -5.9,
                170: -7.6,
            }
        }
    )
    s0 = sq.define_shot("S0", 0, 100, objects=["plugY"])
    s1 = sq.define_shot("S1", 115, 200, objects=["plugY"])
    pkey = _SSI._fc_key("plugY", fc_of(obs["plugY"]))
    sq.ledger.record_key(pkey, 100.0, s0.shot_id, "end")
    before = play(obs["plugY"], range(115, 171))
    _h, tail = sq.trim_shot_to_content(s0.shot_id, edge="trailing")
    after = play(obs["plugY"], range(65, 121))
    check(
        "trim: the end sample leaves before the neighbour lands on its frames",
        tail == -50
        and (s0.start, s0.end, s1.start, s1.end) == (0, 50, 65, 150)
        and times_of(obs["plugY"]) == [20.0, 40.0, 50.0, 65.0, 70.0, 90.0, 120.0]
        and list(sq.ledger.key_times(pkey)) == []
        and before == after,
        f"tail={tail} bounds={(s0.start, s0.end, s1.start, s1.end)} "
        f"times={times_of(obs['plugY'])} claims={list(sq.ledger.key_times(pkey))} "
        f"playback_same={before == after}",
    )

    st, sq, obs = fresh({"headShared": {10: 0, 40: 5, 60: 5, 120: 5, 150: 9, 200: 9}})
    p = sq.define_shot("P", 0, 50, objects=["headShared"])
    sm = sq.define_shot("S", 60, 200, objects=["headShared"])
    hkey = _SSI._fc_key("headShared", fc_of(obs["headShared"]))
    for kp in fc_of(obs["headShared"]).keyframe_points:
        if abs(kp.co[0] - 40) < 1e-3:
            kp.interpolation = "CONSTANT"  # the cut can reshape nothing
    fc_of(obs["headShared"]).update()
    sq.ledger.record_key(hkey, 60.0, sm.shot_id, "start")
    head, tail = sq.trim_shot_to_content(sm.shot_id, edge="leading")
    check(
        "trim: a leading trim takes its own start sample with it",
        (head, tail) == (60, 0)
        and (p.start, p.end, sm.start, sm.end) == (60, 110, 120, 200)
        and times_of(obs["headShared"]) == [70.0, 100.0, 120.0, 150.0, 200.0]
        and list(sq.ledger.key_times(hkey)) == [],
        f"deltas={(head, tail)} bounds={(p.start, p.end, sm.start, sm.end)} "
        f"times={times_of(obs['headShared'])} claims={list(sq.ledger.key_times(hkey))}",
    )

    st, sq, obs = fresh(
        {"moverA": {20: 0, 50: 10}, "holdB": {10: 3, 100: 3, 115: 3, 140: 8}}
    )
    s0 = sq.define_shot("S0", 0, 100, objects=["moverA", "holdB"])
    s1 = sq.define_shot("S1", 115, 200, objects=["holdB"])
    hkey = _SSI._fc_key("holdB", fc_of(obs["holdB"]))
    sq.ledger.record_key(hkey, 100.0, s0.shot_id, "end")
    sq.trim_shot_to_content(s0.shot_id, edge="trailing")
    # A member flat through the shot: its end sample holds nothing, at the
    # new bound either, so it is cut as the bound leaves it, claim and all --
    # mayatk's rule (``test_a_trim_cuts_the_end_sample_of_a_member_flat_
    # through_the_shot``; BACKLOG 2026-09-19, mayatk is the reference).
    check(
        "trim: the end sample of a member flat through the shot is cut, claim released",
        (s0.start, s0.end, s1.start, s1.end) == (0, 50, 65, 150)
        and times_of(obs["holdB"]) == [10.0, 65.0, 90.0]
        and sorted(sq.ledger.key_times(hkey)) == [],
        f"bounds={(s0.start, s0.end, s1.start, s1.end)} times={times_of(obs['holdB'])} "
        f"claims={sorted(sq.ledger.key_times(hkey))}",
    )

    st, sq, obs = fresh(
        {
            "dragged": {
                20: -7.6,
                40: -5.9,
                50: 0.0,
                100: 0.0,
                115: 0.0,
                120: 0.0,
                140: -5.9,
                170: -7.6,
            }
        }
    )
    a = sq.define_shot("A", 0, 100, objects=["dragged"])
    b = sq.define_shot("B", 115, 200, objects=["dragged"])
    gkey = _SSI._fc_key("dragged", fc_of(obs["dragged"]))
    sq.ledger.record_key(gkey, 100.0, a.shot_id, "end")
    before = play(obs["dragged"], range(115, 171))
    sq.resize_shot_bounds(a.shot_id, 0, 50)
    after = play(obs["dragged"], range(65, 121))
    check(
        "resize_shot_bounds: a tail shrink takes its end sample with it",
        (a.start, a.end, b.start, b.end) == (0, 50, 65, 150)
        and times_of(obs["dragged"]) == [20.0, 40.0, 50.0, 65.0, 70.0, 90.0, 120.0]
        and list(sq.ledger.key_times(gkey)) == []
        and before == after,
        f"bounds={(a.start, a.end, b.start, b.end)} times={times_of(obs['dragged'])} "
        f"claims={list(sq.ledger.key_times(gkey))} playback_same={before == after}",
    )

    # -- a bound change ripples the gap's content too; a shrink stops at content
    # (mirrors mayatk's batch of 2026-09-06; its tests carry the measurements)
    def constant(ob, *frames):
        fc = fc_of(ob)
        for kp in fc.keyframe_points:
            if any(abs(kp.co[0] - f) < 1e-3 for f in frames):
                kp.interpolation = "CONSTANT"
        fc.update()

    def bounds(*shots_):
        return tuple(float(v) for sh in shots_ for v in (sh.start, sh.end))

    st, sq, obs = fresh({"tail": {10: 0, 20: 1, 45: 1}, "nbr": {60: 0, 80: 5}})
    a = sq.define_shot("A", 0, 40, objects=["tail"])
    b = sq.define_shot("B", 55, 100, objects=["nbr"])
    sq.resize_shot_bounds(a.shot_id, 0, 50)
    check(
        "carry_gap: a tail grow carries the gap content away",
        bounds(a, b) == (0, 50, 65, 110)
        and times_of(obs["tail"]) == [10.0, 20.0, 55.0]
        and times_of(obs["nbr"]) == [70.0, 90.0],
        f"bounds={bounds(a, b)} tail={times_of(obs['tail'])} nbr={times_of(obs['nbr'])}",
    )

    st, sq, obs = fresh({"tail": {10: 0, 20: 1, 45: 1}, "nbr": {60: 0, 80: 5}})
    a = sq.define_shot("A", 0, 40, objects=["tail"])
    b = sq.define_shot("B", 55, 100, objects=["nbr"])
    sq.resize_shot_bounds(a.shot_id, 0, 30)
    check(
        "carry_gap: a tail shrink carries the gap content in",
        bounds(a, b) == (0, 30, 45, 90)
        and times_of(obs["tail"]) == [10.0, 20.0, 35.0]
        and times_of(obs["nbr"]) == [50.0, 70.0],
        f"bounds={bounds(a, b)} tail={times_of(obs['tail'])} nbr={times_of(obs['nbr'])}",
    )

    st, sq, obs = fresh({"cl": {10: 0, 20: 1, 35: 4}, "nbr": {60: 0, 80: 5}})
    a = sq.define_shot("A", 0, 40, objects=["cl"])
    b = sq.define_shot("B", 55, 100, objects=["nbr"])
    sq.resize_shot_bounds(a.shot_id, 0, 15)
    check(
        "clamp: a plain shrink stops at the shot's content",
        bounds(a, b) == (0, 35, 50, 95)
        and times_of(obs["cl"]) == [10.0, 20.0, 35.0]
        and times_of(obs["nbr"]) == [55.0, 75.0],
        f"bounds={bounds(a, b)} cl={times_of(obs['cl'])} nbr={times_of(obs['nbr'])}",
    )

    st, sq, obs = fresh({"tail": {10: 0, 20: 1, 45: 1}, "nbr": {60: 0, 80: 5}})
    a = sq.define_shot("A", 0, 40, objects=["tail"])
    b = sq.define_shot("B", 55, 100, objects=["nbr"])
    sq.slide_shot(a.shot_id, 20, direction="downstream")
    check(
        "carry_gap: a whole-shot slide carries its gap exactly once",
        bounds(a, b) == (20, 60, 75, 120)
        and times_of(obs["tail"]) == [30.0, 40.0, 65.0]
        and times_of(obs["nbr"]) == [80.0, 100.0],
        f"bounds={bounds(a, b)} tail={times_of(obs['tail'])} nbr={times_of(obs['nbr'])}",
    )

    st, sq, obs = fresh(
        {"deb": {20: -7.6, 40: -5.9, 50: 0.0, 100: 0.0, 115: 0.0, 140: 5.0}}
    )
    constant(obs["deb"], 50, 100, 115)
    a = sq.define_shot("A", 0, 100, objects=["deb"])
    b = sq.define_shot("B", 115, 200, objects=["deb"])
    _h, tail = sq.trim_shot_to_content(a.shot_id, edge="trailing")
    check(
        "trim: a redundant unclaimed key on the bound holds nothing and is cut",
        tail == -50
        and bounds(a, b) == (0, 50, 65, 150)
        and times_of(obs["deb"]) == [20.0, 40.0, 50.0, 65.0, 90.0],
        f"tail={tail} bounds={bounds(a, b)} times={times_of(obs['deb'])}",
    )

    st, sq, obs = fresh(
        {"ret": {10: 0, 20: 5, 30: 5, 40: 5, 52: 5, 60: 5, 70: 9, 100: 9}}
    )
    constant(obs["ret"], 30, 40, 52, 60)
    a = sq.define_shot("A", 0, 30, objects=["ret"])
    ins = sq.define_shot("INS", 40, 52, objects=[])
    c = sq.define_shot("C", 60, 100, objects=["ret"])
    rkey = _SSI._fc_key("ret", fc_of(obs["ret"]))
    for t, owner, edge_ in (
        (30.0, a, "end"),
        (40.0, ins, "start"),
        (52.0, ins, "end"),
        (60.0, c, "start"),
    ):
        sq.ledger.record_key(rkey, t, owner.shot_id, edge_)
    sq.delete_shot(ins.shot_id)
    check(
        "delete: an empty shot's pins go before the gap closes on them",
        bounds(a, c) == (0, 30, 40, 80)
        and times_of(obs["ret"]) == [10.0, 20.0, 30.0, 40.0, 50.0, 80.0]
        and round(fc_of(obs["ret"]).evaluate(52.0), 3) == 9.0
        and sorted(sq.ledger.key_times(rkey)) == [30.0, 40.0],
        f"bounds={bounds(a, c)} times={times_of(obs['ret'])} "
        f"at52={fc_of(obs['ret']).evaluate(52.0)} claims={sorted(sq.ledger.key_times(rkey))}",
    )

    # The same chain on a seam two shots SHARE (mirror of mayatk's, measured
    # 2026-09-23 at gap 0): the respace split leaves the empty shot ending on
    # a claimed copy of C's opening pose, and the claim must name the EMPTY
    # shot's end, or its delete leaves the sample for C's ripple to land on.
    st, sq, obs = fresh({"cseam": {20: 5, 40: 0, 50: 0, 70: -6, 100: -8}})
    a = sq.define_shot("A", 0, 50, objects=["cseam"])
    c = sq.define_shot("C", 50, 100, objects=["cseam"])
    st.gap = 0.0

    def c_plays():
        return [
            round(fc_of(obs["cseam"]).evaluate(c.start + f), 4) for f in range(0, 51, 2)
        ]

    before = c_plays()
    ins = sq.insert_shot("INS", 12.0, at_position=2)
    sq.apply_gap(20.0, scope="all")
    sq.delete_shot(ins.shot_id)
    check(
        "delete: an empty shot on a contiguous seam leaves nothing behind",
        bounds(a, c) == (0, 50, 70, 120)
        and c_plays() == before
        and times_of(obs["cseam"]) == [20.0, 40.0, 50.0, 70.0, 90.0, 120.0],
        f"bounds={bounds(a, c)} plays={c_plays()} was={before} "
        f"times={times_of(obs['cseam'])}",
    )

    st, sq, obs = fresh({"stl": {10: 0, 20: 1, 38: 1, 44: 0}, "snb": {60: 0, 70: 5}})
    a = sq.define_shot("A", 0, 40, objects=["stl"])
    b = sq.define_shot("B", 55, 100, objects=["snb"])
    sq.slide_shot(a.shot_id, 20, direction=None)
    check(
        "slide: the tail lands before the neighbour starts",
        bounds(a, b) == (10, 50, 55, 100)
        and times_of(obs["stl"]) == [20.0, 30.0, 48.0, 54.0]
        and times_of(obs["snb"]) == [60.0, 70.0],
        f"bounds={bounds(a, b)} stl={times_of(obs['stl'])} snb={times_of(obs['snb'])}",
    )

    # -- a moved key lands INSIDE the destination, twin or not -------------
    st, sq, obs = fresh({"twinA": {10: 0, 20: 5, 30: 9}})
    sq.define_shot("A", 0, 40, objects=["twinA"])
    b = sq.define_shot("B", 60, 100, objects=[])
    twin = 30.0 + 2.12585e-7
    fc = fc_of(obs["twinA"])
    fc.keyframe_points.insert(31.0, 9.0)
    next(kp for kp in fc.keyframe_points if abs(kp.co[0] - 31.0) < 1e-3).co[0] = twin
    fc.update()
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "twinA",
                "attr": "location",
                "times": [twin],
                "start": twin,
                "end": twin,
            }
        ],
        b.shot_id,
    )
    landed = [kp.co[0] for kp in fc_of(obs["twinA"]).keyframe_points if kp.co[0] > 40]
    check(
        "move to shot: a near-duplicate twin still lands inside the destination",
        bool(landed) and min(landed) >= b.start,
        f"landed={landed} b.start={b.start}",
    )

    # -- the destination keeps an object whose moved key carries no motion --
    st, sq, obs = fresh({"flatM": {10: 5, 20: 5}})
    sq.define_shot("A", 0, 40, objects=["flatM"])
    b = sq.define_shot("B", 60, 100, objects=[])
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "flatM",
                "attr": "location",
                "times": [20.0],
                "start": 20.0,
                "end": 20.0,
            }
        ],
        b.shot_id,
    )
    dest = sq.shot_by_id(b.shot_id)
    check(
        "move to shot: the destination owns what was moved into it",
        "flatM" in dest.objects,
        f"objects={dest.objects}",
    )

    # -- a growing retime ripples BEFORE it scales (mirror of mayatk) --------
    st, sq, obs = fresh({"rtA": {10: 0, 40: 5}, "rtB": {70: 0, 90: 5}})
    a = sq.define_shot("A", 0, 50, objects=["rtA"])
    b = sq.define_shot("B", 60, 100, objects=["rtB"])
    sq.set_shot_duration(a.shot_id, 100)
    check(
        "retime order: set_shot_duration keeps the scaled key out of the ripple",
        times_of(obs["rtA"]) == [20.0, 80.0]
        and times_of(obs["rtB"]) == [120.0, 140.0]
        and (b.start, b.end) == (110, 150),
        f"A={times_of(obs['rtA'])} B={times_of(obs['rtB'])} {(b.start, b.end)}",
    )
    st, sq, obs = fresh({"roA": {10: 0, 40: 5}, "roB": {70: 0, 90: 5}})
    a = sq.define_shot("A", 0, 50, objects=["roA"])
    b = sq.define_shot("B", 60, 100, objects=["roB"])
    sq.resize_object(a.shot_id, "roA", 10, 40, 10, 80)
    check(
        "retime order: resize_object keeps the scaled key out of the ripple",
        times_of(obs["roA"]) == [10.0, 80.0]
        and times_of(obs["roB"]) == [100.0, 120.0]
        and (b.start, b.end) == (90, 130),
        f"A={times_of(obs['roA'])} B={times_of(obs['roB'])} {(b.start, b.end)}",
    )

    # -- a SHRINKING retime scales first, then ripples (mirror of mayatk's
    # TestAShrinkingRetimeNeverScalesItsNeighbour, 2026-09-19).  Rippled first,
    # a shrink wider than the gap landed the neighbour inside the pivot's span
    # and the scale then retimed the neighbour's keys with the pivot's.
    def shrink_scene():
        st_, sq_, obs_ = fresh(
            {"srA": {5: 0, 20: 4, 35: 1, 48: 6}, "srB": {62: 0, 72: 7, 90: 2}}
        )
        a_ = sq_.define_shot("A", 0, 50, objects=["srA"])
        b_ = sq_.define_shot("B", 60, 100, objects=["srB"])
        return sq_, obs_, a_, b_

    sq, obs, sa, sb = shrink_scene()
    sq.resize_shot(sa.shot_id, 0, 25)  # halved; the gap is only 10 wide
    check(
        "shrink retime: the neighbour moves rigidly, the pivot scales",
        bounds(sa, sb) == (0, 25, 35, 75)
        and times_of(obs["srA"]) == [2.5, 10.0, 17.5, 24.0]
        and times_of(obs["srB"]) == [37.0, 47.0, 65.0],
        f"bounds={bounds(sa, sb)} A={times_of(obs['srA'])} B={times_of(obs['srB'])}",
    )
    sq, obs, sa, sb = shrink_scene()
    sq.set_shot_duration(sa.shot_id, 25)
    check(
        "shrink duration: the neighbour moves rigidly, the pivot scales",
        bounds(sa, sb) == (0, 25, 35, 75)
        and times_of(obs["srA"]) == [2.5, 10.0, 17.5, 24.0]
        and times_of(obs["srB"]) == [37.0, 47.0, 65.0],
        f"bounds={bounds(sa, sb)} A={times_of(obs['srA'])} B={times_of(obs['srB'])}",
    )
    sq, obs, sa, sb = shrink_scene()
    sq.resize_shot(sb.shot_id, 80, 100)  # B's head in by 20; the gap is 10
    check(
        "shrink retime (head): the shot before moves rigidly",
        bounds(sa, sb) == (20, 70, 80, 100)
        and times_of(obs["srB"]) == [81.0, 86.0, 95.0]
        and times_of(obs["srA"]) == [25.0, 40.0, 55.0, 68.0],
        f"bounds={bounds(sa, sb)} A={times_of(obs['srA'])} B={times_of(obs['srB'])}",
    )

    # -- a retime carries the system's claims (mirror of mayatk's
    # TestARetimeCarriesTheSystemsClaims, 2026-09-19).  Every retime used to
    # remap the keys and never the edit ledger, so a claimed end pin scaled
    # onto the new end was released by the next reconcile and read as an
    # animator key.
    st, sq, obs = fresh({"rtcA": {10: 0, 40: 5, 70: 2, 90: 8}})
    ra = sq.define_shot("A", 0, 50, objects=["rtcA"])
    sq.define_shot("B", 60, 100, objects=["rtcA"])
    rfc = fc_of(obs["rtcA"])
    rkey = _SSI._fc_key("rtcA", rfc)
    obs["rtcA"].location.x = rfc.evaluate(50.0)
    obs["rtcA"].keyframe_insert(data_path="location", index=0, frame=50)
    sq.ledger.record_key(rkey, 50.0, ra.shot_id, "end")
    sq._enforce_gap_holds()  # the seam (the pin) is made CONSTANT and claimed
    check(
        "retime claims: setup -- the pin and its hold are claimed",
        (50.0, ra.shot_id, "end") in sq.ledger.key_records(rkey)
        and 50.0 in sq.ledger.step_times(rkey),
        f"claims={sq.ledger.key_records(rkey)} steps={sq.ledger.step_times(rkey)}",
    )
    sq.resize_shot(ra.shot_id, 0, 70)  # the Shift edge drag: A's end +20
    check(
        "retime claims: the scaled end pin is still the system's on the bound",
        (70.0, ra.shot_id, "end")
        in [(round(t, 3), o, e) for t, o, e in sq.ledger.key_records(rkey)],
        f"times={times_of(obs['rtcA'])} claims={sq.ledger.key_records(rkey)}",
    )
    check(
        "retime claims: the retimed seam's hold is still claimed",
        70.0 in [round(t, 3) for t in sq.ledger.step_times(rkey)],
        f"steps={sq.ledger.step_times(rkey)}",
    )

    # (A Ctrl edge drag moving no sample, and Ctrl past the neighbour, are the
    # panel's gesture:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # -- a content-derived bound snaps OUTWARD (mirror of mayatk's
    # TestABoundEnclosesFractionalContent, 2026-09-19).  Rounded to the
    # nearest frame, a start landed past the first key and an end short of the
    # last on fractional content -- what a retime leaves.
    def frac_scene():
        st_, sq_, obs_ = fresh(
            {"frA": {10.6: 0, 25: 5, 40.4: 2}, "frB": {70: 0, 90: 5}}
        )
        a_ = sq_.define_shot("A", 0, 50, objects=["frA"])
        sq_.define_shot("B", 60, 100, objects=["frB"])
        return sq_, a_

    sq, fa = frac_scene()
    sq.trim_shot_to_content(fa.shot_id, edge="both")
    check(
        "fractional content: a trim encloses it",
        (fa.start, fa.end) == (10.0, 41.0),
        f"{(fa.start, fa.end)}",
    )
    sq, fa = frac_scene()
    sq.resize_shot_bounds(fa.shot_id, 0, 30)  # asked past the last key, 40.4
    check(
        "fractional content: a plain shrink stops outside it",
        fa.end == 41.0,
        f"end={fa.end}",
    )
    st, sq, obs = fresh({"frM": {110: 0, 160.4: 5}})
    sq.define_shot("S0", 100, 170, objects=["frM"])
    fd = sq.define_shot("S1", 200, 240, objects=[])
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "frM",
                "attr": "location",
                "times": [110.0, 160.4],
                "start": 110.0,
                "end": 160.4,
            }
        ],
        fd.shot_id,
    )
    landed = max(times_of(obs["frM"]))
    check(
        "fractional content: Move to Shot's destination encloses what landed",
        abs(landed - 250.4) < 1e-3 and fd.end >= landed,
        f"landed={landed} dest_end={fd.end}",
    )
    # The head room: a block ending 0.05 before the destination, onto an object
    # keyed 0.2 into it, must be cleared whole (89.35 frames: 90, where the
    # nearest frame is 89), or the block lands past the destination's first
    # key, which then no longer moves with the rest of its content.
    st, sq, obs = fresh({"frH": {110.6: 0, 150: 5, 199.95: 2, 200.2: 7, 230: 1}})
    sq.define_shot("S0", 100, 200, objects=["frH"])
    hd = sq.define_shot("S1", 200, 240, objects=["frH"])
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "frH",
                "attr": "location",
                "times": [110.6, 150.0, 199.95],
                "start": 110.6,
                "end": 199.95,
            }
        ],
        hd.shot_id,
    )
    hkv = sorted(
        (round(kp.co[0], 3), round(kp.co[1]))
        for kp in fc_of(obs["frH"]).keyframe_points
    )
    check(
        "fractional content: Move to Shot's head room clears the whole block",
        hkv
        == [(200.0, 2), (201.0, 0), (240.4, 5), (290.35, 2), (291.2, 7), (321.0, 1)],
        f"keys={hkv}",
    )
    # (A key dragged past the end growing the shot around it is the panel's drag:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # -- a Move to Shot that extends its destination carries the end sample --
    st, sq, obs = fresh({"mvA": {10: 0, 20: 5, 30: 9}, "mvH": {50: 1, 70: 1, 110: 3}})
    sa = sq.define_shot("A", 0, 40, objects=["mvA"])
    sb = sq.define_shot("B", 60, 70, objects=["mvH"])
    sc = sq.define_shot("C", 80, 120, objects=[])
    hkey = _SSI._fc_key("mvH", fc_of(obs["mvH"]))
    sq.ledger.record_key(hkey, 70.0, sb.shot_id, "end")
    sq._enforce_gap_holds()
    check(
        "move to shot: setup -- the seam before C holds at 70",
        sq.ledger.owns_step(hkey, 70.0) and interp_at(obs["mvH"], 70) == "CONSTANT",
    )
    sq.move_sequences_to_shot(
        [
            {
                "kind": "anim",
                "obj": "mvA",
                "attr": "translateX",
                "times": [10.0, 20.0, 30.0],
                "start": 10.0,
                "end": 30.0,
            }
        ],
        sb.shot_id,
    )
    b, c = sq.shot_by_id(sb.shot_id), sq.shot_by_id(sc.shot_id)
    check(
        "move to shot: the destination grew and C rippled",
        (b.start, b.end) == (60, 80) and (c.start, c.end) == (90, 130),
        f"B=({b.start},{b.end}) C=({c.start},{c.end})",
    )
    # C's start carries the pin that stops the hold (BACKLOG 2026-10-04): it
    # went in with the setup's hold at 80 and rode C to 90.
    check(
        "move to shot: the claimed end sample followed the extended bound",
        times_of(obs["mvH"]) == [50.0, 80.0, 90.0, 120.0]
        and sq.ledger.key_records(hkey)
        == [(80.0, sb.shot_id, "end"), (90.0, sc.shot_id, "start")],
        f"{times_of(obs['mvH'])} {sq.ledger.key_records(hkey)}",
    )
    check(
        "move to shot: the hold moved to the new seam",
        interp_at(obs["mvH"], 80) == "CONSTANT"
        and not sq.ledger.owns_step(hkey, 70.0)
        and interp_at(obs["mvA"], 80) == "CONSTANT",
        f"H@80={interp_at(obs['mvH'], 80)} A@80={interp_at(obs['mvA'], 80)}",
    )

    # -- a FREE handle draws as a broken key; the system's samples get no dot ----
    # (The tangent-handle drag itself is the panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``,
    # through ``SequencerHost.edit_key_tangents`` + ``handle_drag_edit``.)
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector as _SC,
    )

    st, sq, obs = fresh({"tanA": {0: 0, 10: 5, 20: 0}})
    fc_tan = fc_of(obs["tanA"])
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    kp_mid.handle_left_type = "FREE"
    fc_tan.update()
    check(
        "preview: a FREE handle marks the key broken",
        _SC.build_curve_preview(fc_tan, 0, 20)["broken"] == [False, True, False],
        f"{_SC.build_curve_preview(fc_tan, 0, 20).get('broken')}",
    )
    # The shot system's claimed samples get no dot (mirror of mayatk's
    # TestSystemSamplesDrawNoDot, 2026-10-06): flagged hidden, curve intact.
    hidden = _SC.build_curve_preview(fc_tan, 0, 20, hidden_times=[10.0])
    check(
        "preview: a key on hidden_times is flagged hidden, the others not",
        hidden["hidden"] == [False, True, False] and len(hidden["keys"]) == 3,
        f"{hidden.get('hidden')}",
    )

    # -- a boundary sample follows its bound, or is cleaned up --------------
    st, sq, obs = fresh({"bndA": {1: 0, 50: 10}})
    sa = sq.define_shot("A", 1, 50, objects=["bndA"])
    key = _SSI._fc_key("bndA", fc_of(obs["bndA"]))
    sq.ledger.record_key(key, 50.0, sa.shot_id, "end")
    st.update_shot(sa.shot_id, end=40.0)
    moved, removed = sq._reconcile_boundary_keys()
    check(
        "boundary sample: followed its bound",
        moved == 1 and times_of(obs["bndA"]) == [1.0, 40.0],
        f"moved={moved} {times_of(obs['bndA'])}",
    )
    # A sample carrying a real pose is disowned, not cut.
    st.remove_shot(sa.shot_id)
    sq.ledger.disown_shot(sa.shot_id)
    _m, removed = sq._reconcile_boundary_keys()
    check(
        "boundary sample: a pose is kept, not cut",
        removed == 0 and times_of(obs["bndA"]) == [1.0, 40.0],
    )
    # One inside a flat plateau plays no part, so it goes -- and every claim
    # with it: a step claim left on the frame would later hand the pre-hold
    # tangent to a stepped key that landed there (_release_gap_holds).
    st, sq, obs = fresh({"bndC": {1: 5, 20: 5, 40: 5, 60: 9}})
    sc = sq.define_shot("C", 1, 60, objects=["bndC"])
    key = _SSI._fc_key("bndC", fc_of(obs["bndC"]))
    sq.ledger.record_key(key, 20.0, sc.shot_id, "start")
    sq.ledger.record_step(key, 20.0, "AUTO_CLAMPED", "AUTO_CLAMPED")
    sq.ledger.record_key(key, 30.0, sc.shot_id, "start")  # its key is long gone
    sq.ledger.record_step(key, 30.0, "AUTO_CLAMPED", "AUTO_CLAMPED")
    _m, removed = sq._reconcile_boundary_keys()
    check(
        "boundary sample: a redundant one is cut, with every claim on it",
        removed == 1
        and times_of(obs["bndC"]) == [1.0, 40.0, 60.0]
        and sq.ledger.key_times(key) == sq.ledger.step_times(key) == [],
        f"removed={removed} {times_of(obs['bndC'])} "
        f"claims={sq.ledger.key_times(key)} steps={sq.ledger.step_times(key)}",
    )
    # A redundant key a bound moved past is cut to clear its frame for what
    # ripples onto it -- which must not inherit a gap hold's step claim.
    st, sq, obs = fresh({"passA": {20: 1, 50: 0, 100: 0, 140: 5}})
    sq.define_shot("A", 0, 100, objects=["passA"])
    fc = fc_of(obs["passA"])
    key = _SSI._fc_key("passA", fc)
    sq.ledger.record_step(key, 100.0, "CONSTANT", "CONSTANT")
    cut = sq._cut_passed_bound_keys([("passA", fc, 100.0)], 0, 100, 0, 50)
    check(
        "a key a bound moved past is cut with every claim on it",
        cut == 1
        and times_of(obs["passA"]) == [20.0, 50.0, 140.0]
        and sq.ledger.step_times(key) == [],
        f"cut={cut} {times_of(obs['passA'])} steps={sq.ledger.step_times(key)}",
    )

    # -- delete takes the contents and closes the timeline ------------------
    st, sq, obs = fresh(
        {"delA": {1: 0, 20: 1}, "delB": {40: 0, 60: 1}, "delC": {80: 0, 100: 1}}
    )
    sa = sq.define_shot("A", 1, 20, objects=["delA"])
    sb = sq.define_shot("B", 40, 60, objects=["delB"])
    sc = sq.define_shot("C", 80, 100, objects=["delC"])
    res = sq.delete_shot(sb.shot_id)
    _fc = fc_of(obs["delB"])
    _key = _SSI._fc_key("delB", _fc)
    # B's own keys go; A and C keep the poses delB stood in beside it (0
    # through A, 1 through C), each pinned on its bound and claimed.
    check(
        "delete_shot: B's keys are gone, its neighbours keep their poses",
        times_of(obs["delB"]) == [20.0, 40.0]
        and [round(kp.co[1], 4) for kp in _fc.keyframe_points] == [0.0, 1.0]
        and sorted(sq.ledger.key_records(_key))
        == [(20.0, sa.shot_id, "end"), (40.0, sc.shot_id, "start")],
        f"{times_of(obs['delB'])} claims={sq.ledger.key_records(_key)}",
    )
    check(
        "delete_shot: the timeline closed by 40",
        round(res["closed"]) == 40
        and [(s.name, s.start, s.end) for s in sq.sorted_shots()]
        == [("A", 1.0, 20.0), ("C", 40.0, 60.0)],
        f"{res} {[(s.name, s.start, s.end) for s in sq.sorted_shots()]}",
    )
    check("delete_shot: C's keys came with it", times_of(obs["delC"]) == [40.0, 60.0])

    # Both halves are opt-out.
    st, sq, obs = fresh({"kB": {40: 0, 60: 1}, "kC": {80: 0, 100: 1}})
    sb = sq.define_shot("B", 40, 60, objects=["kB"])
    sq.define_shot("C", 80, 100, objects=["kC"])
    sq.delete_shot(sb.shot_id, delete_contents=False, close_gap=False)
    check(
        "delete_shot: contents and space can be kept",
        times_of(obs["kB"]) == [40.0, 60.0]
        and [(s.name, s.start, s.end) for s in sq.sorted_shots()]
        == [("C", 80.0, 100.0)],
    )

    # -- merge spans both, and releases the hold it swallows ----------------
    st, sq, obs = fresh({"mgA": {1: 0, 20: 9}, "mgB": {40: 9, 50: 3}})
    sa = sq.define_shot("A", 1, 20, objects=["mgA"])
    sb = sq.define_shot("B", 40, 50, objects=["mgB"])
    sq._enforce_gap_holds()
    merged = sq.merge_shots([sb.shot_id, sa.shot_id])
    check(
        "merge_shots: one shot spanning both",
        len(st.shots) == 1 and (merged.start, merged.end) == (1.0, 50.0),
        f"{len(st.shots)} {(merged.start, merged.end)}",
    )
    check(
        "merge_shots: objects folded in",
        sorted(merged.objects) == ["mgA", "mgB"],
        f"{merged.objects}",
    )
    check(
        "merge_shots: the swallowed gap's hold is released",
        interp_at(obs["mgA"], 20) != "CONSTANT",
    )

    # -- split divides one shot, leaving content alone ----------------------
    st, sq, obs = fresh({"spA": {1: 0, 30: 5, 60: 9}})
    sa = sq.define_shot("A", 1, 60, objects=["spA"])
    sq.split_shot(sa.shot_id, 30)
    check(
        "split_shot: two contiguous shots",
        [(s.name, s.start, s.end) for s in sq.sorted_shots()]
        == [("A", 1.0, 30.0), ("A_2", 30.0, 60.0)],
        f"{[(s.name, s.start, s.end) for s in sq.sorted_shots()]}",
    )
    check("split_shot: keys untouched", times_of(obs["spA"]) == [1.0, 30.0, 60.0])
    refused = False
    try:
        sq.split_shot(sa.shot_id, 1)
    except ValueError:
        refused = True
    check("split_shot: a cut on a bound is refused", refused)

    # Split membership comes from the shot being split, narrowed per side --
    # a scene-wide rediscovery would sweep in an object the shot never claimed.
    st, sq, obs = fresh(
        {
            "spHead": {1: 0, 20: 5},
            "spTail": {40: 0, 60: 5},
            "spStranger": {30: 0, 35: 5},
        }
    )
    sa = sq.define_shot("A", 1, 60, objects=["spHead", "spTail"])
    tail = sq.split_shot(sa.shot_id, 30)
    check(
        "split_shot: each half keeps only what animates in it",
        set(sq.shot_by_id(sa.shot_id).objects) == {"spHead"}
        and set(tail.objects) == {"spTail"},
        f"{sq.shot_by_id(sa.shot_id).objects} / {tail.objects}",
    )

    # -- add_shot_space anchors the head and shifts everything later -------
    st, sq, obs = fresh({"padA": {1: 0, 20: 1}, "padB": {40: 0, 60: 1}})
    sq.define_shot("A", 1, 20, objects=["padA"])
    sb = sq.define_shot("B", 40, 60, objects=["padB"])
    head, tail = sq.add_shot_space(sb.shot_id, 10, edge="leading")
    check(
        "add_shot_space: leading room opens in FRONT of the content",
        (head, tail) == (0.0, 10.0)
        and [(s.name, s.start, s.end) for s in sq.sorted_shots()]
        == [("A", 1.0, 20.0), ("B", 40.0, 70.0)]
        and times_of(obs["padB"]) == [50.0, 70.0]
        and times_of(obs["padA"]) == [1.0, 20.0],
        f"{(head, tail)} {[(s.name, s.start, s.end) for s in sq.sorted_shots()]} "
        f"{times_of(obs['padB'])}",
    )

    # The pad pushes the downstream shots along, not the upstream ones.
    st, sq, obs = fresh({"padE": {1: 0, 20: 1}, "padF": {40: 0, 60: 1}})
    sa = sq.define_shot("A", 1, 20, objects=["padE"])
    sq.define_shot("B", 40, 60, objects=["padF"])
    head, tail = sq.add_shot_space(sa.shot_id, 10, edge="leading")
    check(
        "add_shot_space: leading room ripples downstream",
        (head, tail) == (0.0, 10.0)
        and [(s.name, s.start, s.end) for s in sq.sorted_shots()]
        == [("A", 1.0, 30.0), ("B", 50.0, 70.0)]
        and times_of(obs["padE"]) == [11.0, 30.0]
        and times_of(obs["padF"]) == [50.0, 70.0],
        f"{(head, tail)} {[(s.name, s.start, s.end) for s in sq.sorted_shots()]} "
        f"{times_of(obs['padE'])} {times_of(obs['padF'])}",
    )

    # A negative pad reclaims only room that is actually empty.
    st, sq, obs = fresh({"padG": {10: 0, 20: 1}})
    sa = sq.define_shot("A", 1, 20, objects=["padG"])
    head, tail = sq.add_shot_space(sa.shot_id, -30, edge="leading")
    check(
        "add_shot_space: removing leading room never drags keys out the front",
        (head, tail) == (0.0, -9.0)
        and [(s.name, s.start, s.end) for s in sq.sorted_shots()] == [("A", 1.0, 11.0)]
        and times_of(obs["padG"]) == [1.0, 11.0],
        f"{(head, tail)} {[(s.name, s.start, s.end) for s in sq.sorted_shots()]} "
        f"{times_of(obs['padG'])}",
    )

    # -- extend: the reached key stays enclosed, the neighbour ripples ------
    st, sq, obs = fresh({"extA": {0: 0, 40: 5, 55: 6}, "extB": {80: 0, 100: 5}})
    s0 = sq.define_shot("S0", 0, 50, objects=["extA"])
    s1 = sq.define_shot("S1", 70, 120, objects=["extB"])
    head, tail = sq.extend_shot_to_fit(s0.shot_id)
    s1 = sq.shot_by_id(s1.shot_id)
    check(
        "extend: the gap key stays enclosed and S1 ripples from the NEW bound",
        (head, tail) == (0, 5)
        and sq.shot_by_id(s0.shot_id).end == 55
        and times_of(obs["extA"]) == [0, 40, 55]
        and (s1.start, s1.end) == (75, 125)
        and times_of(obs["extB"]) == [85, 105],
        f"{(head, tail)} S0.end={sq.shot_by_id(s0.shot_id).end} "
        f"A={times_of(obs['extA'])} S1={(s1.start, s1.end)} B={times_of(obs['extB'])}",
    )

    # -- extend reach: bounded, and it reads the leading gap too ------------
    st, sq, obs = fresh({"rchA": {0: 0, 40: 5, 55: 6, 66: 7}, "rchB": {80: 0, 100: 5}})
    s0 = sq.define_shot("S0", 0, 50, objects=["rchA"])
    sq.define_shot("S1", 70, 120, objects=["rchB"])
    head, tail = sq.extend_shot_to_fit(s0.shot_id, reach=8)
    check(
        "extend reach: a key past the reach is not reached for",
        (head, tail) == (0, 5) and times_of(obs["rchA"]) == [0, 40, 55, 71],
        f"{(head, tail)} A={times_of(obs['rchA'])}",
    )
    st, sq, obs = fresh({"ldA": {0: 0, 40: 5}, "ldB": {45: -2, 62: -1, 80: 0, 100: 5}})
    s0 = sq.define_shot("S0", 0, 50, objects=["ldA"])
    s1 = sq.define_shot("S1", 70, 120, objects=["ldB"])
    s0_before = [fc_of(obs["ldB"]).evaluate(f) for f in range(0, 51)]
    head, tail = sq.extend_shot_to_fit(s1.shot_id, reach=10)
    s0, s1 = sq.shot_by_id(s0.shot_id), sq.shot_by_id(s1.shot_id)
    s0_off = max(
        abs(v - fc_of(obs["ldB"]).evaluate(f)) for f, v in zip(range(-8, 43), s0_before)
    )
    # 45 rode with S0, and S0's closing pose was pinned first (42 now): the
    # ramp from 45 to 62 crossed the gap keyless, and S0 played it stretched
    # over its last frames (the one rule; mirror of mayatk's).
    check(
        "extend reach: the leading gap counts, the previous shot's span never",
        (head, tail) == (-8, 0)
        and s1.start == 62
        and times_of(obs["ldB"]) == [37, 42, 62, 80, 100]
        and (s0.start, s0.end) == (-8, 42)
        and times_of(obs["ldA"]) == [-8, 32]
        and s0_off < 1e-4,
        f"{(head, tail)} S1.start={s1.start} B={times_of(obs['ldB'])} "
        f"S0={(s0.start, s0.end)} A={times_of(obs['ldA'])} S0 off {s0_off}",
    )
    st, sq, obs = fresh({"noA": {0: 0, 40: 5}, "noB": {62: -1, 80: 0, 100: 5}})
    sq.define_shot("S0", 0, 50, objects=["noA"])
    s1 = sq.define_shot("S1", 70, 120, objects=["noB"])
    check(
        "extend without reach: the leading gap is still the previous shot's",
        sq.extend_shot_to_fit(s1.shot_id) == (0.0, 0.0),
    )

    # -- a flat key on the LAST shot's end holds no bound --------------------
    st, sq, obs = fresh({"trmA": {0: 0, 40: 5}, "trmB": {60: 0, 100: 5, 120: 5}})
    sq.define_shot("S0", 0, 50, objects=["trmA"])
    s1 = sq.define_shot("S1", 60, 120, objects=["trmB"])
    head, tail = sq.trim_shot_to_content(s1.shot_id, edge="trailing")
    check(
        "trim trailing: passes a flat key on the last shot's end and cuts it",
        (head, tail) == (0, -20)
        and sq.shot_by_id(s1.shot_id).end == 100
        and times_of(obs["trmB"]) == [60, 100],
        f"{(head, tail)} end={sq.shot_by_id(s1.shot_id).end} B={times_of(obs['trmB'])}",
    )
    st, sq, obs = fresh({"shpA": {0: 0, 40: 5}, "shpB": {60: 0, 100: 5, 120: 7}})
    sq.define_shot("S0", 0, 50, objects=["shpA"])
    s1 = sq.define_shot("S1", 60, 120, objects=["shpB"])
    check(
        "trim trailing: a shaped end key still holds the bound",
        sq.trim_shot_to_content(s1.shot_id, edge="trailing") == (0.0, 0.0)
        and times_of(obs["shpB"]) == [60, 100, 120],
        f"B={times_of(obs['shpB'])}",
    )
    st, sq, obs = fresh({"cycB": {60: 0, 100: 5, 120: 5}})
    s1 = sq.define_shot("S1", 60, 120, objects=["cycB"])
    fc_of(obs["cycB"]).extrapolation = "LINEAR"
    check(
        "trim trailing: a flat end key under non-constant extrapolation holds",
        sq.trim_shot_to_content(s1.shot_id, edge="trailing") == (0.0, 0.0),
    )
    st, sq, obs = fresh({"fstA": {0: 0, 20: 0, 40: 5}})
    s0 = sq.define_shot("S0", 0, 50, objects=["fstA"])
    head, tail = sq.trim_shot_to_content(s0.shot_id, edge="leading")
    check(
        "trim leading: a flat key on the first shot's start gives way too",
        (head, tail) == (20, 0) and times_of(obs["fstA"]) == [20, 40],
        f"{(head, tail)} A={times_of(obs['fstA'])}",
    )

    # -- what the panel's rows read: content channels and their labels ------------
    # (The widget sync, the key menu's edits, its clipboard and the key selection
    # mirror -- mayatk parity, 2026-10-04 to 10-11 -- are the panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)
    st, sq, obs = fresh({"wsFlat": {0: 1, 10: 1}, "wsMove": {0: 0, 10: 3}})
    vis = obs["wsVis"] = bpy.data.objects.new("wsVis", None)
    bpy.context.scene.collection.objects.link(vis)
    for f, hidden in ((0, False), (6, True), (12, False)):
        vis.hide_render = hidden
        vis.keyframe_insert("hide_render", frame=f)
    check(
        "content: a visibility-keyed object is shot content (mayatk CONTENT_ATTRS)",
        "wsVis" in sq._find_keyed_transforms(0, 24),
        f"{sq._find_keyed_transforms(0, 24)}",
    )
    check(
        "labels: hide_render reads as mayatk's 'visibility' channel",
        _SC.label_for("hide_render") == "visibility",
        _SC.label_for("hide_render"),
    )

    # -- a whole-shot move clears its landing zone (mayatk's move_curve_keys) ----
    st, sq, obs = fresh({"lzA": {0: 0, 10: 4, 20: 9, 30: 1, 40: 6}})
    sq.define_shot("L1", 0, 10, objects=["lzA"])
    lz2 = sq.define_shot("L2", 30, 40, objects=["lzA"])
    sq.slide_shot(lz2.shot_id, 15.0, direction=None)
    landed = times_of(obs["lzA"])
    check(
        "slide: a shared curve's gap key is not left interleaved in the landing zone",
        15.0 in landed and 25.0 in landed and not any(15.0 < t < 25.0 for t in landed),
        f"{landed}",
    )

    # -- a sparse move onto occupied frames overwrites, never stacks ------------
    st, sq, obs = fresh({"ovA": {0: 1, 10: 2, 20: 3, 30: 4}})
    ShotSequencer.move_curve_keys(fc_of(obs["ovA"]), [0.0, 20.0], 10.0)
    ov = sorted(
        (round(kp.co[0], 3), round(kp.co[1], 3))
        for kp in fc_of(obs["ovA"]).keyframe_points
    )
    check(
        "sparse move: a key landing on a stationary key replaces it (setKeyframe)",
        ov == [(10.0, 1.0), (30.0, 3.0)],
        f"{ov}",
    )

    # -- redundancy: a neighbour whose derived handle would re-derive holds ----
    st, sq, obs = fresh({"rdA": {-10: 0, 0: 5, 10: 5, 20: 5}})
    rd_fc = fc_of(obs["rdA"])
    for kp in rd_fc.keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "AUTO"
    rd_fc.keyframe_points[1].interpolation = "CONSTANT"  # 0 -> 10 steps
    rd_fc.update()
    rd_hl = rd_fc.keyframe_points[1]
    check(
        "redundancy: a step passes the span test, but a sloped AUTO neighbour keeps the key",
        abs(rd_hl.handle_left[1] - rd_hl.co[1]) > 1e-3
        and not ShotSequencer._sample_is_redundant(rd_fc, 2),
        f"hl={tuple(rd_hl.handle_left)}",
    )

    # -- an eased segment previews as the curve plays, not a straight line -----
    st, sq, obs = fresh({"ezA": {0: 0, 20: 10}})
    ez_fc = fc_of(obs["ezA"])
    ez_fc.keyframe_points[0].interpolation = "BACK"  # overshoots past 10
    ez_fc.update()
    ez_prev = _SC.build_curve_preview(ez_fc, 0, 20)
    ez_seg = (ez_prev or {}).get("segments", [{}])[0]
    ez_samples = ez_seg.get("samples") or []
    check(
        "curve preview: an eased (BACK) segment is drawn through samples of the curve",
        len(ez_samples) >= 8
        and all(abs(v - ez_fc.evaluate(t)) < 1e-4 for t, v in ez_samples)
        and ez_prev["val_max"] > 10.0,
        f"{len(ez_samples)} samples, val_max={ez_prev and ez_prev.get('val_max')}",
    )

    # -- respace retimes gap content and leaves every shot playing as it did ----
    # BACKLOG 2026-08-29 (blendertk): mayatk's pin + gap retime.  Keys sit
    # BETWEEN the bounds, so the pin has to split segments, and AUTO handles,
    # which re-derive from neighbours, are the hard case.
    for rt_handle in ("AUTO", "AUTO_CLAMPED"):
        st, sq, obs = fresh(
            {
                "rtA": {
                    -5: 1.0,
                    7: 4.0,
                    16: 2.5,
                    29: 9.0,
                    44: 1.0,
                    52: 6.0,
                    63: 3.0,
                    71: 8.0,
                    86: 0.0,
                    93: 5.0,
                    104: 2.0,
                }
            }
        )
        rt_fc = fc_of(obs["rtA"])
        for kp in rt_fc.keyframe_points:
            kp.handle_left_type = kp.handle_right_type = rt_handle
        rt_fc.update()
        for nm, a, b in (("RA", 0, 20), ("RB", 40, 60), ("RC", 80, 100)):
            sq.define_shot(nm, a, b, objects=["rtA"])
        rt_before = {
            sh.name: [rt_fc.evaluate(sh.start + i) for i in range(21)]
            for sh in sq.sorted_shots()
        }
        sq.respace(gap=5.0, start_frame=0)
        rt_fc = fc_of(obs["rtA"])
        rt_worst = max(
            abs(rt_fc.evaluate(sh.start + i) - rt_before[sh.name][i])
            for sh in sq.sorted_shots()
            for i in range(21)
        )
        rt_starts = [sh.start for sh in sq.sorted_shots()]
        check(
            f"respace ({rt_handle}): every shot plays exactly as before its gaps changed",
            rt_starts == [0.0, 25.0, 50.0] and rt_worst < 1e-4,
            f"starts={rt_starts} worst={rt_worst:.5f}",
        )
        check(
            f"respace ({rt_handle}): a gap key is retimed into the narrower gap",
            any(abs(kp.co[0] - 22.25) < 1e-3 for kp in rt_fc.keyframe_points),
            f"{[round(kp.co[0], 3) for kp in rt_fc.keyframe_points]}",
        )

    # -- a gap hold stops on the next shot's start (BACKLOG 2026-10-04) --------
    # The "between-bounds" control: a respace over gaps already 20 wide moves
    # nothing but still enforces the holds, and a hold steps the gap's last key
    # (29, 71).  With no key ON the next shot's start the held value ran on to
    # that shot's first key: 4 frames of the second shot and 6 of the third
    # changed, by up to 7.9.  Holds are now pinned where they stop.
    for nb_handle in ("AUTO_CLAMPED", "AUTO"):
        st, sq, obs = fresh(
            {
                "nopA": {
                    -5: 1.0,
                    7: 4.0,
                    16: 2.5,
                    29: 9.0,
                    44: 1.0,
                    52: 6.0,
                    63: 3.0,
                    71: 8.0,
                    86: 0.0,
                    93: 5.0,
                    104: 2.0,
                }
            }
        )
        nop_fc = fc_of(obs["nopA"])
        for kp in nop_fc.keyframe_points:
            kp.handle_left_type = kp.handle_right_type = nb_handle
        nop_fc.update()
        for nm, a, b in (("PA", 0, 20), ("PB", 40, 60), ("PC", 80, 100)):
            sq.define_shot(nm, a, b, objects=["nopA"])
        nop_before = {
            sh.name: [nop_fc.evaluate(sh.start + i) for i in range(21)]
            for sh in sq.sorted_shots()
        }
        sq.respace(gap=20.0, start_frame=0)
        nop_fc = fc_of(obs["nopA"])
        nop_changed = {
            sh.name: sum(
                abs(nop_fc.evaluate(sh.start + i) - nop_before[sh.name][i]) > 1e-4
                for i in range(21)
            )
            for sh in sq.sorted_shots()
        }
        nop_key = _SSI._fc_key("nopA", nop_fc)
        check(
            f"no-op respace ({nb_handle}): the gap holds change no frame of a shot",
            [(sh.start, sh.end) for sh in sq.sorted_shots()]
            == [(0.0, 20.0), (40.0, 60.0), (80.0, 100.0)]
            and not any(nop_changed.values()),
            f"changed frames {nop_changed}",
        )
        check(
            f"no-op respace ({nb_handle}): both gaps hold, stopped by claimed pins",
            interp_at(obs["nopA"], 29) == "CONSTANT"
            and interp_at(obs["nopA"], 71) == "CONSTANT"
            and sorted(sq.ledger.key_times(nop_key)) == [40.0, 80.0],
            f"@29={interp_at(obs['nopA'], 29)} @71={interp_at(obs['nopA'], 71)} "
            f"pins={sq.ledger.key_times(nop_key)}",
        )

    # A hold from inside a shot: the seam is the first shot's own last key and
    # the hold starts on it, as it always has -- what changed is where it stops.
    # The next shot's start is pinned, so that shot plays as it did (stepped
    # bare, the held value ran on to its first key at 50).
    st, sq, obs = fresh({"ngA": {0: 0.0, 10: 4.0, 50: 9.0, 60: 2.0}})
    ng_fc = fc_of(obs["ngA"])
    for kp in ng_fc.keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    ng_fc.update()
    sq.define_shot("GA", 0, 20, objects=["ngA"])
    ng_b = sq.define_shot("GB", 40, 60, objects=["ngA"])
    ng_before = [ng_fc.evaluate(t) for t in range(40, 61)]
    sq.reconcile_system_edits()
    ng_fc = fc_of(obs["ngA"])
    ng_worst = max(abs(ng_fc.evaluate(t) - v) for t, v in zip(range(40, 61), ng_before))
    ng_pins = sq.ledger.key_records(_SSI._fc_key("ngA", ng_fc))
    check(
        "a hold from inside a shot still starts there, and stops on the next start",
        ng_worst < 1e-4
        and interp_at(obs["ngA"], 10) == "CONSTANT"
        and ng_pins == [(40.0, ng_b.shot_id, "start")],
        f"worst={ng_worst:.5f} @10={interp_at(obs['ngA'], 10)} pins={ng_pins}",
    )

    # A curve through a shot keyless: the pin that stops the previous hold is
    # its only key in that shot -- the next gap's seam.  A shot's start pin is
    # never held: stepped, it would hold the shot still from its first frame.
    st, sq, obs = fresh({"thA": {0: 0.0, 10: 4.0, 90: 9.0, 100: 2.0}})
    th_fc = fc_of(obs["thA"])
    for kp in th_fc.keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    th_fc.update()
    for nm, a, b in (("TA", 0, 20), ("TB", 40, 60), ("TC", 80, 100)):
        sq.define_shot(nm, a, b, objects=["thA"])
    th_frames = list(range(40, 61)) + list(range(80, 101))
    th_before = [th_fc.evaluate(t) for t in th_frames]
    sq.reconcile_system_edits()
    th_fc = fc_of(obs["thA"])
    th_worst = max(abs(th_fc.evaluate(t) - v) for t, v in zip(th_frames, th_before))
    check(
        "a curve through a shot keyless never holds that shot (its start pin)",
        th_worst < 1e-4 and interp_at(obs["thA"], 40) not in (None, "CONSTANT"),
        f"worst={th_worst:.5f} @40={interp_at(obs['thA'], 40)}",
    )

    # A curve holding only behavior claims was swept by neither the hold nor
    # the boundary pass, so its claims outlived it (SOL_REPLACE_ASSEMBLY, Maya:
    # 399 on 24 curves the scene no longer had).  A deleted owner's claims go;
    # a renamed owner's are re-keyed first, not forgotten.
    st, sq, obs = fresh({"dcGone": {0: 0.0, 10: 1.0}, "dcOld": {0: 0.0, 10: 1.0}})
    sq.define_shot("DC", 0, 20, objects=["dcGone", "dcOld"])
    for obj_name in ("dcGone", "dcOld"):
        dc_key = _SSI._fc_key(obj_name, fc_of(obs[obj_name]))
        sq.ledger.record_authored(dc_key, 10.0, 0, "highlight", obj_name)
    bpy.data.objects.remove(obs["dcGone"])
    obs["dcOld"].name = "dcNew"
    sq.reconcile_system_edits()
    dc_claims = sq.ledger.authored()
    check(
        "a deleted behavior curve's claims are forgotten, a renamed one's followed",
        dc_claims == [(_SSI._fc_key("dcNew", fc_of(obs["dcOld"])), 10.0)],
        str(dc_claims),
    )

    # A merge retires the keeper's END sample in place, as it does the removed
    # shots': left to the reconcile it FOLLOWED the merged end across every key
    # the merge took in (found 2026-10-05 on a respace's pins: 84 frames of the
    # curve changed).  A key in the gap (30): A's tail ramps toward it rather
    # than holding, so the respace pins A's end (a held tail is a hold, which
    # it no longer pins -- BACKLOG 2026-10-05).
    st, sq, obs = fresh({"mgA": {0: 0.0, 10: 4.0, 30: 6.0, 50: 9.0, 55: 2.0}})
    mg_fc = fc_of(obs["mgA"])
    for kp in mg_fc.keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    mg_fc.update()
    mg_a = sq.define_shot("MA", 0, 20, objects=["mgA"])
    mg_b = sq.define_shot("MB", 40, 60, objects=["mgA"])
    sq.respace(gap=25.0, start_frame=0)  # a gap change pins every bound
    mg_fc = fc_of(obs["mgA"])
    mg_fixture = 20.0 in sq.ledger.key_times(_SSI._fc_key("mgA", mg_fc))
    mg_keys = times_of(obs["mgA"])
    mg_frames = [t for t in range(-10, 111) if not 20 < t < 45]  # held span out
    mg_before = [mg_fc.evaluate(t) for t in mg_frames]
    sq.merge_shots([mg_a.shot_id, mg_b.shot_id])  # no gap: the gap key's hold goes
    mg_fc = fc_of(obs["mgA"])
    mg_changed = [
        t for t, v in zip(mg_frames, mg_before) if abs(mg_fc.evaluate(t) - v) > 1e-4
    ]
    check(
        "merge: the keeper's end pin stays put, nothing moves",
        mg_fixture and times_of(obs["mgA"]) == mg_keys and not mg_changed,
        f"fixture={mg_fixture} keys {mg_keys} -> {times_of(obs['mgA'])} "
        f"changed {mg_changed[:3]}",
    )

    # (A point clip drag moving what its mark was drawn from is the panel's drag
    # over ``ShotSequencer.move_stepped_keys``:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # -- a key a hair off a bound never absorbs the bound's pin (BTK-SHOTS-7) ----
    # ``keyframe_points.insert`` REPLACES a key within 0.01 frame, so a pin for
    # B's start at 40 landed on the animator's key at 39.995 (the split's
    # handles written onto it, its value overwritten, the pin's claim left on a
    # frame no key held): B's first frames played 2.1 off.  Blender holds no
    # second key that close (``FCurve.update()`` merges them), so the key IS
    # the bound's, put exactly on it: the curve moves by no more than that
    # sub-frame shift.  Each side of a bound: in the gap before B's start,
    # inside B, and in the gap after A's end.
    for near in (39.995, 40.004, 20.003):
        st, sq, obs = fresh(
            {
                "nbA": {
                    -5: 1.0,
                    7: 4.0,
                    16: 2.5,
                    29: 9.0,
                    near: 3.3,
                    44: 1.0,
                    52: 6.0,
                    63: 3.0,
                    71: 8.0,
                    86: 0.0,
                    93: 5.0,
                    104: 2.0,
                }
            }
        )
        nb_fc = fc_of(obs["nbA"])
        for kp in nb_fc.keyframe_points:
            kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
        nb_fc.update()
        for nm, a, b in (("NA", 0, 20), ("NB", 40, 60), ("NC", 80, 100)):
            sq.define_shot(nm, a, b, objects=["nbA"])
        nb_before = {
            sh.name: [nb_fc.evaluate(sh.start + i) for i in range(21)]
            for sh in sq.sorted_shots()
        }
        sq.respace(gap=5.0, start_frame=0)
        nb_fc = fc_of(obs["nbA"])
        nb_worst = max(
            abs(nb_fc.evaluate(sh.start + i) - nb_before[sh.name][i])
            for sh in sq.sorted_shots()
            for i in range(21)
        )
        nb_key = _SSI._fc_key("nbA", nb_fc)
        nb_orphans = [
            t
            for t in sq.ledger.key_times(nb_key)
            if _SSI._key_index_at(nb_fc, t) is None
        ]
        nb_bounds = {b for sh in sq.sorted_shots() for b in (sh.start, sh.end)}
        nb_on = any(
            abs(kp.co[0] - b) < 1e-4 and abs(kp.co[1] - 3.3) < 1e-4
            for kp in nb_fc.keyframe_points
            for b in nb_bounds
        )
        check(
            f"respace: a key {near:g} off a bound is put on it, every shot plays on",
            nb_worst < 0.01 and nb_on and not nb_orphans,
            f"worst={nb_worst:.5f} on_bound={nb_on} orphan claims={nb_orphans} "
            f"keys={[round(kp.co[0], 4) for kp in nb_fc.keyframe_points]}",
        )

    # -- a renamed member is followed, never dropped (mayatk reconcile) --------
    st, sq, obs = fresh({"rnOld": {0: 0, 10: 5}, "rnKeep": {0: 1, 10: 2}})
    rn_shot = sq.define_shot("RN", 0, 10, objects=["rnKeep", "rnOld"])
    rn_key = _SSI._fc_key("rnOld", fc_of(obs["rnOld"]))
    sq.ledger.record_key(rn_key, 10.0, rn_shot.shot_id, "end")
    st.set_object_hidden("rnOld", True)
    obs["rnOld"].name = "rnNew"
    changed = sq.reconcile_all_shots()
    check(
        "reconcile: a renamed member is re-pointed through its action slot",
        changed and sq.shot_by_id(rn_shot.shot_id).objects == ["rnKeep", "rnNew"],
        f"{changed} {sq.shot_by_id(rn_shot.shot_id).objects}",
    )
    # A claim names its CURVE (ledger format 2): the object's rename leaves
    # the key standing, under either name.
    check(
        "reconcile: the claims stay with the curve and the hidden flag follows",
        sq.ledger.key_records(_SSI._fc_key("rnNew", fc_of(obs["rnOld"])))
        == [(10.0, rn_shot.shot_id, "end")]
        and _SSI._fc_key("rnNew", fc_of(obs["rnOld"])) == rn_key
        and st.is_object_hidden("rnNew")
        and not st.is_object_hidden("rnOld"),
        f"{sq.ledger.key_records(rn_key)} hidden={st.hidden_objects}",
    )
    sq.store.update_shot(rn_shot.shot_id, objects=["rnKeep", "rnNew", "rnGone"])
    check(
        "reconcile: a name nothing answers for is kept as stored",
        not sq.reconcile_all_shots()
        and "rnGone" in sq.shot_by_id(rn_shot.shot_id).objects,
    )

    # -- the STORE follows renames, for every name it holds (BTK-SHOTS-1/5) ----
    # The follow covered shot members only, on the panel's rebuild, and read
    # the old name off the action slot alone; the passes that forget a claim
    # whose key no longer resolves dropped every other renamed claim.

    # A renamed NON-member's claimed hold: a fresh sequencer's respace closes
    # the gap and releases it (forgotten, the key stayed CONSTANT for good).
    st, sq, obs = fresh(
        {"rfMem": {0: 0, 10: 5, 25: 5, 30: 9}, "rfOther": {0: 0, 10: 5, 25: 5, 30: 2}}
    )
    sq.define_shot("RA", 0, 10, objects=["rfMem"])
    sq.define_shot("RB", 20, 30, objects=["rfMem"])
    sq._enforce_gap_holds()
    rf_key = _SSI._fc_key("rfOther", fc_of(obs["rfOther"]))
    rf_held = (
        sq.ledger.owns_step(rf_key, 10.0)
        and interp_at(obs["rfOther"], 10) == "CONSTANT"
    )
    obs["rfOther"].name = "rfRenamed"
    ShotSequencer(store=st).respace(gap=0.0, start_frame=0.0)
    check(
        "rename follow: a renamed non-member's hold is released when its gap closes",
        rf_held
        and interp_at(obs["rfOther"], 10) != "CONSTANT"
        and st.edit_ledger.step_count == 0,
        f"held={rf_held} @10={interp_at(obs['rfOther'], 10)} "
        f"{st.edit_ledger.to_dict()}",
    )

    # A renamed BONE: Blender rewrites its channels' paths; the claims follow.
    st, sq, obs = fresh({})
    bpy.ops.object.armature_add()
    rf_rig = bpy.context.active_object
    rf_rig.name = "rfRig"
    rf_bone = rf_rig.data.bones[0].name
    for f, v in ((0, 0.0), (10, 5.0), (25, 5.0), (30, 9.0)):
        rf_rig.pose.bones[rf_bone].location.x = v
        rf_rig.keyframe_insert(f'pose.bones["{rf_bone}"].location', index=0, frame=f)
    sq.define_shot("BA", 0, 10, objects=["rfRig"])
    sq.define_shot("BB", 20, 30, objects=["rfRig"])
    with st.scene_edit("Hold Gaps"):  # as the panel brackets it
        sq._enforce_gap_holds()
    bone_held = sq.ledger.owns_step(
        BlenderShotStore.curve_key("rfRig", f'pose.bones["{rf_bone}"].location', 0),
        10.0,
    )
    rf_rig.data.bones[rf_bone].name = "rfBone_L"
    ShotSequencer(store=st).respace(gap=0.0, start_frame=0.0)
    bone_fc = next(
        fc
        for fc in AnimUtils.get_fcurves(rf_rig)
        if fc.data_path == 'pose.bones["rfBone_L"].location' and fc.array_index == 0
    )
    bone_at_10 = next(
        (
            kp.interpolation
            for kp in bone_fc.keyframe_points
            if abs(kp.co[0] - 10) < 1e-3
        ),
        None,
    )
    check(
        "rename follow: a renamed bone's hold is released when its gap closes",
        bone_held and bone_at_10 != "CONSTANT" and st.edit_ledger.step_count == 0,
        f"held={bone_held} @10={bone_at_10} {st.edit_ledger.to_dict()}",
    )

    # Renamed under a name no action slot records: keyed as rfCube (its slot
    # reads rfCube), renamed rfDoor, built into a shot, renamed rfDoor_main.
    st, sq, obs = fresh({"rfCube": {0: 0, 10: 5}})
    obs["rfCube"].name = "rfDoor"
    with st.scene_edit("Build"):
        door_shot = sq.define_shot("RD", 0, 10, objects=["rfDoor"])
    obs["rfCube"].name = "rfDoor_main"
    check(
        "rename follow: a rename no action slot records is followed",
        ShotSequencer(store=st).reconcile_all_shots()
        and st.shot_by_id(door_shot.shot_id).objects == ["rfDoor_main"],
        f"{st.shot_by_id(door_shot.shot_id).objects}",
    )

    # A Shift+D copy's action keeps the source's slot name: two objects
    # answered for it, and the slot rule could not say which was renamed.
    st, sq, obs = fresh({"rfSrc": {0: 0, 10: 5}})
    with st.scene_edit("Build"):
        src_shot = sq.define_shot("RS", 0, 10, objects=["rfSrc"])
    bpy.ops.object.select_all(action="DESELECT")
    obs["rfSrc"].select_set(True)
    bpy.context.view_layer.objects.active = obs["rfSrc"]
    bpy.ops.object.duplicate()
    rf_copy = bpy.context.active_object.name
    obs["rfSrc"].name = "rfSrcRenamed"
    check(
        "rename follow: a Shift+D copy sharing the slot's name never blocks it",
        ShotSequencer(store=st).reconcile_all_shots()
        and st.shot_by_id(src_shot.shot_id).objects == ["rfSrcRenamed"]
        and rf_copy not in ("rfSrc", "rfSrcRenamed"),
        f"{st.shot_by_id(src_shot.shot_id).objects} copy={rf_copy}",
    )

    # An object name holding "|" (BTK-SHOTS-11): a ledger key is split from
    # the right (``<object>|<data_path>|<index>``), never at the first bar.
    st, sq, obs = fresh({"rf|Pipe": {0: 0, 10: 5}})
    pipe_shot = sq.define_shot("RP", 0, 10, objects=["rf|Pipe"])
    pipe_fc = fc_of(obs["rf|Pipe"])
    sq.ledger.record_key(
        _SSI._fc_key("rf|Pipe", pipe_fc), 10.0, pipe_shot.shot_id, "end"
    )
    obs["rf|Pipe"].name = "rf|PipeRenamed"
    sq.reconcile_all_shots()
    check(
        "rename follow: an object name holding '|' re-keys its claims too",
        st.shot_by_id(pipe_shot.shot_id).objects == ["rf|PipeRenamed"]
        and sq.ledger.key_records(_SSI._fc_key("rf|PipeRenamed", pipe_fc))
        == [(10.0, pipe_shot.shot_id, "end")],
        f"{st.shot_by_id(pipe_shot.shot_id).objects} {sq.ledger.to_dict()}",
    )

    # An undo's restore point brings back a name from renames ago: every name
    # the object bore this session leads to it, not only the last one seen.
    st, sq, obs = fresh({"rfZero": {0: 0, 10: 5}})
    obs["rfZero"].name = "rfFirst"
    hist_shot = sq.define_shot("RH", 0, 10, objects=["rfFirst"])
    with st.scene_edit("Edit"):  # its restore point names rfFirst
        st.update_shot(hist_shot.shot_id, end=12.0)
    for later in ("rfSecond", "rfThird"):
        obs["rfZero"].name = later
        sq.reconcile_all_shots()
    st.restore_boundary_snapshot()
    hist_restored = list(st.shot_by_id(hist_shot.shot_id).objects)
    check(
        "rename follow: a name a restore point brings back is followed again",
        hist_restored == ["rfFirst"]
        and sq.reconcile_all_shots()
        and st.shot_by_id(hist_shot.shot_id).objects == ["rfThird"],
        f"restored={hist_restored} now={st.shot_by_id(hist_shot.shot_id).objects}",
    )

    # -- undo pairing: only OUR step consumes a restore point (2026-10-04) --------
    # The store follows Blender's undo by itself (``BlenderShotStore.set_active``);
    # the panel's Undo / Redo over it -- its one-click New Shot and shot change
    # landing the playhead too -- is
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``,
    # and its host half, one step of Blender's undo, ``SequencerHost.step_undo``.
    st, sq, obs = fresh({"unA": {0: 0, 10: 5}})
    un_shot = sq.define_shot("U", 0, 10, objects=["unA"])
    bpy.ops.ed.undo_push(message="baseline")
    serial0 = BlenderShotStore.edit_serial()
    with st.scene_edit("Probe Edit"):
        st.update_shot(un_shot.shot_id, end=20.0)
    tag = st.peek_boundary_tag()
    check(
        "scene_edit: stamps a fresh serial inside its step and tags the point with it",
        isinstance(tag, tuple)
        and tag[0] is True
        and tag[1] == BlenderShotStore.edit_serial()
        and tag[1] != serial0,
        f"tag={tag} serial={BlenderShotStore.edit_serial()} before={serial0}",
    )
    depth = len(st._boundary_undo)
    with st.scene_edit("No-op") as edit:
        edit.cancel()
    check(
        "scene_edit: a cancelled edit leaves no restore point and no serial stamp",
        len(st._boundary_undo) == depth and BlenderShotStore.edit_serial() == tag[1],
        f"depth {depth}->{len(st._boundary_undo)}",
    )

    # An unrelated step on top: a native undo must take only that step.
    obs["unA"].location.z = 3.0
    bpy.ops.ed.undo_push(message="unrelated")
    bpy.ops.ed.undo()
    st_u = sq.shot_by_id(un_shot.shot_id)
    check(
        "native undo of an UNRELATED step leaves the shot bounds alone",
        st_u.end == 20.0 and st.has_boundary_snapshot(),
        f"end={st_u.end}",
    )
    bpy.ops.ed.undo()
    check(
        "native undo of OUR step restores the bounds",
        sq.shot_by_id(un_shot.shot_id).end == 10.0,
        f"end={sq.shot_by_id(un_shot.shot_id).end}",
    )
    bpy.ops.ed.redo()
    check(
        "native redo of OUR step re-applies the bounds",
        sq.shot_by_id(un_shot.shot_id).end == 20.0,
        f"end={sq.shot_by_id(un_shot.shot_id).end}",
    )

    # The panel's undo (the host's step) with an unrelated step on top: the
    # same contract.  (Memfile undo replaced the object: fetch it again.)
    bpy.data.objects["unA"].location.z = 7.0
    bpy.ops.ed.undo_push(message="unrelated 2")
    SequencerHost.step_undo(st)
    check(
        "step_undo: an unrelated step on top is undone alone, bounds kept",
        sq.shot_by_id(un_shot.shot_id).end == 20.0,
        f"end={sq.shot_by_id(un_shot.shot_id).end}",
    )
    SequencerHost.step_undo(st)
    check(
        "step_undo: then our step, bounds restored",
        sq.shot_by_id(un_shot.shot_id).end == 10.0,
        f"end={sq.shot_by_id(un_shot.shot_id).end}",
    )

    # -- an Undo History jump restores EVERY edit it crossed (BTK-SHOTS-6) -----
    # Blender fires undo_pre / undo_post ONCE for a jump over several steps:
    # restoring only the newest point left the earlier edits' bounds behind,
    # and a redo jump re-applied nothing.
    bpy.ops.wm.read_factory_settings(use_empty=True)  # an empty undo history
    BlenderShotStore.clear_active()
    st = BlenderShotStore()
    BlenderShotStore.set_active(st)
    # No operator, so no undo step: the history holds exactly what is pushed.
    jump_obj = bpy.data.objects.new("jumpA", None)
    bpy.context.scene.collection.objects.link(jump_obj)
    sq = ShotSequencer(store=st)
    jump_shot = sq.define_shot("J", 0, 10, objects=["jumpA"])
    bpy.ops.ed.undo_push(message="baseline")  # history item 0
    for jump_end in (20.0, 30.0):  # items 1 and 2
        with st.scene_edit("Jump Edit"):
            st.update_shot(jump_shot.shot_id, end=jump_end)
    bpy.ops.ed.undo_history(item=0)
    jump_back = sq.shot_by_id(jump_shot.shot_id).end
    bpy.ops.ed.undo_history(item=2)
    jump_fwd = sq.shot_by_id(jump_shot.shot_id).end
    check(
        "undo history: a jump over two edits restores both, and redoes both",
        jump_back == 10.0 and jump_fwd == 30.0,
        f"back={jump_back} forward={jump_fwd}",
    )

    # -- the STORE follows Blender's undo, no Shot Sequencer open (BACKLOG -----
    # 2026-10-05 / 2026-10-07): only the panel's undo_post handler applied the
    # restore points, so an edit made with no panel this session -- a Shots
    # window resize, a key edit's claims -- was undone in its keys and kept in
    # its bounds and claims (measured: claims stayed [9] after an Align undo).
    st, sq, obs = fresh({"nsA": {0: 0, 10: 5, 20: 9}})
    ns_shot = sq.define_shot("NS", 0, 30, objects=["nsA"])
    ns_key = BlenderShotStore.curve_key("nsA", "location", 0)
    st.edit_ledger.record_authored(ns_key, 10.0, 0, "fade_in", "nsA")
    bpy.ops.ed.undo_push(message="baseline")
    with st.scene_edit("Shots Window End"):
        st.update_shot(ns_shot.shot_id, end=40.0)
        st.edit_ledger.remap(ns_key, [(10.0, 15.0)])
    edited = (sq.shot_by_id(ns_shot.shot_id).end, st.edit_ledger.owns_any(ns_key, 15.0))
    bpy.ops.ed.undo()
    undone = (
        sq.shot_by_id(ns_shot.shot_id).end,
        st.edit_ledger.owns_any(ns_key, 10.0),
        st.edit_ledger.owns_any(ns_key, 15.0),
    )
    bpy.ops.ed.redo()
    redone = sq.shot_by_id(ns_shot.shot_id).end
    check(
        "no panel: Blender's own undo restores the bounds and claims, redo re-applies",
        edited == (40.0, True) and undone == (30.0, True, False) and redone == 40.0,
        f"edited={edited} undone={undone} redone={redone}",
    )
    names = {"undo_pre", "redo_pre", "undo_post", "redo_post"}
    installed = {
        n
        for n, fn in BlenderShotStore.__dict__.get("_native_undo_handlers", ())
        if fn in getattr(bpy.app.handlers, n)
    }
    BlenderShotStore.clear_active()
    left = {
        n
        for n, fn in BlenderShotStore.__dict__.get("_native_undo_handlers", ())
        if fn in getattr(bpy.app.handlers, n)
    }
    check(
        "the store's undo handlers: set_active attaches all four, clear_active detaches",
        installed == names and not left,
        f"installed={sorted(installed)} left={sorted(left)}",
    )

    # (A key edit that raises keeping its restore point is the panel's key
    # bracket:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # -- the scene watcher: a rename / a strip edit is a scene change, never a key edit
    # (BTK-SHOTS-4) The panel's keying epilogue adds keyed objects to the active
    # shot, falling back to the SELECTION when nothing was keyed -- which a
    # rename and a Sequencer strip edit never are.  The host's watcher
    # (``SequencerHost.watch_scene``) reports both as a scene change; the
    # panel's answer to one (a plain rebuild) is tentacle's panel check.
    import types

    from blendertk.audio_utils._audio_utils import AudioUtils as _AU

    def _watch():
        """A scene watcher whose reports are recorded: ``(watcher, record)``."""
        rec = {"keys": [], "scene": 0}

        def _scene_changed():
            rec["scene"] += 1

        watcher = SequencerHost.watch_scene(
            busy=lambda: False,
            on_keys_edited=lambda items: rec["keys"].append(sorted(items)),
            on_time_changed=lambda _frame: None,
            on_scene_changed=_scene_changed,
        )
        return watcher, rec

    st, sq, obs = fresh({"reMem": {0: 0, 10: 5}})
    re_watch, re_rec = _watch()
    try:
        obs["reMem"].name = "reMemRenamed"
        re_watch._on_rename()  # what the rename subscription runs
    finally:
        re_watch.remove()
    check(
        "watcher: a rename is a scene change, never a key edit",
        re_rec["scene"] == 1 and re_rec["keys"] == [],
        f"{re_rec}",
    )

    # The Sequencer's strips: an edit there is seen (and the baseline is not).
    st, sq, obs = fresh({"reMem": {0: 0, 10: 5}})
    with ptk.TempArtifacts(prefix="btk_seq_strip_") as strip_tmp:
        strip_wav = str(strip_tmp.path(".wav"))
        with wave.open(strip_wav, "wb") as fh:  # a tenth of a second of silence
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00" * 4410)
        strip_watch, strip_rec = _watch()  # the baseline is taken as it attaches
        try:
            strip_baseline = strip_watch._sound_strips_changed()
            _AU.add_clip(strip_wav, frame_start=5, name="reStrip")
            fake_dg = types.SimpleNamespace(
                updates=[types.SimpleNamespace(id=bpy.context.scene)]
            )
            strip_watch._on_depsgraph_update(bpy.context.scene, fake_dg)
            strip_again = strip_watch._sound_strips_changed()
        finally:
            strip_watch.remove()
            _AU.remove_all_clips()
    check(
        "strip watch: an external strip edit is one change, the baseline none",
        strip_baseline is False and strip_again is False,
        f"baseline={strip_baseline} again={strip_again}",
    )
    check(
        "strip edit: a scene change, never a key edit",
        strip_rec["scene"] == 1 and strip_rec["keys"] == [],
        f"{strip_rec}",
    )

    # Nothing banked (or a bulk edit past the panel's cap, handed over as
    # nothing): the new members fall back to the selection.  A member is never
    # new.  (The cap itself is the panel's.)
    st, sq, obs = fresh({"reMem": {0: 0, 10: 5}, "reSel": {0: 1, 10: 3}})
    nk_shot = sq.define_shot("RE", 0, 10, objects=["reMem"])
    for ob in bpy.context.scene.objects:
        ob.select_set(ob.name == "reSel")
    nk_selected = SequencerHost.newly_keyed_members(set(), nk_shot, sq)
    nk_banked = SequencerHost.newly_keyed_members({"reMem", "reSel"}, nk_shot, sq)
    check(
        "newly_keyed_members: nothing banked falls back to the selection",
        nk_selected == ["reSel"] and nk_banked == ["reSel"],
        f"selected={nk_selected} banked={nk_banked}",
    )

    # -- the sequencer's own members are no hand edit (BACKLOG 2026-10-10) -----
    # Filling a shot that names no object the scene holds, when it is shown,
    # and the keying epilogue's additions read as a hand edit to the Shot
    # Manifest, so every later Build kept that shot, doc changes and all.  Both
    # tag what they add (ShotStore.SYSTEM_MEMBERS); a hand's add still counts.
    from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
        BlenderShotManifest,
    )
    from pythontk.core_utils.engines.shots.manifest.manifest_model import (
        BuilderObject,
        BuilderStep,
    )

    sm_st, sm_sq, _sm_obs = fresh({"smMover": {5: 0, 20: 5}, "smKeyed": {5: 1, 20: 3}})
    sm_step = BuilderStep("A01", "A", "SEC A", "", [BuilderObject("smGhost")])
    BlenderShotManifest(sm_st).update([sm_step], initial_shot_length=30)
    sm_shot = sm_st.shot_by_name("A01")
    sm_edited = lambda: BlenderShotManifest(sm_st).edited_by_hand(sm_shot)  # noqa: E731
    sm_built = sm_edited()
    sm_segments = sm_sq.collect_object_segments(sm_shot.shot_id)
    sm_members = list(sm_st.shot_by_id(sm_shot.shot_id).objects)
    sm_filled = sm_edited()
    sm_step.objects.append(BuilderObject("smDocAdded"))  # a doc change
    sm_action = BlenderShotManifest(sm_st).update([sm_step], remove_missing=False)
    check(
        "empty-shot fill: no hand edit, the doc's name stays and its changes apply",
        not sm_built
        and sm_segments
        and "smGhost" in sm_members
        and "smMover" in sm_members
        and not sm_filled
        and sm_action == {"A01": "patched"},
        f"built={sm_built} filled={sm_filled} members={sm_members} {sm_action}",
    )

    # (The keying epilogue's own additions, tagged as no hand edit, are the
    # panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # -- an export's own key edits are reported to no one (2026-10-11) ---------
    # Bug (measured 2026-10-05 on a Unity send): the write's bake and proxy
    # Actions banked every animated object, and the keying epilogue added a
    # non-member keyed in the active shot's frames to its members -- after the
    # scene had been put back.  Both producers now hold
    # ``ShotStore.transient_edit`` and the watcher reports no key edit inside one.
    sys.path.insert(0, os.path.join(MONO, "unitytk"))  # the Unity send's deliverer
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge

    for tr_via in ("export", "send"):
        tr_st, sq_tr, obs_tr = fresh({"trMem": {0: 0, 20: 5}, "trOther": {0: 1, 20: 3}})
        tr_shot = sq_tr.define_shot("TR", 0, 20, objects=["trMem"])
        tr_st.set_active_shot(tr_shot.shot_id)
        for ob in bpy.context.scene.objects:
            ob.select_set(False)
        tr_watch, tr_rec = _watch()
        try:
            with ptk.TempArtifacts(prefix="btk_transient_") as tr_tmp:
                if tr_via == "export":
                    tr_ok = SceneExporter().perform_export(
                        export_dir=str(tr_tmp.dir_path()),
                        objects=[obs_tr["trMem"]],
                        output_name="transient",
                        export_visible=False,
                    )
                else:
                    tr_bridge = UnityBridge(project_path=str(tr_tmp.dir_path()))
                    tr_payload = tr_bridge._produce(  # the defaults: it bakes
                        [obs_tr["trMem"]], ptk.HandoffRequest(mode="send_to")
                    )
                    tr_ok = bool(tr_payload and tr_payload.primary)
                    if tr_ok and os.path.isfile(tr_payload.primary):
                        os.remove(tr_payload.primary)
            bpy.context.view_layer.update()  # the next redraw's evaluation
        finally:
            tr_watch.remove()
        tr_banked = sorted({name for batch in tr_rec["keys"] for name in batch})
        tr_now = sq_tr.shot_by_id(tr_shot.shot_id)
        check(
            f"{'an export' if tr_via == 'export' else 'a send'}'s own key edits "
            "are reported to no one",
            tr_ok
            and tr_banked == []
            and (tr_now.objects, tr_now.start, tr_now.end) == (["trMem"], 0.0, 20.0),
            f"ok={tr_ok} banked={tr_banked} members={tr_now.objects} "
            f"range={tr_now.start}-{tr_now.end}",
        )

    # -- the view's playback range is the PREVIEW range, never the render range --
    # (Which range a view asks for -- the shot, or the visible shots -- is the
    # panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)
    fresh({})
    pr_scene = bpy.context.scene
    pr_render = (pr_scene.frame_start, pr_scene.frame_end)
    ranges = {}
    for label, (lo, hi) in (("a shot", (10, 40)), ("the visible shots", (10, 90))):
        SequencerHost.set_playback_range(lo, hi)
        ranges[label] = (
            pr_scene.use_preview_range,
            pr_scene.frame_preview_start,
            pr_scene.frame_preview_end,
        )
    check(
        "set_playback_range: the preview range, turned on; the render range untouched",
        ranges == {"a shot": (True, 10, 40), "the visible shots": (True, 10, 90)}
        and (pr_scene.frame_start, pr_scene.frame_end) == pr_render,
        f"{ranges} render={(pr_scene.frame_start, pr_scene.frame_end)} was {pr_render}",
    )
    check(
        "playback_range reads the preview range back",
        SequencerHost.playback_range() == (10.0, 90.0),
        f"{SequencerHost.playback_range()}",
    )

    # -- Compact's object-row ticks: real keys, never the system's samples ----
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector,
    )

    _sm_st, sm_sq, sm_obs = fresh({"sumCube": {1: 1, 20: 20, 50: 50}})
    sm_ob = sm_obs["sumCube"]
    for _f in (1, 50):
        sm_ob.location.y = _f
        sm_ob.keyframe_insert(data_path="location", index=1, frame=_f)
    sm_fcs = {
        fc.array_index: fc
        for fc in AnimUtils.get_fcurves(sm_ob)
        if fc.data_path == "location"
    }
    sm_seg = {"curves": [sm_fcs[0], sm_fcs[1]], "keyframes": [1.0, 20.0, 50.0]}

    def sm_summary():
        return SegmentCollector.summary_times(
            [sm_seg],
            lambda fc: sm_sq.ledger.key_times(_SSI._fc_key("sumCube", fc)),
        )

    sm_runs = [sm_summary()]
    sm_sq.ledger.record_key(_SSI._fc_key("sumCube", sm_fcs[0]), 20.0, 0, "end")
    sm_runs.append(sm_summary())
    sm_sq.ledger.record_key(_SSI._fc_key("sumCube", sm_fcs[0]), 50.0, 0, "end")
    sm_runs.append(sm_summary())
    sm_sq.ledger.record_key(_SSI._fc_key("sumCube", sm_fcs[1]), 50.0, 0, "end")
    sm_runs.append(sm_summary())
    check(
        "summary ticks: unclaimed all tick; a lone-curve claim drops; a claim "
        "beside another channel's key stays; claimed on every keyed curve drops",
        sm_runs
        == [
            [1.0, 20.0, 50.0],
            [1.0, 50.0],
            [1.0, 50.0],
            [1.0],
        ],
        f"{sm_runs}",
    )

    # ---- linked duplicates share ONE curve (BACKLOG 2026-10-05) ---------------
    # A claim was keyed by OWNER (``object|data_path|index``) while the curve is
    # its action slot's: with LinkA keyed 10/20 and an Alt+D LinkB sharing its
    # action, every op walking owner by owner moved, scaled or pinned the
    # shared curve once per sharer -- LinkB a member or not (measured 7 of 15
    # ops: a slide landed [20, 30] for [15, 25], set_shot_duration [10, 12.5]
    # for [10, 15]).  Ledger format 2 keys a claim by the curve's identity.
    # Each op runs on a control (LinkA alone) and with LinkB sharing.
    def ld_scene(share, member):
        st, sq, obs = fresh({"ldA": {10: 0.0, 20: 5.0}, "ldOther": {30: 1.0, 40: 7.0}})
        if share:
            ld_b = obs["ldA"].copy()  # Alt+D: a new object, the action shared
            ld_b.name = "ldB"
            bpy.context.scene.collection.objects.link(ld_b)
        ld_a = sq.define_shot(
            "LA", 10, 20, objects=["ldA", "ldB"] if member else ["ldA"]
        )
        ld_o = sq.define_shot("LB", 30, 40, objects=["ldOther"])
        return st, sq, ld_a, ld_o

    def ld_played(sq):
        fc = fc_of(bpy.data.objects["ldA"])
        claims = sq.ledger.key_records(_SSI._fc_key("ldA", fc))
        return (
            [(round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc.keyframe_points],
            sorted((round(t, 3), edge) for t, _owner, edge in claims),
        )

    def ld_sequences(sq, shot_id):
        return [
            s
            for s in sq.collect_shot_sequences(shot_id, include_audio=False)
            if s.get("obj") in ("ldA", "ldB")
        ]

    ld_ops = {
        "move_shot": lambda sq, a, b: sq.move_shot(a.shot_id, 15),
        "slide_shot": lambda sq, a, b: sq.slide_shot(a.shot_id, 15),
        "ripple_downstream": lambda sq, a, b: sq.ripple_downstream(a.shot_id, 10, 5),
        "set_shot_duration": lambda sq, a, b: sq.set_shot_duration(a.shot_id, 5),
        "resize_shot": lambda sq, a, b: sq.resize_shot(a.shot_id, 10, 15),
        "add_shot_space": lambda sq, a, b: sq.add_shot_space(a.shot_id, 5, "leading"),
        "split, tail slid": lambda sq, a, b: sq.slide_shot(
            sq.split_shot(a.shot_id, 15).shot_id, 19
        ),
        "respace": lambda sq, a, b: sq.respace(gap=2, start_frame=10),
        "Move to Shot": lambda sq, a, b: sq.move_sequences_to_shot(
            ld_sequences(sq, a.shot_id), b.shot_id
        ),
        "delete_shot": lambda sq, a, b: sq.delete_shot(a.shot_id),
    }
    ld_diff = []
    for ld_label, ld_op in ld_ops.items():
        ld_runs = []
        for share, member in ((False, False), (True, True), (True, False)):
            st, sq, ld_a, ld_o = ld_scene(share, member)
            sq._enforce_gap_holds()
            ld_op(sq, ld_a, ld_o)
            ld_runs.append(ld_played(sq))
        if not ld_runs[0] == ld_runs[1] == ld_runs[2]:
            ld_diff.append(f"{ld_label}: {ld_runs}")
    check(
        "linked duplicates: every op plays the shared curve as it plays one owner's",
        not ld_diff,
        " | ".join(ld_diff),
    )

    # One curve, one key: a claim rides a key moved through EITHER sharer, and
    # an edit through either releases it (it is one key the two play).
    st, sq, ld_a, ld_o = ld_scene(True, True)
    ld_fa = fc_of(bpy.data.objects["ldA"])
    ld_fb = fc_of(bpy.data.objects["ldB"])
    ld_key = _SSI._fc_key("ldA", ld_fa)
    sq.ledger.record_key(ld_key, 20.0, ld_a.shot_id, "end")
    sq.move_attribute_keys("ldB", None, 3.0, times=[20.0])
    ld_moved = sq.ledger.key_times(_SSI._fc_key("ldB", ld_fb))
    st.release_replaced(["ldB"])(ld_fb, [23.0])
    check(
        "linked duplicates: one key per curve; a claim rides and releases through "
        "either sharer",
        _SSI._fc_key("ldB", ld_fb) == ld_key
        and ld_key.startswith(f"{ld_fa.id_data.name}|")
        and ld_moved == [23.0]
        and not sq.ledger.key_times(ld_key),
        f"keys {ld_key} / {_SSI._fc_key('ldB', ld_fb)} moved={ld_moved} "
        f"{sq.ledger.to_dict()}",
    )

    # (A key drag and a key edit over both sharers' rows -- one curve, moved and
    # edited once -- are the panel's:
    # tentacle's ``test/blender/shot_sequencer_panel_check.py``.)

    # A format-1 ledger (claims by OWNER) is re-keyed on load: each claim on
    # its curve's identity, a linked pair's merged onto the curve they share,
    # one naming nothing kept as stored -- and it plays on as before: the
    # migrated hold is still the system's to release when its gap closes.
    st, sq, obs = fresh({"v1A": {1: 0.0, 10: 4.0, 20: 9.0}, "v1B": {40: 9.0, 50: 3.0}})
    v1_a = sq.define_shot("V1A", 1, 20, objects=["v1A"])
    v1_b = sq.define_shot("V1B", 40, 50, objects=["v1B"])
    sq._enforce_gap_holds()  # v1A@20 CONSTANT
    v1_link = obs["v1A"].copy()
    v1_link.name = "v1Link"
    bpy.context.scene.collection.objects.link(v1_link)
    v1_data = st.to_dict()
    v1_data["edit_ledger"] = {  # what a format-1 save wrote: no version
        "steps": {"v1A|location|0": [[20.0, "BEZIER", "BEZIER"]]},
        "keys": {
            "v1A|location|0": [[10.0, v1_a.shot_id, "end"]],
            "v1Link|location|0": [[1.0, v1_a.shot_id, "start"]],
        },
        "authored": {"v1Gone|location|0": [[5.0, 0, "fade_in", "v1Gone"]]},
    }
    v1_store = BlenderShotStore.from_dict(v1_data)
    v1_led = v1_store.edit_ledger
    v1_key = _SSI._fc_key("v1A", fc_of(obs["v1A"]))
    check(
        "format 1: claims load on their curves' identities, a linked pair's merged",
        v1_led.step_times(v1_key) == [20.0]
        and v1_led.key_times(v1_key) == [1.0, 10.0]
        and v1_led.authored() == [("v1Gone|location|0", 5.0)]
        and v1_led.curves == {v1_key, "v1Gone|location|0"}
        and v1_led.source_version == v1_led.FORMAT_VERSION,
        str(v1_led.to_dict()),
    )
    BlenderShotStore.set_active(v1_store)
    v1_store.update_shot(v1_b.shot_id, start=20.0)  # the gap closes
    ShotSequencer(store=v1_store)._enforce_gap_holds()
    check(
        "format 1: a migrated hold is still the system's, released when its gap closes",
        interp_at(obs["v1A"], 20) != "CONSTANT" and v1_led.step_count == 0,
        f"@20={interp_at(obs['v1A'], 20)} {v1_led.to_dict()}",
    )

    # An ACTION renamed since a claim was made: the claim names the action
    # now.  One the store never saw is read off Blender's default spelling
    # (``<object>Action``, its slot named after the object); one it saw, by
    # session_uid, as an object's rename is.
    st, sq, obs = fresh({"arA": {0: 0.0, 10: 5.0}})
    ar_shot = sq.define_shot("AR", 0, 10, objects=["arA"])
    sq.ledger.record_key(
        _SSI._fc_key("arA", fc_of(obs["arA"])), 10.0, ar_shot.shot_id, "end"
    )
    ar_follows = []
    for ar_name in ("arWalk", "arRun"):
        obs["arA"].animation_data.action.name = ar_name
        ar_follows.append(st.follow_renames())
    ar_key = _SSI._fc_key("arA", fc_of(obs["arA"]))
    check(
        "rename follow: a renamed action's claims follow it, unseen or seen",
        ar_follows == [True, True]
        and ar_key.startswith("arRun|")
        and sq.ledger.key_records(ar_key) == [(10.0, ar_shot.shot_id, "end")]
        and sq.ledger.curves == {ar_key},
        f"followed={ar_follows} {sq.ledger.to_dict()}",
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
