"""Channels panel icon cells — the Lock / Key columns SET on a press and CLEAR on Alt+press.

Needs **Qt, not bpy** (``channels_slots`` imports uitk; the controller is a stand-in), so it runs
under the workspace ``.venv`` like ``test_blender_ui_handler.py``::

    .venv\\Scripts\\python.exe blendertk/test/test_channels_slots.py

A toggle left the rows mixed whenever a drag crossed rows that started in different states; a
drag also reaches the panel once, so the table refreshes once. Mirror of mayatk's
``TestIconCellDispatch``. Under the Blender harness (no Qt) it SKIPS with a PASS sentinel.
"""

import os
import sys
import traceback
from types import MethodType, SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    from qtpy import QtCore
except Exception:
    print("SKIP test_channels_slots (no Qt binding — run under the workspace .venv)")
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

try:
    from blendertk.node_utils.attributes.channels.channels_slots import ChannelsSlots

    Qt = QtCore.Qt
    OBJECTS = ["Cube"]
    LOC_X = {"name": "location_x", "kind": "transform", "data_path": "location", "index": 0}

    def stand_in(modifiers=Qt.NoModifier):
        """A stand-in panel whose every row is location_x, with a mock controller."""
        controller = mock.MagicMock()
        controller.get_selected_nodes.return_value = OBJECTS
        slots = SimpleNamespace(
            COL_LOCK=ChannelsSlots.COL_LOCK,
            COL_CONN=ChannelsSlots.COL_CONN,
            ui=SimpleNamespace(tbl000=mock.MagicMock()),
            controller=controller,
            sb=SimpleNamespace(
                QtCore=QtCore,
                QtWidgets=SimpleNamespace(
                    QApplication=SimpleNamespace(keyboardModifiers=lambda: modifiers)
                ),
            ),
            _descriptor_at=lambda row: LOC_X,
            _refresh_table=mock.Mock(),
        )
        slots._apply_icon_cell = MethodType(ChannelsSlots._apply_icon_cell, slots)
        return slots

    def press(col, modifiers=Qt.NoModifier):
        """Press one icon cell; return the stand-in controller."""
        slots = stand_in(modifiers)
        ChannelsSlots._on_icon_cell_clicked(slots, 0, col)
        return slots.controller

    c = press(ChannelsSlots.COL_LOCK)
    check("lock press -> set_lock(True), whatever the row's state",
          c.set_lock.call_args == mock.call(OBJECTS, [LOC_X], True) and not c.toggle_lock.called,
          str(c.method_calls))
    c = press(ChannelsSlots.COL_LOCK, Qt.AltModifier)
    check("lock Alt+press -> set_lock(False)",
          c.set_lock.call_args == mock.call(OBJECTS, [LOC_X], False), str(c.method_calls))
    c = press(ChannelsSlots.COL_CONN)
    check("key press -> set_key_at_current_time(keyed=True), never a toggle",
          c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=True)
          and not c.toggle_key_at_current_time.called, str(c.method_calls))
    c = press(ChannelsSlots.COL_CONN, Qt.AltModifier)
    check("key Alt+press -> set_key_at_current_time(keyed=False)",
          c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=False),
          str(c.method_calls))
    c = press(ChannelsSlots.COL_CONN, Qt.ControlModifier)
    check("key Ctrl+press -> break_connections only",
          c.break_connections.called and not c.set_key_at_current_time.called,
          str(c.method_calls))

    s = stand_in(Qt.AltModifier)
    ChannelsSlots._on_icon_cells_dragged(s, [0, 1, 2], ChannelsSlots.COL_CONN)
    check("Alt-drag over 3 rows -> 3 key removals, ONE refresh",
          s.controller.set_key_at_current_time.call_count == 3
          and s._refresh_table.call_count == 1,
          f"keys={s.controller.set_key_at_current_time.call_count} "
          f"refreshes={s._refresh_table.call_count}")

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if lines and all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
