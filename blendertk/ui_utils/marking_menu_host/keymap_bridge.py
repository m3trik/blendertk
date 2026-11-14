# !/usr/bin/python
# coding=utf-8
"""Bridge a marking menu's activation key from Blender's keymap to Qt — the half Maya needs none of.

Blender's GHOST owns the keyboard, so a Qt ``QShortcut`` never sees a viewport keystroke.
:class:`KeymapBridge` registers one operator plus a **3D View** keymap item (PRESS + RELEASE)
that drive the live menu's activation state machine, a held-button poller covering what the
keymap cannot (GHOST eats the key while a mouse button is held), and the rebind logic that
keeps Blender's half of the binding on the menu's key. :class:`ActivationDiagnostics` reports
its live state in the Python console.

Moved from tentacle's ``tcl_blender`` (``_KeymapBridge`` / ``Diagnostics``). ``bpy`` and
``qtpy`` are imported inside the methods that need them, so the module imports anywhere.
"""

import os
import sys
import time

import pythontk as ptk

from blendertk.ui_utils.blender_window import BlenderWindow
from blendertk.ui_utils.qt_runtime import HostedApp


class KeymapBridge(ptk.LoggingMixin):
    """Bridge the activation key from Blender's keymap to the Qt marking menu — the half Maya needs none of.

    Maya is a Qt app, so MarkingMenu's ``QShortcut`` (ApplicationShortcut context) catches the
    activation key from anywhere — Maya adds nothing. Blender's GHOST owns the keyboard, so that
    shortcut never sees viewport keystrokes. This registers one Blender operator + a **3D-View**
    keymap item that drives the menu, restoring the "instantiate and the key works" behavior —
    automatically, on construction, with no separate add-on to enable. Scoped to the viewport on
    purpose: a 3D-View region keymap item is evaluated before the global ``Screen`` keymap, so it takes
    the key when the viewport has focus *without* us disabling anyone's global shortcut (F12 still
    renders everywhere else). ``import bpy`` is deferred so importing this module never needs Blender.

    The menu is driven through what ``uitk.MarkingMenu`` publishes for a host that owns the
    keyboard -- ``activate(buttons=)`` on key-down, ``release()`` on key-up, ``armed`` while the
    key is held -- never its private handlers.

    One per Blender process: the live menu (:attr:`menu`), the keymap items (:attr:`keymaps`), the
    held-button poller (:attr:`poller`) and the in-flight gesture flag (:attr:`gesture_active`) are
    class state, so the registered Blender operator and the poller timer reach them through the class.
    They reset when the module is re-executed on reload (matching the old module globals' semantics).

    The operator's ``bl_idname`` (``tentacle.show_marking_menu``) is a SAVED name: a rebind the
    user makes in Preferences ▸ Keymap is stored in ``userpref.blend`` under it, so it keeps
    tentacle's spelling wherever this class lives (moved from tentacle's ``tcl_blender``).
    """

    DEBUG = False  # set True to log each activation (helps confirm the keymap is firing live)

    menu = None  # the live marking menu the operator drives (one Qt host per Blender process)
    keymaps = []  # (keymap, keymap_item) pairs we added, for clean removal
    poller = (
        None  # live poll callback (held-button activation fallback), for clean removal
    )
    gesture_active = False  # a bridge-initiated press is awaiting its key-up (≈ GlobalShortcut pairing)
    chord_active = False  # the live gesture is a button-held chord (the grab path) — drives hover pump
    active_vk = None  # Windows virtual-key the poller watches; moves with a rebind
    key_down = False  # poller edge state for ``active_vk`` (class-level so a rebind can reset it)
    _declined = None  # (type, is_bare) of a rebind we refused — remembered so it's reported once
    # Preferences ▸ Keymap rebind scan. Throttled off the 20 ms poll cadence because it walks the
    # 3D-View keymap (hundreds of items) — once a second is instant to a human and free in aggregate.
    KEY_SCAN_INTERVAL = 1.0
    _last_key_scan = 0.0

    # Qt key-name → Blender keymap ``type`` enum. Most keys line up after stripping ``Key_`` and
    # upper-casing (``F12``→``F12``, ``A``→``A``, ``SPACE``→``SPACE``); these are the ones that don't —
    # notably the Windows/Cmd/Super key, which Qt calls ``Meta``/``Super_L`` and Blender calls ``OSKEY``.
    # Keyed by the key's *canonical Qt spelling* (not upper-cased) because this table is also read
    # backwards, to name the Qt key for a Blender type when adopting a Preferences ▸ Keymap rebind —
    # and ``QtCore.Qt`` has ``Key_Meta``, not ``Key_META``. The forward lookup case-folds via
    # ``_ALIAS_BY_UPPER``, so callers can still pass any casing.
    _BLENDER_KEY_ALIASES = {
        "Meta": "OSKEY",
        "Super_L": "OSKEY",
        "Super_R": "OSKEY",
        "Escape": "ESC",
        "Return": "RET",
        "Enter": "RET",
        "Control": "LEFT_CTRL",
        "Alt": "LEFT_ALT",
        "Shift": "LEFT_SHIFT",
        "PageUp": "PAGE_UP",
        "PageDown": "PAGE_DOWN",
        "Delete": "DEL",
        # Number row — Qt names them by digit, Blender's enum by word.
        "1": "ONE",
        "2": "TWO",
        "3": "THREE",
        "4": "FOUR",
        "5": "FIVE",
        "6": "SIX",
        "7": "SEVEN",
        "8": "EIGHT",
        "9": "NINE",
        "0": "ZERO",
        # Arrow keys — Blender suffixes ``_ARROW``.
        "Left": "LEFT_ARROW",
        "Right": "RIGHT_ARROW",
        "Up": "UP_ARROW",
        "Down": "DOWN_ARROW",
        # Punctuation whose Qt name diverges from Blender's enum identifier.
        "Semicolon": "SEMI_COLON",
        "BracketLeft": "LEFT_BRACKET",
        "BracketRight": "RIGHT_BRACKET",
        "Backslash": "BACK_SLASH",
        "QuoteLeft": "ACCENT_GRAVE",
    }
    # Case-folded forward lookup, so any spelling of a Qt key name resolves.
    _ALIAS_BY_UPPER = {
        name.upper(): key_type for name, key_type in _BLENDER_KEY_ALIASES.items()
    }
    # Blender type → canonical Qt name. Several Qt names share one Blender type (Meta/Super_L/
    # Super_R → OSKEY, Return/Enter → RET); reversing the item order before the (last-wins) dict
    # build makes the FIRST listed spelling the canonical one, so OSKEY names back to ``Meta``.
    _QT_BY_BLENDER = {
        key_type: name
        for name, key_type in reversed(list(_BLENDER_KEY_ALIASES.items()))
    }

    # Windows virtual-key → Qt button for the held-button poller (VK order: L=0x01, R=0x02, M=0x04).
    _POLL_VK_BUTTONS = (
        (0x01, "LeftButton"),
        (0x02, "RightButton"),
        (0x04, "MiddleButton"),
    )

    # --- key translation --------------------------------------------------------------------
    @classmethod
    def qt_key_to_blender_type(cls, key_show):
        """Translate a Qt key name (``'Key_F12'`` / ``'Key_Meta'``) to a Blender keymap ``type``
        (``'F12'`` / ``'OSKEY'``). Falls back to the stripped-upper name for keys that already match."""
        name = (key_show or "").replace("Key_", "").upper()
        return cls._ALIAS_BY_UPPER.get(name, name)

    @classmethod
    def blender_type_to_qt_key(cls, key_type):
        """Translate a Blender keymap ``type`` back to a Qt key name (``'F12'`` → ``'Key_F12'``).

        The inverse of :meth:`qt_key_to_blender_type`, needed to name the key a user picked in
        Preferences ▸ Keymap so the Qt side can adopt it. Unaliased types line up after case
        folding — single characters and F-keys stay upper (``'A'``, ``'F12'``), everything else is
        capitalized (``'SPACE'`` → ``'Space'``), matching ``QtCore.Qt``'s spelling. Returns ``None``
        for a type with no ``Key_*`` counterpart (Blender has keymap types Qt has no key for, e.g.
        the mouse/NDOF entries) rather than a name that would only be rejected downstream."""
        if not key_type:
            return None
        name = cls._QT_BY_BLENDER.get(key_type)
        if name is None:
            f_key = key_type[0] == "F" and key_type[1:].isdigit()
            name = key_type if len(key_type) == 1 or f_key else key_type.capitalize()
        from qtpy import QtCore

        qt_name = f"Key_{name}"
        return qt_name if hasattr(QtCore.Qt, qt_name) else None

    @staticmethod
    def _is_bare_press(kmi):
        """True if ``kmi`` is an unmodified PRESS chord (no Ctrl/Alt/Shift/OS/any) — the kind that
        collides with a bare activation key. A modified combo like ``Ctrl+F12`` does not."""
        return kmi.value == "PRESS" and not (
            kmi.any or kmi.ctrl or kmi.alt or kmi.shift or kmi.oskey
        )

    @staticmethod
    def _vk_for_key(key_show):
        """Windows virtual-key code for a Qt key name (``'Key_F12'`` → ``0x7B``); None if unmapped
        (the poller is then simply skipped — the keymap bridge still covers the no-button path).

        ``None`` off Windows, where there is no such thing: :meth:`install_poller` already returns
        early there, but :meth:`rebind` reaches this on every platform, and a single-character key
        would otherwise hit ``ctypes.windll`` and take the whole rebind down with it."""
        if sys.platform != "win32":
            return None
        name = (key_show or "").replace("Key_", "")
        if (
            len(name) > 1
            and name[0].upper() == "F"
            and name[1:].isdigit()
            and 1 <= int(name[1:]) <= 24
        ):
            return 0x70 + int(name[1:]) - 1  # VK_F1..VK_F24
        if len(name) == 1:
            import ctypes

            # VkKeyScanW returns a SHORT — force the restype so the -1 failure check is
            # reliable (ctypes' default c_int leaves the upper bits undefined).
            ctypes.windll.user32.VkKeyScanW.restype = ctypes.c_short
            code = ctypes.windll.user32.VkKeyScanW(ord(name.upper()))
            return code & 0xFF if code != -1 else None
        return None

    @classmethod
    def physical_mouse_buttons(cls):
        """Qt mouse-button mask read from the OS (``GetAsyncKeyState``), or ``NoButton``.

        GHOST owns the mouse, so Qt's own ``QApplication.mouseButtons()`` can't see a
        button held over the viewport. This reads the physical state directly — the one
        signal that's always true (same source as the held-button poller). Windows-only;
        ``NoButton`` elsewhere (the caller then falls back to Qt). Shared by the poller's
        chord detection and the menu's ``MouseTracking`` buttons-provider so the marking
        menu's ``visible_on_mouse_over`` Regions reveal during a grabbed chord gesture.
        """
        from qtpy import QtCore

        if sys.platform != "win32":
            return QtCore.Qt.NoButton
        import ctypes

        user32 = ctypes.windll.user32
        held = QtCore.Qt.NoButton
        for button_vk, name in cls._POLL_VK_BUTTONS:
            if user32.GetAsyncKeyState(button_vk) & 0x8000:
                held |= getattr(QtCore.Qt, name)
        return held

    # --- operator ---------------------------------------------------------------------------
    @classmethod
    def ensure_operator(cls):
        """Register the bridge operator once per process (idempotent)."""
        import bpy

        if hasattr(bpy.types, "TENTACLE_OT_show_marking_menu"):
            return

        class TENTACLE_OT_show_marking_menu(bpy.types.Operator):
            """Bridge a Blender key event to the Qt marking menu.

            GHOST owns the keyboard, so the menu's own Qt shortcut never fires from the viewport — this
            operator stands in for it, driving the SAME interaction state machine Maya's Qt
            ``GlobalShortcut`` does: ``KeymapBridge.drive_press`` on key-down (``menu.activate``: arms
            the gesture, shows + syncs the menu) and ``KeymapBridge.drive_release`` on key-up
            (``menu.release``: completes/hides, releases the grab). Two keymap items (PRESS + RELEASE)
            set ``phase``. The old bridge called ``show()`` directly — it painted the overlay but never
            armed it, so the menu was "stuck at the launch screen" (no grab → child buttons swallowed
            the click → no nav)."""

            bl_idname = "tentacle.show_marking_menu"
            # The menu's application names it (HostedApp, set before the first launch).
            bl_label = f"{HostedApp.label()} Marking Menu"
            # "press" (key-down) or "release" (key-up). The F821 suppression is a
            # pyflakes false positive: it misreads the string default inside the
            # bpy.props annotation as a forward-reference type name.
            phase: bpy.props.StringProperty(default="press")  # noqa: F821

            def execute(self, context):
                # Key-up: complete/hide the gesture and release the mouse grab.
                if self.phase == "release":
                    if KeymapBridge.menu is None:
                        return {
                            "CANCELLED"
                        }  # inactive → consume nothing (symmetry with the press branch)
                    try:
                        KeymapBridge.drive_release()
                    except Exception as error:
                        self.report({"ERROR"}, f"{HostedApp.label()}: {error}")
                        if KeymapBridge.DEBUG:
                            KeymapBridge.logger.warning(f"release failed → {error!r}")
                    return {"FINISHED"}

                # Key-down, no live menu → let the key do its normal thing (CANCELLED passes F12 through
                # to render). report() — not a log line — is what's visible from a keymap-invoked
                # operator (the log goes to the hidden system console, never the Python Console panel).
                if KeymapBridge.menu is None:
                    if KeymapBridge.DEBUG:
                        self.report(
                            {"WARNING"},
                            f"{HostedApp.label()}: key fired but no live menu → render",
                        )
                    return {"CANCELLED"}
                if KeymapBridge.DEBUG:
                    self.report(
                        {"INFO"}, f"{HostedApp.label()}: key fired → marking menu"
                    )
                try:
                    KeymapBridge.drive_press()
                except (
                    Exception
                ) as error:  # surface in the status bar, never crash Blender
                    self.report({"ERROR"}, f"{HostedApp.label()}: {error}")
                    if KeymapBridge.DEBUG:
                        KeymapBridge.logger.warning(
                            f"activation press failed → {error!r}"
                        )
                # ALWAYS consume the key-down once a menu is live so it never falls through to render.
                return {"FINISHED"}

        bpy.utils.register_class(TENTACLE_OT_show_marking_menu)

    # --- activation state machine -----------------------------------------------------------
    @classmethod
    def drive_press(cls, buttons=None):
        """Drive the menu's real activation state machine (``menu.activate``: arms it, shows +
        syncs the menu) — what Maya's Qt shortcut drives — then nudge the overlay to the front
        (GHOST keeps OS focus). Released by :meth:`drive_release` (key-up) or by the menu itself
        after an action.

        Crucially this does **not** grab the mouse for the plain key-hold (no-button) path — it
        mirrors Maya, where the shared ``MarkingMenu`` grabs *only* when a button is already held
        (the chord gesture, via ``_transfer_mouse_control``, reached through
        ``menu.activate(buttons=...)``). An explicit ``grabMouse()`` on the always-visible
        overlay was the cause of "Blender menu buttons need several clicks": a Qt mouse grab
        suppresses the child enter/leave events hover-navigation relies on (``child_enterEvent`` never
        fires → submenus don't open on hover) **and** funnels every click into ``MarkingMenu``'s own
        ``mousePressEvent``, which re-resolves it as the ``F12|LeftButton`` chord (jumping the menu to
        ``cameras#startmenu``) instead of letting the click reach the button. Without the grab, hover
        opens submenus and a leaf click reaches the leaf — exactly Maya's behavior (proven by
        ``tests/blender/hover_nav_check.py`` and the deterministic ``grab_policy_check.py``). The chord
        gesture keeps its grab because the button-held branch still routes through
        ``_transfer_mouse_control``.

        Shared by the keymap operator (no-button path — GHOST never dispatches the key while a
        button is held) and the held-button key poller, which passes the physically-held button
        mask so the ``F12|LeftButton``-style chord bindings resolve."""
        from qtpy import QtCore

        cls.gesture_active = (
            True  # pair every press with a guaranteed release on physical key-up
        )
        # A button held at activation is a chord (the grab path). Record it so the poller pumps
        # MouseTracking during the gesture — GHOST doesn't deliver MouseMove to the grabbed Qt
        # overlay, so the menu's own event-driven hover tracking never fires (see install_poller).
        cls.chord_active = buttons is not None and buttons != QtCore.Qt.NoButton
        # A button held at activation → the shared state machine grabs the mouse itself
        # (_transfer_mouse_control) for the chord gesture, same as Maya. No grab here.
        cls.menu.activate(buttons=buttons)
        for nudge in ("raise_", "activateWindow"):
            try:
                getattr(cls.menu, nudge)()
            except Exception:
                pass

    @classmethod
    def drive_release(cls):
        """Complete the gesture — the single release path for every Blender-side trigger (keymap
        RELEASE item, Qt ``keyReleaseEvent``, key-watcher edge/stuck checks). Mirrors Maya's
        ``GlobalShortcut.released``, which fires application-wide on key-up no matter which window
        has focus: ``menu.release()`` must run even when the menu is no longer ``armed`` (opening
        a standalone window mid-gesture disarms it, see uitk ``_show_window``) — it's what clears
        the menu's standalone suppression (otherwise every subsequent
        press is silently ignored) and what runs ``request_hide`` on unpinned standalone windows
        at key-up. Afterwards hand OS focus back to Blender unless one of the menu's Qt windows kept it —
        otherwise the foreground is left on a hidden window and the next key press reaches neither
        Blender's keymap nor Qt until the user clicks the viewport."""
        cls.gesture_active = False
        cls.chord_active = False
        if cls.menu is None:
            return
        try:
            cls.menu.release()
        finally:
            BlenderWindow.restore_foreground(cls.menu)

    # --- keymap install / uninstall ---------------------------------------------------------
    @classmethod
    def uninstall_keymap(cls):
        """Remove the keymap items added by :meth:`install_keymap`."""
        for km, kmi in cls.keymaps:
            try:
                km.keymap_items.remove(kmi)
            except Exception:
                pass
        cls.keymaps.clear()

    # --- rebinding: keeping Blender's half of the activation key in step -------------------
    @classmethod
    def addon_key_type(cls):
        """The key OUR keymap items carry, or ``None`` when the bridge isn't installed.

        Read live rather than cached alongside :meth:`install_keymap`, so it cannot drift from
        what is actually bound — the divergence that would let a rebind silently revert."""
        for _km, kmi in cls.keymaps:
            try:
                return kmi.type
            except (
                Exception
            ):  # item removed underneath us (add-on reload, keyconfig reset)
                return None
        return None

    @staticmethod
    def _retype_items(items, key_type):
        """Point every keymap item in ``items`` at ``key_type``; False if any assignment failed.

        The one write shared by both halves of a rebind — our own add-on items
        (:meth:`retype_keymap`) and the merged user-keyconfig rows the editor shows
        (:meth:`_sync_sibling_items`)."""
        ok = True
        for kmi in items:
            try:
                if kmi.type != key_type:
                    kmi.type = key_type
            except Exception:  # invalid type, or an item removed underneath us
                ok = False
        return ok

    @classmethod
    def retype_keymap(cls, key_type):
        """Move OUR installed items onto ``key_type`` in place; True when they all moved.

        A rebind is a move, not a reinstall — see :meth:`rebind`. False when nothing is installed
        or an assignment failed, which the caller recovers from by reinstalling."""
        return bool(cls.keymaps) and cls._retype_items(
            [kmi for _km, kmi in cls.keymaps], key_type
        )

    @classmethod
    def user_keymap_items(cls):
        """Our bridge's 3D-View items **as Preferences ▸ Keymap sees them** (order unspecified).

        The user keyconfig, deliberately — not the addon items in :attr:`keymaps`. Blender merges
        the addon keymap into the user one; the editor edits THAT copy and dispatch follows it,
        while our addon item goes on reporting the key we installed. Both halves were measured on
        real injected keystrokes in ``tests/blender/gui_keymap_editor_check.py``: after a rebind to
        F12 the operator fires on F12 and no longer on Z, and the addon item still reads Z. So a
        bridge that watched its own item could never notice a user rebind."""
        try:
            import bpy

            km = bpy.context.window_manager.keyconfigs.user.keymaps.get("3D View")
            if km is None:
                return []
            return [
                kmi
                for kmi in km.keymap_items
                if kmi.idname == "tentacle.show_marking_menu"
            ]
        except Exception:  # no bpy (plain-python/Maya interpreter), no window
            return []  # manager yet, or keyconfig mid-rebuild

    @classmethod
    def live_press_item(cls):
        """Our PRESS row as Preferences ▸ Keymap sees it, or ``None``.

        The row that actually shows the menu, and so the one whose key *is* the activation key.
        (Editing only the RELEASE row is therefore not a rebind; the next PRESS edit re-syncs
        both, see :meth:`_sync_sibling_items`.)"""
        for kmi in cls.user_keymap_items():
            if kmi.value == "PRESS":
                return kmi
        return None

    @classmethod
    def _sync_sibling_items(cls, key_type):
        """Move our other 3D-View rows onto ``key_type`` too.

        The bridge installs a PRESS row and a RELEASE row, and the editor shows both under the same
        name. A user rebinds the one they see; left alone the other keeps answering the old key —
        which would then fire ``drive_release`` against an unrelated keystroke. Best-effort: a row
        that can't be written (read-only in some keyconfig states) isn't worth failing a rebind."""
        cls._retype_items(cls.user_keymap_items(), key_type)

    @classmethod
    def rebind(cls, menu, key_show):
        """Move Blender's half of the activation binding onto ``key_show`` (a Qt key name).

        ``MarkingMenu.set_activation_key`` moves the Qt half — the chord table and the
        ``GlobalShortcut``. On Blender that half never sees a viewport keystroke, so a rebind that
        stops there leaves the menu still answering the OLD key: the 3D-View keymap item and the
        poller's virtual-key both have to follow. Every rebind route (tentacle's Preferences panel,
        the shortcut editor, adopting a Preferences ▸ Keymap edit) reaches this through
        the menu's ``set_activation_key``.

        Our items move **in place** when they already exist (:meth:`retype_keymap`), and are only
        built from scratch when there are none. Leaving them on the old key would let the rebind
        revert the moment Blender rebuilds the user keyconfig from them; retyping is the smaller
        operation than tearing both items down and recreating them, and it preserves the item
        identity Blender's merged user keyconfig is built from rather than briefly removing what
        the user just edited."""
        key_type = cls.qt_key_to_blender_type(key_show)
        cls.active_vk = cls._vk_for_key(key_show)
        cls.key_down = False  # the new key's edge state is unknown — start from "up"
        if not key_type:
            return
        if key_type != cls.addon_key_type():
            # Nothing installed yet, or the retype failed — build from scratch.
            if not cls.retype_keymap(key_type):
                cls.install_keymap(menu, key_type)
        # The rows Preferences ▸ Keymap shows are a MERGED copy of our items (see
        # user_keymap_items) and do not follow an addon-item retype — including a
        # customization Blender restored from userpref.blend. Left on the old key,
        # sync_keymap_rebind reads that stale row as a fresh user edit and adopts the
        # OLD key right back: a shortcut-editor rebind silently reverts within one
        # scan tick, and the persisted key resets across sessions. Runs even when the
        # addon item already holds the key (a redundant set_activation_key is the
        # self-heal route for rows gone stale some other way); _retype_items no-ops
        # on rows already matching.
        cls._sync_sibling_items(key_type)

    @classmethod
    def sync_keymap_rebind(cls, menu):
        """Adopt a rebind made in Preferences ▸ Keymap (throttled; driven by the poller).

        Blender's keymap editor is a *working* rebind route — after an edit the new key reaches our
        operator and the old one stops (measured, see :meth:`user_keymap_items`). But it moves only
        Blender's half, and everything else still keys off the old one. Most destructively the
        poller's virtual-key: it reads the OLD key as physically up while the new one arms a
        gesture, so the release fires on the next 20 ms tick and the menu opens and vanishes — the
        rebind reads as "nothing happened". Adopting closes that, and routes the new key through
        ``set_activation_key`` so it is **persisted**, which the addon keyconfig cannot do (it is
        built from scratch every launch, which is why the editor showed ``Z`` again next session).

        Never runs while the activation key is armed — moving it out from under a live gesture
        would strand that gesture. The menu's ``armed`` is checked alongside
        ``gesture_active`` because a press that half-failed leaves the first set without the
        second (see :meth:`install_poller`), and that is exactly the state a rebind must not hit."""
        if cls.gesture_active or cls.menu is not menu:
            return
        if getattr(menu, "armed", False):
            return
        now = time.monotonic()
        if now - cls._last_key_scan < cls.KEY_SCAN_INTERVAL:
            return
        cls._last_key_scan = now
        item, installed = cls.live_press_item(), cls.addon_key_type()
        live = item.type if item is not None else None
        # No installed item yet (startup, before Blender merges the addon keymap) is not a rebind.
        if not live or not installed or live == installed:
            return
        # Keyed on the whole state, not just the type, so clearing a modifier off a declined
        # binding is re-evaluated rather than skipped as "already reported".
        state = (live, cls._is_bare_press(item))
        if state == cls._declined:
            return
        if not state[1]:
            cls._declined = state
            cls.logger.warning(
                "the marking menu's activation key must be an UNMODIFIED key press, "
                f"and Preferences ▸ Keymap has it on '{live}' with a modifier — not adopted, the "
                "menu keeps its previous key. Rebind it without Ctrl/Alt/Shift/Cmd."
            )
            return
        qt_key = cls.blender_type_to_qt_key(live)
        if qt_key is None:
            cls._declined = state  # report once, not once a second
            cls.logger.warning(
                f"Preferences ▸ Keymap bound the marking menu to '{live}', which has "
                "no Qt key equivalent — the menu keeps its previous activation key. Pick a "
                "standard keyboard key."
            )
            return
        cls._declined = None
        cls._sync_sibling_items(live)
        try:
            menu.set_activation_key(qt_key)  # Qt chords + persistence
            # …and Blender's half explicitly, rather than relying on the callback chain through
            # set_activation_key: that one returns early when the Qt side ALREADY holds this key,
            # which would leave our item on the old one — and this scan re-firing on it every
            # second forever. rebind() is idempotent, so the usual double call costs nothing.
            cls.rebind(menu, qt_key)
        except Exception as error:
            # Bounded and audible: retrying a key that reliably fails would spin silently.
            cls._declined = state
            cls.logger.warning(
                f"could not adopt the '{live}' keymap rebind → {error!r}"
            )

    @classmethod
    def install_keymap(cls, menu, key_type):
        """Bind ``key_type`` in Blender's **3D View** keymap to the bridge operator — a PRESS item and a
        RELEASE item — so holding the key over the viewport opens the marking menu and releasing it
        completes/hides, the same press/hold/release gesture as Maya (driven through the operator since
        GHOST consumes the key before Qt). Records ``menu`` as the live menu the operator drives.

        Scoped to the viewport on purpose: a 3D-View region keymap item is evaluated before the global
        ``Screen`` keymap, so it takes the key over the viewport and naturally wins over ``render.render``
        — *without* us disabling anyone's global F12 (it still renders everywhere else). No muting needed.
        Within the ``3D View`` keymap itself, an addon-keyconfig item also outranks factory/user items on
        the same bare key (measured on a real injected keypress — ``tests/blender/gui_key_rival_check.py``),
        so a stock binding like the ``Z`` shading pie is shadowed while installed, not a conflict.
        """
        import bpy

        cls.menu = menu
        cls.ensure_operator()
        cls.uninstall_keymap()  # re-launch safe
        kc = bpy.context.window_manager.keyconfigs.addon
        if not (kc and key_type):
            return
        # Guard against a key_type that Blender's keymap ``type`` enum doesn't accept (a
        # number/arrow/punctuation activation key with no _BLENDER_KEY_ALIASES entry). Left
        # unguarded, keymap_items.new() raises TypeError, and the broad except at the call site
        # (the menu's construction) would silently skip the WHOLE bridge — poller included. Diagnose
        # the specific offending key and return cleanly so install_poller still runs.
        valid_types = {
            item.identifier
            for item in bpy.types.KeyMapItem.bl_rna.properties["type"].enum_items
        }
        if key_type not in valid_types:
            cls.logger.warning(
                f"the activation key maps to Blender keymap type '{key_type}', which "
                "is not a valid keymap type — the 3D-View bridge was not installed. Pick a "
                "different activation key, or bind it via Preferences ▸ Keymap."
            )
            return
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        for value, phase in (("PRESS", "press"), ("RELEASE", "release")):
            kmi = km.keymap_items.new(
                "tentacle.show_marking_menu", type=key_type, value=value
            )
            kmi.properties.phase = phase
            cls.keymaps.append((km, kmi))

    # --- held-button poller -----------------------------------------------------------------
    @classmethod
    def install_poller(cls, menu, key_show):
        """Global activation-key watcher (Windows-only, idempotent) — the focus-independent half
        of the bridge. Qt only receives the key when a Qt window has OS focus, and Blender's
        keymap only when GHOST has it *and* dispatch isn't eaten, so neither side alone can track
        the key through a real gesture; ``GetAsyncKeyState`` reads the physical key state and is
        the one signal that is always true. Four duties, polled on the pump cadence:

        **Press (edge) — the two states GHOST can't serve.** *Held-button chord:* measured
        (held-button probe, 2026-06-12): with ANY mouse button physically held, GHOST dispatches
        the activation key to NOTHING — not our 3D-View item, not Screen render; the pending
        click/drag, modal op, or popup consumes it. So the keymap bridge alone can never serve
        Maya's "hold a button, then the key" chords, and a mistaken button press before the key
        killed activation entirely. On the key's down-edge with a button held, drive the same
        press path with the OS-read button mask — ``QApplication.mouseButtons()`` is blind to
        GHOST's mouse state. Nothing competes for the key here (the measured eaten-by-Blender
        state means no render fires alongside). *Plain key over one of OUR Qt windows:* when a
        Qt window of ours holds the OS foreground (a pinned tool panel in use — or a stranded
        hidden window the foreground watchdog hasn't healed yet), GHOST doesn't have focus so
        the keymap can't fire, and Qt's own shortcut is inert (its owner widget is hidden) —
        Maya's ``ApplicationShortcut`` covers exactly this state, so the poller does here. The
        keymap operator owns the GHOST-focused no-button path (mutually exclusive with both
        cases — no double-press race; the menu's ``armed`` dedups any residue).

        **Foreground watchdog (level).** :meth:`BlenderWindow.restore_foreground_if_stranded`
        every tick: a Qt popup that took activation and hid after the gesture ended (an
        option_box dropdown dismissed after key-up; a standalone panel closed) leaves the OS
        foreground on a hidden Qt window — uitk restores focus Qt-side only and cannot target
        the foreign GHOST window — deadening the activation key AND every Blender hotkey until
        the user clicks the viewport. State-based on purpose (not hide-event-driven): it reads
        settled OS state on the next tick and heals every strand path, however reached.

        **Rebind scan (throttled).** :meth:`sync_keymap_rebind` once a second, adopting a key the
        user changed in Preferences ▸ Keymap. It lives here rather than on its own timer because
        the poller is the thing that most needs to know: its watched key is read from
        :attr:`active_vk`, and a stale one ends every gesture ~20 ms after it starts.

        **Release (level).** The gesture must end when the key is physically up,
        but every event-driven release path is conditional: Qt's ``keyReleaseEvent`` needs the
        overlay to have won the focus tussle, and the RELEASE keymap item is region-scoped (cursor
        must still hover the viewport). Miss both and the menu stays open with the mouse grab
        eating every click. A *half-failed* press is worse: ``activate``'s error handler
        disarms the menu but doesn't un-show/un-grab, leaving a stuck
        overlay no flag-gated path will ever release. So: whenever the key is physically up and
        the gesture is still armed (``menu.armed``) **or** the overlay is visible while
        holding the mouse grab (the half-failed-press signature; the grab gate keeps programmatic
        ``show()`` — harnesses, tools — untouched), complete the release. ``drive_release``
        is idempotent and hiding auto-releases the Qt grab.

        Off Windows there is no key-state source to poll (Wayland has none), so only the
        throttled rebind scan runs -- a key rebound in Preferences ▸ Keymap is still adopted."""
        if sys.platform != "win32":
            import bpy

            cls.uninstall_poller()

            def _rebind_scan():
                try:
                    cls.sync_keymap_rebind(menu)
                except Exception as error:  # a raising bpy timer unregisters itself
                    if KeymapBridge.DEBUG:
                        cls.logger.warning(f"rebind scan failed → {error!r}")
                return 1.0

            bpy.app.timers.register(_rebind_scan, persistent=True)
            cls.poller = _rebind_scan
            return
        import bpy
        import ctypes
        from ctypes import wintypes

        from qtpy import QtCore, QtWidgets

        cls.uninstall_poller()
        user32 = ctypes.windll.user32
        # The watched key lives on the class, not in this closure, so a rebind moves it without
        # restarting the timer — unregistering a bpy timer from inside its own callback is exactly
        # what a closure-captured key would have forced. The timer itself is registered
        # unconditionally: an activation key with no virtual-key mapping (``Space``, punctuation)
        # only skips the key branch below, and still gets the foreground watchdog and the
        # rebind scan — which is how it recovers if the user then picks a mappable key.
        cls.active_vk = cls._vk_for_key(key_show)
        cls.key_down = False

        def _poll():
            try:
                if (
                    BlenderWindow.native_modal_loop_active()
                ):  # never act inside a native size/move loop
                    return 0.02
                # Pump the menu's hover tracking during a chord. GHOST owns the mouse and doesn't
                # deliver MouseMove to the grabbed Qt overlay, so MouseTracking's event-driven
                # track() never fires — without this the menu's `visible_on_mouse_over` Regions
                # (cameras/editors upper+lower) never reveal their nav buttons and hover-nav into a
                # submenu never happens. track() reads the live cursor + grab state directly, so
                # driving it on the timer reproduces what mouse-move would do under Maya's Qt host.
                if cls.chord_active and cls.menu is menu and menu.isVisible():
                    try:
                        menu.mouse_tracking.update_child_widgets()  # follow submenu transitions
                        menu.mouse_tracking.track()
                    except Exception:
                        pass
                # Foreground watchdog — heal "foreground stuck on a hidden Qt window" (see
                # docstring). Cheap in every steady state: GHOST-focused early-outs on a handle
                # compare; ours-visible / another-app states exit on the top-level scan. Gated
                # off during a live gesture: menu-page transitions hide a window that may still
                # hold the foreground for a tick (_do_pending_hide re-raises it), and yanking
                # focus to GHOST mid-gesture would kill the interaction.
                if cls.menu is menu and not cls.gesture_active:
                    BlenderWindow.restore_foreground_if_stranded(menu)
                # Adopt a Preferences ▸ Keymap rebind (throttled internally).
                cls.sync_keymap_rebind(menu)
                vk = cls.active_vk
                if (
                    vk is None
                ):  # unmappable key — the keymap items still carry activation
                    return 0.02
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down and not cls.key_down:
                    held = cls.physical_mouse_buttons()
                    if held != QtCore.Qt.NoButton:
                        pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(
                            user32.GetForegroundWindow(), ctypes.byref(pid)
                        )
                        # chord: any of our windows foreground (Maya-parity)
                        fire = pid.value == os.getpid()
                    else:
                        # plain key while one of OUR Qt windows holds the OS foreground —
                        # GHOST doesn't have focus, so the keymap operator can't also fire.
                        fire = (
                            BlenderWindow._qt_widget_for_hwnd(
                                BlenderWindow._foreground_hwnd()
                            )
                            is not None
                        )
                    if fire and cls.menu is menu and not menu.armed:
                        try:
                            if held != QtCore.Qt.NoButton:
                                cls.drive_press(buttons=held)
                            else:
                                cls.drive_press()  # plain press — same entry as the keymap operator
                        except Exception as error:
                            # Always audible (system console) — a silent failure here reads as
                            # "the menu just doesn't work"; then tear down any half-armed state.
                            cls.logger.error(f"poller activation failed → {error!r}")
                            try:
                                cls.drive_release()
                            except Exception:
                                pass
                elif not down:
                    # Key physically up → the gesture must end (GlobalShortcut pairing):
                    # gesture_active covers presses whose flags were since cleared (opening a
                    # standalone window does — see drive_release); the stuck checks
                    # cover armed/half-failed states reached without the bridge.
                    stuck = menu.armed or (
                        menu.isVisible() and QtWidgets.QWidget.mouseGrabber() is menu
                    )
                    if (cls.gesture_active or stuck) and cls.menu is menu:
                        try:
                            cls.drive_release()
                        except Exception:
                            pass
                cls.key_down = down
            except Exception as error:
                # Swallow: a raising bpy timer is auto-unregistered, and a dead watchdog means
                # permanently stuck menus. Still audible in debug so plumbing bugs surface.
                if KeymapBridge.DEBUG:
                    cls.logger.warning(f"key-watcher tick failed → {error!r}")
            return 0.02

        bpy.app.timers.register(
            _poll, persistent=True
        )  # persistent: survive File ▸ New/Open
        cls.poller = _poll

    @classmethod
    def uninstall_poller(cls):
        """Remove the held-button poll timer (idempotent)."""
        if cls.poller is None:
            return
        import bpy

        try:
            bpy.app.timers.unregister(cls.poller)
        except Exception:
            pass
        cls.poller = None

    @classmethod
    def teardown(cls):
        """Remove the keymap items and unregister the bridge operator (for add-on unregister)."""
        import bpy

        cls.menu = (
            None  # the next launch() builds a fresh instance (with a fresh keymap)
        )
        cls.gesture_active = False
        cls.active_vk = None
        cls.key_down = False
        cls._last_key_scan = (
            0.0  # the next install's first scan must not be throttled out
        )
        cls._declined = None
        cls.uninstall_poller()
        cls.uninstall_keymap()
        op = getattr(bpy.types, "TENTACLE_OT_show_marking_menu", None)
        if op is not None:
            try:
                bpy.utils.unregister_class(op)
            except Exception:
                pass


class ActivationDiagnostics(ptk.LoggingMixin):
    """The live-activation-state report — run in Blender's Python console to see why the key isn't
    showing the menu (moved from tentacle's ``tcl_blender.Diagnostics``)."""

    @staticmethod
    def report(emit=True):
        """Return (and, when ``emit``, print) the live activation state — run in Blender's Python
        console to see why the key isn't showing the menu::

            print(tcl_blender.diagnose())

        ``emit`` (logged at INFO) defaults on for the interactive ``diagnose()``; tentacle's
        ``register()`` passes ``emit=False`` so a routine launch doesn't dump the whole multi-line
        report every time — it is logged only on a PROBLEM/CONFLICT.

        The returned string is shown right in the Python Console panel (the log alone would land in
        the hidden *system* console). Reports the live module file (a stale/duplicate copy on
        ``sys.path`` is the #1 cause of "nothing happens"), whether the bridge operator is registered,
        whether a live marking menu is wired, the keymap item(s) we installed, any *other* active item
        bound to the same bare key in the ``3D View`` keymap — split into genuine rivals (another
        add-on's item) vs merely shadowed factory/user bindings — and a plain-language VERDICT.
        Blender's bare-F12 ``render.render`` lives in the global ``Screen`` keymap and is evaluated
        *after* our region item, so it is intentionally not a rival."""
        import bpy

        wm = bpy.context.window_manager
        operator_ok = hasattr(bpy.types, "TENTACLE_OT_show_marking_menu")
        our_active = [kmi for _km, kmi in KeymapBridge.keymaps if kmi.active]
        our_keys = {kmi.type for _km, kmi in KeymapBridge.keymaps}
        # Same-key bare-press items in the ``3D View`` keymap, split by what they actually mean:
        # another ADD-ON binding the key is a genuine contest (ordering among addon items is
        # insertion order — undetermined), while factory/user items are merely SHADOWED — an
        # addon-keyconfig item outranks them within the same keymap, measured on a real injected
        # keypress in a factory session (``tests/blender/gui_key_rival_check.py``: the stock shading
        # pie on bare ``Z``, the default activation key, never fires while our item is installed).
        # Dedup by name, not id(): ``active`` often aliases ``user``/``default`` but Blender hands
        # back a fresh Python wrapper each access (distinct id), so id()-dedup would double-list.
        addon_rivals = []
        shadowed = []
        seen = set()
        for is_addon, kc in (
            (True, wm.keyconfigs.addon),
            (False, wm.keyconfigs.user),
            (False, wm.keyconfigs.active),
            (False, wm.keyconfigs.default),
        ):
            if kc is None or kc.name in seen:
                continue
            seen.add(kc.name)
            # Only a *bare* same-key PRESS in the 3D View keymap is on our dispatch path — modified
            # combos coexist and the global Screen render shortcut is evaluated after our region item.
            bucket = addon_rivals if is_addon else shadowed
            bucket += [
                f"{kc.name}:{kmi.idname}" if is_addon else kmi.idname
                for km in kc.keymaps
                if km.name == "3D View"
                for kmi in km.keymap_items
                if kmi.active
                and kmi.type in our_keys
                and KeymapBridge._is_bare_press(kmi)
                and kmi.idname != "tentacle.show_marking_menu"
            ]
        shadowed = sorted(set(shadowed))

        if not (operator_ok and our_active):
            verdict = (
                "PROBLEM: activation key not installed — launch the marking menu (tentacle: "
                "tcl_blender.register()). If you just did, a stale/duplicate copy is on "
                "sys.path — check 'module file' above."
            )
        elif KeymapBridge.menu is None:
            verdict = (
                "PROBLEM: no live menu wired — launch the marking menu again (tentacle: "
                "tcl_blender.register())."
            )
        elif addon_rivals:
            verdict = (
                f"CONFLICT: another add-on's 3D View binding {addon_rivals} shares the key and "
                "may win — disable it, or pick a different activation key."
            )
        else:
            # Reached only with an active item of ours, so our_keys is never empty here
            # (the key-resolution fallback this once carried could not be reached).
            key = next(iter(our_keys))
            shadow_note = f" (shadowing {shadowed} while installed)" if shadowed else ""
            verdict = (
                f"LIKELY WORKING: '{key}' is bound in the 3D View keymap{shadow_note}. Hover the "
                "3D viewport and press it. If render still opens, set blendertk.KeymapBridge.DEBUG="
                f"True — each fire then shows '{HostedApp.label()}: key fired' in the status bar "
                "(log output is in the hidden system console: Window > Toggle System Console)."
            )

        lines = [
            f"=== {HostedApp.name} Blender activation ===",
            f"module file        : {__file__}",
            f"operator registered: {operator_ok}",
            f"live marking menu   : {KeymapBridge.menu!r}",
            f"DEBUG (logs fires)  : {KeymapBridge.DEBUG}",
            f"key watcher (poll)  : {'installed' if KeymapBridge.poller is not None else 'NOT installed'} (held-button press + release watchdog)",
            f"our keymap items    : {[(km.name, kmi.type, kmi.value, kmi.active) for km, kmi in KeymapBridge.keymaps]}",
            f"addon-kc rivals     : {addon_rivals or 'none'} (genuine contest — undetermined order)",
            f"shadowed same-key   : {shadowed or 'none'} (factory/user items our addon item outranks)",
            f"VERDICT             : {verdict}",
        ]
        report = "\n".join(lines)
        if emit:
            ActivationDiagnostics.logger.info(report)
        return report
