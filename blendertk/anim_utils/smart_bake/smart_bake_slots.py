# !/usr/bin/python
# coding=utf-8
"""Slots for the Smart Bake tool panel (``smart_bake.ui``) — Blender port of mayatk's
``anim_utils.smart_bake.smart_bake_slots`` (same widget objectNames where the concept
translates directly; see ``_smart_bake.py``'s module docstring for what diverges and why).

Composition, not inheritance (mirrors ``HierarchyManagerSlots``/``RenderOpacitySlots``):
:class:`SmartBake` is instantiated fresh per bake from the panel's current option state, not
held as a persistent collaborator. Self-contained (``ptk.LoggingMixin`` only) so blendertk
carries no back-dependency on tentacle; discovered by ``BlenderUiHandler``
(``marking_menu.show("smart_bake")``).

Divergence from mayatk's panel (mirrors the engine's own scope cuts — see ``_smart_bake.py``):
no Bake-Inherited-Visibility checkbox (architecturally absent on Blender — there is no
inherited-visibility bake pass to expose). ``Mute Sources`` / ``Delete Sources`` map 1:1 onto the
engine's own ``use_override`` / ``delete_sources`` keywords — mayatk's separate
``chk_override_layer``/``chk_mute_drivers`` collapse into these two, since Blender's bake has no
base-layer-conversion mode to give a standalone "mute the driver instead of the layer" meaning
(see the engine's module docstring). ``Preserve Outside Keys``, ``Optimize Keys``, and the
``Backup`` combo are wired straight through to the engine's own ``preserve_outside_keys``/
``optimize_keys``/``backup_file`` parameters.
"""
from typing import List, Optional

import pythontk as ptk
from uitk.switchboard import Cancelable

from blendertk.core_utils._core_utils import selected_objects
from blendertk.anim_utils.smart_bake._smart_bake import SmartBake


class SmartBakeSlots(ptk.LoggingMixin):
    """Controller wiring ``smart_bake.ui`` to the :class:`SmartBake` engine."""

    # Combo index -> SmartBake(backup_file=...) value. Index is the source of truth so
    # reordering the combo labels can't silently remap the value (mirrors mayatk's own
    # smart_bake_slots._BACKUP_MODES verbatim).
    _BACKUP_MODES = (("Auto", None), ("Always", True), ("Never", False))

    def __init__(self, switchboard, log_level: str = "INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.smart_bake

        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        # setup_logging_redirect() defaults its *own* level to INFO, which would silently
        # override whatever log_level the caller just set — pass the resolved level through
        # explicitly so log_level actually governs verbosity.
        self.logger.setup_logging_redirect(self.ui.txt000, level=self.logger.level)
        self.logger.hide_logger_name(True)
        self.logger.log_timestamp = "%H:%M:%S"

        # Deferred: child widgets (footer, checkboxes, combos) aren't wired onto self.ui until
        # register_children runs after __init__ returns.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Wire cross-widget behavior and sync the Unbake button to scene state.

        Deferred from __init__ (see there) so the full UI is registered before any
        ``self.ui.<widget>`` access.
        """
        self.sb.add_reset_buttons(self.ui)
        self.ui.chk_delete_sources.toggled.connect(self._on_delete_sources_toggled)
        self._on_delete_sources_toggled(self.ui.chk_delete_sources.isChecked())
        self._log_getting_started()
        self._refresh_session_state()

    def _log_getting_started(self) -> None:
        """Print a one-time orientation block to the output panel."""
        self.logger.log_box(
            "Smart Bake",
            [
                "1. Pick Scope — Auto (whole scene, default) or Selected.",
                "2. Adjust the options above, then click Bake.",
                "3. Click Unbake anytime — even after saving and",
                "   reopening the scene — to reverse the last bake.",
            ],
        )

    def _log_run_header(self, title: str) -> None:
        """Blank line + colored section title + divider, opening a new bake/unbake report so
        consecutive runs stay visually distinct."""
        self.logger.log_raw("")
        self.logger.notice(title)
        self.logger.log_divider()

    def _warn(self, msg: str) -> None:
        """Write *msg* to the footer (transient) and the output panel (persistent) — every
        warning in this panel needs both."""
        self.ui.footer.setText(msg, level="warning")
        self.logger.warning(msg)

    def _succeed(
        self,
        msg: str,
        details: Optional[List[str]] = None,
        item_color: Optional[str] = None,
    ) -> None:
        """Write *msg* to the footer (transient) and the output panel (persistent). With
        *details*, renders a colored group; otherwise a plain success line."""
        self.ui.footer.setText(msg, level="success")
        if details:
            kwargs = {"item_color": item_color} if item_color else {}
            self.logger.log_group(msg, details, level="SUCCESS", **kwargs)
        else:
            self.logger.success(msg)

    def cmb_scope_init(self, widget) -> None:
        # Auto is index 0 (the default): analyze() + bake() already restrict a whole-scene
        # scope to driven objects, so Auto costs nothing extra over "Selected" — it just names
        # what already happens.
        widget.add(["Auto (Whole Scene)", "Selected"])

    def cmb_backup_init(self, widget) -> None:
        widget.add([label for label, _ in self._BACKUP_MODES])

    def header_init(self, widget) -> None:
        """Configure header menu, refresh button, and help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._refresh_session_state)
        widget.menu.add(
            "QPushButton",
            setText="Reset to Defaults",
            setObjectName="reset_defaults",
            setToolTip="Reset every field in this panel to its default value.",
        )
        widget.set_help_text(
            fmt(
                title="Smart Bake",
                body="Analyzes the scene for constraints, IK, drivers, and driven shape-key "
                "weights, then bakes only the objects/bones that are actually driven — "
                "already-keyed channels are untouched.",
                steps=[
                    "Pick <b>Scope</b> — <b>Auto</b> (default) scans the whole scene and "
                    "bakes only what's actually driven; <b>Selected</b> restricts the scan "
                    "to your selection.",
                    "Adjust the options below and click <b>Bake</b>.",
                    "Click <b>Unbake</b> at any time — even after saving and reopening the "
                    "scene — to reverse the most recent bake.",
                ],
                sections=[
                    (
                        "Safety",
                        [
                            "<b>Mute Sources</b> (default on) mutes every constraint/driver/IK "
                            "identified by analysis after baking; the original rig stays "
                            "connected but overridden — fully reversible.",
                            "<b>Delete Sources</b> permanently removes those constraints/"
                            "drivers instead. Not reversible by Unbake for those sources; "
                            "always wins over Mute Sources when both are checked. A scene "
                            "backup is saved automatically unless <b>Backup</b> is set to "
                            "Never.",
                        ],
                    )
                ],
            )
        )

    def _on_delete_sources_toggled(self, checked: bool) -> None:
        # Delete Sources always wins over Mute Sources in the engine (see _smart_bake.py's
        # bake()), so Mute Sources would be silently ignored while Delete Sources is checked —
        # disable it to make that precedence visible rather than surprising.
        self.ui.chk_use_override.setDisabled(checked)

    def reset_defaults(self) -> None:
        """Header menu: reset every field in this panel to its registry default."""
        self.ui.state.reset_all()

    def _scope_objects(self) -> Optional[List]:
        """Selected scope -> the selection (possibly empty); Auto -> None (SmartBake then
        scans every mesh/empty/armature and bakes only the ones analyze() finds actually
        driven)."""
        if self.ui.cmb_scope.currentIndex() == 1:  # Selected
            return selected_objects()
        return None

    def _backup_value(self):
        return self._BACKUP_MODES[self.ui.cmb_backup.currentIndex()][1]

    @Cancelable(180)
    def b000(self, widget) -> None:
        """Bake."""
        objects = self._scope_objects()
        if objects == []:
            # Selected scope with nothing selected must NOT silently escalate to a
            # whole-scene bake (objects=None would).
            self._warn(
                "Nothing selected — select objects, or set Scope to Auto (Whole Scene)."
            )
            return

        self._log_run_header("Bake")

        baker = SmartBake(
            objects=objects,
            sample_by=self.ui.spn_sample_by.value(),
            use_override=self.ui.chk_use_override.isChecked(),
            delete_sources=self.ui.chk_delete_sources.isChecked(),
            bake_blend_shapes=self.ui.chk_bake_blend_shapes.isChecked(),
            preserve_outside_keys=self.ui.chk_preserve_outside.isChecked(),
            optimize_keys=self.ui.chk_optimize.isChecked(),
            backup_file=self._backup_value(),
        )

        # All footer messaging happens AFTER the progress context: its __exit__
        # synchronously writes "Complete" to the status label, so text set inside the block
        # would be clobbered on exit.
        result = None
        with self.ui.footer.progress(text="Analyzing scene…") as update:
            self.logger.info("Analyzing scene…")
            analysis = baker.analyze()
            if any(a.requires_bake for a in analysis.values()):
                update(None, "Baking…")
                self.logger.info("Baking…")
                result = baker.bake(analysis)

        if result is None:
            self._warn(
                "Nothing to bake — no constraints, IK, drivers, or driven shape keys detected."
            )
        else:
            self._report_bake_result(result, baker)
        self._refresh_session_state()

    def _report_bake_result(self, result, baker: SmartBake) -> None:
        if not result.success:
            self._warn("Bake produced no output.")
            return

        summary = (
            f"Baked {result.baked_count} item(s), "
            f"range {result.time_range[0]}-{result.time_range[1]}."
        )

        details = [f"{key}: {', '.join(sources)}" for key, sources in result.baked.items()]
        if result.skipped:
            details.append(f"Skipped {len(result.skipped)} item(s).")
        if result.muted_constraints:
            details.append(f"Muted {len(result.muted_constraints)} constraint(s).")
        if result.muted_drivers:
            details.append(f"Muted {len(result.muted_drivers)} driver(s).")
        if result.optimized:
            details.append(f"Optimized {len(result.optimized)} item(s).")
        if result.backup_path:
            details.append(f"Backup saved: {result.backup_path}")
        if result.session_id and not baker.delete_sources:
            details.append(
                f"Restorable — session '{result.session_id}'. Click Unbake to reverse."
            )
        elif result.session_id:
            details.append(
                f"Session '{result.session_id}' recorded, but Delete Sources bakes cannot "
                "be fully reversed — Unbake will report what it couldn't rebuild."
            )
        else:
            details.append("Not restorable.")

        self._succeed(summary, details)

    @Cancelable(60)
    def b001(self, widget) -> None:
        """Unbake."""
        self._log_run_header("Unbake")
        restore = SmartBake.restore()
        if not restore.success:
            self._warn(restore.warnings[0] if restore.warnings else "Nothing to restore.")
        else:
            summary = f"Restored session '{restore.session_id}'."
            self._succeed(
                summary, restore.warnings, item_color=self.LOG_COLORS["WARNING"]
            )
        self._refresh_session_state()

    def _refresh_session_state(self) -> None:
        # Non-restorable (delete_sources) sessions stay clickable on purpose: the click reports
        # what couldn't be rebuilt and pops the dead entry so any older restorable session
        # becomes reachable on the next click.
        try:
            pending = SmartBake.list_sessions()
        except Exception:
            # No running Blender (e.g. an offscreen Qt-only structural test, or a panel
            # opened before bpy is importable) — leave Unbake disabled rather than raising
            # out of a deferred init call (mirrors render_opacity_slots's bpy-touch guard).
            pending = []
        self.ui.b001.setEnabled(bool(pending))
        self.ui.b001.setToolTip(
            f"Restore the most recent of {len(pending)} pending bake(s)."
            if pending
            else "No bakes pending restore."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("smart_bake", reload=True)
    ui.show(pos="screen", app_exec=True)
