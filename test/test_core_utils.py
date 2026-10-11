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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")

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

    # ---- ensure_packages: FOUND is not IMPORTABLE -------------------------------------------
    # A 4.x profile carried to 5.1 by *Load Previous Settings* brings a Pillow built for
    # CPython 3.11: ``find_spec("PIL")`` finds it (``PIL/__init__`` is pure Python) while
    # ``from PIL import Image`` fails on ``_imaging``, so ensure_image_deps reported PIL
    # available and never provisioned. Sandboxed stand-in: a package whose top level
    # imports and whose compiled-core probe does not, with its dist-info in the install
    # dir -- exactly what leaves pip's plan "already satisfied".
    import shutil as _shutil

    _sandbox = _os.path.join(HERE, "temp_tests", "_ensure_packages")
    _pkg = _os.path.join(_sandbox, "zzcarried")
    _shutil.rmtree(_sandbox, ignore_errors=True)
    _os.makedirs(_pkg)
    with open(_os.path.join(_pkg, "__init__.py"), "w") as fh:
        fh.write("")
    with open(_os.path.join(_pkg, "core.py"), "w") as fh:
        fh.write("raise ImportError('built for another Python (stand-in for _imaging)')\n")
    _stale_info = _os.path.join(_sandbox, "zz_carried-1.0.dist-info")
    _os.makedirs(_stale_info)

    class _RepairPM:
        """pip stand-in: plans only what has no dist-info, then installs a working build."""

        calls = []

        def __init__(self, python_path=None):
            pass

        def install_targeted(self, specs, target_dir, upgrade=False):
            satisfied = _os.path.isdir(_stale_info)
            type(self).calls.append((list(specs), satisfied))
            if satisfied:
                return []  # what pip does while the carried dist-info is present
            with open(_os.path.join(_pkg, "core.py"), "w") as fh:
                fh.write("OK = True\n")
            _os.makedirs(_os.path.join(target_dir, "zz_carried-2.0.dist-info"))
            return ["zz-carried==2.0"]

    _real_dirs = _cu_mod._CoreUtilsInternal._engine_install_dirs
    _probes = _cu_mod._CoreUtilsInternal.__dict__.get("_IMPORT_PROBES")
    try:
        _cu_mod.ptk.PackageManager = _RepairPM
        _cu_mod._CoreUtilsInternal._engine_install_dirs = staticmethod(
            lambda: (_sandbox, _os.path.join(_sandbox, "_legacy"))
        )
        if isinstance(_probes, dict):
            _probes["zzcarried"] = "zzcarried.core"
        sys.path.append(_sandbox)
        got = btk.CoreUtils.ensure_packages({"zz-carried": "zzcarried"})
        check(
            "ensure_packages: a found-but-unimportable package is provisioned, not "
            "reported available",
            got == ["zzcarried"]
            and _RepairPM.calls == [(["zz-carried"], False)]
            and not _os.path.isdir(_stale_info),
            f"got={got} pip calls (specs, plan-satisfied)={_RepairPM.calls}",
        )
        check(
            "ensure_packages: Pillow is proven by PIL.Image (it loads _imaging)",
            isinstance(_probes, dict) and _probes.get("PIL") == "PIL.Image",
            f"probes={_probes}",
        )
    finally:
        _cu_mod.ptk.PackageManager = _real_pm
        _cu_mod._CoreUtilsInternal._engine_install_dirs = staticmethod(_real_dirs)
        if isinstance(_probes, dict):
            _probes.pop("zzcarried", None)
        sys.path[:] = [q for q in sys.path if q != _sandbox]
        for _name in [n for n in sys.modules if n.split(".")[0] == "zzcarried"]:
            del sys.modules[_name]
        _shutil.rmtree(_sandbox, ignore_errors=True)

    # --- preserved_selection: mtk.CoreUtils.preserved_selection's mirror ---------------------
    # The selection -- and, Blender having one apart from it, the active object -- comes
    # back after a block that changed it; objects the block removed are dropped, an empty
    # prior selection comes back empty, and the restore never raises.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    pa = bpy.context.view_layer.objects.active
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    pb = bpy.context.view_layer.objects.active
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
    pc = bpy.context.view_layer.objects.active
    _vl = bpy.context.view_layer

    def _sel_state():
        return (
            _vl.objects.active,
            sorted(o.name for o in _vl.objects if o.select_get()),
        )

    _ps = getattr(btk.CoreUtils, "preserved_selection", None)
    check("preserved_selection exists (mayatk's CoreUtils mirror)", _ps is not None)
    if _ps is not None:
        for o in _vl.objects:
            o.select_set(o in (pa, pb))
        _vl.objects.active = pb
        with _ps() as captured:
            for o in _vl.objects:
                o.select_set(o == pc)
            _vl.objects.active = pc
        check(
            "preserved_selection: yields the selection and restores it + the active",
            sorted(o.name for o in captured) == sorted([pa.name, pb.name])
            and _sel_state() == (pb, sorted([pa.name, pb.name])),
            f"{_sel_state()}",
        )
        with _ps():
            bpy.data.objects.remove(pb, do_unlink=True)
            pc.select_set(True)
        check(
            "preserved_selection: an object the block removed is dropped",
            _sel_state()[1] == [pa.name],
            f"{_sel_state()}",
        )
        # link -> an operator -> unlink leaves the layer's base list due a rebuild, and
        # a select_set INSIDE a live ``view_layer.objects`` loop triggers it mid-walk:
        # measured, the relinked object was skipped and stayed selected. (Readers that
        # only build a list are unaffected -- measured the same sequence, all correct.)
        bpy.ops.mesh.primitive_cube_add(location=(9, 0, 0))  # pc mid-list, not last
        for o in _vl.objects:
            o.select_set(o == pa)
        _vl.objects.active = pa
        with _ps():
            bpy.context.scene.collection.objects.link(pc)
            pc.select_set(True)
            _vl.objects.active = pc
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.context.scene.collection.objects.unlink(pc)
        check(
            "preserved_selection: an object relinked around an operator is restored "
            "too (the layer is rebuilt before the restore walks it)",
            _sel_state() == (pa, [pa.name]),
            f"{_sel_state()}",
        )
        for o in _vl.objects:
            o.select_set(False)
        _vl.objects.active = None
        with _ps():
            pa.select_set(True)
            _vl.objects.active = pa
        check(
            "preserved_selection: an empty prior selection comes back empty",
            _sel_state() == (None, []),
            f"{_sel_state()}",
        )
        try:
            with _ps():
                pa.select_set(True)
                raise ValueError("boom")
        except ValueError:
            _raised = True
        else:
            _raised = False
        check(
            "preserved_selection: a raising body still restores, and re-raises",
            _raised and _sel_state() == (None, []),
        )

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
            # the window-independent READERS resolve the same layer the override enters:
            # measured before the fix, all three read the scene default (crv) while every
            # operator run under the override acted on the window layer's m1.
            pump_state()
            with bpy.context.temp_override(window=None):
                got = (
                    btk.CoreUtils._active_view_layer(),
                    btk.selected_objects(),
                    btk.active_object(),
                )
            check(
                "readers (pump): _active_view_layer / selected_objects / active_object "
                "resolve the window's layer, not the scene default",
                got == (win_vl, [m1], m1),
                f"layer={got[0].name} sel={[o.name for o in got[1]]} "
                f"active={getattr(got[2], 'name', None)}",
            )

            # ...and so do the engine paths that pair a layer read with a write or an
            # operator. Each case starts from pump_state() (window layer: m1 selected +
            # active; default layer: crv), runs windowless, and checks the WINDOW layer
            # got the effect while the default layer was left alone. Measured before the
            # fix: the exports restored the default layer's selection into the window's,
            # the hidden-object export dropped a window-hidden mesh, the selectors wrote
            # the default layer, and the lightmap unwrap poll-failed.
            import shutil

            art = os.path.join(HERE, "temp_tests", "_core_utils_pump")
            os.makedirs(art, exist_ok=True)
            mat = bpy.data.materials.new("PumpMat")
            m2.data.materials.clear()
            m2.data.materials.append(mat)

            def default_untouched():
                return default_vl.objects.active == crv and [
                    o for o in default_vl.objects if o.select_get(view_layer=default_vl)
                ] == [crv]

            def usd_has_m2(path):
                with open(path, encoding="utf-8") as fh:
                    return btk.UsdUtils.sanitize_prim_name(m2.name) in fh.read()

            def hide_m2_in_window():
                m2.hide_set(True, view_layer=win_vl)

            def visible_inside():
                with btk.CoreUtils.visible_override([m2]):
                    return m2.visible_get(view_layer=win_vl)

            # The lightmap panel's reads: a light in a collection EXCLUDED from the
            # window's layer only (the bake renders the window's layer), and a panel
            # over stub widgets for the Scope / Exclude reads.
            from types import SimpleNamespace as _NS

            from blendertk.light_utils.lightmap_baker import (
                lightmap_baker_slots as _lm_slots,
            )

            def window_light():
                coll = bpy.data.collections.get("PumpLights")
                if coll is None:
                    coll = bpy.data.collections.new("PumpLights")
                    bpy.context.scene.collection.children.link(coll)
                    lamp = bpy.data.objects.new(
                        "PumpLamp", bpy.data.lights.new("PumpLamp", "POINT")
                    )
                    coll.objects.link(lamp)
                win_vl.layer_collection.children[coll.name].exclude = True

            def lm_panel(scope="Visible"):
                panel = _lm_slots.LightmapBakerSlots.__new__(_lm_slots.LightmapBakerSlots)
                texts = []
                panel.ui = _NS(
                    cmb_scope=_NS(currentText=lambda: scope),
                    footer=_NS(setText=texts.append, text=lambda: texts[-1]),
                )
                panel._refresh_exclusions = lambda: None
                return panel

            def select_exclusions():
                real = _lm_slots.LightmapExcludeSet
                _lm_slots.LightmapExcludeSet = _NS(members=lambda: [m2])
                try:
                    lm_panel().select_exclusions()
                finally:
                    _lm_slots.LightmapExcludeSet = real

            def lamp_row():
                rows = btk.LightmapBaker._light_rows(bpy.context.scene)
                return next((r for r in rows if "PumpLamp" in r), "")

            def preserved_inside():
                with btk.CoreUtils.preserved_selection():
                    with btk.CoreUtils.window_context_override():
                        for o in bpy.context.view_layer.objects:
                            o.select_set(o == m2)
                        bpy.context.view_layer.objects.active = m2

            cases = [
                # name, setup, run (windowless), expectation(result) on the window layer
                (
                    "FbxUtils.export restores the window layer's selection",
                    None,
                    lambda: btk.FbxUtils.export(
                        os.path.join(art, "a.fbx"), objects=[m2]
                    ),
                    lambda r: win_state() == (m1, [m1]),
                ),
                (
                    "UsdUtils.export restores the window layer's selection",
                    None,
                    lambda: btk.UsdUtils.export(
                        os.path.join(art, "a.usda"), objects=[m2]
                    ),
                    lambda r: win_state() == (m1, [m1]),
                ),
                (
                    "UsdUtils.export(include_hidden) ships a mesh hidden in the window "
                    "layer and hides it there again",
                    hide_m2_in_window,
                    lambda: btk.UsdUtils.export(
                        os.path.join(art, "b.usda"), objects=[m2], include_hidden=True
                    ),
                    lambda r: usd_has_m2(os.path.join(art, "b.usda"))
                    and m2.hide_get(view_layer=win_vl)
                    and not m2.hide_get(view_layer=default_vl),
                ),
                (
                    "visible_override reveals in the window layer, then re-hides",
                    hide_m2_in_window,
                    visible_inside,
                    lambda r: r is True and m2.hide_get(view_layer=win_vl),
                ),
                (
                    "preserved_selection restores the window layer's selection",
                    None,
                    preserved_inside,
                    lambda r: win_state() == (m1, [m1]),
                ),
                (
                    "DisplayUtils.get_visible_geometry reads the window layer",
                    hide_m2_in_window,
                    lambda: btk.DisplayUtils.get_visible_geometry(),
                    lambda r: m1 in r and m2 not in r,
                ),
                (
                    "MatUtils.select_by_material selects in the window layer",
                    None,
                    lambda: btk.MatUtils.select_by_material(mat),
                    lambda r: win_state() == (m2, [m2]),
                ),
                (
                    "EditUtils.get_similar_mesh(select) selects in the window layer",
                    None,
                    lambda: btk.EditUtils.get_similar_mesh(
                        [m1], vertex=True, face=True, select=True
                    ),
                    lambda r: win_state() == (m2, [m2]),
                ),
                (
                    "Selection.select_by_type selects in the window layer",
                    None,
                    lambda: btk.Selection.select_by_type(
                        "Polygon Meshes", objects=[m2, crv]
                    ),
                    lambda r: win_state() == (m2, [m2]),
                ),
                (
                    "LightmapWebExport.export_glb(objects=) exports windowless, from "
                    "the window layer",
                    None,
                    lambda: btk.LightmapWebExport(
                        resolution=64, samples=1, device=None
                    ).export_glb(
                        os.path.join(art, "a.glb"), objects=[m2], texture_max_size=None
                    ),
                    lambda r: os.path.isfile(os.path.join(art, "a.glb")),
                ),
                (
                    "UvUtils.create_lightmap_uvs runs windowless and restores the "
                    "window layer's selection",
                    None,
                    lambda: btk.UvUtils.create_lightmap_uvs([m2]),
                    lambda r: r == [m2.name] and win_state() == (m1, [m1]),
                ),
                (
                    "LightmapBaker.preflight refuses a light the window layer excludes",
                    window_light,
                    lambda: btk.LightmapBaker(
                        include_environment=False, device=None
                    ).preflight(),
                    lambda r: isinstance(r, str),
                ),
                (
                    "LightmapBaker._light_rows reports the window layer's visibility",
                    window_light,
                    lamp_row,
                    lambda r: "viewport_visible=False" in r,
                ),
                (
                    "the lightmap panel's Visible scope reads the window layer",
                    hide_m2_in_window,
                    lambda: lm_panel("Visible")._scope_objects(),
                    lambda r: m1 in r and m2 not in r,
                ),
                (
                    "the lightmap panel's Select Exclusions selects in the window layer",
                    None,
                    select_exclusions,
                    lambda r: win_state() == (m2, [m2]),
                ),
            ]
            try:
                for name, setup, run, expect in cases:
                    pump_state()
                    m2.hide_set(False, view_layer=win_vl)
                    m2.hide_set(False, view_layer=default_vl)
                    if setup:
                        setup()
                    try:
                        with bpy.context.temp_override(window=None):
                            result = run()
                        ok, detail = expect(result), ""
                    except Exception as e:  # noqa: BLE001 -- a raise is a failed case
                        ok, detail = False, f"raised {e!r}"
                    act, sel = win_state()
                    check(
                        f"consumer (pump): {name}; default layer untouched",
                        ok and default_untouched(),
                        detail
                        or f"window: {getattr(act, 'name', None)} "
                        f"{[o.name for o in sel]}",
                    )
            finally:
                m2.hide_set(False, view_layer=win_vl)
                bpy.data.materials.remove(mat)
                shutil.rmtree(art, ignore_errors=True)
                _lamps = bpy.data.collections.get("PumpLights")
                if _lamps is not None:
                    for _o in list(_lamps.objects):
                        bpy.data.objects.remove(_o)
                    bpy.data.collections.remove(_lamps)

            # The windowless DEPSGRAPH: ``bpy.context.evaluated_depsgraph_get()`` is the
            # default layer's, which never evaluates a collection excluded there, and
            # ``evaluated_get`` then hands back the ORIGINAL object -- no modifiers.
            # Measured before the fix: the Scene Audit counted the subdivided cube (on
            # screen in the window's layer) at its 12 base triangles, not 48.
            coll = bpy.data.collections.new("PumpWindowOnly")
            bpy.context.scene.collection.children.link(coll)
            sub = m2.copy()
            sub.data = m2.data.copy()
            coll.objects.link(sub)
            sub.modifiers.new("Sub", "SUBSURF").levels = 1
            default_vl.layer_collection.children[coll.name].exclude = True
            try:
                dg_get = getattr(btk.CoreUtils, "_evaluated_depsgraph", None)
                with bpy.context.temp_override(window=None):
                    faces = (
                        len(sub.evaluated_get(dg_get()).data.polygons) if dg_get else None
                    )
                    audit = btk.SceneAnalyzer._collect(
                        [sub], False, collect_materials=False, collect_textures=False
                    )
                tris = [r["tris"] for r in audit["meshes"]]
                check(
                    "depsgraph (pump): the window layer's evaluated depsgraph, and the "
                    "Scene Audit reads it",
                    faces == 24 and tris == [48],
                    f"evaluated faces={faces} (24 subdivided, 6 raw) audit tris={tris}",
                )
                # the Marmoset cage's unit match measures the EVALUATED target: an
                # Array of the subdivided cube spans ~3.36, the raw cube 2
                sub.modifiers.new("Arr", "ARRAY").count = 2
                with bpy.context.temp_override(window=None):
                    lo, hi = btk.MarmosetBridge._world_bounds([sub])
                width = (hi[0] - lo[0]) if lo else None
                check(
                    "depsgraph (pump): the Marmoset bridge's world bounds are the "
                    "window layer's evaluated mesh",
                    width is not None and width > 3.0,
                    f"width={width} (~3.36 evaluated, 2 raw)",
                )
            finally:
                bpy.data.objects.remove(sub)
                bpy.data.collections.remove(coll)
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

    # --- static guard: no view-layer access outside the window override -----------------
    # The runtime cases above pin a sample; this pins every site. Windowless,
    # ``context.view_layer`` and a bare ``select_set`` / ``select_get`` / ``hide_set`` /
    # ``hide_get`` / ``visible_get`` address the scene's DEFAULT layer, while the operators
    # run under ``window_context_override`` (and ``selected_objects`` / ``active_object``)
    # act on the window's. So such an access sits inside the override (a ``with`` block,
    # or a function decorated with it or ``_object_mode``) or passes ``view_layer=``;
    # the allowlist names each deliberate exception and why. The same holds for the
    # screen-context members (``bpy.context.selected_objects`` / ``active_object`` /
    # ``object`` / ``selected_editable_objects``: empty or ABSENT windowless -- read
    # ``CoreUtils.selected_objects()`` / ``active_object()``) and for
    # ``bpy.context.evaluated_depsgraph_get()`` (the default layer's depsgraph, which
    # never evaluates a collection excluded there -- use ``CoreUtils._evaluated_depsgraph()``).
    import ast

    _GUARDS = (
        "window_context_override",
        "temp_override",
        "edit_mode",
        "_object_mode",
        "_active_mode",
    )
    _LAYER_CALLS = ("select_set", "select_get", "hide_set", "hide_get", "visible_get")
    _SCREEN = ("selected_objects", "active_object", "object", "selected_editable_objects")
    _ALLOW = {
        ("core_utils/_core_utils.py", "visible_override"): "reveals on the context's "
        "AND the window's layer on purpose",
        ("core_utils/diagnostics/scene_audit.py", "_visible"): "explicit view_layer "
        "param; bare only as its None fallback",
        ("display_utils/outliner_tint.py", "_draw_impl"): "draw callback: runs with "
        "its window in context",
        ("edit_utils/target_weld.py", "_edit_meshes"): "modal operator's own context",
        ("edit_utils/target_weld.py", "_do_weld"): "modal operator's own context",
        ("edit_utils/target_weld.py", "_source_xy"): "modal operator's own context",
        ("edit_utils/selection.py", "_on_depsgraph"): "the None probe for a render "
        "handler context; the read goes through _active_view_layer",
        ("edit_utils/selection.py", "_safe_select_set"): "context-relative helper: "
        "select_by_type runs it under the override",
        ("edit_utils/selection.py", "_hide_get_safe"): "context-relative helper "
        "(select_by_type)",
        ("edit_utils/selection.py", "_select_uv_overlap"): "context-relative helper "
        "(select_by_type)",
        ("edit_utils/selection.py", "_apply_selection_mode"): "context-relative helper "
        "(select_by_type)",
        ("edit_utils/_edit_utils.py", "_apply_modifier"): "only called from "
        "@_object_mode functions",
        ("edit_utils/_edit_utils.py", "_join_copies"): "only called from "
        "@_object_mode combine_objects",
        ("env_utils/usd.py", "_reveal"): "context-relative: export runs it under its "
        "override, bake_transform_caches evaluates in the context's layer",
        ("env_utils/usd.py", "_restore_hidden"): "context-relative (see _reveal)",
        ("env_utils/usd.py", "hidden_objects"): "context-relative (see _reveal)",
        ("mat_utils/texture_baker.py", "_bake_one"): "only called from bake(), under "
        "its override",
        ("uv_utils/_auto_unwrap.py", "_restore_mode"): "run() only, under its "
        "override",
    }

    def _dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def _layer_access(node):
        """A view-layer access that resolves the CONTEXT's layer, or None."""
        if isinstance(node, ast.Attribute) and node.attr == "view_layer":
            return "context.view_layer" if _dotted(node.value).endswith("context") else None
        if isinstance(node, ast.Attribute) and node.attr in _SCREEN:
            return f"bpy.context.{node.attr}" if _dotted(node.value) == "bpy.context" else None
        if not isinstance(node, ast.Call):
            return None
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "evaluated_depsgraph_get"
            and _dotted(node.func.value) == "bpy.context"
        ):
            return "bpy.context.evaluated_depsgraph_get()"
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and getattr(node.args[1], "value", None) in _SCREEN
            and _dotted(node.args[0]) == "bpy.context"
        ):
            return f"getattr(bpy.context, {node.args[1].value!r})"
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and getattr(node.args[1], "value", None) == "view_layer"
            and _dotted(node.args[0]).endswith("context")
        ):
            return "getattr(context, 'view_layer')"
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _LAYER_CALLS
            and not any(k.arg == "view_layer" for k in node.keywords)
        ):
            return node.func.attr
        return None

    def _unguarded(tree):
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            kind = _layer_access(node)
            parent = parents.get(node)
            if kind is None or (  # ``view_layer.update()`` evaluates, never selects
                isinstance(parent, ast.Attribute) and parent.attr == "update"
            ):
                continue
            func, guarded, cur = None, False, node
            while cur in parents and not guarded:
                cur = parents[cur]
                if isinstance(cur, ast.With):
                    guarded = any(
                        g in ast.unparse(i.context_expr) for i in cur.items for g in _GUARDS
                    )
                elif isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func = func or cur.name
                    guarded = any(
                        g in ast.unparse(d) for d in cur.decorator_list for g in _GUARDS
                    )
            if not guarded:
                yield func, node.lineno, kind

    pkg_root = os.path.join(REPO, "blendertk")
    stray, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        # templates run in their own headless Blender, where the context has a window
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "templates")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            rel = os.path.relpath(path, pkg_root).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for func, lineno, kind in _unguarded(tree):
                if (rel, func) in _ALLOW:
                    seen.add((rel, func))
                else:
                    stray.append(f"{rel}:{lineno} {func}() {kind}")
    check(
        "static: every view-layer access runs under the window override "
        f"({len(seen)} allowlisted sites)",
        not stray,
        "; ".join(stray[:12]) + (f" (+{len(stray) - 12} more)" if len(stray) > 12 else ""),
    )
    check(
        "static: no stale allowlist entries",
        set(_ALLOW) <= seen,
        "; ".join(f"{r}:{f}" for r, f in sorted(set(_ALLOW) - seen)),
    )

    # A select_set / hide_set INSIDE a live ``<view_layer>.objects`` loop rebuilds the
    # base list mid-walk when a collection link changed around an operator earlier in
    # the callback, and the walk skips the relinked object (measured; see the
    # preserved_selection case). Such loops walk a ``list(...)`` of the objects.
    live = []
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "templates")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                it = getattr(node, "iter", None) if isinstance(node, ast.For) else None
                if isinstance(it, ast.IfExp):
                    it = it.body
                layer = getattr(it, "value", None)
                if not (
                    isinstance(it, ast.Attribute)
                    and it.attr == "objects"
                    and (  # a path through a layer, or the resolver's own call
                        "view_layer" in _dotted(layer)
                        or _dotted(layer) == "vl"
                        or isinstance(layer, ast.Call)
                        and _dotted(layer.func).endswith("_active_view_layer")
                    )
                ):
                    continue
                writes = any(
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("select_set", "hide_set")
                    for stmt in node.body
                    for n in ast.walk(stmt)
                )
                if writes:
                    rel = os.path.relpath(path, pkg_root).replace(os.sep, "/")
                    live.append(f"{rel}:{node.lineno}")
    check(
        "static: no select_set / hide_set inside a live view_layer.objects loop",
        not live,
        "; ".join(live),
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CORE-UTILS-SELECTION===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
