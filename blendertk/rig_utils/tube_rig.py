# !/usr/bin/python
# coding=utf-8
"""Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies).

Rigs a tube-shaped mesh **multiple ways** through a strategy registry, mirroring Maya's
``TubeStrategy`` / ``RIG_MODES`` design (the structure the user asked to keep):

- :class:`SplineIKStrategy` — *Spline (Hose/Cable)*: a bone chain fit to a driver curve via Blender's
  **Spline IK** bone constraint; a few control Empties hook the curve's points (live reshape, the
  ``DynamicPipe`` pattern), optional stretch via the constraint's curve-fit scaling.
- :class:`AnchorStrategy` — *Anchor (Piston/Hydraulic)*: two end controls; one bone whose head
  follows the start control and which **Stretch-To**s (or **Damped-Track**s) the end control.
- :class:`FKChainStrategy` — *FK Chain (Tail/Tentacle)*: native bone-hierarchy FK — the deform bones
  **are** the controls (curve ``custom_shape``s make them grabbable; rotating one carries its
  descendants), the most Blender-idiomatic FK.

**Architecture (HYBRID, confirmed with the user):** each strategy *declares its options* as plain
Qt-free **dicts** (``AttributeSpec`` kwargs — see :data:`SplineIKStrategy.options`). The engine stays
Qt-free (Blender ``--background`` has no Qt binding, and ``uitk.bridge.spec`` imports Qt), so the
options data lives here while ``TubeRigSlots`` (Qt side) turns each dict into an ``AttributeSpec`` +
``make_widget`` to rebuild the options body per selected strategy. Adding a rig type = subclass
:class:`TubeStrategy` + :func:`register_strategy` + its option dicts — no ``.ui`` edits, the
extensibility Maya's registry gives.

Divergences vs Maya (documented for parity): Maya joints → Armature **bones** (``RigUtils`` armature
primitives), ``ikSplineSolver`` → the **Spline IK** constraint, ``skinCluster`` → **Armature-deform +
auto weights**, separate FK control objects → **bones-as-controls** with custom shapes. The
centerline comes from the shared :class:`~blendertk.rig_utils.tube_path.TubePath`. ``import bpy`` is
deferred into the call bodies.
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
    ]

    def build(self, rig, **opts):
        o = self.resolve(opts)
        centerline = rig.resolve_centerline(o["num_joints"])
        root = rig.create_root()
        arm, bones = rig.create_armature(centerline)
        curve = rig.build_curve(centerline, int(o["num_controls"]))
        RigUtils.parent_keep_transform(curve, root)
        RigUtils.add_spline_ik(
            arm, bones[-1], curve, chain_count=len(bones),
            y_scale_mode=("FIT_CURVE" if o["enable_stretch"] else "BONE_ORIGINAL"),
        )
        controls = rig.hook_curve_controls(curve, float(o["radius"]), root)
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
        shape = Controls.create(
            "circle", name=f"{rig.rig_name}_fkshape", size=float(o["radius"]),
            axis="x", collection=rig.collection,
        )
        # hide the shape SOURCE (a circle at the origin) — the bones still draw it as their custom
        # shape; without this it sits as clutter in the viewport. Parent it under the rig root so it
        # belongs to the rig (deleted with the group), not orphaned in the collection.
        shape.hide_viewport = True
        shape.hide_render = True
        RigUtils.parent_keep_transform(shape, root)
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
    def resolve_centerline(self, num_joints):
        """The tube's centerline (world points) for *num_joints*, raising if the mesh isn't a
        resolvable tube — the guard every strategy shares."""
        pts, _ = TubePath.get_centerline(self.mesh, int(num_joints))
        if len(pts) < 2:
            raise ValueError("TubeRig: could not extract a centerline from the mesh.")
        return pts

    def create_root(self):
        self._root = RigUtils.create_locator(
            f"{self.rig_name}_grp", display_type="ARROWS", collection=self.collection
        )
        return self._root

    def create_armature(self, centerline):
        """Armature + bone chain along *centerline*, parented under the rig root. Returns
        ``(armature_obj, bone_names)``."""
        arm = RigUtils.create_armature(f"{self.rig_name}_arm", collection=self.collection)
        bones = RigUtils.add_bone_chain(arm, centerline, prefix=f"{self.rig_name}_jnt")
        if self._root:
            RigUtils.parent_keep_transform(arm, self._root)
        return arm, bones

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("tube_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
