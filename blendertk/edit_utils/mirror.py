# !/usr/bin/python
# coding=utf-8
"""Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.

Blender port of mayatk's ``edit_utils.mirror`` panel: the actual mirror is the general
``EditUtils.mirror`` engine (bmesh duplicate + reflect with optional seam weld) and, for the
Bounding-Box-*center* pivot, ``EditUtils.cut_along_axis(delete=True, mirror=True)`` (the same
symmetrize routing the Maya slot uses). The Slots class lives here next to nothing but its
``.ui`` — the engine it drives is shared in ``_edit_utils`` — exactly like mayatk's thin
``MirrorSlots``. Discovered + served by ``BlenderUiHandler`` (``marking_menu.show("mirror")``).

Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on tentacle.
The Qt-only ``uitk`` helper is deferred into its method (headless Blender ships no Qt binding).
"""
import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.node_utils._node_utils import NodeUtils


class MirrorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the mirror UI (live preview + axis/pivot/merge combos).

    The pivot/merge combos are Blender-specific (``cmb002``/``cmb003`` — fresh numbers so
    state can't bleed into the Maya panel's QSettings store). Live preview via
    :class:`blendertk.Preview` (snapshot/restore).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mirror
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[mirror] ")

        # Per-field reset buttons must precede connect_multi/Preview (wrap-first
        # optimization — see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui, "cmb002-3")

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Mirror",
        )

        # The '-' toggle only changes the result for the bounding-box pivots (center: which
        # half survives; border: min vs max face) — connect BEFORE the refresh wiring so the
        # enabled state settles before perform_operation re-reads the axis.
        self.ui.cmb002.currentIndexChanged.connect(self._sync_axis_sign_enabled)

        self.sb.connect_multi(
            self.ui, "cmb002-3", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)

        self._sync_axis_sign_enabled()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Mirror",
                body="Mirror selected meshes across an axis, optionally merging "
                "seam vertices or keeping the mirrored half as its own object.",
                steps=[
                    "Select one or more mesh objects.",
                    "Check an <b>Axis</b> (X / Y / Z). The <b>—</b> toggle makes it "
                    "negative; it only applies to the Bounding Box pivots.",
                    "Pick a <b>Pivot</b> — Object / World, or a Bounding Box pivot.",
                    "Pick a <b>Merge Mode</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Create</b> to commit.",
                ],
                sections=[
                    ("Bounding Box pivots", [
                        "<b>Center</b> — keep one half and mirror it across the "
                        "center to symmetrize. The <b>—</b> toggle flips which half "
                        "is kept.",
                        "<b>Border</b> — mirror across the max face of the axis; "
                        "the <b>—</b> toggle flips it to the min face.",
                    ]),
                    ("Options", [
                        "<b>Un-Instance</b> — make shared mesh data single-user first.",
                        "<b>Delete Original</b> — with Merge OFF, remove the source "
                        "object once the mirrored copy exists.",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects):
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        if axis.lstrip("-") not in ("x", "y", "z"):
            raise ValueError("Select an axis (X / Y / Z) to mirror across.")

        pivot_index = self.ui.cmb002.currentIndex()
        uninstance = self.ui.chk005.isChecked()

        # Bounding Box (center): reflecting the whole object across its own center just
        # overlaps it, so this pivot SYMMETRIZES — cut at the center, keep one half, mirror
        # it across the cut. invert=True makes the UI's "+X" keep the +X half (the btk
        # convention deletes the signed-axis side, mirroring Maya).
        if pivot_index == 2:
            if uninstance:
                NodeUtils.uninstance(objects)
            EditUtils.cut_along_axis(
                objects,
                axis=axis,
                invert=True,
                pivot="center",
                amount=1,
                delete=True,
                mirror=True,
            )
            return

        EditUtils.mirror(
            objects,
            axis=axis,
            pivot=self._resolve_pivot(pivot_index, axis),
            merge_mode=self.ui.cmb003.currentIndex() - 1,
            uninstance=uninstance,
            delete_original=self.ui.chk006.isChecked(),
        )

    @staticmethod
    def _axis_sign_relevant(pivot_index: int) -> bool:
        """The '-' toggle only matters for the bounding-box pivots: Center (index 2) picks
        which half survives the symmetrize; Border (index 3) picks the min vs max face."""
        return pivot_index in (2, 3)

    def _sync_axis_sign_enabled(self, *args) -> None:
        """Enable the '-' toggle only where the sign matters; uncheck it when disabling so
        a stale sign can't leak into a pivot that ignores it."""
        relevant = self._axis_sign_relevant(self.ui.cmb002.currentIndex())
        self.ui.chk001.setEnabled(relevant)
        if not relevant and self.ui.chk001.isChecked():
            self.ui.chk001.setChecked(False)

    @staticmethod
    def _resolve_pivot(pivot_index: int, axis: str) -> str:
        """Map the pivot combo to an ``EditUtils.mirror`` pivot. Border (index 3): the axis
        sign selects the face — +axis -> max face, -axis -> min face."""
        base = axis.lstrip("-")
        face = f"{base}min" if axis.startswith("-") else f"{base}max"
        return {0: "object", 1: "world", 3: face}.get(pivot_index, "object")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("mirror", reload=True)
    ui.show(pos="screen", app_exec=True)
