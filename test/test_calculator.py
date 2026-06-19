"""blendertk Calculator engine headless test.

Verifies the controller delegates its math/unit engine to the shared ``pythontk.MathUtils``
(identical to the Maya panel) and that the Blender-specific time helpers read the scene FPS,
plus the display-action logic (input / evaluate / convert) on a UI stand-in.

Run: blender --background --factory-startup --python blendertk/test/test_calculator.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


class FakeDisplay:
    """Minimal stand-in for the txt_display QLineEdit."""
    def __init__(self, value=""):
        self._v = value
    def text(self):
        return self._v
    def setText(self, v):
        self._v = v
    def clear(self):
        self._v = ""


class FakeCombo:
    def __init__(self, value):
        self._v = value
    def currentText(self):
        return self._v


try:
    import bpy
    from blendertk.ui_utils.calculator import CalculatorController, CalculatorSlots

    ctrl = CalculatorController()

    # 1. Math/unit engine is delegated to the shared pythontk engine (parity with Maya).
    check("calculate 2+2", ctrl.calculate("2+2") == "4")
    check("calculate 10/4", ctrl.calculate("10/4") == "2.5")
    check("calculate sqrt(16)", ctrl.calculate("sqrt(16)") == "4")
    check("calculate 1/0 -> Error", ctrl.calculate("1/0") == "Error")
    check("convert cm->m", ctrl.convert_unit(100, "cm", "m") == "1.0", ctrl.convert_unit(100, "cm", "m"))
    check("convert in->cm", ctrl.convert_unit(1, "in", "cm") == "2.54")

    # 2. Blender time helpers read the scene (default factory scene = 24 fps).
    fps = ctrl.get_fps_value()
    check("get_fps_value positive float", isinstance(fps, float) and fps > 0, str(fps))
    check("get_current_time is str", isinstance(ctrl.get_current_time(), str))
    # frames<->sec round-trip against the live fps
    secs = ctrl.frames_to_sec(fps)          # one second of frames -> ~1.0s
    check("frames_to_sec(fps) ~ 1.0", abs(float(secs) - 1.0) < 1e-6, secs)
    frames = ctrl.sec_to_frames(1.0)        # one second -> fps frames
    check("sec_to_frames(1) ~ fps", abs(float(frames) - fps) < 1e-6, frames)

    # 3. Slot display-action logic (no Qt grid build — bypass __init__).
    slot = CalculatorSlots.__new__(CalculatorSlots)
    slot.ui = type("UI", (), {})()
    slot.ui.txt_display = FakeDisplay()
    slot.controller = ctrl

    slot.on_input("1"); slot.on_input("2"); slot.on_input("+"); slot.on_input("3")
    check("on_input builds expression", slot.ui.txt_display.text() == "12+3")
    slot.on_equal()
    check("on_equal evaluates", slot.ui.txt_display.text() == "15")
    slot.on_backspace()
    check("on_backspace", slot.ui.txt_display.text() == "1")
    slot.on_clear()
    check("on_clear", slot.ui.txt_display.text() == "")

    # convert: evaluate display then convert cm->m
    slot.ui.txt_display.setText("100")
    slot.ui.cmb_unit_from = FakeCombo("cm")
    slot.ui.cmb_unit_to = FakeCombo("m")
    slot.on_convert_units()
    check("on_convert_units cm->m", slot.ui.txt_display.text() == "1.0", slot.ui.txt_display.text())

    # time helper resolves the display expression first
    slot.ui.txt_display.setText(str(int(fps)))
    slot.frames_to_sec()
    check("frames_to_sec action ~1.0", abs(float(slot.ui.txt_display.text()) - 1.0) < 1e-6,
          slot.ui.txt_display.text())

    # error path: an invalid display expression resolves to "Error" (unified _resolve helper)
    slot.ui.txt_display.setText("1/0")
    slot.on_convert_units()
    check("on_convert_units invalid -> Error", slot.ui.txt_display.text() == "Error",
          slot.ui.txt_display.text())
    slot.ui.txt_display.setText("nope(")
    slot.frames_to_sec()
    check("time helper invalid -> Error", slot.ui.txt_display.text() == "Error",
          slot.ui.txt_display.text())

except Exception as e:
    traceback.print_exc()
    check("calculator test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
