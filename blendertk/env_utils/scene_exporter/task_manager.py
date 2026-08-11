# !/usr/bin/python
# coding=utf-8
"""Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
identically-named module. :class:`TaskManager` supplies the methods
:class:`pythontk.core_utils.task_factory.TaskFactory` discovers by name
(``getattr(self, task_name)`` reflection) -- see that module for the generic dispatch/revert
engine (the pythontk single source of truth, 100% DCC-agnostic).

~26 of mayatk's ~28 tasks/checks are ported here as real Blender implementations (the smart_bake
group uses :mod:`blendertk.anim_utils.smart_bake`; ``export_data_node`` rides the ported
:class:`blendertk.node_utils.data_nodes.DataNodes` carrier). The remaining ~2 depend on
integrations blendertk doesn't have yet (the exporter-side hierarchy diff *check* — the
scene-data sidecar itself IS written, by the engine (``_write_scene_data_sidecar``); the
Shots export-view/FBX-take projection — the Shots subsystem itself is ported) and are declared
in :attr:`TaskManager.task_definitions` / :attr:`TaskManager.check_definitions` as DISABLED
placeholders (the widget shows in the panel, 1:1 with mayatk's label/position, greyed out with a
tooltip explaining the gap) -- ``TODO(blender-parity)``. No method is defined for a disabled
placeholder: :class:`TaskFactory` gracefully skips a missing method (logs + no-ops), and a
disabled widget can never be toggled to invoke it anyway.
"""

import math
import os
import re
from collections import defaultdict
from typing import Any, Dict, List

import pythontk as ptk

from pythontk import TaskFactory

from blendertk.core_utils._core_utils import CoreUtils


_NEEDS_HIERARCHY_MANAGER = (
    "Not available yet: the scene-data sidecar (diff baseline + data_export "
    "snapshot) is written on export, but the exporter-side hierarchy diff "
    "check isn't wired yet. TODO(blender-parity)."
)
_NEEDS_SHOTS = (
    "Not available yet: the Shots subsystem is ported, but the export-view / "
    "FBX-animation-take projection it feeds this task from is a documented "
    "follow-up (see anim_utils/shots — publish_export_view is a no-op). "
    "TODO(blender-parity)."
)

_LOD_SUFFIX_RE = re.compile(r"_lod\d*$", re.IGNORECASE)

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
        from blendertk.anim_utils._anim_utils import AnimUtils

        if not self.objects:
            return False
        return any(fc.keyframe_points for fc in AnimUtils.get_fcurves(self.objects))

    def _get_all_materials(self) -> List:
        """Materials assigned to ``self.objects`` (cached; invalidated on ``objects`` reassign)."""
        from blendertk.mat_utils._mat_utils import MatUtils

        if not hasattr(self, "_cached_materials") or self._cached_materials is None:
            self._cached_materials = MatUtils.get_mats(self.objects or [])
        return self._cached_materials

    def _get_export_images(self, materials=None) -> List:
        """Deduplicated image datablocks feeding ``materials`` (default: :meth:`_get_all_materials`).

        The Blender analogue of mayatk's ``_get_export_file_nodes`` (Maya ``file`` nodes).
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        materials = materials if materials is not None else self._get_all_materials()
        seen = []
        for mat in materials:
            if mat is None:
                continue
            for _node, img in _MatUtilsInternal._material_image_nodes(mat):
                if img not in seen:
                    seen.append(img)
        return seen

    @staticmethod
    def _workspace_dir() -> str:
        """The saved .blend's directory -- the Blender analogue of Maya's workspace root."""
        import bpy

        return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""

    @staticmethod
    def _scene():
        """The active scene, resolved through the package's context accessor.

        Routed via ``CoreUtils._active_view_layer`` (whose ``id_data`` is the
        owning scene) rather than read straight off ``bpy.context``: the panel
        runs from tentacle's Qt event-pump timer, a context where
        ``bpy.context.window`` is ``None`` and parts of the context are unset.
        That accessor is the one place in blendertk that knows the fallback
        chain, so scene reads here inherit it instead of re-deriving it. It
        falls back to ``bpy.context.scene`` itself, so this is never worse than
        the direct read -- unlike ``selected_objects`` / ``active_object``,
        which are *proven* empty in that context (``test_core_utils.py``).
        """
        import bpy

        vl = CoreUtils._active_view_layer()
        return vl.id_data if vl is not None else bpy.context.scene


class _TaskActionsMixin(_TaskDataMixin):
    """Export-prep tasks (mutate scene state).

    A ``set_*`` task either pairs with a ``revert_*`` (undone when ``run_tasks``
    returns) or, when the FBX *write* itself reads the mutation, returns ``None``
    and stages its restore via ``TaskFactory.stage_deferred_restore``.
    """

    def set_linear_unit(self, value):
        """Set the scene's unit system + scale for the duration of the export.

        **Staged, not ``set_``/``revert_``-paired.** Blender's FBX exporter reads
        ``scene.unit_settings.scale_length`` when it *writes* (``apply_unit_scale``
        is on by default), while the ``revert_<x>`` pair the TaskFactory wires up
        fires when ``run_tasks`` returns -- *before* the write (see
        ``TaskFactory._get_revert_method``: only mutations the export doesn't read
        may revert that way). Pairing this task therefore undid the unit change
        before it could take effect, making the task inert. The restore rides
        ``TaskFactory.stage_deferred_restore`` instead, so the unit survives the
        write and is undone immediately after it.

        Returns ``None`` unconditionally -- a non-None result is what would
        re-arm the too-early revert.
        """
        if not value:
            return None
        settings = self._scene().unit_settings
        original = (settings.system, settings.scale_length)

        def restore():
            reverting = self._scene().unit_settings
            reverting.system, reverting.scale_length = original
            self.logger.debug(f"Reverted scene units to {original}.")

        self.stage_deferred_restore("linear_unit", restore)
        system, scale = value
        settings.system = system
        settings.scale_length = scale
        self.logger.debug(f"Changed scene units to {system} (scale_length={scale}).")
        return None

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

        from blendertk.node_utils._node_utils import NodeUtils

        excluded = set()
        for root in (o for o in bpy.data.objects if o.parent is None):
            if root.name.lower() in names:
                excluded.add(root)
                excluded.update(NodeUtils.get_children(root, recursive=True))
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
        from blendertk.mat_utils._mat_utils import MatUtils

        _reassign = MatUtils.reassign_duplicate_materials
        materials = self._get_all_materials()
        groups = MatUtils.find_materials_with_duplicate_textures(materials=materials)
        if not groups:
            return
        count = _reassign(groups, delete=True)
        self._cached_materials = None
        self.logger.debug(f"Reassigned {count} duplicate-material slot(s).")

    def convert_to_relative_paths(self):
        """Copy external textures into the project's textures folder, then convert their paths
        to ``//``-relative (the Blender analogue of mayatk's sourceimages + relative-path task)."""
        from blendertk.mat_utils._mat_utils import MatUtils

        images = self._get_export_images()
        if not images:
            return
        copied = MatUtils.normalize_texture_paths(mode="copy", images=images)
        if copied:
            self.logger.info(
                f"Copied {copied} external texture(s) into the project textures folder "
                "before relative-path conversion."
            )
        MatUtils.normalize_texture_paths(mode="relative", images=images)

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths by searching the .blend's directory."""
        from blendertk.mat_utils._mat_utils import MatUtils

        images = self._get_export_images()
        if not images:
            return
        search_dir = self._workspace_dir()
        if not search_dir:
            self.logger.debug(
                "No saved .blend directory to search for missing textures. Skipping."
            )
            return
        resolved = MatUtils.resolve_missing_textures(
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
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
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

        from blendertk.anim_utils._anim_utils import AnimUtils

        _optimize_keys = AnimUtils.optimize_keys
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

        from blendertk.anim_utils._anim_utils import AnimUtils

        self.logger.info("Tying keyframes for all objects.")
        changed = AnimUtils.tie_keyframes(self.objects, absolute=True)
        self.logger.info(f"Tied {changed} keyframe(s).")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        from blendertk.anim_utils._anim_utils import AnimUtils

        self.logger.info("Snapping keyframes to nearest whole frame.")
        snapped = AnimUtils.snap_keys(self.objects)
        self.logger.info(f"Snapped {snapped} keyframe(s).")

    def set_bake_animation_range(self):
        """Set the scene's playback range to the exported objects' keyframe extent.

        Blender's FBX exporter bakes over the *scene's* frame range (``bake_anim=True`` in
        ``_scene_exporter.py`` -- there is no separate "bake complex start/end" knob the way
        Maya's FBX plugin exposes via MEL), so the analogue of mayatk's auto-range task is to
        set the scene's own range for the export. Runs last in the animation phase
        (TASK_ORDER) so it captures the final, post-processing extent.

        **Staged, not ``set_``/``revert_``-paired** -- for the same reason as
        :meth:`set_linear_unit`: the range is read by the *write*, and the paired
        revert fires before it. Returns ``None`` so that pairing stays disarmed;
        the restore rides ``stage_deferred_restore`` instead.

        The range is the FULL evaluated extent (``AnimUtils.get_animated_extent``):
        active-action fcurves UNION non-muted NLA strip extents in scene time UNION
        data-level / shape-key fcurve ranges. The FBX write bakes the *evaluated*
        scene, so an NLA-strip-only or shape-key-driven object used to export with
        a wrong bake range (the active-action reader saw no keys at all).
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        rng = AnimUtils.get_animated_extent(self.objects)
        if rng is None:
            self.logger.debug("No keyframes found. Skipping frame range setting.")
            return None

        start, end = math.floor(rng[0]), math.ceil(rng[1])
        self._set_frame_range(start, end)
        self.logger.info(f"Set animation range to start: {int(start)}, end: {int(end)}")
        return None

    def export_data_node(self):
        """Include the shared ``data_export`` carrier in the export (default on).

        ``data_export`` is the single Empty every metadata producer stamps
        (Lightmap Baker → ``lightmap_metadata``; Shots / Audio when ported).
        The mesh-only export object sets would otherwise omit it and the
        metadata silently wouldn't ship.  Appends the carrier to the export
        set so its custom properties ride into the FBX as user properties
        (``use_custom_props`` + Empty-inclusive ``object_types`` — both on by
        default in ``_DEFAULT_FBX_OPTIONS``).

        Mirror of mayatk's ``export_data_node``: refreshes every known
        producer's channel from live scene state first (Blender has no
        before-export event, so this task is the only refresh dispatch point —
        producers also publish at authoring time, but scene edits since then
        would otherwise ship a stale manifest), then folds the carrier in.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        self._refresh_scene_data_node()

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
            was_hidden = True
        if was_hidden:
            self.logger.info("data_export carrier was hidden — cleared for export.")

        # The object-level clears above can't help when the carrier's COLLECTION
        # is hidden or excluded from the view layer (hide_set even RAISES in the
        # excluded case) — the selection funnel would still drop it, or
        # select_set would kill the export outright. Link the carrier to the
        # scene root collection for the duration of the write and unlink it
        # right after (deferred restore: task reverts fire BEFORE the write).
        try:
            still_hidden = not carrier.visible_get()
        except RuntimeError:  # not in the active view layer at all
            still_hidden = True
        if still_hidden:
            root = self._scene().collection
            if carrier.name not in root.objects:
                root.objects.link(carrier)

                def _unlink_carrier(root=root, carrier=carrier):
                    try:
                        root.objects.unlink(carrier)
                    except RuntimeError:
                        pass

                self.stage_deferred_restore("data_export_root_link", _unlink_carrier)
                vl = CoreUtils._active_view_layer()
                if vl is not None:
                    vl.update()  # visible_get/select_set read the evaluated layer
                try:  # now reachable through the root — re-clear per-view-layer hiding
                    carrier.hide_set(False)
                except RuntimeError:
                    pass
                self.logger.info(
                    "data_export carrier's collection is hidden/excluded — linked "
                    "the carrier to the scene root collection for the write "
                    "(unlinked after)."
                )

        if carrier not in (self.objects or []):
            self.objects = list(self.objects or []) + [carrier]
            self.logger.info("data_export carrier added to the export set.")

        # Keyed-weight curve proxies: Blender's FBX exporter can't ship
        # custom-property animation, so EmissiveGroups stages one transient
        # Empty per keyed group whose scale.x carries the weight curve (the
        # Blender half of mayatk's _KNOWN_PRODUCERS export hook). They must
        # exist THROUGH the FBX write and vanish after, which the task-revert
        # engine can't express (reverts run before the write) — hence the
        # deferred restore.
        from blendertk.mat_utils.emissive_groups import EmissiveGroups

        proxies = EmissiveGroups.create_export_curve_proxies()
        if proxies:
            self.objects = list(self.objects or []) + proxies
            self.stage_deferred_restore(
                "emissive_curve_proxies",
                EmissiveGroups.remove_export_curve_proxies,
            )
            self._cover_frame_range(proxies)

        self._log_data_node_summary()

    def _refresh_scene_data_node(self):
        """Refresh ``data_export`` channels from the live metadata producers.

        Delegates to :meth:`FbxUtils.run_export_preparers` — the single
        producer registry, so a new metadata system ships without touching the
        exporter.  Each producer no-ops (or clears its channel) when it has
        nothing to write and is isolated so an absent or erroring subsystem
        never blocks the export.  Mirror of mayatk's
        ``_refresh_scene_data_node``.
        """
        try:
            from blendertk.env_utils.fbx_utils import FbxUtils

            FbxUtils.run_export_preparers()
        except Exception:
            self.logger.debug("data_export refresh skipped.", exc_info=True)

    def _set_frame_range(self, start, end) -> None:
        """Set the scene's frame range for the export, staging its restore.

        Deferred rather than ``revert_``-paired (the FBX writer reads the range
        *at* the write); ``stage_deferred_restore`` keys on ``frame_range`` so a
        later widen builds on the first caller's original.
        """
        scene = self._scene()
        original = (scene.frame_start, scene.frame_end)

        def restore():
            reverting = self._scene()
            reverting.frame_start, reverting.frame_end = original
            self.logger.debug(f"Reverted animation range to {original}.")

        self.stage_deferred_restore("frame_range", restore)
        scene.frame_start, scene.frame_end = int(start), int(end)

    def _cover_frame_range(self, objects) -> None:
        """Widen the scene's frame range so *objects*' keys all fall inside it.

        Blender bakes animation over the SCENE range, so a curve keyed outside
        it ships flattened to its extrapolated value.  The weight-curve proxies
        are staged in :meth:`export_data_node` -- after
        :meth:`set_bake_animation_range` has already computed its extent, and
        that task is a user-toggleable checkbox that may be off entirely -- so
        the range they need is claimed here rather than inferred there.  Only
        ever widens (never clips someone else's range), and rides the same
        staged restore.
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        rng = AnimUtils._key_range(AnimUtils.get_fcurves(objects))
        if rng is None:
            return
        scene = self._scene()
        start = min(scene.frame_start, math.floor(rng[0]))
        end = max(scene.frame_end, math.ceil(rng[1]))
        if (start, end) == (scene.frame_start, scene.frame_end):
            return
        self._set_frame_range(start, end)
        self.logger.info(
            f"Widened the export frame range to {int(start)}-{int(end)} so the "
            "staged keyed-weight curves bake in full."
        )

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

    def _warn_unseen_animation(self, check_name: str) -> None:
        """One WARNING per run when the export objects carry NLA-strip or data-level
        (object data / shape-key) animation the active-action anim checks cannot see.

        Those checks keep their pass/fail semantics on active object actions — NLA
        strip actions may be shared/library-linked and carry their own frame mapping,
        so extending the *edit* tasks to them is deliberately off the table — but the
        FBX write bakes the evaluated scene, so a silently-green check over animation
        it never validated is a lie. The flag is reset per run in
        :meth:`TaskManager._execute_tasks_and_checks`."""
        if getattr(self, "_unseen_anim_warned", False):
            return
        from blendertk.anim_utils._anim_utils import AnimUtils

        if AnimUtils.has_nla_or_data_animation(self.objects or []):
            self._unseen_anim_warned = True
            self.logger.warning(
                "NLA/data-level animation present — "
                f"{check_name} only validates active object actions."
            )

    def check_framerate(self, target_key) -> tuple:
        if not target_key:
            return True, []
        target = ptk.VidUtils.FRAME_RATES.get(target_key)
        if target is None:
            return True, []
        self._warn_unseen_animation("check_framerate")
        if not self._has_keyframes:
            return True, []
        scene = self._scene()
        actual = scene.render.fps / scene.render.fps_base
        if abs(actual - target) > 1e-3:
            return False, [
                f"Scene FPS ({actual:g}) does not match target ({target:g})."
            ]
        return True, []

    def check_referenced_objects(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import EnvUtils

        libs = EnvUtils.list_libraries()
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
        found = [
            o.name
            for o in self.objects
            if o.type == "MESH" and _LOD_SUFFIX_RE.search(o.name)
        ]
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
                groups[CoreUtils.strip_dup_suffix(o.name)].append(o.name)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if dupes:
            messages = [
                f"'{base}': {', '.join(names)}" for base, names in dupes.items()
            ]
            return False, ["Duplicate Empty base name(s) detected:"] + messages
        return True, []

    def check_root_default_transforms(self, enabled) -> tuple:
        """Root groups (an Empty with children) should sit at identity transform."""
        if not enabled or not self.objects:
            return True, []
        from blendertk.node_utils._node_utils import NodeUtils

        roots = set()
        for o in self.objects:
            chain = NodeUtils.get_parent(o, all=True)
            root = chain[-1] if chain else o
            if root.type == "EMPTY" and root.children:
                roots.add(root)

        bad = []
        for root in roots:
            loc = tuple(round(v, 5) for v in root.location)
            rot = tuple(round(v, 5) for v in root.rotation_euler)
            scale = tuple(round(v, 5) for v in root.scale)
            if (
                loc != (0.0, 0.0, 0.0)
                or rot != (0.0, 0.0, 0.0)
                or scale != (1.0, 1.0, 1.0)
            ):
                bad.append(root.name)
        if bad:
            return False, [
                f"Root group(s) with non-default transform: {', '.join(bad)}"
            ]
        return True, []

    def check_hidden_geometry(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        hidden = [
            o.name for o in self.objects if o.type == "MESH" and not o.visible_get()
        ]
        if hidden:
            shown = ", ".join(hidden[:10]) + (" …" if len(hidden) > 10 else "")
            # Opposite consequence to the Maya mirror: the export funnel is
            # selection-based (use_selection=True) and hidden objects can't be
            # selected, so hidden meshes are silently DROPPED from the FBX —
            # they do not ship hidden.
            return False, [
                f"{len(hidden)} hidden mesh object(s) would be silently OMITTED "
                f"from the export: {shown}"
            ]
        return True, []

    def check_overlapping_duplicate_mesh(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        from blendertk.edit_utils._edit_utils import EditUtils

        dupes = EditUtils.get_overlapping_duplicates(objects=self.objects)
        if dupes:
            shown = ", ".join(o.name for o in dupes[:10]) + (
                " …" if len(dupes) > 10 else ""
            )
            return False, [
                f"{len(dupes)} overlapping duplicate mesh object(s) found: {shown}"
            ]
        return True, []

    def check_objects_below_floor(self, enabled, tolerance: float = 0.5) -> tuple:
        """Blender is Z-up natively (Maya's version checks Y)."""
        if not enabled or not self.objects:
            return True, []
        from blendertk.xform_utils._xform_utils import XformUtils

        below = []
        for o in self.objects:
            if o.type != "MESH":
                continue
            mn, _mx = XformUtils.get_world_bbox(o)
            if mn.z < -tolerance:
                below.append(o.name)
        if below:
            shown = ", ".join(below[:10]) + (" …" if len(below) > 10 else "")
            return False, [f"{len(below)} object(s) dip below the floor (Z=0): {shown}"]
        return True, []

    def check_duplicate_materials(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.mat_utils._mat_utils import MatUtils

        groups = MatUtils.find_materials_with_duplicate_textures(
            materials=self._get_all_materials()
        )
        if groups:
            messages = [", ".join(m.name for m in g) for g in groups]
            return False, [
                f"{len(groups)} duplicate material group(s) found:"
            ] + messages
        return True, []

    def convert_textures(self, template) -> None:
        """Convert the export materials' textures to *template* (mirror of mayatk's).

        The task half of the Texture Template combobox (``cmb005``); ``b000``
        folds the one selection into this and
        :meth:`check_material_compatibility`, so there is a single definition
        to manage. Delegates to :meth:`blendertk.MatUpdater.update_materials`
        with the template as its workflow config -- the same shared pythontk
        factory the Maya twin runs; only the repath glue is Blender-idiomatic
        (image datablocks, and packed maps land on disk beside the sources
        rather than into the Principled graph, per MatUpdater's documented
        divergence).

        Deliberately not reverted: choosing a template means moving these
        materials to it, and the FBX embed must read the repathed images.

        Runs in TASK_ORDER's material-cleanup phase, after
        ``resolve_invalid_texture_paths`` and before
        ``convert_to_relative_paths``.
        """
        if not template:
            return None
        from blendertk.mat_utils._mat_utils import MatUpdater

        materials = self._get_all_materials()
        if not materials:
            self.logger.info("Texture template: no export materials to convert.")
            return None
        self.logger.info(
            f"Converting textures for {len(materials)} material(s) "
            f"to the {template!r} template..."
        )
        # Guarded because a task exception ABORTS the pipeline (TaskFactory
        # re-raises after logging) -- one unreadable texture would kill the
        # whole export with a traceback. The designed failure path is the
        # paired check instead: it validates the actual post-task state, so
        # masks this conversion could not bring to the template fail the
        # export cleanly, with the residuals named and this error above them.
        try:
            MatUpdater.update_materials(materials=materials, config=template)
        except Exception:  # noqa: BLE001 — the paired check is the gate
            self.logger.error(
                f"Texture conversion to {template!r} failed; "
                "check_material_compatibility will gate on what remains.",
                exc_info=True,
            )
        # The conversion repaths image nodes: the cached material/texture reads
        # are now stale, including the compatibility check's own.
        self._cached_materials = None
        return None

    def check_material_compatibility(self, template) -> tuple:
        """Every mask map matches the chosen texture template (mirror of mayatk's).

        The check half of the Texture Template combobox: armed only when a
        template is selected, alongside :meth:`convert_textures`. Checks run
        after the task phase, so this validates the **converted** state -- it
        fails only for a mask map the conversion could not bring to the
        template, naming the residuals rather than blocking the fix.

        The judgement is pythontk's (``MeshConvert.sidecar_foreign_packings``
        -> ``MapFactory.foreign_packings``), keyed by the registry workflow the
        combobox named, so no engine name or channel layout is spelled out here
        and this cannot drift from the Maya twin.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        from blendertk.env_utils.scene_state import SceneState

        try:
            sections = SceneState.read(self.objects or [])
        except Exception:  # noqa: BLE001 — a read failure must not block an export
            self.logger.warning("Material compatibility check skipped.", exc_info=True)
            return True, []

        foreign = ptk.MeshConvert.sidecar_foreign_packings(
            {"sections": sections}, workflow=template
        )
        if not foreign:
            return True, []

        # Count header then indented offenders, as check_path_length does.
        messages = [
            f"{len(foreign)} mask map(s) do not match the {template!r} template "
            "after conversion:"
        ]
        messages.extend(
            f"  - {map_type}: {os.path.basename(path)}"
            for path, map_type in sorted(foreign.items())
        )
        return False, messages + [
            "See the Map Updater log above for why these did not convert, or "
            "set Textures back to 'As Authored' to ship them as they are."
        ]

    def check_path_length(self, max_length) -> tuple:
        """No export path exceeds the OS path-length limit (mirror of mayatk's).

        Covers the export destination and every export texture, measured in
        ABSOLUTE form — that is the string the filesystem, the FBX exporter and
        the receiving pipeline all see, and a ``//``-relative path can be short
        while resolving to a very long one. PACKED images are exempt: they ship
        embedded from memory, so their (often stale) bookkeeping path never
        travels.

        ``max_length`` may be the spin box's value or any numeric-ish string;
        ``0`` (the spin box's "OFF" position) or ``"OFF"`` disables the check,
        and a non-numeric value logs a warning and skips.
        """
        if max_length is not None:
            if not max_length or str(max_length).upper() == "OFF":
                return True, []
            try:
                limit = int(max_length)
            except (TypeError, ValueError):
                self.logger.warning(
                    f"Invalid max path length '{max_length}'. Skipping length check."
                )
                return True, []
        else:
            limit = ptk.FileUtils.path_length_limit()

        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        offenders = []

        export_path = getattr(self, "export_path", None)
        if export_path:
            # ``_abspath`` already resolves an image's ``//`` path against its own
            # .blend; only the export path needs expanding here.
            resolved = os.path.abspath(os.path.expandvars(export_path))
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                offenders.append(f"export path ({len(resolved)} chars)")

        seen = set()
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory; its stored path never travels
            path = _MatUtilsInternal._abspath(img)
            if not path or path in seen:
                continue
            seen.add(path)
            if ptk.FileUtils.exceeds_path_length(path, limit):
                offenders.append(f"{img.name} ({len(path)} chars)")

        if offenders:
            shown = ", ".join(offenders[:10]) + (" …" if len(offenders) > 10 else "")
            return False, [
                f"{len(offenders)} path(s) exceed the {limit}-character limit: {shown}"
            ]
        return True, []

    def check_valid_paths(self, enabled) -> tuple:
        """Every export texture and every linked library resolves on disk.

        Image scope is ``_get_export_images`` — the datablocks feeding the
        materials assigned to ``self.objects`` — not every FILE image in the
        .blend (mirrors mayatk's ``check_valid_paths``).  Whole-file scope
        flagged maps that never ship: the World/Environment-Texture HDR (never
        part of the object export set at all) and the zero-user images left
        behind after a duplicate-material cleanup.  Linked libraries stay
        whole-file — they are the analogue of Maya's scene references, not of a
        texture.

        Storage-aware (mirrors mayatk's ``resolve_path`` semantics):

        * PACKED images are treated as valid — the FBX embeds them from memory,
          so a stale disk path is irrelevant (it used to fail the export over a
          file that never ships).
        * TILED (UDIM) images never appear in ``get_image_records`` (FILE-only),
          so a deleted tile set used to pass unseen.  They are validated here by
          probing the first declared tile on disk (``<UDIM>`` collapsed via
          ``tiles[0].number``, 1001 fallback — the same probe-tile collapse
          mayatk applies).
        """
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import EnvUtils
        from blendertk.mat_utils._mat_utils import MatUtils, _MatUtilsInternal

        # Filter get_image_records() by the export set rather than re-deriving
        # "is a FILE image whose abspath exists" — that predicate belongs to
        # get_image_records, and a second copy of it here would be free to drift.
        records = {r["image"]: r for r in MatUtils.get_image_records()}
        missing = []
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory regardless of the stored path
            if getattr(img, "source", "") == "TILED":
                probe = _MatUtilsInternal._udim_first_tile_path(img)
                if not (probe and os.path.isfile(probe)):
                    missing.append(img.name)
            elif img in records and not records[img]["exists"]:
                missing.append(records[img]["name"])
        missing += [r["name"] for r in EnvUtils.list_libraries() if not r["exists"]]
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            return False, [f"{len(missing)} missing file(s): {shown}"]
        return True, []

    def check_texture_file_size(self, max_mb) -> tuple:
        """No export texture exceeds ``max_mb`` on disk.

        Iterates the export image *datablocks* (not bare paths) so storage is
        respected:

        * PACKED images are skipped — they ship embedded from memory, so the
          on-disk copy (if any) is not what exports.
        * TILED (UDIM) images are size-probed at their **largest existing
          tile** (the ``<UDIM>`` token globbed on disk).  Previously
          ``os.path.getsize`` on the raw token path raised ``OSError`` into a
          silent ``continue``, letting entire multi-GB tile sets through the
          gate unmeasured (mayatk collapses the token to a probe tile the same
          way).

        ``max_mb`` may be the spin box's value or any numeric-ish string (e.g.
        ``"16"``); ``None``, ``0`` (the spin box's "OFF" position), ``""``, or
        ``"OFF"`` disables the check, and a non-numeric value logs a warning
        and skips.
        """
        if not max_mb or str(max_mb).upper() == "OFF":
            return True, []
        try:
            max_mb = float(max_mb)
        except (TypeError, ValueError):
            self.logger.warning(
                f"Invalid max texture size '{max_mb}'. Skipping size check."
            )
            return True, []
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        oversized = []
        seen = set()
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory; the disk copy is not what exports
            if getattr(img, "source", "") == "TILED":
                sizes = []
                for t in _MatUtilsInternal._udim_tile_paths(img):
                    try:
                        sizes.append((os.path.getsize(t), t))
                    except OSError:
                        continue
                if not sizes:
                    continue  # no tiles on disk — check_valid_paths' domain
                size, p = max(sizes)
            else:
                p = _MatUtilsInternal._abspath(img)
                if not p:
                    continue
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue  # missing file — check_valid_paths' domain
            if p in seen:
                continue
            seen.add(p)
            size_mb = size / (1024 * 1024)
            if size_mb > max_mb:
                oversized.append(f"{os.path.basename(p)} ({size_mb:.1f} MB)")
        if oversized:
            shown = ", ".join(oversized[:10]) + (" …" if len(oversized) > 10 else "")
            return False, [
                f"{len(oversized)} texture(s) exceed {max_mb:g} MB: {shown}"
            ]
        return True, []

    def check_untied_keyframes(self, enabled) -> tuple:
        """Verify every animated channel has a bookend key at its object's own keyed extent
        (the inverse of what ``tie_all_keyframes`` fixes)."""
        if not enabled:
            return True, []
        self._warn_unseen_animation("check_untied_keyframes")
        if not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import AnimUtils

        untied = []
        for o in self.objects:
            bounds = [
                (fc, fc.keyframe_points[0].co.x, fc.keyframe_points[-1].co.x)
                for fc in AnimUtils.get_fcurves([o])
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
        if not enabled:
            return True, []
        self._warn_unseen_animation("check_floating_point_keys")
        if not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import AnimUtils

        offenders = []
        for o in self.objects:
            for fc in AnimUtils.get_fcurves([o]):
                for k in fc.keyframe_points:
                    if abs(k.co.x - round(k.co.x)) > 1e-4:
                        offenders.append(
                            f"{o.name}.{fc.data_path}[{fc.array_index}] (frame {k.co.x:.3f})"
                        )
                        break

        if offenders:
            shown = ", ".join(offenders[:10]) + (" …" if len(offenders) > 10 else "")
            return False, [
                f"{len(offenders)} curve(s) have floating point keys: {shown}"
            ]
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
        # Texture-template conversion needs resolved sources, and its repathed
        # images still need to go //-relative for the FBX (mirrors mayatk).
        "convert_textures",
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

    def __init__(self, logger):
        super().__init__(logger)

        self.logger = logger
        self._objects = None
        self._cached_materials = None
        self._unseen_anim_warned = False

    def _execute_tasks_and_checks(self, tasks_only, checks_only):
        # smart_bake's completion log mentions the follow-on optimize_keys
        # task; the generic TaskFactory knows nothing about either, so the
        # flag is set here, in the consumer that reads it.
        self._optimize_keys_enabled = bool(tasks_only.get("optimize_keys", False))
        # The NLA/data-level-animation advisory fires once per run, not once
        # per check (see _warn_unseen_animation).
        self._unseen_anim_warned = False
        return super()._execute_tasks_and_checks(tasks_only, checks_only)

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
        """Return the task definitions for the UI.

        Tooltips are built with uitk's rich-text DSL (imported lazily so this
        engine module still imports Qt-free under ``--background``).  Keep the
        ``TooltipFormat.fmt`` call form and literal arguments — that is what
        ``m3trik/scripts/check_tooltips.py`` statically renders and validates.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "sep_general": {"widget_type": "Separator", "title": "General"},
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "set_row_label": "Scope",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scope",
                    body="Which objects the export set is built from, resolved "
                    "fresh each time you export.",
                    bullets=[
                        "<b>All Scene Objects</b> — every mesh object in the "
                        "scene, visible or not.",
                        "<b>All Visible Objects</b> — visible geometry only.",
                        "<b>Selected Objects Only</b> — the current selection, "
                        "minus the data_internal Empty (a plain Select All would "
                        "otherwise sweep the bake-session manifest in).",
                    ],
                    notes=[
                        "The data_export metadata carrier is an Empty, not a mesh, "
                        "so <b>Export Scene Data Node</b> is what puts it in the "
                        "set."
                    ],
                ),
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "set_row_label": "Units",
                "setToolTip": TooltipFormat.fmt(
                    title="Linear Unit",
                    body="Unit system and scale length the scene is switched to "
                    "for the FBX write, then switched back.",
                    notes=[
                        "Blender has no named-unit enum, so each option sets the "
                        "(system, scale_length) pair that makes one scene unit "
                        "equal one of the named unit — the scale the receiving "
                        "engine reads.",
                        "<b>OFF</b> writes with the scene's current unit settings.",
                    ],
                ),
                "add": self._scene_unit_options,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "setText": "Exclude HDR Environment",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude HDR Environment",
                    body="No-op in Blender — kept so the panel matches Maya's.",
                    notes=[
                        "The World shader is never part of the object export set, "
                        "unlike Maya's aiSkyDomeLight, which does ride into its "
                        "All Scene Objects mode."
                    ],
                ),
                "setChecked": True,
            },
            "sep_materials": {"widget_type": "Separator", "title": "Materials"},
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Reassign Duplicate Materials",
                    body="Collapse materials that are genuinely identical onto a "
                    "single keeper and reassign every object using them.",
                    notes=[
                        "Reports the same materials as <b>Check For Duplicate "
                        "Materials</b>.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "setText": "Convert To Relative Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Convert To Relative Paths",
                    body="Rewrite the export materials' texture paths in "
                    "Blender's //-relative project form.",
                    notes=[
                        "External textures are copied into the project's textures "
                        "folder first (if not already there), otherwise the "
                        "relative paths would point at files that aren't there.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Resolve Invalid Texture Paths",
                    body="Rebind broken texture paths by hunting for the missing "
                    "file under the .blend's own directory.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "sep_anim": {"widget_type": "Separator", "title": "Animation"},
            "smart_bake": {
                "widget_type": "QCheckBox",
                "setText": "Smart Bake",
                "setToolTip": TooltipFormat.fmt(
                    title="Smart Bake",
                    body="Bake the rig's indirect animation — constraints "
                    "(including IK), drivers and expressions, driven blend-shape "
                    "weights — down to plain keyframes, which is all an FBX can "
                    "carry.",
                    notes=[
                        "The time range is detected from the driving animation "
                        "itself.",
                        "Bakes into a fresh Action while muting the identified "
                        "sources; the pre-bake state is restorable afterward via "
                        "SmartBake.restore.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "setText": "Optimize Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Keys",
                    body="Delete static curves and redundant flat keys from the "
                    "exported objects, including the curves Smart Bake just "
                    "created.",
                    notes=[
                        "Boundary keys are always kept.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Tie All Keyframes",
                    body="Insert bookend keys at the union keyed extent of the "
                    "whole export set, so every animated channel has a key at "
                    "both range boundaries.",
                    notes=[
                        "Fixes what <b>Check For Untied Keyframes</b> reports.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "setText": "Snap Keys To Frame",
                "setToolTip": TooltipFormat.fmt(
                    title="Snap Keys To Frame",
                    body="Round every key on the exported objects to the nearest "
                    "whole frame.",
                    notes=[
                        "Fixes what <b>Check For Floating Point Keys</b> reports — "
                        "fractional key times left behind by retiming, scaling, or "
                        "an import at a different rate.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Auto Set Bake Animation Range",
                    body="Set the scene's frame range to the first and last "
                    "keyframe of the exported objects for the duration of the "
                    "export.",
                    notes=[
                        "Runs after Smart Bake, Optimize, Snap and Tie, so it "
                        "measures the final keyframe extent.",
                        "The original frame range is restored after the write.",
                    ],
                ),
                "setChecked": True,
            },
            "apply_declared_takes": {
                "widget_type": "QCheckBox",
                "setText": "Export Shots as Animation Takes",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Shots as Animation Takes",
                    body="Would split the exported animation into one named take "
                    "per shot, so the file arrives in an engine as separate "
                    "AnimationClips instead of a single continuous clip.",
                    notes=[
                        _NEEDS_SHOTS,
                        "Shipping the shot metadata is a separate switch — see "
                        "<b>Export Scene Data Node</b>.",
                    ],
                ),
                "setChecked": False,
                "setEnabled": False,
            },
            "sep_hierarchy": {"widget_type": "Separator", "title": "Hierarchy"},
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "set_row_label": "Ignore",
                "setPlaceholderText": "Group names to ignore (comma-separated)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated names of top-level objects to drop from "
                    "the export set (case-insensitive).",
                    notes=[
                        "Example: temp, proxy",
                        "Leave empty to skip.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "setText": "Export Scene Data Node",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scene Data Node",
                    body="Ship the shared <b>data_export</b> carrier in the export "
                    "so the metadata stamped on it (the Lightmap Baker's "
                    "lightmap_metadata, and any other producer's channel) rides "
                    "into the FBX as user properties.",
                    notes=[
                        "Every export scope is geometry-driven, so the carrier "
                        "would otherwise be dropped.",
                        "No-op when the scene has no carrier.",
                        "A readable copy is also written beside the export as "
                        ".scene_data.json.",
                        "This ships the metadata only — it never changes the "
                        "animation.",
                    ],
                ),
                "setChecked": True,
            },
            "sep_output": {"widget_type": "Separator", "title": "Output"},
            "version": {
                "widget_type": "QLineEdit",
                "set_row_label": "Version",
                "setPlaceholderText": "{stem}_v{n:03d}  — empty disables",
                "setToolTip": TooltipFormat.fmt(
                    title="Version",
                    body="Filename pattern for the exported file. Leave empty to "
                    "export without versioning.",
                    rows=[
                        ("{stem}", "output basename"),
                        ("{n:NNd}", "version number, zero-padded to NN digits"),
                        ("{date}", "YYYY-MM-DD"),
                        (
                            "{user}",
                            "OS username — embeds dev identity, so beware on "
                            "shared exports",
                        ),
                        ("{scene}", ".blend basename (requires a saved file)"),
                    ],
                    notes=[
                        "The extension is added automatically — do not include "
                        "{ext}."
                    ],
                ),
                "setText": "",
                "value_method": "text",
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI.

        A failed check aborts the export, so each tooltip below leads with what
        makes it fail.  Tooltip authoring rules: see :attr:`task_definitions`.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "sep_general": {"widget_type": "Separator", "title": "General"},
            "check_framerate": {
                "widget_type": "ComboBox",
                "set_row_label": "Framerate",
                "setToolTip": TooltipFormat.fmt(
                    title="Scene Framerate",
                    body="Fails the export when the scene's FPS is not the "
                    "framerate selected here.",
                    notes=[
                        "Skipped when the scene has no keyframes.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._frame_rate_options,
            },
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "setText": "Check For Referenced Objects",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Referenced Objects",
                    body="Fails the export when the scene contains linked "
                    "libraries — Blender's analogue of Maya file references.",
                    notes=["Make the data local to pass."],
                ),
                "setChecked": True,
            },
            "sep_hierarchy": {
                "widget_type": "Separator",
                "title": "Hierarchy & Naming",
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Geometry LOD Suffix (_LODx)",
                    body="Lists geometry named with an LOD suffix — '_LOD' alone "
                    "or followed by digits ('_LOD1', '_LOD02'), case-insensitive.",
                    notes=[
                        "Informational only: it reports what it finds and never "
                        "fails the export."
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_locator_names": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Locator Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Locator Names",
                    body="Fails the export when two Empties share a base name.",
                    notes=[
                        "Blender's auto '.001' suffix is stripped before "
                        "comparing, so 'pivot' and 'pivot.001' collide — which is "
                        "what a consumer matching them by name downstream will "
                        "see."
                    ],
                ),
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "setText": "Check Root Default Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Root Default Transforms",
                    body="Fails the export when a root group Empty is not at "
                    "identity — location and rotation (0, 0, 0), scale (1, 1, 1).",
                ),
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Hierarchy vs Existing FBX",
                    body="Would fail the export when the hierarchy differs from "
                    "the previous export — nodes that went missing or appeared, "
                    "the signature of an accidental change.",
                    notes=[_NEEDS_HIERARCHY_MANAGER],
                ),
                "setChecked": False,
                "setEnabled": False,
            },
            "sep_geometry": {"widget_type": "Separator", "title": "Geometry"},
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "setText": "Check For Hidden Geometry",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Hidden Geometry",
                    body="Fails the export when geometry in the set is hidden.",
                    notes=[
                        "The export is selection-based, so hidden meshes are "
                        "silently dropped from the FBX — this check catches that "
                        "content loss before the write. (Maya's exporter has the "
                        "opposite problem: there hidden geometry ships anyway.)"
                    ],
                ),
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Overlapping Duplicates",
                    body="Fails the export when two meshes occupy the same space — "
                    "typically a duplicate left sitting on top of the original.",
                ),
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "setText": "Check For Objects Below Floor",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Objects Below Floor",
                    body="Fails the export when geometry dips below Z=0 (Blender "
                    "is Z-up).",
                    notes=[
                        "A 0.5 unit tolerance means shallow penetrations do not "
                        "fail on their own."
                    ],
                ),
                "setChecked": True,
            },
            "sep_materials": {"widget_type": "Separator", "title": "Materials"},
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Materials",
                    body="Fails the export when two of the export materials are "
                    "duplicates of each other.",
                    notes=[
                        "The <b>Reassign Duplicate Materials</b> task merges "
                        "exactly what this reports."
                    ],
                ),
                "setChecked": True,
            },
            "check_path_length": {
                # Mirrors mayatk: a bounded character budget is a spin box, the
                # default is THIS machine's OS limit, and 0 reads back as "OFF".
                "widget_type": "SpinBox",
                "set_row_label": "Max Path Length",
                "set_limits": [0, 32767, 1, 0],
                "setValue": ptk.FileUtils.path_length_limit(),
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Path Length",
                    body="Fails the export when the destination, or any texture "
                    "feeding the export materials, resolves to a path longer than "
                    "this many characters.",
                    notes=[
                        "Over-long paths fail late and opaquely, and a path that "
                        "fits on this machine can still break on one without long "
                        "paths enabled (260 characters).",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Valid Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Valid Paths",
                    body="Fails the export when a texture feeding the export "
                    "materials — or a linked library — does not resolve on disk.",
                    notes=[
                        "Images that will not ship (the World/HDR environment "
                        "texture, images left orphaned by a duplicate-material "
                        "cleanup) are not reported."
                    ],
                ),
                "setChecked": True,
            },
            "check_texture_file_size": {
                # Mirrors mayatk: a bounded MB budget is a spin box, and 0 reads
                # back as "OFF" (the check treats a falsy limit as disabled).
                "widget_type": "SpinBox",
                "set_row_label": "Max Size (MB)",
                "set_limits": [0, 4096, 1, 0],
                "setValue": 16,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture Size (MB)",
                    body="Fails the export when any texture feeding the export "
                    "materials is larger than this on disk.",
                    notes=[
                        "Catches un-downsized authoring maps — an 8K master left "
                        "wired up — that would bloat the shipped asset.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "sep_anim": {"widget_type": "Separator", "title": "Animation"},
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Check For Untied Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Untied Keyframes",
                    body="Fails the export when an animated channel has no bookend "
                    "key at its object's own keyed extent.",
                    notes=[
                        "The <b>Tie All Keyframes</b> task inserts the missing "
                        "bookend keys."
                    ],
                ),
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "setText": "Check For Floating Point Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Floating Point Keys",
                    body="Fails the export when a key sits on a fractional frame.",
                    notes=[
                        "The <b>Snap Keys To Frame</b> task rounds them to whole "
                        "frames."
                    ],
                ),
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
