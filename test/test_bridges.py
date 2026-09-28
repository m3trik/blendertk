"""blendertk bridge feature test: export_selection_fbx + RizomUVBridge send-script / discovery.

Run: blender --background --factory-startup --python blendertk/test/test_bridges.py

Covers the export-and-hand-off foundation shared by the Substance / Marmoset / RizomUV bridges
(the actual app launch is not exercised — it would open RizomUV / Painter / Toolbag).
"""

import sys
import os
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
# uitk: a full ``send()`` reads the bridge's parameter registry (uitk.bridge's
# Qt-free AttributeSpec), which the Marmoset bake-source checks drive.
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk
    from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    tmp = tempfile.mkdtemp(prefix="btk_bridge_")

    # ---- export_selection_fbx -----------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    other = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)  # prior selection = {cube}

    out = os.path.join(tmp, "sel.fbx")
    written = btk.FbxUtils.export_selection_fbx(filepath=out, objects=[cube])
    check(
        "export_selection_fbx writes the file",
        written == out and os.path.isfile(out) and os.path.getsize(out) > 0,
    )
    check(
        "export_selection_fbx restores the prior selection",
        cube.select_get() and not other.select_get(),
    )

    bpy.ops.object.select_all(action="DESELECT")
    try:
        btk.FbxUtils.export_selection_fbx(filepath=os.path.join(tmp, "empty.fbx"))
        check("export_selection_fbx with nothing selected -> RuntimeError", False)
    except RuntimeError:
        check("export_selection_fbx with nothing selected -> RuntimeError", True)

    default_path = btk.FbxUtils.export_selection_fbx(objects=[cube])
    check(
        "export_selection_fbx default temp path",
        default_path.endswith("_bridge.fbx") and os.path.isfile(default_path),
    )
    os.remove(default_path)

    # ---- windowless (Qt event-pump timer) context ----------------------------
    # tentacle drives the bridge slots from a bpy.app.timers callback where
    # bpy.context.window is None: bpy.context.selected_objects raises AttributeError
    # (and io_scene_fbx's exporter reads it internally). Before the fix, the export
    # aborted with "Nothing selected to export." even with a valid selection + output
    # dir -- the reported Substance/Marmoset bug. FbxUtils.export now reads selection
    # window-independently and runs the operators under window_context_override().
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)  # prior selection = {cube}
    win_out = os.path.join(tmp, "windowless.fbx")
    with bpy.context.temp_override(window=None):
        win_written = btk.FbxUtils.export_selection_fbx(
            filepath=win_out, objects=[cube]
        )
    check(
        "export_selection_fbx works with context.window=None (Qt-timer state)",
        win_written == win_out
        and os.path.isfile(win_out)
        and os.path.getsize(win_out) > 0,
    )
    check(
        "export_selection_fbx restores selection from the windowless state",
        cube.select_get() and not other.select_get(),
    )

    # objects given but all unresolvable -> guard raises, but the prior selection is restored
    # (the guard lives inside the try/finally, so DESELECT doesn't strand the caller's selection).
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    try:
        btk.FbxUtils.export_selection_fbx(
            filepath=os.path.join(tmp, "unresolved.fbx"), objects=["__nope__"]
        )
        check("export_selection_fbx unresolved objects -> RuntimeError", False)
    except RuntimeError:
        check("export_selection_fbx unresolved objects -> RuntimeError", True)
    check(
        "export_selection_fbx restores selection after a guard raise",
        cube.select_get() and not other.select_get(),
    )

    # ---- Maya MEL FBX option names (vendored templates) ----------------------
    # The Substance/Marmoset templates are vendored verbatim from mayatk and carry Maya MEL FBX
    # names (FBXExportEmbeddedTextures) that mean nothing to Blender's export_scene.fbx. FbxUtils
    # must translate them, else the export faults with 'keyword "FBXExport..." unrecognized'.
    from blendertk.env_utils.fbx_utils import FbxUtils

    t = FbxUtils._translate_fbx_options(
        {"FBXExportEmbeddedTextures": True, "use_tspace": True}
    )
    check(
        "_translate_fbx_options maps FBXExportEmbeddedTextures -> Blender kwargs",
        t == {"use_tspace": True, "embed_textures": True, "path_mode": "COPY"},
        str(t),
    )
    t2 = FbxUtils._translate_fbx_options({"FBXExportEmbeddedTextures": False})
    check(
        "_translate_fbx_options False embed -> embed_textures False, no COPY",
        t2 == {"embed_textures": False},
        str(t2),
    )
    t3 = FbxUtils._translate_fbx_options(
        {"FBXExportSomethingMayaOnly": True, "embed_textures": True}
    )
    check(
        "_translate_fbx_options drops unmapped FBXExport* Maya-only names",
        t3 == {"embed_textures": True},
        str(t3),
    )

    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    maya_out = btk.FbxUtils.export_selection_fbx(
        filepath=os.path.join(tmp, "maya_opt.fbx"),
        objects=[cube],
        FBXExportEmbeddedTextures=True,  # the exact key from templates/import.py
    )
    check(
        "export_selection_fbx accepts a Maya MEL FBX name (no 'unrecognized keyword')",
        os.path.isfile(maya_out) and os.path.getsize(maya_out) > 0,
    )

    # A typo'd *Blender* kwarg must still error loudly (translation only touches FBXExport* keys).
    try:
        btk.FbxUtils.export_selection_fbx(
            filepath=os.path.join(tmp, "typo.fbx"),
            objects=[cube],
            not_a_real_kwarg=True,
        )
        check("real Blender-kwarg typo still errors (not silently dropped)", False)
    except (TypeError, RuntimeError):
        check("real Blender-kwarg typo still errors (not silently dropped)", True)

    # The *real* merged payload each bridge sends: engine _DEFAULT_FBX_OPTIONS (Blender-native) +
    # the template's embed option. Both bugs (windowless selection, Maya FBX name) blocked every
    # prior export, so this default set had never actually reached export_scene.fbx -- lock in that
    # every key is a valid kwarg.
    from blendertk.mat_utils.substance_bridge._substance_bridge import (
        _DEFAULT_FBX_OPTIONS as _SUB_FBX,
    )
    from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
        _DEFAULT_FBX_OPTIONS as _MAR_FBX,
    )

    # ---- the USD carrier on the bake bridges (mirror of mayatk's pins) --------
    # The panel renders the Format combo only where the active template echoes
    # ``__CARRIER__``, so the engine's ``carriers`` and every send template must
    # agree; the USD option set must actually export through Blender's operator.
    import re as _re

    from blendertk.mat_utils.substance_bridge import _substance_bridge as _sub_mod
    from blendertk.mat_utils.marmoset_bridge import _marmoset_bridge as _mar_mod
    from blendertk.env_utils.maya_bridge import _maya_bridge as _maya_mod

    for _mod, _cls, _templates in (
        (_maya_mod, "MayaBridge", ("import",)),
        (_mar_mod, "MarmosetBridge", ("bake", "import", "lookdev")),
        (_sub_mod, "SubstanceBridge", ("import", "bake_lighting")),
    ):
        check(
            f"{_cls} offers fbx then usd",
            getattr(_mod, _cls).carriers == ("fbx", "usd"),
        )
        for _stem in _templates:
            _txt = (_mod._TEMPLATE_DIR / f"{_stem}.py").read_text(encoding="utf-8")
            check(
                f"{_cls} {_stem}.py echoes __CARRIER__ (surfaces the Format combo)",
                bool(_re.search(r"__CARRIER__", _txt)),
            )
    for _name, _opts in (
        ("substance", _sub_mod._DEFAULT_USD_OPTIONS),
        ("marmoset", _mar_mod._DEFAULT_USD_OPTIONS),
    ):
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        _u = os.path.join(tmp, f"{_name}_payload.usd")
        try:
            btk.UsdUtils.export_selection_usd(filepath=_u, objects=[cube], **_opts)
            check(
                f"{_name} _DEFAULT_USD_OPTIONS payload exports",
                os.path.isfile(_u) and os.path.getsize(_u) > 0,
            )
        except Exception as e:  # noqa: BLE001
            check(f"{_name} _DEFAULT_USD_OPTIONS payload exports", False, repr(e))

    for _name, _defaults, _extra in (
        (
            "substance",
            _SUB_FBX,
            {"FBXExportEmbeddedTextures": True},
        ),  # templates/import.py
        ("marmoset", _MAR_FBX, {}),
    ):
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        _merged = dict(_defaults)
        _merged.update(_extra)
        _p = os.path.join(tmp, f"{_name}_payload.fbx")
        try:
            btk.FbxUtils.export_selection_fbx(filepath=_p, objects=[cube], **_merged)
            check(
                f"{_name} _DEFAULT_FBX_OPTIONS payload exports",
                os.path.isfile(_p) and os.path.getsize(_p) > 0,
            )
        except Exception as e:
            check(f"{_name} _DEFAULT_FBX_OPTIONS payload exports", False, repr(e))

    # ---- RizomUVBridge.build_send_script --------------------------------------
    rb = RizomUVBridge()
    script = rb.build_send_script(
        "C:/tmp/mesh.fbx",
        load_uvs=True,
        import_groups=False,
        load_uvw_props=True,
        load_textures=False,
    )
    check(
        "build_send_script: ZomLoad with forward-slashed path",
        'ZomLoad({File={Path="C:/tmp/mesh.fbx"' in script,
    )
    check(
        "build_send_script: Lua booleans map the toggles",
        "XYZUVW=true" in script
        and "ImportGroups=false" in script
        and "UVWProps=true" in script,
    )
    check(
        "build_send_script: no texture block when disabled",
        "ZomLoadTexture" not in script,
    )

    # textured object -> a pcall-wrapped ZomLoadTexture per on-disk texture
    img_path = os.path.join(tmp, "TexA_Diffuse.png")
    gen = bpy.data.images.new("_g", 4, 4)
    gen.filepath_raw = img_path
    gen.file_format = "PNG"
    gen.save()
    bpy.data.images.remove(gen)
    mat = btk.create_mat("standard", name="RZ")
    nt = mat.node_tree
    img = bpy.data.images.load(img_path)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    btk.assign_mat([cube], mat)
    script_t = rb.build_send_script(
        "C:/tmp/mesh.fbx", objects=[cube], load_textures=True
    )
    check(
        "build_send_script: pcall ZomLoadTexture for existing texture",
        "ZomLoadTexture" in script_t
        and "pcall(function()" in script_t
        and "TexA_Diffuse.png" in script_t.replace("\\", "/"),
    )

    # ---- marmoset hand-off scratch lifetime ---------------------------------
    # A bake ROUNDTRIP consumes its own hand-off artifacts (Toolbag runs
    # blocking, the maps are relocated beside the .blend), so they must stage
    # in a temp dir the run then removes -- not beside the .blend, where they
    # used to silt up the project. A send_to's Toolbag reads them AFTER we
    # return, so those must survive. Added: 2026-08-18
    import pythontk as ptk

    from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
        MarmosetBridge as _MarBridge,
        ROUND_TRIP as _RT,
        SEND_TO as _ST,
    )

    def _request(mode):
        return ptk.HandoffRequest(template="bake", mode=mode, params={}, extras={})

    # The maps are the roundtrip's ONE durable output and get a destination of
    # their own beside the .blend -- so the hand-off dir is free to be scratch.
    check(
        "baked_texture_dir is empty while the .blend is unsaved",
        _MarBridge.baked_texture_dir() == "",
        repr(_MarBridge.baked_texture_dir()),
    )
    _blend = os.path.join(tmp, "asset.blend")
    bpy.ops.wm.save_as_mainfile(filepath=_blend)
    check(
        "baked_texture_dir is <blend dir>/baked_textures once saved",
        os.path.normcase(_MarBridge.baked_texture_dir())
        == os.path.normcase(
            os.path.join(tmp, _MarBridge.BAKED_TEXTURE_SUBDIR).replace("\\", "/")
        ),
        _MarBridge.baked_texture_dir(),
    )

    _bridge = _MarBridge()
    _bridge.logger.setLevel("CRITICAL")

    check(
        "a roundtrip stages scoped, a send_to detached",
        _bridge._scratch_policy(_request(_RT)) == "scoped"
        and _bridge._scratch_policy(_request(_ST)) == "detached",
    )

    rt_req = _request(_RT)
    rt_dir = _bridge._scratch_dir(rt_req, "handoff")
    check(
        "roundtrip scratch is a real dir under the system temp dir",
        os.path.isdir(rt_dir)
        and os.path.normcase(rt_dir).startswith(
            os.path.normcase(tempfile.gettempdir())
        ),
        rt_dir,
    )
    # Every dir the run opens shares ONE root, so one cleanup takes them all.
    rt_stage = _bridge._scratch_dir(rt_req, "asset_staging")
    check(
        "a run's scratch dirs share one root",
        os.path.dirname(rt_stage) == os.path.dirname(rt_dir),
        f"{rt_dir} | {rt_stage}",
    )
    _bridge._discard_scratch(rt_req, {})
    check(
        "a clean roundtrip takes its whole scratch away",
        not os.path.exists(rt_dir) and not os.path.exists(rt_stage),
        rt_dir,
    )

    # The guard: with the .blend unsaved the maps land IN the scratch, so it
    # must survive -- deleting it would destroy the bake. Routed through the
    # real ``_delivered_paths``, which is what reads the result dict.
    keep_req = _request(_RT)
    keep_dir = _bridge._scratch_dir(keep_req, "handoff")
    _bridge._discard_scratch(
        keep_req,
        {"outputs": [os.path.join(keep_dir, "MAT_Base_Color.tga")], "texture_dir": ""},
    )
    check("scratch holding the run's output is kept", os.path.isdir(keep_dir), keep_dir)
    __import__("shutil").rmtree(os.path.dirname(keep_dir), ignore_errors=True)

    # send_to hands its files to a DETACHED Toolbag: nothing may delete them.
    st_req = _request(_ST)
    st_dir = _bridge._scratch_dir(st_req, "handoff")
    _bridge._discard_scratch(st_req, {})
    check("send_to hand-off artifacts outlive the call", os.path.isdir(st_dir), st_dir)

    # ---- exe discovery: graceful, never raises ------------------------------
    resolved = RizomUVBridge().rizom_path
    check(
        "rizom_path returns None or a str (no raise)",
        resolved is None or isinstance(resolved, str),
        f"{resolved}",
    )

    # ---- mayatk's Blender-side templates vendor a blendertk reader ----------
    # The mirror of the guard blendertk's own templates get from mayatk's suite.
    # mayatk's conversion templates run in whatever Blender IT finds, which may have
    # no blendertk, so they carry a dependency-free COPY of the scene-clock reader --
    # allowed only while a test proves it still matches its source (CODE_STANDARD 6:
    # a drift-GUARDED copy is the one sanctioned duplicate). Executed from its AST,
    # never imported: an unrendered template is not importable at all.
    import ast as _ast

    import bpy as _bpy

    from blendertk.env_utils._env_utils import EnvUtils as _EnvUtils

    _scene = _bpy.context.scene
    _scene.render.fps, _scene.render.fps_base = 30, 1.0
    _scene.frame_start, _scene.frame_end = 1, 90
    _scene.use_preview_range = True  # Blender's inner range == Maya's playback range
    _scene.frame_preview_start, _scene.frame_preview_end = 10, 40
    _scene.frame_current = 12
    _want_clock = _EnvUtils.scene_settings()
    check(
        "scene clock reader itself reads the preview range as the playback range",
        _want_clock["fps"] == 30.0
        and (_want_clock["frame_start"], _want_clock["frame_end"]) == (10, 40)
        and (_want_clock["anim_start"], _want_clock["anim_end"]) == (1, 90),
        str(_want_clock),
    )
    _mtk_templates = os.path.join(
        MONO, "mayatk", "mayatk", "env_utils", "blender_bridge", "templates"
    )
    for _name in ("_import_scene.py", "_import_scene_usd.py"):
        _path = os.path.join(_mtk_templates, _name)
        if not os.path.isfile(_path):
            # NOT a pass for the comparison: name what actually happened, so an
            # absent sibling can never read as "the copies match".
            check(
                f"mayatk/{_name}: sibling checkout absent, clock-copy guard NOT run",
                True,
                _path,
            )
            continue
        with open(_path, encoding="utf-8") as _fh:
            _module = _ast.parse(_fh.read())
        _fn = next(
            (
                n
                for n in _module.body
                if isinstance(n, _ast.FunctionDef) and n.name == "scene_settings"
            ),
            None,
        )
        if _fn is None:
            check(
                f"mayatk/{_name}: scene_settings copy matches EnvUtils",
                False,
                "template lost scene_settings()",
            )
            continue
        # Imports + literal constants only: a rendering placeholder is not a literal.
        _prelude = []
        for _node in _module.body:
            if isinstance(_node, (_ast.Import, _ast.ImportFrom)):
                _prelude.append(_node)
            elif isinstance(_node, _ast.Assign):
                try:
                    _ast.literal_eval(_node.value)
                except (ValueError, TypeError, SyntaxError):
                    continue
                _prelude.append(_node)
        _ns = {}
        exec(
            compile(_ast.Module(body=_prelude + [_fn], type_ignores=[]), _path, "exec"),
            _ns,
        )
        _got_clock = _ns["scene_settings"](_bpy)
        check(
            f"mayatk/{_name}: scene_settings copy matches EnvUtils (no hand-keeping)",
            _got_clock == _want_clock,
            f"{_got_clock} != {_want_clock}",
        )

    # The guard set the clock to non-default values to prove the readers agree on
    # them; put it back, so a check appended after this one cannot silently inherit
    # 30 fps and an enabled preview range. The file's `reset()` clears objects and
    # materials, never the scene clock.
    _scene.use_preview_range = False
    _scene.render.fps = 24

    # ---- mayatk's pull template: what the FBX exporter drops ships as an Empty -----
    # Blender's FBX exporter writes no node for a hair-type Curves object -- what its
    # USD importer makes of every Maya control curve -- so the Maya side lost the
    # object, its keys and its place in the hierarchy. The template gives it an
    # Empty of the same name that follows it; run here, where bpy exists.
    _mtk_fbx_template = os.path.join(_mtk_templates, "_import_scene.py")
    if not os.path.isfile(_mtk_fbx_template):
        # Neither OK nor FAIL: a check that cannot run must not count as a
        # pass (the runner tallies OK/FAIL lines; a SKIP line is neither).
        lines.append(
            "SKIP mayatk/_import_scene.py: sibling checkout absent, stand-in "
            f"NOT run | {_mtk_fbx_template}"
        )
    else:
        import pythontk as _ptk

        with open(_mtk_fbx_template, encoding="utf-8") as _fh:
            _module = _ast.parse(_fh.read())
        _wanted = ("stand_in_dropped_objects",)
        _body = [
            n
            for n in _module.body
            if isinstance(n, (_ast.Import, _ast.ImportFrom))
            or (isinstance(n, _ast.FunctionDef) and n.name in _wanted)
            or (
                isinstance(n, _ast.Assign)
                and any(
                    isinstance(t, _ast.Name) and t.id == "FBX_DROPPED_TYPES"
                    for t in n.targets
                )
            )
        ]
        _ns = {}
        exec(
            compile(
                _ast.Module(body=_body, type_ignores=[]), _mtk_fbx_template, "exec"
            ),
            _ns,
        )
        _bpy.ops.wm.read_factory_settings(use_empty=True)
        _scene = _bpy.context.scene
        _scene.frame_start, _scene.frame_end = 1, 10
        _col = _scene.collection
        _root = _bpy.data.objects.new("si_root", None)
        _col.objects.link(_root)
        _hair_data = _bpy.data.hair_curves.new("si_ctrl")
        _hair_data.add_curves([2])
        _hair = _bpy.data.objects.new("si_ctrl", _hair_data)
        _col.objects.link(_hair)
        _hair.parent = _root
        _hair["note"] = "authored"
        for _frame, _y in ((1, 0.0), (10, 3.0)):
            _hair.location = (0.0, _y, 0.0)
            _hair.keyframe_insert("location", frame=_frame)
        _under = _bpy.data.objects.new("si_under", None)
        _col.objects.link(_under)
        _under.parent = _hair
        _under.location = (1.0, 0.0, 0.0)
        _follower = _bpy.data.objects.new("si_follower", None)
        _col.objects.link(_follower)
        _follower.constraints.new("COPY_LOCATION").target = _hair

        def _worlds():
            out = {}
            for _frame in (1, 5, 10):
                _scene.frame_set(_frame)
                out[_frame] = {
                    o.name.split("__")[0]: [list(r) for r in o.matrix_world]
                    for o in _scene.objects
                }
            return out

        _before = _worlds()
        _named = _ns["stand_in_dropped_objects"](_bpy)
        _stand_in = _bpy.data.objects.get("si_ctrl")
        _after = _worlds()
        check(
            "mayatk pull template: a hair Curves control gets an Empty of its name",
            _named == ["si_ctrl"]
            and _stand_in is not None
            and _stand_in.type == "EMPTY"
            and _stand_in.parent == _root
            and _under.parent == _stand_in
            and _stand_in.get("note") == "authored",
            f"{_named} {_stand_in and _stand_in.type}",
        )
        _moved = [
            (_frame, _name)
            for _frame in _before
            for _name in ("si_ctrl", "si_under", "si_follower")
            if max(
                abs(a - b)
                for ra, rb in zip(_before[_frame][_name], _after[_frame][_name])
                for a, b in zip(ra, rb)
            )
            > 1e-6
        ]
        check(
            "mayatk pull template: the stand-in, its child and a follower keep their "
            "world motion at every frame",
            not _moved,
            str(_moved),
        )
        _fbx = os.path.join(tmp, "stand_in.fbx")
        _bpy.ops.export_scene.fbx(
            filepath=_fbx,
            use_custom_props=True,
            add_leaf_bones=False,
            bake_anim=True,
            bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False,
        )
        _file = _ptk.FbxFile.load(_fbx, raw_payloads=False)
        _models = {}
        _kinds = {}
        for _rec in _file.iter_objects():
            _props = _rec["props"]
            if _props and isinstance(_props[0], int):
                _kinds[_props[0]] = _rec["name"]
                if _rec["name"] == "Model":
                    _models[_props[0]] = _ptk.FbxFile._display_name(_props[1])
        _parents = {}
        _animated = set()
        for _kind, _child, _parent, _prop in _file.connections():
            if _child in _models and _parent in _models:
                _parents[_models[_child]] = _models[_parent]
            if _parent in _models and _kinds.get(_child) == "AnimationCurveNode":
                _animated.add(_models[_parent])
        check(
            "mayatk pull template: the FBX ships the stand-in, parented and keyed, "
            "and never the source",
            _parents.get("si_ctrl") == "si_root"
            and _parents.get("si_under") == "si_ctrl"
            and "si_ctrl" in _animated
            and not any("__fbx_stand_in_source" in m for m in _models.values()),
            f"{sorted(_models.values())} parents={_parents} animated={sorted(_animated)}",
        )

    # ---- the Substance bake source ships its HIDDEN members too ----------------
    # Blender's FBX export is selection-based and drops whatever it cannot select,
    # so a high-poly mesh hidden by its own flags or by its collection left the
    # bake source without it. The set's collection used to reveal the
    # collection-hidden ones as a side effect -- in the viewport and the render
    # too -- until it became render-neutral (``bake_sets.BakeSet``); the export
    # reveals its members for the write alone and puts every flag back.
    import pythontk as _hp_ptk
    from blendertk.mat_utils.bake_sets import BakeSourceSet
    from blendertk.mat_utils.substance_bridge._substance_bridge import (
        SubstanceBridge,
    )

    def _fbx_models(path):
        """The Model names an FBX on disk carries (empty when there is no file)."""
        found = set()
        if path and os.path.isfile(path):
            for _rec in _hp_ptk.FbxFile.load(path, raw_payloads=False).iter_objects():
                _props = _rec["props"]
                if _rec["name"] == "Model" and _props and isinstance(_props[0], int):
                    found.add(_hp_ptk.FbxFile._display_name(_props[1]))
        return found

    reset()
    hp_home = bpy.data.collections.new("hp_hidden_home")
    bpy.context.scene.collection.children.link(hp_home)
    hp_objs = []
    for i, name in enumerate(("hp_shown", "hp_flagged", "hp_in_hidden")):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        hp_objs.append(bpy.context.active_object)
        hp_objs[-1].name = name
    hp_shown, hp_flagged, hp_in_hidden = hp_objs
    hp_flagged.hide_viewport = True
    hp_flagged.hide_set(True)
    for c in list(hp_in_hidden.users_collection):
        c.objects.unlink(hp_in_hidden)
    hp_home.objects.link(hp_in_hidden)
    hp_home.hide_viewport = True
    hp_home.hide_render = True
    BakeSourceSet.define(hp_objs)
    hp_written = SubstanceBridge()._export_bake_source(
        os.path.join(tmp, "hp_asset.fbx"),
        dict(_SUB_FBX),
        {"BAKE_SOURCE_SET"},
        _hp_ptk.HandoffRequest(),
    )
    hp_models = _fbx_models(hp_written)
    check(
        "the Substance bake source ships its hidden members too",
        {"hp_shown", "hp_flagged", "hp_in_hidden"} <= hp_models,
        f"{hp_written} -> {sorted(hp_models)}",
    )
    # One companion name across both bridges and both DCCs (mayatk's
    # ``BakeSourceSet.companion_path``); Blender's Substance send used to write
    # ``<stem>_high.fbx`` while every other one wrote ``<stem>_source.fbx``.
    check(
        "the Substance bake source lands at <stem>_source.fbx",
        os.path.normcase(str(hp_written))
        == os.path.normcase(os.path.join(tmp, "hp_asset_source.fbx")),
        str(hp_written),
    )
    check(
        "...and the export leaves every hide flag as it found it",
        hp_flagged.hide_viewport
        and hp_flagged.hide_get()
        and hp_home.hide_viewport
        and hp_home.hide_render
        and not hp_in_hidden.visible_get()
        and hp_in_hidden.name not in bpy.context.scene.collection.objects,
    )
    BakeSourceSet.clear()

    # A member that GROUPS the source ships what is under it. Blender's FBX
    # writes exactly the objects it is handed, never a subtree, so the bare
    # member list sent an Empty to Painter and none of its meshes (Maya's
    # exporter writes the subtree, which is what mayatk's twin relies on).
    reset()
    hp_grp = bpy.data.objects.new("hp_grp", None)
    bpy.context.scene.collection.objects.link(hp_grp)
    bpy.ops.mesh.primitive_cube_add()
    hp_grp_child = bpy.context.active_object
    hp_grp_child.name = "hp_grp_child"
    hp_grp_child.parent = hp_grp
    BakeSourceSet.define([hp_grp])
    hp_grp_models = _fbx_models(
        SubstanceBridge()._export_bake_source(
            os.path.join(tmp, "hp_grp.fbx"),
            dict(_SUB_FBX),
            {"BAKE_SOURCE_SET"},
            _hp_ptk.HandoffRequest(),
        )
    )
    check(
        "a grouping member ships the meshes under it to Painter",
        "hp_grp_child" in hp_grp_models,
        str(sorted(hp_grp_models)),
    )
    BakeSourceSet.clear()

    # ---- BakeSourceSet: one bake-source set, legacy files adopted -----------
    # Mirror of mayatk's ``bake_sets.BakeSourceSet`` (canonical
    # ``bakeBridge_source``). A .blend saved while the set was the Substance
    # bridge's ``HighPolySet`` carries a collection stamped
    # ``btk_substance_high_poly``: it must read transparently, migrate on the
    # next define, and go on a clear -- a file never keeps two competing sets.
    # Added: 2026-09-27
    try:
        import warnings as _bs_warnings

        reset()
        bpy.ops.mesh.primitive_cube_add()
        bs_a = bpy.context.active_object
        bs_a.name = "bs_a"
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        bs_b = bpy.context.active_object
        bs_b.name = "bs_b"

        def _legacy_set(members):
            col = bpy.data.collections.new("substanceBridge_highPoly")
            col["btk_substance_high_poly"] = True
            bpy.context.scene.collection.children.link(col)
            for obj in members:
                col.objects.link(obj)
            return col

        def _stamped():
            return [
                c
                for c in bpy.data.collections
                if "btk_substance_high_poly" in c or BakeSourceSet.STAMP in c
            ]

        _legacy_set([bs_a])
        check(
            "a legacy HighPolySet collection reads as the bake source",
            BakeSourceSet.exists()
            and [o.name for o in BakeSourceSet.members()] == ["bs_a"],
            str([o.name for o in BakeSourceSet.members()]),
        )
        BakeSourceSet.define([bs_b])
        _cols = _stamped()
        check(
            "define migrates the legacy collection to the canonical set",
            len(_cols) == 1
            and _cols[0].name == BakeSourceSet.SET_NAME == "bakeBridge_source"
            and BakeSourceSet.STAMP in _cols[0]
            and "btk_substance_high_poly" not in _cols[0]
            and [o.name for o in BakeSourceSet.members()] == ["bs_b"],
            str([(c.name, list(c.keys())) for c in _cols]),
        )
        _legacy_set([bs_a])  # a stray legacy beside the canonical one
        BakeSourceSet.clear()
        check(
            "clear removes the canonical AND a stray legacy set; members stay",
            not BakeSourceSet.exists()
            and not _stamped()
            and bs_a.name in bpy.context.scene.objects
            and bs_b.name in bpy.context.scene.objects,
            str([c.name for c in _stamped()]),
        )
        # The class name is public API: kept one release as a warned alias.
        with _bs_warnings.catch_warnings(record=True) as _caught:
            _bs_warnings.simplefilter("always")
            from blendertk.mat_utils.substance_bridge._substance_bridge import (
                HighPolySet as _HighPolySet,
            )
        check(
            "HighPolySet is a deprecated alias of BakeSourceSet",
            _HighPolySet is BakeSourceSet
            and any(issubclass(w.category, DeprecationWarning) for w in _caught),
            str([str(w.message) for w in _caught]),
        )
    except Exception as e:  # noqa: BLE001
        check("BakeSourceSet storage + legacy adoption", False, repr(e))
        lines.append(traceback.format_exc())

    # ---- the Marmoset bake send ships the Bake Source set --------------------
    # Set From Selection stored the set and told the artist the next send would
    # ship it, while ``_produce`` exported the scope alone and paired by name
    # suffix only: the set did nothing (measured: a set + a selected target
    # wrote scene.fbx with the target, no companion, SOURCE_MODEL_FILE = "").
    # Mirror of mayatk's test_split_bake_objects_routes_high_set_members /
    # test_bake_produce_exports_high_companion. Added: 2026-09-27
    try:
        from unittest import mock as _mm_mock

        from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
            MarmosetBridge as _MmBridge,
            SEND_TO as _MM_SEND_TO,
        )

        reset()
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        mm_tgt = bpy.context.active_object
        mm_tgt.name = "mm_tgt"
        # A plain member the artist has hidden -- the usual state of a bake
        # source while the target is being worked on.
        bpy.ops.mesh.primitive_cube_add(size=2.4)
        mm_src = bpy.context.active_object
        mm_src.name = "mm_src"
        # A member that only GROUPS source geometry.
        mm_grp = bpy.data.objects.new("mm_src_grp", None)
        bpy.context.scene.collection.objects.link(mm_grp)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.3)
        mm_child = bpy.context.active_object
        mm_child.name = "mm_src_child"
        mm_child.parent = mm_grp
        # The source's textured material: the surface-transfer bake samples it,
        # so it has to reach the manifest.
        _mm_img = os.path.join(tmp, "MmSrc_BaseColor.png")
        _gen = bpy.data.images.new("_mm", 4, 4)
        _gen.filepath_raw = _mm_img
        _gen.file_format = "PNG"
        _gen.save()
        bpy.data.images.remove(_gen)
        mm_mat = btk.create_mat("standard", name="MM_SRC_MAT")
        _nt = mm_mat.node_tree
        _tex = _nt.nodes.new("ShaderNodeTexImage")
        _tex.image = bpy.data.images.load(_mm_img)
        _bsdf = next(n for n in _nt.nodes if n.type == "BSDF_PRINCIPLED")
        _nt.links.new(_tex.outputs["Color"], _bsdf.inputs["Base Color"])
        btk.assign_mat([mm_src], mm_mat)
        mm_src.hide_viewport = True
        mm_src.hide_set(True)
        BakeSourceSet.define([mm_src, mm_grp])

        mm_out = os.path.join(tmp, "mm_out")

        class _MmErrors(__import__("logging").Handler):
            """Collects the bridge's ERROR records (its logger is the panel's log)."""

            def __init__(self):
                super().__init__(level=40)
                self.messages = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        def _mm_send(objects, template="bake"):
            """One send_to, launch stubbed; returns ({file: path}, [error messages]).

            The launch stub answers ``None``, so every send ends on the engine's
            "Could not launch" error -- AFTER ``_produce`` has written what it
            writes, which is all these checks read.
            """
            __import__("shutil").rmtree(mm_out, ignore_errors=True)
            errors = _MmErrors()
            with _mm_mock.patch(
                "blendertk.mat_utils.marmoset_bridge._marmoset_engine"
                ".AppLauncher.launch",
                return_value=None,
            ):
                bridge = _MmBridge(toolbag_path="not-used.exe")
                bridge.logger.setLevel("ERROR")
                bridge.logger.addHandler(errors)
                try:
                    bridge.send(
                        objects=objects,
                        output_dir=mm_out,
                        output_name="scene",
                        template=template,
                        mode=_MM_SEND_TO,
                    )
                finally:
                    bridge.logger.removeHandler(errors)
            files = (
                {f: os.path.join(mm_out, f) for f in os.listdir(mm_out)}
                if os.path.isdir(mm_out)
                else {}
            )
            return files, errors.messages

        bpy.ops.object.select_all(action="DESELECT")
        mm_tgt.select_set(True)
        _files, _errs = _mm_send([mm_tgt])
        _primary = _fbx_models(_files.get("scene.fbx"))
        _companion = _fbx_models(_files.get("scene_source.fbx"))
        check(
            "a bake send ships the Bake Source set as <base>_source.fbx",
            {"mm_src", "mm_src_child"} <= _companion and "mm_tgt" not in _companion,
            f"{sorted(_files)} -> {sorted(_companion)}",
        )
        check(
            "...and the scoped target alone as <base>.fbx",
            "mm_tgt" in _primary and not {"mm_src", "mm_src_child"} & _primary,
            str(sorted(_primary)),
        )
        _script = _files.get("scene_bake_send_to.py")
        _script_txt = (
            open(_script, encoding="utf-8").read() if _script else ""
        ).replace("\\", "/")
        check(
            "the rendered bake script imports the companion into the High side",
            'SOURCE_MODEL_FILE = r"' in _script_txt
            and "scene_source.fbx" in _script_txt,
            next(
                (ln for ln in _script_txt.splitlines() if "SOURCE_MODEL_FILE =" in ln),
                "",
            ),
        )
        check(
            "the two-file flow writes no name-suffix pairs sidecar",
            "scene.bake_pairs.json" not in _files,
            str(sorted(_files)),
        )
        import json as _mm_json

        _manifest = {}
        if "scene.materials.json" in _files:
            with open(_files["scene.materials.json"], encoding="utf-8") as fh:
                _manifest = _mm_json.load(fh)
        check(
            "the manifest carries the source's material (the transfer samples it)",
            "MM_SRC_MAT" in (_manifest.get("materials") or {}),
            str(sorted(_manifest.get("materials") or {})),
        )
        # The auto cage is measured source -> target, keyed by source mesh.
        check(
            "the auto cage is measured from the set's meshes to the target",
            "mm_src_child"
            in _script_txt.split("CAGE_STANDOFFS =", 1)[-1].split("\n", 1)[0],
            next(
                (ln for ln in _script_txt.splitlines() if "CAGE_STANDOFFS =" in ln),
                "",
            ),
        )
        check(
            "the companion export leaves hide flags and selection as it found them",
            mm_src.hide_viewport
            and mm_src.hide_get()
            and [o.name for o in btk.selected_objects()] == ["mm_tgt"],
            str([o.name for o in btk.selected_objects()]),
        )

        # "Entire Scene" hands the set's members in with the target.
        _files, _errs = _mm_send([mm_tgt, mm_src, mm_grp, mm_child])
        _primary = _fbx_models(_files.get("scene.fbx"))
        check(
            "a scope holding the source too never ships it on the target side",
            "mm_tgt" in _primary
            and not {"mm_src", "mm_src_child", "mm_src_grp"} & _primary
            and {"mm_src", "mm_src_child"}
            <= _fbx_models(_files.get("scene_source.fbx")),
            str(sorted(_primary)),
        )

        _files, _errs = _mm_send([mm_src])
        check(
            "a scope of set members only is refused (there is no bake target)",
            "scene.fbx" not in _files and any("no bake target" in m for m in _errs),
            f"{sorted(_files)} {_errs}",
        )
        # A member under an unrelated group Empty: the scope closure adds that
        # Empty as an ancestor and it stays on the target side, but an Empty is
        # no bake target -- the send shipped it alone as scene.fbx. mayatk drops
        # a member's ancestors, so its scope was refused. Added: 2026-09-27
        mm_holder = bpy.data.objects.new("mm_holder", None)
        bpy.context.scene.collection.objects.link(mm_holder)
        mm_src.parent = mm_holder
        try:
            _files, _errs = _mm_send([mm_src])
        finally:
            mm_src.parent = None
            bpy.data.objects.remove(mm_holder, do_unlink=True)
        check(
            "...and so is a member under a group Empty (an Empty is no bake target)",
            "scene.fbx" not in _files and any("no bake target" in m for m in _errs),
            f"{sorted(_files)} {_errs}",
        )

        # Only the bake splits: lookdev ships its scope as it always has.
        _files, _errs = _mm_send([mm_tgt], template="lookdev")
        check(
            "a non-bake send ignores the Bake Source set",
            "scene_source.fbx" not in _files and "scene.fbx" in _files,
            str(sorted(_files)),
        )
        BakeSourceSet.clear()
    except Exception as e:  # noqa: BLE001
        check("Marmoset bake send ships the Bake Source set", False, repr(e))
        lines.append(traceback.format_exc())

    # ---- a scoped GROUP ships the subtree it names ----------------------------
    # Blender's FBX writes exactly the objects it is handed, never their
    # children, so a send of a selected Empty shipped the Empty alone (measured:
    # Marmoset bake + lookdev, Substance import and the RizomUV send each wrote
    # only `grp` for a group of two meshes). Maya's export-selection writes the
    # subtree; the texture bridges now close the scope over the hierarchy the
    # way the export-mixin bridges already did. Added: 2026-09-27
    try:
        import json as _gs_json

        from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
            MarmosetBridge as _GsMarmoset,
        )

        reset()
        gs_grp = bpy.data.objects.new("gs_grp", None)
        bpy.context.scene.collection.objects.link(gs_grp)
        gs_sub = bpy.data.objects.new("gs_sub", None)
        bpy.context.scene.collection.objects.link(gs_sub)
        gs_sub.parent = gs_grp
        for _name, _parent, _x in (("gs_a", gs_grp, 0), ("gs_b", gs_sub, 3)):
            bpy.ops.mesh.primitive_cube_add(location=(_x, 0, 0))
            _obj = bpy.context.active_object
            _obj.name = _name
            _obj.parent = _parent
        # A textured material on the NESTED mesh: the manifest must reach it too.
        _gs_img = os.path.join(tmp, "GsMat_BaseColor.png")
        _gen = bpy.data.images.new("_gs", 4, 4)
        _gen.filepath_raw = _gs_img
        _gen.file_format = "PNG"
        _gen.save()
        bpy.data.images.remove(_gen)
        gs_mat = btk.create_mat("standard", name="GS_MAT")
        _nt = gs_mat.node_tree
        _tex = _nt.nodes.new("ShaderNodeTexImage")
        _tex.image = bpy.data.images.load(_gs_img)
        _bsdf = next(n for n in _nt.nodes if n.type == "BSDF_PRINCIPLED")
        _nt.links.new(_tex.outputs["Color"], _bsdf.inputs["Base Color"])
        btk.assign_mat([bpy.data.objects["gs_b"]], gs_mat)
        _gs_tree = {"gs_grp", "gs_sub", "gs_a", "gs_b"}

        def _gs_read(folder):
            manifest = {}
            path = os.path.join(folder, "scene.materials.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    manifest = _gs_json.load(fh)
            return (
                _fbx_models(os.path.join(folder, "scene.fbx")),
                sorted(manifest.get("materials") or {}),
            )

        for _template in ("bake", "lookdev"):
            _dir = os.path.join(tmp, f"gs_mar_{_template}")
            _bridge = _GsMarmoset(toolbag_path="not-used.exe")
            _bridge.logger.setLevel("CRITICAL")
            _bridge._produce(
                [gs_grp],
                _hp_ptk.HandoffRequest(
                    template=_template,
                    mode="send_to",
                    params=_bridge.merge_params({}),
                    extras={"output_dir": _dir, "output_name": "scene"},
                ),
            )
            _models, _mats = _gs_read(_dir)
            check(
                f"a Marmoset {_template} send of a group ships its subtree",
                _gs_tree <= _models and "GS_MAT" in _mats,
                f"{sorted(_models)} mats={_mats}",
            )

        _sub_bridge = SubstanceBridge()
        _sub_bridge.logger.setLevel("CRITICAL")
        _dir = os.path.join(tmp, "gs_sub")
        _req = _hp_ptk.HandoffRequest(
            template="import",
            mode="send_to",
            params={},
            extras={"output_dir": _dir, "output_name": "scene", "target": "new"},
        )
        if _sub_bridge._preflight([gs_grp], _req):
            _sub_bridge._produce([gs_grp], _req)
        _models, _mats = _gs_read(_dir)
        check(
            "a Substance send of a group ships its subtree",
            _gs_tree <= _models and "GS_MAT" in _mats,
            f"{sorted(_models)} mats={_mats}",
        )

        # Visible Only ships no hidden child, and the pairs sidecar must agree
        # with the file: it classified the group's HIDDEN `_source` child (which
        # never shipped) while the export left it out.
        _hid = bpy.data.objects["gs_b"]
        _hid.name = "gs_hidden_source"
        _hid.hide_set(True)
        _dir = os.path.join(tmp, "gs_visible")
        _bridge = _GsMarmoset(toolbag_path="not-used.exe")
        _bridge.logger.setLevel("CRITICAL")
        _bridge._produce(
            [gs_grp, gs_sub, bpy.data.objects["gs_a"]],
            _hp_ptk.HandoffRequest(
                template="bake",
                mode="send_to",
                params=_bridge.merge_params({"SCOPE": "visible"}),
                extras={"output_dir": _dir, "output_name": "scene"},
            ),
        )
        _pairs = {}
        _pairs_path = os.path.join(_dir, "scene.bake_pairs.json")
        if os.path.isfile(_pairs_path):
            with open(_pairs_path, encoding="utf-8") as fh:
                _pairs = _gs_json.load(fh)
        check(
            "Visible Only: the pairs sidecar classifies only what shipped",
            "gs_hidden_source" not in _fbx_models(os.path.join(_dir, "scene.fbx"))
            and "gs_hidden_source" not in _pairs,
            str(_pairs),
        )
    except Exception as e:  # noqa: BLE001
        check("a scoped group ships its subtree", False, repr(e))
        lines.append(traceback.format_exc())

    # ---- a Marmoset bake roundtrip puts the baked maps back ---------------------
    # The panel's Assign Material row did nothing in Blender: the bridge had no
    # ``_deliver``, so a roundtrip left the maps on disk and the scene as it was.
    # Mirror of mayatk's delivery half (``_assign_baked_materials``, the
    # re-bake-stable names, the filing aliases, the packed-map staging); the
    # Toolbag leg is stubbed here and proven live. Added: 2026-09-27
    try:
        import json as _bk_json
        from unittest import mock as _bk_mock

        from blendertk.mat_utils.game_shader import GameShader as _BkGameShader
        from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
            MarmosetBridge as _BkBridge,
            ROUND_TRIP as _BK_RT,
        )

        reset()
        bk_maps = os.path.join(tmp, "bk_maps")
        os.makedirs(bk_maps, exist_ok=True)

        def _bk_png(name, rgb):
            path = os.path.join(bk_maps, name)
            img = bpy.data.images.new("_bk", 4, 4)
            img.pixels[:] = [c / 255.0 for c in rgb + (255,)] * 16
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            bpy.data.images.remove(img)
            return path

        bk_outputs = [
            _bk_png("BK_A_Base_Color.png", (200, 40, 40)),
            _bk_png("BK_A_Normal_OpenGL.png", (128, 128, 255)),
            _bk_png("BK_B_Base_Color.png", (40, 200, 40)),
        ]
        bpy.ops.mesh.primitive_cube_add()
        bk_tgt = bpy.context.active_object
        bk_tgt.name = "bk_tgt"
        for _mname in ("BK_A", "BK_B"):
            bk_tgt.data.materials.append(btk.create_mat("standard", name=_mname))
        for _i, _poly in enumerate(bk_tgt.data.polygons):
            _poly.material_index = 0 if _i < 3 else 1

        def _bk_slots(obj):
            return [s.material.name if s.material else None for s in obj.material_slots]

        def _bk_images(mat):
            return sorted(
                os.path.basename(n.image.filepath)
                for n in (mat.node_tree.nodes if mat and mat.node_tree else [])
                if n.type == "TEX_IMAGE" and n.image
            )

        def _bk_deliver(assignments, aliases=None, packing=None, assign=True):
            """``_deliver`` of a bake roundtrip whose Toolbag leg returns *bk_outputs*."""
            bridge = _BkBridge(toolbag_path="not-used.exe")
            bridge.logger.setLevel("CRITICAL")
            warnings_seen = []
            bridge.logger.warning = lambda msg, *a, **k: warnings_seen.append(
                str(msg) % a if a else str(msg)
            )
            bridge.deliverer.deliver = lambda b, p, r: {
                "outputs": list(bk_outputs),
                "texture_dir": bk_maps,
            }
            request = _hp_ptk.HandoffRequest(
                template="bake",
                mode=_BK_RT,
                params={"ASSIGN_MATERIAL": assign},
                extras={
                    "output_name": "scene",
                    "bake_assignments": assignments,
                    "texture_set_aliases": aliases or {},
                    "source_packing": packing or {},
                },
            )
            return bridge._deliver(_hp_ptk.Payload(primary="unused.fbx"), request), (
                warnings_seen
            )

        bk_assign = _BkBridge._material_assignments([bk_tgt])
        check(
            "the export records which target meshes wore which material",
            bk_assign == {"BK_A": ["bk_tgt"], "BK_B": ["bk_tgt"]},
            str(bk_assign),
        )

        _res, _ = _bk_deliver(bk_assign, assign=False)
        check(
            "Assign Material off leaves the scene alone",
            "materials" not in (_res or {}) and _bk_slots(bk_tgt) == ["BK_A", "BK_B"],
            str(_bk_slots(bk_tgt)),
        )

        _calls = []
        _real_create = _BkGameShader.create_network

        def _spy_create(self, textures, *a, **kw):
            _calls.append((kw.get("name"), kw))
            return _real_create(self, textures, *a, **kw)

        with _bk_mock.patch.object(_BkGameShader, "create_network", _spy_create):
            _res, _ = _bk_deliver(bk_assign, packing={"BK_A": "MSAO", "BK_B": "ORM"})
        first_a = bpy.data.materials.get("BK_A_BAKED")
        check(
            "a bake roundtrip builds <mat>_BAKED per texture set, slot by slot",
            (_res or {}).get("materials")
            == {"BK_A": "BK_A_BAKED", "BK_B": "BK_B_BAKED"}
            and _bk_slots(bk_tgt) == ["BK_A_BAKED", "BK_B_BAKED"],
            f"{(_res or {}).get('materials')} {_bk_slots(bk_tgt)}",
        )
        check(
            "...each wired from its own set's maps",
            "BK_A_Base_Color.png" in _bk_images(first_a)
            and "BK_B_Base_Color.png"
            in _bk_images(bpy.data.materials.get("BK_B_BAKED"))
            and "BK_B_Base_Color.png" not in _bk_images(first_a),
            f"{_bk_images(first_a)}",
        )
        check(
            "...restoring each source's packed layout (MSAO / ORM)",
            any(n == "BK_A_BAKED" and kw.get("mask_map") for n, kw in _calls)
            and any(n == "BK_B_BAKED" and kw.get("orm_map") for n, kw in _calls)
            and not any(n == "BK_B_BAKED" and kw.get("mask_map") for n, kw in _calls),
            str(
                [(n, sorted(k for k, v in kw.items() if v is True)) for n, kw in _calls]
            ),
        )

        # A re-bake: the meshes now wear <mat>_BAKED, the maps are filed under
        # the source name (the aliases), and the rebuild REPLACES the material.
        first_ptr = first_a.as_pointer()
        _again = _BkBridge._material_assignments([bk_tgt])
        _aliases = _BkBridge.texture_set_aliases(_again)
        _res, _ = _bk_deliver(_again, aliases=_aliases)
        _now = bpy.data.materials.get("BK_A_BAKED")
        check(
            "a re-bake replaces <mat>_BAKED instead of stacking a .001",
            _aliases == {"BK_A_BAKED": "BK_A", "BK_B_BAKED": "BK_B"}
            and _bk_slots(bk_tgt) == ["BK_A_BAKED", "BK_B_BAKED"]
            and _now is not None
            and _now.as_pointer() != first_ptr
            and not [
                m.name for m in bpy.data.materials if m.name.startswith("BK_A_BAKED.")
            ],
            f"{_bk_slots(bk_tgt)} {[m.name for m in bpy.data.materials]}",
        )

        # A partial re-bake: another mesh still wears the earlier BK_B_BAKED,
        # so it stays (Blender would strip that mesh) and the rebuild says so.
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        bk_other = bpy.context.active_object
        bk_other.name = "bk_other"
        bk_other.data.materials.append(bpy.data.materials["BK_B_BAKED"])
        _again = _BkBridge._material_assignments([bk_tgt])
        _res, _warned = _bk_deliver(
            _again, aliases=_BkBridge.texture_set_aliases(_again)
        )
        check(
            "a partial re-bake keeps the earlier material another mesh still wears",
            _bk_slots(bk_other) == ["BK_B_BAKED"]
            and _bk_slots(bk_tgt)[1] not in (None, "BK_B_BAKED")
            and any("still worn" in w for w in _warned),
            f"other={_bk_slots(bk_other)} tgt={_bk_slots(bk_tgt)} {_warned}",
        )

        # The export half: a source reading a PACKED map gets it unpacked for
        # Toolbag (which samples whole images per field), and the layout is
        # recorded for the rewire above.
        reset()
        _orm = _bk_png("BkSrc_ORM.png", (255, 128, 0))
        bpy.ops.mesh.primitive_cube_add(size=2.2)
        bk_src = bpy.context.active_object
        bk_src.name = "bk_src_source"
        _src_mat = btk.create_mat("standard", name="BK_SRC")
        _nt = _src_mat.node_tree
        _tex = _nt.nodes.new("ShaderNodeTexImage")
        _tex.image = bpy.data.images.load(_orm)
        _bsdf = next(n for n in _nt.nodes if n.type == "BSDF_PRINCIPLED")
        _nt.links.new(_tex.outputs["Color"], _bsdf.inputs["Roughness"])
        btk.assign_mat([bk_src], _src_mat)
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        bk_low = bpy.context.active_object
        bk_low.name = "bk_low"
        btk.assign_mat([bk_low], btk.create_mat("standard", name="BK_LOW_BAKED"))
        _dir = os.path.join(tmp, "bk_produce")
        _bridge = _BkBridge(toolbag_path="not-used.exe")
        _bridge.logger.setLevel("CRITICAL")
        _req = _hp_ptk.HandoffRequest(
            template="bake",
            mode=_BK_RT,
            params=_bridge.merge_params({"AUTO_CAGE": False}),
            extras={"output_dir": _dir, "output_name": "scene"},
        )
        _bridge._produce([bk_low, bk_src], _req)
        with open(os.path.join(_dir, "scene.materials.json"), encoding="utf-8") as fh:
            _rough = (
                (_bk_json.load(fh).get("materials") or {}).get("BK_SRC") or {}
            ).get("roughness", "")
        check(
            "a packed source map is unpacked for Toolbag and its layout recorded",
            _req.extras.get("source_packing") == {"BK_SRC": "ORM"}
            and _rough
            and "_staging" in _rough.replace("\\", "/")
            and not _rough.endswith("BkSrc_ORM.png"),
            f"{_req.extras.get('source_packing')} roughness={_rough}",
        )
        check(
            "...and the export records the assignments and the filing aliases",
            _req.extras.get("bake_assignments")
            == {"BK_LOW_BAKED": ["bk_low"], "BK_SRC": ["bk_src_source"]}
            and _req.extras.get("texture_set_aliases") == {"BK_LOW_BAKED": "BK_LOW"},
            f"{_req.extras.get('bake_assignments')} {_req.extras.get('texture_set_aliases')}",
        )
        _bridge._discard_scratch(_req, {})
    except Exception as e:  # noqa: BLE001
        check("a Marmoset bake roundtrip puts the baked maps back", False, repr(e))
        lines.append(traceback.format_exc())

    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
