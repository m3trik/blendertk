# !/usr/bin/python
# coding=utf-8
"""Node / datablock utilities — instancing via shared object data.

Maya "instances" = multiple transforms sharing one shape node. Blender's analogue is a
**linked duplicate**: multiple objects pointing at the same ``obj.data`` datablock. So this
mirrors mayatk's ``node_utils`` instance helpers (``btk.replace_with_instances`` ↔
``mtk.replace_with_instances``, ``get_instances``, ``uninstance``) onto ``obj.data`` sharing.

These operate on ``bpy.data`` object/datablock references (no VIEW_3D context) → **headless-testable**.
``import bpy`` is deferred into the call bodies (no import side effects).
"""
import pythontk as ptk


def _object_users(data):
    """Count scene **objects** referencing ``data``. ``data.users`` also counts fake users and
    references from other datablocks, which would false-positive instance detection."""
    import bpy

    return sum(1 for o in bpy.data.objects if o.data is data)


def get_instances(objects=None):
    """Return objects that share their data with another object (Maya-style instances).

    ``objects=None`` scans the whole scene; otherwise restricts the result to objects sharing
    a datablock with any of the given ``objects``. "Shared" means >1 *object* references the data.
    """
    import bpy
    from collections import Counter

    scene_objs = [o for o in bpy.data.objects if getattr(o, "data", None) is not None]
    counts = Counter(o.data for o in scene_objs)  # object-user counts (one pass)
    if objects is None:
        return [o for o in scene_objs if counts[o.data] > 1]
    datas = {o.data for o in ptk.make_iterable(objects) if getattr(o, "data", None) is not None}
    return [o for o in scene_objs if o.data in datas and counts[o.data] > 1]


def replace_with_instances(
    objects, freeze_transforms=False, center_pivot=False, delete_history=False
):
    """Make ``objects[1:]`` instances of ``objects[0]`` by sharing its data — Blender's linked
    duplicate, the analogue of Maya's shared-shape instancing (mirror of ``mtk.replace_with_instances``).

    Targets adopt the source's datablock (only same-type, data-bearing objects). ``freeze_transforms``
    / ``center_pivot`` pre-clean the objects via the xform helpers; ``delete_history`` is a no-op in
    Blender (no construction history) — kept for signature parity. Returns the instanced targets.
    """
    import blendertk as btk  # public API (already loaded at call time); avoids the bool-param shadow

    objs = [o for o in ptk.make_iterable(objects) if o]
    if len(objs) < 2:
        return []
    source, targets = objs[0], objs[1:]
    # Pre-clean only the SOURCE — its data is what the targets adopt. Freezing/centering a
    # target would mutate (and, for freeze, relocate to the origin) geometry about to be discarded.
    if center_pivot:
        btk.center_pivot([source], mode="object")
    if freeze_transforms:
        btk.freeze_transforms([source])
    instanced = []
    for t in targets:
        if getattr(t, "data", None) is not None and t.type == source.type:
            t.data = source.data
            instanced.append(t)
    return instanced


def uninstance(objects):
    """Break the instance link — make each object's data single-user (mirror of ``mtk.uninstance``).

    Blender: replace a shared datablock with an independent copy. Returns the objects changed.
    """
    result = []
    for o in (x for x in ptk.make_iterable(objects) if getattr(x, "data", None) is not None):
        if _object_users(o.data) > 1:
            o.data = o.data.copy()
            result.append(o)
    return result


def get_parent(obj, all=False):
    """The object's parent — mirror of ``mtk.get_parent``. ``all=True`` returns the full
    ancestor chain (immediate parent first)."""
    if not all:
        return getattr(obj, "parent", None)
    chain = []
    node = getattr(obj, "parent", None)
    while node is not None:
        chain.append(node)
        node = node.parent
    return chain


def get_children(obj, recursive=False):
    """The object's children — mirror of ``mtk.get_children``. ``recursive=True`` returns the
    whole descendant subtree."""
    if recursive:
        return list(getattr(obj, "children_recursive", []))
    return list(getattr(obj, "children", []))


def get_shape(obj):
    """The object's data datablock (mesh/curve/…) — the Blender analogue of Maya's shape node
    under a transform (mirror of ``mtk.get_shape``). Returns ``None`` for data-less objects
    (e.g. Empties)."""
    return getattr(obj, "data", None)


def reparent(objects, parent, keep_transform=True):
    """Parent ``objects`` under ``parent`` (``None`` to unparent) — mirror of ``mtk.reparent``.

    ``keep_transform`` preserves each object's world position (Blender's "Keep Transform").
    Skips parenting an object to itself. Returns the reparented objects.
    """
    import bpy

    out = []
    for o in (x for x in ptk.make_iterable(objects) if x is not None and x is not parent):
        world = o.matrix_world.copy()
        o.parent = parent
        if keep_transform:
            o.matrix_world = world
        out.append(o)
    bpy.context.view_layer.update()
    return out


class NodeUtils:
    """Namespace mirror of mayatk's ``NodeUtils`` (instance helpers also exposed module-level)."""

    get_instances = staticmethod(get_instances)
    replace_with_instances = staticmethod(replace_with_instances)
    uninstance = staticmethod(uninstance)
    get_parent = staticmethod(get_parent)
    get_children = staticmethod(get_children)
    get_shape = staticmethod(get_shape)
    reparent = staticmethod(reparent)
