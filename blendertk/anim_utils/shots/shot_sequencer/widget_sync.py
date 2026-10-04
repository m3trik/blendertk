# !/usr/bin/python
# coding=utf-8
"""Widget sync: rebuilding the sequencer from the scene.

Provides :class:`WidgetSyncMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. The full rebuild
(content, decoration, viewport), the shotless scene-wide view, tracks, clips,
audio tracks and per-attribute sub-rows, the header settings and attribute
colours, and the display-mode toggles that trigger a rebuild.
"""

from collections import defaultdict
from typing import Optional

import pythontk as ptk

from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer.segment_collector import SegmentCollector


class WidgetSyncMixin:
    """Rebuilding the widget from the scene: tracks, clips, sub-rows, decoration."""

    _node_icons_cls_cache = ...  # sentinel — not yet resolved

    @classmethod
    def _try_load_blender_icons(cls):
        """Return :class:`NodeIcons` (``Object.type`` → uitk icon), memoised per process."""
        if cls._node_icons_cls_cache is not ...:
            return cls._node_icons_cls_cache
        try:
            from blendertk.ui_utils.node_icons import NodeIcons

            cls._node_icons_cls_cache = NodeIcons
        except ImportError:
            cls._node_icons_cls_cache = None
        return cls._node_icons_cls_cache

    def _set_view_mode(self, mode: str) -> None:
        """Set the shot display mode and rebuild the widget."""
        self._shot_display_mode = mode
        if self._playback_range_mode != "off":
            self._apply_view_playback_range()
        self._sync_to_widget()

    def _set_playback_range_mode(self, mode: str) -> None:
        """Set the playback-range tracking mode.

        *mode* must be one of ``"off"``, ``"follows_view"``, or
        ``"locked"``.
        """
        self._playback_range_mode = mode
        if mode != "off":
            self._apply_view_playback_range()

    def _set_cmb_mode(self, mode: str) -> None:
        """Switch the combobox between shots and scene markers."""
        self._cmb_mode = mode
        # Keep the mode selector in sync (guard against re-entry)
        cmb_mode = self._cmb_mode_widget
        if cmb_mode is not None:
            idx = 1 if mode == "markers" else 0
            if cmb_mode.currentIndex() != idx:
                cmb_mode.blockSignals(True)
                cmb_mode.setCurrentIndex(idx)
                cmb_mode.blockSignals(False)
        self._sync_combobox()

    def _visible_shots(self, active_shot):
        """Return the shots to render based on ``_shot_display_mode``."""
        if self._shot_display_mode == "current":
            return [active_shot]
        sorted_shots = self.sequencer.sorted_shots()
        if self._shot_display_mode == "all":
            return sorted_shots
        # "adjacent" — previous + current + next
        idx = next(
            (i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id),
            None,
        )
        if idx is None:
            return [active_shot]
        result = []
        if idx > 0:
            result.append(sorted_shots[idx - 1])
        result.append(active_shot)
        if idx < len(sorted_shots) - 1:
            result.append(sorted_shots[idx + 1])
        return result

    def refresh(self) -> None:
        """Clear cached segments and rebuild the sequencer widget."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        self._sync_to_widget()

    def _sync_to_widget(
        self, shot_id: Optional[int] = None, *, frame: bool = False
    ) -> None:
        """Full rebuild: content + decoration + viewport.

        When the display mode is ``"adjacent"`` or ``"all"``, clips from
        non-active shots are also rendered (greyed-out, locked) and their
        ranges are shown as non-interactive overlays.

        Parameters:
            shot_id: Shot to display.  Falls back to :attr:`active_shot_id`.
            frame: If True, reframe the viewport on the active shot.
        """
        widget, shot = self._resolve_sync_target(shot_id)
        if widget is None or shot is None:
            # No shots — try scene-wide display
            widget = self._get_sequencer_widget()
            if (
                widget is not None
                and self.sequencer is not None
                and not self.sequencer.shots
            ):
                self._sync_shotless(widget, frame=frame)
            return

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        visible_shots = self._visible_shots(shot)

        # bulk_updates defers the per-add scene-rect recompute (which
        # walks every clip/marker/gap) to one pass at exit — without it
        # a rebuild is O(n²) in clip count.
        bulk = getattr(widget, "bulk_updates", None)
        if callable(bulk):
            with bulk():
                self._rebuild_content(widget, shot, visible_shots)
                self._rebuild_decoration(widget, shot, visible_shots)
        else:
            self._rebuild_content(widget, shot, visible_shots)
            self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        self._update_footer_shot_summary()

    def _sync_shotless(self, widget, *, frame: bool = False) -> None:
        """Scene-wide animation display when no shots exist."""
        from pythontk.core_utils.engines.shots.shot_model import ShotBlock

        scene = self._scene()
        if scene is None:
            return
        start, end = self._scene_playback_range(scene)
        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        widget.clear()
        self._sync_header_settings(widget)
        if end <= start:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No valid playback range.")
            return
        discovered = self.sequencer._find_keyed_transforms(start, end)
        if not discovered:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No animated objects in scene.")
            return
        scene_shot = ShotBlock(
            shot_id=-1,
            name="Scene",
            start=start,
            end=end,
            objects=sorted(set(discovered)),
        )
        from blendertk.anim_utils.segment_keys import SegmentKeys

        # The synthetic scene_shot (id -1) isn't in the store — collect its
        # motion segments directly (same pipeline as collect_object_segments).
        segments = SegmentKeys.collect_segments(
            scene_shot.objects,
            split_static=True,
            time_range=(start, end),
            ignore_holds=True,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
        )
        segments_by_shot = {scene_shot.shot_id: segments}
        all_objects = set(scene_shot.objects) | {seg["obj"] for seg in segments}
        track_ids = self._build_tracks(
            widget, all_objects, all_objects, active_shot=scene_shot
        )
        self._build_clips(widget, scene_shot, [scene_shot], segments_by_shot, track_ids)
        self._ensure_scene_attr_colors(widget)
        self._build_audio_tracks(widget, scene_shot, [scene_shot])
        widget.set_playhead(scene.frame_current_final)
        widget.set_active_range(start, end)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        n = len(scene_shot.objects)
        self._set_footer(
            f"Scene  {start:.0f}–{end:.0f}  ·  {n} object{'s' if n != 1 else ''}"
        )

    def _resolve_sync_target(self, shot_id=None):
        """Return ``(widget, shot)`` or ``(None, None)`` if unavailable."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return None, None

        if shot_id is None:
            shot_id = self.active_shot_id
        if shot_id is None:
            return None, None

        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return None, None
        return widget, shot

    def _save_viewport_state(self, widget):
        """Capture scroll, zoom, and expanded tracks for later restoration."""
        h_scroll = widget._timeline.horizontalScrollBar().value()
        zoom = widget._timeline.pixels_per_unit
        expanded_names = set()
        for tid in list(widget._expanded_tracks):
            td = widget.get_track(tid)
            if td is not None:
                expanded_names.add(td.name)
        return h_scroll, zoom, expanded_names

    def _rebuild_content(self, widget, shot, visible_shots) -> None:
        """Clear widget and rebuild tracks + clips from segments (expensive)."""
        # Suppress store-event → _sync_to_widget re-entrancy for the
        # entire rebuild.  Both reconciliation and auto-discovery may
        # call store.update_shot(); without this guard each call would
        # trigger a nested _sync_to_widget mid-build → duplicate tracks.
        # Restored, not cleared: a caller that rebuilds from inside its own
        # guard would otherwise have it dropped here, halfway through.
        was_syncing = self._syncing
        self._syncing = True
        try:
            widget.clear(keep_range_highlight=True)
            self._sub_row_cache.clear()
            self._sync_header_settings(widget)

            # Re-resolve any stale DAG paths (e.g. parent renamed) across
            # ALL shots before collecting segments so that global track sets
            # and segment caches never mix old and new paths.  Gated by a
            # dirty flag so pure shot-switches (which can't rename nodes)
            # don't pay the path-resolve cost on every rebuild.
            if self._reconcile_needed:
                if self.sequencer.reconcile_all_shots():
                    self._segment_cache.clear()
                self._reconcile_needed = False

            segments_by_shot, all_objects = SegmentCollector.collect_segments(
                self.sequencer,
                shot,
                visible_shots,
                self._segment_cache,
                self._shifted_out_keys,
                self.logger,
            )

            # When "global" scope is active, expand the object set to include
            # every object across all shots so track positions never shift.
            if self._track_order_scope == "global":
                for s in self.sequencer.sorted_shots():
                    all_objects.update(s.objects)

            active_objects = SegmentCollector.active_object_set(shot, segments_by_shot)
            track_ids = self._build_tracks(
                widget, all_objects, active_objects, active_shot=shot
            )
            self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
            self._ensure_scene_attr_colors(widget)
            self._build_audio_tracks(widget, shot, visible_shots)
        finally:
            self._syncing = was_syncing

    def _rebuild_decoration(self, widget, shot, visible_shots) -> None:
        scene = self._scene()
        current_time = scene.frame_current_final if scene is not None else shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))
        widget.set_active_range(shot.start, shot.end)
        widget.set_range_highlight(shot.start, shot.end)
        all_sorted = self.sequencer.sorted_shots()
        store = self.sequencer.store
        shot_blocks = [
            {
                "id": s.shot_id,
                "name": s.name,
                "start": s.start,
                "end": s.end,
                "active": s.shot_id == shot.shot_id,
            }
            for s in all_sorted
        ]
        widget.set_shot_blocks(shot_blocks)
        for m in self.sequencer.markers:
            widget.add_marker(
                time=m["time"],
                note=m.get("note", ""),
                color=m.get("color"),
                draggable=m.get("draggable", True),
                style=m.get("style", "triangle"),
                line_style=m.get("line_style", "dashed"),
                opacity=m.get("opacity", 1.0),
            )
        for i in range(len(all_sorted) - 1):
            left, right = all_sorted[i], all_sorted[i + 1]
            if right.start - left.end > -0.5:
                locked = store.is_gap_locked(left.shot_id, right.shot_id)
                widget.add_gap_overlay(left.end, right.start, locked=locked)
        # The last shot has no following shot, so the loop above leaves it with
        # no drag handle at its end — the one shot that could not be resized
        # like the others.  A zero-width tail overlay supplies that handle; its
        # left edge IS the shot's end, which on_gap_left_resized already
        # knows how to act on.
        if all_sorted:
            widget.add_gap_overlay(all_sorted[-1].end, all_sorted[-1].end, tail=True)
            # ...and the FIRST shot's start, which no gap precedes either.
            widget.add_gap_overlay(all_sorted[0].start, all_sorted[0].start, head=True)
        for s in all_sorted:
            if s.shot_id != shot.shot_id:
                widget.add_range_overlay(s.start, s.end, color="#000000", alpha=40)

    #: Set by the first :meth:`_restore_viewport`; see its docstring.
    _viewport_framed = False

    def _restore_viewport(self, widget, frame, h_scroll, zoom, expanded_names) -> None:
        """Restore scroll/zoom/expansion and trigger geometry recalculation.

        The FIRST restore always frames: there is no prior view to preserve
        on the first build, and the panel opening on frame 0 of a
        several-thousand-frame scene starts every session by hunting for the
        shot being worked on.  (``SequencerWidget.frame_on_first_show`` does
        the same at show time; whichever runs last frames the same range, so
        the two agree however the panel is brought up.)
        """
        frame = frame or not self._viewport_framed
        self._viewport_framed = True
        if frame:
            widget._timeline._refresh_all()
            widget.frame_shot()
        else:
            widget._timeline._pixels_per_unit = zoom
            widget._timeline._refresh_all()
            widget._timeline.horizontalScrollBar().setValue(h_scroll)

        widget.sub_row_provider = self._provide_sub_rows

        if expanded_names:
            for td in widget.tracks():
                if td.name in expanded_names:
                    widget.expand_track(td.track_id)

    def _sync_header_settings(self, widget) -> None:
        spn_snap = getattr(self.ui, "spn_snap", None)
        if spn_snap is not None:
            widget.snap_interval = float(spn_snap.value())
        # Read on every rebuild so the widget also picks up the value the
        # checkbox restored from settings on load.
        chk_snap_keys = getattr(self.ui, "chk_snap_to_keys", None)
        if chk_snap_keys is not None:
            widget.snap_to_keys = bool(chk_snap_keys.isChecked())
        cmb_overlay = getattr(self.ui, "cmb_shortcut_overlay", None)
        if cmb_overlay is not None:
            # Only a value the widget knows: this runs on EVERY rebuild, and
            # a menu that has not been built yet answers with whatever its
            # placeholder feels like -- which must not take the sync down.
            mode = cmb_overlay.itemData(cmb_overlay.currentIndex())
            if mode in widget.SHORTCUT_OVERLAY_MODES:
                widget.shortcut_overlay_mode = mode
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            stored_gap = self.sequencer.store.gap if self.sequencer else 0
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(stored_gap))
            spn_gap.blockSignals(False)
        if self._color_map_cache is None:
            from uitk import AttributeColorDialog

            self._color_map_cache = AttributeColorDialog.load_color_map(
                ptk.Palette.channels()
            )
        widget.attribute_colors = self._color_map_cache

    _AUTO_PALETTE = [
        "#5B8BD4",
        "#6EBF6E",
        "#D4A65B",
        "#C45C5C",
        "#8E6FBF",
        "#5BBFB4",
        "#BF6E8E",
        "#8EB05B",
    ]

    def _ensure_scene_attr_colors(self, widget) -> None:
        if widget is None:
            return
        from hashlib import md5

        color_map = widget.attribute_colors
        changed = False
        for attr in widget.clip_attributes():
            if attr not in color_map:
                idx = int(md5(attr.encode()).hexdigest(), 16) % len(self._AUTO_PALETTE)
                color_map[attr] = self._AUTO_PALETTE[idx]
                changed = True
        if changed:
            widget.attribute_colors = color_map

    def _build_tracks(
        self, widget, all_objects, active_objects, active_shot=None
    ) -> dict:
        from pythontk import SHOT_PALETTE

        obj_classes = (
            active_shot.classify_objects()
            if active_shot and hasattr(active_shot, "classify_objects")
            else {}
        )
        track_ids: dict = {}
        _NOT_FOUND_COLOR = "#E0A0A0"
        if self._track_order_scope == "global":
            ordered = sorted(all_objects)
        else:
            active = sorted(o for o in all_objects if o in active_objects)
            inactive = sorted(o for o in all_objects if o not in active_objects)
            ordered = active + inactive

        try:
            import bpy

            existing_set = {n for n in ordered if bpy.data.objects.get(n) is not None}
        except ImportError:
            existing_set = set(ordered)

        node_icons_cls = self._try_load_blender_icons()
        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = obj_name in existing_set
            if not exists and not self.sequencer.store.is_object_pinned(obj_name):
                continue
            in_active = obj_name in active_objects
            icon = (
                node_icons_cls.get_icon(obj_name)
                if (node_icons_cls and exists)
                else None
            )
            if not exists and icon is None:
                from uitk.managers.icon_manager import IconManager

                icon = IconManager.get("close", size=(16, 16), color=_NOT_FOUND_COLOR)
            color_kw: dict = {}
            status = obj_classes.get(obj_name, "valid")
            if status != "valid":
                pair = SHOT_PALETTE.get(status)
                if pair is not None:
                    fg, bg = pair[0], pair[1]
                    if bg:
                        color_kw["color"] = bg
                    if fg:
                        color_kw["text_color"] = fg
            tid = widget.add_track(
                obj_name.split("|")[-1],
                icon=icon,
                dimmed=not in_active or not exists,
                italic=not in_active and exists,
                **color_kw,
            )
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        from pythontk import SHOT_PALETTE

        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot.get(vs.shot_id, [])
            obj_classes = (
                vs.classify_objects() if hasattr(vs, "classify_objects") else {}
            )
            by_obj: dict = defaultdict(list)
            for seg in segs:
                by_obj[seg["obj"]].append(seg)
            store = self.sequencer.store if self.sequencer else None

            for obj_name in sorted(set(vs.objects) | set(by_obj)):
                if self.sequencer.is_object_hidden(obj_name):
                    continue
                tid = track_ids.get(obj_name)
                if tid is None:
                    continue
                obj_segs = by_obj.get(obj_name, [])
                if not obj_segs:
                    continue
                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                elif store and obj_name in store.locked_objects:
                    extra = {"locked": True}
                status = obj_classes.get(obj_name, "valid")
                if status != "valid":
                    pair = SHOT_PALETTE.get(status)
                    if pair is not None and pair[0]:
                        extra["status_color"] = pair[0]

                # Merge adjacent segments separated only by flat-key gaps so
                # the main track shows fewer, larger clips.  Stepped
                # (zero-duration) segments are kept separate -- they are point
                # events and must not be absorbed into spans.
                gap = store.detection_threshold if store else 10.0
                span_segs = [sg for sg in obj_segs if not sg.get("is_stepped")]
                stepped_segs = [sg for sg in obj_segs if sg.get("is_stepped")]
                merged = [
                    {
                        "start": cluster[0]["start"],
                        "end": max(sg["end"] for sg in cluster),
                        "segs": cluster,
                    }
                    for cluster in ptk.ShotDetection.cluster_spans(
                        span_segs, gap=gap, inclusive=True
                    )
                ]

                for m in merged:
                    s, e = m["start"], m["end"]
                    attrs = SegmentCollector.extract_attributes(m["segs"])
                    clip_extra = dict(extra)
                    if is_active and attrs:
                        clip_extra["label_center"] = SegmentCollector.abbreviate_attrs(
                            attrs
                        )
                    widget.add_clip(
                        track_id=tid,
                        start=s,
                        duration=e - s,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=s,
                        orig_end=e,
                        attributes=attrs,
                        **clip_extra,
                    )

                # Stepped (zero-duration) clips, one per point event -- a
                # hold-only member's own marks, or the lone key Move to Shot
                # just brought here, so the row shows where it landed.
                for seg in stepped_segs:
                    t = seg["start"]
                    # A stepped key inside a merged span is already covered.
                    if any(m["start"] <= t <= m["end"] for m in merged):
                        self.logger.debug(
                            "[SYNC]   stepped key at %s inside span -- skipped", t
                        )
                        continue
                    widget.add_clip(
                        track_id=tid,
                        start=t,
                        duration=0.0,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=t,
                        orig_end=t,
                        is_stepped=True,
                        stepped_key_time=t,
                        **dict(extra),
                    )

    def _build_audio_tracks(self, widget, shot, visible_shots) -> None:
        """Add one track per VSE sound strip overlapping the visible shots.

        Segments come from :class:`~blendertk.audio_utils.segments.AudioSegment`
        (strip name = ``track_id``); they only change on audio edits, not on
        shot switches, so they're cached by visible range like the Maya original.
        """
        scene_start = min(vs.start for vs in visible_shots)
        scene_end = max(vs.end for vs in visible_shots)
        # The strips this build reads are the baseline an external VSE edit
        # is detected against (``_sound_strips_changed``).
        self._note_sound_strips()
        cache_key = (scene_start, scene_end)
        cached = self._audio_segments_cache
        if cached is not None and cached[0] == cache_key:
            segs = cached[1]
        else:
            try:
                from blendertk.audio_utils.segments import AudioSegment

                segs = AudioSegment.collect_all_segments(
                    scene_start=scene_start, scene_end=scene_end, include_waveform=True
                )
            except Exception:
                # Surfaced, not swallowed: mayatk's rebuild raises here, and a
                # silently missing audio track reads as "no audio".
                self.logger.warning("audio segment collection failed", exc_info=True)
                segs = []
            self._audio_segments_cache = (cache_key, segs)

        by_track: dict = defaultdict(list)
        for seg in segs:
            by_track[seg.track_id].append(seg)
        if not by_track:
            return

        from uitk.managers.icon_manager import IconManager

        for track_id, track_segs in by_track.items():
            if self.sequencer.is_object_hidden(track_id):
                continue
            clip_descs: list = []
            for seg in track_segs:
                for vs in visible_shots:
                    vis_start = max(seg.start, vs.start)
                    vis_end = min(seg.end, vs.end)
                    if vis_end <= vis_start:
                        continue
                    clip_descs.append((seg, vs, vis_start, vis_end))
            if not clip_descs:
                continue

            icon = IconManager.get("activity", size=(16, 16), color="#888888")
            widget_track_id = widget.add_track(track_id, icon=icon)

            for seg, vs, vis_start, vis_end in clip_descs:
                is_active = vs.shot_id == shot.shot_id
                full_waveform = seg.waveform or []
                full_dur = seg.end - seg.start
                if full_waveform and full_dur > 0:
                    n = len(full_waveform)
                    i_lo = int((vis_start - seg.start) / full_dur * n)
                    i_hi = max(i_lo + 1, int((vis_end - seg.start) / full_dur * n))
                    vis_waveform = full_waveform[i_lo:i_hi]
                else:
                    vis_waveform = full_waveform
                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                widget.add_clip(
                    track_id=widget_track_id,
                    start=vis_start,
                    duration=vis_end - vis_start,
                    label=seg.label or track_id,
                    color="#3A7D44",
                    is_audio=True,
                    audio_track_id=seg.track_id,
                    file_path=seg.file_path,
                    waveform=vis_waveform,
                    orig_start=seg.start,
                    orig_end=seg.end,
                    vis_start=vis_start,  # a drag reports where THIS landed
                    shot_id=vs.shot_id,
                    **extra,
                )

    def _provide_sub_rows(self, track_id, track_name):
        """Per-attribute sub-rows: ``[(attr, [(start, dur, label, color, extra)...])...]``.

        Same ``SegmentKeys.collect_segments`` pipeline as the object row (hold
        absorption, hold-only synthesis and motion detection agree between the
        two views); with *Show Internal Holds* on, pure-hold spans are emitted
        with ``is_hold`` so the widget can style them.  Each sub-row also gets a
        full-range background curve preview.
        """
        if self.sequencer is None:
            return []
        shot_id = self.active_shot_id
        shot = self.sequencer.shot_by_id(shot_id) if shot_id is not None else None
        if shot is None:
            return []
        obj_name = self._resolve_full_name(track_name)
        cache_key = (shot_id, track_name)
        cached = self._sub_row_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            import bpy
        except ImportError:
            return []
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return []
        from blendertk.anim_utils.segment_keys import SegmentKeys

        # Every keyed channel on the object, as mayatk lists every animCurve
        # on the node -- custom properties and visibility get rows too.
        all_curves = list(BlenderShotStore.iter_action_fcurves(obj))
        if not all_curves:
            return []

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        show_holds = self._show_internal_holds

        # Channel label → first fcurve (the full-range background preview source).
        attr_to_curve: dict = {}
        for fc in all_curves:
            attr_to_curve.setdefault(SegmentCollector.attr_label(fc), fc)

        store = self.sequencer.store if self.sequencer else None
        is_obj_locked = bool(store and obj_name in store.locked_objects)

        visible = self._visible_shots(shot)
        curve_range_start = min(sh.start for sh in visible)
        curve_range_end = max(sh.end for sh in visible)

        result = []
        for attr_name in sorted(attr_to_curve):
            segs = SegmentKeys.collect_segments(
                [obj_name],
                split_static=True,
                channel_box_attrs=[attr_name],
                ignore_holds=not show_holds,
                ignore_visibility_holds=True,
                motion_only=True,
                motion_rate=1e-3,
                time_range=(shot.start, shot.end),
            )
            if not segs:
                continue

            hold_ranges: set = set()
            if show_holds:
                active_segs = SegmentKeys.collect_segments(
                    [obj_name],
                    split_static=True,
                    channel_box_attrs=[attr_name],
                    ignore_holds=True,
                    ignore_visibility_holds=True,
                    motion_only=True,
                    motion_rate=1e-3,
                    time_range=(shot.start, shot.end),
                )
                active_spans = [(a["start"], a["end"]) for a in active_segs]
                for seg in segs:
                    ss, se = seg["start"], seg["end"]
                    if not any(a_s < se and a_e > ss for a_s, a_e in active_spans):
                        hold_ranges.add((ss, se))

            color = color_map.get(attr_name)
            segments = []
            for seg in segs:
                st, en = seg["start"], seg["end"]
                preview = None
                for crv in seg.get("curves", []):
                    preview = SegmentCollector.build_curve_preview(crv, st, en)
                    if preview:
                        break
                extra = {
                    "obj": obj_name,
                    "attr_name": attr_name,
                    "shot_id": shot_id,
                    "orig_start": st,
                    "orig_end": en,
                    "is_stepped": abs(en - st) < 1e-6,
                    "attributes": [attr_name],
                }
                if preview:
                    extra["curve_preview"] = preview
                if (st, en) in hold_ranges:
                    extra["is_hold"] = True
                if is_obj_locked:
                    extra["locked"] = True
                segments.append((st, max(en - st, 0.0), attr_name, color, extra))
            result.append((attr_name, segments))

        if widget is not None:
            for attr_name, _ in result:
                crv = attr_to_curve.get(attr_name)
                if crv is None:
                    continue
                bg_preview = SegmentCollector.build_curve_preview(
                    crv, curve_range_start, curve_range_end
                )
                hex_color = color_map.get(attr_name, "#CCCCCC")
                widget.set_bg_curve_preview(
                    track_id, attr_name, bg_preview, color=hex_color or "#CCCCCC"
                )

        self._sub_row_cache[cache_key] = result
        return result

    def _set_show_internal_holds(self, enabled: bool) -> None:
        """Toggle flat-key span visibility in attribute sub-rows."""
        self._show_internal_holds = enabled
        self._sub_row_cache.clear()
        self._sync_to_widget()

    # ---- header / transport / toggles ------------------------------------

    def _on_frame_on_shot_change_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.frame_on_shot_change = checked
        self.sequencer.store.mark_dirty()

    def _on_select_on_load_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.select_on_load = checked
        self.sequencer.store.mark_dirty()
        # A cross-scene preference: saved by the panel that changes it (a
        # store save no longer writes the prefs file).
        self.sequencer.store._save_user_prefs()
