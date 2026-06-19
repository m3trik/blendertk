# !/usr/bin/python
# coding=utf-8
"""Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.

Blender port of mayatk's ``display_utils.exploded_view`` panel (``ExplodedViewSlots``): spread the
selected meshes outward from their shared bbox center for inspection, with an exact, reversible
restore. The explode/unexplode **engine** already lives module-level in
:mod:`~blendertk.display_utils._display_utils` (``explode_view`` / ``unexplode_view`` /
``unexplode_all`` / ``is_exploded``) — Maya uses an iterative repulsive-force solve; blendertk uses
a deterministic bbox-center separation (headless-testable) that stamps each pre-explode location as
a custom prop so the toggle survives save/reload. This module is just the panel wiring; the Slots
class is discovered and served by ``BlenderUiHandler`` (``marking_menu.show("exploded_view")``).

``import bpy`` is deferred into the call bodies and the Qt-only ``uitk`` helper into ``header_init``.
"""
import pythontk as ptk

from blendertk.display_utils._display_utils import (
    explode_view,
    unexplode_view,
    unexplode_all,
    is_exploded,
)


class ExplodedViewSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Exploded View panel (mirror of mayatk's ``ExplodedViewSlots``).

    Self-contained (``ptk.LoggingMixin`` only); calls the module-level engine directly.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.exploded_view
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[exploded_view] ")

    _NEED_TWO = "Select two or more mesh objects to explode apart."

    def _mesh_selection(self):
        import bpy

        return [o for o in (bpy.context.selected_objects or []) if o and o.type == "MESH"]

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Exploded View",
                body="Spread selected objects outward from their shared center to inspect "
                "interior parts. Each object's pre-explode location is stamped as a custom "
                "property, so the explode is fully reversible.",
                sections=[
                    ("Actions", [
                        "<b>Explode</b> — push the selected meshes away from the group's bbox "
                        "center until their bounding boxes no longer overlap.",
                        "<b>Un-Explode</b> — return the selected objects to their stored "
                        "positions.",
                        "<b>Un-Explode All</b> — reset every exploded object in the scene "
                        "(regardless of selection).",
                        "<b>Toggle Explode</b> — alternate between exploded and original views "
                        "on the current selection.",
                    ]),
                ],
            )
        )

    def b000(self):
        """Explode."""
        meshes = self._mesh_selection()
        if len(meshes) < 2:  # warn only when genuinely under-selected (not when already exploded)
            self.sb.message_box(self._NEED_TWO)
            return
        explode_view(meshes)  # no-ops silently if already exploded

    def b001(self):
        """Un-Explode (selected)."""
        unexplode_view(self._mesh_selection())

    def b002(self):
        """Un-Explode All."""
        unexplode_all()

    def b003(self):
        """Toggle Explode."""
        meshes = self._mesh_selection()
        if is_exploded(meshes):
            unexplode_view(meshes)
        elif len(meshes) < 2:
            self.sb.message_box(self._NEED_TWO)
        else:
            explode_view(meshes)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("exploded_view", reload=True)
    ui.show(pos="screen", app_exec=True)
