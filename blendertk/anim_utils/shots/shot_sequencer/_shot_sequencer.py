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
      ``interpolation="CONSTANT"`` (Maya: ``step`` out-tangent).  Both are
      claimed in the shared edit ledger so they can be RELEASED again; an
      fcurve has no node name, so a claim is keyed by
      ``"<object>|<data_path>|<array_index>"`` (:meth:`_fc_key`).
    * **Reorder** goes through the pure ``plan_reorder`` + park/land apply
      (Maya hand-rolls the park loop; same result).
    * **No DAG-path reconciliation** — Blender object names are flat and
      unique, so :meth:`reconcile_all_shots` has nothing to re-resolve.
    * **No undo pairing.** mayatk tags each boundary restore point with
      whether a native undo step accompanies it: Maya DISCARDS an empty undo
      chunk, so a bounds-only edit records nothing and an unconditional
      ``cmds.undo()`` would pop the user's previous, unrelated operation.
      ``CoreUtils.undo_chunk`` here pushes unconditionally (``ed.undo_push``
      on exit), so every restore point already has a native partner and the
      ledger's tag stays unset — which :meth:`on_undo` reads as "restore and
      undo", the behaviour Blender needs.
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

    @staticmethod
    def _fc_key(obj_name: str, fc) -> str:
        """Stable ledger key for an fcurve.

        Maya claims are keyed by animCurve NODE name; a Blender fcurve is not
        a node and has no name, so its owner plus channel identity stands in.
        Same shape, same uniqueness, and it survives everything except a
        rename of the object (which drops the claim, not the animation).
        """
        return f"{obj_name}|{fc.data_path}|{fc.array_index}"

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

    @staticmethod
    def _has_keys(obj, start, end) -> bool:
        """True when any transform channel of *obj* carries a key in the range."""
        for fc in _ShotSequencerInternal._transform_fcurves(obj):
            times = AnimUtils.key_times(fc)
            i0, i1 = AnimUtils.window_indices(times, start - _EPS, end + _EPS)
            if i1 > i0:
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
    def _find_keyed_transforms(
        start,
        end,
        value_tolerance: float = 1e-4,
        require_motion: bool = False,
    ) -> List[str]:
        """Names of transforms animated in ``[start, end]``.

        Only standard transform channels count — custom trigger attrs are
        ignored so marker objects don't register as scene content.

        ``require_motion=True`` additionally drops objects whose keyed values
        are wholly constant across the range.  That test belongs to shot
        *boundary detection*, where a held pose carries no cut information —
        it is the wrong test for shot *membership*: an object keyed on a hold
        for the whole shot is still that shot's content, and excluding it left
        it invisible in the panel and stranded when the shot moved (ripples
        shift ``shot.objects``, so anything missing from that list is left
        behind).  Membership therefore defaults to "has keys in range".
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

    def sequence_separation(self) -> float:
        """Room to leave between a moved sequence and what it lands after.

        Strictly MORE than the inter-shot gap, and never less than a frame: at
        zero the arriving clip butts against the existing one and the two draw
        as a single merged run (a non-destructive move that LOOKS like an
        overwrite), and at exactly the shot gap a seam inside a shot is
        indistinguishable from a seam between shots.
        """
        return self.store.snap(max(float(self.store.gap) + 1.0, 1.0))

    def move_sequences_to_shot(
        self, sequences: List[Dict[str, Any]], dest_shot_id: int
    ) -> None:
        """Move *sequences* (anim and/or audio) into *dest_shot_id*.

        Sequences are grouped by source shot so each subgroup moves as a unit
        (internal offsets preserved) -- a multi-object selection keeps its
        shape.  Inside the destination there is one placement rule, whichever
        direction the content travelled from: it lands AFTER the destination's
        existing run on those objects, separated by
        :meth:`sequence_separation`, or at the destination start when there is
        nothing of its own to clear.  Anchoring by direction of travel put a
        clip dragged from a LATER shot ahead of the destination's content, and
        could place it ahead of the destination's start -- on top of the
        previous shot.

        The room is opened BEFORE the content lands (see the mayatk twin for
        why growing afterwards cannot work), so an arrival only ever touches
        empty timeline.  ``shot.objects`` is recomputed for the destination and
        every source shot.
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

        separation = self.sequence_separation()

        # ---- 1. resolve every landing spot BEFORE anything moves ----------
        # Purely arithmetic, against the destination's CURRENT content: what is
        # already there never moves, so these targets survive the room-making.
        placements: List[tuple] = []  # (seq, source_shot_id, target_start)
        needed_end = dest.end
        for source_id, group in sorted(
            groups.items(), key=lambda kv: min(s["start"] for s in kv[1])
        ):
            base = min(s["start"] for s in group)

            existing: List[Dict[str, Any]] = []
            for seq in group:
                existing.extend(dest_seqs_by_obj.get(seq["obj"], []))

            if existing:
                anchor = self.store.snap(max(e["end"] for e in existing) + separation)
            else:
                anchor = dest.start

            for seq in group:
                target = anchor + (seq["start"] - base)
                span = seq["end"] - seq["start"]
                placements.append((seq, source_id, target))
                needed_end = max(needed_end, target + span)
                dest_seqs_by_obj.setdefault(seq["obj"], []).append(
                    {
                        "kind": seq["kind"],
                        "obj": seq["obj"],
                        "start": target,
                        "end": target + span,
                    }
                )
            if source_id is not None:
                affected.add(source_id)

        # ---- 2. open the room, THEN land in it ----------------------------
        with self.store.batch_update():
            room = self.store.snap(needed_end) - dest.end
            if room > _EPS:
                old_end = dest.end
                # Source shots at or after the destination's end travel with
                # the ripple, and so does the content still sitting in them.
                travelled = {
                    sid
                    for sid in groups
                    if sid is not None
                    and (self.shot_by_id(sid) or dest).start >= old_end - _EPS
                }
                dest.end = self.store.snap(needed_end)
                self.ripple_downstream(dest_shot_id, old_end, room)
                for seq, source_id, _target in placements:
                    if source_id in travelled:
                        seq["start"] += room
                        seq["end"] += room

            for seq, _source_id, target in placements:
                self._move_sequence(seq, target)

            for sid in affected:
                self._recompute_shot_objects(sid)

        # Safety net for anything the arithmetic could not predict; normally a
        # no-op now, because the room was already opened to size.
        self.extend_shot_to_fit(dest_shot_id)

    # ---- shot fit / trim / extend ----------------------------------------

    def fit_shot_to_content(
        self, shot_id: int, mode: str = "fit", edge: str = "both"
    ) -> Tuple[float, float]:
        """Resize a shot's boundaries to its sequence content, rippling neighbours.

        ``"fit"`` snaps both edges to content; ``"trim"`` only contracts;
        ``"extend"`` only expands to enclose out-of-range content.  *edge*
        restricts which end may move: ``"both"`` (default), ``"leading"`` or
        ``"trailing"``.  Keys owned by OTHER shots are never attributed to this
        one (shared objects), but keys in gaps (fade tails) still count.
        Returns ``(head_delta, tail_delta)``.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        sequences = self.collect_shot_sequences(shot_id)

        # ``sequences`` reports MOTION (hold spans are dropped so a clip reads
        # as the animation it plays), but a BOUND may not move past a key — so
        # the in-bounds KEY extent is folded in beside it.  Without that, a
        # shot whose tail is a long hold read as empty at the end: the trim
        # put the bound in front of keys that stayed behind while the
        # downstream ripple pulled the next shot's content on top of them.
        # (Mirrors mayatk; see its twin for the production case.)
        inner_start = inner_end = None
        outer_start = outer_end = None
        if shot.objects:
            probe_outside = mode in ("extend", "fit")
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
                        if shot.start <= t <= shot.end:
                            inner_start = (
                                t if inner_start is None else min(inner_start, t)
                            )
                            inner_end = t if inner_end is None else max(inner_end, t)
                            continue
                        if not probe_outside or _owned_elsewhere(t):
                            continue
                        if t < shot.start:
                            outer_start = (
                                t if outer_start is None else min(outer_start, t)
                            )
                        else:
                            outer_end = t if outer_end is None else max(outer_end, t)

        probes = (inner_start, outer_start, outer_end)
        if not sequences and all(v is None for v in probes):
            return 0.0, 0.0

        seq_start = min(s["start"] for s in sequences) if sequences else None
        seq_end = max(s["end"] for s in sequences) if sequences else None

        def _combine(agg, *vals):
            present = [v for v in vals if v is not None]
            return agg(present) if present else None

        content_start = _combine(min, seq_start, inner_start, outer_start)
        content_end = _combine(max, seq_end, inner_end, outer_end)
        if mode == "extend":
            # One-sided rescue: content that drifted entirely past ONE edge
            # leaves the other side None — substitute the shot's own
            # boundary so extend still encloses the out-of-range side.
            if content_start is None:
                content_start = shot.start
            if content_end is None:
                content_end = shot.end
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

        if edge == "leading":
            new_end = shot.end
        elif edge == "trailing":
            new_start = shot.start

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
        # reconcile, not just enforce: this moved a shot BOUND, which is
        # exactly when a boundary sample the system created has to follow
        # it or be cleaned up.
        self.reconcile_system_edits()
        self.store.mark_dirty()
        return head_delta, tail_delta

    def trim_shot_to_content(
        self, shot_id: int, edge: str = "both"
    ) -> Tuple[float, float]:
        """Shrink shot boundaries inward so they exactly enclose content.

        *edge* narrows the operation to one end — ``"leading"`` or
        ``"trailing"`` — leaving the other where the animator put it.
        """
        return self.fit_shot_to_content(shot_id, mode="trim", edge=edge)

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
                AnimUtils.shift_keys_in_window(
                    fc, lo, hi, delta, inclusive_hi=hi_closed
                )
                # A claim is a (curve, time) pair, so it has to travel with
                # the keys; left behind, the system could neither release its
                # own hold nor recognise the moved key as its own.
                led.shift(_ShotSequencerInternal._fc_key(name, fc), lo, hi, delta)

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
        owns a shared sample.
        """
        self._move_keys(
            objects, env_lo, env_hi, delta, lo_open=lo_open, hi_closed=hi_closed
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
        hi = env_hi if env_hi < _INF else env_lo + 1.0e7
        hi += _AUDIO_UPPER_MARGIN if hi_closed else -_AUDIO_UPPER_MARGIN
        lo = env_lo + _AUDIO_UPPER_MARGIN if lo_open else env_lo
        if hi <= lo:
            return
        from blendertk.audio_utils._audio_utils import AudioUtils

        AudioUtils.shift_clips_in_range(lo, hi, delta)

    @staticmethod
    def _keyed_transform_times() -> dict:
        """Map every object's name to the key times on its transform channels.

        Standard transform channels only (``_is_transform_path``), so custom
        trigger attributes never make a marker look like scene content.
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

    def _adopt_keyed_objects(
        self,
        shot,
        lo: float,
        hi: float,
        keyed=None,
        lo_open: bool = False,
        hi_closed: bool = False,
    ) -> bool:
        """Add to *shot* the objects keyed in the window it is about to move.

        The caller supplies the window its own writer will move, so this
        never invents a second definition of what a shot covers.

        The selection rule is :meth:`ShotPlanner.objects_to_adopt`, shared
        with mayatk so the two cannot drift; only discovery is
        Blender-specific.  No ownership exemption is needed: the window's
        fencepost flags already partition the timeline, so a key on a shared
        sample is inside exactly one shot's window.

        Returns ``True`` if the shot gained an object.
        """
        if keyed is None:
            keyed = self._keyed_transform_times()
        if not keyed:
            return False
        add = ShotPlanner.objects_to_adopt(
            keyed,
            set(self._shot_nodes(shot)),
            lo,
            hi,
            lo_open=lo_open,
            hi_closed=hi_closed,
            # The same slop the writers pad their window by: adopting on a
            # tighter window than the writer moves would list an object
            # whose key the writer then leaves behind.
            eps=_SLOP,
        )
        if not add:
            return False
        shot.objects = sorted(set(shot.objects) | set(add))
        self.store.update_shot(shot.shot_id, objects=shot.objects)
        return True

    def _backfill_envelope_membership(self, plan) -> bool:
        """Give every moving shot the objects actually keyed in its envelope.

        :class:`ShotApply` shifts ``shot.objects`` within ``[env_start,
        env_end)``, so an object keyed inside that window but missing from
        the list is **left behind**: the shot moves and part of its
        animation does not, landing inside a neighbouring shot.
        :meth:`_find_keyed_transforms` already defines membership as "has
        keys in range" for exactly this reason; this applies that rule at
        the moment it matters.  Mirrors mayatk.

        Returns ``True`` if any shot gained an object.
        """
        targets = [
            (sid, mv)
            for sid, mv in plan.moves.items()
            if mv.moves or sid in plan.parked
        ]
        if not targets:
            return False
        keyed = self._keyed_transform_times()
        changed = False
        for shot_id, move in targets:
            shot = self.shot_by_id(shot_id)
            if shot is None:
                continue
            if self._adopt_keyed_objects(
                shot,
                move.env_start,
                move.env_end,
                keyed,
                lo_open=move.env_lo_open,
                hi_closed=move.env_hi_closed,
            ):
                changed = True
        return changed

    @staticmethod
    def _object_fcurves(names) -> list:
        """Every action fcurve of the named objects, in name order."""
        out: list = []
        for name in names or ():
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            out.extend(BlenderShotStore.iter_action_fcurves(obj))
        return out

    @staticmethod
    def _named_fcurves(names) -> list:
        """``[(object_name, fcurve), ...]`` for the named objects.

        The twin of :meth:`_object_fcurves` for callers that also need the
        owner — an fcurve carries no name, so the ledger key is built from
        the object plus the channel.
        """
        out: list = []
        for name in names or ():
            obj = _ShotSequencerInternal._object(name)
            if obj is None:
                continue
            out.extend((name, fc) for fc in BlenderShotStore.iter_action_fcurves(obj))
        return out

    @staticmethod
    def _key_at(fc, t: float, eps: float = _SLOP):
        """The keyframe point of *fc* within *eps* of *t*, or ``None``."""
        for kp in fc.keyframe_points:
            if abs(kp.co[0] - t) <= eps:
                return kp
        return None

    def _plan_curves(self, plan) -> list:
        """``[(fcurve, [key time, ...]), ...]`` for every curve *plan* moves."""
        names: set = set()
        for shot_id, move in plan.moves.items():
            if not move.moves:
                continue
            shot = self.shot_by_id(shot_id)
            if shot is not None:
                names.update(self._shot_nodes(shot))
        out: list = []
        for fc in self._object_fcurves(sorted(names)):
            times = AnimUtils.key_times(fc)
            if times:
                out.append((fc, sorted(times)))
        return out

    def _reconcile_boundaries(self, plan):
        """Keep fencepost samples whole across boundaries *plan* changes.

        Contiguous shots share one sample — the preceding shot's closing
        pose IS the following shot's opening pose, on the same frame — so a
        plan that changes a gap has to split that sample in two or merge two
        into one.  A split captures the pose before anything moves and
        re-keys it at the following shot's new start (only on curves that
        shot animates past the boundary); a merge cuts the loser so the
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
        from pythontk.core_utils.engines.shots.shot_plan import ShotBoundaryConflict

        def _noop():
            return None

        windows = ShotPlanner.move_windows(plan)
        if not windows:
            return _noop

        # ---- merges: detect every conflict BEFORE cutting anything -------
        conflicts: list = []
        losers: list = []
        for fc, times in self._plan_curves(plan):
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
                    losers.extend((fc, t) for t in movers[1:] + still)
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
                captures.append(
                    (
                        fc,
                        float(new_start),
                        float(kp.co[1]),
                        kp.interpolation,
                        shared,
                        _ShotSequencerInternal._fc_key(obj_name, fc),
                        shot_id,
                    )
                )
                if not shared:
                    losers.append((fc, float(boundary)))

        # Never empty a curve: keep at least one key so the fcurve survives.
        for fc, t in losers:
            kp = self._key_at(fc, t)
            if kp is not None and len(fc.keyframe_points) > 1:
                try:
                    fc.keyframe_points.remove(kp)
                    fc.update()
                except RuntimeError:
                    pass  # locked or library-linked curve — leave it as it was

        if not captures:
            return _noop

        led = self.ledger

        def _finish():
            for fc, frame, value, interp, _shared, key, owner in captures:
                if self._key_at(fc, frame) is not None:
                    continue  # something already landed here; leave it alone
                try:
                    kp = fc.keyframe_points.insert(frame, value)
                    kp.interpolation = interp
                    fc.update()
                except (RuntimeError, TypeError, ValueError):
                    pass  # locked/linked curve — the move still stands
                else:
                    # Claimed for the shot bound it opens, so it follows that
                    # bound from here rather than being left behind by it.
                    led.record_key(key, frame, owner, "start")

        return _finish

    def _apply(self, plan) -> None:
        """Commit *plan* with the Blender key + audio writers.

        Membership is completed first so no shot moves while leaving part of
        its animation behind (see :meth:`_backfill_envelope_membership`), and
        shared samples are reconciled around the write (see
        :meth:`_reconcile_boundaries`).
        """
        self._backfill_envelope_membership(plan)
        finish = self._reconcile_boundaries(plan)
        ShotApply.apply(
            plan,
            self.store,
            move_keys=self._move_keys,
            shift_audio=self._shift_audio_envelope,
        )
        finish()

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
                    ledger.release_step(ledger_key, t)
                    ledger.release_key(ledger_key, t)
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
        edge of the landing zone.

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
            for i in sorted(idx):
                kp = crv.keyframe_points[i]
                kp.co[0] += delta
                kp.handle_left[0] += delta
                kp.handle_right[0] += delta
            crv.update()
        if ledger is not None and ledger_key:
            ledger.remap(ledger_key, [(t, t + delta) for t in moved_times])

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
            crv.update()
            if ledger is not None and ledger_key:
                ledger.remap(ledger_key, pairs)

    # ---- per-object keyframe editing -------------------------------------

    def move_object_keys(
        self, obj: str, old_start: float, old_end: float, new_start: float
    ) -> None:
        """Offset *obj*'s keys in ``[old_start, old_end]`` so the run begins at *new_start*."""
        # An explicit inclusive range, not a shot envelope: both ends are the
        # caller's own and there is no neighbouring shot to share one with.
        self._move_keys(
            [obj], old_start, old_end, new_start - old_start, hi_closed=True
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

        Uses the same envelope as the plan path, derived from the layout as
        it stands right now; safe because every caller vacates the
        destination side first (see :meth:`slide_shot`).
        """
        new_start = self.store.snap(new_start)
        old_start, old_end = shot.start, shot.end
        delta = new_start - old_start
        if abs(delta) < _EPS:
            return
        duration = old_end - old_start

        # The engine derives the pivot's envelope and one-move plan, so this
        # mover and the plan path cannot disagree about a shared sample.
        plan = ShotPlanner.plan_pivot_move(self.store, shot.shot_id, new_start)
        move = plan.moves.get(shot.shot_id)
        if move is None:
            env_lo, env_hi, lo_open, hi_closed = old_start, old_end, False, True
        else:
            env_lo, env_hi = move.env_start, move.env_end
            lo_open, hi_closed = move.env_lo_open, move.env_hi_closed

        # A shot moving must carry everything keyed inside it, not just what
        # membership happens to list.
        self._adopt_keyed_objects(
            shot, env_lo, env_hi, lo_open=lo_open, hi_closed=hi_closed
        )
        finish = self._reconcile_boundaries(plan)
        self._batch_move_keys(
            shot.objects, env_lo, env_hi, delta, lo_open=lo_open, hi_closed=hi_closed
        )
        # Audio keeps its own inclusive [old_start, old_end] window: strips
        # are a separate store with no fencepost sharing.
        self._shift_audio(old_start, old_end, delta)
        finish()
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

        # Boundaries FIRST, keys second (mirrors mayatk).  Expanding past the
        # next shot's start ripples that shot through its envelope, and a key
        # already landed at/past the envelope's start is swept a SECOND time --
        # the clip travels the drag distance plus the ripple.  Before the move
        # the keys still sit at their old times inside the pivot, which the
        # ripple plan excludes, so nothing can reach them.
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

        # Move the object's keys into the room just opened for them.
        self.move_object_keys(obj, old_start, old_end, new_start)
        # No _enforce_gap_holds here (matches mayatk): a per-object move must
        # not restep interpolation on objects the user didn't touch.

    # ---- ripple editing --------------------------------------------------

    def move_shot(self, shot_id: int, new_start: float) -> None:
        """Move an entire shot to *new_start*, rippling downstream (duration preserved)."""
        self.slide_shot(shot_id, new_start, direction="downstream")

    def _clamp_slide_start(self, shot, new_start: float, rippled) -> float:
        """Hold *new_start* inside the room the neighbours are NOT making.

        A slide moves the shot whole and ripples ONE side, or neither — so
        the other side has to hold, or the pivot slides over its neighbour
        and the store ends up with two shots claiming one span (key
        ownership, and every envelope derived from it, goes ambiguous).
        Same rule the inner gap-edge drag already keeps.  Mirrors mayatk.

        *rippled* is ``"downstream"``, ``"upstream"``, or ``None`` when
        neither side moves and both therefore hold.
        """
        sorted_s = self.sorted_shots()
        idx = next(
            (i for i, s in enumerate(sorted_s) if s.shot_id == shot.shot_id), None
        )
        if idx is None:
            return new_start
        # Tail first, head second, so the head clamp wins a tie.
        if rippled != "downstream" and idx + 1 < len(sorted_s):
            new_start = min(
                new_start, sorted_s[idx + 1].start - (shot.end - shot.start)
            )
        if rippled != "upstream" and idx > 0:
            new_start = max(new_start, sorted_s[idx - 1].end)
        return new_start

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
        new_start = self._clamp_slide_start(shot, self.store.snap(new_start), direction)
        delta = new_start - old_start
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

    # ---- system-authored edits (ledger-backed) ---------------------------
    #
    # Mirror of mayatk's contract: the two writes this system makes on the
    # animator's curves — a gap hold and a boundary sample — are claimed in
    # ``store.edit_ledger`` as they are made, so they can be RELEASED when
    # the boundary that justified them moves.  A hold the animator put in is
    # never claimed and therefore never taken back.

    @property
    def ledger(self):
        """The store's :class:`~pythontk.ShotEditLedger` (system-write claims)."""
        return self.store.edit_ledger

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
        Shot membership is per OBJECT, so every fcurve on a member reaches
        here -- including ones whose first key lands in the gap and whose
        motion runs on into the NEXT shot.  Such a curve has no pre-gap value
        to hold, and its first key is the next shot's lead-in, not this shot's
        overhang.  (Mirrors mayatk; see its twin for the production case.)
        """
        seams: Dict[str, list] = {}
        sorted_s = self.sorted_shots()
        if len(sorted_s) < 2 or _ShotSequencerInternal._scene() is None:
            return seams
        for i in range(len(sorted_s) - 1):
            pre, nxt = sorted_s[i], sorted_s[i + 1]
            if nxt.start - pre.end < _EPS or not pre.objects:
                continue
            lo, hi = pre.start - _SLOP, nxt.start - _SLOP
            for name in pre.objects:
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

    def _enforce_gap_holds(self) -> None:
        """Hold every inter-shot gap, and release the holds that no longer are.

        For each gap the last key before it gets ``interpolation = "CONSTANT"``
        — the Blender form of Maya's stepped out-tangent — so gaps never
        contain interpolated motion.  The release half keeps that from
        accumulating: a key this system held that is no longer the seam gets
        its original interpolation back.  Idempotent.
        """
        seams = self._gap_hold_seams()
        self._release_gap_holds(seams)
        self._apply_gap_holds(seams)

    @classmethod
    def _sample_is_redundant(cls, fc, idx: int) -> bool:
        """True when removing point *idx* cannot change what *fc* plays.

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
        if idx <= 0 or idx >= len(pts) - 1:
            return False  # no neighbour on one side: the hold beyond it is shape
        prev, nxt = pts[idx - 1], pts[idx + 1]
        here = pts[idx].co[1]
        if abs(prev.co[1] - here) > _POSE_TOL or abs(nxt.co[1] - here) > _POSE_TOL:
            return False

        # The surviving segment runs prev -> next and takes its shape from
        # PREV's interpolation, plus the two handles that face into it.
        if prev.interpolation in _FLAT_SPAN_INTERPOLATIONS:
            return True
        return (
            abs(prev.handle_right[1] - prev.co[1]) <= _POSE_TOL
            and abs(nxt.handle_left[1] - nxt.co[1]) <= _POSE_TOL
        )

    def _reconcile_boundary_keys(self) -> Tuple[int, int]:
        """Make every claimed boundary sample follow — or leave — its bound.

        Mirror of mayatk's: the sample follows a bound that moved, is dropped
        when the key is already gone, and is cut only where that is provably a
        no-op; otherwise it is disowned and left where it is.

        Returns ``(moved, removed)``.
        """
        if _ShotSequencerInternal._scene() is None:
            return 0, 0
        led = self.ledger
        moved = removed = 0
        for key in led.keyed_curves():
            fc = _ShotSequencerInternal._fcurve_for_key(key)
            if fc is None:
                led.forget_curve(key)
                continue
            for t, owner, edge in led.key_records(key):
                shot = self.shot_by_id(owner) if owner >= 0 else None
                bound = None
                if shot is not None and edge in ("start", "end"):
                    bound = shot.start if edge == "start" else shot.end
                if bound is not None and abs(bound - t) <= _SLOP:
                    continue  # still on its bound
                idx = _ShotSequencerInternal._key_index_at(fc, t)
                if idx is None:
                    led.release_key(key, t)
                    continue
                occupied = (
                    bound is not None
                    and _ShotSequencerInternal._key_index_at(fc, bound) is not None
                )
                if bound is not None and not occupied:
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
                        removed += 1
                    except (RuntimeError, TypeError):
                        pass  # locked/linked curve — leave it as it was
                led.release_key(key, t)
        return moved, removed

    def reconcile_system_edits(self) -> Dict[str, int]:
        """Release every shot-system write whose boundary has moved on.

        The single maintenance entry point, safe after any mutation.
        Returns ``{"keys_moved", "keys_removed", "holds"}``.
        """
        moved, removed = self._reconcile_boundary_keys()
        self._enforce_gap_holds()
        return {
            "keys_moved": moved,
            "keys_removed": removed,
            "holds": self.ledger.step_count,
        }

    # ---- shot lifecycle (delete / merge / split / pad) --------------------

    def _shot_envelope(self, shot_id: int):
        """``(lo, hi, lo_open, hi_closed)`` — the key window a shot owns."""
        shots = self.sorted_shots()
        idx = next((i for i, s in enumerate(shots) if s.shot_id == shot_id), None)
        if idx is None:
            return None
        return ShotPlanner.envelope_for(shots, idx)

    def _unique_shot_name(self, base: str) -> str:
        """*base*, or the first ``base_2``, ``base_3``... no shot is using."""
        taken = {s.name for s in self.store.shots}
        if base not in taken:
            return base
        n = 2
        while f"{base}_{n}" in taken:
            n += 1
        return f"{base}_{n}"

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
        if hi >= _INF:
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
                for i in range(i1 - 1, i0 - 1, -1):
                    try:
                        fc.keyframe_points.remove(fc.keyframe_points[i])
                    except (RuntimeError, TypeError):
                        break  # locked/linked curve
                fc.update()
                cut += 1
                key = _ShotSequencerInternal._fc_key(name, fc)
                for t in led.step_times(key):
                    if window[0] <= t <= window[1]:
                        led.release_step(key, t)
                for t in led.key_times(key):
                    if window[0] <= t <= window[1]:
                        led.release_key(key, t)
        return cut

    def delete_shot(
        self,
        shot_id: int,
        delete_contents: bool = True,
        close_gap: bool = True,
    ) -> Dict[str, Any]:
        """Remove a shot — by default with its keys, and closing up behind it.

        Removing only the RECORD leaves the shot's animation orphaned mid-
        timeline and a hole where the shot was, which is almost never what
        "delete this shot" means.  The default cuts the shot's own content and
        slides everything downstream back by the span it occupied — its range
        plus the gap that followed it — so the next shot lands where this one
        started.  Both halves are opt-out.

        Returns ``{"curves_cut", "closed", "name"}``.

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        shots = self.sorted_shots()
        idx = next(i for i, s in enumerate(shots) if s.shot_id == shot_id)
        nxt = shots[idx + 1] if idx + 1 < len(shots) else None
        span_end = nxt.start if nxt is not None else shot.end
        vacated = max(0.0, span_end - shot.start)
        name = shot.name

        curves_cut = self._cut_shot_content(shot_id) if delete_contents else 0

        self.ledger.disown_shot(shot_id)
        self.store.remove_shot(shot_id)

        closed = 0.0
        if close_gap and nxt is not None and vacated > _EPS:
            # Pivot -1: no shot is exempt.  The record is already gone, so the
            # plan cannot pick it up as one of the shots to move.
            plan = ShotPlanner.plan_ripple_downstream(
                self.store, -1, span_end, -vacated
            )
            if plan.sequence or plan.parked:
                self._apply(plan)
                closed = vacated

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return {"curves_cut": curves_cut, "closed": closed, "name": name}

    def merge_shots(self, shot_ids: List[int], name: Optional[str] = None):
        """Fuse two or more shots into one spanning all of them.

        The earliest is kept and grown to the union range; the others go and
        their objects fold in.  Nothing MOVES — a merge says how the timeline
        is divided, not where content sits — so a gap between the merged shots
        becomes ordinary empty space, and the hold that guarded it is released
        by :meth:`reconcile_system_edits`.

        Raises:
            ValueError: If fewer than two of *shot_ids* resolve to shots.
        """
        shots = [s for s in (self.shot_by_id(i) for i in shot_ids) if s is not None]
        if len(shots) < 2:
            raise ValueError("merge_shots needs at least two existing shots")
        shots.sort(key=lambda s: (s.start, s.shot_id))
        keeper = shots[0]

        new_start = min(s.start for s in shots)
        new_end = max(s.end for s in shots)
        objects: List[str] = []
        for s in shots:  # union, first-seen order, so track order is stable
            for obj in s.objects:
                if obj not in objects:
                    objects.append(obj)
        notes = [s.description for s in shots if s.description]

        with self.store.batch_update():
            for s in shots[1:]:
                self.ledger.disown_shot(s.shot_id)
                self.store.remove_shot(s.shot_id)
            self.store.update_shot(
                keeper.shot_id,
                name=name or keeper.name,
                start=new_start,
                end=new_end,
                objects=objects,
                description=" / ".join(notes),
            )

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return self.shot_by_id(keeper.shot_id)

    def split_shot(
        self,
        shot_id: int,
        at_frame: float,
        name: Optional[str] = None,
        gap: float = 0.0,
    ):
        """Cut a shot in two at *at_frame*, leaving its content where it is.

        The head keeps the original record and ends at the cut; the tail is a
        new shot from the cut to the original end.  The two are contiguous and
        therefore share the sample on the cut frame, the same fencepost
        convention every other operation uses.  With *gap* the tail and
        everything after it ripples downstream, so the split lands as a real
        cut.

        Raises:
            ValueError: If *shot_id* does not exist, or *at_frame* is not
                strictly inside it.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        at = self.store.snap(float(at_frame))
        if not (shot.start + _EPS < at < shot.end - _EPS):
            raise ValueError(
                f"Split frame {at:g} is not inside {shot.name} "
                f"[{shot.start:g}-{shot.end:g}]"
            )

        tail_end = shot.end
        tail_name = name or self._unique_shot_name(f"{shot.name}_2")

        self.store.update_shot(shot_id, end=at)
        # The tail INHERITS the shot's object list and is then narrowed to
        # what actually animates in it.  Seeding it empty instead makes
        # ``collect_object_segments`` fall back to a scene-wide probe for
        # keyed transforms, which adopts objects this shot never claimed.
        tail = self.define_shot(
            name=tail_name,
            start=at,
            end=tail_end,
            objects=list(shot.objects),
            description=shot.description,
        )
        self._recompute_shot_objects(shot_id)
        self._recompute_shot_objects(tail.shot_id)

        gap = float(gap)
        if abs(gap) > _EPS:
            plan = ShotPlanner.plan_ripple_downstream(self.store, -1, at, gap)
            if plan.sequence or plan.parked:
                self._apply(plan)

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return self.shot_by_id(tail.shot_id)

    def _leading_room(self, shot_id: int) -> float:
        """Empty frames between a shot's start and its first piece of content.

        Zero when the shot is empty -- there is no room to reclaim from a shot
        that holds nothing, and treating its whole span as slack would let a
        pad silently resize it to a point.
        """
        sequences = self.collect_shot_sequences(shot_id)
        if not sequences:
            return 0.0
        shot = self.shot_by_id(shot_id)
        return max(0.0, min(s["start"] for s in sequences) - shot.start)

    def add_shot_space(
        self, shot_id: int, frames: float, edge: str = "leading"
    ) -> Tuple[float, float]:
        """Insert empty room at a shot's head and/or tail, rippling downstream.

        Both edges open room *forward in time* -- the shot's start is an
        anchor, never something padding drags backwards:

        * ``"leading"`` -- the start stays exactly where it is and everything
          from it onward shifts later by *frames*: this shot's own keys and
          audio, its end, and every downstream shot.
        * ``"trailing"`` -- the end moves later by *frames* and the downstream
          shots follow; the shot's own content stays put.
        * ``"both"`` -- each of the above, so the shot grows by ``2 * frames``
          and its content sits *frames* further along.

        Spacing between shots is preserved, so the padding is new room rather
        than an existing gap being eaten.  A negative *frames* removes room.

        Returns ``(head_delta, tail_delta)`` -- the head delta is always 0 for
        a leading pad: that is the point.

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        frames = float(frames)
        if abs(frames) < _EPS:
            return 0.0, 0.0

        head = frames if edge in ("leading", "both") else 0.0
        tail = frames if edge in ("trailing", "both") else 0.0
        if head == 0.0 and tail == 0.0:
            return 0.0, 0.0

        old_start, old_end = shot.start, shot.end
        if head < 0:
            # Removing head room pulls the content back toward the anchored
            # start, so it may only reclaim room that is actually EMPTY --
            # past that it would drag keys out through the head and into the
            # upstream gap, which is a delete dressed up as a pad.
            head = -min(-head, self._leading_room(shot_id))
            if abs(head) < _EPS and abs(tail) < _EPS:
                return 0.0, 0.0
        # A shot may not be padded into nothing; both ends push the tail out,
        # so the guard is on the end alone.
        if self.store.snap(old_end + head + tail) <= old_start:
            return 0.0, 0.0

        if abs(head) > _EPS:
            # Slide the shot bodily downstream, then put the start back: the
            # content and every following shot end up *frames* later while the
            # head holds -- exactly "empty room at the front".  ``slide_shot``
            # owns the ordering that keeps the pivot's keys out of a
            # neighbour's not-yet-read envelope.
            self.slide_shot(
                shot_id, old_start + head, direction="downstream", _enforce=False
            )
            shot.start = old_start
        if abs(tail) > _EPS:
            pre_tail_end = shot.end
            shot.end = self.store.snap(pre_tail_end + tail)
            # Ripple by what the END ACTUALLY moved rather than by the amount
            # asked for (the value ``fit_shot_to_content`` passes).  The plan
            # snaps its own destinations, so the two agree today; deriving it
            # from the bound keeps them agreeing if either side's rounding
            # ever changes.
            self.ripple_downstream(shot_id, pre_tail_end, shot.end - pre_tail_end)

        head_delta = shot.start - old_start
        tail_delta = shot.end - old_end
        # Holds are left to the reconcile below rather than enforced per step:
        # the head slide is asked NOT to enforce so the seams are read once,
        # after the start has been put back.
        self.reconcile_system_edits()
        self.store.mark_dirty()
        return head_delta, tail_delta

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
        # Same inversion normalization as resize_shot_bounds.
        if new_end < new_start:
            new_start, new_end = new_end, new_start
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
            # reconcile, not just enforce: this moved a shot BOUND, which is
            # exactly when a boundary sample the system created has to follow
            # it or be cleaned up.
            self.reconcile_system_edits()
        self.store.mark_dirty()

    def resize_shot_bounds(
        self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True
    ) -> None:
        """Move a shot's boundaries WITHOUT touching its keyframes.

        Counterpart to :meth:`resize_shot`: same envelope bookkeeping, but the
        shot's own content stays where the animator put it.  Dragging a shot
        edge means "this shot covers a different span"; retiming is the
        Shift-modified gesture.  A shrink can therefore leave keys outside the
        new bounds — they are not deleted, they simply stop counting as this
        shot's content until a boundary covers them again.

        EVERY edge move ripples the neighbours on that side by the same
        delta, so a gap keeps its width unless it is the thing being dragged:
        growing pushes them away, shrinking pulls them in behind the bound.
        A shrink used to leave them, which silently widened the adjacent gap
        on every resize.  The ripple runs BEFORE the pivot's bounds are
        written, in both directions: a neighbour's move window is bounded by
        the pivot's boundary, so while that is still the OLD one the window
        cannot reach the keys a shrink is about to strand.  (Mirrors mayatk.)
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        if new_end < new_start:
            new_start, new_end = new_end, new_start
        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < _EPS and abs(new_end - old_end) < _EPS:
            return
        tail_delta = new_end - old_end
        head_delta = new_start - old_start
        # Ripple BEFORE the pivot's bounds are written, in both directions
        # (see the docstring).
        if abs(tail_delta) > _EPS:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        if abs(head_delta) > _EPS:
            self.ripple_upstream(shot_id, old_start, head_delta)
        shot.start = new_start
        shot.end = new_end
        if _enforce:
            # reconcile, not just enforce: this moved a shot BOUND, which is
            # exactly when a boundary sample the system created has to follow
            # it or be cleaned up.
            self.reconcile_system_edits()
        self.store.mark_dirty()

    def insert_shot(
        self,
        name: str,
        duration: float,
        after_shot_id: Optional[int] = None,
        at_position: Optional[int] = None,
        gap: Optional[float] = None,
        objects: Optional[List[str]] = None,
        description: str = "",
    ):
        """Create a shot BETWEEN existing shots, pushing later ones downstream.

        Appending was the only way to add a shot, so making room in the middle
        meant hand-rippling every following shot.  This opens the space first —
        every shot at or after the insertion point (and its keyframes and audio)
        moves by ``duration + gap`` — then defines the new shot in the hole.

        Pass either *after_shot_id* or a 1-based *at_position*; with neither the
        shot is appended (after the last shot's trailing envelope content —
        fade tails and trailing audio are never built over).  The *gap*
        falls between the preceding shot's content and the new shot;
        downstream shots ripple rigidly by ``duration + gap``.  Raises
        ``ValueError`` for an unknown *after_shot_id*.
        """
        gap = self.store.gap if gap is None else gap
        shots = self.sorted_shots()

        if after_shot_id is not None:
            idx = next(
                (i for i, s in enumerate(shots) if s.shot_id == after_shot_id), None
            )
            if idx is None:
                raise ValueError(f"No shot with id {after_shot_id}")
            insert_idx = idx + 1
        elif at_position is not None:
            insert_idx = max(0, min(int(at_position) - 1, len(shots)))
        else:
            insert_idx = len(shots)

        if not shots:
            start = self.store.snap(1.0)
        elif insert_idx == 0:
            start = shots[0].start
        elif insert_idx == len(shots):
            # Appending after the LAST shot: clear its trailing envelope
            # content (fade tails, trailing audio) before placing.
            prev = shots[-1]
            start = self.store.snap(
                max(prev.end, self._trailing_content_extent(prev)) + gap
            )
        else:
            start = self.store.snap(shots[insert_idx - 1].end + gap)
        new_end = self.store.snap(start + duration)

        # Open the hole before the shot exists, so the ripple can't pick up the
        # new shot as one of the shots it should move.  A pivot id no shot owns
        # means "shift everything at or after the frame".
        if insert_idx < len(shots):
            delta = (new_end - start) + gap
            if abs(delta) > _EPS:
                self._apply(
                    ShotPlanner.plan_ripple_downstream(
                        self.store, -1, shots[insert_idx].start, delta
                    )
                )

        block = self.define_shot(
            name=name,
            start=start,
            end=new_end,
            objects=objects if objects is not None else [],
            description=description,
        )
        self._enforce_gap_holds()
        self.store.mark_dirty()
        return block

    def set_shot_start(
        self, shot_id: int, new_start: float, ripple: bool = True
    ) -> None:
        """Move a shot to *new_start*; with *ripple* downstream shots shift by the same delta."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        new_start = self._clamp_slide_start(
            shot, new_start, "downstream" if ripple else None
        )
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
        # reconcile, not just enforce: this moved a shot BOUND, which is
        # exactly when a boundary sample the system created has to follow
        # it or be cleaned up.
        self.reconcile_system_edits()
        self.store.mark_dirty()

    def move_shot_to_position(self, shot_id: int, target_pos: int) -> None:
        """Reorder *shot_id* to 1-based timeline position *target_pos*.

        Durations are preserved; gaps use the store's gap setting (locked gaps
        honoured).  Keys and audio travel through the planner's park/land phases.

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        if self.shot_by_id(shot_id) is None:
            raise ValueError(f"No shot with id {shot_id}")
        plan = ShotPlanner.plan_reorder(self.store, shot_id, target_pos, self.store.gap)
        # Same no-op guard as mayatk: an all-sub-EPS plan still has ``moves``
        # entries but nothing in sequence/parked — running the epilogue
        # would restep interpolation and dirty the store for nothing.
        if not plan.sequence and not plan.parked:
            return
        self._apply(plan)
        self._enforce_gap_holds()
        self.store.mark_dirty()

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
