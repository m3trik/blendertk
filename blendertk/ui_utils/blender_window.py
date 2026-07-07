# !/usr/bin/python
# coding=utf-8
"""Native-window geometry helpers for hosting a Qt overlay over a Blender window.

Blender editors are *areas* Blender paints inside a single GHOST OS window — they
are not OS windows, so a Qt (HWND) widget cannot be embedded in one. The next best
thing, used by :mod:`blendertk.env_utils.script_output`, is to float a frameless Qt
widget **exactly over** a Blender area's content region so it reads as that area's
body — a "skin". This module does the coordinate math and OS-owner wiring that makes
that shadowing possible.

Measured reality (Blender 5.1, verified 2026-07-04) the math relies on:

* bpy ``Area``/``Region`` rects are in the **same pixel units** as the hosting GHOST
  window's *client* area — bpy ``window.width/height`` == ``GetClientRect`` w/h.
* bpy uses a **bottom-left origin, y-up**; Windows screen coords are top-left, y-down.
  So a region's screen-top = client_origin_y + (client_height − (region.y + region.h)).
* Qt ``setGeometry`` sets the **client** rect (frame sits outside) — the skin must be
  frameless so its geometry equals the shadowed region.

Everything is Windows-only and degrades to a safe no-op / ``None`` elsewhere (Blender
on Linux/mac would need its own native path; the console falls back to the bare native
Info editor there). ``import bpy`` is **not** needed here — callers pass the bpy
``region`` object; only its ``.x/.y/.width/.height`` ints are read.
"""
import os
import sys
import ctypes


class BlenderWindow:
    """Static win32 helpers for GHOST-window enumeration, geometry and ownership.

    Stateless — every method reads live OS state. Mirrors the subset of tentacle's
    ``tcl_blender._NativeWindow`` that a *Blender-layer* host needs (blendertk sits
    below tentacle and can't import it). The owner-setting logic is intentionally the
    same GWLP_HWNDPARENT idiom; see the note in :meth:`set_owner`.
    """

    _GWLP_HWNDPARENT = -8  # window long index for the OS *owner* handle
    _GHOST_CLASS = "GHOST_WindowClass"

    # -- guards ---------------------------------------------------------------
    @staticmethod
    def _win() -> bool:
        return sys.platform == "win32"

    @classmethod
    def _user32(cls):
        return ctypes.windll.user32

    # -- enumeration ----------------------------------------------------------
    @classmethod
    def process_ghost_hwnds(cls):
        """List of visible GHOST-window HWNDs owned by THIS process (``[]`` off-Windows).

        Self-contained (does not depend on tentacle's harness ``_input``) so the console
        host works when launched standalone, not only under tcl_blender.
        """
        if not cls._win():
            return []
        from ctypes import wintypes

        user32 = cls._user32()
        pid = os.getpid()
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, buf, 64)
                if buf.value == cls._GHOST_CLASS:
                    found.append(int(hwnd))
            return True

        user32.EnumWindows(_enum, 0)
        return found

    @classmethod
    def new_ghost_hwnd(cls, before):
        """The single GHOST HWND present now but not in ``before`` (a set/list), else None.

        Used to capture the OS handle of the Blender window ``open_editor`` just spawned:
        snapshot :meth:`process_ghost_hwnds` before the call, diff after.
        """
        before = set(before or ())
        fresh = [h for h in cls.process_ghost_hwnds() if h not in before]
        return fresh[-1] if fresh else None  # the just-opened window (last if several)

    # -- state ----------------------------------------------------------------
    @classmethod
    def is_window(cls, hwnd) -> bool:
        if not cls._win() or not hwnd:
            return False
        return bool(cls._user32().IsWindow(ctypes.c_void_p(int(hwnd))))

    @classmethod
    def is_iconic(cls, hwnd) -> bool:
        """True if ``hwnd`` is minimized (skin should hide to follow it)."""
        if not cls._win() or not hwnd:
            return False
        return bool(cls._user32().IsIconic(ctypes.c_void_p(int(hwnd))))

    @classmethod
    def client_origin(cls, hwnd):
        """Screen (x, y) of the window's client-area top-left, or None."""
        if not cls._win() or not hwnd:
            return None
        from ctypes import wintypes

        pt = wintypes.POINT(0, 0)
        if not cls._user32().ClientToScreen(ctypes.c_void_p(int(hwnd)), ctypes.byref(pt)):
            return None
        return (pt.x, pt.y)

    @classmethod
    def client_size(cls, hwnd):
        """(width, height) of the window's client area in physical pixels, or None."""
        if not cls._win() or not hwnd:
            return None
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not cls._user32().GetClientRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(rect)):
            return None
        return (rect.right, rect.bottom)

    # -- the transform --------------------------------------------------------
    @classmethod
    def region_screen_rect(cls, hwnd, region, dpr: float = 1.0):
        """Map a bpy ``region`` inside GHOST window ``hwnd`` to a Qt-logical screen rect.

        Returns ``(x, y, w, h)`` suitable for ``QWidget.setGeometry`` (frameless), or
        ``None`` if the window/geometry can't be read. ``region`` needs ``.x/.y`` (bpy
        window-space, bottom-left origin) and ``.width/.height``. ``dpr`` is the target
        screen's device-pixel-ratio: win32 returns physical pixels, Qt positions in
        logical pixels, so physical is divided by ``dpr`` (no-op at dpr=1, the verified
        path; the divide keeps the hi-dpi case structurally correct though unverified).
        """
        origin = cls.client_origin(hwnd)
        size = cls.client_size(hwnd)
        if origin is None or size is None or region is None:
            return None
        client_h = size[1]
        left_px = origin[0] + int(region.x)
        # bottom-left (bpy) -> top-left (screen): flip within the client height.
        top_px = origin[1] + (client_h - (int(region.y) + int(region.height)))
        w_px = int(region.width)
        h_px = int(region.height)
        if dpr and dpr != 1.0:
            return (round(left_px / dpr), round(top_px / dpr),
                    round(w_px / dpr), round(h_px / dpr))
        return (left_px, top_px, w_px, h_px)

    # -- ownership ------------------------------------------------------------
    @classmethod
    def set_owner(cls, widget, owner_hwnd):
        """Make Qt ``widget`` an *owned* window of ``owner_hwnd`` (``GWLP_HWNDPARENT``).

        An owned top-level window always stacks above its owner and minimizes/closes
        with it — so the skin follows the Blender anchor window natively instead of
        drifting behind it on focus loss. Qt's ``setTransientParent`` does **not** set
        this for a *foreign* (non-Qt) owner — verified in tentacle's port — so it must be
        set directly. Idempotent; safe to re-assert every geometry tick. Returns the
        resulting owner handle, or None off-Windows / on failure.
        """
        if not cls._win() or widget is None or not owner_hwnd:
            return None
        try:
            user32 = cls._user32()
            if not user32.IsWindow(ctypes.c_void_p(int(owner_hwnd))):
                return None
            # Force native restypes so the 64-bit handle isn't truncated to c_int.
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            hwnd = ctypes.c_void_p(int(widget.winId()))  # forces native-window creation
            user32.SetWindowLongPtrW(hwnd, cls._GWLP_HWNDPARENT, ctypes.c_void_p(int(owner_hwnd)))
            return user32.GetWindowLongPtrW(hwnd, cls._GWLP_HWNDPARENT)
        except Exception:
            return None
