# !/usr/bin/python
# coding=utf-8
"""Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.

Blender port of mayatk's ``rig_utils.telescope_rig`` (``btk.TelescopeRig`` ↔ ``mtk.TelescopeRig``):
build a chain of segments that telescopes — extends and retracts — between a base and an end handle,
the middle segments distributed evenly along the line and scaling with the base→end distance.

Maya wires this with a ``distanceBetween`` node + aim/point/parent constraints + driven keys; the
Blender analogue is idiomatic constraints + a single linear driver (the "relax the mirror where
concepts diverge" rule). Each handle ``Damped Track``s the other; each segment is positioned by a
pair of ``Copy Location`` constraints (a base→end lerp) and aimed along the chain; and each *middle*
segment's ``scale.y`` is driven by ``LOC_DIFF(base, end) / initial_distance`` — equivalent to Maya's
two-key driven curve (1.0 at the rest distance, ``collapsed/​initial`` when collapsed) but continuous
and dependency-cycle-free.

The ``.ui`` is a byte-identical copy of mayatk's (``header`` / ``grp_options.spin_collapsed`` /
``grp_finalize.btn_build`` / ``output_grp.txt003``) and ``TelescopeRigSlots`` mirrors mayatk's slot
1:1 — same widget names, same log-link wiring (clickable ``action://`` links in the log panel),
same ``header_init`` shape. Maya's strict click-order selection (base-first…end-last) has no
Blender analogue (selection order isn't reliably preserved), so ``build_rig`` uses Blender's own
idiom instead: the **active** object is the base, the **farthest** selected object is the end, and
the rest are the segments (near→far).

``import bpy`` is deferred into the call bodies and the Qt-only ``uitk`` helper into its method, so
importing the module / resolving the package surface never needs a running Blender or Qt.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import undo_checkpoint
from blendertk.rig_utils._rig_utils import RigUtils


class TelescopeRig(ptk.LoggingMixin):
    """Constraint + driver telescoping-segment rig (mirror of mayatk's ``TelescopeRig``)."""

    def __init__(self, log_level="WARNING"):
        """Initialize telescope rig with logging."""
        super().__init__()
        self.set_log_level(log_level)

    @undo_checkpoint
    def setup_telescope_rig(self, base_locator, end_locator, segments, collapsed_distance=1.0):
        """Wire a telescoping rig between two handles.

        Parameters:
            base_locator (str/object): The base handle (an Empty or any object).
            end_locator (str/object): The end handle.
            segments (list): Ordered segment objects (>= 2): the tube nearest the base first,
                the tube nearest the end last.
            collapsed_distance (float): Accepted for Maya signature parity and recorded on the
                base handle. Blender uses a *continuous* linear scale driver
                (``distance / initial_distance``) rather than Maya's two-key driven curve, so the
                collapsed scale falls out of the ratio automatically.

        Returns:
            list: the segment objects that were rigged.

        Raises:
            ValueError: If the base/end handles are invalid, coincident, or fewer than two
                segments are provided.
        """
        import bpy

        self.logger.info("Setting up Telescope Rig...", preset="header")

        base = RigUtils.resolve_object(base_locator)
        if base is None:
            self.logger.error("A valid base handle must be provided.")
            raise ValueError("A valid base handle must be provided.")

        end = RigUtils.resolve_object(end_locator)
        if end is None:
            self.logger.error("A valid end handle must be provided.")
            raise ValueError("A valid end handle must be provided.")

        segs = [s for s in (RigUtils.resolve_object(o) for o in ptk.make_iterable(segments)) if s is not None]
        if len(segs) < 2:
            self.logger.error("At least two segments must be provided.")
            raise ValueError("At least two segments must be provided.")

        bpy.context.view_layer.update()
        base_pos = base.matrix_world.translation.copy()
        end_pos = end.matrix_world.translation.copy()
        initial_distance = (end_pos - base_pos).length
        if initial_distance < 1e-6:
            raise ValueError("The base and end handles must not be coincident.")

        def constrain_locators():
            # Handles aim at each other so the chain keeps a consistent up-axis.
            RigUtils.damped_track(base, end, "TRACK_Y")
            RigUtils.damped_track(end, base, "TRACK_NEGATIVE_Y")
            self.logger.info("Locators constrained.")

        def constrain_segments():
            k_last = len(segs) - 1
            for k, seg in enumerate(segs):
                frac = k / k_last
                # Position = lerp(base, end, frac): copy the base fully, then blend toward the end.
                RigUtils.copy_location(seg, base, 1.0)
                if frac > 0.0:
                    RigUtils.copy_location(seg, end, frac)
                # Aim along the chain (the end tube aims back so its +Y stays chain-aligned).
                if k < k_last:
                    RigUtils.damped_track(seg, end, "TRACK_Y")
                else:
                    RigUtils.damped_track(seg, base, "TRACK_NEGATIVE_Y")
            self.logger.info("Segments constrained.")

        def set_driven_keys():
            # Middle segments telescope: scale.y tracks the live base->end distance (Blender's
            # continuous-driver analogue of Maya's two-key driven curve).
            k_last = len(segs) - 1
            for k, seg in enumerate(segs):
                if 0 < k < k_last:
                    RigUtils.add_distance_driver(
                        seg, "scale", 1, base, end, expression=f"dist / {initial_distance!r}"
                    )
            self.logger.info("Driven keys set.")

        def lock_segment_attributes():
            # Location & rotation are constraint-driven; only scale.y telescopes.
            for seg in segs:
                RigUtils.lock_channels(
                    seg, location=(True, True, True), rotation=(True, True, True), scale=(True, False, True)
                )

        constrain_locators()
        constrain_segments()
        set_driven_keys()
        lock_segment_attributes()

        RigUtils.refresh_drivers(segs)  # post-build recompile (script-built driver gotcha)
        base["telescope_collapsed_distance"] = float(collapsed_distance)

        self.logger.success("Telescope Rig setup complete.")
        return segs


class TelescopeRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Telescope Rig panel.

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle; the Qt-only ``uitk`` helper is deferred into ``header_init``.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.telescope_rig

        # Setup Logging Redirect. Best-effort: a mock/headless switchboard may not carry
        # ``registered_widgets.TextEditLogHandler`` — the panel still works without it.
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt003)
        except (AttributeError, TypeError):
            pass
        self.logger.info("Telescope Rig Tool initialized.", preset="italic")

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        # Connect Signals
        self.ui.btn_build.clicked.connect(self.build_rig)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Telescope Rig",
                body="Build a telescoping segment chain where segments extend "
                "and retract between a base and end handle, driven by their "
                "distance.",
                steps=[
                    "Select the base handle, the segments (min 2), and the end "
                    "handle — <b>at least 4 objects total</b>:",
                    "  &nbsp;1. Base handle",
                    "  &nbsp;2. Segments (min 2, in extension order)",
                    "  &nbsp;3. End handle",
                    "Make the <b>base handle active</b> — Blender doesn't preserve "
                    "click-order, so the active object is the base and the "
                    "<b>farthest</b> selected object becomes the end handle.",
                    "Set <b>Collapsed Dist</b> — informational; the driver is "
                    "continuous, so the collapse ratio falls out automatically.",
                    "Press <b>Build</b> to wire the constraints + scale driver "
                    "on each segment.",
                ],
                notes=[
                    "Build results stream to the log panel; handle names are "
                    "rendered as clickable <i>action://</i> links that select "
                    "the object in the viewport.",
                    "Empties make natural handles, but any objects work.",
                ],
            )
        )

    def build_rig(self):
        self.logger.log_divider()

        import bpy

        from blendertk.core_utils._core_utils import selected_objects

        sel = selected_objects()
        base = bpy.context.view_layer.objects.active
        if len(sel) < 4 or base is None or base not in sel:
            self.logger.error("Insufficient selection.")
            self.sb.message_box(
                "Selection Error:\n"
                "Please select at least 4 objects, with the BASE handle "
                "ACTIVE:\n"
                "1. Base Handle (active)\n"
                "2. Segments (min 2)\n"
                "3. End Handle (farthest from the base)"
            )
            return

        # Blender has no reliable click-order, so the active object is the base and the rest are
        # ranked by distance from it: farthest = end, the remainder (near->far) = segments.
        bpy.context.view_layer.update()
        base_pos = base.matrix_world.translation
        others = sorted(
            (o for o in sel if o is not base),
            key=lambda o: (o.matrix_world.translation - base_pos).length,
        )
        end = others[-1]
        segments = others[:-1]

        collapsed_dist = self.ui.spin_collapsed.value()

        try:
            rig = TelescopeRig()
            # Stream the engine's logs into the same panel browser (mirror the Maya panel).
            # ``logger`` is a ClassProperty (no setter) — configure it, never reassign it.
            try:
                rig.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
                rig.logger.setup_logging_redirect(self.ui.txt003)
            except (AttributeError, TypeError):
                pass

            base_link = self.logger.log_link(base.name, "select", node=base.name)
            end_link = self.logger.log_link(end.name, "select", node=end.name)
            self.logger.info(f"Base detected: {base_link}")
            self.logger.info(f"End detected: {end_link}")
            self.logger.info(f"Segments detected: <hl>{len(segments)}</hl>")

            rig.setup_telescope_rig(
                base_locator=base,
                end_locator=end,
                segments=segments,
                collapsed_distance=collapsed_dist,
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {str(e)}")
            self.sb.message_box(f"Error setting up rig: {str(e)}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
