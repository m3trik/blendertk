# !/usr/bin/python
# coding=utf-8
"""Clip context menus and clip-level key operations.

Provides :class:`ClipMenuMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. The clip and gap
context menus, Move to Shot, clip locking, and deleting, stashing and
retrieving the keys under clips.
"""

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin


class ClipMenuMixin:
    """Clip and gap context menus, and the clip-level key operations they run."""

    def on_clip_menu(self, menu, clip_id: int) -> None:
        """Add Delete-key + lock actions to a clip's context menu."""
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        clip = widget.get_clip(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        selected_ids = widget.selected_clips() or [clip_id]
        if clip_id not in selected_ids:
            selected_ids = [clip_id]
        multi = len(selected_ids) > 1
        menu.addSeparator()
        # No Delete row: the Delete KEY runs _delete_selected_clip_keys over
        # this same selection, so a row here would duplicate a key every
        # editor already binds.

        # Key stash: park the clips' keys out of the working animation (inert,
        # never exported, retrievable across sessions) — the non-destructive
        # sibling of Delete Keys — and bring stored clips back onto this lane.
        act_store = menu.addAction(
            f"Store Keys ({len(selected_ids)})" if multi else "Store Keys"
        )
        act_store.triggered.connect(lambda: self._stash_clip_keys(selected_ids))
        if obj_name:
            self._add_retrieve_menu(menu, obj_name)
        if obj_name and self.sequencer:
            menu.addSeparator()
            menu.addAction("Lock Others", lambda: self._lock_others(widget, obj_name))
            menu.addAction("Unlock All", lambda: self._unlock_all(widget))

        # "Move to Shot" submenu — anim/audio clips moved as sequences.
        if self.sequencer:
            seqs = self._clips_to_sequences(
                widget, selected_ids, include_read_only=True
            )
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_label = f"Move to Shot ({len(seqs)})" if multi else "Move to Shot"
                # Parent the submenu explicitly: PySide 6.11's ``addMenu(str)`` hands
                # back a wrapper that goes stale once this frame drops it (the C++
                # menu survives, but a later ``action.menu()`` raises).
                from qtpy import QtWidgets

                move_menu = QtWidgets.QMenu(move_label, menu)
                menu.addMenu(move_menu)
                self._populate_move_to_shot(move_menu, seqs)

    def _clips_to_sequences(self, widget, clip_ids, include_read_only=False):
        """Convert widget clip ids to unified sequence dicts.

        A stepped (zero-duration) clip is ONE key and moves as one (the
        key-level ``"times"`` path); a single-attribute sub-row clip moves as
        THAT attribute's sequence (``"attr"``).  Read-only clips (non-active
        visible shots) are skipped unless *include_read_only*, which Move to
        Shot passes -- the engine resolves each sequence's source shot itself.
        Duplicates (one segment spanning several visible shots) are collapsed.
        Mirrors mayatk.
        """
        seqs = []
        seen: set = set()
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            if clip.data.get("read_only") and not include_read_only:
                continue
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None:
                continue
            stepped = bool(clip.data.get("is_stepped"))
            if end <= start and not stepped:
                continue
            attr = None
            if clip.data.get("is_audio"):
                obj, kind = clip.data.get("audio_track_id"), "audio"
            else:
                obj, kind = clip.data.get("obj"), "anim"
                attr = clip.data.get("attr_name") or None
            if not obj:
                continue
            key = (kind, obj, attr, round(start, 6), round(end, 6))
            if key in seen:
                continue
            seen.add(key)
            seq = {"kind": kind, "obj": obj, "start": start, "end": end}
            if attr:
                seq["attr"] = attr
            if stepped:
                seq["times"] = [float(start)]
                seq["end"] = start
            seqs.append(seq)
        return seqs

    #: The two moves an animator makes most: one shot along the sequence.
    _RELATIVE_MOVES = (("Next Shot", "merge_next"), ("Previous Shot", "merge_prev"))

    def _populate_move_to_shot(self, move_menu, seqs: list, noun: str = "clip"):
        """Fill a Move to Shot submenu: the neighbours first, then every shot.

        **Next Shot** and **Previous Shot** head the list so the common move --
        nudging a selection one shot along the sequence -- is always in the
        same place, whatever the shots are called.  They are relative to the
        shot the selection lives in (the active shot when it spans several),
        and each is listed only when that neighbour exists.  Below a
        separator comes every shot by name and range, minus the one the whole
        selection already occupies.

        Parameters:
            move_menu: The submenu to fill.
            seqs: Sequence dicts to move (clips or key selections).
            noun: What the footer counts afterwards -- ``"clip"`` or ``"key"``.
        """
        source_ids = {self.sequencer._source_shot_id_for(sq) for sq in seqs}
        single = next(iter(source_ids)) if len(source_ids) == 1 else None
        anchor = single if single is not None else self.active_shot_id
        near = (
            self._neighbour_shots(anchor)
            if anchor is not None
            else {"merge_prev": None, "merge_next": None}
        )
        entries = [
            (f"{label}  ({near[key].name})", near[key].shot_id)
            for label, key in self._RELATIVE_MOVES
            if near[key] is not None
        ]
        if entries:
            entries.append(None)  # separator
        entries += [
            (f"{sh.name}  [{sh.start:.0f}–{sh.end:.0f}]", sh.shot_id)
            for sh in self.sequencer.sorted_shots()
            if single is None or sh.shot_id != single
        ]
        for entry in entries:
            if entry is None:
                move_menu.addSeparator()
                continue
            label, shot_id = entry
            act = move_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, sid=shot_id: self._move_clips_to_shot(
                    seqs, sid, noun=noun
                )
            )

    def _move_clips_to_shot(self, sequences, dest_shot_id, noun: str = "clip"):
        """Run ``move_sequences_to_shot``, undoable, then refresh.

        Reports the outcome in the footer (mirror of mayatk): the move is a
        no-op whenever every selected sequence already lives in the
        destination — which used to look like the command silently failing.
        *noun* is what the footer counts: a clip menu moves clips, a key menu
        moves keys.
        """
        if self.sequencer is None or not sequences:
            self._set_footer(
                "Move to Shot: nothing movable in the selection.", color="#E0A0A0"
            )
            return
        dest = self.sequencer.shot_by_id(dest_shot_id)
        movable = [
            sq
            for sq in sequences
            if self.sequencer._source_shot_id_for(sq) != dest_shot_id
        ]
        if not movable:
            self._set_footer(
                "Move to Shot: selection is already in "
                f"{dest.name if dest else 'that shot'}.",
                color="#E0A0A0",
            )
            return
        with self.sequencer.store.scene_edit("Move to Shot"):
            self.sequencer.move_sequences_to_shot(movable, dest_shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        n = (
            sum(len(sq.get("times") or ()) for sq in movable)
            if noun == "key"
            else len(movable)
        )
        self._set_footer(
            f"Moved {n} {noun}{'s' if n != 1 else ''} to "
            f"{dest.name if dest else dest_shot_id}"
        )

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Add domain-specific actions to a gap overlay's context menu (none by default)."""

    def _lock_others(self, widget, keep_obj: str) -> None:
        store = self.sequencer.store if self.sequencer else None
        if store is None:
            return
        obj_names = {
            cd.data.get("obj")
            for cd in widget._clips.values()
            if cd.data.get("obj")
            and not getattr(cd, "sub_row", False)
            and not cd.data.get("read_only")
        }
        for o in obj_names:
            if o == keep_obj:
                store.locked_objects.discard(o)
            else:
                store.locked_objects.add(o)
        for cid, cd in list(widget._clips.items()):
            o = cd.data.get("obj")
            if o and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, o != keep_obj)
        self._sub_row_cache.clear()

    def _unlock_all(self, widget) -> None:
        store = self.sequencer.store if self.sequencer else None
        if store is not None:
            store.locked_objects.clear()
        for cid, cd in list(widget._clips.items()):
            if cd.locked and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, False)
        self._sub_row_cache.clear()

    def _delete_clip_keys(self, clip_ids: list) -> None:
        """Delete the given clips' keys within their span.

        A whole-object clip is scoped to the object's TRANSFORM fcurves — the
        same ``_is_transform_path`` filter its span was collected from
        (``_span_segments``) — never every fcurve on the action: custom-property,
        constraint-influence, and modifier curves aren't part of the clip and
        must survive a "Delete Key".
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        try:
            import bpy
        except ImportError:
            return

        deleted = 0
        # Guarded like every other edit path here: removing a keyframe point
        # tags its Action and the depsgraph handler reacts to exactly that.
        # Whether Blender delivers that synchronously is NOT measured (mayatk's
        # equivalent proved to be idle-deferred, 2026-09-11), so this is for
        # consistency with the sibling paths, not a measured saving.
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("Delete Keys") as edit:
                for cid in clip_ids:
                    clip = widget.get_clip(cid)
                    if clip is None or clip.data.get("read_only"):
                        continue
                    obj = bpy.data.objects.get(clip.data.get("obj", ""))
                    if obj is None:
                        continue
                    s, e = clip.data.get("orig_start"), clip.data.get("orig_end")
                    if s is None or e is None:
                        continue
                    for fc in ClipMenuMixin._clip_fcurves(obj, clip):
                        i0, i1 = AnimUtils.window_indices(
                            AnimUtils.key_times(fc), s - 1e-3, e + 1e-3
                        )
                        for i in reversed(range(i0, i1)):
                            fc.keyframe_points.remove(fc.keyframe_points[i])
                            deleted += 1
                        if i1 > i0:
                            fc.update()
                if deleted:
                    # A key edit like any other (``_key_scene_edit``): the claims
                    # on the deleted keys go with them and the gap holds re-settle.
                    self.sequencer.reconcile_system_edits()
                else:
                    edit.cancel()  # nothing happened -- no dead restore point
        finally:
            self._syncing = was_syncing
        if not deleted:
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")

    @staticmethod
    def _clip_fcurves(obj, clip) -> list:
        """The fcurves *clip* stands for on *obj*: its channels, as mayatk scopes a
        clip edit to the clip's ``attributes`` (a sub-row clip: its one
        ``attr_name``).  A clip naming no channel -- a stepped point clip --
        falls back to the object's content fcurves."""
        attrs = list(clip.data.get("attributes") or [])
        if not attrs and clip.data.get("attr_name"):
            attrs = [clip.data["attr_name"]]
        if not attrs:
            return ShotSequencer._transform_fcurves(obj)
        return ClipMotionMixin.curves_for_attrs(obj.name, attrs)

    def _stash_clip_keys(self, clip_ids: list) -> None:
        """Move the given clips' keys into the key stash (``KeyStash.stash``).

        Same scoping as :meth:`_delete_clip_keys` — a whole-object clip is the
        object's TRANSFORM fcurves, a sub-row clip its one attribute, over the
        clip's original span — but the keys are parked, not destroyed: the shot
        block stays as it is and the clip records the shot it came from.
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        try:
            import bpy
        except ImportError:
            return
        jobs = []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None or clip.data.get("read_only"):
                continue
            obj = bpy.data.objects.get(clip.data.get("obj", ""))
            if obj is None:
                continue
            s, e = clip.data.get("orig_start"), clip.data.get("orig_end")
            if s is None or e is None:
                continue
            fcurves = ClipMenuMixin._clip_fcurves(obj, clip)
            if not fcurves:
                continue
            shot_id = clip.data.get("shot_id")
            jobs.append((obj, fcurves, s, e, None if shot_id == -1 else shot_id))
        self._run_stash(jobs)

    def _run_stash(self, jobs: list) -> None:
        """Park ``(obj, fcurves, start, end, shot_id)`` *jobs* in the key stash.

        The half of "Store Keys" that does not depend on where the gesture
        came from, so a clip selection and a key selection reach the stash
        through the same call rather than two copies of this loop.

        ONE clip, however many objects, channels and spans the gesture
        covered.  A stash is the thing the animator put away, and a
        three-channel selection stored as three clips left three rows to
        find, and to retrieve one at a time.  ``KeyStash.stash`` takes the
        whole scope list, so the merge happens where the clip is built
        instead of by stitching clips back together afterwards.

        The shot is recorded only when every job came from the same one:
        a clip that spans two shots belongs to neither.
        """
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        if not jobs or self.sequencer is None:
            return
        shots = {sid for *_scope, sid in jobs if sid is not None}
        try:
            with self.sequencer.store.scene_edit("Store Keys") as edit:
                clip_rec = KeyStash.active().stash(
                    targets=[
                        (obj, fcurves, start, end)
                        for obj, fcurves, start, end, _sid in jobs
                    ],
                    source_shot_id=shots.pop() if len(shots) == 1 else None,
                )
                stored = clip_rec.key_count if clip_rec is not None else 0
                if not stored:
                    edit.cancel()  # nothing happened -- keep the ledger clean
        except Exception:
            # One call for the whole gesture, so a raise means nothing landed
            # and the restore point would "restore" the state we are in.
            self._discard_shot_state()
            raise
        if not stored:
            self._set_footer("No keys to store")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(
            f"Stored {stored} key{'s' if stored != 1 else ''} in the key stash"
        )

    def _add_retrieve_menu(self, menu, obj_name: str) -> None:
        """Append a "Retrieve Stored Keys" submenu listing *obj_name*'s clips."""
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        clips = KeyStash.active().clips_for_object(obj_name)
        if not clips:
            return
        # Parent the submenu explicitly (see the Move-to-Shot note above).
        from qtpy import QtWidgets

        sub = QtWidgets.QMenu("Retrieve Stored Keys", menu)
        menu.addMenu(sub)
        for clip in clips:
            act = sub.addAction(clip.label)
            act.triggered.connect(
                lambda _checked=False, cid=clip.clip_id: self._retrieve_stashed_clip(
                    cid
                )
            )
        # The rows above put a clip straight back on its own frames.  The
        # panel is for everything that needs more than that -- retrieve at a
        # different time, preview before committing, drop a clip -- so it
        # hangs here, under the object that HAS stored keys, rather than
        # adding a second row to the menu root.
        sub.addSeparator()
        sub.addAction("Restore Keys\u2026").triggered.connect(self._open_key_stash)

    def _open_key_stash(self) -> None:
        """Open the Key Stash panel."""
        self.sb.handlers.marking_menu.show("key_stash")

    def _retrieve_stashed_clip(self, clip_id: int) -> None:
        """Put a stored clip back on its original frames (``KeyStash.retrieve``)."""
        if self.sequencer is None:
            return
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        with self.sequencer.store.scene_edit("Retrieve Stored Keys") as edit:
            restored = KeyStash.active().retrieve(clip_id)
            if not restored:
                edit.cancel()  # mayatk discards the dead restore point too
        if not restored:
            self._set_footer("Nothing retrieved — see the console")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(f"Retrieved {restored} key{'s' if restored != 1 else ''}")

    def _delete_selected_clip_keys(self) -> None:
        """Delete selected keyframes or, if none, all keys on selected clips.

        Individual keyframe items are batched into a single undo step so Ctrl+Z
        restores every deleted key at once.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        from uitk import KeyframeItem

        try:
            items = widget._timeline._scene.selectedItems()
        except RuntimeError:
            items = []
        by_clip: dict = {}
        for item in items:
            if isinstance(item, KeyframeItem):
                cid = item._parent_clip._data.clip_id
                by_clip.setdefault(cid, []).append(item._time)

        if by_clip:
            deleted = 0
            # The restore point is pushed BEFORE the edit (``scene_edit``): its
            # reconcile releases the deleted keys' claims, so a point taken
            # after it handed undo a ledger without them.
            # Guarded like every other edit path here: removing a keyframe point
            # tags its Action and the depsgraph handler reacts to exactly that.
            # Whether Blender delivers that synchronously is NOT measured (mayatk's
            # equivalent proved to be idle-deferred, 2026-09-11), so this is for
            # consistency with the sibling paths, not a measured saving.
            was_syncing = self._syncing
            self._syncing = True
            try:
                with self.sequencer.store.scene_edit("Delete Keys") as edit:
                    for clip_id, times in by_clip.items():
                        clip = widget.get_clip(clip_id)
                        if clip is None:
                            continue
                        obj_name = clip.data.get("obj")
                        attr_name = clip.data.get("attr_name")
                        if not obj_name or not attr_name:
                            continue
                        curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
                        for t in times:
                            cut_ok = False
                            for fc in curves:
                                i0, i1 = AnimUtils.window_indices(
                                    AnimUtils.key_times(fc), t - 1e-3, t + 1e-3
                                )
                                for i in reversed(range(i0, i1)):
                                    fc.keyframe_points.remove(fc.keyframe_points[i])
                                    cut_ok = True
                                if i1 > i0:
                                    fc.update()
                            if cut_ok:
                                deleted += 1
                    if deleted:
                        # A key edit like any other (``_key_scene_edit``): the
                        # claims on the deleted keys go with them and the gap
                        # holds re-settle.
                        self.sequencer.reconcile_system_edits()
                    else:
                        edit.cancel()  # nothing happened -- no dead point
            finally:
                self._syncing = was_syncing
            if not deleted:
                return
            shot_id = self.active_shot_id
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget(shot_id=shot_id)
            self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")
            return

        selected = widget.selected_clips() or []
        if selected:
            self._delete_clip_keys(selected)
            return

        # Nothing at all is selected inside the tracks, so Delete is about the
        # SHOT -- the only other thing the panel has selected.  It confirms
        # first, so the key cannot quietly take a shot and its animation.
        block = widget.selected_shot()
        if block is not None and block.get("id") is not None:
            self.delete_shot(block["id"])
