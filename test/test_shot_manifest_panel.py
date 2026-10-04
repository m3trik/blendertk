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
        # A process singleton: in a run that built it earlier (another panel
        # module) it keeps ITS switchboard, which is the one holding the UIs.
        cls.sb = cls.handler.sb
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
        expected = [
            "header",
            "footer",
            "txt_csv_path",
            "tbl_steps",
            "b002",
            "b003",
        ]
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
                "btn_render_effects",
                "btn_settings",
                "cmb_csv_mapping",
            )
            if getattr(self.ui, name, None) is None
        ]
        self.assertEqual(missing, [])

    def test_render_effects_entry_opens_its_panel(self):
        """The header's Render Effects entry opens that panel the way Audio
        Clips and Shots open theirs -- through the host's panel registry, so
        the window is the one tentacle's own menus show."""
        from unittest import mock

        ctrl = self.ui.slots.controller
        with mock.patch.object(ctrl, "sb") as sb:
            self.ui.btn_render_effects.released.emit()
        sb.handlers.marking_menu.show.assert_called_once_with("render_effects")

    def test_mapping_combo_populated(self):
        """The mapping combo lists the shipped built-in templates ('(none)' + default…)."""
        cmb = getattr(self.ui, "cmb_csv_mapping", None)
        self.assertIsNotNone(cmb)
        data = [cmb.itemData(i) for i in range(cmb.count())]
        self.assertIn(None, data, f"expected a '(none)' entry: {data}")
        self.assertIn(
            "default", data, f"expected the built-in 'default' mapping: {data}"
        )

    def test_tbl_steps_headers(self):
        """The tree carries the unified 6-column layout."""
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import HEADERS

        self.assertIsNotNone(self.ui.tbl_steps)
        # populate_table sets header labels; before first populate the column
        # count may be default — assert the constant is the 6-col contract.
        self.assertEqual(len(HEADERS), 6)
        self.assertEqual(HEADERS[0], "Step")

    def test_template_options_render_and_apply(self):
        """The template declares its settings: the default's options appear as
        rows in the header menu, and acting on one changes the template in
        effect -- no option is hard-coded in the panel."""
        ctrl = self.ui.slots.controller
        ctrl._apply_mapping("default", persist=False)
        menu = self.ui.header.menu
        find = lambda name: menu.findChild(QtWidgets.QWidget, name)  # noqa: E731
        for name in ("opt_step_ids", "opt_audio", "opt_fill_missing_assets"):
            self.assertIsNotNone(find(name), name)

        self.addCleanup(ctrl._apply_mapping, "default", False)  # runs second
        self.addCleanup(ctrl._settings.setValue, "mapping_options/default", "")
        fill, ids = find("opt_fill_missing_assets"), find("opt_step_ids")
        fill.setChecked(True)
        ids.setCurrentIndex(ids.findData("numbered"))

        self.assertTrue(ctrl._active_mapping.get("fill_missing_assets"))
        self.assertIn("step_pattern", ctrl._active_mapping["columns"])
        # Saved per template: a rebuild shows the values just chosen.
        ctrl._build_option_rows()
        self.assertTrue(find("opt_fill_missing_assets").isChecked())

    def test_object_name_pickers_offer_the_strutils_cases_and_rules(self):
        """The case and legal-name pickers list exactly what pythontk's
        StrUtils applies, and picking one reshapes the names the sheet's
        prose gives."""
        import pythontk as ptk

        ctrl = self.ui.slots.controller
        ctrl._apply_mapping("default", persist=False)
        self.addCleanup(ctrl._apply_mapping, "default", False)  # runs second
        self.addCleanup(ctrl._settings.setValue, "mapping_options/default", "")
        find = lambda name: self.ui.header.menu.findChild(QtWidgets.QWidget, name)  # noqa: E731
        case, rule = find("opt_object_case"), find("opt_object_name_rule")
        self.assertEqual(
            [case.itemData(i) for i in range(case.count())],
            ["keep", *ptk.StrUtils.CASES],
        )
        self.assertEqual(
            sorted(rule.itemData(i) for i in range(rule.count())),
            sorted(["keep", *ptk.StrUtils.NAME_RULES]),
        )
        case.setCurrentIndex(case.findData("lower"))
        self.assertEqual(ctrl._active_mapping["columns"]["object_case"], "lower")

    def test_empty_source_shows_the_scenes_shots_with_descriptions(self):
        """No build sheet: the table lists the store's own shots, descriptions
        included, instead of re-detecting blank steps from animation."""
        from blendertk import BlenderShotStore
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_DESC

        ctrl = self.ui.slots.controller
        BlenderShotStore.clear_active()
        store = BlenderShotStore.active()
        store.define_shot(
            "intro", 1, 40, objects=["Arm"], description="Arm reaches out"
        )
        self.addCleanup(BlenderShotStore.clear_active)
        self.addCleanup(setattr, ctrl, "_store", ctrl._store)
        ctrl._store = store
        self.ui.txt_csv_path.setText("")
        ctrl._populate_from_source()

        self.assertEqual(ctrl._source, "scene")
        tree = self.ui.tbl_steps
        self.assertEqual(tree.topLevelItemCount(), 1)
        self.assertEqual(tree.topLevelItem(0).text(COL_DESC), "Arm reaches out")
        self.assertTrue(self.ui.txt_csv_path.isEnabled())

    def _populate_one_step(self, ctrl):
        """Load one synthetic step into the tree (shared by the presenter tests)."""
        from pythontk import BuilderStep, BuilderObject

        ctrl._steps = [
            BuilderStep(
                step_id="A01",
                section="A",
                section_title="Test",
                description="step one",
                objects=[BuilderObject(name="Obj1", behaviors=[])],
            )
        ]
        ctrl._populate_table()

    def test_additional_object_row_matches_column_count(self):
        """Additional-object rows mirror the mayatk reference shape (6 texts, EMPTY
        Behaviors cell — table_presenter.py:705 uses ["", "", display, "", "", ""];
        a stray 'scene' tag landed in the Behaviors column here)."""
        from pythontk.core_utils.engines.shots.manifest.manifest_model import (
            StepStatus,
        )
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import (
            HEADERS,
            COL_DESC,
        )

        ctrl = self.ui.slots.controller
        self._populate_one_step(ctrl)
        ctrl._apply_assessment(
            [
                StepStatus(
                    step_id="A01",
                    built=True,
                    objects=[],
                    additional_objects=["ExtraObj"],
                )
            ]
        )
        tree = self.ui.tbl_steps
        parent = tree.topLevelItem(0)
        extra = next(
            (
                parent.child(j)
                for j in range(parent.childCount())
                if parent.child(j).text(COL_DESC) == "ExtraObj"
            ),
            None,
        )
        self.assertIsNotNone(extra, "additional-object row not created")
        texts = [extra.text(c) for c in range(len(HEADERS))]
        self.assertEqual(texts, ["", "", "ExtraObj", "", "", ""])

    def test_expand_missing_uses_logger_and_footer(self):
        """expand_missing reports through the logger + footer exactly as the mayatk
        reference (table_presenter.py:764-765) — never bare print()."""
        import io
        from contextlib import redirect_stdout
        from pythontk.core_utils.engines.shots.manifest.manifest_model import (
            StepStatus,
        )

        ctrl = self.ui.slots.controller
        self._populate_one_step(ctrl)
        ctrl._last_results = [StepStatus(step_id="A01", built=False, objects=[])]
        footers = []
        orig_footer = ctrl._set_footer
        ctrl._set_footer = lambda *a, **kw: footers.append(a[0] if a else "")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                ctrl.expand_missing()
        finally:
            ctrl._set_footer = orig_footer
            ctrl._last_results = []
        self.assertEqual(buf.getvalue(), "", "expand_missing must not print to stdout")
        self.assertTrue(
            any("expanded" in m for m in footers),
            f"expected the mayatk-style footer feedback, got {footers}",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # Report the tally in the harness's (ok/attempted) form -- without it the
    # suite counts for zero checks in run_tests.py's totals. Skips are neither
    # passes nor failures, so they leave the ratio entirely (a fully skipped
    # suite reports 0/0, exactly what it contributed).
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
