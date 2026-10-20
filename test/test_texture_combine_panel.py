# !/usr/bin/python
# coding=utf-8
"""Texture Combine panel test -- the Qt half of ``test_texture_combine``.

Needs **Qt, not bpy**: loads ``texture_combine.ui`` and wires ``TextureCombineSlots``
through a real (offscreen) Qt / Switchboard / BlenderUiHandler stack, with the engine's
scene reads stood in for by a small fake file (two quads, two materials, the record in a
dict) -- the plan itself is the real ``ptk.UvCombine``. Mirror of mayatk's
``test_texture_combine_panel``: the mode switch shows each mode's groups, the Sources table
follows the selection and re-plans on a Scale edit, Combine / Update / the picker's verbs
drive the engine, Combine reads the meshes at run time, and Transfer mode's rows grey as
they should. The engine and the Transfer runs on real geometry are
``test_texture_combine.py`` / ``test_texture_combine_transfer_mode.py`` (Blender harness).
Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_texture_combine_panel.py
"""

import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import bpy  # noqa: F401

    QtWidgets = None  # the Blender harness: this suite is a .venv target
except Exception:
    try:
        from qtpy import QtWidgets
    except Exception:  # pragma: no cover - no Qt binding
        QtWidgets = None

if QtWidgets is not None:
    # Sandbox the real uitk\shared QSettings store (uitk/test/conftest.py owns the shim)
    # so loading the panel can't read/write the developer's live state.
    import importlib.util

    _conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
    if os.path.isfile(_conftest):
        _spec = importlib.util.spec_from_file_location("_uitk_conftest", _conftest)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
    else:
        QtWidgets = None


class _FakeFile:
    """The scene side the panel reads: quads by object name, each wearing one
    material (``{name: material}``), the selection, and the combine record."""

    def __init__(self):
        self.meshes = {}
        self.selection = []
        self.store = {}
        self.gathers = []

    def save(self, data):
        self.store.clear()
        self.store.update(data)

    def read(self, name):
        import numpy as np

        return {
            "uv_set": "UVMap",
            "uvs": np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64),
            "shell_ids": np.zeros(4, dtype=np.int64),
            "tri_uv": np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
            "tri_face": np.zeros(2, dtype=np.int64),
            "tri_area": np.full(2, 0.5),
            "materials": [self.meshes[name]],
            "face_material": np.zeros(1, dtype=np.int64),
            "transforms": [name],
        }

    def inputs(self, names):
        from blendertk.uv_utils.texture_combine.texture_combine import (
            CombineInputs,
            TextureCombine,
        )

        inputs = CombineInputs(transforms=list(names))
        for name in names:
            inputs.shapes[name] = self.read(name)
        TextureCombine._build_shells(inputs)
        for shell in inputs.shells:
            inputs.sources.setdefault(
                shell.group,
                {"maps": {"baseColor": f"/maps/{shell.group}.png"}, "resolution": 1024},
            )
        return inputs


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class _PanelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blendertk.uv_utils.texture_combine.texture_combine import TextureCombine

        cls.TC = TextureCombine
        cls.file = _FakeFile()
        f = cls.file

        def gather(engine, objects=None):
            names = list(objects) if objects is not None else list(f.selection)
            if not names:
                raise ValueError("no meshes to combine")
            f.gathers.append(names)
            return f.inputs(names)

        def combine(engine, objects=None, *, name=None, inputs=None, **settings):
            inputs = inputs or gather(engine, objects)
            engine.apply_rows(inputs, settings.get("rows") or ())
            plan = engine.plan(
                inputs,
                sizing=settings.get("sizing", "proportional"),
                normalize=settings.get("normalize", "resolution"),
            )
            name = name or "combined"
            f.store[name] = {
                "created": str(len(f.store)),
                "settings": {k: v for k, v in settings.items() if k != "rows"},
                "rows": [dict(r) for r in settings.get("rows") or ()],
                "meshes": {n: {"transforms": [n]} for n in inputs.transforms},
                "sources": {},
            }
            f.combined = (name, inputs, settings)
            return {
                "name": name,
                "material": name,
                "maps": {"baseColor": f"/out/{name}_BaseColor.png"},
                "meshes": len(inputs.transforms),
                "groups": plan.groups,
                "utilization": 0.8,
                "map_size": settings.get("map_size", 4096),
                "lightmaps": {},
            }

        def update(engine, name, **changes):
            f.updated = (name, changes)
            entry = f.store.pop(name)
            return combine(engine, list(entry["meshes"]), name=name, **changes)

        def restore(engine, name, forget=True):
            entry = f.store.pop(name) if forget else f.store[name]
            return list(entry["meshes"])

        cls._patches = [
            mock.patch.object(
                TextureCombine, "_load", classmethod(lambda c: dict(f.store))
            ),
            mock.patch.object(TextureCombine, "_save", staticmethod(f.save)),
            mock.patch.object(
                TextureCombine,
                "resolve_meshes",
                classmethod(
                    lambda c, objects=None: [
                        o if isinstance(o, str) else o.name
                        for o in (f.selection if objects is None else objects)
                    ]
                ),
            ),
            mock.patch.object(
                TextureCombine, "_uuid", classmethod(lambda c, t: str(t))
            ),
            mock.patch.object(
                TextureCombine,
                "_object",
                staticmethod(lambda o: NS(name=str(o)) if str(o) in f.meshes else None),
            ),
            mock.patch.object(TextureCombine, "gather", gather),
            mock.patch.object(
                TextureCombine,
                "inputs_for",
                lambda engine, name: f.inputs(list(f.store[name]["meshes"])),
            ),
            mock.patch.object(TextureCombine, "combine", combine),
            mock.patch.object(TextureCombine, "update", update),
            mock.patch.object(TextureCombine, "restore", restore),
        ]
        for patcher in cls._patches:
            patcher.start()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        handler = BlenderUiHandler(switchboard=Switchboard())
        # A process singleton: in a run that built it earlier it keeps ITS
        # switchboard, which is the one holding the UIs.
        cls.sb = handler.sb
        cls.ui = handler.get("texture_combine")
        cls.slots = cls.ui.slots
        for _ in range(5):
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls._patches):
            patcher.stop()

    def setUp(self):
        self.boxes = []
        patcher = mock.patch.object(
            self.sb,
            "message_box",
            side_effect=lambda text, *a, **k: self.boxes.append(text),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.file.store.clear()
        self.file.selection = []
        self.file.gathers.clear()
        self.ui.t_name.clear()
        self.ui.cmb_atlas.setCurrentIndex(0)
        self.ui.cmb_quality.setCurrentIndex(0)
        self._mode("combine")
        self.slots._refresh_sets(select="")
        self.slots._on_set()

    def _mode(self, mode):
        self.ui.cmb_mode.setCurrentIndex(self.ui.cmb_mode.findData(mode))
        self.app.processEvents()

    def _scene(self):
        self.file.meshes = {"pa": "matA", "pb": "matB"}
        self.file.selection = ["pa", "pb"]
        self.slots._follow_selection(force=True)
        return "pa", "pb"

    def _table(self):
        t = self.ui.tbl_sources
        return [
            [
                t.item(r, c).text() if t.item(r, c) else ""
                for c in range(t.columnCount())
            ]
            for r in range(t.rowCount())
        ]


class TestSurface(_PanelCase):
    def test_widgets_resolve(self):
        for name in (
            "header", "cmb_mode", "cmb_set", "tbl_sources", "b001", "b002",
            "cmb_sizing", "cmb_normalize", "cmb_atlas", "s_spacing", "chk_rotate",
            "chk_lightmaps", "cmb_tt_transfer", "cmb_tt_source", "cmb_tt_targets",
            "t_tt_src_uvset", "t_tt_dst_uvset", "cmb_tt_resolution",
            "chk_tt_lightmaps", "t_name", "t_output", "cmb_quality", "s_padding",
            "chk_assign", "t_affix", "cmb_shader", "b000", "footer",
        ):  # fmt: skip
            self.assertIsNotNone(getattr(self.ui, name, None), name)
        self.assertEqual(type(self.ui.tbl_sources).__name__, "TableWidget")
        self.assertEqual(type(self.slots).__name__, "TextureCombineSlots")

    def test_mode_switch_shows_each_modes_groups(self):
        self._mode("combine")
        self.assertFalse(self.ui.grp_sources.isHidden())
        self.assertTrue(self.ui.grp_transfer.isHidden())
        self.assertFalse(self.ui.grp_output.isHidden())
        self.assertTrue(self.ui.chk_assign.isHidden())
        # Blender's Combine has no shader type to rebuild on: the Material row
        # is Transfer's alone.
        self.assertTrue(self.ui.cmb_shader.isHidden())
        self.assertEqual(self.ui.b000.text(), "Combine")
        self._mode("transfer")
        self.assertTrue(self.ui.grp_sources.isHidden())
        self.assertTrue(self.ui.cmb_set.isHidden())
        self.assertFalse(self.ui.grp_transfer.isHidden())
        self.assertFalse(self.ui.grp_output.isHidden())
        self.assertFalse(self.ui.chk_assign.isHidden())
        self.assertFalse(self.ui.cmb_shader.isHidden())
        self.assertEqual(self.ui.b000.text(), "Transfer")

    def test_normalize_enables_only_for_proportional(self):
        sizing = self.ui.cmb_sizing
        sizing.setCurrentIndex(sizing.findData("density"))
        self.assertFalse(self.ui.cmb_normalize.isEnabled())
        sizing.setCurrentIndex(sizing.findData("proportional"))
        self.assertTrue(self.ui.cmb_normalize.isEnabled())

    def test_parse_scale(self):
        parse = self.slots.parse_scale
        self.assertAlmostEqual(parse("1/3"), 1 / 3)
        self.assertAlmostEqual(parse("33%"), 0.33)
        self.assertAlmostEqual(parse("0,5"), 0.5)
        self.assertIsNone(parse("abc"))
        self.assertIsNone(parse("-1"))

    def test_material_row_offers_source_target_then_overwrite(self):
        """Overwrite target last, so the index-persisted state still means
        what it did (mayatk's order, without its shader types)."""
        combo = self.ui.cmb_shader
        self.assertEqual(
            [combo.itemData(i) for i in range(combo.count())],
            ["source", "target", "in_place"],
        )
        self.assertEqual(combo.itemText(2), "Material: Overwrite target")


class TestCombineMode(_PanelCase):
    def test_table_follows_selection_and_replans_on_a_scale_edit(self):
        self._scene()
        rows = self._table()
        self.assertEqual([r[0] for r in rows], ["matA", "matB"])
        self.assertEqual(rows[0][3], "50%")
        # Type 1/3 into matB's Scale cell, the way a user edits it.
        self.ui.tbl_sources.item(1, self.slots.SCALE_COL).setText("1/3")
        rows = self._table()
        self.assertEqual(rows[1][2], "0.33")
        self.assertEqual(rows[1][3], "10%")  # (1/3)^2 of matA's area
        self.assertIn("2 mesh(es)", self.ui.footer.text())

    def test_combine_update_and_restore_from_the_picker(self):
        self._scene()
        self.ui.t_name.setText("kit")
        self.slots.b000()
        self.assertEqual(self.TC.names(), ["kit"])
        self.assertEqual(self.slots._current_set(), "kit")
        self.assertEqual(self.ui.b000.text(), "Update")
        self.assertFalse(self.ui.t_name.isEnabled())
        # The stored set re-opens with its ORIGINAL materials in the table.
        self.assertEqual([r[0] for r in self._table()], ["matA", "matB"])

        self.ui.tbl_sources.item(1, self.slots.SCALE_COL).setText("0.5")
        self.slots.b000()  # Update: re-pack with the edit
        name, changes = self.file.updated
        self.assertEqual(name, "kit")
        self.assertEqual(
            changes["rows"], [{"kind": "material", "target": "matB", "scale": 0.5}]
        )
        self.assertIn("map_size", changes)

        self.slots.restore_set()
        self.assertEqual(self.TC.names(), [])
        self.assertEqual(self.slots._current_set(), "")

    def test_a_combine_made_with_auto_spacing_reopens_as_auto(self):
        """Auto is recorded as None, and picking that combine shows Auto again --
        not the last set's count -- so an Update at another atlas size
        re-derives the gutter instead of keeping a stale one."""
        self._scene()
        for name, spacing in (("fixed", 6), ("auto", -1)):
            self.slots._refresh_sets(select="")
            self.slots._on_set()
            self.ui.s_spacing.setValue(spacing)
            self.ui.t_name.setText(name)
            self.slots.b000()
        self.assertEqual(self.file.store["fixed"]["settings"]["padding"], 6)
        self.assertIsNone(self.file.store["auto"]["settings"]["padding"])
        self.slots._refresh_sets(select="fixed")
        self.slots._on_set()
        self.assertEqual(self.ui.s_spacing.value(), 6)
        self.slots._refresh_sets(select="auto")
        self.slots._on_set()
        self.assertEqual(self.ui.s_spacing.value(), -1)

    def test_combine_reads_the_meshes_at_run_time_not_the_table_cache(self):
        """The table's read is a preview: Combine gathers the same meshes again
        when it runs, so an edit since the selection is what gets packed."""
        self._scene()
        cached = self.slots._inputs
        before = len(self.file.gathers)
        self.ui.t_name.setText("kit")
        self.slots.b000()
        self.assertEqual(len(self.file.gathers), before + 1)
        self.assertEqual(self.file.gathers[-1], ["pa", "pb"])
        self.assertIsNot(self.file.combined[1], cached)

    def test_mesh_override_row(self):
        self._scene()
        with mock.patch(
            "blendertk.uv_utils.texture_combine.texture_combine_slots."
            "CoreUtils.selected_objects",
            return_value=["pb"],
        ):
            self.slots.b001()
        rows = self._table()
        self.assertEqual(rows[-1][0].strip(), "mesh: pb")
        self.ui.tbl_sources.setCurrentCell(len(rows) - 1, 0)
        self.slots.remove_override()
        self.assertEqual(len(self._table()), 2)

    def test_island_override_row(self):
        self._scene()
        with mock.patch.object(
            type(self.slots), "_selected_faces", staticmethod(lambda: ["pb.f[0:0]"])
        ):
            self.slots.b002()
        rows = self._table()
        self.assertEqual(rows[-1][0].strip(), "islands: pb")
        self.assertEqual(self.slots._rows[-1]["target"], ["pb.f[0:0]"])


class TestTransferMode(_PanelCase):
    def setUp(self):
        super().setUp()
        self._mode("transfer")

    def test_auto_leads_both_mode_combos(self):
        for combo, data in (
            (self.ui.cmb_tt_transfer, ["auto", "textures", "uvs"]),
            (self.ui.cmb_tt_source, ["auto", "first", "last", "stored", "uvset"]),
        ):
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], data)
        self.assertEqual(self.ui.cmb_tt_transfer.itemText(2), "Transfer: UV Map")
        self.assertEqual(self.ui.cmb_tt_source.itemText(1), "Source: Active Mesh")

    def test_targets_grey_unless_first_selected(self):
        src = self.ui.cmb_tt_source
        for mode, live in (("first", True), ("auto", False), ("last", False)):
            src.setCurrentIndex(src.findData(mode))
            self.app.processEvents()
            self.assertEqual(self.ui.cmb_tt_targets.isEnabled(), live, mode)

    def test_capture_switches_the_source_to_stored(self):
        cube = NS(name="cap", type="MESH", children=[])
        with mock.patch(
            "blendertk.uv_utils.texture_combine._transfer_mode."
            "CoreUtils.selected_objects",
            return_value=[cube],
        ):
            self.slots.transfer.capture_source()
        self.assertEqual(self.ui.cmb_tt_source.currentData(), "stored")
        self.assertTrue(self.slots.transfer.clear_action.widget.isEnabled())
        self.slots.transfer.clear_source()
        self.assertFalse(self.slots.transfer.clear_action.widget.isEnabled())

    def test_overwrite_target_greys_the_affix_and_drops_it(self):
        """The target's own material keeps its name: no affix applies."""
        combo = self.ui.cmb_shader
        self.ui.chk_assign.setChecked(True)
        self.ui.t_affix.setText("_MAT")
        self.addCleanup(self.ui.t_affix.clear)
        self.addCleanup(combo.setCurrentIndex, 0)
        combo.setCurrentIndex(combo.findData("in_place"))
        self.app.processEvents()
        self.assertEqual(self.slots.assign_options(), {"assign_from": "in_place"})
        self.assertFalse(self.ui.t_affix.isEnabled())
        self.assertEqual(self.slots.material_affix(), ("", None))
        combo.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.ui.t_affix.isEnabled())

    def test_combine_mode_re_enables_the_shared_rows(self):
        """Transfer greys the shared rows with its texture pass; Combine always
        writes and assigns."""
        src = self.ui.cmb_tt_source
        src.setCurrentIndex(src.findData("first"))
        uvs = self.ui.cmb_tt_transfer
        self.addCleanup(uvs.setCurrentIndex, 0)
        uvs.setCurrentIndex(uvs.findData("uvs"))
        self.app.processEvents()
        self.assertFalse(self.ui.t_output.isEnabled())
        self._mode("combine")
        for name in ("t_output", "cmb_quality", "s_padding", "t_affix"):
            self.assertTrue(getattr(self.ui, name).isEnabled(), name)

    def test_one_mesh_pins_the_transfer_to_textures(self):
        src = self.ui.cmb_tt_source
        src.setCurrentIndex(src.findData("uvset"))
        self.app.processEvents()
        self.assertEqual(self.ui.cmb_tt_transfer.currentData(), "textures")
        self.assertFalse(self.ui.cmb_tt_transfer.isEnabled())
        self.assertFalse(self.ui.chk_tt_lightmaps.isEnabled())


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    # The harness's (ok/attempted) tally -- `unittest.main()` would parse
    # Blender's own flags under run_tests.py. A fully skipped suite reports
    # `skipped`, which tells the runner to re-run it under the workspace .venv.
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
