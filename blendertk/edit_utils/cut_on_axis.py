# !/usr/bin/python
# coding=utf-8
"""Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.

Blender port of mayatk's ``edit_utils.cut_on_axis`` panel: the engine is the general
``EditUtils.cut_along_axis`` (bmesh ``bisect_plane`` — N evenly spaced slices centered on the
pivot, optional delete of the signed-axis half, optional mirror/symmetrize of the survivor).
The Slots class lives next to its ``.ui``; the engine it drives is shared in ``_edit_utils``,
exactly like mayatk's thin ``CutOnAxisSlots``. Served by ``BlenderUiHandler``
(``marking_menu.show("cut_on_axis")``).

Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into its
method (headless Blender ships no Qt binding).
"""
import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import EditUtils


class CutOnAxisSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the cut-on-axis UI (live preview).

    ``cmb000`` mirrors mayatk's pivot combo verbatim (Manip / Object / World / Bounding Box
    center). "Manip" resolves via the shared ``pivot="manip"`` path in ``_edit_utils``'s
    ``_plane_frame``/``_manip_point`` — Blender's active Transform Pivot Point setting
    (3D cursor / bounding-box center / else the object's own origin), the same mechanism
    ``mirror.py`` uses. Live preview via :class:`blendertk.Preview` (snapshot/restore).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.cut_on_axis
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[cut_on_axis] ")

        # Per-field reset buttons must precede connect_multi/Preview (wrap-first
        # optimization — see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Cut On Axis",
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)
        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

        # TODO(blender-parity): mayatk refreshes the preview on viewport pivot changes
        # (selection / tool / manipulator drag release) via mayatk.xform_utils.PivotWatcher.
        # blendertk has no equivalent poller yet; the Manip pivot only re-samples when a
        # widget fires (any Preview refresh), not on a bare cursor/pivot-mode change.

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            fmt(
                title="Cut on Axis",
                body="Slice selected meshes along an axis, then optionally "
                "delete or mirror the cut half.",
                steps=[
                    "Select one or more mesh objects.",
                    "Check an <b>Axis</b> (X / -X / Y / -Y / Z / -Z).",
                    "Pick a <b>Pivot</b> — Manip / Object / World / Center.",
                    "Set <b>Cuts</b> (number of slices) and <b>Offset</b>.",
                    "Toggle <b>Preview</b>, then press <b>Cut</b> to commit.",
                ],
                sections=[
                    ("Options", [
                        "<b>Manip</b> pivot follows Blender's Transform Pivot Point "
                        "setting (3D Cursor / Bounding Box / Active Element / …).",
                        "<b>Delete</b> — discard faces on the negative side of "
                        "the axis after cutting.",
                        "<b>Mirror</b> — after deleting one side, mirror the "
                        "remaining half across the axis to rebuild symmetric "
                        "geometry.",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects):
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        pivot_index = self.ui.cmb000.currentIndex()
        cuts = self.ui.s000.value()
        cut_offset = self.ui.s001.value()
        delete = self.ui.chk005.isChecked()
        mirror = self.ui.chk006.isChecked()

        pivot_options = {0: "manip", 1: "object", 2: "world", 3: "center"}
        pivot = pivot_options.get(pivot_index, "center")

        EditUtils.cut_along_axis(
            objects,
            axis=axis,
            pivot=pivot,
            amount=cuts,
            offset=cut_offset,
            delete=delete,
            mirror=mirror,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("cut_on_axis", reload=True)
    ui.show(pos="screen", app_exec=True)
