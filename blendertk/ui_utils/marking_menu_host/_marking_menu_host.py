# !/usr/bin/python
# coding=utf-8
"""What a uitk marking menu needs from Blender: :class:`BlenderMarkingMenuHost`.

Blender is not a Qt app, so it answers most of ``uitk.MarkingMenuHost`` differently from a
plain Qt application (``uitk.PureMarkingMenuHost``):

* **start** provisions a Qt binding, the ``QApplication``, the hidden ``blender_widget``
  parent and the event pump (:class:`~blendertk.ui_utils.qt_runtime.QtRuntime`);
* **defer** builds on a one-shot persistent ``bpy.app.timers`` callback (startup scripts run
  before the UI settles) and shows the two errors that carry their fix in a popup;
* **bind_activation_key** installs the 3D View keymap bridge and its poller
  (:class:`~blendertk.ui_utils.marking_menu_host.keymap_bridge.KeymapBridge`): GHOST owns
  the keyboard, so the menu's own Qt shortcut never sees a viewport keystroke;
* **mouse_buttons** reads the physical buttons (GHOST owns the mouse too);
* **adopt_window** makes each window an OS-owned window of Blender's GHOST window;
* **key_event** sends Ctrl+Z to Blender's undo and completes the gesture on the activation
  key's release;
* **attach** reopens the Script Output console, arms the ordered-selection tracker, pins the
  slots' ``ExecutionMonitor`` interpreter and reports a broken activation path.

Importable without Qt and without a running Blender: ``qtpy`` and ``bpy`` are imported in the
methods that need them.
"""

import re

from uitk import PureMarkingMenuHost

from blendertk.ui_utils.blender_window import BlenderWindow
from blendertk.ui_utils.marking_menu_host.keymap_bridge import (
    ActivationDiagnostics,
    KeymapBridge,
)
from blendertk.ui_utils.qt_runtime import (
    QtBootstrap,
    QtPlatformUnavailable,
    QtRuntime,
)


class BlenderMarkingMenuHost(PureMarkingMenuHost):
    """Blender's answers to ``uitk.MarkingMenuHost`` (see the module docstring)."""

    #: Blender startup scripts run before the UI has settled; the Qt host needs a beat.
    START_DELAY = 0.5

    @property
    def ui_handler(self):
        """``BlenderUiHandler``: wraps Blender's native menus, shows the tool windows."""
        from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

        return BlenderUiHandler

    # ---------------------------------------------------------------- launcher
    def start(self) -> None:
        """Stand up Qt in Blender: a binding, the QApplication, its parent, the pump."""
        QtBootstrap.ensure_qt()
        app = QtRuntime.ensure_qapp()
        QtRuntime.ensure_widget(app)
        QtRuntime.start_pump(app)

    def defer(self, fn):
        """Run *fn* on a one-shot ``bpy.app.timers`` callback; returns None.

        persistent: Blender enables add-ons (and runs startup scripts) BEFORE it opens a
        .blend named on the command line -- a double-clicked scene -- and a file load drops
        every non-persistent timer, so the menu never came up on such a start.
        """
        import bpy

        def build():
            try:
                fn()
            except (ImportError, QtPlatformUnavailable) as error:
                # A timer callback has no handler, so this would print a traceback
                # to the SYSTEM console -- hidden by default on Windows -- and the
                # user would see the launcher simply do nothing. Each error carries
                # its fix (the install line; the Qt libraries Linux lacks), which is
                # its entire value, so it has to reach a surface they are looking
                # at. Scoped to these two deliberately: a real bug in registration
                # keeps its traceback rather than being dressed up as one of them.
                self._report_startup_error(error)
            return None  # a timer returning None is unregistered — one shot, not a poll

        bpy.app.timers.register(build, first_interval=self.START_DELAY, persistent=True)
        return None

    @staticmethod
    def _report_startup_error(error):
        """Show *error* in Blender, falling back to the console.

        ``popup_menu`` is the only surface available here: there is no operator to
        ``self.report`` from, and a timer callback is not guaranteed a UI context,
        so the call itself can raise. Both the popup and the fallback are inside
        the guard because the thing that must not happen is the ORIGINAL error
        escaping the callback -- Blender then reports the add-on as broken, which
        is a worse answer than the one this exists to deliver.

        The message is split on sentences and line breaks rather than drawn as one
        label: Blender does not wrap a ``layout.label``, so the pip line -- the part
        the user has to read -- would be clipped at the popup's edge, and a
        multi-line error (the Qt preflight's) would draw its breaks inside a label.
        """
        import bpy

        text = str(error)
        try:
            # Split AFTER the terminator, keeping it: appending "." to each
            # piece double-punctuates the last one -- and the last one is the
            # pip line, the part that has to be readable.
            lines = [
                s for s in (p.strip() for p in re.split(r"(?<=\.)\s+|\n", text)) if s
            ]

            def draw(self, _context):
                for line in lines:
                    self.layout.label(text=line)

            bpy.context.window_manager.popup_menu(draw, title="Tentacle", icon="ERROR")
        except Exception:  # noqa: BLE001 -- no UI context; the console is all that is left
            print(f"[tentacle] {text}")

    # ---------------------------------------------------------------- the menu's questions
    def main_window(self):
        """``app.blender_widget`` (set by :meth:`start`), or None: the menu then shows as
        a top-level window, which works over Blender. Never raises."""
        from qtpy import QtWidgets

        app = QtWidgets.QApplication.instance()
        return getattr(app, "blender_widget", None) if app is not None else None

    def mouse_buttons(self):
        """The physically held buttons, else Qt's view.

        GHOST owns the mouse, so ``QApplication.mouseButtons()`` is blind to a button
        held over the viewport. Reading the physical state lets the menu's
        ``MouseTracking`` run its drag-gated ``track()`` during a chord gesture -- what
        reveals the ``visible_on_mouse_over`` regions (cameras/editors upper+lower) --
        without which the chord submenus "don't launch". Qt's answer off Windows / when
        nothing is physically held.
        """
        from qtpy import QtCore

        physical = KeymapBridge.physical_mouse_buttons()
        return physical if physical != QtCore.Qt.NoButton else super().mouse_buttons()

    def bind_activation_key(self, menu, key: str) -> None:
        """Bridge the activation key from Blender's keymap to *menu*.

        The first call for a menu installs the 3D View keymap items and the held-button
        poller (GHOST eats the key while a mouse button is held); each later one moves
        Blender's half of the binding onto the menu's new key -- the 3D View item and the
        poller's virtual-key -- which the menu's Qt shortcut alone would leave behind.
        Never raises: a live Qt binding beats no binding.
        """
        if KeymapBridge.tcl is not menu:
            try:
                KeymapBridge.install_keymap(
                    menu, KeymapBridge.qt_key_to_blender_type(key)
                )
                KeymapBridge.install_poller(menu, key)
            except Exception as error:
                print(f"{__file__}: Blender keymap bridge skipped: {error}")
            return
        try:
            KeymapBridge.rebind(menu, key)
        except Exception as error:
            print(f"{__file__}: Blender keymap rebind skipped: {error}")

    def adopt_window(self, widget) -> None:
        """Make *widget* an OS-owned window of Blender's GHOST window.

        Blender isn't a Qt app, so a top-level Qt window is otherwise an unrelated OS
        window: it falls behind Blender the moment it loses focus and doesn't
        move/minimize with it. The Qt idiom -- ``setTransientParent`` to the GHOST window
        wrapped as a foreign ``QWindow`` -- is kept for Qt's own stacking bookkeeping and
        the non-Windows path, but it does **not** set the Windows owner for a *foreign*
        transient parent (verified on PySide6 6.10), so on Windows the owner is set
        explicitly (:meth:`BlenderWindow.set_owner`). Re-asserted on every show:
        construction can run before the GHOST window is enumerable, and Qt re-creating a
        native window on a flag change drops the OS owner. Best-effort.
        """
        try:
            native = (
                BlenderWindow.main_qwindow()
            )  # also caches app._blender_native_hwnd
            if native is None or widget is None:
                return
            from qtpy import QtWidgets

            widget.winId()  # force native-window creation so windowHandle() exists
            handle = widget.windowHandle()
            if handle is not None:
                handle.setTransientParent(native)
            app = QtWidgets.QApplication.instance()
            BlenderWindow.set_owner(
                widget, getattr(app, "_blender_native_hwnd", 0) if app else 0
            )
        except Exception as error:
            # Best-effort z-order nicety; DEBUG-gated so a persistent failure can't spam the
            # console (this runs per tool-window open, not just once at construction).
            if KeymapBridge.DEBUG:
                print(f"{__file__}: parent-to-Blender skipped: {error}")

    def key_event(self, menu, event) -> None:
        """Ctrl+Z while the menu holds focus -> Blender's undo; the activation key's
        release -> complete the gesture.

        Once shown, the overlay takes OS keyboard focus, so the activation key's RELEASE
        arrives as a Qt event (not at Blender's keymap, which no longer has focus) -- and
        the menu's own shortcut release filter is disarmed because its press never fired
        (GHOST consumed the key-down). Completing the gesture here is what hides the menu
        and releases the mouse grab; idempotent with the keymap's RELEASE item.
        """
        from qtpy import QtCore, QtWidgets

        if event.isAutoRepeat():
            return
        if event.type() == QtCore.QEvent.KeyPress:
            modifiers = QtWidgets.QApplication.instance().keyboardModifiers()
            if (
                event.key() == QtCore.Qt.Key_Z
                and modifiers == QtCore.Qt.ControlModifier
            ):
                try:
                    import bpy

                    bpy.ops.ed.undo()
                except Exception:
                    pass
        elif (
            event.type() == QtCore.QEvent.KeyRelease
            and event.key() == menu._activation_key
        ):
            try:
                KeymapBridge.drive_release()
            except Exception:
                pass

    def attach(self, menu) -> None:
        """Blender's once-per-menu setup, each step guarded so none blocks a launch."""
        # Re-open the Script Output console if it was open when the last session ended
        # (≈ Maya's workspaceControl uiScript restore — Blender has no such hook, so the
        # host calls it explicitly once the Qt host is up). Also starts the
        # stdout/stderr/logging capture, so the console's transcript covers the session
        # even before it's first shown.
        try:
            from blendertk.env_utils.script_output import ScriptConsole

            ScriptConsole.restore()
        except Exception as error:
            print(f"{__file__}: script_output restore skipped: {error}")

        # Arm the ordered-selection tracker from launch so click order is already
        # recorded when an order-consuming tool (Reorder Selection, ordered transfers)
        # first asks for it — enable() is idempotent and its handler no-ops until the
        # selection actually changes.
        try:
            from blendertk.edit_utils.selection import SelectionOrder

            SelectionOrder.enable()
        except Exception as error:
            print(f"{__file__}: SelectionOrder enable skipped: {error}")

        # ExecutionMonitor spawns the slots' spinner and over-threshold dialog as
        # subprocesses, so the interpreter it uses has to be a real python: hand it
        # Blender's bundled one rather than relying on its ``sys.executable`` fallback.
        # On 5.1 that fallback happens to be correct (``sys.executable`` is
        # ``<prefix>/bin/python.exe``), but blendertk supports 4.x, where it was the
        # Blender binary. Pinning it explicitly costs nothing and cannot regress.
        try:
            import pythontk as ptk

            from blendertk.env_utils._env_utils import EnvUtils

            ptk.ExecutionMonitor.set_interpreter(EnvUtils.python_executable())
        except Exception as error:
            print(f"{__file__}: ExecutionMonitor interpreter skipped: {error}")

        self.report_activation()

    @staticmethod
    def report_activation():
        """The live activation report; on a PROBLEM or CONFLICT, also printed in full and
        raised as a Blender popup (a console user can't see the hidden system console).
        Silent on success — a routine launch shouldn't announce itself.

        Returns:
            str: The report (:meth:`ActivationDiagnostics.report`).
        """
        report = ActivationDiagnostics.report(emit=False)
        if "PROBLEM" in report or "CONFLICT" in report:
            print(report)  # something's actually wrong — surface the full diagnostic
            message = report.splitlines()[-1].split(": ", 1)[
                -1
            ]  # verdict text, minus the "VERDICT :" label
            try:
                import bpy

                bpy.context.window_manager.popup_menu(
                    lambda menu, _ctx: menu.layout.label(text=message[:200]),
                    title="Tentacle activation",
                    icon="ERROR",
                )
            except Exception:
                pass
        return report


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
