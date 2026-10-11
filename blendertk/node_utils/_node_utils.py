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

import contextlib
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pythontk as ptk


@dataclass
class _PreservedInstances:
    """Handle yielded by :meth:`NodeUtils._preserved_instances`.

    ``objects`` is the operable set — one master per linked-data group plus
    every non-instanced input.  ``map`` resolves each input member to the
    object that carries it through the block (its group's master, or
    itself).  ``skipped``/``restored``/``errors`` report what the context
    manager did; the last two are populated on exit.
    """

    objects: list = field(default_factory=list)
    map: dict = field(default_factory=dict)
    #: master -> all group members (master first).
    groups: dict = field(default_factory=dict)
    #: targeted member -> reason it could not be preserved.
    skipped: dict = field(default_factory=dict)
    restored: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class _NodeUtilsInternal(object):
    """Internal helpers for NodeUtils."""

    @staticmethod
    def _object_users(data):
        """Count scene **objects** referencing ``data``. ``data.users`` also counts fake users and
        references from other datablocks, which would false-positive instance detection."""
        import bpy

        return sum(1 for o in bpy.data.objects if o.data is data)

    @staticmethod
    def _transforms_driven(obj) -> bool:
        """True when something re-writes *obj*'s transform after we would set
        it: transform drivers / fcurves (the delta channels included) in its
        action or an unmuted NLA strip, or an unmuted constraint. Compact
        yes/no twin of ``transform_diag._driving_connections`` (which returns
        per-connection tags for its diagnosis dict)."""
        paths = (
            "location",
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
            "scale",
            "delta_location",
            "delta_rotation_euler",
            "delta_rotation_quaternion",
            "delta_scale",
        )
        anim = getattr(obj, "animation_data", None)
        if anim:
            if any(d.data_path in paths for d in (anim.drivers or [])):
                return True
            # Slot-aware: Blender 5.x dropped the flat ``action.fcurves``.
            from blendertk.anim_utils._anim_utils import AnimUtils

            if anim.action and any(
                f.data_path in paths for f in AnimUtils.get_fcurves(obj)
            ):
                return True
            # A strip plays its action with no active one assigned.
            for track in anim.nla_tracks:
                if track.mute:
                    continue
                for strip in track.strips:
                    if strip.mute or strip.action is None:
                        continue
                    curves = AnimUtils._slot_fcurves(
                        strip.action, getattr(strip, "action_slot", None)
                    )
                    if any(f.data_path in paths for f in curves):
                        return True
        return any(not c.mute for c in getattr(obj, "constraints", []))

    @staticmethod
    def _is_maya_node_type(objects, node_type, filter):
        """Body of :meth:`NodeUtils.is_locator` / :meth:`NodeUtils.is_group`."""
        import bpy

        objs = [
            bpy.data.objects.get(o) if isinstance(o, str) else o
            for o in ptk.make_iterable(objects)
        ]
        objs = [o for o in objs if o is not None]
        result = [NodeUtils.get_maya_node_type(o) == node_type for o in objs]
        if filter:
            return [o for o, hit in zip(objs, result) if hit]
        return ptk.format_return(result, objects)

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

    #: The custom property naming the Maya node an Empty stands for: ``"locator"`` or
    #: ``"group"``. Maya tells the two apart by a locator SHAPE; Blender has only the
    #: Empty, so the type travels as this stamp -- the Maya pull writes it on every Empty
    #: it brings in, the locator rig on the Empties it builds, and the Maya send restores
    #: each stamped Empty as that node type.
    MAYA_NODE_TYPE_PROP = "maya_node_type"

    #: Display size of an Empty that stands for a Maya GROUP. A Maya group draws
    #: nothing; Blender has no "no display" Empty, so the group is shrunk to the
    #: property's hard minimum (``Object.empty_display_size`` clamps at 0.0001) --
    #: sub-pixel at any working zoom, yet still selectable (outliner / pick-walk),
    #: transformable, and showing its origin dot when selected, like a Maya group's
    #: pivot. Unlike a Maya group, a click on its origin can select it: Blender cycles
    #: a repeat click through everything under it (a locator rig can draw its group
    #: clear of the centre instead -- :attr:`RigUtils.LOCATOR_RING_SCALE`). A locator
    #: keeps its size: Maya draws those. Deliberately NOT
    #: ``hide_viewport`` / ``hide_set``: both make the Empty untransformable and would
    #: fight the animated ``hide_viewport`` keys the Maya pull's visibility replay stamps.
    MAYA_GROUP_DISPLAY_SIZE = 0.0001

    @staticmethod
    def get_maya_node_type(obj) -> Optional[str]:
        """The Maya node *obj* stands for: ``"locator"``, ``"group"`` or ``None``.

        Maya has a locator shape; Blender only the Empty, so an Empty answers by its
        :attr:`MAYA_NODE_TYPE_PROP` stamp when it carries one -- a rig's locator is one
        even holding its object -- and otherwise by its hierarchy: a parent is a group,
        a leaf a locator. (The Maya send reads an unstamped Empty the same way, save
        that it takes any display but ``PLAIN_AXES`` as a deliberate locator; a stamp
        settles an Empty for both.) An image Empty (Blender's image plane) and any
        non-Empty stand for neither.

        Parameters:
            obj (bpy.types.Object/None): The object to classify.

        Returns:
            (str/None): ``"locator"``, ``"group"`` (or another stamped type), or ``None``.
        """
        if getattr(obj, "type", None) != "EMPTY":
            return None
        stamp = obj.get(NodeUtils.MAYA_NODE_TYPE_PROP)
        if stamp:
            return str(stamp)
        if obj.empty_display_type == "IMAGE":
            return None
        return "group" if obj.children else "locator"

    @staticmethod
    def set_maya_node_type(objects, node_type: str, look: bool = True) -> List[Any]:
        """Stamp Empties as the Maya node *node_type* they stand for.

        Writes :attr:`MAYA_NODE_TYPE_PROP` -- what :meth:`get_maya_node_type` reads and
        the Maya send restores. With *look*, an Empty also takes that node's Maya look:
        a ``"group"`` draws nothing (:attr:`MAYA_GROUP_DISPLAY_SIZE`); a ``"locator"``
        keeps its display, since Maya draws those. A non-Empty is skipped: only an
        Empty stands in for a node Blender has no object type for.

        Parameters:
            objects (obj/str/list): The Empties (or names).
            node_type (str): ``"locator"`` or ``"group"``.
            look (bool): Give each Empty its node's Maya look. False stamps the type
                alone, keeping the Empty's own display -- a group grabbed by its arrows.

        Returns:
            (list): The Empties stamped.
        """
        import bpy

        stamped = []
        for o in ptk.make_iterable(objects):
            o = bpy.data.objects.get(o) if isinstance(o, str) else o
            if getattr(o, "type", None) != "EMPTY":
                continue
            o[NodeUtils.MAYA_NODE_TYPE_PROP] = node_type
            if look and node_type == "group":
                o.empty_display_size = NodeUtils.MAYA_GROUP_DISPLAY_SIZE
            stamped.append(o)
        return stamped

    @staticmethod
    def is_locator(objects, filter: bool = False):
        """Whether each of *objects* (refs or names) is a locator.

        Mirror of mayatk's, where a locator is a transform with a locator shape -- here
        an Empty that stands for one (:meth:`get_maya_node_type`). ``filter`` returns
        the locators instead of one bool per object.

        Parameters:
            objects (obj/str/list): The objects (or names); unknown names are skipped.
            filter (bool): Return the locators rather than one bool per object.

        Returns:
            (bool/list): One bool per object (a single bool for a single object), or
            the locators with *filter*.
        """
        return NodeUtils._is_maya_node_type(objects, "locator", filter)

    @staticmethod
    def expand_sets(objects) -> List[Any]:
        """*objects* with every collection replaced by the objects in it.

        Mirror of mayatk's, where a set stands for its members: Blender's sets
        are collections (a bake set is a stamped one). A collection given -- a
        ref, or a name no object has -- stands for every object in it and its
        child collections (``all_objects``); everything else passes through
        as given, in order.

        Parameters:
            objects (obj/str/list): Objects, collections, or their names.

        Returns:
            (list): The objects, each collection replaced by its objects.
        """
        import bpy

        result: List[Any] = []
        for o in ptk.make_iterable(objects):
            if isinstance(o, bpy.types.Collection):
                coll = o
            elif isinstance(o, str) and o not in bpy.data.objects:
                coll = bpy.data.collections.get(o)
            else:
                coll = None
            if coll is None:
                result.append(o)
            else:
                result.extend(coll.all_objects)
        return result

    @staticmethod
    def is_group(objects, filter: bool = False):
        """Whether each of *objects* (refs or names) is a group.

        Mirror of mayatk's, where a group is a transform with no shape -- here an
        Empty that stands for one (:meth:`get_maya_node_type`), so a locator is no
        group, as in Maya. ``filter`` returns the groups instead of one bool per
        object.

        Parameters:
            objects (obj/str/list): The objects (or names); unknown names are skipped.
            filter (bool): Return the groups rather than one bool per object.

        Returns:
            (bool/list): One bool per object (a single bool for a single object), or
            the groups with *filter*.
        """
        return NodeUtils._is_maya_node_type(objects, "group", filter)

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
    @contextlib.contextmanager
    def _preserved_instances(objects, quiet=True):
        """Context manager: keep linked-data instancing intact across a
        destructive op.

        Blender-only: Maya's twin was withdrawn because forking + re-linking
        a shape there renumbers ``instObjGroups`` and breaks per-instance
        shading (``mtk.XformUtils.freeze_instanced_group`` bakes in place
        instead). Blender re-points ``obj.data``, which has no such
        side effect, so the context-manager shape is safe here — it stays
        private since there is no Maya counterpart to mirror.

        For each linked-data group among *objects*, the first targeted member
        (the *master*) gets a unique copy of the shared datablock and is
        yielded for the wrapped operation; its siblings keep the original
        data, untouched.  On exit — including when the block raises — every
        sibling re-adopts the master's post-op data and its world matrix is
        compensated by the master's baked delta (``B = post_world⁻¹ @
        pre_world``; sibling ``W → W @ B⁻¹``), so both the data sharing and
        every member's world-space geometry are preserved.  Restore is
        per-group best-effort: one failed group never strands the rest, and
        errors are aggregated on the handle.

        Contract for the wrapped operation: any change it makes to the
        master's world matrix must be baked into the master's data (the
        ``transform_apply`` contract); pure data edits need no compensation
        and get none (``B ≈ I``).  Moving a master without baking is outside
        the contract.  Sibling channels are rewritten by the compensation;
        stored bake history (``freeze_transforms(store=True)``) on siblings
        is not updated.

        Groups that cannot be preserved are skipped up front — reported on
        the handle, their members untouched: library-linked objects/data,
        and siblings whose transforms are driven (drivers, action fcurves,
        unmuted constraints).

        Yields:
            _PreservedInstances: ``objects`` (the operable set), ``map``,
            ``groups``, ``skipped``; ``restored``/``errors`` fill on exit.

        Example:
            with btk.NodeUtils._preserved_instances(sel) as ctx:
                btk.freeze_transforms(ctx.objects, scale=True)
        """
        import bpy
        from collections import Counter

        ctx = _PreservedInstances()
        targets = [o for o in ptk.make_iterable(objects) if o is not None]
        targets_set = set(id(o) for o in targets)

        scene_objs = [
            o for o in bpy.data.objects if getattr(o, "data", None) is not None
        ]
        counts = Counter(o.data for o in scene_objs)

        def _skip(members, reason):
            for m in members:
                assigned.add(id(m))
                if id(m) in targets_set:
                    ctx.skipped[m] = reason
                    if not quiet:
                        print(f"preserved_instances: skipping '{m.name}' — {reason}")

        assigned = set()
        groups = []
        bpy.context.view_layer.update()  # settle matrix_world before capture

        for t in targets:
            if id(t) in assigned:
                continue
            data = getattr(t, "data", None)
            if data is None or counts.get(data, 0) <= 1:
                assigned.add(id(t))
                ctx.objects.append(t)
                ctx.map[t] = t
                continue

            siblings = sorted(
                (o for o in scene_objs if o.data is data and o is not t),
                key=lambda o: o.name,
            )
            members = [t] + siblings

            if any(o.library is not None for o in members) or (
                data.library is not None
            ):
                _skip(members, "library-linked data in group")
                continue
            driven = [m for m in siblings if _NodeUtilsInternal._transforms_driven(m)]
            if driven:
                _skip(members, f"driven sibling channels ({driven[0].name})")
                continue

            groups.append(
                {
                    "master": t,
                    "old_data": data,
                    "pre_world": t.matrix_world.copy(),
                    "siblings": siblings,
                    "sib_world": {o: o.matrix_world.copy() for o in siblings},
                }
            )
            assigned.update(id(m) for m in members)
            ctx.objects.append(t)
            ctx.groups[t] = members
            for m in members:
                ctx.map[m] = t

        # Fork each master's datablock — the only pre-op mutation.
        for rec in groups:
            rec["master"].data = rec["old_data"].copy()

        try:
            yield ctx
        finally:
            bpy.context.view_layer.update()
            for rec in groups:
                try:
                    NodeUtils._restore_instance_group(rec, ctx)
                except Exception as e:
                    ctx.errors.append(
                        f"restore failed for group '{rec['master']}': {e}"
                    )
                    if not quiet:
                        print(
                            "preserved_instances: restore failed for "
                            f"'{rec['master']}': {e}"
                        )
            bpy.context.view_layer.update()

    @staticmethod
    def _restore_instance_group(rec, ctx) -> None:
        """Re-link one group's siblings to the master's post-op data and
        compensate their world matrices.  See :meth:`preserved_instances`."""
        import bpy
        from blendertk.xform_utils.matrices import Matrices

        master = rec["master"]
        try:
            master.name  # raises ReferenceError if the op deleted it
        except ReferenceError:
            raise RuntimeError("master no longer exists")

        B = master.matrix_world.inverted() @ rec["pre_world"]
        b_identity = Matrices.is_identity(B)

        for o in rec["siblings"]:
            try:
                o.data = master.data
                if not b_identity:
                    o.matrix_world = rec["sib_world"][o] @ B.inverted()
                ctx.restored.append(o)
            except ReferenceError:
                ctx.errors.append("a sibling disappeared during the operation")

        # The original datablock is orphaned once every sibling re-adopted
        # the fork — drop it and let the fork take over its name.
        old = rec["old_data"]
        try:
            old_name = old.name
            if old.users == 0:
                bpy.data.batch_remove((old,))
                master.data.name = old_name
        except ReferenceError:
            pass

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
