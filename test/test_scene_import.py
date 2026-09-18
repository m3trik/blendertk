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
        "template: judged-by-artifact contract (hard exit, no teardown)",
        "ProcessExit.hard_exit(code)" in txt
        and "_exit(0)" in txt
        and "FBXExport -f" in txt,
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
                "FBXExportLights -v false",
                "FBXExportEmbeddedTextures",
            )
        ),
    )
    check(
        "template: per-flag tolerance (a missing FBX command must not abort)",
        "FBX flag skipped" in txt,
    )
    # Production pull (2026-09-15): four area lights in the FBX aborted Blender 5.1's
    # importer outright ('CyclesLightSettings' has no 'cast_shadow') -- nothing
    # imported. Lights travel as manifest data through mayatk's own reader.
    check(
        "template: lights ride the manifest, never the FBX",
        "def scene_lights" in txt
        and "BlenderBridge()._manifest_lights(" in txt
        and '"lights": list(lights)' in txt
        and "lights=lights" in txt,
    )
    check(
        "template: progress markers are flushed ProgressRelay lines",
        '"::progress:: {}/{} {}".format(done, total, text), flush=True' in txt
        and '_progress(1, 6, "Preparing skins")' in txt
        and '_progress(4, 6, "Writing the FBX")' in txt,
    )
    import ast as _ast_tpl

    _whole_scene = [
        c
        for c in _ast_tpl.walk(_ast_tpl.parse(txt))
        if isinstance(c, _ast_tpl.Call)
        and getattr(c.func, "id", None) == "SmartBake"
        and any(
            k.arg == "bake_blend_shapes" and getattr(k.value, "value", None) is True
            for k in c.keywords
        )
    ]
    check(
        "template: every whole-scene-shaped bake skips its inner key optimization (the FBX write re-samples it)",
        # The rig mode adds a SCOPED pass of the same shape (bake_blend_shapes=True);
        # the property must hold for ALL of them, not for exactly one.
        bool(_whole_scene)
        and all(
            any(
                k.arg == "optimize_keys" and getattr(k.value, "value", None) is False
                for k in c.keywords
            )
            for c in _whole_scene
        ),
    )
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
    from blendertk.env_utils.maya_bridge._scene_import import _mayatk_syspath

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
    # Whitespace-normalized on purpose: this asserts the template's GATING LOGIC,
    # and a formatter is free to wrap a long condition across lines -- ruff did
    # exactly that (2026-09-17), which made a pure layout change read as a missing
    # gate. The tokens are what matter; where the line breaks fall is not.
    flat = " ".join(txt.split())
    check(
        "template: smart-bake gated by INCLUDE_ANIMATION + cheap probe (or True force)",
        "SMART_BAKE and INCLUDE_ANIMATION" in flat
        and "SMART_BAKE is True or _detect_complex_anim(cmds)" in flat,
    )
    check(
        "template: bake reuses mayatk SmartBake (not a reimplementation)",
        "SmartBake(" in txt
        and "bake_inherited_visibility=True" in txt
        and "restorable=False" in txt,  # throwaway conversion scene
    )
    # BACKLOG 2026-08-02: the pre-pass used to enable bake_inherited_visibility
    # over objects=None (EVERY transform), so a scene carrying RenderOpacity
    # fades (a fade = the GAP between two opposite .visibility keys) came back
    # with keys inserted inside the gap. The whole-scene pass must leave
    # visibility alone; the inherited-vis bake is a SECOND, scoped pass.
    check(
        "template: whole-scene pass never enables the inherited-vis bake",
        "bake_inherited_visibility=False" in txt
        and "def _inherited_visibility_targets" in txt
        and "objects=vis_targets" in txt,
    )
    import ast as _ast

    # The scoped bake is the ONLY consumer of `vis_targets`, but
    # `_collect_baked_visibility` also folds in the AUTHORED curves the bake
    # deliberately refuses to touch -- and those are carried by nothing else,
    # since the FBX route cannot express visibility at all. So the collection
    # must NOT sit inside the `if vis_targets:` / `if vis_only:` branch: gated
    # there, a scene with authored fades but no unkeyed child under a keyed
    # ancestor (the common case) shipped no visibility whatsoever. Checked
    # structurally rather than by string, because the defect IS the nesting.
    _bake_fn = next(
        (
            n
            for n in _ast.parse(txt).body
            if isinstance(n, _ast.FunctionDef) and n.name == "_run_smart_bake"
        ),
        None,
    )
    if _bake_fn is None:
        check(
            "template: visibility collected regardless of what the bake found",
            False,
            "_run_smart_bake missing from the template",
        )
    else:

        def _calls(node):
            return [
                c
                for c in _ast.walk(node)
                if isinstance(c, _ast.Call)
                and isinstance(c.func, _ast.Name)
                and c.func.id == "_collect_baked_visibility"
            ]

        _all = _calls(_bake_fn)
        # A call is GATED when it lives inside some `if`. Walking the whole
        # function and asking 'is it in the body?' does not work -- `ast.walk`
        # descends through the If, so the buggy nesting reads as ungated too.
        # Subtracting the conditional calls is what actually separates them.
        _conditional = {
            id(c)
            for n in _ast.walk(_bake_fn)
            if isinstance(n, _ast.If)
            for c in _calls(n)
        }
        _ungated = [c for c in _all if id(c) not in _conditional]
        check(
            "template: visibility collected regardless of what the bake found",
            bool(_all) and bool(_ungated),
            "calls={} ungated={}".format(len(_all), len(_ungated)),
        )

        # The scoped visibility pre-pass was inserted AHEAD of the whole-scene
        # `SmartBake(...).execute()` inside the SAME `try`, so a raise anywhere
        # in the narrow new pass skipped the bake that carries constraints, IK,
        # SDKs, motion paths and driven blend shapes -- the FBX shipped those
        # unbaked while the log blamed the pre-pass. One guard per pass;
        # structural, because the defect IS the shared `try`.
        def _calls_named(node, name):
            return [
                c
                for c in _ast.walk(node)
                if isinstance(c, _ast.Call)
                and isinstance(c.func, _ast.Name)
                and c.func.id == name
            ]

        _execute_tries = [
            t
            for t in _ast.walk(_bake_fn)
            if isinstance(t, _ast.Try)
            and any(
                isinstance(c.func, _ast.Attribute) and c.func.attr == "execute"
                for c in _ast.walk(t)
                if isinstance(c, _ast.Call)
            )
        ]
        _shared = [
            t
            for t in _execute_tries
            if _calls_named(t, "_inherited_visibility_targets")
        ]
        check(
            "template: the whole-scene bake is guarded apart from the vis pre-pass",
            bool(_execute_tries) and not _shared,
            "execute-tries={} shared={}".format(len(_execute_tries), len(_shared)),
        )

        # The authored-curve snapshot has to be taken BEFORE the scoped pass
        # writes any: `SmartBake` keys `.visibility` directly on its targets, so
        # a scan run afterwards reports the bake's own keys as authored ones
        # (and repeats the whole-scene transform walk the scoping helper just
        # did). So `_run_smart_bake` snapshots it up front and hands it to the
        # collector; the collector must not re-scan.
        _collect_fn = next(
            (
                n
                for n in _ast.parse(txt).body
                if isinstance(n, _ast.FunctionDef)
                and n.name == "_collect_baked_visibility"
            ),
            None,
        )
        _snapshot = _calls_named(_bake_fn, "_authored_visibility_curves")
        _bakes = [
            c
            for c in _ast.walk(_bake_fn)
            if isinstance(c, _ast.Call)
            and isinstance(c.func, _ast.Attribute)
            and c.func.attr in ("bake", "execute")
        ]
        check(
            "template: authored visibility snapshotted BEFORE the bake writes curves",
            len(_snapshot) == 1
            and bool(_bakes)
            and _snapshot[0].lineno < min(c.lineno for c in _bakes)
            and _collect_fn is not None
            and not _calls_named(_collect_fn, "_authored_visibility_curves"),
            "snapshots={} bakes={}".format(len(_snapshot), len(_bakes)),
        )

    # Behavioural: run the template's scoping helper against a stub ``cmds``
    # (no Maya needed) -- only children whose OWN visibility is unkeyed but
    # that sit under an animated-visibility ancestor may be baked.

    _scope_fn = next(
        (
            n
            for n in _ast.parse(txt).body
            if isinstance(n, _ast.FunctionDef)
            and n.name == "_inherited_visibility_targets"
        ),
        None,
    )
    if _scope_fn is None:
        check(
            "scoping helper: unkeyed children under a keyed ancestor only",
            False,
            "_inherited_visibility_targets missing from the template",
        )
    else:
        _scope_ns = {}
        exec(
            compile(
                _ast.Module(body=[_scope_fn], type_ignores=[]), "<template>", "exec"
            ),
            _scope_ns,
        )
        _scope = _scope_ns["_inherited_visibility_targets"]

        class _StubCmds:
            """Minimal ``cmds`` stand-in: a flat transform list + an animated set."""

            def __init__(self, transforms, animated):
                self._transforms, self._animated = transforms, set(animated)

            def ls(self, *args, **kwargs):
                return list(self._transforms)

            def listConnections(self, plug, **kwargs):
                return ["curve1"] if plug.rsplit(".", 1)[0] in self._animated else []

        _targets = _scope(
            _StubCmds(
                [
                    "|keyed_grp",
                    "|keyed_grp|child_geo",
                    "|keyed_grp|own_keyed_geo",
                    "|keyed_grp|child_geo|grandchild_geo",
                    "|free_geo",
                ],
                {"|keyed_grp", "|keyed_grp|own_keyed_geo"},
            )
        )
        check(
            "scoping helper: unkeyed children under a keyed ancestor only",
            sorted(_targets)
            == ["|keyed_grp|child_geo", "|keyed_grp|child_geo|grandchild_geo"],
            str(sorted(_targets)),
        )
        _none = _scope(_StubCmds(["|a", "|a|b"], set()))
        check(
            "scoping helper: no animated ancestor anywhere -> nothing to bake",
            _none == [],
            str(_none),
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

    # import_scene hands the payload to import_payload -- the ONE consumer the bake
    # template and mayatk's receiving templates run too -- so the replays live there.
    check(
        "import_scene routes through import_payload (one consumer for every door)",
        "self.import_payload(" in _inspect.getsource(MayaSceneImport.import_scene),
    )
    import_src = _inspect.getsource(MayaSceneImport.import_payload)
    check(
        "import_payload replays the manifest's visibility section (non-fatal contract)",
        "_apply_visibility_manifest" in import_src
        and "manifest_path" in import_src
        and ".vis.json" not in import_src,
    )
    from blendertk.env_utils.maya_bridge._scene_import import _BAKE_TEMPLATE

    bake_txt = _BAKE_TEMPLATE.read_text()
    check(
        "bake template replays the manifest through the SHARED engine consumer",
        'MayaSceneImport(log_level="INFO").import_payload(' in bake_txt
        and "scene_settings=True" in bake_txt
        and "reduce_keys=REDUCE_KEYS" in bake_txt
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
        check(
            "visibility replay tolerates a missing/unreadable manifest", False, repr(e)
        )
    # Frame alignment (live-verified bug): Blender's FBX importer shifts every
    # imported curve by anim_offset (default 1.0), so raw-Maya-frame visibility must
    # be shifted by the SAME amount or it desyncs a frame from the transforms.
    check(
        "visibility replay shifts by the FBX importer's anim_offset (frame alignment)",
        "frame + frame_offset"
        in _inspect.getsource(MayaSceneImport._apply_visibility_manifest)
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
        check(
            "scan: keyed visibility (_visibility curve) -> complex",
            scan(ma_vis) is True,
        )
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

    mb_con = _write_mb("btk_cx_con.mb", b"\x00\x01FOR4junk parentConstraint junk\x02")
    mb_vis = _write_mb("btk_cx_vis.mb", b"FOR4 animCurveTU LOC_parent_visibility")
    mb_plain = _write_mb(
        "btk_cx_plain.mb", b"FOR4 transform mesh animCurveTL cube_translateX"
    )
    try:
        check("mb scan: constraint token -> complex", scan(mb_con) is True)
        check("mb scan: visibility-curve token -> complex", scan(mb_vis) is True)
        check(
            "mb scan: plain keys/static tokens -> not complex", scan(mb_plain) is False
        )
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
            key = getattr(self, "authored", {}).get(name.rsplit(".", 1)[0], name)
            times, values = self._curves[key]
            return list(times) if timeChange else list(values)

        # _authored_visibility_curves sweeps the scene for transforms that
        # already own a .visibility curve. `authored` maps such a transform to
        # the curve key this double serves keyframe() from.
        def ls(self, *a, **k):
            return list(getattr(self, "authored", {}))

        def listConnections(self, plug, **k):
            transform = plug.rsplit(".", 1)[0]
            return ["curve"] if transform in getattr(self, "authored", {}) else []

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
    _vis = _collect(_fake_cmds, _fake_result, {})
    check(
        "visibility collect: ambiguous duplicate short name dropped (never one curve on both)",
        "wheel" not in _vis,
        str(_vis),
    )
    # The bake deliberately skips objects that already own a visibility curve
    # (re-keying them splits RenderOpacity fade ramps). They must still reach
    # the manifest: Blender's FBX importer drops visibility outright, so the
    # sidecar is the ONLY route, and excluding them from both dropped their
    # animation silently.
    _authored_cmds = _FakeCmds({"cFade": ([1.0, 11.0], [0.0, 1.0])})
    _authored_cmds.authored = {"|fx|glow": "cFade"}
    _vis_authored = _collect(
        _authored_cmds,
        SimpleNamespace(visibility_curves={}),
        ns_exec["_authored_visibility_curves"](_authored_cmds),
    )
    check(
        "visibility collect: an un-baked authored curve still reaches the manifest",
        _vis_authored.get("glow") == [[1.0, 0.0], [11.0, 1.0]],
        str(_vis_authored),
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
    check(
        "render: USD route omits SMART_BAKE (FBX-only feature)", "SMART_BAKE" not in usd
    )

    # ---- rig_mode: ONE parameter for how rig logic travels (schema 15.1) ------
    import ast as _ast
    import json as _json
    import warnings as _warnings

    for mode, smart in (
        ("auto", "'auto'"),
        ("bake", "True"),
        ("raw", "False"),
        ("rig", "False"),
    ):
        s = eng.render_script(r"C:\s.ma", r"C:\o.fbx", via="fbx", rig_mode=mode)
        check(
            f"render: rig_mode={mode!r} -> RIG_MODE = {mode!r} and SMART_BAKE = {smart} (FBX)",
            f"RIG_MODE = {mode!r}" in s and f"SMART_BAKE = {smart}" in s,
        )
    s = eng.render_script(r"C:\s.ma", r"C:\o.fbx", via="fbx", rig_mode="rig")
    cap_line = next(
        (ln for ln in s.splitlines() if ln.startswith("RIG_CAPABILITY = ")), ""
    )
    check(
        "render: rig mode ships the CONSUMER's capability manifest (FBX)",
        bool(cap_line)
        and _json.loads(_ast.literal_eval(cap_line.split("=", 1)[1].strip()))["target"]
        == "blender",
    )
    u = eng.render_script(r"C:\s.ma", r"C:\o.usd", via="usd", rig_mode="rig")
    check(
        "render: rig mode applies to the USD route too (RIG_MODE + RIG_CAPABILITY)",
        "RIG_MODE = 'rig'" in u and "RIG_CAPABILITY = " in u and "SMART_BAKE" not in u,
    )
    r = eng.render_script(r"C:\s.ma", r"C:\o.fbx", via="fbx", rig_mode="auto")
    check("render: a non-rig mode ships NO capability", "RIG_CAPABILITY = ''" in r)
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        legacy = eng.render_script(r"C:\s.ma", r"C:\o.fbx", via="fbx", smart_bake=True)
    check(
        "render: smart_bake=True is a deprecated alias for rig_mode='bake'",
        "SMART_BAKE = True" in legacy
        and "RIG_MODE = 'bake'" in legacy
        and any(issubclass(w.category, DeprecationWarning) for w in caught),
    )
    check(
        "cache key: rig_mode is part of the conversion identity on BOTH routes",
        MayaSceneImport._cache_key(__file__, {"rig_mode": "rig"}, "usd")
        != MayaSceneImport._cache_key(__file__, {"rig_mode": "auto"}, "usd"),
    )
    check("manifest section name is data", MayaSceneImport.RIG_SECTION == "rig")
    # Found by the production run, not the unit tests: convert() forwards EVERY
    # script option into render_script, and the capability hash that belongs in
    # the cache key alone must be stripped on that trip -- so the test goes
    # through convert(), where the forwarding happens, with the run stubbed.
    _opts = {}
    eng._rig_mode_opts(_opts, "rig")
    _rendered = {}
    _orig_run, _orig_req = eng._run_script, eng.require_mayapy
    eng.require_mayapy = lambda: "mayapy"
    eng._run_script = lambda mayapy, script, **kw: (
        _rendered.setdefault("script", script),
        None,
    )[1]
    # A REAL source: convert() refuses a missing file before it renders, and a
    # check that never reaches the seam would pass vacuously.
    _rt_dir = os.path.join(HERE, "temp_tests")
    os.makedirs(_rt_dir, exist_ok=True)
    _rt_src = os.path.join(_rt_dir, "_rig_roundtrip.ma")
    with open(_rt_src, "w") as _fh:
        _fh.write(
            "//Maya ASCII 2025 scene"
            + chr(10)
            + 'createNode transform -n "cube";'
            + chr(10)
        )
    try:
        try:
            eng.convert(
                _rt_src, os.path.join(_rt_dir, "_rig_roundtrip.usd"), via="usd", **_opts
            )
        except (
            Exception
        ) as _e:  # the stubbed run returns None; anything else is the failure
            _rendered["error"] = repr(_e)
    finally:
        eng._run_script, eng.require_mayapy = _orig_run, _orig_req
        for _p in (_rt_src, os.path.join(_rt_dir, "_rig_roundtrip.usd")):
            if os.path.exists(_p):
                os.remove(_p)
    check(
        "rig mode's cache-key-only options are stripped on the trip into render_script",
        "rig_capability" in _opts
        and "script" in _rendered
        and "RIG_MODE = 'rig'" in _rendered["script"]
        and "unexpected keyword" not in _rendered.get("error", ""),
        _rendered.get("error", "")
        or ("rendered" if "script" in _rendered else "never rendered"),
    )

    from blendertk.env_utils.maya_bridge._scene_import import (
        _IMPORT_TEMPLATE_USD as _T_USD,
    )

    fbx_t, usd_t = (
        _IMPORT_TEMPLATE.read_text(encoding="utf-8"),
        _T_USD.read_text(encoding="utf-8"),
    )
    for label, txt_ in (("FBX", fbx_t), ("USD", usd_t)):
        check(
            f"template ({label}): RIG_MODE/RIG_CAPABILITY placeholders + _transfer_rig",
            "RIG_MODE = __RIG_MODE__" in txt_
            and "RIG_CAPABILITY = __RIG_CAPABILITY__" in txt_
            and "def _transfer_rig" in txt_,
        )

    def _fn(src_text, name):
        for node in _ast.parse(src_text).body:
            if isinstance(node, _ast.FunctionDef) and node.name == name:
                return _ast.dump(node)
        return None

    check(
        "template: the two _transfer_rig copies are AST-identical (drift guard)",
        _fn(fbx_t, "_transfer_rig") is not None
        and _fn(fbx_t, "_transfer_rig") == _fn(usd_t, "_transfer_rig"),
    )

    # ---- the baked rig's apparatus -------------------------------------------
    # A carrier ships every DAG node as an object, so a rig that could not travel
    # arrives TWICE: its motion, as keys on what renders, AND the apparatus that
    # used to produce it -- which in the target selects, draws and drives nothing
    # (943 of 2727 transforms on the production module, ~1100 objects delivered).
    # The producer NAMES it and the consumer drops it: deleting at the source
    # would delete the motion the USD route is about to sample.
    for label, txt_ in (("FBX", fbx_t), ("USD", usd_t)):
        check(
            f"template ({label}): names the baked rig's apparatus into the manifest",
            "def _classify_rig_machinery" in txt_
            and "_classify_rig_machinery(cmds, rig)" in txt_
            and '"machinery": machinery' in txt_
            and 'RIG_MODE == "raw"' in txt_,
        )
    check(
        "template: the two _classify_rig_machinery copies are AST-identical (drift guard)",
        _fn(fbx_t, "_classify_rig_machinery") is not None
        and _fn(fbx_t, "_classify_rig_machinery")
        == _fn(usd_t, "_classify_rig_machinery"),
    )

    # Behavioural: the census, extracted from the template and run against a stub
    # scene. What it must NOT name is the half that matters -- "draws nothing" is
    # not "is a rig", or a scene's own export marker goes with the controls.
    _m_src = fbx_t[
        fbx_t.index("def _classify_rig_machinery") : fbx_t.index(
            "def _inherited_visibility_targets"
        )
    ]
    _m_ns = {"traceback": traceback}
    exec(compile(_m_src, "_classify_rig_machinery.py", "exec"), _m_ns)
    _M_SCENE = {
        "|grp": ("transform", []),
        "|grp|cube": ("transform", ["mesh"]),
        "|grp|cube|cube_parentConstraint1": ("parentConstraint", []),
        "|rig": ("transform", []),
        "|rig|ctrl_GRP": ("transform", []),
        "|rig|ctrl_GRP|ctrl": ("transform", ["nurbsCurve"]),
        "|rig|ctrl_GRP|ctrl|ctrl_pointConstraint1": ("pointConstraint", []),
        "|rig|up_loc": ("transform", ["locator"]),
        "|rig|ik": ("ikHandle", []),
        "|rig|ik_curve": ("transform", ["nurbsCurve"]),
        "|skel": ("joint", []),
        "|skel|j1": ("joint", []),
        "|driver_jnt": ("joint", []),
        "|artist_null": ("transform", []),
        "|data_export": ("transform", ["locator"]),
        "|cam": ("transform", ["camera"]),
        "|particles": ("transform", ["nParticle"]),
    }
    _M_SKINS = {
        "skinCube": {"geometry": ["cubeShape"], "influence": ["|skel|j1"]},
        "skinCurve": {"geometry": ["ik_curveShape"], "influence": ["|driver_jnt"]},
    }
    _M_SHAPES = {"cubeShape": "mesh", "ik_curveShape": "nurbsCurve"}
    for _p, (_t, _sh) in _M_SCENE.items():
        if _sh:
            _M_SHAPES[_p.rsplit("|", 1)[-1] + "Shape"] = _sh[0]

    class _MachineryCmds:
        def ls(self, *a, **k):
            kind = k.get("type")
            if kind == "transform":
                return sorted(_M_SCENE)
            if kind == "skinCluster":
                return sorted(_M_SKINS)
            if kind == "constraint":
                return [p for p, v in _M_SCENE.items() if v[0].endswith("Constraint")]
            if kind in ("ikHandle", "ikEffector"):
                return [p for p, v in _M_SCENE.items() if v[0] == kind]
            if k.get("dag"):  # transforms AND the shapes under them
                return sorted(
                    list(_M_SCENE)
                    + [
                        p + "|" + p.rsplit("|", 1)[-1] + "Shape"
                        for p, v in _M_SCENE.items()
                        if v[1]
                    ]
                )
            if a and a[0]:
                return [a[0]] if a[0] in _M_SCENE else []
            return []

        def nodeType(self, node):
            if node in _M_SCENE:
                return _M_SCENE[node][0]
            return _M_SHAPES.get(node, "unknown")

        def listRelatives(self, node, **k):
            if k.get("shapes"):
                return [node.rsplit("|", 1)[-1] + "Shape"] if _M_SCENE[node][1] else []
            if k.get("allDescendents"):
                return [p for p in _M_SCENE if p.startswith(node + "|")]
            return []

        def skinCluster(self, skin, query=False, geometry=False, influence=False):
            return _M_SKINS[skin]["geometry" if geometry else "influence"]

    _M_GRAPH = {
        "nodes": [
            {"id": f"n{i}", "path": p}
            for i, p in enumerate(
                ("|rig|ctrl_GRP|ctrl", "|rig|ik_curve", "|driver_jnt", "|skel|j1")
            )
        ],
        "records": [{"id": "r1", "target": {"id": "n0"}, "sources": [{"id": "n2"}]}],
    }
    _named = _m_ns["_classify_rig_machinery"](_MachineryCmds(), {"graph": _M_GRAPH})
    check(
        "machinery census: the rig's apparatus by kind, wrapper groups included",
        _named
        == {
            "|grp|cube|cube_parentConstraint1": "constraint",
            "|rig": "group",
            "|rig|ctrl_GRP": "group",
            "|rig|ctrl_GRP|ctrl": "control",
            "|rig|ctrl_GRP|ctrl|ctrl_pointConstraint1": "constraint",
            "|rig|up_loc": "locator",
            "|rig|ik": "ik",
            "|rig|ik_curve": "control",
            "|driver_jnt": "joint",
        },
        str(sorted(_named.items())),
    )
    # The half that matters: apparatus means CONNECTED TO A RIG. A scene's own
    # locator draws nothing either, and deleting it would be a data loss the user
    # never asked for.
    check(
        "machinery census: content, influences and the scene's own nulls are kept",
        not (
            {
                "|grp",
                "|grp|cube",
                "|skel",
                "|skel|j1",
                "|artist_null",
                "|data_export",
                "|cam",
                "|particles",
            }
            & set(_named)
        ),
        str(sorted(_named)),
    )
    # Neither carrier keeps a Maya DAG path, so the consumer matches by LEAF
    # name -- and a mirrored rig repeats short names. Ambiguity is resolved where
    # the paths are, in favour of keeping.
    _M_SCENE["|keepme"] = ("transform", [])
    _M_SCENE["|keepme|up_loc"] = ("transform", ["locator"])
    _collide = _m_ns["_classify_rig_machinery"](_MachineryCmds(), {"graph": _M_GRAPH})
    del _M_SCENE["|keepme"], _M_SCENE["|keepme|up_loc"]
    check(
        "machinery census: a short name shared with a node that must survive is KEPT",
        "|rig|up_loc" not in _collide
        and "|keepme|up_loc" not in _collide
        and "|rig|ctrl_GRP|ctrl" in _collide,
        str(sorted(_collide)),
    )
    check(
        "machinery census: a BUILT record keeps its nodes and their wrapper groups",
        not (
            {"|rig|ctrl_GRP|ctrl", "|driver_jnt", "|rig|ctrl_GRP"}
            & set(
                _m_ns["_classify_rig_machinery"](
                    _MachineryCmds(), {"graph": _M_GRAPH, "plan": {"build": ["r1"]}}
                )
            )
        ),
    )

    # The consumer half: drop what was named, and refuse anything still load-bearing.
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _coll = bpy.context.scene.collection
    _mesh = bpy.data.meshes.new("body")
    _mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    _body = bpy.data.objects.new("body", _mesh)
    _arm = bpy.data.objects.new("skel", bpy.data.armatures.new("skel"))
    _ctrl = bpy.data.objects.new("ctrl", None)
    _ctrl_shape = bpy.data.objects.new("ctrlShape", None)
    _up = bpy.data.objects.new("up_loc", None)
    _marker = bpy.data.objects.new("data_export", None)
    _driven = bpy.data.objects.new("driven_ctrl", None)
    for _o in (_body, _arm, _ctrl, _ctrl_shape, _up, _marker, _driven):
        _coll.objects.link(_o)
    _ctrl_shape.parent = _ctrl
    _body.modifiers.new("Armature", "ARMATURE").object = _arm
    _driven.constraints.new("COPY_LOCATION")
    # A rig builds onto a BONE, but what the manifest names -- and this deletes --
    # is the armature OBJECT around it.
    from blendertk.rig_utils._rig_utils import RigUtils as _RigUtils

    _bone_rig = _RigUtils.create_armature("bone_rig")
    _bone_names = _RigUtils.add_bone_chain(
        _bone_rig, [(0, 0, 0), (0, 1, 0)], prefix="b"
    )
    _bone_rig.pose.bones[_bone_names[0]].constraints.new("COPY_LOCATION")
    _section = {
        "|rig|ctrl": "control",
        "|rig|up_loc": "locator",
        "|skel": "joint",  # a lie: something is skinned to it
        "|rig|driven_ctrl": "control",  # a lie: a rebuilt rig drives it
        "|rig|bone_rig": "joint",  # a lie: a rebuilt rig drives one of its BONES
    }
    _kept = MayaSceneImport()._strip_rig_machinery(
        ptk.HandoffManifest({"machinery": _section}),
        [_body, _arm, _ctrl, _ctrl_shape, _up, _marker, _driven, _bone_rig],
    )
    _live = {o.name for o in bpy.data.objects}
    check(
        "machinery strip: the named apparatus goes, with the shape objects under it",
        not ({"ctrl", "ctrlShape", "up_loc"} & _live),
        str(sorted(_live)),
    )
    check(
        "machinery strip: a deforming armature, a rig-driven object and the "
        "scene's own marker are refused",
        {"body", "skel", "data_export", "driven_ctrl"} <= _live,
        str(sorted(_live)),
    )
    check(
        "machinery strip: an armature a rebuilt rig drives through a BONE is refused",
        "bone_rig" in _live,
        str(sorted(_live)),
    )
    check(
        "machinery strip: the returned list is the survivors only",
        {o.name for o in _kept} == _live,
        str(sorted(o.name for o in _kept)),
    )

    # Rig mode must still carry VISIBILITY. The FBX writes the curve and
    # Blender's importer drops it, so a scene's authored opacity fades reach
    # Blender only through the manifest's `visibility` section -- which only
    # `_run_smart_bake` fills. Rig mode replaces smart-bake's WHOLE-SCENE bake
    # (its plan bakes exactly what it could not build), never the visibility
    # half; the first cut used `if rig / elif smart_bake`, and every fade was
    # silently lost the moment rig mode was on.
    def _rig_branch_calls(src_text, callee):
        """Does main()'s `RIG_MODE == "rig"` branch call *callee*?"""
        for node in _ast.walk(_ast.parse(src_text)):
            if not (isinstance(node, _ast.FunctionDef) and node.name == "main"):
                continue
            for sub in _ast.walk(node):
                if not isinstance(sub, _ast.If):
                    continue
                test = _ast.dump(sub.test)
                if "'rig'" not in test and '"rig"' not in test:
                    continue
                for call in _ast.walk(_ast.Module(body=sub.body, type_ignores=[])):
                    if (
                        isinstance(call, _ast.Call)
                        and getattr(call.func, "id", "") == callee
                    ):
                        return True
        return False

    check(
        "template (FBX): rig mode still collects visibility (authored fades cannot travel otherwise)",
        _rig_branch_calls(fbx_t, "_run_smart_bake"),
    )
    check(
        "template (FBX): _run_smart_bake can skip ONLY the whole-scene pass (whole_scene seam)",
        "def _run_smart_bake(cmds, whole_scene=True):" in fbx_t
        and "_run_smart_bake(cmds, whole_scene=False)" in fbx_t,
    )
    # The consumer step: a manifest `rig` section is BUILT through the builder.
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _a = bpy.data.objects.new("ctrl_a", None)
    _b = bpy.data.objects.new("driven", None)
    for _o in (_a, _b):
        bpy.context.scene.collection.objects.link(_o)
    _a["stretch"] = 0.0
    _graph = {
        "version": 1,
        "source": {"app": "maya", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [{"id": "/rig/ctrl_a"}, {"id": "/rig/driven"}],
        "records": [
            {
                "id": "r1",
                "shape": "channel",
                "op": "linear",
                "target": "/rig/driven.scale.y",
                "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
                "params": {"scale": 2.0, "offset": 0.0},
            }
        ],
    }
    _res = MayaSceneImport()._apply_rig_section(
        {"rig": {"graph": _graph}}, [_a, _b], is_usd=False
    )
    check(
        "import_payload step: a manifest rig section is built (driver lands on the target)",
        _res is not None
        and _res.get("built") == ["r1"]
        and _b.animation_data is not None
        and any(
            fc.data_path == "scale" and fc.array_index == 1
            for fc in _b.animation_data.drivers
        ),
    )
    # A failed build takes its whole rig component back, samples or not: r2
    # targets a node the payload never made, and r1 (sharing the control)
    # is `cascaded` with it -- a rig component is all-or-nothing.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _a = bpy.data.objects.new("ctrl_a", None)
    _b = bpy.data.objects.new("driven", None)
    for _o in (_a, _b):
        bpy.context.scene.collection.objects.link(_o)
    _a["stretch"] = 0.0

    def _linear(rid, target):
        return {
            "id": rid,
            "shape": "channel",
            "op": "linear",
            "target": target,
            "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
            "params": {"scale": 2.0, "offset": 0.0},
        }

    _graph2 = {
        "version": 1,
        "source": {"app": "maya", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [{"id": "/rig/ctrl_a"}, {"id": "/rig/driven"}, {"id": "/rig/missing"}],
        "records": [
            _linear("r1", "/rig/driven.scale.y"),
            _linear("r2", "/rig/missing.scale.x"),
        ],
    }
    _res2 = MayaSceneImport()._apply_rig_section(
        {"rig": {"graph": _graph2}}, [_a, _b], is_usd=False
    )
    _kinds = {
        e["record"]: e["kind"]
        for e in (_res2 or {}).get("report", [])
        if e["kind"] in ("failed", "cascaded")
    }
    _drivers = (
        [fc.data_path for fc in _b.animation_data.drivers] if _b.animation_data else []
    )
    check(
        "import_payload step: a failed build takes its rig component back (cascaded), samples or not",
        _res2 is not None
        and _res2.get("built") == []
        and _kinds == {"r2": "failed", "r1": "cascaded"}
        and "scale" not in _drivers,
        str((_kinds, _drivers)),
    )

    dirs = _mayatk_syspath()

    def _holds(d, pkg):
        return os.path.isfile(os.path.join(d, pkg, "__init__.py"))

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
            for d in _mayatk_syspath(mayatk_path="X:/definitely/not/mayatk")
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
        def _run_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
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
            "convert: smart_bake=False still injects mayatk (the skin pre-pass "
            "runs on both routes)",
            bool(mayatk_parent) and mayatk_parent in pp_off,
            pp_off,
        )
        EnvCaptureImport().convert(src2, out2, via="usd")
        pp_usd = (envs["last"] or {}).get("PYTHONPATH", "")
        check(
            "convert: the USD route injects the mayatk parent too",
            bool(mayatk_parent) and mayatk_parent in pp_usd,
            pp_usd,
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
        "import_payload(" in bake_txt
        and "replay_instances" in import_src
        and "_rollback_import" in import_src,
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
    # ---- !resetXformStack!: Blender's importer ignores a prim's own opinion ------
    # The token means "my local transform IS my world transform -- ignore every
    # ancestor"; Maya writes it for an offsetParentMatrix-driven node. Blender parents
    # such an object normally, so an ANIMATED ancestor drags it away. Measured on a
    # production pull: 7 dual-quaternion wire looms 1.0-1.4 m out of
    # position, matching Maya to 0.001 m once re-rooted.
    try:
        from pxr import Gf, Usd, UsdGeom, UsdSkel

        _rx_ok = True
    except ImportError:
        _rx_ok = False
    check("resetXformStack: pxr available to author the fixture", _rx_ok)
    if _rx_ok:
        import shutil as _rx_shutil

        import bpy

        from blendertk.env_utils.usd import UsdUtils

        _rx_dir = tempfile.mkdtemp(prefix="btk_resetxform_")
        _rx_usd = os.path.join(_rx_dir, "reset.usda")
        _rx_objs = []
        try:
            _st = Usd.Stage.CreateNew(_rx_usd)
            _rt = UsdGeom.Xform.Define(_st, "/rx_root")
            _rop = _rt.AddTranslateOp()
            _rop.Set(Gf.Vec3d(0, 0, 0), 1.0)
            _rop.Set(Gf.Vec3d(100, 0, 0), 10.0)  # the ancestor MOVES
            _ch = UsdGeom.Xform.Define(_st, "/rx_root/rx_child")
            _ch.AddTranslateOp().Set(Gf.Vec3d(0, 0, 50))  # static, parent-independent
            _ch.SetResetXformStack(True)
            # NESTED declarer: freeing its parent first must not truncate the path this
            # one is matched by, nor resolve its conversion against a detached root.
            _gk = UsdGeom.Xform.Define(_st, "/rx_root/rx_child/rx_grandchild")
            _gk.AddTranslateOp().Set(Gf.Vec3d(0, 0, 10))
            _gk.SetResetXformStack(True)
            # A SKELETON-BOUND mesh that also declares the token (mayaUsd writes it
            # for a pinned skinned mesh): UsdSkel places it through its skeleton and
            # Blender's Armature modifier deforms RELATIVE to the armature object,
            # so the repair must leave it parented -- freed, it lands its module's
            # motion away from its bones. It is dual-quaternion skinned, too.
            _sr = UsdSkel.Root.Define(_st, "/rx_root/rx_skelroot")
            _sk = UsdSkel.Skeleton.Define(_st, "/rx_root/rx_skelroot/rx_skel")
            _sk.CreateJointsAttr(["j0"])
            _sk.CreateBindTransformsAttr([Gf.Matrix4d(1.0)])
            _sk.CreateRestTransformsAttr([Gf.Matrix4d(1.0)])
            _an = UsdSkel.Animation.Define(_st, "/rx_root/rx_skelroot/rx_skel/anim")
            _an.CreateJointsAttr(["j0"])
            _an.CreateTranslationsAttr([Gf.Vec3f(0, 0, 0)])
            _an.CreateRotationsAttr([Gf.Quatf(1, 0, 0, 0)])
            _an.CreateScalesAttr([Gf.Vec3h(1, 1, 1)])
            UsdSkel.BindingAPI.Apply(
                _sk.GetPrim()
            ).CreateAnimationSourceRel().SetTargets([_an.GetPath()])
            _me = UsdGeom.Mesh.Define(_st, "/rx_root/rx_skelroot/rx_skinned")
            _me.CreatePointsAttr(
                [
                    Gf.Vec3f(0, 0, 0),
                    Gf.Vec3f(10, 0, 0),
                    Gf.Vec3f(10, 10, 0),
                    Gf.Vec3f(0, 10, 0),
                ]
            )
            _me.CreateFaceVertexCountsAttr([4])
            _me.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            _me.AddTranslateOp().Set(Gf.Vec3d(0, 0, 25))
            _me.SetResetXformStack(True)
            _mb = UsdSkel.BindingAPI.Apply(_me.GetPrim())
            _mb.CreateSkeletonRel().SetTargets([_sk.GetPath()])
            _mb.CreateJointIndicesPrimvar(False, 1).Set([0, 0, 0, 0])
            _mb.CreateJointWeightsPrimvar(False, 1).Set([1.0, 1.0, 1.0, 1.0])
            _mb.CreateSkinningMethodAttr().Set("dualQuaternion")
            _st.SetStartTimeCode(1.0)
            _st.SetEndTimeCode(10.0)
            _st.GetRootLayer().Save()

            _rx_objs = list(UsdUtils.import_scene(_rx_usd) or [])
            _kid = next((o for o in _rx_objs if o.name.startswith("rx_child")), None)
            check("resetXformStack: the fixture imported", _kid is not None)
            if _kid is not None:
                bpy.context.scene.frame_set(1)
                _w1 = _kid.matrix_world.translation.copy()
                bpy.context.scene.frame_set(10)
                _w10 = _kid.matrix_world.translation.copy()
                # Red first: this is the defect the repair exists for.
                check(
                    "resetXformStack: Blender's importer lets the ancestor move it",
                    (_w10 - _w1).length > 0.1,
                    f"{list(_w1)} -> {list(_w10)}",
                )
                _n = UsdUtils.honor_reset_xform_stack(_rx_usd, _rx_objs)
                check(
                    "resetXformStack: the repair frees exactly the declaring prims "
                    "(the child AND the nested grandchild, NOT the skeleton-bound mesh)",
                    _n == 2,
                    f"freed {_n}",
                )
                _gkid = next(
                    (o for o in _rx_objs if o.name.startswith("rx_grandchild")), None
                )
                # Its OWN 10-unit offset, converted: -0.1 m on Blender's -Y. Resolving
                # the paths AFTER detaching instead of before would leave this one
                # inheriting the freed child's -0.5 m and landing at -0.6.
                check(
                    "resetXformStack: a NESTED declarer is freed from its freed parent",
                    _gkid is not None
                    and _gkid.parent is None
                    and abs(_gkid.matrix_world.translation.y + 0.1) < 1e-4,
                    str(list(_gkid.matrix_world.translation)) if _gkid else "missing",
                )
                bpy.context.scene.frame_set(1)
                _f1 = _kid.matrix_world.translation.copy()
                bpy.context.scene.frame_set(10)
                _f10 = _kid.matrix_world.translation.copy()
                check(
                    "resetXformStack: after the repair no ancestor motion reaches it",
                    (_f10 - _f1).length < 1e-6,
                    f"{list(_f1)} -> {list(_f10)}",
                )
                # It keeps its OWN offset and gains the stage conversion: 50 along the
                # stage's up axis (Y, 0.01 m/unit) is -0.5 m on Blender's -Y. Not
                # zeroed (dragged to the root) and not left in raw USD units.
                check(
                    "resetXformStack: keeps its own offset, with the stage conversion",
                    abs(_kid.matrix_world.translation.y + 0.5) < 1e-4
                    and abs(_kid.matrix_world.translation.x) < 1e-4,
                    str(list(_kid.matrix_world.translation)),
                )
                check(
                    "resetXformStack: parenting cleared (no STATIC parent-inverse can "
                    "cancel an ANIMATED ancestor)",
                    _kid.parent is None,
                    str(_kid.parent.name if _kid.parent else None),
                )
                _skinned = next(
                    (o for o in _rx_objs if o.name.startswith("rx_skinned")), None
                )
                _arm_mods = [
                    m
                    for m in getattr(_skinned, "modifiers", ())
                    if m.type == "ARMATURE"
                ]
                check(
                    "resetXformStack: the skeleton-bound fixture prim imported bound "
                    "(an Armature modifier)",
                    _skinned is not None and len(_arm_mods) == 1,
                    str([m.type for m in getattr(_skinned, "modifiers", ())]),
                )
                check(
                    "resetXformStack: a skeleton-bound mesh KEEPS its parent (its "
                    "armature places it; freed, it lands the module's motion away)",
                    _skinned is not None and _skinned.parent is not None,
                    str(_skinned.parent.name if _skinned and _skinned.parent else None),
                )
                # Skinning method: Blender binds the skin but never reads the method.
                _methods = UsdUtils.skinning_methods(_rx_usd)
                check(
                    "skinning_methods: the authored UsdSkel method, by prim path",
                    _methods.get("/rx_root/rx_skelroot/rx_skinned") == "dualQuaternion",
                    str(_methods),
                )
                _switched = MayaSceneImport()._apply_skinning_methods(
                    _rx_usd, {}, _rx_objs
                )
                check(
                    "apply_skinning_methods: a dual-quaternion skin gets the Armature "
                    "modifier's Preserve Volume (Blender's own DQS)",
                    _switched == 1
                    and all(m.use_deform_preserve_volume for m in _arm_mods),
                    f"switched {_switched}",
                )
                check(
                    "apply_skinning_methods: already matched -> nothing switched",
                    MayaSceneImport()._apply_skinning_methods(_rx_usd, {}, _rx_objs)
                    == 0,
                )
        finally:
            for _o in _rx_objs:
                try:
                    bpy.data.objects.remove(_o, do_unlink=True)
                except Exception:  # noqa: BLE001
                    pass
            _rx_shutil.rmtree(_rx_dir, ignore_errors=True)

    # ---- skins: the FBX manifest names each mesh's method; ONE pre-pass per route --
    class _Mod:
        def __init__(self, kind):
            self.type = kind
            self.use_deform_preserve_volume = False

    _dq = SimpleNamespace(name="loomA.001", modifiers=[_Mod("ARMATURE")])
    _lin = SimpleNamespace(name="loomB", modifiers=[_Mod("ARMATURE"), _Mod("SUBSURF")])
    _plain = SimpleNamespace(name="wall", modifiers=[])
    _skins = {
        "loomA": "dualQuaternion",
        "loomB": "classicLinear",
        "wall": "dualQuaternion",
    }
    _n_fbx = MayaSceneImport()._apply_skinning_methods(
        "x.fbx", {MayaSceneImport.SKINS_SECTION: _skins}, [_dq, _lin, _plain]
    )
    check(
        "skins manifest (FBX): dual quaternion -> Preserve Volume, through the .001 "
        "rename; classic stays linear; an unskinned object is left alone",
        _n_fbx == 1
        and _dq.modifiers[0].use_deform_preserve_volume is True
        and _lin.modifiers[0].use_deform_preserve_volume is False
        and _lin.modifiers[1].use_deform_preserve_volume is False,
        f"switched {_n_fbx}",
    )
    check(
        "skins manifest (FBX): no section -> nothing switched",
        MayaSceneImport()._apply_skinning_methods("x.fbx", {}, [_dq]) == 0,
    )
    import ast as _ast

    def _fn_dump(text, name):
        node = next(
            (
                n
                for n in _ast.walk(_ast.parse(text))
                if isinstance(n, _ast.FunctionDef) and n.name == name
            ),
            None,
        )
        if node is None:
            return None
        node.body = [n for n in node.body if not isinstance(n, _ast.Expr)]
        return _ast.dump(node)

    _fbx_src = MayaSceneImport._template("fbx").read_text(encoding="utf-8")
    _usd_src = MayaSceneImport._template("usd").read_text(encoding="utf-8")
    check(
        "skins: both templates carry ONE _export_ready_skins (AST-identical copies, "
        "the mayatk-guarded pre-pass)",
        _fn_dump(_fbx_src, "_export_ready_skins") is not None
        and _fn_dump(_fbx_src, "_export_ready_skins")
        == _fn_dump(_usd_src, "_export_ready_skins"),
    )
    check(
        "skins: both routes run the pre-pass BEFORE their export reads the scene, "
        "the FBX route rooting the skeleton at the top level (its importer places a "
        "skinned mesh from the armature node's LOCAL bind matrix), the USD route on "
        "the mesh's own parent chain (its importer keeps the mesh transform literal)",
        _fbx_src.index('_export_ready_skins(cmds, frames, "world")')
        < _fbx_src.index("FBXExport -f")
        and _usd_src.index('"ancestor",')
        < _usd_src.index("export_usd(cmds, frame_range)")
        and _usd_src.index("_export_ready_skins(") < _usd_src.index('"ancestor",'),
    )
    check(
        "skins: the FBX template writes the engine's manifest section",
        '"' + MayaSceneImport.SKINS_SECTION + '": skins or {}' in _fbx_src
        and "def skinning_methods" in _fbx_src
        and "skins=skins" in _fbx_src,
    )

    # ---- bones: the flatten's record puts the skeleton back at the chain's scale --
    # Both carriers infer a bone length from the distance to its children, so the
    # flat skeleton a portable skin needs arrives drawn at the ROOT's spread:
    # measured on production looms, 2.49 m and 0.62 m bones on joints 0.89 cm apart.
    import bpy as _bpy_bone
    from mathutils import Vector
    from blendertk.rig_utils import _rig_utils

    _bpy_bone.ops.wm.read_factory_settings(use_empty=True)
    _ad = _bpy_bone.data.armatures.new("loom")
    _arm = _bpy_bone.data.objects.new("loom", _ad)
    _bpy_bone.context.collection.objects.link(_arm)
    _bpy_bone.context.view_layer.objects.active = _arm
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    _root = _ad.edit_bones.new("root")  # the synthetic root, 2.4 m away
    _root.head, _root.tail = (0, 0, 0), (0, 2.4, 0)
    for _i in range(4):  # the chain, flat, 1 cm apart
        _b = _ad.edit_bones.new("j%d" % _i)
        _b.head = (2.40 + _i * 0.01, 0, 0)
        _b.tail = (2.40 + _i * 0.01, 0.6, 0)  # the length the importer guessed
        _b.parent = _root
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    _before = {b.name: round(b.length, 4) for b in _ad.bones}
    _sized = MayaSceneImport()._apply_bone_lengths(
        {MayaSceneImport.BONES_SECTION: {"j1": "j0", "j2": "j1", "j3": "j2"}}, [_arm]
    )
    _after = {b.name: round(b.length, 4) for b in _ad.bones}
    check(
        "bones manifest: each bone gets the distance to its recorded child, the "
        "chain's leaf inherits its parent's, and the synthetic root the mean -- "
        "not the 2.4 m / 0.6 m spread the carrier guessed",
        _sized == 5
        and _before == {"root": 2.4, "j0": 0.6, "j1": 0.6, "j2": 0.6, "j3": 0.6}
        and _after == {"root": 0.01, "j0": 0.01, "j1": 0.01, "j2": 0.01, "j3": 0.01},
        f"{_before} -> {_after}",
    )
    check(
        "bones manifest: no section -> nothing resized",
        MayaSceneImport()._apply_bone_lengths({}, [_arm]) == 0,
    )
    # A referenced rig's joints are namespaced; both carriers rewrite the colon.
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    for _old, _new in (("j0", "ns_j0"), ("j1", "ns_j1")):
        _ad.edit_bones[_old].name = _new
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    _rig_utils.RigUtils.set_bone_lengths(_arm, {"ns_j0": 0.5})  # something to undo
    _ns = MayaSceneImport()._apply_bone_lengths(
        {MayaSceneImport.BONES_SECTION: {"ns:j1": "ns:j0"}}, [_arm]
    )
    check(
        "bones manifest: a namespaced joint matches the bone the carrier renamed",
        _ns and round(_ad.bones["ns_j0"].length, 4) == 0.01,
        f"{_ns} resized, ns_j0 {_ad.bones['ns_j0'].length}",
    )

    # ---- the synthetic root bone is placed HERE, where it cannot move a skin -----
    # mayaUsd derives bindTransforms from the skinCluster's bindPreMatrix, so the
    # flatten's root -- which influences nothing -- has no bind and goes out as
    # IDENTITY: its bone lands at the WORLD ORIGIN, 2.5 m from its own skin on a
    # production module. Giving it a bind Maya-side (a zero-weight influence) is
    # deformation-neutral in Maya but makes mayaUsd prune it from the MESH's
    # skel:joints while keeping it in the skeleton's, and the pull then missed Maya
    # by 380 mm. A bone nothing is weighted to cannot affect a deform, so it is moved
    # on this side instead.
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    for _old, _new in (("ns_j0", "j0"), ("ns_j1", "j1")):
        _ad.edit_bones[_old].name = _new
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    _skin = _bpy_bone.data.meshes.new("loomGeo")
    _skin.from_pydata([(2.40, 0.0, 0.0), (2.43, 0.0, 0.0)], [], [])
    _skin.update()
    _geo = _bpy_bone.data.objects.new("loomGeo", _skin)
    _bpy_bone.context.collection.objects.link(_geo)
    _geo.vertex_groups.new(name="j0").add([0], 1.0, "REPLACE")
    _geo.vertex_groups.new(name="j3").add([1], 1.0, "REPLACE")
    _geo.modifiers.new("Armature", "ARMATURE").object = _arm
    for _i in range(4):
        _pb = _arm.pose.bones["j%d" % _i]
        _pb.rotation_mode = "XYZ"
        _pb.rotation_euler = (0.3 + _i * 0.1, -0.2, 0.5)

    def _skin_points():
        _dg = _bpy_bone.context.evaluated_depsgraph_get()
        _dg.update()
        _ev = _geo.evaluated_get(_dg)
        _m = _ev.to_mesh()
        _out = [Vector(v.co) for v in _m.vertices]
        _ev.to_mesh_clear()
        return _out

    _skin_was = _skin_points()
    _root_was = Vector(_ad.bones["root"].head_local)
    _placed = MayaSceneImport()._place_skeleton_roots([_arm, _geo])
    _centre = (
        sum((Vector(_ad.bones["j%d" % _i].head_local) for _i in range(4)), Vector())
        / 4.0
    )
    _skin_drift = max((a - b).length for a, b in zip(_skin_was, _skin_points()))
    check(
        "skeleton root: the unweighted root bone moves to the centre of the bones "
        "it anchors, instead of sitting at the world origin",
        _placed == 1
        and (Vector(_ad.bones["root"].head_local) - _centre).length < 1e-6
        and (_root_was - _centre).length > 1.0,
        f"{_root_was} -> {_ad.bones['root'].head_local} (want {_centre})",
    )
    check(
        "skeleton root: moving it does not move the skin (nothing is weighted to it)",
        _skin_drift == 0.0,
        f"skin moved {_skin_drift:.3e} m",
    )
    check(
        "skeleton root: the whole bone translates -- length and direction keep",
        round(_ad.bones["root"].length, 4) == 0.01,
        f"{_ad.bones['root'].length}",
    )
    check(
        "skeleton root: already placed -> idempotent, nothing rewritten",
        MayaSceneImport()._place_skeleton_roots([_arm, _geo]) == 0,
    )
    # The guarantee is "nothing is weighted to it" -- so a root that IS a vertex
    # group is left alone, weights zero or not.
    _geo.vertex_groups.new(name="root")
    check(
        "skeleton root: a root ANY mesh has a vertex group for is never moved",
        MayaSceneImport()._place_skeleton_roots([_arm, _geo]) == 0,
    )
    _geo.vertex_groups.remove(_geo.vertex_groups["root"])
    # An authored rig has many top-level bones; only the flatten's single root is ours.
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    _loose = _ad.edit_bones.new("loose")
    _loose.head, _loose.tail = (9, 9, 9), (9, 9.1, 9)
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    check(
        "skeleton root: an armature with two parentless bones is left alone",
        MayaSceneImport()._place_skeleton_roots([_arm, _geo]) == 0,
    )
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    _ad.edit_bones.remove(_ad.edit_bones["loose"])
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    check(
        "skeleton root: an armature that deforms nothing is left alone (a rig's "
        "proxy joints arrive as one, unweighted and with a single root)",
        MayaSceneImport()._place_skeleton_roots([_arm]) == 0,
    )
    # An authored CHAIN has the same one root and no weights; only the flatten's
    # shape -- every other bone a direct child of the root -- may be touched.
    _bpy_bone.ops.object.mode_set(mode="EDIT")
    _ad.edit_bones["j1"].parent = _ad.edit_bones["j0"]
    _bpy_bone.ops.object.mode_set(mode="OBJECT")
    _chain_head = Vector(_ad.bones["root"].head_local)
    check(
        "skeleton root: a nested (authored) chain keeps the shape its author gave it",
        MayaSceneImport()._place_skeleton_roots([_arm, _geo]) == 0
        and Vector(_ad.bones["root"].head_local) == _chain_head,
    )
    check(
        "bones: both templates write the engine's manifest section from the "
        "pre-pass's record",
        '"' + MayaSceneImport.BONES_SECTION + '": bones or {}' in _fbx_src
        and '"' + MayaSceneImport.BONES_SECTION + '": bones or {}' in _usd_src
        and "bones = _export_ready_skins(" in _fbx_src
        and "bones = _export_ready_skins(" in _usd_src,
    )

    # Bake template: source-generalized (a USD intermediate imports natively, so
    # main() bypasses the texture/visibility replays -- but a sidecar DOES exist
    # beside a USD; it carries the instance grouping, replayed by apply_instances).
    check(
        "bake template: source token generalized (SRC_FILE, no SRC_FBX)",
        "__" + "SRC_FILE" + "__" in bake_txt and "SRC_FBX" not in bake_txt,
    )
    check(
        "import_payload: a USD payload imports natively (UsdUtils branch on the extension)",
        "UsdUtils.import_scene(src" in import_src and "USD_EXTENSIONS" in import_src,
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
        def _run_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
            with open(artifact, "wb") as fh:
                fh.write(b"conv-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        @staticmethod
        def _run_bake_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
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

        for p in [baked_path, baked_path + BAKE_SOURCE_SUFFIX] if baked_path else []:
            if os.path.exists(p):
                os.remove(p)
    # rig_mode applies to BOTH routes (schema 15.1): the same helper surfaces it
    # into the cache key and render context from import_scene AND bake_scene,
    # and the old FBX-only gating of the alias is gone.
    _is_src = _inspect.getsource(MayaSceneImport.import_scene)
    _bs_src = _inspect.getsource(MayaSceneImport.bake_scene)
    check(
        "both routes surface rig_mode through ONE helper (no FBX-only gating)",
        "self._rig_mode_opts(script_opts, rig_mode)" in _is_src
        and "self._rig_mode_opts(script_opts, rig_mode)" in _bs_src
        and 'script_opts["smart_bake"]' not in _is_src + _bs_src,
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
        def _run_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
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
        # scene_settings=False: adoption needs a live scene (bpy); it is covered in
        # the bpy section, and this orchestration also runs under the .venv.
        result = StubbedImport().import_scene(
            src,
            via="fbx",
            use_cache=False,
            fbx_options={"use_anim": False},
            scene_settings=False,
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
        from blendertk.env_utils.maya_bridge._scene_import import FBX_IMPORT_OPTIONS

        check(
            "fbx_options forwarded over the shared FBX_IMPORT_OPTIONS",
            calls["opts"] == dict(FBX_IMPORT_OPTIONS, use_anim=False),
            str(calls["opts"]),
        )
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
        # The USD route replays by material IDENTITY only: its bindings are
        # exact per prim path, and a short-name fallback lands the wrong set's
        # textures on a scene carrying one leaf name under two hierarchies.
        import json as _json_ident

        _ident_manifest = os.path.join(tempfile.gettempdir(), "btk_ident.manifest.json")
        with open(_ident_manifest, "w", encoding="utf-8") as _mf:
            _json_ident.dump(
                {
                    "materials": [
                        {
                            "name": "M_fb2",
                            "fbx_material": "M_not_on_any_slot",
                            "objects": ["objB"],
                            "files": [tex],
                        }
                    ]
                },
                _mf,
            )
        calls.pop("assigned", None)
        MayaSceneImport()._apply_texture_manifest(
            _ident_manifest, [obj_a, obj_b], object_fallback=False
        )
        check(
            "manifest: object_fallback=False never assigns by short object name",
            calls.get("assigned") is None,
            str(calls.get("assigned")),
        )
        os.remove(_ident_manifest)
        check("intermediate FBX removed on success", not os.path.exists(calls["fbx"]))
        check(
            "manifest sidecar removed on success",
            not os.path.exists(calls["fbx"] + ".manifest.json"),
        )

        # conversion cache: identical scene + options -> the second import must
        # NOT relaunch Maya; use_cache=False must force a fresh conversion.
        import glob as _glob

        _cache_glob = os.path.join(tempfile.gettempdir(), "maya_to_btk_cache_*")
        _caches_before = set(_glob.glob(_cache_glob))
        runs_before = calls["runs"]
        StubbedImport().import_scene(
            src, via="fbx", fbx_options={"use_anim": False}, scene_settings=False
        )
        StubbedImport().import_scene(
            src, via="fbx", fbx_options={"use_anim": False}, scene_settings=False
        )
        check(
            "conversion cache: second identical import skips the Maya run",
            calls["runs"] == runs_before + 1,
            f"runs={calls['runs']}",
        )
        StubbedImport().import_scene(
            src,
            via="fbx",
            use_cache=False,
            fbx_options={"use_anim": False},
            scene_settings=False,
        )
        check(
            "use_cache=False forces a fresh conversion",
            calls["runs"] == runs_before + 2,
            f"runs={calls['runs']}",
        )
        # Only the entries THIS test promoted: outside the runner's sandbox the cache
        # store is the real temp dir, and a blanket glob deleted a production
        # scene's cached conversion (2026-09-15).
        for stale in set(_glob.glob(_cache_glob)) - _caches_before:
            os.remove(stale)

        # failure path: import blows up -> intermediate FBX kept for debugging
        def broken_import(filepath, **opts):
            calls["kept"] = filepath
            raise RuntimeError("import boom")

        fbx_utils.FbxUtils.import_fbx = staticmethod(broken_import)
        try:
            StubbedImport().import_scene(
                src, via="fbx", use_cache=False, scene_settings=False
            )
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

    # ---- import_payload: progress, cancellation, level validation --------------
    import json as _json_pl
    import shutil as _shutil_pl

    import blendertk.env_utils.fbx_utils as _fbx_pl
    from blendertk.env_utils.maya_bridge._scene_import import (
        FBX_IMPORT_OPTIONS as _FBX_OPTS,
    )

    _pl_dir = tempfile.mkdtemp(prefix="btk_payload_")
    _pl_fbx = os.path.join(_pl_dir, "payload.fbx")
    with open(_pl_fbx, "wb") as fh:
        fh.write(b"fbx-bytes")
    with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
        _json_pl.dump({"version": 1, "transforms": {"grp": "group"}}, fh)
    _pl_seen = {}

    def _pl_import(filepath, **opts):
        _pl_seen["opts"] = opts
        return []

    _pl_orig = _fbx_pl.FbxUtils.import_fbx
    _fbx_pl.FbxUtils.import_fbx = staticmethod(_pl_import)
    try:
        _reports = []
        MayaSceneImport().import_payload(
            _pl_fbx,
            reduce_keys=False,
            progress=lambda d, t, m: _reports.append((d, t, m)),
        )
        check(
            "import_payload: progress before every step and once at the end",
            [m for _, _, m in _reports]
            == ["Importing the FBX", "Tagging groups and locators", "Imported"]
            and [d for d, _, _ in _reports] == [0, 1, 2]
            and all(t == 2 for _, t, _ in _reports),
            str(_reports),
        )
        # A damaged sidecar costs the sections, never the landed payload: the
        # plan admits nothing section-driven and the import still completes,
        # with one warning naming the file (mirror of mayatk's).
        with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
            fh.write("{not json")
        _reports_bad = []
        _warned = []
        _engine_bad = MayaSceneImport()
        _engine_bad.logger.warning = lambda msg, *a, **k: _warned.append(str(msg))
        _engine_bad.import_payload(
            _pl_fbx,
            reduce_keys=False,
            progress=lambda d, t, m: _reports_bad.append((d, t, m)),
        )
        check(
            "import_payload: an unreadable sidecar warns once and still imports",
            [m for _, _, m in _reports_bad] == ["Importing the FBX", "Imported"]
            and any("Unreadable manifest" in w for w in _warned),
            str(_reports_bad) + " | " + str(_warned),
        )
        # The skins step runs only when the FBX manifest carries the section (a
        # USD payload always authors the method itself).
        with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
            _json_pl.dump({"version": 1, "skins": {"loom": "dualQuaternion"}}, fh)
        _reports_skins = []
        MayaSceneImport().import_payload(
            _pl_fbx,
            reduce_keys=False,
            progress=lambda d, t, m: _reports_skins.append((d, t, m)),
        )
        check(
            "import_payload: an FBX manifest with a skins section adds the "
            "skinning-method step (and only then)",
            [m for _, _, m in _reports_skins]
            == ["Importing the FBX", "Matching skinning methods", "Imported"],
            str(_reports_skins),
        )
        # Same gate for the bones section -- the record of what the flatten removed.
        with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
            _json_pl.dump({"version": 1, "bones": {"j1": "j0"}}, fh)
        _reports_bones = []
        MayaSceneImport().import_payload(
            _pl_fbx,
            reduce_keys=False,
            progress=lambda d, t, m: _reports_bones.append((d, t, m)),
        )
        check(
            "import_payload: a manifest with a bones section adds the skeleton-"
            "sizing step (and only then)",
            [m for _, _, m in _reports_bones]
            == ["Importing the FBX", "Sizing the skeletons", "Imported"],
            str(_reports_bones),
        )
        # Same gate for the shots section -- and it runs LAST, after the lights
        # rebuild replaces objects, because memberships name them.
        _shots_section = {
            "version": 1,
            "store": {
                "shots": [
                    {
                        "shot_id": 0,
                        "name": "Intro",
                        "start": 1.0,
                        "end": 24.0,
                        "objects": ["pCube1"],
                        "metadata": {},
                        "locked": False,
                        "description": "",
                    }
                ],
                "hidden_objects": [],
                "pinned_objects": [],
                "markers": [],
                "gap": 0.0,
                "locked_gaps": [],
                "scene_fps": 24.0,
                "snap_whole_frames": True,
            },
            "ledger": {"steps": {}, "keys": {}},
        }
        with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
            _json_pl.dump(
                {
                    "version": 1,
                    "lights": [{"name": "L", "type": "point"}],
                    "shots": _shots_section,
                },
                fh,
            )
        _reports_shots = []
        _orig_lights = MayaSceneImport._rebuild_lights
        MayaSceneImport._rebuild_lights = staticmethod(lambda manifest_path: {})
        try:
            MayaSceneImport().import_payload(
                _pl_fbx,
                reduce_keys=False,
                progress=lambda d, t, m: _reports_shots.append((d, t, m)),
            )
        finally:
            MayaSceneImport._rebuild_lights = _orig_lights
        check(
            "import_payload: a manifest with a shots section adds the shot-rebuild "
            "step, last",
            [m for _, _, m in _reports_shots]
            == [
                "Importing the FBX",
                "Rebuilding lights",
                "Rebuilding shots",
                "Imported",
            ],
            str(_reports_shots),
        )
        try:
            import bpy as _bpy_shots  # noqa: F401
        except ImportError:
            _bpy_shots = None
        if _bpy_shots is not None:
            from blendertk.anim_utils.shots._shots import BlenderShotStore as _BSS

            check(
                "import_payload: the shot is rebuilt on the scene's store (FBX: bounds "
                "shifted by the importer's anim_offset)",
                [(s.name, s.start, s.end) for s in _BSS.active().shots]
                == [("Intro", 2.0, 25.0)],
                str([(s.name, s.start, s.end) for s in _BSS.active().shots]),
            )
            _BSS.clear_active()
            _bpy_shots.context.scene.pop("shot_store", None)
        _reports_declined = []
        MayaSceneImport().import_payload(
            _pl_fbx,
            reduce_keys=False,
            shots=False,
            progress=lambda d, t, m: _reports_declined.append((d, t, m)),
        )
        check(
            "import_payload: shots=False leaves the section alone",
            "Rebuilding shots" not in [m for _, _, m in _reports_declined],
            str(_reports_declined),
        )
        check(
            "conversion templates: both routes carry the shots through mayatk's store",
            all(
                "def shots_section(cmds, spell):"
                in (_IMPORT_TEMPLATE.parent / t).read_text()
                and "shots=shots" in (_IMPORT_TEMPLATE.parent / t).read_text()
                for t in ("_import_scene.py", "_import_scene_usd.py")
            ),
        )
        with open(_pl_fbx + ".manifest.json", "w", encoding="utf-8") as fh:
            _json_pl.dump({"version": 1, "transforms": {"grp": "group"}}, fh)
        check(
            "import_payload: fbx_options merge over FBX_IMPORT_OPTIONS",
            _pl_seen.get("opts") == _FBX_OPTS,
            str(_pl_seen.get("opts")),
        )
        _pl_seen.clear()
        try:
            MayaSceneImport().import_payload(
                _pl_fbx, reduce_keys=False, progress=lambda d, t, m: False
            )
            check("import_payload: a False progress stops before the first step", False)
        except ptk.OperationCancelled:
            check(
                "import_payload: a False progress stops before the first step",
                "opts" not in _pl_seen,
            )
        try:
            MayaSceneImport().import_payload(_pl_fbx, reduce_keys="no-such-level")
            check(
                "import_payload: an unknown reduce level raises before importing", False
            )
        except ValueError:
            check(
                "import_payload: an unknown reduce level raises before importing",
                "opts" not in _pl_seen,
            )
    finally:
        _fbx_pl.FbxUtils.import_fbx = _pl_orig
        _shutil_pl.rmtree(_pl_dir, ignore_errors=True)

    # ---- bake_scene: child markers drive ONE bar across both stages -------------
    _prog = {}

    class _ProgressStub(MayaSceneImport):
        @staticmethod
        def _run_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
            for line in (
                "a line that is not a marker",
                "::progress:: 1/2 Opening the scene",
                "::progress:: 2/2 Writing the FBX",
            ):
                if on_output is not None and on_output(line) is False:
                    raise ptk.OperationCancelled("stub child stopped")
            with open(artifact, "wb") as fh:
                fh.write(b"conv-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        @staticmethod
        def _run_bake_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
            _prog["bake_script"] = script_text
            if on_output is not None:
                on_output("::progress:: 1/2 Importing the FBX")
            with open(artifact, "wb") as fh:
                fh.write(b"blend-bytes")
            return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

        def require_mayapy(self):
            return "stub_mayapy"

        def require_blender(self):
            return "stub_blender"

    from blendertk.env_utils.maya_bridge._scene_import import (
        BAKE_SOURCE_SUFFIX as _PSUFFIX,
    )

    _psrc = os.path.join(tempfile.gettempdir(), "btk_progress_src.ma")
    with open(_psrc, "w") as fh:
        fh.write("//Maya ASCII scene\n")
    _pbakes = []
    try:
        _preports = []
        _pbakes.append(
            _ProgressStub().bake_scene(
                _psrc,
                use_cache=False,
                progress=lambda c, t, m: _preports.append((c, t, m)),
            )
        )
        _pvalues = [c for c, _, _ in _preports if c is not None]
        check(
            "bake_scene progress: child markers map onto one bar across both stages",
            (25, 100, "Maya: Opening the scene") in _preports
            and (50, 100, "Maya: Writing the FBX") in _preports
            and (75, 100, "Blender: Importing the FBX") in _preports
            and _pvalues == sorted(_pvalues)
            and _pvalues[-1] == 100,
            str(_preports),
        )
        check(
            "bake_scene: ordinary child output keeps the bar alive (a keep-alive tick)",
            (None, 100, None) in _preports,
            str(_preports),
        )
        check(
            "bake_scene: the default reduction level reaches the bake script",
            "REDUCE_KEYS = 'extremes'" in _prog.get("bake_script", ""),
        )
        _pbakes.append(
            _ProgressStub().bake_scene(_psrc, use_cache=False, reduce_keys=False)
        )
        check(
            "bake_scene: reduce_keys=False keeps every key (REDUCE_KEYS = None)",
            "REDUCE_KEYS = None" in _prog.get("bake_script", ""),
        )
        _prog.pop("bake_script", None)
        try:
            _ProgressStub().bake_scene(_psrc, use_cache=False, reduce_keys="bogus")
            check(
                "bake_scene: an unknown reduce level fails before any child runs", False
            )
        except ValueError:
            check(
                "bake_scene: an unknown reduce level fails before any child runs",
                "bake_script" not in _prog,
            )
        try:
            _ProgressStub().bake_scene(
                _psrc, use_cache=False, progress=lambda c, t, m: False
            )
            check("bake_scene: a False progress stops the running child", False)
        except ptk.OperationCancelled:
            check(
                "bake_scene: a False progress stops the running child",
                "bake_script" not in _prog,
            )
        # The reduction shapes the bake, so it is part of the bake cache key.
        _key_parts = []
        _orig_key2 = ptk.CachedArtifact.__dict__["key"]

        def _capture_parts(*parts, files=(), length=16):
            _key_parts.append((parts, list(files)))
            return _orig_key2.__func__(*parts, files=files, length=length)

        ptk.CachedArtifact.key = staticmethod(_capture_parts)
        try:
            for _level in ("extremes", False):
                _pbakes.append(
                    _ProgressStub().bake_scene(
                        _psrc, use_cache=False, reduce_keys=_level
                    )
                )
        finally:
            ptk.CachedArtifact.key = _orig_key2
        _bake_parts = [
            p
            for p, fs in _key_parts
            if any(
                str(f).endswith(".blend") is False and "reduce_keys" in str(p)
                for f in fs
            )
        ]
        check(
            "bake cache key: the reduction level and its bound are part of the identity",
            len(_bake_parts) == 2
            and _bake_parts[0] != _bake_parts[1]
            and any(
                MayaSceneImport.KEY_REDUCTION_MAX_ERROR in p[0] for p in _bake_parts
            ),
            str(_bake_parts),
        )
    finally:
        os.remove(_psrc)
        for _pb in _pbakes:
            for _p in (_pb, _pb + _PSUFFIX):
                if _p and os.path.exists(_p):
                    os.remove(_p)

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
            return staticmethod(
                lambda textures, config=None: {
                    "by_type": dict(plan.get("by_type", {})),
                    "dropped": {},
                    "extracted": {},
                    "unknown": list(plan.get("unknown", [])),
                    "unhandled": {},
                    "wired": set(),
                }
            )

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
            {
                "by_type": {"Base_Color": "/t/rock_BaseColor.png"},
                "unknown": ["/t/Agilent_PNA.png"],
            }
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
        _mu.MatUtils.resolve_pbr_plan = _stub({"by_type": {}, "unknown": ["/t/x.png"]})
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
    # ---- the USD route ships the FBX route's texture manifest -----------------
    # mayaUsd's registry exporter writes no normal off a bump2d chain (nor any
    # packed / AO map) -- probed: every UsdPreviewSurface in a production pull
    # arrived normal=None. The native USD materials are the baseline; the
    # manifest rebuild on top is what guarantees parity with FBX.
    check(
        "USD template: collects the texture manifest off the ORIGINAL shaders",
        "def collect_materials" in usd_txt
        and "materials, shading_groups = collect_materials(cmds)" in usd_txt
        and usd_txt.index("collect_materials(cmds)")
        < usd_txt.index("usd_safe_materials(cmds)" + "\n")
        and '"materials": materials or []' in usd_txt
        and '"shading_groups": shading_groups or {}' in usd_txt,
    )
    check(
        "USD template: normal maps wire straight into normalCamera (no bump2d chain)",
        "def _flatten_normal_chain" in usd_txt
        and "_usdsafe_bump" not in usd_txt
        and 'f"{file_node}.outColor", f"{ss}.normalCamera"' in usd_txt,
    )
    check(
        "USD template: Stingray data maps export raw",
        "STINGRAY_DATA_SLOTS" in usd_txt and '"Raw", type="string"' in usd_txt,
    )
    check(
        "USD template: Maya's primary UV set travels by NAME (preserveUVSetNames)",
        '"preserveUVSetNames": True' in usd_txt,
    )
    import inspect as _inspect_usd

    _engine_src = _inspect_usd.getsource(MayaSceneImport.import_payload)
    check(
        "engine: the USD route imports through UsdUtils.import_scene (every prim, hidden stay hidden, map1 active)",
        _engine_src.count("UsdUtils.import_scene(") == 1
        and "UsdUtils.import_usd(" not in _engine_src
        and "UsdUtils.import_scene("
        not in _inspect_usd.getsource(MayaSceneImport.import_scene),
    )
    check(
        "engine: the USD material replay is identity-only (no short-name fallback)",
        "object_fallback=False"
        in _inspect_usd.getsource(MayaSceneImport._apply_usd_materials),
    )
    check(
        "bake template: a USD source imports through the engine (no importer of its own)",
        "import_payload(" in bake_txt
        and "usd_import" not in bake_txt
        and "import_scene.fbx" not in bake_txt,
    )
    # The collectors are copies of the FBX template's -- identical by AST.
    import ast as _ast2

    def _fn_dump(text, name):
        for node in _ast2.walk(_ast2.parse(text)):
            if isinstance(node, _ast2.FunctionDef) and node.name == name:
                node.body = [n for n in node.body if not isinstance(n, _ast2.Expr)]
                return _ast2.dump(node)
        return None

    from blendertk.env_utils.maya_bridge._scene_import import (
        _IMPORT_TEMPLATE as _FBX_TEMPLATE,
    )

    fbx_txt = _FBX_TEMPLATE.read_text()
    for fn in ("_resolved_file", "_material_slots", "_material_files"):
        check(
            f"USD template: {fn} is the FBX template's copy (AST-identical)",
            _fn_dump(usd_txt, fn) is not None
            and _fn_dump(usd_txt, fn) == _fn_dump(fbx_txt, fn),
        )
    check(
        "engine: the USD branch renames SG materials and replays the manifest",
        "_apply_usd_materials(manifest_path, imported)"
        in _inspect.getsource(MayaSceneImport.import_payload),
    )

    _payload_src = _inspect.getsource(MayaSceneImport.import_payload)
    check(
        "bake template: USD sidecar replay is loud (no flattened .blend saved)",
        "_rollback_import(imported)" in _payload_src
        and "Instance rebuild failed; meshes stay independent" not in _payload_src
        and "_exit(1)" in bake_txt
        and bake_txt.index("_exit(1)") > bake_txt.index("traceback.print_exc()"),
    )
    check(
        "engine: materials-scope Empty stripped (prim-path keyed)",
        "_strip_materials_scope(imported, src)" in _payload_src,
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
            f"{_san('ref:nsCube')}/{_san('Chair.001')}/{_san('1digit')}",
        )

    # import leg: a USD conversion without its sidecar must fail BEFORE importing.
    _usd_stub = os.path.join(tempfile.gettempdir(), "btk_strict_nomanifest.usda")
    with open(_usd_stub, "w") as f:
        f.write("#usda 1.0\n")

    class _NoManifestStub(MayaSceneImport):
        def _cached_conversion(self, s, **kw):
            return SimpleNamespace(path=_usd_stub, scratch=None, hit=False)

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
            return SimpleNamespace(path=_bake_usd, scratch=None, hit=False)

        @staticmethod
        def _run_bake_script(
            app_exe, script_text, *, artifact, timeout, env=None, on_output=None
        ):
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
        # Capture the bake's cache-key inputs: the bake template calls back into
        # the ENGINE (tagging, manifest replays), so a baked .blend depends on the
        # engine module as much as on the template -- an engine fix must invalidate
        # cached bakes or a linked scene keeps showing the old bug.
        _key_files = []
        _orig_key = ptk.CachedArtifact.__dict__["key"]  # the staticmethod object

        def _capture_key(*parts, files=(), length=16):
            _key_files.append(list(files))
            return _orig_key.__func__(*parts, files=files, length=length)

        ptk.CachedArtifact.key = staticmethod(_capture_key)
        try:
            _baked2 = _BakeManifestStub().bake_scene(
                _bake_src, via="usd", use_cache=False
            )
        finally:
            ptk.CachedArtifact.key = _orig_key
        check(
            "bake_scene: USD intermediate WITH sidecar bakes",
            _bake_ran.get("ran") and _baked2.endswith(".blend"),
        )
        from blendertk.env_utils.maya_bridge import _scene_import as _si

        check(
            "bake cache key: intermediate + template + ENGINE module (engine fix invalidates)",
            any(
                _si._BAKE_TEMPLATE in fs and _si._BAKE_ENGINE in fs and _bake_usd in fs
                for fs in _key_files
            )
            and _si._BAKE_ENGINE.name == "_scene_import.py",
            str(_key_files),
        )
    finally:
        from blendertk.env_utils.maya_bridge._scene_import import BAKE_SOURCE_SUFFIX

        for p in [_bake_usd, _bake_usd + ".manifest.json", _bake_src] + (
            [_baked2, _baked2 + BAKE_SOURCE_SUFFIX] if _baked2 else []
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

        # The gate is the SPELLING, not the version (2026-09-17). A "names"
        # sidecar was written by a BLENDER producer, whose members are object
        # names; replaying it here -- where members are Maya DAG paths -- matches
        # nothing, and a silently flat scene only betrays itself when an artist
        # edits one "instance" and its siblings do not follow.
        with open(_mpath, "w") as f:
            _json.dump(
                {"version": 2, "format": "names", "instances": [["Chair_001"]]}, f
            )
        try:
            _eng2._apply_instance_manifest(_mpath, [_c1])
            check("strict replay: a foreign spelling is refused", False)
        except RuntimeError as e:
            check(
                "strict replay: a foreign spelling is refused",
                "names" in str(e),
                str(e),
            )
        finally:
            os.remove(_mpath)

        # No `format` at all: pre-2026-09-17 documents, and anything hand-rolled.
        with open(_mpath, "w") as f:
            _json.dump({"version": 2, "instances": [["Chair_001"]]}, f)
        try:
            _eng2._apply_instance_manifest(_mpath, [_c1])
            check("strict replay: a sidecar spelling nothing is refused", False)
        except RuntimeError:
            check("strict replay: a sidecar spelling nothing is refused", True)
        finally:
            os.remove(_mpath)

        # And the half of that decision which is easy to lose: `version` names
        # the SCHEMA now, so it must not quietly become a dialect gate again. A
        # group needs two members to mean anything, so this is a real replay
        # that has to SUCCEED -- a refusal here would be the old gate returning.
        with open(_mpath, "w") as f:
            _json.dump(
                {
                    "version": 1,
                    "format": "paths",
                    "instances": [["/Chair_001", "/Chair_002"]],
                },
                f,
            )
        try:
            _eng2._apply_instance_manifest(_mpath, [_c1, _c2])
            check("strict replay: the version alone never refuses", True)
        except RuntimeError as e:
            check("strict replay: the version alone never refuses", False, str(e))
        finally:
            os.remove(_mpath)

        # No route may reintroduce a per-carrier version number: until
        # 2026-09-17 the FBX route wrote 1 and the USD route 2 although the
        # schema was identical, so `version` named the CARRIER and a real schema
        # change had no number left to turn.
        import re as _re_ver

        _ver_bad = []
        _btk_bridge_dir = str(_IMPORT_TEMPLATE.parent.parent)
        for _pname in (
            os.path.join("templates", "_import_scene.py"),
            os.path.join("templates", "_import_scene_usd.py"),
            "_maya_bridge.py",
        ):
            _ppath = os.path.join(_btk_bridge_dir, _pname)
            with open(_ppath, encoding="utf-8") as _fh:
                _found = _re_ver.findall(r'"version":\s*(\d+)', _fh.read())
            if not _found:
                _ver_bad.append(f"{_pname}: writes no version")
            _ver_bad += [
                f"{_pname}: writes {_v}"
                for _v in _found
                if _v != str(ptk.HandoffManifest.VERSION)
            ]
        check(
            "manifest: every producer writes the one document version",
            not _ver_bad,
            "; ".join(_ver_bad),
        )

        # ---- materials-scope Empty strip (prim-path keyed, never name-keyed) --
        _scope_usda = os.path.join(tempfile.gettempdir(), "btk_strict_scope.usda")
        with open(_scope_usda, "w") as f:
            f.write(
                "#usda 1.0\n"
                'def Scope "mtl" {\n'
                '    def Material "M1" {}\n'
                "}\n"
                'def Mesh "Probe_Cube" {\n'
                "    int[] faceVertexCounts = [3]\n"
                "    int[] faceVertexIndices = [0, 1, 2]\n"
                "    point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n"
                "}\n"
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
                "#usda 1.0\n"
                'def Xform "mtl" {\n'
                '    def Mesh "sub" {\n'
                "        int[] faceVertexCounts = [3]\n"
                "        int[] faceVertexIndices = [0, 1, 2]\n"
                "        point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n"
                "    }\n"
                "}\n"
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

        # A materials Scope NESTED under an exported root (what a SELECTION export
        # -- the send direction -- writes) is stripped too; prim-path keyed.
        _nested_usda = os.path.join(
            tempfile.gettempdir(), "btk_strict_nested_scope.usda"
        )
        with open(_nested_usda, "w") as f:
            f.write(
                "#usda 1.0\n"
                'def Xform "asset_grp" {\n'
                '    def Mesh "wall" {\n'
                "        int[] faceVertexCounts = [3]\n"
                "        int[] faceVertexIndices = [0, 1, 2]\n"
                "        point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n"
                "    }\n"
                '    def Scope "mtl" {\n'
                '        def Material "wall_mat" {\n'
                "        }\n"
                "    }\n"
                "}\n"
            )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _imp3 = UsdUtils.import_usd(_nested_usda)
        _imp3_names = [o.name for o in _imp3]  # before the strip frees one
        _kept3 = _eng2._strip_materials_scope(_imp3, _nested_usda)
        check(
            "scope strip: a materials Scope nested under the exported root is removed",
            sorted(o.name for o in _kept3) == ["asset_grp", "wall"]
            and "mtl" not in bpy.data.objects,
            str(_imp3_names) + " -> " + str([o.name for o in _kept3]),
        )
        os.remove(_nested_usda)

        # ---- SG-named USD materials get their shader's name back ----------------
        _rn_manifest = os.path.join(
            tempfile.gettempdir(), "btk_usd_rename.manifest.json"
        )
        with open(_rn_manifest, "w", encoding="utf-8") as f:
            _json.dump(
                {
                    "shading_groups": {
                        "wall_matSG": "wall_mat",
                        "post_matSG": "taken_mat",
                    }
                },
                f,
            )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _taken = bpy.data.materials.new("taken_mat")  # a scene object WEARS this name
        _mk_obj("bystander", _mk_mesh("RN0")).data.materials.append(_taken)
        _orphan = bpy.data.materials.new("wall_mat")  # nobody wears this leftover
        _rn_obj = _mk_obj("wall", _mk_mesh("RN1"))
        _rn_obj.data.materials.append(bpy.data.materials.new("wall_matSG"))
        _rn_obj2 = _mk_obj("post", _mk_mesh("RN2"))
        _rn_obj2.data.materials.append(bpy.data.materials.new("post_matSG"))
        _renamed = _eng2._rename_usd_materials(_rn_manifest, [_rn_obj, _rn_obj2])
        check(
            "usd rename: wall_matSG -> wall_mat (unworn leftover purged); a WORN name is left alone",
            _renamed == 1
            and _rn_obj.data.materials[0].name == "wall_mat"
            and _rn_obj.data.materials[0] is not _orphan
            and _rn_obj2.data.materials[0].name == "post_matSG"
            and _taken.name == "taken_mat",
            f"{_renamed} {_rn_obj.data.materials[0].name} {_rn_obj2.data.materials[0].name}",
        )
        _eng2._apply_usd_materials(
            _rn_manifest, [_rn_obj, _rn_obj2]
        )  # no materials section: no-op, no raise
        check("usd materials: a manifest without entries is a quiet no-op", True)
        os.remove(_rn_manifest)

        # One Maya material feeding TWO shading groups arrives as two Blender
        # materials; they fold onto one (the FBX route's one-per-source rule).
        _mg_manifest = os.path.join(
            tempfile.gettempdir(), "btk_usd_merge.manifest.json"
        )
        with open(_mg_manifest, "w", encoding="utf-8") as f:
            _json.dump(
                {"shading_groups": {"e2e_ssSG": "e2e_ss", "e2e_ssSG2": "e2e_ss"}}, f
            )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _mg_a = _mk_obj("a", _mk_mesh("MG1"))
        _mg_a.data.materials.append(bpy.data.materials.new("e2e_ssSG"))
        _mg_b = _mk_obj("b", _mk_mesh("MG2"))
        _mg_b.data.materials.append(bpy.data.materials.new("e2e_ssSG2"))
        _mg_changed = _eng2._rename_usd_materials(_mg_manifest, [_mg_a, _mg_b])
        check(
            "usd rename: per-shading-group duplicates fold onto ONE named material",
            _mg_changed == 2
            and _mg_a.data.materials[0] is _mg_b.data.materials[0]
            and _mg_a.data.materials[0].name == "e2e_ss"
            and "e2e_ssSG2" not in bpy.data.materials,
            f"{_mg_changed} {_mg_a.data.materials[0].name} {_mg_b.data.materials[0].name}",
        )
        # ...whatever the slot order: a material already bearing the name wins
        # even when it is visited AFTER its SG-named twin.
        with open(_mg_manifest, "w", encoding="utf-8") as f:
            _json.dump(
                {"shading_groups": {"e2e_ssSG": "e2e_ss", "e2e_ss": "e2e_ss"}}, f
            )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _mg_a = _mk_obj("a", _mk_mesh("MG1"))
        _mg_a.data.materials.append(bpy.data.materials.new("e2e_ssSG"))
        _mg_b = _mk_obj("b", _mk_mesh("MG2"))
        _mg_b.data.materials.append(bpy.data.materials.new("e2e_ss"))
        _mg_changed = _eng2._rename_usd_materials(_mg_manifest, [_mg_a, _mg_b])
        check(
            "usd rename: the SG-named twin folds onto the already-named material regardless of order",
            _mg_changed == 1
            and _mg_a.data.materials[0] is _mg_b.data.materials[0]
            and _mg_b.data.materials[0].name == "e2e_ss"
            and "e2e_ssSG" not in bpy.data.materials,
            f"{_mg_changed} {_mg_a.data.materials[0].name} {_mg_b.data.materials[0].name}",
        )
        os.remove(_mg_manifest)

        # ---- atomic rollback: a failed replay removes everything it imported --
        _rb_usda = os.path.join(tempfile.gettempdir(), "btk_strict_rollback.usda")
        with open(_rb_usda, "w") as f:
            f.write(
                "#usda 1.0\n"
                'def Mesh "Chair_001" {\n'
                "    int[] faceVertexCounts = [3]\n"
                "    int[] faceVertexIndices = [0, 1, 2]\n"
                "    point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n"
                "}\n"
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
                return SimpleNamespace(path=_rb_usda, scratch=None, hit=False)

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
        check(
            "name reclaim: a freed source name is taken back",
            _clashed.name == "M_claim",
        )

        _holder = bpy.data.materials.new("M_held")
        _other = bpy.data.materials.new("M_held.001")
        _engine._claim_material_name(_other, "M_held")
        check(
            "name reclaim: a name still in use is never stolen",
            _other.name == "M_held.001" and _holder.name == "M_held",
            f"{_other.name} / {_holder.name}",
        )

        # ---- Maya groups arrive invisible (live user report) ------------------
        # A Maya group draws nothing in its viewport, but its FBX null lands as a
        # full-size PLAIN_AXES Empty. The node-type tagger (the one path every
        # Maya->Blender route funnels through: direct import, the reference bake,
        # mayatk's round-trip templates) shrinks a tagged group to the property's
        # hard minimum -- invisible, yet still selectable and transformable, and
        # with its display TYPE untouched (the send-back classification keys on
        # it). Locators keep the importer's display: Maya draws those.
        from blendertk.env_utils.maya_bridge._scene_import import (
            MAYA_GROUP_EMPTY_DISPLAY_SIZE,
        )

        bpy.ops.wm.read_factory_settings(use_empty=True)
        _grp = _mk_obj("asset_GRP")
        _grp_dup = _mk_obj("asset_GRP.001")  # Blender's collision rename of a twin
        _loc = _mk_obj("snap_LOC")
        _body = _mk_obj("body", _mk_mesh("M_body"), _grp)
        for _o in (_grp, _grp_dup, _loc):
            _o.empty_display_size = 1.0  # what the FBX importer hands us
        _tag_manifest = os.path.join(tempfile.gettempdir(), "btk_grp_tag.manifest.json")
        with open(_tag_manifest, "w") as f:
            _json.dump(
                {
                    "version": 1,
                    "transforms": {
                        "asset_GRP": "group",
                        "snap_LOC": "locator",
                        "body": "group",  # a MESH is never an Empty -- must be skipped
                    },
                },
                f,
            )
        try:
            _tagged = MayaSceneImport._tag_maya_node_types(
                _tag_manifest, [_grp, _grp_dup, _loc, _body]
            )
        finally:
            os.remove(_tag_manifest)
        check(
            "group tag: Maya groups shrink to the invisible display size",
            _tagged == 3
            and abs(_grp.empty_display_size - MAYA_GROUP_EMPTY_DISPLAY_SIZE) < 1e-9
            and abs(_grp_dup.empty_display_size - MAYA_GROUP_EMPTY_DISPLAY_SIZE) < 1e-9
            and _grp["maya_node_type"] == "group",
            f"tagged={_tagged} grp={_grp.empty_display_size} dup={_grp_dup.empty_display_size}",
        )
        check(
            "group tag: locators keep the importer's display size",
            _loc.empty_display_size == 1.0 and _loc["maya_node_type"] == "locator",
            f"loc={_loc.empty_display_size}",
        )
        check(
            "group tag: display TYPE untouched (send-back classification keys on it)",
            _grp.empty_display_type == "PLAIN_AXES",
            _grp.empty_display_type,
        )
        check(
            "group tag: a shrunk group is size-hidden, never visibility-hidden",
            not _grp.hide_viewport and not _grp.hide_get() and not _grp.hide_select,
        )
        check(
            "group tag: a mesh named like a group is never tagged",
            "maya_node_type" not in _body.keys(),
        )
        check(
            "group tag: the constant is Blender's hard minimum (sub-pixel, still drawn)",
            abs(
                _grp.bl_rna.properties["empty_display_size"].hard_min
                - MAYA_GROUP_EMPTY_DISPLAY_SIZE
            )
            < 1e-9,
            str(_grp.bl_rna.properties["empty_display_size"].hard_min),
        )

        # ---- scene settings: the manifest's ``scene`` section + file fallbacks ----
        # Measured before this existed: the USD route dropped the fps (30 -> 24),
        # the FBX route the range (1-250) -- a Maya scene never kept its clock.
        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.env_utils.usd import UsdUtils

        _tmp_scene = tempfile.mkdtemp(prefix="btk_scene_settings_")
        _rec = {
            "fps": 30.0,
            "frame_start": 10.0,
            "frame_end": 90.0,
            "anim_start": 5.0,
            "anim_end": 100.0,
            "frame_current": 42.0,
        }
        _man = os.path.join(_tmp_scene, "x.fbx.manifest.json")
        with open(_man, "w", encoding="utf-8") as fh:
            _json.dump({"version": 1, "materials": [], "scene": _rec}, fh)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _scn = bpy.context.scene
        _got = MayaSceneImport()._apply_scene_manifest(_man, None, frame_offset=1.0)
        check(
            "scene manifest: fps adopted",
            _scn.render.fps == 30 and _scn.render.fps_base == 1.0,
        )
        check(
            "scene manifest: ranges shifted by the FBX importer's anim_offset",
            (_scn.frame_start, _scn.frame_end) == (6, 101)
            and (_scn.frame_preview_start, _scn.frame_preview_end) == (11, 91)
            and _scn.use_preview_range
            and _scn.frame_current == 43,
            f"{_scn.frame_start}-{_scn.frame_end} / {_scn.frame_preview_start}-{_scn.frame_preview_end} @ {_scn.frame_current}",
        )
        check("scene manifest: the applied record is returned", _got.get("fps") == 30.0)
        check(
            "scene manifest: no manifest, no intermediate -> nothing applied, no raise",
            MayaSceneImport()._apply_scene_manifest(None, None) == {},
        )

        # File fallbacks: an FBX / USD written at 30 fps over 10-90 by Blender itself.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _scn = bpy.context.scene
        _scn.render.fps, _scn.render.fps_base = 30, 1.0
        _scn.frame_start, _scn.frame_end = 10, 90
        bpy.ops.mesh.primitive_cube_add()
        _anim_cube = bpy.context.active_object
        _anim_cube.name = "anim_cube"
        _anim_cube.location.x = 0.0
        _anim_cube.keyframe_insert("location", frame=10)
        _anim_cube.location.x = 2.0
        _anim_cube.keyframe_insert("location", frame=90)
        _fbx_out = os.path.join(_tmp_scene, "clock.fbx")
        FbxUtils.export(
            _fbx_out,
            objects=[_anim_cube],
            bake_anim=True,
            bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False,
        )
        _fbx_rec = FbxUtils.scene_settings(_fbx_out)
        check(
            "FbxUtils.scene_settings: fps + animation span from GlobalSettings",
            _fbx_rec.get("fps") == 30.0
            and (_fbx_rec.get("anim_start"), _fbx_rec.get("anim_end")) == (10, 90),
            f"{_fbx_rec}",
        )
        check(
            "FbxUtils.scene_settings: unreadable file -> {}",
            FbxUtils.scene_settings(_man) == {},
        )
        _usd_out = os.path.join(_tmp_scene, "clock.usdc")
        UsdUtils.export(_usd_out, objects=[_anim_cube], export_animation=True)
        _usd_rec = UsdUtils.scene_settings(_usd_out)
        check(
            "UsdUtils.scene_settings: fps + authored range from the stage",
            _usd_rec.get("fps") == 30.0
            and (_usd_rec.get("anim_start"), _usd_rec.get("anim_end")) == (10, 90),
            f"{_usd_rec}",
        )
        check(
            "UsdUtils.scene_settings: unreadable file -> {}",
            UsdUtils.scene_settings(_man) == {},
        )

        # Fallback path through the engine: no manifest -> the file's own record
        # (fps from the FBX, no shift on the USD route).
        bpy.ops.wm.read_factory_settings(use_empty=True)
        MayaSceneImport()._apply_scene_manifest(None, _usd_out)
        check(
            "scene fallback: USD stage record adopted 1:1",
            bpy.context.scene.render.fps == 30
            and (bpy.context.scene.frame_start, bpy.context.scene.frame_end)
            == (10, 90),
        )

        # USD animation ownership: Blender's importer streams animated xforms from
        # the file through a TRANSFORM_CACHE constraint -- a by-path dependency on
        # a temp intermediate the cache lifecycle deletes. The engine bakes it to keys.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _usd_objs = UsdUtils.import_usd(_usd_out)
        _cached = [
            o
            for o in _usd_objs
            if any(c.type == "TRANSFORM_CACHE" for c in o.constraints)
        ]
        check(
            "USD import streams animation through a Transform Cache (the premise)",
            len(_cached) == 1,
            f"{[(o.name, [c.type for c in o.constraints]) for o in _usd_objs]}",
        )
        # The bake must write INTO the action the object already has: with a fresh
        # one the engine ASSIGNS it and whatever was there is gone -- and on a USD
        # pull that is the visibility apply_visibility keys at import time.
        if _cached:
            _cached[0].hide_viewport = True
            _cached[0].keyframe_insert("hide_viewport", frame=10)
            _cached[0].hide_viewport = False
            _cached[0].keyframe_insert("hide_viewport", frame=50)
        _baked_n = MayaSceneImport()._own_usd_animation(_usd_out, _usd_objs)
        _ob = _cached[0] if _cached else None
        check(
            "own_usd_animation: the bake keeps keys the object already carried "
            "(visibility survives the transform-cache bake)",
            _ob is not None
            and "hide_viewport"
            in {
                fc.data_path
                for layer in getattr(_ob.animation_data.action, "layers", []) or []
                for strip in layer.strips
                for cbag in strip.channelbags
                for fc in cbag.fcurves
            },
        )
        _ad = _ob.animation_data if _ob else None
        check(
            "own_usd_animation: constraint + cache file gone, keys own the motion",
            _baked_n == 1
            and _ob is not None
            and not _ob.constraints
            and len(bpy.data.cache_files) == 0
            and _ad is not None
            and _ad.action is not None,
        )
        if _ob is not None:
            _scn = bpy.context.scene
            _scn.frame_set(10)
            _x10 = _ob.matrix_world.translation.x
            _scn.frame_set(90)
            _x90 = _ob.matrix_world.translation.x
            check(
                "own_usd_animation: baked keys reproduce the motion",
                abs(_x10) < 1e-4 and abs(_x90 - 2.0) < 1e-4,
                f"x@10={_x10} x@90={_x90}",
            )
        check(
            "own_usd_animation: nothing to bake -> 0, never raises",
            MayaSceneImport()._own_usd_animation(_usd_out, _usd_objs) == 0,
        )
        import shutil as _shutil

        _shutil.rmtree(_tmp_scene, ignore_errors=True)

    # ---- template contract: both Maya-side conversions record the scene clock ----
    for _tpl in (_IMPORT_TEMPLATE, _IMPORT_TEMPLATE_USD):
        _txt = _tpl.read_text(encoding="utf-8")
        check(
            f"{_tpl.name}: writes the manifest's scene section",
            "def scene_settings(cmds)" in _txt and '"scene": ' in _txt,
        )
        check(
            f"{_tpl.name}: fps read from the API (no unit-name table to drift)",
            "om.MTime(1.0, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())" in _txt,
        )
    _bake_txt = _BAKE_TEMPLATE.read_text(encoding="utf-8")
    _payload_src2 = _inspect.getsource(MayaSceneImport.import_payload)
    check(
        "bake template adopts the scene clock and owns USD animation",
        "scene_settings=True" in _bake_txt
        and "_apply_scene_manifest" in _payload_src2
        and "_own_usd_animation" in _payload_src2,
    )
    from blendertk.env_utils.usd import UsdUtils as _UsdUtilsSrc

    _btc_src = _inspect.getsource(_UsdUtilsSrc.bake_transform_caches)
    # Production pull (2026-09-15): the bake of 205 prims over 4742 frames took 70 s,
    # and the Bake Action engine's own clean -- a remove() per key from the front of
    # each curve, quadratic in its length -- ran past the 600 s budget.
    check(
        "bake_transform_caches: never the Bake Action clean (quadratic on per-frame bakes)",
        "do_clean=False" in _btc_src
        and "do_clean=True" not in _btc_src
        and "AnimUtils.optimize_keys(" in _btc_src,
    )

    # ---- apply_world (needs bpy): explicit HDRI > the scene's sky dome > ambient --
    if _HAVE_BPY:
        import json as _json_world
        import shutil as _shutil_world

        from blendertk.light_utils._light_utils import LightUtils as _LightUtils

        _world_dir = tempfile.mkdtemp(prefix="btk_world_")

        def _png(name):
            image = bpy.data.images.new(name, 8, 4)
            path = os.path.join(_world_dir, f"{name}.png")
            image.filepath_raw = path
            image.file_format = "PNG"
            image.save()
            return path

        def _world_manifest(world):
            path = os.path.join(_world_dir, "scene.fbx.manifest.json")
            data = {"lights": []}
            if world:
                data["world"] = world
            with open(path, "w", encoding="utf-8") as fh:
                _json_world.dump(data, fh)
            return path

        def _dome(**fields):
            return {"name": "skydome", "axis_up": "Y", **fields}

        _sky, _override = _png("sky"), _png("override")
        _out = MayaSceneImport.apply_world(
            _world_manifest(_dome(hdri=_sky, strength=4.0, center=[1.0, 0.0, 0.0])),
            hdri=None,
            strength=0.35,
        )
        _state = _LightUtils.get_world_hdri() or {}
        check(
            "a sky dome lights the world when no HDRI is set, at its own strength",
            os.path.normcase(_state.get("filepath", "")) == os.path.normcase(_sky)
            and abs(_state.get("strength", 0.0) - 4.0) < 1e-5,
            f"{_state}",
        )
        check(
            "a dome whose image centre faces +X needs no turn",
            abs(_state.get("rotation", 99.0)) < 1e-4,
            f"{_state.get('rotation')}",
        )
        check(
            "the summary names the dome and its image, and no explicit HDRI",
            _out.get("sky_dome") == "skydome (sky.png)" and _out.get("hdri") == "",
            f"{_out}",
        )
        # An unturned Maya dome: a latlong centre faces local +Z, which the FBX
        # import's Y-up -> Z-up turn puts at Blender -Y, while Blender's own equirect
        # centre faces +X and a positive Z rotation turns it clockwise seen from
        # above -- both measured (kick and Cycles against a u-ramp).
        MayaSceneImport.apply_world(
            _world_manifest(_dome(hdri=_sky, strength=1.0, center=[0.0, 0.0, 1.0]))
        )
        _rot = (_LightUtils.get_world_hdri() or {}).get("rotation")
        check(
            "an unturned Maya dome is a 90 degree world rotation",
            _rot is not None and abs(_rot - 90.0) < 1e-4,
            f"{_rot}",
        )
        _out = MayaSceneImport.apply_world(
            _world_manifest(_dome(hdri=_sky, strength=4.0, center=[1.0, 0.0, 0.0])),
            hdri=_override,
            strength=0.5,
        )
        _state = _LightUtils.get_world_hdri() or {}
        check(
            "an explicit HDRI wins over the scene's dome",
            os.path.normcase(_state.get("filepath", "")) == os.path.normcase(_override)
            and abs(_state.get("strength", 0.0) - 0.5) < 1e-5
            and _out.get("hdri") == "override.png"
            and _out.get("sky_dome") == "",
            f"{_state} {_out}",
        )
        _out = MayaSceneImport.apply_world(
            _world_manifest(_dome(name="flatdome", color=[0.2, 0.3, 0.4], strength=2.0))
        )
        _bg = next(
            (
                n
                for n in bpy.context.scene.world.node_tree.nodes
                if n.type == "BACKGROUND"
            ),
            None,
        )
        check(
            "an untextured dome lights the world as its colour",
            _bg is not None
            and all(
                abs(a - b) < 1e-5
                for a, b in zip(_bg.inputs["Color"].default_value[:3], (0.2, 0.3, 0.4))
            )
            and abs(_bg.inputs["Strength"].default_value - 2.0) < 1e-5
            and _out.get("sky_dome") == "flatdome",
            f"{_out}",
        )
        _out = MayaSceneImport.apply_world(_world_manifest(None), strength=0.35)
        check(
            "no dome and no HDRI keeps the flat ambient",
            "flat ambient" in _out.get("description", "")
            and _out.get("sky_dome") == ""
            and _out.get("hdri") == "",
            f"{_out}",
        )
        _shutil_world.rmtree(_world_dir, ignore_errors=True)

    # ---- this suite writes into an ISOLATED temp root ------------------------
    # Nothing here used to fail, which was the problem: this suite's own
    # orchestration check globbed `maya_to_btk_cache_*` in the real temp dir and
    # deleted the user's cached production USD conversion, a multi-minute
    # headless Maya run to earn back. `run_tests.py` now routes the whole run's
    # temp into one throwaway root and announces it; assert we INHERITED it
    # rather than assume the env crossed the process boundary.
    _sandbox_root = os.environ.get("BLENDERTK_TEST_TEMP_ROOT")
    if _sandbox_root:
        check(
            "temp isolation: this suite writes under the runner's throwaway root",
            os.path.realpath(tempfile.gettempdir()) == os.path.realpath(_sandbox_root),
            f"{tempfile.gettempdir()} != {_sandbox_root}",
        )

    # ---- a chain's TIP joint survives the FBX pull ---------------------------
    # Maya's FBX exporter appends NO synthetic `<parent>_end` bones, so the
    # "leaf bone" the importer would ignore is a real tip joint. Dropping it cost
    # 7 of 217 rig records on the production module (`LookupError` on each
    # chain's last joint, demoted to the bake) while the USD route resolved all
    # 217, and any skin weighted to a tip lost that influence (backlog
    # 2026-09-17, decided: keep tips).
    #
    # Blender's own exporter writes those synthetic leaves unless told not to, so
    # `add_leaf_bones=False` produces a MAYA-SHAPED file here -- the mechanism
    # reproduced faithfully without a Maya on the far side.
    import bpy as _bpy_tip

    _tip_fbx = os.path.join(tempfile.gettempdir(), "btk_tip_chain.fbx")
    try:
        for _o in list(_bpy_tip.data.objects):
            _bpy_tip.data.objects.remove(_o, do_unlink=True)
        _arm_data = _bpy_tip.data.armatures.new("TipRig")
        _arm = _bpy_tip.data.objects.new("TipRig", _arm_data)
        _bpy_tip.context.scene.collection.objects.link(_arm)
        _bpy_tip.context.view_layer.objects.active = _arm
        _bpy_tip.ops.object.mode_set(mode="EDIT")
        _prev = None
        for _i, _bname in enumerate(("jnt_0", "jnt_1", "jnt_2")):
            _eb = _arm_data.edit_bones.new(_bname)
            _eb.head = (0.0, 0.0, float(_i))
            _eb.tail = (0.0, 0.0, float(_i) + 1.0)
            if _prev is not None:
                _eb.parent = _prev
                _eb.use_connect = True
            _prev = _eb
        _bpy_tip.ops.object.mode_set(mode="OBJECT")
        _bpy_tip.ops.export_scene.fbx(
            filepath=_tip_fbx, add_leaf_bones=False, use_selection=False
        )
        for _o in list(_bpy_tip.data.objects):
            _bpy_tip.data.objects.remove(_o, do_unlink=True)
        _bpy_tip.ops.import_scene.fbx(filepath=_tip_fbx, **_FBX_OPTS)
        _bones = {
            b.name: b
            for a in _bpy_tip.data.objects
            if a.type == "ARMATURE"
            for b in a.data.bones
        }
        _pulled = sorted(_bones)
        check(
            "FBX pull: a chain's TIP joint arrives as a bone",
            _pulled == ["jnt_0", "jnt_1", "jnt_2"],
            f"{_pulled}",
        )
        check(
            "FBX pull: ignore_leaf_bones stays OFF (Maya writes no _end bones)",
            _FBX_OPTS.get("ignore_leaf_bones") is False,
            f"{_FBX_OPTS}",
        )
        # Keeping the tip changes the SHAPE of every chain -- the parent bone no
        # longer has to stretch to the tip -- so the decision that keeps it owes
        # this measurement, not just a presence check. Each head must still land
        # on its source joint, and no bone may come back degenerate (Blender
        # deletes a zero-length bone on leaving edit mode, which would take the
        # tip straight back out again by another route).
        _head_err = {
            n: max(abs(_bones[n].head_local[i] - e) for i, e in enumerate(_expect))
            for n, _expect in (
                ("jnt_0", (0.0, 0.0, 0.0)),
                ("jnt_1", (0.0, 0.0, 1.0)),
                ("jnt_2", (0.0, 0.0, 2.0)),
            )
            if n in _bones
        }
        check(
            "FBX pull: every bone head still lands on its source joint",
            len(_head_err) == 3 and max(_head_err.values()) < 1e-4,
            f"{ {k: round(v, 6) for k, v in _head_err.items()} }",
        )
        _lengths = {n: round(b.length, 6) for n, b in _bones.items()}
        check(
            "FBX pull: no bone comes back degenerate",
            _lengths and min(_lengths.values()) > 1e-4,
            f"{_lengths}",
        )
    finally:
        if os.path.exists(_tip_fbx):
            os.remove(_tip_fbx)


except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
