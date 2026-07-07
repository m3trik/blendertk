# !/usr/bin/python
# coding=utf-8
"""Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.

Maya is a Qt app, so mayatk docks a syntax-highlighted ``uitk.ScriptOutput`` straight into
Maya's layout via ``workspaceControl``. Blender is **not** a Qt app and its editors are areas
Blender paints itself — a Qt (HWND) widget can't be embedded in one. So this host realizes the
same *result* a different way (**Route 2+**, "shadow a native area"):

1. **Anchor** — open a real, dockable native Blender **Info Log window** (:func:`btk.open_editor`).
   Blender owns/manages it; the user can move, resize, or join it like any editor, and it is the
   graceful fallback if the skin ever detaches.
2. **Skin** — float a *frameless* :class:`uitk.ScriptOutput` (the SAME widget mayatk uses — shared
   highlighter, copy, context menu) **exactly over** that window's content region, made an OS-owned
   child of the anchor so it stacks/minimizes/closes with it. A ``bpy.app.timers`` tick re-shadows
   it as the anchor moves/resizes (:mod:`blendertk.ui_utils.blender_window` does the coordinate math).
3. **Content** — tee ``sys.stdout``/``sys.stderr`` and attach a root ``logging`` handler, so
   ``print`` output, tracebacks and logging land in the console (Blender's nearest analogue of
   "the script editor output"); the shared highlighter colors ``Error``/``Warning``/``Result`` lines.

Windows-only for the skin (the win32 owner/geometry path). Off-Windows — or when no QApplication /
Qt is present (blendertk used without the tentacle host) — it degrades to just opening the native
Info Log window (still useful, just unstyled). Mirrors mayatk's module API: :func:`show` /
:func:`toggle` / :func:`hide`.

``import bpy`` / Qt are deferred into call bodies (no import side effects; headless-import safe).
"""
import sys
from typing import Optional


class ScriptConsole:
    """Singleton host: a native Info-Log anchor window skinned with ``uitk.ScriptOutput``.

    Use the module-level :func:`show` / :func:`toggle` / :func:`hide`; the class is a container for
    the one live instance (one Qt host per Blender process, like the marking menu).
    """

    _instance: Optional["ScriptConsole"] = None
    _SYNC_INTERVAL = 0.05  # geometry re-shadow cadence (s); matches the Qt pump feel

    def __init__(self):
        self._skin = None                # uitk.ScriptOutput (the styled overlay)
        self._anchor_window = None       # bpy Window opened for the Info Log (the dock anchor)
        self._anchor_hwnd = None         # its GHOST OS window handle (owner of the skin)
        self._timer = None               # the bpy geometry-sync timer callback
        self._last_rect = None           # last shadowed rect (skip redundant setGeometry)
        self._orig_stdout = None
        self._orig_stderr = None
        self._log_handler = None
        self._in_sink = False            # reentrancy guard for the stream sink

    # -- capability gate ------------------------------------------------------
    @staticmethod
    def _qt_app():
        """The live QApplication, or None (blendertk used without the tentacle Qt host)."""
        try:
            from qtpy import QtWidgets
        except Exception:
            return None
        return QtWidgets.QApplication.instance()

    @classmethod
    def _skin_supported(cls) -> bool:
        """True when we can build+shadow the Qt skin (Windows + a live QApplication)."""
        return sys.platform == "win32" and cls._qt_app() is not None

    # -- anchor (native Info Log window) --------------------------------------
    def _open_anchor(self) -> bool:
        """Open the native Info Log window and capture it + its GHOST handle. False on failure."""
        import blendertk as btk
        from blendertk.ui_utils.blender_window import BlenderWindow

        before = BlenderWindow.process_ghost_hwnds()
        window = btk.open_editor("Info Log")
        if window is None:
            return False
        self._anchor_window = window
        self._anchor_hwnd = BlenderWindow.new_ghost_hwnd(before)
        return True

    def _anchor_content_region(self):
        """The WINDOW (content, below the header) region of the anchor's editor area, or None.

        Re-resolved every tick from the live bpy window; returns None (→ teardown) if the anchor
        window was closed (its wrapped struct raises ``ReferenceError`` on access)."""
        try:
            areas = self._anchor_window.screen.areas
            if not areas:
                return None
            area = areas[0]  # open_editor makes a single-area window
            for region in area.regions:
                if region.type == "WINDOW":
                    return region
        except (ReferenceError, AttributeError):
            return None
        return None

    # -- content redirect -----------------------------------------------------
    def _sink(self, text: str) -> None:
        """Append ``text`` to the skin on the GUI thread (reentrancy- and error-guarded)."""
        if self._in_sink or self._skin is None or not text:
            return
        self._in_sink = True
        try:
            from qtpy import QtCore

            app = self._qt_app()
            if app is not None and app.thread() == QtCore.QThread.currentThread():
                self._skin.append_text(text)
            else:  # marshal writes from worker threads onto the GUI thread
                QtCore.QTimer.singleShot(
                    0, lambda t=text: self._skin.append_text(t) if self._skin else None
                )
        except Exception:
            pass
        finally:
            self._in_sink = False

    def _install_redirect(self) -> None:
        """Tee stdout/stderr and attach a root logging handler → the skin."""
        import logging

        console = self

        class _StreamTee:
            def __init__(self, original):
                self._original = original

            def write(self, s):
                if self._original is not None:
                    try:
                        self._original.write(s)
                    except Exception:
                        pass
                console._sink(s)

            def flush(self):
                if self._original is not None:
                    try:
                        self._original.flush()
                    except Exception:
                        pass

            def __getattr__(self, name):  # delegate isatty/fileno/encoding/etc.
                return getattr(self._original, name)

        if self._orig_stdout is None:  # idempotent
            self._orig_stdout = sys.stdout
            self._orig_stderr = sys.stderr
            sys.stdout = _StreamTee(self._orig_stdout)
            sys.stderr = _StreamTee(self._orig_stderr)

        if self._log_handler is None:
            class _SinkHandler(logging.Handler):
                def emit(self_handler, record):
                    try:
                        console._sink(self_handler.format(record) + "\n")
                    except Exception:
                        pass

            handler = _SinkHandler()
            handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            handler.setLevel(logging.NOTSET)
            logging.getLogger().addHandler(handler)
            self._log_handler = handler

    def _remove_redirect(self) -> None:
        """Restore stdout/stderr and detach the logging handler."""
        import logging

        if self._orig_stdout is not None:
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr
            self._orig_stdout = None
            self._orig_stderr = None
        if self._log_handler is not None:
            try:
                logging.getLogger().removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None

    # -- skin (the Qt overlay) ------------------------------------------------
    def _build_skin(self):
        from qtpy import QtCore
        from uitk import ScriptOutput

        # app_wide_copy=False: Blender delivers the widget its own key events, so scope
        # Ctrl+C to the widget rather than hijacking it app-wide (Maya's default). max_blocks:
        # terminal-style scrollback cap so a long session can't grow the document unbounded.
        skin = ScriptOutput(clear_callback=self._clear, app_wide_copy=False, max_blocks=5000)
        # Frameless so setGeometry == the shadowed region (Qt sets the CLIENT rect; a frame
        # would spill outside it). Top-level; OS-owned to the anchor via set_owner (below).
        skin.setWindowFlags(skin.windowFlags() | QtCore.Qt.FramelessWindowHint)
        skin.setWindowTitle("Script Output")
        return skin

    def _clear(self) -> None:
        """Context-menu Clear: empty the skin (Blender's Info log has no scripted clear)."""
        if self._skin is not None:
            self._skin.clear()

    def _sync_geometry(self):
        """Timer tick: re-shadow the skin over the anchor's content region.

        Returns the next interval to keep ticking, or None to unregister (teardown). Hides the
        skin (without tearing down) while the anchor is minimized; tears down when the anchor
        window is gone."""
        from blendertk.ui_utils.blender_window import BlenderWindow

        if self._skin is None:
            return None  # closed
        if not BlenderWindow.is_window(self._anchor_hwnd):
            self._teardown()
            return None
        if BlenderWindow.is_iconic(self._anchor_hwnd):
            if self._skin.isVisible():
                self._skin.hide()
            return self._SYNC_INTERVAL
        region = self._anchor_content_region()
        if region is None:
            self._teardown()
            return None
        dpr = self._skin.devicePixelRatioF() if hasattr(self._skin, "devicePixelRatioF") else 1.0
        rect = BlenderWindow.region_screen_rect(self._anchor_hwnd, region, dpr=dpr)
        if rect is not None:
            if rect != self._last_rect:  # skip redundant setGeometry on an idle tick
                self._skin.setGeometry(*rect)
                self._last_rect = rect
            if not self._skin.isVisible():
                self._skin.show()
                # Qt drops the OS owner only when it (re)creates the native window (a show /
                # flag change); re-assert here rather than every tick — nothing else changes
                # flags mid-session, and a plain move/resize keeps the owner.
                BlenderWindow.set_owner(self._skin, self._anchor_hwnd)
        return self._SYNC_INTERVAL

    def _start_timer(self) -> None:
        import bpy

        self._stop_timer()
        # Bind the method ONCE and register that exact object: bpy.app.timers.unregister
        # compares by identity, so a fresh `self._sync_geometry` bound method (a new object
        # per attribute access) would not match and _stop_timer would silently no-op.
        self._timer = self._sync_geometry
        # persistent: survive File ▸ New/Open like the marking-menu pump (else the console
        # silently stops tracking for the rest of the session).
        bpy.app.timers.register(self._timer, persistent=True)

    def _stop_timer(self) -> None:
        if self._timer is None:
            return
        import bpy

        try:
            bpy.app.timers.unregister(self._timer)
        except Exception:
            pass
        self._timer = None

    # -- lifecycle ------------------------------------------------------------
    def open(self) -> "ScriptConsole":
        """Open the console: native anchor + (where supported) the Qt skin + redirect."""
        if not self._open_anchor():
            return self
        if not self._skin_supported():
            return self  # native Info Log only (off-Windows / no Qt host)
        self._skin = self._build_skin()
        self._install_redirect()
        self._start_timer()
        # Seed with a hint so an empty console isn't mistaken for "broken".
        self._sink("# Script Output — mirroring stdout / stderr / logging\n")
        return self

    def close(self) -> None:
        """Fully tear down: skin, redirect, timer, and the anchor window."""
        self._teardown(close_anchor=True)

    def is_open(self) -> bool:
        return self._skin is not None or self._anchor_window is not None

    def _teardown(self, close_anchor: bool = True) -> None:
        self._stop_timer()
        self._remove_redirect()
        if self._skin is not None:
            try:
                self._skin.close()
                self._skin.deleteLater()
            except Exception:
                pass
            self._skin = None
        if close_anchor and self._anchor_hwnd:
            self._close_anchor_window()
        self._anchor_window = None
        self._anchor_hwnd = None

    def _close_anchor_window(self) -> None:
        """Close the anchor Blender window via its own window-close operator."""
        try:
            import bpy

            with bpy.context.temp_override(window=self._anchor_window):
                bpy.ops.wm.window_close()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Module API — mirrors mayatk.env_utils.script_output (show / toggle / hide).
# -----------------------------------------------------------------------------
def show(*args, **kwargs) -> ScriptConsole:
    """Open the Script Output console (idempotent — re-opens a torn-down instance)."""
    inst = ScriptConsole._instance
    if inst is None or not inst.is_open():
        inst = ScriptConsole()
        ScriptConsole._instance = inst
        inst.open()
    return inst


def hide(*args, **kwargs) -> None:
    """Close the Script Output console if open."""
    inst = ScriptConsole._instance
    if inst is not None and inst.is_open():
        inst.close()


def toggle(*args, **kwargs):
    """Toggle the Script Output console open/closed."""
    inst = ScriptConsole._instance
    if inst is not None and inst.is_open():
        inst.close()
        return None
    return show(*args, **kwargs)


if __name__ == "__main__":
    show()
