# !/usr/bin/python
# coding=utf-8
"""Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.

``duplicate_radial`` orbits copies about a pivot via ``mathutils`` matrices (no temp groups);
``DuplicateRadialSlots`` is the Switchboard wiring for the co-located ``duplicate_radial.ui``
panel, discovered + served by ``BlenderUiHandler`` (``marking_menu.show("duplicate_radial")``).

``import bpy`` / ``mathutils`` (and the Qt-only ``uitk`` helpers) are deferred into the call
bodies (no import side effects; headless Blender ships no Qt binding).
"""
import math

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import (
    _AXIS_INDEX,
    _copy_object,
    _group_under_empty,
    _join_copies,
)
from blendertk.edit_utils.naming._naming import Naming


def _radial_factor(x, weight_bias, weight_curve):
    """Maya's radial weighting: lerp between linear and a bias-curved progression."""
    curve = min(max(weight_curve, 0.0), 0.999)  # 1.0 would divide by zero
    weight_factor = 2 * abs(weight_bias - 0.5)
    curve_value = (
        x ** (1 / (1 - curve))
        if weight_bias >= 0.5
        else 1 - (1 - x) ** (1 / (1 - curve))
    )
    return (1 - weight_factor) * x + weight_factor * curve_value


@_object_mode
def duplicate_radial(
    objects,
    num_copies,
    start_angle=0.0,
    end_angle=360.0,
    weight_bias=0.5,
    weight_curve=0.5,
    rotate_axis="y",
    offset=(0, 0, 0),
    translate=(0, 0, 0),
    rotate=(0, 0, 0),
    scale=(1, 1, 1),
    pivot="object",
    keep_original=False,
    instance=False,
    combine=False,
    suffix=True,
):
    """Duplicate object(s) in a radial pattern — mirror of mayatk's
    ``DuplicateRadial.duplicate_radial``.

    Copies orbit the pivot point (``"object"`` = the source's origin, ``"world"``, or a
    world ``(x, y, z)`` point; ``offset`` shifts it) about the **world** ``rotate_axis``.
    A full 360° sweep drops the shared endpoint so the last copy doesn't stack on the
    first. The per-copy ``rotate`` / ``scale`` are applied in the source's local frame and
    ``translate`` ramps in world space, like Maya. ``combine`` joins the copies into one
    mesh; otherwise they're grouped under an Empty (``<name>_array``). ``keep_original``
    ``False`` removes the source once the pattern is built. ``suffix`` renames the finished
    copy/copies with a location-based suffix (``Naming.append_location_based_suffix``,
    alphabetical, ordered by distance from the source's origin), mirroring Maya. Returns
    ``{name: [copies]}``.
    """
    import bpy
    from mathutils import Euler, Matrix, Vector

    if rotate_axis not in ("x", "y", "z"):
        raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")
    axis_vec = Vector((0.0, 0.0, 0.0))
    axis_vec[_AXIS_INDEX[rotate_axis]] = 1.0

    total_rotation = end_angle - start_angle
    remainder = abs(total_rotation) % 360.0
    is_full_revolution = (
        abs(total_rotation) > 1e-6 and min(remainder, 360.0 - remainder) < 1e-6
    )
    span_divisor = num_copies if is_full_revolution else max(num_copies - 1, 1)

    out = {}
    for src in (o for o in ptk.make_iterable(objects) if o):
        # the "driven" base transform: the uniform per-copy rotate/scale in the local frame
        # (Maya's initial-transformations step; translate ramps per copy instead — Maya also
        # pre-shifts every copy by the full translate, a double-application quirk not mirrored)
        rot = Euler([math.radians(r) for r in rotate], "XYZ").to_matrix().to_4x4()
        scl = Matrix.Diagonal((*scale, 1.0))
        base = src.matrix_world @ rot @ scl

        if pivot == "world":
            pivot_pos = Vector((0.0, 0.0, 0.0))
        elif isinstance(pivot, (tuple, list)) and len(pivot) == 3:
            pivot_pos = Vector(pivot)
        else:  # "object"
            pivot_pos = src.matrix_world.translation.copy()
        pivot_pos = pivot_pos + Vector(offset)

        copies = []
        for i in range(num_copies):
            x = i / span_divisor if num_copies > 1 else 0.0
            f = _radial_factor(x, weight_bias, weight_curve)
            angle = math.radians(start_angle + total_rotation * f)
            orbit = (
                Matrix.Translation(pivot_pos)
                @ Matrix.Rotation(angle, 4, axis_vec)
                @ Matrix.Translation(-pivot_pos)
            )
            dup = _copy_object(src, instance=instance)
            t = Vector([v * f for v in translate])
            dup.matrix_world = Matrix.Translation(t) @ orbit @ base
            copies.append(dup)

        name = src.name
        if not copies:  # num_copies <= 0: no-op, don't destroy the source
            out[name] = []
            continue
        if not keep_original:
            data = src.data
            bpy.data.objects.remove(src, do_unlink=True)
            if data is not None and data.users == 0:  # don't leak an orphaned datablock
                bpy.data.batch_remove([data])
        if combine:
            copies = [_join_copies(copies, f"{name}_array")]
        else:
            _group_under_empty(copies, f"{name}_array")
        if suffix:
            Naming.append_location_based_suffix(
                copies, first_obj_as_ref=True, alphabetical=True
            )
        out[name] = copies
    bpy.context.view_layer.update()
    return out


class DuplicateRadial:
    """Namespace mirror of mayatk's ``DuplicateRadial`` (helper also exposed module-level)."""

    duplicate_radial = staticmethod(duplicate_radial)


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class DuplicateRadialSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Duplicate-Radial panel.

    Copies are grouped under an Empty (``<name>_array``), so no Maya-style regroup finalize
    step is needed. Self-contained (``ptk.LoggingMixin`` only).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_radial
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[duplicate_radial] ")

        # Output mode: independent copies vs shared-data instances (was the
        # "Instance" checkbox). Copy is the default, matching the prior unchecked state.
        self.ui.cmb_inst.clear()
        self.ui.cmb_inst.add([("Copy", "copy"), ("Instance", "instance")])
        self.ui.cmb_inst.setAsCurrent("copy")

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Duplicate Radial",
        )
        self.sb.connect_multi(self.ui, "s000-16", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(self.ui, "chk002-4", "toggled", self.preview.refresh)
        self.sb.connect_multi(self.ui, "chk006-8", "toggled", self.preview.refresh)
        self.sb.connect_multi(self.ui, "cmb_inst", "currentIndexChanged", self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Duplicate Radial",
                body="Duplicate selected objects in a radial / circular pattern "
                "around a chosen pivot.",
                steps=[
                    "Select one or more objects.",
                    "Set <b>Copies</b>, <b>Start Angle</b>, <b>End Angle</b>, and "
                    "the <b>Rotate Axis</b> (X / Y / Z).",
                    "Pick the <b>Pivot</b> — Object or World.",
                    "Optionally set per-copy <b>Translate</b> / <b>Rotate</b> / "
                    "<b>Scale</b> offsets and a <b>Pivot Offset</b>.",
                    "Toggle <b>Preview</b>, then <b>Duplicate</b> to commit.",
                ],
                sections=[
                    ("Options", [
                        "<b>Instance</b> — linked duplicates sharing one mesh "
                        "(cheaper; edits propagate).",
                        "<b>Keep Original</b> — leave the source object in place "
                        "(off discards it after the pattern is built).",
                        "<b>Combine</b> — merge the result into a single mesh.",
                        "<b>Suffix</b> — append a location-based alphabetical "
                        "suffix to the copy names.",
                    ]),
                ],
                notes=[
                    "<b>Weight Bias</b> and <b>Weight Curve</b> control non-uniform "
                    "angular spacing between start and end angle.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
        num_copies = self.ui.s009.value()
        if num_copies < 1:
            raise ValueError("Set Copies to at least 1.")
        duplicate_radial(
            objects,
            num_copies,
            start_angle=self.ui.s013.value(),
            end_angle=self.ui.s014.value(),
            weight_bias=self.ui.s015.value(),
            weight_curve=self.ui.s016.value(),
            rotate_axis=(
                "x"
                if self.ui.chk002.isChecked()
                else "y" if self.ui.chk003.isChecked() else "z"
            ),
            offset=(self.ui.s010.value(), self.ui.s011.value(), self.ui.s012.value()),
            translate=(self.ui.s000.value(), self.ui.s001.value(), self.ui.s002.value()),
            rotate=(self.ui.s003.value(), self.ui.s004.value(), self.ui.s005.value()),
            scale=(self.ui.s006.value(), self.ui.s007.value(), self.ui.s008.value()),
            pivot=self._resolve_pivot(self.ui.cmb000.currentIndex()),
            keep_original=self.ui.chk006.isChecked(),
            instance=self.ui.cmb_inst.currentData() == "instance",
            combine=self.ui.chk007.isChecked(),
            suffix=self.ui.chk008.isChecked(),
        )

    @staticmethod
    def _resolve_pivot(pivot_index: int) -> str:
        return {0: "object", 1: "world"}.get(pivot_index, "object")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("duplicate_radial", reload=True)
    ui.show(pos="screen", app_exec=True)
