# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest adapter test — CSV → shots + Blender-native behaviors.

bpy-only suite: parses a structured CSV via the shared pure engine and drives
``BlenderShotManifest.sync`` against a real headless scene, asserting that shots
are created from the steps AND that fade behaviors are realised Blender-natively
(opacity / ``hide_render`` keys via ``RenderOpacity``), plus that ``assess`` flags
a missing object.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_shot_manifest.py
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


def _run_manifest_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy

    from blendertk import BlenderShotStore
    from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
        BlenderShotManifest,
    )
    from pythontk.core_utils.engines.shots.manifest.manifest_model import ManifestModel

    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_mani_")
    BlenderShotStore.clear_active()

    # clean scene + create the assets the CSV references (empties are fine)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for name in ("aileron_geo", "wing_geo", "fuselage_geo"):
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = name

    csv_text = """SECTION A: RIGGING
Step,Step Contents,Asset Names,Voice Support
A01.),Aileron fades in,aileron_geo,
A02.),Wing static detail,wing_geo,
A03.),Fuselage fades out,fuselage_geo,
"""
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.write(fd, csv_text.encode())
    os.close(fd)
    steps = ManifestModel.parse_csv(path)
    os.remove(path)

    store = BlenderShotStore()
    mani = BlenderShotManifest(store)
    actions, beh_result, assessment = mani.sync(steps, initial_shot_length=100)

    # ---- shots created ---------------------------------------------------
    check("3 steps parsed", len(steps) == 3, f"{len(steps)}")
    check(
        "all steps created",
        actions == {"A01": "created", "A02": "created", "A03": "created"},
        f"{actions}",
    )
    shots = {s.name: s for s in store.sorted_shots()}
    check(
        "shots A01,A02,A03 exist", set(shots) == {"A01", "A02", "A03"}, f"{set(shots)}"
    )
    check(
        "A01 placed at [1,101]",
        (shots["A01"].start, shots["A01"].end) == (1, 101),
        f"{(shots['A01'].start, shots['A01'].end)}",
    )
    check(
        "A01 owns aileron_geo",
        shots["A01"].objects == ["aileron_geo"],
        f"{shots['A01'].objects}",
    )

    # ---- behaviors detected + applied Blender-natively -------------------
    check(
        "A01 metadata records fade_in behavior",
        any(
            e.get("behavior") == "fade_in"
            for e in shots["A01"].metadata.get("behaviors", [])
        ),
    )
    # mayatk-shaped records: {"object", "behavior", "shot"} dicts + a "failed" bucket
    applied_names = {r["object"] for r in beh_result.get("applied", [])}
    check(
        "aileron_geo fade applied",
        "aileron_geo" in applied_names,
        f"applied={beh_result.get('applied')}",
    )
    check(
        "fuselage_geo fade applied",
        "fuselage_geo" in applied_names,
        f"applied={beh_result.get('applied')}",
    )
    check(
        "apply_behaviors returns a failed bucket (mirror of mayatk's apply_to_shots)",
        beh_result.get("failed") == [],
        f"{beh_result.get('failed')}",
    )

    def fade_key_paths(obj_name):
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return set()
        return {
            fc.data_path
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            if "opacity" in fc.data_path or fc.data_path == "hide_render"
        }

    check(
        "aileron_geo has opacity/visibility fade keys",
        bool(fade_key_paths("aileron_geo")),
        f"{fade_key_paths('aileron_geo')}",
    )
    check("fuselage_geo has fade keys", bool(fade_key_paths("fuselage_geo")))
    check("wing_geo (no behavior) has NO fade keys", not fade_key_paths("wing_geo"))

    # fade_in keys ascend 0->1 over the fade window inside the shot
    ail = bpy.data.objects.get("aileron_geo")
    op_fc = next(
        (
            fc
            for fc in BlenderShotStore.iter_action_fcurves(ail)
            if "opacity" in fc.data_path
        ),
        None,
    )
    if op_fc is not None:
        vals = [
            round(kp.co[1], 3)
            for kp in sorted(op_fc.keyframe_points, key=lambda k: k.co[0])
        ]
        check(
            "aileron opacity fades 0->1 (fade_in)",
            vals and vals[0] == 0.0 and vals[-1] == 1.0,
            f"{vals}",
        )

    # ---- assess flags a missing object ----------------------------------
    bpy.data.objects.remove(bpy.data.objects["fuselage_geo"], do_unlink=True)
    assessment2 = mani.assess(steps)
    by_id = {a.step_id: a for a in assessment2}
    check(
        "assess A01 valid (object present)",
        by_id["A01"].status == "valid",
        f"{by_id['A01'].status}",
    )
    check(
        "assess A03 missing_object (fuselage removed)",
        by_id["A03"].status == "missing_object",
        f"{by_id['A03'].status}",
    )

    # ---- audio behavior: place a VSE sound strip + measure it -----------
    import wave
    import struct

    wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav_fd)
    with wave.open(wav_path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        # 1 second of silence -> ~24 frames at 24fps
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(48000)))

    from pythontk.core_utils.engines.shots.manifest.manifest_model import (
        BuilderStep as _BS,
        BuilderObject as _BO,
    )

    store2 = BlenderShotStore()
    mani2 = BlenderShotManifest(store2)
    audio_step = [
        _BS(
            "V01",
            "V",
            "t",
            "",
            [
                _BO(
                    "vo_clip",
                    behaviors=["set_clip"],
                    kind="audio",
                    source_path=wav_path,
                )
            ],
        )
    ]
    a2, b2, _ = mani2.sync(audio_step, initial_shot_length=50)
    from blendertk.audio_utils._audio_utils import AudioUtils

    placed = AudioUtils.get_clip("vo_clip")
    check("audio: get_clip is callable + strip placed", placed is not None, f"{placed}")
    check(
        "audio: apply_behaviors reported it applied",
        any(
            r["object"] == "vo_clip" and r["behavior"] == "set_clip"
            for r in b2.get("applied", [])
        ),
        f"{b2.get('applied')}",
    )
    # _measure_audio returns the placed strip's duration (no crash — the get_clip fix)
    meas = mani2._measure_audio(_BO("vo_clip", kind="audio"))
    check(
        "audio: _measure_audio returns a duration",
        meas is not None and meas > 0,
        f"{meas}",
    )

    # ---- audio idempotency: a rebuild must NOT stack duplicate strips ----
    # (pre-fix: every sync re-added the source -> "vo_clip.001" overlapping strip)
    a3, b3, _ = mani2.sync(audio_step, initial_shot_length=50)
    check(
        "audio: second Build does not stack a duplicate strip",
        AudioUtils.get_clip("vo_clip.001") is None,
    )
    check(
        "audio: second Build reports the strip skipped (already placed)",
        any(r["object"] == "vo_clip" for r in b3.get("skipped", [])),
        f"{b3}",
    )

    # ---- audio verify: a built audio step assesses valid -----------------
    # (pre-fix: _verify_behavior scanned bpy.data.objects for the STRIP name ->
    # every built audio behavior was permanently flagged missing_behavior)
    assessment3 = mani2.assess(audio_step)
    v01 = next(a for a in assessment3 if a.step_id == "V01")
    vo = next(o for o in v01.objects if o.name == "vo_clip")
    check(
        "audio: built audio step assesses valid (verify routes via audio_clip mode)",
        vo.status == "valid" and not vo.broken_behaviors,
        f"status={vo.status} broken={vo.broken_behaviors}",
    )
    os.remove(wav_path)

    # ---- apply_behaviors guards (mirror of mayatk's apply_to_shots) -------
    # existing-keys: a re-apply must skip objects already keyed in range,
    # never overwrite (also what makes repeated Builds idempotent).
    ail_keys_before = sorted(
        kp.co[0]
        for fc in BlenderShotStore.iter_action_fcurves(bpy.data.objects["aileron_geo"])
        for kp in fc.keyframe_points
    )
    re_result = mani.apply_behaviors()
    ail_keys_after = sorted(
        kp.co[0]
        for fc in BlenderShotStore.iter_action_fcurves(bpy.data.objects["aileron_geo"])
        for kp in fc.keyframe_points
    )
    check(
        "guards: re-apply skips an already-keyed object (existing-keys guard)",
        any(r["object"] == "aileron_geo" for r in re_result.get("skipped", []))
        and ail_keys_before == ail_keys_after,
        f"skipped={re_result.get('skipped')}",
    )

    # locked shot: behaviors on a locked shot are never touched ("Locked shots
    # are never modified" — the panel help's promise).
    shots["A02"].locked = True
    shots["A02"].metadata["behaviors"] = [
        {"name": "wing_geo", "behavior": "fade_in", "kind": "scene"}
    ]
    locked_result = mani.apply_behaviors()
    check(
        "guards: locked shot's behaviors untouched",
        not fade_key_paths("wing_geo")
        and not any(
            r["object"] == "wing_geo" for r in locked_result.get("applied", [])
        ),
        f"{locked_result}",
    )
    shots["A02"].locked = False
    shots["A02"].metadata["behaviors"] = []  # don't bleed into the next guard check

    # zero-duration shot: nothing to key over -> skipped entirely.
    z = store.define_shot("Z0", 500, 500, objects=["wing_geo"])
    z.metadata["behaviors"] = [
        {"name": "wing_geo", "behavior": "fade_in", "kind": "scene"}
    ]
    zero_result = mani.apply_behaviors()
    check(
        "guards: zero-duration shot's behaviors untouched",
        not fade_key_paths("wing_geo")
        and not any(r["shot"] == "Z0" for r in zero_result.get("applied", [])),
        f"{zero_result}",
    )

    # ---- _show_in_outliner guards objects outside the active view layer ----
    # (pre-fix: select_set raised RuntimeError on an object in an excluded
    # collection, killing the context-menu reveal action)
    from types import SimpleNamespace

    from blendertk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
        ShotManifestController,
    )

    vlg_col = bpy.data.collections.new("VlgExcluded")
    bpy.context.scene.collection.children.link(vlg_col)
    vlg_out = bpy.data.objects.new("VlgOutside", None)
    vlg_col.objects.link(vlg_out)
    bpy.context.view_layer.layer_collection.children["VlgExcluded"].exclude = True
    bpy.context.view_layer.update()

    footer_msgs = []
    stub = SimpleNamespace(_set_footer=lambda text, **kw: footer_msgs.append(text))
    reveal_err = ""
    try:
        ShotManifestController._show_in_outliner(stub, "VlgOutside")
        reveal_ok = True
    except RuntimeError as e:
        reveal_ok, reveal_err = False, repr(e)
    check(
        "_show_in_outliner: outside-view-layer object doesn't raise",
        reveal_ok,
        reveal_err,
    )
    check(
        "_show_in_outliner: outside-view-layer object reported via footer",
        any("view layer" in m for m in footer_msgs),
        f"{footer_msgs}",
    )
    ShotManifestController._show_in_outliner(stub, "aileron_geo")
    check(
        "_show_in_outliner: in-layer object selected + active",
        bpy.data.objects["aileron_geo"].select_get()
        and bpy.context.view_layer.objects.active is bpy.data.objects["aileron_geo"],
    )

    # ---- scene walks: flat keys are boundary markers, not animation ------
    # (mirror of mayatk: only transform channels whose values VARY in range
    # count; a held/flat channel or a custom-prop-only animation is excluded)
    def _key(obj, frame, loc):
        obj.location = loc
        obj.keyframe_insert(data_path="location", frame=frame)

    moving = bpy.data.objects.new("walk_moving", None)
    flat = bpy.data.objects.new("walk_flat", None)
    custom = bpy.data.objects.new("walk_custom", None)
    for o in (moving, flat, custom):
        bpy.context.scene.collection.objects.link(o)
    _key(moving, 1000, (0, 0, 0))
    _key(moving, 1010, (5, 0, 0))
    _key(flat, 1000, (1, 1, 1))
    _key(flat, 1010, (1, 1, 1))
    custom["marker"] = 0.0
    custom.keyframe_insert(data_path='["marker"]', frame=1000)
    custom["marker"] = 1.0
    custom.keyframe_insert(data_path='["marker"]', frame=1010)

    walk = BlenderShotManifest(BlenderShotStore())
    found = walk._discover_scene_objects(1000, 1010, set())
    check(
        "discover: moving object found; flat + custom-prop-only excluded",
        "walk_moving" in found
        and "walk_flat" not in found
        and "walk_custom" not in found,
        f"{found}",
    )
    check(
        "discover: exclude_names honoured",
        "walk_moving" not in walk._discover_scene_objects(1000, 1010, {"walk_moving"}),
    )
    check(
        "discover: out-of-range window finds nothing",
        not walk._discover_scene_objects(2000, 2010, set()),
    )
    filt = walk._filter_to_animated(["walk_moving", "walk_flat", "ghost"], 1000, 1010)
    check(
        "filter_to_animated: keeps only varying, existing objects",
        filt == ["walk_moving"],
        f"{filt}",
    )
    check(
        "discover: curve map + curve data cached per cycle",
        walk._animated_transforms is not None and bool(walk._curve_data),
    )

    # Two objects driven by ONE action through different slots. The per-cycle
    # sample cache must key on the FCurve, not on (action, data_path, index):
    # crv.id_data is the ACTION, so a tuple key collides here and the second
    # curve silently reads the first's samples.
    shared_a = bpy.data.objects.new("slot_moving", None)
    shared_b = bpy.data.objects.new("slot_flat", None)
    for o in (shared_a, shared_b):
        bpy.context.scene.collection.objects.link(o)
    _key(shared_a, 1000, (0, 0, 0))
    _key(shared_a, 1010, (7, 0, 0))
    shared_action = shared_a.animation_data.action
    shared_b.animation_data_create()
    shared_b.animation_data.action = shared_action
    if getattr(shared_b.animation_data, "action_slot", None) in (
        None,
        shared_a.animation_data.action_slot,
    ):
        shared_b.animation_data.action_slot = shared_action.slots.new(
            id_type="OBJECT", name="slot_flat"
        )
    _key(shared_b, 1000, (2, 2, 2))
    _key(shared_b, 1010, (2, 2, 2))

    shared_walk = BlenderShotManifest(BlenderShotStore())
    shared_filt = shared_walk._filter_to_animated(
        ["slot_moving", "slot_flat"], 1000, 1010
    )
    check(
        "filter_to_animated: one action across two slots does not cross-contaminate",
        shared_filt == ["slot_moving"],
        f"{shared_filt} (the flat object read the moving one's samples)",
    )
    check(
        "_default_keyframe_range: full extent of an object's keys",
        BlenderShotManifest._default_keyframe_range("walk_moving") == (1000.0, 1010.0)
        and BlenderShotManifest._default_keyframe_range("ghost") is None,
    )

    # ---- fps cache + source-path audio measurement --------------------
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1.0
    fresh = BlenderShotManifest(BlenderShotStore())
    fps1 = fresh._resolve_fps()
    bpy.context.scene.render.fps = 60
    check(
        "_resolve_fps: scene fps, cached per instance until update() clears it",
        fps1 == 30.0 and fresh._resolve_fps() == 30.0 and fresh._fps_cache == 30.0,
        f"{fps1} / {fresh._resolve_fps()}",
    )
    bpy.context.scene.render.fps = 24

    wav2_fd, wav2_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav2_fd)
    with wave.open(wav2_path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(96000)))  # 2 s
    probe = BlenderShotManifest(BlenderShotStore())
    meas_src = probe._measure_audio(
        _BO("unplaced", kind="audio", source_path=wav2_path)
    )
    check(
        "_measure_audio: unplaced source file measured headlessly (2 s @ 24 = 48)",
        meas_src == 48.0,
        f"{meas_src}",
    )
    check(
        "_measure_audio: no source + no strip -> None",
        probe._measure_audio(_BO("nothing_here", kind="audio")) is None,
    )
    grow = probe._audio_grow_duration(
        [_BO("unplaced", kind="audio", behaviors=["set_clip"], source_path=wav2_path)]
    )
    check(
        "_audio_grow_duration: routes through Blender compute_duration",
        grow == 48.0,
        f"{grow}",
    )
    os.remove(wav2_path)

    check(
        "rewire_audio: no-op returning the mayatk result shape",
        BlenderShotManifest.rewire_audio()
        == {"created": [], "updated": [], "deleted": []},
    )

    # ---- reapply_object: explicit user action re-keys WITHOUT the guard ----
    both = _BO("wing_geo", behaviors=["fade_in", "fade_out"])
    re_shot = store.define_shot("R1", 700, 800, objects=["wing_geo"])
    check("reapply_object: applies", mani.reapply_object(re_shot, both))
    wing = bpy.data.objects["wing_geo"]
    op_times = sorted(
        round(kp.co[0])
        for fc in BlenderShotStore.iter_action_fcurves(wing)
        if "opacity" in fc.data_path
        for kp in fc.keyframe_points
    )
    check(
        "reapply_object: fade_in at start + fade_out at end (distributed anchors)",
        op_times == [700, 715, 785, 800],
        f"{op_times}",
    )
    check(
        "reapply_object: re-keying an already-keyed object is allowed (no guard)",
        mani.reapply_object(re_shot, both),
    )
    check(
        "reapply_object: no behaviors -> nothing applied",
        not mani.reapply_object(re_shot, _BO("wing_geo")),
    )

    BlenderShotStore.clear_active()
    BlenderShotStore._prefs_dir_override = None
    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_manifest_checks()
    except Exception as e:  # pragma: no cover
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
