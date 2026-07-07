# !/usr/bin/python
# coding=utf-8
"""Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.

mayatk's Arnold bridge attaches a parallel ``aiStandardSurface`` shading-engine network to a
Maya material so the same asset previews correctly under Arnold (MtoA) *inside Maya* while the
exported Stingray / Standard Surface material is untouched -- Maya's two renderers read two
separate shader graphs off the same shading engine.

Blender has no such split: Cycles and EEVEE both read the *same* Principled BSDF node graph, and
this codebase ships no Arnold-for-Blender integration at all (no MtoA-equivalent addon, no
``aiStandardSurface`` node type). There is nothing to attach and nothing to preview under, so
this is a deliberately inert, disabled panel -- copied 1:1 from mayatk's ``.ui`` per this repo's
port convention ("if something is not applicable, disable the widget with a tooltip note"),
wired to no-op slots that explain why rather than silently doing nothing.

``ArnoldBridge`` itself has no scene-mutating implementation for the same reason: there is no
Blender-side operation for it to perform. It exists for structural/API parity only.
"""
from typing import List, Optional, Union

import pythontk as ptk

try:  # UI-only helper; keep the headless import clean if uitk is absent
    from uitk.widgets.mixins.tooltip_mixin import fmt
except Exception:
    fmt = None


_NOT_AVAILABLE = (
    "Not available in Blender: this codebase ships no Arnold-for-Blender integration "
    "(no MtoA-equivalent addon, no aiStandardSurface node type). Cycles and EEVEE both "
    "read the same Principled BSDF graph, so there is no parallel preview network to "
    "attach the way mayatk's Arnold bridge does in Maya."
)


class ArnoldBridge(ptk.LoggingMixin):
    """Structural mirror of mayatk's ``ArnoldBridge`` -- no-op on Blender (see module docstring).

    Kept importable (rather than omitted) so code that duck-types across ``mtk.ArnoldBridge`` /
    ``btk.ArnoldBridge`` doesn't need a branch just to detect blendertk's absence of the concept.
    """

    def add(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
        force: bool = False,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    def remove(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    def rebuild(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    def get_bridge(self, material) -> Optional[str]:
        return None

    def has_bridge(self, material) -> bool:
        return False


class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``arnold_bridge.ui`` panel -- every control disabled.

    The ``.ui`` is copied verbatim from mayatk (structural parity per this repo's port
    convention); every functional widget is disabled with a tooltip explaining why, and the
    header help text carries the same explanation. See :data:`_NOT_AVAILABLE`.
    """

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.arnold_bridge

        self.ui.cmb000.setEnabled(False)
        self.ui.cmb000.setToolTip(_NOT_AVAILABLE)
        self.ui.chk000.setEnabled(False)
        self.ui.chk000.setToolTip(_NOT_AVAILABLE)
        self.ui.b000.setEnabled(False)
        self.ui.b000.setToolTip(_NOT_AVAILABLE)
        self.ui.b001.setEnabled(False)
        self.ui.b001.setToolTip(_NOT_AVAILABLE)

    # ------------------------------------------------------------------ header
    def header_init(self, widget) -> None:
        """Configure the help text (no menu actions -- the panel is inert)."""
        if fmt is not None:
            widget.set_help_text(
                fmt(
                    title="Arnold Preview Shader",
                    body="Not available in Blender.",
                    sections=[
                        ("Why", [_NOT_AVAILABLE]),
                    ],
                )
            )

    # -------------------------------------------------------------------- combo
    def cmb000_init(self, widget) -> None:
        """Populate with mayatk's scope labels for visual parity (the combo itself stays
        disabled -- see class docstring; there is nothing to scope)."""
        widget.clear()
        widget.addItems(["Selected Objects", "All Scene Materials"])
        widget.setCurrentIndex(0)

    # ------------------------------------------------------------------ actions
    # b000 (Add Network) / b001 (Remove Network) are disabled in __init__; no handlers needed.


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("arnold_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
