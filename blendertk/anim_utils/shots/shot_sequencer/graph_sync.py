# !/usr/bin/python
# coding=utf-8
"""Graph editor glue: the shot sequencer's curves in uitk's graph editor (Blender).

Mirror of mayatk's ``graph_sync.py``.  :class:`GraphSync` is a collaborator the
panel composes beside its controller (not one more controller mixin): it feeds
the sequencer widget's ``"graph"`` editor the visible shots' objects and their
fcurves -- read through :meth:`blendertk.AnimUtils.get_key_curves` -- and
commits the editor's edits.

A key's TIME goes through the controller's own shot-aware key path
(``_expand_and_compensate`` + ``_commit_curve_moves`` inside one
``store.scene_edit``), so a graph move grows and ripples shot bounds and moves
the system's claims exactly as a track-row key drag does; its VALUE through
:meth:`AnimUtils.shift_key_values`, first, at the keys' old times.  Handles,
handle types, interpolation and break / unify go through the key menu's own
writers (``_edit_key_tangents`` and the rest), so a graph edit and a track-row
edit are one code path.  A Blender handle always carries its length: the graph
offers no weighting rows here.  Infinity is the fcurve's extrapolation -- one
for both ends, as Blender has it -- or a Cycles modifier's side.

Curve ids are ``"<object>|<data_path>|<index>"`` -- per object, since one
layered Action can animate several -- and resolved to the fcurve at every
edit: a held fcurve wrapper dies with an undo.  Blender reports no per-curve
edit, so after a rebuild only the curves whose points changed are re-read (a
``foreach_get`` fingerprint of each curve read).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from blendertk.anim_utils._anim_utils import AnimUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer.key_menu import KeyMenuMixin
from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
    SegmentCollector,
)
from blendertk.core_utils._core_utils import CoreUtils

__all__ = ["GraphSync"]


class GraphSync:
    """Feeds the sequencer's graph editor and commits its edits (module docstring).

    Parameters:
        controller: The panel's ``ShotSequencerController``.
        widget: Its ``SequencerWidget``.
    """

    #: Blender's handle types, then its interpolations, as the graph offers
    #: them -- one list, as Maya's tangent types span both.  ``FLAT`` is the
    #: key menu's horizontal handle (``KeyMenuMixin._set_key_tangents``).
    TANGENT_TYPES = (
        ("AUTO_CLAMPED", "Auto Clamped"),
        ("AUTO", "Automatic"),
        ("VECTOR", "Vector"),
        ("ALIGNED", "Aligned"),
        ("FREE", "Free"),
        ("FLAT", "Flat"),
        ("BEZIER", "Bezier"),
        ("LINEAR", "Linear"),
        ("CONSTANT", "Constant"),
    )
    #: The entries above that are interpolations, not handle types.
    INTERPOLATIONS = ("BEZIER", "LINEAR", "CONSTANT")
    #: ``pythontk.KeyCurve`` cycling infinity -> a Cycles modifier's mode.
    CYCLES = {"cycle": "REPEAT", "cycle_offset": "REPEAT_OFFSET", "oscillate": "MIRROR"}
    #: Channel order in the outline; other channels follow alphabetically.
    PATH_ORDER = ("location", "rotation_euler", "rotation_quaternion", "scale")

    def __init__(self, controller, widget):
        self.controller = controller
        self.widget = widget
        self.editor = widget.editor("graph")
        self._dirty = True
        #: curve id -> (object, data_path, index): what the id resolves through.
        self._ids: Dict[str, Tuple[str, str, int]] = {}
        #: curve id -> (object, channel label): the key menu's row for it.
        self._owners: Dict[str, Tuple[str, str]] = {}
        #: curve id -> fingerprint of the points last read for it.
        self._prints: Dict[str, tuple] = {}
        self._outline_cache: Optional[list] = None
        self._follow_queued = False
        self._window: Optional[Tuple[float, float]] = None
        #: A Live Update drag wrote properties (the animation re-evaluates on
        #: cancel and after a commit).
        self._live_written = False
        self._connections: List[Tuple[object, object]] = []
        self.editor.set_tangent_types(self.TANGENT_TYPES)
        self.editor.offers_weighting = False
        self.editor.set_curve_provider(self._provide)
        for signal, slot in (
            (widget.content_rebuilt, self._on_rebuilt),
            (widget.editor_changed, self._on_editor_changed),
            (self.editor.keys_edited, self.on_keys_edited),
            (self.editor.tangents_edited, self.on_tangents_edited),
            (self.editor.keys_deleted, self.on_keys_deleted),
            (self.editor.keys_inserted, self.on_keys_inserted),
            (self.editor.tangent_type_requested, self.on_tangent_type),
            (self.editor.tangents_broken, self.on_tangents_broken),
            (self.editor.infinity_requested, self.on_infinity),
            (self.editor.key_selection_changed, self.on_key_selection),
            (self.editor.key_menu_requested, self.on_key_menu),
            (self.editor.curves_previewed, self._on_preview),
            (self.editor.preview_cancelled, self._on_preview_cancelled),
        ):
            signal.connect(slot)
            self._connections.append((signal, slot))
        controller.register_key_targets(self.editor.name, self.key_targets)
        self._subscribe()
        # A file load drops every msgbus subscription: subscribe again on the
        # store's invalidation (a scene opened), as the controller does its own
        # -- and let go of both when the widget goes, as the controller does.
        BlenderShotStore.add_invalidation_listener(self._on_scene_swap)
        widget.destroyed.connect(lambda *_: self.detach())
        if widget.active_editor is self.editor:
            self.sync()

    def _on_scene_swap(self, _event=None) -> None:
        """Another scene: follow its active object (:meth:`_subscribe`)."""
        try:
            import bpy

            bpy.msgbus.clear_by_owner(self)
        except Exception:
            pass
        self._subscribe()

    def _subscribe(self) -> None:
        """Blender's active object, through msgbus (it takes plain functions
        only, so the hook is a closure; ``detach`` clears it by owner, and a
        scene swap subscribes it again, :meth:`_on_scene_swap`)."""
        try:
            import bpy
        except ImportError:
            return

        def _on_active(*_args):
            self._on_blender_selection()

        try:
            bpy.msgbus.subscribe_rna(
                key=(bpy.types.LayerObjects, "active"),
                owner=self,
                args=(),
                notify=_on_active,
            )
        except Exception:  # pragma: no cover - msgbus unavailable
            pass

    def _on_blender_selection(self) -> None:
        if self.widget.active_editor is self.editor and not self._follow_queued:
            from qtpy import QtCore

            self._follow_queued = True
            QtCore.QTimer.singleShot(0, self._follow_selection)

    def _follow_selection(self) -> None:
        """Show the selected objects' curves, as Maya's Graph Editor loads them."""
        self._follow_queued = False
        if not self._connections:  # detached while queued
            return
        if self.widget.active_editor is not self.editor or not self.editor.auto_load:
            return  # another view, or the display pinned
        try:
            # The window's layer even with no window in context (a Qt timer's).
            selected = {o.name for o in CoreUtils.selected_objects()}
        except Exception:
            return
        ids = [e["id"] for e in self._outline_cache or () if e["id"] in selected]
        if ids:
            self.editor.focus_objects(ids)

    def detach(self) -> None:
        """Disconnect everything (a panel re-init builds a new one)."""
        BlenderShotStore.remove_invalidation_listener(self._on_scene_swap)
        try:
            import bpy

            bpy.msgbus.clear_by_owner(self)
        except Exception:
            pass
        if (
            self.controller._key_target_sources.get(self.editor.name)
            == self.key_targets
        ):
            self.controller.register_key_targets(self.editor.name, None)
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # the widget went first
        self._connections = []

    # -- feeding the editor -------------------------------------------------------

    def _on_rebuilt(self) -> None:
        if self.widget.active_editor is self.editor:
            self.sync()
        else:
            self._dirty = True  # rebuilt on the next switch to the graph

    def _on_editor_changed(self, name: str) -> None:
        if name == self.editor.name:
            self.sync()
            self._follow_selection()

    def sync(self, full: bool = False) -> None:
        """Bring the graph in line with the scene.

        The outline is re-derived and re-set only when it changed; of the
        curves, only those whose points changed since they were read are
        re-read -- every displayed one on *full*.
        """
        outline = self.outline()
        if outline != self._outline_cache:
            self._outline_cache = outline
            self.editor.set_outline(outline)
        # What the scope shows is what the graph keeps bright (Dim Outside Shots).
        if self.editor.focus_range != self._window:
            self.editor.focus_range = self._window
        self._flush(full)
        self._dirty = False

    def _flush(self, full: bool = False, curves=()) -> None:
        """Re-read the curves that changed, *curves* among them."""
        if full:
            self._prints.clear()
            self.editor.refresh()
            return
        changed = set(curves)
        for cid, before in list(self._prints.items()):
            if cid not in changed and self._fingerprint(self._fcurve(cid)) != before:
                changed.add(cid)
        if changed:
            for cid in changed:
                self._prints.pop(cid, None)  # read again when next provided
            self.editor.invalidate(changed)

    def _visible(self) -> Tuple[List[str], Optional[Tuple[float, float]]]:
        """The visible shots' members (the active shot's first, hidden ones
        out) and the frame window those shots span.

        Read from the store, not from the track rows: those are not built
        while the graph shows (``WidgetSyncMixin._rebuild_content``).
        """
        ctl = self.controller
        seq = ctl.sequencer
        active = seq.shot_by_id(ctl.active_shot_id) if seq is not None else None
        if active is None:
            return [], None
        shots = [active] + [s for s in ctl._visible_shots(active) if s is not active]
        out: List[str] = []
        for shot in shots:
            for obj in shot.objects:
                if obj not in out and not seq.is_object_hidden(obj):
                    out.append(obj)
        window = (min(s.start for s in shots), max(s.end for s in shots))
        return out, window

    def outline(self) -> List[dict]:
        """The visible shots' objects keyed across their window, and their curves."""
        colors = self.widget.attribute_colors
        entries = []
        objects, window = self._visible()
        self._window = window
        for obj in objects:
            curves = self._curves_of(obj)
            # A member whose keys all sit on one side of the shots on screen
            # draws a flat line there: it stays out.  One keyed across them
            # animates there, a key inside or not.
            if window is not None and not any(
                kt and kt[0] <= window[1] and kt[-1] >= window[0]
                for kt in (AnimUtils.key_times(fc) for _cid, fc, _label in curves)
            ):
                continue
            children = []
            for cid, fc, label in curves:
                self._ids[cid] = (obj, fc.data_path, fc.array_index)
                self._owners[cid] = (obj, label)
                children.append(
                    {"curve_id": cid, "label": label, "color": colors.get(label)}
                )
            if children:
                entries.append({"id": obj, "label": obj, "children": children})
        return entries

    def _curves_of(self, obj: str) -> List[Tuple[str, object, str]]:
        """``[(curve id, fcurve, channel label), ...]`` animating *obj*."""
        try:
            import bpy
        except ImportError:
            return []
        node = bpy.data.objects.get(obj)
        if node is None:
            return []
        order = {path: i for i, path in enumerate(self.PATH_ORDER)}
        found = []
        for fc in AnimUtils.get_fcurves(node):  # no drivers here
            cid = f"{obj}|{fc.data_path}|{fc.array_index}"
            found.append((cid, fc, SegmentCollector.attr_label(fc)))
        return sorted(
            found,
            key=lambda item: (
                order.get(item[1].data_path, len(order)),
                item[1].data_path,
                item[1].array_index,
            ),
        )

    def _fcurve(self, cid: str):
        """The live fcurve behind *cid*, or None (resolved fresh every time)."""
        resolved = self._ids.get(cid)
        if resolved is None:
            return None
        obj, data_path, index = resolved
        try:
            import bpy
        except ImportError:
            return None
        node = bpy.data.objects.get(obj)
        if node is None:
            return None
        for fc in AnimUtils.get_fcurves(node):
            if fc.data_path == data_path and fc.array_index == index:
                return fc
        return None

    @staticmethod
    def _fingerprint(fc) -> Optional[tuple]:
        """What a re-read would change on: the points, handles, their types
        and interpolations, and the infinity the curve draws."""
        if fc is None:
            return None
        try:
            kps = fc.keyframe_points
            n = len(kps)
            parts = []
            # All bulk reads (enums read as ints): a production bake is ~130k
            # keys, and a per-key attribute walk would cost every rebuild.
            for prop, width, zero in (
                ("co", 2, 0.0),
                ("handle_left", 2, 0.0),
                ("handle_right", 2, 0.0),
                ("interpolation", 1, 0),
                ("handle_left_type", 1, 0),
                ("handle_right_type", 1, 0),
                ("type", 1, 0),
            ):
                buf = [zero] * (width * n)
                kps.foreach_get(prop, buf)
                parts.append(tuple(buf))
        except ReferenceError:  # died with an undo
            return None
        return (
            *parts,
            fc.extrapolation,
            tuple(
                (
                    m.type,
                    getattr(m, "mode_before", ""),
                    getattr(m, "mode_after", ""),
                    m.mute,
                )
                for m in fc.modifiers
            ),
        )

    def _provide(self, curve_ids) -> list:
        """KeyCurves for *curve_ids*, the shot system's samples drawn keyless."""
        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            _ShotSequencerInternal,
        )

        ledger = self._ledger()
        out = []
        for cid in curve_ids:
            fc = self._fcurve(cid)
            read = AnimUtils.get_key_curves([fc]) if fc is not None else []
            if not read:
                continue
            curve = read[0]
            curve.curve_id = cid
            obj, label = self._owners.get(cid, ("", ""))
            curve.label = f"{obj}.{label}"
            if ledger is not None:
                key = _ShotSequencerInternal._fc_key(obj, fc)
                for h in ledger.key_times(key):  # a handful; the keys bisected
                    i = curve.index_at(h)
                    if i >= 0:
                        curve.keys[i].hidden = True
            self._prints[cid] = self._fingerprint(fc)
            out.append(curve)
        return out

    # -- commits ------------------------------------------------------------------

    def _ledger(self):
        seq = self.controller.sequencer
        return seq.ledger if seq is not None else None

    def _shot_at(self, t: float, dt: float = 0.0, shots=None) -> Optional[int]:
        """The shot a key at *t* is edited as part of: the one holding it.  On
        a seam two flush shots share, the one a move by *dt* goes into -- the
        later shot for a move right, else the earlier -- so a seam key moved
        into a shot grows none.  *shots*: ``sorted_shots()``, read once by a
        caller asking for many keys."""
        if shots is None:
            seq = self.controller.sequencer
            if seq is None:
                return None
            shots = seq.sorted_shots()
        hits = [s for s in shots if s.start - 1e-3 <= t <= s.end + 1e-3]
        if not hits:
            return None
        return (hits[-1] if dt > 0 else hits[0]).shot_id

    def key_targets(self, groups=None) -> list:
        """``[(obj, attr, [times], shot_id), ...]`` for the graph's key
        selection (or *groups*, ``[(curve, [times])]``): the shape the key
        menu and the key shortcuts act on (``KeyMenuMixin._key_targets``)."""
        if groups is None:
            groups = self.editor.selected_keys()
        targets = []
        for curve, times in groups:
            owner = self._owners.get(curve)
            if owner is None or not times:
                continue
            times = sorted(times)
            targets.append(owner + (times, self._shot_at(times[0])))
        return targets

    def on_key_menu(self, menu, groups) -> None:
        """The track view's key edits and Move / Copy to Shot, on the graph's
        selection -- one key menu, whichever view shows it."""
        targets = self.key_targets(groups)
        if not targets:
            return
        n = sum(len(times) for _o, _a, times, _s in targets)
        self.controller._add_selection_key_actions(
            menu, targets, f" ({n})" if n > 1 else ""
        )

    def on_keys_edited(self, edits) -> None:
        """Commit a move / scale / typed stat: ``[(curve, [(t, dt, dv), ...])]``."""
        ctl = self.controller
        seq = ctl.sequencer
        if seq is None or not edits:
            return
        values: Dict[object, list] = {}
        # By the fcurve's pointer: linked duplicates' ids name ONE curve, and
        # moved once per id it took the drag twice (a clip drag's twin).
        curve_moves: Dict[int, dict] = {}
        shot_extents: Dict[int, tuple] = {}
        shots = seq.sorted_shots()
        for cid, rows in edits:
            fc = self._fcurve(cid)
            if fc is None:
                continue
            shifted = [(t, dv) for t, _dt, dv in rows if abs(dv) > 1e-9]
            if shifted:
                values[fc] = shifted  # an fcurve hashes by its pointer
            entry = curve_moves.setdefault(
                fc.as_pointer(), {"fc": fc, "obj": self._ids[cid][0], "pairs": []}
            )
            known = {round(t, 6) for t, _n, _s in entry["pairs"]}
            for t, dt, _dv in rows:
                if abs(dt) <= 1e-6 or round(t, 6) in known:
                    continue
                sid = self._shot_at(t, dt, shots)
                entry["pairs"].append((t, t + dt, sid))
                if sid is not None:
                    lo, hi = shot_extents.get(sid, (t + dt, t + dt))
                    shot_extents[sid] = (min(lo, t + dt), max(hi, t + dt))
        curve_moves = {ptr: e for ptr, e in curve_moves.items() if e["pairs"]}
        if not values and not curve_moves:
            self._reevaluate_live()  # nothing to commit: hand live writes back
            return
        n = sum(len(rows) for _c, rows in edits)
        bounds = self._bounds()
        with self._edit("Graph Keys"):
            # Values first, at the keys' old times: the time commit's
            # recreate path reads the keys as they then are.
            if values:
                AnimUtils.shift_key_values(values)
            if curve_moves:
                # The graph records no press modifiers, and Shift there
                # constrains an axis: the shot grows over the move whatever a
                # tracks gesture left in the widget's flags (Shift: free).
                from qtpy import QtCore

                self.widget.record_press_modifiers(QtCore.Qt.NoModifier)
                ctl._expand_and_compensate(curve_moves, shot_extents)
                ctl._commit_curve_moves(curve_moves, ledger=seq.ledger)
                # Times moved: the system's claims and gap holds follow.  A
                # value-only edit leaves both alone (they are about times).
                seq.reconcile_system_edits()
        self._after(
            f"Moved {n} key{'s' if n != 1 else ''} in the graph",
            bounds=self._bounds() != bounds,
            curves=[c for c, _rows in edits],
        )

    def on_tangents_edited(self, rows) -> None:
        """Commit dragged handles: ``[(curve, [(t, side, dt, dv, broken), ...])]``,
        through the key menu's handle writer (``place_dragged_handle``)."""
        vectors = {}
        groups = []
        for cid, items in rows:
            owner = self._owners.get(cid)
            if owner is None:
                continue
            for t, side, dt, dv, broken in items:
                vectors[owner + (round(float(t), 6),)] = (side, dt, dv, broken)
            groups.append((cid, [t for t, *_rest in items]))

        def apply(obj, attr, time, kp):
            vector = vectors.get((obj, attr, round(float(time), 6)))
            if vector is not None:
                side, dt, dv, broken = vector
                KeyMenuMixin.place_dragged_handle(kp, side, dt, dv, broken=broken)

        targets = self.key_targets(groups)
        if targets:
            self.controller._edit_key_tangents(targets, apply, "dragged in the graph")
            self._after_key_menu_edit([c for c, _t in groups])

    def on_keys_deleted(self, groups) -> None:
        """Remove keys: ``[(curve, [time, ...])]``; their claims go with them."""
        seq = self.controller.sequencer
        if seq is None:
            return
        n = 0
        bounds = self._bounds()
        with self._edit("Graph Delete Keys"):
            release = seq.store.release_replaced(
                list({self._ids[c][0] for c, _t in groups if c in self._ids})
            )
            for cid, times in groups:
                fc = self._fcurve(cid)
                if fc is None:
                    continue
                for t in times:
                    kt = AnimUtils.key_times(fc)  # re-read: each cut shifts them
                    i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                    for i in reversed(range(i0, i1)):
                        fc.keyframe_points.remove(fc.keyframe_points[i])
                        n += 1
                    if i1 > i0:
                        fc.update()
                        release(fc, kt[i0:i1])
            seq.reconcile_system_edits()
        self._after(
            f"Deleted {n} key{'s' if n != 1 else ''}",
            bounds=self._bounds() != bounds,
            curves=[c for c, _t in groups],
            outline=True,
        )

    def on_keys_inserted(self, items) -> None:
        """Key ``[(curve, time), ...]`` on the curve as it runs there (Blender
        subdivides the span's handles, so the shape holds)."""
        seq = self.controller.sequencer
        if seq is None:
            return
        bounds = self._bounds()
        with self._edit("Graph Insert Keys"):
            for cid, t in items:
                fc = self._fcurve(cid)
                if fc is not None:
                    fc.keyframe_points.insert(t, fc.evaluate(t))
                    fc.update()
            seq.reconcile_system_edits()
        self._after(
            f"Inserted {len(items)} key{'s' if len(items) != 1 else ''}",
            bounds=self._bounds() != bounds,
            curves=[c for c, _t in items],
        )

    def on_tangent_type(self, type_id: str, groups) -> None:
        targets = self.key_targets(groups)
        if not targets:
            return
        if type_id in self.INTERPOLATIONS:
            self.controller._set_key_interpolation(targets, type_id)
        else:
            self.controller._set_key_tangents(targets, type_id)
        self._after_key_menu_edit([c for c, _t in groups])

    def on_tangents_broken(self, broken: bool, groups) -> None:
        targets = self.key_targets(groups)
        if targets:
            self.controller._lock_key_tangents(targets, not broken)
            self._after_key_menu_edit([c for c, _t in groups])

    def on_infinity(self, side: str, mode: str, curves) -> None:
        """Pre / post infinity.  ``constant`` / ``linear`` set the fcurve's
        extrapolation -- both ends: Blender keeps one -- and stop that side
        cycling; a cycling mode is a Cycles modifier's side."""
        sides = {"pre": ("mode_before",), "post": ("mode_after",)}.get(
            side, ("mode_before", "mode_after")
        )
        with self._edit(None, "Graph infinity"):
            for cid in curves:
                fc = self._fcurve(cid)
                if fc is None:
                    continue
                cyc = next((m for m in fc.modifiers if m.type == "CYCLES"), None)
                if mode in ("constant", "linear"):
                    fc.extrapolation = mode.upper()
                    if cyc is not None:
                        for attr in sides:
                            setattr(cyc, attr, "NONE")
                        if cyc.mode_before == cyc.mode_after == "NONE":
                            fc.modifiers.remove(cyc)
                elif mode in self.CYCLES:
                    if cyc is None:
                        cyc = fc.modifiers.new("CYCLES")
                        # A new modifier repeats both sides: the other keeps
                        # what it had (none).
                        cyc.mode_before = cyc.mode_after = "NONE"
                    for attr in sides:
                        setattr(cyc, attr, self.CYCLES[mode])
                fc.update()
        self._after(
            f"{side.title()} infinity: {mode.replace('_', ' ')}",
            bounds=False,
            curves=list(curves),
        )

    def _on_preview(self, rows) -> None:
        """Live Update: each curve's value at the current frame, as the drag
        shapes it, written to its property -- what the viewport shows.  No
        key changes until the release commits; a cancel re-evaluates the
        animation, which puts every property back."""
        try:
            import bpy
        except ImportError:
            return
        frame = bpy.context.scene.frame_current
        for cid, curve in rows:
            resolved = self._ids.get(cid)
            node = bpy.data.objects.get(resolved[0]) if resolved else None
            if node is None:
                continue
            _obj, data_path, index = resolved
            # The writer a paste uses (an fcurve's data_path / index pair).
            if AnimUtils._set_path_value(node, data_path, index, curve.evaluate(frame)):
                self._live_written = True

    def _on_preview_cancelled(self) -> None:
        """A live drag ended with no edit: re-evaluate the animation."""
        self._reevaluate_live()

    def _reevaluate_live(self) -> None:
        if not self._live_written:
            return
        self._live_written = False
        try:
            import bpy

            bpy.context.scene.frame_set(bpy.context.scene.frame_current)
        except Exception:
            pass

    def on_key_selection(self, groups) -> None:
        """Mirror the graph's key selection into Blender's (not an undo step)."""
        self.controller._apply_key_selection(
            [(o, a, t) for o, a, t, _s in self.key_targets(groups)]
        )

    # -- plumbing -----------------------------------------------------------------

    @contextmanager
    def _edit(self, store_label: Optional[str], chunk_label: str = ""):
        """The bracket around one graph edit: the controller's guard against
        its own depsgraph debounce, and ONE undo step -- a shot-aware
        ``store.scene_edit`` (a restore point for the bounds) when
        *store_label* is given, a plain undo chunk otherwise."""
        ctl = self.controller
        was = ctl._syncing
        ctl._syncing = True
        try:
            if store_label is not None and ctl.sequencer is not None:
                bracket = ctl.sequencer.store.scene_edit(store_label)
            else:
                bracket = CoreUtils.undo_chunk(chunk_label or "Graph edit")
            with bracket:
                yield
        finally:
            ctl._syncing = was
            self._dirty = True

    def _bounds(self) -> list:
        seq = self.controller.sequencer
        return [(s.shot_id, s.start, s.end) for s in seq.sorted_shots()] if seq else []

    def _after_key_menu_edit(self, curves) -> None:
        """After a key-menu writer (it rebuilt the panel itself): the curves
        it touched re-read, whatever the rebuild's fingerprints found."""
        if self.widget.active_editor is self.editor:
            self._flush(curves=curves)

    def _after(
        self, footer: str, bounds: bool = True, curves=None, outline: bool = False
    ) -> None:
        """Bring the panel and the graph back in line with the scene, and report.

        An edit that moved a shot bound rebuilds the panel (the shot lane,
        overlays and combobox follow; the graph re-syncs on the rebuild).
        One that did not re-reads only the curves that changed -- *curves*
        among them -- the rows hidden and marked stale until the track
        editor shows again; *outline* re-derives the outline too (a curve cut
        to no keys is gone).
        """
        ctl = self.controller
        # Every shot's: the graph edits any shot's keys (Adjacent / All), and
        # a cached shot's segments are not collected again.
        ctl._segment_cache.clear()
        ctl._sub_row_cache.clear()
        self._reevaluate_live()  # a live drag's properties read the new keys
        if bounds or curves is None:
            ctl._sync_to_widget()
            if bounds:
                ctl._sync_combobox()
            if curves:
                self._flush(curves=curves)
        else:
            ctl._content_stale = True
            if outline:
                self.sync()
            self._flush(curves=curves)
            self._dirty = False
        # A rebuild that returned early raised no ``content_rebuilt``.
        if self._dirty and self.widget.active_editor is self.editor:
            self.sync()
        ctl._set_footer(footer)
