# !/usr/bin/python
# coding=utf-8
"""Texture-phase export tasks (mirror of mayatk's): material cleanup, path
repair, the texture template conversion and the optimization pass, plus the
lightmap hints the GLB build reads.
"""

import contextlib
import os
from typing import Any, Dict, List

import pythontk as ptk

from blendertk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _TextureTasksMixin(_TaskDataMixin):
    """Texture-phase export tasks (mirror of mayatk's): material cleanup, path
    repair, the texture template conversion and the optimization pass, plus the lightmap hints the GLB build reads."""

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
        if count:
            self.record_kept_edit("merged duplicate materials")
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
            self.record_kept_edit("project-relative texture paths")
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
            self.record_kept_edit("rebound missing texture paths")
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
        if report["healed"]:
            self.record_kept_edit("re-pointed lightmap folders")
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
        export_path = self.export_path
        temp_staging = (
            bool(self.run.glb_only)
            or bool(self.run.fbx_media_selfcontained)
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

        **Non-destructive by default** (``run.texture_write_back`` unset -- the
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
        write_back = self.run.texture_write_back
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
        if write_back:
            self.record_kept_edit("textures converted in place")
        if write_back and self.run.relative_paths:
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

        The size ceiling (``run.texture_max_size``, a per-run mode stamped by
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
        drift. Already-optimal maps ship as-is, untouched — except, where the
        scene's own maps ship (staged, not GLB-only), a map in a container a
        plain re-encode can shrink (``ptk.MapOptimizer.RECOMPRESSIBLE_FORMATS``),
        which is re-encoded because "optimal" never judges compression; the
        copy ships only when it saves ``ptk.MapOptimizer.RECOMPRESS_MIN_SAVING``.
        The file half -- judge, claim every output name, encode in parallel,
        verify each write -- is ``ptk.MapOptimizer.stage_maps``, shared with
        mayatk; only the repoint below knows Blender.

        **Non-destructive by default** (``run.texture_write_back`` unset — the
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

        write_back = self.run.texture_write_back
        # The file half -- judge, claim, encode in parallel, verify -- is the
        # shared pass; the staging dir is resolved only once something is
        # pending, so a run with every map already optimal creates none.
        # Re-encode candidates only where the scene's own maps ship: a GLB-only
        # run's GLB pass re-encodes every map itself, and in write-back a
        # re-run would archive the re-encode over the true original.
        report = ptk.MapOptimizer.stage_maps(
            sources,
            lambda path: self._assess_optimization(path, tpl),
            output_profile=tpl,
            clamp=clamp,
            staging_dir=lambda: self._texture_staging_dir("texopt"),
            write_back=write_back,
            recompress=not (write_back or self.run.glb_only),
            pass_desc=pass_desc,
            logger=self.logger,
        )
        if not report["pending"]:
            return
        staging_dir, temp_staging = report["staging_dir"], report["temp_staging"]

        # Staged repoints are image_paths_scope entries under ONE ExitStack (a
        # temp staging dir's removal rides the same stack), handed to
        # stage_deferred_context so the write still sees the staged paths.
        scope = contextlib.ExitStack()
        if not write_back and temp_staging:
            scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
        repathed: set = set()  # images already scoped (LIFO restores the original)
        for record in report["results"]:
            if record["status"] != "optimized":
                continue  # the source ships (kept / failed / name collision)
            src, written = record["path"], record["written"]
            # Repoint the consuming images wherever the written file is not
            # the current target (always, when staging; on a normalized
            # filename, when writing back).
            if os.path.normcase(os.path.normpath(written)) == os.path.normcase(
                os.path.normpath(src)
            ):
                continue
            new_path = written.replace("\\", "/")
            for img in record["entry"]["images"]:
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

        if report["optimized"]:
            if write_back:
                self.record_kept_edit("texture files optimized in place")
            sizes = ptk.FileUtils.format_bytes_delta(
                report["bytes_before"], report["bytes_after"]
            )
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
                f"Optimized {report['optimized']} texture(s): {sizes}; {destination}."
            )
