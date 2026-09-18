# !/usr/bin/python
# coding=utf-8
"""The Scene Exporter's task/check manager -- mirror of mayatk's ``TaskManager``.

:class:`TaskManager` composes the phase mixins (scene, textures, animation,
checks) and the panel's definitions over :class:`pythontk.TaskFactory`, which
dispatches the ``task_*`` / ``check_*`` methods by name in the shared order
(``ptk.ExportProfile.TASK_ORDER``, checks hoisted by its
``CHECK_DEPENDENCIES``, both scoped to the names this class implements --
what it does not, :attr:`TaskManager.PARITY_GAPS` declares). The class itself
holds what spans a run: the :class:`pythontk.ExportRun` modes it is begun
with, the export set, and the post-write API (``create_glb``,
``write_scene_data_sidecar``) the exporter calls once the file is on disk.
"""

import os
from typing import Optional, Dict, Any, List

import pythontk as ptk
from pythontk import TaskFactory

# From this package:
from blendertk.env_utils.hierarchy_sync.hierarchy_baseline import HierarchyBaseline
from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar
from blendertk.env_utils.scene_exporter._task_animation import _AnimationTasksMixin
from blendertk.env_utils.scene_exporter._task_checks import _TaskChecksMixin
from blendertk.env_utils.scene_exporter._task_data import _NEEDS_HIERARCHY_MANAGER
from blendertk.env_utils.scene_exporter._task_scene import _SceneTasksMixin
from blendertk.env_utils.scene_exporter._task_textures import _TextureTasksMixin
from blendertk.env_utils.scene_exporter.task_definitions import _TaskDefinitionsMixin


@ptk.ExportProfile.scoped_tables
class TaskManager(
    TaskFactory,
    _SceneTasksMixin,
    _TextureTasksMixin,
    _AnimationTasksMixin,
    _TaskChecksMixin,
    _TaskDefinitionsMixin,
):
    """The export pipeline's tasks and checks, run in the shared order.

    ``TASK_ORDER`` and ``CHECK_DEPENDENCIES`` are ``ptk.ExportProfile``'s
    tables scoped to this class by the decorator, so the two DCC pipelines
    cannot disagree on order or on what a check reads; the names scoped away
    are :attr:`PARITY_GAPS`, pinned by the suite. A run is bracketed by
    :meth:`begin_run` (the modes and the per-run reset) and the exporter's
    ``run_deferred_restores`` (every mutation a task staged for the write,
    unwound LIFO).
    """

    #: Shared-table names this manager has no method for, with the reason --
    #: the parity gap to mayatk, declared rather than hidden. The suite pins
    #: this dict against ``ptk.ExportProfile.unimplemented``: porting one
    #: means deleting its entry, and a task mayatk adds shows up as an
    #: undeclared gap.
    PARITY_GAPS: Dict[str, str] = {
        "set_workspace": (
            "no Blender analogue: there is no project-directory switch, the "
            ".blend's folder is the workspace"
        ),
        "conform_shape_names": (
            "no Blender analogue: no transform/shape pair to conform, and "
            "mesh-data names never reach the FBX"
        ),
        "flatten_sheared_chains": (
            "no Blender analogue: an object's local matrix is loc/rot/scale, "
            "so a sheared chain cannot be authored"
        ),
        "check_mangled_names": (
            "no Blender analogue: the patterns are Maya import escapes "
            "(FBXASC###, __RZTMP) that Blender never writes"
        ),
        "check_sheared_local_transforms": (
            "no Blender analogue: see flatten_sheared_chains"
        ),
        "check_uv_snapshots": (
            "no Blender analogue: the _uv_snap_* backup sets are mayatk Auto "
            "Unwrap temporaries, and Blender's unwrap takes none"
        ),
        "check_default_materials": (
            "not yet ported: objects on no material / the default material. "
            "TODO(blender-parity)"
        ),
        "check_hierarchy_vs_existing_fbx": _NEEDS_HIERARCHY_MANAGER,
    }

    def __init__(self, logger):
        super().__init__(logger)
        self._objects = None
        self._cached_materials = None

    def run_tasks(self, tasks: Dict[str, Any]) -> bool:
        """Run *tasks*, first adopting the two modes derived from them.

        Read off the FULL dict here rather than in
        ``_execute_tasks_and_checks``: an override that resumes the tasks a
        failed check stopped hands that method a subset, from which
        ``optimize_keys_level`` would come back False (see
        ``ptk.ExportRun.with_tasks``). Mirror of mayatk's.
        """
        self.run = self.run.with_tasks(tasks)
        return super().run_tasks(tasks)

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Drop the cache derived from the export set.

        Nothing else: tasks assign this mid-run (a filter, the carrier
        fold-in, the staged curve proxies), and the per-run markers are
        :meth:`begin_run`'s to reset.
        """
        self._objects = value
        self._cached_materials = None

    # ------------------------------------------------------------------
    # Post-write API -- what the exporter calls once the file is on disk
    # (mirror of mayatk's; the exporter sets ``objects`` to the shipped set
    # first, so every read here describes what actually shipped)
    # ------------------------------------------------------------------

    def _sidecar_kwargs(self) -> dict:
        """Sidecar path-derivation kwargs: a versioned Output Filename routes
        through the base stem so every version of a series shares one
        manifest."""
        return {"base_stem": bool(self.run.versioned)}

    def _data_export_snapshot(self) -> dict:
        """Decoded copy of every ``data_export`` channel, as shipped in the FBX.

        Empty dict when the carrier is absent, empty, or not part of the
        export set -- the carrier is a hidden Empty, so it only ships when
        the ``export_data_node`` task folded it into the export set, and the
        record must only claim what actually shipped. Never raises -- the
        record must not break the export it records.
        """
        try:
            from blendertk.node_utils.data_nodes import DataNodes

            carrier = DataNodes.get_export_node(create=False)
            if carrier is None or carrier not in self._live_objects():
                return {}
            return DataNodes.dump(decode=True).get(DataNodes.EXPORT) or {}
        except Exception:
            self.logger.debug("data_export snapshot skipped.", exc_info=True)
            return {}

    def write_scene_data_sidecar(self, glb_path: Optional[str] = None) -> None:
        """Write the sidecar JSON recording what shipped in the export.

        Mirror of mayatk's: the manifest carries the exported hierarchy paths
        (the diff-check baseline) plus a snapshot of the ``data_export``
        carrier channels. The hierarchy section is maintained when a manifest
        already exists (the exporter-side hierarchy *check* is a declared
        :attr:`PARITY_GAPS` entry, so unlike mayatk there is no check-ran
        trigger); the data section is recorded whenever the carrier shipped
        content. A metadata-free export leaves no sidecar. Best-effort: the
        record must never break the export it records.

        With a GLB written, its ``lightmap_metadata`` is recorded as the GLB
        ships it (mirror of mayatk's): the GLB pass corrects that copy to the
        encoded map and the scalar restoring the bake range, while the scene's
        still names the pre-encode ``.exr`` at 1.0.

        Parameters:
            glb_path: The GLB this export wrote, if any.
        """
        export_path = self.export_path
        objects = self._live_objects()
        if not export_path or not objects:
            return
        try:
            paths = SceneDataSidecar.build_full_path_set(objects)

            # Adopt any on-disk baselines before rolling forward, so history
            # survives the upgrade (mirror of mayatk; no-ops once the .blend
            # carries a record of its own). There is no hierarchy CHECK here to
            # do it, so the writer is the only place it can happen.
            HierarchyBaseline.migrate_from_sidecar(os.path.dirname(export_path))

            # The BASELINE first, and unconditionally: it goes to the .blend,
            # not the sidecar, so it must not be skipped by the sidecar's own
            # "nothing to write" shortcut below (mirror of mayatk). blendertk
            # has no hierarchy CHECK yet (a declared PARITY_GAPS entry), so
            # nothing reads this yet -- but the record has to exist and roll
            # forward from the day the writer does, or the check lands with no
            # history behind it.
            import bpy

            if not HierarchyBaseline.write(paths):
                self.logger.warning(
                    "Could not record the hierarchy baseline on the .blend — the "
                    "diff baseline for the next export was NOT updated."
                )
            elif not bpy.data.filepath:
                self.logger.warning(
                    "Hierarchy baseline recorded, but the .blend is unsaved — save "
                    "it to keep the baseline for the next session."
                )

            sk = self._sidecar_kwargs()
            SceneDataSidecar.migrate_legacy(export_path, **sk)
            manifest_path = SceneDataSidecar.manifest_path_for(export_path, **sk)

            data = self._data_export_snapshot()
            key = ptk.MeshConvert.LIGHTMAP_METADATA_KEY
            if glb_path and key in data:
                try:
                    shipped = ptk.MeshConvert.read_glb_lightmap_manifest(glb_path)
                except (OSError, ValueError) as error:
                    self.logger.warning(
                        "Scene-data sidecar: the GLB's lightmap manifest could not "
                        f"be read ({error}); recording the scene's."
                    )
                    shipped = None
                if shipped is not None:
                    data = {**data, key: shipped}
            if not data and not os.path.exists(manifest_path):
                return

            if (
                SceneDataSidecar.write_manifest(export_path, paths, data=data, **sk)
                is None
            ):
                self.logger.warning(
                    "Could not write the scene-data sidecar — the metadata shipped "
                    "alongside this deliverable was NOT updated."
                )
        except Exception:
            self.logger.debug("scene-data sidecar write skipped.", exc_info=True)

    def _lightmap_search_dirs(self) -> List[str]:
        """Folders the GLB applier joins the manifest's basenames against
        (:meth:`LightmapBaker.search_dirs`, scoped to the export set; mirror of
        mayatk's ``TaskManager._lightmap_search_dirs``)."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker.search_dirs(self._live_objects() or None)

    def create_glb(
        self, fbx_path: Optional[str] = None, announce: bool = True
    ) -> Optional[str]:
        """Convert an exported FBX to a GLB through the shared build.

        Runs after the FBX has been written; ``perform_export`` invokes this
        explicitly rather than as part of the pre-export task pipeline
        (mirror of mayatk's ``TaskManager.create_glb``).

        The build is :class:`pythontk.GlbPipeline` -- the SAME chain the WebXR
        preview publishes through -- handed this run's dials: the scene sidecar
        built from the export set (:class:`~blendertk.env_utils.scene_state.SceneState`,
        the readers the preview shares), where the maps live NOW
        (:meth:`_lightmap_search_dirs`), the GLB's half of the panel's two
        texture dials (:meth:`_glb_texture_params`) and the Animation Clips
        choice ``apply_declared_takes`` recorded. A sidecar read failure
        degrades to a bare conversion rather than costing the deliverable; a
        failed conversion or texture pass fails it.

        Parameters:
            fbx_path: FBX to convert. Defaults to :attr:`export_path` (the
                FBX-alongside case). The GLB-only path passes the temp FBX so
                the ``.glb`` lands beside it (then gets moved into the output
                dir).
            announce: When True, log the resulting path. The GLB-only path
                sets this False and logs the final (moved) path itself.

        Returns:
            The created ``.glb`` path, or ``None`` if the build failed.
        """
        from blendertk.env_utils.scene_state import SceneState

        src = fbx_path or self.export_path
        objects = self._live_objects()
        sidecar = None
        if objects:
            sidecar = ptk.GlbPipeline.envelope(
                lambda: SceneState.read(objects),
                source=SceneState.source(),
                asset=os.path.basename(src),
                logger=self.logger,
            )
        try:
            built = ptk.GlbPipeline.build(
                src,
                sidecar=sidecar,
                # Where the maps are NOW: the manifest's recorded authoring
                # folder goes stale the moment the project is reorganised.
                lightmap_dirs=self._lightmap_search_dirs(),
                texture_params=self._glb_texture_params(),
                # GLB Key Tolerance: the deviation bound, or None for the
                # converter's per-frame keys.
                key_tolerance=self.run.glb_key_tolerance,
                # Which clips survive the rebuild: decided by the Animation
                # Clips row, "both" when that task never ran.
                clip_mode=self._clip_mode,
                progress=lambda message: self._report_progress(None, None, message),
                logger=self.logger,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # A destination held open is the likeliest failure (the build
            # REPLACES the .glb): say which process rather than read a locked
            # file as a broken conversion.
            reason = ptk.FileUtils.describe_lock(os.path.splitext(src)[0] + ".glb")
            if reason:
                self.logger.error(f"GLB build could not write its output: {reason}")
            else:
                self.logger.error(f"GLB build failed: {e}")
            return None

        glb_path = built["glb"]
        if announce:
            self.logger.success(f"GLB created: {glb_path}")
        return glb_path


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
