# !/usr/bin/python
# coding=utf-8
"""The Blender ``ShotSequencer`` supplies every scene hook pythontk leaves empty.

Needs neither Qt nor bpy: structural, over the engine's classes. Run under the
workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_shot_sequencer_hooks.py

mayatk's twin is its ``test_shot_sequencer_hooks.py``. The Shots panel and its
guard (every edit that can be refused goes through ``_boundary_edit``) are
tentacle's (``test/test_shots_panel.py``); the engine's behavior is
``test_shot_sequencer.py``'s, under the Blender harness.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)


class TestSequencerAdapterHooks(unittest.TestCase):
    """The Blender ``ShotSequencer`` supplies the scene hooks pythontk leaves empty.

    ``pythontk.ShotSequencer`` reaches the scene only through the hooks on
    ``_ShotSequencerHooks``, and each default describes an EMPTY scene: no
    keys, no audio, nothing to hold. A hook this adapter does not override is
    therefore no error anywhere -- the panel's edits silently move bounds only.
    Structural, so it needs neither Qt nor bpy; mayatk's twin guard is its
    ``test_shot_sequencer_hooks.py``.
    """

    #: Hooks inherited on purpose. ``_content_batch``: a VSE strip's position IS
    #: its keyed state, so there is no audio re-sync to batch (the engine
    #: module's "Audio = VSE sound strips").  ``_member_names``: a Blender
    #: object has ONE name -- no short name beside a long path, as a Maya node
    #: has -- so the default's names-as-given comparison is the match.
    INHERITED = frozenset({"_content_batch", "_member_names"})

    @staticmethod
    def _classes():
        """``(adapter, pythontk's ShotSequencer, its hook defaults)``."""
        import pythontk as ptk
        from pythontk.core_utils.engines.shots.shot_sequencer import (
            _ShotSequencerHooks,
        )

        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        return ShotSequencer, ptk.ShotSequencer, _ShotSequencerHooks

    def test_every_hook_default_is_overridden(self):
        adapter, core, defaults = self._classes()
        hooks = {
            n for n in vars(defaults) if not (n.startswith("__") and n.endswith("__"))
        } - {"STORE_CLASS"}
        self.assertTrue(hooks, "no hooks read off _ShotSequencerHooks")
        mro = adapter.__mro__
        ahead = mro[: mro.index(core)]
        inherited = {n for n in hooks if not any(n in vars(c) for c in ahead)}
        self.assertEqual(
            [],
            sorted(inherited - self.INHERITED),
            "these hooks resolve to pythontk's empty-scene default, so every "
            "operation reaching them edits bounds only",
        )
        self.assertEqual(
            [],
            sorted(self.INHERITED - inherited),
            "listed in INHERITED but overridden now (or no longer a hook): "
            "drop them from INHERITED",
        )

    def test_no_blender_base_sits_behind_the_hook_defaults(self):
        """A helper base listed after pythontk's class is shadowed by it: a hook
        implemented on ``_ShotSequencerInternal`` (the house home for helpers)
        resolves to the pythontk no-op instead. The bases read
        ``(_ShotSequencerCore, _ShotSequencerInternal)`` until 2026-09-27."""
        adapter, core, _ = self._classes()
        mro = adapter.__mro__
        behind = [
            c.__qualname__
            for c in mro[mro.index(core) :]
            if c.__module__.partition(".")[0] == "blendertk"
        ]
        self.assertEqual(
            [],
            behind,
            "these blendertk bases come after pythontk's ShotSequencer in the "
            "MRO, so its hook defaults shadow anything they define",
        )


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
