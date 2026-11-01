# !/usr/bin/python
# coding=utf-8
"""Tube Rig build strategies (Blender) — mirror of mayatk's ``tube_rig/strategies.py``.

Each :class:`TubeStrategy` declares its options as Qt-free ``AttributeSpec``-shaped dicts and
orchestrates :class:`TubeRig` step methods; ``TUBE_STRATEGIES`` is the registry
(:meth:`TubeStrategy.register` extends it). :class:`TubeRigBundle` is the build result.
:data:`RIG_MODES` (``TubeRig.MODES``) is the modes tentacle's Tube Rig panel offers -- mirror of
mayatk's, describing these strategies.
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
    single source of the build defaults."""

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

    @staticmethod
    def centerline(rig, count, o: dict) -> list:
        """The rig's centerline, root first -- read from ``o["edges"]`` (a selected-edge
        override, mayatk's) when given, reversed when ``o["reverse"]``.

        ``reverse`` and ``edges`` are every strategy's, so neither is a declared option (the
        panel's one Reverse Direction toggle carries the first, as mayatk's does): the end the
        chain roots at -- the end that stays put -- swaps.
        """
        pts = rig.resolve_centerline(count, edges=o.get("edges"))
        return list(pts)[::-1] if o.get("reverse") else pts

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
            "label": "Joint Size",
            "kind": "float",
            "default": -1.0,
            "minimum": -1.0,
            "maximum": 1000.0,
            "decimals": 2,
            "tooltip": "Bone display radius; -1 (Auto) is half the measured tube radius. "
            "Controls always size to the tube radius (mayatk's radius).",
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
        centerline = self.centerline(rig, o["num_joints"], o)
        joint_radius, size = rig.resolve_sizes(centerline, o["radius"])
        root = rig.create_root()
        rig._report(f"Building spline IK: creating {len(centerline)} joints…")
        arm, bones = rig.create_armature(centerline, radius=joint_radius)
        rig._report("Building spline IK: creating curve, IK and controls…")
        # Step 2 (IK/controls + deform) is the shared attach_spline_rig — the same engine path
        # the step workflow's create_controls_from_selection drives, so the two can't diverge.
        curve, controls = rig.attach_spline_rig(
            arm,
            bones,
            num_controls=int(o["num_controls"]),
            radius=size,
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
            "label": "Joint Size",
            "kind": "float",
            "default": -1.0,
            "minimum": -1.0,
            "maximum": 1000.0,
            "decimals": 2,
            "tooltip": "Bone display radius; -1 (Auto) is half the measured tube radius. "
            "Controls always size to the tube radius (mayatk's radius).",
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
        centerline = self.centerline(rig, 2, o)
        start, end = centerline[0], centerline[-1]
        joint_radius, size = rig.resolve_sizes([start, end], o["radius"])
        root = rig.create_root()
        rig._report("Building anchor rig: creating end joints…")
        arm, bones = rig.create_armature([start, end], radius=joint_radius)
        rig._report("Building anchor rig: creating controls…")
        controls = rig.attach_anchor_rig(
            arm, bones, size=size, enable_stretch=o["enable_stretch"]
        )
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        return TubeRigBundle(root, arm, bones, controls=controls)


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
            "label": "Joint Size",
            "kind": "float",
            "default": -1.0,
            "minimum": -1.0,
            "maximum": 1000.0,
            "decimals": 2,
            "tooltip": "Bone display radius; -1 (Auto) is half the measured tube radius. "
            "Controls always size to the tube radius (mayatk's radius).",
        },
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        rig._report("Building FK chain: reading the tube's centerline…")
        centerline = self.centerline(rig, o["num_joints"], o)
        joint_radius, size = rig.resolve_sizes(centerline, o["radius"])
        root = rig.create_root()
        rig._report(f"Building FK chain: creating {len(centerline)} bones…")
        arm, bones = rig.create_armature(centerline, radius=joint_radius)
        rig.attach_fk_rig(arm, bones, size=size)
        rig._report("Building FK chain: binding skin…")
        RigUtils.bind_armature(rig.mesh, arm, auto_weights=True)
        rig._report("FK chain build complete.")
        return TubeRigBundle(root, arm, bones, controls=list(bones))


# Strategy registry (mayatk's ``TubeRig.STRATEGIES``) — extend with ``TubeStrategy.register``.
TUBE_STRATEGIES = {
    c.name: c for c in (SplineIKStrategy, AnchorStrategy, FKChainStrategy)
}


# ----------------------------------------------------------------------------
# Rig modes (what tentacle's Tube Rig panel offers per strategy)
# ----------------------------------------------------------------------------


@dataclass
class RigModeConfig:
    """A rig mode: its strategy, its option defaults, and which options it supports (an option a
    strategy implements no system for is not editable). Mirror of mayatk's."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # Which options the mode lets the user change (default: all).
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


#: The modes, in the panel's Mode combo order (the combo saves an index) -- mayatk's, with
#: Blender's own capabilities.
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=True,
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=False,  # one bone between two controls: no volume system
        squash_editable=False,  # ...nor squash
        auto_bend_editable=False,  # ...nor a mid control to bend
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        num_controls=5,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        # Bones-as-controls: every bone is a control, so there is no count to choose
        # (mayatk spreads N controls over the chain's spans).
        num_controls_editable=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
    ),
]
