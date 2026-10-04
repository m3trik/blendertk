# !/usr/bin/python
# coding=utf-8
"""Blender shot sequencer engine — ripple editing + key motion over the shared planner.

Mirror of mayatk's ``ShotSequencer`` (name + behavior): manual definition,
auto-detection, per-object segment collection, the unified anim+audio
*sequence* model (``collect_shot_sequences`` / ``move_sequences_to_shot``),
fit/trim/extend, per-object key motion, ripple editing, reorder, respace,
gap application and serialisation.  Every collision-safe multi-shot plan is
built by the pure pythontk planner (``shot_plan``) and committed by
``ShotApply.apply``; this class injects the two Blender writer strategies
(:meth:`_move_keys` for fcurve keys, :meth:`_shift_audio_envelope` for VSE
sound strips) and supplies the scene measures the planner cannot.

DCC swaps versus the Maya original (by design, not gaps):
    * **Keys move in place.** Maya needs ``keyframe option='over'`` and a
      tangent-preserving cut-and-recreate fallback; writing
      ``keyframe_point.co[0]`` has no neighbour clamp and the point's
      interpolation/handles travel with it, so :meth:`move_curve_keys` /
      :meth:`recreate_curve_keys` collapse to one direct move.
    * **Audio = VSE sound strips.** Maya's keyed carrier tracks become strips
      (``AudioUtils.shift_clips_in_range``); a strip's position *is* its keyed
      state, so there is no batch/compositor re-sync to wrap.
    * **Gap holds** set the last key before each inter-shot gap to
      ``interpolation="CONSTANT"`` (Maya: ``step`` out-tangent).  Both are
      claimed in the shared edit ledger so they can be RELEASED again; an
      fcurve has no node name, so a claim is keyed by
      ``"<object>|<data_path>|<array_index>"`` (:meth:`_fc_key`).
    * **Reorder** goes through the pure ``plan_reorder`` + park/land apply
      (Maya hand-rolls the park loop; same result).
    * **No DAG-path reconciliation** — Blender object names are flat and
      unique, so :meth:`reconcile_all_shots` has nothing to re-resolve.
    * **Undo pairing by serial, not by name.** mayatk tags each boundary
      restore point with the undo chunk its edit landed under and checks that
      name before an undo consumes the point.  Blender's undo exposes no step
      names, so ``BlenderShotStore.scene_edit`` stamps a scene serial inside
      the step and tags the point with it; memfile undo winds the serial with
      the steps, which is how the undo/redo handlers tell our step from any
      other (``UndoLedgerMixin._native_event_is_ours``).  A no-op edit
      cancels its step (``scene_edit``'s handle), the twin of Maya discarding
      an empty chunk.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from pythontk import ShotPlanner, ShotStore
from pythontk import ShotSequencer as _ShotSequencerCore
from pythontk.core_utils.engines.shots.shot_apply import ShotApply

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore

_log = logging.getLogger(__name__)

_EPS = 1.0e-6
_SLOP = 1.0e-3  # matches mayatk's _ENVELOPE_SLOP so the window semantics agree
# ``AudioUtils.shift_clips_in_range`` inflates its window by ±1e-3; move the
# envelope's bound past that so a strip exactly on a shot boundary is never
# claimed by two envelopes (mirror of mayatk's margin).
_AUDIO_UPPER_MARGIN = 3.0e-3
# Two poses count as the same pose below this.  Used only to decide whether
# samples converging on one frame can merge losslessly or must be refused
# (mirror of mayatk's _POSE_TOL).
_POSE_TOL = 1.0e-4

# Interpolations whose segment between two equal-valued keys is constant no
# matter where the handles sit -- CONSTANT holds its value, LINEAR draws a
# straight line between the two and ignores handles entirely.  Blender's twin
# of mayatk's _STEP_TANGENTS, one entry wider for that reason: over there a
# linear plateau reports zero tangent angles and passes the angle test.
_FLAT_SPAN_INTERPOLATIONS = ("CONSTANT", "LINEAR")

# Handle types Blender re-computes on every ``fcurve.update()`` -- the twin of
# mayatk's derived tangents. Under the default auto smoothing (``CONT_ACCEL``)
# a run of keys auto on BOTH sides is solved together, so a new neighbour
# reshapes the whole run; a key that is not auto on both sides ends one.
_DERIVED_HANDLES = ("AUTO", "AUTO_CLAMPED")

# Handle types whose position Blender keeps as authored (VECTOR is re-derived
# from the neighbours on every update, like an auto handle).
_POSITIONED_HANDLES = ("FREE", "ALIGNED")

# A handle offset that moved by less than this (frames / value units) did not
# really move: freezing it would only convert its type.
_HANDLE_MOVED_TOL = 1.0e-4


class _ShotSequencerInternal(object):
    """Internal helpers for ShotSequencer."""

    @staticmethod
    def _scene():
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    @staticmethod
    def _object(name: str):
        try:
            import bpy
        except ImportError:
            return None
        return bpy.data.objects.get(name)

    @staticmethod
    def _transform_fcurves(obj):
        return [
            fc
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            if BlenderShotStore._is_transform_path(fc.data_path)
        ]

    @staticmethod
    def _fcurve_moves_in(fc, start, end, value_tolerance: float = 1e-4) -> bool:
        """True when *fc* has keys inside ``[start, end]`` whose values vary by
        more than *value_tolerance* -- mayatk's ``Detection.curve_moves_in``:
        a key is not animation, a change of value is."""
        times, values = AnimUtils.key_arrays(fc)
        i0, i1 = AnimUtils.window_indices(times, start - _EPS, end + _EPS)
        if i1 - i0 < 2:
            return False
        window = values[i0:i1]
        return (max(window) - min(window)) > value_tolerance

    @staticmethod
    def _has_motion(obj, start, end, value_tolerance: float = 1e-4) -> bool:
        """True when any transform channel of *obj* varies by > *value_tolerance* in ``[start, end]``."""
        return any(
            _ShotSequencerInternal._fcurve_moves_in(fc, start, end, value_tolerance)
            for fc in _ShotSequencerInternal._transform_fcurves(obj)
        )

    @staticmethod
    def _fc_key(obj_name: str, fc) -> str:
        """Stable ledger key for an fcurve.

        Maya claims are keyed by animCurve NODE name; a Blender fcurve is not
        a node and has no name, so its owner plus channel identity stands in.
        Same shape, same uniqueness, and it survives everything except a
        rename of the object (which drops the claim, not the animation).
        """
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        return BlenderShotStore.curve_key(obj_name, fc.data_path, fc.array_index)

    @staticmethod
    def _fcurve_for_key(key: str):
        """Resolve a :meth:`_fc_key` back to a live fcurve, or ``None``."""
        try:
            obj_name, data_path, index = key.rsplit("|", 2)
            index = int(index)
        except (ValueError, AttributeError):
            return None
        obj = _ShotSequencerInternal._object(obj_name)
        if obj is None:
            return None
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if fc.data_path == data_path and fc.array_index == index:
                return fc
        return None

    @staticmethod
    def _key_index_at(fc, t: float, eps: float = _SLOP):
        """Index of *fc*'s keyframe point within *eps* of *t*, else ``None``."""
        times = AnimUtils.key_times(fc)
        i0, i1 = AnimUtils.window_indices(times, t - eps, t + eps)
        if i1 <= i0:
            return None
        return i0

    @classmethod
    def _same_value(cls, fc, t_a: float, t_b: float, eps: float = _SLOP) -> bool:
        """Whether *fc*'s keys at *t_a* and *t_b* hold the same value (to
        ``1e-6``, relative above 1) -- a pose already waiting at *t_b*."""
        ia, ib = cls._key_index_at(fc, t_a, eps), cls._key_index_at(fc, t_b, eps)
        if ia is None or ib is None:
            return False
        a, b = fc.keyframe_points[ia].co[1], fc.keyframe_points[ib].co[1]
        return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))

    @classmethod
    def _any_key_at(cls, curves, frame: float, eps: float = _SLOP) -> bool:
        """Whether any fcurve of *curves* holds a key within *eps* of *frame*:
        the ``seam_keyed`` question ``ShotStore.enclosing_bounds`` asks of the
        curves a landing rides on (mirror of mayatk's)."""
        return any(cls._key_index_at(fc, frame, eps) is not None for fc in curves)

    @staticmethod
    def _retime_fcurve(
        fc, lo, hi, old_start, old_end, new_start, new_end, ledger=None, key=None
    ) -> int:
        """``AnimUtils.remap_keys_in_window`` that carries the edit ledger along.

        A retime moves keys the shot system claims -- a pin on a bound, the
        seam key a gap hold made CONSTANT -- and each claim has to move with
        its key, or the next reconcile releases it and the sample reads as the
        animator's from then on (mirror of mayatk's
        ``_ShotApplyInternal._claims_follow``).  The keys in ``[lo, hi]`` are
        read BEFORE the remap and their claims remapped after it: claims
        travel by the keys moved -- the movers' rule, ``ledger.remap`` --
        never by the window.  An unclaimed curve, or no *ledger*, costs
        nothing.

        Returns the number of keys remapped.
        """
        moved = []
        if ledger is not None and key is not None and key in ledger.curves:
            moved = [t for t in AnimUtils.key_times(fc) if lo <= t <= hi]
        n = AnimUtils.remap_keys_in_window(
            fc, lo, hi, old_start, old_end, new_start, new_end
        )
        if n and moved:
            scale = (new_end - new_start) / (old_end - old_start)
            ledger.remap(
                key, [(t, new_start + (t - old_start) * scale) for t in sorted(moved)]
            )
        return n

    @staticmethod
    def _has_keys(obj, start, end) -> bool:
        """True when any transform channel of *obj* carries a key in the range."""
        for fc in _ShotSequencerInternal._transform_fcurves(obj):
            times = AnimUtils.key_times(fc)
            i0, i1 = AnimUtils.window_indices(times, start - _EPS, end + _EPS)
            if i1 > i0:
                return True
        return False


# The helper base goes FIRST: listed after pythontk's class, anything it defines
# under a hook name is shadowed by the no-op default (test_shots_slots.py).
class ShotSequencer(_ShotSequencerInternal, _ShotSequencerCore):
    """Manages a :class:`BlenderShotStore` and provides ripple editing and
    keyframe manipulation on top of it.

    The timeline operations themselves are the DCC-free
    :class:`pythontk.ShotSequencer` orchestration, shared with mayatk; this
    class supplies the Blender scene I/O it reaches through hooks (fcurve key
    movers, VSE strips, gap holds, the boundary-sample ledger, content scans).

    Parameters:
        shots: Initial shot list (creates an internal store).
        store: Existing store to wrap.  Takes precedence over *shots*.
    """

    #: The Blender store: scene persistence plus the Blender acquisition hooks.
    STORE_CLASS = BlenderShotStore

    def __init__(self, shots=None, store=None):
        if store is None and isinstance(shots, ShotStore):
            # ``ShotSequencer(store)`` — the positional form the Blender tests
            # and earlier callers use; mayatk's signature is (shots, store).
            shots, store = None, shots
        super().__init__(shots, store)

    # ---- scene hooks (the pythontk ShotSequencer contract) ----------------

    def _scene_available(self) -> bool:
        """A Blender scene is reachable (``bpy`` importable); else bounds only."""
        return _ShotSequencerInternal._scene() is not None

    def _move_content_keys(
        self, objects, env_lo, env_hi, delta, lo_open=False, hi_closed=False
    ) -> None:
        """Move the envelope's keyed content (:meth:`_batch_move_keys`)."""
        self._batch_move_keys(
            objects, env_lo, env_hi, delta, lo_open=lo_open, hi_closed=hi_closed
        )

    def _move_audio_sequence(self, seq: Dict[str, Any], delta: float) -> None:
        """Shift one VSE sound strip (a strip's position IS its keyed state)."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(
            seq["start"], seq["end"], delta, names=[seq["obj"]]
        )

    def _object_key_probe(self, obj: str):
        """``frame -> bool`` over *obj*'s fcurves (:meth:`_any_key_at`)."""
        curves = self._fcurves_of(obj, None)
        return lambda frame: _ShotSequencerInternal._any_key_at(curves, frame)

    def _animator_times_in(self, obj: str, attr: Optional[str], window) -> List[float]:
        """The animator's key times on *obj*'s (*attr*'s) fcurves inside *window*."""
        found: List[float] = []
        for fc in self._fcurves_of(obj, attr):
            found.extend(self._animator_key_times(obj, fc, window))
        return found

    # ---- Blender-specific behaviour (differs from mayatk; kept deliberately) --

    def slide_shot(
        self,
        shot_id: int,
        new_start: float,
        direction: str = "downstream",
        _enforce: bool = True,
    ) -> None:
        """Slide a shot intact to *new_start*, rippling only in *direction*.

        The shared orchestration (:meth:`pythontk.ShotSequencer.slide_shot`)
        with one Blender difference: *new_start* is snapped BEFORE the clamp
        and the no-op test, so a sub-frame request that rounds back onto the
        shot's own start moves nothing (mayatk ripples the unsnapped delta).
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        old_start, old_end = shot.start, shot.end
        new_start = self._clamp_slide_start(shot, self.store.snap(new_start), direction)
        delta = new_start - old_start
        if abs(delta) < _EPS:
            return
        self._slide(shot, old_start, old_end, new_start, delta, direction, _enforce)

    def split_shot(
        self,
        shot_id: int,
        at_frame: float,
        name: Optional[str] = None,
        gap: float = 0.0,
    ):
        """Cut a shot in two at *at_frame*, leaving its content where it is.

        The shared orchestration (:meth:`pythontk.ShotSequencer.split_shot`)
        with one Blender difference in validation order: an explicit *name*
        the store refuses is reported BEFORE the frame check, and a generated
        tail name is not re-validated (mayatk checks the tail name, explicit
        or generated, after the frame check).  Either way nothing is trimmed
        before a refusal.

        Raises:
            ValueError: If *shot_id* does not exist, *at_frame* is not
                strictly inside it, or the store refuses *name*.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        error = self.store.name_error(name) if name else None
        if error:
            raise ValueError(error)
        at = self.store.snap(float(at_frame))
        if not (shot.start + _EPS < at < shot.end - _EPS):
            raise ValueError(
                f"Split frame {at:g} is not inside {shot.name} "
                f"[{shot.start:g}-{shot.end:g}]"
            )
        tail_name = name or self.store.unique_name(f"{shot.name}_2")
        return self._split_at(shot, at, tail_name, shot.end, gap)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(
        start,
        end,
        value_tolerance: float = 1e-4,
        require_motion: bool = True,
    ) -> List[str]:
        """Names of transforms that MOVE in ``[start, end]``.

        Only content channels count (``_is_transform_path``: the transform
        channels and the render-effect properties) — any other custom
        property is ignored so marker objects don't register as scene content.

        Membership needs motion: a transform channel whose values vary by
        more than *value_tolerance* inside the range.  Keys alone are not
        content -- a baked rig carries a key on every frame of every shot and
        holds still through most of them (mayatk measured 531 of 603
        memberships on a production assembly as flat-keyed proxy joints).
        Discovery used to accept any key in range so a hold-only object would
        not be stranded when its shot moved; the movers now carry the whole
        keyed content of an envelope whatever the list says
        (:meth:`_content_objects`), so that guarantee no longer needs the
        list.  ``require_motion=False`` is the old rule, on request.
        """
        scene = _ShotSequencerInternal._scene()
        if scene is None:
            return []
        result: List[str] = []
        for obj in scene.objects:
            if require_motion:
                hit = _ShotSequencerInternal._has_motion(
                    obj, start, end, value_tolerance
                )
            else:
                hit = _ShotSequencerInternal._has_keys(obj, start, end)
            if hit:
                result.append(obj.name)
        return sorted(set(result))

    @staticmethod
    def _shot_nodes(shot) -> list:
        """Return the shot's object names that still exist in the file.

        Blender names are flat and unique, so no ambiguity resolution is
        needed (Maya's twin disambiguates same-named DAG nodes here).
        """
        if not shot.objects:
            return []
        return [
            n for n in shot.objects if _ShotSequencerInternal._object(n) is not None
        ]

    def reconcile_all_shots(self) -> bool:
        """Follow members renamed since they were stored, NEVER dropping one.

        Mirror of mayatk's: Blender names are flat, so there are no DAG paths
        to re-resolve, but a RENAME leaves a shot naming an object that is no
        longer there.  mayatk follows it through the anim curves Maya named
        after the old node; the Blender twin is the action slot, named
        ``OB<name>`` when the object was first keyed and left as it is by a
        rename (:meth:`_renamed_target`) -- so this works across sessions,
        not only for renames it saw happen.

        A name that resolves to nothing is kept as stored (renamed and deleted
        look the same from a name, and dropping membership is irreversible;
        ``assess`` surfaces deletions).  Everything the store and the ledger
        key by object name follows the rename too: the hidden / pinned /
        locked sets, and the edit claims (``<object>|<path>|<index>``).

        Returns ``True`` if anything was re-pointed.
        """
        try:
            import bpy
        except ImportError:
            return False
        existing = set(bpy.data.objects.keys())
        stale = {o for shot in self.store.shots for o in shot.objects} - existing
        if not stale:
            return False
        renames = self._renamed_targets(stale)
        if not renames:
            return False
        store = self.store
        with store.batch_update():
            for shot in store.shots:
                objects = sorted({renames.get(o, o) for o in shot.objects})
                if objects != sorted(shot.objects):
                    store.update_shot(shot.shot_id, objects=objects)
            for attr in ("hidden_objects", "pinned_objects", "locked_objects"):
                names = getattr(store, attr, None)
                if names and names & set(renames):
                    setattr(store, attr, {renames.get(n, n) for n in names})
            ledger = self.ledger
            for key in sorted(ledger.curves):
                obj_name, sep, rest = key.partition("|")
                if sep and obj_name in renames:
                    ledger.rename_curve(key, f"{renames[obj_name]}|{rest}")
            store.mark_dirty()
        return True

    @staticmethod
    def _renamed_targets(stale) -> dict:
        """``{old name: new name}`` for the *stale* names an object answers for.

        Blender names an object's action slot after the object when it is
        first keyed (``OB<name>``; legacy actions: ``<name>Action``) and a
        rename leaves both as they were -- the twin of the anim curves Maya
        named after the old node.  Exactly one object must answer: a slot two
        objects share (a linked duplicate) cannot say which one was renamed.
        One scan of the objects for every stale name: this runs on each
        keying burst's rebuild, and a deleted member stays stale for good
        (mayatk memoises its curve scan per pass for the same reason).
        """
        import bpy

        claims: dict = {}
        for obj in bpy.data.objects:
            ad = obj.animation_data
            if ad is None or ad.action is None:
                continue
            slot = getattr(ad, "action_slot", None)
            if slot is not None:
                old = slot.name_display
            elif ad.action.name.endswith("Action"):
                old = ad.action.name[: -len("Action")]
            else:
                continue
            if old in stale and obj.name != old:
                claims.setdefault(old, []).append(obj.name)
        return {old: hits[0] for old, hits in claims.items() if len(hits) == 1}

    # ---- per-object segment collection (timeline track data) -------------

    def collect_object_segments(
        self,
        shot_id: int,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
        ignore_holds: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect per-object animation segments within a shot's range.

        Each dict has ``"obj"``, ``"curves"``, ``"keyframes"``, ``"start"``,
        ``"end"``, ``"duration"`` and ``"segment_range"`` — the sequencer
        track data.  Motion/hold splitting is :class:`~blendertk.SegmentKeys`
        (the port of Maya's ``SegmentKeys.collect_segments``); auto-discovers
        keyed transforms when the shot has none.

        Parameters:
            shot_id: The shot whose objects and range to query.
            ignore: Channel label(s) to exclude.
            motion_rate: Per-frame rate-of-change threshold.
            ignore_holds: If True (default), flat-key hold spans are excluded
                so only actual motion is shown.  When False, trailing holds are
                absorbed into adjacent motion segments and hold-only objects
                produce a single segment spanning all keys.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return []
        nodes = self._shot_nodes(shot)
        if not nodes:
            discovered = self._find_keyed_transforms(shot.start, shot.end)
            if discovered:
                shot.objects = sorted(set(discovered))
                self.store.update_shot(shot.shot_id, objects=shot.objects)
                nodes = self._shot_nodes(shot)
            if not nodes:
                return []

        from blendertk.anim_utils.segment_keys import SegmentKeys

        segments = SegmentKeys.collect_segments(
            nodes,
            split_static=True,
            ignore=ignore,
            time_range=(shot.start, shot.end),
            ignore_holds=ignore_holds,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=motion_rate,
        )
        # A member that MOVES in the shot but produced no segment -- its
        # motion sits below ``motion_rate`` (a slow drift), which
        # ``SegmentKeys.collect_segments`` drops under ignore_holds=True --
        # still gets one span-of-keys track.  A member whose keys in range
        # are all flat gets none: keys are not animation (mirror of mayatk;
        # see :meth:`_find_keyed_transforms`).
        if ignore_holds and nodes:
            covered = {s["obj"] for s in segments}
            scene = _ShotSequencerInternal._scene()
            if scene is not None:
                moving = []
                for n in nodes:
                    if n in covered:
                        continue
                    obj = _ShotSequencerInternal._object(n)
                    if obj is None:
                        continue
                    fcurves = _ShotSequencerInternal._transform_fcurves(obj)
                    if any(
                        _ShotSequencerInternal._fcurve_moves_in(
                            fc, shot.start, shot.end
                        )
                        for fc in fcurves
                    ):
                        moving.append(n)
                        continue
                    # A FEW value-less keys of the animator's OWN are marks
                    # (the lone key Move to Shot just brought here) and draw
                    # as stepped points; many are a bake, which draws
                    # nothing, and the system's bound samples are never
                    # marks (see _animator_marks).  Mirrors mayatk.
                    marks = self._animator_marks(n, fcurves, shot)
                    if 0 < len(marks) <= self.ISOLATED_KEY_LIMIT:
                        segments.extend(self._point_segment(n, t) for t in marks)
                segments.extend(
                    self._span_segments(scene, moving, shot.start, shot.end)
                )
        return segments

    #: A flat member with this many keys in a shot (or fewer) shows them as
    #: stepped points; more is a bake, which draws nothing.
    ISOLATED_KEY_LIMIT = 4

    def _animator_marks(self, obj_name: str, fcurves, shot) -> List[float]:
        """The keys of *fcurves* inside *shot* that are the animator's own marks.

        Mirror of mayatk's: the samples the system planted for a bound (the
        ledger's claims, :meth:`_animator_key_times`) are left out, and so is
        an unclaimed key ON a bound that provably holds nothing
        (:meth:`_sample_is_redundant`) -- the shape a released bound sample
        takes.  mayatk's docstring carries the production measurement.
        """
        marks: set = set()
        for fc in fcurves:
            for t in self._animator_key_times(obj_name, fc, (shot.start, shot.end)):
                if abs(t - shot.start) <= _SLOP or abs(t - shot.end) <= _SLOP:
                    idx = _ShotSequencerInternal._key_index_at(fc, t)
                    if idx is not None and self._sample_is_redundant(
                        fc, idx, terminal=False
                    ):
                        continue
                marks.add(float(t))
        return sorted(marks)

    @staticmethod
    def _span_segments(scene, names, lo: float, hi: float) -> List[dict]:
        """One keyed-span segment per object in *names* within ``[lo, hi]``.

        The backfill primitive (an object with keys but no motion segment) —
        same dict shape as :meth:`collect_object_segments`.  Missing objects
        are skipped.
        """
        out: List[dict] = []
        for name in names:
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            # Any key in range, as mayatk's ``keyframe -query`` over the node.
            curves = list(BlenderShotStore.iter_action_fcurves(obj))
            times_set: set = set()
            for fc in curves:
                kt = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(kt, lo - _EPS, hi + _EPS)
                times_set.update(round(t, 6) for t in kt[i0:i1])
            times = sorted(times_set)
            if not times:
                continue
            out.append(
                {
                    "obj": name,
                    "curves": curves,
                    "keyframes": times,
                    "start": times[0],
                    "end": times[-1],
                    "duration": times[-1] - times[0],
                    "segment_range": (times[0], times[-1]),
                }
            )
        return out

    # ---- unified sequence model (anim + audio) ---------------------------

    @staticmethod
    def _read_all_audio_events() -> Dict[str, List[tuple]]:
        """Return ``{strip_name: [(start, end)]}`` for every VSE sound strip."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        try:
            clips = AudioUtils.list_clips()
        except Exception:
            return {}
        return {
            c["name"]: [(float(c["frame_start"]), float(c["frame_end"]))] for c in clips
        }

    def _collect_audio_sequences(
        self, start: float, end: float
    ) -> List[Dict[str, Any]]:
        """Audio strips overlapping ``[start, end]`` as sequence dicts.

        Each dict carries ``{"kind": "audio", "obj": <strip name>, "start", "end"}``.
        Every call reads fresh so external VSE edits are never masked.
        """
        sequences: List[Dict[str, Any]] = []
        for tid, events in self._read_all_audio_events().items():
            for ev_start, ev_end in events:
                if ev_end < start or ev_start > end:
                    continue
                sequences.append(
                    {"kind": "audio", "obj": tid, "start": ev_start, "end": ev_end}
                )
        return sequences

    def _drop_claimed_seam_copy(self, seq, target: float, seam: float, eps=_SLOP):
        """Cut the key a head landing claims (mirror of mayatk's): on each
        fcurve of *seq* whose own key lands exactly on *seam*, the stationary
        key already sitting there -- the source's closing-pose copy a leading
        room's split left. The block's own key at the source frame stays, so
        no fcurve is emptied; a two-key one (that key and the seam pose) is
        the common on/off shape, and a "never below two keys" guard left its
        seam pose to be pushed a frame into the destination."""
        src = seam - (target - seq["start"])
        if not seq["start"] - eps <= src <= seq["end"] + eps:
            return
        times = seq.get("times")
        if times and not any(abs(t - src) <= eps for t in times):
            return
        for fc in self._fcurves_of(seq["obj"], seq.get("attr")):
            if _ShotSequencerInternal._key_index_at(fc, src, eps) is None:
                continue
            idx = _ShotSequencerInternal._key_index_at(fc, seam, eps)
            if idx is None:
                continue
            fc.keyframe_points.remove(fc.keyframe_points[idx])
            fc.update()
            self.ledger.release(_ShotSequencerInternal._fc_key(seq["obj"], fc), seam)

    # ---- shot fit / trim / extend ----------------------------------------

    def _key_extent(
        self, shot, probe_outside: bool, reach: Optional[float] = None
    ) -> tuple:
        """Where *shot*'s content sits, as the keys tell it -- the one scan behind
        :meth:`fit_shot_to_content` and the plain drag's clamp in
        :meth:`resize_shot_bounds`.  Mirrors mayatk's (its docstring carries the
        production cases).

        Returns ``(inner_start, inner_end, outer_start, outer_end, on_bound)``.
        The outer probe reads the shot's own envelope only.  Content is MOTION:
        a curve that never moves inside the probed window holds no bound.
        ``on_bound`` lists the UNCLAIMED keys sitting exactly on a bound that
        are provably redundant (:meth:`_sample_is_redundant`) -- a disowned
        pin -- which hold nothing and are cut once a bound moves past them.
        *reach* (frames) is the explicit "extend to the keys I set" probe:
        BOTH gaps, each cut at *reach* from the bound, the leading one up to
        (never on) the previous shot's end; a neighbour's span is never read.
        """
        inner_start = inner_end = None
        outer_start = outer_end = None
        on_bound: list = []
        if not shot.objects:
            return inner_start, inner_end, outer_start, outer_end, on_bound
        ordered = self.sorted_shots()
        idx = next(i for i, s in enumerate(ordered) if s.shot_id == shot.shot_id)
        head_is_open = idx == 0
        prev_end = ordered[idx - 1].end if idx > 0 else None
        tail_ceiling = ordered[idx + 1].start if idx + 1 < len(ordered) else None
        if probe_outside:
            lo = -1e9 if head_is_open else shot.start
            hi = 1e9 if tail_ceiling is None else tail_ceiling
            if reach is not None:
                lo = max(shot.start - reach, -1e9 if prev_end is None else prev_end)
                hi = min(shot.end + reach, hi)
        else:
            lo, hi = shot.start, shot.end
        for name in self._shot_nodes(shot):
            obj = _ShotSequencerInternal._object(name)
            # Every curve on the member, as mayatk's scan reads every animCurve
            # on the node -- the movers carry them all (:meth:`_move_keys`), so
            # trim/fit must see the same keys or a custom property's motion
            # would ride a ripple past a bound that ignored it.
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                if not _ShotSequencerInternal._fcurve_moves_in(fc, lo, hi):
                    continue
                for t in self._animator_key_times(name, fc):
                    if shot.start <= t <= shot.end:
                        if abs(t - shot.start) <= _SLOP or abs(t - shot.end) <= _SLOP:
                            i = _ShotSequencerInternal._key_index_at(fc, t)
                            if i is not None and self._sample_is_redundant(fc, i):
                                on_bound.append((name, fc, t))
                                continue
                        inner_start = t if inner_start is None else min(inner_start, t)
                        inner_end = t if inner_end is None else max(inner_end, t)
                        continue
                    if not probe_outside:
                        continue
                    if t < shot.start:
                        if reach is not None:
                            if t < lo - _SLOP or (
                                prev_end is not None and t <= prev_end + _SLOP
                            ):
                                continue  # out of reach, or the previous shot's fencepost
                        elif not head_is_open:
                            continue  # the leading gap is the previous shot's
                        outer_start = t if outer_start is None else min(outer_start, t)
                    elif tail_ceiling is None or t < tail_ceiling - _EPS:
                        if reach is not None and t > hi + _SLOP:
                            continue
                        outer_end = t if outer_end is None else max(outer_end, t)
        return inner_start, inner_end, outer_start, outer_end, on_bound

    def _cut_passed_bound_keys(
        self, on_bound, old_start, old_end, new_start, new_end
    ) -> int:
        """Cut the redundant on-bound keys (:meth:`_key_extent`) a bound has just
        moved past, so their frames are clear before anything ripples onto them."""
        cut = 0
        for name, fc, t in on_bound:
            passed = (abs(t - old_end) <= _SLOP and new_end < t - _SLOP) or (
                abs(t - old_start) <= _SLOP and new_start > t + _SLOP
            )
            if not passed or len(fc.keyframe_points) <= 2:
                continue
            i = _ShotSequencerInternal._key_index_at(fc, t)
            if i is None:
                continue
            try:
                fc.keyframe_points.remove(fc.keyframe_points[i])
                fc.update()
            except (RuntimeError, TypeError):
                continue  # locked/linked curve -- leave it as it was
            cut += 1
            # A gap hold's step claim on it goes too, or what ripples onto the
            # frame inherits it (mirror of mayatk's).
            self.ledger.release(_ShotSequencerInternal._fc_key(name, fc), t)
        return cut

    # ---- automatic shot detection ----------------------------------------

    def detect_shots(
        self,
        objects: Optional[List[str]] = None,
        gap_threshold: float = 5.0,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
        min_duration: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """Detect shot boundaries from existing animation (delegates to ``Detection``).

        Returns candidate dicts with ``"name"``, ``"start"``, ``"end"``,
        ``"objects"`` — suitable for :meth:`define_shot`.
        """
        from blendertk.anim_utils.shots._detection import Detection

        return Detection.detect_shot_regions(
            objects=objects,
            gap_threshold=gap_threshold,
            ignore=ignore,
            motion_rate=motion_rate,
            min_duration=min_duration,
        )

    # ---- key motion primitives -------------------------------------------

    def _move_keys(
        self,
        objects,
        env_lo: float,
        env_hi: float,
        delta: float,
        over: bool = False,
        lo_open: bool = False,
        hi_closed: bool = False,
    ) -> None:
        """Shift the keys of *objects* inside a time window by *delta* frames.

        The ``move_keys`` strategy ``ShotApply.apply`` calls: translate every
        keyframe point (and both bezier handles) whose time falls in the window,
        then ``fcurve.update()``.  Each bound is deflated by :data:`_SLOP` to
        exclude a sample sitting exactly on it and inflated to include one,
        per the fencepost flags: contiguous shots share a sample and it
        belongs to the PRECEDING shot (``hi_closed`` on that shot,
        ``lo_open`` on the next).  With a gap both bounds deflate — the old
        half-open ``[env_lo, env_hi)``.  ``over`` is advisory: a direct
        ``co[0]`` write has no neighbour clamp, so keys always pass.
        """
        if not objects or abs(delta) < _EPS:
            return
        lo = (env_lo + _SLOP) if lo_open else (env_lo - _SLOP)
        hi = (env_hi + _SLOP) if hi_closed else (env_hi - _SLOP)
        led = self.ledger
        for name in objects:
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                times = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(times, lo, hi, hi_closed)
                if i1 <= i0:
                    continue
                AnimUtils.shift_keys_in_window(
                    fc, lo, hi, delta, inclusive_hi=hi_closed
                )
                # A claim is a (curve, time) pair, so it travels with the KEYS
                # that moved -- exactly those, never a bound sample the
                # fencepost rule left in place (a window shift, inflated by
                # the ledger's own epsilon, reached it; mirrors mayatk).
                led.remap(
                    _ShotSequencerInternal._fc_key(name, fc),
                    [(t, t + delta) for t in times[i0:i1]],
                )

    def _batch_move_keys(
        self,
        objects,
        env_lo,
        env_hi,
        delta,
        lo_open: bool = False,
        hi_closed: bool = False,
    ) -> None:
        """Shift every key of *objects* inside the envelope by *delta*.

        Takes the same window (bounds plus fencepost flags) as the plan
        path's writer, so the two movers cannot disagree about which shot
        owns a shared sample.  Each fcurve's keys in the window go through
        :meth:`move_curve_keys`, as mayatk's do: the landing zone is cleared
        first (flat holds absorbed, posed keys pushed aside) -- a raw window
        shift interleaved a moved shot's keys with a shared curve's gap keys
        already sitting where it landed -- and the claims ride along.
        """
        if not objects or abs(delta) < _EPS:
            return
        lo = (env_lo + _SLOP) if lo_open else (env_lo - _SLOP)
        hi = (env_hi + _SLOP) if hi_closed else (env_hi - _SLOP)
        for name in objects:
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            for fc in list(BlenderShotStore.iter_action_fcurves(obj)):
                times = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(times, lo, hi, hi_closed)
                if i1 <= i0:
                    continue
                self.move_curve_keys(
                    fc,
                    list(times[i0:i1]),
                    delta,
                    ledger=self.ledger,
                    ledger_key=_ShotSequencerInternal._fc_key(name, fc),
                )

    @staticmethod
    def _shift_audio(old_start: float, old_end: float, delta: float) -> None:
        """Shift VSE sound strips whose start falls in ``[old_start, old_end]`` by *delta*."""
        if abs(delta) < _EPS:
            return
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(old_start, old_end, delta)

    @staticmethod
    def _shift_audio_envelope(
        env_lo: float,
        env_hi: float,
        delta: float,
        lo_open: bool = False,
        hi_closed: bool = False,
    ) -> None:
        """``shift_audio`` strategy for ``ShotApply.apply`` (half-open envelope).

        ``env_hi`` extends to the next shot's current start, so strips sitting in
        the trailing gap travel with the preceding shot (mirror of the keyframe
        fade-tail rule); the upper bound is deflated by
        :data:`_AUDIO_UPPER_MARGIN` so a strip exactly on a shot boundary can't be
        claimed by two envelopes.
        """
        if abs(delta) < _EPS:
            return
        hi = env_hi if env_hi < ShotPlanner.UNBOUNDED else env_lo + 1.0e7
        hi += _AUDIO_UPPER_MARGIN if hi_closed else -_AUDIO_UPPER_MARGIN
        lo = env_lo + _AUDIO_UPPER_MARGIN if lo_open else env_lo
        if hi <= lo:
            return
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(lo, hi, delta)

    @staticmethod
    def _keyed_transform_times() -> dict:
        """Map every object's name to the key times on its transform channels.

        Content channels only (``_is_transform_path``: transform channels
        and the render-effect properties), so a marker's custom property
        never makes it look like scene content.
        """
        scene = _ShotSequencerInternal._scene()
        if scene is None:
            return {}
        keyed: dict = {}
        for obj in scene.objects:
            times: list = []
            for fc in _ShotSequencerInternal._transform_fcurves(obj):
                times.extend(AnimUtils.key_times(fc) or [])
            if times:
                keyed[obj.name] = times
        return keyed

    @staticmethod
    def _named_fcurves(names) -> list:
        """``[(object_name, fcurve), ...]`` for the named objects, in name order.

        The owner comes along because an fcurve carries no name: its ledger
        key is built from the object plus the channel (:meth:`_fc_key`).
        """
        out: list = []
        for name in names or ():
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            out.extend((name, fc) for fc in BlenderShotStore.iter_action_fcurves(obj))
        return out

    @staticmethod
    def _handle_snapshot(kp) -> tuple:
        """``(left type, right type, left offset, right offset)`` of point *kp*,
        offsets relative to the point -- what a split carries to its copy."""
        co = kp.co
        return (
            kp.handle_left_type,
            kp.handle_right_type,
            (kp.handle_left[0] - co[0], kp.handle_left[1] - co[1]),
            (kp.handle_right[0] - co[0], kp.handle_right[1] - co[1]),
        )

    @classmethod
    def _hold_split_handles(
        cls, fc, frame: float, original: float, handles, shared: bool = True
    ) -> None:
        """Both shots play on as they did around a split seam (mirror of
        mayatk's ``_hold_split_tangent``; BACKLOG 2026-09-07).

        The key at *frame* -- the copy, or the carried sample itself when the
        preceding shot does not animate the curve (*shared* False) -- opens
        the following shot with the original's handle types, and an AUTHORED
        handle sits where the original's did. Inserted plain it took the
        default AUTO_CLAMPED handles: a VECTOR seam played 1.152 off.

        A DERIVED handle (:data:`_DERIVED_HANDLES`) cannot simply be put back:
        under the default ``CONT_ACCEL`` auto smoothing a run of auto keys is
        solved together, so the new neighbour reshapes the run on both sides
        of the seam -- measured on a seam on a slope (``probe_split_freeze.py``),
        AUTO_CLAMPED played 0.437 off before it and 0.718 after, AUTO 0.332 and
        0.932. When either shot-facing half moved, both are put back at the
        offsets they had while shared -- the copy's right handle and, where it
        stays (*shared*), the left one of the original at *original* -- and
        their keys are frozen ``FREE``. A key that is not auto on both sides
        ends a smoothing run, so each shot's run is solved against the boundary
        it had before and plays exactly as it did. The half facing the new gap
        keeps the handle Blender just derived for its new neighbour, frozen
        with it rather than left auto: an auto half keeps its key in Blender's
        handle solve, and AUTO_CLAMPED's overshoot guard re-aligned the frozen
        half to it (measured: a carried sample's 0.500 came back 0.444, the
        shot 0.025 off). A seam the split did not move (an AUTO_CLAMPED
        extremum) keeps its types.
        """
        ltype, rtype, loff, roff = handles
        try:
            copy = cls._key_at(fc, frame)
            if copy is None:
                return
            copy.handle_left_type, copy.handle_right_type = ltype, rtype
            co = copy.co
            if ltype not in _DERIVED_HANDLES:
                copy.handle_left = (co[0] + loff[0], co[1] + loff[1])
            if rtype not in _DERIVED_HANDLES:
                copy.handle_right = (co[0] + roff[0], co[1] + roff[1])
            fc.update()
            facing = []
            if rtype in _DERIVED_HANDLES:
                facing.append((frame, "handle_right", roff))
            if shared and ltype in _DERIVED_HANDLES:
                facing.append((original, "handle_left", loff))
            if not any(cls._handle_moved(fc, t, side, off) for t, side, off in facing):
                return
            for t, side, off in facing:
                kp = cls._key_at(fc, t)
                if kp is None:
                    continue
                kp.handle_left_type = kp.handle_right_type = "FREE"
                setattr(kp, side, (kp.co[0] + off[0], kp.co[1] + off[1]))
            fc.update()
        except (RuntimeError, TypeError, ValueError, AttributeError):
            pass  # a locked or linked curve keeps what the split gave it

    @classmethod
    def _handle_moved(cls, fc, t: float, side: str, offset) -> bool:
        """True when *side* of *fc*'s key at *t* no longer sits at *offset*."""
        kp = cls._key_at(fc, t)
        if kp is None:
            return False
        handle = getattr(kp, side)
        return (
            abs(handle[0] - kp.co[0] - offset[0]) > _HANDLE_MOVED_TOL
            or abs(handle[1] - kp.co[1] - offset[1]) > _HANDLE_MOVED_TOL
        )

    @classmethod
    def _insert_in_place(cls, fc, frame: float, value: float):
        """``fc.keyframe_points.insert`` without reshaping the neighbours.

        Blender inserts INTO the segment: it subdivides the bezier there so
        the curve keeps its shape, shortening the facing handle of the key on
        either side. A derived or vector handle is re-derived on the next
        update anyway, but a FREE or ALIGNED one keeps the cut -- measured: a
        split left the following shot's second key with a left handle 4.30
        long instead of 6.67, and the shot played 0.431 off. The shot system
        inserts a pose, not a subdivision (Maya's ``setKeyframe`` leaves a
        fixed tangent alone too), so those handles are put back.

        Returns the new keyframe point.
        """
        times = AnimUtils.key_times(fc)
        i0, i1 = AnimUtils.window_indices(times, frame - _SLOP, frame + _SLOP)
        kps = fc.keyframe_points
        kept = []
        if i0 > 0 and kps[i0 - 1].handle_right_type in _POSITIONED_HANDLES:
            kept.append(
                (times[i0 - 1], "handle_right", tuple(kps[i0 - 1].handle_right))
            )
        if i1 < len(times) and kps[i1].handle_left_type in _POSITIONED_HANDLES:
            kept.append((times[i1], "handle_left", tuple(kps[i1].handle_left)))
        kp = kps.insert(frame, value)
        for t, side, point in kept:
            neighbour = cls._key_at(fc, t)
            if neighbour is not None:
                setattr(neighbour, side, point)
        return kp

    @classmethod
    def _key_at(cls, fc, t: float, eps: float = _SLOP):
        """The keyframe point of *fc* within *eps* of *t*, or ``None``."""
        i = cls._key_index_at(fc, t, eps)
        return None if i is None else fc.keyframe_points[i]

    def _plan_curves(self, plan) -> list:
        """``[(ledger key, fcurve, [time, ...]), ...]`` for every curve *plan* moves."""
        names: set = set()
        for shot_id, move in plan.moves.items():
            if not move.moves:
                continue
            shot = self.shot_by_id(shot_id)
            if shot is not None:
                names.update(self._shot_nodes(shot))
        out: list = []
        for name, fc in self._named_fcurves(sorted(names)):
            times = AnimUtils.key_times(fc)
            if times:
                key = _ShotSequencerInternal._fc_key(name, fc)
                out.append((key, fc, sorted(times)))
        return out

    def _reconcile_boundaries(self, plan, retimes=()):
        """Keep fencepost samples whole across boundaries *plan* changes.

        Contiguous shots share one sample — the preceding shot's closing
        pose IS the following shot's opening pose, on the same frame — so a
        plan that changes a gap has to split that sample in two or merge two
        into one.  A split captures the pose before anything moves and
        re-keys it at the following shot's new start (only on curves that
        shot animates past the boundary), with the handles it had while
        shared (:meth:`_hold_split_handles`); a merge cuts the loser so the
        destination is clear, unless the two poses disagree, in which case
        the operation is refused (:class:`ShotBoundaryConflict`) before it
        writes anything.  Mirrors mayatk — see that docstring for the full
        rationale.

        Assumes membership is already complete (the back-fill runs
        first): a collision is predicted for every key inside a moving
        window, and that only matches what the writer does because the
        writer moves each shot's OWN objects and every object keyed in
        the window has just been adopted into it.

        Returns a callable to invoke after the plan has been applied.
        """
        from pythontk import ShotBoundaryConflict

        def _noop():
            return None

        windows = ShotPlanner.move_windows(plan)
        if not windows:
            return _noop

        # ---- merges: detect every conflict BEFORE cutting anything -------
        # *retimes* names the gaps a later stage RESCALES into their new width
        # (mirror of mayatk): a key strictly inside one lands strictly inside
        # the new gap, disjoint from every shot, so it cannot collide with a
        # shot's sample -- analysed with the rest, a shrinking gap's key read
        # as landing on the next shot and refused the respace.  Open intervals:
        # a shot's own bookend ON a bound is still analysed.
        deferred = [(g.lo, g.hi) for g in retimes]
        conflicts: list = []
        losers: list = []
        for key, fc, times in self._plan_curves(plan):
            times = [t for t in times if not any(lo < t < hi for lo, hi in deferred)]
            for dest, movers, still in ShotPlanner.key_collisions(windows, times):
                vals = {}
                for t in movers + still:
                    kp = self._key_at(fc, t)
                    if kp is not None:
                        vals[t] = float(kp.co[1])
                if not vals:
                    continue
                if max(vals.values()) - min(vals.values()) > _POSE_TOL:
                    label = f"{getattr(fc.id_data, 'name', '?')}.{fc.data_path}"
                    conflicts.append((label, float(dest), sorted(vals.values())))
                else:  # lossless: keep one mover, clear what it lands on
                    losers.extend((fc, t, key) for t in movers[1:] + still)
        if conflicts:
            raise ShotBoundaryConflict(conflicts)

        # ---- splits: capture while the shared sample still exists ---------
        captures: list = []
        for prev_id, shot_id, boundary, new_start in ShotPlanner.boundary_splits(
            self.store, plan
        ):
            shot = self.shot_by_id(shot_id)
            prev_shot = self.shot_by_id(prev_id)
            if shot is None or prev_shot is None:
                continue
            # The shared sample is the preceding shot's, so it moves with that
            # shot's content -- by its delta, or not at all (mirror of mayatk).
            prev_move = plan.moves.get(prev_id)
            prev_delta = (
                prev_move.delta if prev_move is not None and prev_move.moves else 0.0
            )
            for obj_name, fc in self._named_fcurves(self._shot_nodes(shot)):
                kp = self._key_at(fc, boundary)
                if kp is None:
                    continue  # this curve has no pose on the shared sample
                times = AnimUtils.key_times(fc) or []
                if not any(boundary + _SLOP < t <= shot.end + _SLOP for t in times):
                    continue  # the following shot does not animate this curve
                # Shared only where the PRECEDING shot animates this curve
                # too.  Where it does not, the sample was never a shared
                # fencepost — it is the following shot's opening pose alone,
                # so it travels with that shot instead of being duplicated
                # and left behind on a curve its neighbour has no stake in.
                shared = any(
                    prev_shot.start - _SLOP <= t < boundary - _SLOP for t in times
                )
                key = _ShotSequencerInternal._fc_key(obj_name, fc)
                key_t = float(kp.co[0])
                if shared and self.ledger.owns_key(key, key_t):
                    # It stays the preceding shot's closing pose, so a claim on
                    # it serves THAT shot's end from here (mirror of mayatk's).
                    self.ledger.release_key(key, key_t)
                    self.ledger.record_key(key, key_t, prev_id, "end")
                captures.append(
                    (
                        fc,
                        float(new_start),
                        float(kp.co[1]),
                        kp.interpolation,
                        shared,
                        key,
                        shot_id,
                        key_t + prev_delta,
                        self._handle_snapshot(kp),
                        # A copy is the system's; a carried sample stays whose
                        # it was (mirror of mayatk's).
                        shared or self.ledger.owns_key(key, key_t),
                    )
                )
                # A carried sample is NOT cut here with the merge losers: it
                # rides the move and leaves its old frame only once the new
                # start holds its pose (``_finish`` / ``_cut_carried_sample``,
                # mirror of mayatk) -- cut first, a failed re-key (a locked
                # curve) lost the opening pose outright.

        # Never empty a curve: keep at least one key so the fcurve survives.
        # A cut key's claims go with it: the move remaps only the keys it
        # finds, so a claim left on the frame is inherited by whatever lands
        # there next (mirror of mayatk's).
        for fc, t, key in losers:
            kp = self._key_at(fc, t)
            if kp is not None and len(fc.keyframe_points) > 1:
                try:
                    fc.keyframe_points.remove(kp)
                    fc.update()
                except RuntimeError:
                    continue  # locked or library-linked curve — leave it as it was
                self.ledger.release(key, t)

        if not captures:
            return _noop

        led = self.ledger

        def _finish():
            for (
                fc,
                frame,
                value,
                interp,
                shared,
                key,
                owner,
                original,
                handles,
                claim,
            ) in captures:
                keyed = self._key_at(fc, frame) is not None  # already landed
                if not keyed:
                    try:
                        kp = self._insert_in_place(fc, frame, value)
                        kp.interpolation = interp
                        fc.update()
                    except (RuntimeError, TypeError, ValueError):
                        pass  # locked/linked curve — the move still stands
                    else:
                        keyed = True
                        # Claimed for the shot bound it opens, so it follows
                        # that bound from here rather than being left behind
                        # by it -- unless it is the animator's own pose,
                        # carried.
                        if claim:
                            led.record_key(key, frame, owner, "start")
                        self._hold_split_handles(fc, frame, original, handles, shared)
                if not shared and keyed:
                    # Only now does a carried sample leave its old frame, and
                    # only once the new start holds a pose (mirror of mayatk).
                    self._cut_carried_sample(fc, original, value, led, key)

        return _finish

    def _cut_carried_sample(self, fc, t: float, value: float, ledger, key) -> None:
        """Cut the carried seam sample left at *t* once its re-key is down.

        Only a key still holding the carried pose (*value*) is cut -- anything
        else there now is not the sample -- and never a curve's last key.  Its
        claims go with it, as with every key the system cuts (mirror of
        mayatk's ``_cut_carried_sample``).
        """
        kp = self._key_at(fc, t)
        if kp is None or abs(float(kp.co[1]) - value) > _POSE_TOL:
            return
        if len(fc.keyframe_points) <= 1:
            return
        landed = float(kp.co[0])
        try:
            fc.keyframe_points.remove(kp)
            fc.update()
        except RuntimeError:
            return  # locked or library-linked curve — leave it as it was
        if ledger is not None:
            ledger.release(key, landed)

    def _apply_plan(self, plan, retime_gaps: bool = False) -> None:
        """Commit *plan* with the Blender key + audio writers.

        Every envelope moves the scene's whole keyed content (see
        :meth:`_content_objects`) so no shot moves while leaving part of its
        animation behind -- and no member list is written to -- and shared
        samples are reconciled around the write (see
        :meth:`_reconcile_boundaries`).

        *retime_gaps* (``respace`` asks for it) adds mayatk's two stages for a
        plan that changes any GAP's width, since a rigid move is only lossless
        while every gap keeps its width:

        1. every shot is PINNED -- a key on both of its bounds, inserted so the
           curve plays exactly as before (:meth:`_pin_shot_bounds`) -- so
           nothing outside a shot can change what plays inside it;
        2. each changed gap's content is RETIMED into its new width
           (:meth:`_retime_gaps`), shrinking gaps before the moves and growing
           ones after them.

        Both are no-ops for a pure translation.  The order is mayatk's and is
        load-bearing: the pin is lossless so it runs before the boundary
        check (and is what lets the check see a collapsed gap's conflict at
        all); the retime is not, so it runs after the check, which refuses a
        collapse before anything is written.
        """
        retimes = ShotPlanner.plan_gap_retimes(self.store, plan) if retime_gaps else []
        content = self._content_objects()
        if content and retimes:
            bound_owner: dict = {}
            for shot in self.store.sorted_shots():
                bound_owner.setdefault(float(shot.start), (shot.shot_id, "start"))
                bound_owner.setdefault(float(shot.end), (shot.shot_id, "end"))
            # Claimed as inserted: a pin is the system's own sample, so it is
            # carried with its bound (or cleaned up), never left behind.
            for name, fc, frame in self._pin_shot_bounds(content):
                owner, edge = bound_owner.get(float(frame), (-1, ""))
                self.ledger.record_key(
                    _ShotSequencerInternal._fc_key(name, fc), frame, owner, edge
                )
        finish = self._reconcile_boundaries(plan, retimes)
        if retimes:
            self._retime_gaps(retimes, content, after_move=False)
        ShotApply.apply(
            plan,
            self.store,
            move_keys=self._move_keys,
            shift_audio=self._shift_audio_envelope,
            objects_for=(lambda _sid: content) if content else None,
        )
        finish()
        if retimes:
            self._retime_gaps(retimes, content, after_move=True)

    def _pin_shot_bounds(self, content) -> list:
        """Give every shot a key on both of its bounds, changing nothing.

        Mirror of mayatk's ``ShotApply.pin_shot_bounds``: a shot's content is
        only its own while a key sits on each end of it -- otherwise the
        segment spanning a bound is shared with the other side, and moving
        that neighbour reshapes frames that never moved.  Every bound of every
        shot (mayatk's: the gap hold that follows steps each gap's last key,
        and it is the key on the NEXT shot's start that stops the hold).
        Shape-preserving and idempotent (:meth:`_insert_shape_keys`).

        Returns ``[(object name, fcurve, frame), ...]`` for the keys inserted.
        """
        bounds = sorted(
            {float(b) for shot in self.store.shots for b in (shot.start, shot.end)}
        )
        pinned: list = []
        if not bounds:
            return pinned
        for name, fc in self._named_fcurves(content):
            times = AnimUtils.key_times(fc)
            if len(times) < 2 or not any(times[0] < b < times[-1] for b in bounds):
                # No bound crosses it: the curve moves rigidly with one shot,
                # or scales whole inside one gap -- nothing re-derives, so it
                # keeps its auto handles.
                continue
            pinned.extend((name, fc, t) for t in self._insert_shape_keys(fc, bounds))
        return pinned

    @staticmethod
    def _freeze_derived_handles(fc, tol: float = 1e-6) -> int:
        """Turn *fc*'s derived handles into FREE ones where they already sit.

        The Blender form of mayatk's ``_hold_interior_tangents`` for an edit
        that changes the distance between keys (a gap retime): an AUTO /
        AUTO_CLAMPED / VECTOR handle is re-derived from its neighbours, so a
        bound key's handle follows the gap key beside it as that key is
        retimed, and the shot it bounds plays differently.  Under the default
        ``CONT_ACCEL`` smoothing a run of auto keys is solved TOGETHER, so
        freezing only the keys next to a bound would re-solve the rest of the
        run -- the whole curve is frozen, which leaves it playing exactly as
        it did (every handle stays where it was computed).  Called by
        :meth:`_insert_shape_keys`, so only for a curve a shot bound crosses.
        Returns the number of points frozen.
        """
        derived = _DERIVED_HANDLES + ("VECTOR",)
        kps = fc.keyframe_points
        targets = [
            (i, tuple(kp.handle_left), tuple(kp.handle_right))
            for i, kp in enumerate(kps)
            if kp.handle_left_type in derived or kp.handle_right_type in derived
        ]
        if not targets:
            return 0
        for i, _hl, _hr in targets:
            kps[i].handle_left_type = kps[i].handle_right_type = "FREE"
        for i, hl, hr in targets:
            kps[i].handle_left, kps[i].handle_right = hl, hr
        fc.update()
        return len(targets)

    #: Interpolations a key can be inserted INTO without changing the curve:
    #: a bezier splits exactly (de Casteljau), a straight line trivially.  An
    #: eased segment (SINE, BOUNCE, ...) is shaped relative to its own two
    #: keys and cannot be split, so it is left unpinned.
    _SPLITTABLE = ("BEZIER", "LINEAR")

    @classmethod
    def _insert_shape_keys(cls, fc, times, tol: float = 1e-4) -> list:
        """Insert keys at *times* WITHOUT changing what *fc* evaluates to.

        The twin of mayatk's ``AnimUtils.insert_keys`` (Maya's ``setKeyframe
        -insert``), under its rules: only times strictly inside the curve's
        key range (outside, the curve holds -- no shape to preserve), never
        on an existing key (idempotent), never inside a HOLD (a segment that
        plays one value end to end: a step, or equal values with level facing
        handles -- a key planted there is clutter to carry and explain).

        Exact because nothing re-derives: the curve's derived handles are
        frozen where they sit first (:meth:`_freeze_derived_handles` -- which
        a gap retime needs anyway, a bound key's AUTO handle otherwise
        following the retimed key beside it), and a bezier segment is then
        split exactly (:meth:`_split_segment`).

        Returns the frames inserted at.
        """
        if len(fc.keyframe_points) < 2:
            return []
        cls._freeze_derived_handles(fc)
        times_now = AnimUtils.key_times(fc)
        first, last = times_now[0], times_now[-1]
        kps = fc.keyframe_points
        inserted: list = []
        for t in sorted({float(x) for x in times}):
            if not first + tol < t < last - tol:
                continue
            times_now = AnimUtils.key_times(fc)
            if any(abs(t - k) <= tol for k in times_now):
                continue
            i = max(j for j, k in enumerate(times_now) if k < t)
            a, b = kps[i], kps[i + 1]
            if a.interpolation not in cls._SPLITTABLE or cls._is_hold(a, b, tol):
                continue
            if cls._split_segment(fc, i, t, tol):
                inserted.append(t)
        if inserted:
            fc.update()
        return inserted

    @staticmethod
    def _is_hold(a, b, tol: float) -> bool:
        """True when the segment from point *a* to *b* plays one value."""
        if a.interpolation == "CONSTANT":
            return True
        if abs(a.co[1] - b.co[1]) > tol:
            return False
        if a.interpolation == "LINEAR":
            return True
        return (
            abs(a.handle_right[1] - a.co[1]) <= tol
            and abs(b.handle_left[1] - b.co[1]) <= tol
        )

    @classmethod
    def _split_segment(cls, fc, i: int, t: float, tol: float) -> bool:
        """Insert a key at *t* into the segment after point *i*, exactly.

        For a curve whose handles are FREE (:meth:`_insert_shape_keys` freezes
        them): the new key and the two neighbours' facing handles are written
        where the de Casteljau split puts them.  Returns ``False`` (inserting
        nothing) when that split would not reproduce the curve's own value at
        *t* -- the segment is shaped by something this cannot model, and a pin
        that changed the curve would defeat its purpose.
        """
        kps = fc.keyframe_points
        a, b = kps[i], kps[i + 1]
        if a.interpolation == "LINEAR":
            kp = kps.insert(t, fc.evaluate(t))
            kp.interpolation = "LINEAR"
            kp.handle_left_type = kp.handle_right_type = "VECTOR"
            return True
        p0, p1 = tuple(a.co), tuple(a.handle_right)
        p2, p3 = tuple(b.handle_left), tuple(b.co)
        u = cls._bezier_param_at(p0[0], p1[0], p2[0], p3[0], t)
        if u is None:
            return False

        def lerp(m, n):
            return (m[0] + (n[0] - m[0]) * u, m[1] + (n[1] - m[1]) * u)

        q01, q12, q23 = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
        r0, r1 = lerp(q01, q12), lerp(q12, q23)
        split = lerp(r0, r1)  # left half P0 q01 r0 S, right half S r1 q23 P3
        if abs(split[1] - fc.evaluate(t)) > max(tol, 1e-3):
            return False
        a_t, b_t = float(p0[0]), float(p3[0])
        kp = kps.insert(t, split[1])  # stales a / b: re-found by time below
        kp.interpolation = "BEZIER"
        kp.handle_left_type = kp.handle_right_type = "FREE"
        kp.handle_left, kp.handle_right = r0, r1
        # Blender subdivides on insert itself; the exact split is written
        # either way.
        cls._key_at(fc, a_t).handle_right = q01
        cls._key_at(fc, b_t).handle_left = q23
        return True

    @staticmethod
    def _bezier_param_at(x0, x1, x2, x3, t, iterations: int = 60):
        """The parameter ``u`` in ``[0, 1]`` where the cubic's x equals *t*.

        Bisection on the x polynomial, which is monotonic for a well-formed
        fcurve segment; ``None`` when *t* is outside ``[x0, x3]``.
        """
        if not (min(x0, x3) - 1e-9 <= t <= max(x0, x3) + 1e-9):
            return None

        def x_at(u):
            v = 1.0 - u
            return (
                v * v * v * x0
                + 3 * v * v * u * x1
                + 3 * v * u * u * x2
                + u * u * u * x3
            )

        lo, hi = 0.0, 1.0
        for _ in range(iterations):
            mid = 0.5 * (lo + hi)
            if x_at(mid) < t:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def _retime_gaps(self, retimes, content, after_move: bool) -> int:
        """Scale each changed gap's content into the width the plan gives it.

        Mirror of mayatk's ``ShotApply.retime_gaps``: called twice around the
        shot moves -- SHRINKING gaps before them (the content compresses
        toward the gap's left edge, inside the gap it is already in) and
        GROWING gaps after them (by then the content has travelled with the
        preceding shot and the following one has opened the room).  Only the
        keys strictly inside the gap move, so a shot's own bookend is never
        touched, and their claims travel with them.  A gap collapsed to zero
        width is REFUSED, not applied: its keys stay where they are and the
        count is reported -- stacking them on the bound, or cutting them,
        would discard the animator's keys.  *content* is the scene's keyed
        content, not a shot's member list (mayatk's docstring has why).

        Returns the number of curves moved.
        """
        named = self._named_fcurves(content)
        moved = stranded = 0
        for gap in retimes:
            if gap.grows is not after_move:
                continue
            lo = gap.lo + (gap.left_delta if after_move else 0.0)
            hi = lo + gap.width
            wlo, whi = lo + _SLOP, hi - _SLOP
            if whi <= wlo:
                continue
            scale = gap.scale
            for name, fc in named:
                times = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(times, wlo, whi)
                if i1 <= i0:
                    continue
                if scale <= 0.0:
                    stranded += 1
                    continue
                inside = list(times[i0:i1])
                AnimUtils.remap_keys_in_window(
                    fc, wlo, whi, lo, hi, lo, lo + (hi - lo) * scale
                )
                self.ledger.remap(
                    _ShotSequencerInternal._fc_key(name, fc),
                    [(t, lo + (t - lo) * scale) for t in inside],
                )
                moved += 1
        if stranded:
            _log.warning(
                "Respace: %d curve(s) have keys in a gap that collapsed to zero "
                "width. They were left where they are rather than cut -- use a "
                "gap of at least 1 frame to keep them between the shots.",
                stranded,
            )
        return moved

    #: Frames a displaced key is pushed clear of the arriving cluster; one
    #: frame is the quantum of an animation timeline (mirrors mayatk).
    _PUSH_CLEARANCE = 1.0

    @classmethod
    def _is_contiguous_run(cls, crv, times: list, eps: float = 1e-3) -> bool:
        """True when *times* is EVERY point of *crv* between its first and last.

        A contiguous run is a clip -- a continuous region of the timeline.  A
        sparse set is hand-picked key dots with others deliberately left
        between them, occupying discrete frames only.  (Mirrors mayatk.)
        """
        if not times:
            return False
        kt = AnimUtils.key_times(crv)
        idx: set = set()
        for t in times:
            i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
            idx.update(range(i0, i1))
        if not idx:
            return False
        return len(idx) == max(idx) - min(idx) + 1

    @classmethod
    def _absorb_holds(cls, crv, times: list, eps: float, ledger, ledger_key: str):
        """Remove every flat HOLD at *times*; return the times that stayed.

        A hold carries no pose -- both neighbours already sit at its value --
        so removing it cannot change what the curve plays, and the frames
        between two clips are exactly where holds pile up.  Never removes
        below two points.
        """
        kept = []
        removed = False
        for t in sorted(times, reverse=True):  # highest first: removal renumbers
            kt = AnimUtils.key_times(crv)
            i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
            if i0 >= i1:
                continue  # already gone; keeping it would be a phantom
            idx = i0
            if len(crv.keyframe_points) > 2 and cls._sample_is_redundant(crv, idx):
                crv.keyframe_points.remove(crv.keyframe_points[idx])
                if ledger is not None and ledger_key:
                    ledger.release(ledger_key, t)
                removed = True
            else:
                kept.append(t)
        if removed:
            crv.update()  # removals re-sort the point list
        kept.sort()
        return kept

    @classmethod
    def _clear_destination(
        cls,
        crv,
        times: list,
        delta: float,
        eps: float = 1e-3,
        ledger=None,
        ledger_key: str = "",
    ) -> None:
        """Make room on *crv* for ``times + delta``, without losing a pose.

        Mirror of mayatk's.  A cluster dropped on occupied frames used to land
        INTERLEAVED with what was already there -- the arriving motion and the
        old poses sharing one span, playing as neither.  Now the landing zone
        is cleared first: flat HOLDS are absorbed, and whatever carries a pose
        is PUSHED clear -- in the direction of travel, by ONE delta, as a
        single rigid block grown to a fixpoint first, so the displaced
        material keeps its timing instead of being torn where it straddled the
        edge of the landing zone -- or, when it is one key whose pose already
        waits where the push would lay it, merged into that key.

        CONTIGUOUS RUNS ONLY -- see the mayatk twin.  A sparse selection
        occupies discrete frames, so the span between its first and last
        arrival is not a region a stationary key can block.
        """
        if not cls._is_contiguous_run(crv, times, eps):
            return
        kt = AnimUtils.key_times(crv)
        if not kt:
            return
        moving = sorted(times)
        moving_idx: set = set()
        for t in moving:
            i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
            moving_idx.update(range(i0, i1))
        stationary = [kt[i] for i in range(len(kt)) if i not in moving_idx]
        if not stationary:
            return

        lo = moving[0] + delta
        hi = moving[-1] + delta
        blocking = [t for t in stationary if lo - eps <= t <= hi + eps]
        if not blocking:
            return

        displaced = cls._absorb_holds(crv, blocking, eps, ledger, ledger_key)
        if not displaced:
            crv.update()
            return

        # One delta for the whole block, decided by the member that has to
        # travel furthest to clear the arrival.  Growing the block never
        # changes it: a leftward push only reaches EARLIER keys, a rightward
        # one only later, so the deciding member is already in.
        if delta < 0:
            push = lo - cls._PUSH_CLEARANCE - displaced[-1]
        else:
            push = hi + cls._PUSH_CLEARANCE - displaced[0]
        if abs(push) < eps:
            crv.update()
            return

        # A LONE displaced key the push would lay exactly onto a stationary key
        # of the SAME value merges into it instead (mirror of mayatk): that
        # pose already waits there, while pushing both shifts what follows --
        # a key dragged onto a contiguous seam pushed the old seam pose onto
        # the neighbour's identical opening pose, and that pose a frame late.
        # Alone, because the rest of a block are still pushed by the same
        # delta, PAST the key the merged one joined: 50 (5), 55 (8) ahead of
        # 56 (5) came out 56 (5), 61 (8), the return to 5 after the 8 gone.
        if len(displaced) == 1 and len(crv.keyframe_points) > 2:
            d = displaced[0]
            idx = cls._key_index_at(crv, d, eps)
            if (
                idx is not None
                and any(abs(s - (d + push)) <= eps for s in stationary)
                and cls._same_value(crv, d, d + push, eps)
            ):
                crv.keyframe_points.remove(crv.keyframe_points[idx])
                crv.update()
                if ledger is not None and ledger_key:
                    ledger.release(ledger_key, d)
                return

        # Grow to a fixpoint: anything the block would land on travels WITH it.
        # Seeded with every candidate already CONSIDERED, not just the
        # survivors: an absorbed key is still in the `stationary` snapshot, and
        # re-offering it would be adopted into the block as a phantom.
        taken = set(blocking)
        for _ in range(len(stationary)):
            d_lo = displaced[0] + push - eps
            d_hi = displaced[-1] + push + eps
            reached = [t for t in stationary if t not in taken and d_lo <= t <= d_hi]
            if not reached:
                break
            taken.update(reached)
            kept = cls._absorb_holds(crv, reached, eps, ledger, ledger_key)
            if kept:
                displaced.extend(kept)
                displaced.sort()

        cls._commit_curve_move(
            crv, displaced, push, eps=eps, ledger=ledger, ledger_key=ledger_key
        )

    @classmethod
    def move_curve_keys(
        cls,
        crv,
        times: list,
        delta: float,
        plug=None,
        eps: float = 1e-3,
        ledger=None,
        ledger_key: str = "",
    ) -> None:
        """Shift the keys of fcurve *crv* at *times* by *delta* (handles travel too).

        Public for the same two consumers as mayatk's (shot moves and the
        clip-motion drag handler).  *plug* is accepted for signature parity
        (Maya needs the driven plug when ``cutKey`` deletes the curve node;
        a Blender fcurve survives with zero points).

        The landing zone is CLEARED first (:meth:`_clear_destination`): keys
        already there are absorbed when they are flat holds and pushed aside
        when they carry a pose.

        *ledger* + *ledger_key* carry the shot system's claims along with the
        keys.  Maya derives the key from the animCurve node name; an fcurve
        has no name, so the caller supplies it (:meth:`_fc_key`).

        NO twin of mayatk's ``_hold_interior_tangents`` here, deliberately.
        Over there a derived tangent is recomputed from the keys on both
        sides, so sliding a run away from what precedes it reshapes the run's
        own interior motion.  A Blender handle is stored ON its point and
        travels with it, so the same slide leaves the shape alone: measured
        across four curve shapes x AUTO/AUTO_CLAMPED, seven came through
        byte-identical and the eighth moved by 0.016 on a 20-unit curve --
        residue from Blender re-deriving the ADJACENT point's handle, which
        pinning the boundary cannot reach (measured 0.01608 held vs 0.016076
        free).  Mirroring the guard would rewrite an AUTO_CLAMPED handle to
        FREE for no measurable gain, so it is not mirrored.
        """
        if not times or abs(delta) < _EPS:
            return
        cls._clear_destination(
            crv, times, delta, eps=eps, ledger=ledger, ledger_key=ledger_key
        )
        cls._commit_curve_move(
            crv, times, delta, eps=eps, ledger=ledger, ledger_key=ledger_key
        )

    @classmethod
    def _commit_curve_move(
        cls,
        crv,
        times: list,
        delta: float,
        eps: float = 1e-3,
        ledger=None,
        ledger_key: str = "",
    ) -> None:
        """The raw shift, with the destination assumed already clear.

        Split out because the collision handling has to move keys too (that
        is what "push out of the way" is) and must not recurse into its own
        clearing pass.
        """
        if not times or abs(delta) < _EPS:
            return
        kt = AnimUtils.key_times(crv)
        idx: set = set()
        for t in times:
            i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
            idx.update(range(i0, i1))
        if not idx:
            return
        moved_times = [kt[i] for i in sorted(idx)]
        lo_i, hi_i = min(idx), max(idx)
        if len(idx) == hi_i - lo_i + 1:  # cf. _is_contiguous_run
            # Contiguous run — one bulk window shift.
            AnimUtils.shift_keys_in_window(crv, kt[lo_i], kt[hi_i], delta)
        else:
            # Sparse selection inside a span: move the named points only.
            # ``_clear_destination`` leaves a sparse set alone, so a point
            # landing on a stationary key replaces it below.
            moved_pts = [crv.keyframe_points[i] for i in sorted(idx)]
            for kp in moved_pts:
                kp.co[0] += delta
                kp.handle_left[0] += delta
                kp.handle_right[0] += delta
            cls._overwrite_landed(crv, moved_pts, ledger, ledger_key)
            crv.update()
        if ledger is not None and ledger_key:
            ledger.remap(ledger_key, [(t, t + delta) for t in moved_times])

    @staticmethod
    def _overwrite_landed(crv, moved, ledger=None, ledger_key: str = "") -> int:
        """Remove the stationary points of *crv* that a point in *moved* landed on.

        mayatk's sparse move recreates each key with ``setKeyframe``, which
        OVERWRITES a key already on the frame (the Graph Editor's behaviour);
        a direct ``co`` write here would otherwise leave two points on one
        frame.  The replaced key's claims go with it.  Returns the count.
        """
        removed = AnimUtils._merge_onto_moved(crv, moved, eps=_SLOP)
        if ledger is not None and ledger_key:
            for t in removed:
                ledger.release_key(ledger_key, t)
        return len(removed)

    @classmethod
    def recreate_curve_keys(
        cls,
        crv,
        pairs: list,
        plug=None,
        eps: float = 1e-3,
        ledger=None,
        ledger_key: str = "",
    ) -> None:
        """Move the keys named by ``[(old_time, new_time), ...]`` on *crv*.

        Two-pass (match against PRE-move positions, then write) so a later pair
        can never grab a key an earlier pair just moved; the point's
        interpolation and handles travel with it — no cut-and-recreate.
        """
        pairs = sorted(p for p in pairs if abs(p[1] - p[0]) >= _EPS)
        if not pairs:
            return
        kt = AnimUtils.key_times(crv)
        targets = []
        claimed: set = set()
        for old_t, new_t in pairs:
            i0, i1 = AnimUtils.window_indices(kt, old_t - eps, old_t + eps)
            for i in range(i0, i1):
                if i in claimed:
                    continue
                claimed.add(i)
                targets.append((crv.keyframe_points[i], new_t))
                break
        for kp, new_t in targets:
            d = new_t - kp.co[0]
            kp.co[0] = new_t
            kp.handle_left[0] += d
            kp.handle_right[0] += d
        if targets:
            cls._overwrite_landed(crv, [kp for kp, _t in targets], ledger, ledger_key)
            crv.update()
            if ledger is not None and ledger_key:
                ledger.remap(ledger_key, pairs)

    # ---- per-object keyframe editing -------------------------------------

    def _fcurves_of(self, obj: str, attr: Optional[str]) -> list:
        """The fcurves driving ``obj.attr``, or every one on *obj*."""
        o = _ShotSequencerInternal._object(obj)
        if o is None:
            return []
        if attr:
            from blendertk.anim_utils.shots.shot_sequencer.clip_motion import (
                ClipMotionMixin,
            )

            return list(ClipMotionMixin.curves_for_attr(obj, attr))
        return list(BlenderShotStore.iter_action_fcurves(o))

    def _animator_key_times(self, obj_name: str, fc, window=None) -> List[float]:
        """*fc*'s key times that are the animator's own: the samples the
        system planted for a shot bound (the ledger's claims, keyed by
        :meth:`_ShotSequencerInternal._fc_key`) are left out.  *window* is an
        optional ``(lo, hi)`` range.  Mirrors mayatk, whose docstring carries
        the production case (a pinned end that would not trim).
        """
        kt = AnimUtils.key_times(fc)
        if window is not None:
            i0, i1 = AnimUtils.window_indices(kt, *window)
            kt = kt[i0:i1]
        system = self.ledger.key_times(_ShotSequencerInternal._fc_key(obj_name, fc))
        return [t for t in kt if not any(abs(t - k) <= self.ledger.eps for k in system)]

    def move_attribute_keys(
        self,
        obj: str,
        attr: Optional[str],
        delta: float,
        times: Optional[List[float]] = None,
        window: Optional[tuple] = None,
    ) -> int:
        """Shift keys of *obj* by *delta* frames -- one attribute's, or all.

        Mirror of mayatk's: the key-level primitive a key selection's Move to
        Shot sends.  *attr* narrows the fcurves to the channel labelled so
        (``curves_for_attr``); ``None`` takes every fcurve on the object.
        *times* names the keys outright (matched within a frame tolerance);
        otherwise every key inside *window* moves.  Every fcurve goes through
        :meth:`move_curve_keys`, so handles travel, the landing zone is
        cleared, and the ledger's claims ride along.

        Returns:
            The number of keys moved.
        """
        if abs(delta) < _EPS:
            return 0
        curves = self._fcurves_of(obj, attr)
        eps = 1e-3
        moved = 0
        for fc in curves:
            kt = AnimUtils.key_times(fc)
            pairs = []
            if times is not None:
                present = []
                for t in times:
                    i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
                    present.extend(kt[i0:i1])
                    pairs.extend((t, f) for f in kt[i0:i1])
            elif window is not None:
                i0, i1 = AnimUtils.window_indices(kt, window[0] - eps, window[1] + eps)
                present = list(kt[i0:i1])
            else:
                present = list(kt)
            if not present:
                continue
            # The curve can answer with a key a fraction of a frame from the
            # time the caller named (a near-duplicate left by an earlier move).
            # Moving THAT key by the caller's delta lands it the same fraction
            # off its target, and for a Move to Shot the target IS the
            # destination's first frame: the key ends up just before it, in the
            # gap, owned and drawn by nothing.  See mayatk's twin.
            fc_delta = delta
            if pairs:
                named_t, found_t = min(pairs, key=lambda p: p[1])
                fc_delta = delta + (named_t - found_t)
            self.move_curve_keys(
                fc,
                present,
                fc_delta,
                eps=eps,
                ledger=self.ledger,
                ledger_key=_ShotSequencerInternal._fc_key(obj, fc),
            )
            moved += len(present)
        return moved

    def move_stepped_keys(
        self,
        obj: str,
        old_time: float,
        new_time: float,
        attr_name: Optional[str] = None,
        eps: float = 1e-3,
    ) -> None:
        """Move the key(s) at *old_time* to *new_time*.

        A keyframe's interpolation travels with its point, so the "stepped"
        character is preserved automatically.  *attr_name* scopes the move to
        one channel — a ``translateX``-style label (via ``curves_for_attr``) or a
        ``data_path`` substring; omit it to move every fcurve with a key there.
        """
        delta = new_time - old_time
        if abs(delta) < _EPS:
            return
        o = _ShotSequencerInternal._object(obj)
        if o is None:
            return
        if attr_name:
            from blendertk.anim_utils.shots.shot_sequencer.clip_motion import (
                ClipMotionMixin,
            )

            curves = ClipMotionMixin.curves_for_attr(obj, attr_name)
        else:
            curves = list(BlenderShotStore.iter_action_fcurves(o))
        for fc in curves:
            self.move_curve_keys(
                fc,
                [old_time],
                delta,
                eps=eps,
                ledger=self.ledger,
                ledger_key=_ShotSequencerInternal._fc_key(obj, fc),
            )

    def _scale_keys(self, objects, old_start, old_end, new_start, new_end) -> None:
        """Linearly remap each object's keys in ``[old_start, old_end]`` onto ``[new_start, new_end]``.

        The Blender analogue of Maya's ``scaleKey``; bezier handles are remapped
        the same way so tangents scale with the clip.
        """
        span = old_end - old_start
        if abs(span) < _EPS:
            return
        scale = (new_end - new_start) / span
        if abs(scale - 1.0) < _EPS and abs(new_start - old_start) < _EPS:
            return

        lo, hi = old_start - _SLOP, old_end + _SLOP
        for name in objects:
            o = _ShotSequencerInternal._object(name)
            if o is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(o):
                # The claims ride with the keys (_retime_fcurve): mirror of
                # mayatk's scale_object_keys.
                _ShotSequencerInternal._retime_fcurve(
                    fc,
                    lo,
                    hi,
                    old_start,
                    old_end,
                    new_start,
                    new_end,
                    ledger=self.ledger,
                    key=_ShotSequencerInternal._fc_key(name, fc),
                )

    def scale_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale one object's keys from ``[old_start, old_end]`` into ``[new_start, new_end]``."""
        self._scale_keys([obj], old_start, old_end, new_start, new_end)

    # ---- system-authored edits (ledger-backed) ---------------------------
    #
    # Mirror of mayatk's contract: the two writes this system makes on the
    # animator's curves — a gap hold and a boundary sample — are claimed in
    # ``store.edit_ledger`` as they are made, so they can be RELEASED when
    # the boundary that justified them moves.  A hold the animator put in is
    # never claimed and therefore never taken back.

    def _gap_hold_seams(self) -> Dict[str, list]:
        """``{fc_key: [seam_time, ...]}`` — where gap holds belong now.

        The seam is the last key before the NEXT shot's start (envelope rule):
        a bounds-only shrink strands keys in the gap, and stepping the last
        key INSIDE the bounds while stranded keys interpolate beyond it puts a
        permanent hold on a mid-content key.

        A LIST per curve, not one time: shot objects are routinely shared, so
        one fcurve is commonly the seam of several gaps and every one of them
        has to hold.

        An fcurve with NO key inside the pre-gap shot is skipped entirely.
        Content is per OBJECT, so every fcurve on a keyed object reaches
        here -- including ones whose first key lands in the gap and whose
        motion runs on into the NEXT shot.  Such a curve has no pre-gap value
        to hold, and its first key is the next shot's lead-in, not this shot's
        overhang.  (Mirrors mayatk; see its twin for the production case.)
        """
        seams: Dict[str, list] = {}
        sorted_s = self.sorted_shots()
        if len(sorted_s) < 2 or _ShotSequencerInternal._scene() is None:
            return seams
        # The scene's CONTENT, not each shot's member list: a gap must hold
        # on every curve that crosses it, and membership is a motion label
        # -- an object posed once in a shot is not listed there, while its
        # curve still interpolates across the gap toward its next key.
        content = self._content_objects()
        for i in range(len(sorted_s) - 1):
            pre, nxt = sorted_s[i], sorted_s[i + 1]
            if nxt.start - pre.end < _EPS:
                continue
            lo, hi = pre.start - _SLOP, nxt.start - _SLOP
            for name in content:
                obj = _ShotSequencerInternal._object(name)
                if obj is None:
                    continue
                # Every fcurve, not just transforms — Maya steps every
                # animCurve on the object, so custom-prop and visibility
                # channels hold through the gap too.
                for fc in BlenderShotStore.iter_action_fcurves(obj):
                    times = AnimUtils.key_times(fc)
                    i0, i1 = AnimUtils.window_indices(times, lo, hi)
                    if i1 <= i0:
                        continue
                    if float(times[i0]) > pre.end + _SLOP:
                        continue  # starts inside the gap: lead-in, not overhang
                    last_t = float(times[i1 - 1])
                    key = _ShotSequencerInternal._fc_key(name, fc)
                    got = seams.setdefault(key, [])
                    # Two shots can share a seam; record it once so the claim
                    # count matches the number of held keys.
                    if not any(abs(last_t - t) <= _SLOP for t in got):
                        got.append(last_t)
        return seams

    def _release_gap_holds(self, seams: Dict[str, list]) -> int:
        """Undo every claimed hold *seams* no longer asks for.

        The CLAIM goes whatever the scene says, so a curve that has since been
        deleted or re-interpolated by hand cannot leave a permanent entry
        behind.  The WRITE is only taken back where the key is still there and
        still ``CONSTANT`` — an animator who changed it since owns it now.
        """
        led = self.ledger
        restored = 0
        for key in led.stepped_curves():
            fc = _ShotSequencerInternal._fcurve_for_key(key)
            if fc is None:
                led.forget_curve(key)
                continue
            want = seams.get(key, ())
            for t in led.step_times(key):
                if any(abs(t - w) <= _SLOP for w in want):
                    continue  # still a seam — the hold still belongs here
                types = led.release_step(key, t)
                if types is None:
                    continue
                idx = _ShotSequencerInternal._key_index_at(fc, t)
                if idx is None:
                    continue  # the key is gone; the claim went with it
                kp = fc.keyframe_points[idx]
                if kp.interpolation != "CONSTANT":
                    continue  # re-authored since — not ours to take back
                kp.interpolation = types[1] or "BEZIER"
                fc.update()
                restored += 1
        return restored

    def _apply_gap_holds(self, seams: Dict[str, list]) -> int:
        """Hold every seam that is not already held.

        A key that is ALREADY ``CONSTANT`` is left alone and not claimed: it
        is either this system's own hold from an earlier pass (already
        claimed) or the animator's, which must never be taken back.
        """
        led = self.ledger
        held = 0
        for key, times in seams.items():
            fc = _ShotSequencerInternal._fcurve_for_key(key)
            if fc is None:
                continue
            touched = False
            for t in times:
                idx = _ShotSequencerInternal._key_index_at(fc, t)
                if idx is None:
                    continue
                kp = fc.keyframe_points[idx]
                if kp.interpolation == "CONSTANT":
                    continue
                led.record_step(key, t, kp.easing, kp.interpolation)
                kp.interpolation = "CONSTANT"
                touched = True
                held += 1
            if touched:  # one re-evaluation per curve, not per key
                fc.update()
        return held

    @classmethod
    def _sample_is_redundant(cls, fc, idx: int, terminal: bool = True) -> bool:
        """True when removing point *idx* cannot change what *fc* plays.

        *terminal* admits the curve's first/last point to the test (see
        :meth:`_terminal_sample_is_redundant`); the marker scan turns it off
        -- a flat member's bookend on a bound is the animator's own mark to
        draw (mirror of mayatk).

        Two conditions, and equal values alone is NOT one of them:

        1. the point sits in a flat plateau — both immediate neighbours carry
           its value; and
        2. the segment its removal leaves behind is flat too.

        (2) is what a released boundary sample satisfies: it was created as a
        duplicate of the pose across the seam, on a curve that was already
        holding.  Anything else carries shape, and shape is never cut to tidy
        up — with BEZIER neighbours the middle point is what PINS the plateau,
        so absorbing it bows the surviving segment and recomputes both AUTO
        handles with it, silently editing a curve the drag never touched.

        A classmethod because :meth:`move_curve_keys` asks the same question
        of the keys a move is about to land on (mirrors mayatk).
        """
        pts = fc.keyframe_points
        if idx < 0 or idx >= len(pts) or len(pts) < 2:
            return False
        if idx == 0 or idx == len(pts) - 1:
            if not terminal:
                return False
            # No neighbour on one side.  The hold beyond a terminal point is
            # shape only while something can differ there: under CONSTANT
            # extrapolation the curve holds its terminal value forever, so a
            # terminal point that duplicates its one neighbour across a flat
            # span changes nothing by going.  Mirrors mayatk: this is where
            # the LAST shot's end samples ended up -- never redundant by the
            # plateau test, never cut, and once disowned they pinned every
            # trailing trim of the last shot.
            return cls._terminal_sample_is_redundant(fc, idx)
        prev, nxt = pts[idx - 1], pts[idx + 1]
        here = pts[idx].co[1]
        if abs(prev.co[1] - here) > _POSE_TOL or abs(nxt.co[1] - here) > _POSE_TOL:
            return False

        # (a) The surviving segment runs prev -> next and takes its shape from
        # PREV's interpolation, plus the two handles that face into it.
        if prev.interpolation not in _FLAT_SPAN_INTERPOLATIONS and not (
            abs(prev.handle_right[1] - prev.co[1]) <= _POSE_TOL
            and abs(nxt.handle_left[1] - nxt.co[1]) <= _POSE_TOL
        ):
            return False
        # (b) And no surviving point may be RESHAPED by the cut.
        return not (cls._reshaped_by_cut(prev) or cls._reshaped_by_cut(nxt))

    @staticmethod
    def _reshaped_by_cut(kp) -> bool:
        """True when a neighbour's derived handle would re-derive off level.

        mayatk's condition (b): a derived tangent is computed from the keys on
        BOTH sides of its own, so removing a neighbour re-computes it --
        including the half facing away from the cut, which is how the damage
        hid in production (a step out-tangent passed the span test while the
        spline in-tangent marched across four unrelated drags).  An AUTO /
        AUTO_CLAMPED handle is Blender's derived tangent; a level one is safe,
        since the neighbour it gains carries the removed point's own value.
        """
        for side, handle in (
            ("handle_left_type", "handle_left"),
            ("handle_right_type", "handle_right"),
        ):
            if (
                getattr(kp, side) in _DERIVED_HANDLES
                and abs(getattr(kp, handle)[1] - kp.co[1]) > _POSE_TOL
            ):
                return True
        return False

    @classmethod
    def _terminal_sample_is_redundant(cls, fc, idx: int) -> bool:
        """The first/last point case of :meth:`_sample_is_redundant`:
        CONSTANT extrapolation beyond it, its one neighbour carrying its
        value, and the span between them playing flat."""
        if getattr(fc, "extrapolation", "CONSTANT") != "CONSTANT":
            return False
        pts = fc.keyframe_points
        last = idx == len(pts) - 1
        here = pts[idx]
        neighbour = pts[idx - 1] if last else pts[idx + 1]
        if abs(neighbour.co[1] - here.co[1]) > _POSE_TOL:
            return False
        earlier, later = (neighbour, here) if last else (here, neighbour)
        if earlier.interpolation not in _FLAT_SPAN_INTERPOLATIONS and not (
            abs(earlier.handle_right[1] - earlier.co[1]) <= _POSE_TOL
            and abs(later.handle_left[1] - later.co[1]) <= _POSE_TOL
        ):
            return False
        # mayatk's condition (4): the surviving neighbour is not re-derived.
        return not cls._reshaped_by_cut(neighbour)

    def _reconcile_boundary_keys(
        self, bounds: Optional[Dict[int, tuple]] = None, follow: bool = True
    ) -> Tuple[int, int]:
        """Make every claimed boundary sample follow — or leave — its bound.

        Mirror of mayatk's: the sample follows a bound that moved, is dropped
        when the key is already gone, and is cut only where that is provably a
        no-op; otherwise it is disowned and left where it is.

        *bounds* (``{shot_id: (start, end)}``) narrows the pass to those shots
        and resolves their claims against the bounds given instead of the ones
        the store holds — the PENDING form
        (:meth:`_reconcile_pending_bounds`), for an edit that has not written
        its new bounds yet.

        *follow* ``False`` never MOVES a sample (the Ctrl edge drag: the bound
        moves and nothing else does -- a followed pin re-times its ramp, and
        can slide past another key); one whose bound moved is cut when
        provably redundant and disowned in place otherwise.

        Returns ``(moved, removed)``.
        """
        if _ShotSequencerInternal._scene() is None:
            return 0, 0
        led = self.ledger
        moved = removed = 0
        for key in led.keyed_curves():
            records = [
                rec
                for rec in led.key_records(key)
                if bounds is None or rec[1] in bounds
            ]
            if not records:
                continue  # nothing here belongs to the shots named in *bounds*
            fc = _ShotSequencerInternal._fcurve_for_key(key)
            if fc is None:
                if bounds is None:
                    led.forget_curve(key)
                continue
            for t, owner, edge in records:
                shot = self.shot_by_id(owner) if owner >= 0 else None
                bound = None
                if edge in ("start", "end"):
                    if bounds is not None:
                        bound = bounds[owner][0 if edge == "start" else 1]
                    elif shot is not None:
                        bound = shot.start if edge == "start" else shot.end
                # Gone first: a sample deleted ON its bound -- where the system
                # makes them -- would otherwise keep its claim for the next key
                # to land there (mirror of mayatk's).
                idx = _ShotSequencerInternal._key_index_at(fc, t)
                if idx is None:
                    led.release(key, t)  # the key is gone, and every claim with it
                    continue
                if bound is not None and abs(bound - t) <= _SLOP:
                    continue  # still on its bound
                occupied = (
                    bound is not None
                    and _ShotSequencerInternal._key_index_at(fc, bound) is not None
                )
                if follow and bound is not None and not occupied:
                    kp = fc.keyframe_points[idx]
                    d = bound - kp.co[0]
                    kp.co[0] = bound
                    kp.handle_left[0] += d
                    kp.handle_right[0] += d
                    fc.update()
                    led.remap(key, [(t, bound)])
                    moved += 1
                    continue
                if self._sample_is_redundant(fc, idx) and len(fc.keyframe_points) > 2:
                    try:
                        fc.keyframe_points.remove(fc.keyframe_points[idx])
                        fc.update()
                    except (RuntimeError, TypeError):
                        pass  # locked/linked curve — leave it as it was
                    else:
                        removed += 1
                        led.release(key, t)  # cut: every claim goes with it
                        continue
                led.release_key(key, t)  # kept: disowned; a hold on it stays
        return moved, removed

    # ---- shot lifecycle (delete / merge / split / pad) --------------------

    def _cut_shot_content(self, shot_id: int) -> int:
        """Delete every key inside *shot_id*'s owned window.

        The window comes from :meth:`_shot_envelope`, so a sample shared with
        a contiguous NEIGHBOUR stays with the neighbour that owns it.  Sound
        strips are out of scope: they are a separate store, and clearing whole
        strips would take more than the shot.

        Returns the number of fcurves keys were cut from.
        """
        shot = self.shot_by_id(shot_id)
        env = self._shot_envelope(shot_id)
        if shot is None or env is None or _ShotSequencerInternal._scene() is None:
            return 0
        lo, hi, lo_open, hi_closed = env
        # The LAST shot's envelope runs to +INF so its trailing content belongs
        # to it.  Right ownership for a delete too, but the sentinel itself
        # must not reach the key walk -- cap it the way the audio shifter does.
        if hi >= ShotPlanner.UNBOUNDED:
            hi = lo + 1.0e7
        window = (
            lo + _SLOP if lo_open else lo - _SLOP,
            hi + _SLOP if hi_closed else hi - _SLOP,
        )
        led = self.ledger
        cut = 0
        for name in list(shot.objects):
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                times = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(times, window[0], window[1])
                if i1 <= i0:
                    continue
                removed = 0
                for i in range(i1 - 1, i0 - 1, -1):
                    try:
                        fc.keyframe_points.remove(fc.keyframe_points[i])
                        removed += 1
                    except (RuntimeError, TypeError):
                        break  # locked/linked curve
                if not removed:
                    # Nothing came off (a locked curve): neither counted nor
                    # released, as mayatk skips a curve its cutKey refused --
                    # its keys still stand, and so do their claims.
                    continue
                fc.update()
                cut += 1
                key = _ShotSequencerInternal._fc_key(name, fc)
                led.release(key, window[0], window[1])
        return cut

    def scale_shot_keys(
        self,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Retime every key inside ``[old_start, old_end]`` into the new span.

        Acts on the scene's keyed CONTENT (:meth:`_content_objects`), not on
        a shot's member list: membership is a label, and a retime that read
        the list left every unlisted object's keys where they were.  Measured (mayatk)
        on the production assembly: a Shift-drag of "Step 6" to 2145 scaled
        nothing on ``DA1_LOC``, whose highlight pulse the saved list never
        named.  The one retime under :meth:`resize_shot`,
        :meth:`set_shot_duration` and the gap handles' Shift drag.
        """
        for obj in self._content_objects():
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)

    def _trailing_content_extent(self, shot) -> float:
        """Last frame of *shot*'s content past its end (keys and strips).

        Keys owned by OTHER shots are excluded (shared objects), matching
        the outer-content probe in :meth:`fit_shot_to_content`.  Audio past
        the last shot's end has no other owner, so every trailing strip
        counts.  Returns ``shot.end`` when nothing trails.
        """
        extent = shot.end
        if _ShotSequencerInternal._scene() is None:
            return extent
        other_spans = [
            (s.start - _EPS, s.end + _EPS)
            for s in self.store.shots
            if s.shot_id != shot.shot_id
        ]

        def _owned_elsewhere(t: float) -> bool:
            return any(lo <= t <= hi for lo, hi in other_spans)

        for name in self._shot_nodes(shot):
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                for t in AnimUtils.key_times(fc):
                    if t > extent and not _owned_elsewhere(t):
                        extent = t
        for events in self._read_all_audio_events().values():
            for _ev_start, ev_end in events:
                if ev_end > extent:
                    extent = ev_end
        return extent
