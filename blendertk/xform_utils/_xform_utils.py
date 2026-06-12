# !/usr/bin/python
# coding=utf-8
"""Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
match-scale, move-to).

These operate on object transforms via ``bpy.data`` / object operators (no VIEW_3D context),
so unlike component selection they are **headless-testable**. Mirrors mayatk's ``xform_utils``
public names (``btk.freeze_transforms`` ↔ ``mtk.freeze_transforms``, etc.).

``import bpy`` / ``mathutils`` are deferred into call bodies (no import side effects).
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode  # shared OBJECT-mode guard


def get_world_bbox(obj):
    """Return ``(min, max)`` ``Vector``s of ``obj``'s bounding box in world space."""
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    mx = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    return mn, mx


def _combined_bbox(objects):
    from mathutils import Vector

    boxes = [get_world_bbox(o) for o in objects]
    mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
    mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
    return mn, mx


def _pivot_point(objects, pivot):
    from mathutils import Vector

    if pivot == "object":
        locs = [o.matrix_world.translation for o in objects]
        return sum(locs, Vector((0.0, 0.0, 0.0))) / len(locs)
    mn, mx = _combined_bbox(objects)
    return (mn + mx) / 2.0  # bounding-box center


@_object_mode
def freeze_transforms(objects, location=True, rotation=False, scale=True):
    """Apply (bake) the given transform channels into the object data — Blender's
    ``transform_apply`` (mirror of ``mtk.freeze_transforms``)."""
    import bpy

    if not (location or rotation or scale):
        return  # nothing to bake (transform_apply with all channels off is a no-op/error)
    objects = [o for o in ptk.make_iterable(objects) if o]
    if not objects:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)


@_object_mode
def drop_to_grid(objects, align="Min", origin=False, center_pivot=False):
    """Drop each object so its bbox ``Min`` / ``Mid`` / ``Max`` sits on the ground (Z=0).

    ``origin``: first move the object to the world origin. ``center_pivot``: re-center the
    object origin on its geometry bbox afterwards.
    """
    import bpy

    for obj in (o for o in ptk.make_iterable(objects) if o):
        if origin:
            obj.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        mn, mx = get_world_bbox(obj)
        z = {"Min": mn.z, "Max": mx.z}.get(align, (mn.z + mx.z) / 2.0)
        obj.location.z -= z
        bpy.context.view_layer.update()  # refresh matrix_world for downstream reads
        if center_pivot:  # bool param; reach the helper via the class (name is shadowed here)
            XformUtils.center_pivot(obj, mode="object")


_ORIGIN_MODES = {
    "object": ("ORIGIN_GEOMETRY", "BOUNDS"),  # bbox center
    "median": ("ORIGIN_GEOMETRY", "MEDIAN"),  # geometry median
    "component": ("ORIGIN_GEOMETRY", "MEDIAN"),  # Maya "component" ~= median
}


@_object_mode
def center_pivot(objects, mode="object"):
    """Move each object's origin (Blender's single pivot) — mirror of Maya's Center Pivot.

    ``mode``: ``"object"`` bounding-box center, ``"median"`` / ``"component"`` geometry
    median, ``"world"`` the world origin (0,0,0). Headless-testable (object operator).
    """
    import bpy

    objects = [o for o in ptk.make_iterable(objects) if o]
    if not objects:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if mode == "world":
        cursor = bpy.context.scene.cursor.location.copy()
        bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
        try:
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        finally:
            bpy.context.scene.cursor.location = cursor
    else:
        otype, center = _ORIGIN_MODES.get(mode, _ORIGIN_MODES["object"])
        bpy.ops.object.origin_set(type=otype, center=center)
    bpy.context.view_layer.update()  # refresh matrix_world for downstream reads


def get_pivot_modes():
    """Center-pivot mode keys understood by :func:`center_pivot`."""
    return ["object", "median", "world"]


def match_scale(source, target, average=True):
    """Uniformly rescale ``source`` object(s) to match ``target``'s bounding-box size."""
    targets = [o for o in ptk.make_iterable(target) if o]
    if not targets:
        return
    t_mn, t_mx = _combined_bbox(targets)
    t_size = t_mx - t_mn
    for src in (o for o in ptk.make_iterable(source) if o):
        s_mn, s_mx = get_world_bbox(src)
        s_size = s_mx - s_mn
        ratios = [t_size[i] / s_size[i] for i in range(3) if s_size[i] > 1e-9]
        if not ratios:
            continue
        factor = (sum(ratios) / len(ratios)) if average else max(ratios)
        src.scale = [v * factor for v in src.scale]


def move_to(source, target, pivot="center"):
    """Move ``source`` object(s) so their pivot aligns with the ``target``'s pivot point."""
    import bpy

    targets = [o for o in ptk.make_iterable(target) if o]
    if not targets:
        return
    dst = _pivot_point(targets, pivot)
    for src in (o for o in ptk.make_iterable(source) if o):
        cur = _pivot_point([src], pivot)
        src.location = src.location + (dst - cur)
        bpy.context.view_layer.update()


class XformUtils:
    """Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level)."""

    get_world_bbox = staticmethod(get_world_bbox)
    freeze_transforms = staticmethod(freeze_transforms)
    drop_to_grid = staticmethod(drop_to_grid)
    center_pivot = staticmethod(center_pivot)
    get_pivot_modes = staticmethod(get_pivot_modes)
    match_scale = staticmethod(match_scale)
    move_to = staticmethod(move_to)

    @staticmethod
    def get_pivot_options():
        """Pivot keys understood by :func:`move_to` (mirror of ``mtk.XformUtils.get_pivot_options``)."""
        return ["center", "object"]
