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

    # ---- rendering -----------------------------------------------------------
    eng = MayaSceneImport(maya_path="X:/fake/bin/maya.exe")
    script = eng.render_script(
        r"C:\scenes\test scene.ma",
        r"C:\tmp\out.fbx",
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
        and 'SRC_FBX + ".manifest.json"' in bake_txt
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

    for val, lit in (("auto", "'auto'"), (True, "True"), (False, "False")):
        s = eng.render_script(r"C:\s.ma", r"C:\o.fbx", smart_bake=val)
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
        EnvCaptureImport().convert(src2, out2, smart_bake=True)
        pp_on = (envs["last"] or {}).get("PYTHONPATH", "")
        check(
            "convert: smart_bake=True injects the mayatk parent on the child PYTHONPATH",
            bool(mayatk_parent) and mayatk_parent in pp_on and pp_on != baseline_pp,
            pp_on,
        )
        EnvCaptureImport().convert(src2, out2, smart_bake=False)
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
    finally:
        for p in (src2, out2):
            if os.path.exists(p):
                os.remove(p)

    check(
        "cache key: smart_bake is part of the conversion identity (toggle invalidates)",
        MayaSceneImport._cache_key(__file__, {"smart_bake": True}, "fbx")
        != MayaSceneImport._cache_key(__file__, {"smart_bake": False}, "fbx"),
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
            src, use_cache=False, fbx_options={"use_anim": False}
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
        StubbedImport().import_scene(src, fbx_options={"use_anim": False})
        StubbedImport().import_scene(src, fbx_options={"use_anim": False})
        check(
            "conversion cache: second identical import skips the Maya run",
            calls["runs"] == runs_before + 1,
            f"runs={calls['runs']}",
        )
        StubbedImport().import_scene(
            src, use_cache=False, fbx_options={"use_anim": False}
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
            StubbedImport().import_scene(src, use_cache=False)
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

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
