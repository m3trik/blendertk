"""blendertk.PlayblastExporter (mirror of mayatk's) -- headless test.
Run: blender --background --factory-startup --python blendertk/tests/test_playblast_exporter.py

``PlayblastExporter`` is ``ptk.SequenceExporter`` bound to Blender's host adapter
(``BlenderPlayblastHost``). ``render.opengl`` needs a GPU context ``--background``
does not have, so the one call the capture makes to it
(``BlenderPlayblastHost._opengl``) is substituted by a writer that records what
the render would read (the scene's frame range, output path and format, the
overlays) and writes the files Blender would. Everything else -- the plan, the
scene borrow and its restore, the file naming, the registry -- runs for real.
"""

import os
import sys
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import pythontk as ptk
    from blendertk.anim_utils.playblast_exporter import (
        BlenderPlayblastHost,
        PlayblastExporter,
    )

    scene = bpy.context.scene
    seen = []
    _EXT = {"PNG": "png", "JPEG": "jpg", "TIFF": "tif", "TARGA": "tga", "OPEN_EXR": "exr"}

    def fake_opengl(**kwargs):
        """Write what Blender's OpenGL render writes for the scene's settings."""
        render = scene.render
        seen.append({
            "kwargs": kwargs,
            "path": render.filepath,
            "range": (scene.frame_start, scene.frame_end),
            "format": render.image_settings.file_format,
            "size": (render.resolution_x, render.resolution_y),
            "percent": render.resolution_percentage,
            "audio": render.ffmpeg.audio_codec,
            "container": (render.ffmpeg.format, render.ffmpeg.codec),
            "camera": scene.camera.name if scene.camera else None,
        })
        if render.image_settings.file_format == "FFMPEG":
            with open(render.filepath, "wb") as f:
                f.write(b"movie")
            return {"FINISHED"}
        head, hashes = render.filepath.rstrip("#"), len(render.filepath) - len(render.filepath.rstrip("#"))
        for frame in range(scene.frame_start, scene.frame_end + 1):
            ext = _EXT[render.image_settings.file_format]
            with open(f"{head}{frame:0{hashes}d}.{ext}", "wb") as f:
                f.write(b"frame")
        return {"FINISHED"}

    BlenderPlayblastHost._opengl = staticmethod(fake_opengl)

    # ---- the registry: Blender's own movies, no external encode
    names = [n for n, _ in PlayblastExporter.available_targets()]
    check("TARGETS: movies, sequences, OpenEXR and the still",
          names[:3] == ["mp4", "mov", "avi"]
          and {"png_sequence", "jpg_sequence", "exr_sequence", "still"} <= set(names), str(names))
    check("...movies are native (Blender's FFmpeg), none encoded",
          all(t.kind != "encode" for t in PlayblastExporter.TARGETS.values())
          and PlayblastExporter.TARGETS["mp4"].kind == "native")
    check("scene_name is 'playblast' while unsaved", PlayblastExporter.scene_name() == "playblast")
    import blendertk as btk

    check("btk.PlayblastExporter is the root name",
          getattr(btk, "PlayblastExporter", None) is PlayblastExporter)
    check("...the shared exporter bound to Blender's host adapter",
          isinstance(PlayblastExporter.HOST, BlenderPlayblastHost)
          and issubclass(PlayblastExporter, ptk.SequenceExporter))

    # ---- ranges
    scene.frame_start, scene.frame_end, scene.frame_current = 1, 20, 7
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 5, 9
    check("playback: the preview range when it is on",
          PlayblastExporter.resolve_frame_range("playback") == (5, 9))
    check("animation: the scene range", PlayblastExporter.resolve_frame_range("animation") == (1, 20))
    check("current: the current frame", PlayblastExporter.resolve_frame_range("current") == (7, 7))
    scene.use_preview_range = False
    check("playback without a preview range: the scene range",
          PlayblastExporter.resolve_frame_range("playback") == (1, 20))

    # ---- one export: a sequence, a movie and a still, the scene left as it was
    cam = bpy.data.objects.new("ShotCam", bpy.data.cameras.new("ShotCam"))
    scene.collection.objects.link(cam)
    scene.render.resolution_x, scene.render.resolution_y = 640, 480
    scene.render.filepath = "//keep_me"
    scene.render.image_settings.file_format = "PNG"
    camera_before = scene.camera
    tmp = ptk.TempArtifacts.scoped("btk_playblast_test")
    try:
        out = tmp.dir_path()
        exporter = PlayblastExporter(camera="ShotCam", width=320, height=200, include_audio=True)
        results = exporter.export(out, name="shot", targets=["png_sequence", "mp4", "still"],
                                  range_mode="custom", start=3, end=5)
        by = {r.target: r for r in results}
        check("export: every target ok", all(r.ok for r in results),
              str([(r.target, r.error) for r in results]))
        check("...the sequence keeps real frame numbers in <name>_png/",
              [os.path.basename(f) for f in by["png_sequence"].output]
              == ["shot.0003.png", "shot.0004.png", "shot.0005.png"])
        check("...the movie is written as named, by Blender's FFmpeg, with the scene's sound",
              os.path.basename(by["mp4"].output) == "shot.mp4" and os.path.isfile(by["mp4"].output)
              and any(s["format"] == "FFMPEG" and s["audio"] == "AAC" for s in seen))
        check("...the still is its own file", os.path.basename(by["still"].output) == "shot.png"
              and os.path.isfile(by["still"].output))
        check("...the render ran through the camera at the capture size",
              all(s["camera"] == "ShotCam" and s["size"] == (320, 200)
                  and s["kwargs"].get("view_context") is False for s in seen))
        check("...and the scene is left as it was",
              (scene.frame_start, scene.frame_end, scene.frame_current) == (1, 20, 7)
              and scene.render.filepath == "//keep_me"
              and (scene.render.resolution_x, scene.render.resolution_y) == (640, 480)
              and scene.render.image_settings.file_format == "PNG"
              and scene.camera == camera_before)
        bad = PlayblastExporter(camera="NoSuchCam").export(out, name="bad", targets=["png_sequence"],
                                                           range_mode="custom", start=1, end=2)
        check("a missing camera fails its target, not the run",
              not bad[0].ok and "NoSuchCam" in bad[0].error, str(bad[0].error))

        # ---- the default range, the view, padding, OpenEXR and the other movies
        scene.use_preview_range = True
        del seen[:]
        out2 = os.path.join(out, "second")
        results = PlayblastExporter(percent=50, show_ornaments=False, frame_padding=3).export(
            out2, targets=["exr_sequence", "avi", "mov"])
        by = {r.target: r for r in results}
        check("a default export plays back the preview range, named after the scene",
              all(r.ok for r in results)
              and [os.path.basename(f) for f in by["exr_sequence"].output]
              == [f"playblast.{n:03d}.exr" for n in range(5, 10)]
              and all(s["range"] == (5, 9) for s in seen),
              str([(r.target, r.output, r.error) for r in results]))
        check("...each movie by Blender's FFmpeg in its container, without sound unless asked",
              [s["container"] for s in seen if s["format"] == "FFMPEG"]
              == [("AVI", "FFV1"), ("QUICKTIME", "H264")]
              and all(s["audio"] == "NONE" for s in seen if s["format"] == "FFMPEG")
              and sorted(os.listdir(out2)) == ["playblast.avi", "playblast.mov", "playblast_exr"])
        check("...through the view at the capture scale",
              all(s["kwargs"].get("view_context") is True and s["percent"] == 50
                  and s["size"] == (1920, 1080) for s in seen))
        scene.use_preview_range = False

        # ---- a still: the current frame, one file, exactly where asked
        del seen[:]
        still = PlayblastExporter().capture_still(os.path.join(out, "st", "one.jpg"),
                                                   image_format="jpg")
        check("a still is the current frame, moved to exactly its path",
              os.path.basename(still) == "one.jpg"
              and sorted(os.listdir(os.path.dirname(still))) == ["one.jpg"]
              and [s["range"] for s in seen] == [(7, 7)]
              and seen[0]["format"] == "JPEG", str((still, seen)))
        try:
            PlayblastExporter().capture_sequence(os.path.join(out, "bmp"), image_format="bmp")
            refused = False
        except ValueError as exc:
            refused = "bmp" in str(exc)
        check("an image format Blender cannot write is refused", refused)
    finally:
        tmp.cleanup()

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
