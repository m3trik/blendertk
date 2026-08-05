"""blendertk MayaSceneImport feature test (Qt-free; bpy optional).

Run: blender --background --factory-startup --python blendertk/test/test_scene_import.py
Also runs under the workspace ``.venv`` (the bpy-dependent import step is stubbed).

Covers the pull-direction engine: template hygiene (underscore-hidden, renders to
valid Python, judged-by-artifact contract), mayapy derivation from the discovered
maya.exe, input validation, and the convert -> import -> cleanup orchestration with
the mayapy run and the FBX import stubbed. The live conversion is exercised by the
gated end-to-end check (requires Maya + a license), not here.
"""

import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import pythontk as ptk
    from blendertk.env_utils.maya_bridge import _maya_bridge as mb
    from blendertk.env_utils.maya_bridge._scene_import import (
        MayaSceneImport,
        _IMPORT_TEMPLATE,
    )

    # ---- template hygiene ----------------------------------------------------
    check("template file exists", _IMPORT_TEMPLATE.is_file(), str(_IMPORT_TEMPLATE))
    check(
        "underscore template hidden from the bridge panel",
        "_import_scene" not in {p.stem for p in mb.MayaBridge.list_templates()},
    )
    txt = _IMPORT_TEMPLATE.read_text()
    check(
        "template: standalone.initialize before cmds",
        txt.index("maya.standalone") < txt.index("import maya.cmds"),
    )
    check(
        "template: judged-by-artifact contract (os._exit, no teardown)",
        "os._exit(0)" in txt and "FBXExport -f" in txt,
    )
    check(
        "template: HardEdges explicitly off (dense-mesh hang)",
        "FBXExportHardEdges -v false" in txt,
    )
    # Texture fidelity (live user report): FBX carries only the classic
    # Lambert/Phong material model — modern surface shaders must be translated
    # before export or their textures silently drop.
    check(
        "template: modern shaders translated to FBX-safe phong",
        "fbx_safe_materials" in txt
        and all(
            t in txt for t in ("standardSurface", "aiStandardSurface", "openPBRSurface")
        ),
    )
    # Live user report #2: StingrayPBS (ShaderFX) exports as a Maya|TEX_* property
    # set Blender ignores — needs its own translation branch (different attrs).
    check(
        "template: StingrayPBS translated (color/normal/emissive maps)",
        "StingrayPBS" in txt
        and "TEX_color_map" in txt
        and "use_color_map" in txt
        and "TEX_normal_map" in txt,
    )
    check(
        "template: full-fidelity flag battery (whole-scene semantics)",
        all(
            f in txt
            for f in (
                "FBXExportInstances -v true",
                "FBXExportSkins -v true",
                "FBXExportShapes -v true",
                "FBXExportCameras -v true",
                "FBXExportLights -v true",
                "FBXExportEmbeddedTextures",
            )
        ),
    )
    check(
        "template: per-flag tolerance (a missing FBX command must not abort)",
        "FBX flag skipped" in txt,
    )
    # Packed maps (Metallic_Smoothness / MSAO / ORM) have no FBX slot at all —
    # they travel via a manifest sidecar the Blender side rebuilds from
    # (btk.create_pbr_material, the game_shader engine).
    check(
        "template: texture manifest written beside the FBX",
        "write_manifest" in txt
        and ".manifest.json" in txt
        and "STINGRAY_TEX_SLOTS" in txt,
    )
    # Production fixes (live report: pink materials + _fbxsafeN duplicates):
    check(
        "template: scene's Maya project opened before converting",
        "_resolve_workspace" in txt
        and "workspace.mel" in txt
        and "openWorkspace" in txt,
    )
    check(
        "template: one phong per source material (SG-sharing memoized)",
        "translated = {}" in txt and "mat in translated" in txt,
    )
    # Live report: the "_fbxsafe" marker became the Blender material's NAME, was
    # saved into the .blend, and rode back to Maya on the next send
    # ("REF_x_fbxsafe1"). The phong takes over the source material's name
    # instead (source renamed aside), so the FBX itself carries true names --
    # no dependence on a successful manifest rebuild to undo a marker.
    check(
        "template: translated phong claims the source material's name",
        'name=f"{_ns_safe(mat)}_fbxsafe"' not in txt
        and 'cmds.rename(mat, "{}_src".format(leaf))' in txt,
    )
    check(
        "template: SG-sharing memo keyed by the post-rename node name",
        "translated[source] = (phong, entry)" in txt,
    )

    # ---- rendering -----------------------------------------------------------
    eng = MayaSceneImport(maya_path="X:/fake/bin/maya.exe")
    script = eng.render_script(
        r"C:\scenes\test scene.ma",
        r"C:\tmp\out.fbx",
        via="fbx",
        embed_textures=False,
        include_animation=True,
    )
    check("render: no placeholders left", "__" + "SRC_PATH" + "__" not in script)
    check(
        "render: forward-slashed paths substituted",
        'r"C:/scenes/test scene.ma"' in script and "C:/tmp/out.fbx" in script,
    )
    check(
        "render: bools are Python literals",
        "EMBED_TEXTURES = False" in script and "INCLUDE_ANIMATION = True" in script,
    )
    try:
        compile(script, "_import_scene_rendered.py", "exec")
        check("render: compiles as valid Python", True)
    except SyntaxError as e:
        check("render: compiles as valid Python", False, repr(e))

    # ---- smart_bake: optional SmartBake pre-pass (template + wiring) ----------
    from blendertk.env_utils.maya_bridge._scene_import import _smart_bake_syspath

    check(
        "template: smart-bake placeholder + guarded mayatk import + probe/bake fns",
        "SMART_BAKE = __" + "SMART_BAKE" + "__" in txt
        and "from mayatk.anim_utils.smart_bake._smart_bake import SmartBake" in txt
        and "def _detect_complex_anim" in txt
        and "def _run_smart_bake" in txt,
    )
    check(
        "template: smart-bake degrades to the plain bake without mayatk",
        "mayatk unavailable" in txt and "plain FBX bake" in txt,
    )
    check(
        "template: smart-bake gated by INCLUDE_ANIMATION + cheap probe (or True force)",
        "SMART_BAKE and INCLUDE_ANIMATION" in txt
        and "SMART_BAKE is True or _detect_complex_anim(cmds)" in txt,
    )
    check(
        "template: bake reuses mayatk SmartBake (not a reimplementation)",
        "SmartBake(" in txt
        and "bake_inherited_visibility=True" in txt
        and "restorable=False" in txt,  # throwaway conversion scene
    )
    # Blender's FBX importer drops visibility animation outright (verified live:
    # even directly-keyed vis arrives as nothing) — baked visibility must travel
    # in the ONE conversion manifest's ``visibility`` section (not a second
    # sidecar) and be replayed Blender-side.
    check(
        "template: baked visibility rides the single manifest (Blender drops FBX vis)",
        "def _collect_baked_visibility" in txt
        and "def write_manifest" in txt
        and '"visibility": visibility' in txt
        and "visibility_curves" in txt
        and ".vis.json" not in txt,  # merged — no second sidecar
    )
    import inspect as _inspect

    import_src = _inspect.getsource(MayaSceneImport.import_scene)
    check(
        "import_scene replays the manifest's visibility section (non-fatal contract)",
        "_apply_visibility_manifest" in import_src
        and "manifest_path" in import_src
        and ".vis.json" not in import_src,
    )
    from blendertk.env_utils.maya_bridge._scene_import import _BAKE_TEMPLATE

    bake_txt = _BAKE_TEMPLATE.read_text()
    check(
        "bake template replays visibility through the SHARED engine method",
        "def apply_visibility" in bake_txt
        and "_apply_visibility_manifest" in bake_txt
        and 'SRC_FILE + ".manifest.json"' in bake_txt
        and ".vis.json" not in bake_txt,
    )
    # venv (no bpy): the replay must degrade silently, never raise. Uses the merged
    # schema — visibility beside materials in one file.
    import json as _json

    merged = os.path.join(tempfile.gettempdir(), "btk_vis_test.manifest.json")
    with open(merged, "w") as f:
        _json.dump(
            {
                "version": 1,
                "materials": [],
                "visibility": {"CUBE": [[1, 1], [10, 0]]},
            },
            f,
        )
    try:
        MayaSceneImport()._apply_visibility_manifest(merged, [])
        check("visibility replay degrades gracefully without bpy", True)
    except Exception as e:
        check("visibility replay degrades gracefully without bpy", False, repr(e))
    finally:
        os.remove(merged)
    try:
        MayaSceneImport()._apply_visibility_manifest("X:/no/such.manifest.json", [])
        check("visibility replay tolerates a missing/unreadable manifest", True)
    except Exception as e:
        check("visibility replay tolerates a missing/unreadable manifest", False, repr(e))
    # Frame alignment (live-verified bug): Blender's FBX importer shifts every
    # imported curve by anim_offset (default 1.0), so raw-Maya-frame visibility must
    # be shifted by the SAME amount or it desyncs a frame from the transforms.
    check(
        "visibility replay shifts by the FBX importer's anim_offset (frame alignment)",
        "frame + frame_offset" in _inspect.getsource(
            MayaSceneImport._apply_visibility_manifest
        )
        and 'get("anim_offset", 1.0)' in import_src,
    )

    # scene_has_complex_animation — the cheap .ma text probe that lets the Reference
    # Manager prompt bake-vs-raw without launching Maya. Mirrors the Maya-side
    # _detect_complex_anim node-type signals (constraints / SDK / expr / IK / motion
    # path) plus keyed visibility (Maya's <node>_visibility curve).
    def _write(name, body):
        p = os.path.join(tempfile.gettempdir(), name)
        with open(p, "w") as f:
            f.write("//Maya ASCII 2025 scene\n" + body)
        return p

    ma_constraint = _write(
        "btk_cx_con.ma", 'createNode pointConstraint -n "c1_pointConstraint1";\n'
    )
    ma_sdk = _write("btk_cx_sdk.ma", 'createNode animCurveUL -n "drivenKey1";\n')
    ma_vis = _write(
        "btk_cx_vis.ma", 'createNode animCurveTU -n "LOC_parent_visibility";\n'
    )
    ma_static = _write(
        "btk_cx_static.ma",
        'createNode transform -n "cube";\ncreateNode mesh -n "cubeShape" -p "cube";\n',
    )
    ma_plainkey = _write(
        "btk_cx_plain.ma", 'createNode animCurveTL -n "cube_translateX";\n'
    )
    try:
        scan = MayaSceneImport.scene_has_complex_animation
        check("scan: constraint -> complex", scan(ma_constraint) is True)
        check("scan: set-driven key (animCurveU*) -> complex", scan(ma_sdk) is True)
        check("scan: keyed visibility (_visibility curve) -> complex", scan(ma_vis) is True)
        check("scan: plain keyframes only -> not complex", scan(ma_plainkey) is False)
        check("scan: static scene -> not complex", scan(ma_static) is False)
        check(
            "scan: .fbx (already baked) -> not complex",
            scan(ma_constraint[:-3] + ".fbx") is False,  # nonexistent .fbx path
        )
        check("scan: missing file -> not complex", scan("X:/nope.ma") is False)
    finally:
        for p in (ma_constraint, ma_sdk, ma_vis, ma_static, ma_plainkey):
            os.remove(p)

    # .mb probe: node type names are stored as plain byte strings in the binary,
    # so a chunked token scan gives .mb the same bake-vs-raw signal .ma gets
    # (previously a blind False). Heuristic by contract: a false positive only
    # costs an unnecessary bake attempt (the Maya-side probe re-decides).
    def _write_mb(name, payload):
        p = os.path.join(tempfile.gettempdir(), name)
        with open(p, "wb") as f:
            f.write(payload)
        return p

    mb_con = _write_mb(
        "btk_cx_con.mb", b"\x00\x01FOR4junk parentConstraint junk\x02"
    )
    mb_vis = _write_mb("btk_cx_vis.mb", b"FOR4 animCurveTU LOC_parent_visibility")
    mb_plain = _write_mb(
        "btk_cx_plain.mb", b"FOR4 transform mesh animCurveTL cube_translateX"
    )
    try:
        check("mb scan: constraint token -> complex", scan(mb_con) is True)
        check("mb scan: visibility-curve token -> complex", scan(mb_vis) is True)
        check("mb scan: plain keys/static tokens -> not complex", scan(mb_plain) is False)
        # A token straddling a chunk boundary must still match (overlap reads).
        straddle = _write_mb("btk_cx_straddle.mb", b"AAAAA" + b"parentConstraint")
        try:
            check(
                "mb scan: token straddling a chunk boundary matches",
                MayaSceneImport._mb_declares_drivers(straddle, chunk_size=8) is True,
            )
        finally:
            os.remove(straddle)
    finally:
        for p in (mb_con, mb_vis, mb_plain):
            os.remove(p)

    # ---- robustness: tolerant open + namespace-safe created nodes --------------
    from types import SimpleNamespace

    from blendertk.env_utils.maya_bridge._scene_import import _IMPORT_TEMPLATE_USD

    usd_txt = _IMPORT_TEMPLATE_USD.read_text()
    check(
        "template: tolerant scene open (a LOADED scene with plugin/node errors never aborts)",
        "def _open_scene" in txt
        and "sceneName" in txt
        and "_open_scene(cmds, SRC_PATH)" in txt,
    )
    check(
        "usd template: tolerant scene open",
        "def _open_scene" in usd_txt and "_open_scene(cmds, SRC_PATH)" in usd_txt,
    )
    check(
        "template: created shader nodes are namespace-safe (colon-free, root-namespace)",
        "def _ns_safe" in txt and "_ns_safe(mat)" in txt,
    )
    check(
        "usd template: created shader nodes are namespace-safe",
        "def _ns_safe" in usd_txt and "_ns_safe(mat)" in usd_txt,
    )

    # Visibility collection: short names are the manifest keys (the Blender-side
    # match convention), so duplicate short names with DIFFERING curves are
    # ambiguous -- the replay would land one object's curve on both -- and must
    # be dropped loudly; identical curves merge fine. Behavioral: the function
    # is extracted from the template text and run against a stub cmds.
    fn_src = txt[
        txt.index("def _collect_baked_visibility") : txt.index("def _run_smart_bake")
    ]
    ns_exec = {}
    exec(compile(fn_src, "_collect_baked_visibility.py", "exec"), ns_exec)
    _collect = ns_exec["_collect_baked_visibility"]

    class _FakeCmds:
        def __init__(self, curves):
            self._curves = curves

        def objExists(self, name):
            return name in self._curves

        def keyframe(self, name, query=True, timeChange=False, valueChange=False):
            times, values = self._curves[name]
            return list(times) if timeChange else list(values)

    _fake_cmds = _FakeCmds(
        {
            "cA": ([1.0, 10.0], [1.0, 0.0]),
            "cB": ([1.0, 10.0], [0.0, 1.0]),  # differs from cA
            "cC": ([1.0, 10.0], [1.0, 0.0]),  # identical to cA
            "cS": ([5.0], [0.0]),
        }
    )
    _fake_result = SimpleNamespace(
        visibility_curves={
            "|grpA|wheel": "cA",
            "|grpB|wheel": "cB",  # same short name, different keys -> ambiguous
            "|solo": "cS",
            "|grpA|hub": "cA",
            "|grpB|hub": "cC",  # same short name, identical keys -> kept
        }
    )
    _vis = _collect(_fake_cmds, _fake_result)
    check(
        "visibility collect: ambiguous duplicate short name dropped (never one curve on both)",
        "wheel" not in _vis,
        str(_vis),
    )
    check(
        "visibility collect: unambiguous + identical-duplicate names kept",
        _vis.get("solo") == [[5.0, 0.0]]
        and _vis.get("hub") == [[1.0, 1.0], [10.0, 0.0]],
        str(_vis),
    )

    for val, lit in (("auto", "'auto'"), (True, "True"), (False, "False")):
        s = eng.render_script(r"C:\s.ma", r"C:\o.fbx", via="fbx", smart_bake=val)
        ok_compile = True
        try:
            compile(s, "r.py", "exec")
        except SyntaxError:
            ok_compile = False
        check(
            f"render: smart_bake={val!r} -> SMART_BAKE = {lit} (compiles)",
            f"SMART_BAKE = {lit}" in s and ok_compile,
        )
    usd = eng.render_script(r"C:\s.ma", r"C:\o.usd", via="usd", smart_bake=True)
    check("render: USD route omits SMART_BAKE (FBX-only feature)", "SMART_BAKE" not in usd)

    dirs = _smart_bake_syspath()
    _holds = lambda d, pkg: os.path.isfile(os.path.join(d, pkg, "__init__.py"))
    check(
        "syspath: resolves parents that actually HOLD pythontk + mayatk (not namespace dirs)",
        any(_holds(d, "pythontk") for d in dirs)
        and any(_holds(d, "mayatk") for d in dirs),
        str(dirs),
    )
    check(
        "syspath: a bogus explicit mayatk_path falls back to a real package parent",
        all(
            _holds(d, "mayatk") or _holds(d, "pythontk")
            for d in _smart_bake_syspath(mayatk_path="X:/definitely/not/mayatk")
        ),
    )

    # convert: PYTHONPATH injection gated on smart_bake (FBX route). Reuses the
    # env-capturing _run_script seam.
    src2 = os.path.join(tempfile.gettempdir(), "btk_smartbake_src.ma")
    with open(src2, "w") as f:
        f.write("//Maya ASCII scene\n")
    out2 = os.path.join(tempfile.gettempdir(), "btk_smartbake_out.fbx")
    envs = {}

    class EnvCaptureImport(MayaSceneImport):
        @staticmethod
        def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
            envs["last"] = env
            with open(artifact, "wb") as fh:
                fh.write(b"fbx")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

    mayatk_parent = next((d for d in dirs if _holds(d, "mayatk")), None)
    baseline_pp = os.environ.get("PYTHONPATH", "")
    try:
        EnvCaptureImport().convert(src2, out2, via="fbx", smart_bake=True)
        pp_on = (envs["last"] or {}).get("PYTHONPATH", "")
        check(
            "convert: smart_bake=True injects the mayatk parent on the child PYTHONPATH",
            bool(mayatk_parent) and mayatk_parent in pp_on and pp_on != baseline_pp,
            pp_on,
        )
        EnvCaptureImport().convert(src2, out2, via="fbx", smart_bake=False)
        pp_off = (envs["last"] or {}).get("PYTHONPATH", "")
        check(
            "convert: smart_bake=False leaves PYTHONPATH untouched (no injection)",
            pp_off == baseline_pp,
            pp_off,
        )
        check(
            "convert: fast-startup env still applied with smart_bake off",
            (envs["last"] or {}).get("MAYA_SKIP_USERSETUP_PY") == "1",
        )
        # The conversion mayapy is launched FROM Blender and inherits its OCIO --
        # a 2.5-profile config Maya 2025's OCIO 2.3 cannot load (color-management
        # init fails on every conversion). The send path already strips it; the
        # pull path must go through the SAME helper, not a second copy.
        check(
            "convert: OCIO hand-off scrub reuses MayaBridge._launch_env",
            "MayaBridge._launch_env()" in _inspect.getsource(MayaSceneImport.convert),
        )
    finally:
        for p in (src2, out2):
            if os.path.exists(p):
                os.remove(p)

    check(
        "cache key: smart_bake is part of the conversion identity (toggle invalidates)",
        MayaSceneImport._cache_key(__file__, {"smart_bake": True}, "fbx")
        != MayaSceneImport._cache_key(__file__, {"smart_bake": False}, "fbx"),
    )

    # ---- FBX default route + USD-capable bake --------------------------------
    # The pull route defaults to FBX: its instancing is carried by the format
    # itself on both sides, so a Maya instance set reaches Blender as linked
    # duplicates with no sidecar replay in the path. The USD route matches that
    # only by replaying a recorded grouping, and that replay degrades SILENTLY
    # into a flattened scene (see .claude/BACKLOG.md), so USD is opt-in
    # (via="usd") rather than the default.
    for _fn in (
        MayaSceneImport.import_scene,
        MayaSceneImport.bake_scene,
        MayaSceneImport.render_script,
        MayaSceneImport.convert,
    ):
        _p = _inspect.signature(_fn).parameters.get("via")
        check(
            f"default route is FBX: {_fn.__name__}(via='fbx')",
            _p is not None and _p.default == "fbx",
        )
    s_default = eng.render_script(r"C:\s.ma", r"C:\o.fbx")
    check(
        "render: default route renders the FBX template (FBXExport, no mayaUSDExport)",
        "FBXExport" in s_default and "mayaUSDExport" not in s_default,
    )
    s_usd = eng.render_script(r"C:\s.ma", r"C:\o.usd", via="usd")
    check(
        "render: via='usd' still renders the USD template (mayaUSDExport, no FBXExport)",
        "mayaUSDExport" in s_usd and "FBXExport" not in s_usd,
    )
    check(
        "cache key: routes are separate cache identities",
        MayaSceneImport._cache_key(__file__, {}, "usd")
        != MayaSceneImport._cache_key(__file__, {}, "fbx"),
    )
    # Live regression (user report, 2026-08-02): a production scene pulled via USD
    # arrived with a `prototypes` collection of uneditable collection-instance
    # Empties AND lost materials. Measured cause: with exportInstances=True a
    # scene holding instances collapses material export (def Material 3 -> 0,
    # material:binding 4 -> 0 on the probe scene). Behavior pinned live by the
    # e2e's e2e_inst_* trap; this just guards the flag itself.
    check(
        "USD template: exportInstances OFF (instancing collapses material export)",
        '"exportInstances": False' in usd_txt,
    )
    check(
        "USD template: the measured reason is recorded next to the flag",
        "material:binding" in usd_txt and "prototypes" in usd_txt,
    )
    # Flattening must NOT mean losing instances: the relationship is recorded
    # Maya-side and rebuilt as Blender linked duplicates on import. USD's own
    # instancing can't express that (measured: collection-instance Empties one
    # way, zero data sharing the other).
    check(
        "USD template: instance groups recorded for the Blender-side rebuild",
        "def collect_instance_groups" in usd_txt
        and "allParents=True" in usd_txt
        and '"instances": groups' in usd_txt,
    )
    check(
        "USD route: engine rebuilds shared mesh data from the sidecar",
        "_apply_instance_manifest" in import_src
        and hasattr(MayaSceneImport, "_apply_instance_manifest"),
    )
    _inst_src = _inspect.getsource(MayaSceneImport._apply_instance_manifest)
    check(
        "instance rebuild: per-instance shaders survive via OBJECT-linked slots",
        'slot.link = "OBJECT"' in _inst_src,
    )
    check(
        "instance rebuild: displaced meshes are freed (memory is the point)",
        "bpy.data.meshes.remove" in _inst_src and "users == 0" in _inst_src,
    )
    check(
        "bake template rebuilds instances too (the Reference Manager's Open path)",
        "def apply_instances" in bake_txt
        and "_apply_instance_manifest" in bake_txt,
    )
    # The frame range is a direct multiplier on USD export cost (no curves --
    # one time sample per frame per prim). Measured on a 755-mesh static module
    # with a 1-200 playback range: 234s -> 1.8s. Sample only what moves.
    check(
        "USD template: frame range gated on real animation, not playback range",
        "def _animation_frame_range" in usd_txt
        and "_animation_frame_range(cmds) if INCLUDE_ANIMATION" in usd_txt,
    )
    check(
        "USD template: unkeyed drivers still force the full range",
        "_UNKEYED_DRIVERS" in usd_txt and "motionPath" in usd_txt,
    )
    check(
        "USD template: only TIME curves counted (animCurveU* are driver VALUES)",
        all(
            t in usd_txt
            for t in ("animCurveTA", "animCurveTL", "animCurveTU", "animCurveTT")
        )
        and "set-driven" in usd_txt,
    )
    # Bake template: source-generalized (a USD intermediate imports natively, so
    # main() bypasses the texture/visibility replays -- but a sidecar DOES exist
    # beside a USD; it carries the instance grouping, replayed by apply_instances).
    check(
        "bake template: source token generalized (SRC_FILE, no SRC_FBX)",
        "__" + "SRC_FILE" + "__" in bake_txt and "SRC_FBX" not in bake_txt,
    )
    check(
        "bake template: USD sources import natively (wm.usd_import branch)",
        "usd_import" in bake_txt and ".usd" in bake_txt,
    )
    bs_usd = eng.render_bake_script(r"C:\cache\conv.usd", r"C:\cache\conv.blend")
    _ok_bs = True
    try:
        compile(bs_usd, "bake_rendered.py", "exec")
    except SyntaxError:
        _ok_bs = False
    check(
        "bake render: .usd source substitutes + compiles",
        "C:/cache/conv.usd" in bs_usd and _ok_bs,
    )
    # bake_scene orchestration: the default route's intermediate is a .usd.
    _bake_cap = {}

    class BakeCapture(MayaSceneImport):
        @staticmethod
        def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
            with open(artifact, "wb") as fh:
                fh.write(b"conv-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        @staticmethod
        def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
            _bake_cap["script"] = script_text
            with open(artifact, "wb") as fh:
                fh.write(b"blend-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

        def require_blender(self):
            return "stub_blender"

    src_usdbake = os.path.join(tempfile.gettempdir(), "btk_usdbake_src.ma")
    with open(src_usdbake, "w") as f:
        f.write("//Maya ASCII scene\n")
    baked_path = None
    try:
        baked_path = BakeCapture().bake_scene(src_usdbake, use_cache=False)
        # Judge the SUBSTITUTED source line, not the template text (which always
        # contains ".usd" in its extension table) -- the default route is FBX.
        _src_line = next(
            (
                line
                for line in _bake_cap.get("script", "").splitlines()
                if line.startswith("SRC_FILE")
            ),
            "",
        )
        check(
            "bake_scene: default route bakes from an .fbx intermediate",
            _src_line.rstrip().endswith('.fbx"'),
            _src_line,
        )
        check("bake_scene: returns the .blend path", baked_path.endswith(".blend"))
    finally:
        os.remove(src_usdbake)
        # An uncached bake lives in scratch by design (the caller links it);
        # this test's bake links nothing, so clean it + its source sidecar.
        from blendertk.env_utils.maya_bridge._scene_import import BAKE_SOURCE_SUFFIX

        for p in ([baked_path, baked_path + BAKE_SOURCE_SUFFIX] if baked_path else []):
            if os.path.exists(p):
                os.remove(p)
    # smart_bake is FBX-only: it must stay out of the USD route's cache key
    # (an inert option fragmenting the default route's cache).
    _is_src = _inspect.getsource(MayaSceneImport.import_scene)
    check(
        "USD route keeps smart_bake out of the conversion cache key",
        'if via == "fbx":' in _is_src
        and 'script_opts["smart_bake"] = smart_bake' in _is_src,
    )

    # ---- discovery / derivation ----------------------------------------------
    check(
        "mayapy derivation swaps the basename",
        MayaSceneImport.mayapy_from_maya_exe("X:/nowhere/bin/maya.exe") is None,
    )  # nonexistent -> None

    # Regression (caught live): the install scan returns 'maya.EXE' (uppercase) —
    # the suffix check must be case-insensitive or derivation silently fails.
    fake_bin = tempfile.mkdtemp(prefix="btk_fake_maya_bin_")
    open(os.path.join(fake_bin, "mayapy.exe"), "w").close()
    try:
        derived = MayaSceneImport.mayapy_from_maya_exe(
            os.path.join(fake_bin, "maya.EXE")
        )
        check(
            "mayapy derivation is suffix-case-insensitive",
            derived is not None and derived.endswith("mayapy.exe"),
            str(derived),
        )
    finally:
        import shutil

        shutil.rmtree(fake_bin, ignore_errors=True)
    check(
        "engine reuses the bridge AppSpec (no raise; None or str)",
        MayaSceneImport().maya_path is None
        or isinstance(MayaSceneImport().maya_path, str),
    )
    check(
        "explicit maya_path wins",
        MayaSceneImport("Y:/maya.exe").maya_path == "Y:/maya.exe",
    )

    # require_mayapy: fake maya.exe -> no mayapy beside it -> the error must name
    # the exe it derived from (NOT claim Maya itself wasn't found).
    try:
        eng.require_mayapy()
        check("require_mayapy raises naming the derivation source", False)
    except FileNotFoundError as e:
        check(
            "require_mayapy raises naming the derivation source",
            "mayapy" in str(e) and "X:/fake/bin/maya.exe" in str(e),
            str(e),
        )

    # ---- input validation ----------------------------------------------------
    try:
        eng.convert("no_such_scene.ma", "out.fbx")
        check("convert: missing scene raises", False)
    except FileNotFoundError:
        check("convert: missing scene raises", True)

    bad = os.path.join(tempfile.gettempdir(), "btk_scene_import_bad.fbx")
    open(bad, "w").close()
    try:
        eng.convert(bad, "out.fbx")
        check("convert: non-.ma/.mb raises", False)
    except ValueError:
        check("convert: non-.ma/.mb raises", True)
    finally:
        os.remove(bad)

    # ---- orchestration (mayapy run + bpy import + material rebuild stubbed) ----
    from types import SimpleNamespace

    src = os.path.join(tempfile.gettempdir(), "btk_scene_import_src.ma")
    with open(src, "w") as f:
        f.write("//Maya ASCII scene\n")
    tex = os.path.join(tempfile.gettempdir(), "btk_scene_import_BaseColor.png")
    with open(tex, "wb") as f:
        f.write(b"png-bytes")

    calls = {}

    class StubbedImport(MayaSceneImport):
        @staticmethod
        def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
            calls["ran"] = True
            calls["runs"] = calls.get("runs", 0) + 1
            calls["env"] = env
            with open(artifact, "wb") as fh:  # the Maya side "produces" the FBX
                fh.write(b"fbx-bytes")
            import json

            with open(artifact + ".manifest.json", "w") as mf:  # ...and the sidecar
                json.dump(
                    {
                        "version": 1,
                        "materials": [
                            # slot-swap primary path (fbx_material matches a slot)
                            {
                                "name": "M_test",
                                "fbx_material": "M_test_fbxsafe",
                                "objects": ["objA"],
                                "files": [tex],
                            },
                            # object-level fallback (no slot carries this name)
                            {
                                "name": "M_fb",
                                "fbx_material": "M_renamed_by_importer",
                                "objects": ["objB"],
                                "files": [tex],
                            },
                            {
                                "name": "M_gone",
                                "fbx_material": "M_gone_fbxsafe",
                                "objects": ["objB"],
                                "files": ["X:/missing.png"],
                            },  # all files gone -> skipped
                        ],
                    },
                    mf,
                )
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

    import blendertk.env_utils.fbx_utils as fbx_utils
    import blendertk.mat_utils._mat_utils as mat_utils

    orig_import = fbx_utils.FbxUtils.import_fbx
    orig_create = mat_utils.MatUtils.create_pbr_material
    orig_assign = mat_utils.MatUtils.assign_mat

    # objA carries the translated phong in ONE of two slots (multi-material mesh);
    # the swap must touch only that slot. objB's slot has an unrelated material.
    slot_stingray = SimpleNamespace(material=SimpleNamespace(name="M_test_fbxsafe.001"))
    slot_other = SimpleNamespace(material=SimpleNamespace(name="untranslated_phong"))
    obj_a = SimpleNamespace(name="objA", material_slots=[slot_stingray, slot_other])
    obj_b = SimpleNamespace(
        name="objB.001",
        material_slots=[SimpleNamespace(material=SimpleNamespace(name="other"))],
    )

    def fake_import(filepath, **opts):
        calls["fbx"] = filepath
        calls["opts"] = opts
        return [obj_a, obj_b]

    def fake_create(files, name=None, **kw):
        calls.setdefault("created", []).append((tuple(files), name))
        return SimpleNamespace(name=name)

    def fake_assign(objects, material):
        calls["assigned"] = (list(objects), material.name)

    fbx_utils.FbxUtils.import_fbx = staticmethod(fake_import)
    mat_utils.MatUtils.create_pbr_material = fake_create
    mat_utils.MatUtils.assign_mat = fake_assign
    try:
        result = StubbedImport().import_scene(
            src, via="fbx", use_cache=False, fbx_options={"use_anim": False}
        )
        check("import_scene returns the imported objects", result == [obj_a, obj_b])
        check(
            "conversion mayapy runs with the fast-startup env",
            all(
                calls["env"].get(k) == "1"
                for k in (
                    "MAYA_SKIP_USERSETUP_PY",
                    "MAYA_DISABLE_CIP",
                    "MAYA_DISABLE_CER",
                )
            ),
        )
        check(
            "conversion ran and produced the payload the import consumed",
            calls.get("ran") and calls["fbx"].endswith(".fbx"),
        )
        check("fbx_options forwarded", calls["opts"] == {"use_anim": False})
        check(
            "manifest: materials rebuilt from texture files (missing-file entry skipped)",
            calls.get("created") == [((tex,), "M_test"), ((tex,), "M_fb")],
            f"{calls.get('created')}",
        )
        check(
            "manifest: slot-level swap hit only the matching slot",
            slot_stingray.material.name == "M_test"
            and slot_other.material.name == "untranslated_phong",
        )
        check(
            "manifest: object-level fallback when no slot matches",
            calls.get("assigned") == ([obj_b], "M_fb"),
        )
        check("intermediate FBX removed on success", not os.path.exists(calls["fbx"]))
        check(
            "manifest sidecar removed on success",
            not os.path.exists(calls["fbx"] + ".manifest.json"),
        )

        # conversion cache: identical scene + options -> the second import must
        # NOT relaunch Maya; use_cache=False must force a fresh conversion.
        runs_before = calls["runs"]
        StubbedImport().import_scene(src, via="fbx", fbx_options={"use_anim": False})
        StubbedImport().import_scene(src, via="fbx", fbx_options={"use_anim": False})
        check(
            "conversion cache: second identical import skips the Maya run",
            calls["runs"] == runs_before + 1,
            f"runs={calls['runs']}",
        )
        StubbedImport().import_scene(
            src, via="fbx", use_cache=False, fbx_options={"use_anim": False}
        )
        check(
            "use_cache=False forces a fresh conversion",
            calls["runs"] == runs_before + 2,
            f"runs={calls['runs']}",
        )
        import glob as _glob

        for stale in _glob.glob(
            os.path.join(tempfile.gettempdir(), "maya_to_btk_cache_*")
        ):
            os.remove(stale)

        # failure path: import blows up -> intermediate FBX kept for debugging
        def broken_import(filepath, **opts):
            calls["kept"] = filepath
            raise RuntimeError("import boom")

        fbx_utils.FbxUtils.import_fbx = staticmethod(broken_import)
        try:
            StubbedImport().import_scene(src, via="fbx", use_cache=False)
            check("failure propagates", False)
        except RuntimeError:
            check("failure propagates", True)
        check("intermediate FBX kept on failure", os.path.exists(calls["kept"]))
        os.remove(calls["kept"])
        os.remove(calls["kept"] + ".manifest.json")
    finally:
        fbx_utils.FbxUtils.import_fbx = orig_import
        mat_utils.MatUtils.create_pbr_material = orig_create
        mat_utils.MatUtils.assign_mat = orig_assign
        os.remove(src)
        os.remove(tex)

    # ---- public surface --------------------------------------------------------
    import blendertk as btk

    check("btk.MayaSceneImport registered", btk.MayaSceneImport is MayaSceneImport)

    # ---------------------------------------------------------------- slot fallback
    # The manifest's authoritative shader slots rescue textures whose FILENAME
    # carries no map-type token (a plain color map named after a product). Two
    # rules make this safe, and both are easy to regress:
    #   * only files that classified to NOTHING are rescued -- a filename is the
    #     only thing that reveals packing (MSAO in a metallic slot is still MSAO),
    #   * a rescued type never displaces one the filename already resolved.
    # resolve_pbr_plan is stubbed so these rules are tested on their own (and
    # without bpy), rather than through real classification.
    from blendertk.mat_utils import _mat_utils as _mu

    _real_plan = _mu.MatUtils.resolve_pbr_plan
    try:
        engine = MayaSceneImport(log_level="WARNING")

        def _stub(plan):
            return staticmethod(lambda textures, config=None: {
                "by_type": dict(plan.get("by_type", {})),
                "dropped": {}, "extracted": {},
                "unknown": list(plan.get("unknown", [])),
                "unhandled": {}, "wired": set(),
            })

        # 1. An unclassifiable file is rescued via its channel.
        _mu.MatUtils.resolve_pbr_plan = _stub(
            {"by_type": {}, "unknown": ["/t/Agilent_PNA.png"]}
        )
        out = engine._plan_with_slot_fallback(
            ["/t/Agilent_PNA.png"], {"baseColor": "/t/Agilent_PNA.png"}, "REF"
        )
        check(
            "slot fallback rescues an unclassifiable texture",
            out and out["by_type"].get("Base_Color") == "/t/Agilent_PNA.png",
            str(out and out["by_type"]),
        )
        check(
            "rescued file leaves the plan's unknown list",
            out and "/t/Agilent_PNA.png" not in out["unknown"],
            str(out and out["unknown"]),
        )

        # 2. A rescued channel must NOT displace what the filename resolved.
        _mu.MatUtils.resolve_pbr_plan = _stub(
            {"by_type": {"Base_Color": "/t/rock_BaseColor.png"},
             "unknown": ["/t/Agilent_PNA.png"]}
        )
        out = engine._plan_with_slot_fallback(
            ["/t/rock_BaseColor.png", "/t/Agilent_PNA.png"],
            {"baseColor": "/t/Agilent_PNA.png"},
            "REF",
        )
        check(
            "slot fallback never displaces a filename-resolved map",
            out and out["by_type"]["Base_Color"] == "/t/rock_BaseColor.png",
            str(out and out["by_type"]),
        )

        # 3. A classified file is left alone even when a slot names it.
        _mu.MatUtils.resolve_pbr_plan = _stub(
            {"by_type": {"MSAO": "/t/cab_MSAO.png"}, "unknown": []}
        )
        out = engine._plan_with_slot_fallback(
            ["/t/cab_MSAO.png"], {"metallic": "/t/cab_MSAO.png"}, "MAT"
        )
        check(
            "a packed map keeps its filename type, not its slot's",
            out and out["by_type"] == {"MSAO": "/t/cab_MSAO.png"},
            str(out and out["by_type"]),
        )

        # 4. An unmapped channel rescues nothing.
        _mu.MatUtils.resolve_pbr_plan = _stub(
            {"by_type": {}, "unknown": ["/t/x.png"]}
        )
        out = engine._plan_with_slot_fallback(
            ["/t/x.png"], {"notAChannel": "/t/x.png"}, "MAT"
        )
        check(
            "an unmapped channel rescues nothing",
            out is not None and not out["by_type"],
            str(out and out["by_type"]),
        )

        # 5. No slots -> None, so the caller resolves the plan exactly as before.
        check(
            "no slots returns None (unchanged legacy path)",
            engine._plan_with_slot_fallback(["/t/x.png"], None, "MAT") is None,
        )
    finally:
        _mu.MatUtils.resolve_pbr_plan = _real_plan

    # ---- USD instance replay: guaranteed-or-fail (v2 sidecar) -----------------
    # A silently flattened scene looks correct and only misbehaves when an artist
    # edits one duplicate and its siblings don't follow -- the one outcome a
    # non-destructive transfer forbids. The replay either fully rebuilds the
    # recorded sharing or the conversion FAILS atomically (imported objects
    # removed). The sidecar records SANITIZED PRIM PATHS (v2): mayaUSDExport
    # rewrites names the prim grammar forbids (probe-verified: ref:nsCube ->
    # ref_nsCube), and paths keep duplicate leaf names (/g1/wheel vs /g2/wheel)
    # unambiguous through Blender's .001 collision renames.
    usd_txt = _IMPORT_TEMPLATE_USD.read_text()
    bake_txt = _BAKE_TEMPLATE.read_text()
    check(
        "USD template: sidecar records sanitized prim PATHS (v2)",
        "def _sanitize_prim_name" in usd_txt
        and '"version": 2' in usd_txt
        and '"format": "paths"' in usd_txt,
    )
    check(
        "USD template: failed sidecar write withholds the artifact",
        "os.remove(OUT_USD)" in usd_txt,
    )
    check(
        "USD template: sanitize-collisions fail the export loudly",
        "collide" in usd_txt,
    )
    check(
        "bake template: USD sidecar replay is loud (no flattened .blend saved)",
        "raise RuntimeError" in bake_txt
        and "Instance rebuild failed; meshes stay independent" not in bake_txt,
    )
    check(
        "bake template: materials-scope Empty stripped (prim-path keyed)",
        "_strip_materials_scope" in bake_txt,
    )

    import ast as _ast

    _fn = next(
        (
            n
            for n in _ast.walk(_ast.parse(usd_txt))
            if isinstance(n, _ast.FunctionDef) and n.name == "_sanitize_prim_name"
        ),
        None,
    )
    if _fn is None:
        check("USD template: _sanitize_prim_name extractable", False)
    else:
        _ns = {"re": __import__("re")}
        exec(compile(_ast.Module(body=[_fn], type_ignores=[]), "<tmpl>", "exec"), _ns)
        _san = _ns["_sanitize_prim_name"]
        # Pinned against live probes: mayaUSDExport flattens ':' (namespaces) and
        # Blender's exporter PREFIXES a leading digit (TfMakeValidIdentifier
        # replaces it -- the templates must match the DCC, not Tf).
        check(
            "sanitizer matches the probed exporter behavior",
            _san("ref:nsCube") == "ref_nsCube"
            and _san("Chair.001") == "Chair_001"
            and _san("1digit") == "_1digit"
            and _san("") == "_",
            f'{_san("ref:nsCube")}/{_san("Chair.001")}/{_san("1digit")}',
        )

    # import leg: a USD conversion without its sidecar must fail BEFORE importing.
    _usd_stub = os.path.join(tempfile.gettempdir(), "btk_strict_nomanifest.usda")
    with open(_usd_stub, "w") as f:
        f.write("#usda 1.0\n")

    class _NoManifestStub(MayaSceneImport):
        def _cached_conversion(self, s, **kw):
            return SimpleNamespace(path=_usd_stub, scratch=None)

    try:
        _NoManifestStub().import_scene("X:/nope/scene.ma", via="usd", use_cache=False)
        check("USD leg: missing sidecar fails the import", False)
    except RuntimeError as e:
        check(
            "USD leg: missing sidecar fails the import",
            "manifest" in str(e).lower(),
            str(e),
        )
    except Exception as e:  # noqa: BLE001
        check("USD leg: missing sidecar fails the import", False, repr(e))
    finally:
        os.remove(_usd_stub)

    # bake leg: bake_scene(via="usd") must refuse to bake without the sidecar.
    _bake_usd = os.path.join(tempfile.gettempdir(), "btk_strict_bake.usda")
    with open(_bake_usd, "w") as f:
        f.write("#usda 1.0\n")
    _bake_src = os.path.join(tempfile.gettempdir(), "btk_strict_bake_src.ma")
    with open(_bake_src, "w") as f:
        f.write("//Maya ASCII scene\n")
    _bake_ran = {}

    class _BakeManifestStub(MayaSceneImport):
        def _cached_conversion(self, s, **kw):
            return SimpleNamespace(path=_bake_usd, scratch=None)

        @staticmethod
        def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
            _bake_ran["ran"] = True
            with open(artifact, "wb") as fh:
                fh.write(b"blend-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

        def require_blender(self):
            return "stub_blender"

    _baked2 = None
    try:
        _BakeManifestStub().bake_scene(_bake_src, via="usd", use_cache=False)
        check("bake_scene: USD intermediate without sidecar refuses to bake", False)
    except RuntimeError as e:
        check(
            "bake_scene: USD intermediate without sidecar refuses to bake",
            "manifest" in str(e).lower() and "ran" not in _bake_ran,
            str(e),
        )
    except Exception as e:  # noqa: BLE001
        check(
            "bake_scene: USD intermediate without sidecar refuses to bake",
            False,
            repr(e),
        )
    try:
        import json as _json

        with open(_bake_usd + ".manifest.json", "w") as f:
            _json.dump({"version": 2, "format": "paths", "instances": []}, f)
        _baked2 = _BakeManifestStub().bake_scene(_bake_src, via="usd", use_cache=False)
        check(
            "bake_scene: USD intermediate WITH sidecar bakes",
            _bake_ran.get("ran") and _baked2.endswith(".blend"),
        )
    finally:
        from blendertk.env_utils.maya_bridge._scene_import import BAKE_SOURCE_SUFFIX

        for p in (
            [_bake_usd, _bake_usd + ".manifest.json", _bake_src]
            + ([_baked2, _baked2 + BAKE_SOURCE_SUFFIX] if _baked2 else [])
        ):
            if p and os.path.exists(p):
                os.remove(p)

    # ---- behavioral (needs bpy): strict path matching + atomic rollback -------
    try:
        import bpy

        _HAVE_BPY = True
    except Exception:  # noqa: BLE001 -- also runs under the workspace .venv
        _HAVE_BPY = False

    if _HAVE_BPY:
        import json as _json

        bpy.ops.wm.read_factory_settings(use_empty=True)

        def _mk_mesh(name):
            m = bpy.data.meshes.new(name)
            m.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
            return m

        def _mk_obj(name, data=None, parent=None):
            o = bpy.data.objects.new(name, data)
            bpy.context.scene.collection.objects.link(o)
            o.parent = parent
            return o

        _g1 = _mk_obj("g1")
        _w1 = _mk_obj("wheel", _mk_mesh("M1"), _g1)
        _g2 = _mk_obj("g2")
        # Simulates Blender's collision rename of the second /g2/wheel prim.
        _w2 = _mk_obj("wheel.001", _mk_mesh("M2"), _g2)
        _c1 = _mk_obj("Chair_001", _mk_mesh("M3"))
        _c2 = _mk_obj("Chair_002", _mk_mesh("M4"))
        _matA = bpy.data.materials.new("strict_A")
        _matB = bpy.data.materials.new("strict_B")
        _c1.data.materials.append(_matA)
        _c2.data.materials.append(_matB)

        _mpath = os.path.join(tempfile.gettempdir(), "btk_strict_replay.manifest.json")
        with open(_mpath, "w") as f:
            _json.dump(
                {
                    "version": 2,
                    "format": "paths",
                    "instances": [
                        ["/g1/wheel", "/g2/wheel"],
                        ["/Chair_001", "/Chair_002"],
                    ],
                },
                f,
            )
        _eng2 = MayaSceneImport()
        try:
            _relinked = _eng2._apply_instance_manifest(
                _mpath, [_g1, _w1, _g2, _w2, _c1, _c2]
            )
            check(
                "strict replay: duplicate leaf names resolved by PATH through the "
                ".001 rename",
                _relinked == 2 and _w2.data is _w1.data,
            )
            check(
                "strict replay: root-level group shares one datablock",
                _c2.data is _c1.data,
            )
            check(
                "strict replay: per-instance material survives via OBJECT slot",
                _c2.material_slots[0].link == "OBJECT"
                and _c2.material_slots[0].material is _matB,
            )
        except Exception as e:  # noqa: BLE001
            check("strict replay: path-matched rebuild", False, repr(e))
        finally:
            os.remove(_mpath)

        # Unmatched member -> loud failure naming the path.
        with open(_mpath, "w") as f:
            _json.dump(
                {
                    "version": 2,
                    "format": "paths",
                    "instances": [["/Chair_001", "/Ghost"]],
                },
                f,
            )
        try:
            _eng2._apply_instance_manifest(_mpath, [_c1, _c2])
            check("strict replay: unmatched member raises", False)
        except RuntimeError as e:
            check("strict replay: unmatched member raises", "/Ghost" in str(e), str(e))
        finally:
            os.remove(_mpath)

        # Stale v1 sidecar -> refused (pre-sanitization names can mismatch).
        with open(_mpath, "w") as f:
            _json.dump({"version": 1, "instances": [["Chair_001"]]}, f)
        try:
            _eng2._apply_instance_manifest(_mpath, [_c1])
            check("strict replay: v1 sidecar refused", False)
        except RuntimeError:
            check("strict replay: v1 sidecar refused", True)
        finally:
            os.remove(_mpath)

        # ---- materials-scope Empty strip (prim-path keyed, never name-keyed) --
        _scope_usda = os.path.join(tempfile.gettempdir(), "btk_strict_scope.usda")
        with open(_scope_usda, "w") as f:
            f.write(
                '#usda 1.0\n'
                'def Scope "mtl" {\n'
                '    def Material "M1" {}\n'
                '}\n'
                'def Mesh "Probe_Cube" {\n'
                '    int[] faceVertexCounts = [3]\n'
                '    int[] faceVertexIndices = [0, 1, 2]\n'
                '    point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n'
                '}\n'
            )
        from blendertk.env_utils.usd import UsdUtils

        bpy.ops.wm.read_factory_settings(use_empty=True)
        _user_mtl = _mk_obj("mtl")  # a user's own object legitimately named mtl
        _imp = UsdUtils.import_usd(_scope_usda)
        _scope_empties = [o for o in _imp if o.type == "EMPTY"]
        check(
            "usd import materializes the materials Scope as an Empty (the defect)",
            len(_scope_empties) == 1,
            str([o.name for o in _imp]),
        )
        _kept = _eng2._strip_materials_scope(_imp, _scope_usda)
        check(
            "scope strip: the Scope Empty is removed from the import",
            all(o.type != "EMPTY" for o in _kept)
            and any(o.type == "MESH" for o in _kept),
            str([o.name for o in _kept]),
        )
        check(
            "scope strip: the user's own 'mtl' object survives",
            any(o is _user_mtl for o in bpy.data.objects.values()),
        )
        os.remove(_scope_usda)

        # An Xform named mtl carrying real geometry is NOT a materials scope.
        _xform_usda = os.path.join(tempfile.gettempdir(), "btk_strict_xform.usda")
        with open(_xform_usda, "w") as f:
            f.write(
                '#usda 1.0\n'
                'def Xform "mtl" {\n'
                '    def Mesh "sub" {\n'
                '        int[] faceVertexCounts = [3]\n'
                '        int[] faceVertexIndices = [0, 1, 2]\n'
                '        point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n'
                '    }\n'
                '}\n'
            )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _imp2 = UsdUtils.import_usd(_xform_usda)
        _kept2 = _eng2._strip_materials_scope(_imp2, _xform_usda)
        check(
            "scope strip: an Xform named mtl with geometry is untouched",
            len(_kept2) == len(_imp2),
            str([o.name for o in _imp2]),
        )
        os.remove(_xform_usda)

        # ---- atomic rollback: a failed replay removes everything it imported --
        _rb_usda = os.path.join(tempfile.gettempdir(), "btk_strict_rollback.usda")
        with open(_rb_usda, "w") as f:
            f.write(
                '#usda 1.0\n'
                'def Mesh "Chair_001" {\n'
                '    int[] faceVertexCounts = [3]\n'
                '    int[] faceVertexIndices = [0, 1, 2]\n'
                '    point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n'
                '}\n'
            )
        with open(_rb_usda + ".manifest.json", "w") as f:
            _json.dump(
                {
                    "version": 2,
                    "format": "paths",
                    "instances": [["/Chair_001", "/Ghost_777"]],
                },
                f,
            )

        class _RollbackStub(MayaSceneImport):
            def _cached_conversion(self, s, **kw):
                return SimpleNamespace(path=_rb_usda, scratch=None)

        bpy.ops.wm.read_factory_settings(use_empty=True)
        _before = set(bpy.data.objects)
        try:
            _RollbackStub().import_scene(
                "X:/nope/scene.ma", via="usd", use_cache=False, cleanup=False
            )
            check("USD leg: failed replay raises", False)
        except RuntimeError:
            check("USD leg: failed replay raises", True)
        except Exception as e:  # noqa: BLE001
            check("USD leg: failed replay raises", False, repr(e))
        check(
            "USD leg: failed replay rolls the import back out of the scene",
            set(bpy.data.objects) == _before,
            str([o.name for o in bpy.data.objects if o not in _before]),
        )
        for p in (_rb_usda, _rb_usda + ".manifest.json"):
            if os.path.exists(p):
                os.remove(p)

        # ---- name reclaim (real Blender datablock naming) --------------------
        # A rebuilt material is necessarily created while the FBX-carried one
        # still owns the name, so Blender hands it "M_x.001"; once the FBX one
        # is purged the name is free and must be taken back. Left unclaimed, the
        # suffix rides into the next hand-off and compounds -- names are the
        # binding for a game-engine-bound asset (live production report).
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _engine = MayaSceneImport(log_level="WARNING")

        _clashed = bpy.data.materials.new("M_claim.001")
        _engine._claim_material_name(_clashed, "M_claim")
        check("name reclaim: a freed source name is taken back", _clashed.name == "M_claim")

        _holder = bpy.data.materials.new("M_held")
        _other = bpy.data.materials.new("M_held.001")
        _engine._claim_material_name(_other, "M_held")
        check(
            "name reclaim: a name still in use is never stolen",
            _other.name == "M_held.001" and _holder.name == "M_held",
            f"{_other.name} / {_holder.name}",
        )


except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
