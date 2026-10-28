# !/usr/bin/python
# coding=utf-8
"""Audio Clips engine (``blendertk.audio_utils.audio_clips.AudioClips``) headless test.

bpy-only: the operations tentacle's shared Audio Clips panel calls (mirror of
mayatk's ``AudioClips`` by name), over real VSE sound strips. The panel itself
is tentacle's ``test/test_audio_clips_panel.py``.

Run headless (fresh instance -- session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_audio_clips.py
"""

import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_audio_clips_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy
    import pythontk as ptk

    from blendertk.audio_utils._audio_utils import AudioUtils
    from blendertk.audio_utils.audio_clips import AudioClips
    from blendertk.core_utils.script_job_manager import ScriptJobManager

    check(
        "the panel vocabulary: a clip, placed by moving it, with no end key",
        (AudioClips.ITEM_NOUN, AudioClips.END_KEYS) == ("Clip", False)
        and ptk.AudioUtils.SOURCE_EXTENSIONS <= AudioClips.PLAYABLE_EXTENSIONS,
    )
    AudioUtils.remove_all_clips()
    scene = bpy.context.scene
    with ptk.TempArtifacts(prefix="btk_audio_clips_") as tmp:
        wav = str(tmp.path(".wav"))
        with wave.open(wav, "wb") as fh:  # one second of silence
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00" * 44100)
        stem = os.path.splitext(os.path.basename(wav))[0]
        scene.frame_current = 5
        added = AudioClips.add_tracks([wav, str(tmp.path(".wav"))])
        check(
            "add_tracks: each file a clip at the current frame, a missing one skipped",
            added == [stem] and AudioUtils.get_clip(stem)["frame_start"] == 5,
            f"{added}",
        )
        check(
            "list_tracks / find_track name the strips",
            AudioClips.list_tracks() == [stem]
            and AudioClips.find_track(stem) == stem
            and AudioClips.find_track("nowhere") is None,
            f"{AudioClips.list_tracks()}",
        )
        placed = AudioClips.key_track(stem, frame=20)
        check(
            "key_track moves the strip (its position is its keyed state)",
            placed == 20.0 and AudioUtils.get_clip(stem)["frame_start"] == 20,
            f"{placed} {AudioUtils.get_clip(stem)}",
        )
        scene.frame_current = 30
        AudioClips.key_track(stem)
        check(
            "key_track places at the current frame by default",
            AudioUtils.get_clip(stem)["frame_start"] == 30,
        )
        renamed = AudioClips.rename_track(stem, "early")
        check(
            "rename_track returns the strip's new name",
            renamed == "early" and AudioClips.find_track("early") == "early",
            f"{renamed}",
        )
        refused = []
        for call in (
            lambda: AudioClips.rename_track("nowhere", "x"),
            lambda: AudioClips.replace_track("nowhere", wav),
            lambda: AudioClips.key_track("nowhere", frame=1),
            lambda: AudioClips.reveal(None),
            lambda: AudioClips.reveal("nowhere"),
        ):
            try:
                call()
            except ValueError as exc:
                refused.append(str(exc))
        check(
            "a missing (or unnamed) clip is refused with a ValueError for the user",
            len(refused) == 5,
            f"{refused}",
        )
        check(
            "replace_track keeps the strip's name",
            AudioClips.replace_track("early", wav) == "early",
        )
        played = AudioClips.trim_track("early", start=1, end=1)
        check(
            "trim_track trims both ends and reports the frames that play",
            played == AudioUtils.get_clip("early")["duration"]
            and AudioClips.get_trim("early") == (1, 1),
            f"{played} {AudioClips.get_trim('early')}",
        )
        check(
            "trim_track / get_trim answer None for a missing clip",
            AudioClips.trim_track("nowhere", start=1) is None
            and AudioClips.get_trim("nowhere") is None,
        )
        info = AudioUtils.get_clip("early")
        check(
            "sync_scene_range fits the frame range to the clips",
            AudioClips.sync_scene_range(extend_only=False)
            == (info["frame_start"], info["frame_end"]),
            f"{(scene.frame_start, scene.frame_end)} vs {info}",
        )
        check(
            "reveal selects the clip in the Sequencer",
            AudioClips.reveal("early") == "early"
            and scene.sequence_editor.active_strip.name == "early",
        )
        check(
            "remove_track deletes one clip",
            AudioClips.remove_track("early") is True and AudioClips.list_tracks() == [],
        )
        AudioClips.add_tracks([wav, wav])
        check(
            "remove deletes every clip",
            AudioClips.remove() == 2 and AudioClips.list_tracks() == [],
        )
    owner = type("Owner", (), {})()
    events = []
    tokens = AudioClips.watch(
        owner, on_scene=lambda: events.append("scene"), on_time=events.append
    )
    jobs = ScriptJobManager.instance()
    jobs._dispatch("SceneOpened")
    jobs._dispatch("timeChanged")
    check(
        "watch: a file load and a frame change reach the owner",
        len(tokens) == 2
        and events == ["scene", float(bpy.context.scene.frame_current)],
        f"{tokens} {events}",
    )
    jobs.unsubscribe_all(owner)
    AudioUtils.remove_all_clips()
    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_audio_clips_checks()
    except Exception as e:  # pragma: no cover - harness failure prints its own trace
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
