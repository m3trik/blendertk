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

    # ---- a slotted action animates an ID only through its ASSIGNED slot.
    # Assigning an action whose one slot is already another object's leaves
    # the new holder with no slot, and Blender does not animate it -- but the
    # object-scoped readers took "no slot" as "every channelbag", so snap /
    # tie / optimize on the holder edited the OWNER's keys (measured
    # 2026-09-27: optimize counted a 4-curve action on two objects as 7).
    # Added: 2026-09-27
    import blendertk as btk

    owner = empty("slot_owner")
    for f, x in ((1.5, 0.0), (10, 2.0)):
        owner.location = (x, 0, 0)
        owner.keyframe_insert("location", index=0, frame=f)
    shared = owner.animation_data.action
    holder = empty("slotless_holder")
    holder.animation_data_create()
    holder.animation_data.action = shared
    check(
        "precondition: the second holder gets no slot",
        holder.animation_data.action_slot is None,
        f"slot={holder.animation_data.action_slot}",
    )
    bpy.context.scene.frame_set(10)
    check(
        "precondition: Blender does not animate a slotless holder",
        abs(holder.location[0]) < 1e-6 and abs(owner.location[0] - 2.0) < 1e-6,
        f"holder={tuple(holder.location)} owner={tuple(owner.location)}",
    )
    check(
        "get_fcurves: a slotless holder has no fcurves",
        AnimUtils.get_fcurves([holder]) == [],
        f"{[fc.data_path for fc in AnimUtils.get_fcurves([holder])]}",
    )
    check(
        "get_animated_extent: a slotless holder has no animated extent",
        AnimUtils.get_animated_extent([holder]) is None,
        f"{AnimUtils.get_animated_extent([holder])}",
    )
    check(
        "get_animation_info: a slotless holder is not reported",
        AnimUtils.get_animation_info([holder]) == [],
        f"{AnimUtils.get_animation_info([holder])}",
    )
    snapped = AnimUtils.snap_keys([holder])
    btk.scale_keys([holder], factor=2.0)
    AnimUtils.tie_keyframes([holder], frame_range=(0, 20))
    check(
        "snap / scale / tie through a slotless holder leave the owner's keys alone",
        snapped == 0
        and AnimUtils.key_times(AnimUtils.get_fcurves([owner])[0]) == [1.5, 10.0],
        f"snapped={snapped} "
        f"owner={AnimUtils.key_times(AnimUtils.get_fcurves([owner])[0])}",
    )

    # ---- two objects on ONE slot are one set of curves: a per-object pass
    # visited them twice (optimize's stats doubled; a lossy level would thin
    # the same curve twice). Added: 2026-09-27
    twin = empty("slot_twin")
    twin.animation_data_create()
    twin.animation_data.action = shared
    twin.animation_data.action_slot = owner.animation_data.action_slot
    stats = AnimUtils.optimize_keys(
        [owner, twin], remove_static_curves=False, remove_flat_keys=False
    )
    check(
        "optimize_keys: a slot shared by two objects is counted once",
        stats["curves_before"] == 1 and stats["keys_before"] == 2,
        f"{stats}",
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
