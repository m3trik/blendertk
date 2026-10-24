# !/usr/bin/python
# coding=utf-8
"""Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).

Blender counterpart of mayatk's ``audio_utils`` (name + domain mirrored; the internal
model is deliberately NOT mirrored — see "Divergence from mayatk" below).

Divergence from mayatk (by design)
-----------------------------------
Mayatk's ``audio_utils`` exists to work around two limitations Maya's Time Slider has and
Blender's Sequencer doesn't:

* Maya can only play **one** sound during scrubbing, so mayatk authors keyed ``on``/``off``
  enum attrs on a canonical carrier node, derives one DG ``audio`` node per track via a
  compositor, and mixes every keyed start event into a single composite WAV
  (``AudioClips.rebuild_composite``) purely so the Time Slider has one thing to play.
* Maya's Time Slider only plays WAV/AIFF, so mayatk's ``ptk.AudioUtils.ensure_playable_path``
  ffmpeg-converts MP3/OGG/M4A/FLAC to a cached WAV before anything can touch them.

Neither problem exists here:

* The VSE natively plays any number of simultaneous :class:`bpy.types.SoundStrip`
  strips — there is nothing to mix or reconcile; a strip's position *is* its keyed state.
* Blender's own media backend decodes WAV/OGG/MP3/FLAC/AAC directly (the VSE draws each
  strip's waveform natively too) — no ffmpeg pre-conversion step is needed.

So this module has no carrier node, no DG-node compositor, no composite WAV, and no
scriptJob/callback plumbing to keep a combo in sync with the playhead (the Sequencer editor
already shows exactly what is live). What is left, genuinely needed, and built here: clip
CRUD (add/remove/rename/replace/trim/move) plus fitting the scene frame range to the loaded
clips (:func:`AudioUtils.sync_scene_range` — the one thing actually named "sync" in the
mayatk feature set that has a real Blender counterpart), and what an FBX deliverable needs
to play the clips: the ``audio`` record (:func:`AudioUtils.export_record`, read off
the strips) and the files its events name (:func:`AudioUtils.track_files`).

Carrier
-------
The "canonical carrier" for Blender purposes is simply ``scene.sequence_editor`` — every
clip is a :class:`bpy.types.SoundStrip` in it, addressed by its unique strip name. There is
no separate attribute/keyframe schema to maintain alongside it.

API note (Blender 5.x)
-----------------------
Blender 5.x renamed the VSE's old ``frame_start`` / ``frame_final_start`` /
``frame_final_end`` / ``frame_final_duration`` / ``frame_offset_start`` / ``frame_offset_end``
strip properties to ``content_start`` / ``left_handle`` / ``right_handle`` / ``duration`` /
``left_handle_offset`` / ``right_handle_offset`` and flagged the old names "expected to be
removed in Blender 6.0" (confirmed live against Blender 5.1.2 — see
``test/test_audio_utils.py``). This module uses only the new names throughout so it keeps
working past that removal.

``import bpy`` is deferred into every call body (no import side effects; this module must be
importable from headless tooling with no Blender running).
"""

import os
from typing import Dict, List, Optional, Tuple

import pythontk as ptk

DEFAULT_CHANNEL = 1
"""First VSE channel candidate a new clip tries when *channel* is omitted from :func:`add_clip`."""

MAX_CHANNEL_SEARCH = 64
"""Upper bound on the free-channel search in :func:`add_clip` (a generous ceiling, not a hard limit
Blender enforces — it happily takes any positive channel number)."""

_DEFAULT_FPS = 24.0

#: ``{source_path: [(min, max), ...]}`` — waveform envelopes computed once per path
#: (mirror of mayatk's module cache; see :meth:`AudioUtils.cached_waveform`).
_WAVEFORM_CACHE: Dict[str, List[Tuple[float, float]]] = {}


class AudioUtils(ptk.LoggingMixin):
    """Scene-wide audio-clip CRUD over Blender's Video Sequence Editor.

    Every method takes an optional ``scene`` (defaults to ``bpy.context.scene``) so callers can
    target a background/other scene without touching global state.
    """

    PLAYABLE_EXTENSIONS = {
        ".wav",
        ".ogg",
        ".mp3",
        ".flac",
        ".aif",
        ".aiff",
        ".m4a",
        ".aac",
        ".opus",
    }
    """Browse-dialog filter only — Blender's own media backend decides what actually decodes;
    unlike mayatk there is no conversion step gating this list (see module docstring)."""

    # ------------------------------------------------------------------
    # Carrier
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_sequence_editor(scene=None):
        """Return *scene*'s sequence editor, creating it if this is the first strip."""
        import bpy

        scene = scene or bpy.context.scene
        if scene.sequence_editor is None:
            scene.sequence_editor_create()
        return scene.sequence_editor

    @staticmethod
    def get_sequence_editor(scene=None):
        """Return *scene*'s sequence editor, or ``None`` when it doesn't exist yet."""
        import bpy

        scene = scene or bpy.context.scene
        return scene.sequence_editor

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @classmethod
    def list_clips(cls, scene=None) -> List[Dict]:
        """Return every sound strip in *scene* as a plain dict, sorted by visible start frame."""
        seq_ed = cls.get_sequence_editor(scene)
        if seq_ed is None:
            return []
        clips = [cls._clip_info(s) for s in seq_ed.strips if s.type == "SOUND"]
        clips.sort(key=lambda c: c["frame_start"])
        return clips

    @classmethod
    def read_all_events(cls, scene=None) -> Dict[str, List[Tuple[float, float]]]:
        """Every sound strip's span: ``{strip_name: [(frame_start, frame_end)]}``.

        The whole-scene scan the shot sequencer's audio lane and the shot
        store's empty-shot check read -- the shape mayatk's
        ``AudioUtils.read_all_events`` returns (a Maya track can hold several
        on/off spans; a strip is one).

        Parameters:
            scene: The scene to read; ``None`` for the current one.

        Returns:
            ``{strip_name: [(frame_start, frame_end)]}``; empty when the scene
            has no sequence editor.
        """
        try:
            clips = cls.list_clips(scene)
        except Exception:
            return {}
        return {
            c["name"]: [(float(c["frame_start"]), float(c["frame_end"]))] for c in clips
        }

    @classmethod
    def get_clip(cls, name: str, scene=None) -> Optional[Dict]:
        """Return info for the sound strip named *name*, or ``None``."""
        strip = cls._find_strip(name, scene)
        return cls._clip_info(strip) if strip else None

    @staticmethod
    def _clip_info(strip) -> Dict:
        import bpy

        sound = strip.sound
        return {
            "name": strip.name,
            "filepath": bpy.path.abspath(sound.filepath) if sound else "",
            "channel": strip.channel,
            "frame_start": strip.left_handle,
            "frame_end": strip.right_handle,
            "duration": strip.duration,
            "trim_start": strip.left_handle_offset,
            "trim_end": strip.right_handle_offset,
            "mute": strip.mute,
            "volume": strip.volume,
        }

    @staticmethod
    def _find_strip(name: str, scene=None):
        import bpy

        scene = scene or bpy.context.scene
        seq_ed = scene.sequence_editor
        if seq_ed is None:
            return None
        strip = seq_ed.strips_all.get(name)
        return strip if (strip is not None and strip.type == "SOUND") else None

    # ------------------------------------------------------------------
    # Create / destroy
    # ------------------------------------------------------------------

    @classmethod
    def add_clip(
        cls, filepath, frame_start=None, channel=None, name=None, scene=None
    ) -> str:
        """Add *filepath* as a new sound strip. Returns the created strip's actual name.

        ``frame_start`` defaults to the scene's current frame — mayatk's two-phase "register a
        track, then key it at the playhead" collapses into one step here: a VSE strip always
        carries both a source and a timeline position, so there is no unplaced-track state to
        model. ``channel`` defaults to the lowest channel with nothing already occupying
        *frame_start* (a tidiness heuristic only — Blender itself allows overlapping strips on
        one channel, see the module docstring's API note).
        """
        import bpy

        scene = scene or bpy.context.scene
        filepath = os.path.abspath(filepath).replace("\\", "/")
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Audio file does not exist: {filepath}")

        seq_ed = cls.ensure_sequence_editor(scene)
        if frame_start is None:
            frame_start = scene.frame_current
        frame_start = int(frame_start)
        if channel is None:
            channel = cls._free_channel(seq_ed, frame_start)

        strip_name = name or os.path.splitext(os.path.basename(filepath))[0]
        strip = seq_ed.strips.new_sound(
            name=strip_name,
            filepath=filepath,
            channel=int(channel),
            frame_start=frame_start,
        )
        return strip.name

    @staticmethod
    def _free_channel(seq_ed, frame_start: int) -> int:
        """First channel (>= :data:`DEFAULT_CHANNEL`) with no strip covering *frame_start*."""
        occupied = [
            s for s in seq_ed.strips if s.left_handle <= frame_start < s.right_handle
        ]
        used_channels = {s.channel for s in occupied}
        channel = DEFAULT_CHANNEL
        while (
            channel in used_channels and channel < DEFAULT_CHANNEL + MAX_CHANNEL_SEARCH
        ):
            channel += 1
        return channel

    @classmethod
    def remove_clip(cls, name: str, scene=None) -> bool:
        """Remove the sound strip named *name*, and its ``bpy.data.sounds`` datablock if orphaned.

        Returns True if a strip was removed.
        """
        import bpy

        scene = scene or bpy.context.scene
        strip = cls._find_strip(name, scene)
        if strip is None:
            return False
        seq_ed = scene.sequence_editor
        sound = strip.sound
        seq_ed.strips.remove(strip)
        if sound is not None and sound.users == 0:
            bpy.data.sounds.remove(sound)
        return True

    @classmethod
    def remove_all_clips(cls, scene=None) -> int:
        """Remove every sound strip in *scene*. Returns the count removed."""
        names = [c["name"] for c in cls.list_clips(scene)]
        return sum(1 for n in names if cls.remove_clip(n, scene))

    # ------------------------------------------------------------------
    # Edit
    # ------------------------------------------------------------------

    @classmethod
    def rename_clip(cls, name: str, new_name: str, scene=None) -> Optional[str]:
        """Rename a clip. Returns the resulting name (Blender auto-suffixes on collision),
        or ``None`` if *name* doesn't exist."""
        strip = cls._find_strip(name, scene)
        if strip is None:
            return None
        strip.name = new_name
        return strip.name

    @classmethod
    def replace_clip(cls, name: str, new_filepath: str, scene=None) -> bool:
        """Swap *name*'s underlying audio file, keeping its position/channel/trim.

        Returns True on success. Raises ``FileNotFoundError`` when *new_filepath* is missing.
        """
        import bpy

        strip = cls._find_strip(name, scene)
        if strip is None:
            return False
        new_filepath = os.path.abspath(new_filepath).replace("\\", "/")
        if not os.path.isfile(new_filepath):
            raise FileNotFoundError(f"Audio file does not exist: {new_filepath}")

        old_sound = strip.sound
        new_sound = bpy.data.sounds.load(new_filepath, check_existing=True)
        strip.sound = new_sound
        if old_sound is not None and old_sound.users == 0:
            bpy.data.sounds.remove(old_sound)
        return True

    @classmethod
    def move_clip(cls, name: str, frame_start, scene=None) -> bool:
        """Reposition *name* so its content begins at *frame_start* (trim is preserved).

        Behavioral mirror of mayatk's "Key Audio Event" placement gesture, without the
        OFF-key/next-event/key-all machinery that only existed to work around Maya's
        single-slot Time Slider (see module docstring).
        """
        strip = cls._find_strip(name, scene)
        if strip is None:
            return False
        strip.content_start = int(frame_start)
        return True

    @classmethod
    def trim_clip(
        cls, name: str, offset_start=None, offset_end=None, scene=None
    ) -> bool:
        """Trim *name*'s head/tail without moving its overall position.

        ``offset_start``/``offset_end`` are frame counts trimmed off each end (Blender's
        ``left_handle_offset``/``right_handle_offset``); pass ``None`` to leave a side
        untouched. Negative values are clamped to 0.
        """
        strip = cls._find_strip(name, scene)
        if strip is None:
            return False
        if offset_start is not None:
            strip.left_handle_offset = max(0, int(offset_start))
        if offset_end is not None:
            strip.right_handle_offset = max(0, int(offset_end))
        return True

    # ------------------------------------------------------------------
    # Timeline queries / moves (sequencer consumers)
    # ------------------------------------------------------------------

    @staticmethod
    def get_fps(scene=None) -> float:
        """Effective scene framerate (``render.fps / fps_base``), ``24.0`` outside Blender."""
        try:
            import bpy
        except ImportError:
            return _DEFAULT_FPS
        scene = scene or bpy.context.scene
        if scene is None:
            return _DEFAULT_FPS
        base = scene.render.fps_base or 1.0
        return float(scene.render.fps) / float(base)

    @classmethod
    def clips_in_range(cls, start: float, end: float, scene=None) -> List[Dict]:
        """Sound strips whose visible span ``[frame_start, frame_end]`` overlaps ``[start, end]``."""
        return [
            c
            for c in cls.list_clips(scene)
            if not (c["frame_end"] < start or c["frame_start"] > end)
        ]

    @classmethod
    def shift_clips_in_range(
        cls,
        old_start: float,
        old_end: float,
        delta: float,
        names: Optional[List[str]] = None,
        scene=None,
    ) -> List[str]:
        """Shift every sound strip whose visible start lies in ``[old_start, old_end]`` by *delta*.

        Behavioral mirror of mayatk's ``shift_keys_in_range`` (which moves a track's
        keyed start event): a VSE strip's timeline position *is* its keyed state, so
        the whole strip translates (``content_start += delta``, trims preserved).
        *names* restricts the move to those strips.  Returns the names moved.

        The window is inclusive on both ends with a ±1e-3 slop (a strip sitting
        exactly on a shot boundary belongs to the shot that starts there — callers
        with half-open envelopes deflate ``old_end`` themselves, as the shot planner
        does).
        """
        if abs(delta) < 1e-6:
            return []
        seq_ed = cls.get_sequence_editor(scene)
        if seq_ed is None:
            return []
        eps = 1e-3
        lo, hi = old_start - eps, old_end + eps
        wanted = set(names) if names is not None else None
        moved: List[str] = []
        for strip in seq_ed.strips:
            if strip.type != "SOUND":
                continue
            if wanted is not None and strip.name not in wanted:
                continue
            if not (lo <= strip.left_handle <= hi):
                continue
            strip.content_start = int(round(strip.content_start + delta))
            moved.append(strip.name)
        return moved

    # ------------------------------------------------------------------
    # Waveform
    # ------------------------------------------------------------------

    compute_waveform_envelope = staticmethod(ptk.AudioUtils.compute_waveform_envelope)
    """Re-export: ``(wav_path) -> List[(min, max)]``."""

    @classmethod
    def cached_waveform(cls, path: str) -> List[Tuple[float, float]]:
        """Waveform envelope for *path*, computed once per path (mirror of mayatk).

        The pure envelope reader is WAV-only; other formats Blender plays natively
        (MP3/OGG/FLAC/…) go through ``ptk.AudioUtils.resolve_playable_path`` (an
        ffmpeg conversion cached next to the source) — without ffmpeg the strip
        simply renders without a waveform.
        """
        if not path:
            return []
        if path not in _WAVEFORM_CACHE:
            playable = ptk.AudioUtils.resolve_playable_path(path)
            _WAVEFORM_CACHE[path] = (
                ptk.AudioUtils.compute_waveform_envelope(playable) if playable else []
            )
        return _WAVEFORM_CACHE[path]

    @staticmethod
    def clear_waveform_cache() -> None:
        """Drop all cached waveform envelopes."""
        _WAVEFORM_CACHE.clear()

    # ------------------------------------------------------------------
    # Scene-range sync
    # ------------------------------------------------------------------

    @classmethod
    def sync_scene_range(cls, scene=None, extend_only: bool = True) -> Tuple[int, int]:
        """Fit *scene*'s frame range to the loaded clips. Returns the resulting
        ``(frame_start, frame_end)``.

        With ``extend_only=True`` (default) the range only grows to cover clips that fall
        outside it; a range that already brackets every clip is left untouched. With
        ``extend_only=False`` the range is set to exactly bracket the clips (can shrink it).
        A no-op returning the current range when there are no clips.
        """
        import bpy

        scene = scene or bpy.context.scene
        clips = cls.list_clips(scene)
        if not clips:
            return scene.frame_start, scene.frame_end

        earliest = min(c["frame_start"] for c in clips)
        latest = max(c["frame_end"] for c in clips)

        if extend_only:
            new_start = min(scene.frame_start, earliest)
            new_end = max(scene.frame_end, latest)
        else:
            new_start, new_end = earliest, latest

        scene.frame_start = int(new_start)
        scene.frame_end = int(max(new_end, new_start))
        return scene.frame_start, scene.frame_end

    # ------------------------------------------------------------------
    # Export: the ``audio`` record and the files it names
    # ------------------------------------------------------------------

    @classmethod
    def bake_events(cls, scene=None) -> List[Tuple[int, str]]:
        """``[(frame, track id), ...]`` for every strip that plays, by frame.

        Mirror of mayatk's ``AudioUtils.bake_events``: raw scene frames, the
        rebasing left to :meth:`export_record`. A strip is its own track --
        its name is the track id, as :class:`AudioSegment` reads it -- with
        one start, the visible start it is heard from. A muted strip, or one
        on a muted channel, plays nothing and ships no event (mayatk's
        ``off`` key).

        Parameters:
            scene: The scene to read; ``None`` for the current one.

        Returns:
            The events, sorted by frame; empty with no sequence editor.
        """
        seq_ed = cls.get_sequence_editor(scene)
        if seq_ed is None:
            return []
        channels = seq_ed.channels
        events = [
            (int(strip.left_handle), strip.name)
            for strip in seq_ed.strips
            if strip.type == "SOUND"
            and not strip.mute
            and not channels[strip.channel].mute
        ]
        events.sort(key=lambda event: event[0])
        return events

    @classmethod
    def track_files(cls, track_ids: List[str], scene=None) -> List[Tuple[str, str]]:
        """``(file name, path)`` of each track in *track_ids* whose file exists.

        Mirror of mayatk's: the file name is the TRACK ID -- here the strip's
        name -- with the file's extension. An ``audio`` event names
        its track and a consumer matches the clip by that name (unitytk's
        importer, by stem), so an FBX deliverable embeds each strip's audio
        under it (``FbxUtils.embed_dependencies``). A strip whose sound has no
        file on disk -- a moved file, or a sound packed into the .blend with
        none -- is left out.

        Parameters:
            track_ids: The strips to look up, in the order wanted.
            scene: The scene to read; ``None`` for the current one.

        Returns:
            ``[(track id + extension, path), ...]`` in *track_ids* order.
        """
        found: List[Tuple[str, str]] = []
        for track_id in track_ids:
            info = cls.get_clip(track_id, scene)
            path = info["filepath"] if info else ""
            if path and os.path.isfile(path):
                found.append(
                    (f"{track_id}{os.path.splitext(path)[1]}", os.path.normpath(path))
                )
        return found

    @classmethod
    def export_record(cls, ctx: "ptk.ExportContext") -> Optional["ptk.Record"]:
        """The ``audio`` record for this scene, or ``None`` when there
        is nothing to publish -- the ``ptk.SceneRecords.AUDIO`` producer
        (``FbxUtils.PRODUCERS``, mirror of mayatk's
        ``AudioClips.export_record``). Pure: it reads the strips and the takes
        and never writes.

        Each playing strip is an event (:meth:`bake_events`), scoped by
        ``ptk.AudioUtils.scope_events`` to the takes the shot record declares
        (``ptk.SceneRecords.declared_takes`` over ``ctx.record``: produced
        earlier in the same assembly -- the record declares
        ``after=("animation",)`` -- else as stored). With no take (none
        declared, or a ``full`` Animation Clips mode, which realizes none)
        events ship unscoped, counted from the first frame the file's
        animation holds: ``ctx.clip_span`` when the pipeline measured it, else
        the range the write bakes (``FbxUtils.bake_range``), else the scene's
        ``frame_start`` -- mayatk's chain too. Noted on *ctx*: an event that
        can never fire -- outside every take, or before that first frame --
        and a strip trimmed at its head: the record carries no source offset,
        so the consumer plays the file from its start.

        Parameters:
            ctx: The export's decisions and the records produced before this
                one.

        Returns:
            The record (``{"version", "events": [...], "fps"}``: the frames
            count at the scene's rate, which travels with them), or ``None``
            when no strip plays.
        """
        import bpy

        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.node_utils.data_nodes import DataNodes

        scene = bpy.context.scene
        events = cls.bake_events(scene)
        if not events:
            return None
        declared = ptk.SceneRecords.declared_takes(
            lambda key: ctx.record(key, DataNodes)
        )
        takes = [] if ctx.clip_mode == "full" else declared
        stack = ctx.clip_span or FbxUtils.bake_range(declared)
        origin = stack[0] if stack else scene.frame_start
        scoped, dropped = ptk.AudioUtils.scope_events(events, takes, origin)
        if dropped:
            where = (
                "outside every shot take"
                if takes
                else f"before the clip's first frame ({origin:g})"
            )
            ctx.note(
                f"Audio: {len(dropped)} event(s) {where} were left out (they "
                f"can never fire in a clip): {', '.join(dropped)}."
            )
        playing = {name for _, name in events}
        trimmed = [
            clip["name"]
            for clip in cls.list_clips(scene)
            if clip["name"] in playing and clip["trim_start"] > 0
        ]
        if trimmed:
            ctx.note(
                f"Audio: {', '.join(trimmed)} trimmed at the head; the "
                "deliverable plays each from its file's start, at the strip's "
                "visible start."
            )
        if not scoped:
            return None
        return ptk.SceneRecords.AUDIO.make(
            {"events": scoped, "fps": cls.get_fps(scene)}
        )
