# !/usr/bin/python
# coding=utf-8
"""Core blendertk utilities — DCC-environment info + cross-cutting decorators.

Mirrors the mayatk ``CoreUtils`` public surface (``btk.undoable`` ↔ ``mtk.undoable``,
``btk.get_env_info`` ↔ ``mtk.get_env_info``) so the shared tentacle slots stay branch-free.

``import bpy`` is deferred into the call bodies so importing this module (and resolving
the package surface) never requires a running Blender — matching the ecosystem's
no-import-side-effects rule.
"""
import os
from functools import wraps

import pythontk as ptk


def undoable(fn):
    """Wrap ``fn`` so its changes collapse into a single Blender undo step.

    Blender has no explicit open/close undo chunk like Maya; an operator — or an explicit
    ``bpy.ops.ed.undo_push`` — marks a restore point. This pushes one step after ``fn``
    runs, tagged with the function name.

    NOTE: undo granularity for raw ``bpy.data`` / ``bmesh`` edits (vs. operator calls) is
    finicky; revisit the exact push placement per-slot once real edits are wired (the
    cross-cutting name + contract is what matters at scaffold time).
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        try:
            import bpy

            bpy.ops.ed.undo_push(message=getattr(fn, "__name__", "blendertk op"))
        except Exception:
            pass
        return result

    return wrapper


def _object_mode(fn):
    """Run ``fn`` in OBJECT mode, restoring the caller's prior mode afterward.

    Blender's object operators (``transform_apply``, ``origin_set``, ``modifier_apply``) require
    OBJECT mode and raise from a component/edit context. This guard makes the helpers that wrap
    them safe to call from anywhere. Shared by ``xform_utils`` and ``edit_utils``.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        import bpy

        view_layer = bpy.context.view_layer
        active = view_layer.objects.active
        prior = getattr(active, "mode", "OBJECT")
        if prior != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            return fn(*args, **kwargs)
        finally:
            if prior != "OBJECT" and active is not None:
                # fn may have re-activated one of its targets (the helpers select what they
                # operate on); mode_set acts on the ACTIVE object, so restore the caller's
                # active first or the wrong object ends up in edit mode.
                try:
                    view_layer.objects.active = active
                    bpy.ops.object.mode_set(mode=prior)
                except (RuntimeError, ReferenceError):
                    pass  # active was deleted by fn, or the mode no longer applies

    return wrapper


def get_env_info(key=None):
    """Return Blender scene / environment info (mirror of ``mtk.get_env_info``).

    With ``key`` returns that single value, else the whole dict. camelCase keys to match
    the ecosystem's cross-DCC info convention (also what the Unity bridge expects).
    """
    import bpy

    scene = bpy.context.scene
    filepath = bpy.data.filepath
    workspace = os.path.dirname(filepath) if filepath else ""
    info = {
        "sceneName": filepath or "untitled",
        "blenderVersion": bpy.app.version_string,
        "fps": scene.render.fps,
        "currentFrame": scene.frame_current,
        "frameRange": (scene.frame_start, scene.frame_end),
        "unitSystem": scene.unit_settings.system,
        "selectionCount": len(getattr(bpy.context, "selected_objects", []) or []),
        # Blender's analogue of Maya's project workspace = the saved .blend file's directory.
        "workspace": workspace,
        "workspace_dir": os.path.basename(workspace) if workspace else "",
    }
    return info.get(key) if key is not None else info


class CoreUtils(ptk.CoreUtils):
    """Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors
    ``mayatk.CoreUtils(ptk.CoreUtils, ...)``), inheriting the shared helpers and adding the
    Blender-specific ones rather than duplicating logic (SSoT).

    The Blender helpers are also exposed module-level (``btk.undoable`` / ``btk.get_env_info``)
    so slots can call either form, matching mayatk.
    """

    undoable = staticmethod(undoable)
    get_env_info = staticmethod(get_env_info)
