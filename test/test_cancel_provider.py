# !/usr/bin/python
# coding=utf-8
"""Tests for blendertk.BlenderCancelProvider — Blender's host cancel strategy.

Qt-side, bpy-free (like ``test_blender_ui_handler``): the provider subclasses a
uitk class, and headless Blender ships no Qt binding — so this runs under the
workspace ``.venv``, where the deferred ``bpy`` imports are absent. That absence
is itself worth pinning: every host call has to degrade to a no-op rather than
raise into the slot dispatcher.

Parity contract with ``mayatk.MayaCancelProvider`` is asserted structurally, so
a hook added to one twin and not the other fails here.

Harness contract (``run_tests.py``): launched under ``blender --background``,
which ships no Qt, this SKIPS with a ``(skipped)`` sentinel -- which is what
makes the runner re-launch it under the workspace ``.venv`` where the binding
exists. Reporting a bare PASS there would look green while never executing a
single assertion, and omitting the sentinel entirely marks the suite FAILED
however green it was.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")


class TestBlenderCancelProvider(unittest.TestCase):
    def setUp(self):
        from uitk.managers.cancel_manager import CancelManager
        from blendertk.ui_utils.cancel_provider import BlenderCancelProvider

        self.CancelManager = CancelManager
        self.provider = BlenderCancelProvider()

    def tearDown(self):
        self.CancelManager.reset()

    def test_is_a_cancel_provider(self):
        from uitk.managers.cancel_manager import CancelProvider

        self.assertIsInstance(self.provider, CancelProvider)
        self.assertEqual(self.provider.name, "blender")

    def test_install_registers_with_uitk(self):
        from blendertk.ui_utils.cancel_provider import BlenderCancelProvider

        provider = BlenderCancelProvider.install()
        self.assertIs(self.CancelManager.provider(), provider)

    def test_excludes_user_input(self):
        """A progress tick must not dispatch queued input into a nested slot."""
        self.assertTrue(self.provider.exclude_user_input)

    def test_declares_no_rollback_support(self):
        """Blender cannot count the undo steps to unwind; see the backlog."""
        self.assertFalse(self.provider.supports_rollback)

    def test_supplies_a_pump_independent_escape_source(self):
        """Blender has no host Esc peek, so the key-hold probe is the source."""
        sources = self.provider.create_sources(None)
        self.assertEqual(len(sources), 1)
        self.assertTrue(callable(sources[0]))

    def test_bracket_balances_without_bpy(self):
        token = self.provider.begin(None, "job")
        self.assertIsNotNone(token)
        self.assertEqual(len(self.provider._brackets), 1)
        self.provider.tick(1, 10, "working")
        self.provider.end(token)
        self.assertEqual(len(self.provider._brackets), 0)

    def test_nested_brackets_unwind(self):
        outer = self.provider.begin(None, "outer")
        inner = self.provider.begin(None, "inner")
        self.assertEqual(len(self.provider._brackets), 2)
        self.provider.end(inner)
        self.provider.end(outer)
        self.assertEqual(len(self.provider._brackets), 0)

    def test_end_tolerates_a_junk_token(self):
        self.provider.end(None, cancelled=True, rollback=True)  # must not raise

    def test_tick_without_a_bracket_is_a_noop(self):
        self.provider.tick(1, 10, "working")  # must not raise

    def test_cancelled_rollback_request_is_reported(self):
        with self.assertLogs(
            "blendertk.ui_utils.cancel_provider", level="WARNING"
        ) as cm:
            token = self.provider.begin(None, "job", rollback=True)
            self.provider.end(token, cancelled=True, rollback=True)
        self.assertTrue(any("cannot roll back" in m for m in cm.output))

    def test_no_warning_when_the_run_completed(self):
        token = self.provider.begin(None, "job", rollback=True)
        with self.assertNoLogs(
            "blendertk.ui_utils.cancel_provider", level="WARNING"
        ):
            self.provider.end(token, cancelled=False, rollback=True)

    def test_hook_parity_with_the_mayatk_twin(self):
        """The tentacle slots stay branch-free only if both twins match."""
        from uitk.managers.cancel_manager import CancelProvider
        import inspect

        hooks = ("create_sources", "begin", "tick", "end", "pump", "install")
        for hook in hooks:
            self.assertTrue(
                hasattr(type(self.provider), hook), f"missing hook: {hook}"
            )
        for hook in ("begin", "tick", "end"):
            self.assertEqual(
                inspect.signature(getattr(type(self.provider), hook)),
                inspect.signature(getattr(CancelProvider, hook)),
                f"{hook} diverges from the CancelProvider contract",
            )


if __name__ == "__main__":
    try:
        import qtpy  # noqa: F401 — the binding every assertion here needs
    except Exception:
        # Under the Blender harness. Emit the SKIP sentinel, not a tally:
        # run_tests.py re-runs a skipped suite under the .venv, and a bare
        # PASS here would read green while never executing an assertion.
        # ``except Exception`` deliberately -- qtpy present but binding-less
        # raises QtBindingsNotFoundError, which ImportError alone would miss.
        print("===RESULT: PASS=== (skipped)")
        raise SystemExit(0)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # Report the tally in the harness's (ok/attempted) form. run_tests.py parses
    # stdout for this sentinel and records a suite that omits it as FAILED
    # however green its assertions were -- which is exactly what this file did
    # on its first run, as the only suite under test/ lacking it. Skips are
    # neither passes nor failures, so they leave the ratio entirely.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
