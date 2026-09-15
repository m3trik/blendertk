# !/usr/bin/python
# coding=utf-8
"""Per-run state and the scope helpers every Scene Exporter task and check
shares -- the base of the phase mixins (``_task_scene``, ``_task_textures``,
``_task_animation``, ``_task_checks``). Mirror of mayatk's.
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk

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
    """Per-run state and the scope helpers every task and check shares.

    The base of each phase mixin (``_task_scene``, ``_task_textures``,
    ``_task_animation``, ``_task_checks``) -- mirror of mayatk's: the run's
    modes, the markers one task leaves for a later one, the caches derived
    from the export set, and the texture-pass resolvers (container, size
    clamp, assessment) a task and its paired check judge through.
    """

    #: The modes of the run in flight -- adopted by :meth:`begin_run` from
    #: what ``perform_export`` parsed (``ptk.ExportRun.from_tasks``). A
    #: manager driven directly reads the defaults: no deliverable path, an
    #: FBX run, every pass at its off / staged setting.
    run: ptk.ExportRun = ptk.ExportRun()

    # Markers one task leaves for a later one (or for the post-write API),
    # declared with the value a fresh run has and reset by begin_run -- never
    # as a side effect of assigning ``objects``, which tasks do mid-run.
    #: The Animation Clips choice, read by ``create_glb`` after the write.
    _clip_mode: str = "both"
    #: ``export_data_node`` refreshed the carrier this run, so
    #: ``apply_declared_takes`` skips its own refresh.
    _data_node_refreshed: bool = False
    #: The frame spans claimed through ``_require_range_coverage``, which
    #: ``set_bake_animation_range`` widens to cover.
    _required_range_coverage: Optional[Tuple[float, float]] = None
    #: The delivery-only-container note's once-per-run throttle.
    _delivery_only_clamp_said: bool = False
    #: ``smart_bake``'s session manifest, undone by the restore the task stages.
    _bake_session_id: Optional[str] = None
    #: The NLA / data-level-animation advisory fires once per run, not once
    #: per check (``_warn_unseen_animation``).
    _unseen_anim_warned: bool = False
    #: Materials assigned to the export set; None = not computed this run.
    _cached_materials: Optional[List] = None

    @property
    def export_path(self) -> str:
        """The deliverable this run writes (``run.export_path``).

        Empty for a manager driven directly, which every reader treats as
        "nothing durable to stage beside".
        """
        return self.run.export_path

    def begin_run(self, run: ptk.ExportRun) -> None:
        """Adopt *run*'s modes and reset every per-run marker -- the ONE reset.

        ``perform_export`` calls this after resolving the output path and
        before seeding the export set, so a run with no task checked still
        starts clean: left standing, a previous run's Animation Clips choice
        would convert this run's GLB, and its claimed frame spans would widen
        this run's bake range. Mirror of mayatk's.
        """
        self.run = run
        self._clip_mode = "both"
        self._data_node_refreshed = False
        self._required_range_coverage = None
        self._delivery_only_clamp_said = False
        self._bake_session_id = None
        self._unseen_anim_warned = False
        self._cached_materials = None

    def _live_objects(self) -> List:
        """``self.objects`` re-resolved to the objects that still exist.

        Tasks can remove members of the export set from the file (a bake that
        replaces a driver Empty, a producer that rebuilds its carrier), and a
        dead datablock reference raises ``ReferenceError`` on its first
        attribute read -- from whichever bulk consumer touches it first, with
        a message that names no object. Mutating tasks refresh ``self.objects``
        themselves; this is the read-side guard for everything downstream
        (mirror of mayatk's ``_live_objects``).
        """
        live = []
        for obj in self.objects or []:
            try:
                obj.name  # a removed datablock raises here
            except ReferenceError:
                continue
            live.append(obj)
        return live

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

        Binds the per-run ``run.texture_file_type`` mode (the Texture File Type
        combo, stamped by ``perform_export`` — never a dispatched task) to the
        shared rule: :meth:`pythontk.OutputTemplates.resolve_selection` owns
        "a concrete container outranks the profile's template", so naming a
        file type here writes every map as that, while the template still
        supplies the budget and bit depth. Falsy (Original) defers to
        :meth:`_scene_safe_output_type`.
        """
        _, chosen = ptk.OutputTemplates.resolve_selection(
            template, self.run.texture_file_type
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
                if not self._delivery_only_clamp_said:
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

    #: Texture File Type token for KTX2 PLUS a core-readable PNG/JPEG twin of
    #: every map (``optimize_glb_textures(ktx2_fallback=True)``). The export
    #: parses it into the ``ktx2`` container and the per-run ``run.ktx2_fallback``
    #: flag, so no other consumer ever compares it. Mirrors mayatk.
    KTX2_WITH_FALLBACK = ptk.ExportRun.KTX2_WITH_FALLBACK

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
          interchange ones. ``KTX2 + PNG/JPEG`` is the KTX2 container plus
          ``ktx2_fallback`` (``run.ktx2_fallback``): a core-readable copy of each
          map beside its KTX2, for a GLB that must also open in Blender, Unreal
          or stock Unity. Plain ``KTX2`` takes the policy's KTX2 alone.
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
        file_type = (self.run.texture_file_type or "").lower().lstrip(".")
        optimize = bool(self.run.optimize_textures)

        carrier = file_type if file_type in self.GLB_CARRIER_FORMATS else ""
        if file_type and not carrier:
            self.logger.info(
                f"GLB textures: {file_type.upper()} is not a container glTF can "
                f"embed — the GLB carries "
                f"{ptk.MeshConvert.WEB_DELIVERY_FORMAT} (the scene's own maps "
                f"still use {file_type.upper()})."
            )

        # ``or None`` on every part: an unset dial is "unspecified", which the
        # shared resolver answers with the policy, NOT a falsy value it would
        # read as a decision (0 there means "keep every pixel").
        return ptk.MeshConvert.web_delivery_texture_params(
            image_format=self._glb_format_id(carrier) if carrier else None,
            max_size=(self._glb_max_size() if optimize else 0) or None,
            ktx2_fallback=bool(self.run.ktx2_fallback) or None,
            # The two GLB-only dials ride the same policy call; an
            # unset dial is None so the policy answers, as above.
            secondary_max_size=self.run.secondary_max_size or None,
            uastc_rdo=self.run.uastc_rdo or None,
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
        template = self.run.texture_template
        clamp = self._texture_size_clamp(template)
        if clamp.get("enforce_budget"):
            return int(ptk.OutputTemplates.budget(template).max_size or 0)
        return int(clamp.get("max_size") or 0)

    #: ``run.texture_max_size`` sentinel: clamp to the active template's own
    #: :class:`~pythontk.DeliveryBudget` (``enforce_budget``) rather than to a
    #: pixel ceiling. Aliases the shared resolver's own sentinel so the combo
    #: row, the exporter and the optimizer cannot drift apart on its value —
    #: and so this cannot drift from mayatk's twin either.
    TEXTURE_MAX_SIZE_TEMPLATE = ptk.MapOptimizer.SIZE_CLAMP_TEMPLATE

    def _texture_size_clamp(self, template) -> Dict[str, Any]:
        """The resize rule the optimization pass applies under *template*
        (mirror of mayatk's).

        Binds the per-run ``run.texture_max_size`` mode (the Optimize Textures
        combo's size half, stamped by ``perform_export`` — never a dispatched
        task) to the shared resolver, which owns the rule: see
        :meth:`pythontk.MapOptimizer.resolve_size_clamp` for the modes and
        why the budget's POT flag is deliberately not adopted.

        Returns:
            dict of keyword arguments for ``MapOptimizer.assess`` /
            ``optimize_map``. Empty when no clamp applies.
        """
        return ptk.MapOptimizer.resolve_size_clamp(
            self.run.texture_max_size, template, logger=self.logger
        )

    def _texture_size_clamp_desc(self, template) -> str:
        """Human-readable form of :meth:`_texture_size_clamp` for log lines."""
        return ptk.MapOptimizer.describe_size_clamp(
            self.run.texture_max_size, template, logger=self.logger
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
            plan itself does not model), ``warnings`` (list[str]),
            ``output_type``, and ``predicted_name`` (the basename
            ``optimize_map`` would write, so a collision can be decided before
            any file is written).
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
            "predicted_name": os.path.basename(result["predicted"].get("path") or path),
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

        objects = self._live_objects()
        if not objects:
            return False
        return any(fc.keyframe_points for fc in AnimUtils.get_fcurves(objects))

    def _get_all_materials(self) -> List:
        """Materials assigned to ``self.objects`` (cached; invalidated on ``objects`` reassign)."""
        from blendertk.mat_utils._mat_utils import MatUtils

        if self._cached_materials is None:
            self._cached_materials = MatUtils.get_mats(self._live_objects())
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
