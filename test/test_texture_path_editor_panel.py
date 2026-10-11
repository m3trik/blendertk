# !/usr/bin/python
# coding=utf-8
"""Texture Path Editor panel load test -- the Qt half of ``test_texture_path_editor``.

Needs **Qt, not bpy**: loads ``texture_path_editor.ui`` and wires
``TexturePathEditorSlots`` through a real (offscreen) Qt / Switchboard /
BlenderUiHandler stack, with the table's few scene reads stood in for (image
records, the material map, lightmap rows). Proves what the headless Blender
suite cannot: the header menu's Keep Names In Sync toggle and its option box,
the Truncate Texture Paths length box, the six-column table with its optional
columns hidden until shown (and read when shown), and the row menu fitted to the
selection. Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_texture_path_editor_panel.py

The engine (rename, sync, undo) is covered by ``test_texture_path_editor.py``
under the Blender harness. Added: 2026-10-05
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import bpy  # noqa: F401

    QtWidgets = None  # the Blender harness: this suite is a .venv target
except Exception:
    try:
        from qtpy import QtWidgets
    except Exception:  # pragma: no cover - no Qt binding
        QtWidgets = None

if QtWidgets is not None:
    # Sandbox the real uitk\shared QSettings store (uitk/test/conftest.py owns the shim)
    # so loading the panel can't read/write the developer's live state.
    import importlib.util

    _conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
    if os.path.isfile(_conftest):
        _spec = importlib.util.spec_from_file_location("_uitk_conftest", _conftest)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
    else:
        QtWidgets = None


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestTexturePathEditorPanel(unittest.TestCase):
    """The panel loads and its 2026-10-05 controls are wired."""

    @classmethod
    def setUpClass(cls):
        import pythontk as ptk

        import blendertk as btk
        from blendertk.mat_utils.texture_path_editor import TexturePathEditorSlots

        cls.ptk = ptk
        cls.tmp = tempfile.TemporaryDirectory(prefix="tpe_panel_")
        cls.paths = []
        for name in ("rock_Normal.png", "shared.png"):
            path = os.path.join(cls.tmp.name, name)
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG" + b"0" * 60)
            cls.paths.append(path)
        records = [
            {
                "name": os.path.basename(path),
                "image": None,
                "filepath": path,
                "abspath": path,
                "exists": True,
                "users": 1,
            }
            for path in cls.paths
        ]
        # The table's scene reads, stood in for: no bpy in this interpreter.
        cls.patches = [
            mock.patch.object(btk, "get_image_records", return_value=records),
            mock.patch.object(
                btk,
                "get_image_material_map",
                return_value={
                    "rock_Normal.png": ["rock_MAT"],
                    "shared.png": ["A", "B"],
                },
            ),
            mock.patch.object(
                TexturePathEditorSlots, "_lightmap_dependencies", return_value=[]
            ),
            mock.patch.object(
                TexturePathEditorSlots,
                "_renameable_material",
                staticmethod(lambda name: bool(name)),
            ),
            mock.patch.object(
                TexturePathEditorSlots,
                "_row_texture_path",
                lambda self, widget, row: widget.item(row, 1).text(),
            ),
        ]
        for patch in cls.patches:
            patch.start()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard

        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        # A process singleton: in a run that built it earlier it keeps ITS
        # switchboard, which is the one holding the UIs.
        cls.sb = cls.handler.sb
        cls.ui = cls.handler.get("texture_path_editor")
        cls.ui.header.init_slot()
        cls.ui.tbl000.init_slot()
        cls.app.processEvents()
        cls.slots = cls.ui.slots
        cls.table = cls.ui.tbl000

    @classmethod
    def tearDownClass(cls):
        for patch in reversed(cls.patches):
            patch.stop()
        cls.tmp.cleanup()

    def test_resolves_to_the_slots_class(self):
        self.assertEqual(type(self.slots).__name__, "TexturePathEditorSlots")

    def test_keep_names_in_sync_and_its_option_box(self):
        menu = self.ui.header.menu
        self.assertFalse(menu.chk_sync_names.isChecked(), "off by default")
        flyout = menu.chk_sync_names.option_box.menu
        self.assertEqual(flyout.txt_shader_affix.option_box.affix_mode, "convention")
        self.assertEqual(flyout.txt_shader_affix.placeholderText(), "Scene Convention")
        self.assertFalse(self.slots._sync_names_enabled())
        self.assertEqual(
            self.slots._sync_affixes(),
            (self.ptk.NamingConvention.affix_parts("material"), ("", "")),
        )
        flyout.txt_file_node_suffix.setText("img")
        try:
            self.assertEqual(self.slots._sync_affixes()[1], ("", "_img"))
        finally:
            flyout.txt_file_node_suffix.setText("")

    def test_truncate_length_box(self):
        menu = self.ui.header.menu
        spin = menu.chk_truncate_paths.option_box.menu.spn_truncate_length
        self.assertEqual((spin.value(), spin.minimum(), spin.maximum()), (96, 24, 400))
        menu.chk_truncate_paths.setChecked(True)
        try:
            spin.setValue(140)
            self.assertEqual(self.table.column_truncation(1)[0], 140)
        finally:
            spin.setValue(96)
            menu.chk_truncate_paths.setChecked(False)
        self.assertIsNone(self.table.column_truncation(1))

    def test_set_directory_offers_allow_missing_targets(self):
        button = self.ui.header.menu.tb_set_texture_directory
        menu = button.option_box.menu
        if getattr(menu, "chk_allow_missing", None) is None:
            button.init_slot()  # its option box fills on the button's own init
        self.assertFalse(menu.chk_allow_missing.isChecked(), "off by default")
        self.assertFalse(
            self.slots._read_option_flag(button, "chk_allow_missing", True)
        )
        self.assertEqual(menu.cmb_relocate_mode.count(), 3)

    def test_six_columns_the_optional_ones_hidden(self):
        self.table.init_slot()  # a fresh rebuild, whatever another test showed
        headers = [
            self.table.horizontalHeaderItem(c).text()
            for c in range(self.table.columnCount())
        ]
        self.assertEqual(headers, list(self.slots._HEADERS))
        hidden = [
            c
            for c in range(self.table.columnCount())
            if self.table.horizontalHeader().isSectionHidden(c)
        ]
        self.assertEqual(hidden, [2, 3, 4, 5])
        self.assertEqual(self.table.item(0, 3).text(), "", "a hidden fact is not read")

    def test_showing_a_fact_column_reads_it(self):
        from uitk.widgets.column_config import ColumnConfig

        config = ColumnConfig.of(self.table)
        config.set_hidden(3, False)
        try:
            self.assertTrue(self.table.item(0, 3).text().endswith("bytes"))
        finally:
            config.set_hidden(3, True)

    def test_the_material_cell_is_editable_on_a_row_of_one_material(self):
        Qt = self.sb.QtCore.Qt
        editable = {
            self.table.item(row, 1).text(): bool(
                self.table.item(row, 0).flags() & Qt.ItemIsEditable
            )
            for row in range(self.table.rowCount())
        }
        self.assertEqual(
            editable,
            {self.paths[0]: True, self.paths[1]: False},
            "one vs two materials",
        )

    def test_the_row_menu_fits_the_selection(self):
        QPushButton = QtWidgets.QPushButton
        menu = self.table.menu
        rename = menu.findChild(QPushButton, "row_rename_file")
        browse = menu.findChild(QPushButton, "row_browse_for_file")
        self.assertIsNotNone(rename, "Rename File... is on the row menu")
        self.table.clearSelection()
        self.table.selectRow(0)
        self.slots._fit_row_menu(self.table)
        self.assertTrue(rename.isEnabled())
        self.assertEqual(browse.text(), "Browse for File...")
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.table.selectRow(1)
        self.slots._fit_row_menu(self.table)
        self.assertFalse(rename.isEnabled())
        self.assertEqual(browse.text(), "Browse for Folder...")
        self.table.clearSelection()

    def test_rename_file_opens_the_path_cell_on_the_file_name(self):
        """Acted on the real table: the path cell's editor opens on the file name."""
        self.ui.show()
        self.table.clearSelection()
        self.table.setCurrentCell(0, 1)
        selection = self.table.get_selection(columns=self.slots._ROW_SELECTION_COLUMNS)
        with (
            mock.patch.object(
                type(self.slots),
                "_images_from_selection",
                return_value=[_Image(self.paths[0])],
            ),
            mock.patch.dict(sys.modules, {"bpy": _bpy(self.paths[0])}),
        ):
            self.slots.row_rename_file(selection)
        self.app.processEvents()
        editors = [
            e
            for e in self.table.findChildren(QtWidgets.QLineEdit)
            if e.isVisible() and e.text() == "rock_Normal.png"
        ]
        try:
            self.assertEqual(
                self.table.state(), QtWidgets.QAbstractItemView.EditingState
            )
            self.assertEqual(len(editors), 1, "the editor holds just the file name")
            self.assertEqual(editors[0].selectedText(), "rock_Normal", "the stem")
        finally:
            for editor in editors:
                self.table.closeEditor(
                    editor, QtWidgets.QAbstractItemDelegate.RevertModelCache
                )
            self.ui.hide()


class _Image:
    """An image datablock's two fields the row menu reads."""

    def __init__(self, path):
        self.name = os.path.basename(path)
        self.filepath = path
        self.library = None


def _bpy(path):
    """A ``bpy`` module double: one image, and ``bpy.path.basename``."""
    from types import SimpleNamespace

    image = _Image(path)
    return SimpleNamespace(
        data=SimpleNamespace(images={image.name: image}),
        path=SimpleNamespace(basename=os.path.basename),
    )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # The harness's (ok/attempted) tally, as every Blender suite reports it --
    # `unittest.main()` cannot be used here: run_tests.py launches each suite as
    # `blender --background --factory-startup --python <suite>`, and main()
    # parses THOSE flags as its own argv and exits 2 before a test runs. A fully
    # skipped suite reports `skipped`, which tells the runner to re-run it
    # under the workspace .venv.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
