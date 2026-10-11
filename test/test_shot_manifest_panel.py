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

    def test_export_sits_before_assess_with_its_option_menu(self):
        """Export (2026-10-10, in place of the header menu's Copy Asset Names)
        sits in the footer before Assess and Build, its options in its own
        menu, and a click exports."""
        from unittest import mock

        footer, ctrl = self.ui.footer, self.ui.slots.controller

        def place(widget):
            while widget.parentWidget() is not footer:
                widget = widget.parentWidget()
            return footer.main_layout.indexOf(widget)

        self.assertLess(place(self.ui.b004), place(self.ui.b002))
        self.assertLess(place(self.ui.b002), place(self.ui.b003))
        menu = self.ui.b004.option_box.menu
        for widget in ctrl._export_widgets.values():
            self.assertTrue(menu.isAncestorOf(widget))
        self.assertIsNone(getattr(self.ui, "btn_copy_asset_names", None))
        with mock.patch.object(ctrl, "export") as export:
            self.ui.b004.click()
        export.assert_called_once_with()

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

        with (
            mock.patch.object(ctrl, "_detect_regions", return_value=regions) as det,
            mock.patch.object(ctrl, "_reassess_soon") as soon,
        ):
            store.update_effect_recipe(fade_frames=20)
            soon.assert_called_once_with()  # ...run again once settled (mayatk)
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

    def test_a_bound_edit_made_elsewhere_reaches_a_sheets_built_row(self):
        """A bound edit written straight onto the shot (no ShotUpdated) is
        announced by its ``scene_edit`` (``ShotsEdited``), and a sheet's built
        row follows it (mayatk 2026-10-07: "the manifest doesn't refresh
        properly on many actions")."""
        from pythontk import BuilderObject, BuilderStep
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import (
            COL_END,
            COL_START,
        )

        ctrl = self.ui.slots.controller
        store = self._fresh_store(ctrl, listen=True)
        store.define_shot("A01", 10, 50, metadata={"step": "A01"})
        step = BuilderStep("A01", "A", "t", "", [BuilderObject("Arm")])
        ctrl._load_data([step], csv_path="X:/sheet.csv")
        ctrl._first_shown = True
        tree = ctrl.ui.tbl_steps

        def cells():
            row = tree.topLevelItem(0)
            return row.text(COL_START), row.text(COL_END)

        self.assertEqual(cells(), ("10", "50"), "a built row shows its shot")
        with store.scene_edit("Trim"):
            store.shot_by_name("A01").end = 40.0
            store.mark_dirty()
        self.assertEqual(cells(), ("10", "40"))

    def test_a_shot_deleted_or_added_elsewhere_reshapes_a_sheet(self):
        """A sheet follows the pairing, not only the ranges: a deleted shot's
        step reads unbuilt, an added shot is listed "not in doc" and its range
        follows (mayatk mirror)."""
        from pythontk import BuilderObject, BuilderStep
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import (
            COL_END,
            COL_START,
        )

        ctrl = self.ui.slots.controller
        store = self._fresh_store(ctrl, listen=True)
        store.define_shot("A01", 10, 50, metadata={"step": "A01"})
        step = BuilderStep("A01", "A", "t", "", [BuilderObject("Arm")])
        ctrl._load_data([step], csv_path="X:/sheet.csv")
        ctrl._first_shown = True
        tree = ctrl.ui.tbl_steps

        def cells(name):
            for i in range(tree.topLevelItemCount()):
                row = tree.topLevelItem(i)
                if row.text(0) == name:
                    return row.text(COL_START), row.text(COL_END)
            return None

        store.remove_shot(store.shot_by_name("A01").shot_id)
        self.assertEqual(cells("A01"), ("", ""))
        extra = store.define_shot("Extra", 300, 340)
        self.assertEqual(cells("Extra"), ("300", "340"))
        with store.scene_edit("Trim"):
            extra.end = 330.0
            store.mark_dirty()
        self.assertEqual(cells("Extra"), ("300", "330"))

    def test_build_options_reach_the_engine(self):
        """Rebuild Edited Shots (off by default) and the behavior toggles
        (mayatk mirror), kept per user."""
        from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        ctrl = self.ui.slots.controller
        self.addCleanup(ctrl._settings.clear, "rebuild_edited")
        self.addCleanup(ctrl._settings.clear, "disabled_behaviors")
        self.assertFalse(ctrl._chk_rebuild_edited.isChecked())
        self.assertEqual(
            sorted(ctrl._behavior_checks), sorted(Behaviors.list_behaviors())
        )
        ctrl._chk_rebuild_edited.setChecked(True)
        ctrl._behavior_checks["highlight"].setChecked(False)
        try:
            mani = ctrl._manifest()
            self.assertTrue(mani.rebuild_edited)
            self.assertEqual(mani.disabled_behaviors, frozenset({"highlight"}))
        finally:
            ctrl._chk_rebuild_edited.setChecked(False)
            ctrl._behavior_checks["highlight"].setChecked(True)
        self.assertEqual(ctrl._manifest().disabled_behaviors, frozenset())

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


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestShotManifestLocalEdits(unittest.TestCase):
    """Local Edits, acted out on the real panel: double-click a cell, type,
    press Return; trigger the context menu's own actions.  The sheet is never
    changed, the edits live on the scene's store, and Build follows them."""

    SHEET = "local_edits_sheet.csv"  # a source key only: nothing reads it

    @classmethod
    def setUpClass(cls):
        TestShotManifestPanelLoads.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        TestShotManifestPanelLoads.tearDownClass.__func__(cls)

    @staticmethod
    def _sheet():
        from pythontk import BuilderStep, BuilderObject

        return [
            BuilderStep(
                "A01",
                "A",
                "Access",
                "Open the door " * 12,
                [
                    BuilderObject("door", behaviors=["fade_in"]),
                    BuilderObject("bolt01"),
                ],
            ),
            BuilderStep("A02", "A", "Access", "Unhook", [BuilderObject("latch")]),
        ]

    def setUp(self):
        from blendertk import BlenderShotStore

        self.ctrl = ctrl = self.ui.slots.controller
        self.tree = self.ui.tbl_steps
        self.addCleanup(BlenderShotStore.clear_active)
        self.addCleanup(setattr, ctrl, "_store", ctrl._store)
        BlenderShotStore.clear_active()
        self.store = BlenderShotStore()
        BlenderShotStore.set_active(self.store)
        ctrl._store = self.store
        self.addCleanup(ctrl._editor._chk.setChecked, False)
        self.footers = []
        from unittest import mock

        patcher = mock.patch.object(
            ctrl,
            "_set_footer",
            side_effect=lambda text, **_kw: self.footers.append(text),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _load(self, on: bool = True):
        """Load the sheet; *on* False views the original sheet (the header
        menu's View Original Sheet, checked)."""
        self.ctrl._editor._chk.setChecked(not on)
        self.ctrl._load_data(self._sheet(), csv_path=self.SHEET)
        self._settle()

    def _settle(self):
        for _ in range(5):
            self.app.processEvents()

    def _row(self, step_id):
        from qtpy.QtCore import Qt

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if getattr(data, "step_id", None) == step_id:
                return item
        return None

    def _child(self, step_id, name):
        from qtpy.QtCore import Qt

        row = self._row(step_id)
        for j in range(row.childCount()):
            if getattr(row.child(j).data(0, Qt.UserRole), "name", None) == name:
                return row.child(j)
        return None

    def _type_into(self, item, column, text):
        """Double-click the cell (the tree's own signal: the panel is never
        shown here), type *text* over it, press Return."""
        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest

        self.tree.itemDoubleClicked.emit(item, column)
        self._settle()
        editor = self.tree.indexWidget(self.tree.indexFromItem(item, column))
        self.assertIsNotNone(editor, "the double-click opened no editor")
        editor.setText(text)
        QTest.keyClick(editor, Qt.Key_Return)
        self._settle()
        return editor

    def _actions(self, item, selected=()):
        """The Local Edits actions the row's context menu offers, by text."""
        from qtpy.QtWidgets import QMenu

        menu = QMenu(self.tree)
        self.addCleanup(menu.deleteLater)
        self.ctrl._editor.add_menu_actions(menu, item, list(selected))
        return {act.text(): act for act in menu.actions()}

    def _trigger(self, item, text, selected=()):
        """Trigger the row's menu action *text* (``"Menu > Action"`` for a
        submenu's), the menus held while it runs: a submenu action's wrapper
        dies with the wrapper of the menu it came from."""
        from qtpy.QtWidgets import QMenu

        menu = QMenu(self.tree)
        self.addCleanup(menu.deleteLater)
        self.ctrl._editor.add_menu_actions(menu, item, list(selected))
        top, _, leaf = text.partition(" > ")
        actions = menu.actions()
        act = next((a for a in actions if a.text() == top), None)
        self.assertIsNotNone(act, f"no '{top}' in the menu")
        if leaf:
            sub_menu = act.menu()
            subs = sub_menu.actions()
            act = next((a for a in subs if a.text() == leaf), None)
            self.assertIsNotNone(act, f"no '{leaf}' under '{top}'")
        act.trigger()
        self._settle()

    # ---- object rows: the menu acts on every selected one ---------------------

    def _object_menu(self, item, pick):
        """Right-click *item*; return the object actions' texts and run *pick*
        (a text) as if chosen."""
        from unittest import mock
        from qtpy.QtWidgets import QMenu

        seen = []

        def chosen(menu, *_args):
            actions = menu.actions()
            seen.extend(a.text() for a in actions)
            return next((a for a in actions if a.text() == pick), None)

        with mock.patch.object(self.tree, "itemAt", return_value=item):
            with mock.patch.object(QMenu, "exec_", chosen):
                self.ctrl._show_item_menu(self.tree.visualItemRect(item).center())
        return seen

    def test_object_actions_take_every_selected_object_row(self):
        """Regression: the actions took the clicked row only -- selecting the
        objects a name several nodes share was one row at a time."""
        from unittest import mock
        from qtpy.QtWidgets import QApplication

        self._load(on=False)
        door, bolt = self._child("A01", "door"), self._child("A01", "bolt01")
        latch = self._child("A02", "latch")
        revealed = []
        with mock.patch.object(self.ctrl, "_show_in_outliner", revealed.append):
            self.tree.clearSelection()
            for item in (door, bolt, latch):
                item.setSelected(True)
            texts = self._object_menu(door, "Show 3 Objects in Outliner")
            self.assertIn("Copy 3 Names to Clipboard", texts)
            self.assertEqual(revealed, [["door", "bolt01", "latch"]])
            self._object_menu(bolt, "Copy 3 Names to Clipboard")
            self.assertEqual(QApplication.clipboard().text(), "door\nbolt01\nlatch")
            # A row outside the selection acts on itself alone.
            door.setSelected(False)
            self._object_menu(door, "Show 'door' in Outliner")
            self.assertEqual(revealed[-1], ["door"])

    # ---- the option ----------------------------------------------------------

    def test_in_force_by_default_and_the_original_view_edits_nothing(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        self.assertTrue(self.ctrl._editor.enabled)
        self.assertEqual(self.ctrl._editor._chk.text(), "View Original Sheet")
        self._load(on=False)
        self.assertIs(self.ctrl._steps, self.ctrl._sheet_steps)
        row = self._row("A01")
        self.assertTrue(self.ctrl._editor.begin_edit(row, COL_STEP))
        self.assertIsNone(self.ctrl._editor._pending, "no editor opened")
        self.assertEqual(self._actions(row), {})
        self.assertFalse(self.ctrl._editor._btn_reset.isEnabled())

    def test_the_original_view_shows_the_sheet_and_back_restores_every_edit(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        self._load()
        self._type_into(self._row("A01"), COL_STEP, "Door_Open")
        self.assertEqual(self._row("A01").text(COL_STEP), "Door_Open")
        self.assertTrue(self.ctrl._editor._btn_reset.isEnabled())

        self.ctrl._editor._chk.setChecked(True)
        self._settle()
        self.assertEqual(self._row("A01").text(COL_STEP), "A01")
        self.assertEqual(self.ctrl._steps, self._sheet())
        self.assertFalse(self.ctrl._editor._btn_reset.isEnabled())
        self.assertFalse(self.ui.b003.isEnabled(), "Build is off in the view")

        self.ctrl._editor._chk.setChecked(False)
        self._settle()
        self.assertEqual(self._row("A01").text(COL_STEP), "Door_Open")

    def test_edits_live_on_the_scene_and_survive_a_reload(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_DESC

        self._load()
        self._type_into(self._child("A01", "bolt01"), COL_DESC, "bolt_01")
        saved = self.store.manifest_edits[self.SHEET]
        self.assertEqual(saved["objects"], {"A01": {"bolt01": {"name": "bolt_01"}}})
        self.ctrl._load_data(self._sheet(), csv_path=self.SHEET)  # re-parsed
        self.assertEqual(
            [o.name for o in self.ctrl._steps[0].objects], ["door", "bolt_01"]
        )
        # Another sheet has its own layer.
        self.ctrl._load_data(self._sheet(), csv_path="other.csv")
        self.assertEqual(self.ctrl._steps, self._sheet())

    def test_reset_drops_every_edit_and_the_scenes_record(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        self._load()
        self._type_into(self._row("A02"), COL_STEP, "Unhook")
        self.ctrl._editor.reset(confirm=False)
        self._settle()
        self.assertNotIn(self.SHEET, self.store.manifest_edits)
        self.assertEqual(self._row("A02").text(COL_STEP), "A02")
        self.assertFalse(self.ctrl._editor._btn_reset.isEnabled())

    # ---- cells ---------------------------------------------------------------

    def test_a_renamed_built_step_builds_into_the_same_shot(self):
        import pythontk as ptk
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        ptk.ShotManifest(self.store).update(self._sheet(), initial_shot_length=50)
        self._load()
        self._type_into(self._row("A01"), COL_STEP, "Door_Open")
        row = self._row("A01")
        self.assertTrue(row.font(COL_STEP).italic())
        self.assertIn("Sheet: A01", row.toolTip(COL_STEP))
        self.assertIn("Build renames its shot", self.footers[-1])
        ptk.ShotManifest(self.store).update(self.ctrl._steps, remove_missing=False)
        self.assertEqual(
            [s.name for s in self.store.sorted_shots()], ["Door_Open", "A02"]
        )
        self.assertEqual(self.store.shot_by_name("Door_Open").metadata["step"], "A01")

    def test_a_name_another_step_has_is_refused(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        self._load()
        self._type_into(self._row("A01"), COL_STEP, "a02")
        self.assertFalse(self.ctrl._editor.edits.has_edits)
        self.assertIn("already names step", self.footers[-1])
        self.assertEqual(self._row("A01").text(COL_STEP), "A01")

    def test_an_object_rename_edits_the_full_name_and_marks_the_sheets(self):
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_DESC

        self._load()
        editor = self._type_into(self._child("A01", "door"), COL_DESC, "red_door")
        self.assertIsNotNone(editor)
        child = self._child("A01", "red_door")
        self.assertTrue(child.font(COL_DESC).italic())
        self.assertIn("Sheet: door", child.toolTip(COL_DESC))
        self.assertEqual(self.ctrl._steps[0].objects[0].behaviors, ["fade_in"])

    def test_a_long_description_shows_in_full_in_its_tooltip(self):
        self._load()
        self.assertTrue(self.tree.elided_tooltips)
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_DESC

        index = self.tree.indexFromItem(self._row("A01"), COL_DESC)
        self.tree.header().resizeSection(COL_DESC, 80)
        self.assertTrue(self.tree.is_elided(index))
        self.assertIn(
            "Open the door Open the door", self.tree.item_tooltip(index, None)
        )

    # ---- the context menu ----------------------------------------------------

    def test_add_a_step_name_it_and_move_objects_into_it(self):
        import pythontk as ptk
        from blendertk.anim_utils.shots.shot_manifest.manifest_data import COL_STEP

        self._load()
        self._trigger(self._row("A02"), "Add Step After 'A02'")
        new = self.ctrl._steps[-1]
        self.assertEqual(new.step_id, "NEW_STEP")
        # The new row opens on its name: type it.
        editor = self.tree.indexWidget(
            self.tree.indexFromItem(self._row(new.step_id), COL_STEP)
        )
        self.assertIsNotNone(editor)
        editor.setText("Inspect")
        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest

        QTest.keyClick(editor, Qt.Key_Return)
        self._settle()
        self._trigger(self._child("A01", "door"), "Move 'door' To > Inspect")
        self.assertEqual(
            [(s.shot_name, [o.name for o in s.objects]) for s in self.ctrl._steps],
            [("A01", ["bolt01"]), ("A02", ["latch"]), ("Inspect", ["door"])],
        )
        self.assertIn(
            "Moved here from 'A01'", self._child("NEW_STEP", "door").toolTip(2)
        )
        actions = ptk.ShotManifest(self.store).update(
            self.ctrl._steps, initial_shot_length=50
        )
        self.assertEqual(actions["NEW_STEP"], "created")
        self.assertEqual(self.store.shot_by_name("Inspect").objects, ["door"])
        # Removing the added step puts the door back where the sheet has it.
        self._trigger(self._row("NEW_STEP"), "Remove 'Inspect'")
        self.assertEqual(
            [o.name for o in self.ctrl._steps[0].objects], ["door", "bolt01"]
        )

    def test_hide_and_show_objects_and_unbuilt_steps(self):
        import pythontk as ptk

        self._load()
        self._trigger(self._child("A01", "bolt01"), "Hide 'bolt01'")
        self.assertEqual([o.name for o in self.ctrl._steps[0].objects], ["door"])
        self._trigger(self._row("A01"), "Show Hidden Objects (1) > bolt01")
        self.assertEqual(
            [o.name for o in self.ctrl._steps[0].objects], ["door", "bolt01"]
        )

        self._trigger(self._row("A02"), "Hide 'A02'")
        self.assertEqual([s.step_id for s in self.ctrl._steps], ["A01"])
        self._trigger(self._row("A01"), "Show Hidden Steps (1) > A02")
        self.assertEqual([s.step_id for s in self.ctrl._steps], ["A01", "A02"])

        # A built step stays: Build never removes a shot.
        ptk.ShotManifest(self.store).update(self.ctrl._steps, initial_shot_length=50)
        self.assertFalse(self._actions(self._row("A01"))["Hide 'A01'"].isEnabled())

    def test_a_reloaded_sheet_shows_an_added_steps_shot_range(self):
        """Bug: ``_load_csv`` seeded the built steps' Start/End from the
        sheet's steps alone, so a built step Local Edits added showed an
        auto-filled range instead of its shot's.
        Fixed: 2026-10-06"""
        from pythontk import ManifestEdits

        sheet = os.path.join(HERE, "temp_tests", "local_edits_reload.csv")
        os.makedirs(os.path.dirname(sheet), exist_ok=True)
        with open(sheet, "w", encoding="utf-8") as f:
            f.write(
                "SECTION A: Access\n"
                "Step,Step Contents,Asset Names\n"
                "A01.),Open the door,door\n"
                "A02.),Unhook,latch\n"
            )
        self.addCleanup(os.remove, sheet)
        edits = ManifestEdits()
        new = edits.add_step("A01")
        self.store.manifest_edits[sheet] = edits.to_dict()
        self.store.define_shot("A01", 1, 20, metadata={"step": "A01"})
        self.store.define_shot("Inspect", 30, 45, metadata={"step": new})
        self.ctrl._editor._chk.setChecked(False)
        self.assertTrue(self.ctrl._load_csv(sheet))
        self.assertEqual([s.step_id for s in self.ctrl._steps], ["A01", new, "A02"])
        self.assertEqual(self.ctrl._user_ranges[new], (30, 45))
        self.assertEqual(self.ctrl._user_ranges["A01"], (1, 20))
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_rename_all_and_revert(self):
        from unittest import mock
        from pythontk import BuilderObject

        self._load()
        self.ctrl._sheet_steps[1].objects.append(BuilderObject("door"))
        self.ctrl._editor.refresh()
        with mock.patch.object(self.ctrl.sb, "input_dialog", return_value="red_door"):
            self._trigger(self._child("A02", "door"), "Rename All 'door'…")
        self.assertEqual(
            [[o.name for o in s.objects] for s in self.ctrl._steps],
            [["red_door", "bolt01"], ["latch", "red_door"]],
        )
        self._trigger(self._child("A01", "red_door"), "Revert 'red_door'")
        self.assertEqual(
            [[o.name for o in s.objects] for s in self.ctrl._steps],
            [["door", "bolt01"], ["latch", "red_door"]],
        )

    def test_behavior_ticks_are_edits_while_they_are_in_force(self):
        from unittest import mock

        self._load(on=False)
        obj = self.ctrl._steps[0].objects[0]
        box = mock.Mock(**{"isChecked.return_value": False})
        self.ctrl._on_behaviors_changed(obj, [box], step_id="A01")
        self._settle()
        self.assertEqual(self.ctrl._steps[0].objects[0].behaviors, ["fade_in"])
        self.assertFalse(self.ctrl._editor.edits.has_edits)  # the sheet's view

        self._load()
        obj = self.ctrl._steps[0].objects[0]
        self.ctrl._on_behaviors_changed(obj, [box], step_id="A01")
        self._settle()
        door = self.ctrl._steps[0].objects[0]  # the redrawn table's
        self.assertEqual(door.behaviors, [])
        self.assertEqual(door.sheet, {"behaviors": ["fade_in"]})
        self.assertTrue(self.ctrl._edited_since_assess)
        self.assertEqual(
            self.store.manifest_edits[self.SHEET]["objects"]["A01"]["door"],
            {"behaviors": []},
        )
        self.assertEqual(self.ctrl._sheet_steps[0].objects[0].behaviors, ["fade_in"])


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestShotManifestRowTags(unittest.TestCase):
    """Colour tags on the real panel: the ``.ui``'s tree carries the strip,
    a pick from a step row's context menu is kept on the scene's store under
    the sheet, and the template's ``color_by`` shows under the picks."""

    SHEET = "row_tags_sheet.csv"  # a source key only: nothing reads it

    @classmethod
    def setUpClass(cls):
        TestShotManifestPanelLoads.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        TestShotManifestPanelLoads.tearDownClass.__func__(cls)

    def setUp(self):
        from blendertk import BlenderShotStore

        self.ctrl = ctrl = self.ui.slots.controller
        self.tree = self.ui.tbl_steps
        self.tags = ctrl._row_tags
        self.addCleanup(BlenderShotStore.clear_active)
        self.addCleanup(setattr, ctrl, "_store", ctrl._store)
        self.addCleanup(setattr, ctrl, "_active_mapping", ctrl._active_mapping)
        BlenderShotStore.clear_active()
        self.store = BlenderShotStore()
        BlenderShotStore.set_active(self.store)
        ctrl._store = self.store

    def _load(self):
        from pythontk import BuilderStep

        steps = [
            BuilderStep("A01", "A", "Access", "Open", []),
            BuilderStep("A02", "A", "Access", "Unhook", []),
            BuilderStep("B01", "B", "Repair", "Swap", []),
        ]
        self.ctrl._load_data(steps, csv_path=self.SHEET)
        for _ in range(5):
            self.app.processEvents()
        return [self.tree.topLevelItem(i) for i in range(len(steps))]

    def _pick(self, item, swatch):
        """Right-click step row *item*, click swatch *swatch* (0: clear)."""
        from unittest import mock

        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest
        from qtpy.QtWidgets import QMenu, QWidgetAction

        def chosen(menu, *_args):
            row = next(
                a.defaultWidget()
                for a in menu.actions()
                if isinstance(a, QWidgetAction)
            )
            QTest.mouseClick(row.swatches[swatch], Qt.LeftButton)
            return None

        with mock.patch.object(self.tree, "itemAt", return_value=item):
            with mock.patch.object(QMenu, "exec_", chosen):
                self.ctrl._show_item_menu(self.tree.visualItemRect(item).center())

    def test_the_trees_strip_is_attached(self):
        from uitk import RowTags

        self.assertIs(RowTags.of(self.tree), self.tags)

    def test_a_pick_is_kept_on_the_store_and_survives_a_reload(self):
        _a01, a02, _b01 = self._load()
        self.tree.clearSelection()
        self._pick(a02, 6)
        self.assertEqual(self.store.manifest_tags, {self.SHEET: {"A02": "tag6"}})
        _a01, a02, _b01 = self._load()
        self.assertEqual(self.tags.shown_tag(a02), "tag6")

    def test_a_closed_item_menu_is_not_kept(self):
        """Bug: every right-click parented a fresh ``QMenu`` to the tree and
        never deleted it, so each menu lived as long as the panel.
        Fixed: 2026-10-09"""
        from qtpy.QtCore import QCoreApplication, QEvent, Qt
        from qtpy.QtWidgets import QMenu

        a01, _a02, _b01 = self._load()
        for swatch in (1, 0):
            self._pick(a01, swatch)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(
            self.tree.findChildren(QMenu, "", Qt.FindDirectChildrenOnly), []
        )

    def test_color_by_section_colours_each_section(self):
        """The next section takes a far hue, never the neighbouring one
        (``Palette.contrast_order``: tag4 after tag1)."""
        self.ctrl._active_mapping = {"color_by": "section"}
        rows = self._load()
        self.assertEqual(
            [self.tags.shown_tag(row) for row in rows], ["tag1", "tag1", "tag4"]
        )


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestShotManifestUnlistedShots(unittest.TestCase):
    """mayatk's ``TestShotsTheDocDoesNotList`` on the real panel: a shot no
    step pairs with sits where the timeline has it with its members under
    it, joins the manifest from its menu; a section locks; steps with no
    shot hide."""

    SHEET = "unlisted_sheet.csv"  # a source key only: nothing reads it

    @classmethod
    def setUpClass(cls):
        TestShotManifestPanelLoads.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        TestShotManifestPanelLoads.tearDownClass.__func__(cls)

    def setUp(self):
        from blendertk import BlenderShotStore

        self.ctrl = ctrl = self.ui.slots.controller
        self.tree = self.ui.tbl_steps
        self.addCleanup(BlenderShotStore.clear_active)
        self.addCleanup(setattr, ctrl, "_store", ctrl._store)
        self.addCleanup(setattr, ctrl, "_active_mapping", ctrl._active_mapping)
        self.addCleanup(
            ctrl._settings.setValue,
            "hide_missing",
            ctrl._settings.value("hide_missing"),
        )
        BlenderShotStore.clear_active()
        self.store = BlenderShotStore()
        BlenderShotStore.set_active(self.store)
        ctrl._store = self.store
        ctrl._active_mapping = None
        self.store.define_shot(
            "A01_1", 0, 111, objects=["KEYS", "LOCK"], metadata={"step": "A01"}
        )
        self.store.define_shot("A01_2", 121, 231, objects=["LOCK_B"])
        self.store.define_shot("A02", 241, 321, objects=["DOOR"])

    def _load(self, hide_missing=False):
        from pythontk import BuilderStep

        self.ctrl._settings.setValue("hide_missing", hide_missing)
        steps = [
            BuilderStep("A01", "A", "Access", "Unlock", []),
            BuilderStep("A02", "A", "Access", "Open", []),
            BuilderStep("B01", "B", "Repair", "Swap", []),
        ]
        self.ctrl._load_data(steps, csv_path=self.SHEET)
        for _ in range(5):
            self.app.processEvents()

    def _names(self):
        return [
            self.tree.topLevelItem(i).text(0)
            for i in range(self.tree.topLevelItemCount())
        ]

    def _row(self, text):
        return next(
            self.tree.topLevelItem(i)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).text(0) == text
        )

    def _menu(self, item, pick):
        """Right-click *item*, trigger the entry starting with *pick*."""
        from unittest import mock

        from qtpy.QtWidgets import QMenu

        def run(menu, *_args):
            def walk(m):
                for a in m.actions():
                    if a.menu():
                        found = walk(a.menu())
                        if found:
                            return found
                    elif a.text().startswith(pick):
                        return a
                return None

            action = walk(menu)
            self.assertIsNotNone(action, pick)
            action.trigger()

        with mock.patch.object(self.tree, "itemAt", return_value=item):
            with mock.patch.object(QMenu, "exec_", run):
                self.ctrl._show_item_menu(self.tree.visualItemRect(item).center())
        for _ in range(5):
            self.app.processEvents()

    def test_a_split_shot_sits_where_the_timeline_has_it_with_its_members(self):
        self._load()
        self.assertEqual(self._names(), ["A01", "A01_2", "A02", "B01"])
        row = self._row("A01_2")
        self.assertEqual(
            [row.child(j).text(2) for j in range(row.childCount())], ["LOCK_B"]
        )
        a01 = self._row("A01")
        self.assertEqual(
            [a01.child(j).text(2) for j in range(a01.childCount())], ["KEYS", "LOCK"]
        )

    def test_adding_the_shot_makes_it_a_step(self):
        self._load()
        self._menu(self._row("A01_2"), "Add 'A01_2'")
        self.assertEqual(self.store.shot_by_name("A01_2").metadata["step"], "A01_2")
        self.assertTrue(self.ctrl._step_is_built("A01_2"))
        self.assertEqual(self.ctrl._orphan_shots(), [])

    def test_a_section_locks(self):
        self._load()
        self._menu(self._row("A01"), "Lock Section 'A'")
        self.assertTrue(self.store.shot_by_name("A01_1").locked)
        self.assertTrue(self.store.shot_by_name("A02").locked)

    def test_steps_with_no_shot_hide(self):
        self._load(hide_missing=True)
        self.assertEqual(self._names(), ["A01", "A01_2", "A02"])


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
