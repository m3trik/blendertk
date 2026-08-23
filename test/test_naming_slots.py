"""NamingSlots panel test — the Qt wiring of the Naming tool, driven on the real loaded panel.

Needs **Qt, not bpy** (the file scopes and the option boxes never touch Blender), so it runs
under the workspace ``.venv`` like ``test_blender_ui_handler.py``::

    .venv\\Scripts\\python.exe blendertk/test/test_naming_slots.py

Covers: the header Scope combo (Selection / Scene / Directory / Files) + Dry Run toggle, the
output pane wiring (the engine's report lands in ``txt002``), the suffix-by-type option box
(19 fields from the shared table; Blender-inapplicable ones disabled), and the Directory /
Files workflow end to end on a temp directory: Find opens the browser and narrows the working
set, Rename honours Dry Run, a live Rename renames on disk and keeps the working set valid,
Convert Case / Strip Chars follow, and the scene-only operations report instead of acting.
Under the Blender harness (no Qt) it SKIPS with a PASS sentinel.
"""

import os
import sys
import shutil
import tempfile
import traceback

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
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    print("SKIP test_naming_slots (no Qt binding — run under the workspace .venv)")
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

# Keep this run off the live QSettings store (uitk/test/conftest.py owns the shim).
import importlib.util

_conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
if not os.path.isfile(_conftest):
    raise SystemExit("SKIP test_naming_slots (no uitk/test/conftest.py)")
_spec = importlib.util.spec_from_file_location("_uitk_conftest", _conftest)
_spec.loader.exec_module(importlib.util.module_from_spec(_spec))

tmp = tempfile.mkdtemp(prefix="naming_slots_")
try:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from uitk import Switchboard
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    sb = Switchboard()
    BlenderUiHandler(switchboard=sb)
    ui = sb.get_ui("naming")
    slots = ui.slots
    # The offscreen load skips header_init; drive the documented init entry points.
    slots.header_init(ui.header)
    for w in ("txt000", "txt001", "tb000", "tb001", "tb002", "tb003"):
        getattr(slots, f"{w}_init")(getattr(ui, w))
    menu = ui.header.menu

    # ---- header: scope combo + dry-run toggle -------------------------------------------
    scopes = [menu.cmb_scope.itemText(i) for i in range(menu.cmb_scope.count())]
    check(
        "scope combo items",
        scopes == ["Selection", "Scene", "Directory", "Files"],
        str(scopes),
    )
    check(
        "dry-run toggle under scope",
        hasattr(menu, "chk_dry_run") and not menu.chk_dry_run.isChecked(),
    )
    check("output pane intro", "Dry Run" in ui.txt002.toPlainText())
    check(
        "output pane not persisted", getattr(ui.txt002, "restore_state", True) is False
    )

    # ---- suffix-by-type option box: 19 fields from the shared table ---------------------
    m = ui.tb003.option_box.menu
    fields = {name: getattr(m, name, None) for name in slots.SUFFIX_FIELDS.values()}
    check(
        "19 suffix fields built",
        len(fields) == 19 and all(fields.values()),
        str([n for n, w in fields.items() if w is None]),
    )
    check(
        "suffix defaults",
        m.tb003_txt003.text() == "_GEO"
        and m.tb003_txt009.text() == "_SRF"
        and m.tb003_txt018.text() == "_SET",
    )
    disabled = {n for n, w in fields.items() if w is not None and not w.isEnabled()}
    expected_disabled = {slots.SUFFIX_FIELDS[kw] for kw in slots._BLENDER_NA}
    check(
        "Blender-inapplicable fields disabled",
        disabled == expected_disabled,
        str(sorted(disabled ^ expected_disabled)),
    )
    check(
        "valid_suffixes from the fields",
        "_SRF" in slots.valid_suffixes and "_GEO" in slots.valid_suffixes,
    )

    # ---- file scope: browse on Find, narrow, dry-run rename, live rename ----------------
    for name in ("pCube1.png", "pCube2.PNG", "sphere.txt"):
        with open(os.path.join(tmp, name), "wb") as f:
            f.write(b"x")
    menu.cmb_scope.setCurrentText("Directory")
    browsed = []
    sb.dir_dialog = lambda *a, **k: (browsed.append(1), tmp)[1]
    check("file_scope property", slots.file_scope)

    ui.txt000.setText("pCube*")
    slots.txt000(ui.txt000)
    check("Find browses for the directory", browsed == [1])
    check(
        "Find narrows the working set",
        sorted(os.path.basename(f) for f in slots._files)
        == ["pCube1.png", "pCube2.PNG"],
        str(slots._files),
    )
    out = ui.txt002.toPlainText()
    check(
        "Find report lists matches",
        "Find — 2 of 3 files" in out and "pCube1" in out,
        out[:200],
    )
    check("Find report links the directory", "Directory:" in out)

    menu.chk_dry_run.setChecked(True)
    ui.txt001.setText("*box*")
    slots.txt001(ui.txt001)
    out = ui.txt002.toPlainText()
    check(
        "dry-run rename reports the plan",
        "DRY RUN" in out and "pCube1 → box1" in out,
        out[:300],
    )
    check(
        "dry-run rename touches nothing",
        sorted(os.listdir(tmp)) == ["pCube1.png", "pCube2.PNG", "sphere.txt"],
    )
    check("Find did not re-browse", browsed == [1])

    menu.chk_dry_run.setChecked(False)
    slots.txt001(ui.txt001)
    check(
        "live rename renamed on disk",
        sorted(os.listdir(tmp)) == ["box1.png", "box2.PNG", "sphere.txt"],
        str(os.listdir(tmp)),
    )
    check("extension preserved", os.path.isfile(os.path.join(tmp, "box2.PNG")))
    check(
        "working set follows the renames",
        sorted(os.path.basename(f) for f in slots._files) == ["box1.png", "box2.PNG"],
        str(slots._files),
    )
    check("live rename summary", "renamed 2 files" in ui.txt002.toPlainText())

    ui.tb000.option_box.menu.cmb001.setCurrentText("upper")
    slots.tb000(ui.tb000)
    check(
        "convert case on files",
        sorted(os.listdir(tmp)) == ["BOX1.png", "BOX2.PNG", "sphere.txt"],
        str(os.listdir(tmp)),
    )

    ui.tb002.option_box.menu.s000.setValue(1)
    ui.tb002.option_box.menu.cmb002.setCurrentText("Leading")
    slots.tb002(ui.tb002)
    check(
        "strip chars on files",
        sorted(os.listdir(tmp)) == ["OX1.png", "OX2.PNG", "sphere.txt"],
        str(os.listdir(tmp)),
    )

    slots.tb003(ui.tb003)
    check(
        "suffix by type is scene-only in a file scope",
        "scene objects only" in ui.txt002.toPlainText()
        and sorted(os.listdir(tmp)) == ["OX1.png", "OX2.PNG", "sphere.txt"],
    )
    slots.tb001(ui.tb001)
    check(
        "suffix by location is scene-only in a file scope",
        "scene objects only" in ui.txt002.toPlainText(),
    )

    # ---- Files scope uses the file browser ---------------------------------------------
    menu.cmb_scope.setCurrentText("Files")
    picked = [os.path.join(tmp, "sphere.txt")]
    sb.file_dialog = lambda *a, **k: picked
    ui.txt000.setText("")
    slots.txt000(ui.txt000)
    check(
        "Files scope: empty Find keeps every chosen file",
        slots._files == picked,
        str(slots._files),
    )

    # ---- cancelled browser ----------------------------------------------------------------
    sb.file_dialog = lambda *a, **k: []
    slots.txt000(ui.txt000)
    check(
        "cancelled browser reports, no working set",
        slots._files == [] and "No files chosen" in ui.txt002.toPlainText(),
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
