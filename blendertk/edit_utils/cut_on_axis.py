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

    The pivot combo is Blender-specific (``cmb001`` — fresh number, no Manip entry).
    Live preview via :class:`blendertk.Preview`.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.cut_on_axis
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[cut_on_axis] ")

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Cut On Axis",
        )

        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)
        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)
        self.ui.cmb001.currentIndexChanged.connect(self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Cut on Axis",
                body="Slice selected meshes along an axis, then optionally delete "
                "or mirror the cut half.",
                steps=[
                    "Select one or more mesh objects.",
                    "Check an <b>Axis</b>; the <b>—</b> toggle makes it negative.",
                    "Pick a <b>Pivot</b> — Object / World / Bounding Box center.",
                    "Set <b>Cuts</b> (number of slices) and <b>Offset</b>.",
                    "Toggle <b>Preview</b>, then press <b>Cut</b> to commit.",
                ],
                sections=[
                    ("Options", [
                        "<b>Delete</b> — discard the geometry on the signed-axis "
                        "side of the deepest cut.",
                        "<b>Mirror</b> — after deleting, reflect the surviving half "
                        "across the cut plane and weld the seam (symmetrize).",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects):
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        if axis.lstrip("-") not in ("x", "y", "z"):
            raise ValueError("Select an axis (X / Y / Z) to cut along.")
        cuts = self.ui.s000.value()
        if cuts < 1:
            raise ValueError("Set at least one cut.")

        pivot = {0: "object", 1: "world", 2: "center"}.get(
            self.ui.cmb001.currentIndex(), "center"
        )
        EditUtils.cut_along_axis(
            objects,
            axis=axis,
            pivot=pivot,
            amount=cuts,
            offset=self.ui.s001.value(),
            delete=self.ui.chk005.isChecked(),
            mirror=self.ui.chk006.isChecked(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("cut_on_axis", reload=True)
    ui.show(pos="screen", app_exec=True)
