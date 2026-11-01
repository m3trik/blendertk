# !/usr/bin/python
# coding=utf-8
"""Tests for ``blendertk.BlenderWindow``'s Qt-facing helpers: the OS-foreground hand-back and
window ownership (moved from tentacle's ``test_blender_focus_restore`` /
``test_blender_window_parenting`` with ``tcl_blender._NativeWindow``).

**Foreground hand-back.** Blender's activation key is delivered by a Blender keymap operator,
which only fires while Blender's native GHOST window has OS keyboard focus (the Qt shortcut
fallback is inert — its owner widget is hidden). Any Qt popup that takes activation and then
hides without handing the OS foreground back to GHOST leaves the key dead until the user clicks
the viewport. The live repro: open a marking-menu option_box, release the key while its dropdown
is open, dismiss the dropdown — ``uitk.Menu.hideEvent`` restores focus Qt-side only, so the
foreground stays on a hidden Qt window and neither Blender's keymap nor Qt ever sees the key again.
Pinned here:

* ``_qt_widget_for_hwnd`` — maps an OS foreground handle back to one of OUR Qt top-levels
  without forcing native-window creation on unrelated widgets.
* ``restore_foreground`` — hands focus to GHOST only when the foreground is ours (never steals
  from another app, or from Blender's own secondary GHOST windows / native dialogs — same PID,
  not Qt), and no-ops when GHOST already has it.
* ``restore_foreground_if_stranded`` — the poller watchdog: detects "foreground stuck on one of
  our HIDDEN Qt windows" and heals it; leaves every other state alone.

All ctypes goes through the ``_foreground_hwnd``/``_set_foreground`` seams (patched here), so
those tests are deterministic and offscreen-QPA safe — no real HWNDs or OS focus involved.

**Ownership.** Every Qt window over Blender (the marking-menu overlay + each standalone tool
panel) is an unrelated top-level OS window unless it is tied to Blender's native GHOST window.
``QWindow.setTransientParent`` to a *foreign* parent never sets the Windows owner
(``GWLP_HWNDPARENT``), so ``set_owner`` sets it directly. Those tests need real native HWNDs, so
they run only on Windows with a real Qt platform and skip offscreen; the negative test pins the
root cause so a "just use setTransientParent" refactor cannot silently regress it.

Qt-side, bpy-free: runs under the workspace ``.venv``; under ``blender --background`` (no Qt)
it reports the ``(skipped)`` sentinel so ``run_tests.py`` re-runs it there.
"""

from __future__ import annotations

import ctypes
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

from blendertk.ui_utils.blender_window import BlenderWindow  # noqa: E402  (Qt-free import)


def _can_run() -> bool:
    """Windows (the primitives are win32 no-ops elsewhere) + a Qt that can host real widgets."""
    if sys.platform != "win32":
        return False
    try:
        from qtpy import QtWidgets  # noqa: F401
    except Exception:
        return False
    return True


def _real_windows_platform() -> bool:
    """True only on a real Windows Qt platform — not the offscreen/minimal QPA.

    The ownership these tests assert lives in the OS window manager, so they
    need genuine native HWNDs. Under ``QT_QPA_PLATFORM=offscreen`` (CI and
    headless local runs) ``winId()`` returns a synthetic handle that Win32
    ``SetWindowLongPtr`` rejects, so the tests must *skip* there rather than
    run-and-fail.
    """
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal"):
        return False
    try:
        from qtpy import QtGui

        app = QtGui.QGuiApplication.instance()
        if app is not None and app.platformName().lower() in ("offscreen", "minimal"):
            return False
    except Exception:
        return False
    return True


_SKIP_REASON = "Foreground hand-back tests need win32 + a Qt binding."
_OWNERSHIP_SKIP = (
    "Window-ownership tests need Windows + a real Qt platform "
    "(skipped in CI / off-Windows / offscreen)."
)


GHOST_HWND = (
    0xB1E42  # fake GHOST handle — never dereferenced (the ctypes seams are patched)
)


def _foreign_hwnd():
    """An HWND guaranteed not to belong to any of our Qt top-levels."""
    from qtpy import QtWidgets

    ids = {
        int(w.internalWinId() or 0) for w in QtWidgets.QApplication.topLevelWidgets()
    }
    return max(ids | {0x1000}) + 0x5150


@unittest.skipUnless(_can_run(), _SKIP_REASON)
class _FocusRestoreBase(unittest.TestCase):
    """Shared fixture: QApplication, a fake GHOST handle, patched ctypes seams."""

    @classmethod
    def setUpClass(cls):
        from qtpy import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])

    def setUp(self):
        from qtpy import QtWidgets

        self.nw = BlenderWindow
        self.app._blender_native_hwnd = GHOST_HWND
        self.addCleanup(lambda: delattr(self.app, "_blender_native_hwnd"))
        # Deterministic: no window is Qt-active unless a test says so (offscreen-QPA rule).
        self._aw = patch.object(
            QtWidgets.QApplication, "activeWindow", return_value=None
        )
        self._aw.start()
        self.addCleanup(self._aw.stop)
        self.set_fg = MagicMock()
        patcher = patch.object(self.nw, "_set_foreground", self.set_fg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sentinel_tcl = (
            self._widget()
        )  # stands in for the live marking-menu overlay

    def _widget(self, native=True, visible=False):
        from qtpy import QtCore, QtWidgets

        w = QtWidgets.QWidget()
        w.setWindowFlag(QtCore.Qt.Window)
        if native:
            w.winId()  # force native-window creation so internalWinId is set
        if visible:
            w.show()
        self.addCleanup(w.deleteLater)
        return w

    def _with_fg(self, hwnd):
        patcher = patch.object(
            self.nw, "_foreground_hwnd", MagicMock(return_value=int(hwnd))
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TestQtWidgetForHwnd(_FocusRestoreBase):
    """``_qt_widget_for_hwnd`` — foreground handle → our Qt top-level, without side effects."""

    def test_matches_widget_with_created_native_window(self):
        w = self._widget()
        self.assertIs(self.nw._qt_widget_for_hwnd(int(w.winId())), w)

    def test_foreign_hwnd_returns_none(self):
        self.assertIsNone(self.nw._qt_widget_for_hwnd(_foreign_hwnd()))

    def test_zero_returns_none(self):
        self.assertIsNone(self.nw._qt_widget_for_hwnd(0))

    def test_lookup_never_forces_native_window_creation(self):
        """The scan must skip natively-uncreated widgets, not create windows for them.

        ``QWidget.winId()`` forces native creation; ``internalWinId()`` doesn't. A poller
        running this 50×/s would otherwise materialize native windows for every lazy
        top-level in the process.
        """
        virgin = self._widget(native=False)
        self.nw._qt_widget_for_hwnd(_foreign_hwnd())
        self.assertEqual(int(virgin.internalWinId() or 0), 0)


class TestRestoreForeground(_FocusRestoreBase):
    """``restore_foreground`` — hand focus to GHOST, but never steal it from anyone else."""

    def test_noop_when_ghost_already_foreground(self):
        self._with_fg(GHOST_HWND)
        self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_not_called()

    def test_restores_when_foreground_is_our_hidden_window(self):
        """The stranded state: a dead Qt popup still holds the OS foreground."""
        dead = self._widget()
        self._with_fg(int(dead.winId()))
        self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_called_once_with(GHOST_HWND)

    def test_never_steals_from_foreign_window(self):
        """Foreground belongs to another app (or a Blender-native dialog) → leave it alone.

        This is the pin for the alt-tab case: releasing the activation key after switching
        to another application must not yank focus back to Blender.
        """
        self._with_fg(_foreign_hwnd())
        self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_not_called()

    def test_restores_when_no_foreground_at_all(self):
        """A destroyed foreground window leaves fg=0 — restoring steals from nobody."""
        self._with_fg(0)
        self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_called_once_with(GHOST_HWND)

    def test_bails_when_another_visible_tentacle_window_is_active(self):
        """Maya-parity guard (pre-existing): a pinned tool window the user works in keeps focus."""
        from qtpy import QtWidgets

        pinned = self._widget(visible=True)
        dead = self._widget()
        self._with_fg(int(dead.winId()))
        with patch.object(QtWidgets.QApplication, "activeWindow", return_value=pinned):
            self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_not_called()

    def test_idempotent_second_call_after_restore(self):
        """Once foreground is back on GHOST, further calls are clean no-ops."""
        dead = self._widget()
        self._with_fg(int(dead.winId()))
        self.nw.restore_foreground(self.sentinel_tcl)
        self._with_fg(GHOST_HWND)  # the restore landed
        self.nw.restore_foreground(self.sentinel_tcl)
        self.set_fg.assert_called_once_with(GHOST_HWND)


class TestRestoreForegroundIfStranded(_FocusRestoreBase):
    """``restore_foreground_if_stranded`` — the poller watchdog's decision table."""

    def test_hidden_our_window_foreground_heals(self):
        dead = self._widget()
        self._with_fg(int(dead.winId()))
        self.assertTrue(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_called_once_with(GHOST_HWND)

    def test_ghost_foreground_is_not_stranded(self):
        self._with_fg(GHOST_HWND)
        self.assertFalse(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_not_called()

    def test_visible_our_window_is_not_stranded(self):
        """A visible tentacle window holding focus is in use — never yank it."""
        live = self._widget(visible=True)
        self._with_fg(int(live.winId()))
        self.assertFalse(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_not_called()

    def test_foreign_foreground_is_not_stranded(self):
        self._with_fg(_foreign_hwnd())
        self.assertFalse(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_not_called()

    def test_no_foreground_is_not_stranded(self):
        self._with_fg(0)
        self.assertFalse(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_not_called()

    def test_without_ghost_handle_does_nothing(self):
        dead = self._widget()
        self._with_fg(int(dead.winId()))
        delattr(self.app, "_blender_native_hwnd")
        self.addCleanup(lambda: setattr(self.app, "_blender_native_hwnd", GHOST_HWND))
        self.assertFalse(self.nw.restore_foreground_if_stranded(self.sentinel_tcl))
        self.set_fg.assert_not_called()


_GWLP_HWNDPARENT = -8


def _os_owner(widget):
    """The window's OS owner handle (``GWLP_HWNDPARENT``), or ``None`` when unowned."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return user32.GetWindowLongPtrW(
        ctypes.c_void_p(int(widget.winId())), _GWLP_HWNDPARENT
    )


@unittest.skipUnless(_can_run() and _real_windows_platform(), _OWNERSHIP_SKIP)
class TestBlenderWindowOwnership(unittest.TestCase):
    """Verify the owner relationship that keeps tentacle windows above Blender."""

    @classmethod
    def setUpClass(cls):
        from qtpy import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])

    def setUp(self):
        from qtpy import QtCore, QtWidgets

        # Stand-in for Blender's GHOST window: a real top-level whose HWND we own things to.
        self.owner = QtWidgets.QWidget()
        self.owner.setWindowFlag(QtCore.Qt.Window)
        self.owner_hwnd = int(self.owner.winId())
        self.addCleanup(self.owner.deleteLater)

    def _make_window(self):
        from qtpy import QtCore, QtWidgets

        w = QtWidgets.QWidget()
        w.setWindowFlag(QtCore.Qt.Window)
        w.winId()  # force native-window creation
        self.addCleanup(w.deleteLater)
        return w

    def test_set_native_owner_sets_os_owner(self):
        """``BlenderWindow.set_owner`` makes the window owned by the given HWND — the actual fix."""
        w = self._make_window()
        result = BlenderWindow.set_owner(w, self.owner_hwnd)
        self.assertEqual(result, self.owner_hwnd)
        self.assertEqual(_os_owner(w), self.owner_hwnd)

    def test_set_native_owner_idempotent(self):
        """Re-asserting the same owner (every show / native-window recreate) stays a clean no-op."""
        w = self._make_window()
        BlenderWindow.set_owner(w, self.owner_hwnd)
        BlenderWindow.set_owner(w, self.owner_hwnd)
        self.assertEqual(_os_owner(w), self.owner_hwnd)

    def test_set_native_owner_noop_without_owner(self):
        """No owner handle yet (GHOST not enumerable) → leave the window untouched, never raise."""
        w = self._make_window()
        self.assertIsNone(BlenderWindow.set_owner(w, 0))
        self.assertFalse(_os_owner(w))  # still unowned

    def test_foreign_transient_parent_does_not_set_owner(self):
        """Root-cause pin: ``setTransientParent`` to a FOREIGN window does NOT set the OS owner.

        This is why the explicit ``BlenderWindow.set_owner`` is required — if a future change drops it
        and relies on transient-parenting alone, this test fails and explains why the windows
        fall behind Blender.
        """
        from qtpy import QtGui

        foreign = QtGui.QWindow.fromWinId(self.owner_hwnd)
        w = self._make_window()
        w.windowHandle().setTransientParent(foreign)
        self.app.processEvents()
        self.assertNotEqual(
            _os_owner(w),
            self.owner_hwnd,
            "setTransientParent unexpectedly set the OS owner — the explicit owner-set "
            "may now be redundant; re-check before removing BlenderWindow.set_owner.",
        )


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
