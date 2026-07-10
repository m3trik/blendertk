# !/usr/bin/python
# coding=utf-8
"""Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.

Blender port of mayatk's ``display_utils.exploded_view`` panel (``ExplodedViewSlots``): spread the
selected meshes outward from their shared bbox center for inspection, with an exact, reversible
restore. The explode/unexplode **engine** already lives module-level in
:mod:`~blendertk.display_utils._display_utils` (``explode_view`` / ``unexplode_view`` /
``unexplode_all`` / ``is_exploded``) — Maya uses an iterative repulsive-force solve; blendertk uses
a deterministic bbox-center separation (headless-testable) that stamps each pre-explode location as
a custom prop so the toggle survives save/reload. Target-*resolution* (which objects the buttons act
on) stays in this module rather than the shared engine file — mirroring mayatk, which keeps its
``objects`` property / ``_get_target_objects`` on the ``ExplodedView`` class in ``exploded_view.py``
rather than in the shared ``_display_utils.py``. The Slots class is discovered and served by
``BlenderUiHandler`` (``marking_menu.show("exploded_view")``).

``import bpy`` is deferred into the call bodies (via ``selected_objects()``) and the Qt-only
``uitk`` helper into ``header_init``.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import selected_objects
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
        """Resolve the target meshes from the current selection.

        Mirrors mayatk's ``ExplodedView.objects`` + ``NodeUtils.get_unique_children``: a
        selected "group" — a Blender Empty carrying no mesh data of its own — is expanded to
        its child meshes (recursively) instead of being treated as an explode target itself.
        Non-mesh, non-empty selections (curves, armatures, lights, …) are dropped, same as the
        prior behavior — ``explode_view``/``unexplode_view`` only ever operate on meshes.
        """

        def collect(obj, found):
            if obj.type == "MESH":
                found.add(obj)
            elif obj.type == "EMPTY":
                for child in obj.children:
                    collect(child, found)

        found = set()
        for obj in selected_objects():
            collect(obj, found)
        return list(found)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
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
        # Mirrors mayatk's toggle_explode reasoning: only collapse once the WHOLE selection is
        # already exploded; otherwise push out whatever isn't exploded yet. explode_view() only
        # stamps/moves the not-yet-exploded subset, so a partially-exploded selection converges
        # toward "fully exploded" one toggle at a time, same as mayatk's explode()/_get_target_
        # objects(unexploded=True) split.
        fully_exploded = bool(meshes) and all(is_exploded([m]) for m in meshes)
        if fully_exploded:
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
