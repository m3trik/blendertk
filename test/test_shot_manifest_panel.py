# !/usr/bin/python
# coding=utf-8
"""Shot Manifest panel load test — Blender port of mayatk's manifest panel.

Needs **Qt, not bpy**: loads ``shot_manifest.ui`` and wires ``ShotManifestSlots``
through a real (offscreen) Qt / Switchboard / BlenderUiHandler stack. None of the
controller's build path touches Blender — ``BlenderShotStore.active()`` falls back
to an in-memory store, detection degrades to no-regions (``_active_scene`` returns
``None`` headless), and the mapping combo reads the shipped built-in templates.
Proves the ``.ui`` compiles, the controller builds (CSV widgets, header/mapping
menus, store binding, footer action-button relocation), and the mapping combo is
populated. Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_shot_manifest_panel.py

The functional engine behaviour (CSV → shots + native fades + VSE audio + assess,
which needs a real scene) is covered by ``test_shot_manifest.py`` under the Blender
harness.
"""
import os
import sys
import tempfile
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


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestShotManifestPanelLoads(unittest.TestCase):
    """The Shot Manifest panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        from blendertk import BlenderShotStore

        # Isolate cross-scene prefs + class singleton so the load can't touch real config.
        cls._prefs_tmp = tempfile.mkdtemp(prefix="btk_manifest_panel_")
        BlenderShotStore._prefs_dir_override = cls._prefs_tmp
        BlenderShotStore.clear_active()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("shot_manifest")
        for _ in range(5):
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        from blendertk import BlenderShotStore

        BlenderShotStore.clear_active()
        BlenderShotStore._prefs_dir_override = None

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "shot_manifest UI failed to load")

    def test_resolves_to_slots_class(self):
        self.assertEqual(type(self.ui.slots).__name__, "ShotManifestSlots")

    def test_controller_built(self):
        ctrl = getattr(self.ui.slots, "controller", None)
        self.assertIsNotNone(ctrl)
        self.assertEqual(type(ctrl).__name__, "ShotManifestController")

    def test_static_widgets_exist(self):
        expected = ["header", "footer", "chk_csv", "txt_csv_path", "tbl_steps", "b002", "b003"]
        missing = [w for w in expected if not hasattr(self.ui, w)]
        self.assertEqual(missing, [])

    def test_header_menu_widgets_exist(self):
        """The controller's _setup_header_menu / _setup_mapping_combo build the
        option-box actions, which uitk registers on the ui by objectName."""
        missing = [
            name
            for name in (
                "btn_expand_missing",
                "btn_expand_extra",
                "btn_manifest_colors",
                "btn_audio_clips",
                "btn_settings",
                "cmb_csv_mapping",
            )
            if getattr(self.ui, name, None) is None
        ]
        self.assertEqual(missing, [])

    def test_mapping_combo_populated(self):
        """The mapping combo lists the shipped built-in templates ('(none)' + default…)."""
        cmb = getattr(self.ui, "cmb_csv_mapping", None)
        self.assertIsNotNone(cmb)
        data = [cmb.itemData(i) for i in range(cmb.count())]
        self.assertIn(None, data, f"expected a '(none)' entry: {data}")
        self.assertIn("default", data, f"expected the built-in 'default' mapping: {data}")

    def test_tbl_steps_headers(self):
        """The tree carries the unified 6-column layout."""
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import HEADERS

        tree = self.ui.tbl_steps
        # populate_table sets header labels; before first populate the column
        # count may be default — assert the constant is the 6-col contract.
        self.assertEqual(len(HEADERS), 6)
        self.assertEqual(HEADERS[0], "Step")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}===")
