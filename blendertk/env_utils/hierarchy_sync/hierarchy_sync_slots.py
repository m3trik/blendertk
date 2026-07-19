# !/usr/bin/python
# coding=utf-8
"""Slots for the Hierarchy Sync panel -- Blender port of mayatk's ``env_utils.hierarchy_sync``.

Diffs the current scene against a reference ``.blend`` and repairs drift (stub missing objects,
quarantine extras, fix reparented / fuzzy-renamed objects). Co-located with its engine
(``_hierarchy_sync.HierarchySync``), sidecar (``hierarchy_sidecar.HierarchySidecar``), and
panel (``hierarchy_sync.ui``, copied verbatim from mayatk). Discovered by ``BlenderUiHandler``
(``marking_menu.show("hierarchy_sync")``).

Scope of this port:
    * **Diff** and **Fix** are fully live: hierarchy comparison (missing / extra / reparented /
      fuzzy-renamed detection) and repair (create stub Empties, quarantine extras, fix reparented,
      fix fuzzy renames) all work against a real reference file.
    * **Pull** is live (``ObjectSwapper``). Where mayatk imports the reference into a temporary
      namespace and strips it across the pulled subtree + its connected materials/shading engines,
      the Blender port APPENDS the matched objects fresh from the reference ``.blend``
      (``bpy.data.libraries.load(link=False)``) — native Append already delivers a fully-local
      object + mesh data + materials + animation Action in one call, so all of mayatk's
      namespace-strip / anim-curve-transfer machinery collapses to nothing. Each pulled object is
      grafted onto its reference hierarchy position (an existing stub or a rebuilt parent chain)
      preserving the reference world transform; the ancestor dupes Append drags in are cleaned up.
    * The reference "sandbox": Maya imports the reference file into a temporary namespace so its
      objects can be diffed/pulled without colliding with the current scene. Blender has no
      namespaces; the equivalent here is linking the reference ``.blend`` as a **library**
      (``blendertk.env_utils._env_utils.link_blend_file`` / ``remove_library`` — the same engine
      behind the Reference Manager panel) into its own nested collection, then removing the
      library when the reference path changes or the window is hidden. A linked object's
      ``.library`` attribute is the direct analogue of Maya's namespace prefix — no cleaning pass
      needed (see ``_hierarchy_sync.py``'s module docstring). Reference formats other than
      ``.blend`` (``.fbx`` — mayatk's first-class reference format) are converted to a throwaway
      temp ``.blend`` by ``_hierarchy_sync.stage_reference_blend`` first, then linked the same
      way; the temp file is cached alongside the library and deleted on teardown.
    * Locator-group atomic moves and ``lockNode`` stub protection are treated as architecturally
      absent (no Blender counterpart), not deferred placeholders — see ``_hierarchy_sync.py``.
      Anim-layer-aware curve transfer is likewise unneeded: Append copies each pulled object's
      Action wholesale, so there is no per-curve transfer to make layer-aware.
    * ``_apply_diff_options`` (auto-select + expand after Diff) is deliberately simplified
      relative to mayatk's ~600-line version, which accumulated debug-driven fallback passes (a
      "fuzzy child resolution" pass, a second variant pass stripping trailing digits off
      ``_GRP``/``_LOC`` suffixes, and heavy Qt repaint/selection-verification bookkeeping) — narrow
      patches for one specific rig's naming convention, not general functionality. This keeps the
      genuinely useful behavior (select + expand differences, root-only/leaves-only condensing,
      ignored-path filtering) without the accumulated cruft. Likewise, mayatk's "Diff Mode" combo
      (Full Hierarchy / Selected / Missing Only / Extra Only) is dropped: in the mayatk source
      only "Full Hierarchy Compare" ever had a real effect (clearing selection) — the other three
      modes changed nothing but a debug log line, so there is no working behavior to mirror.

``import bpy`` / ``qtpy`` are deferred into method bodies where the rest of this port's ported
Slots classes do so, EXCEPT for the module-level ``_MiddleButtonDragFilter`` (a ``QObject``
subclass needs its Qt base class resolved at class-definition time, matching mayatk's own
convention for this file).
"""
import os
from pathlib import Path
from typing import Any, Dict, List

from qtpy import QtCore, QtGui, QtWidgets

import pythontk as ptk

from blendertk.env_utils.hierarchy_sync._hierarchy_sync import (
    HierarchySync,
    ObjectSwapper,
    build_path,
    delete_objects,
    stage_reference_blend,
)
from blendertk.env_utils.hierarchy_sync.hierarchy_sidecar import HierarchySidecar
from blendertk.env_utils.hierarchy_sync.tree_renderer import HierarchyTreeRenderer
import blendertk.env_utils.hierarchy_sync.tree_utils as tree_utils
from blendertk.node_utils._node_utils import reparent as _reparent


class HierarchySyncController(ptk.LoggingMixin):
    """Controller for hierarchy management operations."""

    #: Local collection that sandboxes the linked reference objects. Excluded from the view layer
    #: so the reference never clutters the outliner/viewport — the Blender analogue of Maya's
    #: reference namespace, which the user likewise doesn't want mixed into the working scene.
    REFERENCE_SANDBOX_COLLECTION = "__hierarchy_sync_reference__"

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui

        self._redirect_logger(self.logger)

        self.hierarchy_sync = None
        self._current_diff_result = None
        self._reference_path = ""

        self._ignored_ref_paths = set()  # ignored paths in reference tree (tree000)
        self._ignored_cur_paths = set()  # ignored paths in current tree (tree001)
        self._hide_ignored = False  # 'Hide Ignored' toggle: hide vs dim ignored items

        # Cached reference library — avoids re-linking the same file for tree display + diff
        # analysis within a single session. Structure: {"path", "mtime", "library"} or None.
        self._cached_reference_import = None
        self._importing_reference = False

        self.tree = HierarchyTreeRenderer(self)

        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        self.logger.debug("HierarchySyncController initialized.")

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def _redirect_logger(self, logger) -> None:
        """Configure a logger to redirect output to the UI text widget."""
        logger.hide_logger_name(True)
        logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        logger.setup_logging_redirect(self.ui.txt003)

    @property
    def workspace(self):
        from blendertk.core_utils._core_utils import get_env_info

        workspace_path = get_env_info("workspace")
        if not workspace_path:
            self.logger.error("No saved .blend directory found.")
        return workspace_path

    @property
    def reference_path(self) -> str:
        """The current reference scene path."""
        return self._reference_path

    @reference_path.setter
    def reference_path(self, text: str) -> None:
        text = (text or "").strip()
        if text == self._reference_path:
            return
        self._reference_path = text
        self.logger.debug(f"Reference path changed: {text}")

        if not text:
            self._clear_analysis_cache()
            self.tree.show_reference_placeholder(self.ui.tree000)
            self._update_header_tooltips()
            return

        if not os.path.exists(text):
            self._clear_analysis_cache()
            self.tree.show_reference_error(self.ui.tree000, Path(text).stem, "File Not Found")
            self._update_header_tooltips()
            return

        self.logger.debug(f"Auto-refreshing reference tree for: {os.path.basename(text)}")
        self.populate_reference_tree(self.ui.tree000, text)
        self._update_header_tooltips()

    def _update_header_tooltips(self) -> None:
        """Set tree header tooltips to their respective full paths."""
        ref_path = self._reference_path
        self.ui.tree000.headerItem().setToolTip(0, ref_path if ref_path else "No reference scene set")
        import bpy

        scene = bpy.data.filepath or ""
        self.ui.tree001.headerItem().setToolTip(0, str(scene) if scene else "Untitled")

    # ------------------------------------------------------------------ diff
    def analyze_hierarchies(
        self,
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        filter_meshes: bool = False,
        filter_cameras: bool = False,
        filter_lights: bool = False,
    ) -> bool:
        """Link the reference file (or reuse the cached link) and diff it against the scene.

        ``filter_cameras`` / ``filter_lights`` exclude all CAMERA / LIGHT objects from the
        comparison (mirrors ``filter_meshes``).
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

            import bpy

            reference_objects = self._import_reference_cached(reference_path)
            if not reference_objects:
                self.logger.error("Failed to link reference file or no objects found")
                return False

            current_objects = [o for o in bpy.context.scene.objects if o.library is None]

            self.hierarchy_sync = HierarchySync(fuzzy_matching=fuzzy_matching, dry_run=dry_run)
            self._redirect_logger(self.hierarchy_sync.logger)

            self._current_diff_result = self.hierarchy_sync.analyze_hierarchies(
                current_objects,
                reference_objects,
                filter_meshes=filter_meshes,
                filter_cameras=filter_cameras,
                filter_lights=filter_lights,
            )

            if not self._current_diff_result:
                self.logger.warning("Analysis returned no results")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")
            self._current_diff_result = None
            return False

    def _clear_analysis_cache(self):
        """Clear the analysis cache to force re-analysis on next diff operation."""
        self.hierarchy_sync = None
        self._current_diff_result = None
        self.clear_ignored_paths()
        self._cleanup_cached_reference_import()
        self.logger.debug("Analysis cache cleared (ignore paths also reset)")

    def _on_window_hidden(self):
        """Unlink the reference library when the hierarchy sync is hidden."""
        self._cleanup_cached_reference_import()
        if hasattr(self, "ui") and hasattr(self.ui, "tree000"):
            self.ui.tree000.clear()
        self.logger.debug("Reference library unlinked on window hide.")

    def _cleanup_cached_reference_import(self):
        """Remove the cached reference library (mirrors mayatk's namespace-sandbox cleanup)."""
        if self._cached_reference_import is not None:
            lib = self._cached_reference_import.get("library")
            if lib is not None:
                import blendertk as btk

                try:
                    btk.remove_library(lib)
                except Exception as e:
                    self.logger.debug(f"Cached reference cleanup failed: {e}")
            # Delete the throwaway .blend a non-.blend reference (FBX) was staged into.
            temp_blend = self._cached_reference_import.get("temp_blend")
            if temp_blend and os.path.exists(temp_blend):
                try:
                    os.remove(temp_blend)
                except OSError as e:
                    self.logger.debug(f"Temp reference .blend cleanup failed: {e}")
            self._cached_reference_import = None
        # Drop the (now-empty) sandbox collection regardless of cache state, so a stale one from a
        # prior session is never left behind.
        self._remove_reference_sandbox_collection()

    def _ensure_reference_sandbox_collection(self):
        """Create (or reuse) the hidden collection that sandboxes the linked reference objects.

        Linked into the scene so its objects have a real user (surviving orphan cleanup), but
        excluded from the active view layer so they never show in the outliner/viewport.
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

    def _remove_reference_sandbox_collection(self):
        """Delete the sandbox collection (its linked objects are already gone with the library)."""
        import bpy

        wrapper = bpy.data.collections.get(self.REFERENCE_SANDBOX_COLLECTION)
        if wrapper is not None:
            try:
                bpy.data.collections.remove(wrapper)
            except Exception as e:
                self.logger.debug(f"Reference sandbox collection cleanup failed: {e}")

    @staticmethod
    def _exclude_layer_collection(collection) -> None:
        """Set the view-layer ``exclude`` flag on *collection* so it disappears from the outliner."""
        import bpy

        def _find(layer_coll):
            if layer_coll.collection == collection:
                return layer_coll
            for child in layer_coll.children:
                found = _find(child)
                if found is not None:
                    return found
            return None

        lc = _find(bpy.context.view_layer.layer_collection)
        if lc is not None:
            lc.exclude = True

    def _import_reference_cached(self, reference_path: str):
        """Link a reference file, reusing a cached link when the path matches.

        Returns the list of objects belonging to the linked library, or ``None`` on failure. The
        library is kept alive in ``_cached_reference_import`` so subsequent operations (tree
        display, diff analysis) can reuse it without re-linking.
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
                        cached_lib.name  # touch to detect a removed/invalidated reference
                        objects = self._reference_objects_for(cached_lib)
                        if objects:
                            self.logger.debug(f"Reusing cached reference link ({len(objects)} objects)")
                            return objects
                except (ReferenceError, OSError):
                    pass
                self.logger.debug("Cached reference link is stale, re-linking")

        self._cleanup_cached_reference_import()

        self.logger.progress(f"Linking reference: {os.path.basename(reference_path)}")

        import blendertk as btk

        # A .blend is linked directly; an FBX (or other) is first converted to a throwaway .blend
        # so the rest of the pipeline is format-agnostic. temp_blend must be cleaned up on teardown.
        blend_path, temp_blend = stage_reference_blend(resolved, self.logger)
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
        count = btk.link_blend_file(blend_path, link=True, instance=False, target_collection=wrapper)
        # Linking can register a fresh (included) layer-collection for the wrapper — re-assert the
        # exclusion so the freshly-linked reference objects stay out of the outliner.
        self._exclude_layer_collection(wrapper)
        if not count:
            return _abort("Failed to link reference file or no objects found")

        lib = next(
            (
                r["library"]
                for r in btk.list_libraries()
                if r["abspath"] and os.path.normpath(r["abspath"]).lower() == os.path.normpath(blend_path).lower()
            ),
            None,
        )
        if lib is None:
            return _abort("Linked the reference file but could not locate its library record")

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

    @staticmethod
    def _reference_objects_for(library) -> List:
        import bpy

        return [o for o in bpy.data.objects if o.library == library]

    # ------------------------------------------------------------------ repair
    def repair_hierarchies(
        self,
        create_stubs: bool = True,
        quarantine_extras: bool = True,
        quarantine_group: str = "_QUARANTINE",
        skip_animated: bool = True,
        fix_reparented: bool = True,
        fix_fuzzy_renames: bool = True,
        dry_run: bool = True,
    ) -> bool:
        """Run repair operations on the current scene to match the reference hierarchy.

        Requires a prior successful diff analysis. Ignored paths are automatically excluded.
        """
        if not self.hierarchy_sync or not self._current_diff_result:
            self.logger.error("Please run a diff analysis first.")
            return False

        effective = self._filter_ignored_from_diff()

        prev_dry_run = self.hierarchy_sync.dry_run
        self.hierarchy_sync.dry_run = dry_run

        results = {}
        try:
            # Fuzzy renames FIRST — renaming a parent makes its children resolvable, preventing
            # stub creation from claiming the target name and causing an unwanted .001 suffix.
            if fix_fuzzy_renames and effective.get("fuzzy_matches"):
                self.logger.progress(f"Renaming {len(effective['fuzzy_matches'])} fuzzy-matched items...")
                results["renamed"] = self.hierarchy_sync.fix_fuzzy_renames(effective["fuzzy_matches"])

                if results["renamed"]:
                    fuzzy_cur_prefixes = [
                        f["current_name"] for f in effective["fuzzy_matches"] if f.get("current_name")
                    ]
                    effective["extra"] = [
                        p
                        for p in effective["extra"]
                        if not any(p.startswith(prefix + "|") for prefix in fuzzy_cur_prefixes)
                    ]
                    fuzzy_ref_prefixes = [
                        f["target_name"] for f in effective["fuzzy_matches"] if f.get("target_name")
                    ]
                    effective["missing"] = [
                        p
                        for p in effective["missing"]
                        if not any(p.startswith(prefix + "|") for prefix in fuzzy_ref_prefixes)
                    ]

            if create_stubs and effective["missing"]:
                self.logger.progress(f"Creating stubs for {len(effective['missing'])} missing items...")
                results["stubs"] = self.hierarchy_sync.create_stubs(effective["missing"])

            # Reparent BEFORE quarantine: quarantining an extra parent first would strand any
            # reparented children that still need to move relative to it.
            if fix_reparented and effective["reparented"]:
                self.logger.progress(f"Fixing {len(effective['reparented'])} reparented items...")
                results["reparented"] = self.hierarchy_sync.fix_reparented(
                    effective["reparented"], skip_animated=skip_animated
                )

            if quarantine_extras and effective["extra"]:
                self.logger.progress(f"Quarantining {len(effective['extra'])} extra items...")
                results["quarantined"] = self.hierarchy_sync.quarantine_extras(
                    group=quarantine_group, paths=effective["extra"], skip_animated=skip_animated
                )
        finally:
            self.hierarchy_sync.dry_run = prev_dry_run

        total = sum(len(v) for v in results.values())
        if total == 0:
            self.logger.notice("No repairs needed or no applicable items found.")
            return False

        mode = "DRY-RUN" if dry_run else "APPLIED"
        parts = [f"{key}: {len(items)}" for key, items in results.items() if items]
        self.logger.result(f"[{mode}] Repairs — {', '.join(parts)}")

        if not dry_run:
            import bpy

            try:
                bpy.ops.ed.undo_push(message="Hierarchy Sync: Repair Hierarchies")
            except RuntimeError:
                pass
            self._clear_analysis_cache()

        return True

    def pull_objects(
        self,
        target_paths: List[str],
        reference_path: str,
        dry_run: bool = True,
        pull_children: bool = False,
        pull_mode: str = "Add to Scene",
    ) -> bool:
        """Pull selected reference objects into the current scene.

        Blender port of mayatk's ``pull_objects``: appends each matched reference object fresh
        from the staged reference ``.blend`` (native local copy + materials + Action) and grafts
        it onto its reference hierarchy position. See ``ObjectSwapper`` for the mechanism.

        Args:
            target_paths: reference hierarchy paths (``build_path`` keys) to pull.
            reference_path: path to the reference ``.blend`` or ``.fbx``.
            dry_run: report only, don't modify the scene.
            pull_children: pull the whole subtree, not just the selected object.
            pull_mode: "Add to Scene" (auto-suffix on collision) or "Merge Hierarchies" (replace
                the existing object at the target path).
        """
        if not target_paths:
            self.logger.error("No objects specified for pulling.")
            return False
        if not reference_path or not os.path.exists(reference_path):
            self.logger.error("Please specify a valid reference scene path.")
            return False

        # Ensure the reference is staged + linked, then build a full path map of its objects
        # (independent of any diff-time type filtering).
        reference_objects = self._import_reference_cached(reference_path)
        if not reference_objects:
            self.logger.error("Failed to link reference file or no objects found.")
            return False
        reference_path_map = {build_path(o): o for o in reference_objects}

        # Append from the STAGED .blend — for an FBX reference that's the throwaway temp .blend the
        # objects were converted into, never the raw .fbx (which libraries.load can't open).
        cached = self._cached_reference_import or {}
        source_blend = cached.get("temp_blend") or reference_path

        swapper = ObjectSwapper(dry_run=dry_run, pull_mode=pull_mode, pull_children=pull_children)
        self._redirect_logger(swapper.logger)

        success = swapper.pull_objects_from_reference(
            target_paths, source_blend, reference_path_map
        )

        if success and not dry_run:
            import bpy

            try:
                bpy.ops.ed.undo_push(message="Hierarchy Sync: Pull Objects")
            except RuntimeError:
                pass
            self._clear_analysis_cache()
            self.refresh_trees()

        return success

    @staticmethod
    def select_objects(object_names: List[str]) -> int:
        """Select objects in the Blender scene by name. Returns count of selected.

        ``bpy.data.objects.get(name)`` is ambiguous when a linked reference object shares the
        exact same name string as a local one (names are only unique per-library, not globally)
        — filter explicitly for a local match so this never selects/activates the wrong object.
        """
        import bpy

        local_by_name = {o.name: o for o in bpy.data.objects if o.library is None}
        valid = [local_by_name[n] for n in object_names if n in local_by_name]
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        for o in valid:
            o.select_set(True)
        if valid:
            bpy.context.view_layer.objects.active = valid[0]
        return len(valid)

    # ----------------------------- Tree orchestration ----------------------------- #

    def populate_reference_tree(self, tree_widget, reference_path: str = None):
        """Populate the reference tree — handles cache, library link, and rendering."""
        if reference_path:
            resolved = str(Path(reference_path).resolve())
            cached_path = self._cached_reference_import.get("path") if self._cached_reference_import else None
            if cached_path != resolved:
                self._clear_analysis_cache()
        else:
            self._clear_analysis_cache()

        reference_name = (Path(reference_path).stem or "Reference Scene") if reference_path else "Reference Scene"

        if not reference_path:
            self.tree.show_reference_placeholder(tree_widget, reference_name)
            return
        if not os.path.exists(reference_path):
            self.tree.show_reference_error(tree_widget, reference_name)
            return

        try:
            self._importing_reference = True
            objects = self._import_reference_cached(reference_path)
            if not objects:
                self.tree.show_reference_error(tree_widget, reference_name, "Failed to load reference")
                return
            self.tree.populate_reference_tree(tree_widget, objects, reference_name)
        except Exception as e:
            self.logger.error(f"Error loading reference hierarchy: {e}")
            self.tree.show_reference_error(tree_widget, reference_name, f"Error: {e}")
        finally:
            self._importing_reference = False

    def refresh_trees(self, restore_selection: bool = True):
        """Refresh both tree widgets with current hierarchy data."""
        if restore_selection:
            current_scene_selection = self.tree._store_tree_selection(self.ui.tree001)
            reference_selection = self.tree._store_tree_selection(self.ui.tree000)
            old_current_structure = self.tree._get_tree_structure(self.ui.tree001)
            old_reference_structure = self.tree._get_tree_structure(self.ui.tree000)

        self.tree.populate_current_scene_tree(self.ui.tree001)

        reference_path = self._reference_path
        reference_populated = False
        if reference_path:
            self.populate_reference_tree(self.ui.tree000, reference_path)
            reference_populated = True

        restored_count = 0
        if restore_selection:
            new_current_structure = self.tree._get_tree_structure(self.ui.tree001)
            new_reference_structure = self.tree._get_tree_structure(self.ui.tree000)

            if old_current_structure == new_current_structure and current_scene_selection:
                restored_count += self.tree._restore_tree_selection(self.ui.tree001, current_scene_selection)
            if (
                reference_populated
                and old_reference_structure == new_reference_structure
                and reference_selection
            ):
                restored_count += self.tree._restore_tree_selection(self.ui.tree000, reference_selection)

        if restored_count > 0:
            self.logger.success(f"Refreshed trees and restored {restored_count} selections (hierarchy unchanged).")
        else:
            self.logger.result("Refreshed trees (hierarchy may have changed — selection cleared).")

        self.tree.apply_ignore_styling(self.ui.tree000)
        self.tree.apply_ignore_styling(self.ui.tree001)

    # ----------------------------- Ignore support ----------------------------- #

    def _get_ignored_set(self, tree_widget):
        if tree_widget is self.ui.tree000:
            return self._ignored_ref_paths
        if tree_widget is self.ui.tree001:
            return self._ignored_cur_paths
        return set()

    def is_path_ignored(self, tree_widget, path):
        ignored = self._get_ignored_set(tree_widget)
        if path in ignored:
            return True
        return any(path.startswith(ip + "|") for ip in ignored)

    def clear_ignored_paths(self):
        self._ignored_ref_paths.clear()
        self._ignored_cur_paths.clear()

    def _filter_ignored_from_diff(self):
        if not self._current_diff_result:
            return {"missing": [], "extra": [], "reparented": [], "fuzzy_matches": []}

        missing = [
            p for p in self._current_diff_result.get("missing", []) if not self.is_path_ignored(self.ui.tree000, p)
        ]
        extra = [
            p for p in self._current_diff_result.get("extra", []) if not self.is_path_ignored(self.ui.tree001, p)
        ]
        reparented = [
            r
            for r in self._current_diff_result.get("reparented", [])
            if not self.is_path_ignored(self.ui.tree001, r.get("current_path", ""))
            and not self.is_path_ignored(self.ui.tree000, r.get("reference_path", ""))
        ]
        fuzzy = [
            f
            for f in self._current_diff_result.get("fuzzy_matches", [])
            if not self.is_path_ignored(self.ui.tree001, f.get("current_name", ""))
            and not self.is_path_ignored(self.ui.tree000, f.get("target_name", ""))
        ]
        return {"missing": missing, "extra": extra, "reparented": reparented, "fuzzy_matches": fuzzy}

    def log_diff_results(self):
        """Log detailed hierarchy difference analysis results using rich formatting."""
        if not self._current_diff_result:
            self.logger.error("No diff results available. Please analyze hierarchies first.")
            return

        effective = self._filter_ignored_from_diff()
        missing = effective["missing"]
        extra = effective["extra"]
        reparented = effective["reparented"]
        fuzzy_matches = effective["fuzzy_matches"]

        self.logger.log_divider()

        if missing:
            top_missing = HierarchySidecar.get_top_level(missing)
            missing_set = set(missing)
            items = []
            for t in top_missing[:10]:
                count = HierarchySidecar.count_descendants(t, missing_set)
                suffix = f"  ({count} nodes)" if count > 1 else ""
                items.append(f"  - {t}{suffix}")
            if len(top_missing) > 10:
                items.append(f"  ... and {len(top_missing) - 10} more top-level")
            self.logger.log_box(
                f"MISSING IN CURRENT SCENE ({len(missing)} nodes, {len(top_missing)} top-level)",
                items,
                level="WARNING",
            )

        if extra:
            top_extra = HierarchySidecar.get_top_level(extra)
            extra_set = set(extra)
            items = []
            for t in top_extra[:10]:
                count = HierarchySidecar.count_descendants(t, extra_set)
                link = self.logger.log_link(t, "select", node=t.rsplit("|", 1)[-1])
                suffix = f"  ({count} nodes)" if count > 1 else ""
                items.append(f"  + {link}{suffix}")
            if len(top_extra) > 10:
                items.append(f"  ... and {len(top_extra) - 10} more top-level")
            self.logger.log_box(
                f"EXTRA IN CURRENT SCENE ({len(extra)} nodes, {len(top_extra)} top-level)", items, level="INFO"
            )

        if reparented:
            items = [
                f"  ~ {self.logger.log_link(r.get('leaf', ''), 'select', node=r.get('current_path', '').rsplit('|', 1)[-1])}"
                for r in reparented[:10]
            ]
            if len(reparented) > 10:
                items.append(f"  ... and {len(reparented) - 10} more")
            self.logger.log_box(f"REPARENTED OBJECTS ({len(reparented)})", items, level="WARNING")

        if fuzzy_matches:
            fuzzy_rows = [[m.get("current_name", ""), m.get("target_name", "")] for m in fuzzy_matches[:10]]
            self.log_table(data=fuzzy_rows, headers=["Current", "Reference"], title=f"FUZZY MATCHES ({len(fuzzy_matches)})")
            if len(fuzzy_matches) > 10:
                self.logger.notice(f"  ... and {len(fuzzy_matches) - 10} more fuzzy matches")

        if not missing and not extra and not reparented:
            self.logger.success("Hierarchies match perfectly!")
        else:
            total_diffs = len(missing) + len(extra) + len(reparented)
            self.logger.warning(f"Found {total_diffs} hierarchy differences")
            diff_path = self._write_diff_report(missing, extra, reparented, fuzzy_matches)
            if diff_path:
                link = self.logger.log_link("Open full diff report", "open", path=diff_path)
                self.logger.info(link)

        self.logger.log_divider()

    def _write_diff_report(self, missing, extra, reparented, fuzzy_matches):
        """Write a full hierarchy diff report to a temp file. Returns the path, or None on failure."""
        import tempfile

        try:
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="hierarchy_diff_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("Hierarchy Diff Report\n")
                f.write("=" * 60 + "\n\n")
                f.write("Summary\n")
                f.write("-" * 40 + "\n")
                f.write(f"  Missing in current scene:  {len(missing)}\n")
                f.write(f"  Extra in current scene:    {len(extra)}\n")
                f.write(f"  Reparented:                {len(reparented)}\n")
                f.write(f"  Fuzzy matches:             {len(fuzzy_matches)}\n")
                f.write(f"  Total differences:         {len(missing) + len(extra) + len(reparented)}\n\n")

                if missing:
                    f.write(f"Missing in current scene ({len(missing)}):\n")
                    for p in missing:
                        f.write(f"  - {p}\n")
                    f.write("\n")
                if extra:
                    f.write(f"Extra in current scene ({len(extra)}):\n")
                    for p in extra:
                        f.write(f"  + {p}\n")
                    f.write("\n")
                if reparented:
                    f.write(f"Reparented ({len(reparented)}):\n")
                    for r in reparented:
                        f.write(f"  ~ {r.get('leaf', '')}\n")
                        f.write(f"      reference: {r.get('reference_path', '')}\n")
                        f.write(f"      current:   {r.get('current_path', '')}\n")
                    f.write("\n")
                if fuzzy_matches:
                    f.write(f"Fuzzy matches ({len(fuzzy_matches)}):\n")
                    for m in fuzzy_matches:
                        f.write(
                            f"  {m.get('current_name', '')}  <->  {m.get('target_name', '')}  "
                            f"({m.get('score', 0):.0%} match)\n"
                        )
            return path
        except OSError:
            return None

    @staticmethod
    def _coerce_str_list(value) -> List[str]:
        """QSettings round-trips a one-item list as a bare string — coerce back to a list."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    def get_recent_reference_scenes(self) -> List[str]:
        """Get recent reference scenes from settings."""
        recent_scenes = self._coerce_str_list(
            self.ui.settings.value("recent_reference_scenes", [])
        )
        return [scene for scene in recent_scenes if os.path.exists(scene)][-10:]

    def save_recent_reference_scene(self, scene_path: str):
        """Save reference scene to recent list."""
        if not scene_path:
            return
        scene_path = str(Path(scene_path).resolve())
        recent_scenes = self._coerce_str_list(
            self.ui.settings.value("recent_reference_scenes", [])
        )
        if scene_path in recent_scenes:
            recent_scenes.remove(scene_path)
        recent_scenes.append(scene_path)
        recent_scenes = recent_scenes[-10:]
        self.ui.settings.setValue("recent_reference_scenes", recent_scenes)


class _MiddleButtonDragFilter(QtCore.QObject):
    """Event filter enabling middle-mouse drag-to-reparent on a QTreeWidget.

    Installed on ``tree001``'s viewport to intercept middle-button presses and synthesise
    left-button events so Qt's built-in ``InternalMove`` drag-drop machinery handles the visual
    move. Also installed on the tree widget itself to intercept ``Drop`` events; after Qt
    completes the internal move, the filter calls back into the slots layer to mirror the
    reparent operation inside the Blender scene.
    """

    def __init__(self, parent=None, *, reparent_callback=None):
        super().__init__(parent)
        self._mid_dragging = False
        self._reparent_callback = reparent_callback
        self._dragged_items = []

    @staticmethod
    def _synth_mouse(etype, event, button=QtCore.Qt.LeftButton):
        return QtGui.QMouseEvent(etype, event.localPos(), button, button, event.modifiers())

    def eventFilter(self, obj, event):  # noqa: N802
        etype = event.type()
        is_viewport = not obj.inherits("QTreeWidget")

        if is_viewport:
            if etype == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.MiddleButton:
                tree = obj.parent()
                self._dragged_items = list(tree.selectedItems())
                self._mid_dragging = True
                QtCore.QCoreApplication.sendEvent(obj, self._synth_mouse(QtCore.QEvent.MouseButtonPress, event))
                return True

            if self._mid_dragging and etype == QtCore.QEvent.MouseMove:
                QtCore.QCoreApplication.sendEvent(obj, self._synth_mouse(QtCore.QEvent.MouseMove, event))
                return True

            if etype == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.MiddleButton:
                was_dragging = self._mid_dragging
                self._mid_dragging = False
                if was_dragging:
                    QtCore.QCoreApplication.sendEvent(obj, self._synth_mouse(QtCore.QEvent.MouseButtonRelease, event))
                    return True

            return super().eventFilter(obj, event)

        if etype == QtCore.QEvent.Drop and self._reparent_callback:
            # Let Qt handle the tree-item move first.
            result = super().eventFilter(obj, event)
            # Mirror every reparent via ONE batch callback. The callback rebuilds
            # the tree, which deletes every QTreeWidgetItem — a per-item callback
            # left the remaining iterations holding dangling C++ items
            # (RuntimeError + partial reparent on multi-select drags).
            moves = [(item, item.parent()) for item in self._dragged_items]
            self._dragged_items.clear()
            if moves:
                self._reparent_callback(moves)
            return result

        return super().eventFilter(obj, event)


class HierarchySyncSlots(ptk.LoggingMixin):
    """Slots class for hierarchy management UI operations.

    Maintains no business logic — routes UI events to :class:`HierarchySyncController`.
    """

    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hierarchy_sync

        self.ui.txt003.setText("")  # Log Output

        self.controller = HierarchySyncController(self)

        self._tree001_drag_filter = _MiddleButtonDragFilter(self.ui, reparent_callback=self._on_tree001_drop_reparent)

        self.controller._redirect_logger(self.logger)

        if hasattr(self.ui, "footer") and self.ui.footer:
            self.ui.footer.setDefaultStatusText("Browse for a reference scene and click Diff or Fix to begin.")

        self._show_startup_text()

        from blendertk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
        mgr.subscribe("NewSceneOpened", self._on_scene_changed, owner=self)
        mgr.connect_cleanup(self.ui, owner=self)

        if hasattr(self.ui, "on_hide"):
            self.ui.on_hide.connect(self.controller._on_window_hidden)

        self.controller.tree.populate_current_scene_tree(self.ui.tree001)
        self.controller._update_header_tooltips()

    def _on_scene_changed(self):
        """Reset UI state when a new scene is loaded."""
        self.controller._clear_analysis_cache()

        if not self.ui.isVisible():
            return

        self.controller.tree.populate_current_scene_tree(self.ui.tree001)

        ref_path = self.controller.reference_path
        if ref_path and os.path.exists(ref_path):
            self.controller.populate_reference_tree(self.ui.tree000, ref_path)
        else:
            self.controller.tree.show_reference_placeholder(self.ui.tree000)

        self.controller._update_header_tooltips()
        self._show_startup_text()

    def _show_startup_text(self):
        """Display startup instructions in the log output widget."""
        import bpy

        scene = bpy.data.filepath or ""
        scene_name = Path(scene).name if scene else "Untitled"
        workspace = self.controller.workspace or "(not set)"

        lines = [
            '<span style="color:#aaa; font-size:11px;">'
            "<b>Hierarchy Sync</b><br>"
            f"Scene: {scene_name}<br>"
            f"Workspace: {workspace}<br><br>"
            "<b>Workflow:</b><br>"
            "&nbsp;&nbsp;1. Browse for a reference .blend (folder icon in the source tree header).<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Open or switch scenes via the folder/history icons on the current scene tree header.<br>"
            "&nbsp;&nbsp;2. <b>Diff</b> &mdash; compare current scene against reference<br>"
            "&nbsp;&nbsp;3. <b>Fix</b> &mdash; auto-repair stubs, quarantine extras, fix reparented/renamed<br><br>"
            "Right-click trees for more options. "
            "Enable <i>Dry Run</i> in the header menu to preview without changes."
            "</span>",
        ]
        self.ui.txt003.setHtml("\n".join(lines))

    def header_init(self, widget):
        """Initialize the header widget."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            "QCheckBox",
            setText="Dry Run Mode",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Perform analysis without making actual changes.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb001",
            add=self._log_level_options,
            setCurrentIndex=1,  # Default to INFO
            setToolTip="Set the log level.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Ignored",
            setObjectName="chk_hide_ignored",
            setChecked=False,
            setToolTip="Hide ignored items from the trees instead of dimming them.",
        )
        widget.menu.chk_hide_ignored.toggled.connect(self._on_hide_ignored_toggled)

        widget.set_help_text(
            fmt(
                title="Hierarchy Sync",
                body="Compare, diff, and synchronise the scene hierarchy against a reference "
                ".blend file.",
                steps=[
                    "Click the <b>folder icon</b> in the source tree header to browse for a "
                    "reference .blend.",
                    "Press <b>Diff</b> to compare the current scene against the reference. "
                    "Differences are highlighted in the tree views and logged below.",
                    "Press <b>Fix</b> to auto-repair: create stub placeholders for missing "
                    "objects, quarantine extras, fix reparented/renamed objects.",
                ],
                sections=[
                    ("Header menu", [
                        "<b>Dry Run</b> — preview changes without modifying the scene.",
                        "<b>Log Level</b> — control verbosity of the output panel.",
                        "<b>Hide Ignored</b> — hide ignored items from the trees instead of "
                        "dimming them.",
                    ]),
                ],
                notes=[
                    "<b>Right-click</b> either tree for additional actions: refresh, show "
                    "differences, select objects.",
                    "<b>Pull</b> — select objects in the reference tree, then Pull to append them "
                    "into the scene at their reference hierarchy position.",
                ],
            )
        )

    def _on_hide_ignored_toggled(self, state):
        """Toggle whether ignored items are hidden from the trees or merely dimmed."""
        self.controller._hide_ignored = bool(state)
        self.controller.tree.apply_ignore_styling(self.ui.tree000)
        self.controller.tree.apply_ignore_styling(self.ui.tree001)

    def tree000_init(self, widget):
        """Initialize the reference/linked hierarchy tree widget."""
        if not hasattr(widget, "is_initialized") or not widget.is_initialized:
            widget.setEditTriggers(self.sb.QtWidgets.QAbstractItemView.NoEditTriggers)
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.ExtendedSelection)

            widget.configure_menu(hide_on_leave=True)
            widget.menu.add(
                "QPushButton", setText="Refresh Reference", setObjectName="b009",
                setToolTip="Refresh the reference hierarchy display.",
            )
            widget.menu.add(
                "QPushButton", setText="Analyze Hierarchies", setObjectName="b012",
                setToolTip="Analyze and compare current scene with reference hierarchy.",
            )
            widget.menu.add(
                "QPushButton", setText="Show Differences", setObjectName="b011",
                setToolTip="Highlight differences between hierarchies.",
            )
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton", setText="Ignore Selected", setObjectName="b013",
                setToolTip="Mark selected items as ignored (skipped during auto-selection and dimmed).",
            )
            widget.menu.add(
                "QPushButton", setText="Unignore Selected", setObjectName="b014",
                setToolTip="Remove ignore mark from selected items.",
            )

            widget.itemClicked.connect(self._on_reference_tree_item_clicked)

            widget.header_actions.add("browse", icon="folder", tooltip="Browse for reference .blend", callback=self.b003)
            widget.header_actions.add(
                "history", icon="history", tooltip="Recent reference scenes", callback=self._show_recent_references
            )
            widget.header_actions.add("refresh", icon="refresh", tooltip="Refresh reference tree", callback=self.b009)

            widget.is_initialized = True

        if not widget.topLevelItemCount():
            self.controller.tree.show_reference_placeholder(widget)

    def _on_reference_tree_item_clicked(self, item, column):
        if item.data(0, self.sb.QtCore.Qt.UserRole) == "browse_placeholder":
            self.b003()

    def _open_scene_dialog(self):
        """Browse for and open a Blender scene file."""
        scene_files = self.sb.file_dialog(
            file_types="Blender Files (*.blend);;All Files (*.*)", title="Open Scene:", start_dir=self.controller.workspace,
        )
        if scene_files:
            import bpy

            try:
                bpy.ops.wm.open_mainfile(filepath=scene_files[0])
            except RuntimeError as e:
                self.sb.message_box(str(e))

    def _show_recent_scenes(self):
        """Show a popup menu with recently opened .blend files."""
        from blendertk.core_utils._core_utils import get_recent_files

        recent = get_recent_files()

        menu = QtWidgets.QMenu(self.ui.tree001)
        menu.setToolTipsVisible(True)
        if not recent:
            empty_action = menu.addAction("(No recent scenes)")
            empty_action.setEnabled(False)
            menu.exec_(QtGui.QCursor.pos())
            return

        for scene_path in recent:
            action = menu.addAction(Path(scene_path).name)
            action.setToolTip(scene_path)
            action.setData(scene_path)

        action = menu.exec_(QtGui.QCursor.pos())
        if action:
            import bpy

            try:
                bpy.ops.wm.open_mainfile(filepath=action.data())
            except RuntimeError as e:
                self.sb.message_box(str(e))

    def _on_current_tree_item_clicked(self, item, column):
        if item.data(0, self.sb.QtCore.Qt.UserRole) == "open_scene_placeholder":
            self._open_scene_dialog()

    def _on_current_tree_item_renamed(self, item, column):
        """Rename the Blender object when the user edits a tree item's name."""
        if column != 0 or getattr(self, "_renaming_in_progress", False):
            return
        self._renaming_in_progress = True
        try:
            obj = item.data(0, self.sb.QtCore.Qt.UserRole)
            if obj is None:
                return
            try:
                obj.name
            except ReferenceError:
                return

            new_name = item.text(0).strip()
            if not new_name or new_name == obj.name:
                return

            try:
                old_name = obj.name
                obj.name = new_name
                item._raw_name = obj.name
                if obj.name != new_name:  # Blender resolved a collision with a .001 suffix
                    item.setText(0, obj.name)
                self.controller.logger.info(f"Renamed '{old_name}' → '{obj.name}'")
            except Exception as e:
                try:
                    item.setText(0, obj.name)
                except Exception:
                    pass
                self.controller.logger.error(f"Rename failed: {e}")
        finally:
            self._renaming_in_progress = False

    def _on_tree001_drop_reparent(self, moves):
        """Mirror tree-widget drag-drop reparents in the Blender scene.

        Called by :class:`_MiddleButtonDragFilter` with the whole dropped
        selection after Qt finishes moving the tree items. Every ``(obj,
        parent_obj)`` is resolved from its ``QTreeWidgetItem`` up front, before
        the final tree rebuild — ``refresh_trees`` clears ``tree001`` and
        destroys every item, so a per-item callback left the remaining items
        dangling (``RuntimeError`` + partial reparent). Unlike Maya, bpy object
        references stay valid across reparenting, so resolving item data before
        the single final refresh is sufficient (no UUID indirection needed).

        Parameters:
            moves: List of ``(item, new_parent_item)`` pairs; ``new_parent_item``
                is ``None`` when dropped at root.
        """
        role = self.sb.QtCore.Qt.UserRole
        pending = []  # (obj, parent_obj | None-for-world) — resolved while items live
        for item, new_parent_item in moves:
            obj = item.data(0, role)
            if obj is None:
                continue
            try:
                obj.name
            except ReferenceError:
                continue
            if new_parent_item is not None:
                parent_obj = new_parent_item.data(0, role)
                if parent_obj is None:
                    self.controller.logger.warning("Drop target has no scene object — reparent skipped.")
                    continue
                pending.append((obj, parent_obj))
            else:
                pending.append((obj, None))  # dropped at root
        if not pending:
            return

        try:
            for obj, parent_obj in pending:
                # A manual drag is a deliberate act, so warn (don't skip) — unlike the
                # batched Fix path, which honours the 'Skip Animated' toggle.
                if HierarchySync._reparent_would_shift_animation(obj):
                    self.controller.logger.warning(
                        f"'{obj.name}' has animation — its motion may change under the new parent."
                    )
                _reparent([obj], parent_obj, keep_transform=True)
                if parent_obj is not None:
                    self.controller.logger.info(f"Reparented '{obj.name}' under '{parent_obj.name}'")
                else:
                    self.controller.logger.info(f"Reparented '{obj.name}' to world")
            self.controller.refresh_trees()
        except Exception as e:
            self.controller.logger.error(f"Reparent failed: {e}")
            self.controller.tree.populate_current_scene_tree(self.ui.tree001)

    def tree001_init(self, widget):
        """Initialize the current scene hierarchy tree widget."""
        if not hasattr(widget, "is_initialized") or not widget.is_initialized:
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.ExtendedSelection)

            widget.configure_menu(hide_on_leave=True)
            widget.menu.add(
                "QPushButton", setText="Refresh Current Scene", setObjectName="b005",
                setToolTip="Refresh the current scene hierarchy display.",
            )
            widget.menu.add(
                "QPushButton", setText="Select Objects", setObjectName="b006",
                setToolTip="Select the checked objects in the scene.",
            )
            widget.menu.add("QPushButton", setText="Expand All", setObjectName="b007", setToolTip="Expand all hierarchy items.")
            widget.menu.add("QPushButton", setText="Collapse All", setObjectName="b008", setToolTip="Collapse all hierarchy items.")
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton", setText="Ignore Selected", setObjectName="b015",
                setToolTip="Mark selected items as ignored (skipped during auto-selection and dimmed).",
            )
            widget.menu.add(
                "QPushButton", setText="Unignore Selected", setObjectName="b016",
                setToolTip="Remove ignore mark from selected items.",
            )
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton", setText="Rename from Reference", setObjectName="b017",
                setToolTip="Rename selected current-scene items using the names of selected "
                "reference-tree items (matched by order), or auto-apply fuzzy matches from "
                "the last diff if nothing is selected in the reference tree.",
            )
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton", setText="Delete Selected", setObjectName="b018",
                setToolTip="Delete the selected objects from the scene.",
            )

            del_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Delete), widget)
            del_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            del_shortcut.activated.connect(self.b018)
            widget._del_shortcut = del_shortcut

            widget.header_actions.add("open_scene", icon="folder", tooltip="Open a Blender scene", callback=self._open_scene_dialog)
            widget.header_actions.add("recent_scenes", icon="history", tooltip="Recent scenes", callback=self._show_recent_scenes)

            widget.itemClicked.connect(self._on_current_tree_item_clicked)
            widget.itemChanged.connect(self._on_current_tree_item_renamed)

            widget.setDragDropMode(self.sb.QtWidgets.QAbstractItemView.InternalMove)
            widget.setDefaultDropAction(self.sb.QtCore.Qt.MoveAction)
            widget.viewport().installEventFilter(self._tree001_drag_filter)
            widget.installEventFilter(self._tree001_drag_filter)

            widget.is_initialized = True

        self.controller.tree.populate_current_scene_tree(widget)

    def cmb_diff_options_init(self, widget):
        """Populate the diff-options WidgetComboBox below the Diff button."""
        items = []

        cmb_selection_mode = self.sb.registered_widgets.ComboBox()
        cmb_selection_mode.setObjectName("cmb_selection_mode")
        cmb_selection_mode.setToolTip("Select how differences should be selected in trees.")
        cmb_selection_mode.add(
            {
                "Select: All Differences": "all",
                "Select: Root Only": "root_only",
                "Select: Leaves Only": "leaves_only",
                "Select: No Auto-Selection": "none",
            }
        )
        items.append((cmb_selection_mode, "Selection Mode"))

        chk_expand_diff = self.sb.registered_widgets.CheckBox()
        chk_expand_diff.setText("Expand Difference Nodes")
        chk_expand_diff.setObjectName("chk_expand_diff")
        chk_expand_diff.setChecked(True)
        chk_expand_diff.setToolTip("Automatically expand nodes with differences.")
        items.append((chk_expand_diff, "Expand Difference Nodes"))

        chk_force_reanalysis = self.sb.registered_widgets.CheckBox()
        chk_force_reanalysis.setText("Force Re-analysis")
        chk_force_reanalysis.setObjectName("chk_force_reanalysis")
        chk_force_reanalysis.setChecked(False)
        chk_force_reanalysis.setToolTip("Force re-link and re-analysis even if reference was already analyzed.")
        items.append((chk_force_reanalysis, "Force Re-analysis"))

        chk_fuzzy_matching = self.sb.registered_widgets.CheckBox()
        chk_fuzzy_matching.setText("Enable Fuzzy Matching")
        chk_fuzzy_matching.setObjectName("chk_fuzzy_matching")
        chk_fuzzy_matching.setChecked(True)
        chk_fuzzy_matching.setToolTip("Enable fuzzy name matching for improved object identification.")
        items.append((chk_fuzzy_matching, "Enable Fuzzy Matching"))

        chk_filter_meshes = self.sb.registered_widgets.CheckBox()
        chk_filter_meshes.setText("Filter Mesh Objects")
        chk_filter_meshes.setObjectName("chk_filter_meshes")
        chk_filter_meshes.setChecked(False)
        chk_filter_meshes.setToolTip(
            "Exclude mesh-bearing objects from the comparison. When unchecked, all objects "
            "(including geometry) are compared."
        )
        items.append((chk_filter_meshes, "Filter Mesh Objects"))

        chk_filter_cameras = self.sb.registered_widgets.CheckBox()
        chk_filter_cameras.setText("Filter Cameras")
        chk_filter_cameras.setObjectName("chk_filter_cameras")
        chk_filter_cameras.setChecked(False)
        chk_filter_cameras.setToolTip("Exclude all camera objects from the comparison.")
        items.append((chk_filter_cameras, "Filter Cameras"))

        chk_filter_lights = self.sb.registered_widgets.CheckBox()
        chk_filter_lights.setText("Filter Lights")
        chk_filter_lights.setObjectName("chk_filter_lights")
        chk_filter_lights.setChecked(False)
        chk_filter_lights.setToolTip("Exclude all light objects from the comparison.")
        items.append((chk_filter_lights, "Filter Lights"))

        chk_ignore_quarantine = self.sb.registered_widgets.CheckBox()
        chk_ignore_quarantine.setText("Ignore Quarantine Group")
        chk_ignore_quarantine.setObjectName("chk_ignore_quarantine")
        chk_ignore_quarantine.setChecked(True)
        chk_ignore_quarantine.setToolTip(
            "Automatically ignore the quarantine group (e.g. _QUARANTINE) in the current scene "
            "tree during diff analysis."
        )
        items.append((chk_ignore_quarantine, "Ignore Quarantine Group"))

        widget.add(items, header="Diff Options", header_alignment="center", clear=True)
        widget.add_defaults_button = True

    def cmb_pull_options_init(self, widget):
        """Populate the pull-options WidgetComboBox below the Pull button."""
        items = []

        cmb_pull_mode = self.sb.registered_widgets.ComboBox()
        cmb_pull_mode.setObjectName("cmb_pull_mode")
        cmb_pull_mode.setToolTip("Select how objects should be pulled into the scene.")
        cmb_pull_mode.add(
            {
                "Mode: Add to Scene": "Add to Scene",
                "Mode: Merge Hierarchies": "Merge Hierarchies",
            }
        )
        items.append((cmb_pull_mode, "Pull Mode"))

        chk_pull_children = self.sb.registered_widgets.CheckBox()
        chk_pull_children.setText("Pull Children")
        chk_pull_children.setObjectName("chk_pull_children")
        chk_pull_children.setChecked(True)
        chk_pull_children.setToolTip(
            "Include all children when pulling. When enabled, complete subtrees are pulled; "
            "when disabled, only the selected objects are pulled."
        )
        items.append((chk_pull_children, "Pull Children"))

        widget.add(items, header="Pull Options", header_alignment="center", clear=True)
        widget.add_defaults_button = True

    def tb002_init(self, widget):
        """Initialize the Pull toggle button."""
        widget.setToolTip(
            "Pull the objects selected in the reference tree into the current scene, placing each "
            "at its reference hierarchy position (native append — brings materials + animation)."
        )

    def tb003_init(self, widget):
        """Initialize the fix/repair toggle button with options menu."""
        widget.option_box.menu.setTitle("Repair Options:")
        widget.option_box.menu.add(
            "QCheckBox", setText="Create Stubs (Missing)", setObjectName="chk_fix_stubs", setChecked=True,
            setToolTip="Create empty placeholder objects for items missing from the current scene.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Quarantine Extras", setObjectName="chk_fix_quarantine", setChecked=True,
            setToolTip="Move extra items (not in reference) to a quarantine group.",
        )
        widget.option_box.menu.add(
            "QLineEdit", setObjectName="txt_quarantine_name", setPlaceholderText="_QUARANTINE",
            setToolTip="Custom name for the quarantine group (leave blank for default).",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Skip Animated", setObjectName="chk_skip_animated", setChecked=True,
            setToolTip="Leave animated objects in place during Fix. Skips quarantining extras "
            "that carry (or hang under) an action, drivers, or constraints, and skips reparenting "
            "nodes whose keyed/driven local transforms would evaluate differently under a new "
            "parent. Uncheck to move them anyway.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Fix Reparented", setObjectName="chk_fix_reparented", setChecked=True,
            setToolTip="Move reparented nodes to match their reference hierarchy position.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Fix Fuzzy Renames", setObjectName="chk_fix_fuzzy_renames", setChecked=True,
            setToolTip="Rename nodes identified as fuzzy matches to their reference names.",
        )

    def tb001(self, state=None):
        """Run the diff analysis using settings from cmb_diff_options."""
        self.ui.txt003.clear()

        reference_path = self.controller.reference_path
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return
        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return

        dry_run = self.ui.chk002.isChecked()
        selection_mode = "all"
        expand_diff = True
        force_reanalysis = False
        fuzzy_matching = True
        filter_meshes = False
        filter_cameras = False
        filter_lights = False

        if hasattr(self.ui, "cmb_selection_mode"):
            selection_mode = self.ui.cmb_selection_mode.currentData() or selection_mode
        if hasattr(self.ui, "chk_expand_diff"):
            expand_diff = self.ui.chk_expand_diff.isChecked()
        if hasattr(self.ui, "chk_force_reanalysis"):
            force_reanalysis = self.ui.chk_force_reanalysis.isChecked()
        if hasattr(self.ui, "chk_fuzzy_matching"):
            fuzzy_matching = self.ui.chk_fuzzy_matching.isChecked()
        if hasattr(self.ui, "chk_filter_meshes"):
            filter_meshes = self.ui.chk_filter_meshes.isChecked()
        if hasattr(self.ui, "chk_filter_cameras"):
            filter_cameras = self.ui.chk_filter_cameras.isChecked()
        if hasattr(self.ui, "chk_filter_lights"):
            filter_lights = self.ui.chk_filter_lights.isChecked()

        auto_select = selection_mode != "none"
        select_root_only = selection_mode == "root_only"
        select_leaves_only = selection_mode == "leaves_only"

        if force_reanalysis:
            self.controller._clear_analysis_cache()
            self.logger.notice("Force re-analysis: cache cleared")

        self.logger.log_divider()
        self.logger.progress("Running diff analysis")

        success = self.controller.analyze_hierarchies(
            reference_path,
            fuzzy_matching,
            dry_run,
            filter_meshes=filter_meshes,
            filter_cameras=filter_cameras,
            filter_lights=filter_lights,
        )
        if not success:
            return

        self._ensure_trees_populated_for_diff(reference_path)

        ignore_quarantine = self.ui.chk_ignore_quarantine.isChecked() if hasattr(self.ui, "chk_ignore_quarantine") else True
        if ignore_quarantine:
            self._auto_ignore_quarantine_group()

        self.controller.log_diff_results()

        if self.controller._current_diff_result:
            self.controller.tree.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
            self.controller.tree.apply_ignore_styling(self.ui.tree000)
            self.controller.tree.apply_ignore_styling(self.ui.tree001)

        if auto_select or expand_diff:
            if self.count_tree_items(self.ui.tree000) > 0:
                try:
                    self._apply_diff_options(auto_select, expand_diff, select_root_only, select_leaves_only)
                except Exception as e:
                    self.logger.error(f"Auto-selection failed: {e}")
            else:
                self.logger.warning("Reference tree is empty — skipping auto-selection.")

        if hasattr(self.ui, "footer") and self.ui.footer:
            diff = self.controller._current_diff_result
            if diff:
                effective = self.controller._filter_ignored_from_diff()
                n_miss = len(effective["missing"])
                n_extra = len(effective["extra"])
                n_repar = len(effective["reparented"])
                n_fuzzy = len(effective.get("fuzzy_matches", []))
                if n_miss + n_extra + n_repar + n_fuzzy == 0:
                    self.ui.footer.setText("Diff: hierarchies match.")
                else:
                    parts = []
                    if n_miss:
                        parts.append(f"{n_miss} missing")
                    if n_extra:
                        parts.append(f"{n_extra} extra")
                    if n_repar:
                        parts.append(f"{n_repar} reparented")
                    if n_fuzzy:
                        parts.append(f"{n_fuzzy} renamed")
                    self.ui.footer.setText(f"Diff: {', '.join(parts)}.")

    def tb002(self, state=None):
        """Pull the reference-tree selection into the current scene."""
        self.ui.txt003.clear()

        # Gather selected reference-tree objects, skipping ignored paths. The ignore check
        # compares TREE paths (build_item_path — what the ignored set stores); the pulled target
        # is the object's build_path (what reference_path_map is keyed by). These coincide in the
        # common case but are resolved distinctly, exactly as mayatk does.
        tree = self.ui.tree000
        target_paths = []
        for item in tree_utils.get_selected_tree_items(tree):
            if self.controller.is_path_ignored(tree, self.controller.tree.build_item_path(item)):
                continue
            target_path = tree_utils._extract_object_name_from_item(item)
            if target_path:
                target_paths.append(target_path)
        if not target_paths:
            self.logger.error("Please select objects in the reference hierarchy tree.")
            return

        reference_path = self.controller.reference_path
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return

        dry_run = self.ui.chk002.isChecked()
        pull_mode = "Add to Scene"
        pull_children = True
        if hasattr(self.ui, "cmb_pull_mode"):
            pull_mode = self.ui.cmb_pull_mode.currentData() or pull_mode
        if hasattr(self.ui, "chk_pull_children"):
            pull_children = self.ui.chk_pull_children.isChecked()

        children_msg = "with children" if pull_children else "individual only"
        self.logger.log_divider()
        self.logger.notice(f"Pull: '{pull_mode}' mode, {children_msg}")

        self.controller.pull_objects(
            target_paths,
            reference_path,
            dry_run=dry_run,
            pull_children=pull_children,
            pull_mode=pull_mode,
        )

    def tb003(self, state=None):
        """Toggle button for fix/repair operations."""
        self.ui.txt003.clear()

        if not self.controller._current_diff_result:
            self.logger.error("Please run a diff analysis first (Diff button).")
            return

        dry_run = self.ui.chk002.isChecked()

        do_stubs = do_quarantine = do_reparent = do_fuzzy_renames = skip_animated = True
        quarantine_name = "_QUARANTINE"

        if hasattr(self.ui, "tb003"):
            _m = self.ui.tb003.option_box.menu
            if hasattr(_m, "chk_fix_stubs"):
                do_stubs = _m.chk_fix_stubs.isChecked()
            if hasattr(_m, "chk_fix_quarantine"):
                do_quarantine = _m.chk_fix_quarantine.isChecked()
            if hasattr(_m, "chk_skip_animated"):
                skip_animated = _m.chk_skip_animated.isChecked()
            if hasattr(_m, "chk_fix_reparented"):
                do_reparent = _m.chk_fix_reparented.isChecked()
            if hasattr(_m, "chk_fix_fuzzy_renames"):
                do_fuzzy_renames = _m.chk_fix_fuzzy_renames.isChecked()
            if hasattr(_m, "txt_quarantine_name"):
                custom_name = _m.txt_quarantine_name.text().strip()
                if custom_name:
                    quarantine_name = custom_name

        mode = "DRY-RUN" if dry_run else "LIVE"
        self.logger.log_divider()
        self.logger.progress(f"Running hierarchy repair ({mode})...")

        success = self.controller.repair_hierarchies(
            create_stubs=do_stubs,
            quarantine_extras=do_quarantine,
            quarantine_group=quarantine_name,
            skip_animated=skip_animated,
            fix_reparented=do_reparent,
            fix_fuzzy_renames=do_fuzzy_renames,
            dry_run=dry_run,
        )

        if success and not dry_run:
            self.controller.refresh_trees(restore_selection=False)
            self.logger.info("Scene modified — re-run Diff to see updated differences.")

        if hasattr(self.ui, "footer") and self.ui.footer:
            if success:
                self.ui.footer.setText(f"Fix: {mode} complete" if dry_run else "Fix: repairs applied")
            else:
                self.ui.footer.setText("Fix: nothing to repair")

    def b003(self):
        """Browse for reference scene file."""
        reference_file = self.sb.file_dialog(
            file_types="Blender Files (*.blend);;All Files (*.*)",
            title="Select Reference Scene:",
            start_dir=self.controller.workspace,
        )
        if reference_file:
            self.controller.reference_path = reference_file[0]
            self.controller.save_recent_reference_scene(reference_file[0])

    def _show_recent_references(self):
        """Show a popup menu with recent reference scenes."""
        recent = self.controller.get_recent_reference_scenes()

        menu = QtWidgets.QMenu(self.ui.tree000)
        menu.setToolTipsVisible(True)
        if not recent:
            empty_action = menu.addAction("(No recent files)")
            empty_action.setEnabled(False)
            menu.exec_(QtGui.QCursor.pos())
            return
        for scene_path in recent:
            action = menu.addAction(Path(scene_path).name)
            action.setToolTip(scene_path)
            action.setData(scene_path)

        action = menu.exec_(QtGui.QCursor.pos())
        if action:
            chosen = action.data()
            self.controller.reference_path = chosen
            self.controller.save_recent_reference_scene(chosen)

    def b005(self):
        """Refresh current scene hierarchy tree."""
        self.controller.tree.populate_current_scene_tree(self.ui.tree001)

    def b006(self):
        """Select checked objects in the scene."""
        object_names = self.controller.tree.get_selected_object_names(self.ui.tree001)
        object_names = [n.rsplit("|", 1)[-1] for n in object_names]
        if not object_names:
            self.logger.warning("No objects selected in hierarchy tree.")
            return
        self.controller.select_objects(object_names)

    def b007(self):
        """Expand all items in current scene tree."""
        self.ui.tree001.expandAll()

    def b008(self):
        """Collapse all items in current scene tree."""
        self.ui.tree001.collapseAll()

    def b009(self):
        """Refresh reference hierarchy tree."""
        reference_path = self.controller.reference_path
        if not reference_path:
            self.logger.notice("No reference scene set — browse for one first.")
            return
        self.controller.populate_reference_tree(self.ui.tree000, reference_path)

    def b011(self):
        """Show differences between hierarchies."""
        if not self.controller._current_diff_result:
            self.logger.error("Please analyze hierarchies first.")
            return
        self.controller.tree.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
        self.controller.tree.apply_ignore_styling(self.ui.tree000)
        self.controller.tree.apply_ignore_styling(self.ui.tree001)
        self.logger.debug("Applied difference highlighting to tree widgets.")

    def b012(self):
        """Analyze hierarchies and perform comparison (no auto-select/expand — see tb001)."""
        self.ui.txt003.clear()

        reference_path = self.controller.reference_path
        fuzzy_matching = self.ui.chk_fuzzy_matching.isChecked() if hasattr(self.ui, "chk_fuzzy_matching") else True
        filter_meshes = self.ui.chk_filter_meshes.isChecked() if hasattr(self.ui, "chk_filter_meshes") else False
        filter_cameras = self.ui.chk_filter_cameras.isChecked() if hasattr(self.ui, "chk_filter_cameras") else False
        filter_lights = self.ui.chk_filter_lights.isChecked() if hasattr(self.ui, "chk_filter_lights") else False
        dry_run = self.ui.chk002.isChecked()
        log_level = self.ui.cmb001.currentData()
        if log_level:
            self.logger.setLevel(log_level)

        success = self.controller.analyze_hierarchies(
            reference_path,
            fuzzy_matching,
            dry_run,
            filter_meshes=filter_meshes,
            filter_cameras=filter_cameras,
            filter_lights=filter_lights,
        )
        if success:
            self.controller.refresh_trees()
            self.controller.save_recent_reference_scene(reference_path)

    def b013(self):
        """Ignore selected items in the reference tree."""
        self._ignore_selected(self.ui.tree000)

    def b014(self):
        """Unignore selected items in the reference tree."""
        self._unignore_selected(self.ui.tree000)

    def b015(self):
        """Ignore selected items in the current scene tree."""
        self._ignore_selected(self.ui.tree001)

    def b016(self):
        """Unignore selected items in the current scene tree."""
        self._unignore_selected(self.ui.tree001)

    def b018(self):
        """Delete selected objects from the Blender scene and refresh the tree."""
        items = self.ui.tree001.selectedItems()
        if not items:
            self.logger.warning("No items selected to delete.")
            return

        import bpy

        objects = []
        for item in items:
            obj = item.data(0, self.sb.QtCore.Qt.UserRole)
            if obj is None:
                continue
            try:
                obj.name
            except ReferenceError:
                continue
            objects.append(obj)

        if not objects:
            self.logger.warning("No valid scene objects found in selection.")
            return

        selected_names = [o.name for o in objects]
        try:
            # Cascades to descendants — Maya parity: cmds.delete removes the whole subtree,
            # while a bare remove() would re-root the children.
            names = delete_objects(objects)
        except Exception as e:
            self.logger.error(f"Delete failed: {e}")
            return

        # Total counts the cascade; list only the selected roots (a big subtree would
        # otherwise dump hundreds of names into one log line).
        self.logger.info(
            f"Deleted {len(names)} object(s) (selected: {', '.join(selected_names)})"
        )
        # Undo integration is best-effort and must NOT gate the tree refresh: bpy.ops.ed.undo_push
        # needs a window context the tentacle Qt event-pump may lack (raises RuntimeError). Keeping
        # it inside the delete try previously swallowed that error and returned before refreshing,
        # so the object vanished from the scene but its row stayed in the table.
        try:
            bpy.ops.ed.undo_push(message="Hierarchy Sync: Delete Selected Objects")
        except RuntimeError:
            pass

        self.controller.refresh_trees()

    def b017(self):
        """Rename current-scene items to match reference names.

        Manual mode pairs selected items by selection order; auto mode (nothing selected in the
        reference tree) applies fuzzy matches from the last diff, restricted to any current-tree
        selection.
        """
        cur_items = self.ui.tree001.selectedItems()
        ref_items = self.ui.tree000.selectedItems()

        rename_pairs = []

        if ref_items:
            if not cur_items:
                self.logger.warning("Select at least one item in the current scene tree.")
                return
            pairs = min(len(cur_items), len(ref_items))
            if len(cur_items) != len(ref_items):
                self.logger.notice(
                    f"Selection counts differ (current={len(cur_items)}, reference={len(ref_items)}). "
                    f"Renaming first {pairs} pairs."
                )
            for ci, ri in zip(cur_items[:pairs], ref_items[:pairs]):
                rename_pairs.append((ci, ri.text(0).strip()))
        else:
            diff = self.controller._current_diff_result
            if not diff or not diff.get("fuzzy_matches"):
                self.logger.warning(
                    "No reference selection and no fuzzy matches available.\n"
                    "Select items in both trees, or run Diff first."
                )
                return

            fuzzy_list = diff["fuzzy_matches"]
            self.logger.info(f"Auto-rename: {len(fuzzy_list)} fuzzy match(es) from last diff.")

            cur_item_map = {}
            it = self.sb.QtWidgets.QTreeWidgetItemIterator(self.ui.tree001)
            while it.value():
                item = it.value()
                cur_item_map[self.controller.tree.build_item_path(item)] = item
                it += 1

            selected_paths = {self.controller.tree.build_item_path(i) for i in cur_items} if cur_items else set()

            for fz in fuzzy_list:
                cur_path = fz.get("current_name", "")
                ref_path = fz.get("target_name", "")
                if not cur_path or not ref_path:
                    continue
                ref_leaf = ref_path.rsplit("|", 1)[-1]
                item = cur_item_map.get(cur_path)
                if not item:
                    continue
                if selected_paths and cur_path not in selected_paths:
                    continue
                rename_pairs.append((item, ref_leaf))

            if not rename_pairs:
                self.logger.notice("No matching fuzzy items found in the current tree.")
                return

        renamed = 0
        for cur_item, new_name in rename_pairs:
            obj = cur_item.data(0, self.sb.QtCore.Qt.UserRole)
            if obj is None or not new_name:
                continue
            try:
                obj.name
            except ReferenceError:
                continue

            try:
                old_name = obj.name
                if new_name == old_name:
                    continue
                obj.name = new_name
                cur_item._raw_name = obj.name

                self.ui.tree001.blockSignals(True)
                try:
                    cur_item.setText(0, obj.name)
                finally:
                    self.ui.tree001.blockSignals(False)

                renamed += 1
                self.controller.logger.info(f"Renamed '{old_name}' → '{obj.name}'")
            except Exception as e:
                self.controller.logger.error(f"Failed to rename '{obj}': {e}")

        if renamed:
            self.logger.success(f"Renamed {renamed} object(s) from reference names.")
            if self.controller._current_diff_result:
                self.controller.tree.apply_difference_formatting(self.ui.tree001, self.ui.tree000)

    def _auto_ignore_quarantine_group(self):
        """Add the quarantine group path to the current-scene ignored set."""
        quarantine_name = "_QUARANTINE"
        if hasattr(self.ui, "tb003"):
            _m = self.ui.tb003.option_box.menu
            if hasattr(_m, "txt_quarantine_name"):
                custom = _m.txt_quarantine_name.text().strip()
                if custom:
                    quarantine_name = custom

        tree = self.ui.tree001
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0) == quarantine_name:
                self.controller._ignored_cur_paths.add(self.controller.tree.build_item_path(item))
                return

    def _ignore_selected(self, tree_widget):
        items = tree_widget.selectedItems()
        if not items:
            self.logger.warning("No items selected to ignore.")
            return

        ignored_set = self.controller._get_ignored_set(tree_widget)
        added = 0
        for item in items:
            path = self.controller.tree.build_item_path(item)
            if path not in ignored_set:
                ignored_set.add(path)
                added += 1

        self._refresh_tree_styling()
        tree_widget.clearSelection()
        self.logger.info(f"Ignored {added} items (descendants also ignored).")

    def _unignore_selected(self, tree_widget):
        items = tree_widget.selectedItems()
        if not items:
            self.logger.warning("No items selected to unignore.")
            return

        ignored_set = self.controller._get_ignored_set(tree_widget)
        removed = 0
        inherited_count = 0
        for item in items:
            path = self.controller.tree.build_item_path(item)
            if path in ignored_set:
                ignored_set.discard(path)
                to_remove = {p for p in ignored_set if p.startswith(path + "|")}
                ignored_set -= to_remove
                removed += 1 + len(to_remove)
            elif self.controller.is_path_ignored(tree_widget, path):
                inherited_count += 1

        self._refresh_tree_styling()
        if removed:
            self.logger.info(f"Unignored {removed} items.")
        if inherited_count:
            self.logger.warning(f"{inherited_count} item(s) ignored via a parent — unignore the parent to remove.")

    def _refresh_tree_styling(self):
        """Re-apply diff colors then ignore styling to both trees."""
        if self.controller._current_diff_result:
            self.controller.tree.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
        else:
            self.controller.tree.clear_tree_colors(self.ui.tree001)
            self.controller.tree.clear_tree_colors(self.ui.tree000)
        self.controller.tree.apply_ignore_styling(self.ui.tree000)
        self.controller.tree.apply_ignore_styling(self.ui.tree001)

    def _apply_diff_options(self, auto_select, expand_diff, select_root_only=False, select_leaves_only=False):
        """Auto-select and expand tree items for the current diff result.

        See the module docstring — deliberately simplified relative to mayatk's ~600-line version.
        """
        diff = self.controller._current_diff_result
        if not diff:
            return

        tree001 = self.ui.tree001
        tree000 = self.ui.tree000

        def _condense(paths):
            if select_root_only:
                ordered = sorted(paths, key=lambda p: p.count("|"))
                kept = []
                for p in ordered:
                    if not any(p.startswith(r + "|") for r in kept):
                        kept.append(p)
                return kept
            if select_leaves_only:
                path_set = set(paths)
                return [p for p in paths if not any(o != p and o.startswith(p + "|") for o in path_set)]
            return list(paths)

        def _select_path(path, matcher, by_full, by_last):
            candidates, _ = matcher.find_path_matches(path, by_full, by_last, strict=False)
            for c in candidates:
                c.setSelected(True)
                parent = c.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
            return candidates

        def _select_children(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if not self.controller.is_path_ignored(item.treeWidget(), self.controller.tree.build_item_path(child)):
                    child.setSelected(True)
                _select_children(child)

        tree_matcher = tree_utils.TreePathMatcher()
        self.controller._redirect_logger(tree_matcher.logger)
        ref_by_full, ref_by_last = tree_matcher.build_tree_index(tree000)
        cur_by_full, cur_by_last = tree_matcher.build_tree_index(tree001)

        if auto_select:
            tree001.clearSelection()
            tree000.clearSelection()

            missing = _condense(
                [p for p in diff.get("missing", []) if not self.controller.is_path_ignored(tree000, p)]
            )
            extra = _condense([p for p in diff.get("extra", []) if not self.controller.is_path_ignored(tree001, p)])

            for p in missing:
                _select_path(p, tree_matcher, ref_by_full, ref_by_last)
            for p in extra:
                _select_path(p, tree_matcher, cur_by_full, cur_by_last)
            for rp in diff.get("reparented", []):
                if rp.get("reference_path"):
                    _select_path(rp["reference_path"], tree_matcher, ref_by_full, ref_by_last)
                if rp.get("current_path"):
                    _select_path(rp["current_path"], tree_matcher, cur_by_full, cur_by_last)
            for fz in diff.get("fuzzy_matches", []):
                if fz.get("target_name"):
                    _select_path(fz["target_name"], tree_matcher, ref_by_full, ref_by_last)
                if fz.get("current_name"):
                    _select_path(fz["current_name"], tree_matcher, cur_by_full, cur_by_last)

            for tree in (tree000, tree001):
                it = self.sb.QtWidgets.QTreeWidgetItemIterator(tree)
                while it.value():
                    item = it.value()
                    if item.isSelected() and item.childCount():
                        _select_children(item)
                    it += 1

            n_ref = sum(1 for i in self._iter_items(tree000) if i.isSelected())
            n_cur = sum(1 for i in self._iter_items(tree001) if i.isSelected())
            if n_ref or n_cur:
                self.logger.success(f"Auto-select: {n_ref} item(s) in reference tree, {n_cur} item(s) in current tree.")

        if expand_diff:
            for path, tree in (
                *((p, tree000) for p in diff.get("missing", [])),
                *((p, tree001) for p in diff.get("extra", [])),
                *((rp.get("reference_path", ""), tree000) for rp in diff.get("reparented", [])),
                *((rp.get("current_path", ""), tree001) for rp in diff.get("reparented", [])),
                *((fz.get("target_name", ""), tree000) for fz in diff.get("fuzzy_matches", [])),
                *((fz.get("current_name", ""), tree001) for fz in diff.get("fuzzy_matches", [])),
            ):
                if not path:
                    continue
                item = self.controller.tree.find_tree_item_by_name(tree, path)
                if item is None:
                    continue
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()

    @staticmethod
    def _iter_items(tree_widget):
        it = QtWidgets.QTreeWidgetItemIterator(tree_widget)
        while it.value():
            yield it.value()
            it += 1

    def _ensure_trees_populated_for_diff(self, reference_path):
        """Ensure both trees are populated for diff visualization."""
        try:
            self.controller.tree.populate_current_scene_tree(self.ui.tree001)
            if reference_path:
                self.controller.populate_reference_tree(self.ui.tree000, reference_path)
        except Exception as e:
            self.logger.debug(f"Error ensuring trees populated for diff: {e}")

    def count_tree_items(self, tree_widget):
        """Count total items in a tree widget."""
        return sum(1 for _ in self._iter_items(tree_widget))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("hierarchy_sync", reload=True)
    ui.show(pos="screen", app_exec=True)
