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

``import bpy`` is deferred into the call bodies and the Qt-only ``uitk`` helper into its method, so
importing the module / resolving the package surface never needs a running Blender or Qt.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import undo_checkpoint
from blendertk.rig_utils._rig_utils import RigUtils


class TelescopeRig(ptk.LoggingMixin):
    """Constraint + driver telescoping-segment rig (mirror of mayatk's ``TelescopeRig``)."""

    def __init__(self, log_level="WARNING"):
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
        """
        import bpy

        base = RigUtils.resolve_object(base_locator)
        end = RigUtils.resolve_object(end_locator)
        if base is None or end is None:
            raise ValueError("A valid base and end handle must be provided.")
        segs = [s for s in (RigUtils.resolve_object(o) for o in ptk.make_iterable(segments)) if s is not None]
        if len(segs) < 2:
            raise ValueError("At least two segments must be provided.")

        bpy.context.view_layer.update()
        base_pos = base.matrix_world.translation.copy()
        end_pos = end.matrix_world.translation.copy()
        initial_distance = (end_pos - base_pos).length
        if initial_distance < 1e-6:
            raise ValueError("The base and end handles must not be coincident.")

        # Handles aim at each other so the chain keeps a consistent up-axis.
        RigUtils.damped_track(base, end, "TRACK_Y")
        RigUtils.damped_track(end, base, "TRACK_NEGATIVE_Y")

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
            # Middle segments telescope: scale.y tracks the live base→end distance.
            if 0 < k < k_last:
                RigUtils.add_distance_driver(seg, "scale", 1, base, end, expression=f"dist / {initial_distance!r}")
            # Location & rotation are constraint-driven; only scale.y telescopes.
            RigUtils.lock_channels(
                seg, location=(True, True, True), rotation=(True, True, True), scale=(True, False, True)
            )

        RigUtils.refresh_drivers(segs)  # post-build recompile (script-built driver gotcha)
        base["telescope_collapsed_distance"] = float(collapsed_distance)
        self.logger.success(f"Telescope Rig built on {len(segs)} segment(s).")
        return segs


class TelescopeRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Telescope Rig panel.

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle; the Qt-only ``uitk`` helper is deferred into ``header_init``.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.telescope_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[telescope_rig] ")

        # Stream build status into the panel's log browser (txt003), mirroring the Maya panel.
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt003)
        except (AttributeError, TypeError):
            pass

        self.ui.btn_build.clicked.connect(self.build_rig)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Telescope Rig",
                body="Build a telescoping segment chain whose middle segments extend and retract "
                "between a base and end handle, driven by their distance.",
                steps=[
                    "Select the handles + segment tubes (min 4 objects total).",
                    "Make the <b>base handle</b> the <b>active</b> object — the farthest selected "
                    "object becomes the end handle, the rest are the segments.",
                    "Press <b>Build</b> to wire the constraints + scale driver.",
                ],
                notes=[
                    "Empties make natural handles, but any objects work.",
                    "Blender orders by distance (not click-order), so the active = base contract "
                    "replaces Maya's strict selection order.",
                    "The driver is continuous, so the Collapsed Distance is informational — the "
                    "collapse ratio is automatic.",
                ],
            )
        )

    def build_rig(self):
        import bpy

        sel = [o for o in (bpy.context.selected_objects or []) if o]
        base = bpy.context.view_layer.objects.active
        if len(sel) < 4 or base is None or base not in sel:
            self.sb.message_box(
                "Telescope Rig needs at least 4 selected objects, with the BASE handle active.\n"
                "Active = base · farthest selected = end · the rest = segments."
            )
            return

        bpy.context.view_layer.update()
        base_pos = base.matrix_world.translation
        others = sorted(
            (o for o in sel if o is not base),
            key=lambda o: (o.matrix_world.translation - base_pos).length,
        )
        end, segments = others[-1], others[:-1]  # farthest = end; rest near→far = segments

        self.logger.info(f"Base: <hl>{base.name}</hl> · End: <hl>{end.name}</hl>")
        self.logger.info(f"Segments: <hl>{len(segments)}</hl>")
        try:
            rig = TelescopeRig()
            # Stream the engine's logs into the same panel browser (mirror the Maya panel).
            # ``logger`` is a ClassProperty (no setter) — configure it, never reassign it.
            try:
                rig.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
                rig.logger.setup_logging_redirect(self.ui.txt003)
            except (AttributeError, TypeError):
                pass
            rig.setup_telescope_rig(
                base, end, segments, collapsed_distance=self.ui.spin_collapsed.value()
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {e}")
            self.sb.message_box(f"Error setting up rig: {e}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
