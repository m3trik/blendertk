# !/usr/bin/python
# coding=utf-8
"""``run_tests.py`` (the Blender test harness): what it hands each child.

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

Nor may a child work out its own temp dir: :class:`TestChildTempRoot`.

Run (the temp-root cases launch a real Blender when one is installed)::

    .venv/Scripts/python.exe blendertk/test/test_run_tests.py
"""

import os
import shutil
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


class TestChildTempRoot(unittest.TestCase):
    """Every child is handed the run's temp root outright, never left to derive it.

    The runner routes the whole run's temp into one throwaway root, and a child
    used to receive it only as ``TMPDIR``/``TEMP``/``TMP``. A fresh interpreter
    re-derives its temp dir from those: ``tempfile`` probes each with a
    throwaway write and, on any failure but ``FileExistsError``, moves on --
    past all three (they name the same root) to ``~\\AppData\\Local\\Temp``,
    without a word. Measured in a fresh Blender 5.1 and in the venv: a root
    that is gone, an ``OSError`` on three probes in a row, or a refused create
    each resolved the user's real temp dir. One full run did exactly that
    (2026-09-26, caught by ``test_scene_import``'s isolation canary), and a
    suite there that cleans up by prefix deletes whatever else matches -- how a
    cached production conversion was lost once.

    Real children, not a mocked ``subprocess.run``: the defect lives in the
    child interpreter's own startup, which a fake never reaches. Each case runs
    under both hosts: a headless Blender (when one is installed) and a plain
    interpreter, the ``.venv`` route.
    """

    #: The suite each child runs. It refuses every write into the root, as a
    #: lock or a full volume would, then asks where temp files go. It also
    #: checks it runs as ``python <suite>`` would run it: the pinned route
    #: starts a plain interpreter through ``-c``.
    PROBE = r"""
import errno, os, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
root = os.environ["TEMP"]
open(os.path.join(here, "ran"), "w").close()
pinned = tempfile.tempdir  # before any suite code asks
# Blender's --python leaves sys.path alone; a script run puts its own dir first.
as_script = __name__ == "__main__" and (
    "bpy" in sys.modules or os.path.normcase(sys.path[0]) == os.path.normcase(here)
)
real_open = os.open


def refuse(path, *args, **kwargs):
    if os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(
        os.path.abspath(root)
    ):
        raise OSError(errno.EIO, "probe write refused", path)
    return real_open(path, *args, **kwargs)


os.open = refuse
try:
    got = tempfile.gettempdir()
finally:
    os.open = real_open
ok = pinned is not None and os.path.normcase(got) == os.path.normcase(root)
print(f"{'OK  ' if ok else 'FAIL'} temp root | pinned={pinned!r} resolved={got!r}")
print(f"{'OK  ' if as_script else 'FAIL'} run as a script | {__name__} {sys.path[0]!r}")
passed = int(ok) + int(as_script)
print(f"===RESULT: {'PASS' if passed == 2 else 'FAIL'}=== ({passed}/2)")
"""

    def setUp(self):
        self.work = os.path.join(HERE, "temp_tests", f"child_temp_{os.getpid()}")
        os.makedirs(self.work, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.work, True)
        self.root = os.path.join(self.work, "root")
        self.suite = Path(self.work, "probe_suite.py")
        self.suite.write_text(self.PROBE, encoding="utf-8")
        self.ran = os.path.join(self.work, "ran")

    @staticmethod
    def _blender() -> str:
        """This Blender when hosted by one, else the harness's own lookup."""
        try:
            import bpy

            return bpy.app.binary_path
        except ImportError:
            pythontk = os.path.join(os.path.dirname(os.path.dirname(HERE)), "pythontk")
            if os.path.isdir(pythontk) and pythontk not in sys.path:
                sys.path.insert(0, pythontk)
            return run_tests.find_blender() or ""

    def _hosts(self):
        """(label, run_suite kwargs) per host this machine can launch."""
        hosts = [("python", {"python": sys.executable})]
        if self._blender():
            hosts.insert(0, ("blender", {}))
        return hosts

    def _run(self, **kwargs):
        """One suite child, launched the way a real run launches it: the env
        names the root (as :meth:`TestSandbox.temp` leaves it) and the runner
        holds it."""
        if os.path.exists(self.ran):
            os.remove(self.ran)
        runner = run_tests.BlenderTestRunner(blender=self._blender() or "blender")
        runner.temp_root = self.root
        env = {name: self.root for name in ("TMPDIR", "TEMP", "TMP")}
        with mock.patch.dict(os.environ, env):
            return runner.run_suite(self.suite, **kwargs)

    def test_a_refused_probe_write_never_reaches_the_real_temp(self):
        os.makedirs(self.root, exist_ok=True)
        for label, kwargs in self._hosts():
            with self.subTest(host=label):
                passed, ok, failed, _ = self._run(**kwargs)
                self.assertTrue(passed, f"{label}: {ok} ok / {failed} failed")

    def test_a_root_that_vanished_is_recreated_not_swapped_for_the_real_temp(self):
        for label, kwargs in self._hosts():
            with self.subTest(host=label):
                shutil.rmtree(self.root, ignore_errors=True)
                passed, ok, failed, _ = self._run(**kwargs)
                self.assertTrue(passed, f"{label}: {ok} ok / {failed} failed")
                self.assertTrue(os.path.isdir(self.root))

    def test_a_root_that_cannot_be_used_stops_the_child_before_the_suite(self):
        """Loud, not quiet: an unpinned suite is the defect, so it must not run."""
        with open(self.root, "w") as fh:  # a FILE where the root should be
            fh.write("not a directory")
        for label, kwargs in self._hosts():
            with self.subTest(host=label):
                passed, _, failed, _ = self._run(**kwargs)
                self.assertFalse(passed)
                self.assertEqual(failed, 1)
                self.assertFalse(os.path.exists(self.ran), "the suite ran unpinned")

    def test_a_run_without_a_root_launches_the_suite_unchanged(self):
        """An unimportable pythontk leaves no root: the suite must then fail on
        its own, not on a pin that has nothing to pin."""
        runner = run_tests.BlenderTestRunner(blender="blender.exe")
        calls = []

        def fake_run(cmd, **run_kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(run_tests.subprocess, "run", side_effect=fake_run):
            runner.run_suite(self.suite)
            runner.run_suite(self.suite, python="py.exe")
        self.assertEqual(
            calls,
            [
                ["blender.exe", "--background", "--factory-startup", "--python"]
                + [str(self.suite)],
                ["py.exe", str(self.suite)],
            ],
        )


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
