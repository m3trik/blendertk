# !/usr/bin/python
# coding=utf-8
"""Regression tests for ShotManifestController._load_csv with a URL source.

Mirror of the URL cases in mayatk's ``test_shot_manifest_csv_load.py``: a URL
skips the file-exists gate, a fetch failure is reported as a fetch failure
(never as the local disk/sync diagnosis), and the live validator accepts an
existing file or an ``http(s)`` URL only.

Needs **Qt, not bpy**: ``_load_csv`` is exercised as an unbound method against
a stub ``self`` so no Qt window / switchboard construction is required.  Run
under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_shot_manifest_csv_load.py
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pythontk as ptk  # noqa: E402
from pythontk.core_utils.engines.shots.manifest.manifest_model import (  # noqa: E402
    ColumnMap,
)

try:
    from uitk.widgets.mixins.tooltip_mixin import TooltipFormat
    from blendertk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
        ShotManifestController,
    )
except ImportError:  # pragma: no cover - Qt absent (Blender's headless Python)
    TooltipFormat = None
    ShotManifestController = None

_SLOTS = "blendertk.anim_utils.shots.shot_manifest.shot_manifest_slots"


def _make_stub_controller():
    """A stub carrying only the collaborators ``_load_csv`` touches."""
    ctrl = types.SimpleNamespace()
    ctrl._sync_csv_widgets = MagicMock()
    ctrl._set_footer = MagicMock()
    ctrl._load_data = MagicMock()
    ctrl._refresh_ranges = MagicMock()
    ctrl._recent_csv_option = MagicMock()
    ctrl._active_mapping = None
    ctrl._column_map = ColumnMap()
    ctrl._csv_path = ""
    ctrl.logger = MagicMock()
    ctrl.ui = MagicMock()
    ctrl._describe_read_failure = ShotManifestController._describe_read_failure
    # The tooltip/invalid helpers are plain methods; bind the real ones so the
    # stub renders genuine uitk-formatted tooltips.
    ctrl.sb = types.SimpleNamespace(tooltip=TooltipFormat)
    ctrl._csv_source_tooltip = lambda problem=None: (
        ShotManifestController._csv_source_tooltip(ctrl, problem)
    )
    ctrl._mark_csv_invalid = lambda reason: ShotManifestController._mark_csv_invalid(
        ctrl, reason
    )
    return ctrl


@unittest.skipIf(
    ShotManifestController is None, "Qt not available (Blender headless Python)"
)
class LoadCsvUrlSourceTest(unittest.TestCase):
    """A URL is a CSV source: it skips the file gate, and a fetch failure is
    reported as a fetch failure, never as the local disk/sync diagnosis."""

    _URL = "https://docs.google.com/spreadsheets/d/1AbC/edit#gid=0"

    def test_url_skips_the_file_gate_and_loads(self):
        ctrl = _make_stub_controller()
        with patch(f"{_SLOTS}.ManifestModel.parse_csv", return_value=[]) as parse:
            ShotManifestController._load_csv(ctrl, self._URL)

        self.assertEqual(parse.call_args.args[0], self._URL)
        ctrl._load_data.assert_called_once()
        ctrl.ui.txt_csv_path.reset_action_color.assert_called_once()

    def test_fetch_failure_reports_the_fetch_not_the_disk(self):
        ctrl = _make_stub_controller()
        err = ptk.RemoteFile.Error("Can't fetch https://x: HTTP 404 Not Found.")
        with patch(f"{_SLOTS}.ManifestModel.parse_csv", side_effect=err):
            ShotManifestController._load_csv(ctrl, self._URL)

        ctrl._sync_csv_widgets.assert_called_once_with(True)
        ctrl._load_data.assert_not_called()
        ctrl.ui.txt_csv_path.set_action_color.assert_called_with("invalid")
        msg = ctrl._set_footer.call_args.args[0].lower()
        self.assertIn("can't fetch", msg)
        self.assertNotIn("disk may be full", msg)

    def test_missing_local_file_is_still_gated(self):
        ctrl = _make_stub_controller()
        ShotManifestController._load_csv(ctrl, "X:/no/such/file.csv")

        ctrl._load_data.assert_not_called()
        ctrl.ui.txt_csv_path.set_action_color.assert_called_with("invalid")

    def test_fetch_failure_reason_reaches_the_tooltip_too(self):
        ctrl = _make_stub_controller()
        err = ptk.RemoteFile.Error("Can't fetch https://x: HTTP 404 Not Found.")
        with patch(f"{_SLOTS}.ManifestModel.parse_csv", side_effect=err):
            ShotManifestController._load_csv(ctrl, self._URL)

        tip = ctrl.ui.txt_csv_path.setToolTip.call_args.args[0]
        self.assertIn("HTTP 404", tip)
        self.assertIn("CSV Source", tip)


if __name__ == "__main__":
    # Not unittest.main(): under the Blender harness argv carries Blender's own
    # flags (--background --factory-startup --python), which argparse rejects.
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # Report the tally in the harness's (ok/attempted) form; a fully skipped
    # suite (Qt absent) reports "skipped" rather than counting as 0/0 checks.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
