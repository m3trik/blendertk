# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest ``range_resolver`` facade test (mirror of mayatk's).

bpy-only suite: the facade's only job is to inject the Blender-bound
``Behaviors.compute_duration`` as the default ``duration_fn``, so an audio
step in the panel table is sized to its clip's real length (probed from the
source file against the scene FPS) before it is ever built — and an explicit
``duration_fn`` still wins.  Also pins the ``prune_to_top_boundaries``
re-export.

Run headless (fresh instance — session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_shot_manifest_range_resolver.py
"""

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

TEMP = os.path.join(HERE, "temp_tests", "shot_manifest_range_resolver")


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
    from pythontk import BuilderObject, BuilderStep
    from pythontk.core_utils.engines.shots.manifest.range_resolver import (
        RangeResolver as _PyRangeResolver,
    )

    from blendertk.anim_utils.shots.shot_manifest import range_resolver
    from blendertk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver

    bpy.context.scene.render.fps = 24
    bpy.context.scene.render.fps_base = 1.0

    os.makedirs(TEMP, exist_ok=True)
    wav = os.path.join(TEMP, "clip.wav")
    with wave.open(wav, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(96000)))  # 2 s

    steps = [
        BuilderStep(
            "V01",
            "V",
            "vo",
            "",
            [
                BuilderObject(
                    "vo", behaviors=["set_clip"], kind="audio", source_path=wav
                )
            ],
        ),
        BuilderStep(
            "A01", "A", "fade", "", [BuilderObject("g", behaviors=["fade_in"])]
        ),
    ]

    def _resolve(**kw):
        args = dict(
            steps=steps,
            user_ranges={},
            gap_starts=[],
            gap_end_map={},
            gap=1,
            use_selected_keys=False,
            last_resolved=[],
        )
        args.update(kw)
        return RangeResolver.resolve_ranges(**args)

    resolved = _resolve()
    by_id = {r[0]: r for r in resolved}
    check(
        "facade: audio step sized to its source clip (2 s @ 24 fps = 48 frames)",
        "V01" in by_id and by_id["V01"][2] - by_id["V01"][1] == 48.0,
        f"{resolved}",
    )
    check(
        "facade: template-driven step still sized by phase durations (fade_in = 15)",
        "A01" in by_id and by_id["A01"][2] - by_id["A01"][1] == 15.0,
        f"{resolved}",
    )

    # Pure engine default (no Blender probe) cannot size the clip -> fallback.
    pure = _PyRangeResolver.resolve_ranges(
        steps, {}, [], {}, 1, False, [], default_duration=0
    )
    pure_by_id = {r[0]: r for r in pure}
    check(
        "facade differs from the pure default: pure engine falls back (30)",
        pure_by_id["V01"][2] - pure_by_id["V01"][1] == 30.0,
        f"{pure}",
    )

    explicit = _resolve(duration_fn=lambda objs, fallback=30: 7.0)
    check(
        "facade: an explicit duration_fn wins over the injected one",
        all(r[2] - r[1] == 7.0 for r in explicit),
        f"{explicit}",
    )

    # default_duration mode: audio still consults the Blender probe
    uniform = _resolve(default_duration=200)
    u_by_id = {r[0]: r for r in uniform}
    check(
        "facade: use_default mode — audio sized to clip, template step uniform",
        u_by_id["V01"][2] - u_by_id["V01"][1] == 48.0
        and u_by_id["A01"][2] - u_by_id["A01"][1] == 200.0,
        f"{uniform}",
    )

    check(
        "prune_to_top_boundaries re-exported from the engine class",
        range_resolver.prune_to_top_boundaries
        is _PyRangeResolver.prune_to_top_boundaries,
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
