# !/usr/bin/python
# coding=utf-8
"""Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
anim helpers where names align: ``stagger_keys``, ``invert_keys``, ``scale_keys``, …).

Key timing is plain math over keyframe coordinates — not DCC-specific (the plan's §5 finding) —
so these are **headless-testable**. ``import bpy`` is deferred into call bodies (no import side
effects).
"""

import html as _html

from contextlib import contextmanager

import pythontk as ptk

# ``StaggerKeys`` is imported for its ``_group_units`` overlap-grouping helper (reused by
# :meth:`AnimUtils.set_visibility_keys`); it imports this module's fcurve helpers lazily inside its
# own call bodies, so importing it here at module top is cycle-safe. (The ``scale_keys`` /
# ``stagger_keys`` module-level fns and the ``ScaleKeys`` / ``StaggerKeys`` classes are exposed on
# ``btk`` via their own package-surface entries — not through this import.)
from blendertk.anim_utils.stagger_keys import StaggerKeys


_VISIBILITY_PATHS = ("hide_viewport", "hide_render")


_DELETE_KEYS_SCOPES = {
    "current": lambda x, cur: x == cur,
    "before": lambda x, cur: x < cur,
    "before|current": lambda x, cur: x <= cur,
    "after": lambda x, cur: x > cur,
    "after|current": lambda x, cur: x >= cur,
}


# Coarse map of a 0–100 quality slider to FFMPEG's constant-rate-factor enum (lower CRF = higher
# quality). Ordered high→low so the first threshold met wins.
_CRF_BY_QUALITY = (
    (95, "PERC_LOSSLESS"),
    (85, "HIGH"),
    (70, "MEDIUM"),
    (50, "LOW"),
    (0, "VERYLOW"),
)


class _AnimUtilsInternal(object):
    """Internal helpers for AnimUtils."""

    @staticmethod
    def _slot_fcurves(action, slot=None):
        """The fcurves of ``action`` (slot-aware).

        Blender 4.4+ actions are *slotted/layered* — 5.x drops the legacy flat ``action.fcurves``
        entirely, so keys live in per-slot channelbags (``layers → strips → channelbag(slot)``).
        The layers are read first: on 4.4-5.0 ``action.fcurves`` survives only as a proxy
        for the FIRST slot, so read first it handed every slot of a multi-slot action the
        first slot's curves. The legacy accessor serves an action with no layers (pre-4.4).
        """
        layers = getattr(action, "layers", None)
        if not layers:
            legacy = getattr(action, "fcurves", None)
            return list(legacy) if legacy is not None else []
        out = []
        for layer in layers:
            for strip in layer.strips:
                if slot is not None:
                    cb = strip.channelbag(slot)
                    if cb is not None:
                        out.extend(cb.fcurves)
                else:
                    out.extend(fc for cb in strip.channelbags for fc in cb.fcurves)
        return out

    @staticmethod
    def _animating(ad):
        """``(action, slot)`` that animates the ID owning *ad*, or ``(None, None)``.

        A slotted action (Blender 4.4+) animates an ID only through the slot
        assigned to it. Assigning an action whose slots all belong to other
        IDs leaves the new holder with NO slot, and Blender does not animate
        it -- so reading "no slot" as "every channelbag" (what
        :meth:`_slot_fcurves` does for an action-wide read) reached other
        IDs' channels: key edits through the holder moved the owner's keys.
        A legacy action has no slots and animates as before.
        """
        action = getattr(ad, "action", None) if ad is not None else None
        if action is None:
            return None, None
        slot = getattr(ad, "action_slot", None)
        if slot is None and len(getattr(action, "slots", None) or ()):
            return None, None
        return action, slot

    @staticmethod
    def _owned_actions(objects):
        """``(object, action, slot)`` once per unique ``(action, slot)`` pair.

        The object is the first in *objects* animated through that pair --
        the one a per-curve write (a static curve's held value) lands on.
        Uniqueness is per (action, slot): one slotted action can drive several
        objects, through different slots or through ONE, and a slot two
        objects share is one set of curves, edited once. The action compares
        by identity (IDs are instance-cached), the slot with ``==`` -- slots
        are non-ID RNA structs Blender does not wrapper-cache, so ``is``
        spuriously fails for the same slot; ``bpy_struct.__eq__`` matches by
        data pointer. An object its action does not animate (see
        :meth:`_animating`) is skipped.
        """
        seen, owned = [], []
        for o in ptk.make_iterable(objects):
            action, slot = _AnimUtilsInternal._animating(
                getattr(o, "animation_data", None)
            )
            if action is None:
                continue
            if all(not (action is a and slot == s) for a, s in seen):
                seen.append((action, slot))
                owned.append((o, action, slot))
        return owned

    @staticmethod
    def _actions(objects):
        """Unique ``(action, slot)`` pairs animating the given objects."""
        return [
            (action, slot)
            for _o, action, slot in _AnimUtilsInternal._owned_actions(objects)
        ]

    @staticmethod
    def _fcurves(objects):  # internal alias -> AnimUtils.get_fcurves (call-time lookup)
        return AnimUtils.get_fcurves(objects)

    @staticmethod
    def _is_fcurve(obj):
        """Whether *obj* is an fcurve rather than something that owns some."""
        return hasattr(obj, "keyframe_points") and hasattr(obj, "data_path")

    @staticmethod
    def _animation_data_owners(o):
        """``(level, animation_data)`` pairs for every place *o* can hang animation:
        the object itself, its data block (mesh/camera/light properties), and its
        data's shape keys. ``level`` is ``"object"`` / ``"data"`` / ``"shape_keys"`` —
        only the ``"object"`` level is visible to :meth:`AnimUtils.get_fcurves`."""
        out = []
        ad = getattr(o, "animation_data", None)
        if ad is not None:
            out.append(("object", ad))
        data = getattr(o, "data", None)
        dad = getattr(data, "animation_data", None) if data is not None else None
        if dad is not None:
            out.append(("data", dad))
        sk = getattr(data, "shape_keys", None) if data is not None else None
        sad = getattr(sk, "animation_data", None) if sk is not None else None
        if sad is not None:
            out.append(("shape_keys", sad))
        return out

    @staticmethod
    def _nla_strip_frames(anim_data):
        """Scene-time frame extents (``strip.frame_start``/``frame_end``) of every
        non-muted strip on every non-muted NLA track of *anim_data*."""
        frames = []
        for track in getattr(anim_data, "nla_tracks", None) or []:
            if track.mute:
                continue
            for strip in track.strips:
                if strip.mute:
                    continue
                frames.extend((strip.frame_start, strip.frame_end))
        return frames

    @staticmethod
    def _key_range(fcurves):
        """(min, max) key frame across ``fcurves``, or None when keyless."""
        lo = hi = None
        for fc in fcurves:
            times = AnimUtils.key_times(fc)
            if not times:
                continue
            lo = times[0] if lo is None else min(lo, times[0])
            hi = times[-1] if hi is None else max(hi, times[-1])
        return None if lo is None else (lo, hi)

    @staticmethod
    def _shift_fcurves(fcurves, offset):
        """Shift every key (and its handles) of ``fcurves`` by ``offset`` frames."""
        for fc in fcurves:
            AnimUtils.shift_keys_in_window(fc, None, None, offset)

    @staticmethod
    def _edit_key_times_in_window(fc, lo, hi, remap, inclusive_hi: bool = True) -> int:
        """Apply ``remap(time) -> time`` to *fc*'s keys (and both handles) in ``[lo, hi]``.

        The one bulk read-edit-write pass behind :meth:`shift_keys_in_window` and
        :meth:`remap_keys_in_window`.  Returns the number of keys edited; the
        curve is ``update()``-d (re-sorted) only when something changed.
        """
        n = len(fc.keyframe_points)
        if not n:
            return 0
        # Two scalar reads beat a bulk read when the curve's whole span misses
        # the window (shared objects whose keys live in other shots).
        kps = fc.keyframe_points
        if (lo is not None and kps[-1].co[0] < lo) or (
            hi is not None and kps[0].co[0] > hi
        ):
            return 0
        co = [0.0] * (2 * n)
        kps.foreach_get("co", co)
        i0, i1 = AnimUtils.window_indices(co[0::2], lo, hi, inclusive_hi)
        if i1 <= i0:
            return 0
        hl = [0.0] * (2 * n)
        hr = [0.0] * (2 * n)
        kps.foreach_get("handle_left", hl)
        kps.foreach_get("handle_right", hr)
        for i in range(2 * i0, 2 * i1, 2):
            co[i] = remap(co[i])
            hl[i] = remap(hl[i])
            hr[i] = remap(hr[i])
        kps.foreach_set("co", co)
        kps.foreach_set("handle_left", hl)
        kps.foreach_set("handle_right", hr)
        fc.update()
        return i1 - i0

    @staticmethod
    def interpolation_value(name: str) -> int:
        """Int value of a ``Keyframe.interpolation`` enum item (bulk reads return ints)."""
        import bpy

        return (
            bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items[name].value
        )

    @staticmethod
    def _is_visibility_fcurve(fc):
        """True when ``fc`` drives an object's viewport/render visibility."""
        return fc.data_path in _VISIBILITY_PATHS

    @staticmethod
    def _when_frames(when, lo, hi, offset):
        """Frames for a range-relative ``when`` mode given an explicit ``(lo, hi)`` key range —
        shared by the per-object and grouped (``group_overlapping``) paths of
        :func:`set_visibility_keys`."""
        table = {
            "start": [lo],
            "end": [hi],
            "both": [lo, hi],
            "before_start": [lo - 1],
            "after_end": [hi + 1],
        }
        return [f + offset for f in table.get(when, [lo])]

    @staticmethod
    def _visibility_key_frames(o, when, frame, offset, scene):
        """Frames to key visibility on ``o`` for the given ``when`` mode (mirror of Maya's cmb002):
        ``current`` (the playhead/``frame``), or — relative to the object's own key range —
        ``start`` / ``end`` / ``both`` / ``before_start`` / ``after_end``. ``[]`` when ``when`` needs
        a key range the object doesn't have."""
        if when == "current":
            base = scene.frame_current if frame is None else frame
            return [base + offset]
        rng = _AnimUtilsInternal._key_range(AnimUtils.get_fcurves([o]))
        if rng is None:
            return []
        return _AnimUtilsInternal._when_frames(when, rng[0], rng[1], offset)

    @staticmethod
    def _paste_pose(objects, buffer, target_time, on_replace=None):
        """Key a ``"current_frame"``-mode :func:`copy_keys` snapshot back onto ``objects`` at
        ``target_time`` (or the frame it was captured at, when ``None``); *on_replace*: see
        :func:`paste_keys`."""
        frame = buffer["frame"] if target_time is None else target_time
        pasted = []
        for o in ptk.make_iterable(objects):
            touched = False
            for (data_path, array_index), value in buffer["values"].items():
                fc, replaced = (
                    _AnimUtilsInternal._keyed_over(
                        o, data_path, array_index, [(frame, value)]
                    )
                    if on_replace is not None
                    else (None, [])
                )
                if not _AnimUtilsInternal._set_path_value(
                    o, data_path, array_index, value
                ):
                    continue
                o.keyframe_insert(data_path, index=array_index, frame=frame)
                touched = True
                if replaced:
                    on_replace(fc, replaced)
            if touched:
                pasted.append(o)
        return pasted

    @staticmethod
    def _report_action_swap(o, incoming, shift, on_replace, value_offsets=None):
        """Report every key of *o*'s action that pasting the *incoming* fcurves (shifted by
        *shift*) in its place REPLACES: all of them, bar one the new action holds at its frame
        with its value.  Called BEFORE the swap -- after it, the old fcurves are no longer
        *o*'s, and nothing resolves their owner.  *value_offsets* (``{(data_path, index):
        offset}``) is what a channel's values gain after the swap (a relative transfer), so
        the comparison reads the values *o* ends with."""
        import bisect

        action, slot = _AnimUtilsInternal._animating(getattr(o, "animation_data", None))
        if action is None:
            return
        value_offsets = value_offsets or {}
        arriving = {}
        for fc in incoming:
            addr = (fc.data_path, fc.array_index)
            dv = value_offsets.get(addr, 0.0)
            arriving[addr] = sorted(
                (float(k.co.x) + shift, float(k.co.y) + dv) for k in fc.keyframe_points
            )
        for fc in _AnimUtilsInternal._slot_fcurves(action, slot):
            keys = arriving.get((fc.data_path, fc.array_index), [])
            frames = [x for x, _y in keys]
            replaced = []
            for k in fc.keyframe_points:
                x, y = float(k.co.x), float(k.co.y)
                i = bisect.bisect_left(frames, x - 0.01)
                same = (
                    i < len(frames)
                    and frames[i] - x <= 0.01
                    and abs(keys[i][1] - y) <= 1e-6 * max(1.0, abs(y))
                )
                if not same:
                    replaced.append(x)
            if replaced:
                on_replace(fc, replaced)

    @staticmethod
    def _paste_selected_keys(objects, buffer, target_time, on_replace=None):
        """Key a ``"selected"``-mode :func:`copy_keys` buffer back onto ``objects``, shifting so the
        earliest captured frame lands on ``target_time`` (unshifted, at the original frames, when
        ``None``); *on_replace*: see :func:`paste_keys`."""
        keys_by_path = buffer["keys"]
        if not keys_by_path:
            return []
        offset = 0.0
        if target_time is not None:
            earliest = min(x for pts in keys_by_path.values() for x, _v in pts)
            offset = target_time - earliest
        tangents = buffer.get("tangents") or {}
        extrapolation = buffer.get("extrapolation") or {}
        pasted = []
        for o in ptk.make_iterable(objects):
            touched = False
            for path, pts in keys_by_path.items():
                data_path, array_index = path
                fc, replaced = (
                    _AnimUtilsInternal._keyed_over(
                        o, data_path, array_index, [(x + offset, y) for x, y in pts]
                    )
                    if on_replace is not None
                    else (None, [])
                )
                written = []
                for x, y in pts:
                    if not _AnimUtilsInternal._set_path_value(
                        o, data_path, array_index, y
                    ):
                        continue
                    o.keyframe_insert(data_path, index=array_index, frame=x + offset)
                    written.append(x + offset)
                    touched = True
                if written and (path in tangents or path in extrapolation):
                    _AnimUtilsInternal._restore_pasted_tangents(
                        o,
                        path,
                        written,
                        tangents.get(path) or [],
                        extrapolation.get(path),
                    )
                if written and replaced:
                    on_replace(fc, replaced)
            if touched:
                pasted.append(o)
        return pasted

    @staticmethod
    def _restore_pasted_tangents(obj, path, frames, details, extrapolation):
        """Give the keys :meth:`_paste_selected_keys` just wrote on *obj* the shape
        they were copied with: interpolation, easing, handle types and the handle
        offsets from their key, plus the curve's extrapolation (mayatk's paste of a
        ``tangent_detail`` copy restores the same)."""
        data_path, array_index = path
        fc = next(
            (
                c
                for c in AnimUtils.get_fcurves([obj])
                if c.data_path == data_path and c.array_index == array_index
            ),
            None,
        )
        if fc is None:
            return
        if extrapolation:
            fc.extrapolation = extrapolation
        for frame, detail in zip(frames, details):
            k = next(
                (p for p in fc.keyframe_points if abs(p.co.x - frame) < 1e-4), None
            )
            if k is None:
                continue
            k.interpolation = detail["interpolation"]
            k.easing = detail["easing"]
            k.handle_left_type = detail["handle_left_type"]
            k.handle_right_type = detail["handle_right_type"]
            k.handle_left = (
                k.co.x + detail["handle_left"][0],
                k.co.y + detail["handle_left"][1],
            )
            k.handle_right = (
                k.co.x + detail["handle_right"][0],
                k.co.y + detail["handle_right"][1],
            )
        fc.update()

    @staticmethod
    def _invert_selected(
        fcurves, do_time, do_value, value_pivot, on_replace=None, on_move=None
    ) -> int:
        """Body of ``AnimUtils.invert_keys(selected_only=True)``: mirror the selected
        points of *fcurves* in place over the selection's combined ``[min, max]``
        (*on_replace* / *on_move*: see ``invert_keys``)."""
        picked = []
        for fc in fcurves:
            pts = sorted(
                (k for k in fc.keyframe_points if k.select_control_point),
                key=lambda k: k.co.x,
            )
            if pts:
                picked.append((fc, pts))
        if not picked:
            return 0
        times = [k.co.x for _fc, pts in picked for k in pts]
        span = min(times) + max(times)  # t' = span - t
        n = 0
        for fc, pts in picked:
            snap = [
                (
                    k.co.x,
                    k.co.y,
                    k.interpolation,
                    k.easing,
                    k.handle_left_type,
                    k.handle_right_type,
                    tuple(k.handle_left),
                    tuple(k.handle_right),
                )
                for k in pts
            ]
            if do_time and len(snap) > 1:
                # A segment's interpolation lives on the key BEFORE it; after
                # the flip that segment starts at the key that used to end it,
                # so the run's modes rotate one place (the last key's outgoing
                # mode passes to the key now standing where it stood).
                modes = [(row[2], row[3]) for row in snap]
                modes = [modes[-1]] + modes[:-1]
            else:
                modes = [(row[2], row[3]) for row in snap]
            pairs = []
            for k, row, (interp, easing) in zip(pts, snap, modes):
                x, y, _i, _e, hlt, hrt, hl, hr = row
                if do_time:
                    hl, hr = (span - hr[0], hr[1]), (span - hl[0], hl[1])
                    hlt, hrt = hrt, hlt
                    x = span - x
                if do_value:
                    y = 2.0 * value_pivot - y
                    hl = (hl[0], 2.0 * value_pivot - hl[1])
                    hr = (hr[0], 2.0 * value_pivot - hr[1])
                pairs.append((float(row[0]), float(x)))
                k.co = (x, y)
                k.interpolation = interp
                k.easing = easing
                k.handle_left_type, k.handle_right_type = hlt, hrt
                k.handle_left, k.handle_right = hl, hr
            n += len(pts)
            replaced = _AnimUtilsInternal._merge_onto_moved(fc, pts)
            fc.update()
            _AnimUtilsInternal._report_edit(fc, replaced, pairs, on_replace, on_move)
        return n

    @staticmethod
    def _report_edit(fc, replaced, pairs, on_replace=None, on_move=None) -> None:
        """Hand a key edit's ``on_replace(fc, frames)`` the frames of the keys it
        replaced on *fc*, then its ``on_move(fc, pairs)`` the ``(old, new)``
        frames of the keys it moved -- replaced first, so a claim carried onto a
        frame is not the one released there.  A pair that went nowhere is no move.
        """
        if replaced and on_replace is not None:
            on_replace(fc, replaced)
        if on_move is not None:
            moved = [(old, new) for old, new in pairs if abs(new - old) >= 1e-9]
            if moved:
                on_move(fc, moved)

    @staticmethod
    def _merge_onto_moved(fc, moved, eps=1e-4) -> list:
        """Remove the points of *fc* NOT in *moved* that share a frame with one in it.

        The Graph Editor's auto-merge, and Maya's ``setKeyframe`` overwrite: a key
        moved onto an occupied frame replaces the one that was there instead of
        stacking two points on one frame.  Removed last-first: a removal shifts
        the points after it, so a reference to a later point would go stale.
        Returns the frames of the points removed (a caller with claims on
        them releases those).
        """
        landing = sorted(k.co.x for k in moved)
        if not landing:
            return []
        import bisect

        ptrs = {k.as_pointer() for k in moved}
        doomed = []
        for k in fc.keyframe_points:
            if k.as_pointer() in ptrs:
                continue
            i = bisect.bisect_left(landing, k.co.x - eps)
            if i < len(landing) and abs(landing[i] - k.co.x) <= eps:
                doomed.append(k)
        frames = [float(k.co.x) for k in doomed]
        for k in reversed(doomed):
            fc.keyframe_points.remove(k, fast=True)
        return frames

    @staticmethod
    def _keyed_over(obj, data_path, index, keys, eps=0.01):
        """``(fcurve, frames)``: the keys of *obj*'s ``data_path[index]`` fcurve that keying
        the ``(frame, value)`` *keys* REPLACES -- one within Blender's 0.01-frame insert
        window holding another value (keying the value a key already holds only re-keys
        it).  Read BEFORE the writes; ``(None, [])`` while the fcurve does not exist.
        Mirror of mayatk's ``_keyed_over``."""
        import bisect

        fc = next(
            (
                c
                for c in AnimUtils.get_fcurves([obj])
                if c.data_path == data_path and c.array_index == index
            ),
            None,
        )
        if fc is None:
            return None, []
        wanted = sorted((float(f), float(v)) for f, v in keys)
        landing = [f for f, _v in wanted]
        hit = []
        for k in fc.keyframe_points:
            x, y = float(k.co.x), float(k.co.y)
            i = bisect.bisect_left(landing, x - eps)
            if i == len(landing) or landing[i] - x > eps:
                continue  # nothing keyed here
            new = wanted[i][1]
            if abs(new - y) > 1e-6 * max(1.0, abs(new), abs(y)):
                hit.append(x)
        return fc, hit

    @staticmethod
    def _key_visibility(o, frames, visible, on_replace=None):
        """Key ``hide_viewport`` / ``hide_render`` to *visible* at *frames*.  A key on a
        frame that held the other state is replaced, and *on_replace* hears it."""
        hide = not visible
        reports = []
        if on_replace is not None:
            for path in _VISIBILITY_PATHS:
                fc, hit = _AnimUtilsInternal._keyed_over(
                    o, path, 0, [(f, float(hide)) for f in frames]
                )
                if hit:
                    reports.append((fc, hit))
        o.hide_viewport = hide
        o.hide_render = hide
        for f in frames:
            o.keyframe_insert("hide_viewport", frame=f)
            o.keyframe_insert("hide_render", frame=f)
        for fc, hit in reports:
            on_replace(fc, hit)

    @staticmethod
    def _merge_landed(fc, moves, eps=1e-4):
        """Merge for an edit that can land MOVED points on each other too (a
        snapped scale): ``(replaced, pairs)``.

        *moves* is ``[(old x, point)]`` or ``[(old x, point, ideal x)]`` for the
        points the edit moved, already at their new frames.  A point it did not
        move that one lands on is removed (the Graph Editor's auto-merge); of
        moved points that land together the LEAST MOVED is kept -- the one whose
        frame is nearest its *ideal* (a snapped scale: its exact scaled time;
        default: its own frame), an exact tie going to the LATER source -- and
        the others are removed (mayatk's ``_move_curve_keys`` decides the same).
        *replaced* is the frames of the points removed -- a moved one's at its
        SOURCE, where what is held on it still is -- and *pairs* the ``(old,
        new)`` frames of the moved points kept.  Removed last-first, so no
        reference to a later point goes stale.
        """
        import bisect

        moves = [
            (float(m[0]), m[1], float(m[2]) if len(m) > 2 else float(m[1].co.x))
            for m in moves
        ]
        ordered = sorted(moves, key=lambda m: m[1].co.x)
        kept, losers = [], []
        i = 0
        while i < len(ordered):
            j = i + 1
            while j < len(ordered) and ordered[j][1].co.x - ordered[i][1].co.x <= eps:
                j += 1
            group = ordered[i:j]
            best = min(group, key=lambda m: (abs(m[1].co.x - m[2]), -m[0]))
            kept.append(best)
            losers.extend(m for m in group if m is not best)
            i = j
        pairs = [(old, float(k.co.x)) for old, k, _ideal in kept]
        replaced = [old for old, _k, _ideal in losers]
        landing = sorted(k.co.x for _old, k, _ideal in kept)
        moved = {k.as_pointer() for _old, k, _ideal in moves}
        doomed = {k.as_pointer() for _old, k, _ideal in losers}
        remove = []
        for k in fc.keyframe_points:
            ptr = k.as_pointer()
            if ptr in doomed:
                remove.append(k)
            elif ptr not in moved:
                at = bisect.bisect_left(landing, k.co.x - eps)
                if at < len(landing) and abs(landing[at] - k.co.x) <= eps:
                    replaced.append(float(k.co.x))
                    remove.append(k)
        for k in reversed(remove):
            fc.keyframe_points.remove(k, fast=True)
        return replaced, pairs

    @staticmethod
    def _landed_on(times, moves, moving=False, eps=1e-4) -> list:
        """The frames of *times* (a curve's keys before an edit) that the
        ``(source, destination)`` *moves* land on and so REPLACE.

        Not one the edit carries on itself: with *moving* every source goes on
        to its own destination, and a key its own copy lands on is re-keyed.
        Mirror of mayatk's ``_landed_on`` (which reads the times off the curve).
        """
        import bisect

        dests = sorted((d, s) for s, d in moves)
        landing = [d for d, _s in dests]
        sources = {s for s, _d in moves}
        hit = []
        for t in times:
            i = bisect.bisect_left(landing, t - eps)
            if i == len(landing) or landing[i] - t > eps:
                continue  # nothing lands here
            if abs(dests[i][1] - t) <= eps or (moving and t in sources):
                continue
            hit.append(float(t))
        return hit

    @staticmethod
    def _remove_fcurve(action, slot, fc):
        """Remove ``fc`` from ``action`` (slot-aware — legacy flat list or per-slot channelbag).

        Returns ``True`` when the curve was found and removed, ``False`` when it was
        not there: a caller reporting "did anything change" has to tell those apart,
        and a bare ``return`` cannot. Layers first, as :meth:`_slot_fcurves` reads
        them (4.4-5.0's ``action.fcurves`` is a first-slot proxy).
        """
        layers = getattr(action, "layers", None)
        if not layers:
            legacy = getattr(action, "fcurves", None)
            if legacy is None or fc not in list(legacy):
                return False
            legacy.remove(fc)
            return True
        for layer in layers:
            for strip in layer.strips:
                bags = (
                    [strip.channelbag(slot)]
                    if slot is not None
                    else list(strip.channelbags)
                )
                for cb in bags:
                    if cb is not None and fc in list(cb.fcurves):
                        cb.fcurves.remove(fc)
                        return True
        return False

    @staticmethod
    def _resolve_prop_container(obj, data_path):
        """``(container, prop_name)`` for an fcurve's ``data_path`` on ``obj`` — resolves nested
        paths (``pose.bones["Bone"].location``) via ``path_resolve``. Shared by
        :func:`_get_path_value` / :func:`_set_path_value`."""
        if "." in data_path:
            container_path, prop = data_path.rsplit(".", 1)
            return obj.path_resolve(container_path), prop
        return obj, data_path

    @staticmethod
    def _get_path_value(obj, data_path, array_index):
        """Read ``obj``'s CURRENT property value at ``data_path``/``array_index`` (an fcurve's
        addressing pair) — the read-side mirror of :func:`_set_path_value`, used by
        :func:`transfer_keyframes`'s relative mode to snapshot a target's own pose before it's
        overwritten. Returns ``None`` for an exotic/unresolvable path or a property that doesn't
        exist on ``obj`` (e.g. a custom attribute present on the source only)."""
        try:
            container, prop = _AnimUtilsInternal._resolve_prop_container(obj, data_path)
            current = getattr(container, prop)
            if array_index >= 0 and hasattr(current, "__len__"):
                return current[array_index]
            return current
        except Exception:
            return None

    @staticmethod
    def _set_path_value(obj, data_path, array_index, value):
        """Write ``value`` to ``obj``'s property at ``data_path``/``array_index`` (an fcurve's
        addressing pair). Resolves nested paths (``pose.bones["Bone"].location``) and array indices
        via ``path_resolve``. Returns True on success, False for an exotic/unresolvable path."""
        try:
            container, prop = _AnimUtilsInternal._resolve_prop_container(obj, data_path)
            current = getattr(container, prop)
            if array_index >= 0 and hasattr(current, "__len__"):
                current[array_index] = value
            else:
                setattr(container, prop, value)
            return True
        except (
            Exception
        ):  # exotic / unresolvable data path — leave the caller's fallback in place
            return False

    @staticmethod
    def _set_fcurve_value(obj, fc, value):
        """Write ``value`` to the property ``fc`` drives so its curve can be dropped losslessly.
        Returns True on success."""
        return _AnimUtilsInternal._set_path_value(
            obj, fc.data_path, fc.array_index, value
        )

    #: Per-key RNA properties a rebuild carries over: floats with their element width,
    #: then the enum / bool ones, which ``foreach_get`` fills as ints.
    _KEY_FLOAT_PROPS = (
        ("co", 2),
        ("handle_left", 2),
        ("handle_right", 2),
        ("back", 1),
        ("amplitude", 1),
        ("period", 1),
    )
    _KEY_INT_PROPS = (
        "interpolation",
        "handle_left_type",
        "handle_right_type",
        "easing",
        "type",
        "select_control_point",
        "select_left_handle",
        "select_right_handle",
    )

    @staticmethod
    def _keep_keys(fc, keep):
        """Rebuild *fc* with only the keyframe points whose *keep* flag is true.

        Every per-key property (position, both handles and their types, interpolation,
        easing, key type, the dynamic easings' back/amplitude/period, selection) is read
        in bulk and written back for the survivors, so a kept key is unchanged -- only
        its neighbours are gone. One ``clear`` + ``add`` instead of a ``remove`` per
        dropped key, each of which shifts the rest of the curve. Returns the kept count.
        """
        pts = fc.keyframe_points
        n = len(pts)
        index = [i for i, flag in enumerate(keep) if flag]
        data = []
        for name, width in _AnimUtilsInternal._KEY_FLOAT_PROPS:
            buf = [0.0] * (n * width)
            pts.foreach_get(name, buf)
            data.append(
                (name, [buf[i * width + j] for i in index for j in range(width)])
            )
        for name in _AnimUtilsInternal._KEY_INT_PROPS:
            buf = [0] * n
            pts.foreach_get(name, buf)
            data.append((name, [buf[i] for i in index]))
        pts.clear()
        pts.add(len(index))
        for name, values in data:
            pts.foreach_set(name, values)
        fc.update()
        return len(index)

    @staticmethod
    def _remove_flat_keys(fc, tolerance, candidate=None):
        """Remove interior keys that sit on a flat segment (value equal to both neighbours within
        ``tolerance``); keeps the boundary keys. Returns the number removed.

        ``candidate(key)`` narrows WHICH interior keys may go; a key it refuses
        stays and becomes a boundary for the rest (:func:`AnimUtils.get_redundant_flat_keys`).

        Unscoped, the walk runs over one bulk read and the survivors are written back
        in one rebuild (:meth:`_keep_keys`): deleting keys one at a time shifts the rest
        of the curve on every call, which on a per-frame bake is quadratic in its
        length. It is the same walk -- from the end, each key judged against its
        predecessor and the next key still standing -- so both paths remove the same
        keys."""
        pts = fc.keyframe_points
        if candidate is None:
            n = len(pts)
            if n < 3:
                return 0
            values = AnimUtils.key_arrays(fc)[1]
            keep = [True] * n
            next_v = values[-1]
            for i in range(n - 2, 0, -1):
                cur_v = values[i]
                if (
                    abs(cur_v - values[i - 1]) <= tolerance
                    and abs(next_v - cur_v) <= tolerance
                ):
                    keep[i] = False
                else:
                    next_v = cur_v
            removed = n - sum(keep)
            if removed:
                _AnimUtilsInternal._keep_keys(fc, keep)
            return removed
        removed = 0
        i = len(pts) - 2
        while i >= 1:
            if not candidate(pts[i]):
                i -= 1
                continue
            prev_v, cur_v, next_v = pts[i - 1].co.y, pts[i].co.y, pts[i + 1].co.y
            if abs(cur_v - prev_v) <= tolerance and abs(next_v - cur_v) <= tolerance:
                pts.remove(pts[i], fast=True)
                removed += 1
            i -= 1
        return removed

    @staticmethod
    def _reduce_fcurve_to_extremes(fc, tolerance, max_error=None):
        """Reduce a baked fcurve to its shape-defining keys and refit the handles.

        Keeps endpoints, peaks, valleys and hold boundaries, plus any sample the
        refit would miss by more than ``max_error`` (``ptk.MathUtils.reduce_samples``;
        None = 1% of the curve's own amplitude); the tweens go and the survivors get
        Bezier handles at the segment thirds carrying the fitted slopes, so the
        sparse curve traces the bake within the bound.  A hold stays exactly flat (``FREE`` handles, the
        facing one level); elsewhere the handles are ``ALIGNED``.  Returns
        ``(keys_removed, max_error)`` or ``None`` when the curve has stepped keys (no
        tween to refit) or nothing to drop."""
        import bpy
        import numpy as np

        pts = fc.keyframe_points
        n = len(pts)
        constant = (
            bpy.types.Keyframe.bl_rna.properties["interpolation"]
            .enum_items["CONSTANT"]
            .value
        )
        if n < 3 or constant in AnimUtils.key_interpolations(fc):
            return None
        # Bulk reads: per-key RNA access dominated this pass on a production bake
        # (25.9 M keys in 64 s, the reduction math itself about a fifth of it).
        times, values = AnimUtils.key_arrays(fc)
        keep, in_slopes, out_slopes = ptk.MathUtils.reduce_samples(
            times, values, value_tolerance=tolerance, max_error=max_error
        )
        if len(keep) == n:
            return None
        kept = [(times[i], values[i]) for i in keep]
        pts.clear()
        pts.add(len(kept))
        last = len(kept) - 1
        for k, (x, y) in enumerate(kept):
            key = pts[k]
            key.co = (x, y)
            key.interpolation = "BEZIER"
            m_in, m_out = in_slopes[k], out_slopes[k]
            dt_l = x - kept[k - 1][0] if k > 0 else kept[1][0] - x
            dt_r = kept[k + 1][0] - x if k < last else x - kept[k - 1][0]
            htype = "ALIGNED" if m_in == m_out else "FREE"
            key.handle_left_type = htype
            key.handle_right_type = htype
            key.handle_left = (x - dt_l / 3.0, y - m_in * dt_l / 3.0)
            key.handle_right = (x + dt_r / 3.0, y + m_out * dt_r / 3.0)
        fc.update()
        # The written curve IS this Hermite fit -- each segment's handles sit at its
        # thirds carrying the fitted slopes -- so its deviation from the samples comes
        # from one numpy evaluation rather than an fc.evaluate per sample.
        fitted = ptk.MathUtils.evaluate_hermite(
            times, values, keep, in_slopes, out_slopes
        )
        error = float(np.max(np.abs(fitted - np.asarray(values, dtype=float))))
        return len(times) - len(kept), error

    @staticmethod
    def _simplify_fcurve(fc, tolerance, candidate=None):
        """Greedy collinear reduction — drop an interior key when its value is within ``tolerance``
        of the straight line between its neighbours (a light decimate). Returns the number removed.
        The caller (:func:`optimize_keys`) runs ``fc.update()`` once afterward (as for
        :func:`_remove_flat_keys`).

        ``candidate(key)`` narrows WHICH interior keys may go — a key it refuses is
        kept and becomes a neighbour the rest are measured against, which is how a
        windowed or selection-scoped pass keeps its two ends and everything outside
        it (:func:`AnimUtils.simplify_curve`). The first and last key of the curve are
        never candidates whatever it says."""
        pts = fc.keyframe_points
        removed = 0
        i = 1
        while i < len(pts) - 1:
            if candidate is not None and not candidate(pts[i]):
                i += 1
                continue
            # A key reached by a STEP is not on any line: the segment before it
            # holds the previous value, so dropping it extends that hold and
            # moves every frame in between.  Maya's keyReducer refuses a
            # stepped curve outright for the same reason; here the refusal is
            # per key, and a stepped HOLD is still cleaned -- by the flat pass
            # (:meth:`AnimUtils.get_redundant_flat_keys`), where equal values
            # make the removal exact.  Without this a staircase 0/5/10 read as
            # collinear and simplified into a ramp.
            if (
                pts[i - 1].interpolation == "CONSTANT"
                or pts[i].interpolation == "CONSTANT"
            ):
                i += 1
                continue
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

    @staticmethod
    def _active_range(fcurves, tolerance=1e-4):
        """The innermost ``(start, end)`` frame range where at least one of ``fcurves`` actually
        changes value between consecutive keys — ``None`` when every curve is a flat hold (every key
        the same value). Used by :func:`get_animation_info`'s ``ignore_holds`` to trim static leading/
        trailing segments, the Blender counterpart of mayatk's active-segment detection (relaxed to a
        single overall range — mtk's per-segment breakdown has no Blender info-panel equivalent)."""
        start = end = None
        for fc in fcurves:
            pts = sorted(fc.keyframe_points, key=lambda k: k.co.x)
            for i in range(len(pts) - 1):
                if abs(pts[i + 1].co.y - pts[i].co.y) > tolerance:
                    t0, t1 = pts[i].co.x, pts[i + 1].co.x
                    start = t0 if start is None else min(start, t0)
                    end = t1 if end is None else max(end, t1)
        return (start, end) if start is not None else None


class AnimUtils(_AnimUtilsInternal):
    """Namespace mirror (helpers also exposed module-level)."""

    #: Optimization levels for :meth:`optimize_keys`, least to most aggressive.
    #: The single source of truth every consumer reads -- the Scene Exporter's
    #: Optimize Keys combo, SmartBake's pass-through, and any headless caller --
    #: so a level added here reaches all of them without a second edit.  Each
    #: value is literally the ``optimize_keys`` kwargs that level means: the
    #: level is sugar over the primitive, never a replacement for it, and a
    #: caller that wants a combination no level names still passes kwargs.
    #: Mirrors ``mtk.AnimUtils.OPTIMIZE_LEVELS`` key-for-key -- both engines
    #: take the same four knobs, including the negative-tolerance extremes
    #: sentinel -- so a Scene Exporter template moves between the two DCCs.
    OPTIMIZE_LEVELS = {
        # Delete curves whose value never changes; leave every surviving curve's
        # keys alone.  The conservative rung: nothing that carries motion is
        # touched, so it is safe on hand-animated curves whose flat sections are
        # deliberate holds.
        "static": {"remove_static_curves": True, "remove_flat_keys": False},
        # ... plus the redundant interior keys of a flat run.  What every caller
        # got before levels existed (see DEFAULT_OPTIMIZE_LEVEL).
        "flat": {"remove_static_curves": True, "remove_flat_keys": True},
        # ... plus dropping keys that lie on the line between their neighbours.
        # Lossy by construction: that judgement is only as good as the tolerance.
        "simplify": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "simplify_keys": True,
        },
        # Reduce smooth curves to their extrema with handles refit against the
        # samples (:meth:`reduce_to_extremes`, selected by the negative tolerance).
        # The answer for per-frame BAKED output, where the other rungs have
        # almost nothing to delete -- a bake has no redundant flat keys to find.
        "extremes": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "value_tolerance": -1.0,
        },
    }

    #: The level a bare ``True`` resolves to -- what every caller got before
    #: levels existed, so a bool keeps behaving exactly as it did.
    DEFAULT_OPTIMIZE_LEVEL = "flat"

    #: Retired level names -> the canonical key, warning until they go.
    #: ``"unbake"`` (until 2026-09-02) read as reversing a bake -- which is
    #: ``SmartBake.restore`` -- when the level only thins a bake to its
    #: extremes; a saved template or preset may still say it.
    _resolve_retired_level = staticmethod(
        ptk.Deprecation.values(
            {"unbake": "extremes"},
            what="AnimUtils optimize level",
            remove_in="0.11.0",
            since="2026-09-23",
        )
    )

    @classmethod
    def normalize_optimize_level(cls, level):
        """The canonical :attr:`OPTIMIZE_LEVELS` key *level* names, or None for OFF.

        Split out of :meth:`resolve_optimize_level` so a caller that wants to
        REPORT the level (a log line, a summary) names the same thing the pass
        actually ran -- ``"  Extremes "`` resolves correctly but should not be
        echoed back with the caller's spacing and case.

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Raises:
            ValueError: *level* is a non-empty string naming no known level.
        """
        if not level:  # None/False/0/"" -- OFF.  Tested BEFORE the string
            return None  # branch: "" is a falsy config value, not a bad level
        if not isinstance(level, str):  # True, or a legacy truthy bool flag
            return cls.DEFAULT_OPTIMIZE_LEVEL
        key = cls._resolve_retired_level(level.strip().lower())
        if key not in cls.OPTIMIZE_LEVELS:
            raise ValueError(
                f"Unknown optimize level {level!r}; expected one of "
                f"{', '.join(cls.OPTIMIZE_LEVELS)}."
            )
        return key

    @classmethod
    def resolve_optimize_level(cls, level):
        """Resolve an optimization level into :meth:`optimize_keys` kwargs.

        The seam between a UI/config choice and the primitive, so no consumer
        hard-codes a level's kwargs::

            kwargs = AnimUtils.resolve_optimize_level(level)
            if kwargs:
                AnimUtils.optimize_keys(objects, **kwargs)

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Returns:
            The kwargs for that level, or None when it is OFF (so the caller
            skips the pass rather than running it with everything disabled).

        Raises:
            ValueError: *level* is a string naming no known level.  Loud rather
                than silently falling back: an unknown level is a config error,
                and a quiet default would optimize the user's curves at a
                setting they did not choose.
        """
        key = cls.normalize_optimize_level(level)
        return dict(cls.OPTIMIZE_LEVELS[key]) if key else None

    # ---- bulk keyframe access ------------------------------------------------
    #
    # Per-keyframe attribute access from Python costs ~1 µs per point; a timeline
    # edit on a sixty-object scene touches tens of thousands of points several
    # times over.  ``keyframe_points.foreach_get/foreach_set`` move the whole
    # array in one C call (10-20x faster measured on 5.1), and points are kept
    # time-sorted by ``fcurve.update()``, so a time window is a ``bisect`` slice.
    # Every hot path in the shots system reads and writes keys through these.

    @staticmethod
    def key_arrays(fc):
        """``(times, values)`` of *fc*'s keyframe points as plain float lists (time order)."""
        n = len(fc.keyframe_points)
        if not n:
            return [], []
        co = [0.0] * (2 * n)
        fc.keyframe_points.foreach_get("co", co)
        return co[0::2], co[1::2]

    @staticmethod
    def key_times(fc):
        """Key times of *fc* as a plain float list (time order)."""
        return AnimUtils.key_arrays(fc)[0]

    @staticmethod
    def key_interpolations(fc):
        """Per-key ``interpolation`` enum ints of *fc* (see :meth:`interpolation_value`)."""
        n = len(fc.keyframe_points)
        if not n:
            return []
        out = [0] * n
        fc.keyframe_points.foreach_get("interpolation", out)
        return out

    @staticmethod
    def window_indices(times, lo, hi, inclusive_hi: bool = True):
        """``(i0, i1)`` slice bounds of the keys of sorted *times* inside ``[lo, hi]``.

        ``lo``/``hi`` of ``None`` are open ends; ``inclusive_hi=False`` makes the
        window half-open ``[lo, hi)``.
        """
        import bisect

        i0 = 0 if lo is None else bisect.bisect_left(times, lo)
        if hi is None:
            i1 = len(times)
        elif inclusive_hi:
            i1 = bisect.bisect_right(times, hi)
        else:
            i1 = bisect.bisect_left(times, hi)
        return i0, max(i0, i1)

    @staticmethod
    def shift_keys_in_window(
        fc, lo, hi, delta: float, inclusive_hi: bool = True
    ) -> int:
        """Translate *fc*'s keys (and both handles) inside ``[lo, hi]`` by *delta*.

        ``None`` bounds are open.  Returns the number of keys moved.
        """
        if not delta:
            return 0
        return _AnimUtilsInternal._edit_key_times_in_window(
            fc, lo, hi, lambda t: t + delta, inclusive_hi
        )

    @staticmethod
    def remap_keys_in_window(fc, lo, hi, old_start, old_end, new_start, new_end) -> int:
        """Linearly remap *fc*'s keys (and handles) in ``[lo, hi]`` from one span to another.

        A key at *t* lands at ``new_start + (t - old_start) * scale``; the Blender
        analogue of Maya's ``scaleKey``.  Returns the number of keys remapped.
        """
        span = old_end - old_start
        if abs(span) < 1e-9:
            return 0
        scale = (new_end - new_start) / span
        return _AnimUtilsInternal._edit_key_times_in_window(
            fc, lo, hi, lambda t: new_start + (t - old_start) * scale
        )

    @staticmethod
    def step_last_key_in_window(fc, lo, hi) -> bool:
        """Set the LAST key of *fc* inside ``[lo, hi]`` to ``CONSTANT`` interpolation.

        Returns ``True`` when a key was changed (already-constant keys are left).
        """
        times = AnimUtils.key_times(fc)
        i0, i1 = AnimUtils.window_indices(times, lo, hi)
        if i1 <= i0:
            return False
        kp = fc.keyframe_points[i1 - 1]
        if kp.interpolation == "CONSTANT":
            return False
        kp.interpolation = "CONSTANT"
        return True

    @staticmethod
    @contextmanager
    def evaluable_override(objects):
        """Yield with *objects* evaluated by the depsgraph at ANY frame: revealed
        (``CoreUtils.visible_override``) and their animated ``hide_viewport`` /
        ``hide_render`` keys muted, then the view layer updated once.

        Blender does not evaluate a hidden object, so its ``matrix_world``
        freezes at the last frame it was seen -- and a static reveal alone is
        undone by the next ``frame_set``, because the hide keys write the flag
        back. Measured on a production pull: every rig record "diverged" by
        exactly its own travel, on three runs, whatever the constraint was,
        because the rig internals are hidden with keyed visibility. Wrap any
        per-frame sampling of objects that may be hidden in this.

        Parameters:
            objects: Object refs (or a single object).

        Example:
            >>> with AnimUtils.evaluable_override([driven, space]):
            ...     scene.frame_set(10)
            ...     point = driven.matrix_world.translation
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        objects = [o for o in ptk.make_iterable(objects) if o is not None]
        muted = []
        for fc in AnimUtils.get_fcurves(objects):
            if fc.data_path in ("hide_viewport", "hide_render") and not fc.mute:
                fc.mute = True
                muted.append(fc)
        try:
            with CoreUtils.visible_override(objects):
                bpy.context.view_layer.update()
                yield
        finally:
            for fc in muted:
                try:
                    fc.mute = False
                except ReferenceError:
                    continue

    @staticmethod
    def get_fcurves(objects):
        """All fcurves across the given objects' actions (slot-aware; public for slot code/tests).

        Each object contributes the curves of the slot that animates it, once
        per slot however many objects share it; an object holding a slotted
        action with no slot assigned is not animated by it and contributes none.

        An FCURVE passed in comes straight back out, so a caller that already
        holds the channels it means -- a sub-row's ``curves_for_attr`` -- can
        hand them to any helper here and have the edit stop at those channels.
        The mirror of ``mtk.AnimUtils.objects_to_curves`` passing anim curves
        through, and the reason an attribute scope needs no name matching on
        this side: in Blender the fcurve IS the channel.
        """
        objs, curves = [], []
        for o in ptk.make_iterable(objects):
            (curves if _AnimUtilsInternal._is_fcurve(o) else objs).append(o)
        for action, slot in _AnimUtilsInternal._actions(objs):
            curves.extend(_AnimUtilsInternal._slot_fcurves(action, slot))
        # Not de-duplicated, as before: ``is`` is unreliable on non-ID RNA structs
        # (see ``_actions``) and an O(n^2) scan would cost every scene-wide caller
        # for a case only a mixed objects+fcurves argument can produce.
        return curves

    @staticmethod
    def get_animated_extent(objects):
        """``(start, end)`` of EVERYTHING that animates *objects* over time, or ``None``.

        Unions, per object:

        * active-action fcurve key ranges at every animation level — the object
          itself, its data block, and its data's shape keys (only the object level is
          visible to :meth:`get_fcurves`);
        * the scene-time extents of every non-muted NLA strip on every non-muted
          track (``strip.frame_start``/``frame_end`` — strips carry their own frame
          mapping, so their placed extent IS their scene-time footprint).

        This is the range an exporter's bake must cover: the FBX write bakes the
        *evaluated* scene, so NLA-strip-only or shape-key/data-level animation that
        the active-action readers can't see still ships — previously with a wrong
        bake range. Used by scene_exporter's ``set_bake_animation_range``.
        """
        frames = []
        for o in ptk.make_iterable(objects):
            for _level, ad in _AnimUtilsInternal._animation_data_owners(o):
                action, slot = _AnimUtilsInternal._animating(ad)
                if action is not None:
                    rng = _AnimUtilsInternal._key_range(
                        _AnimUtilsInternal._slot_fcurves(action, slot)
                    )
                    if rng is not None:
                        frames.extend(rng)
                frames.extend(_AnimUtilsInternal._nla_strip_frames(ad))
        return (min(frames), max(frames)) if frames else None

    @staticmethod
    def has_nla_or_data_animation(objects):
        """True when *objects* carry animation :meth:`get_fcurves` cannot see:
        non-muted NLA strips (at any animation level), or actions on the data /
        shape-key levels. The exporter's active-action anim checks use this to WARN
        instead of staying vacuously green over animation they never validated."""
        for o in ptk.make_iterable(objects):
            for level, ad in _AnimUtilsInternal._animation_data_owners(o):
                if _AnimUtilsInternal._nla_strip_frames(ad):
                    return True
                if level != "object" and getattr(ad, "action", None) is not None:
                    return True
        return False

    @staticmethod
    def scene_has_animation():
        """True if the blend file contains any action carrying fcurves (keyed motion).

        Mirror of mayatk's ``AnimUtils.scene_has_animation`` (name + behavior): the
        canonical, lightweight "does anything move over time?" check used to early-out
        of a playblast on a static scene. Scans every action's fcurves (slot-aware),
        so it covers all animated datablocks (objects, shape keys, cameras, materials,
        lights, …), not just one. Checks for *existence* of fcurves, not whether they
        carry non-flat motion. Returns ``False`` when Blender is unavailable.
        """
        try:
            import bpy
        except ImportError:
            return False
        return any(
            _AnimUtilsInternal._slot_fcurves(action) for action in bpy.data.actions
        )

    @staticmethod
    def set_current_frame(
        time=None, update=True, relative=False, snap_mode=None, invert_snap=False
    ):
        """Set the scene's current frame, with optional relative offset and clean-number snapping —
        mirror of ``mtk.set_current_frame`` (name + behavior).

        * ``time`` — the desired frame, or the offset when ``relative``; ``None`` re-evaluates/re-snaps
          the CURRENT frame in place — read as ``frame_current_final`` (frame + subframe) when
          available, so snapping a sub-frame playhead (NLA/motion-blur scrubbing) to a clean whole
          number is meaningful; Blender's plain ``frame_current`` is always a whole number already,
          so re-snapping it alone would otherwise be a no-op.
        * ``relative`` — treat ``time`` as an offset from the current frame instead of an absolute one.
        * ``snap_mode`` — any :meth:`pythontk.MathUtils.round_value` mode (``"nearest"``/``"floor"``/
          ``"ceil"``/``"half_up"``/``"preferred"``/``"aggressive_preferred"``/``"none"``), plus Maya's
          ``"aggressive"`` alias for ``"aggressive_preferred"``. ``None``/``"none"`` skips snapping.
        * ``invert_snap`` — swap directional snapping (``floor`` <-> ``ceil``).
        * ``update`` — ``True`` (default) forces a depsgraph re-evaluation (``frame_set``); ``False``
          only writes the integer property (``frame_current``) — Blender's analogue of Maya's "change
          the current time, but do not update the world" flag.

        Returns the frame that was set.
        """
        import bpy

        scene = bpy.context.scene
        if time is None:
            target = getattr(scene, "frame_current_final", scene.frame_current)
        elif relative:
            target = scene.frame_current + time
        else:
            target = time

        if snap_mode and snap_mode.lower() != "none":
            mode = snap_mode.lower()
            if mode == "aggressive":
                mode = "aggressive_preferred"
            if invert_snap:
                if mode == "floor":
                    mode = "ceil"
                elif mode == "ceil":
                    mode = "floor"
            target = ptk.MathUtils.round_value(target, mode=mode)

        target = int(target)
        if update:
            scene.frame_set(target)
        else:
            scene.frame_current = target
        return target

    @staticmethod
    def shift_keys(objects, offset):
        """Shift every key of the given objects by ``offset`` frames."""
        _AnimUtilsInternal._shift_fcurves(_AnimUtilsInternal._fcurves(objects), offset)

    @staticmethod
    def move_keys_to_frame(
        objects,
        frame=None,
        retain_spacing=True,
        selected_keys_only=False,
        align="auto",
        on_replace=None,
        on_move=None,
    ):
        """Move the objects' keys so they align to ``frame`` (default: the current frame).

        ``retain_spacing=True`` applies one global offset — the earliest key across the selection
        lands on ``frame`` and relative timing between objects is kept; ``False`` aligns each
        action's own first key to ``frame``. ``align`` chooses which end of the key range anchors to
        ``frame``: ``"start"`` the earliest key, ``"end"`` the latest, ``"auto"`` whichever end is
        nearer -- End when the keys' midpoint (the selected keys', with ``selected_keys_only``)
        sits before ``frame``, Start otherwise (mayatk's rule). With
        ``selected_keys_only`` only the keys selected in the Dope Sheet / Graph Editor move (the
        selected set's chosen end lands on ``frame``, and an unselected key one lands on is
        replaced, the Graph Editor's auto-merge); returns keys moved. Otherwise returns the
        number of keyed actions (an already-aligned action counts — it is at the target).
        *on_replace* / *on_move* hear the keys replaced and moved, as
        ``align_selected_keyframes``'s do.
        """
        import bpy

        if frame is None:
            frame = bpy.context.scene.frame_current
        if align == "auto":
            # mayatk's rule: the end nearer the frame anchors.
            xs = [
                k.co.x
                for fc in _AnimUtilsInternal._fcurves(objects)
                for k in fc.keyframe_points
                if k.select_control_point or not selected_keys_only
            ]
            align = "end" if xs and (min(xs) + max(xs)) / 2.0 < frame else "start"
        use_end = align == "end"

        if selected_keys_only:
            sel = [
                (fc, k)
                for fc in _AnimUtilsInternal._fcurves(objects)
                for k in fc.keyframe_points
                if k.select_control_point
            ]
            if not sel:
                return 0
            xs = [k.co.x for _fc, k in sel]
            offset = frame - (max(xs) if use_end else min(xs))
            by_fc = {}
            for fc, k in sel:
                by_fc.setdefault(fc, []).append((float(k.co.x), k))
                k.co.x += offset
                k.handle_left.x += offset
                k.handle_right.x += offset
            for fc, moves in by_fc.items():
                replaced = _AnimUtilsInternal._merge_onto_moved(
                    fc, [k for _old, k in moves]
                )
                fc.update()
                _AnimUtilsInternal._report_edit(
                    fc,
                    replaced,
                    [(old, old + offset) for old, _k in moves],
                    on_replace,
                    on_move,
                )
            return len(sel)

        pairs = []
        for action, slot in _AnimUtilsInternal._actions(objects):
            fcurves = _AnimUtilsInternal._slot_fcurves(action, slot)
            rng = _AnimUtilsInternal._key_range(fcurves)
            if rng:
                pairs.append((fcurves, rng))
        if not pairs:
            return 0

        def _anchor(rng):
            return rng[1] if use_end else rng[0]

        def _shift(fcurves, offset):
            # Every key of the action moves, so none is landed on: moves only.
            moved = {
                fc: [(float(t), float(t) + offset) for t in AnimUtils.key_times(fc)]
                for fc in fcurves
            }
            _AnimUtilsInternal._shift_fcurves(fcurves, offset)
            for fc, fc_pairs in moved.items():
                _AnimUtilsInternal._report_edit(fc, [], fc_pairs, None, on_move)

        if retain_spacing:
            global_anchor = (max if use_end else min)(
                _anchor(rng) for _fc, rng in pairs
            )
            for fcurves, _rng in pairs:
                offset = frame - global_anchor
                if offset:
                    _shift(fcurves, offset)
        else:
            for fcurves, rng in pairs:
                offset = frame - _anchor(rng)
                if offset:
                    _shift(fcurves, offset)
        return len(pairs)

    @staticmethod
    def adjust_key_spacing(
        objects,
        spacing=1,
        frame=None,
        relative=False,
        preserve_keys=False,
        selected_keys_only=False,
        exact_gap=False,
        on_replace=None,
        on_move=None,
    ):
        """Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
        frame) — every key at/after ``frame`` shifts by ``spacing``; mirror of
        ``mtk.adjust_key_spacing``. Negative spacing larger than the gap can land keys on the
        ones before ``frame`` (and with ``selected_keys_only`` on an unselected one): such a key
        is REPLACED (the Graph Editor's auto-merge), and *on_replace* / *on_move* hear the keys
        replaced and moved, as ``align_selected_keyframes``'s do. Returns keys shifted.

        * ``objects`` — ``None`` adjusts every scene object (mirrors ``mtk.adjust_key_spacing``'s
          own "If None, adjusts all scene objects" contract, and the sibling ``objects=None``
          convention already used by ``optimize_keys``/``get_animation_info``/``tie_keyframes``/
          ``repair_corrupted_curves``/``fit_playback_range`` in this module) rather than nothing —
          ``tentacle``'s Adjust Spacing "Scope: Entire Scene" option relies on this.
        * ``relative`` — when True and ``frame`` is given, ``frame`` is an offset from the
          current frame (the adjustment point = current frame + ``frame``) instead of an
          absolute frame number. Ignored when ``frame`` is None (always the current frame).
        * ``preserve_keys`` — if a keyframe exists exactly at the adjustment point, re-insert it
          there (same value/interpolation/handle shape) after the shift moves it away, so a key
          stays anchored at the point where the spacing changes -- with what is held on it
          (it is not reported moved), and a moved key the re-insert lands on is replaced.
        * ``selected_keys_only`` — only shift keys selected in the Dope Sheet / Graph Editor.
        * ``exact_gap`` — interpret ``spacing`` as a target gap: shift so the first key at/after
          ``frame`` lands exactly at ``frame + spacing`` (clears a precise range), mirror of Maya.
        """
        import bpy

        scene = bpy.context.scene
        if frame is None:
            adjusted = scene.frame_current
        else:
            adjusted = (frame + scene.frame_current) if relative else frame
        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.data.objects)
        )
        fcurves = AnimUtils.get_fcurves(pool)

        def _affected(k):
            return k.co.x >= adjusted and (
                not selected_keys_only or k.select_control_point
            )

        if exact_gap:
            candidates = [
                k.co.x for fc in fcurves for k in fc.keyframe_points if _affected(k)
            ]
            if not candidates:
                return 0
            shift = (adjusted + spacing) - min(candidates)
        else:
            shift = spacing

        moved = 0
        for fc in fcurves:
            preserved = None
            if preserve_keys:
                for k in fc.keyframe_points:
                    if abs(k.co.x - adjusted) < 1e-4:
                        preserved = (
                            k.co.y,
                            k.interpolation,
                            k.handle_left_type,
                            k.handle_right_type,
                            k.handle_left.x - k.co.x,
                            k.handle_right.x - k.co.x,
                            k.handle_left.y - k.co.y,
                            k.handle_right.y - k.co.y,
                        )
                        break
            moves = []
            for k in fc.keyframe_points:
                if _affected(k):
                    moves.append((float(k.co.x), k))
                    k.co.x += shift
                    k.handle_left.x += shift
                    k.handle_right.x += shift
                    moved += 1
            touched = bool(moves)
            pairs = [(old, old + shift) for old, _k in moves]
            # A moved key landing on one that stayed replaces it.
            replaced = (
                _AnimUtilsInternal._merge_onto_moved(fc, [k for _old, k in moves])
                if moves
                else []
            )
            if preserved is not None:
                # The key at the adjustment point is keyed back there, with what
                # is held on it (no move); a moved key that insert lands on
                # (Blender's insert replaces within 0.01) is replaced -- from its
                # source frame, where what is held on it still is.
                replaced += [old for old, new in pairs if abs(new - adjusted) < 0.01]
                pairs = [
                    (old, new)
                    for old, new in pairs
                    if abs(old - adjusted) >= 1e-4 and abs(new - adjusted) >= 0.01
                ]
                value, interp, hlt, hrt, hl_dx, hr_dx, hl_dy, hr_dy = preserved
                nk = fc.keyframe_points.insert(adjusted, value)
                nk.interpolation = interp
                nk.handle_left_type = hlt
                nk.handle_right_type = hrt
                nk.handle_left.x = adjusted + hl_dx
                nk.handle_left.y = value + hl_dy
                nk.handle_right.x = adjusted + hr_dx
                nk.handle_right.y = value + hr_dy
                touched = True
            if touched:
                fc.update()
            _AnimUtilsInternal._report_edit(fc, replaced, pairs, on_replace, on_move)
        return moved

    @staticmethod
    def align_selected_keyframes(
        objects, target_frame=None, use_earliest=True, on_replace=None, on_move=None
    ):
        """Shift each object's SELECTED keyframes (``select_control_point``, e.g. picked in
        the Dope Sheet / Graph Editor) so every object's selection starts on one frame --
        mirror of ``mtk.align_selected_keyframes``.

        Each object's selection moves as ONE block, keeping its spacing, and keys that are
        not selected stay put (a key the block lands on is replaced, as the Graph Editor's
        auto-merge does).  The auto target is the earliest (or, ``use_earliest=False``, the
        latest) per-object selection START; *target_frame* overrides it.  An fcurve passed
        in aligns with the other curves of its own action.  *on_replace* is called as
        ``on_replace(fcurve, frames)`` with the frames of the keys a moved key replaced,
        then *on_move* as ``on_move(fcurve, pairs)`` with the ``(old, new)`` frames of the
        keys moved -- for a caller holding something on them (the shot system's claims:
        ``ShotStore.release_replaced`` / ``remap_moved``).

        Returns the number of keys moved (0 = nothing selected, or already aligned).
        """
        objs, curves = [], []
        for o in ptk.make_iterable(objects):
            (curves if _AnimUtilsInternal._is_fcurve(o) else objs).append(o)
        groups = [
            _AnimUtilsInternal._slot_fcurves(action, slot)
            for _o, action, slot in _AnimUtilsInternal._owned_actions(objs)
        ]
        by_action = {}
        for fc in curves:
            by_action.setdefault(fc.id_data.as_pointer(), []).append(fc)
        groups.extend(by_action.values())

        blocks = []  # [(start, [(fc, [selected points])])]
        for fcurves in groups:
            picked = []
            for fc in fcurves:
                pts = [k for k in fc.keyframe_points if k.select_control_point]
                if pts:
                    picked.append((fc, pts))
            if picked:
                start = min(k.co.x for _fc, pts in picked for k in pts)
                blocks.append((start, picked))
        if not blocks:
            return 0
        if target_frame is None:
            starts = [start for start, _picked in blocks]
            target_frame = min(starts) if use_earliest else max(starts)

        moved = 0
        for start, picked in blocks:
            delta = target_frame - start
            if abs(delta) < 1e-6:
                continue
            for fc, pts in picked:
                pairs = [(float(k.co.x), float(k.co.x) + delta) for k in pts]
                for k in pts:
                    k.co.x += delta
                    k.handle_left.x += delta
                    k.handle_right.x += delta
                moved += len(pts)
                replaced = _AnimUtilsInternal._merge_onto_moved(fc, pts)
                fc.update()
                _AnimUtilsInternal._report_edit(
                    fc, replaced, pairs, on_replace, on_move
                )
        return moved

    @staticmethod
    def set_visibility_keys(
        objects,
        visible=True,
        frame=None,
        when="current",
        offset=0,
        group_overlapping=False,
        on_replace=None,
    ):
        """Key viewport + render visibility (``hide_viewport``/``hide_render``) — mirror of
        ``mtk.set_visibility_keys``.

        ``when`` chooses the frame(s): ``"current"`` (the playhead / ``frame``) or, relative to each
        object's own keyed range, ``"start"`` / ``"end"`` / ``"both"`` / ``"before_start"`` /
        ``"after_end"``; ``offset`` nudges every chosen frame. ``group_overlapping`` treats objects
        whose key ranges overlap (strictly — merely touching ranges stay separate, matching Maya's
        ``_group_overlapping_keyframes``) as one group sharing a combined range for the ``when``
        calculation (ignored for ``"current"``, which needs no range); reuses
        :func:`stagger_keys._group_units` as the grouping model. Returns the objects keyed (objects
        with no key range are skipped for the range-relative modes).  A visibility key on a frame
        that held the other state is replaced, and *on_replace* hears it (``on_replace(fcurve,
        frames)``, as ``align_selected_keyframes``'s)."""
        import bpy

        scene = bpy.context.scene
        keyed = []

        if group_overlapping and when != "current":
            units = []
            for o in ptk.make_iterable(objects):
                rng = _AnimUtilsInternal._key_range(AnimUtils.get_fcurves([o]))
                if rng is not None:
                    units.append({"obj": o, "start": rng[0], "end": rng[1]})
            for block in StaggerKeys._group_units(units, merge_touching=False):
                b_start = min(u["start"] for u in block)
                b_end = max(u["end"] for u in block)
                frames = _AnimUtilsInternal._when_frames(when, b_start, b_end, offset)
                for u in block:
                    _AnimUtilsInternal._key_visibility(
                        u["obj"], frames, visible, on_replace
                    )
                    keyed.append(u["obj"])
            return keyed

        for o in ptk.make_iterable(objects):
            frames = _AnimUtilsInternal._visibility_key_frames(
                o, when, frame, offset, scene
            )
            if not frames:
                continue
            _AnimUtilsInternal._key_visibility(o, frames, visible, on_replace)
            keyed.append(o)
        return keyed

    @staticmethod
    def add_intermediate_keys(
        objects, step=1.0, time_range=None, ignore_visibility=False, percent=None
    ):
        """Insert sampled keys every ``step`` frames between each fcurve's first and last key
        (existing keys untouched) — mirror of ``mtk.add_intermediate_keys``. Returns keys added.

        * ``time_range`` — a ``(start, end)`` window; only frames inside it get keys.
        * ``ignore_visibility`` — skip ``hide_viewport``/``hide_render`` curves (leave vis keys alone).
        * ``percent`` — 0-100; when given, overrides ``step`` and mirrors ``mtk.add_intermediate_keys``'s
          density control: each curve's OWN interior integer frames (strictly between its first/last
          key, or the ``time_range`` bounds) are evenly subsampled down to that fraction, so the
          density scales with the curve's own span instead of a fixed frame count.
        """
        import bisect

        added = 0
        for fc in _AnimUtilsInternal._fcurves(objects):
            if ignore_visibility and _AnimUtilsInternal._is_visibility_fcurve(fc):
                continue
            pts = fc.keyframe_points
            if len(pts) < 2:
                continue
            existing = sorted(k.co.x for k in pts)
            start, end = existing[0], existing[-1]
            lo, hi = time_range if time_range is not None else (start, end)

            if percent is not None:
                # Clamp to this curve's own keyed extent — an unbounded ``time_range`` side
                # (e.g. a caller-supplied +/-1e9 sentinel) must not blow up the interior list.
                clo, chi = max(start, lo), min(end, hi)
                interior = list(range(int(round(clo)) + 1, int(round(chi))))
                if not interior:
                    continue
                count = max(1, round(len(interior) * (percent / 100.0)))
                stride = max(1, len(interior) // count)
                candidates = interior[::stride]
            else:
                candidates = []
                f = start + step
                while f < end - 1e-6:
                    if lo - 1e-6 <= f <= hi + 1e-6:
                        candidates.append(f)
                    f += step

            frames = []
            for f in candidates:
                i = bisect.bisect_left(existing, f)
                # Blender's insert REPLACES a key within 0.01 of its frame (its
                # value rewritten): a candidate that close is the key's own frame.
                near = any(
                    abs(existing[j] - f) < 0.01
                    for j in (i - 1, i)
                    if 0 <= j < len(existing)
                )
                if not near:
                    frames.append(f)
            # Sample BEFORE inserting: each insert re-smooths the curve's handles, so
            # evaluating as we go would drift later samples off the original curve.
            samples = [(f, fc.evaluate(f)) for f in frames]
            for f, v in samples:
                pts.insert(f, v)
                added += 1
            fc.update()
        return added

    @staticmethod
    def remove_intermediate_keys(
        objects, time_range=None, ignore_visibility=False, on_delete=None
    ):
        """Remove every key strictly between each fcurve's first and last (keeps only the
        endpoints) — mirror of ``mtk.remove_intermediate_keys``. Returns keys removed.

        * ``time_range`` — a ``(start, end)`` window; only interior keys STRICTLY inside it are
          removed, so a key sitting on either end survives, as mayatk cuts
          ``(start + 0.001, end - 0.001)``.  The shot sequencer hands the key selection's own
          span here, and the selection's first and last keys are the ones it keeps.
        * ``ignore_visibility`` — skip ``hide_viewport``/``hide_render`` curves.
        * ``on_delete(fcurve, frames)`` — hears the frames of the keys removed, per fcurve
          (what the shot system releases its claims on).
        """
        removed = 0
        for fc in _AnimUtilsInternal._fcurves(objects):
            if ignore_visibility and _AnimUtilsInternal._is_visibility_fcurve(fc):
                continue
            pts = fc.keyframe_points
            if len(pts) <= 2:
                continue
            gone = []
            # Walk interior keys high→low so removals don't shift unvisited indices; endpoints
            # (index 0 and the last) are never touched.
            for i in range(len(pts) - 2, 0, -1):
                x = pts[i].co.x
                if (
                    time_range is None
                    or time_range[0] + 1e-3 < x < time_range[1] - 1e-3
                ):
                    gone.append(float(x))
                    pts.remove(pts[i], fast=True)
                    removed += 1
            fc.update()
            if gone and on_delete is not None:
                on_delete(fc, sorted(gone))
        return removed

    @staticmethod
    def select_keys(objects, time=None, add_to_selection=False):
        """Select keyframe points (``select_control_point`` — visible in the Dope Sheet /
        Graph Editor) — mirror of ``mtk.select_keys``.

        ``time`` is ``None`` (default) for all keys, a ``(start, end)`` frame range, or one of the
        current-frame-relative scopes shared with :func:`delete_keys` (Maya's cmb041/cmb004 lists):
        ``"current"``, ``"before"``, ``"before|current"``, ``"after"``, ``"after|current"``.
        ``add_to_selection`` keeps out-of-scope keys selected rather than deselecting them. Returns
        the number of keys selected. Maya's ``channel_box_only`` has no Blender analogue (no Channel
        Box — see ``parity_map.py``)."""
        predicate = None
        current = None
        if isinstance(time, str):
            predicate = _DELETE_KEYS_SCOPES.get(time)
            if predicate is None:
                raise ValueError(f"Unknown select_keys time scope: {time!r}")
            import bpy

            current = bpy.context.scene.frame_current

        selected = 0
        for fc in _AnimUtilsInternal._fcurves(objects):
            for k in fc.keyframe_points:
                if predicate is not None:
                    hit = predicate(k.co.x, current)
                else:
                    hit = time is None or time[0] <= k.co.x <= time[1]
                if hit:
                    k.select_control_point = True
                    selected += 1
                elif not add_to_selection:
                    k.select_control_point = False
        return selected

    @staticmethod
    def invert_keys(
        objects,
        mode="time",
        value_pivot=0.0,
        start_frame=None,
        relative=True,
        delete_original=False,
        selected_only=False,
        on_replace=None,
        on_move=None,
    ):
        """Mirror keys to reverse motion — Blender analogue of Maya's invert (modes mirror its X/Y/both
        time/value/both, plus the reversed-copy semantics of Maya's ``time``/``relative``/
        ``delete_original``).

        ``start_frame=None`` (default) mirrors the keys IN PLACE: a move, not a copy — the animation
        reverses within its own key range; ``relative``/``delete_original`` are ignored in that case.
        When ``start_frame`` is given, a REVERSED COPY is placed instead: the copy's end (mirroring the
        source's last key) lands at ``max_key_frame + start_frame`` (``relative=True``, the default) or
        at the absolute frame ``start_frame`` (``relative=False``); the source keys are kept unless
        ``delete_original`` is set (a source key that lands on the same frame+value as a copy key is
        never removed — the copy already occupies that point). ``mode`` picks what gets mirrored:
        ``'time'`` (frames), ``'value'`` (about ``value_pivot``), or ``'both'``. Pure
        ``keyframe_points`` math → headless-safe.

        ``selected_only`` mirrors only the keyframe points selected in the Dope Sheet / Graph
        Editor, in place, over the combined range of every selected key -- mayatk's
        invert-with-a-selection: ``t' = min + max - t``, the unselected keys stay put, a key
        landing on one replaces it, and on a time flip each segment's interpolation is
        re-homed to the key that now precedes it (a ``CONSTANT`` hold stays a hold of the
        same span).  *objects* may then be fcurves (the channels to read).  Returns the
        number of keys mirrored; ``start_frame`` / ``relative`` / ``delete_original`` do not
        apply.

        *on_replace* is called as ``on_replace(fcurve, frames)`` with the frames of the keys
        a mirrored key REPLACED (as ``align_selected_keyframes``): with ``selected_only``, an
        unselected key a mirrored one lands on; with a reversed copy, a source key another
        key's copy lands on (none when ``delete_original`` moves every source on).  A key its
        own copy lands on is only re-keyed, and the whole-range in-place mirror moves every
        key, so it replaces none.  *on_move* is then called as ``on_move(fcurve, pairs)``
        with the ``(old, new)`` frames of the keys the edit moved: every mirrored key in
        place, every source of a copy that deletes them, none of a copy that keeps them.
        Mirror of mayatk's ``invert_keys``."""
        do_time = mode in ("time", "both")
        do_value = mode in ("value", "both")
        if selected_only:
            return _AnimUtilsInternal._invert_selected(
                _AnimUtilsInternal._fcurves(objects),
                do_time,
                do_value,
                value_pivot,
                on_replace,
                on_move,
            )
        for action, slot in _AnimUtilsInternal._actions(objects):
            fcurves = _AnimUtilsInternal._slot_fcurves(action, slot)
            rng = _AnimUtilsInternal._key_range(fcurves)
            if not rng:
                continue
            lo, hi = rng

            if start_frame is None:
                center = (lo + hi) / 2.0
                for fc in fcurves:
                    # Every key moves (nothing is replaced): t' = 2c - t.
                    pairs = [
                        (float(k.co.x), 2.0 * center - float(k.co.x))
                        for k in fc.keyframe_points
                        if do_time
                    ]
                    for k in fc.keyframe_points:
                        if do_time:
                            k.co.x = 2.0 * center - k.co.x
                            k.handle_left.x = 2.0 * center - k.handle_left.x
                            k.handle_right.x = 2.0 * center - k.handle_right.x
                        if do_value:
                            k.co.y = 2.0 * value_pivot - k.co.y
                            k.handle_left.y = 2.0 * value_pivot - k.handle_left.y
                            k.handle_right.y = 2.0 * value_pivot - k.handle_right.y
                        if do_time:
                            # Time reversal reflects handles horizontally, so the handle
                            # that was on the right now sits on the left (and vice versa):
                            # swap the two sides entirely (x, y, type). AUTO/AUTO_CLAMPED
                            # keys have equal types and get recomputed by fc.update() below,
                            # so this only corrects FREE/ALIGNED/custom-tangent keys.
                            k.handle_left.x, k.handle_right.x = (
                                k.handle_right.x,
                                k.handle_left.x,
                            )
                            k.handle_left.y, k.handle_right.y = (
                                k.handle_right.y,
                                k.handle_left.y,
                            )
                            k.handle_left_type, k.handle_right_type = (
                                k.handle_right_type,
                                k.handle_left_type,
                            )
                    fc.update()
                    _AnimUtilsInternal._report_edit(fc, [], pairs, None, on_move)
                continue

            inversion_point = (hi + start_frame) if relative else start_frame
            originals = [
                (
                    fc,
                    k.co.x,
                    k.co.y,
                    k.interpolation,
                    k.handle_left_type,
                    k.handle_right_type,
                    k.handle_left.x - k.co.x,
                    k.handle_right.x - k.co.x,
                    k.handle_left.y - k.co.y,
                    k.handle_right.y - k.co.y,
                )
                for fc in fcurves
                for k in fc.keyframe_points
            ]
            touched = set()
            new_frames_by_fc = {}
            moves_by_fc = {}  # fc -> [(source frame, copy frame)]
            for fc, ox, oy, interp, hlt, hrt, hl_dx, hr_dx, hl_dy, hr_dy in originals:
                new_x = (inversion_point - (ox - hi)) if do_time else ox
                new_y = (2.0 * value_pivot - oy) if do_value else oy
                moves_by_fc.setdefault(fc, []).append((ox, new_x))
                nk = fc.keyframe_points.insert(new_x, new_y)
                nk.interpolation = interp
                # Time-reversal reflects handles horizontally, so the handle that was
                # on the right now sits on the left (and vice versa): swap the source
                # side for the x-offset, y-offset AND handle type together. Value-
                # reversal then flips the y-offset about the same pivot as the key.
                # Mirrors the in-place branch above (which swaps all three); leaving
                # y/type unswapped here made the copy path disagree with it.
                if do_time:
                    new_hl_dx, new_hr_dx = -hr_dx, -hl_dx
                    src_hl_dy, src_hr_dy = hr_dy, hl_dy
                    new_hlt, new_hrt = hrt, hlt
                else:
                    new_hl_dx, new_hr_dx = hl_dx, hr_dx
                    src_hl_dy, src_hr_dy = hl_dy, hr_dy
                    new_hlt, new_hrt = hlt, hrt
                nk.handle_left_type = new_hlt
                nk.handle_right_type = new_hrt
                new_hl_dy = -src_hl_dy if do_value else src_hl_dy
                new_hr_dy = -src_hr_dy if do_value else src_hr_dy
                nk.handle_left.x = new_x + new_hl_dx
                nk.handle_left.y = new_y + new_hl_dy
                nk.handle_right.x = new_x + new_hr_dx
                nk.handle_right.y = new_y + new_hr_dy
                touched.add(fc)
                new_frames_by_fc.setdefault(fc, set()).add(round(new_x, 3))

            if delete_original:
                for fc, ox, oy, *_rest in originals:
                    if round(ox, 3) in new_frames_by_fc.get(fc, ()):
                        continue  # a copy key already occupies this frame — nothing to remove
                    for k in list(fc.keyframe_points):
                        if abs(k.co.x - ox) < 1e-4 and abs(k.co.y - oy) < 1e-4:
                            fc.keyframe_points.remove(k, fast=True)
                            break

            for fc in touched:
                fc.update()
            # A copy inserted on a source key overwrote it (Blender's insert
            # replaces a key within 0.01 frame); every key here is a source, so
            # delete_original, which moves them all on, replaces none -- and
            # only it moves anything: a copy that keeps its sources moves none.
            for fc, moves in moves_by_fc.items():
                frames = (
                    _AnimUtilsInternal._landed_on(
                        [s for s, _d in moves], moves, moving=delete_original, eps=0.01
                    )
                    if on_replace is not None
                    else []
                )
                _AnimUtilsInternal._report_edit(
                    fc,
                    frames,
                    moves if delete_original else [],
                    on_replace,
                    on_move,
                )

    @staticmethod
    def snap_keys(
        objects=None,
        selected_only=False,
        time_range=None,
        method="nearest",
        on_move=None,
    ):
        """Snap keys to whole frames (or "clean" numbers) — mirror of ``mtk.snap_keys_to_frames``.

        ``method`` is any :meth:`pythontk.MathUtils.round_value` mode — DRY reuse of the same
        DCC-agnostic rounding table mayatk composes with: ``"nearest"`` (default), ``"floor"``,
        ``"ceil"``, ``"half_up"``, ``"preferred"``/``"aggressive_preferred"`` (round to clean
        numbers — 24→25, 48→50 — when close), or ``"none"`` (no-op).

        * ``selected_only`` — only snap keys selected in the Dope Sheet / Graph Editor.
        * ``time_range`` — a ``(start, end)`` window; only keys inside it are snapped.

        ``objects`` defaults to every scene object, as ``repair_corrupted_curves`` does — the
        scope a repair pass wants when nothing is selected.

        A snap never replaces a key (mayatk's contract): one whose target frame is taken
        stays where it is.  Per fcurve the candidates go highest time first, and a target
        within 1e-4 of a whole-frame key or of a frame an earlier candidate took is skipped
        -- so of two keys snapping onto one frame the later takes it.  A key with a
        non-finite (corrupt) time is left to :meth:`repair_corrupted_curves`.  *on_move*
        hears the ``(old, new)`` frames of the keys moved (the shot system's claims ride
        along: ``ShotStore.remap_moved``).

        Returns the number of keys that actually moved."""
        if method == "none":
            return 0
        import bisect
        import math

        import bpy

        pool = objects if objects is not None else list(bpy.data.objects)
        snapped = 0
        for fc in _AnimUtilsInternal._fcurves(pool):
            candidates = []
            occupied = []  # sorted below: a bisect per candidate, not a scan
            for k in fc.keyframe_points:
                x = float(k.co.x)
                if not math.isfinite(x):
                    continue  # a corrupt time (Repair's job): nothing to round
                if x.is_integer():
                    occupied.append(x)  # a whole-frame key holds its frame
                if selected_only and not k.select_control_point:
                    continue
                # Inclusive with a slop: ``co.x`` is float32, so a fractional
                # key read back (10.4 -> 10.3999996) falls just outside a range
                # built from the frame it was keyed on -- exactly the keys a
                # snap exists for.
                if time_range is not None and not (
                    time_range[0] - 1e-3 <= x <= time_range[1] + 1e-3
                ):
                    continue
                r = ptk.MathUtils.round_value(x, mode=method)
                if r != x:
                    candidates.append((x, float(r), k))
            if not candidates:
                continue
            occupied.sort()
            pairs = []
            for x, r, k in sorted(candidates, key=lambda c: c[0], reverse=True):
                i = bisect.bisect_left(occupied, r - 1e-4)
                if i < len(occupied) and occupied[i] <= r + 1e-4:
                    continue  # taken: the key stays, never a replacement
                delta = r - k.co.x
                k.co.x = r
                k.handle_left.x += delta
                k.handle_right.x += delta
                bisect.insort(occupied, r)
                pairs.append((x, r))
            if not pairs:
                continue
            snapped += len(pairs)
            fc.update()
            _AnimUtilsInternal._report_edit(fc, [], pairs, None, on_move)
        return snapped

    @staticmethod
    def set_interpolation(objects, interpolation="CONSTANT", handle=None):
        """Set fcurve key ``interpolation`` (``CONSTANT`` / ``LINEAR`` / ``BEZIER`` / ``SINE`` …) on
        every key of the selection — the Blender analogue of Maya's per-key tangent type. ``handle``
        (``AUTO`` / ``AUTO_CLAMPED`` / ``VECTOR`` / ``ALIGNED`` / ``FREE``) optionally sets both bezier
        handle types too. Returns the number of fcurves touched."""
        interp = interpolation.upper()
        n = 0
        for fc in _AnimUtilsInternal._fcurves(objects):
            for k in fc.keyframe_points:
                k.interpolation = interp
                if handle:
                    k.handle_left_type = k.handle_right_type = handle.upper()
            fc.update()
            n += 1
        return n

    @staticmethod
    def set_stepped(objects, stepped=True):
        """Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key."""
        AnimUtils.set_interpolation(objects, "CONSTANT" if stepped else "BEZIER")

    @staticmethod
    def step_visibility_keys(objects):
        """Force CONSTANT interpolation on the ``hide_viewport`` / ``hide_render``
        fcurves of *objects*, leaving every other curve alone. Returns the number of
        fcurves stepped.

        Visibility is boolean and Blender's default is Bezier, which RAMPS the
        toggle: a show/hide replayed with interpolated keys leaves the object part
        drawn for the frames between them. The narrow counterpart of
        :meth:`set_interpolation`, which steps every curve an object owns — a carrier
        replays visibility onto objects whose transforms are keyed in the same
        action."""
        stepped = 0
        for fc in _AnimUtilsInternal._fcurves(objects):
            if not _AnimUtilsInternal._is_visibility_fcurve(fc):
                continue
            for key in fc.keyframe_points:
                key.interpolation = "CONSTANT"
            fc.update()
            stepped += 1
        return stepped

    @staticmethod
    def delete_keys(objects, time=None, on_delete=None):
        """Remove animation from the given objects — mirror of ``mtk.delete_keys``.

        ``time`` is ``None`` (default, backward-compatible) to clear all animation outright
        (``animation_data_clear`` — the whole action goes), or one of Maya's ``cmb004`` time-scope
        values to only remove keys in that window relative to the current frame: ``"current"``,
        ``"before"``, ``"before|current"``, ``"after"``, ``"after|current"`` (Maya's ``"all"`` maps to
        ``None`` at the call site). Returns the objects touched (cleared outright, or with at least
        one key removed for a scoped ``time``).

        ``on_delete(fcurve, frames)`` hears the frames of the keys removed, per fcurve (what the
        shot system releases its claims on) -- for a clear, every key of the action, BEFORE it
        goes, while the fcurves still resolve to their owner."""
        if time is None:
            cleared = []
            for o in ptk.make_iterable(objects):
                if getattr(o, "animation_data", None):
                    if on_delete is not None:
                        for fc in AnimUtils.get_fcurves([o]):
                            frames = sorted(float(k.co.x) for k in fc.keyframe_points)
                            if frames:
                                on_delete(fc, frames)
                    o.animation_data_clear()
                    cleared.append(o)
            return cleared

        predicate = _DELETE_KEYS_SCOPES.get(time)
        if predicate is None:
            raise ValueError(f"Unknown delete_keys time scope: {time!r}")

        import bpy

        current = bpy.context.scene.frame_current
        touched = []
        for o in ptk.make_iterable(objects):
            obj_touched = False
            for fc in AnimUtils.get_fcurves([o]):
                pts = fc.keyframe_points
                gone = []
                for i in range(len(pts) - 1, -1, -1):
                    if predicate(pts[i].co.x, current):
                        gone.append(float(pts[i].co.x))
                        pts.remove(pts[i], fast=True)
                if gone:
                    fc.update()
                    obj_touched = True
                    if on_delete is not None:
                        on_delete(fc, sorted(gone))
            if obj_touched:
                touched.append(o)
        return touched

    @staticmethod
    def fit_playback_range(objects=None):
        """Set the scene frame range to the keyed extent of ``objects`` (or every scene object).

        Returns the (start, end) applied, or None when nothing is keyed.
        """
        import bpy

        pool = ptk.make_iterable(objects) if objects is not None else bpy.data.objects
        rng = _AnimUtilsInternal._key_range(_AnimUtilsInternal._fcurves(pool))
        if not rng:
            return None
        scene = bpy.context.scene
        scene.frame_start = int(rng[0])
        scene.frame_end = max(int(rng[1]), int(rng[0]))
        return scene.frame_start, scene.frame_end

    @staticmethod
    def copy_keys(source, mode="action"):
        """Return a copy-buffer for :func:`paste_keys` — mirror of ``mtk.AnimUtils.copy_keys`` (same
        mode vocabulary, minus Maya's Channel-Box mode, which has no Blender analogue).

        * ``"action"`` (default) — the whole Action datablock driving ``source``, Blender's native
          "everything, as one object" copy; :func:`paste_keys` links an independent copy of it.
        * ``"current_frame"`` — a pose snapshot: every animated property's evaluated value at the
          current frame (Maya's Current Frame mode — values only, no key timing carried).
        * ``"selected"`` — only the keyframe points selected in the Dope Sheet / Graph Editor, per
          fcurve, each with its own frame + value.

        Returns ``None`` when there is nothing to copy for the given source/mode."""
        if mode == "action":
            ad = getattr(source, "animation_data", None)
            return ad.action if ad else None

        import bpy

        if mode == "current_frame":
            frame = bpy.context.scene.frame_current
            values = {
                (fc.data_path, fc.array_index): fc.evaluate(frame)
                for fc in AnimUtils.get_fcurves([source])
            }
            return (
                {"mode": "current_frame", "frame": frame, "values": values}
                if values
                else None
            )

        if mode == "selected":
            keys, tangents, extrapolation = {}, {}, {}
            for fc in AnimUtils.get_fcurves([source]):
                sel = [k for k in fc.keyframe_points if k.select_control_point]
                if not sel:
                    continue
                path = (fc.data_path, fc.array_index)
                keys[path] = [(k.co.x, k.co.y) for k in sel]
                # mayatk copies with ``tangent_detail``: the shape travels with
                # the keys.  Kept beside the (frame, value) pairs, which stay
                # the buffer's documented shape.
                tangents[path] = [
                    {
                        "interpolation": k.interpolation,
                        "easing": k.easing,
                        "handle_left_type": k.handle_left_type,
                        "handle_right_type": k.handle_right_type,
                        "handle_left": (
                            k.handle_left.x - k.co.x,
                            k.handle_left.y - k.co.y,
                        ),
                        "handle_right": (
                            k.handle_right.x - k.co.x,
                            k.handle_right.y - k.co.y,
                        ),
                    }
                    for k in sel
                ]
                extrapolation[path] = fc.extrapolation
            if not keys:
                return None
            return {
                "mode": "selected",
                "keys": keys,
                "tangents": tangents,
                "extrapolation": extrapolation,
            }

        raise ValueError(f"Unknown copy_keys mode: {mode!r}")

    @staticmethod
    def paste_keys(objects, buffer, target_time=None, on_replace=None):
        """Paste a copy-buffer from :func:`copy_keys` onto ``objects`` — mirror of
        ``mtk.AnimUtils.paste_keys``.

        * An ``"action"``-mode buffer (an Action datablock) links an independent COPY of it to each
          target — Blender's native "paste the whole animation" (the original two-arg behavior).
        * A ``"current_frame"``/``"selected"``-mode buffer (a dict from :func:`copy_keys`) keys only
          the captured property values back onto each target's own action (creating one if needed).
        * ``target_time`` — ``None`` (default) pastes at the buffer's own original frame(s) unshifted
          ("at copy frame"); a frame number re-anchors it there instead ("at playhead") — for
          multi-key buffers the EARLIEST captured frame aligns to ``target_time`` and every other key
          keeps its relative offset.
        * ``on_replace`` — called as ``on_replace(fcurve, frames)`` with the frames of the keys a
          paste replaced, as ``align_selected_keyframes``'s: a key on a pasted frame (Blender's
          insert replaces within 0.01) holding another value -- pasting the value a key already
          holds only re-keys it.  An ``"action"`` paste replaces each target's whole action, so
          every key of it the new one does not hold at that frame with that value.

        Returns the objects pasted onto."""
        if buffer is None:
            return []

        if isinstance(buffer, dict):
            mode = buffer.get("mode")
            if mode == "current_frame":
                return _AnimUtilsInternal._paste_pose(
                    objects, buffer, target_time, on_replace
                )
            if mode == "selected":
                return _AnimUtilsInternal._paste_selected_keys(
                    objects, buffer, target_time, on_replace
                )
            raise ValueError(f"Unknown paste_keys buffer mode: {mode!r}")

        pasted = []
        for o in ptk.make_iterable(objects):
            if o.animation_data is None:
                o.animation_data_create()
            copy = buffer.copy()
            slots = getattr(
                copy, "slots", None
            )  # slotted actions need an explicit slot pick
            fcurves = _AnimUtilsInternal._slot_fcurves(
                copy, slots[0] if slots else None
            )
            shift = 0.0
            if target_time is not None:
                rng = _AnimUtilsInternal._key_range(fcurves)
                if rng is not None:
                    shift = target_time - rng[0]
            if on_replace is not None:
                # BEFORE the swap: after it the old fcurves are no longer the target's.
                _AnimUtilsInternal._report_action_swap(o, fcurves, shift, on_replace)
            o.animation_data.action = copy
            if slots:
                o.animation_data.action_slot = slots[0]
            if shift:
                _AnimUtilsInternal._shift_fcurves(fcurves, shift)
            pasted.append(o)
        return pasted

    @staticmethod
    def transfer_keyframes(
        objects, relative=False, optimize=False, on_replace=None, on_delete=None
    ):
        """Transfer keyframes from the first object (source) onto the rest (targets) — mirror of
        ``mtk.AnimUtils.transfer_keyframes`` (``source = objects[0]``, targets = the remainder, same
        convention as :func:`blendertk.xform_utils.transfer_pivot`).

        Built on :func:`copy_keys` / :func:`paste_keys`'s ``"action"`` mode — each target gets its
        own independent copy of the source's Action (so the per-target value offset below never
        cross-talks between targets) — rather than a parallel keyframe-copy path.

        Parameters:
            objects: ``[source, *targets]``.
            relative (bool): if True, each target keeps its OWN current pose as the animation's
                base: for every fcurve (matched by ``data_path``/``array_index``) the pasted values
                are shifted so the value at the SOURCE's own earliest keyed frame lands on the
                target's pre-transfer value at that same address, and every other keyed value keeps
                the same offset — mirrors ``mtk.transfer_keyframes``'s relative semantics (e.g.
                transferring a walk cycle from a reference rig onto several differently-posed
                targets preserves each target's own base pose instead of snapping them all to the
                source's literal values). If False (default), values are copied verbatim (absolute)
                — the prior/only Blender behavior.
            optimize (bool): if True, run :func:`optimize_keys` on the source before transferring.
            on_replace: as :func:`paste_keys`'s -- each target's own keys are replaced by the
                source's action (bar one it holds at that frame with that value; with
                ``relative``, the offset value the target ends with).
            on_delete: as :func:`optimize_keys`'s -- the SOURCE keys (and curves) the
                ``optimize`` pass drops.

        Returns the targets that received keys (empty list if the source has no keys, or there are
        no targets).
        """
        objects = [o for o in ptk.make_iterable(objects) if o]
        if len(objects) < 2:
            return []
        source = objects[0]
        targets = [o for o in objects[1:] if o is not source]
        if not targets:
            return []

        if optimize:
            AnimUtils.optimize_keys([source], on_delete=on_delete)

        src_fcurves = AnimUtils.get_fcurves([source])
        if not src_fcurves:
            return []

        # Relative mode: each target's offset per (data_path, array_index) -- its CURRENT
        # value (snapshotted BEFORE paste_keys overwrites it with the source's action: the
        # "own base pose" relative mode preserves) less the source fcurve's OWN earliest keyed
        # value (mirrors mtk's "this attribute's own first key", not the
        # global-earliest-frame-across-all-curves value).
        offsets = {}
        if relative:
            src_first_values = {
                (fc.data_path, fc.array_index): min(
                    fc.keyframe_points, key=lambda k: k.co.x
                ).co.y
                for fc in src_fcurves
                if len(fc.keyframe_points)
            }
            for target in targets:
                own = {}
                for addr, src_first in src_first_values.items():
                    tgt_val = _AnimUtilsInternal._get_path_value(target, *addr)
                    if tgt_val is not None and tgt_val - src_first:
                        own[addr] = tgt_val - src_first
                offsets[target] = own

        action = AnimUtils.copy_keys(source, mode="action")
        if relative and on_replace is not None:
            # A target ends with the source's values PLUS its offset, so the keys the swap
            # replaces are read against those -- paste_keys would compare the bare source
            # values (mayatk's transfer compares the values it writes).  Before the paste,
            # as paste_keys reports, while the old fcurves still resolve to their owner.
            slots = getattr(action, "slots", None)
            incoming = _AnimUtilsInternal._slot_fcurves(
                action, slots[0] if slots else None
            )
            for target in targets:
                _AnimUtilsInternal._report_action_swap(
                    target, incoming, 0.0, on_replace, value_offsets=offsets[target]
                )
            pasted = AnimUtils.paste_keys(targets, action)
        else:
            pasted = AnimUtils.paste_keys(targets, action, on_replace=on_replace)
        if not pasted or not relative:
            return pasted

        for target in pasted:
            own = offsets.get(target) or {}
            for fc in AnimUtils.get_fcurves([target]):
                offset = own.get((fc.data_path, fc.array_index))
                if not offset:
                    continue
                for kp in fc.keyframe_points:
                    kp.co.y += offset
                    kp.handle_left.y += offset
                    kp.handle_right.y += offset
                fc.update()

        return pasted

    @staticmethod
    def reduce_to_extremes(
        objects=None, value_tolerance=0.001, stats=None, max_error=None, on_delete=None
    ):
        """Reduce baked fcurves to their shape-defining keys and refit the handles —
        mirror of ``mtk.AnimUtils.reduce_to_extremes``.

        A per-frame bake thinned to its shape, not undone (reversing a bake is
        ``SmartBake.restore``): each curve keeps only its endpoints, peaks,
        valleys and hold boundaries; the tweens are deleted and the survivors get
        Bezier handles fitted by least squares against the deleted samples, so the
        sparse curve traces the baked motion.  Holds stay exactly flat (broken
        handles); elsewhere handles are ``ALIGNED``.  Curves with stepped
        (``CONSTANT``) keys are left untouched.

        ``objects`` defaults to every scene object.  Pass a dict as ``stats`` to
        receive ``reduced`` (curve count), ``reduce_keys_removed`` and
        ``reduce_max_error`` (largest deviation from the baked samples).
        ``max_error`` bounds that deviation (None = 1% of each curve's own
        amplitude; 0 keeps the extrema alone).  Returns the reduced fcurves.
        ``on_delete(fcurve, frames)`` hears the frames of the tweens removed, per fcurve
        (what the shot system releases its claims on).
        """
        import bpy

        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.data.objects)
        )
        reduced, removed, worst_error = [], 0, 0.0
        for _o, action, slot in _AnimUtilsInternal._owned_actions(pool):
            for fc in list(_AnimUtilsInternal._slot_fcurves(action, slot)):
                before = set(AnimUtils.key_times(fc)) if on_delete is not None else None
                result = _AnimUtilsInternal._reduce_fcurve_to_extremes(
                    fc, value_tolerance, max_error
                )
                if result is None:
                    continue
                reduced.append(fc)
                if before is not None:
                    gone = before - set(AnimUtils.key_times(fc))
                    if gone:
                        on_delete(fc, sorted(gone))
                removed += result[0]
                worst_error = max(worst_error, result[1])
        if stats is not None:
            stats.update(
                {
                    "reduced": len(reduced),
                    "reduce_keys_removed": removed,
                    "reduce_max_error": worst_error,
                }
            )
        return reduced

    @staticmethod
    def get_redundant_flat_keys(
        objects,
        value_tolerance=1e-5,
        remove=False,
        time_range=None,
        selected_only=False,
    ):
        """Interior keys of a flat run — mirror of ``mtk.AnimUtils.get_redundant_flat_keys``.

        A key whose value matches both neighbours within *value_tolerance* is
        redundant: the boundary pair alone reproduces the hold. Returns
        ``[(fcurve, [times]), ...]``; ``remove=True`` deletes them.

        This is the pass that matters on hand-animated footage, where a hold is
        usually spelled with ``CONSTANT`` interpolation — and a stepped curve is
        exactly what :func:`simplify_curve` will not touch. Scope narrows the same
        two ways as that function and *objects* may be fcurves.
        """

        def candidate(key):
            if selected_only and not key.select_control_point:
                return False
            if time_range is not None:
                # Slop for float32 ``co.x`` (see :meth:`snap_keys`).
                return time_range[0] - 1e-3 <= key.co.x <= time_range[1] + 1e-3
            return True

        scoped = time_range is not None or selected_only
        out = []
        for fc in _AnimUtilsInternal._fcurves(objects):
            before = {k.co.x for k in fc.keyframe_points}
            n = (
                _AnimUtilsInternal._remove_flat_keys(
                    fc, value_tolerance, candidate if scoped else None
                )
                if remove
                else 0
            )
            if remove:
                if n:
                    fc.update()
                    gone = sorted(before - {k.co.x for k in fc.keyframe_points})
                    out.append((fc, gone))
                continue
            pts = list(fc.keyframe_points)
            gone = [
                pts[i].co.x
                for i in range(1, len(pts) - 1)
                if (not scoped or candidate(pts[i]))
                and abs(pts[i].co.y - pts[i - 1].co.y) <= value_tolerance
                and abs(pts[i + 1].co.y - pts[i].co.y) <= value_tolerance
            ]
            if gone:
                out.append((fc, gone))
        return out

    @staticmethod
    def simplify_curve(
        objects,
        value_tolerance=0.001,
        time_range=None,
        selected_only=False,
        on_delete=None,
    ):
        """Drop the keys that do not contribute to a curve's shape — mirror of
        ``mtk.AnimUtils.simplify_curve``. Returns the fcurves that lost a key.

        The optimizer's ``simplify`` rung (:func:`_simplify_fcurve`) offered on its own
        so a caller can aim it at a SELECTION instead of a scene. Scope narrows two ways
        and both keep the boundary keys: ``time_range`` reduces only inside the window,
        ``selected_only`` only the keys selected in the Dope Sheet / Graph Editor. They
        compose with *objects*, which may be fcurves — the attribute-level scope, since
        in Blender the fcurve is the channel.

        A key reached by a STEP is never removed: it is not on any line, so dropping it
        would extend the hold before it. Stepped HOLDS are the flat pass's job
        (:meth:`get_redundant_flat_keys`), where equal values make the cut exact.

        Maya reduces through ``filterCurve(keyReducer)``, which weighs a key against the
        whole curve's shape; this is the greedy collinear pass, which weighs it against
        its two neighbours. Same verb and same scope rules, different engine — the
        parity contract is behaviour, not the arithmetic.

        ``on_delete(fcurve, frames)`` hears the frames of the keys removed, per fcurve
        (what the shot system releases its claims on).
        """

        def in_scope(key):
            if selected_only and not key.select_control_point:
                return False
            if time_range is not None:
                # Slop for float32 ``co.x`` (see :meth:`snap_keys`).
                return time_range[0] - 1e-3 <= key.co.x <= time_range[1] + 1e-3
            return True

        def protected(fc):
            """The x of the first and last key of each in-scope RUN.

            Measured against Maya, which is the reference here: reducing the
            selected keys 3-7 and 13-17 of a 0-20 ramp leaves 3, 7, 13 and 17
            standing.  Without this the greedy pass eats a run whole -- its
            out-of-scope neighbours anchor the line -- and the motion OUTSIDE
            the selection changes, which is the one thing a scoped edit must
            not do.
            """
            ends, run = set(), []
            for key in sorted(fc.keyframe_points, key=lambda k: k.co.x):
                if in_scope(key):
                    run.append(key.co.x)
                    continue
                if run:
                    ends.update((run[0], run[-1]))
                    run = []
            if run:
                ends.update((run[0], run[-1]))
            return ends

        scoped = time_range is not None or selected_only
        simplified = []
        for fc in _AnimUtilsInternal._fcurves(objects):
            candidate = None
            if scoped:
                ends = protected(fc)

                def candidate(key, _ends=ends):
                    return in_scope(key) and key.co.x not in _ends

            before = {k.co.x for k in fc.keyframe_points}
            if _AnimUtilsInternal._simplify_fcurve(fc, value_tolerance, candidate):
                fc.update()
                simplified.append(fc)
                if on_delete is not None:
                    gone = before - {k.co.x for k in fc.keyframe_points}
                    if gone:
                        on_delete(fc, sorted(float(x) for x in gone))
        return simplified

    @staticmethod
    def optimize_keys(
        objects=None,
        value_tolerance=0.001,
        remove_static_curves=True,
        remove_flat_keys=True,
        simplify_keys=False,
        stats=None,
        max_error=None,
        on_delete=None,
    ):
        """Remove redundant animation data — mirror of ``mtk.AnimUtils.optimize_keys``.

        Pure ``keyframe_points`` math (headless-safe; the native Graph-Editor ``clean``/``decimate``
        ops can't run ``--background``):

        * ``remove_static_curves`` — a curve whose values are constant within ``value_tolerance`` is
          dropped after writing its held value back to the property (lossless).
        * ``remove_flat_keys`` — interior keys on a flat segment are removed (boundaries kept).
        * ``simplify_keys`` — additionally drop keys that lie on the line between their neighbours.

        A negative ``value_tolerance`` (``-1``) selects **extremes** mode: after the static pass every
        smooth curve is reduced to its extrema with refit handles (:meth:`reduce_to_extremes`); stepped
        curves still get the flat-key pass, ``simplify_keys`` is ignored and the static/flat
        tolerance falls back to the default. ``max_error`` bounds that refit's deviation in the
        curve's own units (None = 1% of each curve's amplitude, see :meth:`reduce_to_extremes`);
        inert outside extremes mode.

        ``objects`` defaults to every scene object. Pass a dict as ``stats`` to receive
        ``curves_before/after`` and ``keys_before/after`` counts (also returned), plus the
        :meth:`reduce_to_extremes` stats in extremes mode.

        ``on_delete(fcurve, frames)`` hears the frames of the keys removed, per fcurve (what
        the shot system releases its claims on) -- ``frames`` ``None`` for a static curve
        removed outright, BEFORE it goes, while the fcurve still resolves to its owner.
        """
        extremes = value_tolerance < 0
        if extremes:
            value_tolerance = 0.001  # the sentinel carries no magnitude
        if objects is None:
            import bpy

            pool = list(bpy.data.objects)
        else:
            pool = ptk.make_iterable(objects)
        s = {"curves_before": 0, "curves_after": 0, "keys_before": 0, "keys_after": 0}
        if extremes:
            s.update({"reduced": 0, "reduce_keys_removed": 0, "reduce_max_error": 0.0})
        for o, action, slot in _AnimUtilsInternal._owned_actions(pool):
            for fc in list(_AnimUtilsInternal._slot_fcurves(action, slot)):
                pts = fc.keyframe_points
                s["curves_before"] += 1
                s["keys_before"] += len(pts)
                before = set(AnimUtils.key_times(fc)) if on_delete is not None else None
                if remove_static_curves and len(pts):
                    vals = AnimUtils.key_arrays(fc)[1]
                    if max(vals) - min(
                        vals
                    ) <= value_tolerance and _AnimUtilsInternal._set_fcurve_value(
                        o, fc, vals[0]
                    ):
                        if on_delete is not None:
                            on_delete(fc, None)  # the whole curve goes
                        _AnimUtilsInternal._remove_fcurve(action, slot, fc)
                        continue
                reduced = (
                    _AnimUtilsInternal._reduce_fcurve_to_extremes(
                        fc, value_tolerance, max_error
                    )
                    if extremes
                    else None
                )
                if reduced is not None:
                    s["reduced"] += 1
                    s["reduce_keys_removed"] += reduced[0]
                    s["reduce_max_error"] = max(s["reduce_max_error"], reduced[1])
                else:
                    if remove_flat_keys:
                        _AnimUtilsInternal._remove_flat_keys(fc, value_tolerance)
                    if simplify_keys and not extremes:
                        _AnimUtilsInternal._simplify_fcurve(fc, value_tolerance)
                fc.update()
                s["curves_after"] += 1
                s["keys_after"] += len(fc.keyframe_points)
                if before is not None:
                    # Every pass here only removes keys: the times gone are its cut.
                    gone = before - set(AnimUtils.key_times(fc))
                    if gone:
                        on_delete(fc, sorted(gone))
        if stats is not None:
            stats.update(s)
        return s

    @staticmethod
    def repair_corrupted_curves(
        objects=None,
        *,
        delete_unfixable=True,
        fix_infinite=True,
        fix_invalid_times=True,
        time_threshold=100000.0,
        value_threshold=1000000.0,
        on_delete=None,
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

        ``on_delete(fcurve, frames)`` hears the frames of the keys removed, per fcurve (what
        the shot system releases its claims on) -- ``frames`` ``None`` for a curve deleted
        outright, BEFORE it goes.
        """
        import bpy
        import math

        def _bad_value(v):
            return fix_infinite and (
                math.isnan(v) or math.isinf(v) or abs(v) > value_threshold
            )

        def _bad_time(t):
            return fix_invalid_times and (
                math.isnan(t) or math.isinf(t) or abs(t) > time_threshold
            )

        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.data.objects)
        )
        result = {
            "corrupted_found": 0,
            "curves_repaired": 0,
            "curves_deleted": 0,
            "keys_fixed": 0,
            "details": [],
        }
        for _o, action, slot in _AnimUtilsInternal._owned_actions(pool):
            for fc in list(_AnimUtilsInternal._slot_fcurves(action, slot)):
                if not any(
                    _bad_value(k.co.y) or _bad_time(k.co.x) for k in fc.keyframe_points
                ):
                    continue
                result["corrupted_found"] += 1
                path = f"{fc.data_path}[{fc.array_index}]"
                gone = []
                # Remove corrupted keys one at a time: removing a keyframe_point invalidates the
                # other references, so re-fetch the next bad key each pass rather than batch-remove.
                while True:
                    bad = next(
                        (
                            k
                            for k in fc.keyframe_points
                            if _bad_value(k.co.y) or _bad_time(k.co.x)
                        ),
                        None,
                    )
                    if bad is None:
                        break
                    if math.isfinite(bad.co.x):
                        gone.append(float(bad.co.x))
                    fc.keyframe_points.remove(bad)
                    result["keys_fixed"] += 1
                if len(fc.keyframe_points) == 0 and delete_unfixable:
                    if on_delete is not None:
                        on_delete(fc, None)  # the whole curve goes
                    _AnimUtilsInternal._remove_fcurve(action, slot, fc)
                    result["curves_deleted"] += 1
                    result["details"].append(
                        f"{path}: deleted (no valid keys remained)"
                    )
                else:
                    fc.update()
                    result["curves_repaired"] += 1
                    if gone and on_delete is not None:
                        on_delete(fc, sorted(gone))
                    result["details"].append(
                        f"{path}: {'emptied' if not fc.keyframe_points else 'removed corrupt key(s)'}"
                    )
        return result

    @staticmethod
    def tie_keyframes(
        objects=None, untie=False, frame_range=None, absolute=False, on_delete=None
    ):
        """Add (tie) or remove (untie) bookend keys at the playback-range boundaries — mirror of
        ``mtk.AnimUtils.tie_keyframes``.

        Tying inserts a key (sampled from the curve) at the range start and end on every animated
        channel that lacks one, so each object has keys at the boundaries. Untying removes only those
        boundary keys (never the last remaining key). ``frame_range`` defaults to the scene's
        ``frame_start``/``frame_end``; ``absolute`` (when no explicit range is given) uses the actual
        keyed extent across the objects instead of the scene range. Returns the number of keys changed.
        ``on_delete(fcurve, frames)`` hears the frames of the bookends an untie removed, per
        fcurve (what the shot system releases its claims on).
        """
        import bpy

        scene = bpy.context.scene
        if objects is not None:
            pool = ptk.make_iterable(objects)
        else:
            pool = [
                o
                for o in bpy.data.objects
                if getattr(o, "animation_data", None) and o.animation_data.action
            ]
        if frame_range is not None:
            lo, hi = frame_range
        elif absolute:
            rng = _AnimUtilsInternal._key_range(AnimUtils.get_fcurves(pool))
            if rng is None:
                return 0
            lo, hi = rng
        else:
            lo, hi = scene.frame_start, scene.frame_end
        changed = 0
        for _o, action, slot in _AnimUtilsInternal._owned_actions(pool):
            for fc in _AnimUtilsInternal._slot_fcurves(action, slot):
                pts = fc.keyframe_points
                if not len(pts):
                    continue
                if untie:
                    gone = []
                    for bound in (hi, lo):
                        for i in reversed(
                            [i for i, k in enumerate(pts) if abs(k.co.x - bound) < 1e-6]
                        ):
                            if len(pts) > 1:
                                gone.append(float(pts[i].co.x))
                                pts.remove(pts[i], fast=True)
                                changed += 1
                    if gone and on_delete is not None:
                        on_delete(fc, sorted(gone))
                else:
                    for bound in (lo, hi):
                        # A key within Blender's 0.01 insert window already stands
                        # on the bound: inserting would rewrite its value instead.
                        if not any(abs(k.co.x - bound) < 0.01 for k in pts):
                            pts.insert(bound, fc.evaluate(bound))
                            changed += 1
                fc.update()
        return changed

    @staticmethod
    def bake_keys(
        objects=None,
        frame_range=None,
        step=1,
        only_selected=False,
        visual_keying=True,
        clear_constraints=False,
        clear_parents=False,
        use_current_action=True,
        bake_types=None,
    ):
        """Bake animation to plain keyframes — the Blender analogue of Maya's Smart Bake (wraps the
        native ``nla.bake``, which resolves constraints/drivers/parenting via ``visual_keying``).

        ``objects`` defaults to the current selection; armatures bake their pose, others bake object
        transforms (override with ``bake_types``, a subset of ``{'POSE', 'OBJECT'}``).
        ``frame_range`` defaults to the scene playback range. ``use_current_action`` (default
        ``True``, preserving prior behavior) bakes into the object's existing active action in place;
        ``False`` makes ``nla.bake`` create and assign a brand-new Action instead, leaving the
        pre-bake action untouched and still referenced elsewhere (e.g. ``SmartBake``'s non-destructive
        restore, which keeps the original action alive via ``use_fake_user`` and swaps it back in on
        restore). Returns the baked objects.
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        pool = [
            o
            for o in (
                ptk.make_iterable(objects)
                if objects is not None
                else CoreUtils.selected_objects()
            )
        ]
        if not pool:
            return []
        scene = bpy.context.scene
        start, end = (
            frame_range
            if frame_range is not None
            else (scene.frame_start, scene.frame_end)
        )
        if bake_types is None:
            bake_types = (
                {"POSE", "OBJECT"}
                if any(o.type == "ARMATURE" for o in pool)
                else {"OBJECT"}
            )
        # The window's view layer and context: windowless, select_set / the active
        # write address the scene's default layer, and nla.bake reads its targets
        # from screen context.
        with CoreUtils.window_context_override():
            view_layer = bpy.context.view_layer
            for o in list(CoreUtils.selected_objects()):
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
                use_current_action=use_current_action,
                bake_types=bake_types,
            )
            return pool

    @staticmethod
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
        from blendertk.core_utils._core_utils import CoreUtils

        scene = bpy.context.scene
        start, end = (
            frame_range
            if frame_range is not None
            else (scene.frame_start, scene.frame_end)
        )

        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else CoreUtils.selected_objects()
        )
        targets = []  # (obj, shape_keys) for meshes with driven/animated shape keys
        for o in pool:
            if getattr(o, "type", None) != "MESH":
                continue
            sk = getattr(o.data, "shape_keys", None)
            ad = getattr(sk, "animation_data", None) if sk else None
            # slot-aware fcurve check — Blender 5.x drops the flat action.fcurves (slotted actions).
            if ad and (
                ad.drivers
                or (ad.action and _AnimUtilsInternal._slot_fcurves(ad.action))
            ):
                targets.append((o, sk))
        if not targets:
            return []

        # 1. Sample each non-reference key's evaluated value per frame.
        frames = list(range(int(start), int(end) + 1, max(1, step)))
        samples = {}  # (obj, key_name) -> [(frame, value), ...]
        orig_frame = scene.frame_current
        for f in frames:
            scene.frame_set(f)
            depsgraph = CoreUtils._evaluated_depsgraph()  # the window layer's
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

    @staticmethod
    def get_animation_info(objects=None, by_time=False, ignore_holds=False):
        """Per-object animation summary — mirror of ``mtk`` get-animation-info.

        Returns a list of records ``{name, action, start, end, channels, keys, paths}`` for every
        animated object in scope (``objects`` defaults to the whole scene). Sorted by start frame
        when ``by_time`` else by name. Pair with :func:`format_animation_info_html` for the viewer.

        ``ignore_holds`` — report the ACTIVE range (trims static leading/trailing hold keys via
        :func:`_active_range`) instead of the raw first/last-key extent; an object whose curves never
        change value at all (a pure hold) is excluded from the report entirely — mirror of mayatk's
        ``SegmentKeys`` ignore-holds filter, relaxed to one range per object (see :func:`_active_range`).
        """
        import bpy

        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.data.objects)
        )
        records = []
        for o in pool:
            action, slot = _AnimUtilsInternal._animating(
                getattr(o, "animation_data", None)
            )
            if action is None:
                continue
            fcurves = _AnimUtilsInternal._slot_fcurves(action, slot)
            rng = (
                _AnimUtilsInternal._active_range(fcurves)
                if ignore_holds
                else _AnimUtilsInternal._key_range(fcurves)
            )
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
        records.sort(
            key=(lambda r: r["start"]) if by_time else (lambda r: r["name"].lower())
        )
        return records

    @staticmethod
    def format_animation_info_csv(records):
        """Render :func:`get_animation_info` records as CSV (paste into a spreadsheet) — mirror of
        Maya's CSV-output info flag. Empty string when there are no records."""
        if not records:
            return ""
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(
            buf, lineterminator="\n"
        )  # display-bound: avoid stray \r in the viewer
        writer.writerow(
            ["Object", "Action", "Start", "End", "Channels", "Keys", "Paths"]
        )
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

    @staticmethod
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

    @staticmethod
    def configure_render_output(
        scene, file_format="PNG", container=None, codec=None, quality=None
    ):
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

    # ---- key selection (mirror of mayatk) -----------------------------------------

    @staticmethod
    def clear_key_selection() -> None:
        """Deselect every key, whatever is or is not selected.

        Mirror of mayatk's ``AnimUtils.clear_key_selection`` (name + behavior):
        Maya's ``selectKey -clear`` empties the key selection on every anim
        curve in the scene, so every action's fcurves are cleared here -- each
        slot's, whatever ID it animates (object, data, shape keys) -- and the
        handles with the control points, or a stale handle selection outlives
        the clear for the next Graph Editor / Dope Sheet operation to act on.
        Driver fcurves belong to no action and are left alone.  The Maya twin
        skips its clear with nothing selected (the bare ``selectKey -clear``
        raises then); Blender has no such failure, so with nothing selected
        this only rewrites flags that are already off.  Not an undo step of
        its own.

        Returns:
            None.
        """
        import bpy

        for action in bpy.data.actions:
            # Every slot's channelbag, not ``_slot_fcurves(action)``: 4.4/4.5
            # still carry the legacy ``Action.fcurves``, which reaches the
            # first slot only.
            layers = getattr(action, "layers", None)
            if layers:
                curves = [
                    fc
                    for layer in layers
                    for strip in layer.strips
                    for cb in strip.channelbags
                    for fc in cb.fcurves
                ]
            else:  # a legacy action (pre-4.4, or not yet converted)
                curves = getattr(action, "fcurves", None) or ()
            for fc in curves:
                n = len(fc.keyframe_points)
                if not n:
                    continue
                off = [False] * n
                for prop in (
                    "select_control_point",
                    "select_left_handle",
                    "select_right_handle",
                ):
                    fc.keyframe_points.foreach_set(prop, off)

    @staticmethod
    def get_selected_key_times(objects=None):
        """Graph Editor / Dope Sheet key selection, per fcurve.

        Mirror of mayatk's ``AnimUtils.get_selected_key_times`` (name + behavior):
        a selection is per key, so the result is per curve.  Blender fcurves have
        no node name, so the key is ``(object_name, data_path, array_index)``.

        Parameters:
            objects: Restrict to these objects; ``None`` = every object holding an
                action.

        Returns:
            ``{(object_name, data_path, array_index): [frames]}`` — sorted,
            de-duplicated; curves with no selected key are absent.
        """
        import bpy

        from blendertk.anim_utils.shots._shots import BlenderShotStore

        if objects is None:
            objects = [
                o
                for o in bpy.data.objects
                if o.animation_data is not None and o.animation_data.action is not None
            ]
        out = {}
        for o in ptk.make_iterable(objects):
            for fc in BlenderShotStore.iter_action_fcurves(o):
                times = sorted(
                    {k.co.x for k in fc.keyframe_points if k.select_control_point}
                )
                if times:
                    out[(o.name, fc.data_path, fc.array_index)] = times
        return out

    @staticmethod
    def get_timeline_selection():
        """The timeline's preview range, or ``None`` when it is off.

        Blender has no drag-selected slider range; the user-marked sub-range
        of the timeline is the preview range (``P`` in the timeline), so that
        is what the mayatk twin's "timeline selection" maps to.
        """
        import bpy

        scene = bpy.context.scene
        if scene is None or not scene.use_preview_range:
            return None
        return float(scene.frame_preview_start), float(scene.frame_preview_end)

    # ---- transient preview layer (mirror of mayatk) --------------------------------

    @staticmethod
    def create_preview_layer(sources, gate=None, name="previewLayer"):
        """Play foreign actions on objects through throwaway NLA tracks.

        Mirror of mayatk's ``AnimUtils.create_preview_layer`` (name + behavior; a
        Maya override layer becomes NLA strips here).  The objects' own animation
        is untouched: :meth:`remove_preview_layer` removes the tracks and hands the
        active action back.  Verified live (Blender 5.1):

        * **in context** (*gate* given): the object's active action is pushed down
          onto a HOLD strip and cleared, and the source action sits on a REPLACE
          strip above it with no extrapolation — so the base plays up to the
          strip, the preview takes over for its range, the base resumes.
        * **isolated** (no *gate*): the source strip's track is soloed; its end
          poses hold outside its keys.  (An un-soloed strip loses to the active
          action, which Blender evaluates on top of the NLA stack.)

        Parameters:
            sources: ``{object_name: (action, slot)}`` — the action each object
                previews, and the slot within it to bind.
            gate: ``(start, end)`` for the in-context view; ``None`` = isolated.
            name: Track name (the pushed-down base track is ``<name>_base``).

        Returns:
            A JSON-safe handle for :meth:`remove_preview_layer`.

        Raises:
            ValueError: When no source object is in the scene.
        """
        import bpy

        handle = {"name": name, "objects": {}}
        for obj_name, (action, slot) in sources.items():
            obj = bpy.data.objects.get(obj_name)
            if obj is None or action is None:
                continue
            ad = obj.animation_data or obj.animation_data_create()
            entry = {"tracks": [], "action": None, "slot": None}
            live = ad.action
            if gate is not None and live is not None:
                live_slot = ad.action_slot
                base = ad.nla_tracks.new()
                base.name = f"{name}_base"
                bstrip = base.strips.new(
                    f"{name}_base", int(round(live.frame_range[0])), live
                )
                AnimUtils._bind_strip_slot(bstrip, live_slot)
                bstrip.extrapolation = "HOLD"
                entry["action"] = live.name
                entry["slot"] = live_slot.identifier if live_slot is not None else None
                ad.action = None
                entry["tracks"].append(base.name)
            top = ad.nla_tracks.new()
            top.name = name
            tstrip = top.strips.new(name, int(round(action.frame_range[0])), action)
            AnimUtils._bind_strip_slot(tstrip, slot)
            tstrip.blend_type = "REPLACE"
            if gate is not None:
                tstrip.extrapolation = "NOTHING"
            else:
                tstrip.extrapolation = "HOLD"
                top.is_solo = True
            entry["tracks"].append(top.name)
            handle["objects"][obj_name] = entry
        if not handle["objects"]:
            raise ValueError("create_preview_layer: no source object is in the scene")
        return handle

    @staticmethod
    def _bind_strip_slot(strip, slot):
        """Point an NLA strip at *slot* (4.4+ slotted actions; no-op on legacy builds)."""
        if slot is None or not hasattr(strip, "action_slot"):
            return
        try:
            strip.action_slot = slot
        except Exception:  # a slot from another action, or a legacy strip
            pass

    @staticmethod
    def remove_preview_layer(handle) -> bool:
        """Tear down :meth:`create_preview_layer`'s tracks and restore the active action.

        Returns:
            ``True`` if any preview track existed.
        """
        import bpy

        if not handle:
            return False
        found = False
        for obj_name, entry in (handle.get("objects") or {}).items():
            obj = bpy.data.objects.get(obj_name)
            ad = obj.animation_data if obj is not None else None
            if ad is None:
                continue
            for track_name in entry.get("tracks", ()):
                track = ad.nla_tracks.get(track_name)
                if track is not None:
                    ad.nla_tracks.remove(track)
                    found = True
            action = bpy.data.actions.get(entry.get("action") or "")
            if action is not None:
                ad.action = action
                ident = entry.get("slot")
                for s in action.slots:
                    if s.identifier == ident:
                        ad.action_slot = s
                        break
        return found
