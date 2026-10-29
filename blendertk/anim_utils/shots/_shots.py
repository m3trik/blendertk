# !/usr/bin/python
# coding=utf-8
"""Blender shots host — the DCC layer under ``pythontk``'s one shot store.

Mirror of mayatk's ``anim_utils.shots._shots`` at the class/behavior level
(:class:`BlenderShotsHost`, :class:`BlenderScenePersistence`).  All of the shot
*model*, planning, detection *math* and the hand-off transfer live once in
``pythontk.core_utils.engines.shots`` (the DCC-agnostic engine);
``btk.ShotStore`` IS ``ptk.ShotStore``.  This module is only the thin Blender
**acquisition + persistence** layer it is injected with:

- :class:`BlenderScenePersistence` stores the serialized store as the
  ``shot_store`` record on the private carrier (``btk.DataNodes``, a scene ID
  property group), so it rides the ``.blend`` file and is never exported.
- :class:`BlenderShotsHost` is the store's :class:`pythontk.ShotsHost`: the
  scene reads (gathering fcurve segments / selected keys from the live scene
  for the pure :meth:`~pythontk.ShotDetection.cluster_segments_by_gap` /
  :meth:`~pythontk.ShotDetection.boundaries_from_key_entries` boundary math),
  the undo steps and their edit serials, the rename follow, the deferred flush,
  the export carrier and the transfer's curve naming.  It is registered as this
  module is imported, so ``btk.ShotStore.active()`` binds Blender with no entry
  point having run.

Divergence from mayatk (by design):
    * **Slotted-action fcurve access.** Blender 4.4+ removed the flat
      ``Action.fcurves`` accessor (fully gone in 5.1); fcurves live under
      ``action.layers[*].strips[*].channelbag(slot).fcurves`` where the slot is
      ``obj.animation_data.action_slot``.  :meth:`AnimUtils.get_fcurves` is the
      single walk that yields an object's fcurves across that structure.
    * **Motion filtering is per-fcurve value-variance**, not Maya's
      ``SegmentKeys`` static-interval splitting (Blender has no equivalent).  A
      transform channel whose values never change across its keys is treated as
      a held/flat channel and excluded; an object with only flat channels
      contributes no segment.  Held sub-intervals *within* a moving channel are
      not split out — coarser than Maya but correct for boundary detection.
    * **Export-view projection targets the carrier Empty.**
      :meth:`publish_export_view` mirrors the Maya original — the same
      ``animation`` JSON channel from the same
      :meth:`~pythontk.ShotStore.to_export_view` pass — written as custom
      properties on the ``data_export`` Empty (:class:`blendertk.node_utils.
      data_nodes.DataNodes`) instead of string attrs on a Maya transform.
      Take realization diverges too: no before-export hook exists in bpy, so
      the Scene Exporter's ``apply_declared_takes`` task is what arms
      ``FbxUtils`` with the declared takes (see that module's docstring).
    * **Cross-scene prefs** use the engine's zero-dep JSON store (pythontk user
      config), not QSettings — inherited unchanged from the base.  The Maya
      twin's one-time QSettings → JSON migration is N/A (Blender never had
      QSettings prefs), as is its legacy ``shotStore`` carrier-node fold.
    * **Framerate-change hook rides ``bpy.msgbus``.** Maya's ``timeUnitChanged``
      scriptJob has no ``bpy.app.handlers`` analogue; the persistence backend
      subscribes ``RenderSettings.fps`` / ``fps_base`` via ``bpy.msgbus``
      (owner = the backend, so ``remove_callbacks`` clears it).  msgbus
      subscriptions are dropped on file load, so the backend re-subscribes in
      its ``SceneOpened`` handler — which also makes Maya's
      ``MFileIO.isReadingFile()`` guard unnecessary: nothing can fire mid-load.
      Python-side writes (``scene.render.fps = 30``) do not notify msgbus
      (Blender only publishes RNA edits from the UI / operators); the hook is
      public as :meth:`BlenderScenePersistence._on_time_unit_changed` for
      callers that change the framerate from script.
    * **A frame-rate change records the rate and moves nothing.** Blender
      keys stay on their FRAMES when the rate changes (Maya's keep real time,
      so the Maya store rescales); the shots and the ledger's claims stay with
      the keys they bound -- ``ShotStore.rescale_to_fps`` over :attr:`BlenderShotsHost.keys_keep_time`, the key
      stash's rule too.
    * **Deferred flush uses ``bpy.app.timers``** in a GUI session (one
      coalesced write per burst of mutations, mirror of ``cmds.evalDeferred``).
      In ``--background`` the timer loop never runs, so the flush is immediate.
    * **Export preparer hooks are static.** bpy has no before-FBX-export event;
      ``FbxUtils.PRODUCERS`` already routes every Scene
      Exporter write through :meth:`~pythontk.ShotStore.produce_export_records`,
      so the base's ``_register_export_preparer`` / ``_unregister_export_preparer``
      no-ops are the Blender twins of Maya's session-hook install/remove.
"""

import contextlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk

# The one store, re-exported: ``btk.ShotStore``.
from pythontk import ShotStore

from blendertk.anim_utils.shots._detection import Detection
from blendertk.anim_utils._anim_utils import AnimUtils, _VISIBILITY_PATHS
from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

_log = logging.getLogger(__name__)

_DEFAULT_FPS = 24.0

#: Object-transform animation channels (top-level object *and* pose-bone forms).
#: A pose-bone channel data_path is e.g. ``pose.bones["Arm"].location``, so the
#: predicate below matches both the bare path and the ``.<channel>`` suffix.
_TRANSFORM_CHANNELS: Tuple[str, ...] = (
    "location",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "scale",
)

#: A pose-bone channel's path: ``pose.bones["<name>"]`` (the name escaped as
#: ``bpy.utils.escape_identifier`` writes it), then the rest of the path.
_BONE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]')

#: Where a ledger key's slot field may sit (:meth:`BlenderShotsHost.curve_key`):
#: a ``|`` followed by a slot handle (digits; empty for a legacy action) and
#: ``|`` -- matched zero-width, so a name holding ``|`` yields every candidate.
_SLOT_FIELD = re.compile(r"\|(?=(\d*)\|)")

__all__ = ["BlenderShotsHost", "BlenderScenePersistence", "Detection", "ShotStore"]

# ---------------------------------------------------------------------------
# Persistence backend
# ---------------------------------------------------------------------------


class BlenderScenePersistence:
    """Persist the store as a JSON string in a private scene record.

    Implements the :class:`pythontk.ScenePersistence` protocol
    (``save(data)`` / ``load() -> dict | None``).  The record lives in the
    active scene's ``data_internal`` property group (``DataNodes``, private
    scope), so it survives save/reopen with the ``.blend`` and -- being scene
    ID data, not an object -- never leaks into an FBX/glTF export.

    Mirror of mayatk's ``MayaScenePersistence`` scene jobs, registered via
    :class:`ScriptJobManager`:

    * ``SceneOpened`` → :meth:`_on_scene_changed` invalidates
      :attr:`ShotStore._active` — without it the previous file's store
      would stay active and its next save would write the OLD file's shots
      JSON into the NEW scene's ``shot_store`` property.
    * ``SceneBeforeSave`` → :meth:`_on_before_save` flushes a dirty store
      before the ``.blend`` is written (the deferred-flush timer may not have
      fired yet).
    * ``RenderSettings.fps`` / ``fps_base`` (``bpy.msgbus``) →
      :meth:`_on_time_unit_changed` hands the new framerate to the store's
      ``rescale_to_fps`` (which, for the shot store, records it and moves
      nothing: Blender keys stay on their frames).

    The manager's master handlers are ``@persistent``, so the subscriptions
    survive File ▸ New/Open; the msgbus subscription does not and is renewed
    from :meth:`_on_scene_changed`.
    """

    #: The private record carrying the serialized store -- the declaration's
    #: key, never a second spelling of it.
    ATTR_NAME = ptk.SceneRecords.SHOT_STORE.key
    #: The record as this backend last wrote or read it (:meth:`record_changed`).
    _last_raw: Optional[str] = None
    #: :meth:`load`'s decode default: distinguishes "not JSON" from a payload.
    _UNREADABLE = object()

    def __init__(self, attr_name: Optional[str] = None, store_cls=None):
        """
        Parameters:
            attr_name: The private record's key (default ``shot_store``).
            store_cls: The active-store class this backend serves — invalidated
                on file load, flushed before save, told of a frame-rate
                change.  Defaults to :class:`ShotStore`; the key stash
                passes its own class so both stores ride the scene on separate
                channels without reacting to each other's events (mirror of
                mayatk's ``MayaScenePersistence(store_cls=...)``).
        """
        self._attr_name = attr_name or self.ATTR_NAME
        self._store_cls = store_cls
        self._scene_subs_installed = False
        self._fps_sub_installed = False
        self._install_scene_jobs()

    @property
    def store_cls(self):
        """The store class this backend serves (``ShotStore`` unless the key
        stash passed its own)."""
        return self._store_cls if self._store_cls is not None else ShotStore

    # ---- scene lifecycle subscriptions ------------------------------------

    def _install_scene_jobs(self) -> None:
        """Register the persistent scene subscriptions (mirror of mayatk).

        ``SceneOpened`` and ``NewSceneOpened`` both back onto ``load_post`` in
        Blender and the manager dispatches every event mapped to a fired
        handler list, so subscribing ONE of them is enough (both would fire the
        invalidation twice per load).  Headless-safe: without ``bpy`` the
        manager records the subscription and installs the master handler on
        the first subscribe under a real runtime.
        """
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager
        except Exception:
            return
        mgr = ScriptJobManager.instance()
        if not self._scene_subs_installed:
            mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
            mgr.subscribe("SceneBeforeSave", self._on_before_save, owner=self)
            self._scene_subs_installed = True
        self._install_fps_watch()

    def _install_fps_watch(self) -> None:
        """Subscribe the framerate watch (``bpy.msgbus``; no app handler exists).

        Idempotent per file session: msgbus drops every subscription on file
        load, so :meth:`_on_scene_changed` resets the flag and calls this again.
        """
        if self._fps_sub_installed:
            return
        try:
            import bpy
        except ImportError:
            return

        def _notify(*_args):
            # msgbus accepts plain functions only (a bound method raises
            # TypeError), so the hook is reached through this closure.
            self._on_time_unit_changed()

        try:
            for prop in ("fps", "fps_base"):
                bpy.msgbus.subscribe_rna(
                    key=(bpy.types.RenderSettings, prop),
                    owner=self,
                    args=(),
                    notify=_notify,
                )
        except Exception:
            _log.debug("shot_store: msgbus fps watch unavailable", exc_info=True)
            return
        self._fps_sub_installed = True

    def remove_callbacks(self) -> None:
        """Tear down every SJM subscription + msgbus watch owned by this backend.

        Called by :meth:`pythontk.ShotStore.clear_active` when the backend is
        dropped — a leaked subscription would keep firing invalidations after
        the tests/panel that installed it are gone.
        """
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager

            ScriptJobManager.instance().unsubscribe_all(self)
        except Exception:
            pass
        try:
            import bpy

            bpy.msgbus.clear_by_owner(self)
        except Exception:
            pass
        self._scene_subs_installed = False
        self._fps_sub_installed = False

    def _on_scene_changed(self) -> None:
        """Invalidate the cached store when a different file is loaded.

        Mirror of mayatk's ``_on_scene_changed``: null the active store (the
        next ``active()`` loads the NEW file's property through this same
        backend) and fire the class-level invalidation listeners so open
        panels rebind + re-register their non-persistent ``bpy.app`` handlers.
        The msgbus framerate watch is renewed here (file load clears it).
        """
        self.store_cls.invalidate()
        self._fps_sub_installed = False
        self._install_fps_watch()

    def _on_time_unit_changed(self, *args) -> None:
        """Hand a scene framerate change to the store's ``rescale_to_fps``
        (mirror of mayatk's hook; what the store does with it is its own:
        ``ShotStore.rescale_to_fps`` records the rate only).

        No ``isReadingFile`` guard is needed: the msgbus watch is cleared at
        the start of a file load and only re-armed after the ``SceneOpened``
        invalidation, so the OLD scene's store can never be told of the NEW
        scene's rate.
        """
        store = self.store_cls._active
        if store is None or store.is_empty():
            return
        new_fps = _BlenderShotsHostInternal._get_scene_fps()
        old_fps = store.scene_fps
        if old_fps and abs(new_fps - old_fps) > 0.01:
            store.rescale_to_fps(new_fps)

    def _on_before_save(self, *args) -> None:
        """Flush dirty store data to the scene property before the file is written.

        The session's renames are followed first (a store that follows them:
        :meth:`BlenderShotsHost.follow_renames`): what a follow reads a rename
        off -- ``session_uid`` -- lasts one session, and a never-keyed object
        has no action slot to remember its first name by, so a rename saved
        unfollowed left its hold shot stale in every later session, its take
        dropped from the export (BACKLOG 2026-10-11).
        """
        store = self.store_cls._active
        if store is None:
            return
        follow = getattr(store, "follow_renames", None)
        if follow is not None:
            try:
                follow()
            except Exception:  # a save must never fail on the shot store
                _log.debug("shot_store: rename follow before save", exc_info=True)
        if store._dirty:
            store.save()

    def _scene(self):
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    @property
    def _spec(self) -> ptk.RecordSpec:
        """The record this backend serves (``shot_store`` / ``key_stash``),
        which owns the encoding; an unregistered channel name gets a shapeless
        private declaration so the backend still works for it."""
        return ptk.SceneRecords.by_key(self._attr_name, ptk.Scope.PRIVATE) or (
            ptk.RecordSpec(
                self._attr_name, ptk.Scope.PRIVATE, 1, "store", "", envelope=False
            )
        )

    def save(self, data: Dict[str, Any]) -> None:
        if self._scene() is None:
            return
        from blendertk.node_utils.data_nodes import DataNodes

        raw = self._spec.encode(data)
        DataNodes.write(ptk.Scope.PRIVATE, self._attr_name, raw)
        self._last_raw = raw

    def load(self) -> Optional[Dict[str, Any]]:
        """The stored document, or ``None`` when the scene holds none.

        Raises:
            ValueError: The record is present but not JSON.  It is left as
                stored: loading ``None`` would activate an EMPTY store whose
                next save overwrites a record that may still be recoverable
                (mirror of mayatk's contract).
        """
        if self._scene() is None:
            return None
        from blendertk.node_utils.data_nodes import DataNodes

        raw = DataNodes.read(ptk.Scope.PRIVATE, self._attr_name)
        self._last_raw = raw
        if not raw:
            return None
        data = self._spec.decode(raw, self._UNREADABLE)
        if data is self._UNREADABLE:
            raise ValueError(
                f"The {self._attr_name!r} record is not valid JSON; it was left "
                "as stored rather than loaded as an empty store."
            )
        return data

    def record_changed(self) -> bool:
        """Whether the channel differs from what this backend last wrote or read.

        Blender's undo restores the scene's custom properties with the step,
        so an undo or redo of a key-stash operation moves the record under a
        store that is already loaded; ``KeyStash.active`` asks this to re-read
        it (mirror of mayatk's ``MayaScenePersistence.record_changed``).
        """
        if self._scene() is None:
            return False
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.read(ptk.Scope.PRIVATE, self._attr_name) != self._last_raw


# ---------------------------------------------------------------------------
# Blender shots host
# ---------------------------------------------------------------------------


class _BlenderShotsHostInternal(object):
    """Internal helpers for BlenderShotsHost."""

    @staticmethod
    def _get_scene_fps() -> float:
        """Effective scene framerate ``render.fps / render.fps_base``, or 24.0 outside Blender."""
        try:
            import bpy
        except ImportError:
            return _DEFAULT_FPS
        scene = bpy.context.scene
        if scene is None:
            return _DEFAULT_FPS
        base = scene.render.fps_base or 1.0
        return float(scene.render.fps) / float(base)

    @staticmethod
    def _is_ignored_path(data_path: str, ignore) -> bool:
        """True if *data_path*'s channel leaf matches any *ignore* pattern.

        *ignore* is a ``fnmatch`` pattern or an iterable of them (``"scale"``,
        ``"rotation_*"``) — the Blender reading of Maya's attribute-pattern
        ``ignore`` argument.  ``None`` / empty ignores nothing.
        """
        if not ignore:
            return False
        from fnmatch import fnmatchcase

        patterns = [ignore] if isinstance(ignore, str) else list(ignore)
        leaf = data_path.rsplit(".", 1)[-1]
        return any(
            fnmatchcase(leaf, pat) or fnmatchcase(data_path, pat) for pat in patterns
        )

    @staticmethod
    def _transform_key_times(obj, value_tol: float = 1e-6, ignore=None) -> List[float]:
        """Sorted unique key times over *obj*'s **moving** transform fcurves.

        A transform channel whose values never vary (``max - min <= value_tol``
        across ≥2 keys) is treated as held/flat and skipped — the Blender stand-in
        for Maya's motion-only segment collection.
        """
        times: set = set()
        for fc in AnimUtils.get_fcurves(obj):
            if not BlenderShotsHost.is_transform_path(fc.data_path):
                continue
            if _BlenderShotsHostInternal._is_ignored_path(fc.data_path, ignore):
                continue
            kt, vals = AnimUtils.key_arrays(fc)
            if not kt:
                continue
            if len(kt) >= 2 and (max(vals) - min(vals)) <= value_tol:
                continue  # flat/held channel — no motion
            times.update(round(t, 6) for t in kt)
        return sorted(times)

    @staticmethod
    def _transform_motion_intervals(
        obj, motion_rate: float = 1e-3, ignore=None
    ) -> List[Tuple[float, float]]:
        """Merged ``(start, end)`` intervals where *obj*'s transform channels move.

        The Blender stand-in for Maya's ``SegmentKeys`` static-interval
        splitting: on every transform fcurve, each consecutive key pair whose
        per-frame rate of change ``|dv| / dt`` exceeds *motion_rate* is a moving
        interval; held spans (baked flat keys) between them are dropped, so a
        hold hidden inside a channel still splits the object's segment.
        Overlapping / touching intervals across channels are merged.
        """
        raw: List[Tuple[float, float]] = []
        for fc in AnimUtils.get_fcurves(obj):
            if not BlenderShotsHost.is_transform_path(fc.data_path):
                continue
            if _BlenderShotsHostInternal._is_ignored_path(fc.data_path, ignore):
                continue
            kt, kv = AnimUtils.key_arrays(fc)
            keys = [(round(t, 6), v) for t, v in zip(kt, kv)]
            for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
                dt = t1 - t0
                if dt <= 0:
                    continue
                if abs(v1 - v0) / dt > motion_rate:
                    raw.append((t0, t1))
        return [
            (run[0][0], max(t1 for _t0, t1 in run))
            for run in ptk.ShotDetection.cluster_spans(
                raw, inclusive=True, span=lambda iv: iv
            )
        ]

    @staticmethod
    def _active_scene(scene=None):
        """Resolve *scene* (explicit or the context's active scene); ``None`` if headless-empty."""
        if scene is not None:
            return scene
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    # ---- rename follow (:meth:`BlenderShotsHost.follow_renames`) -----------

    @staticmethod
    def _scene_names() -> Tuple[Dict[int, str], Dict[int, Tuple[str, ...]]]:
        """``({session_uid: name}, {session_uid: bone names})`` for every object
        in the file -- bones for armatures, in list order -- or two empty maps
        outside Blender.  ``ID.session_uid`` is what a rename keeps, through
        undo too, and Blender never hands one out twice in a session (a file
        load gives every ID a new one), so it is the identity a rename is
        followed by."""
        try:
            import bpy

            objects = list(bpy.data.objects)
        except Exception:  # no bpy, or bpy.data restricted (add-on register)
            return {}, {}
        names: Dict[int, str] = {}
        bones: Dict[int, Tuple[str, ...]] = {}
        for obj in objects:
            uid = obj.session_uid
            names[uid] = obj.name
            if obj.type == "ARMATURE" and obj.data is not None:
                bones[uid] = tuple(b.name for b in obj.data.bones)
        return names, bones

    @staticmethod
    def _remember_names(seen_names: dict, seen_bones: dict, names, bones) -> None:
        """Fold the file's current *names* / *bones* (:meth:`_scene_names`)
        into the history maps, in place: *seen_names* ``{name: session_uid}``
        -- each name's latest bearer -- and *seen_bones* ``{(armature uid,
        bone name): list index}``.  A history, not the last look: a restore
        point an undo puts back can name an object as it was called several
        renames ago."""
        for uid, name in names.items():
            seen_names[name] = uid
        for uid, bone_names in bones.items():
            for i, bone in enumerate(bone_names):
                seen_bones[(uid, bone)] = i

    @staticmethod
    def _scene_actions() -> Dict[int, str]:
        """``{session_uid: name}`` for every action in the file (empty outside
        Blender) -- what a claim's curve key names (:meth:`curve_key`), and a
        rename keeps the uid of, as an object's (:meth:`_scene_names`)."""
        try:
            import bpy

            return {a.session_uid: a.name for a in bpy.data.actions}
        except Exception:  # no bpy, or bpy.data restricted (add-on register)
            return {}

    @staticmethod
    def _uid_renames(stale, seen_names: dict, names: Dict[int, str]) -> dict:
        """``{old: new}`` for each *stale* name whose latest bearer this
        session (*seen_names*) is still in the file, under another name."""
        out = {}
        for old in stale:
            new = names.get(seen_names.get(old))
            if new is not None and new != old:
                out[old] = new
        return out

    @staticmethod
    def _slot_renames(stale) -> dict:
        """``{old name: new name}`` for the *stale* names an action slot answers for.

        Blender names an object's action slot after the object when it is
        first keyed (``OB<name>``; legacy actions: ``<name>Action``) and a
        rename leaves both as they were -- the twin of the anim curves Maya
        named after the old node, and the evidence for a rename this session
        never saw.  Exactly one object must answer: a slot two objects share
        (a linked duplicate, or a Shift+D copy of the action) cannot say which
        one was renamed.  One scan of the objects for every stale name.
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

    @staticmethod
    def _action_slot_renames(stale) -> dict:
        """``{old name: new name}`` for the *stale* ACTION names Blender's
        default spelling answers for -- :meth:`_slot_renames`' twin.

        A first keying names an object's action ``<object>Action`` and its
        slot after the object, and renaming the action leaves the slot as it
        was: the evidence for an action rename the store never saw (made
        before it saw the action, or in an earlier session).  Exactly one
        action must answer.
        """
        import bpy

        claims: dict = {}
        for obj in bpy.data.objects:
            action, slot = AnimUtils._animating(obj.animation_data)
            if action is None:
                continue
            first = slot.name_display if slot is not None else obj.name
            old = f"{first}Action"
            if old in stale and action.name != old:
                claims.setdefault(old, set()).add(action.name)
        return {old: next(iter(new)) for old, new in claims.items() if len(new) == 1}

    @staticmethod
    def _bone_rename(seen_bones: dict, bones: dict, uid, old: str) -> Optional[str]:
        """The name armature *uid*'s bone *old* bears now, or ``None``.

        Read off its place in the bone list, which a rename keeps: the bone
        now where *old* was last seen, when *old* itself is gone -- and only
        a name never seen elsewhere in the list, so a bone that merely moved
        up into the place is not read as the renamed one.
        """
        i = seen_bones.get((uid, old))
        now = bones.get(uid) or ()
        if i is None or i >= len(now) or old in now:
            return None
        new = now[i]
        return new if seen_bones.get((uid, new), i) == i else None

    @staticmethod
    def _path_bone(data_path: str) -> Optional[Tuple[str, str]]:
        """``(bone name, rest of the path)`` for a pose-bone channel, else ``None``."""
        m = _BONE_PATH.match(data_path)
        if m is None:
            return None
        return re.sub(r"\\(.)", r"\1", m.group(1)), data_path[m.end() :]

    @staticmethod
    def _has_fcurve(obj_name: str, data_path: str, index: int) -> bool:
        """Whether *obj_name* is driven by an fcurve on ``data_path[index]``."""
        import bpy

        obj = bpy.data.objects.get(obj_name)
        return obj is not None and any(
            fc.data_path == data_path and fc.array_index == index
            for fc in AnimUtils.get_fcurves(obj)
        )

    @staticmethod
    def _renamed_curve_key(key: str, renames: dict, bone_rename) -> str:
        """Format-1 ledger *key* (``<object>|<data_path>|<index>``) under its
        object's new name (*renames*) and, for a renamed bone's channel, the path
        Blender rewrote it to (``bone_rename(object, bone) -> new or None``)
        -- taken only when the old path drives nothing and the new one does,
        so a bone deleted and another added is never read as one."""
        try:
            obj_name, data_path, index = key.rsplit("|", 2)
        except ValueError:
            return key
        obj_name = renames.get(obj_name, obj_name)
        parsed = _BlenderShotsHostInternal._path_bone(data_path)
        new_bone = bone_rename(obj_name, parsed[0]) if parsed is not None else None
        if new_bone is not None and index.isdigit():
            import bpy

            i = int(index)
            moved = f'pose.bones["{bpy.utils.escape_identifier(new_bone)}"]{parsed[1]}'
            if not _BlenderShotsHostInternal._has_fcurve(
                obj_name, data_path, i
            ) and _BlenderShotsHostInternal._has_fcurve(obj_name, moved, i):
                data_path = moved
        return f"{obj_name}|{data_path}|{index}"

    # ---- curve identity (ledger format 2: :meth:`BlenderShotsHost.curve_key`) --

    @staticmethod
    def _identity_key(action_name: str, slot, data_path: str, index) -> str:
        """``<action>|<slot handle>|<data_path>|<index>``: an fcurve's ledger key
        by what it IS.  A legacy action (no slots) spells the handle empty."""
        handle = "" if slot is None else str(slot.handle)
        return f"{action_name}|{handle}|{data_path}|{int(index)}"

    @staticmethod
    def _key_parts(key) -> Optional[Tuple[str, str, str, int]]:
        """``(action, slot handle, data_path, index)`` of an identity key, or
        ``None`` for anything else -- a format-1 ``<object>|<data_path>|<index>``.

        A name may hold ``|``, so every slot-field position is a candidate
        (:data:`_SLOT_FIELD`); the first whose action is in the file wins, the
        first at all when none is.
        """
        try:
            head, index = str(key).rsplit("|", 1)
            index = int(index)
        except (ValueError, AttributeError):
            return None
        cands = []
        for m in _SLOT_FIELD.finditer(head):
            handle = m.group(1)
            path = head[m.start() + len(handle) + 2 :]
            if m.start() and path:
                cands.append((head[: m.start()], handle, path, index))
        if not cands:
            return None
        try:
            import bpy

            actions = bpy.data.actions
            return next((c for c in cands if c[0] in actions), cands[0])
        except Exception:  # no bpy, or bpy.data restricted (add-on register)
            return cands[0]

    @staticmethod
    def _identity_fcurve(action_name: str, handle: str, data_path: str, index: int):
        """The fcurve of action *action_name*'s slot *handle* on
        ``data_path[index]``, or ``None`` -- also for an action nothing uses
        (its object deleted: Blender keeps the orphan until the file is
        saved, and its curves animate nothing)."""
        import bpy

        action = bpy.data.actions.get(action_name)
        if action is None or not action.users:
            return None
        slot = None
        if handle:
            slot = next(
                (s for s in getattr(action, "slots", ()) if str(s.handle) == handle),
                None,
            )
            if slot is None:
                return None
        for fc in AnimUtils._slot_fcurves(action, slot):
            if fc.data_path == data_path and fc.array_index == index:
                return fc
        return None

    @staticmethod
    def _key_stale(key: str, owners=None) -> bool:
        """Whether ledger *key* names what a rename left behind: a format-1
        key, an action not in the file, or a pose bone no object its slot
        animates has (:meth:`follow_renames` re-keys them).  Names only --
        no curve walk -- so a pass with nothing renamed pays little.
        *owners* is :meth:`_slot_owners`, read once by a caller asking about
        many keys (default: read here, for a bone's channel)."""
        parts = _BlenderShotsHostInternal._key_parts(key)
        if parts is None:
            return str(key).count("|") >= 2
        import bpy

        if bpy.data.actions.get(parts[0]) is None:
            return True
        bone = _BlenderShotsHostInternal._path_bone(parts[2])
        if bone is None:
            return False
        if owners is None:
            owners = _BlenderShotsHostInternal._slot_owners()
        for name in owners.get((parts[0], parts[1]), ()):
            pose = getattr(bpy.data.objects.get(name), "pose", None)
            if pose is not None and pose.bones.get(bone[0]) is not None:
                return False
        return True

    @staticmethod
    def _action_curves(action):
        """Yield ``(ledger key, fcurve)`` for every fcurve of *action*, each
        keyed by its identity: slot by slot -- a curve knows its action
        (``id_data``), not the slot it belongs to."""
        key = _BlenderShotsHostInternal._identity_key
        layers = getattr(action, "layers", None)
        if not layers:
            for fc in getattr(action, "fcurves", None) or ():
                yield key(action.name, None, fc.data_path, fc.array_index), fc
            return
        for layer in layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    for fc in cb.fcurves:
                        yield (
                            key(action.name, cb.slot, fc.data_path, fc.array_index),
                            fc,
                        )

    @staticmethod
    def _slot_owners() -> Dict[Tuple[str, str], List[str]]:
        """``{(action name, slot handle): [object names]}``: who each action
        slot animates (:meth:`AnimUtils._animating`) -- a linked duplicate's
        slot answers for every object sharing it, in name order."""
        import bpy

        owners: Dict[Tuple[str, str], List[str]] = {}
        for obj in bpy.data.objects:
            action, slot = AnimUtils._animating(obj.animation_data)
            if action is not None:
                handle = "" if slot is None else str(slot.handle)
                owners.setdefault((action.name, handle), []).append(obj.name)
        for names in owners.values():
            names.sort()
        return owners

    @staticmethod
    def _renamed_bone_path(parts, owners, bone_rename) -> str:
        """The path an identity key's pose-bone channel (*parts*:
        :meth:`_key_parts`) bears now: Blender rewrote it for a renamed bone
        (``bone_rename(object, bone) -> new or None``, asked of each object
        the slot animates, *owners*) -- taken only when the old path drives
        nothing and the new one does, so a bone deleted and another added is
        never read as one."""
        import bpy

        action, handle, data_path, index = parts
        bone, rest = _BlenderShotsHostInternal._path_bone(data_path)
        find = _BlenderShotsHostInternal._identity_fcurve
        for owner in owners:
            new_bone = bone_rename(owner, bone)
            if new_bone is None:
                continue
            moved = f'pose.bones["{bpy.utils.escape_identifier(new_bone)}"]{rest}'
            if find(action, handle, data_path, index) is None and (
                find(action, handle, moved, index) is not None
            ):
                return moved
        return data_path

    @staticmethod
    def _legacy_identity(key: str) -> Optional[str]:
        """The identity key a format-1 ``<object>|<data_path>|<index>`` key
        stands for -- its object's action and slot (:meth:`curve_key`) -- or
        ``None`` when that object is not in the file or nothing animates it."""
        try:
            obj_name, data_path, index = str(key).rsplit("|", 2)
            index = int(index)
        except (ValueError, AttributeError):
            return None
        new = BlenderShotsHost.curve_key(obj_name, data_path, index)
        return new if _BlenderShotsHostInternal._key_parts(new) is not None else None

    @staticmethod
    def _is_legacy_key(key: str) -> bool:
        """A format-1 key (``<object>|<data_path>|<index>``), not an identity."""
        return (
            str(key).count("|") >= 2
            and _BlenderShotsHostInternal._key_parts(key) is None
        )



    # ---- ledger migration (:meth:`BlenderShotsHost.migrate_ledger`) --------

    @staticmethod
    def _migrate_curve_keys(store) -> int:
        """Re-key *store*'s format-1 ledger claims (``<object>|<data_path>|<index>``)
        by the identity of the curve they name: the object's action and slot
        (:meth:`_legacy_identity`).  An object no longer under that name is
        found by the name its action slot remembers (:meth:`_slot_renames`);
        a claim neither finds stays as stored, as a format-1 store kept a claim
        it could not resolve, and :meth:`BlenderShotsHost.follow_renames` tries
        it again.  In memory only: the next save writes format 2, and a file
        opened is not marked changed.

        Returns:
            The number of curves re-keyed.
        """
        host = _BlenderShotsHostInternal
        ledger = store.edit_ledger
        legacy = [k for k in ledger.curves if host._is_legacy_key(k)]
        moved = 0
        try:
            import bpy

            live = set(bpy.data.objects.keys())
            owners = {k.rsplit("|", 2)[0] for k in legacy}
            renames = host._slot_renames(owners - live) if owners - live else {}
        except Exception:  # no bpy, or bpy.data restricted (add-on register)
            return 0
        for key in legacy:
            obj_name = key.rsplit("|", 2)[0]
            rest = key[len(obj_name) :]
            new = host._legacy_identity(renames.get(obj_name, obj_name) + rest)
            if new is not None and ledger.rename_curve(key, new):
                moved += 1
        ledger.source_version = ledger.FORMAT_VERSION
        return moved


class _BlenderStoreState(object):
    """What :class:`BlenderShotsHost` keeps per store (``store.host_state``).

    ``seen_names`` / ``seen_bones``: every name an object (and an armature's
    bone) has borne while the store held the file -- what
    :meth:`BlenderShotsHost.follow_renames` reads a rename off, seeded with the
    names as they are when the store is made; ``seen_actions``: the same
    history for the actions a claim names its curve by.  ``flush_pending``: a
    deferred flush is queued on ``bpy.app.timers`` (the coalescing flag).
    """

    __slots__ = ("seen_names", "seen_bones", "seen_actions", "flush_pending")

    def __init__(self):
        self.seen_names: Dict[str, int] = {}
        self.seen_bones: Dict[Tuple[int, str], int] = {}
        self.seen_actions: Dict[str, int] = {}
        self.flush_pending = False


class BlenderShotsHost(_BlenderShotsHostInternal):
    """Blender's half of the shot store: the :class:`pythontk.ShotsHost` the
    one :class:`pythontk.ShotStore` asks about the scene.

    Mirror of mayatk's ``MayaShotsHost`` by name and behavior; registered as
    this module is imported (:meth:`resolve`), so ``btk.ShotStore.active()``
    binds Blender's scene, persistence, undo and rename follow with no entry
    point having run.  Each member answers the pure default outside Blender
    (``bpy`` absent).  The Blender-only ledger spelling lives here too:
    :meth:`curve_key`, :meth:`fcurve_for_key`, :meth:`is_transform_path`,
    :meth:`edit_serial`, and detection's scene walks
    (:meth:`collect_transform_segments`, :meth:`collect_selected_key_entries`).
    """

    #: Blender keys stay on their FRAMES when the rate changes, so the store
    #: records the new rate and moves nothing (``ShotStore.rescale_to_fps``;
    #: the key stash's rule too).
    keys_keep_time = False

    #: The one adapter :meth:`resolve` hands out.
    _instance: Optional["BlenderShotsHost"] = None
    #: Whether ``bpy`` imports in this process, probed once (``None``: not
    #: yet): a store made outside Blender asks on every construction.
    _in_blender: Optional[bool] = None

    @classmethod
    def resolve(cls) -> Optional["BlenderShotsHost"]:
        """The adapter when this process runs Blender (``bpy`` imports), else
        ``None`` -- the resolver :meth:`ShotStore.register_host` holds."""
        if cls._in_blender is None:
            try:
                import bpy  # noqa: F401
            except ImportError:
                cls._in_blender = False
            else:
                cls._in_blender = True
        if not cls._in_blender:
            return None
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- shot content and the ledger's curve spelling ----------------------

    @staticmethod
    def is_transform_path(data_path: str) -> bool:
        """True if *data_path* is shot content: an object/bone transform
        channel, an object's visibility (``hide_viewport`` / ``hide_render``,
        Maya's ``visibility``), or a render-effect property
        (``RenderEffects.PROP_PATHS`` -- deliverable animation, so a pulse
        keyed inside a shot is that shot's content and travels with it; any
        other custom property, a marker such as ``["audio_trigger"]``, never
        makes an object look animated).

        The one predicate membership, detection and the movers' content walk
        share (mayatk: ``Detection.first_standard_destination`` over
        ``CONTENT_ATTRS``, which is ``TRANSFORM_CHANNELS`` -- visibility
        included -- plus the render-effect attributes).  What the sequencer
        DRAWS is wider: every keyed channel, as Maya lists every animCurve on
        the node.
        """
        if (
            data_path in _TRANSFORM_CHANNELS
            or data_path in _VISIBILITY_PATHS
            or data_path in RenderEffects.PROP_PATHS
        ):
            return True
        return any(data_path.endswith("." + c) for c in _TRANSFORM_CHANNELS)

    @staticmethod
    def fcurve_for_key(key: str):
        """The live fcurve ledger *key* (:meth:`curve_key`) names, or ``None``
        -- a format-1 key names none until :meth:`follow_renames` re-keys it."""
        parts = _BlenderShotsHostInternal._key_parts(key)
        if parts is None:
            return None
        try:
            return _BlenderShotsHostInternal._identity_fcurve(*parts)
        except ImportError:
            return None

    @staticmethod
    def curve_key(obj_name: str, data_path: str, index: int = 0) -> str:
        """Ledger key of the fcurve driving *obj_name*'s ``data_path[index]``.

        The curve's own identity (ledger format 2): ``<action>|<slot
        handle>|<data_path>|<index>`` of the action and slot animating the
        object.  Maya claims are keyed by anim-curve NODE; a Blender fcurve
        has no name, and its owner was no stand-in: linked duplicates share
        one action slot, so one curve went by two keys -- an op applied it
        once per sharer, and an edit through one released only that one's
        claims.  A rename of the object leaves the
        key standing; an action's or a bone's is followed
        (:meth:`follow_renames`).  The slot is named by its handle, which a
        slot rename keeps.  The one spelling the sequencer and the manifest's
        behavior keys share; an object nothing animates gets the format-1
        ``<object>|<data_path>|<index>``, which names no curve.
        """
        try:
            import bpy

            obj = bpy.data.objects.get(obj_name)
        except Exception:  # no bpy, or bpy.data restricted
            obj = None
        ad = getattr(obj, "animation_data", None)
        action, slot = AnimUtils._animating(ad) if ad is not None else (None, None)
        if action is None:
            return f"{obj_name}|{data_path}|{index}"
        return _BlenderShotsHostInternal._identity_key(
            action.name, slot, data_path, index
        )

    # ---- the scene and the store's lifecycle -----------------------------

    @staticmethod
    def available() -> bool:
        """Whether the file can be reached (``bpy`` imports)."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def persistence() -> Optional[BlenderScenePersistence]:
        """A new :class:`BlenderScenePersistence` (``ShotStore.active``
        installs it once, mirroring how the Maya host installs its own), or
        ``None`` outside Blender."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        return BlenderScenePersistence()

    @classmethod
    def store_state(cls) -> _BlenderStoreState:
        """A new store's rename history, seeded with the file's names as they
        are now (:class:`_BlenderStoreState`)."""
        state = _BlenderStoreState()
        cls._remember_names(state.seen_names, state.seen_bones, *cls._scene_names())
        cls._remember_names(state.seen_actions, {}, cls._scene_actions(), {})
        return state

    @staticmethod
    def scene_fps() -> float:
        """Effective scene framerate: ``render.fps / render.fps_base``."""
        return _BlenderShotsHostInternal._get_scene_fps()

    @staticmethod
    def schedule_flush(store) -> None:
        """Coalesce rapid mutations into a single deferred write (mirror of mayatk).

        GUI session: one ``bpy.app.timers`` callback per burst — every
        ``mark_dirty`` during the same event-loop turn shares it.  Headless
        (``bpy.app.background`` — the timer loop never runs) or no ``bpy``:
        flush immediately, exactly like the Maya host outside Maya.
        """
        try:
            import bpy
        except ImportError:
            store._flush_dirty()
            return
        if bpy.app.background:
            store._flush_dirty()
            return
        state = store.host_state
        if state.flush_pending:
            return
        state.flush_pending = True

        def _run():
            state.flush_pending = False
            store._flush_dirty()
            return None  # one-shot

        try:
            bpy.app.timers.register(_run, first_interval=0.0)
        except Exception:
            state.flush_pending = False
            store._flush_dirty()

    @staticmethod
    def legacy_prefs() -> None:
        """None: Blender never kept prefs before the prefs file (the Maya
        twin's one-time QSettings hand-over is N/A)."""
        return None

    @classmethod
    def migrate_ledger(cls, store) -> None:
        """Re-key a loaded format-1 ledger's claims by their curves' identity
        (:meth:`curve_key`; :meth:`_migrate_curve_keys`)."""
        cls._migrate_curve_keys(store)

    @classmethod
    def follow_renames(cls, store) -> bool:
        """Re-point everything *store* names an object by at its new name.

        Blender keys what the shot system stores by object NAME -- shot
        members, the hidden / pinned / locked sets and the edit ledger's
        claims (:meth:`curve_key`) -- so a rename leaves them naming an object
        that is no longer there, and the passes that forget a claim they
        cannot resolve (the sequencer's gap-hold and boundary-sample
        reconciles) dropped it with the rename: the hold the system wrote read
        as the animator's from then on.  The store's half of mayatk's
        stale-path reconcile, run before every shot edit
        (``ShotStore.scene_edit``), by the sequencer's rebuild and content
        moves, by the passes that forget, and by ``ShotStore.export_transfer``
        -- whoever renamed the object, panel open or not.

        A rename is read off ``ID.session_uid``, which a rename keeps (through
        undo too), against every name the store has seen an object bear --
        when it was made and at every pass since -- so any rename is followed
        under any name, a Shift+D copy beside it included, and so is a name a
        restore point brings back from several renames ago.  A name the store
        held before it saw the object falls back to the action slot
        (:meth:`_slot_renames`), which still knows the object's first name in
        a later session.  A renamed BONE re-keys the claims on its channels:
        Blender rewrites the fcurve's path (``pose.bones["Arm"]`` to
        ``pose.bones["Arm_L"]``), and the ledger key follows it.

        A claim names its CURVE (:meth:`curve_key`): an object's rename leaves
        it standing, its action's is followed by ``session_uid`` as an
        object's is, and a format-1 claim still naming its object
        (:meth:`_migrate_curve_keys` found none under that name) is followed
        to it and re-keyed by identity.

        A name nothing answers for is kept as stored: renamed and deleted look
        the same from a name, dropping membership is irreversible, and
        ``assess`` surfaces deletions.

        Returns:
            ``True`` if anything was re-pointed.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return False
        names, bones = cls._scene_names()
        if not names:
            return False  # no object in the file (or none readable) to follow to
        state = store.host_state
        ledger = store.edit_ledger
        legacy = [k for k in ledger.curves if cls._is_legacy_key(k)]
        referenced = store.held_names()
        referenced |= {k.rsplit("|", 2)[0] for k in legacy}
        stale = referenced - set(names.values())
        renames = cls._uid_renames(stale, state.seen_names, names)
        rest = stale - set(renames)
        if rest:
            # Never an object the session already followed under another name.
            taken = set(renames.values())
            renames.update(
                (old, new)
                for old, new in cls._slot_renames(rest).items()
                if new not in taken
            )
        uids = {name: uid for uid, name in names.items()}
        live_bones = {uid: set(bone_names) for uid, bone_names in bones.items()}

        def bone_rename(obj_name, bone):
            uid = uids.get(obj_name)
            if uid is None or bone in live_bones.get(uid, ()):
                return None  # not an object in the file, or the bone is there
            return cls._bone_rename(state.seen_bones, bones, uid, bone)

        rekeyed = {}
        for key in legacy:
            new = cls._renamed_curve_key(key, renames, bone_rename)
            new = cls._legacy_identity(new) or new
            if new != key:
                rekeyed[key] = new
        actions = cls._scene_actions()
        live_actions = set(actions.values())
        parsed = {
            key: parts
            for key in set(ledger.curves) - set(legacy)
            if (parts := cls._key_parts(key)) is not None
        }
        stale_actions = {p[0] for p in parsed.values()} - live_actions
        action_renames = cls._uid_renames(stale_actions, state.seen_actions, actions)
        rest = stale_actions - set(action_renames)
        if rest:
            taken = set(action_renames.values())
            action_renames.update(
                (old, new)
                for old, new in cls._action_slot_renames(rest).items()
                if new not in taken
            )
        owners = None
        for key, (action, handle, data_path, index) in parsed.items():
            action = action_renames.get(action, action)
            bone = cls._path_bone(data_path)
            if bone is not None and action in live_actions:
                if owners is None:
                    owners = cls._slot_owners()
                data_path = cls._renamed_bone_path(
                    (action, handle, data_path, index),
                    owners.get((action, handle), ()),
                    bone_rename,
                )
            new = f"{action}|{handle}|{data_path}|{index}"
            if new != key:
                rekeyed[key] = new
        # The history takes the names as they are now only after it was read.
        cls._remember_names(state.seen_names, state.seen_bones, names, bones)
        cls._remember_names(state.seen_actions, {}, actions, {})
        if not renames and not rekeyed:
            return False
        with store.batch_update():
            store.rename_members(renames)
            for old in sorted(rekeyed):
                ledger.rename_curve(old, rekeyed[old])
            store.mark_dirty()
        return True

    # ---- scene reads -----------------------------------------------------

    @classmethod
    def has_animation(cls) -> bool:
        """True if any scene object has a moving-or-keyed transform fcurve.

        Lightweight existence check (mirrors the Maya original's intent): a
        transform channel carrying at least one keyframe counts.
        """
        try:
            import bpy
        except ImportError:
            return False
        scene = bpy.context.scene
        if scene is None:
            return False
        for obj in scene.objects:
            for fc in AnimUtils.get_fcurves(obj):
                if cls.is_transform_path(fc.data_path) and len(fc.keyframe_points) > 0:
                    return True
        return False

    @staticmethod
    def detection():
        """Blender's region detection (:class:`Detection`): dispatched exactly
        as the Maya host's is (``ShotStore.detect_regions``)."""
        return Detection

    @staticmethod
    def assess(store) -> Dict[int, str]:
        """Flag shots whose stored objects no longer exist in the file.

        Blender object names are unique within ``bpy.data.objects`` and stored
        verbatim (identity name resolution), so exact membership is the contract.
        A shot with no objects is ``"valid"`` (nothing to miss).
        """
        try:
            import bpy
        except ImportError:
            return {s.shot_id: "valid" for s in store.shots}
        existing = set(bpy.data.objects.keys())
        return {
            s.shot_id: (
                "valid" if all(o in existing for o in s.objects) else "missing_object"
            )
            for s in store.shots
        }

    @staticmethod
    def member_nodes(name: str) -> List[str]:
        """Names of every object answering to a doc object *name*.

        Exact first; then, for a name written without a namespace, the objects
        whose :meth:`~pythontk.ShotStore.member_key` it is -- an FBX from Maya
        names its objects ``AC:door_geo`` for the doc's ``door_geo`` (mirror
        of mayatk's).  :meth:`~pythontk.ShotStore.resolve_member` reads one
        hit as found, several as ambiguous.
        """
        try:
            import bpy
        except ImportError:
            return [str(name)]
        name = str(name)
        if name in bpy.data.objects:
            return [name]
        if ":" in name:
            return []
        return [
            o.name for o in bpy.data.objects if ShotStore.member_key(o.name) == name
        ]

    @staticmethod
    def existing_objects(store, names) -> set:
        """Which of *names* the file still holds (``ptk.ShotStore.stale_shots``).

        Blender object names are unique and stored verbatim, so a member is
        present exactly when an object bears its name (mayatk also tries a
        moved DAG path's leaf; a Blender name has no path to move).
        """
        names = [str(n) for n in names]
        try:
            import bpy
        except ImportError:
            return set(names)
        present = {n for n in names if n in bpy.data.objects}
        present.update(
            BlenderShotsHost._current_names(
                store, (n for n in names if n not in present)
            )
        )
        return present

    @staticmethod
    def member_ids(names) -> Dict[str, str]:
        """None: Blender has no persistent node id (renames are followed by
        :meth:`follow_renames` instead)."""
        return {}

    @classmethod
    def _current_names(cls, store, names) -> Dict[str, str]:
        """``{name: name now}`` for each of *names* no object bears whose
        object :meth:`follow_renames` would follow -- by ``session_uid``
        against the names this session saw, else the action slot -- WITHOUT
        following it: the export reads a renamed HOLD member without the
        store being changed inside it (mirror of mayatk's, which reads a
        saved node UUID; Blender has no persistent node id)."""
        try:
            import bpy
        except ImportError:
            return {}
        stale = {str(n) for n in names if str(n) not in bpy.data.objects}
        if not stale:
            return {}
        scene_names, _bones = cls._scene_names()
        out = cls._uid_renames(stale, store.host_state.seen_names, scene_names)
        rest = stale - set(out)
        if rest:
            taken = set(out.values())
            out.update(
                (old, new)
                for old, new in cls._slot_renames(rest).items()
                if new not in taken
            )
        return out

    @classmethod
    def keyed_windows(cls, store, windows) -> List[bool]:
        """Per ``(start, end)`` window, whether anything in the file is keyed
        inside it (``ptk.ShotStore.stale_shots``) -- every action in use, every
        slot, whatever it animates: an object, a camera's lens, a light, a
        shape key, as mayatk asks every animation curve.  An action with no
        user (a deleted object's) animates nothing and is skipped, and so is
        one only its fake user holds: a Key Stash clip, SmartBake's parked
        original -- ``users`` counts the fake user (mayatk skips a curve whose
        only link is its stash registry's ``message``).  A key the shot system
        holds as a bound sample (``edit_ledger``, keyed by :meth:`curve_key`)
        is its bookkeeping, not content, as :meth:`members_keyed_windows`
        reads it: a New Shot naming nothing got a gap hold's start pin on every
        curve through it, and Delete Empty kept it.  One sorted pass over the
        key times, each window a bisection.
        """
        windows = list(windows)
        try:
            import bpy
        except ImportError:
            return [True] * len(windows)  # the pure default: never stale on a guess
        from bisect import bisect_left

        ledger = store.edit_ledger
        times: List[float] = []
        for action in bpy.data.actions:
            if action.users <= int(action.use_fake_user):
                continue
            for key, fc in cls._action_curves(action):
                kt = AnimUtils.key_times(fc)
                if ledger and ledger.key_times(key):
                    kt = [t for t in kt if not ledger.owns_key(key, t)]
                times.extend(kt)
        times.sort()
        keyed = []
        for start, end in windows:
            i = bisect_left(times, start)
            keyed.append(i < len(times) and times[i] <= end)
        return keyed

    @classmethod
    def members_keyed_windows(cls, store, entries) -> List[Optional[bool]]:
        """Per ``(names, (start, end))``, whether an fcurve of the named
        objects keys a frame inside the window -- ``None`` when none of the
        names is in the file (``ptk.ShotStore.empty_shots``).  A key the shot
        system holds as a bound sample (``edit_ledger``, keyed by
        :meth:`curve_key`) is its bookkeeping, not the shot's content -- the
        mirror of mayatk's.
        """
        try:
            import bpy
        except ImportError:
            return [True] * len(entries)  # the pure default: never empty on a guess
        ledger = store.edit_ledger
        out: List[Optional[bool]] = []
        for names, (start, end) in entries:
            objs = [bpy.data.objects.get(str(n)) for n in names]
            objs = [o for o in objs if o is not None]
            if not objs:
                out.append(None)
                continue
            keyed = False
            for obj in objs:
                for fc in AnimUtils.get_fcurves(obj):
                    key = cls.curve_key(obj.name, fc.data_path, fc.array_index)
                    if any(
                        start <= t <= end and not ledger.owns_key(key, t)
                        for t in AnimUtils.key_times(fc)
                    ):
                        keyed = True
                        break
                if keyed:
                    break
            out.append(keyed)
        return out

    @staticmethod
    def audio_spans() -> List[tuple]:
        """Every sound strip's span (``ptk.ShotStore.empty_shots``; mirror of
        mayatk's)."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return []
        from blendertk.audio_utils._audio_utils import AudioUtils

        return [s for events in AudioUtils.read_all_events().values() for s in events]

    @classmethod
    def curve_ledger_keys(cls, store, objects=None):
        """``keys_of(fcurve)``: the ledger keys an fcurve is claimed under --
        what ``ShotStore.release_replaced`` resolves through.

        One: its identity (:meth:`curve_key`), whichever object -- of
        *objects*, kept for the hook's signature -- the edit went through, so
        an edit through one linked duplicate releases the claims every
        sharer plays.  A curve knows its action (``id_data``), not its slot:
        each action's curves are keyed once, on first use (building the hook
        scans nothing).  Renames are followed first then
        (``store.follow_renames``, as every shot edit does) when anything is
        claimed: a claim still naming an action by its old name would match
        nothing.
        """
        keyed: Dict[int, Dict[int, str]] = {}
        followed = False

        def keys_of(fc):
            nonlocal followed
            if not followed:
                followed = True
                if store.edit_ledger:
                    store.follow_renames()
            action = fc.id_data
            ptr = action.as_pointer()
            if ptr not in keyed:
                keyed[ptr] = {
                    curve.as_pointer(): key for key, curve in cls._action_curves(action)
                }
            key = keyed[ptr].get(fc.as_pointer())
            return [key] if key is not None else []

        return keys_of

    # ---- Blender's undo --------------------------------------------------

    #: Scene ID property every shot edit stamps INSIDE its undo step.  Maya
    #: pairs a restore point with its edit by the undo chunk's name; Blender's
    #: undo has no step names Python can read, but memfile undo restores an
    #: ID property -- so this value moves exactly when one of OUR steps is
    #: undone or redone, and never for anybody else's.  Leading underscore:
    #: kept out of the Custom Properties panel.
    EDIT_SERIAL_PROP = "_btk_shot_edit_serial"
    #: Highest serial handed out this session.  A stamp is one past the larger
    #: of this and the scene's own, so an undo (which winds the scene value
    #: back) never lets a new edit reuse a serial a redo point still names.
    _serial_floor = 0
    #: The scene's edit serial as the ``undo_pre`` / ``redo_pre`` handler found
    #: it, BEFORE the native step ran; ``None`` once that step was followed.
    _serial_before_native: Optional[int] = None
    #: ``{on_step: ((handler list name, function), ...)}``: the handlers
    #: :meth:`install_undo_sync` made, once per store class.
    _native_undo_handlers: Dict[Any, tuple] = {}

    @classmethod
    def edit_serial(cls) -> int:
        """The current scene's edit serial (0 before any shot edit, or no bpy)."""
        try:
            import bpy
        except ImportError:
            return 0
        scene = bpy.context.scene
        return int(scene.get(cls.EDIT_SERIAL_PROP, 0)) if scene is not None else 0

    @classmethod
    def _stamp_edit_serial(cls) -> int:
        """Write a fresh serial into the current scene and return it."""
        try:
            import bpy
        except ImportError:
            return 0
        scene = bpy.context.scene
        if scene is None:
            return 0
        n = max(cls._serial_floor, int(scene.get(cls.EDIT_SERIAL_PROP, 0))) + 1
        BlenderShotsHost._serial_floor = n
        scene[cls.EDIT_SERIAL_PROP] = n
        return n

    @classmethod
    @contextlib.contextmanager
    def undo_step(cls, store, label: str = "edit", snapshot: bool = True):
        """Run a shot edit as one undo step (``ptk.ShotStore.scene_edit``,
        which keeps the restore point's books).

        Mirror of mayatk's ``MayaShotsHost.undo_step``: the body runs inside
        :meth:`CoreUtils.undo_chunk` named *label*, and the handle is tagged
        ``(paired, marker)`` as mayatk's is.  The marker is the
        :attr:`EDIT_SERIAL_PROP` value stamped inside the step for an edit
        with a restore point (*snapshot*; mayatk's is the chunk name), which
        is how the store's undo/redo handlers tell OUR step from anybody
        else's (:meth:`step_crossed`).  ``cancel()`` pushes no undo step --
        the twin of Maya discarding an empty chunk, which Blender does not do
        on its own.

        The store's record is written INSIDE the step (a GUI session defers
        that write to a timer that ran after the push, so the step held the
        record from before the edit and undoing whatever came next put it
        back).
        """
        from blendertk.core_utils._core_utils import CoreUtils

        edit = store.EditHandle()
        with CoreUtils.undo_chunk(label) as chunk:
            try:
                yield edit
            finally:
                if edit.cancelled:
                    chunk.cancel()
                # Inside the step, and even when the body raised: a refused
                # drag may already have rippled (mayatk tags in its finally).
                store._flush_dirty()
                if snapshot and not edit.cancelled:
                    edit.tag = (True, cls._stamp_edit_serial())

    @classmethod
    def step_crossed(cls, marker, redo: bool = False) -> bool:
        """Whether the undo/redo Blender JUST performed passed over the step
        stamped *marker* (``ptk.ShotStore._native_step_crossed``).

        Mirror of mayatk's: Blender fires its undo/redo handlers for every step
        in the session, and consuming a restore point for someone else's step
        reverted shot bounds whose keys Blender had left where they were.
        mayatk reads the step's NAME off the queue; Blender's undo exposes no
        names, so each edit stamps :attr:`EDIT_SERIAL_PROP` inside its own step
        (:meth:`undo_step`) and the restore point records it.  Memfile undo
        winds that property with the steps, and serials only grow, so the
        point's marker says whether the jump crossed its edit:

        * an undo crossed it when ``after < marker <= before``;
        * a redo crossed it when ``before < marker <= after``.

        One step, or an Undo History jump over several, which fires the
        handlers ONCE (``follow_native_step`` asks again per point).  Any other
        step leaves the serial where it was.  An untagged point (*marker*
        ``None``, a legacy push) reads as crossed.
        """
        if marker is None:
            return True
        before, after = cls._serial_before_native, cls.edit_serial()
        if before is None or before == after:
            return False
        lo, hi = (before, after) if redo else (after, before)
        return lo < marker <= hi

    @classmethod
    def install_undo_sync(cls, on_step) -> None:
        """Subscribe *on_step* (the store's ``_on_native_step``) to Blender's
        undo and redo: ``undo_pre`` / ``redo_pre`` note the edit serial,
        ``undo_post`` / ``redo_post`` run it (mayatk's Undo/Redo events).

        ``@persistent``, so a file load keeps them -- the next file's store
        follows from its first edit -- and re-checked on every call, so a
        handler list rebuilt under them (factory settings) gets them back.
        """
        try:
            import bpy
        except ImportError:
            return
        handlers = cls._native_undo_handlers.get(on_step)
        if handlers is None:
            handlers = cls._native_undo_handler_fns(
                on_step, bpy.app.handlers.persistent
            )
            cls._native_undo_handlers[on_step] = handlers
        for list_name, fn in handlers:
            handler_list = getattr(bpy.app.handlers, list_name)
            if fn not in handler_list:
                handler_list.append(fn)

    @classmethod
    def _native_undo_handler_fns(cls, on_step, persistent) -> tuple:
        """``(handler list name, function)`` pairs for *on_step*: plain
        functions (``bpy.app.handlers`` wants functions), made once."""

        @persistent
        def _note_serial(*_args):
            cls._serial_before_native = cls.edit_serial()

        @persistent
        def _follow_undo(*_args):
            cls._follow_native(on_step, redo=False)

        @persistent
        def _follow_redo(*_args):
            cls._follow_native(on_step, redo=True)

        return (
            ("undo_pre", _note_serial),
            ("redo_pre", _note_serial),
            ("undo_post", _follow_undo),
            ("redo_post", _follow_redo),
        )

    @classmethod
    def _follow_native(cls, on_step, redo: bool) -> None:
        """A ``*_post`` handler's body: follow the step once, then forget the
        serial it was measured from."""
        try:
            on_step(redo=redo)
        finally:
            cls._serial_before_native = None

    @classmethod
    def remove_undo_sync(cls, on_step) -> None:
        """Detach the handlers :meth:`install_undo_sync` attached for
        *on_step*."""
        handlers = cls._native_undo_handlers.get(on_step)
        if not handlers:
            return
        try:
            import bpy
        except ImportError:
            return
        for list_name, fn in handlers:
            handler_list = getattr(bpy.app.handlers, list_name)
            while fn in handler_list:
                handler_list.remove(fn)

    # ---- export ----------------------------------------------------------

    @staticmethod
    def publish_records(records) -> Optional[str]:
        """Commit the shot records onto the shared ``data_export`` Empty
        (``FbxUtils.publish_authored``; mirror of the Maya host's); returns the
        carrier name, or ``None`` when a clear had nothing to do.  The Scene
        Exporter does not call this: ``FbxUtils.PRODUCERS`` names
        ``ShotStore.produce_export_records``."""
        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.node_utils.data_nodes import DataNodes

        FbxUtils.publish_authored(records)
        carrier = DataNodes.get_export_node(create=False)
        if carrier is None:
            return None
        # A clear on a carrier that never held the record had nothing to do.
        return (
            DataNodes.EXPORT if ptk.SceneRecords.SHOTS.key in carrier.keys() else None
        )

    @staticmethod
    def export_producer(enabled: bool) -> None:
        """Nothing to arm: bpy has no before-FBX-export event, and
        ``FbxUtils.PRODUCERS`` already routes every Scene Exporter write
        through ``ShotStore.produce_export_records`` -- the Blender twin of
        Maya's session-hook install / remove."""
        return None

    # ---- hand-off transfer (the manifest's ``shots`` section) ---------------

    @staticmethod
    def transfer() -> "_BlenderShotTransfer":
        """How a Blender file meets the hand-off codec
        (:class:`_BlenderShotTransfer`)."""
        return _BlenderShotTransfer()

    # ---- scene acquisition for detection (5.1 slotted-action API) ----------

    @staticmethod
    def collect_transform_segments(
        scene=None,
        gap_threshold: float = 5.0,
        objects: Optional[List[str]] = None,
        ignore=None,
        motion_rate: float = 1e-3,
    ) -> List[Dict[str, Any]]:
        """Gather per-object **motion** segments for auto shot detection.

        For every object in *scene* (or only the named *objects*), the moving
        intervals of its transform channels (:meth:`_transform_motion_intervals`
        — key pairs whose per-frame rate exceeds *motion_rate*; *ignore*
        channel patterns skipped) are joined into runs separated by gaps larger
        than *gap_threshold*; each run becomes a ``{"start", "end", "obj"}``
        segment.  The segments are the plain-data input to
        :meth:`pythontk.ShotDetection.cluster_segments_by_gap`, which does the
        cross-object clustering and ``min_duration`` filtering — this function
        only reaches the scene; the boundary math stays pure.
        """
        scene = _BlenderShotsHostInternal._active_scene(scene)
        if scene is None:
            return []
        wanted = set(objects) if objects is not None else None
        segments: List[Dict[str, Any]] = []
        for obj in scene.objects:
            if wanted is not None and obj.name not in wanted:
                continue
            intervals = _BlenderShotsHostInternal._transform_motion_intervals(
                obj, motion_rate=motion_rate, ignore=ignore
            )
            if not intervals:
                continue
            run_start, run_end = intervals[0]
            for t0, t1 in intervals[1:]:
                if t0 - run_end > gap_threshold:
                    segments.append(
                        {"start": run_start, "end": run_end, "obj": obj.name}
                    )
                    run_start = t0
                run_end = max(run_end, t1)
            segments.append({"start": run_start, "end": run_end, "obj": obj.name})
        return segments

    @staticmethod
    def collect_selected_key_entries(scene=None) -> List[Tuple[float, float, str]]:
        """Gather ``(time, value, object)`` triples from currently selected keyframes.

        Every selected keyframe on any fcurve of a scene object is a boundary
        marker — mirroring Maya's ``regions_from_selected_keys`` (which takes all
        selected keys, not just transform channels, so custom trigger/marker attrs
        such as an audio cue drive the shot boundaries).  The triples feed
        :meth:`pythontk.ShotDetection.boundaries_from_key_entries`.
        """
        scene = _BlenderShotsHostInternal._active_scene(scene)
        if scene is None:
            return []
        entries: List[Tuple[float, float, str]] = []
        for obj in scene.objects:
            for fc in AnimUtils.get_fcurves(obj):
                n = len(fc.keyframe_points)
                if not n:
                    continue
                sel = [False] * n
                fc.keyframe_points.foreach_get("select_control_point", sel)
                if not any(sel):
                    continue
                kt, kv = AnimUtils.key_arrays(fc)
                entries.extend((kt[i], kv[i], obj.name) for i in range(n) if sel[i])
        return entries


class _BlenderShotTransfer(object):
    """Blender's :class:`pythontk.ShotTransferHost`: how the hand-off codec
    (``pythontk.ShotTransfer``) names a Blender curve and finds a key.

    Mirror of mayatk's: the Maya bridge's ``.manifest.json`` carries the store
    (``ptk.ShotStore.export_transfer`` / ``apply_transfer``), and these only say
    how Blender spells it -- a claim's curve by its identity
    (:meth:`BlenderShotsHost.curve_key`), as the object(s) it animates and the
    mayatk channel label.
    """

    #: Channels whose sequencer label differs from the Maya attribute they
    #: stand in for (``hide_render`` is what a Maya visibility replay keys).
    _TRANSFER_LABEL_ALIASES: Dict[str, str] = {"hide_render": "visibility"}

    @staticmethod
    def spell(name: str) -> str:
        """FBX's spelling of a Blender name: as is (the Maya side respells
        through its importer's ``FBXASC`` encoding)."""
        return str(name)

    @classmethod
    def curve_refs(cls):
        """``curve_ref(key) -> [(object, label), ...]`` for one encode, the
        slot owners read once (:meth:`BlenderShotsHost._slot_owners`)."""
        owners = _BlenderShotsHostInternal._slot_owners()
        return lambda key: cls.curve_ref(key, owners)

    @classmethod
    def curve_ref(cls, key: str, owners=None) -> List[Tuple[str, str]]:
        """``[(object name, channel label), ...]`` for a ledger key: one per
        object its curve animates -- linked duplicates share one -- in name
        order, ``[]`` for none.  *owners* is :meth:`BlenderShotsHost._slot_owners`, read once
        by a caller asking for many keys (default: read here).  A format-1
        key names its object outright."""
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        parts = _BlenderShotsHostInternal._key_parts(key)
        if parts is None:
            try:
                obj_name, data_path, index = str(key).rsplit("|", 2)
                names, index = [obj_name], int(index)
            except (ValueError, AttributeError):
                return []
        else:
            action, handle, data_path, index = parts
            try:
                if owners is None:
                    owners = _BlenderShotsHostInternal._slot_owners()
            except ImportError:
                return []
            names = owners.get((action, handle), ())
        label = SegmentCollector.label_for(data_path, index)
        label = cls._TRANSFER_LABEL_ALIASES.get(label, label)
        return [(name, label) for name in names]

    @classmethod
    def curve_key(cls, obj_name: str, label: str) -> Optional[str]:
        """The ledger key of the fcurve labelled *label* on *obj_name*, or ``None``.

        Exact matches only: the sequencer's label (``translateX`` for
        ``location[0]``, ``opacity`` for the custom property) or a transfer
        alias of it -- never a substring, which could claim a sibling channel.
        """
        try:
            import bpy
        except ImportError:
            return None
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            _ShotSequencerInternal,
        )
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return None
        wanted = {label} | {
            path
            for path, alias in cls._TRANSFER_LABEL_ALIASES.items()
            if alias == label
        }
        for fc in AnimUtils.get_fcurves(obj):
            if SegmentCollector.attr_label(fc) in wanted:
                return _ShotSequencerInternal._fc_key(obj_name, fc)
        return None

    @staticmethod
    def key_exists(key: str, time: float) -> bool:
        """Whether the fcurve behind ledger *key* holds a key at *time*."""
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            _ShotSequencerInternal,
        )

        fc = _ShotSequencerInternal._fcurve_for_key(key)
        return (
            fc is not None
            and _ShotSequencerInternal._key_index_at(fc, time) is not None
        )

    @staticmethod
    def resolve(name: str) -> Optional[str]:
        """The scene object spelled *name* (a consumer scoped to an import passes
        its own resolver, which also tolerates the ``.001`` clash suffix)."""
        try:
            import bpy
        except ImportError:
            return None
        return name if bpy.data.objects.get(name) is not None else None

    @staticmethod
    def channels(names):
        """The render-effect channels *names* carry
        (``RenderEffects.channel_records``)."""
        return RenderEffects.channel_records(names)

    @staticmethod
    def write_channels(node: str, records: Dict[str, Any]) -> int:
        """Land received render-effect *records* on object *node*
        (``RenderEffects.apply_channel_records``)."""
        return RenderEffects.apply_channel_records(node, records)

    @staticmethod
    def audio() -> List[Dict[str, Any]]:
        """The scene's sound strips as the transfer's ``audio`` payload (mirror
        of mayatk's; ``offset`` is the head trim only Blender can express)."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        return [
            {
                "name": clip["name"],
                "file": clip.get("filepath") or "",
                "start": float(clip["frame_start"]),
                "end": float(clip["frame_end"]),
                "offset": float(clip.get("trim_start") or 0),
            }
            for clip in AudioUtils.list_clips()
        ]

    @staticmethod
    def write_audio(clips: List[Dict[str, Any]]) -> int:
        """Land transfer ``audio`` clips as sound strips (``AudioUtils.add_clip``
        + ``trim_clip`` for the placed span); a strip already named is left
        alone, so a re-apply and the scene's own clips are never doubled."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        existing = {clip["name"] for clip in AudioUtils.list_clips()}
        added = 0
        seen: Dict[str, int] = {}
        for clip in clips or []:
            name = str(clip.get("name") or "")
            path = str(clip.get("file") or "")
            if not path:
                continue
            # A Maya track plays several events under ONE name; each is a strip
            # here, so the repeats take a numbered name (stable, so a re-apply
            # finds them again).
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name}_{seen[name]}"
            if name in existing:
                continue
            start = float(clip.get("start") or 0.0)
            offset = max(0.0, float(clip.get("offset") or 0.0))
            try:
                strip = AudioUtils.add_clip(
                    path, frame_start=start - offset, name=name or None
                )
            except (FileNotFoundError, ValueError, RuntimeError) as e:
                _log.warning("audio: clip %r not added (%s)", name, e)
                continue
            end = clip.get("end")
            tail = None
            if end is not None:
                info = AudioUtils.get_clip(strip) or {}
                tail = max(0.0, float(info.get("frame_end") or 0.0) - float(end))
            AudioUtils.trim_clip(strip, offset_start=offset or None, offset_end=tail)
            added += 1
        return added


# A registration that must run at import: ``ptk.ShotStore`` resolves the running
# host on first ask, so a script reaching ``btk.ShotStore`` binds Blender's half.
ShotStore.register_host("blender", BlenderShotsHost.resolve)

# Bound for the callers that still import the retired name from this module
# (``DataNodes.OWNERS``, the Scene Exporter's ``_task_animation`` and
# ``test_data_nodes``); remove once they name ``ShotStore``.
BlenderShotStore = ShotStore
