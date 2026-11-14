# !/usr/bin/python
# coding=utf-8
"""Tests for ``blendertk.KeymapBridge`` — the Qt ↔ Blender keymap translation and the rebind
that keeps Blender's half of a marking menu's activation binding on its key (moved from
tentacle's ``test_tcl_launcher.TestBlenderKeyTranslation`` with ``tcl_blender._KeymapBridge``).

Qt-side, bpy-free: the bridge reads keymap items through stand-ins here, so this runs under the
workspace ``.venv``; under ``blender --background`` (no Qt) it reports the ``(skipped)``
sentinel so ``run_tests.py`` re-runs it there.
"""

import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for _path in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")


def _user_keymap_row(key_type, value="PRESS"):
    """A stand-in for a merged user-keyconfig keymap item, with every field the
    bridge reads (``_is_bare_press`` checks the modifier flags)."""
    return types.SimpleNamespace(
        type=key_type,
        value=value,
        idname="tentacle.show_marking_menu",
        any=False,
        ctrl=False,
        alt=False,
        shift=False,
        oskey=False,
    )


class TestTheBridgeDrivesTheMenusPublicSurface(unittest.TestCase):
    """The bridge drives a ``uitk.MarkingMenu`` through what the menu publishes for a
    keyboard-owning host -- ``activate(buttons=)``, ``release()``, ``armed`` -- never its
    private members, and holds it as ``KeymapBridge.menu``."""

    @classmethod
    def setUpClass(cls):
        try:
            from qtpy import QtCore  # noqa: F401
        except Exception as error:
            raise unittest.SkipTest(f"no Qt binding: {error}")
        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        cls.bridge = KeymapBridge

    def setUp(self):
        before = (self.bridge.menu, self.bridge.gesture_active, self.bridge.chord_active)
        self.addCleanup(self._restore, before)
        self.menu = mock.Mock(armed=False)
        self.bridge.menu = self.menu

    def _restore(self, before):
        self.bridge.menu, self.bridge.gesture_active, self.bridge.chord_active = before

    def test_a_key_down_activates_the_menu(self):
        self.bridge.drive_press()
        self.menu.activate.assert_called_once_with(buttons=None)
        self.assertTrue(self.bridge.gesture_active)

    def test_a_held_button_is_passed_on(self):
        from qtpy import QtCore

        self.bridge.drive_press(buttons=QtCore.Qt.LeftButton)
        self.menu.activate.assert_called_once_with(buttons=QtCore.Qt.LeftButton)
        self.assertTrue(self.bridge.chord_active)

    def test_a_key_up_releases_the_menu(self):
        from blendertk.ui_utils.blender_window import BlenderWindow

        with mock.patch.object(BlenderWindow, "restore_foreground") as restore:
            self.bridge.drive_release()
        self.menu.release.assert_called_once_with()
        restore.assert_called_once_with(self.menu)
        self.assertFalse(self.bridge.gesture_active)

    def test_an_armed_menu_is_never_rebound_under_its_gesture(self):
        self.menu.armed = True
        before = self.bridge._last_key_scan
        self.addCleanup(setattr, self.bridge, "_last_key_scan", before)
        self.bridge._last_key_scan = 0.0
        with mock.patch.object(self.bridge, "live_press_item") as scan:
            self.bridge.sync_keymap_rebind(self.menu)
        scan.assert_not_called()


class TestBlenderKeyTranslation(unittest.TestCase):
    """The Qt ↔ Blender keymap-``type`` translation — Blender's half of the activation-key contract.

    It has to work in BOTH directions: forward to install the keymap item, and backward to name
    the Qt key for a rebind a user made in Preferences ▸ Keymap (measured as a working rebind
    route in tentacle's ``tests/blender/gui_keymap_editor_check.py``). A backward translation that
    produces a
    name ``QtCore.Qt`` doesn't have would make the adoption a silent no-op.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from qtpy import QtCore  # noqa: F401 — the backward translation reads QtCore.Qt
        except Exception as error:  # no Qt binding available in this interpreter
            raise unittest.SkipTest(f"no Qt binding: {error}")
        from blendertk.ui_utils.marking_menu_host.keymap_bridge import KeymapBridge

        cls.bridge = KeymapBridge

    def test_forward_translation(self):
        for qt_key, expected in (
            ("Key_F12", "F12"),
            ("Key_Z", "Z"),
            ("Key_Meta", "OSKEY"),
            ("Key_1", "ONE"),
            ("Key_BracketLeft", "LEFT_BRACKET"),
        ):
            with self.subTest(qt_key=qt_key):
                self.assertEqual(self.bridge.qt_key_to_blender_type(qt_key), expected)

    def test_forward_translation_accepts_any_casing(self):
        """Callers pass bare or Qt-named keys in whatever case the user typed."""
        for form in ("Key_META", "meta", "Meta", "Key_Meta"):
            with self.subTest(form=form):
                self.assertEqual(self.bridge.qt_key_to_blender_type(form), "OSKEY")

    def test_backward_translation_names_a_real_qt_key(self):
        from qtpy import QtCore

        for key_type in ("F12", "Z", "OSKEY", "ONE", "SPACE", "LEFT_ARROW", "ESC"):
            with self.subTest(key_type=key_type):
                name = self.bridge.blender_type_to_qt_key(key_type)
                self.assertIsNotNone(name, f"{key_type} produced no Qt name")
                self.assertTrue(hasattr(QtCore.Qt, name), f"{name} is not a Qt key")

    def test_shared_blender_types_name_the_canonical_qt_spelling(self):
        """Meta/Super_L/Super_R all map to OSKEY; coming back it must be the first — ``Meta``."""
        self.assertEqual(self.bridge.blender_type_to_qt_key("OSKEY"), "Key_Meta")
        self.assertEqual(self.bridge.blender_type_to_qt_key("RET"), "Key_Return")

    def test_round_trip_is_stable_for_every_alias(self):
        """Qt name → Blender type → Qt name → the SAME Blender type (aliases may collapse)."""
        for qt_name, key_type in self.bridge._BLENDER_KEY_ALIASES.items():
            with self.subTest(qt_name=qt_name):
                back = self.bridge.blender_type_to_qt_key(key_type)
                self.assertIsNotNone(back, f"{key_type} has no Qt name")
                self.assertEqual(self.bridge.qt_key_to_blender_type(back), key_type)

    def test_types_with_no_qt_counterpart_return_none(self):
        """Blender binds things Qt has no key for; adopting one must be declined, not faked."""
        for key_type in ("LEFTMOUSE", "NDOF_BUTTON_1", "WHEELUPMOUSE", "", None):
            with self.subTest(key_type=key_type):
                self.assertIsNone(self.bridge.blender_type_to_qt_key(key_type))

    def test_rebind_moves_existing_items_in_place(self):
        """A rebind is a MOVE: retype the installed items, never tear them down and rebuild.

        Recreating would briefly unbind the very item the user just edited in the keymap editor."""
        bridge = self.bridge
        before = (bridge.keymaps, bridge.active_vk, bridge.key_down)
        item = types.SimpleNamespace(type="Z")
        try:
            bridge.keymaps = [(None, item)]
            with mock.patch.object(bridge, "install_keymap") as reinstall:
                bridge.rebind(None, "Key_F12")
            self.assertEqual(item.type, "F12")
            reinstall.assert_not_called()
        finally:
            bridge.keymaps, bridge.active_vk, bridge.key_down = before

    def test_rebind_installs_when_nothing_is_bound_yet(self):
        bridge = self.bridge
        before = (bridge.keymaps, bridge.active_vk, bridge.key_down)
        try:
            bridge.keymaps = []
            with mock.patch.object(bridge, "install_keymap") as install:
                bridge.rebind("tcl-sentinel", "Key_F12")
            install.assert_called_once_with("tcl-sentinel", "F12")
        finally:
            bridge.keymaps, bridge.active_vk, bridge.key_down = before

    def test_rebind_survives_a_platform_without_virtual_keys(self):
        """``rebind`` runs on every platform; only the poller's key is Windows-specific.

        A single-character key used to reach ``ctypes.windll`` from here and take the whole
        rebind — keymap item included — down with it off Windows."""
        bridge = self.bridge
        before = (bridge.keymaps, bridge.active_vk, bridge.key_down)
        item = types.SimpleNamespace(type="F12")
        try:
            bridge.keymaps = [(None, item)]
            with mock.patch(
                "blendertk.ui_utils.marking_menu_host.keymap_bridge.sys.platform",
                "linux",
            ):
                bridge.rebind(None, "Key_Z")
            self.assertEqual(item.type, "Z")  # the keymap half still moved
            self.assertIsNone(
                bridge.active_vk
            )  # the Windows-only half declined cleanly
        finally:
            bridge.keymaps, bridge.active_vk, bridge.key_down = before

    def test_rebind_moves_the_user_keyconfig_rows_too(self):
        """A Qt-side rebind must retype the merged user-keyconfig rows, not just our addon items.

        Blender's keymap editor edits a MERGED copy of our item (see ``user_keymap_items``), and
        that copy — including any customization saved in userpref.blend — does not follow an
        addon-item retype. Left behind on the old key, ``sync_keymap_rebind`` reads it as a fresh
        Preferences ▸ Keymap edit and adopts the OLD key right back: the shortcut-editor rebind
        reverts within a second, and the store resets to the stale key across sessions."""
        bridge = self.bridge
        before = (bridge.keymaps, bridge.active_vk, bridge.key_down)
        addon_item = types.SimpleNamespace(type="Z")
        user_row = _user_keymap_row("Z")
        try:
            bridge.keymaps = [(None, addon_item)]
            with mock.patch.object(
                bridge, "user_keymap_items", return_value=[user_row]
            ):
                bridge.rebind(None, "Key_F12")
            self.assertEqual(addon_item.type, "F12")
            self.assertEqual(user_row.type, "F12")
        finally:
            bridge.keymaps, bridge.active_vk, bridge.key_down = before

    def test_editor_rebind_survives_the_keymap_scan(self):
        """The scan must not re-adopt the key a Qt-side rebind just moved away from.

        The end-to-end regression: rebind to F12 through the shortcut-editor route, then run the
        throttled ``sync_keymap_rebind`` scan against a user-keyconfig row that carried the old
        key. Pre-fix the scan saw live=Z ≠ installed=F12 and called ``set_activation_key(Key_Z)``
        — silently reverting the user's choice."""
        bridge = self.bridge
        before = (
            bridge.keymaps,
            bridge.active_vk,
            bridge.key_down,
            bridge.menu,
            bridge.gesture_active,
            bridge._last_key_scan,
            bridge._declined,
        )
        addon_item = types.SimpleNamespace(type="Z")
        user_row = _user_keymap_row("Z")
        tcl = types.SimpleNamespace(
            armed=False, set_activation_key=mock.Mock()
        )
        try:
            bridge.keymaps = [(None, addon_item)]
            bridge.menu = tcl
            bridge.gesture_active = False
            bridge._declined = None
            with mock.patch.object(
                bridge, "user_keymap_items", return_value=[user_row]
            ):
                bridge.rebind(tcl, "Key_F12")
                bridge._last_key_scan = 0.0  # defeat the once-a-second throttle
                bridge.sync_keymap_rebind(tcl)
            tcl.set_activation_key.assert_not_called()
        finally:
            (
                bridge.keymaps,
                bridge.active_vk,
                bridge.key_down,
                bridge.menu,
                bridge.gesture_active,
                bridge._last_key_scan,
                bridge._declined,
            ) = before

    @unittest.skipUnless(sys.platform == "win32", "virtual-keys are a Windows concept")
    def test_rebind_moves_the_pollers_virtual_key(self):
        """The poller watches by virtual-key; left on the old one it ends the gesture instantly."""
        bridge = self.bridge
        before = (bridge.active_vk, bridge.key_down)
        try:
            bridge.key_down = True
            # No keymap items installed (no Blender here) → rebind only moves the poller.
            with mock.patch.object(bridge, "addon_key_type", return_value="F12"):
                bridge.rebind(None, "Key_F12")
            self.assertEqual(bridge.active_vk, 0x7B)  # VK_F12
            self.assertFalse(bridge.key_down)  # edge state reset for the new key
        finally:
            bridge.active_vk, bridge.key_down = before


if __name__ == "__main__":
    try:
        import qtpy  # noqa: F401 — the binding every assertion here needs
    except Exception:
        # Under the Blender harness (no Qt): the SKIP sentinel makes run_tests.py
        # re-run this suite under the workspace .venv.
        print("===RESULT: PASS=== (skipped)")
        raise SystemExit(0)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    _attempted = result.testsRun - len(result.skipped)
    _ok = _attempted - len(result.failures) - len(result.errors)
    _tally = f"{_ok}/{_attempted}" if _attempted else "skipped"
    print(f"===RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}=== ({_tally})")
