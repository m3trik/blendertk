# !/usr/bin/python
# coding=utf-8
"""Lightmap Baker panel test -- the Qt half of ``test_lightmap_baker``.

Needs **Qt, not bpy**: loads ``lightmap_baker.ui`` and wires ``LightmapBakerSlots``
through a real (offscreen) Qt / Switchboard / BlenderUiHandler stack, with the dry run
and the map rows stood in for (``LightmapBaker.stale_report`` / ``bake_maps``; the
Blender harness's ``test_lightmap_baker`` drives them on a real file). Mirror of
mayatk's ``mock_tests/test_lightmap_baker_panel``: the Bake page bakes fresh, the
Manage page lists each map with its state and counts what changed on the shared run
button, and the help card links the reference. Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_lightmap_baker_panel.py
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

import pythontk as ptk  # noqa: E402


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestLightmapBakerPanel(unittest.TestCase):
    """The panel loads with its two pages, and the Manage page reads the bake."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        handler = BlenderUiHandler(switchboard=Switchboard())
        cls.ui = handler.get("lightmap_baker")
        for _ in range(5):
            cls.app.processEvents()
        cls.slots = cls.ui.slots

    def _show_page(self, index):
        self.ui.cmb_page.setCurrentIndex(index)
        self.slots.cmb_page(index)
        self.app.processEvents()

    def _manage(self, rows, report):
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        with mock.patch.object(
            LightmapBaker, "stale_report", return_value=report
        ), mock.patch.object(LightmapBaker, "bake_maps", return_value=rows), mock.patch.object(
            self.slots, "_output_dir", return_value=None
        ):
            self._show_page(1)

    def test_the_help_card_links_the_tools_reference(self):
        """Read at a glance, ending in mayatk's doc, which covers both twins."""
        self.assertTrue(self.slots.HELP_URL.endswith("/docs/lightmap_baker.md"))
        self.assertEqual(self.ui.header.help_url(), self.slots.HELP_URL)

    def test_the_bake_page_bakes_fresh_and_the_manage_page_redoes(self):
        """Scope has no Changed row, the header menu holds only the whole
        file's Bake Report (Select Changed moved to the footer's Select), the
        Bake Set row is gone, and the run button names the page's run."""
        self.assertEqual(
            [self.ui.cmb_scope.itemText(i) for i in range(self.ui.cmb_scope.count())],
            ["Selected", "Visible", "Scene"],
        )
        QPushButton = self.slots.sb.QtWidgets.QPushButton
        self.assertEqual(
            [b.objectName() for b in self.ui.header.menu.findChildren(QPushButton)
             if b.objectName() and not b.objectName().startswith("hdr_")],
            ["show_bake_state"],
        )
        self.assertIsNone(
            self.ui.tbl_maps.menu.findChild(QPushButton, "show_bake_state")
        )
        for retired in ("lbl_bake_set", "select_bake_set"):
            self.assertIsNone(getattr(self.ui, retired, None), retired)
        self.assertEqual(
            [self.ui.cmb_page.itemText(i) for i in range(self.ui.cmb_page.count())],
            ["Bake", "Manage"],
        )
        self._show_page(0)
        self.assertEqual(self.ui.stk_pages.currentWidget(), self.ui.page_bake)
        self.assertEqual(self.ui.b000.text(), "Bake Lightmaps")

    def test_the_output_field_opens_its_folder(self):
        opens = [
            option
            for option in self.ui.txt_output_dir.option_box.get_options()
            if getattr(option, "_action_handler", None) == self.slots.open_output
        ]
        self.assertEqual(len(opens), 1)

    def test_the_manage_page_lists_each_map_with_its_state(self):
        report = ptk.StaleReport(
            targets=("RoomA",), stale={"RoomA": (ptk.BakeBaseline.Reason.MOVED,)}
        )
        rows = report.map_rows(
            [
                {"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
                 "objects": ["RoomA", "RoomB"], "writer": None, "size": (512, 512)},
                {"map": "Lost_LM.exr", "path": None, "folder": "lm",
                 "objects": ["RoomC"], "writer": None, "size": None},
            ]
        )
        try:
            self._manage(rows, report)
            table = self.ui.tbl_maps
            self.assertEqual(self.ui.stk_pages.currentWidget(), self.ui.page_manage)
            cells = {
                table.item(r, 0).text(): (table.item(r, 1).text(), table.item(r, 2).text())
                for r in range(table.rowCount())
            }
            self.assertEqual(
                cells, {"Room_LM.exr": ("2", "1 changed"), "Lost_LM.exr": ("1", "missing")}
            )
            room = next(r for r in range(2) if table.item(r, 0).text() == "Room_LM.exr")
            tip = table.item(room, 2).toolTip()
            self.assertIn("RoomA: moved", tip)
            self.assertIn("Re-Bake", tip, "the tooltip names the fix")
        finally:
            self._show_page(0)

    def test_preset_and_reset_are_the_bake_pages(self):
        """Mirror of mayatk's: the Manage page shows no Preset, Reset to
        Defaults or run button -- its verbs are its table menu's."""
        ui = self.ui
        try:
            self._manage([], ptk.StaleReport())
            self.assertFalse(ui.cmb000.isVisibleTo(ui))
            self.assertFalse(ui.btn_reset_defaults.isVisibleTo(ui))
            self.assertFalse(ui.b000.isVisibleTo(ui))
            self._show_page(0)
            for widget in (ui.cmb000, ui.btn_reset_defaults, ui.b000):
                self.assertTrue(widget.isVisibleTo(ui), widget.objectName())
        finally:
            self._show_page(0)

    def test_the_table_menu_re_bakes_and_reverts_the_selection(self):
        """Mirror of mayatk's: Re-Bake and Revert are the table menu's; the
        Manage page carries no buttons of its own."""
        menu = self.ui.tbl_maps.menu
        QPushButton = self.slots.sb.QtWidgets.QPushButton
        self.assertEqual(menu.findChild(QPushButton, "rebake_selected").text(), "Re-Bake")
        self.assertEqual(menu.findChild(QPushButton, "revert_selected").text(), "Revert…")
        self.assertEqual(
            menu.findChild(QPushButton, "track_selected").text(), "Start Tracking"
        )
        self.assertEqual(menu.findChild(QPushButton, "combine_selected").text(), "Combine…")
        buttons = [  # the menu's own items are buttons parented under the table
            b.objectName()
            for b in self.ui.page_manage.findChildren(QPushButton)
            if not menu.isAncestorOf(b)
        ]
        self.assertEqual(buttons, [])

    def test_start_tracking_is_offered_on_an_untracked_row(self):
        """Mirror of mayatk's: offered where a row is untracked, nowhere else."""
        report = ptk.StaleReport(untracked=("U",))
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [obj], "writer": None, "size": (512, 512)}
             for name, obj in (("T_LM.exr", "T"), ("U_LM.exr", "U"))]
        )
        table = self.ui.tbl_maps
        item = table.menu.findChild(self.slots.sb.QtWidgets.QPushButton, "track_selected")
        try:
            self._manage(rows, report)
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            for name, enabled in (("T_LM.exr", False), ("U_LM.exr", True)):
                blocked = table.blockSignals(True)  # no bpy to select readers in
                table.selectRow(names.index(name))
                table.blockSignals(blocked)
                self.slots._fit_map_menu()
                self.assertEqual(item.isEnabled(), enabled, name)
        finally:
            blocked = table.blockSignals(True)
            table.clearSelection()
            table.blockSignals(blocked)
            self._show_page(0)

    def test_select_and_fix_all_are_the_manage_pages_footer(self):
        """Mirror of mayatk's: Select (maps by state) and Fix All sit in the
        footer, shown with the Manage page only."""
        combo, fix = self.ui.footer._lightmap_manage
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["Select…", "Changed", "Split", "Untracked", "Missing", "All"],
        )
        self.assertEqual(fix.text(), "Fix All")
        # A button with a background (an action button is transparent on the
        # footer), and a list as wide as its items (a plain one clipped them).
        # Changed: 2026-10-23
        self.assertFalse(fix.property("footerWidget"))
        self.assertGreaterEqual(combo.width(), combo.sizeHint().width())
        combo.showPopup()
        try:
            view = combo.view()
            self.assertGreaterEqual(view.viewport().width(), view.sizeHintForColumn(0))
        finally:
            combo.hidePopup()
            self.app.processEvents()  # the popup closed before teardown
        # A reloaded panel re-wires its own handler, once.
        self.slots._manage_footer()
        self.assertEqual(fix.receivers("2clicked(bool)"), 1)
        try:
            self._manage([], ptk.StaleReport())
            self.assertTrue(combo.isVisibleTo(self.ui) and fix.isVisibleTo(self.ui))
            self._show_page(0)
            self.assertFalse(combo.isVisibleTo(self.ui) or fix.isVisibleTo(self.ui))
        finally:
            self._show_page(0)

    def test_combine_is_offered_on_a_split_row(self):
        """Mirror of mayatk's: offered where a row is split, nowhere else."""
        report = ptk.StaleReport(
            split={"S_LM.exr": ("S_LM.exr", "S_LM_1.exr")},
            split_materials={"S_LM.exr": ("Crate",)},
        )
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [name[0]], "writer": None, "size": (512, 512)}
             for name in ("S_LM.exr", "T_LM.exr")]
        )
        table = self.ui.tbl_maps
        item = table.menu.findChild(self.slots.sb.QtWidgets.QPushButton, "combine_selected")
        try:
            self._manage(rows, report)
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            for name, enabled in (("T_LM.exr", False), ("S_LM.exr", True)):
                blocked = table.blockSignals(True)  # no bpy to select readers in
                table.selectRow(names.index(name))
                table.blockSignals(blocked)
                self.slots._fit_map_menu()
                self.assertEqual(item.isEnabled(), enabled, name)
        finally:
            blocked = table.blockSignals(True)
            table.clearSelection()
            table.blockSignals(blocked)
            self._show_page(0)

    def test_the_table_menu_edits_no_material(self):
        """Mirror of mayatk's: a duplicate material is one atlas group with
        the one it duplicates, so the table offers no merge -- the baker never
        reassigns what the exporter reads."""
        find = self.ui.tbl_maps.menu.findChild
        QPushButton = self.slots.sb.QtWidgets.QPushButton
        self.assertIsNone(find(QPushButton, "merge_duplicate_materials"))
        self.assertIsNotNone(find(QPushButton, "open_map_folder"))
        self.assertFalse(hasattr(self.slots, "merge_duplicate_materials"))

    def test_a_footer_message_is_colored_by_its_level(self):
        """Mirror of mayatk's: something to act on is gold, a failure red."""
        moved = ptk.StaleReport(
            targets=("RoomA",), stale={"RoomA": (ptk.BakeBaseline.Reason.MOVED,)}
        )
        rows = moved.map_rows(
            [{"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
              "objects": ["RoomA"], "writer": None, "size": (512, 512)}]
        )
        missing = ptk.StaleReport().map_rows(
            [{"map": "Gone_LM.exr", "path": None, "folder": "lm",
              "objects": ["RoomA"], "writer": None, "size": None}]
        )
        footer = self.ui.footer
        try:
            self._manage(rows, moved)
            self.assertEqual(footer._status_level, "warning", "one to re-bake")
            self._manage(missing, ptk.StaleReport())
            self.assertEqual(footer._status_level, "error", "a map is missing")
            self._manage([], ptk.StaleReport())
            self.assertIsNone(footer._status_level, "nothing baked is no fault")
        finally:
            self._show_page(0)

    def test_a_refresh_keeps_the_highlighted_rows_and_the_scene_selection(self):
        """A re-bake or revert refills the table: the rows highlighted before stay
        highlighted, and the refill selects nothing in the file (it cleared the
        user's selection)."""
        from blendertk.core_utils._core_utils import CoreUtils

        report = ptk.StaleReport()
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [name[0]], "writer": None, "size": (512, 512)}
             for name in ("A_LM.exr", "B_LM.exr", "C_LM.exr")]
        )
        try:
            self._manage(rows, report)
            table = self.ui.tbl_maps
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            flags = self.slots.sb.QtCore.QItemSelectionModel
            blocked = table.blockSignals(True)  # no bpy here to select into
            for name in ("A_LM.exr", "C_LM.exr"):
                table.selectionModel().select(
                    table.model().index(names.index(name), 0), flags.Select | flags.Rows
                )
            table.blockSignals(blocked)
            with mock.patch.object(CoreUtils, "_active_view_layer") as layer:
                self._manage(rows, report)
            self.assertEqual(
                sorted(row["map"] for row in self.slots._selected_maps()),
                ["A_LM.exr", "C_LM.exr"],
            )
            layer.assert_not_called()
        finally:
            blocked = self.ui.tbl_maps.blockSignals(True)
            self.ui.tbl_maps.clearSelection()
            self.ui.tbl_maps.blockSignals(blocked)
            self._show_page(0)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # The harness's (ok/attempted) tally -- `unittest.main()` would parse
    # Blender's own flags under run_tests.py. A fully skipped suite reports
    # `skipped`, which tells the runner to re-run it under the workspace .venv.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
