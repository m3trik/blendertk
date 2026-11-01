# !/usr/bin/python
# coding=utf-8
"""Tests for ``blendertk.ClickDebugger`` — the opt-in click tracer stays off, and never sinks
into the user's home (moved from tentacle's ``test_blender_slots.ClickDebuggerSink`` with
``tcl_blender._ClickDebugger``).

Qt-free: the tracer imports without a binding, and these tests never enable it, so the suite
runs both under ``blender --background`` and under the workspace ``.venv``.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


class ClickDebuggerSink(unittest.TestCase):
    """The opt-in click tracer must stay off, and must never sink into the user's HOME.

    ``ClickDebugger`` is a support tool: a user is told to run ``ClickDebugger.enable()``,
    reproduce a dead click, then share the log. Its original sink was
    ``~/tentacle_click_debug.log`` — an unmanaged, never-reclaimed file in the user's home
    (a 2.2 MB one was found there from a July capture). Managed scratch owns it now, with
    an env override for anyone who wants it somewhere specific.
    """

    def setUp(self):
        from blendertk.ui_utils.marking_menu_host.click_debugger import ClickDebugger

        self.dbg = ClickDebugger
        self._cached = self.dbg._path
        self.dbg._path = None

    def tearDown(self):
        self.dbg._path = self._cached

    def test_tracer_is_off_until_enabled(self):
        """Nothing is opened, filtered or traced by import alone — and the import needs no Qt
        (the event filter's ``QObject`` class is built on first ``enable()``)."""
        self.assertIsNone(
            self.dbg._filter_class, "the filter class must be built by enable() only"
        )
        self.assertIsNone(
            self.dbg._fh, "the trace file handle must be opened by enable() only"
        )
        self.assertIsNone(
            self.dbg._filter, "the event filter must be installed by enable() only"
        )
        self.assertIsNone(
            self.dbg._slot_patch, "the slot trace must be installed by enable() only"
        )

    def test_a_write_while_disabled_creates_nothing(self):
        """``_write`` must be a silent no-op with no handle — not an implicit open."""
        target = Path(self.dbg.log_path())
        self.dbg._write("this line must never be recorded")
        self.assertFalse(target.exists(), f"a disabled tracer wrote to {target}")

    def test_sink_is_not_a_hardcoded_file_in_the_user_home(self):
        target = Path(self.dbg.log_path())
        home = Path(os.path.expanduser("~")).resolve()
        self.assertNotEqual(
            target.resolve().parent,
            home,
            f"the click trace sinks straight into the user's home directory ({target}); "
            "it must land in managed scratch (ptk.TempArtifacts) instead.",
        )

    def test_resolving_the_sink_creates_no_file(self):
        target = Path(self.dbg.log_path())
        self.assertFalse(
            target.exists(),
            "resolving the sink path must not create the file — only enable() opens it.",
        )

    def test_env_var_overrides_the_sink(self):
        """An explicit path wins, so a support capture can be steered anywhere."""
        wanted = Path(HERE) / "temp_tests" / "click_debug_override.log"
        with mock.patch.dict(os.environ, {self.dbg.PATH_ENV: str(wanted)}):
            self.dbg._path = None
            self.assertEqual(Path(self.dbg.log_path()), wanted)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
