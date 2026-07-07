# !/usr/bin/python
# coding=utf-8
import sys
import os
from uitk import Switchboard
from uitk.handlers.ui_handler import UiHandler
from blendertk.ui_utils.blender_native_menus import BlenderNativeMenus


class BlenderUiHandler(UiHandler):
    """UI Handler for Blender applications.

    The Blender analogue of :class:`mayatk.ui_utils.maya_ui_handler.MayaUiHandler`:
    it scans the **blendertk package** recursively so a tool that ships its own
    Switchboard ``.ui`` + ``<Tool>Slots`` co-located with its logic (e.g.
    ``edit_utils/curtain.ui`` + ``CurtainSlots``) is auto-discovered and served by
    ``marking_menu.show("<tool>")`` — exactly the way mayatk tools are. This keeps the
    Blender tool panels in blendertk (next to the code that drives them), not in
    tentacle, mirroring the mayatk/tentacle split.

    Like ``MayaUiHandler``, it also **wraps the DCC's own native menus** for the both-button
    chord menu (``ui/blender_menus`` — the mirror of ``ui/maya_menus``). Maya harvests the live
    ``QAction`` rows into a floating Qt window; Blender draws its UI in OpenGL (no ``QAction``s to
    harvest), so the faithful wrap is to invoke Blender's real menu via ``btk.call_native_menu``.
    The mechanism is otherwise identical to Maya's: a bare ``MenuButton`` target (``"mesh"``,
    ``"select"``, …) resolves through :meth:`can_resolve` (membership in
    :class:`BlenderNativeMenus`), and :meth:`show` pops the native menu instead of a Qt window.
    See :meth:`_register_native_menu_proxies` for how the resolution is wired through the shared
    switchboard's ``get_ui`` (which resolves names via ``loaded_ui``, never the handler directly).
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

        # Wrap Blender's native menus for the both-button chord menu (mirror of the way
        # MayaUiHandler wraps Maya's). Register a lightweight proxy per symbolic node name so a
        # release on a bare-target MenuButton resolves to a real UI via the shared switchboard's
        # get_ui (loaded_ui lookup), and pops the native menu on show — see the methods below.
        self._register_native_menu_proxies()

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

    # ── Native-menu wrap (both-button chord menu) ────────────────────────
    # Marker attribute stamped on a proxy so :meth:`show` knows to pop the native menu.
    _NATIVE_MENU_ATTR = "_blender_native_menu"

    def can_resolve(self, name: str) -> bool:
        """Recognise the native Blender menus this handler wraps on demand.

        The Blender mirror of :meth:`MayaUiHandler.can_resolve`: a both-button-menu ``MenuButton``
        whose bare ``target`` is a native menu (``"mesh"``, ``"select"`` …) is not a ``.ui`` file
        stem, so uitk's ``ui_name_resolves`` consults this hook to decide the release opens the
        native menu (resolvable) rather than falling back to the ``<name>#submenu`` hover overlay.
        Membership only; nothing is built here.
        """
        if name in BlenderNativeMenus.names():
            return True
        return super().can_resolve(name)

    def _register_native_menu_proxies(self) -> None:
        """Pre-register one proxy UI per native-menu node name in ``loaded_ui``.

        The switchboard resolves ``get_ui(name)`` via ``loaded_ui`` (file stem / slot class),
        never by calling ``handler.get`` — so unlike Maya's lazily-built wrappers, the proxy has
        to already exist in ``loaded_ui`` for the release path (``_resolve_button_menu`` ->
        ``_cached_ui`` -> ``sb.get_ui``) to return it. Each proxy is an empty, never-shown
        ``MainWindow`` tagged ``{"blender", "menu"}`` (so the marking menu treats it as a
        standalone target, not a stacked submenu) carrying the symbolic name on
        :attr:`_NATIVE_MENU_ATTR`; :meth:`show` reads that and pops the real Blender menu instead
        of showing the empty window. Cheap (no content), and skipped if a name is already loaded.

        The proxies are pinned in ``self._native_menu_proxies`` because ``loaded_ui`` holds only
        *weak* references — the same reason ``MayaNativeMenus`` keeps its wrappers in ``.menus``.
        Without a strong ref the proxy is garbage-collected the moment registration returns and
        the ``loaded_ui`` entry goes dead, so the release path would fall through to the overlay.
        """
        self._native_menu_proxies = {}
        for name in BlenderNativeMenus.names():
            if name in self.sb.loaded_ui:
                continue
            proxy = self.sb.add_ui(
                name=name,
                tags={"blender", "menu"},
                add_footer=False,
                restore_window_size=False,
            )
            setattr(proxy, self._NATIVE_MENU_ATTR, name)
            self._native_menu_proxies[name] = proxy

    def show(self, ui, pos=None, force: bool = False, **kwargs):
        """Pop the wrapped Blender menu when ``ui`` resolves to a native-menu proxy; else default show.

        The both-button menu's release dispatch calls ``ui_handler.show(proxy)`` (via
        ``MarkingMenu._show_window``). For a native-menu proxy this must NOT show the empty Qt
        window — it pops Blender's own menu (``wm.call_menu``) and returns. Any other UI (a
        blendertk tool panel) goes through the normal :class:`UiHandler` show. A name string is
        resolved first, so a direct ``show("mesh")`` pops the menu too (not the empty proxy).
        """
        if isinstance(ui, str):
            ui = self.get(ui, **kwargs)
        if ui is None:
            return None
        name = getattr(ui, self._NATIVE_MENU_ATTR, None)
        if name is not None:
            self._pop_native_menu(name)
            return ui
        return super().show(ui, pos=pos, force=force, **kwargs)

    @staticmethod
    def _pop_native_menu(name: str) -> None:
        """Pop the native Blender menu for symbolic ``name``, one timer tick later.

        Deferred a tick (as the old slot path was) so the Qt overlay has hidden and OS focus has
        returned to Blender before ``wm.call_menu`` — otherwise the popup lands behind the
        always-on-top overlay. ``bpy`` is imported here (show time only), so construction /
        resolution stay Blender-free.
        """
        import bpy
        import blendertk as btk

        idname = BlenderNativeMenus.resolve(name)
        if not idname:
            return

        def _deferred():
            try:
                btk.call_native_menu(idname)
            except Exception:
                pass
            return None  # one-shot

        bpy.app.timers.register(_deferred, first_interval=0.05)

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
