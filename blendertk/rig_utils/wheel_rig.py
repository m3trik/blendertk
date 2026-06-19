# !/usr/bin/python
# coding=utf-8
"""Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.

Blender port of mayatk's ``rig_utils.wheel_rig`` (``btk.WheelRig`` ↔ ``mtk.WheelRig``): make wheels
auto-rotate from a control's travel (rolling). Maya wires this with an ``expression`` node reading
``distance / circumference`` (plus keyable ``wheelHeight`` / ``enableRotation`` / ``spinDirection``
control attrs and a left/right auto-flip); the Blender analogue is a ``rotation_euler`` **driver**
(``rotation = 2·travel / height`` radians — equal to Maya's ``distance/(π·height)·360°``) reading the
control's WORLD-space travel (so parent movement is captured without Maya's ``decomposeMatrix`` node),
with the same three control attrs as keyable custom properties and the same dot-product auto-flip.

Built on the shared ``RigUtils`` base (constraints / drivers / custom-prop vars). ``import bpy`` is
deferred into the call bodies and the Qt-only ``uitk`` helper into its method.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import undo_checkpoint
from blendertk.rig_utils._rig_utils import RigUtils


class WheelRig(ptk.LoggingMixin):
    """Auto-rolling wheel rig (mirror of mayatk's ``WheelRig``)."""

    # combo index -> (control travel channel, wheel rotation_euler index). Mirrors Maya's combo
    # (Move X→Rotate Z, Y→Y, Z→X); the user picks the pair matching their wheel's axle orientation.
    _AXIS_MAP = {0: ("LOC_X", 2), 1: ("LOC_Y", 1), 2: ("LOC_Z", 0)}

    def __init__(self, control, wheels, rig_name=None):
        super().__init__()
        self.control = RigUtils.resolve_object(control)
        self.wheels = [w for w in (RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels)) if w]
        if self.control is None or not self.wheels:
            raise ValueError("Invalid control or wheel inputs.")
        # Re-entrancy: reuse the stamped rig id so a rebuild updates rather than duplicates.
        self._rig_name = rig_name or self.control.get("wheel_rig_id") or "wheel_rig"
        self.control["wheel_rig_id"] = self._rig_name

    @property
    def rig_name(self):
        return self._rig_name

    @undo_checkpoint
    def rig_rotation(self, movement_axis="LOC_Z", rotation_index=None, wheel_height=1.0, wheels=None):
        """Drive each wheel's rotation from the control's travel along ``movement_axis``.

        Parameters:
            movement_axis (str): Control travel channel — ``"LOC_X"`` / ``"LOC_Y"`` / ``"LOC_Z"``.
            rotation_index (int): Wheel ``rotation_euler`` index (0=X, 1=Y, 2=Z). Auto-inferred
                from ``movement_axis`` when ``None`` (Maya's vehicle-dynamics mapping).
            wheel_height (float): Wheel **diameter** (rotation = ``2·travel / height`` radians).
            wheels (list): Override the wheels (defaults to the constructor's).

        Returns:
            list: the wheel objects that were rigged.
        """
        import bpy

        wheels = self.wheels if wheels is None else [
            w for w in (RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels)) if w
        ]
        if not wheels:
            raise ValueError("No wheels specified.")
        if wheel_height <= 0:
            raise ValueError(f"Invalid wheel height: {wheel_height}")
        if rotation_index is None:
            rotation_index = {"LOC_Z": 0, "LOC_X": 2, "LOC_Y": 1}.get(movement_axis, 0)

        # Keyable control attrs (Maya parity): seed if absent, set wheelHeight to the request.
        RigUtils.ensure_custom_prop(self.control, "wheelHeight", float(wheel_height), min_value=0.001)
        self.control["wheelHeight"] = float(wheel_height)
        RigUtils.ensure_custom_prop(self.control, "enableRotation", 1.0, min_value=0.0, max_value=1.0)
        RigUtils.ensure_custom_prop(self.control, "spinDirection", 1.0)

        bpy.context.view_layer.update()
        ctrl_axis = self.control.matrix_world.col[rotation_index].to_3d()
        for wheel in wheels:
            # Auto-flip mirrored wheels: opposite rotation-axis world direction -> negate.
            wheel_axis = wheel.matrix_world.col[rotation_index].to_3d()
            auto_flip = -1.0 if ctrl_axis.dot(wheel_axis) < 0 else 1.0
            RigUtils.remove_driver(wheel, "rotation_euler", rotation_index)  # re-entrant
            # Build the driver + ALL variables BEFORE the expression — an expression that
            # references a not-yet-added variable evaluates it as 0 (div-by-zero) and sticks.
            fc = RigUtils.add_transform_driver(
                wheel, "rotation_euler", rotation_index, self.control, movement_axis, var_name="travel"
            )
            RigUtils.add_prop_var(fc, "height", self.control, '["wheelHeight"]')
            RigUtils.add_prop_var(fc, "enable", self.control, '["enableRotation"]')
            RigUtils.add_prop_var(fc, "direction", self.control, '["spinDirection"]')
            # radians = 2*travel/height (== Maya's distance/(pi*height)*360deg). Pure arithmetic
            # (no ternary -> stays on Blender's fast driver parser, no full-Python security gate);
            # height can't hit 0 (custom-prop min=0.001).
            fc.driver.expression = f"enable * direction * {auto_flip} * 2.0 * travel / height"

        RigUtils.refresh_drivers(wheels)  # post-build recompile (script-built driver gotcha)
        self.logger.success(f"Wheel rig '{self.rig_name}' rigged {len(wheels)} wheel(s).")
        return wheels


class WheelRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Wheel Rig panel.

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``.
    """

    _AXIS_ITEMS = ["Move X → Rotate Z", "Move Y → Rotate Y", "Move Z → Rotate X"]

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.wheel_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[wheel_rig] ")
        self.ui.b000.clicked.connect(self.wheel_rig)
        # Vestigial widgets in the shared .ui (Maya removes them at runtime). Hide rather than
        # deleteLater (the runtime loader can invalidate deleted-widget wrappers).
        for name in ("cmb001", "chk000", "chk010"):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setVisible(False)

    def cmb000_init(self, widget):
        widget.add(self._AXIS_ITEMS, header="Roll Axis:")
        if hasattr(widget, "setCurrentIndex"):
            widget.setCurrentIndex(2)  # default Move Z -> Rotate X (Maya's default)

    def s000_init(self, widget):
        # s000 is a LineEdit (wheel diameter); seed a sensible default.
        if hasattr(widget, "text") and not widget.text():
            widget.setText("1.0")

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Wheel Rig",
                body="Auto-rotate wheels from a control's travel (rolling), via a driver on each "
                "wheel's rotation.",
                steps=[
                    "Select the <b>wheel(s)</b>, then the <b>control/driver LAST</b> (active).",
                    "Pick the <b>Roll Axis</b> (control travel → wheel rotation) and set the wheel "
                    "<b>diameter</b>.",
                    "Press <b>Rig Rotation</b>. Re-running updates the rig in place.",
                ],
                notes=[
                    "Tweak <i>wheelHeight</i> / <i>enableRotation</i> / <i>spinDirection</i> live "
                    "on the control's Custom Properties.",
                    "Mirrored (left/right) wheels are auto-flipped so both roll forward.",
                ],
            )
        )

    def wheel_rig(self):
        import bpy

        sel = [o for o in (bpy.context.selected_objects or []) if o]
        control = bpy.context.view_layer.objects.active
        if len(sel) < 2 or control is None or control not in sel:
            self.sb.message_box(
                "Wheel Rig needs the wheel(s) selected plus the control/driver LAST (active)."
            )
            return
        wheels = [o for o in sel if o is not control]
        try:
            height = float(self.ui.s000.text() or 1.0)
        except (ValueError, AttributeError):
            height = 1.0
        movement_axis, rot_idx = WheelRig._AXIS_MAP.get(self.ui.cmb000.currentIndex(), ("LOC_Z", 0))
        try:
            name = (self.ui.txt000.text() or None) if hasattr(self.ui, "txt000") else None
            rig = WheelRig(control, wheels, rig_name=name)
            rig.rig_rotation(movement_axis=movement_axis, rotation_index=rot_idx, wheel_height=height)
        except Exception as e:
            self.sb.message_box(f"Error setting up wheel rig: {e}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("wheel_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
