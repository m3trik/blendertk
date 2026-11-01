# !/usr/bin/python
# coding=utf-8
"""Opt-in click tracer for "buttons need several clicks" reports in the menu Blender hosts.

:class:`ClickDebugger` logs every mouse press/release with the context needed to see why a
click did not act, plus whether each click's slot ran; a support capture runs
``ClickDebugger.enable()``, reproduces the dead click, then shares the file it names. Off until
enabled: nothing is opened, filtered or patched by import. Moved from tentacle's
``tcl_blender._ClickDebugger``; Qt is imported inside the methods that need it.
"""

import os
import sys
import time

from blendertk.ui_utils.qt_runtime import HostedApp, QtRuntime


class ClickDebugger:
    """Opt-in two-layer tracer for "buttons need several clicks" reports.

    (1) An app-wide mouse-event observer logs every press/release/dblclick (target widget,
    ``widgetAt``, grab owner, active window, foreground, the menu's ``armed``).
    (2) A slot-dispatch trace (:meth:`_install_slot_trace`) logs whether each click's slot
    actually ran.

    Captured data ruled out the original window-activation theory for the *open panel* case:
    presses and releases deliver cleanly to the button (``fg_is_ghost=False``, ``grab=None``)
    yet the action can still not happen — so the loss is at the slot layer, not the OS window.
    The slot trace localizes which: ``clicked`` never reaching the wrapper (dblclick-eaten or a
    lost connection), a debounce/coalesce swallow, or the slot running but raising into Blender's
    hidden system console.
    """

    _path = None  # resolved lazily by log_path(), never at import (it sweeps a dir)
    _fh = None
    _filter = None
    _slot_patch = None  # (orig __call__, orig _invoke) saved for restore on disable
    _filter_class = None  # the event filter's class, built by _filter_type()

    @classmethod
    def log_path(cls):
        """Resolve (and cache) the trace sink — managed scratch, not the user's home.

        The sink used to be a hardcoded ``~/tentacle_click_debug.log``: unmanaged, never
        reclaimed, and growing without bound in the user's home directory across support
        captures. It goes through ``ptk.TempArtifacts`` instead, which age-sweeps stale
        same-prefix leftovers on allocation — so an abandoned capture is collected on a
        later run instead of leaking forever. Tagged by pid, so two Blender sessions never
        interleave into one file. Resolution is lazy (it allocates, which scans a directory)
        and creates nothing: only :meth:`enable` ever opens the file.

        Returns:
            (str): Absolute path of the trace log. The hosted application's
            ``<NAME>_CLICK_DEBUG_LOG`` (:meth:`HostedApp.env`: ``TENTACLE_CLICK_DEBUG_LOG``)
            overrides it -- an explicit (absolute or ``~``-relative) path wins over managed
            scratch, for a support capture that has to land somewhere known.
        """
        if cls._path is None:
            override = HostedApp.env("CLICK_DEBUG_LOG")
            if override:
                cls._path = os.path.abspath(os.path.expanduser(override))
            else:
                import pythontk as ptk

                cls._path = ptk.TempArtifacts(
                    f"{HostedApp.name}_click_debug", policy="detached"
                ).path(extension=".log", name=str(os.getpid()))
        return cls._path

    @staticmethod
    def _widget_id(w):
        """Short '<objectName-or-class>:<class>' id for a widget, never raises."""
        if w is None:
            return "None"
        try:
            return f"{w.objectName() or '-'}:{type(w).__name__}"
        except Exception:
            return repr(w)

    @classmethod
    def _write(cls, line):
        if cls._fh is None:
            return
        try:
            cls._fh.write(line + "\n")
        except Exception:
            pass

    @classmethod
    def _install_slot_trace(cls):
        """Trace the slot dispatch chokepoint so the log shows whether a click's
        slot actually *ran* — the event filter only proves the click was
        delivered, not that the slot fired. ``SLOT_CALL`` = the clicked signal
        reached the wrapper; ``SLOT_RUN``/``SLOT_RAISED`` = the slot body
        executed (and whether it threw — a Blender context error in
        ``@btk.undoable`` would surface here, invisible otherwise as it prints to
        Blender's hidden system console). A click with a clean delivery but no
        ``SLOT_CALL`` is dispatch loss; ``SLOT_CALL`` with no ``SLOT_RUN`` is a
        debounce/coalesce swallow; ``SLOT_RUN`` with no visible effect is an
        idempotent/no-op action, not a dead click. Patches the class (covers
        already-connected wrappers), restored verbatim by :meth:`_remove_slot_trace`."""
        if cls._slot_patch is not None:
            return
        try:
            from uitk import SlotWrapper
        except Exception as e:
            cls._write(f"{time.time():.3f} SLOT_TRACE_UNAVAILABLE err={e!r}")
            return

        # Anchor to the TRUE original, peeling any stale trace left installed by a
        # prior enable that was never disabled before a module reload — otherwise we
        # wrap an already-traced function (double logging) and disable() would restore
        # to the stale trace, leaving logging on after "off".
        orig_call = getattr(
            SlotWrapper.__call__, "_slot_trace_orig", SlotWrapper.__call__
        )
        orig_invoke = getattr(
            SlotWrapper._invoke, "_slot_trace_orig", SlotWrapper._invoke
        )

        def _name(self):
            try:
                w = self.widget.objectName() if self.widget is not None else "?"
            except Exception:
                w = "?"
            return f"{w}:{getattr(self.slot, '__name__', '?')}"

        def traced_call(self, *args, **kwargs):
            try:
                ui = getattr(self.widget, "ui", None)
                uin = ui.objectName() if ui is not None else "?"
                deb = getattr(self.widget, "debounce", 0) or 0
                cls._write(
                    f"{time.time():.3f} SLOT_CALL widget={_name(self)} ui={uin} debounce={deb}"
                )
            except Exception:
                pass
            return orig_call(self, *args, **kwargs)

        def traced_invoke(self, *args, **kwargs):
            cls._write(f"{time.time():.3f} SLOT_RUN  widget={_name(self)}")
            try:
                return orig_invoke(self, *args, **kwargs)
            except BaseException as e:
                cls._write(
                    f"{time.time():.3f} SLOT_RAISED widget={_name(self)} err={e!r}"
                )
                raise

        traced_call._slot_trace_orig = orig_call
        traced_invoke._slot_trace_orig = orig_invoke
        SlotWrapper.__call__ = traced_call
        SlotWrapper._invoke = traced_invoke
        cls._slot_patch = (orig_call, orig_invoke)

    @classmethod
    def _remove_slot_trace(cls):
        """Restore the un-traced slot dispatch installed by :meth:`_install_slot_trace`."""
        if cls._slot_patch is None:
            return
        try:
            from uitk import SlotWrapper

            SlotWrapper.__call__, SlotWrapper._invoke = cls._slot_patch
        except Exception:
            pass
        cls._slot_patch = None

    @classmethod
    def _filter_type(cls):
        """The application event filter's class, built on first use: it subclasses
        ``QtCore.QObject``, and this module must import without Qt (headless Blender)."""
        if cls._filter_class is not None:
            return cls._filter_class
        from qtpy import QtCore, QtWidgets

        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        class Filter(QtCore.QObject):
            """Application event filter that logs each mouse-button event with the context needed to see
            why a first click doesn't execute. Installed by :meth:`ClickDebugger.enable`, off by default."""

            _NAMES = {
                QtCore.QEvent.MouseButtonPress: "PRESS",
                QtCore.QEvent.MouseButtonRelease: "RELEASE",
                QtCore.QEvent.MouseButtonDblClick: "DBLCLICK",
            }

            def eventFilter(self, obj, event):
                name = self._NAMES.get(event.type())
                if name:
                    try:
                        app = QtWidgets.QApplication.instance()
                        gpos = (
                            event.globalPosition().toPoint()
                            if hasattr(event, "globalPosition")
                            else event.globalPos()
                        )
                        at = QtWidgets.QApplication.widgetAt(gpos)
                        grab = QtWidgets.QWidget.mouseGrabber()
                        active = app.activeWindow() if app else None
                        held = getattr(KeymapBridge.menu, "armed", None)
                        fg = ""
                        if sys.platform == "win32" and app is not None:
                            try:
                                import ctypes

                                hwnd = ctypes.windll.user32.GetForegroundWindow()
                                fg = f" fg_is_ghost={hwnd == getattr(app, '_blender_native_hwnd', 0)}"
                            except Exception:
                                pass
                        ClickDebugger._write(
                            f"{time.time():.3f} {name:8} target={ClickDebugger._widget_id(obj)} "
                            f"widgetAt={ClickDebugger._widget_id(at)} grab={ClickDebugger._widget_id(grab)} "
                            f"active={ClickDebugger._widget_id(active)} held={held}{fg}"
                        )
                    except Exception:
                        pass
                return False  # never consume — pure observer

        cls._filter_class = Filter
        return Filter

    @classmethod
    def enable(cls):
        """Turn on click tracing — run in Blender's Python Console, reproduce the multi-click, then
        share the log file whose path this prints (see :meth:`log_path`).

        Installs an application-wide mouse-event observer (logs every press/release with the target
        widget, ``widgetAt``, grab owner, active window, foreground, and the menu's ``armed``).
        :meth:`disable` removes it.

        Returns:
            (str): The path being written to.
        """
        target = cls.log_path()
        if cls._fh is None:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            cls._fh = open(target, "a", buffering=1, encoding="utf-8")
        cls._write(
            f"\n===== click-debug enabled pid={os.getpid()} t={time.time():.3f} ====="
        )
        app = QtRuntime.ensure_qapp()
        if cls._filter is None:
            cls._filter = cls._filter_type()()
            app.installEventFilter(cls._filter)
        cls._install_slot_trace()  # also trace whether each click's slot actually runs
        print(
            f"{HostedApp.name}: click debugging ON\n  {target}\n"
            "Reproduce the multi-click, then blendertk.ClickDebugger.disable()."
        )
        return target

    @classmethod
    def disable(cls):
        """Remove the click tracer installed by :meth:`enable`."""
        from qtpy import QtWidgets

        app = QtWidgets.QApplication.instance()
        if cls._filter is not None and app is not None:
            app.removeEventFilter(cls._filter)
        cls._filter = None
        cls._remove_slot_trace()
        if cls._fh is not None:
            try:
                cls._fh.close()
            except Exception:
                pass
            cls._fh = None
        print(f"{HostedApp.name}: click debugging OFF")
