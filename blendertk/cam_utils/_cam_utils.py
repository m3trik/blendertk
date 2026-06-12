# !/usr/bin/python
# coding=utf-8
"""Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).

Operates on camera object ``.data`` (no VIEW_3D context) → **headless-testable**. ``import bpy``
/ ``mathutils`` are deferred into the call bodies (no import side effects).
"""
import pythontk as ptk

# Blender camera clip defaults.
_NEAR_DEFAULT = 0.1
_FAR_DEFAULT = 1000.0


def _resolve_cameras(camera):
    """Coerce ``camera`` (object / list / None) to a list of camera *objects*. ``None`` ->
    the scene's active (render) camera — Blender's closest analogue to the viewport camera."""
    import bpy

    if not camera:
        cam = bpy.context.scene.camera
        return [cam] if cam else []
    return [o for o in ptk.make_iterable(camera) if getattr(o, "type", None) == "CAMERA"]


def _scene_bbox_corners():
    """The 8 world-space corners of the combined bbox of visible mesh objects (or all meshes)."""
    import bpy
    from mathutils import Vector
    import blendertk as btk

    geo = [o for o in bpy.data.objects if o.type == "MESH" and o.visible_get()]
    if not geo:
        geo = [o for o in bpy.data.objects if o.type == "MESH"]
    if not geo:
        return None
    boxes = [btk.get_world_bbox(o) for o in geo]
    mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
    mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
    return [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]


def _resolve_clip(value, max_dist, *, near):
    """Resolve a near/far clip directive to a float (or ``None`` to leave unchanged).

    ``'reset'`` -> Blender default; ``'auto'`` -> from ``max_dist`` (far = dist*1.2, near =
    far/3000 floored at 0.1, matching mtk's Z-precision ratio); a number -> itself.
    """
    if value == "reset":
        return _NEAR_DEFAULT if near else _FAR_DEFAULT
    if value == "auto":
        if max_dist <= 0:
            return _NEAR_DEFAULT if near else _FAR_DEFAULT
        far = max_dist * 1.2
        return max(far / 3000.0, _NEAR_DEFAULT) if near else far
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def adjust_camera_clipping(camera=None, near_clip=None, far_clip=None):
    """Adjust near/far clip planes of camera object(s) — mirror of ``mtk.adjust_camera_clipping``.

    ``camera=None`` targets the scene's active camera. ``near_clip``/``far_clip``: ``None``
    leaves unchanged, ``'auto'`` derives from the scene bbox vs. the camera position, ``'reset'``
    restores Blender defaults (0.1 / 1000.0), or pass a float.
    """
    cams = _resolve_cameras(camera)
    if not cams:
        return
    needs_auto = near_clip == "auto" or far_clip == "auto"
    corners = _scene_bbox_corners() if needs_auto else None
    for cam in cams:
        data = cam.data
        max_dist = 0.0
        if needs_auto and corners:
            cam_pos = cam.matrix_world.translation
            max_dist = max((c - cam_pos).length for c in corners)
        if near_clip is not None:
            val = _resolve_clip(near_clip, max_dist, near=True)
            if val is not None:
                data.clip_start = val
        if far_clip is not None:
            val = _resolve_clip(far_clip, max_dist, near=False)
            if val is not None:
                data.clip_end = val


class CamUtils:
    """Namespace mirror of mayatk's ``CamUtils`` (helper also exposed module-level)."""

    adjust_camera_clipping = staticmethod(adjust_camera_clipping)
