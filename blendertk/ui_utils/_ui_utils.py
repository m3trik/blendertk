# !/usr/bin/python
# coding=utf-8
"""UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).

Blender has no floating editor windows per se: an editor is an *area* ``ui_type`` inside a
window. ``open_editor`` opens a new OS window (``wm.window_new``) and switches its area to the
requested editor — the closest Blender-idiomatic match to "open the X editor". **GUI-only**
(``--background`` has no windows), so these are exercised by the GUI harnesses, not headless.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

# Friendly editor name -> Area.ui_type enum (Blender 4.x/5.x).
EDITOR_TYPES = {
    "3D Viewport": "VIEW_3D",
    "Image Editor": "IMAGE_EDITOR",
    "UV Editor": "UV",
    "Shader Editor": "ShaderNodeTree",
    "Compositor": "CompositorNodeTree",
    "Geometry Nodes": "GeometryNodeTree",
    "Video Sequencer": "SEQUENCE_EDITOR",
    "Movie Clip Editor": "CLIP_EDITOR",
    "Dope Sheet": "DOPESHEET",
    "Timeline": "TIMELINE",
    "Graph Editor": "FCURVES",
    "Drivers": "DRIVERS",
    "NLA Editor": "NLA_EDITOR",
    "Text Editor": "TEXT_EDITOR",
    "Python Console": "CONSOLE",
    "Info Log": "INFO",
    "Outliner": "OUTLINER",
    "Properties": "PROPERTIES",
    "File Browser": "FILES",
    "Asset Browser": "ASSETS",
    "Spreadsheet": "SPREADSHEET",
    "Preferences": "PREFERENCES",
}


def get_editor_types():
    """The friendly-name → ``Area.ui_type`` map understood by :func:`open_editor`."""
    return dict(EDITOR_TYPES)


def open_editor(editor):
    """Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
    in a new window. Returns the new window, or None when it could not be opened.

    Preferences routes through ``screen.userpref_show`` (Blender's own dedicated path);
    everything else duplicates the current window and switches its area's ``ui_type``.
    """
    import bpy

    ui_type = EDITOR_TYPES.get(editor, editor)
    if ui_type == "PREFERENCES":
        bpy.ops.screen.userpref_show()
        return bpy.context.window_manager.windows[-1]
    bpy.ops.wm.window_new()
    window = bpy.context.window_manager.windows[-1]
    area = window.screen.areas[0]
    try:
        area.ui_type = ui_type
    except TypeError:  # unknown enum for this Blender version — leave the duplicate window
        return None
    return window


class UiUtils:
    """Namespace mirror (helpers also exposed module-level)."""

    get_editor_types = staticmethod(get_editor_types)
    open_editor = staticmethod(open_editor)
