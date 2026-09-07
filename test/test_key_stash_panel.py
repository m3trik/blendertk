# !/usr/bin/python
# coding=utf-8
"""Key Stash panel load test — Blender twin of mayatk's ``test_key_stash_panel``.

Needs **Qt, not bpy**: loads ``key_stash.ui`` and wires ``KeyStashSlots``
through a real (offscreen) Qt / Switchboard / BlenderUiHandler stack. Nothing
on the load path touches Blender — ``KeyStash.active()`` falls back to an
in-memory store when ``bpy`` is absent. Proves the twin ``.ui`` compiles into
the house layout (header / titled groups / footer, 19 px rows, four-column
clip list, Preview as a checkbox), the controller builds (combos with their
display prefixes, header refresh / collapse / pin + help, store binding,
selection gating). Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_key_stash_panel.py

The engine behaviour (stash / retrieve / preview against a real file) is
covered by ``test_key_stash.py`` under the Blender harness.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from qtpy import QtWidgets
except Exception:  # pragma: no cover - Qt absent (Blender's headless Python)
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

COLUMNS = ["Clip", "Objects", "Range", "Stored"]


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestKeyStashPanelLoads(unittest.TestCase):
    """The Key Stash panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        # No bpy here: force the in-memory store so the load can't touch a file.
        KeyStash._active = None
        KeyStash.set_persistence(None)

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("key_stash")
        # The slots populate on the next event-loop tick; give it that tick.
        for _ in range(50):
            if getattr(cls.ui.slots, "_initialized", False):
                break
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        KeyStash._active = None
        KeyStash.set_persistence(None)

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "key_stash UI failed to load")

    def test_resolves_to_slots_class(self):
        self.assertEqual(type(self.ui.slots).__name__, "KeyStashSlots")
        self.assertTrue(self.ui.slots._initialized)

    def test_static_widgets_exist(self):
        expected = [
            "header",
            "store_group",
            "cmb000",
            "b000",
            "tree000",
            "clip_group",
            "cmb001",
            "b001",
            "b003",
            "chk000",
            "chk001",
            "footer",
        ]
        missing = [w for w in expected if getattr(self.ui, w, None) is None]
        self.assertEqual(missing, [])

    def test_titled_groups_frame_the_actions(self):
        self.assertIsInstance(self.ui.store_group, QtWidgets.QGroupBox)
        self.assertIsInstance(self.ui.clip_group, QtWidgets.QGroupBox)
        self.assertEqual(self.ui.store_group.title(), "Store")
        self.assertEqual(self.ui.clip_group.title(), "Selected Clip")
        self.assertIs(self.ui.cmb000.parentWidget(), self.ui.store_group)
        self.assertIs(self.ui.b001.parentWidget(), self.ui.clip_group)
        self.assertIs(self.ui.chk001.parentWidget(), self.ui.clip_group)

    def test_controls_use_the_19px_row(self):
        for name in ("cmb000", "b000", "cmb001", "b001", "b003", "chk000", "chk001"):
            self.assertEqual(getattr(self.ui, name).maximumHeight(), 19, name)

    def test_preview_and_in_context_are_checkboxes(self):
        self.assertIsInstance(self.ui.chk001, QtWidgets.QCheckBox)
        self.assertIsInstance(self.ui.chk000, QtWidgets.QCheckBox)
        self.assertEqual(self.ui.chk001.text(), "Preview")
        self.assertEqual(self.ui.chk000.text(), "In Context")

    def test_mode_combos_populate_with_display_prefixes(self):
        slots = self.ui.slots
        cmb000, cmb001 = self.ui.cmb000, self.ui.cmb001
        self.assertEqual(
            [cmb000.itemText(i) for i in range(cmb000.count())], list(slots.SOURCES)
        )
        self.assertEqual(
            [cmb001.itemText(i) for i in range(cmb001.count())],
            list(slots.RETRIEVE_AT),
        )
        self.assertEqual(cmb000.current_text_prefix, "Source:  ")
        self.assertEqual(cmb001.current_text_prefix, "Retrieve At:  ")

    def test_clip_list_shape(self):
        tree = self.ui.tree000
        self.assertEqual(type(tree).__name__, "TreeWidget")
        self.assertEqual(
            [tree.headerItem().text(i) for i in range(tree.columnCount())], COLUMNS
        )
        self.assertEqual(
            tree.selectionMode(), QtWidgets.QAbstractItemView.SingleSelection
        )
        self.assertFalse(tree.rootIsDecorated())

    def test_header_buttons_and_help(self):
        header = self.ui.header
        for name in ("refresh", "collapse", "pin", "help"):
            self.assertIn(name, header.buttons, name)
        self.assertIn("Key Stash", header.help_text())

    def test_store_bound_and_idle_footer(self):
        store = self.ui.slots.store
        self.assertEqual(type(store).__name__, "KeyStash")
        self.assertEqual(store.clips, [])
        self.assertEqual(self.ui.footer.getDefaultStatusText(), "No stored clips")

    def test_clip_actions_gate_on_selection(self):
        self.assertEqual(self.ui.tree000.topLevelItemCount(), 0)
        for name in ("b001", "b003", "chk001"):
            self.assertFalse(getattr(self.ui, name).isEnabled(), name)
        self.assertTrue(self.ui.chk000.isEnabled())

    def test_preview_without_a_selection_unchecks_itself(self):
        """The checkbox's toggle reaches the slot (Switchboard wiring) and is refused."""
        self.ui.chk001.setChecked(True)
        self.assertFalse(self.ui.chk001.isChecked())
        self.assertIn("Select a stored clip", self.ui.footer.statusText())


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # The harness's (ok/attempted) tally, as every Blender suite reports it --
    # `unittest.main()` cannot be used here: run_tests.py launches each suite as
    # `blender --background --factory-startup --python <suite>`, and main()
    # parses THOSE flags as its own argv and exits 2 before a test runs. Skips
    # leave the ratio (a fully skipped suite reports `skipped`, which is what
    # tells the runner to re-run this Qt-only suite under the workspace .venv).
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
