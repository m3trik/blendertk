# !/usr/bin/python
# coding=utf-8
"""Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the
class/function-name level (``SmartBake``, ``BakeAnalysis``, ``BakeResult``).

Analyzes scene objects to detect what is fighting a plain keyframe bake:

- Constraints (any type — ``COPY_LOCATION``, ``CHILD_OF``, ``ARMATURE``, …).
- IK (a Blender IK-type constraint on a pose bone — see divergence notes below).
- Drivers (``obj.animation_data.drivers``).
- Shape-key weights driven by a driver, or already carrying their own action.

Auto-detects an optimal bake time range from each driven source's own animation, bakes it
non-destructively (:func:`SmartBake.bake`/:func:`SmartBake.execute`) by muting every identified
source instead of touching it, and can reverse the whole thing (:func:`SmartBake.restore`) via
the session engine in ``bake_session.py``.

Divergence from mayatk (by design, not a gap to fill in later):
    * No per-channel selectivity. ``bpy.ops.nla.bake`` (wrapped by
      ``anim_utils.bake_keys``) bakes an object's/bone's *whole* transform in one pass — there
      is no Blender equivalent of ``bakeResults(attribute=[...])`` picking individual channels.
      :class:`BakeAnalysis` therefore only decides **whether** an object needs baking and
      supplies data for time-range detection; it does not enumerate channels to bake.
    * No IK-handle object model. Blender's IK lives as an ``IK``-type constraint on a pose bone
      (``obj.pose.bones[i].constraints``) — treated as just another constraint bucket (tagged
      ``"ik"`` instead of ``"constraint"``), not a separate subsystem, since ``constraint.mute``
      already covers it uniformly. Analyzing an ``ARMATURE`` walks ``pose.bones[*].constraints``
      in addition to the object's own ``.constraints``.
    * No inherited-visibility bake pass. Maya's FBX exporter needs an ancestor-visibility bake
      hack because it does not evaluate inherited DAG visibility; Blender's
      ``hide_viewport``/``hide_render`` are not inherited/multiplied down a parent chain the way
      Maya's is, and ``AnimUtils.set_visibility_keys`` already covers direct visibility keying.
      This is a permanent scope cut, matching the precedent set porting ``hierarchy_manager``.
    * Name-based references, not UUIDs. See ``bake_session.py``'s module docstring — object
      names are force-unique within ``bpy.data.objects`` and survive save/reopen, unlike
      Blender's process-local ``session_uid``.
"""
import logging
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from blendertk.core_utils._core_utils import undoable

if TYPE_CHECKING:
    from blendertk.anim_utils.smart_bake.bake_session import RestoreResult

logger = logging.getLogger(__name__)


@dataclass
class BakeAnalysis:
    """Analysis result for one bake-relevant unit — either a whole object or one of its pose
    bones.

    ``object`` is the key used both as this analysis's identity and as the corresponding key in
    the ``Dict[str, BakeAnalysis]`` returned by :func:`SmartBake.analyze`:

    * A plain object name (e.g. ``"Cube"``) for an object-level source: the object's own
      ``.constraints`` and ``.animation_data.drivers``, and — for a ``MESH`` — its shape-key
      weights (source_type ``"blend_shape"``).
    * A composite ``"<armature_name>:<bone_name>"`` key (e.g. ``"Rig:Hand.L"``) for a pose-bone
      constraint/IK source. Split on the *first* ``:`` — a bone name containing ``:`` (rare;
      typically only seen on rigs imported with a namespace-style prefix) is not addressable this
      way and is skipped.
    """

    object: str
    """The bpy object name, or an ``"armature:bone"`` composite key (see class docstring)."""

    driven_sources: Dict[str, List[str]] = field(default_factory=dict)
    """Live (unmuted) sources keeping this unit from being a plain keyframe bake. Maps
    ``source_type`` (``"constraint"`` / ``"ik"`` / ``"driver"`` / ``"blend_shape"``) to a list of
    identifying names — constraint names for ``"constraint"``/``"ik"``, driver
    ``fcurve.data_path`` strings for ``"driver"``/``"blend_shape"``."""

    @property
    def requires_bake(self) -> bool:
        """True if any live source was found for this unit."""
        return bool(self.driven_sources)


@dataclass
class BakeResult:
    """Result container for ``SmartBake.bake()``."""

    baked: Dict[str, List[str]] = field(default_factory=dict)
    """Objects that were baked. Maps object name to the constraint/driver names that were muted
    (or removed, if ``delete_sources=True``) for it."""

    skipped: List[str] = field(default_factory=list)
    """Objects/keys analyzed but not baked (no live source, or the bake attempt failed)."""

    time_range: Tuple[int, int] = (0, 0)
    """Time range used for baking (start, end)."""

    muted_constraints: List[str] = field(default_factory=list)
    """Constraint names muted across the whole bake (report; see also ``baked``)."""

    muted_drivers: List[str] = field(default_factory=list)
    """Driver ``data_path`` strings muted across the whole bake (report; see also ``baked``)."""

    session_id: Optional[str] = None
    """Id of the restore-manifest session recorded for this bake (if ``restorable=True``). Pass
    to ``SmartBake.restore()`` to reverse the bake — the manifest persists on the
    ``data_internal`` carrier, so restore works even after scene save/reopen."""

    backup_path: Optional[str] = None
    """Path of the pre-bake ``.blend`` snapshot written when ``backup_file`` was truthy, or
    ``None`` (no backup requested, or the scene has never been saved). See
    ``SmartBake._save_backup``."""

    optimized: List[str] = field(default_factory=list)
    """Objects (or shape-key datablock owners) that had keys optimized via
    ``AnimUtils.optimize_keys()`` (if ``optimize_keys=True``)."""

    @property
    def baked_count(self) -> int:
        """Number of objects successfully baked."""
        return len(self.baked)

    @property
    def success(self) -> bool:
        """True if any objects were baked."""
        return bool(self.baked)


# ---------------------------------------------------------------------------
# Module-level helpers — pure data lookups, no ``self`` needed.
# ---------------------------------------------------------------------------


def _resolve_analysis_key(key: str):
    """Resolve a :attr:`BakeAnalysis.object` key back to live Blender data.

    Returns ``(object, bone_name)`` — ``bone_name`` is ``None`` for a plain object key, or the
    pose-bone name for an ``"armature:bone"`` composite key. ``(None, None)`` when nothing
    resolves.
    """
    import bpy

    if ":" in key:
        arm_name, _, bone_name = key.partition(":")
        arm = bpy.data.objects.get(arm_name)
        if arm is not None and arm.type == "ARMATURE":
            return arm, bone_name
    return bpy.data.objects.get(key), None


def _constraints_for(obj, bone_name: Optional[str]):
    """The constraints collection a :func:`_resolve_analysis_key` ``(obj, bone_name)`` pair maps
    to — a pose bone's own when ``bone_name`` is given (an armature bone-level source), else the
    object's own ``.constraints``. Shared by :func:`SmartBake.get_time_range` and
    :func:`SmartBake.bake` so both always agree on which collection a given source lives in."""
    return obj.pose.bones[bone_name].constraints if bone_name else obj.constraints


def _object_key_range(obj) -> Optional[Tuple[float, float]]:
    """(min, max) keyframe frame across ``obj``'s own action, or ``None`` when keyless."""
    from blendertk.anim_utils._anim_utils import _key_range, get_fcurves

    return _key_range(get_fcurves([obj]))


def _driver_variable_target_frames(ad, data_paths: List[str]) -> List[float]:
    """Keyframe frames from every object a driver on ``ad`` (matching one of ``data_paths``)
    reads through its variables — the driver's own "source animation" range."""
    frames: List[float] = []
    if ad is None:
        return frames
    for fc in ad.drivers:
        if fc.data_path not in data_paths:
            continue
        for var in fc.driver.variables:
            for target in var.targets:
                if target.id is not None:
                    rng = _object_key_range(target.id)
                    if rng:
                        frames.extend(rng)
    return frames


def _blend_shape_source_frames(sk, data_paths: List[str]) -> List[float]:
    """Driving-animation frames for a shape-keys datablock's driven/animated weights: driver
    variable targets when live drivers exist, else the shape-keys' own action key range (a
    directly-keyed weight IS its own driving animation)."""
    frames: List[float] = []
    ad = getattr(sk, "animation_data", None) if sk is not None else None
    if ad is None:
        return frames
    if ad.drivers:
        frames.extend(_driver_variable_target_frames(ad, data_paths))
    elif ad.action is not None:
        from blendertk.anim_utils._anim_utils import _key_range, _slot_fcurves

        own = _key_range(_slot_fcurves(ad.action))
        if own:
            frames.extend(own)
    return frames


def _shape_key_block_for_fcurve(sk, fc):
    """The key_block a shape-keys datablock ``sk``'s driver/action fcurve ``fc`` targets (e.g.
    ``key_blocks["Key 1"].value`` -> the ``"Key 1"`` key_block), or ``None`` if it doesn't
    resolve. Shared by the driver- and action-snapshot branches of :func:`SmartBake.bake`."""
    container_path = fc.data_path.rsplit(".", 1)[0] if "." in fc.data_path else ""
    try:
        return sk.path_resolve(container_path) if container_path else None
    except Exception:
        return None


def _collect_constraint_sources(
    constraints, bucket: str, sources: Dict[str, List[str]]
) -> None:
    """Append every live (unmuted) constraint's name into ``sources[bucket]`` — ``"IK"``-type
    constraints go into the ``"ik"`` bucket regardless of ``bucket``."""
    for c in constraints:
        if c.mute:
            continue
        key = "ik" if c.type == "IK" else bucket
        sources.setdefault(key, []).append(c.name)


def _copy_outside_range_keys(
    original_action,
    original_slot,
    baked_action,
    baked_slot,
    time_range: Tuple[float, float],
) -> None:
    """Copy every ``keyframe_point`` on ``original_action`` that lies outside what ``baked_action``
    actually ended up covering onto the matching (same ``data_path`` + ``array_index``) fcurve of
    ``baked_action`` — the mechanism behind ``SmartBake(preserve_outside_keys=True)`` (the
    default).

    ``bake_keys`` (``nla.bake`` with ``use_current_action=False``) always produces a brand-new
    Action containing ONLY the baked range (confirmed live: the pre-bake action's own fcurves are
    never touched), so anything hand-keyed outside that range would otherwise be silently dropped
    the moment the object swaps onto the new action. Only channels present on BOTH actions count
    as a "corresponding channel" — mirrors mayatk's own ``preserveOutsideKeys`` semantics of
    extending the SAME curve being baked rather than inventing new ones for untouched channels.

    The "outside" boundary is taken from the corresponding baked fcurve's OWN first/last sampled
    frame, not the nominal ``time_range`` passed in. Confirmed live: ``nla.bake``'s ``step`` (the
    ``sample_by`` option) walks ``frame_start`` upward in fixed increments and does not force a
    final sample exactly at ``frame_end`` when the range isn't evenly divisible by it — e.g.
    ``step=3`` over ``(5, 10)`` bakes only frames ``5, 8`` and never touches ``10``. Comparing
    against the nominal ``end`` in that case would misclassify a hand-key sitting anywhere in the
    ``8..10`` gap as "inside" (silently discarding it) even though the baked curve never actually
    reached that far — using the baked curve's real extent instead means anything past its last
    real sample is correctly treated as unbaked and gets preserved. Falls back to ``time_range``
    only for the (should not happen via ``nla.bake``) case of a baked fcurve with zero keys.
    """
    from blendertk.anim_utils._anim_utils import _slot_fcurves

    fallback_start, fallback_end = time_range
    baked_by_channel = {
        (fc.data_path, fc.array_index): fc for fc in _slot_fcurves(baked_action, baked_slot)
    }
    if not baked_by_channel:
        return

    for src_fc in _slot_fcurves(original_action, original_slot):
        dst_fc = baked_by_channel.get((src_fc.data_path, src_fc.array_index))
        if dst_fc is None:
            continue
        baked_frames = [k.co.x for k in dst_fc.keyframe_points]
        start, end = (
            (min(baked_frames), max(baked_frames))
            if baked_frames
            else (fallback_start, fallback_end)
        )
        outside = [k for k in src_fc.keyframe_points if k.co.x < start or k.co.x > end]
        if not outside:
            continue
        for k in outside:
            new_kf = dst_fc.keyframe_points.insert(k.co.x, k.co.y)
            new_kf.interpolation = k.interpolation
            new_kf.easing = k.easing
            new_kf.back = k.back
            new_kf.amplitude = k.amplitude
            new_kf.period = k.period
            new_kf.handle_left_type = k.handle_left_type
            new_kf.handle_right_type = k.handle_right_type
            new_kf.handle_left = k.handle_left.copy()
            new_kf.handle_right = k.handle_right.copy()
        dst_fc.update()


class SmartBake:
    """Intelligent bake+restore with automatic detection of what needs baking.

    Analyzes objects/pose bones to find live constraints (including IK), drivers, and
    driven/animated shape-key weights, auto-detects the optimal bake time range from that
    animation, and (in a later stage of this port) bakes with either a non-destructive
    mute-and-restore path or a destructive delete-sources path.

    Example:
        >>> baker = SmartBake()
        >>> analysis = baker.analyze()
        >>> start, end = baker.get_time_range(analysis)
    """

    def __init__(
        self,
        objects: Optional[List[Any]] = None,
        sample_by: int = 1,
        use_override: bool = True,
        delete_sources: bool = False,
        bake_blend_shapes: bool = True,
        preserve_outside_keys: bool = True,
        optimize_keys: bool = False,
        restorable: bool = True,
        backup_file: Any = None,
    ):
        """Initialize SmartBake with configuration.

        Parameters:
            objects: Objects to analyze/bake. If None, uses every ``MESH``/``EMPTY``/
                ``ARMATURE`` object in the current scene (excluding the ``data_internal`` /
                ``data_export`` carriers).
            sample_by: Keyframe sample interval forwarded to ``AnimUtils.bake_keys()``'s
                ``nla.bake`` step (1 = every frame).
            use_override: Non-destructive mode (default). ``bake()`` bakes each object into a
                brand-new Action (``bake_keys(..., use_current_action=False)``) while the
                original stays alive via ``Action.use_fake_user``, and mutes every source
                :func:`analyze` found instead of touching it; ``restore()`` swaps the original
                action back in and unmutes everything to its recorded prior state.
            delete_sources: Destructive alternative to ``use_override`` (mirrors mayatk's
                ``delete_inputs``) — removes the identified constraints/drivers outright instead
                of muting them. That portion of the session is recorded as non-restorable,
                matching mayatk's own ``delete_inputs`` precedent.
            bake_blend_shapes: Analyze and bake driven/animated shape-key weights via
                ``AnimUtils.bake_blend_shapes()``. Required for Unity/FBX export when shape keys
                are driven, since ``nla.bake`` only bakes object/pose transforms.
            preserve_outside_keys: Keep keyframe_points that existed on an object's/armature's
                pre-bake action OUTSIDE the detected/baked time range (default: True). Since
                ``bake_keys`` (``nla.bake`` with ``use_current_action=False``) always produces a
                brand-new Action containing ONLY the baked range (confirmed live — nla.bake never
                touches the pre-existing Action under ``use_current_action=False``), anything
                hand-keyed outside that range on the pre-bake action would otherwise be silently
                dropped once the object swaps onto the new one; this copies each such key onto the
                matching channel (same ``data_path`` + ``array_index``) of the new baked action
                before the swap is finalized. ``False`` leaves the new action containing only the
                freshly baked range — mirrors mayatk's own ``preserve_outside_keys=False``
                semantics of discarding anything outside the bake window.
            optimize_keys: Run ``AnimUtils.optimize_keys()`` (remove static curves + redundant
                flat keys) on the objects — and driven shape-key datablocks — that were actually
                baked (default: False). Mirrors mayatk's own ``optimize_keys`` precedent; see
                :attr:`BakeResult.optimized` for which objects were touched.
            restorable: Record a restore-manifest session on the ``data_internal`` carrier
                (default: True) so ``SmartBake.restore()`` can reverse the bake, even after a
                scene save/reopen.
            backup_file: Save a pre-bake ``.blend`` snapshot before any destructive edits (mirrors
                mayatk's ``EnvUtils.save_scene_backup`` precedent) — ``True`` auto-generates an
                ``"<scene>_prebake.blend"`` path next to the current file, or pass an explicit
                path string. ``None`` (default) auto-resolves to ``True`` only when
                ``delete_sources=True`` (the one non-restorable path) — same auto-backup rule as
                mayatk's. No-op (returns ``None``, warns) if the scene has never been saved —
                there is no directory to write the backup next to.
        """
        self.objects = objects
        self.sample_by = sample_by
        self.use_override = use_override
        self.delete_sources = delete_sources
        self.bake_blend_shapes = bake_blend_shapes
        self.preserve_outside_keys = preserve_outside_keys
        self.optimize_keys = optimize_keys
        self.restorable = restorable
        if backup_file is None:
            backup_file = bool(delete_sources)
        self.backup_file = backup_file

    # -------------------------------------------------------------------------
    # Analysis
    # -------------------------------------------------------------------------

    def _get_objects(self) -> List[Any]:
        """Objects to analyze, defaulting to every scene MESH/EMPTY/ARMATURE object."""
        if self.objects:
            return list(self.objects)

        import bpy
        from blendertk.node_utils.data_nodes import DataNodes

        carriers = {DataNodes.INTERNAL, DataNodes.EXPORT}
        return [
            o
            for o in bpy.context.scene.objects
            if o.type in ("MESH", "EMPTY", "ARMATURE") and o.name not in carriers
        ]

    def analyze(self) -> Dict[str, BakeAnalysis]:
        """Analyze objects to determine what needs baking.

        Returns:
            Dict mapping :attr:`BakeAnalysis.object` keys to their analysis (see
            :class:`BakeAnalysis` for the key format).
        """
        results: Dict[str, BakeAnalysis] = {}
        objects = self._get_objects()
        if not objects:
            return results

        for obj in objects:
            self._analyze_object(obj, results)

        if self.bake_blend_shapes:
            self._analyze_blend_shapes(objects, results)

        return results

    def _analyze_object(self, obj, results: Dict[str, BakeAnalysis]) -> None:
        """Analyze one object's own constraints/drivers, plus (for an ARMATURE) each pose bone's
        constraints as separate composite-keyed entries."""
        own_sources: Dict[str, List[str]] = {}
        _collect_constraint_sources(obj.constraints, "constraint", own_sources)

        ad = getattr(obj, "animation_data", None)
        if ad is not None:
            for fc in ad.drivers:
                if fc.mute:
                    continue
                own_sources.setdefault("driver", []).append(fc.data_path)

        if own_sources:
            results[obj.name] = BakeAnalysis(object=obj.name, driven_sources=own_sources)

        if obj.type == "ARMATURE":
            for bone in obj.pose.bones:
                bone_sources: Dict[str, List[str]] = {}
                _collect_constraint_sources(bone.constraints, "constraint", bone_sources)
                if bone_sources:
                    key = f"{obj.name}:{bone.name}"
                    results[key] = BakeAnalysis(object=key, driven_sources=bone_sources)

    def _analyze_blend_shapes(self, objects: List[Any], results: Dict[str, BakeAnalysis]) -> None:
        """Detect driven/animated shape-key weights — same condition as
        ``AnimUtils.bake_blend_shapes()`` uses to pick its targets (reused rather than
        re-derived): a live driver on the shape-keys datablock, or its own action fcurves.
        Unlike the constraint/driver buckets above, mute state is not checked here —
        ``bake_blend_shapes()`` destructively removes a shape-key driver outright regardless of
        mute (there is no mute-and-restore path for this bucket; see ``bake_session.py``)."""
        from blendertk.anim_utils._anim_utils import _slot_fcurves

        for obj in objects:
            if obj.type != "MESH":
                continue
            sk = getattr(obj.data, "shape_keys", None)
            ad = getattr(sk, "animation_data", None) if sk is not None else None
            if ad is None:
                continue
            if ad.drivers:
                names = [fc.data_path for fc in ad.drivers]
            elif ad.action is not None and _slot_fcurves(ad.action):
                names = [fc.data_path for fc in _slot_fcurves(ad.action)]
            else:
                continue

            entry = results.get(obj.name)
            if entry is None:
                entry = BakeAnalysis(object=obj.name)
                results[obj.name] = entry
            entry.driven_sources.setdefault("blend_shape", []).extend(names)

    # -------------------------------------------------------------------------
    # Time Range Detection
    # -------------------------------------------------------------------------

    def get_time_range(self, analysis: Optional[Dict[str, BakeAnalysis]] = None) -> Tuple[int, int]:
        """Determine the optimal bake time range from driver/constraint-target animation.

        Walks each driven source's own animation: a constraint's ``.target`` object, or a
        driver's variable targets (directly, or — for ``"blend_shape"`` — via the shape-keys
        datablock). Falls back to the scene's playback range when no driving animation is found.

        Parameters:
            analysis: Pre-computed analysis dict. If None, runs :func:`analyze`.

        Returns:
            Tuple of (start_frame, end_frame) as integers.
        """
        if analysis is None:
            analysis = self.analyze()

        all_frames: List[float] = []

        for key, data in analysis.items():
            obj, bone_name = _resolve_analysis_key(key)
            if obj is None:
                continue

            for source_type, names in data.driven_sources.items():
                if source_type in ("constraint", "ik"):
                    constraints = _constraints_for(obj, bone_name)
                    for name in names:
                        c = constraints.get(name)
                        target = getattr(c, "target", None) if c is not None else None
                        if target is not None:
                            rng = _object_key_range(target)
                            if rng:
                                all_frames.extend(rng)
                elif source_type == "driver":
                    ad = getattr(obj, "animation_data", None)
                    all_frames.extend(_driver_variable_target_frames(ad, names))
                elif source_type == "blend_shape":
                    sk = getattr(obj.data, "shape_keys", None) if obj.type == "MESH" else None
                    all_frames.extend(_blend_shape_source_frames(sk, names))

        if all_frames:
            return math.floor(min(all_frames)), math.ceil(max(all_frames))

        import bpy

        scene = bpy.context.scene
        return int(scene.frame_start), int(scene.frame_end)

    # -------------------------------------------------------------------------
    # Baking
    # -------------------------------------------------------------------------

    def _save_backup(self) -> Optional[str]:
        """Save a pre-bake ``.blend`` snapshot when ``self.backup_file`` is truthy, without
        disturbing the active document identity.

        Blender's ``wm.save_as_mainfile(copy=True)`` ("Save Copy") writes to *path* but leaves
        ``bpy.data.filepath`` pointing at the original file — unlike mayatk's ``EnvUtils.
        save_scene_backup``, which has to rename-save-then-rename-back since Maya's ``cmds.file``
        has no copy-only save, no restore step is needed here.

        Returns:
            The backup path, or ``None`` (backup not requested, or the scene has never been
            saved — there is no directory to write it next to).
        """
        if not self.backup_file:
            return None

        import bpy

        scene_path = bpy.data.filepath
        if not scene_path:
            logger.warning(
                "SmartBake: cannot save a backup — the scene has not been saved yet."
            )
            return None

        if isinstance(self.backup_file, str):
            final_path = self.backup_file
        else:
            base, ext = os.path.splitext(scene_path)
            final_path = f"{base}_prebake{ext or '.blend'}"

        try:
            bpy.ops.wm.save_as_mainfile(filepath=final_path, copy=True)
            return final_path
        except RuntimeError as e:
            logger.warning(f"SmartBake: failed to save backup: {e}")
            return None

    @undoable
    def bake(
        self,
        analysis: Optional[Dict[str, BakeAnalysis]] = None,
        time_range: Optional[Tuple[int, int]] = None,
    ) -> BakeResult:
        """Bake every driven source :func:`analyze` found.

        Transform sources (``"constraint"``/``"ik"``/``"driver"``) are baked together per
        object/armature via ``AnimUtils.bake_keys(..., use_current_action=False)`` — this creates
        a brand-new Action (confirmed live: ``nla.bake`` never touches an existing Action under
        ``use_current_action=False``) while the pre-bake action, if any, is kept alive via
        ``Action.use_fake_user`` so :func:`restore` can swap it back in. Once an object's new
        action is in place, every constraint/driver :func:`analyze` identified for it is muted
        (``use_override=True``, the default) or removed outright (``delete_sources=True`` —
        destructive; ``delete_sources`` always wins over ``use_override`` when both are set, and
        that portion of the session is recorded non-restorable, mirroring mayatk's
        ``delete_inputs`` precedent). Neither flag set leaves the identified sources live —
        baked, but still fighting the new action every evaluation.

        Blend-shape (shape key) sources go through the separate
        ``AnimUtils.bake_blend_shapes()`` pass, which resamples each driven/animated key in
        place — snapshotted first so restore can reverse it, since there is no mute-and-restore
        path for shape keys (see module docstring / ``bake_session.py``): a live driver is
        snapshotted via ``bake_session.snapshot_blend_shape_driver`` (``bake_blend_shapes``
        deletes it outright), and a key already carrying its own action — no driver — has its
        existing keyframes snapshotted via ``bake_session.snapshot_blend_shape_action``, since
        ``bake_blend_shapes`` densely resamples that SAME fcurve in place with no fresh Action
        datablock to fall back on.

        A restore-manifest session (``bake_session.BakeSessionStore``) is only pushed when
        ``self.restorable`` and the bake actually changed something — a no-op analysis leaves no
        session to restore, matching mayatk's precedent.

        Parameters:
            analysis: Pre-computed analysis. If None, runs :func:`analyze`.
            time_range: Custom time range. If None, auto-detects via :func:`get_time_range`.

        Returns:
            BakeResult with ``baked``/``skipped``/``time_range``/``muted_constraints``/
            ``muted_drivers``/``session_id``/``backup_path`` populated.
        """
        if analysis is None:
            analysis = self.analyze()
        if time_range is None:
            time_range = self.get_time_range(analysis)

        result = BakeResult(time_range=time_range)

        transform_keys = {
            key: data
            for key, data in analysis.items()
            if any(st in data.driven_sources for st in ("constraint", "ik", "driver"))
        }
        blend_shape_keys = {
            key: data for key, data in analysis.items() if "blend_shape" in data.driven_sources
        }

        if not transform_keys and not blend_shape_keys:
            result.skipped = list(analysis.keys())
            return result

        result.backup_path = self._save_backup()

        from blendertk.anim_utils import _anim_utils
        from blendertk.anim_utils.smart_bake import bake_session

        session: Optional[Dict[str, Any]] = None
        if self.restorable:
            session = {
                "version": bake_session.BakeSessionStore.SCHEMA_VERSION,
                "id": bake_session.BakeSessionStore.new_session_id(),
                "restorable": not self.delete_sources,
                "time_range": list(time_range),
                "baked_objects": [],
                "muted_constraints": [],
                "muted_drivers": [],
                "blend_shape_drivers": [],
                "blend_shape_actions": [],
            }

        # ---- Phase 1: transform bake (constraints/IK/drivers) ----
        objects_to_bake = []
        seen = set()
        for key in transform_keys:
            obj, _bone = _resolve_analysis_key(key)
            if obj is not None and obj.name not in seen:
                seen.add(obj.name)
                objects_to_bake.append(obj)

        baked_names = set()
        baked_objects_actual: List[Any] = []
        if objects_to_bake:
            pre_bake_actions = {}
            pre_bake_slots = {}
            prior_fake_user = {}
            for obj in objects_to_bake:
                ad = getattr(obj, "animation_data", None)
                original_action = ad.action if ad is not None else None
                if original_action is not None:
                    prior_fake_user[obj.name] = original_action.use_fake_user
                    original_action.use_fake_user = True
                pre_bake_actions[obj.name] = original_action
                pre_bake_slots[obj.name] = (
                    getattr(ad, "action_slot", None) if ad is not None else None
                )

            _anim_utils.bake_keys(
                objects_to_bake,
                frame_range=time_range,
                step=self.sample_by,
                visual_keying=True,
                # NEVER forward delete_sources here: nla.bake's own clear_constraints wipes an
                # object's/bone's ENTIRE constraint collection unconditionally, not just the
                # ones analyze() identified — deletion of identified sources only happens in the
                # scoped per-source loop below.
                clear_constraints=False,
                use_current_action=False,
            )

            for obj in objects_to_bake:
                ad = getattr(obj, "animation_data", None)
                baked_action = ad.action if ad is not None else None
                original_action = pre_bake_actions.get(obj.name)
                if baked_action is None or baked_action is original_action:
                    continue
                baked_names.add(obj.name)
                baked_objects_actual.append(obj)
                if self.preserve_outside_keys and original_action is not None:
                    _copy_outside_range_keys(
                        original_action,
                        pre_bake_slots.get(obj.name),
                        baked_action,
                        getattr(ad, "action_slot", None),
                        time_range,
                    )
                if session is not None:
                    baked_entry = {
                        "object": bake_session.node_ref(obj),
                        "original_action": bake_session.node_ref(original_action),
                        "baked_action": bake_session.node_ref(baked_action),
                    }
                    if original_action is not None:
                        baked_entry["original_action_prior_fake_user"] = prior_fake_user.get(
                            obj.name, False
                        )
                    session["baked_objects"].append(baked_entry)

        # ---- Mute (or delete) every identified constraint/IK/driver source ----
        muted_constraints: List[str] = []
        muted_drivers: List[str] = []
        for key, data in transform_keys.items():
            obj, bone_name = _resolve_analysis_key(key)
            if obj is None or obj.name not in baked_names:
                continue

            constraint_names = list(data.driven_sources.get("constraint", [])) + list(
                data.driven_sources.get("ik", [])
            )
            if constraint_names:
                constraints = _constraints_for(obj, bone_name)
                for name in constraint_names:
                    c = constraints.get(name)
                    if c is None:
                        continue
                    if self.delete_sources:
                        constraints.remove(c)
                    elif self.use_override:
                        if session is not None:
                            session["muted_constraints"].append(
                                {
                                    "ref": bake_session.constraint_ref(obj, c, bone=bone_name),
                                    "prior_mute": c.mute,
                                }
                            )
                        c.mute = True
                    else:
                        continue
                    muted_constraints.append(name)

            driver_paths = data.driven_sources.get("driver", [])
            if driver_paths:
                ad = getattr(obj, "animation_data", None)
                if ad is not None:
                    for fc in list(ad.drivers):
                        if fc.data_path not in driver_paths:
                            continue
                        if self.delete_sources:
                            ad.drivers.remove(fc)
                        elif self.use_override:
                            if session is not None:
                                session["muted_drivers"].append(
                                    {
                                        "ref": bake_session.driver_ref(obj, fc),
                                        "prior_mute": fc.mute,
                                    }
                                )
                            fc.mute = True
                        else:
                            continue
                        muted_drivers.append(fc.data_path)

            result.baked[key] = constraint_names + list(driver_paths)

        # ---- Phase 2: blend-shape bake (snapshot drivers, then bake_blend_shapes) ----
        baked_bs_objects: List[Any] = []
        if blend_shape_keys and self.bake_blend_shapes:
            bs_objects = []
            seen_bs = set()
            for key in blend_shape_keys:
                obj, _bone = _resolve_analysis_key(key)
                if obj is not None and obj.name not in seen_bs:
                    seen_bs.add(obj.name)
                    bs_objects.append(obj)

            if session is not None:
                for obj in bs_objects:
                    sk = getattr(obj.data, "shape_keys", None)
                    ad = getattr(sk, "animation_data", None) if sk is not None else None
                    if ad is None:
                        continue
                    if ad.drivers:
                        for fc in list(ad.drivers):
                            key_block = _shape_key_block_for_fcurve(sk, fc)
                            if key_block is not None:
                                session["blend_shape_drivers"].append(
                                    bake_session.snapshot_blend_shape_driver(obj, key_block, fc)
                                )
                    elif ad.action is not None:
                        # No driver to snapshot-and-rebuild — this key's weight is animated by
                        # its OWN action, which bake_blend_shapes() resamples densely IN PLACE
                        # (same fcurve, no fresh Action datablock the way the transform bake
                        # gets one). The only way back is recording every existing key so
                        # restore can clear the dense resample and rebuild the originals.
                        from blendertk.anim_utils._anim_utils import _slot_fcurves

                        for fc in _slot_fcurves(ad.action):
                            key_block = _shape_key_block_for_fcurve(sk, fc)
                            if key_block is not None:
                                session["blend_shape_actions"].append(
                                    bake_session.snapshot_blend_shape_action(obj, key_block, fc)
                                )

            baked_bs_objects = _anim_utils.bake_blend_shapes(
                bs_objects, frame_range=time_range, step=self.sample_by
            )
            for obj in baked_bs_objects:
                data = blend_shape_keys.get(obj.name)
                names = data.driven_sources.get("blend_shape", []) if data else []
                result.baked.setdefault(obj.name, [])
                result.baked[obj.name].extend(names)

        # ---- Optimize keys on everything actually baked (transform + blend-shape) ----
        if self.optimize_keys and result.baked:
            optimize_targets: List[Any] = []
            optimized_names: List[str] = []
            for obj in baked_objects_actual:
                optimize_targets.append(obj)
                optimized_names.append(obj.name)
            for obj in baked_bs_objects:
                sk = getattr(obj.data, "shape_keys", None)
                if sk is not None:
                    optimize_targets.append(sk)
                    if obj.name not in optimized_names:
                        optimized_names.append(obj.name)
            if optimize_targets:
                _anim_utils.optimize_keys(optimize_targets)
                result.optimized = sorted(optimized_names)

        result.muted_constraints = muted_constraints
        result.muted_drivers = muted_drivers

        baked_key_set = set(result.baked.keys())
        result.skipped = [key for key in analysis if key not in baked_key_set]

        if session is not None and result.baked:
            bake_session.BakeSessionStore.push(session)
            result.session_id = session["id"]

        return result

    def execute(self) -> BakeResult:
        """High-level entry point: :func:`analyze` then :func:`bake` in one call."""
        analysis = self.analyze()
        return self.bake(analysis)

    # -------------------------------------------------------------------------
    # Restore
    # -------------------------------------------------------------------------

    @classmethod
    def list_sessions(cls) -> List[str]:
        """Ids of restorable bake sessions recorded on this scene's ``data_internal`` carrier,
        oldest first."""
        from blendertk.anim_utils.smart_bake.bake_session import BakeSessionStore

        return BakeSessionStore.list_ids()

    @classmethod
    @undoable
    def restore(cls, session_id: Optional[str] = None) -> "RestoreResult":
        """Reverse a bake session recorded by ``bake(restorable=True)``.

        Restores from the manifest persisted on the ``data_internal`` carrier, so this works in a
        later Blender session after a scene save/reopen: swaps each baked object's action back
        (deleting the baked Action datablock), unmutes constraints/drivers to their recorded prior
        state, and best-effort rebuilds any blend-shape driver ``bake_blend_shapes()`` removed.

        Parameters:
            session_id: Session to restore. None (default) restores the most recent (LIFO). The
                session is removed from the manifest only AFTER the restore pass completes — an
                unexpected failure mid-restore leaves it in place, retryable.

        Returns:
            RestoreResult with the success flag, per-category restore lists, and warnings.
        """
        from blendertk.anim_utils.smart_bake.bake_session import (
            BakeSessionStore,
            RestoreResult,
            restore_session,
        )

        session = BakeSessionStore.peek(session_id)
        if session is None:
            result = RestoreResult(session_id=session_id)
            msg = (
                "No bake session found to restore."
                if session_id is None
                else f"Bake session '{session_id}' not found."
            )
            result.warnings.append(msg)
            logger.warning(f"SmartBake: {msg}")
            return result

        result = restore_session(session)
        # Pop only after the restore pass completes — an unexpected failure mid-restore leaves
        # the session in place so it can be retried.
        BakeSessionStore.pop(session.get("id"))
        for warning in result.warnings:
            logger.warning(f"SmartBake restore: {warning}")
        return result

    @classmethod
    @contextmanager
    def session(cls, **kwargs):
        """Context manager: bake on enter, restore on exit.

        The scene returns to its pre-bake state even if the body raises — made for export
        workflows::

            with SmartBake.session(objects=meshes) as result:
                export_fbx(...)
            # actions swapped back, constraints/drivers unmuted

        Parameters:
            **kwargs: Forwarded to ``SmartBake.__init__`` (``restorable`` is forced True — the
                exit restore depends on the manifest).

        Yields:
            BakeResult from the enter-time bake.
        """
        kwargs["restorable"] = True
        result = cls(**kwargs).execute()
        try:
            yield result
        finally:
            if result.session_id:
                cls.restore(result.session_id)

    @classmethod
    def run(cls, **kwargs) -> BakeResult:
        """Quick entry point for a one-shot bake: ``cls(**kwargs).execute()``.

        Example:
            >>> result = SmartBake.run()
            >>> result = SmartBake.run(objects=[cube], delete_sources=True)
            >>> if result.success:
            ...     print(f"Baked {result.baked_count} objects")
        """
        return cls(**kwargs).execute()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
