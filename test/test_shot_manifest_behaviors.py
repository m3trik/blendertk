# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest ``behaviors`` appliers test (mirror of mayatk's).

bpy-only suite: drives ``Behaviors.apply_behavior`` / ``verify_behavior`` /
``apply_audio_clip`` / ``compute_duration`` / ``apply_to_shots`` against a real
headless scene and asserts the Maya contract — template keys land on the
``opacity`` property mirrored to a stepped ``hide_render`` curve, anchors
distribute across multi-behavior objects, verification routes on
``verify.mode``, audio is placed as a VSE strip and measured from its source.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_shot_manifest_behaviors.py
"""

import json
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

TEMP = os.path.join(HERE, "temp_tests", "shot_manifest_behaviors")


def _write_wav(path, seconds=1.0, rate=48000):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(struct.pack("<h", 0) for _ in range(int(rate * seconds)))
        )


def _run_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import bpy
    from types import SimpleNamespace

    from blendertk import BlenderShotStore
    from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors
    from blendertk.audio_utils._audio_utils import AudioUtils
    from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

    os.makedirs(TEMP, exist_ok=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.render.fps_base = 1.0

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for name in ("fade_geo", "both_geo", "plain_geo", "flat_geo"):
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = name

    def keys(obj_name, data_path):
        obj = bpy.data.objects[obj_name]
        out = []
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if fc.data_path == data_path:
                out.extend(
                    (round(kp.co[0], 3), round(kp.co[1], 3), kp.interpolation)
                    for kp in fc.keyframe_points
                )
        return sorted(out)

    OP = f'["{RenderOpacity.ATTR_NAME}"]'
    VIS = RenderOpacity.VIS_PATH

    # ---- apply_behavior: dual-keyed opacity + stepped hide_render ---------
    Behaviors.apply_behavior("fade_geo", "fade_in", 10, 100)
    op = keys("fade_geo", OP)
    vis = keys("fade_geo", VIS)
    check(
        "apply_behavior: fade_in keys opacity 0->1 over the template duration",
        [(t, v) for t, v, _ in op] == [(10.0, 0.0), (25.0, 1.0)],
        f"{op}",
    )
    check(
        "apply_behavior: opacity keys use the template tangent (LINEAR)",
        all(i == "LINEAR" for _, _, i in op),
        f"{op}",
    )
    check(
        "apply_behavior: hide_render mirrored + stepped (hidden at 0, visible at 1)",
        vis == [(10.0, 1.0, "CONSTANT"), (25.0, 0.0, "CONSTANT")],
        f"{vis}",
    )
    check(
        "apply_behavior: opacity property auto-created (RenderOpacity)",
        RenderOpacity.ATTR_NAME in bpy.data.objects["fade_geo"],
    )

    # anchor_override places the block relative to the range end
    Behaviors.apply_behavior("plain_geo", "fade_in", 10, 100, anchor_override=1.0)
    op = [(t, v) for t, v, _ in keys("plain_geo", OP)]
    check(
        "apply_behavior: anchor_override=1.0 anchors fade_in at the range end",
        op == [(85.0, 0.0), (100.0, 1.0)],
        f"{op}",
    )

    # missing object -> RuntimeError (recorded as a failure by apply_to_shots)
    try:
        Behaviors.apply_behavior("nope_geo", "fade_in", 0, 10)
        raised = False
    except RuntimeError:
        raised = True
    check("apply_behavior: missing object raises RuntimeError", raised)

    # ---- verify_behavior: values_in_range / missing / exact ---------------
    check(
        "verify_behavior: values_in_range passes on the keyed object",
        Behaviors.verify_behavior("fade_geo", "fade_in", 10, 100),
    )
    check(
        "verify_behavior: values_in_range fails outside the keyed window",
        not Behaviors.verify_behavior("fade_geo", "fade_in", 200, 300),
    )
    check(
        "verify_behavior: unkeyed object fails",
        not Behaviors.verify_behavior("both_geo", "fade_in", 10, 100),
    )
    check(
        "verify_behavior: missing object fails (no raise)",
        not Behaviors.verify_behavior("nope_geo", "fade_in", 10, 100),
    )

    # exact mode via a custom template dir: keys must sit at the modelled anchor
    tmpl_dir = os.path.join(TEMP, "templates")
    os.makedirs(tmpl_dir, exist_ok=True)
    with open(os.path.join(tmpl_dir, "exact_in.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "description": "exact-mode fade",
                "kind": ["scene"],
                "verify": {"mode": "exact"},
                "attributes": {
                    "visibility": {
                        "in": {
                            "anchor": "start",
                            "offset": 0,
                            "duration": 15,
                            "values": [0.0, 1.0],
                            "tangent": "linear",
                        }
                    }
                },
            },
            f,
        )
    from pathlib import Path

    sp = Path(tmpl_dir)
    check(
        "verify_behavior: exact mode passes at the template anchor",
        Behaviors.verify_behavior("fade_geo", "exact_in", 10, 100, search_path=sp),
    )
    check(
        "verify_behavior: exact mode honours anchor_override (end-anchored keys)",
        Behaviors.verify_behavior(
            "plain_geo", "exact_in", 10, 100, search_path=sp, anchor_override=1.0
        ),
    )
    check(
        "verify_behavior: exact mode fails when the anchor is modelled wrong",
        not Behaviors.verify_behavior("plain_geo", "exact_in", 10, 100, search_path=sp),
    )

    # ---- apply_audio_clip + _verify_audio_clip + compute_duration ---------
    wav = os.path.join(TEMP, "clip.wav")
    _write_wav(wav, seconds=1.0)  # 24 frames @ 24 fps

    Behaviors.apply_audio_clip("vo", 30, 80, source_path=wav)
    info = AudioUtils.get_clip("vo")
    check(
        "apply_audio_clip: creates the VSE strip at the shot start",
        info is not None and info["frame_start"] == 30,
        f"{info}",
    )
    Behaviors.apply_audio_clip("vo", 40, 90, source_path="")
    info = AudioUtils.get_clip("vo")
    check(
        "apply_audio_clip: idempotent — existing strip moved, not duplicated",
        info is not None
        and info["frame_start"] == 40
        and AudioUtils.get_clip("vo.001") is None,
        f"{info}",
    )
    Behaviors.apply_audio_clip("vo", 50, 50, source_path=wav)
    check(
        "apply_audio_clip: non-positive range is skipped",
        AudioUtils.get_clip("vo")["frame_start"] == 40,
    )
    Behaviors.apply_audio_clip("vo_nosrc", 10, 20)
    check(
        "apply_audio_clip: no strip + no source_path -> nothing created",
        AudioUtils.get_clip("vo_nosrc") is None,
    )
    check(
        "_verify_audio_clip: true at the placed start",
        Behaviors._verify_audio_clip("vo", 40, 90),
    )
    check(
        "_verify_audio_clip: false at a different start",
        not Behaviors._verify_audio_clip("vo", 30, 90),
    )
    check(
        "_verify_audio_clip: false when the strip overruns the shot end",
        not Behaviors._verify_audio_clip("vo", 40, 50),
    )
    check(
        "verify_behavior: audio_clip mode routes to the strip check",
        Behaviors.verify_behavior("vo", "set_clip", 40, 90)
        and not Behaviors.verify_behavior("vo", "set_clip", 30, 90),
    )

    frames, _ = Behaviors._audio_duration_frames(wav, 24.0)
    check(
        "_audio_duration_frames: 1 s wav @ 24 fps = 24 frames",
        frames == 24.0,
        f"{frames}",
    )
    check(
        "_audio_duration_frames: unreadable path -> 0",
        Behaviors._audio_duration_frames(os.path.join(TEMP, "missing.wav"), 24.0)[0]
        == 0.0,
    )
    check(
        "_track_source_path: resolves a placed strip's file",
        os.path.normcase(Behaviors._track_source_path("vo")) == os.path.normcase(wav),
        f"{Behaviors._track_source_path('vo')}",
    )

    src_entry = SimpleNamespace(
        name="x", kind="audio", behaviors=["set_clip"], source_path=wav
    )
    check(
        "compute_duration: from_source template probes the source file",
        Behaviors.compute_duration([src_entry], fallback=30, fps=24.0) == 24.0,
        f"{Behaviors.compute_duration([src_entry], fallback=30, fps=24.0)}",
    )
    strip_entry = SimpleNamespace(
        name="vo", kind="audio", behaviors=["set_clip"], source_path=""
    )
    check(
        "compute_duration: no source_path resolves via the placed strip (scene fps)",
        Behaviors.compute_duration([strip_entry], fallback=30) == 24.0,
        f"{Behaviors.compute_duration([strip_entry], fallback=30)}",
    )
    none_entry = SimpleNamespace(
        name="ghost", kind="audio", behaviors=["set_clip"], source_path=""
    )
    check(
        "compute_duration: unresolvable audio falls back",
        Behaviors.compute_duration([none_entry], fallback=30) == 30,
    )
    check(
        "compute_duration: template-only entry uses phase durations",
        Behaviors.compute_duration([{"behavior": "fade_in"}], fallback=30) == 15.0,
    )

    # ---- apply_to_shots: anchors distributed, audio-first, failures recorded
    store = BlenderShotStore()
    s1 = store.define_shot("S1", 100, 200, objects=["both_geo"])
    s1.metadata["behaviors"] = [
        {"name": "both_geo", "behavior": "fade_in", "kind": "scene"},
        {"name": "both_geo", "behavior": "fade_out", "kind": "scene"},
        {"name": "vo2", "behavior": "set_clip", "kind": "audio", "source_path": wav},
        {"name": "nope_geo", "behavior": "fade_in", "kind": "scene"},
    ]
    res = Behaviors.apply_to_shots([s1], apply_fn=Behaviors.apply_behavior)
    op = [(t, v) for t, v, _ in keys("both_geo", OP)]
    check(
        "apply_to_shots: 2 behaviors distribute to start (fade_in) and end (fade_out)",
        op == [(100.0, 0.0), (115.0, 1.0), (185.0, 1.0), (200.0, 0.0)],
        f"{op}",
    )
    check(
        "apply_to_shots: audio entry placed as a strip at the shot start",
        (AudioUtils.get_clip("vo2") or {}).get("frame_start") == 100,
        f"{AudioUtils.get_clip('vo2')}",
    )
    check(
        "apply_to_shots: applied records carry object/behavior/shot",
        {(r["object"], r["behavior"], r["shot"]) for r in res["applied"]}
        == {
            ("both_geo", "fade_in", "S1"),
            ("both_geo", "fade_out", "S1"),
            ("vo2", "set_clip", "S1"),
        },
        f"{res['applied']}",
    )
    check(
        "apply_to_shots: missing object is skipped silently (assess surfaces it)",
        not any(r["object"] == "nope_geo" for r in res["applied"] + res["skipped"]),
        f"{res}",
    )
    res2 = Behaviors.apply_to_shots([s1], apply_fn=Behaviors.apply_behavior)
    check(
        "apply_to_shots: rebuild skips keyed objects + placed strips (no re-key)",
        {r["object"] for r in res2["skipped"]} == {"both_geo", "vo2"}
        and not res2["applied"]
        and [(t, v) for t, v, _ in keys("both_geo", OP)] == op,
        f"{res2}",
    )

    def _boom(obj, behavior, start, end, source_path="", anchor_override=None):
        raise RuntimeError("locked attribute")

    s2 = store.define_shot("S2", 300, 400, objects=["plain_geo"])
    s2.metadata["behaviors"] = [
        {"name": "fade_geo", "behavior": "fade_in", "kind": "scene"}
    ]
    res3 = Behaviors.apply_to_shots([s2], apply_fn=_boom)
    check(
        "apply_to_shots: applier failure recorded, batch continues",
        len(res3["failed"]) == 1
        and res3["failed"][0]["error"] == "locked attribute"
        and res3["failed"][0]["object"] == "fade_geo",
        f"{res3}",
    )
    s2.locked = True
    res4 = Behaviors.apply_to_shots([s2], apply_fn=Behaviors.apply_behavior)
    check(
        "apply_to_shots: locked shot untouched",
        res4 == {"applied": [], "skipped": [], "failed": []},
        f"{res4}",
    )
    # legacy 3-arg seams still bind
    seen = []
    Behaviors.apply_to_shots(
        [s1],
        apply_fn=lambda o, b, s, e: seen.append((o, b)),
        exists_fn=lambda n: True,
        has_keys_fn=lambda o, s, e: False,
    )
    check(
        "apply_to_shots: legacy 3-arg apply/exists/has_keys seams bind",
        ("both_geo", "fade_in") in seen and ("nope_geo", "fade_in") in seen,
        f"{seen}",
    )

    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_checks()
    except Exception as e:  # pragma: no cover
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]
    finally:
        import shutil

        shutil.rmtree(TEMP, ignore_errors=True)

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
