# !/usr/bin/python
# coding=utf-8
"""Transport: the playhead, audio scrub and the transport row.

Provides :class:`TransportMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController` -- and
:class:`_BlenderPlayController`, the play/stop driver the transport row calls
(``screen.animation_play``). Moving the playhead sets the scene frame; scrub
audio is Blender's own ``scene.use_audio_scrub``.
"""

from typing import TYPE_CHECKING

from blendertk.core_utils._core_utils import CoreUtils

if TYPE_CHECKING:  # annotation only: the controller imports this module
    from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_controller import (
        ShotSequencerController,
    )


class _BlenderPlayController:
    """``PlayController`` adapter driving Blender's timeline via ``screen.animation_play``.

    Tracks direction so ``TransportControls`` can resume the right way; every
    operator call runs under a window context so it works from the Qt pump.
    """

    def __init__(self, controller: "ShotSequencerController"):
        self._ctl = controller
        self._forward = True

    @staticmethod
    def _screen():
        try:
            import bpy
        except ImportError:
            return None
        screen = getattr(bpy.context, "screen", None)
        if screen is None:
            win = getattr(bpy.context, "window_manager", None)
            wins = getattr(win, "windows", None) or []
            screen = wins[0].screen if wins else None
        return screen

    def is_playing(self) -> bool:
        screen = self._screen()
        return bool(screen is not None and screen.is_animation_playing)

    def play(self, forward: bool) -> None:
        self._forward = bool(forward)
        try:
            import bpy
        except ImportError:
            return
        try:
            self._ctl._ensure_sound_on_timeline()
        except Exception:
            pass
        try:
            with CoreUtils.window_context_override():
                if self.is_playing():
                    bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play(reverse=not self._forward)
        except Exception:
            self._ctl.logger.debug("animation_play failed", exc_info=True)

    def stop(self) -> None:
        try:
            import bpy
        except ImportError:
            return
        try:
            if self.is_playing():
                with CoreUtils.window_context_override():
                    bpy.ops.screen.animation_cancel(restore_frame=False)
        except Exception:
            self._ctl.logger.debug("animation_cancel failed", exc_info=True)


class TransportMixin:
    """The playhead, audible scrubbing and the transport button row."""

    def on_playhead_moved(self, frame: float) -> None:
        """Widget playhead drag → set the scene frame (scrub audio via Blender)."""
        scene = self._scene()
        if scene is None:
            return
        self._syncing_playhead = True
        try:
            self._ensure_sound_on_timeline()
            scene.frame_set(int(round(frame)))
        finally:
            self._syncing_playhead = False

    def _ensure_sound_on_timeline(self) -> None:
        """Make scrubbing audible — Blender's own ``use_audio_scrub`` on the scene.

        Mirror of mayatk's "bind the composite to the Time Slider": Blender's
        sequencer already plays every strip, and ``scene.frame_set`` seeks the
        audio, so scrub grains only need the scene flag.  The widget's own
        ScrubPlayer is deliberately NOT bound (two players on one scene would
        double every grain).  Only flips the flag while sound strips exist.  The
        flag is read off the scene each time rather than cached: an undo swaps
        ``bpy.data`` and can revert it behind a cached "armed" marker.
        """
        scene = self._scene()
        if scene is None or scene.use_audio_scrub:
            return
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils

            if AudioUtils.list_clips(scene):
                scene.use_audio_scrub = True
        except Exception:
            self.logger.debug("audio scrub arming failed", exc_info=True)

    # ---- Transport controls (footer) -------------------------------------

    #: Button edge of the footer transport, in pixels (mirrors mayatk): sized
    #: so the glyphs land on uitk's 16px icon grid (icons are 0.7 of the
    #: button) instead of the 14px the old 20px height produced.
    TRANSPORT_BUTTON_HEIGHT = 23

    def _setup_transport_controls(self) -> None:
        """Install the reusable ``TransportControls`` row on the footer's RIGHT.

        Wired to :class:`_BlenderPlayController`; keyed off the persistent
        footer (not this controller) so a slots re-init adopts the existing row
        instead of stacking a duplicate.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        existing = getattr(footer, "_shot_transport_controls", None)
        if existing is not None:
            existing.set_play_controller(_BlenderPlayController(self))
            # range_fn is an instance method — the constructor binding would
            # otherwise keep reading the retired controller's stale state.
            existing.set_range_fn(self._playback_range)
            self._transport_controls = existing
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        from uitk.widgets.sequencer import TransportControls

        transport = TransportControls(
            sequencer=widget,
            play_controller=_BlenderPlayController(self),
            parent=footer,
            # The footer grows to fit a taller child, so this is a floor.
            button_height=max(footer.height(), self.TRANSPORT_BUTTON_HEIGHT),
            interrupt_mode=TransportControls.INTERRUPT_STOP,
            range_fn=self._playback_range,
            button_names=(
                "go_to_start",
                "prev_key",
                "play_back",
                "play_forward",
                "next_key",
                "go_to_end",
            ),
        )
        transport.attach_to_footer(footer, side="right")
        self._transport_controls = transport
        footer._shot_transport_controls = transport
        try:
            self._ensure_sound_on_timeline()
        except Exception:
            pass

    def _playback_range(self) -> tuple:
        """Range the transport's go-to-start / go-to-end buttons target.

        The ACTIVE SHOT wins over the scene range.  Reading the scene range
        made the two buttons skip the current shot's own boundaries whenever
        the range covered more than that shot — which it does in the
        "adjacent" and "all" view modes, and whenever the playback-range mode
        is "off".  An empty shot has no clips to fall back on, so there the
        skip was total.
        """
        if self.sequencer is not None:
            sid = self.active_shot_id
            shot = self.sequencer.shot_by_id(sid) if sid is not None else None
            if shot is not None and shot.end > shot.start:
                return float(shot.start), float(shot.end)
        scene = self._scene()
        if scene is None:
            return 1.0, 120.0
        return float(scene.frame_start), float(scene.frame_end)
