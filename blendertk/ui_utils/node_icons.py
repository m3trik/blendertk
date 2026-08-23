# !/usr/bin/python
# coding=utf-8
"""Resolve per-object-type icons for Blender objects (mirror of ``mtk.NodeIcons``).

Blender ships no ``:/``-style Qt resource icons, so the Maya original's
``out_<nodeType>.png`` lookup becomes a small ``Object.type`` → uitk named-icon
map rendered through :class:`uitk.managers.icon_manager.IconManager`.  Same
public surface (``icon_name_for_type`` / ``icon_name_for_node`` / ``get_icon`` /
``get_pixmap``) so the shared panels (sequencer tracks, manifest table,
hierarchy tree) resolve an icon through one call on either DCC.

Usage::

    from blendertk.ui_utils.node_icons import NodeIcons

    icon = NodeIcons.get_icon("Cube")          # QIcon (uitk "cube") or None
    name = NodeIcons.icon_name_for_node("Cube")  # "cube"
"""

from typing import Optional

#: ``Object.type`` → uitk icon name (``uitk/icons/<name>.svg``).  Types without a
#: visually meaningful glyph in the set resolve to ``None`` (caller falls back).
_TYPE_ICONS = {
    "MESH": "cube",
    "CAMERA": "camera",
    "LIGHT": "light",
    "EMPTY": "target",
    "ARMATURE": "branch",
    "CURVE": "edge_loop",
    "SURFACE": "edge_loop",
    "FONT": "font",
    "SPEAKER": "activity",
    "LATTICE": "grid",
    "LIGHT_PROBE": "light",
    "VOLUME": "stack",
    "GPENCIL": "edit",
    "GREASEPENCIL": "edit",
    "POINTCLOUD": "asterisk",
    "CURVES": "edge_loop",
    "META": "circle_add",
}

#: Icon colour — the uitk icon grammar's neutral foreground.
ICON_COLOR = "#888888"


class NodeIcons:
    """Resolve Blender object-type icons as Qt QIcons."""

    @staticmethod
    def icon_name_for_type(obj_type: str) -> Optional[str]:
        """Return the uitk icon name for a Blender ``Object.type`` (or ``None``).

        Parameters:
            obj_type: An ``Object.type`` enum string (e.g. ``"MESH"``).

        Returns:
            Icon name such as ``"cube"``, or ``None`` when no glyph is mapped.
        """
        return _TYPE_ICONS.get((obj_type or "").upper())

    @staticmethod
    def icon_name_for_node(obj_name: str) -> Optional[str]:
        """Return the icon name for the named scene object.

        Parameters:
            obj_name: ``bpy.data.objects`` key.

        Returns:
            Icon name, or ``None`` outside Blender / for an unknown object or
            an unmapped type.
        """
        try:
            import bpy
        except ImportError:
            return None
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return None
        return NodeIcons.icon_name_for_type(getattr(obj, "type", ""))

    @staticmethod
    def get_icon(obj_name: str, size: int = 16):
        """Return a ``QIcon`` for a Blender object, or ``None`` if unavailable.

        Parameters:
            obj_name: ``bpy.data.objects`` key.
            size: Icon size in pixels (square).

        Returns:
            A ``QtGui.QIcon`` from uitk's named-icon set, or ``None``.
        """
        name = NodeIcons.icon_name_for_node(obj_name)
        if name is None:
            return None
        from uitk.managers.icon_manager import IconManager

        icon = IconManager.get(name, size=(size, size), color=ICON_COLOR)
        if icon is None or icon.isNull():
            return None
        return icon

    @staticmethod
    def get_pixmap(obj_name: str, size: int = 16):
        """Return a ``QPixmap`` for a Blender object scaled to *size*, or ``None``."""
        icon = NodeIcons.get_icon(obj_name, size)
        if icon is None:
            return None
        return icon.pixmap(size, size)


__all__ = ["NodeIcons"]
