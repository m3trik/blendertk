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


def open_editor(editor, properties_context=None):
    """Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
    in a new window. Returns the new window, or None when it could not be opened.

    Preferences routes through ``screen.userpref_show`` (Blender's own dedicated path);
    everything else duplicates the current window and switches its area's ``ui_type``.
    ``properties_context`` (only when opening the Properties editor — e.g. ``"VIEW_LAYER"``,
    ``"OBJECT"``, ``"RENDER"``) selects which Properties tab is shown.
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
    if properties_context and ui_type == "PROPERTIES":
        try:
            area.spaces.active.context = properties_context
        except (TypeError, AttributeError):
            pass  # tab not available in this Blender version
    return window


def menu_exists(menu_idname):
    """True if ``menu_idname`` (e.g. ``"VIEW3D_MT_add"``) is a registered Blender menu.

    Cheap, runtime-only validity check backing the no-dead-links guard on the both-button menu —
    the analogue of validating an editor name against :func:`get_editor_types`.
    """
    import bpy

    return hasattr(bpy.types, menu_idname)


def call_native_menu(menu_idname):
    """Pop Blender's own native menu ``menu_idname`` (e.g. ``"VIEW3D_MT_add"``) at the cursor.

    The Blender-idiomatic analogue of Maya's Qt-menu *wrapping*: rather than rebuild the menu in Qt
    (Blender draws its UI in OpenGL — there are no ``QMenu``/``QAction`` objects to harvest), invoke
    Blender's **real** menu via ``bpy.ops.wm.call_menu`` under a VIEW_3D override. Always accurate +
    add-on/mode-aware, zero content maintenance. **GUI-only** (``--background`` has no window to pop
    into). Returns the operator result set, or ``None`` for an unknown menu / no 3D viewport.
    """
    import bpy
    import blendertk as btk

    # GUI-only: there is no window to pop a menu into headless, and ``wm.call_menu`` faults
    # natively under ``--background`` (EXCEPTION_ACCESS_VIOLATION) — guard before touching it.
    if bpy.app.background:
        return None
    if not hasattr(bpy.types, menu_idname):
        return None
    ctx = btk.get_view3d_context()
    if not ctx or not ctx.get("region"):
        return None
    with bpy.context.temp_override(**ctx):
        return bpy.ops.wm.call_menu("INVOKE_DEFAULT", name=menu_idname)


class UiUtils:
    """Namespace mirror (helpers also exposed module-level)."""

    get_editor_types = staticmethod(get_editor_types)
    open_editor = staticmethod(open_editor)
    menu_exists = staticmethod(menu_exists)
    call_native_menu = staticmethod(call_native_menu)
