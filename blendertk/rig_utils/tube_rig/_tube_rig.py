# !/usr/bin/python
# coding=utf-8
"""Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies).

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

The panel is tentacle's ``slots/tools/tube_rig.py`` (one Slots class for both hosts), which drives
this class exactly as it drives mayatk's: :data:`TubeRig.MODES`, :meth:`TubeRig.selected_tubes`,
:meth:`TubeRig.for_build`, :meth:`TubeRig.undo_step`, :meth:`TubeRig.create_joints` (Step 1),
:meth:`TubeRig.create_controls_from_selection` (Step 2: :meth:`attach_spline_rig` /
:meth:`attach_anchor_rig` / :meth:`attach_fk_rig` on an EXISTING bone chain, the building blocks
the one-shot strategies use too), :meth:`TubeRig.bind_from_selection` (Step 3) and
:meth:`TubeRig.constrain_ends_from_selection` (an anchor bone per end plus the falloff weight blend
:meth:`RigUtils.apply_falloff_weights`). Squash / volume / auto-bend are Spline-mode options (native
Spline IK XZ-scale modes + a distance driver -- see :func:`_xz_scale_mode` /
:meth:`TubeRig._add_auto_bend`). ``enable_twist`` (:meth:`TubeRig.add_twist`): Blender's Spline IK
does NOT propagate the driver curve's point tilt to bone twist (probed), so twist is the one toggle
built as a roll-control bone + per-bone Copy Rotation chain (constant ``1/N`` influence → linear
base→tip twist) applied AFTER the Spline IK solve, rather than a native scale-mode flag like the
others. Sizing is mayatk's: ``radius`` is the bone display radius (Auto = half the tube radius) and
the controls follow the measured tube radius (:meth:`TubeRig.resolve_sizes`).

Divergences vs Maya (documented for parity): Maya joints → Armature **bones** (``RigUtils`` armature
primitives), ``ikSplineSolver`` → the **Spline IK** constraint, ``skinCluster`` → **Armature-deform +
auto weights**, separate FK control objects → **bones-as-controls** with custom shapes, Maya's
per-mesh ``TubeRig`` UUID cache → none yet (each call resolves fresh from the current selection,
same end-user model, no cross-call rig registry). The centerline comes from the shared
:class:`~blendertk.rig_utils.tube_rig.tube_path.TubePath`. ``import bpy`` is deferred into the call bodies.

Not mirrored yet: mayatk's rig registry and scene record, so no teardown, rename or rebind
(tentacle's panel disables those three here), and a rebuild beside an existing rig makes a second
one rather than replacing it.

Layout mirrors mayatk's ``rig_utils/tube_rig/``: this engine, ``strategies.py`` (the strategy
classes, :class:`TubeRigBundle`, ``TUBE_STRATEGIES`` and the panel's ``RIG_MODES``) and
``tube_path.py``.
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
    RIG_MODES,
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
        — callers holding both (:meth:`TubeRig.constrain_ends_from_selection`) assign them
        pairwise first.
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

    #: Bone constraint types a control rig puts on its chain (Spline IK, Anchor's follow and
    #: stretch): Step 2 refuses a chain carrying one rather than stacking a second rig on it.
    _CONTROL_CONSTRAINTS = ("SPLINE_IK", "COPY_LOCATION", "STRETCH_TO", "DAMPED_TRACK")

    @staticmethod
    def _ordered_chain(armature):
        """Bone names root->tip for the deform chain — Spline IK needs the chain order (``bones[-1]``
        is the tip it constrains). Walks parent→first-child from a root. When the armature ALSO holds
        isolated control bones (an ``enable_twist`` roll control, end-anchor bones — all parentless
        AND childless), picks the parentless root whose chain is LONGEST, so those single-bone helpers
        can't be mistaken for the deform chain (order-independent, unlike picking the first root)."""

        def walk(root):
            chain, b = [], root
            while b is not None:
                chain.append(b.name)
                b = b.children[0] if b.children else None
            return chain

        chains = [walk(b) for b in armature.data.bones if b.parent is None]
        return max(chains, key=len) if chains else []

    @staticmethod
    def _bound_mesh(armature):
        """The mesh an Armature modifier binds to *armature* (mayatk's skinCluster lookup), or
        ``None`` for a chain not bound yet."""
        import bpy

        return next(
            (
                o
                for o in bpy.data.objects
                if o.type == "MESH"
                and any(m.type == "ARMATURE" and m.object == armature for m in o.modifiers)
            ),
            None,
        )


class TubeRig(ptk.LoggingMixin, _TubeRigInternal):
    """Rig a tube mesh via a named strategy — Blender mirror of mayatk's ``TubeRig``.

    ``TubeRig(mesh).build("spline", num_joints=16, num_controls=4)`` builds a Spline-IK hose rig.
    The strategies call the shared building blocks (:meth:`create_armature`, :meth:`build_curve`,
    :meth:`hook_curve_controls`, :meth:`make_control`) so each stays a thin orchestration.
    """

    #: The modes tentacle's Tube Rig panel offers, in its Mode combo's order (the combo saves an
    #: index) -- mirror of mayatk's ``TubeRig.MODES`` (``strategies.RIG_MODES``).
    MODES = RIG_MODES

    def __init__(self, mesh, rig_name=None, log_level="WARNING"):
        self.mesh = mesh
        self.rig_name = rig_name or f"{getattr(mesh, 'name', 'tube')}_RIG"
        self.logger.setLevel(log_level)
        self._root = None
        # The chain once built or read back (mayatk's ``joints``): sizes fall back to it.
        self.armature = None
        self.joints = None

    @property
    def collection(self):
        import bpy

        users = getattr(self.mesh, "users_collection", None) or getattr(
            self.armature, "users_collection", None
        )
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

    def _chain_points(self):
        """World positions of the chain's bone heads plus its tip (``[]`` without a chain)."""
        arm = self.armature
        if arm is None or not self.joints:
            return []
        db, mw = arm.data.bones, arm.matrix_world
        bones = [db[b] for b in self.joints if db.get(b) is not None]
        if not bones:
            return []
        return [mw @ b.head_local for b in bones] + [mw @ bones[-1].tail_local]

    def estimate_tube_radius(self, centerline=None):
        """Measure the tube's radius from the mesh surface -- mirror of mayatk's.

        Uses *centerline* when given, else the chain's bone positions, else a fresh centerline.
        ``None`` when the rig has no mesh (a chain read back from a bare armature).
        """
        if getattr(self.mesh, "type", None) != "MESH":
            return None
        pts = list(centerline) if centerline else self._chain_points()
        if not pts:
            try:
                pts = self.resolve_centerline(-1)
            except ValueError:
                return None
        return TubePath.estimate_radius(self.mesh, pts)

    def resolve_sizes(self, centerline=None, joint_radius=-1.0):
        """``(joint_radius, control_size)`` from the tube -- mirror of mayatk's ``resolve_sizes``.

        The control size is the measured tube radius, so rigs stay proportional to the mesh
        whatever the scene scale. ``joint_radius`` (the bones' display radius) ``<= 0`` is Auto:
        half the tube radius. Without a mesh (Step 2 on a chain not bound yet) the tube radius
        falls back to the bones' own radii, which Auto-sized bones carry at half of it; else 1.0.
        """
        tube_radius = self.estimate_tube_radius(centerline)
        if not tube_radius:
            radii = []
            if self.armature is not None:
                db = self.armature.data.bones
                radii = [db[b].head_radius for b in self.joints or [] if db.get(b) is not None]
            tube_radius = max(radii) * 2.0 if radii else 1.0
        if joint_radius is None or joint_radius <= 0:
            joint_radius = tube_radius * 0.5
        return float(joint_radius), float(tube_radius)

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
        self.armature, self.joints = arm, list(bones)
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

    @CoreUtils.undoable
    def create_joints(
        self, strategy="spline", num_joints=-1, radius=-1.0, reverse=False, edges=None
    ):
        """Step 1 of a step-by-step build: this rig's bone chain along the tube's centerline, as
        the mode's strategy makes it -- mirror of mayatk's ``create_joints``.

        Parameters:
            strategy (str): The mode the chain is for: "anchor" makes one bone between the
                tube's two ends, any other a chain.
            num_joints (int): Centerline points (the chain has one bone fewer); ``-1`` = Auto
                (:meth:`TubePath.get_centerline`'s default count). Ignored for "anchor".
            radius (float): Bone display radius; ``<= 0`` = Auto (half the tube radius).
            reverse (bool): Root the chain at the tube's other end.
            edges: Selected edges to read the path from (:meth:`selected_edges`), else the mesh.

        Returns:
            list: The bone names, root first.

        Raises:
            ValueError: No centerline could be read from the mesh.
        """
        if strategy == "anchor":
            num_joints = 2
        centerline = self.resolve_centerline(num_joints, edges=edges)
        joint_radius, _ = self.resolve_sizes(centerline, radius)
        _, bones = self.create_joint_chain(centerline, radius=joint_radius, reverse=reverse)
        return list(bones)

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
        granular Step 2 (the 'spline' branch of :meth:`create_controls_from_selection`), which adds IK/controls onto joints a
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

    def attach_anchor_rig(self, armature, bones, size=1.0, enable_stretch=True):
        """Anchor (piston) controls on an EXISTING one-bone chain — mirror of mayatk's
        ``create_anchor_controls``: a cube control at each end; the bone's head follows the start
        control and it Stretch-Tos (or, without stretch, Damped-Tracks) the end control. Reparents
        *armature* under a fresh rig root if this ``TubeRig`` has none yet. Returns the two
        controls."""
        import bpy

        if self._root is None:
            self.create_root()
            RigUtils.parent_keep_transform(armature, self._root)
        bpy.context.view_layer.update()
        mw, db = armature.matrix_world, armature.data.bones
        start = mw @ db[bones[0]].head_local
        end = mw @ db[bones[-1]].tail_local
        c_start = self.make_control(
            "cube", f"{self.rig_name}_start", size * 1.5, start, self._root, (0, 1, 1)
        )
        c_end = self.make_control(
            "cube", f"{self.rig_name}_end", size * 1.5, end, self._root, (0, 1, 1)
        )
        # head follows the start anchor; the bone stretches (or just aims) at the end anchor.
        RigUtils.add_bone_constraint(armature, bones[0], "COPY_LOCATION", target=c_start)
        RigUtils.add_bone_constraint(
            armature,
            bones[0],
            "STRETCH_TO" if enable_stretch else "DAMPED_TRACK",
            target=c_end,
        )
        return [c_start, c_end]

    def attach_fk_rig(self, armature, bones, size=1.0):
        """FK on an EXISTING bone chain — Blender's bones-as-controls (mayatk's
        ``create_fk_controls`` builds separate control curves): the deform bones ARE the
        controls, a curve custom shape per bone makes each grabbable, and rotating one carries its
        descendants through the connected chain. Reparents *armature* under a fresh rig root if
        this ``TubeRig`` has none yet. Returns the bone names (the controls)."""
        if self._root is None:
            self.create_root()
            RigUtils.parent_keep_transform(armature, self._root)
        shape = self._hidden_control_shape(f"{self.rig_name}_fkshape", size, axis="x")
        for bn in bones:
            armature.pose.bones[bn].custom_shape = shape
        return list(bones)

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

    # -- selection API (what tentacle's Tube Rig panel acts on) -----------------
    @staticmethod
    def _unique_auto_rig_name(leaf):
        """An unused ``<leaf>_RIG`` (its root ``<name>_grp`` free) for a rig the user did not name
        -- mirror of mayatk's: a second rig on a same-named tube would otherwise collide with the
        first one's names, Blender suffixing ``.001`` onto its pieces one by one."""
        import bpy

        base = f"{leaf}_RIG"
        name, i = base, 1
        while bpy.data.objects.get(f"{name}_grp") is not None:
            name = f"{base}_{i:02d}"
            i += 1
        return name

    @classmethod
    def for_build(cls, obj, rig_name=None):
        """The rig an operation on *obj* (a tube mesh, or its name) builds — mirror of mayatk's.
        Blender keeps no rig registry or scene record yet, so this is always a NEW rig (mayatk's
        finds the one that already owns *obj*): named *rig_name*, else an unused ``<mesh>_RIG``.
        ``None`` for no object."""
        mesh = RigUtils.resolve_object(obj)
        if mesh is None:
            return None
        return cls(mesh, rig_name=rig_name or cls._unique_auto_rig_name(mesh.name))

    @classmethod
    def _from_armature(cls, armature, rig_name=None):
        """A handle on the rig *armature* belongs to — the stand-in for mayatk's ``for_node``,
        read from the scene: its root is the armature's parent, its mesh the one an Armature
        modifier binds to it, its name the armature's minus the ``_arm`` :meth:`create_armature`
        adds (*rig_name* names a hand-made armature's rig)."""
        name = armature.name
        if name.endswith("_arm"):
            rig_name = name[: -len("_arm")]
        rig = cls(cls._bound_mesh(armature), rig_name=rig_name or name)
        rig._root = armature.parent
        rig.armature = armature
        rig.joints = cls._ordered_chain(armature)
        return rig

    @staticmethod
    def selected_tubes():
        """The tube meshes a build acts on — mirror of mayatk's: every selected mesh object (one
        rig each), or, with an edge selection in Edit Mode (a path on ONE mesh), just that mesh."""
        meshes = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        if TubeRig.selected_edges():
            active = CoreUtils.active_object()
            return [active] if active in meshes else meshes[:1]
        return meshes

    @staticmethod
    def selected_edges():
        """The active mesh's selected Edit-Mode edges — the path a build or Step 1 reads the
        centerline from instead of the mesh (mayatk's edge selection) — or ``None``."""
        active = CoreUtils.active_object()
        if active is None or active.type != "MESH":
            return None
        return TubePath.get_selected_edges(active)

    @staticmethod
    def _selected_armature():
        """The active armature, else the first selected one (``None`` without one)."""
        arm = CoreUtils.active_object()
        if arm is None or arm.type != "ARMATURE":
            arm = next(
                (o for o in CoreUtils.selected_objects() if o.type == "ARMATURE"), None
            )
        return arm

    def _existing_controls(self):
        """The first control-rig piece already on this rig's chain (``bone: constraint`` or
        ``bone: custom shape``), or ``None`` — mirror of mayatk's."""
        arm = self.armature
        if arm is None:
            return None
        for bn in self.joints or []:
            pb = arm.pose.bones.get(bn)
            if pb is None:
                continue
            for c in pb.constraints:
                if c.type in self._CONTROL_CONSTRAINTS:
                    return f"{bn}: {c.name}"
            if pb.custom_shape is not None:
                return f"{bn}: {pb.custom_shape.name}"
        return None

    @staticmethod
    @contextlib.contextmanager
    def undo_step(name="Tube Rig"):
        """ONE undo step for everything the block does — mirror of mayatk's: how a batch of
        builds (the Tube Rig panel's) stays a single Ctrl+Z. The operations inside fold into it.
        As :meth:`CoreUtils.undo_checkpoint` does, the drivers are recompiled AFTER the step's
        push, which re-stales them (an auto-bend driver read 0 until recompiled)."""
        try:
            with CoreUtils.undo_chunk(name):
                yield
        finally:
            try:
                RigUtils.refresh_drivers(None)  # every object carrying a driver
            except ImportError:  # no Blender: nothing was built
                pass

    @classmethod
    @CoreUtils.undo_checkpoint
    def create_controls_from_selection(
        cls,
        strategy="spline",
        num_controls=3,
        rig_name=None,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_twist=True,
        enable_auto_bend=False,
    ):
        """Step 2 of a step-by-step build: the mode's controls on the selected armature's EXISTING
        bone chain (Step 1's) — mirror of mayatk's, which reads the selected root joint. Spline:
        :meth:`attach_spline_rig`; Anchor: :meth:`attach_anchor_rig` (a one-bone chain); FK:
        :meth:`attach_fk_rig` (every bone a control, so *num_controls* is unused).

        Returns:
            tuple: ``(kind, bones, controls)`` — the rig kind for a report ("Spline IK" /
            "Anchor" / "FK"), the chain's bone names and the controls' names.

        Raises:
            ValueError: No armature is selected, its chain does not fit the mode, or the chain
                already has controls (the message says what to do instead).
        """
        arm = cls._selected_armature()
        if arm is None:
            raise ValueError("Select the joint chain (armature) created in Step 1.")
        bones = cls._ordered_chain(arm)
        if not bones:
            raise ValueError(f"The armature '{arm.name}' has no bones.")
        if strategy == "anchor" and len(bones) != 1:
            raise ValueError(
                f"Anchor rigs use one bone between their two end controls (got "
                f"{len(bones)}).\nRun Step 1 in Anchor mode to create it."
            )
        if strategy == "spline" and len(bones) < 2:
            raise ValueError(
                "The selected armature needs a chain of at least 2 bones for Spline IK."
            )

        rig = cls._from_armature(arm, rig_name)
        existing = rig._existing_controls()
        if existing:
            raise ValueError(
                f"Controls already exist for '{rig.rig_name}' ({existing}).\n"
                "Delete the previous controls or rebuild with One-Click Rig."
            )
        _, size = rig.resolve_sizes()
        if strategy == "spline":
            _, controls = rig.attach_spline_rig(
                arm,
                bones,
                num_controls=int(num_controls),
                radius=size,
                enable_stretch=bool(enable_stretch),
                enable_squash=bool(enable_squash),
                enable_volume=bool(enable_volume),
                enable_auto_bend=bool(enable_auto_bend),
                enable_twist=bool(enable_twist),
            )
            kind = "Spline IK"
        elif strategy == "anchor":
            controls = rig.attach_anchor_rig(
                arm, bones, size=size, enable_stretch=bool(enable_stretch)
            )
            kind = "Anchor"
        else:
            controls = rig.attach_fk_rig(arm, bones, size=size)
            kind = "FK"
        return kind, list(bones), [getattr(c, "name", c) for c in controls]

    @classmethod
    @CoreUtils.undo_checkpoint
    def bind_from_selection(cls, strategy="spline", rig_name=None):
        """Step 3 of a step-by-step build: bind the selected tube mesh to the selected armature
        (an Armature modifier with automatic weights) — mirror of mayatk's. Blender keeps no
        reliable selection order, so the two are told apart by type; *strategy* / *rig_name* are
        mayatk's signature (the armature carries both here).

        Returns:
            tuple: ``(mesh, bone_count, modifier)`` — names, for a report.

        Raises:
            ValueError: The selection lacks the mesh or the armature.
        """
        sel = CoreUtils.selected_objects()
        mesh = next((o for o in sel if o.type == "MESH"), None)
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        if mesh is None or arm is None:
            raise ValueError(
                "Select BOTH the tube mesh and its joint chain (armature), then Bind."
            )
        bones = cls._ordered_chain(arm)
        modifier = RigUtils.bind_armature(mesh, arm, auto_weights=True)
        return mesh.name, len(bones), getattr(modifier, "name", arm.name)

    @classmethod
    @CoreUtils.undo_checkpoint
    def constrain_ends_from_selection(cls, strategy="spline", rig_name=None, progress=None):
        """Constrain one or both ends of the selected bound rig to anchors — mirror of mayatk's:
        the rig's armature plus one anchor object per end to constrain. Each anchor constrains
        its NEAREST end (:meth:`constrain_end_with_falloff`, falloff ≈2× the tube radius); a
        single anchor leaves the other end free; two anchors off the same end are refused with
        the reason. The bound mesh being selected too (the common case) is never taken for an
        anchor. *strategy* / *rig_name* are mayatk's signature; *progress* is ticked per end.

        Returns:
            list: ``(end, anchor, anchor_bone)`` per anchor.

        Raises:
            ValueError: The selection is not an armature plus one or two anchors at different
                ends, or the chain is not bound yet.
        """
        import bpy

        sel = list(CoreUtils.selected_objects())
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        anchors = [o for o in sel if o != arm and o.type != "MESH"]
        if arm is None or not anchors:
            raise ValueError(
                "Select the rig's armature and the anchor object for each tube end to "
                "constrain (one or two)."
            )
        if len(anchors) > 2:
            raise ValueError(
                f"A tube has two ends — got {len(anchors)} anchors. Select one anchor "
                "per end to constrain."
            )
        bones = cls._ordered_chain(arm)
        if len(bones) < 2:
            raise ValueError("The selected armature needs a chain of at least 2 bones.")
        rig = cls._from_armature(arm, rig_name)
        if rig.mesh is None:
            raise ValueError(
                "The joints aren't bound to a mesh yet — run Step 3 (Bind Skin) first."
            )

        bpy.context.view_layer.update()
        mw, db = arm.matrix_world, arm.data.bones
        p_start = mw @ db[bones[0]].head_local
        p_end = mw @ db[bones[-1]].tail_local
        # each anchor to its nearest tube end (order-independent); two anchors off
        # one end are refused rather than silently collapsed onto it
        nearest = [
            0
            if (a.matrix_world.translation - p_start).length
            <= (a.matrix_world.translation - p_end).length
            else -1
            for a in anchors
        ]
        if len(anchors) == 2 and nearest[0] == nearest[1]:
            which = "start" if nearest[0] == 0 else "end"
            raise ValueError(
                f"Both anchors sit nearest the tube's {which} end ({anchors[0].name}, "
                f"{anchors[1].name}).\nSelect one anchor per end — or a single anchor to "
                "constrain just that end."
            )

        _, size = rig.resolve_sizes()  # falloff proportional to the tube: ≈2× its radius
        results = []
        with rig._reporting(progress):
            for anchor, idx in zip(anchors, nearest):
                end = "start" if idx == 0 else "end"
                rig._report(f"Constraining {end} to {anchor.name}…")
                bone = rig.constrain_end_with_falloff(
                    arm, bones, anchor, rig.mesh, falloff=size * 2.0, bone_index=idx
                )
                results.append((end, anchor.name, bone))
        return results

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
