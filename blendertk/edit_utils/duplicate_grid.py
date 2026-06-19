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

from blendertk.core_utils._core_utils import _object_mode
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _copy_object, _group_under_empty, _join_copies


# Maya prompts before huge grids; headless we hard-cap instead (a 50³ drag would hang).
GRID_MAX_COPIES = 10000


@_object_mode
def duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode="instance"):
    """Duplicate object(s) into a 3D grid — mirror of mayatk's ``DuplicateGrid.duplicate_grid``.

    ``dimensions`` = per-axis cell counts (negative counts lay the grid out in the opposite
    direction); the step per axis is the source's world-bbox size plus ``spacing``. The
    source object itself occupies the ``(0, 0, 0)`` cell and is never mutated — copies fill
    the remaining cells. ``mode``: ``"instance"`` (linked duplicates) / ``"copy"`` /
    ``"combine"`` (one joined mesh). Instance/copy results are grouped under an Empty
    (``<name>_grid``). Returns ``{original: [copies]}``.
    """
    import bpy
    from mathutils import Matrix

    from blendertk.xform_utils._xform_utils import get_world_bbox

    counts = [max(abs(int(d)), 1) for d in dimensions]
    total = counts[0] * counts[1] * counts[2] - 1
    if total > GRID_MAX_COPIES:
        raise ValueError(
            f"Grid of {total} copies exceeds the {GRID_MAX_COPIES} cap — reduce the counts."
        )
    signs = [-1.0 if d < 0 else 1.0 for d in dimensions]

    out = {}
    for src in (o for o in ptk.make_iterable(objects) if o):
        mn, mx = get_world_bbox(src)
        steps = [(mx[i] - mn[i] + spacing) * signs[i] for i in range(3)]
        m0 = src.matrix_world.copy()
        copies = []
        for ix, iy, iz in itertools.product(*(range(c) for c in counts)):
            if (ix, iy, iz) == (0, 0, 0):
                continue  # the source occupies the origin cell
            dup = _copy_object(src, instance=(mode == "instance"))
            dup.matrix_world = (
                Matrix.Translation((ix * steps[0], iy * steps[1], iz * steps[2])) @ m0
            )
            copies.append(dup)
        if not copies:
            out[src] = []
            continue
        if mode == "combine":
            copies = [_join_copies(copies, f"{src.name}_grid")]
        else:
            _group_under_empty(copies, f"{src.name}_grid")
        out[src] = copies
    bpy.context.view_layer.update()
    return out


class DuplicateGrid:
    """Namespace mirror of mayatk's ``DuplicateGrid`` (helper also exposed module-level)."""

    duplicate_grid = staticmethod(duplicate_grid)


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class DuplicateGridSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Duplicate-Grid panel.

    Oversized grids raise instead of prompting (``GRID_MAX_COPIES``), which the preview
    surfaces as a message. Self-contained (``ptk.LoggingMixin`` only).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_grid
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[duplicate_grid] ")

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Duplicate Grid",
        )
        self.sb.connect_multi(self.ui, "s000-3", "valueChanged", self.preview.refresh)

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
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Duplicate Grid",
                body="Duplicate selected objects into a 3D grid layout.",
                steps=[
                    "Select one or more objects.",
                    "Set per-axis counts <b>X</b> / <b>Y</b> / <b>Z</b> and a "
                    "uniform <b>Spacing</b> (added to the bounding-box step).",
                    "Toggle <b>Preview</b> to iterate, or press <b>Duplicate</b> "
                    "to commit.",
                ],
                sections=[
                    ("Output", [
                        "<b>Combine</b> — merge every copy into a single mesh.",
                        "<b>Instance</b> — linked duplicates sharing one mesh, "
                        "grouped under an Empty.",
                        "<b>Unique</b> — independent copies, grouped under an Empty.",
                    ]),
                ],
                notes=[
                    "Counts can be negative to lay the grid out in the opposite "
                    "direction. The source object keeps the origin cell.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
        duplicate_grid(
            objects,
            dimensions=(
                self.ui.s000.value(),
                self.ui.s001.value(),
                self.ui.s002.value(),
            ),
            spacing=self.ui.s003.value(),
            mode=self.ui.cmb000.currentData(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)
