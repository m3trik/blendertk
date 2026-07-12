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
    * **No export-view / auto-FBX-take projection.** :meth:`publish_export_view`
      keeps the pure no-op default; the Blender FBX-take pipeline is out of this
      phase's scope (a documented follow-up, not a silent gap).
    * **Cross-scene prefs** use the engine's zero-dep JSON store (pythontk user
      config), not QSettings — inherited unchanged from the base.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pythontk import (
    ShotStore,
    cluster_segments_by_gap,
    boundaries_from_key_entries,
)

_log = logging.getLogger(__name__)

#: Scene custom-property channel carrying the serialized store (rides the
#: ``.blend``; a plain ID custom prop never serializes into an FBX export).
ATTR_NAME = "shot_store"

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

__all__ = [
    "BlenderShotStore",
    "BlenderScenePersistence",
    "iter_action_fcurves",
    "collect_transform_segments",
    "collect_selected_key_entries",
]


# ---------------------------------------------------------------------------
# fcurve acquisition (5.1 slotted-action API)
# ---------------------------------------------------------------------------


def _is_transform_path(data_path: str) -> bool:
    """True if *data_path* drives an object/bone transform channel."""
    if data_path in _TRANSFORM_CHANNELS:
        return True
    return any(data_path.endswith("." + c) for c in _TRANSFORM_CHANNELS)


def iter_action_fcurves(obj):
    """Yield every fcurve driving *obj*, across Blender 5.1's slotted actions.

    Blender 4.4+ moved fcurves off the flat ``Action.fcurves`` list (removed in
    5.1) into per-slot channelbags: ``action.layers[*].strips[*].channelbag(slot)``
    where the slot is ``obj.animation_data.action_slot``.  This is the single
    place that walks that structure; every acquisition helper below goes through
    it so the traversal has one definition.
    """
    ad = getattr(obj, "animation_data", None)
    if ad is None or ad.action is None:
        return
    act = ad.action
    slot = getattr(ad, "action_slot", None)
    layers = getattr(act, "layers", None)
    if layers is None:
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


def _transform_key_times(obj, value_tol: float = 1e-6) -> List[float]:
    """Sorted unique key times over *obj*'s **moving** transform fcurves.

    A transform channel whose values never vary (``max - min <= value_tol``
    across ≥2 keys) is treated as held/flat and skipped — the Blender stand-in
    for Maya's motion-only segment collection.
    """
    times: set = set()
    for fc in iter_action_fcurves(obj):
        if not _is_transform_path(fc.data_path):
            continue
        kps = fc.keyframe_points
        n = len(kps)
        if n == 0:
            continue
        if n >= 2:
            vals = [kp.co[1] for kp in kps]
            if (max(vals) - min(vals)) <= value_tol:
                continue  # flat/held channel — no motion
        for kp in kps:
            times.add(round(float(kp.co[0]), 6))
    return sorted(times)


def _active_scene(scene=None):
    """Resolve *scene* (explicit or the context's active scene); ``None`` if headless-empty."""
    if scene is not None:
        return scene
    try:
        import bpy
    except ImportError:
        return None
    return bpy.context.scene


def collect_transform_segments(
    scene=None, gap_threshold: float = 5.0
) -> List[Dict[str, Any]]:
    """Gather per-object animation segments for auto shot detection.

    For every object in *scene* with moving transform animation, its key times
    are split into runs separated by gaps larger than *gap_threshold*; each run
    becomes a ``{"start", "end", "obj"}`` segment.  The segments are the plain-data
    input to :func:`pythontk.cluster_segments_by_gap`, which does the cross-object
    clustering and ``min_duration`` filtering — this function only reaches the
    scene; the boundary math stays pure.
    """
    scene = _active_scene(scene)
    if scene is None:
        return []
    segments: List[Dict[str, Any]] = []
    for obj in scene.objects:
        times = _transform_key_times(obj)
        if not times:
            continue
        run_start = times[0]
        prev = times[0]
        for t in times[1:]:
            if t - prev > gap_threshold:
                segments.append({"start": run_start, "end": prev, "obj": obj.name})
                run_start = t
            prev = t
        segments.append({"start": run_start, "end": prev, "obj": obj.name})
    return segments


def collect_selected_key_entries(scene=None) -> List[Tuple[float, float, str]]:
    """Gather ``(time, value, object)`` triples from currently selected keyframes.

    Every selected keyframe on any fcurve of a scene object is a boundary
    marker — mirroring Maya's ``regions_from_selected_keys`` (which takes all
    selected keys, not just transform channels, so custom trigger/marker attrs
    such as an audio cue drive the shot boundaries).  The triples feed
    :func:`pythontk.boundaries_from_key_entries`.
    """
    scene = _active_scene(scene)
    if scene is None:
        return []
    entries: List[Tuple[float, float, str]] = []
    for obj in scene.objects:
        for fc in iter_action_fcurves(obj):
            for kp in fc.keyframe_points:
                if kp.select_control_point:
                    entries.append((float(kp.co[0]), float(kp.co[1]), obj.name))
    return entries


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

    Registers a ``SceneOpened`` subscription via :class:`ScriptJobManager`
    (mirror of mayatk's ``MayaScenePersistence`` scene jobs) so that
    :attr:`BlenderShotStore._active` is invalidated when the user opens or
    creates a file — without it, the previous file's store would stay active
    and its next save would write the OLD file's shots JSON into the NEW
    scene's ``shot_store`` property.  The manager's master handlers are
    ``@persistent``, so the subscription survives File ▸ New/Open.
    """

    def __init__(self, attr_name: str = ATTR_NAME):
        self._attr_name = attr_name
        self._scene_subs_installed = False
        self._install_scene_jobs()

    # ---- scene lifecycle subscriptions ------------------------------------

    def _install_scene_jobs(self) -> None:
        """Register the persistent scene-open subscription (mirror of mayatk).

        ``SceneOpened`` and ``NewSceneOpened`` both back onto ``load_post`` in
        Blender and the manager dispatches every event mapped to a fired
        handler list, so subscribing ONE of them is enough (both would fire the
        invalidation twice per load).  Headless-safe: without ``bpy`` the
        manager records the subscription and installs the master handler on
        the first subscribe under a real runtime.
        """
        if self._scene_subs_installed:
            return
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager
        except Exception:
            return
        ScriptJobManager.instance().subscribe(
            "SceneOpened", self._on_scene_changed, owner=self
        )
        self._scene_subs_installed = True

    def remove_callbacks(self) -> None:
        """Tear down every SJM subscription owned by this backend.

        Called by :meth:`pythontk.ShotStore.clear_active` when the backend is
        dropped — a leaked subscription would keep firing invalidations after
        the tests/panel that installed it are gone.
        """
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager
        except Exception:
            return
        ScriptJobManager.instance().unsubscribe_all(self)
        self._scene_subs_installed = False

    def _on_scene_changed(self) -> None:
        """Invalidate the cached store when a different file is loaded.

        Mirror of mayatk's ``_on_scene_changed``: null the active store (the
        next ``active()`` loads the NEW file's property through this same
        backend) and fire the class-level invalidation listeners so open
        panels rebind + re-register their non-persistent ``bpy.app`` handlers.
        """
        BlenderShotStore._active = None
        BlenderShotStore._notify_invalidated()

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


class BlenderShotStore(ShotStore):
    """:class:`pythontk.ShotStore` with the scene hooks bound to Blender.

    Only the DCC-reaching hooks are overridden; every CRUD / observer / planning
    behaviour is inherited unchanged from the pure engine.  :meth:`active`
    auto-installs :class:`BlenderScenePersistence` (mirroring how the Maya store
    installs its own backend), so ``BlenderShotStore.active()`` transparently
    loads any store saved in the current ``.blend``.
    """

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
        try:
            import bpy
        except ImportError:
            return super()._scene_fps()
        scene = bpy.context.scene
        if scene is None:
            return super()._scene_fps()
        base = scene.render.fps_base or 1.0
        return float(scene.render.fps) / float(base)

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
            for fc in iter_action_fcurves(obj):
                if _is_transform_path(fc.data_path) and len(fc.keyframe_points) > 0:
                    return True
        return False

    def detect_regions(self) -> List[Dict[str, Any]]:
        """Detect shot candidates from the scene using the store's settings.

        Dispatches exactly as the Maya store does: the selected-key filter modes
        (``all`` / ``skip_zero`` / ``zero_as_end``) build boundaries from
        currently-selected keys; ``auto`` clusters per-object motion segments.
        Both paths gather plain data here and delegate the boundary math to
        pythontk.
        """
        if self.detection_mode != "auto":
            entries = collect_selected_key_entries()
            return boundaries_from_key_entries(
                entries,
                gap_threshold=self.detection_threshold,
                key_filter=self.detection_mode,
            )
        segments = collect_transform_segments(gap_threshold=self.detection_threshold)
        return cluster_segments_by_gap(segments, gap_threshold=self.detection_threshold)

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
                "valid"
                if all(o in existing for o in s.objects)
                else "missing_object"
            )
            for s in self.shots
        }
