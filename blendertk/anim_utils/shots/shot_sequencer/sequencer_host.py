# !/usr/bin/python
# coding=utf-8
"""What the Shot Sequencer panel needs from Blender: :class:`SequencerHost`.

The panel is tentacle's, one for both hosts (``slots/tools/shot_sequencer.py``
and its ``_shot_sequencer`` package); every call it makes that names the host
is answered here (``btk.SequencerHost``), as mayatk's ``SequencerHost``
answers them for Maya -- same names, signatures and return shapes, mayatk's
the reference.  Plain values in and out: object names, frames, key targets
``(obj, attr, times, shot_id)``, the engine and store objects, and curve
handles this class itself produced (FCurves).  ``bpy`` is imported inside the
calls that need it, so headless Python and the docs tooling import this
module without it, and every call degrades there: ``None``, an empty result
or a no-op.

The engine is :class:`~blendertk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`;
the graph view's half is :class:`~blendertk.anim_utils.shots.shot_sequencer.graph_sync.GraphSync`
(:meth:`SequencerHost.graph_sync_cls`) and the segment reader
:class:`~blendertk.anim_utils.shots.shot_sequencer.segment_collector.SegmentCollector`
(:meth:`SequencerHost.segment_collector`).
"""

import contextlib
import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pythontk as ptk
from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.core_utils._core_utils import CoreUtils


_LOG = logging.getLogger(__name__)
#: What a copied key carries besides its frame and value: the shape the
#: animator gave it (``AnimUtils.copy_keys``' tangent detail).
_KEY_SHAPE = (
    "interpolation",
    "easing",
    "handle_left_type",
    "handle_right_type",
    "handle_left",
    "handle_right",
)


class _SceneWatch:
    """Blender's scene hooks for one Shot Sequencer panel
    (:meth:`SequencerHost.watch_scene`): ``bpy.app.handlers`` in place of
    mayatk's OpenMaya callbacks.

    - ``frame_change_post`` moves the panel's playhead;
    - ``depsgraph_update_post``, scoped to animation-data updates, reports key
      edits with the objects they touched (banked at handler time: the
      depsgraph is invalid by the time the panel's debounce fires);
    - the same handler compares the scene's sound strips, and an object rename
      (``bpy.msgbus``) -- scene changes that key nothing -- report a plain
      rebuild.

    Blender clears non-``@persistent`` handlers on File > New / Open:
    :meth:`refresh` re-attaches them (the panel calls it after a scene swap).
    Every handler is tracked, so :meth:`remove` detaches exactly what was
    attached.
    """

    def __init__(
        self,
        busy: Callable[[], bool],
        on_keys_edited: Callable[[set], None],
        on_time_changed: Callable[[float], None],
        on_scene_changed: Callable[[], None],
        cap: int,
    ):
        self._busy = busy
        self._keys_edited = on_keys_edited
        self._time_changed = on_time_changed
        self._scene_changed = on_scene_changed
        self._cap = cap
        self._handlers: list = []  # (handler_list, fn) pairs
        #: The sound strips as :meth:`_sound_strips_changed` last saw them.
        self._strip_signature = None
        self._removed = False
        self._attach()

    @property
    def active(self) -> bool:
        """Whether the handlers are attached."""
        return bool(self._handlers)

    def _attach(self) -> None:
        """(Re-)attach the handlers and the rename watch; detaches any this
        watcher attached first, so it can run again after a scene swap."""
        try:
            import bpy
        except ImportError:
            return
        self._detach()

        def _add(handler_list, fn):
            handler_list.append(fn)
            self._handlers.append((handler_list, fn))

        h = bpy.app.handlers
        _add(h.frame_change_post, self._on_frame_change)
        _add(h.depsgraph_update_post, self._on_depsgraph_update)

        # An object rename (no app handler reports one): a rebuild re-points
        # the shots naming it (``reconcile_all_shots``) -- mayatk's DAG-path
        # reconcile, reached the same way.  msgbus takes plain functions only,
        # so the hook goes through this closure; file load drops the
        # subscription and the panel's refresh re-runs this method.
        def _on_rename(*_args):
            self._on_rename()

        try:
            bpy.msgbus.subscribe_rna(
                key=(bpy.types.Object, "name"),
                owner=self,
                args=(),
                notify=_on_rename,
            )
        except Exception:
            _LOG.debug("object-rename watch unavailable", exc_info=True)
        # The strips as they are now are the baseline, not an edit.
        self._strip_signature = None
        self._sound_strips_changed()

    def refresh(self) -> None:
        """Re-attach the handlers a File > New / Open cleared (idempotent)."""
        if not self._removed:
            self._attach()

    def remove(self) -> None:
        """Detach every handler and the rename watch (idempotent)."""
        self._removed = True
        self._detach()

    def _detach(self) -> None:
        """Detach the tracked handlers (tolerates ones Blender already cleared)."""
        try:
            import bpy

            bpy.msgbus.clear_by_owner(self)
        except Exception:
            pass
        for handler_list, fn in self._handlers:
            try:
                handler_list.remove(fn)
            except (ValueError, ReferenceError):
                pass
        self._handlers.clear()

    def _on_rename(self) -> None:
        """An object was renamed: a plain rebuild re-points the shots.

        Not a key edit: the keying refresh adds keyed objects to the active
        shot, falling back to the SELECTION when nothing was keyed -- and a
        rename keys nothing, so riding it put whatever was selected into the
        shot (and let Extend to Keys grow it), outside any undo step.
        """
        try:
            self._scene_changed()
        except Exception:
            _LOG.debug("rename refresh not scheduled", exc_info=True)

    def _on_frame_change(self, *args) -> None:
        """Move the panel's playhead when the scene frame changes.

        Render guard: ``frame_change_post`` also fires per-frame during a
        render job — on the render thread, against the evaluated scene copy —
        and calling into Qt from there is unsafe.  Skip when a render job is
        running or when the handler's scene isn't the UI scene.

        Fully try-wrapped: this fires every frame during playback, so a raise
        here (e.g. a stale-widget access) would spam the console and break the
        refresh loop mid-playback — Blender does NOT auto-remove a raising
        handler, it just prints the traceback each time.
        """
        try:
            import bpy

            is_job_running = getattr(bpy.app, "is_job_running", None)
            if is_job_running is not None and is_job_running("RENDER"):
                return
            scene = SequencerHost._scene()
            if args and scene is not None and args[0] is not scene:
                return  # evaluated copy from a render/bake job, not the UI scene
            if scene is not None:
                self._time_changed(scene.frame_current_final)
        except Exception:
            _LOG.debug("frame-change handler failed", exc_info=True)

    def _on_depsgraph_update(self, *args) -> None:
        """Report key edits when the scene's ANIMATION DATA changes.

        Replaces mayatk's ``MAnimMessage`` keyframe-edited callback, so it must
        be scoped like one: ``depsgraph_update_post`` fires on nearly every
        scene interaction (selection clicks, transform drags, playback ticks),
        and the panel's refresh epilogue mutates state (it merges keyed
        objects into the active shot and marks the store dirty).  Two guards
        keep it a keyframe-edit proxy:

        - **playback guard** — skip while the animation is playing (every
          frame is a depsgraph tick; the frame-change handler owns playback);
        - **Action filter** — only react when an ``Action`` datablock is among
          ``depsgraph.updates`` (keyframe insert/move/delete tags the action;
          a bare selection click or a transform drag without autokey does not).

        Deaf during the panel's own edits and inside a transient edit
        (``ShotStore.transient_edit``: an export or a bridge send, which puts
        the scene back): its bake banked every animated object, and the keying
        epilogue added a non-member keyed in the active shot's frames to its
        members (2026-10-05, a Unity send).  During the panel's own edits the
        strip baseline follows silently, so a strip the panel itself moved is
        not read as an outside edit later.

        Try-wrapped: a raise here would spam the console on every scene edit
        and break the live refresh — Blender does NOT auto-remove a raising
        handler, it just prints the traceback each time.
        """
        try:
            if self._busy():
                self._strip_signature = None
                self._sound_strips_changed()
                return
            if ptk.ShotStore.in_transient_edit():
                return
            import bpy

            screen = getattr(bpy.context, "screen", None)
            if screen is not None and screen.is_animation_playing:
                return
            depsgraph = args[1] if len(args) > 1 else None
            if depsgraph is not None and not SequencerHost._is_animation_update(
                depsgraph
            ):
                # A sound strip moved, trimmed, added or removed in Blender's
                # own Sequencer tags no Action -- mayatk hears the same edit
                # through its keyframe callback, its audio being keyed on a
                # carrier -- so compare the strips themselves.  Only when the
                # SCENE is among the updates (a strip edit tags it; a viewport
                # drag, every tick of it, does not).  A plain rebuild redraws
                # the audio track: the keying epilogue would add the SELECTION
                # to the active shot, since a strip edit keys no object.
                scene_tagged = any(
                    isinstance(u.id, bpy.types.Scene) for u in depsgraph.updates
                )
                if scene_tagged and self._sound_strips_changed():
                    self._scene_changed()
                return
            edited: set = set()
            if depsgraph is not None:
                # Bank NOW — the depsgraph is invalid by the time the 200ms
                # debounce fires.  Object IDs arrive in the same updates
                # batch as the Action (probed pairing: key insert →
                # ['Object', 'Action']); updates carry EVALUATED ids, so
                # take .original.  Past the cap the panel stops banking: the
                # burst is a bake or an import and its refresh scans the
                # selection instead.
                for u in depsgraph.updates:
                    if isinstance(u.id, bpy.types.Object):
                        edited.add(u.id.original.name)
            self._keys_edited(edited)
        except Exception:
            _LOG.debug("depsgraph handler failed", exc_info=True)

    def _sound_strips_changed(self) -> bool:
        """True when the scene's sound strips differ from the last look.

        Name, placement, trim, channel and mute of every sound strip: what the
        audio track draws.  Cheap -- a scene holds a handful of strips -- which
        matters, since it runs on every depsgraph update that is not a key edit.
        """
        scene = SequencerHost._scene()
        ed = getattr(scene, "sequence_editor", None) if scene is not None else None
        sig = (
            tuple(
                sorted(
                    (
                        st.name,
                        st.frame_start,
                        st.frame_final_start,
                        st.frame_final_end,
                        st.channel,
                        st.mute,
                    )
                    for st in ed.strips_all
                    if st.type == "SOUND"
                )
            )
            if ed is not None
            else ()
        )
        if sig == self._strip_signature:
            return False
        first = self._strip_signature is None
        self._strip_signature = sig
        return not first  # the baseline, not an edit


class _BlenderPlayController:
    """``PlayController`` adapter driving Blender's timeline via ``screen.animation_play``.

    Runs *before_play* (the panel arms its audio scrub there) before starting
    playback, tracks direction so ``TransportControls`` can resume the right
    way, and runs every operator under a window context so it works from the
    Qt pump.
    """

    def __init__(self, before_play: Optional[Callable[[], None]] = None):
        self._before_play = before_play
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
        if self._before_play is not None:
            try:
                self._before_play()
            except Exception:
                pass
        try:
            with CoreUtils.window_context_override():
                if self.is_playing():
                    bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play(reverse=not self._forward)
        except Exception:
            _LOG.debug("animation_play failed", exc_info=True)

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
            _LOG.debug("animation_cancel failed", exc_info=True)


def _object(obj_name: str):
    """The Blender object named *obj_name*, or ``None`` (also without bpy)."""
    try:
        import bpy
    except ImportError:
        return None
    return bpy.data.objects.get(obj_name)


def _place_dragged_handle(kp, side: str, dt: float, dv: float, broken: bool = False):
    """Put one handle of keyframe point *kp* at ``co + (dt, dv)``.

    The preview's control points ARE the handles (``handle_left`` /
    ``handle_right``), so the vector lands directly.  A computed handle
    (auto, auto-clamped, vector) cannot hold a position -- ``update()``
    re-derives it -- so the first drag makes it what the Graph Editor would:
    ALIGNED, both sides, unless the other side is already FREE, in which case
    the dragged side goes FREE too.  On an aligned key the opposite handle is
    re-aimed along the new line, keeping its length, which is what aligned
    means.

    *broken* (the Alt drag) is Maya's ``keyTangent -lock false``: it is the
    KEY that breaks, not one side of it, so BOTH handles go FREE and the
    partner is left exactly where it sits.  An aligned partner would otherwise
    swing itself back in line with the handle being dragged -- the very thing
    the gesture is asking not to happen.  (``GraphSync`` places its dragged
    handles through this too.)
    """
    co_t, co_v = float(kp.co[0]), float(kp.co[1])
    dragged, other = (
        ("handle_right", "handle_left")
        if side == "out"
        else ("handle_left", "handle_right")
    )
    d_type, o_type = dragged + "_type", other + "_type"
    if broken:
        setattr(kp, d_type, "FREE")
        setattr(kp, o_type, "FREE")
    elif getattr(kp, d_type) in ("AUTO", "AUTO_CLAMPED", "VECTOR"):
        new = "FREE" if getattr(kp, o_type) == "FREE" else "ALIGNED"
        setattr(kp, d_type, new)
        if new == "ALIGNED":
            setattr(kp, o_type, "ALIGNED")
    setattr(kp, dragged, (co_t + dt, co_v + dv))
    if getattr(kp, d_type) == "ALIGNED" and getattr(kp, o_type) == "ALIGNED":
        ox, oy = getattr(kp, other)
        length = math.hypot(float(ox) - co_t, float(oy) - co_v)
        norm = math.hypot(dt, dv)
        if norm > 1e-9 and length > 1e-9:
            setattr(kp, other, (co_t - dt / norm * length, co_v - dv / norm * length))


class SequencerHost:
    """Blender's answers to the Shot Sequencer panel's host calls."""

    # ---- the scene's time, playback range, scene events, undo and transport ------

    #: The host's name, as the panel's messages say it.
    APP = "Blender"

    #: Whether the panel redraws after EVERY native undo / redo the store
    #: followed, or only after one that put a shot edit's restore point back.
    #: Blender has no per-key callback to report what an unrelated undo did to
    #: the keys on screen (mayatk's anim-curve callback does), and the refresh
    #: carries a rename reconcile an undo can need (it can take a rename back).
    REDRAW_EVERY_NATIVE_STEP = True

    @staticmethod
    def _scene():
        """Active Blender scene, or ``None`` headless."""
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    @classmethod
    def current_time(cls) -> Optional[float]:
        """The playhead's frame, or ``None`` without a scene.

        ``frame_current_final``: the playhead with its sub-frame
        (``frame_current`` is the int), as Maya's currentTime keeps the
        fraction.
        """
        scene = cls._scene()
        if scene is None:
            return None
        try:
            return float(scene.frame_current_final)
        except Exception:
            return None

    @classmethod
    def set_current_time(cls, frame: float) -> None:
        """Move the playhead to *frame*.

        Sub-frames are kept, as mayatk's ``currentTime`` takes the float: with
        a fractional snap the playhead would otherwise sit up to half a frame
        off the time it shows.  A frame change records no undo step in Blender.
        """
        scene = cls._scene()
        if scene is None:
            return
        whole = math.floor(frame + 1e-6)
        scene.frame_set(int(whole), subframe=max(0.0, float(frame) - whole))

    @classmethod
    def playback_range(cls) -> Optional[Tuple[float, float]]:
        """The scene's playback range: the preview range when it is on
        (Blender's twin of Maya's playback range), else the scene range;
        ``None`` without a scene."""
        scene = cls._scene()
        if scene is None:
            return None
        if scene.use_preview_range:
            return float(scene.frame_preview_start), float(scene.frame_preview_end)
        return float(scene.frame_start), float(scene.frame_end)

    @classmethod
    def set_playback_range(cls, start: float, end: float) -> None:
        """Set the playback range: the PREVIEW range, turned on (the scene
        range is also the render range, so the panel never moves it)."""
        scene = cls._scene()
        if scene is None:
            return
        scene.use_preview_range = True
        scene.frame_preview_start = int(round(start))
        scene.frame_preview_end = int(round(end))

    @staticmethod
    def watch_scene(
        *,
        busy: Callable[[], bool],
        on_keys_edited: Callable[[set], None],
        on_time_changed: Callable[[float], None],
        on_scene_changed: Callable[[], None],
        cap: int = 400,
    ) -> _SceneWatch:
        """Attach the panel's scene hooks; returns the watcher.

        Parameters:
            busy: Whether the panel is mid-edit: key edits made then are its
                own and are not reported.
            on_keys_edited: Called with the set of object names whose Actions
                Blender reported as updated (empty when it named none): the
                panel banks them and debounces its refresh
                (:meth:`newly_keyed_members` resolves them).
            on_time_changed: Called with the new frame on every frame change.
            on_scene_changed: A scene change that keys nothing -- an object
                renamed, a sound strip edited outside the panel.
            cap: The panel's banking cap (unused here: the panel applies it).

        Returns:
            The watcher: ``refresh()`` re-attaches after a scene change,
            ``remove()`` detaches (both idempotent), ``active`` says whether
            it is attached.
        """
        return _SceneWatch(busy, on_keys_edited, on_time_changed, on_scene_changed, cap)

    @staticmethod
    def _is_animation_update(depsgraph) -> bool:
        """True when an ``Action`` datablock is among the depsgraph updates.

        Keyframe insert/move/delete tags the owning Action (probed on Blender
        5.1: key insert → ``['Object', 'Action']``); a bare selection click
        (``['Scene']``) or a transform drag without autokey (``['Object']``)
        does not — the discriminator that scopes the watcher's depsgraph
        handler to keyframe edits, like mayatk's ``MAnimMessage`` callback.
        """
        import bpy

        return any(isinstance(u.id, bpy.types.Action) for u in depsgraph.updates)

    @staticmethod
    def newly_keyed_members(edited: Iterable[str], shot, sequencer) -> List[str]:
        """The objects keyed in *shot*'s frames that are not its members yet.

        Candidates come from *edited* -- the objects whose Actions the scene
        watcher reported as updated -- falling back to the current selection
        when it names none (a bulk edit past the cap is handed over as none),
        so scripted or channel-pinned keying on UNSELECTED objects still joins
        the shot.  A candidate qualifies on "has a key in the shot's range on
        a content channel" (``sequencer._has_keys``: transforms, visibility,
        render effects), and deliberately NOT on its values varying, as
        mayatk's does: an object keyed on a hold inside the shot -- or keyed
        for the first time -- is still the shot's content.

        Parameters:
            edited: Object names banked since the last refresh.
            shot: The shot (``ShotBlock``) the keys would join.
            sequencer: The panel's ``ShotSequencer`` (its key probe).

        Returns:
            The new members' names, sorted.
        """
        try:
            import bpy
        except ImportError:
            return []
        candidates = set(edited)
        if not candidates:
            # not bpy.context.selected_objects: absent windowless (the Qt pump)
            candidates = {o.name for o in CoreUtils.selected_objects()}
        candidates -= set(shot.objects)
        return sorted(
            name
            for name in candidates
            if (obj := bpy.data.objects.get(name)) is not None
            and sequencer._has_keys(obj, shot.start, shot.end)
        )

    @staticmethod
    def step_undo(store, redo: bool = False) -> bool:
        """One step of Blender's undo (*redo*: redo) for the panel's Undo / Redo.

        mayatk decides up front from the queue's step names; with no names to
        read, Blender decides from what the undo did: the store follows it
        (``BlenderShotStore.follow_native_step``, its ``undo_post`` handler --
        the same path as a Ctrl+Z in a viewport, with the panel open or not)
        and applies the restore point for our own step alone.  An unrelated
        step on top is undone by itself, as mayatk's plan passes it straight
        through.  Runs under a window context (Qt-timer safe).

        Parameters:
            store: The panel's store (the store follows by itself here).
            redo: Step the redo queue instead.

        Returns:
            Whether a step was attempted (``False`` without Blender).
        """
        try:
            import bpy
        except ImportError:
            return False
        try:
            with CoreUtils.window_context_override():
                if redo:
                    bpy.ops.ed.redo()
                else:
                    bpy.ops.ed.undo()
        except Exception:
            _LOG.debug("native %s failed", "redo" if redo else "undo", exc_info=True)
        return True

    @staticmethod
    def play_controller(before_play: Optional[Callable[[], None]] = None):
        """The transport row's ``PlayController``: Blender's timeline through
        ``screen.animation_play``, running *before_play* before playback."""
        return _BlenderPlayController(before_play)

    @classmethod
    def bind_timeline_audio(cls, current: str = "") -> str:
        """Make scrubbing audible: Blender's own ``use_audio_scrub`` on the scene.

        mayatk binds the composite to the Time Slider; Blender's sequencer
        already plays every strip, and ``scene.frame_set`` seeks the audio,
        so scrub grains only need the scene flag -- set only while sound
        strips exist.  The flag is read off the scene each time rather than
        cached: an undo swaps ``bpy.data`` and can revert it behind a cached
        "armed" marker.

        Returns:
            ``""`` always: the widget's own scrub player is deliberately NOT
            bound (two players on one scene would double every grain).
        """
        scene = cls._scene()
        if scene is None or scene.use_audio_scrub:
            return ""
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils

            if AudioUtils.list_clips(scene):
                scene.use_audio_scrub = True
        except Exception:
            _LOG.debug("audio scrub arming failed", exc_info=True)
        return ""

    @staticmethod
    def timeline_audio_wav(source: str) -> str:
        """The WAV the widget's scrub player would play: none in Blender, which
        scrubs its own strips (:meth:`bind_timeline_audio`)."""
        return ""

    @classmethod
    def scene_fps(cls) -> float:
        """The scene's frame rate (24.0 outside Blender)."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        return AudioUtils.get_fps(cls._scene())

    # ---- objects, names, icons, segments and audio rows (the widget's rebuild) ----

    #: ``NodeIcons`` once resolved (``None`` when it cannot load); ``...`` until then.
    _node_icons_cls = ...

    @staticmethod
    def segment_collector() -> type:
        """The library's ``SegmentCollector``: the shots' motion segments, a
        segment's attributes, the object row's key ticks and a curve's
        preview, read from Blender's fcurves."""
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        return SegmentCollector

    @staticmethod
    def existing(names: Iterable[str]) -> set:
        """The subset of *names* that are objects in ``bpy.data.objects``.

        Blender object names are flat and unique, so a name is its own
        lookup.  Without ``bpy`` there is no scene to ask, and every name
        counts as present.

        Parameters:
            names: Object names.

        Returns:
            The names that exist.
        """
        names = list(names)
        try:
            import bpy
        except ImportError:
            return set(names)
        return {n for n in names if bpy.data.objects.get(n) is not None}

    @staticmethod
    def leaf_name(name: str) -> str:
        """*name*'s last ``|`` segment: a Blender name is flat, but a member a
        Maya scene named by DAG path keeps its path in the store."""
        return name.split("|")[-1]

    @classmethod
    def node_icon(cls, name: str):
        """The object's type icon (a ``QIcon``), or ``None``.  ``NodeIcons``
        (``Object.type`` -> uitk icon) is resolved once per process."""
        if cls._node_icons_cls is ...:
            try:
                from blendertk.ui_utils.node_icons import NodeIcons
            except ImportError:
                NodeIcons = None
            cls._node_icons_cls = NodeIcons
        node_icons = cls._node_icons_cls
        return node_icons.get_icon(name) if node_icons else None

    @staticmethod
    def select_objects(names: Iterable[str], add: bool = False) -> List[str]:
        """Select the objects *names* name, on the window's view layer.

        ``CoreUtils.select``: any mode, the first selected object active; an
        object the layer cannot select (an excluded collection, another
        scene -- the ``bpy.data.objects`` lookup is scene-wide) is skipped,
        never raised on; nothing selectable clears the selection (unless
        *add*).  A view mirror: Blender's selection is no undo step of its own.

        Parameters:
            names: Object names.
            add: Add to the selection instead of replacing it.

        Returns:
            The names of the objects selected.
        """
        try:
            import bpy
        except ImportError:
            return []
        from blendertk.core_utils._core_utils import CoreUtils

        objects = [bpy.data.objects.get(n) for n in names]
        done = CoreUtils.select(objects, mode="add" if add else "replace")
        return [o.name for o in done]

    @staticmethod
    def claimed_key_times(sequencer, obj: str, curve) -> List[float]:
        """The samples the shot system created on *curve* (its ledger claims).

        Blender keys a claim by the object NAME plus the channel
        (``ShotSequencer._fc_key``); passing the Object instead silently
        builds a key nothing matches.

        Parameters:
            sequencer: The host's ``ShotSequencer``.
            obj: The name of the object the fcurve animates.
            curve: The fcurve.
        """
        return sequencer.ledger.key_times(sequencer._fc_key(obj, curve))

    @staticmethod
    def motion_segments(
        objects: Iterable[str],
        time_range: Tuple[float, float],
        attributes: Optional[Iterable[str]] = None,
        ignore_holds: bool = True,
    ) -> List[Dict[str, Any]]:
        """The motion segments of *objects* inside *time_range*.

        ``SegmentKeys.collect_segments`` with the sequencer's rules: split at
        static gaps, visibility holds ignored, motion-rate classified.

        Parameters:
            objects: Object names.
            time_range: ``(start, end)`` frames.
            attributes: Restrict to these channel labels (``translateX``...);
                ``None`` reads every keyed one.
            ignore_holds: Drop trailing holds instead of absorbing them.
        """
        objects = list(objects)
        if not objects:
            return []
        try:
            import bpy  # noqa: F401 -- availability check
        except ImportError:
            return []
        from blendertk.anim_utils.segment_keys import SegmentKeys

        extra = {} if attributes is None else {"channel_box_attrs": list(attributes)}
        return SegmentKeys.collect_segments(
            objects,
            split_static=True,
            time_range=tuple(time_range),
            ignore_holds=ignore_holds,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
            **extra,
        )

    @staticmethod
    def attribute_curves(name: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """The object *name* and its animated channels.

        Every keyed channel on the object, as Maya lists every animCurve on
        the node -- custom properties and visibility get rows too -- mapped
        by its Maya-style label (``SegmentCollector.attr_label``); the first
        fcurve wins.

        Parameters:
            name: The object's name.

        Returns:
            ``(name, {label: fcurve})``; ``(None, {})`` when the object (or
            ``bpy``) is missing.
        """
        try:
            import bpy
        except ImportError:
            return None, {}
        obj = bpy.data.objects.get(name)
        if obj is None:
            return None, {}
        from blendertk.anim_utils._anim_utils import AnimUtils
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        attr_to_curve: Dict[str, Any] = {}
        for fc in AnimUtils.get_fcurves(obj):
            attr_to_curve.setdefault(SegmentCollector.attr_label(fc), fc)
        return name, attr_to_curve

    @staticmethod
    def audio_segments(start: float, end: float) -> list:
        """The sound strips overlapping ``[start, end]`` as audio segments,
        waveforms included (``AudioSegment.collect_all_segments`` over the
        scene's sequence editor)."""
        try:
            import bpy  # noqa: F401 -- availability check
        except ImportError:
            return []
        from blendertk.audio_utils.segments import AudioSegment

        return AudioSegment.collect_all_segments(
            scene_start=start, scene_end=end, include_waveform=True
        )

    @staticmethod
    def audio_track_icon(track_id: str):
        """The icon of an audio track: a sound strip has no object, so the
        generic waveform glyph."""
        from uitk import IconManager

        return IconManager.get("activity", size=(16, 16), color="#888888")

    # ---- clip-level key deletes and the key stash --------------------------------

    @classmethod
    def _content_curves(cls, obj, attrs: Sequence[str]) -> list:
        """The fcurves a clip stands for on *obj* (an Object): its channels'
        (``curves_for_attrs``), or -- a clip naming no channel, a stepped
        point clip -- the object's TRANSFORM fcurves, the ``is_transform_path``
        set its span was collected from.  Custom-property, constraint and
        modifier curves are never part of a clip."""
        if attrs:
            return cls.curves_for_attrs(obj.name, attrs)
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        return ShotSequencer._transform_fcurves(obj)

    @classmethod
    def delete_span_keys(
        cls,
        obj: str,
        attrs: Sequence[str],
        start: float,
        end: float,
        release: Optional[Callable] = None,
    ) -> int:
        """Remove *obj*'s keys on *attrs* inside ``[start, end]``.

        Parameters:
            obj: The object's name.
            attrs: The channels (``translateX``-style labels); empty: the
                object's transform fcurves (:meth:`_content_curves`).
            start: First frame of the span (inclusive).
            end: Last frame of the span (inclusive).
            release: ``release(fcurve, times)`` -- the store's
                ``release_replaced`` hook -- called with each curve's removed
                times, so every claim on a deleted key goes with it.

        Returns:
            How many keys were removed (0 when the object is gone or holds no
            key in the span).
        """
        try:
            import bpy
        except ImportError:
            return 0
        from blendertk.anim_utils._anim_utils import AnimUtils

        ob = bpy.data.objects.get(obj or "")
        if ob is None:
            return 0
        deleted = 0
        for fc in cls._content_curves(ob, attrs):
            kt = AnimUtils.key_times(fc)
            i0, i1 = AnimUtils.window_indices(kt, start - 1e-3, end + 1e-3)
            for i in reversed(range(i0, i1)):
                fc.keyframe_points.remove(fc.keyframe_points[i])
                deleted += 1
            if i1 > i0:
                fc.update()
                if release is not None:
                    release(fc, kt[i0:i1])
        return deleted

    @classmethod
    def stash_keys(
        cls,
        targets: Sequence[Tuple[str, Sequence[str], float, float]],
        source_shot_id: Optional[int] = None,
    ):
        """Park the keys of *targets* in the file's key stash as ONE clip.

        Parameters:
            targets: ``(obj, attrs, start, end)`` scopes by object name;
                *attrs* empty for the object's transform fcurves.  An object
                that no longer exists, or has no curve in scope, is skipped.
            source_shot_id: The shot every scope came from, or ``None``.

        Returns:
            The stored clip (``StashedClip``), or ``None`` when nothing was
            stashable.
        """
        try:
            import bpy
        except ImportError:
            return None
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        scopes = []
        for name, attrs, start, end in targets:
            ob = bpy.data.objects.get(name or "")
            if ob is None:
                continue
            fcurves = cls._content_curves(ob, attrs)
            if fcurves:
                scopes.append((ob, fcurves, start, end))
        if not scopes:
            return None
        return KeyStash.active().stash(targets=scopes, source_shot_id=source_shot_id)

    # ---- curves, key moves and scales, tangents and key-selection edits ----------

    @classmethod
    def curves_for_attr(cls, obj_name: str, attr_name: str) -> list:
        """The fcurves driving *attr_name* (a ``translateX``-style label) on
        *obj_name*.

        Matches by ``SegmentCollector.attr_label`` -- the *same* forward
        function the sub-rows are labelled with -- so resolution is always
        consistent with the label the user sees, works for
        ``rotation_quaternion`` (a hand-kept reverse ``translateX->(location,0)``
        map missed it and silently returned ``[]``), and can't drift from
        ``attr_label``.  Falls back to a raw ``data_path`` substring only when
        no curve's label matches exactly -- mayatk resolves the exact plug,
        and an unconditional substring test let a custom property named
        ``scale`` (or ``x``) drag sibling channels into a sub-row's move,
        resize or delete.
        """
        return cls.curves_for_attrs(obj_name, [attr_name])

    @staticmethod
    def curves_for_attrs(obj_name: str, attr_names: Iterable[str]) -> list:
        """:meth:`curves_for_attr` over several labels: every fcurve behind
        *attr_names* on *obj_name*, each once -- two segments of one channel
        are one curve, and handed twice an edit would run over it twice.  One
        read of the object's fcurves however many labels are asked for."""
        obj = _object(obj_name)
        if obj is None:
            return []
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        curves = list(AnimUtils.get_fcurves(obj))
        labels = [SegmentCollector.attr_label(fc) for fc in curves]
        out, seen = [], set()
        for attr in dict.fromkeys(attr_names):
            exact = [fc for fc, label in zip(curves, labels) if label == attr]
            for fc in exact or [fc for fc in curves if attr in fc.data_path]:
                if fc.as_pointer() not in seen:
                    seen.add(fc.as_pointer())
                    out.append(fc)
        return out

    @staticmethod
    def curve_id(curve) -> int:
        """A curve's identity across reads: its data pointer.  bpy hands out a
        fresh wrapper per access, so ``id()`` told one curve apart from itself
        -- and linked duplicates' rows name one shared curve."""
        return curve.as_pointer()

    @staticmethod
    def object_curves(obj_name: str) -> list:
        """Every fcurve animating *obj_name*."""
        obj = _object(obj_name)
        return list(AnimUtils.get_fcurves(obj)) if obj is not None else []

    @staticmethod
    def first_key(curves) -> Optional[float]:
        """The earliest key time across the fcurves *curves*, or ``None``."""
        times = [kp.co[0] for fc in curves or () for kp in fc.keyframe_points]
        return min(times) if times else None

    @staticmethod
    def keyed_at(curve, times: Iterable[float], eps: float = 1e-3) -> List[bool]:
        """Whether *curve* holds a key within *eps* of each of *times* (one
        read of its key times)."""
        kt = AnimUtils.key_times(curve)
        out = []
        for t in times:
            i0, i1 = AnimUtils.window_indices(kt, t - eps, t + eps)
            out.append(i1 > i0)
        return out

    @staticmethod
    def any_key_at(curves, frame: float) -> bool:
        """Whether any fcurve of *curves* holds a key on *frame*: the
        ``seam_keyed`` question ``ShotStore.enclosing_bounds`` asks of the
        curves a landing rides on."""
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            _ShotSequencerInternal,
        )

        return _ShotSequencerInternal._any_key_at(list(curves), frame)

    @classmethod
    def scale_attribute_keys(
        cls,
        obj_name: str,
        attr_name: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
        ledger=None,
    ) -> bool:
        """Scale only the fcurves driving *attr_name* on *obj_name* (a sub-row
        clip resize).

        Parameters:
            obj_name: The object.
            attr_name: The attribute label whose fcurves scale.
            old_start: The span's start before the resize.
            old_end: The span's end before the resize.
            new_start: Where the span starts after it.
            new_end: Where it ends after it.
            ledger: The store's ``edit_ledger``: its claims on the retimed keys
                are carried with them, as every retime does
                (``_ShotSequencerInternal._retime_fcurve``); ``None`` skips that.

        Returns:
            ``True`` when a remap was actually issued -- a caller that
            snapshotted for undo needs to know a no-op happened so it can
            cancel the snapshot instead of leaving a dead restore point.
        """
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            _ShotSequencerInternal,
        )

        curves = cls.curves_for_attr(obj_name, attr_name)
        if not curves or abs(old_end - old_start) < 1e-6:
            return False
        lo, hi = old_start - 1e-3, old_end + 1e-3
        for fc in curves:
            _ShotSequencerInternal._retime_fcurve(
                fc,
                lo,
                hi,
                old_start,
                old_end,
                new_start,
                new_end,
                ledger=ledger,
                key=_ShotSequencerInternal._fc_key(obj_name, fc),
            )
        return True

    @staticmethod
    def commit_curve_moves(curve_moves: Dict, ledger=None, eps: float = 1e-3) -> None:
        """Land merged key moves, one primitive call per fcurve.

        *curve_moves* is ``{curve_id: {"curve": fc, "obj": obj, "pairs": [(old,
        new, shot_id), ...]}}`` (the panel's gesture plan and ``GraphSync``
        build it).  Routed through the engine's ``move_curve_keys`` /
        ``recreate_curve_keys``: each point moves in place (``co[0] = new_t``)
        -- no tangent capture / replay -- and the two-pass recreate guarantees
        a later ``(old_t, new_t)`` pair can never grab a key an earlier pair
        just moved.  *ledger* rides along so a gap hold or boundary sample the
        drag picks up moves with its key.
        """
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
            _ShotSequencerInternal,
        )

        for entry in curve_moves.values():
            fc = entry["curve"]
            key = _ShotSequencerInternal._fc_key(entry.get("obj", ""), fc)
            pairs = [(old, new) for old, new, _sid in entry["pairs"]]
            deltas = {round(new - old, 6) for old, new in pairs}
            if len(deltas) == 1:
                ShotSequencer.move_curve_keys(
                    fc,
                    [old for old, _ in pairs],
                    deltas.pop(),
                    eps=eps,
                    ledger=ledger,
                    ledger_key=key,
                )
            else:
                ShotSequencer.recreate_curve_keys(
                    fc, pairs, eps=eps, ledger=ledger, ledger_key=key
                )

    @staticmethod
    def delete_keys_at(
        curves, times: Iterable[float], release: Optional[Callable] = None
    ) -> int:
        """Remove the keyframe points of *curves* on each of *times*.

        Parameters:
            curves: The fcurves to remove from.
            times: Key times, matched within 1e-3.
            release: ``release(curve, frames)`` -- the store's
                ``release_replaced`` -- called with the frames removed from
                each fcurve, so their claims go with them.

        Returns:
            How many of *times* had at least one point removed.
        """
        cut = 0
        for t in times:
            cut_any = False
            for fc in curves:
                kt = AnimUtils.key_times(fc)
                i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                for i in reversed(range(i0, i1)):
                    fc.keyframe_points.remove(fc.keyframe_points[i])
                    cut_any = True
                if i1 > i0:
                    fc.update()
                    if release is not None:
                        release(fc, kt[i0:i1])
            if cut_any:
                cut += 1
        return cut

    @staticmethod
    @contextlib.contextmanager
    def moving_audio_track(track_id: str, ledger=None):
        """Yield ``(move, lowest)`` for moving an audio track's content.

        ``move(lo, hi, delta)`` translates the VSE sound strip named
        *track_id* (the track id is the strip's name; a strip's position IS
        its keyed state, so nothing re-syncs and *ledger* has nothing to
        carry).  By NAME alone: a strip lands on whole frames, so a window
        test at the unrounded parked position could miss it and leave it
        parked.  ``lowest`` is ``None``: a strip meets no key.
        """
        from blendertk.audio_utils._audio_utils import AudioUtils

        def move(_lo, _hi, d):
            AudioUtils.shift_clips_in_range(-1e9, 1e9, d, names=[track_id])

        yield move, None

    @staticmethod
    def undo_chunk(label: str):
        """One named undo step (``CoreUtils.undo_chunk``) for an edit made
        without a shot store; yields a handle whose ``cancel()`` pushes no
        step for a body that changed nothing."""
        return CoreUtils.undo_chunk(label)

    #: Handle types a key's context menu offers: ``(label, handle type)``.
    #: Blender's own names (the concept diverges from Maya's tangent types);
    #: ``FLAT`` is not a Blender type but Maya's flat tangent -- a horizontal
    #: handle, written by :meth:`tangent_type_edit`.  Maya's step is the
    #: Interpolation submenu's Constant.
    TANGENT_TYPES = (
        ("Free", "FREE"),
        ("Aligned", "ALIGNED"),
        ("Vector", "VECTOR"),
        ("Automatic", "AUTO"),
        ("Auto Clamped", "AUTO_CLAMPED"),
        ("Flat", "FLAT"),
    )

    #: Interpolation modes: ``(label, interpolation)``.
    INTERPOLATIONS = (
        ("Constant", "CONSTANT"),
        ("Linear", "LINEAR"),
        ("Bezier", "BEZIER"),
    )

    @staticmethod
    def tangent_from_handle(side: str, dt: float, dv: float) -> Tuple[float, float]:
        """``(angle_degrees, weight)`` of the tangent a dragged handle stands
        for (mayatk's; Blender places the handle itself,
        :meth:`handle_drag_edit`)."""
        if side == "in":
            dt, dv = -dt, -dv
        return math.degrees(math.atan2(dv, dt)), math.hypot(dt, dv)

    @staticmethod
    def tangent_type_edit(tangent: str, sides=("in", "out")) -> Callable:
        """The edit that sets the handle type *tangent* (a
        :attr:`TANGENT_TYPES` type) on the given sides.

        ``FLAT`` is Maya's flat tangent: the side's handle goes horizontal
        (its length kept), ALIGNED when both sides are flattened, FREE when
        only one is -- a partner left ALIGNED would swing level with it.
        Setting the OUT side also makes the outgoing segment a bezier, as a
        Maya out-tangent type replaces a step: on a constant or linear segment
        a handle has no effect.

        Returns:
            ``apply(obj, attr, time, kp)`` for :meth:`edit_key_tangents`.
        """

        def apply(_obj, _attr, _time, kp):
            if "out" in sides and kp.interpolation != "BEZIER":
                kp.interpolation = "BEZIER"
            if tangent == "FLAT":
                handles = [
                    h
                    for side, h in (("in", "handle_left"), ("out", "handle_right"))
                    if side in sides
                ]
                if len(handles) == 2:
                    kp.handle_left_type = kp.handle_right_type = "ALIGNED"
                else:
                    other = (
                        "handle_right" if handles[0] == "handle_left" else "handle_left"
                    )
                    setattr(kp, handles[0] + "_type", "FREE")
                    if getattr(kp, other + "_type") == "ALIGNED":
                        setattr(kp, other + "_type", "FREE")
                for h in handles:
                    setattr(kp, h, (getattr(kp, h)[0], kp.co[1]))
                return
            if "in" in sides:
                kp.handle_left_type = tangent
            if "out" in sides:
                kp.handle_right_type = tangent

        return apply

    @staticmethod
    def interpolation_edit(mode: str) -> Callable:
        """The edit that sets interpolation *mode* (an :attr:`INTERPOLATIONS`
        mode): ``apply(obj, attr, time, kp)``."""

        def apply(_obj, _attr, _time, kp):
            kp.interpolation = mode

        return apply

    @classmethod
    def tangent_lock_edit(cls, lock: bool) -> Callable:
        """The edit that unifies (aligned handles) or breaks (free handles)
        the keys.

        Break makes both sides FREE -- Blender has no lock flag apart from
        the handle type, so FREE is how a broken key stays broken.  Unify
        aligns FREE and VECTOR sides and leaves AUTO / AUTO_CLAMPED alone:
        those are unified by definition, and converting them would freeze
        handles Blender keeps re-deriving (Maya's ``-lock true`` leaves the
        tangent type as it is).
        """
        if not lock:
            return cls.tangent_type_edit("FREE")

        def apply(_obj, _attr, _time, kp):
            for side in ("handle_left_type", "handle_right_type"):
                if getattr(kp, side) in ("FREE", "VECTOR"):
                    setattr(kp, side, "ALIGNED")

        return apply

    @staticmethod
    def handle_drag_edit(vectors: Dict, side: str, broken: bool) -> Callable:
        """The edit a dragged tangent handle asks for: each key's handle on
        *side* lands at its vector (:func:`_place_dragged_handle`).

        *vectors* is ``{(obj, attr, round(time, 6)): (dt, dv)}``, one handle
        vector per key the drag carried; *broken* is the Alt drag.  Keyed on
        the time that ASKED for the point, not on the point's own ``co`` --
        the point is matched within a window, and a float read back off the
        curve need not compare equal to the one the drag reported.
        """

        def apply(obj, attr, time, kp):
            vector = vectors.get((obj, attr, round(float(time), 6)))
            if vector is not None:
                _place_dragged_handle(kp, side, *vector, broken=broken)

        return apply

    @classmethod
    def edit_key_tangents(cls, targets: list, edit) -> int:
        """Run *edit* -- ``apply(obj, attr, time, kp)`` -- on every selected
        keyframe point, one undo step.

        The row and the requested time ride along with the point: a handle
        drag that carried a selection writes a different vector per key, the
        same frame can be a key on two different objects, and the point is
        matched within a window -- so only the caller's own time identifies
        which edit this point is.

        Returns:
            How many keyframe points were edited.
        """
        n = 0
        with CoreUtils.undo_chunk("Key Tangents"):
            for obj, attr, times, _sid in targets:
                for fc in cls.curves_for_attr(obj, attr):
                    kt = AnimUtils.key_times(fc)
                    touched = False
                    for t in times:
                        i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                        for i in range(i0, i1):
                            edit(obj, attr, t, fc.keyframe_points[i])
                            touched = True
                            n += 1
                    if touched:
                        fc.update()
        return n

    @classmethod
    def target_curves(cls, targets: list) -> list:
        """The fcurves behind *targets*' (object, attribute) pairs.

        The attribute-level scope: an edit handed these reaches only the
        channels the user selected.  Each curve once: two segments of one
        channel are one curve, and a simplify handed it twice ran an extra
        pass -- as are linked duplicates' rows, which share one action slot
        (an Invert over both ran twice, mirroring the keys back).
        """
        by_obj: dict = {}
        for obj, attr, _times, _sid in targets:
            by_obj.setdefault(obj, []).append(attr)
        curves: dict = {}
        for obj, attrs in by_obj.items():
            for fc in cls.curves_for_attrs(obj, attrs):
                curves.setdefault(fc.as_pointer(), fc)
        return list(curves.values())

    @classmethod
    def simplify_keys(
        cls, targets: list, span: Tuple[float, float], on_delete=None
    ) -> Tuple[int, int]:
        """Drop the selected keys that do not change the curve's shape.

        The FLAT pass (``get_redundant_flat_keys``) -- the one that matters on
        real footage, because a hold is normally spelled with ``CONSTANT``
        interpolation and a stepped curve is exactly what the reducer will
        not touch -- then the SHAPE pass (``simplify_curve``), both scoped to
        the selected channels' fcurves and, within them, the selected keys.

        Returns:
            ``(flat keys removed, curves reduced)``.
        """
        curves = cls.target_curves(targets)
        flat = AnimUtils.get_redundant_flat_keys(
            curves, remove=True, selected_only=True, time_range=span
        )
        if on_delete is not None:
            for fc, times in flat:
                on_delete(fc, times)
        shaped = AnimUtils.simplify_curve(
            curves, selected_only=True, time_range=span, on_delete=on_delete
        )
        return sum(len(times) for _c, times in flat), len(shaped)

    @classmethod
    def thin_keys(cls, targets: list, span: Tuple[float, float], on_delete=None) -> int:
        """Keep only the first and last key of each selected fcurve.

        The fcurves are passed outright rather than the objects that own
        them: an fcurve IS a channel here, so this is the attribute scope
        mayatk states with ``remove_intermediate_keys(attributes=...)``.

        Returns:
            How many keys were removed.
        """
        return (
            AnimUtils.remove_intermediate_keys(
                cls.target_curves(targets), time_range=span, on_delete=on_delete
            )
            or 0
        )

    @classmethod
    def snap_keys(cls, targets: list, span: Tuple[float, float], on_move=None) -> int:
        """Pull the selected keys off fractional frames onto whole ones.

        Returns:
            How many keys were snapped.
        """
        return (
            AnimUtils.snap_keys(
                cls.target_curves(targets),
                selected_only=True,
                time_range=span,
                on_move=on_move,
            )
            or 0
        )

    @classmethod
    def euler_filter_keys(cls, targets: list) -> list:
        """Unflip the rotation fcurves behind *targets* (whole curves).

        Returns:
            The curves filtered.
        """
        return list(AnimUtils.euler_filter(cls.target_curves(targets)) or [])

    @classmethod
    def invert_keys(cls, targets: list, on_replace=None, on_move=None) -> int:
        """Mirror the selected keys in time, in place: only the SELECTED keys,
        over their combined range -- the keys around them stay put.

        Returns:
            How many keys were mirrored.
        """
        return (
            AnimUtils.invert_keys(
                cls.target_curves(targets),
                mode="time",
                selected_only=True,
                on_replace=on_replace,
                on_move=on_move,
            )
            or 0
        )

    @staticmethod
    def align_keys(targets: list, on_replace=None, on_move=None) -> int:
        """Line the selected keys up on the earliest one's frame.

        Handed a name, ``AnimUtils`` finds no fcurves and silently does
        nothing, so the objects are resolved first (missing ones dropped).

        Returns:
            How many keys were aligned.
        """
        names = dict.fromkeys(obj for obj, _a, _t, _s in targets)
        objects = [o for o in (_object(name) for name in names) if o is not None]
        return (
            AnimUtils.align_selected_keyframes(
                objects, on_replace=on_replace, on_move=on_move
            )
            or 0
        )

    @classmethod
    def copy_keys(cls, sequencer, targets: list) -> dict:
        """The panel's key clipboard for *targets*' keys.

        Each record keeps its ``frame``, ``value`` and :data:`_KEY_SHAPE`.  A
        key the gap hold stepped is read back to its pre-hold interpolation
        (``ShotEditLedger.claimed_step``): the ``CONSTANT`` the hold wrote is
        the shot system's, and a paste carried it as if the animator had
        keyed it (2026-10-10).  Only while the key is still the one the hold
        stepped (``ShotEditLedger.is_same_key``, by value and left handle): a
        key written over the held one since -- a Graph Editor paste, the held
        value with another slope included -- is the animator's, its
        ``CONSTANT`` with it.  A claim made in Maya (a pulled scene) names
        Maya tangent types, read back as the release reads them
        (``ShotSequencer._own_interpolation``).  The curves are read directly:
        no Blender key selection is needed or changed.

        Parameters:
            sequencer: The panel's ``ShotSequencer`` (its ``ledger``).
            targets: ``[(obj, attr, [times], shot_id), ...]``.

        Returns:
            ``{"rows": [(obj, attr, [record, ...]), ...], "start": frame,
            "paths": {(obj, attr): (data_path, index)}}`` -- ``paths`` the
            curve each row was copied from -- or ``{}`` when nothing was
            copied.
        """
        if sequencer is None:
            return {}
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        ledger = sequencer.ledger
        rows, paths = [], {}
        for obj, attr, times, _sid in targets:
            fcurves = cls.curves_for_attr(obj, attr)
            if not fcurves:
                continue
            fc = fcurves[0]
            curve = BlenderShotStore.curve_key(obj, fc.data_path, fc.array_index)
            key_times, points = AnimUtils.key_times(fc), fc.keyframe_points
            keys = []
            for t in times:
                i0, i1 = AnimUtils.window_indices(key_times, t - 1e-4, t + 1e-4)
                if i1 <= i0:
                    continue
                kp = points[i0]
                kd = {
                    "frame": float(kp.co.x),
                    "value": float(kp.co.y),
                    "interpolation": kp.interpolation,
                    "easing": kp.easing,
                    "handle_left_type": kp.handle_left_type,
                    "handle_right_type": kp.handle_right_type,
                    "handle_left": (
                        kp.handle_left.x - kp.co.x,
                        kp.handle_left.y - kp.co.y,
                    ),
                    "handle_right": (
                        kp.handle_right.x - kp.co.x,
                        kp.handle_right.y - kp.co.y,
                    ),
                }
                claim = ledger.claimed_step(curve, kd["frame"])
                if (
                    claim is not None
                    and kd["interpolation"] == "CONSTANT"
                    and ledger.is_same_key(
                        claim, kd["easing"], kd["value"], in_half=kd["handle_left"]
                    )
                ):
                    kd["interpolation"] = sequencer._own_interpolation(claim[1])
                keys.append(kd)
            if keys:
                rows.append((obj, attr, keys))
                paths[(obj, attr)] = (fc.data_path, fc.array_index)
        if not rows:
            return {}
        return {
            "rows": rows,
            "start": min(kd["frame"] for _o, _a, keys in rows for kd in keys),
            "paths": paths,
        }

    @classmethod
    def paste_keys(
        cls, clipboard: dict, pairs: list, now: float, on_replace=None
    ) -> list:
        """Paste :meth:`copy_keys` rows at the playhead.

        Each row lands on the destination's own curve for its attribute; a
        row copied from a curve deleted since lands on the path it was copied
        from.

        Parameters:
            clipboard: What :meth:`copy_keys` returned (``start``, ``paths``).
            pairs: ``[(dest_obj, dest_attr, records), ...]`` --
                ``ptk.ShotSequencer.paste_pairs`` over the clipboard's rows.
            now: The frame the clipboard's earliest key lands on.
            on_replace: ``on_replace(curve, frames)`` for a key a pasted one
                replaces (the store's ``release_replaced``).

        Returns:
            ``[(obj, attr, [landed frame, ...]), ...]`` for the rows that
            keyed anything (a row whose object is gone since the copy keys
            nothing).
        """
        start = clipboard["start"]
        copied_paths = clipboard.get("paths") or {}
        landed = []
        for obj_name, attr, keys in pairs:
            obj = _object(obj_name)
            if obj is None:
                continue
            fcurves = cls.curves_for_attr(obj_name, attr)
            path = (
                (fcurves[0].data_path, fcurves[0].array_index)
                if fcurves
                else next((p for (o, a), p in copied_paths.items() if a == attr), None)
            )
            if path is None:
                continue
            buffer = {
                "mode": "selected",
                "keys": {path: [(kd["frame"], kd["value"]) for kd in keys]},
                "tangents": {path: [{f: kd[f] for f in _KEY_SHAPE} for kd in keys]},
            }
            first = min(kd["frame"] for kd in keys)
            if AnimUtils.paste_keys(
                [obj],
                buffer,
                target_time=now + (first - start),
                on_replace=on_replace,
            ):
                landed.append(
                    (obj_name, attr, [now + kd["frame"] - start for kd in keys])
                )
        return landed

    # ---- selection mirrors, names and the graph view's handle writer -------------

    #: Blender has no counterpart of Maya's Attribute Spread Sheet (its
    #: Spreadsheet editor shows geometry, not object attributes): the track
    #: menu leaves the row out.
    ATTRIBUTE_SPREADSHEET = False

    @staticmethod
    def graph_sync_cls() -> type:
        """The library's ``GraphSync`` (the graph view's host half)."""
        from blendertk.anim_utils.shots.shot_sequencer.graph_sync import GraphSync

        return GraphSync

    @staticmethod
    def object_exists(name: str) -> bool:
        """Whether *name* names an object in the file."""
        try:
            import bpy
        except ImportError:
            return False
        return bool(name) and bpy.data.objects.get(name) is not None

    @staticmethod
    def full_name(name: str) -> str:
        """*name* as the scene names it: Blender object names are flat and
        unique, so a DAG-path-like name (a record written in Maya) maps to its
        leaf, and anything else to itself."""
        return name.rsplit("|", 1)[-1] if "|" in name else name

    @staticmethod
    def undo_disabled():
        """A context in which scene changes record no undo step: Blender pushes
        steps per operator, never for the panel's direct writes, so there is
        nothing to suspend."""
        return contextlib.nullcontext()

    @staticmethod
    def _objects(names) -> list:
        """The ``bpy`` objects named by *names*, in order; missing ones dropped.

        What every helper that reads ``animation_data`` needs: handed a name,
        ``AnimUtils`` finds no fcurves and silently does nothing.
        """
        try:
            import bpy
        except ImportError:
            return []
        objs = (bpy.data.objects.get(name) for name in names)
        return [obj for obj in objs if obj is not None]

    @staticmethod
    def _select_strips(names) -> bool:
        """Select the VSE strips named *names* (the last one active); ``False``
        when none of them is a strip, which leaves the strip selection alone."""
        from blendertk.audio_utils._audio_utils import AudioUtils

        strips = [st for st in (AudioUtils._find_strip(n) for n in names) if st]
        if not strips:
            return False
        ed = AudioUtils.get_sequence_editor()
        for st in ed.strips_all:
            st.select = False
        for st in strips:
            st.select = True
        ed.active_strip = strips[-1]
        return True

    @classmethod
    def mirror_selection(cls, names) -> None:
        """Put *names* on the scene selection as a view mirror (the Outliner
        and the Graph Editor follow it).

        Replaces the selection, the last named active (Maya's lead).  Nothing
        to select leaves the selection alone, as mayatk returns early: a
        click on an audio clip, an audio header or a missing object's clip
        must not wipe what the user had selected.  An audio track's name is
        its VSE strip's, so those names select the strip instead -- mayatk
        selects the audio node behind the track.

        Parameters:
            names: Object (or strip) names.
        """
        try:
            import bpy
        except ImportError:
            return
        from blendertk.core_utils._core_utils import CoreUtils

        names = [n for n in names or () if n]
        if not names:
            return
        # The window's view layer: windowless, context.view_layer addresses
        # the scene's default layer.  Selection state is a view-layer
        # concept, so objects outside it (excluded collection, another scene
        # -- the bpy.data lookup is file-wide) are not this mirror's to pick,
        # and a pick of none of them must not clear the selection.
        with CoreUtils.window_context_override():
            layer = bpy.context.view_layer
            objects = [
                o
                for o in (bpy.data.objects.get(name) for name in names)
                if o is not None and o.name in layer.objects
            ]
            if objects:
                # CoreUtils.select makes its FIRST object active.
                CoreUtils.select(list(reversed(objects)), mode="replace")
                return
            cls._select_strips(names)

    @classmethod
    def select_channels(cls, objects, attrs=()) -> None:
        """Select *attrs*' fcurves on the named objects; no attrs clears the selection.

        Blender has no Channel Box: the channel scope IS the fcurve's own
        ``select`` flag, which the Dope Sheet and Graph Editor show and read.
        Everything on the objects is deselected first so the flag states the
        panel's pick rather than adding to whatever was there.

        Parameters:
            objects: Object NAMES (resolved here: ``AnimUtils.get_fcurves``
                reads ``animation_data`` off an object, so handed a name it
                found no fcurves and the clear was a silent no-op).
            attrs: Channel labels (``translateX``-style); empty clears.
        """
        resolved = cls._objects(objects or ())
        for fc in AnimUtils.get_fcurves(resolved):
            fc.select = False
        for obj in resolved:
            for fc in cls.curves_for_attrs(obj.name, list(attrs or ())):
                fc.select = True

    @classmethod
    def select_keys(cls, rows) -> int:
        """Select exactly the ``(obj, attr, times)`` *rows*' keys; return the
        count.

        Every keyframe point in the scene is deselected first
        (``AnimUtils.clear_key_selection``, as mayatk's twin clears every
        curve) -- an emptied panel selection must not leave keys selected on
        other objects for the next Graph Editor / Dope Sheet operation to act
        on -- handles included, or a stale handle selection outlives the
        edit.

        Parameters:
            rows: ``[(object, attribute, [times]), ...]``; a row without an
                attribute contributes the clear and nothing more.
        """
        try:
            import bpy  # noqa: F401 -- no Blender, no keys to select
        except ImportError:
            return 0
        AnimUtils.clear_key_selection()
        n = 0
        for obj_name, attr_name, times in rows:
            if not attr_name or not times:
                continue  # an object-level row contributes its clear, no more
            for fc in cls.curves_for_attr(obj_name, attr_name):
                kt = AnimUtils.key_times(fc)
                for t in times:
                    i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                    for i in range(i0, i1):
                        fc.keyframe_points[i].select_control_point = True
                        n += 1
        return n

    @staticmethod
    def open_attribute_spreadsheet() -> bool:
        """Maya's Attribute Spread Sheet has no Blender counterpart: ``False``
        (:attr:`ATTRIBUTE_SPREADSHEET` keeps the row out of the menu)."""
        return False

    @classmethod
    def set_dragged_handles(cls, curve, items) -> int:
        """Write dragged tangent handles on *curve*; return how many.

        The graph view's handle writer (``GraphSync.on_tangents_edited``),
        one curve at a time; the track view's handle drag batches through
        :meth:`handle_drag_edit` and places each handle by the same rule.  Each handle is a VECTOR
        from its key in curve units (frames along, value up); a Blender
        handle always carries its length.

        Parameters:
            curve: The FCurve.
            items: ``[(time, side, dt, dv, broken), ...]`` -- *side* ``"in"``
                (``handle_left``) or ``"out"`` (``handle_right``); *broken*
                frees both handles first (the Alt drag).
        """
        if curve is None or not items:
            return 0
        key_times = AnimUtils.key_times(curve)
        points = curve.keyframe_points
        n = 0
        for t, side, dt, dv, broken in items:
            i0, i1 = AnimUtils.window_indices(key_times, t - 1e-3, t + 1e-3)
            for i in range(i0, i1):
                _place_dragged_handle(points[i], side, dt, dv, broken=broken)
                n += 1
        if n:
            curve.update()
        return n
