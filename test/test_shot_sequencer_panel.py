# !/usr/bin/python
# coding=utf-8
"""Shot Sequencer panel load test — Blender port of mayatk's sequencer panel.

Needs **Qt, not bpy**: loads ``shot_sequencer.ui`` (which promotes the shared uitk
``SequencerWidget``) and wires ``ShotSequencerSlots`` through a real (offscreen)
Qt / Switchboard / BlenderUiHandler stack.  The controller's build path is
headless-safe — ``BlenderShotStore.active()`` falls back to an in-memory store,
``bpy.app.handlers`` registration is skipped when ``bpy`` is absent, and
``_sync_to_widget`` degrades to the shotless/no-scene path.  Proves the ``.ui``
compiles, the controller builds (callbacks, store binding, widget signal wiring),
and every widget signal maps to a real controller slot.  Run under the ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_shot_sequencer_panel.py

The engine behaviour (move / ripple / scale / resize / segments) is covered live by
``test_shot_sequencer.py`` under the Blender harness.
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
    import importlib.util

    _conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
    if os.path.isfile(_conftest):
        _spec = importlib.util.spec_from_file_location("_uitk_conftest", _conftest)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
    else:
        QtWidgets = None


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestShotSequencerPanelLoads(unittest.TestCase):
    """The Shot Sequencer panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        from blendertk import BlenderShotStore

        cls._prefs_tmp = tempfile.mkdtemp(prefix="btk_seq_panel_")
        BlenderShotStore._prefs_dir_override = cls._prefs_tmp
        BlenderShotStore.clear_active()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("shot_sequencer")
        for _ in range(5):
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        from blendertk import BlenderShotStore

        # Tear the controller's callbacks down explicitly (mirrors panel close).
        try:
            cls.ui.slots.controller.remove_callbacks()
        except Exception:
            pass
        BlenderShotStore.clear_active()
        BlenderShotStore._prefs_dir_override = None

    def assertPromoted(self, name, *probes):
        """Fail (never skip) when a ``.ui`` widget did not promote to its uitk class.

        The Qt binding being absent is an *environment* gate and is handled once,
        at class level. Everything past that gate is a real defect: a widget that
        stayed a bare ``QWidget`` means the ``<customwidgets>`` header, the uitk
        class, or the loader broke -- exactly the regression these checks exist
        to catch, so it must assert rather than skip green.

        Parameters:
            name: Attribute name of the widget on ``self.ui``.
            probes: Attribute names the promoted class must expose.

        Returns:
            The promoted widget.
        """
        w = getattr(self.ui, name, None)
        self.assertIsNotNone(w, f"{name} is missing from the loaded shot_sequencer UI")
        for probe in probes:
            self.assertTrue(
                hasattr(w, probe),
                f"{name} did not promote to its uitk class "
                f"(no {probe!r}; got {type(w).__name__})",
            )
        return w

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "shot_sequencer UI failed to load")

    def test_resolves_to_slots_class(self):
        self.assertEqual(type(self.ui.slots).__name__, "ShotSequencerSlots")

    def test_controller_built(self):
        ctrl = getattr(self.ui.slots, "controller", None)
        self.assertIsNotNone(ctrl)
        self.assertEqual(type(ctrl).__name__, "ShotSequencerController")

    def test_controller_mro_has_all_mixins(self):
        ctrl = self.ui.slots.controller
        names = {c.__name__ for c in type(ctrl).__mro__}
        for mixin in (
            "GapManagerMixin",
            "ClipMotionMixin",
            "ShotNavMixin",
            "MarkerManagerMixin",
        ):
            self.assertIn(mixin, names, f"{mixin} missing from controller MRO")

    def test_static_widgets_exist(self):
        expected = ["cmb_mode", "cmb_shot", "sequencer_widget"]
        missing = [w for w in expected if not hasattr(self.ui, w)]
        self.assertEqual(missing, [])

    def test_sequencer_widget_is_promoted(self):
        self.assertPromoted("sequencer_widget", "add_track")

    def test_signals_wired_to_real_slots(self):
        """Every widget signal in the wiring table maps to an existing controller slot."""
        from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerSlots,
        )

        ctrl = self.ui.slots.controller
        missing = [
            slot for _, slot in ShotSequencerSlots._WIRING if not hasattr(ctrl, slot)
        ]
        self.assertEqual(
            missing, [], f"wiring references missing controller slots: {missing}"
        )

    def test_widget_signal_connections_recorded(self):
        """The controller connected the widget signals (recorded on the widget)."""
        w = self.assertPromoted("sequencer_widget", "clip_resized")
        conns = getattr(w, "_slots_connections", [])
        self.assertGreater(len(conns), 0, "no widget signals were connected")

    def test_shot_nav_options_built(self):
        """cmb_shot carries the full mayatk-mirror option set (prev/next/add/view/refresh).

        Pins the view-mode port through the REAL ActionOption path — the wiring
        sits in a try/except, so a bad option construction would otherwise be
        swallowed into a debug log and the dead-state regression would return.
        """
        cmb = self.assertPromoted("cmb_shot", "option_box")
        opts = getattr(cmb, "_shot_nav_options", None)
        self.assertIsNotNone(opts, "shot-nav options were not built (swallowed?)")
        for key in ("prev", "next", "add", "view", "holds", "refresh"):
            self.assertIsNotNone(opts.get(key), f"missing shot-nav option: {key}")
        # holds toggle state is adopted (mirror of mayatk's Show Internal Holds)
        self.assertIsInstance(self.ui.slots.controller._show_internal_holds, bool)
        # the view cycle is adopted into the controller's display mode
        self.assertIn(
            self.ui.slots.controller._shot_display_mode, ("current", "adjacent", "all")
        )

    def test_transport_controls_attached_once(self):
        """Footer carries ONE TransportControls row wired to the Blender play controller."""
        from uitk.widgets.sequencer import TransportControls
        from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            _BlenderPlayController,
        )

        footer = self.assertPromoted("footer", "add_action_button", "container_layout")
        transport = getattr(footer, "_shot_transport_controls", None)
        self.assertIsInstance(transport, TransportControls)
        self.assertIs(self.ui.slots.controller._transport_controls, transport)
        self.assertIsInstance(transport._play_controller, _BlenderPlayController)
        # headless (no bpy): the adapter reports "not playing" rather than raising
        self.assertFalse(transport._play_controller.is_playing())
        rows = [w for w in footer.findChildren(TransportControls)]
        self.assertEqual(len(rows), 1, "transport row duplicated on the footer")

    def test_cmb_shot_context_menu_installed(self):
        """Right-click on cmb_shot opens the New/Generate/Edit/Delete menu (mayatk mirror)."""
        from qtpy import QtCore

        cmb = self.assertPromoted("cmb_shot", "option_box")
        self.assertEqual(cmb.contextMenuPolicy(), QtCore.Qt.CustomContextMenu)
        for name in (
            "_cmb_context_menu",
            "_detect_next_shot",
            "_delete_shot",
            "_edit_shot_in_settings",
        ):
            self.assertTrue(callable(getattr(self.ui.slots, name, None)), name)

    def test_controller_parity_surface(self):
        """Every mayatk controller slot the widget/menu can reach exists here too."""
        ctl = self.ui.slots.controller
        for name in (
            "_build_audio_tracks",
            "_clips_to_sequences",
            "_move_clips_to_shot",
            "_set_show_internal_holds",
            "_ensure_sound_on_timeline",
            "_setup_transport_controls",
            "_playback_range",
            "on_key_selection_changed",
            "_delete_selected_clip_keys",
            "_reveal_in_outliner",
            "_native_undo",
        ):
            self.assertTrue(callable(getattr(ctl, name, None)), name)
        # audio-track label prefix round-trips through _resolve_full_name
        self.assertEqual(ctl._resolve_full_name("♫ Strip"), "Strip")

    def test_reinit_stashes_and_tears_down_prior_controller(self):
        """Re-initing the slots over the same UI tears the old controller down
        (bpy handlers + store/invalidation listeners) instead of leaking it."""
        from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerSlots,
        )

        first = self.ui._sequencer_controller
        self.assertIs(first, self.ui.slots.controller)
        second_slots = ShotSequencerSlots(self.sb)
        try:
            self.assertIsNot(
                self.ui._sequencer_controller,
                first,
                "re-init did not stash the new controller",
            )
            # the old controller's invalidation listener must be gone
            from blendertk import BlenderShotStore

            self.assertNotIn(
                first._on_store_invalidated, BlenderShotStore._invalidation_listeners
            )
        finally:
            second_slots.controller.remove_callbacks()


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
