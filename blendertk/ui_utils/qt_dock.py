# !/usr/bin/python
# coding=utf-8
"""Dock any Qt widget into a native Blender area — a true child window, not an overlay.

Blender editors are *areas* painted by Blender inside a single GHOST OS window, so a Qt
widget can't join the area layout the way Maya's ``workspaceControl`` hosts one. What CAN
be done — verified mechanism, Blender 5.1 / 2026-07 — is to make the Qt widget's native
window a **real WS_CHILD of the GHOST window** (``QtGui.QWindow.fromWinId`` foreign-window
parenting) glued to a docked area's content region:

* the child moves / minimizes / stacks / clips with the Blender window **natively** — no
  geometry timer, no screen-coordinate math, no OS-owner juggling, no minimize polling;
* ``WS_CLIPCHILDREN`` on the GHOST window makes Blender's GL present clip around the
  child instead of stomping it (the classic embed-into-a-GL-app arrangement);
* geometry sync is **event-driven**: a draw handler on the area's space type re-positions
  the child whenever the region redraws — exactly when its rect can change (area-border
  drags, window resizes, DPI changes) and never otherwise. Child coordinates are
  parent-client-relative, so a window move needs no update at all;
* the child's **top edge is inset by Blender's area-edge grab tolerance** when the region
  is flush with the area top, so the whole band where the OS cursor reads "resize" stays
  Blender-owned — hover and click agree, and dragging the strip border never starts a text
  selection in the embedded widget instead (see :meth:`QtDock._expose_resize_edge`).

:class:`QtDock` packages that: it docks a placeholder area (:func:`btk.dock_editor` — the
same primitive the editors-menu toggles use), embeds the caller's widget over the area's
content region, and keeps them glued until :meth:`undock`. A cheap liveness watchdog
(no geometry work) detects the two things a draw handler can't report — the user closing
/ merging the area away, or the host window dying — and detaches gracefully via the
``on_detach`` callback.

Scope and caveats:

* **Windows-only** (the GHOST/HWND path); :meth:`supported` gates it. Off-Windows callers
  keep their own fallback (e.g. the Script Output console degrades to a bare native area).
* Requires a live ``QApplication`` and an external Qt event pump (the tentacle host's, or
  any ``bpy`` timer calling ``processEvents``) — QtDock deliberately doesn't own a pump.
* The caller owns the widget's lifecycle: :meth:`undock` un-parents and hides it (ready to
  re-dock later, history intact); QtDock never deletes it.
* Keyboard focus follows the mouse (``focus_on_hover``, default on): hovering the embedded
  widget hands it the keys, leaving hands them back to Blender — matching how Blender's own
  areas behave, so a docked Qt panel's shortcuts feel native. While the pointer is over the
  widget, Blender's shortcuts pause (it doesn't have focus) — the same trade Blender itself
  makes for its text fields. This one *is* a poll (20 Hz, only while docked): unlike
  geometry, focus has no event to hang off — Qt's ``Leave`` never fires for a child inside
  a GL-painted foreign parent (measured), and Blender's areas aren't windows that could
  report a crossing. Blender's own focus-follows-mouse is loop-driven for the same reason.
  See :meth:`QtDock._start_focus_follow`.

``import bpy`` / Qt are deferred into call bodies (no import side effects; headless-import
safe).
"""
import sys
from typing import Callable, Optional


class QtDock:
    """Host a Qt widget as the body of a true docked Blender area.

    One instance manages one (widget → area) pairing. ``dock()`` may be called again
    after an ``undock()`` or a detach — the area is re-created and the same widget
    re-embedded.

    Parameters:
        editor: Friendly editor name or raw ``ui_type`` for the placeholder area
            (default ``"Info Log"`` — semantically closest for console-like panels).
        area_header: Show the native area header strip. Default False: the docked Qt
            widget IS the panel; the placeholder editor's own menus would be noise.
        focus_on_hover: Hand OS keyboard focus to the embedded widget while the pointer
            is over it, and back to Blender on leave (default True). Without it the
            widget only gets keys after a click, and never gives them back — an
            embedded child is a real focus target, so Blender would stay mute until the
            user clicked back into a Blender region.
        on_detach: Called (no args) when the pairing dies out from under us — the user
            closed/merged the area, or the host window went away. The widget is already
            un-parented and hidden when this fires.
    """

    WATCH_INTERVAL = 0.5   # liveness-only tick (s); geometry is draw-handler-driven
    FOCUS_INTERVAL = 0.05  # focus-follows-mouse poll (s) — 20 Hz reads as instant
    DEFAULT_HEIGHT = 200   # first-dock strip height (px) when the caller passes none

    def __init__(
        self,
        editor: str = "Info Log",
        area_header: bool = False,
        focus_on_hover: bool = True,
        on_detach: Optional[Callable[[], None]] = None,
    ):
        self._editor = editor
        self._area_header = area_header
        self._focus_on_hover = focus_on_hover
        self._focus_timer = None   # the registered focus-follow tick (identity for unregister)
        self._on_detach = on_detach
        self._widget = None        # the hosted QWidget (caller-owned)
        self._edge_pad = 0         # native edge-grab tolerance (px); resolved at dock()
        self._foreign = None       # QWindow wrapper of the GHOST hwnd (kept alive while docked)
        self._window = None        # the bpy.types.Window we docked into
        self._area = None          # our docked bpy.types.Area
        self._hwnd = None          # the GHOST OS handle of self._window
        self._space_cls = None     # the space type class the draw handler is registered on
        self._draw_handle = None
        self._watch_timer = None   # the registered bpy timer callable (identity for unregister)
        self._last_rect = None     # last applied child rect (skip redundant SetWindowPos)

    # -- capability gate ----------------------------------------------------------
    @staticmethod
    def _qt_app():
        """The live QApplication, or None (no Qt binding / no host)."""
        try:
            from qtpy import QtWidgets
        except Exception:
            return None
        return QtWidgets.QApplication.instance()

    @classmethod
    def supported(cls) -> bool:
        """True when embedding can work here: Windows + a live QApplication."""
        return sys.platform == "win32" and cls._qt_app() is not None

    # -- state ----------------------------------------------------------------------
    @property
    def docked(self) -> bool:
        """True while the widget is embedded over a LIVE docked area — a user closing/
        merging the area away (dead wrapper) reads as not-docked even before the
        liveness watchdog has noticed."""
        return self._widget is not None and self.content_region() is not None

    @property
    def widget(self):
        """The hosted widget (or None) — for callers/tests."""
        return self._widget

    @property
    def area(self):
        """Our docked ``bpy.types.Area`` (or None)."""
        return self._area

    def content_region(self):
        """The WINDOW (content) region of our area, or None when the area is gone —
        the user closed/merged it (dead wrapper raises ``ReferenceError``) or nothing
        is docked. Doubles as the liveness probe."""
        try:
            for region in self._area.regions:
                if region.type == "WINDOW":
                    return region
        except (ReferenceError, AttributeError):
            return None
        return None

    # -- lifecycle --------------------------------------------------------------------
    def dock(self, widget, height: Optional[int] = None) -> bool:
        """Dock ``widget``: create/dock the placeholder area, embed the widget as a
        child of the host window, start the draw-handler glue. Idempotent while
        docked. Returns False when it can't (unsupported platform, no QApplication,
        no viewport to dock into, or the host window's handle can't be resolved).

        ``height`` is the initial strip height in px (an area the user resizes is
        theirs; callers can persist and re-pass it — Maya keeps a workspaceControl's
        size the same way).
        """
        import blendertk as btk
        from blendertk.ui_utils.blender_window import BlenderWindow

        if self.docked and widget is self._widget:
            return True
        # Reset any stale pairing first — a dead area the watchdog hasn't noticed yet,
        # or a previously docked DIFFERENT widget (docking on top of it would leak its
        # area and abandon its glue). No-op when idle.
        self.undock()
        if not self.supported():
            return False

        window = btk.main_window()
        if window is None:
            return False
        area = btk.dock_editor(
            self._editor, edge_size=height or self.DEFAULT_HEIGHT, window=window
        )
        if area is None:
            return False
        hwnd = BlenderWindow.window_hwnd(window)
        if hwnd is None:  # can't resolve the OS handle — don't leave a stray area behind
            btk.close_area(window, area)
            return False

        self._window, self._area, self._hwnd = window, area, hwnd
        self._edge_pad = self._native_edge_pad()
        self._apply_area_header(window, area)

        if not self._embed(widget, hwnd):
            self.undock()
            return False

        self._position(self.content_region())
        self._start_glue()
        # Nudge a first redraw through the handler so the glue is exercised immediately.
        try:
            for region in area.regions:
                region.tag_redraw()
        except Exception:
            pass
        return True

    def undock(self) -> None:
        """Release the widget (un-parent to a hidden top-level, ready to re-dock) and
        close OUR area only (a user's own same-typed area survives). Idempotent."""
        import blendertk as btk

        self._stop_glue()
        self._release_widget()
        if self._area is not None:
            try:
                btk.close_area(self._window, self._area)
            except Exception:
                pass
        self._window = self._area = self._hwnd = None
        self._last_rect = None

    def teardown(self) -> None:
        """Full uninstall for a host reload: :meth:`undock` + drop the widget reference.
        The widget itself is caller-owned and NOT deleted."""
        self.undock()
        self._widget = None

    def _apply_area_header(self, window, area) -> None:
        """Show/hide the native area header per ``area_header`` (cosmetic; best-effort).

        MUST run under a full context override: setting ``show_region_header`` fires
        ``ED_region_visibility_change_update`` → ``ED_area_init``, which dereferences the
        context window — from a bare ``bpy`` timer (no window in context) that is a hard
        crash (EXCEPTION_ACCESS_VIOLATION in ``WM_window_get_active_workspace``),
        reproduced by the console GUI harness.
        """
        import bpy

        try:
            space = area.spaces.active
            if space is None or space.show_region_header == self._area_header:
                return
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            with bpy.context.temp_override(
                window=window, screen=window.screen, area=area, region=region
            ):
                space.show_region_header = self._area_header
        except Exception:
            pass  # cosmetic only

    # -- embedding ----------------------------------------------------------------------
    def _embed(self, widget, hwnd) -> bool:
        """Make ``widget``'s native window a WS_CHILD of GHOST window ``hwnd``."""
        from qtpy import QtGui
        from blendertk.ui_utils.blender_window import BlenderWindow

        try:
            BlenderWindow.set_clip_children(hwnd)  # GL present must clip around children
            widget.winId()  # force native-window creation before touching windowHandle()
            foreign = QtGui.QWindow.fromWinId(int(hwnd))
            if foreign is None:
                return False
            widget.windowHandle().setParent(foreign)
            self._foreign = foreign  # keep the wrapper alive while parented to it
            self._widget = widget
            widget.show()
            if self._focus_on_hover:
                self._start_focus_follow()
            return True
        except Exception:
            return False

    def _release_widget(self) -> None:
        """Un-parent the widget back to a hidden top-level (survives for a later dock)."""
        widget = self._widget
        if widget is None:
            return
        # Stop the follow and hand focus back BEFORE un-parenting: undocking while the
        # pointer sits over the widget would otherwise leave win32 focus on a window we
        # just hid, and Blender would take no keys until the user clicked it.
        self._stop_focus_follow()
        self._focus_host()
        try:
            handle = widget.windowHandle()
            if handle is not None:
                handle.setParent(None)
            widget.hide()
        except Exception:
            pass
        # Drop the foreign wrapper AFTER un-parenting. destroy() would close the wrapped
        # native window on some Qt builds — just release the reference.
        self._foreign = None

    # -- focus follows mouse ----------------------------------------------------------
    def _start_focus_follow(self) -> None:
        """Keep OS keyboard focus tracking the pointer while docked.

        This is an **embedding** concern, which is why it lives here and not in the
        widget: the widget's own Qt-level focus (e.g. uitk ``ScriptOutput.focus_on_hover``)
        picks which Qt widget consumes a key event, but cannot redirect native key
        *messages* — with a foreign GHOST parent those follow win32 focus, i.e. go to
        Blender. The two layers compose: win32 focus routes keys to our child window, Qt
        focus routes them to the right widget inside it. Keeping the HWND half out of the
        widget lets any plain QWidget dock here and behave the same.

        A poll, not an event filter: Qt's ``Enter`` does arrive for the embedded child,
        but ``Leave`` never does (measured — a child inside a GL-painted foreign parent),
        so releasing focus on Leave would strand it and leave Blender's keyboard dead.
        Polling both directions is one self-correcting mechanism instead of two, and
        recovers from teleporting pointers (alt-tab, window switches) that no crossing
        event would report. Two syscalls at 20 Hz, only while docked.
        """
        import bpy

        self._stop_focus_follow()
        # Bind ONCE and register that exact object (bpy.app.timers.unregister matches by
        # identity), same as the liveness watchdog.
        self._focus_timer = self._focus_tick
        bpy.app.timers.register(self._focus_timer, persistent=True)

    def _stop_focus_follow(self) -> None:
        if self._focus_timer is None:
            return
        import bpy

        try:
            bpy.app.timers.unregister(self._focus_timer)
        except Exception:
            pass
        self._focus_timer = None

    def _child_hwnd(self) -> int:
        """The hosted widget's CURRENT native handle, or 0.

        ``internalWinId``, never ``winId``: ``winId`` *creates* a native window when there
        isn't one, which the 20 Hz poll below must never do (the same trap tentacle's key
        poller documents). 0 means "no native window right now" — nothing to focus.
        """
        if self._widget is None:
            return 0
        try:
            return int(self._widget.internalWinId() or 0)
        except Exception:
            return 0

    def _focus_tick(self):
        """Sync win32 focus with "is the pointer over our widget?"; None unregisters."""
        from blendertk.ui_utils.blender_window import BlenderWindow

        if self._widget is None or self._hwnd is None:
            return None
        child = self._child_hwnd()
        if not child:
            return self.FOCUS_INTERVAL
        if self._interaction_in_flight():
            return self.FOCUS_INTERVAL
        over = BlenderWindow.cursor_over(child)
        focus = BlenderWindow.keyboard_focus()
        if over and focus != child:
            BlenderWindow.set_keyboard_focus(child)
        elif not over and focus == child:
            BlenderWindow.set_keyboard_focus(self._hwnd)
        return self.FOCUS_INTERVAL

    @staticmethod
    def _interaction_in_flight() -> bool:
        """True while a Qt interaction owns the pointer — don't touch focus.

        Two states the pointer position lies about: an open popup (our own context menu
        is a separate top-level, so the cursor reads as "not over the widget" and we'd
        yank focus out from under the menu the user is reading), and a held mouse button
        (a drag-select that strays outside the widget still belongs to it).
        """
        try:
            from qtpy import QtCore, QtWidgets

            app = QtWidgets.QApplication.instance()
            if app is None:
                return False
            if QtWidgets.QApplication.activePopupWidget() is not None:
                return True
            return QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton
        except Exception:
            return False

    def _focus_host(self) -> None:
        """Route keystrokes back to Blender — but only when WE are holding them.

        Handing the host focus unconditionally would yank it away from an unrelated
        window of ours (a standalone panel the user is typing in) that merely happened
        to hold focus when this dock was torn down.
        """
        from blendertk.ui_utils.blender_window import BlenderWindow

        if self._hwnd is None:
            return
        child = self._child_hwnd()
        if child and BlenderWindow.keyboard_focus() == child:
            BlenderWindow.set_keyboard_focus(self._hwnd)

    # -- the glue -------------------------------------------------------------------------
    @staticmethod
    def _native_edge_pad() -> int:
        """Blender's grab tolerance around a shared area edge, in physical px.

        Mirrors ``BORDERPADDING`` (``screen_geometry.cc``: ``2 * UI_SCALE_FAC +
        U.pixelsize``) — the half-height of the band where Blender flips the cursor
        to a resize arrow and a press grabs the edge. Falls back to the 1x-scale
        value outside Blender (unit tests; the embed itself can't run there).
        """
        try:
            import bpy

            system = bpy.context.preferences.system
            return max(2, round(2.0 * float(system.ui_scale) + float(system.pixel_size)))
        except Exception:
            return 3

    def _expose_resize_edge(self, rect, region):
        """Shrink the child's TOP by the native grab tolerance when ``region`` is
        flush with its area's top edge — the bottom-strip dock's shared border with
        the viewport above.

        Glued flush, the child covers the lower half of Blender's grab band
        (edge ± :meth:`_native_edge_pad`): the OS cursor still reads "resize" over
        the remaining sliver of Blender-owned border, but a press that lands one
        pixel lower reaches the embedded widget — a text-selection drag instead of
        the resize the cursor promised. Leaving the band's lower half exposed keeps
        every resize-cursor pixel Blender-owned, so hover and click agree; the
        exposed strip reads as a slightly thicker splitter. The other edges stay
        flush: the strip's bottom is the window boundary, and its side edges are
        grabbable along their full height in the taller neighboring areas.
        """
        pad = self._edge_pad
        if not pad:
            return rect
        try:  # header shown = region tops out below the area top -> nothing to expose
            area = self._area
            flush = int(region.y) + int(region.height) >= int(area.y) + int(area.height) - 1
        except (ReferenceError, AttributeError):  # dead bpy wrapper mid-read
            return rect
        if not flush:
            return rect
        x, y, w, h = rect
        inset = max(0, min(pad, h - pad))  # never collapse a strip shorter than the band
        return (x, y + inset, w, h - inset)

    def _position(self, region) -> None:
        """Place the child over ``region`` (parent-client coords; physical px),
        minus the exposed native resize edge."""
        from blendertk.ui_utils.blender_window import BlenderWindow

        if region is None or self._widget is None or self._hwnd is None:
            return
        rect = BlenderWindow.region_client_rect(self._hwnd, region)
        if rect is not None:
            rect = self._expose_resize_edge(rect, region)
        if rect is None or rect == self._last_rect:
            return
        try:
            child = int(self._widget.winId())
        except Exception:
            return
        BlenderWindow.move_child(child, rect)
        self._last_rect = rect

    def _on_region_draw(self) -> None:
        """Draw-handler: fires on every redraw of any region of our space type; re-glues
        when it's OUR area drawing. Must never raise (a raising handler spams the
        console every redraw)."""
        import bpy

        try:
            area = bpy.context.area
            if area is None or self._area is None:
                return
            if area.as_pointer() != self._area.as_pointer():
                return
            self._position(self.content_region())
        except Exception:
            pass

    def _start_glue(self) -> None:
        import bpy

        self._stop_glue()
        space = self._area.spaces.active
        self._space_cls = type(space)
        self._draw_handle = self._space_cls.draw_handler_add(
            self._on_region_draw, (), "WINDOW", "POST_PIXEL"
        )
        # Bind ONCE and register that exact object — bpy.app.timers.unregister compares
        # by identity, so a fresh bound method would silently fail to unregister.
        self._watch_timer = self._watch
        # persistent: keep watching across File ▸ New/Open — that's precisely when the
        # screen layout (and our area with it) is replaced and a detach must be noticed.
        bpy.app.timers.register(self._watch_timer, persistent=True)

    def _stop_glue(self) -> None:
        # Early-out BEFORE importing bpy: undock()/teardown() on a never-glued instance
        # must stay callable outside Blender (headless-import-safe module contract).
        if self._draw_handle is None and self._watch_timer is None:
            return
        import bpy

        if self._draw_handle is not None:
            try:
                self._space_cls.draw_handler_remove(self._draw_handle, "WINDOW")
            except Exception:
                pass
            self._draw_handle = None
            self._space_cls = None
        if self._watch_timer is not None:
            try:
                bpy.app.timers.unregister(self._watch_timer)
            except Exception:
                pass
            self._watch_timer = None

    def _watch(self):
        """Liveness-only tick: geometry is the draw handler's job; this just notices the
        area or host window dying and detaches. Returning None unregisters the timer."""
        from blendertk.ui_utils.blender_window import BlenderWindow

        if self._widget is None:  # torn down mid-flight
            return None
        if self.content_region() is None or not BlenderWindow.is_window(self._hwnd):
            self._detach()
            return None
        return self.WATCH_INTERVAL

    def _detach(self) -> None:
        """The pairing died out from under us (area closed / window gone): release the
        widget, drop area state, notify. The widget survives for a later ``dock()``."""
        self._stop_glue()
        self._release_widget()
        self._window = self._area = self._hwnd = None
        self._last_rect = None
        if self._on_detach is not None:
            try:
                self._on_detach()
            except Exception:
                pass
