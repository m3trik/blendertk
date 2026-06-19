# !/usr/bin/python
# coding=utf-8
import sys
import os
from typing import TYPE_CHECKING
from uitk import Switchboard
from uitk.handlers.ui_handler import UiHandler

if TYPE_CHECKING:
    from qtpy import QtWidgets


class BlenderUiHandler(UiHandler):
    """UI Handler for Blender applications.

    The Blender analogue of :class:`mayatk.ui_utils.maya_ui_handler.MayaUiHandler`:
    it scans the **blendertk package** recursively so a tool that ships its own
    Switchboard ``.ui`` + ``<Tool>Slots`` co-located with its logic (e.g.
    ``edit_utils/curtain.ui`` + ``CurtainSlots``) is auto-discovered and served by
    ``marking_menu.show("<tool>")`` — exactly the way mayatk tools are. This keeps the
    Blender tool panels in blendertk (next to the code that drives them), not in
    tentacle, mirroring the mayatk/tentacle split.

    Unlike Maya, Blender draws its own UI in OpenGL (no ``QMenu`` objects to wrap), so
    there is no native-menu loading here — Blender's native menus are popped directly
    by the marking menu's both-button chord via ``btk.call_native_menu``.
    """

    def __init__(
        self,
        switchboard: Switchboard = None,
        log_level: str = "WARNING",
        **kwargs,
    ) -> None:
        """Initialize the Blender UI Handler.

        ``switchboard`` is optional. When omitted a fresh ``Switchboard`` is
        constructed so the handler can be stood up by a startup script without any
        prior setup. Production callers (tentacle's ``tcl_blender``) pass an explicit
        instance to share state with the rest of the application.
        """
        self.root_dir = os.path.dirname(sys.modules["blendertk"].__file__)

        if switchboard is None:
            from uitk import Switchboard as _Switchboard

            switchboard = _Switchboard()

        super().__init__(
            switchboard=switchboard,
            ui_root=self.root_dir,
            slot_root=self.root_dir,
            discover_slots=True,
            recursive=True,
            log_level=log_level,
            source_tags={"blendertk"},
            **kwargs,
        )

    @classmethod
    def instance(cls, switchboard: Switchboard = None, **kwargs) -> "BlenderUiHandler":
        """Return the BlenderUiHandler singleton, bootstrapping if needed.

        Mirrors :meth:`MayaUiHandler.instance`: a no-argument call returns an existing
        handler (e.g. the one tentacle's ``tcl_blender`` built with the app switchboard)
        rather than keying a fresh, broken handler off ``id(None)``. This makes the
        shelf/console one-liner reliable::

            from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler
            BlenderUiHandler.instance().get("curtain").show(pos="screen")
        """
        if switchboard is None:
            # SingletonMixin._instances is shared across subclasses; filter to ours.
            for inst in cls._instances.values():
                if isinstance(inst, cls):
                    return inst
        return super().instance(switchboard=switchboard, **kwargs)

    def apply_styles(self, ui, style=None):
        """Give blendertk-sourced tool panels a hide button instead of a pin.

        Matches MayaUiHandler: a tool panel popped from a marking-menu button is
        transient, so the default ``pin`` button is swapped for ``hide``.
        """
        import copy

        style = copy.deepcopy(style or self.DEFAULT_STYLE)
        try:
            if ui.has_tags(["blendertk"]):
                style["header_buttons"] = ("menu", "collapse", "hide")
        except AttributeError:
            pass
        # Pass the pre-built style so the base skips its own deepcopy.
        super().apply_styles(ui, style=style)
