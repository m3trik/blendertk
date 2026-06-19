# !/usr/bin/python
# coding=utf-8
"""Display utilities — the exploded-view toggle (mirror of mayatk's
``display_utils.ExplodedView`` workflow: push objects apart for inspection, restore their
exact positions afterwards).

Maya's version runs an iterative repulsive-force arrangement; here the same outcome (no
world-bbox overlaps, restorable) comes from scaling the objects' positions away from the
group's bbox center until nothing overlaps — deterministic and headless-testable. The
pre-explode location is stamped as a custom prop (like the freeze bakes), so the toggle
survives a save/reload.

``import bpy`` / ``mathutils`` are deferred into the call bodies (no import side effects).
"""
import pythontk as ptk

from blendertk.xform_utils._xform_utils import get_world_bbox

_ORIG_LOCATION = "btk_explode_orig"  # custom-prop key holding the pre-explode location


def _bboxes_overlap(a, b, margin=0.0):
    (amn, amx), (bmn, bmx) = a, b
    return all(amn[i] - margin <= bmx[i] and bmn[i] - margin <= amx[i] for i in range(3))


def is_exploded(objects):
    """True when any of the given objects carries an exploded-view origin stamp."""
    return any(_ORIG_LOCATION in o for o in ptk.make_iterable(objects) if o)


def explode_view(objects, step=1.2, margin=0.05, max_iterations=50):
    """Push the given objects apart for inspection — each moves away from the group's bbox
    center along its own bbox-center offset (grown ×``step`` per pass) until no world
    bounding boxes overlap (or ``max_iterations``). Separation is driven by the **geometry**
    centers, not the origins, so frozen objects (origins all at world zero) explode too.
    Each object's location is stamped so :func:`unexplode_view` restores it exactly;
    already-stamped objects are left alone (re-explode safe). Returns the objects moved.
    """
    import bpy
    from mathutils import Vector

    objects = [
        o for o in ptk.make_iterable(objects) if o and getattr(o, "type", None) == "MESH"
    ]
    if len(objects) < 2:
        return []
    targets = [o for o in objects if _ORIG_LOCATION not in o]
    if not targets:
        return []

    boxes = [get_world_bbox(o) for o in objects]
    mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
    mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
    center = (mn + mx) / 2.0

    for o in targets:
        o[_ORIG_LOCATION] = list(o.location)

    for _ in range(max_iterations):
        bpy.context.view_layer.update()
        boxes = [get_world_bbox(o) for o in objects]
        if not any(
            _bboxes_overlap(boxes[i], boxes[j], margin)
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ):
            break
        for n, o in enumerate(targets):
            o_mn, o_mx = get_world_bbox(o)
            d = (o_mn + o_mx) / 2.0 - center
            if d.length < 1e-6:  # exactly centered geometry — nudge deterministically
                d = Vector((1e-2 * (n + 1), 0.0, 0.0))
            o.location += d * (step - 1.0)
    bpy.context.view_layer.update()
    return targets


def unexplode_view(objects):
    """Restore the exact pre-explode locations stamped by :func:`explode_view`.
    Returns the objects restored."""
    import bpy
    from mathutils import Vector

    restored = []
    for o in (o for o in ptk.make_iterable(objects) if o):
        if _ORIG_LOCATION in o:
            o.location = Vector(o[_ORIG_LOCATION])
            del o[_ORIG_LOCATION]
            restored.append(o)
    if restored:
        bpy.context.view_layer.update()
    return restored


def unexplode_all():
    """Restore every exploded object in the scene, regardless of selection (mirror of mayatk's
    ``ExplodedView.un_explode_all``). Returns the objects restored."""
    import bpy

    return unexplode_view(list(bpy.data.objects))


class DisplayUtils:
    """Namespace mirror of mayatk's ``DisplayUtils`` (helpers also exposed module-level)."""

    explode_view = staticmethod(explode_view)
    unexplode_view = staticmethod(unexplode_view)
    unexplode_all = staticmethod(unexplode_all)
    is_exploded = staticmethod(is_exploded)
