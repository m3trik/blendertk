# !/usr/bin/python
# coding=utf-8
"""Tube Rig build strategies (Blender) — mirror of mayatk's ``tube_rig/strategies.py``.

Each :class:`TubeStrategy` declares its options as Qt-free ``AttributeSpec``-shaped dicts and
orchestrates :class:`TubeRig` step methods; ``TUBE_STRATEGIES`` is the registry
(:meth:`TubeStrategy.register` extends it). :class:`TubeRigBundle` is the build result.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from blendertk.rig_utils._rig_utils import RigUtils

if TYPE_CHECKING:
    from blendertk.rig_utils.tube_rig._tube_rig import TubeRig


@dataclass
class TubeRigBundle:
    """Result of a strategy build — mirror of mayatk's ``TubeRigBundle``."""

    root: object
    armature: object
    bones: List[str]
    curve: Optional[object] = None
    controls: List = field(default_factory=list)


# ----------------------------------------------------------------------------
# Strategies (each owns its option declaration)
# ----------------------------------------------------------------------------


class TubeStrategy(ABC):
    """Base tube-rig strategy. ``options`` is a list of **AttributeSpec kwargs dicts** (Qt-free) — the
    single source of both the panel widgets and the build defaults."""

    name: str = ""
    label: str = ""
    options: List[dict] = []

    @staticmethod
    def register(cls):
        """Register a custom :class:`TubeStrategy` subclass (keyed by ``cls.name``) — the extension
        point mirroring Maya's mode registry, reached as ``btk.TubeStrategy.register``. Usable as a
        decorator: ``@TubeStrategy.register`` above a strategy subclass. A genuine blendertk-only
        extension point mayatk's hardcoded ``if strategy == …`` dispatch doesn't have."""
        TUBE_STRATEGIES[cls.name] = cls
        return cls

    def defaults(self) -> dict:
        return {o["key"]: o.get("default") for o in self.options}

    def resolve(self, opts: Optional[dict]) -> dict:
        """Merge caller *opts* over the declared defaults (``None`` values fall back to default)."""
        d = self.defaults()
        if opts:
            d.update({k: v for k, v in opts.items() if v is not None})
        return d

    @abstractmethod
    def build(self, rig: "TubeRig", **opts) -> TubeRigBundle: ...


class SplineIKStrategy(TubeStrategy):
    name = "spline"
    label = "Spline (Hose/Cable)"
    options = [
        {
            "key": "num_joints",
            "label": "Joints",
            "kind": "int",
            "default": 12,
            "minimum": 2,
            "maximum": 200,
            "tooltip": "Deforming bones fit to the curve.",
        },
        {
            "key": "num_controls",
            "label": "Controls",
            "kind": "int",
            "default": 3,
            "minimum": 2,
            "maximum": 24,
            "tooltip": "Animatable handles hooked along the curve.",
        },
        {
            "key": "radius",
            "label": "Control Size",
            "kind": "float",
            "default": 1.0,
            "minimum": 0.01,
            "maximum": 100.0,
            "decimals": 2,
        },
        {
            "key": "enable_stretch",
            "label": "Stretch",
            "kind": "bool",
            "default": True,
            "tooltip": "Bones scale to fill the curve length (Spline IK Fit Curve).",
        },
        {
            "key": "enable_squash",
            "label": "Squash",
            "kind": "bool",
            "default": True,
            "tooltip": "Cross-section thins on stretch / fattens on compression (Spline IK XZ scale).",
        },
        {
            "key": "enable_volume",
            "label": "Volume",
            "kind": "bool",
            "default": True,
            "tooltip": "Preserve volume while squashing (XZ = 1/sqrt of the stretch). Needs Squash.",
        },
        {
            "key": "enable_auto_bend",
            "label": "Auto Bend",
            "kind": "bool",
            "default": False,
            "tooltip": "The middle bulges out as the two ends compress together (hose buckle).",
        },
        {
            "key": "enable_twist",
            "label": "Twist",
            "kind": "bool",
            "default": False,
            "tooltip": "Add a tip roll control; rolling it twists the hose progressively from start to end.",
        },
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        rig._report("Building spline IK: reading the tube's centerline…")
        centerline = rig.resolve_centerline(o["num_joints"])
        root = rig.create_root()
        rig._report(f"Building spline IK: creating {len(centerline)} joints…")
        arm, bones = rig.create_armature(centerline)
        rig._report("Building spline IK: creating curve, IK and controls…")
        # Steps 2 (IK/controls + deform) is the shared attach_spline_rig — the same engine path the
        # granular b002 button drives, so the one-shot and step workflows can't diverge.
        curve, controls = rig.attach_spline_rig(
            arm,
            bones,
            num_controls=int(o["num_controls"]),
            radius=float(o["radius"]),
            enable_stretch=o["enable_stretch"],
            enable_squash=o["enable_squash"],
            enable_volume=o["enable_volume"],
            enable_auto_bend=o["enable_auto_bend"],
            enable_twist=o["enable_twist"],
        )
        rig._report("Building spline IK: binding skin…")
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        rig._report("Spline IK build complete.")
        return TubeRigBundle(root, arm, bones, curve=curve, controls=controls)


class AnchorStrategy(TubeStrategy):
    name = "anchor"
    label = "Anchor (Piston/Hydraulic)"
    options = [
        {
            "key": "radius",
            "label": "Control Size",
            "kind": "float",
            "default": 1.0,
            "minimum": 0.01,
            "maximum": 100.0,
            "decimals": 2,
        },
        {
            "key": "enable_stretch",
            "label": "Stretch",
            "kind": "bool",
            "default": True,
            "tooltip": "Bone stretches between the two anchors (else fixed-length aim).",
        },
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        rig._report("Building anchor rig: reading the tube's centerline…")
        centerline = rig.resolve_centerline(2)
        start, end = centerline[0], centerline[-1]
        root = rig.create_root()
        rig._report("Building anchor rig: creating end joints…")
        arm, bones = rig.create_armature([start, end])
        rig._report("Building anchor rig: creating controls…")
        c_start, c_end = (
            rig.make_control(
                "cube",
                f"{rig.rig_name}_start",
                o["radius"] * 1.5,
                start,
                root,
                (0, 1, 1),
            ),
            rig.make_control(
                "cube", f"{rig.rig_name}_end", o["radius"] * 1.5, end, root, (0, 1, 1)
            ),
        )
        # head follows the start anchor; the bone stretches (or just aims) at the end anchor.
        RigUtils.add_bone_constraint(arm, bones[0], "COPY_LOCATION", target=c_start)
        RigUtils.add_bone_constraint(
            arm,
            bones[0],
            "STRETCH_TO" if o["enable_stretch"] else "DAMPED_TRACK",
            target=c_end,
        )
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        return TubeRigBundle(root, arm, bones, controls=[c_start, c_end])


class FKChainStrategy(TubeStrategy):
    name = "fk"
    label = "FK Chain (Tail/Tentacle)"
    options = [
        {
            "key": "num_joints",
            "label": "Joints",
            "kind": "int",
            "default": 10,
            "minimum": 2,
            "maximum": 200,
            "tooltip": "FK control bones along the tube.",
        },
        {
            "key": "radius",
            "label": "Control Size",
            "kind": "float",
            "default": 1.0,
            "minimum": 0.01,
            "maximum": 100.0,
            "decimals": 2,
        },
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        rig._report("Building FK chain: reading the tube's centerline…")
        centerline = rig.resolve_centerline(o["num_joints"])
        root = rig.create_root()
        rig._report(f"Building FK chain: creating {len(centerline)} bones…")
        arm, bones = rig.create_armature(centerline)
        # native bone-hierarchy FK: the deform bones ARE the controls; a curve custom shape per bone
        # makes each grabbable (rotating one carries its descendants through the connected chain).
        shape = rig._hidden_control_shape(
            f"{rig.rig_name}_fkshape", o["radius"], axis="x"
        )
        for bn in bones:
            arm.pose.bones[bn].custom_shape = shape
        rig._report("Building FK chain: binding skin…")
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        rig._report("FK chain build complete.")
        return TubeRigBundle(root, arm, bones, controls=list(bones))


# Strategy registry (mayatk's RIG_MODES) — extend with ``TubeStrategy.register``.
TUBE_STRATEGIES = {
    c.name: c for c in (SplineIKStrategy, AnchorStrategy, FKChainStrategy)
}
