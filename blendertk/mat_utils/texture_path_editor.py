# !/usr/bin/python
# coding=utf-8
"""Texture Path Editor engine (Blender): the file's texture paths and the commands
that fix them -- mirror of mayatk's ``TexturePathEditor`` (names and behavior).

Maya's file node is Blender's image datablock: a row is one FILE image (or TILED
set; ``img.filepath`` is the path, its name the node column), and the shader
column lists the material(s) whose shader graph reads it. Maya's *sourceimages*
maps onto the workspace's texture folder (its ``sourceImages`` rule, or an
existing ``sourceimages`` / ``textures`` folder), and paths are made
``//``-relative where mayatk's are made project-relative. The work is
``MatUtils``' (``get_image_records`` / ``set_texture_directory`` /
``normalize_texture_paths`` / ``resolve_missing_textures`` /
``rename_texture_file`` / ``sync_material_names`` ...), ``LightmapRecords``' and,
for Find & Copy, ``ptk.FileCollect``'s (this class is its ``CollectHost``);
this class gives it mayatk's one call shape. Its panel is tentacle's shared
Texture Path Editor (``slots/tools/texture_path_editor.py``).

``bpy`` is imported in the call bodies: without it (the Qt-only .venv loading the
panel) the listings answer empty.
"""

import os

import pythontk as ptk

import blendertk as btk
from blendertk.core_utils._core_utils import CoreUtils


class TexturePathEditor(ptk.LoggingMixin):
    """The Texture Path Editor's scene half: images, their paths, and the commands
    that relocate, respell, find and rename them (mirror of mayatk's).

    One instance per editor: it keeps :attr:`previous_paths` (the path each image
    had before this session's last repath -- the table tooltip's "Previous:"
    line) and :attr:`active_logger` (where a command reports while a panel lends
    one; else this class's logger).
    """

    #: The host's words for the table's two name columns and the row actions
    #: that name a node (mayatk: Shader / File Node / Hypershade ...).
    LABELS = {
        "shader": "Material",
        "file_node": "Texture",
        "texture_dir": "Textures Folder",
        "select_file_node": "Select Texture Node",
        "graph": "Show in Shader Editor",
        "delete": "Remove Texture",
    }

    #: Blender has no Arnold integration and no parallel preview shaders: its
    #: rows come from image datablocks, shared by reference, never duplicated
    #: per renderer -- there is nothing for "Exclude Arnold Nodes" to hide.
    EXCLUDE_ARNOLD = False

    #: Scene events the table refreshes on, each with whether only a SHOWN
    #: table follows it. Blender has no import-into event distinct from a load
    #: and no per-project workspace switch (mayatk also follows SceneImported
    #: and workspaceChanged).
    SCENE_EVENTS = (
        ("SceneOpened", False),
        ("NewSceneOpened", False),
        ("Undo", True),
        ("Redo", True),
    )

    #: Browse for File's filter: what an image datablock loads.
    FILE_TYPES = (
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.tga",
        "*.tif",
        "*.tiff",
        "*.exr",
        "*.hdr",
        "*.bmp",
        "*.*",
    )

    RELOCATE_MODES = ("rewrite", "copy", "move")
    NORMALIZE_MODES = ("rewrite", "copy", "move")
    RESOLVE_STRATEGY_ORDER = ("stem", "texture", "fuzzy")

    #: Said when Keep Names In Sync leaves a rename to the file or image edited
    #: (:meth:`_sync_carries`).
    _SYNC_ALONE = (
        "Keep Names In Sync: {name} was renamed alone -- {material} follows only a "
        "new base name for its own texture set."
    )

    def __init__(self):
        #: image name -> its path before this session's last repath (the
        #: tooltip's "Previous:" line); the panel clears it when the file changes.
        self.previous_paths = {}
        #: Where ``_log`` reports while a caller lends a logger (a panel's pane);
        #: ``None``: this class's logger.
        self.active_logger = None
        self.logger.set_log_prefix("[texture_path_editor] ")

    # ------------------------------------------------------------------
    # Project / resolution
    # ------------------------------------------------------------------

    def texture_dir(self) -> str:
        """The project's texture folder -- Blender analogue of Maya's sourceimages:
        the workspace's ``sourceImages`` rule (or an existing ``sourceimages`` /
        ``textures`` folder), else the workspace root. ``""`` until the file has
        been saved (or a workspace is pinned)."""
        ws = btk.current_workspace()
        if ws is None:
            return ""
        return (
            ws.resolve_dir(("sourceImages",), ("sourceimages", "textures")) or ws.root
        )

    @staticmethod
    def _image(file_node):
        """The image datablock *file_node* names (or is), ``None`` when gone."""
        if file_node is None or not isinstance(file_node, str):
            return file_node
        try:
            import bpy
        except ImportError:
            return None
        return bpy.data.images.get(file_node)

    def _images(self, file_nodes):
        """The live image datablocks behind *file_nodes* (names or datablocks)."""
        images = []
        for node in file_nodes or []:
            img = self._image(node)
            if img is not None and img not in images:
                images.append(img)
        return images

    @staticmethod
    def _resolve(path, library=None) -> str:
        """A raw path (``//``-relative included) as an absolute path.

        *library* is handed to ``bpy.path.abspath``: a datablock linked from a
        library .blend stores its ``//`` path relative to the LIBRARY file, so
        resolving it against the open file finds nothing (``MatUtils._abspath``
        resolves the same way, so the editor and the engine agree).
        """
        if not path:
            return ""
        try:
            import bpy

            return os.path.normpath(bpy.path.abspath(path, library=library))
        except Exception:
            return os.path.normpath(path)

    def resolve_path(self, path: str, file_node=None):
        """``(exists, absolute path)`` for a stored or typed *path* -- a linked
        *file_node*'s ``//`` path against its library; a set by its tiles."""
        img = self._image(file_node)
        abs_path = self._resolve(path, getattr(img, "library", None))
        return bool(abs_path and ptk.TiledPath.tiles(abs_path)), abs_path

    def _path_resolves(self, img) -> bool:
        """True when the image's stored path points at a file (a set: its tiles)."""
        return self.resolve_path(getattr(img, "filepath", "") or "", img)[0]

    @staticmethod
    def is_absolute_path(path: str) -> bool:
        """Whether *path* is stored absolute (not a ``//`` .blend-relative path)."""
        return bool(path) and not path.startswith("//") and os.path.isabs(path)

    # ------------------------------------------------------------------
    # The table's rows
    # ------------------------------------------------------------------

    def rows(self, exclude_arnold: bool = False):
        """One row per FILE image (a TILED set: one, its ``<UDIM>`` pattern).

        *exclude_arnold* is accepted for mayatk's one signature (nothing to
        exclude here). ``[]`` without ``bpy``.

        Returns:
            list[dict]: ``shader`` (the material label: every material reading
            the image, ``(unused)`` for none), ``shader_node`` (the ONE material
            a rename of that cell renames -- ``""`` when several or none read
            it), ``materials``, ``shader_editable`` (that material is local),
            ``path`` (stored), ``file_node`` (the image's name), ``exists`` and
            ``abspath``.
        """
        try:
            import bpy  # noqa: F401
        except ImportError:
            return []
        image_to_mats = btk.get_image_material_map()
        rows = []
        for record in btk.get_image_records():
            mats = list(image_to_mats.get(record["name"], []))
            one = mats[0] if len(mats) == 1 else ""
            rows.append(
                {
                    "shader": ", ".join(mats) if mats else "(unused)",
                    "shader_node": one,
                    "materials": mats,
                    "shader_editable": self.can_rename_shader(one),
                    "path": record["filepath"],
                    "file_node": record["name"],
                    "exists": record["exists"],
                    "abspath": record["abspath"],
                }
            )
        return rows

    def file_nodes(self, exclude_arnold: bool = False):
        """Every FILE image (and TILED set) by name -- the "all" scope."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return []
        return [record["name"] for record in btk.get_image_records()]

    def node_exists(self, node) -> bool:
        """Whether the image *node* names is still in the file."""
        return self._image(node) is not None

    def stored_path(self, node) -> str:
        """The image's stored ``filepath``, ``""`` when it is gone."""
        return getattr(self._image(node), "filepath", "") or ""

    def selected_file_nodes(self):
        """The images the selected objects' materials read.

        Returns:
            ``None`` when nothing is selected, else the image names (``[]``: the
            selection carries no material, or its materials no image).
        """
        objects = btk.selected_objects()
        if not objects:
            return None
        mat_names = {
            slot.material.name
            for obj in objects
            for slot in getattr(obj, "material_slots", [])
            if slot.material
        }
        return [
            name
            for name, mats in btk.get_image_material_map().items()
            if mat_names.intersection(mats)
        ]

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------

    @staticmethod
    def reload_textures() -> None:
        """Force Blender to re-read every image from disk."""
        btk.reload_textures()

    def _snapshot(self, images):
        """Snapshot *images*' paths; returns a callback recording each path that
        changed into :attr:`previous_paths` once the batch finishes (the engine
        calls mutate ``img.filepath`` and return only a count)."""
        before = {img.name: img.filepath for img in images}

        def record():
            changed = 0
            for name, old_path in before.items():
                img = self._image(name)
                if img is not None and old_path and old_path != img.filepath:
                    self.previous_paths[name] = old_path
                    changed += 1
            return changed

        return record

    # ------------------------------------------------------------------
    # Set Directory
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def set_texture_directory(
        self,
        file_nodes,
        target_dir: str,
        relocate_mode: str = "rewrite",
        allow_missing: bool = False,
    ) -> int:
        """Repath each image so its file lives directly under *target_dir* --
        mirror of mayatk's.

        The file work is ``ptk.FileCollect.set_directory`` over this host (a
        FLAT Collect: the engine Find & Copy runs on). *relocate_mode*:
        ``"rewrite"`` (path only), or ``"copy"`` / ``"move"`` -- each file
        brought in under its name, a TILED set whole, identical bytes already
        there reused, and a move takes a source away only once its copy
        compares whole, never one that IS the destination however spelled (a
        junction, a mapped drive), never one another image still reads (it is
        copied). A name a different file holds at the destination, or that two
        different files of the batch bring, leaves those images on their path,
        warned.

        An image is repathed only when its file is ACTUALLY at the target
        afterwards, unless *allow_missing*; one whose file is on disk nowhere
        has nothing to bring and is repathed by that rule. A LINKED image is
        left (its path is its library's). Records prior paths in
        :attr:`previous_paths`. Returns the number of images repathed.
        """
        if relocate_mode not in self.RELOCATE_MODES:
            raise ValueError(
                f"Unknown relocate_mode {relocate_mode!r}; expected one of "
                f"{sorted(self.RELOCATE_MODES)}."
            )
        images = self._images(file_nodes)
        # A linked image's path is its library's: repathing it here would read
        # nothing once the library reloads (and its datablock refuses the edit).
        linked = [
            img.name for img in images if getattr(img, "library", None) is not None
        ]
        if linked:
            self._log(
                f"Set Directory: linked, their library's path: {', '.join(linked)}.",
                "warning",
            )
        names = [img.name for img in images if getattr(img, "library", None) is None]
        if not (names and target_dir):
            return 0
        target = os.path.normpath(target_dir)
        done = ptk.FileCollect(self).set_directory(
            names,
            target,
            relocate_mode,
            allow_missing=allow_missing,
            copy=self._copy_into,
            kind="texture",
        )
        for level, message, items in done["report"]:
            self._log(f"Set Directory: {message}", level, items)
        count = self._repath_nodes(done["repath"])
        if done["absent"]:
            self._log(
                f"Set Directory: {len(done['absent'])} texture(s) left on their "
                f"path -- {target} holds no file of that name. Copy / Move brings "
                "the files along; Allow Missing Targets points them there anyway.",
                "warning",
                items=done["absent"],
            )
        return count

    # ------------------------------------------------------------------
    # Make Paths Absolute / Normalize Paths
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def make_paths_absolute(self, file_nodes) -> int:
        """Rewrite ``//``-relative paths to absolute -- inverse of Normalize Paths.

        Returns:
            int: How many paths were rewritten.

        Raises:
            ValueError: The .blend is unsaved: ``//`` has nothing to resolve
                against, and Blender would bake the process CWD into the path.
        """
        import bpy

        if not bpy.data.filepath:
            raise ValueError(
                "Save the .blend first -- // paths have nothing to resolve against."
            )
        images = self._images(file_nodes)
        if not images:
            return 0
        record = self._snapshot(images)
        count = btk.normalize_texture_paths("absolute", images=images)
        record()
        self._log(f"Make Paths Absolute -- {count} path(s) made absolute.")
        return count

    @CoreUtils.undoable
    def normalize_paths(self, file_nodes, external_mode: str = "rewrite") -> int:
        """Rewrite (selected) paths relative to the saved .blend (``//`` paths).

        *external_mode* handles textures outside the project folder whose file
        exists: ``"rewrite"`` leaves them, ``"copy"`` / ``"move"`` bring them
        into the project's texture folder (a TILED set whole) and rebind.

        Returns:
            int: How many paths changed.
        """
        if external_mode not in self.NORMALIZE_MODES:
            raise ValueError(
                f"Unknown external_mode {external_mode!r}; "
                f"expected one of {sorted(self.NORMALIZE_MODES)}."
            )
        images = self._images(file_nodes)
        if not images:
            return 0
        record = self._snapshot(images)
        moved = (
            btk.normalize_texture_paths(external_mode, images=images)
            if external_mode in ("copy", "move")
            else 0
        )
        relative = btk.normalize_texture_paths("relative", images=images)
        changed = record()
        verb = "copied" if external_mode == "copy" else "moved"
        self._log(
            f"Normalize Paths -- made relative: {relative}"
            + (f"; external {verb}: {moved}" if moved else "")
            + "."
        )
        return changed

    # ------------------------------------------------------------------
    # Resolve Missing
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def resolve_missing_textures(self, modes, file_nodes=None) -> int:
        """Rebind missing textures to files found under the texture folder.

        Mirror of mayatk's: the enabled *modes* run as one cascade, safest first
        (``stem`` same name any extension, ``texture`` same map type + base name,
        ``fuzzy`` similar name); a TILED set is never rebound to one tile.

        Parameters:
            modes: Mode names drawn from :attr:`RESOLVE_STRATEGY_ORDER`.
            file_nodes: The images to restrict to; ``None``: every one.

        Returns:
            int: How many were rebound.
        """
        if not modes:
            raise ValueError("At least one mode is required.")
        unknown = set(modes) - set(self.RESOLVE_STRATEGY_ORDER)
        if unknown:
            raise ValueError(f"Unknown resolve mode(s): {sorted(unknown)}")
        search_dir = self.texture_dir()
        if not (search_dir and os.path.isdir(search_dir)):
            self._log(f"Texture folder not found: {search_dir or '(none)'}", "warning")
            return 0
        images = None if file_nodes is None else self._images(file_nodes)
        if images is not None and not images:
            self._log("No textures to process.", "warning")
            return 0
        record = self._snapshot(
            images if images is not None else self._images(self.file_nodes())
        )
        count = btk.resolve_missing_textures(
            search_dir,
            stem="stem" in modes,
            texture="texture" in modes,
            fuzzy="fuzzy" in modes,
            images=images,
        )
        record()
        self._log(
            f"Resolved {count} missing texture(s) under {search_dir}; "
            f"strategies: {', '.join(modes)}."
        )
        return count

    # ------------------------------------------------------------------
    # Find & Copy -- Resolve, then Collect
    # ------------------------------------------------------------------

    def unresolved_file_nodes(self, file_nodes):
        """The images whose stored path does not resolve -- what a Find & Copy
        search folder is for (resolved library-aware, a set by its tiles, the way
        :meth:`plan_find_and_copy` judges it)."""
        return [
            img.name for img in self._images(file_nodes) if not self._path_resolves(img)
        ]

    @staticmethod
    def _find_copy_answers(answers):
        """``(source_dir, dest_dir, mode)`` of a Find & Copy answer dict, the mode
        normalized (mirror of mayatk's)."""
        mode = "move" if str(answers.get("mode", "")).lower() == "move" else "copy"
        return answers.get("source_dir") or "", answers.get("dest_dir") or "", mode

    def plan_find_and_copy(
        self, file_nodes, answers, lightmaps=(), choices=None, progress=None
    ):
        """What Find & Copy would do: one row per image and per lightmap.

        Mirror of mayatk's: a Resolve, then a Collect (``ptk.FileCollect``). An
        image whose path resolves (library-aware; a TILED set by its tiles) is
        its own source; one that does not is searched for by name under
        ``answers["source_dir"]``, and several different same-named files leave
        it AMBIGUOUS with its candidates, waiting for *choices*. Each file keeps
        its name, flat in ``answers["dest_dir"]`` unless a different file of the
        name is there or gathered with it -- then under the shortest run of its
        own parent folders that tells it apart. Nothing is written: the ONE call
        a preview and its commit derive destinations through.

        Parameters:
            file_nodes: The images (names or datablocks).
            answers: ``source_dir``, ``dest_dir``, ``mode`` (``"Copy"`` /
                ``"Move"``, any case).
            lightmaps: The lightmap dependency records in scope.
            choices: ``{row key: path}`` -- the file picked for an ambiguous row
                (``ptk.FileCollect.row_key``).
            progress: ``progress(text)`` -> a context manager; ``None``: none.

        Returns:
            list[dict]: ``ptk.FileCollect.plan`` rows, each with its ``kind``.
        """
        source_dir, dest_dir, mode = self._find_copy_answers(answers)
        images = self._images(file_nodes)
        # A linked image's path is its library's: it is never repathed here, so
        # it is never collected -- and its file is kept by a move
        # (:meth:`read_elsewhere`), which would take it from the library.
        linked = [i.name for i in images if getattr(i, "library", None) is not None]
        if linked:
            self._log(f"Linked, their library's path: {', '.join(linked)}.", "warning")
        local = [i.name for i in images if getattr(i, "library", None) is None]
        with self._progress(progress, f"Searching {source_dir or 'the paths'}…"):
            return ptk.FileCollect(self).plan_nodes(
                local,
                dest_dir,
                search_dir=source_dir,
                choices=choices,
                kind="texture",
                extra=self._lightmap_entries(lightmaps, source_dir, dest_dir, mode),
            )

    def find_and_copy_textures(
        self, file_nodes, answers, lightmaps=(), dry_run=False, progress=None, plan=None
    ) -> bool:
        """Find & Copy: gather the scope's textures into one folder and repath.

        Mirror of mayatk's: the plan is :meth:`plan_find_and_copy`'s (*plan*:
        rows a preview returned, picks applied, committed as shown); the
        report, the file work and the honest close are
        ``ptk.FileCollect.run``'s; the repath is :meth:`repath`, one undo step.

        Parameters:
            file_nodes: The images (names or datablocks).
            answers: ``source_dir``, ``dest_dir``, ``mode``.
            lightmaps: The lightmap dependency records in scope.
            dry_run: Report the plan; change nothing.
            progress: ``progress(text)`` -> a context manager; ``None``: none.
            plan: :meth:`plan_find_and_copy` rows to commit.

        Returns:
            bool: whether there was anything to do.
        """
        source_dir, dest_dir, mode = self._find_copy_answers(answers)
        rows = list(plan) if plan is not None else self.plan_find_and_copy(
            file_nodes, answers, lightmaps=lightmaps, progress=progress
        )
        return ptk.FileCollect(self, report=self._log).run(
            rows,
            dest_dir,
            mode=mode,
            dry_run=dry_run,
            search_dir=source_dir,
            copy=self._copy_into,
            progress=progress,
        )

    def read_elsewhere(self, nodes):
        """The files the file reads through images other than *nodes*
        (``ptk.CollectHost``): every image, linked ones included, resolved
        library-aware. A Move copies these instead of taking them away --
        moved, a linked image (or one out of scope) reading the same file was
        left on a deleted path. Mirror of mayatk's."""
        try:
            import bpy
        except ImportError:
            return []
        held = {img.as_pointer() for img in self._images(nodes)}
        return [
            self._resolve(img.filepath, getattr(img, "library", None))
            for img in bpy.data.images
            if img.filepath and img.as_pointer() not in held
        ]

    @staticmethod
    def stored_form(path: str, file_node=None) -> str:
        """The path an image stores for the absolute *path*: ``//``-relative
        inside the project (``MatUtils.to_project_relative``), as every repath
        here writes it (``ptk.CollectHost``; mirror of mayatk's)."""
        return btk.to_project_relative(path)

    def repath(self, rows) -> int:
        """Point each landed Collect row at its destination (``ptk.CollectHost``),
        one undo step: a texture row's image (:meth:`_repath_nodes`), a lightmap
        row's bake markers (each at the folder its map landed in). Mirror of
        mayatk's."""
        maps = [r for r in rows if r.get("kind") == "lightmap"]
        textures = [r for r in rows if r.get("kind") != "lightmap"]
        with CoreUtils.undo_chunk("Remap Found Textures") as step:
            count = self._repath_nodes(textures) + self._repoint_lightmaps(maps)
            if not count:
                step.cancel()  # nothing changed: no empty undo step
        return count

    def _repoint_lightmaps(self, rows) -> int:
        """Repoint the markers of the landed lightmap rows, each map at the
        folder it landed in (``LightmapRecords.landed_dirs``)."""
        if not rows:
            return 0
        records = self._lightmap_records()
        dirs = records.landed_dirs(r["destination"] for r in rows)
        markers = records.repath_lightmaps(dirs)
        self._log(f"{markers} bake marker(s) repointed.", "success")
        return markers

    def _repath_nodes(self, rows) -> int:
        """Repath each texture row's image to its destination (inside
        :meth:`repath`'s one undo step); an image already storing its final
        form is left alone."""
        count = unchanged = 0
        for row in rows:
            img = self._image(row["owner"])
            if img is None:
                continue
            current = img.filepath
            final = self.stored_form(row["destination"], img)
            if final == current:
                unchanged += 1
                continue
            img.filepath = final
            try:
                img.reload()
            except RuntimeError:
                pass
            self.previous_paths[img.name] = current
            count += 1
        self._log(f"Repathed {count} image(s).", "success" if count else "info")
        if unchanged:
            self._log(f"{unchanged} already pointed at the destination.")
        return count

    @staticmethod
    def _progress(progress, text):
        """``progress(text)``, or a context yielding no callback when there is none."""
        if progress is not None:
            return progress(text)
        import contextlib

        return contextlib.nullcontext(None)

    @staticmethod
    def _copy_into(sources, dest_dir, mode, progress=None):
        """The Collect's copier: the editor's own collision policy
        (``_MatUtilsInternal._copy_files``: identical bytes reused, a different
        file never replaced)."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        return _MatUtilsInternal._copy_files(sources, dest_dir, mode, progress)

    def _lightmap_entries(self, lightmaps, source_dir, dest_dir, mode):
        """The lightmap rows to plan beside the textures: the records' own dry
        Collect (``LightmapRecords.relocate_lightmaps``). Mirror of mayatk's."""
        if not lightmaps:
            return []
        planned = self._lightmap_records().relocate_lightmaps(
            dest_dir,
            source_dir=source_dir,
            mode=mode,
            maps=self._lightmap_maps(lightmaps),
            dry_run=True,
        )
        return [dict(row, kind="lightmap") for row in planned["plan"]]

    # ------------------------------------------------------------------
    # Repath one image / a typed path
    # ------------------------------------------------------------------

    @staticmethod
    def _as_set_pattern(img, chosen):
        """*chosen* -- a file picked for *img* -- spelled as a tile set when *img*
        is one and *chosen* is one of the tiles: ``rock.1002.png`` ->
        ``rock.<UDIM>.png``. Blender keeps a concrete name set on a TILED image,
        so a set repathed by picking a tile read that one tile."""
        import bpy

        stored = bpy.path.basename(getattr(img, "filepath", "") or "")
        if getattr(img, "source", "") != "TILED" and not ptk.TiledPath.has_token(
            stored
        ):
            return chosen
        tile = ptk.MapFactory.get_tile_token(chosen)  # ".1002", ".u1_v1"; "" for none
        token = {"udim": "<UDIM>", "uvtile": "<UVTILE>"}.get(
            ptk.TiledPath.scheme(tile[1:])
        )
        if not token:
            return chosen
        folder, name = os.path.split(chosen)
        stem, ext = os.path.splitext(name)
        return os.path.join(
            folder, f"{stem[: len(stem) - len(tile)]}{tile[0]}{token}{ext}"
        )

    @CoreUtils.undoable
    def set_texture_path(self, file_node, path: str, relative: bool = False) -> str:
        """Point *file_node*'s image at *path* -- Browse for File, or a typed path.

        Parameters:
            relative: A picked file: spelled ``//``-relative inside the project,
                and as the set's pattern when the image is a set and a tile was
                picked. ``False`` stores *path* as typed.

        Returns:
            str: The path now stored.

        Raises:
            ValueError: The image is gone.
        """
        img = self._image(file_node)
        if img is None:
            raise ValueError(f"Image {file_node!r} no longer exists.")
        new_path = (
            btk.to_project_relative(self._as_set_pattern(img, path))
            if relative
            else path
        )
        old_path = img.filepath
        btk.repath_image(img, new_path)
        if old_path and old_path != img.filepath:
            self.previous_paths[img.name] = old_path
        self._log(f"{img.name}: '{old_path}' -> '{img.filepath}'")
        return img.filepath

    def is_file_rename(self, stored: str, typed: str, file_node=None) -> bool:
        """Whether a typed path renames *stored*'s file rather than repointing.

        Mirror of mayatk's: same folder, another name, the old file on disk and
        the new one not -- inside the scene's project
        (``DataNodes.scene_project_root``); outside it a path edit repoints, and
        says why. *file_node*'s library resolves a linked image's ``//`` path.
        """
        if not stored or not typed:
            return False
        img = self._image(file_node)
        old = self._resolve(stored, getattr(img, "library", None))
        new = self._resolve(typed)
        if not (
            ptk.FileUtils.is_same_file(os.path.dirname(old), os.path.dirname(new))
            and os.path.basename(old) != os.path.basename(new)
            and ptk.TiledPath.tiles(old)
            and not ptk.TiledPath.tiles(new)
        ):
            return False
        project = btk.DataNodes.scene_project_root()
        if project and ptk.FileUtils.is_under(os.path.abspath(old), project):
            return True
        self._log(
            f"{os.path.basename(old)} is outside this project -- another may read it, "
            "so a typed path does not rename it (Rename File does).",
            "warning",
        )
        return False

    def apply_path_edit(self, file_node, typed: str, shader=None, sync=None) -> str:
        """A path typed into *file_node*'s row: rename its file, or repoint it
        (mirror of mayatk's). *shader*: the material Keep Names In Sync follows
        (default: the image's one material).

        Returns:
            str: ``"renamed"`` or ``"repathed"``.

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        typed = str(typed or "").strip()
        img = self._image(file_node)
        if img is None:
            raise ValueError(f"Image {file_node!r} no longer exists.")
        if self.is_file_rename(img.filepath, typed, img):
            name = os.path.basename(typed.replace("\\", "/"))
            shader = shader or self._one_material(img)
            self.rename_texture(
                {"file_node": img.name, "shader_node": shader}, name, sync=sync
            )
            return "renamed"
        self.set_texture_path(img, typed)
        return "repathed"

    # ------------------------------------------------------------------
    # Renaming -- a texture file, datablocks, and the names kept in sync
    # ------------------------------------------------------------------

    @staticmethod
    def _materials_of(img):
        """The material names whose graph reads *img*."""
        return list(btk.get_image_material_map().get(getattr(img, "name", ""), []))

    def _one_material(self, img):
        """The ONE material reading *img*, ``None`` for several or none."""
        mats = self._materials_of(img)
        return mats[0] if len(mats) == 1 else None

    def rename_texture(self, context, new_name: str, sync=None) -> bool:
        """Rename the texture file *context* names to *new_name* (mirror of mayatk's).

        With *sync* -- Keep Names In Sync's ``(material affix, image affix)`` -- a
        new base name for a file of the material's own texture set renames the
        whole material (:meth:`_sync_names`); any other rename -- another set's
        map, a new map type, an image several materials read -- renames that
        file alone and says so. A lightmap row's bake markers are re-stamped
        (:meth:`rename_lightmap`).

        Parameters:
            context: ``{"file_node", "shader_node"}`` (the image, and the
                material Keep Names In Sync follows), or ``{"lightmap": record}``.

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        import bpy

        new_name = str(new_name or "").strip()
        dep = context.get("lightmap")
        if dep is not None:
            self.rename_lightmap(dep, new_name)
            return True
        img = self._image(context.get("file_node") or "")
        if img is None:
            raise ValueError(f"Image {context.get('file_node')!r} no longer exists.")
        path = self._resolve(img.filepath, img.library)
        old_name = bpy.path.basename(img.filepath)
        material = context.get("shader_node")
        syncing = bool(sync) and bool(material)
        base = ptk.MapFactory.get_base_texture_name(new_name)
        if syncing and self._sync_carries(
            material, base, sync, texture=(path, new_name)
        ):
            self._sync_names(material, base, sync)
            return True
        result = btk.MatUtils.rename_texture_file(path, new_name)
        self._log(
            f"Renamed {old_name} -> {new_name}; repointed {len(result['images'])} image(s)."
        )
        if syncing:
            self._log(
                self._SYNC_ALONE.format(name=old_name, material=material), "warning"
            )
        elif sync:
            readers = self._materials_of(img)
            if readers:
                self._log(
                    f"Keep Names In Sync: {old_name} was renamed alone -- "
                    f"{len(readers)} materials read it.",
                    "warning",
                )
        return True

    def can_rename_shader(self, shader) -> bool:
        """Whether *shader* is one local material -- what a material cell may rename."""
        if not shader:
            return False
        try:
            import bpy
        except ImportError:
            return False
        mat = bpy.data.materials.get(shader)
        return (
            mat is not None
            and mat.library is None
            and getattr(mat, "override_library", None) is None
        )

    def rename_shader(self, shader, new_name: str, sync=None) -> str:
        """Rename the material *shader*; with *sync*, its whole set to the new base.

        Returns:
            str: The material's name now (Blender may de-duplicate it).

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        import bpy

        new_name = str(new_name or "").strip()
        if sync:
            base = ptk.StrUtils.strip_known_affix(new_name, *sync[0]).strip("_")
            plan = self._sync_names(shader, base, sync)
            return (plan.get("material") or (shader, shader))[1]
        mat = bpy.data.materials.get(shader or "")
        if mat is None:
            raise ValueError(f"Material {shader!r} no longer exists.")
        return self._rename_block(mat, new_name, "material")

    def rename_file_node(self, file_node, new_name: str, shader=None, sync=None) -> str:
        """Rename *file_node*'s image datablock; with *sync*, its material when the
        name is a new base for the set's own texture (mirror of mayatk's).

        Returns:
            str: The image's name now.

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        new_name = str(new_name or "").strip()
        img = self._image(file_node)
        if img is None:
            raise ValueError(f"Image {file_node!r} no longer exists.")
        if sync and shader:
            base = ptk.MapFactory.get_base_texture_name(
                ptk.StrUtils.strip_known_affix(new_name, *sync[1])
            ).strip("_")
            if self._sync_carries(shader, base, sync, node=(img.name, new_name)):
                plan = self._sync_names(shader, base, sync)
                return dict(plan["file_nodes"]).get(img.name, img.name)
            old = img.name
            actual = self._rename_block(img, new_name, "image")
            self._log(self._SYNC_ALONE.format(name=old, material=shader), "warning")
            return actual
        return self._rename_block(img, new_name, "image")

    def _rename_block(self, block, new_name: str, label: str) -> str:
        """Rename a datablock; a linked one refuses (ValueError)."""
        old = block.name
        if not new_name or new_name == old:
            return old
        try:
            block.name = new_name
        except (AttributeError, RuntimeError, ReferenceError) as e:  # linked
            raise ValueError(f"Failed to rename {label} {old!r}: {e}") from e
        self._log(f"Renamed {label} '{old}' -> '{block.name}'")
        return block.name

    def _sync_plan(self, shader, base: str, sync, dry_run: bool = False) -> dict:
        """``MatUtils.sync_material_names`` for *shader* at *base* with *sync*'s
        affixes and the lightmap records; the plan carries mayatk's
        ``"file_nodes"`` key beside Blender's ``"images"``."""
        shader_affix, image_affix = sync
        plan = btk.MatUtils.sync_material_names(
            shader,
            base,
            material_affix=tuple(shader_affix),
            image_affix=tuple(image_affix),
            dry_run=dry_run,
            lightmaps=self._lightmap_records(),
        )
        plan = dict(plan)
        plan.setdefault("file_nodes", plan.get("images", []))
        return plan

    def _sync_carries(self, shader, base: str, sync, texture=None, node=None) -> bool:
        """Whether Keep Names In Sync renames *shader*'s whole material for an
        edit: the sync at *base* (a dry run) gives the edited thing exactly the
        name typed (case aside) -- *texture* ``(absolute path, new file name)``
        or *node* ``(image name, new image name)``. Mirror of mayatk's.

        Raises:
            ValueError: The sync refuses *base*.
        """
        plan = self._sync_plan(shader, base, sync, dry_run=True)
        if texture is not None:
            path, name = texture
            planned = next(
                (
                    n
                    for old, n in plan["textures"]
                    if ptk.FileUtils.is_same_file(old, path)
                ),
                None,
            )
        else:
            edited, name = node
            planned = dict(plan["file_nodes"]).get(edited)
            name = ptk.StrUtils.apply_affix(name, *sync[1])
        return bool(planned) and planned.lower() == name.lower()

    def _sync_names(self, shader, base: str, sync) -> dict:
        """Name *shader*'s material, its texture set, lightmap and images for
        *base*, and report it."""
        plan = self._sync_plan(shader, base, sync)
        changed = int(bool(plan["material"])) + sum(
            len(plan[key])
            for key in ("companions", "textures", "file_nodes", "lightmaps")
        )
        self._log(
            f"Names synced to {base!r}: {changed} rename(s) "
            f"({len(plan['textures'])} texture file(s), {len(plan['lightmaps'])} "
            "lightmap(s))."
        )
        for reason in plan["skipped"]:
            self._log(f"Sync Names: {reason}", "warning")
        return plan

    # ------------------------------------------------------------------
    # Selection / graph / remove
    # ------------------------------------------------------------------

    def select_objects(self, shaders=(), lightmaps=()) -> int:
        """Select the objects using the materials *shaders* and those *lightmaps*
        bind (replacing the selection, on the window's view layer).

        Returns:
            int: How many objects were selected (none: the selection is left).
        """
        import bpy

        objects = []
        for dep in lightmaps:
            for name in dep.get("objects") or []:
                obj = bpy.data.objects.get(name)
                if obj is not None:
                    objects.append(obj)
        for shader in shaders:
            mat = bpy.data.materials.get(shader) if shader else None
            if mat is not None:
                objects.extend(btk.find_by_mat_id(mat) or [])
        objects = list(dict.fromkeys(objects))
        if not objects:
            return 0
        return len(CoreUtils.select(objects, mode="replace"))

    def select_file_nodes(self, file_nodes) -> int:
        """Select the Image Texture nodes bound to *file_nodes*' images (Maya's file
        node splits into the datablock and the nodes reading it); returns how many."""
        images = self._images(file_nodes)
        return btk.select_image_nodes(images) if images else 0

    def graph_file_nodes(self, file_nodes) -> None:
        """Graph the materials reading *file_nodes*' images in the Shader Editor
        (the editor alone when no material reads them)."""
        import bpy

        names = {m for img in self._images(file_nodes) for m in self._materials_of(img)}
        mats = [m for m in (bpy.data.materials.get(n) for n in sorted(names)) if m]
        if mats:
            btk.graph_materials(mats)
        else:
            btk.open_editor("Shader Editor")

    def delete_consequences(self, file_nodes):
        """What removing *file_nodes*' images would leave behind, as sentences: the
        Image Texture nodes left with no texture, and the files kept on disk."""
        images = self._images(file_nodes)
        orphaned = len(btk.image_texture_nodes(images)) if images else 0
        lines = []
        if orphaned:
            lines.append(
                f"{orphaned} Image Texture node(s) will be left with no texture."
            )
        lines.append("The file(s) on disk are not deleted.")
        return lines

    @CoreUtils.undoable
    def delete_file_nodes(self, file_nodes) -> int:
        """Remove *file_nodes*' image datablocks from the .blend (one undo step).

        Neither the file on disk nor the nodes using it are deleted: every
        ``ShaderNodeTexImage`` that referenced one survives with an empty slot.
        Returns how many were removed.
        """
        import bpy

        removed = 0
        for img in self._images(file_nodes):
            try:
                bpy.data.images.remove(img)
                removed += 1
            except (RuntimeError, ReferenceError) as e:
                self._log(f"Failed to remove image: {e}", "warning")
        if removed:
            self._log(f"Removed {removed} texture(s).")
        return removed

    # ------------------------------------------------------------------
    # Lightmap dependencies -- rows with no image behind them
    # ------------------------------------------------------------------

    @staticmethod
    def _lightmap_records():
        """The lightmap record (a class used as a namespace), imported on use."""
        from blendertk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        return LightmapRecords

    def lightmap_dependencies(self):
        """The file's lightmap dependency records (``LightmapRecords``); ``[]``
        without ``bpy``."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return []
        return self._lightmap_records().lightmap_dependencies()

    def lightmap_labels(self, dep):
        """``(material, node)`` labels for a lightmap row (mirror of mayatk's): the
        material(s) the lightmapped objects wear (``""`` for none), and the map's
        stem -- what an image for it would be called."""
        import bpy

        mats = []
        for name in dep.get("objects") or []:
            obj = bpy.data.objects.get(name)
            for slot in getattr(obj, "material_slots", []) if obj is not None else []:
                if slot.material is not None and slot.material.name not in mats:
                    mats.append(slot.material.name)
        label = ", ".join(mats[:2])
        if len(mats) > 2:
            label += f" (+{len(mats) - 2})"
        return label, os.path.splitext(dep["map"])[0]

    @staticmethod
    def _lightmap_objects(lightmaps):
        """The object names the records name -- the records' scope argument."""
        return list(
            dict.fromkeys(o for dep in lightmaps for o in (dep.get("objects") or []))
        )

    @staticmethod
    def _lightmap_maps(lightmaps):
        """The file names the records carry -- a relocation's scope (a row IS its
        file; the reflection probe's row names no object)."""
        return list(dict.fromkeys(dep["map"] for dep in lightmaps))

    def normalize_lightmaps(self, lightmaps, relative: bool) -> int:
        """Re-spell the scoped lightmap markers' folders: ``//``-relative inside the
        project (*relative*) or absolute. Files never move."""
        if not lightmaps:
            return 0
        try:
            count = self._lightmap_records().normalize_lightmap_paths(
                self._lightmap_objects(lightmaps), relative=relative
            )
        except Exception as e:  # noqa: BLE001 -- the texture half already ran
            self._log(f"Lightmap folders not rewritten: {e}", "warning")
            return 0
        if count:
            self._log(
                f"{count} lightmap marker(s) now record their folder "
                f"{'relative to the project' if relative else 'as an absolute path'}."
            )
        return count

    def repath_lightmap(self, dep, folder: str) -> bool:
        """Point every marker naming *dep*'s map at *folder* (files untouched; the
        FBX manifest republished)."""
        folder = str(folder or "").replace("\\", "/").rstrip("/")
        if not folder:
            self._log("A lightmap path needs a folder.", "warning")
            return False
        try:
            count = self._lightmap_records().repath_lightmaps(
                {dep["map"].lower(): folder}, dep.get("objects")
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Failed to repath lightmap {dep['map']}: {e}", "warning")
            return False
        self._log(f"{dep['map']}: lightmap folder -> {folder} ({count} marker(s)).")
        return True

    def rename_lightmap(self, dep, new_name: str) -> None:
        """Rename a lightmap's file and re-stamp the markers that bind it by name.
        A map a LINKED object binds is its library's and is refused.

        Raises:
            ValueError, OSError: Not on disk, linked, or the name was refused.
        """
        import bpy

        if not dep.get("path"):
            raise ValueError(f"{dep['map']} is not on disk to rename.")
        linked = [
            name
            for name in dep.get("objects") or []
            if getattr(bpy.data.objects.get(name), "library", None) is not None
        ]
        if linked:
            raise ValueError(
                f"{dep['map']} is bound by linked object(s) {', '.join(linked)}; "
                "rename it in their library."
            )
        with CoreUtils.undo_chunk("Rename Lightmap") as step:
            try:
                btk.MatUtils.rename_texture_file(dep["path"], new_name)
            except Exception:
                step.cancel()  # refused before anything changed
                raise
            self._lightmap_records().rename_lightmap(dep["map"], new_name)
        self._log(f"Renamed lightmap {dep['map']} -> {new_name}.")

    def lightmap_revert_scope(self, deps):
        """``(objects, kept)``: the objects reverting *deps* would clear, and the
        LINKED ones it leaves (their markers are their library's)."""
        import bpy

        owners = self._lightmap_objects([d for d in deps if d.get("objects")])
        kept = [
            name
            for name in owners
            if getattr(bpy.data.objects.get(name), "library", None) is not None
        ]
        return [name for name in owners if name not in kept], kept

    def revert_lightmaps(self, objects):
        """The Lightmap Baker's Revert Selected on *objects* (markers cleared, the
        manifest republished, the map left on disk). One undo step. Returns the
        objects cleared."""
        return self._lightmap_records().revert(list(objects))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _log(self, message: str, level: str = "info", items=None) -> None:
        """Report one line -- or one titled list -- of a command's result: into
        :attr:`active_logger` when a caller lent one (a panel's pane), else this
        class's logger. A list goes through ``log_group`` as ONE record."""
        logger = self.active_logger or self.logger
        items = list(items) if items else []
        if items and hasattr(logger, "log_group"):
            logger.log_group(message, items, level=level)
            return
        if items:
            message = "\n".join([message, *(f"  {item}" for item in items)])
        getattr(logger, level, logger.info)(message)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
