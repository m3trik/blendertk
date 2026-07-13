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
from blendertk.xform_utils._xform_utils import get_operation_axis_matrix


@_object_mode
def duplicate_linear(
    objects,
    num_copies,
    translate=(0, 0, 0),
    rotate=(0, 0, 0),
    scale=(1, 1, 1),
    weight_bias=0.5,
    weight_curve=4,
    pivot="object",
    calculation_mode="weighted",
    instance=True,
):
    """Duplicate object(s) along a linear path — mirror of mayatk's
    ``DuplicateLinear.duplicate_linear``.

    Each copy ``i`` interpolates toward the end-state ``translate`` / ``rotate`` / ``scale``
    by ``ptk.ProgressionCurves.calculate_progression_factor(i, num_copies, …)`` (the same
    shared math as Maya — the last copy gets the full offset). ``pivot`` is resolved via
    :func:`blendertk.xform_utils.get_operation_axis_matrix` (``"object"`` / ``"world"`` /
    ``"manip"`` / bbox locations / an explicit point — mirror of mayatk's
    ``XformUtils.get_operation_axis_matrix``). Returns ``{original: [copies]}``.
    """
    import bpy
    from mathutils import Euler, Matrix, Vector

    out = {}
    for src in (o for o in ptk.make_iterable(objects) if o):
        copies = []

        # Get the pivot matrix (Orientation + Position) using the centralized utility
        pivot_mat = get_operation_axis_matrix(src, pivot)
        pivot_inv = pivot_mat.inverted()
        m0 = src.matrix_world.copy()

        for i in range(num_copies):
            dup = _copy_object(src, instance=instance)

            # Calculate the transformation factor using the selected method
            f = ptk.ProgressionCurves.calculate_progression_factor(
                i, num_copies, weight_bias, weight_curve, calculation_mode
            )

            # Calculate transformations
            factors = [(abs(s) ** f) * (-1.0 if s < 0 else 1.0) for s in scale]

            # 1. Local scale
            scaled = m0 @ Matrix.Diagonal((*factors, 1.0))

            # 2. Rotate around Pivot — orbits the object around the pivot frame (Pos + Ori)
            rot = Euler([math.radians(r * f) for r in rotate], "XYZ").to_matrix().to_4x4()
            orbit = pivot_mat @ rot @ pivot_inv

            # 3. Apply Translation (World Space, but respecting Pivot Orientation) — rotate
            # the translation vector so it follows the pivot's axes (e.g. Manip axis).
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
    """Switchboard slot wiring for the Duplicate-Linear panel — 1:1 objectName mirror of
    mayatk's ``DuplicateLinearSlots`` (cross-host QSettings collision is host-namespaced by
    Switchboard/MainWindow now, so identical objectNames between the two copies of this panel
    are safe — no renumbering workaround needed here).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle. Unlike mayatk's Preview (node-diff + ``MUTATES_SELECTION``-gated preserve/
    restore), blendertk's ``Preview`` is snapshot-based (captures each object's datablock +
    matrix + collection links up front), so there is no Blender analogue of that flag here.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_linear
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[duplicate_linear] ")

        # Ensure preview cleanup triggers when resetting defaults
        self.ui.chk000.block_signals_on_restore = False

        # Populate pivot combobox — mirror of mayatk's XformUtils.get_pivot_options() list
        # (kept local: blendertk's XformUtils.get_pivot_options() backs move_to's narrower
        # pivot vocabulary, a different consumer). "manip" resolves to the 3D cursor and
        # "baked" has no Blender analogue (both handled in get_operation_axis_matrix / below).
        self.ui.cmb002.clear()
        self.pivot_options = [
            "object", "world", "center", "manip",
            "xmin", "xmax", "ymin", "ymax", "zmin", "zmax",
            "baked",
        ]
        self.ui.cmb002.add(self.pivot_options, prefix="Pivot:")
        # TODO(blender-parity): "baked" is Maya's rotate-pivot value baked distinct from the
        # transform's own origin — Blender objects carry a single origin, so there is no
        # analogous value to bake. Disable the item rather than dropping it from the list, so
        # the combo stays a 1:1 item-count mirror of mayatk's.
        baked_index = self.ui.cmb002.findData("baked")
        if baked_index >= 0:
            baked_item = self.ui.cmb002.model().item(baked_index)
            baked_item.setEnabled(False)
            baked_item.setToolTip(
                "Not supported in Blender: objects have a single origin, so there is no "
                "separate baked-pivot value to resolve."
            )

        # Populate calculation mode combobox
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

        # Set default calculation mode to "Weighted" to match tool defaults
        self.ui.cmb001.setAsCurrent("weighted")

        # Output mode: independent copies vs shared-data instances (was the "Inst"
        # checkbox). Instance is the default, matching the prior checked state.
        self.ui.cmb_inst.clear()
        self.ui.cmb_inst.add([("Copy", "copy"), ("Instance", "instance")])
        self.ui.cmb_inst.setAsCurrent("instance")

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Duplicate Linear",
        )
        self.sb.connect_multi(
            self.ui,
            "s000-11",
            "valueChanged",
            self.preview.refresh,
        )
        # Connect pivot combobox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "cmb002",
            "currentIndexChanged",
            self.preview.refresh,
        )
        # Connect calculation mode combobox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "cmb001",
            "currentIndexChanged",
            self.preview.refresh,
        )
        # Connect calculation mode combobox to toggle_weight_ui logic
        self.sb.connect_multi(
            self.ui,
            "cmb001",
            "currentIndexChanged",
            self.toggle_weight_ui,
        )

        # Connect output-mode combobox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "cmb_inst",
            "currentIndexChanged",
            self.preview.refresh,
        )

        # Initialize the UI state
        self.toggle_weight_ui()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Duplicate Linear",
                body="Duplicate selected objects along a linear path with "
                "per-copy translate, rotate, and scale offsets.",
                steps=[
                    "Select one or more objects.",
                    "Set <b>Copies</b> and the end-state <b>Translate</b> / "
                    "<b>Rotate</b> / <b>Scale</b> offsets — each copy interpolates "
                    "from source to that final offset.",
                    "Pick an interpolation <b>Mode</b> (linear, ease in/out, "
                    "weighted, sine, bounce, elastic, …).",
                    "Pick a <b>Pivot</b>.",
                    "Toggle <b>Preview</b>, then <b>Duplicate</b> to commit.",
                ],
                notes=[
                    "<b>Weight Bias</b> only applies to the <i>weighted</i> mode; "
                    "<b>Weight Curve</b> applies to non-linear modes. Disabled "
                    "spinners are simply ignored by the current mode.",
                    "<b>Inst</b> makes linked duplicates (shared mesh data — "
                    "edits propagate) instead of full copies.",
                ],
            )
        )

    def toggle_weight_ui(self):
        """Disable weight UI components if the current calculation mode doesn't use them."""
        # Modes that don't typically use bias/curve parameters
        # Based on pythontk.math_utils.progression.ProgressionCurves
        mode = self.ui.cmb001.currentData()

        # 'linear' uses neither
        # 'exponential' uses weight_curve
        # 'logarithmic' uses weight_curve
        # 'sine' uses weight_curve
        # 'ease_in' uses weight_curve (power)
        # 'ease_out' uses weight_curve
        # 'ease_in_out' uses weight_curve
        # 'smooth_step' uses neither (it's fixed hermite 3x^2 - 2x^3)
        # 'bounce' uses weight_curve (bounciness?)
        # 'elastic' uses weight_curve (period/amplitude?)
        # 'weighted' uses BOTH weight_bias and weight_curve

        # Define which modes need what
        # (This is a simplified assumption based on typical usage)
        uses_curve = mode not in ["linear", "smooth_step"]
        uses_bias = mode in ["weighted"]

        self.ui.s010.setEnabled(uses_bias)  # Weight Bias
        self.ui.s011.setEnabled(uses_curve)  # Weight Curve

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
        """Perform the linear duplication operation."""
        num_copies = self.ui.s009.value() - 1  # Include the orig object in the count
        translate = (
            self.ui.s000.value(),
            self.ui.s001.value(),
            self.ui.s002.value(),
        )
        rotate = (
            self.ui.s003.value(),
            self.ui.s004.value(),
            self.ui.s005.value(),
        )
        scale = (
            self.ui.s006.value(),
            self.ui.s007.value(),
            self.ui.s008.value(),
        )
        weight_bias = self.ui.s010.value()
        weight_curve = self.ui.s011.value()

        # Get pivot from dropdown
        pivot = self.ui.cmb002.currentData()

        # Get calculation mode from dropdown
        calculation_mode = self.ui.cmb001.currentData()

        # Get instance mode from checkbox
        instance = self.ui.cmb_inst.currentData() == "instance"

        self.copies = duplicate_linear(
            objects,
            num_copies,
            translate,
            rotate,
            scale,
            weight_bias,
            weight_curve,
            pivot,
            calculation_mode,
            instance,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("duplicate_linear", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
