# !/usr/bin/python
# coding=utf-8
"""Native-window (win32/GHOST) helpers for hosting Qt widgets around a Blender window.

Blender draws its editors itself inside a single GHOST OS window, so Qt content is hosted
*next to* Blender at the OS level, in one of two modes — both backed by the helpers here:

* **Embedded child** (:class:`blendertk.ui_utils.qt_dock.QtDock`): the Qt widget's native
  window becomes a real ``WS_CHILD`` of the GHOST window, glued over a docked area's
  content region. :meth:`set_clip_children` keeps Blender's GL present from stomping the
  child; :meth:`region_client_rect` + :meth:`move_child` do the placement.
* **Owned top-level** (standalone panels): the Qt window stays top-level but is made an
  OS-*owned* window of the GHOST window via :meth:`set_owner`, so it stacks above and
  minimizes with Blender.

Measured reality (Blender 5.1, verified 2026-07) the math relies on:

* bpy ``Area``/``Region`` rects are in the **same pixel units** as the hosting GHOST
  window's *client* area — bpy ``window.width/height`` == ``GetClientRect`` w/h.
* bpy uses a **bottom-left origin, y-up**; win32 client coords are top-left, y-down. So a
  region's client-top = client_height − (region.y + region.height). Child windows are
  positioned in parent-client coordinates (physical px) — no screen mapping, no DPI math.

Everything is Windows-only and degrades to a safe no-op / ``None`` elsewhere (Blender on
Linux/mac would need its own native path). ``import bpy`` is **not** needed here — callers
pass the bpy ``region`` object; only its ``.x/.y/.width/.height`` ints are read.
"""
import os
import sys
import ctypes


class BlenderWindow:
    """Static win32 helpers for GHOST-window enumeration, geometry, embedding, ownership.

    Stateless — every method reads live OS state. Mirrors the subset of tentacle's
    ``tcl_blender._NativeWindow`` that a *Blender-layer* host needs (blendertk sits
    below tentacle and can't import it). The owner-setting logic is intentionally the
    same GWLP_HWNDPARENT idiom; see the note in :meth:`set_owner`.
    """

    _GWLP_HWNDPARENT = -8  # window long index for the OS *owner* handle
    _GWL_STYLE = -16
    _WS_CLIPCHILDREN = 0x02000000
    _SWP_NOACTIVATE = 0x0010
    _SWP_NOZORDER = 0x0004
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

        Self-contained (does not depend on tentacle's harness ``_input``) so blendertk
        hosts work when launched standalone, not only under tcl_blender.
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
    def window_hwnd(cls, bpy_window):
        """The GHOST hwnd of a SPECIFIC already-open ``bpy.types.Window``, or None.

        bpy exposes no native handle for a window, so it is identified among this
        process's GHOST windows by live client size: the one whose size equals
        ``bpy_window.width/height``. Returns None off-Windows, if nothing matches, or if
        the match is ambiguous (more than one same-sized window — rare: two windows
        opened at an identical size). Verified NOT ambiguous in the expected common case:
        a ``wm.window_new()``-spawned editor window does NOT clone the main window's
        exact client size on this Blender build (differing insets/borders), so this keeps
        resolving the main window correctly even with another editor window open.
        """
        if not cls._win() or bpy_window is None:
            return None
        target = (int(bpy_window.width), int(bpy_window.height))
        matches = [h for h in cls.process_ghost_hwnds() if cls.client_size(h) == target]
        return matches[0] if len(matches) == 1 else None

    # -- state ----------------------------------------------------------------
    @classmethod
    def is_window(cls, hwnd) -> bool:
        if not cls._win() or not hwnd:
            return False
        return bool(cls._user32().IsWindow(ctypes.c_void_p(int(hwnd))))

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

    # -- embedding (child-window) primitives -----------------------------------
    @classmethod
    def region_client_rect(cls, hwnd, region):
        """Map a bpy ``region`` inside GHOST window ``hwnd`` to a parent-CLIENT rect.

        Returns ``(x, y, w, h)`` in the window's client coordinate space (top-left
        origin, physical px) — exactly what :meth:`move_child` takes for a child window
        of ``hwnd`` — or ``None`` if the geometry can't be read. Only the y-flip is
        needed: bpy region rects are already client-relative physical px (module
        docstring); child windows never need screen coords or DPI scaling.
        """
        size = cls.client_size(hwnd)
        if size is None or region is None:
            return None
        try:
            return (
                int(region.x),
                size[1] - (int(region.y) + int(region.height)),
                int(region.width),
                int(region.height),
            )
        except (ReferenceError, AttributeError):  # dead bpy wrapper mid-read
            return None

    @classmethod
    def set_clip_children(cls, hwnd) -> bool:
        """Set ``WS_CLIPCHILDREN`` on ``hwnd`` (idempotent); True when the style is set.

        Required on a GHOST window before embedding a child: it excludes child rects
        from the parent's GL present, so Blender's redraws clip around the embedded Qt
        surface instead of overdrawing it.
        """
        if not cls._win() or not hwnd:
            return False
        try:
            user32 = cls._user32()
            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            style = int(user32.GetWindowLongPtrW(ctypes.c_void_p(int(hwnd)), cls._GWL_STYLE) or 0)
            if not style & cls._WS_CLIPCHILDREN:
                user32.SetWindowLongPtrW(
                    ctypes.c_void_p(int(hwnd)),
                    cls._GWL_STYLE,
                    ctypes.c_void_p(style | cls._WS_CLIPCHILDREN),
                )
            style = int(user32.GetWindowLongPtrW(ctypes.c_void_p(int(hwnd)), cls._GWL_STYLE) or 0)
            return bool(style & cls._WS_CLIPCHILDREN)
        except Exception:
            return False

    @classmethod
    def move_child(cls, hwnd, rect) -> bool:
        """Place child window ``hwnd`` at ``rect`` = (x, y, w, h) in its parent's client
        coordinates (physical px, from :meth:`region_client_rect`); True on success.
        No activation / z-order change — safe to call from a draw handler."""
        if not cls._win() or not hwnd or rect is None:
            return False
        try:
            return bool(
                cls._user32().SetWindowPos(
                    ctypes.c_void_p(int(hwnd)),
                    None,
                    int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]),
                    cls._SWP_NOACTIVATE | cls._SWP_NOZORDER,
                )
            )
        except Exception:
            return False

    # -- keyboard focus --------------------------------------------------------
    @classmethod
    def keyboard_focus(cls):
        """``GetFocus()`` — the hwnd receiving this thread's keystrokes, or 0/None."""
        if not cls._win():
            return None
        try:
            user32 = cls._user32()
            user32.GetFocus.restype = ctypes.c_void_p  # don't truncate the handle
            return int(user32.GetFocus() or 0)
        except Exception:
            return None

    @classmethod
    def cursor_over(cls, hwnd) -> bool:
        """True when the pointer is over ``hwnd`` (or one of its child windows).

        ``WindowFromPoint`` on the live cursor — a **poll**, deliberately: there is no
        reliable event for "the pointer left my child window into a foreign sibling
        area". Qt's ``Leave`` does not fire for a child embedded in a GL-painted foreign
        parent (measured: Enter arrives, Leave never does), and Blender's areas aren't
        windows that could notify us. Blender's own focus-follows-mouse is loop-driven
        for the same reason.
        """
        if not cls._win() or not hwnd:
            return False
        try:
            from ctypes import wintypes

            user32 = cls._user32()
            pt = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return False
            user32.WindowFromPoint.restype = ctypes.c_void_p
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            at = int(user32.WindowFromPoint(pt) or 0)
            if at == int(hwnd):
                return True
            # A composite widget's parts (QTextEdit's viewport/scrollbars) are their own
            # child windows, so an exact match alone would read as "left the widget".
            return bool(
                user32.IsChild(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(at))
            )
        except Exception:
            return False

    @classmethod
    def set_keyboard_focus(cls, hwnd) -> bool:
        """``SetFocus(hwnd)`` — route this thread's keystrokes to ``hwnd``; True if it took.

        **Focus is not foreground.** Foreground (``SetForegroundWindow``, tentacle's
        ``_NativeWindow.restore_foreground``) picks which *application* is active; focus
        is per-thread and picks which of that thread's windows receives ``WM_KEYDOWN``.
        An embedded Qt child and its GHOST parent live in Blender's one UI thread, so
        this hands keys between them with **no** activation, z-order or foreground
        change — which is exactly what a hover hand-off must not disturb.

        Being thread-scoped also makes it safe by construction: when another app holds
        the foreground, ``SetFocus`` no-ops (Windows refuses focus changes from a thread
        without the active window), so this can never steal keys from another app.
        """
        if not cls._win() or not hwnd:
            return False
        try:
            user32 = cls._user32()
            handle = ctypes.c_void_p(int(hwnd))
            if not user32.IsWindow(handle):
                return False
            user32.SetFocus(handle)
            user32.GetFocus.restype = ctypes.c_void_p  # don't truncate the handle
            return int(user32.GetFocus() or 0) == int(hwnd)
        except Exception:
            return False

    # -- ownership ------------------------------------------------------------
    @classmethod
    def set_owner(cls, widget, owner_hwnd):
        """Make Qt ``widget`` an *owned* window of ``owner_hwnd`` (``GWLP_HWNDPARENT``).

        An owned top-level window always stacks above its owner and minimizes/closes
        with it — the hosting mode for **standalone** panels that must follow a Blender
        window without being embedded in it. Qt's ``setTransientParent`` does **not**
        set this for a *foreign* (non-Qt) owner — verified in tentacle's port — so it
        must be set directly. Idempotent. Returns the resulting owner handle, or None
        off-Windows / on failure.
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
