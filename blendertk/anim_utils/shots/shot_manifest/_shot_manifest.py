# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

Mirror of mayatk's ``anim_utils.shots.shot_manifest._shot_manifest`` at the
class level (:class:`BlenderShotManifest` ↔ ``ShotManifest``).  The CSV parsing,
column mapping, behavior keying-math, range resolution, and build planning all
live once, pure, in ``pythontk.core_utils.engines.shots.manifest``; this class
subclasses that engine's :class:`~pythontk.ShotManifest` and overrides only its
scene hooks:

- ``_resolve_fps`` → ``scene.render.fps / fps_base`` (cached per cycle);
- ``_measure_audio`` → the source file probed headlessly (``aud``), falling
  back to an already-placed VSE strip's path;
- ``_audio_grow_duration`` → the Blender-bound ``Behaviors.compute_duration``;
- name resolution → the store's ``resolve_member``, which the engine's
  ``_resolve_object`` asks (no override here);
- ``_discover_scene_objects`` / ``_filter_to_animated`` → objects whose
  transform channels actually *vary* in a shot's range (fcurve walk, cached
  per cycle — flat keys are boundary markers, same rule as Maya);
- assess seams (``_object_exists`` / ``_keyframe_range`` / ``_audio_exists`` /
  ``_verify_behavior``) → ``bpy.data`` / fcurve / VSE queries;
- ``apply_behaviors`` / ``_apply_one`` → :meth:`Behaviors.apply_to_shots` /
  :meth:`Behaviors.apply_behavior` with the Blender appliers (:mod:`.behaviors`):
  fades dual-keyed on :class:`RenderOpacity`'s ``opacity`` + stepped
  ``hide_render``, audio placed as VSE sound strips;
- ``_key_samples`` / ``_delete_keys`` → a behavior's fcurve keys, named by
  :meth:`BlenderShotStore.curve_key` -- what a build claims as its own and
  releases;
- ``rewire_audio`` → no-op (a VSE strip is its own playback node; Maya needs
  the compositor to materialise DG audio nodes from keyed tracks).

One divergence, kept on purpose: an audio behavior claims nothing and
``_placed_clip_keys`` keeps the engine's ``[]``.  A clip here is one VSE strip,
not keys -- a build re-places it (``apply_audio_clip`` moves the strip) but
never takes one out, so a clip the doc drops keeps its strip and Assess lists
it under no ``dropped_behaviors``.  Maya claims a clip's track keys, so a build
there removes a dropped clip.  Claiming the strip would need a stand-in key
whose release deletes the strip -- and the strip is where the clip's source
path lives, which a re-apply that cannot re-create it would lose.
"""

import functools
import logging
from typing import Dict, List, Optional, Tuple

from pythontk import ShotManifest, BuilderObject

from blendertk.anim_utils.shots._shots import BlenderShotStore

log = logging.getLogger(__name__)


class _ShotManifestInternal(object):
    """Internal helpers for BlenderShotManifest."""

    @staticmethod
    def _measure_audio_obj(obj: BuilderObject, fps: float) -> Optional[float]:
        """Length in frames of *obj*'s audio source (path, or a placed strip's path)."""
        from blendertk.anim_utils.shots.shot_manifest.behaviors._behaviors import (
            Behaviors,
        )

        src = getattr(obj, "source_path", "") or ""
        if not src:
            src = Behaviors._track_source_path(getattr(obj, "name", "") or "")
        if not src:
            return None
        try:
            frames, _ = Behaviors._audio_duration_frames(src, fps)
        except Exception as exc:
            log.debug("audio duration probe failed for %r: %s", obj.name, exc)
            return None
        return float(frames) if frames > 0 else None

    @staticmethod
    def _scene_fps() -> float:
        """Scene FPS (``fps / fps_base``), or 24 when Blender is unavailable."""
        try:
            import bpy

            scene = bpy.context.scene
            base = scene.render.fps_base or 1.0
            return float(scene.render.fps) / float(base)
        except Exception:
            return 24.0

    @staticmethod
    def _transform_fcurves(obj) -> list:
        """*obj*'s fcurves on standard transform channels."""
        return [
            fc
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            if BlenderShotStore._is_transform_path(fc.data_path)
        ]


class BlenderShotManifest(ShotManifest, _ShotManifestInternal):
    """:class:`pythontk.ShotManifest` with the scene hooks bound to Blender.

    Only the DCC-reaching hooks are overridden; the planner
    (``update`` / ``_compute_plan`` / ``_execute_plan``), the ``sync``
    orchestrator, and ``assess`` are inherited unchanged from the pure engine.
    """

    # ---- fps / audio measurement ----------------------------------------

    def _resolve_fps(self) -> float:
        """Return scene FPS, or 24 when Blender is unavailable.

        Cached per instance; cleared at the top of ``update`` so a single
        build queries the scene once instead of twice per shot.
        """
        if self._fps_cache is not None:
            return self._fps_cache
        self._fps_cache = _ShotManifestInternal._scene_fps()
        return self._fps_cache

    def _measure_audio(self, obj: BuilderObject) -> Optional[float]:
        """Audio-clip length in frames via source path or placed strip."""
        return _ShotManifestInternal._measure_audio_obj(obj, self._resolve_fps())

    def _audio_grow_duration(self, audio_objs: List[BuilderObject]) -> float:
        """Content-driven duration for an existing audio step.

        Routes through the Blender-bound ``behaviors.compute_duration`` (which
        resolves placed-strip paths and probes files itself) — imported lazily
        from the package, the established mock seam.
        """
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        return Behaviors.compute_duration(audio_objs, fallback=0.0)

    # ---- behavior application / audio rewire ------------------------------

    def apply_behaviors(self) -> Dict[str, list]:
        """Apply detected behaviors to Blender objects (fades, highlights,
        audio strips), each keyed from the scene's effect recipe.

        Lazy package import preserves the ``...behaviors.Behaviors`` mock seam.
        Guards (locked / zero-duration shots, existing keys, already-placed
        strips) live in the engine's build loop (``Behaviors.apply_to_shots``),
        shared with mayatk.
        """
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        # Shots a build keeps (locked, or edited by hand) get no behavior
        # pass, and turned-off behavior types are neither keyed nor released.
        return Behaviors.apply_to_shots(
            self._behavior_shots(),
            apply_fn=functools.partial(
                Behaviors.apply_behavior, recipe=self.recipe, fps=self._resolve_fps()
            ),
            store=self.store,
            resolve_fn=lambda name: self._resolve_object(name)[0],
            conflict_fn=lambda node, b, s, e: bool(self.unowned_keys(node, b, s, e)),
            release_fn=lambda shot, name, b: self.release_authored(
                shot.shot_id, name, b
            ),
            skip_behaviors=self.disabled_behaviors,
        )

    @staticmethod
    def rewire_audio(tracks: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """Reconcile audio playback nodes with keyed track state — no-op in Blender.

        Maya materialises DG audio nodes from keyed ``audio_clips`` tracks via
        the compositor; a VSE sound strip is already both the track and its
        playback node, so there is nothing to reconcile.  Kept for name parity
        so callers stay branch-free.

        Parameters:
            tracks: Accepted for signature parity; unused.

        Returns:
            ``{"created": [], "updated": [], "deleted": []}``.
        """
        return {"created": [], "updated": [], "deleted": []}

    # ---- assess seams ----------------------------------------------------

    def _object_exists(self, name: str) -> bool:
        try:
            import bpy
        except ImportError:
            return True
        return name in bpy.data.objects

    def _verify_behavior(
        self,
        obj: str,
        behavior: str,
        start: float,
        end: float,
        anchor_override: Optional[float] = None,
    ) -> bool:
        # Lazy package import — the established ``...behaviors.Behaviors``
        # mock seam.  Routes on the template's ``verify.mode`` (exact /
        # values_in_range / audio_clip) exactly like mayatk.
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        return Behaviors.verify_behavior(
            obj,
            behavior,
            start,
            end,
            anchor_override=anchor_override,
            recipe=self.recipe,
            fps=self._resolve_fps(),
        )

    def _keyframe_range(self, obj_name: str) -> Optional[Tuple[float, float]]:
        return self._default_keyframe_range(obj_name)

    def _audio_exists(self, name: str) -> bool:
        return self._default_audio_exists(name)

    # ---- behavior-key ownership seams --------------------------------------

    def _key_samples(
        self, obj: str, behavior: str, start: float, end: float
    ) -> List[Tuple[str, float]]:
        """Keys in ``[start, end]`` on the fcurves *behavior* keys on *obj*
        (``Behaviors._behavior_paths``), by :meth:`BlenderShotStore.curve_key`."""
        try:
            import bpy
        except ImportError:
            return []
        from blendertk.anim_utils.shots._shots import BlenderShotStore
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        node = bpy.data.objects.get(obj)
        if node is None:
            return []
        paths = set(Behaviors._behavior_paths(node, behavior))
        out: List[Tuple[str, float]] = []
        for fc in BlenderShotStore.iter_action_fcurves(node):
            if fc.data_path not in paths:
                continue
            key = BlenderShotStore.curve_key(node.name, fc.data_path, fc.array_index)
            for kp in fc.keyframe_points:
                if start - 1e-6 <= kp.co[0] <= end + 1e-6:
                    out.append((key, kp.co[0]))
        return out

    def _delete_keys(self, curve: str, times: List[float]) -> None:
        try:
            import bpy
        except ImportError:
            return
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        obj_name, data_path, index = curve.rsplit("|", 2)
        node = bpy.data.objects.get(obj_name)
        if node is None:
            return
        for fc in BlenderShotStore.iter_action_fcurves(node):
            if fc.data_path != data_path or str(fc.array_index) != index:
                continue
            doomed = [
                kp
                for kp in fc.keyframe_points
                if any(abs(kp.co[0] - t) <= 1e-3 for t in times)
            ]
            for kp in reversed(doomed):
                fc.keyframe_points.remove(kp)

    def _is_asset_candidate(self, name: str) -> bool:
        """An object a doc would list: not a camera, light, armature or speaker."""
        try:
            import bpy
        except ImportError:
            return True
        node = bpy.data.objects.get(name)
        return node is not None and node.type not in (
            "CAMERA",
            "LIGHT",
            "LIGHT_PROBE",
            "ARMATURE",
            "SPEAKER",
        )

    def _apply_one(self, node: str, behavior: str, start: float, end: float, **kwargs):
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        return Behaviors.apply_behavior(node, behavior, start, end, **kwargs)

    # ---- scene walks (fcurve acquisition) ---------------------------------

    def _discover_scene_objects(
        self, start: float, end: float, exclude_names=None
    ) -> List[str]:
        """Find objects with non-flat transform animation in ``[start, end]``.

        Only objects with keys on standard transform channels whose values
        actually change (variance > 1e-4) are returned.  Objects with flat
        keys or animated exclusively on custom properties are treated as
        boundary markers and excluded — the same rule as Maya.

        The object → transform-fcurve map is built once per assess/update
        cycle and cached on ``self._animated_transforms``.
        """
        try:
            import bpy  # noqa: F401 — availability probe
        except ImportError:
            return []
        exclude = set(exclude_names or ())
        animated = self._transform_curve_map()
        return [
            name
            for name in sorted(animated)
            if BlenderShotStore.member_key(name) not in exclude
            and any(
                self._curve_varies_in_range(crv, start, end) for crv in animated[name]
            )
        ]

    def _transform_curve_map(self) -> Dict[str, list]:
        """Object name → transform-channel fcurves map, cached per cycle.

        Walks every object in the scene, so it is computed at most once per
        assess/update cycle (both entry points clear
        ``self._animated_transforms``) and shared by every per-step check.
        """
        if self._animated_transforms is None:
            import bpy

            scene = bpy.context.scene
            objects = scene.objects if scene is not None else ()
            amap = {}
            for o in objects:
                crvs = self._transform_fcurves(o)
                if crvs:
                    amap[o.name] = crvs
            self._animated_transforms = amap
        return self._animated_transforms

    def _curve_varies_in_range(self, crv, start: float, end: float) -> bool:
        """True if fcurve *crv*'s value varies by >1e-4 within ``[start, end]``.

        Each curve's ``(times, values)`` are read once per cycle and range
        checks are evaluated in Python — S steps × C curves used to mean S×C
        keyframe walks; now it's C.
        """
        if self._curve_data is None:
            self._curve_data = {}
        # Keyed on the FCurve itself, NOT (action, data_path, array_index): for
        # an action fcurve ``crv.id_data`` is the ACTION, and iter_action_fcurves
        # is slot-aware, so two objects sharing one action through different
        # slots produce distinct curves that collide on that tuple -- the second
        # would then read the first's samples and be mis-classified.
        key = crv.as_pointer()
        data = self._curve_data.get(key)
        if data is None:
            times = [float(kp.co[0]) for kp in crv.keyframe_points]
            values = [float(kp.co[1]) for kp in crv.keyframe_points]
            data = self._curve_data[key] = (times, values)
        times, values = data
        window = [v for t, v in zip(times, values) if start <= t <= end]
        return bool(window) and (max(window) - min(window)) > 1e-4

    def _filter_to_animated(
        self, objects: List[str], start: float, end: float
    ) -> List[str]:
        """Return only objects that have non-flat transform animation in range.

        Looks each KNOWN name up directly (O(len(objects)), not O(scene));
        flat or custom-property-only animation is excluded, as in discovery.
        """
        if not objects:
            return []
        try:
            import bpy
        except ImportError:
            return list(objects)
        result = []
        for name in objects:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            if any(
                self._curve_varies_in_range(crv, start, end)
                for crv in self._transform_fcurves(obj)
            ):
                result.append(name)
        return result

    # ---- default seam implementations (kept as named statics for tests) ----

    @staticmethod
    def _default_audio_exists(name: str) -> bool:
        """Return True if a VSE sound strip named *name* exists."""
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils

            return bool(AudioUtils.get_clip(name))
        except Exception:
            return False

    @staticmethod
    def _default_keyframe_range(obj_name: str) -> Optional[Tuple[float, float]]:
        """Query the full keyframe time range for an object in Blender."""
        try:
            import bpy
        except ImportError:
            return None
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return None
        times = [
            kp.co[0]
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            for kp in fc.keyframe_points
        ]
        return (min(times), max(times)) if times else None

    # ---- from_csv ----------------------------------------------------------

    @classmethod
    def from_csv(cls, filepath, store=None, columns=None, post_process=None):
        """Parse a CSV and return a ready-to-build engine.

        Overrides the engine version so the default store is the **Blender**
        :meth:`BlenderShotStore.active` (auto-installing scene persistence) —
        the inherited default would silently create a separate, persistence-less
        pure-engine store.
        """
        return super().from_csv(
            filepath,
            store=store or BlenderShotStore.active(),
            columns=columns,
            post_process=post_process,
        )
