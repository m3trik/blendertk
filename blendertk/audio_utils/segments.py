# !/usr/bin/python
# coding=utf-8
"""Consumer-facing audio-segment discovery for the sequencer + manifest (Blender).

Mirror of mayatk's ``audio_utils.segments`` — the same :class:`AudioSegment`
snapshot the shared sequencer widget renders as an audio track, derived here
from the scene's VSE **sound strips** instead of Maya's keyed carrier attrs.

Mapping (one strip = one segment):

* ``track_id`` — the strip name (unique within the sequence editor; the same
  key :meth:`AudioUtils.move_clip` / :meth:`AudioUtils.shift_clips_in_range`
  address).
* ``start`` / ``end`` — the strip's *visible* span (``left_handle`` /
  ``right_handle``), i.e. trims applied — mayatk's "start key + effective
  duration (stop key or file length)".
* ``waveform`` — :meth:`AudioUtils.cached_waveform` of the strip's source file.

Consumers treat segments as read-only snapshots; mutations go through the
``AudioUtils`` primitives (``move_clip``, ``shift_clips_in_range``, ``trim_clip``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from blendertk.audio_utils._audio_utils import AudioUtils

__all__ = ["AudioSegment"]


class _AudioSegmentInternal(object):
    """Internal helpers for AudioSegment."""

    @staticmethod
    def _from_clip(clip: dict, include_waveform: bool) -> "AudioSegment":
        """Materialize one :class:`AudioSegment` from an ``AudioUtils.list_clips`` dict."""
        start = float(clip["frame_start"])
        end = float(clip["frame_end"])
        path = clip.get("filepath") or ""
        wf = AudioUtils.cached_waveform(path) if (include_waveform and path) else []
        return AudioSegment(
            track_id=clip["name"],
            file_path=path,
            start=start,
            end=end,
            duration=max(0.0, end - start),
            label=clip["name"],
            waveform=wf,
        )


@dataclass
class AudioSegment(_AudioSegmentInternal):
    """A resolved audio segment for sequencer/manifest consumption.

    Attributes:
        track_id: VSE strip name (the canonical track identifier).
        file_path: Source audio file path.
        start: Timeline start frame (strip ``left_handle``).
        end: Timeline end frame (strip ``right_handle``).
        duration: Visible duration in frames.
        label: User-facing label (the strip name).
        waveform: Envelope points for rendering, empty if disabled/unavailable.
    """

    track_id: str
    file_path: str
    start: float
    end: float
    duration: float
    label: str = ""
    waveform: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def is_audio(self) -> bool:
        return True

    @staticmethod
    def collect_all_segments(
        scene_start: Optional[float] = None,
        scene_end: Optional[float] = None,
        include_waveform: bool = True,
        scene=None,
    ) -> List["AudioSegment"]:
        """Return every :class:`AudioSegment` in the scene's sequence editor.

        Parameters:
            scene_start: Filter out segments that end before this frame.
            scene_end: Filter out segments that start after this frame.
            include_waveform: When True, attach cached PCM envelopes.
            scene: Scene to read (default: the context scene).

        Returns:
            Segments sorted by ``start`` frame.
        """
        out: List[AudioSegment] = []
        for clip in AudioUtils.list_clips(scene):
            if scene_start is not None and clip["frame_end"] < scene_start:
                continue
            if scene_end is not None and clip["frame_start"] > scene_end:
                continue
            out.append(_AudioSegmentInternal._from_clip(clip, include_waveform))
        out.sort(key=lambda s: s.start)
        return out

    @staticmethod
    def collect_segments_for_track(
        track_id: str, include_waveform: bool = True, scene=None
    ) -> List["AudioSegment"]:
        """Return the segment for a single strip *track_id* (``[]`` if absent)."""
        clip = AudioUtils.get_clip(track_id, scene)
        if clip is None:
            return []
        return [_AudioSegmentInternal._from_clip(clip, include_waveform)]
