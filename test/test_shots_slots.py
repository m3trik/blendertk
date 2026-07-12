# !/usr/bin/python
# coding=utf-8
"""Shots settings panel load test — Blender port of mayatk's shots panel.

Needs **Qt, not bpy**: loads ``shots.ui`` and wires ``ShotsSlots`` through a real
(offscreen) Qt / Switchboard / BlenderUiHandler stack — none of which touch Blender
(``BlenderShotStore.active()`` / ``has_animation()`` swallow the ``bpy``-import
failure, falling back to an in-memory store, exactly as the engine's headless
defaults intend). Proves the ``.ui`` compiles, the controller builds (option-box
menus, store binding, hide-on-leave timer), and the combos / defaults match the
mayatk panel. Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_shots_slots.py

The functional engine behaviour (move / ripple / gap / reorder / trim, which need a
real scene) is covered by ``test_shot_sequencer.py`` under the Blender harness.
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
class TestShotsPanelLoads(unittest.TestCase):
    """The Shots panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        from blendertk import BlenderShotStore

        # Isolate cross-scene prefs + class singleton so the load can't touch real config.
        cls._prefs_tmp = tempfile.mkdtemp(prefix="btk_shots_panel_")
        BlenderShotStore._prefs_dir_override = cls._prefs_tmp
        BlenderShotStore.clear_active()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("shots")
        for _ in range(5):
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        from blendertk import BlenderShotStore

        BlenderShotStore.clear_active()
        BlenderShotStore._prefs_dir_override = None

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "shots UI failed to load")

    def test_resolves_to_slots_class(self):
        self.assertEqual(type(self.ui.slots).__name__, "ShotsSlots")

    def test_controller_built(self):
        ctrl = getattr(self.ui.slots, "controller", None)
        self.assertIsNotNone(ctrl)
        self.assertEqual(type(ctrl).__name__, "ShotsController")

    def test_all_referenced_widgets_exist(self):
        expected = [
            "header",
            "footer",
            "cmb_detection_mode",
            "spn_detection",
            "spn_initial_length",
            "cmb_fit_mode",
            "chk_snap_whole_frames",
            "cmb_shot_select",
            "txt_shot_name",
            "spn_shot_start",
            "spn_shot_end",
            "txt_shot_desc",
            "spn_gap",
            "spn_move_to",
            "btn_trim_empty",
            "b000",
        ]
        missing = [w for w in expected if not hasattr(self.ui, w)]
        self.assertEqual(missing, [])

    def test_detection_mode_combo_items(self):
        cmb = self.ui.cmb_detection_mode
        items = [cmb.itemText(i) for i in range(cmb.count())]
        self.assertEqual(
            items, ["Auto-Detect", "All Keys", "Skip Zero-Value", "Zero = Shot End"]
        )

    def test_fit_mode_combo_items(self):
        cmb = self.ui.cmb_fit_mode
        items = [cmb.itemText(i) for i in range(cmb.count())]
        self.assertEqual(items, ["Extend Only", "Shrink & Extend to Fit"])

    def test_detection_default_value(self):
        # Controller syncs spn_detection from a fresh store (detection_threshold 5.0).
        self.assertAlmostEqual(self.ui.spn_detection.value(), 5.0)

    def test_initial_length_default_value(self):
        self.assertAlmostEqual(self.ui.spn_initial_length.value(), 200.0)

    def test_option_box_menu_widgets_exist(self):
        """The controller's _setup_*_menu build the option-box actions, which uitk
        registers on the ui by objectName (the slots read them via getattr(self.ui, ...))."""
        missing = [
            name
            for name in (
                "btn_delete_all_shots",
                "btn_move_shot",
                "btn_trim_all_shots",
                "cmb_gap_scope",
                "btn_apply_gap",
            )
            if getattr(self.ui, name, None) is None
        ]
        self.assertEqual(missing, [])

    def test_gap_scope_combo_items(self):
        cmb = getattr(self.ui, "cmb_gap_scope", None)
        self.assertIsNotNone(cmb)
        data = [cmb.itemData(i) for i in range(cmb.count())]
        self.assertEqual(data, ["all", "start", "end", "start_end"])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}===")
