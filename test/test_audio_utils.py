"""blendertk audio_utils (scene-wide VSE sound-clip CRUD) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_audio_utils.py
"""
import sys, os, wave, struct, tempfile, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


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
    check("remove_clip on a missing name returns False", AudioUtils.remove_clip("renamed_a") is False)

    before_count = len(AudioUtils.list_clips())
    n_removed = AudioUtils.remove_all_clips()
    check(
        "remove_all_clips removed everything left",
        n_removed == before_count and AudioUtils.list_clips() == [],
        f"n_removed={n_removed} remaining={AudioUtils.list_clips()}",
    )

    # ---- sequencer consumers: fps, range query, range shift, waveform, segments
    from blendertk.audio_utils.segments import AudioSegment

    check("get_fps reads the scene clock", AudioUtils.get_fps() == scene.render.fps / scene.render.fps_base)
    s1 = AudioUtils.add_clip(wav_a, frame_start=10, name="seq_a")
    s2 = AudioUtils.add_clip(wav_b, frame_start=100, name="seq_b")
    in_rng = [c["name"] for c in AudioUtils.clips_in_range(0, 50)]
    check("clips_in_range: only the strip overlapping [0,50]", in_rng == [s1], str(in_rng))
    moved = AudioUtils.shift_clips_in_range(0, 50, 7)
    check("shift_clips_in_range: moved only the in-range strip", moved == [s1], str(moved))
    check(
        "shift_clips_in_range: strip start 10 -> 17, other untouched",
        AudioUtils.get_clip(s1)["frame_start"] == 17 and AudioUtils.get_clip(s2)["frame_start"] == 100,
        f"{AudioUtils.get_clip(s1)['frame_start']} / {AudioUtils.get_clip(s2)['frame_start']}",
    )
    moved_named = AudioUtils.shift_clips_in_range(0, 200, -5, names=[s2])
    check("shift_clips_in_range(names=): scoped to the named strip", moved_named == [s2] and AudioUtils.get_clip(s2)["frame_start"] == 95)
    check("shift_clips_in_range: zero delta is a no-op", AudioUtils.shift_clips_in_range(0, 200, 0.0) == [])
    wf = AudioUtils.cached_waveform(wav_a)
    check("cached_waveform: envelope read from WAV", isinstance(wf, list) and len(wf) > 0, str(len(wf)))
    check("cached_waveform: cached object reused", AudioUtils.cached_waveform(wav_a) is wf)
    AudioUtils.clear_waveform_cache()
    check("clear_waveform_cache: recomputed (new object)", AudioUtils.cached_waveform(wav_a) is not wf)
    segs = AudioSegment.collect_all_segments()
    check("AudioSegment.collect_all_segments: one per strip, sorted by start", [sg.track_id for sg in segs] == [s1, s2], str([sg.track_id for sg in segs]))
    if segs:
        sg = segs[0]
        check(
            "AudioSegment fields mirror the strip's visible span",
            sg.start == 17 and sg.end == AudioUtils.get_clip(s1)["frame_end"] and sg.duration == sg.end - sg.start and sg.is_audio and sg.label == s1,
            f"{sg}",
        )
    only_late = AudioSegment.collect_all_segments(scene_start=90)
    check("collect_all_segments(scene_start): drops strips ending before it", [sg.track_id for sg in only_late] == [s2])
    one = AudioSegment.collect_segments_for_track(s2, include_waveform=False)
    check("collect_segments_for_track: the named strip, no waveform", len(one) == 1 and one[0].track_id == s2 and one[0].waveform == [])
    check("collect_segments_for_track: unknown -> []", AudioSegment.collect_segments_for_track("nope") == [])
    AudioUtils.remove_all_clips()

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
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
