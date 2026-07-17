# !/usr/bin/python
# coding=utf-8
"""Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.

Maya is a Qt app, so mayatk docks a syntax-highlighted ``uitk.ScriptOutput`` straight into
Maya's layout via ``workspaceControl``. Blender is **not** a Qt app, so this host gets the
same result from two focused collaborators:

* :class:`_OutputCapture` — pure Python (no Qt, no bpy). Tees ``sys.stdout``/``sys.stderr``
  and attaches a root ``logging`` handler, accumulating everything in a bounded in-process
  transcript buffer and notifying a live listener. Because it is UI-free it can install at
  **startup** (:func:`begin_capture`, before the Qt host or any window exists), so the
  console's transcript covers the whole session — tentacle's greeting banner included —
  exactly like Maya's Script Editor, which has been recording since Maya launched.
* :class:`~blendertk.ui_utils.qt_dock.QtDock` — the native dock container. The SAME
  ``uitk.ScriptOutput`` widget mayatk uses (shared highlighter, font, context menu) is
  embedded as a **true child window** of Blender's main window, glued to a docked area's
  content region — a real dock that tiles with the viewport and resizes with the area
  border. No overlay, no geometry timer (see the ``qt_dock`` module docstring for the
  mechanism).

:class:`ScriptConsole` is the thin orchestrator over the two, plus **cross-session
persistence**: ``show()``/``hide()`` record the visible state (and the user's strip
height) in a small JSON flag under Blender's user config dir, and :func:`restore` —
called by the tentacle host at launch — re-opens the console if it was open when the
previous session ended. This is the Blender analogue of Maya's ``workspaceControl``
``uiScript`` restore (Maya re-runs the uiScript from its workspace prefs; Blender has no
such hook, so the host calls :func:`restore` explicitly).

Embedding is Windows-only (:meth:`QtDock.supported`). Off-Windows — or when no
QApplication / Qt binding is present (blendertk used without the tentacle host) — it
degrades to a docked native Info Log area (still a toggle-able dock, just not the
transcript) — **observably**: the reason is logged. Module API mirrors mayatk's:
:func:`show` / :func:`toggle` / :func:`hide`, plus :func:`begin_capture` / :func:`restore`.

``import bpy`` / Qt are deferred into call bodies (no import side effects; headless-import safe).
"""
import os
import sys
import json
import threading
from typing import Optional

import pythontk as ptk
from blendertk.ui_utils.qt_dock import QtDock


class _OutputCapture:
    """Process-wide stdout/stderr/logging tee → bounded transcript buffer + live listener.

    Pure Python on purpose: no Qt, no bpy — so it can install before any UI exists and keep
    running after the UI hides. The buffer holds the session transcript the console seeds
    from when it is (re)built; the listener is the console's live append. Writes still reach
    the original streams (tee, not redirect), so Blender's system console keeps working.

    Chunks are buffered **with the log level they arrived on** (``None`` for plain
    stdout/stderr writes), so the console can color a record by what it *is* rather than
    by re-sniffing words out of its text. This is the one place in the stack that still
    knows — past the tee, a logging record is just characters.
    """

    MAX_CHARS = 200_000  # transcript cap (chars) — the widget itself caps at max_blocks anyway

    def __init__(self):
        self._chunks = []            # the transcript, as (text, level) pairs
        self._size = 0               # sum of chunk text lengths (cheap cap enforcement)
        self._lock = threading.Lock()  # prints can come from worker threads
        self._listener = None        # callable(str, level) — the console's live sink
        self._notifying = False      # same-thread reentrancy guard for the listener call
        self._tee_out = None
        self._tee_err = None
        self._orig_stdout = None
        self._orig_stderr = None
        self._log_handler = None

    @property
    def installed(self) -> bool:
        return self._orig_stdout is not None

    def install(self) -> None:
        """Tee stdout/stderr + attach a root logging handler (idempotent, once per process)."""
        import logging

        if self.installed:
            return
        capture = self

        class _StreamTee:
            def __init__(self, original):
                self._original = original

            def write(self, s):
                if self._original is not None:
                    try:
                        self._original.write(s)
                    except Exception:
                        pass
                capture._write(s)

            def flush(self):
                if self._original is not None:
                    try:
                        self._original.flush()
                    except Exception:
                        pass

            def __getattr__(self, name):  # delegate isatty/fileno/encoding/etc.
                return getattr(self._original, name)

        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._tee_out = _StreamTee(self._orig_stdout)
        self._tee_err = _StreamTee(self._orig_stderr)
        sys.stdout = self._tee_out
        sys.stderr = self._tee_err

        class _SinkHandler(logging.Handler):
            def emit(self_handler, record):
                try:
                    # levelno rides along: the console colors by level, which beats
                    # matching words in the message (a DEBUG line about "error" is not
                    # an error).
                    capture._write(
                        self_handler.format(record) + "\n", level=record.levelno
                    )
                except Exception:
                    pass

        handler = _SinkHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        handler.setLevel(logging.NOTSET)
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

        # The capture persists for the whole session (that's the point — it's what makes the
        # console show history from before it was opened). Left installed, a teed write can
        # fire during interpreter/Qt teardown and reach the (by then partially destroyed)
        # console widget through the listener — reproduced as a quit-time crash (exit 11) in
        # the GUI harness. atexit fires during normal Python finalization (Blender embeds
        # Python and calls Py_Finalize on quit), detaching the listener and restoring plain
        # stdout/stderr before that teardown reaches anything Qt-related.
        import atexit

        atexit.register(self.uninstall)

        self._write("# Script Output — mirroring stdout / stderr / logging\n")

    def uninstall(self) -> None:
        """Detach the listener, restore the streams, remove the logging handler."""
        import logging

        self._listener = None  # first: even a stacked foreign tee can no longer reach the widget
        if not self.installed:
            return
        # Only unwind streams that are still OURS — if another tool redirected on top of the
        # tee, restoring would silently break its redirect chain.
        if sys.stdout is self._tee_out:
            sys.stdout = self._orig_stdout
        if sys.stderr is self._tee_err:
            sys.stderr = self._orig_stderr
        self._orig_stdout = None
        self._orig_stderr = None
        self._tee_out = None
        self._tee_err = None
        if self._log_handler is not None:
            try:
                logging.getLogger().removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None

    def set_listener(self, callback) -> None:
        """Set (or clear, with None) the live-append callback — ``callable(text, level)``,
        the console's sink."""
        self._listener = callback

    def chunks(self) -> list:
        """The buffered transcript as ``(text, level)`` pairs — what the console seeds
        from on build, level-tagged so a rebuilt console colors history exactly as it
        colored it live. The sole reader of the buffer: joining the text out of it is a
        caller's business, and a second plain-text accessor would just be a second way to
        say the same thing."""
        with self._lock:
            return list(self._chunks)

    def clear(self) -> None:
        with self._lock:
            self._chunks = []
            self._size = 0

    def _write(self, text: str, level: Optional[int] = None) -> None:
        """Buffer ``text`` and notify the listener (called from the tee / logging handler).

        ANSI escapes are scrubbed here, at ingest: CPython emits colored tracebacks when
        ``stderr.isatty()``, and the tee below delegates ``isatty`` to Blender's system
        console — which IS a TTY — so escape sequences arrive unbidden. Stripping on the
        way in keeps the buffer plain text, so the char cap counts characters a reader
        will actually see and every consumer of the transcript gets clean text.
        """
        text = ptk.strip_ansi(text)
        if not text:
            return
        with self._lock:
            self._chunks.append((text, level))
            self._size += len(text)
            while self._size > self.MAX_CHARS and len(self._chunks) > 1:
                self._size -= len(self._chunks.pop(0)[0])
        # Notify OUTSIDE the lock (the listener may print, re-entering _write — the buffer
        # append above stays safe; the nested notify is skipped by the guard, same-thread).
        listener = self._listener
        if listener is None or self._notifying:
            return
        self._notifying = True
        try:
            listener(text, level)
        except Exception:
            pass
        finally:
            self._notifying = False


class ScriptConsole:
    """Singleton orchestrator: capture + native-docked ``uitk.ScriptOutput``, with the
    visible state (and strip height) persisted across Blender sessions.

    Use the module-level :func:`show` / :func:`toggle` / :func:`hide` / :func:`restore` /
    :func:`begin_capture`; the class is a container for the one live instance (one Qt host
    per Blender process, like the marking menu).
    """

    _instance: Optional["ScriptConsole"] = None
    _STATE_FILE = "blendertk_script_output.json"
    DEFAULT_HEIGHT = QtDock.DEFAULT_HEIGHT  # first-ever dock height (px); user resize persists
    # Tests point this at a sandbox dir so check runs never touch the user's real config
    # (the ShotStore._prefs_dir_override pattern).
    _state_dir_override: Optional[str] = None

    def __init__(self):
        self._capture = _OutputCapture()
        self._dock = QtDock(editor="Info Log", on_detach=self._on_detach)
        self._widget = None         # uitk.ScriptOutput — built once, survives hide cycles
        self._fallback_area = None  # bare native Info Log area (no-Qt / off-Windows degrade)
        self._fallback_window = None
        self._visible = False       # docked + shown (vs hidden-but-still-capturing)

    @classmethod
    def instance(cls) -> "ScriptConsole":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- persisted state --------------------------------------------------------
    @classmethod
    def _state_path(cls) -> Optional[str]:
        base = cls._state_dir_override
        if base is None:
            try:
                import bpy

                base = bpy.utils.user_resource("CONFIG")
            except Exception:
                return None
        return os.path.join(base, cls._STATE_FILE) if base else None

    @classmethod
    def _load_state(cls) -> dict:
        path = cls._state_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except Exception:  # missing/corrupt/unreadable → default hidden
            return {}

    @classmethod
    def _save_state(cls, visible: bool, height: Optional[int] = None) -> None:
        path = cls._state_path()
        if not path:
            return
        state = cls._load_state()
        state["visible"] = visible
        if height:
            state["height"] = int(height)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass  # persistence is best-effort; never break the console over it

    # -- the widget ----------------------------------------------------------------
    @property
    def widget(self):
        """The live ``uitk.ScriptOutput`` (or None) — for tests/diagnostics."""
        return self._widget

    def _ensure_widget(self):
        """Build the console widget once: seed it with the capture's transcript, then
        subscribe for live appends. Survives hide cycles — capture keeps appending while
        hidden, so a re-show picks up with full history. Raises on Qt failure (the
        caller degrades observably)."""
        if self._widget is not None:
            return self._widget
        from uitk import ScriptOutput

        # app_wide_copy=False: Blender delivers the widget its own key events, so scope
        # Ctrl+C to the widget rather than hijacking it app-wide (Maya's default).
        # max_blocks: terminal-style scrollback cap so a long session can't grow the
        # document unbounded.
        widget = ScriptOutput(clear_callback=self._clear, app_wide_copy=False, max_blocks=5000)
        widget.setWindowTitle("Script Output")
        self._widget = widget
        # Seed chunk-by-chunk rather than as one joined string: the level tags are
        # per-chunk, and a single append would flatten them all to None — history would
        # come back colored differently than it was when it scrolled past live.
        for text, level in self._capture.chunks():
            widget.append_text(text, level)
        self._capture.set_listener(self._append)
        return widget

    def _clear(self) -> None:
        """Context-menu Clear: empty the widget AND the transcript buffer (else a rebuild
        would resurrect the cleared text). Blender's Info log has no scripted clear."""
        self._capture.clear()
        if self._widget is not None:
            self._widget.clear()

    def _append(self, text: str, level: Optional[int] = None) -> None:
        """Capture listener: append to the widget on the GUI thread (error-guarded; the
        capture's own guard already prevents same-thread reentrancy)."""
        if self._widget is None:
            return
        try:
            from qtpy import QtCore, QtWidgets

            app = QtWidgets.QApplication.instance()
            if app is not None and app.thread() == QtCore.QThread.currentThread():
                self._widget.append_text(text, level)
            else:  # marshal writes from worker threads onto the GUI thread
                QtCore.QTimer.singleShot(
                    0,
                    lambda t=text, lv=level: (
                        self._widget.append_text(t, lv) if self._widget else None
                    ),
                )
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------------
    def begin_capture(self) -> "ScriptConsole":
        """Start recording stdout/stderr/logging into the transcript buffer NOW (idempotent).

        Call as early as possible (the tentacle startup script does, before ``import
        tentacle``) so the console — whenever it is first shown — already holds the whole
        session's output, greeting banner included. UI-free: safe with no Qt and no window.
        """
        self._capture.install()
        return self

    def restore(self) -> "ScriptConsole":
        """Reinstate the previous session's console — the Blender analogue of Maya's
        ``workspaceControl`` ``uiScript`` restore (Maya re-runs the uiScript from its
        workspace prefs at launch; Blender has no such hook, so the tentacle host calls
        this explicitly once the Qt host is up). Starts capture unconditionally, then
        re-shows the console if it was visible when the last session ended."""
        self.begin_capture()
        if self._load_state().get("visible", False):
            self.show()
        return self

    def show(self) -> "ScriptConsole":
        """Dock the console into the main window and persist visible=True.

        The capture + widget are built ONCE and are never torn down by :meth:`hide` —
        only undocked/hidden — so a later ``show()`` reuses the same widget with
        everything captured while hidden already in it. When embedding can't work here
        (or building the widget fails), the console degrades to a docked native Info Log
        area — **observably**: the reason is logged, so a "bare, unformatted pane"
        symptom is diagnosable, not silent.
        """
        import logging

        log = logging.getLogger(__name__)
        self._capture.install()  # capture precedes UI — also covers standalone (no-restore) use
        try:
            height = int(self._load_state().get("height") or 0) or self.DEFAULT_HEIGHT
        except (TypeError, ValueError):  # corrupt/foreign state value
            height = self.DEFAULT_HEIGHT

        widget = None
        if QtDock.supported():
            try:
                widget = self._ensure_widget()
            except Exception:
                log.exception(
                    "Script Output: console widget build failed; degrading to the docked "
                    "native Info Log."
                )
        elif sys.platform == "win32":  # the supported path — say why it fell back
            log.warning(
                "Script Output: no QApplication found; showing the docked native Info "
                "Log only. The syntax-highlighted console needs the tentacle Qt host."
            )
        else:  # the degrade must always be observable, off-Windows included
            log.warning(
                "Script Output: Qt embedding is Windows-only (WS_CHILD dock); showing "
                "the docked native Info Log only on this platform."
            )

        if widget is not None:
            if not self._dock.dock(widget, height=height):
                log.warning(
                    "Script Output: docking failed (no viewport to dock into, or the "
                    "main window's native handle couldn't be resolved); showing the "
                    "docked native Info Log only."
                )
                widget = None
        if widget is None and not self._ensure_fallback_area(height):
            return self  # nothing to dock into — nothing shown, nothing persisted
        self._visible = True
        self._save_state(True)
        return self

    def hide(self) -> None:
        """Undock (persisting the user's strip height) and persist visible=False. Capture
        keeps running in the background — stdout/stderr/logging keep accumulating, so the
        next :meth:`show` (this session or the next) picks up where this left off."""
        height = self._area_height()
        self._dock.undock()
        self._close_fallback_area()
        self._visible = False
        self._save_state(False, height=height)

    def is_open(self) -> bool:
        return self._visible

    def teardown(self) -> None:
        """Full un-install for a HOST RELOAD (``tb.reload()``): undock the area, drop the
        widget + draw handler + listener, restore the streams — WITHOUT touching the
        persisted flag, so the reloaded module's :meth:`restore` re-opens the console
        fresh. Without this, the old module's glue, widget and docked area survive the
        reload and the new module's ``restore()`` docks a SECOND area. (Blender quit
        needs none of this — atexit restores the streams; the rest dies with the
        process.)"""
        self._dock.teardown()
        self._close_fallback_area()
        if self._widget is not None:
            try:
                self._widget.close()
                self._widget.deleteLater()
            except Exception:
                pass
            self._widget = None
        self._capture.uninstall()
        self._visible = False

    # -- internals -------------------------------------------------------------------
    def _area_height(self) -> Optional[int]:
        """The current docked strip height (px), or None when nothing is docked — what
        gets persisted so the user's resize survives hide/show and across sessions."""
        for area in (self._dock.area, self._fallback_area):
            try:
                if area is not None:
                    return int(area.height)
            except (ReferenceError, AttributeError):
                continue
        return None

    def _ensure_fallback_area(self, height: int) -> bool:
        """Dock the bare native Info Log (the no-Qt / off-Windows degrade); idempotent."""
        import blendertk as btk

        try:
            if self._fallback_area is not None and any(
                r.type == "WINDOW" for r in self._fallback_area.regions
            ):
                return True
        except (ReferenceError, AttributeError):
            pass  # user closed/merged it away — re-dock
        self._fallback_area = None
        window = btk.main_window()
        if window is None:
            return False
        area = btk.dock_editor("Info Log", edge_size=height, window=window)
        if area is None:
            return False
        self._fallback_window = window
        self._fallback_area = area
        return True

    def _close_fallback_area(self) -> None:
        """Close OUR fallback area only (no-op if already gone — a user's own separately
        opened Info Log area must survive)."""
        import blendertk as btk

        if self._fallback_area is None:
            return
        try:
            btk.close_area(self._fallback_window, self._fallback_area)
        except Exception:
            pass
        self._fallback_area = None
        self._fallback_window = None

    def _on_detach(self) -> None:
        # Runtime state only — deliberately NOT persisted: this also fires from the
        # liveness watchdog during Blender's quit teardown (main window already
        # destroyed), and writing visible=False there would cancel the next session's
        # restore.
        self._visible = False


# -----------------------------------------------------------------------------
# Module API — mirrors mayatk.env_utils.script_output (show / toggle / hide),
# plus the Blender-only persistence hooks (begin_capture / restore).
# -----------------------------------------------------------------------------
def show(*args, **kwargs) -> ScriptConsole:
    """Dock + show the Script Output console (reuses the persistent instance/widget if one
    already exists from an earlier show/hide cycle — history isn't lost)."""
    return ScriptConsole.instance().show()


def hide(*args, **kwargs) -> None:
    """Undock + hide the Script Output console (capture keeps running in the background)."""
    ScriptConsole.instance().hide()


def toggle(*args, **kwargs):
    """Toggle the Script Output console shown/hidden."""
    console = ScriptConsole.instance()
    if console.is_open():
        console.hide()
        return None
    return console.show()


def begin_capture() -> ScriptConsole:
    """Start the stdout/stderr/logging capture now (idempotent; UI-free). Call as early as
    possible at startup so the console's transcript covers the whole session."""
    return ScriptConsole.instance().begin_capture()


def restore() -> ScriptConsole:
    """Start capture and re-open the console if it was open when the previous session
    ended — called by the tentacle host at launch (≈ Maya's workspace uiScript restore)."""
    return ScriptConsole.instance().restore()


if __name__ == "__main__":
    show()
