# !/usr/bin/python
# coding=utf-8
"""blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.

Maya *file references* map onto Blender **linked libraries** (``bpy.data.libraries`` — File ▸ Link).
This module is the Qt-free, bpy-only engine (unit-testable headless): discover ``.blend`` files under
a folder, list the libraries already linked, link a ``.blend``'s collections (or append a copy), and
reload / remove a library. ``ReferenceManager`` (``reference_manager.py``) composes these for a
browser; its panel is tentacle's shared Reference Manager.

``import bpy`` is deferred into the call bodies (importing the package surface must not need a
running Blender — the no-import-side-effects rule).
"""

import os
from typing import Optional

import pythontk as ptk


# ----------------------------------------------------------------- workspace / scene files
# The Blender analogue of Maya's project workspace, built on ``pythontk.Workspace``. A workspace
# is a project folder, either *marked* (a ``workspace.mel`` at its root — a shared Maya/Blender
# project whose file rules say where scenes/textures live) or *unmarked* (a directory directly
# holding .blend files — zero-ceremony Blender-alone projects, promotable via
# ``promote_workspace``). These back the Reference Manager's workspace combo + scene table +
# open/save/rename/delete operations and the package-wide current-workspace resolver (no bpy
# except open/save and the current-file lookup → testable on disk).

# The session pin (Blender has no native ``workspace -o``) is held by
# ``DataNodes._session_root``: the project rule resolves there, below every tool.


# --- workspace templates (named file-rule sets for building NEW workspaces) -----------------
# The store itself is `ptk.WorkspaceTemplates` — unnamespaced and shared with mayatk, because a
# workspace.mel is a shared project: a template saved from the Workspace Editor is equally what
# `mtk.create_workspace` builds from. The `btk.*_workspace_template*` names below are the thin
# mirror surface (twins of `mtk.*`), not a second store.


# ----------------------------------------------------------------- reference display modes
# Per-reference display override (Maya's overrideEnabled/overrideDisplayType tri-state) → Blender's
# per-object display_type + hide_select on the objects/instances belonging to a linked library.
_DISPLAY_MODES = ("off", "reference", "template")
# Object types Blender's default document ships with — a scene holding only these (and no linked
# libraries) is not work worth guarding. See EnvUtils.scene_has_content.
_DEFAULT_DOC_OBJECT_TYPES = frozenset({"CAMERA", "LIGHT"})


class _EnvUtilsInternal(object):
    """Internal helpers for EnvUtils."""

    @staticmethod
    def _abspath(filepath):
        """Absolute, normalized path of a (possibly ``//`` project-relative) library path, or ''."""
        import bpy

        if not filepath:
            return ""
        try:
            return os.path.normpath(bpy.path.abspath(filepath))
        except Exception:
            return os.path.normpath(filepath)

    @staticmethod
    def _split_filter(filter_text):
        """Split a comma/semicolon filter string into a list of wildcard patterns."""
        patterns = [filter_text]
        for delim in (",", ";"):
            expanded = []
            for p in patterns:
                expanded.extend(s.strip() for s in p.split(delim) if s.strip())
            patterns = expanded
        return patterns or [filter_text]

    @staticmethod
    def _is_unsaved_work(is_dirty, is_saved, has_content):
        """Decide whether replacing the open file would lose work, from the three facts
        :meth:`EnvUtils.scene_has_unsaved_changes` gathers (kept pure so it is testable headless,
        where the dirty flag can neither be set nor cleared)."""
        if not is_dirty:
            return False
        return bool(is_saved or has_content)

    @staticmethod
    def _is_open_file(path):
        """True if *path* is the .blend open in this session (False without bpy / unsaved file)."""
        try:
            import bpy
        except ImportError:  # no running Blender — nothing can be open
            return False

        current = bpy.data.filepath
        if not (current and path):
            return False
        return os.path.normcase(os.path.normpath(current)) == os.path.normcase(
            os.path.normpath(path)
        )

    @staticmethod
    def _save_open_file():
        """Flush the open file to disk so a pending rename carries the user's unsaved edits.
        True when the file on disk is up to date, False when the save failed (caller aborts —
        renaming a file out from under an unsaved session loses the edits at the next save)."""
        import bpy

        try:
            bpy.ops.wm.save_mainfile()
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _library_objects(lib):
        """Scene objects belonging to ``lib`` — directly linked objects + the local collection-instance
        empties that instance one of the library's linked collections."""
        import bpy

        objs = []
        for o in bpy.data.objects:
            if o.library == lib:
                objs.append(o)
            elif (
                o.instance_type == "COLLECTION"
                and o.instance_collection is not None
                and o.instance_collection.library == lib
            ):
                objs.append(o)
        return objs

    @staticmethod
    def _write_scene_fbx(file_path, options, progress):
        """Export Scene's FBX: ``FbxUtils.export``, the toggles mapped to its
        ``object_types`` / ``use_tspace`` / ``path_mode``."""
        from blendertk.env_utils.fbx_utils import FbxUtils

        object_types = {"MESH", "EMPTY", "OTHER"}
        if options["include_cameras"]:
            object_types.add("CAMERA")
        if options["include_lights"]:
            object_types.add("LIGHT")
        if options["include_skins"]:
            object_types.add("ARMATURE")
        FbxUtils.export(
            filepath=file_path,
            selection_only=options["selection_only"],
            object_types=object_types,
            use_tspace=options["include_tangents"],
            path_mode="COPY" if options["embed_textures"] else "AUTO",
            embed_textures=options["embed_textures"],
        )

    @staticmethod
    def _write_scene_obj(file_path, options, progress):
        """Export Scene's OBJ: ``export_scene_as_obj``, its ``.mtl`` with the maps."""
        EnvUtils.export_scene_as_obj(
            file_path=file_path,
            selection_only=options["selection_only"],
            materials=options["embed_textures"],
        )

    @staticmethod
    def _write_scene_usd(file_path, options, progress):
        """Export Scene's USD: the interchange set of the Scene Exporter's USD format,
        the toggles mapped to the exporter's own switches."""
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter
        from blendertk.env_utils.usd import UsdUtils

        usd_options = dict(SceneExporter.USD_EXPORT_OPTIONS)
        usd_options.update(
            export_cameras=options["include_cameras"],
            export_lights=options["include_lights"],
            export_armatures=options["include_skins"],
        )
        objects = (
            list(CoreUtils.selected_objects())  # the view layer's: windowless-safe
            if options["selection_only"]
            else list(bpy.context.scene.objects)
        )
        frame_range = UsdUtils.sampling_frame_range(objects)
        if frame_range:
            usd_options["export_animation"] = True
        UsdUtils.export(
            filepath=file_path,
            selection_only=options["selection_only"],
            frame_range=frame_range,
            **usd_options,
        )

    @staticmethod
    def _write_scene_glb(file_path, options, progress):
        """Export Scene's GLB: Blender's own glTF exporter, no FBX hop.

        The scene's COMMITTED lightmaps ride the GLB (``LightmapWebExport.
        wired_for_export``: wired for the export's lifetime, restored after, a no-op
        on an unbaked scene), and the written file is finished as mayatk's FBX2glTF
        route's is. Under ``window_context_override``: the bundled exporter calls
        ``context.window.cursor_set``, an AttributeError windowless.
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.light_utils.lightmap_baker.web_export import LightmapWebExport

        with LightmapWebExport().wired_for_export(glb_path=file_path):
            with CoreUtils.window_context_override():
                bpy.ops.export_scene.gltf(
                    filepath=file_path,
                    export_format="GLB",
                    use_selection=options["selection_only"],
                    export_cameras=options["include_cameras"],
                    export_lights=options["include_lights"],
                    export_extras=True,  # carries the lightmaps block
                )


class EnvUtils(_EnvUtilsInternal):
    """Namespace mirror of mayatk's ``EnvUtils`` (helpers also exposed module-level)."""

    #: The host application's display name, for UI text naming the app a panel
    #: drives ("Blender Preferences"). Mirror of mayatk's ``EnvUtils.APP_NAME``.
    APP_NAME = "Blender"

    #: Linear unit (mayatk's full name, the reference) -> its short name, for the
    #: units a Blender scene works in: its metric and imperial length units
    #: (Maya's yard has none). Mirror of mayatk's ``EnvUtils.SCENE_UNIT_VALUES``.
    SCENE_UNIT_VALUES = {
        "millimeter": "mm",
        "centimeter": "cm",
        "meter": "m",
        "kilometer": "km",
        "inch": "in",
        "foot": "ft",
        "mile": "mi",
    }

    #: Linear unit -> Blender's ``(unit system, length unit)``.
    _LENGTH_UNITS = {
        "millimeter": ("METRIC", "MILLIMETERS"),
        "centimeter": ("METRIC", "CENTIMETERS"),
        "meter": ("METRIC", "METERS"),
        "kilometer": ("METRIC", "KILOMETERS"),
        "inch": ("IMPERIAL", "INCHES"),
        "foot": ("IMPERIAL", "FEET"),
        "mile": ("IMPERIAL", "MILES"),
    }

    @staticmethod
    def find_blend_files(root_dir, recursive=True, filter_text=""):
        """Every ``.blend`` file under ``root_dir`` (recursively by default), optionally name-filtered.

        ``filter_text`` uses ``pythontk`` wildcard semantics (``*foo``, ``foo*``, ``*foo*``; comma /
        semicolon separated). Returns a sorted list of absolute paths.
        """
        if not (root_dir and os.path.isdir(root_dir)):
            return []
        found = []
        walker = (
            os.walk(root_dir)
            if recursive
            else [
                (
                    root_dir,
                    [],
                    [
                        f
                        for f in os.listdir(root_dir)
                        if os.path.isfile(os.path.join(root_dir, f))
                    ],
                )
            ]
        )
        for dirpath, _dirs, files in walker:
            for f in files:
                if f.lower().endswith(".blend"):
                    found.append(os.path.normpath(os.path.join(dirpath, f)))
        if filter_text:
            names = [os.path.basename(p) for p in found]
            kept = set(
                ptk.filter_list(names, inc=_EnvUtilsInternal._split_filter(filter_text))
            )
            found = [p for p in found if os.path.basename(p) in kept]
        return sorted(found)

    @staticmethod
    def list_libraries():
        """Every linked library as a record: ``{name, library, filepath, abspath, exists}``.
        ``library`` is the live ``bpy.types.Library`` datablock (reload / remove use it)."""
        import bpy

        records = []
        for lib in bpy.data.libraries:
            ap = _EnvUtilsInternal._abspath(lib.filepath)
            records.append(
                {
                    "name": lib.name,
                    "library": lib,
                    "filepath": lib.filepath,
                    "abspath": ap,
                    "exists": bool(ap and os.path.exists(ap)),
                }
            )
        return records

    @staticmethod
    def linked_blend_paths():
        """Set of normalized absolute paths of the ``.blend`` files currently linked as
        libraries, case-folded only where the filesystem folds case (``normcase``)."""
        return {
            os.path.normcase(r["abspath"])
            for r in EnvUtils.list_libraries()
            if r["abspath"]
        }

    @staticmethod
    def is_blend_linked(path):
        """True iff ``path`` is already linked as a library."""
        return (
            os.path.normcase(_EnvUtilsInternal._abspath(path))
            in EnvUtils.linked_blend_paths()
        )

    @staticmethod
    def link_blend_file(
        path, link=True, instance=True, target_collection=None, relative=False
    ):
        """Link (or append, ``link=False``) every collection from ``path`` and instance them into the
        active scene — the closest Blender analogue of adding a Maya file reference. Falls back to
        objects when the file has no collections. Returns the number of datablocks brought in.

        ``target_collection`` links into that collection instead of the active scene's master
        collection — used by Hierarchy Sync to sandbox the reference in a hidden, view-layer-excluded
        collection so it never clutters the outliner/viewport.

        ``relative`` stores the library path relative to the open file (``//``), so a scene
        that links a project file (a Reference Manager bake in ``cache/bridge``) keeps it when
        the project moves; an unsaved file stores it absolute until it is saved (Blender's save
        remaps it).
        """
        import bpy

        if not (path and os.path.isfile(path)):
            return 0
        # An unsaved file has no folder to be relative to: absolute until saved.
        relative = bool(relative and bpy.data.filepath)
        with bpy.data.libraries.load(path, link=link, relative=relative) as (
            data_from,
            data_to,
        ):
            data_to.collections = list(data_from.collections)
            if not data_from.collections:
                data_to.objects = list(data_from.objects)

        dest_coll = target_collection or bpy.context.scene.collection
        count = 0
        for coll in getattr(data_to, "collections", []) or []:
            if coll is None:
                continue
            if instance:
                inst = bpy.data.objects.new(coll.name, None)
                inst.instance_type = "COLLECTION"
                inst.instance_collection = coll
                dest_coll.objects.link(inst)
            else:
                dest_coll.children.link(coll)
            count += 1
        if not count:  # object fallback
            for obj in getattr(data_to, "objects", []) or []:
                if obj is not None:
                    dest_coll.objects.link(obj)
                    count += 1
        return count

    @staticmethod
    def reload_library(library):
        """Reload a library from disk (``library`` is a datablock or its name). Returns True on success."""
        import bpy

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return False
        try:
            lib.reload()
            return True
        except (RuntimeError, AttributeError):
            return False

    @staticmethod
    def remove_library(library):
        """Remove a library and everything linked from it (datablock or name). Returns True on success.

        The local collection-instance Empties that instance its collections go with it (the
        ones :meth:`link_blend_file` creates): they belong to this file, so removing the
        library alone left each one behind instancing nothing -- one more per Reference
        Manager row click, now that the row selection is the reference set.
        """
        import bpy

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return False
        try:
            instancers = [
                o for o in _EnvUtilsInternal._library_objects(lib) if o.library is None
            ]
            bpy.data.libraries.remove(lib, do_unlink=True)
        except (RuntimeError, ReferenceError):
            return False  # still linked: its instancers still show it
        for o in instancers:
            bpy.data.objects.remove(o, do_unlink=True)
        return True

    #: What a make-local does with a library's scene data (see make_library_local).
    SCENE_DATA_MODES = ("merge", "discard")

    @staticmethod
    def make_library_local(library, scene_data="merge"):
        """Make every datablock linked from ``library`` **local** (a native, editable copy) and drop the
        now-unused library — the Blender analogue of Maya's *import references* (``importContents``).

        ``library`` is a ``bpy.types.Library`` datablock or its name. Returns the number of datablocks
        made local (``0`` when *scene_data*'s ``decide`` leaves the library linked).

        *scene_data* is what becomes of the library's own scene data -- the records its tools kept
        (shots, parked keys, emissive groups), which no link brings and which a leftover
        ``data_export.001`` would otherwise hold where nothing reads it (mirror of mayatk's
        ``ReferenceManager.import_references``):

        - ``"merge"`` (default): each record merges into this file's by its declared rule
          (``DataNodes.merge_carriers``), names respelled to where the move put each object;
          whatever arrives renamed or re-slotted is logged.
        - ``"discard"``: the records go (``DataNodes.discard_carriers``).
        - ``decide(summary, name) -> "merge" | "discard" | None``: asked only when a merge would
          keep something (*summary*: one line per record); ``None`` leaves the library linked.

        The library's actions come local with it: its parked clips name them, and linking only its
        collections never reaches an action nothing in them uses.
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.node_utils.data_nodes import DataNodes

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return 0
        if not callable(scene_data) and scene_data not in EnvUtils.SCENE_DATA_MODES:
            raise ValueError(
                f"Invalid scene_data {scene_data!r}; expected one of "
                f"{EnvUtils.SCENE_DATA_MODES} or a callable"
            )
        # the library goes with the move; bpy.path: a '//'-relative path is a UNC
        # share to os.path on Windows, whose basename is empty
        source = bpy.path.basename(lib.filepath)
        # Its path records are spelled from ITS project, read before it goes.
        source_project = DataNodes.project_root_of(bpy.path.abspath(lib.filepath))
        carriers = DataNodes.carriers_in(lib)
        decision = "merge"
        if carriers:
            decision = scene_data
            if callable(scene_data):
                plan = DataNodes.merge_plan(carriers)
                decision = (
                    "merge" if plan.is_empty else scene_data(plan.summary(), lib.name)
                )
                if decision is None:
                    return 0
                if decision not in EnvUtils.SCENE_DATA_MODES:
                    raise ValueError(
                        f"scene_data decided {decision!r}; expected one of "
                        f"{EnvUtils.SCENE_DATA_MODES} or None"
                    )
            # What this file's stores hold unwritten goes to its records before
            # anything moves: the settle reads the records (mirror of mayatk's
            # import_references).
            DataNodes.flush_owners()
            path = bpy.path.abspath(lib.filepath)
            # Every action this make-local brings -- linked below, a copy
            # `make_local` returns for a clash included -- is new to the file
            # from here on; the wrappers in `named` may not outlive the move.
            before_actions = {action.session_uid for action in bpy.data.actions}
            if os.path.isfile(path):
                # The actions the parked clips name, and only those: every
                # other action of the library would come local with it and
                # stay (a fake-user one for good), asked for by nothing.
                spec = ptk.SceneRecords.KEY_STASH
                raw = (
                    getattr(carriers.get(ptk.Scope.PRIVATE), "values", None) or {}
                ).get(spec.key)
                stash = (spec.decode(raw) if isinstance(raw, str) else raw) or {}
                parked = {
                    rec.get("action")
                    for clip in stash.get("clips") or []
                    for rec in clip.get("curves") or []
                }
                # Not `source`: that names the library in every note below.
                with bpy.data.libraries.load(path, link=True) as (held, wanted):
                    wanted.actions = [a for a in held.actions if a in parked]
        named = [
            (db, db.name)
            for kind in (bpy.data.objects, bpy.data.actions)
            for db in kind
            if db.library == lib
        ]
        count = 0
        # `id.make_local()` clears a datablock's `.library` pointer in place -- every ID collection,
        # so linked meshes/materials/etc. come local too, and pass after pass until one localizes
        # nothing more: `make_local` is a silent no-op on an ID whose only user is still linked (a
        # mesh under a linked object), and `dir()` order visits meshes before objects. One pass
        # left such a mesh linked, and dropping the library then took it AND the made-local object
        # using it (measured 2026-09-19: every mesh object of the library vanished).
        while True:
            localized = 0
            for db in CoreUtils.all_ids(lib):
                try:
                    db.make_local()
                except (RuntimeError, ReferenceError, AttributeError):
                    continue
                if getattr(db, "library", None) is None:
                    localized += 1
            if not localized:
                break
            count += localized
        # Dropped only once nothing of it is left linked: removing a library takes every
        # datablock still linked from it, and whatever uses them.
        if count and not CoreUtils.all_ids(lib):
            try:
                bpy.data.libraries.remove(lib, do_unlink=True)
            except (RuntimeError, ReferenceError):
                pass
        if carriers:
            # Before anything writes a private record: under its canonical name the made-local
            # legacy Empty would fold into this file's records and replace them.
            DataNodes._drop_legacy_carrier(carriers)
            settle = (
                DataNodes.merge_carriers
                if decision == "merge"
                else DataNodes.discard_carriers
            )
            settle(
                carriers,
                rename=DataNodes.library_renames(named),
                source=source,
                source_path_base=source_project,
                # What the library brought, for an owner that removes on
                # discard: a name alone can be this file's own datablock.
                adapters={
                    "library_actions": {
                        action
                        for action in bpy.data.actions
                        if action.session_uid not in before_actions
                    }
                },
            )
        return count

    @staticmethod
    def set_current_workspace(root=None):
        """Pin (or clear, with None) the session's current workspace — the Blender analogue of
        Maya's ``workspace -o``. Returns the pinned root (or None when cleared)."""
        from blendertk.node_utils.data_nodes import DataNodes

        DataNodes._session_root = os.path.normpath(root) if root else None
        return DataNodes._session_root

    @staticmethod
    def current_workspace(path=None):
        """The active ``pythontk.Workspace``, or None.

        Ambient resolution (``path=None``): the session pin (:func:`set_current_workspace`)
        → the nearest marked (``workspace.mel``) root containing the saved .blend → the
        .blend's own folder as an unmarked workspace → None (nothing saved, nothing pinned).

        An explicit *path* resolves THAT path (marked ancestor → its own folder) and never
        answers with the unrelated session pin — the pin is global state, like Maya's
        ``workspace -o``, and only governs the ambient chain."""
        if path is None:
            from blendertk.node_utils.data_nodes import DataNodes

            pin = DataNodes._session_pin()
            if pin:
                return ptk.Workspace.load(pin)
            try:
                import bpy

                path = bpy.data.filepath
            except ImportError:  # headless .venv — no bpy, no open file
                path = ""
        return ptk.Workspace.for_path(path)

    @staticmethod
    def workspace_root(path=None):
        """Absolute root of the current workspace, or '' — what ``get_env_info("workspace")``
        reports."""
        ws = EnvUtils.current_workspace(path)
        return ws.root if ws else ""

    # The scene-source family's one owner is DataNodes (2026-10-25); these
    # retired doors forward there for one release.
    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.set_scene_source", remove_in="0.23.0", since="2026-10-25"
    )
    def set_scene_source(scene: str, source: Optional[str] = None) -> Optional[str]:
        """Moved to :meth:`DataNodes.set_scene_source`, which records the stand-in
        and arms its save."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.set_scene_source(scene, source)

    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.twin_save_target", remove_in="0.23.0", since="2026-10-25"
    )
    def twin_save_target(scene: Optional[str] = None) -> Optional[str]:
        """Moved to :meth:`DataNodes.twin_save_target`."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.twin_save_target(scene)

    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.scene_source", remove_in="0.23.0", since="2026-10-25"
    )
    def scene_source(scene: Optional[str] = None) -> Optional[str]:
        """Moved to :meth:`DataNodes.scene_source`."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_source(scene)

    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.scene_project_root", remove_in="0.23.0", since="2026-10-25"
    )
    def scene_project_root() -> Optional[str]:
        """Moved to :meth:`DataNodes.scene_project_root`."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_project_root()

    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.scene_workspace", remove_in="0.23.0", since="2026-10-25"
    )
    def scene_workspace() -> Optional[ptk.Workspace]:
        """Moved to :meth:`DataNodes.scene_workspace`."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_workspace()

    @staticmethod
    @ptk.Deprecation.symbol(
        "DataNodes.scene_source_images_dir", remove_in="0.23.0", since="2026-10-25"
    )
    def scene_source_images_dir(subfolder: str = "", create: bool = False) -> str:
        """Moved to :meth:`DataNodes.scene_source_images_dir`."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_source_images_dir(subfolder, create=create)

    @staticmethod
    def scene_artifact_path(suffix: str) -> str:
        """A file named for the open .blend, beside it: ``<blend dir>/<stem><suffix>``.

        An unsaved file is ``untitled`` in the current workspace
        (:meth:`workspace_root`). The suggested path a Save dialog opens on.
        Mirror of mayatk's.
        """
        try:
            import bpy

            blend = bpy.data.filepath or ""
        except ImportError:  # headless .venv -- no bpy, no open file
            blend = ""
        stem = os.path.splitext(os.path.basename(blend))[0] or "untitled"
        folder = os.path.dirname(blend) or EnvUtils.workspace_root()
        return os.path.normpath(os.path.join(folder, stem + suffix))

    @staticmethod
    def source_images_dir(path=None):
        """The current workspace's texture folder — its ``sourceImages`` rule → an existing
        ``sourceimages``/``textures`` folder → ``sourceimages`` (``ptk.Workspace``'s, the
        same folder mayatk names: one project tree for both DCCs; an existing ``textures/``
        still matches). '' when there is no current workspace."""
        ws = EnvUtils.current_workspace(path)
        if ws is None:
            return ""
        return ws.source_images_dir

    @staticmethod
    def texture_search_dirs(path=None):
        """Where this scene's map files can be found NOW, most specific first.

        The workspace's texture folder then the .blend's own folder, absolute,
        de-duplicated, and filtered to directories that actually exist. Mirror
        of mayatk's :meth:`EnvUtils.texture_search_dirs`.

        Exists because several consumers resolve a map by BASENAME against a
        recorded authoring directory -- most visibly the GLB lightmap applier,
        whose manifest stores the folder the bake was committed from. That
        record is history, not a contract: reorganise the project and every
        lookup misses, so the deliverable ships unlit while the EXRs sit one
        folder away. The host knows where its textures are today; this is that
        answer, in one place.
        """
        import bpy

        # isdir before dirname: ``current_workspace`` (which resolves the first
        # candidate) takes *path* as either a file or a folder, and a blind
        # dirname on a folder yields its PARENT.
        scene = path or bpy.data.filepath or ""
        candidates = [
            EnvUtils.source_images_dir(path),
            scene if os.path.isdir(scene) else os.path.dirname(scene),
        ]
        seen, dirs = set(), []
        for candidate in candidates:
            resolved = os.path.abspath(candidate) if candidate else ""
            if resolved and resolved not in seen and os.path.isdir(resolved):
                seen.add(resolved)
                dirs.append(resolved)
        return dirs

    @staticmethod
    def scenes_dir(path=None):
        """The current workspace's scene folder (``scene`` rule → existing ``scenes`` → the root),
        or ''."""
        ws = EnvUtils.current_workspace(path)
        return ws.scene_dir if ws else ""

    @staticmethod
    def workspace_scenes_dir(root):
        """The scene-rule folder of a *marked* workspace at ``root`` (absolute), or '' when
        ``root`` is unmarked / the rule resolves to the root itself — lets callers extend a flat
        folder scan into a shared project's ``scenes/`` without double-listing anything."""
        if not (root and os.path.isdir(root)):
            return ""
        ws = ptk.Workspace.load(root)
        if not ws.is_marked:
            return ""
        sd = ws.scene_dir
        return "" if os.path.normcase(sd) == os.path.normcase(ws.root) else sd

    @staticmethod
    def list_workspace_templates():
        """Saved workspace-template names (the Workspace Editor's Save Template entries) —
        the store is shared with mayatk, so Maya-saved templates list here too."""
        return ptk.WorkspaceTemplates.list()

    @staticmethod
    def workspace_template_rules(name=None):
        """File rules for building a NEW workspace: the *name*d (default: active / last-saved)
        template, falling back to the standard ``ptk.DEFAULT_FILE_RULES``. Seeds
        :func:`create_workspace` and the Workspace Editor's fresh-path definition."""
        return ptk.WorkspaceTemplates.rules(name)

    @staticmethod
    def save_workspace_template(name, rules=None):
        """Save *rules* as workspace template *name* and make it the active default for new
        workspaces. ``rules=None`` captures the CURRENT workspace's own rules — publishing a
        hand-tuned project layout as the studio template. Returns the saved name."""
        if rules is None:
            ws = EnvUtils.current_workspace()
            rules = dict(ws.rules) if ws is not None else {}
            if not rules:
                raise ValueError(
                    "No file rules to save — the current workspace has no workspace.mel rules."
                )
        return ptk.WorkspaceTemplates.save(name, rules)

    @staticmethod
    def delete_workspace_template(name):
        """Delete the user template *name* (the store keeps the active pointer consistent).
        True when a file was removed."""
        return ptk.WorkspaceTemplates.delete(name)

    @staticmethod
    def create_workspace(root, rules=None, create_dirs=True):
        """Create a marked workspace at ``root`` — the Blender counterpart of Maya's File ▸
        Project Window ▸ New. ``rules=None`` seeds from :func:`workspace_template_rules` (the
        active saved template, else the Maya-standard defaults) and creates the rule subfolders.
        Idempotent on an existing project (its rules win). Returns the ``pythontk.Workspace``."""
        if not root:
            return None
        if rules is None:
            rules = EnvUtils.workspace_template_rules()
        return ptk.Workspace.create(root, rules=rules, create_dirs=create_dirs)

    @staticmethod
    def promote_workspace(root=None):
        """Mark ``root`` (default: the current workspace folder) as a shared Maya/Blender project
        by writing a ``workspace.mel`` that describes the layout it ALREADY has — scene rule ``.``
        when .blend files sit at the root, ``sourceImages`` → ``textures`` when that's the existing
        texture folder. Creates no subfolders and never clobbers an existing marker's rules.

        Twin of ``mtk.promote_workspace``; the layout heuristics live in
        ``ptk.Workspace.promote`` so both DCCs describe the same folder identically."""
        if root is None:
            ws = EnvUtils.current_workspace()
            root = ws.root if ws else ""
        return ptk.Workspace.promote(root, scene_exts=(".blend",))

    @staticmethod
    def find_workspaces(root_dir, recursive=False):
        """Project folders under ``root_dir`` — marked workspaces (a ``workspace.mel`` at their
        root: shared Maya/Blender projects) plus unmarked ones (a directory directly holding .blend
        files). An unmarked candidate nested inside a marked project (e.g. its ``scenes/`` folder)
        belongs to that project and is not listed. Mirror of mayatk's
        ``find_available_workspaces`` / ``EnvUtils.find_workspaces``.

        ``recursive=False`` (default) only looks at ``root_dir`` and its immediate children —
        mirrors mayatk's workspace-*discovery* toggle (a workspace never nests another workspace's
        scan, only the search for *more* workspace folders goes deeper). Returns absolute dir
        paths, root first, then the rest alphabetically.
        """
        return [
            w.root
            for w in ptk.Workspace.find(
                root_dir, recursive=recursive, scene_exts=(".blend",)
            )
        ]

    @staticmethod
    def is_workspace(path):
        """True if *path* is a workspace root this session opens as it is: marked by
        a ``workspace.mel`` (a shared Maya/Blender project) or directly holding
        .blend files (:func:`find_workspaces`). Mirror of mayatk's
        ``EnvUtils.is_workspace`` (a marked folder only there)."""
        if not path or not os.path.isdir(path):
            return False
        return os.path.normpath(path) in EnvUtils.find_workspaces(path, recursive=False)

    @staticmethod
    def saved_scene_path():
        """The open .blend's path, or ``""`` when it has never been saved. Mirror of
        mayatk's ``EnvUtils.saved_scene_path``."""
        import bpy

        return bpy.data.filepath or ""

    @staticmethod
    def get_recent_files(index=None):
        """Recently-opened .blend paths, most recent first (mirror of ``mtk.get_recent_files``).

        Reads Blender's own ``recent-files.txt`` (the source of File ▸ Open Recent). ``index``
        may be an int or slice. Missing files are filtered out.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        path = CoreUtils.user_config_path("recent-files.txt") or ""
        files = []
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                files = [line.strip() for line in f if line.strip()]
            files = [p for p in files if os.path.isfile(p)]
        return files[index] if index is not None else files

    @staticmethod
    def get_recent_autosave(filter_time=24, timestamp_format="%H:%M:%S"):
        """Recent autosave .blend files as ``(path, timestamp)`` pairs, newest first
        (mirror of ``mtk.get_recent_autosave``). ``filter_time`` is the max age in hours.

        Blender autosaves land in the temporary directory (Preferences ▸ File Paths, falling
        back to the OS temp dir) as ``.blend`` files.
        """
        import time
        import glob
        import tempfile
        import bpy

        temp_dir = (
            bpy.context.preferences.filepaths.temporary_directory
            or tempfile.gettempdir()
        )
        cutoff = time.time() - filter_time * 3600
        results = []
        for path in glob.glob(os.path.join(temp_dir, "*.blend")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff:
                results.append((path, mtime))
        results.sort(key=lambda x: x[1], reverse=True)
        return [
            (path, time.strftime(timestamp_format, time.localtime(mtime)))
            for path, mtime in results
        ]

    @staticmethod
    def get_recent_projects(index=None, format="standard"):
        """Recently used workspaces, most recent first: the folders of Blender's
        recent files (File > Open Recent) that are workspaces (:func:`is_workspace`),
        since Blender keeps no project history of its own. Mirror of mayatk's
        ``EnvUtils.get_recent_projects`` (Maya's recent-projects list there).

        Parameters:
            index (int/slice): Return the entry, or the entries, at it.
            format (str): ``"standard"`` (paths), ``"timestamp"``, or the
                ``"standard|timestamp"`` / ``"timestamp|standard"`` dicts, as mayatk's.
        """
        result = []
        for recent in EnvUtils.get_recent_files():
            folder = os.path.normpath(os.path.dirname(recent))
            if folder not in result and EnvUtils.is_workspace(folder):
                result.append(folder)
        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                return []
        scalar = isinstance(index, int)
        items = [result] if scalar else result
        keys = format.split("|")
        if len(keys) == 2 and set(keys) == {"standard", "timestamp"}:
            stamped = {path: ptk.time_stamp(path) for path in items}
            if keys[0] == "timestamp":
                return {stamp: path for path, stamp in stamped.items()}
            return stamped
        if "timestamp" in keys:
            stamps = [ptk.time_stamp(path) for path in items]
            return stamps[0] if scalar else stamps
        return result

    @staticmethod
    def find_workspace_using_path(scene_path=None):
        """The workspace that holds *scene_path* (default: the open .blend): the
        nearest marked (``workspace.mel``) root above it, else its own folder when
        that is a workspace; for an unsaved file, the session pin. None when nothing
        qualifies. Mirror of mayatk's ``EnvUtils.find_workspace_using_path`` (the
        marked walk-up alone there)."""
        if scene_path is None:
            from blendertk.node_utils.data_nodes import DataNodes

            ws = DataNodes.scene_workspace()
        elif scene_path and os.path.isabs(scene_path):
            ws = ptk.Workspace.for_path(scene_path)
        else:
            ws = None
        if ws is None or not (ws.is_marked or EnvUtils.is_workspace(ws.root)):
            return None
        return ws.root

    @staticmethod
    def python_executable():
        """Blender's bundled interpreter (``<python>/bin/python``; ``sys.executable``
        when none is found): the one pip must drive to install into Blender's
        environment. Mirror of mayatk's ``EnvUtils.python_executable`` (mayapy)."""
        from blendertk.core_utils._core_utils import CoreUtils

        return CoreUtils._blender_python_exe()

    @staticmethod
    def open_scene(path):
        """Open a .blend file (replaces the current file — Maya's ``file -open``). True on success."""
        import bpy

        if not (path and os.path.isfile(path)):
            return False
        try:
            bpy.ops.wm.open_mainfile(filepath=path)
            return True
        except RuntimeError:
            return False

    @staticmethod
    def new_scene():
        """Discard the current file and start an empty, unsaved scene (Maya's ``file -new``).

        The 'close' counterpart of :meth:`open_scene` — there is no null document in Blender, so
        closing a scene means replacing it with a fresh empty one. Reads the *empty* homefile so
        no default cube/camera/light is added. True on success.
        """
        import bpy

        try:
            bpy.ops.wm.read_homefile(use_empty=True)
            return True
        except RuntimeError:
            return False

    @staticmethod
    def scene_has_content():
        """True if the open file holds authored data — anything beyond Blender's *default
        document*: an empty scene, or the startup file's bare camera + light.

        Deliberately conservative — only object-less-but-for-a-camera/light scenes read as
        empty, so a startup cube the user has been modeling on still counts as content. Linked
        libraries count too (an unsaved scene assembled out of references is work), as do text
        datablocks — an unsaved script in the Text Editor is the one authored thing that leaves
        no object behind (nothing in blendertk creates one, so this can't self-trigger).
        """
        import bpy

        if len(bpy.data.libraries) or len(bpy.data.texts):
            return True
        return any(o.type not in _DEFAULT_DOC_OBJECT_TYPES for o in bpy.data.objects)

    @staticmethod
    def scene_has_unsaved_changes():
        """True if replacing the open file (open / close / new) would lose work.

        ``bpy.data.is_dirty`` alone is **not** that test. Blender derives it from the undo
        stack, so a single click in the viewport flips it on a brand-new, never-saved, empty
        scene (verified live in 5.1: one ``ed.undo_push`` in a VIEW_3D context is enough) — any
        'unsaved changes' guard built straight on the flag then prompts with nothing to lose.
        A never-saved file therefore only counts when it actually holds something
        (:meth:`scene_has_content`); a file that exists on disk trusts the flag.

        Maya's counterpart needs no such correction — ``cmds.file(q=True, modified=True)``
        tracks real edits — so this is a Blender-only helper, not a parity gap.
        """
        import bpy

        return EnvUtils._is_unsaved_work(
            bool(getattr(bpy.data, "is_dirty", False)),
            bool(getattr(bpy.data, "is_saved", False)),
            EnvUtils.scene_has_content(),
        )

    # ------------------------------------------------------------------ scene settings
    # The DCC-agnostic ``scene`` record the bridges carry beside a converted scene — the
    # time setup neither FBX nor USD round-trips whole (FBX: fps only; USD: the sampled
    # range only). Same keys on both sides (``mtk.scene_settings`` ↔ ``btk.scene_settings``):
    #   fps           frames per second (float — 29.97 stays 29.97)
    #   frame_start   playback range the timeline plays (Maya min/max; Blender's preview
    #   frame_end     range when enabled, else its scene range)
    #   anim_start    full animation range ⊇ playback (Maya ast/aet; Blender's scene range)
    #   anim_end
    #   frame_current
    SCENE_SETTINGS_KEYS = (
        "fps",
        "frame_start",
        "frame_end",
        "anim_start",
        "anim_end",
        "frame_current",
    )

    #: The frame rate a new Blender scene gets (factory startup).
    DEFAULT_FPS = 24

    @staticmethod
    def scene_status(scene=None):
        """The scene's modelling and session state as plain values: what a
        status readout (tentacle's HUD) shows and warns on. Twin of mayatk's
        ``EnvUtils.scene_status``; a fact Blender has no answer for is ``None``
        (``xform_constraint``, ``time_unit``).

        Returns:
            dict: ``symmetry`` (the active mesh's mirror axes, upper case, or
            None), ``xform_constraint`` (None), ``workspace`` (the current
            workspace's folder name, or None), ``units`` (the unit system and
            length unit), ``fps`` (effective: fps / fps_base), ``time_unit``
            (None), ``default_fps`` (the rate is :attr:`DEFAULT_FPS`),
            ``autosave`` (auto-save is on), ``autosave_scene`` (the open
            .blend is in the temporary / autosave folder), ``saved`` (the
            .blend is a file on disk).
        """
        import tempfile

        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        scene = scene or bpy.context.scene
        active = CoreUtils.active_object()
        symmetry = None
        if active is not None and active.type == "MESH":
            axes = "".join(
                axis.upper()
                for axis in "xyz"
                if getattr(active.data, f"use_mirror_{axis}", False)
            )
            symmetry = axes or None
        units = scene.unit_settings
        fps = EnvUtils.scene_settings(scene)["fps"]
        prefs = bpy.context.preferences.filepaths
        path = bpy.data.filepath
        temp = prefs.temporary_directory or tempfile.gettempdir()
        return {
            "symmetry": symmetry,
            "xform_constraint": None,
            "workspace": CoreUtils.get_env_info("workspace_dir") or None,
            "units": (
                f"{units.system.lower()} {units.length_unit.lower()}"
                if units.system != "NONE"
                else "none"
            ),
            "fps": fps,
            "time_unit": None,
            # Effective: a 23.98 scene (fps 24, base 1.001) is not the default.
            "default_fps": fps == EnvUtils.DEFAULT_FPS,
            "autosave": bool(prefs.use_auto_save_temporary_files),
            "autosave_scene": bool(path) and ptk.FileUtils.is_under(path, temp),
            "saved": bool(path) and os.path.isfile(path),
        }

    @staticmethod
    def scene_settings(scene=None):
        """The live scene's time setup as the bridges' ``scene`` record (see
        :attr:`SCENE_SETTINGS_KEYS`). Blender's preview range is Maya's inner playback
        range: when it is on, it is the ``frame_*`` pair and the scene range the ``anim_*``
        pair; off, both pairs are the scene range. Twin of ``mtk.scene_settings``."""
        import bpy

        scene = scene or bpy.context.scene
        render = scene.render
        anim = (scene.frame_start, scene.frame_end)
        playback = (
            (scene.frame_preview_start, scene.frame_preview_end)
            if scene.use_preview_range
            else anim
        )
        return {
            "fps": render.fps / (render.fps_base or 1.0),
            "frame_start": playback[0],
            "frame_end": playback[1],
            "anim_start": anim[0],
            "anim_end": anim[1],
            "frame_current": scene.frame_current,
        }

    @staticmethod
    def apply_scene_settings(settings, scene=None):
        """Apply a ``scene`` record (any subset of :attr:`SCENE_SETTINGS_KEYS`) to the live
        scene; returns the keys applied. Twin of ``mtk.apply_scene_settings``.

        ``fps`` lands as Blender's integer ``fps`` + fractional ``fps_base`` (29.97 →
        30 / 1.001, the FBX importer's own formula). A playback range narrower than the
        animation range becomes an enabled preview range — Blender's one inner range, so
        a Maya scene with ast/aet ⊋ min/max shows the same two-range time slider here and
        round-trips back losslessly; identical ranges leave the preview range off.
        """
        import bpy

        scene = scene or bpy.context.scene
        settings = settings or {}
        applied = []
        fps = settings.get("fps")
        if fps and float(fps) > 0:
            fps = float(fps)
            scene.render.fps = max(1, int(round(fps)))
            scene.render.fps_base = scene.render.fps / fps
            applied.append("fps")

        def _frames(start_key, end_key):
            start, end = settings.get(start_key), settings.get(end_key)
            if start is None or end is None:
                return None
            start, end = int(round(float(start))), int(round(float(end)))
            return (start, max(start, end))

        playback = _frames("frame_start", "frame_end")
        anim = _frames("anim_start", "anim_end") or playback
        if anim:
            scene.frame_start, scene.frame_end = anim
            applied += ["anim_start", "anim_end"]
        if playback:
            inner = playback != anim
            scene.use_preview_range = inner
            if inner:
                scene.frame_preview_start, scene.frame_preview_end = playback
            applied += ["frame_start", "frame_end"]
        current = settings.get("frame_current")
        if current is not None:
            scene.frame_current = int(round(float(current)))
            applied.append("frame_current")
        return applied

    @staticmethod
    def get_linear_unit():
        """The scene's linear unit by mayatk's name (``"centimeter"``), a key of
        :attr:`SCENE_UNIT_VALUES`; None for a unit outside it (Adaptive, None,
        micrometers, thou). Mirror of mayatk's ``EnvUtils.get_linear_unit``."""
        import bpy

        units = bpy.context.scene.unit_settings
        current = (units.system, units.length_unit)
        return next(
            (name for name, pair in EnvUtils._LENGTH_UNITS.items() if pair == current),
            None,
        )

    @staticmethod
    def set_linear_unit(unit):
        """Set the scene's unit system and length unit from mayatk's unit name, in
        any case; the unit scale stays as it is. Mirror of mayatk's
        ``EnvUtils.set_linear_unit``.

        Returns:
            str: The unit set.

        Raises:
            ValueError: A unit Blender has none for (yard), or an unknown one.
        """
        import bpy

        unit = str(unit).lower()
        pair = EnvUtils._LENGTH_UNITS.get(unit)
        if pair is None:
            raise ValueError(
                f"No Blender length unit for {unit!r}; use one of "
                f"{list(EnvUtils._LENGTH_UNITS)}."
            )
        units = bpy.context.scene.unit_settings
        units.system, units.length_unit = pair  # the system first: it scopes the unit
        return unit

    @staticmethod
    def frame_rates():
        """``{rate name: fps}`` for the frame rates a Blender scene takes: the video
        rates of pythontk's table (mayatk's time-unit names; Maya's 44100 / 48000 fps
        audio units have no Blender counterpart), each set by
        :func:`apply_scene_settings` (a fractional rate through ``fps_base``).
        Mirror of mayatk's ``EnvUtils.frame_rates``."""
        return {
            name: fps for name, fps in ptk.VidUtils.FRAME_RATES.items() if fps < 1000
        }

    @staticmethod
    def autosave_settings():
        """Blender's autosave as mayatk's ``{"max_backups", "interval"}``: the
        backup versions kept on save (Save Versions: ``.blend1`` ...) and the Auto
        Save timer in minutes, 0 while Auto Save is off. Mirror of mayatk's
        ``EnvUtils.autosave_settings``."""
        import bpy

        paths = bpy.context.preferences.filepaths
        return {
            "max_backups": paths.save_version,
            "interval": (
                paths.auto_save_time if paths.use_auto_save_temporary_files else 0
            ),
        }

    @staticmethod
    def set_autosave_settings(max_backups=None, interval=None):
        """Set Blender's backup versions (*max_backups*) and Auto Save timer
        (*interval*, minutes; 0 turns Auto Save off); ``None`` leaves a setting as
        it is. Mirror of mayatk's ``EnvUtils.set_autosave_settings``."""
        import bpy

        paths = bpy.context.preferences.filepaths
        if max_backups is not None:
            paths.save_version = int(max_backups)
        if interval is not None:
            paths.use_auto_save_temporary_files = interval > 0
            if interval > 0:
                paths.auto_save_time = int(interval)

    @staticmethod
    def format_scene_name(name, case=None, suffix=""):
        """Apply a naming convention to a base scene name — ``case`` via :meth:`pythontk.StrUtils.set_case`
        (``None``/"None" leaves it), then append ``suffix`` (not duplicated). Mirror of mayatk's
        ``_format_name``."""
        import pythontk as ptk

        base = name
        if case and case != "None":
            try:
                base = ptk.StrUtils.set_case(base, case)
            except Exception:
                pass
        suffix = (suffix or "").strip()
        if suffix and not base.endswith(suffix):
            base += suffix
        return base

    @staticmethod
    def scene_save_path(directory, name, case=None, suffix="", subfolder=""):
        """The absolute ``.blend`` path :meth:`save_scene_as` would write — the pure
        path computation (no ``bpy``, nothing created on disk), split out so a
        panel's save-preview tooltip shows exactly what the save will do.

        ``case``/``suffix`` format the name; ``subfolder`` is an optional path
        pattern with ``{name}`` / ``{workspace}`` / ``{suffix}`` / ``{scenes}``
        placeholders (``{scenes}`` resolves through the workspace's ``scene`` file
        rule when ``directory`` is a marked workspace — the same
        ``workspace -q -fre "scene"`` lookup mayatk does — falling back to the
        literal ``"scenes"``). Returns ``None`` for missing inputs; propagates
        ``ValueError`` from an invalid placeholder pattern.
        """
        import pythontk as ptk

        if not (directory and name):
            return None
        base = EnvUtils.format_scene_name(name, case, suffix)
        target_dir = directory
        if subfolder:
            scene_rule = ptk.Workspace.load(directory).rules.get("scene")
            resolved = ptk.StrUtils.replace_placeholders(
                subfolder,
                name=EnvUtils.format_scene_name(name, case, ""),
                workspace=os.path.basename(os.path.normpath(directory)),
                suffix=suffix,
                scenes=scene_rule
                if scene_rule and not os.path.isabs(scene_rule)
                else "scenes",
            )
            target_dir = os.path.join(directory, resolved)
        # normpath: the pattern's own separators ride through the join, and the
        # path is user-facing (a panel's save tooltip previews it verbatim).
        return os.path.normpath(os.path.join(target_dir, base + ".blend"))

    @staticmethod
    def save_scene_as(
        directory, name, case=None, suffix="", subfolder="", overwrite=True
    ):
        """Save the current scene as a .blend under ``directory`` with naming conventions applied —
        mirror of mayatk's ``save_scene``. Naming/placeholder handling per
        :meth:`scene_save_path` (the shared path computation). Returns the saved path
        (or ``None`` if it exists and ``overwrite`` is False, or on failure).
        """
        import bpy

        try:
            path = EnvUtils.scene_save_path(
                directory, name, case=case, suffix=suffix, subfolder=subfolder
            )
        except ValueError:
            return None
        if not path:
            return None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError:
            return None
        if os.path.exists(path) and not overwrite:
            return None
        try:
            bpy.ops.wm.save_as_mainfile(filepath=path)
            return os.path.normpath(path)
        except RuntimeError:
            return None

    @staticmethod
    def export_scene_as_obj(
        file_path=None,
        *,
        selection_only=False,
        materials=True,
        smoothing=True,
        normals=True,
        groups=True,
    ):
        """Export the scene as a Wavefront OBJ — mirror of mayatk's ``export_scene_as_obj``.

        Same parameter names and meanings on both sides so a caller (the Scene panel's
        Export Scene format combo) needs no branch. ``groups`` maps onto Blender's
        ``export_object_groups``, ``smoothing`` onto ``export_smooth_groups``.

        A note on what OBJ *cannot* carry, since the format is often picked by habit:
        no transform hierarchy (everything is flattened into world space), no
        skinning, no animation, and no textures beyond a ``.mtl`` sidecar referencing
        them by path. It is a geometry interchange, not a scene one.

        Parameters:
            file_path (str): Destination ``.obj``. ``None`` derives it from the open
                .blend (which must therefore have been saved).
            selection_only (bool): Export only the selection (default: whole scene).
            materials (bool): Write the ``.mtl`` sidecar beside the OBJ.
            smoothing (bool): Write smoothing-group records.
            normals (bool): Write vertex normals.
            groups (bool): Write ``g``/``o`` group records.

        Returns:
            str: The written path.

        Raises:
            ValueError: When *file_path* is None and the scene has never been saved.
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        if not file_path:
            blend_path = bpy.data.filepath or ""
            if not blend_path:
                raise ValueError(
                    "Scene has not been saved yet.\nPlease save the scene first, or "
                    "specify a file path."
                )
            file_path = os.path.splitext(blend_path)[0] + ".obj"

        # window override: the bundled exporters call ``context.window.cursor_set``,
        # which is an AttributeError when window is None (the Qt-pump state) -- the
        # same guard btk.FbxUtils.export applies.
        with CoreUtils.window_context_override():
            bpy.ops.wm.obj_export(
                filepath=file_path,
                export_selected_objects=selection_only,
                export_materials=materials,
                export_smooth_groups=smoothing,
                export_normals=normals,
                export_object_groups=groups,
                export_uv=True,
            )
        return file_path

    @staticmethod
    def foreign_scene_bridge():
        """The hand-off bridge CLASS that writes the other DCC's native scene:
        ``MayaBridge`` here. Mirror of mayatk's (its ``BlenderBridge``).

        A class, not an instance: its ``spec.app.name`` ("Maya") and
        ``save_extensions`` name the target without resolving its executable;
        build one to ``save_as``.
        """
        from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge

        return MayaBridge

    @staticmethod
    def foreign_scene_import():
        """The engine CLASS that pulls a file into this scene through the other DCC:
        ``MayaSceneImport`` here. Mirror of mayatk's (its ``BlenderSceneImport``).

        Its ``import_scene(src, via=...)`` converts a .ma / .mb in a fresh headless
        mayapy, imports a USD natively and a glTF natively (KTX2 decoded first);
        ``SCENE_EXTENSIONS`` are the scene files it converts.
        """
        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        return MayaSceneImport

    #: Blender's own import / export commands by the name a panel lists them under:
    #: ``(operator id, the properties it is invoked with)``, each opening the
    #: operator's file browser. Mirror of mayatk's ``EnvUtils.FILE_COMMANDS`` (MEL
    #: there). A command whose operator this Blender does not register is not offered
    #: (Collada left in 5.0).
    FILE_COMMANDS = {
        "import": {
            "Import OBJ": ("wm.obj_import", {}),
            "Import Collada": ("wm.collada_import", {}),
            "Append from .blend": ("wm.append", {}),
            "Link from .blend": ("wm.link", {}),
        },
        "export": {
            "Export Selection": ("export_scene.fbx", {"use_selection": True}),
            "Export FBX": ("export_scene.fbx", {}),
            "Export OBJ": ("wm.obj_export", {}),
            "Export glTF": ("export_scene.gltf", {}),
            "Export Collada": ("wm.collada_export", {}),
        },
    }

    @classmethod
    def get_file_commands(cls, direction="import"):
        """The names of Blender's own import (*direction* ``"import"``) or export
        (``"export"``) commands this session registers, in menu order: what
        :meth:`run_file_command` takes. Mirror of mayatk's.

        Raises:
            ValueError: *direction* is neither.
        """
        from blendertk.ui_utils._ui_utils import UiUtils

        commands = cls.FILE_COMMANDS.get(direction)
        if commands is None:
            raise ValueError(
                f"Unknown direction {direction!r}: expected 'import' or 'export'."
            )
        return [
            name
            for name, (idname, _props) in commands.items()
            if UiUtils.get_operator(idname) is not None
        ]

    @classmethod
    def run_file_command(cls, name):
        """Run Blender's own import / export command *name*
        (:meth:`get_file_commands`): its operator is invoked as a menu click does, its
        file browser opening. Mirror of mayatk's.

        Raises:
            ValueError: No command of that name; Export Selection with nothing
                selected.
            RuntimeError: Its operator is not registered in this Blender, or refused
                the context.
        """
        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.ui_utils._ui_utils import UiUtils

        command = next(
            (group[name] for group in cls.FILE_COMMANDS.values() if name in group),
            None,
        )
        if command is None:
            raise ValueError(f"No file command named {name!r}.")
        idname, props = command
        if props.get("use_selection") and not CoreUtils.selected_objects():
            raise ValueError(f"{name} requires a selection.")
        if UiUtils.invoke_operator_dialog(idname, **props) is None:
            raise RuntimeError(
                f"{name} is not available in this Blender ({idname} is not registered)."
            )

    @staticmethod
    def scene_triangle_count(selection_only=False):
        """Triangles in the scene's meshes -- or, *selection_only*, in the selected
        ones -- counted off the base meshes (per mesh: loops - 2 x polygons, exact for
        the pre-modifier data, O(1) per mesh): the cost a dense-export warning weighs.
        Mirror of mayatk's."""
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        if selection_only:
            pool = CoreUtils.selected_objects()
        else:
            with CoreUtils.window_context_override():  # the window's layer
                pool = list(bpy.context.view_layer.objects)
        return sum(
            len(o.data.loops) - 2 * len(o.data.polygons)
            for o in pool
            if o.type == "MESH"
        )

    #: Export Scene's writers: format -> the method writing the scene (or the
    #: selection) in it (:meth:`export_scene`). Mirror of mayatk's.
    SCENE_WRITERS = {
        "fbx": "_write_scene_fbx",
        "obj": "_write_scene_obj",
        "glb": "_write_scene_glb",
        "usd": "_write_scene_usd",
    }

    @classmethod
    def export_scene(
        cls,
        file_path,
        file_format="fbx",
        *,
        selection_only=False,
        include_cameras=False,
        include_lights=False,
        include_skins=False,
        include_tangents=True,
        embed_textures=True,
        progress=None,
    ):
        """Write the scene, or the selection, to *file_path* in *file_format*: the
        writer behind the Scene panel's Export Scene and its Export-list one-shots.
        Mirror of mayatk's ``EnvUtils.export_scene`` (the same options, each host's
        route): Blender writes GLB natively (no FBX hop) and USD through the
        interchange export.

        Parameters:
            file_path (str): The file to write.
            file_format (str): ``"fbx"``, ``"obj"``, ``"glb"`` or ``"usd"``
                (:attr:`SCENE_WRITERS`).
            selection_only (bool): Write the selection, not the whole scene.
            include_cameras (bool): Cameras ride the FBX / GLB / USD.
            include_lights (bool): Lights ride the FBX / GLB / USD.
            include_skins (bool): Armatures ride the FBX / USD.
            include_tangents (bool): Per-vertex tangent space (FBX).
            embed_textures (bool): Pack the maps into an FBX; an OBJ writes its
                ``.mtl``.
            progress (callable): ``progress(text=...)`` for a slow leg (none here).

        Returns:
            str: *file_path*.

        Raises:
            ValueError: An unknown *file_format*; the writer's own errors otherwise.
        """
        writer = cls.SCENE_WRITERS.get(file_format)
        if writer is None:
            raise ValueError(
                f"Unknown export format {file_format!r}: expected one of "
                f"{sorted(cls.SCENE_WRITERS)}."
            )
        options = {
            "selection_only": selection_only,
            "include_cameras": include_cameras,
            "include_lights": include_lights,
            "include_skins": include_skins,
            "include_tangents": include_tangents,
            "embed_textures": embed_textures,
        }
        getattr(cls, writer)(
            file_path, options, progress or (lambda *args, **kwargs: None)
        )
        return file_path

    @staticmethod
    def rename_scene_file(path, new_base):
        """Rename a .blend on disk (and its ``.blend1`` backup) — mirror of mayatk's ``rename_scene``.

        Renaming the **open** file is save-then-reopen: unsaved edits are flushed to the old
        .blend first (a save afterwards would just re-create the old name) and the renamed file
        is re-opened at the end. Without the reopen ``bpy.data.filepath`` keeps pointing at a
        filename that no longer exists, so the next save silently resurrects it and the panel
        lists two scenes where the user renamed one.

        Returns the new path, or ``None`` (missing source, name clash, an unchanged name, or the
        open file could not be saved). A case-only rename goes through: on a case-insensitive
        file system its target "exists" -- it is the file itself -- and ``os.rename`` recases it.
        """
        if not (path and os.path.isfile(path) and new_base):
            return None
        directory = os.path.dirname(path)
        ext = os.path.splitext(path)[1] or ".blend"
        new_path = os.path.join(directory, new_base + ext)
        if os.path.normpath(new_path) == os.path.normpath(path):
            return None
        if os.path.exists(new_path) and not ptk.FileUtils.is_same_file(new_path, path):
            return None
        is_open = EnvUtils._is_open_file(path)
        if is_open and not EnvUtils._save_open_file():
            return None
        try:
            os.rename(path, new_path)
        except OSError:
            return None
        backup = path + "1"  # Blender's .blend1 backup
        if os.path.isfile(backup):
            try:
                os.rename(backup, new_path + "1")
            except OSError:
                pass
        new_path = os.path.normpath(new_path)
        if is_open:
            EnvUtils.open_scene(new_path)
        return new_path

    @staticmethod
    def delete_scene_file(path, permanent: bool = False):
        """Move a .blend (and its ``.blend1`` backup) to the trash — mirror of mayatk's
        ``delete_scene``. True once it is gone.

        To the Recycle Bin / Trash (``ptk.FileUtils.move_to_trash``), where it can be
        restored; False, the file untouched, when no trash would take it (a network
        share, a removable drive) -- deleting it for good is the caller's to ask about
        (``ptk.FileUtils.can_trash`` answers first), then *permanent* does it.

        Parameters:
            path (str): The .blend to remove.
            permanent (bool): Delete outright instead: the confirmed choice where
                there is no trash.

        Returns:
            bool: True when the file is gone; False when it was missing, could not
            be removed, or (not *permanent*) no trash took it.
        """
        if not (path and os.path.isfile(path)):
            return False

        def discard(file_path):
            if permanent:
                os.remove(file_path)
                return True
            return ptk.FileUtils.move_to_trash(file_path) is not None

        try:
            if not discard(path):
                return False
        except OSError:
            return False
        backup = path + "1"
        if os.path.isfile(backup):
            try:
                discard(backup)  # a refused backup stays: never lost unasked
            except OSError:
                pass
        return True

    @staticmethod
    def set_reference_display_mode(library, mode):
        """Set the display override for a linked library's objects — mirror of mayatk's
        ``set_reference_display_mode``. ``mode``: ``"off"`` (normal), ``"reference"`` (locked from
        selection, normal shading), ``"template"`` (wireframe + locked). Returns True if any object
        was updated.
        """
        import bpy

        if mode not in _DISPLAY_MODES:
            raise ValueError(
                f"Invalid display mode {mode!r}; expected one of {_DISPLAY_MODES}"
            )
        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return False
        display_type = "WIRE" if mode == "template" else "TEXTURED"
        hide_select = mode != "off"
        count = 0
        for o in _EnvUtilsInternal._library_objects(lib):
            o.display_type = display_type
            o.hide_select = hide_select
            count += 1
        return count > 0

    @staticmethod
    def get_reference_display_mode(library):
        """Return the active display mode (``"off"`` / ``"reference"`` / ``"template"``) for a linked
        library — ``"off"`` when its objects disagree (mirror of mayatk's all-must-agree rule)."""
        import bpy

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return "off"
        modes = set()
        for o in _EnvUtilsInternal._library_objects(lib):
            if not o.hide_select:
                modes.add("off")
            elif o.display_type == "WIRE":
                modes.add("template")
            else:
                modes.add("reference")
        return modes.pop() if len(modes) == 1 else "off"
