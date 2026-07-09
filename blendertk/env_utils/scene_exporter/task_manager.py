# !/usr/bin/python
# coding=utf-8
"""Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
identically-named module. :class:`TaskManager` supplies the methods
:class:`blendertk.env_utils.scene_exporter.task_factory.TaskFactory` discovers by name
(``getattr(self, task_name)`` reflection) -- see that module for the generic dispatch/revert
engine (vendored verbatim, 100% DCC-agnostic).

~26 of mayatk's ~28 tasks/checks are ported here as real Blender implementations (the smart_bake
group uses :mod:`blendertk.anim_utils.smart_bake`; ``export_data_node`` rides the ported
:class:`blendertk.node_utils.data_nodes.DataNodes` carrier). The remaining ~2 depend on
subsystems blendertk doesn't have yet (``hierarchy_manager``, Shots) and are declared in
:attr:`TaskManager.task_definitions` / :attr:`TaskManager.check_definitions` as DISABLED
placeholders (the widget shows in the panel, 1:1 with mayatk's label/position, greyed out with a
tooltip explaining the gap) -- ``TODO(blender-parity)``. No method is defined for a disabled
placeholder: :class:`TaskFactory` gracefully skips a missing method (logs + no-ops), and a
disabled widget can never be toggled to invoke it anyway.
"""
import math
import os
import re
from collections import defaultdict
from typing import Dict, Any, List

import pythontk as ptk

from blendertk.env_utils.scene_exporter.task_factory import TaskFactory


_NEEDS_HIERARCHY_MANAGER = (
    "Not available yet: needs blendertk's hierarchy_manager port (unstarted). "
    "TODO(blender-parity)."
)
_NEEDS_SHOTS = (
    "Not available yet: needs blendertk's Shots port (unstarted). "
    "TODO(blender-parity)."
)

_LOD_SUFFIX_RE = re.compile(r"_lod\d*$", re.IGNORECASE)
_BLENDER_DUP_SUFFIX_RE = re.compile(r"\.\d{3}$")

# Blender has no named-unit enum like Maya's currentUnit(linear=...) -- unit_settings is a
# (system, scale_length) pair. Values chosen so 1 scene unit == 1 of the named unit.
_LINEAR_UNIT_VALUES: Dict[str, Any] = {
    "mm": ("METRIC", 0.001),
    "cm": ("METRIC", 0.01),
    "m": ("METRIC", 1.0),
    "km": ("METRIC", 1000.0),
    "in": ("IMPERIAL", 0.0254),
    "ft": ("IMPERIAL", 0.3048),
    "yd": ("IMPERIAL", 0.9144),
    "mi": ("IMPERIAL", 1609.344),
}


class _TaskDataMixin:
    """Shared, cached scope-resolution helpers for the task/check methods below."""

    @property
    def _has_keyframes(self) -> bool:
        from blendertk.anim_utils._anim_utils import get_fcurves

        if not self.objects:
            return False
        return any(fc.keyframe_points for fc in get_fcurves(self.objects))

    def _get_all_materials(self) -> List:
        """Materials assigned to ``self.objects`` (cached; invalidated on ``objects`` reassign)."""
        from blendertk.mat_utils._mat_utils import get_mats

        if not hasattr(self, "_cached_materials") or self._cached_materials is None:
            self._cached_materials = get_mats(self.objects or [])
        return self._cached_materials

    def _get_export_images(self, materials=None) -> List:
        """Deduplicated image datablocks feeding ``materials`` (default: :meth:`_get_all_materials`).

        The Blender analogue of mayatk's ``_get_export_file_nodes`` (Maya ``file`` nodes).
        """
        from blendertk.mat_utils._mat_utils import _material_image_nodes

        materials = materials if materials is not None else self._get_all_materials()
        seen = []
        for mat in materials:
            if mat is None:
                continue
            for _node, img in _material_image_nodes(mat):
                if img not in seen:
                    seen.append(img)
        return seen

    @staticmethod
    def _workspace_dir() -> str:
        """The saved .blend's directory -- the Blender analogue of Maya's workspace root."""
        import bpy

        return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""


class _TaskActionsMixin(_TaskDataMixin):
    """Export-prep tasks (mutate scene state; ``set_*`` tasks pair with a ``revert_*``)."""

    def set_linear_unit(self, value):
        """Temporarily set the scene's unit system + scale for export. Returns the prior
        ``(system, scale_length)`` for :meth:`revert_linear_unit`."""
        import bpy

        settings = bpy.context.scene.unit_settings
        original = (settings.system, settings.scale_length)
        if value:
            system, scale = value
            settings.system = system
            settings.scale_length = scale
            self.logger.debug(
                f"Changed scene units to {system} (scale_length={scale})."
            )
        return original

    def revert_linear_unit(self, original):
        """Revert to the unit system + scale recorded by :meth:`set_linear_unit`."""
        import bpy

        settings = bpy.context.scene.unit_settings
        settings.system, settings.scale_length = original
        self.logger.debug(f"Reverted scene units to {original}.")

    def exclude_hdr(self, enabled):
        """No-op by design: Blender's World/Environment-Texture network is not a scene object
        and neither the FBX nor GLB exporter ever pulls it into the export set the way Maya's
        ``aiSkyDomeLight`` transform rides into "All Scene Objects" mode -- there is nothing in
        ``self.objects`` to exclude."""
        if enabled:
            self.logger.debug(
                "Exclude HDR Environment: no-op on Blender -- the World shader is never part "
                "of the object export set (unlike Maya's aiSkyDomeLight)."
            )

    def ignore_groups(self, value):
        """Remove objects under any top-level object named in the comma-separated ``value``
        (case-insensitive) from ``self.objects``."""
        if not value or not str(value).strip() or not self.objects:
            return
        names = {n.strip().lower() for n in str(value).split(",") if n.strip()}
        if not names:
            return

        import bpy

        from blendertk.node_utils._node_utils import get_children

        excluded = set()
        for root in (o for o in bpy.data.objects if o.parent is None):
            if root.name.lower() in names:
                excluded.add(root)
                excluded.update(get_children(root, recursive=True))
        if excluded:
            before = len(self.objects)
            self.objects = [o for o in self.objects if o not in excluded]
            removed = before - len(self.objects)
            if removed:
                self.logger.debug(
                    f"Excluded {removed} object(s) under ignored group(s): {sorted(names)}."
                )

    def reassign_duplicate_materials(self):
        """Reassign every object using a duplicate material to the group's canonical material."""
        from blendertk.mat_utils._mat_utils import (
            find_materials_with_duplicate_textures,
            reassign_duplicate_materials as _reassign,
        )

        materials = self._get_all_materials()
        groups = find_materials_with_duplicate_textures(materials=materials)
        if not groups:
            return
        count = _reassign(groups, delete=True)
        self._cached_materials = None
        self.logger.debug(f"Reassigned {count} duplicate-material slot(s).")

    def convert_to_relative_paths(self):
        """Copy external textures into the project's textures folder, then convert their paths
        to ``//``-relative (the Blender analogue of mayatk's sourceimages + relative-path task)."""
        from blendertk.mat_utils._mat_utils import normalize_texture_paths

        images = self._get_export_images()
        if not images:
            return
        copied = normalize_texture_paths(mode="copy", images=images)
        if copied:
            self.logger.info(
                f"Copied {copied} external texture(s) into the project textures folder "
                "before relative-path conversion."
            )
        normalize_texture_paths(mode="relative", images=images)

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths by searching the .blend's directory."""
        from blendertk.mat_utils._mat_utils import resolve_missing_textures

        images = self._get_export_images()
        if not images:
            return
        search_dir = self._workspace_dir()
        if not search_dir:
            self.logger.debug(
                "No saved .blend directory to search for missing textures. Skipping."
            )
            return
        resolved = resolve_missing_textures(
            search_dir, recursive=True, stem=True, texture=True, images=images
        )
        if resolved:
            self.logger.info(f"Resolved {resolved} missing texture path(s).")

    def smart_bake(self):
        """Pre-bake constrained/driven objects before export.

        Uses SmartBake to detect constraints (including IK), drivers, and driven blend-shape
        weights, then bakes each into a fresh Action while muting the sources that were
        fighting it -- non-destructive: the pre-bake action and the constraint/driver network
        both survive, and the bake is restorable via ``SmartBake.restore()``.
        """
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        baker = SmartBake(objects=self.objects, sample_by=1)
        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info("No constrained/driven objects found. Skipping smart bake.")
            return

        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} object(s)/bone(s) requiring bake.")

        result = baker.bake(analysis)
        if result.session_id:
            self._bake_session_id = result.session_id

        log_parts = [
            f"Smart bake completed: {result.baked_count} unit(s) baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        # SmartBake never optimizes its own output -- that's the separate optimize_keys
        # task's job (TASK_ORDER runs it immediately after this one).
        if getattr(self, "_optimize_keys_enabled", False):
            log_parts.append("optimize_keys will run next")
        self.logger.info(", ".join(log_parts) + ".")

    def optimize_keys(self):
        """Remove redundant animation keys from all exported objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        from blendertk.anim_utils._anim_utils import optimize_keys as _optimize_keys

        self.logger.info("Optimizing baked animation keys...")
        stats = _optimize_keys(self.objects)
        self.logger.info(
            f"Optimization completed: {stats['curves_before']} -> {stats['curves_after']} "
            f"curve(s), {stats['keys_before']} -> {stats['keys_after']} key(s)."
        )

    def tie_all_keyframes(self):
        """Tie (bookend) keyframes at the union keyed extent across all exported objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        from blendertk.anim_utils._anim_utils import tie_keyframes

        self.logger.info("Tying keyframes for all objects.")
        changed = tie_keyframes(self.objects, absolute=True)
        self.logger.info(f"Tied {changed} keyframe(s).")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        from blendertk.anim_utils._anim_utils import snap_keys

        self.logger.info("Snapping keyframes to nearest whole frame.")
        snapped = snap_keys(self.objects)
        self.logger.info(f"Snapped {snapped} keyframe(s).")

    def set_bake_animation_range(self):
        """Set the scene's playback range to the exported objects' keyframe extent.

        Blender's FBX exporter bakes over the *scene's* frame range (``bake_anim=True`` in
        ``_scene_exporter.py`` -- there is no separate "bake complex start/end" knob the way
        Maya's FBX plugin exposes via MEL), so the analogue of mayatk's auto-range task is to
        set the scene's own range for the export and revert it after. Runs last in the
        animation phase (TASK_ORDER) so it captures the final, post-processing extent.
        """
        from blendertk.anim_utils._anim_utils import _key_range, get_fcurves

        rng = _key_range(get_fcurves(self.objects))
        if rng is None:
            self.logger.debug("No keyframes found. Skipping frame range setting.")
            return None

        import bpy

        scene = bpy.context.scene
        original = (scene.frame_start, scene.frame_end)
        start, end = math.floor(rng[0]), math.ceil(rng[1])
        scene.frame_start, scene.frame_end = int(start), int(end)
        self.logger.info(f"Set animation range to start: {int(start)}, end: {int(end)}")
        return original

    def revert_bake_animation_range(self, original):
        """Revert the scene's frame range recorded by :meth:`set_bake_animation_range`."""
        import bpy

        bpy.context.scene.frame_start, bpy.context.scene.frame_end = original
        self.logger.debug(f"Reverted animation range to {original}.")

    def export_data_node(self):
        """Include the shared ``data_export`` carrier in the export (default on).

        ``data_export`` is the single Empty every metadata producer stamps
        (Lightmap Baker → ``lightmap_metadata``; Shots / Audio when ported).
        The mesh-only export object sets would otherwise omit it and the
        metadata silently wouldn't ship.  Appends the carrier to the export
        set so its custom properties ride into the FBX as user properties
        (``use_custom_props`` + Empty-inclusive ``object_types`` — both on by
        default in ``_DEFAULT_FBX_OPTIONS``).

        Mirror of mayatk's ``export_data_node``, minus the producer-refresh
        step: Blender has no before-export event, so producers publish at
        authoring time (e.g. the lightmap baker publishes when a bake
        completes) and the carrier is already current at export.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        carrier = DataNodes.get_export_node(create=False)
        if carrier is None:
            self.logger.debug("No data_export carrier in scene — nothing to include.")
            return

        # The FBX funnel exports via use_selection + select_set, which can only
        # ship selectable, visible objects — a hidden carrier would silently
        # drop the metadata, so clear any hide state before including it.
        # Deliberately NOT restored afterwards: task reverts run when
        # run_tasks returns, which is BEFORE the FBX write — re-hiding there
        # would drop the carrier from the export again (proven by the
        # hidden-carrier round-trip check in test_scene_exporter.py).
        was_hidden = carrier.hide_select or carrier.hide_viewport
        carrier.hide_select = False
        carrier.hide_viewport = False
        try:
            if not carrier.visible_get():
                was_hidden = True
                carrier.hide_set(False)
        except RuntimeError:  # not in the active view layer
            pass
        if was_hidden:
            self.logger.info("data_export carrier was hidden — cleared for export.")

        if carrier not in (self.objects or []):
            self.objects = list(self.objects or []) + [carrier]
            self.logger.info("data_export carrier added to the export set.")
        self._log_data_node_summary()

    def _log_data_node_summary(self):
        """Log what metadata actually shipped on ``data_export``.

        Makes a silently-empty export distinguishable from a populated one —
        mirror of mayatk's channel-agnostic summary: every string custom
        property on the carrier is summarized by entry count (JSON array /
        dict-of-list / whitespace-token wire string), so new producers show up
        with no exporter edits.  Pure logging convenience — fully best-effort
        so it can never abort the export it describes.
        """
        try:
            import json

            from blendertk.node_utils.data_nodes import DataNodes

            carrier = DataNodes.get_export_node(create=False)
            if carrier is None:
                return

            def entry_count(raw: str) -> int:
                try:
                    data = json.loads(raw)
                except ValueError:
                    return len(raw.split())  # wire strings, e.g. "frame:label …"
                if isinstance(data, list):
                    return len(data)
                if isinstance(data, dict):
                    for value in data.values():
                        if isinstance(value, list):
                            return len(value)
                return 1

            parts = []
            for key in carrier.keys():
                raw = carrier.get(key)
                if isinstance(raw, str) and raw:
                    n = entry_count(raw)
                    parts.append(f"{key} ({n} entr{'y' if n == 1 else 'ies'})")
            if parts:
                self.logger.info("Embedded on data_export: " + ", ".join(parts) + ".")
        except Exception:  # a summary must never break the export it describes
            self.logger.debug("data_export summary skipped.", exc_info=True)


class _TaskChecksMixin(_TaskDataMixin):
    """Validation checks -- each returns ``(passed: bool, messages: list[str])``."""

    def check_framerate(self, target_key) -> tuple:
        if not target_key or not self._has_keyframes:
            return True, []
        target = ptk.VidUtils.FRAME_RATES.get(target_key)
        if target is None:
            return True, []
        import bpy

        scene = bpy.context.scene
        actual = scene.render.fps / scene.render.fps_base
        if abs(actual - target) > 1e-3:
            return False, [f"Scene FPS ({actual:g}) does not match target ({target:g})."]
        return True, []

    def check_referenced_objects(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import list_libraries

        libs = list_libraries()
        if libs:
            names = ", ".join(r["name"] for r in libs)
            return False, [
                f"Scene has {len(libs)} linked librar{'y' if len(libs) == 1 else 'ies'}: {names}"
            ]
        return True, []

    def check_geometry_lod_suffix(self, enabled) -> tuple:
        """Informational only -- always succeeds (mirrors mayatk's contract)."""
        if not enabled or not self.objects:
            return True, []
        found = [o.name for o in self.objects if o.type == "MESH" and _LOD_SUFFIX_RE.search(o.name)]
        if found:
            shown = ", ".join(found[:10]) + (" …" if len(found) > 10 else "")
            return True, [f"{len(found)} object(s) use an LOD suffix: {shown}"]
        return True, []

    def check_duplicate_locator_names(self, enabled) -> tuple:
        """Empties sharing a base name once Blender's auto ``.001``-style suffix is stripped --
        the closest Blender analogue of Maya's same-short-name-under-different-parents locator
        collision (Blender itself force-uniques ``bpy.data.objects`` names, so the exact Maya
        failure mode can't occur; this catches the case that motivated the check)."""
        if not enabled or not self.objects:
            return True, []
        groups = defaultdict(list)
        for o in self.objects:
            if o.type == "EMPTY":
                groups[_BLENDER_DUP_SUFFIX_RE.sub("", o.name)].append(o.name)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if dupes:
            messages = [f"'{base}': {', '.join(names)}" for base, names in dupes.items()]
            return False, ["Duplicate Empty base name(s) detected:"] + messages
        return True, []

    def check_root_default_transforms(self, enabled) -> tuple:
        """Root groups (an Empty with children) should sit at identity transform."""
        if not enabled or not self.objects:
            return True, []
        from blendertk.node_utils._node_utils import get_parent

        roots = set()
        for o in self.objects:
            chain = get_parent(o, all=True)
            root = chain[-1] if chain else o
            if root.type == "EMPTY" and root.children:
                roots.add(root)

        bad = []
        for root in roots:
            loc = tuple(round(v, 5) for v in root.location)
            rot = tuple(round(v, 5) for v in root.rotation_euler)
            scale = tuple(round(v, 5) for v in root.scale)
            if loc != (0.0, 0.0, 0.0) or rot != (0.0, 0.0, 0.0) or scale != (1.0, 1.0, 1.0):
                bad.append(root.name)
        if bad:
            return False, [f"Root group(s) with non-default transform: {', '.join(bad)}"]
        return True, []

    def check_hidden_geometry(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        hidden = [o.name for o in self.objects if o.type == "MESH" and not o.visible_get()]
        if hidden:
            shown = ", ".join(hidden[:10]) + (" …" if len(hidden) > 10 else "")
            return False, [f"{len(hidden)} hidden mesh object(s) will be exported: {shown}"]
        return True, []

    def check_overlapping_duplicate_mesh(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        from blendertk.edit_utils._edit_utils import get_overlapping_duplicates

        dupes = get_overlapping_duplicates(objects=self.objects)
        if dupes:
            shown = ", ".join(o.name for o in dupes[:10]) + (" …" if len(dupes) > 10 else "")
            return False, [f"{len(dupes)} overlapping duplicate mesh object(s) found: {shown}"]
        return True, []

    def check_objects_below_floor(self, enabled, tolerance: float = 0.5) -> tuple:
        """Blender is Z-up natively (Maya's version checks Y)."""
        if not enabled or not self.objects:
            return True, []
        from blendertk.xform_utils._xform_utils import get_world_bbox

        below = []
        for o in self.objects:
            if o.type != "MESH":
                continue
            mn, _mx = get_world_bbox(o)
            if mn.z < -tolerance:
                below.append(o.name)
        if below:
            shown = ", ".join(below[:10]) + (" …" if len(below) > 10 else "")
            return False, [f"{len(below)} object(s) dip below the floor (Z=0): {shown}"]
        return True, []

    def check_duplicate_materials(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.mat_utils._mat_utils import find_materials_with_duplicate_textures

        groups = find_materials_with_duplicate_textures(materials=self._get_all_materials())
        if groups:
            messages = [", ".join(m.name for m in g) for g in groups]
            return False, [f"{len(groups)} duplicate material group(s) found:"] + messages
        return True, []

    def check_absolute_paths(self, enabled) -> tuple:
        if not enabled:
            return True, []
        absolute = [
            img.name
            for img in self._get_export_images()
            if (getattr(img, "filepath", "") or "") and not img.filepath.startswith("//")
        ]
        if absolute:
            shown = ", ".join(absolute[:10]) + (" …" if len(absolute) > 10 else "")
            return False, [f"{len(absolute)} texture(s) use an absolute path: {shown}"]
        return True, []

    def check_valid_paths(self, enabled) -> tuple:
        """Every FILE image and linked library in the .blend resolves on disk (whole-file scope,
        matching Maya's version -- not limited to the export object set)."""
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import list_libraries
        from blendertk.mat_utils._mat_utils import get_image_records

        missing = [r["name"] for r in get_image_records() if not r["exists"]]
        missing += [r["name"] for r in list_libraries() if not r["exists"]]
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            return False, [f"{len(missing)} missing file(s): {shown}"]
        return True, []

    def check_texture_file_size(self, max_mb) -> tuple:
        if not max_mb:
            return True, []
        from blendertk.mat_utils._mat_utils import get_texture_paths

        paths = get_texture_paths(objects=self.objects, absolute=True)
        oversized = []
        for p in paths:
            try:
                size_mb = os.path.getsize(p) / (1024 * 1024)
            except OSError:
                continue
            if size_mb > max_mb:
                oversized.append(f"{os.path.basename(p)} ({size_mb:.1f} MB)")
        if oversized:
            shown = ", ".join(oversized[:10]) + (" …" if len(oversized) > 10 else "")
            return False, [f"{len(oversized)} texture(s) exceed {max_mb} MB: {shown}"]
        return True, []

    def check_untied_keyframes(self, enabled) -> tuple:
        """Verify every animated channel has a bookend key at its object's own keyed extent
        (the inverse of what ``tie_all_keyframes`` fixes)."""
        if not enabled or not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import get_fcurves

        untied = []
        for o in self.objects:
            bounds = [
                (fc, fc.keyframe_points[0].co.x, fc.keyframe_points[-1].co.x)
                for fc in get_fcurves([o])
                if len(fc.keyframe_points)
            ]
            if not bounds:
                continue
            min_start = min(b[1] for b in bounds)
            max_end = max(b[2] for b in bounds)
            for fc, start, end in bounds:
                if start > min_start or end < max_end:
                    untied.append(
                        f"{o.name}.{fc.data_path}[{fc.array_index}] ({start:g}-{end:g} != "
                        f"{min_start:g}-{max_end:g})"
                    )

        if untied:
            shown = ", ".join(untied[:10]) + (" …" if len(untied) > 10 else "")
            return False, [f"{len(untied)} curve(s) with untied keyframes: {shown}"]
        return True, []

    def check_floating_point_keys(self, enabled) -> tuple:
        """Detect keyframes that don't sit on a whole frame."""
        if not enabled or not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import get_fcurves

        offenders = []
        for o in self.objects:
            for fc in get_fcurves([o]):
                for k in fc.keyframe_points:
                    if abs(k.co.x - round(k.co.x)) > 1e-4:
                        offenders.append(
                            f"{o.name}.{fc.data_path}[{fc.array_index}] (frame {k.co.x:.3f})"
                        )
                        break

        if offenders:
            shown = ", ".join(offenders[:10]) + (" …" if len(offenders) > 10 else "")
            return False, [f"{len(offenders)} curve(s) have floating point keys: {shown}"]
        return True, []


class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task/check UI definitions for the Scene Exporter -- mirror of mayatk's
    ``TaskManager`` (see module docstring for the ported-vs-placeholder split)."""

    TASK_ORDER = [
        # No Blender analogue of Maya's "set_workspace" (project-directory switch) — omitted
        # entirely rather than placeholder-disabled; there's no Blender concept for it to gate.
        "set_linear_unit",
        "ignore_groups",
        "exclude_hdr",
        "reassign_duplicate_materials",
        "resolve_invalid_texture_paths",
        "convert_to_relative_paths",
        # Phase 4 — Animation (bake THEN optimize THEN snap/tie THEN set range)
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "set_bake_animation_range",
        # Phase 5 — Metadata carrier (last, so it sees the final export set)
        "export_data_node",
    ]

    _export_mode_options: Dict[str, Any] = {
        "Export: All Scene Objects": "all",
        "Export: All Visible Objects": "visible",
        "Export: Selected Objects Only": "selected",
    }

    _frame_rate_options: Dict[str, Any] = {
        (
            f"Check Scene FPS: {k}"
            if v is None
            else (
                f"Check Scene FPS: {v:g} fps"
                if any(c.isdigit() for c in k)
                else f"Check Scene FPS: {k} ({v:g} fps)"
            )
        ): (k if v is not None else None)
        for k, v in ptk.insert_into_dict(ptk.VidUtils.FRAME_RATES, "OFF", None).items()
    }

    _scene_unit_options: Dict[str, Any] = {
        f"Set Linear Unit: {k}": v
        for k, v in ptk.insert_into_dict(_LINEAR_UNIT_VALUES, "OFF", None).items()
    }

    _texture_size_options: Dict[str, Any] = {
        "Check Max Texture Size: OFF": None,
        "Check Max Texture Size: 4 MB": 4,
        "Check Max Texture Size: 8 MB": 8,
        "Check Max Texture Size: 16 MB": 16,
        "Check Max Texture Size: 32 MB": 32,
        "Check Max Texture Size: 64 MB": 64,
        "Check Max Texture Size: 128 MB": 128,
    }

    def __init__(self, logger):
        super().__init__(logger)

        self.logger = logger
        self._objects = None
        self._cached_materials = None

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Invalidate the materials cache whenever objects change."""
        self._objects = value
        self._cached_materials = None

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI."""
        return {
            "sep_general": {"widget_type": "Separator", "title": "General"},
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "setToolTip": "Choose what objects to export:\n- All Visible Objects: Export all visible geometry in the scene\n- Selected Objects Only: Export only currently selected objects\n- All Scene Objects: Export all objects regardless of visibility or selection",
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "setToolTip": "Linear unit to be used during export.",
                "add": self._scene_unit_options,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "setText": "Exclude HDR Environment",
                "setToolTip": (
                    "No-op on Blender: the World shader is never part of the object export "
                    "set (unlike Maya's aiSkyDomeLight, which rides into 'All Scene Objects' "
                    "mode). Kept for panel parity."
                ),
                "setChecked": True,
            },
            "sep_materials": {"widget_type": "Separator", "title": "Materials"},
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": "Reassign any duplicate materials to a single material.",
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "setText": "Convert To Relative Paths",
                "setToolTip": (
                    "Convert texture paths to //-relative (Blender's project-relative form).\n"
                    "External textures are first copied into the project's textures folder "
                    "(if not already there) so the relative paths still resolve."
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": "Attempt to resolve missing texture paths by searching the .blend's directory.",
                "setChecked": True,
            },
            "sep_anim": {"widget_type": "Separator", "title": "Animation"},
            "smart_bake": {
                "widget_type": "QCheckBox",
                "setText": "Smart Bake",
                "setToolTip": (
                    "Intelligently bake constraints (including IK), drivers/expressions, "
                    "and driven blend-shape weights to keyframes.\nAuto-detects the time "
                    "range from the driving animation. Bakes into a fresh Action while "
                    "muting the identified sources; the pre-bake scene state is restorable "
                    "afterward (SmartBake.restore)."
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "setText": "Optimize Keys",
                "setToolTip": (
                    "Remove static curves and redundant flat keys from all exported "
                    "objects, including any curves Smart Bake just created.\nBoundary "
                    "keys are always kept."
                ),
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": (
                    "Insert bookend keyframes at the union keyed extent across all "
                    "exported objects, so every animated channel has a key at both "
                    "range boundaries."
                ),
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "setText": "Snap Keys To Frame",
                "setToolTip": "Snap all keyframes to the nearest whole frame.",
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": (
                    "Set the scene's frame range to the first and last keyframes of the "
                    "exported objects for the duration of the export (reverted "
                    "afterward).\nRuns after Smart Bake/Optimize/Snap/Tie, so it captures "
                    "the final keyframe extent."
                ),
                "setChecked": True,
            },
            "apply_declared_takes": {
                "widget_type": "QCheckBox",
                "setText": "Export Shots as Animation Takes",
                "setToolTip": _NEEDS_SHOTS,
                "setChecked": False,
                "setEnabled": False,
            },
            "sep_hierarchy": {"widget_type": "Separator", "title": "Hierarchy"},
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "setPlaceholderText": "Group names to ignore (comma-separated)",
                "setToolTip": "Comma-separated names of top-level objects to exclude from export (case-insensitive).\nExample: temp, proxy\nLeave empty to skip.",
                "setText": "temp",
                "value_method": "text",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "setText": "Export Scene Data Node",
                "setToolTip": (
                    "Include the shared data_export carrier in the export so its "
                    "embedded metadata (e.g. the Lightmap Baker's "
                    "lightmap_metadata) ships in the FBX as user properties.\n"
                    "The mesh-only export modes would otherwise omit it.  "
                    "No-ops when the scene has no carrier."
                ),
                "setChecked": True,
            },
            "sep_output": {"widget_type": "Separator", "title": "Output"},
            "version": {
                "widget_type": "QLineEdit",
                "setPlaceholderText": "{stem}_v{n:03d}  — empty disables",
                "setToolTip": (
                    "Version format for the export filename. Placeholders:\n"
                    "  {stem}  output basename\n"
                    "  {n:NNd} version number (zero-padded, NN digits)\n"
                    "  {date}  YYYY-MM-DD\n"
                    "  {user}  OS username (embeds dev identity — beware shared exports)\n"
                    "  {scene} .blend basename (requires a saved file)\n"
                    "Extension is handled automatically — do not include {ext}."
                ),
                "setText": "",
                "value_method": "text",
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI."""
        return {
            "sep_general": {"widget_type": "Separator", "title": "General"},
            "check_framerate": {
                "widget_type": "ComboBox",
                "setToolTip": "Check the scene framerate against the target framerate.",
                "add": self._frame_rate_options,
            },
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "setText": "Check For Referenced Objects.",
                "setToolTip": "Check for linked libraries (Blender's analogue of Maya file references).",
                "setChecked": True,
            },
            "sep_hierarchy": {"widget_type": "Separator", "title": "Hierarchy & Naming"},
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": "Detect geometry named with LOD suffixes ending in '_LOD' or '_LOD' followed by digits (e.g., _LOD, _LOD1, _LOD02). This is informational.",
                "setChecked": True,
            },
            "check_duplicate_locator_names": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Locator Names",
                "setToolTip": "Check for Empties sharing a base name (once Blender's auto '.001' suffix is stripped).",
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "setText": "Check Root Default Transforms",
                "setToolTip": "Check for default transforms on root group Empties.\nLocation/Rotation should be (0, 0, 0) and Scale (1, 1, 1).",
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": _NEEDS_HIERARCHY_MANAGER,
                "setChecked": False,
                "setEnabled": False,
            },
            "sep_geometry": {"widget_type": "Separator", "title": "Geometry"},
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "setText": "Check For Hidden Geometry.",
                "setToolTip": "Check for hidden geometry that will be exported.",
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": "Check for overlapping duplicate geometry.",
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "setText": "Check For Objects Below Floor.",
                "setToolTip": (
                    "Check for geometry dipping below Z=0 (Blender is Z-up). A default 0.5 "
                    "unit tolerance is applied so shallow penetrations do not immediately fail."
                ),
                "setChecked": True,
            },
            "sep_materials": {"widget_type": "Separator", "title": "Materials"},
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Materials.",
                "setToolTip": "Check for duplicate materials.",
                "setChecked": True,
            },
            "check_absolute_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Absolute Paths.",
                "setToolTip": "Check for absolute (non //-relative) texture paths.",
                "setChecked": True,
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Valid Paths.",
                "setToolTip": "Check if all file paths (textures, linked libraries) exist on disk.",
                "setChecked": True,
            },
            "check_texture_file_size": {
                "widget_type": "ComboBox",
                "add": self._texture_size_options,
                "setCurrentIndex": 3,  # Default to 16 MB
                "setToolTip": (
                    "Fail the export when any texture feeding the export materials exceeds "
                    "the selected size on disk. Set to OFF to disable."
                ),
                "value_method": "currentData",
            },
            "sep_anim": {"widget_type": "Separator", "title": "Animation"},
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Check For Untied Keyframes",
                "setToolTip": (
                    "Check that every animated channel has a bookend keyframe at its "
                    "object's own keyed extent (the inverse of Tie All Keyframes)."
                ),
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "setText": "Check For Floating Point Keys",
                "setToolTip": "Check for keyframes that are not on whole frames.",
                "setChecked": True,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
