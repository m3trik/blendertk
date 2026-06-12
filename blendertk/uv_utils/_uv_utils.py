# !/usr/bin/python
# coding=utf-8
"""UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
names where they apply). Operate on the mesh UV layer via bmesh / ``mesh.uv_layers`` → headless.

The unwrap / pack / project / seam operators are ``bpy.ops.uv.*`` and live in the slot (they run
in edit mode); this module holds the data-level UV helpers that need no UV editor.

``import bpy`` / ``bmesh`` are deferred into the call bodies (no import side effects). Shares the
mesh primitives with ``edit_utils`` (the canonical home for mesh-bmesh infra).
"""
from blendertk.core_utils._core_utils import _object_mode
from blendertk.edit_utils._edit_utils import _meshes, _bmesh_edit


def _uv_edit(obj, fn):
    """Run ``fn(bm)`` against ``obj``'s bmesh, **mode-aware** (NOT ``@_object_mode``):
    edit mode updates the live edit-bmesh, object mode round-trips through the mesh."""
    import bmesh

    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        fn(bm)
        bmesh.update_edit_mesh(obj.data)
    else:
        _bmesh_edit(obj, fn)


def _uv_read(obj, fn):
    """Read-only variant of :func:`_uv_edit` — no mesh write-back (a get must not touch the
    mesh) and no edit-bmesh update."""
    import bmesh

    if obj.mode == "EDIT":
        fn(bmesh.from_edit_mesh(obj.data))
    else:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        fn(bm)
        bm.free()


def move_uvs(objects, du=0.0, dv=0.0):
    """Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
    (whole UV map)."""

    def _shift(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                loop[uvl].uv.x += du
                loop[uvl].uv.y += dv

    for o in _meshes(objects):
        _uv_edit(o, _shift)


def _uv_bounds(objects):
    """Combined UV bounding box (min_u, min_v, max_u, max_v) across the meshes, or None.

    Running min/max, not a coordinate list — dense (photogrammetry-scale) meshes would
    otherwise materialize tens of millions of tuples. Read-only: a missing UV layer is
    skipped, never created.
    """
    box = [float("inf"), float("inf"), float("-inf"), float("-inf")]

    def _gather(bm):
        uvl = bm.loops.layers.uv.active
        if uvl is None:
            return
        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uvl].uv
                if uv.x < box[0]:
                    box[0] = uv.x
                if uv.y < box[1]:
                    box[1] = uv.y
                if uv.x > box[2]:
                    box[2] = uv.x
                if uv.y > box[3]:
                    box[3] = uv.y

    for o in _meshes(objects):
        _uv_read(o, _gather)
    return None if box[0] == float("inf") else tuple(box)


def transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0):
    """Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s) about
    the combined UV bounding-box center (one shared pivot so multi-object maps stay aligned)."""
    import math

    box = _uv_bounds(objects)
    if box is None:
        return
    pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def _apply(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uvl].uv
                u, v = uv.x - pu, uv.y - pv
                if flip_u:
                    u = -u
                if flip_v:
                    v = -v
                if angle:
                    u, v = u * cos_a - v * sin_a, u * sin_a + v * cos_a
                uv.x, uv.y = u + pu, v + pv

    for o in _meshes(objects):
        _uv_edit(o, _apply)


def pin_uvs(objects, pin=True, selected_only=True):
    """Pin/unpin UVs (bmesh ``pin_uv``). ``selected_only`` restricts to the UVs of selected
    verts (the 3D-edit-mode workflow — no UV editor needed); object mode pins all."""

    def _pin(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                if selected_only and not loop.vert.select:
                    continue
                loop[uvl].pin_uv = pin

    for o in _meshes(objects):
        _uv_edit(o, _pin)


def _face_areas(obj, bm):
    """(world_area_sum, uv_area_sum) over the bmesh's faces.

    World area via Newell's method, UV area via the shoelace formula — both exact for the
    simple (possibly non-convex) polygons real meshes carry; fan-triangulation would
    overcount non-convex UV faces. Read-only: a missing UV layer yields (0, 0).
    """
    from mathutils import Vector

    uvl = bm.loops.layers.uv.active
    if uvl is None:
        return 0.0, 0.0
    mw = obj.matrix_world
    world_sum = uv_sum = 0.0
    for face in bm.faces:
        pts = [mw @ loop.vert.co for loop in face.loops]
        uvs = [loop[uvl].uv for loop in face.loops]
        normal = Vector((0.0, 0.0, 0.0))
        shoelace = 0.0
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            normal += pts[i].cross(pts[j])
            shoelace += uvs[i].x * uvs[j].y - uvs[j].x * uvs[i].y
        world_sum += normal.length / 2.0
        uv_sum += abs(shoelace) / 2.0
    return world_sum, uv_sum


def get_texel_density(objects, map_size):
    """Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
    mirror of ``mtk.get_texel_density``: ``sqrt(uv_area / world_area) * map_size``.
    Returns 0 when either area is zero."""
    from math import sqrt

    totals = [0.0, 0.0]

    def _accumulate(o):
        def _sum(bm):
            w, u = _face_areas(o, bm)
            totals[0] += w
            totals[1] += u

        return _sum

    for o in _meshes(objects):
        _uv_read(o, _accumulate(o))
    world_sum, uv_sum = totals
    if not world_sum or not uv_sum:
        return 0
    return sqrt(uv_sum / world_sum) * map_size


def set_texel_density(objects, density=1.0, map_size=4096):
    """Scale each object's UVs (about its own UV bbox center) to the target texel density —
    mirror of ``mtk.set_texel_density``. Per-OBJECT, not per-UV-shell (documented divergence:
    Maya scales each shell about its own center; shell detection is deferred until needed)."""
    for o in _meshes(objects):
        current = get_texel_density(o, map_size)
        if not current:
            continue
        scale = density / current
        box = _uv_bounds(o)
        pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

        def _scale(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    uv.x = pu + (uv.x - pu) * scale
                    uv.y = pv + (uv.y - pv) * scale

        _uv_edit(o, _scale)


@_object_mode
def delete_extra_uv_sets(objects):
    """Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets"."""
    for o in _meshes(objects):
        while len(o.data.uv_layers) > 1:
            o.data.uv_layers.remove(o.data.uv_layers[-1])


class UvUtils:
    """Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level)."""

    move_uvs = staticmethod(move_uvs)
    delete_extra_uv_sets = staticmethod(delete_extra_uv_sets)
    transform_uvs = staticmethod(transform_uvs)
    pin_uvs = staticmethod(pin_uvs)
    get_texel_density = staticmethod(get_texel_density)
    set_texel_density = staticmethod(set_texel_density)
