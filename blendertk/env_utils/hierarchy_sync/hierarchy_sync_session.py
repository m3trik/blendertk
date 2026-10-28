# !/usr/bin/python
# coding=utf-8
"""Hierarchy Sync's host half: the reference a diff runs against, held in the
scene, and every scene operation the Hierarchy Sync panel performs -- mirror of
mayatk's ``env_utils.hierarchy_sync.hierarchy_sync_session``.

The panel is tentacle's shared tool (``slots/tools/hierarchy_sync.py``), one
class for both hosts: it holds a :class:`HierarchySyncSession`
(``btk.HierarchySyncSession``) and calls only this API, the calls mayatk's
``HierarchySyncSession`` answers for Maya. Blender has no namespaces: the
reference is linked as a **library** (``EnvUtils.link_blend_file``) into its own
collection, excluded from the view layer -- an ``.fbx`` reference is converted
to a throwaway ``.blend`` first (``HierarchySync.stage_reference_blend``) -- and
kept, cached by path and file time, until it is released. A tree node is the
``bpy`` object itself.

``import bpy`` is deferred into the call bodies: the panel opens (and this
class is built) without a running Blender, where every scene query answers
empty.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.env_utils.hierarchy_sync._hierarchy_sync import (
    HierarchySync,
    ObjectSwapper,
)


class HierarchySyncSession(ptk.LoggingMixin):
    """The reference held for a diff, the last analysis, and the scene calls
    the Hierarchy Sync panel makes (mirror of mayatk's ``HierarchySyncSession``).

    Attributes:
        hierarchy_sync: The engine of the last analysis (``None`` before one).
        diff_result: The last analysis' differences (``None`` before one).
    """

    #: Globs the current scene's Open Scene dialog offers. The reference
    #: browser is a different contract: :meth:`reference_formats`.
    SCENE_FILE_TYPES = ("*.blend",)

    #: The local collection the linked reference objects live in: excluded from
    #: the view layer, so the reference never clutters the outliner or the
    #: viewport -- the Blender analogue of Maya's reference namespace.
    REFERENCE_SANDBOX_COLLECTION = "__hierarchy_sync_reference__"

    def __init__(self):
        super().__init__()
        self.hierarchy_sync = None
        self.diff_result = None
        # {"path", "mtime", "library", "temp_blend"} of the held reference.
        self._cached_reference_import = None

    @classmethod
    def loggers(cls) -> list:
        """The loggers this session's work reports through: its own and its
        engines' (a panel redirects them into its log)."""
        return [cls.logger, HierarchySync.logger, ObjectSwapper.logger]

    @property
    def fuzzy_matching(self) -> bool:
        """Whether the last analysis matched names fuzzily."""
        return bool(self.hierarchy_sync and self.hierarchy_sync.fuzzy_matching)

    # ------------------------------------------------------------------ reference
    def is_loaded(self, reference_path: str) -> bool:
        """Whether *reference_path* is the reference this session holds."""
        if not reference_path or self._cached_reference_import is None:
            return False
        resolved = str(Path(reference_path).resolve())
        return self._cached_reference_import.get("path") == resolved

    def load_reference(self, reference_path: str) -> Optional[list]:
        """Link *reference_path* as a library, or reuse the link already held.

        The link is kept (path, file time, library, staged ``.blend``) so the
        tree and the diff share one link; a file changed on disk, or a library
        that was removed, is linked again.

        Returns:
            The reference's objects, or ``None`` on failure.
        """
        resolved = str(Path(reference_path).resolve())

        if self._cached_reference_import is not None:
            cached_path = self._cached_reference_import.get("path")
            cached_lib = self._cached_reference_import.get("library")
            if cached_path == resolved and cached_lib is not None:
                try:
                    cached_mtime = self._cached_reference_import.get("mtime")
                    current_mtime = os.path.getmtime(resolved)
                    if cached_mtime is not None and current_mtime != cached_mtime:
                        self.logger.notice("Reference file changed on disk, re-linking")
                    else:
                        cached_lib.name  # touch: a removed library raises here
                        objects = self._reference_objects_for(cached_lib)
                        if objects:
                            self.logger.debug(
                                f"Reusing cached reference link ({len(objects)} objects)"
                            )
                            return objects
                except (ReferenceError, OSError):
                    pass
                self.logger.debug("Cached reference link is stale, re-linking")

        self.release_reference()

        self.logger.progress(f"Linking reference: {os.path.basename(reference_path)}")

        from blendertk.env_utils._env_utils import EnvUtils

        # A .blend links directly; an FBX is converted to a throwaway .blend first,
        # so the rest is format-agnostic. The staged file is deleted on release.
        blend_path, temp_blend = HierarchySync.stage_reference_blend(
            resolved, self.logger
        )
        if not blend_path:
            return None

        def _abort(msg):
            self.logger.error(msg)
            if temp_blend and os.path.exists(temp_blend):
                try:
                    os.remove(temp_blend)
                except OSError:
                    pass
            return None

        wrapper = self._ensure_reference_sandbox_collection()
        count = EnvUtils.link_blend_file(
            blend_path, link=True, instance=False, target_collection=wrapper
        )
        # Linking can register a fresh (included) layer collection for the
        # wrapper: exclude it again so the linked objects stay out of view.
        self._exclude_layer_collection(wrapper)
        if not count:
            return _abort("Failed to link reference file or no objects found")

        lib = next(
            (
                r["library"]
                for r in EnvUtils.list_libraries()
                if r["abspath"]
                and os.path.normpath(r["abspath"]).lower()
                == os.path.normpath(blend_path).lower()
            ),
            None,
        )
        if lib is None:
            return _abort(
                "Linked the reference file but could not locate its library record"
            )

        objects = self._reference_objects_for(lib)
        if not objects:
            return _abort("Reference file contains no objects")

        self._cached_reference_import = {
            "path": resolved,
            "mtime": os.path.getmtime(resolved),
            "library": lib,
            "temp_blend": temp_blend,
        }
        self.logger.result(f"Linked {len(objects)} object(s) from reference")
        return objects

    def release_reference(self) -> None:
        """Remove the held reference library (and the ``.blend`` an FBX
        reference was staged into), and the sandbox collection."""
        if self._cached_reference_import is not None:
            lib = self._cached_reference_import.get("library")
            if lib is not None:
                from blendertk.env_utils._env_utils import EnvUtils

                try:
                    EnvUtils.remove_library(lib)
                except Exception as e:
                    self.logger.debug(f"Cached reference cleanup failed: {e}")
            temp_blend = self._cached_reference_import.get("temp_blend")
            if temp_blend and os.path.exists(temp_blend):
                try:
                    os.remove(temp_blend)
                except OSError as e:
                    self.logger.debug(f"Temp reference .blend cleanup failed: {e}")
            self._cached_reference_import = None
        # Regardless of the cache: never leave a stale one from a prior session.
        self._remove_reference_sandbox_collection()

    def _ensure_reference_sandbox_collection(self):
        """Create (or reuse) the hidden collection that holds the linked reference.

        Linked into the scene so its objects have a real user (surviving orphan
        cleanup), but excluded from the window's view layer.
        """
        import bpy

        scene_coll = bpy.context.scene.collection
        wrapper = bpy.data.collections.get(self.REFERENCE_SANDBOX_COLLECTION)
        if wrapper is None:
            wrapper = bpy.data.collections.new(self.REFERENCE_SANDBOX_COLLECTION)
        if scene_coll.children.get(wrapper.name) is None:
            scene_coll.children.link(wrapper)
        self._exclude_layer_collection(wrapper)
        return wrapper

    def _remove_reference_sandbox_collection(self) -> None:
        """Delete the sandbox collection (its linked objects left with the library)."""
        try:
            import bpy
        except ImportError:
            return
        wrapper = bpy.data.collections.get(self.REFERENCE_SANDBOX_COLLECTION)
        if wrapper is not None:
            try:
                bpy.data.collections.remove(wrapper)
            except Exception as e:
                self.logger.debug(f"Reference sandbox collection cleanup failed: {e}")

    @staticmethod
    def _exclude_layer_collection(collection) -> None:
        """Exclude *collection* from the window's view layer (the one the
        outliner shows; windowless, ``context.view_layer`` is the scene's default)."""

        def _find(layer_coll):
            if layer_coll.collection == collection:
                return layer_coll
            for child in layer_coll.children:
                found = _find(child)
                if found is not None:
                    return found
            return None

        lc = _find(CoreUtils._active_view_layer().layer_collection)
        if lc is not None:
            lc.exclude = True

    @staticmethod
    def _reference_objects_for(library) -> list:
        """The objects that came from *library*."""
        import bpy

        return [o for o in bpy.data.objects if o.library == library]

    # ------------------------------------------------------------------ analysis
    def analyze(
        self,
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        filter_meshes: bool = False,
        filter_cameras: bool = False,
        filter_lights: bool = False,
    ) -> bool:
        """Diff the scene's local objects against *reference_path* (linked or reused).

        Parameters:
            reference_path: The reference ``.blend`` or ``.fbx``.
            fuzzy_matching: Match renamed objects by name similarity.
            dry_run: The repairs this analysis feeds report without editing.
            filter_meshes: Leave mesh objects out of the diff.
            filter_cameras: Leave every camera out.
            filter_lights: Leave every light out.

        Returns:
            True if the analysis produced a result (:attr:`diff_result`).
        """
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return False
        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return False

        try:
            self.logger.progress(
                f"Analyzing hierarchy differences with: {os.path.basename(reference_path)}"
            )
            reference_objects = self.load_reference(reference_path)
            if not reference_objects:
                self.logger.error("Failed to link reference file or no objects found")
                return False

            self.hierarchy_sync = HierarchySync(
                fuzzy_matching=fuzzy_matching, dry_run=dry_run
            )
            self.diff_result = self.hierarchy_sync.analyze_hierarchies(
                self.current_scene_nodes(),
                reference_objects,
                filter_meshes=filter_meshes,
                filter_cameras=filter_cameras,
                filter_lights=filter_lights,
            )
            if not self.diff_result:
                self.logger.warning("Analysis returned no results")
                return False
            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")
            self.diff_result = None
            return False

    def clear_analysis(self) -> None:
        """Drop the last analysis and release the reference."""
        self.hierarchy_sync = None
        self.diff_result = None
        self.release_reference()
        self.logger.debug("Analysis cache cleared")

    def repair(
        self,
        differences: Optional[Dict[str, list]] = None,
        create_stubs: bool = True,
        quarantine_extras: bool = True,
        quarantine_group: str = "_QUARANTINE",
        skip_animated: bool = True,
        fix_reparented: bool = True,
        fix_fuzzy_renames: bool = True,
        dry_run: bool = True,
    ) -> bool:
        """Repair the scene toward the reference, from the last analysis.

        Renames run first (a renamed parent makes its children resolvable, so a
        stub never claims the name and takes a ``.001`` suffix), then stubs,
        then reparents (before quarantine: quarantining an extra parent first
        would strand a reparented child inside it), then quarantine. A live run
        is one undo step, and it leaves the analysis stale, so it clears it
        (:meth:`clear_analysis`).

        Parameters:
            differences: The differences to repair -- the panel's, its ignored
                paths left out; defaults to the whole :attr:`diff_result`.
            create_stubs: Create empty placeholders for missing objects.
            quarantine_extras: Move extra objects under *quarantine_group*.
            quarantine_group: The quarantine group's name.
            skip_animated: Leave an object whose motion would change.
            fix_reparented: Move reparented objects to their reference parent.
            fix_fuzzy_renames: Rename fuzzy-matched objects to their reference
                names (no animation guard: a Blender driver holds its object by
                pointer, so a rename breaks none).
            dry_run: Report what would change, without changing it.

        Returns:
            True if anything was (or, dry, would be) repaired.
        """
        if not self.hierarchy_sync or not self.diff_result:
            self.logger.error("Please run a diff analysis first.")
            return False

        source = differences if differences is not None else self.diff_result
        effective = {
            key: list(source.get(key, []))
            for key in ("missing", "extra", "reparented", "fuzzy_matches")
        }

        prev_dry_run = self.hierarchy_sync.dry_run
        self.hierarchy_sync.dry_run = dry_run
        results = {}
        try:
            if fix_fuzzy_renames and effective["fuzzy_matches"]:
                self.logger.progress(
                    f"Renaming {len(effective['fuzzy_matches'])} fuzzy-matched items..."
                )
                results["renamed"] = self.hierarchy_sync.fix_fuzzy_renames(
                    effective["fuzzy_matches"]
                )
                if results["renamed"]:
                    self._prune_resolved_by_renames(effective)

            if create_stubs and effective["missing"]:
                self.logger.progress(
                    f"Creating stubs for {len(effective['missing'])} missing items..."
                )
                results["stubs"] = self.hierarchy_sync.create_stubs(
                    effective["missing"]
                )

            if fix_reparented and effective["reparented"]:
                self.logger.progress(
                    f"Fixing {len(effective['reparented'])} reparented items..."
                )
                results["reparented"] = self.hierarchy_sync.fix_reparented(
                    effective["reparented"], skip_animated=skip_animated
                )

            if quarantine_extras and effective["extra"]:
                self.logger.progress(
                    f"Quarantining {len(effective['extra'])} extra items..."
                )
                results["quarantined"] = self.hierarchy_sync.quarantine_extras(
                    group=quarantine_group,
                    paths=effective["extra"],
                    skip_animated=skip_animated,
                )
        finally:
            self.hierarchy_sync.dry_run = prev_dry_run

        if not sum(len(v) for v in results.values()):
            self.logger.notice("No repairs needed or no applicable items found.")
            return False

        mode = "DRY-RUN" if dry_run else "APPLIED"
        parts = [f"{key}: {len(items)}" for key, items in results.items() if items]
        self.logger.result(f"[{mode}] Repairs — {', '.join(parts)}")

        if not dry_run:
            CoreUtils.undo_push("Hierarchy Sync: Repair Hierarchies")
            self.clear_analysis()
        return True

    @staticmethod
    def _prune_resolved_by_renames(effective: Dict[str, list]) -> None:
        """Drop the extras and missing objects a fuzzy rename resolved: the
        renamed parents' children now sit under the right name."""
        fuzzy = effective["fuzzy_matches"]
        cur_prefixes = [f["current_name"] for f in fuzzy if f.get("current_name")]
        ref_prefixes = [f["target_name"] for f in fuzzy if f.get("target_name")]
        effective["extra"] = [
            p
            for p in effective["extra"]
            if not any(p.startswith(prefix + "|") for prefix in cur_prefixes)
        ]
        effective["missing"] = [
            p
            for p in effective["missing"]
            if not any(p.startswith(prefix + "|") for prefix in ref_prefixes)
        ]

    def pull(
        self,
        targets: Sequence[str],
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        pull_children: bool = False,
        pull_mode: str = "Add to Scene",
    ) -> bool:
        """Bring reference objects into the current scene.

        Each target is APPENDED fresh from the staged reference ``.blend``
        (``ObjectSwapper``: a local copy with its data, materials and action)
        and grafted at its reference hierarchy position. A live run is one undo
        step, and it leaves the analysis stale, so it clears it
        (:meth:`clear_analysis`).

        Parameters:
            targets: The reference objects to pull, by hierarchy path
                (:meth:`node_path`).
            reference_path: The reference ``.blend`` or ``.fbx``.
            fuzzy_matching: Kept for the mirror (a Blender pull resolves each
                target by its exact path).
            dry_run: Report what would be pulled, without pulling it.
            pull_children: Pull each target's whole subtree.
            pull_mode: ``"Add to Scene"`` (a colliding name takes a ``.001``
                suffix) or ``"Merge Hierarchies"`` (replace the object already
                at the target's path).

        Returns:
            True if anything was (or, dry, would be) pulled.
        """
        targets = [t for t in targets if t]
        if not targets:
            self.logger.error("No objects specified for pulling.")
            return False
        if not reference_path or not os.path.exists(reference_path):
            self.logger.error("Please specify a valid reference scene path.")
            return False

        # The reference's full path map, whatever the diff filtered out.
        reference_objects = self.load_reference(reference_path)
        if not reference_objects:
            self.logger.error("Failed to link reference file or no objects found.")
            return False
        reference_path_map = {HierarchySync.build_path(o): o for o in reference_objects}

        # Append from the STAGED .blend: an FBX reference's throwaway .blend,
        # never the raw .fbx (which libraries.load cannot open).
        cached = self._cached_reference_import or {}
        source_blend = cached.get("temp_blend") or reference_path

        swapper = ObjectSwapper(
            fuzzy_matching=fuzzy_matching,
            dry_run=dry_run,
            pull_mode=pull_mode,
            pull_children=pull_children,
        )
        success = swapper.pull_objects_from_reference(
            targets, source_blend, reference_path_map
        )
        if success and not dry_run:
            CoreUtils.undo_push("Hierarchy Sync: Pull Objects")
            self.clear_analysis()
        return success

    # ------------------------------------------------------------------ the scene
    def current_scene_nodes(self) -> list:
        """The scene's local objects (the linked reference left out)."""
        try:
            import bpy
        except ImportError:
            return []
        all_objects = list(bpy.context.scene.objects)
        local = [o for o in all_objects if o.library is None]
        if len(local) != len(all_objects):
            self.logger.debug(
                f"Left {len(all_objects) - len(local)} reference-library object(s) "
                "out of the current scene"
            )
        return local

    @staticmethod
    def hierarchy_structure(nodes) -> Tuple[Dict[str, dict], List[str]]:
        """The tree a panel draws from *nodes* (mirror of mayatk's).

        Returns:
            ``({key: info}, root keys)``: each key is the object's hierarchy
            path (``HierarchySync.build_path``); ``info`` holds ``object`` (the
            object, what a tree item carries), ``short_name`` and
            ``display_name`` (its name -- no namespace to drop), ``node_name``
            (what ``NodeIcons`` resolves it by), ``type`` and ``parent`` (the
            parent's key, or ``None``).
        """
        items: Dict[str, dict] = {}
        roots: List[str] = []
        for obj in nodes:
            try:
                key = HierarchySync.build_path(obj)
                parent = obj.parent
                items[key] = {
                    "object": obj,
                    "short_name": obj.name,
                    "display_name": obj.name,
                    "node_name": obj.name,
                    "type": obj.type,
                    "parent": HierarchySync.build_path(parent)
                    if parent is not None
                    else None,
                }
                if parent is None:
                    roots.append(key)
            except Exception:
                continue
        return items, roots

    @staticmethod
    def clean_path(path: str) -> str:
        """*path* as the diff compares it: unchanged (Blender has no namespaces;
        a linked object keeps its source name)."""
        return path

    @staticmethod
    def node_path(node) -> str:
        """The name a pull takes *node* by: its hierarchy path (``""`` for a
        removed object or anything that is not one)."""
        if node is None or isinstance(node, str):
            return ""
        try:
            return HierarchySync.build_path(node)
        except (ReferenceError, AttributeError):
            return ""

    @staticmethod
    def node_name(node) -> str:
        """*node*'s name (``""`` for a removed object)."""
        if node is None or isinstance(node, str):
            return ""
        try:
            return node.name
        except (ReferenceError, AttributeError):
            return ""

    @staticmethod
    def node_alive(node) -> bool:
        """Whether *node* (a tree item's node) is an object still in the file."""
        if node is None or isinstance(node, str):
            return False
        try:
            node.name
        except (ReferenceError, AttributeError):
            return False
        return True

    def rename_nodes(self, pairs: Sequence[Tuple[Any, str]]) -> List[Optional[Any]]:
        """Rename each ``(object, new_name)`` pair, as one undo step.

        A removed object, an empty name or the name it already has is skipped;
        a collision takes Blender's ``.001`` suffix; a failure is logged.

        Returns:
            Per pair, in order: the renamed object, or ``None`` when it was not
            renamed.
        """
        results: List[Optional[Any]] = [None] * len(pairs)
        with CoreUtils.undo_chunk("Hierarchy Sync: Rename") as chunk:
            for i, (obj, new_name) in enumerate(pairs):
                if not new_name or not self.node_alive(obj):
                    continue
                old_name = obj.name
                if new_name == old_name:
                    continue
                try:
                    obj.name = new_name
                except Exception as e:
                    self.logger.error(f"Failed to rename '{old_name}': {e}")
                    continue
                self.logger.info(f"Renamed '{old_name}' → '{obj.name}'")
                results[i] = obj
            if not any(r is not None for r in results):
                chunk.cancel()  # nothing changed: no empty step in the history
        return results

    def reparent_nodes(self, moves: Sequence[Tuple[Any, Any]]) -> int:
        """Reparent each ``(object, parent)`` move (``parent=None``: to the
        world), keeping world transforms, as one undo step.

        A move already in place is skipped; an object whose keyed transforms
        would evaluate differently under the new parent is warned about (a drag
        is deliberate), not refused.

        Returns:
            How many moves were attempted (a failed one is logged).
        """
        from blendertk.node_utils._node_utils import NodeUtils

        pending = []
        for obj, parent in moves:
            if not self.node_alive(obj):
                continue
            if parent is not None and not self.node_alive(parent):
                self.logger.warning(
                    "Drop target has no scene object — reparent skipped."
                )
                continue
            current = obj.parent
            # bpy hands out a fresh wrapper per access: compare by ==, never is.
            in_place = current is None if parent is None else current == parent
            if in_place:
                continue
            pending.append((obj, parent))
        if not pending:
            return 0

        with CoreUtils.undo_chunk("Hierarchy Sync: Drag Reparent"):
            for obj, parent in pending:
                try:
                    if HierarchySync._reparent_would_shift_animation(obj):
                        self.logger.warning(
                            f"'{obj.name}' has animation — its motion may change "
                            "under the new parent."
                        )
                    NodeUtils.reparent([obj], parent, keep_transform=True)
                    if parent is None:
                        self.logger.info(f"Reparented '{obj.name}' to world")
                    else:
                        self.logger.info(
                            f"Reparented '{obj.name}' under '{parent.name}'"
                        )
                except Exception as e:
                    self.logger.error(f"Reparent failed for '{obj.name}': {e}")
        return len(pending)

    def delete_nodes(self, nodes: Sequence[Any]) -> List[str]:
        """Delete *nodes* and their descendants (``cmds.delete``'s reach), as
        one undo step.

        Returns:
            The deleted objects' names, descendants included. Raises what the
            delete raises.
        """
        live = [o for o in nodes if self.node_alive(o)]
        if not live:
            return []
        names = HierarchySync.delete_objects(live)
        # Never gates the caller's refresh: undo_push supplies the window a Qt
        # slot lacks, and never raises.
        CoreUtils.undo_push("Hierarchy Sync: Delete Selected Objects")
        return names

    @staticmethod
    def select_nodes(nodes: Sequence[Any]) -> int:
        """Select *nodes* (the first active), replacing the selection.

        Returns:
            How many were selected.
        """
        objects = [o for o in nodes if HierarchySyncSession.node_alive(o)]
        return len(CoreUtils.select(objects))

    @staticmethod
    def scene_path() -> str:
        """The open ``.blend``'s file, or ``""`` (unsaved, or no Blender)."""
        try:
            import bpy
        except ImportError:
            return ""
        return bpy.data.filepath or ""

    @staticmethod
    def workspace() -> str:
        """The current project's root, or ``""``."""
        try:
            return CoreUtils.get_env_info("workspace") or ""
        except Exception:
            return ""

    @staticmethod
    def recent_scenes() -> List[str]:
        """The recently opened ``.blend`` files, most recent first."""
        try:
            return CoreUtils.get_recent_files() or []
        except Exception:
            return []

    @staticmethod
    def open_scene(path: str) -> None:
        """Open *path* as the current file (Maya's forced ``file -open``).
        Raises ``RuntimeError`` on failure."""
        import bpy

        bpy.ops.wm.open_mainfile(filepath=path)

    @staticmethod
    def reference_formats() -> List[str]:
        """The extensions a reference may have (what the staging accepts)."""
        return HierarchySync.get_supported_formats()
