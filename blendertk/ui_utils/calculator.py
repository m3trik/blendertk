# !/usr/bin/python
# coding=utf-8
"""Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.

Blender counterpart of mayatk's Calculator: an expression-based calculator with length-unit
conversion and DCC time helpers. The pure engine (safe expression eval + unit conversion) is
the **shared** ``pythontk.MathUtils`` — identical to the Maya panel, no duplication — while the
time helpers read Blender's scene (``scene.render.fps`` / ``scene.frame_current``) instead of
Maya's. Served by ``BlenderUiHandler`` (``marking_menu.show("calculator")``).

Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on tentacle.
The Qt-only (``qtpy``/``uitk``) and ``bpy`` imports are deferred into their methods (headless
Blender ships no Qt binding and the module surface must import without a running Blender).

The ``.ui`` is a verbatim copy of mayatk's — same objectNames (including ``maya_container`` /
``grid_maya`` for the animation-helpers group), same widget tree, same layout — a true 1:1 mirror.
Identical objectNames between the mayatk and blendertk copies of this panel are safe: uitk's
``Switchboard.add_ui`` / ``MainWindow._relative_state`` host-namespace the QSettings branch by
context tag (``mirror_maya`` vs ``mirror_blender``), so the two panels never collide.
"""

import pythontk as ptk


class CalculatorController:
    """DCC-agnostic math engine + Blender time helpers.

    ``calculate`` / ``convert_unit`` delegate to ``pythontk.MathUtils`` (the same engine the
    Maya panel uses); only the frame/second helpers are Blender-specific.
    """

    @staticmethod
    def calculate(expression):
        """Safely evaluate a math expression (delegates to the shared engine)."""
        return ptk.MathUtils.eval_expression(expression)

    @staticmethod
    def convert_unit(value, from_unit, to_unit):
        """Convert a length value between units (delegates to the shared engine)."""
        return ptk.MathUtils.convert_length_unit(value, from_unit, to_unit)

    @staticmethod
    def get_fps_value():
        """Scene frame rate (falls back to 24.0)."""
        import bpy

        try:
            scene = bpy.context.scene
            # fps_base accounts for fractional rates (e.g. 29.97 = 30 / 1.001).
            return float(scene.render.fps) / float(scene.render.fps_base)
        except Exception:
            return 24.0

    @staticmethod
    def get_current_time():
        """Current frame as a string."""
        import bpy

        try:
            return str(bpy.context.scene.frame_current)
        except Exception:
            return "0"

    @classmethod
    def frames_to_sec(cls, frames):
        try:
            return str(round(frames / cls.get_fps_value(), 4))
        except Exception:
            return "Error"

    @classmethod
    def sec_to_frames(cls, seconds):
        try:
            return str(round(seconds * cls.get_fps_value(), 2))
        except Exception:
            return "Error"


