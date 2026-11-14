"""blendertk audio_utils (scene-wide VSE sound-clip CRUD) headless test.
Run: blender --background --factory-startup --python blendertk/tests/test_audio_utils.py
"""

import sys
import os
import wave
import struct
import tempfile
import traceback
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

# The real exports below run the Scene Exporter: isolate its preset store and
# the per-user cache, as test_export_baselines does.
os.environ.setdefault("UITK_PRESETS_ROOT", tempfile.mkdtemp(prefix="btk_audio_presets_"))
os.environ.setdefault("PYTHONTK_CACHE_ROOT", tempfile.mkdtemp(prefix="btk_audio_cache_"))

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def make_wav(path, seconds=1.0, sr=44100):
    n = int(sr * seconds)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"".join(struct.pack("<h", 0) for _ in range(n)))


try:
    import bpy
    from blendertk.audio_utils._audio_utils import AudioUtils

    tmp_dir = tempfile.mkdtemp(prefix="btk_audio_")
    wav_a = os.path.join(tmp_dir, "clip_a.wav")
    wav_b = os.path.join(tmp_dir, "clip_b.wav")
    make_wav(wav_a, seconds=1.0)  # ~24 frames @ 24fps
    make_wav(wav_b, seconds=2.0)  # ~48 frames

    scene = bpy.context.scene
    scene.frame_current = 10

    check("no clips initially", AudioUtils.list_clips() == [])

    # ---- add: places at the current frame by default, auto-picks a free channel
    name_a = AudioUtils.add_clip(wav_a)
    check("add_clip returns a name", isinstance(name_a, str) and name_a, name_a)

    info = AudioUtils.get_clip(name_a)
    check("get_clip finds it", info is not None)
    check("add_clip placed at current frame", info["frame_start"] == 10, str(info))
    check("add_clip duration > 0", info["duration"] > 0, str(info))
    check(
        "add_clip resolved filepath",
        os.path.normpath(info["filepath"]).lower() == os.path.normpath(wav_a).lower(),
        info["filepath"],
    )

    name_b = AudioUtils.add_clip(wav_b, frame_start=100, channel=2, name="clip_b_named")
    check("add_clip explicit name/channel/frame", name_b == "clip_b_named")
    info_b = AudioUtils.get_clip(name_b)
    check("explicit channel honored", info_b["channel"] == 2, str(info_b))
    check("explicit frame_start honored", info_b["frame_start"] == 100, str(info_b))

    clips = AudioUtils.list_clips()
    check(
        "list_clips returns both, sorted by start",
        [c["name"] for c in clips] == [name_a, name_b],
        str([c["name"] for c in clips]),
    )

    # ---- auto free-channel avoidance: a clip at the same frame as clip A gets a
    # different channel so the two don't stack on top of each other
    name_c = AudioUtils.add_clip(wav_a, frame_start=10)
    info_c = AudioUtils.get_clip(name_c)
    check(
        "auto free-channel avoids collision with a clip at the same frame",
        info_c["channel"] != info["channel"],
        f"a={info['channel']} c={info_c['channel']}",
    )

    # ---- move: repositions without touching trim
    moved = AudioUtils.move_clip(name_a, 20)
    check("move_clip returns True", moved is True)
    info = AudioUtils.get_clip(name_a)
    check("move_clip repositioned", info["frame_start"] == 20, str(info))

    # ---- trim: shrinks the visible range from both ends
    trimmed = AudioUtils.trim_clip(name_a, offset_start=2, offset_end=3)
    check("trim_clip returns True", trimmed is True)
    info2 = AudioUtils.get_clip(name_a)
    check(
        "trim_clip shrank the visible duration by offset_start + offset_end",
        info2["duration"] == info["duration"] - 5,
        f"before={info['duration']} after={info2['duration']}",
    )
    check(
        "trim_start/trim_end recorded",
        info2["trim_start"] == 2 and info2["trim_end"] == 3,
        str(info2),
    )

    # ---- rename (+ Blender's own auto-suffix on collision)
    new_name = AudioUtils.rename_clip(name_a, "renamed_a")
    check("rename_clip applied", new_name == "renamed_a", new_name)
    check("get_clip old name gone", AudioUtils.get_clip(name_a) is None)
    check("get_clip new name present", AudioUtils.get_clip("renamed_a") is not None)

    dup_name = AudioUtils.rename_clip(name_b, "renamed_a")
    check(
        "rename collision auto-suffixes instead of silently overwriting",
        dup_name is not None and dup_name != "renamed_a",
        dup_name,
    )

    # ---- replace: swaps the source file but preserves position/channel/trim (Blender's
    # own strip.sound reassignment does not re-fit the strip to the new source's length)
    replaced = AudioUtils.replace_clip("renamed_a", wav_b)
    check("replace_clip returns True", replaced is True)
    info3 = AudioUtils.get_clip("renamed_a")
    check(
        "replace_clip swapped the file path",
        os.path.normpath(info3["filepath"]).lower() == os.path.normpath(wav_b).lower(),
        info3["filepath"],
    )
    check(
        "replace_clip preserved position/channel/trim",
        info3["frame_start"] == info2["frame_start"]
        and info3["channel"] == info2["channel"]
        and info3["duration"] == info2["duration"],
        f"before={info2} after={info3}",
    )

    try:
        AudioUtils.replace_clip("renamed_a", os.path.join(tmp_dir, "nope.wav"))
        check("replace_clip raises FileNotFoundError on a missing file", False)
    except FileNotFoundError:
        check("replace_clip raises FileNotFoundError on a missing file", True)

    # ---- sync_scene_range: extend-only vs exact-fit
    scene.frame_start = 1
    scene.frame_end = 5
    rng = AudioUtils.sync_scene_range(extend_only=True)
    all_clips = AudioUtils.list_clips()
    expected_end = max(c["frame_end"] for c in all_clips)
    check(
        "sync_scene_range (extend_only) grows frame_end to bracket clips, keeps frame_start",
        rng[1] >= expected_end and rng[0] <= 1,
        f"rng={rng} expected_end={expected_end}",
    )

    scene.frame_start = 1
    scene.frame_end = 99999
    rng2 = AudioUtils.sync_scene_range(extend_only=False)
    expected_start = min(c["frame_start"] for c in all_clips)
    check(
        "sync_scene_range (exact fit) brackets the clips exactly, can shrink",
        rng2 == (expected_start, expected_end),
        f"rng2={rng2} expected=({expected_start},{expected_end})",
    )

    # ---- remove / remove_all
    removed = AudioUtils.remove_clip("renamed_a")
    check("remove_clip returns True", removed is True)
    check("remove_clip actually removed it", AudioUtils.get_clip("renamed_a") is None)
    check(
        "remove_clip on a missing name returns False",
        AudioUtils.remove_clip("renamed_a") is False,
    )

    before_count = len(AudioUtils.list_clips())
    n_removed = AudioUtils.remove_all_clips()
    check(
        "remove_all_clips removed everything left",
        n_removed == before_count and AudioUtils.list_clips() == [],
        f"n_removed={n_removed} remaining={AudioUtils.list_clips()}",
    )

    # ---- sequencer consumers: fps, range query, range shift, waveform, segments
    from blendertk.audio_utils.segments import AudioSegment

    check(
        "get_fps reads the scene clock",
        AudioUtils.get_fps() == scene.render.fps / scene.render.fps_base,
    )
    s1 = AudioUtils.add_clip(wav_a, frame_start=10, name="seq_a")
    s2 = AudioUtils.add_clip(wav_b, frame_start=100, name="seq_b")
    in_rng = [c["name"] for c in AudioUtils.clips_in_range(0, 50)]
    check(
        "clips_in_range: only the strip overlapping [0,50]", in_rng == [s1], str(in_rng)
    )
    moved = AudioUtils.shift_clips_in_range(0, 50, 7)
    check(
        "shift_clips_in_range: moved only the in-range strip", moved == [s1], str(moved)
    )
    check(
        "shift_clips_in_range: strip start 10 -> 17, other untouched",
        AudioUtils.get_clip(s1)["frame_start"] == 17
        and AudioUtils.get_clip(s2)["frame_start"] == 100,
        f"{AudioUtils.get_clip(s1)['frame_start']} / {AudioUtils.get_clip(s2)['frame_start']}",
    )
    moved_named = AudioUtils.shift_clips_in_range(0, 200, -5, names=[s2])
    check(
        "shift_clips_in_range(names=): scoped to the named strip",
        moved_named == [s2] and AudioUtils.get_clip(s2)["frame_start"] == 95,
    )
    check(
        "shift_clips_in_range: zero delta is a no-op",
        AudioUtils.shift_clips_in_range(0, 200, 0.0) == [],
    )
    wf = AudioUtils.cached_waveform(wav_a)
    check(
        "cached_waveform: envelope read from WAV",
        isinstance(wf, list) and len(wf) > 0,
        str(len(wf)),
    )
    check(
        "cached_waveform: cached object reused", AudioUtils.cached_waveform(wav_a) is wf
    )
    AudioUtils.clear_waveform_cache()
    check(
        "clear_waveform_cache: recomputed (new object)",
        AudioUtils.cached_waveform(wav_a) is not wf,
    )
    segs = AudioSegment.collect_all_segments()
    check(
        "AudioSegment.collect_all_segments: one per strip, sorted by start",
        [sg.track_id for sg in segs] == [s1, s2],
        str([sg.track_id for sg in segs]),
    )
    if segs:
        sg = segs[0]
        check(
            "AudioSegment fields mirror the strip's visible span",
            sg.start == 17
            and sg.end == AudioUtils.get_clip(s1)["frame_end"]
            and sg.duration == sg.end - sg.start
            and sg.is_audio
            and sg.label == s1,
            f"{sg}",
        )
    only_late = AudioSegment.collect_all_segments(scene_start=90)
    check(
        "collect_all_segments(scene_start): drops strips ending before it",
        [sg.track_id for sg in only_late] == [s2],
    )
    one = AudioSegment.collect_segments_for_track(s2, include_waveform=False)
    check(
        "collect_segments_for_track: the named strip, no waveform",
        len(one) == 1 and one[0].track_id == s2 and one[0].waveform == [],
    )
    check(
        "collect_segments_for_track: unknown -> []",
        AudioSegment.collect_segments_for_track("nope") == [],
    )
    AudioUtils.remove_all_clips()

    # ---- export: the audio record (the SceneRecords.AUDIO producer,
    # mirror of mayatk's AudioClips.export_record) and the files it names
    import pythontk as ptk

    def norm(path):
        return os.path.normcase(os.path.normpath(path))

    scene.frame_start, scene.frame_end = 1, 250
    AudioUtils.add_clip(wav_a, frame_start=5, name="steps")
    AudioUtils.add_clip(wav_b, frame_start=30, name="punch")
    AudioUtils.add_clip(wav_a, frame_start=12, name="muted")
    scene.sequence_editor.strips_all["muted"].mute = True
    AudioUtils.add_clip(wav_a, frame_start=40, channel=9, name="on_muted_channel")
    scene.sequence_editor.channels[9].mute = True
    check(
        "bake_events: every strip that plays, by frame; a muted strip or channel ships none",
        AudioUtils.bake_events() == [(5, "steps"), (30, "punch")],
        str(AudioUtils.bake_events()),
    )
    files = AudioUtils.track_files(["punch", "steps", "nope"])
    check(
        "track_files: (track id + extension, path) per track with a file, in the order asked",
        [(n, norm(p)) for n, p in files]
        == [("punch.wav", norm(wav_b)), ("steps.wav", norm(wav_a))],
        str(files),
    )

    # The record v3 (2026-11-05): each strip cut into cues per clip, in
    # seconds from the clip's start, with its offset into the sound and its
    # stop -- read back here in frames at the scene's rate. The takes are the
    # shot store's (ptk.ShotStore.declared_takes); with none, the file's one
    # take holds exactly the range the write bakes -- the scene's.
    from blendertk import ShotStore

    fps = AudioUtils.get_fps(scene)

    def cues(record):
        """``[(clip, sound, time, offset, stop)]`` in frames."""
        return [
            (
                c["clip"],
                c["sound"],
                round(c["time"] * fps, 3),
                round(c["offset"] * fps, 3),
                round(c["stop"] * fps, 3),
            )
            for c in (record.payload["cues"] if record is not None else [])
        ]

    ShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_audio_prefs_")
    ShotStore.clear_active()
    scene.frame_start, scene.frame_end = 1, 60
    ctx = ptk.ExportContext()
    record = AudioUtils.export_record(ctx)
    check(
        "export_record: no shots -> the one take's cues, counted from the range it bakes, stopped at its end",
        record is not None
        and record.spec is ptk.SceneRecords.AUDIO
        and cues(record)
        == [("", "steps.wav", 4, 0, 28), ("", "punch.wav", 29, 0, 59)],
        str(record and record.payload),
    )
    check(
        "export_record: no frame and no rate leave the DCC",
        record is not None and set(record.payload) == {"version", "cues"},
        str(record and record.payload),
    )
    # ``media`` -- each embedded file's SHA-256, what Unity compares a
    # same-named sound against before it reuses one -- is the FBX dependency
    # pass's to write, from the bytes it embedded (ptk.FbxMedia
    # .embed_dependencies; mayatk's producer too). Written here from the
    # source files, it named audio a raw export never embedded, and Unity
    # bound nothing where a library clip had bound. Added: 2026-10-31
    check(
        "export_record: the record leaves its digests to the dependency pass",
        record is not None
        and "media" not in record.payload
        and not ptk.SceneRecords.problems(ptk.SceneRecords.AUDIO, record.payload),
        str(record and record.payload),
    )
    _au_store = ShotStore.active()
    _au_store.define_shot("Intro", 1, 20)
    _au_store.define_shot("Fight", 21, 25)
    ctx = ptk.ExportContext()
    record = AudioUtils.export_record(ctx)
    check(
        "export_record: a strip across a cut is a cue per shot, each stopped at the cut",
        cues(record)
        == [("Intro", "steps.wav", 4, 0, 19), ("Fight", "steps.wav", 0, 16, 4)],
        str(record and record.payload),
    )
    check(
        "export_record: a strip in no shot's take is dropped, and said",
        any("punch.wav@30" in note for note in ctx.notes),
        str(ctx.notes),
    )
    for _sid in [s.shot_id for s in list(_au_store.shots)]:
        _au_store.remove_shot(_sid)
    ShotStore.clear_active()
    # A strip that starts before the one take does plays from its offset.
    scene.frame_start = 10
    ctx = ptk.ExportContext()
    record = AudioUtils.export_record(ctx)
    scene.frame_start = 1
    check(
        "export_record: a strip starting before its clip starts at its offset into the sound",
        cues(record)
        == [("", "steps.wav", 0, 5, 19), ("", "punch.wav", 20, 0, 50)],
        f"{record and record.payload} {ctx.notes}",
    )
    AudioUtils.trim_clip("punch", offset_start=3, offset_end=0)
    ctx = ptk.ExportContext()
    record = AudioUtils.export_record(ctx)
    check(
        "export_record: a head-trimmed strip starts its trim into its file",
        ("", "punch.wav", 32, 3, 59) in cues(record),
        f"{record and record.payload} {ctx.notes}",
    )
    check(
        "export_record: a write that carries no animation has no clip to cue",
        AudioUtils.export_record(ptk.ExportContext(animated=False)) is None,
    )
    AudioUtils.remove_all_clips()
    check(
        "export_record: no strip -> no record",
        AudioUtils.export_record(ptk.ExportContext()) is None,
    )

    # ---- real exports: the cues as a Scene Exporter write ships them -------
    # The FBX's own take windows and key extents (ptk.FbxFile) against its
    # ``audio`` record, and the GLB carrying that record as it stands (less
    # ``media``: a GLB embeds no sound file). Each cue's time, added to where
    # its clip's take starts in the file, lands on the frame the sound
    # starts on in the scene -- mayatk's TestAudioClipsRealExport, the same
    # scenes. Added: 2026-11-05
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter

    export_dir = tempfile.mkdtemp(prefix="btk_audio_export_")

    def reset_scene():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for block in list(bpy.data.actions):
            bpy.data.actions.remove(block)
        AudioUtils.remove_all_clips()
        ShotStore.clear_active()
        if "data_internal" in scene:
            del scene["data_internal"]

    def real_export(name, objects, **tasks):
        written = SceneExporter().perform_export(
            export_dir=export_dir,
            objects=objects,
            output_name=name,
            export_visible=True,
            tasks={"output_format": "fbx_glb", "export_data_node": True, **tasks},
        )
        check(f"real export: the {name} export writes its deliverable", written is True, written)
        fbx = os.path.join(export_dir, f"{name}.fbx")
        glb = os.path.join(export_dir, f"{name}.glb")
        rec = ptk.FbxMedia.record(fbx, ptk.SceneRecords.AUDIO.key)
        check(
            f"real export: the {name} FBX carries the audio record, v3",
            not ptk.SceneRecords.problems(ptk.SceneRecords.AUDIO, rec),
            rec,
        )
        with ptk.MeshConvert.open_glb(glb) as edit:
            carried = (edit.gltf.get("extras") or {}).get(ptk.SceneRecords.AUDIO.key)
        check(
            f"real export: the {name} GLB carries the record as it stands",
            carried == {k: v for k, v in (rec or {}).items() if k != "media"},
            f"{carried} vs {rec}",
        )
        return ptk.FbxFile.load(fbx, span_arrays=("KeyTime",)), rec or {"cues": []}

    def strip_wav(name, start, stop):
        path = os.path.join(export_dir, f"{name}_src.wav")
        make_wav(path, seconds=(stop - start) / fps)
        AudioUtils.add_clip(path, frame_start=start, name=name)
        return path

    # The S2 case: keys 0-100, the scene range 20-80, Bake Range "Scene
    # Animation Range", a sound at 50-60. Blender samples exactly the range it
    # bakes, so the file's one take is 20-80 and the cue is 30 frames in --
    # the same scene frame Maya's take (its keys, 0-100) puts it at, 50 in.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    box = bpy.context.active_object
    box.name = "box"
    for frame, x in ((0, 0.0), (100, 10.0)):
        box.location.x = x
        box.keyframe_insert("location", index=0, frame=frame)
    scene.frame_start, scene.frame_end = 20, 80
    strip_wav("hit", 50, 60)
    fbx, rec = real_export("s2", [box], set_bake_animation_range="scene")
    spans = fbx.take_spans()
    first = min((v[0] for v in spans.values()), default=None)
    check(
        "real export S2: the one take holds exactly the baked range, 20-80",
        len(spans) == 1 and round(first * fps, 3) == 20,
        {k: (round(a * fps, 3), round(b * fps, 3)) for k, (a, b) in spans.items()},
    )
    check(
        "real export S2: the take declares what it holds (no fit needed)",
        fbx.take_windows() == spans,
        f"{fbx.take_windows()} vs {spans}",
    )
    got = [
        (c["clip"], c["sound"], round((first + c["time"]) * fps, 3),
         round(c["offset"] * fps, 3), round((first + c["stop"]) * fps, 3))
        for c in rec["cues"]
    ] if first is not None else rec["cues"]
    check(
        "real export S2: the cue lands on the sound's scene frame, counted from where the take starts",
        got == [("", "hit.wav", 50, 0, 60)],
        got,
    )

    # The same scene under Bake Range "keys": the range task runs LAST, after
    # the records publish, and sets 0-100; the producer reads the range the
    # run predicts (TaskManager._predicted_bake_range). Measured before that
    # (2026-11-05): the take 0-100, the cue counted from the scene's 20, so it
    # landed on frame 30.
    fbx, rec = real_export("s2_keys", [box], set_bake_animation_range="keys")
    spans = fbx.take_spans()
    first = min((v[0] for v in spans.values()), default=None)
    got = [
        (c["clip"], round((first + c["time"]) * fps, 3), round((first + c["stop"]) * fps, 3))
        for c in rec["cues"]
    ] if first is not None else rec["cues"]
    check(
        "real export S2, Bake Range keys: the take is the keys, 0-100, and the cue still lands on frame 50",
        round(first * fps, 3) == 0 and got == [("", 50, 60)],
        f"{spans} {got}",
    )
    check(
        "real export S2, Bake Range keys: the take declares what it holds",
        fbx.take_windows() == spans,
        f"{fbx.take_windows()} vs {spans}",
    )

    # FbxUtils.export, no Scene Exporter (the hand-offs' write): keys 10-100
    # under a 1-120 range -- Maya's writer declares 1-120 over keys 10-100;
    # Blender's bakes 1-120, a key at each end, and declares that. The cue
    # the producer cuts before the write counts from the same start.
    from blendertk.env_utils.fbx_utils import FbxUtils as _FbxUtils

    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    lone = bpy.context.active_object
    lone.name = "lone"
    for frame, x in ((10, 0.0), (100, 10.0)):
        lone.location.x = x
        lone.keyframe_insert("location", index=0, frame=frame)
    scene.frame_start, scene.frame_end = 1, 120
    strip_wav("tick", 50, 60)
    direct = _FbxUtils.export(os.path.join(export_dir, "direct.fbx"), [lone], bake_anim=True)
    loaded = ptk.FbxFile.load(direct, span_arrays=("KeyTime",))
    def frames(d):
        return {k: (round(a * fps, 3), round(b * fps, 3)) for k, (a, b) in d.items()}

    check(
        "FbxUtils.export: the shotless take declares exactly what it holds, the baked 1-120",
        frames(loaded.take_windows()) == frames(loaded.take_spans()) == {"Scene": (1.0, 120.0)},
        f"{frames(loaded.take_windows())} vs {frames(loaded.take_spans())}",
    )
    record = AudioUtils.export_record(_FbxUtils.export_context())
    start = loaded.take_windows().get("Scene", (0.0, 0.0))[0]
    cues = [
        (round((start + c["time"]) * fps, 3), round((start + c["stop"]) * fps, 3))
        for c in (record.payload["cues"] if record else [])
    ]
    check(
        "FbxUtils.export: the cue counts from the window the file declares",
        cues == [(50.0, 60.0)],
        cues,
    )

    # One take per shot: a sound across the cut is a cue in each, stopped at
    # the cut, the next playing it on from its offset.
    reset_scene()
    scene.frame_start, scene.frame_end = 1, 40
    bpy.ops.mesh.primitive_cube_add()
    mover = bpy.context.active_object
    mover.name = "mover"
    for frame, x in ((1, 0.0), (40, 10.0)):
        mover.location.x = x
        mover.keyframe_insert("location", index=0, frame=frame)
    store = ShotStore.active()
    store.define_shot("ShotA", 1, 20, objects=["mover"])
    store.define_shot("ShotB", 21, 40, objects=["mover"])
    store.publish_export_view()
    strip_wav("hum", 15, 30)
    fbx, rec = real_export("cut", [mover])
    windows = fbx.take_windows()
    got = [
        (c["clip"], round((windows[c["clip"]][0] + c["time"]) * fps, 3),
         round(c["offset"] * fps, 3), round((windows[c["clip"]][0] + c["stop"]) * fps, 3))
        for c in rec["cues"]
        if c["clip"] in windows
    ]
    check(
        "real export shots: one take per shot, the sound cut at the shots' cut",
        sorted(windows) == ["ShotA", "ShotB"]
        and got == [("ShotA", 15, 0, 20), ("ShotB", 21, 6, 30)],
        f"{windows} {got}",
    )
    reset_scene()
    shutil.rmtree(export_dir, ignore_errors=True)
    ShotStore._prefs_dir_override = None

    scene.frame_start, scene.frame_end = 1, 250
    rng3 = AudioUtils.sync_scene_range()
    check("sync_scene_range is a no-op with zero clips", rng3 == (1, 250), str(rng3))

    try:
        AudioUtils.add_clip(os.path.join(tmp_dir, "missing.wav"))
        check("add_clip raises FileNotFoundError for a missing source", False)
    except FileNotFoundError:
        check("add_clip raises FileNotFoundError for a missing source", True)

    os.remove(wav_a)
    os.remove(wav_b)
    os.rmdir(tmp_dir)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
passed = sum(1 for line in lines if line.startswith("OK"))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(lines)})")
