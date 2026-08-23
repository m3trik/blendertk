# !/usr/bin/python
# coding=utf-8
"""Shot-region detection — Blender scene acquisition over the pure engine math.

Mirror of mayatk's ``anim_utils.shots._detection`` (name + behavior, not
signatures): the Blender-side acquisition (discovering animated objects,
resolving an fcurve owner to its object, gathering selected-key entries,
filtering flat objects) feeding the DCC-agnostic boundary math in
``pythontk.core_utils.engines.shots`` (:meth:`~pythontk.ShotDetection.
cluster_segments_by_gap` / :meth:`~pythontk.ShotDetection.
boundaries_from_key_entries`).

The scene *walks* (slotted-action fcurve iteration, per-object motion
segments, selected-key triples) live on :class:`BlenderShotStore` — the
sequencer and manifest depend on them by name — so this module is a thin
:class:`Detection` namespace over them with the Maya signatures.

Divergence from mayatk (by design):
    * **Node → transform resolution is object-level.** Maya resolves an
      animCurve's destination (shape, ``unitConversion``, ``pairBlend``, …) up
      to its owning transform's long DAG path.  In Blender an fcurve belongs
      to an ID datablock's action; :meth:`Detection.resolve_to_transform`
      accepts an ID (or an object name) and resolves it to the **object name**
      that owns it — an Object is itself, object data / shape keys resolve to
      the object using them, a Material (no transform) resolves to ``None``.
      Names are flat and unique so there is no long-path form.
    * **Static-interval splitting is key-pair based.** Maya's ``SegmentKeys``
      splits a curve into moving / held intervals by sampling; Blender has no
      equivalent, so a transform channel's motion intervals are the key pairs
      whose per-frame rate of change exceeds *motion_rate* (see
      :meth:`BlenderShotStore.collect_transform_segments`).  A baked hold
      inside a channel therefore still splits the object's segment — the
      property the Maya docstring calls out ("boundaries hidden by baked
      animation are correctly detected").
    * ``ignore`` patterns match fcurve ``data_path`` channel leaves
      (``"scale"``, ``"rotation_*"``) instead of Maya attribute names.
"""

from typing import Any, Dict, List, Optional

from pythontk import ShotDetection
from blendertk.anim_utils._anim_utils import AnimUtils

__all__ = ["Detection"]


class _DetectionInternal(object):
    """Internal helpers for Detection."""

    @staticmethod
    def _map_standard_curves_to_transforms(scene=None, ignore=None):
        """Map each animated object to the fcurves driving its transform channels.

        Returns ``dict[str, list[FCurve]]`` — *object_name* → [*fcurves*].
        Fcurves that only drive custom / non-transform properties are skipped
        (mirror of the Maya helper, which keeps only ``STANDARD_TRANSFORM_ATTRS``
        destinations).  *ignore* channel patterns are honoured.
        """
        from blendertk.anim_utils.shots._shots import (
            BlenderShotStore,
            _BlenderShotStoreInternal,
        )

        scene = _BlenderShotStoreInternal._active_scene(scene)
        if scene is None:
            return {}
        result: Dict[str, list] = {}
        for obj in scene.objects:
            curves = [
                fc
                for fc in BlenderShotStore.iter_action_fcurves(obj)
                if _BlenderShotStoreInternal._is_transform_path(fc.data_path)
                and not _BlenderShotStoreInternal._is_ignored_path(fc.data_path, ignore)
            ]
            if curves:
                result[obj.name] = curves
        return result

    @staticmethod
    def _filter_flat_objects(
        candidates: List[Dict[str, Any]], value_tolerance: float = 1e-4
    ) -> List[Dict[str, Any]]:
        """Remove objects whose animation is flat or only on custom trigger properties.

        An object is genuine animated content if at least one of its transform
        fcurves has changing values within the shot's range.  Objects animated
        only on custom properties (e.g. an ``audio_trigger`` marker) are
        boundary markers and are excluded.  Candidates with no remaining
        objects are kept (the boundary is still valid); only ``"objects"`` is
        pruned.  Mirror of the Maya helper.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return candidates
        if not candidates:
            return candidates
        transform_curves = _DetectionInternal._map_standard_curves_to_transforms()
        if not transform_curves:
            return candidates

        def _varies_in_range(fc, start: float, end: float) -> bool:
            kt, kv = AnimUtils.key_arrays(fc)
            i0, i1 = AnimUtils.window_indices(kt, start, end)
            in_range = kv[i0:i1]
            return bool(in_range) and (max(in_range) - min(in_range)) > value_tolerance

        for cand in candidates:
            start, end = cand["start"], cand["end"]
            cand["objects"] = [
                obj
                for obj in cand["objects"]
                if any(
                    _varies_in_range(fc, start, end)
                    for fc in transform_curves.get(obj) or ()
                )
            ]
        return candidates


class Detection(_DetectionInternal):
    """Detection — module namespace."""

    @staticmethod
    def resolve_to_transform(node, cache=None):
        """Resolve an fcurve owner (ID datablock or object name) to its object name.

        Returns the owning object's name, or ``None`` when *node* has no
        transform owner (e.g. a Material, or a name not in ``bpy.data.objects``).
        An Object resolves to itself; object data (Mesh, Curve, Armature, …)
        and shape keys resolve to the first object using them.

        ``cache`` (a dict keyed by the ID's ``name_full`` / the string) memoizes
        results across calls — pass one shared dict when resolving many owners.
        """
        try:
            import bpy
        except ImportError:
            return None
        key = node if isinstance(node, str) else getattr(node, "name_full", None)
        if cache is not None and key in cache:
            return cache[key]

        result = None
        if isinstance(node, str):
            result = node if node in bpy.data.objects else None
        elif isinstance(node, bpy.types.Object):
            result = node.name
        elif isinstance(node, bpy.types.ID):
            for obj in bpy.data.objects:
                data = getattr(obj, "data", None)
                if data is None:
                    continue
                if data == node or getattr(data, "shape_keys", None) == node:
                    result = obj.name
                    break

        if cache is not None and key is not None:
            cache[key] = result
        return result

    @staticmethod
    def detect_shot_regions(
        objects: Optional[List[str]] = None,
        gap_threshold: float = 5.0,
        ignore=None,
        motion_rate: float = 1e-3,
        min_duration: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """Detect animation regions by clustering per-object motion segments.

        Scans the scene's animated transforms and groups contiguous segments
        into regions separated by gaps of at least *gap_threshold* frames.
        Single source of truth for shot-boundary detection — used by both the
        shot sequencer and the shot manifest (mirror of the Maya original).
        Flat / held intervals are always excluded so boundaries hidden by baked
        animation are detected.

        Parameters:
            objects: Object names to scan.  ``None`` scans every object in
                the active scene.  Names not in the file are dropped.
            gap_threshold: Minimum gap (frames) between clusters.
            ignore: Channel pattern(s) (``fnmatch`` on the ``data_path`` leaf,
                e.g. ``"scale"`` / ``"rotation_*"``) excluded from collection.
            motion_rate: Per-frame rate-of-change threshold.  Key pairs whose
                rate falls below this are treated as static.
            min_duration: Minimum shot duration in frames; shorter clusters
                are discarded.

        Returns:
            List of dicts with ``"name"``, ``"start"``, ``"end"``, and
            ``"objects"`` keys, sorted by start time.
        """
        try:
            import bpy
        except ImportError:
            return []
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        if objects is not None:
            objects = [n for n in objects if n in bpy.data.objects]
            if not objects:
                return []
        segments = BlenderShotStore.collect_transform_segments(
            gap_threshold=gap_threshold,
            objects=objects,
            ignore=ignore,
            motion_rate=motion_rate,
        )
        if not segments:
            return []
        # Pure clustering math lives in the engine (shared with mayatk).
        return ShotDetection.cluster_segments_by_gap(
            segments, gap_threshold=gap_threshold, min_duration=min_duration
        )

    @staticmethod
    def regions_from_selected_keys(
        gap_threshold: float = 5.0,
        key_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """Build shot regions from currently selected keyframes.

        Each unique selected key time is an explicit shot boundary; keys closer
        than *gap_threshold* merge into one boundary.  Designed for stepped /
        marker keys (e.g. audio triggers) where each key marks the start of a
        shot.  Objects with flat animation within a shot's range are excluded
        from that shot's ``"objects"`` list (mirror of the Maya original).

        Parameters:
            gap_threshold: Keys within this many frames merge into one boundary.
            key_filter: ``"all"`` (every key a boundary), ``"skip_zero"``
                (zero-value keys ignored) or ``"zero_as_end"`` (non-zero keys
                start shots, zero-value keys end them).

        Returns:
            List of dicts with ``"name"``, ``"start"``, ``"end"``, and
            ``"objects"`` keys, sorted by start time.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return []
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        entries = BlenderShotStore.collect_selected_key_entries()
        if not entries:
            return []
        candidates = ShotDetection.boundaries_from_key_entries(
            entries, gap_threshold=gap_threshold, key_filter=key_filter
        )
        return _DetectionInternal._filter_flat_objects(candidates)
