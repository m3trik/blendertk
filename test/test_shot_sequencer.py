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
            import logging

            self._widget = widget
            self.sequencer = sequencer
            self._segment_cache = {}
            self._sub_row_cache = {}
            self._syncing = False
            self.logger = logging.getLogger("test.clip_motion_host")

        def _get_sequencer_widget(self):
            return self._widget

        def _save_shot_state(self):
            pass

        def _discard_shot_state(self):
            pass

        def _sync_to_widget(self, **kw):
            pass

        def _sync_combobox(self):
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

    # ---- on_keys_batch_moved: one drag over TWO clips is one commit ---------
    # uitk emits keys_batch_moved for a multi-clip key drag; nothing else in
    # this suite exercises that entry point, and an unhandled signal would
    # make the drag silently do nothing.
    class _MultiWidget:
        def __init__(self, clips):
            self._clips = clips

        def get_clip(self, cid):
            return self._clips.get(cid)

    batch_objs = []
    for name, frames in (("BatchA", (10, 20)), ("BatchB", (10, 20))):
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        o.name = name
        for i, f in enumerate(frames):
            o.location = (float(i), 0.0, 0.0)
            o.keyframe_insert(data_path="location", index=0, frame=f)
        batch_objs.append(o)

    batch_host = _KeysHost(
        _MultiWidget(
            {
                1: _FakeClip(
                    {"obj": "BatchA", "attr_name": "translateX", "shot_id": None}
                ),
                2: _FakeClip(
                    {"obj": "BatchB", "attr_name": "translateX", "shot_id": None}
                ),
            }
        )
    )
    batch_host.on_keys_batch_moved([(1, [(20.0, 25.0)]), (2, [(20.0, 25.0)])])

    def _times(obj):
        fc = next(
            fc
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            if fc.data_path == "location" and fc.array_index == 0
        )
        return sorted(round(kp.co[0], 3) for kp in fc.keyframe_points)

    check(
        "on_keys_batch_moved: every clip in the gesture is committed",
        _times(batch_objs[0]) == [10.0, 25.0] and _times(batch_objs[1]) == [10.0, 25.0],
        f"{_times(batch_objs[0])} / {_times(batch_objs[1])}",
    )

    # ---- a drag onto a KEYED neighbour boundary moves the neighbour whole ----
    # Mirror of mayatk's TestADragOntoAKeyedNeighbourLeavesItWhole (2026-09-22):
    # the key handlers ripple BEFORE the keys land, but the carried window
    # treated the sample on the new bound as the dragged shot's, stranding the
    # neighbour's own pose in the gap; the clip paths landed first instead and
    # stacked the landed key on that pose.
    def _seam_scene(name, shots, keys):
        """A cube keyed on location.x, in every one of *shots*: (obj, store, seq)."""
        BlenderShotStore.clear_active()
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        o.name = name
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
            for fc in BlenderShotStore.iter_action_fcurves(o)
            if fc.data_path == "location" and fc.array_index == 0
        )

    def _move_clip(data, sq, new_start):
        host = _KeysHost(_FakeWidget(_FakeClip(data)), sq)
        host._shifted_out_keys = {}
        host._audio_segments_cache = None
        host.on_clip_moved(1, new_start)

    def _seam_case(name, shots, keys, gesture):
        o, st, sq = _seam_scene(name, shots, keys)
        ids = [st.shot_by_name(sname).shot_id for sname, _s, _e in shots]
        kind, idx, payload = gesture
        data = {"obj": o.name, "shot_id": ids[idx]}
        if kind == "keys":
            data["attr_name"] = "translateX"
            _KeysHost(_FakeWidget(_FakeClip(data)), sq).on_keys_batch_moved(
                [(1, payload)]
            )
        else:
            clip_data, new_start = payload
            _move_clip(dict(data, **clip_data), sq, new_start)
        got = [
            (round(kp.co[0], 3), round(kp.co[1], 3)) for kp in _loc_x(o).keyframe_points
        ]
        return [(s.start, s.end) for s in st.sorted_shots()], got

    ab = [("A", 0, 50), ("B", 65, 100)]
    bounds, got = _seam_case(
        "SeamEq", ab, ((10, 0), (40, 1), (65, 1), (80, 2)), ("keys", 0, [(40.0, 65.0)])
    )
    check(
        "keyed neighbour: a key onto its start moves it whole",
        bounds == [(0, 65), (80, 115)]
        and got == [(10.0, 0.0), (65.0, 1.0), (80.0, 1.0), (95.0, 2.0)],
        f"{bounds} {got}",
    )
    _b, got = _seam_case(
        "SeamNe", ab, ((10, 0), (40, 1), (65, 7), (80, 2)), ("keys", 0, [(40.0, 65.0)])
    )
    check(
        "keyed neighbour: an unequal opening pose stays on its new start",
        got == [(10.0, 0.0), (65.0, 1.0), (80.0, 7.0), (95.0, 2.0)],
        f"{got}",
    )
    bounds, got = _seam_case(
        "SeamUp",
        [("P", 0, 35), ("A", 50, 100)],
        ((5, 0), (35, 4), (60, 2), (90, 3)),
        ("keys", 1, [(60.0, 35.0)]),
    )
    check(
        "keyed neighbour: a key onto the previous shot's end moves it whole",
        bounds == [(-15, 20), (35, 100)]
        and got == [(-10.0, 0.0), (20.0, 4.0), (35.0, 2.0), (90.0, 3.0)],
        f"{bounds} {got}",
    )
    _b, got = _seam_case(
        "SeamIn",
        ab,
        ((10, 0), (40, 1), (65, 1), (70, 2), (80, 3)),
        ("keys", 0, [(40.0, 70.0)]),
    )
    check(
        "keyed neighbour: a key dragged inside it moves it whole",
        got == [(10.0, 0.0), (70.0, 1.0), (85.0, 1.0), (90.0, 2.0), (100.0, 3.0)],
        f"{got}",
    )
    _b, got = _seam_case(
        "SeamClip",
        ab,
        ((10, 0), (30, 5), (40, 1), (65, 7), (80, 2)),
        (
            "clip",
            0,
            ({"attr_name": "translateX", "orig_start": 30.0, "orig_end": 40.0}, 55.0),
        ),
    )
    check(
        "keyed neighbour: a sub-row clip lands after the neighbour moved",
        got == [(10.0, 0.0), (55.0, 5.0), (65.0, 1.0), (80.0, 7.0), (95.0, 2.0)],
        f"{got}",
    )
    _b, got = _seam_case(
        "SeamStep",
        ab,
        ((10, 0), (40, 1), (65, 7), (80, 2)),
        ("clip", 0, ({"orig_start": 40.0, "orig_end": 40.0, "is_stepped": True}, 65.0)),
    )
    check(
        "keyed neighbour: a stepped clip lands after the neighbour moved",
        got == [(10.0, 0.0), (65.0, 1.0), (80.0, 7.0), (95.0, 2.0)],
        f"{got}",
    )
    # A run that outlasts its shot: made room for before it moved, its part in
    # the neighbour's envelope rode the ripple (+22) instead of the drag (+2).
    bounds, got = _seam_case(
        "SeamSpan",
        ab,
        ((10, 0), (45, 1), (70, 2), (85, 3)),
        (
            "clip",
            0,
            ({"attr_name": "translateX", "orig_start": 45.0, "orig_end": 70.0}, 47.0),
        ),
    )
    check(
        "keyed neighbour: a sub-row clip reaching into the next shot moves as one",
        bounds == [(0, 72), (87, 122)]
        and got == [(10.0, 0.0), (47.0, 1.0), (72.0, 2.0), (107.0, 3.0)],
        f"{bounds} {got}",
    )
    # A claim is a (curve, time) pair, so it moves with its key -- as the key
    # drag's always did; the sub-row move shifted the keys alone.
    from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
        _ShotSequencerInternal,
    )

    co, cst, csq = _seam_scene("SeamClaim", ab, ((10, 0), (30, 5), (40, 1), (80, 2)))
    ckey = _ShotSequencerInternal._fc_key(co.name, _loc_x(co))
    csq.ledger.record_key(ckey, 40.0)
    _move_clip(
        {
            "obj": co.name,
            "shot_id": cst.shot_by_name("A").shot_id,
            "attr_name": "translateX",
            "orig_start": 30.0,
            "orig_end": 40.0,
        },
        csq,
        32.0,
    )
    ctimes = [round(kp.co[0], 3) for kp in _loc_x(co).keyframe_points]
    check(
        "keyed neighbour: a sub-row clip carries its keys' claims",
        ctimes == [10.0, 32.0, 42.0, 80.0] and csq.ledger.key_times(ckey) == [42.0],
        f"{ctimes} claims={csq.ledger.key_times(ckey)}",
    )
    # Dragged back past its shot's start, a clip grows the head, which ripples
    # the shots before it back as far -- onto the lifted clip, on a long drag.
    bounds, got = _seam_case(
        "SeamBack",
        [("A", 0, 50), ("B", 60, 100)],
        ((10, 0), (40, 1), (70, 5), (90, 6)),
        (
            "clip",
            1,
            ({"attr_name": "translateX", "orig_start": 70.0, "orig_end": 90.0}, -980.0),
        ),
    )
    check(
        "keyed neighbour: a clip dragged far back is not met by the ripple",
        bounds == [(-1040, -990), (-980, 100)]
        and got == [(-1030.0, 0.0), (-1000.0, 1.0), (-980.0, 5.0), (-960.0, 6.0)],
        f"{bounds} {got}",
    )

    # ---- a landing on a CONTIGUOUS seam grows its shot a frame past it -------
    # Mirror of mayatk's TestALandingOnAContiguousSeamGrowsItsShotAFrame
    # (BACKLOG 2026-09-22, decided 2026-09-23): touching shots share ONE
    # sample, so a key landing on that frame took the neighbour's opening pose.
    touching = [("A", 0, 50), ("B", 50, 100)]
    seam_keys = ((10, 0), (40, 5), (50, 3), (70, 9), (90, 2))
    bounds, got = _seam_case(
        "SeamPast", touching, seam_keys, ("keys", 0, [(40.0, 60.0)])
    )
    check(
        "contiguous seam: a key past the end leaves B its opening pose",
        bounds == [(0, 61), (61, 111)]
        and got
        == [
            (10.0, 0.0),
            (50.0, 3.0),
            (60.0, 5.0),
            (61.0, 3.0),
            (81.0, 9.0),
            (101.0, 2.0),
        ],
        f"{bounds} {got}",
    )
    bounds, got = _seam_case(
        "SeamEqual",
        touching,
        ((10, 0), (40, 5), (50, 5), (70, 9), (90, 2)),
        ("keys", 0, [(40.0, 60.0)]),
    )
    check(
        "contiguous seam: equal poses keep their frames",
        bounds == [(0, 61), (61, 111)]
        and got
        == [
            (10.0, 0.0),
            (50.0, 5.0),
            (60.0, 5.0),
            (61.0, 5.0),
            (81.0, 9.0),
            (101.0, 2.0),
        ],
        f"{bounds} {got}",
    )
    bounds, got = _seam_case("SeamOn", touching, seam_keys, ("keys", 0, [(40.0, 50.0)]))
    check(
        "contiguous seam: a key onto the existing seam leaves B whole",
        bounds == [(0, 51), (51, 101)]
        and got == [(10.0, 0.0), (50.0, 5.0), (51.0, 3.0), (71.0, 9.0), (91.0, 2.0)],
        f"{bounds} {got}",
    )
    bounds, got = _seam_case(
        "SeamBack", touching, seam_keys, ("keys", 1, [(70.0, 45.0)])
    )
    check(
        "contiguous seam: a key dragged back over it mirrors the step",
        bounds == [(-6, 44), (44, 100)]
        and got
        == [
            (4.0, 0.0),
            (34.0, 5.0),
            (44.0, 3.0),
            (45.0, 9.0),
            (50.0, 3.0),
            (90.0, 2.0),
        ],
        f"{bounds} {got}",
    )
    unkeyed = ((10, 0), (20, 1), (40, 2), (55, 3))
    bounds, got = _seam_case(
        "SeamBare", [("A", 0, 30), ("B", 30, 60)], unkeyed, ("keys", 0, [(20.0, 30.0)])
    )
    check(
        "contiguous seam: with no key on it nothing steps",
        bounds == [(0, 30), (30, 60)]
        and [t for t, _v in got] == [10.0, 30.0, 40.0, 55.0],
        f"{bounds} {got}",
    )
    # A main-track OBJECT clip grows through move_object_in_shot, which sized
    # the bound itself (rounded, no step): past the seam the dragged 5 landed
    # on B's opening pose and one of the two was dropped, and onto the
    # existing seam the same.
    object_clip = {"orig_start": 10.0, "orig_end": 40.0}
    bounds, got = _seam_case(
        "SeamObjPast", touching, seam_keys, ("clip", 0, (object_clip, 30.0))
    )
    check(
        "contiguous seam: an object clip past the end leaves B its opening pose",
        bounds == [(0, 61), (61, 111)]
        and got == [(30.0, 0.0), (60.0, 5.0), (61.0, 3.0), (81.0, 9.0), (101.0, 2.0)],
        f"{bounds} {got}",
    )
    bounds, got = _seam_case(
        "SeamObjOn", touching, seam_keys, ("clip", 0, (object_clip, 20.0))
    )
    check(
        "contiguous seam: an object clip onto the existing seam leaves B whole",
        bounds == [(0, 51), (51, 101)]
        and got == [(20.0, 0.0), (50.0, 5.0), (51.0, 3.0), (71.0, 9.0), (91.0, 2.0)],
        f"{bounds} {got}",
    )
    bounds, got = _seam_case(
        "SeamObjBare",
        [("A", 0, 30), ("B", 30, 60)],
        unkeyed,
        ("clip", 0, ({"orig_start": 10.0, "orig_end": 20.0}, 20.0)),
    )
    check(
        "contiguous seam: an object clip onto an unkeyed seam steps nothing",
        bounds == [(0, 30), (30, 60)]
        and [t for t, _v in got] == [20.0, 30.0, 40.0, 55.0],
        f"{bounds} {got}",
    )
    # Back over the seam, A's closing pose stays behind in B's landing zone:
    # the object move shifted keys raw (mayatk's clears the landing zone), so
    # it stayed there as a stray 3 inside B and A closed on nothing.
    bounds, got = _seam_case(
        "SeamObjBack",
        touching,
        seam_keys,
        ("clip", 1, ({"orig_start": 70.0, "orig_end": 90.0}, 45.0)),
    )
    check(
        "contiguous seam: an object clip dragged back over it mirrors the step",
        bounds == [(-6, 44), (44, 100)]
        and got == [(4.0, 0.0), (34.0, 5.0), (44.0, 3.0), (45.0, 9.0), (65.0, 2.0)],
        f"{bounds} {got}",
    )

    # ---- a split seam: both shots play on as they did -----------------------
    # BACKLOG 2026-09-07 (mirror of mayatk's TestASplitSeamPlaysOnAsItDid):
    # opening a contiguous seam copies the shared sample to the following
    # shot's new start -- or CARRIES it there when only that shot animates the
    # curve. Inserted plain, the key took the default AUTO_CLAMPED handles (a
    # VECTOR seam: the following shot 1.152 off), the insert's subdivision cut
    # the next key's ALIGNED handle (0.431 off), and CONT_ACCEL re-solved each
    # auto run through the seam (a seam on a slope: AUTO_CLAMPED 0.437 off
    # before it, 0.718 after).
    def _split_case(name, keys, handle, op):
        o, st, sq = _seam_scene(name, [("A", 0, 50), ("B", 50, 100)], keys)
        fc = _loc_x(o)
        for kp in fc.keyframe_points:
            kp.handle_left_type = kp.handle_right_type = handle
        fc.update()
        a0 = [fc.evaluate(f) for f in range(0, 51)]
        b0 = [fc.evaluate(f) for f in range(50, 101)]
        if op == "grow":
            sq.resize_shot_bounds(st.shot_by_name("A").shot_id, 0, 60)
        else:
            sq.move_shot(st.shot_by_name("B").shot_id, 60)
        a1 = [fc.evaluate(f) for f in range(0, 51)]
        b1 = [fc.evaluate(f + 10) for f in range(50, 101)]
        da = max(abs(x - y) for x, y in zip(a0, a1))
        db = max(abs(x - y) for x, y in zip(b0, b1))
        return fc, da, db

    _split_keys = {
        "extremum": seam_keys,
        "slope": ((10, 0), (40, 3), (50, 5), (70, 9), (90, 2)),
    }
    for _where, _keys in _split_keys.items():
        for _handle in ("AUTO", "AUTO_CLAMPED", "VECTOR", "ALIGNED"):
            for _op in ("grow", "move"):
                _fc, _da, _db = _split_case(
                    f"Split{_where}{_handle}{_op}", _keys, _handle, _op
                )
                check(
                    f"a split {_handle} seam ({_where}, {_op}): both shots play on",
                    _da < 1e-3 and _db < 1e-3,
                    f"A {_da:.5f} B {_db:.5f}",
                )
    _carried = ((-30, 1), (50, 3), (70, 9), (90, 2))
    for _handle in ("AUTO", "AUTO_CLAMPED", "VECTOR", "ALIGNED"):
        _fc, _da, _db = _split_case(f"Carried{_handle}", _carried, _handle, "move")
        check(
            f"a carried {_handle} opening pose: the following shot plays on",
            _db < 1e-3,
            f"B {_db:.5f}",
        )
    # A claimed carried sample (B's start-bound sample) leaves no claim behind
    # on the frame it was cut from (mirror of mayatk's).
    _o, _st, _sq = _seam_scene("CarriedClaim", [("A", 0, 50), ("B", 50, 100)], _carried)
    _key = _ShotSequencerInternal._fc_key(_o.name, _loc_x(_o))
    _sq.ledger.record_key(_key, 50.0, _st.shot_by_name("B").shot_id, "start")
    _sq.move_shot(_st.shot_by_name("B").shot_id, 60)
    _times = [round(kp.co[0], 3) for kp in _loc_x(_o).keyframe_points]
    check(
        "a carried sample leaves no claim behind at its old frame",
        _times == [-30.0, 60.0, 80.0, 100.0] and _sq.ledger.key_times(_key) == [60.0],
        f"{_times} claims={_sq.ledger.key_times(_key)}",
    )
    # ...and an UNCLAIMED one -- the animator's own opening pose -- lands
    # unclaimed: carried, it is still their key, not a sample the system made
    # (claimed, content scans skip it and a bound edit may move or cut it).
    _o, _st, _sq = _seam_scene("CarriedOwn", [("A", 0, 50), ("B", 50, 100)], _carried)
    _key = _ShotSequencerInternal._fc_key(_o.name, _loc_x(_o))
    _sq.move_shot(_st.shot_by_name("B").shot_id, 60)
    _times = [round(kp.co[0], 3) for kp in _loc_x(_o).keyframe_points]
    check(
        "a carried animator pose stays the animator's",
        _times == [-30.0, 60.0, 80.0, 100.0] and _sq.ledger.key_times(_key) == [],
        f"{_times} claims={_sq.ledger.key_times(_key)}",
    )
    # So do a lossless merge's cut keys (mirror of mayatk's): closing gaps
    # onto agreeing poses, B's 110 and C's 120 both land on 100 and one is
    # cut at its old frame, where C's animator key from 140 then lands.
    _o, _st, _sq = _seam_scene(
        "MergeClaim",
        [("A", 0, 50), ("B", 60, 110), ("C", 120, 170)],
        ((0, 0), (50, 2), (60, 2), (110, 4), (120, 4), (130, 5), (140, 7), (170, 9)),
    )
    _key = _ShotSequencerInternal._fc_key(_o.name, _loc_x(_o))
    for _t, _shot, _edge in (
        (50, "A", "end"),
        (60, "B", "start"),
        (110, "B", "end"),
        (120, "C", "start"),
    ):
        _sq.ledger.record_key(_key, float(_t), _st.shot_by_name(_shot).shot_id, _edge)
    _sq.respace(gap=0, start_frame=0)
    _times = [round(kp.co[0], 3) for kp in _loc_x(_o).keyframe_points]
    check(
        "a lossless merge's cut keys leave no claim behind",
        _times == [0.0, 50.0, 100.0, 110.0, 120.0, 150.0]
        and _sq.ledger.key_times(_key) == [50.0, 100.0],
        f"{_times} claims={_sq.ledger.key_times(_key)}",
    )

    def _halves(fc, t):
        kp = next(k for k in fc.keyframe_points if abs(k.co[0] - t) < 1e-3)
        return kp.handle_left_type, kp.handle_right_type, kp

    # A seam the split did not move keeps its types; one it did is frozen,
    # the shot-facing half exactly where it was while shared.
    _fc, _da, _db = _split_case("SplitGate", seam_keys, "AUTO_CLAMPED", "move")
    check(
        "a split AUTO_CLAMPED extremum keeps its handle types",
        _halves(_fc, 50)[:2] == _halves(_fc, 60)[:2] == ("AUTO_CLAMPED",) * 2,
        f"50 {_halves(_fc, 50)[:2]} 60 {_halves(_fc, 60)[:2]}",
    )
    _o, _st, _sq = _seam_scene(
        "SplitFreeze", [("A", 0, 50), ("B", 50, 100)], _split_keys["slope"]
    )
    _fc = _loc_x(_o)
    for _kp in _fc.keyframe_points:
        _kp.handle_left_type = _kp.handle_right_type = "AUTO"
    _fc.update()
    _shared = _halves(_fc, 50)[2]
    _roff = _shared.handle_right[1] - _shared.co[1]
    _sq.move_shot(_st.shot_by_name("B").shot_id, 60)
    _lt, _rt, _copy = _halves(_fc, 60)
    check(
        "a split AUTO seam on a slope: the copy is frozen at the shared handle",
        (_lt, _rt) == ("FREE", "FREE")
        and abs(_copy.handle_right[1] - _copy.co[1] - _roff) < 1e-4,
        f"{_lt}/{_rt} right {_copy.handle_right[1] - _copy.co[1]:.4f} vs {_roff:.4f}",
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
    # landed, and the copy was pushed to 290.1 -- a stray sixth key.
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
        got == [(200.0, 0.0), (239.3, 5.0), (289.1, 2.0), (290.0, 7.0), (320.0, 1.0)],
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
        got == [(200.0, 0.0), (239.3, 5.0), (289.1, 2.0), (290.0, 7.0), (320.0, 1.0)]
        and [(s.start, s.end) for s in st.sorted_shots()] == [(100, 200), (200, 330)],
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
        got == [(200.0, 5.0)],
        f"{got}",
    )

    # ---- an audio clip moves by what its VISIBLE part moved ---------------
    # The widget draws only the part of a strip inside its shot and a drag
    # reports where THAT landed; measured from the strip's own start the move
    # was off by the part before the shot.
    import wave

    import pythontk as ptk
    from blendertk.audio_utils._audio_utils import AudioUtils as _Audio

    with ptk.TempArtifacts(prefix="btk_seq_visaudio_") as vtmp:
        vwav = vtmp.path(".wav")
        with wave.open(vwav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 4000)  # 0.5 s of silence
        BlenderShotStore.clear_active()
        vst = BlenderShotStore()
        vst.define_shot("A", 0, 50, objects=[])
        b_shot = vst.define_shot("B", 60, 100, objects=[])
        strip = _Audio.add_clip(vwav, frame_start=55, scene=bpy.context.scene)
        info = _Audio.get_clip(strip)
        strip_data = {
            "is_audio": True,
            "audio_track_id": strip,
            "shot_id": b_shot.shot_id,
            "orig_start": float(info["frame_start"]),
            "orig_end": float(info["frame_end"]),
            "vis_start": 60.0,
        }
        _move_clip(strip_data, ShotSequencer(vst), 62.0)  # the visible part moved +2
        moved = _Audio.get_clip(strip)["frame_start"]
        vshots = [(s.start, s.end) for s in vst.sorted_shots()]
        check(
            "audio clip: moved by its visible part's +2, not +7, and no shot moved",
            moved == 57 and vshots == [(0, 50), (60, 100)],
            f"{moved} {vshots}",
        )
        _Audio.remove_clip(strip)

    # ---- a refused drag reports instead of raising (mirror of mayatk) -------
    # The planner REFUSES a ripple that would force two shots' disagreeing
    # poses onto one frame; measured in Maya, that refusal came out of the
    # clip handlers as a traceback at the end of a mouse drag.
    from pythontk import ShotBoundaryConflict

    class _RefusingHost(_KeysHost):
        def __init__(self, widget, sequencer):
            super().__init__(widget, sequencer)
            self.footers, self.warned, self.dropped = [], [], []
            self._shifted_out_keys = {}
            self._audio_segments_cache = None
            warned = self.warned

            class _Log:
                def warning(self, msg, *a, **k):
                    warned.append(msg)

                def __getattr__(self, _name):
                    return lambda *a, **k: None

            self.logger = _Log()

        def _set_footer(self, text, *a, **k):
            self.footers.append(text)

        def _discard_shot_state(self):
            self.dropped.append(True)

        def _expand_shot_range(self, *_a, **_kw):
            raise ShotBoundaryConflict([("crv", 1.0, [0.0, 1.0])])

    for label, drag in (
        ("a single clip drag", lambda h: h.on_clip_moved(1, 40.0)),
        ("a batch clip drag", lambda h: h.on_clips_batch_moved([(1, 40.0)])),
    ):
        ro, rst, rsq = _seam_scene("RefuseMv", [("A", 10, 20)], ((10, 0), (20, 1)))
        clip = _FakeClip(
            {
                "obj": ro.name,
                "attr_name": "translateX",
                "shot_id": rst.shot_by_name("A").shot_id,
                "orig_start": 10.0,
                "orig_end": 20.0,
            }
        )
        rhost = _RefusingHost(_FakeWidget(clip), rsq)
        try:
            drag(rhost)
            raised = None
        except ShotBoundaryConflict as exc:  # the defect: a drag-end traceback
            raised = exc
        want = str(ShotBoundaryConflict([("crv", 1.0, [0.0, 1.0])]))
        check(
            f"refused drag: {label} reports the refusal instead of raising",
            raised is None
            and rhost.warned == [want]
            and rhost.footers[-1:] == [want]
            and rhost.dropped == [],
            f"raised={raised!r} warned={rhost.warned} footers={rhost.footers} "
            f"dropped={rhost.dropped}",
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
        sequencer=ShotSequencer(BlenderShotStore()),
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
    # A key delete is a key edit like any other, so it runs the bracket's
    # reconcile: a deleted key's claims -- a sample's and a gap hold's -- go
    # with it instead of waiting for the next key that lands on the frame.
    bpy.ops.mesh.primitive_cube_add()
    dc_obj = bpy.context.active_object
    dc_obj.name = "DelClaims"
    for f, v in ((5, 1.0), (10, 2.0), (60, 3.0)):
        dc_obj.location = (v, 0.0, 0.0)
        dc_obj.keyframe_insert(data_path="location", index=0, frame=f)
    dc_store = BlenderShotStore()
    dc_store.define_shot("A", 0, 50, objects=["DelClaims"])
    dc_seq = ShotSequencer(dc_store)
    dc_fc = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(dc_obj)
        if fc.data_path == "location" and fc.array_index == 0
    )
    dc_key = _ShotSequencerInternal._fc_key("DelClaims", dc_fc)
    dc_seq.ledger.record_key(dc_key, 10.0, 0, "end")
    dc_seq.ledger.record_step(dc_key, 10.0, "CONSTANT", "CONSTANT")
    dc_host = _KeysHost(
        _FakeWidget(
            _FakeClip({"obj": "DelClaims", "orig_start": 0.0, "orig_end": 50.0})
        ),
        sequencer=dc_seq,
    )
    ShotSequencerController._delete_clip_keys(dc_host, [1])
    dc_times = [round(kp.co[0], 3) for kp in dc_fc.keyframe_points]
    check(
        "Delete Key: a deleted key's claims go with it",
        dc_times == [60.0]
        and dc_seq.ledger.key_times(dc_key) == dc_seq.ledger.step_times(dc_key) == [],
        f"{dc_times} claims={dc_seq.ledger.key_times(dc_key)} "
        f"steps={dc_seq.ledger.step_times(dc_key)}",
    )
    # ...a sample still ON its bound too -- where the system makes them.  The
    # reconcile passed a claim on its bound before asking whether its key was
    # still there, so the claim waited on the frame for the next key to land.
    bpy.ops.mesh.primitive_cube_add()
    db_obj = bpy.context.active_object
    db_obj.name = "DelOnBound"
    for f, v in ((10, 0.0), (30, 5.0), (50, 3.0)):
        db_obj.location = (v, 0.0, 0.0)
        db_obj.keyframe_insert(data_path="location", index=0, frame=f)
    db_store = BlenderShotStore()
    db_shot = db_store.define_shot("A", 0, 50, objects=["DelOnBound"])
    db_seq = ShotSequencer(db_store)
    db_fc = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(db_obj)
        if fc.data_path == "location" and fc.array_index == 0
    )
    db_key = _ShotSequencerInternal._fc_key("DelOnBound", db_fc)
    db_seq.ledger.record_key(db_key, 50.0, db_shot.shot_id, "end")
    _KeysHost(
        _FakeWidget(
            _FakeClip(
                {
                    "obj": "DelOnBound",
                    "attr_name": "translateX",
                    "shot_id": db_shot.shot_id,
                }
            )
        ),
        sequencer=db_seq,
    ).on_keys_deleted(1, [50.0])
    db_times = [round(kp.co[0], 3) for kp in db_fc.keyframe_points]
    check(
        "Delete Key: a deleted sample on its bound gives up its claim",
        db_times == [10.0, 30.0] and db_seq.ledger.key_times(db_key) == [],
        f"{db_times} claims={db_seq.ledger.key_times(db_key)}",
    )
    # ...and every OTHER claim on a deleted key, through all three delete paths:
    # a behavior's authored claim is no sample, so the reconcile left it on the
    # frame for the next key that lands there to inherit.
    bpy.ops.mesh.primitive_cube_add()
    da_obj = bpy.context.active_object
    da_obj.name = "DelAuthored"
    for f, v in ((10, 0.0), (30, 5.0), (40, 6.0), (60, 3.0)):
        da_obj.location = (v, 0.0, 0.0)
        da_obj.keyframe_insert(data_path="location", index=0, frame=f)
    da_store = BlenderShotStore()
    da_store.define_shot("A", 0, 50, objects=["DelAuthored"])
    da_seq = ShotSequencer(da_store)
    da_key = _ShotSequencerInternal._fc_key("DelAuthored", _loc_x(da_obj))
    for t in (10.0, 30.0, 40.0):
        da_seq.ledger.record_authored(da_key, t, 0, "fade_in", "DelAuthored")
    da_sub = {"obj": "DelAuthored", "attr_name": "translateX", "shot_id": 0}
    _KeysHost(_FakeWidget(_FakeClip(da_sub)), sequencer=da_seq).on_keys_deleted(
        1, [30.0]
    )
    ShotSequencerController._delete_clip_keys(
        _KeysHost(
            _FakeWidget(
                _FakeClip({"obj": "DelAuthored", "orig_start": 0.0, "orig_end": 20.0})
            ),
            sequencer=da_seq,
        ),
        [1],
    )

    from types import SimpleNamespace

    class _SelKey:  # stands in for uitk's KeyframeItem (no Qt in Blender)
        def __init__(self, time):
            self._time = time
            self._parent_clip = SimpleNamespace(_data=SimpleNamespace(clip_id=1))

    dk_widget = _FakeWidget(_FakeClip(da_sub))
    dk_widget._timeline = SimpleNamespace(
        _scene=SimpleNamespace(selectedItems=lambda: [_SelKey(40.0)])
    )
    dk_host = _KeysHost(dk_widget, sequencer=da_seq)
    dk_host.active_shot_id = None
    import types

    fake_uitk = types.ModuleType("uitk")  # a module: import hooks read __name__
    fake_uitk.KeyframeItem = _SelKey
    saved_uitk = sys.modules.get("uitk")
    sys.modules["uitk"] = fake_uitk
    try:
        ShotSequencerController._delete_selected_clip_keys(dk_host)
    finally:
        if saved_uitk is not None:
            sys.modules["uitk"] = saved_uitk
        else:
            sys.modules.pop("uitk", None)
    da_times = [round(kp.co[0], 3) for kp in _loc_x(da_obj).keyframe_points]
    check(
        "Delete Key: every claim on a deleted key goes, a behavior's too (3 paths)",
        da_times == [60.0]
        and not any(da_seq.ledger.owns_any(da_key, t) for t in (10.0, 30.0, 40.0)),
        f"{da_times} {da_seq.ledger.to_dict()}",
    )
    # The restore point PREDATES the edit (mirror of mayatk's scene_edit): a
    # delete now reconciles, releasing the deleted keys' claims, so a point
    # pushed after it handed undo a ledger without them -- the keys came back
    # unclaimed.  A delete that removed nothing leaves no point behind.
    from pythontk.core_utils.engines.shots.shot_ledger import ShotEditLedger

    class _SnapHost(_KeysHost):
        def _save_shot_state(self):
            self.sequencer.store.push_boundary_snapshot()

        def _discard_shot_state(self):
            self.sequencer.store.discard_boundary_snapshot()

    bpy.ops.mesh.primitive_cube_add()
    du_obj = bpy.context.active_object
    du_obj.name = "DelUndo"
    for f, v in ((10, 0.0), (30, 5.0), (50, 3.0)):
        du_obj.location = (v, 0.0, 0.0)
        du_obj.keyframe_insert(data_path="location", index=0, frame=f)
    du_store = BlenderShotStore()
    du_shot = du_store.define_shot("A", 0, 50, objects=["DelUndo"])
    du_seq = ShotSequencer(du_store)
    du_key = _ShotSequencerInternal._fc_key("DelUndo", _loc_x(du_obj))
    du_seq.ledger.record_key(du_key, 50.0, du_shot.shot_id, "end")
    du_data = {"obj": "DelUndo", "attr_name": "translateX", "shot_id": du_shot.shot_id}
    du_host = _SnapHost(_FakeWidget(_FakeClip(du_data)), sequencer=du_seq)
    du_host.on_keys_deleted(1, [50.0])
    du_points = [state for state, _tag in du_store._boundary_undo]
    du_saved = (
        ShotEditLedger.from_dict(du_points[-1].get("ledger")).key_times(du_key)
        if du_points
        else None
    )
    check(
        "Delete Key: the restore point predates the edit (undo gets the claims)",
        len(du_points) == 1
        and du_saved == [50.0]
        and du_seq.ledger.key_times(du_key) == [],
        f"points={len(du_points)} saved={du_saved} "
        f"now={du_seq.ledger.key_times(du_key)}",
    )
    du_host.on_keys_deleted(1, [30.5])  # no key there
    ShotSequencerController._delete_clip_keys(
        _SnapHost(
            _FakeWidget(
                _FakeClip({"obj": "DelUndo", "orig_start": 31.0, "orig_end": 40.0})
            ),
            sequencer=du_seq,
        ),
        [1],
    )
    check(
        "Delete Key: a delete that removed nothing leaves no restore point",
        len(du_store._boundary_undo) == 1,
        f"points={len(du_store._boundary_undo)}",
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

    # ---- _select_channels: every caller hands NAMES, and the pick replaces ----
    # (pre-fix: the clear read ``animation_data`` off the name strings, found no
    # fcurves, and left every earlier channel selected beside the new pick)
    bpy.ops.mesh.primitive_cube_add()
    chan_obj = bpy.context.active_object
    chan_obj.name = "ChanSel"
    chan_obj.keyframe_insert(data_path="location", frame=1)
    chan_obj.keyframe_insert(data_path="rotation_euler", frame=1)

    def _picked_channels():
        return sorted(
            (fc.data_path, fc.array_index)
            for fc in BlenderShotStore.iter_action_fcurves(chan_obj)
            if fc.select
        )

    def _pick_every_channel():
        for fc in BlenderShotStore.iter_action_fcurves(chan_obj):
            fc.select = True

    chan_ctl = ShotSequencerController.__new__(ShotSequencerController)
    chan_ctl.ui = None  # no footer to write to
    chan_ctl._syncing = False
    chan_ctl._get_sequencer_widget = lambda: _FakeWidget(
        _FakeClip({"obj": "ChanSel", "attr_name": "rotateZ"})
    )
    for label, act, expected in (
        (
            "a header label clears the object's channel selection",
            lambda: chan_ctl.on_track_selected(["ChanSel"]),
            [],
        ),
        (
            "a sub-row label selects only its channel",
            lambda: chan_ctl.on_sub_track_selected([("ChanSel", "translateX")]),
            [("location", 0)],
        ),
        (
            "a sub-row clip selects only its channel",
            lambda: chan_ctl.on_selection_changed([1]),
            [("rotation_euler", 2)],
        ),
    ):
        _pick_every_channel()
        try:
            act()
            picked = _picked_channels()
        except Exception as e:  # report, don't abort the suite
            picked = repr(e)
        check(f"_select_channels: {label}", picked == expected, f"{picked}")
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
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if fc.data_path == data_path and fc.array_index == index:
                return sorted(round(float(kp.co[0]), 3) for kp in fc.keyframe_points)
        return []

    tx = channel_times("A", "location", 0)
    ty = channel_times("A", "location", 1)
    check(
        "move_attribute_keys: only the named keys of the named channel moved",
        2.0 not in tx and 4.0 not in tx and 3.0 in tx and len(tx) == 11,
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
            b.start <= t <= b.end for t in tx if t not in [float(i) for i in range(11)]
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
            for fc in BlenderShotStore.iter_action_fcurves(obj)
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
            for fc in BlenderShotStore.iter_action_fcurves(ob)
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
        for fc in BlenderShotStore.iter_action_fcurves(lead)
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
    check(
        "trim: the end sample follows the bound onto a free frame, claim intact",
        (s0.start, s0.end, s1.start, s1.end) == (0, 50, 65, 150)
        and times_of(obs["holdB"]) == [10.0, 50.0, 65.0, 90.0]
        and sorted(sq.ledger.key_times(hkey)) == [50.0],
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

    # -- a Ctrl edge drag moves no sample (mirror of mayatk's
    # TestACtrlEdgeDragMovesNoSample, 2026-09-19).  The Ctrl path reconciled
    # with the default rule, so a claimed pin FOLLOWED the bound, re-timing its
    # ramp (and sliding past a gap key); nothing moves in a Ctrl drag.
    from blendertk.anim_utils.shots.shot_sequencer.gap_manager import (
        GapManagerMixin,
    )

    class _CtrlHost(GapManagerMixin):
        _syncing = False

        def __init__(self, seq_, sid):
            self.sequencer, self.active_shot_id = seq_, sid

        def _drag_modifiers(self):
            return True, False  # Ctrl held at the press

        def _save_shot_state(self):
            pass

        def _gap_edit_epilogue(self):
            pass

    for label, new_end in (("grow", 53), ("grow over a gap key", 57), ("shrink", 45)):
        st, sq, obs = fresh({"cpA": {10: 0, 40: 5, 55: 1, 70: 2, 90: 8}})
        ca = sq.define_shot("A", 0, 50, objects=["cpA"])
        sq.define_shot("B", 60, 100, objects=["cpA"])
        cfc = fc_of(obs["cpA"])
        obs["cpA"].location.x = cfc.evaluate(50.0)
        obs["cpA"].keyframe_insert(data_path="location", index=0, frame=50)
        sq.ledger.record_key(_SSI._fc_key("cpA", cfc), 50.0, ca.shot_id, "end")
        sq._enforce_gap_holds()  # so only the gesture is measured
        before = [round(fc_of(obs["cpA"]).evaluate(f), 4) for f in range(0, 101)]
        _CtrlHost(sq, ca.shot_id).on_range_highlight_changed(0, new_end)
        after = [round(fc_of(obs["cpA"]).evaluate(f), 4) for f in range(0, 101)]
        check(
            f"ctrl {label}: the bound moves and no sample does",
            sq.shot_by_id(ca.shot_id).end == new_end
            and before == after
            and 50.0 in times_of(obs["cpA"]),
            f"end={sq.shot_by_id(ca.shot_id).end} times={times_of(obs['cpA'])} "
            f"changed={sum(a != b for a, b in zip(before, after))}",
        )

    # -- Ctrl past the neighbour (mirror of mayatk's
    # test_a_ctrl_edge_drag_pushes_into_the_next_shot, 2026-10-06): the
    # neighbour's start gives way, no key moves, and the object moving in the
    # frames taken joins this shot while still moving in the neighbour.
    st, sq, obs = fresh({"cpA": {10: 0, 40: 5}, "cpB": {70: 0, 90: 5}})
    ca = sq.define_shot("A", 0, 50, objects=["cpA"])
    cb = sq.define_shot("B", 60, 100, objects=["cpB"])
    keys_before = (times_of(obs["cpA"]), times_of(obs["cpB"]))
    _CtrlHost(sq, ca.shot_id).on_range_highlight_changed(0, 75)
    sa, sb = sq.shot_by_id(ca.shot_id), sq.shot_by_id(cb.shot_id)
    check(
        "ctrl past the neighbour: its start gives way and the mover joins",
        (sa.start, sa.end, sb.start, sb.end) == (0, 75, 75, 100)
        and "cpB" in sa.objects
        and "cpB" in sb.objects
        and (times_of(obs["cpA"]), times_of(obs["cpB"])) == keys_before,
        f"A={sa.start}-{sa.end} {sa.objects} B={sb.start}-{sb.end} {sb.objects}",
    )

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
        hkv == [(200.0, 0), (239.4, 5), (289.35, 2), (290.2, 7), (320.0, 1)],
        f"keys={hkv}",
    )
    # A key dragged past the end (to 50.4): the shot grows AROUND it.
    from blendertk.anim_utils.shots.shot_sequencer.clip_motion import (
        ClipMotionMixin,
    )

    class _ExpandHost(ClipMotionMixin):
        _syncing = False

        def __init__(self, seq_):
            self.sequencer, self._segment_cache = seq_, {}

        def _get_sequencer_widget(self):
            return None

    st, sq, obs = fresh({"exA": {10.6: 0, 50.4: 2}, "exB": {70: 0, 90: 5}})
    ea = sq.define_shot("A", 0, 50, objects=["exA"])
    eb = sq.define_shot("B", 60, 100, objects=["exB"])
    _ExpandHost(sq)._expand_shot_range(ea.shot_id, 10.6, 50.4)
    check(
        "fractional content: a key dragged past the end is enclosed",
        bounds(ea, eb) == (0, 51, 61, 101) and times_of(obs["exB"]) == [71.0, 91.0],
        f"bounds={bounds(ea, eb)} B={times_of(obs['exB'])}",
    )

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

    # -- a dragged tangent handle lands on the keyframe point ---------------
    from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
        ShotSequencerController as _Ctrl,
    )

    st, sq, obs = fresh({"tanA": {0: 0, 10: 5, 20: 0}})
    sq.define_shot("A", 0, 20, objects=["tanA"])
    tan_footers = []

    class _TanClip:
        data = {"obj": "tanA", "attr_name": "translateX", "shot_id": 0}

    class _TanWidget:
        @staticmethod
        def get_clip(_cid):
            return _TanClip()

        @staticmethod
        def select_keys(_wanted):
            return 0

    class _TanCtl(_Ctrl):
        def __init__(self):  # bypass the panel's __init__
            self._segment_cache = {}
            self._sub_row_cache = {}
            self._audio_segments_cache = None
            self._syncing = False

        sequencer = sq

        def _get_sequencer_widget(self):
            return _TanWidget()

        def _set_footer(self, text, **kw):
            tan_footers.append(text)

        def _sync_to_widget(self, **kw):
            pass

    tan_ctl = _TanCtl()
    check(
        "the retired single-key tangent handler stays removed (2026-09-21)",
        not hasattr(_TanCtl, "on_key_tangent_dragged"),
    )
    fc_tan = fc_of(obs["tanA"])
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    kp_mid.handle_left_type = kp_mid.handle_right_type = "AUTO_CLAMPED"
    fc_tan.update()
    tan_ctl.on_keys_tangent_dragged([(1, [(10.0, 3.0, 4.0)])], "out", False)
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    hr = (round(kp_mid.handle_right[0], 3), round(kp_mid.handle_right[1], 3))
    hl = (kp_mid.handle_left[0] - 10.0, kp_mid.handle_left[1] - 5.0)
    check(
        "handle drag: the OUT handle lands on co + (dt, dv) and both sides go ALIGNED",
        hr == (13.0, 9.0)
        and kp_mid.handle_right_type == "ALIGNED"
        and kp_mid.handle_left_type == "ALIGNED",
        f"right={hr} types={kp_mid.handle_left_type}/{kp_mid.handle_right_type}",
    )
    check(
        "handle drag: the aligned IN handle is re-aimed opposite, length kept",
        abs(hl[0] * 4.0 - hl[1] * 3.0) < 1e-3 and hl[0] < 0,
        f"left vector={hl}",
    )
    kp_mid.handle_left_type = "FREE"
    fc_tan.update()
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector as _SC,
    )

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
    tan_ctl.on_keys_tangent_dragged([(1, [(10.0, -2.0, 1.0)])], "in", False)
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    check(
        "handle drag: a FREE side takes the vector as-is and stays FREE",
        (round(kp_mid.handle_left[0], 3), round(kp_mid.handle_left[1], 3)) == (8.0, 6.0)
        and kp_mid.handle_left_type == "FREE"
        and any("handle dragged" in f for f in tan_footers),
        f"left={tuple(kp_mid.handle_left)} type={kp_mid.handle_left_type}",
    )

    # -- a drag that carried the selection, and the Alt break ---------------
    tan_ctl.on_keys_tangent_dragged(
        [(1, [(0.0, 2.0, 1.0), (20.0, 3.0, -1.0)])], "out", False
    )
    kps = {round(kp.co[0]): kp for kp in fc_tan.keyframe_points}
    first = (round(kps[0].handle_right[0], 3), round(kps[0].handle_right[1], 3))
    last = (round(kps[20].handle_right[0], 3), round(kps[20].handle_right[1], 3))
    check(
        "handle drag: every key the gesture carried took its OWN vector",
        first == (2.0, 1.0) and last == (23.0, -1.0),
        f"first={first} last={last}",
    )
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    kp_mid.handle_left_type = kp_mid.handle_right_type = "ALIGNED"
    fc_tan.update()
    left_before = (round(kp_mid.handle_left[0], 3), round(kp_mid.handle_left[1], 3))
    tan_ctl.on_keys_tangent_dragged([(1, [(10.0, 4.0, 2.0)])], "out", True)
    kp_mid = next(kp for kp in fc_tan.keyframe_points if abs(kp.co[0] - 10) < 1e-3)
    left_after = (round(kp_mid.handle_left[0], 3), round(kp_mid.handle_left[1], 3))
    right = (round(kp_mid.handle_right[0], 3), round(kp_mid.handle_right[1], 3))
    check(
        "handle drag: a broken drag frees BOTH sides and leaves the partner put",
        kp_mid.handle_left_type == "FREE"
        and kp_mid.handle_right_type == "FREE"
        and left_after == left_before
        and right == (14.0, 7.0)
        and any("handle broken" in f for f in tan_footers),
        f"left {left_before}->{left_after} right={right} "
        f"types={kp_mid.handle_left_type}/{kp_mid.handle_right_type}",
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
    sq.define_shot("A", 1, 20, objects=["delA"])
    sb = sq.define_shot("B", 40, 60, objects=["delB"])
    sq.define_shot("C", 80, 100, objects=["delC"])
    res = sq.delete_shot(sb.shot_id)
    check(
        "delete_shot: B's keys are gone",
        len(fc_of(obs["delB"]).keyframe_points) == 0,
        f"{times_of(obs['delB'])}",
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
    head, tail = sq.extend_shot_to_fit(s1.shot_id, reach=10)
    s0, s1 = sq.shot_by_id(s0.shot_id), sq.shot_by_id(s1.shot_id)
    check(
        "extend reach: the leading gap counts, the previous shot's span never",
        (head, tail) == (-8, 0)
        and s1.start == 62
        and times_of(obs["ldB"]) == [37, 62, 80, 100]
        and (s0.start, s0.end) == (-8, 42)
        and times_of(obs["ldA"]) == [-8, 32],
        f"{(head, tail)} S1.start={s1.start} B={times_of(obs['ldB'])} "
        f"S0={(s0.start, s0.end)} A={times_of(obs['ldA'])}",
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

    # -- widget sync parity with mayatk (2026-10-04) ----------------------------
    # Expected values are mayatk's own output on the identical scene, measured
    # side by side (fresh mayapy + fresh headless Blender, same keys).
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
        SegmentCollector as _SC,
    )

    class _SyncWidget:
        """Records what the controller's widget sync hands the widget."""

        def __init__(self):
            self.clips = []
            self.attribute_colors = {}
            self.bg_rows = []

        def add_clip(self, track_id=None, start=None, duration=None, **kw):
            self.clips.append(dict(track_id=track_id, start=start, dur=duration, **kw))
            return len(self.clips) - 1

        def set_bg_curve_preview(self, track_id, sub_row, preview, **kw):
            self.bg_rows.append(sub_row)

    def sync_ctl(seq, widget):
        import logging

        ctl = _Ctrl.__new__(_Ctrl)
        ctl.sequencer = seq
        ctl.ui = None
        ctl._get_sequencer_widget = lambda: widget
        ctl.logger = logging.getLogger("test.widget_sync")
        ctl._sub_row_cache = {}
        ctl._segment_cache = {}
        ctl._shifted_out_keys = {}
        ctl._shot_display_mode = "current"
        ctl._track_order_scope = "shot"
        ctl._cmb_mode = "shots"
        ctl._show_internal_holds = False
        return ctl

    def build_main_row(seq, widget, ctl, shot):
        segs, objs = _SC.collect_segments(
            seq, shot, [shot], ctl._segment_cache, ctl._shifted_out_keys, ctl.logger
        )
        track_ids = {o: i for i, o in enumerate(sorted(objs))}
        ctl._build_clips(widget, shot, [shot], segs, track_ids)
        return track_ids, objs

    st, sq, obs = fresh({"wsFlat": {0: 1, 10: 1}, "wsMove": {0: 0, 10: 3}})
    vis = obs["wsVis"] = bpy.data.objects.new("wsVis", None)
    bpy.context.scene.collection.objects.link(vis)
    for f, hidden in ((0, False), (6, True), (12, False)):
        vis.hide_render = hidden
        vis.keyframe_insert("hide_render", frame=f)
    flag = bpy.data.objects.new("wsFlag", None)
    bpy.context.scene.collection.objects.link(flag)
    for f, v in ((0, 0.0), (10, 1.0)):
        flag["flag"] = v
        flag.keyframe_insert('["flag"]', frame=f)
    for f, x in ((12, 0.0), (20, 1.0)):
        flag.location.x = x
        flag.keyframe_insert("location", index=0, frame=f)
    ws_shot = sq.define_shot("WS", 0, 24, objects=["wsFlat", "wsMove", "wsFlag"])
    st.set_active_shot(ws_shot.shot_id)

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

    st.update_shot(ws_shot.shot_id, objects=["wsFlat", "wsMove", "wsFlag", "wsVis"])
    ws_widget = _SyncWidget()
    ws_ctl = sync_ctl(sq, ws_widget)
    tids, _objs = build_main_row(sq, ws_widget, ws_ctl, sq.shot_by_id(ws_shot.shot_id))
    by_obj = {}
    for c in ws_widget.clips:
        by_obj.setdefault(c.get("obj"), []).append(c)
    flat_clips = sorted(
        (c["start"], c["dur"], bool(c.get("is_stepped")), c.get("stepped_key_time"))
        for c in by_obj.get("wsFlat", [])
    )
    check(
        "main row: a hold-only object shows its keys as stepped point clips",
        flat_clips == [(0.0, 0.0, True, 0.0), (10.0, 0.0, True, 10.0)],
        f"{flat_clips}",
    )
    flag_clips = [
        (c["start"], c["dur"], sorted(c.get("attributes") or []))
        for c in by_obj.get("wsFlag", [])
    ]
    check(
        "main row: a custom-property channel joins the object's clip",
        flag_clips == [(0.0, 20.0, ["flag", "translateX"])],
        f"{flag_clips}",
    )
    vis_clips = [
        (c["start"], c["dur"], c.get("attributes")) for c in by_obj.get("wsVis", [])
    ]
    check(
        "main row: a visibility-only object gets a visibility clip",
        vis_clips == [(0.0, 12.0, ["visibility"])],
        f"{vis_clips}",
    )

    def rows_of(name):
        ws_ctl._sub_row_cache = {}
        return {
            attr: [(s[0], s[1], bool(s[4].get("is_hold"))) for s in segs]
            for attr, segs in ws_ctl._provide_sub_rows(tids.get(name, 0), name)
        }

    check(
        "sub-rows: a keyed custom property gets its own row",
        rows_of("wsFlag")
        == {"flag": [(0.0, 10.0, False)], "translateX": [(12.0, 8.0, False)]},
        f"{rows_of('wsFlag')}",
    )
    check(
        "sub-rows: hide_render expands as a 'visibility' row",
        rows_of("wsVis") == {"visibility": [(0.0, 12.0, False)]},
        f"{rows_of('wsVis')}",
    )

    # -- key menu edits (mayatk parity, 2026-10-04) ------------------------------
    def keyed_x(name, pairs):
        ob = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(ob)
        for f, v in pairs:
            ob.location.x = v
            ob.keyframe_insert("location", index=0, frame=f)
        return ob

    def pts(ob):
        return sorted(
            (round(kp.co[0], 3), round(kp.co[1], 3)) for kp in fc_of(ob).keyframe_points
        )

    km_footers = []

    class _KmWidget:
        @staticmethod
        def select_keys(_wanted):
            return 0

    class _KeyHost(_Ctrl):
        def __init__(self, seq):  # bypass the panel's __init__
            self._segment_cache = {}
            self._sub_row_cache = {}
            self._audio_segments_cache = None
            self._syncing = False
            self._copied_keys = {}
            self.sequencer = seq

        def _get_sequencer_widget(self):
            return _KmWidget()

        def _save_shot_state(self):
            pass

        def _discard_shot_state(self):
            pass

        def _set_footer(self, text, **kw):
            km_footers.append(text)

        def _sync_to_widget(self, **kw):
            pass

        def _current_time(self):
            return float(bpy.context.scene.frame_current)

    st, sq, obs = fresh({})
    kh = _KeyHost(sq)

    snap = keyed_x("kmSnap", [(10.4, 0.0), (20.6, 1.0)])
    kh._snap_selected_keys([("kmSnap", "translateX", [10.4, 20.6], None)])
    check(
        "key menu: Snap Fractional Keys moves the selected keys to whole frames",
        [t for t, _v in pts(snap)] == [10.0, 21.0],
        f"{pts(snap)} footer={km_footers[-1:]}",
    )

    inv = keyed_x("kmInv", [(0, 0.0), (10, 1.0), (20, 4.0), (30, 9.0)])
    fc_of(inv).keyframe_points[1].interpolation = "CONSTANT"  # 10 -> 20 held
    kh._invert_selected_keys([("kmInv", "translateX", [10.0, 20.0], None)])
    check(
        "key menu: Invert Keys mirrors only the selected keys, in place",
        pts(inv) == [(0.0, 0.0), (10.0, 4.0), (20.0, 1.0), (30.0, 9.0)],
        f"{pts(inv)}",
    )
    check(
        "key menu: Invert Keys re-homes a hold to the key now opening its segment",
        interp_at(inv, 10) == "CONSTANT",
        f"@10={interp_at(inv, 10)} @20={interp_at(inv, 20)}",
    )

    al_a = keyed_x("kmAlA", [(10, 0.0), (15, 1.0), (40, 2.0)])
    al_b = keyed_x("kmAlB", [(20, 0.0), (30, 1.0), (50, 2.0)])
    kh._align_selected_keys(
        [
            ("kmAlA", "translateX", [10.0, 15.0], None),
            ("kmAlB", "translateX", [20.0, 30.0], None),
        ]
    )
    check(
        "key menu: Align Keys shifts each object's block to one start, spacing kept",
        [t for t, _v in pts(al_a)] == [10.0, 15.0, 40.0]
        and [t for t, _v in pts(al_b)] == [10.0, 20.0, 50.0],
        f"A={pts(al_a)} B={pts(al_b)}",
    )

    # A key Align / Invert lands on takes its claims with it (BTK-SHOTS-3):
    # left on the frame, a behavior's authored claim passed to the key that
    # landed there, which the next Build then deleted as its own.
    al_c = keyed_x("kmAlC", [(10, 0.0), (30, 1.0)])
    al_d = keyed_x("kmAlD", [(10, 7.0), (20, 0.0), (40, 1.0)])
    d_key = _SSI._fc_key("kmAlD", fc_of(al_d))
    kh.sequencer.ledger.record_authored(d_key, 10.0, 0, "fade_in", "kmAlD")
    kh._align_selected_keys(
        [
            ("kmAlC", "translateX", [10.0], None),
            ("kmAlD", "translateX", [20.0, 40.0], None),
        ]
    )
    check(
        "key menu: Align Keys releases the claims of a key it lands on",
        pts(al_d) == [(10.0, 0.0), (30.0, 1.0)]
        and pts(al_c) == [(10.0, 0.0), (30.0, 1.0)]
        and not kh.sequencer.ledger.owns_authored(d_key, 10.0),
        f"{pts(al_d)} {kh.sequencer.ledger.to_dict()}",
    )
    inv_o = keyed_x("kmInvO", [(0, 0.0), (3, 1.0), (7, 4.0), (10, 9.0)])
    o_key = _SSI._fc_key("kmInvO", fc_of(inv_o))
    kh.sequencer.ledger.record_authored(o_key, 7.0, 0, "fade_in", "kmInvO")
    kh._invert_selected_keys([("kmInvO", "translateX", [0.0, 3.0, 10.0], None)])
    check(
        "key menu: Invert Keys releases the claims of a key it lands on",
        pts(inv_o) == [(0.0, 9.0), (7.0, 1.0), (10.0, 0.0)]
        and not kh.sequencer.ledger.owns_authored(o_key, 7.0),
        f"{pts(inv_o)} {kh.sequencer.ledger.to_dict()}",
    )
    # A key Align / Invert MOVES carries its claims along (ShotStore.remap_moved):
    # left on its old frame, a fade's claim passed to the key that arrived there
    # and the moved fade key read as the animator's.
    inv_m = keyed_x("kmInvM", [(10, 1.0), (15, 2.0), (25, 3.0), (30, 4.0)])
    m_key = _SSI._fc_key("kmInvM", fc_of(inv_m))
    kh.sequencer.ledger.record_authored(m_key, 15.0, 0, "fade_in", "kmInvM")
    kh._invert_selected_keys([("kmInvM", "translateX", [10.0, 15.0, 25.0, 30.0], None)])
    check(
        "key menu: Invert Keys carries a moved key's claims to its new frame",
        kh.sequencer.ledger.authored(obj="kmInvM") == [(m_key, 25.0)]
        and (25.0, 2.0) in pts(inv_m),
        f"{pts(inv_m)} {kh.sequencer.ledger.to_dict()}",
    )
    al_m = keyed_x("kmAlM", [(10, 1.0), (20, 2.0)])
    keyed_x("kmAlN", [(5, 0.0), (30, 1.0)])  # its selection starts the block at 5
    am_key = _SSI._fc_key("kmAlM", fc_of(al_m))
    kh.sequencer.ledger.record_authored(am_key, 20.0, 0, "fade_out", "kmAlM")
    kh._align_selected_keys(
        [
            ("kmAlM", "translateX", [10.0, 20.0], None),
            ("kmAlN", "translateX", [5.0], None),
        ]
    )
    check(
        "key menu: Align Keys carries a moved key's claims to its new frame",
        [t for t, _v in pts(al_m)] == [5.0, 15.0]
        and kh.sequencer.ledger.authored(obj="kmAlM") == [(am_key, 15.0)],
        f"{pts(al_m)} {kh.sequencer.ledger.to_dict()}",
    )

    thin = keyed_x("kmThin", [(0, 0.0), (10, 1.0), (20, 3.0), (30, 2.0), (40, 0.0)])
    kh._thin_selected_keys([("kmThin", "translateX", [10.0, 20.0, 30.0], None)])
    check(
        "key menu: Remove Intermediate Keys keeps the selection's own end keys",
        [t for t, _v in pts(thin)] == [0.0, 10.0, 30.0, 40.0],
        f"{pts(thin)}",
    )
    # A key a delete edit removes takes its claims with it, as a replaced one
    # does: left on the frame, a behavior's claim passed to the next key there.
    km_tc = keyed_x("kmThinC", [(0, 0.0), (10, 1.0), (20, 3.0)])
    tc_key = _SSI._fc_key("kmThinC", fc_of(km_tc))
    kh.sequencer.ledger.record_authored(tc_key, 10.0, 0, "fade_in", "kmThinC")
    kh._thin_selected_keys([("kmThinC", "translateX", [0.0, 10.0, 20.0], None)])
    check(
        "key menu: Remove Intermediate Keys releases the claims of the keys it removes",
        [t for t, _v in pts(km_tc)] == [0.0, 20.0]
        and not kh.sequencer.ledger.owns_any(tc_key, 10.0),
        f"{pts(km_tc)} {kh.sequencer.ledger.to_dict()}",
    )
    km_sc = keyed_x("kmSimpC", [(0, 0.0), (10, 1.0), (20, 2.0)])  # 10: no shape
    sc_key = _SSI._fc_key("kmSimpC", fc_of(km_sc))
    kh.sequencer.ledger.record_authored(sc_key, 10.0, 0, "fade_in", "kmSimpC")
    kh._simplify_selected_keys([("kmSimpC", "translateX", [0.0, 10.0, 20.0], None)])
    check(
        "key menu: Simplify releases the claims of a key the shape pass removes",
        [t for t, _v in pts(km_sc)] == [0.0, 20.0]
        and not kh.sequencer.ledger.owns_any(sc_key, 10.0),
        f"{pts(km_sc)} {kh.sequencer.ledger.to_dict()}",
    )
    km_hc = keyed_x("kmHoldC", [(0, 1.0), (10, 1.0), (20, 1.0), (30, 0.0)])
    hc_key = _SSI._fc_key("kmHoldC", fc_of(km_hc))
    kh.sequencer.ledger.record_authored(hc_key, 10.0, 0, "fade_in", "kmHoldC")
    kh._simplify_selected_keys(
        [("kmHoldC", "translateX", [0.0, 10.0, 20.0, 30.0], None)]
    )
    check(
        "key menu: Simplify releases the claims of a hold key the flat pass removes",
        10.0 not in [t for t, _v in pts(km_hc)]
        and not kh.sequencer.ledger.owns_any(hc_key, 10.0),
        f"{pts(km_hc)} {kh.sequencer.ledger.to_dict()}",
    )

    flat = keyed_x("kmFlat", [(0, 0.0), (10, 5.0), (20, 0.0)])
    kp_mid = fc_of(flat).keyframe_points[0]
    for kp in fc_of(flat).keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "AUTO"
    fc_of(flat).keyframe_points[2].co = (20.0, 9.0)
    fc_of(flat).update()
    kh._set_key_tangents([("kmFlat", "translateX", [10.0], None)], "FLAT")
    kp_mid = next(k for k in fc_of(flat).keyframe_points if abs(k.co[0] - 10) < 1e-3)
    check(
        "key menu: Flat tangent levels both handles on the key's value",
        abs(kp_mid.handle_left[1] - 5.0) < 1e-4
        and abs(kp_mid.handle_right[1] - 5.0) < 1e-4
        and kp_mid.handle_left[0] < 10.0 < kp_mid.handle_right[0],
        f"hl={tuple(kp_mid.handle_left)} hr={tuple(kp_mid.handle_right)}",
    )

    # One side: that handle goes level and FREE, and an ALIGNED partner is
    # freed where it sits -- left aligned, it would swing level with it.
    one = keyed_x("kmFlatOut", [(0, 0.0), (10, 5.0), (20, 9.0)])
    for kp in fc_of(one).keyframe_points:
        kp.handle_left_type = kp.handle_right_type = "ALIGNED"
        kp.interpolation = "LINEAR"
    fc_of(one).update()
    kp_one = next(k for k in fc_of(one).keyframe_points if abs(k.co[0] - 10) < 1e-3)
    one_left = tuple(round(v, 4) for v in kp_one.handle_left)
    kh._set_key_tangents([("kmFlatOut", "translateX", [10.0], None)], "FLAT", ("out",))
    kp_one = next(k for k in fc_of(one).keyframe_points if abs(k.co[0] - 10) < 1e-3)
    check(
        "key menu: a one-sided Flat levels that handle alone, the partner freed in place",
        abs(kp_one.handle_right[1] - 5.0) < 1e-4
        and kp_one.handle_right[0] > 10.0
        and (kp_one.handle_left_type, kp_one.handle_right_type) == ("FREE", "FREE")
        and tuple(round(v, 4) for v in kp_one.handle_left) == one_left
        and kp_one.interpolation == "BEZIER",
        f"hl={tuple(kp_one.handle_left)} (was {one_left}) hr={tuple(kp_one.handle_right)} "
        f"types={kp_one.handle_left_type}/{kp_one.handle_right_type} {kp_one.interpolation}",
    )

    uni = keyed_x("kmUni", [(0, 0.0), (10, 5.0), (20, 0.0)])
    pts_u = fc_of(uni).keyframe_points
    pts_u[0].handle_left_type = pts_u[0].handle_right_type = "FREE"
    pts_u[1].handle_left_type = pts_u[1].handle_right_type = "AUTO_CLAMPED"
    kh._lock_key_tangents([("kmUni", "translateX", [0.0, 10.0], None)], True)
    pts_u = fc_of(uni).keyframe_points
    check(
        "key menu: Unify aligns free handles and leaves auto handles auto",
        (pts_u[0].handle_left_type, pts_u[0].handle_right_type)
        == ("ALIGNED", "ALIGNED")
        and (pts_u[1].handle_left_type, pts_u[1].handle_right_type)
        == ("AUTO_CLAMPED", "AUTO_CLAMPED"),
        f"{[(k.handle_left_type, k.handle_right_type) for k in pts_u]}",
    )

    cp = keyed_x("kmCopy", [(0, 0.0), (10, 5.0), (20, 0.0)])
    src_kp = fc_of(cp).keyframe_points[1]
    src_kp.interpolation = "CONSTANT"
    src_kp.handle_left_type = src_kp.handle_right_type = "FREE"
    src_kp.handle_right = (13.0, 7.0)
    fc_of(cp).update()
    kh._copy_selected_keys([("kmCopy", "translateX", [10.0], None)])
    bpy.context.scene.frame_set(30)
    kh._paste_selected_keys([("kmCopy", "translateX", [10.0], None)])
    pasted = next(
        (k for k in fc_of(cp).keyframe_points if abs(k.co[0] - 30) < 1e-3), None
    )
    check(
        "key menu: pasted keys keep their interpolation and handles (tangent detail)",
        pasted is not None
        and pasted.interpolation == "CONSTANT"
        and pasted.handle_right_type == "FREE"
        and (round(pasted.handle_right[0], 3), round(pasted.handle_right[1], 3))
        == (33.0, 7.0),
        f"{pasted and (pasted.interpolation, pasted.handle_right_type, tuple(pasted.handle_right))}",
    )
    # Paste over a claimed key replaces it, and its claims go with it (mirror
    # of mayatk's TestKeySelectionEdits): left on the frame, a behavior's claim
    # passed to the pasted key, which the next Build then deleted as its own.
    km_ps = keyed_x("kmPaste", [(0, 0.0), (20, 2.0), (40, 5.0)])
    ps_key = _SSI._fc_key("kmPaste", fc_of(km_ps))
    kh.sequencer.ledger.record_authored(ps_key, 20.0, 0, "fade_in", "kmPaste")
    kh._copy_selected_keys([("kmPaste", "translateX", [40.0], None)])
    bpy.context.scene.frame_set(20)
    kh._paste_selected_keys([("kmPaste", "translateX", [40.0], None)])
    check(
        "key menu: Paste Keys releases the claims of a key it replaces",
        pts(km_ps) == [(0.0, 0.0), (20.0, 5.0), (40.0, 5.0)]
        and not kh.sequencer.ledger.owns_any(ps_key, 20.0),
        f"{pts(km_ps)} {kh.sequencer.ledger.to_dict()}",
    )
    # Copy a gap-held seam: the hold's CONSTANT is the system's, so the key the
    # hold stepped is copied with its pre-hold interpolation.  A key written
    # over the held one since (a Graph Editor paste) is the animator's, its
    # CONSTANT with it -- mirror of mayatk's ``is_same_key`` copy guard.  The
    # pasted key here differs in value AND easing: Blender's in side is the
    # easing, and with the claim's easing ``is_same_key`` matches on it alone.
    km_gh = keyed_x("kmGapHold", [(0, 0.0), (20, 9.0), (40, 1.0)])
    gh_key = _SSI._fc_key("kmGapHold", fc_of(km_gh))

    def gh_point():
        return next(k for k in fc_of(km_gh).keyframe_points if abs(k.co[0] - 20) < 1e-3)

    def gh_copied():
        kh._copy_selected_keys([("kmGapHold", "translateX", [20.0], None)])
        buf = kh._copied_keys.get("kmGapHold") or {}
        tans = buf.get("tangents", {}).get(("location", 0), [])
        return [t["interpolation"] for t in tans]

    gh_kp = gh_point()
    kh.sequencer.ledger.record_step(
        gh_key, 20.0, gh_kp.easing, gh_kp.interpolation, gh_kp.co[1]
    )
    gh_kp.interpolation = "CONSTANT"
    held_copy = gh_copied()
    check(
        "key menu: Copy gives a held seam back its pre-hold interpolation",
        held_copy == ["BEZIER"],
        f"{held_copy}",
    )
    gh_kp = gh_point()
    gh_kp.co = (20.0, 3.0)
    gh_kp.easing = "EASE_IN"
    fc_of(km_gh).update()
    over_copy = gh_copied()
    check(
        "key menu: Copy keeps the CONSTANT of a key written over a held seam since",
        over_copy == ["CONSTANT"] and kh.sequencer.ledger.owns_step(gh_key, 20.0),
        f"{over_copy} {kh.sequencer.ledger.to_dict()}",
    )

    # -- key selection mirror: scene-wide clear + channel scope -----------------
    other = keyed_x("ksOther", [(0, 0.0), (10, 1.0)])
    for kp in fc_of(other).keyframe_points:
        kp.select_control_point = True
    mine = keyed_x("ksMine", [(0, 0.0), (10, 1.0), (20, 2.0)])
    for f in (0, 20):
        mine.location.y = f * 0.1
        mine.keyframe_insert("location", index=1, frame=f)
    mine_y = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(mine)
        if fc.data_path == "location" and fc.array_index == 1
    )
    # New fcurves start SELECTED in Blender: clear the flags so the check
    # below measures the mirror, not the default.
    fc_of(mine).select = False
    mine_y.select = True

    class _KsClip:
        data = {"obj": "ksMine", "attr_name": "translateX", "shot_id": None}

    class _KsWidget:
        @staticmethod
        def get_clip(_cid):
            return _KsClip()

    ks = _KeyHost(sq)
    ks._get_sequencer_widget = lambda: _KsWidget()
    from blendertk.core_utils._core_utils import CoreUtils as _CU

    with _CU.window_context_override():
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        other.select_set(True)
    ks.on_key_selection_changed([{"clip_id": 1, "times": [10.0]}])
    with _CU.window_context_override():
        picked = {o.name for o in bpy.context.selected_objects}
    check(
        "key selection: the keys' object joins the object selection -- the "
        "Graph Editor lists only selected objects' curves (2026-10-07)",
        picked == {"ksOther", "ksMine"},
        f"{picked}",
    )
    check(
        "key selection: keys selected on OTHER objects are cleared (selectKey clear)",
        not any(kp.select_control_point for kp in fc_of(other).keyframe_points),
    )
    check(
        "key selection: the picked keys' channel becomes the channel selection",
        fc_of(mine).select
        and not mine_y.select
        and [kp.select_control_point for kp in fc_of(mine).keyframe_points]
        == [False, True, False],
        f"x={fc_of(mine).select} y={mine_y.select}",
    )

    with _CU.window_context_override():
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        mine.select_set(True)
    ks._select_and_show(["no such object"])
    with _CU.window_context_override():
        still = [o.name for o in bpy.context.selected_objects]
    check(
        "selection: a click with nothing to select keeps the scene selection",
        still == ["ksMine"],
        f"{still}",
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
    # curve changed).
    st, sq, obs = fresh({"mgA": {0: 0.0, 10: 4.0, 50: 9.0, 55: 2.0}})
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
    sq.merge_shots([mg_a.shot_id, mg_b.shot_id])  # no gap: the hold on 20 goes
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

    # -- a point clip drag moves what its mark was drawn from (BACKLOG 2026-10-04)
    # A point (stepped) clip is a MARK: an animator's key on a member that holds
    # still in its shot, drawn from its content channels, CONSTANT or not.  The
    # drag moved every fcurve keyed on that frame -- a custom property the mark
    # never drew included -- where mayatk moved only stepped keys; both now move
    # the mark's own keys (pythontk ``ShotSequencer.move_stepped_keys``).  Each
    # curve has an equal key far outside the shots: a single-key curve is a
    # zero-length SPAN, not a mark.
    st, sq, obs = fresh({"ptMover": {0: 0.0, 50: 10.0, 60: 0.0, 100: 5.0}})
    bpy.ops.mesh.primitive_cube_add()
    pt = bpy.context.active_object
    pt.name = "ptHold"
    pt["note"] = 1.0
    for f in (25, 300):
        pt.location.y = 3.0
        pt.keyframe_insert(data_path="location", index=1, frame=f)
        pt.keyframe_insert(data_path="hide_viewport", frame=f)
        pt.keyframe_insert(data_path='["note"]', frame=f)
    pt_s0 = sq.define_shot("PT0", 0, 50, objects=["ptMover", "ptHold"])
    sq.define_shot("PT1", 60, 100, objects=["ptMover"])
    pt_drawn = [
        (s["start"], bool(s.get("is_stepped")))
        for s in sq.collect_object_segments(pt_s0.shot_id)
        if s["obj"] == "ptHold"
    ]
    pt_host = _KeysHost(
        _FakeWidget(
            _FakeClip(
                {
                    "obj": "ptHold",
                    "shot_id": pt_s0.shot_id,
                    "orig_start": 25.0,
                    "orig_end": 25.0,
                    "is_stepped": True,
                    "stepped_key_time": 25.0,
                }
            )
        ),
        sq,
    )
    pt_host._shifted_out_keys = {}
    pt_host.on_clip_moved(1, 30.0)

    def pt_times(path, index=0):
        fc = next(
            c
            for c in BlenderShotStore.iter_action_fcurves(pt)
            if c.data_path == path and c.array_index == index
        )
        return sorted(round(kp.co[0], 3) for kp in fc.keyframe_points)

    pt_y, pt_hide = pt_times("location", 1), pt_times("hide_viewport")
    pt_note = pt_times('["note"]')
    check(
        "point clip: the drag moves the mark's own keys, no channel it never drew",
        pt_drawn == [(25.0, True)]
        and pt_y == [30.0, 300.0]
        and pt_hide == [30.0, 300.0]
        and pt_note == [25.0, 300.0],
        f"drawn={pt_drawn} y={pt_y} hide={pt_hide} note={pt_note}",
    )

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
    check(
        "reconcile: the claims and the hidden flag follow the rename",
        sq.ledger.key_records(_SSI._fc_key("rnNew", fc_of(obs["rnOld"])))
        == [(10.0, rn_shot.shot_id, "end")]
        and not sq.ledger.key_records(rn_key)
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
    bone_held = sq.ledger.owns_step(f'rfRig|pose.bones["{rf_bone}"].location|0', 10.0)
    rf_rig.data.bones[rf_bone].name = "rfBone_L"
    ShotSequencer(store=st).respace(gap=0.0, start_frame=0.0)
    bone_fc = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(rf_rig)
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
    st, sq, obs = fresh({"unA": {0: 0, 10: 5}})
    un_shot = sq.define_shot("U", 0, 10, objects=["unA"])

    class _UndoHost(_Ctrl):
        def __init__(self, seq):  # bypass the panel's __init__
            import logging

            self._segment_cache = {}
            self._sub_row_cache = {}
            self._audio_segments_cache = None
            self._syncing = False
            self._syncing_playhead = False  # read by the frame-change handler
            self._playback_range_mode = "off"  # the post-undo refresh reads it
            self._playhead_to_shot_start = True  # the header option's default
            self._handlers = []
            self.sequencer = seq
            self.logger = logging.getLogger("test.undo_host")

        def _on_depsgraph_update(self, *_a):  # no Qt debounce here
            pass

        def _sync_to_widget(self, **kw):
            pass

        def _sync_combobox(self):
            pass

        def _get_sequencer_widget(self):
            return None

    uh = _UndoHost(sq)
    uh._register_scene_callbacks()
    try:
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
            len(st._boundary_undo) == depth
            and BlenderShotStore.edit_serial() == tag[1],
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

        # The panel's undo with an unrelated step on top: same contract.
        # (Memfile undo replaced the object: fetch it again, never reuse it.)
        bpy.data.objects["unA"].location.z = 7.0
        bpy.ops.ed.undo_push(message="unrelated 2")
        uh.on_undo()
        check(
            "panel undo: an unrelated step on top is undone alone, bounds kept",
            sq.shot_by_id(un_shot.shot_id).end == 20.0,
            f"end={sq.shot_by_id(un_shot.shot_id).end}",
        )
        uh.on_undo()
        check(
            "panel undo: then our step, bounds restored",
            sq.shot_by_id(un_shot.shot_id).end == 10.0,
            f"end={sq.shot_by_id(un_shot.shot_id).end}",
        )

        # One-click New Shot is its own undo step, paired with its point.
        n_before = len(sq.sorted_shots())
        uh._create_shot_one_click = _Ctrl._create_shot_one_click.__get__(uh)
        uh.ui = None
        uh.select_shot = lambda *_a, **_k: None
        uh._set_footer = lambda *_a, **_k: None
        bpy.context.scene.frame_set(1)
        uh._create_shot_one_click()
        made = len(sq.sorted_shots())
        newest = max(sq.sorted_shots(), key=lambda s: s.shot_id)
        check(
            "one-click New Shot: the playhead lands on its start (2026-10-06)",
            bpy.context.scene.frame_current == int(newest.start),
            f"frame={bpy.context.scene.frame_current} start={newest.start}",
        )
        bpy.ops.ed.undo()
        check(
            "one-click New Shot: a native undo removes exactly the new shot",
            made == n_before + 1 and len(sq.sorted_shots()) == n_before,
            f"{n_before} -> {made} -> {len(sq.sorted_shots())}",
        )

        # A shot change lands the playhead too, behind the header's Playhead
        # to Shot Start (mayatk 2026-10-07); the shot already active does not.
        later = sq.define_shot("U2", 40, 60, objects=[])
        uh._shifted_out_keys = {}
        uh._update_shot_nav_state = lambda: None
        uh.select_shot = st.set_active_shot
        st.set_active_shot(un_shot.shot_id)
        bpy.context.scene.frame_set(1)
        uh._go_to_shot(un_shot.shot_id)
        stayed = bpy.context.scene.frame_current
        uh._go_to_shot(later.shot_id)
        check(
            "shot change: the playhead lands on the shot's start, not on a re-pick",
            stayed == 1 and bpy.context.scene.frame_current == 40,
            f"re-pick={stayed} change={bpy.context.scene.frame_current}",
        )
        uh._set_playhead_to_shot_start(False)
        bpy.context.scene.frame_set(1)
        uh._go_to_shot(un_shot.shot_id)
        check(
            "shot change with Playhead to Shot Start off: the playhead stays",
            bpy.context.scene.frame_current == 1
            and st.active_shot_id == un_shot.shot_id,
            f"frame={bpy.context.scene.frame_current}",
        )
    finally:
        uh._unregister_scene_callbacks()

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
    uh = _UndoHost(sq)
    uh._register_scene_callbacks()
    try:
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
    finally:
        uh._unregister_scene_callbacks()

    # -- a key edit that raises keeps its restore point (BTK-SHOTS-8) ----------
    # ``scene_edit`` has pushed and tagged its step by then: dropped, the point
    # left an undo of that step reverting the keys but not the bounds.
    st, sq, obs = fresh({"kseA": {0: 0, 10: 5}})
    kse_shot = sq.define_shot("KS", 0, 10, objects=["kseA"])
    kse_host = _UndoHost(sq)
    kse_depth = len(st._boundary_undo)

    def _kse_boom():
        st.update_shot(kse_shot.shot_id, end=20.0)  # wrote, then failed
        raise RuntimeError("boom")

    try:
        kse_host._key_scene_edit("Boom", _kse_boom)
        kse_raised = None
    except RuntimeError as exc:
        kse_raised = exc
    kse_kept = len(st._boundary_undo) == kse_depth + 1 and isinstance(
        st.peek_boundary_tag(), tuple
    )
    st.restore_boundary_snapshot()
    check(
        "key edit: a raise keeps the restore point its pushed step pairs with",
        kse_raised is not None
        and kse_kept
        and sq.shot_by_id(kse_shot.shot_id).end == 10.0,
        f"raised={kse_raised!r} kept={kse_kept} end={sq.shot_by_id(kse_shot.shot_id).end}",
    )

    # -- a rename / a strip edit rebuilds, never runs the keying epilogue -------
    # (BTK-SHOTS-4) The keyframe debounce's epilogue adds keyed objects to the
    # active shot and falls back to the SELECTION when nothing was keyed --
    # which a rename and a Sequencer strip edit never are.
    import types

    import pythontk as ptk

    from blendertk.audio_utils._audio_utils import AudioUtils as _AU

    def _selected_nonmember_scene():
        """A shot holding reMem, active, with reSel -- keyed, not a member --
        selected: what the keying epilogue's selection fallback would add."""
        st, sq, obs = fresh({"reMem": {0: 0, 10: 5}, "reSel": {0: 1, 10: 3}})
        shot = sq.define_shot("RE", 0, 10, objects=["reMem"])
        st.set_active_shot(shot.shot_id)
        for ob in bpy.context.scene.objects:
            ob.select_set(ob.name == "reSel")
        return sq, obs, shot

    class _RebuildHost(_UndoHost):
        def __init__(self, seq):
            super().__init__(seq)
            self.armed = []
            self.ui = None
            self._cmb_mode = "shots"
            self._edited_objects = set()
            self._reconcile_needed = False
            self._extend_to_keys = False

        def _arm_keyframe_debounce(self):
            self.armed.append("_on_keyframe_debounce_fire")

        def _arm_rebuild_debounce(self):
            self.armed.append("_on_rebuild_debounce_fire")

        def fire(self):
            """Run what the armed timers would, once each."""
            fired, self.armed = list(self.armed), []
            for name in fired:
                getattr(self, name)()
            return fired

    _on_depsgraph = _Ctrl._on_depsgraph_update  # _UndoHost stubs it out

    sq, obs, re_shot = _selected_nonmember_scene()
    re_host = _RebuildHost(sq)
    obs["reMem"].name = "reMemRenamed"
    re_host._on_object_renamed()
    re_fired = re_host.fire()
    check(
        "rename: a plain rebuild, and the selection joins no shot",
        re_fired == ["_on_rebuild_debounce_fire"]
        and re_host._reconcile_needed
        and "reSel" not in sq.shot_by_id(re_shot.shot_id).objects,
        f"fired={re_fired} members={sq.shot_by_id(re_shot.shot_id).objects}",
    )

    # The Sequencer's strips: an edit there is seen (and the baseline is not).
    sq, obs, re_shot = _selected_nonmember_scene()
    with ptk.TempArtifacts(prefix="btk_seq_strip_") as strip_tmp:
        import wave

        strip_wav = str(strip_tmp.path(".wav"))
        with wave.open(strip_wav, "wb") as fh:  # a tenth of a second of silence
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00" * 4410)
        strip_host = _RebuildHost(sq)
        strip_host._note_sound_strips()
        strip_baseline = strip_host._sound_strips_changed()
        _AU.add_clip(strip_wav, frame_start=5, name="reStrip")
        fake_dg = types.SimpleNamespace(
            updates=[types.SimpleNamespace(id=bpy.context.scene)]
        )
        _on_depsgraph(strip_host, bpy.context.scene, fake_dg)
        strip_fired = strip_host.fire()
        strip_again = strip_host._sound_strips_changed()
        _AU.remove_all_clips()
    check(
        "strip watch: an external strip edit is one change, the baseline none",
        strip_baseline is False and strip_again is False,
        f"baseline={strip_baseline} again={strip_again}",
    )
    check(
        "strip edit: a plain rebuild, and the selection joins no shot",
        strip_fired == ["_on_rebuild_debounce_fire"]
        and "reSel" not in sq.shot_by_id(re_shot.shot_id).objects,
        f"fired={strip_fired} members={sq.shot_by_id(re_shot.shot_id).objects}",
    )

    # Past the banked-object cap the burst is a bake / import: the keying
    # epilogue scans the selection instead (mirror of mayatk's curve cap).
    sq, obs, re_shot = _selected_nonmember_scene()
    cap_host = _RebuildHost(sq)
    cap_host._edited_objects = {
        f"ghost{i}" for i in range(cap_host._EDITED_OBJECT_CAP + 1)
    }
    cap_added = cap_host._auto_add_keyed_objects(re_shot.shot_id)
    check(
        "keying epilogue: past the banked cap it falls back to the selection",
        cap_added
        and "reSel" in sq.shot_by_id(re_shot.shot_id).objects
        and not cap_host._edited_objects,
        f"added={cap_added} members={sq.shot_by_id(re_shot.shot_id).objects}",
    )

    # -- the view's playback range is the PREVIEW range, never the render range --
    pr_st, pr_sq, _pr_obs = fresh({})
    pr_a = pr_sq.define_shot("PA", 10, 40)
    pr_sq.define_shot("PB", 50, 90)
    pr_scene = bpy.context.scene
    pr_render = (pr_scene.frame_start, pr_scene.frame_end)
    pr_host = _RebuildHost(pr_sq)
    pr_host._shot_display_mode = "adjacent"
    ranges = {}
    for mode in ("locked", "follows_view"):
        pr_host._playback_range_mode = mode
        pr_host._apply_view_playback_range(pr_a)
        ranges[mode] = (
            pr_scene.use_preview_range,
            pr_scene.frame_preview_start,
            pr_scene.frame_preview_end,
        )
    check(
        "playback range: locked spans the shot, follows-view the visible shots, "
        "both on the preview range",
        ranges == {"locked": (True, 10, 40), "follows_view": (True, 10, 90)}
        and (pr_scene.frame_start, pr_scene.frame_end) == pr_render,
        f"{ranges} render={(pr_scene.frame_start, pr_scene.frame_end)} was {pr_render}",
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
