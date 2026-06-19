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


class CalculatorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Calculator panel.

    Same widget names as the mayatk panel (``txt_display``/``calc_container``/``grp_units``/
    ``cmb_unit_*``/``btn_convert``); the animation-helpers group is ``dcc_container`` (Blender's
    scene FPS / current frame) in place of Maya's ``maya_container``.
    """

    _CALC_BUTTONS = [
        ("C", 0, 0), ("(", 0, 1), (")", 0, 2), ("/", 0, 3),
        ("7", 1, 0), ("8", 1, 1), ("9", 1, 2), ("*", 1, 3),
        ("4", 2, 0), ("5", 2, 1), ("6", 2, 2), ("-", 2, 3),
        ("1", 3, 0), ("2", 3, 1), ("3", 3, 2), ("+", 3, 3),
        ("0", 4, 0), (".", 4, 1), ("<", 4, 2), ("=", 4, 3),
    ]
    _UNITS = ["mm", "cm", "m", "km", "in", "ft", "yd", "mi"]

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.calculator
        self.controller = CalculatorController()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[calculator] ")

        self._init_calc_grid()
        self._init_dcc_grid()
        self._init_units()

        self.ui.txt_display.returnPressed.connect(self.on_equal)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Calculator",
                body="Expression-based calculator with unit conversion and "
                "Blender time helpers.",
                sections=[
                    ("Expression entry", [
                        "Type or click button keys to build an expression in "
                        "the display field.",
                        "Press <b>=</b> or <b>Enter</b> to evaluate.",
                        "Standard math operators plus functions: "
                        "<code>sin</code>, <code>cos</code>, <code>tan</code>, "
                        "<code>sqrt</code>, <code>pow</code>, <code>pi</code>, "
                        "etc.",
                    ]),
                    ("Unit conversion", [
                        "Pick <b>From</b> and <b>To</b> units (mm, cm, m, km, "
                        "in, ft, yd, mi).",
                        "Press <b>Convert</b> to convert the current display "
                        "value.",
                    ]),
                    ("Animation helpers", [
                        "<b>Get FPS</b> — read the scene's frame rate.",
                        "<b>Get Time</b> — read the current frame.",
                        "<b>Frames → Sec</b> / <b>Sec → Frames</b> — convert "
                        "the display value using the scene FPS.",
                    ]),
                ],
            )
        )

    # ------------------------------------------------------------------ grid builders
    def _init_calc_grid(self):
        from qtpy import QtWidgets

        for text, r, c in self._CALC_BUTTONS:
            btn = QtWidgets.QPushButton(text)
            btn.setMinimumHeight(35)
            font = btn.font()
            font.setPointSize(10)
            btn.setFont(font)
            self.ui.calc_container.layout().addWidget(btn, r, c)

            if text == "=":
                btn.clicked.connect(self.on_equal)
            elif text == "C":
                btn.clicked.connect(self.on_clear)
            elif text == "<":
                btn.clicked.connect(self.on_backspace)
            else:
                btn.clicked.connect(lambda checked=False, t=text: self.on_input(t))

    def _init_dcc_grid(self):
        from qtpy import QtWidgets

        dcc_buttons = [
            ("Get FPS", self.get_fps),
            ("Get Time", self.get_current_time),
            ("Frames -> Sec", self.frames_to_sec),
            ("Sec -> Frames", self.sec_to_frames),
        ]
        cols = 2
        for i, (text, func) in enumerate(dcc_buttons):
            btn = QtWidgets.QPushButton(text)
            btn.setMinimumHeight(30)
            btn.clicked.connect(func)
            self.ui.dcc_container.layout().addWidget(btn, i // cols, i % cols)

    def _init_units(self):
        self.ui.cmb_unit_from.addItems(self._UNITS)
        self.ui.cmb_unit_to.addItems(self._UNITS)
        self.ui.cmb_unit_from.setCurrentText("cm")
        self.ui.cmb_unit_to.setCurrentText("m")
        self.ui.btn_convert.clicked.connect(self.on_convert_units)

    # ------------------------------------------------------------------ calculator actions
    def on_input(self, text):
        self.ui.txt_display.setText(self.ui.txt_display.text() + text)

    def on_clear(self):
        self.ui.txt_display.clear()

    def on_backspace(self):
        current = self.ui.txt_display.text()
        if current:
            self.ui.txt_display.setText(current[:-1])

    def on_equal(self):
        result = self.controller.calculate(self.ui.txt_display.text())
        self.ui.txt_display.setText(result)

    def _resolve_display_value(self):
        """Evaluate the display as an expression; return a float, or ``None`` on error
        (in which case the display is set to ``"Error"``). A failed eval returns ``"Error"``
        from the engine, so ``float`` raising ``ValueError`` covers both bad cases."""
        try:
            return float(self.controller.calculate(self.ui.txt_display.text()))
        except ValueError:
            self.ui.txt_display.setText("Error")
            return None

    def on_convert_units(self):
        if not self.ui.txt_display.text():
            return
        value = self._resolve_display_value()
        if value is None:
            return
        self.ui.txt_display.setText(
            self.controller.convert_unit(
                value,
                self.ui.cmb_unit_from.currentText(),
                self.ui.cmb_unit_to.currentText(),
            )
        )

    # ------------------------------------------------------------------ animation helpers
    def get_fps(self):
        self.ui.txt_display.setText(str(self.controller.get_fps_value()))

    def get_current_time(self):
        self.ui.txt_display.setText(self.controller.get_current_time())

    def frames_to_sec(self):
        self._apply_time_helper(self.controller.frames_to_sec)

    def sec_to_frames(self):
        self._apply_time_helper(self.controller.sec_to_frames)

    def _apply_time_helper(self, convert):
        """Resolve the display (as an expression) then run ``convert(value)`` on it."""
        value = self._resolve_display_value()
        if value is None:
            return
        self.ui.txt_display.setText(convert(value))


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
