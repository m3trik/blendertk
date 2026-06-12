# !/usr/bin/python
# coding=utf-8
"""Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
anim helpers where names align: ``stagger_keys``, ``invert_keys``, ``scale_keys``, …).

Key timing is plain math over keyframe coordinates — not DCC-specific (the plan's §5 finding) —
so these are **headless-testable**. ``import bpy`` is deferred into call bodies (no import side
effects).
"""
import pythontk as ptk


def _slot_fcurves(action, slot=None):
    """The fcurves of ``action`` (slot-aware).

    Blender 4.4+ actions are *slotted/layered* — 5.x drops the legacy flat ``action.fcurves``
    entirely, so keys live in per-slot channelbags (``layers → strips → channelbag(slot)``).
    Falls back to the legacy accessor on older builds.
    """
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        return list(legacy)
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            if slot is not None:
                cb = strip.channelbag(slot)
                if cb is not None:
                    out.extend(cb.fcurves)
            else:
                out.extend(fc for cb in strip.channelbags for fc in cb.fcurves)
    return out


def _actions(objects):
    """Unique ``(action, slot)`` pairs across the given objects' animation data."""
    seen = []
    for o in ptk.make_iterable(objects):
        ad = getattr(o, "animation_data", None)
        action = ad.action if ad else None
        if action is not None and all(action is not a for a, _s in seen):
            seen.append((action, getattr(ad, "action_slot", None)))
    return seen


def get_fcurves(objects):
    """All fcurves across the given objects' actions (slot-aware; public for slot code/tests)."""
    return [fc for action, slot in _actions(objects) for fc in _slot_fcurves(action, slot)]


_fcurves = get_fcurves  # internal alias


def _key_range(fcurves):
    """(min, max) key frame across ``fcurves``, or None when keyless."""
    frames = [k.co.x for fc in fcurves for k in fc.keyframe_points]
    return (min(frames), max(frames)) if frames else None


def _shift_fcurves(fcurves, offset):
    """Shift every key (and its handles) of ``fcurves`` by ``offset`` frames."""
    for fc in fcurves:
        for k in fc.keyframe_points:
            k.co.x += offset
            k.handle_left.x += offset
            k.handle_right.x += offset
        fc.update()


def shift_keys(objects, offset):
    """Shift every key of the given objects by ``offset`` frames."""
    _shift_fcurves(_fcurves(objects), offset)


def move_keys_to_frame(objects, frame=None, retain_spacing=True):
    """Move the objects' keys so they align to ``frame`` (default: the current frame).

    ``retain_spacing=True`` applies one global offset — the earliest key across the selection
    lands on ``frame`` and relative timing between objects is kept; ``False`` aligns each
    action's own first key to ``frame``. Returns the number of keyed actions (an already-
    aligned action counts — it is at the target).
    """
    import bpy

    if frame is None:
        frame = bpy.context.scene.frame_current
    pairs = []
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if rng:
            pairs.append((fcurves, rng))
    if not pairs:
        return 0
    global_offset = frame - min(rng[0] for _fc, rng in pairs)
    for fcurves, rng in pairs:
        offset = global_offset if retain_spacing else frame - rng[0]
        if offset:
            _shift_fcurves(fcurves, offset)
    return len(pairs)


def adjust_key_spacing(objects, spacing=1, frame=None):
    """Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
    frame) — every key at/after ``frame`` shifts by ``spacing``; mirror of
    ``mtk.adjust_key_spacing``. Negative spacing larger than the gap can collide keys with
    the ones before ``frame`` (as in Maya without preserve-keys). Returns keys shifted."""
    import bpy

    if frame is None:
        frame = bpy.context.scene.frame_current
    moved = 0
    for fc in _fcurves(objects):
        for k in fc.keyframe_points:
            if k.co.x >= frame:
                k.co.x += spacing
                k.handle_left.x += spacing
                k.handle_right.x += spacing
                moved += 1
        fc.update()
    return moved


def align_selected_keyframes(objects, target_frame=None, use_earliest=True):
    """Move the SELECTED keyframes (``select_control_point``, e.g. picked in the Dope Sheet /
    Graph Editor) to one frame — mirror of ``mtk.align_selected_keyframes``. Auto target =
    the earliest (or latest) selected frame. Returns the number of keys moved (0 = none
    selected)."""
    selected = [
        (fc, k)
        for fc in _fcurves(objects)
        for k in fc.keyframe_points
        if k.select_control_point
    ]
    if not selected:
        return 0
    frames = [k.co.x for _fc, k in selected]
    target = (
        target_frame
        if target_frame is not None
        else (min(frames) if use_earliest else max(frames))
    )
    for fc, k in selected:
        delta = target - k.co.x
        k.co.x = target
        k.handle_left.x += delta
        k.handle_right.x += delta
    for fc, _k in selected:
        fc.update()
    return len(selected)


def set_visibility_keys(objects, visible=True, frame=None):
    """Key viewport + render visibility (``hide_viewport``/``hide_render``) at ``frame``
    (default: the current frame) — mirror of ``mtk.set_visibility_keys``. Returns the
    objects keyed."""
    import bpy

    if frame is None:
        frame = bpy.context.scene.frame_current
    keyed = []
    for o in ptk.make_iterable(objects):
        o.hide_viewport = not visible
        o.hide_render = not visible
        o.keyframe_insert("hide_viewport", frame=frame)
        o.keyframe_insert("hide_render", frame=frame)
        keyed.append(o)
    return keyed


def invert_keys(objects):
    """Mirror key times about the center of each object's own key range (reverses the motion)."""
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if not rng:
            continue
        center = (rng[0] + rng[1]) / 2.0
        for fc in fcurves:
            for k in fc.keyframe_points:
                k.co.x = 2.0 * center - k.co.x
                k.handle_left.x = 2.0 * center - k.handle_left.x
                k.handle_right.x = 2.0 * center - k.handle_right.x
            fc.update()


def stagger_keys(objects, spacing=5):
    """Re-time the objects sequentially: each object's keys start ``spacing`` frames after the
    previous object's keys END (mirror of ``mtk`` stagger-keys)."""
    cursor = None
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if not rng:
            continue
        if cursor is None:
            cursor = rng[1] + spacing
            continue
        offset = cursor - rng[0]
        _shift_fcurves(fcurves, offset)
        cursor = rng[1] + offset + spacing


def snap_keys(objects):
    """Snap every key to whole frames."""
    for fc in _fcurves(objects):
        for k in fc.keyframe_points:
            k.co.x = round(k.co.x)
        fc.update()


def scale_keys(objects, factor, pivot=None):
    """Scale key times by ``factor`` about ``pivot`` (defaults to each action's first key)."""
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if not rng:
            continue
        p = rng[0] if pivot is None else pivot
        for fc in fcurves:
            for k in fc.keyframe_points:
                k.co.x = p + (k.co.x - p) * factor
                k.handle_left.x = p + (k.handle_left.x - p) * factor
                k.handle_right.x = p + (k.handle_right.x - p) * factor
            fc.update()


def set_stepped(objects, stepped=True):
    """Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key."""
    interp = "CONSTANT" if stepped else "BEZIER"
    for fc in _fcurves(objects):
        for k in fc.keyframe_points:
            k.interpolation = interp
        fc.update()


def delete_keys(objects):
    """Remove all animation from the given objects. Returns the objects cleared."""
    cleared = []
    for o in ptk.make_iterable(objects):
        if getattr(o, "animation_data", None):
            o.animation_data_clear()
            cleared.append(o)
    return cleared


def fit_playback_range(objects=None):
    """Set the scene frame range to the keyed extent of ``objects`` (or every scene object).

    Returns the (start, end) applied, or None when nothing is keyed.
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else bpy.data.objects
    rng = _key_range(_fcurves(pool))
    if not rng:
        return None
    scene = bpy.context.scene
    scene.frame_start = int(rng[0])
    scene.frame_end = max(int(rng[1]), int(rng[0]))
    return scene.frame_start, scene.frame_end


def copy_keys(source):
    """Return the action carrying ``source``'s keys (the copy buffer for :func:`paste_keys`)."""
    ad = getattr(source, "animation_data", None)
    return ad.action if ad else None


def paste_keys(objects, action):
    """Link a COPY of ``action`` to each target (independent keys, mirror of Maya paste)."""
    pasted = []
    for o in ptk.make_iterable(objects):
        if action is None:
            continue
        if o.animation_data is None:
            o.animation_data_create()
        copy = action.copy()
        o.animation_data.action = copy
        slots = getattr(copy, "slots", None)  # slotted actions need an explicit slot pick
        if slots:
            o.animation_data.action_slot = slots[0]
        pasted.append(o)
    return pasted


class AnimUtils:
    """Namespace mirror (helpers also exposed module-level)."""

    get_fcurves = staticmethod(get_fcurves)
    shift_keys = staticmethod(shift_keys)
    move_keys_to_frame = staticmethod(move_keys_to_frame)
    adjust_key_spacing = staticmethod(adjust_key_spacing)
    align_selected_keyframes = staticmethod(align_selected_keyframes)
    set_visibility_keys = staticmethod(set_visibility_keys)
    invert_keys = staticmethod(invert_keys)
    stagger_keys = staticmethod(stagger_keys)
    snap_keys = staticmethod(snap_keys)
    scale_keys = staticmethod(scale_keys)
    set_stepped = staticmethod(set_stepped)
    delete_keys = staticmethod(delete_keys)
    fit_playback_range = staticmethod(fit_playback_range)
    copy_keys = staticmethod(copy_keys)
    paste_keys = staticmethod(paste_keys)
