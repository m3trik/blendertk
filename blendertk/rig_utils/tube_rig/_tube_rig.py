# !/usr/bin/python
# coding=utf-8
"""Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies + panel).

Rigs a tube-shaped mesh **multiple ways** through a strategy registry, mirroring Maya's
``TubeStrategy`` / ``RIG_MODES`` design at the name + behavior level:

- :class:`SplineIKStrategy` — *Spline (Hose/Cable)*: a bone chain fit to a driver curve via Blender's
  **Spline IK** bone constraint; a few control Empties hook the curve's points (live reshape, the
  ``DynamicPipe`` pattern), optional stretch via the constraint's curve-fit scaling.
- :class:`AnchorStrategy` — *Anchor (Piston/Hydraulic)*: two end controls; one bone whose head
  follows the start control and which **Stretch-To**s (or **Damped-Track**s) the end control.
- :class:`FKChainStrategy` — *FK Chain (Tail/Tentacle)*: native bone-hierarchy FK — the deform bones
  **are** the controls (curve ``custom_shape``s make them grabbable; rotating one carries its
  descendants), the most Blender-idiomatic FK.

Each strategy still *declares its options* as plain Qt-free **dicts** (``AttributeSpec``-shaped —
see :data:`SplineIKStrategy.options`); the engine stays Qt-free (Blender ``--background`` has no Qt
binding) and ``TubeStrategy.resolve``/``defaults`` use them to fill in a one-shot ``build()`` call's
missing kwargs. Adding a rig type = subclass :class:`TubeStrategy` + :meth:`TubeStrategy.register` —
no ``.ui`` edits needed to use it from code; ``TubeStrategy.register`` is a genuine blendertk-only
extension point mayatk's hardcoded ``if strategy == …`` dispatch doesn't have.

``tube_rig.ui`` is now a **verbatim mirror of mayatk's** (same objectNames/layout: a toolbox with
Step 1 *Create Joints* / Step 2 *Create IK & Controls* / Step 3 *Bind Skin* / *Utility* / *One-Click
Rig* pages), so :class:`TubeRigSlots` also exposes the **granular step-workflow** mayatk does —
:meth:`TubeRig.create_joint_chain` (Step 1) and :meth:`TubeRig.attach_spline_rig` (Step 2, spline
mode) operate on an EXISTING bone chain the same way the one-shot strategies' internal steps do,
just callable standalone. Squash / volume / auto-bend are now Spline-mode **options** (native Spline
IK XZ-scale modes + a distance driver — see :func:`_xz_scale_mode` / :meth:`TubeRig._add_auto_bend`),
and ``b004`` *Add End Constraints* is implemented (:meth:`TubeRig.constrain_end_with_falloff` — an
anchor bone that tracks an external object plus the per-vertex falloff weight blend
:meth:`RigUtils.apply_falloff_weights`). ``enable_twist`` is implemented too (:meth:`TubeRig.add_twist`):
Blender's Spline IK does NOT propagate the driver curve's point tilt to bone twist (probed), so twist
is the one toggle built as a roll-control bone + per-bone Copy Rotation chain (constant ``1/N``
influence → linear base→tip twist) applied AFTER the Spline IK solve, rather than a native scale-mode
flag like the others.

Divergences vs Maya (documented for parity): Maya joints → Armature **bones** (``RigUtils`` armature
primitives), ``ikSplineSolver`` → the **Spline IK** constraint, ``skinCluster`` → **Armature-deform +
auto weights**, separate FK control objects → **bones-as-controls** with custom shapes, Maya's
per-mesh ``TubeRig`` UUID cache → none yet (each call resolves fresh from the current selection,
same end-user model, no cross-call rig registry). The centerline comes from the shared
:class:`~blendertk.rig_utils.tube_rig.tube_path.TubePath`. ``import bpy`` is deferred into the call bodies.

Layout mirrors mayatk's ``rig_utils/tube_rig/``: this engine, ``strategies.py`` (the strategy
classes, :class:`TubeRigBundle` and ``TUBE_STRATEGIES``), ``tube_path.py`` and the panel
(``tube_rig_slots.py`` + ``tube_rig.ui``).
"""

import contextlib
from typing import Callable, Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.rig_utils.controls import Controls
from blendertk.rig_utils.tube_rig.tube_path import TubePath
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.rig_utils.tube_rig.strategies import (
    TUBE_STRATEGIES,
    TubeRigBundle,
)


# ----------------------------------------------------------------------------
# Engine (shared building blocks the strategies orchestrate)
# ----------------------------------------------------------------------------


class _TubeRigInternal(object):
    """Internal helpers for TubeRig."""

    @staticmethod
    def _uncrossed_end_index(armature, bones, anchor_pos, bone_index: int) -> int:
        """Return the end index the anchor at *anchor_pos* actually belongs to — mirror of
        mayatk's ``_TubeRigInternal._uncrossed_end_index``.

        A crossed call — handing the far end's anchor to ``bone_index=0`` (or the near end's
        to ``-1``) — builds a rig that looks right at rest and tears off BOTH ends the moment
        the anchor moves: the anchor bone is created at the far end but named for, and hooked
        to, the near end's control. Found in Maya (PROPS_DA, 2026-08-25) on 2 of 7 tubes; the
        Blender port had the same unguarded primitive.

        Only the two END indices can be crossed; a mid-chain index is returned unchanged. The
        swap needs a clear margin so a tube whose ends nearly coincide is left alone.

        Note: a per-call invariant cannot see a caller that hands BOTH anchors to the same end
        — callers holding both (``TubeRigSlots.b004``) assign them pairwise first.
        """
        n = len(bones)
        if n < 2:
            return bone_index
        idx = bone_index % n
        if idx not in (0, n - 1):
            return bone_index
        opposite = n - 1 if idx == 0 else 0

        db = armature.data.bones
        mw = armature.matrix_world

        def _head(j):
            return mw @ db[bones[j]].head_local

        here = (_head(idx) - anchor_pos).length
        there = (_head(opposite) - anchor_pos).length
        # 1% of the end-to-end span is the noise floor we require to act.
        margin = (_head(0) - (mw @ db[bones[-1]].tail_local)).length * 0.01
        return opposite if there < here - margin else bone_index

    @staticmethod
    def _xz_scale_mode(squash: bool, volume: bool) -> str:
        """Map Maya's separate squash / volume toggles onto Blender's Spline IK XZ-scale enum (Maya's
        two node-graph systems collapse to one native constraint mode): no squash -> ``NONE`` (the
        cross-section stays fixed on stretch); squash without volume -> ``INVERSE_PRESERVE`` (XZ = 1/Y,
        over-thins); squash with volume -> ``VOLUME_PRESERVE`` (XZ = 1/sqrt(Y), true volume preservation)."""
        if not squash:
            return "NONE"
        return "VOLUME_PRESERVE" if volume else "INVERSE_PRESERVE"


class TubeRig(ptk.LoggingMixin, _TubeRigInternal):
    """Rig a tube mesh via a named strategy — Blender mirror of mayatk's ``TubeRig``.

    ``TubeRig(mesh).build("spline", num_joints=16, num_controls=4)`` builds a Spline-IK hose rig.
    The strategies call the shared building blocks (:meth:`create_armature`, :meth:`build_curve`,
    :meth:`hook_curve_controls`, :meth:`make_control`) so each stays a thin orchestration.
    """

    def __init__(self, mesh, rig_name=None, log_level="WARNING"):
        self.mesh = mesh
        self.rig_name = rig_name or f"{getattr(mesh, 'name', 'tube')}_RIG"
        self.logger.setLevel(log_level)
        self._root = None

    @property
    def collection(self):
        import bpy

        users = getattr(self.mesh, "users_collection", None)
        return users[0] if users else bpy.context.collection

    # -- shared building blocks ------------------------------------------------
    def resolve_centerline(self, num_joints, precision=None, edges=None):
        """The tube's centerline (world points) for *num_joints*, raising if the mesh isn't a
        resolvable tube — the guard every strategy shares. *precision*/*edges* thread straight
        through to :meth:`TubePath.get_centerline` (edges = an explicit edge-selection override,
        e.g. :meth:`TubePath.get_selected_edges` — mayatk's optional ``filterExpand`` edge pick)."""
        pts, _ = TubePath.get_centerline(
            self.mesh, int(num_joints), precision=precision, edges=edges
        )
        if len(pts) < 2:
            raise ValueError("TubeRig: could not extract a centerline from the mesh.")
        return pts

    def create_root(self):
        self._root = RigUtils.create_locator(
            f"{self.rig_name}_grp", display_type="ARROWS", collection=self.collection
        )
        return self._root

    def create_armature(self, centerline, radius=None):
        """Armature + bone chain along *centerline*, parented under the rig root. Returns
        ``(armature_obj, bone_names)``. *radius* (optional) sets each bone's display radius
        (Maya's per-joint ``.radius``) — see :meth:`RigUtils.add_bone_chain`."""
        arm = RigUtils.create_armature(
            f"{self.rig_name}_arm", collection=self.collection
        )
        bones = RigUtils.add_bone_chain(
            arm, centerline, prefix=f"{self.rig_name}_jnt", radius=radius
        )
        if self._root:
            RigUtils.parent_keep_transform(arm, self._root)
        return arm, bones

    def create_joint_chain(self, centerline, radius=1.0, reverse=False):
        """Bones-only build step — mirror of mayatk's ``generate_joint_chain`` + lazy
        ``rig_group`` (the granular workflow's Step 1: joints with no curve/controls/bind yet).
        Creates the rig root on first use if one doesn't already exist on this instance. Returns
        ``(armature_obj, bone_names)`` — see :meth:`create_armature`."""
        pts = list(reversed(centerline)) if reverse else list(centerline)
        if self._root is None:
            self.create_root()
        return self.create_armature(pts, radius=radius)

    def _add_auto_bend(self, controls, factor=0.5):
        """Auto-bend: the middle control bulges in +Y as the two end controls compress together — a
        distance-driven mirror of Maya's ``setup_auto_bend`` (its ``multiplyDivide`` on the mid
        offset). +Y follows Maya's up-axis convention, which is perpendicular to the axis for the
        common Z/X-aligned tube. Uses ``delta_location`` so the bulge is ADDITIVE to the user's
        manual handle position (like Maya's separate auto-bend offset group). No-op for fewer than 3
        controls (nothing between the two ends to bulge)."""
        import bpy

        if len(controls) < 3:
            return
        start, end = controls[0], controls[-1]
        mid = controls[len(controls) // 2]
        bpy.context.view_layer.update()
        rest = (start.matrix_world.translation - end.matrix_world.translation).length
        RigUtils.add_distance_driver(
            mid,
            "delta_location",
            1,
            start,
            end,
            expression=f"max(0.0, ({rest:.5f} - dist)) * {factor}",
            var_name="dist",
        )
        RigUtils.refresh_drivers([mid])

    def add_twist(self, armature, bones, radius=1.0):
        """Progressive roll twist for a Spline-IK chain — Blender's Spline IK ignores the driver
        curve's point tilt (probed 2026-07-11), so twist can't ride the curve like Maya's ikSpline
        twist. Instead: a roll-control BONE at the tip (oriented along the chain axis, outside the IK
        chain) plus a per-deform-bone Copy Rotation (local Y only, ``mix_mode='ADD'``, CONSTANT
        ``influence = 1/N``). Equal local roll increments accumulate LINEARLY down the parented chain,
        so rolling the control about its Y spins the tip a full turn while the start stays put — Maya's
        base→tip twist distribution, applied AFTER the Spline IK solve (a driver on the bones' rotation
        would be overwritten by the constraint; the constraint stacks on top of it). The control gets a
        ring custom shape (grabbable, like the FK strategy's bones-as-controls). Returns the twist bone
        name."""
        import bpy
        from mathutils import Vector

        bpy.context.view_layer.update()
        mw = armature.matrix_world
        db = armature.data.bones
        tip = mw @ db[bones[-1]].tail_local
        prev = mw @ db[bones[-1]].head_local
        axis = tip - prev
        axis = axis.normalized() if axis.length > 1e-6 else Vector((0.0, 0.0, 1.0))
        twist = RigUtils.add_bone(
            armature,
            f"{self.rig_name}_twist_ctrl",
            head=tip,
            tail=tip + axis * max(float(radius), 0.1),
            deform=False,
        )
        n = len(bones)
        for bn in bones:
            RigUtils.add_bone_constraint(
                armature,
                bn,
                "COPY_ROTATION",
                target=armature,
                subtarget=twist,
                use_x=False,
                use_y=True,
                use_z=False,
                mix_mode="ADD",
                owner_space="LOCAL",
                target_space="LOCAL",
                influence=1.0 / n,
            )
        shape = self._hidden_control_shape(
            f"{self.rig_name}_twistshape", radius, axis="y"
        )
        armature.pose.bones[twist].custom_shape = shape
        return twist

    def attach_spline_rig(
        self,
        armature,
        bones,
        num_controls=3,
        radius=1.0,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
    ):
        """Curve + Spline IK + hooked controls on an EXISTING bone chain — mirror of mayatk's
        granular Step 2 (the 'spline' branch of ``b002``), which adds IK/controls onto joints a
        prior step already created, instead of building the armature itself (that's
        :meth:`create_armature`, used by the one-shot strategies). Reparents *armature* under a
        fresh rig root if this ``TubeRig`` doesn't have one yet (e.g. *armature* came from the
        user's own selection rather than :meth:`create_joint_chain`). Returns ``(curve, controls)``.

        The deform toggles map onto native Spline IK scale modes (Maya's per-node deform systems):
        ``enable_stretch`` -> Y-scale ``FIT_CURVE``; ``enable_squash`` / ``enable_volume`` ->
        XZ-scale via :func:`_xz_scale_mode` (squash-and-stretch with optional volume preservation).
        ``enable_twist`` adds the roll-control chain (:meth:`add_twist`) — the one toggle with no
        native Spline IK equivalent (curve tilt is ignored), so it's a constraint chain rather than a
        scale-mode flag.
        """
        import bpy

        if self._root is None:
            self.create_root()
            RigUtils.parent_keep_transform(armature, self._root)
        bpy.context.view_layer.update()
        mw = armature.matrix_world
        data_bones = armature.data.bones
        centerline = [mw @ data_bones[b].head_local for b in bones]
        centerline.append(mw @ data_bones[bones[-1]].tail_local)

        curve = self.build_curve(centerline, int(num_controls))
        RigUtils.parent_keep_transform(curve, self._root)
        RigUtils.add_spline_ik(
            armature,
            bones[-1],
            curve,
            chain_count=len(bones),
            y_scale_mode=("FIT_CURVE" if enable_stretch else "BONE_ORIGINAL"),
            xz_scale_mode=_TubeRigInternal._xz_scale_mode(enable_squash, enable_volume),
        )
        controls = self.hook_curve_controls(curve, float(radius), self._root)
        if enable_twist:
            self.add_twist(armature, bones, radius=float(radius))
        if enable_auto_bend:
            self._add_auto_bend(controls)
        return curve, controls

    def build_curve(self, points, count):
        """A low-res NURBS driver curve (``count`` control points resampled along *points*) for the
        Spline IK to follow — built at the world origin (identity matrix → clean hook binds)."""
        import bpy

        ctrl_pts = ptk.Polyline.resample([list(p) for p in points], max(2, int(count)))
        cu = bpy.data.curves.new(f"{self.rig_name}_curve", "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("NURBS")
        sp.points.add(len(ctrl_pts) - 1)
        for pt, p in zip(sp.points, ctrl_pts):
            pt.co = (p[0], p[1], p[2], 1.0)
        sp.order_u = min(4, len(ctrl_pts))
        sp.use_endpoint_u = True
        obj = bpy.data.objects.new(f"{self.rig_name}_curve", cu)
        self.collection.objects.link(obj)
        return obj

    def _hidden_control_shape(self, name, size, axis="x"):
        """A hidden circle whose only job is to be a pose bone's ``custom_shape`` (bones-as-controls)
        — shared by :class:`FKChainStrategy` (every deform bone becomes grabbable) and
        :meth:`add_twist` (the roll control). The source object is hidden (the bones draw it, not the
        origin clutter) and parented under the rig root so it's owned by the rig (deleted with it)."""
        shape = Controls.create(
            "circle",
            name=name,
            size=float(size),
            axis=axis,
            collection=self.collection,
        )
        shape.hide_viewport = shape.hide_render = True
        if self._root:
            RigUtils.parent_keep_transform(shape, self._root)
        return shape

    def make_control(
        self, shape, name, size, location, root, color=(1, 1, 0), axis="x"
    ):
        """Create a control curve at *location*, parented under *root* (keeping its world pos).

        *root* comes from :meth:`create_root` (built at the world origin → identity ``matrix_world``
        without a depsgraph settle), so ``parent_keep_transform`` binds a correct identity
        parent-inverse here. Callers that then read the control's ``matrix_world`` (e.g. for a hook
        bind) must ``view_layer.update()`` once after creating all controls — not per control."""
        ctrl = Controls.create(
            shape,
            name=name,
            size=size,
            axis=axis,
            color=color,
            location=tuple(location),
            collection=self.collection,
        )
        RigUtils.parent_keep_transform(ctrl, root)
        return ctrl

    def hook_curve_controls(self, curve, radius, root):
        """One control per curve control-point, each Hook-bound to its point (the live-reshape
        pattern shared with ``DynamicPipe``). Returns the controls."""
        import bpy

        bpy.context.view_layer.update()
        spline = curve.data.splines[0]
        controls = []
        for i, p in enumerate(spline.points):
            world = curve.matrix_world @ p.co.to_3d()  # NURBS points are 4D
            controls.append(
                self.make_control(
                    "circle", f"{self.rig_name}_ctrl_{i}", radius, world, root
                )
            )
        bpy.context.view_layer.update()  # settle control matrices before binding hooks
        for i, ctrl in enumerate(controls):
            EditUtils.hook_curve_point(curve, i, ctrl)
        bpy.context.view_layer.update()
        return controls

    def _end_control(self, armature, bones, index):
        """Resolve the rig's start (``0``) / end (``-1``/last) control Empty, or ``None``
        for a bare chain — the Blender analogue of mayatk's ``_end_control``.

        Scene-derived (no build registry, so post-restart rigs still resolve): the
        Spline IK constraint on the chain carries the driver curve, and each of the
        curve's Hook modifiers records which control point it binds
        (``hook_curve_point`` sets ``vertex_indices``) — the lowest-index hook's
        object is the start control, the highest the end control."""
        curve = None
        for bn in reversed(bones):
            pb = armature.pose.bones.get(bn)
            if pb is None:
                continue
            for c in pb.constraints:
                if c.type == "SPLINE_IK" and c.target is not None:
                    curve = c.target
                    break
            if curve is not None:
                break
        if curve is None:
            return None
        hooks = {}
        for mod in curve.modifiers:
            if mod.type == "HOOK" and mod.object is not None:
                indices = tuple(mod.vertex_indices)
                if indices:
                    hooks[indices[0]] = mod.object
        if not hooks:
            return None
        return hooks[min(hooks)] if index == 0 else hooks[max(hooks)]

    def _clear_end_anchor(self, armature, mesh, bone_name, control=None):
        """Delete a previous :meth:`constrain_end_with_falloff` result for ONE end — mirror of
        mayatk's ``TubeRig._clear_end_anchor``, so re-anchoring REPLACES rather than stacks.

        Without this ``RigUtils.add_bone`` uniquifies the colliding name (``…_anchor_end.001``,
        per its own docstring) and ``RigUtils.child_of`` appends a second CHILD_OF, so a rerun
        left a dead deform bone painted on the mesh and a control following BOTH anchors.

        Removing the anchor's vertex group needs no weight restoration (unlike Maya's
        ``removeInfluence``): :meth:`RigUtils.apply_falloff_weights` scaled every other group on
        an affected vertex by the SAME ``(1 - w)``, so dropping the anchor group leaves the
        remaining weights in their original proportions and Blender's armature deform
        normalizes them back — the deform is bit-for-bit the pre-anchor one.
        """

        if control is not None:
            for c in [c for c in control.constraints if c.name.startswith(bone_name)]:
                control.constraints.remove(c)
        vg = mesh.vertex_groups.get(bone_name) if mesh else None
        if vg is not None:
            mesh.vertex_groups.remove(vg)
        pb = armature.pose.bones.get(bone_name)
        if pb is not None:  # pose constraints die with the bone, but be explicit
            for c in list(pb.constraints):
                pb.constraints.remove(c)
        if armature.data.bones.get(bone_name) is not None:
            with RigUtils._active_mode(armature, "EDIT"):
                ebones = armature.data.edit_bones
                eb = ebones.get(bone_name)
                if eb is not None:
                    ebones.remove(eb)

    def constrain_end_with_falloff(
        self, armature, bones, anchor, mesh, falloff=5.0, bone_index=-1, control=None
    ):
        """Constrain one end of a BOUND tube rig to an external *anchor* object with a distance-falloff
        weight blend — Blender mirror of mayatk's ``constrain_end_with_falloff`` (the granular Step-4
        utility). Grafts an *anchor bone* onto *armature* at the anchor's position, ``COPY_LOCATION``s
        it to *anchor* (so it tracks the anchor's motion; translation-following avoids the bind-time
        pose jump a rotation-copy would cause — see the twist divergence note), then paints falloff
        weights (:meth:`RigUtils.apply_falloff_weights`) so *mesh* vertices near that end blend onto
        the anchor bone over *falloff* world units: the near-end skin sticks to the anchor, fading to
        the existing deform by the radius.

        End-control routing (mirror of Maya's route-through-the-end-control): when the
        chain carries a Spline IK rig, the end's hooked *control* Empty is auto-resolved
        (:meth:`_end_control`) — pass ``control=`` to override — and ``CHILD_OF``-bound
        to the anchor, so the whole end assembly (curve hook + IK-driven bones) follows
        coherently; the falloff blend alone would drag only the near-end skin while the
        IK curve stayed put. Bare bound chains (no controls) keep the direct falloff-only
        behaviour. *bone_index* picks the end (0 = start, -1 = end). Returns the created
        anchor-bone name.

        Re-anchoring the same end REPLACES its previous anchor (:meth:`_clear_end_anchor`),
        matching mayatk.

        Divergence vs Maya: Maya's parentConstraint copies position AND orientation; the Blender port
        copies translation only (``COPY_LOCATION``) to stay bind-time-stable headlessly — anchor
        rotation-follow would need a maintain-offset ``CHILD_OF`` inverse (same family as ``chk_twist``,
        deferred with it)."""
        import bpy
        from mathutils import Vector

        bpy.context.view_layer.update()
        db = armature.data.bones

        # Assign the anchor to the end it actually sits at. A crossed call builds a rig
        # that is correct at rest and tears off BOTH ends as soon as the anchor moves.
        anchor_pos = anchor.matrix_world.translation.copy()
        corrected = _TubeRigInternal._uncrossed_end_index(
            armature, bones, anchor_pos, bone_index
        )
        if corrected != bone_index:
            self.logger.warning(
                f"constrain_end_with_falloff: '{anchor.name}' is nearer the opposite end "
                f"of the chain than bone index {bone_index}; anchoring index {corrected} "
                "instead. Pass the anchor that sits at the end you name."
            )
            bone_index = corrected

        constrained = bones[bone_index]
        mw = armature.matrix_world
        end_head = mw @ db[constrained].head_local
        end_tail = mw @ db[constrained].tail_local
        axis = end_tail - end_head
        axis = axis.normalized() if axis.length > 1e-6 else Vector((0.0, 0.0, 1.0))
        length = max((end_tail - end_head).length, 1e-3)
        # read before the sweep: its EDIT-mode round trip rebuilds the bone collection
        head_radius = db[constrained].head_radius
        idx = bone_index % len(bones)
        end = "start" if idx == 0 else "end" if idx == len(bones) - 1 else str(idx)
        bone_name = f"{self.rig_name}_anchor_{end}"

        # Resolve the end control BEFORE the sweep — it is found from the Spline IK
        # constraint + curve hooks, both independent of the anchor assembly.
        if control is None and idx in (0, len(bones) - 1):
            control = self._end_control(armature, bones, 0 if idx == 0 else -1)
        self._clear_end_anchor(armature, mesh, bone_name, control)

        anchor_bone = RigUtils.add_bone(
            armature,
            bone_name,
            head=anchor_pos,
            tail=anchor_pos + axis * length,
            radius=head_radius,
            deform=True,
        )
        # the anchor bone tracks the external anchor object; the graft deforms via the matching group.
        RigUtils.add_bone_constraint(
            armature, anchor_bone, "COPY_LOCATION", target=anchor
        )
        if control is not None:
            # named off the bone so the replace sweep can find it
            RigUtils.child_of(
                control, anchor, name=f"{bone_name}_child_of"
            )  # coherent end-assembly follow (curve hook + bones)
        RigUtils.apply_falloff_weights(
            mesh, anchor_bone, anchor_pos, falloff, profile="linear"
        )
        return anchor_bone

    # -- dispatch --------------------------------------------------------------
    #: Deliberately per-operation state (scoped by :meth:`_reporting`) rather
    #: than a constructor argument: a hook set at construction would outlive
    #: the UI that owns it, and a later script call would tick a footer that is
    #: no longer on screen. Mirror of mayatk's ``TubeRig._progress``.
    _progress = None

    @contextlib.contextmanager
    def _reporting(self, progress: Optional[Callable]):
        """Route this operation's phase reports to *progress* for its duration.

        A nested operation without a hook of its own INHERITS the caller's, so
        a rebuild's teardown still reports. Outside any operation the ambient
        hook is ``None``, so a standalone script call reports nothing.

        Parameters:
            progress: ``callable(current, total, message)`` -- the ecosystem's
                progress-callback shape, so ``Switchboard.progress_adapter``
                wires a footer straight in. ``None`` inherits (or disables).
        """
        prev = self._progress
        self._progress = progress if progress is not None else prev
        try:
            yield
        finally:
            self._progress = prev

    def _report(self, message: str) -> None:
        """Announce a build phase: log it, and tick the progress hook.

        One call site for both, so a phase can never be logged but not shown
        (or the reverse). The tick is indeterminate -- ``current=None``,
        ``total=0`` -- because a rig's phase count varies with the strategy and
        the options, so a percentage would be a fiction; the message is what
        tells the user the tool is working. A build runs with global undo off
        and no per-step redraw, which is exactly when a silent panel reads as
        hung.

        A hook that raises is dropped rather than allowed to abort the rig:
        feedback failing is never a reason to leave a half-built rig behind.
        """
        self.logger.info(message)
        cb = self._progress
        if cb is None:
            return
        try:
            cb(None, 0, message)
        except Exception as e:  # noqa: BLE001
            self._progress = None
            self.logger.debug(f"Progress hook dropped ({e}).")

    @CoreUtils.undo_checkpoint
    def build(
        self, strategy="spline", progress: Optional[Callable] = None, **opts
    ) -> TubeRigBundle:
        """Build the rig with the named *strategy* (``"spline"`` / ``"anchor"`` / ``"fk"`` or a
        registered custom one); *opts* override the strategy's declared option defaults.

        Parameters:
            progress: Optional ``callable(current, total, message)``. The build
                runs with global undo suspended and no per-step redraw, so
                without a hook the panel is the only thing telling the user the
                tool is working rather than hung. Wire a footer with
                ``sb.progress_adapter(update)``.
        """
        cls = TUBE_STRATEGIES.get(strategy)
        if cls is None:
            raise ValueError(
                f"Unknown tube rig strategy '{strategy}'. Available: {sorted(TUBE_STRATEGIES)}"
            )
        with self._reporting(progress):
            return cls().build(self, **opts)
