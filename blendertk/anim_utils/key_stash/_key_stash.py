# !/usr/bin/python
# coding=utf-8
"""Key Stash — park keyframes outside the working animation, retrieve later (Blender).

Mirror of mayatk's ``anim_utils.key_stash`` (name + behavior; the mechanism is
Blender's).

**Storage.** One orphan :class:`bpy.types.Action` per clip, ``use_fake_user``
so it survives save/reload, holding a copy of the stashed keys in a slot per
object (4.4+ slotted actions: ``layers → strips → channelbag(slot)``).  An
action assigned to nothing is evaluated by nothing, and the FBX exporter is
run with ``bake_anim_use_all_actions`` off (``handoff_export`` /
``fbx_utils``), so a clip that is never retrieved has zero effect on the scene
or an export.  Verified live (Blender 5.1): evaluation identical to a plain
delete, one action in the FBX round-trip, none named after the stash.

**Record.** The clip manifest (:class:`pythontk.KeyStash`) rides the scene
custom property ``scene["key_stash"]`` through :class:`BlenderScenePersistence`,
beside the shot store's ``shot_store``.  Copy-before-cut: the manifest and the
stash action exist before a single live key is removed.  Objects are tracked
by NAME — Blender has no node UUID — so a renamed object orphans its record;
the clip stays and is retrieved onto a *target*.

**Preview.** Transient NLA tracks (:meth:`AnimUtils.create_preview_layer`):
in-context pushes the live action down under a REPLACE strip of the stash
action; isolated solos the stash strip.  Ending removes the tracks and hands
the live action back.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pythontk.core_utils.engines.key_stash.key_stash_model import (
    KeyStash as _KeyStashCore,
    StashChanged,
    StashedClip,
)

from blendertk.anim_utils._anim_utils import AnimUtils, _AnimUtilsInternal
from blendertk.anim_utils.shots._shots import (
    BlenderScenePersistence,
    BlenderShotStore,
    _BlenderShotStoreInternal,
)
from blendertk.core_utils._core_utils import CoreUtils

_log = logging.getLogger(__name__)


class _KeyStashInternal(object):
    """Scene-side helpers for :class:`KeyStash`."""

    STASH_ACTION_PREFIX = "keyStash_"
    PREVIEW_NAME = "keyStashPreview"
    #: Retrieve modes (mirror of Maya's ``pasteKey -option`` subset).
    PASTE_OPTIONS = ("merge", "replace", "insert")
    _EPS = 1e-6

    # ---- objects / actions ------------------------------------------------

    @staticmethod
    def _as_object(obj):
        import bpy

        return bpy.data.objects.get(obj) if isinstance(obj, str) else obj

    @staticmethod
    def _live(obj, create: bool = False):
        """``(action, slot)`` the object animates through; created on demand."""
        ad = obj.animation_data
        if ad is not None and ad.action is not None:
            slot = ad.action_slot
            if slot is None and create:
                slots = ad.action.slots
                slot = slots[0] if len(slots) else slots.new(obj.id_type, obj.name)
                ad.action_slot = slot
            return ad.action, slot
        if not create:
            return None, None
        import bpy

        ad = ad or obj.animation_data_create()
        action = bpy.data.actions.new(f"{obj.name}Action")
        slot = action.slots.new(obj.id_type, obj.name)
        ad.action = action
        ad.action_slot = slot
        return action, slot

    @staticmethod
    def _slot_by_identifier(action, identifier: Optional[str]):
        for slot in action.slots:
            if slot.identifier == identifier:
                return slot
        return None

    @staticmethod
    def _ensure_channelbag(action, slot):
        """The channelbag of *slot* in *action*'s first keyframe strip (created on demand)."""
        for layer in action.layers:
            for strip in layer.strips:
                return strip.channelbag(slot, ensure=True)
        layer = action.layers.new("Layer")
        strip = layer.strips.new(type="KEYFRAME")
        return strip.channelbag(slot, ensure=True)

    @classmethod
    def _stash_channelbag(cls, action, rec: Dict[str, Any]):
        """The stash action's channelbag holding *rec*'s slot, or ``None``."""
        if action is None:
            return None
        slot = cls._slot_by_identifier(action, rec.get("slot"))
        if slot is None:
            return None
        for layer in action.layers:
            for strip in layer.strips:
                cb = strip.channelbag(slot)
                if cb is not None:
                    return cb
        return None

    @classmethod
    def _stash_fcurve(cls, action, rec: Dict[str, Any]):
        """``(channelbag, fcurve)`` of *rec* in the stash action, or ``(None, None)``."""
        cb = cls._stash_channelbag(action, rec)
        if cb is None:
            return None, None
        return cb, cb.fcurves.find(rec["data_path"], index=int(rec["array_index"]))

    # ---- keys -------------------------------------------------------------

    @classmethod
    def _copy_keys(
        cls, src_fc, dst_fc, times: Sequence[float], offset: float = 0.0
    ) -> int:
        """Insert *src_fc*'s keys at *times* into *dst_fc* shifted by *offset*; count."""
        keep = {round(float(t), 6) for t in times}
        n = 0
        for k in src_fc.keyframe_points:
            if round(k.co.x, 6) not in keep:
                continue
            kp = dst_fc.keyframe_points.insert(
                k.co.x + offset, k.co.y, options={"FAST"}
            )
            kp.interpolation = k.interpolation
            kp.easing = k.easing
            kp.type = k.type
            kp.handle_left_type = k.handle_left_type
            kp.handle_right_type = k.handle_right_type
            kp.handle_left = (k.handle_left.x + offset, k.handle_left.y)
            kp.handle_right = (k.handle_right.x + offset, k.handle_right.y)
            n += 1
        dst_fc.update()
        return n

    @classmethod
    def _remove_keys(cls, fc, times: Sequence[float]) -> None:
        keep = {round(float(t), 6) for t in times}
        for k in reversed([k for k in fc.keyframe_points if round(k.co.x, 6) in keep]):
            fc.keyframe_points.remove(k)
        fc.update()

    @classmethod
    def _remove_keys_in(cls, fc, lo: float, hi: float) -> None:
        for k in reversed(
            [k for k in fc.keyframe_points if lo - cls._EPS <= k.co.x <= hi + cls._EPS]
        ):
            fc.keyframe_points.remove(k)
        fc.update()

    @classmethod
    def _shift_keys_from(cls, fc, start: float, delta: float) -> None:
        for k in fc.keyframe_points:
            if k.co.x >= start - cls._EPS:
                k.co.x += delta
                k.handle_left.x += delta
                k.handle_right.x += delta
        fc.update()

    @staticmethod
    def _matches_attribute(fc, attributes: Sequence[str]) -> bool:
        return (
            fc.data_path in attributes
            or f"{fc.data_path}[{fc.array_index}]" in attributes
        )

    # ---- playback -----------------------------------------------------------

    @staticmethod
    def _capture_playback() -> List[Any]:
        import bpy

        scene = bpy.context.scene
        return [
            bool(scene.use_preview_range),
            int(scene.frame_preview_start),
            int(scene.frame_preview_end),
        ]

    @staticmethod
    def _restore_playback(payload: Dict[str, Any]) -> None:
        import bpy

        rng = payload.get("playback")
        if not rng or len(rng) != 3:
            return
        scene = bpy.context.scene
        scene.use_preview_range = bool(rng[0])
        scene.frame_preview_start = int(rng[1])
        scene.frame_preview_end = int(rng[2])


class KeyStash(_KeyStashCore, _KeyStashInternal):
    """:class:`pythontk.KeyStash` with the scene side bound to Blender.

    ``KeyStash.active()`` auto-installs :class:`BlenderScenePersistence` on the
    ``key_stash`` channel, loads any clips saved in the ``.blend``, prunes
    records whose stash action is gone and tears down a preview left over from
    a save made mid-preview.

    Every scene operation — :meth:`stash`, :meth:`retrieve`, :meth:`drop`,
    :meth:`preview`, :meth:`end_preview` — is ONE undo step.
    """

    ATTR_NAME = "key_stash"
    _flush_pending: bool = False

    # ---- singleton / hooks ---------------------------------------------

    @classmethod
    def active(cls) -> "KeyStash":
        """The active store, auto-installing the Blender backend once."""
        if cls._active is None and cls._persistence is None:
            try:
                import bpy  # noqa: F401
            except ImportError:
                pass
            else:
                cls.set_persistence(
                    BlenderScenePersistence(attr_name=cls.ATTR_NAME, store_cls=cls)
                )
        return super().active()  # type: ignore[return-value]

    def _scene_fps(self) -> float:
        return _BlenderShotStoreInternal._get_scene_fps()

    def rescale_to_fps(self, new_fps: float) -> None:
        """Record the new rate only.

        Blender keys live in frames and stay put when the frame rate changes,
        so the stashed times need no rescale (the Maya twin's do — its curve
        keys live in ticks).
        """
        if abs(float(new_fps) - self.scene_fps) < 0.01:
            return
        self.scene_fps = float(new_fps)
        self.mark_dirty()

    def _schedule_flush(self) -> None:
        """Coalesce rapid mutations into one deferred write (mirror of the shot store)."""
        try:
            import bpy
        except ImportError:
            self._flush_dirty()
            return
        if bpy.app.background:
            self._flush_dirty()
            return
        if self._flush_pending:
            return
        self._flush_pending = True

        def _run():
            self._flush_pending = False
            self._flush_dirty()
            return None  # one-shot

        try:
            bpy.app.timers.register(_run, first_interval=0.0)
        except Exception:
            self._flush_pending = False
            self._flush_dirty()

    def _on_activated(self) -> None:
        self.reconcile()

    def reconcile(self) -> List[int]:
        """Bring the record in line with the file.

        Drops every clip whose stash action is gone and ends a preview the
        record says is active (a file saved mid-preview must not reopen
        silently overridden).

        Returns:
            The ids of the pruned clips.
        """
        try:
            import bpy
        except ImportError:
            return []
        gone: List[int] = []
        for clip in list(self.clips):
            if not any(
                bpy.data.actions.get(rec.get("action") or "") for rec in clip.curves
            ):
                self.clips.remove(clip)
                gone.append(clip.clip_id)
        changed = bool(gone)
        if self.active_preview:
            AnimUtils.remove_preview_layer(self.active_preview.get("handle"))
            self._restore_playback(self.active_preview)
            self.active_preview = None
            changed = True
        if changed:
            self.mark_dirty()
            self._notify(StashChanged("reloaded"))
        return gone

    # ---- operations ----------------------------------------------------

    def stash(
        self,
        objects=None,
        time_range: Optional[Tuple[float, float]] = None,
        selected_keys: bool = False,
        attributes: Optional[Sequence[str]] = None,
        fcurves=None,
        label: Optional[str] = None,
        source_shot_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[StashedClip]:
        """Move keys off the working animation into a stored clip.

        Two selection sources, as in mayatk:

        * ``selected_keys=True`` — the Graph Editor / Dope Sheet key selection,
          per fcurve (optionally narrowed to *objects*).
        * otherwise *objects* (default: the selected objects) and *time_range* —
          every key inside the inclusive range, optionally only on *attributes*
          (``"location"`` or ``"location[0]"`` forms) or only the given
          *fcurves* (Blender-idiomatic: the sequencer already knows its curves).

        The gap the keys leave is left as-is (neighbours interpolate across it).

        Returns:
            The new clip, or ``None`` when the source held no keys.

        Raises:
            ValueError: Range mode without a range, or nothing to stash from.
        """
        import bpy

        entries: List[Tuple[Any, Any, List[float]]] = []
        if selected_keys:
            objs = (
                [self._as_object(o) for o in objects]
                if objects
                else [
                    o
                    for o in bpy.data.objects
                    if o.animation_data is not None
                    and o.animation_data.action is not None
                ]
            )
            for o in objs:
                if o is None:
                    continue
                for fc in BlenderShotStore.iter_action_fcurves(o):
                    times = sorted(
                        {k.co.x for k in fc.keyframe_points if k.select_control_point}
                    )
                    if times:
                        entries.append((o, fc, times))
        else:
            objs = (
                [self._as_object(o) for o in objects]
                if objects
                else list(bpy.context.selected_objects)
            )
            objs = [o for o in objs if o is not None]
            if not objs:
                raise ValueError("stash: no objects given or selected")
            if time_range is None:
                raise ValueError("stash: time_range is required unless selected_keys")
            lo, hi = float(time_range[0]), float(time_range[1])
            for o in objs:
                cands = (
                    list(fcurves)
                    if fcurves is not None
                    else list(BlenderShotStore.iter_action_fcurves(o))
                )
                if attributes:
                    cands = [
                        fc for fc in cands if self._matches_attribute(fc, attributes)
                    ]
                for fc in cands:
                    times = sorted(
                        {
                            k.co.x
                            for k in fc.keyframe_points
                            if lo - self._EPS <= k.co.x <= hi + self._EPS
                        }
                    )
                    if times:
                        entries.append((o, fc, times))
        if not entries:
            return None

        with CoreUtils.undo_chunk("Store Keys"):
            action = bpy.data.actions.new(f"{self.STASH_ACTION_PREFIX}{self._next_id}")
            action.use_fake_user = True
            slots: Dict[str, Any] = {}
            records: List[Dict[str, Any]] = []
            owners: List[str] = []
            for o, fc, times in entries:
                slot = slots.get(o.name)
                if slot is None:
                    slot = slots[o.name] = action.slots.new(o.id_type, o.name)
                cb = self._ensure_channelbag(action, slot)
                dst = cb.fcurves.find(fc.data_path, index=fc.array_index)
                if dst is None:
                    dst = cb.fcurves.new(fc.data_path, index=fc.array_index)
                self._copy_keys(fc, dst, times)
                records.append(
                    {
                        "times": [float(t) for t in times],
                        "object": o.name,
                        "data_path": fc.data_path,
                        "array_index": int(fc.array_index),
                        "action": action.name,
                        "slot": slot.identifier,
                    }
                )
                if o.name not in owners:
                    owners.append(o.name)
            clip = self.add_clip(
                owners,
                records,
                label=label,
                source_shot_id=source_shot_id,
                metadata=metadata,
            )
            # Copy-before-cut: manifest + stash action exist before a live key goes.
            self.save()
            for o, fc, times in entries:
                self._remove_keys(fc, times)
                if not len(fc.keyframe_points):
                    live_action, live_slot = self._live(o)
                    if live_action is not None:
                        _AnimUtilsInternal._remove_fcurve(live_action, live_slot, fc)
        return clip

    def retrieve(
        self,
        clip_id: int,
        at: Optional[float] = None,
        mode: str = "merge",
        target: Optional[str] = None,
    ) -> int:
        """Put a stored clip's keys back and forget the clip.

        Parameters:
            clip_id: The clip.
            at: Frame the clip's first key lands on; ``None`` = original frames.
            mode: ``"merge"`` (a key already at a pasted frame is replaced),
                ``"replace"`` (existing keys inside the pasted range go first),
                ``"insert"`` (keys at or after the paste point shift right by the
                clip's length).
            target: Object name to paste onto instead of the recorded one.

        Returns:
            Number of keys restored.  Records whose object no longer exists stay
            in the clip so it can be retrieved onto a *target*.

        Raises:
            KeyError: Unknown *clip_id*.  ValueError: unknown *mode*.
        """
        import bpy

        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if mode not in self.PASTE_OPTIONS:
            raise ValueError(f"mode must be one of {self.PASTE_OPTIONS}, got {mode!r}")
        if self.is_previewing(clip_id):
            self.end_preview()
        offset = self.offset_for(clip, at)
        restored = 0
        remaining: List[Dict[str, Any]] = []
        problems: List[str] = []
        actions_touched = {rec.get("action") for rec in clip.curves}
        with CoreUtils.undo_chunk("Retrieve Stored Keys"):
            for rec in clip.curves:
                action = bpy.data.actions.get(rec.get("action") or "")
                cb_src, src = self._stash_fcurve(action, rec)
                if src is None:
                    problems.append(
                        f"stash curve for {rec.get('object')}.{rec.get('data_path')} is gone"
                    )
                    continue
                obj = bpy.data.objects.get(target or rec.get("object") or "")
                if obj is None:
                    remaining.append(rec)
                    problems.append(
                        f"object {rec.get('object')!r} no longer exists — record kept; "
                        "retrieve onto a target"
                    )
                    continue
                live_action, live_slot = self._live(obj, create=True)
                cb = self._ensure_channelbag(live_action, live_slot)
                dst = cb.fcurves.find(rec["data_path"], index=int(rec["array_index"]))
                if dst is None:
                    dst = cb.fcurves.new(
                        rec["data_path"], index=int(rec["array_index"])
                    )
                times = [float(t) for t in rec.get("times", ())]
                if times:
                    lo, hi = min(times) + offset, max(times) + offset
                    if mode == "replace":
                        self._remove_keys_in(dst, lo, hi)
                    elif mode == "insert":
                        self._shift_keys_from(dst, lo, (hi - lo) or 1.0)
                    restored += self._copy_keys(src, dst, times, offset)
                cb_src.fcurves.remove(
                    src
                )  # consumed; _gc_actions drops an emptied action
            if remaining:
                clip.curves = remaining
                self.mark_dirty()
            else:
                self.remove_clip(clip_id, kind="retrieved")
            self._gc_actions(actions_touched)
            self.save()
        for msg in problems:
            _log.warning("KeyStash.retrieve: %s", msg)
        return restored

    def drop(self, clip_id: int) -> None:
        """Discard a stored clip and delete its stash action.

        Raises:
            KeyError: Unknown *clip_id*.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if self.is_previewing(clip_id):
            self.end_preview()
        actions = {rec.get("action") for rec in clip.curves}
        with CoreUtils.undo_chunk("Drop Stored Keys"):
            self.remove_clip(clip_id, kind="dropped")
            self._gc_actions(actions)
            self.save()

    def _gc_actions(self, names) -> None:
        """Delete the stash actions in *names* that no remaining record references."""
        import bpy

        referenced = {rec.get("action") for c in self.clips for rec in c.curves}
        for name in names:
            if name and name not in referenced:
                action = bpy.data.actions.get(name)
                if action is not None:
                    bpy.data.actions.remove(action)

    # ---- preview -------------------------------------------------------

    def is_previewing(self, clip_id: Optional[int] = None) -> bool:
        """Whether a preview is active (for *clip_id*, when given)."""
        if not self.active_preview:
            return False
        return clip_id is None or self.active_preview.get("clip_id") == clip_id

    def preview(
        self,
        clip_id: int,
        in_context: bool = True,
        set_playback_range: bool = True,
    ) -> Dict[str, Any]:
        """Play a stored clip on its objects without retrieving it.

        Parameters:
            clip_id: The clip.
            in_context: The scene animation plays outside the clip's range and
                the clip takes over inside it.  ``False`` solos the clip and holds
                its end poses outside its keys.
            set_playback_range: Set the timeline's preview range to the clip
                (restored by :meth:`end_preview`).

        Returns:
            The preview handle (also kept in :attr:`active_preview`).

        Raises:
            KeyError: Unknown *clip_id*.  RuntimeError: none of the clip's
            objects are in the scene.
        """
        import bpy

        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if self.active_preview:
            self.end_preview()
        sources: Dict[str, Tuple[Any, Any]] = {}
        for rec in clip.curves:
            name = rec.get("object")
            if name in sources or bpy.data.objects.get(name or "") is None:
                continue
            action = bpy.data.actions.get(rec.get("action") or "")
            slot = self._slot_by_identifier(action, rec.get("slot")) if action else None
            if action is not None and slot is not None:
                sources[name] = (action, slot)
        if not sources:
            raise RuntimeError("preview: none of the clip's objects are in the scene")
        payload: Dict[str, Any] = {"in_context": bool(in_context)}
        with CoreUtils.undo_chunk("Preview Stored Keys"):
            handle = AnimUtils.create_preview_layer(
                sources,
                gate=self.gate_range(clip) if in_context else None,
                name=self.PREVIEW_NAME,
            )
            payload["handle"] = handle
            if set_playback_range and clip.start is not None:
                payload["playback"] = self._capture_playback()
                scene = bpy.context.scene
                scene.use_preview_range = True
                scene.frame_preview_start = int(math.floor(clip.start))
                scene.frame_preview_end = int(math.ceil(clip.end))
        self.set_preview(clip_id, payload)
        self.save()
        return handle

    def end_preview(self) -> bool:
        """Tear the preview tracks down and restore the preview range.

        Returns:
            ``True`` if a preview was active.
        """
        payload = self.clear_preview()
        if payload is None:
            return False
        with CoreUtils.undo_chunk("End Stored Keys Preview"):
            AnimUtils.remove_preview_layer(payload.get("handle"))
            self._restore_playback(payload)
        self.save()
        return True
