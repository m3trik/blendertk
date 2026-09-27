"""blendertk.core_utils._core_utils headless test — the window-independent context readers
(``selected_objects`` / ``active_object`` / ``get_areas``).

Regression guard for the "tentacle Blender operations report *nothing selected* while an object
IS selected" bug: the slots run from tentacle's Qt event-pump timer, a context where
``bpy.context.window`` is None, and the screen-context members ``bpy.context.selected_objects`` /
``active_object`` are empty there. ``btk.selected_objects()`` / ``btk.active_object()`` must read
the window-independent ``view_layer.objects`` instead. ``bpy.context.temp_override(window=None)``
reproduces the exact failing condition headlessly.

Run: blender --background --factory-startup --python blendertk/test/test_core_utils.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)           # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # --- one cube, selected + active -------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.view_layer.objects.active
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube

    # 1. normal reads (window present)
    check("selected_objects() -> [cube]", btk.selected_objects() == [cube])
    check("active_object() -> cube", btk.active_object() is cube)
    check("selected_objects() matches view_layer.objects.selected",
          btk.selected_objects() == [o for o in bpy.context.view_layer.objects.selected])

    # 2. THE REGRESSION: from a window-less context (the Qt event-pump timer condition), the
    #    screen-context members go empty while the view-layer readers stay correct.
    with bpy.context.temp_override(window=None):
        raw_sel = list(getattr(bpy.context, "selected_objects", None) or [])
        raw_active = getattr(bpy.context, "active_object", None)
        check("precondition: temp_override(window=None) empties bpy.context.selected_objects",
              raw_sel == [], f"raw={ [o.name for o in raw_sel] }")
        check("precondition: temp_override(window=None) nulls bpy.context.active_object",
              raw_active is None, f"raw={raw_active!r}")
        check("selected_objects() survives window=None -> [cube]",
              btk.selected_objects() == [cube], f"got={ [o.name for o in btk.selected_objects()] }")
        check("active_object() survives window=None -> cube",
              btk.active_object() is cube, f"got={btk.active_object()!r}")

    # 3. empty selection -> empty list / None active (no crash)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = None
    check("selected_objects() empty -> []", btk.selected_objects() == [])
    check("active_object() no active -> None", btk.active_object() is None)

    # 4. get_areas — the same window-independence contract for area iteration: a
    #    ``context.screen.areas`` loop crashes with AttributeError when window is None (the
    #    display/selection viewport toggles' bug); get_areas resolves through the window
    #    manager instead, so its result is IDENTICAL with and without a context window.
    #    (Even --background keeps one window with the default screen, so the list is
    #    usually non-empty here — the contract is type-filtered + window-independent,
    #    not empty.)
    baseline = btk.get_areas("VIEW_3D")
    check("get_areas returns only VIEW_3D areas",
          all(a.type == "VIEW_3D" for a in baseline), f"got={ [a.type for a in baseline] }")
    with bpy.context.temp_override(window=None):
        check("precondition: window=None nulls bpy.context.screen",
              getattr(bpy.context, "screen", None) is None)
        check("get_areas survives window=None (identical result)",
              btk.get_areas("VIEW_3D") == baseline)

    # 5. multi-select is order-independent set membership
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.view_layer.objects.active
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.view_layer.objects.active
    a.select_set(True); b.select_set(True)
    with bpy.context.temp_override(window=None):
        check("selected_objects() window=None sees both of a multi-selection",
              set(btk.selected_objects()) == {a, b},
              f"got={ sorted(o.name for o in btk.selected_objects()) }")

    # --- 6. _rebind_pil_globals repairs BOTH un-provisioned states ------------------------
    # Blender's bundled Python ships no Pillow, so ``import pythontk`` at startup takes the
    # ImportError branch of its guarded PIL imports; ``ensure_image_deps`` pip-installs Pillow
    # later in the session and calls this to make the already-imported modules see it.
    # A guard that binds only ``Image = None`` leaves its other names UNDEFINED, and an
    # undefined name is a NameError at the call site — which is how the Material Updater died
    # with "name 'ImageOps' is not defined" while Pillow was installed and importable.
    # pythontk binds them all now; blendertk still runs against whatever pythontk is installed,
    # so the repair must cover the absent case too.
    import types
    from blendertk.core_utils._core_utils import _CoreUtilsInternal

    def _pil_importable():
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False

    if not _pil_importable():  # --factory-startup drops the user-modules dir from sys.path
        _mods = bpy.utils.user_resource("SCRIPTS", path="modules", create=False)
        if _mods and os.path.isdir(_mods) and _mods not in sys.path:
            sys.path.insert(0, _mods)

    if not _pil_importable():
        check("_rebind_pil_globals (SKIPPED — no Pillow in this interpreter)", True)
    else:
        WATCHED = (
            "pythontk.img_utils._img_utils",
            "pythontk.core_utils.engines.textures.map_factory._map_factory",
            "pythontk.core_utils.engines.textures.map_factory.processor",
        )
        # Single-name guards (``Image`` only), deliberately NOT in the repair's
        # hand-listed set — pass 1 has to reach them by walking loaded pythontk
        # modules, or the Map Converter's optimize/mask paths stay dead in Blender
        # while Pillow is installed.
        UNLISTED = (
            "pythontk.core_utils.engines.textures.map_optimizer",
            "pythontk.core_utils.engines.textures.region_masks",
            "pythontk.img_utils.mask_generator",
            "pythontk.img_utils.ktx2_encoder",
        )
        NEEDED = ("Image", "ImageOps", "ImageEnhance", "ImageFilter",
                  "ImageChops", "ImageDraw", "ImageMode")
        saved = {name: sys.modules.get(name) for name in WATCHED + UNLISTED}
        try:
            # Stub each watched module in the OLD pythontk shape: only ``Image`` exists (as
            # None); every other PIL name was never created by the failed ``from PIL import``.
            for name in WATCHED + UNLISTED:
                stub = types.ModuleType(name)
                stub.Image = None
                sys.modules[name] = stub

            _CoreUtilsInternal._rebind_pil_globals()

            check(
                "_rebind_pil_globals reaches guards outside its hand-listed set",
                all(getattr(sys.modules[n], "Image", None) is not None for n in UNLISTED),
                f"unrepaired={[n for n in UNLISTED if getattr(sys.modules[n], 'Image', None) is None]}",
            )

            unrepaired = [
                f"{name.rsplit('.', 1)[-1]}.{n}"
                for name in WATCHED
                for n in NEEDED
                if getattr(sys.modules[name], n, None) is None
            ]
            check("_rebind_pil_globals binds names that were never created (not just None)",
                  not unrepaired, f"unrepaired={unrepaired}")

            # And it must never clobber a binding that already works.
            sentinel = object()
            for name in WATCHED + UNLISTED:
                sys.modules[name].ImageOps = sentinel
            _CoreUtilsInternal._rebind_pil_globals()
            check("_rebind_pil_globals leaves an already-bound name alone",
                  all(sys.modules[n].ImageOps is sentinel for n in WATCHED + UNLISTED))
        finally:
            for name, mod in saved.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod

    # ---- user_config_path: the one resolver behind every config-dir sidecar
    # (recent-files.txt, blendertk_script_output.json, blendertk_ui_state.json)
    cfg = btk.user_config_path("x.json")
    check("user_config_path resolves under Blender's CONFIG dir",
          cfg is not None and os.path.basename(cfg) == "x.json"
          and os.path.dirname(cfg) == bpy.utils.user_resource("CONFIG"), str(cfg))
    check("user_config_path(base=...) honors the sandbox override",
          btk.user_config_path("x.json", base=os.path.join("a", "b")) == os.path.join("a", "b", "x.json"))
    check("get_recent_files still reads through it (list)", isinstance(btk.get_recent_files(), list))

    # ---- _mesh_face_counts: the ONE fan-count primitive behind get_scene_info /
    # analyze_scene / _mesh_metrics / AutoInstancer / InstancingStrategy.
    # Pins it against the per-polygon Python idiom it replaced -- the counts must be
    # identical on a mesh carrying tris, quads AND ngons, or the five call sites drift.
    import bmesh
    from blendertk.core_utils._core_utils import _CoreUtilsInternal

    def old_idiom(me):
        """The pre-unification loop: fan count + ngon count, one RNA read per face."""
        tris = ngons = 0
        for poly in me.polygons:
            n = len(poly.vertices)
            tris += max(n - 2, 0)
            if n > 4:
                ngons += 1
        return tris, ngons

    reset()
    mixed = bpy.data.meshes.new("btk_mixed_faces")
    bm = bmesh.new()
    verts = [bm.verts.new((float(i), float(i % 3), 0.0)) for i in range(24)]
    bm.verts.ensure_lookup_table()
    bm.faces.new(verts[0:3])     # tri
    bm.faces.new(verts[3:6])     # tri
    bm.faces.new(verts[6:10])    # quad
    bm.faces.new(verts[10:14])   # quad
    bm.faces.new(verts[14:19])   # 5-gon
    bm.faces.new(verts[19:24])   # 5-gon
    bm.to_mesh(mixed)
    bm.free()

    check("mixed fixture really carries tris + quads + ngons",
          sorted(len(f.vertices) for f in mixed.polygons) == [3, 3, 4, 4, 5, 5])
    check("_mesh_face_counts == the loop idiom on tris+quads+ngons",
          _CoreUtilsInternal._mesh_face_counts(mixed) == old_idiom(mixed),
          f"fast={_CoreUtilsInternal._mesh_face_counts(mixed)} loop={old_idiom(mixed)}")
    check("_mesh_face_counts returns the hand-computed (tris, ngons)",
          _CoreUtilsInternal._mesh_face_counts(mixed) == (1 + 1 + 2 + 2 + 3 + 3, 2),
          str(_CoreUtilsInternal._mesh_face_counts(mixed)))

    # A denser, subdivided mesh: same answer as the loop, no clamping drift.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40)
    grid = bpy.context.view_layer.objects.active
    check("_mesh_face_counts == the loop idiom on a dense all-quad grid",
          _CoreUtilsInternal._mesh_face_counts(grid.data) == old_idiom(grid.data),
          f"{len(grid.data.polygons)} polys")

    # Degenerate / non-bpy inputs must not raise (the auto-instancer passes objects
    # whose .data may be missing, and InstancingStrategy is exercised with doubles).
    check("_mesh_face_counts(None) -> (0, 0)", _CoreUtilsInternal._mesh_face_counts(None) == (0, 0))
    check("_mesh_face_counts(empty mesh) -> (0, 0)",
          _CoreUtilsInternal._mesh_face_counts(bpy.data.meshes.new("btk_empty")) == (0, 0))

    class _FakePoly:
        def __init__(self, n): self.vertices = tuple(range(n))

    class _FakeMesh:
        polygons = [_FakePoly(3), _FakePoly(4), _FakePoly(6)]

    check("_mesh_face_counts falls back for a non-bpy sequence (test double)",
          _CoreUtilsInternal._mesh_face_counts(_FakeMesh()) == (1 + 2 + 4, 1),
          str(_CoreUtilsInternal._mesh_face_counts(_FakeMesh())))

    # The five call sites agree with the primitive.
    from blendertk.edit_utils._edit_utils import _EditUtilsInternal
    from blendertk.core_utils.auto_instancer.instancing_strategy import (
        InstancingStrategy, StrategyConfig,
    )

    expected_tris = old_idiom(grid.data)[0]
    check("EditUtils._mesh_metrics 'triangle' matches the primitive",
          _EditUtilsInternal._mesh_metrics(grid, ["triangle"]) == [expected_tris],
          str(_EditUtilsInternal._mesh_metrics(grid, ["triangle"])))
    check("InstancingStrategy._get_triangle_count matches the primitive",
          InstancingStrategy(StrategyConfig())._get_triangle_count(grid) == expected_tris)
    info = btk.get_scene_info(objects=[grid])
    check("get_scene_info triangles/ngons match the primitive",
          (info["triangles"], info["ngons"]) == _CoreUtilsInternal._mesh_face_counts(grid.data),
          f"{info['triangles']}/{info['ngons']}")
    reset()

    # ---- ensure_packages: resolver-aware install into the TAIL-precedence dir ----
    # The provisioning policy must (a) target scripts\addons\modules — natively on
    # sys.path AFTER bundled site-packages, so a provisioned dist can never shadow a
    # bundled one — and (b) go through PackageManager.install_targeted (pip-resolver
    # plan + --no-deps apply), never a raw resolver-blind `pip install --target`.
    import os as _os
    from blendertk.core_utils import _core_utils as _cu_mod

    target_dir, legacy_dir = _cu_mod._CoreUtilsInternal._engine_install_dirs()
    check("engine install dir is scripts/addons/modules (tail precedence)",
          _os.path.normpath(target_dir).lower().endswith(_os.path.normpath("scripts/addons/modules")),
          target_dir)
    check("legacy dir is the old top-precedence scripts/modules",
          _os.path.normpath(legacy_dir).lower().endswith(_os.path.normpath("scripts/modules"))
          and "addons" not in _os.path.normpath(legacy_dir).lower().split(_os.sep)[-2],
          legacy_dir)

    class _RecorderPM:
        captured = None
        def __init__(self, python_path=None):
            type(self).captured_python = python_path
        def install_targeted(self, specs, target_dir, upgrade=False):
            type(self).captured = {"specs": list(specs), "target_dir": target_dir, "upgrade": upgrade}
            return []

    _real_pm = _cu_mod.ptk.PackageManager
    try:
        _cu_mod.ptk.PackageManager = _RecorderPM
        got = btk.CoreUtils.ensure_packages({"zz-absent-probe-pkg": "zz_absent_probe_pkg"})
        check("ensure_packages routes through install_targeted", _RecorderPM.captured is not None,
              str(_RecorderPM.captured))
        if _RecorderPM.captured:
            check("ensure_packages targets addons/modules",
                  _os.path.normpath(_RecorderPM.captured["target_dir"]) == _os.path.normpath(target_dir),
                  _RecorderPM.captured["target_dir"])
            check("ensure_packages passes the missing spec",
                  _RecorderPM.captured["specs"] == ["zz-absent-probe-pkg"])
        check("ensure_packages returns [] for a still-missing pkg", got == [])

        # tail-append branch: when the dir is absent from sys.path it must be APPENDED
        # (bundled site-packages keeps import precedence), never insert(0)'d.
        _norm_target = _os.path.normpath(target_dir)
        _removed = [q for q in sys.path if _os.path.normpath(q) == _norm_target]
        for q in _removed:
            sys.path.remove(q)
        try:
            btk.CoreUtils.ensure_packages({"zz-absent-probe-pkg": "zz_absent_probe_pkg"})
            _idx = [i for i, q in enumerate(sys.path) if _os.path.normpath(q) == _norm_target]
            check("ensure_packages re-adds the dir at the TAIL of sys.path",
                  bool(_idx) and _idx[0] >= len(sys.path) - 1, f"idx={_idx} len={len(sys.path)}")
        finally:
            sys.path[:] = [q for q in sys.path if _os.path.normpath(q) != _norm_target]
            sys.path.extend(_removed)
    finally:
        _cu_mod.ptk.PackageManager = _real_pm

    # --- edit_mode: the Edit-Mode bracket (scope alone, then prior state back) --------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    m1 = bpy.context.view_layer.objects.active
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    m2 = bpy.context.view_layer.objects.active
    bpy.ops.curve.primitive_bezier_curve_add(location=(6, 0, 0))
    crv = bpy.context.view_layer.objects.active
    vl = bpy.context.view_layer
    for o in (m1, m2, crv):
        o.select_set(True)
    vl.objects.active = crv
    inside = {}
    with btk.CoreUtils.edit_mode([m1, m2]) as scope:
        inside["scope"] = list(scope)
        inside["modes"] = (m1.mode, m2.mode, crv.mode)
        inside["crv_selected"] = crv.select_get()
        inside["active"] = vl.objects.active
    check(
        "edit_mode: the scope alone enters Edit Mode, the first one active",
        inside["scope"] == [m1, m2]
        and inside["modes"] == ("EDIT", "EDIT", "OBJECT")
        and not inside["crv_selected"]
        and inside["active"] == m1,
        f"{inside}",
    )
    check(
        "edit_mode: prior selection, active object and Object Mode come back",
        (m1.mode, m2.mode) == ("OBJECT", "OBJECT")
        and all(o.select_get() for o in (m1, m2, crv))
        and vl.objects.active == crv,
    )
    # a prior Edit Mode on another object is re-entered on exit
    for o in vl.objects:
        o.select_set(o == m1)
    vl.objects.active = m1
    bpy.ops.object.mode_set(mode="EDIT")
    with btk.CoreUtils.edit_mode([m2]):
        inside["m1"] = m1.mode
    check(
        "edit_mode: the prior active object's own Edit Mode is left, then restored",
        inside["m1"] == "OBJECT" and m1.mode == "EDIT" and m2.mode == "OBJECT",
        f"inside={inside['m1']} after={m1.mode}/{m2.mode}",
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    # the body raising still restores, and the error reaches the caller
    try:
        with btk.CoreUtils.edit_mode([m2]):
            raise ValueError("boom")
    except ValueError:
        raised = True
    else:
        raised = False
    check(
        "edit_mode: a raising body restores the state and re-raises",
        raised and m2.mode == "OBJECT" and vl.objects.active == m1,
    )

    # the Qt-pump state: bpy.context.window is None, so context.view_layer falls back to the
    # scene's DEFAULT layer, while window_context_override enters windows[0] -- whose layer
    # the user may have switched. The prior state must be captured INSIDE the override.
    # Measured before the fix: the window layer's selected mesh entered Edit Mode beside the
    # scope (the body's operator would run on it too), that layer's selection came back as
    # the default layer's, and a curve scope left the mesh in Edit Mode.
    wins = bpy.context.window_manager.windows
    check("edit_mode (pump): precondition -- a window to override into", len(wins) > 0)
    if len(wins):
        win = wins[0]
        default_vl = bpy.context.scene.view_layers[0]
        win_vl = bpy.context.scene.view_layers.new("WindowLayer")
        prior_win_vl = win.view_layer
        win.view_layer = win_vl
        try:
            def pump_state():
                for o in default_vl.objects:
                    o.select_set(o == crv, view_layer=default_vl)
                default_vl.objects.active = crv
                for o in win_vl.objects:
                    o.select_set(o == m1, view_layer=win_vl)
                win_vl.objects.active = m1

            def win_state():
                return (
                    win_vl.objects.active,
                    [o for o in win_vl.objects if o.select_get(view_layer=win_vl)],
                )

            pump_state()
            with bpy.context.temp_override(window=None):
                outer = bpy.context.view_layer
                with btk.CoreUtils.window_context_override():
                    inner = bpy.context.view_layer
            check(
                "edit_mode (pump): precondition -- the override switches the view layer",
                outer == default_vl and inner == win_vl,
                f"outside={outer.name} inside={inner.name}",
            )
            with bpy.context.temp_override(window=None):
                with btk.CoreUtils.edit_mode([m2]):
                    inside["pump"] = (m1.mode, m2.mode)
            check(
                "edit_mode (pump): only the scope enters Edit Mode",
                inside["pump"] == ("OBJECT", "EDIT"),
                f"(m1, m2) = {inside['pump']}",
            )
            check(
                "edit_mode (pump): the window layer's selection + active come back, "
                "the default layer's are untouched",
                win_state() == (m1, [m1])
                and default_vl.objects.active == crv
                and [o for o in default_vl.objects if o.select_get(view_layer=default_vl)]
                == [crv]
                and (m1.mode, m2.mode) == ("OBJECT", "OBJECT"),
                f"window layer: {win_state()[0].name} {[o.name for o in win_state()[1]]}",
            )
            pump_state()
            with bpy.context.temp_override(window=None):
                with btk.CoreUtils.edit_mode([crv]):
                    inside["pump_crv"] = (crv.mode, m1.mode)
            check(
                "edit_mode (pump): a curve scope enters Edit Mode, and nothing is left there",
                inside["pump_crv"] == ("EDIT", "OBJECT")
                and all(o.mode == "OBJECT" for o in (m1, m2, crv)),
                f"inside (crv, m1) = {inside['pump_crv']}, after m1={m1.mode}",
            )
        finally:
            # the real window is back in context, still showing the window layer
            win_vl.objects.active = m1
            if m1.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            win.view_layer = prior_win_vl
            bpy.context.scene.view_layers.remove(win_vl)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CORE-UTILS-SELECTION===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
