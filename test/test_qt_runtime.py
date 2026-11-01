# !/usr/bin/python
# coding=utf-8
"""Tests for ``blendertk.QtRuntime`` — Qt inside Blender: the Linux platform preflight and the
event pump (moved from tentacle's ``test_tcl_launcher.TestLinuxQtHost`` with
``tcl_blender._QtHost``).

Qt-side, bpy-free (``bpy`` is a stand-in module), so this runs under the workspace ``.venv``; under ``blender --background`` (no Qt) it reports the ``(skipped)``
sentinel so ``run_tests.py`` re-runs it there.
"""

import contextlib
import os
import subprocess
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
    """A stand-in ``bpy`` that survives being imported.

    A bare ``MagicMock`` is NOT usable here: inside Maya, PySide6's ``shibokensupport`` installs an
    import hook that reads ``__name__`` off every imported module, and MagicMock raises
    AttributeError for it — so the mock blew up at ``import bpy`` in the canonical in-Maya run
    while passing everywhere else. A real module object has the dunders any import hook expects.
    """
    module = types.ModuleType("bpy")
    module.app = mock.MagicMock()
    return module


class TestLinuxQtRuntime(unittest.TestCase):
    """The Linux-only parts of ``QtRuntime``: the platform preflight and the pump.

    Preflight: a Qt platform plug-in that fails to load aborts the process from C++ -- in-process
    that is Blender, with the user's unsaved work -- so ``QtRuntime`` starts a QApplication in a
    CHILD first and raises instead. The error carries the fix (the system libraries to
    install), so, like the missing engine, it must reach a popup rather than the hidden system
    console. Measured before the fix: the launcher's timer caught ImportError alone, so the
    probe's RuntimeError escaped it and the user saw nothing.

    Driven through the REAL ``QtRuntime``: only the probe's ``subprocess.run``, the platform
    and ``bpy`` are faked -- no Blender, no child process. (The launcher half -- the error
    reaching a popup -- is tentacle's ``test_tcl_launcher``.)
    """

    ABORTED = subprocess.CompletedProcess(
        args=[],
        returncode=-6,
        stdout="",
        stderr=(
            'qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it '
            "was found.\nThis application failed to start because no Qt platform plugin "
            "could be initialized."
        ),
    )

    def setUp(self):
        try:
            from qtpy import QtWidgets  # noqa: F401
        except Exception as error:  # noqa: BLE001 - no binding = not testable here
            self.skipTest(f"needs a Qt binding: {error}")
        from blendertk.ui_utils import qt_runtime

        self.qt_runtime = qt_runtime

    @contextlib.contextmanager
    def _linux_without_a_qapp(self, probe):
        """Linux, no QApplication yet, uitk's platform policy stubbed, the probe child faked:
        *probe* is its ``CompletedProcess``, or the exception launching it raises."""
        import uitk
        from qtpy import QtWidgets

        run = (
            mock.patch("subprocess.run", side_effect=probe)
            if isinstance(probe, BaseException)
            else mock.patch("subprocess.run", return_value=probe)
        )
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(
                QtWidgets.QApplication, "instance", new=staticmethod(lambda: None)
            ),
            mock.patch.object(
                uitk.Bootstrap,
                "configure_platform",
                new=staticmethod(lambda *a, **k: False),
            ),
            run,
        ):
            yield

    def test_a_failing_probe_raises_the_dedicated_error(self):
        QtPlatformUnavailable = self.qt_runtime.QtPlatformUnavailable

        with self._linux_without_a_qapp(self.ABORTED):
            with self.assertRaises(QtPlatformUnavailable) as ctx:
                self.qt_runtime.QtRuntime.ensure_qapp()
        # Still a RuntimeError: a caller that caught the old error keeps catching it.
        self.assertIsInstance(ctx.exception, RuntimeError)
        message = str(ctx.exception)
        self.assertIn("marking menu is off", message)
        self.assertIn('Could not load the Qt platform plugin "xcb"', message)
        self.assertIn("libxcb-cursor0", message)

    def test_a_probe_that_cannot_run_raises_it_too(self):
        QtPlatformUnavailable = self.qt_runtime.QtPlatformUnavailable

        with self._linux_without_a_qapp(OSError("no interpreter")):
            with self.assertRaises(QtPlatformUnavailable) as ctx:
                self.qt_runtime.QtRuntime.ensure_qapp()
        self.assertIn("marking menu is off", str(ctx.exception))

    def test_the_pump_runs_qts_own_loop_on_linux_only(self):
        """X11/Wayland: Qt owns its display connection, so the pump runs Qt's loop there.
        Elsewhere the queue is shared with Blender -- the Windows thread queue, macOS's
        NSApplication -- and draining it re-enters Blender (see ``start_pump``), so only
        posted events are flushed. Measured before the fix: the gate read ``!= "win32"``,
        so macOS drained the shared queue too."""
        from qtpy import QtCore

        from blendertk.ui_utils.blender_window import BlenderWindow

        host, calls = self.qt_runtime.QtRuntime, []
        bpy = _fake_bpy()
        with (
            mock.patch.dict(sys.modules, {"bpy": bpy}),
            mock.patch.object(host, "_pump_registered", False),
            mock.patch.object(
                BlenderWindow,
                "native_modal_loop_active",
                new=staticmethod(lambda **_kw: False),
            ),
            mock.patch.object(
                QtCore.QCoreApplication,
                "processEvents",
                new=staticmethod(lambda *_a: calls.append("processEvents")),
            ),
            mock.patch.object(
                QtCore.QCoreApplication,
                "sendPostedEvents",
                new=staticmethod(lambda *_a: None),
            ),
        ):
            host.start_pump(types.SimpleNamespace())
            pump = bpy.app.timers.register.call_args.args[0]
            ran = {}
            for platform in ("linux", "darwin", "win32"):
                calls.clear()
                with mock.patch.object(sys, "platform", platform):
                    pump()
                ran[platform] = bool(calls)
        self.assertEqual(ran, {"linux": True, "darwin": False, "win32": False})


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
