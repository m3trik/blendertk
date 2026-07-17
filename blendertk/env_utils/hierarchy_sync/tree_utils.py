# !/usr/bin/python
# coding=utf-8
"""Tree widget utilities for hierarchy sync UI operations — mirror of mayatk's
``env_utils.hierarchy_sync.tree_utils``.

Separated from ``hierarchy_sync_slots.py`` to keep the Qt-widget mechanics isolated. Unlike
mayatk (which needs a raw-vs-cleaned path split because a Maya DAG path may carry a namespace
prefix that must be stripped for comparison), Blender tree item text is already the clean
comparison key — there is no namespace to strip — so this drops the raw/cleaned index duality
and keeps a single full-path index plus a last-component fallback.

``qtpy`` is deferred into each function body (headless Blender ships no Qt binding).
"""
from typing import Any, Dict, List, Tuple

import pythontk as ptk

from blendertk.env_utils.hierarchy_sync._hierarchy_sync import build_path


class TreePathMatcher(ptk.LoggingMixin):
    """Tree path matching functionality for UI tree widgets."""

    def build_tree_index(self, widget):
        """Build tree indices for fast item lookup: full hierarchy path, and last component."""
        items = list(self._iter_items(widget))

        by_full: Dict[str, Any] = {}
        for item in items:
            path = self._get_item_path(item)
            if path:
                existing = by_full.get(path)
                if existing is None:
                    by_full[path] = item
                else:
                    if not isinstance(existing, list):
                        by_full[path] = [existing]
                    by_full[path].append(item)

        by_last: Dict[str, list] = {}
        for item in items:
            path = self._get_item_path(item)
            if path:
                by_last.setdefault(path.split("|")[-1], []).append(item)

        return by_full, by_last

    def find_path_matches(self, target_path: str, by_full: dict, by_last: dict, strict: bool = False):
        """Find tree items matching a target path — exact full-path match, falling back to
        last-component matching unless ``strict``."""
        candidates = []
        strategy = "none"

        item = by_full.get(target_path)
        if item is not None:
            candidates = [item]
            strategy = "full"

        if not strict and not candidates:
            last = target_path.split("|")[-1]
            candidates = by_last.get(last, [])
            if candidates:
                strategy = "last"

        if candidates:
            flat = []
            for entry in candidates:
                if isinstance(entry, (list, tuple, set)):
                    for sub in entry:
                        if sub is not None and sub not in flat:
                            flat.append(sub)
                else:
                    if entry is not None and entry not in flat:
                        flat.append(entry)
            candidates = flat

        return candidates, strategy

    def _iter_items(self, widget):
        """Iterate through all tree widget items recursively."""
        stack = [widget.topLevelItem(i) for i in range(widget.topLevelItemCount())]
        while stack:
            n = stack.pop()
            if not n:
                continue
            yield n
            for ci in range(n.childCount() - 1, -1, -1):
                stack.append(n.child(ci))

    def _get_item_path(self, item) -> str:
        """Extract the full hierarchy path from a tree widget item's display text chain."""
        parts = []
        cur = item
        while cur:
            parts.insert(0, cur.text(0))
            cur = cur.parent()
        return "|".join(parts)

    def log_matching_debug(self, path, candidates, strategy, prefix=""):
        """Log debug information about path matching."""
        self.logger.debug(f"{prefix} path '{path}' -> {len(candidates)} candidates via {strategy}")

    def log_tree_index_debug(self, by_full, by_last, tree_type):
        """Log debug information about tree indices."""
        self.logger.debug(f"{tree_type} tree index: {len(by_full)} full, {len(by_last)} last")


def get_selected_object_names(tree_widget) -> List[str]:
    """Extract object names from selected tree widget items."""
    return [
        name
        for item in get_selected_tree_items(tree_widget)
        if (name := _extract_object_name_from_item(item))
    ]


def get_selected_tree_items(tree_widget) -> list:
    """Get all selected items from tree widget."""
    from qtpy import QtWidgets

    selected_items = []
    iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
    while iterator.value():
        item = iterator.value()
        if item.isSelected():
            selected_items.append(item)
        iterator += 1
    return selected_items


#: UserRole payloads that are UI placeholders, not scene objects.
_PLACEHOLDER_USER_DATA = {"browse_placeholder", "open_scene_placeholder"}


def _extract_object_name_from_item(item) -> str:
    """Extract the hierarchy path from a tree widget item.

    Prefers the stored UserRole object's canonical hierarchy path (``build_path``): the
    display-text chain can drift from the live object's real name, and a bare leaf name is
    ambiguous when duplicate names exist under different parents. Placeholder rows (the
    'Browse…' / 'Open scene…' prompts) resolve to no object.
    """
    from qtpy import QtCore

    try:
        data = item.data(0, QtCore.Qt.UserRole)
    except Exception:
        data = None

    if isinstance(data, str):
        if data in _PLACEHOLDER_USER_DATA:
            return ""
    elif data is not None:
        try:
            return build_path(data)  # a live bpy object payload
        except (ReferenceError, AttributeError):
            pass

    raw_name = getattr(item, "_raw_name", None)
    parts = []
    current = item
    while current:
        parts.insert(0, current.text(0))
        current = current.parent()
    full = "|".join(parts) if len(parts) > 1 else parts[0] if parts else ""
    return full or raw_name or ""


def find_tree_item_by_name(tree_widget, object_name: str):
    """Find tree widget item by object name (or hierarchy path)."""
    from qtpy import QtWidgets

    iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
    while iterator.value():
        item = iterator.value()
        if _extract_object_name_from_item(item) == object_name:
            return item
        iterator += 1
    return None


def build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]:
    """Build hierarchical structure from Blender objects.

    Keys are the full pipe-path (``Grp|Child``, see ``build_path``) so same-named objects under
    different parents are preserved (Blender itself only guarantees name-uniqueness within a
    single parent-independent namespace, but the hierarchy display still benefits from full paths
    for consistency with the diff engine).

    Returns:
        Tuple of (object_items_dict, root_objects_list).
    """
    object_items: Dict[str, dict] = {}
    root_objects: List[str] = []

    for obj in objects:
        try:
            obj_key = build_path(obj)
            parent = obj.parent

            object_items[obj_key] = {
                "object": obj,
                "short_name": obj.name,
                "type": obj.type,
                "parent": build_path(parent) if parent is not None else None,
                "item": None,
            }

            if parent is None:
                root_objects.append(obj_key)
        except Exception:
            continue

    return object_items, root_objects
