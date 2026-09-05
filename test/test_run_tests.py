# !/usr/bin/python
# coding=utf-8
"""``run_tests.py`` (the Blender test harness), the part that needs no Blender.

A suite child must never be able to wait on a human. ``SceneExporter.confirm``
answers a ``[y/N]`` on the console whenever ``sys.stdin.isatty()`` is true, and
a Blender launched by the harness inherits the launching console -- so
``test_smart_bake``'s deliberate failed-check export sat on
``sys.stdin.readline()`` until the suite timer killed it (2026-09-04: TIMEOUT
after 600 s in the full run, 2.3 s of CPU in 100 s when re-run alone, and a
PASS in under 75 s when the same module was run by hand with no console). The
harness therefore hands every child ``stdin=subprocess.DEVNULL``: ``isatty()``
is then false and the consent seam answers no, exactly as its docstring
promises for "nobody there to ask".

Run (Blender-free)::

    .venv/Scripts/python.exe blendertk/test/test_run_tests.py
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_tests  # noqa: E402


class TestChildStdin(unittest.TestCase):
    """Every child the harness launches gets a closed stdin."""

    def _launch(self, **kwargs):
        runner = run_tests.BlenderTestRunner(blender="blender.exe")
        calls = []

        def fake_run(cmd, **run_kwargs):
            calls.append((cmd, run_kwargs))
            return subprocess.CompletedProcess(
                cmd, 0, stdout="===RESULT: PASS=== (1/1)\n", stderr=""
            )

        with mock.patch.object(run_tests.subprocess, "run", side_effect=fake_run):
            result = runner.run_suite(Path(HERE, "test_fake.py"), **kwargs)
        self.assertEqual(len(calls), 1, "one child per suite")
        self.assertTrue(result[0], result)
        return calls[0]

    def test_blender_child_cannot_read_the_console(self):
        cmd, kwargs = self._launch()
        self.assertEqual(cmd[0], "blender.exe")
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_venv_child_cannot_read_the_console(self):
        cmd, kwargs = self._launch(python=sys.executable)
        self.assertEqual(cmd[0], sys.executable)
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_capture_and_kill_timer_are_kept(self):
        """The stdin change must not displace the capture the sentinel parse
        reads, nor the kill timer that turns a hung suite into a failure."""
        _, kwargs = self._launch()
        self.assertTrue(kwargs.get("capture_output"))
        self.assertEqual(kwargs.get("timeout"), 600)


if __name__ == "__main__":
    argv = [sys.argv[0]] + (
        sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    )
    result = unittest.main(argv=argv, exit=False, verbosity=2).result
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(
        f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({passed}/{result.testsRun})"
    )
    sys.exit(0 if result.wasSuccessful() else 1)
