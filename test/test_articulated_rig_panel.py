# !/usr/bin/python
# coding=utf-8
"""Articulated Rig panel test -- the Qt half of ``test_articulated_rig``.

Needs **Qt, not bpy**: loads ``articulated_rig.ui`` and wires ``ArticulatedRigSlots``
through a real (offscreen) Qt / Switchboard / BlenderUiHandler stack, with the engine
stood in for by a small fake that keeps its plan with the real ``ptk.ArticulationPlan``,
and the panel's scene glue (``_selection`` / ``_select``, which the Blender harness's
``test_articulated_rig`` drives on a real rig) by a selection list. Mirror of mayatk's
``test_articulated_rig_panel``, acting on the widgets as a user does: Analyze filling
the table (Selection Order reading the picks as the chain); a row's type combo retyping
the plan and the built rig (the rebuild replaces the very combo whose signal ran it, so
the refresh is deferred); limits and give typed into a row, the give scrolled; a row
clicked selecting its control; the End Control checkbox on a built rig; Switch IK / FK,
Match End Control and Rest Pose; the Grab Tool; Split Off Selected, Fold Into Parent,
Set Limits From Pose, Clear Limits, Adjust Pivots, Rebuild and Remove Rig. Run under the
workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_articulated_rig_panel.py
"""

import copy
import os
import sys
import unittest
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
        from qtpy import QtCore, QtWidgets
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

import pythontk as ptk  # noqa: E402

#: The fixture's parts (mayatk's magnifier arm), root first, and what the fake
#: analysis proposes for each joint.
ARM = ["BASE", "LEG_1", "LEG_2", "LEG_3", "LEG_4", "HEAD"]
TYPES = ["universal", "hinge", "hinge", "slide", "ball"]
IDENTITY = [float(r == c) for r in range(4) for c in range(4)]

WIDGETS = (
    "header",
    "cmb_source",
    "txt_name",
    "btn_analyze",
    "tbl_joints",
    "chk_end_control",
    "btn_build",
    "btn_grab",
    "btn_ik_fk",
    "btn_rest",
    "cmb_insert_type",
    "btn_insert",
    "btn_fold",
    "btn_limits_from_pose",
    "btn_clear_limits",
    "chk_adjust",
    "btn_rebuild",
    "btn_remove",
    "txt003",
)


class _Node:
    """A scene node the panel hands around: a part (an object) or a control bone."""

    def __init__(self, name, bone=False):
        self.name = name
        self.bone = bone

    def __repr__(self):
        return f"<{self.name}>"


class _Scene:
    """The scene side: the parts, the selection, what the panel selected, the rigs,
    and every engine call the panel made."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.parts = {n: _Node(n) for n in ARM + ["ARM"]}
        self.selection = []
        self.selected = []
        self.rigs = []
        self.calls = []

    def verbs(self, verb):
        return [c[1:] for c in self.calls if c[0] == verb]


SCENE = _Scene()
#: What the panel's open called on the engine, and each time it armed the order
#: tracker -- recorded once: the process loads the panel once, every class after the
#: first gets it back from the switchboard.
OPENED, TRACKED = [], []


class _FakeRig(ptk.LoggingMixin):
    """``ArticulatedRig``'s surface the panel drives, over a plan dict the real
    ``ptk.ArticulationPlan`` edits; every call is logged on :data:`SCENE`."""

    JOINT_TYPES = ptk.ArticulationAnalysis.JOINT_TYPES
    END_KEY = ptk.ArticulationPlan.END_KEY
    #: What the next ``create`` raises (a refused build), or None.
    refuse = None

    def __init__(self, spec):
        self._spec = spec
        self._group_uuid = spec["group_uuid"]
        self._group = _Node(f"{spec['name']}_RIG")
        self._end = _Node(f"{spec['name']}_end_ctrl", bone=True)
        self._controls = {}
        self.alive = True
        self.handles = {}
        self.moved = False
        self.ik = True
        self.pose = {}

    # ---------------------------------------------------------------- lookup
    @classmethod
    def analyze(cls, nodes, root=None, ordered=False):
        SCENE.calls.append(("analyze", [n.name for n in nodes], ordered))
        return {
            "links": [[p] for p in ARM],
            "joints": [
                {
                    "link": i + 1,
                    "parent": i,
                    "type": kind,
                    "position": [0.0, 0.0, float(i)],
                    "aim": [0.0, 0.0, 1.0],
                    "normal": [1.0, 0.0, 0.0],
                    "limits": {},
                    "size": 1.0,
                    "reason": f"{kind} from the contact",
                }
                for i, kind in enumerate(TYPES)
            ],
            "unreached": [],
        }

    @classmethod
    def create(cls, links, joints=None, name=None, parent=None, end_control=True):
        SCENE.calls.append(("create", name, end_control, copy.deepcopy(joints)))
        if cls.refuse:
            raise ValueError(cls.refuse)
        links = [[getattr(m, "name", m) for m in link] for link in links]
        spec = {
            "name": name or "arm",
            "group_uuid": f"g{len(SCENE.rigs)}",
            "links": [[{"uuid": f"u_{m}", "name": m} for m in link] for link in links],
            "joints": [dict(copy.deepcopy(j), id=links[j["link"]][0]) for j in joints],
        }
        if end_control:
            spec["end"] = {"part": f"u_{links[-1][0]}", "frame": IDENTITY}
        rig = cls(spec)
        SCENE.rigs.append(rig)
        return rig

    @classmethod
    def for_node(cls, node):
        for rig in SCENE.rigs:
            if rig.alive and (
                node is rig._group or rig._uuid(node) or rig.joint_id_of(node)
            ):
                return rig
        return None

    @classmethod
    def repair_scene(cls):
        SCENE.calls.append(("repair_scene",))
        return 0

    @property
    def group(self):
        if not self.alive:
            raise RuntimeError("The rig group no longer exists.")
        return self._group

    @property
    def spec(self):
        _ = self.group
        return copy.deepcopy(self._spec)

    @property
    def name(self):
        return self._spec["name"]

    @property
    def end_control(self):
        return self._end if self._spec.get("end") else None

    @property
    def adjusting(self):
        _ = self.group
        return bool(self.handles)

    def ensure_solver(self):
        SCENE.calls.append(("ensure_solver", self.name))
        return self.end_control is not None

    def joint_ids(self):
        return [j["id"] for j in self._spec["joints"]]

    def control(self, joint_id):
        return self._controls.setdefault(
            joint_id, _Node(f"{self.name}_{joint_id}_ctrl", bone=True)
        )

    def joint_id_of(self, node):
        for joint in self._spec["joints"]:
            if node is self.control(joint["id"]):
                return joint["id"]
            names = [m["name"] for m in self._spec["links"][joint["link"]]]
            if not getattr(node, "bone", True) and node.name in names:
                return joint["id"]
        return None

    def _uuid(self, node):
        if getattr(node, "bone", True):
            return None
        return next(
            (
                m["uuid"]
                for link in self._spec["links"]
                for m in link
                if m["name"] == node.name
            ),
            None,
        )

    # ----------------------------------------------------------------- edits
    def edit_joint(self, joint_id, **fields):
        SCENE.calls.append(("edit_joint", joint_id, fields))
        joint = ptk.ArticulationPlan.joint_entry(self._spec, joint_id)
        if "type" in fields:
            ptk.ArticulationPlan.retype(joint, fields["type"])
        return self

    def set_limits(self, joint_id, channel, minimum, maximum):
        SCENE.calls.append(("set_limits", joint_id, channel, minimum, maximum))
        ptk.ArticulationPlan.set_limits(self._spec, joint_id, channel, minimum, maximum)

    def channel_value(self, joint_id, channel):
        return self.pose.get((joint_id, channel), 0.0)

    def set_limit_from_pose(self, joint_id, channel, side):
        value = self.channel_value(joint_id, channel)
        SCENE.calls.append(("set_limit_from_pose", joint_id, channel, side))
        lo, hi = ptk.ArticulationPlan.side_limits(
            self._spec, joint_id, channel, side, value
        )
        ptk.ArticulationPlan.set_limits(self._spec, joint_id, channel, lo, hi)
        return value

    def set_weight(self, joint_id, weight, channel=None):
        SCENE.calls.append(("set_weight", joint_id, weight))
        ptk.ArticulationPlan.set_weight(self._spec, joint_id, weight, channel)

    def set_end_control(self, enabled=True, part=None):
        SCENE.calls.append(("set_end_control", enabled))
        if enabled:
            self._spec["end"] = {"part": "u_HEAD", "frame": IDENTITY}
        else:
            self._spec.pop("end", None)
        return self

    def insert_joint(self, members, joint_type=None, **fields):
        SCENE.calls.append(("insert_joint", [m.name for m in members], joint_type))
        uuids = {self._uuid(m) for m in members}
        host, keep, split = ptk.ArticulationPlan.split_link(self._spec, uuids)
        self._spec["links"][host] = keep
        self._spec["links"].append(split)
        self._spec["joints"].append(
            {
                "id": split[0]["name"],
                "link": len(self._spec["links"]) - 1,
                "parent": host,
                "type": joint_type or "slide",
                "limits": {},
                "reason": "split off",
            }
        )
        return self

    def remove_joint(self, joint_id):
        SCENE.calls.append(("remove_joint", joint_id))
        self._spec["joints"] = [j for j in self._spec["joints"] if j["id"] != joint_id]
        return self

    def rebuild(self, spec=None):
        SCENE.calls.append(("rebuild", self.name))
        return self

    def teardown(self):
        SCENE.calls.append(("teardown", self.name))
        self.alive = False

    # -------------------------------------------------------------- pose / IK
    def switch_ik(self, on=None, key=None):
        if self.end_control is None:
            raise RuntimeError(f"Rig {self.name!r} has no end control.")
        self.ik = (not self.ik) if on is None else bool(on)
        SCENE.calls.append(("switch_ik", self.ik))
        return self.ik

    def match_end_control(self, key=None):
        SCENE.calls.append(("match_end_control", self.name))

    def reset_pose(self, key=None):
        SCENE.calls.append(("reset_pose", self.name))
        self.pose.clear()

    # ---------------------------------------------------------------- adjust
    def begin_adjust(self):
        SCENE.calls.append(("begin_adjust", self.name))
        self.handles = {j: _Node(f"{self.name}_{j}_pivot") for j in self.joint_ids()}
        if self.end_control is not None:
            self.handles[self.END_KEY] = _Node(f"{self.name}_end_pivot")
        return dict(self.handles)

    def adjust_handles(self):
        return dict(self.handles)

    def end_adjust(self, apply=True):
        SCENE.calls.append(("end_adjust", self.name, apply))
        moved = bool(self.handles) and self.moved
        self.handles = {}
        return moved


class _FakeGrab:
    """``ArticulatedRigGrab``: the tool's activation, refused when :attr:`refuse`."""

    refuse = None

    @classmethod
    def activate(cls):
        SCENE.calls.append(("grab",))
        if cls.refuse:
            raise RuntimeError(cls.refuse)
        return True


@unittest.skipIf(QtWidgets is None, "Qt not available (Blender headless Python)")
class _PanelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blendertk.edit_utils.selection import SelectionOrder
        from blendertk.rig_utils.articulated_rig import articulated_rig_slots as module

        cls.module = module
        Slots = module.ArticulatedRigSlots
        cls._patches = [
            mock.patch.object(module, "ArticulatedRig", _FakeRig),
            mock.patch.object(module, "ArticulatedRigGrab", _FakeGrab),
            mock.patch.object(
                Slots, "_selection", staticmethod(lambda: list(SCENE.selection))
            ),
            mock.patch.object(
                Slots,
                "_select",
                staticmethod(lambda nodes: SCENE.selected.append(nodes)),
            ),
            mock.patch.object(
                SelectionOrder,
                "enable",
                classmethod(lambda c: TRACKED.append(True)),
            ),
        ]
        for patcher in cls._patches:
            patcher.start()

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        SCENE.reset()
        handler = BlenderUiHandler(switchboard=Switchboard())
        # A process singleton: in a run that built it earlier it keeps ITS
        # switchboard, which is the one holding the UIs.
        cls.sb = handler.sb
        cls.ui = handler.get("articulated_rig")
        cls.slots = cls.ui.slots
        for _ in range(5):
            cls.app.processEvents()
        if not OPENED:
            OPENED.extend(SCENE.calls)

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls._patches):
            patcher.stop()

    def setUp(self):
        SCENE.reset()
        _FakeRig.refuse = None
        _FakeGrab.refuse = None
        self.slots.plan = None
        self.slots.rig = None
        self.slots._sync_adjust()  # clear a box a failed test left checked
        self.ui.cmb_source.setCurrentIndex(0)
        self.ui.cmb_insert_type.setCurrentIndex(0)
        self.ui.txt_name.setText("")
        self.ui.chk_end_control.setChecked(True)
        self.ui.txt003.clear()
        self.messages = []
        patcher = mock.patch.object(
            self.sb,
            "message_box",
            side_effect=lambda text, *a, **k: self.messages.append(str(text)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def settle(self):
        for _ in range(3):
            self.app.processEvents()

    def rows(self):
        table = self.ui.tbl_joints
        return [
            (table.item(r, 0).text(), table.cellWidget(r, 1).currentText())
            for r in range(table.rowCount())
        ]

    def cell(self, row, col):
        return self.ui.tbl_joints.item(row, col)

    def log(self):
        return self.ui.txt003.toPlainText()

    def select(self, *names_or_nodes):
        SCENE.selection = [
            SCENE.parts[n] if isinstance(n, str) else n for n in names_or_nodes
        ]

    def build(self, end_control=True):
        self.ui.chk_end_control.setChecked(end_control)
        self.select("ARM")
        self.ui.btn_build.click()  # analyzes first when nothing is planned
        self.settle()
        self.assertIsNotNone(self.slots.rig)
        return self.slots.rig


class TestPanelSurface(_PanelCase):
    def test_every_widget_the_slots_address_exists(self):
        for name in WIDGETS:
            self.assertTrue(hasattr(self.ui, name), name)
        headers = [
            self.ui.tbl_joints.horizontalHeaderItem(c).text()
            for c in range(self.ui.tbl_joints.columnCount())
        ]
        self.assertEqual(headers, self.slots.COLUMNS)
        self.assertEqual(type(self.slots).__name__, "ArticulatedRigSlots")
        self.assertTrue(type(self.slots).__module__.startswith("blendertk."))

    def test_the_panel_open_restores_the_end_controls_and_tracks_the_order(self):
        """Opening the panel arms what it relies on: every end control's solve
        (``repair_scene``) and the order the parts are picked in, which Blender
        keeps for no object (``SelectionOrder``) -- Selection Order reads it."""
        self.assertIn(("repair_scene",), OPENED)
        self.assertTrue(TRACKED)

    def test_the_help_card_links_the_tools_reference(self):
        """The ``?`` card is read at a glance and ends in a link to the full
        reference (mayatk's doc covers both twins). Added: 2026-10-19"""
        self.assertTrue(self.slots.HELP_URL.endswith("/docs/articulated_rig.md"))
        self.assertEqual(self.ui.header.help_url(), self.slots.HELP_URL)

    def test_limits_typed_as_text_parse(self):
        parse = self.slots.parse_limits
        self.assertEqual(parse("rz -30..95", ("rz",)), {"rz": (-30.0, 95.0)})
        self.assertEqual(parse("-..12.5", ("tx",)), {"tx": (None, 12.5)})
        self.assertEqual(
            parse("rx -10..10, rz ..45", ("rx", "rz")),
            {"rx": (-10.0, 10.0), "rz": (None, 45.0)},
        )
        self.assertEqual(
            parse("free", ("rx", "rz")), {"rx": (None, None), "rz": (None, None)}
        )
        for bad in ("rz 5", "ry -1..1", "-1..1", "rz 9..3"):
            with self.assertRaises(ValueError, msg=bad):
                parse(bad, ("rx", "rz"))


class TestAnalyzeAndBuild(_PanelCase):
    def test_analyze_fills_the_table_and_a_row_retypes_the_plan(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        self.assertEqual(
            self.rows(),
            [
                ("LEG_1", "universal"),
                ("LEG_2", "hinge"),
                ("LEG_3", "hinge"),
                ("LEG_4", "slide"),
                ("HEAD", "ball"),
            ],
        )
        self.ui.tbl_joints.cellWidget(1, 1).setCurrentText("ball")
        self.settle()
        self.assertEqual(self.slots.plan["joints"][1]["type"], "ball")
        self.assertEqual(self.rows()[1], ("LEG_2", "ball"))

    def test_selection_order_analyzes_the_picks_as_the_chain(self):
        self.select("BASE", "LEG_2", "LEG_1")
        self.ui.cmb_source.setCurrentIndex(1)  # Parts: Selection Order
        self.ui.btn_analyze.click()
        self.assertEqual(SCENE.verbs("analyze"), [(["BASE", "LEG_2", "LEG_1"], True)])
        self.ui.cmb_source.setCurrentIndex(0)
        self.ui.btn_analyze.click()
        self.assertFalse(SCENE.verbs("analyze")[-1][1])

    def test_nothing_selected_is_reported_and_analyzes_nothing(self):
        self.ui.btn_analyze.click()
        self.assertEqual(SCENE.verbs("analyze"), [])
        self.assertIn("Select the prop", self.log())

    def test_build_names_the_rig_then_a_row_retypes_the_built_joint(self):
        self.ui.txt_name.setText("desk")
        rig = self.build()
        ((name, end_control, _joints),) = SCENE.verbs("create")
        self.assertEqual((name, end_control), ("desk", True))
        self.assertEqual(rig.name, "desk")
        self.assertIsNone(self.slots.plan)
        self.assertIn("Built desk", self.log())
        # The combo's own signal rebuilds the rig: its refresh must wait.
        self.ui.tbl_joints.cellWidget(1, 1).setCurrentText("ball")
        self.settle()
        self.assertEqual(SCENE.verbs("edit_joint"), [("LEG_2", {"type": "ball"})])
        self.assertEqual(self.rows()[1], ("LEG_2", "ball"))

    def test_a_build_without_the_end_control(self):
        rig = self.build(end_control=False)
        self.assertIsNone(rig.end_control)
        self.assertFalse(SCENE.verbs("create")[0][1])

    def test_a_refused_build_is_reported_and_keeps_the_plan(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        _FakeRig.refuse = "LEG_2 is driven"
        self.ui.btn_build.click()
        self.assertIsNone(self.slots.rig)
        self.assertIsNotNone(self.slots.plan)
        self.assertTrue(any("LEG_2 is driven" in m for m in self.messages))


class TestTableEdits(_PanelCase):
    def test_limits_typed_into_a_row_bound_the_built_joint(self):
        self.build(end_control=False)
        self.cell(1, self.slots.LIMITS_COLUMN).setText("rz -20..45")
        self.settle()
        self.assertEqual(SCENE.verbs("set_limits"), [("LEG_2", "rz", -20.0, 45.0)])
        self.assertEqual(self.cell(1, self.slots.LIMITS_COLUMN).text(), "rz -20..45")

    def test_a_bad_limits_entry_is_reported_and_changes_nothing(self):
        rig = self.build(end_control=False)
        self.cell(1, self.slots.LIMITS_COLUMN).setText("tight please")
        self.settle()
        joint = next(j for j in rig.spec["joints"] if j["id"] == "LEG_2")
        self.assertEqual(joint.get("limits") or {}, {})
        self.assertEqual(SCENE.verbs("set_limits"), [])
        self.assertIn("failed", self.log())
        self.assertEqual(self.cell(1, self.slots.LIMITS_COLUMN).text(), "free")

    def test_limits_typed_into_a_plan_row_reach_the_build(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        self.cell(1, self.slots.LIMITS_COLUMN).setText("rz -30..95")
        self.settle()
        self.ui.btn_build.click()
        joints = SCENE.verbs("create")[0][2]
        self.assertEqual(joints[1]["limits"], {"rz": [-30.0, 95.0]})

    def test_give_typed_and_scrolled_sets_the_weight(self):
        self.build()
        give = self.slots.GIVE_COLUMN
        self.cell(0, give).setText("0.5")
        self.settle()
        self.assertEqual(SCENE.verbs("set_weight"), [("LEG_1", 0.5)])
        self.ui.tbl_joints.cellWheelScrolled.emit(0, give, 2, QtCore.Qt.NoModifier)
        self.settle()
        self.assertAlmostEqual(SCENE.verbs("set_weight")[-1][1], 0.7, places=6)
        self.assertEqual(self.cell(0, give).text(), "0.7")
        self.ui.tbl_joints.cellWheelScrolled.emit(0, give, -1, QtCore.Qt.ShiftModifier)
        self.settle()
        self.assertEqual(SCENE.verbs("set_weight")[-1][1], 0.0)  # never below 0

    def test_a_give_in_the_plan_reaches_the_build(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        self.cell(3, self.slots.GIVE_COLUMN).setText("0.25")
        self.settle()
        self.ui.btn_build.click()
        joints = SCENE.verbs("create")[0][2]
        self.assertEqual(joints[3]["weights"], {"tx": 0.25})

    def test_clicking_a_row_selects_its_control_or_before_the_build_its_part(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        self.ui.tbl_joints.selectRow(1)
        self.settle()
        self.assertEqual(SCENE.selected[-1], ["LEG_2"])
        rig = self.build()
        SCENE.selected.clear()
        self.ui.tbl_joints.clearSelection()
        self.ui.tbl_joints.selectRow(2)
        self.settle()
        self.assertEqual(SCENE.selected[-1], [rig.control("LEG_3")])


class TestPose(_PanelCase):
    def test_the_end_control_checkbox_drops_and_restores_it(self):
        rig = self.build()
        self.ui.chk_end_control.setChecked(False)
        self.settle()
        self.assertIsNone(rig.end_control)
        self.ui.chk_end_control.setChecked(True)
        self.settle()
        self.assertIsNotNone(rig.end_control)
        self.assertEqual(SCENE.verbs("set_end_control"), [(False,), (True,)])

    def test_before_a_build_the_checkbox_is_only_the_builds_option(self):
        self.select("ARM")
        self.ui.btn_analyze.click()
        self.ui.chk_end_control.setChecked(False)
        self.settle()
        self.assertEqual(SCENE.verbs("set_end_control"), [])

    def test_switch_ik_fk_and_rest_pose(self):
        rig = self.build()
        self.select(rig.end_control)
        self.ui.btn_ik_fk.click()
        self.assertFalse(rig.ik)
        self.assertIn("FK", self.log())
        self.ui.btn_ik_fk.click()
        self.assertTrue(rig.ik)
        self.assertEqual(SCENE.verbs("switch_ik"), [(False,), (True,)])
        self.ui.btn_rest.click()
        self.assertEqual(SCENE.verbs("reset_pose"), [(rig.name,)])

    def test_switch_ik_fk_without_an_end_control_is_refused(self):
        rig = self.build(end_control=False)
        self.select(rig.control("LEG_2"))
        self.ui.btn_ik_fk.click()
        self.assertEqual(SCENE.verbs("switch_ik"), [])
        self.assertIn("has no end control", self.log())

    def test_match_end_control_from_the_option_box(self):
        rig = self.build()
        menu = self.ui.btn_ik_fk.option_box.menu
        menu.btn_match_end.click()
        self.assertEqual(SCENE.verbs("match_end_control"), [(rig.name,)])

    def test_nothing_selected_and_no_rig_shown_is_reported(self):
        self.ui.btn_rest.click()
        self.assertEqual(SCENE.verbs("reset_pose"), [])
        self.assertIn("Select a part or control", self.log())


class TestGrabTool(_PanelCase):
    def test_the_grab_button_starts_the_tool(self):
        self.ui.btn_grab.click()
        self.assertEqual(SCENE.verbs("grab"), [()])
        self.assertIn("Grab Tool", self.log())

    def test_a_refused_grab_is_reported_not_raised(self):
        _FakeGrab.refuse = "No 3D viewport"
        self.ui.btn_grab.click()
        self.assertIn("No 3D viewport", self.log())


class TestPostRig(_PanelCase):
    def _telescope(self):
        """A rig with the inner rod riding the outer one -- the telescope left out."""
        links = [["BASE"], ["LEG_1"], ["LEG_2"], ["LEG_3", "LEG_4"], ["HEAD"]]
        joints = [
            {"link": i + 1, "parent": i, "type": kind, "limits": {}, "reason": ""}
            for i, kind in enumerate(["universal", "hinge", "hinge", "ball"])
        ]
        self.slots.rig = _FakeRig.create(links, joints)
        self.slots.refresh_table()
        return self.slots.rig

    def test_split_off_selected_adds_the_telescope(self):
        rig = self._telescope()
        self.assertEqual(len(self.rows()), 4)
        self.select("LEG_4", rig.control("LEG_2"))  # a control is not a part to split
        self.ui.cmb_insert_type.setCurrentIndex(1)  # Joint: Slide
        self.ui.btn_insert.click()
        self.assertEqual(SCENE.verbs("insert_joint"), [(["LEG_4"], "slide")])
        self.assertIn(("LEG_4", "slide"), self.rows())

    def test_split_off_with_no_part_selected_is_refused(self):
        rig = self._telescope()
        self.select(rig.control("LEG_2"))
        self.ui.btn_insert.click()
        self.assertEqual(SCENE.verbs("insert_joint"), [])
        self.assertIn("Select the parts to split off", self.log())

    def test_an_analysis_left_in_the_table_gives_way_to_the_rig_acted_on(self):
        rig = self.build()
        self.select("ARM")
        self.ui.btn_analyze.click()  # a plan for another prop fills the table
        self.select(rig.control("LEG_2"))
        self.ui.btn_rebuild.click()
        self.assertIsNone(self.slots.plan)
        self.assertIs(self.slots.rig, rig)
        self.assertEqual(SCENE.verbs("rebuild"), [(rig.name,)])
        self.assertEqual(len(self.rows()), 5)

    def test_adjust_pivots_puts_handles_out_and_rebuilds_when_cleared(self):
        rig = self.build()
        self.select(rig.control("LEG_2"))
        self.ui.chk_adjust.setChecked(True)
        self.settle()
        handles = rig.adjust_handles()
        self.assertEqual(set(handles), set(rig.joint_ids()) | {_FakeRig.END_KEY})
        self.assertEqual(SCENE.selected[-1], list(handles.values()))
        self.assertTrue(self.ui.chk_adjust.isChecked())
        rig.moved = True  # a handle moved in the viewport
        self.ui.chk_adjust.setChecked(False)
        self.settle()
        self.assertEqual(SCENE.verbs("end_adjust"), [(rig.name, True)])
        self.assertFalse(rig.adjusting)
        self.assertIn("rebuilt on the adjusted pivots", self.log())
        self.assertFalse(self.ui.chk_adjust.isChecked())

    def test_a_panel_open_never_restores_adjust_pivots(self):
        """The box shows a live state -- whether the rig in the table is
        adjusting -- not a setting: restored, an adjust that ended any way but
        by clearing the box began again on the next panel open."""
        rig = self.build()
        self.select(rig.control("LEG_2"))
        box = self.ui.chk_adjust
        self.ui.restored_widgets.discard(box)  # an open restores a widget once
        stored_on = mock.patch.object(
            self.ui.state, "load", side_effect=lambda w, *a, **k: w.setChecked(True)
        )
        with stored_on:
            self.ui.perform_restore_state(box)
        self.settle()
        self.assertFalse(rig.adjusting, "the restore began an adjust")
        self.assertFalse(box.isChecked())
        self.assertFalse(box.restore_state)

    def test_limits_from_pose_then_clear_limits(self):
        rig = self.build(end_control=False)
        rig.pose[("LEG_2", "rz")] = 55.0
        self.select(rig.control("LEG_2"), rig.control("LEG_1"))  # LEG_1 at rest
        self.ui.btn_limits_from_pose.click()
        self.assertEqual(SCENE.verbs("set_limit_from_pose"), [("LEG_2", "rz", "max")])
        self.assertEqual(self.cell(1, self.slots.LIMITS_COLUMN).text(), "rz -..55")
        self.ui.btn_clear_limits.click()
        self.assertEqual(
            SCENE.verbs("set_limits"),
            [
                ("LEG_1", "rx", None, None),
                ("LEG_1", "rz", None, None),
                ("LEG_2", "rz", None, None),
            ],
        )

    def test_fold_into_parent(self):
        rig = self.build()
        self.select(rig.control("LEG_3"))
        self.ui.btn_fold.click()
        self.assertEqual(SCENE.verbs("remove_joint"), [("LEG_3",)])
        self.assertNotIn("LEG_3", [r[0] for r in self.rows()])

    def test_remove_rig(self):
        rig = self.build()
        self.select(rig.control("LEG_2"))
        self.ui.btn_remove.click()
        self.assertEqual(SCENE.verbs("teardown"), [(rig.name,)])
        self.assertIsNone(self.slots.rig)
        self.assertEqual(self.ui.tbl_joints.rowCount(), 0)


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
