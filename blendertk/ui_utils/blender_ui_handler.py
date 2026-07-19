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
    chord menu (``ui/blender_menus`` — the mirror of ``ui/maya_menus``). Maya lifts the live
    ``QAction`` rows off its menu bar; Blender draws its menus in OpenGL, so
    :class:`BlenderNativeMenus` *harvests* the menu's Python ``draw`` into an equivalent
    ``QMenu`` (:mod:`blendertk.ui_utils.menu_harvest`). Hosting is then identical to Maya's:
    a bare ``MenuButton`` target (``"mesh"``, ``"select"``, …) resolves through
    :meth:`can_resolve`, and :meth:`show` presents the wrapped menu in a Switchboard
    ``MainWindow`` — pin header, hides on ``key_show`` release, exactly like the other
    marking-menu windows. See :meth:`_register_native_menu_proxies` for how the resolution is
    wired through the shared switchboard's ``get_ui`` (which resolves names via ``loaded_ui``,
    never the handler directly).
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

        # Route the log panel's node actions through uitk's dependency-inverted
        # registry so uitk needn't import blendertk (mirror of MayaUiHandler).
        # Before this, uitk hard-imported mayatk, so Blender-session node links
        # were dead despite blendertk shipping its own dispatch_log_link.
        try:
            from uitk.bridge.slots import register_log_link_handler
            from blendertk.ui_utils._ui_utils import dispatch_log_link

            register_log_link_handler(dispatch_log_link)
        except Exception:  # never let a wiring hiccup block UI-handler startup
            pass

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
        :attr:`_NATIVE_MENU_ATTR`; :meth:`show` reads that and presents the wrapped, harvested
        menu (:meth:`_wrap_native_menu`), which populates the blank proxy in place on first use
        — the window's identity never changes. Cheap (no content), and skipped if a name is
        already loaded.

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
        """Swap a native-menu proxy for its wrapped, freshly-harvested menu window; else default show.

        The both-button menu's release dispatch calls ``ui_handler.show(proxy)`` (via
        ``MarkingMenu._show_window``). For a native-menu proxy the shown window is the wrapped
        Qt clone of Blender's menu — built on first use, content re-harvested every show so it
        tracks the live mode — presented through the normal :class:`UiHandler` show like any
        other marking-menu window (pin header, hides on ``key_show`` release; Maya parity).
        A name string is resolved first, so a direct ``show("mesh")`` works too.
        """
        if isinstance(ui, str):
            ui = self.get(ui, **kwargs)
        if ui is None:
            return None
        name = getattr(ui, self._NATIVE_MENU_ATTR, None)
        if name is not None:
            ui = self._wrap_native_menu(name)
            if ui is None:
                return None
        return super().show(ui, pos=pos, force=force, **kwargs)

    def _wrap_native_menu(self, name: str):
        """The Switchboard window hosting the Qt clone of native menu ``name``, content fresh.

        The Blender mirror of ``MayaUiHandler._load_maya_ui``, with one deliberate twist:
        the window IS the pre-registered proxy, populated in place on first use
        (``MainWindow.setCentralWidget`` is designed for set-or-change). The window's
        identity must never change — the marking menu's ``_show_window`` holds the object it
        resolved from ``loaded_ui`` and raises/activates *it* on a deferred timer after this
        returns; swapping in a different window would leave that timer targeting a dead blank
        proxy. When the menu can't be built right now (a mode-dependent draw outside its
        mode), falls back to the hand-authored ``<name>#submenu`` overlay — Maya's exact
        fallback — or ``None`` when no overlay is registered.
        """
        if not hasattr(self, "_blender_native_menus"):
            self._blender_native_menus = BlenderNativeMenus()

        widget = self._blender_native_menus.get_menu(name)
        if widget is None:
            overlay = f"{name}#submenu"
            try:
                if self.sb.is_registered_ui(overlay):
                    fallback = self.sb.get_ui(overlay)
                    if fallback is not None:
                        self.logger.debug(
                            f"[{name}] Native menu unavailable; "
                            f"falling back to '{overlay}' overlay."
                        )
                        return fallback
            except Exception as e:  # noqa: BLE001 - fallback must never raise
                self.logger.debug(f"[{name}] Overlay fallback failed: {e}")
            return None

        window = self._native_menu_proxies.get(name)
        if window is None:
            # Defensive: names() and the proxy registration cover the same set, so this
            # only runs if a caller invented a name — register it the same way.
            window = self.sb.add_ui(
                name=name,
                tags={"blender", "menu"},
                add_footer=False,
                restore_window_size=False,
            )
            setattr(window, self._NATIVE_MENU_ATTR, name)
            self._native_menu_proxies[name] = window

        if window.centralWidget() is not widget:
            # First use: give the blank proxy its content + Maya's header/styles pipeline.
            # A raise mid-setup must not leave a half-dressed window that every later show
            # returns — strip the content again and fail soft (next show retries).
            try:
                window.setCentralWidget(widget)
                window.set_flags(Window=True)
                window.header = self.sb.registered_widgets.Header()
                window.header.setTitle(name.upper())
                window.header.attach_to(window.centralWidget())
                window.style.set(window.header, "dark", "Header")
                window.edit_tags(add="blender_menu")
                self.apply_styles(window)
            except Exception as e:
                self.logger.error(
                    f"[{name}] Wrapper setup failed; stripping the half-built content: "
                    f"{type(e).__name__}: {e}"
                )
                try:
                    window.takeCentralWidget()
                except Exception:  # noqa: BLE001 - unwind is best-effort
                    pass
                self._blender_native_menus.menus.pop(name, None)
                widget.deleteLater()
                return None

        widget.fit_to_window()
        return window

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
