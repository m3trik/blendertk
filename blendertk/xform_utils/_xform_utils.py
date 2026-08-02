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

from blendertk.core_utils._core_utils import (
    CoreUtils,
)  # shared OBJECT-mode guard + window-independent selection read


# Custom-prop keys freeze_transforms stamps so restore_transforms can un-freeze
# (the Blender analogue of mayatk's ``original_{T,R,S}_bake`` attributes).
# ``prefix`` mirrors mayatk's: the default is the user-facing freeze history,
# and a tool that re-bases a local frame for its own bookkeeping stamps under
# its own prefix so the two never compose (see the auto-instancer).
_DEFAULT_BAKE_PREFIX = "btk"
_BAKE_T, _BAKE_R, _BAKE_S = (
    f"{_DEFAULT_BAKE_PREFIX}_{channel}_bake" for channel in "TRS"
)


_ORIGIN_MODES = {
    "object": ("ORIGIN_GEOMETRY", "BOUNDS"),  # bbox center
    "median": ("ORIGIN_GEOMETRY", "MEDIAN"),  # geometry median
    "component": ("ORIGIN_GEOMETRY", "MEDIAN"),  # Maya "component" ~= median
}


# aim/up world-vector -> Blender track-axis tokens for ``Vector.to_track_quat``.
_TRACK_AXIS = {
    (1, 0, 0): "X",
    (-1, 0, 0): "-X",
    (0, 1, 0): "Y",
    (0, -1, 0): "-Y",
    (0, 0, 1): "Z",
    (0, 0, -1): "-Z",
}


class _XformUtilsInternal(object):
    """Internal helpers for XformUtils."""

    @staticmethod
    def _bake_keys(prefix=_DEFAULT_BAKE_PREFIX):
        """``(T, R, S)`` custom-prop key triple for *prefix* (mirror of
        mayatk's ``_XformUtilsInternal._bake_attr_names``)."""
        return f"{prefix}_T_bake", f"{prefix}_R_bake", f"{prefix}_S_bake"

    @staticmethod
    def _is_multi_user(obj):
        """Whether ``obj``'s data is shared with another OBJECT (a linked duplicate).

        Delegates the count to ``NodeUtils``' canonical object-user tally, which
        deliberately ignores fake users and non-object references that ``data.users``
        would inflate (they don't block a bake).
        """
        from blendertk.node_utils._node_utils import _NodeUtilsInternal

        data = getattr(obj, "data", None)
        return data is not None and _NodeUtilsInternal._object_users(data) > 1

    @staticmethod
    def _combined_bbox(objects):
        from mathutils import Vector

        boxes = [XformUtils.get_world_bbox(o) for o in objects]
        mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
        mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
        return mn, mx

    @staticmethod
    def _pivot_point(objects, pivot):
        from mathutils import Vector

        if pivot == "object":
            locs = [o.matrix_world.translation for o in objects]
            return sum(locs, Vector((0.0, 0.0, 0.0))) / len(locs)
        mn, mx = _XformUtilsInternal._combined_bbox(objects)
        return (mn + mx) / 2.0  # bounding-box center

    @staticmethod
    def _store_bakes(obj, location, rotation, scale, prefix=_DEFAULT_BAKE_PREFIX):
        """Record the pre-freeze local channels, composing with any existing bake (the
        cumulative freeze/unfreeze contract: repeated freeze+transform cycles compose,
        one restore returns the full history — T adds, R quaternion-composes, S multiplies)."""
        from mathutils import Quaternion, Vector

        bake_t, bake_r, bake_s = _XformUtilsInternal._bake_keys(prefix)
        loc, rot, scl = obj.matrix_basis.decompose()
        if location:
            prior = Vector(obj.get(bake_t, (0.0, 0.0, 0.0)))
            obj[bake_t] = list(prior + loc)
        if rotation:
            prior = Quaternion(obj.get(bake_r, (1.0, 0.0, 0.0, 0.0)))
            obj[bake_r] = list(prior @ rot)
        if scale:
            prior = Vector(obj.get(bake_s, (1.0, 1.0, 1.0)))
            obj[bake_s] = [prior[i] * scl[i] for i in range(3)]

    @staticmethod
    def _invalidate_location_bake(objects, prefix=_DEFAULT_BAKE_PREFIX):
        """Drop any stored freeze-*location* bake on *objects*.

        Blender's origin IS the translate reference, so ANY origin move
        (``origin_set`` in all its forms) invalidates a stored location bake —
        leaving it would make a later ``restore_transforms`` double-apply the
        translation. Rotation/scale bakes are unaffected. Every origin-moving
        path must come through here; that is the whole reason it is a helper
        rather than three lines inlined in ``center_pivot``.
        """
        bake_t = _XformUtilsInternal._bake_keys(prefix)[0]
        for obj in ptk.make_iterable(objects):
            if obj is not None and bake_t in obj:
                del obj[bake_t]

    @staticmethod
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

    @staticmethod
    def _as_point(value):
        """Coerce an object / Vector / 3-sequence to a world-space ``Vector`` position."""
        from mathutils import Vector

        if hasattr(value, "matrix_world"):
            return value.matrix_world.translation.copy()
        return Vector(tuple(value))


class XformUtils(_XformUtilsInternal):
    """Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level)."""

    @staticmethod
    def get_world_bbox(obj):
        """Return ``(min, max)`` ``Vector``s of ``obj``'s bounding box in world space."""
        from mathutils import Vector

        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        mn = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
        mx = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
        return mn, mx

    @staticmethod
    @CoreUtils._object_mode
    def freeze_transforms(
        objects,
        location=True,
        rotation=False,
        scale=True,
        store=True,
        instance_strategy="skip",
    ):
        """Apply (bake) the given transform channels into the object data — Blender's
        ``transform_apply`` (mirror of ``mtk.freeze_transforms``). ``store`` stamps the
        pre-freeze channels as custom props so :func:`restore_transforms` can un-freeze.

        ``instance_strategy`` decides what happens to multi-user (linked) objects —
        a bake into shared data would rewrite every linked duplicate's geometry, and
        ``transform_apply`` refuses it outright:

        - ``"skip"`` (default): skipped in place with a message; the rest of the
          batch still bakes (matching mayatk's twin).
        - ``"preserve"``: each group's first targeted member is baked via
          ``NodeUtils._preserved_instances`` — data sharing and every member's
          world geometry survive; sibling matrices are rewritten with the
          compensating delta (so only the operated member ends identity).
        - ``"uninstance"``: break the links first (``NodeUtils.uninstance``),
          then bake every object normally.

        Returns the objects actually baked (mirroring :func:`restore_transforms`) —
        empty when everything was skipped, which is the only way a caller can tell a
        fully-skipped multi-user batch from a successful one.
        """
        import bpy

        if not (location or rotation or scale):
            return []  # nothing to bake (transform_apply with all channels off is a no-op/error)
        objects = [o for o in ptk.make_iterable(objects) if o]
        if not objects:
            return []

        inst_strategy = (instance_strategy or "skip").lower()
        valid_inst_strategies = {"skip", "preserve", "uninstance"}
        if inst_strategy not in valid_inst_strategies:
            raise ValueError(
                f"Invalid instance_strategy '{instance_strategy}'. "
                f"Valid options: {sorted(valid_inst_strategies)}"
            )

        if inst_strategy != "skip":
            multi = [o for o in objects if _XformUtilsInternal._is_multi_user(o)]
            if multi:
                # Local import: node_utils imports XformUtils for its freeze
                # option, so a module-level import here would be circular.
                from blendertk.node_utils._node_utils import NodeUtils

                if inst_strategy == "uninstance":
                    NodeUtils.uninstance(multi)
                else:  # preserve — re-enter with each group's master forked;
                    # the CM restores siblings (compensated) on exit.
                    with NodeUtils._preserved_instances(objects, quiet=False) as ctx:
                        return XformUtils.freeze_transforms(
                            ctx.objects,
                            location=location,
                            rotation=rotation,
                            scale=scale,
                            store=store,
                            instance_strategy="skip",
                        )

        shared = [o for o in objects if _XformUtilsInternal._is_multi_user(o)]
        if shared:
            names = ", ".join(o.name for o in shared)
            print(
                f"XformUtils.freeze_transforms: skipping {len(shared)} multi-user "
                f"object(s) ({names}) — use NodeUtils.uninstance(objs, freeze=True) "
                f"to break the link and bake in one step."
            )
            objects = [o for o in objects if o not in shared]
            if not objects:
                return []

        bpy.ops.object.select_all(action="DESELECT")
        snapshots = []  # prior bake values (read pre-store) so a failed apply leaves no orphaned bakes
        for o in objects:
            if store:
                snapshots.append(
                    (
                        o,
                        {
                            k: (list(o[k]) if k in o else None)
                            for k in (_BAKE_T, _BAKE_R, _BAKE_S)
                        },
                    )
                )
                _XformUtilsInternal._store_bakes(o, location, rotation, scale)
            o.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        try:
            bpy.ops.object.transform_apply(
                location=location, rotation=rotation, scale=scale
            )
        except Exception:  # e.g. "Cannot apply to a multi user" — undo the just-stamped bakes and re-raise
            for o, snap in snapshots:
                for k, prev in snap.items():
                    if prev is None:
                        if k in o:
                            del o[k]
                    else:
                        o[k] = prev
            raise
        return objects

    @staticmethod
    @CoreUtils._object_mode
    def restore_transforms(
        objects,
        delete_attrs=True,
        channels=None,
        traverse=False,
        prefix=_DEFAULT_BAKE_PREFIX,
    ):
        """Un-freeze: compose the stored pre-freeze channels back into the local transform
        (``new local C = stored C ∘ current C``) and counter-shift the geometry so the world
        position is preserved — mirror of ``mtk.restore_transforms``. Bakes are stamped by
        :func:`freeze_transforms`. ``channels`` optionally restricts the restore to a subset
        of ``{"translate", "rotate", "scale"}`` (unlisted channels keep their bake history for
        later calls); ``traverse`` also restores every descendant, parents first — mirror of
        ``mtk.restore_transforms(traverse=True)``. ``prefix`` selects which bake history to
        consume (default: the user-facing freeze). Returns the objects restored."""
        import bpy
        from mathutils import Matrix, Quaternion, Vector

        bake_t, bake_r, bake_s = _XformUtilsInternal._bake_keys(prefix)
        valid_channels = {"translate", "rotate", "scale"}
        if channels is None:
            target_channels = valid_channels
        else:
            target_channels = set(channels) & valid_channels
            if not target_channels:
                return []

        targets = []
        seen = set()
        for obj in (o for o in ptk.make_iterable(objects) if o):
            if obj not in seen:
                targets.append(obj)
                seen.add(obj)
            if traverse:
                for child in obj.children_recursive:
                    if child not in seen:
                        targets.append(child)
                        seen.add(child)

        # Restore top-down: a child's restore composes against its parent's
        # already-restored transform (mirrors the mayatk restore contract).
        def _depth(o):
            d = 0
            while o.parent is not None:
                d += 1
                o = o.parent
            return d

        targets.sort(key=_depth)

        restored = []
        for obj in targets:
            has_t = bake_t in obj and "translate" in target_channels
            has_r = bake_r in obj and "rotate" in target_channels
            has_s = bake_s in obj and "scale" in target_channels
            if not (has_t or has_r or has_s):
                continue
            old_basis = obj.matrix_basis.copy()
            loc, rot, scl = old_basis.decompose()
            if has_t:
                loc = Vector(obj[bake_t]) + loc
            if has_r:
                rot = Quaternion(obj[bake_r]) @ rot
            if has_s:
                scl = Vector([obj[bake_s][i] * scl[i] for i in range(3)])
            new_basis = Matrix.LocRotScale(loc, rot, scl)
            if obj.data is not None and hasattr(obj.data, "transform"):
                obj.data.transform(new_basis.inverted() @ old_basis)
                obj.data.update()
            obj.matrix_basis = new_basis
            # Freeze (transform_apply) folded the applied basis into each
            # child's matrix_parent_inverse to keep children in place; invert
            # that compensation here or children double-inherit the restored
            # transform (child world = restored parent @ frozen compensation).
            delta = new_basis.inverted() @ old_basis
            for child in obj.children:
                child.matrix_parent_inverse = delta @ child.matrix_parent_inverse
            if delete_attrs:
                # Only the consumed channels — unrestored bake history stays
                # available for future restore calls (the mayatk contract).
                for consumed, k in (
                    (has_t, bake_t),
                    (has_r, bake_r),
                    (has_s, bake_s),
                ):
                    if consumed and k in obj:
                        del obj[k]
            restored.append(obj)
        bpy.context.view_layer.update()
        return restored

    @staticmethod
    def has_stored_transforms(objects, prefix=_DEFAULT_BAKE_PREFIX):
        """Map each object → whether it carries pre-freeze bake data (mirror of
        ``mtk.XformUtils.has_stored_transforms``). Bakes are stamped by :func:`freeze_transforms`;
        the Channels panel uses this to gate its Unfreeze action."""
        keys = _XformUtilsInternal._bake_keys(prefix)
        return {
            o: any(k in o for k in keys)
            for o in ptk.make_iterable(objects)
            if o
        }

    @staticmethod
    def store_transforms(
        objects, prefix=_DEFAULT_BAKE_PREFIX, channels=None, traverse=False
    ):
        """Capture the current local channels as cumulative bake history — mirror of
        ``mtk.XformUtils.store_transforms``.

        The stamp side of the freeze/unfreeze contract, exposed for tools that
        re-base a local frame WITHOUT going through :func:`freeze_transforms`
        (the axis-orthogonality fixer, the auto-instancer's canonicalization).
        Without it those rewrites destroy the authored frame unrecoverably.
        ``channels`` restricts the update to a subset of ``{"translate",
        "rotate", "scale"}``; ``traverse`` also stamps every descendant.
        """
        valid_channels = {"translate", "rotate", "scale"}
        target = valid_channels if channels is None else set(channels) & valid_channels
        if not target:
            return []

        stamped, seen = [], set()
        for obj in (o for o in ptk.make_iterable(objects) if o):
            if obj not in seen:
                stamped.append(obj)
                seen.add(obj)
            if traverse:
                for child in obj.children_recursive:
                    if child not in seen:
                        stamped.append(child)
                        seen.add(child)

        for obj in stamped:
            _XformUtilsInternal._store_bakes(
                obj,
                "translate" in target,
                "rotate" in target,
                "scale" in target,
                prefix=prefix,
            )
        return stamped

    @staticmethod
    def get_stored_transforms(obj, prefix=_DEFAULT_BAKE_PREFIX):
        """Read one object's stored pre-freeze channels back as plain values —
        mirror of ``mtk.XformUtils.get_stored_transforms``.

        The read side of the freeze/unfreeze contract: a frozen object reports
        identity channels, so anything needing its *authored* frame (pivot
        orientation, mirror/cut axes, instance matching) has to come through
        here rather than reading ``matrix_world``.

        Returns:
            (dict/None): ``{"translate": Vector, "rotate": Quaternion,
            "scale": Vector, "matrix": Matrix}`` — the pre-freeze local
            transform — or ``None`` when the object carries no bake history.
            Absent channels read as identity, so the dict is always complete.
        """
        from mathutils import Matrix, Quaternion, Vector

        if obj is None:
            return None
        bake_t, bake_r, bake_s = _XformUtilsInternal._bake_keys(prefix)
        if not any(k in obj for k in (bake_t, bake_r, bake_s)):
            return None

        loc = Vector(obj.get(bake_t, (0.0, 0.0, 0.0)))
        rot = Quaternion(obj.get(bake_r, (1.0, 0.0, 0.0, 0.0)))
        scl = Vector(obj.get(bake_s, (1.0, 1.0, 1.0)))
        return {
            "translate": loc,
            "rotate": rot,
            "scale": scl,
            "matrix": Matrix.LocRotScale(loc, rot, scl),
        }

    @staticmethod
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
        for o in (
            o for o in ptk.make_iterable(objects) if getattr(o, "type", None) == "MESH"
        ):
            if o.mode != "EDIT":
                continue
            bm = bmesh.from_edit_mesh(o.data)
            selected = [e for e in bm.edges if e.select]
            if not selected:
                continue
            for edge_set in _XformUtilsInternal._connected_edge_sets(selected):
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

    @staticmethod
    @CoreUtils._object_mode
    def drop_to_grid(objects, align="Min", origin=False, center_pivot=False):
        """Drop each object so its bbox ``Min`` / ``Mid`` / ``Max`` sits on the ground (Z=0).

        ``origin``: first move the object to the world origin. ``center_pivot``: re-center the
        object origin on its geometry bbox afterwards.
        """
        import bpy

        for obj in (o for o in ptk.make_iterable(objects) if o):
            if origin:  # move to the world origin (round-trip matrix_world so parented objects reach it)
                m = obj.matrix_world.copy()
                m.translation = (0.0, 0.0, 0.0)
                obj.matrix_world = m
            bpy.context.view_layer.update()
            mn, mx = XformUtils.get_world_bbox(obj)
            z = {"Min": mn.z, "Max": mx.z}.get(align, (mn.z + mx.z) / 2.0)
            m = (
                obj.matrix_world.copy()
            )  # shift Z in world space so the parent transform doesn't rescale it
            m.translation.z -= z
            obj.matrix_world = m
            bpy.context.view_layer.update()  # refresh matrix_world for downstream reads
            if (
                center_pivot
            ):  # bool param; reach the helper via the class (name is shadowed here)
                XformUtils.center_pivot(obj, mode="object")

    @staticmethod
    @CoreUtils._object_mode
    def center_pivot(objects, mode="object"):
        """Move each object's origin (Blender's single pivot) — mirror of Maya's Center Pivot.

        ``mode``: ``"object"`` bounding-box center, ``"median"`` / ``"component"`` geometry
        median, ``"world"`` the world origin (0,0,0), ``"cursor"`` the current 3D cursor
        (Maya's Bake Pivot). Headless-testable (object operator).
        ``@_object_mode``: ``select_all``/``origin_set`` need OBJECT mode and a window in
        context — its sibling ``transfer_pivot`` was guarded; this one wasn't.

        This is the ONLY sanctioned origin-move entry point: it drops the stored
        location bake afterwards (see ``_invalidate_location_bake``), which a
        direct ``bpy.ops.object.origin_set`` call does not.
        """
        import bpy

        objects = [o for o in ptk.make_iterable(objects) if o]
        if not objects:
            return
        bpy.ops.object.select_all(action="DESELECT")
        for o in objects:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        if mode == "cursor":
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        elif mode == "world":
            cursor = bpy.context.scene.cursor.location.copy()
            bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
            try:
                bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
            finally:
                bpy.context.scene.cursor.location = cursor
        else:
            otype, center = _ORIGIN_MODES.get(mode, _ORIGIN_MODES["object"])
            bpy.ops.object.origin_set(type=otype, center=center)
        _XformUtilsInternal._invalidate_location_bake(objects)
        bpy.context.view_layer.update()  # refresh matrix_world for downstream reads

    @staticmethod
    @CoreUtils._object_mode
    def transfer_pivot(
        objects,
        translate=True,
        rotate=False,
        scale=False,
        world_space=True,
        mirror="",
        select_targets_after_transfer=False,
    ):
        """Transfer the object **origin** from the first object to the rest — mirror of Maya's
        ``transfer_pivot`` (``source = objects[0]``, targets = the remainder).

        Blender has a single object origin (a point), so only Maya's **translate** pivot maps:
        each target's origin moves onto the source's origin *without moving its geometry* (3D-cursor
        → ``ORIGIN_CURSOR``). The ``rotate`` / ``scale`` flags are accepted for signature parity but
        no-op — Blender has no separate rotate/scale pivot. ``world_space`` is implicit (the origin is
        read in world space). ``mirror`` ("x"/"y"/"z", default off) reflects the transferred origin
        across that world axis-plane through the origin before the snap — the position level of
        mtk's mirror= (orientation-conjugation stays N/A: a point origin has no orientation).
        Returns the target objects (selected afterward when requested).
        """
        import bpy

        objects = [o for o in ptk.make_iterable(objects) if o]
        if len(objects) < 2:
            return []
        source, targets = objects[0], objects[1:]

        scene = bpy.context.scene
        saved_cursor = tuple(scene.cursor.location)
        saved_active = bpy.context.view_layer.objects.active
        saved_sel = list(
            CoreUtils.selected_objects()
        )  # view-layer read: bpy.context.selected_objects is empty from the Qt-pump context
        try:
            if translate:
                pos = list(source.matrix_world.translation)
                axis = str(mirror).strip().lower()
                if axis in ("x", "y", "z"):
                    idx = "xyz".index(axis)
                    pos[idx] = -pos[idx]
                scene.cursor.location = pos
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

    @staticmethod
    def get_pivot_modes():
        """Center-pivot mode keys understood by :func:`center_pivot`."""
        return ["object", "median", "world"]

    @staticmethod
    def match_scale(source, target, average=True):
        """Uniformly rescale ``source`` object(s) to match ``target``'s bounding-box size."""
        targets = [o for o in ptk.make_iterable(target) if o]
        if not targets:
            return
        t_mn, t_mx = _XformUtilsInternal._combined_bbox(targets)
        t_size = t_mx - t_mn
        for src in (o for o in ptk.make_iterable(source) if o):
            s_mn, s_mx = XformUtils.get_world_bbox(src)
            s_size = s_mx - s_mn
            ratios = [t_size[i] / s_size[i] for i in range(3) if s_size[i] > 1e-9]
            if not ratios:
                continue
            factor = (sum(ratios) / len(ratios)) if average else max(ratios)
            src.scale = [v * factor for v in src.scale]

    @staticmethod
    def move_to(source, target, pivot="center"):
        """Move ``source`` object(s) so their pivot aligns with the ``target``'s pivot point."""
        import bpy

        targets = [o for o in ptk.make_iterable(target) if o]
        if not targets:
            return
        dst = _XformUtilsInternal._pivot_point(targets, pivot)
        for src in (o for o in ptk.make_iterable(source) if o):
            cur = _XformUtilsInternal._pivot_point([src], pivot)
            mw = (
                src.matrix_world.copy()
            )  # dst/cur are world-space; apply the delta through matrix_world
            mw.translation = mw.translation + (dst - cur)
            src.matrix_world = mw
            bpy.context.view_layer.update()

    @staticmethod
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
            mn, mx = _XformUtilsInternal._combined_bbox(objs)
        else:
            corners = [
                c for o in objs for c in o.bound_box
            ]  # all objects' local corners pooled
            mn = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
            mx = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
        size = mx - mn
        bbox = {
            "xmin": mn.x,
            "xmax": mx.x,
            "ymin": mn.y,
            "ymax": mx.y,
            "zmin": mn.z,
            "zmax": mx.z,
            "sizex": size.x,
            "sizey": size.y,
            "sizez": size.z,
            "size": tuple(size),
            "volume": size.x * size.y * size.z,
            "center": tuple((mn + mx) / 2.0),
            "min": tuple(mn),
            "max": tuple(mx),
        }
        return bbox.get(value) if value else bbox

    @staticmethod
    def get_center_point(objects):
        """Bounding-box center of ``objects`` as an ``(x, y, z)`` tuple (mirror of
        ``mtk.get_center_point``)."""
        return XformUtils.get_bounding_box(objects, "center")

    @staticmethod
    def get_operation_axis_matrix(obj, pivot):
        """World pivot frame (orientation + position, scale stripped) for a per-object linear/
        radial operation — mirror of ``mtk.XformUtils.get_operation_axis_matrix``. Shared by the
        duplicate-array tools (``duplicate_linear`` et al.) that orbit/translate each copy about a
        chosen pivot rather than the object's own origin.

        ``pivot``:
          - ``"object"`` — the object's own orientation + its origin.
          - ``"original"`` — the object's PRE-FREEZE orientation, rebuilt from its stored
            bake history. A freeze zeroes the rotation channel, so a frozen object's local
            axes ARE the world axes and ``"object"`` silently degrades into ``"world"`` —
            losing the frame the asset was authored in for every axis-based op. Falls back
            to ``"object"`` when the object carries no bake history, so it is always safe
            to pass.
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
        if pivot == "original":
            # Column-vector convention: world = matrix_world @ local. A freeze
            # folded the old basis into the data, so a point that sat at p
            # pre-freeze now sits at (stored @ p) in the current local space —
            # making ``matrix_world @ stored`` the authored-frame -> world map.
            stored = XformUtils.get_stored_transforms(obj)
            if stored is None:
                return obj.matrix_world.normalized()
            return (obj.matrix_world @ stored["matrix"]).normalized()
        if pivot == "manip":
            import bpy

            return bpy.context.scene.cursor.matrix.copy()
        if pivot == "world":
            return Matrix.Identity(4)
        if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
            return Matrix.Translation(Vector(pivot))

        mn, mx = XformUtils.get_world_bbox(obj)
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

    @staticmethod
    def get_distance(a, b):
        """Distance between two points — each an object (world origin), ``Vector``, or 3-sequence
        (mirror of ``mtk.get_distance``)."""
        return (
            _XformUtilsInternal._as_point(a) - _XformUtilsInternal._as_point(b)
        ).length

    @staticmethod
    def order_by_distance(objects, reference_point=None, reverse=False):
        """Order ``objects`` by distance from ``reference_point`` (an object / Vector / 3-seq;
        default world origin) — mirror of ``mtk.order_by_distance``. Returns the ordered list."""
        from mathutils import Vector

        ref = (
            _XformUtilsInternal._as_point(reference_point)
            if reference_point is not None
            else Vector((0.0, 0.0, 0.0))
        )
        objs = [o for o in ptk.make_iterable(objects) if o]
        return sorted(
            objs,
            key=lambda o: (o.matrix_world.translation - ref).length,
            reverse=reverse,
        )

    @staticmethod
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim ``objects`` at a world-space point — mirror of ``mtk.aim_object_at_point`` (which uses
        an aimConstraint). ``aim_vect`` is the object axis pointed at the target; ``up_vect`` the axis
        kept upright. Location and scale are preserved. Returns the aimed objects."""
        from mathutils import Matrix

        target = _XformUtilsInternal._as_point(target_pos)
        track = _TRACK_AXIS.get(tuple(aim_vect), "X")
        up = _TRACK_AXIS.get(tuple(up_vect), "Y").lstrip("-")  # up token is unsigned
        aimed = []
        for o in (x for x in ptk.make_iterable(objects) if x):
            loc, _rot, scl = o.matrix_world.decompose()
            direction = target - loc
            if direction.length < 1e-9:
                continue
            quat = direction.normalized().to_track_quat(track, up)
            o.matrix_world = Matrix.LocRotScale(loc, quat, scl)
            aimed.append(o)
        return aimed

    @staticmethod
    def restore_original_axes(objects=None, prefix=_DEFAULT_BAKE_PREFIX, name="Authored"):
        """Point the transform gizmo at an object's PRE-FREEZE axes, without un-freezing —
        mirror of ``mtk.XformUtils.restore_original_axes``.

        The companion to Un-Freeze for when the freeze is wanted but the authored frame is
        still needed to work in: a frozen object's local axes are the world's, so the gizmo
        can no longer show the frame the asset was modelled in.

        Maya aims its manipulator pivot; Blender's equivalent knob is a **custom transform
        orientation** (the same slot ``World-Aligned Pivot`` flips to ``GLOBAL``), so this
        writes the authored frame into one named *name* and makes it active. Non-destructive:
        nothing about the object changes. With several objects the LAST wins — one gizmo.

        Returns the object the orientation was built from, or None (no bake history, or no
        screen context — ``create_orientation`` needs a window, so this no-ops headless).
        """
        import bpy

        objects = [o for o in ptk.make_iterable(objects) if o]
        if not objects:
            objects = list(CoreUtils.selected_objects())
        stamped = [
            o for o in objects if XformUtils.get_stored_transforms(o, prefix) is not None
        ]
        if not stamped:
            return None

        obj = stamped[-1]
        frame = XformUtils.get_operation_axis_matrix(obj, "original")
        try:
            with CoreUtils.window_context_override():
                bpy.context.view_layer.objects.active = obj
                bpy.ops.transform.create_orientation(
                    name=name, use=True, overwrite=True
                )
                slot = bpy.context.scene.transform_orientation_slots[0]
                slot.custom_orientation.matrix = frame.to_3x3()
        except (RuntimeError, AttributeError):
            # Headless / no screen: create_orientation has no context to run in.
            return None
        return obj

    @staticmethod
    def get_pivot_options():
        """Pivot keys understood by :func:`move_to` (mirror of ``mtk.XformUtils.get_pivot_options``)."""
        return ["center", "object"]
