# !/usr/bin/python
# coding=utf-8
"""Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.

The constraint / driver / handle / grouping helpers shared by the procedural rigs
(telescope / wheel / tube / shadow), **plus the armature/bone/Spline-IK/bind primitives** that
Maya carries as joint-chain / IK-handle / skinCluster machinery. Per the "relax the mirror where
concepts diverge" rule these are mapped to their Blender idioms — Maya **joints** → Armature
**bones**, ``ikSplineSolver`` IK handle → the **Spline IK bone constraint**, ``skinCluster`` →
**Armature-deform + automatic weights** — but they live here (shared) rather than buried in
``TubeRig``, because the tube strategies + any future bone rig genuinely share them (a deferred
"per-rig where needed" would just get re-implemented three times).

``import bpy`` is deferred into the call bodies, so importing this module / resolving the package
surface never needs a running Blender (matches the no-import-side-effects rule).
"""
from contextlib import contextmanager


class RigUtils:
    """Constraint / driver / handle / grouping / armature helpers shared by the procedural rigs."""

    # ----------------------------------------------------------------- resolution
    @staticmethod
    def resolve_object(obj):
        """An object or its name → the ``bpy`` object (``None`` if missing)."""
        import bpy

        if obj is None:
            return None
        return bpy.data.objects.get(obj) if isinstance(obj, str) else obj

    # ----------------------------------------------------------------- handles / grouping
    @staticmethod
    def create_locator(
        name="locator", location=(0, 0, 0), display_type="PLAIN_AXES", size=1.0, collection=None
    ):
        """Create an Empty — Blender's analogue of Maya's spaceLocator (a rig handle)."""
        import bpy

        loc = bpy.data.objects.new(name, None)
        loc.empty_display_type = display_type
        loc.empty_display_size = size
        loc.location = location
        (collection or bpy.context.collection).objects.link(loc)
        return loc

    @staticmethod
    def create_group(name="rig_grp", location=(0, 0, 0), children=None):
        """Create an Empty used as a transform group, parenting ``children`` under it (keeping
        each child's world transform). Mirror of mayatk's ``create_group``."""
        import bpy

        grp = RigUtils.create_locator(name, location, display_type="ARROWS")
        if children:
            # matrix_world is lazy: settle the just-created group before parent_keep_transform reads
            # its matrix_world, else a non-origin group binds an identity parent-inverse and the
            # children double their offset (the matrix_world-is-lazy gotcha).
            bpy.context.view_layer.update()
            for child in children:
                RigUtils.parent_keep_transform(child, grp)
        return grp

    @staticmethod
    def parent_keep_transform(child, parent):
        """Parent ``child`` to ``parent`` without moving it in world space (Maya ``parent`` default)."""
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
        return child

    # ----------------------------------------------------------------- armature / bones
    @staticmethod
    @contextmanager
    def _active_mode(obj, mode):
        """Temporarily make *obj* the active object in *mode* (``EDIT``/``POSE``/``OBJECT``),
        restoring the prior active object + OBJECT mode on exit. Bone editing and a few rig ops
        require the operator context's active object to be in the right mode; this scopes that
        switch so callers don't leak it (the headless ``--background`` interpreter still has a
        valid view layer under ``--factory-startup``)."""
        import bpy

        view_layer = bpy.context.view_layer
        prev_active = view_layer.objects.active
        # Operators need to start from OBJECT mode; settle whatever was active first.
        if prev_active is not None and getattr(prev_active, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode=mode)
        try:
            yield
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
            view_layer.objects.active = prev_active

    @staticmethod
    def create_armature(name="armature", location=(0, 0, 0), collection=None):
        """Create an empty Armature object (Maya's joint-chain container). Bones are added with
        :meth:`add_bone_chain`."""
        import bpy

        data = bpy.data.armatures.new(name)
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        (collection or bpy.context.collection).objects.link(obj)
        return obj

    @staticmethod
    def add_bone_chain(armature, points, prefix="bone", connect=True, radius=None):
        """Build a connected bone chain through world-space *points* — Maya's ``generate_joint_chain``
        analogue (head[i] = points[i], tail[i] = points[i+1], so N points → N-1 bones). Returns the
        ordered bone names. The points are converted into the armature's local space, so the chain
        sits on the centerline regardless of where the armature object is. *radius* (optional) sets
        each bone's ``head_radius``/``tail_radius`` — Maya's per-joint ``.radius`` (viewport display
        size, also reused as control scale)."""
        import bpy
        from mathutils import Vector

        mw_inv = armature.matrix_world.inverted()
        pts = [mw_inv @ Vector(p) for p in points]
        names = []
        with RigUtils._active_mode(armature, "EDIT"):
            ebones = armature.data.edit_bones
            prev = None
            for i in range(len(pts) - 1):
                b = ebones.new(f"{prefix}_{i:02d}")
                b.head = pts[i]
                b.tail = pts[i + 1]
                if radius is not None:
                    b.head_radius = b.tail_radius = float(radius)
                if prev is not None:
                    b.parent = prev
                    b.use_connect = connect
                prev = b
                names.append(b.name)
        return names

    @staticmethod
    def add_bone(armature, name, head, tail, parent=None, connect=False, radius=None, deform=True):
        """Add ONE bone to an existing armature at world-space *head*/*tail* — the single-bone
        analogue of :meth:`add_bone_chain`, for anchor/helper bones grafted onto an already-built
        rig. *parent* is an existing bone name to parent under (``connect`` snaps this bone's head to
        it). ``deform`` flags it for Armature-deform (``use_deform``). Points are converted to the
        armature's local space (like :meth:`add_bone_chain`). Returns the created bone name — Blender
        uniquifies collisions, so use the return value (e.g. as the deform vertex-group name)."""
        import bpy
        from mathutils import Vector

        mw_inv = armature.matrix_world.inverted()
        with RigUtils._active_mode(armature, "EDIT"):
            ebones = armature.data.edit_bones
            b = ebones.new(name)
            b.head = mw_inv @ Vector(head)
            b.tail = mw_inv @ Vector(tail)
            if radius is not None:
                b.head_radius = b.tail_radius = float(radius)
            if parent is not None:
                b.parent = ebones.get(parent)
                b.use_connect = connect
            b.use_deform = deform
            created = b.name
        return created

    @staticmethod
    def get_bone_chain_from_root(armature, bone_name=None, reverse=False):
        """Walk a single-path bone chain from a root bone — mirror of mayatk's
        ``RigUtils.get_joint_chain_from_root``. *bone_name* defaults to the armature's active bone
        (``data.bones.active``, set by the last bone clicked in Pose/Edit mode and persisted into
        Object mode — so the user doesn't need to stay in Pose mode to also select the mesh),
        falling back to the first parentless bone (a simple chain has exactly one). Descends via
        each bone's FIRST child only, same single-path assumption Maya's version makes — a
        branching skeleton just follows one side. Returns an ordered list of bone names."""
        bones = armature.data.bones
        root = bones.get(bone_name) if bone_name else bones.active
        if root is None:
            root = next((b for b in bones if b.parent is None), None)
        if root is None:
            return []
        chain = []
        b = root
        while b is not None:
            chain.append(b.name)
            b = b.children[0] if b.children else None
        if reverse:
            chain.reverse()
        return chain

    @staticmethod
    def invert_bone_chain(armature, bone_names):
        """Rebuild *bone_names* (head->tail order) with reversed hierarchy — mirror of mayatk's
        ``RigUtils.invert_joint_chain`` (always destructive/``keep_original=False``; the granular
        tube-rig step-workflow's only call site never keeps the original). Reads each bone's world
        head/tail/radius first, then replaces the whole chain end->start in one EDIT-mode pass so
        the new first bone sits at the old chain's END. Returns the new ordered bone names."""
        mw = armature.matrix_world
        mw_inv = mw.inverted()
        data_bones = armature.data.bones
        heads_tails = [
            (mw @ data_bones[n].head_local, mw @ data_bones[n].tail_local,
             data_bones[n].head_radius, data_bones[n].tail_radius)
            for n in bone_names
        ]
        # Old chain end's tail becomes the new chain's first head; walk the rest in reverse.
        new_points = [heads_tails[-1][1]] + [ht[0] for ht in reversed(heads_tails)]
        radii = [heads_tails[-1][3]] + [ht[2] for ht in reversed(heads_tails)]
        prefix = bone_names[0].rsplit("_", 1)[0] if bone_names else "bone"

        with RigUtils._active_mode(armature, "EDIT"):
            ebones = armature.data.edit_bones
            for n in bone_names:
                eb = ebones.get(n)
                if eb is not None:
                    ebones.remove(eb)
            prev = None
            new_names = []
            for i in range(len(new_points) - 1):
                b = ebones.new(f"{prefix}_{i:02d}")
                b.head = mw_inv @ new_points[i]
                b.tail = mw_inv @ new_points[i + 1]
                b.head_radius = radii[i]
                b.tail_radius = radii[i + 1] if i + 1 < len(radii) else radii[i]
                if prev is not None:
                    b.parent = prev
                    b.use_connect = True
                prev = b
                new_names.append(b.name)
        return new_names

    @staticmethod
    def add_bone_constraint(armature, bone_name, ctype, target=None, subtarget=None, **props):
        """Add a **pose-bone** constraint (``ctype`` e.g. ``COPY_LOCATION`` / ``STRETCH_TO`` /
        ``DAMPED_TRACK`` / ``SPLINE_IK`` / ``COPY_TRANSFORMS``) to *bone_name*, optionally targeting
        *target* (object) + *subtarget* (a bone name on it). Extra props pass through via ``setattr``.
        Pose bones exist in OBJECT mode, so no mode switch is needed. The bone-level analogue of
        :meth:`_constraint`."""
        c = armature.pose.bones[bone_name].constraints.new(ctype)
        if target is not None:
            c.target = target
            if subtarget is not None:
                c.subtarget = subtarget
        for k, v in props.items():
            setattr(c, k, v)
        return c

    @staticmethod
    def add_spline_ik(armature, bone_name, curve, chain_count, name="Spline IK", **props):
        """Add a **Spline IK** bone constraint to pose bone *bone_name* so *chain_count* bones up the
        chain fit to *curve* — the faithful analogue of Maya's ``ikSplineSolver`` IK handle. Extra
        constraint props (``y_scale_mode``, ``xz_scale_mode``, ``use_curve_radius`` …) pass through."""
        return RigUtils.add_bone_constraint(
            armature, bone_name, "SPLINE_IK", target=curve,
            name=name, chain_count=int(chain_count), **props,
        )

    @staticmethod
    def bind_armature(mesh, armature, auto_weights=True):
        """Bind *mesh* to *armature* (Maya ``skinCluster`` analogue). ``auto_weights`` uses Blender's
        automatic-weights parenting (creates the Armature modifier + bone vertex groups); otherwise
        only the Armature modifier is added (caller supplies weights). Returns the Armature modifier."""
        import bpy

        if auto_weights:
            with RigUtils._active_mode(armature, "OBJECT"):
                bpy.ops.object.select_all(action="DESELECT")
                mesh.select_set(True)
                armature.select_set(True)
                bpy.context.view_layer.objects.active = armature
                bpy.ops.object.parent_set(type="ARMATURE_AUTO")
            return next((m for m in mesh.modifiers if m.type == "ARMATURE"), None)
        mod = mesh.modifiers.new("Armature", "ARMATURE")
        mod.object = armature
        return mod

    @staticmethod
    def apply_falloff_weights(mesh, group_name, center, radius, profile="linear", add_group=True):
        """Distance-falloff vertex weights — the Blender (vertex-group) analogue of mayatk's
        ``SkinUtils.apply_falloff`` (skinCluster ``skinPercent``). Every *mesh* vertex within
        *radius* world units of *center* gets ``group_name`` weight ``w = 1 - d/radius``
        (``profile='linear'``) or a smoothstep (``'smooth'``), and its EXISTING group weights are
        scaled by ``(1 - w)`` so the row stays normalized — the group claims ``w`` and the remainder
        REDISTRIBUTES across the vertex's current influences (Maya's ``skinPercent`` semantics), not
        stolen from one bone. Vertices at/beyond the radius are left untouched (``w -> 0`` is
        continuous at the boundary → no crease). Deforms via the Armature modifier because the group
        name matches the (deform) bone name. Returns the vertex group (or ``None`` if absent and
        ``add_group=False``). *center* is any 3-sequence; *radius* is clamped away from zero.

        Only groups matching a DEFORM bone of the mesh's Armature modifier(s) are rescaled —
        Maya's ``skinPercent`` touches skinCluster influences only, and scaling every vertex
        group would corrupt non-skin data (cloth pin groups, selection sets, shape-key masks)."""
        from mathutils import Vector

        vg = mesh.vertex_groups.get(group_name)
        if vg is None:
            if not add_group:
                return None
            vg = mesh.vertex_groups.new(name=group_name)
        # Influence set = deform-bone groups of the mesh's armature(s) — the
        # Blender equivalent of the skinCluster influence list.
        deform_names = set()
        for m in mesh.modifiers:
            if m.type == "ARMATURE" and m.object is not None:
                deform_names.update(b.name for b in m.object.data.bones if b.use_deform)
        groups = mesh.vertex_groups
        influence_indices = {
            g.index for g in groups if g.name in deform_names and g.index != vg.index
        }
        center = Vector(center)
        mw = mesh.matrix_world
        r = max(float(radius), 1e-6)
        smooth = profile == "smooth"
        for v in mesh.data.vertices:
            d = (mw @ v.co - center).length
            if d >= r:
                continue
            t = d / r
            w = 1.0 - t * t * (3.0 - 2.0 * t) if smooth else 1.0 - t
            # snapshot the vertex's other INFLUENCES before mutating (add() invalidates v.groups)
            others = [(g.group, g.weight) for g in v.groups if g.group in influence_indices]
            scale = 1.0 - w
            for gi, gw in others:
                groups[gi].add([v.index], gw * scale, "REPLACE")
            vg.add([v.index], w, "REPLACE")
        return vg

    # ----------------------------------------------------------------- constraints
    @staticmethod
    def _constraint(obj, ctype, target=None, **props):
        c = obj.constraints.new(ctype)
        if target is not None:
            c.target = target
        for k, v in props.items():
            setattr(c, k, v)
        return c

    @staticmethod
    def copy_location(obj, target, influence=1.0):
        """Maya pointConstraint → COPY_LOCATION. Stack two (influence 1, then ``frac``) for a lerp."""
        return RigUtils._constraint(obj, "COPY_LOCATION", target, influence=influence)

    @staticmethod
    def copy_rotation(obj, target, influence=1.0):
        """Maya orientConstraint → COPY_ROTATION."""
        return RigUtils._constraint(obj, "COPY_ROTATION", target, influence=influence)

    @staticmethod
    def damped_track(obj, target, track_axis="TRACK_Y"):
        """Single-axis aim (Maya aimConstraint, no up-vector) → DAMPED_TRACK."""
        return RigUtils._constraint(obj, "DAMPED_TRACK", target, track_axis=track_axis)

    @staticmethod
    def track_to(obj, target, track_axis="TRACK_Y", up_axis="UP_Z"):
        """Aim with an up-vector (full Maya aimConstraint) → TRACK_TO."""
        return RigUtils._constraint(obj, "TRACK_TO", target, track_axis=track_axis, up_axis=up_axis)

    @staticmethod
    def child_of(obj, target, set_inverse=True):
        """Maya parentConstraint(maintainOffset=True) → CHILD_OF (inverse bound at the current pose)."""
        c = RigUtils._constraint(obj, "CHILD_OF", target)
        if set_inverse:
            c.inverse_matrix = target.matrix_world.inverted()
        return c

    # ----------------------------------------------------------------- drivers
    @staticmethod
    def _driver_add(obj, data_path, index):
        return obj.driver_add(data_path, index) if index is not None else obj.driver_add(data_path)

    @staticmethod
    def refresh_drivers(objects):
        """Force-recompile every driver on ``objects`` — call ONCE after building a rig's drivers.

        A *script-built* driver caches a stale compile (it first evaluates with an incomplete
        variable set → wrong/0 result, sometimes a 'Math Domain Error'). The reliable trigger is to
        let the depsgraph settle the new drivers/relations (``view_layer.update()``), THEN re-assign
        each expression to force a recompile against the now-complete variable set.
        """
        import bpy

        bpy.context.view_layer.update()  # settle new drivers + relations first
        for obj in objects:
            ad = getattr(obj, "animation_data", None)
            for d in (ad.drivers if ad else ()):
                d.driver.expression = d.driver.expression  # re-assign -> recompile

    @staticmethod
    def add_distance_driver(obj, data_path, index, a, b, expression="dist", var_name="dist"):
        """Drive ``obj.<data_path>[index]`` from the live distance between objects ``a`` and ``b``
        (a ``LOC_DIFF`` variable named ``var_name``). Replaces a Maya ``distanceBetween`` + driven
        key. ``expression`` is evaluated with that variable in scope (default just the distance).

        Call :meth:`refresh_drivers` once after building all of a rig's drivers — script-built
        drivers don't compile correctly until then (see that method)."""
        fc = RigUtils._driver_add(obj, data_path, index)
        drv = fc.driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = var_name
        var.type = "LOC_DIFF"
        var.targets[0].id = a
        var.targets[1].id = b
        fc.driver.expression = expression
        return fc

    @staticmethod
    def add_transform_driver(
        obj, data_path, index, target, transform_type,
        space="WORLD_SPACE", expression=None, var_name="var",
    ):
        """Drive ``obj.<data_path>[index]`` from a single transform channel of ``target`` (a
        ``TRANSFORMS`` variable). E.g. an auto-rolling wheel: rotation ← its own travel.

        Returns the fcurve so extra variables can be appended (see ``add_prop_var``) before the
        ``expression`` references them. Call :meth:`refresh_drivers` once after building all of a
        rig's drivers — script-built drivers don't compile correctly until then."""
        fc = RigUtils._driver_add(obj, data_path, index)
        drv = fc.driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = var_name
        var.type = "TRANSFORMS"  # API enum; the UI labels this "Transform Channel"
        t = var.targets[0]
        t.id = target
        t.transform_type = transform_type
        t.transform_space = space
        fc.driver.expression = expression if expression is not None else var_name
        return fc

    @staticmethod
    def add_prop_var(fcurve, name, id_obj, data_path, id_type=None):
        """Append a ``SINGLE_PROP`` variable to an existing driver fcurve — e.g. a control's keyable
        custom property (``data_path='["wheelHeight"]'``) the driver expression reads live.

        ``id_type`` defaults to ``None``, which leaves ``DriverTarget.id_type`` at its RNA
        default (``'OBJECT'``) — the common case for every existing caller (rig controls are
        always Objects). Pass an explicit RNA id_type enum (e.g. ``'KEY'``) when ``id_obj`` is a
        non-Object ID datablock (a mesh's ``shape_keys``, a material node tree, …) — Blender
        rejects the assignment otherwise (``DriverTarget.id`` type-checks against ``id_type``).
        """
        var = fcurve.driver.variables.new()
        var.name = name
        var.type = "SINGLE_PROP"
        if id_type is not None:
            var.targets[0].id_type = id_type
        var.targets[0].id = id_obj
        var.targets[0].data_path = data_path
        return var

    @staticmethod
    def add_transform_var(
        fcurve, name, target, transform_type, space="WORLD_SPACE"
    ):
        """Append a ``TRANSFORMS`` variable (a single transform channel of *target*) to an existing
        driver fcurve — the multi-input companion to :meth:`add_prop_var` for rigs whose driver
        expression reads several world-space channels (e.g. the shadow rig reading a light's +
        contact's world position). ``add_transform_driver`` seeds the first such var; this appends
        the rest."""
        var = fcurve.driver.variables.new()
        var.name = name
        var.type = "TRANSFORMS"  # API enum; the UI labels this "Transform Channel"
        t = var.targets[0]
        t.id = target
        t.transform_type = transform_type
        t.transform_space = space
        return var

    @staticmethod
    def ensure_custom_prop(obj, name, value, min_value=None, max_value=None):
        """Set a keyable custom property (Maya's ``addAttr`` analogue), creating it if absent and
        configuring soft UI limits. Existing values are preserved; only missing props are seeded."""
        if name not in obj:
            obj[name] = value
        try:
            ui = obj.id_properties_ui(name)
            kw = {}
            if min_value is not None:
                kw["min"] = min_value
                kw["soft_min"] = min_value
            if max_value is not None:
                kw["max"] = max_value
                kw["soft_max"] = max_value
            if kw:
                ui.update(**kw)
        except (AttributeError, TypeError):
            pass
        return obj[name]

    @staticmethod
    def remove_driver(obj, data_path, index=None):
        """Remove a driver on ``obj.<data_path>[index]`` if present (idempotent; for re-entrant rigs)."""
        try:
            if index is not None:
                obj.driver_remove(data_path, index)
            else:
                obj.driver_remove(data_path)
        except (TypeError, RuntimeError):
            pass

    # ----------------------------------------------------------------- channels
    @staticmethod
    def lock_channels(obj, location=None, rotation=None, scale=None):
        """Lock the given transform channels (each a 3-tuple of bools, or ``None`` to leave as-is)."""
        if location is not None:
            obj.lock_location = location
        if rotation is not None:
            obj.lock_rotation = rotation
        if scale is not None:
            obj.lock_scale = scale
        return obj
