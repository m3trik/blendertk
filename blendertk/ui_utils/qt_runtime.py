# !/usr/bin/python
# coding=utf-8
"""Qt inside Blender: provision a binding, stand up a QApplication, pump it from Blender's loop.

Blender is not a Qt app (GHOST event loop, no bundled PySide6), so -- unlike Maya, which
provides a live QApplication -- a Qt UI in Blender needs three things before its first widget:

* :class:`HostedApp` -- the application Qt is stood up for, named by it (tentacle:
  ``"tentacle"``): the name that spells its env vars, its managed folder and its messages.
* :class:`QtBootstrap` -- a Qt binding importable in Blender's bundled Python (pip-installed
  on first launch, or a pre-staged ``<NAME>_QT_DEPS`` folder), and ``QT_API`` pinned.
* :class:`QtRuntime` -- the process QApplication, the hidden ``blender_widget`` parent, and an
  event pump on ``bpy.app.timers`` that delivers Qt's posted events.
* :class:`QtPlatformUnavailable` -- the error raised, before any QApplication exists, when Qt
  cannot open a window in this process at all (Linux without xcb's libraries).

Moved from tentacle's ``tcl_blender`` (``_QtBootstrap`` / ``_QtHost`` / ``tcl.QtPlatformUnavailable``),
so blendertk owns Blender's Qt hosting. Importable without Qt and without a running Blender:
``qtpy`` and ``bpy`` are imported inside the methods that need them.
"""

import os
import sys

import pythontk as ptk


class HostedApp:
    """The application blendertk stands Qt up for in this Blender: it names itself.

    blendertk is a library and names no product. Whatever launches a Qt UI here sets
    :attr:`name` before the first :meth:`QtBootstrap.ensure_qt` (tentacle's entry points set
    ``"tentacle"``, ``Tcl.APP_NAME``; ``BlenderMarkingMenuHost.start(app=)`` does it for a
    launcher), and that name spells what is made for it: the env vars it reads
    (:meth:`env`: ``TENTACLE_QT_DEPS``, ``TENTACLE_CLICK_DEBUG_LOG``), the managed qt_deps
    folder (:meth:`QtBootstrap.qt_install_dir`) and the prefix of its messages
    (:meth:`label`). So an install named ``"tentacle"`` keeps every ``TENTACLE_*`` variable
    and folder it had. One hosted application per Blender process: class state.
    """

    #: The hosted application's name, lower case; the default names the library itself.
    name = "blendertk"

    @classmethod
    def env(cls, suffix):
        """The ``<NAME>_<suffix>`` environment variable (``env("QT_DEPS")`` ->
        ``$TENTACLE_QT_DEPS``), or None when unset."""
        return os.environ.get(f"{cls.name.upper()}_{suffix}")

    @classmethod
    def label(cls):
        """The name as a message title or prefix (``"Tentacle"``)."""
        return cls.name.capitalize()


class QtPlatformUnavailable(RuntimeError):
    """Qt cannot open a window in this host process, so the marking menu is off.

    Raised by :meth:`QtRuntime.ensure_qapp` BEFORE a QApplication is built: on Linux a Qt
    platform plug-in that fails to load aborts the process from C++ -- in-process,
    Blender, with the user's unsaved work. Like a missing engine, the message carries the
    fix (the system libraries to install), so the launcher shows it where the user is
    looking. A ``RuntimeError``, so a caller that catches that keeps catching it.
    """


# --------------------------------------------------------------------------------------------
# Import-time provisioning — the asymmetry between Maya and Blender.
# --------------------------------------------------------------------------------------------
# Maya (2025+) *bundles* PySide6, so qtpy resolves a binding with zero setup — and we must NEVER
# pip-install Qt into Maya (it would shadow/clash with the bundled copy). That's also why
# tentacle/uitk deliberately exclude qtpy/PySide from their pyproject ``dependencies``. Blender's
# bundled Python ships *no* Qt, so it has to be provided.
class QtBootstrap(ptk.LoggingMixin):
    """Make a Qt binding importable in Blender's Python *before* anything imports qtpy.

    :meth:`ensure_qt` must run before the first ``qtpy`` import: ``QT_API`` has to be set
    before qtpy is first imported, and Blender's bundled Python ships no binding at all.
    An interpreter that already resolves one (Maya's bundled PySide6, a provisioned Blender)
    makes every guard no-op.
    """

    #: What Blender's Qt-less Python needs pip-installed. tentacle's installer installs
    #: the same set alongside the package so this bootstrap finds a binding and no-ops
    #: (its copy is drift-guarded against this one by ``test_tentacle_installer.py``).
    QT_SPECS = ("PySide6", "qtpy")

    @staticmethod
    def qt_importable():
        """True if qtpy can resolve a real Qt binding (Maya's bundled PySide, or Blender's installed one)."""
        try:
            from qtpy import QtWidgets  # noqa: F401

            return True
        except Exception:
            return False

    @staticmethod
    def qt_install_dir():
        """Where Blender's on-demand Qt deps live. Prefer Blender's *own* per-version
        ``addons/modules`` dir (``bpy.utils.user_resource('SCRIPTS', 'addons/modules')``) —
        Blender has it on ``sys.path`` natively at TAIL precedence (after its bundled
        site-packages), so once installed Qt imports with no further wiring and nothing
        provisioned there can shadow a dist Blender ships. (The pre-2026-08 target was the
        sibling ``scripts/modules``, which sits at ``sys.path`` index 0 — a copy already
        there keeps winning imports, so existing installs stay live.) Falls back to a
        non-roaming managed dir named for the hosted application (:class:`HostedApp`) when
        bpy isn't importable (the install itself is bpy-gated, so that branch is just the
        reuse-scan)."""
        try:
            import bpy

            return os.path.normpath(
                bpy.utils.user_resource("SCRIPTS", path="addons/modules", create=True)
            )
        except Exception:
            # Windows: LOCALAPPDATA. Linux/macOS: the XDG data dir, never loose in the
            # home folder (pythontk's rule).
            base = os.environ.get("LOCALAPPDATA") or ptk.UserConfig.xdg_home("data")
            return os.path.join(
                base,
                HostedApp.name,
                "qt_deps",
                f"py{sys.version_info[0]}{sys.version_info[1]}",
            )

    @classmethod
    def ensure_qt(cls):
        """Make a Qt binding importable; no-op under Maya, on-demand pip-install under Blender.

        ``ensure_qt`` adds a pre-staged folder if you point ``<NAME>_QT_DEPS`` (the hosted
        application's, :meth:`HostedApp.env`: ``TENTACLE_QT_DEPS``) at one, otherwise
        pip-installs PySide6 + qtpy into Blender's *own* user-modules dir **on first launch** (the
        on-demand install) — gated on actually running inside Blender so a bare interpreter never gets
        an unsolicited download.
        """
        os.environ.setdefault("QT_API", "pyside6")
        if (
            cls.qt_importable()
        ):  # Maya (bundled) and any already-provisioned interpreter stop here.
            return
        install_dir = cls.qt_install_dir()
        for target in (
            HostedApp.env("QT_DEPS"),
            install_dir,
        ):  # reuse a pre-staged / previously-installed dir (append: host keeps precedence)
            if target and os.path.isdir(target) and target not in sys.path:
                sys.path.append(target)
        if cls.qt_importable():
            return
        try:
            import bpy  # noqa: F401 — only auto-install inside Blender, never a bare interpreter
        except Exception:
            return
        if (
            not install_dir
        ):  # user_resource can return "" if the path can't be resolved/created
            return
        import subprocess

        from blendertk.core_utils._core_utils import CoreUtils

        py = CoreUtils._blender_python_exe()
        cls.logger.info(
            f"{HostedApp.name}: Blender has no Qt -- installing PySide6 + qtpy into "
            f"{install_dir} (first launch only)..."
        )
        try:
            os.makedirs(install_dir, exist_ok=True)
            subprocess.run([py, "-m", "ensurepip", "--upgrade"], check=False)
            # Resolver-aware, never a raw ``pip install --target``: that plans a
            # complete standalone closure and re-downloads deps Blender already
            # ships (this is how a shadowing ``packaging`` landed in profiles).
            ptk.PackageManager(python_path=py).install_targeted(
                list(cls.QT_SPECS), install_dir
            )
        except Exception as error:
            cls.logger.error(
                f"{HostedApp.name}: on-demand Qt install failed ({error}). Set "
                f"{HostedApp.name.upper()}_QT_DEPS to a folder holding PySide6 + qtpy, or "
                "pip-install them into Blender's Python."
            )
            return
        # APPEND, never insert(0): the host's bundled packages keep import precedence.
        if install_dir not in sys.path:
            sys.path.append(install_dir)


class QtRuntime:
    """Qt in Blender — Blender has no QApplication of its own, so stand one up and pump it from
    the timer loop. Idempotent, so re-launching reuses the live runtime. State that must survive a
    reload caches on the QApplication; ``_pump_registered`` resets with the module so a reloaded
    module registers a fresh pump (the old one retires via its generation token)."""

    _pump_registered = False

    @staticmethod
    def ensure_qapp():
        """Return the process QApplication, creating one if Blender has none.

        Linux: the same platform policy as a standalone uitk app (X11 first on
        a Wayland session, :meth:`uitk.Bootstrap.configure_platform`), and a
        one-time check that Qt can open a window at all -- a platform plug-in
        that fails to load (xcb's system libraries missing) aborts the process
        from C++, and in-process that is Blender, with the user's unsaved work.
        """
        from qtpy import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is None:
            if sys.platform.startswith("linux"):
                from uitk import Bootstrap

                Bootstrap.configure_platform()
                QtRuntime._preflight_platform()
            app = QtWidgets.QApplication(sys.argv or ["blender"])
        return app

    @staticmethod
    def _preflight_platform():
        """Raise (never abort) when Qt cannot start a GUI in this environment.

        Starts a QApplication in a CHILD of Blender's own python, with this
        process's import path and platform settings: if the child dies, so would
        Blender. The error names what Linux usually lacks.

        Raises:
            QtPlatformUnavailable: The child failed, or could not be run at all.
                The launcher shows it in a popup.
        """
        import subprocess

        from blendertk.core_utils._core_utils import CoreUtils

        code = (
            "import sys; from qtpy import QtWidgets; "
            "QtWidgets.QApplication(sys.argv[:1]); print('ok')"
        )
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        try:
            probe = subprocess.run(
                [CoreUtils._blender_python_exe(), "-c", code],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise QtPlatformUnavailable(
                f"{HostedApp.name}: could not test whether Qt can open a window here, so "
                f"the marking menu is off: {e}"
            ) from e
        if probe.returncode != 0 or "ok" not in probe.stdout:
            raise QtPlatformUnavailable(
                f"{HostedApp.name}: Qt cannot open a window here, so the marking menu is off "
                f"(QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')!r}).\n"
                f"{probe.stderr.strip()[-800:]}\n"
                "On Linux, Qt's X11 plug-in needs: libxcb-cursor0 libxkbcommon-x11-0 "
                "libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0."
            )

    @staticmethod
    def ensure_widget(app):
        """Establish ``app.blender_widget`` — the parent for the marking menu.

        A hidden top-level ``QWidget`` (top-level Qt windows over Blender work — Phase 0).
        Native z-order against Blender's own window is handled separately by transient-parenting
        the menu to the GHOST window (:meth:`BlenderWindow.main_qwindow`), not by this widget.
        """
        from qtpy import QtWidgets

        if getattr(app, "blender_widget", None) is None:
            widget = QtWidgets.QWidget()
            widget.setObjectName("BlenderWindow")
            app.blender_widget = widget
        return app.blender_widget

    @classmethod
    def start_pump(cls, app, interval=0.01):
        """Deliver Qt's POSTED events from Blender's timer loop so the Qt UI stays live (idempotent).

        The pump carries a generation token on the QApplication: after a reload (tentacle's
        ``Settings > Reload``) the
        module is re-executed (resetting ``_pump_registered``), so the reloaded module registers a
        fresh pump and stamps a new token — the superseded pump sees the mismatch on its next tick and
        unregisters itself (no double-pumping, no stale-closure leak).

        **Why ``sendPostedEvents`` and NEVER ``processEvents``** (2026-07-18, py-spy-proven): on a
        shared GUI thread, GHOST's own message loop already dispatches every thread window's native
        messages — Qt input, Qt paints (WM_PAINT), Qt timers (the dispatcher's internal message
        window) all arrive that way, the standard plugin-embed contract. The ONLY thing Qt cannot get
        by itself is delivery of its posted (queued) ``QEvent``s — which is exactly what
        ``sendPostedEvents`` flushes, dispatching NO native messages. ``processEvents`` instead
        PeekMessage-drains the WHOLE thread queue, i.e. it dispatches BLENDER's queued messages from
        a bpy-timer context: on a heavy wndproc cascade (a per-monitor DPI transition mid-drag, a
        Win11 snap-release) that re-enters Blender's half-finished draw — captured stacks tower
        ``processEvents -> DispatchMessage -> GHOST wndproc -> wglSwapBuffers -> nested dispatch ->
        WM_TIMER -> pump`` several layers deep until the innermost dispatch never returns. Every bpy
        timer then stops forever while the window still answers ``WM_NULL``, so the OS never even
        flags Not Responding. Deterministic repros: ``tests/blender/console_frame_resize_check.py``
        (snap legs) and ``console_dpi_drag_check.py`` (cross-DPI drag); a WM_TIMER can arrive inside
        the nest with ``GUI_INMOVESIZE`` reading False and any cooldown already starved out, so no
        gate on ``processEvents`` closes the hole — not dispatching native messages at all does.

        The modal-loop gate (with a 0.5 s cooldown bridging the probe's nested-context lies) and a
        re-entrancy latch are kept as cheap belts: posted-event handlers can run arbitrary Python,
        and skipping a tick during a drag costs nothing.
        """
        import bpy
        from qtpy import QtCore

        from blendertk.ui_utils.blender_window import BlenderWindow

        if cls._pump_registered:
            return
        token = object()
        app._tentacle_pump_token = token
        pumping = {
            "now": False
        }  # re-entrancy latch (closure state, fresh per generation)

        def _pump():
            if getattr(app, "_tentacle_pump_token", None) is not token:
                return None  # superseded by a reloaded module's pump — unregister
            if pumping["now"] or BlenderWindow.native_modal_loop_active(cooldown=0.5):
                return interval
            pumping["now"] = True
            try:
                if sys.platform.startswith("linux"):
                    # X11/Wayland: Qt reads its OWN display connection, which
                    # only its dispatcher services -- nothing of Blender's
                    # dispatches Qt input, paints or timers here, so run Qt's
                    # loop, briefly. It touches Qt's event sources alone, never
                    # Blender's. Linux only, matching ensure_qapp: on macOS Qt
                    # and Blender share one NSApplication event queue, so
                    # draining it re-enters Blender -- the Windows hazard above.
                    QtCore.QCoreApplication.processEvents(
                        QtCore.QEventLoop.AllEvents, 5
                    )
                QtCore.QCoreApplication.sendPostedEvents()
                # deleteLater() garbage: posted at a loop level no plain flush matches,
                # so it must be requested explicitly — the plugin-embed idiom.
                QtCore.QCoreApplication.sendPostedEvents(
                    None, QtCore.QEvent.DeferredDelete
                )
            finally:
                pumping["now"] = False
            return interval

        # persistent — non-persistent timers are dropped on File ▸ New/Open, which would
        # silently freeze the Qt UI for the rest of the session.
        bpy.app.timers.register(_pump, persistent=True)
        cls._pump_registered = True
