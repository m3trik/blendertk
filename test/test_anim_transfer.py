"""blendertk transfer_keyframes headless test — absolute (verbatim) vs relative (value-offset)
transfer, mirroring mtk.AnimUtils.transfer_keyframes's relative semantics (offset computed
against each source fcurve's OWN earliest keyed value, applied per target so each target keeps
its own current pose as the animation's base).
Run: blender --background --factory-startup --python blendertk/test/test_anim_transfer.py
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

    def make_empty(name, x=0.0):
        o = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(o)
        o.location.x = x
        return o

    def key_walk(o, values):
        """Key location.x at frames 1..N to the given values (a 'walk cycle')."""
        for f, v in enumerate(values, start=1):
            o.location.x = v
            o.keyframe_insert("location", index=0, frame=f)

    co = lambda fc: [(round(k.co.x, 3), round(k.co.y, 3)) for k in fc.keyframe_points]
    xfc = lambda o: next(
        fc for fc in btk.get_fcurves([o]) if fc.array_index == 0 and fc.data_path == "location"
    )

    # ---- absolute mode (relative=False, default): targets snap to source's literal values ----
    reset()
    source = make_empty("Source", x=0.0)
    key_walk(source, [0.0, 5.0, 10.0])  # frames 1,2,3
    t1 = make_empty("Target1", x=100.0)  # differently posed
    t2 = make_empty("Target2", x=-50.0)  # differently posed

    pasted = btk.transfer_keyframes([source, t1, t2], relative=False)
    check("absolute: pasted both targets", set(pasted) == {t1, t2}, f"{pasted}")
    check("absolute: target1 gets source's literal values",
          co(xfc(t1)) == [(1, 0), (2, 5), (3, 10)], f"{co(xfc(t1))}")
    check("absolute: target2 gets source's literal values too",
          co(xfc(t2)) == [(1, 0), (2, 5), (3, 10)], f"{co(xfc(t2))}")

    # ---- relative mode: each target keeps its own base pose, offset applied ----
    reset()
    source = make_empty("Source", x=0.0)
    key_walk(source, [0.0, 5.0, 10.0])  # source's own first value = 0.0
    t1 = make_empty("Target1", x=100.0)
    t2 = make_empty("Target2", x=-50.0)
    check("relative: targets start with no animation_data (never keyed before)",
          t1.animation_data is None and t2.animation_data is None)

    pasted = btk.transfer_keyframes([source, t1, t2], relative=True)
    check("relative: pasted both targets", set(pasted) == {t1, t2}, f"{pasted}")
    check("relative: target1 preserves its own base pose (offset +100)",
          co(xfc(t1)) == [(1, 100), (2, 105), (3, 110)], f"{co(xfc(t1))}")
    check("relative: target2 preserves its own DIFFERENT base pose (offset -50)",
          co(xfc(t2)) == [(1, -50), (2, -45), (3, -40)], f"{co(xfc(t2))}")

    # ---- relative offset is computed against the source's OWN first keyed value ----
    reset()
    source = make_empty("Source", x=20.0)
    key_walk(source, [20.0, 25.0, 15.0])  # source's own first value = 20.0
    t1 = make_empty("Target1", x=0.0)
    btk.transfer_keyframes([source, t1], relative=True)
    check("relative: offset uses source's OWN first keyed value (non-zero source base)",
          co(xfc(t1)) == [(1, 0), (2, 5), (3, -5)], f"{co(xfc(t1))}")

    # ---- optimize=True runs the source through optimize_keys first, still transfers ----
    reset()
    source = make_empty("Source", x=0.0)
    key_walk(source, [0.0, 0.0, 0.0, 10.0])  # flat run + one distinct value
    t1 = make_empty("Target1", x=5.0)
    pasted = btk.transfer_keyframes([source, t1], relative=True, optimize=True)
    check("optimize=True runs without error and still pastes", pasted == [t1], f"{pasted}")
    check("optimize=True actually collapsed the source's flat run before transfer",
          co(xfc(source)) == [(1, 0), (3, 0), (4, 10)], f"{co(xfc(source))}")
    check("optimize=True: relative offset applied to the post-optimize (reduced) keys",
          co(xfc(t1)) == [(1, 5), (3, 5), (4, 15)], f"{co(xfc(t1))}")

    # ---- degenerate inputs return [] rather than raising ----
    reset()
    lone = make_empty("Lone")
    check("single object (no targets) returns []", btk.transfer_keyframes([lone]) == [])
    src_no_keys = make_empty("NoKeys")
    tgt = make_empty("Tgt")
    check("source with no keyframes returns []",
          btk.transfer_keyframes([src_no_keys, tgt]) == [])

    # ---- targets get INDEPENDENT actions (mirrors mtk: each target keyed separately) ----
    reset()
    source = make_empty("Source", x=0.0)
    key_walk(source, [0.0, 10.0])
    t1 = make_empty("Target1", x=0.0)
    t2 = make_empty("Target2", x=0.0)
    btk.transfer_keyframes([source, t1, t2], relative=False)
    check("targets get independent actions (not a shared datablock)",
          t1.animation_data.action is not t2.animation_data.action)
    xfc(t1).keyframe_points[0].co.y = 999.0
    check("editing target1's curve doesn't affect target2's curve",
          co(xfc(t2)) == [(1, 0), (2, 10)], f"{co(xfc(t2))}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
