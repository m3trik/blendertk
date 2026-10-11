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
        name="locator",
        location=(0, 0, 0),
        display_type="PLAIN_AXES",
        size=1.0,
        collection=None,
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
        each child's world transform). Mirror of mayatk's ``create_group``: typed a Maya
        group (``NodeUtils.set_maya_node_type``), so even childless it is no locator to
        selection, naming or the Maya send; its arrows stay as the handle."""
        import bpy

        from blendertk.node_utils._node_utils import NodeUtils

        grp = RigUtils.create_locator(name, location, display_type="ARROWS")
        NodeUtils.set_maya_node_type(grp, "group", look=False)
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

    @staticmethod
    def _take_parent_link(obj, source):
        """Give *obj* the parent link *source* has: the parent, its type with
        the bone / vertices it names, and the parent-inverse -- so *obj*, given
        *source*'s channels, evaluates exactly where *source* does, on a bone or
        vertex parent too. No parent leaves *obj* in the world.

        Parameters:
            obj (bpy.types.Object): The object to re-link.
            source (bpy.types.Object): The object whose link it takes.
        """
        obj.parent = source.parent  # every link write resets the inverse: it goes last
        if source.parent is not None:
            obj.parent_type = source.parent_type
            obj.parent_bone = source.parent_bone
            obj.parent_vertices = source.parent_vertices[:]
            obj.matrix_parent_inverse = source.matrix_parent_inverse.copy()

    # ----------------------------------------------------------------- locator rigs
    #: The custom property :meth:`create_locator_at_object` stamps on the two Empties it
    #: builds ("locator" / "group"): RIG membership, which tells :meth:`remove_locator`
    #: a rig's own group from any other group. (What Maya node each Empty stands for is
    #: :attr:`NodeUtils.MAYA_NODE_TYPE_PROP`, which the bridge reads.) The Scene
    #: Exporter exports custom properties, so both ride along as FBX user properties,
    #: which unitytk's importers skip (they look up the keys they own).
    LOCATOR_RIG_PROP = "btk_locator_rig"

    #: A ring-drawn rig group's radius, in multiples of its locator's display size
    #: (``create_locator_at_object(group_display="ring")``): the circle clears the
    #: tips of the locator's axes, so it draws nothing at the centre a click on the
    #: locator lands on.
    LOCATOR_RING_SCALE = 1.5

    @staticmethod
    def _locator_rig_role(obj):
        """``"locator"`` / ``"group"`` for an Empty of a locator rig, else ``None``.

        A rig is known by the stamp :meth:`create_locator_at_object` writes; one
        built before the stamp, by its names -- a ``<stem><locator>`` Empty under
        the ``<stem><group>`` Empty, in the naming convention's affixes (Blender's
        ``.001`` clash suffix ignored). Any Empty stamped a Maya locator is a
        ``"locator"`` too -- one the Maya pull brought in, rig or not -- as
        mayatk's :meth:`remove_locator` takes any locator.

        Parameters:
            obj (bpy.types.Object/None): The object to classify.

        Returns:
            (str/None): ``"locator"``, ``"group"`` or ``None``.
        """
        import re

        import pythontk as ptk

        from blendertk.node_utils._node_utils import NodeUtils

        if obj is None or obj.type != "EMPTY":
            return None
        stamp = obj.get(RigUtils.LOCATOR_RIG_PROP)
        if stamp in ("locator", "group"):
            return stamp
        if obj.get(NodeUtils.MAYA_NODE_TYPE_PROP) == "locator":
            return "locator"
        loc_rule = ptk.NamingConvention.get("locator")
        grp_rule = ptk.NamingConvention.get("group")

        def stem(o, rule):
            name = re.sub(r"\.\d{3}$", "", o.name)
            if not rule.text or not rule.matches(name):
                return None
            prefix, suffix = rule.parts()
            return name[len(prefix) : len(name) - len(suffix)].casefold()

        def legacy_locator(o):
            grp = o.parent
            if grp is None or grp.type != "EMPTY":
                return False
            loc_stem = stem(o, loc_rule)
            return loc_stem is not None and loc_stem == stem(grp, grp_rule)

        if legacy_locator(obj):
            return "locator"
        if any(c.type == "EMPTY" and legacy_locator(c) for c in obj.children):
            return "group"
        return None

    @staticmethod
    def create_locator_at_object(
        objects,
        loc_scale=1.0,
        lock_translate=False,
        lock_rotation=False,
        lock_scale=False,
        grp_suffix=None,
        grp_affix_mode="auto",
        loc_suffix=None,
        loc_affix_mode="auto",
        obj_suffix=None,
        obj_affix_mode="auto",
        strip_digits=False,
        strip_trailing_underscores=True,
        strip_suffix=True,
        rotate_order="xyz",
        freeze_object=True,
        group_display="none",
    ):
        """Rig each object under a zeroed locator (Empty) at its origin, under a group Empty.

        Mirror of mayatk's ``RigUtils.create_locator_at_object`` (name + behavior), down
        to the channels: ``<base><grp>`` -> ``<base><loc>`` -> the object renamed
        ``<base><obj>``, locked as asked. The three names share one stem, stripped of
        the affixes in play (and of trailing digits / underscores when asked); nothing
        moves.

        - The group takes the object's place -- its parent link (object, bone or
          vertices) and parent-inverse -- and holds its pose at unit scale, as
          mayatk's group holds the object's translate / rotate. So it keeps following
          a posed bone, and the locator under it is never squashed by the object's
          scale. It draws nothing, as a Maya group
          (:attr:`NodeUtils.MAYA_GROUP_DISPLAY_SIZE`) -- or, as asked, a ring around
          the locator (*group_display*).
        - The locator sits on the group ZEROED -- identity channels and inverse -- so
          clearing it puts the rig back at rest, and draws at *loc_scale*.
        - The object re-links to the locator as a plain OBJECT child, frozen: its
          channels zero and its parent-inverse takes what they held (the scale the
          group does not), where Maya bakes it into the shape -- so no data changes
          and :meth:`remove_locator` hands every channel back. An object whose
          transform something writes (keys, an NLA strip, drivers, a constraint)
          keeps its channels, the inverse cancelling the pose the group took, so its
          animation plays as before -- where mayatk's freeze plays the keys offset by
          that pose.

        A group -- an Empty standing for a Maya group (:meth:`NodeUtils.is_group`) --
        gets its rig on the world bounding-box centre of its contents, turned like the
        group, as mayatk's, and is never renamed. The locator and group carry the Maya
        node type they stand for (:attr:`NodeUtils.MAYA_NODE_TYPE_PROP`), so the Maya
        send restores a locator and a group, and :attr:`LOCATOR_RIG_PROP`, which is how
        :meth:`remove_locator` knows a rig's group from the user's own. The new
        locators end up selected, as mayatk leaves them.

        Parameters:
            objects (obj/list): The objects (or names) to rig; unknown names skip.
            loc_scale (float): The locator's display size.
            lock_translate, lock_rotation, lock_scale (bool): Lock (or, False,
                unlock) the object's location / rotation / scale channels.
            grp_suffix, loc_suffix (str): Affix for the group / locator. ``None``
                takes the shared naming convention's ``group`` / ``locator`` entry
                ("_GRP" / "_LOC" as shipped) -- spelling AND placement.
            grp_affix_mode, loc_affix_mode (str): Placement of an explicit affix:
                "auto" (infer from the delimiter), "suffix" or "prefix".
            obj_suffix (str): Affix for the object. ``None`` resolves it per object
                from its OWN type (a mesh "_GEO", a camera "_CAM") via
                ``Naming.affix_for``, and strips whichever convention affix the
                name already carries; "" leaves the stem unaffixed. A group (an
                Empty with children) is never renamed -- the group Empty already
                holds that stem.
            obj_affix_mode (str): Placement of an explicit *obj_suffix*.
            strip_digits (bool): Strip trailing digits from the stem.
            strip_trailing_underscores (bool): Strip trailing underscores from it.
            strip_suffix (bool): Strip the affixes in play from it first.
            rotate_order (str): The locator's Euler order, Maya-spelled ("xyz",
                "zyx", ...; Blender's ``rotation_mode`` of the same letters
                applies the axes in the same sequence). Its FIRST letter is the
                local axis that spins through a single channel at any pose.
            freeze_object (bool): Freeze the object under the locator (mayatk's
                flag): zero its channels, its parent-inverse taking what they held.
                False keeps them, cancelled by the parent-inverse. Driven channels
                are kept either way.
            group_display (str): How the group draws (Blender only: a Maya group
                has no display). "none" -- nothing, as a Maya group, though a
                repeat click on the locator's centre cycles to it, as Blender cycles
                every object under a click. "ring" -- a circle around the locator
                (:attr:`LOCATOR_RING_SCALE` times its size): it marks the rig, gives
                the group a handle, and leaves the centre to the locator. Either
                way the group exports, and goes to Maya, as a group.

        Returns:
            (list): The created locator Empties, in input order.

        Raises:
            ValueError: *rotate_order* is not an Euler order, or *group_display*
                not "none" / "ring".
        """
        import re

        import bpy
        import pythontk as ptk
        from mathutils import Matrix

        import blendertk as btk
        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.edit_utils.naming._naming import Naming
        from blendertk.node_utils._node_utils import NodeUtils

        rotation_mode = str(rotate_order).upper()
        if sorted(rotation_mode) != ["X", "Y", "Z"]:
            raise ValueError(f"Invalid rotate order {rotate_order!r}.")
        if group_display not in ("none", "ring"):
            raise ValueError(f"Invalid group display {group_display!r}.")

        def rule(affix, mode, key):
            if affix is None:
                return ptk.NamingConvention.get(key)
            return ptk.AffixRule(affix, mode)

        grp_rule = rule(grp_suffix, grp_affix_mode, "group")
        loc_rule = rule(loc_suffix, loc_affix_mode, "locator")
        by_type = obj_suffix is None
        obj_rule = None if by_type else ptk.AffixRule(obj_suffix, obj_affix_mode)
        # By type, a name may carry the affix of a type it is not (a camera an
        # earlier run wrote as "_GEO"), so the whole convention vocabulary is
        # strippable. Longest first: "_SG" would otherwise eat the tail of "_LSG".
        strip = {grp_rule.text, loc_rule.text}
        strip |= set(ptk.NamingConvention.all_affixes()) if by_type else {obj_rule.text}
        strip = tuple(sorted((a for a in strip if a), key=lambda a: (-len(a), a)))

        def stem(name):
            base = ptk.format_suffix(
                name,
                suffix="",
                strip=strip if strip_suffix else (),
                strip_trailing_ints=strip_digits,
            )
            if strip_trailing_underscores:
                base = re.sub(r"_+$", "", base)
            return base or name  # the affixes consumed the whole name: keep it

        if not isinstance(objects, (list, tuple, set)):
            objects = [objects]
        # One rig per object, in input order (a dict keeps first-seen order).
        resolved = map(RigUtils.resolve_object, objects)
        targets = list({o.as_pointer(): o for o in resolved if o is not None}.values())
        groups = [NodeUtils.is_group(o) for o in targets]
        if any(groups):
            # A group's rig sits on its contents, so their worlds must be current
            # (a fresh object's matrix_world is identity until a depsgraph update).
            bpy.context.view_layer.update()
        # Every pose is read before anything is rigged: a rig built for one object
        # adds Empties that a later group's contents would count, unevaluated.
        poses = [RigUtils._locator_rig_pose(o, g) for o, g in zip(targets, groups)]

        locators = []
        for o, is_group, pose in zip(targets, groups, poses):
            coll = (
                o.users_collection[0]
                if o.users_collection
                else bpy.context.scene.collection
            )
            base = stem(o.name)
            if not is_group:
                # Classified before anything moves, and renamed FIRST: Blender
                # names are global, and the locator may want the very name the
                # object gives up (an empty locator affix names it the bare stem).
                child_rule = Naming.affix_for(o) if by_type else obj_rule
                if by_type and not child_rule.text:
                    # An unmapped type has no entry to follow; keep the mesh
                    # affix over a bare rename.
                    child_rule = ptk.NamingConvention.get("mesh")
                # .apply honours placement and is idempotent.
                new_name = child_rule.apply(base)
                if new_name and o.name != new_name:
                    o.name = new_name
            # Blender's world is parent.world @ matrix_parent_inverse @
            # matrix_basis, so everything below is solved against the BASIS: the
            # group shares the object's link and inverse, and its channels act
            # where the object's did.
            channels = o.matrix_basis.copy()

            # The group takes the object's place -- link and inverse -- and holds
            # its pose, so a bone-parented prop keeps following its bone.
            grp = bpy.data.objects.new(grp_rule.apply(base), None)
            if group_display == "ring":
                # The stamp alone keeps a CIRCLE a group: the send reads an
                # unstamped one as a deliberate locator.
                grp.empty_display_type = "CIRCLE"
                grp.empty_display_size = loc_scale * RigUtils.LOCATOR_RING_SCALE
                NodeUtils.set_maya_node_type(grp, "group", look=False)
            else:
                grp.empty_display_type = "PLAIN_AXES"
                NodeUtils.set_maya_node_type(grp, "group")
            grp[RigUtils.LOCATOR_RIG_PROP] = "group"
            coll.objects.link(grp)
            RigUtils._take_parent_link(grp, o)
            grp.matrix_basis = pose
            pose = grp.matrix_basis.copy()  # exactly as its channels hold it

            # The locator sits on the group ZEROED: a new object's channels and
            # inverse are identity, and the parent write keeps them so.
            loc = bpy.data.objects.new(loc_rule.apply(base), None)
            loc.empty_display_type = "PLAIN_AXES"
            loc.empty_display_size = loc_scale
            loc.rotation_mode = rotation_mode
            NodeUtils.set_maya_node_type(loc, "locator")
            loc[RigUtils.LOCATOR_RIG_PROP] = "locator"
            coll.objects.link(loc)
            loc.parent = grp

            # The object re-links to the locator as a plain OBJECT child (a bone or
            # vertex link means nothing under an Empty; the inverse goes last --
            # every link write resets it). Driven channels ARE the animation, so
            # they stay, the inverse cancelling the pose the group took.
            o.parent = loc
            o.parent_type = "OBJECT"
            o.parent_bone = ""
            if freeze_object and not NodeUtils._transforms_driven(o):
                o.matrix_parent_inverse = pose.inverted_safe() @ channels
                # matrix_basis counts the deltas, so they zero with the rest.
                o.delta_location = (0.0, 0.0, 0.0)
                o.delta_rotation_euler = (0.0, 0.0, 0.0)
                o.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                o.delta_scale = (1.0, 1.0, 1.0)
                o.matrix_basis = Matrix.Identity(4)
            else:
                o.matrix_parent_inverse = pose.inverted_safe()
            o.lock_location = (lock_translate,) * 3
            o.lock_rotation = (lock_rotation,) * 3
            o.lock_scale = (lock_scale,) * 3
            locators.append(loc)
        if locators:  # nothing rigged leaves the selection alone, as mayatk's
            # The windowless context's view layer is the scene's default, not the
            # window's: select inside the override.
            with CoreUtils.window_context_override():
                btk.Selection._apply_selection_mode(locators, "replace")
        return locators

    @staticmethod
    def _locator_rig_pose(obj, is_group):
        """The channels a locator rig's group takes for *obj*: its pose, never its scale.

        The group shares *obj*'s parent link and inverse, so these act where *obj*'s
        own channels do: their location and rotation, at unit scale. A group
        (*is_group*) gets its rig on the world bounding-box centre of its contents
        instead, held in that same space -- mayatk's group placement; one with nothing
        under it keeps its own origin.

        Parameters:
            obj (bpy.types.Object): The object to rig.
            is_group (bool): Whether *obj* stands for a Maya group.

        Returns:
            (mathutils.Matrix): The group's ``matrix_basis``.
        """
        from mathutils import Matrix, Vector

        from blendertk.xform_utils._xform_utils import XformUtils

        channels = obj.matrix_basis
        location, rotation, _scale = channels.decompose()
        contents = list(obj.children_recursive) if is_group else []
        if contents:
            centre = Vector(XformUtils.get_bounding_box(contents, "center"))
            # The space the channels act in: the world they evaluate to, less them.
            space = obj.matrix_world @ channels.inverted_safe()
            location = space.inverted_safe() @ centre
        return Matrix.LocRotScale(location, rotation, None)

    @staticmethod
    def remove_locator(objects):
        """Dissolve locator rigs: the inverse of :meth:`create_locator_at_object`.

        Mirror of mayatk's ``RigUtils.remove_locator``: each locator in *objects*
        is removed after its children -- their channels unlocked -- go back where
        the rig took them from, world transforms intact (a bare delete pops them
        back to their raw local matrix). They take the parent link of the
        locator's group, landing under the group's parent (mayatk's grandparent)
        -- on its bone, for a bone-parented prop -- or in the world for a rig at
        the scene root. The group goes with its locator when that leaves it
        childless.

        Blender has no locator shape, so a locator is an Empty of a locator RIG, or
        any Empty stamped a Maya locator -- one the Maya pull brought in -- as
        mayatk takes any locator (:meth:`_locator_rig_role`); any other Empty -- a
        user's own group -- is skipped, as mayatk skips anything that is not a
        locator. A rig's group names its rig, so selecting it (in the outliner: it
        draws nothing) dissolves the rig. Only a rig's own group is ever skipped
        over or deleted -- a locator the user moved under their own Empty hands its
        children to that Empty.

        Parameters:
            objects (obj/list): The locators or their groups (or their names).

        Returns:
            (list): The names of the locators removed (empty when no locator rig
            was given -- the caller's cue to say so).
        """
        import bpy

        role = RigUtils._locator_rig_role
        if not isinstance(objects, (list, tuple, set)):
            objects = [objects]
        locators = []
        for o in (RigUtils.resolve_object(x) for x in objects):
            kind = role(o)
            if kind == "locator":
                found = [o]
            elif kind == "group":
                found = [c for c in o.children if role(c) == "locator"]
            else:
                found = []
            locators.extend(loc for loc in found if loc not in locators)

        def group_of(loc):
            """The rig's own group, while the locator still sits in one."""
            return loc.parent if role(loc.parent) == "group" else None

        if locators:
            # The worlds read below must be current: a rig built earlier in the
            # same script has not been evaluated yet.
            bpy.context.view_layer.update()
        for loc in locators:
            # The link the children take: the rig's group's (or the locator's own,
            # when its group is gone), then on past any LISTED locator and its
            # group, dissolved too -- so no child is handed to an Empty about to
            # be deleted.
            host = group_of(loc) or loc
            while host.parent is not None and host.parent in locators:
                host = group_of(host.parent) or host.parent
            for child in list(loc.children):
                if child in locators:
                    continue  # a listed locator dies anyway -- leave it parented
                child.lock_location = (False,) * 3
                child.lock_rotation = (False,) * 3
                child.lock_scale = (False,) * 3
                world = child.matrix_world.copy()
                RigUtils._take_parent_link(child, host)
                if child.parent is None:
                    child.matrix_basis = world
                else:
                    # The channels that hold *world* under the host's link,
                    # which carries the host's channels to the host's world
                    # (the rig's Empties have no constraints of their own).
                    # Solved here, not by the matrix_world setter: that cannot
                    # place a VERTEX parent (the original mesh has no evaluated
                    # verts).
                    child.matrix_basis = (
                        host.matrix_basis @ host.matrix_world.inverted_safe() @ world
                    )
        # The rigs' groups, collected before the deletes below invalidate the
        # locators' references.
        groups = []
        for loc in locators:
            grp = group_of(loc)
            if grp is not None and grp not in groups:
                groups.append(grp)
        removed = [loc.name for loc in locators]
        for loc in locators:
            bpy.data.objects.remove(loc, do_unlink=True)
        for grp in groups:
            if not grp.children:  # only a group left childless goes
                bpy.data.objects.remove(grp, do_unlink=True)
        return removed

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

        from blendertk.core_utils._core_utils import CoreUtils

        # The window's view layer + context for the whole scope, the caller's body
        # included: windowless, mode_set poll-fails and the active / select_set
        # writes address the scene's default layer, not the one the window shows.
        with CoreUtils.window_context_override():
            view_layer = bpy.context.view_layer
            prev_active = view_layer.objects.active
            # Operators need to start from OBJECT mode; settle whatever was active first.
            if (
                prev_active is not None
                and getattr(prev_active, "mode", "OBJECT") != "OBJECT"
            ):
                bpy.ops.object.mode_set(mode="OBJECT")
            # ``mode_set`` refuses a hidden object outright ("Cannot edit hidden object"),
            # and hiding is not an opinion a rig edit should have: a Maya pull imports a
            # skeleton whose visibility is ANIMATED, so at the import frame its armature
            # is routinely hidden and every edit-mode primitive here would fail on it.
            with CoreUtils.visible_override(obj):
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
    def add_bone(
        armature, name, head, tail, parent=None, connect=False, radius=None, deform=True
    ):
        """Add ONE bone to an existing armature at world-space *head*/*tail* — the single-bone
        analogue of :meth:`add_bone_chain`, for anchor/helper bones grafted onto an already-built
        rig. *parent* is an existing bone name to parent under (``connect`` snaps this bone's head to
        it). ``deform`` flags it for Armature-deform (``use_deform``). Points are converted to the
        armature's local space (like :meth:`add_bone_chain`). Returns the created bone name — Blender
        uniquifies collisions, so use the return value (e.g. as the deform vertex-group name)."""
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
    def _detach_connected_children(bone):
        """Unglue every child of *bone* whose head is locked to its TAIL, so an edit
        to this bone cannot move another one. Returns how many were detached.

        `use_connect` is Blender's "my head IS my parent's tail", so any edit that
        moves the tail drags the child -- a REST change. At rest that is invisible,
        because the pose follows the rest; under a BAKED pose (what every pull
        writes) it moves the skin. A carrier's bind is authored data and a bone's
        drawn length is a guess, so when the two disagree it is the glue that gives.
        """
        detached = 0
        for child in bone.children:
            if child.use_connect:
                child.use_connect = False
                detached += 1
        return detached

    @staticmethod
    def set_bone_heads(armature, heads):
        """Move bones by NAME — ``{bone: (x, y, z)}``, the new HEAD in armature space.
        Returns the number moved; a name with no bone, and a bone already within
        float32 reach of that point, are skipped.

        The WHOLE bone translates — head and tail together — so its direction, length
        and roll are unchanged. Connected neighbours are unglued first, this bone's
        own ``use_connect`` included (a head glued to a parent's tail cannot move at
        all), so nothing else shifts with it. That still makes this the opposite of
        :meth:`set_bone_lengths`: a head is part of the rest matrix every deform
        reads, so moving a bone ANYTHING is weighted to shifts the skin under a baked
        pose. Only move a bone nothing deforms from — a carrier's synthetic anchor —
        and the caller owns that check.
        """
        from mathutils import Vector

        moved = 0
        with RigUtils._active_mode(armature, "EDIT"):
            ebones = armature.data.edit_bones
            for name, head in dict(heads).items():
                bone = ebones.get(name)
                if bone is None:
                    continue
                target = Vector(head)
                delta = target - bone.head
                # Same float32 reasoning as set_bone_lengths: a head is an ABSOLUTE
                # coordinate, so a no-op write still re-quantizes it.
                if delta.length <= 1e-6 * (1.0 + target.length):
                    continue
                RigUtils._detach_connected_children(bone)
                bone.use_connect = False
                tail = Vector(bone.tail)
                bone.head = target
                bone.tail = tail + delta
                moved += 1
        return moved

    @staticmethod
    def set_bone_lengths(armature, lengths):
        """Resize bones by NAME — ``{bone: length}``, in the armature's own units.
        Returns the number resized; a name with no bone, a length that is not
        positive, and a bone already at that length are all skipped.

        Only the tail moves, and only along the bone's existing axis: the head,
        direction and roll stay, so the rest matrix every deform reads is
        untouched and the pose is not disturbed. A child bone CONNECTED to this
        one is disconnected first -- its head is glued to this tail and would
        otherwise be dragged, which is a rest change and moves a baked pose's
        skin (0.35 m on a 0.6 m resize, measured). Verified on a scaled, rotated,
        rolled, flat-parented chain: the deformed vertices moved 0.0, parent
        bones included (a child's rest offset is stored from its parent's TAIL,
        and Blender recomputes it from the edit bones).

        The one residual is float32: a tail is stored as an absolute coordinate,
        so writing one re-quantizes it, and the further the bone sits from its
        armature's origin the coarser that step is. Measured on production wire
        looms 250 units out, sizing a whole skeleton moves vertices 0.4-22 µm --
        an order below the pull's own 0-300 µm. A no-op write costs the same, so a
        bone already within float32 reach of the requested length is left alone,
        which also makes repeated calls converge instead of drifting.

        Bone length is a carrier's guess, not authored data: FBX and USD store no
        length, so both importers infer one from the distance to a bone's
        children and fall back to the parent's when there are none. That guess is
        wrong for any skeleton whose hierarchy was flattened for export — see
        ``MayaSceneImport``'s ``bones`` manifest section, which replays the
        lengths the flatten removed.
        """
        resized = 0
        with RigUtils._active_mode(armature, "EDIT"):
            ebones = armature.data.edit_bones
            for name, length in dict(lengths).items():
                bone = ebones.get(name)
                if bone is None or not length or float(length) <= 0.0:
                    continue
                # What a write could even land on: float32 resolution scales with
                # the coordinate magnitude, and a tail is stored ABSOLUTE, so a
                # difference under this would only re-quantize it. Too tight a
                # tolerance is worse than none -- it rewrites on every call and
                # drifts (measured on a rig 250 units out, converging over passes).
                if abs(bone.length - float(length)) <= 1e-6 * (1.0 + bone.head.length):
                    continue
                RigUtils._detach_connected_children(bone)
                bone.length = float(length)
                resized += 1
        return resized

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
        if not bone_names:
            return []
        mw = armature.matrix_world
        mw_inv = mw.inverted()
        data_bones = armature.data.bones
        heads_tails = [
            (
                mw @ data_bones[n].head_local,
                mw @ data_bones[n].tail_local,
                data_bones[n].head_radius,
                data_bones[n].tail_radius,
            )
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
    def add_bone_constraint(
        armature, bone_name, ctype, target=None, subtarget=None, **props
    ):
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
    def add_spline_ik(
        armature, bone_name, curve, chain_count, name="Spline IK", **props
    ):
        """Add a **Spline IK** bone constraint to pose bone *bone_name* so *chain_count* bones up the
        chain fit to *curve* — the faithful analogue of Maya's ``ikSplineSolver`` IK handle. Extra
        constraint props (``y_scale_mode``, ``xz_scale_mode``, ``use_curve_radius`` …) pass through."""
        return RigUtils.add_bone_constraint(
            armature,
            bone_name,
            "SPLINE_IK",
            target=curve,
            name=name,
            chain_count=int(chain_count),
            **props,
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
    def apply_falloff_weights(
        mesh, group_name, center, radius, profile="linear", add_group=True
    ):
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
            others = [
                (g.group, g.weight) for g in v.groups if g.group in influence_indices
            ]
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
    def copy_location(obj, target, influence=1.0, **props):
        """Maya pointConstraint → COPY_LOCATION. Stack two (influence 1, then ``frac``) for a lerp.

        Extra constraint props pass through (``use_offset=True`` adds the owner's pre-constraint
        location, i.e. Maya's ``maintainOffset``; ``name=`` labels it for teardown)."""
        return RigUtils._constraint(
            obj, "COPY_LOCATION", target, influence=influence, **props
        )

    @staticmethod
    def copy_rotation(obj, target, influence=1.0, **props):
        """Maya orientConstraint → COPY_ROTATION."""
        return RigUtils._constraint(
            obj, "COPY_ROTATION", target, influence=influence, **props
        )

    @staticmethod
    def damped_track(obj, target, track_axis="TRACK_Y", **props):
        """Single-axis aim (Maya aimConstraint, no up-vector) → DAMPED_TRACK."""
        return RigUtils._constraint(
            obj, "DAMPED_TRACK", target, track_axis=track_axis, **props
        )

    @staticmethod
    def track_to(obj, target, track_axis="TRACK_Y", up_axis="UP_Z", **props):
        """Aim with an up-vector (full Maya aimConstraint) → TRACK_TO."""
        return RigUtils._constraint(
            obj, "TRACK_TO", target, track_axis=track_axis, up_axis=up_axis, **props
        )

    @staticmethod
    def child_of(obj, target, set_inverse=True, **props):
        """Maya parentConstraint(maintainOffset=True) → CHILD_OF (inverse bound at the current pose).

        Extra props pass through — pass ``use_scale_x/y/z=False`` for a strict Maya
        ``parentConstraint`` mirror (that constraint drives translate/rotate only)."""
        c = RigUtils._constraint(obj, "CHILD_OF", target, **props)
        if set_inverse:
            c.inverse_matrix = target.matrix_world.inverted()
        return c

    # ----------------------------------------------------------------- drivers
    @staticmethod
    def _driver_add(obj, data_path, index):
        return (
            obj.driver_add(data_path, index)
            if index is not None
            else obj.driver_add(data_path)
        )

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
            for d in ad.drivers if ad else ():
                d.driver.expression = d.driver.expression  # re-assign -> recompile

    @staticmethod
    def add_distance_driver(
        obj, data_path, index, a, b, expression="dist", var_name="dist"
    ):
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
        obj,
        data_path,
        index,
        target,
        transform_type,
        space="WORLD_SPACE",
        expression=None,
        var_name="var",
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
    def add_transform_var(fcurve, name, target, transform_type, space="WORLD_SPACE"):
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
