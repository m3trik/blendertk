# !/usr/bin/python
# coding=utf-8
"""Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.

``duplicate_linear`` is pure ``mathutils`` matrix math over the shared
``ptk.ProgressionCurves`` (no temp nodes, fully headless-testable); ``DuplicateLinearSlots`` is
the Switchboard wiring for the co-located ``duplicate_linear.ui`` panel, discovered + served by
``BlenderUiHandler`` (``marking_menu.show("duplicate_linear")``).

``import bpy`` / ``mathutils`` (and the Qt-only ``uitk`` helpers) are deferred into the call
bodies (no import side effects; headless Blender ships no Qt binding).
"""
import math

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _copy_object


@_object_mode
def duplicate_linear(
    objects,
    num_copies,
    translate=(0, 0, 0),
    rotate=(0, 0, 0),
    scale=(1, 1, 1),
    weight_bias=0.5,
    weight_curve=4.0,
    pivot="object",
    calculation_mode="weighted",
    instance=True,
):
    """Duplicate object(s) along a linear path — mirror of mayatk's
    ``DuplicateLinear.duplicate_linear``.

    Each copy ``i`` interpolates toward the end-state ``translate`` / ``rotate`` / ``scale``
    by ``ptk.ProgressionCurves.calculate_progression_factor(i, num_copies, …)`` (the same
    shared math as Maya — the last copy gets the full offset). ``pivot``: ``"object"`` (the
    source's own frame — translate/orbit follow its local axes) or ``"world"``.
    Returns ``{original: [copies]}``.
    """
    import bpy
    from mathutils import Euler, Matrix, Vector

    out = {}
    for src in (o for o in ptk.make_iterable(objects) if o):
        copies = []
        pivot_mat = (
            src.matrix_world.normalized() if pivot == "object" else Matrix.Identity(4)
        )  # orientation + position only — scale must not leak into the pivot frame
        pivot_inv = pivot_mat.inverted()
        m0 = src.matrix_world.copy()
        for i in range(num_copies):
            f = ptk.ProgressionCurves.calculate_progression_factor(
                i, num_copies, weight_bias, weight_curve, calculation_mode
            )
            dup = _copy_object(src, instance=instance)
            # 1. local scale (sign-preserving exponential ramp, mirroring Maya)
            factors = [(abs(s) ** f) * (-1.0 if s < 0 else 1.0) for s in scale]
            scaled = m0 @ Matrix.Diagonal((*factors, 1.0))
            # 2. orbit about the pivot frame
            rot = Euler([math.radians(r * f) for r in rotate], "XYZ").to_matrix().to_4x4()
            orbit = pivot_mat @ rot @ pivot_inv
            # 3. translation along the pivot frame's axes
            t = pivot_mat.to_3x3() @ Vector([v * f for v in translate])
            dup.matrix_world = Matrix.Translation(t) @ orbit @ scaled
            copies.append(dup)
        out[src] = copies
    bpy.context.view_layer.update()
    return out


class DuplicateLinear:
    """Namespace mirror of mayatk's ``DuplicateLinear`` (helper also exposed module-level)."""

    duplicate_linear = staticmethod(duplicate_linear)


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class DuplicateLinearSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Duplicate-Linear panel.

    ``Instance`` maps to Blender linked duplicates. The pivot combo is Blender-specific
    (``cmb003`` — Object / World, no Manip). Self-contained (``ptk.LoggingMixin`` only) so
    blendertk carries no back-dependency on tentacle.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_linear
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[duplicate_linear] ")

        # Ensure preview cleanup triggers when resetting defaults
        self.ui.chk000.block_signals_on_restore = False

        self.ui.cmb003.clear()
        self.ui.cmb003.add(
            [("Object", "object"), ("World", "world")], prefix="Pivot:"
        )

        self.ui.cmb001.clear()
        self.interpolation_modes = [
            ("Linear", "linear"),
            ("Ease In", "ease_in"),
            ("Ease Out", "ease_out"),
            ("Ease In-Out", "ease_in_out"),
            ("Exponential", "exponential"),
            ("Smooth Step", "smooth_step"),
            ("Weighted", "weighted"),
        ]
        self.ui.cmb001.add(self.interpolation_modes, prefix="Interpolation:")
        self.ui.cmb001.setAsCurrent("weighted")

        self.ui.chk001.setChecked(True)  # default to instanced (linked) duplicates

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Duplicate Linear",
        )
        self.sb.connect_multi(self.ui, "s000-11", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(
            self.ui, "cmb003", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(
            self.ui, "cmb001", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(
            self.ui, "cmb001", "currentIndexChanged", self.toggle_weight_ui
        )
        self.sb.connect_multi(self.ui, "chk001", "stateChanged", self.preview.refresh)

        self.toggle_weight_ui()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Duplicate Linear",
                body="Duplicate selected objects along a linear path with per-copy "
                "translate, rotate, and scale offsets.",
                steps=[
                    "Select one or more objects.",
                    "Set <b>Copies</b> and the end-state <b>Translate</b> / "
                    "<b>Rotate</b> / <b>Scale</b> offsets — each copy interpolates "
                    "from source to that final offset.",
                    "Pick an interpolation <b>Mode</b> and a <b>Pivot</b>.",
                    "Toggle <b>Preview</b>, then <b>Duplicate</b> to commit.",
                ],
                notes=[
                    "<b>Instance</b> makes linked duplicates (shared mesh data — "
                    "edits propagate).",
                    "<b>Weight Bias</b> only applies to the <i>weighted</i> mode; "
                    "<b>Weight Curve</b> applies to non-linear modes.",
                ],
            )
        )

    def toggle_weight_ui(self):
        """Disable the weight spinners for modes that don't use them."""
        mode = self.ui.cmb001.currentData()
        uses_curve = mode not in ("linear", "smooth_step")
        uses_bias = mode == "weighted"
        self.ui.s010.setEnabled(uses_bias)  # Weight Bias
        self.ui.s011.setEnabled(uses_curve)  # Weight Curve

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
        num_copies = self.ui.s009.value() - 1  # the count includes the original
        if num_copies < 1:
            raise ValueError("Set Copies to at least 2 (the count includes the original).")
        duplicate_linear(
            objects,
            num_copies,
            translate=(self.ui.s000.value(), self.ui.s001.value(), self.ui.s002.value()),
            rotate=(self.ui.s003.value(), self.ui.s004.value(), self.ui.s005.value()),
            scale=(self.ui.s006.value(), self.ui.s007.value(), self.ui.s008.value()),
            weight_bias=self.ui.s010.value(),
            weight_curve=self.ui.s011.value(),
            pivot=self.ui.cmb003.currentData(),
            calculation_mode=self.ui.cmb001.currentData(),
            instance=self.ui.chk001.isChecked(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("duplicate_linear", reload=True)
    ui.show(pos="screen", app_exec=True)
