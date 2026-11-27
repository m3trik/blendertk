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
    --factory-startup --python blendertk/tests/test_shot_manifest.py
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

    from blendertk import ShotStore
    from blendertk.anim_utils._anim_utils import AnimUtils
    from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
        BlenderShotManifest,
    )
    from pythontk.core_utils.engines.shots.manifest.manifest_model import ManifestModel

    ShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_mani_")
    ShotStore.clear_active()

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

    store = ShotStore()
    mani = BlenderShotManifest(store, disabled_behaviors=())  # every behavior keyed
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
            for fc in AnimUtils.get_fcurves(obj)
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
            for fc in AnimUtils.get_fcurves(ail)
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

    store2 = ShotStore()
    mani2 = BlenderShotManifest(store2, disabled_behaviors=())
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
    # Ownership: the keys a Build wrote are the manifest's (ShotEditLedger),
    # so re-applying replaces exactly those -- repeated Builds are idempotent
    # -- while a key the animator set on a fade's channel is never overwritten.
    def _ail_keys():
        return sorted(
            (fc.data_path, kp.co[0])
            for fc in AnimUtils.get_fcurves(
                bpy.data.objects["aileron_geo"]
            )
            for kp in fc.keyframe_points
        )

    ail_keys_before = _ail_keys()
    re_result = mani.apply_behaviors()
    check(
        "guards: re-apply replaces only its own keys (idempotent)",
        _ail_keys() == ail_keys_before
        and any(r["object"] == "aileron_geo" for r in re_result.get("applied", [])),
        f"applied={re_result.get('applied')} skipped={re_result.get('skipped')}",
    )
    aileron = bpy.data.objects["aileron_geo"]
    mid = (shots["A01"].start + shots["A01"].end) / 2.0
    aileron.keyframe_insert(data_path='["opacity"]', frame=mid)  # the animator's
    animator_keys = _ail_keys()
    re_result = mani.apply_behaviors()
    check(
        "guards: animator keys on a fade's channel are never overwritten",
        any(r["object"] == "aileron_geo" for r in re_result.get("skipped", []))
        and _ail_keys() == animator_keys,
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

    # ---- Show in Outliner: ManifestHost.find_nodes / reveal (the panel's host calls) ----
    # (pre-fix: select_set raised RuntimeError on an object in an excluded
    # collection, killing the context-menu reveal action)
    from blendertk import ShotStore, ManifestHost

    vlg_col = bpy.data.collections.new("VlgExcluded")
    bpy.context.scene.collection.children.link(vlg_col)
    vlg_out = bpy.data.objects.new("VlgOutside", None)
    vlg_col.objects.link(vlg_out)
    bpy.context.view_layer.layer_collection.children["VlgExcluded"].exclude = True
    bpy.context.view_layer.update()

    store = ShotStore.active()
    reveal_err, notes = "", []
    try:
        notes = ManifestHost.reveal(ManifestHost.find_nodes("VlgOutside", store))
        reveal_ok = True
    except RuntimeError as e:
        reveal_ok, reveal_err = False, repr(e)
    check(
        "reveal: outside-view-layer object doesn't raise",
        reveal_ok,
        reveal_err,
    )
    check(
        "reveal: outside-view-layer object reported in its notes",
        any("view layer" in m for m in notes),
        f"{notes}",
    )
    ManifestHost.reveal(ManifestHost.find_nodes("aileron_geo", store))
    check(
        "reveal: in-layer object selected + active",
        bpy.data.objects["aileron_geo"].select_get()
        and bpy.context.view_layer.objects.active is bpy.data.objects["aileron_geo"],
    )
    # A name two objects answer to finds both (the duplicates to fix).
    for dup in ("AC:twin_geo", "BC:twin_geo"):
        bpy.context.scene.collection.objects.link(bpy.data.objects.new(dup, None))
    bpy.context.view_layer.update()  # a new link joins the layer's objects here
    twins = ManifestHost.find_nodes("twin_geo", store)
    check(
        "find_nodes: a shared name answers with every object it names",
        sorted(twins) == ["AC:twin_geo", "BC:twin_geo"],
        f"{twins}",
    )
    ManifestHost.reveal(twins + ManifestHost.find_nodes("wing_geo", store))
    check(
        "reveal: every found object selected",
        sorted(o.name for o in bpy.data.objects if o.select_get())
        == ["AC:twin_geo", "BC:twin_geo", "wing_geo"],
        f"{[o.name for o in bpy.data.objects if o.select_get()]}",
    )
    for dup in ("AC:twin_geo", "BC:twin_geo"):
        bpy.data.objects.remove(bpy.data.objects[dup], do_unlink=True)

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

    walk = BlenderShotManifest(ShotStore())
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
    # What a shot holds was left out of its discovery by leaf (ShotStore.member_key): naming PANEL|LATCH left out
    # nothing. By what a name names (mayatk's test_discovery_leaves_out_only_the_members_a_name_names): a path's tail
    # the member it ends (the door's latch arrives as LATCH.001), a bare name the members of its key. 2026-11-26
    latches = []
    for parent_name in ("DISC_PANEL", "DISC_DOOR"):
        holder = bpy.data.objects.new(parent_name, None)
        latch = bpy.data.objects.new("DISC_LATCH", None)
        latch.parent = holder
        for o in (holder, latch):
            bpy.context.scene.collection.objects.link(o)
        _key(latch, 9000, (0, 0, 0))
        _key(latch, 9010, (5, 0, 0))
        latches.append(latch.name)
    tails = BlenderShotManifest(ShotStore())._discover_scene_objects(9000, 9010, {"DISC_PANEL|DISC_LATCH"})
    bare = BlenderShotManifest(ShotStore())._discover_scene_objects(9000, 9010, {"DISC_LATCH"})
    check(
        "discover: a path's tail leaves out the member it ends, a bare name the members of its key",
        tails == [latches[1]] and bare == [latches[1]] and latches[1] != "DISC_LATCH",
        f"{tails} {bare} {latches}",
    )
    # A step listing a path's tail owns its bare-named member (Blender keeps a member by its object's name), and the
    # shot's members reach discovery as names, never leaves: the door's latch is still said. 2026-11-26
    owned_store = ShotStore()
    owned_store.define_shot("A01", 9000, 9010, objects=["DISC_LATCH"], metadata={"step": "A01"})
    (owned,) = BlenderShotManifest(owned_store, disabled_behaviors=()).assess(
        [_BS("A01", "A", "t", "", [_BO("DISC_PANEL|DISC_LATCH")])]
    )
    check(
        "assess: a path's tail owns its bare-named member; the other latch is still unlisted",
        owned.objects[0].status != "not_in_shot" and owned.additional_objects == [latches[1]],
        f"{owned.objects[0].status} {owned.additional_objects}",
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

    shared_walk = BlenderShotManifest(ShotStore())
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
    fresh = BlenderShotManifest(ShotStore())
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
    probe = BlenderShotManifest(ShotStore())
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
        for fc in AnimUtils.get_fcurves(wing)
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
    # The doc dropped both behaviors: the Apply gives back the keys they held
    # (they are the manifest's own output), and then has nothing to do.
    check(
        "reapply_object: an object whose doc lists no behaviors gives its keys back",
        mani.reapply_object(re_shot, _BO("wing_geo"))
        and not any(
            fc.keyframe_points
            for fc in AnimUtils.get_fcurves(wing)
            if "opacity" in fc.data_path
        )
        and not store.edit_ledger.authored(owner=re_shot.shot_id, obj="wing_geo"),
    )
    check(
        "reapply_object: no behaviors and no keys -> nothing applied",
        not mani.reapply_object(re_shot, _BO("wing_geo")),
    )

    # ---- the effect recipe through a build (mirror of mayatk's round trip) --
    # Build keys the scene's recipe; a recipe change reads as stale until a
    # Build re-keys it; a behavior the doc drops takes its keys -- never the
    # animator's.
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "door_geo"
    door = bpy.data.objects["door_geo"]
    rt_store = ShotStore()
    rt = BlenderShotManifest(rt_store, disabled_behaviors=())
    rt_range = {"D01": (1000.0, 1240.0)}
    hl_path = '["highlight"]'

    def rt_steps(*behaviors):
        return [_BS("D01", "D", "t", "", [_BO("door_geo", behaviors=list(behaviors))])]

    def door_keys(path):
        return sorted(
            (kp.co[0], kp.co[1])
            for fc in AnimUtils.get_fcurves(door)
            if fc.data_path == path
            for kp in fc.keyframe_points
        )

    def pulse_plan():
        shot = next(s for s in rt_store.sorted_shots() if s.name == "D01")
        return rt_store.effect_recipe.plan("pulse", shot.start, shot.end, 24)

    rt_store.update_effect_recipe(pulse_period=2.0, pulse_duty=0.5)
    _, _, built = rt.sync(rt_steps("highlight"), ranges=rt_range)
    check(
        "recipe: Build keys the scene recipe's pulse",
        door_keys(hl_path) == pulse_plan(),
        f"{door_keys(hl_path)[:4]} vs {pulse_plan()[:4]}",
    )
    check(
        "recipe: a fresh build assesses valid and needs no build",
        built[0].objects[0].status == "valid" and not built[0].needs_build,
        f"{built[0].objects[0].status}",
    )
    rt_store.update_effect_recipe(pulse_period=2.5)
    stale = rt.assess(rt_steps("highlight"))
    check(
        "recipe: a recipe change reads as stale, and Build is worth pressing",
        stale[0].objects[0].status == "stale_behavior"
        and stale[0].objects[0].stale_behaviors == ["highlight"]
        and stale[0].needs_build,
        f"{stale[0].objects[0].status} {stale[0].objects[0].stale_behaviors}",
    )
    _, _, rebuilt = rt.sync(rt_steps("highlight"), ranges=rt_range)
    check(
        "recipe: Build re-keys a stale behavior",
        door_keys(hl_path) == pulse_plan() and rebuilt[0].objects[0].status == "valid",
        f"{rebuilt[0].objects[0].status}",
    )
    rt.sync(rt_steps("highlight", "fade_in"), ranges=rt_range)
    door["highlight"] = 0.5
    door.keyframe_insert(data_path=hl_path, frame=2000)  # the animator's
    dropped = rt.assess(rt_steps("fade_in"))
    check(
        "recipe: assess names a behavior the doc dropped",
        dropped[0].dropped_behaviors == [["door_geo", "highlight"]]
        and dropped[0].needs_build,
        f"{dropped[0].dropped_behaviors}",
    )
    rt.sync(rt_steps("fade_in"), ranges=rt_range)
    check(
        "recipe: Build takes a dropped behavior's keys, never the animator's",
        door_keys(hl_path) == [(2000.0, 0.5)]
        and door_keys('["opacity"]')
        and not rt_store.edit_ledger.authored(behavior="highlight"),
        f"{door_keys(hl_path)}",
    )
    # Assess's "Nf unused" is what Trim Trailing Space would cut
    # (``ShotSequencer.content_end``), as mayatk's: the faded door also slides
    # until 130 frames in and holds to a key past the shot. Measured before
    # (mayatk): all but the fade read as unused. Fixed: 2026-11-08
    _d01 = next(s for s in rt_store.sorted_shots() if s.name == "D01")
    for _frame, _value in ((_d01.start, 0.0), (_d01.start + 130, 5.0), (_d01.end + 800, 5.0)):
        door.location.x = _value
        door.keyframe_insert(data_path="location", index=0, frame=_frame)
    [_tail] = rt.assess(rt_steps("fade_in"))
    check(
        "assess: the unused tail ends at a faded member's own keys",
        _tail.shrinkable_frames == _d01.end - (_d01.start + 130),
        f"{_tail.shrinkable_frames} in [{_d01.start}, {_d01.end}]",
    )

    _insert_in_sheet_order_checks(check, _BS, _BO)

    ShotStore.clear_active()
    ShotStore._prefs_dir_override = None
    return lines


def _insert_in_sheet_order_checks(check, _BS, _BO):
    """A Build on a built timeline inserts a new step's shot in sheet order
    through Blender's ``ShotSequencer``, as the panel's Build does: the shots
    after it move with their fcurve keys and VSE strips, and ONE undo puts
    keys, strips and shots back (mayatk's ``TestIncrementalPlacement`` ran
    only the bounds; 2026-10-12)."""
    import struct
    import wave

    import bpy

    import pythontk as ptk
    from blendertk import ShotStore
    from blendertk.anim_utils._anim_utils import AnimUtils
    from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
        BlenderShotManifest,
    )
    from blendertk.audio_utils._audio_utils import AudioUtils

    def keyed_cube(name, frames):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        for i, frame in enumerate(frames):
            obj.location.x = float(i)
            obj.keyframe_insert(data_path="location", index=0, frame=frame)

    def key_times(name):
        obj = bpy.data.objects[name]  # memfile undo replaces objects: by name
        return sorted(
            kp.co[0]
            for fc in AnimUtils.get_fcurves(obj)
            for kp in fc.keyframe_points
        )

    def strip_start():
        clip = AudioUtils.get_clip("ins_vo")
        return clip["frame_start"] if clip else None

    def layout():
        return [(s.name, s.start, s.end) for s in store.sorted_shots()]

    tmp = ptk.TempArtifacts("btk_manifest_insert", policy="scoped")
    try:
        wav_path = tmp.path(".wav")
        with wave.open(wav_path, "w") as w:  # 1 s of silence: 24 frames
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(48000)))
        keyed_cube("ins_a", [10, 40])
        keyed_cube("ins_b", [70, 100])

        ShotStore.clear_active()
        store = ShotStore()
        ShotStore.set_active(store)
        store.gap = 10.0
        mani = BlenderShotManifest(store, disabled_behaviors=())  # every behavior keyed: the clip is placed
        step_a = _BS("A", "A", "t", "", [_BO("ins_a")])
        step_b = _BS(
            "B",
            "A",
            "t",
            "",
            [
                _BO("ins_b"),
                _BO(
                    "ins_vo", behaviors=["set_clip"], kind="audio", source_path=wav_path
                ),
            ],
        )
        step_n = _BS("N", "A", "t", "", [])
        mani.sync([step_a, step_b], ranges={"A": (1.0, 51.0), "B": (61.0, 111.0)})
        before = (layout(), key_times("ins_a"), key_times("ins_b"), strip_start())
        check(
            "insert: built A, B; B's keys and strip in it",
            before[0] == [("A", 1.0, 51.0), ("B", 61.0, 111.0)]
            and before[2] == [70.0, 100.0]
            and before[3] == 61,
            f"{before}",
        )

        bpy.ops.ed.undo_push(message="before the build")
        steps = [step_a, step_n, step_b]
        plans = mani.plan_insertions(steps, initial_shot_length=50)
        check(
            "insert: N is planned after A, moving B 60 frames",
            [(p.step.step_id, p.start, p.end, p.push) for p in plans]
            == [("N", 61.0, 111.0, 60.0)],
            f"{[(p.step.step_id, p.start, p.end, p.push) for p in plans]}",
        )
        with store.scene_edit("manifest_build"), store.batch_update():  # the panel's
            inserted = mani.insert_planned(plans)
            shots = {sid: (s.start, s.end) for sid, s in mani.pair(steps).shots.items()}
            ranges = ptk.RangeResolver.incremental_ranges(steps, shots)
            mani.sync(
                steps, ranges=ranges, remove_missing=False, zero_duration_fallback=True
            )
        check(
            "insert: N lands between A and B, B moved by its length",
            inserted == {"N": "created"}
            and layout() == [("A", 1.0, 51.0), ("N", 61.0, 111.0), ("B", 121.0, 171.0)],
            f"{inserted} {layout()}",
        )
        check(
            "insert: B's fcurve keys and VSE strip moved with it, A's stayed",
            key_times("ins_b") == [130.0, 160.0]
            and key_times("ins_a") == [10.0, 40.0]
            and strip_start() == 121,
            f"b={key_times('ins_b')} a={key_times('ins_a')} strip={strip_start()}",
        )
        n_meta = store.shot_by_name("N").metadata or {}
        check(
            "insert: N is recorded as a build's (its step, the name it gave)",
            n_meta.get("step") == "N" and n_meta.get(mani.BUILD_NAME) == "N",
            f"{n_meta}",
        )

        bpy.ops.ed.undo()
        after = (layout(), key_times("ins_a"), key_times("ins_b"), strip_start())
        check(
            "insert: ONE undo puts the shots, keys and strip back",
            after == before,
            f"{after} != {before}",
        )
    finally:
        tmp.cleanup()

    # --- A build makes the helper a placing row names (`where`) where the scene has none: a camera for a picture,
    # an empty for a viewpoint or an arrow, at ptk.ManifestPlacement's plain first pose, riding what it is for.
    # Mirror of mayatk's PlacingHelpersTest. A delivery in metres, Z up: a cabinet 2 m tall, a latch on its front (-Y).
    import math

    from mathutils import Vector
    from pythontk.core_utils.engines.shots.manifest.manifest_placement import ManifestPlacement

    def cube(name, size, at, parent=None):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=at)
        made = bpy.context.active_object
        made.name = name
        made.scale = size
        if parent is not None:
            made.parent = parent
        return made

    delivery = bpy.data.objects.new("DELIVERY", None)
    bpy.context.scene.collection.objects.link(delivery)
    cube("BODY", (1.0, 0.6, 2.0), (0.0, 0.0, 1.0), delivery)
    latch = cube("PLACE_LATCH", (0.1, 0.04, 0.1), (0.2, -0.32, 1.1), delivery)
    bpy.context.view_layer.update()
    rows = [
        {"word": "teleport", "places": "stand", "assets": ["BODY"], "settings": {"where": "VIEW_A01"}},
        {"word": "indicate", "places": "point", "assets": ["PLACE_LATCH"], "settings": {"where": "ARROW_LATCH"}},
        {"word": "pip", "places": "view", "assets": ["PLACE_LATCH"], "settings": {"where": "CAM_A01"}},
        {"word": "pip", "places": "view", "assets": ["NO_SUCH_PART"], "settings": {"where": "CAM_NONE"}},
    ]
    # The floor a viewpoint stands on is the scene's: the lowest point of everything it shows (the cubes the checks
    # above left reach below this delivery).
    scene_floor = min((o.matrix_world @ Vector(c)).z for o in bpy.context.scene.objects if o.type == "MESH" for c in o.bound_box)
    store3 = ShotStore()
    mani3 = BlenderShotManifest(store3)
    placed = mani3.place_helpers([_BS("P01", "A", "t", "", rows=rows)])
    bpy.context.view_layer.update()
    check(
        "place: each named helper is made once, and a part the scene lacks is said",
        placed["made"] == ["VIEW_A01", "ARROW_LATCH", "CAM_A01"] and [n for n, _ in placed["skipped"]] == ["CAM_NONE"],
        f"{placed}",
    )
    view, arrow, cam = (bpy.data.objects.get(n) for n in ("VIEW_A01", "ARROW_LATCH", "CAM_A01"))
    if view and arrow and cam:
        at = view.matrix_world.translation
        facing = view.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))
        check(
            "place: a viewpoint is an empty under the delivery, on the floor 1.5 m out of its body's front (-Y), its +Z facing it",
            view.type == "EMPTY" and view.parent is delivery and abs(at.z - scene_floor) < 1e-4 and abs(at.y + (0.3 + ManifestPlacement.STAND_OFF)) < 0.02
            and facing.y > 0.99,
            f"type={view.type} parent={view.parent} at={tuple(at)} facing={tuple(facing)}",
        )
        tip = arrow.matrix_world.translation
        down = arrow.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))
        at_latch = (Vector((0.2, -0.32, 1.1)) - tip).normalized()
        check(
            "place: an arrow's tip is an empty riding its part, its +Z pointing at it, clear of the body's face by its room",
            arrow.parent is latch and down.dot(at_latch) > 0.999 and tip.y < -0.3 - ManifestPlacement.ARROW * 0.3,
            f"parent={arrow.parent} tip={tuple(tip)} down={tuple(down)}",
        )
        eye = cam.matrix_world.translation
        look = cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        to = Vector((0.2, -0.32, 1.1)) - eye
        angle = math.degrees(2 * math.atan(cam.data.sensor_height / 2 / cam.data.lens)) if cam.type == "CAMERA" else 0.0
        check(
            "place: a picture's camera rides its part, looks at it from far enough to frame it, with the picture's lens",
            cam.type == "CAMERA" and cam.parent is latch and look.dot(to.normalized()) > 0.999 and to.length > 0.2
            and cam.data.sensor_fit == "VERTICAL" and abs(angle - ManifestPlacement.VIEW_ANGLE) < 0.01,
            f"type={cam.type} parent={cam.parent} dot={look.dot(to.normalized()):.4f} far={to.length:.3f} angle={angle:.2f}",
        )
        cam.matrix_world.translation = Vector((0.6, -0.9, 1.2))
        bpy.context.view_layer.update()
        again = mani3.place_helpers([_BS("P01", "A", "t", "", rows=rows)])
        check(
            "place: a helper is the truth once made: a second build neither makes nor moves it",
            again["made"] == [] and sorted(again["kept"]) == ["ARROW_LATCH", "CAM_A01", "VIEW_A01"]
            and (cam.matrix_world.translation - Vector((0.6, -0.9, 1.2))).length < 1e-5,
            f"{again} {tuple(cam.matrix_world.translation)}",
        )

    # Dials on the back of a panel behind a door, the delivery reaching out behind them (-Y): by bounds alone the
    # outer side for the dials is the door's, where the panel and the door hide them. Clear of the cubes above (+50 X).
    cabinet = bpy.data.objects.new("PLACE_CABINET", None)
    bpy.context.scene.collection.objects.link(cabinet)
    cube("PLACE_DOOR", (1.0, 0.06, 2.0), (50.0, 0.0, 1.0), cabinet)
    cube("PLACE_PANEL", (0.8, 0.03, 0.6), (50.0, -0.415, 1.5), cabinet)
    cube("PLACE_DIALS", (0.1, 0.03, 0.1), (50.0, -0.445, 1.5), cabinet)
    line = bpy.data.curves.new("PLACE_REACH", "CURVE")
    spline = line.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (47.0, -6.0, 0.0, 1.0), (53.0, -0.6, 2.5, 1.0)
    reach = bpy.data.objects.new("PLACE_REACH", line)  # a curve: it spans the delivery's bounds, and no ray stops at it
    bpy.context.scene.collection.objects.link(reach)
    reach.parent = cabinet
    bpy.context.view_layer.update()
    dials_rows = [{"word": "pip", "places": "view", "assets": ["PLACE_DIALS"], "settings": {"where": "CAM_D01"}}]
    made = mani3.place_helpers([_BS("D01", "D", "t", "", rows=dials_rows)])["made"]
    bpy.context.view_layer.update()
    dials_cam = bpy.data.objects.get("CAM_D01")
    eye = dials_cam.matrix_world.translation if dials_cam else Vector()
    to = Vector((50.0, -0.445, 1.5)) - eye
    look = dials_cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0)) if dials_cam else Vector()
    check(
        "place: a picture stands where its part is seen, never behind what hides it",
        made == ["CAM_D01"] and eye.y < -0.46 and look.dot(to.normalized()) > 0.999,
        f"made={made} eye={tuple(eye)} dot={look.dot(to.normalized()) if to.length else 0:.4f}",
    )

    # A Target with no shape under it (an empty) is placed by where it stands, as mayatk's: none was made.
    mark = bpy.data.objects.new("PLACE_MARK", None)
    bpy.context.scene.collection.objects.link(mark)
    mark.parent = delivery
    mark.location = (0.2, -0.32, 1.5)
    bpy.context.view_layer.update()
    mark_rows = [{"word": "pip", "places": "view", "assets": ["PLACE_MARK"], "settings": {"where": "CAM_MARK"}}]
    made = mani3.place_helpers([_BS("M01", "M", "t", "", rows=mark_rows)])["made"]
    bpy.context.view_layer.update()
    mark_cam = bpy.data.objects.get("CAM_MARK")
    eye = mark_cam.matrix_world.translation if mark_cam else Vector()
    to = Vector((0.2, -0.32, 1.5)) - eye
    look = mark_cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0)) if mark_cam else Vector()
    check(
        "place: a Target with no geometry is placed by where it stands",
        made == ["CAM_MARK"] and mark_cam.parent is mark and 0.0 < to.length < 3.0 and look.dot(to.normalized()) > 0.999,
        f"made={made} eye={tuple(eye)}",
    )

    # A helper it could not finish is removed (mayatk's mirror): a failure after it is made, before it is parented.
    from unittest import mock

    fail_rows = [{"word": "pip", "places": "view", "assets": ["BODY"], "settings": {"where": "CAM_FAIL"}}]
    with mock.patch.object(ptk.ManifestPlacement, "RIDES", {}):
        try:
            mani3.place_helpers([_BS("F01", "F", "t", "", rows=fail_rows)])
            raised = False
        except KeyError:
            raised = True
    check(
        "place: a helper it could not finish is removed",
        raised and "CAM_FAIL" not in bpy.data.objects and "CAM_FAIL" not in bpy.data.cameras,
        f"raised={raised} objects={'CAM_FAIL' in bpy.data.objects} cameras={'CAM_FAIL' in bpy.data.cameras}",
    )

    # A `set` row's Target is given the state it names (mirror of mayatk's SetStatesTest): the emissive group worn on it
    # or under it takes the tool's defaults where it has none. Before, Blender's build skipped every one ("this host
    # keeps no emission states").
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.mat_utils.emissive_groups import EmissiveGroups

    def glowing(obj):
        mat = bpy.data.materials.new(obj.name + "Mat")
        bsdf = next(n for n in MatUtils.get_node_tree(mat, create=True).nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Emission Strength"].default_value = 1.0
        bsdf.inputs["Emission Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        obj.data.materials.append(mat)
        return obj

    def board(name, *parts):
        top = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(top)
        for i, part in enumerate(parts):
            cube(part, (0.1, 0.1, 0.1), (100.0 + i, 0.0, 0.0), top)
        return top

    board("STATE_BOARD", "STATE_LED", "STATE_PLAIN")
    EmissiveGroups.add_group("state_led", {glowing(bpy.data.objects["STATE_LED"]).name: []})
    set_rows = [
        {"word": "set", "assets": ["STATE_LED"], "settings": {"state": "off"}},
        {"word": "set", "assets": ["STATE_BOARD"], "settings": {"state": "green"}},  # a group above the indicator
        {"word": "set", "assets": ["STATE_LED"], "settings": {"state": "purple"}},
        {"word": "set", "assets": ["STATE_PLAIN"], "settings": {"state": "off"}},
    ]
    states = mani3.set_states([_BS("S01", "S", "t", "", rows=set_rows)])
    check(
        "states: a set row's group takes the tool's default states, found by the faces worn under its Target",
        states["given"] == [("STATE_LED", "off")] and states["kept"] == [("STATE_BOARD", "green")]
        and [(t, s) for t, s, _ in states["skipped"]] == [("STATE_LED", "purple"), ("STATE_PLAIN", "off")]
        and list(EmissiveGroups.states("state_led")) == ["off", "green", "blue", "amber", "red"],
        f"{states}",
    )
    board("STATE_PAIR", "STATE_BARE", "STATE_ONOFF")
    EmissiveGroups.add_group("state_bare", {glowing(bpy.data.objects["STATE_BARE"]).name: []})
    EmissiveGroups.add_group("state_onoff", {glowing(bpy.data.objects["STATE_ONOFF"]).name: []})
    EmissiveGroups.set_states("state_onoff", {"off": {"weight": 0.0}, "on": {"weight": 1.0}})
    pair = mani3.set_states([_BS("S02", "S", "t", "", rows=[{"word": "set", "assets": ["STATE_PAIR"], "settings": {"state": "green"}}])])
    check(
        "states: a Target whose groups cannot all take the state changes none",
        [(t, s) for t, s, _ in pair["skipped"]] == [("STATE_PAIR", "green")] and EmissiveGroups.states("state_bare") == {},
        f"{pair} {EmissiveGroups.states('state_bare')}",
    )


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
