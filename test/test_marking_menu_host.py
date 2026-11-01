# !/usr/bin/python
# coding=utf-8
"""Tests for ``blendertk.BlenderMarkingMenuHost`` — what a uitk marking menu needs from Blender.

Pinned here: it is a ``uitk.MarkingMenuHost``; ``defer`` builds on a one-shot, persistent
``bpy.app.timers`` callback and shows the two errors that carry their fix (a missing install, Qt
unable to open a window) in a popup instead of the hidden system console (moved from tentacle's
``test_tcl_launcher`` with ``Tcl._launch_blender``); ``bind_activation_key`` installs the keymap
bridge for a new menu and rebinds it for the live one; ``mouse_buttons`` prefers the physical
buttons; ``key_event`` completes the gesture on the activation key's release.

Qt-side, bpy-free (``bpy`` is a stand-in module), so this runs under the workspace ``.venv``;
under ``blender --background`` (no Qt) it reports the ``(skipped)`` sentinel so
``run_tests.py`` re-runs it there.
"""

import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")


def _fake_bpy():
    """A stand-in ``bpy`` that survives being imported (a real module object, not a MagicMock:
    an import hook reading ``__name__`` would raise on a mock)."""
    module = types.ModuleType("bpy")
    module.app = mock.MagicMock()
    module.context = mock.MagicMock()
    return module


class _HostCase(unittest.TestCase):
    def setUp(self):
        try:
            from qtpy import QtWidgets
        except Exception as error:  # noqa: BLE001 - no binding = not testable here
            self.skipTest(f"needs a Qt binding: {error}")
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["t"])
        from blendertk.ui_utils.marking_menu_host._marking_menu_host import (
            BlenderMarkingMenuHost,
        )

        self.host = BlenderMarkingMenuHost()
        self.bpy = _fake_bpy()
        patcher = mock.patch.dict(sys.modules, {"bpy": self.bpy})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _deferred(self, fn):
        """Defer *fn* and hand back the timer callback Blender would call."""
        self.assertIsNone(self.host.defer(fn))
        return self.bpy.app.timers.register.call_args.args[0]


class TestTheProtocol(_HostCase):
    def test_it_is_a_marking_menu_host(self):
        from uitk import MarkingMenuHost, PureMarkingMenuHost

        self.assertIsInstance(self.host, MarkingMenuHost)
        self.assertIsInstance(self.host, PureMarkingMenuHost)

    def test_the_package_root_publishes_it(self):
        import blendertk as btk

        self.assertIs(btk.BlenderMarkingMenuHost, type(self.host))

    def test_its_ui_handler_is_blenders(self):
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        self.assertIs(self.host.ui_handler, BlenderUiHandler)

    def test_it_parents_to_the_hidden_blender_widget(self):
        """The widget ``start`` stands up; None before it -- never a raise."""
        had = getattr(self.app, "blender_widget", None)
        try:
            self.app.blender_widget = None
            self.assertIsNone(self.host.main_window())
            self.app.blender_widget = marker = object()
            self.assertIs(self.host.main_window(), marker)
        finally:
            self.app.blender_widget = had


class TestDefer(_HostCase):
    def test_the_build_waits_for_the_timer(self):
        """Blender startup scripts run before the UI settles — the build runs from the
        timer callback, never during the launch."""
        built = []
        callback = self._deferred(lambda: built.append(True))
        self.assertEqual(built, [])
        callback()
        self.assertEqual(built, [True])

    def test_the_timer_is_one_shot(self):
        """bpy.app.timers unregisters a callback only when it returns None; returning the
        menu would re-run the whole build every half second."""
        callback = self._deferred(lambda: "a menu")
        self.assertIsNone(callback())

    def test_the_timer_survives_the_file_the_user_opened(self):
        """A default timer is dropped when Blender loads a .blend -- and Blender enables
        add-ons (and runs startup scripts) BEFORE it opens the file a user double-clicked
        (measured on 5.1.2: the default timer never fired, a persistent one did)."""
        self._deferred(lambda: None)
        kwargs = self.bpy.app.timers.register.call_args.kwargs
        self.assertIs(kwargs.get("persistent"), True)
        self.assertEqual(kwargs.get("first_interval"), self.host.START_DELAY)

    def _popup_lines(self):
        drawn = []

        def _popup(draw, **_kw):
            """Run the draw callback the way Blender does, capturing its labels."""
            menu = mock.MagicMock()
            menu.layout.label.side_effect = lambda text: drawn.append(text)
            draw(menu, None)

        self.bpy.context.window_manager.popup_menu.side_effect = _popup
        return drawn

    def test_an_import_error_in_the_build_reaches_the_user_not_the_hidden_console(self):
        """An ImportError raised by the build (a dependency the build imports) carries its
        install hint, which is the whole point: raised from a timer callback it would
        print to the hidden SYSTEM console. (A missing blendertk raises before any
        adapter exists -- the launcher's caller sees that one.)"""
        drawn = self._popup_lines()

        def build():
            raise ImportError(
                "tentacle: the blender engine (blendertk) is not installed. "
                'pip install "tentacletk[blender]".'
            )

        self.assertIsNone(
            self._deferred(build)()
        )  # must NOT propagate out of the timer
        # The pip line is the payload; splitting the message must not corrupt it.
        self.assertIn('pip install "tentacletk[blender]".', drawn)
        self.assertEqual(
            " ".join(drawn),
            "tentacle: the blender engine (blendertk) is not installed. "
            'pip install "tentacletk[blender]".',
        )

    def test_a_qt_platform_error_reaches_the_user_one_line_per_label(self):
        from blendertk.ui_utils.qt_runtime import QtPlatformUnavailable

        drawn = self._popup_lines()

        def build():
            raise QtPlatformUnavailable(
                "tentacle: Qt cannot open a window here, so the marking menu is off.\n"
                "On Linux, Qt's X11 plug-in needs: libxcb-cursor0."
            )

        self.assertIsNone(self._deferred(build)())
        self.assertTrue(any("marking menu is off" in s for s in drawn), drawn)
        self.assertTrue(any("libxcb-cursor0" in s for s in drawn), drawn)
        # Blender draws a label unwrapped, a newline included.
        self.assertEqual([s for s in drawn if "\n" in s], [])

    def test_a_popup_that_cannot_render_still_does_not_escape_the_timer(self):
        """A timer callback has no guaranteed UI context, so the popup itself can raise;
        letting the original error out would report the add-on as broken."""
        self.bpy.context.window_manager.popup_menu.side_effect = RuntimeError("no ctx")

        def build():
            raise ImportError("engine missing")

        with mock.patch("builtins.print"):
            self.assertIsNone(self._deferred(build)())

    def test_a_non_import_failure_keeps_its_traceback(self):
        """Scoped to the errors that carry a fix: a real bug is not dressed up as one."""

        def build():
            raise ValueError("a real bug")

        callback = self._deferred(build)
        with self.assertRaises(ValueError):
            callback()


class TestLiveMenu(_HostCase):
    def test_the_bridges_menu_is_the_live_one(self):
        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        before = KeymapBridge.tcl
        self.addCleanup(setattr, KeymapBridge, "tcl", before)
        KeymapBridge.tcl = None
        self.assertIsNone(self.host.live_menu(), "no bridge, no live menu")
        KeymapBridge.tcl = menu = object()
        self.assertIs(self.host.live_menu(), menu)


class TestActivationBinding(_HostCase):
    def setUp(self):
        super().setUp()
        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        self.bridge = KeymapBridge
        before = self.bridge.tcl
        self.addCleanup(setattr, self.bridge, "tcl", before)

    def test_a_new_menu_gets_the_keymap_and_the_poller(self):
        menu = object()
        self.bridge.tcl = None
        with (
            mock.patch.object(self.bridge, "install_keymap") as keymap,
            mock.patch.object(self.bridge, "install_poller") as poller,
            mock.patch.object(self.bridge, "rebind") as rebind,
        ):
            self.host.bind_activation_key(menu, "Key_F12")
        keymap.assert_called_once_with(menu, "F12")
        poller.assert_called_once_with(menu, "Key_F12")
        rebind.assert_not_called()

    def test_the_live_menu_is_rebound_in_place(self):
        menu = object()
        self.bridge.tcl = menu
        with (
            mock.patch.object(self.bridge, "install_keymap") as keymap,
            mock.patch.object(self.bridge, "rebind") as rebind,
        ):
            self.host.bind_activation_key(menu, "Key_F11")
        rebind.assert_called_once_with(menu, "Key_F11")
        keymap.assert_not_called()

    def test_a_failing_bridge_never_raises(self):
        self.bridge.tcl = None
        with (
            mock.patch.object(
                self.bridge, "install_keymap", side_effect=RuntimeError("no keyconfig")
            ),
            mock.patch("builtins.print"),
        ):
            self.host.bind_activation_key(object(), "Key_F12")  # must not raise


class TestInput(_HostCase):
    def test_a_physically_held_button_wins_over_qts_view(self):
        from qtpy import QtCore

        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        with mock.patch.object(
            KeymapBridge,
            "physical_mouse_buttons",
            classmethod(lambda cls: QtCore.Qt.LeftButton),
        ):
            self.assertEqual(self.host.mouse_buttons(), QtCore.Qt.LeftButton)
        with mock.patch.object(
            KeymapBridge,
            "physical_mouse_buttons",
            classmethod(lambda cls: QtCore.Qt.NoButton),
        ):
            from qtpy import QtWidgets

            self.assertEqual(
                self.host.mouse_buttons(), QtWidgets.QApplication.mouseButtons()
            )

    def test_the_activation_keys_release_completes_the_gesture(self):
        from qtpy import QtCore, QtGui

        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        menu = types.SimpleNamespace(_activation_key=int(QtCore.Qt.Key_F12))
        release = QtGui.QKeyEvent(
            QtCore.QEvent.KeyRelease, QtCore.Qt.Key_F12, QtCore.Qt.NoModifier
        )
        other = QtGui.QKeyEvent(
            QtCore.QEvent.KeyRelease, QtCore.Qt.Key_A, QtCore.Qt.NoModifier
        )
        with mock.patch.object(KeymapBridge, "drive_release") as release_gesture:
            self.host.key_event(menu, other)
            release_gesture.assert_not_called()
            self.host.key_event(menu, release)
            release_gesture.assert_called_once_with()

    def test_ctrl_z_is_blenders_undo(self):
        from qtpy import QtCore, QtGui, QtWidgets

        menu = types.SimpleNamespace(_activation_key=int(QtCore.Qt.Key_F12))
        press = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier
        )
        self.bpy.ops = mock.MagicMock()
        with mock.patch.object(
            QtWidgets.QApplication,
            "keyboardModifiers",
            staticmethod(lambda: QtCore.Qt.ControlModifier),
        ):
            self.host.key_event(menu, press)
        self.bpy.ops.ed.undo.assert_called_once_with()


if __name__ == "__main__":
    try:
        import qtpy  # noqa: F401 — the binding every assertion here needs
    except Exception:
        # Under the Blender harness (no Qt): the SKIP sentinel makes run_tests.py
        # re-run this suite under the workspace .venv.
        print("===RESULT: PASS=== (skipped)")
        raise SystemExit(0)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
