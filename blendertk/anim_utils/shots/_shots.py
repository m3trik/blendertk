# !/usr/bin/python
# coding=utf-8
"""Blender shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

Mirror of mayatk's ``anim_utils.shots._shots`` at the class/behavior level
(:class:`BlenderShotStore`, :class:`BlenderScenePersistence`).  All of the shot
*model*, planning, and detection *math* lives once in
``pythontk.core_utils.engines.shots`` (the DCC-agnostic engine); this module is
only the thin Blender **acquisition + persistence** layer:

- :class:`BlenderScenePersistence` stores the serialized store as a JSON string
  on a scene custom property (``scene["shot_store"]``), so it rides the ``.blend``
  file and is never exported.
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
      ``fbx_takes`` / ``shot_metadata`` JSON channels from the same
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
      ``FbxUtils._KNOWN_PRODUCERS["shots"]`` already routes every Scene
      Exporter write through :meth:`~pythontk.ShotStore.refresh_export_view`,
      so the base's ``_register_export_preparer`` / ``_unregister_export_preparer``
      no-ops are the Blender twins of Maya's session-hook install/remove.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pythontk import ShotStore

from blendertk.anim_utils.shots._detection import Detection
from blendertk.anim_utils._anim_utils import AnimUtils

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
    """Persist the store as a JSON string on a scene custom property.

    Implements the :class:`pythontk.ScenePersistence` protocol
    (``save(data)`` / ``load() -> dict | None``).  The property lives on the
    active scene ID block, so it survives save/reopen with the ``.blend`` and —
    being a plain ID custom prop on a non-exported datablock — never leaks into
    an FBX/glTF export.

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

    #: Scene custom-property channel carrying the serialized store (rides the
    #: ``.blend``; a plain ID custom prop never serializes into an FBX export).
    ATTR_NAME = "shot_store"

    def __init__(self, attr_name: Optional[str] = None, store_cls=None):
        """
        Parameters:
            attr_name: Scene custom-property channel (default ``shot_store``).
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

    def save(self, data: Dict[str, Any]) -> None:
        scene = self._scene()
        if scene is None:
            return
        scene[self._attr_name] = json.dumps(data)

    def load(self) -> Optional[Dict[str, Any]]:
        scene = self._scene()
        if scene is None:
            return None
        raw = scene.get(self._attr_name)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            _log.warning("shot_store custom property is not valid JSON", exc_info=True)
            return None


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
        """True if *data_path* drives an object/bone transform channel."""
        if data_path in _TRANSFORM_CHANNELS:
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
        """Project the export view onto the shared ``data_export`` carrier.

        Writes the ``fbx_takes`` and ``shot_metadata`` channels as JSON custom
        properties (mirror of the Maya store's projection — same channels, same
        single :meth:`to_export_view` resolution pass, so the FBX take name and
        the metadata ``clip`` join-key cannot drift).  Idempotent; regenerated
        from the live store so it can't go stale.  An empty store **clears**
        both channels (never creating the carrier just to hold them) — deleting
        the last shot must not leave the previous takes riding into the next
        export.  Returns the carrier name, or ``None`` outside Blender / when
        a clear had nothing to do.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        from blendertk.node_utils.data_nodes import DataNodes

        view = self.to_export_view(strategy=strategy or self.clip_name_strategy)
        # shot_metadata is envelope-shaped ({"version": …, "shots": []}) and
        # therefore truthy even when empty — gate both channels on the store.
        has_shots = bool(self.shots)
        DataNodes.set_export_json(
            DataNodes.FBX_TAKES, view["fbx_takes"] if has_shots else None
        )
        return DataNodes.set_export_json(
            DataNodes.SHOT_METADATA, view["shot_metadata"] if has_shots else None
        )

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
