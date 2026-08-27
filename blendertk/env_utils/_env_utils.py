# !/usr/bin/python
# coding=utf-8
"""blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.

Maya *file references* map onto Blender **linked libraries** (``bpy.data.libraries`` — File ▸ Link).
This module is the Qt-free, bpy-only engine (unit-testable headless): discover ``.blend`` files under
a folder, list the libraries already linked, link a ``.blend``'s collections (or append a copy), and
reload / remove a library. The Switchboard slot (``reference_manager.py``) is the thin Qt driver.

``import bpy`` is deferred into the call bodies (importing the package surface must not need a
running Blender — the no-import-side-effects rule).
"""

import os

import pythontk as ptk


# ----------------------------------------------------------------- workspace / scene files
# The Blender analogue of Maya's project workspace, built on ``pythontk.Workspace``. A workspace
# is a project folder, either *marked* (a ``workspace.mel`` at its root — a shared Maya/Blender
# project whose file rules say where scenes/textures live) or *unmarked* (a directory directly
# holding .blend files — zero-ceremony Blender-alone projects, promotable via
# ``promote_workspace``). These back the Reference Manager's workspace combo + scene table +
# open/save/rename/delete operations and the package-wide current-workspace resolver (no bpy
# except open/save and the current-file lookup → testable on disk).

_current_workspace_root = None  # session pin (Blender has no native `workspace -o`)


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
    def _workspace_template_store():
        """The shared ``ptk.WorkspaceTemplates`` preset store — what the Workspace Editor's
        template combo is wired to (mayatk reads the same one)."""
        return ptk.WorkspaceTemplates.store()

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


class EnvUtils(_EnvUtilsInternal):
    """Namespace mirror of mayatk's ``EnvUtils`` (helpers also exposed module-level)."""

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
        """Set of normalized absolute paths of the ``.blend`` files currently linked as libraries."""
        return {r["abspath"].lower() for r in EnvUtils.list_libraries() if r["abspath"]}

    @staticmethod
    def is_blend_linked(path):
        """True iff ``path`` is already linked as a library."""
        return _EnvUtilsInternal._abspath(path).lower() in EnvUtils.linked_blend_paths()

    @staticmethod
    def link_blend_file(path, link=True, instance=True, target_collection=None):
        """Link (or append, ``link=False``) every collection from ``path`` and instance them into the
        active scene — the closest Blender analogue of adding a Maya file reference. Falls back to
        objects when the file has no collections. Returns the number of datablocks brought in.

        ``target_collection`` links into that collection instead of the active scene's master
        collection — used by Hierarchy Sync to sandbox the reference in a hidden, view-layer-excluded
        collection so it never clutters the outliner/viewport.
        """
        import bpy

        if not (path and os.path.isfile(path)):
            return 0
        with bpy.data.libraries.load(path, link=link) as (data_from, data_to):
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
        """Remove a library and everything linked from it (datablock or name). Returns True on success."""
        import bpy

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return False
        try:
            bpy.data.libraries.remove(lib, do_unlink=True)
            return True
        except (RuntimeError, ReferenceError):
            return False

    @staticmethod
    def make_library_local(library):
        """Make every datablock linked from ``library`` **local** (a native, editable copy) and drop the
        now-unused library — the Blender analogue of Maya's *import references* (``importContents``).

        ``library`` is a ``bpy.types.Library`` datablock or its name. Returns the number of datablocks
        made local.
        """
        import bpy

        lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
        if lib is None:
            return 0
        count = 0
        # `id.make_local()` clears each datablock's `.library` pointer in place; iterate every ID
        # collection so linked meshes/materials/etc. come local too, not just the objects.
        for attr in dir(bpy.data):
            coll = getattr(bpy.data, attr, None)
            if getattr(coll, "rna_type", None) is None or not hasattr(coll, "__iter__"):
                continue
            for db in list(coll):
                # Compare by `==` not `is`: bpy hands back fresh datablock wrappers, so identity is
                # unreliable; `==` compares the underlying ID (the documented bpy-wrapper gotcha).
                if getattr(db, "library", None) == lib:
                    try:
                        db.make_local()
                        count += 1
                    except (RuntimeError, ReferenceError, AttributeError):
                        pass
        if count:  # the library has no linked users left → drop it
            try:
                bpy.data.libraries.remove(lib, do_unlink=True)
            except (RuntimeError, ReferenceError):
                pass
        return count

    @staticmethod
    def set_current_workspace(root=None):
        """Pin (or clear, with None) the session's current workspace — the Blender analogue of
        Maya's ``workspace -o``. Returns the pinned root (or None when cleared)."""
        global _current_workspace_root
        _current_workspace_root = os.path.normpath(root) if root else None
        return _current_workspace_root

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
            if _current_workspace_root and os.path.isdir(_current_workspace_root):
                return ptk.Workspace.load(_current_workspace_root)
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

    @staticmethod
    def source_images_dir(path=None):
        """The current workspace's texture folder — its ``sourceImages`` rule → an existing
        ``sourceimages``/``textures`` folder → ``textures`` (the legacy Blender-alone default).
        '' when there is no current workspace."""
        ws = EnvUtils.current_workspace(path)
        if ws is None:
            return ""
        return ws.resolve_dir(
            ("sourceImages",), ("sourceimages", "textures"), default="textures"
        )

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
    def save_scene_as(
        directory, name, case=None, suffix="", subfolder="", overwrite=True
    ):
        """Save the current scene as a .blend under ``directory`` with naming conventions applied —
        mirror of mayatk's ``save_scene``. ``case``/``suffix`` format the name; ``subfolder`` is an
        optional path pattern with ``{name}`` / ``{workspace}`` / ``{suffix}`` / ``{scenes}``
        placeholders (``{scenes}`` resolves through the workspace's ``scene`` file rule when
        ``directory`` is a marked workspace — the same ``workspace -q -fre "scene"`` lookup mayatk
        does — falling back to the literal ``"scenes"``). Returns the saved path (or ``None`` if it
        exists and ``overwrite`` is False, or on failure).
        """
        import bpy
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
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError:
            return None
        path = os.path.join(target_dir, base + ".blend")
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
    def rename_scene_file(path, new_base):
        """Rename a .blend on disk (and its ``.blend1`` backup) — mirror of mayatk's ``rename_scene``.

        Renaming the **open** file is save-then-reopen: unsaved edits are flushed to the old
        .blend first (a save afterwards would just re-create the old name) and the renamed file
        is re-opened at the end. Without the reopen ``bpy.data.filepath`` keeps pointing at a
        filename that no longer exists, so the next save silently resurrects it and the panel
        lists two scenes where the user renamed one.

        Returns the new path, or ``None`` (missing source, name clash, no-op rename, or the open
        file could not be saved).
        """
        if not (path and os.path.isfile(path) and new_base):
            return None
        directory = os.path.dirname(path)
        ext = os.path.splitext(path)[1] or ".blend"
        new_path = os.path.join(directory, new_base + ext)
        if os.path.normcase(os.path.normpath(new_path)) == os.path.normcase(
            os.path.normpath(path)
        ):
            return None
        if os.path.exists(new_path):
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
    def delete_scene_file(path):
        """Delete a .blend (and its ``.blend1`` backup) — mirror of mayatk's ``delete_scene``. True on
        success."""
        if not (path and os.path.isfile(path)):
            return False
        try:
            os.remove(path)
        except OSError:
            return False
        backup = path + "1"
        if os.path.isfile(backup):
            try:
                os.remove(backup)
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
