# !/usr/bin/python
# coding=utf-8
"""Tracks and the Blender selection.

Provides :class:`SceneSelectionMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. Hiding, showing
and deleting tracks; mirroring clip, track, channel and key selections onto the
scene (outliner, channels, Graph Editor); the track and header context menus.
"""

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin


class SceneSelectionMixin:
    """Tracks, and the DCC selection that follows the sequencer's."""

    # ---- name resolution (flat in Blender) -------------------------------

    @staticmethod
    def _resolve_full_name(name: str) -> str:
        """Identity — Blender object names are flat and unique.

        Strips the audio-track prefix (``\u266b ``) so an audio track label maps
        back to its strip name, like the Maya original.
        """
        if name.startswith("\u266b "):
            name = name[2:]
        return name.rsplit("|", 1)[-1] if "|" in name else name

    def _select_and_show(self, obj_names) -> None:
        """Select the given objects (Blender Outliner/Graph Editor follow selection)."""
        try:
            import bpy
        except ImportError:
            return
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        active = None
        # Selection state is a view-layer concept: ``select_set`` raises on objects
        # outside the active view layer (excluded collection, another scene — the
        # ``bpy.data.objects`` lookup is scene-wide), so skip those instead of
        # letting one abort the loop mid-way.
        view_layer = bpy.context.view_layer
        for name in obj_names:
            o = bpy.data.objects.get(name)
            if o is not None and o.name in view_layer.objects:
                o.select_set(True)
                active = o
        try:
            bpy.context.view_layer.objects.active = active
        except Exception:
            pass

    def _reveal_in_outliner(self, obj_names) -> None:
        """Select and scroll the Outliner to the object(s)."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner(obj_names)

    def _open_spreadsheet(self, track_names) -> None:
        """Maya's Attribute Spreadsheet has no direct Blender analogue — no-op."""
        self._set_footer("Attribute spreadsheet is Maya-only.")

    # ---- widget signal handlers (non-mixin) ------------------------------

    def hide_track(self, track_names) -> None:
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            self.sequencer.set_object_hidden(self._resolve_full_name(name), True)
        self._sync_to_widget()

    def show_track(self, track_name: str) -> None:
        if self.sequencer is None:
            return
        self.sequencer.set_object_hidden(track_name, False)
        self._sync_to_widget()

    def delete_track(self, track_names) -> None:
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            self.sequencer.store.remove_object_from_shots(self._resolve_full_name(name))
        self._sync_to_widget()

    def on_selection_changed(self, clip_ids: list) -> None:
        """Select the clicked clips' objects, and their CHANNEL if they name one.

        A sub-row clip means one channel, exactly as picking it in the Dope
        Sheet does; an object row means the whole object and clears the
        channel selection rather than listing the object's channels, so a
        mixed selection is object-scoped.
        """
        if not clip_ids or self._syncing:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        resolved, labels = [], []
        chan_attrs, whole_object = [], False
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            obj = clip.data.get("obj")
            if not obj:
                continue
            resolved.append(self._resolve_full_name(obj))
            attr_name = clip.data.get("attr_name")
            if attr_name:
                if attr_name not in chan_attrs:
                    chan_attrs.append(attr_name)
            else:
                whole_object = True
            attrs = clip.data.get("attributes") or ([attr_name] if attr_name else [])
            start, end = clip.data.get("orig_start"), clip.data.get("orig_end")
            parts = [obj]
            if attrs:
                parts.append(", ".join(a for a in attrs[:3] if a))
            if start is not None and end is not None:
                parts.append(f"{start:.0f}–{end:.0f} ({int(end - start)}f)")
            labels.append(" · ".join(parts))
        self._select_and_show(resolved)
        if chan_attrs or whole_object:  # something addressable was clicked
            self._select_channels(resolved, () if whole_object else chan_attrs)
        if labels:
            self._set_footer(
                "  |  ".join(labels[:3])
                + (f"  (+{len(labels) - 3} more)" if len(labels) > 3 else "")
            )

    def on_track_selected(self, track_names: list) -> None:
        if not track_names:
            return
        names = [self._resolve_full_name(n) for n in track_names]
        self._select_and_show(names)
        self._select_channels(names)  # a header label is the whole object

    def on_sub_track_selected(self, rows: list) -> None:
        """Select a channel when its sub-row label is clicked in the header.

        ``rows`` is ``[(track_name, attr_name), ...]``.  Mirror of mayatk's
        handler; where that one highlights the Channel Box, this selects the
        fcurve CHANNELS, which is what the Dope Sheet and Graph Editor scope
        their own operations by.
        """
        if not rows:
            return
        objs, attrs = [], []
        for track_name, attr_name in rows:
            full = self._resolve_full_name(track_name)
            if full not in objs:
                objs.append(full)
            if attr_name and attr_name not in attrs:
                attrs.append(attr_name)
        self._select_and_show(objs)
        self._select_channels(objs, attrs)
        shown = ", ".join(attrs[:6]) + (f" +{len(attrs) - 6}" if len(attrs) > 6 else "")
        self._set_footer(
            f"{len(attrs)} channel{'s' if len(attrs) != 1 else ''}: {shown}"
        )

    def _select_channels(self, obj_names, attrs=()) -> None:
        """Select *attrs*' fcurves on the named objects; no attrs clears the selection.

        Blender has no Channel Box: the channel scope IS the fcurve's own
        ``select`` flag, which the Dope Sheet and Graph Editor show and read.
        Everything on the objects is deselected first so the flag states the
        panel's pick rather than adding to whatever was there.

        *obj_names* are names, what every caller holds (``_resolve_full_name``),
        resolved to objects once here: ``AnimUtils.get_fcurves`` reads
        ``animation_data`` off an object, so handed a name it found no fcurves
        and the clear was a silent no-op.
        """
        from blendertk.anim_utils.shots.shot_sequencer.clip_motion import (
            ClipMotionMixin,
        )

        try:
            import bpy
        except ImportError:
            return
        objects = [
            obj
            for obj in (bpy.data.objects.get(name) for name in obj_names)
            if obj is not None
        ]
        for fc in AnimUtils.get_fcurves(objects):
            fc.select = False
        for obj in objects:
            for attr in attrs:
                for fc in ClipMotionMixin.curves_for_attr(obj.name, attr):
                    fc.select = True

    def on_clip_locked(self, clip_id: int, locked: bool) -> None:
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clip = widget._clips.get(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        if not obj_name:
            return
        store = self.sequencer.store
        if locked:
            store.locked_objects.add(obj_name)
        else:
            store.locked_objects.discard(obj_name)
        for cid, cd in widget._clips.items():
            if cd.data.get("obj") == obj_name:
                widget.set_clip_locked(cid, locked)
        self._sub_row_cache.clear()

    def on_track_menu(self, menu, track_names) -> None:
        if not track_names:
            return
        try:
            import bpy
        except ImportError:
            return
        menu.addSeparator()
        resolved = [
            full
            for full in (self._resolve_full_name(n) for n in track_names)
            if bpy.data.objects.get(full) is not None
        ]
        if resolved:
            menu.addAction(
                "Reveal in Outliner",
                lambda objs=list(resolved): self._reveal_in_outliner(objs),
            )
        # Offered for every track, resolved or not -- pasting the name of an
        # object the scene no longer holds is exactly how it gets found again.
        shorts = [self._resolve_full_name(n) for n in track_names]
        copy_label = (
            f"Copy '{shorts[0]}' to Clipboard"
            if len(shorts) == 1
            else f"Copy {len(shorts)} Names to Clipboard"
        )
        menu.addAction(copy_label, lambda n=list(shorts): self._copy_names(n))

    @staticmethod
    def _copy_names(names) -> None:
        """Put the given object names on the clipboard, one per line."""
        from qtpy import QtWidgets

        QtWidgets.QApplication.clipboard().setText("\n".join(names))

    def on_header_menu(self, menu) -> None:
        """Header background context menu — no domain actions this phase."""

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Renaming a clip is display-only in Blender (object names own identity)."""

    def _select_target_keys(self, targets: list) -> None:
        """Put the key menu's *targets* on the Graph Editor selection.

        The edits offered there read that selection rather than taking a
        range (``invert_keys``, ``align_selected_keyframes``,
        ``copy_keys(mode="selected")``, ``snap_keys(selected_only)``), and the
        reaction that keeps it in step is skipped while the panel is
        rebuilding -- so it can be a rebuild out of date by the time a menu
        opens on it.  Asserted from *targets* rather than the raw groups:
        that is already the resolved, writable selection the menu was built
        from.
        """
        self._apply_key_selection([(o, a, t) for o, a, t, _s in targets])

    @staticmethod
    def _apply_key_selection(rows) -> int:
        """Select exactly the ``(obj, attr, times)`` *rows*' keys; return the
        count.  Every keyframe point on those objects is deselected first --
        handles included, or a stale handle selection outlives the edit."""
        try:
            import bpy
        except ImportError:
            return 0
        for obj_name in dict.fromkeys(o for o, _a, _t in rows):
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                n = len(fc.keyframe_points)
                if not n:
                    continue
                off = [False] * n
                for prop in (
                    "select_control_point",
                    "select_left_handle",
                    "select_right_handle",
                ):
                    fc.keyframe_points.foreach_set(prop, off)
        n = 0
        for obj_name, attr_name, times in rows:
            if not attr_name or not times:
                continue  # an object-level row contributes its clear, no more
            for fc in ClipMotionMixin.curves_for_attr(obj_name, attr_name):
                kt = AnimUtils.key_times(fc)
                for t in times:
                    i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                    for i in range(i0, i1):
                        fc.keyframe_points[i].select_control_point = True
                        n += 1
        return n

    def on_key_selection_changed(self, key_groups: list) -> None:
        """Sync the Graph Editor's key selection to match the sequencer.

        *key_groups* is ``[{clip_id, times}, ...]`` — one entry per clip with
        selected keyframe items.  Every keyframe point on the object's
        transform fcurves is deselected first, then the named times are
        selected on the clip's attribute curves (mirror of ``cmds.selectKey``).
        """
        if self._syncing:
            # During a rebuild the scene selection empties as items are torn
            # down.  Mirroring that would clear the user's Graph Editor key
            # selection on every refresh.
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        rows = []
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            attr_name = clip.data.get("attr_name")
            if not obj_name:
                continue
            # An object-level row carries no attribute; it still contributes
            # its object, whose points must be cleared with the rest.
            rows.append((obj_name, attr_name, group["times"] if attr_name else []))
        n = self._apply_key_selection(rows)
        if n:
            self._set_footer(f"{n} key{'s' if n != 1 else ''} selected")
