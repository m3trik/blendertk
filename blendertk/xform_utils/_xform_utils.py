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

from blendertk.core_utils._core_utils import _object_mode, selected_objects  # shared OBJECT-mode guard + window-independent selection read


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


# Custom-prop keys freeze_transforms stamps so restore_transforms can un-freeze
# (the Blender analogue of mayatk's ``original_{T,R,S}_bake`` attributes).
_BAKE_T, _BAKE_R, _BAKE_S = "btk_T_bake", "btk_R_bake", "btk_S_bake"


def _store_bakes(obj, location, rotation, scale):
    """Record the pre-freeze local channels, composing with any existing bake (the
    cumulative freeze/unfreeze contract: repeated freeze+transform cycles compose,
    one restore returns the full history — T adds, R quaternion-composes, S multiplies)."""
    from mathutils import Quaternion, Vector

    loc, rot, scl = obj.matrix_basis.decompose()
    if location:
        prior = Vector(obj.get(_BAKE_T, (0.0, 0.0, 0.0)))
        obj[_BAKE_T] = list(prior + loc)
    if rotation:
        prior = Quaternion(obj.get(_BAKE_R, (1.0, 0.0, 0.0, 0.0)))
        obj[_BAKE_R] = list(prior @ rot)
    if scale:
        prior = Vector(obj.get(_BAKE_S, (1.0, 1.0, 1.0)))
        obj[_BAKE_S] = [prior[i] * scl[i] for i in range(3)]


@_object_mode
def freeze_transforms(objects, location=True, rotation=False, scale=True, store=True):
    """Apply (bake) the given transform channels into the object data — Blender's
    ``transform_apply`` (mirror of ``mtk.freeze_transforms``). ``store`` stamps the
    pre-freeze channels as custom props so :func:`restore_transforms` can un-freeze."""
    import bpy

    if not (location or rotation or scale):
        return  # nothing to bake (transform_apply with all channels off is a no-op/error)
    objects = [o for o in ptk.make_iterable(objects) if o]
    if not objects:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        if store:
            _store_bakes(o, location, rotation, scale)
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)


@_object_mode
def restore_transforms(objects, delete_attrs=True):
    """Un-freeze: compose the stored pre-freeze channels back into the local transform
    (``new local C = stored C ∘ current C``) and counter-shift the geometry so the world
    position is preserved — mirror of ``mtk.restore_transforms``. Bakes are stamped by
    :func:`freeze_transforms`. Returns the objects restored."""
    import bpy
    from mathutils import Matrix, Quaternion, Vector

    restored = []
    for obj in (o for o in ptk.make_iterable(objects) if o):
        has_t, has_r, has_s = (k in obj for k in (_BAKE_T, _BAKE_R, _BAKE_S))
        if not (has_t or has_r or has_s):
            continue
        old_basis = obj.matrix_basis.copy()
        loc, rot, scl = old_basis.decompose()
        if has_t:
            loc = Vector(obj[_BAKE_T]) + loc
        if has_r:
            rot = Quaternion(obj[_BAKE_R]) @ rot
        if has_s:
            scl = Vector([obj[_BAKE_S][i] * scl[i] for i in range(3)])
        new_basis = Matrix.LocRotScale(loc, rot, scl)
        if obj.data is not None and hasattr(obj.data, "transform"):
            obj.data.transform(new_basis.inverted() @ old_basis)
            obj.data.update()
        obj.matrix_basis = new_basis
        if delete_attrs:
            for k in (_BAKE_T, _BAKE_R, _BAKE_S):
                if k in obj:
                    del obj[k]
        restored.append(obj)
    bpy.context.view_layer.update()
    return restored


def has_stored_transforms(objects):
    """Map each object → whether it carries pre-freeze bake data (mirror of
    ``mtk.XformUtils.has_stored_transforms``). Bakes are stamped by :func:`freeze_transforms`;
    the Channels panel uses this to gate its Unfreeze action."""
    return {
        o: any(k in o for k in (_BAKE_T, _BAKE_R, _BAKE_S))
        for o in ptk.make_iterable(objects)
        if o
    }


def _connected_edge_sets(edges):
    """Group ``edges`` into connected sets (union-find over shared verts)."""
    parent = {e: e for e in edges}

    def find(x):
        while parent[x] is not x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    vert_owner = {}
    for e in edges:
        for v in e.verts:
            if v in vert_owner:
                parent[find(e)] = find(vert_owner[v])
            else:
                vert_owner[v] = e
    groups = {}
    for e in edges:
        groups.setdefault(find(e), []).append(e)
    return list(groups.values())


def scale_connected_edges(objects, scale_factor=1.1):
    """Scale each CONNECTED set of selected edges about that set's own centroid — mirror
    of ``mtk.scale_connected_edges``. Edit-mode workflow (mode-aware, like
    ``crease_edges``): acts on the selected edges of each mesh in EDIT mode. A tuple
    factor scales per-axis in **local** space (documented divergence: Maya uses world
    axes; a uniform factor is exact in both). Returns the number of edge sets scaled."""
    import bmesh

    if isinstance(scale_factor, (tuple, list)):
        factors = tuple(scale_factor)
    else:
        factors = (scale_factor,) * 3

    from mathutils import Vector

    scaled = 0
    for o in (o for o in ptk.make_iterable(objects) if getattr(o, "type", None) == "MESH"):
        if o.mode != "EDIT":
            continue
        bm = bmesh.from_edit_mesh(o.data)
        selected = [e for e in bm.edges if e.select]
        if not selected:
            continue
        for edge_set in _connected_edge_sets(selected):
            verts = {v for e in edge_set for v in e.verts}
            center = sum((v.co for v in verts), Vector()) / len(verts)
            for v in verts:
                d = v.co - center
                v.co = center + Vector(
                    (d.x * factors[0], d.y * factors[1], d.z * factors[2])
                )
            scaled += 1
        bmesh.update_edit_mesh(o.data)
    return scaled


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
    ``@_object_mode``: ``select_all``/``origin_set`` need OBJECT mode and a window in
    context — its sibling ``transfer_pivot`` was guarded; this one wasn't.
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
    # Moving the origin invalidates any stored freeze-*location* bake (Blender's origin is the
    # translate reference), so drop it — otherwise a later restore_transforms would double-apply
    # the translation. The rotation/scale bakes are unaffected (origin_set leaves them).
    for o in objects:
        if _BAKE_T in o:
            del o[_BAKE_T]
    bpy.context.view_layer.update()  # refresh matrix_world for downstream reads


@_object_mode
def transfer_pivot(
    objects,
    translate=True,
    rotate=False,
    scale=False,
    world_space=True,
    select_targets_after_transfer=False,
):
    """Transfer the object **origin** from the first object to the rest — mirror of Maya's
    ``transfer_pivot`` (``source = objects[0]``, targets = the remainder).

    Blender has a single object origin (a point), so only Maya's **translate** pivot maps:
    each target's origin moves onto the source's origin *without moving its geometry* (3D-cursor
    → ``ORIGIN_CURSOR``). The ``rotate`` / ``scale`` flags are accepted for signature parity but
    no-op — Blender has no separate rotate/scale pivot. ``world_space`` is implicit (the origin is
    read in world space). Returns the target objects (selected afterward when requested).
    """
    import bpy

    objects = [o for o in ptk.make_iterable(objects) if o]
    if len(objects) < 2:
        return []
    source, targets = objects[0], objects[1:]

    scene = bpy.context.scene
    saved_cursor = tuple(scene.cursor.location)
    saved_active = bpy.context.view_layer.objects.active
    saved_sel = list(selected_objects())  # view-layer read: bpy.context.selected_objects is empty from the Qt-pump context
    try:
        if translate:
            scene.cursor.location = source.matrix_world.translation
            for t in targets:
                bpy.ops.object.select_all(action="DESELECT")
                t.select_set(True)
                bpy.context.view_layer.objects.active = t
                bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    finally:
        scene.cursor.location = saved_cursor
        bpy.ops.object.select_all(action="DESELECT")
        restore = targets if select_targets_after_transfer else saved_sel
        for o in restore:
            try:
                o.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        bpy.context.view_layer.objects.active = (
            targets[0] if select_targets_after_transfer else saved_active
        )
        bpy.context.view_layer.update()
    return targets


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


def get_bounding_box(objects, value="", world_space=True):
    """Combined bounding box of ``objects`` — mirror of ``mtk.get_bounding_box`` (name + behavior).

    ``value`` selects a single property; empty returns the whole dict. Keys:
    ``xmin xmax ymin ymax zmin zmax sizex sizey sizez size volume center min max``.
    ``world_space=False`` measures in each object's local space (no ``matrix_world``).
    """
    from mathutils import Vector

    objs = [o for o in ptk.make_iterable(objects) if o]
    if not objs:
        return None
    if world_space:
        mn, mx = _combined_bbox(objs)
    else:
        corners = [c for o in objs for c in o.bound_box]  # all objects' local corners pooled
        mn = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
        mx = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    size = mx - mn
    bbox = {
        "xmin": mn.x, "xmax": mx.x, "ymin": mn.y, "ymax": mx.y, "zmin": mn.z, "zmax": mx.z,
        "sizex": size.x, "sizey": size.y, "sizez": size.z,
        "size": tuple(size), "volume": size.x * size.y * size.z,
        "center": tuple((mn + mx) / 2.0), "min": tuple(mn), "max": tuple(mx),
    }
    return bbox.get(value) if value else bbox


def get_center_point(objects):
    """Bounding-box center of ``objects`` as an ``(x, y, z)`` tuple (mirror of
    ``mtk.get_center_point``)."""
    return get_bounding_box(objects, "center")


def get_operation_axis_matrix(obj, pivot):
    """World pivot frame (orientation + position, scale stripped) for a per-object linear/
    radial operation — mirror of ``mtk.XformUtils.get_operation_axis_matrix``. Shared by the
    duplicate-array tools (``duplicate_linear`` et al.) that orbit/translate each copy about a
    chosen pivot rather than the object's own origin.

    ``pivot``:
      - ``"object"`` — the object's own orientation + its origin.
      - ``"world"`` — world axes at the world origin.
      - ``"manip"`` — Blender's analogue of Maya's *manip* pivot (a manipulator position the
        user can freely relocate mid-operation): the 3D cursor
        (``bpy.context.scene.cursor``), itself freely positionable **and** orientable.
      - ``"center"`` / ``"xmin"`` / ``"xmax"`` / ``"ymin"`` / ``"ymax"`` / ``"zmin"`` /
        ``"zmax"`` — that world bounding-box location (axis-aligned, no rotation) — same
        convention as ``edit_utils._plane_frame``.
      - an explicit ``(x, y, z)`` world point (position only, no rotation).
      - ``"baked"`` (Maya's rotate-pivot value baked distinct from the transform's own
        origin) has no Blender analogue — an object carries a single origin — so, like any
        unrecognized key, it falls back to the bounding-box center.

    Returns a 4x4 ``Matrix``.
    """
    from mathutils import Matrix, Vector

    if pivot == "object":
        return obj.matrix_world.normalized()
    if pivot == "manip":
        import bpy

        return bpy.context.scene.cursor.matrix.copy()
    if pivot == "world":
        return Matrix.Identity(4)
    if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
        return Matrix.Translation(Vector(pivot))

    mn, mx = get_world_bbox(obj)
    center = (mn + mx) / 2.0
    if (
        isinstance(pivot, str)
        and len(pivot) == 4
        and pivot[0] in "xyz"
        and pivot[1:] in ("min", "max")
    ):
        idx = {"x": 0, "y": 1, "z": 2}[pivot[0]]
        pos = center.copy()
        pos[idx] = (mn if pivot.endswith("min") else mx)[idx]
        return Matrix.Translation(pos)
    return Matrix.Translation(center)  # "center", "baked", or any unrecognized key


def _as_point(value):
    """Coerce an object / Vector / 3-sequence to a world-space ``Vector`` position."""
    from mathutils import Vector

    if hasattr(value, "matrix_world"):
        return value.matrix_world.translation.copy()
    return Vector(tuple(value))


def get_distance(a, b):
    """Distance between two points — each an object (world origin), ``Vector``, or 3-sequence
    (mirror of ``mtk.get_distance``)."""
    return (_as_point(a) - _as_point(b)).length


def order_by_distance(objects, reference_point=None, reverse=False):
    """Order ``objects`` by distance from ``reference_point`` (an object / Vector / 3-seq;
    default world origin) — mirror of ``mtk.order_by_distance``. Returns the ordered list."""
    from mathutils import Vector

    ref = _as_point(reference_point) if reference_point is not None else Vector((0.0, 0.0, 0.0))
    objs = [o for o in ptk.make_iterable(objects) if o]
    return sorted(
        objs, key=lambda o: (o.matrix_world.translation - ref).length, reverse=reverse
    )


# aim/up world-vector -> Blender track-axis tokens for ``Vector.to_track_quat``.
_TRACK_AXIS = {
    (1, 0, 0): "X", (-1, 0, 0): "-X", (0, 1, 0): "Y", (0, -1, 0): "-Y",
    (0, 0, 1): "Z", (0, 0, -1): "-Z",
}


def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
    """Aim ``objects`` at a world-space point — mirror of ``mtk.aim_object_at_point`` (which uses
    an aimConstraint). ``aim_vect`` is the object axis pointed at the target; ``up_vect`` the axis
    kept upright. Location and scale are preserved. Returns the aimed objects."""
    from mathutils import Matrix

    target = _as_point(target_pos)
    track = _TRACK_AXIS.get(tuple(aim_vect), "X")
    up = _TRACK_AXIS.get(tuple(up_vect), "Y").lstrip("-")  # up token is unsigned
    aimed = []
    for o in (x for x in ptk.make_iterable(objects) if x):
        loc, _rot, scl = o.matrix_world.decompose()
        direction = (target - loc)
        if direction.length < 1e-9:
            continue
        quat = direction.normalized().to_track_quat(track, up)
        o.matrix_world = Matrix.LocRotScale(loc, quat, scl)
        aimed.append(o)
    return aimed


class XformUtils:
    """Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level)."""

    get_world_bbox = staticmethod(get_world_bbox)
    get_bounding_box = staticmethod(get_bounding_box)
    get_center_point = staticmethod(get_center_point)
    get_operation_axis_matrix = staticmethod(get_operation_axis_matrix)
    get_distance = staticmethod(get_distance)
    order_by_distance = staticmethod(order_by_distance)
    aim_object_at_point = staticmethod(aim_object_at_point)
    freeze_transforms = staticmethod(freeze_transforms)
    restore_transforms = staticmethod(restore_transforms)
    scale_connected_edges = staticmethod(scale_connected_edges)
    drop_to_grid = staticmethod(drop_to_grid)
    center_pivot = staticmethod(center_pivot)
    transfer_pivot = staticmethod(transfer_pivot)
    get_pivot_modes = staticmethod(get_pivot_modes)
    match_scale = staticmethod(match_scale)
    move_to = staticmethod(move_to)

    @staticmethod
    def get_pivot_options():
        """Pivot keys understood by :func:`move_to` (mirror of ``mtk.XformUtils.get_pivot_options``)."""
        return ["center", "object"]
