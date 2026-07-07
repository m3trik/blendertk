# !/usr/bin/python
# coding=utf-8
"""Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.

Blender port of mayatk's ``edit_utils.snap`` panel (``SnapSlots``): snap vertices to a target
surface, to the closest target vertex, or to the world grid. The engine lives module-level in
:mod:`~blendertk.edit_utils._edit_utils` (``snap_to_surface`` / ``snap_closest_verts`` /
``snap_to_grid`` — mirror of mayatk's ``Snap`` class). Each button carries an option box (▸) built
in ``b###_init``, matching the Maya panel's option boxes exactly (same objectNames: ``s000``/
``s001``/``chk000`` on Surface, ``s002`` on Closest Vertex, ``s003``/``txt000`` on Grid).

Selection convention mirrors Maya's "source first, target last": the **active** object is the
target (Blender's equivalent of Maya's last-ordered selection), the rest are sources. The Slots
class is discovered and served by ``BlenderUiHandler`` (``marking_menu.show("snap")``).

``import bpy`` is deferred into the call bodies and the Qt-only ``uitk`` helper into ``header_init``.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import selected_objects
from blendertk.edit_utils._edit_utils import (
    snap_closest_verts,
    snap_to_grid,
    snap_to_surface,
)


class SnapSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Snap panel (mirror of mayatk's ``SnapSlots``).

    Self-contained (``ptk.LoggingMixin`` only); calls the module-level engine directly.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.snap
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[snap] ")

    def _source_target(self):
        """((sources), target) using the active object as the target (= Maya's last-ordered
        selection); both restricted to meshes. Returns ([], None) when under-selected."""
        import bpy

        meshes = [o for o in selected_objects() if o.type == "MESH"]
        target = bpy.context.view_layer.objects.active
        if len(meshes) < 2 or target is None or target.type != "MESH" or target not in meshes:
            return [], None
        return [o for o in meshes if o is not target], target

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Snap",
                body="Snap vertices to other vertices, surfaces, or the world grid. Each button "
                "has an option box (▸) for its per-tool parameters.",
                sections=[
                    ("Snap to Surface", [
                        "Select source mesh(es), then the target mesh <b>last (active)</b>.",
                        "Source verts are projected onto the target surface.",
                        "Option box: <b>Offset</b>, <b>Threshold</b>, <b>Invert</b>.",
                    ]),
                    ("Snap to Closest Vertex", [
                        "Select exactly two meshes: source, then target <b>last (active)</b>.",
                        "Source verts within <b>Tolerance</b> snap to the closest target vert.",
                    ]),
                    ("Snap to Grid", [
                        "Select objects (or vertices in Edit Mode).",
                        "Option box: <b>Grid Size</b> + per-axis filter (xyz).",
                        "Edit Mode snaps selected verts; Object Mode snaps each object's origin.",
                    ]),
                ],
            )
        )

    def b000_init(self, widget):
        """Initialize Snap to Surface button option box."""
        widget.option_box.menu.setTitle("Snap to Surface")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Offset: ",
            setObjectName="s000",
            set_limits=[0, 100, 0.01, 1],
            setValue=0.0,
            setToolTip="Distance from surface to place affected vertices.",
        )
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Threshold: ",
            setObjectName="s001",
            set_limits=[0, 1000, 0.1, 1],
            setValue=0.0,
            setToolTip="Only process vertices within this distance. 0 = no limit.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Invert",
            setObjectName="chk000",
            setChecked=False,
            setToolTip="Invert direction (use if target normals point inward).",
        )

    def b000(self):
        """Snap to Surface button."""
        sources, target = self._source_target()
        if not target:
            self.sb.message_box(
                "Select source mesh(es) first, then the target mesh last (active)."
            )
            return

        offset = self.ui.b000.menu.s000.value()
        threshold = self.ui.b000.menu.s001.value() or None  # 0 means no limit
        invert = self.ui.b000.menu.chk000.isChecked()

        count = snap_to_surface(
            sources,
            target,
            offset=offset,
            threshold=threshold,
            invert=invert,
        )
        self.sb.message_box(f"<hl>Snapped {count} vertices to surface.</hl>")

    def b001_init(self, widget):
        """Initialize Snap to Closest Vertex button option box."""
        widget.option_box.menu.setTitle("Snap to Closest Vertex")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Tolerance: ",
            setObjectName="s002",
            set_limits=[0, 1000, 0.1, 1],
            setValue=10.0,
            setToolTip="Maximum search distance for matching vertices.",
        )

    def b001(self):
        """Snap to Closest Vertex button."""
        sources, target = self._source_target()
        if not target or len(sources) != 1:
            self.sb.message_box(
                "Select exactly two meshes: source first, then target last (active)."
            )
            return

        tolerance = self.ui.b001.menu.s002.value()

        count = snap_closest_verts(sources[0], target, tolerance=tolerance)
        self.sb.message_box(f"<hl>Snapped {count} vertices.</hl>")

    def b002_init(self, widget):
        """Initialize Snap to Grid button option box."""
        widget.option_box.menu.setTitle("Snap to Grid")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Grid Size: ",
            setObjectName="s003",
            set_limits=[0.001, 1000, 0.1, 3],
            setValue=1.0,
            setToolTip="Grid spacing to snap to.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Axes (xyz)...",
            setObjectName="txt000",
            setText="xyz",
            setToolTip="Which axes to snap: x, y, z, or combinations like xy.",
        )

    def b002(self):
        """Snap to Grid button."""
        grid_size = self.ui.b002.menu.s003.value()
        axes = self.ui.b002.menu.txt000.text() or "xyz"

        count = snap_to_grid(grid_size=grid_size, axes=axes)
        self.sb.message_box(f"<hl>Snapped {count} items to grid.</hl>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("snap", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
