# !/usr/bin/python
# coding=utf-8
"""Playblast capture and export for Blender -- mirror of mayatk's
``anim_utils.playblast_exporter``.

The plan (each image format captured once, per-target failures isolated), the
target vocabulary and the frame bookkeeping are :class:`pythontk.SequenceExporter`'s,
shared with mayatk. What reaches Blender is its host adapter,
:class:`BlenderPlayblastHost` (a :class:`pythontk.SequenceHost`):

- ``capture_frames`` -- Blender's OpenGL viewport render (``render.opengl``) to
  a numbered image sequence (PNG, JPEG, TIFF, TGA, OpenEXR);
- ``capture_movie`` -- the ``native`` movie targets (MP4, MOV, AVI), written by
  Blender's own FFmpeg in the same pass, so no external ffmpeg is needed (Maya's
  MP4 / MOV are ``encode`` targets: encoded from its capture by the shared plan);
- the scene's range modes, name and rate, its sound, and ``//`` paths. A still
  is the shared one-frame sequence moved into place.

Every scene and viewport setting a capture borrows (frame range, camera,
resolution, output format, overlays) is restored afterwards.
:class:`PlayblastExporter` is the shared class bound to it: configuration plus
Blender's own ``capture_movie``. Extend by registering a new
:class:`pythontk.ExportTarget` in ``PlayblastExporter.TARGETS``: UIs build
their pickers from ``available_targets()``.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

import pythontk as ptk
from pythontk import ExportTarget

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.core_utils._core_utils import CoreUtils


class BlenderPlayblastHost(ptk.PureSequenceHost):
    """What the sequence exporter asks of Blender (:class:`pythontk.SequenceHost`):
    the OpenGL viewport render, the scene's timeline and sound, and Blender's
    own FFmpeg for the movie targets."""

    #: ``image_settings.file_format`` per capture image format.
    IMAGE_FORMATS: Dict[str, str] = {
        "png": "PNG",
        "jpg": "JPEG",
        "tif": "TIFF",
        "tga": "TARGA",
        "exr": "OPEN_EXR",
    }

    # ------------------------------------------------------------------
    # The scene
    # ------------------------------------------------------------------
    @staticmethod
    def _scene():
        """The window's scene (windowless, ``bpy.context`` holds the default one)."""
        import bpy

        with CoreUtils.window_context_override():
            return bpy.context.scene

    def frame_range(self, mode: str) -> Tuple[float, float]:
        """The scene's ``(start, end)`` for a timeline range mode: ``playback``
        (the preview range when it is on, else the scene range),
        ``animation`` (the scene range), ``current`` (the current frame)."""
        scene = self._scene()
        if mode == "current":
            return scene.frame_current, scene.frame_current
        if mode == "playback" and scene.use_preview_range:
            return scene.frame_preview_start, scene.frame_preview_end
        return scene.frame_start, scene.frame_end

    @staticmethod
    def sequence_name() -> str:
        """Basename of the open .blend without extension; ``"playblast"`` while
        unsaved, as mayatk's."""
        import bpy

        name = os.path.splitext(os.path.basename(bpy.data.filepath or ""))[0]
        return name or "playblast"

    def sequence_fps(self) -> float:
        """The scene frame rate (``fps / fps_base``)."""
        render = self._scene().render
        return render.fps / (render.fps_base or 1.0)

    @staticmethod
    def sound_source() -> Optional[str]:
        """The scene's own sound: a movie target muxes it (AAC)."""
        return "scene"

    @staticmethod
    def resolve_path(path: str) -> str:
        """*path*, a ``//`` (.blend-relative) one included, as the filesystem reads it."""
        import bpy

        return bpy.path.abspath(path)

    # ------------------------------------------------------------------
    # The capture
    # ------------------------------------------------------------------
    def capture_frames(
        self,
        exporter: ptk.SequenceExporter,
        directory: str,
        prefix: str,
        start: int,
        end: int,
        camera: Any,
        image_format: str,
        padding: int,
        overrides: Dict[str, Any],
    ) -> None:
        """The OpenGL render of ``start..end`` to ``<prefix>.<frame>.<ext>``
        (Blender's ``#`` frame placeholders, *padding* of them)."""
        file_format = self._image_format(image_format)
        cam = self._resolve_camera(camera)
        path = os.path.join(directory, f"{prefix}.") + "#" * max(1, padding)
        with self._capture_scene(exporter, cam, path, start, end, file_format) as override:
            self._render(override, cam)

    def capture_movie(
        self,
        exporter: ptk.SequenceExporter,
        filepath: str,
        fmt: str,
        compression: str,
        start: int,
        end: int,
        camera: Any,
        sound: Optional[str],
        overrides: Dict[str, Any],
    ) -> str:
        """A movie written by Blender's own FFmpeg in one OpenGL pass (*fmt* /
        *compression*: its ``ffmpeg.format`` / ``ffmpeg.codec``).

        The path is used as given: it ends in the container's extension, so
        Blender appends no frame range to it (a version that does anyway has
        its file moved into place).
        """
        cam = self._resolve_camera(camera)
        if os.path.exists(filepath):
            os.remove(filepath)
        started = time.time()
        with self._capture_scene(
            exporter, cam, filepath, start, end, "FFMPEG", fmt, compression,
            audio=bool(sound),
        ) as override:
            self._render(override, cam)
        if not (os.path.isfile(filepath) and os.path.getsize(filepath) > 0):
            self._adopt_movie(filepath, started)
        return filepath

    @staticmethod
    def _opengl(**kwargs):
        """Blender's OpenGL viewport render (``render.opengl``): the one call a
        capture needs a GPU context for, so a headless run substitutes it."""
        import bpy

        return bpy.ops.render.opengl(**kwargs)

    @staticmethod
    def _resolve_camera(camera):
        """*camera* as a camera object; ``None`` for the view."""
        import bpy

        if camera is None:
            return None
        obj = bpy.data.objects.get(camera) if isinstance(camera, str) else camera
        if obj is None or obj.type != "CAMERA":
            raise ValueError(f"Camera '{camera}' does not exist.")
        return obj

    @contextmanager
    def _capture_scene(
        self, exporter, camera, filepath, start, end, file_format, container=None,
        codec=None, audio=False,
    ):
        """Borrow what a capture needs from the scene and the 3D viewport, and
        restore every one of them; yields the context override the OpenGL
        render runs under."""
        scene = self._scene()
        render = scene.render
        imgs, ff = render.image_settings, render.ffmpeg
        has_media_type = hasattr(imgs, "media_type")  # Blender 5.x movie/image gate
        view3d = CoreUtils.get_view3d_context()
        space = view3d["area"].spaces.active if view3d else None
        snap = {
            "frame": scene.frame_current,
            "fs": scene.frame_start,
            "fe": scene.frame_end,
            "cam": scene.camera,
            "rx": render.resolution_x,
            "ry": render.resolution_y,
            "pct": render.resolution_percentage,
            "path": render.filepath,
            "ext": render.use_file_extension,
            "media": imgs.media_type if has_media_type else None,
            "fmt": imgs.file_format,
            "mode": imgs.color_mode,
            "depth": imgs.color_depth,
            "q": imgs.quality,
            "ffmt": ff.format,
            "fcodec": ff.codec,
            "fcrf": ff.constant_rate_factor,
            "faudio": ff.audio_codec,
            "overlays": space.overlay.show_overlays if space is not None else None,
        }
        try:
            render.resolution_x, render.resolution_y = exporter.width, exporter.height
            render.resolution_percentage = exporter.percent
            render.use_file_extension = True
            render.filepath = filepath
            AnimUtils.configure_render_output(
                scene,
                file_format=file_format,
                container=container,
                codec=codec,
                quality=exporter.quality,
            )
            if file_format == "FFMPEG":
                ff.audio_codec = "AAC" if audio else "NONE"
            if camera is not None:
                scene.camera = camera
            # Start first: Blender pushes the end along when the start passes it.
            scene.frame_start, scene.frame_end = int(start), int(end)
            if space is not None:
                space.overlay.show_overlays = exporter.show_ornaments
            yield {k: v for k, v in view3d.items() if v is not None} if view3d else {}
        finally:
            scene.frame_start, scene.frame_end = snap["fs"], snap["fe"]
            scene.camera = snap["cam"]
            render.resolution_x, render.resolution_y = snap["rx"], snap["ry"]
            render.resolution_percentage = snap["pct"]
            render.filepath = snap["path"]
            render.use_file_extension = snap["ext"]
            if has_media_type:  # before file_format: it gates the valid values
                imgs.media_type = snap["media"]
            imgs.file_format = snap["fmt"]
            for prop in ("color_mode", "color_depth"):
                try:  # a format change may have narrowed them; put them back
                    setattr(imgs, prop, snap["mode" if prop == "color_mode" else "depth"])
                except TypeError:
                    pass
            imgs.quality = snap["q"]
            ff.format, ff.codec = snap["ffmt"], snap["fcodec"]
            ff.constant_rate_factor, ff.audio_codec = snap["fcrf"], snap["faudio"]
            if space is not None:
                space.overlay.show_overlays = snap["overlays"]
            scene.frame_set(snap["frame"])

    def _render(self, override, camera) -> None:
        """Run the OpenGL render over the scene's frame range. It reads the
        viewport it draws from CONTEXT: run it over the real VIEW_3D area,
        under a live window (the Qt-pump state has neither)."""
        import bpy

        with CoreUtils.window_context_override(), bpy.context.temp_override(**override):
            self._opengl(animation=True, write_still=False, view_context=camera is None)

    def _image_format(self, image_format: str) -> str:
        file_format = self.IMAGE_FORMATS.get(str(image_format).lower())
        if file_format is None:
            raise ValueError(
                f"Unknown image format {image_format!r}; expected one of "
                f"{sorted(self.IMAGE_FORMATS)}."
            )
        return file_format

    @staticmethod
    def _adopt_movie(filepath: str, since: float) -> None:
        """Move the movie this run wrote under a frame-range-suffixed name
        (``<name>0001-0250.mp4``) to *filepath*."""
        folder = os.path.dirname(filepath)
        stem, ext = os.path.splitext(os.path.basename(filepath))
        candidates = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.startswith(stem) and f.lower().endswith(ext.lower())
        ]
        fresh = [c for c in candidates if os.path.getmtime(c) >= since - 1]
        if fresh:
            os.replace(max(fresh, key=os.path.getmtime), filepath)


class PlayblastExporter(ptk.SequenceExporter):
    """Viewport capture and export. Mirror of mayatk's ``PlayblastExporter``.

    :class:`pythontk.SequenceExporter` bound to :class:`BlenderPlayblastHost`.
    Instance attributes hold capture *defaults*; every public method accepts
    per-call overrides. Frame ranges are resolved at call time, and a path
    may be relative to the open .blend (``//``).

    Parameters:
        camera: Default camera (object or name). ``None`` captures the 3D
            viewport's own view.
        width/height: Capture resolution.
        percent: The resolution percentage.
        quality: 0-100; the movie's FFmpeg rate factor and JPEG quality.
        off_screen: mayatk's; Blender's OpenGL render always draws offscreen.
        show_ornaments: Draw the viewport overlays (grid, gizmos, empties).
        frame_padding: Digits for image-sequence frame numbers.
        include_audio: Mux the scene's sound (its sound strips and speakers)
            into movie outputs.
    """

    HOST = BlenderPlayblastHost()

    #: Registry of exportable outputs: Blender's own movies, then the shared
    #: sequences, OpenEXR and the still. Movies are ``native``: Blender's FFmpeg
    #: writes them in the capture pass (``native_format`` / ``native_compression``
    #: are its ``ffmpeg.format`` / ``ffmpeg.codec``).
    TARGETS: Dict[str, ExportTarget] = {
        t.name: t
        for t in (
            ExportTarget(
                "mp4",
                "MP4 (H.264)",
                "native",
                extension="mp4",
                native_format="MPEG4",
                native_compression="H264",
            ),
            ExportTarget(
                "mov",
                "MOV (H.264)",
                "native",
                extension="mov",
                native_format="QUICKTIME",
                native_compression="H264",
            ),
            ExportTarget(
                "avi",
                "AVI (FFV1)",
                "native",
                extension="avi",
                native_format="AVI",
                native_compression="FFV1",
            ),
            *(
                t
                for t in ptk.SequenceExporter.TARGETS.values()
                if t.kind == "sequence"
            ),
            ExportTarget(
                "exr_sequence",
                "OpenEXR Sequence",
                "sequence",
                image_format="exr",
                extension="exr",
            ),
            *(t for t in ptk.SequenceExporter.TARGETS.values() if t.kind == "still"),
        )
    }

    #: ``image_settings.file_format`` per capture image format (the host's).
    IMAGE_FORMATS: Dict[str, str] = BlenderPlayblastHost.IMAGE_FORMATS

    #: Frame-range modes: the shared ``custom`` plus the three the scene's
    #: timeline answers (mayatk's).
    RANGE_MODES: Tuple[str, ...] = ("playback", "animation", "current", "custom")

    #: An :meth:`export` naming no range plays back the timeline, as mayatk's.
    DEFAULT_RANGE_MODE: str = "playback"

    def capture_movie(
        self,
        filepath: str,
        container: str = "MPEG4",
        codec: str = "H264",
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera=None,
        sound: Optional[str] = None,
        **overrides: Any,
    ) -> str:
        """Write a movie with Blender's own FFmpeg in one OpenGL pass
        (:meth:`BlenderPlayblastHost.capture_movie`)."""
        return self._capture_movie(
            filepath, container, codec, start, end, camera, sound, overrides
        )
