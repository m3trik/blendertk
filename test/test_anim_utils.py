"""blendertk.anim_utils._anim_utils.AnimUtils -- headless test.
Run: blender --background --factory-startup --python blendertk/test/test_anim_utils.py

The primitives other suites lean on through feature tests get their own
checks here (one test file per module).
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [
    os.path.dirname(HERE),
    os.path.join(os.path.dirname(os.path.dirname(HERE)), "pythontk"),
]

lines = []
passed = 0


def check(name, cond, detail=""):
    global passed
    ok = bool(cond)
    passed += ok
    lines.append(
        ("OK   " if ok else "FAIL ")
        + name
        + ((" | " + detail) if detail and not ok else "")
    )


try:
    import bpy
    from blendertk.anim_utils._anim_utils import AnimUtils

    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.context.scene.collection

    def empty(name, loc=(0, 0, 0)):
        o = bpy.data.objects.new(name, None)
        o.location = loc
        coll.objects.link(o)
        return o

    # ---- evaluable_override: a hidden object with KEYED visibility is not
    # evaluated by the depsgraph, so its matrix_world freezes at the last frame
    # it was seen; inside the scope it moves with its keys, and the hide state
    # is back afterwards.
    mover = empty("mover")
    for f, loc in ((1, (0, 0, 0)), (10, (5, 0, 0))):
        mover.location = loc
        mover.keyframe_insert("location", frame=f)
    mover.hide_viewport = True
    mover.keyframe_insert("hide_viewport", frame=1)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(10)
    bpy.context.view_layer.update()
    frozen = tuple(mover.matrix_world.translation)
    check(
        "precondition: a hide-keyed object freezes (Blender does not evaluate it)",
        abs(frozen[0] - 5.0) > 1e-3,
        f"frozen={frozen}",
    )
    bpy.context.scene.frame_set(1)
    with AnimUtils.evaluable_override([mover]):
        bpy.context.scene.frame_set(10)
        moved = tuple(mover.matrix_world.translation)
    check(
        "evaluable_override: the object evaluates at another frame",
        abs(moved[0] - 5.0) < 1e-4,
        f"moved={moved}",
    )
    check(
        "evaluable_override: hide keys are unmuted and the flag restored on exit",
        all(
            not fc.mute
            for fc in AnimUtils.get_fcurves(mover)
            if fc.data_path == "hide_viewport"
        )
        and mover.hide_viewport is True,
        f"hide_viewport={mover.hide_viewport}",
    )

    # ---- the retired "unbake" optimize level (renamed "extremes" 2026-09-02)
    # resolves through ptk.Deprecation.values: it still lands on the live
    # level, and now it SAYS so and names the release it stops working in.
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        level = AnimUtils.normalize_optimize_level("  Unbake ")
    notices = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    check(
        "retired 'unbake' level: resolves to 'extremes' and warns",
        level == "extremes"
        and len(notices) == 1
        and "blendertk 0.11.0" in str(notices[0].message),
        f"level={level!r} notices={[str(w.message) for w in notices]}",
    )
    check(
        "retired AnimUtils.unbake_keys alias stays removed (2026-09-21)",
        not hasattr(AnimUtils, "unbake_keys"),
    )
except Exception as e:  # noqa: BLE001
    traceback.print_exc()
    lines.append("FAIL test raised | " + repr(e))

for line in lines:
    print(line)
print(
    f"===RESULT: {'PASS' if all(ln.startswith('OK') for ln in lines) else 'FAIL'}=== "
    f"({passed}/{len(lines)})"
)
sys.stdout.flush()
