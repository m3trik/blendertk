# !/usr/bin/python
# coding=utf-8
"""Animation-phase export tasks (mirror of mayatk's): the smart bake and its
restore, key optimization / snap / tie, the bake range, the data_export
carrier with its staged curve proxies, and the declared takes.
"""

import math
from typing import Union

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _AnimationTasksMixin(_TaskDataMixin):
    """Animation-phase export tasks (mirror of mayatk's): the smart bake and its
    restore, key optimization / snap / tie, the bake range, the data_export carrier with its staged curve proxies, and the declared takes."""

    def smart_bake(self):
        """Pre-bake constrained/driven objects before export.

        Uses SmartBake to detect constraints (including IK), drivers, and driven blend-shape
        weights, then bakes each into a fresh Action while muting the sources that were
        fighting it -- non-destructive: the pre-bake action and the constraint/driver network
        both survive, and the bake is restorable via ``SmartBake.restore()``.
        """
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        baker = SmartBake(objects=self._live_objects(), sample_by=1)
        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
            return

        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} object(s)/bone(s) requiring bake.")

        result = baker.bake(analysis)
        # The session manifest (SmartBake.restore swaps the actions back and
        # unmutes the sources) -- undone by _restore_bake_session, staged
        # HERE, after every earlier task's restore, so it unwinds FIRST.
        if result.session_id:
            self._bake_session_id = result.session_id
            self.stage_deferred_restore("smart_bake", self._restore_bake_session)
            if self.run.animation_write_back:  # that restore keeps the bake
                self.record_kept_edit("key edits")

        log_parts = [
            f"Smart bake completed: {result.baked_count} unit(s) baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        # SmartBake never optimizes its own output -- that's the separate optimize_keys
        # task's job (TASK_ORDER runs it immediately after this one).
        if self.run.optimize_keys_level:
            log_parts.append("optimize_keys will run next")
        self.logger.info(", ".join(log_parts) + ".")

    def optimize_keys(self, level=True):
        """Remove redundant animation data from all exported objects, at *level*.

        Parameters:
            level: A key of ``AnimUtils.OPTIMIZE_LEVELS`` (``"static"``, ``"flat"``,
                ``"simplify"``, ``"extremes"``), ``True`` for the default level, or
                anything falsy for OFF. Mirror of mayatk's task: the panel's Optimize
                Keys combo supplies the token, a headless caller's legacy ``True``
                keeps behaving as it did, and an unknown level raises out of the
                resolver rather than silently optimizing at a setting nobody chose.
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        kwargs = AnimUtils.resolve_optimize_level(level)
        if not kwargs:  # OFF — a headless caller's falsy value; the panel's own
            return  # OFF row never reaches the dispatcher (b000 filters it)
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        resolved = AnimUtils.normalize_optimize_level(level)
        self.logger.info(f"Optimizing baked animation keys ({resolved})...")
        stats = AnimUtils.optimize_keys(self.objects, **kwargs)
        if (stats["curves_before"], stats["keys_before"]) != (
            stats["curves_after"],
            stats["keys_after"],
        ):
            self.record_kept_edit("key edits")
        self.logger.info(
            f"Optimization completed: {stats['curves_before']} -> {stats['curves_after']} "
            f"curve(s), {stats['keys_before']} -> {stats['keys_after']} key(s)."
        )

    def tie_all_keyframes(self):
        """Tie (bookend) keyframes at the union keyed extent across all exported objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        from blendertk.anim_utils._anim_utils import AnimUtils

        self.logger.info("Tying keyframes for all objects.")
        changed = AnimUtils.tie_keyframes(self.objects, absolute=True)
        if changed:
            self.record_kept_edit("key edits")
        self.logger.info(f"Tied {changed} keyframe(s).")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        from blendertk.anim_utils._anim_utils import AnimUtils

        self.logger.info("Snapping keyframes to nearest whole frame.")
        snapped = AnimUtils.snap_keys(self.objects)
        if snapped:
            self.record_kept_edit("key edits")
        self.logger.info(f"Snapped {snapped} keyframe(s).")

    #: Bake Range sources, in the order the combo offers them. Mirrors mayatk's
    #: tokens exactly, so one export template means the same thing in both DCCs.
    BAKE_RANGE_MODES = ("auto", "keys", "scene")

    def _require_range_coverage(self, start, end) -> None:
        """Claim a frame span the export's bake range MUST cover.

        The seam every claimant uses instead of writing the range itself.
        ``set_bake_animation_range`` runs last and widens to the union of every
        claim, so a task that stages animation the write has to carry cannot
        have its span silently clipped by whichever range source the user
        picked -- and a future claimant registers here rather than editing the
        range task.

        Two claimants today: the declared takes (a shot can outrun the last
        keyframe, and shipping metadata for a clip the file truncates is wrong
        in every deliverable at once) and, in blendertk, the staged
        keyed-weight curve proxies (whose keys sit outside the exported
        objects' own extent by construction).
        """
        current = self._required_range_coverage
        if current is None:
            self._required_range_coverage = (start, end)
        else:
            self._required_range_coverage = (
                min(current[0], start),
                max(current[1], end),
            )

    def _bake_range_from_shots(self):
        """The union of the scene's declared shots, or None when there are none.

        Reads the ShotStore, never the published ``data_export`` carrier: the carrier
        is a projection, and refreshing it to answer a question about RANGE would
        stamp a metadata node as a side effect of computing a number -- on scenes
        where the user deliberately switched that off. ``declared_range`` rounds
        through the same ``resolve_clip_specs`` the export view uses, so this range
        and the published clip ranges cannot disagree about a fractional boundary.
        """
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        return BlenderShotStore.declared_range()

    def _bake_range_from_keys(self):
        """The exported objects' FULL evaluated animated extent, or None.

        ``AnimUtils.get_animated_extent``: active-action fcurves UNION non-muted NLA
        strip extents in scene time UNION data-level / shape-key fcurve ranges. The
        FBX write bakes the *evaluated* scene, so an NLA-strip-only or shape-key-driven
        object used to export with a wrong bake range (the active-action reader saw no
        keys at all).
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        rng = AnimUtils.get_animated_extent(self.objects)
        if rng is None:
            return None
        return math.floor(rng[0]), math.ceil(rng[1])

    def _bake_range_from_scene(self):
        """The scene's own frame range.

        Mirrors mayatk's ``"scene"`` mode in MEANING -- "bake the scene's authored
        range" -- though the mechanics differ: Maya copies the scene range onto a
        separate FBX bake-complex range, while in Blender the range the exporter
        bakes over IS the scene's, so this writes it back unchanged. It is therefore
        a near no-op by design, and still differs from OFF: it participates in the
        widen below, so a declared take is covered either way.
        """
        scene = self._scene()
        return scene.frame_start, scene.frame_end

    def set_bake_animation_range(self, mode="auto"):
        """Set the scene's playback range for the export, from the selected source.

        Blender's FBX exporter bakes over the *scene's* frame range (``bake_anim=True``
        in ``_scene_exporter.py`` -- there is no separate "bake complex start/end" knob
        the way Maya's FBX plugin exposes via MEL), so the analogue of mayatk's Bake
        Range dial is to set the scene's own range for the export.

        Mirror of mayatk's redesign: this task now OWNS the range and runs LAST in
        TASK_ORDER, after ``apply_declared_takes``, instead of being overwritten by
        that task's widen. Every mode is then widened to cover the takes realized this
        run, so no source can write a range that clips a clip the same export
        declared.

        **Staged** -- for the same reason as :meth:`set_linear_unit`: the range
        is read by the *write*, so the restore rides ``stage_deferred_restore``
        (see :meth:`_set_frame_range`) and is undone after it.

        Parameters:
            mode: ``"auto"`` (shot union, falling back to the animated extent when
                the scene declares no shots), ``"keys"`` (animated extent),
                ``"scene"`` (the scene's own range), or anything falsy for OFF.
                A legacy ``True`` reads as ``"keys"`` -- what this task did as a
                checkbox.
        """
        if not mode:  # OFF — as optimize_keys: the panel filters its own OFF row
            return None  # out, so this is a headless caller's falsy value
        mode = "keys" if mode is True else str(mode).strip().lower()
        if mode not in self.BAKE_RANGE_MODES:
            raise ValueError(
                f"Unknown bake range mode {mode!r}; expected one of "
                f"{', '.join(self.BAKE_RANGE_MODES)}."
            )

        if mode == "auto":
            resolved, source = self._bake_range_from_shots(), "shot union"
            if resolved is None:
                resolved = self._bake_range_from_keys()
                source = "animated extent (no shots declared)"
        elif mode == "keys":
            resolved, source = self._bake_range_from_keys(), "animated extent"
        else:
            resolved, source = self._bake_range_from_scene(), "scene frame range"

        # Never clip a span another task claimed (:meth:`_require_range_coverage`),
        # whatever the selected source measured.
        required = self._required_range_coverage
        if required:
            if resolved is None:
                resolved, source = required, "required coverage"
            else:
                widened = (min(resolved[0], required[0]), max(resolved[1], required[1]))
                if widened != resolved:
                    resolved = widened
                    source += ", widened to cover the required span"

        if resolved is None:
            self.logger.debug(
                f"Nothing to measure for bake range mode {mode!r}. Skipping."
            )
            return None

        start, end = int(math.floor(resolved[0])), int(math.ceil(resolved[1]))
        self._set_frame_range(start, end)
        self.logger.info(f"Set bake range to {start}-{end} ({source}).")
        return None

    def export_data_node(self):
        """Publish the scene records and ship the carrier (default on).

        The ONE publish of an export (mirror of mayatk's ``export_data_node``):
        every producer in ``FbxUtils.PRODUCERS`` runs here, in dependency
        order, with the run's decisions as INPUT -- the Animation Clips mode
        from the run -- and the ``data_export`` Empty is committed once.
        Blender has no before-export event, so this task is the only refresh
        point (producers also publish at authoring time, which is what a
        non-exporter write ships).  Then the carrier joins the export set so
        its custom properties ride into the FBX as user properties
        (``use_custom_props`` + Empty-inclusive ``object_types``, both on by
        default in ``_DEFAULT_FBX_OPTIONS``); the mesh-only object sets would
        otherwise omit it.
        """
        self._scene_snapshot = self._publish_scene_records()
        self._include_data_export_node()

        # Keyed-weight curve proxies: Blender's FBX exporter can't ship
        # custom-property animation, so EmissiveGroups stages one transient
        # Empty per keyed group whose scale.x carries the weight curve (the
        # Blender half of mayatk's emissive export transport). They must
        # exist THROUGH the FBX write and vanish after, which the task-revert
        # engine can't express (reverts run before the write) — hence the
        # deferred restore.
        from blendertk.mat_utils.emissive_groups import EmissiveGroups

        proxies = EmissiveGroups.create_export_curve_proxies()
        if proxies:
            self.objects = list(self.objects or []) + proxies
            self.stage_deferred_restore(
                "emissive_curve_proxies",
                EmissiveGroups.remove_export_curve_proxies,
            )
            self._cover_frame_range(proxies)

        # The render-effect channels ride the same transport: one Empty per
        # keyed channel per object, parented under it (the Unity importer
        # rebinds by the parent path), removed after the write.
        from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

        effect_proxies = RenderEffects.stage_export_proxies()
        if effect_proxies:
            self.objects = list(self.objects or []) + effect_proxies
            self.stage_deferred_restore(
                "render_effect_curve_proxies",
                RenderEffects.remove_export_proxies,
            )
            self._cover_frame_range(effect_proxies)

        self._log_data_node_summary()

    def _include_data_export_node(self):
        """Fold the ``data_export`` carrier into the export set (shippable).

        Idempotent; a no-op when the scene has no carrier.  Shared by
        :meth:`export_data_node` and :meth:`apply_declared_takes` (mirror of
        mayatk's ``_include_data_export_node``), and — beyond the mayatk twin —
        also clears, for the write only, whatever hide state would make the
        selection-based FBX funnel silently drop the Empty.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        carrier = DataNodes.get_export_node(create=False)
        if carrier is None:
            self.logger.debug("No data_export carrier in scene — nothing to include.")
            return

        # The FBX funnel exports via use_selection + select_set, which can only
        # ship selectable, visible objects — a hidden carrier would silently
        # drop the metadata, so clear any hide state before including it, and
        # put it back after the write: a deferred restore runs AFTER it (the
        # task reverts that ran before the write were retired 2026-09-13, and
        # the carrier was left visible for good until 2026-09-24).
        try:
            layer_hidden = carrier.hide_get()
        except RuntimeError:  # not in the active view layer
            layer_hidden = False
        hide_state = (carrier.hide_select, carrier.hide_viewport, layer_hidden)

        def _rehide(carrier=carrier, state=hide_state):
            try:
                carrier.hide_select, carrier.hide_viewport = state[0], state[1]
                if state[2]:
                    carrier.hide_set(True)
            except (RuntimeError, ReferenceError):
                pass  # unlinked from the layer since, or freed

        # First wins: a later call sees the state this one cleared.
        self.stage_deferred_restore("data_export_hide", _rehide)
        was_hidden = carrier.hide_select or carrier.hide_viewport
        carrier.hide_select = False
        carrier.hide_viewport = False
        try:
            if not carrier.visible_get():
                was_hidden = True
                carrier.hide_set(False)
        except RuntimeError:  # not in the active view layer
            was_hidden = True
        if was_hidden:
            self.logger.info("data_export carrier was hidden — cleared for export.")

        # The object-level clears above can't help when the carrier's COLLECTION
        # is hidden or excluded from the view layer (hide_set even RAISES in the
        # excluded case) — the selection funnel would still drop it, or
        # select_set would kill the export outright. Link the carrier to the
        # scene root collection for the duration of the write and unlink it
        # right after (deferred restore: task reverts fire BEFORE the write).
        try:
            still_hidden = not carrier.visible_get()
        except RuntimeError:  # not in the active view layer at all
            still_hidden = True
        if still_hidden:
            root = self._scene().collection
            if carrier.name not in root.objects:
                root.objects.link(carrier)

                def _unlink_carrier(root=root, carrier=carrier):
                    try:
                        root.objects.unlink(carrier)
                    except RuntimeError:
                        pass

                self.stage_deferred_restore("data_export_root_link", _unlink_carrier)
                vl = CoreUtils._active_view_layer()
                if vl is not None:
                    vl.update()  # visible_get/select_set read the evaluated layer
                try:  # now reachable through the root — re-clear per-view-layer hiding
                    carrier.hide_set(False)
                except RuntimeError:
                    pass
                self.logger.info(
                    "data_export carrier's collection is hidden/excluded — linked "
                    "the carrier to the scene root collection for the write "
                    "(unlinked after)."
                )

        if carrier not in (self.objects or []):
            self.objects = list(self.objects or []) + [carrier]
            self.logger.info("data_export carrier added to the export set.")

    def ensure_scene_records_published(self):
        """Publish once if no task did (mirror of mayatk's): the write's
        fallback for a run with the carrier tasks off.  What it left out is
        logged as the carrier task's publish logs it
        (:meth:`_log_snapshot_notes`): with that task off, this is the run's
        only publish, and the only place a stale shot is named."""
        if self._scene_snapshot is None:
            self._scene_snapshot = self._publish_scene_records()
            self._log_snapshot_notes()

    def _publish_scene_records(self, only=None):
        """``FbxUtils.publish`` with THIS run's context (mirror of mayatk's).

        The clip span stays unmeasured here: what a Blender stack's origin
        should be with no takes armed is an open contract question (the
        producer seeds it from the bake range, as before).  Never raises -- a
        record that cannot be produced is logged and left as stored.

        Outside the write's bracket the publish PREPARES the session stagers
        (the shadow preview stands down so no producer reads it), and a run
        that stops before its write -- a declined check, an empty export set,
        a cancel -- never reaches the bracket that finishes them; so their
        finish is staged here too, as a deferred restore.  A completed run
        finishes them twice, which a stager's ``finish`` tolerates (it undoes
        what its ``prepare`` recorded, and the first pass consumed that).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        try:
            ctx = FbxUtils.export_context(
                clip_mode=ptk.ExportRun.clip_mode(self.run.animation_clips_mode),
                # The FBX's handoff record publishes the same lighting recipe
                # the GLB's envelope does, with this run's choices.
                rendering=self.run.rendering,
            )
            if not FbxUtils._export_depth:
                staged = dict(FbxUtils._session_stagers)
                self.stage_deferred_restore(
                    "export_stagers",
                    lambda: FbxUtils._run_stagers("finish", staged),
                )
            return FbxUtils.publish(ctx, only=only)
        except Exception:  # noqa: BLE001 - the write goes on; say what ships
            self.logger.warning(
                "Scene records not published; the carrier ships as last stored.",
                exc_info=True,
            )
            return None

    def _set_frame_range(self, start, end) -> None:
        """Set the scene's frame range for the export, staging its restore.

        Deferred past the write (the FBX writer reads the range *at* the
        write); ``stage_deferred_restore`` keys on ``frame_range`` so a later
        widen builds on the first caller's original.
        """
        scene = self._scene()
        original = (scene.frame_start, scene.frame_end)

        def restore():
            reverting = self._scene()
            reverting.frame_start, reverting.frame_end = original
            self.logger.debug(f"Reverted animation range to {original}.")

        self.stage_deferred_restore("frame_range", restore)
        scene.frame_start, scene.frame_end = int(start), int(end)

    def _cover_frame_range(self, objects) -> None:
        """Widen the scene's frame range so *objects*' keys all fall inside it.

        Blender bakes animation over the SCENE range, so a curve keyed outside
        it ships flattened to its extrapolated value.  The weight-curve proxies
        are staged in :meth:`export_data_node`, whose keys sit outside the
        exported objects' own extent by construction, so the range they need is
        claimed here rather than inferred by whichever source the Bake Range
        dial selected.  Only ever widens (never clips someone else's range),
        and rides the same staged restore.

        Both halves are load-bearing.  The immediate widen covers the case
        where Bake Range is OFF (nothing else sets the range at all), and the
        :meth:`_require_range_coverage` claim covers the case where it is not:
        that task runs LAST and would otherwise overwrite this widen with its
        own measurement -- verified to clip a 300-400 proxy span back to the
        exported objects' 10-200 before the claim existed.
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        rng = AnimUtils._key_range(AnimUtils.get_fcurves(objects))
        if rng is None:
            return
        # Claim FIRST, unconditionally: the widen below is skipped when the
        # scene range already covers these keys, but set_bake_animation_range
        # runs later and measures the EXPORTED OBJECTS, which is a different
        # (and routinely narrower) span -- so a scene that needed no widen
        # still needs the claim, or the proxies ship clipped anyway.
        self._require_range_coverage(math.floor(rng[0]), math.ceil(rng[1]))
        scene = self._scene()
        start = min(scene.frame_start, math.floor(rng[0]))
        end = max(scene.frame_end, math.ceil(rng[1]))
        if (start, end) == (scene.frame_start, scene.frame_end):
            return
        self._set_frame_range(start, end)
        self.logger.info(
            f"Widened the export frame range to {int(start)}-{int(end)} so the "
            "staged keyed-weight curves bake in full."
        )

    def apply_declared_takes(self, mode: Union[bool, str, None] = "both"):
        """Ship the declared shots, the whole sequence, or both.

        Producer-agnostic mirror of mayatk's task: refreshes every producer's
        ``data_export`` channel (skipped when ``export_data_node`` already did
        so this run — the two tasks are default-on neighbors, and one refresh
        per export is enough), then arms ``FbxUtils`` with whatever takes
        the scene declares, folding the carrier into the export
        selection with them (a scene declaring none is a true no-op);
        the write realizes them by splitting its baked scene-range AnimStack
        (see ``fbx_utils``' module docstring for the divergence from Maya's
        exporter-state mechanism). Blender's split REPLACES the single
        scene-range take with the per-shot ones, so on the FBX leg the two
        shot-bearing modes ship the same takes; the GLB leg keeps the
        difference -- :meth:`create_glb` carries *mode* to the conversion as
        ``clip_mode``, and the sequence has to be CUT before it can be
        dropped, so which clips survive is decided on the deliverable.

        Parameters:
            mode: ``"both"`` (shots + the whole-timeline sequence),
                ``"shots"``, or ``"full"``. A legacy ``True``/``False`` from a
                preset written when this row was a checkbox maps onto
                ``"both"``/``"full"`` respectively.

        Widens the scene range to cover the takes it arms, so every window lies
        inside the baked span whatever else runs — the same self-guarantee Maya's
        ``FbxUtils.apply_takes`` makes internally. It no longer DECIDES the range:
        ``set_bake_animation_range`` owns that and runs after this (TASK_ORDER),
        widening in turn to cover what this task stamped. Splitting is all this
        task means now; clamping an export to its shots is <b>Bake Range: Auto</b>.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        mode = ptk.ExportRun.clip_mode(mode)
        # Read by create_glb, after the FBX is written. Set even on the paths
        # that return early: the GLB is converted whether or not a take was
        # ever realized, and it still has to know which clips to keep.
        self._clip_mode = mode

        # The carrier task is off: publish here, once, so the takes armed
        # below are the ones the carrier declares.
        self.ensure_scene_records_published()

        if mode == "full":
            # No split: the FBX ships its scene-range take, and the converter
            # is told to keep only that. The declared shots still ride on the
            # carrier as METADATA (extras.animation_web publishes each one's
            # frame range so a player can seek the window inside the clip).
            self.logger.info(
                "Animation clips: shipping the full sequence only; the "
                "declared shots are published as metadata, not as clips."
            )
            return

        count = FbxUtils.apply_takes_from_node()
        if count:
            # The carrier ships WITH the clips, never instead of them (mirror
            # of mayatk's ordering, and load-bearing for the same reason now
            # that this task is default-on): included unconditionally, it
            # handed the carrier back to a user who had deliberately unchecked
            # "Export Scene Data Node", on a scene with no shots at all.
            self._include_data_export_node()
            # Armed takes are sticky FbxUtils state consumed by EVERY write
            # until reset — stage the clear deferred (post-write) so nothing
            # leaks into a later export this session (mirror of mayatk's
            # fbx_takes deferred restore).
            self.stage_deferred_restore("fbx_takes", FbxUtils.reset_takes)
            takes = FbxUtils._pending_takes or []
            scene = self._scene()
            start = min(scene.frame_start, *(s for _n, s, _e in takes))
            end = max(scene.frame_end, *(e for _n, _s, e in takes))
            if (start, end) != (scene.frame_start, scene.frame_end):
                self._set_frame_range(start, end)
                self.logger.debug(
                    f"Widened the scene range to {start}-{end} so every take "
                    "window lies inside the baked span."
                )
            # The union this task realized, CLAIMED so set_bake_animation_range
            # (which runs after it) widens to cover it. Read off the takes rather
            # than off the scene range, which a later mode is about to overwrite.
            self._require_range_coverage(
                min(s for _n, s, _e in takes),
                max(e for _n, _s, e in takes),
            )
            self.logger.info(
                f"Animation takes: {count} clip(s) armed from the declared "
                "takes; shot metadata embedded on data_export."
            )
        else:
            self.logger.debug("No takes declared. Skipping animation takes.")

    #: The panel that acts on a record's export notes (mirror of mayatk's):
    #: record key -> (link label, panel name), opened by ``action://show``.
    NOTE_PANELS = {ptk.SceneRecords.SHOTS.key: ("Open Shots", "shots")}

    def _log_data_node_summary(self):
        """Log what this run published on ``data_export`` -- the snapshot's
        own summary (mirror of mayatk's), so a silently-empty export is
        distinguishable from a populated one -- and what a producer left out
        of it (:meth:`_log_snapshot_notes`).  Best-effort: never aborts the
        export it describes."""
        snapshot = self._scene_snapshot
        if snapshot is None:
            return
        try:
            summary = snapshot.summary()
            if summary:
                self.logger.info(f"Embedded on data_export: {summary}.")
        except Exception:  # a summary must never break the export it describes
            self.logger.debug("data_export summary skipped.", exc_info=True)
        self._log_snapshot_notes()

    def _log_snapshot_notes(self):
        """Log, as warnings, what a producer left out of this run's publish
        (``ExportSnapshot.noted``), each with a link to the panel that acts on
        it (:attr:`NOTE_PANELS`).  Every publish path calls it -- the carrier
        task's summary and the fallback publish of a run with that task off
        (mirror of mayatk's).  Best-effort: never aborts the export."""
        snapshot = self._scene_snapshot
        if snapshot is None:
            return
        try:
            for key, notes in snapshot.noted.items():
                link = self._note_link(key)
                for note in notes:
                    self.logger.warning(f"{note} {link}" if link else note)
        except Exception:  # a note must never break the export it describes
            self.logger.debug("data_export notes skipped.", exc_info=True)

    def _note_link(self, key: str) -> str:
        """The link to the panel that acts on *key*'s notes, or ``""``."""
        entry = self.NOTE_PANELS.get(key)
        build = getattr(self.logger, "log_link", None)
        if entry is None or build is None:
            return ""
        label, panel = entry
        return build(label, "show", ui=panel)

    def _restore_bake_session(self) -> None:
        """Undo :meth:`smart_bake`'s session -- the restore that task stages.

        Swaps the original actions back and unmutes the constraints and
        drivers the bake silenced (``SmartBake.restore``). Staged right after
        the bake, so LIFO unwinds it before anything an earlier task staged.
        Animation Output at Scene Keys (In Place) keeps the bake instead (the
        manifest stays recorded for a manual ``SmartBake.restore('<id>')``).
        Never raises: a failure is logged, and every other staged restore
        still runs. Mirror of mayatk's.
        """
        from blendertk.anim_utils.smart_bake._smart_bake import SmartBake

        session, self._bake_session_id = self._bake_session_id, None
        if not session:
            return
        if self.run.animation_write_back:
            self.logger.info(
                f"Bake kept in the scene (session '{session}') — Animation "
                "Output is set to Scene Keys (In Place)."
            )
            return
        try:
            restore = SmartBake.restore(session)
            if restore.success:
                self.logger.info(
                    f"Restored pre-bake scene state (session '{session}')."
                )
            else:
                # The session stays in the manifest until a restore completes,
                # so a manual retry is possible.
                self.logger.warning(
                    f"SmartBake restore failed for session '{session}' — "
                    "constraints/drivers may still be muted; retry with "
                    f"SmartBake.restore('{session}')."
                )
        except Exception as e:  # noqa: BLE001 -- a restore never raises
            self.logger.error(f"SmartBake restore failed: {e}")
