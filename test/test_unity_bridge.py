"""blendertk UnityBridge feature test (headless Blender -- bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_unity_bridge.py

Mirror of mayatk's ``test_unity_bridge.py``. ``UnityBridge`` is the Blender half of the
Unity Scripts app (extapps' ``unity_scripts``, opened by tentacle with this class as its
engine): it resolves the app's Scope, exports it with the Scene Exporter and hands the
payload to the delivery the app attaches (``deliverer=``: the app's
``ptk.FolderDeliverer``, a copy into the project's ``Assets/``). Covered here: the host
half names no unitytk, its delivery is the caller's, the Scope it resolves for the app,
what it hands back, and a real end-to-end hand-off through a ``ptk.FolderDeliverer``
shaped as the app shapes it into a temp Unity project's ``Assets/`` (a folder, no Unity
install).

Two things stay module-level-Qt-bound and are therefore covered elsewhere, matching how
the Maya/Rizom bridge slots split their coverage:

* The panel is extapps' Unity Scripts app, which imports ``uitk`` -- never imported here.
  What it asks of blendertk is Qt-free and exercised directly below: the engine's
  ``scope_objects`` (and ``btk.BridgeHost``'s reads beneath it) and the hand-off.
* ``UnityBridge.params_defaults()`` (and therefore the public ``send()``, which calls it via
  ``merge_params``) imports ``parameters.py`` -> ``uitk.bridge.AttributeSpec`` -- also Qt-bound
  (see ``mayatk.env_utils.blender_bridge`` and every other hand-off bridge for the same
  split; ``params_defaults()`` output is asserted under the ``.venv`` instead). The
  end-to-end hand-off below drives the engine's own ``_preflight`` / ``_produce`` /
  ``_deliver`` steps directly with an explicit params dict -- the exact pipeline ``send()``
  runs, minus the Qt-only default-merging step -- so real bpy geometry still exercises the
  real FBX export + the app's real copy-into-Assets/ delivery.
"""

import sys
import os
import shutil
import tempfile
import traceback
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk
    from pythontk import HandoffRequest
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    import ast

    import pythontk as ptk

    def copy_req():
        return HandoffRequest(template="copy_to_assets", mode="send_to")

    class HandBack(ptk.Deliverer):
        """What the app receives from the host half: the payload, as produced."""

        def __init__(self, ok=True):
            self.ok, self.payloads, self.fbx = ok, [], []

        def preflight(self, bridge, request):
            return self.ok

        def deliver(self, bridge, payload, request):
            # Read here: the run's staging folder is gone once the send returns.
            self.payloads.append(payload)
            self.fbx.append(ptk.FbxFile.is_fbx(payload.primary))
            return {"fbx": payload.primary, "project": bridge.project_path}

    # ---- pure (no Blender geometry) -- composition, and what is the app's ----
    def imported_roots(path):
        roots = []
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.append(node.module.split(".")[0])
        return roots

    half = Path(REPO) / "blendertk" / "env_utils" / "unity_bridge"
    naming = sorted(p.name for p in half.glob("*.py") if "unitytk" in imported_roots(p))
    check("no module of the host half imports unitytk", not naming, f"{naming}")
    pyproject = (Path(REPO) / "pyproject.toml").read_text(encoding="utf-8")
    check("blendertk declares no unity extra", "unitytk" not in pyproject)
    check("the delivery is the caller's: none by default", UnityBridge().deliverer is None)
    attached = HandBack()
    check(
        "an attached delivery is the one it sends through",
        UnityBridge(project_path="C:/proj", deliverer=attached).deliverer is attached,
    )
    # The delivery, its modes and what it needs installed are the app's (a Unity
    # project installs its packages through Unity's Package Manager).
    gone = [
        name
        for name in (
            "list_delivery_modes",
            "list_template_modes",
            "_deliverer_cls",
            "install_package",
        )
        if hasattr(UnityBridge, name)
    ]
    check("the delivery is the app's, not the engine's", not gone, f"{gone}")
    check(
        "a send with no delivery refuses before its export",
        not UnityBridge()._preflight(["x"], copy_req()),
    )

    # ---- scope resolution: BridgeHost and the primitives it reads ------------
    # "all" -> the engine's whole-scene hook, else the scene's mesh objects;
    # "visible" -> the export mixin's visible read; "selected"/default -> btk.selected_objects().
    reset()
    bpy.ops.mesh.primitive_cube_add()
    visible_cube = bpy.context.active_object
    visible_cube.name = "VisibleCube"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    hidden_cube = bpy.context.active_object
    hidden_cube.name = "HiddenCube"
    hidden_cube.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    visible_cube.select_set(True)

    all_names = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    check(
        "scope 'all' gathers every scene mesh",
        {"VisibleCube", "HiddenCube"} <= all_names,
        f"{sorted(all_names)}",
    )

    selected_names = {o.name for o in btk.selected_objects()}
    check(
        "scope 'selected' uses the selection only",
        selected_names == {"VisibleCube"},
        f"{sorted(selected_names)}",
    )

    visible_names = {o.name for o in btk.get_visible_geometry()}
    check(
        "scope 'visible' excludes hidden objects",
        "VisibleCube" in visible_names and "HiddenCube" not in visible_names,
        f"{sorted(visible_names)}",
    )

    # The SHIPPED read, not a look-alike: the check above asserts what
    # `get_visible_geometry` returns, while the resolver actually calls
    # `BlenderExportMixin._visible_objects` (visible_get() over the current
    # scene, unfiltered by type so a visible group Empty travels). Two
    # different functions, and only one of them is on the export path.
    #
    # The mixin itself, the read `BridgeHost.resolve_scope("visible")` makes:
    # Qt-free, so headless Blender (no Qt binding) reaches the real function.
    from blendertk.env_utils.handoff_export import BlenderExportMixin

    resolved = {o.name for o in BlenderExportMixin._visible_objects()}
    check(
        "the export mixin's visible read excludes hidden objects",
        "VisibleCube" in resolved and "HiddenCube" not in resolved,
        f"{sorted(resolved)}",
    )

    # The API the shared panel calls (btk.BridgeHost), against the same scene.
    from blendertk.env_utils.handoff_export import BridgeHost

    by_scope = {
        scope: {o.name for o in BridgeHost.resolve_scope(scope)}
        for scope in ("selected", "visible", "bogus")
    }
    check(
        "BridgeHost.resolve_scope: the selection, the visible, and no widening",
        by_scope["selected"] == {"VisibleCube"}
        and "VisibleCube" in by_scope["visible"]
        and "HiddenCube" not in by_scope["visible"]
        and by_scope["bogus"] == {"VisibleCube"},
        f"{by_scope}",
    )
    # What the app asks its engine for the Current Scene source.
    engine_scope = {
        scope: {o.name for o in UnityBridge().scope_objects(scope)}
        for scope in ("selected", "visible", "bogus", "all")
    }
    check(
        "UnityBridge.scope_objects: the selection, the visible, all, no widening",
        engine_scope["selected"] == {"VisibleCube"}
        and engine_scope["bogus"] == {"VisibleCube"}
        and "HiddenCube" not in engine_scope["visible"]
        and {"VisibleCube", "HiddenCube"} <= engine_scope["all"],
        f"{engine_scope}",
    )
    bpy.ops.object.empty_add()
    group = bpy.context.active_object
    group.name = "UnityGroup"
    visible_cube.parent = group
    closed = {o.name for o in BridgeHost.scope_closure([group], {"SCOPE": "selected"})}
    check(
        "BridgeHost.scope_closure ships a scoped group's children",
        closed == {"UnityGroup", "VisibleCube"},
        f"{sorted(closed)}",
    )

    # ---- end-to-end: real FBX export -> copy into a temp Unity project -------
    # Drives _preflight/_produce/_deliver directly (the exact steps send() runs via
    # _run) with an explicit, already-merged params dict -- see the module docstring
    # for why the public send()/merge_params() path itself isn't callable headless.
    def run_handoff(bridge, objects, params):
        request = HandoffRequest(
            template="copy_to_assets", mode="send_to", params=params
        )
        resolved = bridge._resolve_objects(objects)
        if bridge.requires_objects and not resolved:
            return None
        if not bridge._preflight(resolved, request):
            return None
        payload = bridge._produce(resolved, request)
        if payload is None:
            return None
        return bridge._deliver(payload, request)

    tmp = Path(tempfile.mkdtemp(prefix="unity_bridge_test_"))
    try:
        project = tmp / "UnityProj"
        (project / "Assets").mkdir(parents=True)

        reset()
        bpy.ops.mesh.primitive_cube_add()
        hero = bpy.context.active_object
        hero.name = "UnityHero"
        bpy.ops.object.select_all(action="DESELECT")
        hero.select_set(True)

        # The host half's contract with the app, no unitytk: the FBX and its
        # asset name reach the delivery the app attached, checked first.
        back = HandBack()
        handed = run_handoff(
            UnityBridge(
                project_path=str(tmp),
                deliverer=back,
                decide_check_failure=lambda *a: "override_all",
            ),
            [hero],
            {},
        )
        check(
            "the app receives the FBX and the asset name",
            handed is not None
            and len(back.payloads) == 1
            and back.fbx == [True]
            and back.payloads[0].extras.get("default_asset_name") == "UnityHero",
            f"{handed} {[p.extras for p in back.payloads]}",
        )
        refused = HandBack(ok=False)
        check(
            "a refused delivery exports nothing",
            run_handoff(UnityBridge(deliverer=refused), [hero], {}) is None
            and not refused.payloads,
        )

        # The app's delivery as the app shapes pythontk's: a copy into the
        # project's Assets/ by the panel's keys, the FBX carrying the files its
        # records name (the Editor launch is the app's own).
        class AssetsDeliverer(ptk.FolderDeliverer):
            DEFAULT_SUBDIR = "Imported"
            SUBDIR_PARAM = "ASSETS_SUBDIR"
            NAME_PARAM = "ASSET_NAME"

            def __init__(self):
                super().__init__(embed=ptk.FbxMedia.embed_dependencies)

            def target(self, bridge):
                root = bridge.project_path
                assets = os.path.join(root, "Assets") if root else None
                return assets if assets and os.path.isdir(assets) else None

        # Headless, nobody answers a failed check: these sends carry on past
        # them, as a panel's Override All would.
        bridge = UnityBridge(
            project_path=str(project),
            deliverer=AssetsDeliverer(),
            decide_check_failure=lambda *args: "override_all",
        )

        result = run_handoff(
            bridge, [hero], {"ASSETS_SUBDIR": "Models", "ASSET_NAME": ""}
        )
        check("hand-off returns a result (delivery succeeded)", result is not None)
        if result is not None:
            dest = Path(result["file"])
            check(
                "asset named after the selected object, under Assets/Models",
                dest == project / "Assets" / "Models" / "UnityHero.fbx",
                f"{dest}",
            )
            check("copied FBX exists on disk", dest.is_file())
            check("copied FBX is non-empty", dest.is_file() and dest.stat().st_size > 0)

        # explicit asset name + default subdir
        bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
        cube2 = bpy.context.active_object
        cube2.name = "UnityCube"
        bpy.ops.object.select_all(action="DESELECT")
        cube2.select_set(True)
        result2 = run_handoff(bridge, [cube2], {"ASSET_NAME": "Custom/Name"})
        if result2 is not None:
            dest2 = Path(result2["file"])
            check(
                "default subdir 'Imported'; name sanitized",
                dest2 == project / "Assets" / "Imported" / "Custom_Name.fbx",
                f"{dest2}",
            )
            check("second copied FBX exists on disk", dest2.is_file())
        else:
            check("hand-off returns a result (explicit name)", False)

        # aborts cleanly with no project / empty selection
        bridge.project_path = str(tmp / "not_a_project")
        check(
            "hand-off aborts when the project path has no Assets/",
            run_handoff(bridge, [cube2], {}) is None,
        )

        bridge.project_path = str(project)
        check(
            "hand-off aborts with an empty selection",
            run_handoff(bridge, [], {}) is None,
        )

        # ---- the curve-proxy transport rides the bridge's FBX (2026-10-05) ----
        # Unity's importers rebind <object>__<channel> and emissive-weight curve
        # proxies; Blender stages them, and its exporter writes only what is
        # selected, so they reached the Scene Exporter's FBX and no bridge's:
        # fades and keyed weights imported static. Mirror of mayatk's bridge,
        # which names the render_effects stager.
        import pythontk as ptk
        from blendertk.mat_utils.emissive_groups import EmissiveGroups
        from blendertk.node_utils.data_nodes import DataNodes

        check(
            "the Unity bridge sends the Scene Exporter's deliverable (its stagers, records, takes)",
            issubclass(UnityBridge, ptk.SceneExportHandoff)
            and UnityBridge.SCENE_EXPORT_OVERRIDES == {},  # no Blender workspace row
            f"{UnityBridge.__mro__}",
        )
        reset()
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 10
        bpy.ops.mesh.primitive_cube_add()
        fader = bpy.context.active_object
        fader.name = "BridgeFader"
        for frame, alpha in ((1, 1.0), (10, 0.0)):
            fader["opacity"] = alpha
            fader.keyframe_insert('["opacity"]', frame=frame)
        from blendertk.mat_utils._mat_utils import MatUtils as _GlowMatUtils

        _glow = bpy.data.materials.new("BridgeMat")
        next(n for n in _GlowMatUtils.get_node_tree(_glow, create=True).nodes if n.type == "BSDF_PRINCIPLED").inputs[
            "Emission Strength"
        ].default_value = 1.0
        fader.data.materials.append(_glow)
        EmissiveGroups.add_group("glow", {"BridgeFader": [0]})
        EmissiveGroups.key_weight("glow", value=1.0, frame=1)
        EmissiveGroups.key_weight("glow", value=0.0, frame=10)
        bpy.ops.object.select_all(action="DESELECT")
        fader.select_set(True)
        faded = run_handoff(
            bridge,
            [fader],
            {"ASSET_NAME": "faded"},
        )
        marked = [
            o.name
            for o in bpy.data.objects
            if o.get(ptk.MeshConvert.CURVE_PROXY_MARKER)
        ]
        if faded is None:
            check("the animated hand-off returns a result", False)
        else:
            shipped = ptk.FbxFile.load(faded["file"], raw_payloads=False)
            models = shipped.object_names("Model")
            check(
                "the bridge's FBX carries the opacity proxy beside its object",
                "BridgeFader__opacity" in models,
                f"{sorted(models)}",
            )
            check(
                "the bridge's FBX carries the keyed emissive weight's proxy",
                "BridgeMat_glow__emission" in models and DataNodes.EXPORT in models,
                f"{sorted(models)}",
            )
            check(
                "the proxies arrive marked, so the importer finds and removes them",
                len(shipped.user_properties(ptk.MeshConvert.CURVE_PROXY_MARKER)) >= 2,
                f"{shipped.user_properties(ptk.MeshConvert.CURVE_PROXY_MARKER)}",
            )
        check("the send leaves no proxy in the scene", not marked, f"{marked}")

        # ---- the bridge bakes animation with the panel's own params (2026-10-05) ----
        # The Unity panel offers no animation knob, so INCLUDE_ANIMATION never
        # reached the hand-off and Blender's exporter, which writes no take unless
        # it bakes, shipped none: measured in Unity, the staged proxies arrived as
        # static nodes. Mirror of mayatk's bridge, whose takes never split either.
        check(
            "the Unity bridge's export rows are the Scope alone",
            set(UnityBridge().params_defaults()) == {"SCOPE"},
        )
        panel_params = {"ASSETS_SUBDIR": "Bridge", "ASSET_NAME": "panel_send"}
        sent = run_handoff(bridge, [fader], panel_params)
        if sent is None:
            check("the panel-params hand-off returns a result", False)
        else:
            sent_fbx = ptk.FbxFile.load(
                sent["file"], raw_payloads=False, span_arrays=("KeyTime",)
            )
            animated = {
                target
                for take in sent_fbx.take_curves().values()
                for target, *_ in take
            }
            check(
                "a send with the panel's params ships a take carrying the fade",
                sent_fbx.take_names() and "BridgeFader__opacity" in animated,
                f"takes={sent_fbx.take_names()} animated={sorted(animated)}",
            )
        # ---- a send of a scene with shots ships exactly their takes (2026-11-05) ----
        # Every writer of the per-shot takes ships them and no other: Maya's
        # File > Export through its opt-in hook shipped Take 001 beside them.
        # Blender's split REPLACES the scene-range stack; this pins it on the
        # hand-off path, read back from the file.
        from blendertk.anim_utils.shots._shots import ShotStore

        ShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_bridge_takes_")
        ShotStore.clear_active()
        try:
            store = ShotStore.active()
            store.define_shot("FadeHalfA", 1, 5, objects=["BridgeFader"])
            store.define_shot("FadeHalfB", 6, 10, objects=["BridgeFader"])
            shot_send = run_handoff(
                bridge, [fader], dict(panel_params, ASSET_NAME="shot_send")
            )
            shot_fbx = (
                ptk.FbxFile.load(shot_send["file"], raw_payloads=False)
                if shot_send is not None
                else None
            )
            shot_windows = (
                {
                    name: tuple(round(t * bpy.context.scene.render.fps) for t in span)
                    for name, span in shot_fbx.take_windows().items()
                }
                if shot_fbx is not None
                else None
            )
            check(
                "a send of a scene with shots ships one take per shot and no other",
                shot_fbx is not None
                and shot_fbx.take_names() == ["FadeHalfA", "FadeHalfB"]
                and shot_windows == {"FadeHalfA": (1, 5), "FadeHalfB": (6, 10)},
                f"takes={shot_fbx.take_names() if shot_fbx else None} "
                f"windows={shot_windows}",
            )
        finally:
            ShotStore.clear_active()
            ShotStore._prefs_dir_override = None

        # ---- the audio the scene plays NOW rides inside the bridge's FBX ----
        # A hand-off refreshed only derived records and the audio record was
        # authored, so the bridge shipped the last Scene Exporter run's events
        # (or none); and the deliverer embeds only the files a record names by
        # FILE name, while an audio event names its strip. Mirror of mayatk's.
        import wave

        from blendertk.audio_utils._audio_utils import AudioUtils

        def make_wav(name):
            path = str(tmp / "sound" / name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with wave.open(path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(22050)
                out.writeframes(name.encode() * 400)
            return path

        reset()
        AudioUtils.remove_all_clips()
        bpy.ops.mesh.primitive_cube_add()
        crate = bpy.context.active_object
        crate.name = "AudioCrate"
        step = make_wav("footstep_take3.wav")
        AudioUtils.add_clip(step, frame_start=1, name="footstep")
        run_handoff(bridge, [crate], {"ASSETS_SUBDIR": "First"})
        punch = make_wav("punch.wav")
        AudioUtils.add_clip(punch, frame_start=5, name="punch")
        heard = run_handoff(bridge, [crate], {"ASSETS_SUBDIR": "Second"})
        if heard is None:
            check("the audio hand-off returns a result", False)
        else:
            sounds = ptk.FbxMedia.audio_sounds(heard["file"])
            check(
                "the bridge's FBX names every strip the scene plays now",
                sorted(sounds) == ["footstep.wav", "punch.wav"],
                f"{sounds}",
            )
            embedded = {
                row["name"]: row["bytes"]
                for row in ptk.FbxMedia.embedded(heard["file"])
            }
            check(
                "each strip's file is inside the bridge's FBX, whole, under its "
                "event's name",
                embedded.get("footstep.wav") == os.path.getsize(step)
                and embedded.get("punch.wav") == os.path.getsize(punch),
                f"{embedded}",
            )
        AudioUtils.remove_all_clips()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
