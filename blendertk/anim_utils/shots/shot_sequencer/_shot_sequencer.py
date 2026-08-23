# !/usr/bin/python
# coding=utf-8
"""Blender shot sequencer engine — ripple editing + key motion over the shared planner.

Mirror of mayatk's ``ShotSequencer`` (name + behavior): manual definition,
auto-detection, per-object segment collection, the unified anim+audio
*sequence* model (``collect_shot_sequences`` / ``move_sequences_to_shot``),
fit/trim/extend, per-object key motion, ripple editing, reorder, respace,
gap application and serialisation.  Every collision-safe multi-shot plan is
built by the pure pythontk planner (``shot_plan``) and committed by
``shot_apply.apply``; this class injects the two Blender writer strategies
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
      ``interpolation="CONSTANT"`` (Maya: ``step`` out-tangent).
    * **Reorder** goes through the pure ``plan_reorder`` + park/land apply
      (Maya hand-rolls the park loop; same result).
    * **No DAG-path reconciliation** — Blender object names are flat and
      unique, so :meth:`reconcile_all_shots` has nothing to re-resolve.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.shot_plan import _INF, ShotPlanner
from pythontk.core_utils.engines.shots.shot_apply import ShotApply

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore

_log = logging.getLogger(__name__)

_EPS = 1.0e-6
_SLOP = 1.0e-3  # matches mayatk's _ENVELOPE_SLOP so the window semantics agree
# ``AudioUtils.shift_clips_in_range`` inflates its window by ±1e-3; deflate the
# half-open envelope's upper bound past that so a strip exactly on the next
# shot's start is never claimed by two envelopes (mirror of mayatk's margin).
_AUDIO_UPPER_MARGIN = 3.0e-3


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
    def _has_motion(obj, start, end, value_tolerance: float = 1e-4) -> bool:
        """True when any transform channel of *obj* varies by > *value_tolerance* in ``[start, end]``."""
        for fc in _ShotSequencerInternal._transform_fcurves(obj):
            times, values = AnimUtils.key_arrays(fc)
            i0, i1 = AnimUtils.window_indices(times, start - _EPS, end + _EPS)
            if i1 - i0 < 2:
                continue
            window = values[i0:i1]
            if (max(window) - min(window)) > value_tolerance:
                return True
        return False


class ShotSequencer(_ShotSequencerInternal):
    """Manages a :class:`BlenderShotStore` and provides ripple editing and
    keyframe manipulation on top of it.

    Parameters:
        shots: Initial shot list (creates an internal store).
        store: Existing store to wrap.  Takes precedence over *shots*.
    """

    def __init__(self, shots=None, store=None):
        from pythontk import ShotStore as _PtkShotStore

        if store is None and isinstance(shots, _PtkShotStore):
            # ``ShotSequencer(store)`` — the positional form the Blender tests
            # and earlier callers use; mayatk's signature is (shots, store).
            shots, store = None, shots
        if store is not None:
            self.store = store
        else:
            self.store = BlenderShotStore(shots)

    # ---- delegated properties -------------------------------------------

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

    # ---- query -----------------------------------------------------------

    def sorted_shots(self):
        return self.store.sorted_shots()

    def shot_by_id(self, shot_id: int):
        return self.store.shot_by_id(shot_id)

    def shot_by_name(self, name: str):
        return self.store.shot_by_name(name)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(start, end, value_tolerance: float = 1e-4) -> List[str]:
        """Names of transforms with *non-flat* animation in ``[start, end]``.

        Walks each object's transform fcurves and keeps those whose keyed values
        vary by more than *value_tolerance* within the range (a wholly-constant
        curve is a hold, not content).  Only standard transform channels count —
        custom trigger attrs are ignored so marker objects don't register as
        scene content.
        """
        scene = _ShotSequencerInternal._scene()
        if scene is None:
            return []
        result: List[str] = []
        for obj in scene.objects:
            if _ShotSequencerInternal._has_motion(obj, start, end, value_tolerance):
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
        """No-op in Blender (documented divergence).

        mayatk's reconcile re-resolves stale Maya **DAG paths** (``|``-separated,
        which go stale on reparent); Blender object names are flat and unique, so
        a shot's stored names never need path-reconciliation.  Object *deletion*
        is surfaced by ``assess`` / ``classify_objects`` instead of silently
        rewriting membership.  Returns ``False`` (nothing reconciled).
        """
        return False

    # ---- manual definition -----------------------------------------------

    def define_shot(
        self,
        name: str,
        start: float,
        end: float,
        objects: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        locked: bool = False,
        description: str = "",
    ):
        """Define a shot; auto-discover keyed transforms when *objects* is None."""
        if objects is None:
            objects = self._find_keyed_transforms(start, end)
        return self.store.define_shot(
            name=name,
            start=start,
            end=end,
            objects=objects,
            metadata=metadata,
            locked=locked,
            description=description,
        )

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
        # Sequencer GUI invariant (mirror of mayatk): every keyed object on the
        # shot deserves a track marker even when its keys are static-value only
        # or its motion sits below ``motion_rate`` — backfill a span-of-keys
        # segment for any node with keys in range but no segment.
        if ignore_holds and nodes:
            covered = {s["obj"] for s in segments}
            scene = _ShotSequencerInternal._scene()
            if scene is not None:
                segments.extend(
                    self._span_segments(
                        scene,
                        [n for n in nodes if n not in covered],
                        shot.start,
                        shot.end,
                    )
                )
        return segments

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
            curves = _ShotSequencerInternal._transform_fcurves(obj)
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

    def collect_shot_sequences(
        self, shot_id: int, include_audio: bool = True
    ) -> List[Dict[str, Any]]:
        """All sequences (anim + audio) inside a shot's range.

        Each item: ``{"kind": "anim"|"audio", "obj", "start", "end"}``.
        """
        anim = self.collect_object_segments(shot_id)
        result: List[Dict[str, Any]] = [
            {"kind": "anim", "obj": s["obj"], "start": s["start"], "end": s["end"]}
            for s in anim
        ]
        if include_audio:
            shot = self.shot_by_id(shot_id)
            if shot is not None:
                result.extend(self._collect_audio_sequences(shot.start, shot.end))
        return result

    def _move_sequence(self, seq: Dict[str, Any], new_start: float) -> None:
        """Dispatch a sequence move on ``seq["kind"]`` (anim keys / audio strip)."""
        delta = new_start - seq["start"]
        if abs(delta) < _EPS:
            return
        if seq["kind"] == "anim":
            self.move_object_keys(seq["obj"], seq["start"], seq["end"], new_start)
        elif seq["kind"] == "audio":
            from blendertk.audio_utils._audio_utils import AudioUtils

            AudioUtils.shift_clips_in_range(
                seq["start"], seq["end"], delta, names=[seq["obj"]]
            )

    def _recompute_shot_objects(self, shot_id: int) -> None:
        """Rebuild ``shot.objects`` from the animation that actually lives in the shot.

        Locked / pinned objects are preserved even with no remaining keys.
        Audio is out of scope — strips are not part of ``shot.objects``.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None or _ShotSequencerInternal._scene() is None:
            return
        anim_objs = {seg["obj"] for seg in self.collect_object_segments(shot_id)}
        keep = self.store.pinned_objects | self.store.locked_objects
        new_objs = sorted(set(shot.objects) & (anim_objs | keep) | anim_objs)
        if new_objs != sorted(shot.objects):
            self.store.update_shot(shot_id, objects=new_objs)

    # ---- move sequences across shots -------------------------------------

    def _source_shot_id_for(self, seq: Dict[str, Any]) -> Optional[int]:
        """Return the shot_id that currently contains *seq* (by frame range)."""
        for sh in self.store.shots:
            if sh.start - _EPS <= seq["start"] and seq["end"] <= sh.end + _EPS:
                return sh.shot_id
        return None

    def move_sequences_to_shot(
        self, sequences: List[Dict[str, Any]], dest_shot_id: int
    ) -> None:
        """Move *sequences* (anim and/or audio) into *dest_shot_id*.

        Sequences are grouped by source shot so each subgroup moves as a unit
        (internal offsets preserved).  Inside the destination a subgroup is
        placed adjacent to an existing sequence on the same object (after it
        when the source lies upstream, before it when downstream) or anchored
        to the destination start.  ``shot.objects`` is recomputed for the
        destination and every source shot; the destination then extends to fit
        any overrun (implicit, never a separate user action).
        """
        dest = self.shot_by_id(dest_shot_id)
        if dest is None:
            raise ValueError(f"No shot with id {dest_shot_id}")
        if not sequences:
            return

        dest_seqs_by_obj: Dict[str, List[Dict[str, Any]]] = {}
        for s in self.collect_shot_sequences(dest_shot_id):
            dest_seqs_by_obj.setdefault(s["obj"], []).append(s)

        groups: Dict[Optional[int], List[Dict[str, Any]]] = {}
        for seq in sequences:
            sid = self._source_shot_id_for(seq)
            if sid == dest_shot_id:
                continue
            groups.setdefault(sid, []).append(seq)
        if not groups:
            return

        affected: set = {dest_shot_id}

        # Pre-register moved anim objects on dest so the post-move recompute
        # actually scans them.
        additions = {
            seq["obj"]
            for grp in groups.values()
            for seq in grp
            if seq["kind"] == "anim"
        }
        if additions:
            merged = sorted(set(dest.objects) | additions)
            if merged != sorted(dest.objects):
                self.store.update_shot(dest_shot_id, objects=merged)

        with self.store.batch_update():
            for source_id, group in groups.items():
                src = self.shot_by_id(source_id) if source_id is not None else None
                direction = (
                    "left" if (src is not None and src.start > dest.start) else "right"
                )

                base = min(s["start"] for s in group)
                group_dur = max(s["end"] for s in group) - base

                existing: List[Dict[str, Any]] = []
                for seq in group:
                    existing.extend(dest_seqs_by_obj.get(seq["obj"], []))

                if existing:
                    if direction == "right":
                        anchor = max(e["end"] for e in existing)
                    else:
                        anchor = min(e["start"] for e in existing) - group_dur
                else:
                    anchor = dest.start

                for seq in group:
                    offset = seq["start"] - base
                    new_start = anchor + offset
                    self._move_sequence(seq, new_start)
                    dest_seqs_by_obj.setdefault(seq["obj"], []).append(
                        {
                            "kind": seq["kind"],
                            "obj": seq["obj"],
                            "start": new_start,
                            "end": new_start + (seq["end"] - seq["start"]),
                        }
                    )
                if source_id is not None:
                    affected.add(source_id)

            for sid in affected:
                self._recompute_shot_objects(sid)

        self.extend_shot_to_fit(dest_shot_id)

    # ---- shot fit / trim / extend ----------------------------------------

    def fit_shot_to_content(
        self, shot_id: int, mode: str = "fit"
    ) -> Tuple[float, float]:
        """Resize a shot's boundaries to its sequence content, rippling neighbours.

        ``"fit"`` snaps both edges to content; ``"trim"`` only contracts;
        ``"extend"`` only expands to enclose out-of-range content.  Keys owned
        by OTHER shots are never attributed to this one (shared objects), but
        keys in gaps (fade tails) still count.  Returns ``(head_delta,
        tail_delta)``.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        sequences = self.collect_shot_sequences(shot_id)

        outer_start = outer_end = None
        if mode in ("extend", "fit") and shot.objects:
            other_spans = [
                (s.start - _EPS, s.end + _EPS)
                for s in self.store.shots
                if s.shot_id != shot_id
            ]

            def _owned_elsewhere(t: float) -> bool:
                return any(lo <= t <= hi for lo, hi in other_spans)

            for name in self._shot_nodes(shot):
                obj = _ShotSequencerInternal._object(name)
                for fc in _ShotSequencerInternal._transform_fcurves(obj):
                    for t in AnimUtils.key_times(fc):
                        if shot.start <= t <= shot.end or _owned_elsewhere(t):
                            continue
                        if t < shot.start:
                            outer_start = (
                                t if outer_start is None else min(outer_start, t)
                            )
                        else:
                            outer_end = t if outer_end is None else max(outer_end, t)

        if not sequences and outer_start is None and outer_end is None:
            return 0.0, 0.0

        seq_start = min(s["start"] for s in sequences) if sequences else None
        seq_end = max(s["end"] for s in sequences) if sequences else None

        def _combine(a, b, agg):
            vals = [v for v in (a, b) if v is not None]
            return agg(vals) if vals else None

        content_start = _combine(seq_start, outer_start, min)
        content_end = _combine(seq_end, outer_end, max)
        if content_start is None or content_end is None:
            return 0.0, 0.0

        if mode == "trim":
            new_start = max(shot.start, content_start)
            new_end = min(shot.end, content_end)
        elif mode == "extend":
            new_start = min(shot.start, content_start)
            new_end = max(shot.end, content_end)
        else:
            new_start, new_end = content_start, content_end

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        head_delta = new_start - shot.start
        tail_delta = new_end - shot.end
        if abs(head_delta) < _EPS and abs(tail_delta) < _EPS:
            return 0.0, 0.0

        old_start, old_end = shot.start, shot.end
        shot.start = new_start
        shot.end = new_end
        if abs(tail_delta) > _EPS:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        if abs(head_delta) > _EPS:
            self.ripple_upstream(shot_id, old_start, head_delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()
        return head_delta, tail_delta

    def trim_shot_to_content(self, shot_id: int) -> Tuple[float, float]:
        """Shrink shot boundaries inward so they exactly enclose content."""
        return self.fit_shot_to_content(shot_id, mode="trim")

    def extend_shot_to_fit(self, shot_id: int) -> Tuple[float, float]:
        """Expand shot boundaries outward to enclose all of its sequences."""
        return self.fit_shot_to_content(shot_id, mode="extend")

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

    def detect_next_shot(
        self,
        gap_threshold: float = 5.0,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
    ) -> Optional[Dict[str, Any]]:
        """Detect the first animation cluster not yet covered by a shot.

        Prefers the first candidate starting after every existing shot; falls
        back to any candidate that overlaps no existing shot.  ``None`` when
        all animation is already registered.
        """
        candidates = self.detect_shots(
            gap_threshold=gap_threshold, ignore=ignore, motion_rate=motion_rate
        )
        if not candidates:
            return None
        existing = self.store.sorted_shots()
        if not existing:
            return candidates[0]
        last_end = max(s.end for s in existing)
        for cand in candidates:
            if cand["start"] >= last_end:
                return cand
        for cand in candidates:
            if not any(
                cand["start"] < s.end and cand["end"] > s.start for s in existing
            ):
                return cand
        return None

    # ---- key motion primitives -------------------------------------------

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

        The ``move_keys`` strategy ``ShotApply.apply`` calls: translate every
        keyframe point (and both bezier handles) whose time falls in the window,
        then ``fcurve.update()``.  ``half_open=True`` (the apply contract) selects
        ``[env_lo, env_hi)`` — a key exactly on ``env_hi`` (the next shot's start)
        stays with that shot; the pivot mover passes ``half_open=False`` for an
        inclusive window.  ``over`` is advisory: a direct ``co[0]`` write has no
        neighbour clamp, so keys always pass.
        """
        if not objects or abs(delta) < _EPS:
            return
        lo = env_lo - _SLOP
        hi = (env_hi - _SLOP) if half_open else (env_hi + _SLOP)
        for name in objects:
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                AnimUtils.shift_keys_in_window(
                    fc, lo, hi, delta, inclusive_hi=not half_open
                )

    def _batch_move_keys(self, objects, old_start, old_end, new_start) -> None:
        """Move all keys of *objects* in ``[old_start, old_end]`` so the run starts at *new_start*."""
        self._move_keys(
            objects, old_start, old_end, new_start - old_start, half_open=False
        )

    @staticmethod
    def _shift_audio(old_start: float, old_end: float, delta: float) -> None:
        """Shift VSE sound strips whose start falls in ``[old_start, old_end]`` by *delta*."""
        if abs(delta) < _EPS:
            return
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(old_start, old_end, delta)

    @staticmethod
    def _shift_audio_envelope(env_lo: float, env_hi: float, delta: float) -> None:
        """``shift_audio`` strategy for ``ShotApply.apply`` (half-open envelope).

        ``env_hi`` extends to the next shot's current start, so strips sitting in
        the trailing gap travel with the preceding shot (mirror of the keyframe
        fade-tail rule); the upper bound is deflated by
        :data:`_AUDIO_UPPER_MARGIN` so a strip exactly on a shot boundary can't be
        claimed by two envelopes.
        """
        if abs(delta) < _EPS:
            return
        hi = env_hi if env_hi < _INF else env_lo + 1.0e7
        hi -= _AUDIO_UPPER_MARGIN
        if hi <= env_lo:
            return
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(env_lo, hi, delta)

    def _apply(self, plan) -> None:
        """Commit *plan* with the Blender key + audio writers."""
        ShotApply.apply(
            plan,
            self.store,
            move_keys=self._move_keys,
            shift_audio=self._shift_audio_envelope,
        )

    @classmethod
    def move_curve_keys(
        cls, crv, times: list, delta: float, plug=None, eps: float = 1e-3
    ) -> None:
        """Shift the keys of fcurve *crv* at *times* by *delta* (handles travel too).

        Public for the same two consumers as mayatk's (shot moves and the
        clip-motion drag handler).  *plug* is accepted for signature parity
        (Maya needs the driven plug when ``cutKey`` deletes the curve node;
        a Blender fcurve survives with zero points).
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
        lo_i, hi_i = min(idx), max(idx)
        if len(idx) == hi_i - lo_i + 1:
            # Contiguous run — one bulk window shift.
            AnimUtils.shift_keys_in_window(crv, kt[lo_i], kt[hi_i], delta)
            return
        # Sparse selection inside a span: move the named points only.
        for i in sorted(idx):
            kp = crv.keyframe_points[i]
            kp.co[0] += delta
            kp.handle_left[0] += delta
            kp.handle_right[0] += delta
        crv.update()

    @classmethod
    def recreate_curve_keys(
        cls, crv, pairs: list, plug=None, eps: float = 1e-3
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
            crv.update()

    # ---- per-object keyframe editing -------------------------------------

    def move_object_keys(
        self, obj: str, old_start: float, old_end: float, new_start: float
    ) -> None:
        """Offset *obj*'s keys in ``[old_start, old_end]`` so the run begins at *new_start*."""
        self._move_keys(
            [obj], old_start, old_end, new_start - old_start, half_open=False
        )

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
            self.move_curve_keys(fc, [old_time], delta, eps=eps)

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
                AnimUtils.remap_keys_in_window(
                    fc, lo, hi, old_start, old_end, new_start, new_end
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

    def _move_shot_content(self, shot, new_start: float) -> None:
        """Shift all content (object keys and audio strips) for *shot* to *new_start*.

        Inclusive window ``[old_start, old_end]``; safe because every caller
        vacates the destination side first (see :meth:`slide_shot`).
        """
        new_start = self.store.snap(new_start)
        old_start, old_end = shot.start, shot.end
        delta = new_start - old_start
        if abs(delta) < _EPS:
            return
        duration = old_end - old_start
        self._batch_move_keys(shot.objects, old_start, old_end, new_start)
        self._shift_audio(old_start, old_end, delta)
        shot.start = new_start
        shot.end = self.store.snap(new_start + duration)

    def move_object_in_shot(
        self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float
    ) -> None:
        """Move one object's keys within a shot, growing the shot + rippling when it overruns."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_start + (old_end - old_start))

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
        # not restep interpolation on objects the user didn't touch.

    # ---- ripple editing --------------------------------------------------

    def move_shot(self, shot_id: int, new_start: float) -> None:
        """Move an entire shot to *new_start*, rippling downstream (duration preserved)."""
        self.slide_shot(shot_id, new_start, direction="downstream")

    def slide_shot(
        self,
        shot_id: int,
        new_start: float,
        direction: str = "downstream",
        _enforce: bool = True,
    ) -> None:
        """Slide a shot intact to *new_start*, rippling only in *direction*.

        Order-sensitive: the destination side is vacated (neighbour ripple)
        before the pivot's own keys move when the pivot advances toward it,
        and after when it retreats — so the two key sets never transiently
        occupy the same frames.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
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
        else:
            if delta < 0:
                self.ripple_upstream(shot_id, old_start, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_upstream(shot_id, old_start, delta)
        if _enforce:
            self._enforce_gap_holds()
        self.store.mark_dirty()

    def ripple_downstream(self, shot_id: int, after_frame: float, delta: float) -> None:
        """Shift every shot starting at/after *after_frame* by *delta* (pivot excluded)."""
        self._apply(
            ShotPlanner.plan_ripple_downstream(self.store, shot_id, after_frame, delta)
        )

    def ripple_upstream(self, shot_id: int, before_frame: float, delta: float) -> None:
        """Shift every shot ending at/before *before_frame* by *delta* (pivot excluded)."""
        self._apply(
            ShotPlanner.plan_ripple_upstream(self.store, shot_id, before_frame, delta)
        )

    def _enforce_gap_holds(self) -> None:
        """Hold the pose through every inter-shot gap.

        For each gap, the last key (inside the pre-gap shot) on every fcurve
        of that shot's objects gets ``interpolation = "CONSTANT"`` — the
        Blender form of Maya's stepped out-tangent — so gaps never contain
        interpolated motion.  Already-constant keys are left alone.
        """
        sorted_s = self.sorted_shots()
        if len(sorted_s) < 2 or _ShotSequencerInternal._scene() is None:
            return
        for i in range(len(sorted_s) - 1):
            pre, nxt = sorted_s[i], sorted_s[i + 1]
            if nxt.start - pre.end < _EPS or not pre.objects:
                continue
            lo, hi = pre.start - _SLOP, pre.end + _SLOP
            for name in pre.objects:
                obj = _ShotSequencerInternal._object(name)
                if obj is None:
                    continue
                # Every fcurve, not just transforms — Maya steps every animCurve
                # on the object (``objects_to_curves``), so custom-prop and
                # visibility channels hold through the gap too.
                for fc in BlenderShotStore.iter_action_fcurves(obj):
                    if AnimUtils.step_last_key_in_window(fc, lo, hi):
                        fc.update()

    def expand_shot(self, shot_id: int, new_end: float) -> float:
        """Expand a shot's end frame and ripple downstream (never contracts).

        Returns the delta by which the shot was expanded (0 if unchanged).
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        if new_end <= shot.end:
            return 0.0
        delta = new_end - shot.end
        old_end = shot.end
        shot.end = self.store.snap(new_end)
        self.ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()
        return delta

    def resize_object(
        self,
        shot_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale one object's keys and ripple neighbours by the head/tail deltas."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        prior_start, prior_end = shot.start, shot.end
        shot.start = min(shot.start, new_start)
        shot.end = max(shot.end, new_end)
        head_delta = shot.start - prior_start
        if abs(head_delta) > _EPS:
            self.ripple_upstream(shot_id, prior_start, head_delta)
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
        delta = new_duration - shot.duration
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

    def resize_shot(
        self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True
    ) -> None:
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

    def set_shot_start(
        self, shot_id: int, new_start: float, ripple: bool = True
    ) -> None:
        """Move a shot to *new_start*; with *ripple* downstream shots shift by the same delta."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        delta = new_start - shot.start
        if abs(delta) < _EPS:
            return
        old_end = shot.end
        if ripple and delta > 0:
            self.ripple_downstream(shot_id, old_end, delta)
            self._move_shot_content(shot, new_start)
        else:
            self._move_shot_content(shot, new_start)
            if ripple:
                self.ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    def move_shot_to_position(self, shot_id: int, target_pos: int) -> None:
        """Reorder *shot_id* to 1-based timeline position *target_pos*.

        Durations are preserved; gaps use the store's gap setting (locked gaps
        honoured).  Keys and audio travel through the planner's park/land phases.
        """
        plan = ShotPlanner.plan_reorder(self.store, shot_id, target_pos, self.store.gap)
        if not plan.moves:
            return
        self._apply(plan)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    # ---- timing redistribution -------------------------------------------

    def respace(self, gap: float = 0, start_frame: float = 1) -> None:
        """Lay all shots out sequentially from *start_frame* with *gap* spacing (locked gaps kept)."""
        self._apply(ShotPlanner.plan_respace(self.store, gap, start_frame))
        self._enforce_gap_holds()

    def apply_gap(
        self, gap: float, scope: str = "all", shot_id: Optional[int] = None
    ) -> bool:
        """Apply *gap* to shots per *scope* (``all`` / ``start`` / ``end`` / ``start_end``).

        Returns ``True`` when any shot was repositioned.
        """
        sorted_s = self.sorted_shots()
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
            sorted_s = self.sorted_shots()
            idx = next((i for i, s in enumerate(sorted_s) if s.shot_id == shot_id), idx)
        if scope in ("end", "start_end") and idx < len(sorted_s) - 1:
            self.move_shot(sorted_s[idx + 1].shot_id, sorted_s[idx].end + gap)
            moved = True
        return moved

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise shots and settings to a plain dict."""
        return self.store.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotSequencer":
        """Restore from serialised data."""
        return cls(store=BlenderShotStore.from_dict(data))
