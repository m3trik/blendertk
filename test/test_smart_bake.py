# !/usr/bin/python
# coding=utf-8
"""Smart Bake panel/engine test — Blender port of mayatk's ``test_smart_bake_panel.py`` +
``test_smart_bake.py`` (kept in one file per this repo's "one test file per module" convention).

Two independent suites share this file, gated on which runtime is importable — each only makes
sense in one of the two environments this repo tests in, and neither needs the other:

``TestSmartBakePanelLoads`` needs **Qt, not bpy** — it loads ``smart_bake.ui`` + wires
``SmartBakeSlots`` through a real (offscreen) Qt/Switchboard/BlenderUiHandler stack, none of
which touch Blender (``SmartBakeSlots._refresh_session_state`` swallows the ``bpy``-import
failure from ``SmartBake.list_sessions()`` the same way ``render_opacity_slots`` guards its
``ScriptJobManager`` touch — see its docstring). Run under the workspace ``.venv``::

    .venv\\Scripts\\python.exe blendertk/test/test_smart_bake.py

``_run_data_internal_export_exclusion_checks`` needs **bpy, not Qt** — it builds a real scene
(a ``data_internal`` carrier via ``DataNodes.ensure_internal()`` plus two ordinary meshes) and
proves the carrier is absent from all three Scene Exporter object-set modes (All Scene Objects /
All Visible Objects / Selected Objects Only), replicating ``scene_exporter_slots.py``'s
``b000.objects_to_export()`` branches inline (see that function for the source of truth).

``_run_engine_round_trip_checks`` also needs **bpy, not Qt** — exercises
``SmartBake.analyze()``/``get_time_range()``/``bake()``/``restore()`` directly (not through
``TaskManager``) against a plain ``COPY_LOCATION``-constrained cube: the object holds its baked
value exactly even when the constraint's target is moved with no new keyframe (proves the
constraint was actually muted, not merely flagged), and ``SmartBake.restore()`` reverses
everything — action swapped back, constraint unmuted, live constraint-following behavior returns
exactly. Also proves ``analyze()`` correctly flags/omits ``requires_bake`` for a live constraint /
a plain object / an already-muted constraint, and that ``get_time_range()`` auto-detects the
driving constraint target's own keyframe range rather than falling back to the scene playback
range.

``_run_task_manager_wiring_checks`` also needs **bpy, not Qt** — it drives
``env_utils.scene_exporter.task_manager.TaskManager``'s ``smart_bake``/``optimize_keys``/
``tie_all_keyframes``/``snap_keys_to_frame``/``set_bake_animation_range`` (+ revert) task methods
and its ``check_untied_keyframes``/``check_floating_point_keys`` check methods directly against a
constraint-driven object and a hand-built fractional-key rig, then drives the full
``TaskFactory.run_tasks`` dispatch to prove ``_execute_tasks_and_checks``'s
``self._optimize_keys_enabled = bool(tasks_only.get("optimize_keys", False))`` line actually
reaches ``smart_bake()`` *before* it runs (``TASK_ORDER`` puts ``smart_bake`` first) — captured
via a plain ``logging.Handler`` on ``TaskManager.logger`` rather than mocking, since the forwarded
flag only ever surfaces as an informational log line (``TaskManager`` has no ``optimize_keys``
constructor knob to assert against directly, and ``SmartBake`` itself never consumes the flag).

``_run_blend_shape_driver_restore_checks`` also needs **bpy, not Qt** — it proves the shape-key
driven-weight bake+restore round trip (fact-set item 7): a ``SCRIPTED``/``SINGLE_PROP`` driver
survives ``bake_blend_shapes`` removing it, because ``SmartBake.restore()`` rebuilds an equivalent
driver (same expression, same variable target) from the snapshot ``bake_session`` took before the
bake, and the rebuilt driver genuinely re-evaluates on a live target change afterward (not a stale
baked value). It also proves the documented rebuild-scope limit: an ``AVERAGE``-type driver (an
otherwise-``SINGLE_PROP``-variable driver whose *type* alone disqualifies it — see
``bake_session.snapshot_blend_shape_driver``'s ``_REBUILDABLE_DRIVER_TYPES`` gate) is skipped with
a warning in ``RestoreResult.warnings`` instead of crashing the restore, and does not block the
other (rebuildable) driver in the SAME session from restoring cleanly.

``_run_driver_bake_restore_regression_checks``/``_run_ik_bake_restore_regression_checks`` also need **bpy, not Qt** -- they prove two concrete regressions: a SCRIPTED driver's baked value holds (does not snap to the live driver's result) once the scene frame moves outside the bake range, then re-evaluates live again exactly after ``SmartBake.restore()``; and a 2-bone IK chain (IK-type constraint on a pose bone) bakes/mutes/restores correctly through the SAME general constraint-mute path as any other constraint, with no dedicated IK subsystem (confirming ``_smart_bake.py``'s divergence-notes item 11 in practice).

``_run_preserve_outside_and_optimize_checks`` also needs **bpy, not Qt** -- proves the two REAL
engine gaps this port pass closed: ``preserve_outside_keys=True`` (the default) copies hand-keyed
frames OUTSIDE the detected bake range onto the fresh baked action with their original values
intact, while ``False`` drops them, since ``bake_keys`` always produces a brand-new Action
containing only the just-baked range (see ``_smart_bake.py``'s ``_copy_outside_range_keys``);
and ``optimize_keys=True`` actually invokes ``AnimUtils.optimize_keys()`` on the baked object(s),
collapsing 8 static freshly-baked transform curves down to the 1 that truly varies (mirroring
the same 9->1 result ``_run_task_manager_wiring_checks`` already proves for
``TaskManager.optimize_keys()``) and records it in ``BakeResult.optimized``, while ``False``
(the default) leaves all 9 untouched.

``_run_backup_mode_checks`` also needs **bpy, not Qt** -- proves ``cmb_backup``'s 3-way mapping
onto the engine's own (pre-existing) ``backup_file`` parameter: Auto (``None``) backs up only a
``delete_sources`` bake, Always (``True``) always does, Never (``False``) never does. Unlike
every other check in this file, ``SmartBake._save_backup`` no-ops on an unsaved scene, so this
one saves a real ``.blend`` under ``test/temp_tests/`` and cleans up after itself.

When launched by the Blender harness (``--background --factory-startup``, which ships no Qt),
the Qt import fails, ``TestSmartBakePanelLoads`` SKIPS, and the bpy-driven checks run instead,
printing the ``===RESULT: PASS/FAIL===`` sentinel ``Run-Tests.ps1`` greps for. When run directly
under the workspace ``.venv`` (Qt present, no ``bpy``), the Qt suite runs via ``unittest`` and
the bpy-driven checks are skipped (nothing to assert without Blender running) — either way this
file never needs both runtimes at once. Engine round-trip coverage (bake analyze/mute/restore
against real ``bpy`` state) is exercised separately during this port's live-Blender verification
passes (see ``_smart_bake.py`` / ``bake_session.py`` module docstrings for what was proven).
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
    from qtpy import QtWidgets
except Exception:  # pragma: no cover - Qt not installed / Blender's headless Python
    QtWidgets = None

if QtWidgets is not None:
    # Keep the Qt half off the real QSettings store (uitk/test/conftest.py owns
    # the shim): loading the panel through Switchboard otherwise reads AND
    # writes the developer's live ``uitk\shared`` state.
    import importlib.util

    _conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
    if os.path.isfile(_conftest):
        _spec = importlib.util.spec_from_file_location("_uitk_conftest", _conftest)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)  # activates the QSettings sandbox
    else:  # refuse to run panel loads against the live store
        QtWidgets = None


@unittest.skipUnless(
    QtWidgets is not None,
    "Qt test — run under the workspace .venv (PySide6), not the Blender harness.",
)
class TestSmartBakePanelLoads(unittest.TestCase):
    """The panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        cls.sb = Switchboard()
        cls.handler = BlenderUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("smart_bake")
        # Flush the QTimer.singleShot(0, self._initialize_ui) deferred in __init__ — Blender's
        # own event loop does this immediately in real use.
        for _ in range(5):
            cls.app.processEvents()

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "smart_bake UI failed to load")

    def test_resolves_to_slots_class_not_bare_engine(self):
        """SmartBake (the engine) is also a registered blendertk class — confirm the Slots
        suffix disambiguates it during discovery."""
        self.assertEqual(type(self.ui.slots).__name__, "SmartBakeSlots")

    def test_all_referenced_widgets_exist(self):
        expected = [
            "cmb_scope",
            "options_group",
            "spn_sample_by",
            "chk_preserve_outside",
            "chk_optimize",
            "chk_bake_blend_shapes",
            "safety_group",
            "chk_use_override",
            "chk_delete_sources",
            "cmb_backup",
            "b000",
            "b001",
            "output_grp",
            "txt000",
            "footer",
            "header",
        ]
        missing = [w for w in expected if not hasattr(self.ui, w)]
        self.assertEqual(missing, [])

    def test_scope_combo_items(self):
        items = [self.ui.cmb_scope.itemText(i) for i in range(self.ui.cmb_scope.count())]
        self.assertEqual(items, ["Auto (Whole Scene)", "Selected"])

    def test_backup_combo_items(self):
        items = [self.ui.cmb_backup.itemText(i) for i in range(self.ui.cmb_backup.count())]
        self.assertEqual(items, ["Auto", "Always", "Never"])

    def test_preserve_outside_defaults_checked(self):
        self.assertTrue(self.ui.chk_preserve_outside.isChecked())

    def test_optimize_defaults_unchecked(self):
        self.assertFalse(self.ui.chk_optimize.isChecked())

    def test_backup_value_mapping(self):
        # Index is the source of truth (see SmartBakeSlots._BACKUP_MODES): Auto -> None,
        # Always -> True, Never -> False, regardless of label text.
        expected = [None, True, False]
        slots = self.ui.slots
        actual = []
        for i in range(self.ui.cmb_backup.count()):
            self.ui.cmb_backup.setCurrentIndex(i)
            actual.append(slots._backup_value())
        self.assertEqual(actual, expected)

    def test_use_override_defaults_checked(self):
        self.assertTrue(self.ui.chk_use_override.isChecked())

    def test_delete_sources_defaults_unchecked(self):
        self.assertFalse(self.ui.chk_delete_sources.isChecked())

    def test_bake_blend_shapes_defaults_checked(self):
        self.assertTrue(self.ui.chk_bake_blend_shapes.isChecked())

    def test_use_override_disabled_when_delete_sources_checked(self):
        self.ui.chk_delete_sources.setChecked(True)
        self.assertFalse(self.ui.chk_use_override.isEnabled())
        self.ui.chk_delete_sources.setChecked(False)  # restore default

    def test_use_override_enabled_when_delete_sources_unchecked(self):
        self.ui.chk_delete_sources.setChecked(False)
        self.assertTrue(self.ui.chk_use_override.isEnabled())

    def test_unbake_disabled_with_no_pending_sessions(self):
        # No running Blender under this Qt-only harness — SmartBake.list_sessions() can't
        # touch bpy, so SmartBakeSlots._refresh_session_state() falls back to an empty list.
        self.assertFalse(self.ui.b001.isEnabled())


def _run_data_internal_export_exclusion_checks():
    """bpy-driven checks (only meaningful under the Blender test harness) proving the
    ``data_internal`` carrier never appears in any of the three Scene Exporter object-set
    modes. Returns a list of ``"OK ..."``/``"FAIL ..."`` lines, mirroring the ``check()``
    sentinel convention used by the other headless suites in this directory (e.g.
    ``test_curtain.py``, ``test_render_opacity.py``).
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy
        import bmesh
        import blendertk as btk
        from blendertk.node_utils.data_nodes import DataNodes

        def reset():
            if (
                bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"
            ):
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o, do_unlink=True)

        def cube(name):
            me = bpy.data.meshes.new(f"{name}_mesh")
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(name, me)
            bpy.context.collection.objects.link(o)
            return o

        reset()
        mesh_a = cube("MeshA")
        mesh_b = cube("MeshB")
        internal_obj = DataNodes.ensure_internal()
        check(
            "data_internal carrier created",
            internal_obj is not None and internal_obj.name == DataNodes.INTERNAL,
        )
        # A fresh headless link doesn't retroactively refresh view_layer.objects — reading it
        # immediately after linking yields stale None entries (confirmed live); get_visible_
        # geometry()'s default pool walks exactly that collection, so force the refresh first.
        bpy.context.view_layer.update()

        # ---- "All Scene Objects" (scene_exporter_slots.objects_to_export, export_mode == "all")
        all_names = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
        check(
            "All Scene Objects excludes data_internal",
            DataNodes.INTERNAL not in all_names,
            str(all_names),
        )
        check(
            "All Scene Objects includes both meshes",
            {"MeshA", "MeshB"} <= all_names,
            str(all_names),
        )

        # ---- "All Visible Objects" (export_mode == "visible", the default/fallback)
        visible_names = {o.name for o in btk.get_visible_geometry()}
        check(
            "All Visible Objects excludes data_internal",
            DataNodes.INTERNAL not in visible_names,
            str(visible_names),
        )
        check(
            "All Visible Objects includes both meshes",
            {"MeshA", "MeshB"} <= visible_names,
            str(visible_names),
        )

        # ---- "Selected Objects Only" (export_mode == "selected") — realistic usage, the
        # carrier is not part of the user's selection (it's an internal-only bookkeeping node).
        bpy.ops.object.select_all(action="DESELECT")
        mesh_a.select_set(True)
        mesh_b.select_set(True)
        internal_obj.select_set(False)
        selected_names = {o.name for o in btk.selected_objects()}
        check(
            "Selected Objects Only excludes data_internal when not selected",
            DataNodes.INTERNAL not in selected_names,
            str(selected_names),
        )
        check(
            "Selected Objects Only includes both selected meshes",
            {"MeshA", "MeshB"} <= selected_names,
            str(selected_names),
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("data_internal export-exclusion harness raised", False, repr(e))

    return lines



def _reset_smart_bake_regression_scene():
    """Clear objects/actions/armatures between the bpy-driven regression checks below -- each
    check builds its own scene from scratch."""
    import bpy

    if (
        bpy.context.view_layer.objects.active
        and bpy.context.view_layer.objects.active.mode != "OBJECT"
    ):
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a, do_unlink=True)
    for arm in list(bpy.data.armatures):
        bpy.data.armatures.remove(arm, do_unlink=True)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 50
    scene.frame_set(1)


def _run_engine_round_trip_checks():
    """bpy-driven checks (only meaningful under the Blender test harness) exercising
    ``SmartBake.analyze()``/``get_time_range()``/``bake()``/``restore()`` directly against a
    plain ``COPY_LOCATION``-constrained cube. Returns ``"OK ..."``/``"FAIL ..."`` lines (see
    ``_run_data_internal_export_exclusion_checks``'s docstring for the sentinel convention).
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy
        import bmesh
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        def reset():
            if (
                bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"
            ):
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            for a in list(bpy.data.actions):
                bpy.data.actions.remove(a)

        def cube(name="Box"):
            me = bpy.data.meshes.new(f"{name}_mesh")
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=2.0)
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(name, me)
            bpy.context.collection.objects.link(o)
            return o

        def spawn_empty(name="Empty", loc=(0.0, 0.0, 0.0)):
            o = bpy.data.objects.new(name, None)
            o.location = loc
            bpy.context.collection.objects.link(o)
            return o

        def key_loc(obj, frame, loc):
            obj.location = loc
            obj.keyframe_insert(data_path="location", frame=frame)

        def world_pos(obj):
            bpy.context.view_layer.update()
            deps = bpy.context.evaluated_depsgraph_get()
            eo = obj.evaluated_get(deps)
            return tuple(round(v, 4) for v in eo.matrix_world.translation)

        def approx3(a, b, tol=1e-3):
            return all(abs(x - y) <= tol for x, y in zip(a, b))

        # ==================== analyze(): constraint detection ====================
        reset()
        target = spawn_empty("Target", (0.0, 0.0, 0.0))
        key_loc(target, 5, (0.0, 0.0, 0.0))
        key_loc(target, 30, (10.0, 0.0, 0.0))

        driven = cube("Driven")
        c = driven.constraints.new("COPY_LOCATION")
        c.name = "CopyLoc"
        c.target = target

        plain = cube("Plain")

        muted_target = spawn_empty("MutedTarget", (0.0, 0.0, 0.0))
        key_loc(muted_target, 5, (0.0, 0.0, 0.0))
        key_loc(muted_target, 30, (5.0, 0.0, 0.0))
        muted_obj = cube("MutedDriven")
        mc = muted_obj.constraints.new("COPY_LOCATION")
        mc.target = muted_target
        mc.mute = True

        baker = SmartBake(objects=[driven, plain, muted_obj], bake_blend_shapes=False)
        analysis = baker.analyze()

        check(
            "analyze() flags a live-constrained object requires_bake True",
            "Driven" in analysis and analysis["Driven"].requires_bake,
            f"{analysis.get('Driven')}",
        )
        check(
            "analyze() gives a plain (unconstrained) object no entry -> requires_bake False",
            "Plain" not in analysis,
        )
        check(
            "analyze() ignores an already-muted constraint -> requires_bake False",
            "MutedDriven" not in analysis,
        )
        check(
            "analyze() records the live constraint's own name",
            analysis["Driven"].driven_sources.get("constraint") == [c.name],
            f"{analysis['Driven'].driven_sources}",
        )

        # ==================== get_time_range(): auto-detect vs scene fallback ====================
        scene = bpy.context.scene
        detected = baker.get_time_range(analysis)
        check(
            "get_time_range() auto-detects the constraint target's own key range (5, 30)",
            detected == (5, 30),
            f"{detected}",
        )

        empty_baker = SmartBake(objects=[plain], bake_blend_shapes=False)
        empty_analysis = empty_baker.analyze()
        fallback = empty_baker.get_time_range(empty_analysis)
        check(
            "get_time_range() falls back to the scene playback range with no driven sources",
            fallback == (int(scene.frame_start), int(scene.frame_end)),
            f"{fallback} vs scene ({scene.frame_start}, {scene.frame_end})",
        )

        # ==================== bake() + restore(): full round trip ====================
        reset()
        scene = bpy.context.scene
        rt_target = spawn_empty("RTTarget", (0.0, 0.0, 0.0))
        key_loc(rt_target, 1, (0.0, 0.0, 0.0))
        key_loc(rt_target, 20, (10.0, 0.0, 0.0))

        rt = cube("RT")
        rt_constraint = rt.constraints.new("COPY_LOCATION")
        rt_constraint.name = "CopyLoc"
        rt_constraint.target = rt_target

        check(
            "RT has no pre-bake action (purely constraint-driven)",
            rt.animation_data is None or rt.animation_data.action is None,
        )

        result = SmartBake.run(objects=[rt], bake_blend_shapes=False)
        check("bake() reports RT as baked", "RT" in result.baked, f"{result.baked}")
        check("BakeResult.success is True", result.success is True)
        check(
            "bake() created a fresh action on RT",
            rt.animation_data is not None and rt.animation_data.action is not None,
        )
        baked_action_name = rt.animation_data.action.name
        check("bake() muted the CopyLoc constraint", rt_constraint.mute is True)
        check(
            "BakeResult.muted_constraints recorded CopyLoc",
            result.muted_constraints == ["CopyLoc"],
            f"{result.muted_constraints}",
        )
        check(
            "bake() recorded a restorable session id",
            bool(result.session_id),
            f"{result.session_id}",
        )

        scene.frame_set(10)
        baked_pos = world_pos(rt)

        # Move the constraint's target with NO new keyframe -- a live constraint would drag RT
        # along with it; a muted one must not.
        rt_target.location = (999.0, 999.0, 999.0)
        held_pos = world_pos(rt)
        check(
            "baked value HOLDS when the muted constraint's target moves with no new key",
            approx3(held_pos, baked_pos),
            f"held={held_pos} baked={baked_pos}",
        )

        restore_result = SmartBake.restore(result.session_id)
        check(
            "restore() succeeds with no warnings",
            restore_result.success and restore_result.warnings == [],
            f"{restore_result.warnings}",
        )
        check(
            "restore() reassigns RT's original (None) action",
            rt.animation_data is None or rt.animation_data.action is None,
        )
        check(
            "restore() deletes the baked action datablock",
            baked_action_name not in bpy.data.actions,
        )
        check("restore() unmutes the CopyLoc constraint", rt_constraint.mute is False)

        live_pos = world_pos(rt)
        check(
            "after restore, RT again follows the (already-moved) target LIVE",
            approx3(live_pos, (999.0, 999.0, 999.0)),
            f"{live_pos}",
        )

        # A second, independent move proves this is live tracking, not a coincidental value.
        rt_target.location = (42.0, -7.0, 3.0)
        live_pos2 = world_pos(rt)
        check(
            "RT continues to live-follow the target after a second move (behavior returns EXACTLY)",
            approx3(live_pos2, (42.0, -7.0, 3.0)),
            f"{live_pos2}",
        )

        check(
            "no bake sessions remain after restore",
            SmartBake.list_sessions() == [],
            f"{SmartBake.list_sessions()}",
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("smart_bake engine round-trip harness raised", False, repr(e))

    return lines


def _run_driver_bake_restore_regression_checks():
    """Regression: a SCRIPTED driver forwarding another object's animated value onto
    ``location.x`` must (1) survive a ``SmartBake`` bake as a *held* value once muted -- even when
    the scene frame moves outside the bake range to a point where the live driver expression
    would evaluate completely differently -- and (2) re-evaluate live again, exactly, once
    ``SmartBake.restore()`` unmutes it. Returns ``"OK ..."``/``"FAIL ..."`` lines (see
    ``_run_data_internal_export_exclusion_checks``'s docstring for the sentinel convention).
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        _reset_smart_bake_regression_scene()
        scene = bpy.context.scene

        # A driven value with a distinctly different key OUTSIDE the intended bake range, so a
        # live-vs-baked divergence at that frame is unambiguous.
        source = bpy.data.objects.new("DriverSource", None)
        bpy.context.collection.objects.link(source)
        for f, v in [(1, 0.0), (10, 5.0), (20, 10.0), (30, 999.0)]:
            source.location.x = v
            source.keyframe_insert("location", index=0, frame=f)

        target = bpy.data.objects.new("DriverTarget", None)
        bpy.context.collection.objects.link(target)
        fcurve = target.driver_add("location", 0)
        drv = fcurve.driver
        drv.type = "SCRIPTED"
        drv.expression = "src"
        var = drv.variables.new()
        var.name = "src"
        var.type = "SINGLE_PROP"
        var.targets[0].id_type = "OBJECT"
        var.targets[0].id = source
        var.targets[0].data_path = "location.x"

        scene.frame_set(30)
        bpy.context.view_layer.update()
        check(
            "sanity: driver forwards source's frame-30 value live, pre-bake",
            abs(target.location.x - 999.0) < 1e-3,
            f"{target.location.x}",
        )

        scene.frame_set(10)
        bpy.context.view_layer.update()

        baker = SmartBake(objects=[target], sample_by=1, use_override=True)
        analysis = baker.analyze()
        check(
            "driver detected on target",
            "DriverTarget" in analysis and "driver" in analysis["DriverTarget"].driven_sources,
            f"{analysis}",
        )

        result = baker.bake(analysis, time_range=(1, 20))
        check("bake reports success", result.success, f"{result}")
        check("session id recorded", result.session_id is not None, f"{result.session_id}")
        check("driver fcurve muted after bake", fcurve.mute is True, f"{fcurve.mute}")

        scene.frame_set(20)
        bpy.context.view_layer.update()
        check(
            "baked value at the last baked frame matches the source's key there (~10.0)",
            abs(target.location.x - 10.0) < 0.5,
            f"{target.location.x}",
        )

        scene.frame_set(30)  # outside the (1, 20) bake range
        bpy.context.view_layer.update()
        baked_at_30 = target.location.x
        check(
            "regression: baked value at frame 30 does NOT jump to the live driver's 999.0",
            abs(baked_at_30 - 999.0) > 1.0,
            f"baked_at_30={baked_at_30}",
        )

        restore_result = SmartBake.restore(result.session_id)
        check("restore reports success", restore_result.success, f"{restore_result}")
        check(
            "restore reports no warnings",
            restore_result.warnings == [],
            f"{restore_result.warnings}",
        )
        check("driver unmuted back to its prior (False) state", fcurve.mute is False, f"{fcurve.mute}")

        scene.frame_set(30)
        bpy.context.view_layer.update()
        check(
            "driver re-evaluates live again exactly after restore (999.0 at frame 30)",
            abs(target.location.x - 999.0) < 1e-3,
            f"{target.location.x}",
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("driver bake+restore regression harness raised", False, repr(e))

    return lines


def _run_ik_bake_restore_regression_checks():
    """Regression: a 2-bone IK chain (IK-type constraint on the tip bone, targeting an animated
    empty) must bake and restore correctly through the SAME general constraint-mute path used for
    any other constraint -- confirming ``_smart_bake.py``'s divergence-notes claim (item 11: no
    dedicated IK subsystem needed) actually holds in practice, not just in theory. Returns
    ``"OK ..."``/``"FAIL ..."`` lines.
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        _reset_smart_bake_regression_scene()
        scene = bpy.context.scene

        arm_data = bpy.data.armatures.new("Rig")
        arm_obj = bpy.data.objects.new("Rig", arm_data)
        bpy.context.collection.objects.link(arm_obj)
        bpy.context.view_layer.objects.active = arm_obj

        bpy.ops.object.mode_set(mode="EDIT")
        eb = arm_data.edit_bones
        b1 = eb.new("Bone1")
        b1.head = (0, 0, 0)
        b1.tail = (0, 0, 1)
        b2 = eb.new("Bone2")
        b2.head = (0, 0, 1)
        b2.tail = (0, 0, 2)
        b2.parent = b1
        b2.use_connect = True
        bpy.ops.object.mode_set(mode="OBJECT")

        ik_target = bpy.data.objects.new("IKTarget", None)
        bpy.context.collection.objects.link(ik_target)
        for f, loc in [(1, (0.3, 0, 1.8)), (10, (0.6, 0, 1.5)), (20, (0.2, 0, 1.9))]:
            ik_target.location = loc
            ik_target.keyframe_insert("location", frame=f)

        pbone = arm_obj.pose.bones["Bone2"]
        con = pbone.constraints.new("IK")
        con.target = ik_target
        con.chain_count = 2

        def tip_world():
            bpy.context.view_layer.update()
            return tuple(arm_obj.matrix_world @ pbone.tail)

        scene.frame_set(10)
        pre_bake_tip_10 = tip_world()

        baker = SmartBake(objects=[arm_obj], sample_by=1, use_override=True)
        analysis = baker.analyze()
        key = "Rig:Bone2"
        check(
            "IK constraint bucketed under 'ik', not 'constraint' (no separate IK subsystem)",
            key in analysis
            and "ik" in analysis[key].driven_sources
            and "constraint" not in analysis[key].driven_sources,
            f"{analysis}",
        )

        result = baker.bake(analysis, time_range=(1, 20))
        check("IK bake reports success", result.success, f"{result}")
        check(
            "IK constraint muted after bake (plain constraint-mute path)",
            con.mute is True,
            f"{con.mute}",
        )

        scene.frame_set(10)
        baked_tip_10 = tip_world()
        check(
            "baked pose at frame 10 matches the pre-bake live IK-solved pose",
            all(abs(a - b) < 1e-3 for a, b in zip(pre_bake_tip_10, baked_tip_10)),
            f"pre={pre_bake_tip_10} baked={baked_tip_10}",
        )

        # Move the IK target far away and re-key it -- since the constraint is muted, the tip
        # must hold its baked pose rather than re-solving toward the new target.
        ik_target.location = (50, 50, 50)
        ik_target.keyframe_insert("location", frame=10)
        moved_tip_10 = tip_world()
        check(
            "moving the IK target while muted does not perturb the baked pose",
            all(abs(a - b) < 1e-3 for a, b in zip(baked_tip_10, moved_tip_10)),
            f"baked={baked_tip_10} after_move={moved_tip_10}",
        )

        restore_result = SmartBake.restore(result.session_id)
        check("IK restore reports success", restore_result.success, f"{restore_result}")
        check(
            "IK restore reports no warnings",
            restore_result.warnings == [],
            f"{restore_result.warnings}",
        )
        check("IK constraint unmuted after restore", con.mute is False, f"{con.mute}")

        live_tip_after_restore = tip_world()
        check(
            "live IK resumes and tracks the moved target (diverges from the stale baked pose)",
            any(abs(a - b) > 0.5 for a, b in zip(baked_tip_10, live_tip_after_restore)),
            f"baked={baked_tip_10} live_after_restore={live_tip_after_restore}",
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("IK bake+restore regression harness raised", False, repr(e))

    return lines


def _run_preserve_outside_and_optimize_checks():
    """bpy-driven checks proving the two REAL engine gaps closed by this port pass:
    ``SmartBake(preserve_outside_keys=...)`` and ``SmartBake(optimize_keys=...)``.

    ``preserve_outside_keys``: a COPY_LOCATION-constrained cube also carries hand-keyed
    ``rotation_euler.z`` keys at frames 60/80 -- well outside the (5, 10) range the constraint
    drives/gets baked over. ``bake_keys`` (``nla.bake`` with ``use_current_action=False``)
    confirmed live (see ``_smart_bake.py``'s ``_copy_outside_range_keys`` docstring) always
    produces a brand-new Action containing ONLY the baked range, so with the default
    ``preserve_outside_keys=True`` those two hand-keyed frames must survive (copied onto the new
    baked action's matching channel) with their ORIGINAL values intact; with
    ``preserve_outside_keys=False`` they must NOT appear at all -- the new action holds only the
    freshly baked (5, 10) range.

    ``optimize_keys``: a COPY_LOCATION-constrained cube whose target only varies on
    ``location.x`` (the other 8 baked transform channels are static) proves
    ``optimize_keys=False`` (default) leaves all 9 freshly-baked curves untouched, while
    ``optimize_keys=True`` actually invokes ``AnimUtils.optimize_keys()`` on the baked object and
    collapses the 8 static ones away (down to the 1 that truly varies) -- mirroring the exact
    9->1 result already proven for ``TaskManager.optimize_keys()`` in
    ``_run_task_manager_wiring_checks`` -- and records the object in
    ``BakeResult.optimized``. Returns ``"OK ..."``/``"FAIL ..."`` lines, same convention as
    ``_run_data_internal_export_exclusion_checks``.
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy
        import bmesh

        from blendertk.anim_utils._anim_utils import get_fcurves
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        def cube(name):
            me = bpy.data.meshes.new(f"{name}_mesh")
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(name, me)
            bpy.context.collection.objects.link(o)
            return o

        def build_driven_with_outside_keys(name):
            """A COPY_LOCATION-constrained cube (target keyed 5..10) that ALSO carries a
            hand-keyed rotation OUTSIDE that range, at frames 60/80."""
            target = bpy.data.objects.new(f"{name}Target", None)
            bpy.context.collection.objects.link(target)
            target.location = (0.0, 0.0, 0.0)
            target.keyframe_insert("location", frame=5)
            target.location = (10.0, 0.0, 0.0)
            target.keyframe_insert("location", frame=10)

            driven = cube(name)
            c = driven.constraints.new("COPY_LOCATION")
            c.name = "CopyLoc"
            c.target = target

            driven.rotation_euler = (0.0, 0.0, 0.1)
            driven.keyframe_insert("rotation_euler", frame=60, index=2)
            driven.rotation_euler = (0.0, 0.0, 0.5)
            driven.keyframe_insert("rotation_euler", frame=80, index=2)
            return driven

        # ==================== preserve_outside_keys=True (the default) ====================
        _reset_smart_bake_regression_scene()
        driven_true = build_driven_with_outside_keys("PreserveTrue")
        baker_true = SmartBake(objects=[driven_true], bake_blend_shapes=False)
        check(
            "preserve_outside_keys defaults to True",
            baker_true.preserve_outside_keys is True,
        )
        result_true = baker_true.bake(baker_true.analyze(), time_range=(5, 10))
        check("preserve-True bake reports success", result_true.success, f"{result_true}")

        rot_fc_true = next(
            fc
            for fc in get_fcurves([driven_true])
            if fc.data_path == "rotation_euler" and fc.array_index == 2
        )
        frames_true = sorted(k.co.x for k in rot_fc_true.keyframe_points)
        check(
            "preserve_outside_keys=True keeps the hand-keyed frames 60 and 80",
            60.0 in frames_true and 80.0 in frames_true,
            f"{frames_true}",
        )
        check(
            "preserve_outside_keys=True preserves the ORIGINAL values at those frames",
            abs(rot_fc_true.evaluate(60) - 0.1) < 1e-3
            and abs(rot_fc_true.evaluate(80) - 0.5) < 1e-3,
            f"60->{rot_fc_true.evaluate(60)} 80->{rot_fc_true.evaluate(80)}",
        )
        if result_true.session_id:
            SmartBake.restore(result_true.session_id)

        # ==================== preserve_outside_keys=False ====================
        _reset_smart_bake_regression_scene()
        driven_false = build_driven_with_outside_keys("PreserveFalse")
        baker_false = SmartBake(
            objects=[driven_false], bake_blend_shapes=False, preserve_outside_keys=False
        )
        result_false = baker_false.bake(baker_false.analyze(), time_range=(5, 10))
        check("preserve-False bake reports success", result_false.success, f"{result_false}")

        rot_fc_false = next(
            fc
            for fc in get_fcurves([driven_false])
            if fc.data_path == "rotation_euler" and fc.array_index == 2
        )
        frames_false = sorted(k.co.x for k in rot_fc_false.keyframe_points)
        check(
            "preserve_outside_keys=False drops the hand-keyed frames 60 and 80",
            60.0 not in frames_false and 80.0 not in frames_false,
            f"{frames_false}",
        )
        check(
            "preserve_outside_keys=False leaves ONLY the freshly baked (5, 10) range",
            bool(frames_false) and all(5.0 <= f <= 10.0 for f in frames_false),
            f"{frames_false}",
        )
        if result_false.session_id:
            SmartBake.restore(result_false.session_id)

        # ========= preserve_outside_keys: exact-boundary frames (off-by-one guard) =========
        # A hand-key sitting EXACTLY at the bake range's start/end frame must NOT be duplicated
        # (the freshly baked curve already carries a correctly-evaluated key at that same exact
        # frame — inserting the original point too would either overwrite it or create ambiguity)
        # while a hand-key one frame OUTSIDE either boundary must still survive untouched.
        _reset_smart_bake_regression_scene()
        target_b = bpy.data.objects.new("BoundaryTarget", None)
        bpy.context.collection.objects.link(target_b)
        target_b.location = (0.0, 0.0, 0.0)
        target_b.keyframe_insert("location", frame=5)
        target_b.location = (10.0, 0.0, 0.0)
        target_b.keyframe_insert("location", frame=10)

        driven_b = cube("Boundary")
        cb = driven_b.constraints.new("COPY_LOCATION")
        cb.name = "CopyLoc"
        cb.target = target_b
        for frame, val in ((4, 0.11), (5, 0.22), (10, 0.33), (11, 0.44)):
            driven_b.rotation_euler = (0.0, 0.0, val)
            driven_b.keyframe_insert("rotation_euler", frame=frame, index=2)

        baker_b = SmartBake(objects=[driven_b], bake_blend_shapes=False)
        result_b = baker_b.bake(baker_b.analyze(), time_range=(5, 10))
        check("boundary bake reports success", result_b.success, f"{result_b}")

        rot_fc_b = next(
            fc
            for fc in get_fcurves([driven_b])
            if fc.data_path == "rotation_euler" and fc.array_index == 2
        )
        pts_b = sorted((k.co.x, k.co.y) for k in rot_fc_b.keyframe_points)
        counts = {x: sum(1 for f, _v in pts_b if f == x) for x in (5.0, 10.0)}
        check(
            "no duplicate keyframe_point is created exactly at the start/end boundary",
            counts[5.0] == 1 and counts[10.0] == 1,
            f"{pts_b}",
        )
        val4 = next((v for f, v in pts_b if f == 4.0), None)
        val11 = next((v for f, v in pts_b if f == 11.0), None)
        check(
            "keys one frame OUTSIDE either boundary (4 and 11) survive with original values",
            val4 is not None
            and abs(val4 - 0.11) < 1e-3
            and val11 is not None
            and abs(val11 - 0.44) < 1e-3,
            f"4->{val4} 11->{val11}",
        )
        if result_b.session_id:
            SmartBake.restore(result_b.session_id)

        # ===== preserve_outside_keys: sample_by tail-gap (nla.bake step-size off-by-one) =====
        # Confirmed live: nla.bake's `step` walks frame_start upward in fixed increments and does
        # NOT force a final sample exactly at frame_end when the range isn't evenly divisible by
        # it -- step=3 over (5, 10) bakes only frames 5 and 8, never 10. A hand-key at frame 9
        # (nominally "inside" (5, 10), but past the LAST frame nla.bake actually sampled) must
        # still be preserved -- comparing against the nominal range instead of the baked curve's
        # own real extent would otherwise silently drop it (neither baked nor preserved).
        _reset_smart_bake_regression_scene()
        target_g = bpy.data.objects.new("GapTarget", None)
        bpy.context.collection.objects.link(target_g)
        target_g.location = (0.0, 0.0, 0.0)
        target_g.keyframe_insert("location", frame=5)
        target_g.location = (10.0, 0.0, 0.0)
        target_g.keyframe_insert("location", frame=10)

        driven_g = cube("TailGap")
        cg = driven_g.constraints.new("COPY_LOCATION")
        cg.name = "CopyLoc"
        cg.target = target_g
        driven_g.rotation_euler = (0.0, 0.0, 0.99)
        driven_g.keyframe_insert("rotation_euler", frame=9, index=2)

        baker_g = SmartBake(objects=[driven_g], sample_by=3, bake_blend_shapes=False)
        result_g = baker_g.bake(baker_g.analyze(), time_range=(5, 10))
        check("tail-gap bake (sample_by=3) reports success", result_g.success, f"{result_g}")

        loc_fc_g = next(
            fc
            for fc in get_fcurves([driven_g])
            if fc.data_path == "location" and fc.array_index == 0
        )
        baked_frames_g = sorted(k.co.x for k in loc_fc_g.keyframe_points)
        check(
            "sample_by=3 over (5, 10) really does stop short of frame_end (proves the gap "
            "this check targets is real, not hypothetical)",
            10.0 not in baked_frames_g,
            f"{baked_frames_g}",
        )

        rot_fc_g = next(
            fc
            for fc in get_fcurves([driven_g])
            if fc.data_path == "rotation_euler" and fc.array_index == 2
        )
        val9 = next((k.co.y for k in rot_fc_g.keyframe_points if k.co.x == 9.0), None)
        check(
            "a hand-key in the sample_by tail gap (frame 9) is preserved, not silently dropped",
            val9 is not None and abs(val9 - 0.99) < 1e-3,
            f"{sorted((k.co.x, k.co.y) for k in rot_fc_g.keyframe_points)}",
        )
        if result_g.session_id:
            SmartBake.restore(result_g.session_id)

        # ==================== optimize_keys=False (default) vs True ====================
        def build_partially_varying_rig(name):
            target = bpy.data.objects.new(f"{name}Target", None)
            bpy.context.collection.objects.link(target)
            target.location.x = 0.0
            target.keyframe_insert("location", frame=1, index=0)
            target.location.x = 5.0
            target.keyframe_insert("location", frame=20, index=0)

            driven = cube(name)
            c = driven.constraints.new("COPY_LOCATION")
            c.name = "CopyLoc"
            c.target = target
            return driven

        check(
            "optimize_keys defaults to False on a plain SmartBake",
            SmartBake().optimize_keys is False,
        )

        _reset_smart_bake_regression_scene()
        driven_no_opt = build_partially_varying_rig("OptOff")
        baker_no_opt = SmartBake(objects=[driven_no_opt], bake_blend_shapes=False)
        result_no_opt = baker_no_opt.bake(baker_no_opt.analyze(), time_range=(1, 20))
        check("optimize_keys=False bake reports success", result_no_opt.success)
        check(
            "optimize_keys=False leaves all 9 freshly-baked transform curves untouched",
            len(get_fcurves([driven_no_opt])) == 9,
            f"{len(get_fcurves([driven_no_opt]))}",
        )
        check(
            "BakeResult.optimized stays empty when optimize_keys=False",
            result_no_opt.optimized == [],
            f"{result_no_opt.optimized}",
        )
        if result_no_opt.session_id:
            SmartBake.restore(result_no_opt.session_id)

        _reset_smart_bake_regression_scene()
        driven_opt = build_partially_varying_rig("OptOn")
        baker_opt = SmartBake(objects=[driven_opt], bake_blend_shapes=False, optimize_keys=True)
        result_opt = baker_opt.bake(baker_opt.analyze(), time_range=(1, 20))
        check("optimize_keys=True bake reports success", result_opt.success, f"{result_opt}")
        check(
            "BakeResult.optimized records the baked object",
            result_opt.optimized == ["OptOn"],
            f"{result_opt.optimized}",
        )
        post_curves = get_fcurves([driven_opt])
        check(
            "optimize_keys=True actually invoked AnimUtils.optimize_keys and collapsed the "
            "8 static baked curves, leaving only the 1 that truly varies",
            len(post_curves) == 1,
            f"{len(post_curves)} fcurve(s) remain: "
            f"{[f'{fc.data_path}[{fc.array_index}]' for fc in post_curves]}",
        )
        if result_opt.session_id:
            SmartBake.restore(result_opt.session_id)

        # ========= optimize_keys=True on a BLEND-SHAPE bake (shape_keys datablock) =========
        # Proves optimize_keys is invoked against the mesh's `shape_keys` datablock -- not just
        # object-level transform bakes -- since `optimize_keys`'s underlying `_actions()` only
        # inspects `obj.animation_data`, never `obj.data.shape_keys.animation_data`. Two shape
        # keys are each independently driven (one truly varies over the bake range, one is
        # driven by a constant expression); after baking + optimizing, the constant one must
        # collapse away entirely, leaving only the one that actually varies.
        def build_driven_shape_key_mesh(name):
            target = bpy.data.objects.new(f"{name}Target", None)
            bpy.context.collection.objects.link(target)
            target.location.x = 0.0
            target.keyframe_insert("location", frame=1, index=0)
            target.location.x = 10.0
            target.keyframe_insert("location", frame=20, index=0)

            mesh_obj = cube(name)
            mesh_obj.shape_key_add(name="Basis")
            kb_varying = mesh_obj.shape_key_add(name="Varying")
            kb_static = mesh_obj.shape_key_add(name="Static")

            drv_v = kb_varying.driver_add("value").driver
            drv_v.type = "SCRIPTED"
            drv_v.expression = "x / 10.0"
            var_v = drv_v.variables.new()
            var_v.name = "x"
            var_v.type = "SINGLE_PROP"
            var_v.targets[0].id_type = "OBJECT"
            var_v.targets[0].id = target
            var_v.targets[0].data_path = "location.x"

            drv_s = kb_static.driver_add("value").driver
            drv_s.type = "SCRIPTED"
            drv_s.expression = "0.5"

            return mesh_obj

        _reset_smart_bake_regression_scene()
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 20
        mesh_bs = build_driven_shape_key_mesh("BsOpt")

        baker_bs = SmartBake(objects=[mesh_bs], bake_blend_shapes=True, optimize_keys=True)
        result_bs = baker_bs.bake(baker_bs.analyze(), time_range=(1, 20))
        check("blend-shape optimize bake reports success", result_bs.success, f"{result_bs}")
        check(
            "BakeResult.optimized includes the shape-key mesh object",
            "BsOpt" in result_bs.optimized,
            f"{result_bs.optimized}",
        )

        post_sk_curves = get_fcurves([mesh_bs.data.shape_keys])
        check(
            "optimize_keys=True on a blend-shape bake collapsed the constant-valued shape-key "
            "curve, leaving only the one that truly varies",
            len(post_sk_curves) == 1
            and post_sk_curves[0].data_path == 'key_blocks["Varying"].value',
            f"{[fc.data_path for fc in post_sk_curves]}",
        )
        if result_bs.session_id:
            SmartBake.restore(result_bs.session_id)
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("preserve_outside_keys/optimize_keys harness raised", False, repr(e))

    return lines


def _run_backup_mode_checks():
    """bpy-driven checks proving ``cmb_backup``'s 3-way mapping onto
    ``SmartBake(backup_file=...)`` -- ``None`` (Auto) backs up only when ``delete_sources=True``,
    ``True`` (Always) always backs up, ``False`` (Never) never backs up -- against a REAL saved
    ``.blend`` (``SmartBake._save_backup`` no-ops on an unsaved scene, unlike every other check
    in this file, which is why this one saves to ``test/temp_tests/`` and cleans up after
    itself). Returns ``"OK ..."``/``"FAIL ..."`` lines, same convention as
    ``_run_data_internal_export_exclusion_checks``.
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    import os

    here = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(here, "temp_tests")
    scene_path = os.path.join(temp_dir, "smart_bake_backup_probe.blend")
    expected_backup_path = os.path.join(temp_dir, "smart_bake_backup_probe_prebake.blend")

    try:
        import bpy
        import bmesh

        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        os.makedirs(temp_dir, exist_ok=True)

        def cube(name):
            me = bpy.data.meshes.new(f"{name}_mesh")
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(name, me)
            bpy.context.collection.objects.link(o)
            return o

        def build_driven_rig(name):
            target = bpy.data.objects.new(f"{name}Target", None)
            bpy.context.collection.objects.link(target)
            target.location = (0.0, 0.0, 0.0)
            target.keyframe_insert("location", frame=1)
            target.location = (5.0, 0.0, 0.0)
            target.keyframe_insert("location", frame=10)

            driven = cube(name)
            c = driven.constraints.new("COPY_LOCATION")
            c.name = "CopyLoc"
            c.target = target
            return driven

        def clear_backup_file():
            if os.path.exists(expected_backup_path):
                os.remove(expected_backup_path)

        _reset_smart_bake_regression_scene()
        bpy.ops.wm.save_as_mainfile(filepath=scene_path)
        check(
            "scene saved to a real path for the backup probe (SmartBake._save_backup no-ops "
            "on an unsaved scene)",
            bpy.data.filepath == scene_path,
            bpy.data.filepath,
        )

        # ---- Auto (None): no backup for a plain (non-delete_sources) bake ----
        clear_backup_file()
        _reset_smart_bake_regression_scene()
        driven_a1 = build_driven_rig("AutoNoDelete")
        result_a1 = SmartBake.run(
            objects=[driven_a1], bake_blend_shapes=False, backup_file=None, delete_sources=False
        )
        check(
            "Auto (None) does NOT back up a plain bake",
            result_a1.backup_path is None and not os.path.exists(expected_backup_path),
            f"{result_a1.backup_path}",
        )
        if result_a1.session_id:
            SmartBake.restore(result_a1.session_id)

        # ---- Auto (None): backs up when delete_sources=True (the one non-restorable path) ----
        clear_backup_file()
        _reset_smart_bake_regression_scene()
        driven_a2 = build_driven_rig("AutoDelete")
        result_a2 = SmartBake.run(
            objects=[driven_a2], bake_blend_shapes=False, backup_file=None, delete_sources=True
        )
        check(
            "Auto (None) DOES back up a delete_sources bake",
            bool(result_a2.backup_path) and os.path.exists(expected_backup_path),
            f"{result_a2.backup_path}",
        )

        # ---- Always (True): backs up even without delete_sources ----
        clear_backup_file()
        _reset_smart_bake_regression_scene()
        driven_always = build_driven_rig("Always")
        result_always = SmartBake.run(
            objects=[driven_always], bake_blend_shapes=False, backup_file=True,
            delete_sources=False,
        )
        check(
            "Always (True) backs up even without delete_sources",
            bool(result_always.backup_path) and os.path.exists(expected_backup_path),
            f"{result_always.backup_path}",
        )
        if result_always.session_id:
            SmartBake.restore(result_always.session_id)

        # ---- Never (False): never backs up, even WITH delete_sources ----
        clear_backup_file()
        _reset_smart_bake_regression_scene()
        driven_never = build_driven_rig("Never")
        result_never = SmartBake.run(
            objects=[driven_never], bake_blend_shapes=False, backup_file=False,
            delete_sources=True,
        )
        check(
            "Never (False) does not back up even WITH delete_sources",
            result_never.backup_path is None and not os.path.exists(expected_backup_path),
            f"{result_never.backup_path}",
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("backup-mode harness raised", False, repr(e))
    finally:
        try:
            if os.path.exists(expected_backup_path):
                os.remove(expected_backup_path)
        except OSError:
            pass
        try:
            if os.path.exists(scene_path):
                os.remove(scene_path)
        except OSError:
            pass

    return lines


def _run_task_manager_wiring_checks():
    """bpy-driven checks proving the 7 ``task_manager.py`` methods that consume
    ``anim_utils.smart_bake``/``_anim_utils`` (``smart_bake``, ``optimize_keys``,
    ``tie_all_keyframes``, ``snap_keys_to_frame``, ``set_bake_animation_range`` +
    ``revert_bake_animation_range``, ``check_untied_keyframes``, ``check_floating_point_keys``)
    behave as documented, and that ``TaskFactory._execute_tasks_and_checks``'s
    ``_optimize_keys_enabled`` forwarding actually reaches ``smart_bake()`` before it runs.
    Returns ``"OK ..."``/``"FAIL ..."`` lines, same convention as
    :func:`_run_data_internal_export_exclusion_checks`.
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import logging

        import bpy
        import bmesh

        from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter
        from blendertk.anim_utils._anim_utils import get_fcurves
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        def reset():
            if (
                bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"
            ):
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            for a in list(bpy.data.actions):
                bpy.data.actions.remove(a)
            bpy.context.scene.frame_start = 1
            bpy.context.scene.frame_end = 250

        def empty(name):
            o = bpy.data.objects.new(name, None)
            bpy.context.collection.objects.link(o)
            return o

        def cube(name):
            me = bpy.data.meshes.new(f"{name}_mesh")
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(name, me)
            bpy.context.collection.objects.link(o)
            return o

        class _ListHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.messages = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        # ---- check_floating_point_keys / check_untied_keyframes / snap / tie -------------
        reset()
        rig = cube("WiringCube1")
        rig.keyframe_insert(data_path="scale", frame=3.3, index=0)
        rig.scale[0] = 2.0
        rig.keyframe_insert(data_path="scale", frame=17.7, index=0)
        rig.rotation_euler[2] = 0.0
        rig.keyframe_insert(data_path="rotation_euler", frame=5, index=2)
        rig.rotation_euler[2] = 1.0
        rig.keyframe_insert(data_path="rotation_euler", frame=20, index=2)

        tm = SceneExporter(log_level="DEBUG").task_manager
        tm.objects = [rig]

        passed, _ = tm.check_floating_point_keys(True)
        check("check_floating_point_keys detects fractional keys", passed is False)

        passed, _ = tm.check_untied_keyframes(True)
        check("check_untied_keyframes detects mismatched curve extents", passed is False)

        tm.snap_keys_to_frame()
        scale_fc = next(fc for fc in get_fcurves([rig]) if fc.data_path == "scale")
        frames = sorted(k.co.x for k in scale_fc.keyframe_points)
        check("snap_keys_to_frame snaps to whole frames", frames == [3.0, 18.0], str(frames))

        passed, msgs = tm.check_floating_point_keys(True)
        check("check_floating_point_keys passes after snap", passed is True and msgs == [])

        tm.tie_all_keyframes()
        scale_fc = next(fc for fc in get_fcurves([rig]) if fc.data_path == "scale")
        rot_fc = next(fc for fc in get_fcurves([rig]) if fc.data_path == "rotation_euler")
        s_frames = sorted(k.co.x for k in scale_fc.keyframe_points)
        r_frames = sorted(k.co.x for k in rot_fc.keyframe_points)
        check(
            "tie_all_keyframes adds matching bookend keys",
            s_frames[0] == r_frames[0] and s_frames[-1] == r_frames[-1],
            f"scale={s_frames} rot={r_frames}",
        )

        passed, msgs = tm.check_untied_keyframes(True)
        check("check_untied_keyframes passes after tie", passed is True and msgs == [])

        # ---- smart_bake / optimize_keys / set+revert_bake_animation_range -----------------
        reset()
        source = empty("WiringSource")
        source.location.x = 0.0
        source.keyframe_insert(data_path="location", frame=2, index=0)
        source.location.x = 5.0
        source.keyframe_insert(data_path="location", frame=15, index=0)

        target = cube("WiringTarget")
        con = target.constraints.new("COPY_LOCATION")
        con.target = source

        tm2 = SceneExporter(log_level="DEBUG").task_manager
        tm2.objects = [target]

        check("_has_keyframes False before bake", tm2._has_keyframes is False)

        try:
            tm2.optimize_keys()
            tm2.tie_all_keyframes()
            tm2.snap_keys_to_frame()
            noop_ok = True
        except Exception as e:  # pragma: no cover
            noop_ok = False
            check("pre-bake no-keyframe methods raised", False, repr(e))
        if noop_ok:
            check("pre-bake no-keyframe methods do not raise", True)

        tm2.smart_bake()
        check(
            "smart_bake assigns a fresh Action",
            target.animation_data is not None and target.animation_data.action is not None,
        )
        check("smart_bake mutes the constraint by default", con.mute is True)
        check(
            "smart_bake records a session id",
            getattr(tm2, "_bake_session_id", None) is not None,
        )
        check("_has_keyframes True after bake", tm2._has_keyframes is True)

        before = len(get_fcurves([target]))
        tm2.optimize_keys()
        after = len(get_fcurves([target]))
        check("optimize_keys runs after bake without raising", after <= before, f"{before}->{after}")

        tm2.tie_all_keyframes()
        tm2.snap_keys_to_frame()
        whole = all(
            abs(k.co.x - round(k.co.x)) < 1e-6
            for fc in get_fcurves([target])
            for k in fc.keyframe_points
        )
        check("post-bake tie/snap keep keys on whole frames", whole)

        original_range = tm2.set_bake_animation_range()
        scene = bpy.context.scene
        frames_all = [k.co.x for fc in get_fcurves([target]) for k in fc.keyframe_points]
        expected = (int(min(frames_all)), int(max(frames_all)))
        check(
            "set_bake_animation_range sets scene range to keyed extent",
            (scene.frame_start, scene.frame_end) == expected,
            f"actual=({scene.frame_start},{scene.frame_end}) expected={expected}",
        )
        check("set_bake_animation_range returns the prior range", original_range == (1, 250))

        tm2.revert_bake_animation_range(original_range)
        check(
            "revert_bake_animation_range restores the scene range",
            (scene.frame_start, scene.frame_end) == (1, 250),
        )

        restore_result = SmartBake.restore(tm2._bake_session_id)
        check("SmartBake.restore cleans up the session", restore_result.success is True)

        # ---- _optimize_keys_enabled forwarding reaches smart_bake() before it logs --------
        reset()
        source2 = empty("WiringSource2")
        source2.location.x = 0.0
        source2.keyframe_insert(data_path="location", frame=1, index=0)
        source2.location.x = 8.0
        source2.keyframe_insert(data_path="location", frame=24, index=0)
        target2 = cube("WiringTarget2")
        con2 = target2.constraints.new("COPY_LOCATION")
        con2.target = source2

        tm3 = SceneExporter(log_level="DEBUG").task_manager
        tm3.objects = [target2]
        handler = _ListHandler()
        tm3.logger.addHandler(handler)

        ok = tm3.run_tasks(
            {
                "smart_bake": True,
                "optimize_keys": True,
                "tie_all_keyframes": True,
                "snap_keys_to_frame": True,
                "set_bake_animation_range": True,
            }
        )
        check("run_tasks full pipeline returns True", ok is True)
        check(
            "_optimize_keys_enabled set before smart_bake runs",
            tm3._optimize_keys_enabled is True,
        )
        check(
            "smart_bake's log reflects the forwarded flag",
            any("optimize_keys will run next" in m for m in handler.messages),
        )

        # Same dispatch WITHOUT optimize_keys in the task dict — proves the forwarding is
        # state-driven (reads what was actually requested), not always-on.
        reset()
        source3 = empty("WiringSource3")
        source3.location.x = 0.0
        source3.keyframe_insert(data_path="location", frame=1, index=0)
        source3.location.x = 3.0
        source3.keyframe_insert(data_path="location", frame=10, index=0)
        target3 = cube("WiringTarget3")
        con3 = target3.constraints.new("COPY_LOCATION")
        con3.target = source3

        tm4 = SceneExporter(log_level="DEBUG").task_manager
        tm4.objects = [target3]
        handler2 = _ListHandler()
        tm4.logger.addHandler(handler2)
        tm4.run_tasks({"smart_bake": True})
        check(
            "_optimize_keys_enabled False when task absent from the dispatch dict",
            tm4._optimize_keys_enabled is False,
        )
        check(
            "smart_bake's log omits the forwarded-flag line when disabled",
            not any("optimize_keys will run next" in m for m in handler2.messages),
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("task_manager wiring harness raised", False, repr(e))

    return lines


def _run_blend_shape_driver_restore_checks():
    """bpy-driven checks proving the shape-key driven-weight bake+restore round trip (fact-set
    item 7): a ``SCRIPTED``/``SINGLE_PROP`` driver is rebuilt by ``SmartBake.restore()`` from the
    snapshot ``bake_session.snapshot_blend_shape_driver`` took before ``bake_blend_shapes()``
    destructively removed it, and genuinely re-evaluates afterward; an ``AVERAGE``-type driver in
    the SAME session is outside the documented rebuild scope and must be skipped with a warning,
    never a crash, without blocking the rebuildable driver from restoring. Returns
    ``"OK ..."``/``"FAIL ..."`` lines, same convention as
    :func:`_run_data_internal_export_exclusion_checks`.
    """
    lines = []

    def check(name, cond, detail=""):
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

    try:
        import bpy

        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        def reset():
            for session_id in list(SmartBake.list_sessions()):
                SmartBake.restore(session_id)
            if (
                bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"
            ):
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            for a in list(bpy.data.actions):
                bpy.data.actions.remove(a)
            bpy.context.scene.frame_start = 1
            bpy.context.scene.frame_end = 250

        def eval_key(mesh_obj, key_name, frame):
            scene = bpy.context.scene
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            sk_eval = mesh_obj.evaluated_get(depsgraph).data.shape_keys
            return sk_eval.key_blocks[key_name].value

        reset()
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 10

        bpy.ops.mesh.primitive_cube_add()
        target = bpy.context.active_object
        target.name = "DriverTarget"
        for frame, x, y in ((1, 0.0, 0.2), (10, 10.0, 0.8)):
            target.location.x = x
            target.location.y = y
            target.keyframe_insert(data_path="location", index=0, frame=frame)
            target.keyframe_insert(data_path="location", index=1, frame=frame)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        mesh = bpy.context.active_object
        mesh.name = "ShapeMesh"
        mesh.shape_key_add(name="Basis")
        kb1 = mesh.shape_key_add(name="Key1")
        kb2 = mesh.shape_key_add(name="Key2")

        drv1 = kb1.driver_add("value").driver
        drv1.type = "SCRIPTED"
        drv1.expression = "x / 10.0"
        var1 = drv1.variables.new()
        var1.name = "x"
        var1.type = "SINGLE_PROP"
        var1.targets[0].id_type = "OBJECT"
        var1.targets[0].id = target
        var1.targets[0].data_path = "location.x"

        # AVERAGE-type driver — outside the SCRIPTED/SINGLE_PROP rebuild scope (item 7) even
        # though its lone variable is itself SINGLE_PROP; the DRIVER TYPE disqualifies it.
        drv2 = kb2.driver_add("value").driver
        drv2.type = "AVERAGE"
        var2 = drv2.variables.new()
        var2.name = "y"
        var2.type = "SINGLE_PROP"
        var2.targets[0].id_type = "OBJECT"
        var2.targets[0].id = target
        var2.targets[0].data_path = "location.y"

        result = SmartBake.run(objects=[mesh, target], bake_blend_shapes=True, sample_by=1)
        check("bake succeeded", result.success, f"baked={result.baked}")
        check("bake recorded a restorable session", result.session_id is not None)

        sk = mesh.data.shape_keys
        check(
            "bake removed both shape-key drivers",
            bool(sk.animation_data) and len(sk.animation_data.drivers) == 0,
        )
        check(
            "bake wrote plain keyframes for the driven weights",
            bool(sk.animation_data) and sk.animation_data.action is not None,
        )

        v1 = eval_key(mesh, "Key1", 1)
        v10 = eval_key(mesh, "Key1", 10)
        check(
            "baked Key1 values match the sampled driver expression (x/10)",
            abs(v1 - 0.0) < 0.02 and abs(v10 - 1.0) < 0.02,
            f"v1={v1:.3f} v10={v10:.3f}",
        )

        restore_result = SmartBake.restore(result.session_id)
        check("restore reported success", restore_result.success)
        check(
            "restore rebuilt Key1's driver",
            f"{mesh.name}.Key1" in restore_result.blend_shapes_restored,
            f"{restore_result.blend_shapes_restored}",
        )
        check(
            "restore warned about the non-rebuildable Key2 driver",
            any("Key2" in w for w in restore_result.warnings),
            f"{restore_result.warnings}",
        )
        check(
            "restore_result.success stayed True despite Key2's warning",
            restore_result.success,
        )
        check(
            "Key2 not falsely reported as restored",
            f"{mesh.name}.Key2" not in restore_result.blend_shapes_restored,
        )

        sk = mesh.data.shape_keys
        rebuilt_drivers = {fc.data_path: fc.driver for fc in sk.animation_data.drivers}
        rebuilt = rebuilt_drivers.get('key_blocks["Key1"].value')
        check(
            "Key1 driver rebuilt with the same expression",
            bool(rebuilt) and rebuilt.expression == "x / 10.0",
            getattr(rebuilt, "expression", None),
        )
        var = rebuilt.variables[0] if rebuilt else None
        check(
            "Key1 driver rebuilt targeting the same object/data_path",
            bool(var) and var.targets[0].id is target and var.targets[0].data_path == "location.x",
        )
        check(
            "Key2 driver correctly left un-rebuilt (out of scope)",
            'key_blocks["Key2"].value' not in rebuilt_drivers,
        )

        # Live-behavior-returns proof: detach the target's own keyed action (it only existed to
        # drive time-range detection / the pre-bake sample), then move it to a value never seen
        # during the bake and confirm the REBUILT driver re-evaluates it, not a stale baked value.
        target.animation_data_clear()
        target.location.x = 6.0
        v_live = eval_key(mesh, "Key1", 1)
        check(
            "rebuilt driver re-evaluates on a live target change (x=6 -> 0.6)",
            abs(v_live - 0.6) < 0.02,
            f"v_live={v_live:.3f}",
        )
    except Exception as e:  # pragma: no cover - failure path prints its own traceback
        import traceback

        traceback.print_exc()
        check("blend-shape driver bake+restore harness raised", False, repr(e))

    return lines


if __name__ == "__main__":
    import importlib

    try:
        importlib.import_module("bpy")
        have_bpy = True
    except Exception:
        have_bpy = False

    if have_bpy:
        result_lines = _run_data_internal_export_exclusion_checks()
        result_lines += _run_engine_round_trip_checks()
        result_lines += _run_driver_bake_restore_regression_checks()
        result_lines += _run_ik_bake_restore_regression_checks()
        result_lines += _run_preserve_outside_and_optimize_checks()
        result_lines += _run_task_manager_wiring_checks()
        result_lines += _run_blend_shape_driver_restore_checks()
        # Saves a REAL .blend under temp_tests/ — run last so its bpy.data.filepath side
        # effect (persists for the rest of this process) can't affect any earlier check.
        result_lines += _run_backup_mode_checks()
        print("\n".join(result_lines))
        passed = sum(1 for ln in result_lines if ln.startswith("OK"))
        ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
        print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
    else:
        _runner = unittest.TextTestRunner(verbosity=2)
        _result = _runner.run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        )
        print(f"===RESULT: {'PASS' if _result.wasSuccessful() else 'FAIL'}===")
