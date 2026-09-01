# !/usr/bin/python
# coding=utf-8
"""Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
identically-named module. :class:`TaskManager` supplies the methods
:class:`pythontk.core_utils.task_factory.TaskFactory` discovers by name
(``getattr(self, task_name)`` reflection) -- see that module for the generic dispatch/revert
engine (the pythontk single source of truth, 100% DCC-agnostic).

~27 of mayatk's ~28 tasks/checks are ported here as real Blender implementations (the smart_bake
group uses :mod:`blendertk.anim_utils.smart_bake`; ``export_data_node`` rides the ported
:class:`blendertk.node_utils.data_nodes.DataNodes` carrier; ``apply_declared_takes`` arms
``FbxUtils``' post-write AnimStack splitting from the Shots-published ``fbx_takes`` channel).
The remaining one depends on an integration blendertk doesn't have yet (the exporter-side
hierarchy diff *check* — the scene-data sidecar itself IS written, by the engine
(``_write_scene_data_sidecar``)) and is declared in :attr:`TaskManager.check_definitions` as a
DISABLED placeholder (the widget shows in the panel, 1:1 with mayatk's label/position, greyed
out with a tooltip explaining the gap) -- ``TODO(blender-parity)``. No method is defined for a
disabled placeholder: :class:`TaskFactory` gracefully skips a missing method (logs + no-ops),
and a disabled widget can never be toggled to invoke it anyway.
"""

import contextlib
import logging
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

    #: Tiled-texture filename tokens (single-file operations must skip these).
    _TEXTURE_TOKEN_RE = re.compile(r"<udim>|<f>|<uvtile>", re.IGNORECASE)

    def _scene_safe_output_type(self, path, template):
        """The container the optimization pass may write for *path* under
        *template* — clamped to what a scene image can read (mirror of
        mayatk's).

        A template's per-map-type :class:`~pythontk.OutputSpec` can name a
        delivery-only container (:attr:`~pythontk.ImgUtils.DELIVERY_ONLY_FORMATS`
        — KTX2, WebP) that no FBX importer reads — those stay with the GLB
        carrier pass. Returns the source's own extension to pin the container in
        that case, None otherwise (an explicit ``output_type`` outranks the
        profile's, so None lets the profile drive).
        """
        map_type = ptk.MapFactory.resolve_map_type(path, key=True)
        spec_ext = (
            (ptk.OutputTemplates.resolve(map_type, template).ext or "")
            .lower()
            .lstrip(".")
        )
        if spec_ext in ptk.ImgUtils.DELIVERY_ONLY_FORMATS:
            return self._source_container(path)
        return None

    @staticmethod
    def _source_container(path):
        """*path*'s own container — what "keep what the scene can already read"
        resolves to, for both the template and the Texture File Type dial."""
        return os.path.splitext(path)[1].lower().lstrip(".") or None

    def _resolved_output_type(self, path, template):
        """The container the optimization pass writes for *path* (mirror of
        mayatk's).

        Binds the per-run ``_texture_file_type`` mode (the Texture File Type
        combo, stamped by ``perform_export`` — never a dispatched task) to the
        shared rule: :meth:`pythontk.OutputTemplates.resolve_selection` owns
        "a concrete container outranks the profile's template", so naming a
        file type here writes every map as that, while the template still
        supplies the budget and bit depth. Falsy (Original) defers to
        :meth:`_scene_safe_output_type`.
        """
        _, chosen = ptk.OutputTemplates.resolve_selection(
            template, getattr(self, "_texture_file_type", None)
        )
        if chosen:
            # A delivery-only container (KTX2, WebP) gets the same clamp a
            # template's would: no scene image or FBX importer reads it, so the
            # scene's own maps keep their container and that choice lands on the
            # GLB carrier instead (:meth:`_glb_texture_params`). WebP is clamped
            # here even though BLENDER itself reads it (4.x+): the constraint is
            # the FBX's consumers, not the authoring app -- a Maya `file` node
            # reports a .webp as 0x0 (measured 2026-08-25) and a shipped
            # webp-textured FBX binds nothing anywhere. Said once per run.
            if chosen in ptk.ImgUtils.DELIVERY_ONLY_FORMATS:
                if not getattr(self, "_delivery_only_clamp_said", False):
                    self._delivery_only_clamp_said = True
                    self.logger.info(
                        f"{chosen.upper()} is a delivery-only container: no DCC "
                        f"texture node or FBX importer reads it, so the scene's "
                        f"own maps keep their container"
                        + (
                            " (the GLB still carries it)."
                            if chosen in self.GLB_CARRIER_FORMATS
                            else "."
                        )
                    )
                return self._source_container(path)
            return chosen
        return self._scene_safe_output_type(path, template) if template else None

    #: Containers a GLB can embed: glTF-core (``MeshConvert.IMAGE_MIME_TYPES``,
    #: the SSoT for what needs no extension) plus the two ``optimize_glb_textures``
    #: declares an extension for — WebP (``EXT_texture_webp``) and KTX2
    #: (``KHR_texture_basisu``). Everything else the Texture File Type dial offers
    #: is a scene-side container only, so the GLB falls back to PNG.
    GLB_CARRIER_FORMATS = frozenset(
        [e.lstrip(".") for e in ptk.MeshConvert.IMAGE_MIME_TYPES] + ["webp", "ktx2"]
    )

    def _glb_texture_params(self):
        """``optimize_glb_textures`` kwargs for this run's GLB deliverable.

        The GLB's half of the panel's two GENERAL texture dials — it has no
        dials of its own — resolved against
        :meth:`pythontk.MeshConvert.web_delivery_texture_params`, the ONE
        definition of what a web deliverable's textures are. Each dial
        *overrides* that policy; neither has to restate it (mirror of mayatk's,
        whose docstring carries the measurement that set the default):

        * **Container** — Texture File Type, when it names something
          :attr:`GLB_CARRIER_FORMATS` covers; anything else (and "Original")
          takes the policy's container, because a GLB from this panel IS the
          web deliverable — the FBX and USD formats beside it are the
          interchange ones.
        * **Resolution** — the Optimize Textures combo's ceiling half, through the same
          :meth:`_texture_size_clamp` every scene map goes through, so the export
          has ONE size policy rather than a second hiding in the GLB. A dial
          that names no ceiling takes the policy's.

        **Behaviour change (2026-08-29)**: untouched dials used to mean no pass
        at all. Measured on a production assembly through every leg, that
        shipped 280.13 MB where the WebXR preview published 8.71 MB of the same
        scene. ``MeshConvert.fbx_to_glb`` alone still runs no pass, for a
        programmatic caller that wants the bytes untouched.
        """
        file_type = (
            (getattr(self, "_texture_file_type", None) or "").lower().lstrip(".")
        )
        optimize = bool(getattr(self, "_optimize_textures_enabled", False))

        carrier = file_type if file_type in self.GLB_CARRIER_FORMATS else ""
        if file_type and not carrier:
            self.logger.info(
                f"GLB textures: {file_type.upper()} is not a container glTF can "
                f"embed — the GLB carries "
                f"{ptk.MeshConvert.WEB_DELIVERY_FORMAT} (the scene's own maps "
                f"still use {file_type.upper()})."
            )

        # ``or None`` on both halves: an unset dial is "unspecified", which the
        # shared resolver answers with the policy, NOT a falsy value it would
        # read as a decision (0 there means "keep every pixel").
        return ptk.MeshConvert.web_delivery_texture_params(
            image_format=self._glb_format_id(carrier) if carrier else None,
            max_size=(self._glb_max_size() if optimize else 0) or None,
        )

    @staticmethod
    def _glb_format_id(ext):
        """*ext* as the format id ``optimize_glb_textures`` needs.

        It passes ``image_format`` straight to Pillow AND builds the glTF mime
        as ``image/<lowercased>``, so the container's file extension is not
        always the right token: ``jpg`` is a legal choice on this dial (and a
        legal filename suffix), but Pillow only knows ``JPEG`` and glTF only
        accepts ``image/jpeg`` — ``JPG`` would raise ``KeyError`` mid-encode
        and, if it hadn't, write an invalid glTF. Canonicalized through
        ``MeshConvert.IMAGE_MIME_TYPES`` rather than a private alias table, so
        the mapping stays the one glTF itself is keyed on.
        """
        mime = ptk.MeshConvert.IMAGE_MIME_TYPES.get(f".{ext}", "")
        return (mime.split("/")[-1] or ext).upper()

    def _glb_max_size(self):
        """The size-ceiling half of Optimize Textures, as pixels for the GLB pass.

        ``optimize_glb_textures`` takes pixels, while :meth:`_texture_size_clamp`
        speaks the optimizer's richer rule (a ceiling OR the template's budget),
        so the sentinel is resolved to the template's own ``max_size`` here.
        """
        template = getattr(self, "_texture_template", None)
        clamp = self._texture_size_clamp(template)
        if clamp.get("enforce_budget"):
            return int(ptk.OutputTemplates.budget(template).max_size or 0)
        return int(clamp.get("max_size") or 0)

    #: ``_texture_max_size`` sentinel: clamp to the active template's own
    #: :class:`~pythontk.DeliveryBudget` (``enforce_budget``) rather than to a
    #: pixel ceiling. Aliases the shared resolver's own sentinel so the combo
    #: row, the exporter and the optimizer cannot drift apart on its value —
    #: and so this cannot drift from mayatk's twin either.
    TEXTURE_MAX_SIZE_TEMPLATE = ptk.MapOptimizer.SIZE_CLAMP_TEMPLATE

    def _texture_size_clamp(self, template) -> Dict[str, Any]:
        """The resize rule the optimization pass applies under *template*
        (mirror of mayatk's).

        Binds the per-run ``_texture_max_size`` mode (the Optimize Textures
        combo's size half, stamped by ``perform_export`` — never a dispatched
        task) to the shared resolver, which owns the rule: see
        :meth:`pythontk.MapOptimizer.resolve_size_clamp` for the modes and
        why the budget's POT flag is deliberately not adopted.

        Returns:
            dict of keyword arguments for ``MapOptimizer.assess`` /
            ``optimize_map``. Empty when no clamp applies.
        """
        return ptk.MapOptimizer.resolve_size_clamp(
            getattr(self, "_texture_max_size", None), template, logger=self.logger
        )

    def _texture_size_clamp_desc(self, template) -> str:
        """Human-readable form of :meth:`_texture_size_clamp` for log lines."""
        return ptk.MapOptimizer.describe_size_clamp(
            getattr(self, "_texture_max_size", None), template, logger=self.logger
        )

    def _assess_optimization(self, path, template):
        """What the optimization pass would do to *path* (mirror of mayatk's).

        The one criterion the task (skip already-optimal sources, re-verify a
        reused staged file) and the check (name residuals) share, via
        ``ptk.MapOptimizer.assess``: the per-map-type pass (mode / bit depth),
        plus the *template*'s per-map-type container when one is active, plus
        the size ceiling when one is set
        (:meth:`_texture_size_clamp`). Without a clamp the template's
        :class:`~pythontk.DeliveryBudget` stays ADVISORY — assess reports it in
        ``warnings`` and nothing here plans a resample.

        Returns:
            None when the file cannot be read (missing / unreadable is
            :meth:`check_valid_paths`' domain); else a dict with ``needed``
            (bool), ``reasons`` (list[str], including a container change the
            plan itself does not model), and ``warnings`` (list[str]).
        """
        output_type = self._resolved_output_type(path, template)
        result = ptk.MapOptimizer.assess(
            path,
            output_profile=template,
            output_type=output_type,
            optimize_bit_depth=True,
            **self._texture_size_clamp(template),
        )
        if result.get("error"):
            return None
        reasons = list(result["reasons"])
        src_ext = os.path.splitext(path)[1].lower().lstrip(".")
        new_ext = (result["predicted"].get("ext") or src_ext).lower().lstrip(".")
        if new_ext != src_ext:
            reasons.append(f"Container: {src_ext} -> {new_ext} (template)")
        return {
            "needed": bool(reasons),
            "reasons": reasons,
            "warnings": list(result["warnings"]),
            "output_type": output_type,
        }

    def _tiled_representative(self, resolved: str):
        """One concrete file standing in for a tiled/sequence texture *resolved*
        path (mirror of mayatk's).

        ``<udim>`` resolves to its first tile, ``1001``; ``<uvtile>`` resolves
        to ITS OWN first tile, ``u1_v1`` — UDIM and UV-tile numbering are not
        interchangeable, so collapsing both onto ``"1001"`` silently pointed a
        Blender-authored ``TILED``/``<uvtile>`` set at a file that was never
        written (the representative never existed, so the caller's
        ``os.path.isfile`` gate always failed it). ``<f>`` has no fixed
        "first" value — frame numbering, padding, and start frame all vary
        per render — so it globs the token's position for the first frame
        file that actually exists on disk.

        Returns:
            str | None: The representative path (for ``<udim>``/``<uvtile>``
            it may not exist — the caller's own ``os.path.isfile`` check is
            what gates that), or ``None`` when a ``<f>`` token's glob finds no
            frame file (distinct from the fixed-token miss).
        """
        basename = os.path.basename(resolved)
        directory = os.path.dirname(resolved)

        def _fixed(match):
            return "1001" if match.group(0).lower() == "<udim>" else "u1_v1"

        if "<f>" in basename.lower():
            import glob as _glob

            pattern = self._TEXTURE_TOKEN_RE.sub(
                lambda m: "*" if m.group(0).lower() == "<f>" else _fixed(m),
                basename,
            )
            matches = sorted(_glob.glob(os.path.join(directory, pattern)))
            return matches[0] if matches else None

        return os.path.join(directory, self._TEXTURE_TOKEN_RE.sub(_fixed, basename))

    def _export_texture_sources(
        self, include_tiled: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """Deduplicated shipping textures: ``{key: {"path", "images", "tiled"}}``.

        The Blender counterpart of mayatk's file-node walk: image datablocks
        feeding the export materials, read from their CURRENT stored paths so
        post-task callers (checks) see what a prior task staged, deduped by
        normcased absolute path.

        Skips — each logged so the skip is auditable — packed images (they
        ship embedded from memory; there is no on-disk file to optimize),
        library-linked images (their datablocks are read-only, so a repath
        cannot be applied), and paths that do not resolve to a file (missing
        files are :meth:`check_valid_paths`' domain). Tiled/UDIM images are
        skipped by default (the optimizer is single-file); with
        ``include_tiled=True`` they are included, resolved to a single
        representative tile/frame (see :meth:`_tiled_representative`) — the
        budget check wants to MEASURE a tiled set it cannot fix, so an
        oversized one fails aloud instead of slipping past the gate.
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        sources: Dict[str, Dict[str, Any]] = {}
        skipped: Dict[str, List[str]] = {}
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                skipped.setdefault("packed", []).append(img.name)
                continue
            if getattr(img, "library", None):
                skipped.setdefault("library-linked", []).append(img.name)
                continue
            tiled = getattr(img, "source", "") == "TILED" or bool(
                self._TEXTURE_TOKEN_RE.search(os.path.basename(img.filepath or ""))
            )
            if tiled and not include_tiled:
                skipped.setdefault("tiled (<UDIM>)", []).append(img.name)
                continue
            resolved = _MatUtilsInternal._abspath(img)
            if tiled and resolved:
                # <udim>/<uvtile> resolve to their own first tile, <f> globs
                # for the first frame actually on disk (mirror of mayatk's).
                representative = self._tiled_representative(resolved)
                if representative is None:
                    skipped.setdefault("<f> frame not found on disk", []).append(
                        img.name
                    )
                    continue
                resolved = representative
            if not resolved or not os.path.isfile(resolved):
                continue
            key = os.path.normcase(os.path.normpath(resolved))
            entry = sources.setdefault(
                key, {"path": resolved, "images": [], "tiled": tiled}
            )
            entry["images"].append(img)
        for reason, names in skipped.items():
            self.logger.info(
                f"{len(names)} {reason} image(s) skipped — not optimized: "
                f"{', '.join(sorted(names))}"
            )
        return sources

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

    def ignore_groups(self, names, case_sensitive: bool = False):
        """Remove objects under any top-level object named in the comma-separated
        ``names`` from ``self.objects``.

        Parameters:
            names: Comma-separated object name patterns to exclude (e.g.
                ``"temp, proxy"``). Each entry is a shell-style glob, so
                ``"temp*"`` catches ``temp_01``/``tempRig`` and ``"*_proxy"``
                catches ``hull_proxy``. A pattern with no wildcard character
                still matches only that exact name, as before.
            case_sensitive: Match names exactly. Off by default, so ``"temp"``
                catches ``TEMP``. The UI arms it from the Ignore row's option-box
                toggle; a headless caller passes the pair as the dict the task
                dispatcher unpacks -- ``{"names": "Temp", "case_sensitive": True}``
                -- while a bare string still selects the insensitive default.
        """
        if not names or not str(names).strip() or not self.objects:
            return
        # Parse the patterns here rather than handing ``filter_list`` a raw
        # string: an all-whitespace field must return early, because a filter
        # with no patterns is a no-op that returns the list unfiltered -- here
        # that would mean matching, and so excluding, every root.
        patterns = ptk.split_delimited_string(
            str(names), delimiter=",", strip_whitespace=True, remove_empty=True
        )
        if not patterns:
            return

        import bpy

        from blendertk.node_utils._node_utils import NodeUtils

        # The glob, the case fold and the pattern list all live in
        # ``filter_list``, so the match rules stay identical here and in
        # mayatk's ``ignore_groups``, which this task mirrors.
        roots = [o for o in bpy.data.objects if o.parent is None]
        excluded = set()
        for root in ptk.filter_list(
            roots,
            inc=patterns,
            map_func=lambda o: o.name,
            ignore_case=not case_sensitive,
        ):
            excluded.add(root)
            excluded.update(NodeUtils.get_children(root, recursive=True))
        if excluded:
            before = len(self.objects)
            self.objects = [o for o in self.objects if o not in excluded]
            removed = before - len(self.objects)
            if removed:
                self.logger.debug(
                    f"Excluded {removed} object(s) under ignored group(s): {patterns}."
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
        """Convert texture paths inside the project to ``//``-relative form.

        The Blender analogue of mayatk's sourceimages relative-path task, and
        scoped the same way: only textures that already live inside the project
        are rewritten. A texture stored anywhere else keeps its absolute path —
        an external reference is usually deliberate (a shared library, another
        project's published maps), and this task must not quietly relocate it.
        ``MatUtils.to_project_relative`` already returns an out-of-project path
        unchanged, so the ``"relative"`` pass is inherently in-scope; what this
        task dropped is the ``"copy"`` pass that used to consolidate externals
        into the project textures folder first (still available on
        ``normalize_texture_paths`` for callers that want it).
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        images = self._get_export_images()
        if not images:
            return
        converted = MatUtils.normalize_texture_paths(mode="relative", images=images)
        if converted:
            self.logger.info(f"Stored project-relative paths on {converted} image(s).")
        external = [
            img.name
            for img in images
            if not (getattr(img, "filepath", "") or "").startswith("//")
        ]
        if external:
            # Not a warning: keeping an external link intact is this task's
            # contract, not a failure. Named so the user can see which maps
            # ship with absolute paths.
            self.logger.info(
                f"{len(external)} texture(s) live outside the project — left on "
                f"their absolute paths: {', '.join(sorted(external))}"
            )

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths by searching the .blend's directory.

        The same hunt heals the lightmap markers first
        (:meth:`LightmapBaker.heal_lightmap_paths`, mirror of mayatk): a
        committed lightmap is a texture dependency with no Image datablock --
        its marker records the folder the bake was committed FROM -- so a
        project reorganised since leaves the FBX manifest pointing at nothing
        while the EXR sits one folder away. A map found by the unique-match
        rule gets its recorded folder rewritten and the manifest republished;
        files are never touched.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        self._heal_lightmap_hints()

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

    # -- lightmap dependencies (mirror of mayatk's TaskManager) ---------------
    # The engine is LightmapBaker (blendertk.light_utils); these are the
    # exporter's thin reads of it, scoped to the export set. Imported lazily:
    # the baker pulls in the Cycles texture baker, which a headless export
    # that never baked anything should not pay for at import time.

    def _lightmap_dependencies(self) -> List[Dict[str, Any]]:
        """The lightmaps the export set's markers name, resolved on disk NOW
        (:meth:`LightmapBaker.lightmap_dependencies`); ``[]`` when none."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        objects = list(self.objects or [])
        if not objects:
            return []
        return LightmapBaker().lightmap_dependencies(objects)

    def _heal_lightmap_hints(self) -> None:
        """Rewrite stale lightmap marker hints to where the maps were found.

        Logged at WARNING like a texture rebind -- a hint moved by name is a
        guess the user should be able to audit -- and what stays missing is
        named, since the exporter's path check is about to fail on it.
        """
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        objects = list(self.objects or [])
        if not objects:
            return
        report = LightmapBaker().heal_lightmap_paths(objects)
        for basename, old_dir, new_dir in report["healed"]:
            self.logger.warning(
                f"Rebound lightmap by unique name match: {basename}: "
                f"{old_dir or '<no folder recorded>'} -> {new_dir}"
            )
        for dep in report["missing"]:
            note = f" ({dep['note']})" if dep.get("note") else ""
            self.logger.warning(
                f"Lightmap could not be resolved: {dep['map']} "
                f"(recorded in {dep['dir'] or '<no folder recorded>'}){note}"
            )

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
        self._refresh_scene_data_node()
        self._include_data_export_node()
        # Mark AFTER _include_data_export_node — assigning self.objects there
        # re-clears the flag via the setter (mirror of mayatk).
        self._data_node_refreshed = True

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

    def _include_data_export_node(self):
        """Fold the ``data_export`` carrier into the export set (shippable).

        Idempotent; a no-op when the scene has no carrier.  Shared by
        :meth:`export_data_node` and :meth:`apply_declared_takes` (mirror of
        mayatk's ``_include_data_export_node``), and — beyond the mayatk twin —
        also clears whatever hide state would make the selection-based FBX
        funnel silently drop the Empty.
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

    def apply_declared_takes(self):
        """Arm one named FBX take (engine AnimationClip) per declared shot.

        Producer-agnostic mirror of mayatk's task: refreshes every producer's
        ``data_export`` channel (skipped when ``export_data_node`` already did
        so this run — the two tasks are default-on neighbors, and one refresh
        per export is enough), then arms ``FbxUtils`` with whatever
        ``fbx_takes`` the scene declares, folding the carrier into the export
        selection with them (a scene declaring none is a true no-op);
        the write realizes them by splitting its baked scene-range AnimStack
        (see ``fbx_utils``' module docstring for the divergence from Maya's
        exporter-state mechanism).  Runs after ``set_bake_animation_range``
        (TASK_ORDER) and widens the scene range to the union of the takes so
        every window lies inside the baked span — the same "union range wins"
        contract as the Maya twin's bake-complex widen.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        if not getattr(self, "_data_node_refreshed", False):
            self._refresh_scene_data_node()

        count = FbxUtils.apply_takes_from_node()
        if count:
            # The carrier ships WITH the clips, never instead of them (mirror
            # of mayatk's ordering, and load-bearing for the same reason now
            # that this task is default-on): included unconditionally, it
            # handed the carrier back to a user who had deliberately unchecked
            # "Export Scene Data Node", on a scene with no shots at all.
            self._include_data_export_node()
            # Armed takes are sticky FbxUtils state consumed by EVERY write
            # until reset — stage the clear deferred (post-write) so nothing
            # leaks into a later export this session (mirror of mayatk's
            # fbx_takes deferred restore).
            self.stage_deferred_restore("fbx_takes", FbxUtils.reset_takes)
            takes = FbxUtils._pending_takes or []
            scene = self._scene()
            start = min(scene.frame_start, *(s for _n, s, _e in takes))
            end = max(scene.frame_end, *(e for _n, _s, e in takes))
            if (start, end) != (scene.frame_start, scene.frame_end):
                self._set_frame_range(start, end)
                self.logger.debug(
                    f"Widened the scene range to {start}-{end} so every take "
                    "window lies inside the baked span."
                )
            self.logger.info(
                f"Animation takes: {count} clip(s) armed from the declared "
                "fbx_takes; shot metadata embedded on data_export."
            )
        else:
            self.logger.debug("No takes declared. Skipping animation takes.")

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

    #: Duplicate Names -- how wide the base-name scan casts, narrowest first;
    #: each tier is a superset of the one above it.  Labels and scope tokens
    #: are mayatk's verbatim (one dial, one portable preset across both DCCs);
    #: the mapping is the obvious one -- a Maya locator is an Empty, a Maya
    #: joint is an armature bone.
    #:
    #: Blender force-uniques ``bpy.data.objects`` names, so the collision that
    #: actually reaches an FBX is the auto ``.001`` suffix -- stripped before
    #: comparing, which is the name a downstream consumer matches.
    _duplicate_name_options: Dict[str, Any] = {
        "OFF": None,
        "Locators": "locators",
        "Locators & Joints": "joints",
        "Connected & Animated": "connected",
        "All Export Objects": "all",
    }

    #: Scope token -> its combo label, for the failure report's header.
    _duplicate_name_labels: Dict[str, str] = {
        v: k for k, v in _duplicate_name_options.items() if v
    }

    @staticmethod
    def _name_is_load_bearing(obj) -> bool:
        """Is *obj* driven, keyed or constrained -- is its NAME what rebuilds
        that plumbing downstream resolves against?"""
        if obj.constraints:
            return True
        anim = obj.animation_data
        return bool(anim and (anim.action or anim.drivers or anim.nla_tracks))

    def _duplicate_name_scope(self, scope: str) -> List[str]:
        """The export-set names *scope* puts in front of the duplicate scan.

        *scope* is validated by :meth:`check_duplicate_names`; anything it did
        not recognize never reaches here (the widest branch is the fallthrough,
        so an unvalidated typo would silently scan a NARROWER tier and pass).
        """
        objects = list(self.objects or [])
        if not objects:
            return []

        if scope == "all":
            picked = objects
        else:
            picked = [o for o in objects if o.type == "EMPTY"]
            if scope != "locators":
                picked += [o for o in objects if o.type == "ARMATURE"]
                if scope != "joints":
                    picked += [
                        o
                        for o in objects
                        if o.type not in ("EMPTY", "ARMATURE")
                        and self._name_is_load_bearing(o)
                    ]

        names = [o.name for o in picked]
        # Bones share the FBX node namespace with objects, so two armatures
        # carrying a same-named bone collide exactly like two same-named
        # Empties -- and bone names are unique only WITHIN an armature.
        names += [
            b.name
            for o in picked
            if o.type == "ARMATURE" and o.data
            for b in o.data.bones
        ]
        return names

    def check_duplicate_names(self, scope=None) -> tuple:
        """Nodes sharing a base name once Blender's auto ``.001``-style suffix is stripped --
        the Blender analogue of Maya's same-short-name-under-different-parents collision
        (Blender itself force-uniques ``bpy.data.objects`` names, so the exact Maya failure
        mode can't occur; this catches the case that motivated the check).

        Parameters:
            scope: One of :attr:`_duplicate_name_options`' values --
                ``"locators"``, ``"joints"``, ``"connected"`` or ``"all"``.
                Falsy (or ``"OFF"``) skips the check; ``True`` is read as
                ``"locators"``, the scope the pre-dial checkbox had.
        """
        if not scope or str(scope).upper() == "OFF":
            return True, []
        scope = "locators" if scope is True else str(scope).lower()
        if scope not in self._duplicate_name_labels:
            # Loud, not a fallthrough: the resolver's widest branch is its
            # default, so a typo'd scope would quietly scan a NARROWER tier
            # than the caller asked for and PASS the export on that basis.
            valid = ", ".join(sorted(self._duplicate_name_labels))
            return False, [f"Unknown duplicate-name scope {scope!r}. Valid: {valid}."]

        groups = defaultdict(list)
        for name in self._duplicate_name_scope(scope):
            groups[CoreUtils.strip_dup_suffix(name)].append(name)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if not dupes:
            return True, []

        label = self._duplicate_name_labels.get(scope, scope)
        messages = [f"{len(dupes)} duplicate base name(s) in scope '{label}':"]
        messages += [
            f"  - {base} (x{len(names)}): {', '.join(names)}"
            for base, names in sorted(dupes.items())
        ]
        return False, messages

    def check_duplicate_locator_names(self, enabled=True) -> tuple:
        """Deprecated alias for ``check_duplicate_names("locators")``.

        Kept for one release: headless callers (and presets saved before the
        check grew its scope dial) still pass this key as a bool.
        """
        return self.check_duplicate_names("locators" if enabled else None)

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

    def _texture_staging_dir(self, tag: str):
        """Where staged (non-write-back) texture processing lands for this run
        (mirror of mayatk's).

        Shared by :meth:`convert_textures` and :meth:`optimize_textures`, so
        both halves of a run stage into ONE place. Staged files are temp only
        when nothing after the export references them — the deliverable
        embeds or copies its own (GLB-only output, or an FBX preset with
        ``embed_textures`` / ``path_mode COPY``) — or when direct TaskManager
        use has no export path to stage beside. Otherwise the written FBX
        references the staged files, so they land durably in ``textures/``
        beside it.

        Returns:
            tuple: ``(staging_dir, temp_staging)``.
        """
        export_path = getattr(self, "export_path", "")
        temp_staging = (
            bool(getattr(self, "_glb_only", False))
            or bool(getattr(self, "_fbx_media_selfcontained", False))
            or not export_path
        )
        if temp_staging:
            return ptk.TempArtifacts(f"scene_exporter_{tag}").dir_path(), True
        staging_dir = os.path.join(os.path.dirname(export_path), "textures")
        os.makedirs(staging_dir, exist_ok=True)
        return staging_dir, False

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

        **Non-destructive by default** (``_texture_write_back`` unset -- the
        Texture Output combo at "Export Copies"): the export images'
        ``filepath``s are recorded, the Map Updater writes its outputs into
        this run's staging dir (:meth:`_texture_staging_dir` -- the same
        place ``optimize_textures`` stages, temp or durable by the same
        rule) and repaths the images at them for the write, and ONE deferred
        restore puts every original path back (and deletes a temp staging
        dir). The scope is :meth:`MatUtils.image_paths_scope` -- the same
        object a script would ``with`` -- handed to ``stage_deferred_context``
        because the write must still see the repaths. Sources on disk are
        never touched in this mode. (Maya's twin must snapshot and restore a
        whole node graph here; Blender's updater only repaths datablocks, so
        the paths ARE the state.)

        **Write-back mode** (Texture Output at "Scene Files (In Place)"): the
        Map Updater's plain in-place migration -- outputs land beside their
        sources, the repaths persist, and are relativized here if
        ``convert_to_relative_paths`` is on (this task now runs after it, so
        the staged mode's absolute paths are never copied into the project).

        Runs in TASK_ORDER's material-cleanup phase after
        ``resolve_invalid_texture_paths`` and ``convert_to_relative_paths``,
        and before ``optimize_textures``.
        """
        import shutil

        if not template:
            return None
        from blendertk.mat_utils._mat_utils import MatUpdater, MatUtils

        materials = self._get_all_materials()
        if not materials:
            self.logger.info("Texture template: no export materials to convert.")
            return None
        write_back = getattr(self, "_texture_write_back", False)
        self.logger.info(
            f"Converting textures for {len(materials)} material(s) "
            f"to the {template!r} template"
            + (
                " — migrating the scene's materials..."
                if write_back
                else " — staging for export only (scene restored after)..."
            )
        )
        if write_back:
            config: Any = template
        else:
            staging_dir, temp_staging = self._texture_staging_dir("texconv")
            scope = contextlib.ExitStack()
            if temp_staging:
                scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
            scope.enter_context(
                MatUtils.image_paths_scope(self._get_export_images(materials))
            )
            self.stage_deferred_context("convert_textures", scope)
            config = {"preset": template, "move_to_folder": staging_dir}
        # Guarded because a task exception ABORTS the pipeline (TaskFactory
        # re-raises after logging) -- one unreadable texture would kill the
        # whole export with a traceback. The designed failure path is the
        # paired check instead: it validates the actual post-task state, so
        # masks this conversion could not bring to the template fail the
        # export cleanly, with the residuals named and this error above them.
        try:
            MatUpdater.update_materials(materials=materials, config=config)
        except Exception:  # noqa: BLE001 — the paired check is the gate
            self.logger.error(
                f"Texture conversion to {template!r} failed; "
                "check_material_compatibility will gate on what remains.",
                exc_info=True,
            )
        # The conversion repaths image nodes: the cached material/texture reads
        # are now stale, including the compatibility check's own.
        self._cached_materials = None
        if write_back and getattr(self, "_relative_paths_enabled", False):
            images = self._get_export_images()
            if images:
                MatUtils.normalize_texture_paths(mode="relative", images=images)
        return None

    def optimize_textures(self, template):
        """Optimize the maps shipping with this export, by map type (mirror
        of mayatk's).

        The export-time twin of the Map Converter's Optimize pass: each
        shipping texture is run through ``ptk.MapOptimizer.optimize_map``,
        whose per-map-type rules (mode coercion, bit depth, palette handling)
        do the work. *template* selects the tier, exactly as the converter's
        Target combo does: ``True`` = generic per-map-type optimization (each
        map keeps its container); a workflow template name (folded from
        cmb005 by ``b000``) additionally drives container and bit depth from
        the template's per-map-type :class:`~pythontk.OutputSpec`, clamped to
        scene-readable containers (:meth:`_scene_safe_output_type`). The
        template's ``DeliveryBudget`` stays ADVISORY unless the size dial
        asks for it: reported by the paired check, not resampled.

        The size ceiling (``_texture_max_size``, a per-run mode stamped by
        ``perform_export`` like the write-back flag) is the pass's one size
        dial — unset by default (never resamples), a fixed longest-edge
        ceiling, or the template-budget sentinel (enforce the selected
        template's own budget's size ceiling). In the panel it rides the same
        **Optimize Textures** combo as the pass switch ("Optimize + Max …" —
        b000 decomposes the choice back into these two inputs); headless
        callers still pass ``texture_max_size`` separately. Resolved by
        :meth:`_texture_size_clamp`; a ceiling only ever shrinks and keeps
        aspect.

        The check half is :meth:`check_texture_optimization`; both judge
        through :meth:`_assess_optimization`, so the task and its gate cannot
        drift. Already-optimal maps ship as-is, untouched.

        **Non-destructive by default** (``_texture_write_back`` unset — the
        Texture Output combo at "Export Copies"): sources are never touched.
        Optimized copies are staged, the export images are repointed at them
        for the write, and ONE deferred restore (post-write, so the FBX write
        and any GLB conversion both read the staged paths) puts every
        original ``filepath`` back. Where the staged files go — and whether
        they outlive the export — is :meth:`_texture_staging_dir` (shared
        with ``convert_textures``): temp, a ``TempArtifacts`` dir deleted by
        that restore, when the deliverable carries its own copies (GLB-only
        output, or an FBX preset with ``embed_textures`` / ``path_mode
        COPY``); else durable in ``textures/`` beside the export and kept
        (``check_existing=True`` makes re-exports incremental).

        **Write-back mode** (Texture Output at "Scene Files (In Place)"): the
        optimization is written over the scene's own texture files (originals
        archived beside them in ``original_textures/``) and persists — same
        philosophy as ``convert_textures`` in that mode.

        Runs LAST in the material phase, after ``convert_to_relative_paths``
        (staged absolute paths must not be copied into the project's textures
        folder). Per-texture failures fall back to the original file with a
        warning — the paired check then names anything left unoptimized.
        """
        import shutil

        from blendertk.mat_utils._mat_utils import MatUtils

        if not template:
            return
        tpl = template if isinstance(template, str) else None

        sources = self._export_texture_sources()
        if not sources:
            self.logger.debug("No export texture images — nothing to optimize.")
            return

        pass_desc = f"the {tpl!r} template" if tpl else "map type (generic)"
        clamp = self._texture_size_clamp(tpl)
        clamp_desc = self._texture_size_clamp_desc(tpl)
        if clamp_desc:
            pass_desc += f", {clamp_desc}"
        if clamp.get("enforce_budget") and not ptk.OutputTemplates.budget(tpl).max_size:
            self.logger.warning(
                f"Optimize Textures is at 'Optimize + Template Budget' but "
                f"the {tpl!r} template is unbudgeted (an authoring target) — "
                "no size clamp applied. Choose an explicit 'Optimize + Max …' "
                "ceiling to resize."
            )

        # Only maps the pass would actually CHANGE are touched — sorted so the
        # collision-subdir assignment below is deterministic across runs. An
        # unreadable source drops out here (None verdict) — check_valid_paths
        # is its gate.
        pending = []
        for _key, entry in sorted(sources.items()):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict and verdict["needed"]:
                pending.append((entry, verdict))
        if not pending:
            self.logger.info(
                f"Texture optimization: all {len(sources)} shipping "
                f"texture(s) already optimal for {pass_desc}."
            )
            return

        write_back = getattr(self, "_texture_write_back", False)
        staging_dir = None
        temp_staging = False
        if not write_back:
            staging_dir, temp_staging = self._texture_staging_dir("texopt")

        self.logger.info(
            f"Optimizing {len(pending)} of {len(sources)} texture(s) for "
            f"{pass_desc}"
            + (
                " — writing back to the scene's texture files..."
                if write_back
                else " — staging for export only (scene untouched)..."
            )
        )

        # Staged repoints are image_paths_scope entries under ONE ExitStack (a
        # temp staging dir's removal rides the same stack), handed to
        # stage_deferred_context so the write still sees the staged paths.
        scope = contextlib.ExitStack()
        if not write_back and temp_staging:
            scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
        repathed: set = set()  # images already scoped (LIFO restores the original)
        written_paths: Dict[str, str] = {}  # normcased written -> source
        used_names: Dict[str, int] = {}
        optimized = failed = 0
        total_before = total_after = 0

        for entry, verdict in pending:
            src = entry["path"]
            output_type = verdict["output_type"]
            size_before = os.path.getsize(src) if os.path.isfile(src) else 0
            try:
                if write_back:
                    written = ptk.MapOptimizer.optimize_map(
                        src,
                        output_profile=tpl,
                        output_type=output_type,
                        old_files_folder="original_textures",
                        **clamp,
                    )
                else:
                    # Two different source folders can hold same-named maps —
                    # a flat staging dir would silently collapse them, so the
                    # second+ claimant of a basename stages into a subdir.
                    base = os.path.basename(src).lower()
                    nth = used_names.get(base, 0)
                    used_names[base] = nth + 1
                    out_dir = (
                        staging_dir
                        if nth == 0
                        else os.path.join(staging_dir, f"alt{nth}")
                    )
                    written = ptk.MapOptimizer.optimize_map(
                        src,
                        output_dir=out_dir,
                        output_profile=tpl,
                        output_type=output_type,
                        check_existing=not temp_staging,
                        **clamp,
                    )
                    if not temp_staging:
                        # check_existing keys reuse on mtime alone, so a
                        # staged file from an earlier run under DIFFERENT
                        # settings (another template, or none) is "newer than
                        # the source" and gets reused while still needing
                        # work — the task would then report success and its
                        # own paired check would name it as a residual with
                        # no UI way out. Re-verify the reused file against
                        # THIS run's pass.
                        stale = self._assess_optimization(written, tpl)
                        if stale and stale["needed"]:
                            written = ptk.MapOptimizer.optimize_map(
                                src,
                                output_dir=out_dir,
                                output_profile=tpl,
                                output_type=output_type,
                                check_existing=False,
                                **clamp,
                            )
            except Exception as e:  # noqa: BLE001 — per-texture fallback
                failed += 1
                self.logger.warning(
                    f"Texture optimization failed for "
                    f"{os.path.basename(src)} — the original ships instead: {e}"
                )
                continue

            written_key = os.path.normcase(os.path.normpath(written))
            if written_key in written_paths and written_paths[written_key] != src:
                # Suffix normalization collapsed two distinct sources onto one
                # output name — keep the first, ship the second unoptimized.
                failed += 1
                self.logger.warning(
                    f"Optimized name collision: {os.path.basename(src)} and "
                    f"{os.path.basename(written_paths[written_key])} both "
                    f"resolve to {os.path.basename(written)} — the original "
                    "ships for the latter."
                )
                continue
            written_paths[written_key] = src

            optimized += 1
            total_before += size_before
            total_after += os.path.getsize(written) if os.path.isfile(written) else 0

            # Repoint the consuming images wherever the written file is not
            # the current target (always, when staging; on a normalized
            # filename, when writing back).
            if written_key != os.path.normcase(os.path.normpath(src)):
                new_path = written.replace("\\", "/")
                for img in entry["images"]:
                    if write_back or img in repathed:
                        img.filepath = new_path
                    else:
                        repathed.add(img)
                        scope.enter_context(
                            MatUtils.image_paths_scope([img], new_path=new_path)
                        )

        if not write_back and repathed:
            self.stage_deferred_context("optimize_textures", scope)
        else:
            scope.close()  # nothing repathed: drop a temp dir right away

        if optimized:
            sizes = ptk.FileUtils.format_bytes_delta(total_before, total_after)
            destination = (
                "written back to the scene's texture files (originals archived "
                "in 'original_textures')"
                if write_back
                else (
                    "staged for the write only — image paths restored after export"
                    if temp_staging
                    else f"staged beside the export in {staging_dir!r} (the FBX "
                    "references them; image paths restored after export)"
                )
            )
            self.logger.info(
                f"Optimized {optimized} texture(s): {sizes}; {destination}."
            )
        if failed:
            self.logger.warning(
                f"{failed} texture(s) could not be optimized and ship as-is."
            )

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

    def check_texture_optimization(self, template) -> tuple:
        """Every shipping texture is optimized for its map type (mirror of mayatk's).

        The check half of the Optimize Textures checkbox: armed alongside
        :meth:`optimize_textures` by the same setting, judged through the same
        :meth:`_assess_optimization`, and — because checks run after tasks —
        validating the **staged/written** state the export will actually
        read. It FAILS only for a texture the task should have optimized but
        could not (a per-texture failure), naming the residuals rather than
        blocking the fix.

        Everything the pass deliberately does not touch is reported without
        failing: tiled/UDIM sets (measured via their 1001 tile), and the
        active template's ``DeliveryBudget`` advisories — advisory means
        REPORTED, not resampled, and never a blocked export. With a Max
        Texture Size clamp set the resize IS part of the pass, so an
        over-size residual the task could not shrink fails here like any
        other unoptimized map. Those notes are logged directly (the runner
        only surfaces messages from failing checks). Unreadable or missing files are :meth:`check_valid_paths`'
        domain and are skipped here.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        tpl = template if isinstance(template, str) else None

        offenders: List[str] = []
        notes: List[str] = []
        # include_tiled: the task cannot TOUCH a tiled set, but the gate
        # should still measure it (via its 1001 tile) so an unoptimized one
        # is at least reported instead of slipping past the scan.
        for _key, entry in sorted(
            self._export_texture_sources(include_tiled=True).items()
        ):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict is None:
                continue
            name = os.path.basename(entry["path"])
            if verdict["needed"]:
                names = ", ".join(sorted(img.name for img in entry["images"]))
                line = f"  - {names} -> {name}: {'; '.join(verdict['reasons'])}"
                if entry["tiled"]:
                    notes.append(line + " (tiled set — not auto-optimized)")
                else:
                    offenders.append(line)
            for warning in verdict["warnings"]:
                notes.append(f"  - {name}: {warning}")

        # Advisory tier: budget notes and untouchable residuals inform, never
        # gate. Logged directly — the runner only surfaces messages from
        # FAILING checks, so returning them on a pass would be a silent no-op.
        if notes and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(f"Texture optimization notes ({len(notes)})", notes)

        if offenders:
            pass_desc = f"the {tpl!r} template" if tpl else "their map type"
            return False, [
                f"{len(offenders)} texture(s) are not optimized for "
                f"{pass_desc} after the optimization task:"
            ] + offenders

        return True, []

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
        messages = []
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            messages.append(f"{len(missing)} missing file(s): {shown}")

        # Lightmap dependencies (mirror of mayatk's check) -- baked maps the
        # bake markers name. No Image datablock references them, so the gate
        # above never sees them, and a scene migrated with its textures ships
        # its GLB unlit and its FBX manifest pointing at nothing. Resolved the
        # way the GLB applier resolves them; a map found only by search still
        # ships (the conversion is handed that folder) but says so, since the
        # manifest's hint is stale until the resolve task rewrites it.
        missing_lightmaps = []
        stale_lightmaps = []
        for dep in self._lightmap_dependencies():
            if not dep["path"]:
                missing_lightmaps.append(dep)
            elif dep["found_by"] != "hint":
                stale_lightmaps.append(dep)
        if missing_lightmaps:
            entries = []
            for dep in missing_lightmaps:
                where = f"{dep['dir']}/{dep['map']}" if dep["dir"] else dep["map"]
                note = f" ({dep['note']})" if dep.get("note") else ""
                entries.append(
                    f"Missing Lightmap: {', '.join(dep['objects'])} -> {where}{note}"
                )
            messages.append(
                f"{len(missing_lightmaps)} lightmap(s) the bake markers name are "
                "not on disk. The GLB would ship unlit and the FBX manifest would "
                "point at nothing. Relocate them (Texture Path Editor ▸ Find & "
                "Copy Textures, lightmaps included) or revert the bake (Lightmap "
                "Baker ▸ Revert)."
            )
            messages.extend(entries[:10] + (["…"] if len(entries) > 10 else []))
        for dep in stale_lightmaps:
            messages.append(
                f"Lightmap {dep['map']}: the recorded folder "
                f"{dep['dir'] or '<none>'} no longer holds it; found at "
                f"{dep['path']} (shipped from there; enable the Resolve Invalid "
                "Texture Paths task to rewrite the marker)."
            )

        if missing or missing_lightmaps:
            return False, messages
        return True, messages

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
            return False, [f"{len(oversized)} texture(s) exceed {max_mb:g} MB: {shown}"]
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
        "convert_to_relative_paths",
        # LAST in the material phase (mirrors mayatk): the texture-processing
        # pair — convert then optimize what will actually ship — and their
        # staged absolute paths must never be seen by convert_to_relative_paths
        # (which would copy them into the project).
        "convert_textures",
        "optimize_textures",
        # Phase 4 — Animation (bake THEN optimize THEN snap/tie THEN set range)
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "set_bake_animation_range",
        # Phase 5 — Metadata carrier (last, so it sees the final export set);
        # takes AFTER it so one producer refresh serves both (mirror of mayatk)
        "export_data_node",
        "apply_declared_takes",
    ]

    # Texture Output — do the texture-processing tasks (convert_textures,
    # optimize_textures) modify the scene's textures, or stage copies for the
    # export and restore the scene afterwards? Data is the write-back flag
    # perform_export pops (never a dispatched task). Mirrors mayatk.
    _texture_output_options: Dict[str, Any] = {
        "Export Copies (Scene Untouched)": False,
        "Scene Files (In Place)": True,
    }

    #: Longest-edge ceilings offered by Optimize Textures. Mirrors mayatk.
    _TEXTURE_MAX_SIZES = (512, 1024, 2048, 4096, 8192)

    # Optimize Textures — the pass switch and its size dial in ONE combo, so
    # no state is representable where a ceiling is set but nothing would apply
    # it (the widget the old checkbox+combo pairing had to grey out). Data is
    # decomposed by b000 into the two engine inputs the run has always taken:
    # falsy 0 = OFF (the task filter drops it), True = optimize without
    # resampling, an int = optimize + hard pixel ceiling, and
    # TEXTURE_MAX_SIZE_TEMPLATE = optimize + enforce the selected template's
    # own budget. OFF is index 0 (default) and the sentinel is LAST — combos
    # persist by index. Mirrors mayatk.
    _optimize_textures_options: Dict[str, Any] = {
        "OFF": 0,
        "Optimize": True,
        **{f"Optimize + Max {s}": s for s in _TEXTURE_MAX_SIZES},
        "Optimize + Template Budget": _TaskDataMixin.TEXTURE_MAX_SIZE_TEMPLATE,
    }

    # Texture File Type — the container dial for EVERY texture the export
    # ships (scene/FBX maps and a GLB's embedded copies alike; the per-
    # destination clamps live in _resolved_output_type / _glb_texture_params).
    # Built from the shared registry so a container added to ImgUtils appears
    # here and in the Map Converter's own Format menu without an edit, plus
    # KTX2 — a delivery-only container no scene image reads, offered here
    # because a GLB deliverable can carry it. "Original" is index 0 and the
    # falsy sentinel: a TEMPLATE contract (templates persist combos by index),
    # so never reorder or insert above it. Mirrors mayatk.
    _texture_file_type_options: Dict[str, Any] = {
        **dict(
            ptk.OutputTemplates.format_choices(sentinel="Original", sentinel_first=True)
        ),
        "KTX2": "ktx2",
    }

    _export_mode_options: Dict[str, Any] = {
        "All Scene Objects": "all",
        "All Visible Objects": "visible",
        "Selected Objects Only": "selected",
    }

    _frame_rate_options: Dict[str, Any] = {
        (
            f"{k}"
            if v is None
            else (f"{v:g} fps" if any(c.isdigit() for c in k) else f"{k} ({v:g} fps)")
        ): (k if v is not None else None)
        for k, v in ptk.insert_into_dict(ptk.VidUtils.FRAME_RATES, "OFF", None).items()
    }

    _scene_unit_options: Dict[str, Any] = {
        k: v for k, v in ptk.insert_into_dict(_LINEAR_UNIT_VALUES, "OFF", None).items()
    }

    def __init__(self, logger):
        super().__init__(logger)
        self._objects = None
        self._cached_materials = None
        self._unseen_anim_warned = False

    def _execute_tasks_and_checks(self, tasks_only, checks_only):
        # smart_bake's completion log mentions the follow-on optimize_keys
        # task; the generic TaskFactory knows nothing about either, so the
        # flag is set here, in the consumer that reads it.
        self._optimize_keys_enabled = bool(tasks_only.get("optimize_keys", False))
        # convert_textures (write-back mode) runs after convert_to_relative_paths
        # and relativizes its own repathed images only if that task is on.
        self._relative_paths_enabled = bool(
            tasks_only.get("convert_to_relative_paths", False)
        )
        # The NLA/data-level-animation advisory fires once per run, not once
        # per check (see _warn_unseen_animation).
        self._unseen_anim_warned = False
        return super()._execute_tasks_and_checks(tasks_only, checks_only)

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Invalidate the materials cache whenever objects change.

        Each export run re-seeds the object set before tasks execute, so this
        doubles as the per-run reset of the producer-refresh marker
        (``export_data_node`` sets it; ``apply_declared_takes`` reads it) —
        mirror of mayatk's setter.
        """
        self._objects = value
        self._cached_materials = None
        self._data_node_refreshed = False

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
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "panel": "settings",
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
                "panel": "settings",
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
                "panel": "settings",
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
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials",
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
                "group": "Materials",
                "setText": "Convert To Relative Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Convert To Relative Paths",
                    body="Rewrite the export materials' texture paths in "
                    "Blender's //-relative project form.",
                    notes=[
                        "Scoped to textures already inside the project. A "
                        "texture stored anywhere else keeps its absolute path — "
                        "an external reference is usually deliberate, and this "
                        "task never relocates it. The log names any it left "
                        "alone.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Resolve Invalid Texture Paths",
                    body="Rebind broken texture paths by hunting for the missing "
                    "file under the .blend's own directory. Committed lightmaps "
                    "get the same hunt: a bake marker whose recorded folder no "
                    "longer holds its map is rewritten to where the map was "
                    "found, and the FBX manifest republished.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content.",
                        "Lightmap files are never moved — only the marker's "
                        "recorded folder changes. To gather them into the "
                        "project use Texture Path Editor ▸ Find &amp; Copy.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            # -- Textures group: the Texture Output gate FIRST, then the three
            # dials it governs directly beneath it, so the gate and the gated
            # read as one block in the Tasks combo. Mirrors mayatk.
            "texture_write_back": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Output",
                    body="Whether the texture rows below — the <b>Textures</b> "
                    "template conversion and the <b>Optimize Textures</b> "
                    "pass (its size ceiling included) — modify the scene's "
                    "textures, or leave the scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: processed maps are staged for the "
                        "write (a temp folder when the deliverable embeds "
                        "its media, else <b>textures/</b> beside it), the "
                        "images read them for the export, and the scene's "
                        "paths are restored afterwards.",
                        "<b>Scene Files (In Place)</b> — permanent: the "
                        "conversion repaths the materials and the "
                        "optimization overwrites the scene's own texture "
                        "files (originals archived beside each texture in an "
                        "<b>original_textures</b> folder). Not reverted after "
                        "export.",
                    ],
                    notes=[
                        "Inert unless a template is selected or Optimize "
                        "Textures is on.",
                    ],
                ),
                "add": self._texture_output_options,
            },
            "convert_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # The widget keeps the objectName it had as a Settings row, so
                # every saved template key, ``cmb005_init`` and b000's reads
                # stay valid across the move into the Tasks combo.
                "object_name": "cmb005",
                "set_row_label": "Texture Template",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Template",
                    body="Convert the export's textures to a target texture "
                    "template (a pythontk map-registry workflow) before the "
                    "write — channel packing and shading model re-authored to "
                    "match what the destination engine expects.",
                    bullets=[
                        "<b>As Authored</b> (default) — send textures exactly "
                        "as the scene references them; converts nothing.",
                        "A template — materials are rebuilt through the Map "
                        "Updater, and a paired check fails the export if any "
                        "mask map still does not match.",
                    ],
                    notes=[
                        "Also drives <b>Optimize Textures</b>: the template's "
                        "per-map-type output spec supplies each map's bit "
                        "depth and container, and its size budget is what "
                        "that combo's Template Budget option enforces.",
                        "Where the rebuilt maps land — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                    ],
                ),
            },
            "optimize_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # NOT the old checkbox's objectName: a preset saved before the
                # merge carries optimize_textures (a bool) plus a separate
                # texture_max_size (an index), and letting the bool restore
                # onto this combo would keep the pass while silently dropping
                # the preset's size ceiling. A fresh name makes such a preset
                # trip the PresetManager's uncovered-keys warning instead, so
                # the user re-saves and the template is whole again. (The TASK
                # key stays optimize_textures — b000 decomposes this widget's
                # value back into the optimize_textures + texture_max_size
                # inputs the engine has always taken, so headless callers and
                # TASK_ORDER see no change.) Mirrors mayatk.
                "object_name": "texture_optimize",
                "set_row_label": "Optimize Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Textures",
                    body="Run the Map Converter's per-map-type optimization "
                    "pass on the textures shipping with this export — mode "
                    "and bit depth corrected per map type, the export reads "
                    "the optimized copies — with an optional longest-edge "
                    "ceiling: larger maps are downsampled, smaller ones "
                    "never grown.",
                    bullets=[
                        "<b>OFF</b> — ship every map as it is.",
                        "<b>Optimize</b> — the pass without resampling (a "
                        "template's size budget is only reported).",
                        "<b>Optimize + Max 512 … 8192</b> — the pass plus a "
                        "hard pixel ceiling, whatever the template says.",
                        "<b>Optimize + Template Budget</b> — the pass plus "
                        "the selected <b>Textures</b> template's own size "
                        "budget (e.g. glTF/URP 2048, HDRP/Unreal 4096; the "
                        "power-of-two rule is not applied). No resize with "
                        "Textures at <b>As Authored</b> or an unbudgeted "
                        "template.",
                    ],
                    notes=[
                        "With a <b>Textures</b> template selected, the "
                        "template's per-map-type output spec also drives each "
                        "map's container and bit depth (delivery containers "
                        "like KTX2 stay with the GLB half of <b>Texture File "
                        "Type</b>); at <b>As Authored</b> it is a generic "
                        "per-map-type pass and each map keeps its container.",
                        "The ceiling also caps a GLB deliverable's embedded "
                        "copies — one size policy for everything the export "
                        "ships.",
                        "Where the optimized maps go — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                        "Already-optimal maps are left untouched; the paired "
                        "check names anything the pass could not optimize.",
                    ],
                ),
                "add": self._optimize_textures_options,
            },
            "texture_file_type": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture File Type",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture File Type",
                    body="Container every texture shipping with this export is "
                    "written in — the maps beside (or inside) the FBX and the "
                    "images embedded in a GLB alike.",
                    bullets=[
                        "<b>Original</b> — keep each source's container; with "
                        "a <b>Textures</b> template selected, the template's "
                        "per-map-type container decides.",
                        "<b>PNG … HDR</b> — write every map as that format.",
                        "<b>KTX2</b> — GPU-compressed Basis for web/XR "
                        "runtimes (UASTC for normals/data, ETC1S for color; "
                        "lightmaps stay lossless WebP). Ships only inside a "
                        "GLB, and the GLB stays importable everywhere: each "
                        "compressed texture embeds a standard PNG/JPEG "
                        "fallback (the KHR_texture_basisu escape hatch), so "
                        "Blender, Unreal or stock Unity read the fallbacks "
                        "while basisu-capable viewers get the GPU-resident "
                        "set. Requires KTX-Software's <b>toktx</b>.",
                    ],
                    notes=[
                        "Naming a type outranks the template's per-map-type "
                        "container, which still supplies bit depth and budget.",
                        "Each destination clamps what it cannot carry: a "
                        "scene file node and an FBX cannot read KTX2, so the "
                        "scene keeps its own container there, and a GLB falls "
                        "back to PNG for anything glTF cannot embed "
                        "(PNG/JPEG/WebP/KTX2 are the ones it can).",
                        "Applied by <b>Optimize Textures</b> for scene maps; "
                        "a GLB deliverable is re-encoded whether or not that "
                        "pass runs.",
                    ],
                ),
                "add": self._texture_file_type_options,
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Smart Bake",
                "setToolTip": TooltipFormat.fmt(
                    title="Smart Bake",
                    body="Bake the rig's indirect animation — constraints "
                    "(including IK), drivers and expressions, driven blend-shape "
                    "weights — down to plain keyframes, which is all an FBX can "
                    "carry.",
                    notes=[
                        "The time range is detected from the driving animation itself.",
                        "Bakes into a fresh Action while muting the identified "
                        "sources; the pre-bake state is restorable afterward via "
                        "SmartBake.restore.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "group": "Animation",
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
                "group": "Animation",
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
                "group": "Animation",
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
                "group": "Animation",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Auto Set Bake Animation Range",
                    body="Set the scene's frame range to the first and last "
                    "keyframe of the exported objects for the duration of the "
                    "export.",
                    notes=[
                        "Runs after Smart Bake, Optimize, Snap and Tie, so it "
                        "measures the final keyframe extent — but <b>Export "
                        "Shots as Animation Takes</b> runs after it and widens "
                        "the range again.",
                        "The original frame range is restored after the write.",
                    ],
                ),
                "setChecked": True,
            },
            "apply_declared_takes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Export Shots as Animation Takes",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Shots as Animation Takes",
                    body="Split the exported animation into one named FBX take "
                    "per shot, so the file arrives in an engine as separate "
                    "AnimationClips instead of a single continuous clip.",
                    notes=[
                        "Requires shots defined in the Shots panel; no-op when "
                        "the scene declares none.",
                        "This is <b>not</b> what ships the shot metadata — "
                        "<b>Export Scene Data Node</b> already does that, and "
                        "the two share one refresh. With this off, the "
                        "deliverable describes shots it cannot play.",
                        "The split <b>replaces</b> the single scene-range take "
                        "with the per-shot ones, so turn it off when you need "
                        "the continuous clip. (Maya's exporter keeps both; "
                        "that difference is in the artifact, not in the API.)",
                        "Forces Bake Animation on and widens the scene frame "
                        "range to the union of all shots, overriding <b>Auto "
                        "Set Bake Animation Range</b>. Both are restored after "
                        "the write.",
                    ],
                ),
                # Default ON, matching mayatk's mirror of this panel and for the
                # same reason: off, a scene with shots exports metadata naming
                # clips the file does not contain -- wrong in the FBX and in the
                # GLB converted from it at once. A scene with no shots no-ops.
                "setChecked": True,
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "panel": "settings",
                "set_row_label": "Ignore",
                "setPlaceholderText": "Group names to ignore (comma-separated, wildcards ok)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated name patterns of top-level objects to "
                    "drop from the export set.",
                    notes=[
                        "Example: temp, proxy",
                        "Wildcards: <b>*</b> any run of characters, <b>?</b> a "
                        "single one &mdash; <b>temp*</b> catches temp_01 and "
                        "tempRig, <b>*_proxy</b> catches hull_proxy.",
                        "A pattern with no wildcard matches that exact name.",
                        "Leave empty to skip.",
                        "Matching ignores case unless the <b>Aa</b> button beside "
                        "the field is on.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "panel": "settings",
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
            # NOTE: `version` is a UI-only field —
            # consumed by SceneExporter (pop'd before the task dispatch), never
            # executed by the task pipeline. Mirrors mayatk.
            "version": {
                "widget_type": "QLineEdit",
                "panel": "settings",
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
                        "The extension is added automatically — do not include {ext}."
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
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check For Referenced Objects",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Referenced Objects",
                    body="Fails the export when the scene contains linked "
                    "libraries — Blender's analogue of Maya file references.",
                    notes=["Make the data local to pass."],
                ),
                "setChecked": True,
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
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
            "check_duplicate_names": {
                "widget_type": "ComboBox",
                "group": "Hierarchy & Naming",
                "set_row_label": "Duplicate Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Names",
                    body="Fails the export when two nodes in the export set "
                    "share a base name. The dial is how wide it looks — each "
                    "step includes the one above it.",
                    bullets=[
                        "<b>Locators</b> — Empties: attach points and sockets, "
                        "which whatever consumes them downstream matches by "
                        "name.",
                        "<b>Locators &amp; Joints</b> — adds armatures and "
                        "their bones, which the FBX writes as the skeleton; "
                        "duplicate bone names break skinning and retargeting "
                        "on import, and bone names are unique only within one "
                        "armature.",
                        "<b>Connected &amp; Animated</b> — adds every object "
                        "carrying a constraint, an action, drivers or NLA "
                        "tracks. Their names are what the take and metadata "
                        "bindings resolve against.",
                        "<b>All Export Objects</b> — every object in the set. "
                        "The strictest setting: expect noise from helper "
                        "hierarchies that collide harmlessly in the FBX.",
                    ],
                    notes=[
                        "Blender's auto '.001' suffix is stripped before "
                        "comparing, so 'pivot' and 'pivot.001' collide — which "
                        "is what a consumer matching them by name downstream "
                        "will see.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._duplicate_name_options,
                # Applied after 'add' (which lands on index 0): Locators is the
                # scope the check shipped with as a plain checkbox.
                "setCurrentIndex": 1,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
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
                "group": "Hierarchy & Naming",
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
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
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
                "group": "Geometry",
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
                "group": "Geometry",
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
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
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
                "group": "Materials & Paths",
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
                "group": "Materials & Paths",
                "setText": "Check For Valid Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Valid Paths",
                    body="Fails the export when a texture feeding the export "
                    "materials, a committed lightmap, or a linked library does "
                    "not resolve on disk.",
                    notes=[
                        "Lightmaps have no Image datablock — the bake marker "
                        "records the folder it was committed from. A map that "
                        "folder no longer holds is looked for where the GLB "
                        "conversion looks (the project's texture folders, then "
                        "all of them recursively); found elsewhere it ships and "
                        "is noted, found nowhere it fails the export.",
                        "Images that will not ship (the World/HDR environment "
                        "texture, images left orphaned by a duplicate-material "
                        "cleanup) are not reported.",
                    ],
                ),
                "setChecked": True,
            },
            "check_texture_file_size": {
                # Mirrors mayatk: a bounded MB budget is a spin box, and 0 reads
                # back as "OFF" (the check treats a falsy limit as disabled).
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Size (MB)",
                "set_limits": [0, 4096, 1, 0],
                "setValue": 16,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture File Size (MB)",
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
            "check_framerate": {
                "widget_type": "ComboBox",
                "group": "Animation",
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
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
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
                "group": "Animation",
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
