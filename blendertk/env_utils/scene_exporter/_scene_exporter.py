# !/usr/bin/python
# coding=utf-8
"""Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.

Batch-exports scene objects to FBX (optionally + GLB) through a configurable task/check
pipeline. Mirror of mayatk's ``SceneExporter`` at the name+behavior level; see
``task_manager.py`` for which of mayatk's tasks/checks are ported vs. disabled placeholders.

FBX export-option presets
--------------------------
Maya's ``cmb000`` picks a ``*.fbxexportpreset`` file from a directory and MEL
``FBXLoadExportPresetFile`` loads it into Maya's own persistent FBX-exporter globals; its
option-box buttons add/delete/browse-to/edit those files. Blender's ``export_scene.fbx``
operator takes its options as direct ``bpy.ops`` keyword args (see ``fbx_utils.py``), so the
Blender-native counterpart of a "preset" is a *named dict of those kwargs*.

Two designs were weighed for storing that dict:

* **Blender's native operator-preset system** (``bl_options={'PRESET'}`` -> the generic
  ``wm.operator_preset_add`` / ``bl_operators.presets.AddPresetBase`` machinery behind the "+"
  button in Blender's own File > Export > FBX dialog). Confirmed live (headless probe) that
  ``export_scene.fbx`` does carry ``'PRESET'`` in its ``bl_options`` and that
  ``bl_operators.presets.AddPresetBase`` is importable -- but that machinery only ever reads
  from / writes to ``context.active_operator``, i.e. a live, *interactively invoked* operator
  instance sitting in its own redo panel. There is no supported way to drive "add" or "edit"
  from an unrelated custom panel button -- let alone headlessly, which the preset test in
  ``test_scene_exporter.py`` requires -- without actually popping Blender's own export
  file-browser: a materially worse UX than Maya's settings-only editor dialog, and untestable
  outside an interactive session.
* **A named JSON store of the kwargs dict** via ``pythontk.PresetStore`` -- Qt-free, works
  headlessly, and is the SAME built-in+user two-tier mechanism already used for this exact
  shape of problem elsewhere in blendertk (``edit_utils.macros``, ``edit_utils.curtain``,
  ``display_utils.color_id``). **Chosen**: it is a straight 1:1 fit for
  ``_DEFAULT_FBX_OPTIONS`` (no exec/attribute-capture indirection needed to get a plain dict
  back out), reuses established ecosystem infra rather than inventing a new one, and is
  trivially unit-testable.

:meth:`SceneExporter.load_fbx_export_preset` resolves a preset *name* (not a file path, unlike
mayatk) via :meth:`SceneExporter._preset_store`; :meth:`SceneExporter.verify_fbx_preset` returns
the resulting kwargs dict that ``perform_export`` forwards to ``export_selection_fbx``.
``scene_exporter_slots.py`` wires the Add / Delete / Open Directory / Edit buttons around this
engine API -- see that module's docstring.

``import bpy`` is deferred into call bodies (no import side effects).
"""

import os
import re
import json
import shutil
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk

from blendertk.env_utils.scene_exporter.task_manager import TaskManager
from blendertk.env_utils.usd import UsdUtils
from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar

# The engine's game-asset baseline (also shipped as the "game_asset" built-in preset -- see
# presets/game_asset.json): embedded textures so nothing ships missing; baked animation since
# there's no bake-pipeline task in this cut to have pre-baked it. Used whenever no preset is
# loaded, and as the seed a fresh "Add Preset" saves from when nothing is selected.
#
# NOT the "default" built-in preset: that one is Blender's OWN export_scene.fbx defaults,
# generated from the operator's live RNA (presets/default.json, drift-guarded by
# test_scene_exporter.py) -- a preset named "default" has to mean what the user gets from
# File > Export > FBX, not this tool's opinion. Select "game_asset" for these values.
#
# ``use_custom_props`` + ``object_types`` are what let the shared ``data_export`` Empty's
# metadata channels (``lightmap_metadata``, ...) ride into the FBX as user properties --
# Blender defaults custom-property export OFF, so it must be overridden here or the
# ``export_data_node`` task ships an FBX with no metadata. ``object_types`` is pinned
# EXPLICITLY (not inherited from ``fbx_utils._EXPORT_DEFAULTS``, which also carries
# ``EMPTY``): the carrier Empty is a hard requirement of this task, not a default worth
# silently tracking. Stored as a list (JSON presets can't hold a set); ``FbxUtils.export``
# coerces it.
#
# ``bake_anim_use_nla_strips`` / ``bake_anim_use_all_actions`` are pinned OFF here AND
# enforced for every resolved option set by ``_force_scene_range_take`` (no preset may
# separate "export animation" from "export it in one coherent take" -- Blender's own
# defaults, which the "default" preset now carries verbatim, turn BOTH on) for
# a reason that is invisible until you read ``export_fbx_bin.fbx_animations``:
# with EITHER left at Blender's default (both are ``True``) the exporter writes
# one FBX take *per action*, each baked over that action's own frame range and
# **start-zeroed** (``fbx_animations_do(..., start_zero=True)``), and — the line
# that makes it a correctness bug rather than a preference —
#
#     # Global (containing everything) animstack, only if not exporting NLA
#     # strips and/or all actions.
#     if not ...bake_anim_use_nla_strips and not ...bake_anim_use_all_actions:
#         add_anim(..., scene.frame_start, scene.frame_end, False)
#
# skips the scene-range take entirely. So the shipped FBX had NO take covering
# the scene range: ``set_bake_animation_range`` could not affect the file at all,
# and independently-authored curves (a mesh's action and EmissiveGroups' staged
# weight-curve proxies) landed in SEPARATE takes, each rebased to frame 1 —
# i.e. silently time-misaligned with each other in the engine. One scene-range
# take is also what mayatk's FBX path produces (FBXExportBakeComplexStart/End),
# so this is the parity-correct setting; per-shot takes are the opt-in job of
# the ``apply_declared_takes`` task, not an accident of the defaults.
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "mesh_smooth_type": "FACE",
    "use_tspace": True,
    "embed_textures": True,
    "path_mode": "COPY",
    "bake_anim": True,
    "bake_anim_use_nla_strips": False,
    "bake_anim_use_all_actions": False,
    "use_custom_props": True,
    "object_types": ["EMPTY", "ARMATURE", "MESH"],
}


class SceneExporter(ptk.LoggingMixin):
    # PresetStore identity for FBX export-option presets (see module docstring).
    # NOT "scene_exporter": that name is taken by the panel's uitk window-template
    # store (PresetManager preset_dir="blendertk/scene_exporter" resolves under the
    # same user_config_root), and sharing the directory made the FBX combo list
    # window templates, let a same-named template shadow a shipped FBX preset, and
    # crossed the two stores' ``.active`` sidecars.
    PRESET_NAME = "fbx_presets"
    PRESET_PACKAGE = "blendertk"

    #: One-shot guard for :meth:`_migrate_legacy_preset_dir` (the legacy scan is
    #: pure filesystem work; once per process is enough).
    _legacy_fbx_presets_migrated = False

    def __init__(
        self, log_level: str = "WARNING", log_handler: Optional[object] = None
    ):
        """ """
        self._setup_logging(log_level, log_handler)

        self.task_manager = TaskManager(self.logger)
        #: Checks a run failed but the user chose to override at the failure
        #: point (see confirm_check_override). Re-stamped by every
        #: ``perform_export``, so the success banner reports the deliverable
        #: as shipped-with-failures rather than claiming a clean pass.
        self._overridden_checks: List[str] = []
        #: The ``(current, total, message)`` stream of the run in flight and
        #: its bookkeeping -- see :meth:`_progress_begin`; cleared when
        #: ``perform_export`` returns.
        self._progress_callback: Optional[Callable] = None
        self._progress_current = 0
        self._progress_total = 0
        self._progress_base = 0
        self._progress_open = False
        self._progress_cancellable = False
        self._progress_cancel_ignored = False
        #: Whether the last run stopped on a cancel (vs. any other abort) --
        #: what the panel's footer reports after the run.
        self._export_cancelled = False
        self.logger.debug("Task manager initialized in SceneExporter.")

    def _setup_logging(
        self, log_level: Optional[str], log_handler: Optional[object]
    ) -> None:
        """Apply a log level and/or handler; ``None`` leaves the level as it is.

        ``perform_export`` calls this with its own ``log_level`` argument, so
        a level there would silently override the one the constructor set --
        ``SceneExporter(log_level="DEBUG").perform_export(...)`` used to run
        at WARNING and drop every per-task line the caller had asked for.
        """
        if log_level is not None:
            self.logger.setLevel(log_level)
        if log_handler:
            self.logger.addHandler(log_handler)

    def _setup_file_logging(self) -> None:
        """Setup file logging."""
        log_file_path = self.generate_log_file_path(self.export_path)
        self.logger.info(f"Generating log file path: {log_file_path}")
        self.setup_file_logging(log_file_path)

    def _initialize_objects(self, objects: Optional[Union[List, Callable]]) -> List:
        """Initialize objects for the scene."""
        import blendertk as btk

        if objects is None:
            self.logger.debug(
                "No objects provided. Defaulting to the current selection."
            )
            objects = btk.selected_objects()
        elif callable(objects):
            self.logger.debug(
                "Callable provided for objects. Resolving objects dynamically."
            )
            objects = objects()
        else:
            self.logger.debug("Static list of objects provided.")

        objs = list(objects) if objects else []

        if hasattr(self, "task_manager"):
            self.task_manager.objects = objs

        self.logger.info(f"{len(objs)} object(s) prepared for export.")
        return objs

    def confirm(self, question: str) -> bool:
        """Yes/no consent for an export-time side effect (a tool download).

        The seam the panel overrides with a dialog. Headless it asks on the
        console when there is one -- an interactive background-Blender user
        gets a ``[y/N]`` -- and answers no otherwise: nobody is there to
        consent, and the caller's own message names the manual install.

        Parameters:
            question: Plain-text question; newlines allowed.
        """
        return bool(ptk.AppInstaller.consent(True, question))

    def confirm_check_override(self) -> bool:
        """Ask, at the failure point, whether to export despite failed checks.

        The tasks have already run and the scene is still staged, so this is
        the ONE moment at which overriding costs nothing. Arming the panel's
        Override Checks toggle *after* a failed run instead means a second
        export from scratch: every task re-runs (re-bake, re-optimize the
        textures, re-rewrite the paths) on a scene the first run already
        mutated. Answering yes here continues the SAME run straight to the
        write.

        Consent only, never an automatic pass: it routes through
        :meth:`confirm`, whose default answers no when nobody is there to ask
        (a batch run still aborts on a failed check).
        """
        failed = list(getattr(self.task_manager, "_last_failed_checks", ()) or ())
        listed = ", ".join(failed[:10]) + (" \u2026" if len(failed) > 10 else "")
        headline = (
            f"{len(failed)} validation check(s) failed: {listed}."
            if failed
            else "A validation check failed."
        )
        return self.confirm(
            f"{headline}\n\n"
            "The export tasks have already run, so overriding now finishes THIS "
            "run instead of re-running the whole pipeline over an already-"
            "mutated scene.\n\n"
            "Override the checks and export anyway?"
        )

    def _resume_skipped_tasks(self, tasks: Dict[str, Any]) -> None:
        """Run the tasks the failed check aborted, so an override still ships a
        fully processed file.

        The runner stops dispatching tasks at the first failed check -- every
        one below it in the schedule is work an aborted write would throw
        away. Overriding turns that write back on, so those tasks are no
        longer wasted and must run before it: without this an overridden
        export silently shipped a file that skipped, say, the texture
        conversion the user asked for.

        Only the skipped names are re-dispatched; the tasks above the failed
        check already ran, and re-running them would repeat their mutation.
        Safe because every ``set_`` task here registers a deferred restore
        rather than a ``revert_`` pair, so the first pass's staged state is
        still in effect (see ``TaskFactory._get_revert_method``).
        """
        tm = self.task_manager
        skipped = [
            n for n in (getattr(tm, "_last_skipped_tasks", ()) or ()) if n in tasks
        ]
        if not skipped:
            return
        self.logger.info(
            f"Resuming {len(skipped)} task(s) the failed check had stopped: "
            f"{', '.join(skipped)}."
        )
        # The second pass re-stamps the run counters the success banner reads.
        # The first pass already counted every REQUESTED task, so its numbers
        # are the ones that describe the run; keep them.
        counts = (
            getattr(tm, "_last_task_count", 0),
            getattr(tm, "_last_check_count", 0),
        )
        # The first pass closed its progress stream with every entry done,
        # these included; rewind so the resumed entries advance to, never
        # past, that mark.
        self._progress_base = max(0, self._progress_current - len(skipped))
        try:
            tm.run_tasks({name: tasks[name] for name in skipped})
        finally:
            tm._last_task_count, tm._last_check_count = counts

    # ------------------------------------------------------------------
    # Progress -- one (current, total, message) stream for the whole run
    # ------------------------------------------------------------------

    def _progress_begin(
        self, callback: Optional[Callable], tasks: Dict[str, Any], phases: int
    ) -> None:
        """Arm the run's progress stream (see ``perform_export``).

        ``current`` counts finished steps: every pipeline entry that will
        dispatch is one (the task manager reports them through its
        ``progress_callback``), and each of the *phases* after the pipeline
        -- the write, a GLB conversion, the sidecar, ... -- is one more.
        """
        self._progress_callback = callback
        self._progress_total = self.task_manager._dispatchable_count(tasks) + phases
        self._progress_current = 0
        self._progress_base = 0
        self._progress_open = False
        self._progress_cancellable = True
        self._progress_cancel_ignored = False
        self._export_cancelled = False
        self.task_manager.progress_callback = self._on_pipeline_progress

    def _progress_end(self) -> None:
        """Disarm the stream; a later run of the task manager reports nothing."""
        self.task_manager.progress_callback = None
        self._progress_callback = None

    def _emit_progress(self, message: Optional[str]) -> bool:
        """Report the current position; False when the caller asked to stop.

        A ``False`` from the callback is honoured only while nothing has been
        written. Once the write starts the deliverable is finished regardless
        -- a GLB abandoned between its conversion and its texture pass is a
        file that looks complete and is not -- and the request is reported
        once instead. A callback that raises is a feedback bug: logged, never
        allowed to fail the export.
        """
        callback = self._progress_callback
        if callback is None:
            return True
        try:
            keep_going = callback(self._progress_current, self._progress_total, message)
        except ptk.OperationCancelled:
            raise
        except Exception as e:  # noqa: BLE001 -- feedback never fails an export
            self.logger.debug(f"Progress callback failed: {e}")
            return True
        if keep_going is not False:
            return True
        if self._progress_cancellable:
            return False
        if not self._progress_cancel_ignored:
            self._progress_cancel_ignored = True
            self.logger.warning(
                "Cancel requested after the write began — finishing the "
                "deliverable rather than leaving it half-written."
            )
        return True

    def _on_pipeline_progress(self, current, total, message) -> bool:
        """The task manager's hook: its entry index rides on the run's base.

        ``(None, None, text)`` is a text-only tick and leaves the count alone.
        """
        if current is not None:
            self._progress_current = self._progress_base + int(current)
            self._progress_open = False
        return self._emit_progress(message)

    def _progress_step(self, message: str) -> None:
        """Start a post-pipeline phase; the one before it is thereby done."""
        if self._progress_open:
            self._progress_current += 1
        self._progress_open = True
        if not self._emit_progress(message):
            raise ptk.OperationCancelled(f"cancelled before {message}")

    def _progress_note(self, message: str) -> None:
        """Narrate inside a phase without moving the count."""
        if not self._emit_progress(message):
            raise ptk.OperationCancelled(f"cancelled before {message}")

    def _progress_finish(self, message: str) -> None:
        """The last tick, snapped to the total (skipped checks leave a gap)."""
        self._progress_current = self._progress_total
        self._progress_open = False
        self._emit_progress(message)

    def perform_export(
        self,
        export_dir: str,
        objects: Optional[Union[List, Callable]] = None,
        preset_name: Optional[str] = None,
        output_name: Optional[str] = None,
        export_visible: bool = True,
        create_log_file: bool = False,
        timestamp: bool = False,
        name_regex: Optional[str] = None,
        log_level: Optional[str] = None,
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        tasks: Optional[Dict[str, Any]] = None,
        usd_options: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None,
    ) -> bool:
        """Perform the export operation, including initialization and task management.

        Returns True only when the deliverable was written -- every abort
        (no export dir, no objects, a failed check, a declined encoder
        install, a cancel) returns False. The panel's export button reads
        this to disarm its Override Checks toggle.

        *progress_callback* ``(current, total, message)`` receives ONE stream
        for the whole run (mirror of mayatk): the task manager's per-entry
        ticks and the post-pipeline phases (write, GLB, sidecar) share a
        count, so a determinate bar can be driven from the first tick; uitk's
        ``sb.progress_adapter(update)`` is the panel's adapter. An explicit
        ``False`` cancels the run before its next step: nothing is written,
        and the tasks that already ran stay applied (as after a failed
        check). Once the write has begun a ``False`` is reported and ignored.
        """
        import bpy

        # First, so a caller's level/handler sees every message of this run,
        # the early aborts below included.
        self._setup_logging(log_level, log_handler)
        start_time = time.time()
        self.logger.info("Starting export process ...")

        # Default to the saved .blend's directory when none is given.
        if not export_dir:
            if bpy.data.filepath:
                export_dir = os.path.dirname(bpy.data.filepath)
                self.logger.info(
                    f"No export directory given; exporting alongside the .blend "
                    f"file: {export_dir}"
                )
            else:
                self.logger.error(
                    "Export directory not set and the file is unsaved — save "
                    "the file or specify an output directory."
                )
                return False

        self.export_dir = os.path.abspath(os.path.expandvars(export_dir))

        if not os.path.isdir(self.export_dir):
            self.logger.error(f"Export directory does not exist: {self.export_dir}")
            return False

        self.preset_name = preset_name
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        tasks = dict(tasks) if tasks else {}
        version_format = tasks.pop("version", "") or ""
        # Non-empty => sidecar paths route through the version base-stem so all
        # versions of a series share one scene-data sidecar (mirror of mayatk's
        # ``task_manager._version_format`` flag).
        self._version_format = version_format
        output_format = (tasks.pop("output_format", "") or "").lower()
        if not output_format:
            output_format = "fbx"
        create_glb_enabled = output_format in ("glb", "fbx_glb")
        glb_only = output_format == "glb"
        # "usd": the deliverable is a USD layer (mirror of mayatk). Same pipeline
        # up to the write; the FBX-only knobs (preset, takes, GLB) are reported
        # inert rather than silently ignored.
        usd = output_format == "usd"
        self._usd_format = usd
        self._usd_options = dict(usd_options or {})
        if usd:
            for key in ("apply_declared_takes", "set_bake_animation_range"):
                if tasks.get(key):
                    self.logger.warning(
                        f"Task '{key}' shapes the FBX take/animation range only; "
                        "a USD export samples the frames that carry motion instead."
                    )
            if self.preset_name:
                self.logger.warning(
                    "The FBX export preset does not apply to a USD export "
                    f"(ignored: {self.preset_name})."
                )

        # Texture File Type: ONE container dial for every texture the export
        # ships — the scene maps the optimization pass writes AND a GLB's
        # embedded copies (each destination clamps what it cannot carry; see
        # TaskManager._resolved_output_type / _glb_texture_params). Parsed here
        # so the KTX2 gate can fail BEFORE any scene work, and stamped on the
        # task manager (mirror of mayatk).
        #
        # ``glb_texture_format`` is the legacy key this replaced (it drove the
        # GLB alone, beside a redundant "Optimize GLB Textures" flag that the
        # general Optimize Textures now covers); an older template keeps
        # working, with the new key winning when both are present.
        texture_file_type = str(tasks.pop("texture_file_type", "") or "").lower()
        legacy_glb_format = str(tasks.pop("glb_texture_format", "") or "").lower()
        tasks.pop("glb_optimize_textures", None)  # redundant: see Optimize Textures
        if not texture_file_type and legacy_glb_format:
            texture_file_type = legacy_glb_format
            self.logger.debug(
                f"Legacy 'glb_texture_format' {legacy_glb_format!r} read as "
                "'texture_file_type'."
            )
        texture_file_type = texture_file_type.lstrip(".") or None
        known = set(TaskManager._texture_file_type_options.values()) - {None, ""}
        if texture_file_type and texture_file_type not in known:
            # A hand-edited template / headless caller can send anything; an
            # unknown value discovered here is a config error and aborts
            # loudly — discovered at encode time it would fail per-image and
            # ship an effectively-unencoded texture set behind warning noise.
            self.logger.error(
                f"Export aborted: unknown texture_file_type "
                f"{texture_file_type!r} (expected one of "
                f"{', '.join(sorted(known))}, or empty for Original)."
            )
            return False
        if texture_file_type == "ktx2":
            if not create_glb_enabled:
                # KTX2 is a delivery-only container: no scene image or FBX
                # importer reads it, so with no GLB to carry it the choice has
                # nowhere to land. Inert, not an error.
                self.logger.info(
                    "Texture File Type 'KTX2' ignored: it can only ship inside "
                    "a GLB, and the output format produces none."
                )
                texture_file_type = None
            else:
                # Encoder presence is ENVIRONMENT state, so this gate is
                # unconditional (never a user-toggleable check row) and runs
                # before the first scene mutation — a missing toktx is settled
                # in second zero, not after N-1 objects already exported.
                # Missing = offer the managed KTX-Software install through
                # :meth:`confirm` (the panel's dialog; a console [y/N]
                # headless) and carry on when accepted; a decline or a failed
                # install aborts with the install URL. Abort idiom, not a
                # raise: the panel's export button reads the return value and
                # the log.
                try:
                    if not ptk.ImgUtils.ktx2_available():
                        self.logger.info(
                            "KTX2 delivery needs KTX-Software's toktx, which is "
                            "not installed: offering the managed install."
                        )
                    installed = ptk.ImgUtils.ensure_ktx2_encoder(prompt=self.confirm)
                except FileNotFoundError as e:
                    self.logger.error(f"Export aborted: {e}")
                    return False
                if installed:
                    self.logger.info(f"Installed KTX-Software (toktx): {installed}")
        self.task_manager._texture_file_type = texture_file_type

        # Texture Output write-back flag: a mode read by convert_textures and
        # optimize_textures, never a dispatched task — popped here (mirror of
        # mayatk) and stamped on the task manager.
        # Legacy key (mirror of mayatk): presets saved before the rename carry
        # ``optimize_textures_write_back``. Left unmapped it survives the pop,
        # reaches the task dispatch as an unknown task, and the run silently
        # falls back to Export Copies -- losing the user's saved setting with
        # only a log line.
        _write_back = tasks.pop("texture_write_back", None)
        if _write_back is None:
            _write_back = tasks.pop("optimize_textures_write_back", False)
        else:
            tasks.pop("optimize_textures_write_back", None)  # new key wins
        self.task_manager._texture_write_back = bool(_write_back)
        # The optimization pass's size dial (OFF / a pixel ceiling / the
        # template-budget sentinel), read by optimize_textures and its paired
        # check through _texture_size_clamp — a mode like the write-back flag,
        # never a dispatched task. In the panel it rides the Optimize Textures
        # combo (b000 decomposes the choice into this key); headless callers
        # pass it explicitly. Falsy = OFF. Mirrors mayatk.
        self.task_manager._texture_max_size = tasks.pop("texture_max_size", None)
        # What the texture pass was asked for, read (not popped — they are real
        # tasks) so the GLB half can resolve the same two dials after the
        # pipeline has run (``TaskManager._glb_texture_params``). Stamped HERE
        # with every other per-run mode rather than inside
        # ``_execute_tasks_and_checks``: ``run_tasks`` returns early on an empty
        # task dict, so a run with nothing checked would otherwise leave the
        # PREVIOUS run's values standing and re-encode the GLB behind the user.
        optimize_textures = tasks.get("optimize_textures")
        self.task_manager._optimize_textures_enabled = bool(optimize_textures)
        template = tasks.get("convert_textures")
        self.task_manager._texture_template = (
            template
            if isinstance(template, str)
            else (optimize_textures if isinstance(optimize_textures, str) else None)
        )

        self.export_path = self.generate_export_path(
            version_format=version_format, extension=".usd" if usd else ".fbx"
        )
        self.logger.debug(f"Generated export path: {self.export_path}")
        # Texture-budget staging inputs (mirror of mayatk's stamps): the
        # optimize_textures task keys its staging policy off whether the
        # deliverable carries its own texture copies, and needs the export
        # path to place durable staging beside the deliverable. Stamping the
        # path also lets check_path_length measure it, as the Maya twin does.
        self.task_manager.export_path = self.export_path
        self.task_manager._glb_only = glb_only

        if self.create_log_file:
            self._setup_file_logging()

        initialized_objs = self._initialize_objects(objects)
        if not initialized_objs:
            self.logger.error("Export aborted: No objects available for export.")
            return False

        # Resolve the FBX export kwargs for this run: the named preset merged over the
        # built-in defaults, or the defaults alone when no preset is selected. Called
        # unconditionally so a prior run's loaded preset never leaks into one with none picked.
        self.load_fbx_export_preset(self.preset_name)

        # Whether the FBX deliverable carries its own texture copies —
        # ``embed_textures`` packs them inside the file; ``path_mode COPY``
        # makes the exporter copy the (possibly staged) sources beside it.
        # Either way nothing references staged files after the write, so the
        # optimize_textures task may stage into a temp dir and clean up.
        fbx_options = self._resolved_fbx_options()
        self.task_manager._fbx_media_selfcontained = (
            bool(fbx_options.get("embed_textures"))
            or str(fbx_options.get("path_mode", "")).upper() == "COPY"
        )

        # Everything from here on can stage export-transient state (scene units,
        # the bake frame range, EmissiveGroups' keyed-weight curve proxies) that
        # must survive the FBX write and be undone right after it. The outer
        # ``finally`` is what guarantees "right after it" on EVERY exit —
        # a failed check, an aborted task, an empty export set, or a raising
        # write — so a bad run can never leave staged state in the user's scene.
        self._overridden_checks = []  # per-run; see the attribute's __init__ note
        # Progress (mirror of mayatk): the pipeline's entries, then the write,
        # a GLB conversion and the sidecar -- one count for the run.
        self._progress_begin(
            progress_callback, tasks, phases=2 + int(create_glb_enabled)
        )
        try:
            self._progress_note("Preparing export…")
            if tasks:
                checks_passed = self.task_manager.run_tasks(tasks)
                if not checks_passed:
                    # Offer the escape hatch HERE, while the staged scene the
                    # write needs is still standing, rather than leaving the
                    # user to arm Override Checks and pay for the whole
                    # pipeline a second time (see confirm_check_override).
                    if self.confirm_check_override():
                        self._overridden_checks = list(
                            getattr(self.task_manager, "_last_failed_checks", ()) or ()
                        )
                        self.logger.warning(
                            "Checks overridden — writing the file despite "
                            f"{len(self._overridden_checks)} failed check(s): "
                            f"{', '.join(self._overridden_checks)}."
                        )
                        self._resume_skipped_tasks(tasks)
                    else:
                        # Checks run AFTER tasks, and tasks mutate the scene with
                        # no automatic rollback — a blocked export must say so
                        # instead of leaving the mutation silent. (The smart_bake
                        # session IS restored, in the finally below.)
                        self.logger.warning(
                            "Export blocked by failed checks, but export tasks "
                            "already ran — task edits (material cleanup, key "
                            "snapping/tying, texture path rewrites, …) remain in "
                            "the scene. Undo or revert if that is not what you want."
                        )
                        return False

            if export_visible:
                # "visible"/"all": the task pipeline's object set is authoritative.
                export_objects = list(self.task_manager.objects or [])
            else:
                # "selected": export the resolved selection captured at init time (already
                # filtered by the caller's ``objects_to_export()``, e.g. excluding the
                # data_internal carrier), then fold in any objects the task pipeline added to
                # the export set — otherwise they'd silently never ship. Re-querying the live
                # selection here instead would bypass that filtering and re-admit anything it
                # deliberately excluded by name.
                current = set(initialized_objs)
                extras = [
                    o for o in (self.task_manager.objects or []) if o not in current
                ]
                export_objects = list(current) + extras

            # The FBX funnel is selection-based (use_selection + select_set) and
            # can only ship selectable, visible objects — pre-filter here with a
            # visible INFO log so the funnel's own dropped-object WARNING stays a
            # backstop, not the primary signal. check_hidden_geometry (default
            # on) remains the gate that FAILS the export over hidden meshes.
            export_objects = self._filter_exportable(export_objects)

            if not export_objects:
                self.logger.error("No objects to export.")
                return False

            return self._write_export(
                export_objects, glb_only, create_glb_enabled, start_time
            )
        except ptk.OperationCancelled as e:
            # The progress callback asked to stop (Esc held over the panel's
            # footer; a headless caller's own gate). Only reachable before the
            # write -- see _emit_progress -- so nothing shipped; the tasks that
            # already ran stay applied, exactly as after a failed check.
            self._export_cancelled = True
            self.logger.warning(
                f"Export {e or 'cancelled'}. Task edits already made remain in "
                "the scene — undo or revert if that is not what you want."
            )
            return False
        finally:
            self._progress_end()
            self.task_manager.run_deferred_restores()
            # Restore the pre-bake scene state recorded by smart_bake's session
            # manifest (swap the original actions back, unmute constraints and
            # drivers) — mirror of mayatk's finally-block restore.  Without
            # this, every export with Smart Bake on permanently muted the
            # user's constraint/driver network and left the baked Action in
            # place, despite the task's "restorable" contract.
            _session = getattr(self.task_manager, "_bake_session_id", None)
            if _session:
                try:
                    from blendertk.anim_utils.smart_bake._smart_bake import (
                        SmartBake,
                    )

                    restore = SmartBake.restore(_session)
                    if restore.success:
                        self.logger.info(
                            f"Restored pre-bake scene state (session '{_session}')."
                        )
                    else:
                        # The session stays in the manifest until a restore
                        # completes, so a manual retry is possible.
                        self.logger.warning(
                            f"SmartBake restore failed for session '{_session}' "
                            "— constraints/drivers may still be muted; retry "
                            f"with SmartBake.restore('{_session}')."
                        )
                except Exception as e:
                    # Never mask an export exception from inside finally.
                    self.logger.error(f"SmartBake restore failed: {e}")
                self.task_manager._bake_session_id = None
            # Closed here rather than around the write: a failed check or an
            # aborted task returns before the write and used to leak the handler
            # (and with it the open .log file).
            if self.create_log_file:
                self.close_file_handlers()

    def _filter_exportable(self, objects: List) -> List:
        """Drop objects the selection-based FBX funnel cannot ship — hidden,
        selection-locked (``hide_select``), or outside the active view layer (an
        excluded collection makes ``select_set`` raise). Mirrors the
        ``exclude_hdr``/``ignore_groups`` pattern: the exclusion is logged (INFO,
        naming what was dropped) rather than silent. ``check_hidden_geometry`` is
        what *fails* an export over hidden meshes; this filter keeps the write
        honest when that check is off or the members aren't meshes."""
        exportable, dropped = [], []
        for o in objects:
            try:
                ok = (not getattr(o, "hide_select", False)) and o.visible_get()
            except RuntimeError:  # not in the active view layer
                ok = False
            (exportable if ok else dropped).append(o)
        if dropped:
            shown = ", ".join(o.name for o in dropped[:10]) + (
                " …" if len(dropped) > 10 else ""
            )
            self.logger.info(
                f"Excluding {len(dropped)} hidden/unselectable object(s) the "
                f"selection-based FBX funnel cannot ship: {shown}"
            )
        return exportable

    def _write_export(
        self,
        export_objects: List,
        glb_only: bool,
        create_glb_enabled: bool,
        start_time: float,
    ) -> bool:
        """Write the FBX (and any GLB deliverable) for an already-prepared export
        set. Split out of :meth:`perform_export` so the staged-state cleanup can
        wrap the whole task+write span in one ``finally`` without nesting."""
        from blendertk.env_utils.fbx_utils import FbxUtils

        export_succeeded = False
        glb_tempdir = None
        try:
            if glb_only:
                glb_tempdir = ptk.TempArtifacts("scene_exporter_glb").dir_path()
                fbx_write_path = os.path.join(
                    glb_tempdir, os.path.basename(self.export_path)
                )
            else:
                fbx_write_path = self.export_path

            usd = bool(getattr(self, "_usd_format", False))
            self._progress_step("Writing USD…" if usd else "Writing FBX…")
            # From here the deliverable is finished regardless of a stop
            # request (see _emit_progress).
            self._progress_cancellable = False
            if usd:
                self._write_usd(fbx_write_path, export_objects)
            else:
                # Resolve -> repair -> report -> write, in that order: the settings
                # report must describe the kwargs actually handed to the exporter,
                # so the carrier repair (which needs the export set) runs first.
                fbx_options = self._resolved_fbx_options()
                self._force_carrier_readability(export_objects, fbx_options)
                self._log_fbx_options(fbx_options)
                FbxUtils.export_selection_fbx(
                    filepath=fbx_write_path,
                    objects=export_objects,
                    **fbx_options,
                )
            export_succeeded = True

            deliverable_path = self.export_path
            if glb_only:
                self._progress_step("Converting to GLB…")
                glb_path = self._create_glb(
                    fbx_path=fbx_write_path, announce=False, objects=export_objects
                )
                if not (glb_path and os.path.exists(glb_path)):
                    self.logger.error(
                        "GLB-only export failed: FBX→GLB conversion produced no file."
                    )
                    export_succeeded = False
                    return False
                deliverable_path = os.path.splitext(self.export_path)[0] + ".glb"
                shutil.move(glb_path, deliverable_path)
                self.logger.success(f"GLB created: {deliverable_path}")

            elapsed = time.time() - start_time
            export_info_lines = [
                "✓ File written successfully",
                "",
                f"Path: {deliverable_path}",
                f"Duration: {elapsed:.1f}s",
            ]
            tm = self.task_manager
            t_cnt = getattr(tm, "_last_task_count", 0)
            c_cnt = getattr(tm, "_last_check_count", 0)
            overridden = self._overridden_checks
            f_cnt = len(overridden)
            if t_cnt or c_cnt:
                export_info_lines.append("")
                export_info_lines.append(f"Tasks Executed: {t_cnt}")
                if c_cnt:
                    # Never "N/N" after an override: the deliverable shipped
                    # WITH known failures and the banner is the record of it.
                    export_info_lines.append(f"Checks Passed: {c_cnt - f_cnt}/{c_cnt}")
                    if f_cnt:
                        export_info_lines.append(
                            f"Checks Overridden: {', '.join(overridden)}"
                        )

            self.logger.log_box("EXPORT SUCCESSFUL", export_info_lines, level="SUCCESS")

            if create_glb_enabled and not glb_only:
                self._progress_step("Converting to GLB…")
                self._create_glb(objects=export_objects)

            # Write the scene-data sidecar (hierarchy baseline + data_export
            # snapshot) as the single LAST step of every mode, so it can
            # describe the deliverable that actually shipped rather than the
            # state before the GLB existed. Safe after _create_glb because that
            # never raises -- every failure path inside it logs and returns None
            # -- so a failed conversion still leaves the sidecar written, simply
            # without a section describing the GLB. An export that shipped
            # NOTHING still writes none: glb-only returns above on a failed
            # conversion, and rolling the hierarchy-diff baseline forward for a
            # phantom would make the next run compare against it. Keyed off the
            # logical export path (output dir + stem), independent of where the
            # FBX was actually written. Mirror of mayatk's ordering.
            self._progress_step("Writing scene sidecar…")
            self._write_scene_data_sidecar(export_objects)
            self._progress_finish("Export complete")
        except Exception as e:
            self.logger.error(f"Failed to export objects: {e}")
            raise RuntimeError(f"Failed to export objects: {e}")
        finally:
            if glb_tempdir:
                shutil.rmtree(glb_tempdir, ignore_errors=True)

        if not export_succeeded:
            return False

        return True

    def _data_export_snapshot(self, export_objects: List) -> dict:
        """Decoded copy of every ``data_export`` channel, as shipped in the FBX.

        Empty dict when the carrier is absent, empty, or not part of
        *export_objects* — the carrier is a hidden Empty, so it only ships
        when the ``export_data_node`` task folded it into the export set,
        and the record must only claim what actually shipped.  Never raises
        — the record must not break the export it records.
        """
        try:
            from blendertk.node_utils.data_nodes import DataNodes

            carrier = DataNodes.get_export_node(create=False)
            if carrier is None or carrier not in export_objects:
                return {}
            return DataNodes.dump(decode=True).get(DataNodes.EXPORT) or {}
        except Exception:
            self.logger.debug("data_export snapshot skipped.", exc_info=True)
            return {}

    def _write_scene_data_sidecar(self, export_objects: List) -> None:
        """Write the sidecar JSON recording what shipped in the export.

        Mirror of mayatk's ``TaskManager.write_scene_data_sidecar``, kept on
        the engine here for the same reason as :meth:`_create_glb` —
        blendertk's ``TaskManager`` carries no ``export_path`` of its own.
        The hierarchy section is maintained when a manifest already exists
        (the exporter-side hierarchy *check* isn't ported yet, so unlike
        mayatk there is no check-ran trigger); the data section is recorded
        whenever the ``data_export`` carrier shipped content.  A
        metadata-free export leaves no sidecar.  Best-effort: the record
        must never break the export it records.
        """
        export_path = getattr(self, "export_path", None)
        if not export_path or not export_objects:
            return
        try:
            sk = {"base_stem": bool(getattr(self, "_version_format", ""))}
            SceneDataSidecar.migrate_legacy(export_path, **sk)
            manifest_path = SceneDataSidecar.manifest_path_for(export_path, **sk)

            data = self._data_export_snapshot(export_objects)
            if not data and not os.path.exists(manifest_path):
                return

            paths = SceneDataSidecar.build_full_path_set(export_objects)
            if (
                SceneDataSidecar.write_manifest(export_path, paths, data=data, **sk)
                is None
            ):
                self.logger.debug("Could not write scene-data sidecar")
        except Exception:
            self.logger.debug("scene-data sidecar write skipped.", exc_info=True)

    @staticmethod
    def _lightmap_search_dirs(objects: Optional[List] = None) -> List[str]:
        """Folders the GLB applier joins the manifest's basenames against
        (:meth:`LightmapBaker.search_dirs`, scoped to *objects*; mirror of
        mayatk's ``TaskManager._lightmap_search_dirs``)."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker.search_dirs(objects or None)

    def _create_glb(
        self,
        fbx_path: Optional[str] = None,
        announce: bool = True,
        objects: Optional[List] = None,
    ) -> Optional[str]:
        """Convert an exported FBX to a GLB via pythontk's ``MeshConvert``.

        Runs after the FBX has been written; :meth:`perform_export` invokes this
        explicitly rather than as part of the pre-export task pipeline. Mirror of
        mayatk's ``TaskManager.create_glb``, kept on the engine here because
        blendertk's ``TaskManager`` carries no ``export_path`` of its own — the
        FBX path is resolved from this engine's :attr:`export_path` instead.

        The conversion is handed the scene sidecar built from *objects*
        (:class:`~blendertk.env_utils.scene_state.SceneState` — the same
        readers the WebXR preview uses), so the production GLB gets the same
        translation repairs the preview shows, and the envelope rides embedded
        in the GLB's ``extras``. A sidecar read failure degrades to a bare
        conversion rather than costing the deliverable.

        Parameters:
            fbx_path: FBX to convert. Defaults to :attr:`export_path` (the
                FBX-alongside case). The GLB-only path passes the temp FBX so the
                ``.glb`` lands beside it (then gets moved into the output dir).
            announce: When True, log the resulting path. The GLB-only path sets
                this False and logs the final (moved) path itself.
            objects: The export set the sidecar describes; ``None`` skips the
                sidecar (bare conversion).

        Returns:
            The created ``.glb`` path, or ``None`` if conversion failed.
        """
        from blendertk.env_utils.scene_state import SceneState

        src = fbx_path or self.export_path
        sidecar = None
        if objects:
            try:
                sections = SceneState.read(objects)
                sidecar = ptk.MeshConvert.build_scene_sidecar(
                    sections,
                    source=SceneState.source(),
                    asset=os.path.basename(src),
                )
                if sections:
                    # Mirror of mayatk's wording: the sections are written INTO
                    # the GLB's own material JSON, with a copy in `extras` as
                    # provenance -- no companion file is produced or required.
                    self.logger.info(
                        "Scene sidecar (%s) written into the GLB's materials "
                        "(copy embedded in extras; no companion file).",
                        ", ".join(sorted(sections)),
                    )
            except Exception:  # noqa: BLE001 — a bare GLB still beats no GLB
                self.logger.warning("Scene sidecar skipped.", exc_info=True)

        self.logger.info("Converting FBX to GLB...")
        self._progress_note("GLB: converting the FBX…")
        try:
            glb_path = ptk.MeshConvert.fbx_to_glb(
                src,
                overwrite=True,
                auto_install=True,
                prompt=False,
                sidecar=sidecar,
                # Where the maps are NOW. The manifest riding the FBX carries
                # the folder the bake was committed from, and the applier tries
                # that first -- but it is history, not a contract: reorganise
                # the project and every EXR lookup misses, shipping an unlit
                # deliverable while the bake sits one folder away. The
                # workspace's texture folders plus wherever the markers' maps
                # were actually found (the applier can only JOIN a basename
                # against a list; a map in a subfolder needs its folder named).
                lightmap_dirs=self._lightmap_search_dirs(objects),
            )
        except (FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"GLB conversion failed: {e}")
            return None

        # GLB texture pass — the GLB's half of the panel's TWO general texture
        # dials (Texture File Type + Optimize Textures), resolved against the
        # shared web-delivery policy by ``TaskManager._glb_texture_params``
        # (mirror of mayatk's, whose ``create_glb`` lives on the task manager).
        # Runs LAST: a KTX2 GLB is opaque to every PIL-based post-tool, so
        # nothing may follow the encode. ONE ``optimize_glb_textures`` call — a
        # second would re-decode and re-encode every image, and a KTX2 payload
        # cannot be re-encoded at all. Unconditional since 2026-08-29: the
        # deliverable this panel writes is a web asset, and the previous "no
        # dials, no pass" default shipped 280 MB where the preview showed 8.71.
        params = self.task_manager._glb_texture_params()
        carrier = params["image_format"]
        self._progress_note(f"GLB: {carrier} texture pass…")
        try:
            summary = ptk.MeshConvert.optimize_glb_textures(glb_path, **params)
        except Exception as e:  # noqa: BLE001 — deliverable must not lie
            self.logger.error(f"GLB texture pass ({carrier}) failed: {e}")
            return None
        # Worded by the converter that produced the summary, so this can no
        # longer drift from mayatk's copy (it already had): an empty summary
        # still speaks, and a populated one reports what was RESAMPLED
        # rather than which mode ran.
        self.logger.info(
            ptk.MeshConvert.describe_texture_pass(
                summary, carrier, params.get("max_size") or 0
            )
        )

        if announce:
            self.logger.success(f"GLB created: {glb_path}")
        return glb_path

    #: The extensions an output name may carry -- stripped before the format's
    #: own is appended, so "asset.fbx" typed into a USD export lands as
    #: "asset.usd" rather than "asset.fbx.usd". The carrier vocabulary, not a
    #: second list (``CARRIER_BY_EXTENSION`` holds every USD spelling too).
    _DELIVERABLE_EXTENSIONS = tuple(ptk.CARRIER_BY_EXTENSION)

    #: ``wm.usd_export`` kwargs for the USD output format: the shared interchange
    #: set (``btk.UsdUtils.INTERCHANGE_EXPORT_OPTIONS``; a MaterialX network is a
    #: ``usd_options`` override), with the texture files referenced relative to
    #: the layer -- a deliverable beside its scene, unlike a scratch payload.
    #: Mirror of mayatk's ``USD_EXPORT_OPTIONS``.
    USD_EXPORT_OPTIONS: Dict[str, Any] = dict(
        UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
        relative_paths=True,
    )

    def _write_usd(self, usd_path: str, export_objects: List) -> str:
        """Write *export_objects* as a USD layer (the ``usd`` output format).

        Samples animation only across the frames that carry motion
        (:meth:`UsdUtils.sampling_frame_range`); the scene range is set for the
        call and restored. Custom properties ride as ``userProperties`` so the
        ``data_export`` carrier ships readable, as the FBX path forces
        ``use_custom_props`` (consumers reading them are not yet verified).
        """
        options = dict(self.USD_EXPORT_OPTIONS)
        options.update(getattr(self, "_usd_options", None) or {})
        frame_range = UsdUtils.sampling_frame_range(export_objects)
        if frame_range:
            options["export_animation"] = True
            self.logger.info(f"USD: sampling frames {frame_range[0]}-{frame_range[1]}.")
        shared = {}
        for obj in export_objects:
            data = getattr(obj, "data", None)
            if data is not None and getattr(obj, "type", None) == "MESH":
                shared.setdefault(data.name, []).append(obj.name)
        linked = {m: n for m, n in shared.items() if len(n) > 1}
        if linked:
            self.logger.warning(
                f"USD: {len(linked)} shared mesh(es) are written flat; every linked "
                "duplicate ships as its own mesh."
            )
        if any(getattr(o, "name", "") == "data_export" for o in export_objects):
            options["export_custom_properties"] = True
            self.logger.info(
                "USD: the data_export carrier ships as a prim with userProperties; "
                "consumers reading them are not yet verified."
            )
        written = UsdUtils.export(
            filepath=usd_path,
            objects=export_objects,
            selection_only=True,
            frame_range=frame_range,
            **options,
        )
        self.logger.info(f"USD written: {written}")
        return written

    @classmethod
    def _strip_deliverable_extension(cls, name: str) -> str:
        """*name* without a trailing deliverable extension (whitelist strip: a
        dotted version token is not an extension)."""
        return ptk.StrUtils.strip_suffix(name, cls._DELIVERABLE_EXTENSIONS)

    def generate_export_path(
        self, version_format: str = "", extension: str = ".fbx"
    ) -> str:
        """Generate the full export file path.

        Parameters:
            version_format: If non-empty, treat as a pythontk-style
                placeholder template (e.g. ``{stem}_v{n:03d}``) and resolve
                the next-version path via ``FileUtils.next_version_path``.
        """
        import bpy

        if self.output_name and any(char in self.output_name for char in "*?"):
            import glob

            pattern = self._strip_deliverable_extension(self.output_name)
            pattern += extension.lower()

            search_path = os.path.join(self.export_dir, pattern)
            matches = glob.glob(search_path)

            if matches:
                matches.sort()
                action = "using as version seed" if version_format else "overwriting"
                self.logger.info(
                    f"Wildcard '{self.output_name}' matched {len(matches)} files; "
                    f"{action}: {matches[-1]}"
                )
                return self._apply_versioning(matches[-1], version_format)

        scene_path = bpy.data.filepath or "untitled"
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self.output_name or scene_name
        export_name = self._strip_deliverable_extension(export_name)
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        path = os.path.join(self.export_dir, f"{export_name}{extension.lower()}")
        return self._apply_versioning(path, version_format)

    def _apply_versioning(self, path: str, template: str) -> str:
        """Resolve a version template into a concrete versioned path.

        Two-stage substitution:
          - Stage 1: substitute ``{date}``, ``{user}``, ``{scene}`` via
            ``StrUtils.replace_placeholders`` (which preserves unresolved
            ``{stem}``/``{n:NNd}`` placeholders along with their format spec).
          - Stage 2: ``FileUtils.next_version_path`` resolves the next
            available ``{n}`` by scanning the parent directory.

        Returns the original path unchanged when the template is empty or
        a guard condition prevents safe versioning (logs a warning in that
        case so the user sees what happened).
        """
        import bpy

        if not template:
            return path

        if "{ext}" in template:
            self.logger.warning(
                "Version format should not include '{ext}' — extension is "
                "handled automatically. Versioning skipped."
            )
            return path

        stem, ext = os.path.splitext(os.path.basename(path))
        if not stem or stem.lower() == "untitled":
            self.logger.warning(
                "Skipping versioning: export name is untitled — save the "
                "file or pass an explicit output_name."
            )
            return path

        import getpass

        scene_path = bpy.data.filepath or ""
        scene_name = (
            os.path.splitext(os.path.basename(scene_path))[0] if scene_path else ""
        )

        if "{scene}" in template and not scene_name:
            self.logger.error(
                "Version format uses '{scene}' but the file is unsaved. "
                "Save the file or remove '{scene}' from the format. "
                "Versioning skipped."
            )
            return path

        expanded = ptk.StrUtils.replace_placeholders(
            template,
            date=datetime.now().date().isoformat(),
            user=getpass.getuser(),
            scene=scene_name,
        )

        if "{stem}" not in expanded and "{scene}" not in template:
            self.logger.warning(
                "Version format missing '{stem}' and '{scene}' — output name "
                "and file identity will not appear in the resulting filename."
            )

        internal_format = expanded + "{ext}"

        class _Dummy(dict):
            def __missing__(self, key):
                return "x"

        try:
            test_name = internal_format.format_map(_Dummy(stem="test", n=1, ext=ext))
            test_stem = os.path.splitext(test_name)[0]
            if not SceneDataSidecar.VERSION_SUFFIX_RE.search(test_stem):
                self.logger.warning(
                    f"Version format {template!r} produces names not matching '_v<N>'."
                )
        except (ValueError, IndexError, KeyError) as e:
            self.logger.warning(f"Could not validate version format: {e}")

        try:
            new_path = ptk.FileUtils.next_version_path(path, format=internal_format)
        except ValueError as e:
            self.logger.error(f"Version format invalid: {e}. Versioning skipped.")
            return path

        self.logger.info(
            f"Versioned export path: {os.path.basename(path)} -> "
            f"{os.path.basename(new_path)}"
        )
        return new_path

    def format_export_name(self, name: str) -> str:
        """Format the export name using a regex pattern and replacement (e.g. 'pattern->replace')."""
        if self.name_regex:
            for delim in ("->", "=>", "|"):
                if delim in self.name_regex:
                    pattern, replacement = self.name_regex.split(delim, 1)
                    break
            else:
                pattern, replacement = self.name_regex, ""
            pattern = pattern.strip()
            replacement = replacement.strip()
            try:
                return re.sub(pattern, replacement, name)
            except re.error as e:
                self.logger.error(f"Invalid regex pattern: {pattern}. Error: {e}")
                return name
        return name

    def generate_log_file_path(self, export_path: str) -> str:
        """Generate the log file path based on the export path."""
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    def setup_file_logging(self, log_file_path: str):
        """Setup file logging to log actions during export."""
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.file_handler = file_handler
        root_logger = logging.getLogger(self.__class__.__name__)
        root_logger.addHandler(self.file_handler)
        self.logger.debug(f"File logging setup complete. Log file: {log_file_path}")

        if self.hide_log_file and os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(log_file_path, 2)

    def close_file_handlers(self):
        """Close and remove file handlers after logging is complete."""
        root_logger = logging.getLogger(self.__class__.__name__)
        handlers = root_logger.handlers[:]
        for handler in handlers:
            if isinstance(handler, logging.FileHandler):
                handler.close()
                root_logger.removeHandler(handler)
                self.logger.debug("File handler closed and removed.")

    # ------------------------------------------------------------------ FBX export presets
    # (pythontk.PresetStore-backed named dicts of export_scene.fbx kwargs -- see module
    # docstring for the design rationale.)

    @classmethod
    def _preset_store(cls) -> ptk.PresetStore:
        """Two-tier store for FBX export-option presets: shipped ``presets/`` (built-in,
        read-only) + a writable user tier under ``user_config_root()``."""
        builtin_dir = os.path.join(os.path.dirname(__file__), "presets")
        store = ptk.PresetStore(
            cls.PRESET_NAME, package=cls.PRESET_PACKAGE, builtin_dir=builtin_dir
        )
        cls._migrate_legacy_preset_dir(store)
        return store

    @classmethod
    def _migrate_legacy_preset_dir(cls, store: ptk.PresetStore) -> None:
        """One-time move of FBX presets out of the window-template directory.

        ``PRESET_NAME`` used to be ``"scene_exporter"``, which resolved the user
        tier to the SAME directory the panel's uitk ``PresetManager`` keeps its
        window templates in. This relocates any stranded FBX kwarg dicts to the
        new ``fbx_presets`` tier and leaves the window templates untouched:

        * a JSON **with** a ``_meta`` block is a window template — skipped;
        * a JSON **without** one is an FBX preset — moved (unless the new tier
          already has the name, or its payload equals ANY shipped built-in's, in
          which case it carries no user intent — it is a copy of something we
          ship — and promoting it would only pin a stale shadow of that built-in
          when its shipped values later change; deleted instead);
        * the shared ``.active`` sidecar is removed when it names a preset this
          migration took away (it belonged to the FBX store's combo, and the
          template combo would otherwise restore an FBX preset name as the
          "active template").
        """
        if cls._legacy_fbx_presets_migrated:
            return
        cls._legacy_fbx_presets_migrated = True
        legacy = (
            Path(ptk.UserConfig.user_config_root())
            / cls.PRESET_PACKAGE
            / "scene_exporter"
        )
        log = logging.getLogger(__name__)
        try:
            if not legacy.is_dir():
                return
            # Read the shipped payloads ONCE: the "is this just a copy of something
            # we ship?" test is by CONTENT, not by name — a stale ``default.json``
            # written when the built-in of that name held different values is still
            # a copy of a shipped preset (today's ``game_asset``), and promoting it
            # would shadow the built-in forever.
            builtin_payloads = []
            builtin_dir = Path(store.builtin_dir) if store.builtin_dir else None
            if builtin_dir is not None and builtin_dir.is_dir():
                for bf in builtin_dir.glob(f"*{store.ext}"):
                    try:
                        builtin_payloads.append(
                            json.loads(bf.read_text(encoding="utf-8"))
                        )
                    except (ValueError, OSError):
                        continue
            for f in legacy.glob(f"*{store.ext}"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                if not isinstance(data, dict) or "_meta" in data:
                    continue  # window template (or not ours) — leave in place
                target = Path(store.user_dir) / f.name
                if target.exists():
                    f.unlink()  # already migrated on a previous run
                elif data in builtin_payloads:
                    f.unlink()  # a copy of a shipped built-in — no user intent
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    f.replace(target)
                    log.info(f"Migrated legacy FBX preset: {f.name}")
            active = legacy / ".active"
            if active.is_file():
                try:
                    name = json.loads(active.read_text(encoding="utf-8")).get("name")
                except (ValueError, OSError, AttributeError):
                    name = None
                if name and not (legacy / f"{name}{store.ext}").exists():
                    active.unlink()
                    log.info(f"Cleared cross-store .active pointer ({name!r}).")
        except OSError as e:
            log.warning(f"Legacy FBX preset migration incomplete: {e}")

    @classmethod
    def list_fbx_presets(cls) -> List[str]:
        """All FBX export-option preset names (built-in + user; a user preset shadows a
        built-in of the same name)."""
        return cls._preset_store().list()

    @classmethod
    def fbx_preset_dir(cls) -> str:
        """Writable directory FBX export-option presets are saved to (the "Open Preset
        Directory" option-box button's target)."""
        return str(cls._preset_store().user_dir)

    @classmethod
    def fbx_preset_path(cls, name: str) -> Optional[str]:
        """Filesystem path *name* resolves to (built-in or user tier), or ``None`` if it
        doesn't exist in either tier."""
        store = cls._preset_store()
        tier = store.source(name)
        return str(store.path(name, tier)) if tier else None

    @classmethod
    def save_fbx_preset(
        cls, name: str, options: Optional[Dict[str, Any]] = None
    ) -> str:
        """Save *options* (default: :data:`_DEFAULT_FBX_OPTIONS`) as user preset *name*.

        Returns the written path (as ``str``).
        """
        data = dict(options) if options is not None else dict(_DEFAULT_FBX_OPTIONS)
        return str(cls._preset_store().save(name, data))

    @classmethod
    def delete_fbx_preset(cls, name: str) -> bool:
        """Delete the *user* FBX export-option preset *name* (built-ins are read-only).
        Returns whether a file was actually removed."""
        return cls._preset_store().delete(name)

    def load_fbx_export_preset(
        self, name: Optional[str] = None, verify: bool = False
    ) -> Optional[dict]:
        """Load a named FBX export-option preset so the next :meth:`perform_export` call
        forwards its kwargs to ``bpy.ops.export_scene.fbx`` (see module docstring for the
        PresetStore-backed design, chosen over Blender's native operator-preset system).

        Parameters:
            name: preset name, as returned by :meth:`list_fbx_presets`. Falsy clears any
                loaded preset, reverting to the built-in defaults (``_DEFAULT_FBX_OPTIONS``).
            verify: if True, also logs + returns the resolved kwargs (see
                :meth:`verify_fbx_preset`).

        Returns:
            The resolved kwargs dict when *verify* is True, otherwise ``None``.

        Raises:
            RuntimeError: *name* does not resolve to an existing preset, or the preset file
                is malformed.
        """
        if not name:
            self._fbx_preset_options = None
        else:
            try:
                options = self._preset_store().load(name)
            except (KeyError, ValueError, OSError) as e:
                self.logger.error(f"Failed to load FBX export preset {name!r}: {e}")
                raise RuntimeError(
                    f"Failed to load FBX export preset {name!r}: {e}"
                ) from e
            self._fbx_preset_options = options
            self.logger.info(f"Loaded FBX export preset: {name}")

        return self.verify_fbx_preset() if verify else None

    def _resolved_fbx_options(self) -> dict:
        """The FBX export kwargs the next write will use — the active preset's options
        merged over the built-in defaults, with the scene-range-take invariant applied.
        The single home of that merge, shared by :meth:`verify_fbx_preset` (which logs
        it) and :meth:`perform_export`'s media-selfcontained probe.

        The invariant is applied HERE rather than at the write so every consumer — the
        settings report included — sees what is actually written; a repair applied after
        the report would make that report lie about the very values it exists to
        disclose."""
        options = {
            **_DEFAULT_FBX_OPTIONS,
            **(getattr(self, "_fbx_preset_options", None) or {}),
        }
        self._force_scene_range_take(options)
        return options

    def _force_carrier_readability(self, export_objects, fbx_options: dict) -> None:
        """When the ``data_export`` carrier is in the export set, force the two exporter
        options that make it readable — Blender's FBX exporter drops custom properties by
        default and excluded object types outright, so a user preset carrying
        ``use_custom_props: false`` or an ``object_types`` without ``EMPTY`` would ship a
        carrier holding nothing (or no carrier at all) with no signal: the failure that
        looks most like success. Same rule as the hand-off bridges (``handoff_export``):
        shipping the carrier and shipping what makes it readable are one decision, so a
        preset override cannot separate them. Mutates *fbx_options* in place and logs any
        repair."""
        from blendertk.node_utils.data_nodes import DataNodes
        from blendertk.env_utils.fbx_utils import FbxUtils

        names = {getattr(o, "name", str(o)) for o in export_objects or []}
        if DataNodes.EXPORT not in names:
            return
        repaired = []
        if not fbx_options.get("use_custom_props"):
            fbx_options["use_custom_props"] = True
            repaired.append("use_custom_props=True")
        types = FbxUtils._as_object_types(fbx_options.get("object_types") or {"MESH"})
        if "EMPTY" not in types:
            fbx_options["object_types"] = types | {"EMPTY"}
            repaired.append("object_types+=EMPTY")
        if repaired:
            self.logger.warning(
                "The active FBX preset would ship an unreadable data_export "
                "carrier — forced " + ", ".join(repaired) + "."
            )

    def _force_scene_range_take(self, fbx_options: dict) -> None:
        """Force the ONE scene-range animation take this exporter's contract rests on.

        With ``bake_anim_use_nla_strips`` or ``bake_anim_use_all_actions`` left at
        Blender's own default (both ``True``), ``export_fbx_bin.fbx_animations``
        writes one take *per action*, each start-zeroed, and **skips the
        scene-range take entirely** — so ``set_bake_animation_range`` cannot affect
        the file and independently-authored curves (a mesh's action vs.
        EmissiveGroups' staged weight proxies) land in separate takes, silently
        time-misaligned in the engine. One scene-range take is also what mayatk's
        FBX path produces, so it is the parity-correct shape; per-shot takes are the
        opt-in job of ``apply_declared_takes``, never an accident of the defaults.

        Same rule as :meth:`_force_carrier_readability`: this is a pipeline-owned
        property, so no preset may separate "export animation" from "export it in
        one coherent take" — including the stock-defaults ``default`` preset and any
        preset a user saves from Blender's own exporter. Mutates *fbx_options* in
        place; no-op when animation isn't being baked.

        The ``apply_declared_takes`` path *depends* on this invariant rather than
        bypassing it: ``FbxUtils`` realizes armed takes by splitting the written
        file's single scene-range AnimStack after the write, so the one extra
        repair takes armed demand is forcing ``bake_anim`` ON — a preset with
        animation off would ship a file with no stack to split, failing the
        takes the user asked for (the Maya twin forces bake-complex on the same
        way).

        DEBUG, not WARNING, precisely because the shipped ``default`` preset carries
        Blender's own values: this fires on every animated export under it, and the
        settings report already discloses the enforced values (the repair runs inside
        :meth:`_resolved_fbx_options`, before anything reads them)."""
        from blendertk.env_utils.fbx_utils import FbxUtils

        takes_armed = bool(FbxUtils._pending_takes)
        if takes_armed and not fbx_options.get("bake_anim"):
            fbx_options["bake_anim"] = True
            self.logger.info(
                "Animation takes are armed — forced bake_anim=True so the "
                "write carries the scene-range take they are cut from."
            )
        if not fbx_options.get("bake_anim"):
            return
        repaired = [
            key
            for key in ("bake_anim_use_nla_strips", "bake_anim_use_all_actions")
            if fbx_options.get(key)
        ]
        for key in repaired:
            fbx_options[key] = False
        if repaired:
            self.logger.debug(
                "Forced one scene-range animation take: "
                + ", ".join(f"{k}=False" for k in repaired)
                + "."
            )

    def verify_fbx_preset(self) -> dict:
        """Return (and log) the FBX export kwargs the next :meth:`perform_export` call will
        use -- the active preset's options merged over the built-in defaults, or the
        defaults alone when no preset is loaded. Mirrors mayatk's ``verify_fbx_preset``,
        which logs Maya's live global FBX-exporter settings the same way."""
        options = self._resolved_fbx_options()
        self._log_fbx_options(options)
        return options

    def _log_fbx_options(self, options: dict) -> None:
        """Report the FBX export settings the write will use.

        Split from :meth:`verify_fbx_preset` so ``perform_export`` can report AFTER
        :meth:`_force_carrier_readability` has run — that repair depends on the export
        set, so it cannot live in the merge, and reporting before it would disclose
        values the write then contradicts.
        """
        # ONE grouped record, mirroring the Maya twin: every log record is its
        # own paragraph in the output panel, so a line per option rendered
        # this dump as ~25 blank-line-separated sections.
        if options and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(
                "FBX Export Settings",
                [f"{k:<34}: {v}" for k, v in sorted(options.items())],
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("scene_exporter", reload=True)
    ui.show(pos="screen", app_exec=True)
