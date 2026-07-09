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
import shutil
import tempfile
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk

from blendertk.env_utils.scene_exporter.task_manager import TaskManager


# Blender-native re-implementation of mayatk's ``HierarchySidecar.VERSION_SUFFIX_RE`` (that class
# lives in the unported ``hierarchy_manager`` subsystem) -- used only for the version-format
# validation warning in :meth:`SceneExporter._apply_versioning`.
_VERSION_SUFFIX_RE = re.compile(r"_v\d+$", re.IGNORECASE)

# Built-in default FBX options (also shipped as the "default" built-in preset -- see
# presets/default.json): embedded textures so nothing ships missing; baked animation since
# there's no bake-pipeline task in this cut to have pre-baked it. Used whenever no preset is
# loaded, and as the seed a fresh "Add Preset" saves from when nothing is selected.
#
# ``use_custom_props`` + ``object_types`` are what let the shared ``data_export`` Empty's
# metadata channels (``lightmap_metadata``, ...) ride into the FBX as user properties --
# Blender defaults custom-property export OFF, and the bridge-oriented ``_EXPORT_DEFAULTS``
# in ``fbx_utils.py`` pins mesh-only ``object_types``, so both must be overridden here or
# the ``export_data_node`` task ships an FBX with no metadata. ``object_types`` is stored
# as a list (JSON presets can't hold a set); ``FbxUtils.export`` coerces it.
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "mesh_smooth_type": "FACE",
    "use_tspace": True,
    "embed_textures": True,
    "path_mode": "COPY",
    "bake_anim": True,
    "use_custom_props": True,
    "object_types": ["EMPTY", "ARMATURE", "MESH"],
}


class SceneExporter(ptk.LoggingMixin):

    # PresetStore identity for FBX export-option presets (see module docstring).
    PRESET_NAME = "scene_exporter"
    PRESET_PACKAGE = "blendertk"

    def __init__(
        self, log_level: str = "WARNING", log_handler: Optional[object] = None
    ):
        """ """
        self._setup_logging(log_level, log_handler)

        self.task_manager = TaskManager(self.logger)
        self.logger.debug("Task manager initialized in SceneExporter.")

    def _setup_logging(self, log_level: str, log_handler: Optional[object]) -> None:
        """Setup logging configuration."""
        self.logger.setLevel(log_level)
        if log_handler:
            self.logger.addHandler(log_handler)

    def _setup_file_logging(self) -> None:
        """Setup file logging."""
        log_file_path = self.generate_log_file_path(self.export_path)
        self.logger.info(f"Generating log file path: {log_file_path}")
        self.setup_file_logging(log_file_path)

    def _initialize_objects(
        self, objects: Optional[Union[List, Callable]]
    ) -> List:
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
        log_level: str = "WARNING",
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        tasks: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, bool]]:
        """Perform the export operation, including initialization and task management."""
        import bpy

        from blendertk.env_utils.fbx_utils import export_selection_fbx

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

        self._setup_logging(log_level, log_handler)

        tasks = dict(tasks) if tasks else {}
        version_format = tasks.pop("version", "") or ""
        output_format = (tasks.pop("output_format", "") or "").lower()
        if not output_format:
            output_format = "fbx"
        create_glb_enabled = output_format in ("glb", "fbx_glb")
        glb_only = output_format == "glb"

        self.export_path = self.generate_export_path(version_format=version_format)
        self.logger.debug(f"Generated export path: {self.export_path}")

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

        export_succeeded = False
        if tasks:
            tasks_successful = self.task_manager.run_tasks(tasks)
            if not tasks_successful:
                return False

        if export_visible:
            # "visible"/"all": the task pipeline's object set is authoritative.
            export_objects = list(self.task_manager.objects or [])
        else:
            # "selected": export the resolved selection captured at init time (already filtered
            # by the caller's ``objects_to_export()``, e.g. excluding the data_internal carrier),
            # then fold in any objects the task pipeline added to the export set — otherwise
            # they'd silently never ship. Re-querying the live selection here instead would
            # bypass that filtering and re-admit anything it deliberately excluded by name.
            current = set(initialized_objs)
            extras = [
                o for o in (self.task_manager.objects or []) if o not in current
            ]
            export_objects = list(current) + extras

        if not export_objects:
            self.logger.error("No objects to export.")
            return False

        glb_tempdir = None
        try:
            if glb_only:
                glb_tempdir = tempfile.mkdtemp(prefix="scene_exporter_glb_")
                fbx_write_path = os.path.join(
                    glb_tempdir, os.path.basename(self.export_path)
                )
            else:
                fbx_write_path = self.export_path

            fbx_options = self.verify_fbx_preset()
            export_selection_fbx(
                filepath=fbx_write_path,
                objects=export_objects,
                **fbx_options,
            )
            export_succeeded = True

            deliverable_path = self.export_path
            if glb_only:
                glb_path = self.task_manager.create_glb(
                    fbx_path=fbx_write_path, announce=False
                )
                if not (glb_path and os.path.exists(glb_path)):
                    self.logger.error(
                        "GLB-only export failed: FBX→GLB conversion produced "
                        "no file."
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
            if t_cnt or c_cnt:
                export_info_lines.append("")
                export_info_lines.append(f"Tasks Executed: {t_cnt}")
                if c_cnt:
                    export_info_lines.append(f"Checks Passed: {c_cnt}/{c_cnt}")

            self.logger.log_box(
                "EXPORT SUCCESSFUL", export_info_lines, level="SUCCESS"
            )

            if create_glb_enabled and not glb_only:
                self.task_manager.create_glb()
        except Exception as e:
            self.logger.error(f"Failed to export objects: {e}")
            raise RuntimeError(f"Failed to export objects: {e}")
        finally:
            if glb_tempdir:
                shutil.rmtree(glb_tempdir, ignore_errors=True)
            if self.create_log_file:
                self.close_file_handlers()

        if not export_succeeded:
            return False

        return True

    def generate_export_path(self, version_format: str = "") -> str:
        """Generate the full export file path.

        Parameters:
            version_format: If non-empty, treat as a pythontk-style
                placeholder template (e.g. ``{stem}_v{n:03d}``) and resolve
                the next-version path via ``FileUtils.next_version_path``.
        """
        import bpy

        if self.output_name and any(char in self.output_name for char in "*?"):
            import glob

            pattern = self.output_name
            if not pattern.lower().endswith((".fbx", ".FBX")):
                pattern += ".fbx"

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
        export_name = export_name.removesuffix(".fbx").removesuffix(".FBX")
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        path = os.path.join(self.export_dir, f"{export_name}.fbx")
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
            test_name = internal_format.format_map(
                _Dummy(stem="test", n=1, ext=ext)
            )
            test_stem = os.path.splitext(test_name)[0]
            if not _VERSION_SUFFIX_RE.search(test_stem):
                self.logger.warning(
                    f"Version format {template!r} produces names not matching "
                    "'_v<N>'."
                )
        except (ValueError, IndexError, KeyError) as e:
            self.logger.warning(f"Could not validate version format: {e}")

        try:
            new_path = ptk.FileUtils.next_version_path(
                path, format=internal_format
            )
        except ValueError as e:
            self.logger.error(
                f"Version format invalid: {e}. Versioning skipped."
            )
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
        return ptk.PresetStore(
            cls.PRESET_NAME, package=cls.PRESET_PACKAGE, builtin_dir=builtin_dir
        )

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

    def verify_fbx_preset(self) -> dict:
        """Return (and log) the FBX export kwargs the next :meth:`perform_export` call will
        use -- the active preset's options merged over the built-in defaults, or the
        defaults alone when no preset is loaded. Mirrors mayatk's ``verify_fbx_preset``,
        which logs Maya's live global FBX-exporter settings the same way."""
        options = {
            **_DEFAULT_FBX_OPTIONS,
            **(getattr(self, "_fbx_preset_options", None) or {}),
        }
        for key, value in options.items():
            self.logger.info(f"{key} is set to: {value}")
        return options


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("scene_exporter", reload=True)
    ui.show(pos="screen", app_exec=True)
