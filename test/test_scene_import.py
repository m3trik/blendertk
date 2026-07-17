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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import pythontk as ptk
    from blendertk.env_utils.maya_bridge import _maya_bridge as mb
    from blendertk.env_utils.maya_bridge._scene_import import (
        MayaSceneImport, import_maya_scene, mayapy_from_maya_exe, _IMPORT_TEMPLATE,
    )

    # ---- template hygiene ----------------------------------------------------
    check("template file exists", _IMPORT_TEMPLATE.is_file(), str(_IMPORT_TEMPLATE))
    check("underscore template hidden from the bridge panel",
          "_import_scene" not in {p.stem for p in mb.list_templates()})
    txt = _IMPORT_TEMPLATE.read_text()
    check("template: standalone.initialize before cmds",
          txt.index("maya.standalone") < txt.index("import maya.cmds"))
    check("template: judged-by-artifact contract (os._exit, no teardown)",
          "os._exit(0)" in txt and "FBXExport -f" in txt)
    check("template: HardEdges explicitly off (dense-mesh hang)",
          "FBXExportHardEdges -v false" in txt)
    # Texture fidelity (live user report): FBX carries only the classic
    # Lambert/Phong material model — modern surface shaders must be translated
    # before export or their textures silently drop.
    check("template: modern shaders translated to FBX-safe phong",
          "fbx_safe_materials" in txt
          and all(t in txt for t in
                  ("standardSurface", "aiStandardSurface", "openPBRSurface")))
    # Live user report #2: StingrayPBS (ShaderFX) exports as a Maya|TEX_* property
    # set Blender ignores — needs its own translation branch (different attrs).
    check("template: StingrayPBS translated (color/normal/emissive maps)",
          "StingrayPBS" in txt and "TEX_color_map" in txt
          and "use_color_map" in txt and "TEX_normal_map" in txt)
    check("template: full-fidelity flag battery (whole-scene semantics)",
          all(f in txt for f in
              ("FBXExportInstances -v true", "FBXExportSkins -v true",
               "FBXExportShapes -v true", "FBXExportCameras -v true",
               "FBXExportLights -v true", "FBXExportEmbeddedTextures")))
    check("template: per-flag tolerance (a missing FBX command must not abort)",
          "FBX flag skipped" in txt)
    # Packed maps (Metallic_Smoothness / MSAO / ORM) have no FBX slot at all —
    # they travel via a manifest sidecar the Blender side rebuilds from
    # (btk.create_pbr_material, the game_shader engine).
    check("template: texture manifest written beside the FBX",
          "write_texture_manifest" in txt and ".manifest.json" in txt
          and "STINGRAY_TEX_SLOTS" in txt)
    # Production fixes (live report: pink materials + _fbxsafeN duplicates):
    check("template: scene's Maya project opened before converting",
          "_resolve_workspace" in txt and "workspace.mel" in txt
          and "openWorkspace" in txt)
    check("template: one phong per source material (SG-sharing memoized)",
          "translated = {}" in txt and "mat in translated" in txt)

    # ---- rendering -----------------------------------------------------------
    eng = MayaSceneImport(maya_path="X:/fake/bin/maya.exe")
    script = eng.render_script(r"C:\scenes\test scene.ma", r"C:\tmp\out.fbx",
                               embed_textures=False, include_animation=True)
    check("render: no placeholders left", "__" + "SRC_PATH" + "__" not in script)
    check("render: forward-slashed paths substituted",
          'r"C:/scenes/test scene.ma"' in script and "C:/tmp/out.fbx" in script)
    check("render: bools are Python literals",
          "EMBED_TEXTURES = False" in script and "INCLUDE_ANIMATION = True" in script)
    try:
        compile(script, "_import_scene_rendered.py", "exec")
        check("render: compiles as valid Python", True)
    except SyntaxError as e:
        check("render: compiles as valid Python", False, repr(e))

    # ---- discovery / derivation ----------------------------------------------
    check("mayapy derivation swaps the basename",
          mayapy_from_maya_exe("X:/nowhere/bin/maya.exe") is None)  # nonexistent -> None

    # Regression (caught live): the install scan returns 'maya.EXE' (uppercase) —
    # the suffix check must be case-insensitive or derivation silently fails.
    fake_bin = tempfile.mkdtemp(prefix="btk_fake_maya_bin_")
    open(os.path.join(fake_bin, "mayapy.exe"), "w").close()
    try:
        derived = mayapy_from_maya_exe(os.path.join(fake_bin, "maya.EXE"))
        check("mayapy derivation is suffix-case-insensitive",
              derived is not None and derived.endswith("mayapy.exe"), str(derived))
    finally:
        import shutil
        shutil.rmtree(fake_bin, ignore_errors=True)
    check("engine reuses the bridge AppSpec (no raise; None or str)",
          MayaSceneImport().maya_path is None
          or isinstance(MayaSceneImport().maya_path, str))
    check("explicit maya_path wins",
          MayaSceneImport("Y:/maya.exe").maya_path == "Y:/maya.exe")

    # require_mayapy: fake maya.exe -> no mayapy beside it -> the error must name
    # the exe it derived from (NOT claim Maya itself wasn't found).
    try:
        eng.require_mayapy()
        check("require_mayapy raises naming the derivation source", False)
    except FileNotFoundError as e:
        check("require_mayapy raises naming the derivation source",
              "mayapy" in str(e) and "X:/fake/bin/maya.exe" in str(e), str(e))

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
                json.dump({"version": 1, "materials": [
                    # slot-swap primary path (fbx_material matches a slot)
                    {"name": "M_test", "fbx_material": "M_test_fbxsafe",
                     "objects": ["objA"], "files": [tex]},
                    # object-level fallback (no slot carries this name)
                    {"name": "M_fb", "fbx_material": "M_renamed_by_importer",
                     "objects": ["objB"], "files": [tex]},
                    {"name": "M_gone", "fbx_material": "M_gone_fbxsafe",
                     "objects": ["objB"],
                     "files": ["X:/missing.png"]},  # all files gone -> skipped
                ]}, mf)
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

    import blendertk.env_utils.fbx_utils as fbx_utils
    import blendertk.mat_utils._mat_utils as mat_utils
    orig_import = fbx_utils.FbxUtils.import_fbx
    orig_create = mat_utils.create_pbr_material
    orig_assign = mat_utils.assign_mat

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
    mat_utils.create_pbr_material = fake_create
    mat_utils.assign_mat = fake_assign
    try:
        result = StubbedImport().import_scene(
            src, use_cache=False, fbx_options={"use_anim": False}
        )
        check("import_scene returns the imported objects", result == [obj_a, obj_b])
        check("conversion mayapy runs with the fast-startup env",
              all(calls["env"].get(k) == "1" for k in
                  ("MAYA_SKIP_USERSETUP_PY", "MAYA_DISABLE_CIP", "MAYA_DISABLE_CER")))
        check("conversion ran and produced the payload the import consumed",
              calls.get("ran") and calls["fbx"].endswith(".fbx"))
        check("fbx_options forwarded", calls["opts"] == {"use_anim": False})
        check("manifest: materials rebuilt from texture files (missing-file entry skipped)",
              calls.get("created") == [((tex,), "M_test"), ((tex,), "M_fb")],
              f"{calls.get('created')}")
        check("manifest: slot-level swap hit only the matching slot",
              slot_stingray.material.name == "M_test"
              and slot_other.material.name == "untranslated_phong")
        check("manifest: object-level fallback when no slot matches",
              calls.get("assigned") == ([obj_b], "M_fb"))
        check("intermediate FBX removed on success", not os.path.exists(calls["fbx"]))
        check("manifest sidecar removed on success",
              not os.path.exists(calls["fbx"] + ".manifest.json"))

        # conversion cache: identical scene + options -> the second import must
        # NOT relaunch Maya; use_cache=False must force a fresh conversion.
        runs_before = calls["runs"]
        StubbedImport().import_scene(src, fbx_options={"use_anim": False})
        StubbedImport().import_scene(src, fbx_options={"use_anim": False})
        check("conversion cache: second identical import skips the Maya run",
              calls["runs"] == runs_before + 1, f"runs={calls['runs']}")
        StubbedImport().import_scene(src, use_cache=False,
                                     fbx_options={"use_anim": False})
        check("use_cache=False forces a fresh conversion",
              calls["runs"] == runs_before + 2, f"runs={calls['runs']}")
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
        mat_utils.create_pbr_material = orig_create
        mat_utils.assign_mat = orig_assign
        os.remove(src)
        os.remove(tex)

    # ---- public surface --------------------------------------------------------
    import blendertk as btk
    check("btk.import_maya_scene registered", btk.import_maya_scene is import_maya_scene)
    check("btk.MayaSceneImport registered", btk.MayaSceneImport is MayaSceneImport)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
