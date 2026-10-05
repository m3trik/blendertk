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
populated -- plus the controller's own rules mayatk's ``test_shot_manifest``
pins (store events, re-apply reporting, template migration, the playhead).
Run under the workspace ``.venv``::

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

    # -- controller behaviour (mirrors mayatk's test_shot_manifest) ---------

    def _fresh_store(self, ctrl, listen: bool = False):
        """A fresh active store for *ctrl* (``listen``: bound to it, as the
        panel is to the scene's), restored to a fresh one afterwards."""
        from blendertk import BlenderShotStore

        def restore(first_shown=ctrl._first_shown, steps=list(ctrl._steps)):
            ctrl._unbind_store_listener()
            BlenderShotStore.clear_active()
            ctrl._store = None
            ctrl._bind_store_listener()
            ctrl._first_shown = first_shown
            ctrl._load_data(steps)

        self.addCleanup(restore)
        ctrl._unbind_store_listener()
        BlenderShotStore.clear_active()
        # Built, not ``active()``: under Blender ``active()`` re-attaches the
        # scene record, so one test's shots persisted into the next (a
        # second ``define_shot('intro')`` refused, a table one row long).
        store = BlenderShotStore()
        BlenderShotStore.set_active(store)
        ctrl._store = store
        if listen:
            ctrl._bind_store_listener()
        return store

    def _footers(self, ctrl) -> list:
        """Every footer *ctrl* sets from here on."""
        from unittest import mock

        footers = []
        patcher = mock.patch.object(
            ctrl, "_set_footer", side_effect=lambda text, **_kw: footers.append(text)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return footers

    def test_a_recipe_change_keeps_the_detected_steps_and_typed_ranges(self):
        """Bug: ``update_effect_recipe`` fires the same bare ``SettingsChanged``
        a detection setting does, so a Render Effects spinbox tick re-ran
        ``detect()`` -- the steps regenerated, a typed range gone.
        Fixed: 2026-10-04
        """
        from unittest import mock
        from pythontk import BuilderStep, StepStatus

        ctrl = self.ui.slots.controller
        store = self._fresh_store(ctrl, listen=True)
        regions = [{"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["Cube"]}]
        steps, ranges = BuilderStep.from_detection(regions)
        ctrl._load_data(steps, ranges=dict(ranges))  # detect mode
        ctrl._first_shown = True
        ctrl._user_ranges["Shot 1"] = (12.0, 60.0)  # typed by the user
        ctrl._last_results = [StepStatus(step_id="Shot 1", built=False)]
        ctrl._update_build_button()
        self.assertTrue(self.ui.b003.isEnabled())  # that Assess wants a Build

        with mock.patch.object(ctrl, "_detect_regions", return_value=regions) as det:
            store.update_effect_recipe(fade_frames=20)
            det.assert_not_called()
            self.assertEqual(ctrl._steps, steps)
            self.assertEqual(ctrl._user_ranges["Shot 1"], (12.0, 60.0))
            # ...but the last Assess judged keys the recipe no longer makes.
            self.assertEqual(ctrl._last_results, [])
            self.assertFalse(self.ui.b003.isEnabled())

            store.detection_threshold = 9.0  # a detection input does re-detect
            store.notify_settings_changed()
            det.assert_called_once()

    def test_shots_deleted_elsewhere_empty_the_table_without_asking(self):
        """Bug: the scene's shots all deleted in another panel, the store event
        reloaded them, found none and fell through to the interactive
        ``detect()`` -- whose selected-keys mode pops a modal "No keys
        selected" for an action taken elsewhere.
        Fixed: 2026-10-04
        """
        from unittest import mock
        from pythontk import ShotRemoved

        ctrl = self.ui.slots.controller
        store = self._fresh_store(ctrl)
        store.define_shot("intro", 1, 40, objects=["Arm"])
        store.detection_mode = "skip_zero"  # a selected-keys mode
        ctrl._first_shown = True
        ctrl._load_scene_shots()
        for shot in list(store.shots):
            store.remove_shot(shot.shot_id)
        footers = self._footers(ctrl)
        with (
            mock.patch.object(ctrl, "_detect_regions", return_value=[]) as det,
            mock.patch.object(ctrl, "sb") as sb,
        ):
            ctrl._on_store_event(ShotRemoved(shot_id=1))
        sb.message_box.assert_not_called()
        det.assert_not_called()
        self.assertEqual(ctrl._steps, [])
        self.assertEqual(ctrl._source, "scene")  # still following the store
        self.assertIn("no shots", footers[-1].lower())

    def test_a_reapply_that_keys_nothing_says_so_and_the_focused_key_raises(self):
        """Bug: ``_reapply_behavior`` ignored ``reapply_object``'s ``False``
        and re-assessed as if it had worked; the focused Key's ``apply`` then
        reported "Re-applied ..." whatever happened.
        Fixed: 2026-10-04
        """
        from unittest import mock
        from pythontk import BuilderObject, BuilderStep

        ctrl = self.ui.slots.controller
        store = self._fresh_store(ctrl)
        store.define_shot("A01", 1, 40)
        ctrl._load_data(
            [BuilderStep(step_id="A01", section="A", section_title="", description="")]
        )
        obj = BuilderObject(name="ghost", behaviors=["fade_in"])
        builder = mock.MagicMock()
        builder.reapply_object.return_value = False
        footers = self._footers(ctrl)
        with (
            mock.patch.object(ctrl, "_manifest", return_value=builder),
            mock.patch.object(ctrl, "assess") as assess,
            mock.patch.object(ctrl, "sb") as sb,
        ):
            self.assertIs(ctrl._reapply_behavior("A01", obj), False)
            assess.assert_not_called()
            self.assertIn("'ghost' is not in the scene", footers[-1])

            ctrl._open_effect("A01", obj, "opacity")
            apply = sb.get_slots_instance.return_value.focus.call_args.kwargs["apply"]
            with self.assertRaises(RuntimeError):
                apply()
            builder.reapply_object.return_value = True
            self.assertEqual(apply(), "Re-applied ghost's behaviors in A01.")
            assess.assert_called_once_with(skip_key_check=True)

    def test_a_retired_templates_options_join_the_saved_ones(self):
        """Bug: the retired template's options were carried only when its
        replacement had none saved, so saved ``default`` options silently
        lost ``audio: derive`` -- while the log said it was carried.
        Fixed: 2026-10-04
        """
        import json
        import types
        from unittest import mock
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        ctrl = self.ui.slots.controller
        key = "mapping_options/default"
        self.addCleanup(ctrl._settings.setValue, key, ctrl._settings.value(key, ""))
        ctrl._settings.setValue(key, json.dumps({"fill_missing_assets": True}))
        retired = Mapping.retired("speedrun")
        templates = types.SimpleNamespace(active="speedrun")
        with mock.patch.object(Mapping, "templates", return_value=templates):
            self.assertEqual(
                ctrl._migrate_retired_mapping("speedrun", *retired), "default"
            )
        self.assertEqual(
            json.loads(ctrl._settings.value(key)),
            {"fill_missing_assets": True, "audio": "derive"},
        )

    def test_set_range_to_current_frame_keeps_the_subframe(self):
        """Bug: read ``scene.frame_current`` -- an int -- though the playhead
        keeps sub-frames (mayatk's ``currentTime`` keeps the fraction).
        Fixed: 2026-10-04
        """
        import types
        from unittest import mock
        from pythontk import BuilderStep

        ctrl = self.ui.slots.controller
        self.addCleanup(setattr, ctrl, "_user_ranges", dict(ctrl._user_ranges))
        self.addCleanup(setattr, ctrl, "_steps", list(ctrl._steps))
        ctrl._steps = [
            BuilderStep(step_id="A01", section="A", section_title="", description="")
        ]
        scene = types.SimpleNamespace(frame_current=12, frame_current_final=12.5)
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(scene=scene))
        with (
            mock.patch.dict(sys.modules, {"bpy": bpy}),
            mock.patch.object(ctrl, "_refresh_ranges"),
        ):
            ctrl._set_range_to_current_frame(None, "A01")
        self.assertEqual(ctrl._user_ranges["A01"], (12.5, None))


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
