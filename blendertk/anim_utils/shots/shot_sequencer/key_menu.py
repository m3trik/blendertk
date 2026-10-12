# !/usr/bin/python
# coding=utf-8
"""Key context menus and key-selection edits.

Provides :class:`KeyMenuMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. The key menu
(handle types, interpolation, break/unify, Move / Copy to Shot), dragged handles, and
the edits run over a key selection: simplify, thin, snap, invert, align, copy /
paste and stash.
"""

import pythontk as ptk

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from blendertk.core_utils._core_utils import CoreUtils


class KeyMenuMixin:
    """Key context menus, tangent edits and drags, and the key-selection edits."""

    # -- key context menu (mirror of mayatk; Blender-idiomatic entries) -----

    #: Handle types a key's context menu offers: ``(label, handle type)``.
    #: Blender's own names (the concept diverges from Maya's tangent types);
    #: ``FLAT`` is not a Blender type but Maya's flat tangent -- a horizontal
    #: handle, written by :meth:`_set_key_tangents`.  Maya's step is the
    #: Interpolation submenu's Constant.
    _TANGENT_TYPES = (
        ("Free", "FREE"),
        ("Aligned", "ALIGNED"),
        ("Vector", "VECTOR"),
        ("Automatic", "AUTO"),
        ("Auto Clamped", "AUTO_CLAMPED"),
        ("Flat", "FLAT"),
    )

    #: Interpolation modes: ``(label, interpolation)``.
    _INTERPOLATIONS = (
        ("Constant", "CONSTANT"),
        ("Linear", "LINEAR"),
        ("Bezier", "BEZIER"),
    )

    def _key_targets(self, widget, key_groups: list) -> list:
        """``[(obj, attr, [times], shot_id), ...]`` for a key selection."""
        targets = []
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None or clip.data.get("read_only"):
                continue
            obj = clip.data.get("obj")
            attr = clip.data.get("attr_name")
            times = sorted(group.get("times") or [])
            if not obj or not attr or not times:
                continue
            targets.append((obj, attr, times, clip.data.get("shot_id")))
        return targets

    @staticmethod
    def _key_targets_to_sequences(targets: list) -> list:
        """The key-level sequence dicts ``move_sequences_to_shot`` takes."""
        return [
            {
                "kind": "anim",
                "obj": obj,
                "attr": attr,
                "times": list(times),
                "start": times[0],
                "end": times[-1],
            }
            for obj, attr, times, _sid in targets
        ]

    def on_key_menu(self, menu, key_groups: list) -> None:
        """Add the key actions to a key's context menu.

        Handle types for both sides (Tangents), one side (In/Out), the
        interpolation mode, Break / Unify (free vs aligned handles), the
        Animation panel's key edits (:meth:`_add_key_edit_actions`), and the
        sequencer's own Move / Copy to Shot for exactly the selected keys, per
        attribute.  The widget appends Delete.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        targets = self._key_targets(widget, key_groups)
        if not targets:
            return
        from qtpy import QtWidgets

        n = sum(len(t) for _o, _a, t, _s in targets)
        suffix = f" ({n})" if n > 1 else ""

        for label, sides in (
            ("Tangents", ("in", "out")),
            ("In Tangent", ("in",)),
            ("Out Tangent", ("out",)),
        ):
            sub = QtWidgets.QMenu(label, menu)
            menu.addMenu(sub)
            for name, handle in self._TANGENT_TYPES:
                act = sub.addAction(name)
                act.triggered.connect(
                    lambda _checked=False, h=handle, s=sides: self._set_key_tangents(
                        targets, h, s
                    )
                )
        interp = QtWidgets.QMenu("Interpolation", menu)
        menu.addMenu(interp)
        for name, mode in self._INTERPOLATIONS:
            act = interp.addAction(name)
            act.triggered.connect(
                lambda _checked=False, m=mode: self._set_key_interpolation(targets, m)
            )
        act_break = menu.addAction(f"Break Tangents{suffix}")
        act_break.triggered.connect(lambda: self._lock_key_tangents(targets, False))
        act_unify = menu.addAction(f"Unify Tangents{suffix}")
        act_unify.triggered.connect(lambda: self._lock_key_tangents(targets, True))

        self._add_selection_key_actions(menu, targets, suffix)

    def _add_selection_key_actions(self, menu, targets: list, suffix: str = "") -> None:
        """The key menu's rows past the tangents -- the key edits
        (:meth:`_add_key_edit_actions`) and Move / Copy to Shot -- for a
        selection from any view: the graph's menu offers them too."""
        self._add_key_edit_actions(menu, targets, suffix)

        if self.sequencer:
            seqs = self._key_targets_to_sequences(targets)
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                self._add_send_to_shot_menu(menu, seqs, noun="key", suffix=suffix)

    #: The key edits offered under the key menu's Edit row, as
    #: ``(label, method name)``.  Declared rather than inlined so the two
    #: forks can be read side by side: these five are spelled identically in
    #: both -- a test on each side pins the LIST, so a row added to one
    #: fork and not the other fails on the side that drifted -- unlike
    #: the tangent rows above them.
    _KEY_EDITS = (
        ("Simplify", "_simplify_selected_keys"),
        ("Remove Intermediate Keys", "_thin_selected_keys"),
        ("Snap Fractional Keys", "_snap_selected_keys"),
        ("Invert Keys", "_invert_selected_keys"),
        ("Align Keys", "_align_selected_keys"),
        ("Euler Filter", "_euler_filter_selected_keys"),
    )

    def _add_key_edit_actions(self, menu, targets, suffix) -> None:
        """Append the stash and Edit rows to the key menu.

        Everything here is scoped to the keys actually SELECTED -- the objects
        they belong to and the span they cover -- which is what makes them
        safe to offer at all: the same verbs applied to a whole shot reached
        every member's every fcurve.

        Copy and Paste are NOT rows: they are the panel's ``Ctrl+C`` /
        ``Ctrl+V`` (see ``_copy_keys_shortcut``).
        """
        from qtpy import QtWidgets

        menu.addSeparator()
        act_store = menu.addAction(f"Store Keys{suffix}")
        act_store.triggered.connect(lambda: self._stash_key_targets(targets))

        edit = QtWidgets.QMenu("Edit", menu)
        menu.addMenu(edit)
        for label, method in self._KEY_EDITS:
            act = edit.addAction(label)
            act.triggered.connect(
                lambda _checked=False, m=method: getattr(self, m)(targets)
            )

    @staticmethod
    def _target_objects(targets: list) -> list:
        """*targets*' objects, de-duplicated, in the order they appear."""
        return list(dict.fromkeys(obj for obj, _a, _t, _s in targets))

    @staticmethod
    def _target_curves(targets: list) -> list:
        """The fcurves behind *targets*' (object, attribute) pairs.

        The attribute-level scope: an edit handed these reaches only the
        channels the user selected, where one handed :meth:`_target_objects`
        reaches every channel those objects carry.
        """
        from blendertk.anim_utils.shots.shot_sequencer.clip_motion import (
            ClipMotionMixin,
        )

        by_obj: dict = {}
        for obj, attr, _times, _sid in targets:
            by_obj.setdefault(obj, []).append(attr)
        # Each curve once (mayatk de-dupes): two segments of one channel are
        # one curve, and a simplify handed it twice ran an extra pass -- as are
        # linked duplicates' rows, which share one action slot (an Invert over
        # both ran twice, mirroring the keys back).
        curves: dict = {}
        for obj, attrs in by_obj.items():
            for fc in ClipMotionMixin.curves_for_attrs(obj, attrs):
                curves.setdefault(fc.as_pointer(), fc)
        return list(curves.values())

    @staticmethod
    def _target_span(targets: list) -> tuple:
        """The frame range *targets* covers, end to end."""
        times = [t for _o, _a, ts, _s in targets for t in ts]
        return (min(times), max(times))

    def _key_scene_edit(self, label: str, fn, shot_id=None):
        """Run *fn* as ONE undoable scene edit, then rebuild.

        The bracket every key edit needs and none of them should re-state.
        ``_syncing`` is held throughout so our own writes do not re-arm the
        keyframe debounce and rebuild underneath us.

        A raise keeps the restore point (mirror of mayatk's): ``scene_edit``
        has pushed and tagged its step by then, so the keys *fn* wrote before
        raising undo with it, and the bounds and claims have to come back with
        them -- a dropped point left them as the failed edit made them.
        """
        if self.sequencer is None:
            return None
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit(label):
                result = fn()
                self.sequencer.reconcile_system_edits()
        finally:
            self._syncing = was_syncing
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        return result

    def _key_selection_edit(self, targets, label: str, fn):
        """Run ``fn(objects, span)`` over a key selection; ``(ran, result)``.

        Two return values because these engine calls disagree about what to
        report -- a count, a list, nothing at all -- so "it ran" cannot be
        read off the result.
        """
        if not targets or self.sequencer is None:
            return False, None
        self._select_target_keys(targets)
        objects = self._target_objects(targets)
        span = self._target_span(targets)
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        result = self._key_scene_edit(label, lambda: fn(objects, span), shot_id=shot_id)
        return True, result

    def _simplify_selected_keys(self, targets) -> None:
        """Drop the selected keys that do not change the curve's shape.

        Both of the optimizer's middle passes, aimed at a key selection
        instead of a scene: the FLAT pass (``get_redundant_flat_keys``), which
        is the one that matters on real footage because a hold is normally
        spelled with ``CONSTANT`` interpolation and a stepped curve is exactly
        what the reducer will not touch, then the SHAPE pass
        (``simplify_curve``).  Both are scoped to the selected channels'
        fcurves and, within them, the selected keys, so neither reaches the
        channels beside the one the user highlighted and the selection's ends
        survive.  Mirror of mayatk's ``_simplify_selected_keys``.

        A key either pass removes takes its claims with it, as a replaced one
        does (``ShotStore.release_replaced``).
        """
        curves = self._target_curves(targets)

        def _run(objects, span):
            released = self._claims_follow(objects)["on_replace"]
            flat = AnimUtils.get_redundant_flat_keys(
                curves, remove=True, selected_only=True, time_range=span
            )
            for fc, times in flat:
                released(fc, times)
            shaped = AnimUtils.simplify_curve(
                curves, selected_only=True, time_range=span, on_delete=released
            )
            return sum(len(times) for _c, times in flat), len(shaped)

        ran, counts = self._key_selection_edit(targets, "Simplify", _run)
        if not ran:
            return
        n_flat, n_shaped = counts or (0, 0)
        if n_flat or n_shaped:
            parts = []
            if n_flat:
                parts.append(f"{n_flat} flat key{'s' if n_flat != 1 else ''}")
            if n_shaped:
                parts.append(f"{n_shaped} curve{'s' if n_shaped != 1 else ''} reduced")
            self._set_footer("Simplified: " + ", ".join(parts))
        else:
            self._set_footer(
                "Nothing to simplify — every selected key carries value or shape"
            )

    def _thin_selected_keys(self, targets) -> None:
        """Keep only the first and last key of each selected fcurve.

        The fcurves are passed outright rather than the objects that own them:
        an fcurve IS a channel here, so this is the attribute scope mayatk
        states with ``remove_intermediate_keys(attributes=...)``.  A key it
        removes takes its claims with it, as a replaced one does.
        """
        curves = self._target_curves(targets)
        ran, n = self._key_selection_edit(
            targets,
            "Remove Intermediate Keys",
            lambda objects, span: AnimUtils.remove_intermediate_keys(
                curves,
                time_range=span,
                on_delete=self._claims_follow(objects)["on_replace"],
            ),
        )
        if ran:
            self._set_footer(
                f"Removed {n or 0} intermediate key{'s' if n != 1 else ''}"
            )

    def _snap_selected_keys(self, targets) -> None:
        """Pull the selected keys off fractional frames onto whole ones."""
        curves = self._target_curves(targets)
        ran, n = self._key_selection_edit(
            targets,
            "Snap Keys",
            # A snap never replaces a key (a taken target is skipped); the keys
            # it moves carry their claims along.
            lambda objects, span: AnimUtils.snap_keys(
                curves,
                selected_only=True,
                time_range=span,
                on_move=self._claims_follow(objects)["on_move"],
            ),
        )
        if ran:
            self._set_footer(
                f"Snapped {n or 0} key{'s' if n != 1 else ''} to whole frames"
            )

    def _euler_filter_selected_keys(self, targets) -> None:
        """Unflip the selected channels' rotation curves (``AnimUtils.euler_filter``).

        Whole curves, as the Graph Editor's Curves > Euler Filter filters
        them: unwrapping a span alone would leave a new jump at its edge.
        Values only -- no key moves, so no claim changes hands.
        """
        curves = self._target_curves(targets)
        ran, done = self._key_selection_edit(
            targets,
            "eulerfilter",
            lambda _objects, _span: AnimUtils.euler_filter(curves),
        )
        if ran:
            n = len(done or ())
            self._set_footer(
                f"Euler filter: {n} rotation curve{'s' if n != 1 else ''}"
                if n
                else "Euler filter: no rotation curve among the selected keys"
            )

    def _claims_follow(self, objects) -> dict:
        """The ``on_replace`` / ``on_move`` a key edit over *objects* takes: a
        key it REPLACES takes its claims with it (``ShotStore.release_replaced``,
        as every key the system cuts does, ``ShotSequencer._overwrite_landed``)
        and a key it MOVES carries them along (``ShotStore.remap_moved``, as the
        sequencer's own moves do).  Mirror of mayatk's key menu."""
        on_replace, on_move = self.sequencer.store.claim_hooks(objects)
        return {"on_replace": on_replace, "on_move": on_move}

    def _invert_selected_keys(self, targets) -> None:
        """Mirror the selected keys in time, in place.

        Only the SELECTED keys, over their combined range (mayatk's invert
        prefers the Graph Editor selection): the keys around them stay put.
        A key the mirror lands on is replaced and a key it moves carries its
        claims along (:meth:`_claims_follow`).
        """
        curves = self._target_curves(targets)
        ran, n = self._key_selection_edit(
            targets,
            "Invert Keys",
            lambda objects, _span: AnimUtils.invert_keys(
                curves,
                mode="time",
                selected_only=True,
                **self._claims_follow(objects),
            ),
        )
        if ran:
            self._set_footer(
                f"Inverted {n} key{'s' if n != 1 else ''}" if n else "Nothing to invert"
            )

    def _align_selected_keys(self, targets) -> None:
        """Line the selected keys up on the earliest one's frame (claims follow
        as Invert's do, :meth:`_claims_follow`)."""
        ran, n = self._key_selection_edit(
            targets,
            "Align Keys",
            lambda objects, _span: AnimUtils.align_selected_keyframes(
                self._resolve_objects(objects), **self._claims_follow(objects)
            ),
        )
        if ran:
            self._set_footer(
                f"Aligned {n} key{'s' if n != 1 else ''}" if n else "Nothing to align"
            )

    #: ``{editor name: callable() -> targets}`` -- how a key shortcut finds
    #: the selection while a view other than the track rows shows
    #: (:meth:`register_key_targets`).  Replaced, never mutated in place:
    #: the class default is shared.
    _key_target_sources: dict = {}

    def register_key_targets(self, editor_name: str, source) -> None:
        """Resolve the key shortcuts' targets through *source* while the
        editor *editor_name* shows (``None`` drops it).

        *source* returns ``[(obj, attr, [times], shot_id), ...]``, the shape
        :meth:`_key_targets` reads from the track rows: the graph's
        ``GraphSync`` registers its curve-keyed selection here, so Ctrl+C /
        Ctrl+V act on what the graph has selected.
        """
        sources = dict(self._key_target_sources)
        if source is None:
            sources.pop(editor_name, None)
        else:
            sources[editor_name] = source
        self._key_target_sources = sources

    def _selected_key_targets(self) -> list:
        """The key menu's ``targets`` for whatever is selected right now --
        what a SHORTCUT has to resolve for itself."""
        widget = self._get_sequencer_widget()
        if widget is None:
            return []
        editor = getattr(widget, "active_editor", None)
        source = self._key_target_sources.get(getattr(editor, "name", None))
        if source is not None:
            return source()
        return self._key_targets(widget, widget.selected_keys())

    def _copy_keys_shortcut(self) -> None:
        """Ctrl+C over the sequencer: copy the selected keys."""
        self._copy_selected_keys(self._selected_key_targets())

    def _paste_keys_shortcut(self) -> None:
        """Ctrl+V over the sequencer: paste them at the playhead."""
        self._paste_selected_keys(self._selected_key_targets())

    #: The panel's key clipboard (:meth:`_copy_selected_keys`, mirror of
    #: mayatk's): ``{"rows": [(obj, attr, [key dict, ...]), ...], "start":
    #: frame, "paths": {(obj, attr): (data_path, index)}}``.  On the class, so
    #: a slots re-init -- a fresh controller -- keeps it; it outlives the
    #: scene (a row whose object is gone pastes nothing).
    _key_clipboard: dict = {}

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

    def _copy_selected_keys(self, targets) -> None:
        """Copy the selected keys onto the panel's own clipboard, one row per
        attribute row (mirror of mayatk's).

        Its own, not Blender's: each key is copied as the animator left it --
        a seam the gap hold made ``CONSTANT`` with the interpolation it had
        before (:meth:`_capture_key_rows`) -- keeping its value, interpolation,
        easing, handle types and handles.  The curves are read directly: no
        Blender key selection is needed or changed, and nothing is undone.
        """
        if not targets or self.sequencer is None:
            return
        rows, paths = self._capture_key_rows(targets)
        if not rows:
            self._set_footer("Nothing to copy")
            return
        type(self)._key_clipboard = {
            "rows": rows,
            "start": min(kd["frame"] for _o, _a, keys in rows for kd in keys),
            "paths": paths,
        }
        n = sum(len(keys) for _o, _a, keys in rows)
        self._set_footer(f"Copied {n} key{'s' if n != 1 else ''}")

    def _capture_key_rows(self, targets) -> tuple:
        """``(rows, paths)`` for *targets*' keys: ``rows`` ``[(obj, attr,
        [key dict, ...]), ...]`` -- each key's ``frame``, ``value`` and
        :attr:`_KEY_SHAPE` -- and ``paths`` ``{(obj, attr): (data_path,
        index)}``, the curve each row was copied from.

        A key the gap hold stepped is read back to its pre-hold interpolation
        (``ShotEditLedger.claimed_step``): the ``CONSTANT`` the hold wrote is
        the shot system's, and a paste carried it as if the animator had keyed
        it (2026-10-10).  Only while the key is still the one the hold stepped
        (``ShotEditLedger.is_same_key``, by value and left handle): a key
        written over the held one since -- a Graph Editor paste, the held
        value with another slope included -- is the animator's, its
        ``CONSTANT`` with it.  Mirror of mayatk's.  A claim made in Maya (a
        pulled scene) names Maya tangent types, read back as the release
        reads them (``ShotSequencer._own_interpolation``).
        """
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        ledger = self.sequencer.ledger
        rows, paths = [], {}
        for obj, attr, times, _sid in targets:
            fcurves = ClipMotionMixin.curves_for_attr(obj, attr)
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
                    kd["interpolation"] = self.sequencer._own_interpolation(claim[1])
                keys.append(kd)
            if keys:
                rows.append((obj, attr, keys))
                paths[(obj, attr)] = (fc.data_path, fc.array_index)
        return rows, paths

    def _paste_selected_keys(self, targets) -> None:
        """Paste the panel's clipboard at the playhead (:meth:`_copy_selected_keys`,
        mirror of mayatk's).

        The earliest copied key lands on the playhead and every other keeps
        its distance from it, across rows.  The destination is the rows the
        selected keys are on (``ShotSequencer.paste_pairs``: a lone row onto
        each, else each row onto its attribute's), or -- nothing selected --
        the rows the keys were copied from.  A pasted key replaces the one on
        its frame, which takes its claims with it; the rest of the curve
        stays.  One undo step; the pasted keys are selected after it.
        Blender's own key clipboard is never read, as mayatk's panel never
        reads Maya's.
        """
        clip = type(self)._key_clipboard
        if not clip:
            self._set_footer(
                "Nothing copied here \u2014 Ctrl+C keys in the sequencer first"
            )
            return
        if self.sequencer is None:
            return
        try:
            import bpy
        except ImportError:
            return
        rows = clip["rows"]
        if targets:
            destinations = list(dict.fromkeys((o, a) for o, a, _t, _s in targets))
        else:
            destinations = [(o, a) for o, a, _k in rows]
        pairs = ptk.ShotSequencer.paste_pairs(rows, destinations)
        if not pairs:
            self._set_footer("Nothing copied for the selected rows' attributes")
            return
        now = self._current_time()
        if now is None:
            return
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        if shot_id is None:
            shot = self._find_shot_at_time(now)
            shot_id = shot.shot_id if shot is not None else None
        on_replace = self._claims_follow([o for o, _a, _k in pairs])["on_replace"]
        copied_paths = clip.get("paths") or {}
        # The rows a paste keyed: a row whose object is gone since the copy
        # keys nothing, and is neither counted nor selected.
        landed = []

        def _run():
            for obj_name, attr, keys in pairs:
                obj = bpy.data.objects.get(obj_name)
                if obj is None:
                    continue
                # The destination's own curve for the row; a row copied from
                # a curve deleted since lands on the path it was copied from.
                fcurves = ClipMotionMixin.curves_for_attr(obj_name, attr)
                path = (
                    (fcurves[0].data_path, fcurves[0].array_index)
                    if fcurves
                    else next(
                        (p for (o, a), p in copied_paths.items() if a == attr), None
                    )
                )
                if path is None:
                    continue
                buffer = {
                    "mode": "selected",
                    "keys": {path: [(kd["frame"], kd["value"]) for kd in keys]},
                    "tangents": {
                        path: [{f: kd[f] for f in self._KEY_SHAPE} for kd in keys]
                    },
                }
                first = min(kd["frame"] for kd in keys)
                if AnimUtils.paste_keys(
                    [obj],
                    buffer,
                    target_time=now + (first - clip["start"]),
                    on_replace=on_replace,
                ):
                    landed.append((obj_name, attr, keys))
            return len(landed)

        n = self._key_scene_edit("Paste Keys", _run, shot_id=shot_id) or 0
        if not n:
            self._set_footer(
                "Nothing pasted: the copied rows' objects or attributes are gone"
            )
            return
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget.select_keys(
                [
                    {
                        "data": {"obj": obj, "attr_name": attr},
                        "times": [now + kd["frame"] - clip["start"] for kd in keys],
                    }
                    for obj, attr, keys in landed
                ]
            )
        keys_n = sum(len(keys) for _o, _a, keys in landed)
        self._set_footer(
            f"Pasted {keys_n} key{'s' if keys_n != 1 else ''} onto "
            f"{n} row{'s' if n != 1 else ''}"
        )

    def _stash_key_targets(self, targets: list) -> None:
        """Park the selected keys in the key stash.

        The key-selection twin of :meth:`_stash_clip_keys`: same store, same
        loop (:meth:`_run_stash`), scoped to the selected times of each
        attribute instead of a whole clip's span.
        """
        try:
            import bpy
        except ImportError:
            return
        jobs = []
        for obj_name, attr, times, shot_id in targets:
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                continue
            fcurves = ClipMotionMixin.curves_for_attr(obj_name, attr)
            if not fcurves:
                continue
            jobs.append(
                (
                    obj,
                    fcurves,
                    min(times),
                    max(times),
                    None if shot_id == -1 else shot_id,
                )
            )
        self._run_stash(jobs)

    def _set_key_tangents(self, targets: list, tangent: str, sides=("in", "out")):
        """Set the handle type on the selected keys (one or both sides).

        ``FLAT`` is Maya's flat tangent: the side's handle goes horizontal
        (its length kept), ALIGNED when both sides are flattened, FREE when
        only one is -- a partner left ALIGNED would swing level with it.
        Setting the OUT side also makes the outgoing segment a bezier, as a
        Maya out-tangent type replaces a step: on a constant or linear segment
        a handle has no effect.
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

        side = "" if len(sides) == 2 else f" {sides[0]}"
        self._edit_key_tangents(targets, apply, f"{tangent.lower()}{side}")

    def _set_key_interpolation(self, targets: list, mode: str) -> None:
        """Set the interpolation mode of the selected keys."""

        def apply(_obj, _attr, _time, kp):
            kp.interpolation = mode

        self._edit_key_tangents(targets, apply, mode.lower())

    def _lock_key_tangents(self, targets: list, lock: bool) -> None:
        """Break (free handles) or unify (aligned handles) the selected keys.

        Break makes both sides FREE -- Blender has no lock flag apart from
        the handle type, so FREE is how a broken key stays broken.  Unify
        aligns FREE and VECTOR sides and leaves AUTO / AUTO_CLAMPED alone:
        those are unified by definition, and converting them would freeze
        handles Blender keeps re-deriving (Maya's ``-lock true`` leaves the
        tangent type as it is).
        """
        if not lock:
            self._set_key_tangents(targets, "FREE")
            return

        def apply(_obj, _attr, _time, kp):
            for side in ("handle_left_type", "handle_right_type"):
                if getattr(kp, side) in ("FREE", "VECTOR"):
                    setattr(kp, side, "ALIGNED")

        self._edit_key_tangents(targets, apply, "unified")

    @staticmethod
    def place_dragged_handle(
        kp, side: str, dt: float, dv: float, broken: bool = False
    ) -> None:
        """Put one handle of keyframe point *kp* at ``co + (dt, dv)``.

        The preview's control points ARE the handles (``handle_left`` /
        ``handle_right``), so the vector lands directly.  A computed handle
        (auto, auto-clamped, vector) cannot hold a position -- ``update()``
        re-derives it -- so the first drag makes it what the Graph Editor
        would: ALIGNED, both sides, unless the other side is already FREE,
        in which case the dragged side goes FREE too.  On an aligned key
        the opposite handle is re-aimed along the new line, keeping its
        length, which is what aligned means.

        *broken* (the Alt drag) is Maya's ``keyTangent -lock false``: it is
        the KEY that breaks, not one side of it, so BOTH handles go FREE and
        the partner is left exactly where it sits.  An aligned partner would
        otherwise swing itself back in line with the handle being dragged --
        the very thing the gesture is asking not to happen.
        """
        import math

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
                setattr(
                    kp, other, (co_t - dt / norm * length, co_v - dv / norm * length)
                )

    def on_keys_tangent_dragged(self, groups: list, side: str, broken: bool) -> None:
        """Write the handles a dragged tangent grab point asks for (see
        :meth:`place_dragged_handle`); one undo step, selection kept.

        *groups* is the whole gesture -- ``[(clip_id, [(time, dt, dv), ...]),
        ...]`` -- since a tangent drag carries the key SELECTION unless the
        user held Ctrl, and every key it carried brings its own vector.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        # Keyed by row, not by time alone: two clips can be the same frame on
        # different objects, and each carries its own vector.
        vectors = {}
        for clip_id, entries in groups:
            clip = widget.get_clip(clip_id)
            if clip is None:
                continue
            row = (clip.data.get("obj"), clip.data.get("attr_name"))
            for time, dt, dv in entries:
                vectors[row + (round(float(time), 6),)] = (dt, dv)
        targets = self._key_targets(
            widget,
            [
                {"clip_id": clip_id, "times": [t for t, _dt, _dv in entries]}
                for clip_id, entries in groups
            ],
        )
        if not targets:
            return

        def apply(obj, attr, time, kp):
            # Keyed on the time that ASKED for this point, not on the point's
            # own ``co`` -- the handler matches a key within a window, and a
            # float read back off the curve need not compare equal to the one
            # the drag reported.
            vector = vectors.get((obj, attr, round(float(time), 6)))
            if vector is not None:
                self.place_dragged_handle(kp, side, *vector, broken=broken)

        what = f"{side} handle {'broken' if broken else 'dragged'}"
        self._edit_key_tangents(targets, apply, what)

    def _edit_key_tangents(self, targets: list, apply, what: str) -> None:
        """Run ``apply(obj, attr, time, kp)`` on every selected keyframe
        point, one undo step.

        The row and the requested time ride along with the point: a handle
        drag that carried a selection writes a different vector per key, the
        same frame can be a key on two different objects, and the point is
        matched within a window -- so only the caller's own time identifies
        which edit this point is.

        The rebuild that follows retires every key dot, so the selection is
        put back by object/attribute/time afterwards -- the user is looking
        at the handles they just changed, and they must stay selected to
        show them.
        """
        widget = self._get_sequencer_widget()
        if widget is None or not targets:
            return
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        n = 0
        was_syncing = self._syncing
        self._syncing = True
        try:
            with CoreUtils.undo_chunk("Key tangents"):
                for obj, attr, times, _sid in targets:
                    for fc in ClipMotionMixin.curves_for_attr(obj, attr):
                        kt = AnimUtils.key_times(fc)
                        touched = False
                        for t in times:
                            i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                            for i in range(i0, i1):
                                apply(obj, attr, t, fc.keyframe_points[i])
                                touched = True
                                n += 1
                        if touched:
                            fc.update()
        finally:
            self._syncing = was_syncing
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        widget.select_keys(
            [
                {"data": {"obj": obj, "attr_name": attr}, "times": list(times)}
                for obj, attr, times, _sid in targets
            ]
        )
        self._set_footer(f"Tangents {what} on {n} key{'s' if n != 1 else ''}")
