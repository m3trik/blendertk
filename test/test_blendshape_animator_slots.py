# !/usr/bin/python
# coding=utf-8
r"""Blendshape-animator panel controller test — the Qt half of ``test_blendshape_animator.py``.

Needs **Qt, not bpy**: ``blendshape_animator_slots`` imports ``qtpy`` at module scope, while
the engine behaviour it wraps (create / tween / apply, which need a real scene) is covered by
``test_blendshape_animator.py`` under the Blender harness. Run under the workspace ``.venv``::

    .venv\Scripts\python.exe blendertk/test/test_blendshape_animator_slots.py

Under the Blender harness (``--background --factory-startup``, which ships no Qt binding) it
reports the ``skipped`` sentinel and ``run_tests.py`` re-runs it under the ``.venv``.

Covers the footer status channel: every panel message went through ``_set_status``, which
called a nonexistent ``footer.set_status`` and let a broad ``except`` downgrade the resulting
AttributeError to a log line — so no status message ever reached the panel. (mayatk's twin
carried the identical defect; fixed there 2026-08-14.)
"""
import os
import sys
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
    from qtpy import QtWidgets  # noqa: F401  (import probe only)
except Exception:  # pragma: no cover - Qt absent (Blender's headless Python)
    QtWidgets = None


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class TestSetStatusFooter(unittest.TestCase):
    """``_set_status`` must call uitk Footer's real API (``setText``) with a valid
    level, and must not swallow a wrong-API AttributeError."""

    class _RecordingFooter:
        def __init__(self):
            self.calls = []

        def setText(self, text, level=None):
            self.calls.append((text, level))

    def _make_host(self, footer):
        import types
        from unittest import mock
        from blendertk.anim_utils.blendshape_animator.blendshape_animator_slots import (
            BlendshapeAnimatorSlots,
        )

        host = types.SimpleNamespace(
            ui=types.SimpleNamespace(footer=footer), logger=mock.MagicMock()
        )
        return BlendshapeAnimatorSlots, host

    def test_set_status_calls_settext_with_valid_level(self):
        from uitk.widgets.footer import Footer

        cls, host = self._make_host(self._RecordingFooter())
        cls._set_status(host, "hello")
        self.assertEqual(len(host.ui.footer.calls), 1)
        text, level = host.ui.footer.calls[0]
        self.assertEqual(text, "hello")
        self.assertIn(level, Footer.LEVEL_COLORS)

    def test_set_status_forwards_level(self):
        cls, host = self._make_host(self._RecordingFooter())
        cls._set_status(host, "boom", level="error")
        self.assertEqual(host.ui.footer.calls, [("boom", "error")])

    def test_wrong_api_attributeerror_not_swallowed(self):
        """A footer that lacks setText is a wiring bug, not a teardown race — it must
        raise, not silently degrade every status message to a log line."""

        class _NoSetText:
            pass

        cls, host = self._make_host(_NoSetText())
        with self.assertRaises(AttributeError):
            cls._set_status(host, "lost")

    def test_deleted_widget_runtimeerror_falls_back_to_logger(self):
        class _Dead:
            def setText(self, text, level=None):
                raise RuntimeError("Internal C++ object already deleted.")

        cls, host = self._make_host(_Dead())
        cls._set_status(host, "teardown")
        host.logger.info.assert_called_once_with("teardown")

    def test_call_site_levels_are_footer_vocabulary(self):
        import inspect
        import re

        from uitk.widgets.footer import Footer
        from blendertk.anim_utils.blendshape_animator import blendshape_animator_slots

        src = inspect.getsource(blendshape_animator_slots)
        # Anchored so ptk.LoggingMixin's unrelated ``log_level="WARNING"`` ctor default
        # isn't mistaken for a footer level (mayatk's twin panel takes no log_level).
        levels = set(re.findall(r'(?<![\w])level="(\w+)"', src))
        self.assertTrue(levels, "no levelled call sites found")
        self.assertTrue(
            levels.issubset(set(Footer.LEVEL_COLORS)),
            f"unknown footer levels: {levels - set(Footer.LEVEL_COLORS)}",
        )

    def test_no_call_site_uses_the_dead_set_status_api(self):
        import inspect

        from blendertk.anim_utils.blendshape_animator import blendshape_animator_slots

        src = inspect.getsource(blendshape_animator_slots)
        self.assertNotIn(
            "footer.set_status",
            src,
            "uitk Footer has no set_status(); the call degrades every message to log-only",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # Report the tally in the harness's (ok/attempted) form -- skips leave the ratio
    # entirely, so a fully skipped suite reports "skipped" and run_tests.py re-runs it
    # under the workspace .venv.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
