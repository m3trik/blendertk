# !/usr/bin/python
# coding=utf-8
"""Animation-segment collection over Blender fcurves (mirror of ``mtk.SegmentKeys``).

Stage 1 of mayatk's segment pipeline — *collection*: split each object's keyed
animation into **motion** runs separated by static holds.  The sequencer builds
its object tracks and per-attribute sub-rows from these, so an object that
moves, holds for 40 frames, then moves again shows two clips (mayatk parity)
instead of one span across the hold.

Ported verbatim at the algorithm level (interval classification → merge → range
clip → optional hold absorption / hold-only synthesis → segment dicts); only the
curve reads differ:

* a curve is a ``bpy.types.FCurve`` (slotted-action walk via
  :meth:`BlenderShotStore.iter_action_fcurves`), not an animCurve node name;
* Maya's ``step``/``stepnext`` out-tangent is a keyframe with
  ``interpolation == "CONSTANT"``;
* ``channel_box_attrs`` / ``ignore`` match channel **labels** (``translateX``,
  ``rotate``…) via :meth:`SegmentCollector.attr_label`, or raw ``data_path``
  substrings.

Segment shape (identical to mayatk so the sequencer's presentation code is
shared)::

    {"obj": str, "curves": [FCurve, ...], "keyframes": [float, ...],
     "start": float, "end": float, "duration": float,
     "segment_range": (start, end)}

The grouping / stagger / speed stages (mayatk Stage 2+) are NOT ported — the
sequencer only consumes Stage 1 (``stagger_keys`` stays thin; ledgered).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils._anim_utils import AnimUtils

_log = logging.getLogger(__name__)

__all__ = ["SegmentKeys"]

_EPS = 1e-3
_BOUNDARY_EPS = 1e-4


class _SegmentKeysInternal(object):
    """Internal helpers for SegmentKeys."""

    @staticmethod
    def _label(fc) -> str:
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        return SegmentCollector.attr_label(fc)

    @staticmethod
    def _matches(fc, names: set) -> bool:
        """True when *fc*'s label, label base, or data_path matches any of *names*."""
        label = _SegmentKeysInternal._label(fc).lower()
        path = fc.data_path.lower()
        for n in names:
            # Maya's ``visibility`` attr ↔ Blender's ``hide_viewport`` / ``hide_render``.
            if n == "visibility" and "hide" in path:
                return True
            if n == label or n == path:
                return True
            # "translate" matches translateX/Y/Z; "location" matches the path.
            if label.startswith(n) and len(label) == len(n) + 1:
                return True
            if n in path:
                return True
        return False

    @staticmethod
    def _filter_curves_by_ignore(
        curves: List[Any], ignore: Optional[Union[str, List[str]]]
    ) -> List[Any]:
        """Drop curves whose channel matches any *ignore* name/pattern."""
        if not ignore or not curves:
            return list(curves)
        names = (
            {ignore.lower()} if isinstance(ignore, str) else {i.lower() for i in ignore}
        )
        return [fc for fc in curves if not _SegmentKeysInternal._matches(fc, names)]

    @staticmethod
    def _filter_curves_by_channel_box(
        curves: List[Any], channel_box_attrs: Optional[List[str]]
    ) -> List[Any]:
        """Keep only curves whose channel matches one of *channel_box_attrs*."""
        if not channel_box_attrs or not curves:
            return list(curves)
        names = {a.lower() for a in channel_box_attrs}
        return [fc for fc in curves if _SegmentKeysInternal._matches(fc, names)]

    @staticmethod
    def _is_visibility_fcurve(fc) -> bool:
        return "hide" in fc.data_path

    @staticmethod
    def _get_active_animation_segments(
        curves: List[Any],
        tolerance: float = 1e-4,
        ignore_visibility_holds: bool = False,
        motion_only: bool = False,
        motion_rate: float = 1e-3,
        time_range: Optional[Tuple[Optional[float], Optional[float]]] = None,
    ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[float]]:
        """Identify spans of active animation across *curves*, excluding static holds.

        Verbatim port of mayatk's classifier: every consecutive key pair on
        every curve is an interval; an interval is *active* when its value
        changes (``motion_only``: rate-normalised by duration; stepped
        intervals are point events unless both keys change value in range);
        visibility curves are always active unless *ignore_visibility_holds*.
        Active intervals are merged across curves.

        Returns ``(merged_spans, [], all_keyframe_times)``.
        """
        if not curves:
            return ([], [], [])

        all_intervals: List[Tuple[float, float]] = []
        all_keyframe_times: set = set()

        ipo_constant = None
        for fc in curves:
            times, values = AnimUtils.key_arrays(fc)
            if not times:
                continue
            all_keyframe_times.update(times)

            if len(times) == 1:
                all_intervals.append((times[0], times[0]))
                continue

            is_visibility = (
                not ignore_visibility_holds
                and _SegmentKeysInternal._is_visibility_fcurve(fc)
            )

            interps = None
            if motion_only:
                if ipo_constant is None:
                    ipo_constant = AnimUtils.interpolation_value("CONSTANT")
                interps = AnimUtils.key_interpolations(fc)
            for i in range(len(times) - 1):
                t1, t2 = times[i], times[i + 1]
                v1, v2 = values[i], values[i + 1]
                is_stepped = interps is not None and interps[i] == ipo_constant

                if motion_only:
                    if is_stepped:
                        if abs(v2 - v1) <= 1e-6:
                            continue
                        if time_range is not None:
                            r_lo = time_range[0] if time_range[0] is not None else -1e18
                            r_hi = time_range[1] if time_range[1] is not None else 1e18
                            t1_in = t1 >= r_lo
                            t2_in = t2 <= r_hi
                            if not t1_in and not t2_in:
                                continue
                            if not t1_in or not t2_in:
                                pt = t2 if not t1_in else t1
                                all_intervals.append((pt, pt))
                                continue
                        is_value_change = True
                    else:
                        dt = max(t2 - t1, 1.0)
                        is_value_change = abs(v2 - v1) / dt > motion_rate
                else:
                    is_value_change = abs(v1 - v2) > tolerance

                if is_visibility or is_value_change:
                    all_intervals.append((t1, t2))

        if not all_intervals:
            return ([], [], sorted(all_keyframe_times))

        all_intervals.sort(key=lambda x: x[0])
        merged: List[Tuple[float, float]] = []
        cur_s, cur_e = all_intervals[0]
        for nxt_s, nxt_e in all_intervals[1:]:
            if nxt_s <= cur_e:
                cur_e = max(cur_e, nxt_e)
            else:
                merged.append((cur_s, cur_e))
                cur_s, cur_e = nxt_s, nxt_e
        merged.append((cur_s, cur_e))
        return (merged, [], sorted(all_keyframe_times))


class SegmentKeys(_SegmentKeysInternal):
    """Collect per-object animation segments from Blender fcurves.

    Blender mirror of ``mtk.SegmentKeys`` (Stage 1 — collection only).
    """

    @classmethod
    def collect_segments(
        cls,
        objects: Union[str, List[str]],
        ignore: Optional[Union[str, List[str]]] = None,
        split_static: bool = False,
        selected_keys_only: bool = False,
        channel_box_attrs: Optional[List[str]] = None,
        static_tolerance: float = 1e-4,
        time_range: Optional[Tuple[Optional[float], Optional[float]]] = None,
        ignore_visibility_holds: bool = False,
        ignore_holds: bool = False,
        exclude_next_start: bool = True,
        motion_only: bool = False,
        motion_rate: float = 1e-3,
        transform_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect animation segments from *objects* (names in ``bpy.data.objects``).

        Parameters:
            objects: Object name(s).
            ignore: Channel label(s)/path substring(s) to exclude.
            split_static: Split segments at static (hold) gaps.
            selected_keys_only: Only consider selected keyframe points.
            channel_box_attrs: Restrict to these channel labels (``translateX``…).
            static_tolerance: Value tolerance for static detection.
            time_range: ``(start, end)`` to limit keyframe collection.
            ignore_visibility_holds: Treat visibility curves like any other.
            ignore_holds: Drop trailing holds instead of absorbing them.
            exclude_next_start: A segment's absorbed hold stops just before
                the next segment's start key.
            motion_only: Rate-normalised motion classification (see
                :meth:`_get_active_animation_segments`).
            motion_rate: Per-frame rate threshold for *motion_only*.
            transform_only: Only transform channels (location/rotation/scale)
                — the sequencer's scope; ``False`` takes every fcurve.

        Returns:
            List of segment dicts (see module docstring).
        """
        try:
            import bpy
        except ImportError:
            return []

        segments: List[Dict[str, Any]] = []
        if isinstance(objects, str):
            objects = [objects]
        if not objects:
            return segments

        range_start = time_range[0] if time_range else None
        range_end = time_range[1] if time_range else None

        for name in objects:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            all_curves = [
                fc
                for fc in BlenderShotStore.iter_action_fcurves(obj)
                if not transform_only
                or BlenderShotStore._is_transform_path(fc.data_path)
            ]
            curves_to_use = cls._filter_curves_by_ignore(all_curves, ignore)
            curves_to_use = cls._filter_curves_by_channel_box(
                curves_to_use, channel_box_attrs
            )
            if not curves_to_use:
                continue

            if selected_keys_only:
                sel_times: set = set()
                for fc in curves_to_use:
                    n = len(fc.keyframe_points)
                    if not n:
                        continue
                    sel = [False] * n
                    fc.keyframe_points.foreach_get("select_control_point", sel)
                    if any(sel):
                        kt = AnimUtils.key_times(fc)
                        sel_times.update(kt[i] for i in range(n) if sel[i])
                keyframes: Optional[List[float]] = sorted(sel_times)
                if not keyframes:
                    continue
            else:
                keyframes = None

            if split_static:
                active_segments, _, all_kf = cls._get_active_animation_segments(
                    curves_to_use,
                    tolerance=static_tolerance,
                    ignore_visibility_holds=ignore_visibility_holds,
                    motion_only=motion_only,
                    motion_rate=motion_rate,
                    time_range=time_range,
                )
                if keyframes is None:
                    keyframes = all_kf
            elif keyframes is None:
                keyframes = sorted(
                    {t for fc in curves_to_use for t in AnimUtils.key_times(fc)}
                )

            if not keyframes:
                continue
            keyframes = sorted(set(keyframes))

            if range_start is not None or range_end is not None:
                keyframes = [
                    k
                    for k in keyframes
                    if (range_start is None or k >= range_start - _EPS)
                    and (range_end is None or k <= range_end + _EPS)
                ]
                if not keyframes:
                    continue

            if split_static:
                if range_start is not None or range_end is not None:
                    filtered = []
                    for seg_start, seg_end in active_segments:
                        if range_start is not None:
                            seg_start = max(seg_start, range_start)
                        if range_end is not None:
                            seg_end = min(seg_end, range_end)
                        if seg_start > seg_end + _BOUNDARY_EPS:
                            continue
                        filtered.append((seg_start, max(seg_start, seg_end)))
                    active_segments = filtered
            else:
                active_segments = [(keyframes[0], keyframes[-1])]

            # Hold-only object (motion_only, no active spans): synthesise one
            # hold segment so the object stays visible unless holds are ignored.
            if (
                split_static
                and not active_segments
                and not ignore_holds
                and keyframes
                and motion_only
            ):
                active_segments = [(keyframes[0], keyframes[-1])]

            if split_static and active_segments and not ignore_holds:
                active_segments = sorted(active_segments, key=lambda x: (x[0], x[1]))
                expanded = []
                for i, (seg_start, seg_end) in enumerate(active_segments):
                    next_start = (
                        active_segments[i + 1][0]
                        if i + 1 < len(active_segments)
                        else None
                    )
                    if next_start is not None:
                        upper = float(next_start) - (
                            _EPS if exclude_next_start else 0.0
                        )
                    else:
                        upper = float(keyframes[-1]) + _EPS
                    seg_end_expanded = float(seg_end)
                    for k in reversed(keyframes):
                        if k <= upper and k >= float(seg_end) - _EPS:
                            seg_end_expanded = float(k)
                            break
                    seg_start_expanded = float(seg_start)
                    if i == 0 and keyframes:
                        first_key = float(keyframes[0])
                        if first_key < seg_start - _EPS:
                            seg_start_expanded = first_key
                    expanded.append((seg_start_expanded, seg_end_expanded))
                active_segments = expanded

            for seg_start, seg_end in sorted(
                active_segments, key=lambda x: (x[0], x[1])
            ):
                segment_keys = [k for k in keyframes if seg_start <= k <= seg_end]
                segments.append(
                    {
                        "obj": name,
                        "curves": list(curves_to_use),
                        "keyframes": segment_keys,
                        "start": seg_start,
                        "end": seg_end,
                        "duration": seg_end - seg_start,
                        "segment_range": (seg_start, seg_end),
                    }
                )
        return segments

    @staticmethod
    def shift_curves(
        curves: List[Any],
        offset: float,
        time_range: Optional[Tuple[float, float]] = None,
        remove_flat_at_dest: bool = False,
    ) -> None:
        """Shift the keys of *curves* (optionally within *time_range*) by *offset* frames.

        Blender needs no relative-move workaround — each point and its handles
        translate directly.  With *remove_flat_at_dest* the destination window is
        first cleared of **flat** keys (value equal to the evaluated curve one
        frame either side) that are not themselves being moved, so an incoming
        run can't collide with a stale hold key.
        """
        if not curves or abs(offset) < 1e-6:
            return
        for fc in curves:
            if time_range is not None:
                lo, hi = float(time_range[0]) - _EPS, float(time_range[1]) + _EPS
            else:
                lo = hi = None
            kt, kv = AnimUtils.key_arrays(fc)
            if not kt:
                continue
            i0, i1 = AnimUtils.window_indices(kt, lo, hi)
            if i1 <= i0:
                continue

            if remove_flat_at_dest and time_range is not None:
                d0, d1 = AnimUtils.window_indices(kt, lo + offset, hi + offset)
                victims = [
                    i
                    for i in range(d0, d1)
                    if not (i0 <= i < i1)
                    and abs(fc.evaluate(kt[i] - 1) - kv[i]) <= 1e-4
                    and abs(fc.evaluate(kt[i] + 1) - kv[i]) <= 1e-4
                ]
                for i in reversed(victims):
                    fc.keyframe_points.remove(fc.keyframe_points[i])

            AnimUtils.shift_keys_in_window(fc, lo, hi, offset)
