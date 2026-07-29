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


class _NodeUtilsInternal(object):
    """Internal helpers for NodeUtils."""

    @staticmethod
    def _object_users(data):
        """Count scene **objects** referencing ``data``. ``data.users`` also counts fake users and
        references from other datablocks, which would false-positive instance detection."""
        import bpy

        return sum(1 for o in bpy.data.objects if o.data is data)

    @staticmethod
    def _local_bbox_size(obj):
        """Extents of ``obj`` in its OWN local frame — the frame its scale channels act in, hence
        independent of the object's rotation, unlike a world bounding box. Mirror of mayatk's
        ``NodeUtils._local_bbox_size``, which has to reach for the shape's object-space box;
        here ``XformUtils.get_bounding_box(world_space=False)`` already pools ``bound_box``
        (object space, no ``matrix_world``)."""
        import blendertk as btk

        return btk.get_bounding_box(obj, "size", world_space=False)


class NodeUtils(_NodeUtilsInternal):
    """Namespace mirror of mayatk's ``NodeUtils`` (instance helpers also exposed module-level)."""

    @staticmethod
    def get_instances(objects=None):
        """Return objects that share their data with another object (Maya-style instances).

        ``objects=None`` scans the whole scene; otherwise restricts the result to objects sharing
        a datablock with any of the given ``objects``. "Shared" means >1 *object* references the data.
        """
        import bpy
        from collections import Counter

        scene_objs = [
            o for o in bpy.data.objects if getattr(o, "data", None) is not None
        ]
        counts = Counter(o.data for o in scene_objs)  # object-user counts (one pass)
        if objects is None:
            return [o for o in scene_objs if counts[o.data] > 1]
        datas = {
            o.data
            for o in ptk.make_iterable(objects)
            if getattr(o, "data", None) is not None
        }
        return [o for o in scene_objs if o.data in datas and counts[o.data] > 1]

    @staticmethod
    def replace_with_instances(
        objects,
        freeze_transforms=False,
        center_pivot=False,
        delete_history=False,
        retain_bbox_scale=False,
        retain_bbox_per_axis=False,
    ):
        """Make ``objects[1:]`` instances of ``objects[0]`` by sharing its data — Blender's linked
        duplicate, the analogue of Maya's shared-shape instancing (mirror of ``mtk.replace_with_instances``).

        Targets adopt the source's datablock (only same-type, data-bearing objects). ``freeze_transforms``
        / ``center_pivot`` pre-clean the objects via the xform helpers; ``delete_history`` is a no-op in
        Blender (no construction history) — kept for signature parity. ``retain_bbox_scale`` preserves each
        target's apparent size: a target keeps its own scale channels, so adopting a differently-sized
        datablock would resize it — this uniformly rescales it back to its pre-instance world bounding-box
        size. ``retain_bbox_per_axis`` (only with ``retain_bbox_scale``) matches each axis independently
        instead (legal — every object owns its scale channels), measured in the local frame so the fit
        is rotation-independent; prefer the
        uniform default unless source and target share proportions, since a per-axis fit reaches the
        target's box by distorting the shared data. Returns the instanced targets.
        """
        import bpy
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
            # Translation only — the mirror of mtk.replace_with_instances
            # (rotation/scale stay live so each instance keeps its own
            # orientation and size against the shared data; baking source
            # scale would rescale every target's adopted datablock).
            btk.freeze_transforms([source], location=True, rotation=False, scale=False)
        instanced, pre_sizes = [], []
        for t in targets:
            if getattr(t, "data", None) is not None and t.type == source.type:
                if retain_bbox_scale:  # measure BEFORE the datablock is swapped
                    if retain_bbox_per_axis:
                        pre_sizes.append(NodeUtils._local_bbox_size(t))
                    else:
                        mn, mx = btk.get_world_bbox(t)
                        pre_sizes.append(mx - mn)
                t.data = source.data
                instanced.append(t)
        if retain_bbox_scale and instanced:
            # The swapped-in data only reaches bound_box/matrix_world after evaluation.
            bpy.context.view_layer.update()
            for t, want in zip(instanced, pre_sizes):
                if retain_bbox_per_axis:
                    # Local frame: the axes the scale channels act in, so the fit holds
                    # under any rotation. An axis with no extent on either side has no
                    # derivable ratio — it keeps its current scale rather than collapsing.
                    have = NodeUtils._local_bbox_size(t)
                    t.scale = [
                        v * ((w / h) if (h > 1e-9 and w > 1e-9) else 1.0)
                        for v, w, h in zip(t.scale, want, have)
                    ]
                else:
                    mn, mx = btk.get_world_bbox(t)
                    have = mx - mn
                    # Averaged world-box ratio: safe under any orientation, and it never
                    # distorts the shared data. Degenerate axes yield no ratio — skipped.
                    ratios = [
                        want[i] / have[i]
                        for i in range(3)
                        if have[i] > 1e-9 and want[i] > 1e-9
                    ]
                    if ratios:
                        factor = sum(ratios) / len(ratios)
                        t.scale = [v * factor for v in t.scale]
            bpy.context.view_layer.update()  # settle matrix_world for callers
        return instanced

    @staticmethod
    def uninstance(objects, freeze=False):
        """Break the instance link — make each object's data single-user (mirror of ``mtk.uninstance``).

        Blender: replace a shared datablock with an independent copy. Returns the objects changed.

        ``freeze`` (optional additional step): after breaking the link, bake each object's SCALE
        into its now-unique mesh. Breaking the link alone leaves the transform untouched — a
        mirrored linked duplicate still carries its negative scale, which is the part exporters
        and game engines object to. Baking is only possible once the data is single-user
        (``transform_apply`` refuses multi-user meshes outright), which is why the two steps
        belong together: ``uninstance(objs, freeze=True)`` is the engine-safe finish.
        """
        result, processed = [], []
        for o in (
            x
            for x in ptk.make_iterable(objects)
            if getattr(x, "data", None) is not None
        ):
            processed.append(o)
            if _NodeUtilsInternal._object_users(o.data) > 1:
                o.data = o.data.copy()
                result.append(o)

        # Freeze everything that was PASSED, not just what needed forking — the caller
        # asked for the bake, and an already-unique object still needs it. (The return
        # value keeps its documented meaning: the objects whose link was broken.)
        if freeze and processed:
            # Local import: xform_utils imports NodeUtils (multi-user detection), so a
            # module-level import here would be circular.
            from blendertk.xform_utils._xform_utils import XformUtils

            XformUtils.freeze_transforms(
                processed, location=False, rotation=False, scale=True
            )
        return result

    @staticmethod
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

    @staticmethod
    def get_children(obj, recursive=False):
        """The object's children — mirror of ``mtk.get_children``. ``recursive=True`` returns the
        whole descendant subtree."""
        if recursive:
            return list(getattr(obj, "children_recursive", []))
        return list(getattr(obj, "children", []))

    @staticmethod
    def get_shape(obj):
        """The object's data datablock (mesh/curve/…) — the Blender analogue of Maya's shape node
        under a transform (mirror of ``mtk.get_shape``). Returns ``None`` for data-less objects
        (e.g. Empties)."""
        return getattr(obj, "data", None)

    @staticmethod
    def reparent(objects, parent, keep_transform=True):
        """Parent ``objects`` under ``parent`` (``None`` to unparent) — mirror of ``mtk.reparent``.

        ``keep_transform`` preserves each object's world position (Blender's "Keep Transform").
        Skips parenting an object to itself. Returns the reparented objects.
        """
        import bpy

        out = []
        for o in (
            x for x in ptk.make_iterable(objects) if x is not None and x is not parent
        ):
            world = o.matrix_world.copy()
            o.parent = parent
            if keep_transform:
                o.matrix_world = world
            out.append(o)
        bpy.context.view_layer.update()
        return out
