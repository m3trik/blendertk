# !/usr/bin/python
# coding=utf-8
"""Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.

Blender port of mayatk's ``edit_utils.mirror`` panel: the actual mirror is the general
``EditUtils.mirror`` engine (bmesh duplicate + reflect with optional seam weld) and, for the
Bounding-Box-*center* pivot, ``EditUtils.cut_along_axis(delete=True, mirror=True)`` (the same
symmetrize routing the Maya slot uses). The Slots class lives here next to nothing but its
``.ui`` — the engine it drives is shared in ``_edit_utils`` — exactly like mayatk's thin
``MirrorSlots``. Discovered + served by ``BlenderUiHandler`` (``marking_menu.show("mirror")``).

The ``.ui`` is a verbatim copy of mayatk's — same objectNames (``cmb000``/``cmb001``,
``chk000-6``, ``b000``) — now that uitk host-namespaces the QSettings branch per DCC
(``Switchboard.add_ui`` / ``MainWindow._relative_state`` via ``context_tags``), identical
objectNames across mayatk's and blendertk's copy of the same panel no longer collide in the
shared "uitk"/"shared" registry root, so there is no need to renumber widgets to dodge it.

Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on tentacle.
The Qt-only ``uitk`` helper is deferred into its method (headless Blender ships no Qt binding).
"""
import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.node_utils._node_utils import NodeUtils


class MirrorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the mirror UI (live preview + axis/pivot/merge combos).

    Live preview via :class:`blendertk.Preview` (snapshot/restore). Mirrors mayatk's
    ``MirrorSlots`` 1:1 (same method names / signal-connection order); see the two Merge-mode
    and Pivot notes below for the two spots where the engines currently diverge.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mirror

        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[mirror] ")

        # Merge:Extrude (cmb001 index 3) has no engine support — see
        # _disable_unsupported_merge_mode. Do this before add_reset_buttons/connect_multi
        # wrap the widgets, same ordering reason as those two calls below.
        self._disable_unsupported_merge_mode()

        # Per-field reset buttons (uitk option-box) on the Pivot / Merge Mode combos — Mirror
        # has no numeric params, and the Axis checkboxes are a mutually-exclusive group (a
        # per-box reset would be confusing). Click resets the combo to its default; Alt/Ctrl
        # +click bypasses it. Must precede connect_multi/Preview — wrapping reparents the
        # widgets and invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui, "cmb000-1")

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Mirror",
        )

        # The '-' (negative axis) toggle only changes the result for the bounding-box pivots —
        # Center symmetrizes and the sign picks which half survives; Border picks the min vs
        # max face. For Manip/Object/World the mirror reflects across a fixed plane, so the
        # sign is a no-op there. Connect BEFORE the preview-refresh wiring so the enabled/
        # checked state is settled before perform_operation re-reads the axis on a pivot change.
        self.ui.cmb000.currentIndexChanged.connect(self._sync_axis_sign_enabled)

        # Connect combos and checkboxes to preview refresh function
        self.sb.connect_multi(
            self.ui, "cmb000-1", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)

        # NOTE (blender-parity): mayatk also refreshes the preview when the Maya viewport
        # pivot changes live (PivotWatcher, built on Maya's scriptJob event bus). Blender has
        # no equivalent "manip drag" event to subscribe to — the Transform Pivot Point is a
        # scene setting, not a live-draggable gizmo, and building a poll-based watcher
        # (bpy.app.timers) for this one panel would be new architecture, not a small addition.
        # The Manip pivot is still resolved fresh on every perform_operation call (any widget
        # -driven refresh picks up the current setting); it just won't auto-refresh from an
        # out-of-band Transform Pivot Point / 3D-cursor change alone.
        # TODO(blender-parity): a shared bpy.app.timers-based pivot watcher, if a future tool
        # needs it too.

        # Settle the '-' toggle's enabled state for the initial (default / restored) pivot
        # before the user interacts.
        self._sync_axis_sign_enabled()

    def _disable_unsupported_merge_mode(self) -> None:
        """Disable the "Merge:  Extrude" item (cmb001 index 3) — it needs a bmesh
        ``bridge_loops``-style operation that matches and orders the two open edge loops on
        either side of the seam before bridging them with new side-wall faces. That loop-
        matching/ordering is a genuinely new algorithm, not a small addition to the existing
        weld-based ``EditUtils.mirror``, so only this one combo item is disabled (the rest of
        the combo works normally) — see the item model, not the ``.ui``, per the parity plan.
        # TODO(blender-parity): implement a bmesh-bridge Merge:Extrude mode and re-enable.
        """
        model = self.ui.cmb001.model()
        item = model.item(3) if model is not None else None
        if item is not None:
            item.setFlags(item.flags() & ~self.sb.QtCore.Qt.ItemIsEnabled)
            item.setToolTip(
                "Not yet implemented in blendertk — bridging the seam with new side-wall "
                "faces needs edge-loop matching the mirror engine doesn't do yet."
            )

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
                    "negative; it only applies to the Bounding Box pivots and is "
                    "disabled otherwise.",
                    "Pick a <b>Pivot</b> — Manip / Object / World, or a Bounding "
                    "Box pivot (see below).",
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
                        "<b>Manip</b> pivot follows Blender's Transform Pivot Point "
                        "setting (3D Cursor / Bounding Box / Active Element / …).",
                        "<b>Un-Instance</b> — make shared mesh data single-user first.",
                        "<b>Delete Original</b> — with Merge OFF, remove the source "
                        "object once the mirrored copy exists.",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects):
        # Read values from UI
        axis = self.sb.get_axis_from_checkboxes(
            "chk001-4", self.ui
        )  # e.g. "x" or "-x"; "-" only honored for the bounding-box pivots
        # get_axis_from_checkboxes returns "" / "-" when no X/Y/Z is selected; guard here so
        # both paths give the same clear error instead of a raw KeyError downstream.
        if axis.lstrip("-") not in ("x", "y", "z"):
            raise ValueError("Select an axis (X / Y / Z) to mirror across.")

        pivot_index = self.ui.cmb000.currentIndex()
        uninstance = self.ui.chk005.isChecked()

        # Bounding Box (center): reflecting the whole object across its own center just
        # overlaps it, so this pivot SYMMETRIZES instead — cut at the center, keep one half,
        # and mirror it across the cut plane. invert=True makes the UI's "+X" keep the +X half
        # (the btk cut_along_axis convention deletes the signed-axis side, mirroring Maya).
        if pivot_index == 3:
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

        pivot = self._resolve_pivot(pivot_index, axis)
        # mergeMode 2 ("Merge:  Extrude") is disabled in the combo (see
        # _disable_unsupported_merge_mode) — no engine branch for it exists below.
        merge_mode = self.ui.cmb001.currentIndex() - 1  # -1 for correct mapping

        EditUtils.mirror(
            objects,
            axis=axis,
            pivot=pivot,
            merge_mode=merge_mode,
            uninstance=uninstance,
            delete_original=self.ui.chk006.isChecked(),
        )

    @staticmethod
    def _axis_sign_relevant(pivot_index: int) -> bool:
        """Whether the '-' (negative axis) toggle changes the mirror result.

        Only the bounding-box pivots use the sign: Center (index 3) picks which half
        survives the symmetrize; Border (index 4) picks the min vs max face. Manip /
        Object / World reflect across a fixed plane, so the sign is a no-op there and the
        toggle is disabled.
        """
        return pivot_index in (3, 4)

    def _sync_axis_sign_enabled(self, *args) -> None:
        """Enable the '-' toggle only where the sign matters; uncheck it when disabling so
        a stale sign can't leak into a pivot that ignores it."""
        relevant = self._axis_sign_relevant(self.ui.cmb000.currentIndex())
        self.ui.chk001.setEnabled(relevant)
        if not relevant and self.ui.chk001.isChecked():
            self.ui.chk001.setChecked(False)

    @staticmethod
    def _resolve_pivot(pivot_index: int, axis: str) -> str:
        # Bounding-box BORDER pivot (index 4): the axis sign selects which face the mirror
        # reflects across — +axis -> max face, -axis -> min face — flipping the side the
        # geometry doubles toward.
        base = axis.lstrip("-")
        face = f"{base}min" if axis.startswith("-") else f"{base}max"

        pivot_mapping = {
            0: "manip",
            1: "object",
            2: "world",
            3: "center",
            4: face,
        }

        return pivot_mapping.get(pivot_index, "manip")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("mirror", reload=True)
    ui.show(pos="screen", app_exec=True)
