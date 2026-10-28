"""blendertk Calculator engine headless test.

Verifies the controller delegates its math/unit engine to the shared ``pythontk.MathUtils``
(identical to Maya's) and that the Blender-specific time helpers read the scene FPS. The
panel's display actions are tentacle's (``test_tool_panels.py``).

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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")


try:
    import bpy
    from blendertk.ui_utils.calculator import CalculatorController

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

except Exception as e:
    traceback.print_exc()
    check("calculator test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
