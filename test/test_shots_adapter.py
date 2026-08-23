# !/usr/bin/python
# coding=utf-8
"""Blender shots adapter test — the DCC layer over pythontk's shots engine.

bpy-only suite (no Qt): exercises ``BlenderShotStore`` + ``BlenderScenePersistence``
against a real headless Blender scene, proving the acquisition + persistence layer
correctly feeds the shared pythontk detection/model core:

- ``_scene_fps`` reads ``render.fps / render.fps_base``;
- ``has_animation`` (staticmethod) flips False→True on a keyed transform;
- ``detect_regions`` auto-mode clusters two disjoint keyed cubes into two shots,
  excludes a keyed-but-flat cube (per-fcurve motion filter), and resolves each
  shot's objects;
- ``collect_transform_segments`` / ``collect_selected_key_entries`` walk the 5.1
  slotted-action fcurve structure;
- ``detect_regions`` selected-keys mode builds boundaries from selected keys;
- ``BlenderScenePersistence`` round-trips the store through ``scene["shot_store"]``,
  and ``BlenderShotStore.active()`` auto-installs the backend + reloads it;
- ``assess`` flags a shot whose object is missing from the file;
- the mayatk-parity lifecycle hooks: ``SceneBeforeSave`` (``save_pre``) flushes a
  dirty store before the ``.blend`` is written, ``_on_time_unit_changed`` rescales
  shot timings to a new ``render.fps``, the ``bpy.msgbus`` framerate watch is
  (re)armed across file loads, and ``_schedule_flush`` is immediate headless.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_shots_adapter.py

Prints the ``===RESULT: PASS/FAIL===`` sentinel ``Run-Tests.ps1`` greps for.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_shots_adapter_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy

    from blendertk import BlenderShotStore, BlenderScenePersistence

    ATTR_NAME = BlenderScenePersistence.ATTR_NAME

    # ---- isolate class state + user-prefs side effects -------------------
    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_shots_prefs_")
    BlenderShotStore.clear_active()

    scene = bpy.context.scene
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]

    # clean scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    # ---- has_animation: empty scene --------------------------------------
    check(
        "has_animation False on empty scene", BlenderShotStore.has_animation() is False
    )

    def add_keyed_cube(name, frames, base_x, vary=True):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        for f in frames:
            x = base_x + (f * 0.1 if vary else 0.0)
            obj.location = (x, 0.0, 0.0)
            obj.keyframe_insert(data_path="location", frame=f)
        return obj

    cube_a = add_keyed_cube("CubeA", [1, 5, 10], 0.0)
    add_keyed_cube("CubeB", [50, 55, 60], 5.0)
    # keyed but constant location -> flat, must be excluded from motion detection
    add_keyed_cube("CubeFlat", [5, 8], 3.0, vary=False)

    # ---- has_animation: keyed scene --------------------------------------
    check(
        "has_animation True with keyed transforms",
        BlenderShotStore.has_animation() is True,
    )

    # ---- _scene_fps hook -------------------------------------------------
    scene.render.fps = 30
    scene.render.fps_base = 1.0
    fps_store = BlenderShotStore()
    check(
        "_scene_fps reads render.fps/fps_base",
        abs(fps_store._scene_fps() - 30.0) < 1e-6,
        f"got {fps_store._scene_fps()}",
    )
    scene.render.fps = 24  # restore

    # ---- fcurve walk (5.1 slotted action) --------------------------------
    fcs_a = list(BlenderShotStore.iter_action_fcurves(cube_a))
    check(
        "iter_action_fcurves yields CubeA fcurves", len(fcs_a) >= 1, f"n={len(fcs_a)}"
    )

    # ---- fcurve walk (legacy pre-4.4 action: no ``layers``, flat fcurves) ----
    # Blender 5.1 can't create a legacy action, so the fallback is proven against a
    # minimal stand-in — the function's contract is iteration, not the bpy types.
    class _LegacyAction:
        fcurves = ["fc_x", "fc_y"]

    class _LegacyAnimData:
        action = _LegacyAction()
        action_slot = None

    class _LegacyObj:
        animation_data = _LegacyAnimData()

    legacy_fcs = list(BlenderShotStore.iter_action_fcurves(_LegacyObj()))
    check(
        "iter_action_fcurves falls back to flat fcurves on a layer-less action",
        legacy_fcs == ["fc_x", "fc_y"],
        f"got {legacy_fcs}",
    )

    # ---- collect_transform_segments (flat cube excluded) -----------------
    segs = BlenderShotStore.collect_transform_segments(gap_threshold=5.0)
    seg_objs = sorted({s["obj"] for s in segs})
    check(
        "segments only from moving cubes",
        seg_objs == ["CubeA", "CubeB"],
        f"got {seg_objs}",
    )
    seg_a = next((s for s in segs if s["obj"] == "CubeA"), None)
    check(
        "CubeA segment spans its keys",
        seg_a and seg_a["start"] == 1.0 and seg_a["end"] == 10.0,
        f"{seg_a}",
    )

    # ---- detect_regions auto mode ----------------------------------------
    store = BlenderShotStore()
    store.detection_mode = "auto"
    store.detection_threshold = 5.0
    regions = store.detect_regions()
    check("auto detect finds 2 shots", len(regions) == 2, f"n={len(regions)}")
    if len(regions) == 2:
        r0, r1 = sorted(regions, key=lambda r: r["start"])
        check(
            "shot 1 bounds+objects",
            r0["start"] == 1.0 and r0["end"] == 10.0 and r0["objects"] == ["CubeA"],
            f"{r0}",
        )
        check(
            "shot 2 bounds+objects",
            r1["start"] == 50.0 and r1["end"] == 60.0 and r1["objects"] == ["CubeB"],
            f"{r1}",
        )
        all_objs = set(r0["objects"]) | set(r1["objects"])
        check("flat cube excluded from detection", "CubeFlat" not in all_objs)

    # ---- detect_regions selected-keys mode -------------------------------
    for obj in scene.objects:
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            for kp in fc.keyframe_points:
                kp.select_control_point = False
    for fc in BlenderShotStore.iter_action_fcurves(cube_a):
        for kp in fc.keyframe_points:
            kp.select_control_point = True
    entries = BlenderShotStore.collect_selected_key_entries()
    ent_objs = sorted({e[2] for e in entries})
    check(
        "selected-key entries only from CubeA", ent_objs == ["CubeA"], f"got {ent_objs}"
    )
    sk_store = BlenderShotStore()
    sk_store.detection_mode = "all"
    sk_store.detection_threshold = 5.0
    sk_regions = sk_store.detect_regions()
    check(
        "selected-keys mode yields a region at first key",
        bool(sk_regions) and sk_regions[0]["start"] == 1.0,
        f"{sk_regions}",
    )

    # ---- persistence round-trip + active() auto-install ------------------
    BlenderShotStore.clear_active()
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]
    active = BlenderShotStore.active()
    check("active() returns a BlenderShotStore", isinstance(active, BlenderShotStore))
    check(
        "active() auto-installs BlenderScenePersistence",
        isinstance(BlenderShotStore._persistence, BlenderScenePersistence),
    )
    active.define_shot("Intro", 1, 20, objects=["CubeA"])
    check(
        "scene prop written on define (immediate flush)",
        scene.get(ATTR_NAME) is not None,
    )

    BlenderShotStore.clear_active()
    reloaded = BlenderShotStore.active()
    check(
        "reloaded exactly 1 shot", len(reloaded.shots) == 1, f"n={len(reloaded.shots)}"
    )
    if reloaded.shots:
        sh = reloaded.shots[0]
        check(
            "reloaded shot fields intact",
            sh.name == "Intro"
            and sh.start == 1.0
            and sh.end == 20.0
            and sh.objects == ["CubeA"],
            f"{sh}",
        )

    # ---- assess: missing object flagged ----------------------------------
    BlenderShotStore.clear_active()
    a_store = BlenderShotStore()
    real = a_store.define_shot("Real", 1, 10, objects=["CubeA"])
    ghost = a_store.define_shot("Ghost", 20, 30, objects=["NoSuchObj"])
    verdict = a_store.assess()
    check(
        "assess valid for existing-object shot",
        verdict.get(real.shot_id) == "valid",
        f"{verdict}",
    )
    check(
        "assess missing_object for ghost shot",
        verdict.get(ghost.shot_id) == "missing_object",
        f"{verdict}",
    )

    # ---- publish_export_view: fbx_takes + shot_metadata on the carrier ----
    # Mirror of the Maya store's projection (shot_export_unity.md contract):
    # both channels from one to_export_view pass, cleared by an empty store.
    import json

    from blendertk.node_utils.data_nodes import DataNodes

    if DataNodes.get_export_node(create=False) is not None:
        import bpy as _bpy

        _bpy.data.objects.remove(
            DataNodes.get_export_node(create=False), do_unlink=True
        )

    pub_store = BlenderShotStore()
    pub_store.define_shot("shotA", 1, 10, objects=["CubeA"])
    pub_store.define_shot("shotB", 20, 30, objects=["CubeA"])
    carrier_name = pub_store.publish_export_view()
    check(
        "publish_export_view creates + returns the data_export carrier",
        carrier_name == DataNodes.EXPORT
        and DataNodes.get_export_node(create=False) is not None,
        f"{carrier_name}",
    )
    takes_raw = DataNodes.get_export_string(DataNodes.FBX_TAKES)
    meta_raw = DataNodes.get_export_string(DataNodes.SHOT_METADATA)
    takes = json.loads(takes_raw) if takes_raw else []
    meta = json.loads(meta_raw) if meta_raw else {}
    check(
        "fbx_takes channel carries one {name,start,end} per shot",
        [t.get("name") for t in takes] == ["shotA", "shotB"]
        and takes[0].get("start") == 1
        and takes[1].get("end") == 30,
        f"{takes}",
    )
    check(
        "shot_metadata clip names join 1:1 with the take names",
        [s.get("clip") for s in meta.get("shots", [])]
        == [t.get("name") for t in takes],
        f"{meta}",
    )
    for sid in [s.shot_id for s in list(pub_store.shots)]:
        pub_store.remove_shot(sid)
    pub_store.publish_export_view()
    check(
        "empty store clears both channels (carrier not recreated to hold them)",
        DataNodes.get_export_string(DataNodes.FBX_TAKES) is None
        and DataNodes.get_export_string(DataNodes.SHOT_METADATA) is None,
    )

    # ---- scene-swap invalidation lifecycle (C1) ---------------------------
    # BlenderScenePersistence must wire load_post (via ScriptJobManager) so a
    # File > New/Open (1) nulls the active store, (2) fires the class-level
    # invalidation listeners (open panels rebind + re-register their
    # non-persistent bpy.app handlers), and (3) the NEXT save can never write
    # the OLD file's shots JSON into the NEW scene. Pre-fix (no scene jobs)
    # every one of these failed — verified by monkeypatching the wiring off.
    from blendertk.core_utils.script_job_manager import ScriptJobManager

    BlenderShotStore.clear_active()
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]

    swap_store = BlenderShotStore.active()
    backend = BlenderShotStore._persistence
    check(
        "persistence backend installed its SceneOpened scene job",
        isinstance(backend, BlenderScenePersistence) and backend._scene_subs_installed,
    )
    status = ScriptJobManager.instance().status()
    check(
        "SJM installed the persistent load_post master handler",
        "load_post" in status["installed_handlers"],
        f"{status['installed_handlers']}",
    )
    swap_store.define_shot("OldSceneShot", 1, 20, objects=["CubeA"])

    fired = []

    def _record_invalidation(event):
        fired.append(event)

    BlenderShotStore.add_invalidation_listener(_record_invalidation)
    bpy.ops.wm.read_homefile(use_empty=True)  # File > New — fires load_post for real
    scene = bpy.context.scene  # the old scene datablock is gone

    check(
        "invalidation listener fired exactly once on file load",
        len(fired) == 1,
        f"n={len(fired)}",
    )
    check("active store nulled on file load", BlenderShotStore._active is None)
    fresh = BlenderShotStore.active()
    check(
        "post-load active() yields the NEW file's (empty) store",
        len(fresh.shots) == 0,
        f"n={len(fresh.shots)}",
    )
    fresh.define_shot("NewSceneShot", 1, 5, objects=[])
    raw = scene.get(ATTR_NAME) or ""
    check(
        "old file's shots never leak into the new scene's property",
        "OldSceneShot" not in raw and "NewSceneShot" in raw,
        f"{raw[:120]}",
    )
    BlenderShotStore.remove_invalidation_listener(_record_invalidation)

    # teardown path: clear_active() -> backend.remove_callbacks() drops the job
    backend2 = BlenderShotStore._persistence
    BlenderShotStore.clear_active()
    subs_after = ScriptJobManager.instance().status()["subscriptions"]
    check(
        "clear_active tears down the backend's scene job",
        not any(s["owner"] == repr(backend2) for s in subs_after),
        f"{subs_after}",
    )

    # ---- mayatk-parity lifecycle hooks -------------------------------------
    # (a) SceneBeforeSave -> save_pre: a dirty store is flushed before the file
    #     is written (mirror of MayaScenePersistence._on_before_save).
    import pythontk as ptk

    BlenderShotStore.clear_active()
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]
    hook_store = BlenderShotStore.active()
    hook_backend = BlenderShotStore._persistence
    status = ScriptJobManager.instance().status()
    check(
        "persistence backend subscribed SceneBeforeSave (save_pre master installed)",
        "save_pre" in status["installed_handlers"]
        and any(
            sub["event"] == "SceneBeforeSave" and sub["owner"] == repr(hook_backend)
            for sub in status["subscriptions"]
        ),
        f"{status}",
    )
    hook_store.define_shot("Flushed", 1, 10, objects=[])
    # Force the dirty state the deferred flush would leave behind in a GUI
    # session (headless flushes immediately — see (d)).
    hook_store.shots[0].name = "FlushedBySavePre"
    hook_store._dirty = True
    with ptk.TempArtifacts(prefix="btk_shots_save_") as tmp:
        save_path = tmp.path(".blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(save_path), copy=True)
    check(
        "save_pre flushed the dirty store into the scene property",
        not hook_store._dirty and "FlushedBySavePre" in (scene.get(ATTR_NAME) or ""),
        f"dirty={hook_store._dirty} raw={(scene.get(ATTR_NAME) or '')[:80]}",
    )

    # (b) _on_time_unit_changed: framerate change rescales shot timings
    #     (mirror of MayaScenePersistence._on_time_unit_changed).
    scene.render.fps = 24
    scene.render.fps_base = 1.0
    hook_store.scene_fps = 24.0
    hook_store.shots[0].start, hook_store.shots[0].end = 12.0, 24.0
    scene.render.fps = 48
    hook_backend._on_time_unit_changed()
    sh = hook_store.shots[0]
    check(
        "fps change rescales shot bounds (24 -> 48 doubles frames)",
        abs(hook_store.scene_fps - 48.0) < 1e-6 and sh.start == 24.0 and sh.end == 48.0,
        f"fps={hook_store.scene_fps} {sh.start}-{sh.end}",
    )
    hook_backend._on_time_unit_changed()  # same fps -> no-op
    check(
        "fps hook is a no-op when the framerate is unchanged",
        sh.start == 24.0 and sh.end == 48.0,
        f"{sh.start}-{sh.end}",
    )
    scene.render.fps = 24

    # (c) msgbus framerate watch: armed by the backend, re-armed after a file
    #     load (msgbus drops subscriptions on load), cleared on remove_callbacks.
    check("msgbus fps watch armed by the backend", hook_backend._fps_sub_installed)
    hook_backend._fps_sub_installed = False
    hook_backend._on_scene_changed()
    check(
        "SceneOpened handler re-arms the msgbus fps watch",
        hook_backend._fps_sub_installed,
    )
    BlenderShotStore.clear_active()
    check(
        "remove_callbacks clears the msgbus fps watch flag",
        not hook_backend._fps_sub_installed and not hook_backend._scene_subs_installed,
    )

    # (d) _schedule_flush: headless (bpy.app.background) flushes immediately —
    #     the timer loop never runs in --background.
    scene = bpy.context.scene
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]
    flush_store = BlenderShotStore.active()
    flush_store.define_shot("Immediate", 1, 5, objects=[])
    check(
        "_schedule_flush writes immediately in --background",
        bpy.app.background
        and not flush_store._dirty
        and not flush_store._flush_pending
        and "Immediate" in (scene.get(ATTR_NAME) or ""),
        f"dirty={flush_store._dirty} pending={flush_store._flush_pending}",
    )

    # cleanup class state so a later suite in the same process starts clean
    BlenderShotStore.clear_active()
    BlenderShotStore._prefs_dir_override = None
    if ATTR_NAME in scene.keys():
        del scene[ATTR_NAME]

    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_shots_adapter_checks()
    except Exception as e:  # pragma: no cover - harness failure prints its own trace
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
