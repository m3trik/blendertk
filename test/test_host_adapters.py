# !/usr/bin/python
# coding=utf-8
"""Every Blender host adapter satisfies the pythontk Protocol it implements.

pythontk declares each host seam as a ``runtime_checkable`` ``<Domain>Host``
Protocol beside its model (its own ``test_host_seams`` holds that rule); this is
the Blender half, mirror of mayatk's ``test/mock_tests/test_host_adapters.py``:
each adapter blendertk binds, built in a fresh headless Blender, is an instance
of its Protocol, so a member a Protocol gains is a member every adapter has. The
roster is checked against the seams pythontk exports: a new seam needs its row
here. Added: 2026-11-01
"""
import importlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _adapter(module, name):
    return lambda: getattr(importlib.import_module(module), name)()


#: ``(Protocol, what builds the adapter)`` for every seam blendertk implements.
ADAPTERS = (
    ("ShotsHost", _adapter("blendertk.anim_utils.shots._shots", "BlenderShotsHost")),
    (
        "ShotTransferHost",
        lambda: _adapter("blendertk.anim_utils.shots._shots", "BlenderShotsHost")().transfer(),
    ),
    (
        "KeyStashHost",
        _adapter("blendertk.anim_utils.key_stash._key_stash", "BlenderKeyStashHost"),
    ),
    (
        "SidecarHost",
        _adapter("blendertk.env_utils.hierarchy_sync.scene_data_sidecar", "BlenderSidecarHost"),
    ),
    (
        "WorkspaceMapHost",
        _adapter("blendertk.env_utils.workspace_map", "BlenderWorkspaceMapHost"),
    ),
    (
        "ProbeHost",
        _adapter("blendertk.light_utils.lightmap_baker._probe_placement", "BlenderProbeHost"),
    ),
    (
        "ProbeMesh",
        _adapter("blendertk.light_utils.lightmap_baker._probe_placement", "BlenderProbeHost"),
    ),
    (
        "SequenceHost",
        _adapter("blendertk.anim_utils.playblast_exporter", "BlenderPlayblastHost"),
    ),
    (
        "EmissiveGroupsHost",
        _adapter("blendertk.mat_utils.emissive_groups", "BlenderEmissiveGroups"),
    ),
    (
        "CollectHost",
        _adapter("blendertk.mat_utils.texture_path_editor", "TexturePathEditor"),
    ),
    (
        "TransferHost",
        _adapter(
            "blendertk.uv_utils.texture_combine._transfer_host", "BlenderTransferHost"
        ),
    ),
    (
        "OperationHost",  # the ``blender`` runner blendertk registers
        lambda: importlib.import_module(
            "blendertk.env_utils.blender_scene_files"
        ).BlenderSceneFiles.operation_host(),
    ),
)


class TestHostAdapters(unittest.TestCase):
    def test_every_adapter_satisfies_its_protocol(self):
        import pythontk as ptk

        for protocol, build in ADAPTERS:
            with self.subTest(protocol=protocol):
                seam = getattr(ptk, protocol, None)
                if seam is None:  # a pythontk older than the seam
                    self.skipTest(f"this pythontk exports no {protocol}")
                self.assertIsInstance(build(), seam)

    def test_every_seam_pythontk_exports_has_a_row(self):
        import pythontk as ptk

        seams = {
            name
            for name in ptk.__all__
            if name.endswith("Host")
            and not name.startswith("Pure")
            and getattr(getattr(ptk, name), "_is_protocol", False)
        }
        self.assertEqual(sorted(seams - {p for p, _build in ADAPTERS}), [])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # The harness's (ok/attempted) sentinel (run_tests.py records a suite
    # without it as FAILED); skips leave the ratio.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
