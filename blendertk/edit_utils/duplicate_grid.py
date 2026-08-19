# !/usr/bin/python
# coding=utf-8
"""Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.

``duplicate_grid`` lays copies out in a 3D grid via ``mathutils`` translation matrices (the
source keeps the origin cell); ``DuplicateGridSlots`` is the Switchboard wiring for the
co-located ``duplicate_grid.ui`` panel, discovered + served by ``BlenderUiHandler``
(``marking_menu.show("duplicate_grid")``).

``import bpy`` / ``mathutils`` (and the Qt-only ``uitk`` helpers) are deferred into the call
bodies (no import side effects; headless Blender ships no Qt binding).
"""

import itertools

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import EditUtils


# Maya prompts before huge grids; headless we also hard-cap (a 50³ drag would hang) — the
# slots' BULK_THRESHOLD confirmation below sits well under this, so the cap is a last-resort
# backstop for a build the user already confirmed (or a scripted call with no UI at all).
GRID_MAX_COPIES = 10000


class DuplicateGrid:
    """Grid-array duplication engine — mirror of mayatk's ``DuplicateGrid``.

    Reached as ``btk.DuplicateGrid.duplicate_grid`` (class-only; the co-located
    ``DuplicateGridSlots`` panel is discovered by ``BlenderUiHandler``, so the module is not
    wildcard-scanned onto the flat ``btk.*`` namespace)."""

    @staticmethod
    def _normalize_spacing(spacing):
        """Coerce a scalar or per-axis spacing into an ``(sx, sy, sz)`` tuple —
        mirror of mayatk's ``DuplicateGrid._normalize_spacing``. A plain number
        applies uniformly to all three axes (the historical contract); any
        3-element sequence is used verbatim for per-axis gaps."""
        if isinstance(spacing, (int, float)):
            return float(spacing), float(spacing), float(spacing)
        sx, sy, sz = spacing
        return float(sx), float(sy), float(sz)

    @staticmethod
    @CoreUtils._object_mode
    def duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode="instance"):
        """Duplicate object(s) into a 3D grid — mirror of mayatk's ``DuplicateGrid.duplicate_grid``.

        ``dimensions`` = per-axis cell counts (negative counts lay the grid out in the opposite
        direction); the step per axis is the source's world-bbox size plus ``spacing``. The
        source object itself occupies the ``(0, 0, 0)`` cell and is never mutated — copies fill
        the remaining cells. ``spacing`` is a single float (uniform) or a ``(sx, sy, sz)`` tuple
        for a per-axis gap. ``mode``: ``"instance"`` (linked duplicates) / ``"copy"`` /
        ``"combine"`` (one joined mesh). Instance/copy results are grouped under an Empty
        (``<name>_grid``). Returns ``{original: [copies]}``.
        """
        import bpy
        from mathutils import Matrix

        from blendertk.xform_utils._xform_utils import XformUtils

        counts = [max(abs(int(d)), 1) for d in dimensions]
        total = counts[0] * counts[1] * counts[2] - 1
        if total > GRID_MAX_COPIES:
            raise ValueError(
                f"Grid of {total} copies exceeds the {GRID_MAX_COPIES} cap — reduce the counts."
            )
        signs = [-1.0 if d < 0 else 1.0 for d in dimensions]
        space = DuplicateGrid._normalize_spacing(spacing)

        out = {}
        for src in (o for o in ptk.make_iterable(objects) if o):
            mn, mx = XformUtils.get_world_bbox(src)
            steps = [(mx[i] - mn[i] + space[i]) * signs[i] for i in range(3)]
            m0 = src.matrix_world.copy()
            copies = []
            for ix, iy, iz in itertools.product(*(range(c) for c in counts)):
                if (ix, iy, iz) == (0, 0, 0):
                    continue  # the source occupies the origin cell
                dup = EditUtils._copy_object(src, instance=(mode == "instance"))
                dup.matrix_world = (
                    Matrix.Translation((ix * steps[0], iy * steps[1], iz * steps[2]))
                    @ m0
                )
                copies.append(dup)
            if not copies:
                out[src] = []
                continue
            if mode == "combine":
                copies = [EditUtils._join_copies(copies, f"{src.name}_grid")]
            else:
                EditUtils._group_under_empty(copies, f"{src.name}_grid")
            out[src] = copies
        bpy.context.view_layer.update()
        return out


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class DuplicateGridSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Duplicate-Grid panel — 1:1 objectName mirror of
    mayatk's ``DuplicateGridSlots`` (cross-host QSettings collision is host-namespaced by
    Switchboard/MainWindow now, so identical objectNames between the two copies of this panel
    are safe — no renumbering workaround needed here).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle. Unlike mayatk's Preview (node-diff + ``MUTATES_SELECTION``-gated preserve/
    restore), blendertk's ``Preview`` is snapshot-based (captures every operated object's
    datablock + matrix + collection links up front, unconditionally), so there is no Blender
    analogue of that flag here.
    """

    # Total copies above which we ask the user to confirm before building — mirror of
    # mayatk's ``BULK_THRESHOLD`` (grid growth is cubic, unlike duplicate_linear/_radial's
    # linear counts, so this is the one duplicate_* tool where a confirm gate earns its keep).
    BULK_THRESHOLD = 1000

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_grid
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[duplicate_grid] ")

        # Largest grid count the user has approved this session (so a confirmed
        # bulk build doesn't re-prompt on every preview refresh).
        self._confirmed_count = 0

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        # Per-axis Spacing X/Y/Z (s003-5) each get a lock toggle: locking two or
        # more links them so a change to one shifts the others by the same delta.
        self.sb.link_spinboxes(self.ui, "s003-5")

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Duplicate Grid",
        )
        # s000-2 are the X/Y/Z counts, s003-5 the per-axis spacing.
        self.sb.connect_multi(self.ui, "s000-5", "valueChanged", self.preview.refresh)

        # Output mode: how the copies are produced (same keys as the btk helper).
        self.ui.cmb000.add(
            [
                ("Combine", "combine"),
                ("Instance", "instance"),
                ("Unique", "copy"),
            ],
            prefix="Output:",
        )
        self.ui.cmb000.setAsCurrent("instance")
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Duplicate Grid",
                body="Duplicate selected objects into a 3D grid layout.",
                steps=[
                    "Select one or more objects.",
                    "Set per-axis counts <b>Dimensions X/Y/Z</b> (minimum 1) and "
                    "the per-axis <b>Spacing X/Y/Z</b> gap added to the bounding-box step.",
                    "Each field has a reset button; each spacing field also has a "
                    "<b>lock</b> — lock two or more to move them together by the "
                    "same amount. Resetting a field clears its lock.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Duplicate</b> "
                    "to commit.",
                ],
                sections=[
                    (
                        "Output",
                        [
                            "<b>Combine</b> — merge every copy into a single mesh.",
                            "<b>Instance</b> — linked duplicates sharing one mesh, "
                            "grouped under an Empty.",
                            "<b>Unique</b> — independent copies, grouped under an Empty.",
                        ],
                    ),
                ],
                notes=[
                    "The source object keeps the origin cell. Spacing may be "
                    "negative to overlap copies.",
                    "Very large grids prompt for confirmation first.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()
        self._confirmed_count = 0

    def perform_operation(self, objects):
        dimensions = (
            self.ui.s000.value(),
            self.ui.s001.value(),
            self.ui.s002.value(),
        )
        # Per-axis spacing: Spacing X / Y / Z (s003 / s004 / s005).
        spacing = (
            self.ui.s003.value(),
            self.ui.s004.value(),
            self.ui.s005.value(),
        )
        mode = self.ui.cmb000.currentData()

        if not self._confirm_bulk(dimensions, objects):
            return

        DuplicateGrid.duplicate_grid(
            objects, dimensions=dimensions, spacing=spacing, mode=mode
        )

    def _confirm_bulk(self, dimensions, objects) -> bool:
        """Gate large builds behind a confirmation dialog.

        Returns True to proceed. The approved magnitude is cached so a confirmed
        bulk build doesn't re-prompt on every preview refresh. On decline the
        preview is switched off directly — safe to call from inside
        ``perform_operation`` here: nothing has been created yet (we bail before
        calling :meth:`DuplicateGrid.duplicate_grid`), so the resulting rollback is a no-op and
        the checkbox simply unchecks, matching mayatk's deferred-disable outcome
        without needing an evalDeferred-style trick.
        """
        counts = [max(abs(int(d)), 1) for d in dimensions]
        total = (counts[0] * counts[1] * counts[2] - 1) * max(len(objects), 1)
        if total <= self.BULK_THRESHOLD or total <= self._confirmed_count:
            return True

        proceed = (
            self.sb.message_box(
                f"This will create <b>{total:,}</b> objects, which may be slow.<br>"
                "Continue?",
                "Yes",
                "No",
            )
            == "Yes"
        )
        if proceed:
            self._confirmed_count = total
            return True

        # Declined: stop the preview so dragging the count further doesn't
        # re-prompt on every refresh tick.
        self.preview.disable()
        return False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)
