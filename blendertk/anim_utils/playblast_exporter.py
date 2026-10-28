# !/usr/bin/python
# coding=utf-8
"""Playblast capture and export for Blender -- mirror of mayatk's
``anim_utils.playblast_exporter``.

Blender's half of :class:`pythontk.SequenceExporter`: the pixels come from
Blender's OpenGL viewport render (``render.opengl``), the timeline from the
scene, and the shared plan captures each image format once and isolates
per-target failures. This module supplies:

- ``capture_sequence`` -- the viewport rendered to a numbered image sequence
  (PNG, JPEG, TIFF, TGA, OpenEXR);
- ``capture_still`` -- one frame to an exact filepath;
- ``capture_movie`` -- the ``native`` movie targets (MP4, MOV, AVI), written by
  Blender's own FFmpeg in the same pass, so no external ffmpeg is needed (Maya's
  MP4 / MOV are ``encode`` targets: encoded from its capture by the shared plan).

Every scene and viewport setting a capture borrows (frame range, camera,
resolution, output format, overlays) is restored afterwards. Extend by
registering a new :class:`pythontk.ExportTarget` in
``PlayblastExporter.TARGETS``: UIs build their pickers from
``available_targets()``.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

import pythontk as ptk
from pythontk import CaptureResult, ExportTarget

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.core_utils._core_utils import CoreUtils


class PlayblastExporter(ptk.SequenceExporter):
    """Viewport capture and export. Mirror of mayatk's ``PlayblastExporter``.

    Instance attributes hold capture *defaults*; every public method accepts
    per-call overrides. Frame ranges are resolved at call time.

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

    #: ``image_settings.file_format`` per capture image format.
    IMAGE_FORMATS: Dict[str, str] = {
        "png": "PNG",
        "jpg": "JPEG",
        "tif": "TIFF",
        "tga": "TARGA",
        "exr": "OPEN_EXR",
    }

    #: Frame-range modes: the shared ``custom`` plus the three the scene's
    #: timeline answers (mayatk's).
    RANGE_MODES: Tuple[str, ...] = ("playback", "animation", "current", "custom")

    #: An :meth:`export` naming no range plays back the timeline, as mayatk's.
    DEFAULT_RANGE_MODE: str = "playback"

    def __init__(
        self,
        camera=None,
        width: int = 1920,
        height: int = 1080,
        percent: int = 100,
        quality: int = 100,
        off_screen: bool = True,
        show_ornaments: bool = True,
        frame_padding: int = 4,
        include_audio: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            width=width,
            height=height,
            quality=quality,
            frame_padding=frame_padding,
            include_audio=include_audio,
            **kwargs,
        )
        self.camera = camera
        self.percent = int(percent)
        self.off_screen = bool(off_screen)
        self.show_ornaments = bool(show_ornaments)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def _scene():
        """The window's scene (windowless, ``bpy.context`` holds the default one)."""
        import bpy

        with CoreUtils.window_context_override():
            return bpy.context.scene

    @staticmethod
    def scene_name() -> str:
        """Basename of the open .blend without extension; ``"playblast"`` while
        unsaved, as mayatk's."""
        import bpy

        name = os.path.splitext(os.path.basename(bpy.data.filepath or ""))[0]
        return name or "playblast"

    @classmethod
    def scene_fps(cls) -> float:
        """The scene frame rate (``fps / fps_base``)."""
        render = cls._scene().render
        return render.fps / (render.fps_base or 1.0)

    def sequence_name(self) -> str:
        return self.scene_name()

    def sequence_fps(self) -> float:
        return self.scene_fps()

    @classmethod
    def _frame_range_for_mode(cls, mode: str) -> Tuple[float, float]:
        """The scene's ``(start, end)`` for a timeline range mode: ``playback``
        (the preview range when it is on, else the scene range),
        ``animation`` (the scene range), ``current`` (the current frame)."""
        scene = cls._scene()
        if mode == "current":
            return scene.frame_current, scene.frame_current
        if mode == "playback" and scene.use_preview_range:
            return scene.frame_preview_start, scene.frame_preview_end
        return scene.frame_start, scene.frame_end

    def _sound_source(self) -> Optional[str]:
        """The scene's own sound: a movie target muxes it (AAC)."""
        return "scene"

    # ------------------------------------------------------------------
    # The capture
    # ------------------------------------------------------------------
    @staticmethod
    def _opengl(**kwargs):
        """Blender's OpenGL viewport render (``render.opengl``): the one call a
        capture needs a GPU context for, so a headless run substitutes it."""
        import bpy

        return bpy.ops.render.opengl(**kwargs)

    def _resolve_camera(self, camera):
        """*camera* (or the default) as a camera object; ``None`` for the view."""
        import bpy

        camera = camera if camera is not None else self.camera
        if camera is None:
            return None
        obj = bpy.data.objects.get(camera) if isinstance(camera, str) else camera
        if obj is None or obj.type != "CAMERA":
            raise ValueError(f"Camera '{camera}' does not exist.")
        return obj

    @contextmanager
    def _capture_scene(
        self, camera, filepath, start, end, file_format, container=None, codec=None,
        audio=False,
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
            render.resolution_x, render.resolution_y = self.width, self.height
            render.resolution_percentage = self.percent
            render.use_file_extension = True
            render.filepath = filepath
            AnimUtils.configure_render_output(
                scene,
                file_format=file_format,
                container=container,
                codec=codec,
                quality=self.quality,
            )
            if file_format == "FFMPEG":
                ff.audio_codec = "AAC" if audio else "NONE"
            if camera is not None:
                scene.camera = camera
            # Start first: Blender pushes the end along when the start passes it.
            scene.frame_start, scene.frame_end = int(start), int(end)
            if space is not None:
                space.overlay.show_overlays = self.show_ornaments
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
    def _abspath(path: str) -> str:
        """*path* absolute, a ``//`` (.blend-relative) one included."""
        import bpy

        return ptk.format_path(os.path.abspath(bpy.path.abspath(path)))

    def capture_sequence(
        self,
        directory: str,
        prefix: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera=None,
        image_format: str = "png",
        **overrides: Any,
    ) -> CaptureResult:
        """Capture the frame range as a numbered image sequence
        (``<prefix>.<frame>.<ext>``, real scene frame numbers). Raises
        RuntimeError when Blender writes fewer frames than asked."""
        file_format = self._image_format(image_format)
        directory = self._abspath(directory)
        os.makedirs(directory, exist_ok=True)
        prefix = prefix or self.scene_name()
        start, end = self.resolve_frame_range("playback", start, end)
        padding = int(overrides.get("frame_padding", self.frame_padding))
        # Frames an earlier, wider run left would pass the count check below.
        self._remove_frames(directory, prefix, image_format)
        cam = self._resolve_camera(camera)
        path = os.path.join(directory, f"{prefix}.") + "#" * max(1, padding)
        with self._capture_scene(cam, path, start, end, file_format) as override:
            self._render(override, cam)
        frames = self._collect_frames(directory, prefix, image_format, start, end)
        expected = end - start + 1
        if len(frames) < expected:
            raise RuntimeError(
                f"The viewport render wrote {len(frames)}/{expected} frames under "
                f"{directory!r} (prefix {prefix!r})."
            )
        return CaptureResult(
            directory=directory,
            prefix=prefix,
            image_format=image_format,
            start=start,
            end=end,
            padding=padding,
            frames=frames,
            fps=self.scene_fps(),
        )

    def capture_still(
        self,
        filepath: str,
        frame: Optional[int] = None,
        camera=None,
        image_format: str = "png",
        **overrides: Any,
    ) -> str:
        """Capture one frame (default: the current one) to an exact filepath:
        a one-frame sequence (whose ``#`` naming is exact), moved into place."""
        file_format = self._image_format(image_format)
        filepath = self._abspath(filepath)
        directory = os.path.dirname(filepath)
        os.makedirs(directory, exist_ok=True)
        frame = int(frame if frame is not None else self._scene().frame_current)
        prefix = f"{os.path.splitext(os.path.basename(filepath))[0]}_still"
        self._remove_frames(directory, prefix, image_format)
        cam = self._resolve_camera(camera)
        path = os.path.join(directory, f"{prefix}.####")
        with self._capture_scene(cam, path, frame, frame, file_format) as override:
            self._render(override, cam)
        frames = self._collect_frames(directory, prefix, image_format, frame, frame)
        if not frames:
            raise RuntimeError(f"Still capture failed; no file at {filepath!r}.")
        os.replace(frames[0], filepath)
        self._notify(f"Still frame captured: {filepath}")
        return filepath

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
        """Write a movie with Blender's own FFmpeg in one OpenGL pass.

        The path is used as given: it ends in the container's extension, so
        Blender appends no frame range to it (a version that does anyway has
        its file moved into place).
        """
        filepath = self._abspath(filepath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        start, end = self.resolve_frame_range("playback", start, end)
        cam = self._resolve_camera(camera)
        if os.path.exists(filepath):
            os.remove(filepath)
        started = time.time()
        with self._capture_scene(
            cam, filepath, start, end, "FFMPEG", container, codec, audio=bool(sound)
        ) as override:
            self._render(override, cam)
        if not self._is_valid_file(filepath):
            self._adopt_movie(filepath, started)
        if not self._is_valid_file(filepath):
            raise RuntimeError(f"Blender wrote no movie at {filepath!r}.")
        self._notify(f"Playblast movie created: {filepath}")
        return filepath

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

    def export(self, output_dir: str, *args: Any, **kwargs: Any) -> List[Any]:
        """:meth:`pythontk.SequenceExporter.export`, *output_dir* allowed to be
        relative to the open .blend (``//``)."""
        return super().export(self._abspath(output_dir), *args, **kwargs)

    # ------------------------------------------------------------------
    # Host-only targets
    # ------------------------------------------------------------------
    def _export_extra_target(
        self,
        spec: ExportTarget,
        output_dir: str,
        name: str,
        start: int,
        end: int,
        camera,
        sound: Optional[str],
        overrides: Dict[str, Any],
    ) -> Union[str, List[str]]:
        """Produce the ``native`` movies for the shared plan (the OCP seam
        :class:`pythontk.SequenceExporter` leaves open)."""
        if spec.kind == "native":
            return self.capture_movie(
                os.path.join(output_dir, f"{name}.{spec.extension}"),
                container=spec.native_format,
                codec=spec.native_compression,
                start=start,
                end=end,
                camera=camera,
                sound=sound,
            )
        return super()._export_extra_target(
            spec,
            output_dir=output_dir,
            name=name,
            start=start,
            end=end,
            camera=camera,
            sound=sound,
            overrides=overrides,
        )
