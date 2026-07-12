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
missing kwargs. Adding a rig type = subclass :class:`TubeStrategy` + :func:`register_strategy` — no
``.ui`` edits needed to use it from code; ``register_strategy`` is a genuine blendertk-only
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
:class:`~blendertk.rig_utils.tube_path.TubePath`. ``import bpy`` is deferred into the call bodies.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

import pythontk as ptk

from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.rig_utils.controls import Controls
from blendertk.rig_utils.tube_path import TubePath
from blendertk.edit_utils._edit_utils import hook_curve_point


@dataclass
class TubeRigBundle:
    """Result of a strategy build — mirror of mayatk's ``TubeRigBundle``."""

    root: object
    armature: object
    bones: List[str]
    curve: Optional[object] = None
    controls: List = field(default_factory=list)


def _xz_scale_mode(squash: bool, volume: bool) -> str:
    """Map Maya's separate squash / volume toggles onto Blender's Spline IK XZ-scale enum (Maya's
    two node-graph systems collapse to one native constraint mode): no squash -> ``NONE`` (the
    cross-section stays fixed on stretch); squash without volume -> ``INVERSE_PRESERVE`` (XZ = 1/Y,
    over-thins); squash with volume -> ``VOLUME_PRESERVE`` (XZ = 1/sqrt(Y), true volume preservation)."""
    if not squash:
        return "NONE"
    return "VOLUME_PRESERVE" if volume else "INVERSE_PRESERVE"


# ----------------------------------------------------------------------------
# Strategies (each owns its option declaration)
# ----------------------------------------------------------------------------


class TubeStrategy(ABC):
    """Base tube-rig strategy. ``options`` is a list of **AttributeSpec kwargs dicts** (Qt-free) — the
    single source of both the panel widgets and the build defaults."""

    name: str = ""
    label: str = ""
    options: List[dict] = []

    def defaults(self) -> dict:
        return {o["key"]: o.get("default") for o in self.options}

    def resolve(self, opts: Optional[dict]) -> dict:
        """Merge caller *opts* over the declared defaults (``None`` values fall back to default)."""
        d = self.defaults()
        if opts:
            d.update({k: v for k, v in opts.items() if v is not None})
        return d

    @abstractmethod
    def build(self, rig: "TubeRig", **opts) -> TubeRigBundle:
        ...


class SplineIKStrategy(TubeStrategy):
    name = "spline"
    label = "Spline (Hose/Cable)"
    options = [
        {"key": "num_joints", "label": "Joints", "kind": "int", "default": 12,
         "minimum": 2, "maximum": 200, "tooltip": "Deforming bones fit to the curve."},
        {"key": "num_controls", "label": "Controls", "kind": "int", "default": 3,
         "minimum": 2, "maximum": 24, "tooltip": "Animatable handles hooked along the curve."},
        {"key": "radius", "label": "Control Size", "kind": "float", "default": 1.0,
         "minimum": 0.01, "maximum": 100.0, "decimals": 2},
        {"key": "enable_stretch", "label": "Stretch", "kind": "bool", "default": True,
         "tooltip": "Bones scale to fill the curve length (Spline IK Fit Curve)."},
        {"key": "enable_squash", "label": "Squash", "kind": "bool", "default": True,
         "tooltip": "Cross-section thins on stretch / fattens on compression (Spline IK XZ scale)."},
        {"key": "enable_volume", "label": "Volume", "kind": "bool", "default": True,
         "tooltip": "Preserve volume while squashing (XZ = 1/sqrt of the stretch). Needs Squash."},
        {"key": "enable_auto_bend", "label": "Auto Bend", "kind": "bool", "default": False,
         "tooltip": "The middle bulges out as the two ends compress together (hose buckle)."},
        {"key": "enable_twist", "label": "Twist", "kind": "bool", "default": False,
         "tooltip": "Add a tip roll control; rolling it twists the hose progressively from start to end."},
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        centerline = rig.resolve_centerline(o["num_joints"])
        root = rig.create_root()
        arm, bones = rig.create_armature(centerline)
        # Steps 2 (IK/controls + deform) is the shared attach_spline_rig — the same engine path the
        # granular b002 button drives, so the one-shot and step workflows can't diverge.
        curve, controls = rig.attach_spline_rig(
            arm, bones, num_controls=int(o["num_controls"]), radius=float(o["radius"]),
            enable_stretch=o["enable_stretch"], enable_squash=o["enable_squash"],
            enable_volume=o["enable_volume"], enable_auto_bend=o["enable_auto_bend"],
            enable_twist=o["enable_twist"],
        )
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        return TubeRigBundle(root, arm, bones, curve=curve, controls=controls)


class AnchorStrategy(TubeStrategy):
    name = "anchor"
    label = "Anchor (Piston/Hydraulic)"
    options = [
        {"key": "radius", "label": "Control Size", "kind": "float", "default": 1.0,
         "minimum": 0.01, "maximum": 100.0, "decimals": 2},
        {"key": "enable_stretch", "label": "Stretch", "kind": "bool", "default": True,
         "tooltip": "Bone stretches between the two anchors (else fixed-length aim)."},
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        centerline = rig.resolve_centerline(2)
        start, end = centerline[0], centerline[-1]
        root = rig.create_root()
        arm, bones = rig.create_armature([start, end])
        c_start, c_end = (
            rig.make_control("cube", f"{rig.rig_name}_start", o["radius"] * 1.5, start, root, (0, 1, 1)),
            rig.make_control("cube", f"{rig.rig_name}_end", o["radius"] * 1.5, end, root, (0, 1, 1)),
        )
        # head follows the start anchor; the bone stretches (or just aims) at the end anchor.
        RigUtils.add_bone_constraint(arm, bones[0], "COPY_LOCATION", target=c_start)
        RigUtils.add_bone_constraint(
            arm, bones[0], "STRETCH_TO" if o["enable_stretch"] else "DAMPED_TRACK", target=c_end
        )
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        return TubeRigBundle(root, arm, bones, controls=[c_start, c_end])


class FKChainStrategy(TubeStrategy):
    name = "fk"
    label = "FK Chain (Tail/Tentacle)"
    options = [
        {"key": "num_joints", "label": "Joints", "kind": "int", "default": 10,
         "minimum": 2, "maximum": 200, "tooltip": "FK control bones along the tube."},
        {"key": "radius", "label": "Control Size", "kind": "float", "default": 1.0,
         "minimum": 0.01, "maximum": 100.0, "decimals": 2},
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        centerline = rig.resolve_centerline(o["num_joints"])
        root = rig.create_root()
        arm, bones = rig.create_armature(centerline)
        # native bone-hierarchy FK: the deform bones ARE the controls; a curve custom shape per bone
        # makes each grabbable (rotating one carries its descendants through the connected chain).
        shape = rig._hidden_control_shape(f"{rig.rig_name}_fkshape", o["radius"], axis="x")
        for bn in bones:
            arm.pose.bones[bn].custom_shape = shape
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        return TubeRigBundle(root, arm, bones, controls=list(bones))


# Strategy registry (mayatk's RIG_MODES) — extend with register_strategy.
TUBE_STRATEGIES = {
    c.name: c for c in (SplineIKStrategy, AnchorStrategy, FKChainStrategy)
}


def register_strategy(cls):
    """Register a custom :class:`TubeStrategy` subclass (keyed by ``cls.name``) — the extension point
    mirroring Maya's mode registry. Usable as a decorator."""
    TUBE_STRATEGIES[cls.name] = cls
    return cls


# ----------------------------------------------------------------------------
# Engine (shared building blocks the strategies orchestrate)
# ----------------------------------------------------------------------------


class TubeRig(ptk.LoggingMixin):
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
        arm = RigUtils.create_armature(f"{self.rig_name}_arm", collection=self.collection)
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
            mid, "delta_location", 1, start, end,
            expression=f"max(0.0, ({rest:.5f} - dist)) * {factor}", var_name="dist",
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
        axis = (tip - prev)
        axis = axis.normalized() if axis.length > 1e-6 else Vector((0.0, 0.0, 1.0))
        twist = RigUtils.add_bone(
            armature, f"{self.rig_name}_twist_ctrl",
            head=tip, tail=tip + axis * max(float(radius), 0.1), deform=False,
        )
        n = len(bones)
        for bn in bones:
            RigUtils.add_bone_constraint(
                armature, bn, "COPY_ROTATION", target=armature, subtarget=twist,
                use_x=False, use_y=True, use_z=False,
                mix_mode="ADD", owner_space="LOCAL", target_space="LOCAL", influence=1.0 / n,
            )
        shape = self._hidden_control_shape(f"{self.rig_name}_twistshape", radius, axis="y")
        armature.pose.bones[twist].custom_shape = shape
        return twist

    def attach_spline_rig(self, armature, bones, num_controls=3, radius=1.0, enable_stretch=True,
                          enable_squash=False, enable_volume=False, enable_auto_bend=False,
                          enable_twist=False):
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
            armature, bones[-1], curve, chain_count=len(bones),
            y_scale_mode=("FIT_CURVE" if enable_stretch else "BONE_ORIGINAL"),
            xz_scale_mode=_xz_scale_mode(enable_squash, enable_volume),
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

        ctrl_pts = ptk.Polyline.resample(
            [list(p) for p in points], max(2, int(count))
        )
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
            "circle", name=name, size=float(size), axis=axis, collection=self.collection,
        )
        shape.hide_viewport = shape.hide_render = True
        if self._root:
            RigUtils.parent_keep_transform(shape, self._root)
        return shape

    def make_control(self, shape, name, size, location, root, color=(1, 1, 0), axis="x"):
        """Create a control curve at *location*, parented under *root* (keeping its world pos).

        *root* comes from :meth:`create_root` (built at the world origin → identity ``matrix_world``
        without a depsgraph settle), so ``parent_keep_transform`` binds a correct identity
        parent-inverse here. Callers that then read the control's ``matrix_world`` (e.g. for a hook
        bind) must ``view_layer.update()`` once after creating all controls — not per control."""
        ctrl = Controls.create(
            shape, name=name, size=size, axis=axis, color=color,
            location=tuple(location), collection=self.collection,
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
                self.make_control("circle", f"{self.rig_name}_ctrl_{i}", radius, world, root)
            )
        bpy.context.view_layer.update()  # settle control matrices before binding hooks
        for i, ctrl in enumerate(controls):
            hook_curve_point(curve, i, ctrl)
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

    def constrain_end_with_falloff(self, armature, bones, anchor, mesh, falloff=5.0,
                                   bone_index=-1, control=None):
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

        Divergence vs Maya: Maya's parentConstraint copies position AND orientation; the Blender port
        copies translation only (``COPY_LOCATION``) to stay bind-time-stable headlessly — anchor
        rotation-follow would need a maintain-offset ``CHILD_OF`` inverse (same family as ``chk_twist``,
        deferred with it)."""
        import bpy
        from mathutils import Vector

        bpy.context.view_layer.update()
        db = armature.data.bones
        constrained = bones[bone_index]
        mw = armature.matrix_world
        end_head = mw @ db[constrained].head_local
        end_tail = mw @ db[constrained].tail_local
        axis = (end_tail - end_head)
        axis = axis.normalized() if axis.length > 1e-6 else Vector((0.0, 0.0, 1.0))
        length = max((end_tail - end_head).length, 1e-3)
        anchor_pos = anchor.matrix_world.translation.copy()
        idx = bone_index % len(bones)
        end = "start" if idx == 0 else "end"

        anchor_bone = RigUtils.add_bone(
            armature, f"{self.rig_name}_anchor_{end}",
            head=anchor_pos, tail=anchor_pos + axis * length,
            radius=db[constrained].head_radius, deform=True,
        )
        # the anchor bone tracks the external anchor object; the graft deforms via the matching group.
        RigUtils.add_bone_constraint(armature, anchor_bone, "COPY_LOCATION", target=anchor)
        if control is None and idx in (0, len(bones) - 1):
            control = self._end_control(armature, bones, 0 if idx == 0 else -1)
        if control is not None:
            RigUtils.child_of(control, anchor)  # coherent end-assembly follow (curve hook + bones)
        RigUtils.apply_falloff_weights(mesh, anchor_bone, anchor_pos, falloff, profile="linear")
        return anchor_bone

    # -- dispatch --------------------------------------------------------------
    def build(self, strategy="spline", **opts) -> TubeRigBundle:
        """Build the rig with the named *strategy* (``"spline"`` / ``"anchor"`` / ``"fk"`` or a
        registered custom one); *opts* override the strategy's declared option defaults."""
        cls = TUBE_STRATEGIES.get(strategy)
        if cls is None:
            raise ValueError(
                f"Unknown tube rig strategy '{strategy}'. Available: {sorted(TUBE_STRATEGIES)}"
            )
        return cls().build(self, **opts)


# ----------------------------------------------------------------------------
# UI slots — the HYBRID docked panel (mode combo rebuilds the options dynamically)
# ----------------------------------------------------------------------------


class TubeRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the co-located ``tube_rig.ui`` — the **HYBRID** panel.

    The mode combo (``cmb_preset``) lists the registered strategies; selecting one **rebuilds the
    options body** from that strategy's option dicts (turning each into a ``uitk.AttributeSpec`` →
    ``make_widget``), so adding a rig type needs no ``.ui`` edit. Build reads the option widgets,
    resolves the selected mesh, and calls :meth:`TubeRig.build`. Self-contained
    (``ptk.LoggingMixin``); the Qt-only ``uitk`` factory is imported lazily (headless-safe).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.tube_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[tube_rig] ")
        self._option_widgets = {}  # key -> built widget (rebuilt per mode)

        self.ui.cmb_preset.clear()
        for name, cls in TUBE_STRATEGIES.items():
            self.ui.cmb_preset.addItem(cls.label or name, name)  # userData = strategy key
        self.ui.cmb_preset.currentIndexChanged.connect(self._on_mode_changed)
        self._rebuild_options()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Tube Rig",
                body="Rig a tube-shaped mesh several ways. The centerline is auto-detected; pick a "
                "<b>Mode</b> and the options below reconfigure to that rig type.",
                steps=[
                    "Select a tube mesh.",
                    "Pick a <b>Mode</b> — Spline (hose/cable), Anchor (piston), or FK (tail).",
                    "Set the mode's options, then press <b>Build Rig</b>.",
                ],
                sections=[
                    ("Modes", [
                        "<b>Spline</b> — a bone chain follows a curve; a few control handles hook "
                        "the curve (great for hoses/cables, with stretch).",
                        "<b>Anchor</b> — two end controls drive a piston/hydraulic that stretches "
                        "between them.",
                        "<b>FK</b> — the bones are the controls (rotate one, the rest follow) — a "
                        "tail/tentacle.",
                    ]),
                ],
                notes=[
                    "Each mode <b>declares its own options</b>; the panel rebuilds them on mode "
                    "change. Custom strategies registered via <b>register_strategy</b> appear here "
                    "automatically.",
                ],
            )
        )

    # ------------------------------------------------------------------ dynamic options
    def _current_strategy(self):
        name = self.ui.cmb_preset.currentData()
        return name, TUBE_STRATEGIES.get(name)

    def _rebuild_options(self):
        """Clear + repopulate the options container from the selected strategy's option dicts."""
        from qtpy import QtWidgets, QtCore
        from uitk.bridge.spec import AttributeSpec, make_widget

        layout = self.ui.wgt_options.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._option_widgets = {}

        _, cls = self._current_strategy()
        if cls is None:
            return
        for opt in cls.options:
            spec = AttributeSpec(**opt)
            row = QtWidgets.QWidget(self.ui.wgt_options)
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(2)
            label = QtWidgets.QLabel(spec.display_label + ":", row)
            label.setMinimumWidth(90)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            widget = make_widget(spec, row)
            widget.setObjectName(f"opt_{spec.key}")
            if spec.tooltip:
                label.setToolTip(spec.tooltip)
                widget.setToolTip(spec.tooltip)
            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            layout.addWidget(row)
            self._option_widgets[spec.key] = widget

    def _on_mode_changed(self, *_):
        self._rebuild_options()

    def _collect_opts(self):
        from uitk.bridge.spec import read_value

        return {k: read_value(w) for k, w in self._option_widgets.items()}

    # ------------------------------------------------------------------ build
    def b000(self):
        """Build Rig — run the selected strategy on the selected tube mesh."""
        from blendertk.core_utils._core_utils import selected_objects

        meshes = [o for o in selected_objects() if o.type == "MESH"]
        if not meshes:
            self.sb.message_box("Select a tube mesh to rig.")
            return
        name, cls = self._current_strategy()
        if cls is None:
            self.sb.message_box("Pick a rig mode first.")
            return
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(meshes[-1], rig_name=rig_name)
            bundle = rig.build(name, **self._collect_opts())
        except Exception as e:  # surface the engine's reason (e.g. non-tube mesh)
            self.sb.message_box(f"Tube rig failed: {e}")
            return
        self.sb.message_box(
            f"<hl>Built {self.ui.cmb_preset.currentText()} rig "
            f"({len(bundle.bones)} bones) on {meshes[-1].name}.</hl>"
        )

    # ------------------------------------------------------------------ granular steps (Spline)
    @staticmethod
    def _ordered_chain(armature):
        """Bone names root->tip for the deform chain — Spline IK needs the chain order (``bones[-1]``
        is the tip it constrains). Walks parent→first-child from a root. When the armature ALSO holds
        isolated control bones (an ``enable_twist`` roll control, ``b004`` anchor bones — all parentless
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

    def b001(self):
        """Step 1 — create the joint/bone chain from the selected tube mesh's centerline (no controls
        or bind yet). Mirror of Maya's ``b001`` create_joints_from_tube; Reverse Direction = chk000."""
        from blendertk.core_utils._core_utils import selected_objects

        meshes = [o for o in selected_objects() if o.type == "MESH"]
        if not meshes:
            self.sb.message_box("Select a tube mesh to create joints from.")
            return
        opts = self._collect_opts()
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(meshes[-1], rig_name=rig_name)
            centerline = rig.resolve_centerline(int(opts.get("num_joints", 12)))
            _, bones = rig.create_joint_chain(
                centerline, radius=float(opts.get("radius", 1.0)),
                reverse=self.ui.chk000.isChecked(),
            )
        except Exception as e:  # surface the engine's reason (e.g. non-tube mesh)
            self.sb.message_box(f"Create Joints failed: {e}")
            return
        self.sb.message_box(f"<hl>Step 1: created {len(bones)} joints on {meshes[-1].name}.</hl>")

    def b002(self):
        """Step 2 — add the curve + Spline IK + hooked controls onto the selected armature's EXISTING
        bone chain (Maya's ``b002`` for Spline mode). Reads the deform toggles from the mode options."""
        import bpy

        arm = bpy.context.view_layer.objects.active
        if arm is None or arm.type != "ARMATURE":
            arm = next((o for o in bpy.context.selected_objects if o.type == "ARMATURE"), None)
        if arm is None:
            self.sb.message_box("Select the joint chain (armature) created in Step 1.")
            return
        bones = self._ordered_chain(arm)
        if len(bones) < 2:
            self.sb.message_box("The selected armature needs a chain of at least 2 bones for Spline IK.")
            return
        opts = self._collect_opts()
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(None, rig_name=rig_name)
            rig._root = arm.parent  # reuse Step 1's rig root if present (else attach_spline_rig makes one)
            _, controls = rig.attach_spline_rig(
                arm, bones, num_controls=int(opts.get("num_controls", 3)),
                radius=float(opts.get("radius", 1.0)),
                enable_stretch=bool(opts.get("enable_stretch", True)),
                enable_squash=bool(opts.get("enable_squash", False)),
                enable_volume=bool(opts.get("enable_volume", False)),
                enable_auto_bend=bool(opts.get("enable_auto_bend", False)),
                enable_twist=bool(opts.get("enable_twist", False)),
            )
        except Exception as e:
            self.sb.message_box(f"Create IK / Controls failed: {e}")
            return
        self.sb.message_box(f"<hl>Step 2: added Spline IK + {len(controls)} controls to {arm.name}.</hl>")

    def b003(self):
        """Step 3 — bind the selected tube mesh to the selected armature (Armature modifier + automatic
        weights). Mirror of Maya's ``b003`` bind_joint_chain."""
        import bpy

        sel = bpy.context.selected_objects
        mesh = next((o for o in sel if o.type == "MESH"), None)
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        if mesh is None or arm is None:
            self.sb.message_box("Select BOTH the tube mesh and its joint chain (armature), then Bind.")
            return
        try:
            RigUtils.bind_armature(mesh, arm, auto_weights=True)
        except Exception as e:
            self.sb.message_box(f"Bind failed: {e}")
            return
        self.sb.message_box(f"<hl>Step 3: bound {mesh.name} to {arm.name}.</hl>")

    @staticmethod
    def _estimate_tube_radius(mesh):
        """Approximate a tube's cross-sectional radius from its world bounding box — for a tube
        aligned to its longest axis the two SMALLER dimensions are the cross-section diameter, so the
        radius ≈ the average of their half-extents. Sizes the end-anchor falloff the way Maya's b004
        uses ~2× the joint radius."""
        dims = sorted(mesh.dimensions)  # ascending world bbox dims (min, mid, max)
        return max((dims[0] + dims[1]) / 4.0, 1e-3)

    def b004(self):
        """Utility — Constrain Both Ends to Anchors: select the rig's armature and TWO anchor objects,
        then each tube end blends onto its NEAREST anchor with a distance falloff (mirror of Maya's
        b004). Anchors are auto-assigned to ends by proximity (Blender selection order is unreliable).
        Requires the mesh already bound (Step 3)."""
        import bpy

        sel = list(bpy.context.selected_objects)
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        # anchors are transforms/Empties, never a mesh — so the bound tube being selected too
        # (the common case) isn't mistaken for an anchor.
        anchors = [o for o in sel if o is not arm and o.type != "MESH"]
        if arm is None:
            self.sb.message_box("Select the rig's armature and the two anchor objects.")
            return
        if len(anchors) < 2:
            self.sb.message_box(
                "Select TWO anchor objects (plus the rig armature) to constrain both ends."
            )
            return
        anchors = anchors[:2]
        bones = self._ordered_chain(arm)
        if len(bones) < 2:
            self.sb.message_box("The selected armature needs a chain of at least 2 bones.")
            return
        # the bound mesh: a mesh whose Armature modifier targets this armature (Maya's skinCluster check)
        mesh = next(
            (o for o in bpy.data.objects if o.type == "MESH"
             and any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers)),
            None,
        )
        if mesh is None:
            self.sb.message_box(
                "The joints aren't bound to a mesh yet — run Step 3 (Bind Joints to Mesh) first."
            )
            return

        bpy.context.view_layer.update()
        mw, db = arm.matrix_world, arm.data.bones
        p_start = mw @ db[bones[0]].head_local
        p_end = mw @ db[bones[-1]].tail_local
        a0 = anchors[0].matrix_world.translation
        a1 = anchors[1].matrix_world.translation
        # uncross: assign each anchor to its nearest tube end (order-independent)
        crossed = ((a0 - p_start).length + (a1 - p_end).length) > (
            (a1 - p_start).length + (a0 - p_end).length
        )
        start_anchor, end_anchor = (anchors[1], anchors[0]) if crossed else (anchors[0], anchors[1])

        falloff = self._estimate_tube_radius(mesh) * 2.0
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(mesh, rig_name=rig_name)
            s = rig.constrain_end_with_falloff(arm, bones, start_anchor, mesh, falloff=falloff, bone_index=0)
            e = rig.constrain_end_with_falloff(arm, bones, end_anchor, mesh, falloff=falloff, bone_index=-1)
        except Exception as ex:
            self.sb.message_box(f"Add End Constraints failed: {ex}")
            return
        self.sb.message_box(f"<hl>Both ends constrained — start: {s}, end: {e}.</hl>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("tube_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
