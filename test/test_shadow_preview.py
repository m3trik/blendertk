# !/usr/bin/python
# coding=utf-8
"""``ShadowPreview`` (``rig_utils/shadow_preview.py``), the device-free half.

``--background`` has no GPU backend, so the overlay's shader can neither
compile nor draw here. What runs is everything AROUND it: the assembled
fragment source (the shared body behind Blender's texel hook), the frame math
that turns the contact empty's matrix into the world-space frame the shader
takes, the headless refusal, the plane-side contract (visibility borrowed and
handed back, the export record unchanged), and the ``"shadow"`` export
preparer standing in for the producer. The compiled shader and the drawn
pixels are ``shadow_preview_gui_check.py``'s, which needs a windowed Blender.

Run (fresh instance, never an existing session)::

    blender --background --factory-startup --python blendertk/test/test_shadow_preview.py
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pythontk as ptk  # noqa: E402
from blendertk.env_utils.fbx_utils import FbxUtils  # noqa: E402
from blendertk.rig_utils.shadow_preview import ShadowPreview  # noqa: E402
from blendertk.rig_utils.shadow_rig import ShadowRig  # noqa: E402


class TestShaderText(unittest.TestCase):
    """The assembled fragment: Blender's hook around the shared body."""

    def test_the_hook_precedes_the_shared_body_and_main_follows(self):
        text = ShadowPreview.fragment_source()
        body = ptk.ShadowHorizon.shader_source("glsl")
        self.assertIn(body, text)
        self.assertLess(text.index("SH_Fetch(int col"), text.index("float ShAlpha("))
        self.assertLess(text.index("float ShAlpha("), text.index("fragColor = "))
        self.assertIn("texelFetch(horizonMap", text)

    def test_the_uniform_block_matches_what_the_draw_fills(self):
        """``_plane_params`` packs eight vec4s in the struct's order; a member
        added on one side without the other reads as garbage, silently."""
        struct = ShadowPreview.params_struct()
        for member in (
            "origin",
            "axisA",
            "axisB",
            "axisUp",
            "source",
            "sourceSize",
            "range",
            "rect",
        ):
            self.assertIn(f"vec4 {member};", struct)
        self.assertEqual(struct.count("vec4 "), 8)


class TestFrameMath(unittest.TestCase):
    """``frame_params``: the contact's LOCAL frame into world space."""

    def test_identity_contact_is_the_bakes_frame(self):
        import numpy as np

        m = np.eye(4)
        origin, a, b, up, ground = ShadowPreview.frame_params(
            m, 0.0, ShadowPreview.LOCAL_A, ShadowPreview.LOCAL_B, ShadowPreview.LOCAL_UP
        )
        self.assertEqual(origin, [0.0, 0.0, 0.0])
        self.assertEqual(a, [1.0, 0.0, 0.0])
        self.assertEqual(b, [0.0, 1.0, 0.0])
        self.assertEqual(up, [0.0, 0.0, 1.0])
        self.assertEqual(ground, 0.0)

    def test_the_frame_is_local_not_the_records_exporter_axes(self):
        """The record's ``HORIZON_FRAME`` is ``(X, -Z)`` in the file's axes;
        the bake ran with ``up = 2`` in the contact's own frame, so the
        preview's B is local +Y and its up is local +Z."""
        self.assertEqual(ShadowPreview.LOCAL_B, (0.0, 1.0, 0.0))
        self.assertEqual(ShadowPreview.LOCAL_UP, (0.0, 0.0, 1.0))
        self.assertEqual(ShadowRig.HORIZON_FRAME[1], (0.0, 0.0, -1.0))

    def test_a_rotated_lifted_contact(self):
        """Yaw 90 degrees about Z: local X becomes world Y. The contact sits
        0.5 above a ground at 0, so the ground is -0.5 along the frame's up
        -- the height the shared body projects every fragment to."""
        import numpy as np

        c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
        m = np.array(
            [[c, -s, 0, 1.0], [s, c, 0, 2.0], [0, 0, 1, 0.5], [0, 0, 0, 1]], float
        )
        origin, a, b, up, ground = ShadowPreview.frame_params(
            m, 0.0, ShadowPreview.LOCAL_A, ShadowPreview.LOCAL_B, ShadowPreview.LOCAL_UP
        )
        self.assertEqual(origin, [1.0, 2.0, 0.5])
        for got, want in zip(a, (0.0, 1.0, 0.0)):
            self.assertAlmostEqual(got, want, places=9)
        for got, want in zip(b, (-1.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want, places=9)
        self.assertEqual(up, [0.0, 0.0, 1.0])
        self.assertAlmostEqual(ground, -0.5)

    def test_up_is_never_a_cross_product_of_the_bearing_axes(self):
        """``cross(B, A)`` is -Z for this frame (X toward Y is a positive
        turn about Z); the up the map was baked with is +Z. Deriving up from
        the bearing sense put every Blender source below the horizon."""
        import numpy as np

        a, b = np.array(ShadowPreview.LOCAL_A), np.array(ShadowPreview.LOCAL_B)
        self.assertEqual(np.cross(b, a).tolist(), [0.0, 0.0, -1.0])
        self.assertEqual(list(ShadowPreview.LOCAL_UP), [0.0, 0.0, 1.0])


class TestLifecycleHeadless(unittest.TestCase):
    """The plane-side contract, without a draw loop."""

    def setUp(self):
        import bpy

        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
        self.cube = bpy.context.active_object
        self.rig = ShadowRig.create(
            [self.cube],
            light_pos=(5, 5, 10),
            texture_res=64,
            rig_type="horizon",
            horizon_bins=8,
            horizon_size=(32, 16),
        )
        self.plane = self.rig.shadow_plane
        self._paths = [self.rig.texture_path, self.rig.horizon_path]

    def tearDown(self):
        FbxUtils.unregister_export_preparer("shadow")
        ShadowPreview._planes.clear()
        for path in self._paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    def test_headless_is_refused_with_the_reason(self):
        self.assertIn("background", ShadowPreview.refusal())
        with self.assertRaises(ValueError) as caught:
            ShadowPreview.attach(self.plane)
        self.assertIn("background", str(caught.exception))
        self.assertFalse(ShadowPreview.is_attached(self.plane))
        self.assertFalse(self.plane.hide_get(), "a refused attach borrows nothing")

    def test_a_projected_plane_is_refused_first(self):
        projected = ShadowRig.create(
            [self.cube], light_pos=(5, 5, 10), texture_res=64, source_name="other"
        )
        self._paths.append(projected.texture_path)
        with self.assertRaises(ValueError) as caught:
            ShadowPreview.attach(projected.shadow_plane)
        self.assertIn("not a horizon", str(caught.exception))

    def test_plane_params_read_the_rig_without_drawing(self):
        """Everything the draw needs, from the stamps and the live matrices."""
        params, image = ShadowPreview._plane_params(self.plane)
        self.assertIsNotNone(image)
        self.assertEqual(image.name, f"{self.rig._base}_horizon")
        self.assertEqual(len(params), 32, "eight vec4s")
        record = ShadowRig.export_record(self.plane)
        hz = record["horizon"]
        self.assertEqual(params[7], float(hz["bins"]))
        self.assertEqual(params[11], float(hz["layout"][0]))
        self.assertEqual(params[15], float(hz["layers"]))
        self.assertEqual(params[19], 1.0, "a positional source: w = 1")
        self.assertEqual(params[22:24], [float(hz["tile"][0]), float(hz["tile"][1])])
        self.assertEqual(params[24:27], [hz["r_min"], hz["r_max"], hz["max_stretch"]])
        contact = ShadowRig._plane_contact(self.plane)
        for got, want in zip(params[0:3], contact.matrix_world.translation):
            self.assertAlmostEqual(got, want, places=6)

    def test_the_record_is_the_same_with_the_plane_borrowed(self):
        """Simulated attach (no draw loop): the plane hidden and the prop
        stamped, exactly what ``attach`` does past the refusal."""
        before = ShadowRig.export_record(self.plane)
        self.plane[ShadowPreview.HIDDEN_PROP] = False
        self.plane.hide_set(True)
        ShadowPreview._planes.append(self.plane.name)
        self.assertEqual(ShadowRig.export_record(self.plane), before)
        self.assertEqual(
            [o.name for o in ShadowPreview.attached_planes()], [self.plane.name]
        )
        self.assertTrue(ShadowPreview.detach(self.plane))
        self.assertFalse(self.plane.hide_get(), "visibility handed back")
        self.assertNotIn(ShadowPreview.HIDDEN_PROP, self.plane)
        self.assertEqual(ShadowRig.export_record(self.plane), before)
        self.assertFalse(ShadowPreview.detach(self.plane), "nothing left to detach")

    def test_the_export_preparer_stands_the_preview_down_and_republishes(self):
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        self.plane[ShadowPreview.HIDDEN_PROP] = False
        self.plane.hide_set(True)
        ShadowPreview._planes.append(self.plane.name)
        ShadowPreview._register_export_preparer()
        self.assertIn("shadow", FbxUtils._export_preparers)
        FbxUtils.run_export_preparers(only=["shadow"])
        self.assertFalse(self.plane.hide_get(), "visible again before the export")
        self.assertEqual(ShadowPreview.attached_planes(), [])
        node = DataNodes.get_export_node(create=False)
        self.assertIsNotNone(node)
        payload = json.loads(node[ShadowRig.SHADOW_METADATA])
        (record,) = [p for p in payload["planes"] if p["name"] == self.plane.name]
        self.assertTrue(record["texture"])
        self.assertEqual(record["type"], "horizon")

    def test_toggle_reports_per_plane_and_never_stops_at_a_failure(self):
        done, failed = ShadowPreview.toggle([self.plane, "no_such_plane"], on=False)
        self.assertEqual(done, [self.plane])
        self.assertEqual(len(failed), 1)


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
