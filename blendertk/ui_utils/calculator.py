# !/usr/bin/python
# coding=utf-8
"""Calculator — the Blender half of the Calculator tool (mirror of mayatk's ``CalculatorController``).

An expression-based calculator with length-unit conversion and scene time helpers. The pure
engine (safe expression eval + unit conversion) is the **shared** ``pythontk.MathUtils``, as
in Maya; the time helpers read Blender's scene (``scene.render.fps`` / ``scene.frame_current``)
instead of Maya's. The panel is tentacle's shared ``slots/tools/calculator.py``, which calls
``self.tk.CalculatorController``. ``bpy`` is imported inside the methods that use it, so the
module surface imports without a running Blender.
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


