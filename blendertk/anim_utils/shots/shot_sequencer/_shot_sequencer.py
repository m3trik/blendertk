# !/usr/bin/python
# coding=utf-8
"""Blender shot sequencer engine — timeline moves over the shared shots planner.

Mirror of mayatk's ``ShotSequencer`` public apply-surface (``move_shot`` /
``ripple_downstream`` / ``ripple_upstream`` / ``respace`` / ``apply_gap`` /
``move_shot_to_position`` / ``trim_shot_to_content``).  Every collision-safe plan
is built by the pure pythontk planner (``shot_plan``) and committed by
``shot_apply.apply``; this class only injects a Blender ``move_keys`` (the fcurve
key-teleport that replaces mayatk's ``_batch_move_keys``) and supplies the two
scene measures the planner cannot: the pivot-shot key move and the trim
content-range query.

Divergence from mayatk (by design, ledgered — not gaps to fill silently):
    * **Reorder via a pure plan.** ``move_shot_to_position`` delegates to the new
      pure ``plan_reorder`` + ``apply`` (whose Phase-0/1/2 park/land handles the
      collision cycle) instead of mayatk's hand-rolled park/land loop.
    * **No audio shifting.** ``apply`` is called with ``shift_audio=None`` this
      phase; VSE sound-strip time-shifting is a documented follow-up.  Shot key
      moves are exact; only co-timed audio would need the extra pass.
    * **Gap-hold stepping is a no-op** (:meth:`_enforce_gap_holds`).  mayatk steps
      the last key before each inter-shot gap to hold its pose; the Blender
      equivalent (set that keyframe ``interpolation='CONSTANT'``) is deferred — its
      absence changes interpolation *through* a gap only, never a shot bound or a
      key position.
    * **``over`` flag is advisory.** mayatk needs Maya's ``keyframe option='over'``
      to let a key slide past an occupied frame; writing ``keyframe_point.co[0]``
      directly has no neighbour clamp, so ``over`` never changes behaviour here
      (the planner's topological order already guarantees ordered-phase moves
      never cross, and park/land teleports clear all real content).
"""
import logging
from typing import List, Optional, Tuple

from pythontk.core_utils.engines.shots.shot_plan import (
    plan_respace,
    plan_ripple_downstream,
    plan_ripple_upstream,
    plan_reorder,
)
from pythontk.core_utils.engines.shots.shot_apply import apply

from blendertk.anim_utils.shots._shots import iter_action_fcurves, _is_transform_path

_log = logging.getLogger(__name__)

_EPS = 1.0e-6
_SLOP = 1.0e-3  # matches mayatk's _ENVELOPE_SLOP so the window semantics agree


class ShotSequencer:
    """Timeline-move engine for a :class:`~blendertk.BlenderShotStore`.

    Parameters:
        store: The shot store to operate on.  All moves mutate its shots and
            the scene's keyframes together.
    """

    def __init__(self, store):
        self.store = store

    # ---- delegated store accessors (panel-facing surface) ----------------

    @property
    def shots(self):
        return self.store.shots

    @shots.setter
    def shots(self, value):
        self.store.shots = value

    @property
    def hidden_objects(self) -> set:
        return self.store.hidden_objects

    @hidden_objects.setter
    def hidden_objects(self, value: set):
        self.store.hidden_objects = value

    @property
    def markers(self):
        return self.store.markers

    @markers.setter
    def markers(self, value):
        self.store.markers = value

    def is_object_hidden(self, obj_name: str) -> bool:
        return self.store.is_object_hidden(obj_name)

    def set_object_hidden(self, obj_name: str, hidden: bool = True) -> None:
        self.store.set_object_hidden(obj_name, hidden)

    def sorted_shots(self):
        return self.store.sorted_shots()

    def shot_by_id(self, shot_id: int):
        return self.store.shot_by_id(shot_id)

    def shot_by_name(self, name: str):
        return self.store.shot_by_name(name)

    def define_shot(self, name, start, end, objects=None, metadata=None,
                    locked=False, description=""):
        """Define a shot; auto-discover keyed transforms when *objects* is None."""
        if objects is None:
            objects = self._find_keyed_transforms(start, end)
        return self.store.define_shot(
            name=name, start=start, end=end, objects=objects,
            metadata=metadata, locked=locked, description=description,
        )

    # ---- scene helpers ---------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(start, end, value_tolerance: float = 1e-4) -> List[str]:
        """Names of transforms with *non-flat* animation in ``[start, end]``.

        Blender mirror of mayatk's helper: walk each object's transform fcurves
        and keep those whose keyed values vary by more than *value_tolerance*
        within the range (a wholly-constant curve is a hold, not content).  Only
        standard transform/visibility channels count — custom trigger attrs are
        ignored so marker objects don't register as scene content.
        """
        try:
            import bpy
        except ImportError:
            return []
        scene = bpy.context.scene
        if scene is None:
            return []
        result: List[str] = []
        for obj in scene.objects:
            for fc in iter_action_fcurves(obj):
                if not _is_transform_path(fc.data_path):
                    continue
                vals = [
                    kp.co[1]
                    for kp in fc.keyframe_points
                    if start - _EPS <= kp.co[0] <= end + _EPS
                ]
                if vals and (max(vals) - min(vals)) > value_tolerance:
                    result.append(obj.name)
                    break
        return sorted(set(result))

    def reconcile_all_shots(self) -> bool:
        """No-op in Blender (documented divergence).

        mayatk's reconcile re-resolves stale Maya **DAG paths** (``|``-separated,
        which go stale on reparent/rename); Blender object names are flat and
        unique within ``bpy.data.objects``, so a shot's stored names never need
        path-reconciliation.  Object *deletion* is surfaced by ``assess`` /
        ``is_object_hidden`` instead of silently rewriting membership here.
        Returns ``False`` (nothing reconciled) to match the "any change?" contract.
        """
        return False

    # ---- Blender key-mover (injected into shot_apply.apply) --------------

    def _move_keys(
        self,
        objects,
        env_lo: float,
        env_hi: float,
        delta: float,
        over: bool = False,
        half_open: bool = True,
    ) -> None:
        """Shift the keys of *objects* inside a time window by *delta* frames.

        Implements the ``move_keys`` contract ``apply`` calls: select each object's
        fcurve keys whose time falls in the window and translate the whole key —
        control point and both bezier handles — by *delta*, then ``fcurve.update()``
        to re-sort and recompute.  ``half_open=True`` (the ``apply`` contract)
        selects ``[env_lo, env_hi)`` (a key exactly on ``env_hi`` — the next shot's
        start — stays with that shot, matching mayatk's ``-slop`` nudge); the pivot
        mover passes ``half_open=False`` for an inclusive ``[env_lo, env_hi]``.
        """
        if not objects or abs(delta) < _EPS:
            return
        try:
            import bpy
        except ImportError:
            return
        scene = bpy.context.scene
        if scene is None:
            return
        lo = env_lo - _SLOP
        hi = (env_hi - _SLOP) if half_open else (env_hi + _SLOP)
        for name in objects:
            obj = scene.objects.get(name)
            if obj is None:
                continue
            for fc in iter_action_fcurves(obj):
                moved = False
                for kp in fc.keyframe_points:
                    t = kp.co[0]
                    inside = (lo <= t < hi) if half_open else (lo <= t <= hi)
                    if inside:
                        kp.co[0] += delta
                        kp.handle_left[0] += delta
                        kp.handle_right[0] += delta
                        moved = True
                if moved:
                    fc.update()

    # ---- pure planner delegations ---------------------------------------

    def ripple_downstream(self, shot_id: int, after_frame: float, delta: float) -> None:
        """Shift every shot starting at/after *after_frame* by *delta* (pivot excluded)."""
        plan = plan_ripple_downstream(self.store, shot_id, after_frame, delta)
        apply(plan, self.store, move_keys=self._move_keys)

    def ripple_upstream(self, shot_id: int, before_frame: float, delta: float) -> None:
        """Shift every shot ending at/before *before_frame* by *delta* (pivot excluded)."""
        plan = plan_ripple_upstream(self.store, shot_id, before_frame, delta)
        apply(plan, self.store, move_keys=self._move_keys)

    def respace(self, gap: float = 0, start_frame: float = 1) -> None:
        """Lay all shots out sequentially from *start_frame* with *gap* spacing."""
        plan = plan_respace(self.store, gap, start_frame)
        apply(plan, self.store, move_keys=self._move_keys)
        self._enforce_gap_holds()

    # ---- pivot move (hybrid: neighbour ripple is pure, pivot is scene) ---

    def _move_shot_content(self, shot, new_start: float) -> None:
        """Move the pivot shot's own keys to *new_start* and update its bounds.

        Inclusive window ``[old_start, old_end]`` (``half_open=False``); safe
        because :meth:`slide_shot` vacates the destination side first, so no
        neighbour key sits in the pivot's window at move time.
        """
        new_start = self.store.snap(new_start)
        old_start, old_end = shot.start, shot.end
        delta = new_start - old_start
        if abs(delta) < _EPS:
            return
        self._move_keys(
            shot.objects, old_start, old_end, delta, over=True, half_open=False
        )
        duration = old_end - old_start
        shot.start = new_start
        shot.end = self.store.snap(new_start + duration)

    def slide_shot(
        self,
        shot_id: int,
        new_start: float,
        direction: str = "downstream",
        _enforce: bool = True,
    ) -> None:
        """Move a shot to *new_start*, rippling neighbours to preserve spacing.

        Order-sensitive: the destination side is vacated (neighbour ripple) before
        the pivot's own keys are moved when the pivot advances, and after when it
        retreats — so the two key sets never transiently occupy the same frames.
        """
        shot = self.store.shot_by_id(shot_id)
        if shot is None:
            return
        old_start, old_end = shot.start, shot.end
        delta = self.store.snap(new_start) - old_start
        if abs(delta) < _EPS:
            return
        if direction == "downstream":
            if delta > 0:
                self.ripple_downstream(shot_id, old_end, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_downstream(shot_id, old_end, delta)
        else:  # upstream
            if delta < 0:
                self.ripple_upstream(shot_id, old_start, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_upstream(shot_id, old_start, delta)
        if _enforce:
            self._enforce_gap_holds()
        self.store.mark_dirty()

    def move_shot(self, shot_id: int, new_start: float) -> None:
        """Move a shot's start to *new_start*, rippling downstream shots."""
        self.slide_shot(shot_id, new_start, direction="downstream")

    # ---- per-object key motion (drag a clip within/around a shot) --------

    def move_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float) -> None:
        """Offset *obj*'s keys in ``[old_start, old_end]`` so the run begins at *new_start*.

        Blender needs none of mayatk's cut-and-recreate dance (Maya's
        ``keyframe(timeChange=)`` refuses to slide a key past an occupied frame);
        writing ``keyframe_point.co[0] += delta`` directly has no neighbour clamp,
        so this is a single inclusive-window :meth:`_move_keys` call.
        """
        self._move_keys([obj], old_start, old_end, new_start - old_start, half_open=False)

    def move_stepped_keys(self, obj: str, old_time: float, new_time: float,
                          attr_name: Optional[str] = None, eps: float = 1e-3) -> None:
        """Move the key(s) at *old_time* to *new_time*.

        A keyframe's interpolation travels with its point when relocated, so the
        "stepped" character is preserved automatically — no tangent capture/replay
        (mayatk's Maya workaround) is needed.  *attr_name* scopes the move to one
        channel by a **``data_path`` substring** (e.g. ``"location"``) — NOT a
        display label like ``"translateX"`` (which is never a substring of
        ``location`` and would match nothing; label-scoped edits go through
        ``clip_motion.curves_for_attr``).  Omit it to move every fcurve of *obj*
        with a key at *old_time*.
        """
        delta = new_time - old_time
        if abs(delta) < _EPS:
            return
        try:
            import bpy
        except ImportError:
            return
        scene = bpy.context.scene
        if scene is None:
            return
        o = scene.objects.get(obj)
        if o is None:
            return
        lo, hi = old_time - eps, old_time + eps
        for fc in iter_action_fcurves(o):
            if attr_name and attr_name not in fc.data_path:
                continue
            moved = False
            for kp in fc.keyframe_points:
                if lo <= kp.co[0] <= hi:
                    kp.co[0] += delta
                    kp.handle_left[0] += delta
                    kp.handle_right[0] += delta
                    moved = True
            if moved:
                fc.update()

    def _scale_keys(self, objects, old_start, old_end, new_start, new_end) -> None:
        """Linearly remap each object's keys in ``[old_start, old_end]`` onto ``[new_start, new_end]``.

        The Blender analogue of Maya's ``scaleKey``: a key at time *t* moves to
        ``new_start + (t - old_start) * scale`` (``scale = new_span / old_span``),
        with bezier handles remapped the same way so tangents scale with the clip.
        """
        span = old_end - old_start
        if abs(span) < _EPS:
            return
        scale = (new_end - new_start) / span
        if abs(scale - 1.0) < _EPS and abs(new_start - old_start) < _EPS:
            return
        try:
            import bpy
        except ImportError:
            return
        scene = bpy.context.scene
        if scene is None:
            return

        def _remap(x):
            return new_start + (x - old_start) * scale

        lo, hi = old_start - _SLOP, old_end + _SLOP
        for name in objects:
            o = scene.objects.get(name)
            if o is None:
                continue
            for fc in iter_action_fcurves(o):
                moved = False
                for kp in fc.keyframe_points:
                    if lo <= kp.co[0] <= hi:
                        kp.handle_left[0] = _remap(kp.handle_left[0])
                        kp.handle_right[0] = _remap(kp.handle_right[0])
                        kp.co[0] = _remap(kp.co[0])
                        moved = True
                if moved:
                    fc.update()

    def scale_object_keys(self, obj: str, old_start: float, old_end: float,
                          new_start: float, new_end: float) -> None:
        """Scale one object's keys from ``[old_start, old_end]`` into ``[new_start, new_end]``."""
        self._scale_keys([obj], old_start, old_end, new_start, new_end)

    def move_object_in_shot(self, shot_id: int, obj: str, old_start: float,
                            old_end: float, new_start: float) -> None:
        """Move one object's keys within a shot, growing the shot + rippling when it overruns."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        dur = old_end - old_start
        new_end = self.store.snap(new_start + dur)

        self.move_object_keys(obj, old_start, old_end, new_start)

        prior_start, prior_end = shot.start, shot.end
        start_expanded = end_expanded = False
        if new_start < shot.start:
            shot.start = new_start
            start_expanded = True
        if new_end > shot.end:
            shot.end = new_end
            end_expanded = True
        if start_expanded:
            d = shot.start - prior_start
            if abs(d) > _EPS:
                self.ripple_upstream(shot_id, prior_start, d)
        if end_expanded:
            d = shot.end - prior_end
            if abs(d) > _EPS:
                self.ripple_downstream(shot_id, prior_end, d)
        if start_expanded or end_expanded:
            self.store.mark_dirty()
        # No _enforce_gap_holds here (matches mayatk): a per-object move must
        # not restep tangents on objects the user didn't touch.

    # ---- resize (scale keys + ripple) ------------------------------------

    def resize_object(self, shot_id: int, obj: str, old_start: float, old_end: float,
                      new_start: float, new_end: float) -> None:
        """Scale one object's keys and ripple downstream shots by the tail delta."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        prior_end = shot.end
        shot.start = min(shot.start, new_start)
        shot.end = max(shot.end, new_end)
        delta = shot.end - prior_end
        if abs(delta) > _EPS:
            self.ripple_downstream(shot_id, prior_end, delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    def set_shot_duration(self, shot_id: int, new_duration: float) -> None:
        """Change a shot's duration (start fixed), scaling its keys + rippling downstream."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        delta = new_duration - (shot.end - shot.start)
        if abs(delta) < _EPS:
            return
        old_end = shot.end
        new_end = self.store.snap(shot.start + new_duration)
        for obj in shot.objects:
            self.scale_object_keys(obj, shot.start, old_end, shot.start, new_end)
        shot.end = new_end
        self.ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    def resize_shot(self, shot_id: int, new_start: float, new_end: float,
                    _enforce: bool = True) -> None:
        """Resize a shot to ``[new_start, new_end]``, scaling all keys and rippling both edges."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < _EPS and abs(new_end - old_end) < _EPS:
            return
        for obj in shot.objects:
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        shot.start = new_start
        shot.end = new_end
        tail_delta = new_end - old_end
        if abs(tail_delta) > _EPS:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        head_delta = new_start - old_start
        if abs(head_delta) > _EPS:
            self.ripple_upstream(shot_id, old_start, head_delta)
        if _enforce:
            self._enforce_gap_holds()
        self.store.mark_dirty()

    # ---- gap application -------------------------------------------------

    def apply_gap(self, gap: float, scope: str = "all", shot_id: Optional[int] = None) -> bool:
        """Apply *gap* to shots per *scope* (``all`` / ``start`` / ``end`` / ``start_end``).

        Returns ``True`` if any shot was repositioned.
        """
        sorted_s = self.store.sorted_shots()
        if not sorted_s:
            return False
        if scope == "all":
            self.respace(gap=gap, start_frame=sorted_s[0].start)
            return True
        if shot_id is None:
            return False
        idx = next((i for i, s in enumerate(sorted_s) if s.shot_id == shot_id), None)
        if idx is None:
            return False
        moved = False
        if scope in ("start", "start_end") and idx > 0:
            self.move_shot(shot_id, sorted_s[idx - 1].end + gap)
            moved = True
            sorted_s = self.store.sorted_shots()
            idx = next(
                (i for i, s in enumerate(sorted_s) if s.shot_id == shot_id), idx
            )
        if scope in ("end", "start_end") and idx < len(sorted_s) - 1:
            self.move_shot(sorted_s[idx + 1].shot_id, sorted_s[idx].end + gap)
            moved = True
        return moved

    # ---- reorder (pure plan_reorder + apply park/land) -------------------

    def move_shot_to_position(self, shot_id: int, target_pos: int) -> None:
        """Reorder *shot_id* to 1-based timeline position *target_pos*."""
        plan = plan_reorder(self.store, shot_id, target_pos, self.store.gap)
        if not plan.moves:
            return
        apply(plan, self.store, move_keys=self._move_keys)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    # ---- per-object segment collection (timeline track data) -------------

    def collect_object_segments(self, shot_id: int, ignore: Optional[str] = None,
                                motion_rate: float = 1e-3,
                                ignore_holds: bool = True) -> List[dict]:
        """Per-object keyed-span segments within a shot — the sequencer track data.

        For each of the shot's objects, returns one segment dict spanning its
        transform keys inside the shot bounds:
        ``{"obj", "curves", "keyframes", "start", "end", "duration", "segment_range"}``.
        Auto-discovers keyed transforms when the shot has none (mirrors mayatk).

        Divergence (ledgered): mayatk splits each object into multiple *motion*
        sub-segments via its 1,599-line ``SegmentKeys`` (static-hold detection);
        this returns one span per object — which is exactly what mayatk itself
        backfills to for a continuous-motion object, and correct for the common
        case.  Multi-burst-with-holds sub-splitting is a documented follow-up;
        ``ignore``/``motion_rate``/``ignore_holds`` are accepted for signature
        parity and currently advisory.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return []
        try:
            import bpy
        except ImportError:
            return []
        scene = bpy.context.scene
        if scene is None:
            return []
        nodes = [n for n in shot.objects if scene.objects.get(n) is not None]
        if not nodes:
            discovered = self._find_keyed_transforms(shot.start, shot.end)
            if discovered:
                shot.objects = sorted(set(discovered))
                self.store.update_shot(shot.shot_id, objects=shot.objects)
                nodes = [n for n in shot.objects if scene.objects.get(n) is not None]
            if not nodes:
                return []

        return self._span_segments(scene, nodes, shot.start, shot.end)

    @staticmethod
    def _span_segments(scene, names, lo: float, hi: float) -> List[dict]:
        """Per-object keyed-span segment dicts for *names* within ``[lo, hi]``.

        The single home for the segment-dict *shape* — shared by
        :meth:`collect_object_segments` (stored shots) and the panel's scene-wide
        shotless view, so the two can't drift.  Missing objects are skipped.
        """
        out: List[dict] = []
        for name in names:
            obj = scene.objects.get(name)
            if obj is None:
                continue
            times = sorted({
                round(kp.co[0], 6)
                for fc in iter_action_fcurves(obj)
                if _is_transform_path(fc.data_path)
                for kp in fc.keyframe_points
                if lo - _EPS <= kp.co[0] <= hi + _EPS
            })
            if not times:
                continue
            out.append({
                "obj": name,
                "curves": [],
                "keyframes": times,
                "start": times[0],
                "end": times[-1],
                "duration": times[-1] - times[0],
                "segment_range": (times[0], times[-1]),
            })
        return out

    # ---- trim to content (hybrid: measure is scene, ripple is pure) -----

    def _content_range(self, shot) -> Optional[Tuple[float, float]]:
        """Return ``(first, last)`` key time across *shot*'s objects within its bounds.

        ``None`` when the shot's objects carry no transform keys in ``[start, end]``.
        """
        try:
            import bpy
        except ImportError:
            return None
        scene = bpy.context.scene
        if scene is None:
            return None
        lo = None
        hi = None
        for name in shot.objects:
            obj = scene.objects.get(name)
            if obj is None:
                continue
            for fc in iter_action_fcurves(obj):
                if not _is_transform_path(fc.data_path):
                    continue
                for kp in fc.keyframe_points:
                    t = kp.co[0]
                    if t < shot.start - _EPS or t > shot.end + _EPS:
                        continue
                    lo = t if lo is None else min(lo, t)
                    hi = t if hi is None else max(hi, t)
        if lo is None:
            return None
        return (lo, hi)

    def fit_shot_to_content(self, shot_id: int, mode: str = "trim") -> Tuple[float, float]:
        """Resize a shot to its keyed content, rippling neighbours by the deltas.

        ``mode="trim"`` only moves bounds *inward* (never past existing content);
        the pivot shot's own keys are never moved — only its bounds change and the
        neighbours ripple to keep spacing.  Returns ``(head_delta, tail_delta)``.
        """
        shot = self.store.shot_by_id(shot_id)
        if shot is None:
            return (0.0, 0.0)
        content = self._content_range(shot)
        if content is None:
            return (0.0, 0.0)
        content_start, content_end = content
        old_start, old_end = shot.start, shot.end
        if mode == "trim":
            new_start = max(old_start, content_start)
            new_end = min(old_end, content_end)
        else:  # "fit" — resize exactly to content
            new_start, new_end = content_start, content_end
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        head_delta = new_start - old_start
        tail_delta = new_end - old_end
        if abs(head_delta) < _EPS and abs(tail_delta) < _EPS:
            return (0.0, 0.0)
        shot.start = new_start
        shot.end = new_end
        # Neighbours ripple to preserve spacing; the pivot is excluded from both
        # plans, so the trimmed shot's own keys never move.
        if abs(tail_delta) >= _EPS:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        if abs(head_delta) >= _EPS:
            self.ripple_upstream(shot_id, old_start, head_delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()
        return (head_delta, tail_delta)

    def trim_shot_to_content(self, shot_id: int) -> Tuple[float, float]:
        """Trim empty space from a shot's start and end (bounds move inward only)."""
        return self.fit_shot_to_content(shot_id, mode="trim")

    # ---- gap-hold epilogue (no-op this phase; see module docstring) ------

    def _enforce_gap_holds(self) -> None:
        """No-op in Blender this phase (documented divergence — see module docstring)."""
        return None
