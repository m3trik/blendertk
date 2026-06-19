# !/usr/bin/python
# coding=utf-8
"""Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
anim helpers where names align: ``stagger_keys``, ``invert_keys``, ``scale_keys``, …).

Key timing is plain math over keyframe coordinates — not DCC-specific (the plan's §5 finding) —
so these are **headless-testable**. ``import bpy`` is deferred into call bodies (no import side
effects).
"""
import html as _html

import pythontk as ptk

# ``scale_keys`` / ``stagger_keys`` live in their own modules (mirror of mayatk's
# ``anim_utils.scale_keys`` / ``stagger_keys``); they import this module's fcurve helpers lazily
# (inside their call bodies), so importing them here at module top is cycle-safe. Re-exported so the
# namespace mirror (``AnimUtils.scale_keys`` / ``stagger_keys``) keeps resolving.
from blendertk.anim_utils.scale_keys import scale_keys
from blendertk.anim_utils.stagger_keys import stagger_keys


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


def move_keys_to_frame(
    objects, frame=None, retain_spacing=True, selected_keys_only=False, align="auto"
):
    """Move the objects' keys so they align to ``frame`` (default: the current frame).

    ``retain_spacing=True`` applies one global offset — the earliest key across the selection
    lands on ``frame`` and relative timing between objects is kept; ``False`` aligns each
    action's own first key to ``frame``. ``align`` chooses which end of the key range anchors to
    ``frame``: ``"auto"``/``"start"`` use the earliest key, ``"end"`` the latest. With
    ``selected_keys_only`` only the keys selected in the Dope Sheet / Graph Editor move (the
    selected set's chosen end lands on ``frame``); returns keys moved. Otherwise returns the
    number of keyed actions (an already-aligned action counts — it is at the target).
    """
    import bpy

    if frame is None:
        frame = bpy.context.scene.frame_current
    use_end = align == "end"

    if selected_keys_only:
        sel = [
            (fc, k)
            for fc in _fcurves(objects)
            for k in fc.keyframe_points
            if k.select_control_point
        ]
        if not sel:
            return 0
        xs = [k.co.x for _fc, k in sel]
        offset = frame - (max(xs) if use_end else min(xs))
        touched = set()
        for fc, k in sel:
            k.co.x += offset
            k.handle_left.x += offset
            k.handle_right.x += offset
            touched.add(fc)
        for fc in touched:
            fc.update()
        return len(sel)

    pairs = []
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if rng:
            pairs.append((fcurves, rng))
    if not pairs:
        return 0

    def _anchor(rng):
        return rng[1] if use_end else rng[0]

    if retain_spacing:
        global_anchor = (max if use_end else min)(_anchor(rng) for _fc, rng in pairs)
        for fcurves, _rng in pairs:
            offset = frame - global_anchor
            if offset:
                _shift_fcurves(fcurves, offset)
    else:
        for fcurves, rng in pairs:
            offset = frame - _anchor(rng)
            if offset:
                _shift_fcurves(fcurves, offset)
    return len(pairs)


def adjust_key_spacing(
    objects, spacing=1, frame=None, selected_keys_only=False, exact_gap=False
):
    """Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
    frame) — every key at/after ``frame`` shifts by ``spacing``; mirror of
    ``mtk.adjust_key_spacing``. Negative spacing larger than the gap can collide keys with
    the ones before ``frame`` (as in Maya without preserve-keys). Returns keys shifted.

    * ``selected_keys_only`` — only shift keys selected in the Dope Sheet / Graph Editor.
    * ``exact_gap`` — interpret ``spacing`` as a target gap: shift so the first key at/after
      ``frame`` lands exactly at ``frame + spacing`` (clears a precise range), mirror of Maya.
    """
    import bpy

    if frame is None:
        frame = bpy.context.scene.frame_current
    fcurves = _fcurves(objects)

    def _affected(k):
        return k.co.x >= frame and (not selected_keys_only or k.select_control_point)

    if exact_gap:
        candidates = [k.co.x for fc in fcurves for k in fc.keyframe_points if _affected(k)]
        if not candidates:
            return 0
        shift = (frame + spacing) - min(candidates)
    else:
        shift = spacing

    moved = 0
    for fc in fcurves:
        touched = False
        for k in fc.keyframe_points:
            if _affected(k):
                k.co.x += shift
                k.handle_left.x += shift
                k.handle_right.x += shift
                moved += 1
                touched = True
        if touched:
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


_VISIBILITY_PATHS = ("hide_viewport", "hide_render")


def _is_visibility_fcurve(fc):
    """True when ``fc`` drives an object's viewport/render visibility."""
    return fc.data_path in _VISIBILITY_PATHS


def _visibility_key_frames(o, when, frame, offset, scene):
    """Frames to key visibility on ``o`` for the given ``when`` mode (mirror of Maya's cmb002):
    ``current`` (the playhead/``frame``), or — relative to the object's own key range —
    ``start`` / ``end`` / ``both`` / ``before_start`` / ``after_end``. ``[]`` when ``when`` needs
    a key range the object doesn't have."""
    if when == "current":
        base = scene.frame_current if frame is None else frame
        return [base + offset]
    rng = _key_range(get_fcurves([o]))
    if rng is None:
        return []
    lo, hi = rng
    table = {
        "start": [lo],
        "end": [hi],
        "both": [lo, hi],
        "before_start": [lo - 1],
        "after_end": [hi + 1],
    }
    return [f + offset for f in table.get(when, [lo])]


def set_visibility_keys(objects, visible=True, frame=None, when="current", offset=0):
    """Key viewport + render visibility (``hide_viewport``/``hide_render``) — mirror of
    ``mtk.set_visibility_keys``.

    ``when`` chooses the frame(s): ``"current"`` (the playhead / ``frame``) or, relative to each
    object's own keyed range, ``"start"`` / ``"end"`` / ``"both"`` / ``"before_start"`` /
    ``"after_end"``; ``offset`` nudges every chosen frame. Returns the objects keyed (objects with
    no key range are skipped for the range-relative modes)."""
    import bpy

    scene = bpy.context.scene
    keyed = []
    for o in ptk.make_iterable(objects):
        frames = _visibility_key_frames(o, when, frame, offset, scene)
        if not frames:
            continue
        o.hide_viewport = not visible
        o.hide_render = not visible
        for f in frames:
            o.keyframe_insert("hide_viewport", frame=f)
            o.keyframe_insert("hide_render", frame=f)
        keyed.append(o)
    return keyed


def add_intermediate_keys(objects, step=1.0, time_range=None, ignore_visibility=False):
    """Insert sampled keys every ``step`` frames between each fcurve's first and last key
    (existing keys untouched) — mirror of ``mtk.add_intermediate_keys``. Returns keys added.

    * ``time_range`` — a ``(start, end)`` window; only frames inside it get keys.
    * ``ignore_visibility`` — skip ``hide_viewport``/``hide_render`` curves (leave vis keys alone).
    """
    import bisect

    added = 0
    for fc in _fcurves(objects):
        if ignore_visibility and _is_visibility_fcurve(fc):
            continue
        pts = fc.keyframe_points
        if len(pts) < 2:
            continue
        existing = sorted(k.co.x for k in pts)
        start, end = existing[0], existing[-1]
        lo, hi = time_range if time_range is not None else (start, end)
        frames = []
        f = start + step
        while f < end - 1e-6:
            if lo - 1e-6 <= f <= hi + 1e-6:
                i = bisect.bisect_left(existing, f)
                near = any(
                    abs(existing[j] - f) <= 1e-4
                    for j in (i - 1, i)
                    if 0 <= j < len(existing)
                )
                if not near:
                    frames.append(f)
            f += step
        # Sample BEFORE inserting: each insert re-smooths the curve's handles, so
        # evaluating as we go would drift later samples off the original curve.
        samples = [(f, fc.evaluate(f)) for f in frames]
        for f, v in samples:
            pts.insert(f, v)
            added += 1
        fc.update()
    return added


def remove_intermediate_keys(objects, time_range=None, ignore_visibility=False):
    """Remove every key strictly between each fcurve's first and last (keeps only the
    endpoints) — mirror of ``mtk.remove_intermediate_keys``. Returns keys removed.

    * ``time_range`` — a ``(start, end)`` window; only interior keys inside it are removed.
    * ``ignore_visibility`` — skip ``hide_viewport``/``hide_render`` curves.
    """
    removed = 0
    for fc in _fcurves(objects):
        if ignore_visibility and _is_visibility_fcurve(fc):
            continue
        pts = fc.keyframe_points
        if len(pts) <= 2:
            continue
        # Walk interior keys high→low so removals don't shift unvisited indices; endpoints
        # (index 0 and the last) are never touched.
        for i in range(len(pts) - 2, 0, -1):
            x = pts[i].co.x
            if time_range is None or time_range[0] <= x <= time_range[1]:
                pts.remove(pts[i], fast=True)
                removed += 1
        fc.update()
    return removed


def select_keys(objects, time=None, add_to_selection=False):
    """Select keyframe points (``select_control_point`` — visible in the Dope Sheet /
    Graph Editor) — mirror of ``mtk.select_keys``. ``time`` is None for all keys or a
    ``(start, end)`` frame range; ``add_to_selection`` keeps out-of-range keys selected.
    Returns the number of keys selected."""
    selected = 0
    for fc in _fcurves(objects):
        for k in fc.keyframe_points:
            if time is None or time[0] <= k.co.x <= time[1]:
                k.select_control_point = True
                selected += 1
            elif not add_to_selection:
                k.select_control_point = False
    return selected


def invert_keys(objects, mode="time", value_pivot=0.0):
    """Mirror keys to reverse motion — Blender analogue of Maya's invert (modes mirror its X/Y/both).

    ``mode='time'`` (default) flips key *frames* about the center of each object's own key range
    (reverses timing); ``'value'`` flips key *values* about ``value_pivot`` (flips the motion);
    ``'both'`` does both. Handles are mirrored with their keys. Pure ``keyframe_points`` math →
    headless-safe."""
    do_time = mode in ("time", "both")
    do_value = mode in ("value", "both")
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if not rng:
            continue
        center = (rng[0] + rng[1]) / 2.0
        for fc in fcurves:
            for k in fc.keyframe_points:
                if do_time:
                    k.co.x = 2.0 * center - k.co.x
                    k.handle_left.x = 2.0 * center - k.handle_left.x
                    k.handle_right.x = 2.0 * center - k.handle_right.x
                if do_value:
                    k.co.y = 2.0 * value_pivot - k.co.y
                    k.handle_left.y = 2.0 * value_pivot - k.handle_left.y
                    k.handle_right.y = 2.0 * value_pivot - k.handle_right.y
            fc.update()


def snap_keys(objects, selected_only=False, time_range=None):
    """Snap keys to whole frames — mirror of ``mtk.snap_keys_to_frames`` (nearest rounding).

    * ``selected_only`` — only snap keys selected in the Dope Sheet / Graph Editor.
    * ``time_range`` — a ``(start, end)`` window; only keys inside it are snapped.

    Returns the number of keys that actually moved."""
    snapped = 0
    for fc in _fcurves(objects):
        touched = False
        for k in fc.keyframe_points:
            if selected_only and not k.select_control_point:
                continue
            if time_range is not None and not (time_range[0] <= k.co.x <= time_range[1]):
                continue
            r = round(k.co.x)
            if r != k.co.x:
                snapped += 1
            k.co.x = r
            touched = True
        if touched:
            fc.update()
    return snapped


def set_interpolation(objects, interpolation="CONSTANT", handle=None):
    """Set fcurve key ``interpolation`` (``CONSTANT`` / ``LINEAR`` / ``BEZIER`` / ``SINE`` …) on
    every key of the selection — the Blender analogue of Maya's per-key tangent type. ``handle``
    (``AUTO`` / ``AUTO_CLAMPED`` / ``VECTOR`` / ``ALIGNED`` / ``FREE``) optionally sets both bezier
    handle types too. Returns the number of fcurves touched."""
    interp = interpolation.upper()
    n = 0
    for fc in _fcurves(objects):
        for k in fc.keyframe_points:
            k.interpolation = interp
            if handle:
                k.handle_left_type = k.handle_right_type = handle.upper()
        fc.update()
        n += 1
    return n


def set_stepped(objects, stepped=True):
    """Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key."""
    set_interpolation(objects, "CONSTANT" if stepped else "BEZIER")


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


def _remove_fcurve(action, slot, fc):
    """Remove ``fc`` from ``action`` (slot-aware — legacy flat list or per-slot channelbag)."""
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        legacy.remove(fc)
        return
    for layer in action.layers:
        for strip in layer.strips:
            bags = [strip.channelbag(slot)] if slot is not None else list(strip.channelbags)
            for cb in bags:
                if cb is not None and fc in list(cb.fcurves):
                    cb.fcurves.remove(fc)
                    return


def _set_fcurve_value(obj, fc, value):
    """Write ``value`` to the property ``fc`` drives so its curve can be dropped losslessly.

    Resolves the owning struct from ``fc.data_path`` (handles nested paths like
    ``pose.bones["Bone"].location`` and array indices). Returns True on success.
    """
    try:
        data_path = fc.data_path
        if "." in data_path:
            container_path, prop = data_path.rsplit(".", 1)
            container = obj.path_resolve(container_path)
        else:
            container, prop = obj, data_path
        current = getattr(container, prop)
        if fc.array_index >= 0 and hasattr(current, "__len__"):
            current[fc.array_index] = value
        else:
            setattr(container, prop, value)
        return True
    except Exception:  # exotic / unresolvable data path — leave the curve in place
        return False


def _remove_flat_keys(fc, tolerance):
    """Remove interior keys that sit on a flat segment (value equal to both neighbours within
    ``tolerance``); keeps the boundary keys. Returns the number removed."""
    pts = fc.keyframe_points
    removed = 0
    i = len(pts) - 2
    while i >= 1:
        prev_v, cur_v, next_v = pts[i - 1].co.y, pts[i].co.y, pts[i + 1].co.y
        if abs(cur_v - prev_v) <= tolerance and abs(next_v - cur_v) <= tolerance:
            pts.remove(pts[i], fast=True)
            removed += 1
        i -= 1
    return removed


def _simplify_fcurve(fc, tolerance):
    """Greedy collinear reduction — drop an interior key when its value is within ``tolerance``
    of the straight line between its neighbours (a light decimate). Returns the number removed.
    The caller (:func:`optimize_keys`) runs ``fc.update()`` once afterward (as for
    :func:`_remove_flat_keys`)."""
    pts = fc.keyframe_points
    removed = 0
    i = 1
    while i < len(pts) - 1:
        x0, y0 = pts[i - 1].co
        x1, y1 = pts[i].co
        x2, y2 = pts[i + 1].co
        t = (x1 - x0) / (x2 - x0) if x2 != x0 else 0.0
        if abs(y1 - (y0 + (y2 - y0) * t)) <= tolerance:
            pts.remove(pts[i], fast=True)
            removed += 1
        else:
            i += 1
    return removed


def optimize_keys(
    objects=None,
    value_tolerance=0.001,
    remove_static_curves=True,
    remove_flat_keys=True,
    simplify_keys=False,
    stats=None,
):
    """Remove redundant animation data — mirror of ``mtk.AnimUtils.optimize_keys``.

    Pure ``keyframe_points`` math (headless-safe; the native Graph-Editor ``clean``/``decimate``
    ops can't run ``--background``):

    * ``remove_static_curves`` — a curve whose values are constant within ``value_tolerance`` is
      dropped after writing its held value back to the property (lossless).
    * ``remove_flat_keys`` — interior keys on a flat segment are removed (boundaries kept).
    * ``simplify_keys`` — additionally drop keys that lie on the line between their neighbours.

    ``objects`` defaults to every scene object. Pass a dict as ``stats`` to receive
    ``curves_before/after`` and ``keys_before/after`` counts (also returned).
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else list(bpy.data.objects)
    s = {"curves_before": 0, "curves_after": 0, "keys_before": 0, "keys_after": 0}
    for o in pool:
        for action, slot in _actions([o]):
            for fc in list(_slot_fcurves(action, slot)):
                pts = fc.keyframe_points
                s["curves_before"] += 1
                s["keys_before"] += len(pts)
                if remove_static_curves and len(pts):
                    vals = [k.co.y for k in pts]
                    if max(vals) - min(vals) <= value_tolerance and _set_fcurve_value(
                        o, fc, vals[0]
                    ):
                        _remove_fcurve(action, slot, fc)
                        continue
                if remove_flat_keys:
                    _remove_flat_keys(fc, value_tolerance)
                if simplify_keys:
                    _simplify_fcurve(fc, value_tolerance)
                fc.update()
                s["curves_after"] += 1
                s["keys_after"] += len(fc.keyframe_points)
    if stats is not None:
        stats.update(s)
    return s


def repair_corrupted_curves(
    objects=None,
    *,
    delete_unfixable=True,
    fix_infinite=True,
    fix_invalid_times=True,
    time_threshold=100000.0,
    value_threshold=1000000.0,
):
    """Detect and repair corrupted animation fcurves — mirror of
    ``mtk.Diagnostics.repair_corrupted_curves``.

    Corruption shows up in Blender too (bad imports, broken drivers, math errors): a keyframe
    can hold a NaN/infinite value or an absurd frame/value beyond any sane range. Repair removes
    the corrupted keyframes (a NaN/inf key can't be interpolated); a curve left with no valid keys
    is deleted when ``delete_unfixable`` is set (else emptied). Pure ``keyframe_points`` math →
    headless-safe.

    * ``fix_infinite`` — flag NaN/inf key *values*, or ``abs(value) > value_threshold``.
    * ``fix_invalid_times`` — flag NaN/inf key *frames*, or ``abs(frame) > time_threshold``.

    ``objects`` defaults to every scene object. Returns
    ``{corrupted_found, curves_repaired, curves_deleted, keys_fixed, details}``.
    """
    import bpy
    import math

    def _bad_value(v):
        return fix_infinite and (math.isnan(v) or math.isinf(v) or abs(v) > value_threshold)

    def _bad_time(t):
        return fix_invalid_times and (math.isnan(t) or math.isinf(t) or abs(t) > time_threshold)

    pool = ptk.make_iterable(objects) if objects is not None else list(bpy.data.objects)
    result = {
        "corrupted_found": 0, "curves_repaired": 0, "curves_deleted": 0,
        "keys_fixed": 0, "details": [],
    }
    for o in pool:
        for action, slot in _actions([o]):
            for fc in list(_slot_fcurves(action, slot)):
                if not any(_bad_value(k.co.y) or _bad_time(k.co.x) for k in fc.keyframe_points):
                    continue
                result["corrupted_found"] += 1
                path = f"{fc.data_path}[{fc.array_index}]"
                # Remove corrupted keys one at a time: removing a keyframe_point invalidates the
                # other references, so re-fetch the next bad key each pass rather than batch-remove.
                while True:
                    bad = next(
                        (k for k in fc.keyframe_points if _bad_value(k.co.y) or _bad_time(k.co.x)),
                        None,
                    )
                    if bad is None:
                        break
                    fc.keyframe_points.remove(bad)
                    result["keys_fixed"] += 1
                if len(fc.keyframe_points) == 0 and delete_unfixable:
                    _remove_fcurve(action, slot, fc)
                    result["curves_deleted"] += 1
                    result["details"].append(f"{path}: deleted (no valid keys remained)")
                else:
                    fc.update()
                    result["curves_repaired"] += 1
                    result["details"].append(
                        f"{path}: {'emptied' if not fc.keyframe_points else 'removed corrupt key(s)'}"
                    )
    return result


def tie_keyframes(objects=None, untie=False, frame_range=None, absolute=False):
    """Add (tie) or remove (untie) bookend keys at the playback-range boundaries — mirror of
    ``mtk.AnimUtils.tie_keyframes``.

    Tying inserts a key (sampled from the curve) at the range start and end on every animated
    channel that lacks one, so each object has keys at the boundaries. Untying removes only those
    boundary keys (never the last remaining key). ``frame_range`` defaults to the scene's
    ``frame_start``/``frame_end``; ``absolute`` (when no explicit range is given) uses the actual
    keyed extent across the objects instead of the scene range. Returns the number of keys changed.
    """
    import bpy

    scene = bpy.context.scene
    if objects is not None:
        pool = ptk.make_iterable(objects)
    else:
        pool = [
            o for o in bpy.data.objects
            if getattr(o, "animation_data", None) and o.animation_data.action
        ]
    if frame_range is not None:
        lo, hi = frame_range
    elif absolute:
        rng = _key_range(get_fcurves(pool))
        if rng is None:
            return 0
        lo, hi = rng
    else:
        lo, hi = scene.frame_start, scene.frame_end
    changed = 0
    for o in pool:
        for action, slot in _actions([o]):
            for fc in _slot_fcurves(action, slot):
                pts = fc.keyframe_points
                if not len(pts):
                    continue
                if untie:
                    for bound in (hi, lo):
                        for i in reversed(
                            [i for i, k in enumerate(pts) if abs(k.co.x - bound) < 1e-6]
                        ):
                            if len(pts) > 1:
                                pts.remove(pts[i], fast=True)
                                changed += 1
                else:
                    for bound in (lo, hi):
                        if not any(abs(k.co.x - bound) < 1e-6 for k in pts):
                            pts.insert(bound, fc.evaluate(bound))
                            changed += 1
                fc.update()
    return changed


def bake_keys(
    objects=None,
    frame_range=None,
    step=1,
    only_selected=False,
    visual_keying=True,
    clear_constraints=False,
    clear_parents=False,
    bake_types=None,
):
    """Bake animation to plain keyframes — the Blender analogue of Maya's Smart Bake (wraps the
    native ``nla.bake``, which resolves constraints/drivers/parenting via ``visual_keying``).

    ``objects`` defaults to the current selection; armatures bake their pose, others bake object
    transforms (override with ``bake_types``, a subset of ``{'POSE', 'OBJECT'}``).
    ``frame_range`` defaults to the scene playback range. Returns the baked objects.
    """
    import bpy

    pool = [
        o for o in (ptk.make_iterable(objects) if objects is not None
                    else bpy.context.selected_objects)
    ]
    if not pool:
        return []
    scene = bpy.context.scene
    start, end = frame_range if frame_range is not None else (scene.frame_start, scene.frame_end)
    if bake_types is None:
        bake_types = (
            {"POSE", "OBJECT"} if any(o.type == "ARMATURE" for o in pool) else {"OBJECT"}
        )
    view_layer = bpy.context.view_layer
    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    for o in pool:
        o.select_set(True)
    view_layer.objects.active = pool[0]
    bpy.ops.nla.bake(
        frame_start=int(start),
        frame_end=int(end),
        step=step,
        only_selected=only_selected,
        visual_keying=visual_keying,
        clear_constraints=clear_constraints,
        clear_parents=clear_parents,
        use_current_action=True,
        bake_types=bake_types,
    )
    return pool


def bake_blend_shapes(objects=None, frame_range=None, step=1):
    """Bake driven/animated blend-shape (shape-key) weights to explicit keyframes — the Blender
    counterpart of Maya Smart Bake's *Bake Blend Shapes*. ``nla.bake`` only bakes object/pose
    transforms, so driven shape-key values (drivers / set-driven-key / expressions) don't survive
    an FBX/Unity export; this samples each driven key's EVALUATED value per frame (from the
    depsgraph, so driver results are captured — the original datablock value would not reflect a
    driver), removes the drivers, then writes the sampled keyframes so the weights export.

    Only meshes whose shape keys are actually driven/animated are touched. ``frame_range`` defaults
    to the scene playback range. Returns the baked mesh objects.
    """
    import bpy

    scene = bpy.context.scene
    start, end = frame_range if frame_range is not None else (scene.frame_start, scene.frame_end)

    pool = ptk.make_iterable(objects) if objects is not None else bpy.context.selected_objects
    targets = []  # (obj, shape_keys) for meshes with driven/animated shape keys
    for o in pool:
        if getattr(o, "type", None) != "MESH":
            continue
        sk = getattr(o.data, "shape_keys", None)
        ad = getattr(sk, "animation_data", None) if sk else None
        # slot-aware fcurve check — Blender 5.x drops the flat action.fcurves (slotted actions).
        if ad and (ad.drivers or (ad.action and _slot_fcurves(ad.action))):
            targets.append((o, sk))
    if not targets:
        return []

    # 1. Sample each non-reference key's evaluated value per frame.
    frames = list(range(int(start), int(end) + 1, max(1, step)))
    samples = {}  # (obj, key_name) -> [(frame, value), ...]
    orig_frame = scene.frame_current
    for f in frames:
        scene.frame_set(f)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for o, sk in targets:
            sk_eval = o.evaluated_get(depsgraph).data.shape_keys
            if sk_eval is None:
                continue
            ref = sk.reference_key
            for kb_orig, kb_eval in zip(sk.key_blocks, sk_eval.key_blocks):
                if kb_orig == ref:
                    continue
                samples.setdefault((o, kb_orig.name), []).append((f, kb_eval.value))
    scene.frame_set(orig_frame)

    # 2. Remove the drivers (a property can't carry both a driver and an fcurve — the driver wins).
    for o, sk in targets:
        ad = sk.animation_data
        for drv in list(ad.drivers):
            ad.drivers.remove(drv)

    # 3. Write the sampled values as keyframes.
    for (o, key_name), vals in samples.items():
        kb = o.data.shape_keys.key_blocks.get(key_name)
        if kb is None:
            continue
        for f, v in vals:
            kb.value = v
            kb.keyframe_insert("value", frame=f)

    return [o for o, _ in targets]


def get_animation_info(objects=None, by_time=False):
    """Per-object animation summary — mirror of ``mtk`` get-animation-info.

    Returns a list of records ``{name, action, start, end, channels, keys, paths}`` for every
    animated object in scope (``objects`` defaults to the whole scene). Sorted by start frame
    when ``by_time`` else by name. Pair with :func:`format_animation_info_html` for the viewer.
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else list(bpy.data.objects)
    records = []
    for o in pool:
        ad = getattr(o, "animation_data", None)
        action = ad.action if ad else None
        if action is None:
            continue
        fcurves = _slot_fcurves(action, getattr(ad, "action_slot", None))
        rng = _key_range(fcurves)
        if rng is None:
            continue
        records.append(
            {
                "name": o.name,
                "action": action.name,
                "start": rng[0],
                "end": rng[1],
                "channels": len(fcurves),
                "keys": sum(len(fc.keyframe_points) for fc in fcurves),
                "paths": sorted({fc.data_path for fc in fcurves}),
            }
        )
    records.sort(key=(lambda r: r["start"]) if by_time else (lambda r: r["name"].lower()))
    return records


def format_animation_info_csv(records):
    """Render :func:`get_animation_info` records as CSV (paste into a spreadsheet) — mirror of
    Maya's CSV-output info flag. Empty string when there are no records."""
    if not records:
        return ""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")  # display-bound: avoid stray \r in the viewer
    writer.writerow(["Object", "Action", "Start", "End", "Channels", "Keys", "Paths"])
    for r in records:
        writer.writerow(
            [
                r["name"],
                r["action"],
                f"{r['start']:.0f}",
                f"{r['end']:.0f}",
                r["channels"],
                r["keys"],
                "; ".join(r["paths"]),
            ]
        )
    return buf.getvalue()


def format_animation_info_html(records):
    """Render :func:`get_animation_info` records as an HTML table for the text-view dialog."""
    if not records:
        return ""
    total_keys = sum(r["keys"] for r in records)
    rows = "".join(
        "<tr>"
        f"<td><b>{_html.escape(r['name'])}</b></td>"
        f"<td>{_html.escape(r['action'])}</td>"
        f"<td align='right'>&nbsp;{r['start']:.0f}–{r['end']:.0f}&nbsp;</td>"
        f"<td align='right'>&nbsp;{r['channels']}&nbsp;</td>"
        f"<td align='right'>&nbsp;{r['keys']:,}</td>"
        "</tr>"
        for r in records
    )
    header = (
        "<tr><th align='left'>Object</th><th align='left'>Action</th>"
        "<th align='right'>Frames</th><th align='right'>Channels</th>"
        "<th align='right'>Keys</th></tr>"
    )
    return (
        f"<h3>Animation Info — {len(records)} animated object(s), {total_keys:,} keys</h3>"
        f"<table cellspacing='6'>{header}{rows}</table>"
    )


# Coarse map of a 0–100 quality slider to FFMPEG's constant-rate-factor enum (lower CRF = higher
# quality). Ordered high→low so the first threshold met wins.
_CRF_BY_QUALITY = (
    (95, "PERC_LOSSLESS"),
    (85, "HIGH"),
    (70, "MEDIUM"),
    (50, "LOW"),
    (0, "VERYLOW"),
)


def configure_render_output(scene, file_format="PNG", container=None, codec=None, quality=None):
    """Apply playblast/render output settings to ``scene.render`` — the engine behind the rendering
    slot's format/quality picker (the Blender counterpart of mayatk's ``PlayblastExporter`` format
    handling). Sets the image format, and for ``file_format="FFMPEG"`` the movie ``container`` and
    ``codec``; ``quality`` (0–100) drives ``image_settings.quality`` and, for FFMPEG, a mapped
    ``constant_rate_factor``. Does NOT snapshot/restore — the caller owns that.

    Args:
        scene: the ``bpy.types.Scene`` to configure.
        file_format: ``image_settings.file_format`` enum ("PNG", "JPEG", "TIFF", "TARGA",
            "OPEN_EXR", "FFMPEG", …).
        container: FFMPEG ``ffmpeg.format`` ("MPEG4", "QUICKTIME", "AVI", …) — FFMPEG only.
        codec: FFMPEG ``ffmpeg.codec`` ("H264", "FFV1", …) — FFMPEG only.
        quality: 0–100; applied to ``image_settings.quality`` (JPEG/movie) and mapped to FFMPEG
            ``constant_rate_factor`` when ``file_format="FFMPEG"``.
    """
    render = scene.render
    imgs = render.image_settings
    # Blender 5.x gates the FFMPEG (video) format behind ``media_type='VIDEO'``; image formats
    # need ``'IMAGE'``. The hasattr guard keeps this working on 4.x (where FFMPEG sits directly
    # in ``file_format`` and there is no ``media_type``).
    if hasattr(imgs, "media_type"):
        imgs.media_type = "VIDEO" if file_format == "FFMPEG" else "IMAGE"
    imgs.file_format = file_format
    if file_format == "FFMPEG":
        if container is not None:
            render.ffmpeg.format = container
        if codec is not None:
            render.ffmpeg.codec = codec
    if quality is not None:
        imgs.quality = int(quality)
        if file_format == "FFMPEG":
            render.ffmpeg.constant_rate_factor = next(
                crf for threshold, crf in _CRF_BY_QUALITY if quality >= threshold
            )


class AnimUtils:
    """Namespace mirror (helpers also exposed module-level)."""

    get_fcurves = staticmethod(get_fcurves)
    shift_keys = staticmethod(shift_keys)
    move_keys_to_frame = staticmethod(move_keys_to_frame)
    adjust_key_spacing = staticmethod(adjust_key_spacing)
    align_selected_keyframes = staticmethod(align_selected_keyframes)
    set_visibility_keys = staticmethod(set_visibility_keys)
    add_intermediate_keys = staticmethod(add_intermediate_keys)
    remove_intermediate_keys = staticmethod(remove_intermediate_keys)
    select_keys = staticmethod(select_keys)
    invert_keys = staticmethod(invert_keys)
    stagger_keys = staticmethod(stagger_keys)
    snap_keys = staticmethod(snap_keys)
    scale_keys = staticmethod(scale_keys)
    set_interpolation = staticmethod(set_interpolation)
    set_stepped = staticmethod(set_stepped)
    delete_keys = staticmethod(delete_keys)
    fit_playback_range = staticmethod(fit_playback_range)
    copy_keys = staticmethod(copy_keys)
    paste_keys = staticmethod(paste_keys)
    optimize_keys = staticmethod(optimize_keys)
    repair_corrupted_curves = staticmethod(repair_corrupted_curves)
    tie_keyframes = staticmethod(tie_keyframes)
    bake_keys = staticmethod(bake_keys)
    bake_blend_shapes = staticmethod(bake_blend_shapes)
    get_animation_info = staticmethod(get_animation_info)
    format_animation_info_html = staticmethod(format_animation_info_html)
    format_animation_info_csv = staticmethod(format_animation_info_csv)
    configure_render_output = staticmethod(configure_render_output)
