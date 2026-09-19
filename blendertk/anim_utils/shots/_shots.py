# !/usr/bin/python
# coding=utf-8
"""Blender shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

Mirror of mayatk's ``anim_utils.shots._shots`` at the class/behavior level
(:class:`BlenderShotStore`, :class:`BlenderScenePersistence`).  All of the shot
*model*, planning, and detection *math* lives once in
``pythontk.core_utils.engines.shots`` (the DCC-agnostic engine); this module is
only the thin Blender **acquisition + persistence** layer:

- :class:`BlenderScenePersistence` stores the serialized store as the
  ``shot_store`` record on the private carrier (``btk.DataNodes``, a scene ID
  property group), so it rides the ``.blend`` file and is never exported.
- :class:`BlenderShotStore` subclasses :class:`pythontk.ShotStore` and overrides
  the scene-reaching hooks (:meth:`_scene_fps`, :meth:`has_animation`,
  :meth:`detect_regions`, :meth:`assess`) — gathering fcurve segments / selected
  keys from the live scene and handing them to the pure
  :func:`~pythontk.cluster_segments_by_gap` /
  :func:`~pythontk.boundaries_from_key_entries` boundary math.

Divergence from mayatk (by design):
    * **Slotted-action fcurve access.** Blender 4.4+ removed the flat
      ``Action.fcurves`` accessor (fully gone in 5.1); fcurves live under
      ``action.layers[*].strips[*].channelbag(slot).fcurves`` where the slot is
      ``obj.animation_data.action_slot``.  :func:`iter_action_fcurves` is the
      single walk that yields an object's fcurves across that structure.
    * **Motion filtering is per-fcurve value-variance**, not Maya's
      ``SegmentKeys`` static-interval splitting (Blender has no equivalent).  A
      transform channel whose values never change across its keys is treated as
      a held/flat channel and excluded; an object with only flat channels
      contributes no segment.  Held sub-intervals *within* a moving channel are
      not split out — coarser than Maya but correct for boundary detection.
    * **Export-view projection targets the carrier Empty.**
      :meth:`publish_export_view` mirrors the Maya original — the same
      ``shot_metadata`` JSON channel from the same
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
    * **Deferred flush uses ``bpy.app.timers``** in a GUI session (one
      coalesced write per burst of mutations, mirror of ``cmds.evalDeferred``).
      In ``--background`` the timer loop never runs, so the flush is immediate.
    * **Export preparer hooks are static.** bpy has no before-FBX-export event;
      ``FbxUtils.PRODUCERS`` already routes every Scene
      Exporter write through :meth:`~pythontk.ShotStore.produce_export_records`,
      so the base's ``_register_export_preparer`` / ``_unregister_export_preparer``
      no-ops are the Blender twins of Maya's session-hook install/remove.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk

from pythontk import ShotStore, ShotTransfer

from blendertk.anim_utils.shots._detection import Detection
from blendertk.anim_utils._anim_utils import AnimUtils
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

__all__ = ["BlenderShotStore", "BlenderScenePersistence", "Detection"]

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
      :attr:`BlenderShotStore._active` — without it the previous file's store
      would stay active and its next save would write the OLD file's shots
      JSON into the NEW scene's ``shot_store`` property.
    * ``SceneBeforeSave`` → :meth:`_on_before_save` flushes a dirty store
      before the ``.blend`` is written (the deferred-flush timer may not have
      fired yet).
    * ``RenderSettings.fps`` / ``fps_base`` (``bpy.msgbus``) →
      :meth:`_on_time_unit_changed` rescales shot timings to the new framerate.

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
                on file load, flushed before save, rescaled on a frame-rate
                change.  Defaults to :class:`BlenderShotStore`; the key stash
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
        """The store class this backend serves (resolved lazily: the shot store
        is defined below this class in the module)."""
        return self._store_cls if self._store_cls is not None else BlenderShotStore

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
        """Rescale shot timings when the scene framerate changes (mirror of mayatk).

        No ``isReadingFile`` guard is needed: the msgbus watch is cleared at
        the start of a file load and only re-armed after the ``SceneOpened``
        invalidation, so the OLD scene's store can never be rescaled onto the
        NEW scene's carrier.
        """
        store = self.store_cls._active
        if store is None or store.is_empty():
            return
        new_fps = _BlenderShotStoreInternal._get_scene_fps()
        old_fps = store.scene_fps
        if old_fps and abs(new_fps - old_fps) > 0.01:
            store.rescale_to_fps(new_fps)

    def _on_before_save(self, *args) -> None:
        """Flush dirty store data to the scene property before the file is written."""
        store = self.store_cls._active
        if store is not None and store._dirty:
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
# Blender shot store
# ---------------------------------------------------------------------------


class _BlenderShotStoreInternal(object):
    """Internal helpers for BlenderShotStore."""

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
    def _is_transform_path(data_path: str) -> bool:
        """True if *data_path* is shot content: an object/bone transform
        channel, or a render-effect property (``RenderEffects.PROP_PATHS`` --
        deliverable animation, so a pulse keyed inside a shot is that shot's
        content and travels with it; any other custom property, a marker such
        as ``["audio_trigger"]``, never makes an object look animated).

        The one predicate membership, detection, the tracks and the movers'
        content walk share (mayatk: ``Detection.first_standard_destination``
        over ``CONTENT_ATTRS``).
        """
        if data_path in _TRANSFORM_CHANNELS or data_path in RenderEffects.PROP_PATHS:
            return True
        return any(data_path.endswith("." + c) for c in _TRANSFORM_CHANNELS)

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
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if not _BlenderShotStoreInternal._is_transform_path(fc.data_path):
                continue
            if _BlenderShotStoreInternal._is_ignored_path(fc.data_path, ignore):
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
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if not _BlenderShotStoreInternal._is_transform_path(fc.data_path):
                continue
            if _BlenderShotStoreInternal._is_ignored_path(fc.data_path, ignore):
                continue
            kt, kv = AnimUtils.key_arrays(fc)
            keys = [(round(t, 6), v) for t, v in zip(kt, kv)]
            for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
                dt = t1 - t0
                if dt <= 0:
                    continue
                if abs(v1 - v0) / dt > motion_rate:
                    raw.append((t0, t1))
        if not raw:
            return []
        raw.sort()
        merged: List[Tuple[float, float]] = [raw[0]]
        for t0, t1 in raw[1:]:
            last0, last1 = merged[-1]
            if t0 <= last1:
                merged[-1] = (last0, max(last1, t1))
            else:
                merged.append((t0, t1))
        return merged

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


class BlenderShotStore(ShotStore, _BlenderShotStoreInternal):
    """:class:`pythontk.ShotStore` with the scene hooks bound to Blender.

    Only the DCC-reaching hooks are overridden; every CRUD / observer / planning
    behaviour is inherited unchanged from the pure engine.  :meth:`active`
    auto-installs :class:`BlenderScenePersistence` (mirroring how the Maya store
    installs its own backend), so ``BlenderShotStore.active()`` transparently
    loads any store saved in the current ``.blend``.
    """

    #: A deferred flush is already queued on ``bpy.app.timers`` (coalescing flag).
    _flush_pending: bool = False

    @classmethod
    def active(cls) -> "BlenderShotStore":
        """Return the active store, auto-installing the Blender backend once."""
        if cls._active is None and cls._persistence is None:
            try:
                import bpy  # noqa: F401
            except ImportError:
                pass
            else:
                cls.set_persistence(BlenderScenePersistence())
        return super().active()  # type: ignore[return-value]

    # ---- scene hooks -----------------------------------------------------

    def _scene_fps(self) -> float:
        """Effective scene framerate: ``render.fps / render.fps_base``."""
        return _BlenderShotStoreInternal._get_scene_fps()

    def _schedule_flush(self) -> None:
        """Coalesce rapid mutations into a single deferred write (mirror of mayatk).

        GUI session: one ``bpy.app.timers`` callback per burst — every
        ``mark_dirty`` during the same event-loop turn shares it.  Headless
        (``bpy.app.background`` — the timer loop never runs) or no ``bpy``:
        flush immediately, exactly like the Maya store outside Maya.
        """
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

    @staticmethod
    def has_animation() -> bool:
        """True if any scene object has a moving-or-keyed transform fcurve.

        Lightweight existence check (mirrors the Maya original's intent): a
        transform channel carrying at least one keyframe counts.  ``@staticmethod``
        so a controller's class-level ``BlenderShotStore.has_animation()`` — which
        queries the live scene, needing no instance — resolves on the class.
        """
        try:
            import bpy
        except ImportError:
            return False
        scene = bpy.context.scene
        if scene is None:
            return False
        for obj in scene.objects:
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                if (
                    _BlenderShotStoreInternal._is_transform_path(fc.data_path)
                    and len(fc.keyframe_points) > 0
                ):
                    return True
        return False

    def detect_regions(self) -> List[Dict[str, Any]]:
        """Detect shot candidates using the store's detection settings.

        Dispatches exactly as the Maya store does: to
        :meth:`Detection.regions_from_selected_keys` for the selected-key
        filter modes (``all`` / ``skip_zero`` / ``zero_as_end``) or to
        :meth:`Detection.detect_shot_regions` for ``auto``, using
        :attr:`detection_mode` and :attr:`detection_threshold`.

        Returns:
            List of candidate dicts with ``"name"``, ``"start"``, ``"end"``,
            and ``"objects"`` keys.
        """
        if self.detection_mode != "auto":
            return Detection.regions_from_selected_keys(
                gap_threshold=self.detection_threshold,
                key_filter=self.detection_mode,
            )
        return Detection.detect_shot_regions(gap_threshold=self.detection_threshold)

    def assess(self) -> Dict[int, str]:
        """Flag shots whose stored objects no longer exist in the file.

        Blender object names are unique within ``bpy.data.objects`` and stored
        verbatim (identity name resolution), so exact membership is the contract.
        A shot with no objects is ``"valid"`` (nothing to miss).
        """
        try:
            import bpy
        except ImportError:
            return {s.shot_id: "valid" for s in self.shots}
        existing = set(bpy.data.objects.keys())
        return {
            s.shot_id: (
                "valid" if all(o in existing for o in s.objects) else "missing_object"
            )
            for s in self.shots
        }

    # ---- export-view projection (Blender carrier) --------------------------

    def publish_export_view(self, strategy: Optional[str] = None) -> Optional[str]:
        """Publish this store's shot records onto the shared ``data_export`` Empty.

        The authoring-time publish (mirror of the Maya store's): the records
        :meth:`export_records` builds -- ``shot_metadata``, each clip with its
        range -- committed through ``FbxUtils.publish_authored``, handoff block
        included (the commit also clears a legacy ``fbx_takes``).  Idempotent;
        regenerated from the live store so it can't go stale.  An empty store
        **clears** the record (never creating the carrier just to hold it)
        — deleting the last shot must not leave the previous takes riding into
        the next export.  Returns the carrier name, or ``None`` outside Blender
        / when a clear had nothing to do.  The Scene Exporter does not call
        this: ``FbxUtils.PRODUCERS`` names :meth:`produce_export_records`.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.node_utils.data_nodes import DataNodes

        records = self.export_records(strategy=strategy) or []
        # The shot record, cleared unless built.  The commit also clears the
        # legacy take list (``fbx_takes``) an older file still holds: its
        # successor is published without it.
        FbxUtils.publish_authored(
            {ptk.SceneRecords.SHOTS: None, **{r.spec: r for r in records}}
        )
        carrier = DataNodes.get_export_node(create=False)
        if carrier is None:
            return None
        # A clear on a carrier that never held the record had nothing to do.
        return (
            DataNodes.EXPORT if ptk.SceneRecords.SHOTS.key in carrier.keys() else None
        )

    # ---- hand-off transfer (the manifest's ``shots`` section) ---------------
    #
    # Mirror of mayatk's ``ShotStore.export_transfer`` / ``apply_transfer``: the
    # Maya bridge's ``.manifest.json`` carries the store, ``pythontk.ShotTransfer``
    # is the codec, and this class only says how Blender names a curve
    # (``object|data_path|index``) and finds a key.

    #: Channels whose sequencer label differs from the Maya attribute they
    #: stand in for (``hide_render`` is what a Maya visibility replay keys).
    _TRANSFER_LABEL_ALIASES: Dict[str, str] = {"hide_render": "visibility"}

    @classmethod
    def _curve_ref(cls, key: str) -> Optional[Tuple[str, str]]:
        """``(object name, channel label)`` for a ledger key, or ``None``."""
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        try:
            obj_name, data_path, index = str(key).rsplit("|", 2)
            index = int(index)
        except (ValueError, AttributeError):
            return None
        label = SegmentCollector.label_for(data_path, index)
        return obj_name, cls._TRANSFER_LABEL_ALIASES.get(label, label)

    @classmethod
    def _curve_key(cls, obj_name: str, label: str) -> Optional[str]:
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
        for fc in cls.iter_action_fcurves(obj):
            if SegmentCollector.attr_label(fc) in wanted:
                return _ShotSequencerInternal._fc_key(obj_name, fc)
        return None

    @staticmethod
    def _key_exists(key: str, time: float) -> bool:
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
    def _resolve_transfer_name(name: str) -> Optional[str]:
        """The scene object spelled *name* (a consumer scoped to an import passes
        its own resolver, which also tolerates the ``.001`` clash suffix)."""
        try:
            import bpy
        except ImportError:
            return None
        return name if bpy.data.objects.get(name) is not None else None

    @classmethod
    def export_transfer(cls, spell=None, objects=None) -> Optional[Dict[str, Any]]:
        """The active store as a hand-off ``shots`` section (``None`` when empty).

        Parameters:
            spell: How the carrier spells a Blender name -- as is for FBX (the
                default; the Maya side respells through its importer's
                ``FBXASC`` encoding), the sanitized prim for USD.
            objects: The exported objects (names or Objects); scopes
                memberships and ledger claims to what ships (``None`` = all).
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        names = None
        if objects is not None:
            names = [
                o if isinstance(o, str) else getattr(o, "name", "") for o in objects
            ]
        return ShotTransfer.encode(
            cls.active().to_dict(),
            spell=spell or str,
            curve_ref=cls._curve_ref,
            objects=names,
            channels=RenderEffects.channel_records(names),
            audio=cls._audio_records(),
        )

    @staticmethod
    def _audio_records() -> List[Dict[str, Any]]:
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

    @classmethod
    def _write_audio(cls, clips: List[Dict[str, Any]]) -> int:
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

    @classmethod
    def apply_transfer(
        cls,
        section: Dict[str, Any],
        *,
        resolve=None,
        frame_offset: float = 0.0,
        replace: bool = False,
        converted=None,
    ) -> Optional["BlenderShotStore"]:
        """Rebuild the scene's shots from a hand-off ``shots`` section (mirror of
        mayatk's; see :meth:`pythontk.ShotTransfer.merge` for the fold rule).

        Parameters:
            section: The manifest's ``shots`` section.
            resolve: Carrier spelling -> imported object name; default: the
                object of exactly that name.
            frame_offset: The importer's frame shift (FBX: its ``anim_offset``).
            replace: Discard the scene's own shots instead of merging.
            converted: ``converted(name) -> bool``: the importer put that object
                through the Y-up / Z-up crossing, so its claims' Y and Z
                channels are exchanged (``ShotTransfer.swap_up_axis``); the
                consumers pass "has no parent". Default: none was.

        Returns:
            The active store after the apply, or ``None`` outside Blender.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        if not section:
            return None
        store = cls.active()
        decoded = ShotTransfer.decode(
            section,
            resolve=resolve or cls._resolve_transfer_name,
            curve_key=cls._curve_key,
            key_exists=cls._key_exists,
            scene_fps=store._scene_fps(),
            frame_offset=frame_offset,
            converted=converted,
            write_channels=RenderEffects.apply_channel_records,
            write_audio=cls._write_audio,
        )
        merged = decoded if replace else ShotTransfer.merge(store.to_dict(), decoded)
        if cls._persistence is None:
            cls.set_active(cls.from_dict(merged))
        else:
            cls._persistence.save(merged)
            cls.invalidate()
        return cls.active()

    # ---- scene acquisition (5.1 slotted-action API) -----------------------

    @staticmethod
    def iter_action_fcurves(obj):
        """Yield every fcurve driving *obj*, across Blender 5.1's slotted actions.

        Blender 4.4+ moved fcurves off the flat ``Action.fcurves`` list (removed in
        5.1) into per-slot channelbags: ``action.layers[*].strips[*].channelbag(slot)``
        where the slot is ``obj.animation_data.action_slot``.  This is the single
        place that walks that structure; every acquisition helper below goes through
        it so the traversal has one definition.  Legacy (pre-4.4) actions carry no
        ``layers`` — their flat ``action.fcurves`` is yielded directly.
        """
        ad = getattr(obj, "animation_data", None)
        if ad is None or ad.action is None:
            return
        act = ad.action
        slot = getattr(ad, "action_slot", None)
        layers = getattr(act, "layers", None)
        if not layers:
            yield from getattr(act, "fcurves", None) or ()
            return
        for layer in layers:
            for strip in layer.strips:
                try:
                    cb = strip.channelbag(slot) if slot is not None else None
                except Exception:
                    cb = None
                if cb is None:
                    continue
                for fc in cb.fcurves:
                    yield fc

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
        scene = _BlenderShotStoreInternal._active_scene(scene)
        if scene is None:
            return []
        wanted = set(objects) if objects is not None else None
        segments: List[Dict[str, Any]] = []
        for obj in scene.objects:
            if wanted is not None and obj.name not in wanted:
                continue
            intervals = _BlenderShotStoreInternal._transform_motion_intervals(
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
        scene = _BlenderShotStoreInternal._active_scene(scene)
        if scene is None:
            return []
        entries: List[Tuple[float, float, str]] = []
        for obj in scene.objects:
            for fc in BlenderShotStore.iter_action_fcurves(obj):
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
