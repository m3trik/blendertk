"""BlenderNativeMenus objectName test — the mirror of mayatk's
``test_maya_menu_handler.TestNativeMenuObjectName``.

``MainWindow.register_widget`` binds every registered child's objectName as an attribute
on the window, so a menu wrapper named for a bare mapping key collides wherever that key
matches a method — 'render' (``QWidget.render``) and 'window' (``QWidget.window``) both
do. uitk's shadow guard warns and skips the binding; before the guard it clobbered the
method outright. This pins the whole MENU_MAPPING clear of MainWindow's surface, since a
new mapping entry is the likely way it regresses.

Needs **Qt, not bpy** (``get_menu``'s harvest is mocked), so it is a workspace ``.venv``
target like ``test_blender_ui_handler.py``::

    .venv\\Scripts\\python.exe blendertk/test/test_blender_native_menus.py

Under the Blender harness (``--background --factory-startup``, no Qt binding) it skips
with a PASS sentinel so ``Run-Tests.ps1`` stays green.
"""
import os
import sys
import traceback
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    # Blender headless ships no Qt binding — this suite is a .venv target. Skip cleanly.
    print("SKIP test_blender_native_menus (no Qt binding — run under the workspace .venv)")
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

try:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from uitk.widgets.mainWindow import MainWindow
    from blendertk.ui_utils.blender_native_menus import BlenderNativeMenus

    handler = BlenderNativeMenus()

    def object_name_for(name):
        """Return the objectName get_menu assigns to *name*'s wrapper.

        Drives the real get_menu so the assertion tracks production naming rather
        than restating it; only the bpy-side harvest is mocked (a non-zero row
        count is what makes get_menu keep and return the wrapper).
        """
        with mock.patch(
            "blendertk.ui_utils.menu_harvest.refill_qmenu", return_value=5
        ):
            widget = handler.get_menu(name)
        return widget.objectName() if widget is not None else None

    offenders = {}
    unbuilt = []
    for key in BlenderNativeMenus.MENU_MAPPING:
        obj_name = object_name_for(key)
        if obj_name is None:
            unbuilt.append(key)
        elif callable(getattr(MainWindow, obj_name, None)):
            offenders[key] = obj_name

    check(
        "every mapped menu builds a wrapper (harvest mocked)",
        not unbuilt,
        f"returned None for: {unbuilt}",
    )
    check(
        "no menu key's wrapper objectName shadows a MainWindow method",
        not offenders,
        f"shadowing objectNames: {offenders}",
    )

except Exception as e:
    traceback.print_exc()
    check("native-menu objectName check raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
