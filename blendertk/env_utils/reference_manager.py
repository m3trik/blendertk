# !/usr/bin/python
# coding=utf-8
"""Reference Manager engine -- the workspace's scene files, the scene's references
(linked libraries) and the scene-file operations a browser runs on them.

:class:`ReferenceManager` mirrors mayatk's ``ReferenceManager`` by name and behavior,
over Blender's model:

* Maya file reference -> a linked **library** (File > Link).
  :attr:`ReferenceManager.current_references` wraps each as a :class:`_LibraryRef`
  (``path``, ``namespace`` = the library's name, ``label``); importing a reference makes
  its library local (``EnvUtils.make_library_local``); a display override sets its
  objects' ``display_type`` + ``hide_select``.
* Maya scene file -> ``.blend``. A foreign (Maya / FBX / USD) row is baked to a cached
  ``.blend`` through the maya_bridge before it can be linked or opened.
* Maya project -> a workspace folder (marked by a ``workspace.mel``, or a folder that
  holds .blend files); the session's project is ``EnvUtils.set_current_workspace``'s pin.

Its panel is tentacle's shared Reference Manager (``slots/tools/reference_manager.py``),
which composes one and passes its progress, bake and report collaborators in.
``import bpy`` is deferred into the call bodies and every read degrades without it
(the Qt-only harness loads the panel with no Blender running).
"""

import os
from typing import Callable, ContextManager, List, Optional

import pythontk as ptk

from blendertk.env_utils._env_utils import EnvUtils

# Scratch twins of foreign scenes opened "as new" (see ReferenceManager.open_scene): one
# per source under the system temp dir, age-swept, discarded on close/next open while
# still untouched. Process-wide, like the open file itself; lazily built (no temp-dir
# lookup at import). Mirror of mayatk's.
_OPENED_SCRATCH_PREFIX = "btk_opened"
_opened_scratches = None


def _scratch_twins():
    """The ``ptk.ScratchTwins`` store (``<temp>/btk_opened_<hash>/<stem>_<ext>.blend``)."""
    global _opened_scratches
    if _opened_scratches is None:
        _opened_scratches = ptk.ScratchTwins(_OPENED_SCRATCH_PREFIX, extension=".blend")
    return _opened_scratches


def _bpy():
    """``bpy``, or None outside a running Blender."""
    try:
        import bpy
    except ImportError:
        return None
    return bpy


class _LibraryRef:
    """A linked library as a reference: what mayatk's file reference answers.

    ``namespace`` is the library's name -- the key :meth:`ReferenceManager.remove_references`
    and :meth:`ReferenceManager.import_references` select by (Blender has no namespaces:
    a library's datablocks carry no prefix).
    """

    __slots__ = ("library",)

    def __init__(self, library):
        self.library = library

    @property
    def namespace(self) -> str:
        return self.library.name

    @property
    def label(self) -> str:
        """Human name for a message (mirror of mayatk's)."""
        return self.library.name

    @property
    def path(self) -> str:
        """The library's file, absolute (a ``//``-relative path resolved)."""
        return EnvUtils._abspath(self.library.filepath)


class _ReferenceManagerInternal(object):
    """Internal helpers for ReferenceManager."""

    @staticmethod
    def _bake_source_key(linked_path) -> Optional[str]:
        """:meth:`ReferenceManager.bake_source`, normalized for comparison."""
        source = ReferenceManager.bake_source(linked_path)
        return os.path.normcase(os.path.normpath(source)) if source else None

    @staticmethod
    def _importer():
        """The maya_bridge's scene importer class (deferred: the bridge is heavy)."""
        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        return MayaSceneImport


class ReferenceManager(ptk.LoggingMixin, _ReferenceManagerInternal):
    """Blender's Reference Manager engine: the workspace's scene files, the file's
    linked libraries, and the scene-file operations a browser runs on them.

    Mirror of mayatk's ``ReferenceManager`` (see the module docstring for the mapping).
    No UI: a panel composes one, handing it its collaborators (:meth:`__init__`), and
    calls its API.
    """

    #: Workspace-scan globs: the scenes a workspace lists.
    SCENE_FILE_TYPES = ("*.blend",)
    #: Extensions linked natively.
    NATIVE_EXTENSIONS = (".blend",)
    #: Foreign (cross-DCC) rows: baked to a ``.blend`` through the maya_bridge before
    #: they can be linked or opened (:meth:`bake_scene`). ``.ma``/``.mb`` go through a
    #: headless Maya first; ``.fbx`` and USD are already the bake's own input.
    FOREIGN_EXTENSIONS = (".ma", ".mb", ".fbx", *ptk.USD_EXTENSIONS)
    #: Rows Unlink and Import brings in with no reference behind them.
    IMPORT_EXTENSIONS = FOREIGN_EXTENSIONS
    #: The Include Types a browser lists by default: Blender's own scene type.
    INCLUDE_DEFAULTS = (".blend",)
    #: What Save writes (:meth:`save_scene`).
    SCENE_SAVE_EXTENSION = ".blend"
    #: What an unlink does with the imported reference's namespace: Blender has none (a
    #: made-local library merges into the file's own name space, a clash taking a
    #: ``.001`` suffix), so there is nothing to choose between.
    NAMESPACE_MODES = ("remove",)
    #: What an unlink does with a library's scene data (see :meth:`import_references`).
    SCENE_DATA_MODES = EnvUtils.SCENE_DATA_MODES
    #: Display override modes (:meth:`set_reference_display_mode`).
    DISPLAY_MODES = ("off", "reference", "template")
    #: How a foreign scene's rig logic can travel (``bake_scene`` / ``import_scene``
    #: ``rig_mode``) -- the choices a browser offers when the scene has some.
    RIG_MODES = ("rig", "bake", "raw")
    #: Where a browser's header presets (naming / filter settings) are saved.
    PRESET_DIR = "blendertk/reference_manager"

    _progress = None
    _report = None

    def __init__(
        self,
        log_level="WARNING",
        *,
        progress: Optional[Callable[[str], ContextManager]] = None,
        bake_for_reference: Optional[Callable[..., Optional[str]]] = None,
        report: Optional[Callable[[str], None]] = None,
    ):
        """Build the engine (mirror of mayatk's; a panel passes the same
        collaborators to either host).

        Parameters:
            log_level: The logger's level.
            progress: ``progress(text)`` -> a context manager yielding a
                ``progress(current, total, message)`` callback (or None): the scope a
                long engine-side step runs in. Accepted for the shared signature: no
                engine-side step here is long enough to need one.
            bake_for_reference: Replaces :meth:`_bake_for_reference` --
                ``(path, project=None) -> bake path or None`` -- the bake a foreign
                row's open reads (a panel runs it in its progress, asking how a rig
                travels, and shows its errors).
            report: ``report(message)``: puts an outcome the user must see in front
                of them (a panel's message box). Default: logged only.
        """
        super().__init__()
        self.set_log_level(log_level)
        self._progress = progress
        self._report = report
        if bake_for_reference is not None:
            self._bake_for_reference = bake_for_reference
        self._workspace_files = None
        self._recursive_search = True
        self._ignore_empty_workspaces = True
        self._current_working_dir = None

    # ------------------------------------------------------------------ workspace
    @property
    def current_workspace(self) -> str:
        """The session's workspace root (``EnvUtils.workspace_root``: the pin, else
        the open file's), or '' with neither. Unlike mayatk's -- Maya always has a
        project, its default one at worst -- there is no Documents fallback: a
        browser scans the folder it is given for workspaces, recursively, and a
        whole personal folder is no project."""
        try:
            root = EnvUtils.workspace_root()
        except Exception:  # noqa: BLE001 - no Blender (the Qt-only harness)
            root = ""
        return os.path.normpath(root) if root and os.path.isdir(root) else ""

    @property
    def current_working_dir(self) -> str:
        """The folder being browsed (the session's workspace until one is set; ''
        with neither)."""
        if self._current_working_dir is None or not os.path.isdir(
            self._current_working_dir
        ):
            self._current_working_dir = self.current_workspace or None
        return self._current_working_dir or ""

    @current_working_dir.setter
    def current_working_dir(self, value):
        """Set the folder being browsed and rescan its workspaces."""
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_files()

    @property
    def recursive_search(self) -> bool:
        """Whether workspace discovery searches below the root's immediate children."""
        return self._recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        self._recursive_search = value
        self.invalidate_workspace_files()

    @property
    def ignore_empty_workspaces(self) -> bool:
        """Whether a workspace holding no scene is left out of discovery."""
        return self._ignore_empty_workspaces

    @ignore_empty_workspaces.setter
    def ignore_empty_workspaces(self, value):
        self._ignore_empty_workspaces = value
        self.invalidate_workspace_files()

    @property
    def workspace_files(self) -> dict:
        """``{workspace: [scene paths]}`` for the workspaces under the browsed folder."""
        if self._workspace_files is None:
            self.invalidate_workspace_files()
        return self._workspace_files

    def find_available_workspaces(self, root_dir: Optional[str] = None) -> list:
        """The workspaces under *root_dir* (default: the browsed folder) as
        ``(name, path)`` -- marked ones (a ``workspace.mel``) and folders holding
        .blend files (``EnvUtils.find_workspaces``). Mirror of mayatk's."""
        if root_dir is None:
            root_dir = self.current_working_dir
        if not root_dir or not os.path.isdir(root_dir):
            return []
        found = []
        for path in EnvUtils.find_workspaces(root_dir, recursive=self.recursive_search):
            if self.ignore_empty_workspaces and not self._scene_files(path):
                continue
            found.append((os.path.basename(os.path.normpath(path)) or path, path))
        return found

    def invalidate_workspace_files(self):
        """Scan for workspaces and rebuild the file cache (mirror of mayatk's)."""
        self._workspace_files = {}
        for _name, path in self.find_available_workspaces():
            if os.path.isdir(path):
                self._workspace_files[path] = self._scene_files(path)

    @staticmethod
    def _scene_files(workspace: str) -> List[str]:
        """Every .blend under *workspace*, its scene-rule folder included even when
        that lies outside it (a shared project's ``scenes/``)."""
        files = EnvUtils.find_blend_files(workspace, recursive=True)
        scene_dir = EnvUtils.workspace_scenes_dir(workspace)
        if scene_dir:
            seen = {os.path.normcase(p) for p in files}
            files += [
                p
                for p in EnvUtils.find_blend_files(scene_dir, recursive=True)
                if os.path.normcase(p) not in seen
            ]
        return files

    # ------------------------------------------------------------------ references
    @property
    def current_references(self) -> List[_LibraryRef]:
        """The file's directly linked libraries, as references ([] without Blender).
        A library linked only through another (``Library.parent``) is that one's
        content, as a nested reference is in Maya: it is no row's to remove."""
        bpy = _bpy()
        if bpy is None:
            return []
        return [
            _LibraryRef(lib)
            for lib in bpy.data.libraries
            if getattr(lib, "parent", None) is None
        ]

    @classmethod
    def bake_source(cls, linked_path) -> Optional[str]:
        """The foreign scene *linked_path* was baked from, or None if it is not a bake
        -- how a library reading a bake maps back to the row the user sees."""
        if not linked_path:
            return None
        try:
            importer = cls._importer()
        except ImportError:  # bridge unavailable: no row can be bake-backed
            return None
        return importer.bake_source(linked_path)

    @classmethod
    def row_key(cls, linked_path) -> str:
        """Normalized path of the row a library reading *linked_path* stands for: its
        bake's source, else the file itself (mirror of mayatk's)."""
        return cls._bake_source_key(linked_path) or os.path.normcase(
            os.path.normpath(linked_path)
        )

    def add_reference(self, namespace: str, file_path: str) -> bool:
        """Link *file_path*'s collections (``EnvUtils.link_blend_file``) -- the Blender
        reference. *namespace* is accepted for mayatk's signature: a library takes its
        file's name. A bake (a foreign row's) is a project file, linked ``//``-relative
        so the file keeps it when the project moves.

        Returns:
            (bool): True once *file_path* is linked, also when it already was.
        """
        if not (file_path and os.path.isfile(file_path)):
            self.logger.error(f"File not found: {file_path}")
            return False
        row = self.row_key(file_path)
        if any(self.row_key(ref.path) == row for ref in self.current_references):
            return True
        baked = bool(self.bake_source(file_path))
        linked = EnvUtils.link_blend_file(file_path, link=True, relative=baked)
        if linked and baked:
            # A Maya DAG draws a dashed parent line per object (see the bridge's note).
            self._importer().hide_relationship_lines()
        return bool(linked)

    def remove_references(self, namespaces=None) -> list:
        """Remove the libraries named *namespaces* (all when None) and everything
        linked from them. Returns the references that could NOT be removed (mirror
        of mayatk's)."""
        refs = self.current_references
        if namespaces is not None:
            wanted = set(ptk.make_iterable(namespaces))
            refs = [ref for ref in refs if ref.namespace in wanted]
        failed = []
        for ref in refs:
            if not EnvUtils.remove_library(ref.library):
                self.logger.warning(f"Failed to remove library '{ref.label}'.")
                failed.append(ref)
        return failed

    def import_references(
        self, namespaces=None, namespace_mode="remove", scene_data="merge"
    ) -> int:
        """Make the libraries named *namespaces* (all when None) local -- Blender's
        unlink and import (``EnvUtils.make_library_local``; *scene_data* as there and
        as mayatk's). *namespace_mode*: :attr:`NAMESPACE_MODES`. Returns the number of
        datablocks made local."""
        if namespace_mode not in self.NAMESPACE_MODES:
            raise ValueError(
                f"Invalid namespace_mode {namespace_mode!r}; "
                f"expected one of {self.NAMESPACE_MODES}"
            )
        refs = self.current_references
        if namespaces is not None:
            wanted = set(ptk.make_iterable(namespaces))
            refs = [ref for ref in refs if ref.namespace in wanted]
        total = 0
        for ref in refs:
            total += EnvUtils.make_library_local(ref.library, scene_data=scene_data)
        self.logger.info(f"Made {total} datablock(s) local.")
        return total

    def update_references(self):
        """Reload every library from disk -- one reading a bake in temp re-homed first
        (:meth:`follow_bake_sources`). Mirror of mayatk's."""
        moved = {ref.namespace for ref in self.follow_bake_sources()}
        for ref in self.current_references:
            if ref.namespace not in moved:
                EnvUtils.reload_library(ref.library)

    def get_reference_display_mode(self, ref) -> str:
        """``"off"`` / ``"reference"`` / ``"template"`` for *ref*'s objects ("off"
        when they disagree; mirror of mayatk's)."""
        return EnvUtils.get_reference_display_mode(ref.library)

    def set_reference_display_mode(self, ref, mode: str) -> bool:
        """Set *ref*'s display override (:attr:`DISPLAY_MODES`). True if any object
        was updated."""
        return EnvUtils.set_reference_display_mode(ref.library, mode)

    def follow_bake_sources(self) -> list:
        """Relink every library that reads a bake in temp (made before 2026-10-13,
        which the temp sweep reclaims) onto the project's bake of its source
        (``MayaSceneImport.rehome_bake``), ``//``-relative. Returns the references
        moved. Mirror of mayatk's (a foreign row's bake is only re-homed here:
        following its source means a Maya conversion)."""
        bpy = _bpy()
        if bpy is None:
            return []
        from blendertk.node_utils.data_nodes import DataNodes

        moved = []
        engine = None
        for ref in self.current_references:
            path = ref.path
            if not (path and ptk.TempArtifacts.contains(path)):
                continue
            if not self.bake_source(path):
                continue
            project = self.bake_project_root()
            if not project:
                continue
            engine = engine or self._importer()()
            try:
                target = engine.rehome_bake(path, project=project)
            except (OSError, RuntimeError) as e:
                self.logger.warning(f"Could not move {path} out of temp: {e}")
                continue
            if not target:
                continue
            # //-relative where a relative path reaches it (never across drives).
            ref.library.filepath = DataNodes._blend_relpath(target)
            ref.library.reload()
            moved.append(ref)
        if moved:
            self.logger.info(f"Moved {len(moved)} linked bake(s) out of temp.")
        return moved

    def purge_unused_bakes(self, dry_run: bool = False) -> list:
        """Remove the bakes in the project that no file links
        (``MayaSceneImport.purge_unused_bakes``). Returns the paths removed."""
        return self._importer()().purge_unused_bakes(
            project=self.bake_project_root(), dry_run=dry_run
        )

    # ------------------------------------------------------------------ foreign rows
    @classmethod
    def is_foreign(cls, path) -> bool:
        """True if *path* is a foreign (Maya / FBX / USD) scene -- a row baked to a
        ``.blend`` before it can be linked or opened."""
        return (
            bool(path)
            and os.path.splitext(str(path))[1].lower() in cls.FOREIGN_EXTENSIONS
        )

    @classmethod
    def needs_conversion(cls, path) -> bool:
        """True if bringing *path* in runs a conversion in the other DCC (a headless
        Maya for a ``.ma``/``.mb``); an ``.fbx`` or USD source is the bake's own
        input, so it converts nothing and has nothing to ask."""
        try:
            from blendertk.env_utils.maya_bridge._scene_import import (
                SUPPORTED_EXTENSIONS as MAYA_SCENES,
            )
        except ImportError:
            MAYA_SCENES = (".ma", ".mb")
        return bool(path) and os.path.splitext(str(path))[1].lower() in MAYA_SCENES

    @classmethod
    def scene_has_complex_animation(cls, path: str) -> bool:
        """True if the foreign scene *path* declares driven animation a raw import
        would lose (``MayaSceneImport.scene_has_complex_animation``)."""
        return cls._importer().scene_has_complex_animation(path)

    @classmethod
    def find_foreign_scenes(
        cls, root_dir: str, recursive: bool = False, extensions=None
    ) -> List[str]:
        """The foreign scenes under *root_dir* (``MayaSceneImport.find_scenes``)."""
        return cls._importer().find_scenes(
            root_dir, recursive=recursive, extensions=extensions
        )

    def bake_scene(self, path: str, **kwargs) -> str:
        """Bake the foreign scene *path* to a cached ``.blend`` and return it
        (``MayaSceneImport.bake_scene``: ``via``, ``rig_mode``, ``progress``,
        ``project``, ...)."""
        return self._importer()().bake_scene(path, **kwargs)

    def import_scene(self, path: str, **kwargs) -> list:
        """Import the foreign scene *path* as local data with no link behind it
        (``MayaSceneImport.import_scene``). Returns what it brought in."""
        return self._importer()().import_scene(path, **kwargs)

    def bake_project_root(self) -> Optional[str]:
        """The project a reference's bake goes in: the open file's
        (``DataNodes.scene_project_root``), else the folder being browsed; None when
        neither resolves (the bake then refuses: a bake a saved file links is a
        project file, never a temp one)."""
        try:
            from blendertk.node_utils.data_nodes import DataNodes

            root = DataNodes.scene_project_root()
        except Exception:  # noqa: BLE001 - no Blender (the Qt-only harness)
            root = None
        root = root or self._current_working_dir
        return root if root and os.path.isdir(root) else None

    def _bake_for_reference(self, file_path: str, project=None) -> Optional[str]:
        """The cached ``.blend`` a foreign row's open reads, or None with a warning.
        A hook: a panel runs it in its progress (``bake_for_reference=``)."""
        try:
            return self.bake_scene(
                file_path, project=project or self.bake_project_root()
            )
        except Exception as e:  # noqa: BLE001 - reported; nothing is opened
            self.logger.warning(f"Could not bake {file_path}: {e}")
            return None

    @staticmethod
    def foreign_scratch_path(path: str) -> str:
        """The deterministic scratch ``.blend`` a foreign row opens as
        (``<temp>/btk_opened_<hash>/<stem>_<ext>.blend``: ``scene.ma`` opens as
        ``scene_ma.blend``, so the title bar says what it was converted from).
        ``ptk.ScratchTwins`` owns the naming, the age sweep and the discard."""
        return _scratch_twins().path_for(path)

    def discard_stale_scratches(self):
        """Discard every untouched scratch twin that is no longer the open file (one
        the user saved into is kept: ``ptk.ScratchTwins.discard_except``)."""
        _scratch_twins().discard_except(self.current_scene())

    # ------------------------------------------------------------------ scene files
    def current_scene(self) -> str:
        """The open .blend's path; '' when it has never been saved, or without
        Blender. Authored casing kept."""
        bpy = _bpy()
        return (bpy.data.filepath or "") if bpy is not None else ""

    def scene_modified(self) -> bool:
        """True if replacing the open file would lose work
        (``EnvUtils.scene_has_unsaved_changes``: the dirty flag follows the undo
        stack, so an empty never-saved scene does not count). False without
        Blender."""
        if _bpy() is None:
            return False
        return EnvUtils.scene_has_unsaved_changes()

    def save_open_scene(self) -> Optional[bool]:
        """Flush the open file to its own path so its unsaved edits reach disk.

        Returns True when the file on disk is up to date, and None when the file has
        never been saved (a browser then saves it under a new name instead).

        Raises:
            RuntimeError: The save failed.
        """
        if not self.current_scene():
            return None
        if not self.scene_modified():
            return True
        if not EnvUtils._save_open_file():
            raise RuntimeError("The open file could not be saved.")
        self.logger.info(f"Saved the open file: {self.current_scene()}")
        return True

    def save_scene(self, path: str) -> str:
        """Save the open file as *path* (``wm.save_as_mainfile``); the session follows
        it. Returns the saved path; raises RuntimeError on failure."""
        bpy = _bpy()
        if bpy is None:
            raise RuntimeError("Saving a scene needs a running Blender.")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        path = os.path.normpath(path)
        self.logger.info(f"Saved scene to: {path}")
        return path

    def new_scene(self) -> bool:
        """Discard the open file and start an empty one (Maya's ``file -new``) -- the
        'close' counterpart of :meth:`open_scene`. Callers guard unsaved changes first.
        An untouched scratch twin the file was goes with it. True on success."""
        if not EnvUtils.new_scene():
            self.logger.error("Failed to start a new scene.")
            return False
        self.logger.info("Started a new (empty) scene.")
        self.discard_stale_scratches()
        return True

    def is_current(self, path, current=None) -> bool:
        """True if *path*'s scene is the open one: the file itself, or -- a foreign
        row -- its scratch twin (so a second Open click on either closes it). Pass
        *current* (normalized) to reuse one read across a listing. Mirror of mayatk's."""
        if not path:
            return False
        if current is None:
            scene = self.current_scene()
            current = os.path.normcase(os.path.normpath(scene)) if scene else ""
        if not current:
            return False
        target = self.foreign_scratch_path(path) if self.is_foreign(path) else path
        return os.path.normcase(os.path.normpath(target)) == current

    def open_scene(self, file_path: str, set_workspace: bool = True) -> bool:
        """Open *file_path* (replaces the file), pinning the session's workspace to
        the scene's project (``ptk.Workspace.for_scene``, the folder being browsed as
        the session root).

        A foreign row has no ``.blend`` to open: its bake (:meth:`_bake_for_reference`,
        baked into the SOURCE's project -- the opened file is what reads it) is copied
        to a scratch twin (:meth:`foreign_scratch_path`) that is opened as a new file
        standing in for its source (``DataNodes.set_scene_source``), so the project's
        bake itself is never edited. Mirror of mayatk's.

        Returns:
            (bool): True once opened; False when a foreign row's bake did not come
            (reported by the bake).

        Raises:
            FileNotFoundError: *file_path* does not exist.
            OSError: A foreign row's scratch copy could not be written.
            RuntimeError: Blender could not open it.
        """
        source = file_path
        if self.is_foreign(file_path):
            home = ptk.Workspace.for_scene(
                file_path, session_root=self._current_working_dir or None
            )
            baked = self._bake_for_reference(
                file_path, project=home.root if home is not None else None
            )
            if not baked:
                return False
            try:
                file_path = _scratch_twins().create(source, baked)
            except OSError as e:
                raise OSError(
                    f"the working copy of its bake could not be written ({e})"
                ) from e
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Scene file not found: {file_path}")
        if not EnvUtils.open_scene(file_path):
            raise RuntimeError(f"Failed to open {os.path.basename(file_path)}.")
        self.logger.info(f"Opened scene: {file_path}")
        if file_path != source:
            from blendertk.node_utils.data_nodes import DataNodes

            importer = self._importer()
            # A copy of the bake opened as a document of its own is no bake: its
            # record goes (else it rides along into whatever the user saves).
            importer._strip_bake_record()
            # The scratch stands in for its source: a lightmap bake's project is the
            # source's, not the temp folder (DataNodes.scene_project_root).
            DataNodes.set_scene_source(file_path, source)
        self.discard_stale_scratches()
        if set_workspace:
            home = ptk.Workspace.for_scene(
                source, session_root=self._current_working_dir or None
            )
            if home is not None:
                EnvUtils.set_current_workspace(home.root)
                self.logger.info(f"Workspace: {home.root}")
        if file_path != source:
            # Held past UiState's re-apply of the saved overlays, which the load queued.
            self._importer().hide_relationship_lines()
        return True

    def rename_scene_file(self, old_path: str, new_path: str, folder=None):
        """Rename the scene file *old_path* to *new_path* (same folder) on disk, its
        ``.blend1`` backup along (``EnvUtils.rename_scene_file``): renaming the OPEN
        file is save-then-reopen. Returns the final path, or None when the rename was
        refused (a name clash, or the open file could not be saved).

        *folder* (the per-scene folder's new name, mayatk's ``{name}`` structure) is
        accepted for mayatk's signature; Blender's rename leaves the folder as it is.
        """
        new_base = os.path.splitext(os.path.basename(new_path))[0]
        renamed = EnvUtils.rename_scene_file(old_path, new_base)
        if renamed:
            self.logger.info(f"Renamed {old_path} to {renamed}")
        return renamed

    def remove_scene_file(
        self, path: str, permanent: bool = False, use_folder: bool = False
    ) -> Optional[bool]:
        """Remove one scene with its ``.blend1`` backup: to the trash, or --
        *permanent* -- for good (``EnvUtils.delete_scene_file``).

        Returns:
            True when it is gone; ``None`` when the trash would not take it (nothing
            touched); False when it could not be removed. *use_folder* is accepted
            for mayatk's signature (an emptied per-scene folder stays here).
        """
        if EnvUtils.delete_scene_file(path, permanent=permanent):
            where = (
                "Deleted" if permanent else f"Moved to the {ptk.FileUtils.trash_name()}"
            )
            self.logger.info(f"{where}: {path}")
            return True
        if not permanent and os.path.isfile(path):
            return None  # the trash would not take it: untouched
        return False


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
