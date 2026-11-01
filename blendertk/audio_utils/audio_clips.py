# !/usr/bin/python
# coding=utf-8
"""Audio Clips: the Audio Clips panel's engine over Blender's Video Sequence Editor.

Mirror of mayatk's ``audio_utils.audio_clips.AudioClips`` by name: the operations
tentacle's shared ``slots/tools/audio_clips.py`` calls on either host, here over
:class:`~blendertk.audio_utils.AudioUtils`' sound-strip CRUD. The model is not
mirrored (see :mod:`blendertk.audio_utils._audio_utils` for why): a Maya track is
keyed on a carrier node and mixed into one composite WAV for the Time Slider,
while a Sequencer strip is registered and placed at once and plays as it is. So
placing a clip moves its strip (:meth:`AudioClips.key_track`), and mayatk's
composite, export, keying-option and legacy-schema operations have no twin here
-- the panel disables their controls (``tentacle/docs/parity_map.py``'s
``audio_clips_slots`` row has the reasons). Three are Blender's own: a strip's
trim (:meth:`AudioClips.get_trim`, :meth:`AudioClips.trim_track`) and fitting the
scene range to the strips (:meth:`AudioClips.sync_scene_range`).

``import bpy`` is deferred into the call bodies: the panel loads with no Blender
running, and every read then answers empty.
"""

from typing import List, Optional, Tuple

import pythontk as ptk

from blendertk.audio_utils._audio_utils import AudioUtils


class AudioClips(ptk.LoggingMixin):
    """The scene's sound clips, as the Audio Clips panel drives them (mirror of
    mayatk's ``AudioClips``; a clip is addressed by its strip name)."""

    ITEM_NOUN: str = "Clip"
    """What one entry is called here: a Sequencer sound strip (mayatk: ``"Track"``)."""

    PLACE_LABEL: str = "Move To Current Frame"
    """The panel's place action (:meth:`key_track`): a strip is one placement,
    so placing it moves it (mayatk keys another ON event)."""

    REVEAL_LABEL: str = "Reveal In Sequencer"
    """The panel's :meth:`reveal` action."""

    END_KEYS: bool = False
    """A strip's end is intrinsic (its sound's length less its trim): there is
    no end key to write (mayatk's tracks key one: ``True``)."""

    PLAYABLE_EXTENSIONS = frozenset(AudioUtils.PLAYABLE_EXTENSIONS)
    """What the Sequencer plays as it is: every format the panel offers, so
    nothing is converted (mayatk's Time Slider plays WAV/AIFF only)."""

    @staticmethod
    def _scene():
        """The current scene, or ``None`` with no Blender running."""
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    # ------------------------------------------------------------------ read

    @classmethod
    def list_tracks(cls) -> List[str]:
        """Every sound strip's name, by start frame (``[]`` outside Blender)."""
        if cls._scene() is None:
            return []
        return [clip["name"] for clip in AudioUtils.list_clips()]

    @classmethod
    def find_track(cls, name: str) -> Optional[str]:
        """*name* when a sound strip is called that, else ``None`` (the Shot
        Manifest's "Open in Audio Clips"; mayatk normalizes it to a track id)."""
        if not name or cls._scene() is None:
            return None
        return name if AudioUtils.get_clip(name) is not None else None

    @classmethod
    def get_trim(cls, track_id: str) -> Optional[Tuple[int, int]]:
        """``(head, tail)``: the frames trimmed off *track_id*'s ends; ``None``
        when there is no such strip (or no Blender)."""
        if not track_id or cls._scene() is None:
            return None
        info = AudioUtils.get_clip(track_id)
        return (int(info["trim_start"]), int(info["trim_end"])) if info else None

    # ------------------------------------------------------------------ edit

    @classmethod
    def add_tracks(cls, audio_files: List[str]) -> List[str]:
        """Add each of *audio_files* as a clip at the current frame (a strip is
        registered and placed at once).

        Returns:
            The names of the strips made; a file that is missing or will not
            load is skipped (logged).
        """
        added: List[str] = []
        for path in audio_files or ():
            try:
                added.append(AudioUtils.add_clip(path))
            except (FileNotFoundError, RuntimeError) as error:
                cls.logger.warning(f"Could not add {path!r}: {error}")
        return added

    @staticmethod
    def rename_track(track_id: str, new_name: str) -> str:
        """Rename the strip *track_id*.

        Returns:
            Its name afterwards (Blender suffixes a name already in use).

        Raises:
            ValueError: No such strip, worded for the user.
        """
        renamed = AudioUtils.rename_clip(track_id, new_name)
        if renamed is None:
            raise ValueError(f"Clip '{track_id}' not found.")
        return renamed

    @staticmethod
    def replace_track(track_id: str, path: str) -> str:
        """Point the strip *track_id* at the audio file *path*, keeping its
        name, place and trim (mayatk's track takes the new file's name).

        Returns:
            The strip's name.

        Raises:
            ValueError: No such strip, worded for the user.
            FileNotFoundError: *path* does not exist.
        """
        if not AudioUtils.replace_clip(track_id, path):
            raise ValueError(f"Clip '{track_id}' not found.")
        return track_id

    @staticmethod
    def remove_track(track_id: str) -> bool:
        """Delete the strip *track_id* (and its sound, once unused).

        Returns:
            Whether it existed.
        """
        return AudioUtils.remove_clip(track_id)

    @staticmethod
    def remove() -> int:
        """Delete every sound strip.

        Returns:
            How many there were.
        """
        return AudioUtils.remove_all_clips()

    @classmethod
    def key_track(
        cls, track_id: str, frame: Optional[float] = None, auto_end: bool = False
    ) -> float:
        """Place *track_id* at *frame* (the current frame when omitted): its
        strip moves there, trim kept -- a strip's position IS its keyed state
        (mayatk keys another ON event). *auto_end* is accepted for mayatk's
        signature: a strip ends where its sound does (:attr:`END_KEYS`).

        Returns:
            The frame placed at.

        Raises:
            ValueError: No such strip, worded for the user.
        """
        if frame is None:
            frame = cls._scene().frame_current
        if not AudioUtils.move_clip(track_id, frame):
            raise ValueError(f"Clip '{track_id}' not found.")
        return float(int(frame))

    @staticmethod
    def trim_track(
        track_id: str, start: Optional[int] = None, end: Optional[int] = None
    ) -> Optional[int]:
        """Trim *start* / *end* frames off the strip's head / tail, leaving
        where it sits (``None`` leaves that end).

        Returns:
            The frames that play afterwards, or ``None`` when there is no such strip.
        """
        if not AudioUtils.trim_clip(track_id, offset_start=start, offset_end=end):
            return None
        return int(AudioUtils.get_clip(track_id)["duration"])

    @staticmethod
    def sync_scene_range(extend_only: bool = True) -> Tuple[int, int]:
        """Fit the scene's frame range to the clips (``AudioUtils.sync_scene_range``):
        grow it over clips outside it, or with *extend_only* off fit it exactly.

        Returns:
            ``(frame_start, frame_end)`` afterwards.
        """
        return AudioUtils.sync_scene_range(extend_only=extend_only)

    @classmethod
    def reveal(cls, track_id: Optional[str] = None) -> str:
        """Select the strip *track_id* in the Sequencer and frame it in an open
        Sequencer editor (mayatk selects the carrier node holding its tracks).

        Returns:
            The strip's name.

        Raises:
            ValueError: No strip named, or none by that name, worded for the user.
        """
        if not track_id:
            raise ValueError("Select a clip first.")
        import bpy

        seq_ed = bpy.context.scene.sequence_editor
        strip = seq_ed.strips_all.get(track_id) if seq_ed else None
        if strip is None:
            raise ValueError(f"Clip '{track_id}' not found.")
        for other in seq_ed.strips_all:
            other.select = False
        strip.select = True
        seq_ed.active_strip = strip
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "SEQUENCE_EDITOR":
                    with bpy.context.temp_override(window=window, area=area):
                        try:
                            bpy.ops.sequencer.view_selected()
                        except RuntimeError as error:
                            cls.logger.debug(f"view_selected failed: {error}")
                    break
        return strip.name

    # ------------------------------------------------------------------ events

    @classmethod
    def watch(
        cls, owner, on_scene=None, on_tracks=None, on_time=None, widget=None
    ) -> List[int]:
        """Tell *owner* what happens to the scene's audio, through the
        ``ScriptJobManager`` -- mirror of mayatk's.

        * *on_scene* -- a .blend loaded (``load_post``).
        * *on_tracks* -- accepted for mayatk's signature, never called: a strip
          has no carrier attribute to watch (the panel re-lists its clips when
          its list opens).
        * *on_time(frame)* -- the current frame changed (``frame_change_post``).

        Every subscription is *owner*'s; with *widget*, they end when it is
        destroyed. Without bpy the manager only books them.

        Returns:
            The subscription tokens.
        """
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        jobs = ScriptJobManager.instance()
        tokens: List[int] = []
        if on_scene is not None:
            tokens.append(jobs.subscribe("SceneOpened", on_scene, owner=owner))
        if on_time is not None:

            def time_changed():
                scene = cls._scene()
                if scene is not None:
                    on_time(float(scene.frame_current))

            tokens.append(jobs.subscribe("timeChanged", time_changed, owner=owner))
        if widget is not None:
            jobs.connect_cleanup(widget, owner=owner)
        return tokens
